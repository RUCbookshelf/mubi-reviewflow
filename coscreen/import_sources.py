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
    # Source identity is the complete uploaded filename, never RIS DB/DP metadata.
    name = Path(filename.replace('\\', '/')).name or 'Source not recorded'
    return {'name': name, 'identity': 'filename:' + workflow.normalized(name),
            'identification': 'file', 'source_type': None, 'provider': '',
            'suggestion': '', 'evidence': {'filename': name}}


def ensure(conn: sqlite3.Connection, detected: dict) -> str:
    known = conn.execute('SELECT source_id FROM workflow_source_details WHERE identity=? ORDER BY source_id LIMIT 1', (detected['identity'],)).fetchone()
    named = conn.execute('SELECT id FROM workflow_sources WHERE normalized_name=?', (workflow.normalized(detected['name']),)).fetchone()
    sid = known[0] if known else named[0] if named else str(uuid.uuid5(uuid.NAMESPACE_URL, 'reviewflow:source:' + detected['identity']))
    conn.execute("INSERT OR IGNORE INTO workflow_sources VALUES(?,?,?,1,datetime('now'))",(sid,detected['name'],workflow.normalized(detected['name'])))
    conn.execute('UPDATE workflow_sources SET name=?,normalized_name=? WHERE id=?',(detected['name'],workflow.normalized(detected['name']),sid))
    conn.execute("""INSERT INTO workflow_source_details VALUES(?,?,?,?,?,?,?)
                 ON CONFLICT(source_id) DO UPDATE SET identification=CASE WHEN identity=excluded.identity AND identification='confirmed' THEN 'confirmed' ELSE 'file' END,identity=excluded.identity,
                 evidence_json=excluded.evidence_json,provider='',suggestion='' """,
                 (sid, detected['identification'], detected['source_type'], detected['provider'],
                  detected['suggestion'], json.dumps(detected['evidence'], ensure_ascii=False), detected['identity']))
    return sid


def prepare_files(conn: sqlite3.Connection, files: list[dict]) -> None:
    for file in files:
        sid=ensure(conn,detect({},file['name'],file['digest']))
        file['source_ids']=[sid]*len(file['articles'])
        file['source_id']=sid


def _migrate_v4(db: str | Path) -> None:
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
                if not filename:
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
        links = conn.execute('SELECT COUNT(*) FROM workflow_article_sources s JOIN articles a ON a.zotero_key=s.zotero_key WHERE s.source_id=? AND a.is_duplicate_of IS NULL', (sid,)).fetchone()[0] if workflow.has_table(conn,'articles') else conn.execute('SELECT COUNT(*) FROM workflow_article_sources WHERE source_id=?',(sid,)).fetchone()[0]
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


def migrate(db: str | Path) -> None:
    """Upgrade filename associations without rewriting original records or research."""
    _migrate_v4(db)
    with closing(workflow.connect(db, read_only=True)) as conn:
        if conn.execute('SELECT 1 FROM workflow_migrations WHERE version=5').fetchone():
            return
        backup = Path(db).with_name(Path(db).name + '.pre-filename-v5.bak')
        if not backup.exists():
            with closing(sqlite3.connect(backup)) as target:
                conn.backup(target)
    with closing(workflow.connect(db)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        mapping, source_status = {}, {}
        if workflow.has_table(conn, 'workflow_source_records'):
            for row in conn.execute('SELECT * FROM workflow_source_records').fetchall():
                if not row['filename']:
                    continue
                old = row['source_id']
                sid = ensure(conn, detect({}, row['filename'], row['digest']))
                mapping.setdefault(old, set()).add(sid)
                status=conn.execute('SELECT active FROM workflow_sources WHERE id=?',(old,)).fetchone()
                source_status[sid]=max(source_status.get(sid,0),status[0] if status else 1)
                collision = conn.execute('SELECT id FROM workflow_source_records WHERE source_id=? AND digest=? AND row_index=? AND id<>?',
                                         (sid, row['digest'], row['row_index'], row['id'])).fetchone()
                # Keep immutable originals and IDs if a legacy collision cannot be reassociated.
                if collision:
                    mapping[old].add(old)
                    continue
                conn.execute('UPDATE workflow_source_records SET source_id=? WHERE id=?', (sid, row['id']))
                if row['state'] == 'active' and row['canonical_key']:
                    key, seen = row['canonical_key'], set()
                    while key and key not in seen:
                        seen.add(key)
                        article = conn.execute('SELECT is_duplicate_of FROM articles WHERE zotero_key=?', (key,)).fetchone()
                        if article is None:
                            break
                        if article[0] is None:
                            workflow.associate_sources(db, [key], [sid], conn=conn)
                            # Remove only this proven obsolete source association.
                            if old != sid:
                                remaining = conn.execute('SELECT 1 FROM workflow_source_records WHERE source_id=? AND canonical_key=? AND state=? LIMIT 1', (old,key,'active')).fetchone()
                                if not remaining:
                                    conn.execute('DELETE FROM workflow_article_sources WHERE zotero_key=? AND source_id=?',(key,old))
                            break
                        key = article[0]
            conn.execute("""UPDATE workflow_import_files SET source_id=COALESCE((
                SELECT r.source_id FROM workflow_import_members m JOIN workflow_source_records r ON r.id=m.record_id
                WHERE m.batch_id=workflow_import_files.batch_id AND m.file_index=workflow_import_files.file_index
                ORDER BY r.row_index LIMIT 1),source_id)""")
        # Older catalogs may retain only source_file in article raw metadata.
        if workflow.has_table(conn,'articles'):
            for article in conn.execute('SELECT zotero_key,raw_json FROM articles WHERE is_duplicate_of IS NULL').fetchall():
                try:
                    filename=first(json.loads(article['raw_json'] or '{}'),'source_file')
                except (TypeError,ValueError):
                    continue
                if not filename:
                    continue
                if conn.execute('SELECT 1 FROM workflow_source_records WHERE canonical_key=? LIMIT 1',(article['zotero_key'],)).fetchone() if workflow.has_table(conn,'workflow_source_records') else False:
                    continue
                old_ids=[r[0] for r in conn.execute('SELECT source_id FROM workflow_article_sources WHERE zotero_key=?',(article['zotero_key'],))]
                sid=ensure(conn,detect({},filename,''))
                workflow.associate_sources(db,[article['zotero_key']],[sid],conn=conn)
                for old in old_ids:
                    mapping.setdefault(old,set()).add(sid)
                    status=conn.execute('SELECT active FROM workflow_sources WHERE id=?',(old,)).fetchone()
                    source_status[sid]=max(source_status.get(sid,0),status[0] if status else 1)
                    if old!=sid:
                        conn.execute('DELETE FROM workflow_article_sources WHERE zotero_key=? AND source_id=?',(article['zotero_key'],old))
        for sid, active in source_status.items():
            conn.execute('UPDATE workflow_sources SET active=? WHERE id=?',(active,sid))
        policy = json.loads(conn.execute('SELECT stages_json FROM workflow_state WHERE singleton=1').fetchone()[0])
        for stage in policy.values():
            ids = set()
            for old in stage['source_ids']:
                ids.update(mapping.get(old, {old}))
                if conn.execute('SELECT 1 FROM workflow_article_sources WHERE source_id=? LIMIT 1',(old,)).fetchone():
                    ids.add(old)
            stage['source_ids'] = sorted(ids)
        conn.execute('UPDATE workflow_state SET stages_json=?,version=version+1,catalog_revision=catalog_revision+1 WHERE singleton=1',(json.dumps(policy),))
        conn.execute("INSERT INTO workflow_migrations VALUES(5,datetime('now'))")
