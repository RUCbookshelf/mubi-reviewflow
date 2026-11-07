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
from coscreen.coding_evidence import _backup_is_usable, _write_backup_atomic

MIGRATION_VERSION = 3


def migrate(path: Path) -> None:
    with closing(workflow.connect(path)) as conn:
        if workflow.has_table(conn, 'workflow_import_undo'):
            return
        backup = path.with_name(path.name + '.pre-import-undo-v3.bak')
        # b15·F4-78：不能只看 exists()——0 字节/截断/写坏的残留 .bak 会被永久
        # 当作有效备份而跳过真备份，迁移 DDL 照常执行。判据与 coding_evidence
        # 的同族备份（b11·F3-17）一致：可读且含表才算可用，否则原子重写并校验。
        if not _backup_is_usable(backup):
            _write_backup_atomic(conn, backup)
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
    """Capture the mutable provenance state of one review action (or a bulk set).

    b15·F4-79：修复前 bulk 条目对每个 action 递归调用本函数，每条记录至少两次
    点查（source_records / import_metadata），``available()`` 在每次批次详情
    GET 与每次写事务内重放一遍——查询数随成员规模线性放大（bulk(40) 实测
    282 条 SELECT）。现在按表做集合查询并保持**完全相同的行内容与行序**
    （先按 part 顺序、再按 part 内记录 id 升序），查询数从 O(成员数) 降为
    常数级；语义与比较结果不变。
    """
    parts = json.loads(subject) if kind == 'bulk' else [{'kind': kind, 'id': subject}]
    # ---- 第一遍：把各 part 需要的 id 分类汇总 ----
    candidate_parts = []   # 需要候选行 + 成员记录的 part
    metadata_parts = []    # 需要 canonical 文章/acceptance 的 part
    for action in parts:
        part_kind, part_id = action['kind'], action['id']
        if part_kind == 'candidate':
            candidate_parts.append(part_id)
        elif part_kind != 'bulk':
            metadata_parts.append((part_kind, part_id))
    candidate_ids = dict.fromkeys(candidate_parts)
    # 各 part 的记录 id 列表（candidate 取成员 record，其余取自身）
    members_by_candidate: dict[str, list] = {}
    if candidate_ids:
        for candidate_id, ref_id in conn.execute(
                "".join((
                    "SELECT candidate_id,ref_id FROM workflow_import_candidate_members ",
                    "WHERE ref_type='record' AND candidate_id IN (",
                    ",".join('?' for _ in candidate_ids), ")",
                )),
                tuple(candidate_ids)):
            members_by_candidate.setdefault(candidate_id, []).append(ref_id)
        for candidate_id in members_by_candidate:
            members_by_candidate[candidate_id].sort()
    part_ids = []
    for action in parts:
        part_kind, part_id = action['kind'], action['id']
        if part_kind == 'candidate':
            part_ids.append(members_by_candidate.get(part_id, []))
        elif part_kind != 'bulk':
            part_ids.append([part_id])
        else:
            part_ids.append([])
    all_record_ids = list(dict.fromkeys(rid for ids in part_ids for rid in ids))
    record_rows = {}
    if all_record_ids:
        placeholders = ','.join('?' for _ in all_record_ids)
        record_rows = {row['id']: dict(row) for row in conn.execute(
            "".join(("SELECT * FROM workflow_source_records WHERE id IN (", placeholders, ")")),
            tuple(all_record_ids))}
        proposal_rows = {row['record_id']: dict(row) for row in conn.execute(
            "".join(("SELECT * FROM workflow_import_metadata WHERE record_id IN (", placeholders, ")")),
            tuple(all_record_ids))}
    else:
        proposal_rows = {}
    candidate_rows = {}
    if candidate_ids:
        placeholders = ','.join('?' for _ in candidate_ids)
        candidate_rows = {row['id']: dict(row) for row in conn.execute(
            "".join(("SELECT * FROM workflow_import_candidates WHERE id IN (", placeholders, ")")),
            tuple(candidate_ids))}
    # metadata 类 part 的 canonical 文章 + acceptance（canonical 沿 is_duplicate_of
    # 链解析，需逐 part 调用；part 数受 bulk 上限 100 约束）
    article_rows, acceptance_rows = {}, {}
    for part_kind, part_id in metadata_parts:
        proposal = proposal_rows.get(part_id)
        if proposal is None:
            continue
        key = imports.canonical(conn, proposal['canonical_key'])
        article = conn.execute('SELECT * FROM articles WHERE zotero_key=?', (key,)).fetchone()
        if article:
            article_rows[part_id] = {'zotero_key': key, **imports.metadata(dict(article))}
        acceptance = conn.execute('SELECT * FROM workflow_metadata_acceptances WHERE zotero_key=?', (key,)).fetchone()
        if acceptance:
            acceptance_rows[part_id] = dict(acceptance)
    legacy_seq = conn.execute("SELECT COALESCE(MAX(seq),0) FROM title_duplicate_undo").fetchone()[0]
    # ---- 第二遍：按 part 顺序重建（行序与修复前逐位一致）----
    result = {'legacy_seq': legacy_seq, 'candidates': [], 'records': [], 'proposals': [],
              'articles': [], 'acceptances': []}
    for action, ids in zip(parts, part_ids):
        part_kind, part_id = action['kind'], action['id']
        part = {'legacy_seq': legacy_seq, 'candidates': [], 'records': [],
                'proposals': [], 'articles': [], 'acceptances': []}
        if part_kind == 'candidate' and part_id in candidate_rows:
            part['candidates'] = [candidate_rows[part_id]]
        for rid in ids:
            if rid in record_rows:
                part['records'].append(record_rows[rid])
            if rid in proposal_rows:
                part['proposals'].append(proposal_rows[rid])
                if part_kind == 'metadata':
                    if rid in article_rows:
                        part['articles'].append(article_rows[rid])
                    if rid in acceptance_rows:
                        part['acceptances'].append(acceptance_rows[rid])
        if kind == 'bulk':
            for field, key_field in [('candidates', 'id'), ('records', 'id'), ('proposals', 'record_id'),
                                     ('articles', 'zotero_key'), ('acceptances', 'zotero_key')]:
                merged = {row[key_field]: row for row in result[field]}
                for row in part[field]:
                    merged[row[key_field]] = row
                result[field] = list(merged.values())
        else:
            for field in ('candidates', 'records', 'proposals', 'articles', 'acceptances'):
                result[field] = part[field]
            break
    return result


def remember(conn, batch: str, actor: str, kind: str, subject: str, before: dict, version: int) -> None:
    after = snapshot(conn, kind, subject)
    if before == after:
        return
    # MM-04：作废只限同一（批次, 操作人）的旧条目——latest()/available()/restore()
    # 均按该口径取条目，全局作废会把他人/他批次的撤销能力一并静默终结。
    conn.execute("UPDATE workflow_import_undo SET state='superseded' "
                 "WHERE state='available' AND batch_id=? AND actor_id=?", (batch, actor))
    conn.execute('''INSERT INTO workflow_import_undo(batch_id,actor_id,kind,subject_id,before_json,
        after_json,policy_version,created_at) VALUES(?,?,?,?,?,?,?,?)''',
        (batch, actor, kind, subject, imports.dumps(before), imports.dumps(after), version, imports.now()))


def latest(conn, batch: str, actor: str):
    return conn.execute('''SELECT * FROM workflow_import_undo WHERE batch_id=? AND actor_id=?
        AND state='available' ORDER BY id DESC LIMIT 1''', (batch, actor)).fetchone()


def activate(conn, batch: str) -> None:
    """批次激活：终结该批次全部操作人的撤销条目（批次已激活则不可再撤销）。

    MM-04：只限定本批次——激活 batch-2 不得把他批次的条目一并置 activated
    （此前的全局 UPDATE 由 review_imports_api 内联，与本模块其余口径分裂）。"""
    conn.execute("UPDATE workflow_import_undo SET state='activated' "
                 "WHERE state='available' AND batch_id=?", (batch,))


def available(conn, batch: str, actor: str) -> dict:
    """撤销可用性判定：本函数是**读路径**，永远返回判定结果、不抛业务异常。

    b16·F4-82：``snapshot()`` 的 metadata 分支经 ``imports.canonical()`` 沿
    ``is_duplicate_of`` 链解析，文章行缺失或成环时抛 ValueError；修复前该异常
    从 ``available()`` 原样穿出——批次详情 GET 500，而同一状态下的写路径
    （``restore()``，经 ``review_imports_api.mutate`` 的 ``except ValueError``）
    被译成 409，读写口径分裂。现在把「库外改写/部分恢复造成的解析失败」归入
    与「快照已变」同级的不安全判定：降级 ``available: False``，并带上
    ``reason`` 供写路径给出同样的说明（不新增对合法输入的拒绝，也不吞掉写路径
    的 409）。

    r11c（MM-04 姊妹，读路径口径补齐）：撤销窗口按「批次 + 操作人」归属——
    写路径（``remember``/``activate`` 的 UPDATE）已按该口径收口，但此处原先
    还要求**全局** ``workflow_state.version`` 与条目的 ``policy_version``
    相等；而 ``review_imports_api.mutate`` 在**任何批次**的 changed 核验后都
    会把全局版本 +1，于是他批次的核验会把本批次仍然完好的撤销入口静默关掉
    （外部形式化复审 probe：journal 两行均 available、版本 4→5、入口 False）。
    读路径的失效判定改由三道与条目作用域一致的守卫承担：行状态 available、
    subject 快照逐位一致（subject 自身未被后续改动）、无已激活记录；全局版本
    漂移不再单独终结撤销。写时的并发安全仍由 mutate 的
    write_guard(expected_version) 把关，与策略变更后仍可撤销「自己批次的上一
    步」的语义一致（策略影响的是后续行为，不使已发生操作的逆操作失效）。
    """
    row = latest(conn, batch, actor)
    if not row:
        return {'available': False}
    after = json.loads(row['after_json'])
    try:
        current = snapshot(conn, row['kind'], row['subject_id'])
    except ValueError as exc:
        # canonical 缺失 / 重复身份成环 / 依赖源记录缺失：撤销不可用，刷新后
        # 由用户在正常库里重做（不在此处改写数据）。
        return {'available': False, 'entry_id': None, 'kind': row['kind'],
                'subject_id': row['subject_id'], 'reason': str(exc)}
    safe = (current == after
            and not any(r['state'] == 'active' for r in after['records']))
    return {'available': safe, 'entry_id': row['id'] if safe else None,
            'kind': row['kind'], 'subject_id': row['subject_id']}


def restore(conn, batch: str, actor: str) -> dict:
    row = latest(conn, batch, actor)
    state = available(conn, batch, actor) if row else {'available': False}
    if not row or not state['available']:
        raise ValueError(state.get('reason')
                         or 'The last import review changed or was activated. Refresh before correcting it.')
    before, after = json.loads(row['before_json']), json.loads(row['after_json'])
    # Restore only the mutable fields of incoming source records.
    for record in before['records']:
        conn.execute('''UPDATE workflow_source_records SET canonical_key=?,target_record_id=?,outcome=?,state=? WHERE id=?''',
                     (record['canonical_key'], record['target_record_id'], record['outcome'], record['state'], record['id']))
    for candidate in before['candidates']:
        fields = ('status', 'keeper_key', 'keeper_record_id', 'decided_by', 'updated_at')
        conn.execute(
            "UPDATE workflow_import_candidates SET status=?,keeper_key=?,keeper_record_id=?,"
            "decided_by=?,updated_at=? WHERE id=?",
            [candidate[f] for f in fields] + [candidate['id']])
    proposal_ids = {r['record_id'] for r in before['proposals']}
    for proposal in after['proposals']:
        if proposal['record_id'] not in proposal_ids:
            conn.execute('DELETE FROM workflow_import_metadata WHERE record_id=?', (proposal['record_id'],))
    for proposal in before['proposals']:
        fields = ('canonical_key', 'before_json', 'incoming_json', 'action', 'accepted_json', 'decided_by', 'decided_at')
        conn.execute(
            "UPDATE workflow_import_metadata SET canonical_key=?,before_json=?,incoming_json=?,"
            "action=?,accepted_json=?,decided_by=?,decided_at=? WHERE record_id=?",
            [proposal[f] for f in fields]+[proposal['record_id']])
    for article in before['articles']:
        if imports.metadata(article) == imports.metadata(next((r for r in after['articles'] if r['zotero_key']==article['zotero_key']), {})):
            continue
        conn.execute(
            "UPDATE articles SET item_type=?,title=?,authors=?,journal=?,year=?,doi=?,"
            "abstract=?,url=? WHERE zotero_key=?",
            [article[f] for f in imports.METADATA_FIELDS]+[article['zotero_key']])
        workflow.bump_catalog_revision(None, conn=conn)
        revision = conn.execute('SELECT catalog_revision FROM workflow_state WHERE singleton=1').fetchone()[0]
        # Publish a new metadata revision so already-synchronized reviewers can
        # receive the correction; never roll their screening/coding back.
        #
        # b15·F4-80：acceptance 行的 **record_id 归属**必须回到快照里的 before
        # 行，而不是"本条被撤销的 proposal"。修复前一律取 before['proposals']
        # 命中的 record_id（即被撤销的那条），于是撤销 m1 后 acceptance 显示
        # "元数据来自 m1"，真正提供该元数据的 m0 归属永久丢失（审计溯源错位，
        # 多来源文献的常规复核流程即可产生）。没有 before acceptance 行时（本次
        # accept 是首个来源）只发布新 revision，不凭空编造来源。
        prior = next((a for a in before.get('acceptances') or []
                      if a.get('zotero_key') == article['zotero_key']), None)
        if prior is None:
            prior = next((a for a in after.get('acceptances') or []
                          if a.get('zotero_key') == article['zotero_key']), None)
        if prior is not None:
            source_record = _acceptance_source(conn, before, article['zotero_key'], prior.get('record_id'))
            if source_record is not None:
                conn.execute('''INSERT INTO workflow_metadata_acceptances VALUES(?,?,?,?) ON CONFLICT(zotero_key)
                    DO UPDATE SET revision=excluded.revision,metadata_json=excluded.metadata_json,record_id=excluded.record_id''',
                    (article['zotero_key'], revision, imports.dumps(imports.metadata(article)), source_record))
    conn.execute("UPDATE workflow_import_undo SET state='undone' WHERE id=?", (row['id'],))
    return {'undone': True, 'subject_id': row['subject_id'], 'kind': row['kind']}


def _acceptance_source(conn, before: dict, key: str, stored_record_id) -> str | None:
    """撤销后 acceptance 应指向的来源记录 id（b15·F4-80 / b16·F4-81）。

    优先用快照里 acceptance 行自己的 ``record_id``——它就是"当前元数据来自
    哪条来源记录"的权威答案。仅当该值不可信时回落到 before proposal 里
    canonical_key 命中的那条记录：

    - bulk 条目的 ``subject_id`` 是 actions JSON 串，绝不是 record id
      （b16·F4-81：修复前把它直接写进 REFERENCES workflow_source_records 的
      ``record_id`` 列，抛裸 ``IntegrityError`` 而不在 ``except (ValueError,
      KeyError)`` 内）；
    - 该 id 在 ``workflow_source_records`` 里已不存在（库外改写/部分恢复）。

    两条都不可用（且回落也落空）时返回 None：调用方只发布新 revision，不写入
    会违反外键的兜底值。
    """
    candidate = stored_record_id if isinstance(stored_record_id, str) else None
    if candidate and not candidate.lstrip().startswith(('[', '{')):
        if conn.execute('SELECT 1 FROM workflow_source_records WHERE id=?', (candidate,)).fetchone():
            return candidate
    fallback = next((p['record_id'] for p in before.get('proposals') or []
                     if p.get('canonical_key') == key), None)
    if fallback and conn.execute('SELECT 1 FROM workflow_source_records WHERE id=?', (fallback,)).fetchone():
        return fallback
    return None
