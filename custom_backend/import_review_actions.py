"""Small, transactional review updates and explicitly confirmed bulk actions."""
from __future__ import annotations

import json
import re

from coscreen import review_imports as store
from coscreen.dedup import _identity_doi
from custom_backend.import_review_presentation import review_projection, present_record, present_candidates


def bulk_subject(actions: list[dict]) -> str:
    return json.dumps(actions, sort_keys=True)


# ---------------------------------------------------------------------------
# 批量确认的资格判据（r10-U2 / 导入12项 #11-A / 2.1.5 G5·B1）
#
# 三段判据，任一成立即 eligible（仍需人工点一下确认，绝不自动判重）：
#   a. DOI 一致：所有成员的身份 DOI 规范化后非空且唯一（期刊样式 DOI 视同缺
#      DOI：_identity_doi，r17）；
#   b. 全员无身份 DOI，且规范化标题 + 第一作者姓氏 + 年份三项全一致且非空；
#   c. 部分成员有身份 DOI，出现的身份 DOI 规范化后只有一个取值，且三项全一致
#      且非空（2.1.5 G5 新增；解决「一侧有 DOI、一侧缺 DOI」的同文献漏判）。
#
# 通用/过短标题从严排除（G5 §七.1）：判据 b/c 依赖题名推断身份，通用题名
# （Editorial/Book review/编者按…）与规范化后过短的题名不适用——误排除只损失
# 一点自动化，误纳入会错合并。判据 a 不受此限：DOI 已是单篇身份，与题名通用
# 与否无关（既有 DOI 批量回归也依赖这一边界）。
#
# 判据与前端镜像的历史关系：custom_frontend/import_review_updates.js 的
# identityKey()/eligible() 是旧镜像；2.1.5 G5 起后端单一事实源，候选载荷直接
# 下发 bulk_eligible / bulk_ineligible_reason（F2 改为删镜像、读字段）。过渡期
# 跨语言钉 build/tests/test_r10_u2_bulk_identity.py 只允许两类已披露差异。
# ---------------------------------------------------------------------------

_PARTICLES = ("van", "von", "der", "den", "de", "di", "da", "del", "della", "la", "le", "du")
_SUFFIXES = ("jr", "sr", "ii", "iii", "iv")


def identity_key(member: dict) -> tuple[str, str, str]:
    """成员的（规范化标题, 第一作者姓氏, 年份）；任一项为空返回空串占位。"""
    article = member.get("article") or (member.get("record") or {}).get("article") or {}
    title = store.normalize_title(article.get("title"))
    head = str(article.get("authors") or "").split(";")[0].strip()
    if "," in head:
        surname = head.split(",", 1)[0].strip()
    else:
        tokens = head.split()
        while len(tokens) > 1 and tokens[-1].lower().rstrip(".") in _SUFFIXES:
            tokens.pop()
        # "Smith J" / "Smith J. A." 是「姓 缩写」序：末位是缩写时姓氏取前一位
        # （与 normalize.first_author_surname 的同义判据；仅用于本判据的强身份
        # 一致，不改变解析层结果）。
        if len(tokens) > 1 and re.fullmatch(r"[A-Z]{1,3}\.?", tokens[-1]):
            tokens.pop()
        surname = tokens[-1] if tokens else ""
    words = surname.split()
    while len(words) > 1 and words[0].lower() in _PARTICLES:
        words.pop(0)
    surname = " ".join(words).casefold()
    year = str(article.get("year") or "").strip()
    return title, surname, year


def _member_doi(member: dict) -> str:
    article = member.get("article") or (member.get("record") or {}).get("article") or {}
    return _identity_doi(article.get("doi") or "")


def bulk_eligibility(candidate: dict) -> tuple[bool, str | None]:
    """批量确认资格 + 不可批量的结构化原因键（载荷与校验共用的单一事实源）。"""
    if candidate.get('kind') == 'identity_conflict':
        return False, 'identity_conflict'
    if candidate.get('status') not in ('pending', 'undecided'):
        return False, 'status'
    members = candidate.get('members') or []
    if len(members) < 2:
        return False, 'insufficient_members'
    if sum(member.get('ref_type') == 'active' for member in members) > 1:
        return False, 'active_conflict'
    dois = [_member_doi(member) for member in members]
    present = {doi for doi in dois if doi}
    if len(present) > 1:
        return False, 'doi_conflict'
    # 判据 a：全员有身份 DOI 且一致（不受通用/过短标题限制）。
    if present and all(dois):
        return True, None
    keys = [identity_key(member) for member in members]
    titles = {key[0] for key in keys}
    surnames = {key[1] for key in keys}
    years = {key[2] for key in keys}
    if not (len(titles) == 1 and len(surnames) == 1 and len(years) == 1
            and "" not in titles and "" not in surnames and "" not in years):
        return False, 'missing_identity'
    title = keys[0][0]
    if store.is_generic_title(title):
        return False, 'generic_title'
    if store.is_short_title(title):
        return False, 'short_title'
    # 判据 b（全员无 DOI）与 c（部分成员有 DOI 但只有一个取值）。
    return True, None


def eligible_duplicate(candidate: dict) -> bool:
    return bulk_eligibility(candidate)[0]


def apply_bulk(conn, batch: str, actions: list[dict], actor: str) -> dict:
    if not actions or len(actions) > 100:
        raise ValueError('Select between 1 and 100 review items.')
    if len({(a['kind'], a['id']) for a in actions}) != len(actions):
        raise ValueError('A review item cannot be selected twice.')
    # Validate every candidate before writing; the surrounding transaction rolls
    # back the entire operation if any proposal becomes invalid afterwards.
    # 与载荷下发共用同一函数（bulk_eligibility）——单一事实源。
    for action in actions:
        if action['kind'] == 'candidate':
            candidate = present_candidates(conn, [store.candidate_row(conn, batch, action['id'])])[0]
            ok, reason = bulk_eligibility(candidate)
            if not ok:
                raise store.MergeError(
                    'Bulk duplicate confirmation requires identical DOI values, or an '
                    'identical title, first author and year with no identity conflict.',
                    'import_merge.bulk_ineligible',
                    params={'candidate_id': action['id'], 'reason': reason}, status=409)
    summary = {'articles': 0, 'fields': {}, 'doi_collision': [], 'skipped': []}
    for action in actions:
        if action['kind'] == 'candidate':
            result = store.decide_candidate(
                conn, batch, action['id'], 'duplicate', actor,
                action.get('keeper_key'), action.get('keeper_record_id'),
                conflict_picks=action.get('conflict_picks'),
                ai_suggestion_id=action.get('ai_suggestion_id'))
            part = (result or {}).get('merge_fill') or {}
            summary['articles'] += part.get('articles', 0)
            for field, count in (part.get('fields') or {}).items():
                summary['fields'][field] = summary['fields'].get(field, 0) + count
            summary['doi_collision'].extend(part.get('doi_collision') or [])
            summary['skipped'].extend(part.get('skipped') or [])
        else:
            store.decide_metadata(conn, batch, action['id'], 'keep_existing', actor)
    return {'merge_fill': summary} if any(
        summary[key] for key in ('articles', 'fields', 'doi_collision', 'skipped')) else {}


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
        # 占位符个数由 record_ids 数量决定，SQLite 不支持绑定占位符列表；拼入的
        # 内容只有 '?' 字符，具体 id 值仍通过参数绑定传入（检查器白名单已登记）。
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
