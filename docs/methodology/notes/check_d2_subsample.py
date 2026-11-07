#!/usr/bin/env python
"""D2 自检:随机子样患病率估计器 + 序贯监测的解析式(见 stopping_rules.md §2)。

覆盖:①超几何 p 值对 R0 的单调性;②单点证书的覆盖保证(蒙特卡洛);
③Bonferroni/alpha-spending 增量和=alpha 与序贯虚证率上界;④"安全-工作量"
前沿式 m_frontier ~ m0(1-alpha^{1/(k+1)}) vs 精确解。

运行:./.venv/bin/python research/notes/check_d2_subsample.py
"""
from __future__ import annotations

import math
import sys

import numpy as np
from scipy.stats import hypergeom, norm

FAILURES: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")
    if not ok:
        FAILURES.append(name)


def pval_certify(m0: int, m: int, x: int, k: int) -> float:
    """证书 'R0 <= k'(置信 1-alpha)的 p 值:
    pval(k) = sup_{r>=k+1} P_{Hypergeom(m0, r, m)}(X <= x) = 在 r=k+1 处取到。"""
    if m0 - (k + 1) < 0:
        return 0.0  # R0 >= k+1 不可能,直接可证
    return float(hypergeom.cdf(x, m0, k + 1, m))


def obf_spending(alpha: float, looks: int) -> list[float]:
    """O'Brien-Fleming 型单侧 spending 函数 A(s) = 1 - Phi(z_{1-alpha}/sqrt(s))
    的增量(Lan-DeMets 形状;此处仅用作 Bonferroni 预算分配)。"""
    z = norm.ppf(1.0 - alpha)
    cum = [1.0 - norm.cdf(z / math.sqrt(s)) for s in np.linspace(0, 1, looks + 1)[1:]]
    inc, prev = [], 0.0
    for a in cum:
        inc.append(a - prev)
        prev = a
    return inc


def main() -> int:
    print("== 1. 超几何 cdf 对总体成功数 R0 的单调递减(数值网格)==")
    ok = True
    for m0 in (50, 200):
        for m in (10, 25, m0 // 2):
            for x in (0, 2, 5):
                cdfs = [hypergeom.cdf(x, m0, r, m) for r in range(0, m0 + 1)]
                ok &= all(cdfs[i] >= cdfs[i + 1] - 1e-12 for i in range(m0))
    check("cdf 单调", ok)

    print("== 2. 单点证书覆盖(蒙特卡洛 20000 次/格,alpha=0.05,m0=300,m=60,k=0)==")
    # 若 R0 >= 1,P(证书判 'R0<=0') <= alpha
    rng = np.random.default_rng(7)
    m0, m, alpha, k, trials = 300, 60, 0.05, 0, 20000
    for r in (1, 2, 5, 10, 30):
        xs = hypergeom.rvs(m0, r, m, size=trials, random_state=rng)
        pvals = np.array([pval_certify(m0, m, int(x), k) for x in xs])
        rate = float((pvals <= alpha).mean())
        tol = 5 * math.sqrt(alpha * (1 - alpha) / trials)
        check(f"R0={r:2d} 虚证率 {rate:.4f} <= alpha={alpha}+tol({tol:.4f})",
              rate <= alpha + tol, f"(上界饱和点 r=1 附近应接近 alpha)")

    print("== 3. UCB 覆盖:U(x) = max{r: P_r(X<=x) > alpha},P(R0 > U(X)) <= alpha ==")
    # 对每个真 R0,MC 检查覆盖
    m0, m, alpha = 200, 40, 0.10
    trials = 20000
    rng = np.random.default_rng(11)
    for r_true in (0, 3, 8, 20):
        # U 表(对全部 x 可行域预计算)
        utable = {}
        for x in range(0, m + 1):
            u = -1
            for r in range(0, m0 + 1):
                if hypergeom.cdf(x, m0, r, m) > alpha:
                    u = r
            utable[x] = u
        xs = hypergeom.rvs(m0, r_true, m, size=trials, random_state=rng)
        miss = float(np.mean([utable[int(x)] < r_true for x in xs]))
        tol = 5 * math.sqrt(alpha * (1 - alpha) / trials)
        check(f"R0={r_true:2d} 失覆盖 {miss:.4f} <= alpha={alpha}+tol({tol:.4f})", miss <= alpha + tol)

    print("== 4. 序贯 spending:Pocock 等分与 OBF 形状的增量和=alpha;序贯虚证率 <= alpha ==")
    alpha, L = 0.05, 4
    for name, inc in (("pocock", [alpha / L] * L), ("obf", obf_spending(alpha, L))):
        s = sum(inc)
        check(f"{name} 增量和={s:.6f} == {alpha}", abs(s - alpha) < 1e-12,
              f"increments={[round(a, 4) for a in inc]}")
    # 依赖 look(嵌套池、有放回共享随机流)下的序贯虚证率:MC
    # 设真 R0=10 固定于 m0=500 的池;每个 look 抽 m=40(独立抽样,池不缩小=最强依赖)
    rng = np.random.default_rng(13)
    trials = 20000
    for name, inc in (("pocock", [alpha / L] * L), ("obf", obf_spending(alpha, L))):
        stop = np.zeros(trials, dtype=bool)
        for a_l in inc:
            xs = hypergeom.rvs(500, 10, 40, size=trials, random_state=rng)
            pvals = np.array([pval_certify(500, 40, int(x), 0) for x in xs])
            stop |= (pvals <= a_l)
        rate = float(stop.mean())
        tol = 5 * math.sqrt(alpha * (1 - alpha) / trials)
        check(f"{name} 序贯虚证率 {rate:.4f} <= alpha={alpha}+tol({tol:.4f})", rate <= alpha + tol)

    print("== 5. 安全-工作量前沿:m_frontier = m0(1-alpha^{1/(k+1)}) vs 精确解 ==")
    # 精确:最小的 m 使 pval_certify(m0, m, x=0, k) <= alpha
    alpha = 0.05
    for m0, k in ((2000, 0), (2000, 2), (2000, 4), (500, 0), (500, 4), (150, 4)):
        m_exact = next((m for m in range(1, m0 + 1)
                        if pval_certify(m0, m, 0, k) <= alpha), None)
        m_form = m0 * (1 - alpha ** (1 / (k + 1)))
        rel = abs(m_exact - m_form) / m_exact if m_exact else float("nan")
        check(f"m0={m0} k={k}: exact={m_exact} formula={m_form:.0f} rel_err={rel:.3f}(<0.05)",
              rel < 0.05)
    print("  (解读:零遗漏证书 k=0 需要 m~(1-alpha)m0 ≈ 普查;k 越大负担越轻)")

    print("== 6. 检测式:真 r 条 relevant 时 P(X=0)=C(m0-r,m)/C(m0,m)≈(1-m/m0)^r ==")
    for m0, r in ((2000, 5), (500, 3)):
        ms = np.linspace(1, m0, 200)
        exact0 = np.array([hypergeom.pmf(0, m0, r, int(m)) for m in ms])
        approx0 = (1 - ms / m0) ** r
        err = float(np.abs(exact0 - approx0).max())
        check(f"m0={m0} r={r}: |P(X=0)_exact - (1-m/m0)^r|max={err:.4f}(<0.02)", err < 0.02)

    print("RESULT:", "FAIL " + repr(FAILURES) if FAILURES else "ALL PASS")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
