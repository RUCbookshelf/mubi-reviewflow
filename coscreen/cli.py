"""coscreen 命令行接口（SPEC.md §9）—— `python -m coscreen` 入口。

子命令：
  import  导入 Zotero CSV/RIS -> 去重 -> 建库写入
  export  导出本人决策 CSV
  merge   合并多名筛选员决策 CSV（--a/--b 两名，或 --files N 名），
          产出明细/冲突清单/stats.json/PRISMA DOT
  stats   查看进度与决策分布
  restore 从导出 CSV 恢复决策（换机恢复 / 复查回滚）
  simulate 黄金标准集上的主动学习模拟基准（WSS@95 / R@95 / 发现位次）

设计约定：
- main(argv) 返回退出码（0 成功 / 2 用户侧错误），__main__ 负责sys.exit；
- 参数缺失/子命令缺失由 argparse 处理（SystemExit 2）；
- 所有用户可见错误信息均为中文并写入 stderr。
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import datetime
from pathlib import Path

from coscreen import __version__
from coscreen import config
from coscreen import db as db_mod
from coscreen.al.simulation import replay, simulate_random_baseline
from coscreen.al.stop import current_streak, suggest_stop
from coscreen.dedup import find_duplicates
from coscreen.export import export_decisions, import_decisions_csv, load_decisions_csv
from coscreen.fulltext import list_stage2_decisions
from coscreen.parsers import ParseError, parse_file
from coscreen.stats.kappa import cohen_kappa, light_kappa, pairwise_kappa
from coscreen.stats.merge import (
    exclusion_reason_distribution,
    exclusion_reason_distribution_multi,
    merge_decisions,
    merge_decisions_multi,
    summarize,
    summarize_multi,
)
from coscreen.stats.prisma import prisma_dot, prisma_from_merge, prisma_from_merge_multi

StrPath = str | Path

# stats.json 中混淆矩阵的键分隔符（JSON 对象键必须是字符串，
# 而 KappaResult.confusion 的键为 (label_a, label_b) 二元组）
_CONFUSION_KEY_SEP = "|"

# stats.json 中两两配对的键分隔符（KappaResult/一致率字典的
# (R1, R2) 二元组键仅在 JSON 边界改写为 "R1|R2" 字符串）
_PAIRWISE_KEY_SEP = "|"


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """构造 argparse 解析器（中文帮助文案，prog 固定为 coscreen）。"""
    parser = argparse.ArgumentParser(
        prog="coscreen",
        description=f"{config.APP_NAME} —— 离线双盲文献筛选命令行工具",
    )
    parser.add_argument(
        "--version", action="version",
        version=f"coscreen {__version__}",
        help="显示版本号后退出",
    )

    sub = parser.add_subparsers(dest="command", required=True, help="可用子命令")

    # import ---------------------------------------------------------------
    p_import = sub.add_parser(
        "import", help="导入 Zotero 导出文件（CSV/RIS），自动去重后写入数据库",
        description="导入文献：解析 -> 去重 -> 建库写入，并记录任务/筛选员等元信息。",
    )
    p_import.add_argument("--file", required=True, help="Zotero 导出文件路径（.csv 或 .ris）")
    p_import.add_argument("--db", required=True, help="SQLite 数据库文件路径")
    p_import.add_argument("--screener", default="", help="筛选员姓名（写入 meta 表）")
    p_import.add_argument("--task", default="", help="任务名称（写入 meta 表）")
    p_import.set_defaults(func=cmd_import)

    # export ---------------------------------------------------------------
    p_export = sub.add_parser(
        "export", help="把本人决策导出为 CSV（供合并仲裁使用）",
        description="导出 decisions 表为 CSV（utf-8-sig，按导入顺序排列）。",
    )
    p_export.add_argument("--db", required=True, help="SQLite 数据库文件路径")
    p_export.add_argument("--out", required=True, help="导出 CSV 的目标路径")
    p_export.set_defaults(func=cmd_export)

    # merge ----------------------------------------------------------------
    p_merge = sub.add_parser(
        "merge", help="合并多名筛选员决策 CSV：明细、冲突清单、kappa 统计与 PRISMA 流程",
        description="合并多名筛选员的决策 CSV（--a/--b 两名，或 --files N 名，N>=2），"
                    "写出 4 个文件：merged_detail.csv / conflicts.csv / stats.json / prisma.dot。",
    )
    p_merge.add_argument("--a", default=None, help="筛选员 A 的决策 CSV（与 --b 搭配使用）")
    p_merge.add_argument("--b", default=None, help="筛选员 B 的决策 CSV（与 --a 搭配使用）")
    p_merge.add_argument(
        "--files", nargs="+", default=None,
        help="N>=2 份决策 CSV，按参数顺序记为 R1..RN；给出时优先于 --a/--b",
    )
    p_merge.add_argument("--outdir", required=True, help="输出目录（不存在时自动创建）")
    p_merge.set_defaults(func=cmd_merge)

    # stats ----------------------------------------------------------------
    p_stats = sub.add_parser(
        "stats", help="查看筛选进度与决策分布",
        description="显示数据库中的文献总数、已筛/未筛计数与纳入/排除/待定分布。",
    )
    p_stats.add_argument("--db", required=True, help="SQLite 数据库文件路径")
    p_stats.set_defaults(func=cmd_stats)

    # restore --------------------------------------------------------------
    p_restore = sub.add_parser(
        "restore", help="从导出的决策 CSV 恢复决策（换机恢复 / 复查回滚）",
        description="把决策 CSV 回导进数据库（按 zotero_key upsert，要求文献已存在）。",
    )
    p_restore.add_argument("--db", required=True, help="目标 SQLite 数据库文件路径")
    p_restore.add_argument("--file", required=True, help="此前导出的决策 CSV 路径")
    p_restore.set_defaults(func=cmd_restore)

    # simulate ---------------------------------------------------------------
    p_simulate = sub.add_parser(
        "simulate", help="黄金标准集上的主动学习模拟基准（WSS@95 / R@95 / 发现位次）",
        description="在完整标注的黄金标准 CSV 上回放主动学习逐条筛选，"
                    "报告 WSS@95、R@95 与平均发现位次，并与纯随机顺序基线对比"
                    "（SPEC.md §13；指标定义与 ASReview Insights 一致）。",
    )
    p_simulate.add_argument("--db", required=True, help="SQLite 数据库文件路径（文献来源）")
    p_simulate.add_argument(
        "--gold", required=True,
        help="黄金标准 CSV（至少含 zotero_key, decision 两列；decision 仅 include/exclude）",
    )
    p_simulate.add_argument(
        "--strategy", default=config.AL_DEFAULT_STRATEGY,
        help="查询策略: max | uncertainty | mixed（默认 max）",
    )
    p_simulate.add_argument(
        "--seed", type=int, default=config.AL_SEED,
        help="随机种子（mixed 策略与随机基线；默认 42）",
    )
    p_simulate.add_argument(
        "--out", default=None, help="可选：模拟报告 JSON 的输出路径",
    )
    p_simulate.set_defaults(func=cmd_simulate)

    return parser


# ---------------------------------------------------------------------------
# 子命令实现
# ---------------------------------------------------------------------------

def cmd_import(args: argparse.Namespace) -> int:
    """import 子命令：解析 -> 去重 -> 建库 -> 写 meta。"""
    try:
        articles = parse_file(args.file)
    except ParseError as exc:
        print(f"导入失败：{exc}", file=sys.stderr)
        return 2

    # 全部条目入库：重复条目按 duplicate_pairs 标记 is_duplicate_of（SPEC §5），
    # 进度统计（get_progress）只计非重复条目，故此处仅取去重报告。
    _, report = find_duplicates(articles)
    db_mod.init_db(args.db)
    summary = db_mod.upsert_articles(args.db, articles, report.exact_pairs + report.fuzzy_pairs)
    if args.screener:
        db_mod.set_meta(args.db, "screener", args.screener)
    if args.task:
        db_mod.set_meta(args.db, "task", args.task)
    db_mod.set_meta(args.db, "source_file", str(Path(args.file)))
    db_mod.set_meta(args.db, "imported_at", datetime.now().isoformat(timespec="seconds"))

    print(
        f"导入 {report.total} 条 | 精确重复 {len(report.exact_pairs)} | "
        f"模糊重复 {len(report.fuzzy_pairs)} | 进入筛选 {report.unique} 条"
    )
    print(
        f"数据库 {args.db} 写入完成：新增 {summary.n_new} 条，"
        f"更新 {summary.n_existing} 条，重复标记 {summary.n_duplicates} 条"
    )
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """export 子命令：导出决策 CSV。"""
    if not Path(args.db).is_file():
        print(f"导出失败：数据库不存在: {args.db}", file=sys.stderr)
        return 2
    out = export_decisions(args.db, args.out)
    decisions = db_mod.list_decisions(args.db)
    print(f"已导出 {len(decisions)} 条决策到 {out}")
    return 0


def cmd_merge(args: argparse.Namespace) -> int:
    """merge 子命令：合并 -> 统计 -> 写出 4 个文件。

    --files（N>=2 份，按参数顺序记为 R1..RN）优先于 --a/--b；
    两者都未提供或 --files 少于 2 份时报中文错误并返回退出码 2。
    """
    if args.files:
        if len(args.files) < 2:
            print(
                f"合并失败：--files 至少需要 2 份决策 CSV（当前 {len(args.files)} 份）",
                file=sys.stderr,
            )
            return 2
        return _merge_from_files(args)

    if args.a is None or args.b is None:
        print(
            "合并失败：请提供 --a 与 --b 两份决策 CSV，或改用 --files 提供 N>=2 份",
            file=sys.stderr,
        )
        return 2

    try:
        df_a = load_decisions_csv(args.a)
        df_b = load_decisions_csv(args.b)
    except (ValueError, OSError) as exc:
        print(f"合并失败：读取决策 CSV 出错：{exc}", file=sys.stderr)
        return 2

    merged = merge_decisions(df_a, df_b)
    summary = summarize(merged)
    reason_dist = exclusion_reason_distribution(merged)

    # kappa 仅用双方共有的决策（agree + conflict 行）
    both = merged[merged["status"].isin(("agree", "conflict"))]
    kappa = cohen_kappa(both["decision_A"].tolist(), both["decision_B"].tolist())

    flow = prisma_from_merge(merged)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    detail_path = outdir / "merged_detail.csv"
    merged.to_csv(detail_path, index=False, encoding="utf-8-sig")

    conflicts = merged[merged["status"] == "conflict"]
    conflict_path = outdir / "conflicts.csv"
    conflicts.to_csv(conflict_path, index=False, encoding="utf-8-sig")

    stats_payload = {
        "summary": dataclasses.asdict(summary),
        "kappa": _kappa_to_json_dict(kappa),
        "exclusion_reason_distribution": reason_dist,
        "prisma": flow.to_dict(),
    }
    stats_path = outdir / "stats.json"
    stats_path.write_text(
        json.dumps(stats_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    dot_path = outdir / "prisma.dot"
    dot_path.write_text(prisma_dot(flow), encoding="utf-8")

    print(
        f"合并完成：共 {summary.n_total} 条 | 一致 {summary.n_agree} | "
        f"冲突 {summary.n_conflict} | 仅A {summary.n_A_only} | 仅B {summary.n_B_only}"
        f"（一致率 {summary.agreement_rate:.1%}）"
    )
    print(
        f"Cohen's κ = {kappa.kappa:.3f}（{kappa.interpretation}，"
        f"基于双方共有的 {kappa.n} 条决策）"
    )
    print(
        f"已写出 4 个文件到 {outdir}: "
        f"merged_detail.csv（{summary.n_total} 条）、"
        f"conflicts.csv（{len(conflicts)} 条）、stats.json、prisma.dot"
    )
    return 0


def _merge_from_files(args: argparse.Namespace) -> int:
    """--files 路径：按参数顺序加载 N 份 CSV，构建 {"R1": df1, ...} 后进入多人合并。"""
    named: dict = {}
    file_labels: dict[str, str] = {}
    for i, path in enumerate(args.files, start=1):
        name = f"R{i}"
        try:
            named[name] = load_decisions_csv(path)
        except (ValueError, OSError) as exc:
            print(f"合并失败：读取决策 CSV 出错（{name} = {path}）：{exc}", file=sys.stderr)
            return 2
        file_labels[name] = str(path)
    return _merge_multi(named, file_labels, Path(args.outdir))


def _merge_multi(named: dict, file_labels: dict[str, str], outdir: Path) -> int:
    """多人合并路径（N>=2）：合并 -> 汇总 -> 两两 kappa -> 写出 4 个文件。"""
    merged = merge_decisions_multi(named)
    summary = summarize_multi(merged)
    reason_dist = exclusion_reason_distribution_multi(merged)

    # 两两 kappa：合并表按 zotero_key 排序、每键一行，
    # cohen_kappa 按位配对并自动丢弃任一方为空的行（即仅共有条目参与）
    named_labels = {
        name: merged[f"decision_{name}"].tolist() for name in named
    }
    pairwise = pairwise_kappa(named_labels)
    light = light_kappa(named_labels)

    flow = prisma_from_merge_multi(merged)

    outdir.mkdir(parents=True, exist_ok=True)

    detail_path = outdir / "merged_detail.csv"
    merged.to_csv(detail_path, index=False, encoding="utf-8-sig")

    conflicts = merged[merged["status"] == "conflict"]
    conflict_path = outdir / "conflicts.csv"
    conflicts.to_csv(conflict_path, index=False, encoding="utf-8-sig")

    stats_payload = {
        "summary": _multi_summary_to_json_dict(summary),
        "kappa": {
            "pairwise": {
                f"{left}{_PAIRWISE_KEY_SEP}{right}": _kappa_to_json_dict(result)
                for (left, right), result in pairwise.items()
            },
            "light": light,
        },
        "exclusion_reason_distribution": reason_dist,
        "prisma": flow.to_dict(),
    }
    stats_path = outdir / "stats.json"
    stats_path.write_text(
        json.dumps(stats_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    dot_path = outdir / "prisma.dot"
    dot_path.write_text(prisma_dot(flow), encoding="utf-8")

    print(
        f"合并完成：共 {summary.n_total} 条 | 一致 {summary.n_agree} | "
        f"冲突 {summary.n_conflict} | 仅单人筛 {summary.n_incomplete}"
        f"（一致率 {summary.agreement_rate:.1%}）"
    )
    labels = "，".join(f"{name} = {file_labels[name]}" for name in named)
    print(f"输入文件：{labels}")
    participation = "，".join(
        f"{name} = {count} 条" for name, count in summary.participation.items()
    )
    print(f"筛选员参与：{participation}")
    for (left, right), result in pairwise.items():
        print(
            f"κ ({left}|{right}) = {result.kappa:.3f}（{result.interpretation}，"
            f"基于共有 {result.n} 条决策）"
        )
    if light is None:
        print("Light 平均 κ = 不可用（没有共同筛选的配对）")
    else:
        print(f"Light 平均 κ = {light:.3f}")
    print(
        f"已写出 4 个文件到 {outdir}: "
        f"merged_detail.csv（{summary.n_total} 条）、"
        f"conflicts.csv（{len(conflicts)} 条）、stats.json、prisma.dot"
    )
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    """stats 子命令：进度与决策分布（print 表格）。"""
    if not Path(args.db).is_file():
        print(f"统计失败：数据库不存在: {args.db}", file=sys.stderr)
        return 2

    progress = db_mod.get_progress(args.db)
    if progress["total"] == 0:
        print("数据库为空（0 条文献）：请先用 import 子命令导入文献。")
        return 0

    meta_bits = []
    for key, label in (("task", "任务"), ("screener", "筛选员")):
        value = db_mod.get_meta(args.db, key)
        if value:
            meta_bits.append(f"{label}: {value}")
    header = f"数据库: {args.db}"
    if meta_bits:
        header += f"（{'，'.join(meta_bits)}）"
    print(header)
    print(
        f"总文献 {progress['total']} 条 | 已筛选 {progress['screened']} 条 | "
        f"未筛选 {progress['remaining']} 条"
    )
    print("决策分布:")
    for decision in config.DECISIONS:
        label = config.DECISION_LABELS.get(decision, decision)
        print(f"  {label} ({decision}) : {progress[decision]} 条")

    # 停止建议（SPEC §12）：按更新时间统计的连续排除达到阈值时提示暂停
    chrono = db_mod.list_decisions_chrono(args.db)
    streak = current_streak([d["decision"] for d in chrono])
    print(f"连续排除（按更新时间）: {streak} 条")
    if suggest_stop(streak, config.AL_STOP_STREAK_EXCLUDE):
        print("建议：暂停筛选并复查最近决策。")

    # 复筛（全文阶段，SPEC §14）：有复筛决策时追加一行复筛进度（additive）
    stage2 = list_stage2_decisions(args.db)
    if stage2:
        stage2_counts = {"include": 0, "exclude": 0, "maybe": 0}
        for d in stage2:
            if d["decision"] in stage2_counts:
                stage2_counts[d["decision"]] += 1
        print(
            f"复筛进度: 已复筛 {len(stage2)} 条 | "
            f"纳入 {stage2_counts['include']} · 排除 {stage2_counts['exclude']} · "
            f"待定 {stage2_counts['maybe']}"
        )
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    """restore 子命令：回导决策 CSV。"""
    if not Path(args.db).is_file():
        print(f"恢复失败：数据库不存在: {args.db}", file=sys.stderr)
        return 2
    try:
        n = import_decisions_csv(args.db, args.file)
    except (ValueError, OSError) as exc:
        print(f"恢复失败：{exc}", file=sys.stderr)
        return 2
    print(f"已恢复 {n} 条决策到 {args.db}")
    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    """simulate 子命令：黄金标准集上的主动学习模拟基准（SPEC §13）。

    文献取自 ``--db``（仅非重复条目），黄金标签取自 ``--gold`` CSV
    （复用 load_decisions_csv 读取，decision 统一小写）。要求数据库中
    每条文献都有黄金标签（缺失 → 退出码 2）；黄金 CSV 多出的键忽略并
    提示警告。目标策略结果与纯随机基线一并打印，``--out`` 给出时写 JSON。
    """
    if not Path(args.db).is_file():
        print(f"模拟失败：数据库不存在: {args.db}", file=sys.stderr)
        return 2
    articles = db_mod.list_articles(args.db, include_duplicates=False)
    db_keys = {a.zotero_key for a in articles}

    try:
        df = load_decisions_csv(args.gold)
    except (ValueError, OSError) as exc:
        print(f"模拟失败：读取黄金标准 CSV 出错：{exc}", file=sys.stderr)
        return 2

    gold_all = {
        row["zotero_key"]: row["decision"]
        for _, row in df.iterrows()
        if row["zotero_key"]
    }
    extra = sorted(k for k in gold_all if k not in db_keys)
    if extra:
        sample = ", ".join(repr(k) for k in extra[:5])
        print(f"警告：黄金标准中 {len(extra)} 个键不在数据库中，已忽略，例如: {sample}")
    gold = {k: v for k, v in gold_all.items() if k in db_keys}

    try:
        target = replay(articles, gold, strategy=args.strategy, seed=args.seed)
        baseline = simulate_random_baseline(articles, gold, seed=args.seed)
    except KeyError as exc:
        message = exc.args[0] if exc.args else exc
        print(f"模拟失败：{message}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"模拟失败：{exc}", file=sys.stderr)
        return 2

    print(
        f"文献 {target.n_records} 条 | 相关 {target.n_relevant} 条 | "
        f"达到 95% 召回需筛 {target.n_to_95} 条 | WSS@95 = {target.wss95:.2f} | "
        f"R@95 = {target.r95:.2f} | 平均发现位次 = {target.ttd_mean:.2f}"
    )
    print(
        f"随机基线: WSS@95 = {baseline.wss95:.2f} | "
        f"达到 95% 召回需筛 {baseline.n_to_95} 条 | "
        f"平均发现位次 = {baseline.ttd_mean:.2f}"
    )
    if args.out:
        payload = {
            "target": dataclasses.asdict(target),
            "baseline": dataclasses.asdict(baseline),
        }
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"已写出模拟报告到 {out}")
    return 0


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _kappa_to_json_dict(kappa_result: object) -> dict:
    """把 KappaResult 转为可 JSON 序列化的 dict。

    混淆矩阵的 (label_a, label_b) 二元组键改写为 "label_a|label_b" 字符串键
    （JSON 对象键必须是字符串），其余字段保持 asdict 原样。
    """
    d = dataclasses.asdict(kappa_result)  # type: ignore[arg-type]
    d["confusion"] = {
        f"{a}{_CONFUSION_KEY_SEP}{b}": count for (a, b), count in d["confusion"].items()
    }
    return d


def _multi_summary_to_json_dict(summary: object) -> dict:
    """把 MultiMergeSummary 转为可 JSON 序列化的 dict。

    pairwise_agreement 的 (name_i, name_j) 二元组键改写为 "name_i|name_j"
    字符串键（仅发生在 JSON 边界；stats 层内部保留二元组键）。
    """
    d = dataclasses.asdict(summary)  # type: ignore[arg-type]
    d["pairwise_agreement"] = {
        f"{left}{_PAIRWISE_KEY_SEP}{right}": value
        for (left, right), value in d["pairwise_agreement"].items()
    }
    return d


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口：解析参数并分发子命令，返回退出码（0 成功 / 2 错误）。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
