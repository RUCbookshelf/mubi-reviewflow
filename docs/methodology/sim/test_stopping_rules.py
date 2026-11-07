"""S1 停止规则后评估的测试(解析断言 + 规则机制 + 端到端集成)。

解析断言使用 research/notes/stopping_rules.md 的任务 1 闭式:
  - streak 规则遗漏率 vs 无信息随机序闭式 P_miss = 1 - (1/C(N,K)) Σ (-1)^j C(K,j) C(N-jn,K);
  - 超几何证书覆盖 / 序贯 spending 虚证率上界;
  - 安全-工作量前沿式 vs 精确解;
  - Kempny 准则 1 的可靠率闭式 P(T >= K) vs 蒙特卡洛。

运行:./.venv/bin/python -m pytest research/sim/test_stopping_rules.py -q
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.stats import hypergeom

import stopping_rules as sr

# ---------------------------------------------------------------------------
# 工具:构造轨迹
# ---------------------------------------------------------------------------


def build_rows(labels_in_order: list[str]) -> tuple[list[dict], dict[str, str]]:
    """按筛序标签序列构造最小轨迹行 + gold(键 T0001.. 按筛序编号)。"""
    rows, gold = [], {}
    found, streak = 0, 0
    K = sum(1 for x in labels_in_order if x == "include")
    for i, lab in enumerate(labels_in_order, start=1):
        rid = f"T{i:04d}"
        gold[rid] = lab
        if lab == "include":
            found += 1
            streak = 0
        else:
            streak += 1
        rows.append({"i": i, "record_id": rid, "label": lab,
                     "n_relevant_found": found, "n_relevant_total": K,
                     "n_remaining": len(labels_in_order) - i,
                     "exclude_streak": streak})
    return rows, gold


def random_order_trajectory(N: int, K: int, rng: np.random.Generator):
    """无信息随机序轨迹(相关位置均匀随机)。"""
    inc_pos = set(int(p) for p in rng.choice(N, size=K, replace=False))
    labels = ["include" if i in inc_pos else "exclude" for i in range(N)]
    return build_rows(labels)


# ---------------------------------------------------------------------------
# streak 规则:机制 + 闭式对照
# ---------------------------------------------------------------------------


class TestStreak:
    def test_stop_mechanics(self):
        rows, gold = build_rows(["exclude"] * 4 + ["include"] + ["exclude"] * 10
                                + ["include"] + ["exclude"] * 20)
        traj = sr.make_trajectory(rows)
        # 位次:1-4 排除(streak 1..4)、5 纳入、6-15 排除(streak 1..10)、
        # 16 纳入、17-36 排除。streak>=5 首次在第 10 行。
        res = sr.evaluate_streak(traj, 5)
        assert res["stop_step"] == 10
        assert res["missed_relevant"] == 1  # 第 16 行的 include 未发现
        assert res["recall_at_stop"] == pytest.approx(1 / 2)

    def test_never_triggers_runs_to_completion(self):
        rows, _ = build_rows(["include", "exclude", "include", "exclude"])
        traj = sr.make_trajectory(rows)
        res = sr.evaluate_streak(traj, 2)
        assert res["stop_step"] is None
        assert res["missed_relevant"] == 0
        assert res["screened_fraction"] == 1.0
        assert "ran_to_completion" in res["safety_flags"]

    def test_closed_form_hand_cases(self):
        assert sr.p_miss_random_order(2, 2, 4) == pytest.approx(1 / 3)
        assert sr.p_miss_random_order(2, 1, 3) == pytest.approx(1 / 3)
        assert sr.p_miss_random_order(2, 1, 4) == pytest.approx(1 / 2)
        assert sr.p_miss_random_order(1, 3, 10) == pytest.approx(1 - 1 / math.comb(10, 3))

    def test_closed_form_vs_monte_carlo(self):
        rng = np.random.default_rng(123)
        trials = 4000
        for (N, K) in [(80, 8), (150, 12)]:
            for n in (4, 7, 11):
                hits = 0
                for _ in range(trials):
                    rows, gold = random_order_trajectory(N, K, rng)
                    res = sr.evaluate_streak(sr.make_trajectory(rows), n)
                    hits += int(res["missed_relevant"] > 0)
                mc = hits / trials
                exact = sr.p_miss_random_order(n, K, N)
                p_ref = min(max(exact, 0.5 / trials), 1 - 0.5 / trials)
                tol = 5 * math.sqrt(p_ref * (1 - p_ref) / trials) + 1e-9
                assert abs(mc - exact) <= tol, (N, K, n, mc, exact, tol)

    def test_closed_form_monotone_in_n_and_matches_notes_script(self):
        # 与 research/notes/check_d1_streak.py 的独立实现交叉对账(抽样点)
        sys_path = __import__("sys").path
        from pathlib import Path
        notes = Path(__file__).resolve().parents[1] / "notes"
        if str(notes) not in sys_path:
            sys_path.insert(0, str(notes))
        try:
            from check_d1_streak import p_miss_closed_form
        except ImportError:
            pytest.skip("notes 脚本不可用")
        for (N, K) in [(366, 80), (1970, 33), (4878, 10)]:
            for n in (5, 25, 100):
                assert sr.p_miss_random_order(n, K, N) == pytest.approx(
                    p_miss_closed_form(n, K, N), abs=1e-9)


# ---------------------------------------------------------------------------
# 超几何证书(D2)
# ---------------------------------------------------------------------------


class TestHypergeomCertificate:
    def test_pval_monotone_decreasing_in_k(self):
        # 证书 p 值对 k 递减(目标越宽松越容易证)
        for m0, m in ((120, 40), (300, 60)):
            for x in (0, 1, 3):
                ps = [sr.pval_certify(m0, m, x, k) for k in range(0, 8)]
                assert all(ps[i] >= ps[i + 1] - 1e-12 for i in range(len(ps) - 1))

    def test_certificate_coverage_monte_carlo(self):
        # 对每个真 R0 >= k+1,虚证率 <= alpha(容差 5 倍标准误)
        rng = np.random.default_rng(2024)
        m0, m, alpha, k, trials = 150, 40, 0.05, 0, 12000
        for r in range(1, 13):
            xs = hypergeom.rvs(m0, r, m, size=trials, random_state=rng)
            rate = float(np.mean([sr.pval_certify(m0, m, int(x), k) <= alpha
                                  for x in xs]))
            tol = 5 * math.sqrt(alpha * (1 - alpha) / trials)
            assert rate <= alpha + tol, (r, rate)

    def test_ucb_coverage(self):
        rng = np.random.default_rng(2025)
        m0, m, alpha, trials = 200, 50, 0.10, 8000
        for r_true in (0, 2, 6, 15, 40):
            bad = 0
            for _ in range(trials):
                x = int(hypergeom.rvs(m0, r_true, m, random_state=rng))
                if sr.ucb_hypergeom(m0, m, x, alpha) < r_true:
                    bad += 1
            tol = 5 * math.sqrt(alpha * (1 - alpha) / trials)
            assert bad / trials <= alpha + tol, r_true

    def test_spending_budgets(self):
        for profile in ("pocock", "obf"):
            for L in (3, 7):
                inc = sr.spending_increments(0.05, L, profile)
                assert len(inc) == L
                assert sum(inc) == pytest.approx(0.05, abs=1e-12)
                assert all(a > 0 for a in inc)
        obf = sr.spending_increments(0.05, 7, "obf")
        assert all(obf[i] <= obf[i + 1] + 1e-12 for i in range(len(obf) - 1)), \
            "OBF 增量应随 look 递增(早期更严)"

    def test_sequential_false_stop_under_dependency(self):
        # 依赖 look(同一固定池重复抽样)下,任一 look 虚证率 <= sum(alpha_l)
        rng = np.random.default_rng(2026)
        m0, m, alpha, k, L, trials = 400, 40, 0.05, 0, 5, 12000
        inc = sr.spending_increments(alpha, L, "pocock")
        stopped = np.zeros(trials, dtype=bool)
        for a_l in inc:
            xs = hypergeom.rvs(m0, 6, m, size=trials, random_state=rng)
            stopped |= np.array([sr.pval_certify(m0, m, int(x), k) <= a_l for x in xs])
        tol = 5 * math.sqrt(alpha * (1 - alpha) / trials)
        assert stopped.mean() <= alpha + tol

    def test_frontier_formula_vs_exact(self):
        for m0 in (300, 2000):
            for k in (0, 2, 5):
                for alpha in (0.05, 0.01):
                    exact = sr.exact_frontier_sample_size(m0, k, alpha)
                    approx = sr.frontier_sample_size(m0, k, alpha)
                    assert abs(exact - approx) / exact < 0.05, (m0, k, alpha)


# ---------------------------------------------------------------------------
# 子样规则:成本簿记与停止语义
# ---------------------------------------------------------------------------


class TestPilotSubsample:
    def _traj_with_gold(self):
        # 20 条;前 8 条含全部 3 条相关;look 在 t=8(池=12),m=25>=12 → 普查
        labels = (["include", "exclude", "include", "exclude", "include",
                   "exclude", "exclude", "exclude"]
                  + ["exclude"] * 12)
        rows, gold = build_rows(labels)
        return sr.make_trajectory(rows), gold

    def test_census_certificate(self):
        traj, gold = self._traj_with_gold()
        res = sr.evaluate_pilot_subsample(traj, gold, m=25, alpha=0.05,
                                          k_mode="zero", look_positions=[8],
                                          seed=0)
        assert res["stop_step"] == 8
        assert res["missed_relevant"] == 0
        assert res["screened_records"] == 20  # 8 前缀 + 12 普查
        assert res["screened_fraction"] == pytest.approx(1.0)
        assert "census_at_look" in res["safety_flags"]
        assert "stopped_certified" in res["safety_flags"]

    def test_no_certificate_when_relevant_remain(self):
        # 池中仍有相关但 m 小:证书不可得 → 全量收尾
        traj, gold = self._traj_with_gold()
        # look 提前到 t=4:池=16,含 1 条相关;t=4 处 m=3 抽样
        res = sr.evaluate_pilot_subsample(traj, gold, m=3, alpha=0.05,
                                          k_mode="zero", look_positions=[4],
                                          seed=1)
        assert res["stop_step"] is None
        assert "ran_to_completion" in res["safety_flags"]
        assert res["missed_relevant"] == 0

    def test_target_zero_needs_near_census(self):
        # 解析断言(k=0, x=0):证书过 <=> m >= (1-alpha)*m0
        alpha = 0.05
        for m0 in (50, 120, 300):
            for m in (m0 // 2, int((1 - alpha) * m0), m0):
                p = sr.pval_certify(m0, min(m, m0), 0, 0)
                should = min(m, m0) >= (1 - alpha) * m0 - 1e-9
                assert (p <= alpha) == should, (m0, m, p)

    def test_recall_target_k_formula(self):
        assert sr.k_target("recall", 19, 0.95) == 1
        assert sr.k_target("recall", 38, 0.95) == 2
        assert sr.k_target("recall", 5, 0.95) == 0
        assert sr.k_target("zero", 500, 0.95) == 0


# ---------------------------------------------------------------------------
# 组合规则(D4)
# ---------------------------------------------------------------------------


class TestCombined:
    def test_streak_gate_blocks_then_allows(self):
        # 场景:t=8 处普查证书必过(池 12 全标注、0 相关),但 streak=1 < 5 → 挡;
        # t=16 处 streak=5 → 放行并停。
        labels = (["include", "exclude", "include", "exclude", "include",
                   "exclude", "exclude", "exclude"]
                  + ["exclude"] * 8 + ["exclude"] * 4)
        rows, gold = build_rows(labels)
        traj = sr.make_trajectory(rows)
        pure = sr.evaluate_pilot_subsample(traj, gold, m=25, alpha=0.05,
                                           k_mode="zero", look_positions=[8, 16],
                                           seed=0)
        comb = sr.evaluate_combined(traj, gold, m=25, alpha=0.05, k_mode="zero",
                                    look_positions=[8, 16], spending="pocock",
                                    n_streak=5, seed=0)
        assert pure["stop_step"] == 8
        assert comb["stop_step"] == 16
        assert "streak_gate_passed" in comb["safety_flags"]
        assert comb["details"]["blocked_looks"] == 1

    def test_combined_never_stops_before_certificate(self):
        # 证书永不通过 ⇒ 组合也不停(与纯证书一致收尾)
        labels = (["include", "exclude"] * 5 + ["exclude"] * 10)
        rows, gold = build_rows(labels)
        traj = sr.make_trajectory(rows)
        comb = sr.evaluate_combined(traj, gold, m=2, alpha=0.05, k_mode="zero",
                                    look_positions=[5, 10, 15], n_streak=1,
                                    seed=2)
        assert comb["stop_step"] is None
        assert comb["missed_relevant"] == 0


# ---------------------------------------------------------------------------
# Kempny 2026 复现
# ---------------------------------------------------------------------------


class TestKempny:
    def test_c1_stop_semantics(self):
        # 3 条相关:位置 2、4、6(T0002/T0004/T0006)。样本 s=10 需恰含 T0006、
        # 不含 T0002/T0004 ⇒ x_s=1,T=round(1/10*20)=2;found_eff 在 t=2 处
        # =1(轨迹 T0002)+1(样本 T0006)=2 → 停在 t=2;T0004 未发现。
        labels = ["exclude", "include", "exclude", "include", "exclude",
                  "include"] + ["exclude"] * 14
        rows, gold = build_rows(labels)
        traj = sr.make_trajectory(rows)
        picked = None
        for seed in range(500):
            rng = np.random.default_rng(seed)
            pos = set(rng.choice(20, size=10, replace=False).tolist())
            if 5 in pos and 1 not in pos and 3 not in pos:  # 0 起:T0006∈,另两∉
                picked = seed
                break
        assert picked is not None
        res = sr.evaluate_kempny_c1(traj, gold, sample_frac=0.5, seed=picked)
        assert res["details"]["estimated_total"] == 2
        assert res["stop_step"] == 2
        assert res["missed_relevant"] == 1  # T0004 未发现
        assert res["recall_at_stop"] == pytest.approx(2 / 3)

    def test_c1_degenerate_zero_estimate(self):
        labels = ["include"] + ["exclude"] * 19
        rows, gold = build_rows(labels)
        traj = sr.make_trajectory(rows)
        for seed in range(200):
            rng = np.random.default_rng(seed)
            if 0 not in set(rng.choice(20, size=5, replace=False).tolist()):
                res = sr.evaluate_kempny_c1(traj, gold, 0.25, seed=seed)
                break
        assert res["stop_step"] == 0
        assert res["missed_relevant"] == 1
        assert "degenerate_zero_estimate" in res["safety_flags"]

    def test_c1_reliability_closed_form_vs_mc(self):
        # 随机序下准则 1 "无遗漏"率 = P(round(x_s/s*N) >= K) = P(x_s >= ceil((K-0.5)s/N))
        rng = np.random.default_rng(3030)
        N, K, frac, trials = 200, 5, 0.05, 1500
        s = int(round(frac * N))
        threshold = math.ceil((K - 0.5) * s / N - 1e-9)
        exact = 1.0 - hypergeom.cdf(threshold - 1, N, K, s)
        ok = 0
        for _ in range(trials):
            rows, gold = random_order_trajectory(N, K, rng)
            res = sr.evaluate_kempny_c1(sr.make_trajectory(rows), gold, frac,
                                        seed=int(rng.integers(1e6)))
            ok += int(res["missed_relevant"] == 0)
        mc = ok / trials
        tol = 5 * math.sqrt(max(exact * (1 - exact), 1 / trials ** 2) / trials) + 0.01
        assert abs(mc - exact) <= tol, (mc, exact)

    def test_k2_safe_threshold_equivalence_with_streak_rule(self):
        # 位次:1-5 排除(5 连)、6 纳入、7-10 排除(4 连)、11 纳入、12-20 排除。
        labels = (["exclude"] * 5 + ["include", "exclude", "exclude", "exclude",
                                     "exclude", "include"]
                  + ["exclude"] * 9)
        rows, gold = build_rows(labels)
        traj = sr.make_trajectory(rows)
        info = sr.kempny_k2_safe_threshold(traj)
        assert info["last_relevant_position"] == 11
        assert info["n_star"] == 5  # 位次 1..5 的 5 连排(另一段仅 4 连)
        for n in range(1, 12):
            res = sr.evaluate_streak(traj, n)
            missed = res["missed_relevant"] > 0
            if n <= info["n_star"]:
                assert missed, n
            else:
                assert not missed, n


# ---------------------------------------------------------------------------
# nb_window(D3 开放实现)
# ---------------------------------------------------------------------------


class TestNbWindow:
    def test_mechanics_deterministic(self):
        # 位次:1-5 排除、6 纳入、7 排除、8 纳入、9-10 排除(后接排除)。
        # look t=10、窗口 5 → 行 6..10:x_w = 2(行 6、行 8)。
        labels = ["exclude"] * 5 + ["include", "exclude", "include"] + \
                 ["exclude"] * 12
        rows, gold = build_rows(labels[:20])
        traj = sr.make_trajectory(rows)
        res = sr.evaluate_nb_window(traj, window=5, conf=0.95, k_mode="recall",
                                    recall_target=0.95, look_positions=[10])
        # 手算:x_w=2, W=5, a_post=1+2=3, b_post=1+5=6
        from scipy.stats import gamma as G
        lam_ub = G.ppf(0.95, 3.0, scale=1 / 6.0)
        r0_ub = (20 - 10) * lam_ub
        assert res["details"]["looks"][0]["x_w"] == 2
        assert res["details"]["looks"][0]["r0_ub"] == pytest.approx(r0_ub)
        # k = floor(2*0.05/0.95) = 0;r0_ub ≈ 8.0 > 0 ⇒ 不停(启发式)
        assert res["stop_step"] is None
        assert "heuristic_no_guarantee" in res["safety_flags"]

    def test_iid_coverage_recorded(self):
        # iid 序下经验覆盖(记录性,不设保证断言;容差宽)
        rng = np.random.default_rng(4040)
        N, conf, W, trials = 1000, 0.95, 100, 400
        covered = 0
        from scipy.stats import gamma as G
        for _ in range(trials):
            lam = 0.02
            x_w = rng.binomial(W, lam)
            ub = (N - W) * G.ppf(conf, 1.0 + x_w, scale=1.0 / (1.0 + W))
            covered += int(ub >= (N - W) * lam)
        assert 0.80 <= covered / trials <= 1.0  # 记录工作特性(无保证)


# ---------------------------------------------------------------------------
# 端到端:真实 S0 引擎轨迹 + 网格评估 + 汇总
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_evaluate_all_on_real_replay(self, tmp_path):
        from replay import make_synthetic_corpus, run_config
        records, gold = make_synthetic_corpus(120, seed=3)
        r = run_config(records=records, gold=gold, preset="asreview",
                       strategy="max", seed=3,
                       log_dir=tmp_path, log_prefix="e2e")
        traj = sr.load_trajectory(tmp_path / "e2e.trajectory.jsonl",
                                  tmp_path / "e2e.summary.json")
        assert traj["n_records"] == len(records)
        assert traj["n_relevant"] == sum(1 for v in gold.values() if v == "include")
        rows = sr.evaluate_all(traj, gold, dataset="e2e", seed=3)
        assert len(rows) == len(sr.default_rule_grid()) == 33
        for row in rows:
            assert row["screened_fraction"] is None or 0 <= row["screened_fraction"] <= 1
            assert row["missed_relevant"] is None or row["missed_relevant"] >= 0
            if row["family"] != "kempny_k2":
                # kempny_c1 允许 stop_step=0(样本零相关 → 估计 T=0,立即停,
                # 正是其不可靠性的语义复现);其余规则触发位次 >= 1
                assert row["stop_step"] is None or row["stop_step"] >= 0
                if row["family"] != "kempny_c1":
                    assert row["stop_step"] is None or row["stop_step"] >= 1
        # 确定性:同 seed 重跑一致
        rows2 = sr.evaluate_all(traj, gold, dataset="e2e", seed=3)
        assert [(r2["rule_id"], r2["stop_step"]) for r2 in rows] == \
               [(r2["rule_id"], r2["stop_step"]) for r2 in rows2]

    def test_summarize_aggregation(self):
        fake = []
        for seed, miss, frac in [(0, 0, 0.3), (1, 2, 0.5), (2, 0, 0.4)]:
            fake.append({"dataset": "d", "rule_id": "r", "seed": seed,
                         "missed_relevant": miss, "screened_fraction": frac,
                         "recall_at_stop": 1 - miss / 10,
                         "safety_flags": "ran_to_completion" if miss == 0 else ""})
        summ = sr.summarize(fake)
        assert len(summ) == 1
        s = summ[0]
        assert s["n_runs"] == 3
        assert s["P0_zero_miss"] == pytest.approx(2 / 3)
        assert s["missed_mean"] == pytest.approx(2 / 3)
        assert s["screened_frac_median"] == pytest.approx(0.4)

    def test_load_trajectory_consistency_guard(self, tmp_path):
        from replay import make_synthetic_corpus, run_config
        records, gold = make_synthetic_corpus(60, seed=5)
        run_config(records=records, gold=gold, preset="asreview",
                   strategy="max", seed=5, log_dir=tmp_path, log_prefix="t2")
        traj = sr.load_trajectory(tmp_path / "t2.trajectory.jsonl")
        other_gold = dict(gold)
        first_id = traj["rows"][0]["record_id"]
        other_gold[first_id] = "include" if gold[first_id] == "exclude" else "exclude"
        with pytest.raises(ValueError):
            sr.evaluate_pilot_subsample(traj, other_gold, m=10, alpha=0.05,
                                        k_mode="zero", look_positions=[10],
                                        seed=0)
