"""S2 dual_oracle 测试:噪声模型恒等式 + 无噪声等效(两份相同轨迹)+ κ 对照 +
NB 分数对产品 ≤1e-12 + 成本公式直检 + 停止语义 + 解析安全界。

运行:``./.venv/bin/python -m pytest research/sim/test_dual_oracle.py -v``
(经 conftest.py,sim 目录与仓库根均在 sys.path;产品代码只读引用。)
"""

from __future__ import annotations

import json
import random

import numpy as np
import pytest

import dual_oracle as D
import replay as R
from coscreen.al.features import TfidfVectorizer
from coscreen.al.nb import MultinomialNB
from coscreen.stats.kappa import cohen_kappa

TOL = 1e-12


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _corpus(n=120, seed=3):
    return D.make_synthetic_corpus(n, seed=seed)


def _toy_corpus():
    """小型受控语料:include/exclude 词表分离,前 8 条含两类(供 NB 对照)。"""
    inc = [("vaccine immunization dose antibody response", "serum neutralization titer"),
           ("immunized boosters antibody vaccine", "dose response titer"),
           ("antibody serum vaccine response", "immunization dose"),
           ("vaccine titer immunized serum", "antibody neutralization response dose")]
    exc = [("rock geology mineral strata", "sedimentary basin fault"),
           ("marine sediment core", "ocean basin strata"),
           ("desert climate arid", "sand dune wind"),
           ("coin numismatic mint", "bronze currency strike"),
           ("fault basin mineral", "rock strata core"),
           ("ocean marine current", "sediment tide basin"),
           ("arid sand climate", "desert wind dune"),
           ("mint currency bronze", "coin strike numismatic")]
    records, gold = [], {}
    for i, (title, abstract) in enumerate(inc):
        records.append(R.FastRecord(zotero_key=f"TOYINC{i}", title=title,
                                    abstract=abstract, authors="A; B", journal="J"))
        gold[f"TOYINC{i}"] = "include"
    for i, (title, abstract) in enumerate(exc):
        records.append(R.FastRecord(zotero_key=f"TOYEXC{i}", title=title,
                                    abstract=abstract, authors="C; D", journal="K"))
        gold[f"TOYEXC{i}"] = "exclude"
    return records, gold


# ---------------------------------------------------------------------------
# 1. 筛员行为模型:极端参数 + 确定性
# ---------------------------------------------------------------------------

def test_screen_decision_extremes_and_determinism():
    rng = random.Random(0)
    # se=sp=1:永远跟黄金一致;完美筛员不消耗随机流(两次抽样同流状态不变)。
    assert D.screen_decision("include", D.PERFECT_SCREENER, rng) == "include"
    assert D.screen_decision("exclude", D.PERFECT_SCREENER, rng) == "exclude"
    # se=0/sp=0:永远反向。
    anti = D.ScreenerNoise(se=0.0, sp=0.0)
    assert D.screen_decision("include", anti, rng) == "exclude"
    assert D.screen_decision("exclude", anti, rng) == "include"
    # p_maybe=1:一律 maybe。
    maybe = D.ScreenerNoise(se=0.7, sp=0.8, p_maybe=1.0)
    assert all(D.screen_decision("include", maybe, rng) == "maybe" for _ in range(20))
    # 确定性:同种子同流 → 同决策序列;se=0.5 与解析频率一致(种子化)。
    rng7 = random.Random(7)
    r1 = [D.screen_decision("include", D.ScreenerNoise(0.5, 0.5), rng7)
          for _ in range(400)]
    rng7b = random.Random(7)
    r2 = [D.screen_decision("include", D.ScreenerNoise(0.5, 0.5), rng7b)
          for _ in range(400)]
    assert r1 == r2
    assert abs(r1.count("include") / 400 - 0.5) < 0.1
    # sp 通道:gold=exclude 时 P(exclude)=sp。
    rng3 = random.Random(11)
    seq = [D.screen_decision("exclude", D.ScreenerNoise(0.5, 0.5), rng3)
           for _ in range(400)]
    assert abs(seq.count("exclude") / 400 - 0.5) < 0.1
    with pytest.raises(ValueError):
        D.screen_decision("maybe", D.PERFECT_SCREENER, rng)  # 黄金标签无 maybe


def test_resolve_pair_identity():
    """仲裁恒等式(纯函数层):需仲裁 ⟺ 不一致或含 maybe;共识即终判。"""
    need, consensus = [], {}
    for da in D.SCREENER_DECISIONS:
        for db in D.SCREENER_DECISIONS:
            final, needs = D.resolve_pair(da, db)
            expect_needs = (da != db) or (da == "maybe")
            assert needs == expect_needs, (da, db)
            if needs:
                need.append((da, db))
                assert final is None
            else:
                assert final == da
                consensus[(da, db)] = final
    assert len(need) == 9 - 2  # 9 组合中仅 (include,include)/(exclude,exclude) 免仲裁
    assert consensus[("include", "include")] == "include"
    assert consensus[("exclude", "exclude")] == "exclude"
    # 仲裁方不允许 maybe。
    rng = random.Random(1)
    assert all(D.arbitrate("include", D.ScreenerNoise(1, 1, 1), rng) == "include"
               for _ in range(10))


# ---------------------------------------------------------------------------
# 2. 无噪声等效:single ≡ replay_fast;dual_shared/dual_indep = 两份相同轨迹
# ---------------------------------------------------------------------------

def test_noiseless_single_matches_replay_fast_max_and_mixed():
    records, gold = _corpus(120, seed=3)
    rr = R.replay_fast(records, gold, "max", 1)
    mine = D.simulate_dual(records, gold, D.DualConfig(
        topology="single", seed=1, stop_streak=None))
    k = len(rr.trajectory)  # replay 找齐全部相关后 oracle 提前终止;比较前缀
    assert k >= 1 and len(mine.trajectory) >= k
    assert [r["record_id"] for r in mine.trajectory[:k]] == \
           [r["record_id"] for r in rr.trajectory]
    assert [r["gold"] for r in mine.trajectory[:k]] == \
           [r["label"] for r in rr.trajectory]
    # mixed:随机流种子 = seed*4+1,与 replay_fast(seed*4+1) 对齐后逐位同序。
    rr_m = R.replay_fast(records, gold, "mixed", 5)
    mine_m = D.simulate_dual(records, gold, D.DualConfig(
        topology="single", seed=1, stop_streak=None, strategy="mixed"))
    km = len(rr_m.trajectory)
    assert [r["record_id"] for r in mine_m.trajectory[:km]] == \
           [r["record_id"] for r in rr_m.trajectory]


def test_noiseless_dual_shared_is_two_identical_copies_of_single():
    """无噪声双盲(共享排序)= 同一轨迹的两份拷贝:零仲裁、成本恰 ×2。"""
    records, gold = _corpus(120, seed=3)
    single = D.simulate_dual(records, gold, D.DualConfig(
        topology="single", seed=2, stop_streak=None))
    dual = D.simulate_dual(records, gold, D.DualConfig(
        topology="dual_shared", seed=2, stop_streak=None))
    assert [r["record_id"] for r in dual.trajectory] == \
           [r["record_id"] for r in single.trajectory]
    for row in dual.trajectory:
        assert row["dec_a"] == row["dec_b"] == row["gold"]  # 完美筛员:与黄金一致
        assert row["arbitrated"] is False
    assert dual.n_arbitrations == 0
    assert dual.n_decisions == 2 * single.n_decisions
    assert dual.cost == 2 * single.cost  # w=1:2N
    assert dual.miss_count == single.miss_count == 0  # 全程筛完,完美筛员零遗漏
    assert dual.kappa_ab == 1.0 and dual.kappa_n == dual.n_records
    assert dual.uncertainty_disagreement_r is None  # 无分歧 → 相关无定义


def test_noiseless_dual_indep_streams_equal_single_and_models_identical():
    records, gold = _corpus(120, seed=3)
    single = D.simulate_dual(records, gold, D.DualConfig(
        topology="single", seed=2, stop_streak=None))
    dual = D.simulate_dual(records, gold, D.DualConfig(
        topology="dual_indep", seed=2, stop_streak=None))
    a = [r["record_id"] for r in dual.trajectory if r["screener"] == "A"]
    b = [r["record_id"] for r in dual.trajectory if r["screener"] == "B"]
    assert a == b == [r["record_id"] for r in single.trajectory]
    assert all(r["partner_dec"] == r["dec"] == r["gold"]
               for r in dual.trajectory if r.get("partner_dec"))
    assert dual.n_arbitrations == 0 and dual.miss_count == 0
    assert dual.kappa_ab == 1.0
    # 两模型训练态逐位相同 → 终局全语料排序完全一致。
    assert dual.topk_overlap["top10_overlap"] == 1.0
    assert dual.topk_overlap["top50_jaccard"] == 1.0


# ---------------------------------------------------------------------------
# 3. 噪声下的恒等式:仲裁计数、成本公式、κ 对照
# ---------------------------------------------------------------------------

def test_noisy_dual_shared_arbitration_count_and_cost_identity():
    records, gold = _corpus(150, seed=4)
    cfg = D.DualConfig(topology="dual_shared", seed=5, stop_streak=None,
                       noise_a=D.NOISY_95_99, noise_b=D.NOISY_95_99,
                       w_decision=2.0, w_arbitration=3.0)
    res = D.simulate_dual(records, gold, cfg)
    rows = res.trajectory
    # 恒等式 1:仲裁数 = 不一致(含 maybe)记录数(逐行重算 resolve_pair)。
    needs = [D.resolve_pair(r["dec_a"], r["dec_b"])[1] for r in rows]
    assert sum(needs) == res.n_arbitrations
    assert all(r["arbitrated"] == n for r, n in zip(rows, needs))
    # 恒等式 2:成本公式直检(含非对称权重)。
    assert res.cost == pytest.approx(
        cfg.w_decision * res.n_decisions + cfg.w_arbitration * res.n_arbitrations)
    assert res.cost == rows[-1]["cost_so_far"]
    # 逐行成本单调递增且增量 = 2×w_dec + (w_arb 若该行仲裁)。
    prev = 0.0
    for r in rows:
        step = cfg.w_decision * 2 + (cfg.w_arbitration if r["arbitrated"] else 0.0)
        assert r["cost_so_far"] == pytest.approx(prev + step)
        prev = r["cost_so_far"]
    # 完美仲裁:所有仲裁记录的终判 = 黄金。
    assert all(r["final"] == r["gold"] for r in rows if r["arbitrated"])
    assert res.arb_precision == pytest.approx(
        sum(1 for r in rows if r["arbitrated"] and r["gold"] == "include")
        / res.n_arbitrations)


def test_kappa_matches_product_cohen_kappa():
    records, gold = _corpus(150, seed=4)
    res = D.simulate_dual(records, gold, D.DualConfig(
        topology="dual_shared", seed=5, stop_streak=None,
        noise_a=D.NOISY_95_99, noise_b=D.NOISY_95_99))
    pairs = [(r["dec_a"], r["dec_b"]) for r in res.trajectory]
    ref = cohen_kappa([a for a, _ in pairs], [b for _, b in pairs])
    assert res.kappa_ab == ref.kappa
    assert res.kappa_po == ref.po
    assert res.kappa_n == ref.n == len(pairs)
    assert 0.0 < res.kappa_ab < 1.0  # 噪声下高但非完全一致(se=0.95/sp=0.99)
    # 产品函数的手算对照:po=0.5,pe=0.5 → kappa=0。
    hand = cohen_kappa(["include", "exclude"], ["include", "include"])
    assert hand.po == 0.5 and hand.pe == 0.5 and hand.kappa == 0.0


def test_maybe_channel_all_records_arbitrated():
    """p_maybe=1:全部决策 maybe → 全部进仲裁;完美仲裁 → 终判=黄金、零遗漏。"""
    records, gold = _corpus(80, seed=6)
    for topo, dec_per_rec in (("single", 1), ("pool", 1), ("dual_shared", 2)):
        maybe = D.ScreenerNoise(se=0.9, sp=0.9, p_maybe=1.0)
        cfg = D.DualConfig(topology=topo, seed=7, stop_streak=None,
                           noise_a=maybe, noise_b=maybe)
        res = D.simulate_dual(records, gold, cfg)
        n = res.n_records
        assert res.n_maybe == dec_per_rec * n
        assert res.n_arbitrations == n  # 每个 maybe 都被仲裁
        assert res.miss_count == 0  # 完美仲裁员全部纠正
        assert all(r["final"] == r["gold"] for r in res.trajectory)
        assert res.cost == dec_per_rec * n + n


def test_pool_topology_structure():
    """共享池:每记录只筛一次,A/B 各约一半,无共筛 → κ 无定义。

    (make_synthetic_corpus 会在尾部追加 ≤5 条重复文档,故 n 取自结果。)
    """
    records, gold = _corpus(101, seed=8)  # 奇数基数:交替后 A ≥ B 且至多多 1
    res = D.simulate_dual(records, gold, D.DualConfig(
        topology="pool", seed=9, stop_streak=None))
    n = res.n_records
    assert res.n_decisions == n == res.n_screened_records
    assert res.n_decisions_a >= res.n_decisions_b
    assert res.n_decisions_a - res.n_decisions_b <= 1
    assert res.n_decisions_a + res.n_decisions_b == n
    assert res.n_co_screened == 0 and res.kappa_ab is None and res.kappa_n == 0
    assert res.topk_overlap == {}  # 池模式无第二模型,不出一致性@k


# ---------------------------------------------------------------------------
# 4. NB 打分 vs 产品(只读)≤1e-12;support 通道等价
# ---------------------------------------------------------------------------

def test_nb_fit_score_matches_product_nb_and_support_path():
    records, gold = _toy_corpus()
    labels = gold
    labeled = [r for r in records if r.zotero_key.startswith("TOYINC")] + \
              [r for r in records if r.zotero_key.startswith("TOYEXC")][:4]
    cand = [r for r in records if r not in labeled]
    y = [1 if labels[r.zotero_key] == "include" else 0 for r in labeled]

    # 产品侧(只读):产品 TfidfVectorizer + MultinomialNB。
    vec = TfidfVectorizer()
    rows_tr = vec.fit_transform([R._article_text(r) for r in labeled])
    rows_ca = vec.transform([R._article_text(r) for r in cand])
    nb = MultinomialNB(alpha=1.0)
    nb.fit(rows_tr, y)
    expected = np.asarray(nb.predict_scores(rows_ca))

    # 引擎侧:_CorpusIndex + 增量 df + _scale_normalize + _nb_fit_score。
    index = R._CorpusIndex(records)
    pos_of = {k: i for i, k in enumerate(index.keys)}
    lab_pos = [pos_of[r.zotero_key] for r in labeled]
    cand_pos = np.array([pos_of[r.zotero_key] for r in cand], dtype=np.int64)
    df = np.zeros(index.V, dtype=np.int64)
    for p in lab_pos:
        df[index.doc_terms[p]] += 1
    sup = np.flatnonzero(df)
    idf_full = np.zeros(index.V, dtype=np.float64)
    idf_full[sup] = np.log((1.0 + len(lab_pos)) / (1.0 + df[sup])) + 1.0
    Xt = R._scale_normalize(index.counts[lab_pos], idf_full)
    Xc = R._scale_normalize(index.counts[cand_pos], idf_full)
    mine = D._nb_fit_score(Xt, Xc, y, 1.0, sup)
    assert np.max(np.abs(mine - expected)) <= TOL
    # support=None 与 support=arange(V) 同义(全语料词表=训练词表),逐位相同;
    # support ⊊ V 时分母 alpha·Vt 不同,是另一个模型,不与产品对照。
    mine_none = D._nb_fit_score(Xt, Xc, y, 1.0, None)
    mine_aw = D._nb_fit_score(Xt, Xc, y, 1.0, np.arange(index.V))
    assert np.array_equal(mine_none, mine_aw)
    # 单类训练:缺席类先验 −inf → 得分恒另一类(产品 nb.py docstring 语义:
    # 只含 1 类恒 1.0;只含 0 类恒 0.0)。
    assert np.all(D._nb_fit_score(Xt, Xc, [1] * len(y), 1.0, sup) == 1.0)
    assert np.all(D._nb_fit_score(Xt, Xc, [0] * len(y), 1.0, sup) == 0.0)


# ---------------------------------------------------------------------------
# 5. 停止语义与 dual_indep 单决策策略
# ---------------------------------------------------------------------------

def test_stop_streak_semantics_single():
    records, gold = _corpus(120, seed=3)
    res = D.simulate_dual(records, gold, D.DualConfig(
        topology="single", seed=3, stop_streak=5))
    assert res.stopped is True
    rows = res.trajectory
    assert len(rows) == res.n_screened_records == 5
    assert all(r["final"] == "exclude" for r in rows[-5:])
    assert rows[-1]["streak_after"] == 5
    assert len({r["record_id"] for r in rows}) == 5
    # 全部遗漏:7 条相关从未被筛到(该语料相关不在前 5)。
    assert res.miss_count == res.n_relevant
    assert res.miss_tail_count == res.n_relevant


def test_dual_indep_single_decision_policy_and_coverage():
    records, gold = _corpus(150, seed=4)
    cfg = D.DualConfig(topology="dual_indep", seed=5, stop_streak=8,
                       noise_a=D.NOISY_95_99, noise_b=D.NOISY_95_99)
    res = D.simulate_dual(records, gold, cfg)
    rows = res.trajectory
    steps = [r for r in rows if "screener" in r]
    policies = [r for r in rows if r.get("event") == "single_decision_policy"]
    seen_a = {r["record_id"] for r in steps if r["screener"] == "A"}
    seen_b = {r["record_id"] for r in steps if r["screener"] == "B"}
    single_side = (seen_a ^ seen_b)
    both = seen_a & seen_b
    assert len(policies) == len(single_side)
    assert {p["record_id"] for p in policies} == single_side
    assert all((r["record_id"] in seen_a) != (r["record_id"] in seen_b)
               for r in policies)
    # 已结算记录 = 双方都筛过的记录;终判覆盖全部"至少被一人筛过"的记录。
    resolved = {r["record_id"] for r in steps if r.get("final") is not None}
    assert resolved == both
    # 成本恒等式含策略仲裁。
    assert res.cost == res.n_decisions + res.n_arbitrations
    assert res.n_arbitrations == len(policies) + \
        sum(1 for r in steps if r.get("arbitrated"))
    # 未筛记录无终判 → 相关者计尾部遗漏。
    unscreened = {r.zotero_key for r in records} - (seen_a | seen_b)
    tail_miss = sum(1 for k in unscreened if gold[k] == "include")
    assert res.miss_tail_count == tail_miss


def test_dual_indep_stop_with_unscreened_tail_regression():
    """回归:早期停止后存在"双方都未筛"的尾部——单决策策略循环不得把
    未筛记录误入仲裁(曾因推导式误用外层变量导致 KeyError)。"""
    records, gold = _corpus(120, seed=3)  # 该语料首 10 条按导入序全为 exclude
    res = D.simulate_dual(records, gold, D.DualConfig(
        topology="dual_indep", seed=3, stop_streak=3))
    steps = [r for r in res.trajectory if "screener" in r]
    seen = {r["record_id"] for r in steps}
    unscreened = {r.zotero_key for r in records} - seen
    assert len(unscreened) > 50  # 早期停止,尾部大量未筛
    assert not any(r.get("event") == "single_decision_policy"
                   and r["record_id"] in unscreened for r in res.trajectory)
    assert res.miss_tail_count == sum(1 for k in unscreened
                                      if gold[k] == "include")


def test_dual_indep_streak_joint_stop():
    """联合停止:双方 streak 同时 ≥ 阈值才停;一方先达标后停止筛。"""
    records, gold = _corpus(120, seed=3)
    res = D.simulate_dual(records, gold, D.DualConfig(
        topology="dual_indep", seed=3, stop_streak=5))
    steps = [r for r in res.trajectory if "screener" in r]
    streaks = {"A": [], "B": []}
    for r in steps:
        streaks[r["screener"]].append(r["streak_after"])
    assert res.stopped is True
    for side in ("A", "B"):
        assert streaks[side] and streaks[side][-1] >= 5
        assert all(s < 5 for s in streaks[side][:-1])


# ---------------------------------------------------------------------------
# 6. 解析安全界:双盲把 (1−se) 压到 (1−se)²(种子化,宽松断言)
# ---------------------------------------------------------------------------

def test_analytic_safety_bounds_se05():
    # 提高相关数以压低二项噪声:relevant_ratio=0.15 → ~75 条相关。
    records, gold = D.make_synthetic_corpus(500, seed=7, relevant_ratio=0.15)
    n_rel = sum(1 for v in gold.values() if v == "include")
    assert n_rel >= 60
    noisy = D.ScreenerNoise(se=0.5, sp=0.99)
    single = D.simulate_dual(records, gold, D.DualConfig(
        topology="single", seed=11, stop_streak=None,
        noise_a=noisy, noise_b=noisy))
    dual = D.simulate_dual(records, gold, D.DualConfig(
        topology="dual_shared", seed=11, stop_streak=None,
        noise_a=noisy, noise_b=noisy))
    # 完美仲裁下:single 终判漏检 ≈ (1−se)=0.5;dual 共识漏检 ≈ (1−se)²=0.25。
    assert 0.35 < single.miss_rate < 0.65
    assert 0.10 < dual.miss_rate < 0.40
    assert dual.miss_count < single.miss_count
    assert dual.n_arbitrations > 0


def test_determinism_same_config_identical_runs():
    records, gold = _corpus(100, seed=9)
    cfg = D.DualConfig(topology="dual_indep", seed=10, stop_streak=20,
                       noise_a=D.NOISY_95_99, noise_b=D.NOISY_95_99)
    r1 = D.simulate_dual(records, gold, cfg)
    r2 = D.simulate_dual(records, gold, cfg)
    assert r1.trajectory == r2.trajectory
    s1, s2 = r1.summary_row(), r2.summary_row()
    s1.pop("elapsed_s"), s2.pop("elapsed_s")  # 计时非确定性字段除外
    assert s1 == s2


def test_config_validation():
    records, gold = _corpus(60, seed=2)
    with pytest.raises(ValueError):
        D.simulate_dual(records, gold, D.DualConfig(topology="bogus"))
    with pytest.raises(ValueError):
        D.DualConfig(w_arbitration=-1.0).validated()
    with pytest.raises(ValueError):
        D.DualConfig(noise_a=D.ScreenerNoise(1.5, 1.0)).validated()
    with pytest.raises(ValueError):
        D.DualConfig(single_exclude_policy="bogus").validated()
    with pytest.raises(ValueError):
        D.DualConfig(stop_streak=0).validated()


def test_save_logs_roundtrip(tmp_path):
    records, gold = _corpus(90, seed=12)
    res = D.simulate_dual(records, gold, D.DualConfig(
        topology="dual_shared", seed=13, stop_streak=None,
        noise_a=D.NOISY_95_99, noise_b=D.NOISY_95_99))
    jsonl, js = res.save_logs(tmp_path / "x" / "run")
    assert jsonl.exists() and js.exists()
    rows = [json.loads(line) for line in
            jsonl.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == len(res.trajectory)
    summary = json.loads(js.read_text(encoding="utf-8"))
    assert summary["n_arbitrations"] == res.n_arbitrations
    assert summary["n_trajectory_rows"] == len(rows)
