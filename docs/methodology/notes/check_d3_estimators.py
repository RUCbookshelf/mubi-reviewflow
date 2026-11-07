#!/usr/bin/env python
"""D3 自检:估计器解析式(见 stopping_rules.md §3)。

①比例/HT 点估计无偏性;②有限总体方差公式;③分数加权 HT 的方差公式;
④窗口 Gamma-Poisson(负二项)预测上界的经验覆盖率(如实记录:模型法无
设计式覆盖保证,此自检只在 iid 序下记录工作特性)。

运行:./.venv/bin/python research/notes/check_d3_estimators.py
"""
from __future__ import annotations

import math
import sys

import numpy as np
from scipy.stats import gamma

FAILURES: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")
    if not ok:
        FAILURES.append(name)


def main() -> int:
    rng = np.random.default_rng(21)
    trials = 40000

    print("== 1. 比例估计 R0_hat = (m0/m) X 无偏 ==")
    for m0, r0, m in ((200, 20, 40), (500, 37, 50)):
        xs = rng.hypergeometric(r0, m0 - r0, m, size=trials)
        est = (m0 / m) * xs
        se = math.sqrt((m0 / m) ** 2 * m * (r0 / m0) * (1 - r0 / m0)
                       * (m0 - m) / (m0 - 1) / trials)
        bias = float(est.mean() - r0)
        check(f"m0={m0} r0={r0} m={m}: bias={bias:+.3f} (|bias|<=3se={3 * se:.3f})",
              abs(bias) <= 3 * se, f"se={se:.3f}")

    print("== 2. 有限总体方差公式 Var(X) = m q(1-q)(m0-m)/(m0-1) vs MC ==")
    for m0, r0, m in ((200, 20, 40), (500, 37, 50)):
        q = r0 / m0
        var_formula = m * q * (1 - q) * (m0 - m) / (m0 - 1)
        xs = rng.hypergeometric(r0, m0 - r0, m, size=trials)
        var_mc = float(xs.var())
        rel = abs(var_mc - var_formula) / var_formula
        check(f"m0={m0} r0={r0} m={m}: formula={var_formula:.4f} MC={var_mc:.4f} "
              f"rel_err={rel:.3f}(<0.10)", rel < 0.10)

    print("== 3. 分数加权 HT:R0_hat_HT = (m0/m) sum_{i in S} f(s_i),方差公式 ==")
    # 构造总体:尾部校准分数 f≈0(方差小)时应远优于比例估计
    m0 = 1000
    f_pool = np.concatenate([np.full(30, 0.9), np.full(m0 - 30, 0.01)])  # 校准概率
    r0 = 30  # 相关记录恰为高分区(理想校准情形)
    m = 60
    mean_f, ssq = float(f_pool.mean()), float(((f_pool - f_pool.mean()) ** 2).mean())
    var_ht = m0 ** 2 * (1 - m / m0) * ssq / m
    idx = np.array([rng.choice(m0, size=m, replace=False) for _ in range(trials)])
    ests = (m0 / m) * f_pool[idx].sum(axis=1)
    emp_var = float(ests.var())
    rel = abs(emp_var - var_ht) / var_ht
    check(f"HT 方差公式 formula={var_ht:.2f} MC={emp_var:.2f} rel_err={rel:.3f}(<0.10)",
          rel < 0.10)
    # 与未加权比例估计对比(记录方差缩减倍数)
    prop_var = (m0 / m) ** 2 * m * (r0 / m0) * (1 - r0 / m0) * (m0 - m) / (m0 - 1)
    print(f"  记录:理想校准下 HT 方差/比例估计方差 = {var_ht / prop_var:.4f} "
          f"(缩减 {prop_var / var_ht:.1f} 倍)")

    print("== 4. 窗口 Gamma-Poisson 预测上界(iid 序下的经验覆盖率,如实记录)==")
    # 序列:N=2000,前 800 条局部率 0.05、其后 0.005(模拟 AL 尾部低患病率)
    conf, W = 0.95, 200
    cov_hits = 0
    reps = 2000
    for rep in range(reps):
        lam = 0.05 if rep % 2 == 0 else 0.005
        window = rng.binomial(W, lam)
        a_post, b_post = 1.0 + window, 1.0 + W
        ub = (2000 - 800) * gamma.ppf(conf, a_post, scale=1 / b_post)
        true_remaining = int((2000 - 800) * lam)
        cov_hits += float(ub >= true_remaining)
    emp_cov = cov_hits / reps
    # 注:Plug-in 预测上界无精确覆盖保证;iid 下经验覆盖率记录值:
    check(f"iid 下经验覆盖 {emp_cov:.3f} in [0.85, 1.0](conf=0.95,如实记录,非保证)",
          0.85 <= emp_cov <= 1.0)
    print("  (注:该估计器为模型法,AL 序下覆盖率未知 → 试点中实证测量,标记 "
          "heuristic_no_guarantee)")

    print("RESULT:", "FAIL " + repr(FAILURES) if FAILURES else "ALL PASS")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
