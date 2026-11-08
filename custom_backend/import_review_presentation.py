"""Read-only prioritization for import review; no migrations or write operations."""
from __future__ import annotations

import json
from coscreen import review_imports as store


def review_projection(conn, batch_id: str) -> dict:
    rows = [dict(r) for r in conn.execute(
        """SELECT DISTINCT r.* FROM workflow_source_records r
        JOIN workflow_import_members m ON m.record_id=r.id
        WHERE m.batch_id=? ORDER BY r.created_at,r.row_index,r.id""", (batch_id,))]
    related = {r['id']: dict(r) for r in conn.execute(
        """WITH RECURSIVE related(id) AS (
          SELECT record_id FROM workflow_import_members WHERE batch_id=?
          UNION SELECT r.target_record_id FROM workflow_source_records r
          JOIN related x ON r.id=x.id WHERE r.target_record_id IS NOT NULL)
        SELECT r.* FROM workflow_source_records r JOIN related x ON r.id=x.id""", (batch_id,))}
    pending = {r[0] for r in conn.execute(
        """SELECT record_id FROM workflow_import_metadata WHERE action IS NULL""")}
    candidates = {r[0] for r in conn.execute(
        """SELECT DISTINCT m.ref_id FROM workflow_import_candidate_members m
        JOIN workflow_import_candidates c ON c.id=m.candidate_id
        WHERE m.ref_type='record' AND c.status IN ('pending','undecided')""")}
    batch_ids = {r['id'] for r in rows}
    classifications = {}
    linked_batches = {}
    for record in rows:
        rid, seen, duplicate, metadata, waiting = record['id'], set(), False, False, False
        linked = None
        while rid is not None:
            if rid in seen or rid not in related:
                waiting = True
                break
            seen.add(rid)
            link = related[rid]
            duplicate |= rid in candidates
            metadata |= rid in pending
            if rid not in batch_ids and (rid in pending or link['state'] != 'active' and link['canonical_key'] is None):
                linked = linked or rid
            if (rid not in batch_ids and link['state'] != 'active'
                    and link['canonical_key'] is None):
                waiting = True
            rid = link['target_record_id']
        # Activation may create a metadata proposal after a new retained record
        # is inserted. Do not promise that such a dependent source row is ready.
        if not duplicate and not metadata and not waiting and record['target_record_id']:
            root_id = record['id']
            while related[root_id]['target_record_id']:
                root_id = related[root_id]['target_record_id']
            root = related[root_id]
            if root['canonical_key'] is None and store.metadata(json.loads(record['article_json'])) != store.metadata(json.loads(root['article_json'])):
                waiting = True
        reason = ('duplicate' if duplicate else 'metadata' if metadata else
                  'dependency' if waiting else 'ready' if record['state'] == 'staged'
                  else 'completed' if record['state'] == 'active' else 'waiting')
        rank = {'duplicate': 0, 'metadata': 1, 'dependency': 2,
                'waiting': 2, 'ready': 3, 'completed': 4}[reason]
        classifications[record['id']] = {'attention_reason': reason, 'review_priority': rank}
        if linked:
            if linked not in linked_batches:
                membership = conn.execute('SELECT batch_id FROM workflow_import_members WHERE record_id=? ORDER BY batch_id LIMIT 1', (linked,)).fetchone()
                linked_batches[linked] = membership[0] if membership else None
            classifications[record['id']]['related_batch_id'] = linked_batches[linked]
    attention = [r for r in rows if classifications[r['id']]['attention_reason'] != 'completed']
    attention.sort(key=lambda r: (classifications[r['id']]['review_priority'],
                                 r['created_at'], r['row_index'], r['id']))
    completed = [r for r in rows if classifications[r['id']]['attention_reason'] == 'completed']
    ready = sum(classifications[r['id']]['attention_reason'] == 'ready' for r in attention)
    return {'all': rows, 'attention': attention, 'completed': completed,
            'classification': classifications,
            'review_counts': {'attention': len(attention), 'completed': len(completed),
                              'ready': ready, 'needs_review': len(attention) - ready}}


def present_record(conn, row: dict, classification: dict) -> dict:
    result = {**store.public_record(conn, row), **classification}
    proposal = result.get('metadata')
    if proposal:
        key = store.canonical(conn, proposal['canonical_key'])
        current = conn.execute('SELECT * FROM articles WHERE zotero_key=?', (key,)).fetchone()
        if current is not None:
            proposal['current'] = store.metadata(dict(current))
            proposal['stale'] = proposal['current'] != store.metadata(proposal['before'])
    return result


def _member_article(member: dict) -> dict:
    if member.get('ref_type') == 'active':
        return member.get('article') or {}
    return (member.get('record') or {}).get('article') or {}


def _member_order(member: dict) -> tuple:
    """原顺序（导入顺序）：活跃文章按 import_order，待审记录按 created_at/row_index。"""
    if member.get('ref_type') == 'active':
        article = member.get('article') or {}
        order = article.get('import_order')
        return (0, '%012d' % order if isinstance(order, int) else '',
                str(article.get('zotero_key') or member.get('ref_id')))
    record = member.get('record') or {}
    row_index = record.get('row_index')
    return (1, str(record.get('created_at') or ''),
            '%012d' % row_index if isinstance(row_index, int) else '',
            str(member.get('ref_id')))


def suggested_keeper(members: list[dict]) -> dict:
    """建议保留者：规则①已入任务成员必为保留者；否则规则②完整度最高者。

    平分取原顺序（导入顺序）。reasons 是结构化键（has_doi… / active_member），
    由前端渲染文案。确认接口仍要求显式 keeper——这里只是建议。
    """
    active = [member for member in members or [] if member.get('ref_type') == 'active']
    if active:
        active.sort(key=_member_order)
        return {'keeper_key': active[0]['ref_id'], 'keeper_record_id': None,
                'reasons': ['active_member']}
    best, best_score = None, None
    for member in sorted(members or [], key=_member_order):
        score = store.completeness(_member_article(member))
        if best_score is None or score > best_score:
            best, best_score = member, score
    if best is None:
        return {'keeper_key': None, 'keeper_record_id': None, 'reasons': []}
    return {'keeper_key': None, 'keeper_record_id': best['ref_id'],
            'reasons': [key for key, present
                        in zip(store.COMPLETENESS_REASONS, best_score) if present]}


def present_candidates(conn, selected: list[dict]) -> list[dict]:
    """Expand only the requested page, without rescanning all groups per item."""
    # 延迟导入：import_review_actions 在模块级导入本模块（判据与载荷必须同源）。
    from custom_backend.import_review_actions import bulk_eligibility
    result = []
    for candidate in selected:
        members = []
        for row in conn.execute(
                'SELECT * FROM workflow_import_candidate_members WHERE candidate_id=? ORDER BY ref_type,ref_id',
                (candidate['id'],)):
            member = dict(row)
            if member['ref_type'] == 'active':
                article = conn.execute('SELECT * FROM articles WHERE zotero_key=?', (member['ref_id'],)).fetchone()
                member['article'] = dict(article) if article else None
            else:
                record = conn.execute('SELECT * FROM workflow_source_records WHERE id=?', (member['ref_id'],)).fetchone()
                member['record'] = store.public_record(conn, dict(record)) if record else None
            members.append(member)
        payload = {**candidate, 'review_id': candidate['id'], 'members': members}
        eligible, reason = bulk_eligibility(payload)
        result.append({**payload, 'bulk_eligible': eligible,
                       'bulk_ineligible_reason': reason,
                       'suggested_keeper': suggested_keeper(members)})
    return result
