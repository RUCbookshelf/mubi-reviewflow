"""S1 任务 2:停止规则后评估(输入 = S0 轨迹日志,输出 = 每规则停止指标)。

设计:"一次模拟、多规则后评估"。轨迹来自 :mod:`replay`(S0 引擎,只读引用),
本模块只消费轨迹 JSONL(+数据集 CSV 的黄金标签,供"模拟人工"标注子样),
不改产品代码、不改引擎。

符号(与 research/notes/stopping_rules.md 一致):
  N   语料总记录数;  K   相关(relevant)总数;  t   已筛位次(1 起);
  m0  当前剩余池大小; R0  剩余池中未知的相关数;  m   随机子样大小;
  x   子样中观测到的相关数;  k   证书目标("剩余相关 <= k");
  alpha  显著性水平(证书置信 1-alpha);  L  序贯 look 次数。

规则族:
  streak          连续排除(ASReview/Kempny 准则 2 语义):首个 exclude_streak>=n 行停。
  pilot_subsample 序贯随机子样超几何证书(D2):在每个 look 从剩余池均匀抽 m 条
                  全部人工判读,pval = P_{R0=k+1}(X<=x) <= alpha_l 时停;
                  alpha_l 为 alpha 的 Bonferroni-spending 分配(pocock 等分 / obf 形状)。
  nb_window       窗口 Gamma-Poisson(负二项)预测上界(D3 开放实现,启发式,
                  无设计式覆盖保证,flag=heuristic_no_guarantee)。
  combined        双闸门:look 处先查 streak 闸门(当前 exclude_streak>=n_streak,
                  不满足则跳过本次抽样,零成本),再查子样证书。
  kempny_c1       Kempny 2026 准则 1 复现:初始随机样本(占比 sample_frac)估患病率
                  → 估计相关总数 T=round(x_s/s*N);找到 T 条即停。
  kempny_k2       Kempny 2026 准则 2 的"安全阈值提取":n* = 最后一条相关记录之前
                  出现过的最大连续排除长度;阈值 n>n* 必安全、n<=n* 必遗漏。

轨迹结束语义:replay 在发现全部相关时提前终止;规则若到轨迹末尾仍未触发,
则真实流程中其后也不会遗漏(相关已全找到)⇒ 记 missed=0、
screened_fraction=1.0、flag="ran_to_completion"。

后评估近似(如实声明):子样的人工标签不回灌 AL 模型(真实流程会回灌并略
改善排序);子样抽取使用黄金标签,等价于"人工即时判读"。

已知能力边界(向调度会话报告的引擎需求,见 notes §3.2):
排序加权(校准)估计器需要剩余池的分数分布摘要,现有轨迹仅记
model_top_remaining(k=5)不足;建议引擎每训练步补充记录
``remaining_score_sum`` 与 ``remaining_score_quantiles``([q10,q25,q50,q75,q90])。
本模块先用 nb_window(可从轨迹计算)作为模型法代表。

运行自检:./.venv/bin/python -m pytest research/sim/ -q
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from scipy.stats import gamma as _gamma
from scipy.stats import hypergeom

__all__ = [
    "make_trajectory", "load_trajectory", "load_gold",
    "p_miss_random_order", "pval_certify", "ucb_hypergeom",
    "spending_increments", "frontier_sample_size", "exact_frontier_sample_size",
    "default_look_positions", "k_target",
    "evaluate_streak", "evaluate_pilot_subsample", "evaluate_nb_window",
    "evaluate_combined", "evaluate_kempny_c1", "kempny_k2_safe_threshold",
    "default_rule_grid", "evaluate_all", "summarize",
]

DEFAULT_LOOK_FRACTIONS = (0.2, 0.4, 0.6, 0.8, 0.9, 0.97, 0.99)


# ---------------------------------------------------------------------------
# 轨迹容器与加载
# ---------------------------------------------------------------------------

def make_trajectory(rows: list[dict], n_records: int | None = None,
                    n_relevant: int | None = None) -> dict:
    """把最小行集合打包成内部轨迹容器(测试/自检用)。

    必需行字段:i, record_id, label, n_relevant_found, n_relevant_total,
    n_remaining, exclude_streak。n_records/n_relevant 缺省从行推断
    (N = i + n_remaining 对任意行成立)。
    """
    if n_records is None:
        n_records = int(rows[0]["i"]) + int(rows[0]["n_remaining"])
    if n_relevant is None:
        n_relevant = int(rows[0]["n_relevant_total"])
    return {"rows": rows, "n_records": int(n_records),
            "n_relevant": int(n_relevant)}


def load_trajectory(jsonl_path: str | Path,
                    summary_path: str | Path | None = None) -> dict:
    """读取 S0 引擎的 trajectory.jsonl(+可选 summary.json 取 seed 等配置)。"""
    rows = []
    with Path(jsonl_path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if not rows:
        raise ValueError(f"空轨迹文件: {jsonl_path}")
    traj = make_trajectory(rows)
    traj["source"] = str(jsonl_path)
    if summary_path is not None:
        summary = json.loads(Path(summary_path).read_text(encoding="utf-8"))
        traj["summary"] = summary
        if int(summary.get("n_records", -1)) != traj["n_records"]:
            raise ValueError("summary.n_records 与轨迹行不一致")
    return traj


def load_gold(csv_path: str | Path) -> dict[str, str]:
    """数据集黄金标签(replay.load_dataset_csv,只读;插入序=数据集行序)。"""
    import sys
    sim_dir = Path(__file__).resolve().parent
    if str(sim_dir) not in sys.path:
        sys.path.insert(0, str(sim_dir))
    from replay import load_dataset_csv  # 延迟导入,避免测试环境循环依赖
    _records, gold = load_dataset_csv(csv_path)
    return gold


# ---------------------------------------------------------------------------
# 理论函数(任务 1 闭式的实现;自检脚本与测试对照用)
# ---------------------------------------------------------------------------

def p_miss_random_order(n: int, k: int, N: int) -> float:
    """无信息随机序下 streak-n 规则的遗漏概率(精确闭式;notes §1 定理 1)。

    P_miss = 1 - (1/C(N,K)) * sum_{j>=0} (-1)^j C(K,j) C(N - j n, K)
    """
    if k == 0 or n <= 0:
        return 0.0
    total = math.comb(N, k)
    acc, sign = 0, 1
    for j in range(k + 1):
        rest = N - j * n
        if rest < k:
            break
        acc += sign * math.comb(k, j) * math.comb(rest, k)
        sign = -sign
    return 1.0 - acc / total


def pval_certify(m0: int, m: int, x: int, k: int) -> float:
    """单点证书 'R0 <= k' 的 p 值(notes §2.1)。

    pval(k) = sup_{r>=k+1} P_{Hypergeom(m0, r, m)}(X <= x);由 cdf 对 r 单调
    递减,sup 在 r=k+1 取到。返回 0 当 k+1 > m0(备择不可能 ⇒ 必然可证)。
    """
    if k + 1 > m0:
        return 0.0
    return float(hypergeom.cdf(x, m0, k + 1, m))


def ucb_hypergeom(m0: int, m: int, x: int, alpha: float) -> int:
    """Clopper-Pearson 式精确上界 U(x) = max{r: P_r(X<=x) > alpha};
    覆盖保证 P(R0 > U(X)) <= alpha(notes §2.1,推论 2.1)。
    cdf 对 r 单调递减 ⇒ 二分(max 处 O(log m0) 次 cdf 调用)。"""
    if hypergeom.cdf(x, m0, 0, m) <= alpha:
        return -1  # 连 r=0 都被拒绝(样本全为相关但 r=0 概率小)⇒ 上界无定义,返回 -1
    lo, hi = 0, m0  # 不变量:cdf(lo) > alpha >= cdf(hi)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if hypergeom.cdf(x, m0, mid, m) > alpha:
            lo = mid
        else:
            hi = mid
    return lo


def spending_increments(alpha: float, looks: int,
                        profile: str = "pocock") -> list[float]:
    """alpha 的序贯分配(notes §2.2)。Pocock=等分 alpha/L;OBF 形状
    A(s)=1-Phi(z_{1-alpha}/sqrt(s)) 的增量。只依赖 Bonferroni(union bound),
    对 look 间任意依赖结构有效。"""
    if looks <= 0:
        return []
    if profile == "pocock":
        return [alpha / looks] * looks
    if profile == "obf":
        from scipy.stats import norm
        z = norm.ppf(1.0 - alpha)
        cum, inc, prev = [], [], 0.0
        for l in range(1, looks + 1):
            a = 1.0 - float(norm.cdf(z / math.sqrt(l / looks)))
            inc.append(a - prev)
            prev = a
        # 修正浮点和误差,使 sum(inc) == alpha 精确成立
        inc[-1] += alpha - sum(inc)
        return inc
    raise ValueError(f"未知 spending profile: {profile!r}")


def frontier_sample_size(m0: int, k: int, alpha: float) -> float:
    """安全-工作量前沿近似式(notes §2.3):单 look 以 x=0 通过 'R0<=k' 证书
    所需的最小子样 m ~ m0 (1 - alpha^{1/(k+1)})。"""
    return m0 * (1.0 - alpha ** (1.0 / (k + 1)))


def exact_frontier_sample_size(m0: int, k: int, alpha: float) -> int | None:
    """同上的精确解:最小 m 使 P_{R0=k+1}(X=0) <= alpha。"""
    for m in range(1, m0 + 1):
        if pval_certify(m0, m, 0, k) <= alpha:
            return m
    return None


def k_target(k_mode: str, found_total: int, recall_target: float) -> int:
    """证书目标 k(notes §2.1/§4):zero → 0;recall → floor(found*(1-R)/R),
    即停后总召回 >= R 的充要上界(R0 <= found*(1-R)/R)。"""
    if k_mode == "zero":
        return 0
    if k_mode == "recall":
        if found_total <= 0:
            return 0
        return int(math.floor(found_total * (1.0 - recall_target) / recall_target))
    raise ValueError(f"未知 k_mode: {k_mode!r}")


# ---------------------------------------------------------------------------
# 规则实现(每规则返回统一 schema 的 dict)
# ---------------------------------------------------------------------------

def _base_result(rule: str, params: dict) -> dict:
    return {
        "rule": rule, "params": params,
        "stop_step": None, "screened_records": None, "screened_fraction": None,
        "missed_relevant": None, "recall_at_stop": None,
        "safety_flags": [], "details": {},
    }


def _finish_no_stop(res: dict, traj: dict) -> dict:
    """轨迹内未触发:相关已全部找到,续筛不会遗漏 ⇒ 安全收尾。"""
    res["stop_step"] = None
    res["screened_records"] = traj["n_records"]
    res["screened_fraction"] = 1.0
    res["missed_relevant"] = 0
    res["recall_at_stop"] = 1.0
    res["safety_flags"].append("ran_to_completion")
    return res


def evaluate_streak(traj: dict, n: int) -> dict:
    """streak-n:首个 exclude_streak >= n 的行停(ASReview 准则 2 语义)。"""
    res = _base_result("streak", {"n": n})
    K = traj["n_relevant"]
    for row in traj["rows"]:
        if int(row["exclude_streak"]) >= n:
            t = int(row["i"])
            found = int(row["n_relevant_found"])
            res["stop_step"] = t
            res["screened_records"] = t
            res["screened_fraction"] = t / traj["n_records"]
            res["missed_relevant"] = K - found
            res["recall_at_stop"] = found / K if K else 1.0
            res["safety_flags"].append("stopped_streak")
            res["details"]["streak_at_stop"] = int(row["exclude_streak"])
            return res
    return _finish_no_stop(res, traj)


def evaluate_pilot_subsample(traj: dict, gold: dict[str, str], m: int,
                             alpha: float, k_mode: str = "zero",
                             recall_target: float = 0.95,
                             look_positions: list[int] | None = None,
                             spending: str = "pocock", seed: int = 0) -> dict:
    """序贯随机子样超几何证书(notes §2)。

    m 可为 int(固定子样数)或 0<m<1 的 float(每次 look 抽剩余池的该比例,
    前沿式设计,notes §2.3)。发现数与成本按"前缀 ∪ 子样"集合并口径(后评估
    中子样记录可能被轨迹继续正常筛到;真实流程中子样即从队列移除,不重复)。

    每个 look:从当前剩余池(未被筛、未被先前子样标注)均匀无放回抽
    m'=min(m, m0) 条"人工判读";x = 子样相关数;pval = P_{R0=k+1}(X<=x);
    pval <= alpha_l(spending 增量)⇒ 停。
    """
    look_positions = look_positions or default_look_positions(traj["n_records"])
    rule = "pilot_subsample"
    res = _base_result(rule, {"m": m, "alpha": alpha, "k_mode": k_mode,
                              "recall_target": recall_target if k_mode == "recall" else None,
                              "spending": spending, "looks": len(look_positions), "seed": seed})
    K, N = traj["n_relevant"], traj["n_records"]
    rows = traj["rows"]
    incs = spending_increments(alpha, len(look_positions), spending)
    rng = np.random.default_rng(seed)
    all_ids = list(gold.keys())
    if len(all_ids) != N:
        raise ValueError(f"gold 大小 {len(all_ids)} != 轨迹 N={N}")
    screened_ids = [row["record_id"] for row in rows]
    for row, rid in zip(rows, screened_ids):
        if gold.get(rid) != row["label"]:
            raise ValueError(f"轨迹标签与 gold 不一致: {rid}")
    label_of = gold
    labeled: set[str] = set()   # 子样已标注(可能与前缀相交:后评估近似,见下)
    subs_rel_ids: set[str] = set()
    total_sub = 0
    looks_log = []
    max_t = len(rows)
    prefix_pos = {row["record_id"]: int(row["i"]) for row in rows}
    for l, t in enumerate(look_positions):
        if t > max_t:
            break
        pool = [rid for rid in all_ids
                if rid not in labeled and prefix_pos.get(rid, 10**9) > t]
        m0 = len(pool)
        if m0 == 0:
            break
        m_l = min(m, m0) if isinstance(m, int) else max(1, min(m0, math.ceil(m * m0)))
        pos = rng.choice(len(pool), size=m_l, replace=False)
        draw = [pool[i] for i in pos]
        x = sum(1 for rid in draw if label_of[rid] == "include")
        labeled.update(draw)
        total_sub += m_l
        subs_rel_ids.update(rid for rid in draw if label_of[rid] == "include")
        # 发现数与成本一律按"集合并"口径:前缀 ∪ 子样(后评估中子样记录可能
        # 会被轨迹继续正常筛到——真实流程中子样即从队列移除,不重复计费/计数)
        prefix_rel = {row2["record_id"] for row2 in rows[:t]
                      if row2["label"] == "include"}
        found_eff = len(prefix_rel | subs_rel_ids)
        kt = k_target(k_mode, found_eff, recall_target)
        p = pval_certify(m0, m_l, x, kt)
        census = (m_l == m0)
        looks_log.append({"look": l + 1, "t": t, "m0": m0, "m": m_l, "x": x,
                          "k": kt, "p": p, "alpha_l": incs[l],
                          "census": census})
        if p <= incs[l]:
            res["stop_step"] = t
            distinct = len({row2["record_id"] for row2 in rows[:t]} | labeled)
            res["screened_records"] = distinct
            res["screened_fraction"] = distinct / N
            res["missed_relevant"] = K - found_eff
            res["recall_at_stop"] = found_eff / K if K else 1.0
            res["safety_flags"].append("stopped_certified")
            if census:
                res["safety_flags"].append("census_at_look")
            res["details"].update({"looks": looks_log,
                                   "p_at_stop": p, "k_at_stop": kt,
                                   "alpha_budget_used": sum(incs[:l + 1])})
            return res
    res["details"]["looks"] = looks_log
    return _finish_no_stop(res, traj)


def evaluate_nb_window(traj: dict, window: int = 200, conf: float = 0.95,
                       k_mode: str = "recall", recall_target: float = 0.95,
                       look_positions: list[int] | None = None,
                       a0: float = 1.0, b0: float = 1.0) -> dict:
    """窗口 Gamma-Poisson(负二项)预测上界(notes §3.3 开放实现)。

    在 look t:取最后 W=min(window, t) 条已筛记录,窗口内发现 x_w 条相关;
    后验 Gamma(a0+x_w, b0+W);lam_ub = Gamma 分位数(conf);预测剩余上界
    R0_ub = (N-t)*lam_ub;R0_ub <= k_t ⇒ (启发式)停。
    """
    look_positions = look_positions or default_look_positions(traj["n_records"])
    res = _base_result("nb_window", {"window": window, "conf": conf,
                                     "k_mode": k_mode,
                                     "recall_target": recall_target if k_mode == "recall" else None,
                                     "a0": a0, "b0": b0,
                                     "looks": len(look_positions)})
    res["safety_flags"].append("heuristic_no_guarantee")
    K, N = traj["n_relevant"], traj["n_records"]
    rows = traj["rows"]
    looks_log = []
    for t in look_positions:
        if t > len(rows):
            break
        w0 = max(0, t - window)
        x_w = int(rows[t - 1]["n_relevant_found"]) - (
            int(rows[w0 - 1]["n_relevant_found"]) if w0 >= 1 else 0)
        W = t - w0
        a_post, b_post = a0 + x_w, b0 + W
        lam_ub = float(_gamma.ppf(conf, a_post, scale=1.0 / b_post))
        n_rem = N - t
        r0_ub = n_rem * lam_ub
        found = int(rows[t - 1]["n_relevant_found"])
        kt = k_target(k_mode, found, recall_target)
        looks_log.append({"look_t": t, "x_w": x_w, "W": W, "lam_ub": lam_ub,
                          "r0_ub": r0_ub, "k": kt})
        if r0_ub <= kt:
            res["stop_step"] = t
            res["screened_records"] = t
            res["screened_fraction"] = t / N
            res["missed_relevant"] = K - found
            res["recall_at_stop"] = found / K if K else 1.0
            res["safety_flags"].append("stopped_heuristic")
            res["details"].update({"looks": looks_log, "r0_ub_at_stop": r0_ub,
                                   "k_at_stop": kt})
            return res
    res["details"]["looks"] = looks_log
    return _finish_no_stop(res, traj)


def evaluate_combined(traj: dict, gold: dict[str, str], m: int, alpha: float,
                      k_mode: str = "zero", recall_target: float = 0.95,
                      look_positions: list[int] | None = None,
                      spending: str = "pocock", n_streak: int = 50,
                      seed: int = 0) -> dict:
    """双闸门(notes §4):look 处先查 streak 闸门(exclude_streak>=n_streak,
    不满足则跳过抽样,零成本),再查子样证书;两者同时满足才停。

    误差界:停止事件 ⊆ 证书事件 ⇒ 虚停率 <= sum(alpha_l) = alpha,
    与 streak 闸门的相关性无关(交集单调)。m 语义同 evaluate_pilot_subsample。
    """
    look_positions = look_positions or default_look_positions(traj["n_records"])
    rule = "combined"
    res = _base_result(rule, {"m": m, "alpha": alpha, "k_mode": k_mode,
                              "recall_target": recall_target if k_mode == "recall" else None,
                              "spending": spending, "n_streak": n_streak,
                              "looks": len(look_positions), "seed": seed})
    K, N = traj["n_relevant"], traj["n_records"]
    rows = traj["rows"]
    incs = spending_increments(alpha, len(look_positions), spending)
    rng = np.random.default_rng(seed)
    all_ids = list(gold.keys())
    prefix_pos = {row["record_id"]: int(row["i"]) for row in rows}
    for rid_key, lbl in gold.items():
        if rid_key in prefix_pos and rows[prefix_pos[rid_key] - 1]["label"] != lbl:
            raise ValueError(f"轨迹标签与 gold 不一致: {rid_key}")
    labeled: set[str] = set()
    subs_rel_ids: set[str] = set()
    total_sub = 0
    blocked = 0
    looks_log = []
    for l, t in enumerate(look_positions):
        if t > len(rows):
            break
        streak_now = int(rows[t - 1]["exclude_streak"])
        if streak_now < n_streak:
            blocked += 1
            looks_log.append({"look": l + 1, "t": t, "blocked_by_streak": True,
                              "streak": streak_now})
            continue
        pool = [rid for rid in all_ids
                if rid not in labeled and prefix_pos.get(rid, 10**9) > t]
        m0 = len(pool)
        if m0 == 0:
            break
        m_l = min(m, m0) if isinstance(m, int) else max(1, min(m0, math.ceil(m * m0)))
        pos = rng.choice(len(pool), size=m_l, replace=False)
        draw = [pool[i] for i in pos]
        x = sum(1 for rid in draw if gold[rid] == "include")
        labeled.update(draw)
        total_sub += m_l
        subs_rel_ids.update(rid for rid in draw if gold[rid] == "include")
        prefix_rel = {row2["record_id"] for row2 in rows[:t]
                      if row2["label"] == "include"}
        found_eff = len(prefix_rel | subs_rel_ids)
        kt = k_target(k_mode, found_eff, recall_target)
        p = pval_certify(m0, m_l, x, kt)
        census = (m_l == m0)
        looks_log.append({"look": l + 1, "t": t, "streak": streak_now, "m0": m0,
                          "m": m_l, "x": x, "k": kt, "p": p, "alpha_l": incs[l],
                          "census": census, "blocked_by_streak": False})
        if p <= incs[l]:
            res["stop_step"] = t
            distinct = len({row2["record_id"] for row2 in rows[:t]} | labeled)
            res["screened_records"] = distinct
            res["screened_fraction"] = distinct / N
            res["missed_relevant"] = K - found_eff
            res["recall_at_stop"] = found_eff / K if K else 1.0
            res["safety_flags"].append("stopped_certified")
            res["safety_flags"].append("streak_gate_passed")
            if census:
                res["safety_flags"].append("census_at_look")
            res["details"].update({"looks": looks_log, "p_at_stop": p,
                                   "k_at_stop": kt, "blocked_looks": blocked})
            return res
    res["details"]["looks"] = looks_log
    res["details"]["blocked_looks"] = blocked
    return _finish_no_stop(res, traj)


def evaluate_kempny_c1(traj: dict, gold: dict[str, str], sample_frac: float,
                       seed: int = 0) -> dict:
    """Kempny 2026 准则 1 复现:初始随机样本 s=round(sample_frac*N) 条人工判读,
    患病率估计 x_s/s ⇒ 相关总数估计 T=round(x_s/s*N);筛到找到 T 条即停。

    协议差异(如实标注):Kempny 用初始样本充当 ASReview 先验/训练集
    (占比 1%–30%);本复现的轨迹是 preset=asreview(先验 (1,1)),样本仅作
    估计源+额外人工成本,不回灌模型。T=0(样本零相关)时按其语义立即停
    (漏全部相关;flag=degenerate_zero_estimate)。
    """
    res = _base_result("kempny_c1", {"sample_frac": sample_frac, "seed": seed})
    K, N = traj["n_relevant"], traj["n_records"]
    rows = traj["rows"]
    all_ids = list(gold.keys())
    rng = np.random.default_rng(seed)
    s = max(1, int(round(sample_frac * N)))
    pos = rng.choice(len(all_ids), size=min(s, N), replace=False)
    sample = [all_ids[i] for i in pos]
    x_s = sum(1 for rid in sample if gold[rid] == "include")
    T = int(round(x_s / len(sample) * N))
    res["details"].update({"sample_size": len(sample), "x_sample": x_s,
                           "estimated_total": T, "true_total": K})
    found_sample = x_s  # 样本先于 AL 筛完(其发现数从 0 时刻起计入)
    if T <= 0:
        res["stop_step"] = 0
        res["screened_records"] = len(sample)
        res["screened_fraction"] = len(sample) / N
        res["missed_relevant"] = K
        res["recall_at_stop"] = 0.0
        res["safety_flags"].extend(["stopped_estimated_total",
                                    "degenerate_zero_estimate"])
        return res
    sample_rel = {rid for rid in sample if gold[rid] == "include"}
    for row in rows:
        t = int(row["i"])
        prefix_ids = {r2["record_id"] for r2 in rows[:t]}
        # 集合并口径(样本记录可能被轨迹继续筛到;真实协议中样本先筛、不重复)
        found_eff = len({r2["record_id"] for r2 in rows[:t]
                         if r2["label"] == "include"} | sample_rel)
        if found_eff >= T:
            res["stop_step"] = t
            res["screened_records"] = len(prefix_ids | set(sample))
            res["screened_fraction"] = res["screened_records"] / N
            res["missed_relevant"] = K - found_eff
            res["recall_at_stop"] = found_eff / K if K else 1.0
            res["safety_flags"].append("stopped_estimated_total")
            return res
    res["details"]["note"] = "T 未在轨迹内达到(相关已全部找到)⇒ 全量收尾"
    return _finish_no_stop(res, traj)


def kempny_k2_safe_threshold(traj: dict) -> dict:
    """Kempny 2026 准则 2 的安全阈值提取:n* = 最后一条相关记录之前出现过的
    最大连续排除长度(= 使准则 2 不漏任何相关的最小阈值 −1 的补)。

    语义:n <= n* ⇒ 触发行在最后相关之前 ⇒ 必遗漏;n > n* ⇒ 必安全。
    Kempny 以 n*/N(百分比)报告(其 5 集范围约 2%–61%)。
    """
    rows = traj["rows"]
    i_last = None
    for row in rows:
        if row["label"] == "include":
            i_last = int(row["i"])
    # n* = 最后一条相关记录之前出现过的最大 exclude_streak(exclude 行上)
    n_star = 0
    for row in rows:
        if int(row["i"]) > (i_last or 0):
            break
        if row["label"] == "exclude":
            n_star = max(n_star, int(row["exclude_streak"]))
    N = traj["n_records"]
    return {
        "rule": "kempny_k2", "params": {},
        "n_star": n_star, "n_star_frac_of_N": n_star / N,
        "last_relevant_position": i_last,
        "last_relevant_frac_of_N": (i_last / N) if i_last else None,
        "n_records": N, "n_relevant": traj["n_relevant"],
        "safety_flags": ["extractor_not_a_rule"],
    }


# ---------------------------------------------------------------------------
# 规则网格、批量评估与汇总
# ---------------------------------------------------------------------------

def default_look_positions(N: int, fractions=None) -> list[int]:
    fractions = fractions or DEFAULT_LOOK_FRACTIONS
    return sorted({max(1, int(round(f * N))) for f in fractions})


def default_rule_grid() -> list[dict]:
    """试点规则网格(见报告;共 33 条/轨迹)。"""
    grid: list[dict] = []
    for n in (10, 25, 50, 75, 100):
        grid.append({"family": "streak", "n": n})
    for m in (25, 50, 100):
        for alpha in (0.05, 0.01):
            for k_mode in ("zero", "recall"):
                grid.append({"family": "pilot_subsample", "m": m, "alpha": alpha,
                             "k_mode": k_mode, "recall_target": 0.95,
                             "spending": "pocock"})
    for k_mode in ("zero", "recall"):
        grid.append({"family": "pilot_subsample", "m": 50, "alpha": 0.05,
                     "k_mode": k_mode, "recall_target": 0.95, "spending": "obf"})
    # 比例子样 + 单 look(前沿式设计:大 m、看早位次;零遗漏作对照)
    for m_frac in (0.3, 0.5):
        for k_mode in ("zero", "recall"):
            grid.append({"family": "pilot_subsample", "m": m_frac, "alpha": 0.05,
                         "k_mode": k_mode, "recall_target": 0.95,
                         "spending": "pocock", "look_fractions": (0.5,)})
    for wm, k_mode in ((100, "recall"), (200, "recall"), (200, "zero")):
        grid.append({"family": "nb_window", "window": wm, "conf": 0.95,
                     "k_mode": k_mode, "recall_target": 0.95})
    for ns in (25, 50):
        grid.append({"family": "combined", "m": 50, "alpha": 0.05, "k_mode": "recall",
                     "recall_target": 0.95, "spending": "pocock", "n_streak": ns})
    grid.append({"family": "combined", "m": 50, "alpha": 0.05, "k_mode": "zero",
                 "recall_target": 0.95, "spending": "pocock", "n_streak": 50})
    for frac in (0.01, 0.05, 0.10):
        grid.append({"family": "kempny_c1", "sample_frac": frac})
    grid.append({"family": "kempny_k2"})
    return grid


def _rule_id(spec: dict) -> str:
    fam = spec["family"]
    if fam == "streak":
        return f"streak[n={spec['n']}]"
    if fam == "pilot_subsample":
        mtxt = str(spec["m"]) if isinstance(spec["m"], int) else f"{spec['m']:.0%}pool"
        ltxt = f"L={len(spec.get('look_fractions', (0.2, 0.4, 0.6, 0.8, 0.9)))}"
        return (f"subsample[m={mtxt},a={spec['alpha']},{spec['k_mode']},"
                f"{spec['spending']},{ltxt}]")
    if fam == "nb_window":
        return f"nb_window[W={spec['window']},conf={spec['conf']},{spec['k_mode']}]"
    if fam == "combined":
        return (f"combined[m={spec['m']},a={spec['alpha']},{spec['k_mode']},"
                f"{spec['spending']},streak{spec['n_streak']}]")
    if fam == "kempny_c1":
        return f"kempny_c1[sample={spec['sample_frac']:.0%}]"
    if fam == "kempny_k2":
        return "kempny_k2[safe_threshold]"
    raise ValueError(fam)


def evaluate_one(traj: dict, gold: dict[str, str], spec: dict,
                 seed: int = 0) -> dict:
    fam = spec["family"]
    if fam == "streak":
        return evaluate_streak(traj, spec["n"])
    if fam == "pilot_subsample":
        return evaluate_pilot_subsample(
            traj, gold, m=spec["m"], alpha=spec["alpha"], k_mode=spec["k_mode"],
            recall_target=spec.get("recall_target", 0.95),
            look_positions=default_look_positions(
                traj["n_records"], spec.get("look_fractions")),
            spending=spec["spending"], seed=seed)
    if fam == "nb_window":
        return evaluate_nb_window(
            traj, window=spec["window"], conf=spec["conf"], k_mode=spec["k_mode"],
            recall_target=spec.get("recall_target", 0.95),
            look_positions=default_look_positions(traj["n_records"]))
    if fam == "combined":
        return evaluate_combined(
            traj, gold, m=spec["m"], alpha=spec["alpha"], k_mode=spec["k_mode"],
            recall_target=spec.get("recall_target", 0.95),
            look_positions=default_look_positions(traj["n_records"]),
            spending=spec["spending"], n_streak=spec["n_streak"], seed=seed)
    if fam == "kempny_c1":
        return evaluate_kempny_c1(traj, gold, spec["sample_frac"], seed=seed)
    if fam == "kempny_k2":
        return kempny_k2_safe_threshold(traj)
    raise ValueError(fam)


def evaluate_all(traj: dict, gold: dict[str, str],
                 grid: list[dict] | None = None,
                 dataset: str = "", seed: int = 0) -> list[dict]:
    """对一条轨迹跑完规则网格;返回行列表(runs_detail 的一行/规则)。"""
    grid = grid or default_rule_grid()
    out = []
    for spec in grid:
        res = evaluate_one(traj, gold, spec, seed=seed)
        if spec["family"] == "kempny_k2":
            # 安全阈值提取器不是停止规则:输出 n*/末条相关位次,其余字段留空
            row = {
                "dataset": dataset, "seed": seed, "rule_id": _rule_id(spec),
                "family": spec["family"],
                "stop_step": None, "screened_records": None,
                "screened_fraction": None, "missed_relevant": None,
                "recall_at_stop": None,
                "safety_flags": ";".join(res["safety_flags"]),
                "params": json.dumps(res["params"], ensure_ascii=False, sort_keys=True),
                "n_star": res["n_star"],
                "n_star_frac_of_N": res["n_star_frac_of_N"],
                "last_relevant_frac_of_N": res["last_relevant_frac_of_N"],
            }
            out.append(row)
            continue
        row = {
            "dataset": dataset, "seed": seed, "rule_id": _rule_id(spec),
            "family": spec["family"],
            "stop_step": res["stop_step"],
            "screened_records": res["screened_records"],
            "screened_fraction": res["screened_fraction"],
            "missed_relevant": res["missed_relevant"],
            "recall_at_stop": res["recall_at_stop"],
            "safety_flags": ";".join(res["safety_flags"]),
            "params": json.dumps(res["params"], ensure_ascii=False, sort_keys=True),
        }
        out.append(row)
    return out


def summarize(run_rows: list[dict],
              by=("dataset", "rule_id")) -> list[dict]:
    """按 (dataset, rule) 聚合:P0(零遗漏比例)、missed 均值/P95、
    screened_fraction 均值/中位、recall 均值、flag 频率。纯标准库。"""
    groups: dict[tuple, list[dict]] = {}
    for r in run_rows:
        groups.setdefault(tuple(r[k] for k in by), []).append(r)
    out = []
    for key, rs in sorted(groups.items()):
        misses = [int(r["missed_relevant"]) for r in rs
                  if r.get("missed_relevant") is not None]
        fracs = [float(r["screened_fraction"]) for r in rs
                 if r.get("screened_fraction") is not None]
        recalls = [float(r["recall_at_stop"]) for r in rs
                   if r.get("recall_at_stop") is not None]
        row = dict(zip(by, key))
        row.update({
            "n_runs": len(rs),
            "P0_zero_miss": sum(1 for m in misses if m == 0) / len(misses) if misses else None,
            "missed_mean": sum(misses) / len(misses) if misses else None,
            "missed_p95": _percentile(misses, 95),
            "missed_max": max(misses) if misses else None,
            "screened_frac_mean": sum(fracs) / len(fracs) if fracs else None,
            "screened_frac_median": _percentile(fracs, 50),
            "recall_mean": sum(recalls) / len(recalls) if recalls else None,
            # recall>=0.95 达标率(recall 目标型规则的正确安全口径;zero 型即 P0)
            "recall_095_met_rate": (sum(1 for q in recalls if q >= 0.95 - 1e-9)
                                    / len(recalls)) if recalls else None,
            "flag_ran_to_completion": sum(
                1 for r in rs if "ran_to_completion" in (r.get("safety_flags") or "")) / len(rs),
            "flag_stopped_certified": sum(
                1 for r in rs if "stopped_certified" in (r.get("safety_flags") or "")) / len(rs),
        })
        out.append(row)
    return out


def _percentile(sorted_or_not: list, q: float):
    if not sorted_or_not:
        return None
    xs = sorted(sorted_or_not)
    if len(xs) == 1:
        return xs[0]
    idx = (len(xs) - 1) * q / 100.0
    lo, hi = int(math.floor(idx)), int(math.ceil(idx))
    if lo == hi:
        return xs[lo]
    return xs[lo] + (xs[hi] - xs[lo]) * (idx - lo)
