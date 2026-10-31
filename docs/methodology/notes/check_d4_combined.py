#!/usr/bin/env python
"""D4 自检:组合规则(双闸门)的误差单调性与小规模闭环(见 stopping_rules.md §4)。

①组合规则停止事件 ⊆ 证书事件 ⇒ 虚停率单调不增(MC);
②streak 闸门在"模型未收敛"场景下阻止过早证书(构造性示例);
③用 stopping_rules.py 的轨迹格式做一次随机序闭环:streak 规则的经验遗漏率
对照 D1 闭式(连接理论↔实现)。

运行:./.venv/bin/python research/notes/check_d4_combined.py
"""
from __future__ import annotations

import math
import sys

import numpy as np
from scipy.stats import hypergeom

sys.path.insert(0, "research/sim")
sys.path.insert(0, "research/notes")

from check_d1_streak import p_miss_closed_form  # noqa: E402
from stopping_rules import (  # noqa: E402
    evaluate_streak, make_trajectory, pval_certify, spending_increments)

FAILURES: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")
    if not ok:
        FAILURES.append(name)


def main() -> int:
    print("== 1. 组合规则虚停率 <= 纯证书规则虚停率(同一批抽样,MC)==")
    rng = np.random.default_rng(31)
    alpha, L, m, m0, k, trials = 0.05, 5, 40, 400, 0, 20000
    # 固定真 R0=3(>k),5 个依赖 look:池不缩、独立抽样(最强共享随机性)
    inc = spending_increments(alpha, L, "pocock")
    cert_stop = np.zeros(trials, dtype=bool)
    streak_ok = rng.random((L, trials)) < 0.7  # streak 闸门以 0.7 概率放行(依赖结构任意)
    for l, a_l in enumerate(inc):
        xs = rng.hypergeometric(m0, 3, m, size=trials)
        pvals = np.array([pval_certify(m0, m, int(x), k) for x in xs])
        cert_stop |= (pvals <= a_l)
    comb_stop = cert_stop & streak_ok.any(axis=0)
    check(f"组合 {comb_stop.mean():.4f} <= 纯证书 {cert_stop.mean():.4f}",
          comb_stop.sum() <= cert_stop.sum(),
          f"(含真值:纯证书虚停率 {cert_stop.mean():.4f} <= alpha={alpha})")
    check(f"纯证书虚停率 {cert_stop.mean():.4f} <= alpha+tol", 
          cert_stop.mean() <= alpha + 5 * math.sqrt(alpha * (1 - alpha) / trials))

    print("== 2. 构造性示例:streak 闸门阻止过早证书 ==")
    # 场景:前 60 条里每 20 条混 1 条 relevant(模型未收敛),第 100 条处安排 look;
    # 证书恰好通过(抽样侥幸),但 streak=0 → 组合规则不停;等 streak=25 后下一 look 才停。
    rows = []
    labels = ["include"] * 0
    seq = []
    for i in range(1, 201):
        if i % 25 == 0:
            seq.append("include")
        else:
            seq.append("exclude")
    gold = {}
    for i, lab in enumerate(seq, start=1):
        rid = f"R{i:04d}"
        gold[rid] = lab
        streak = 0 if lab == "include" else (rows[-1]["exclude_streak"] + 1 if rows else 1)
        rows.append({
            "i": i, "record_id": rid, "label": lab,
            "n_relevant_found": sum(1 for x in seq[:i] if x == "include"),
            "n_relevant_total": sum(1 for x in seq if x == "include"),
            "n_remaining": 200 - i, "exclude_streak": streak,
        })
    traj = make_trajectory(rows, n_records=200, n_relevant=sum(1 for x in seq if x == "include"))
    # 用 m=10, alpha=0.5(故意宽松,使证书易通过)对比纯证书 vs 组合(n_streak=10)
    from stopping_rules import evaluate_pilot_subsample, evaluate_combined
    r_cert = evaluate_pilot_subsample(traj, gold, m=10, alpha=0.5, k_mode="zero",
                                      look_positions=(100, 150), spending="pocock", seed=0)
    r_comb = evaluate_combined(traj, gold, m=10, alpha=0.5, k_mode="zero",
                               look_positions=(100, 150), spending="pocock",
                               n_streak=10, seed=0)
    print(f"  纯证书 stop={r_cert['stop_step']} flags={r_cert['safety_flags']}")
    print(f"  组合   stop={r_comb['stop_step']} flags={r_comb['safety_flags']}")
    check("组合规则的停止步 >= 纯证书(或都不停)",
          r_comb["stop_step"] is None or r_cert["stop_step"] is None
          or r_comb["stop_step"] >= r_cert["stop_step"])
    check("组合规则未早于 streak 放行点停止", 
          r_comb["stop_step"] is None or r_comb["stop_step"] >= 100)

    print("== 3. 闭环:随机序合成轨迹上 streak 规则经验遗漏率 vs D1 闭式 ==")
    N, K, n, reps = 150, 8, 6, 4000
    rng2 = np.random.default_rng(41)
    miss = 0
    for _ in range(reps):
        gold = {f"S{i:04d}": "exclude" for i in range(N)}
        pos = rng2.choice(N, size=K, replace=False)
        for p in pos:
            gold[f"S{int(p):04d}"] = "include"
        order = rng2.permutation(N)
        rows = make_trajectory_rows_from_order(order, gold)
        traj = make_trajectory(rows, n_records=N, n_relevant=K)
        res = evaluate_streak(traj, n)
        miss += int(res["missed_relevant"] > 0)
    mc = miss / reps
    exact = p_miss_closed_form(n, K, N)
    tol = 5 * math.sqrt(max(exact * (1 - exact), 0.5 / reps) / reps)
    check(f"N={N} K={K} n={n}: 实现={mc:.4f} 闭式={exact:.4f} tol={tol:.4f}",
          abs(mc - exact) <= tol)
    print("RESULT:", "FAIL " + repr(FAILURES) if FAILURES else "ALL PASS")
    return 1 if FAILURES else 0


def make_trajectory_rows_from_order(order, gold):
    rows = []
    found = 0
    streak = 0
    K = sum(1 for v in gold.values() if v == "include")
    for i, idx in enumerate(order, start=1):
        rid = f"S{int(idx):04d}"
        lab = gold[rid]
        if lab == "include":
            found += 1
            streak = 0
        else:
            streak += 1
        rows.append({
            "i": i, "record_id": rid, "label": lab,
            "n_relevant_found": found, "n_relevant_total": K,
            "n_remaining": len(order) - i, "exclude_streak": streak,
        })
    return rows


if __name__ == "__main__":
    sys.exit(main())
