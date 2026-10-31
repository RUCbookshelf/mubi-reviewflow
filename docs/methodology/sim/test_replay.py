"""S0 快速回放引擎测试:一致性断言 + 指标手算例 + 平台自检 + 性能冒烟。

运行:``./.venv/bin/python -m pytest research/sim/ -v``

核心断言:``research/sim/replay.py`` 的回放轨迹与产品
``coscreen.al.simulation`` **逐位一致**——逐步队首序列、WSS@95、n_to_95、
r95、ttd_mean 完全相等(不是近似)。产品对照通过产品自身函数插桩
(``_validated_gold``/``_article_text``/``_score_remaining``/``query_order``)
实现,≤2000 条(产品 MAX_RECORDS 上限内)。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
import scipy.sparse as sp

import replay as R
from coscreen.al import simulation as psim
from coscreen.al.strategies import query_order
from coscreen.models import Article

TOL = 1e-12

# 试点产物(只读回归基准:增强前引擎在真实数据上的轨迹)
_RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"
_PILOT_DIR = _RESULTS_DIR / "s1_pilot"
_DATA_DIR = _RESULTS_DIR.parent / "data" / "synergy"


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def to_articles(records):
    return [Article(zotero_key=r.zotero_key, title=r.title, abstract=r.abstract,
                    authors=r.authors, journal=r.journal) for r in records]


def product_trace(articles, gold, strategy, seed, learn=True):
    """用产品自身函数插桩回放循环:返回 (逐步 (位次, 队首), 每次 refit 的分数 dict)。"""
    labels = psim._validated_gold(articles, gold)
    keys = [a.zotero_key for a in articles]
    texts = {a.zotero_key: psim._article_text(a) for a in articles}
    relevant = {k for k in keys if labels[k] == "include"}
    n_relevant = len(relevant)
    order = query_order(keys, {}, strategy, seed)
    labeled, heads, scores_log = [], [], []
    position, ttd = 0, []
    while order:
        key = order.pop(0)
        position += 1
        labeled.append(key)
        heads.append((position, key))
        if key in relevant:
            ttd.append(position)
            if len(ttd) == n_relevant:
                break
        if learn and len(labeled) >= psim.WARMUP and order:
            scores = psim._score_remaining(labeled, labels, texts, order)
            scores_log.append(dict(scores))
            order = query_order(order, scores, strategy, seed)
    return heads, scores_log


def assert_replay_equal(records, gold, strategy, seed, learn=True):
    """产品 vs 快速引擎:轨迹队首逐位一致 + 指标完全相等 + 分数残差 ≤1e-12。"""
    arts = to_articles(records)
    prod = (psim.replay(arts, gold, strategy, seed) if learn
            else psim.simulate_random_baseline(arts, gold, seed))
    fast = R.replay_fast(records, gold, strategy, seed, learn=learn)
    heads, pscores = product_trace(arts, gold, strategy, seed, learn=learn)
    fheads = [(row["i"], row["record_id"]) for row in fast.trajectory]
    assert fheads == heads, "逐步队首序列与产品不一致"
    assert prod.n_to_95 == fast.n_to_95
    assert prod.wss95 == fast.wss95  # 逐位相等(==,非近似)
    assert prod.r95 == fast.r95
    assert prod.ttd_mean == fast.ttd_mean
    assert prod.n_records == fast.n_records
    assert prod.n_relevant == fast.n_relevant
    # 分数残差:产品每次 refit 的分数 vs 快速引擎同步的 top-k 摘要
    ftrained = [r for r in fast.trajectory if r["trained_this_step"]]
    assert len(ftrained) == len(pscores)
    max_res = 0.0
    for row, psc in zip(ftrained, pscores):
        for k, s in (row.get("model_top_remaining") or []):
            if k in psc:
                max_res = max(max_res, abs(s - psc[k]))
    assert max_res <= TOL, f"分数残差 {max_res:.2e} 超过 {TOL:.0e}"
    # 剩余池分数摘要(remaining_score_sum / remaining_score_quantiles)的
    # **独立核算**:以产品插桩分数为独立源,math.fsum(精确舍入)与纯 Python
    # 线性插值分位对照引擎的 np.sum(pairwise)/np.quantile(introselect)。
    rq = R.REMAINING_SCORE_QUANTILES
    for row, psc in zip(ftrained, pscores):
        vals = sorted(psc.values())
        n_pool = len(vals)
        assert n_pool == row["n_remaining"], "摘要池大小与 n_remaining 不一致"
        ref_sum = math.fsum(vals)
        assert abs(row["remaining_score_sum"] - ref_sum) <= 1e-8 * max(1.0, abs(ref_sum)), \
            f"i={row['i']} 分数和独立核算偏差超容差"
        qs_logged = row["remaining_score_quantiles"]
        assert len(qs_logged) == len(rq)
        assert all(a <= b for a, b in zip(qs_logged, qs_logged[1:])), "分位非单调"
        assert 0.0 <= qs_logged[0] and qs_logged[-1] <= 1.0
        for q, logged in zip(rq, qs_logged):
            h = (n_pool - 1) * q
            lo = int(math.floor(h))
            hi = min(lo + 1, n_pool - 1)
            ref = vals[lo] + (h - lo) * (vals[hi] - vals[lo])
            assert abs(logged - ref) <= 1e-8, f"i={row['i']} 分位 q={q} 独立核算偏差超容差"
    return prod, fast


# ---------------------------------------------------------------------------
# 平台自检(逐位等效的数值前提)
# ---------------------------------------------------------------------------

def test_platform_bitwise_assumptions():
    """np.log/np.exp == math 逐位;csr.sum(axis=0) == 行序顺序累加逐位。

    平台相关(x86-64 Linux / numpy 2.x 已验证);若未来平台不满足,
    引擎的"逐位一致"需要重新评估(README 有说明)。
    """
    rng = np.random.default_rng(20260925)
    x = rng.uniform(1e-10, 1e6, 300_000)
    mlog = np.fromiter((math.log(t) for t in x), np.float64, x.size)
    assert np.array_equal(np.log(x), mlog), "np.log 与 math.log 不逐位相同"
    y = rng.uniform(-700, 30, 300_000)
    mexp = np.fromiter((math.exp(t) for t in y), np.float64, y.size)
    assert np.array_equal(np.exp(y), mexp), "np.exp 与 math.exp 不逐位相同"
    for trial in range(15):
        m = sp.random(40, 25, density=0.4, format="csr", dtype=np.float64,
                      random_state=np.random.default_rng(trial),
                      data_rvs=lambda k: rng.uniform(0, 1, k))
        colsum = np.asarray(m.sum(axis=0)).ravel()
        seq = np.zeros(m.shape[1])
        for i in range(m.shape[0]):
            for j in range(m.indptr[i], m.indptr[i + 1]):
                seq[m.indices[j]] += m.data[j]
        assert np.array_equal(colsum, seq), "csr.sum(axis=0) 非行序顺序累加"


# ---------------------------------------------------------------------------
# 查询策略:快速实现 vs 产品 query_order 逐位同序
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("trial", range(120))
def test_strategy_rankers_match_product_query_order(trial):
    rng = random_mod().Random(1000 + trial)
    n = rng.randint(1, 60)
    keys = [f"K{rng.randint(0, 10**9)}_{i}" for i in range(n)]
    if len(set(keys)) != n:
        return
    mode = trial % 4
    if mode == 0:
        scores = {}  # 缺失分数:全部按 0.0
    elif mode == 1:
        scores = {k: 0.5 for k in keys}  # 全同分
    else:
        raw = [rng.choice([0.0, 0.25, 0.5, 0.75, 1.0, rng.random()]) for _ in keys]
        scores = dict(zip(keys, raw))
    arr = np.array([scores.get(k, 0.0) for k in keys])
    for strategy in ("max", "uncertainty", "mixed"):
        expected = query_order(keys, scores, strategy, 1000 + trial)
        got = R._rank_queue(keys, arr, strategy, 1000 + trial,
                            score_by_key=scores or None)
        assert got == expected, f"trial={trial} strategy={strategy}"


def random_mod():
    import random
    return random


# ---------------------------------------------------------------------------
# 指标手算例
# ---------------------------------------------------------------------------

def _tiny_corpus():
    """6 篇、仅 1 篇相关(输入序位 4)。warmup 之前即发现 ⇒ 纯输入序。"""
    records, gold = [], {}
    texts = [
        "alpha beta gamma", "delta epsilon zeta", "eta theta iota",
        "kappa lambda mu", "nu xi omicron", "pi rho sigma",
    ]
    for i, t in enumerate(texts):
        k = f"T{i}"
        records.append(R.FastRecord(zotero_key=k, title=t, abstract=t))
        gold[k] = "include" if i == 3 else "exclude"
    return records, gold


def test_metrics_hand_computed_single_relevant():
    records, gold = _tiny_corpus()
    fast = R.replay_fast(records, gold, "max", 0)
    # 手算:n=6, n_relevant=1;发现位次 = 4;
    # n_to_95 = 4(1/1 ≥ 0.95);wss95 = (6-4)/6 - 0.05 = 0.28333...;
    # r95 = 1.0;ttd_mean = 4.0。
    assert fast.n_to_95 == 4
    # 与产品/引擎相同的运算顺序:(6-4)/6 - (1.0-0.95)(注意 1.0-0.95 ≠ 0.05)
    assert fast.wss95 == (6 - 4) / 6 - (1.0 - 0.95)
    assert fast.wss95 == pytest.approx(0.2833333333333333)
    assert fast.r95 == 1.0
    assert fast.ttd_mean == 4.0
    prod = psim.replay(to_articles(records), gold, "max", 0)
    assert (prod.n_to_95, prod.wss95, prod.r95, prod.ttd_mean) == (
        fast.n_to_95, fast.wss95, fast.r95, fast.ttd_mean)


def test_metrics_hand_computed_all_relevant():
    """12 篇全相关、max 输入序:ttd = 1..12;n_to_95 = 12;wss95 = -0.05;
    ttd_mean = 6.5;发现第 12 条后提前终止(第 5 条起共 7 次 refit)。"""
    records, gold = [], {}
    for i in range(12):
        k = f"A{i}"
        records.append(R.FastRecord(zotero_key=k, title=f"topic {i}", abstract=f"body {i}"))
        gold[k] = "include"
    fast = R.replay_fast(records, gold, "max", 0)
    assert fast.n_relevant == 12
    assert fast.n_to_95 == 12
    assert fast.wss95 == -(1.0 - 0.95)  # (12-12)/12 - (1.0-0.95)
    assert fast.r95 == 1.0
    assert fast.ttd_mean == 6.5
    assert [row["i"] for row in fast.trajectory] == list(range(1, 13))
    assert fast.n_trained_steps == 7  # 第 5..11 条筛完后各 refit 一次(第 12 条 done 即停)
    prod = psim.replay(to_articles(records), gold, "max", 0)
    assert (prod.n_to_95, prod.wss95, prod.r95, prod.ttd_mean) == (
        fast.n_to_95, fast.wss95, fast.r95, fast.ttd_mean)


def test_metrics_zero_relevant():
    records, gold = _tiny_corpus()
    gold = {k: "exclude" for k in gold}
    fast = R.replay_fast(records, gold, "max", 0)
    assert (fast.n_to_95, fast.wss95, fast.r95, fast.ttd_mean) == (0, 0.0, 0.0, 0.0)
    assert fast.trajectory == []  # 产品在 warm-start 前即返回,无轨迹


# ---------------------------------------------------------------------------
# 轨迹逐位一致性(与产品 simulation.py)
# ---------------------------------------------------------------------------

def _edge_corpora():
    """3 个边缘小语料:单相关+空/停用词文档、全相关、首 5 全排除。"""
    corpora = []
    # C1:30 篇 1 相关(输入序位 12),含空文档/纯停用词/重复对/中英混合
    records, gold = [], {}
    for i in range(30):
        k = f"E1_{i:02d}"
        if i == 4:
            rec = R.FastRecord(zotero_key=k)  # 空文本
        elif i == 9:
            rec = R.FastRecord(zotero_key=k, title="the of and to a in")
        elif i % 3 == 0:
            rec = R.FastRecord(zotero_key=k, title=f"协同教学 研究 {i}",
                               abstract=f"co-teaching students' outcomes 第{i}组")
        else:
            rec = R.FastRecord(zotero_key=k, title=f"topic {i} learning",
                               abstract=f"study of classroom practice {i}")
        records.append(rec)
        gold[k] = "include" if i == 11 else "exclude"
    records.append(R.FastRecord(zotero_key="E1_DUP", title="topic 5 learning",
                                abstract="study of classroom practice 5"))
    gold["E1_DUP"] = "exclude"  # 与 E1_05 文本相同 ⇒ 同分行
    corpora.append((records, gold))
    # C2:12 篇全相关(单类训练 + 提前终止)
    records, gold = [], {}
    for i in range(12):
        k = f"E2_{i:02d}"
        records.append(R.FastRecord(zotero_key=k, title=f"relevant {i}",
                                    abstract=f"target phenomenon {i} 教育"))
        gold[k] = "include"
    corpora.append((records, gold))
    # C3:25 篇,前 5 输入序全排除 ⇒ 首次 refit 单类(全排),得分恒 0,
    # 队列保持输入序直至发现第 8 位的相关文献
    records, gold = [], {}
    for i in range(25):
        k = f"E3_{i:02d}"
        records.append(R.FastRecord(zotero_key=k, title=f"case {i}",
                                    abstract=f"screening corpus item {i} 数据"))
        gold[k] = "include" if i in (7, 13, 19) else "exclude"
    corpora.append((records, gold))
    return corpora


@pytest.mark.parametrize("case", range(9))
def test_trajectory_bitwise_equivalence(case):
    corpora = [
        ("synthetic100", R.make_synthetic_corpus(100, seed=0)),
        ("synthetic160", R.make_synthetic_corpus(160, seed=100)),
        *_edge_corpora_named(),
    ]
    plans = [
        ("synthetic100", "max", 42), ("synthetic100", "mixed", 1),
        ("synthetic100", "uncertainty", 7), ("synthetic160", "max", 0),
        ("synthetic160", "mixed", 123), ("edge1", "max", 42),
        ("edge1", "mixed", 1), ("edge2", "max", 42), ("edge3", "max", 42),
    ]
    name, strategy, seed = plans[case]
    records, gold = dict(corpora)[name]
    assert_replay_equal(records, gold, strategy, seed)


def _edge_corpora_named():
    corpora = _edge_corpora()
    return [("edge1", corpora[0]), ("edge2", corpora[1]), ("edge3", corpora[2])]


def test_random_baseline_parity():
    records, gold = R.make_synthetic_corpus(100, seed=0)
    for seed in (0, 42):
        arts = to_articles(records)
        pb = psim.simulate_random_baseline(arts, gold, seed)
        fb = R.simulate_random_baseline_fast(records, gold, seed)
        assert (pb.n_to_95, pb.wss95, pb.r95, pb.ttd_mean) == (
            fb.n_to_95, fb.wss95, fb.r95, fb.ttd_mean)
        heads, _ = product_trace(arts, gold, "mixed", seed, learn=False)
        assert [(r["i"], r["record_id"]) for r in fb.trajectory] == heads


def test_larger_corpus_metrics_parity_500():
    """500 条(产品上限内)指标逐位一致(轨迹对拍在小语料已覆盖)。"""
    records, gold = R.make_synthetic_corpus(500, seed=300)
    prod = psim.replay(to_articles(records), gold, "max", 42)
    fast = R.replay_fast(records, gold, "max", 42)
    assert (prod.n_to_95, prod.wss95, prod.r95, prod.ttd_mean) == (
        fast.n_to_95, fast.wss95, fast.r95, fast.ttd_mean)


# ---------------------------------------------------------------------------
# 输入校验(与产品同语义)
# ---------------------------------------------------------------------------

def test_gold_validation_parities():
    records, gold = _tiny_corpus()
    fast_bad = pytest.raises(ValueError, match="include/exclude")
    with fast_bad:
        R.replay_fast(records, {**gold, "T0": "maybe"}, "max", 0)
    with pytest.raises(ValueError, match="include/exclude"):
        psim.replay(to_articles(records), {**gold, "T0": "maybe"}, "max", 0)
    with pytest.raises(KeyError):
        R.replay_fast(records, {k: v for k, v in gold.items() if k != "T2"},
                      "max", 0)
    with pytest.raises(KeyError):
        psim.replay(to_articles(records),
                    {k: v for k, v in gold.items() if k != "T2"}, "max", 0)
    # 多余键忽略;大小写/空白规范化
    extra = {**gold, "NOT_IN_CORPUS": "include"}
    f = R.replay_fast(records, {k: v.upper() for k, v in extra.items()},
                      "max", 0)
    p = psim.replay(to_articles(records),
                    {k: f" {v.upper()} " for k, v in extra.items()}, "max", 0)
    assert (f.n_to_95, f.wss95) == (p.n_to_95, p.wss95) == (4, (6 - 4) / 6 - (1.0 - 0.95))
    with pytest.raises(ValueError, match="唯一"):
        R.replay_fast(records + records[:1], gold, "max", 0)


# ---------------------------------------------------------------------------
# 轨迹日志格式与持久化
# ---------------------------------------------------------------------------

def test_trajectory_fields_and_persistence(tmp_path):
    records, gold = R.make_synthetic_corpus(80, seed=5)
    fast = R.replay_fast(records, gold, "mixed", 42)
    assert fast.trajectory, "轨迹为空"
    required = {"i", "record_id", "label", "phase", "score_when_head",
                "n_labeled", "n_relevant_found", "n_relevant_total",
                "recall_so_far", "n_remaining", "exclude_streak",
                "trained_this_step", "model_top_remaining",
                "remaining_score_sum", "remaining_score_quantiles",
                "step_elapsed_s"}
    for row in fast.trajectory:
        assert required <= set(row)
    # 位次连续、阶段标记正确(warmup 行无模型分)
    assert [r["i"] for r in fast.trajectory] == list(range(1, len(fast.trajectory) + 1))
    for row in fast.trajectory[:4]:
        assert row["trained_this_step"] is False
        assert row["score_when_head"] is None
    assert fast.trajectory[4]["trained_this_step"] is True  # 第 5 条筛完后首训
    # 队首分数 = 上次模型对该记录的打分布景:warmup 后应非 None
    trained_rows = [r for r in fast.trajectory if r["score_when_head"] is not None]
    assert trained_rows
    # 剩余池分数摘要:每训练步非空(monotone 分位、值域、覆盖 top-5),
    # 未训练步为 null —— schema 与既有字段同样齐整。
    for row in fast.trajectory:
        if row["trained_this_step"]:
            assert row["remaining_score_sum"] is not None
            q = row["remaining_score_quantiles"]
            assert len(q) == len(R.REMAINING_SCORE_QUANTILES)
            assert all(a <= b for a, b in zip(q, q[1:])), "分位非单调"
            assert 0.0 <= q[0] and q[-1] <= 1.0
            top = row["model_top_remaining"] or []
            assert row["remaining_score_sum"] >= sum(s for _, s in top) - 1e-12
            assert q[-1] >= max((s for _, s in top), default=0.0) - 1e-12
        else:
            assert row["remaining_score_sum"] is None
            assert row["remaining_score_quantiles"] is None
    # 回归断言(增强不改回放):轨迹顺序与 WSS/n_to_95/r95/ttd_mean
    # 与产品逐位一致 —— 新字段为纯日志,不影响任何既有数值与顺序。
    arts = to_articles(records)
    heads, _ = product_trace(arts, gold, "mixed", 42)
    assert [(r["i"], r["record_id"]) for r in fast.trajectory] == heads
    prod = psim.replay(arts, gold, "mixed", 42)
    assert (prod.n_to_95, prod.wss95, prod.r95, prod.ttd_mean) == (
        fast.n_to_95, fast.wss95, fast.r95, fast.ttd_mean)
    # 持久化:JSONL 每行一个 JSON + 摘要 JSON
    jsonl, js = fast.save_logs(tmp_path / "replay-mixed-s42")
    rows = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == len(fast.trajectory)
    summary = json.loads(js.read_text(encoding="utf-8"))
    assert summary["n_to_95"] == fast.n_to_95
    assert summary["n_trajectory_rows"] == len(rows)
    assert "trajectory" not in summary
    # 新字段经 JSON 往返无损:训练步非空、warmup 行为 null
    persisted_trained = [r for r in rows if r["trained_this_step"]]
    assert persisted_trained
    assert all(r["remaining_score_sum"] is not None for r in persisted_trained)
    assert rows[0]["remaining_score_sum"] is None
    assert rows[0]["remaining_score_quantiles"] is None


# ---------------------------------------------------------------------------
# 研究扩展:priors / balance / fixed 特征 / run_config
# ---------------------------------------------------------------------------

def test_priors_asreview_semantics():
    records, gold = R.make_synthetic_corpus(120, seed=3)
    f1 = R.replay_fast(records, gold, "max", 7, priors="asreview", warmup=2)
    # 前两条 = 1 随机纳入 + 1 随机排除,位次 1/2
    first2 = f1.trajectory[:2]
    assert [r["i"] for r in first2] == [1, 2]
    assert sorted(r["label"] for r in first2) == ["exclude", "include"]
    assert all(r["phase"] == "prior" for r in first2)
    # 先验计入发现位次:含一条 relevant ⇒ n_relevant_found 首行 = 1
    assert first2[0]["n_relevant_found"] + first2[1]["n_relevant_found"] >= 1
    # 种子化:同种子完全一致,不同种子(几乎必然)先验不同
    f2 = R.replay_fast(records, gold, "max", 7, priors="asreview", warmup=2)
    assert [r["record_id"] for r in f1.trajectory] == [r["record_id"] for r in f2.trajectory]
    f3 = R.replay_fast(records, gold, "max", 8, priors="asreview", warmup=2)
    assert [r["record_id"] for r in f1.trajectory[:2]] != [r["record_id"] for r in f3.trajectory[:2]]
    # 与 ASReview 的 sample_prior_knowledge 同语义:RandomState(seed).choice
    rs = np.random.RandomState(7)
    keys = [r.zotero_key for r in records]
    inc = np.array([i for i, k in enumerate(keys) if gold[k] == "include"])
    exc = np.array([i for i, k in enumerate(keys) if gold[k] == "exclude"])
    expect = [keys[i] for i in list(rs.choice(inc, 1, replace=False))
              + list(rs.choice(exc, 1, replace=False))]
    assert [r["record_id"] for r in first2] == expect


def test_balance_and_fixed_mode_deterministic():
    records, gold = R.make_synthetic_corpus(120, seed=11)
    kw = dict(priors="asreview", balance=True, alpha=3.822, warmup=2,
              feature_refit="fixed")
    f1 = R.replay_fast(records, gold, "max", 9, **kw)
    f2 = R.replay_fast(records, gold, "max", 9, **kw)
    assert f1.metrics_dict() == f2.metrics_dict()
    assert [r["record_id"] for r in f1.trajectory] == [r["record_id"] for r in f2.trajectory]
    # 平衡开关在"标签与文本近乎无关"的语料上改变轨迹(强信号语料下排序被
    # 主导词支配,平衡与否队首可能一致——这正是要区分的情形)。
    import random as _random
    rng = _random.Random(5)
    keys = [r.zotero_key for r in records]
    rel = set(rng.sample(keys, 30))
    gold_rand = {k: ("include" if k in rel else "exclude") for k in keys}
    fb_on = R.replay_fast(records, gold_rand, "max", 9, priors="asreview",
                          warmup=2, alpha=3.822, feature_refit="fixed",
                          balance=True)
    fb_off = R.replay_fast(records, gold_rand, "max", 9, priors="asreview",
                           warmup=2, alpha=3.822, feature_refit="fixed",
                           balance=False)
    assert [r["record_id"] for r in fb_on.trajectory] != [r["record_id"] for r in fb_off.trajectory]
    # 单类训练(首 5 全排除)时 balance 走 fallback 不崩溃
    records3, gold3 = _edge_corpora()[2]
    f3 = R.replay_fast(records3, gold3, "max", 0, balance=True, priors=(0, 2))
    assert f3.n_records == 25


def test_run_config_presets():
    records, gold = R.make_synthetic_corpus(100, seed=0)
    # product preset == 产品 replay(逐位)
    r = R.run_config(records=records, gold=gold, preset="product", seed=42)
    p = psim.replay(to_articles(records), gold, "max", 42)
    assert (r.n_to_95, r.wss95, r.r95, r.ttd_mean) == (p.n_to_95, p.wss95, p.r95, p.ttd_mean)
    assert r.config["preset"] == "product"
    # asreview preset:先验+平衡+alpha=3.822+fixed 特征
    r2 = R.run_config(records=records, gold=gold, preset="asreview", seed=42)
    assert r2.config["priors"] == [1, 1]
    assert r2.config["balance"] is True
    assert r2.config["alpha"] == 3.822
    assert r2.config["feature_refit"] == "fixed"
    assert r2.trajectory[0]["phase"] == "prior"
    # 日志落盘
    r3 = R.run_config(records=records, gold=gold, preset="product", seed=1,
                      log_dir=tmp_dir(), log_prefix="demo")
    assert any("demo" in str(p) for p in r3.config["log_files"])

    # 回归断言(真实数据,增强不改回放):preset=asreview 重跑试点集
    # Nelson_2002(s0),除 wall-clock 与新摘要字段外与 s1_pilot 既有轨迹
    # **逐位一致**;WSS/n_to_95 与试点摘要完全相等 —— 证明分数摘要增强
    # 未改变轨迹顺序与任何指标。
    old_rows = [json.loads(line) for line in
                (_PILOT_DIR / "Nelson_2002-max-s0.trajectory.jsonl")
                .read_text(encoding="utf-8").splitlines()]
    old_summary = json.loads(
        (_PILOT_DIR / "Nelson_2002-max-s0.summary.json").read_text(encoding="utf-8"))
    rr = R.run_config(dataset_csv=str(_DATA_DIR / "Nelson_2002.csv"),
                      preset="asreview", strategy="max", seed=0)
    ignore = {"step_elapsed_s", "remaining_score_sum", "remaining_score_quantiles"}

    def _strip(row):
        return {k: v for k, v in row.items() if k not in ignore}

    assert len(rr.trajectory) == len(old_rows) == old_summary["n_trajectory_rows"]
    assert [_strip(r) for r in rr.trajectory] == [_strip(r) for r in old_rows]
    assert (rr.n_to_95, rr.wss95, rr.r95, rr.ttd_mean) == (
        old_summary["n_to_95"], old_summary["wss95"], old_summary["r95"],
        old_summary["ttd_mean"])
    # 新字段在真实轨迹上:训练步非空、未训练步 null;max 策略下最大分位
    # = 池内最大分 ≥ 新队首(top-1)分数。
    n_with_summary = 0
    for row in rr.trajectory:
        if row["trained_this_step"]:
            q = row["remaining_score_quantiles"]
            assert row["remaining_score_sum"] is not None
            assert len(q) == len(R.REMAINING_SCORE_QUANTILES)
            assert all(a <= b for a, b in zip(q, q[1:]))
            assert q[-1] >= row["model_top_remaining"][0][1]
            n_with_summary += 1
        else:
            assert row["remaining_score_sum"] is None
            assert row["remaining_score_quantiles"] is None
    assert n_with_summary == rr.n_trained_steps >= 1


def tmp_dir():
    import tempfile
    return tempfile.mkdtemp(prefix="simlogs_")


# ---------------------------------------------------------------------------
# CSV 加载
# ---------------------------------------------------------------------------

def test_load_dataset_csv(tmp_path):
    csv_path = tmp_path / "dataset.csv"
    csv_path.write_text(
        "\ufeffrecord_id,title,abstract,authors,journal,keywords,label_included\n"
        'R1,Title A,Abstract A,Auth A,J A,kw1,1\n'
        'R2,Title B,Abstract B,Auth B,J B,kw2,0\n'
        'R3,Title C,Abstract C,Auth C,J C,kw3,true\n'
        'R4,Title D,Abstract D,Auth D,J D,kw4,0\n',
        encoding="utf-8")
    records, gold = R.load_dataset_csv(csv_path)
    assert [r.zotero_key for r in records] == ["R1", "R2", "R3", "R4"]
    assert gold == {"R1": "include", "R2": "exclude", "R3": "include",
                    "R4": "exclude"}
    assert records[0].title == "Title A"
    # keywords 列不参与文本(与产品四字段拼接一致)
    assert "kw1" not in (records[0].title + records[0].abstract)
    # 显式列覆盖
    records2, gold2 = R.load_dataset_csv(csv_path, id_col="record_id",
                                         label_col="label_included")
    assert gold2 == gold
    # fast 引擎可直接吃 CSV 数据
    r = R.replay_fast(records, gold, "max", 0)
    assert r.n_records == 4


# ---------------------------------------------------------------------------
# 性能冒烟(基准表见 README;此处只防退化)
# ---------------------------------------------------------------------------

def test_performance_smoke_1500():
    records, gold = R.make_synthetic_corpus(1500, seed=77)
    fast = R.replay_fast(records, gold, "max", 42, collect_trajectory=True)
    assert fast.n_records == len(records)  # 含追加的重复文档
    assert fast.n_to_95 > 0
    # 冒烟阈值:宽裕(本机 8k 全量 < 60s;1500 条应在数秒级)
    assert fast.elapsed_s < 30.0, f"1500 条耗时 {fast.elapsed_s:.1f}s,疑似退化"
