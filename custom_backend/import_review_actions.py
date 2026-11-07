"""Small, transactional review updates and explicitly confirmed bulk actions."""
from __future__ import annotations

import json
import re
import unicodedata

from coscreen import review_imports as store
from coscreen.dedup import _identity_doi
from custom_backend.import_review_presentation import review_projection, present_record, present_candidates


def bulk_subject(actions: list[dict]) -> str:
    return json.dumps(actions, sort_keys=True)


# ---------------------------------------------------------------------------
# 批量确认的资格判据（r10-U2 / 导入12项 #11-A）
#
# 两段判据，任一成立即 eligible（仍需人工点一下确认，绝不自动判重）：
#   A. DOI 一致：所有成员 DOI 规范化后非空且唯一（既有规则，不变）；
#   B. 无 DOI 的强身份一致：所有成员 DOI 均为空，且
#      规范化标题相同 + 第一作者姓氏相同 + 年份相同（三项都必须非空）。
#
# 判据 B 的三项规范化必须与前端镜像 **逐字一致**
# （custom_frontend/import_review_updates.js 的 identityKey()）：后端是校验的
# 唯一权威，前端只用同一函数做按钮可用性预判。两端一致性由
# build/tests/test_r10_u2_bulk_identity.py 的跨语言对照矩阵钉住。
# ---------------------------------------------------------------------------

_PARTICLES = ("van", "von", "der", "den", "de", "di", "da", "del", "della", "la", "le", "du")
_SUFFIXES = ("jr", "sr", "ii", "iii", "iv")


def identity_key(member: dict) -> tuple[str, str, str]:
    """成员的（规范化标题, 第一作者姓氏, 年份）；任一项为空返回空串占位。"""
    article = member.get("article") or (member.get("record") or {}).get("article") or {}
    title = unicodedata.normalize("NFKC", str(article.get("title") or "")).casefold()
    title = "".join(" " if unicodedata.category(c).startswith(("P", "S")) else c for c in title)
    title = " ".join(title.split())
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


def eligible_duplicate(candidate: dict) -> bool:
    if candidate['kind'] == 'identity_conflict' or candidate['status'] not in ('pending', 'undecided'):
        return False
    members = candidate['members']
    if len(members) < 2 or sum(m['ref_type'] == 'active' for m in members) > 1:
        return False
    # 期刊样式 DOI（"(issn)" 整刊标识）不参与「同 DOI 即同一文献」的批量
    # 确认判据（r17）：视同缺 DOI 落入标题/姓氏/年份强一致判据，避免同刊
    # 不同文献被一键确认成重复。
    dois = [_identity_doi((m.get('article') or (m.get('record') or {}).get('article') or {}).get('doi') or '') for m in members]
    if all(dois):
        return len(set(dois)) == 1
    if any(dois):
        return False
    keys = [identity_key(m) for m in members]
    titles, surnames, years = ({k[0] for k in keys}, {k[1] for k in keys}, {k[2] for k in keys})
    return (len(titles) == 1 and len(surnames) == 1 and len(years) == 1
            and "" not in titles and "" not in surnames and "" not in years)


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
                raise ValueError('Bulk duplicate confirmation requires identical DOI values, or an identical title, first author and year with no DOI, and no identity conflict.')
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
