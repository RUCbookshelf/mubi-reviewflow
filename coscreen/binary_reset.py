"""2.1.5 W1：一次性「暂不确定」存量重置迁移（初筛 + 复筛）。

语义（design_2.1.5_binary_screening.md §二）：
- 每个筛选员库第一次被本迁移处理时，删除 ``decisions`` 与 ``stage2_decisions``
  中 ``decision='maybe'`` 的行（= 回到未筛）；初筛被重置的条目其复筛决定
  （任意决定值）一并级联删除。
- 编码值、提取数据、证据字段、PDF、轨迹事件等下游成果**一律保留**；资格判断
  按当前决定自然流动，重筛纳入后自动重新出现。
- 迁移前对含 maybe 的库写 ``{db}.pre-binary-reset.bak``（在线备份 + integrity_check
  + sha256，沿 research_trace 既有备份规范）；``workflow_migrations`` 记录版本 8
  作为幂等标记；``workflow_binary_reset`` 保存摘要（谁/何时/N 条/备份），并在
  轨迹表存在时追加一条 ``binary_maybe_reset`` 事件（不受个人录制开关影响）。

本模块只做数据迁移，不注册任何 HTTP 入口；入口与 README 文案在 API/文档层。
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from coscreen import review_workflow as store

#: 2.1.5 W1 重置迁移版本号（workflow_migrations 已有 1–7）。
RESET_VERSION = 8
BACKUP_SUFFIX = ".pre-binary-reset.bak"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup(db: Path) -> tuple[Path, str]:
    """在线备份并校验；已存在则直接复用（幂等）。"""
    target = db.with_name(db.name + BACKUP_SUFFIX)
    if target.is_file():
        return target, _sha256(target)
    fd, temporary_name = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=db.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    os.chmod(temporary, 0o600)
    source = destination = None
    try:
        source = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        destination = sqlite3.connect(temporary, timeout=30)
        source.execute("PRAGMA busy_timeout=5000")
        deadline = time.monotonic() + 60

        def check(status: int, remaining: int, total: int) -> None:
            if time.monotonic() >= deadline:
                raise TimeoutError("pre-binary-reset backup exceeded 60 seconds")

        source.backup(destination, pages=256, progress=check, sleep=0.05)
        if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError("pre-binary-reset backup integrity check failed")
        destination.close()
        destination = None
        source.close()
        source = None
        os.replace(temporary, target)
        return target, _sha256(target)
    except Exception:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()
        temporary.unlink(missing_ok=True)
        raise


def _row(conn: sqlite3.Connection) -> dict:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM workflow_binary_reset WHERE singleton=1").fetchone()
    if row is None:
        return {}
    return {
        "applied_at": row["applied_at"],
        "stage1_reset": int(row["stage1_reset"]),
        "stage2_reset": int(row["stage2_reset"]),
        "stage2_cascade_reset": int(row["stage2_cascade_reset"]),
        "backup_name": row["backup_name"],
        "backup_sha256": row["backup_sha256"],
        "triggered_by": row["triggered_by"],
    }


def _candidates(conn: sqlite3.Connection) -> tuple[list[str], int, int]:
    screen_keys = ([str(r[0]) for r in conn.execute(
        "SELECT zotero_key FROM decisions WHERE decision='maybe' ORDER BY zotero_key")]
        if store.has_table(conn, "decisions") else [])
    if not store.has_table(conn, "stage2_decisions"):
        return screen_keys, 0, 0
    direct = int(conn.execute(
        "SELECT COUNT(*) FROM stage2_decisions WHERE decision='maybe'").fetchone()[0])
    cascade = 0
    for key in screen_keys:
        cascade += int(conn.execute(
            "SELECT COUNT(*) FROM stage2_decisions WHERE zotero_key=? AND decision!='maybe'",
            (key,)).fetchone()[0])
    return screen_keys, direct, cascade


def _trace(conn: sqlite3.Connection, *, task_id: str, actor_id: str,
           applied_at: str, stage1: int, stage2: int, cascade: int) -> None:
    """轨迹留痕：不受个人录制开关限制（迁移审计事件）。失败不影响迁移。"""
    try:
        if not (store.has_table(conn, "trace_events")
                and store.has_table(conn, "trace_store")):
            return
        from coscreen import research_trace
        research_trace._insert_event(
            conn, task_id=task_id, actor_id=actor_id, stage="screen",
            action="binary_maybe_reset", subject_id="binary-reset",
            before=None,
            after={"stage1_reset": stage1, "stage2_reset": stage2,
                   "stage2_cascade_reset": cascade, "applied_at": applied_at},
            source="migration")
    except Exception:
        # 轨迹是审计增强，不是迁移正确性的前提；损坏的轨迹表不阻断重置。
        return


def migrate(db: str | Path, *, task_id: str | None = None,
            actor_id: str | int | None = None) -> dict:
    """幂等执行重置。返回摘要（与 ``workflow_binary_reset`` 行同形）。

    未建库/未走基础迁移的库返回 ``{"applied": False, "reason": ...}``。
    """
    path = Path(db)
    if not path.is_file():
        return {"applied": False, "reason": "missing"}
    with closing(store.connect(path, read_only=True)) as conn:
        if not store.has_table(conn, "workflow_migrations"):
            return {"applied": False, "reason": "unmigrated"}
        done = conn.execute(
            "SELECT applied_at FROM workflow_migrations WHERE version=?",
            (RESET_VERSION,)).fetchone()
        if done:
            with closing(store.connect(path, read_only=True)) as again:
                if store.has_table(again, "workflow_binary_reset"):
                    return {"applied": False, **_row(again)}
            return {"applied": False, "applied_at": done[0], "stage1_reset": 0,
                    "stage2_reset": 0, "stage2_cascade_reset": 0,
                    "backup_name": None, "backup_sha256": None,
                    "triggered_by": None}
        screen_keys, direct, cascade = _candidates(conn)

    backup = None
    digest = None
    if screen_keys or direct or cascade:
        backup, digest = _backup(path)

    triggered = None if actor_id is None else str(actor_id)
    applied_at = _now()
    with closing(store.connect(path)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        # 并发/重试：事务内再判一次版本，二跑零操作。
        done = conn.execute(
            "SELECT applied_at FROM workflow_migrations WHERE version=?",
            (RESET_VERSION,)).fetchone()
        if done:
            if store.has_table(conn, "workflow_binary_reset"):
                return {"applied": False, **_row(conn)}
            return {"applied": False, "applied_at": done[0], "stage1_reset": 0,
                    "stage2_reset": 0, "stage2_cascade_reset": 0,
                    "backup_name": None, "backup_sha256": None,
                    "triggered_by": None}
        conn.execute("""
CREATE TABLE IF NOT EXISTS workflow_binary_reset(
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 applied_at TEXT NOT NULL,
 stage1_reset INTEGER NOT NULL DEFAULT 0,
 stage2_reset INTEGER NOT NULL DEFAULT 0,
 stage2_cascade_reset INTEGER NOT NULL DEFAULT 0,
 backup_name TEXT,
 backup_sha256 TEXT,
 triggered_by TEXT)
""")
        screen_keys, direct, cascade = _candidates(conn)
        if direct:
            conn.execute("DELETE FROM stage2_decisions WHERE decision='maybe'")
        cascade_deleted = 0
        for key in screen_keys:
            # 初筛 maybe 被重置 → 该条目任何复筛决定一并回到未筛。
            cascade_deleted += conn.execute(
                "DELETE FROM stage2_decisions WHERE zotero_key=? AND decision!='maybe'",
                (key,)).rowcount
        stage1_deleted = 0
        if screen_keys:
            stage1_deleted = conn.execute(
                "DELETE FROM decisions WHERE decision='maybe'").rowcount
        conn.execute("""
INSERT OR REPLACE INTO workflow_binary_reset(singleton,applied_at,stage1_reset,stage2_reset,stage2_cascade_reset,backup_name,backup_sha256,triggered_by)
VALUES(1,?,?,?,?,?,?,?)
""", (applied_at, stage1_deleted, direct, cascade_deleted,
      backup.name if backup else None, digest, triggered))
        _trace(conn, task_id=task_id or "", actor_id=triggered or "migration",
               applied_at=applied_at, stage1=stage1_deleted, stage2=direct,
               cascade=cascade_deleted)
        conn.execute("INSERT OR IGNORE INTO workflow_migrations VALUES(?,?)",
                     (RESET_VERSION, applied_at))
        return {"applied": True, "stage1_reset": stage1_deleted,
                "stage2_reset": direct, "stage2_cascade_reset": cascade_deleted,
                "applied_at": applied_at,
                "backup_name": backup.name if backup else None,
                "backup_sha256": digest, "triggered_by": triggered}


def summary(db: str | Path) -> dict | None:
    """只读摘要（端点用）。库/表不存在返回 None，绝不创建。"""
    path = Path(db)
    if not path.is_file():
        return None
    with closing(store.connect(path, read_only=True)) as conn:
        if not store.has_table(conn, "workflow_binary_reset"):
            return None
        row = _row(conn)
    return row or None
