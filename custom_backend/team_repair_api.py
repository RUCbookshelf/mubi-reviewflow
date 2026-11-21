"""P2 管理端修复 API（R3 强制移除 + R6 冲突 seal 修复；2026-11-21 用户裁定）。

依据：E:/rf216-store-scan-20261120/GAP_DESIGN_SURVEY.md §4 C 矩阵、§6 R3/R6。
独立路由模块——P1（写路径统一准备/异常收口）与 P2 的 main.py 改动互斥，
本模块不进 main.py，避免并行包撞同一文件。

端点（均属主专属、confirm 语义、管理 receipt 审计留痕）：

- ``POST /api/tasks/{task_id}/collaboration/repair/force-remove``（R3）：
  成员库存在但证据不可确认（损坏/半装/保留映射缺席）时的显式危险移除。
  ``confirm`` 必须逐字等于 ``force-remove``；底层 ``run_team_management(
  action="remove", force=True)``——归档以"证据不可确认"零伪造证据入账
  （receipt ``result.retirement_audit.forced``），F2 登记 retire、watermark
  指向强制证据 sha256。成员库文件随收尾隔离保留在磁盘（不销毁）。
- ``POST /api/tasks/{task_id}/collaboration/repair/seal-conflict``（R6）：
  冲突 seal 修复——``authoritative_seal_id`` 必须是当前可见 seal 之一；
  collab 现行表该键无记录时权威 seal 归档进 collab（成为现行记录）；
  现行表已持另一枚则 fail-closed（``seal_conflict_authority_occupied``，
  不静默覆盖）；非权威 seal 不销毁，逐条哈希入 receipt 审计。

权限＝属主管理门同款（``run_team_management``/``run_team_seal_conflict_
repair`` 内部 ``not_owner`` 强制；HTTP 层再以 ``require_user`` 认证）。
错误口径：确定性 retirement/修复码 → 409 结构化信封 +
``retirement_recovery_payload`` 语义载荷（recovery 键内容唯一权威定义在
team_source_lifecycle，P1 的 team_http_errors.py 映射层取同一来源）；
未识别码保持 503；``not_owner`` 403、``invalid_*`` 400。

**挂载（白名单行漂，呈报不自行登记）**：main.py 注册区需一行——
``from custom_backend.team_repair_api import register_team_repair_routes``
＋ ``register_team_repair_routes(app, require_user, _task_or_404)``。
本包按裁定零提交、不碰 main.py（与 P1 文件互斥），该行在 REPORT.md
呈报为待登记行漂。
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import Depends, HTTPException

from custom_backend.team_source_lifecycle import (
    TeamManagementError,
    retirement_recovery_payload,
    run_team_management,
    run_team_seal_conflict_repair,
)
from custom_backend.workspace_undo_api import member_request_fingerprint

__all__ = ["register_team_repair_routes"]

#: R3 危险操作的 confirm 词（逐字匹配；大小写敏感——危险操作不打马虎眼）。
_CONFIRM_FORCE_REMOVE = "force-remove"
#: R6 修复操作的 confirm 词。
_CONFIRM_SEAL_REPAIR = "repair-seal-conflict"

_REQUEST_HEX64 = set("0123456789abcdef")


def register_team_repair_routes(
    app: Any,
    require_user: Callable[..., dict],
    task_or_404: Callable[[str], Any],
) -> None:
    """在 ``app`` 上注册两个管理端修复端点（main.py 注册区一行调用）。"""

    def _request_fingerprint(route: str, view: dict) -> tuple:
        return member_request_fingerprint(None, {
            "task_id": view.get("task_id"), "route": "team-repair",
            "repair": route, **view})

    def _http_error(exc: TeamManagementError) -> HTTPException:
        if exc.code == "not_owner":
            return HTTPException(
                403, "Only the task owner can run repair operations.")
        if exc.code.startswith("invalid_"):
            return HTTPException(400, {
                "error_code": exc.code,
                "message_key": "team.repair." + exc.code,
                "message": str(exc),
                "recovery": retirement_recovery_payload(exc.code),
            })
        recovery = retirement_recovery_payload(exc.code)
        if recovery is not None:
            # 确定性修复面条件＝409（对齐 appraisal 先例：同请求重试永不
            # 成功绝不 503）；recovery 内容取 lifecycle 唯一权威定义。
            return HTTPException(409, {
                "error_code": exc.code,
                "message_key": "team.repair." + exc.code,
                "message": str(exc),
                "recovery": recovery,
                "retryable": False,
            })
        return HTTPException(503, {
            "error_code": "team_repair_uncommitted",
            "message_key": "team.repair_uncommitted",
            "message": str(exc),
            "retryable": True,
        })

    @app.post("/api/tasks/{task_id}/collaboration/repair/force-remove")
    def repair_force_remove(task_id: str,
                            payload: dict,
                            user: dict = Depends(require_user)) -> dict:
        """R3：证据不可确认的强制移除（confirm 危险操作 + 零伪造审计）。"""
        info = task_or_404(task_id)
        if not isinstance(payload, dict):
            raise HTTPException(400, {"error_code": "invalid_payload",
                                      "message": "body must be a JSON object"})
        confirm = payload.get("confirm")
        if confirm != _CONFIRM_FORCE_REMOVE:
            raise HTTPException(400, {
                "error_code": "confirm_required",
                "message_key": "team.repair.confirm_required",
                "message": "危险操作：body.confirm 必须逐字为 "
                           f"\"{_CONFIRM_FORCE_REMOVE}\"。",
                "recovery": retirement_recovery_payload(
                    "retirement_source_unverifiable"),
            })
        target_user_id = payload.get("target_user_id")
        if isinstance(target_user_id, bool) or not isinstance(target_user_id, int) \
                or target_user_id <= 0:
            raise HTTPException(400, {"error_code": "invalid_target",
                                      "message": "target_user_id must be a positive int"})
        role = payload.get("role")
        if role not in ("coder", "reconciler"):
            role = "coder"
        request_id, request_sha256 = _request_fingerprint("force-remove", {
            "task_id": task_id, "target_user_id": target_user_id})
        try:
            outcome = run_team_management(
                app.state.review_workflow, info, dict(user),
                action="remove", target_user_id=target_user_id,
                payload={"role": role}, request_id=request_id,
                request_sha256=request_sha256, force=True)
        except TeamManagementError as exc:
            raise _http_error(exc) from exc
        return {
            "entry": "repair:force-remove",
            "action": "remove",
            "forced": True,
            "request_id": request_id,
            "membership_committed": outcome.get("outcome") == "committed",
            "idempotent": bool(outcome.get("idempotent", False)),
            "outcome": outcome,
        }

    @app.post("/api/tasks/{task_id}/collaboration/repair/seal-conflict")
    def repair_seal_conflict(task_id: str,
                             payload: dict,
                             user: dict = Depends(require_user)) -> dict:
        """R6：冲突 seal 修复（选权威 seal 归档另一枚 + receipt 审计）。"""
        info = task_or_404(task_id)
        if not isinstance(payload, dict):
            raise HTTPException(400, {"error_code": "invalid_payload",
                                      "message": "body must be a JSON object"})
        confirm = payload.get("confirm")
        if confirm != _CONFIRM_SEAL_REPAIR:
            raise HTTPException(400, {
                "error_code": "confirm_required",
                "message_key": "team.repair.confirm_required",
                "message": "危险操作：body.confirm 必须逐字为 "
                           f"\"{_CONFIRM_SEAL_REPAIR}\"。",
            })
        stage = payload.get("stage")
        item_key = payload.get("item_key")
        authoritative = payload.get("authoritative_seal_id")
        request_id, request_sha256 = _request_fingerprint("seal-conflict", {
            "task_id": task_id, "stage": stage, "item_key": item_key,
            "authoritative_seal_id": authoritative})
        try:
            outcome = run_team_seal_conflict_repair(
                app.state.review_workflow, info, dict(user),
                stage=stage, item_key=item_key,
                authoritative_seal_id=authoritative,
                request_id=request_id, request_sha256=request_sha256)
        except TeamManagementError as exc:
            raise _http_error(exc) from exc
        return {
            "entry": "repair:seal-conflict",
            "action": "seal_conflict_repair",
            "request_id": request_id,
            "membership_committed": outcome.get("outcome") == "committed",
            "idempotent": bool(outcome.get("idempotent", False)),
            "outcome": outcome,
        }
