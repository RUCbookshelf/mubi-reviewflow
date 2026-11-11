"""Owner-authoritative source provenance and protected import staging.

All mutations accept the caller's transaction; the API holds the workflow lock.
Source rows never stand in for active articles. Original upload payloads are
immutable; the staged ``article_json`` payload is the reviewable working copy and
may be completed by duplicate merge fill (field-level, trail-logged).
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from coscreen import db as articles_db, review_workflow as workflow
from coscreen.dedup import (
    _identity_doi, _key_merge_confirmed, _norm_doi, find_duplicates, is_journal_pattern_doi,
)
from coscreen.models import Article

#: 迁移版本号在所有写 workflow_migrations 的模块之间必须**全局唯一**（共用同一
#: 张表）。已占用：1 workflow、2 本模块（G5 之前）、3 import_review_undo、
#: 4/5 import_sources、6 screening_rounds、7 coding_evidence、8 binary_reset。
#: G5 的字段留痕表取 9——绝不能复用 3（曾误用，遗留库会因 3 已存在而早退、
#: 永不建新表，首次确认重复即 500）。
MIGRATION_VERSION = 9
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
CREATE TABLE IF NOT EXISTS workflow_import_field_merges(
 id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
 keeper_kind TEXT NOT NULL, keeper_ref TEXT NOT NULL, field TEXT NOT NULL,
 before_value TEXT, after_value TEXT, source_record_id TEXT,
 decision_source TEXT NOT NULL, applied INTEGER NOT NULL DEFAULT 1,
 conflict INTEGER NOT NULL DEFAULT 0, ai_suggestion_id TEXT,
 actor_id TEXT NOT NULL, created_at TEXT NOT NULL, undone INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS workflow_field_merges_candidate ON workflow_import_field_merges(candidate_id,undone);
'''


# ---------------------------------------------------------------------------
# 合并质量（2.1.5 G5）：空值/占位串、完整度、通用与过短标题
#
# 单一事实源：判据（bulk eligibility）、建议保留者（suggested_keeper）与只补空
# 都只读这里的定义。任何一处改动必须同步契约
# （frontend_contract_2.1.5-g5-merge-rules.md）。
# ---------------------------------------------------------------------------

#: 保守占位串（NFKC + casefold + strip 后逐字比对）。命中即按「空」处理：
#: 只补空不会把占位串当成真实值，也不会把真实值覆盖成占位串。
BLANK_PLACEHOLDERS = frozenset({
    '[no abstract available]', '[no abstract]', '[abstract not available]',
    'no abstract available', 'n.d.', 'n.d', 'none', 'null', 'n/a', 'na',
    '-', '--', '—', '…',
})

#: 完整度字典序（规则②）：身份 DOI 有无 > 摘要 > 期刊 > 年份 > 作者 > 标题。
COMPLETENESS_FIELDS = ('doi', 'abstract', 'journal', 'year', 'authors', 'title')
COMPLETENESS_REASONS = ('has_doi', 'has_abstract', 'has_journal', 'has_year',
                        'has_authors', 'has_title')

#: 通用标题（英文，规范化后整串相等）；前缀项按规范化前缀匹配。
GENERIC_TITLE_EN = (
    'editorial', 'introduction', 'preface', 'foreword', 'erratum', 'corrigendum',
    'correction', 'retraction', 'book review', 'review', 'commentary', 'comment',
    'letter', 'reply', 'response', 'announcement', 'obituary', 'index', 'contents',
)
GENERIC_TITLE_PREFIXES_EN = (
    'introduction to the special issue', 'guest editorial', "editor's note",
)
#: 通用标题（中文，规范化后整串相等）。
GENERIC_TITLE_ZH = ('编者按', '卷首语', '前言', '导言', '书评', '勘误', '更正', '启事', '目录')

_CJK_RE = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]')


def _fold(value) -> str:
    if value is None:
        return ''
    return unicodedata.normalize('NFKC', str(value)).strip().casefold()


def is_blank(value) -> bool:
    """字段是否按「空」处理：NULL、空串、仅空白、以及保守占位串。"""
    text = _fold(value)
    return not text or text in BLANK_PLACEHOLDERS


def normalize_title(value) -> str:
    """题名规范化（与 identity_key 的题名段同一算法）：NFKC → casefold →
    标点/符号折叠为空格 → 压缩空白。"""
    text = unicodedata.normalize('NFKC', str(value or '')).casefold()
    text = ''.join(' ' if unicodedata.category(ch).startswith(('P', 'S')) else ch
                   for ch in text)
    return ' '.join(text.split())


_GENERIC_EXACT = frozenset(
    normalize_title(title) for title in GENERIC_TITLE_EN + GENERIC_TITLE_ZH)
_GENERIC_PREFIXES = tuple(
    normalize_title(title) for title in GENERIC_TITLE_PREFIXES_EN)


def is_generic_title(normalized_title: str) -> bool:
    """通用标题：规范化后整串命中清单，或以三个前缀之一开头。"""
    return (normalized_title in _GENERIC_EXACT
            or any(normalized_title.startswith(prefix) for prefix in _GENERIC_PREFIXES))


def is_short_title(normalized_title: str) -> bool:
    """规范化后过短：含 CJK 时按字数（< 6 字），否则按词数（< 4 词）。"""
    cjk = len(_CJK_RE.findall(normalized_title))
    if cjk:
        return cjk < 6
    return len(normalized_title.split()) < 4


def completeness(article: dict) -> tuple[int, ...]:
    """规则②完整度分（字典序比较，越大越完整）。"""
    article = article or {}
    scores = []
    for field in COMPLETENESS_FIELDS:
        if field == 'doi':
            scores.append(1 if _identity_doi(article.get('doi') or '') else 0)
        else:
            scores.append(0 if is_blank(article.get(field)) else 1)
    return tuple(scores)


def completeness_reasons(article: dict) -> list[str]:
    return [key for key, present in zip(COMPLETENESS_REASONS, completeness(article))
            if present]


class MergeError(ValueError):
    """带结构化 message_key 的合并/元数据错误（API 层译成 JSON 详情）。"""

    def __init__(self, message: str, message_key: str, *, params: dict | None = None,
                 status: int = 409):
        super().__init__(message)
        self.message_key = message_key
        self.params = dict(params or {})
        self.status = status



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
                # 表名来自模块内固定枚举 work_tables → 逐条字面量选择。
                if table == 'decisions':
                    hit = other.execute('SELECT 1 FROM "decisions" LIMIT 1').fetchone()
                elif table == 'stage2_decisions':
                    hit = other.execute('SELECT 1 FROM "stage2_decisions" LIMIT 1').fetchone()
                elif table == 'coding_values':
                    hit = other.execute('SELECT 1 FROM "coding_values" LIMIT 1').fetchone()
                elif table == 'coding_notes':
                    hit = other.execute('SELECT 1 FROM "coding_notes" LIMIT 1').fetchone()
                elif table == 'coding_note_images':
                    hit = other.execute('SELECT 1 FROM "coding_note_images" LIMIT 1').fetchone()
                elif table == 'highlights':
                    hit = other.execute('SELECT 1 FROM "highlights" LIMIT 1').fetchone()
                elif table == 'coding_rater_records':
                    hit = other.execute('SELECT 1 FROM "coding_rater_records" LIMIT 1').fetchone()
                elif table == 'coding_consensus_records':
                    hit = other.execute('SELECT 1 FROM "coding_consensus_records" LIMIT 1').fetchone()
                elif table == 'coding_freezes':
                    hit = other.execute('SELECT 1 FROM "coding_freezes" LIMIT 1').fetchone()
                elif table == 'workflow_coding_evidence':
                    hit = other.execute('SELECT 1 FROM "workflow_coding_evidence" LIMIT 1').fetchone()
                elif table == 'coding_evidence':
                    hit = other.execute('SELECT 1 FROM "coding_evidence" LIMIT 1').fetchone()
                elif table == 'workspace_undo':
                    hit = other.execute('SELECT 1 FROM "workspace_undo" LIMIT 1').fetchone()
                else:
                    hit = None
                if hit:
                    conn.execute('UPDATE workflow_import_state SET researched=1 WHERE singleton=1')
                    return True
            if 'trace_events' in tables and other.execute('''SELECT 1 FROM trace_events WHERE
                stage IN ('screen','fulltext','coding') AND action IN
                ('decision_created','decision_changed','decision_imported','decision_undone',
                 'coding_value_created','coding_value_changed','coding_value_cleared','coding_values_undone',
                 'coding_note_created','coding_note_changed','coding_note_cleared','coding_note_image_added',
                 'coding_note_image_removed','coding_evidence_undone') LIMIT 1''').fetchone():
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
        # 期刊样式 DOI（如 ERIC 的 10.1002/(issn)…）是整刊标识，不能把
        # 不同文献链到同一活动行（r17：否则同刊不同文献被配成 exact_doi，
        # 再以「信息差异」要求人工核验）。
        if doi and not is_journal_pattern_doi(doi):
            by_doi.setdefault(doi, canonical(conn, row['zotero_key']))
    pending = [dict(r) for r in conn.execute("SELECT * FROM workflow_source_records ORDER BY created_at,id")]
    pending_keys, pending_dois = {}, {}
    for r in pending:
        pending_keys.setdefault(r['incoming_key'], []).append(r)
        doi = _norm_doi(json.loads(r['article_json']).get('doi'))
        if doi and not is_journal_pattern_doi(doi):
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
            # DOI 身份键：期刊样式 DOI 视同缺 DOI（r17）。
            doi_identity = '' if is_journal_pattern_doi(doi) else doi
            match_key, target, outcome = None, None, 'new'
            key_row = by_key.get(key)
            key_priors = pending_keys.get(key, [])
            key_refs = set()
            conflicting = False
            if key_row:
                key_refs.add(('active', canonical(conn, key)))
                # U1-P3（web）：同键链接前校验 DOI/标题冲突，与去重层
                # _key_merge_confirmed 同一谓词——双方 DOI 均非空以 DOI
                # 裁决；否则标题 token_set_ratio<90 即冲突。DOI 均为空的
                # 同键不同文献不再静默走 exact_key，改报 identity_conflict。
                conflicting = not _key_merge_confirmed(Article.from_dict(key_row), art)
            # Inspect every retained external-key alias before taking a DOI
            # shortcut. An alias may already point at an active canonical row
            # whose physical key differs from the uploaded source key.
            for prior_row in key_priors:
                conflicting = conflicting or not _key_merge_confirmed(
                    Article.from_dict(json.loads(prior_row['article_json'])), art)
                prior_key, prior_root = record_target(conn, prior_row['id'])
                key_refs.add(('active', prior_key) if prior_key else ('record', prior_root))
            prior = pending_dois.get(doi_identity) if doi_identity else None
            doi_ref = None
            if doi_identity and doi_identity in by_doi:
                doi_ref = ('active', by_doi[doi_identity])
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
            if doi_identity:
                pending_dois.setdefault(doi_identity, r)
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


def _occupied_identity_dois(conn: sqlite3.Connection, *, exclude_key: str | None = None) -> dict:
    """任务内已入任务文献的身份 DOI → zotero_key（撞号守卫用）。"""
    occupied = {}
    for row in conn.execute(
            "SELECT zotero_key,doi FROM articles "
            "WHERE is_duplicate_of IS NULL AND doi IS NOT NULL AND doi!=''"):
        if exclude_key is not None and row['zotero_key'] == exclude_key:
            continue
        ident = _identity_doi(row['doi'])
        if ident:
            occupied.setdefault(ident, row['zotero_key'])
    return occupied


def _doi_block_reason(value, occupied: dict) -> str | None:
    """待写入 DOI 的守卫结论：期刊样式 / 已被另一篇已入任务文献占用。"""
    if is_journal_pattern_doi(value or ''):
        return 'journal_pattern'
    ident = _identity_doi(value or '')
    return 'occupied' if ident and ident in occupied else None


def _doi_collision(field: str, value, reason: str, occupied: dict) -> dict:
    entry = {'field': field, 'doi': value, 'reason': reason}
    if reason == 'occupied':
        entry['occupied_by'] = occupied.get(_identity_doi(value or ''))
    return entry


def _write_articles(conn: sqlite3.Connection, key: str, meta: dict) -> None:
    conn.execute(
        'UPDATE articles SET item_type=?,title=?,authors=?,journal=?,year=?,doi=?,'
        'abstract=?,url=? WHERE zotero_key=?',
        [meta.get(field) for field in METADATA_FIELDS] + [key])


def _log_field_merge(conn, *, batch_id, candidate_id, keeper_kind, keeper_ref, field,
                     before_value, after_value, source_record_id, decision_source,
                     applied, conflict, ai_suggestion_id, actor) -> None:
    conn.execute(
        'INSERT INTO workflow_import_field_merges(batch_id,candidate_id,keeper_kind,'
        'keeper_ref,field,before_value,after_value,source_record_id,decision_source,'
        'applied,conflict,ai_suggestion_id,actor_id,created_at) '
        'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (batch_id, candidate_id, keeper_kind, keeper_ref, field, before_value,
         after_value, source_record_id, decision_source, 1 if applied else 0,
         1 if conflict else 0, ai_suggestion_id, actor, now()))


def apply_keeper_fill(conn: sqlite3.Connection, *, batch_id: str, candidate_id: str,
                      actor: str, keeper_kind: str, keeper_ref: str,
                      keeper_meta: dict, merged: list[dict],
                      conflict_picks: dict | None = None,
                      ai_suggestion_id: str | None = None) -> dict:
    """只补空 + 冲突选择 + DOI 守卫 + 逐字段留痕（保留条目为已入任务文献或待审记录）。

    ``keeper_meta`` 是操作前的保留条目题录（规划依据）；落笔前逐字段复核当前值
    （仍为空 / 仍等于操作前值），不满足则该字段跳过并报告，绝不覆盖他人改动。
    """
    summary = {'articles': 0, 'fields': {}, 'doi_collision': [], 'skipped': []}
    merged = [m for m in (merged or []) if m.get('record_id')]
    member_ids = {m['record_id'] for m in merged}
    picks = dict(conflict_picks or {})
    for field, pick in picks.items():
        if field not in METADATA_FIELDS:
            raise MergeError('Unknown metadata field in conflict_picks: %s' % field,
                             'import_merge.conflict_pick_invalid_field',
                             params={'field': field}, status=400)
        if pick != 'keeper' and pick not in member_ids:
            raise MergeError(
                'conflict_picks must reference the keeper or a merged record of this group.',
                'import_merge.conflict_pick_invalid_member',
                params={'field': field}, status=400)
    if not merged:
        return summary
    # 多被合并者：完整度降序 + 原顺序（稳定排序）决定取值优先级。
    ordered = sorted(merged, key=lambda item: completeness(item.get('article') or {}),
                     reverse=True)
    occupied = _occupied_identity_dois(
        conn, exclude_key=keeper_ref if keeper_kind == 'active' else None)
    writes, trail = [], []
    for field in METADATA_FIELDS:
        keeper_value = keeper_meta.get(field)
        if is_blank(keeper_value):
            chosen = next((item for item in ordered
                           if not is_blank((item.get('article') or {}).get(field))), None)
            if chosen is None:
                continue
            value = (chosen.get('article') or {}).get(field)
            if field == 'doi':
                reason = _doi_block_reason(value, occupied)
                if reason:
                    summary['doi_collision'].append(_doi_collision(field, value, reason, occupied))
                    continue
            distinct = {(item.get('article') or {}).get(field) for item in ordered
                        if not is_blank((item.get('article') or {}).get(field))}
            writes.append({'field': field, 'value': value,
                           'source_record_id': chosen['record_id'],
                           'decision_source': 'rule', 'conflict': len(distinct) > 1})
            for other in ordered:
                if other is chosen:
                    continue
                other_value = (other.get('article') or {}).get(field)
                if is_blank(other_value) or other_value == value:
                    continue
                trail.append({'field': field, 'value': None,
                              'source_record_id': other['record_id'],
                              'decision_source': 'rule', 'conflict': True})
            continue
        pick = picks.get(field)
        if not pick or pick == 'keeper':
            continue
        member = next(item for item in ordered if item['record_id'] == pick)
        value = (member.get('article') or {}).get(field)
        if is_blank(value):
            continue
        if field == 'doi':
            reason = _doi_block_reason(value, occupied)
            if reason:
                summary['doi_collision'].append(_doi_collision(field, value, reason, occupied))
                continue
        writes.append({'field': field, 'value': value, 'source_record_id': pick,
                       'decision_source': 'ai_prefill' if ai_suggestion_id else 'human',
                       'conflict': True})
    if not writes:
        return summary
    if keeper_kind == 'active':
        row = conn.execute('SELECT * FROM articles WHERE zotero_key=?', (keeper_ref,)).fetchone()
        if row is None:
            raise KeyError('Keeper article not found.')
        current = dict(row)
    else:
        row = conn.execute('SELECT article_json FROM workflow_source_records WHERE id=?',
                           (keeper_ref,)).fetchone()
        if row is None:
            raise KeyError('Keeper source record not found.')
        current = json.loads(row[0])
    written = dict(current)
    applied_fields = set()
    for write in writes:
        now_value = current.get(write['field'])
        unchanged = (is_blank(now_value) if write['decision_source'] == 'rule'
                     else now_value == keeper_meta.get(write['field']))
        if not unchanged:
            summary['skipped'].append({'field': write['field'], 'reason': 'changed_by_other'})
            continue
        written[write['field']] = write['value']
        applied_fields.add(write['field'])
        summary['fields'][write['field']] = summary['fields'].get(write['field'], 0) + 1
    if applied_fields:
        if keeper_kind == 'active':
            _write_articles(conn, keeper_ref, written)
            # 终审 #22（用户 2026-11-09 裁定）：补空改写属主库 articles 后必须
            # 同步发布 metadata acceptance（与 decide_metadata 两条写路径同口径，
            # revision 取当前 catalog_revision——articles 的 AFTER UPDATE 触发器
            # 已在上一句内把目录版本抬起）。否则补空之后才首次同步目录的成员
            # （新受邀/成员库重建）会被旧 acceptance 把补全字段冲回 NULL，且被记
            # 为已应用该 revision。待审记录保留者尚无 articles 行、acceptance 无
            # 从挂键：其补全随激活后的 articles 行走正常目录同步，不在此发布。
            first_source = next(write['source_record_id'] for write in writes
                                if write['field'] in applied_fields)
            _publish_metadata_acceptance(conn, keeper_ref, first_source, written)
        else:
            conn.execute('UPDATE workflow_source_records SET article_json=? WHERE id=?',
                         (dumps(written), keeper_ref))
        summary['articles'] = 1
        for write in writes:
            if write['field'] not in applied_fields:
                continue
            _log_field_merge(
                conn, batch_id=batch_id, candidate_id=candidate_id,
                keeper_kind=keeper_kind, keeper_ref=keeper_ref, field=write['field'],
                before_value=keeper_meta.get(write['field']), after_value=write['value'],
                source_record_id=write['source_record_id'],
                decision_source=write['decision_source'], applied=True,
                conflict=write['conflict'], ai_suggestion_id=ai_suggestion_id, actor=actor)
            for other in trail:
                if other['field'] != write['field']:
                    continue
                _log_field_merge(
                    conn, batch_id=batch_id, candidate_id=candidate_id,
                    keeper_kind=keeper_kind, keeper_ref=keeper_ref, field=other['field'],
                    before_value=keeper_meta.get(other['field']), after_value=None,
                    source_record_id=other['source_record_id'],
                    decision_source=other['decision_source'], applied=False,
                    conflict=other['conflict'], ai_suggestion_id=ai_suggestion_id,
                    actor=actor)
    return summary


def _candidate_merged_members(conn: sqlite3.Connection, members: list[dict],
                              keeper_record_id: str | None) -> list[dict]:
    """被合并成员（保留者之外的 record 成员），按导入顺序（原顺序）排列。"""
    merged = []
    for member in members:
        if member['ref_type'] != 'record' or member['ref_id'] == keeper_record_id:
            continue
        row = conn.execute(
            'SELECT article_json,created_at,row_index FROM workflow_source_records WHERE id=?',
            (member['ref_id'],)).fetchone()
        if row is None:
            continue
        merged.append({'record_id': member['ref_id'],
                       'article': json.loads(row['article_json']),
                       '_order': (str(row['created_at'] or ''), int(row['row_index'] or 0),
                                  member['ref_id'])})
    merged.sort(key=lambda item: item['_order'])
    for item in merged:
        item.pop('_order', None)
    return merged


def decide_candidate(conn: sqlite3.Connection, batch_id: str, cid: str, status: str,
                     actor: str, keeper_key: str | None, keeper_record_id: str | None,
                     *, conflict_picks: dict | None = None,
                     ai_suggestion_id: str | None = None) -> dict | None:
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
    fill = None
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
        #
        # 只补空（B3）：先对保留条目做补空/冲突选择（含 DOI 守卫与逐字段留痕），
        # 再写目标指针——这样随后 proposal() 比较的是补空后的保留条目，不会为
        # 已被补入的同一值生成多余的「信息差异」提案。
        if keeper_key:
            keeper_ref = canonical(conn, keeper_key)
            keeper_row = conn.execute('SELECT * FROM articles WHERE zotero_key=?',
                                      (keeper_ref,)).fetchone()
            if keeper_row is None:
                raise KeyError('Keeper article not found.')
            keeper_meta, keeper_kind = dict(keeper_row), 'active'
        else:
            keeper_row = conn.execute('SELECT article_json FROM workflow_source_records WHERE id=?',
                                      (keeper_record_id,)).fetchone()
            if keeper_row is None:
                raise KeyError('Keeper source record not found.')
            keeper_meta, keeper_kind, keeper_ref = json.loads(keeper_row[0]), 'record', keeper_record_id
        fill = apply_keeper_fill(
            conn, batch_id=batch_id, candidate_id=cid, actor=actor,
            keeper_kind=keeper_kind, keeper_ref=keeper_ref, keeper_meta=keeper_meta,
            merged=_candidate_merged_members(conn, members, keeper_record_id),
            conflict_picks=conflict_picks, ai_suggestion_id=ai_suggestion_id)
        for rid in changes:
            conn.execute('UPDATE workflow_source_records SET canonical_key=?,target_record_id=?,outcome=? WHERE id=?',
                         (keeper_key, keeper_record_id, 'confirmed_duplicate', rid))
            if keeper_key:
                proposal(conn, rid, keeper_key)
    conn.execute('UPDATE workflow_import_candidates SET status=?,keeper_key=?,keeper_record_id=?,decided_by=?,updated_at=? WHERE id=?',
                 (status, keeper_key if status == 'duplicate' else None, keeper_record_id if status == 'duplicate' else None, actor, now(), cid))
    if status == 'duplicate' and fill and any(
            fill.get(key) for key in ('articles', 'fields', 'doi_collision', 'skipped')):
        return {'merge_fill': fill}
    return None


def _metadata_doi_guard(conn: sqlite3.Connection, key: str, current_value,
                        incoming_value) -> tuple[str | None, str | None]:
    """信息差异采纳时的 DOI 守卫：返回（原因, 占用方 zotero_key）。"""
    if is_journal_pattern_doi(incoming_value or ''):
        return 'journal_pattern', None
    ident = _identity_doi(incoming_value or '')
    if not ident or ident == _identity_doi(current_value or ''):
        return None, None
    occupied = _occupied_identity_dois(conn, exclude_key=key)
    if ident in occupied:
        return 'occupied', occupied.get(ident)
    return None, None


def _publish_metadata_acceptance(conn: sqlite3.Connection, key: str, rid: str, meta: dict) -> None:
    revision = conn.execute(
        'SELECT catalog_revision FROM workflow_state WHERE singleton=1').fetchone()[0]
    conn.execute('''INSERT INTO workflow_metadata_acceptances VALUES(?,?,?,?) ON CONFLICT(zotero_key)
        DO UPDATE SET revision=excluded.revision,metadata_json=excluded.metadata_json,record_id=excluded.record_id''',
        (key, revision, dumps(metadata(meta)), rid))


def decide_metadata(conn: sqlite3.Connection, batch_id: str, rid: str, action: str, actor: str,
                    *, picks: dict | None = None) -> dict | None:
    if not conn.execute('SELECT 1 FROM workflow_import_members WHERE batch_id=? AND record_id=?', (batch_id, rid)).fetchone():
        raise KeyError('Source record not in this batch.')
    row = conn.execute('SELECT * FROM workflow_import_metadata WHERE record_id=?', (rid,)).fetchone()
    if row is None:
        raise KeyError('No metadata proposal for this source record.')
    if row['action'] == action:
        # 终审 #26（用户 2026-11-09 裁定）：同 action 重复提交 = 幂等 noop——
        # 不报错、不写库（policy_version 不增、不记撤销），但响应携带结构化
        # 标记 metadata_noop=True，前端据此提示「上次选择未变更」。不同 action
        # 仍走下方「already reviewed」409（现状不变）。
        return {'metadata_noop': True}
    if row['action'] is not None:
        raise ValueError('This metadata proposal has already been reviewed.')
    key = canonical(conn, row['canonical_key'])
    current = dict(conn.execute('SELECT * FROM articles WHERE zotero_key=?', (key,)).fetchone())
    incoming = json.loads(row['incoming_json'])
    collision = []
    if action == 'accept_incoming':
        if metadata(current) != metadata(json.loads(row['before_json'])):
            raise ValueError('Canonical metadata changed after this proposal. Keep the current metadata and submit a fresh proposal.')
        written = dict(current)
        for field in METADATA_FIELDS:
            value = incoming.get(field)
            if field == 'doi':
                reason, occupied_by = _metadata_doi_guard(conn, key, current.get('doi'), value)
                if reason:
                    collision.append(_doi_collision(
                        field, value, reason,
                        {_identity_doi(value or ''): occupied_by} if occupied_by else {}))
                    continue
            written[field] = value
        _write_articles(conn, key, written)
        _publish_metadata_acceptance(conn, key, rid, written)
    elif action == 'field_picks':
        if metadata(current) != metadata(json.loads(row['before_json'])):
            raise ValueError('Canonical metadata changed after this proposal. Keep the current metadata and submit a fresh proposal.')
        picks = dict(picks or {})
        for field in picks:
            if field not in METADATA_FIELDS:
                raise MergeError('Unknown metadata field in picks: %s' % field,
                                 'import_merge.metadata_pick_invalid_field',
                                 params={'field': field}, status=400)
        written = dict(current)
        for field in METADATA_FIELDS:
            if picks.get(field) != 'incoming':
                continue
            value = incoming.get(field)
            if field == 'doi':
                reason, occupied_by = _metadata_doi_guard(conn, key, current.get('doi'), value)
                if reason:
                    collision.append(_doi_collision(
                        field, value, reason,
                        {_identity_doi(value or ''): occupied_by} if occupied_by else {}))
                    continue
            written[field] = value
        _write_articles(conn, key, written)
        _publish_metadata_acceptance(conn, key, rid, written)
    else:
        written = current
    conn.execute('UPDATE workflow_import_metadata SET action=?,accepted_json=?,decided_by=?,decided_at=? WHERE record_id=?',
                 (action, dumps(metadata(written)), actor, now(), rid))
    return {'doi_collision': collision} if collision else None


def batch_summary(conn: sqlite3.Connection, bid: str) -> dict:
    batch = conn.execute('SELECT * FROM workflow_import_batches WHERE id=?', (bid,)).fetchone()
    if batch is None:
        raise KeyError('Import batch not found.')
    records = conn.execute('''SELECT DISTINCT r.* FROM workflow_source_records r JOIN workflow_import_members m ON m.record_id=r.id WHERE m.batch_id=?''', (bid,)).fetchall()
    proposals = conn.execute('''SELECT p.action FROM workflow_import_metadata p WHERE p.record_id IN
                              (SELECT record_id FROM workflow_import_members WHERE batch_id=?)''', (bid,)).fetchall()
    candidates = candidate_rows(conn, bid, include_members=False)
    # perf 段7·run3：原实现对每条记录逐条调 record_target()/首批次查询
    # （本基准 126 条 ≈ 330 次 SQL）。改为批量预取 + 内存解析，链式语义
    # （canonical 别名链、target 链、环检测、ValueError 文案）与
    # record_target/canonical 逐位一致，由基准门禁哈希与测试钉。
    _chain = {r['id']: (r['canonical_key'], r['target_record_id']) for r in records}

    def _row(rid: str):
        if rid not in _chain:
            fetched = conn.execute(
                'SELECT canonical_key,target_record_id FROM workflow_source_records WHERE id=?',
                (rid,)).fetchone()
            if fetched is None:
                raise ValueError('Missing source record target.')
            _chain[rid] = (fetched[0], fetched[1])
        return _chain[rid]

    _dup_of: dict[str, str | None] = {}

    def _canon(key: str) -> str:
        seen = set()
        while key not in seen:
            seen.add(key)
            if key not in _dup_of:
                row = conn.execute('SELECT is_duplicate_of FROM articles WHERE zotero_key=?',
                                   (key,)).fetchone()
                if row is None:
                    raise ValueError('Canonical article no longer exists; refresh the import review.')
                _dup_of[key] = row[0]
            nxt = _dup_of[key]
            if nxt is None:
                return key
            key = nxt
        raise ValueError('Existing duplicate identity cycle requires manual repair.')

    def _target(rid: str) -> tuple[str | None, str]:
        seen = set()
        while rid not in seen:
            seen.add(rid)
            key, nxt = _row(rid)
            if key:
                return _canon(key), rid
            if not nxt:
                return None, rid
            rid = nxt
        raise ValueError('Source record target cycle requires review.')

    roots = {_target(r['id']) for r in records}
    # Current cumulative provenance counts, distinct from each activation call's deltas.
    # A retained source row can belong to later reimports; attribute new roots only
    # to their first batch, never count the same introduced article again.
    # 首批次：按**本批成员的 record_id 集**收域的 JOIN 预取（record_id → 按
    # (created_at,rowid) 字典序最小的 batch_id），替代逐记录 LIMIT 1 查询；
    # 取小序与 SQL 的 ORDER BY 语义一致。sec29 收域后语义不变（归属判定只
    # 依赖本批 record 的成员行）。sec29b（复审#4）口径修正：无 record_id
    # 索引时 SQLite 计划外层仍是 SCAN m（配子查询物化 + Bloom filter）——
    # 收域收紧的是**候选集与语义保证**，并非消除全表扫描本身；members 表
    # 巨大的任务可再补 record_id 索引收量级。
    _first_batch: dict[str, str] = {}
    for member_id, batch_id, created, rowid in conn.execute(
            '''SELECT m.record_id, b.id, b.created_at, b.rowid FROM workflow_import_members m
               JOIN workflow_import_batches b ON b.id=m.batch_id
               WHERE m.record_id IN
                   (SELECT record_id FROM workflow_import_members WHERE batch_id=?)''',
            (bid,)):
        held = _first_batch.get(member_id)
        if held is None or (created, rowid) < held[0]:
            _first_batch[member_id] = ((created, rowid), batch_id)
    _first_batch = {rid: pair[1] for rid, pair in _first_batch.items()}
    introduced = set()
    for row in records:
        if (row['state'] != 'active' or row['target_record_id'] is not None
                or row['outcome'] not in ('new', 'identity_conflict')
                or row['canonical_key'] not in (row['staged_key'], row['incoming_key'])):
            continue
        if _first_batch.get(row['id']) == bid:
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
