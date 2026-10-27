"""Per-reviewer repeat-round snapshots; decisions remain the source of truth."""
from __future__ import annotations
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from coscreen import review_workflow as store

#: v6（复筛轮次快照）版本号；context() 热路径判齐用
ROUNDS_VERSION = 6


def migrate(db: Path) -> None:
    with closing(store.connect(db, read_only=True)) as conn:
        if conn.execute('SELECT 1 FROM workflow_migrations WHERE version=?', (ROUNDS_VERSION,)).fetchone():
            return
        # 备份只保护既有库；本进程新建、尚在首建链中的库没有可备份的数据。
        if not store.is_fresh_database(db):
            backup = db.with_name(db.name + '.pre-rounds-v6.bak')
            if not backup.exists():
                with closing(sqlite3.connect(backup)) as target:
                    conn.backup(target)
    with closing(store.connect_for_initial_migration(db)) as conn, conn:
        conn.execute('CREATE TABLE IF NOT EXISTS workflow_screen_rounds(id INTEGER PRIMARY KEY,round_number INTEGER NOT NULL,source_id TEXT,status TEXT NOT NULL,snapshot_json TEXT NOT NULL,created_at TEXT NOT NULL)')
        conn.execute("INSERT OR IGNORE INTO workflow_migrations VALUES(6,datetime('now'))")


def fingerprint(saved: dict | None) -> str:
    return json.dumps({k:(saved or {}).get(k) for k in ('decision','exclusion_reason','notes','tags','updated_at')},sort_keys=True,ensure_ascii=False)


def current(db: Path, saved: dict) -> dict | None:
    with closing(store.connect(db, read_only=True)) as conn:
        if not store.has_table(conn,'workflow_screen_rounds'):
            return None
        row=conn.execute('SELECT * FROM workflow_screen_rounds ORDER BY id DESC LIMIT 1').fetchone()
    if not row or row['status']!='active':
        return None
    snapshots=json.loads(row['snapshot_json'])
    pending=[key for key,baseline in snapshots.items() if not saved.get(key) or fingerprint(saved[key])==baseline]
    return {'id':row['id'],'number':row['round_number'],'source_id':row['source_id'],
            'keys':list(snapshots),'pending_keys':pending,'total':len(snapshots),'completed':len(snapshots)-len(pending)}


def start(db: Path, source_id: str | None, saved: dict, keys: list[str]) -> None:
    with closing(store.connect(db)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        number=conn.execute('SELECT COALESCE(MAX(round_number),1)+1 FROM workflow_screen_rounds').fetchone()[0]
        conn.execute("UPDATE workflow_screen_rounds SET status='closed' WHERE status='active'")
        snapshot={key:fingerprint(saved[key]) for key in keys}
        conn.execute("INSERT INTO workflow_screen_rounds(round_number,source_id,status,snapshot_json,created_at) VALUES(?,?,'active',?,datetime('now'))",(number,source_id,json.dumps(snapshot,ensure_ascii=False)))


def finish(db: Path) -> None:
    with closing(store.connect(db)) as conn, conn:
        conn.execute("UPDATE workflow_screen_rounds SET status='released' WHERE status='active'")


def marker(db: Path) -> dict | None:
    # 复审（legacy 500）：legacy/ghost 任务的筛选库尚未创建（context 的
    # legacy 分支按设计不建库），只读连接打不开文件曾是 500——按"无轮次
    # 标记"处理，与 settings() 对不存在库的既有口径一致。
    if not Path(db).is_file():
        return None
    with closing(store.connect(db, read_only=True)) as conn:
        if not store.has_table(conn,'workflow_screen_rounds'):
            return None
        row=conn.execute('SELECT id,status FROM workflow_screen_rounds ORDER BY id DESC LIMIT 1').fetchone()
        return dict(row) if row else None
