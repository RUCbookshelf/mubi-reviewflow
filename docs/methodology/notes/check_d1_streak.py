#!/usr/bin/env python
"""D1 自检:连续排除规则在"无信息随机序"下的遗漏概率闭式 vs 蒙特卡洛。

推导见 stopping_rules.md §1:
  P_miss(n, K, N) = 1 - (1/C(N,K)) * sum_{j>=0} (-1)^j C(K,j) C(N - j*n, K)

运行:./.venv/bin/python research/notes/check_d1_streak.py
"""
from __future__ import annotations

import math
import sys

import numpy as np

TOL_SIGMA = 5.0  # 蒙特卡洛容差(5 倍标准误)


def p_miss_closed_form(n: int, k: int, N: int) -> float:
    """随机序下 streak-n 规则的遗漏概率(精确闭式,gaps 包含-排除)。

    K 条 relevant 在 N 中均匀放置;遗漏 <=> 前 K 个 gap(最后一条 relevant
    之前的 gap)中存在长度 >= n 的(见 stopping_rules.md 定理 1)。
    """
    if k == 0 or n <= 0:
        return 0.0
    total = math.comb(N, k)
    acc = 0  # 满足"前 K 个 gap 全 <= n-1"的放置数
    sign = 1
    for j in range(0, k + 1):
        rest = N - j * n
        if rest < k:
            break
        acc += sign * math.comb(k, j) * math.comb(rest, k)
        sign = -sign
    return 1.0 - acc / total


def p_miss_poisson_approx(n: int, k: int, N: int) -> float:
    """Poisson/几何近似:1 - exp(-K (1-p)^n), p = K/N(见 §1.3 推论)。"""
    p = k / N
    return 1.0 - math.exp(-k * (1.0 - p) ** n)


def mc_p_miss(n: int, k: int, N: int, trials: int, seed: int = 0) -> float:
    """蒙特卡洛:均匀随机放 K 条 relevant,查前 K 个 gap 是否有 >= n。"""
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(trials):
        pos = np.sort(rng.choice(N, size=k, replace=False))
        # 前 K 个 gap:g_0 = pos[0]-1;g_i = pos[i+1]-pos[i]-1 (i=0..K-2)
        gaps = np.empty(k)
        gaps[0] = pos[0] - 1
        gaps[1:] = np.diff(pos) - 1
        if (gaps >= n).any():
            hits += 1
    return hits / trials


def mc_se(p: float, trials: int) -> float:
    return math.sqrt(max(p * (1 - p), 1e-12) / trials)


def main() -> int:
    failures = []

    # --- 1. 闭式 vs 蒙特卡洛(小规模全核验)---
    print("== 1. 闭式 vs 蒙特卡洛(20000 次;容差=5 倍标准误)==")
    cases = [(N, K) for (N, K) in [(60, 6), (200, 10), (500, 25)]]
    trials = 20000
    for N, K in cases:
        for n in (3, 5, 8, 12):
            exact = p_miss_closed_form(n, K, N)
            mc = mc_p_miss(n, K, N, trials, seed=1000 * N + n)
            # p 参考值加下限,避免 exact≈0/1 时标准误退化为 0
            p_ref = min(max(exact, 0.5 / trials), 1 - 0.5 / trials)
            tol = TOL_SIGMA * mc_se(p_ref, trials) + 1e-9
            ok = abs(exact - mc) <= tol
            print(f"  N={N:4d} K={K:3d} n={n:3d}: exact={exact:.4f} "
                  f"MC={mc:.4f} tol={tol:.4f} {'PASS' if ok else 'FAIL'}")
            if not ok:
                failures.append((N, K, n))

    # --- 2. 小情形手工枚举对账(N=4,K=2,n=2 -> 1/3;N=3,K=1,n=2 -> 1/3)---
    print("== 2. 手工枚举对账 ==")
    for (N, K, n, expect) in [(4, 2, 2, 1 / 3), (3, 1, 2, 1 / 3), (4, 1, 2, 1 / 2)]:
        got = p_miss_closed_form(n, K, N)
        ok = abs(got - expect) < 1e-12
        print(f"  N={N} K={K} n={n}: closed={got:.6f} expect={expect:.6f} "
              f"{'PASS' if ok else 'FAIL'}")
        if not ok:
            failures.append(("hand", N, K, n))

    # --- 3. SYNERGY 量级数例:实际 n 下的"无信息"遗漏概率近 1 ---
    print("== 3. SYNERGY 量级数例(精确闭式;无信息随机序基线)==")
    synergy = [("Bos_2018", 4878, 10), ("Appenzeller-Herzog_2019", 2873, 26),
               ("van_der_Waal_2022", 1970, 33), ("Nelson_2002", 366, 80)]
    for name, N, K in synergy:
        row = []
        for n in (10, 25, 50, 75, 100):
            row.append(f"n={n}:{p_miss_closed_form(n, K, N):.4f}")
        print(f"  {name:24s} (N={N}, K={K}): " + "  ".join(row))

    # --- 4. 安全阈值反解 vs union-bound 近似 ---
    print("== 4. 最小安全 n(P_miss<=0.05):闭式数值反解 vs 近式 ln(K/a)/(-ln(1-p)) ==")
    alpha = 0.05
    for name, N, K in synergy:
        n_safe = next((n for n in range(1, N + 1)
                       if p_miss_closed_form(n, K, N) <= alpha), None)
        p = K / N
        n_approx = math.ceil(math.log(K / alpha) / (-math.log(1 - p)))
        print(f"  {name:24s}: n_safe={n_safe}  approx={n_approx}")

    # --- 5. Poisson 近似的精度(记录容差)---
    print("== 5. Poisson 近似 1-exp(-K(1-p)^n) 的绝对误差 ==")
    worst = 0.0
    for name, N, K in synergy:
        for n in (10, 25, 50, 75, 100, 150, 200, 300):
            err = abs(p_miss_closed_form(n, K, N) - p_miss_poisson_approx(n, K, N))
            worst = max(worst, err)
    print(f"  最大绝对误差 = {worst:.4f}(记录值;近低患病率大 N 端偏保守)")

    print("RESULT:", "FAIL " + repr(failures) if failures else "ALL PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
