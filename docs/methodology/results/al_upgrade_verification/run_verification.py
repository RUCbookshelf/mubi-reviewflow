#!/usr/bin/env python
"""AL 模型升级验证批(2026-10):新配置(Balanced 样本权重 + SVM/调优 NB + TF-IDF
sublinear/max_df)相对旧配置(preset=product)的提升验证。

三组配置 × 6 个 SYNERGY 集 × 5 种子 = 90 场(主批):
  legacy_nb    preset="product"           当前产品默认(NB alpha=1.0, 无平衡,
                                          逐步 refit TF-IDF, warmup=5)
  tuned_nb     preset="asreview_tuned_nb" ASReview v3 调优配置(NB alpha=3.822 +
                                          Balanced ratio=9.8 样本权重 + fixed 特征 +
                                          sublinear_tf + max_df=0.95)
  svm_balanced preset="asreview_svm"      LinearSVC(C=0.11, squared_hinge) +
                                          Balanced ratio=9.8 + 同上 TF-IDF

产物(本目录):
  verification_results.csv      主批每场一行(n_to_95/wss95/r95/ttd_mean/...)
  verification_summary.csv      按 (config, dataset) 聚合 mean±sd + macro 行
  stoprule_runs.csv             停止规则回归(S1 试点 4 集 × 5 种子,最优新配置)
  stoprule_summary.csv          streak_10/25/50 的 P0/漏检/闭式理论 vs 经验
  verification_meta.json        时间戳、耗时、配置回显

用法(仓库根,venv):
  ./.venv/bin/python research/results/al_upgrade_verification/run_verification.py \
      [--phase batch|stoprule|summary] [--configs legacy_nb tuned_nb svm_balanced] \
      [--stoprule-config tuned_nb|svm_balanced] [--datasets ...] [--seeds 1 2 3 4 5]

批阶段可多进程并行:三个进程分别 --configs legacy_nb / tuned_nb / svm_balanced,
各写 verification_results_<config>.csv(summary 阶段合并;单进程跑则直接写总表)。
幂等:已在 CSV 中的 (config, dataset, seed) 跳过。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIM_DIR = HERE.parents[1] / "sim"
DATA_DIR = HERE.parents[1] / "data" / "synergy"
if str(SIM_DIR) not in sys.path:
    sys.path.insert(0, str(SIM_DIR))

from replay import run_config, load_dataset_csv  # noqa: E402

CONFIGS: dict[str, dict] = {
    "legacy_nb": {"preset": "product"},
    "tuned_nb": {"preset": "asreview_tuned_nb"},
    "svm_balanced": {"preset": "asreview_svm"},
}

#: 6 集:患病率高(21.9%)/中(5.8%)/中-低(~1.6%)/低(0.2%)/中/中-高,
#: 与任务书一致(N/K 由 CSV 现算)。
DATASETS = [
    "Nelson_2002",
    "Donners_2021",
    "van_Dis_2020",
    "Bos_2018",
    "Moran_2021",
    "Wassenaar_2017",
]
SEEDS = [1, 2, 3, 4, 5]

#: 停止规则回归:S1 试点 4 集(与 research/results/s1_pilot 同集合),5 种子。
STOPRULE_DATASETS = ["Bos_2018", "Appenzeller-Herzog_2019", "van_der_Waal_2022",
                     "Nelson_2002"]
STREAK_NS = [10, 25, 50]

RESULT_COLS = [
    "config", "preset", "dataset", "seed", "N", "K", "prevalence_pct",
    "n_to_95", "wss95", "r95", "ttd_mean", "n_trained_steps", "elapsed_s",
    "n_random_wss95", "wss95_vs_random",
]


def dataset_stats(name: str) -> tuple[int, int]:
    _records, gold = load_dataset_csv(DATA_DIR / f"{name}.csv")
    n = len(gold)
    k = sum(1 for v in gold.values() if v == "include")
    return n, k


def read_existing(path: Path, key_cols: tuple[str, ...]) -> set[tuple]:
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as f:
        return {tuple(row[c] for c in key_cols) for row in csv.DictReader(f)}


def append_rows(path: Path, rows: list[dict], cols: list[str]) -> None:
    if not rows:
        return
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        if new_file:
            w.writeheader()
        w.writerows(rows)


def run_batch(config_names: list[str], datasets: list[str], seeds: list[int]) -> None:
    """主批:每场一行,幂等追加到 verification_results(_<config>).csv。"""
    for cfg_name in config_names:
        cfg = CONFIGS[cfg_name]
        # 单数据集调用时文件名带数据集后缀(允许按数据集并行,互不写同一文件;
        # load_result_rows 用 glob 合并全部 verification_results*.csv)。
        suffix = f"_{datasets[0]}" if len(datasets) == 1 else ""
        out = HERE / f"verification_results_{cfg_name}{suffix}.csv"
        done = read_existing(out, ("config", "dataset", "seed"))
        for ds in datasets:
            n, k = dataset_stats(ds)
            csv_path = str(DATA_DIR / f"{ds}.csv")
            for seed in seeds:
                if (cfg_name, ds, str(seed)) in done:
                    continue
                t0 = time.perf_counter()
                r = run_config(dataset_csv=csv_path, strategy="max", seed=seed,
                               top_k_log=0, collect_trajectory=False, **cfg)
                # 随机序参照:第 ceil(0.95K) 条相关的位次期望(负超几何顺序统计量
                # 期望 = r·(N+1)/(K+1),r=ceil(0.95K)),代入 WSS@95 公式。
                order_stat = math.ceil(0.95 * k) * (n + 1) / (k + 1)
                rand_wss = (n - order_stat) / n - 0.05
                rows = [{
                    "config": cfg_name, "preset": cfg["preset"], "dataset": ds,
                    "seed": seed, "N": n, "K": k,
                    "prevalence_pct": round(100.0 * k / n, 2),
                    "n_to_95": r.n_to_95, "wss95": r.wss95, "r95": r.r95,
                    "ttd_mean": r.ttd_mean, "n_trained_steps": r.n_trained_steps,
                    "elapsed_s": round(time.perf_counter() - t0, 2),
                    "n_random_wss95": round(rand_wss, 4),
                    "wss95_vs_random": round(r.wss95 - rand_wss, 4),
                }]
                append_rows(out, rows, RESULT_COLS)
                print(f"[batch] {cfg_name:12s} {ds:15s} s{seed}: "
                      f"n95={r.n_to_95:5d} wss95={r.wss95:+.4f} "
                      f"({time.perf_counter() - t0:.1f}s)", flush=True)


def load_result_rows() -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple] = set()
    paths = sorted(HERE.glob("verification_results*.csv"))
    main = HERE / "verification_results.csv"
    for p in [main, *paths]:
        if not p.exists():
            continue
        with p.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                key = (row["config"], row["dataset"], row["seed"])
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
    return rows


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def sd(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def write_summary() -> list[dict]:
    rows = load_result_rows()
    summary: list[dict] = []
    configs = [c for c in CONFIGS if any(r["config"] == c for r in rows)]
    datasets = sorted({r["dataset"] for r in rows})
    for cfg in configs:
        for ds in datasets:
            sub = [r for r in rows if r["config"] == cfg and r["dataset"] == ds]
            if not sub:
                continue
            wss = [float(r["wss95"]) for r in sub]
            n95 = [float(r["n_to_95"]) for r in sub]
            ttd = [float(r["ttd_mean"]) for r in sub]
            summary.append({
                "config": cfg, "dataset": ds, "n_seeds": len(sub),
                "N": sub[0]["N"], "K": sub[0]["K"],
                "prevalence_pct": sub[0]["prevalence_pct"],
                "wss95_mean": round(mean(wss), 4), "wss95_sd": round(sd(wss), 4),
                "n_to_95_mean": round(mean(n95), 1), "n_to_95_sd": round(sd(n95), 1),
                "ttd_mean_mean": round(mean(ttd), 1),
            })
        sub = [r for r in rows if r["config"] == cfg]
        if sub:
            wss = [float(r["wss95"]) for r in sub]
            summary.append({
                "config": cfg, "dataset": "MACRO(6集)", "n_seeds": len(sub),
                "N": "", "K": "", "prevalence_pct": "",
                "wss95_mean": round(mean(wss), 4), "wss95_sd": round(sd(wss), 4),
                "n_to_95_mean": round(mean([float(r["n_to_95"]) for r in sub]), 1),
                "n_to_95_sd": "",
                "ttd_mean_mean": "",
            })
    if summary:
        cols = list(summary[0].keys())
        with (HERE / "verification_summary.csv").open("w", newline="",
                                                      encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(summary)
    return summary


def run_stoprule(cfg_name: str, seeds: list[int]) -> None:
    """停止规则回归:S1 试点 4 集 × 种子,最优新配置,轨迹上后评估 streak。"""
    import stopping_rules as sr

    cfg = CONFIGS[cfg_name]
    out_runs = HERE / "stoprule_runs.csv"
    done = read_existing(out_runs, ("config", "dataset", "seed", "rule"))
    run_rows: list[dict] = []
    for ds in STOPRULE_DATASETS:
        csv_path = str(DATA_DIR / f"{ds}.csv")
        _recs, gold = load_dataset_csv(csv_path)
        for seed in seeds:
            t0 = time.perf_counter()
            r = run_config(dataset_csv=csv_path, strategy="max", seed=seed,
                           top_k_log=0, collect_trajectory=True, **cfg)
            traj = {"rows": r.trajectory, "n_records": r.n_records,
                    "n_relevant": r.n_relevant}
            for n in STREAK_NS:
                if (cfg_name, ds, str(seed), f"streak[n={n}]") in done:
                    continue
                ev = sr.evaluate_streak(traj, n)
                run_rows.append({
                    "config": cfg_name, "dataset": ds, "seed": seed,
                    "rule": f"streak[n={n}]", "N": r.n_records, "K": r.n_relevant,
                    "stop_step": ev["stop_step"],
                    "screened_fraction": ev["screened_fraction"],
                    "missed_relevant": ev["missed_relevant"],
                    "recall_at_stop": ev["recall_at_stop"],
                    "safety_flags": "|".join(ev["safety_flags"]),
                })
            print(f"[stoprule] {cfg_name} {ds} s{seed}: n95={r.n_to_95} "
                  f"wss95={r.wss95:+.4f} ({time.perf_counter() - t0:.1f}s)",
                  flush=True)
    append_rows(out_runs, run_rows,
                ["config", "dataset", "seed", "rule", "N", "K", "stop_step",
                 "screened_fraction", "missed_relevant", "recall_at_stop",
                 "safety_flags"])

    # ---- 汇总:P0 / 漏检 / 闭式理论 vs 经验(模型贡献) ----
    runs = read_existing_full(out_runs)
    runs = [r for r in runs if r["config"] == cfg_name]
    summ: list[dict] = []
    for ds in STOPRULE_DATASETS:
        sub_ds = [r for r in runs if r["dataset"] == ds]
        N = int(sub_ds[0]["N"]) if sub_ds else 0
        K = int(sub_ds[0]["K"]) if sub_ds else 0
        for n in STREAK_NS:
            sub = [r for r in sub_ds if r["rule"] == f"streak[n={n}]"]
            if not sub:
                continue
            missed = [int(r["missed_relevant"]) for r in sub]
            p0 = sum(1 for m in missed if m == 0) / len(missed)
            emp = 1.0 - p0
            theo = sr.p_miss_random_order(n, K, N)
            summ.append({
                "config": cfg_name, "dataset": ds, "N": N, "K": K,
                "streak_n": n, "n_runs": len(sub),
                "P0_zero_miss": round(p0, 4),
                "missed_mean": round(mean([float(m) for m in missed]), 2),
                "missed_max": max(missed),
                "p_miss_random_order_theory": round(theo, 6),
                "emp_miss_rate": round(emp, 4),
                "model_contribution_theo_minus_emp": round(theo - emp, 4),
                "P0_ge_095": "yes" if p0 >= 0.95 else "no",
            })
    for n in STREAK_NS:
        sub = [r for r in summ if r["streak_n"] == n]
        if not sub:
            continue
        summ.append({
            "config": cfg_name, "dataset": "MACRO(4集)", "N": "", "K": "",
            "streak_n": n, "n_runs": sum(int(r["n_runs"]) for r in sub),
            "P0_zero_miss": round(mean([r["P0_zero_miss"] for r in sub]), 4),
            "missed_mean": round(mean([r["missed_mean"] for r in sub]), 2),
            "missed_max": max(r["missed_max"] for r in sub),
            "p_miss_random_order_theory": round(
                mean([r["p_miss_random_order_theory"] for r in sub]), 6),
            "emp_miss_rate": round(mean([r["emp_miss_rate"] for r in sub]), 4),
            "model_contribution_theo_minus_emp": round(
                mean([r["model_contribution_theo_minus_emp"] for r in sub]), 4),
            "P0_ge_095": "yes" if mean(
                [r["P0_zero_miss"] for r in sub]) >= 0.95 else "no",
        })
    if summ:
        cols = list(summ[0].keys())
        with (HERE / "stoprule_summary.csv").open("w", newline="",
                                                  encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(summ)
        print(f"[stoprule] summary -> stoprule_summary.csv ({len(summ)} rows)",
              flush=True)


def read_existing_full(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--phase", choices=["batch", "stoprule", "summary"],
                    default="batch")
    ap.add_argument("--configs", nargs="+", default=list(CONFIGS),
                    choices=list(CONFIGS))
    ap.add_argument("--datasets", nargs="+", default=DATASETS)
    ap.add_argument("--seeds", nargs="+", type=int, default=SEEDS)
    ap.add_argument("--stoprule-config", default=None,
                    choices=[c for c in CONFIGS if c != "legacy_nb"])
    args = ap.parse_args()

    meta_path = HERE / "verification_meta.json"
    meta: dict = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.setdefault("runs", {})
    t0 = time.perf_counter()

    if args.phase == "batch":
        run_batch(args.configs, args.datasets, args.seeds)
        meta["runs"].setdefault("batch", {})
        for c in args.configs:
            meta["runs"]["batch"][c] = {
                "last_finished": datetime.now(timezone.utc).isoformat(),
                "wall_s_last": round(time.perf_counter() - t0, 1),
                "datasets": args.datasets, "seeds": args.seeds,
                "config": CONFIGS[c],
            }
    elif args.phase == "stoprule":
        if not args.stoprule_config:
            ap.error("--phase stoprule 需要 --stoprule-config")
        run_stoprule(args.stoprule_config, args.seeds)
        meta["runs"]["stoprule"] = {
            "config": args.stoprule_config, "config_params": CONFIGS[args.stoprule_config],
            "datasets": STOPRULE_DATASETS, "seeds": args.seeds,
            "last_finished": datetime.now(timezone.utc).isoformat(),
            "wall_s_last": round(time.perf_counter() - t0, 1),
        }
    elif args.phase == "summary":
        write_summary()

    meta["versions"] = {"python": sys.version.split()[0],
                        "numpy": __import__("numpy").__version__,
                        "scipy": __import__("scipy").__version__,
                        "sklearn": __import__("sklearn").__version__}
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
