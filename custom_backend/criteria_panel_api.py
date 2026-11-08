"""标准对照常驻卡 API（2.1.5 W8）——任务级只读聚合端点。

单一只读端点：``GET /api/tasks/{task_id}/criteria-panel``

返回 ``{"inclusion": [...], "exclusion": [...],
"key_terms": [{"term", "definition"}, ...], "has_protocol": bool}``。

数据来源唯一：``task.json`` 的 ``protocol`` 字段（方案设计器保存的那一份）。
本模块**不引入任何新的存储**、**不提供任何写入口**——设计文档 §五 明确
「不做面板内直接编辑（写路径唯一=方案设计器）」；面板"编辑"入口跳方案
设计器，前端据此渲染（契约 frontend_contract_2.1.5-w8-criteria-panel.md）。

治理红线落实：

- **零写入（任务数据）**：本端点不写任何数据——只经 ``tasks._read_task_json``
  读一次 ``task.json``（该函数无锁、无创建、无 ``.tmp`` 落盘），不 ``init_db``、
  不建库建表、不写轨迹表、不碰筛选员库。注意任务解析阶段 ``find_task`` 已经
  读过一次 ``task.json``（``task_or_404``），所以「只读一次」应理解为「端点
  函数体自身只再读一次」。钉子在**调用前后对整个任务目录**逐文件比对 sha256
  **与** ``st_mtime_ns``（等值重写也会被检出）。
  披露（评审 r2/r3 核正，勿再声称「不打开任何 SQLite 库」）：
  - 端点函数内的 `task_visible` 兜底与全局 `require_user` 闸门都会**以只读
    方式**打开协作库（``collaboration.db?mode=ro``）——这是本端点代码路径上的
    SQLite 打开，只是零写入；
  - 令牌校验经既有 ``auth._connect()`` 打开 ``DATA_DIR/users.db``，该连接是
    **普通读写连接**（``auth.py:84-92``：``PRAGMA journal_mode=WAL`` + 幂等
    建表 ``executescript``），不是 ``mode=ro``；它只做幂等 DDL 与 ``SELECT``；
  - ``TaskLockMiddleware`` 按既有行为对每个任务级请求创建再删除
    ``{taskId}.lock``（瞬态文件）。
  以上都不是本波引入的写入，终态零变化由钉子（任务目录 sha/mtime）与整份
  ``DATA_DIR`` 的实测（sha+mtime 无差异）共同覆盖。
- **零出站**：纯内存 JSON 解析，无网络、无定时/后台触发。
- **协议数据畸形绝不 500**：协议字段非 dict / 列表里混入非字符串 → 按「无
  该项」处理（空清单），不是错误。``task.json`` **内容损坏**（非法 JSON，
  ``_read_task_json`` 返回 ``{}``）与「尚未保存方案」同口径 →
  ``has_protocol=false`` + 空清单（与既有 ``GET /protocol`` 的
  ``_task_protocol`` 判定一致）。
  边界（如实披露）：``task.json`` **文件不存在**时 ``find_task`` 返回 None，
  整个请求在 ``require_user`` 闸门即 **404**（任务不存在），到不了本端点；
  病态深嵌套 JSON 触发的 ``RecursionError`` 属既有 ``tasks._read_task_json``
  口径（只捕 ``OSError``/``ValueError``），不在本端点兜底范围内。
- **权限**：任务属主与已授权成员（coder/reconciler）可读；非参与者 403。
  线上 403 通常由 ``require_user`` 的参与权闸门先行给出（历史裸文案）；本
  端点内另有一道 ``task_visible`` 结构化兜底（``criteria_panel.task_forbidden``），
  当前闸门下不可达、作为纵深防御保留（有直调钉覆盖其分支）。
- **隐私**：只读本应用自身任务目录，无回环/外部端点访问。
"""

from __future__ import annotations

from fastapi import Depends, HTTPException

from coscreen import tasks as tasks_mod
from coscreen.protocol_designer import key_terms_from_dict

__all__ = ["register_criteria_panel_routes"]

#: 单份方案的清单长度上限（防御性上限：task.json 由方案设计器写入，正常远小于此）。
#: 超限截断而非报错——面板是辅助件，缺尾部条目好过整块不可用。
_MAX_ITEMS = 500


def _str_list(raw: object) -> list[str]:
    """清单字段归一：非 list → 空；非标量条目跳过；去首尾空白后丢弃空串。"""
    if not isinstance(raw, list):
        return []
    items: list[str] = []
    for item in raw:
        if isinstance(item, (str, int, float)) and not isinstance(item, bool):
            text = str(item).strip()
            if text:
                items.append(text)
        if len(items) >= _MAX_ITEMS:
            break
    return items


def _panel_payload(protocol: object) -> dict:
    """``task.json`` 的 protocol 字段 → 面板载荷（畸形一律按空处理）。"""
    if not isinstance(protocol, dict) or not protocol:
        return {"inclusion": [], "exclusion": [], "key_terms": [],
                "has_protocol": False}
    terms = [{"term": key_term.term, "definition": key_term.definition}
             for key_term in key_terms_from_dict(protocol.get("key_terms"))
             ][:_MAX_ITEMS]
    return {
        "inclusion": _str_list(protocol.get("inclusion_criteria")),
        "exclusion": _str_list(protocol.get("exclusion_criteria")),
        "key_terms": terms,
        "has_protocol": True,
    }


def register_criteria_panel_routes(app, require_user, task_or_404,
                                   task_visible) -> None:
    """在 ``app`` 上注册标准对照常驻卡端点（只读；main.py 追加式注册行调用）。"""

    @app.get("/api/tasks/{task_id}/criteria-panel")
    def criteria_panel(task_id: str, user: dict = Depends(require_user)) -> dict:
        """纳入/排除标准与关键术语的只读聚合（数据 = task.json 的 protocol）。"""
        # 门禁：任务不存在 404；不可见（非属主且非参与者）403。
        # 纵深防御：HTTP 请求在进入本函数前已被 `require_user` 的参与权闸门
        # 统一拦下（本分支因此在线上不可达），保留它是为了端点自身可独立
        # 复用/被直调时仍然 fail-closed；`test_inner_visibility_guard_is_structured_403`
        # 直接调用端点函数钉住该分支。
        info = task_or_404(task_id)
        if not task_visible(info, user):
            raise HTTPException(403, {
                "error_code": "task_forbidden",
                "message_key": "criteria_panel.task_forbidden",
            })
        try:
            raw = tasks_mod._read_task_json(info.dir)  # noqa: SLF001 与 update_task 同一条读路径
        except OSError:
            # 目录/文件不可读（权限、IO 抖动）：降级为「无方案」，不 500。
            return _panel_payload(None)
        protocol = raw.get("protocol") if isinstance(raw, dict) else None
        return _panel_payload(protocol)
