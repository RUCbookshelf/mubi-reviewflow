"""Per-reviewer repeat-round snapshots; decisions remain the source of truth."""
from __future__ import annotations
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from coscreen import review_workflow as store


def migrate(db: Path) -> None:
    with closing(store.connect(db, read_only=True)) as conn:
        if conn.execute('SELECT 1 FROM workflow_migrations WHERE version=6').fetchone():
            return
        backup = db.with_name(db.name + '.pre-rounds-v6.bak')
        if not backup.exists():
            with closing(sqlite3.connect(backup)) as target:
                conn.backup(target)
    with closing(store.connect(db)) as conn, conn:
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
    with closing(store.connect(db, read_only=True)) as conn:
        if not store.has_table(conn,'workflow_screen_rounds'):
            return None
        row=conn.execute('SELECT id,status FROM workflow_screen_rounds ORDER BY id DESC LIMIT 1').fetchone()
        return dict(row) if row else None
