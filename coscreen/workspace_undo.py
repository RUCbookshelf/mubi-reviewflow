"""Transactional, personal Undo history for review decisions and coding saves."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from coscreen.research_trace import TraceContext

STAGES = ("screen", "fulltext", "coding")
SUBMISSION_EVIDENCE = 'coding_submission_evidence'
_DECISION_COLUMNS = "decision,exclusion_reason,notes,tags,screened_at,updated_at"
_TABLES = {"screen": "decisions", "fulltext": "stage2_decisions"}
# 每篇文章（task+actor+stage+zotero_key）保留的可撤销历史条数；更早条目置
# superseded（不删除，可追溯，但不再参与比较，也不再可能被恢复）。
JOURNAL_RETAIN_PER_ARTICLE = 50
# 维度定义的语义键集（与 coding_evidence.definition / trace_actions 的
# definition_keys 同口径）：布局列 section/position/created_at 不参与守卫比较。
_SEMANTIC_DEFINITION_COLUMNS = ('name', 'dtype', 'options_json', 'multi_select', 'unit', 'description')
_SEMANTIC_DEFINITION_SQL = (
    "SELECT name,dtype,options_json,multi_select,unit,description FROM coding_dimensions WHERE id=?")
# 撤销 journal 无法完整恢复时的明确拒绝文案（U3-P1）。
MISSING_EVIDENCE_FILE = '图片文件已不在当前数据目录，无法完整撤销。'


def _ensure_journal_columns(conn: sqlite3.Connection) -> None:
    """旧库自愈：补齐 workspace_undo 的追加列（不重写既有行）。"""
    columns = {r[1] for r in conn.execute('PRAGMA table_info(workspace_undo)')}
    if 'request_id' not in columns:
        conn.execute('ALTER TABLE workspace_undo ADD COLUMN request_id TEXT')
    if 'superseded' not in columns:
        conn.execute('ALTER TABLE workspace_undo ADD COLUMN superseded INTEGER NOT NULL DEFAULT 0')


def begin_save(conn: sqlite3.Connection) -> None:
    """Acquire the write lock before reading snapshots; join the caller's transaction."""
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS workspace_undo ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, actor_id TEXT NOT NULL, "
        "stage TEXT NOT NULL, zotero_key TEXT NOT NULL, before_json TEXT NOT NULL, "
        "after_json TEXT NOT NULL, undone INTEGER NOT NULL DEFAULT 0)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS workspace_undo_latest "
                 "ON workspace_undo(task_id,actor_id,stage,undone,id)")
    _ensure_journal_columns(conn)


def _row(conn: sqlite3.Connection, table: str, key: str) -> list | None:
    row = conn.execute(f"SELECT {_DECISION_COLUMNS} FROM {table} WHERE zotero_key=?", (key,)).fetchone()
    return list(row) if row else None


def snapshot(conn: sqlite3.Connection, stage: str, key: str,
             dimensions: list[int] | None = None, *, evidence: bool = False) -> dict[str, Any]:
    if stage not in STAGES:
        raise ValueError("Unknown Undo stage.")
    if stage in _TABLES:
        state: dict[str, Any] = {"record": _row(conn, _TABLES[stage], key)}
        # Do not undo past downstream edits or silently make newly completed work ineligible.
        # 守卫快照只保留实际有数据的编码取值，证据维度以摘要记录——screen/fulltext
        # 撤销不恢复证据，守卫只需精确比较，不必复制备注/OCR 全文（U3-P5）。
        state["coding"] = [list(r) for r in conn.execute(
            "SELECT dimension_id,value,updated_at FROM coding_values WHERE zotero_key=? ORDER BY dimension_id",
            (key,)) if str(r[1] or '').strip()]
        state['coding_evidence'] = evidence_snapshot(conn, key, guard=True)
        if stage == "screen":
            state["fulltext"] = _row(conn, "stage2_decisions", key)
        return state
    values, definitions = {}, {}
    for dim in sorted(set(dimensions or [])):
        row = conn.execute("SELECT value,updated_at FROM coding_values WHERE zotero_key=? AND dimension_id=?",
                           (key, dim)).fetchone()
        values[str(dim)] = list(row) if row else None
        # coding 快照是撤销的恢复载荷与精确守卫，定义只取语义键集（U3-P3）。
        definition = conn.execute(_SEMANTIC_DEFINITION_SQL, (dim,)).fetchone()
        definitions[str(dim)] = _definition_view(list(definition)) if definition else None
    result = {"values": values, "definitions": definitions}
    if evidence:
        result['evidence'] = evidence_snapshot(conn, key, dimensions)
        from coscreen.review_workflow import has_table
        result['stable_fields'] = [list(r) for r in conn.execute('SELECT * FROM workflow_coding_fields ORDER BY dimension_id')
                                   if r[0] in (dimensions or [])] if has_table(conn, 'workflow_coding_fields') else []
    return result


def evidence_snapshot(conn: sqlite3.Connection, key: str, dimensions: list[int] | None = None,
                      *, guard: bool = False) -> dict:
    from coscreen.coding import _NOTE_COLUMNS, _IMAGE_COLUMNS
    dims = dimensions if dimensions is not None else [r[0] for r in conn.execute('SELECT id FROM coding_dimensions')]
    result = {}
    for dim in sorted(set(dims)):
        note = conn.execute(f"SELECT {','.join(_NOTE_COLUMNS)} FROM coding_notes WHERE zotero_key=? AND dimension_id=?", (key, dim)).fetchone()
        images = [list(r) for r in conn.execute(f"SELECT {','.join(_IMAGE_COLUMNS)} FROM coding_note_images WHERE note_id=? ORDER BY id", (note[0],))] if note else []
        state = {'note': list(note) if note else None, 'images': images}
        if guard:
            # screen/fulltext 守卫快照：跳过无数据维度，有数据维度只记摘要，
            # 不复制备注/OCR 全文（U3-P5）；比较侧对历史全文快照计算同一摘要。
            if not _dim_has_data(state):
                continue
            result[str(dim)] = {'digest': _evidence_digest(state)}
        else:
            # coding 条目按自身维度精确快照（它是撤销的恢复载荷）。
            result[str(dim)] = state
    return result


def _evidence_digest(state: dict) -> str:
    """证据维度内容摘要。图片行抹去路径字段参与摘要：数据目录迁移会整体
    改写绝对路径，文件位置不属于证据内容（图片身份由 id/sha256/OCR 承载），
    这样迁移前后摘要一致，迁移不会破坏 screen/fulltext 撤销守卫。"""
    def without_path(image):
        if not isinstance(image, list):
            return image
        return [field if index != 2 else '' for index, field in enumerate(image)]
    normalized = {'note': state.get('note'),
                  'images': [without_path(image) for image in (state.get('images') or [])]}
    return hashlib.sha256(json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def _dim_has_data(state) -> bool:
    """该维度证据是否含实际数据：任一图片，或非空备注文本（空备注行不算数据）。"""
    if not isinstance(state, dict):
        return False
    images = state.get('images')
    if isinstance(images, list) and images:
        return True
    note = state.get('note')
    return isinstance(note, list) and len(note) > 3 and str(note[3] or '').strip() != ''


def _definition_view(row):
    """维度定义的语义键集视图：name/dtype/options/multi_select/unit/description。

    兼容历史整行快照（``SELECT *`` 含 section/position/created_at 布局列）；
    布局编辑不改变已保存值/备注的语义，不参与守卫比较（U3-P3）。"""
    from coscreen.coding import _DIM_COLUMNS
    if not isinstance(row, list):
        return row
    columns = (_DIM_COLUMNS if len(row) == len(_DIM_COLUMNS) else _SEMANTIC_DEFINITION_COLUMNS)
    dim = dict(zip(columns, row))
    try:
        options = json.loads(dim.get('options_json') or '[]')
    except ValueError:
        options = []
    return [dim.get('name') or '', dim.get('dtype') or 'text', [str(o) for o in options],
            bool(dim.get('multi_select')), dim.get('unit') or '', dim.get('description') or '']


def _guard_state(stage: str, state: dict) -> dict:
    """守卫比较视图（U3-P2/P7/P3/P5）。

    screen/fulltext 条目：忽略双方均无实际数据的编码取值/证据维度——编码方案
    新增或删除空维度不是下游工作，不触发"下游已变更"误报；有数据的维度以
    摘要精确比较（历史条目存全文快照，此处计算同一摘要）。definitions 归一
    到语义键集，忽略布局列。coding 条目是恢复载荷，保持精确比较。"""
    view = {}
    for name, value in state.items():
        if stage in _TABLES and name == 'coding' and isinstance(value, list):
            view[name] = [row for row in value if str(row[1] or '').strip()]
        elif stage in _TABLES and name == 'coding_evidence' and isinstance(value, dict):
            digests = {}
            for dim, snap in value.items():
                if isinstance(snap, dict) and isinstance(snap.get('digest'), str):
                    digests[dim] = {'digest': snap['digest']}
                elif _dim_has_data(snap):
                    digests[dim] = {'digest': _evidence_digest(snap)}
            view[name] = digests
        elif name == 'definitions' and isinstance(value, dict):
            view[name] = {dim: _definition_view(row) for dim, row in value.items()}
        else:
            view[name] = value
    return view


def record_save(conn: sqlite3.Connection, context: TraceContext, stage: str, key: str,
                before: dict, after: dict) -> None:
    context = context.normalized()
    if before == after:
        return
    conn.execute(
        "INSERT INTO workspace_undo(task_id,actor_id,stage,zotero_key,before_json,after_json,request_id) VALUES(?,?,?,?,?,?,?)",
        (context.task_id, str(context.actor_id), stage, key, json.dumps(before, ensure_ascii=False),
         json.dumps(after, ensure_ascii=False), context.request_id),
    )
    # 每篇文章只保留最近 N 条历史：更早条目置 superseded（不删除，可追溯，
    # 不再参与比较/恢复），控制 journal 容量随保存次数放大（U3-P5）。
    conn.execute(
        "UPDATE workspace_undo SET superseded=1 WHERE task_id=? AND actor_id=? AND stage=? AND zotero_key=? "
        "AND superseded=0 AND id NOT IN (SELECT id FROM workspace_undo WHERE task_id=? AND actor_id=? "
        "AND stage=? AND zotero_key=? ORDER BY id DESC LIMIT ?)",
        (context.task_id, str(context.actor_id), stage, key,
         context.task_id, str(context.actor_id), stage, key, JOURNAL_RETAIN_PER_ARTICLE),
    )


def _exists(conn: sqlite3.Connection) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workspace_undo'").fetchone())


def is_submission_evidence(before: dict, after: dict) -> bool:
    """Only server-marked evidence-only operations can use canonical coding access."""
    return (before.get('access_context') == after.get('access_context') == SUBMISSION_EVIDENCE
            and isinstance(before.get('evidence'), dict) and isinstance(after.get('evidence'), dict)
            and isinstance(before.get('values'), dict)
            and before['values'] == after.get('values')
            and set(before['evidence']) == set(after['evidence']) == set(before['values'])
            and before.get('definitions') == after.get('definitions')
            and before.get('stable_fields') == after.get('stable_fields'))


def snapshot_matches(conn: sqlite3.Connection, stage: str, key: str, before: dict, after: dict) -> bool:
    dims = [int(d) for d in after.get('values', {})]
    current = snapshot(conn, stage, key, dims, evidence='evidence' in after)
    if stage == 'coding' and is_submission_evidence(before, after):
        current['access_context'] = SUBMISSION_EVIDENCE
    # Historical entries only protect the state they actually captured.
    return _guard_state(stage, {name: value for name, value in current.items() if name in after}) \
        == _guard_state(stage, after)


def _latest(conn: sqlite3.Connection, context: TraceContext, stage: str) -> dict | None:
    if not _exists(conn):
        return None
    row = conn.execute(
        "SELECT id,zotero_key,before_json,after_json FROM workspace_undo WHERE task_id=? AND actor_id=? AND stage=? "
        "AND undone=0 ORDER BY id DESC LIMIT 1", (context.task_id, str(context.actor_id), stage),
    ).fetchone()
    if not row:
        return None
    canonical = stage == 'coding' and is_submission_evidence(json.loads(row[2]), json.loads(row[3]))
    return {'entry_id': row[0], 'zotero_key': row[1],
            'access_context': SUBMISSION_EVIDENCE if canonical else 'personal'}


def latest(db: str | Path, context: TraceContext, stage: str) -> dict | None:
    from coscreen.db import _connect
    if stage not in STAGES:
        raise ValueError("Unknown Undo stage.")
    with closing(_connect(db)) as conn:
        return _latest(conn, context.normalized(), stage)


def _require_restorable_evidence(db: str | Path, evidence: dict) -> None:
    """拒绝会产生悬空图片引用的撤销：待恢复图片文件必须仍在当前数据目录（U3-P1）。

    迁移前已 detach 的图片没有 coding_note_images 行可被迁移重写；若其文件已
    不在当前数据目录（被 GC/清理或从未复制），恢复会重新插入指向不存在文件
    的行，/coding/evidence/images/{id} 必然 404。此时明确拒绝而不是产生坏引用。
    目录白名单与 API 的 image_file 守卫一致：本库私有目录与旧公共资产目录。"""
    from coscreen import coding, coding_evidence
    allowed = {coding_evidence.assets_dir(db).resolve(),
               (Path(db).resolve().parent / coding.CODING_ASSETS_DIRNAME).resolve()}
    for state in evidence.values():
        for image in (state or {}).get('images') or []:
            if not isinstance(image, list) or len(image) < 3:
                continue
            path = Path(str(image[2]))
            if (not path.is_absolute() or path.is_symlink() or not path.is_file()
                    or path.resolve().parent not in allowed):
                raise ValueError(MISSING_EVIDENCE_FILE)


def _release_request_summaries(conn: sqlite3.Connection, context: TraceContext, request_id: str) -> None:
    """撤销后清理该保存的幂等摘要，使同一请求重试重新执行而不是返回旧结果（U3-P4）。

    覆盖编码表单（workflow_form_requests）与初筛/复筛决策（trace_write_operations）
    两套请求摘要；撤销已使对应业务状态回退，重放旧结果会谎报"保存成功"。"""
    from coscreen.review_workflow import has_table
    for table in ('workflow_form_requests', 'trace_write_operations'):
        if has_table(conn, table):
            conn.execute(f"DELETE FROM {table} WHERE task_id=? AND actor_id=? AND request_id=?",
                         (context.task_id, str(context.actor_id), request_id))


def release_detached_uploads(conn: sqlite3.Connection, context: TraceContext, key: str) -> None:
    """保存后把不再被引用的证据上传 attached 复位为 0，让 7 天 GC 可回收（U3-P8）。

    仅当该上传的文件已不被任何 coding_note_images 行引用、且未被任何活跃
    （未 superseded）coding 撤销快照引用时复位——撤销恢复仍需要该文件。
    screen/fulltext 快照不恢复图片文件，不构成引用。快照 JSON 与上传行存有
    同一路径字符串，但 JSON 会转义反斜杠/引号，因此按 json.dumps 后的转义
    形态做 LIKE 子串判定（路径中的 %/_ 最多造成保守保留，不会误回收）。"""
    context = context.normalized()
    if not _exists(conn):
        return
    rows = conn.execute(
        "SELECT id,path FROM workflow_evidence_uploads WHERE task_id=? AND actor_id=? AND zotero_key=? AND attached=1",
        (context.task_id, str(context.actor_id), key)).fetchall()
    for uid, raw in rows:
        if conn.execute("SELECT 1 FROM coding_note_images WHERE path=? LIMIT 1", (raw,)).fetchone():
            continue
        pattern = f"%{json.dumps(raw, ensure_ascii=False)[1:-1]}%"
        if conn.execute(
            "SELECT 1 FROM workspace_undo WHERE task_id=? AND actor_id=? AND stage='coding' AND superseded=0 "
            "AND (before_json LIKE ? OR after_json LIKE ?) LIMIT 1",
            (context.task_id, str(context.actor_id), pattern, pattern)).fetchone():
            continue
        conn.execute("UPDATE workflow_evidence_uploads SET attached=0 WHERE id=?", (uid,))


def undo(db: str | Path, context: TraceContext, stage: str, entry_id: int) -> dict:
    """Restore exactly one identified operation; retries never pop the next entry."""
    from coscreen.db import _connect
    from coscreen.research_trace import begin_capture_transaction, capture_event

    if stage not in STAGES:
        raise ValueError("Unknown Undo stage.")
    context = context.normalized()
    with closing(_connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if not _exists(conn):
            raise ValueError("There is no saved operation to undo.")
        _ensure_journal_columns(conn)
        row = conn.execute(
            "SELECT zotero_key,before_json,after_json,undone,request_id,superseded FROM workspace_undo "
            "WHERE id=? AND task_id=? AND actor_id=? AND stage=?",
            (entry_id, context.task_id, str(context.actor_id), stage),
        ).fetchone()
        if not row:
            raise ValueError("There is no saved operation to undo.")
        key, before_json, after_json, undone, request_id, superseded = row
        before, after = json.loads(before_json), json.loads(after_json)
        # 每个撤销事件都显式链接回其原始保存条目（与 trace_actions 的
        # "Restorations have an explicit server-assigned link" 契约一致，U3-P6）。
        context = replace(context, source_record_id=f'workspace_undo:{entry_id}')
        if not undone:
            if superseded:
                raise ValueError("Undo history changed. Reload and try again.")
            last = _latest(conn, context, stage)
            if not last or last["entry_id"] != entry_id:
                raise ValueError("Undo history changed. Reload and try again.")
            if not conn.execute("SELECT 1 FROM articles WHERE zotero_key=?", (key,)).fetchone():
                raise ValueError("This article is no longer available for Undo.")
            if not snapshot_matches(conn, stage, key, before, after):
                raise ValueError("The saved result or downstream work has changed. Undo cannot overwrite newer changes.")
            if stage == 'coding' and 'evidence' in before:
                _require_restorable_evidence(db, before['evidence'])
            begin_capture_transaction(conn, context, stage)
            if stage in _TABLES:
                table = _TABLES[stage]
                conn.execute(f"DELETE FROM {table} WHERE zotero_key=?", (key,))
                if before["record"] is not None:
                    conn.execute(f"INSERT INTO {table}(zotero_key,{_DECISION_COLUMNS}) VALUES(?,?,?,?,?,?,?)",
                                 (key, *before["record"]))
                names = _DECISION_COLUMNS.split(",")
                capture_event(conn, context, stage=stage, action="decision_undone", subject_id=key,
                              before=dict(zip(names, after["record"])) if after["record"] else None,
                              after=dict(zip(names, before["record"])) if before["record"] else None)
            else:
                from coscreen.coding import _require_dimension, _trace_dimension
                from coscreen.research_trace import is_recording
                recording = is_recording(conn, context, stage)
                for dim, value in before["values"].items():
                    conn.execute("DELETE FROM coding_values WHERE zotero_key=? AND dimension_id=?", (key, int(dim)))
                    if value is not None:
                        conn.execute("INSERT INTO coding_values(dimension_id,zotero_key,value,updated_at) VALUES(?,?,?,?)",
                                     (int(dim), key, *value))
                    if recording:
                        saved_value = after["values"][dim]
                        capture_event(conn, context, stage=stage, action="coding_values_undone", subject_id=key,
                                      before={"value": saved_value[0] if saved_value else ""},
                                      after={"value": value[0] if value else ""},
                                      field=_trace_dimension(conn, context, _require_dimension(conn, int(dim))))
                if 'evidence' in before:
                    from coscreen.coding import _NOTE_COLUMNS, _IMAGE_COLUMNS
                    for dim, state in before['evidence'].items():
                        note = conn.execute('SELECT id FROM coding_notes WHERE dimension_id=? AND zotero_key=?', (int(dim), key)).fetchone()
                        if note:
                            conn.execute('DELETE FROM coding_note_images WHERE note_id=?', (note[0],))
                            conn.execute('DELETE FROM coding_notes WHERE id=?', (note[0],))
                        if state['note']:
                            conn.execute(f"INSERT INTO coding_notes({','.join(_NOTE_COLUMNS)}) VALUES(?,?,?,?,?,?)", state['note'])
                            for image in state['images']:
                                conn.execute(f"INSERT INTO coding_note_images({','.join(_IMAGE_COLUMNS)}) VALUES(?,?,?,?,?,?,?)", image)
                        # Trace evidence has content hashes, never filesystem paths.
                        def public(value):
                            return {'text': value['note'][3] if value['note'] else '',
                                    'images': [{'id': r[0], 'sha256': r[3]} for r in value['images']]}
                        capture_event(conn, context, stage='coding', action='coding_evidence_undone', subject_id=key,
                                      before=public(after['evidence'][dim]), after=public(state),
                                      field=_trace_dimension(conn, context, _require_dimension(conn, int(dim))) if recording else None)
            conn.execute("UPDATE workspace_undo SET undone=1 WHERE id=?", (entry_id,))
            # 同请求重试必须重新执行保存，而不是命中幂等摘要返回撤销前的旧结果（U3-P4）。
            if request_id:
                _release_request_summaries(conn, context, request_id)
        restored = (_row(conn, _TABLES[stage], key) if stage in _TABLES else None)
        return {"undone": True, "zotero_key": key, "latest": _latest(conn, context, stage),
                'access_context': SUBMISSION_EVIDENCE if stage == 'coding' and is_submission_evidence(before, after) else 'personal',
                "decision": (dict(zip(_DECISION_COLUMNS.split(","), restored)) if restored else None)}
