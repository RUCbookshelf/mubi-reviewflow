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
import os
import re
import sqlite3
import uuid
from collections import Counter
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
# 存量折叠迁移：迁移前备份后缀与完成标记表（fold-v1）。
FOLD_BACKUP_SUFFIX = ".pre-fold-v1.bak"
_FOLD_META_TABLE = "report_evidence_fold_meta"
# 旧全量快照事件的扫描 SQL：载荷含精确键名 "workspace"（新格式/摘要的 workspace_sha256
# 与 note 文本都不会命中该 LIKE 模式）。
_LEGACY_SCAN_SQL = (
    "SELECT sequence,task_id,actor_id,stage,action,subject_id,before_json,after_json,origin_event_id "
    "FROM trace_events WHERE action IN ('report_evidence_changed','imported_report_evidence_changed') "
    "AND (before_json LIKE '%\"workspace\"%' OR after_json LIKE '%\"workspace\"%') ORDER BY sequence")
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
    # 主体键统一为字符串：增量分组与排序不再依赖载荷内的原始类型。
    return {str(entry[k]) for k in ("a", "b", "key") if k in entry} if entry else set()


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
    """计算按受影响主体分组的增量条目与通用映射增量；新写入路径与存量折叠共用。

    links/drafts 按多重集合比较：完全相同的条目先逐一抵消，剩余条目再按
    (id, actor_id) 键配对（多余的一侧记为删除/新增）。跨成员同 id 提案与
    已落库的同 id 重复共享关系因此都能得到正确增量——unlink/undo 删除任一条
    同 id 关系必然留痕，也不会把仍然存在的关系误记为本次新增或替换。
    返回 (deltas, map_before, map_after)：deltas[主体] = (before 条目, after 条目)，
    每侧形如 {"links": [...], "drafts": [...]}，已按 owner_actor 脱敏非本人提案。
    """
    def canonical(entry):
        return json.dumps(entry, ensure_ascii=False, sort_keys=True)

    def usable_entries(value):
        # 非法条目（非 dict/缺 id）不参与增量：写入路径经 validate 保证合法，
        # 折叠路径对这类形态已整体跳过（见 _legacy_workspace_foldable），此处仅防御。
        return [x for x in (value or []) if isinstance(x, dict) and "id" in x]

    def multiset_leftovers(old_list, new_list):
        old_entries, new_entries = usable_entries(old_list), usable_entries(new_list)
        new_counts = Counter(canonical(x) for x in new_entries)
        old_counts = Counter(canonical(x) for x in old_entries)
        kept_old = []
        for entry in old_entries:
            key = canonical(entry)
            if new_counts[key] > 0:
                new_counts[key] -= 1
            else:
                kept_old.append(entry)
        kept_new = []
        for entry in new_entries:
            key = canonical(entry)
            if old_counts[key] > 0:
                old_counts[key] -= 1
            else:
                kept_new.append(entry)
        return kept_old, kept_new

    deltas = {}
    for field in ("links", "drafts"):
        kept_old, kept_new = multiset_leftovers(before.get(field), after.get(field))

        def pair_key(entry):
            return (str(entry.get("id", "")), str(entry.get("actor_id", "")))

        by_key_old, by_key_new = {}, {}
        for entry in kept_old:
            by_key_old.setdefault(pair_key(entry), []).append(entry)
        for entry in kept_new:
            by_key_new.setdefault(pair_key(entry), []).append(entry)
        for key in sorted(set(by_key_old) | set(by_key_new)):
            olds, news = by_key_old.get(key, []), by_key_new.get(key, [])
            for index in range(max(len(olds), len(news))):
                old_entry = olds[index] if index < len(olds) else None
                new_entry = news[index] if index < len(news) else None
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
            "links": len(workspace.get("links", []) or []), "findings": len(workspace.get("drafts", []) or []),
            "note": "Legacy full workspace snapshot compacted by one-time migration; export JSON to inspect current details."}


def _redact_workspace(workspace, owner_actor):
    """整份工作区快照按归属人脱敏：仅处理 links，非本人提案的私有文本替换为摘要。"""
    cleaned = dict(workspace)
    if isinstance(cleaned.get("links"), list):
        cleaned["links"] = [_redact_foreign_entry(link, owner_actor)
                            if isinstance(link, dict) else link for link in cleaned["links"]]
    return cleaned


def _table_exists(c, name):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _fold_marker_present(c):
    return (_table_exists(c, _FOLD_META_TABLE)
            and c.execute(f"SELECT 1 FROM {_FOLD_META_TABLE} WHERE singleton=1").fetchone() is not None)


def _classify_legacy_side(payload):
    """单侧旧载荷形态：full=内嵌全量快照；digest=旧摘要；foreign=与本迁移无关；malformed=畸形。"""
    if not isinstance(payload, dict):
        return "malformed"
    if "workspace" in payload:
        return "full" if isinstance(payload["workspace"], dict) else "malformed"
    if "workspace_sha256" in payload:
        return "digest"
    return "foreign"


def _legacy_workspace_foldable(workspace):
    """全量快照可折叠性检查：links/drafts 缺失或为含 id 的条目 dict 列表才可计算增量。"""
    for field in ("links", "drafts"):
        value = workspace.get(field)
        if value is None:
            continue
        if not isinstance(value, list):
            return False
        if any(not isinstance(entry, dict) or "id" not in entry for entry in value):
            return False
    return True


def _payload_pair_bytes(before_payload, after_payload):
    return (len(json.dumps(before_payload, ensure_ascii=False).encode())
            + len(json.dumps(after_payload, ensure_ascii=False).encode()))


def _rewrite_legacy_rows(c, rows, payload_pairs, *, fold_to_one):
    """只改写 before_json/after_json；fold_to_one 时删除同批多余的自有事件行。

    携带 origin 身份（origin_event_id 非空）的导入事件行绝不会被删除：
    origin_* 九列与 origin_sha256 一律不动，旧导出包重导入保持 duplicate 语义。
    删除语句本身再带 origin_event_id IS NULL 守卫，与批次键中的 origin 列互为双保险。
    """
    for row, (payload_before, payload_after) in zip(rows, payload_pairs):
        c.execute("UPDATE trace_events SET before_json=?, after_json=? WHERE sequence=?",
                  (json.dumps(payload_before, ensure_ascii=False, sort_keys=True),
                   json.dumps(payload_after, ensure_ascii=False, sort_keys=True),
                   row["sequence"]))
    if fold_to_one:
        for row in rows[len(payload_pairs):]:
            c.execute("DELETE FROM trace_events WHERE sequence=? AND origin_event_id IS NULL",
                      (row["sequence"],))


def _merged_batch_payloads(label, redacted_before, redacted_after, actor):
    """同一次保存全部主体的增量合并为单一载荷（changes 内为各主体条目并集 + 映射增量）。

    同一条目可同时属于多个主体（关系的 a 与 b 各算一个受影响主体）：按 canonical
    JSON 去重后并入，避免一次保存的关系条目在合并载荷里按主体数重复出现。
    """
    deltas, map_before, map_after = _workspace_deltas(redacted_before, redacted_after, actor)
    subjects = sorted(deltas)

    def merged_field(field, side_index):
        seen = set()
        merged = []
        for subject in subjects:
            for entry in deltas[subject][side_index][field]:
                key = json.dumps(entry, ensure_ascii=False, sort_keys=True)
                if key not in seen:
                    seen.add(key)
                    merged.append(entry)
        return merged

    changes_before = {"links": merged_field("links", 0), "drafts": merged_field("drafts", 0)}
    changes_after = {"links": merged_field("links", 1), "drafts": merged_field("drafts", 1)}
    changes_before.update(map_before)
    changes_after.update(map_after)
    before_payload = {"value": label, "changes": changes_before}
    after_payload = {"value": label, "changes": changes_after}
    if subjects:
        after_payload["affected_subjects"] = subjects
    return before_payload, after_payload


def _fold_one_run(c, run, budget):
    """折叠一个保存批次；返回 (状态, 删除行数)：folded / foreign（无关，原样保留）/ malformed（跳过）。"""
    rows = run["rows"]
    first = rows[0]
    try:
        before_payload = json.loads(first["before_json"]) if first["before_json"] is not None else None
        after_payload = json.loads(first["after_json"]) if first["after_json"] is not None else None
    except (TypeError, ValueError):
        return "malformed", 0
    kind_before = _classify_legacy_side(before_payload)
    kind_after = _classify_legacy_side(after_payload)
    if kind_before == "foreign" and kind_after == "foreign":
        return "foreign", 0
    if "malformed" in (kind_before, kind_after):
        return "malformed", 0
    if kind_before == "full" and not _legacy_workspace_foldable(before_payload["workspace"]):
        return "malformed", 0
    if kind_after == "full" and not _legacy_workspace_foldable(after_payload["workspace"]):
        return "malformed", 0
    if kind_before == "digest" and kind_after == "digest":
        return "foreign", 0  # 两侧均已是旧摘要：紧凑形态，无需折叠
    actor = str(first["actor_id"])
    label = str(before_payload.get("value") or after_payload.get("value") or "")
    redacted = {side: _redact_workspace(payload["workspace"], actor)
                for side, kind, payload in (("before", kind_before, before_payload),
                                            ("after", kind_after, after_payload)) if kind == "full"}
    native = first["action"] == "report_evidence_changed" and first["origin_event_id"] is None
    budget_key = (first["task_id"], actor)
    used = budget.get(budget_key, 0)

    def over_budget(payload_before, payload_after):
        size = _payload_pair_bytes(payload_before, payload_after)
        return size > TRACE_EVENT_BYTES_LIMIT or used + size > TRACE_SAVE_BYTES_LIMIT, size

    if kind_before == "full" and kind_after == "full":
        if native:
            # 自有事件：整批折叠为一条增量事件；超预算（单条或该成员累计）降级为摘要+哈希。
            payload_before, payload_after = _merged_batch_payloads(label, redacted["before"], redacted["after"], actor)
            exceeded, size = over_budget(payload_before, payload_after)
            if exceeded:
                payload_before = _legacy_snapshot_digest(label, redacted["before"])
                payload_after = _legacy_snapshot_digest(label, redacted["after"])
                size = _payload_pair_bytes(payload_before, payload_after)
            budget[budget_key] = used + size
            _rewrite_legacy_rows(c, rows, [(payload_before, payload_after)], fold_to_one=True)
            return "folded", len(rows) - 1
        # 导入事件：origin 身份行不可删除，逐行只保留各自主体的增量（首批附映射增量）。
        deltas, map_before, map_after = _workspace_deltas(redacted["before"], redacted["after"], actor)
        per_row = []
        row_used = used
        for index, row in enumerate(rows):
            subject = str(row["subject_id"])
            payload_before = payload_after = None
            if subject in deltas:
                payload_before = {"value": label, "changes": deltas[subject][0]}
                payload_after = {"value": label, "changes": deltas[subject][1]}
            if (map_before or map_after) and index == 0:
                base_before = dict(payload_before or {"value": label, "changes": {}})
                base_after = dict(payload_after or {"value": label, "changes": {}})
                base_before["changes"] = {**base_before["changes"], **map_before}
                base_after["changes"] = {**base_after["changes"], **map_after}
                payload_before, payload_after = base_before, base_after
            if payload_before is None:
                payload_before = _legacy_snapshot_digest(label, redacted["before"])
                payload_after = _legacy_snapshot_digest(label, redacted["after"])
            exceeded, size = over_budget(payload_before, payload_after)
            if exceeded:
                payload_before = _legacy_snapshot_digest(label, redacted["before"])
                payload_after = _legacy_snapshot_digest(label, redacted["after"])
                size = _payload_pair_bytes(payload_before, payload_after)
            row_used += size
            per_row.append((payload_before, payload_after))
        budget[budget_key] = row_used
        _rewrite_legacy_rows(c, rows, per_row, fold_to_one=False)
        return "folded", 0
    # 混合形态（一侧全量、一侧已是旧摘要）：摘要侧原样保留其 workspace_sha256 与
    # links/findings 计数（不得重算为空摘要），全量侧脱敏后降级为摘要。
    payload_before = before_payload if kind_before == "digest" else _legacy_snapshot_digest(label, redacted["before"])
    payload_after = after_payload if kind_after == "digest" else _legacy_snapshot_digest(label, redacted["after"])
    size = _payload_pair_bytes(payload_before, payload_after)
    budget[budget_key] = used + size
    if native:
        _rewrite_legacy_rows(c, rows, [(payload_before, payload_after)], fold_to_one=True)
        return "folded", len(rows) - 1
    _rewrite_legacy_rows(c, rows, [(payload_before, payload_after)] * len(rows), fold_to_one=False)
    return "folded", 0


def _fold_legacy_runs(c):
    """按保存批次折叠全部旧全量快照事件；返回 (折叠批次数, 删除行数, {(task,actor,stage): 跳过数})。

    同一次保存的多条事件共享完全相同的 before/after 快照且序号连续（单事务写入），
    以 (task,actor,stage,action,before_json,after_json,origin_event_id) 为键、序号
    连续性切分批次；不同保存（不同快照对）天然落进不同批次，不会被误并。origin_event_id
    入键使携带 origin 身份的行（即便动作键与自有事件相同）永远独立成批、只更新不删除。
    行读取按 SELECT 列序位置取值并 dict 化：本函数与调用方 row_factory 无关
    （导出钩子传入的 db._connect 连接不设 Row 工厂）。
    """
    # 先物化再折叠：避免在未走完的 SELECT 游标上 UPDATE/DELETE 同一张表。
    runs = []
    for row in c.execute(_LEGACY_SCAN_SQL).fetchall():
        record = {"sequence": row[0], "task_id": row[1], "actor_id": row[2], "stage": row[3],
                  "action": row[4], "subject_id": row[5], "before_json": row[6],
                  "after_json": row[7], "origin_event_id": row[8]}
        key = (record["task_id"], record["actor_id"], record["stage"], record["action"],
               record["before_json"], record["after_json"], record["origin_event_id"])
        if runs and runs[-1]["key"] == key and runs[-1]["last_sequence"] + 1 == record["sequence"]:
            runs[-1]["rows"].append(record)
            runs[-1]["last_sequence"] = record["sequence"]
        else:
            runs.append({"key": key, "rows": [record], "last_sequence": record["sequence"]})
    folded_runs = deleted_rows = 0
    skipped = {}
    budget = {}
    for run in runs:
        try:
            status, removed = _fold_one_run(c, run, budget)
        except Exception:
            status, removed = "malformed", 0  # M5：任何畸形/意外都只跳过该批，不中断迁移
        if status == "folded":
            folded_runs += 1
            deleted_rows += removed
        elif status == "malformed":
            first = run["rows"][0]
            skipped[(first["task_id"], str(first["actor_id"]), first["stage"])] = (
                skipped.get((first["task_id"], str(first["actor_id"]), first["stage"]), 0) + 1)
    return folded_runs, deleted_rows, skipped


def _insert_fold_marker_events(c, skipped):
    """畸形载荷跳过标记：沿用既有动作键 report_evidence_changed（不新增动作），按归属一批一条。"""
    store = c.execute("SELECT store_id FROM trace_store WHERE singleton=1").fetchone()
    if store is None:
        return
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    for (task_id, actor_id, stage), count in sorted(skipped.items()):
        c.execute(
            "INSERT INTO trace_events (event_id,store_id,task_id,actor_id,stage,action,subject_id,"
            "before_json,after_json,field_json,source,recorded_at,schema_version,app_version) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), store[0], task_id, actor_id, stage if stage in TRACE_STAGES else "coding",
             "report_evidence_changed", "report-evidence", None,
             json.dumps({"value": "Legacy trace fold skipped malformed events", "skipped_events": count,
                         "note": "Legacy snapshot events with malformed payloads were left unchanged."},
                        ensure_ascii=False, sort_keys=True),
             None, "report_evidence", now, research_trace.SCHEMA_VERSION, research_trace.__version__))


def _backup_before_fold(c):
    """迁移前备份 <db>.pre-fold-v1.bak（经 sqlite backup 捕获 WAL 内容）；已存在则跳过不覆盖。

    返回 False 表示备份失败：本次放弃折叠且不置完成标记，下次连接重试。
    """
    try:
        row = c.execute("PRAGMA database_list").fetchone()
    except sqlite3.Error:
        return False
    path = row[2] if row else ""
    if not path:
        return True  # 内存库无文件可备，直接继续
    backup_path = path + FOLD_BACKUP_SUFFIX
    if os.path.exists(backup_path):
        return True
    target = None
    ok = False
    try:
        target = sqlite3.connect(backup_path)
        c.backup(target)
        ok = True
    except (OSError, sqlite3.Error):
        ok = False
    finally:
        if target is not None:
            try:
                target.close()
            except sqlite3.Error:
                pass
    if not ok:
        try:
            if os.path.exists(backup_path):
                os.remove(backup_path)  # 清理半成品，避免下次被误判为“已备份”
        except OSError:
            pass
        return False
    return True


def compact_legacy_trace_events(c):
    """一次性迁移：把 2.1.3 全量快照轨迹事件按保存批次折叠（幂等、带完成标记与迁移前备份）。

    旧版每次保存给每个受影响主体写一条内嵌整份工作区快照的 report_evidence_changed
    事件；同一次保存的多条事件共享相同快照且序号连续，据此识别保存批次并折叠为
    每批一条增量事件（载荷含 value 标签、changes 增量；单条或该成员累计超出轨迹
    预算时降级为摘要，含 workspace_sha256 与 links/findings 计数）。混合形态
    （一侧全量、一侧已是旧摘要）保留摘要侧原有哈希与计数；畸形载荷跳过并写
    标记事件，任何情况不抛错。迁移只改写/删减 2.1.3 自有事件行的
    before_json/after_json：origin_* 九列与 origin_sha256 绝对不动，携带 origin
    身份的导入事件只更新、不删除；不删除 trace_field_lifecycles 与幂等回执表
    （无 clear_history 语义）。完成标记存在时本函数只做只读检查，不取写锁。
    """
    try:
        if not _table_exists(c, "trace_events"):
            return
        if _fold_marker_present(c):
            return
        needs_fold = c.execute(_LEGACY_SCAN_SQL + " LIMIT 1").fetchone() is not None
        backup_ok = _backup_before_fold(c) if needs_fold else True
        # 备份在取写锁之前完成：backup API 不能在持有 BEGIN IMMEDIATE 事务的
        # 连接上执行（会自锁）；备份失败则本次不折叠、不置标记，下次连接重试。
        own = not c.in_transaction
        if own:
            c.execute("BEGIN IMMEDIATE")
        try:
            c.execute(f"CREATE TABLE IF NOT EXISTS {_FOLD_META_TABLE} "
                      "(singleton INTEGER PRIMARY KEY CHECK(singleton=1), folded_at TEXT NOT NULL, "
                      "folded_runs INTEGER NOT NULL, deleted_rows INTEGER NOT NULL, skipped_events INTEGER NOT NULL)")
            if _fold_marker_present(c):  # 拿到写锁后复核，防并发重复折叠
                if own:
                    c.commit()
                return
            folded_runs = deleted_rows = 0
            skipped = {}
            if needs_fold:
                if not backup_ok:
                    if own:
                        c.commit()  # 备份失败：空事务收尾，不置标记，下次连接重试
                    return
                folded_runs, deleted_rows, skipped = _fold_legacy_runs(c)
                if skipped:
                    _insert_fold_marker_events(c, skipped)
            c.execute(f"INSERT INTO {_FOLD_META_TABLE} VALUES(1, ?, ?, ?, ?)",
                      (datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                       folded_runs, deleted_rows, sum(skipped.values())))
            if own:
                c.commit()
        except Exception:
            if own:
                c.rollback()
            raise
    except Exception:
        # M5：迁移自身异常绝不向上传播（connect/导出路径不得 500）。
        # 自有事务已回滚、无标记，下次连接重试；调用方事务由其自行回滚。
        return


def connect(path):
    c = sqlite3.connect(path, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=15000")
    # 证据表惰性建表：schema 就绪后（含折叠完成标记就位后）纯读路径不再执行
    # 任何 DDL 或迁移写事务，不再取 SQLite 写锁。
    def schema_ready():
        return (_table_exists(c, "report_evidence_state") and _table_exists(c, "report_evidence_requests")
                and "previous_stage" in {row[1] for row in c.execute("PRAGMA table_info(report_evidence_state)")})
    if not schema_ready():
        c.execute("BEGIN IMMEDIATE")
        try:
            c.execute("CREATE TABLE IF NOT EXISTS report_evidence_state (actor TEXT PRIMARY KEY, revision INTEGER NOT NULL, state TEXT NOT NULL, previous TEXT, previous_stage TEXT)")
            # 旧库迁移：为撤销栈补记保存时的轨迹阶段，使撤销事件与对应保存落在同一阶段。
            # BEGIN IMMEDIATE 下查改串行，杜绝并发首连的 duplicate column 竞态。
            if "previous_stage" not in {row[1] for row in c.execute("PRAGMA table_info(report_evidence_state)")}:
                c.execute("ALTER TABLE report_evidence_state ADD COLUMN previous_stage TEXT")
            c.execute("CREATE TABLE IF NOT EXISTS report_evidence_requests (actor TEXT, operation TEXT, digest TEXT NOT NULL, PRIMARY KEY(actor,operation))")
            c.commit()
        except sqlite3.OperationalError:
            c.rollback()
            if not schema_ready():
                raise
    # 2.1.3 存量全量快照轨迹事件的按保存批次折叠迁移（幂等；完成标记后零写锁）。
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
        超过 TRACE_SUBJECT_LIMIT 个受影响主体时在 report_evidence_changed
        载荷内补显式截断标记字段（不新增动作键）。
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
            # 显式截断标记：不再静默丢弃第 101 位及之后的受影响主体。标记沿用既有
            # 动作键 report_evidence_changed（不新增动作），截断信息放在载荷字段内。
            research_trace.capture_event(
                c, tc, stage=stage, action="report_evidence_changed",
                subject_id="report-evidence", before=None,
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
