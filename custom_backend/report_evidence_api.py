"""Optional report relationships and manual synthesis workspaces.

Personal proposals remain actor scoped. Shared adjudication requires an explicit
reconciler grant and paused independent work in team tasks. No eligibility,
coding, study registration or statistical result is rewritten by these routes.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from typing import Literal

from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from coscreen import research_trace, task_collaboration, review_workflow

LIMIT = 2_000_000
# 轨迹写入预算：单条事件增量载荷超过前者、或单次保存累计超过后者时，降级为摘要+哈希，
# 避免大工作区保存造成轨迹写放大并把导出推过 32MB 上限（摘要契约见 REPORT_LINKS_AND_EVIDENCE 文档）。
TRACE_EVENT_BYTES_LIMIT = 850_000
TRACE_SAVE_BYTES_LIMIT = 2_000_000
# 单次保存按受影响主体记录事件的上限；超出部分补一条显式截断标记，不再静默丢弃。
TRACE_SUBJECT_LIMIT = 100
# 幂等去重日志按成员保留的最近操作数：网络重试窗口足够，表不再随保存次数无限增长。
REQUEST_JOURNAL_LIMIT = 200
TRACE_STAGES = ("screen", "fulltext", "coding")
DEFAULT = {"links": [], "drafts": [], "method": "jbi", "review": "quant",
           "interpretations": {}, "confidence": {}, "duplicatePairs": {},
           "question": {}, "appraisals": {}, "appraisalTool": "jbi", "analysisChoices": {}}
KINDS = {"study", "sample", "partial", "uncertain", "independent"}


class WriteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    operation_id: uuid.UUID
    state: dict
    stage: Literal["screen", "fulltext", "coding"] = "coding"
    label: str = Field(min_length=1, max_length=200)


class UndoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)


class RelationIn(UndoIn):
    actor_id: str = Field(min_length=1, max_length=100, pattern=r"^[1-9][0-9]*$")
    link_id: str = Field(min_length=1, max_length=100)
    action: Literal["confirm", "unlink"]
    # 可选：该次裁决动作归属的轨迹阶段；缺省 coding，并随共享撤销栈记录，供撤销复用。
    stage: Literal["screen", "fulltext", "coding"] = "coding"
    # 可选：仅 unlink 使用。同成员同 id 重复等极端场景下，携带关系对 (a, b) 以精确定位目标。
    a: str | None = Field(default=None, min_length=1, max_length=100)
    b: str | None = Field(default=None, min_length=1, max_length=100)


def _entry_subjects(entry):
    return {entry[k] for k in ("a", "b", "key") if k in entry} if entry else set()


def _redact_foreign_entry(entry, owner_actor):
    """非本人提案的 evidence/locator 不入库，导出自然不可见。

    locator 属低熵文本（如 "p.4"），sha256 可被候选枚举比对还原，直接丢弃只留长度；
    evidence 为高熵原文片段，保留哈希摘要与长度供裁决人比对一致性而不读到原文。
    个人工作区的条目不含 actor_id（validate 拒绝该字段），自然按本人处理、不脱敏。
    """
    if str(entry.get("actor_id", owner_actor)) == str(owner_actor):
        return entry
    cleaned = dict(entry)
    for field in ("evidence", "locator"):
        text = cleaned.get(field)
        if isinstance(text, str) and text:
            summary = {"redacted": "other_member_private_text", "length": len(text)}
            if field == "evidence":
                summary["sha256"] = hashlib.sha256(text.encode()).hexdigest()
            cleaned[field] = summary
    return cleaned


def _workspace_deltas(before, after, owner_actor):
    """计算按受影响主体分组的增量条目与通用映射增量；新写入路径与存量迁移共用。

    返回 (deltas, map_before, map_after)：deltas[主体] = (before 条目, after 条目)，
    每侧形如 {"links": [...], "drafts": [...]}，已按 owner_actor 脱敏非本人提案。
    """
    deltas = {}
    for field in ("links", "drafts"):
        old = {x["id"]: x for x in before.get(field, [])}
        new = {x["id"]: x for x in after.get(field, [])}
        for entry_id in set(old) | set(new):
            old_entry, new_entry = old.get(entry_id), new.get(entry_id)
            if old_entry == new_entry:
                continue
            for subject in _entry_subjects(old_entry) | _entry_subjects(new_entry):
                pair = deltas.setdefault(subject, ({"links": [], "drafts": []}, {"links": [], "drafts": []}))
                if old_entry:
                    pair[0][field].append(_redact_foreign_entry(old_entry, owner_actor))
                if new_entry:
                    pair[1][field].append(_redact_foreign_entry(new_entry, owner_actor))
    map_before, map_after = {}, {}
    for name in ("review", "method", "appraisalTool", "interpretations", "confidence",
                 "duplicatePairs", "question", "appraisals", "analysisChoices"):
        old_value, new_value = before.get(name), after.get(name)
        if old_value == new_value:
            continue
        if isinstance(old_value, dict) and isinstance(new_value, dict):
            for key in set(old_value) | set(new_value):
                if old_value.get(key) == new_value.get(key):
                    continue
                map_before.setdefault(name, {})[str(key)] = old_value.get(key)
                map_after.setdefault(name, {})[str(key)] = new_value.get(key)
        else:
            map_before[name] = old_value
            map_after[name] = new_value
    return deltas, map_before, map_after


def _legacy_snapshot_digest(label, workspace):
    """存量全量快照的降级摘要：与新写入路径的超预算摘要同构。"""
    return {"value": label, "workspace_sha256": hashlib.sha256(
                json.dumps(workspace, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
            "links": len(workspace.get("links", [])), "findings": len(workspace.get("drafts", [])),
            "note": "Legacy full workspace snapshot compacted by one-time migration; export JSON to inspect current details."}


def _redact_workspace(workspace, owner_actor):
    """整份工作区快照按归属人脱敏：仅处理 links，非本人提案的私有文本替换为摘要。"""
    cleaned = dict(workspace)
    if isinstance(cleaned.get("links"), list):
        cleaned["links"] = [_redact_foreign_entry(link, owner_actor)
                            if isinstance(link, dict) else link for link in cleaned["links"]]
    return cleaned


def compact_legacy_trace_events(c):
    """一次性迁移 2.1.3 写入的全量快照轨迹事件（幂等，带完成标记，可安全重入）。

    旧版每次保存把整份工作区快照嵌入每个受影响主体事件的 before/after
    （{"value": 标签, "workspace": {...}}）：既会把轨迹导出顶过 32MB 上限（永久 413），
    也让共享裁决轨迹中其他成员的 evidence/locator 原文长期留在库内、随公开导出外带。
    此处把每条旧事件改写为与当前写入路径一致的按主体增量条目（单个主体增量超过
    TRACE_EVENT_BYTES_LIMIT 时降级为摘要+哈希），并按事件归属人脱敏非本人提案文本；
    同一次旧保存产生的多条事件共享相同快照，通用映射增量只保留一份避免重复。
    迁移只改写 before_json/after_json 载荷，事件序号、时间、阶段、主体等元数据不变。
    """
    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trace_events'").fetchone() is None:
        return
    own = not c.in_transaction
    if own:
        c.execute("BEGIN IMMEDIATE")
    try:
        c.execute("CREATE TABLE IF NOT EXISTS report_evidence_trace_meta "
                  "(singleton INTEGER PRIMARY KEY CHECK(singleton=1), legacy_compacted_at TEXT NOT NULL)")
        if c.execute("SELECT 1 FROM report_evidence_trace_meta WHERE singleton=1").fetchone():
            if own:
                c.commit()
            return
        # 旧格式特征：载荷含精确键名 "workspace"（新格式的 workspace_sha256 不会命中该模式）。
        rows = c.execute(
            "SELECT sequence,actor_id,subject_id,before_json,after_json FROM trace_events "
            "WHERE action IN ('report_evidence_changed','imported_report_evidence_changed') "
            "AND (before_json LIKE '%\"workspace\"%' OR after_json LIKE '%\"workspace\"%') ORDER BY sequence"
        ).fetchall()
        groups = {}
        for row in rows:
            try:
                before_payload = json.loads(row[3]) if row[3] is not None else None
                after_payload = json.loads(row[4]) if row[4] is not None else None
            except (TypeError, ValueError):
                continue  # 载荷损坏的事件保持原样，不中断迁移
            if not isinstance(before_payload, dict) or not isinstance(after_payload, dict):
                continue
            groups.setdefault((row[1], row[3], row[4]), []).append(
                (row[0], row[2], before_payload, after_payload))
        for (_actor, _raw_before, _raw_after), events in groups.items():
            actor = str(_actor)
            label = events[0][2].get("value") or events[0][3].get("value") or ""
            w_before = events[0][2].get("workspace") if isinstance(events[0][2].get("workspace"), dict) else None
            w_after = events[0][3].get("workspace") if isinstance(events[0][3].get("workspace"), dict) else None
            if w_before is None or w_after is None:
                # 任一侧已是旧版摘要形态时无法重算增量：两侧统一降级为摘要（脱敏后再哈希）。
                redacted_before = _redact_workspace(w_before or {}, actor)
                redacted_after = _redact_workspace(w_after or {}, actor)
                for sequence, _subject, _bp, _ap in events:
                    c.execute("UPDATE trace_events SET before_json=?, after_json=? WHERE sequence=?",
                              (json.dumps(_legacy_snapshot_digest(label, redacted_before), ensure_ascii=False, sort_keys=True),
                               json.dumps(_legacy_snapshot_digest(label, redacted_after), ensure_ascii=False, sort_keys=True),
                               sequence))
                continue
            redacted_before = _redact_workspace(w_before, actor)
            redacted_after = _redact_workspace(w_after, actor)
            deltas, map_before, map_after = _workspace_deltas(redacted_before, redacted_after, actor)
            digest_before = _legacy_snapshot_digest(label, redacted_before)
            digest_after = _legacy_snapshot_digest(label, redacted_after)
            map_carrier = next((e for e in events if e[1] == "report-evidence"), events[0])
            for sequence, subject, _bp, _ap in events:
                before_payload = after_payload = None
                if subject in deltas:
                    before_payload = {"value": label, "changes": deltas[subject][0]}
                    after_payload = {"value": label, "changes": deltas[subject][1]}
                if (map_before or map_after) and sequence == map_carrier[0]:
                    merged_before = dict(before_payload or {"value": label, "changes": {}})
                    merged_after = dict(after_payload or {"value": label, "changes": {}})
                    merged_changes_before = dict(merged_before["changes"]); merged_changes_before.update(map_before)
                    merged_changes_after = dict(merged_after["changes"]); merged_changes_after.update(map_after)
                    merged_before["changes"] = merged_changes_before
                    merged_after["changes"] = merged_changes_after
                    before_payload, after_payload = merged_before, merged_after
                if before_payload is None:
                    before_payload, after_payload = digest_before, digest_after
                size = (len(json.dumps(before_payload, ensure_ascii=False).encode())
                        + len(json.dumps(after_payload, ensure_ascii=False).encode()))
                if size > TRACE_EVENT_BYTES_LIMIT:
                    before_payload, after_payload = digest_before, digest_after
                c.execute("UPDATE trace_events SET before_json=?, after_json=? WHERE sequence=?",
                          (json.dumps(before_payload, ensure_ascii=False, sort_keys=True),
                           json.dumps(after_payload, ensure_ascii=False, sort_keys=True), sequence))
        c.execute("INSERT INTO report_evidence_trace_meta VALUES(1, ?)",
                  (datetime.now(timezone.utc).isoformat(timespec="milliseconds"),))
        if own:
            c.commit()
    except Exception:
        if own:
            c.rollback()
        raise


def connect(path):
    c = sqlite3.connect(path, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=15000")
    c.execute("CREATE TABLE IF NOT EXISTS report_evidence_state (actor TEXT PRIMARY KEY, revision INTEGER NOT NULL, state TEXT NOT NULL, previous TEXT, previous_stage TEXT)")
    # 旧库迁移：为撤销栈补记保存时的轨迹阶段，使撤销事件与对应保存落在同一阶段。
    if "previous_stage" not in {r[1] for r in c.execute("PRAGMA table_info(report_evidence_state)")}:
        c.execute("ALTER TABLE report_evidence_state ADD COLUMN previous_stage TEXT")
    c.execute("CREATE TABLE IF NOT EXISTS report_evidence_requests (actor TEXT, operation TEXT, digest TEXT NOT NULL, PRIMARY KEY(actor,operation))")
    c.commit()
    # 2.1.3 存量全量快照轨迹事件的一次性压缩/脱敏迁移（幂等；无旧事件时仅一次空扫描后置标记）。
    compact_legacy_trace_events(c)
    return c


def read(c, actor):
    row = c.execute("SELECT * FROM report_evidence_state WHERE actor=?", (actor,)).fetchone()
    return (row["revision"], json.loads(row["state"]), bool(row["previous"])) if row else (0, json.loads(json.dumps(DEFAULT)), False)


def validate(state, report_keys):
    if set(state) - set(DEFAULT):
        raise HTTPException(422, "Unknown workspace fields.")
    value = {**json.loads(json.dumps(DEFAULT)), **state}
    if len(json.dumps(value, ensure_ascii=False).encode()) > LIMIT:
        raise HTTPException(413, "Workspace exceeds 2 MB; export and split the evidence set.")
    def bounded(v, depth=0):
        if depth > 8:
            raise HTTPException(422, "Workspace nesting is too deep.")
        if isinstance(v, str):
            if len(v) > 20000:
                raise HTTPException(422, "A workspace field exceeds 20000 characters.")
        elif isinstance(v, dict):
            if len(v) > 5000 or any(not isinstance(k, str) or len(k) > 400 for k in v):
                raise HTTPException(422, "Invalid workspace map.")
            for x in v.values(): bounded(x, depth + 1)
        elif isinstance(v, list):
            if len(v) > 5000: raise HTTPException(422, "Too many workspace entries.")
            for x in v: bounded(x, depth + 1)
        elif v is not None and not isinstance(v, (bool, int)):
            raise HTTPException(422, "Workspace values must be text, integer, boolean or containers.")
    bounded(value)
    if value["review"] not in {"quant", "qual", "mixed"} or value["method"] not in {"jbi", "thematic", "framework", "ethno"}:
        raise HTTPException(422, "Unknown review path or method.")
    if value["appraisalTool"] not in {"jbi", "casp"}:
        raise HTTPException(422, "Unknown appraisal tool.")
    for field in ("links", "drafts"):
        if not isinstance(value[field], list): raise HTTPException(422, "Expected workspace list.")
        ids = set()
        for entry in value[field]:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", entry["id"]) or entry["id"] in ids:
                raise HTTPException(422, "Every entry needs a unique ID.")
            ids.add(entry["id"])
            if field == "links":
                if set(entry) - {"id", "a", "b", "type", "same", "evidence", "locator", "status"}:
                    raise HTTPException(422, "Unknown relation fields.")
                if entry.get("a") not in report_keys or entry.get("b") not in report_keys or entry["a"] == entry["b"]:
                    raise HTTPException(422, "Choose two different active reports in this task.")
                if entry.get("type") not in KINDS or entry.get("status", "mine") != "mine" or type(entry.get("same", False)) is not bool:
                    raise HTTPException(422, "A personal proposal cannot declare itself confirmed.")
                entry["status"] = "mine"
            else:
                if set(entry) - {"id", "key", "finding", "quote", "locator", "context", "theme", "interpretation", "credibility"} or entry.get("key") not in report_keys:
                    raise HTTPException(422, "Invalid evidence report or fields.")
                if not isinstance(entry.get("finding"), str) or not entry["finding"].strip():
                    raise HTTPException(422, "A finding is required.")
                if entry.get("credibility", "") not in {"", "unequivocal", "credible", "unsupported"}:
                    raise HTTPException(422, "Invalid finding credibility.")
    for name in ("interpretations", "confidence", "duplicatePairs", "question", "appraisals", "analysisChoices"):
        if not isinstance(value[name], dict): raise HTTPException(422, "Expected workspace map.")
    for name in ("links", "drafts"):
        for entry in value[name]:
            for k, v in entry.items():
                if k != "same" and not isinstance(v, str): raise HTTPException(422, "Entry fields must be text.")
    draft_ids = {d["id"] for d in value["drafts"]}
    for pair, choice in value["duplicatePairs"].items():
        ids = pair.split("|")
        if len(ids) != 2 or not all(i in draft_ids for i in ids) or choice not in {"same", "different", "uncertain"}:
            raise HTTPException(422, "Evidence comparison refers to missing findings.")
    for key, assessment in value["confidence"].items():
        if key.split("|", 1)[0] not in {"jbi", "thematic", "framework", "ethno"} or not isinstance(assessment, dict) or assessment.get("overall", "") not in {"", "high", "moderate", "low", "verylow"}:
            raise HTTPException(422, "Invalid confidence method or judgment.")
    for key, assessment in value["appraisals"].items():
        parts = key.split("|", 1)
        if len(parts) != 2 or parts[0] not in {"jbi", "casp"} or parts[1] not in report_keys or not isinstance(assessment, dict):
            raise HTTPException(422, "Appraisal requires a task report and known tool.")
        if not isinstance(assessment.get("items", {}), dict):
            raise HTTPException(422, "Appraisal items must be a map.")
        for index, item in assessment.get("items", {}).items():
            allowed = {"", "yes", "no", "unclear", "na"} if parts[0] == "jbi" else {"", "yes", "no", "unclear"}
            if index not in {str(i) for i in range(10)} or not isinstance(item, dict) or item.get("judgment", "") not in allowed:
                raise HTTPException(422, "Invalid appraisal item or judgment.")
    return value


def register_report_evidence_routes(app, require_user):
    async def size_guard(request: Request):
        # 先按 Content-Length 提前拒绝超限请求，避免把明显超过 2MB 的请求体完整读入内存后再比较；
        # 头部缺失或不合法时仍回退到读取实体后的精确校验。
        header = request.headers.get("content-length")
        if header:
            try:
                length = int(header)
            except ValueError:
                length = None
            if length is not None and length > LIMIT + 4096:
                raise HTTPException(413, "Workspace request exceeds 2 MB.")
        if len(await request.body()) > LIMIT + 4096:
            raise HTTPException(413, "Workspace request exceeds 2 MB.")

    def context(task_id, user):
        ctx = app.state.review_workflow.context(task_id, user)
        if not ctx.owner:
            raise HTTPException(409, "Assign a verified task owner before using report relationships.")
        actor = str(user["user_id"])
        allowed = ctx.can_manage or task_collaboration.authorize_action(ctx.collaboration_db, user["user_id"], "coding:submit", task_id=task_id)
        adjudicator = task_collaboration.authorize_action(ctx.collaboration_db, user["user_id"], "consensus:write", task_id=task_id)
        policy = review_workflow.settings(ctx.owner_db)
        independent = ctx.task_kind == "team" and any(policy["stages"][s]["mode"] != "paused" for s in ("screen", "fulltext", "coding"))
        ctx.report_evidence_shared_visible = not independent
        return ctx, actor, bool(allowed and not ctx.info.degraded), bool(adjudicator and not independent and not ctx.info.degraded)

    def reports(c):
        rows = [dict(r) for r in c.execute("SELECT zotero_key,title,authors,year,journal,doi,raw_json FROM articles WHERE is_duplicate_of IS NULL ORDER BY title,zotero_key")]
        sources = {}
        if review_workflow.has_table(c, "workflow_article_sources"):
            for r in c.execute("SELECT a.zotero_key,s.name FROM workflow_article_sources a JOIN workflow_sources s ON s.id=a.source_id WHERE a.source_id!=? ORDER BY s.name", (review_workflow.UNASSIGNED,)):
                sources.setdefault(r[0], []).append(r[1])
        for r in rows:
            try: raw = json.loads(r.pop("raw_json") or "{}")
            except (TypeError, ValueError): raw = {}
            if not isinstance(raw, dict): raw = {}
            r.update(key=r["zotero_key"], id=r["zotero_key"], author=r["authors"], source="; ".join(sources.get(r["zotero_key"], [])) or raw.get("source_file", ""))
        return rows

    def response(c, actor, write, adjudicate, *, shared_visible=False):
        revision, state, undo = read(c, actor)
        shared_revision, shared, shared_undo = read(c, "*")
        result = {"revision": revision, "state": state, "undo_available": undo,
                  "shared_revision": shared_revision, "shared_undo_available": shared_undo and adjudicate,
                  "confirmed": shared["links"] if shared_visible else [],
                  "can_write": write, "can_adjudicate": adjudicate}
        if adjudicate:
            result["proposals"] = [{**link, "actor_id": row["actor"]} for row in c.execute("SELECT actor,state FROM report_evidence_state WHERE actor!='*'") for link in json.loads(row["state"])["links"]]
        return result

    def fresh_permission(c, ctx, user, *, shared=False):
        action = "consensus:write" if shared else "coding:submit"
        if not task_collaboration.authorize_action(ctx.collaboration_db, user["user_id"], action, task_id=ctx.info.task_id):
            raise HTTPException(403, "Task role changed; reload before saving.")
        policy = c.execute("SELECT team_latched,stages_json FROM workflow_state WHERE singleton=1").fetchone()
        if shared and policy[0] and any(v["mode"] != "paused" for v in json.loads(policy[1]).values()):
            raise HTTPException(409, "Independent stages resumed; relationship adjudication is paused.")

    def trace(c, ctx, actor, label, before, after, stage="coding"):
        """按受影响主体只记录增量条目，替代旧版每事件内嵌完整工作区快照。

        单条事件增量超过 TRACE_EVENT_BYTES_LIMIT、或单次保存累计超过
        TRACE_SAVE_BYTES_LIMIT 时，降级为摘要+哈希（保持既有文档契约：
        大工作区轨迹保存摘要与哈希，完整内容通过 JSON 导出检查）。
        超过 TRACE_SUBJECT_LIMIT 个受影响主体时补一条显式截断标记事件。
        共享工作区轨迹只记录与该主体相关的条目；脱敏按条目归属判定
        （非本人提案只留摘要，locator 低熵直接丢弃，见 _redact_foreign_entry）。
        """
        tc = research_trace.TraceContext(ctx.info.task_id, actor, source="report_evidence")
        research_trace.ensure_default_recording(c, tc)

        # 增量计算与脱敏规则同存量迁移共用（_workspace_deltas）：
        # 每个受影响主体只携带自己的 links/drafts 增量条目，映射字段进通用主体。
        deltas, map_before, map_after = _workspace_deltas(before, after, actor)

        digest_cache = {}

        def digest_state(side):
            if side not in digest_cache:
                value = after if side == "after" else before
                encoded = json.dumps(value, ensure_ascii=False).encode()
                digest_cache[side] = {"value": label, "workspace_sha256": hashlib.sha256(encoded).hexdigest(),
                                      "links": len(value.get("links", [])), "findings": len(value.get("drafts", [])),
                                      "note": "Large workspace: export JSON to inspect current details."}
            return digest_cache[side]

        budget = 0

        def emit(subject, before_payload, after_payload):
            nonlocal budget
            size = (len(json.dumps(before_payload, ensure_ascii=False).encode())
                    + len(json.dumps(after_payload, ensure_ascii=False).encode()))
            if size > TRACE_EVENT_BYTES_LIMIT or budget + size > TRACE_SAVE_BYTES_LIMIT:
                before_payload, after_payload = digest_state("before"), digest_state("after")
                size = (len(json.dumps(before_payload, ensure_ascii=False).encode())
                        + len(json.dumps(after_payload, ensure_ascii=False).encode()))
            budget += size
            research_trace.capture_event(c, tc, stage=stage, action="report_evidence_changed",
                                         subject_id=subject, before=before_payload, after=after_payload)

        subjects = sorted(deltas)
        for subject in subjects[:TRACE_SUBJECT_LIMIT]:
            before_entries, after_entries = deltas[subject]
            emit(subject, {"value": label, "changes": before_entries}, {"value": label, "changes": after_entries})
        if map_before or map_after:
            emit("report-evidence", {"value": label, "changes": map_before}, {"value": label, "changes": map_after})
        if len(subjects) > TRACE_SUBJECT_LIMIT:
            # 显式截断标记：不再静默丢弃第 101 位及之后的受影响主体。
            research_trace.capture_event(
                c, tc, stage=stage, action="report_evidence_truncated", subject_id="report-evidence", before=None,
                after={"value": label, "total_subjects": len(subjects),
                       "truncated_subjects": len(subjects) - TRACE_SUBJECT_LIMIT,
                       "note": f"Only the first {TRACE_SUBJECT_LIMIT} affected subjects are traced; "
                               f"{len(subjects) - TRACE_SUBJECT_LIMIT} more changed subjects are omitted."})

    @app.get("/api/tasks/{task_id}/report-evidence")
    def get_state(task_id: str, catalogue: bool = Query(False), user: dict = Depends(require_user)):
        ctx, actor, write, adjudicate = context(task_id, user)
        with closing(connect(ctx.owner_db)) as c:
            data = response(c, actor, write, adjudicate, shared_visible=ctx.report_evidence_shared_visible)
            if catalogue: data["reports"] = reports(c)
            return data

    @app.put("/api/tasks/{task_id}/report-evidence", dependencies=[Depends(size_guard)])
    def save_state(task_id: str, body: WriteIn, user: dict = Depends(require_user)):
        ctx, actor, write, adjudicate = context(task_id, user)
        if not write: raise HTTPException(403, "A coding role is required to save personal evidence.")
        with closing(connect(ctx.owner_db)) as c, c:
            c.execute("BEGIN IMMEDIATE")
            fresh_permission(c, ctx, user, shared=False)
            state = validate(body.state, {r[0] for r in c.execute("SELECT zotero_key FROM articles WHERE is_duplicate_of IS NULL")})
            encoded = json.dumps(state, ensure_ascii=False, sort_keys=True)
            digest = hashlib.sha256(encoded.encode()).hexdigest()
            old_request = c.execute("SELECT digest FROM report_evidence_requests WHERE actor=? AND operation=?", (actor, str(body.operation_id))).fetchone()
            if old_request:
                if old_request[0] != digest: raise HTTPException(409, "Operation ID reused with different content.")
                return response(c, actor, write, adjudicate, shared_visible=ctx.report_evidence_shared_visible)
            revision, before, _ = read(c, actor)
            if revision != body.expected_revision: raise HTTPException(409, "Evidence changed in another window. Reload before saving.")
            trace(c, ctx, actor, body.label, before, state, body.stage)
            c.execute("INSERT INTO report_evidence_state VALUES(?,?,?,?,?) ON CONFLICT(actor) DO UPDATE SET revision=excluded.revision,state=excluded.state,previous=excluded.previous,previous_stage=excluded.previous_stage", (actor, revision + 1, encoded, json.dumps(before), body.stage))
            c.execute("INSERT INTO report_evidence_requests VALUES(?,?,?)", (actor, str(body.operation_id), digest))
            # 幂等日志按成员限留最近 REQUEST_JOURNAL_LIMIT 条；被清理的旧 operation 不能再重放，
            # 但其重试窗口早已过期，且内容校验（digest 不一致 409）依然兜底。
            c.execute("DELETE FROM report_evidence_requests WHERE actor=? AND rowid NOT IN "
                      "(SELECT rowid FROM report_evidence_requests WHERE actor=? ORDER BY rowid DESC LIMIT ?)",
                      (actor, actor, REQUEST_JOURNAL_LIMIT))
            return response(c, actor, write, adjudicate, shared_visible=ctx.report_evidence_shared_visible)

    @app.post("/api/tasks/{task_id}/report-evidence/undo")
    def undo_state(task_id: str, body: UndoIn, user: dict = Depends(require_user)):
        ctx, actor, write, adjudicate = context(task_id, user)
        if not write: raise HTTPException(403, "Evidence is read only.")
        with closing(connect(ctx.owner_db)) as c, c:
            c.execute("BEGIN IMMEDIATE")
            fresh_permission(c, ctx, user, shared=False)
            revision, before, _ = read(c, actor)
            row = c.execute("SELECT previous,previous_stage FROM report_evidence_state WHERE actor=?", (actor,)).fetchone()
            if revision != body.expected_revision: raise HTTPException(409, "Evidence changed. Reload before undo.")
            if not row or not row[0]: raise HTTPException(409, "No personal change is available to undo.")
            restored = validate(json.loads(row[0]), {r[0] for r in c.execute("SELECT zotero_key FROM articles WHERE is_duplicate_of IS NULL")})
            # 撤销与对应保存使用同一轨迹阶段，避免阶段收窄后撤销事件被静默过滤。
            undo_stage = row[1] if row[1] in TRACE_STAGES else "coding"
            trace(c, ctx, actor, "撤销报告关联与证据变更", before, restored, stage=undo_stage)
            c.execute("UPDATE report_evidence_state SET revision=?,state=?,previous=NULL,previous_stage=NULL WHERE actor=?", (revision + 1, json.dumps(restored), actor))
            return response(c, actor, write, adjudicate, shared_visible=ctx.report_evidence_shared_visible)

    @app.post("/api/tasks/{task_id}/report-evidence/relations")
    def relation(task_id: str, body: RelationIn, user: dict = Depends(require_user)):
        ctx, actor, write, adjudicate = context(task_id, user)
        if not adjudicate: raise HTTPException(403, "Relationship adjudication requires an owner/reconciler and paused independent stages in team tasks.")
        with closing(connect(ctx.owner_db)) as c, c:
            c.execute("BEGIN IMMEDIATE")
            fresh_permission(c, ctx, user, shared=True)
            revision, shared, _ = read(c, "*")
            if revision != body.expected_revision: raise HTTPException(409, "Confirmed relationships changed. Reload first.")
            before = json.loads(json.dumps(shared))
            if body.action == "confirm":
                # 确认前复核提案人当前仍是具有编码提交权的成员：fresh_permission 只校验调用者，
                # 已被撤销角色的成员遗留提案不得经确认进入共享库（其内容仍保留在个人工作区）。
                if not task_collaboration.authorize_action(ctx.collaboration_db, int(body.actor_id),
                                                           "coding:submit", task_id=ctx.info.task_id):
                    raise HTTPException(409, "Proposal author is no longer an active coding member; confirm an active member's proposal.")
                _, personal, _ = read(c, body.actor_id)
                proposal = next((x for x in personal["links"] if x["id"] == body.link_id), None)
                if not proposal: raise HTTPException(404, "Proposal no longer exists.")
                if proposal["type"] == "uncertain" or not proposal.get("evidence", "").strip(): raise HTTPException(422, "Confirm the relationship and supply supporting evidence first.")
                keys = {r[0] for r in c.execute("SELECT zotero_key FROM articles WHERE is_duplicate_of IS NULL")}
                if proposal["a"] not in keys or proposal["b"] not in keys: raise HTTPException(409, "A report is no longer active.")
                pair = {proposal["a"], proposal["b"]}
                shared["links"] = [l for l in shared["links"] if {l["a"], l["b"]} != pair]
                # 共享 id 与提案保持一致（不改写、不注入提案人 user id），前端按 id+字段比对
                # 已确认关系的逻辑不受影响；跨成员同 id 的已确认关系可以并存，由 unlink 唯一定位。
                shared["links"].append({**proposal, "status": "agreed", "actor_id": body.actor_id})
            else:
                # validate 只保证单个成员工作区内 id 唯一，共享库可能存在（含 2.1.3 已落库的）
                # 同 id 已确认关系；仅按 id 匹配会一次解除所有同 id 关系。至少按 (id, actor_id)
                # 定位，请求同时携带 a/b 时再按关系对精确匹配；仍歧义时明确 409 而非批量删除。
                def unlink_match(link):
                    if link["id"] != body.link_id or str(link.get("actor_id", "")) != str(body.actor_id): return False
                    return body.a is None or body.b is None or {link.get("a"), link.get("b")} == {body.a, body.b}
                if not any(unlink_match(l) for l in shared["links"]): raise HTTPException(404, "Confirmed relationship no longer exists.")
                if sum(1 for l in shared["links"] if unlink_match(l)) > 1:
                    raise HTTPException(409, "Ambiguous confirmed relationship; resend with its report pair (a, b) to unlink exactly one.")
                shared["links"] = [l for l in shared["links"] if not unlink_match(l)]
            if shared == before: return response(c, actor, write, adjudicate, shared_visible=ctx.report_evidence_shared_visible)
            trace(c, ctx, actor, "确认报告关系" if body.action == "confirm" else "解除报告关系", before, shared, stage=body.stage)
            c.execute("INSERT INTO report_evidence_state VALUES('*',?,?,?,?) ON CONFLICT(actor) DO UPDATE SET revision=excluded.revision,state=excluded.state,previous=excluded.previous,previous_stage=excluded.previous_stage", (revision + 1, json.dumps(shared), json.dumps(before), body.stage))
            return response(c, actor, write, adjudicate, shared_visible=ctx.report_evidence_shared_visible)

    @app.post("/api/tasks/{task_id}/report-evidence/relations/undo")
    def undo_shared(task_id: str, body: UndoIn, user: dict = Depends(require_user)):
        ctx, actor, write, adjudicate = context(task_id, user)
        if not adjudicate: raise HTTPException(403, "Relationship adjudication is unavailable.")
        with closing(connect(ctx.owner_db)) as c, c:
            c.execute("BEGIN IMMEDIATE")
            fresh_permission(c, ctx, user, shared=True)
            revision, before, _ = read(c, "*")
            row = c.execute("SELECT previous,previous_stage FROM report_evidence_state WHERE actor='*'").fetchone()
            if revision != body.expected_revision or not row or not row[0]: raise HTTPException(409, "No current relationship change is available to undo.")
            restored = json.loads(row[0])
            keys = {r[0] for r in c.execute("SELECT zotero_key FROM articles WHERE is_duplicate_of IS NULL")}
            if any(l["a"] not in keys or l["b"] not in keys for l in restored["links"]):
                raise HTTPException(409, "A report is no longer active; old relationships cannot be restored.")
            # 撤销与对应裁决动作使用同一轨迹阶段（随共享撤销栈记录）。
            undo_stage = row[1] if row[1] in TRACE_STAGES else "coding"
            trace(c, ctx, actor, "撤销报告关系裁决", before, restored, stage=undo_stage)
            c.execute("UPDATE report_evidence_state SET revision=?,state=?,previous=NULL,previous_stage=NULL WHERE actor='*'", (revision + 1, json.dumps(restored)))
            return response(c, actor, write, adjudicate, shared_visible=ctx.report_evidence_shared_visible)

    @app.get("/api/tasks/{task_id}/report-evidence/export")
    def export(task_id: str, format: Literal["csv", "json"] = "csv", user: dict = Depends(require_user)):
        ctx, actor, _, _ = context(task_id, user)
        with closing(connect(ctx.owner_db)) as c:
            _, state, _ = read(c, actor)
            catalogue = {r["key"]: r for r in reports(c)}
            if format == "json":
                return Response(content=json.dumps({"schema": "reviewflow.report-evidence/1", "state": state, "reports": list(catalogue.values())}, ensure_ascii=False).encode(), media_type="application/json", headers={"Content-Disposition": 'attachment; filename="report-evidence.json"'})
            out = io.StringIO(newline="")
            w = csv.writer(out)
            w.writerow(["Report_ID", "Title", "DOI", "Journal", "Author(s)", "Original_Source_File", "Finding", "Supporting_Illustration", "Locator", "Context", "Theme", "Reviewer_Interpretation", "Credibility"])
            for f in state["drafts"]:
                r = catalogue.get(f["key"], {})
                values = [f["key"], r.get("title"), r.get("doi"), r.get("journal"), r.get("author"), r.get("source"), *[f.get(k) for k in ("finding", "quote", "locator", "context", "theme", "interpretation", "credibility")]]
                w.writerow(["'" + str(v) if str(v or "").lstrip().startswith(("=", "+", "-", "@")) else v for v in values])
            return Response(content=("\ufeff" + out.getvalue()).encode(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="report-evidence.csv"'})
