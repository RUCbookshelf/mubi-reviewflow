"""2.1.5 W1：存量「暂不确定」重置摘要端点（供前端 F6 升级提示条）。

- ``GET /api/tasks/{id}/binary-reset-summary``：属主/can_manage 读全员，
  普通成员只读本人，非成员 403；legacy/单人任务只返回调用者本人。
- 迁移本体在 ``coscreen.binary_reset``（一次性、幂等、备份+留痕）；本端点
  在读取时对**已存在**的成员库补跑迁移（不创建不存在的库），使属主首次打开
  任务即可看到全员摘要。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import Depends, HTTPException

from coscreen import binary_reset, review_workflow as store, task_collaboration
from custom_backend import auth as auth_mod


def _empty_member(username: str, is_owner: bool) -> dict:
    return {
        "username": username,
        "is_owner": is_owner,
        "migrated": False,
        "applied_at": None,
        "stage1_reset": 0,
        "stage2_reset": 0,
        "stage2_cascade_reset": 0,
        "backup": {"name": None, "exists": False, "sha256": None},
    }


def register_binary_reset_routes(app, require_user, task_or_404, screener_db) -> None:
    @app.get("/api/tasks/{task_id}/binary-reset-summary")
    def binary_reset_summary(task_id: str, user: dict = Depends(require_user)) -> dict:
        info = task_or_404(task_id)
        runtime = app.state.review_workflow
        ctx = runtime.context(info.task_id, user)
        can_manage = bool(ctx.can_manage)

        if ctx.owner is None:
            members = [(user["username"], user["user_id"], True)]
        else:
            try:
                ids = task_collaboration.list_participant_ids(
                    ctx.collaboration_db, actor_user_id=user["user_id"],
                    task_id=info.task_id)
            except PermissionError as exc:
                raise HTTPException(403, "Task access denied.") from exc
            except task_collaboration.OwnerUnavailableError as exc:
                raise HTTPException(503, "任务参与者状态暂时无法确认，请稍后重试。") from exc
            accounts = [account for uid in ids
                        if (account := auth_mod.get_user_by_id(uid)) is not None]
            accounts.sort(key=lambda account: (
                account["username"] != ctx.owner["owner_username"],
                account["username"]))
            if not can_manage:
                accounts = [account for account in accounts
                            if account["id"] == user["user_id"]]
            members = [(account["username"], account["id"],
                        account["username"] == ctx.owner["owner_username"])
                       for account in accounts]

        rows = []
        with runtime.lock(info.task_id):
            for username, user_id, is_owner in members:
                row = _empty_member(username, is_owner)
                try:
                    db = Path(screener_db(info, username, ensure=False))
                except HTTPException:
                    raise
                if db.is_file():
                    # 成员库尚未打开过也可在此补跑（只对既有库；失败降级为
                    # 未迁移，不让摘要端点 500）。
                    try:
                        store.migrate(db)
                        binary_reset.migrate(db, task_id=info.task_id,
                                             actor_id=user_id)
                        data = binary_reset.summary(db)
                    except Exception:
                        data = None
                    if data:
                        backup = db.with_name(db.name + binary_reset.BACKUP_SUFFIX)
                        row.update({
                            "migrated": True,
                            "applied_at": data.get("applied_at"),
                            "stage1_reset": int(data.get("stage1_reset") or 0),
                            "stage2_reset": int(data.get("stage2_reset") or 0),
                            "stage2_cascade_reset": int(
                                data.get("stage2_cascade_reset") or 0),
                            "backup": {
                                "name": data.get("backup_name"),
                                "exists": backup.is_file(),
                                "sha256": data.get("backup_sha256"),
                            },
                        })
                rows.append(row)

        return {
            "task_id": info.task_id,
            "can_manage": can_manage,
            "migration_version": binary_reset.RESET_VERSION,
            "members": rows,
            "totals": {
                "stage1_reset": sum(row["stage1_reset"] for row in rows),
                "stage2_reset": sum(row["stage2_reset"] for row in rows),
                "stage2_cascade_reset": sum(
                    row["stage2_cascade_reset"] for row in rows),
            },
        }
