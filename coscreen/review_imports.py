"""Owner-authoritative source provenance and protected import staging.

All mutations accept the caller's transaction; the API holds the workflow lock.
Source rows never stand in for active articles. Original payloads are immutable.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from coscreen import db as articles_db, review_workflow as workflow
from coscreen.dedup import _norm_doi, find_duplicates
from coscreen.models import Article

MIGRATION_VERSION = 2
METADATA_FIELDS = ('item_type', 'title', 'authors', 'journal', 'year', 'doi', 'abstract', 'url')
_SCHEMA = '''
CREATE TABLE IF NOT EXISTS workflow_import_state(singleton INTEGER PRIMARY KEY CHECK(singleton=1), researched INTEGER NOT NULL DEFAULT 0);
INSERT OR IGNORE INTO workflow_import_state VALUES(1,0);
CREATE TABLE IF NOT EXISTS workflow_import_batches(
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, history_id TEXT NOT NULL, import_type TEXT NOT NULL,
 protected INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workflow_import_files(
 batch_id TEXT NOT NULL REFERENCES workflow_import_batches(id), file_index INTEGER NOT NULL,
 filename TEXT NOT NULL, digest TEXT NOT NULL, source_id TEXT NOT NULL REFERENCES workflow_sources(id),
 PRIMARY KEY(batch_id,file_index));
CREATE TABLE IF NOT EXISTS workflow_source_records(
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES workflow_sources(id), digest TEXT NOT NULL,
 row_index INTEGER NOT NULL, filename TEXT NOT NULL, original_json TEXT NOT NULL, article_json TEXT NOT NULL,
 incoming_key TEXT NOT NULL, staged_key TEXT NOT NULL, canonical_key TEXT,
 target_record_id TEXT REFERENCES workflow_source_records(id), outcome TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'staged', created_at TEXT NOT NULL,
 UNIQUE(source_id,digest,row_index));
CREATE INDEX IF NOT EXISTS workflow_records_key ON workflow_source_records(canonical_key,state);
CREATE TABLE IF NOT EXISTS workflow_import_members(
 batch_id TEXT NOT NULL REFERENCES workflow_import_batches(id), record_id TEXT NOT NULL REFERENCES workflow_source_records(id),
 file_index INTEGER NOT NULL, PRIMARY KEY(batch_id,record_id,file_index));
CREATE TABLE IF NOT EXISTS workflow_import_metadata(
 record_id TEXT PRIMARY KEY REFERENCES workflow_source_records(id), canonical_key TEXT NOT NULL,
 before_json TEXT NOT NULL, incoming_json TEXT NOT NULL, action TEXT, accepted_json TEXT,
 decided_by TEXT, decided_at TEXT);
CREATE TABLE IF NOT EXISTS workflow_import_candidates(
 id TEXT PRIMARY KEY, signature TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 keeper_key TEXT, keeper_record_id TEXT, decided_by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workflow_import_candidate_members(
 candidate_id TEXT NOT NULL REFERENCES workflow_import_candidates(id), ref_type TEXT NOT NULL,
 ref_id TEXT NOT NULL, PRIMARY KEY(candidate_id,ref_type,ref_id));
CREATE INDEX IF NOT EXISTS workflow_candidate_record ON workflow_import_candidate_members(ref_type,ref_id);
CREATE TABLE IF NOT EXISTS workflow_metadata_acceptances(
 zotero_key TEXT PRIMARY KEY, revision INTEGER NOT NULL, metadata_json TEXT NOT NULL,
 record_id TEXT NOT NULL REFERENCES workflow_source_records(id));
CREATE TABLE IF NOT EXISTS workflow_metadata_applied(
 owner_identity TEXT NOT NULL, zotero_key TEXT NOT NULL, revision INTEGER NOT NULL,
 PRIMARY KEY(owner_identity,zotero_key));
'''


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def migrate(db: str | Path) -> None:
    workflow.migrate(db)
    with closing(workflow.connect(db)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute('SELECT 1 FROM workflow_migrations WHERE version=?', (MIGRATION_VERSION,)).fetchone():
            return
        for statement in _SCHEMA.split(';'):
            if statement.strip():
                conn.execute(statement)
        conn.execute('INSERT INTO workflow_migrations VALUES(?,?)', (MIGRATION_VERSION, now()))


def has_research(task_dir: Path, conn: sqlite3.Connection) -> bool:
    """Include retained former reviewers and shared submissions, without initializing DBs."""
    if conn.execute('SELECT researched FROM workflow_import_state WHERE singleton=1').fetchone()[0]:
        return True
    work_tables = {'decisions', 'stage2_decisions', 'coding_values', 'coding_notes', 'coding_note_images',
                   'highlights', 'coding_rater_records', 'coding_consensus_records', 'coding_freezes',
                   'workflow_coding_evidence', 'coding_evidence', 'workspace_undo'}
    # Review databases and the shared collaboration store are direct task children.
    # Membership is deliberately not consulted: revoked reviewers retain their work.
    for path in sorted(task_dir.iterdir()):
        if path.suffix.lower() not in {'.db', '.sqlite3', '.sqlite'} or not path.is_file():
            continue
        if path.is_symlink():
            raise ValueError('Cannot inspect a symbolic-link task database.')
        with closing(workflow.connect(path, read_only=True)) as other:
            tables = {r[0] for r in other.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in sorted(tables & work_tables):
                if other.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone():
                    conn.execute('UPDATE workflow_import_state SET researched=1 WHERE singleton=1')
                    return True
            if 'trace_events' in tables and other.execute('''SELECT 1 FROM trace_events WHERE
                stage IN ('screen','fulltext','coding') AND action IN
                ('decision_created','decision_changed','decision_imported','decision_undone',
                 'coding_value_created','coding_value_changed','coding_value_cleared','coding_values_undone',
                 'coding_note_created','coding_note_changed','coding_note_cleared','coding_note_image_added',
                 'coding_note_image_removed') LIMIT 1''').fetchone():
                conn.execute('UPDATE workflow_import_state SET researched=1 WHERE singleton=1')
                return True
    return False


def metadata(article: dict) -> dict:
    return {field: article.get(field) for field in METADATA_FIELDS}


def active_rows(conn: sqlite3.Connection) -> list[dict]:
    return [dict(row) for row in conn.execute('SELECT * FROM articles ORDER BY import_order,zotero_key')]


def canonical(conn: sqlite3.Connection, key: str) -> str:
    seen = set()
    while key not in seen:
        seen.add(key)
        row = conn.execute('SELECT is_duplicate_of FROM articles WHERE zotero_key=?', (key,)).fetchone()
        if row is None:
            raise ValueError('Canonical article no longer exists; refresh the import review.')
        if row[0] is None:
            return key
        key = row[0]
    raise ValueError('Existing duplicate identity cycle requires manual repair.')


def proposal(conn: sqlite3.Connection, record_id: str, key: str) -> None:
    current = dict(conn.execute('SELECT * FROM articles WHERE zotero_key=?', (key,)).fetchone())
    incoming = json.loads(conn.execute('SELECT article_json FROM workflow_source_records WHERE id=?', (record_id,)).fetchone()[0])
    if metadata(current) != metadata(incoming):
        conn.execute('INSERT OR IGNORE INTO workflow_import_metadata(record_id,canonical_key,before_json,incoming_json) VALUES(?,?,?,?)',
                     (record_id, key, dumps(current), dumps(incoming)))


def add_candidate(conn: sqlite3.Connection, refs: set[tuple[str, str]], kind: str) -> str:
    signature = hashlib.sha256(dumps(sorted(refs)).encode()).hexdigest()
    row = conn.execute('SELECT id FROM workflow_import_candidates WHERE signature=?', (signature,)).fetchone()
    if row:
        return row[0]
    cid = uuid.uuid4().hex
    conn.execute('INSERT INTO workflow_import_candidates(id,signature,kind,created_at,updated_at) VALUES(?,?,?,?,?)',
                 (cid, signature, kind, now(), now()))
    conn.executemany('INSERT INTO workflow_import_candidate_members VALUES(?,?,?)', [(cid, kind_, rid) for kind_, rid in refs])
    return cid


def prepare(conn: sqlite3.Connection, files: list[dict], *, batch_id: str, history_id: str,
            owner_id: str, import_type: str, protected: bool, title_groups, progress_callback) -> dict:
    """Retain every parsed row, assign canonical targets, then reuse legacy fuzzy/title mechanics."""
    conn.execute('INSERT INTO workflow_import_batches VALUES(?,?,?,?,?,?,?)',
                 (batch_id, str(owner_id), history_id, import_type, int(protected), 'staged', now()))
    rows = active_rows(conn)
    by_key = {r['zotero_key']: r for r in rows}
    by_doi = {}
    for row in rows:
        doi = _norm_doi(row['doi'])
        if doi:
            by_doi.setdefault(doi, canonical(conn, row['zotero_key']))
    pending = [dict(r) for r in conn.execute("SELECT * FROM workflow_source_records ORDER BY created_at,id")]
    pending_keys, pending_dois = {}, {}
    for r in pending:
        pending_keys.setdefault(r['incoming_key'], []).append(r)
        doi = _norm_doi(json.loads(r['article_json']).get('doi'))
        if doi:
            pending_dois.setdefault(doi, r)
    new_ids, conflicts, incoming_articles = [], [], []
    used = set(by_key) | {r['staged_key'] for r in pending}
    for file_index, file in enumerate(files):
        sid, digest = file['source_id'], file['digest']
        source_ids = file.get('source_ids') or [sid] * len(file['articles'])
        conn.execute('INSERT INTO workflow_import_files VALUES(?,?,?,?,?)', (batch_id, file_index, file['name'], digest, sid))
        for row_index, art in enumerate(file['articles'], 1):
            sid = source_ids[row_index - 1]
            original = file['originals'][row_index - 1]
            existing = conn.execute('SELECT id FROM workflow_source_records WHERE source_id=? AND digest=? AND row_index=?',
                                    (sid, digest, row_index)).fetchone()
            if existing:
                rid = existing[0]
                conn.execute('INSERT OR IGNORE INTO workflow_import_members VALUES(?,?,?)', (batch_id, rid, file_index))
                continue
            rid = uuid.uuid4().hex
            data = art.to_dict()
            key, doi = art.zotero_key, _norm_doi(art.doi)
            match_key, target, outcome = None, None, 'new'
            key_row = by_key.get(key)
            key_priors = pending_keys.get(key, [])
            key_refs = set()
            conflicting = False
            if key_row:
                key_refs.add(('active', canonical(conn, key)))
                old_doi = _norm_doi(key_row['doi'])
                conflicting = bool(doi and old_doi and doi != old_doi)
            # Inspect every retained external-key alias before taking a DOI
            # shortcut. An alias may already point at an active canonical row
            # whose physical key differs from the uploaded source key.
            for prior_row in key_priors:
                old_doi = _norm_doi(json.loads(prior_row['article_json']).get('doi'))
                conflicting = conflicting or bool(doi and old_doi and doi != old_doi)
                prior_key, prior_root = record_target(conn, prior_row['id'])
                key_refs.add(('active', prior_key) if prior_key else ('record', prior_root))
            prior = pending_dois.get(doi) if doi else None
            doi_ref = None
            if doi and doi in by_doi:
                doi_ref = ('active', by_doi[doi])
            elif prior:
                prior_key, prior_root = record_target(conn, prior['id'])
                doi_ref = ('active', prior_key) if prior_key else ('record', prior_root)
            contradictory_refs = key_refs | ({doi_ref} if doi_ref else set())
            if conflicting or len(contradictory_refs) > 1:
                outcome = 'identity_conflict'
                conflicts.append(({('record', rid)} | contradictory_refs, outcome))
            elif doi_ref:
                if doi_ref[0] == 'active':
                    match_key, outcome = doi_ref[1], 'exact_doi'
                else:
                    target, outcome = prior['id'], 'exact_staged'
            elif key_refs:
                key_ref = next(iter(key_refs))
                if key_ref[0] == 'active':
                    match_key, outcome = key_ref[1], 'exact_key'
                else:
                    target, outcome = key_priors[0]['id'], 'exact_staged'
            staged_key = key
            if staged_key in used and match_key is None:
                staged_key = 'staged-' + hashlib.sha256(f'{sid}:{digest}:{row_index}'.encode()).hexdigest()[:28]
            used.add(staged_key)
            data['zotero_key'] = staged_key
            conn.execute('''INSERT INTO workflow_source_records
                (id,source_id,digest,row_index,filename,original_json,article_json,incoming_key,staged_key,canonical_key,target_record_id,outcome,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (rid, sid, digest, row_index, file['name'], dumps(original), dumps(data), key, staged_key, match_key, target, outcome, now()))
            conn.execute('INSERT INTO workflow_import_members VALUES(?,?,?)', (batch_id, rid, file_index))
            new_ids.append(rid)
            incoming_articles.append(Article.from_dict(data))
            if match_key:
                proposal(conn, rid, match_key)
            # Keep aliases even for exact-linked rows: a later row with that
            # source key and a conflicting DOI still needs identity review.
            r = {'id': rid, 'incoming_key': key, 'article_json': dumps(data), 'staged_key': staged_key}
            pending_keys.setdefault(key, []).append(r)
            if doi:
                pending_dois.setdefault(doi, r)
    for refs, kind in conflicts:
        add_candidate(conn, refs, kind)
    # Only canonical active records and independent staged representatives take
    # part. Exact matching above prevents incoming keys from changing active IDs.
    reps, ref_by_key = [], {}
    for row in rows:
        if row['is_duplicate_of'] is None:
            art = Article.from_dict(row)
            reps.append(art)
            ref_by_key[art.zotero_key] = ('active', art.zotero_key)
    staged = conn.execute("SELECT * FROM workflow_source_records WHERE state='staged' AND canonical_key IS NULL AND target_record_id IS NULL").fetchall()
    for record in staged:
        art = Article.from_dict(json.loads(record['article_json']))
        # Conflicting external IDs must remain distinct through the exact pass.
        art.raw['_generated_key'] = True
        reps.append(art)
        ref_by_key[art.zotero_key] = ('record', record['id'])
    _, report = find_duplicates(reps, progress_callback=progress_callback)
    # Feed all independent representatives to title review. DOI conflicts remain
    # explicit candidates even if the legacy exact DOI pass would collapse them.
    groups = title_groups(reps, {r['staged_key'] for r in staged if r['id'] in new_ids}, report.fuzzy_pairs)
    for group in groups:
        refs = {ref_by_key[m['zotero_key']] for m in group['members']}
        add_candidate(conn, refs, 'fuzzy' if group['normalized_title'].startswith('fuzzy:') else 'title')
    return {'new_record_ids': new_ids, 'note': report.note, 'fuzzy_candidate_excess': len(report.fuzzy_pairs)}


def record_target(conn: sqlite3.Connection, rid: str) -> tuple[str | None, str]:
    seen = set()
    while rid not in seen:
        seen.add(rid)
        row = conn.execute('SELECT canonical_key,target_record_id FROM workflow_source_records WHERE id=?', (rid,)).fetchone()
        if row is None:
            raise ValueError('Missing source record target.')
        if row[0]:
            return canonical(conn, row[0]), rid
        if not row[1]:
            return None, rid
        rid = row[1]
    raise ValueError('Source record target cycle requires review.')


def blocked(conn: sqlite3.Connection, rid: str, *, ignored_candidates: set[str] | None = None) -> bool:
    ignored_candidates = ignored_candidates or set()
    rows = conn.execute('''SELECT c.id FROM workflow_import_candidate_members m JOIN workflow_import_candidates c ON c.id=m.candidate_id
        WHERE m.ref_type='record' AND m.ref_id=? AND c.status IN ('pending','undecided')''', (rid,))
    return any(row[0] not in ignored_candidates for row in rows)


def dependency_blocked(conn: sqlite3.Connection, rid: str, *, ignored_candidates: set[str] | None = None) -> bool:
    """Check every staged dependency, including intermediate linked records."""
    seen = set()
    while rid is not None:
        if rid in seen:
            raise ValueError('Source record target cycle requires review.')
        seen.add(rid)
        if blocked(conn, rid, ignored_candidates=ignored_candidates):
            return True
        row = conn.execute('SELECT target_record_id FROM workflow_source_records WHERE id=?', (rid,)).fetchone()
        if row is None:
            raise ValueError('Missing source record target.')
        rid = row[0]
    return False


def refresh_statuses(conn: sqlite3.Connection) -> None:
    conn.execute('''UPDATE workflow_import_batches SET status=CASE
        WHEN NOT EXISTS(SELECT 1 FROM workflow_import_members m JOIN workflow_source_records r ON r.id=m.record_id
                        WHERE m.batch_id=workflow_import_batches.id AND r.state='staged') THEN 'active'
        WHEN EXISTS(SELECT 1 FROM workflow_import_members m JOIN workflow_source_records r ON r.id=m.record_id
                    WHERE m.batch_id=workflow_import_batches.id AND r.state='active') THEN 'partially_active'
        ELSE 'staged' END''')


def activate(conn: sqlite3.Connection, db: Path, batch_id: str) -> dict:
    """Activate only resolved records in this batch; repeated calls add no work."""
    records = conn.execute('''SELECT DISTINCT r.* FROM workflow_source_records r JOIN workflow_import_members m ON m.record_id=r.id
                             WHERE m.batch_id=? AND r.state='staged' ORDER BY r.created_at,r.row_index''', (batch_id,)).fetchall()
    if not records:
        return {'activated_new': 0, 'activated_source_records': 0}
    batch_ids = {r['id'] for r in records}
    added = linked = 0
    for record in records:
        rid = record['id']
        if dependency_blocked(conn, rid):
            continue
        key, root = record_target(conn, rid)
        # Cross-batch roots must be explicitly activated in their own batch.
        if key is None:
            if root not in batch_ids:
                continue
            root_row = conn.execute('SELECT * FROM workflow_source_records WHERE id=?', (root,)).fetchone()
            data = json.loads(root_row['article_json'])
            if conn.execute('SELECT 1 FROM articles WHERE zotero_key=?', (data['zotero_key'],)).fetchone():
                raise ValueError('Staged article key now exists. Refresh and review before activation.')
            article = Article.from_dict(data)
            # No upsert on activation: existing work can never be refreshed.
            summary = articles_db.upsert_articles(db, [article], [], conn=conn, preserve_existing=True)
            added += summary.n_new
            key = article.zotero_key
            conn.execute('UPDATE workflow_source_records SET canonical_key=? WHERE id=?', (key, root))
        proposal(conn, rid, key)
        proposal_row = conn.execute('SELECT action FROM workflow_import_metadata WHERE record_id=?', (rid,)).fetchone()
        if proposal_row and proposal_row[0] is None:
            continue
        workflow.associate_sources(db, [key], [record['source_id']], conn=conn)
        conn.execute("UPDATE workflow_source_records SET state='active',canonical_key=? WHERE id=?", (key, rid))
        linked += 1
    if added or linked:
        refresh_statuses(conn)
        workflow.bump_catalog_revision(db, conn=conn)
    return {'activated_new': added, 'activated_source_records': linked}


def decide_candidate(conn: sqlite3.Connection, batch_id: str, cid: str, status: str,
                     actor: str, keeper_key: str | None, keeper_record_id: str | None) -> None:
    candidate = candidate_row(conn, batch_id, cid)
    members = candidate['members']
    record_ids = {m['ref_id'] for m in members if m['ref_type'] == 'record'}
    active_keys = {canonical(conn, m['ref_id']) for m in members if m['ref_type'] == 'active'}
    for rid in record_ids:
        resolved_key, _ = record_target(conn, rid)
        if resolved_key:
            active_keys.add(resolved_key)
    if candidate['status'] == status and candidate.get('keeper_key') == keeper_key and candidate.get('keeper_record_id') == keeper_record_id:
        return
    if any(conn.execute("SELECT 1 FROM workflow_source_records WHERE id=? AND state='active'", (rid,)).fetchone() for rid in record_ids):
        raise ValueError('This review already has activated records; its saved decision cannot be changed.')
    if candidate['status'] == 'duplicate':
        raise ValueError('A confirmed duplicate resolution is final; its keeper cannot be changed.')
    if status == 'duplicate':
        if bool(keeper_key) == bool(keeper_record_id):
            raise ValueError('Choose exactly one keeper_key or keeper_record_id.')
        if len(active_keys) > 1:
            raise ValueError('Merging existing active records could combine independent saved work. Keep them separate and review only new records.')
        if active_keys:
            if keeper_key not in active_keys or keeper_record_id:
                raise ValueError('Keep the existing active article so its saved work remains available.')
        elif keeper_key or keeper_record_id not in record_ids:
            raise ValueError('Keeper must be a staged member of this candidate.')
        if keeper_record_id:
            keeper = conn.execute('SELECT canonical_key,target_record_id FROM workflow_source_records WHERE id=?', (keeper_record_id,)).fetchone()
            if keeper[0] or keeper[1]:
                raise ValueError('Choose the independent canonical staged representative as keeper.')
        # Freeze all members (including keepers) of every previous confirmed
        # duplicate graph. Overlapping reviews may add new links to that graph,
        # but may not redirect any of its existing nodes through another keeper.
        frozen = {row[0] for row in conn.execute('''SELECT DISTINCT m.ref_id
            FROM workflow_import_candidate_members m JOIN workflow_import_candidates c ON c.id=m.candidate_id
            WHERE m.ref_type='record' AND c.status='duplicate' ''')}
        changes = []
        for rid in record_ids:
            if rid == keeper_record_id:
                continue
            current = conn.execute('SELECT canonical_key,target_record_id FROM workflow_source_records WHERE id=?', (rid,)).fetchone()
            intended = (keeper_key, keeper_record_id)
            if tuple(current) == intended:
                continue
            if rid in frozen:
                raise ValueError('This resolution would rewrite a previously confirmed duplicate relationship. Keep its existing keeper or leave this candidate unresolved.')
            changes.append(rid)
        # Validate the complete change set before writing any target or proposal.
        for rid in changes:
            conn.execute('UPDATE workflow_source_records SET canonical_key=?,target_record_id=?,outcome=? WHERE id=?',
                         (keeper_key, keeper_record_id, 'confirmed_duplicate', rid))
            if keeper_key:
                proposal(conn, rid, keeper_key)
    conn.execute('UPDATE workflow_import_candidates SET status=?,keeper_key=?,keeper_record_id=?,decided_by=?,updated_at=? WHERE id=?',
                 (status, keeper_key if status == 'duplicate' else None, keeper_record_id if status == 'duplicate' else None, actor, now(), cid))


def decide_metadata(conn: sqlite3.Connection, batch_id: str, rid: str, action: str, actor: str) -> None:
    if not conn.execute('SELECT 1 FROM workflow_import_members WHERE batch_id=? AND record_id=?', (batch_id, rid)).fetchone():
        raise KeyError('Source record not in this batch.')
    row = conn.execute('SELECT * FROM workflow_import_metadata WHERE record_id=?', (rid,)).fetchone()
    if row is None:
        raise KeyError('No metadata proposal for this source record.')
    if row['action'] == action:
        return
    if row['action'] is not None:
        raise ValueError('This metadata proposal has already been reviewed.')
    key = canonical(conn, row['canonical_key'])
    current = dict(conn.execute('SELECT * FROM articles WHERE zotero_key=?', (key,)).fetchone())
    incoming = json.loads(row['incoming_json'])
    if action == 'accept_incoming':
        if metadata(current) != metadata(json.loads(row['before_json'])):
            raise ValueError('Canonical metadata changed after this proposal. Keep the current metadata and submit a fresh proposal.')
        conn.execute('UPDATE articles SET ' + ','.join(f'{field}=?' for field in METADATA_FIELDS) + ' WHERE zotero_key=?',
                     [incoming.get(field) for field in METADATA_FIELDS] + [key])
        revision = conn.execute('SELECT catalog_revision FROM workflow_state WHERE singleton=1').fetchone()[0]
        conn.execute('''INSERT INTO workflow_metadata_acceptances VALUES(?,?,?,?) ON CONFLICT(zotero_key)
            DO UPDATE SET revision=excluded.revision,metadata_json=excluded.metadata_json,record_id=excluded.record_id''',
            (key, revision, dumps(metadata(incoming)), rid))
    conn.execute('UPDATE workflow_import_metadata SET action=?,accepted_json=?,decided_by=?,decided_at=? WHERE record_id=?',
                 (action, dumps(metadata(incoming) if action == 'accept_incoming' else metadata(current)), actor, now(), rid))


def batch_summary(conn: sqlite3.Connection, bid: str) -> dict:
    batch = conn.execute('SELECT * FROM workflow_import_batches WHERE id=?', (bid,)).fetchone()
    if batch is None:
        raise KeyError('Import batch not found.')
    records = conn.execute('''SELECT DISTINCT r.* FROM workflow_source_records r JOIN workflow_import_members m ON m.record_id=r.id WHERE m.batch_id=?''', (bid,)).fetchall()
    proposals = conn.execute('''SELECT p.action FROM workflow_import_metadata p WHERE p.record_id IN
                              (SELECT record_id FROM workflow_import_members WHERE batch_id=?)''', (bid,)).fetchall()
    candidates = candidate_rows(conn, bid, include_members=False)
    roots = {record_target(conn, r['id']) for r in records}
    # Current cumulative provenance counts, distinct from each activation call's deltas.
    # A retained source row can belong to later reimports; attribute new roots only
    # to their first batch, never count the same introduced article again.
    introduced = set()
    for row in records:
        if (row['state'] != 'active' or row['target_record_id'] is not None
                or row['outcome'] not in ('new', 'identity_conflict')
                or row['canonical_key'] not in (row['staged_key'], row['incoming_key'])):
            continue
        first = conn.execute('''SELECT b.id FROM workflow_import_members m
            JOIN workflow_import_batches b ON b.id=m.batch_id WHERE m.record_id=?
            ORDER BY b.created_at,b.rowid LIMIT 1''', (row['id'],)).fetchone()
        if first and first[0] == bid:
            introduced.add(row['canonical_key'])
    screening_total = conn.execute('''SELECT COUNT(*) FROM articles a WHERE a.is_duplicate_of IS NULL
        AND NOT EXISTS (SELECT 1 FROM title_duplicate_members m
        JOIN title_duplicate_reviews r ON r.review_id=m.review_id
        WHERE m.zotero_key=a.zotero_key AND m.imported_in_batch=1
        AND r.status IN ('pending','undecided'))''').fetchone()[0]
    return {**dict(batch), 'batch_id': bid, 'staged': any(r['state'] == 'staged' for r in records),
            'raw_source_records': len(records),
            'input_rows': conn.execute('SELECT COUNT(*) FROM workflow_import_members WHERE batch_id=?', (bid,)).fetchone()[0],
            'canonical_unique': len({key or root for key, root in roots}),
            'staged_new': len({root for key, root in roots if key is None}),
            'existing_source_links': sum(r['canonical_key'] is not None for r in records),
            'metadata_review_count': sum(p['action'] is None for p in proposals),
            'candidate_groups': len(candidates),
            'pending_candidate_groups': sum(c['status'] in ('pending', 'undecided') for c in candidates),
            'pending_records': sum(r['state'] == 'staged' for r in records),
            'active_records': sum(r['state'] == 'active' for r in records),
            'activated_new_total': len(introduced),
            'activated_source_records_total': sum(r['state'] == 'active' for r in records),
            'current_screening_total': screening_total,
            'files': [dict(f) for f in conn.execute('SELECT * FROM workflow_import_files WHERE batch_id=? ORDER BY file_index', (bid,))]}


def candidate_rows(conn: sqlite3.Connection, bid: str, *, include_members: bool = True,
                   limit: int | None = None, offset: int = 0, candidate_id: str | None = None) -> list[dict]:
    rows = conn.execute('''WITH RECURSIVE related(id) AS (
        SELECT record_id FROM workflow_import_members WHERE batch_id=?
        UNION SELECT r.target_record_id FROM workflow_source_records r JOIN related x ON r.id=x.id WHERE r.target_record_id IS NOT NULL)
        SELECT DISTINCT c.* FROM workflow_import_candidates c
        JOIN workflow_import_candidate_members cm ON cm.candidate_id=c.id AND cm.ref_type='record'
        JOIN related x ON x.id=cm.ref_id ORDER BY c.created_at,c.id''', (bid,)).fetchall()
    if candidate_id is not None:
        rows = [row for row in rows if row["id"] == candidate_id]
    if limit is not None:
        rows = rows[offset:offset + limit]
    if not include_members:
        return [dict(row) for row in rows]
    result = []
    for row in rows:
        members = []
        for member in conn.execute('SELECT * FROM workflow_import_candidate_members WHERE candidate_id=? ORDER BY ref_type,ref_id', (row['id'],)):
            data = dict(member)
            if member['ref_type'] == 'active':
                a = conn.execute('SELECT * FROM articles WHERE zotero_key=?', (member['ref_id'],)).fetchone()
                data['article'] = dict(a) if a else None
            else:
                a = conn.execute('SELECT * FROM workflow_source_records WHERE id=?', (member['ref_id'],)).fetchone()
                data['record'] = public_record(conn, dict(a)) if a else None
            members.append(data)
        result.append({**dict(row), 'review_id': row['id'], 'members': members})
    return result


def candidate_row(conn: sqlite3.Connection, bid: str, cid: str) -> dict:
    for row in candidate_rows(conn, bid, candidate_id=cid):
        if row['id'] == cid:
            return row
    raise KeyError('Candidate not in this batch.')


def public_record(conn: sqlite3.Connection, row: dict) -> dict:
    result = dict(row)
    result['record_id'] = row['id']
    key, root = record_target(conn, row['id'])
    result['resolved_canonical_key'] = key
    result['resolved_target_record_id'] = root
    result['row_locator'] = ('csv-row:' + str(row['row_index'] + 1) if json.loads(row['article_json']).get('source_format') == 'csv' else 'record:' + str(row['row_index']))
    for field in ('original_json', 'article_json'):
        result[field.removesuffix('_json')] = json.loads(result.pop(field))
    proposal_row = conn.execute('SELECT * FROM workflow_import_metadata WHERE record_id=?', (row['id'],)).fetchone()
    result['metadata'] = dict(proposal_row) if proposal_row else None
    if result['metadata']:
        for field in ('before_json', 'incoming_json', 'accepted_json'):
            value = result['metadata'].pop(field)
            result['metadata'][field.removesuffix('_json')] = json.loads(value) if value else None
    result['candidate_statuses'] = [dict(r) for r in conn.execute('''WITH RECURSIVE related(id) AS (SELECT ?
        UNION SELECT r.target_record_id FROM workflow_source_records r JOIN related x ON r.id=x.id WHERE r.target_record_id IS NOT NULL)
        SELECT DISTINCT c.id AS review_id,c.status FROM workflow_import_candidates c
        JOIN workflow_import_candidate_members m ON m.candidate_id=c.id
        JOIN related x ON x.id=m.ref_id WHERE m.ref_type='record' ''', (row['id'],))]
    return result
