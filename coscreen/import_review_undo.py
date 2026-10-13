"""Guarded last-save undo for unactivated import review, in the caller's transaction.

Only affected provenance rows and bibliographic fields are restored. Screening,
coding, original uploads and historical records are never rewritten.
"""
from __future__ import annotations
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from coscreen import review_imports as imports, review_workflow as workflow

MIGRATION_VERSION = 3


def migrate(path: Path) -> None:
    with closing(workflow.connect(path)) as conn:
        if workflow.has_table(conn, 'workflow_import_undo'):
            return
        backup = path.with_name(path.name + '.pre-import-undo-v3.bak')
        if not backup.exists():
            with closing(sqlite3.connect(backup)) as target:
                conn.backup(target)
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('''CREATE TABLE IF NOT EXISTS workflow_import_undo(
                id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL,
                actor_id TEXT NOT NULL, kind TEXT NOT NULL, subject_id TEXT NOT NULL,
                before_json TEXT NOT NULL, after_json TEXT NOT NULL,
                policy_version INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'available',
                created_at TEXT NOT NULL)''')
            conn.execute('INSERT OR IGNORE INTO workflow_migrations VALUES(?,?)',
                         (MIGRATION_VERSION, imports.now()))


def snapshot(conn, kind: str, subject: str) -> dict:
    if kind == 'candidate':
        ids = [r[0] for r in conn.execute(
            "SELECT ref_id FROM workflow_import_candidate_members WHERE candidate_id=? AND ref_type='record'",
            (subject,))]
        candidate = conn.execute('SELECT * FROM workflow_import_candidates WHERE id=?', (subject,)).fetchone()
        candidates = [dict(candidate)] if candidate else []
    else:
        ids, candidates = [subject], []
    records, proposals, articles, acceptances = [], [], [], []
    for rid in sorted(ids):
        row = conn.execute('SELECT * FROM workflow_source_records WHERE id=?', (rid,)).fetchone()
        if row:
            records.append(dict(row))
        proposal = conn.execute('SELECT * FROM workflow_import_metadata WHERE record_id=?', (rid,)).fetchone()
        if proposal:
            proposals.append(dict(proposal))
            if kind == 'metadata':
                key = imports.canonical(conn, proposal['canonical_key'])
                article = conn.execute('SELECT * FROM articles WHERE zotero_key=?', (key,)).fetchone()
                if article:
                    articles.append({'zotero_key': key, **imports.metadata(dict(article))})
                acceptance = conn.execute('SELECT * FROM workflow_metadata_acceptances WHERE zotero_key=?', (key,)).fetchone()
                if acceptance:
                    acceptances.append(dict(acceptance))
    legacy_seq = conn.execute("SELECT COALESCE(MAX(seq),0) FROM title_duplicate_undo").fetchone()[0]
    return dict(legacy_seq=legacy_seq, candidates=candidates, records=records, proposals=proposals,
                articles=articles, acceptances=acceptances)


def remember(conn, batch: str, actor: str, kind: str, subject: str, before: dict, version: int) -> None:
    after = snapshot(conn, kind, subject)
    if before == after:
        return
    conn.execute("UPDATE workflow_import_undo SET state='superseded' WHERE state='available'")
    conn.execute('''INSERT INTO workflow_import_undo(batch_id,actor_id,kind,subject_id,before_json,
        after_json,policy_version,created_at) VALUES(?,?,?,?,?,?,?,?)''',
        (batch, actor, kind, subject, imports.dumps(before), imports.dumps(after), version, imports.now()))


def latest(conn, batch: str, actor: str):
    return conn.execute('''SELECT * FROM workflow_import_undo WHERE batch_id=? AND actor_id=?
        AND state='available' ORDER BY id DESC LIMIT 1''', (batch, actor)).fetchone()


def available(conn, batch: str, actor: str) -> dict:
    row = latest(conn, batch, actor)
    if not row:
        return {'available': False}
    version = conn.execute('SELECT version FROM workflow_state WHERE singleton=1').fetchone()[0]
    after = json.loads(row['after_json'])
    safe = (version == row['policy_version'] and snapshot(conn, row['kind'], row['subject_id']) == after
            and not any(r['state'] == 'active' for r in after['records']))
    return {'available': safe, 'entry_id': row['id'] if safe else None,
            'kind': row['kind'], 'subject_id': row['subject_id']}


def restore(conn, batch: str, actor: str) -> dict:
    row = latest(conn, batch, actor)
    if not row or not available(conn, batch, actor)['available']:
        raise ValueError('The last import review changed or was activated. Refresh before correcting it.')
    before, after = json.loads(row['before_json']), json.loads(row['after_json'])
    # Restore only the mutable fields of incoming source records.
    for record in before['records']:
        conn.execute('''UPDATE workflow_source_records SET canonical_key=?,target_record_id=?,outcome=?,state=? WHERE id=?''',
                     (record['canonical_key'], record['target_record_id'], record['outcome'], record['state'], record['id']))
    for candidate in before['candidates']:
        fields = ('status', 'keeper_key', 'keeper_record_id', 'decided_by', 'updated_at')
        conn.execute('UPDATE workflow_import_candidates SET '+','.join(f'{f}=?' for f in fields)+' WHERE id=?',
                     [candidate[f] for f in fields] + [candidate['id']])
    proposal_ids = {r['record_id'] for r in before['proposals']}
    for proposal in after['proposals']:
        if proposal['record_id'] not in proposal_ids:
            conn.execute('DELETE FROM workflow_import_metadata WHERE record_id=?', (proposal['record_id'],))
    for proposal in before['proposals']:
        fields = ('canonical_key', 'before_json', 'incoming_json', 'action', 'accepted_json', 'decided_by', 'decided_at')
        conn.execute('UPDATE workflow_import_metadata SET '+','.join(f'{f}=?' for f in fields)+' WHERE record_id=?',
                     [proposal[f] for f in fields]+[proposal['record_id']])
    for article in before['articles']:
        if imports.metadata(article) == imports.metadata(next((r for r in after['articles'] if r['zotero_key']==article['zotero_key']), {})):
            continue
        conn.execute('UPDATE articles SET '+','.join(f'{f}=?' for f in imports.METADATA_FIELDS)+' WHERE zotero_key=?',
                     [article[f] for f in imports.METADATA_FIELDS]+[article['zotero_key']])
        workflow.bump_catalog_revision(None, conn=conn)
        revision = conn.execute('SELECT catalog_revision FROM workflow_state WHERE singleton=1').fetchone()[0]
        # Publish a new metadata revision so already-synchronized reviewers can
        # receive the correction; never roll their screening/coding back.
        conn.execute('''INSERT INTO workflow_metadata_acceptances VALUES(?,?,?,?) ON CONFLICT(zotero_key)
            DO UPDATE SET revision=excluded.revision,metadata_json=excluded.metadata_json,record_id=excluded.record_id''',
            (article['zotero_key'], revision, imports.dumps(imports.metadata(article)), row['subject_id']))
    conn.execute("UPDATE workflow_import_undo SET state='undone' WHERE id=?", (row['id'],))
    return {'undone': True, 'subject_id': row['subject_id'], 'kind': row['kind']}
