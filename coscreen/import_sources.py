"""Import-derived provenance. Never change article metadata or research decisions."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from coscreen import review_workflow as workflow

VERSION = 4
SOURCE_TYPES = {'database', 'register', 'website', 'organisation', 'citation_searching', 'other'}
ALIASES = {'wos': 'Web of Science', 'web of science': 'Web of Science', 'scopus': 'Scopus',
           'pubmed': 'PubMed', 'medline': 'MEDLINE', 'eric': 'ERIC', 'proquest': 'ProQuest'}


def first(raw: dict, *keys: str) -> str:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, list):
            value = next((v for v in value if str(v).strip()), '')
        if isinstance(value, str) and value.strip():
            return value.strip()[:200]
    return ''


def detect(original: dict, filename: str, digest: str) -> dict:
    raw = original.get('raw', original.get('raw_json', original))
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = {}
    raw = raw if isinstance(raw, dict) else {}
    database = first(raw, 'name_of_database', 'DB')
    provider = first(raw, 'database_provider', 'DP')
    if database:
        name = ALIASES.get(workflow.normalized(database), database)
        return {'name': name, 'identity': 'database:' + workflow.normalized(name),
                'identification': 'metadata', 'source_type': 'database', 'provider': provider,
                'suggestion': '', 'evidence': {'field': 'DB', 'value': database}}
    # Filename matches are suggestions, never verified database provenance.
    stem = re.sub(r'[_\-.]+', ' ', Path(filename).stem).casefold()
    suggestion = next((label for alias, label in ALIASES.items()
                       if re.search(r'(?<!\w)' + re.escape(alias) + r'(?!\w)', stem)), '')
    return {'name': filename[:200], 'identity': 'file:' + digest,
            'identification': 'inferred' if suggestion else 'file', 'source_type': None,
            'provider': provider, 'suggestion': suggestion, 'evidence': {'filename': filename}}


def ensure(conn: sqlite3.Connection, detected: dict) -> str:
    known = conn.execute('SELECT source_id FROM workflow_source_details WHERE identity=? ORDER BY source_id LIMIT 1', (detected['identity'],)).fetchone()
    if known:
        return known[0]
    sid = str(uuid.uuid5(uuid.NAMESPACE_URL, 'reviewflow:source:' + detected['identity']))
    if conn.execute('SELECT 1 FROM workflow_sources WHERE id=?', (sid,)).fetchone():
        return sid
    # Reuse earlier explicitly named database sources; otherwise keep file identities separate.
    old = conn.execute('SELECT id FROM workflow_sources WHERE normalized_name=?',
                       (workflow.normalized(detected['name']),)).fetchone()
    if old and detected['identification'] == 'metadata':
        sid = old[0]
    elif not old:
        conn.execute("INSERT INTO workflow_sources VALUES(?,?,?,1,datetime('now'))",
                     (sid, detected['name'], workflow.normalized(detected['name'])))
    else:
        name = detected['name'][:180] + ' · ' + sid[:8]
        conn.execute("INSERT INTO workflow_sources VALUES(?,?,?,1,datetime('now'))",
                     (sid, name, workflow.normalized(name)))
    conn.execute('INSERT OR IGNORE INTO workflow_source_details VALUES(?,?,?,?,?,?,?)',
                 (sid, detected['identification'], detected['source_type'], detected['provider'],
                  detected['suggestion'], json.dumps(detected['evidence'], ensure_ascii=False), detected['identity']))
    return sid


def prepare_files(conn: sqlite3.Connection, files: list[dict]) -> None:
    for file in files:
        if file.get('source_id'):
            file['source_ids'] = [file['source_id']] * len(file['articles'])
        else:
            file['source_ids'] = [ensure(conn, detect(original, file['name'], file['digest']))
                                  for original in file['originals']]
            file['source_id'] = (file['source_ids'][0] if file['source_ids'] else
                                 ensure(conn, detect({}, file['name'], file['digest'])))


def migrate(db: str | Path) -> None:
    workflow.migrate(db)
    with closing(workflow.connect(db, read_only=True)) as conn:
        if conn.execute('SELECT 1 FROM workflow_migrations WHERE version=?', (VERSION,)).fetchone():
            return
        backup = Path(db).with_name(Path(db).name + '.pre-source-v4.bak')
        if not backup.exists():
            with closing(sqlite3.connect(backup)) as target:
                conn.backup(target)
    with closing(workflow.connect(db)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('''CREATE TABLE IF NOT EXISTS workflow_source_details(
            source_id TEXT PRIMARY KEY REFERENCES workflow_sources(id), identification TEXT NOT NULL,
            source_type TEXT, provider TEXT NOT NULL DEFAULT '', suggestion TEXT NOT NULL DEFAULT '',
            evidence_json TEXT NOT NULL, identity TEXT NOT NULL)''')
        recovered = set()
        # Original rows are immutable; only their source association is recovered.
        if workflow.has_table(conn, 'workflow_source_records'):
            for row in conn.execute('SELECT * FROM workflow_source_records WHERE source_id=?', (workflow.UNASSIGNED,)).fetchall():
                sid = ensure(conn, detect(json.loads(row['original_json']), row['filename'], row['digest']))
                collision = conn.execute('SELECT 1 FROM workflow_source_records WHERE source_id=? AND digest=? AND row_index=?',
                                         (sid, row['digest'], row['row_index'])).fetchone()
                if collision:
                    continue  # Never collapse retained originals or change candidate IDs.
                conn.execute('UPDATE workflow_source_records SET source_id=? WHERE id=?', (sid, row['id']))
                if row['state'] == 'active' and row['canonical_key']:
                    key, seen = row['canonical_key'], set()
                    while key not in seen:
                        seen.add(key)
                        article = conn.execute('SELECT is_duplicate_of FROM articles WHERE zotero_key=?', (key,)).fetchone()
                        if article is None:
                            break
                        if article[0] is None:
                            workflow.associate_sources(db, [key], [sid], conn=conn)
                            break
                        key = article[0]
                recovered.add(sid)
            # File-level identity is supplementary; records remain authoritative for mixed files.
            conn.execute('''UPDATE workflow_import_files SET source_id=COALESCE((
                SELECT r.source_id FROM workflow_import_members m JOIN workflow_source_records r ON r.id=m.record_id
                WHERE m.batch_id=workflow_import_files.batch_id AND m.file_index=workflow_import_files.file_index
                ORDER BY r.row_index LIMIT 1),source_id) WHERE source_id=?''', (workflow.UNASSIGNED,))
        if workflow.has_table(conn, 'articles'):
            for row in conn.execute('''SELECT a.zotero_key,a.raw_json FROM articles a
                JOIN workflow_article_sources s ON s.zotero_key=a.zotero_key WHERE s.source_id=?''', (workflow.UNASSIGNED,)).fetchall():
                try:
                    raw = json.loads(row['raw_json'] or '{}')
                except (TypeError, ValueError):
                    continue
                filename = first(raw, 'source_file')
                if not filename and not first(raw, 'name_of_database', 'DB'):
                    continue
                digest = hashlib.sha256(('legacy-file:' + filename).encode()).hexdigest()
                sid = ensure(conn, detect({'raw': raw}, filename or 'Legacy import', digest))
                # Legacy duplicate rows retain provenance but are not new work.
                conn.execute('INSERT OR IGNORE INTO workflow_article_sources VALUES(?,?)', (row['zotero_key'], sid))
                conn.execute('DELETE FROM workflow_article_sources WHERE zotero_key=? AND source_id=?', (row['zotero_key'], workflow.UNASSIGNED))
                key, seen = row['zotero_key'], set()
                while key not in seen:
                    seen.add(key)
                    article = conn.execute('SELECT is_duplicate_of FROM articles WHERE zotero_key=?', (key,)).fetchone()
                    if article is None:
                        break
                    if article[0] is None:
                        workflow.associate_sources(db, [key], [sid], conn=conn)
                        break
                    key = article[0]
                recovered.add(sid)
        # Recognize earlier owner-named sources only when every retained record's
        # database metadata agrees with that name. Never infer from the label alone.
        for source in conn.execute('SELECT id,name FROM workflow_sources WHERE id NOT IN (SELECT source_id FROM workflow_source_details)').fetchall():
            if source['id'] == workflow.UNASSIGNED:
                continue
            evidence = []
            if workflow.has_table(conn,'workflow_source_records'):
                for row in conn.execute('SELECT original_json,filename,digest FROM workflow_source_records WHERE source_id=?',(source['id'],)):
                    evidence.append(detect(json.loads(row['original_json']),row['filename'],row['digest']))
            if not evidence and workflow.has_table(conn,'articles'):
                for row in conn.execute('SELECT a.raw_json FROM articles a JOIN workflow_article_sources s ON s.zotero_key=a.zotero_key WHERE s.source_id=?',(source['id'],)):
                    evidence.append(detect({'raw_json':row['raw_json']},'',''))
            label = ALIASES.get(workflow.normalized(source['name']),source['name'])
            if evidence and all(e['source_type']=='database' and workflow.normalized(e['name'])==workflow.normalized(label) for e in evidence):
                detail = evidence[0]
                conn.execute('INSERT INTO workflow_source_details VALUES(?,?,?,?,?,?,?)',
                    (source['id'],'metadata','database',detail['provider'],'',json.dumps(detail['evidence'],ensure_ascii=False),detail['identity']))
        policy = json.loads(conn.execute('SELECT stages_json FROM workflow_state WHERE singleton=1').fetchone()[0])
        placeholder_used = conn.execute('SELECT 1 FROM workflow_article_sources WHERE source_id=? LIMIT 1', (workflow.UNASSIGNED,)).fetchone()
        if workflow.has_table(conn, 'workflow_source_records'):
            placeholder_used = placeholder_used or conn.execute('SELECT 1 FROM workflow_source_records WHERE source_id=? LIMIT 1', (workflow.UNASSIGNED,)).fetchone()
        for stage in policy.values():
            if workflow.UNASSIGNED in stage['source_ids']:
                stage['source_ids'] = sorted(set(stage['source_ids']) | recovered)
                if not placeholder_used:
                    stage['source_ids'].remove(workflow.UNASSIGNED)
        conn.execute('UPDATE workflow_state SET stages_json=?,version=version+1 WHERE singleton=1', (json.dumps(policy),))
        conn.execute("INSERT INTO workflow_migrations VALUES(?,datetime('now'))", (VERSION,))


def decorate(conn: sqlite3.Connection, sources: list[dict]) -> list[dict]:
    result = []
    policies = json.loads(conn.execute('SELECT stages_json FROM workflow_state WHERE singleton=1').fetchone()[0])
    referenced = {sid for stage in policies.values() for sid in stage['source_ids']}
    for source in sources:
        sid = source['id']
        links = conn.execute('SELECT COUNT(*) FROM workflow_article_sources WHERE source_id=?', (sid,)).fetchone()[0]
        originals, filenames = 0, set()
        if workflow.has_table(conn, 'workflow_source_records'):
            rows = conn.execute('SELECT filename FROM workflow_source_records WHERE source_id=?', (sid,)).fetchall()
            originals, filenames = len(rows), {r[0] for r in rows}
        if source['reserved'] and not links and not originals:
            continue
        if not source['reserved'] and not links and not originals and sid not in referenced:
            continue
        detail = conn.execute('SELECT * FROM workflow_source_details WHERE source_id=?', (sid,)).fetchone() if workflow.has_table(conn, 'workflow_source_details') else None
        active_links = conn.execute('SELECT COUNT(*) FROM workflow_article_sources s JOIN articles a ON a.zotero_key=s.zotero_key WHERE s.source_id=? AND a.is_duplicate_of IS NULL', (sid,)).fetchone()[0] if workflow.has_table(conn,'articles') else links
        result.append({**source, **(dict(detail) if detail else {}), 'name': 'Source not recorded' if source['reserved'] else source['name'],
                       'record_count': originals, 'linked_count': active_links, 'files': sorted(filenames),
                       'identification': detail['identification'] if detail else 'legacy'})
    return result
