"""custom_backend/team_current_results.py — P22/C11：当前团队结果（读侧）。

合同（C11 冻结，实现不偏离）：
- ``current_team_results(scope, *, item_keys) -> Mapping[str, object]`` 只消费
  统一 keyed views（``team_chain_view(scope, "stage2", item_keys=...)``：同一
  scope 一次派生两阶段——快照不分裂，两 stage 链一致；stage1 终局排除即剪枝
  stage2 读取）。票面永不进入结果（双盲）；完成事实优先（F1 见证
  persisted=True），live 全员一致/已适用仲裁为可信当前意见（persisted=False）。
- 每键四态 eligible/blocked/partial/unavailable（known 域上不交覆盖）：
  * eligible：两段链上有有效当前结论（F1 终局，或当前全员一致，或已适用
    仲裁）。stage1 终局排除同样是有效当前结论（链终止于 stage1）。
  * blocked：全员已有当前意见但未终局（正式分歧 awaiting_arbitration），
    或名册/角色配置未就绪（awaiting_config）。
  * partial：团队结论未成形，因为部分必需成员尚无正式意见
    （awaiting_members）。raw 个人完成度（raw_pending_members 名单）≠ 团队
    完成——严格区分，不把个人比例当团队结论。
  * unavailable：完成来源坏（witness_unreadable/witness_drift/
    member_vote_unreadable/vote_row_*/vote_time_unverifiable 等 issue）——
    不伪空、不排除、不连坐其他键（A 可用/B 坏返回 A+issue）。
- 计数锚（工单钉死）：``counts['eligible'] + counts['blocked'] +
  counts['unavailable'] == counts['known_total']``。四态按键不交，但
  counts['blocked'] 为「当前不可定团队结论」合并桶（**含** partial 细分；
  counts['partial'] 是其子计数）——锚在 known_total 为具体整数时恒成立
  （违反＝内部不变量破坏，RuntimeError fail-closed）。owner 域无法确定
  总数时 known_total 用 **null**、不把未知当零（``current_team_counts``
  支持 None；本函数成功返回时 known_total 恒为具体整数——owner/键域身份
  坏按 C11 走 task-level typed 503，不静默伪空）。
- 2026-11-15 裁定锚（TEAM_FLOW_REPAIR_CONTRACT_20261114 §9）：完成条目
  保留完成时的名单、意见依据与结论——F1 见证的完成投影（persisted=True，
  roster_basis="completion"）不因成员变动重开、不补历史意见；新名单
  （roster_basis="current"）只作用于未完成条目/阶段。已完成阶段的
  required_uids 以 seal payload 为准（completion_required_uids）。
- 旧路径不动：按人 raw 导出、独立 CSV、generic latest
  （``latest_decision_arbitration``）与 reviewer 模式读不在本模块；
  ``custom_backend/prisma_task_metrics.py`` 仅在 team 模式追加投影分支，
  不全局过滤任何 loader。
- ``current_team_projection``：PRISMA metrics/records 的 team 投影入口
  （受保护按键只读 scope：canonical 锁＋collab IMMEDIATE 屏障，退出只
  ROLLBACK，零持久化）。operation stage="coding"（F1 链式适配器
  (stage1, stage2)——PRISMA 当前结果需要两段完成事实；命名仅入 plan 审计
  面，不授写权限）。读取面经 P08 plan 的 holder 见证 reader：witness 覆盖
  的键不触碰成员库（P16-B 的 witness_readers 语义在读取侧天然生效，见
  箱内 REPORT 跟进单 a）。

依赖归属：只 import ``custom_backend.team_screening_runtime`` /
``custom_backend.team_keyed_reads``（P09 文件互斥表同组）与 coscreen 固定
面；不 import custom_backend 其他模块（无环、无默认 data 触达）。
"""
from __future__ import annotations

import dataclasses
from typing import Mapping

from coscreen.team_flow_types import TeamOperation, normalize_item_keys
from coscreen.team_screening_eligibility import (
    AWAITING_ARBITRATION,
    AWAITING_CONFIG,
    AWAITING_MEMBERS,
    FINAL_EXCLUDE,
    FINAL_INCLUDE,
)

from custom_backend.team_screening_runtime import team_chain_view

__all__ = [
    "current_team_results",
    "current_team_counts",
    "current_team_projection",
    "outcomes_from_current_team",
]

#: 链目标：stage2（初筛终局纳入为 cleared；复筛进度看 stage2 视图自身）。
_CURRENT_RESULTS_TARGET = "stage2"

#: 四态词表（known 域分类；'unknown_key' 为域外请求键，不计数）。
STATE_ELIGIBLE = "eligible"
STATE_BLOCKED = "blocked"
STATE_PARTIAL = "partial"
STATE_UNAVAILABLE = "unavailable"
STATE_UNKNOWN_KEY = "unknown_key"


def _issue_payload(issue) -> dict:
    """KeyIssue DTO → 诊断 payload（reason/stage/key/source_role/retryable）。"""
    return {
        "reason": issue.reason,
        "stage": issue.stage,
        "key": issue.key,
        "source_role": issue.source_role,
        "retryable": bool(issue.retryable),
    }


def _stage_payload(status) -> dict | None:
    """ItemStatus → 状态摘要（不含票面；pending 名单是完成度诊断非意见）。"""
    if status is None:
        return None
    return {
        "status": status.status,
        "decided_by": status.decided_by,
        "applied_arbitration": status.applied_arbitration,
        "pending_members": list(status.pending_members),
        "pending_reasons": [
            {"member": member, "reason": reason}
            for member, reason in status.pending_reasons],
        "stale_arbitrations": [
            {"record_id": record_id, "reason": reason}
            for record_id, reason in status.stale_arbitrations],
    }


def _completion_payload(projection) -> dict | None:
    """CompletionProjection → 按键 attestation payload（F1 或 live 一致）。"""
    if projection is None:
        return None
    return dict(dataclasses.asdict(projection))


def _classify_key(view, key: str) -> dict:
    """一个请求键 → 四态结果项（完成事实优先；阶段链自 stage1 向 stage2）。"""
    chain_issue = view.issues.get(key)
    if chain_issue is not None and chain_issue.reason == "key_not_in_domain":
        # 域外请求键：如实报告，不进 known 计数（不在 owner 键域内）。
        return {
            "key": key, "status": STATE_UNKNOWN_KEY,
            "issue": _issue_payload(chain_issue),
            "stage1": None, "stage2": None,
            "current_decision": None, "deciding_stage": None,
            "attestation": None, "raw_pending_members": [],
        }
    if chain_issue is not None:
        return {
            "key": key, "status": STATE_UNAVAILABLE,
            "issue": _issue_payload(chain_issue),
            "stage1": _stage_payload(view.stage1.statuses.get(key)),
            "stage2": None,
            "current_decision": None, "deciding_stage": None,
            "attestation": None, "raw_pending_members": [],
        }
    first = view.stage1.statuses.get(key)
    stage1_completion = view.stage1.completion.get(key)
    stage2_completion = view.stage2.completion.get(key)
    attestation = {
        "stage1": _completion_payload(stage1_completion),
        "stage2": _completion_payload(stage2_completion),
        "chain": _completion_payload(view.completion.get(key)),
        "applied_arbitration_stage1": (
            view.stage1.applied[key][0]
            if key in view.stage1.applied else None),
        "applied_arbitration_stage2": (
            view.stage2.applied[key][0]
            if key in view.stage2.applied else None),
        "persisted": bool((stage1_completion is not None
                           and stage1_completion.persisted)
                          or (stage2_completion is not None
                              and stage2_completion.persisted)),
    }

    def finish(status, decision, stage_no, pending, stage2_payload):
        return {
            "key": key, "status": status, "issue": None,
            "stage1": _stage_payload(first),
            "stage2": stage2_payload,
            "current_decision": decision, "deciding_stage": stage_no,
            "attestation": attestation,
            "raw_pending_members": pending,
        }

    if first is None:
        # 无 stage1 status 且无 issue＝视图合同违约（防御性 fail-closed，
        # 不伪空）：以 unavailable 呈现而不是静默丢键。
        return finish(STATE_UNAVAILABLE, None, None, [], None)
    st1 = first.status
    if st1 == FINAL_EXCLUDE:
        # stage1 终局排除＝有效当前结论（链终止；stage2 已被剪枝不读）。
        return finish(STATE_ELIGIBLE, "exclude", 1, [],
                      _stage_payload(view.stage2.statuses.get(key)))
    if st1 == FINAL_INCLUDE:
        stage2_issue = view.stage2.issues.get(key)
        if stage2_issue is not None:
            # target=stage2 的链 issue 只取 stage1（上游），stage2 读取期的
            # 逐键损害在这里补齐（A 键 stage2 坏不连坐其他键）。
            item = finish(STATE_UNAVAILABLE, None, None, [], None)
            item["issue"] = _issue_payload(stage2_issue)
            return item
        second = view.stage2.statuses.get(key)
        attestation["stage2"] = _completion_payload(
            view.stage2.completion.get(key))
        attestation["applied_arbitration_stage2"] = (
            view.stage2.applied.get(key, (None, None))[0]
            if key in view.stage2.applied else None)
        if key in view.stage2.completion:
            attestation["persisted"] = bool(
                view.stage2.completion[key].persisted
                or attestation["persisted"])
        if second is None:
            return finish(STATE_UNAVAILABLE, None, None, [],
                          None)
        st2 = second.status
        if st2 == FINAL_INCLUDE:
            return finish(STATE_ELIGIBLE, "include", 2, [],
                          _stage_payload(second))
        if st2 == FINAL_EXCLUDE:
            return finish(STATE_ELIGIBLE, "exclude", 2, [],
                          _stage_payload(second))
        if st2 == AWAITING_MEMBERS:
            return finish(STATE_PARTIAL, None, None,
                          list(second.pending_members),
                          _stage_payload(second))
        # awaiting_arbitration / awaiting_config：全员有意见未终局，或配置
        # 未就绪——当前不可定团队结论。
        return finish(STATE_BLOCKED, None, None, [],
                      _stage_payload(second))
    if st1 == AWAITING_MEMBERS:
        return finish(STATE_PARTIAL, None, None,
                      list(first.pending_members),
                      _stage_payload(view.stage2.statuses.get(key)))
    if st1 in (AWAITING_ARBITRATION, AWAITING_CONFIG):
        return finish(STATE_BLOCKED, None, None, [],
                      _stage_payload(view.stage2.statuses.get(key)))
    # 未知状态令牌（视图合同外）——fail-closed，不猜。
    return finish(STATE_UNAVAILABLE, None, None, [], None)


def current_team_counts(results: Mapping[str, Mapping], *,
                        known_total: int | None) -> dict:
    """四态计数（known 域）：blocked 合并桶含 partial；known_total 未知为
    None（不把未知当零；锚仅对具体整数成立）。"""
    counts = {"eligible": 0, "blocked": 0, "partial": 0, "unavailable": 0,
              "known_total": known_total}
    known = 0
    for item in results.values():
        status = item.get("status")
        if status == STATE_ELIGIBLE:
            counts["eligible"] += 1
            known += 1
        elif status == STATE_PARTIAL:
            counts["partial"] += 1
            counts["blocked"] += 1  # 「当前不可定团队」合并桶含 partial。
            known += 1
        elif status == STATE_BLOCKED:
            counts["blocked"] += 1
            known += 1
        elif status == STATE_UNAVAILABLE:
            counts["unavailable"] += 1
            known += 1
        # unknown_key：域外请求键，不进 known 计数。
    if isinstance(known_total, int) and known != known_total:
        raise RuntimeError(
            "current_team_results 计数锚被破坏: "
            f"eligible({counts['eligible']}) + blocked({counts['blocked']}) "
            f"+ unavailable({counts['unavailable']}) = {known} != "
            f"known_total({known_total})")
    return counts


def current_team_results(scope, *, item_keys) -> Mapping[str, object]:
    """C11 冻结入口：受保护 scope 上的当前团队结果（只消费统一 keyed views）。

    ``item_keys`` 为 None 时以 owner 键域（``scope.canonical_keys()``）为请求
    域。返回 keys（请求序）/results（每键四态+按键 attestation）/counts（锚
    见模块头）/roster（当前名单+config 诊断）/completion_required_uids
    （完成事实覆盖的 required uid 并集——完成时名单保留侧）。
    """
    keys = (normalize_item_keys(item_keys)
            if item_keys is not None else scope.canonical_keys())
    view = team_chain_view(scope, _CURRENT_RESULTS_TARGET, item_keys=keys)
    results = {key: _classify_key(view, key) for key in keys}
    known_keys = [key for key in keys
                  if results[key]["status"] != STATE_UNKNOWN_KEY]
    counts = current_team_counts(results, known_total=len(known_keys))
    for item in results.values():
        basis = "current"
        attestation = item.get("attestation")
        if isinstance(attestation, dict) and attestation.get("persisted"):
            basis = "completion"
        item["roster_basis"] = basis
    config1 = view.stage1.config
    config2 = view.stage2.config
    return {
        "task_id": view.stage1.task_id,
        "target": _CURRENT_RESULTS_TARGET,
        "keys": list(keys),
        "results": results,
        "counts": counts,
        "known_total": counts["known_total"],
        "roster": {
            "required": list(config1.required),
            "config": {
                "stage1": {"ok": bool(config1.ok), "detail": config1.detail},
                "stage2": {"ok": bool(config2.ok), "detail": config2.detail},
            },
            "completion_required_uids": tuple(sorted(set(
                view.stage1.completion_required_uids)
                | set(view.stage2.completion_required_uids))),
        },
        "loader_stale": [list(row) for row in view.stage1.loader_stale]
        + [list(row) for row in view.stage2.loader_stale],
    }


def current_team_projection(runtime, info, user, *, item_keys):
    """PRISMA metrics/records 的 team 投影：受保护按键只读 scope 内一次派生。

    零持久化（manager 退出只 ROLLBACK）；调用方（prisma_task_metrics）自行
    先行 owner/Adjudicator 读门（与旧 outcomes 同形状 403）。
    """
    from custom_backend.team_keyed_reads import protected_keyed_read_scope
    operation = TeamOperation(name="team_current_results", stage="coding")
    with protected_keyed_read_scope(
            runtime, info, user, operation=operation,
            item_keys=item_keys) as scope:
        return current_team_results(scope, item_keys=item_keys)


def outcomes_from_current_team(projection: Mapping[str, object], *,
                               domain=None):
    """当前团队投影 → PRISMA outcomes 口径（与旧 ``outcomes()`` 同形状）。

    返回 ``(first, first_unresolved, second, second_unresolved)``：
    - first/second 只含终局键（include/exclude；键域外与 issue 键不进——
      issue 键计入 unresolved（来源坏≠无决策，不少算）。
    - 完全无人投过票的键（全员 missing_vote）沿旧口径计 unscreened（不在
      first、不计 unresolved）；部分成员已投（partial）计入 unresolved。
    - second 仅 sought（stage1 终局纳入）键；exclusion_reason 恒 ''（票面
      不进 keyed views——团队模式排除理由需按人 raw 导出确认，warnings 侧
      由调用方追加；reviewer 模式原路径不变）。
    - domain=None 时以投影 keys 为域（known_total 未知侧由调用方另行处理）。
    """
    results = projection["results"]
    keys = (list(projection["keys"]) if domain is None
            else [key for key in projection["keys"]
                  if key in set(domain)])
    required = set(projection["roster"]["required"])
    first: dict[str, dict] = {}
    second: dict[str, dict] = {}
    first_unresolved = 0
    second_unresolved = 0
    for key in keys:
        item = results[key]
        stage1 = item["stage1"]
        stage2 = item["stage2"]
        stage1_final = (stage1 or {}).get("status") in (
            FINAL_INCLUDE, FINAL_EXCLUDE)
        fully_unscreened = (
            item["status"] == STATE_PARTIAL
            and ((
                (stage1 or {}).get("status") == AWAITING_MEMBERS
                and set((stage1 or {}).get("pending_members") or ())
                == required)
                or (stage1 or {}).get("status") == FINAL_INCLUDE
                and (stage2 or {}).get("status") == AWAITING_MEMBERS
                and set((stage2 or {}).get("pending_members") or ())
                == required))
        if stage1_final:
            first[key] = {
                "decision": "include"
                if stage1["status"] == FINAL_INCLUDE else "exclude",
                "exclusion_reason": "",
            }
            if stage1["status"] == FINAL_INCLUDE:
                stage2_final = (stage2 or {}).get("status") in (
                    FINAL_INCLUDE, FINAL_EXCLUDE)
                if stage2_final:
                    second[key] = {
                        "decision": "include"
                        if stage2["status"] == FINAL_INCLUDE else "exclude",
                        "exclusion_reason": "",
                    }
                elif not fully_unscreened:
                    second_unresolved += 1
        elif not fully_unscreened:
            first_unresolved += 1
    return first, first_unresolved, second, second_unresolved
