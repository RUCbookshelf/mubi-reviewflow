"""SoF 判定写回存储（2.1.5-sof，用户 2026-11-10 裁定提前到 2.1.5）。

设计 ``out/release_plan/drafts/design_2.1.5_tables.md`` v1.0 把「采纳为判定」
留给 v2；本模块实现裁决后的最小写路径：**每行（结局四元组）一条定稿判定**，
行级覆盖（同一四元组再写 = 新版本，``prev_json`` 保留上一版，单级回看，
不做全历史）。

存储挂**任务协作库**（``collaboration.db``，与仲裁会话同库同模式）：

- ``CREATE TABLE IF NOT EXISTS`` 幂等可加，不迁移、不改任何既有表；
- 本模块只做存储与行级读写：SQL 全部为**调用点内联的常量字面量 + ``?``
  绑定**（CHECK 枚举与表名/列名是 SQL 语法常量，不是外部输入；四元组、
  档位、note、快照 JSON、decided_by 等数据值一律参数绑定，绝不拼接）；
- 档位枚举沿 ``coscreen.grade_assessor``：四构件 = ``DOWNGRADE_LABELS``
  键集（none/serious/very_serious/very_very_serious），总体置信度 =
  ``GRADE_LEVELS`` 键集（high/moderate/low/very_low）——导入期一致性断言
  保证 SQL CHECK 字面量与建议器目录同源不漂移；
- 判定块**不含任何逐人评分**（rater 级字段）：只有定稿判定、来源
  （manual/adopted）、采纳时建议快照、decided_by/decided_at、prev；
- 任何底层 SQLite 失败收敛为 :class:`SofJudgmentStoreUnavailable`
  （HTTP 层映射 503 重试语义，不折叠成 403/404，也绝不静默放行）。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from coscreen import grade_assessor as grade

__all__ = ["COMPONENTS", "SOURCES", "SofJudgmentStoreUnavailable", "ensure_schema",
           "save_judgment", "load_judgments", "block_from_row", "rebind_decided_by"]

logger = logging.getLogger(__name__)

#: SoF 判定的四个 GRADE 构件（沿 grade_assessor 的构件命名，缺间接性——
#: 间接性无法从数据推导、也不在 SoF 四构件建议列中，判定写回不含它）。
COMPONENTS = ("rob", "inconsistency", "imprecision", "publication_bias")

#: 判定来源：manual = 研究者手工填；adopted = 采纳当时的服务端自动建议。
SOURCES = ("manual", "adopted")

#: 写库 busy 超时（秒），与仲裁会话同口径（coscreen/arbitration_session.py）。
STORE_TIMEOUT = 15

#: 四构件档位（grade_assessor.DOWNGRADE_LABELS 键）与总体置信度
#: （GRADE_LEVELS 键）。SQL CHECK 枚举是语法常量、不能参数绑定；用导入期
#: 断言把它们钉死在 grade_assessor 目录上——建议器目录扩档时断言即红，
#: 防止 SQL 侧静默漏档。
_SQL_COMPONENT_TIERS = ("none", "serious", "very_serious", "very_very_serious")
_SQL_OVERALL_LEVELS = ("high", "moderate", "low", "very_low")
assert set(_SQL_COMPONENT_TIERS) == set(grade.DOWNGRADE_LABELS), (
    "sof_judgments 的构件档位 SQL 枚举与 grade_assessor.DOWNGRADE_LABELS 漂移")
assert set(_SQL_OVERALL_LEVELS) == set(grade.GRADE_LEVELS), (
    "sof_judgments 的总体置信度 SQL 枚举与 grade_assessor.GRADE_LEVELS 漂移")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sof_judgments (
  comparison TEXT NOT NULL,
  outcome TEXT NOT NULL,
  timepoint TEXT NOT NULL,
  measure TEXT NOT NULL,
  rob TEXT NOT NULL CHECK(rob IN ('none', 'serious', 'very_serious', 'very_very_serious')),
  inconsistency TEXT NOT NULL
    CHECK(inconsistency IN ('none', 'serious', 'very_serious', 'very_very_serious')),
  imprecision TEXT NOT NULL
    CHECK(imprecision IN ('none', 'serious', 'very_serious', 'very_very_serious')),
  publication_bias TEXT NOT NULL
    CHECK(publication_bias IN ('none', 'serious', 'very_serious', 'very_very_serious')),
  overall TEXT NOT NULL CHECK(overall IN ('high', 'moderate', 'low', 'very_low')),
  note TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL CHECK(source IN ('manual', 'adopted')),
  adopted_snapshot TEXT,
  decided_by INTEGER NOT NULL CHECK(decided_by > 0),
  decided_at TEXT NOT NULL,
  prev_json TEXT,
  PRIMARY KEY (comparison, outcome, timepoint, measure)
);
"""


class SofJudgmentStoreUnavailable(RuntimeError):
    """判定库暂时不可用（锁超时/损坏/读写失败）——HTTP 层映射 503 重试。"""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def ensure_schema(conn: sqlite3.Connection) -> None:
    """幂等建表（``CREATE TABLE IF NOT EXISTS``，重复执行无副作用）。"""
    try:
        conn.executescript(_SCHEMA)
    except sqlite3.Error as exc:
        raise SofJudgmentStoreUnavailable("判定表初始化失败。") from exc


def _validate_payload(components: dict, overall: str, source: str) -> None:
    """防御性校验（HTTP 层 Pydantic 已拦；这里兜底，防内部调用绕过枚举）。"""
    if set(components) != set(COMPONENTS):
        raise ValueError(f"components 必须恰好包含 {COMPONENTS}")
    for name in COMPONENTS:
        if components[name] not in _SQL_COMPONENT_TIERS:
            raise ValueError(f"构件 {name} 档位非法: {components[name]!r}")
    if overall not in _SQL_OVERALL_LEVELS:
        raise ValueError(f"overall 非法: {overall!r}")
    if source not in SOURCES:
        raise ValueError(f"source 非法: {source!r}")


def block_from_row(row: sqlite3.Row) -> dict:
    """存储行 → 判定块（PUT 响应与 ``?detail=1`` 行内同形，无 rater 级字段）。"""
    def _loads(text):
        return json.loads(text) if text else None

    return {
        "components": {name: row[name] for name in COMPONENTS},
        "overall": row["overall"],
        "note": row["note"],
        "source": row["source"],
        "adopted_snapshot": _loads(row["adopted_snapshot"]),
        "decided_by": int(row["decided_by"]),
        "decided_at": row["decided_at"],
        "prev": _loads(row["prev_json"]),
    }


def save_judgment(store: Path, *, comparison: str, outcome: str, timepoint: str,
                  measure: str, components: dict, overall: str, note: str,
                  source: str, adopted_snapshot: dict | None,
                  decided_by: int) -> tuple[dict, bool]:
    """行级覆盖写入；返回 ``(判定块, 是否覆盖了已有判定)``。

    同一结局四元组再写 = 新版本：``prev_json`` 固化为上一版判定块（去掉其
    自身的 prev —— 单级回看，不做全历史），``decided_by``/``decided_at``
    取本次写入者与时刻。读-改-写包在 ``BEGIN IMMEDIATE`` 事务里，并发
    覆盖不会丢 prev。
    """
    _validate_payload(components, overall, source)
    if not all(isinstance(part, str) and part.strip()
               for part in (comparison, outcome, timepoint, measure)):
        raise ValueError("结局四元组各分量必须是非空字符串")
    if not isinstance(decided_by, int) or isinstance(decided_by, bool) or decided_by <= 0:
        raise ValueError("decided_by 必须是正整数用户 id")
    try:
        conn = sqlite3.connect(store, timeout=STORE_TIMEOUT)
    except (sqlite3.Error, OSError) as exc:
        raise SofJudgmentStoreUnavailable("判定库暂时不可用。") from exc
    try:
        conn.row_factory = sqlite3.Row
        ensure_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT comparison, outcome, timepoint, measure, rob, inconsistency, "
            "imprecision, publication_bias, overall, note, source, "
            "adopted_snapshot, decided_by, decided_at, prev_json "
            "FROM sof_judgments WHERE comparison=? AND outcome=? "
            "AND timepoint=? AND measure=?",
            (comparison, outcome, timepoint, measure)).fetchone()
        replaced = existing is not None
        prev_json = None
        if replaced:
            previous = block_from_row(existing)
            # 单级回看：prev 块与判定块同形，但其 prev 恒为 null（不递归携带
            # 更早历史——更早版本在再覆盖时被本版取代）。
            previous["prev"] = None
            prev_json = _canonical_json(previous)
        decided_at = _now_iso()
        snapshot_json = (_canonical_json(adopted_snapshot)
                         if adopted_snapshot is not None else None)
        values = (components["rob"], components["inconsistency"],
                  components["imprecision"], components["publication_bias"],
                  overall, note, source, snapshot_json, decided_by, decided_at,
                  prev_json)
        if replaced:
            conn.execute(
                "UPDATE sof_judgments SET rob=?, inconsistency=?, imprecision=?, "
                "publication_bias=?, overall=?, note=?, source=?, adopted_snapshot=?, "
                "decided_by=?, decided_at=?, prev_json=? "
                "WHERE comparison=? AND outcome=? AND timepoint=? AND measure=?",
                values + (comparison, outcome, timepoint, measure))
        else:
            conn.execute(
                "INSERT INTO sof_judgments "
                "(comparison, outcome, timepoint, measure, rob, inconsistency, "
                "imprecision, publication_bias, overall, note, source, "
                "adopted_snapshot, decided_by, decided_at, prev_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (comparison, outcome, timepoint, measure) + values)
        stored = conn.execute(
            "SELECT comparison, outcome, timepoint, measure, rob, inconsistency, "
            "imprecision, publication_bias, overall, note, source, "
            "adopted_snapshot, decided_by, decided_at, prev_json "
            "FROM sof_judgments WHERE comparison=? AND outcome=? "
            "AND timepoint=? AND measure=?",
            (comparison, outcome, timepoint, measure)).fetchone()
        conn.commit()
        return block_from_row(stored), replaced
    except sqlite3.Error as exc:
        raise SofJudgmentStoreUnavailable("判定写入失败。") from exc
    finally:
        conn.close()


def load_judgments(store: Path) -> dict[tuple[str, str, str, str], dict]:
    """全量判定 → ``{(comparison, outcome, timepoint, measure): 判定块}``。

    只读：库文件不存在或还没有 sof_judgments 表 → 空 dict（不建库、不建表，
    读路径零 DDL）；其余 SQLite 失败 → :class:`SofJudgmentStoreUnavailable`。
    """
    if not Path(store).is_file():
        return {}
    try:
        # closing() 显式关连接：``with sqlite3.connect`` 只管事务不关句柄，
        # Windows 上会持文件锁挡住任务目录清理（探针/夹具实测 WinError 32）。
        with closing(sqlite3.connect(f"{Path(store).resolve().as_uri()}?mode=ro",
                                     uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT comparison, outcome, timepoint, measure, rob, "
                    "inconsistency, imprecision, publication_bias, overall, note, "
                    "source, adopted_snapshot, decided_by, decided_at, prev_json "
                    "FROM sof_judgments").fetchall()
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc).lower():
                    return {}
                raise
    except sqlite3.Error as exc:
        raise SofJudgmentStoreUnavailable("判定读取失败。") from exc
    return {(row["comparison"], row["outcome"], row["timepoint"], row["measure"]):
            block_from_row(row) for row in rows}


def rebind_decided_by(conn: sqlite3.Connection, user_id: int) -> int:
    """整包导入改绑：把全部判定的 ``decided_by``（含 prev 快照里的）指向导入者。

    与 role grants「引用源部署用户 ID 在新部署无意义且可能误授权」同因：判定
    是研究产出、**随包保留**（不像 role grants 直接清空——清掉判定会丢定稿
    结论），但署名必须落到新部署的有效用户上，由导入者承接管理责任。返回改
    写的行数；库里还没有 sof_judgments 表（旧包/无判定）→ 0，静默跳过。

    调用方负责事务（在 BEGIN IMMEDIATE 内执行）；失败抛 sqlite3.Error 由
    调用方回滚。
    """
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        raise ValueError("user_id 必须是正整数")
    # 每游标 row_factory：连接是调用方的（task_package_api._open_rw），不改
    # 其全局设置。
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        rows = cursor.execute(
            "SELECT rowid, prev_json FROM sof_judgments WHERE decided_by!=? "
            "OR prev_json IS NOT NULL",
            (user_id,)).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return 0
        raise
    changed = 0
    for row in rows:
        prev_json = row["prev_json"]
        if prev_json:
            # prev 是本模块写出的单级 JSON 快照；解析失败（损坏/外来数据）时
            # 保留原样——绝不让展示性回看数据阻断整包导入。
            try:
                prev = json.loads(prev_json)
                if isinstance(prev, dict) and prev.get("decided_by") != user_id:
                    prev["decided_by"] = user_id
                    prev_json = _canonical_json(prev)
            except (ValueError, TypeError):
                pass
        conn.execute(
            "UPDATE sof_judgments SET decided_by=?, prev_json=? WHERE rowid=?",
            (user_id, prev_json, row["rowid"]))
        changed += 1
    return changed
