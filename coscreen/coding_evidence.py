"""Actor-private coding evidence, stable task fields and atomic form saves."""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import time
import uuid
import warnings
from contextlib import closing
from pathlib import Path

from coscreen import coding, review_workflow as workflow

MIGRATION_VERSION = 3
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_coding_fields(dimension_id INTEGER PRIMARY KEY,
 field_id TEXT NOT NULL UNIQUE,definition_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workflow_evidence_requirements(field_id TEXT PRIMARY KEY,definition_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workflow_evidence_uploads(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,
 actor_id TEXT NOT NULL,zotero_key TEXT NOT NULL,dimension_id INTEGER NOT NULL,sha256 TEXT NOT NULL,
 path TEXT NOT NULL,created_at REAL NOT NULL,attached INTEGER NOT NULL DEFAULT 0,
 UNIQUE(task_id,actor_id,zotero_key,dimension_id,sha256));
CREATE TABLE IF NOT EXISTS workflow_form_requests(task_id TEXT NOT NULL,actor_id TEXT NOT NULL,
 request_id TEXT NOT NULL,digest TEXT NOT NULL,PRIMARY KEY(task_id,actor_id,request_id));
"""


def migrate(db: str | Path) -> None:
    # The foundation helper makes the pre-workflow backup before any new DDL.
    workflow.migrate(db)
    path = Path(db)
    with closing(workflow.connect(path, read_only=True)) as source:
        if source.execute('SELECT 1 FROM workflow_migrations WHERE version=?', (MIGRATION_VERSION,)).fetchone():
            return
        backup = path.with_name(path.name + '.pre-workflow-v3.bak')
        if not backup.exists():
            with closing(sqlite3.connect(backup)) as target:
                source.backup(target)
    with closing(workflow.connect(db)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute('SELECT 1 FROM workflow_migrations WHERE version=?', (MIGRATION_VERSION,)).fetchone():
            return
        for statement in _SCHEMA.split(';'):
            if statement.strip():
                conn.execute(statement)
        if workflow.has_table(conn, 'workspace_undo') and 'request_id' not in {r[1] for r in conn.execute('PRAGMA table_info(workspace_undo)')}:
            conn.execute('ALTER TABLE workspace_undo ADD COLUMN request_id TEXT')
        conn.execute('INSERT INTO workflow_migrations VALUES(?,?)', (MIGRATION_VERSION, coding._now()))


def definition(dim: dict) -> str:
    return json.dumps({k: dim[k] for k in ('name', 'dtype', 'options', 'multi_select', 'unit', 'description')},
                      ensure_ascii=False, sort_keys=True)


def field_rows(db: str | Path) -> list[dict]:
    if not Path(db).is_file():
        return []
    with closing(workflow.connect(db, read_only=True)) as conn:
        if not workflow.has_table(conn, 'coding_dimensions'):
            return []
        fields = {r['dimension_id']: dict(r) for r in conn.execute('SELECT * FROM workflow_coding_fields')} \
            if workflow.has_table(conn, 'workflow_coding_fields') else {}
        return [{**coding._row_to_dimension(row), **({'field_id': fields[row[0]]['field_id']} if row[0] in fields else {})}
                for row in conn.execute(f"SELECT {','.join(coding._DIM_COLUMNS)} FROM coding_dimensions ORDER BY section,position,id")]


def current_owner_db(db: str | Path) -> Path:
    from coscreen import task_collaboration, tasks
    path = Path(db)
    owner = task_collaboration.get_owner(task_collaboration.shared_store_path(path.parent))
    candidate = path.parent / (tasks.slugify_screener(owner['owner_username']) + '.db') if owner else path
    return candidate if candidate.is_file() else path


def ensure_fields(db: str | Path) -> None:
    migrate(db)
    with closing(workflow.connect(db)) as conn, conn:
        known = {r[0]: r[1] for r in conn.execute('SELECT dimension_id,definition_json FROM workflow_coding_fields')}
        for row in conn.execute(f"SELECT {','.join(coding._DIM_COLUMNS)} FROM coding_dimensions").fetchall():
            dim = coding._row_to_dimension(row)
            signature = definition(dim)
            if dim['id'] not in known:
                conn.execute('INSERT INTO workflow_coding_fields VALUES(?,?,?)', (dim['id'], str(uuid.uuid4()), signature))
            elif known[dim['id']] != signature:
                conn.execute('UPDATE workflow_coding_fields SET definition_json=? WHERE dimension_id=?', (signature, dim['id']))
        # Upgrade Task 1's numeric requirements once, with an exact owner definition snapshot.
        required = json.loads(conn.execute('SELECT evidence_json FROM workflow_state WHERE singleton=1').fetchone()[0])
        if any(isinstance(fid, int) for fid in required):
            converted = []
            for fid in required:
                row = conn.execute('SELECT field_id,definition_json FROM workflow_coding_fields WHERE dimension_id=?', (fid,)).fetchone() if isinstance(fid, int) else None
                stable = row[0] if row else str(fid)
                converted.append(stable)
                if row:
                    conn.execute('INSERT OR IGNORE INTO workflow_evidence_requirements VALUES(?,?)', tuple(row))
            conn.execute('UPDATE workflow_state SET evidence_json=? WHERE singleton=1', (json.dumps(converted),))


def mapping(actor_db: str | Path, owner_db: str | Path) -> tuple[dict[int, str], list[dict], list[str]]:
    """Read current owner policy, even for members who have never reopened their store."""
    owners, actors = field_rows(owner_db), field_rows(actor_db)
    required = [str(fid) for fid in workflow.settings(owner_db)['evidence_required_field_ids']]
    frozen = {}
    if Path(owner_db).is_file():
        with closing(workflow.connect(owner_db, read_only=True)) as conn:
            if workflow.has_table(conn, 'workflow_evidence_requirements'):
                frozen = dict(conn.execute('SELECT field_id,definition_json FROM workflow_evidence_requirements'))
    result, issues = {}, []
    for owner in owners:
        fid = owner.get('field_id', str(owner['id']))
        exact = [a for a in actors if definition(a) == definition(owner)]
        explicit = [a for a in actors if a.get('field_id') == fid]
        candidates = explicit if explicit else exact
        unique_owner = sum(definition(o) == definition(owner) for o in owners) == 1
        valid = len(candidates) == 1 and definition(candidates[0]) == definition(owner) and (bool(explicit) or unique_owner)
        if valid:
            result[candidates[0]['id']] = fid
        if fid in required and (not valid or (fid in frozen and frozen[fid] != definition(owner))):
            issues.append({'field_id': fid, 'reason': 'definition_changed' if valid else 'requirement_mapping_gap'})
    known = {o.get('field_id', str(o['id'])) for o in owners}
    issues.extend({'field_id': fid, 'reason': 'required_field_missing'} for fid in required if fid not in known)
    return result, issues, required


def has_note_text(text: str | None) -> bool:
    """Use Python's Unicode whitespace semantics without changing stored text."""
    return bool((text or '').strip())


def evidence_state(db: str | Path, key: str, owner_db: str | Path, task_id: str = '') -> dict:
    mapped, issues, required = mapping(db, owner_db)
    evidence = {}
    with closing(workflow.connect(db, read_only=True)) as conn:
        for dim in field_rows(db):
            did = dim['id']
            note = conn.execute('SELECT id,text FROM coding_notes WHERE dimension_id=? AND zotero_key=?', (did, key)).fetchone()
            images = [] if not note else [{'id': r[0], 'sha256': r[1],
                'url': f'/api/tasks/{task_id}/coding/evidence/images/{r[0]}'}
                for r in conn.execute('SELECT id,sha256 FROM coding_note_images WHERE note_id=? ORDER BY id', (note[0],))]
            fid = mapped.get(did)
            evidence[str(did)] = {'text': note[1] or '' if note else '', 'images': images,
                                  'field_id': fid, 'required': fid in required}
    missing = [{'field_id': fid, 'dimension_id': did} for did, fid in mapped.items()
               if fid in required and not has_note_text(evidence[str(did)]['text']) and not evidence[str(did)]['images']]
    missing += [{'field_id': i['field_id'], 'dimension_id': None} for i in issues]
    return {'evidence': evidence, 'evidence_complete': not missing, 'missing_evidence': missing,
            'mapping_issues': issues}


def complete_keys(db: str | Path, owner_db: str | Path, keys: list[str]) -> set[str]:
    """Aggregate counts only; no note text or private image metadata leaves this helper."""
    mapped, issues, required = mapping(db, owner_db)
    if issues:
        return set()
    required_dims = {dim for dim, fid in mapped.items() if fid in required}
    with closing(workflow.connect(db, read_only=True)) as conn:
        if not all(workflow.has_table(conn, name) for name in ('coding_dimensions', 'coding_values')):
            return set()
        dims = {r[0] for r in conn.execute('SELECT id FROM coding_dimensions')}
        if not dims:
            return set()
        filled, supported = {}, {}
        for dim, key in conn.execute("SELECT dimension_id,zotero_key FROM coding_values WHERE TRIM(COALESCE(value,''))<>''"):
            filled.setdefault(key, set()).add(dim)
        if required_dims and workflow.has_table(conn, 'coding_notes'):
            image_support = ('EXISTS(SELECT 1 FROM coding_note_images i WHERE i.note_id=n.id)'
                             if workflow.has_table(conn, 'coding_note_images') else '0')
            rows = conn.execute('SELECT n.dimension_id,n.zotero_key,n.text,' + image_support + ' FROM coding_notes n')
            for dim, key, text, has_image in rows:
                if has_note_text(text) or has_image:
                    supported.setdefault(key, set()).add(dim)
    return {key for key in keys if dims <= filled.get(key, set()) and required_dims <= supported.get(key, set())}


def assets_dir(db: str | Path) -> Path:
    # Database identity is server-derived and separates equal note IDs across actors.
    path = Path(db).resolve()
    return path.parent / 'coding_assets' / hashlib.sha256(path.name.encode()).hexdigest()[:24]


def validate_image(data: bytes) -> str:
    from PIL import Image, UnidentifiedImageError
    ext = coding._detect_image_extension(data)
    if not ext or not data or len(data) > MAX_UPLOAD_BYTES:
        raise ValueError('Upload must be a PNG, JPEG or WebP image of at most 10 MB.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as im:
                if im.format != {'png': 'PNG', 'jpg': 'JPEG', 'webp': 'WEBP'}[ext] or im.width * im.height > MAX_IMAGE_PIXELS:
                    raise ValueError('Image dimensions exceed the permitted size.')
                im.verify()
            with Image.open(io.BytesIO(data)) as im:
                im.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError('The image could not be decoded safely.') from exc
    return ext


def stage_upload(db: str | Path, task_id: str, actor_id: int, key: str, dim: int, data: bytes) -> dict:
    ext = validate_image(data)
    migrate(db)
    digest = hashlib.sha256(data).hexdigest()
    with closing(workflow.connect(db)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        coding._require_dimension(conn, dim)
        old = conn.execute('SELECT id,path FROM workflow_evidence_uploads WHERE task_id=? AND actor_id=? AND zotero_key=? AND dimension_id=? AND sha256=?',
                           (task_id, str(actor_id), key, dim, digest)).fetchone()
        uid = old[0] if old else str(uuid.uuid4())
        root = assets_dir(db)
        if root.resolve() != root:
            raise ValueError('Evidence asset directory must not be redirected.')
        root.mkdir(parents=True, exist_ok=True)
        path = root / f'{uid}.{ext}'
        if not old:
            # Exclusive UUID file creation; no user filename enters a disk path.
            with path.open('xb') as target:
                target.write(data)
            conn.execute('INSERT INTO workflow_evidence_uploads VALUES(?,?,?,?,?,?,?,?,0)',
                         (uid, task_id, str(actor_id), key, dim, digest, str(path), time.time()))
        else:
            if not path.exists():
                with path.open('xb') as target:
                    target.write(data)
            conn.execute('UPDATE workflow_evidence_uploads SET created_at=? WHERE id=?', (time.time(), uid))
        cleanup_staging(conn, db)
    return {'upload_id': uid, 'sha256': digest,
            'preview_url': f'/api/tasks/{task_id}/coding/evidence/uploads/{uid}'}


def cleanup_staging(conn: sqlite3.Connection, db: str | Path) -> None:
    # Attached files, including detached images retained by Undo, are never collected.
    root = assets_dir(db)
    if root.resolve() != root:
        return
    for uid, raw in conn.execute('SELECT id,path FROM workflow_evidence_uploads WHERE attached=0 AND created_at<?', (time.time() - 7 * 86400,)).fetchall():
        path = Path(raw)
        if path.is_symlink() or path.resolve().parent != root:
            continue
        if conn.execute('SELECT 1 FROM coding_note_images WHERE path=?', (raw,)).fetchone():
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue
        conn.execute('DELETE FROM workflow_evidence_uploads WHERE id=?', (uid,))


def save_form(db: str | Path, key: str, values: dict[int, str], *, notes: dict[int, str] | None,
              evidence: dict[int, dict] | None, trace_context, submission_evidence: bool = False) -> None:
    from coscreen import workspace_undo as undo, research_trace as trace
    if submission_evidence and values:
        raise ValueError('Submission evidence cannot change personal coding values.')
    migrate(db)
    notes, evidence = notes or {}, evidence or {}
    dims = sorted(set(values) | set(notes) | set(evidence))
    if not dims:
        raise ValueError('Supply at least one coding value, note or evidence field.')
    context = trace_context.normalized()
    with closing(coding._connect(db)) as conn, conn:
        undo.begin_save(conn)
        trace.begin_capture_transaction(conn, context, 'coding')
        payload = {'key': key, 'values': values, 'notes': notes, 'evidence': evidence}
        if submission_evidence:
            payload['access_context'] = undo.SUBMISSION_EVIDENCE
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        if context.request_id:
            old = conn.execute('SELECT digest FROM workflow_form_requests WHERE task_id=? AND actor_id=? AND request_id=?',
                               (context.task_id, str(context.actor_id), context.request_id)).fetchone()
            if old:
                if old[0] != digest:
                    raise ValueError('Request ID was already used for a different form.')
                return
            conn.execute('INSERT INTO workflow_form_requests VALUES(?,?,?,?)',
                         (context.task_id, str(context.actor_id), context.request_id, digest))
        prepared = {did: coding._require_dimension(conn, did) for did in dims}
        if not conn.execute('SELECT 1 FROM articles WHERE zotero_key=?', (key,)).fetchone():
            raise ValueError('Article no longer exists.')
        before = undo.snapshot(conn, 'coding', key, dims, evidence=True)
        for did in dims:
            dim = prepared[did]
            field = coding._trace_dimension(conn, context, dim) if trace.is_recording(conn, context, 'coding') else None
            if did in values:
                value = coding._normalize_value(dim, values[did])
                old = before['values'][str(did)]
                conn.execute('INSERT INTO coding_values(dimension_id,zotero_key,value,updated_at) VALUES(?,?,?,?) ON CONFLICT(dimension_id,zotero_key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',
                             (did, key, value, coding._now()))
                trace.capture_event(conn, context, stage='coding', action='coding_value_changed' if old else 'coding_value_created',
                    subject_id=key, before={'value': old[0] if old else ''}, after={'value': value}, field=field)
            if did not in notes and did not in evidence:
                continue
            if did in notes and (not isinstance(notes[did], str) or len(notes[did]) > 100000):
                raise ValueError('Notes must be plain text of at most 100000 characters.')
            old_note = before['evidence'][str(did)]['note']
            text = notes.get(did, old_note[3] if old_note else '')
            now = coding._now()
            conn.execute('INSERT INTO coding_notes(dimension_id,zotero_key,text,created_at,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(dimension_id,zotero_key) DO UPDATE SET text=excluded.text,updated_at=excluded.updated_at', (did, key, text, now, now))
            nid = conn.execute('SELECT id FROM coding_notes WHERE dimension_id=? AND zotero_key=?', (did, key)).fetchone()[0]
            if did in notes:
                trace.capture_event(conn, context, stage='coding', action='coding_note_changed' if old_note else 'coding_note_created',
                    subject_id=key, before={'text': old_note[3] if old_note else ''}, after={'text': text}, field=field)
            if did in evidence:
                intent = evidence[did]
                existing = {r[0]: r for r in conn.execute('SELECT id,sha256 FROM coding_note_images WHERE note_id=?', (nid,))}
                keep = set(intent.get('keep_image_ids', []))
                if not keep <= set(existing):
                    raise ValueError('An evidence image does not belong to this article and field.')
                for image_id in set(existing) - keep:
                    conn.execute('DELETE FROM coding_note_images WHERE id=? AND note_id=?', (image_id, nid))
                    trace.capture_event(conn, context, stage='coding', action='coding_note_image_removed', subject_id=key,
                        before={'id': image_id, 'sha256': existing[image_id][1]}, after=None, field=field)
                for uid in dict.fromkeys(intent.get('upload_ids', [])):
                    upload = conn.execute('SELECT path,sha256 FROM workflow_evidence_uploads WHERE id=? AND task_id=? AND actor_id=? AND zotero_key=? AND dimension_id=?',
                        (uid, context.task_id, str(context.actor_id), key, did)).fetchone()
                    if not upload or not Path(upload[0]).is_file() or Path(upload[0]).is_symlink() or Path(upload[0]).resolve().parent != assets_dir(db):
                        raise ValueError('Staged evidence is unavailable or belongs to another field.')
                    if conn.execute('SELECT 1 FROM coding_note_images WHERE note_id=? AND sha256=?', (nid, upload[1])).fetchone():
                        continue
                    cur = conn.execute("INSERT INTO coding_note_images(note_id,path,sha256,ocr_text,ocr_status,created_at) VALUES(?,?,?,'','pending',?)", (nid, upload[0], upload[1], now))
                    conn.execute('UPDATE workflow_evidence_uploads SET attached=1 WHERE id=?', (uid,))
                    trace.capture_event(conn, context, stage='coding', action='coding_note_image_added', subject_id=key,
                        before=None, after={'id': cur.lastrowid, 'sha256': upload[1]}, field=field)
        after = undo.snapshot(conn, 'coding', key, dims, evidence=True)
        if submission_evidence:
            before['access_context'] = after['access_context'] = undo.SUBMISSION_EVIDENCE
        undo.record_save(conn, context, 'coding', key, before, after)
