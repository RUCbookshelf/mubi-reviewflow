"""2.1.5 aiux 波 B/C：导入复核 AI 助手的审计导出与记分卡（纯只读端点）。

B 审计导出 ``GET /api/tasks/{task_id}/import-review/ai-assist/audit/export``
    ``?format=csv|json&batch_id=<可选过滤>``：

    - 权限与既有 audit GET 同口径：``require_user`` + 任务成员（``_context``
      403 非成员），读属主库（导入复核数据按属主库权威）。
    - CSV：列固定 ``audit_id,batch_id,item_kind,item_id,verdict,confidence,
      model,model_kind,provider,prompt_version,status,executed,undone,
      created_at``；公式注入防护沿用仓内 MM-05 先例（字段以 ``= + - @ \\t \\r``
      开头补 ``'`` 前缀）；UTF-8 BOM 便于 Excel 直开。
    - JSON：完整行（含 ``rationale_zh`` / ``warnings`` / ``field_picks`` 与
      派生 ``executed`` / ``undone``）。
    - 两种格式都带 ``Content-Disposition: attachment``；``format`` 非法 →
      422 结构化（I-6：code/message_key/params/detail）。

C 记分卡 ``GET /api/tasks/{task_id}/import-review/ai-assist/scorecard``：

    对已有人工最终判定的建议算 AI-人工一致率（纯只读、绝不写库）：

    - ``duplicate_group``：人工侧 = 候选组 ``status``（``duplicate`` /
      ``not_duplicate`` 为最终判定；``pending`` / ``undecided`` 与仓内口径
      一致仍算待处理，不进样本）。
    - ``metadata_diff``：人工侧 = 提案 ``action``（``accept_incoming`` /
      ``field_picks`` = 采纳入站 → 同侧；``keep_existing`` = 拒纳入站 →
      异侧）。
    - 一致口径：AI ``same`` ↔ 人工「同」记 agree；``different`` ↔ 人工「异」
      记 agree；其余 disagree；AI ``unsure`` 单列（``ai_unsure``）不计入
      一致率分母；``agreement_rate`` 分母 = agree+disagree，无样本为 null。
    - ``merge_conflict`` 不做「是不是重复」比较，只统计 ``field_picks`` 与
      ``human_final`` 的**字段级**一致（human_final 派生口径复用
      ``import_ai_assist_api._audit_view``：留痕表 ``workflow_import_field_merges``
      经 ``ai_suggestion_id`` 关联 + ``undone`` 标记）。

钥匙安全：审计表本就不含任何 API 钥匙材料（BYOK 钥匙只活在 ``ai_keys``
保险柜内存/槽文件），本模块只读审计与复核表，绝不回显钥匙、绝不出站。
端到端错误口径：参数问题 → 422 结构化；存储故障 → 503 结构化可重试；
绝不 500。
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException, Response

from coscreen import review_workflow as workflow
# 复用第一段的错误形状 / merge 派生口径（同一服务的两个只读视图，
# 不另写一份口径以免漂移）。
from custom_backend.import_ai_assist_api import (  # noqa: SLF001  同服务复用
    MERGE_VERDICT,
    _audit_view,
    _error,
)

__all__ = ["register_import_ai_report_routes"]

#: 导出格式白名单（其余 → 422 结构化）。
EXPORT_FORMATS: tuple[str, ...] = ("csv", "json")

#: CSV 列（顺序即导出顺序；executed/undone 为派生列，口径见模块 docstring）。
_CSV_COLUMNS: tuple[str, ...] = (
    "audit_id", "batch_id", "item_kind", "item_id", "verdict", "confidence",
    "model", "model_kind", "provider", "prompt_version", "status",
    "executed", "undone", "created_at",
)

#: 既有 audit GET 路由（int 路径参数）——字面量 ``export`` 若注册在其后会被
#: 吞成 422，注册函数末尾把本模块的 export 路由挪到它前面（见 register）。
_SHADOW_ROUTE_PATH = "/api/tasks/{task_id}/import-review/ai-assist/audit/{audit_id}"

_EXPORT_ROUTE_PATH = "/api/tasks/{task_id}/import-review/ai-assist/audit/export"
_SCORECARD_ROUTE_PATH = "/api/tasks/{task_id}/import-review/ai-assist/scorecard"

_STORAGE_UNAVAILABLE_DETAIL = (
    "任务数据库暂时不可用（可能正被其他操作占用，或磁盘只读/已满），请稍后重试。")


def _storage_unavailable() -> HTTPException:
    """sqlite/IO 故障 → 结构化 503（与 import_ai_assist._storage_unavailable
    同语义；code 用本模块自己的命名域）。"""
    return _error(503, "import_ai_report_storage_unavailable",
                  "import_ai_report.storage_unavailable", {},
                  _STORAGE_UNAVAILABLE_DETAIL)


def _csv_cell(value: Any) -> Any:
    """CSV 单元格公式注入防护（MM-05 同款，见 main.py 既有 5 处导出）：
    以 ``= + - @ \\t \\r`` 开头的字符串补 ``'`` 前缀；数值（confidence）与
    None 原样交给 csv.writer（None 落成空单元格）。"""
    if isinstance(value, str) and value.lstrip().startswith(
            ("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def _json_list(text: Any) -> list:
    """审计 warnings_json → 列表（损坏/非列表降级空列表，绝不 500）。"""
    try:
        value = json.loads(text) if text else []
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _json_object(text: Any) -> dict | None:
    """审计 field_picks_json → 对象（损坏降级 None）。"""
    try:
        value = json.loads(text) if text else None
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _attachment(data: bytes, media_type: str, filename: str) -> Response:
    """下载响应：``Content-Disposition: attachment``（文件名是纯 ASCII
    常量，无需 RFC 5987 编码）。"""
    return Response(content=data, media_type=media_type,
                    headers={"Content-Disposition":
                             f'attachment; filename="{filename}"'})


def _audit_rows(conn: sqlite3.Connection, task_id: str,
                batch_id: str | None) -> list[dict]:
    """读审计表（纯 SELECT + 参数绑定）；表未建（任务从未用过 AI 助手）→ 空列表。"""
    if not workflow.has_table(conn, "import_ai_suggestions"):
        return []
    sql = "SELECT * FROM import_ai_suggestions WHERE task_id=?"
    params: list[Any] = [task_id]
    if batch_id:
        sql += " AND batch_id=?"
        params.append(batch_id)
    sql += " ORDER BY id"
    return [dict(row) for row in conn.execute(sql, params)]


def _candidate_status(conn: sqlite3.Connection, item_id: str) -> str | None:
    """候选组人工状态（表未建/条目不存在 → None）。"""
    if not workflow.has_table(conn, "workflow_import_candidates"):
        return None
    row = conn.execute(
        "SELECT status FROM workflow_import_candidates WHERE id=?",
        (item_id,)).fetchone()
    return row[0] if row is not None else None


def _metadata_action(conn: sqlite3.Connection, item_id: str) -> str | None:
    """元数据提案人工动作（表未建/条目不存在/未判定 → None）。"""
    if not workflow.has_table(conn, "workflow_import_metadata"):
        return None
    row = conn.execute(
        "SELECT action FROM workflow_import_metadata WHERE record_id=?",
        (item_id,)).fetchone()
    return row[0] if row is not None else None


def _merge_trail(conn: sqlite3.Connection, record: dict) -> list[dict]:
    """merge_conflict 的逐字段留痕（与 assist_audit 同款三键关联查询）。"""
    if not workflow.has_table(conn, "workflow_import_field_merges"):
        return []
    return [dict(row) for row in conn.execute(
        "SELECT field,source_record_id,after_value,applied,undone "
        "FROM workflow_import_field_merges WHERE batch_id=? AND candidate_id=? "
        "AND ai_suggestion_id=? ORDER BY id",
        (record["batch_id"], record["item_id"], str(record["id"])))]


def _derived_state(conn: sqlite3.Connection, record: dict) -> dict:
    """派生 ``executed`` / ``undone`` / ``candidate_status`` / ``human_side``
    （口径与 ``_audit_view`` 一致：merge 行 join 候选状态与留痕 undone 标记）。

    - duplicate_group：候选组已有同/异最终判定（duplicate/not_duplicate）
      即 executed；快照式撤销会把状态还原回待处理，executed 随之变 False。
    - metadata_diff：提案 action 非空即 executed（与 load_item_payload 的
      ``action IS NULL`` 可建议口径互补）。
    - merge_conflict：executed = 候选组判定为 duplicate（合并已执行）；
      undone = 留痕存在且无任何「已应用且未撤销」行（撤销只标记不抹历史）。
    - human_side：人工最终判定映射到「同/异」侧（无最终判定 → None，不进
      记分卡样本）——duplicate_group 用候选 status（pending/undecided 与仓内
      口径一致仍算待处理）；metadata_diff 用提案 action
      （accept_incoming/field_picks=采纳入站→同侧，keep_existing=拒纳入站→异侧）。
    """
    kind = record["item_kind"]
    if kind == "duplicate_group":
        status = _candidate_status(conn, record["item_id"])
        return {"executed": status in ("duplicate", "not_duplicate"),
                "undone": False, "candidate_status": status, "trail": [],
                "human_side": status if status in ("duplicate", "not_duplicate")
                else None}
    if kind == "metadata_diff":
        action = _metadata_action(conn, record["item_id"])
        human_side = None
        if action in ("accept_incoming", "field_picks"):
            human_side = "duplicate"
        elif action == "keep_existing":
            human_side = "not_duplicate"
        return {"executed": action is not None, "undone": False,
                "candidate_status": None, "trail": [], "human_side": human_side}
    trail = _merge_trail(conn, record)
    status = _candidate_status(conn, record["item_id"])
    undone = bool(trail) and not any(
        row.get("applied") and not row.get("undone") for row in trail)
    return {"executed": status == "duplicate", "undone": undone,
            "candidate_status": status, "trail": trail, "human_side": None}


def _bucket(agree: int, disagree: int, unsure: int) -> dict:
    """一致率桶（agreement_rate 分母 = agree+disagree，无样本为 null）。"""
    compared = agree + disagree
    return {"total_compared": compared, "agree": agree, "disagree": disagree,
            "ai_unsure": unsure,
            "agreement_rate": (agree / compared) if compared else None}


def _verdict_out(record: dict) -> str | None:
    """verdict 出口：merge 行的 n/a 占位映射为 null（与 _audit_view 同口径）。"""
    verdict = record.get("verdict")
    return None if verdict == MERGE_VERDICT else verdict


def _json_row(record: dict, state: dict, merge_view: dict | None) -> dict:
    """JSON 导出行：完整审计行 + 派生列（merge 行附逐字段三栏视图）。"""
    row = {
        "audit_id": record["id"],
        "ai_suggestion_id": str(record["id"]),
        "task_id": record["task_id"],
        "batch_id": record["batch_id"],
        "item_kind": record["item_kind"],
        "item_id": record["item_id"],
        "provider": record["provider"],
        "model": record["model"],
        "model_kind": record.get("model_kind") or "llm",
        "prompt_version": record["prompt_version"],
        "status": record["status"],
        "verdict": _verdict_out(record),
        "confidence": record["confidence"],
        "rationale_zh": record["rationale_zh"],
        "field_picks": _json_object(record.get("field_picks_json")),
        "warnings": _json_list(record.get("warnings_json")),
        "executed": bool(state["executed"]),
        "undone": bool(state["undone"]),
        "candidate_status": state["candidate_status"],
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
    }
    if merge_view is not None:
        row["keeper"] = merge_view.get("keeper")
        row["fields"] = merge_view.get("fields") or []
    return row


def register_import_ai_report_routes(app, require_user, task_lookup) -> None:
    """注册 2.1.5 aiux 波 B/C 端点（依赖注入与 import_ai_assist_api 同款模式）。"""

    def _context(task_id: str, user: dict):
        try:
            ctx = app.state.review_workflow.context(task_id, user)  # 非成员 → 403
        except HTTPException:
            raise
        except (sqlite3.Error, OSError) as exc:
            # 与 assist 端点同口径（终审 #32 场景 A）：上下文存储故障 503，不 500。
            raise _storage_unavailable() from exc
        if ctx.owner is None:
            # 审计数据按属主库权威（与 assist 端点同判）。
            raise _error(403, "import_ai_report_owner_required",
                         "import_ai_report.owner_required", {},
                         "导入复核需要已验证的任务属主。")
        return ctx

    def _with_rows(task_id: str, ctx, batch_id: str | None):
        """打开属主库只读连接并一次取全：审计行 + 派生状态（+merge 视图）。

        属主库文件不存在（任务从未开过导入复核）→ 空数据而非报错。
        """
        owner_db = Path(ctx.owner_db)
        if not owner_db.is_file():
            return []
        try:
            with closing(workflow.connect(owner_db, read_only=True)) as conn:
                prepared: list[tuple[dict, dict, dict | None]] = []
                for record in _audit_rows(conn, task_id, batch_id):
                    state = _derived_state(conn, record)
                    merge_view = None
                    if record["item_kind"] == "merge_conflict":
                        merge_view = _audit_view(
                            {**record, "_trail": state["trail"],
                             "_candidate_status": state["candidate_status"]})
                    prepared.append((record, state, merge_view))
                return prepared
        except sqlite3.Error as exc:
            raise _storage_unavailable() from exc

    @app.get(_EXPORT_ROUTE_PATH)
    def assist_audit_export(
            task_id: str, format: str = "", batch_id: str | None = None,
            user: dict = Depends(require_user)) -> Response:
        """B 审计导出（参与者可读；CSV/JSON 附件；format 非法 → 422）。"""
        info = task_lookup(task_id)
        ctx = _context(task_id, user)
        if format not in EXPORT_FORMATS:
            raise _error(
                422, "import_ai_report_format_invalid",
                "import_ai_report.format_invalid",
                {"format": format, "allowed": list(EXPORT_FORMATS)},
                "导出格式只支持 csv 或 json。")
        prepared = _with_rows(info.task_id, ctx, batch_id)
        if format == "csv":
            output = io.StringIO(newline="")
            writer = csv.writer(output)
            writer.writerow(_CSV_COLUMNS)
            for record, state, _view in prepared:
                # executed/undone 用小写 true/false（CSV 布尔约定），公式
                # 注入防护逐单元格生效（含 model / rationale_zh 等文本列）。
                writer.writerow([
                    _csv_cell(cell) for cell in (
                        record["id"], record["batch_id"], record["item_kind"],
                        record["item_id"], _verdict_out(record),
                        record["confidence"], record["model"],
                        record.get("model_kind") or "llm", record["provider"],
                        record["prompt_version"], record["status"],
                        "true" if state["executed"] else "false",
                        "true" if state["undone"] else "false",
                        record["created_at"])])
            data = ("\ufeff" + output.getvalue()).encode("utf-8")
            return _attachment(data, "text/csv; charset=utf-8",
                               "import-ai-assist-audit.csv")
        payload = {"task_id": info.task_id, "count": len(prepared),
                   "rows": [_json_row(record, state, view)
                            for record, state, view in prepared]}
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        return _attachment(data, "application/json; charset=utf-8",
                           "import-ai-assist-audit.json")

    # 刚注册的 export 路由对象（scorecard 尚未注册，此刻必是最后一条）。
    export_route = app.router.routes[-1]

    @app.get(_SCORECARD_ROUTE_PATH)
    def assist_scorecard(task_id: str,
                         user: dict = Depends(require_user)) -> dict:
        """C 记分卡（参与者可读；纯只读聚合，绝不写库）。"""
        info = task_lookup(task_id)
        ctx = _context(task_id, user)
        prepared = _with_rows(info.task_id, ctx, None)
        counts = {"duplicate_group": [0, 0, 0], "metadata_diff": [0, 0, 0]}
        fields_compared = fields_agree = 0
        for record, state, view in prepared:
            kind = record["item_kind"]
            if kind == "merge_conflict":
                # merge 只做字段级一致（human_final 派生已由 _audit_view 给出）。
                if not (view or {}).get("executed"):
                    continue
                for row in (view or {}).get("fields") or []:
                    pick = row.get("prefill") or "unsure"
                    human_final = row.get("human_final")
                    if pick == "unsure" or not human_final:
                        continue  # AI unsure / 无人工终值 → 不进字段分母
                    ai_side = ("keeper" if pick == "left"
                               else row.get("right_record_id"))
                    fields_compared += 1
                    if human_final == ai_side:
                        fields_agree += 1
                continue
            if kind not in counts:
                continue
            verdict = record.get("verdict")
            human = state["human_side"]
            if human is None:
                continue  # 尚无人工最终判定 → 不进样本
            if verdict == "unsure":
                counts[kind][2] += 1
            elif ((verdict == "same" and human == "duplicate")
                    or (verdict == "different" and human == "not_duplicate")):
                counts[kind][0] += 1
            elif verdict in ("same", "different"):
                counts[kind][1] += 1
        by_kind = {kind: _bucket(*tallies) for kind, tallies in counts.items()}
        top = _bucket(*(sum(tallies) for tallies in zip(*counts.values())))
        return {
            "task_id": info.task_id,
            "read_only": True,
            "total_compared": top["total_compared"],
            "agree": top["agree"],
            "disagree": top["disagree"],
            "ai_unsure": top["ai_unsure"],
            "agreement_rate": top["agreement_rate"],
            "by_kind": {
                **by_kind,
                "merge_conflict": {
                    "fields_compared": fields_compared,
                    "fields_agree": fields_agree,
                    "field_agreement_rate": (
                        fields_agree / fields_compared) if fields_compared else None,
                },
            },
        }

    # 既有 ``GET /audit/{audit_id}``（int 路径参数）先注册：Starlette 按注册
    # 顺序取第一个全匹配，字面量 ``export`` 落在其后会先撞上该路由的 int
    # 解析 → 422。把 export 路由挪到它前面（仅调顺序，路由数量不变，可选
    # 扩展表失败回滚逻辑不受影响；找不到遮蔽路由时保持原序不动）。
    routes = app.router.routes
    for index, existing in enumerate(routes):
        if getattr(existing, "path", None) == _SHADOW_ROUTE_PATH:
            routes.remove(export_route)
            routes.insert(min(index, len(routes)), export_route)
            break
