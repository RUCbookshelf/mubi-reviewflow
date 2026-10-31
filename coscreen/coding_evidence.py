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

MIGRATION_VERSION = 7
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
    # 快路径（门禁墙钟）：基础迁移(v1)与自身版本(v7)齐全时 migrate 是无操作
    # ——一次只读连接判齐即返回（原先每个 API 请求经 ensure_fields 进来要开
    # 2 个只读连接逐个重查）；未建库/旧库走完整链，首建与升级行为不变。
    path = Path(db)
    if path.is_file():
        with closing(workflow.connect(path, read_only=True)) as probe:
            if workflow.has_table(probe, 'workflow_migrations'):
                done = {int(r[0]) for r in probe.execute('SELECT version FROM workflow_migrations')}
                if {workflow.SCHEMA_VERSION, MIGRATION_VERSION} <= done:
                    return
    # The foundation helper makes the pre-workflow backup before any new DDL.
    workflow.migrate(db)
    with closing(workflow.connect(path, read_only=True)) as source:
        if source.execute('SELECT 1 FROM workflow_migrations WHERE version=?', (MIGRATION_VERSION,)).fetchone():
            return
        # 备份只保护既有库；本进程新建、尚在首建链中的库没有可备份的数据。
        if not workflow.is_fresh_database(path):
            # 备份文件名保持 .pre-workflow-v3.bak 稳定标识（不经版本号拼装）：
            # 升级链后续可能再改写 DDL，但「升级前最后一份完整备份」的语义不变；
            # docs/WORKFLOW_CONTROLS.md:36/137 与 docs/WORKFLOW_VALIDATION.md:60
            # 均以该名称为运维口径，随版本号改名会让既有指引失效。
            backup = path.with_name(path.name + '.pre-workflow-v3.bak')
            if not backup.exists():
                with closing(sqlite3.connect(backup)) as target:
                    source.backup(target)
    with closing(workflow.connect_for_initial_migration(db)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute('SELECT 1 FROM workflow_migrations WHERE version=?', (MIGRATION_VERSION,)).fetchone():
            return
        for statement in _SCHEMA.split(';'):
            if statement.strip():
                conn.execute(statement)
        if workflow.has_table(conn, 'workspace_undo'):
            # 旧库自愈：补齐撤销 journal 的追加列（键与既有行不重写）。
            columns = {r[1] for r in conn.execute('PRAGMA table_info(workspace_undo)')}
            if 'request_id' not in columns:
                conn.execute('ALTER TABLE workspace_undo ADD COLUMN request_id TEXT')
            if 'superseded' not in columns:
                conn.execute('ALTER TABLE workspace_undo ADD COLUMN superseded INTEGER NOT NULL DEFAULT 0')
            # r6 #22（v7）：仅**摘要形态**存量条目的摘要按当前公式重写；全文
            # 快照形态（v2.1.3 及更早）不改写，由比较视图按新公式重算（详见
            # _rewrite_legacy_guard_digests）。版本号取 7：workflow_migrations
            # 版本号是全模块共享命名空间（1=workflow、2=review_imports、3=v3 旧版/
            # import_review_undo、4=import_sources、5=文件名迁移、6=screening_rounds），
            # 4 已被占用会误判已迁移。
            _rewrite_legacy_guard_digests(conn)
        conn.execute('INSERT INTO workflow_migrations VALUES(?,?)', (MIGRATION_VERSION, coding._now()))


def _rewrite_legacy_guard_digests(conn: sqlite3.Connection) -> None:
    """r6 #22 旧库兼容迁移（v7）：把摘要形态存量条目的证据摘要迁移到当前公式。

    形态与判据（第 3 轮仲裁后的保守方向）：
    - **全文快照形态**（v2.1.3 及更早：``evidence_snapshot`` 未传 guard=True）
      **一律不改写**——``_guard_state`` 比较时按当前公式重算，纯时间戳漂移可
      撤销、升级前真实编辑仍阻断（内容在快照里，可精确判定）。
    - **摘要形态**（257d96c..326df44 窗口的构建，哈希不可逆）只有在**能证明
      内容未变、仅公式变更**时才重写：用当前库内状态按**旧公式**重算，得到
      的哈希与存量摘要**逐字节相同**才改写为新公式摘要。此时可证"旧摘要 =
      f_old(当前内容)"，即从上次保存到升级之间内容确实没变。
    - 否则（旧摘要与 f_old(当前内容) 不同）**保留旧摘要不放宽**：这说明内容
      真的变了（或无法判定），照旧按守卫失配阻断（宁可 409）。撤销守卫的
      核心语义（不得静默覆盖更新的下游工作）优先于"清理残留误拒"。

    旧公式摘要随条目保留在 ``legacy_digest`` 键内（不参与比较），供审计与
    后续人工核对；升级前被阻断的摘要形态条目属于可接受的残留面，已在
    RELEASE_NOTES 中披露。"""
    from coscreen import workspace_undo as undo_mod
    columns = {r[1] for r in conn.execute('PRAGMA table_info(workspace_undo)')}
    if not {'stage', 'zotero_key', 'after_json', 'undone'} <= columns:
        return
    superseded = 'AND superseded=0 ' if 'superseded' in columns else ''
    rows = conn.execute(
        f"SELECT id,zotero_key,after_json FROM workspace_undo WHERE stage IN ('screen','fulltext') "
        f"AND undone=0 {superseded}").fetchall()
    for entry_id, key, after_json in rows:
        try:
            after = json.loads(after_json)
        except ValueError:
            continue
        evidence = after.get('coding_evidence') if isinstance(after, dict) else None
        if not isinstance(evidence, dict) or not evidence:
            continue
        # 仅摘要形态条目：全文快照交给比较视图按新公式重算（不改写、不损审计）。
        digest_dims = [dim for dim, snap in evidence.items()
                       if isinstance(snap, dict) and isinstance(snap.get('digest'), str)]
        if not digest_dims:
            continue
        rewritten = dict(evidence)
        changed = False
        for dim in digest_dims:
            try:
                snap = undo_mod.evidence_snapshot(conn, key, [int(dim)], guard=False).get(str(dim))
            except (TypeError, ValueError):
                continue
            if not isinstance(snap, dict) or not undo_mod._dim_has_data(snap):
                continue  # 维度已无数据：真实下游变更，保留旧摘要（继续阻断）
            stored = evidence[dim]['digest']
            # 关键判据：存量摘要必须等于「当前内容 + 旧公式」——只有此时可证
            # 内容未变、差异全在公式（时间戳入摘要）上，迁移才是安全的。
            if undo_mod._evidence_digest(snap, legacy_timestamps=True) != stored:
                continue
            fresh = undo_mod._evidence_digest(snap)
            if fresh == stored:
                continue
            rewritten[dim] = {'digest': fresh, 'legacy_digest': stored}
            changed = True
        if not changed:
            continue
        after['coding_evidence'] = rewritten
        conn.execute('UPDATE workspace_undo SET after_json=? WHERE id=?',
                     (json.dumps(after, ensure_ascii=False), entry_id))


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
    # U4-P8：get_owner 必传 task_id（任务目录名），使属主-任务绑定校验与
    # review_workflow_api.context 同口径：库文件被错拷/恢复到错误任务目录时
    # 不再把数据路径重定向到 owner 的个人库，而是回退原路径。
    owner = task_collaboration.get_owner(
        task_collaboration.shared_store_path(path.parent), task_id=path.parent.name)
    candidate = path.parent / (tasks.slugify_screener(owner['owner_username']) + '.db') if owner else path
    return candidate if candidate.is_file() else path


def ensure_fields(db: str | Path) -> None:
    migrate(db)
    # 只读优先（门禁墙钟）：先以只读连接比对，全部就绪（无缺字段/无定义
    # 漂移/无 Task1 数字字段待升级）时不开写连接——原先每个 API 请求都
    # 开写连接并走一遍空事务提交。有待写再走原写路径（罕见竞态窗口下
    # 下一请求自愈，与原实现一致性等价）。
    with closing(workflow.connect(db, read_only=True)) as probe:
        known = {r[0]: r[1] for r in probe.execute('SELECT dimension_id,definition_json FROM workflow_coding_fields')}
        pending = False
        for row in probe.execute(f"SELECT {','.join(coding._DIM_COLUMNS)} FROM coding_dimensions").fetchall():
            dim = coding._row_to_dimension(row)
            if dim['id'] not in known or known[dim['id']] != definition(dim):
                pending = True
                break
        if not pending:
            required_peek = json.loads(probe.execute('SELECT evidence_json FROM workflow_state WHERE singleton=1').fetchone()[0])
            if not any(isinstance(fid, int) for fid in required_peek):
                return
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
        # r6 #28：task_id 以 rtrim('. ') 兼容匹配——修复前（R6-C3 之前）以
        # Windows 尾点别名落库的 staged 行与规范 id 是同一物理任务的同一上传。
        old = conn.execute("SELECT id,path FROM workflow_evidence_uploads WHERE rtrim(task_id,'. ')=? AND actor_id=? AND zotero_key=? AND dimension_id=? AND sha256=?",
                           (task_id.rstrip('. '), str(actor_id), key, dim, digest)).fetchone()
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
        # r6 #21：no-op 保存（record_save 因 before==after 不建 journal）不再落
        # 幂等摘要——摘要先于快照落库时，撤销后重放同 request_id 会命中旧摘要
        # 直接返回"成功"，而业务值并未写回（假成功），且该摘要无 journal 可
        # 关联、永不清除。摘要改为在 record_save 确认产生 journal 后写入；同
        # 一事务提交，重试语义不变（保存中途失败整体回滚，摘要与 journal
        # 同生同灭）。
        pending_summary = None
        if context.request_id:
            old = conn.execute('SELECT digest FROM workflow_form_requests WHERE task_id=? AND actor_id=? AND request_id=?',
                               (context.task_id, str(context.actor_id), context.request_id)).fetchone()
            if old:
                if old[0] != digest:
                    raise ValueError('Request ID was already used for a different form.')
                return
            pending_summary = (context.task_id, str(context.actor_id), context.request_id, digest)
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
                # 幂等保存（R6-U2/#23）：业务值与库内一致时不刷新 updated_at、
                # 不产生 trace 事件——同值重存只动时间戳，会使既有 screen/
                # fulltext 撤销条目被守卫误报"下游已变更"。
                if old is None or value != old[0]:
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
            # 同口径幂等（R6-U2/#23）：备注文本未变化时不刷新 updated_at——
            # 时间戳是守卫摘要的一部分（见 workspace_undo._evidence_digest），
            # 同值重存不得使其漂移。
            note_changed = did in notes and (old_note is None or text != old_note[3])
            if note_changed:
                conn.execute('INSERT INTO coding_notes(dimension_id,zotero_key,text,created_at,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(dimension_id,zotero_key) DO UPDATE SET text=excluded.text,updated_at=excluded.updated_at', (did, key, text, now, now))
                trace.capture_event(conn, context, stage='coding', action='coding_note_changed' if old_note else 'coding_note_created',
                    subject_id=key, before={'text': old_note[3] if old_note else ''}, after={'text': text}, field=field)
            if did in evidence:
                row = conn.execute('SELECT id FROM coding_notes WHERE dimension_id=? AND zotero_key=?', (did, key)).fetchone()
                if row is None:
                    # evidence-only 首次保存且无备注行：补一行承载图片（不改变
                    # 既有行为——旧代码同样会先建行）。
                    conn.execute('INSERT INTO coding_notes(dimension_id,zotero_key,text,created_at,updated_at) VALUES(?,?,?,?,?)',
                                 (did, key, text, now, now))
                    row = conn.execute('SELECT id FROM coding_notes WHERE dimension_id=? AND zotero_key=?', (did, key)).fetchone()
                nid = row[0]
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
                    # r6 #28：尾点别名存量 staged 行同键兼容（见 stage_upload 注释）。
                    upload = conn.execute("SELECT path,sha256 FROM workflow_evidence_uploads WHERE id=? AND rtrim(task_id,'. ')=? AND actor_id=? AND zotero_key=? AND dimension_id=?",
                        (uid, context.task_id.rstrip('. '), str(context.actor_id), key, did)).fetchone()
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
        # r6 #21：仅在本次保存真的产生了撤销 journal（非 no-op）时落幂等摘要。
        if pending_summary is not None and before != after:
            conn.execute('INSERT INTO workflow_form_requests VALUES(?,?,?,?)', pending_summary)
        # 保存后复位不再被引用的上传附件标记，让 7 天垃圾回收可回收（U3-P8）；
        # 仍被 coding_note_images 或活跃撤销快照引用的文件保持 attached=1。
        undo.release_detached_uploads(conn, context, key)
