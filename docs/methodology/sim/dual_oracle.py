"""S2:双盲双筛员 + κ 仲裁拓扑模拟器(研究计划书 S2 节;设计见
``research/notes/dual_oracle_design.md``)。

拓扑(``DualConfig.topology``):

- ``single``      单筛员基线(= S0 引擎语义;无噪声时与 ``replay.replay_fast``
                   轨迹逐位一致,测试锚定);
- ``dual_shared`` 共享排序双盲(子模式 a,产品结构):一个模型一个队列,每条记录
                   由 A/B 从同一队列头各自盲筛一次;一致→共识终判,不一致或任一
                   maybe→仲裁;**仲裁后终判入训练**;每记录重训重排一次;
- ``dual_indep``  独立模型双盲(子模式 b):A/B 各训自己的 TF-IDF+NB 排序器、队列
                   独立;每轮各筛本方队列头一条;一条记录集齐两判才终判(后到者
                   触发结算);仲裁终判回写双方训练集(纠正原始标签);各筛员先以
                   自己标签训练(maybe 不训练,产品语义);own-decision 连续排除
                   计数,联合停止 = 双方 streak 同时 ≥ 阈值;
- ``pool``        ASReview v2 共享池对照:A/B 交替各筛一条,**每条记录只筛一次**,
                   原始标签即时入训练(maybe→仲裁后以仲裁标签入训练);吞吐×2、
                   安全=单筛员的对照极。

筛员噪声:se(P(include|相关)) / sp(P(exclude|不相关)) / p_maybe 逐记录独立
Bernoulli;仲裁方同参数化但不允许 maybe(默认完美=黄金标签)。

成本:``cost = w_decision×#筛查决策 + w_arbitration×#仲裁``(权重显式参数)。

停止:在线判定(T2/T4/T1 在终判流上计 streak;T3 双方 own-stream streak 同时达
阈值);``stop_streak=None`` 全程跑完(安全上界 + 信息量分析)。

组件复用(均 import 自 ``replay.py``,未复制):``_CorpusIndex``(一次性分词+固定
词表 CSR)、``_scale_normalize``(tf×idf+L2)、``_rank_queue``(产品 query_order
等价排序)、``_validated_gold``、``load_dataset_csv``、``make_synthetic_corpus``、
``FastRecord``。NB 拟合+打分在 replay 中是 ``replay_fast`` 内部闭包、不可导入,
此处按其数学逐行重实现为 :func:`_nb_fit_score`,由测试两级锚定:①与产品
``coscreen.al.nb.MultinomialNB``(只读)同输入分数差 ≤1e-12;②无噪声 single 轨迹
与 ``replay_fast`` 逐位一致。κ 对照:产品现成实现位于
``coscreen/stats/kappa.py::cohen_kappa``(只读 import,未改动)。

日志:每记录(T1/T2/T4)或每筛员步(T3)一行 JSONL + 汇总 JSON。

CLI::

    ./.venv/bin/python research/sim/dual_oracle.py pilot [--out DIR] [--quick]
    ./.venv/bin/python research/sim/dual_oracle.py summarize [--out DIR]
    ./.venv/bin/python research/sim/dual_oracle.py run --dataset NAME [--topology ...]
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from coscreen import config  # noqa: E402  (只读:AL_DEFAULT_STRATEGY 等常量)
from coscreen.stats.kappa import cohen_kappa  # noqa: E402  (只读产品 κ 参照)

import replay as R  # noqa: E402  (S0 引擎,只读;同目录,conftest/脚本路径均可导入)
from replay import (  # noqa: E402
    WARMUP,
    FastRecord,
    _CorpusIndex,
    _rank_queue,
    _scale_normalize,
    _validated_gold,
    load_dataset_csv,
    make_synthetic_corpus,
)

__all__ = [
    "SCREENER_DECISIONS", "ScreenerNoise", "PERFECT_SCREENER",
    "NOISY_95_99", "NOISE_PRESETS", "DualConfig", "DualResult",
    "screen_decision", "resolve_pair", "arbitrate", "simulate_dual",
    "run_pilot", "summarize_pilot", "PILOT_DATASETS", "PILOT_TOPOLOGIES",
]

#: 筛员决策值域(产品 ranker 语义;maybe = 待仲裁)。
SCREENER_DECISIONS = ("include", "exclude", "maybe")
_TOPOLOGIES = ("single", "dual_shared", "dual_indep", "pool")
_DATA_DIR = PROJECT_ROOT / "research" / "data" / "synergy"
_DEFAULT_OUT = PROJECT_ROOT / "research" / "results" / "s2_pilot"


# ---------------------------------------------------------------------------
# 筛员行为模型
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScreenerNoise:
    """筛员(或仲裁方)行为参数:se=P(include|相关)、sp=P(exclude|不相关)、
    p_maybe=把已抽得的决策改写为 maybe 的概率。1/1/0 = 完美筛员。"""

    se: float = 1.0
    sp: float = 1.0
    p_maybe: float = 0.0

    def validated(self) -> "ScreenerNoise":
        for name, v in (("se", self.se), ("sp", self.sp), ("p_maybe", self.p_maybe)):
            if not 0.0 <= v <= 1.0:
                raise ValueError(f"{name} 必须在 [0,1],得到 {v!r}")
        return self


PERFECT_SCREENER = ScreenerNoise()
#: 任务书噪声组:se=0.95 / sp=0.99(A、B 独立同参)。
NOISY_95_99 = ScreenerNoise(se=0.95, sp=0.99)

#: 噪声配置名 → 三方参数(A、B 独立;仲裁默认完美)。
NOISE_PRESETS: dict[str, dict[str, ScreenerNoise]] = {
    "perfect": {"noise_a": PERFECT_SCREENER, "noise_b": PERFECT_SCREENER,
                "noise_arbiter": PERFECT_SCREENER},
    "noisy95_99": {"noise_a": NOISY_95_99, "noise_b": NOISY_95_99,
                   "noise_arbiter": PERFECT_SCREENER},
}


def screen_decision(gold_label: str, noise: ScreenerNoise, rng: random.Random) -> str:
    """一次盲筛决策:按 se/sp 抽 include/exclude,再按 p_maybe 改写为 maybe。"""
    if gold_label not in ("include", "exclude"):
        raise ValueError(f"黄金标签非法: {gold_label!r}")
    noise.validated()
    if gold_label == "include":
        d = "include" if rng.random() < noise.se else "exclude"
    else:
        d = "exclude" if rng.random() < noise.sp else "include"
    if noise.p_maybe > 0.0 and rng.random() < noise.p_maybe:
        d = "maybe"
    return d


def resolve_pair(dec_a: str, dec_b: str) -> tuple[str | None, bool]:
    """双判结算:返回 (终判或 None, 是否需仲裁)。

    一致且非 maybe → 共识终判;否则(不一致或任一 maybe)→ 仲裁。
    """
    for d in (dec_a, dec_b):
        if d not in SCREENER_DECISIONS:
            raise ValueError(f"决策非法: {d!r}")
    if dec_a == dec_b and dec_a in ("include", "exclude"):
        return dec_a, False
    return None, True


def arbitrate(gold_label: str, noise: ScreenerNoise, rng: random.Random) -> str:
    """仲裁:同 se/sp 参数化,但不允许 maybe(仲裁必须给出终判)。"""
    return screen_decision(gold_label, ScreenerNoise(noise.se, noise.sp, 0.0), rng)


# ---------------------------------------------------------------------------
# NB 拟合 + 打分(replay_fast 闭包的逐行数学重实现;闭包不可导入)
# ---------------------------------------------------------------------------

def _nb_fit_score(Xt, Xc, y, alpha: float = 1.0,
                  support: np.ndarray | None = None) -> np.ndarray:
    """MultinomialNB(alpha) 拟合于 (Xt, y) 并对 Xc 逐行打分 P(y=1|x)。

    与 ``replay.replay_fast._nb_fit_score`` 闭包同构:类先验 ``log(n1/n)``
    (缺席类 −inf);特征对数概率仅定义在 support 列(词表外贡献恰 0,等价
    产品"忽略未见词项");logsumexp 归一化取 P(y=1)。
    """
    y_arr = np.asarray(y, dtype=np.int64)
    n_tr = int(Xt.shape[0])
    n1 = int((y_arr == 1).sum())
    n0c = n_tr - n1
    p1 = math.log(n1 / n_tr) if n1 else -math.inf
    p0 = math.log(n0c / n_tr) if n0c else -math.inf
    s1 = np.asarray(Xt[y_arr == 1].sum(axis=0)).ravel()
    s0 = np.asarray(Xt[y_arr == 0].sum(axis=0)).ravel()
    Vt = int(Xt.shape[1]) if support is None else int(support.size)
    if support is not None:
        s1v, s0v = s1[support], s0[support]
    else:
        s1v, s0v = s1, s0
    denom1 = float(s1v.sum()) + alpha * Vt
    denom0 = float(s0v.sum()) + alpha * Vt
    V = int(Xt.shape[1])
    F1 = np.zeros(V, dtype=np.float64)
    F0 = np.zeros(V, dtype=np.float64)
    if support is not None:
        F1[support] = np.log(s1v + alpha) - math.log(denom1)
        F0[support] = np.log(s0v + alpha) - math.log(denom0)
    else:
        F1[:] = np.log(s1v + alpha) - math.log(denom1)
        F0[:] = np.log(s0v + alpha) - math.log(denom0)
    jll = Xc @ np.column_stack([F0, F1])
    j0 = p0 + jll[:, 0]
    j1 = p1 + jll[:, 1]
    hi = np.maximum(j0, j1)
    with np.errstate(under="ignore", divide="ignore", invalid="ignore"):
        le = hi + np.log(np.exp(j0 - hi) + np.exp(j1 - hi))
        le = np.where(np.isfinite(hi), le, hi)
        return np.exp(j1 - le)


class _TfidfModel:
    """一个筛员(或共享)的 AL 模型:增量 df + 每训练步全量 refit + 重排。

    与 replay 引擎同构:训练行 = 已筛(非 maybe)记录,顺序 = 筛选顺序;
    ``set(key, y)`` 供仲裁回写(已有行改标签,无行则新增——df 只在首次入训
    时增加;标签改向不改变词频,故 df 不动)。
    """

    def __init__(self, index: _CorpusIndex, seed: int, strategy: str,
                 warmup: int, alpha: float) -> None:
        self.index = index
        self.seed = seed
        self.strategy = strategy
        self.warmup = warmup
        self.alpha = alpha
        self.pos_of = {k: i for i, k in enumerate(index.keys)}
        self.labeled_pos: list[int] = []
        self.y: list[int] = []
        self.row_of: dict[str, int] = {}
        self.df = np.zeros(index.V, dtype=np.int64)
        self.last_scores: dict[str, float] | None = None
        self.n_train_steps = 0

    @property
    def ready(self) -> bool:
        return len(self.labeled_pos) >= self.warmup

    def _add_row(self, key: str, y01: int) -> None:
        p = self.pos_of[key]
        self.row_of[key] = len(self.labeled_pos)
        self.labeled_pos.append(p)
        self.y.append(y01)
        self.df[self.index.doc_terms[p]] += 1

    def set(self, key: str, label: str) -> None:
        """训练行写入/仲裁回写:已有行改标签,否则新增。"""
        y01 = 1 if label == "include" else 0
        if key in self.row_of:
            self.y[self.row_of[key]] = y01
        else:
            self._add_row(key, y01)

    def refit_rerank(self, queue: list[str]) -> list[str]:
        """全量 refit + 对当前队列重排(更新 last_scores);返回新队列。"""
        n = len(self.labeled_pos)
        sup = np.flatnonzero(self.df)
        idf_full = np.zeros(self.index.V, dtype=np.float64)
        idf_full[sup] = np.log((1.0 + n) / (1.0 + self.df[sup])) + 1.0
        cand_pos = np.array([self.pos_of[k] for k in queue], dtype=np.int64)
        Xt = _scale_normalize(self.index.counts[self.labeled_pos], idf_full)
        Xc = _scale_normalize(self.index.counts[cand_pos], idf_full)
        scores = _nb_fit_score(Xt, Xc, self.y, self.alpha, sup)
        sdict = {k: float(s) for k, s in zip(queue, scores)}
        self.last_scores = sdict
        self.n_train_steps += 1
        return _rank_queue(queue, scores, self.strategy, self.seed, sdict)

    def score_all(self) -> dict[str, float]:
        """终局分析用:以当前训练态对全语料打分排序(不改训练状态)。"""
        all_keys = list(self.index.keys)
        order = self.refit_rerank(all_keys)
        return {k: self.last_scores[k] for k in order}


# ---------------------------------------------------------------------------
# 配置与结果
# ---------------------------------------------------------------------------

@dataclass
class DualConfig:
    topology: str = "dual_shared"
    seed: int = 0
    strategy: str = config.AL_DEFAULT_STRATEGY
    warmup: int = WARMUP
    alpha: float = 1.0
    noise_a: ScreenerNoise = field(default_factory=lambda: PERFECT_SCREENER)
    noise_b: ScreenerNoise = field(default_factory=lambda: PERFECT_SCREENER)
    noise_arbiter: ScreenerNoise = field(default_factory=lambda: PERFECT_SCREENER)
    w_decision: float = 1.0
    w_arbitration: float = 1.0
    stop_streak: int | None = 50
    single_exclude_policy: str = "arbitrate"  # 或 "asis"(敏感性分析)

    def validated(self) -> "DualConfig":
        if self.topology not in _TOPOLOGIES:
            raise ValueError(f"未知拓扑: {self.topology!r}(可选 {_TOPOLOGIES})")
        if self.strategy not in ("max", "uncertainty", "mixed"):
            raise ValueError(f"未知查询策略: {self.strategy!r}")
        if self.single_exclude_policy not in ("arbitrate", "asis"):
            raise ValueError(f"未知单决策策略: {self.single_exclude_policy!r}")
        if self.warmup < 1:
            raise ValueError("warmup 必须 ≥ 1")
        if self.stop_streak is not None and self.stop_streak < 1:
            raise ValueError("stop_streak 必须 ≥ 1 或 None")
        if self.w_decision < 0 or self.w_arbitration < 0:
            raise ValueError("成本权重必须非负")
        for n in (self.noise_a, self.noise_b, self.noise_arbiter):
            n.validated()
        return self


@dataclass
class DualResult:
    """一次双筛员模拟的汇总指标 + 完整决策日志(字段见模块 docstring)。"""

    topology: str
    seed: int
    n_records: int
    n_relevant: int
    stopped: bool
    n_screened_records: int
    n_decisions: int
    n_decisions_a: int
    n_decisions_b: int
    n_arbitrations: int
    n_maybe: int
    n_co_screened: int
    n_included_final: int
    miss_count: int
    miss_tail_count: int
    cost: float
    w_decision: float
    w_arbitration: float
    kappa_ab: float | None
    kappa_po: float | None
    kappa_n: int
    uncertainty_disagreement_r: float | None
    arb_precision: float | None
    topk_overlap: dict
    n_train_steps: int
    elapsed_s: float
    config: dict = field(default_factory=dict)
    trajectory: list[dict] = field(default_factory=list)

    @property
    def miss_rate(self) -> float:
        return self.miss_count / self.n_relevant if self.n_relevant else 0.0

    @property
    def miss_screened_count(self) -> int:
        return self.miss_count - self.miss_tail_count

    @property
    def depth(self) -> float:
        return self.n_screened_records / self.n_records if self.n_records else 0.0

    @property
    def arb_share(self) -> float:
        return (self.w_arbitration * self.n_arbitrations / self.cost
                if self.cost else 0.0)

    def summary_row(self) -> dict:
        return {
            "topology": self.topology, "seed": self.seed,
            "n_records": self.n_records, "n_relevant": self.n_relevant,
            "stopped": self.stopped,
            "n_screened_records": self.n_screened_records, "depth": self.depth,
            "n_decisions": self.n_decisions,
            "n_decisions_a": self.n_decisions_a,
            "n_decisions_b": self.n_decisions_b,
            "n_arbitrations": self.n_arbitrations, "n_maybe": self.n_maybe,
            "n_co_screened": self.n_co_screened,
            "n_included_final": self.n_included_final,
            "miss_count": self.miss_count, "miss_rate": self.miss_rate,
            "miss_tail_count": self.miss_tail_count,
            "miss_screened_count": self.miss_screened_count,
            "n_arbitration_cost_units": self.w_arbitration * self.n_arbitrations,
            "cost": self.cost, "arb_share": self.arb_share,
            "kappa_ab": self.kappa_ab, "kappa_po": self.kappa_po,
            "kappa_n": self.kappa_n,
            "uncertainty_disagreement_r": self.uncertainty_disagreement_r,
            "arb_precision": self.arb_precision,
            "topk_overlap": self.topk_overlap,
            "n_train_steps": self.n_train_steps,
            "elapsed_s": self.elapsed_s,
            "w_decision": self.w_decision, "w_arbitration": self.w_arbitration,
            "config": self.config,
        }

    def save_logs(self, prefix: str | Path) -> tuple[Path, Path]:
        prefix = Path(prefix)
        prefix.parent.mkdir(parents=True, exist_ok=True)
        jsonl_path = prefix.with_suffix(".trajectory.jsonl")
        json_path = prefix.with_suffix(".summary.json")
        summary = {k: v for k, v in self.summary_row().items() if k != "topk_overlap"}
        summary["topk_overlap"] = self.topk_overlap
        summary["n_trajectory_rows"] = len(self.trajectory)
        with jsonl_path.open("w", encoding="utf-8") as f:
            for row in self.trajectory:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        json_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return jsonl_path, json_path


# ---------------------------------------------------------------------------
# 主引擎
# ---------------------------------------------------------------------------

def _seed_stream(base_seed: int) -> dict[str, random.Random]:
    """确定性随机流派生:seed*4+k(k=0 排序备用 / 1=A / 2=B / 3=仲裁)。"""
    return {
        "a": random.Random(base_seed * 4 + 1),
        "b": random.Random(base_seed * 4 + 2),
        "arb": random.Random(base_seed * 4 + 3),
    }


def _final_metrics(keys, labels, relevant, final_of, screened_any,
                   cfg: DualConfig, counters: dict, kappa_pairs, unc_pairs,
                   topk_overlap, n_train_steps, elapsed_s, stopped) -> DualResult:
    final_of = dict(final_of)
    n_arb = counters["n_arb"]
    n_dec = counters["n_dec"]
    cost = cfg.w_decision * n_dec + cfg.w_arbitration * n_arb
    miss_keys = [k for k in relevant if final_of.get(k) != "include"]
    miss_tail = [k for k in miss_keys if not screened_any.get(k)]
    kappa_res = cohen_kappa([a for a, _ in kappa_pairs], [b for _, b in kappa_pairs]) \
        if kappa_pairs else None
    # 分歧 vs 不确定性的点二列相关(Pearson;退化(全同分/无分歧)时为 None)。
    r_val: float | None = None
    if len(unc_pairs) >= 3:
        pairs = [(u, d) for u, d in unc_pairs if np.isfinite(u)]
        if len(pairs) >= 3:
            xs = np.array([u for u, _ in pairs], dtype=np.float64)
            ys = np.array([d for _, d in pairs], dtype=np.float64)
            if xs.std() > 0 and ys.std() > 0:
                r_val = float(np.corrcoef(xs, ys)[0, 1])
    arb_rel = counters.get("arb_relevant", 0)
    arb_precision = (arb_rel / n_arb) if n_arb else None
    return DualResult(
        topology=cfg.topology, seed=cfg.seed,
        n_records=len(keys), n_relevant=len(relevant),
        stopped=stopped,
        n_screened_records=counters["n_screened"],
        n_decisions=n_dec,
        n_decisions_a=counters["n_dec_a"], n_decisions_b=counters["n_dec_b"],
        n_arbitrations=n_arb, n_maybe=counters["n_maybe"],
        n_co_screened=counters["n_co_screened"],
        n_included_final=sum(1 for v in final_of.values() if v == "include"),
        miss_count=len(miss_keys), miss_tail_count=len(miss_tail),
        cost=cost,
        w_decision=cfg.w_decision, w_arbitration=cfg.w_arbitration,
        kappa_ab=(kappa_res.kappa if kappa_res else None),
        kappa_po=(kappa_res.po if kappa_res else None),
        kappa_n=(kappa_res.n if kappa_res else 0),
        uncertainty_disagreement_r=r_val,
        arb_precision=arb_precision,
        topk_overlap=topk_overlap,
        n_train_steps=n_train_steps, elapsed_s=elapsed_s,
        config={
            "strategy": cfg.strategy, "warmup": cfg.warmup, "alpha": cfg.alpha,
            "noise_a": vars(cfg.noise_a), "noise_b": vars(cfg.noise_b),
            "noise_arbiter": vars(cfg.noise_arbiter),
            "stop_streak": cfg.stop_streak,
            "single_exclude_policy": cfg.single_exclude_policy,
            "seed_streams": "base*4+{0:order,1:A,2:B,3:arb}",
        },
        trajectory=counters["trajectory"],
    )


def simulate_dual(records: list, gold: dict[str, str],
                  cfg: DualConfig) -> DualResult:
    """按 ``cfg.topology`` 运行一次双筛员(或单筛/共享池)模拟。"""
    t0 = time.perf_counter()
    cfg.validated()
    keys = [r.zotero_key for r in records]
    if len(set(keys)) != len(keys):
        raise ValueError("records 的 zotero_key 必须唯一")
    labels = _validated_gold(keys, gold)
    relevant = {k for k in keys if labels[k] == "include"}
    index = _CorpusIndex(records)
    rngs = _seed_stream(cfg.seed)
    seed_a, seed_b = cfg.seed * 4 + 1, cfg.seed * 4 + 2

    counters: dict = {
        "n_screened": 0, "n_dec": 0, "n_dec_a": 0, "n_dec_b": 0,
        "n_arb": 0, "n_maybe": 0, "n_co_screened": 0, "arb_relevant": 0,
        "trajectory": [],
    }
    screened_any: dict[str, bool] = {}
    final_of: dict[str, str] = {}
    kappa_pairs: list[tuple[str, str]] = []
    unc_pairs: list[tuple[float, int]] = []
    topk_overlap: dict = {}
    n_train_steps = 0
    stopped = False

    if cfg.topology in ("single", "pool", "dual_shared"):
        model = _TfidfModel(index, seed_a, cfg.strategy, cfg.warmup, cfg.alpha)
        order = _rank_queue(list(keys), np.zeros(len(keys)), cfg.strategy,
                            seed_a, None)
        streak = 0
        i = 0
        turn = 0  # pool:A/B 交替;single 恒 A
        while order:
            if cfg.stop_streak is not None and streak >= cfg.stop_streak:
                stopped = True
                break
            key = order.pop(0)
            i += 1
            counters["n_screened"] += 1
            screened_any[key] = True
            score_head = (model.last_scores or {}).get(key)
            if cfg.topology == "dual_shared":
                da = screen_decision(labels[key], cfg.noise_a, rngs["a"])
                db = screen_decision(labels[key], cfg.noise_b, rngs["b"])
                counters["n_dec"] += 2
                counters["n_dec_a"] += 1
                counters["n_dec_b"] += 1
                counters["n_maybe"] += (da == "maybe") + (db == "maybe")
                final, needs = resolve_pair(da, db)
                arbitrated = needs
                if needs:
                    final = arbitrate(labels[key], cfg.noise_arbiter, rngs["arb"])
                    counters["n_arb"] += 1
                    if labels[key] == "include":
                        counters["arb_relevant"] += 1
                    if score_head is not None:
                        unc_pairs.append((abs(score_head - 0.5), 1))
                elif score_head is not None:
                    unc_pairs.append((abs(score_head - 0.5), 0))
                kappa_pairs.append((da, db))
                counters["n_co_screened"] += 1
                dec_a_out, dec_b_out = da, db
            else:  # single / pool:每记录 1 次决策(single 恒 A;pool A/B 交替)
                if cfg.topology == "single":
                    who, noise, rng = "a", cfg.noise_a, rngs["a"]
                else:
                    who, noise, rng = (("a", cfg.noise_a, rngs["a"]) if turn == 0
                                       else ("b", cfg.noise_b, rngs["b"]))
                    turn ^= 1
                da = screen_decision(labels[key], noise, rng)
                db = None
                counters["n_dec"] += 1
                counters[f"n_dec_{who}"] += 1
                counters["n_maybe"] += da == "maybe"
                if da == "maybe":
                    final = arbitrate(labels[key], cfg.noise_arbiter, rngs["arb"])
                    counters["n_arb"] += 1
                    if labels[key] == "include":
                        counters["arb_relevant"] += 1
                    arbitrated = True
                else:
                    final = da
                    arbitrated = False
                if score_head is not None:
                    unc_pairs.append((abs(score_head - 0.5), 1 if arbitrated else 0))
                dec_a_out, dec_b_out = da, None
            # 训练(single/pool=原始标签即入训;dual_shared=终判入训)+ 终判流
            model.set(key, final)
            final_of[key] = final
            streak = 0 if final == "include" else streak + 1
            trained = False
            if order and model.ready:
                order = model.refit_rerank(order)
                trained = True
                n_train_steps += 1
            counters["trajectory"].append({
                "i": i, "topology": cfg.topology, "record_id": key,
                "gold": labels[key], "dec_a": dec_a_out, "dec_b": dec_b_out,
                "final": final, "arbitrated": arbitrated,
                "score_when_head": score_head,
                "streak_after": streak,
                "n_dec_a": counters["n_dec_a"], "n_dec_b": counters["n_dec_b"],
                "n_arb": counters["n_arb"], "n_maybe": counters["n_maybe"],
                "cost_so_far": cfg.w_decision * counters["n_dec"]
                               + cfg.w_arbitration * counters["n_arb"],
                "n_labeled": len(model.labeled_pos), "trained": trained,
            })

    else:  # dual_indep
        model_a = _TfidfModel(index, seed_a, cfg.strategy, cfg.warmup, cfg.alpha)
        model_b = _TfidfModel(index, seed_b, cfg.strategy, cfg.warmup, cfg.alpha)
        queue_a = _rank_queue(list(keys), np.zeros(len(keys)), cfg.strategy,
                              seed_a, None)
        queue_b = _rank_queue(list(keys), np.zeros(len(keys)), cfg.strategy,
                              seed_b, None)
        own: dict[str, dict[str, str]] = {"A": {}, "B": {}}
        streaks = {"A": 0, "B": 0}
        stop_flag = {"A": False, "B": False}
        rnd = 0
        while True:
            if cfg.stop_streak is not None and stop_flag["A"] and stop_flag["B"]:
                stopped = True
                break
            if (not queue_a or stop_flag["A"]) and (not queue_b or stop_flag["B"]):
                break
            rnd += 1
            for side, model, queue, rng, noise in (
                    ("A", model_a, queue_a, rngs["a"], cfg.noise_a),
                    ("B", model_b, queue_b, rngs["b"], cfg.noise_b)):
                if stop_flag[side] or not queue:
                    continue
                key = queue.pop(0)
                counters["n_screened"] += 0 if screened_any.get(key) else 1
                screened_any[key] = True
                counters["n_dec"] += 1
                counters[f"n_dec_{side.lower()}"] += 1
                d = screen_decision(labels[key], noise, rng)
                own[side][key] = d
                counters["n_maybe"] += d == "maybe"
                if d != "maybe":
                    model.set(key, d)  # 自己的原始标签先入本人训练(maybe 不训练)
                score_head = (model.last_scores or {}).get(key)
                partner = "B" if side == "A" else "A"
                partner_d = own[partner].get(key)
                final_out = None
                arbitrated = False
                if partner_d is not None:  # 集齐两判 → 结算
                    counters["n_co_screened"] += 1
                    final, needs = resolve_pair(d, partner_d)
                    arbitrated = needs
                    if needs:
                        final = arbitrate(labels[key], cfg.noise_arbiter, rngs["arb"])
                        counters["n_arb"] += 1
                        if labels[key] == "include":
                            counters["arb_relevant"] += 1
                        # 仲裁终判回写双方训练集(纠正/补齐)
                        model_a.set(key, final)
                        model_b.set(key, final)
                        if score_head is not None:
                            unc_pairs.append((abs(score_head - 0.5), 1))
                    elif score_head is not None:
                        unc_pairs.append((abs(score_head - 0.5), 0))
                    final_of[key] = final
                    kappa_pairs.append((own["A"][key], own["B"][key]))
                    final_out = final
                streaks[side] = streaks[side] + 1 if d == "exclude" else 0
                trained = False
                if queue and model.ready:
                    new_q = model.refit_rerank(queue)
                    if side == "A":
                        queue_a[:] = new_q
                    else:
                        queue_b[:] = new_q
                    trained = True
                    n_train_steps += 1
                counters["trajectory"].append({
                    "round": rnd, "screener": side, "record_id": key,
                    "gold": labels[key], "dec": d, "partner_dec": partner_d,
                    "final": final_out, "arbitrated": arbitrated,
                    "score_when_head": score_head,
                    "streak_after": streaks[side],
                    "n_dec_a": counters["n_dec_a"], "n_dec_b": counters["n_dec_b"],
                    "n_arb": counters["n_arb"],
                    "cost_so_far": cfg.w_decision * counters["n_dec"]
                                   + cfg.w_arbitration * counters["n_arb"],
                    "n_labeled": len(model.labeled_pos), "trained": trained,
                })
                if cfg.stop_streak is not None and streaks[side] >= cfg.stop_streak:
                    stop_flag[side] = True
        # 停止/队列耗尽后的单决策记录(只有一方筛过)
        for key in [k for k in keys if k not in final_of
                    and (k in own["A"]) != (k in own["B"])]:
            side = "A" if key in own["A"] else "B"
            d = own[side][key]
            if cfg.single_exclude_policy == "arbitrate" or d == "maybe":
                final = arbitrate(labels[key], cfg.noise_arbiter, rngs["arb"])
                counters["n_arb"] += 1
                if labels[key] == "include":
                    counters["arb_relevant"] += 1
            else:  # "asis":单决策直接作终判(maybe 维持未纳入)
                final = d
            final_of[key] = final
            counters["trajectory"].append({
                "event": "single_decision_policy", "screener": side,
                "record_id": key, "gold": labels[key], "dec": d, "final": final,
                "policy": cfg.single_exclude_policy,
            })
        # 终局两模型一致性@k(全语料重打分)
        if model_a.labeled_pos and model_b.labeled_pos:
            rank_a = list(model_a.score_all())
            rank_b = list(model_b.score_all())
            topk_overlap = {}
            for k in (10, 50):
                sa, sb = set(rank_a[:k]), set(rank_b[:k])
                topk_overlap[f"top{k}_overlap"] = len(sa & sb) / k
                topk_overlap[f"top{k}_jaccard"] = len(sa & sb) / len(sa | sb)

    return _final_metrics(keys, labels, relevant, final_of, screened_any,
                          cfg, counters, kappa_pairs, unc_pairs, topk_overlap,
                          n_train_steps, time.perf_counter() - t0, stopped)


# ---------------------------------------------------------------------------
# 试点运行器与汇总
# ---------------------------------------------------------------------------

PILOT_DATASETS = ("Donners_2021", "van_der_Valk_2021", "Jeyaraman_2020")
PILOT_TOPOLOGIES = ("single", "dual_shared", "dual_indep", "pool")
_PILOT_STOPS = (50, None)


def _load_pilot_datasets(names) -> dict:
    out = {}
    for name in names:
        out[name] = load_dataset_csv(_DATA_DIR / f"{name}.csv")
    return out


def run_pilot(out_dir: str | Path = _DEFAULT_OUT, *, quick: bool = False,
              datasets=PILOT_DATASETS, topologies=PILOT_TOPOLOGIES,
              noises=("perfect", "noisy95_99"), stops=_PILOT_STOPS,
              seeds=(0, 1, 2, 3, 4)) -> Path:
    """试点:数据集 × 拓扑 × 噪声 × 停止 × 种子;全程 stop 追加轨迹日志。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if quick:
        datasets, seeds = datasets[:1], (0, 1)
    loaded = _load_pilot_datasets(datasets)
    runs_path = out_dir / "s2_pilot_runs.jsonl"
    t_all = time.perf_counter()
    n_runs = 0
    with runs_path.open("w", encoding="utf-8") as f:
        for ds_name in datasets:
            records, gold = loaded[ds_name]
            for topo in topologies:
                for noise_name in noises:
                    for stop in stops:
                        for seed in seeds:
                            cfg = DualConfig(
                                topology=topo, seed=seed, stop_streak=stop,
                                **NOISE_PRESETS[noise_name])
                            res = simulate_dual(records, gold, cfg)
                            row = {"dataset": ds_name, "noise": noise_name,
                                   "stop_streak": stop, **res.summary_row()}
                            f.write(json.dumps(row, ensure_ascii=False) + "\n")
                            if stop is None:
                                res.save_logs(out_dir / (
                                    f"traj-{ds_name}-{topo}-{noise_name}"
                                    f"-full-s{seed}"))
                            n_runs += 1
                            print(f"[pilot] {ds_name} {topo} {noise_name} "
                                  f"stop={stop} s{seed}: miss={res.miss_count}"
                                  f"/{res.n_relevant} cost={res.cost:.0f} "
                                  f"depth={res.depth:.2f} "
                                  f"({res.elapsed_s:.2f}s)", flush=True)
    print(f"[pilot] {n_runs} runs in {time.perf_counter() - t_all:.1f}s "
          f"→ {runs_path}", flush=True)
    return runs_path


_AGG_FIELDS = (
    "miss_rate", "miss_count", "miss_tail_count", "miss_screened_count",
    "depth", "n_decisions", "n_arbitrations", "cost", "arb_share",
    "kappa_ab", "uncertainty_disagreement_r", "arb_precision",
    "n_screened_records",
)


def summarize_pilot(out_dir: str | Path = _DEFAULT_OUT) -> Path:
    """按 (dataset, noise, stop_streak, topology) 聚合 mean±sd → CSV + 控制台表。"""
    out_dir = Path(out_dir)
    runs_path = out_dir / "s2_pilot_runs.jsonl"
    rows = [json.loads(line) for line in
            runs_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault(
            (r["dataset"], r["noise"], r["stop_streak"], r["topology"]), []
        ).append(r)

    def _flat(group):
        out = {}
        for f_ in _AGG_FIELDS:
            vals = [g[f_] for g in group if g.get(f_) is not None]
            if vals and all(isinstance(v, (int, float)) for v in vals):
                mean = sum(vals) / len(vals)
                sd = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
                out[f_] = round(mean, 6)
                out[f_ + "_sd"] = round(sd, 6)
        return out

    agg_rows = []
    for (ds, noise, stop, topo), group in sorted(
            groups.items(), key=lambda kv: (kv[0][0], kv[0][1],
                                            -(kv[0][2] or 10 ** 9), kv[0][3])):
        agg = {"dataset": ds, "noise": noise, "stop_streak": stop,
               "topology": topo, "n_runs": len(group),
               "n_records": group[0]["n_records"],
               "n_relevant": group[0]["n_relevant"]}
        agg.update(_flat(group))
        # topk_overlap dict 字段拍平
        for k in ("top10_overlap", "top50_overlap", "top10_jaccard",
                  "top50_jaccard"):
            vals = [g["topk_overlap"].get(k) for g in group
                    if g.get("topk_overlap")]
            if vals:
                agg[k] = round(sum(vals) / len(vals), 6)
        agg_rows.append(agg)

    fields = list(agg_rows[0].keys())
    csv_path = out_dir / "s2_pilot_summary.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(agg_rows)

    # 控制台汇总表(停止组为主读数)
    for stop in sorted({r["stop_streak"] for r in agg_rows}, key=lambda s: -(s or 10 ** 9)):
        print(f"\n=== stop_streak={stop} ===")
        hdr = f"{'dataset':<18}{'noise':<12}{'topology':<12}{'miss_rate':>10}" \
              f"{'depth':>8}{'cost':>9}{'arb%':>7}{'kappa':>7}"
        print(hdr)
        for r in agg_rows:
            if r["stop_streak"] != stop:
                continue
            print(f"{r['dataset']:<18}{r['noise']:<12}{r['topology']:<12}"
                  f"{r.get('miss_rate', float('nan')):>10.4f}"
                  f"{r.get('depth', float('nan')):>8.3f}"
                  f"{r.get('cost', float('nan')):>9.1f}"
                  f"{(r.get('arb_share') or 0) * 100:>7.2f}"
                  f"{(r.get('kappa_ab') if r.get('kappa_ab') is not None else float('nan')):>7.3f}")
    print(f"\n[summarize] {len(agg_rows)} groups → {csv_path}")

    budget = estimate_full_budget(agg_rows)
    (out_dir / "budget_estimate.json").write_text(
        json.dumps(budget, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[budget] 全量估计(见 {out_dir / 'budget_estimate.json'}):"
          f"总计 ≈ {budget['total_hours']:.1f} 小时(单线程)")
    return csv_path


def estimate_full_budget(agg_rows: list[dict]) -> dict:
    """按试点实测外推全量预算(26 集 × 25 种子 × 4 拓扑 × 2 噪声 × 2 停止)。

    假设(显式):耗时 ≈ c × 训练步数,训练步数 ≈ 已筛深度 × N;full 停止深度 = 1,
    streak 停止深度取试点该组均值;大集的每记录耗时不外推(保守按试点均值计,
    真实值随 N 增长 → 估计偏保守下界)。
    """
    summary_csv = _DATA_DIR / "summary.csv"
    sizes: dict[str, int] = {}
    with summary_csv.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sizes[row["dataset"]] = int(row["n_records"])
    # 每拓扑:full 运行 sec/record(对 N 过原点线性拟合;耗时数据取自 runs.jsonl)
    runs_path = _DEFAULT_OUT / "s2_pilot_runs.jsonl"
    if runs_path.exists():
        raw = [json.loads(line) for line in
               runs_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        raw = []
    per_topo: dict[str, dict] = {}
    for topo in PILOT_TOPOLOGIES:
        full_pts = [(r["n_records"], r["elapsed_s"]) for r in raw
                    if r["topology"] == topo and r["stop_streak"] is None]
        if full_pts:
            xs = np.array([p[0] for p in full_pts], dtype=np.float64)
            ys = np.array([p[1] for p in full_pts], dtype=np.float64)
            b = float(np.sum(xs * ys) / max(np.sum(xs * xs), 1.0))  # 过原点
            per_topo[topo] = {"sec_per_record_full": round(b, 6),
                              "n_points": len(full_pts)}
    streak_depth: dict[tuple, float] = {}
    for r in raw:
        if r["stop_streak"] is not None:
            key = (r["topology"], r["noise"])
            streak_depth.setdefault(key, []).append(r["depth"])
    mean_depth = {k: round(sum(v) / len(v), 4) for k, v in streak_depth.items()}
    plan = []
    total_s = 0.0
    for ds, n in sorted(sizes.items()):
        for topo, meta in per_topo.items():
            for noise in ("perfect", "noisy95_99"):
                for stop in ("full", "streak50"):
                    depth = 1.0 if stop == "full" else mean_depth.get(
                        (topo, noise), 0.5)
                    # 耗时与噪声配置无关(决策数相同)。
                    s = meta["sec_per_record_full"] * n * depth * 25
                    total_s += s
                    plan.append({"dataset": ds, "n_records": n, "topology": topo,
                                 "noise": noise, "stop": stop, "seeds": 25,
                                 "est_seconds": round(s, 1)})
    return {
        "assumptions": [
            "耗时 ≈ sec/record × N × 深度(过原点线性拟合试点 full 运行)",
            "sec/record 不随 N 外推(保守下界;真实大集更贵)",
            "26 集 × 25 种子 × {single,dual_shared,dual_indep,pool} × "
            "{perfect,noisy95_99} × {full,streak50}",
        ],
        "per_topology_sec_per_record_full": per_topo,
        "mean_streak_depth": {f"{k[0]}|{k[1]}": v for k, v in mean_depth.items()},
        "total_seconds": round(total_s, 1),
        "total_hours": round(total_s / 3600, 2),
        "n_runs_planned": len(plan),
        "plan": plan,
    }


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="S2 双盲双筛员 + 仲裁拓扑模拟")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("pilot", help="运行试点网格")
    sp.add_argument("--out", default=str(_DEFAULT_OUT))
    sp.add_argument("--quick", action="store_true", help="1 集 × 2 种子冒烟")
    ss = sub.add_parser("summarize", help="聚合 runs.jsonl → 汇总表 + 预算")
    ss.add_argument("--out", default=str(_DEFAULT_OUT))
    sr = sub.add_parser("run", help="运行单次模拟并打印汇总")
    sr.add_argument("--dataset", required=True)
    sr.add_argument("--topology", default="dual_shared")
    sr.add_argument("--noise", default="perfect", choices=sorted(NOISE_PRESETS))
    sr.add_argument("--seed", type=int, default=0)
    sr.add_argument("--stop-streak", type=int, default=None)
    sr.add_argument("--save-prefix", default=None)
    args = p.parse_args(argv)
    if args.cmd == "pilot":
        run_pilot(args.out, quick=args.quick)
    elif args.cmd == "summarize":
        summarize_pilot(args.out)
    else:
        records, gold = load_dataset_csv(_DATA_DIR / f"{args.dataset}.csv")
        cfg = DualConfig(topology=args.topology, seed=args.seed,
                         stop_streak=args.stop_streak,
                         **NOISE_PRESETS[args.noise])
        res = simulate_dual(records, gold, cfg)
        row = res.summary_row()
        row.pop("config", None)
        row.pop("topk_overlap", None)
        print(json.dumps(row, ensure_ascii=False, indent=2))
        if args.save_prefix:
            res.save_logs(args.save_prefix)


if __name__ == "__main__":
    main()
