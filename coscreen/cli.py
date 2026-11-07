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
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from coscreen import __version__
from coscreen import config
from coscreen import db as db_mod
from coscreen.al.simulation import replay, simulate_random_baseline
from coscreen.al.stop import current_streak, suggest_stop
from coscreen.dedup import (
    find_duplicates,
    merged_group_roots,
    stabilize_conflicting_keys,
)
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
    p_import.add_argument(
        "--auto-merge-fuzzy", action="store_true",
        help="自动合并模糊候选（旧行为；默认关闭——标题相似的配对仅列为"
             "待确认候选，不再静默合并，与 Web 导入的人工候选语义一致）",
    )
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
    """import 子命令：解析 -> 去重 -> 键消歧 -> 建库 -> 写 meta。

    模糊证据（仅标题相似）与键冲突不自动合并（U1-P1/U1-P3）：默认打印为
    待确认候选；``--auto-merge-fuzzy`` 恢复旧版静默合并行为。自动合并仅
    保留"规范化 DOI 相等"与"键相等且 DOI/标题不冲突"两类可核验证据。

    库内既有条目参与去重（复审 #3）：DOI/键匹配在 ``known + 本批`` 的合并
    视图上运行（与 living_review 同一口径）——跨批次同 DOI/同键的条目打
    is_duplicate_of 标记走更新，不再无标记重复插入；既有库主键永不重写
    （R1）。模糊候选只在本批内扫描与展示（评审 R1 返修·FB4）；沿用库内
    键/同键更新的行不计入"进入筛选"（FB6）。
    """
    try:
        articles = parse_file(args.file)
    except ParseError as exc:
        print(f"导入失败：{exc}", file=sys.stderr)
        return 2
    if not articles:
        # 仅表头的 CSV（下载截断/空导出）与 RIS 的"未找到任何记录"不同：
        # 合法空导出也长这样，不阻断导入，但必须给出可自查的警告，
        # 避免用户把截断文件当成功导入（与 RIS 的 ParseError 口径对称）。
        print(
            f"警告：{Path(args.file).name} 未解析到任何数据记录"
            f"（{Path(args.file).suffix.lstrip('.').upper()} 仅含表头或内容为空）；"
            "若非有意导入空文件，请检查文件是否被截断。",
            file=sys.stderr,
        )

    # 全部条目入库：重复条目按 duplicate_pairs 标记 is_duplicate_of（SPEC §5），
    # 进度统计（get_progress）只计非重复条目。
    # r9 夜扫 b11·F2-78：坏 --db 路径（父目录不存在/不可写）此前直接抛英文
    # ``OperationalError: unable to open database file`` 逃出 main → exit 1。
    # 建库失败属用户侧错误：中文提示 + 退出码 2；合法新路径行为不变。
    try:
        db_mod.init_db(args.db)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"导入失败：无法创建或打开数据库 {args.db}：{exc}", file=sys.stderr)
        return 2
    known = db_mod.list_articles(args.db, include_duplicates=True)
    existing_keys = {a.zotero_key for a in known}
    combined = known + articles
    offset = len(known)
    # DOI/键匹配覆盖 known+本批（复审 #3：跨批次重复打标记不重复插入）；
    # 模糊层自 r8 #11 起同样在合并视图上扫描（include_fuzzy 默认开）——
    # 与 living_review/Web legacy_plan 同一口径：此前 combined 跑
    # include_fuzzy=False、模糊只扫本批，「批×库」近重复（同作者异键、
    # 双方无 DOI、token_set≈97）CLI 零提示而 living_review 报
    # pending_candidates，同一输入三条路径两种结论。FB4 的「库内既有
    # 相似标题对不属于本批候选、不得重复打印」由下方展示/计数过滤保持；
    # 合并视图模糊扫描成本与 living_review 增量更新同级（分块 + 生成树
    # 去重 + FUZZY_PAIR_LIMIT 上限）。
    _, report = find_duplicates(combined)
    # 键消歧（U1-P3/U1-P6/R6-D2；评审 R1 返修·FB1/FB3）：键相同但未合并
    # 的条目加确定性 sha256 后缀；与库内同键条目兼容（同一文献）时保留原
    # 键走更新路径，无来源 Key 的生成键按 _fallback_id → content_hash →
    # （标题+姓氏+年份）分层匹配沿用库内键——既有库主键不被重写，重导
    # 同一文件零新增行（R1）。
    stabilize_warnings: list[str] = []
    renames = stabilize_conflicting_keys(
        combined,
        report.exact_pairs + report.fuzzy_pairs + report.key_conflict_pairs,
        known=known, warnings=stabilize_warnings,
    )
    # 合并组根需按含自指事件的精确组重建（同键自然坍缩/沿用库内键的组
    # 依赖这些事件成组，过滤会导致 new/screenable 双计）——先建组、后过滤。
    roots = merged_group_roots(len(combined), report.exact_pairs)
    # 自指配对（改名后双方归一到同一键，如同键自然坍缩、生成键沿用库内键）
    # 不构成候选也不产生标记：在调用方对 report 列表赋值处真正剔除
    # （评审 R1 返修·FB7：仅改 stabilize 收到的临时拼接列表无效）。
    report.exact_pairs = [p for p in report.exact_pairs if p.kept_key != p.dup_key]
    report.fuzzy_pairs = [p for p in report.fuzzy_pairs if p.kept_key != p.dup_key]
    report.key_conflict_pairs = [
        p for p in report.key_conflict_pairs if p.kept_key != p.dup_key
    ]
    # 本批进入筛选的行数（记录口径）：合并组根在本批且键不在库内（新增行）；
    # 组根在库内＝与库重复（打标记），组根本批但键已存在＝沿用库内键/同键
    # 更新（走 UPDATE，行已在库内可筛）——两者都不计入本批新筛选数
    # （评审 R1 返修·FB6："精确重复 0 | 进入筛选 2"与"新增 0、更新 2"失真）。
    screenable = sum(
        1 for pos in range(offset, len(combined))
        if roots[pos] == pos and combined[pos].zotero_key not in existing_keys
    )
    # 沿用/命中库内键走 UPDATE 路径的行数（stabilize 已就地改写 articles 的
    # 键，此时读到的即最终键）。
    updated_existing = sum(1 for a in articles if a.zotero_key in existing_keys)
    # r8 #5（口径文案）：「精确重复」只计被判为另一行同一文献、不单独进入
    # 筛选的本批记录（批内合并/跨批打标记）；沿用库内键的更新行归
    # 「更新库内」——修复前 len(articles)-screenable 把纯重导的库内更新
    # 也计成精确重复（『精确重复 2』与『重复标记 0』并存，按该字段记
    # duplicates_removed 的脚本会多记）。
    exact_dup_count = max(0, len(articles) - screenable - updated_existing)
    incoming_keys = {a.zotero_key for a in articles}
    mark_pairs = report.exact_pairs
    screening_count = screenable
    if args.auto_merge_fuzzy:
        # opt-in 自动合并口径：只把 **dup 侧是本批键** 的模糊候选并入标记
        # （r8 #11 合并视图扫描后 fuzzy_pairs 含库内既有对，标记它们会改写
        # 与本批无关的库内行）；进入筛选数按合并后保留数打印——模糊候选
        # 每个不同的本批 dup_key 记一条被移除（与 DB 可筛选行数一致）。
        mark_pairs = report.exact_pairs + [
            p for p in report.fuzzy_pairs if p.dup_key in incoming_keys
        ]
        fuzzy_removed = len({
            p.dup_key for p in report.fuzzy_pairs
            if p.dup_key != p.kept_key and p.dup_key in incoming_keys
        })
        screening_count = max(0, screenable - fuzzy_removed)
    summary = db_mod.upsert_articles(args.db, articles, mark_pairs)
    if args.screener:
        db_mod.set_meta(args.db, "screener", args.screener)
    if args.task:
        db_mod.set_meta(args.db, "task", args.task)
    db_mod.set_meta(args.db, "source_file", str(Path(args.file)))
    db_mod.set_meta(args.db, "imported_at", datetime.now().isoformat(timespec="seconds"))

    # 候选展示/计数只保留含本批 key 的配对（评审 R1 返修·FB4：库内既有
    # 配对不属于本批，导入无关文件不得重复打印既有候选）。
    fuzzy_pairs = [
        p for p in report.fuzzy_pairs
        if p.kept_key in incoming_keys or p.dup_key in incoming_keys
    ]
    conflict_pairs = [
        p for p in report.key_conflict_pairs
        if p.kept_key in incoming_keys or p.dup_key in incoming_keys
    ]

    print(
        f"导入 {len(articles)} 条 | 精确重复 {exact_dup_count} | "
        f"更新库内 {updated_existing} | 进入筛选 {screening_count} 条"
    )
    if fuzzy_pairs:
        if args.auto_merge_fuzzy:
            print(f"模糊重复 {len(fuzzy_pairs)} 条（--auto-merge-fuzzy：已自动合并）")
        else:
            print(
                f"模糊候选 {len(fuzzy_pairs)} 条（标题相似，待人工确认，未自动合并；"
                "两条均进入筛选，请人工核对；如需旧版静默合并可加 --auto-merge-fuzzy）"
            )
            for pair in fuzzy_pairs[:20]:
                print(f"  - 候选 {pair.dup_key} ≈ 保留 {pair.kept_key}")
            if len(fuzzy_pairs) > 20:
                print(f"  … 共 {len(fuzzy_pairs)} 条")
    if report.note:
        print(f"警告：{report.note}", file=sys.stderr)
    for message in stabilize_warnings:
        print(f"警告：{message}", file=sys.stderr)
    if renames:
        print(
            f"键消歧 {len(renames)} 处（来源键与库内既有条目或批内其他条目相同，"
            "且 DOI/标题证实并非同一文献：已加确定性后缀，不再覆盖既有行）"
        )
        for old, new in renames[:20]:
            print(f"  - {old} -> {new}")
        if len(renames) > 20:
            print(f"  … 共 {len(renames)} 处")
    if conflict_pairs:
        print(
            f"键冲突候选 {len(conflict_pairs)} 条（来源键相同但 DOI 不等、标题不完全"
            "相等或首作者不兼容，未自动合并，两条均进入筛选，请人工核对）"
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
    try:
        if not db_mod.is_coscreen_database(args.db):
            print(f"导出失败：不是 {config.APP_NAME} 数据库: {args.db}", file=sys.stderr)
            return 2
        # r9 夜扫 b11·F2-77：原先先写盘、后计数——写盘成功而随后的计数读取
        # 失败时，用户拿到 exit≠0 却已经有一个看起来完整的导出文件。改为
        # **先取计数、后写盘**：任何读取/校验失败都不会留下半成品文件，写盘
        # 失败也翻译成中文 + 退出码 2（不再 traceback/exit 1）。
        decisions = db_mod.list_decisions(args.db)
        out = export_decisions(args.db, args.out)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"导出失败：{exc}", file=sys.stderr)
        return 2
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

    # r9 夜扫 b03·F2-74：merge_decisions 的键校验同样抛 ValueError（"B 侧
    # zotero_key 必须为 …"）。此前该异常逃出本函数 → traceback + exit 1，
    # 违反「0 成功 / 2 用户侧错误」契约。校验属用户输入错误，一律翻译成
    # 中文提示 + 退出码 2（不是放宽校验，只是把拒绝变成契约内的拒绝）。
    try:
        merged = merge_decisions(df_a, df_b)
    except ValueError as exc:
        print(f"合并失败：{exc}", file=sys.stderr)
        return 2
    summary = summarize(merged)
    reason_dist = exclusion_reason_distribution(merged)

    # kappa 仅用双方共有的决策（agree + conflict 行）
    both = merged[merged["status"].isin(("agree", "conflict"))]
    kappa = cohen_kappa(both["decision_A"].tolist(), both["decision_B"].tolist())

    flow = prisma_from_merge(merged)

    outdir = Path(args.outdir)
    written, error = _write_merge_outputs(
        outdir,
        {
            "merged_detail.csv": lambda p: merged.to_csv(p, index=False, encoding="utf-8-sig"),
            "conflicts.csv": lambda p: merged[merged["status"] == "conflict"].to_csv(
                p, index=False, encoding="utf-8-sig"),
            "stats.json": lambda p: p.write_text(
                json.dumps({
                    "summary": dataclasses.asdict(summary),
                    "kappa": _kappa_to_json_dict(kappa),
                    "exclusion_reason_distribution": reason_dist,
                    "prisma": flow.to_dict(),
                }, ensure_ascii=False, indent=2), encoding="utf-8"),
            "prisma.dot": lambda p: p.write_text(prisma_dot(flow), encoding="utf-8"),
        },
    )
    if error is not None:
        print(f"合并失败：写出结果文件出错：{error}", file=sys.stderr)
        return 2
    conflicts = merged[merged["status"] == "conflict"]

    print(
        f"合并完成：共 {summary.n_total} 条 | 一致 {summary.n_agree} | "
        f"冲突 {summary.n_conflict} | 仅A {summary.n_A_only} | 仅B {summary.n_B_only} | "
        f"无人筛 {summary.n_unscreened}"
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


def _write_merge_outputs(outdir: Path, writers: dict) -> tuple[list[str], str | None]:
    """写出合并结果文件；任何 OSError 转中文错误（不吐 traceback、不 exit 1）。

    r9 夜扫 b11·F2-77：merge 的写路径此前无异常处理——``--outdir``
    指向普通文件时抛 ``FileExistsError [WinError 183]``、权限不足时抛
    ``PermissionError``，都逃出 main 变成 traceback + exit 1，违反 0/2
    退出码契约。这里统一捕获并返回 (已写文件, 错误文案)，由调用方打印并
    返回 2。失败时已写出的文件如实列出（不谎报全部成功）。
    """
    written: list[str] = []
    try:
        outdir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return written, f"无法创建输出目录 {outdir}：{exc}"
    for name, writer in writers.items():
        path = outdir / name
        try:
            writer(path)
        except OSError as exc:
            return written, f"{name}：{exc}"
        written.append(name)
    return written, None


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
    # r9 夜扫 b03·F2-74：与两人路径同口径——merge_decisions_multi 的键校验
    # ValueError 属用户输入错误，翻译成退出码 2（不吐 traceback）。
    try:
        merged = merge_decisions_multi(named)
    except ValueError as exc:
        print(f"合并失败：{exc}", file=sys.stderr)
        return 2
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

    written, error = _write_merge_outputs(
        outdir,
        {
            "merged_detail.csv": lambda p: merged.to_csv(p, index=False, encoding="utf-8-sig"),
            "conflicts.csv": lambda p: merged[merged["status"] == "conflict"].to_csv(
                p, index=False, encoding="utf-8-sig"),
            "stats.json": lambda p: p.write_text(
                json.dumps({
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
                }, ensure_ascii=False, indent=2), encoding="utf-8"),
            "prisma.dot": lambda p: p.write_text(prisma_dot(flow), encoding="utf-8"),
        },
    )
    if error is not None:
        print(f"合并失败：写出结果文件出错：{error}", file=sys.stderr)
        return 2
    conflicts = merged[merged["status"] == "conflict"]

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
    # r9 夜扫 b11·F2-78：非 SQLite 文件/无权限路径此前直接抛英文 traceback
    # （db.py:579 file is not a database）→ exit 1。预检后给中文提示 + exit 2。
    if not db_mod.is_coscreen_database(args.db):
        print(f"统计失败：不是 {config.APP_NAME} 数据库: {args.db}", file=sys.stderr)
        return 2

    try:
        progress = db_mod.get_progress(args.db)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"统计失败：{exc}", file=sys.stderr)
        return 2
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
    if not db_mod.is_coscreen_database(args.db):
        print(f"恢复失败：不是 {config.APP_NAME} 数据库: {args.db}", file=sys.stderr)
        return 2
    try:
        n = import_decisions_csv(args.db, args.file)
    except (ValueError, OSError, sqlite3.Error) as exc:
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
    if not db_mod.is_coscreen_database(args.db):
        print(f"模拟失败：不是 {config.APP_NAME} 数据库: {args.db}", file=sys.stderr)
        return 2
    try:
        articles = db_mod.list_articles(args.db, include_duplicates=False)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"模拟失败：{exc}", file=sys.stderr)
        return 2
    db_keys = {a.zotero_key for a in articles}

    try:
        df = load_decisions_csv(args.gold)
    except (ValueError, OSError) as exc:
        print(f"模拟失败：读取黄金标准 CSV 出错：{exc}", file=sys.stderr)
        return 2

    # r9 夜扫 b11·F2-79：同一 zotero_key 在黄金 CSV 出现多行时，字典推导
    # 静默取最后一行——指标随行序变化且无任何提示（实测「相关 1 vs 2」
    # 「平均发现位次 3.00 vs 2.00」）。同值的重复行仍照常接受（合法冗余）；
    # 只有**互相冲突**的标签才算歧义输入：显式报错并列出冲突键，退出码 2。
    gold_all: dict[str, str] = {}
    conflicting: dict[str, set[str]] = {}
    for _, row in df.iterrows():
        key = row["zotero_key"]
        if not key:
            continue
        decision = row["decision"]
        if key in gold_all and gold_all[key] != decision:
            conflicting.setdefault(key, {gold_all[key]}).add(decision)
            continue
        gold_all.setdefault(key, decision)
    if conflicting:
        sample = ", ".join(
            f"{key!r}（{' / '.join(sorted(values))}）"
            for key, values in sorted(conflicting.items())[:5]
        )
        print(
            f"模拟失败：黄金标准 CSV 中有 {len(conflicting)} 个键带多个冲突标签"
            f"（同一文献不能既是 include 又是 exclude）：{sample}"
            f"{'…' if len(conflicting) > 5 else ''}",
            file=sys.stderr,
        )
        return 2
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
