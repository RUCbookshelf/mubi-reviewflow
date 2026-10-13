"""Small, transactional review updates and explicitly confirmed bulk actions."""
from __future__ import annotations

import json
from coscreen import review_imports as store
from coscreen.dedup import _norm_doi
from custom_backend.import_review_presentation import review_projection, present_record, present_candidates


def bulk_subject(actions: list[dict]) -> str:
    return json.dumps(actions, sort_keys=True)


def eligible_duplicate(candidate: dict) -> bool:
    if candidate['kind'] == 'identity_conflict' or candidate['status'] not in ('pending', 'undecided'):
        return False
    members = candidate['members']
    if len(members) < 2 or sum(m['ref_type'] == 'active' for m in members) > 1:
        return False
    dois = [_norm_doi((m.get('article') or (m.get('record') or {}).get('article') or {}).get('doi') or '') for m in members]
    return all(dois) and len(set(dois)) == 1


def apply_bulk(conn, batch: str, actions: list[dict], actor: str) -> None:
    if not actions or len(actions) > 100:
        raise ValueError('Select between 1 and 100 review items.')
    if len({(a['kind'], a['id']) for a in actions}) != len(actions):
        raise ValueError('A review item cannot be selected twice.')
    # Validate every candidate before writing; the surrounding transaction rolls
    # back the entire operation if any proposal becomes invalid afterwards.
    for action in actions:
        if action['kind'] == 'candidate':
            candidate = present_candidates(conn, [store.candidate_row(conn, batch, action['id'])])[0]
            if not eligible_duplicate(candidate):
                raise ValueError('Bulk duplicate confirmation requires matching DOI values and no identity conflict.')
    for action in actions:
        if action['kind'] == 'candidate':
            store.decide_candidate(conn, batch, action['id'], 'duplicate', actor,
                                   action.get('keeper_key'), action.get('keeper_record_id'))
        else:
            store.decide_metadata(conn, batch, action['id'], 'keep_existing', actor)


def review_updates(conn, batch: str, actions: list[dict]) -> dict:
    """Return changed records and dependent classifications, never the full queue."""
    candidate_ids = {a['id'] for a in actions if a['kind'] == 'candidate'}
    record_ids = {a['id'] for a in actions if a['kind'] == 'metadata'}
    for cid in candidate_ids:
        record_ids.update(r[0] for r in conn.execute(
            "SELECT ref_id FROM workflow_import_candidate_members WHERE candidate_id=? AND ref_type='record'", (cid,)))
    # Metadata acceptance can make other proposals against the same article stale.
    for rid in list(record_ids):
        record_ids.update(r[0] for r in conn.execute(
            'SELECT record_id FROM workflow_import_metadata WHERE canonical_key IN '
            '(SELECT canonical_key FROM workflow_import_metadata WHERE record_id=?)', (rid,)))
    if record_ids:
        placeholders = ','.join('?' for _ in record_ids)
        record_ids.update(r[0] for r in conn.execute(
            'WITH RECURSIVE affected(id) AS (SELECT id FROM workflow_source_records WHERE id IN ('+placeholders+') '
            'UNION SELECT r.id FROM workflow_source_records r JOIN affected a ON r.target_record_id=a.id) SELECT id FROM affected', tuple(record_ids)))
    candidates = store.candidate_rows(conn, batch, include_members=False)
    for cid, rid in conn.execute("SELECT candidate_id,ref_id FROM workflow_import_candidate_members WHERE ref_type='record'"):
        if rid in record_ids:
            candidate_ids.add(cid)
    projection = review_projection(conn, batch)
    records = [present_record(conn, r, projection['classification'][r['id']])
               for r in projection['all'] if r['id'] in record_ids]
    return {'records': records, 'candidates': present_candidates(conn, [c for c in candidates if c['id'] in candidate_ids]),
            'review_counts': projection['review_counts']}
