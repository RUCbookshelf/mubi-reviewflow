"""导入复核 AI 助手（BYOK，2026-11-07 用户设计裁定）。

职责：给导入复核的**待处理项**逐条生成只读建议——重复组是否同一篇文献、
元数据差异逐字段取哪一侧——并逐条落审计表。任何确认仍走导入复核既有
动作端点（``/candidates/{review_id}``、``/metadata/{record_id}``），本模块
**绝不代存**任何复核决定。

设计裁定落点：
- 逐条建议 + 人工确认：响应只含建议；``read_only=true`` 明示无副作用。
- 批量模式：单次请求最多 ``MAX_ITEMS_PER_REQUEST`` 条（默认 20），超出 400。
- 审计留痕：新表 ``import_ai_suggestions``（task/batch/条目/模型名/请求摘要
  哈希/结果/时间），唯一索引使同一请求幂等（重复请求更新同一条）。
- 低置信明说无法判断：``confidence < 0.6``、解析失败、出站失败一律
  ``verdict=unsure``，绝不硬给 same/different。
- 默认关、显式触发：任务级开关 ``import_ai_assist_settings.json``（与
  ``ai_suggest_settings.json`` 同落点=任务目录，缺失/损坏回落默认关）；
  端点只在用户显式调用时出站，无任何后台/定时请求。
- 无钥匙隐藏入口：无可用钥匙 → 404 结构化 guidance（前端据此隐藏入口）。
- 复用 ai_suggest 钥匙柜与请求封装：``ai_keys`` 保险柜 +
  ``ai_suggest_api.resolve_task_credentials`` + ``ai_suggest_api._call_provider``
  （不重写出站客户端）。
- 只发题录/摘要级数据：出站侧题录逐键白名单
  ``title/authors/year/journal/doi/abstract``，全文、笔记、筛选决定一律不出
  （见 ``_bibliographic`` 与负钉测试）。

端到端错误口径：参数/条目问题 → 400/404 结构化 code；钥匙与开关 → 404
结构化 code；出站失败 → 逐条降级 unsure（HTTP 200），绝不 500。
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from coscreen import review_imports as store
from coscreen import review_workflow as workflow
from coscreen import tasks as tasks_mod
from custom_backend import ai_keys
from custom_backend.ai_suggest_api import (
    ProviderError,
    _FALLBACK_MODEL,
    _atomic_write_json,
    _call_provider,
    _load_json_defaults,
    _load_prefs,
    _load_settings,
    resolve_task_credentials,
)

__all__ = ["register_import_ai_assist_routes"]

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

PROMPT_VERSION = "import-ai-assist/v1"
#: 单次请求条目上限（批量模式预算；超出 → HTTP 400）。
MAX_ITEMS_PER_REQUEST = 20
#: 出站短超时：导入复核是交互式逐条建议，宁可快速降级也不长时间挂起。
OUTBOUND_TIMEOUT_SECONDS = 10.0
#: 低于该置信度一律 unsure（低置信明说无法判断）。
MIN_CONFIDENCE = 0.6
#: 连续出站失败达到该次数后，剩余条目不再出站（避免持续消耗额度/时间）。
MAX_CONSECUTIVE_FAILURES = 2

#: 出站题录字段白名单（设计裁定：只发题录/摘要级数据）。
SENT_FIELDS: tuple[str, ...] = ("title", "authors", "year", "journal", "doi", "abstract")
#: metadata_diff 的逐字段选择键（与 SENT_FIELDS 一致）。
FIELD_PICK_KEYS = frozenset(SENT_FIELDS)

MAX_TITLE_CHARS = 300
MAX_AUTHORS_CHARS = 300
MAX_JOURNAL_CHARS = 200
MAX_DOI_CHARS = 120
MAX_ABSTRACT_CHARS = 2_000
MAX_RATIONALE_CHARS = 400
MAX_DIGEST_INPUT_CHARS = 60_000

_SETTINGS_FILENAME = "import_ai_assist_settings.json"
_DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": False,                       # 默认关
    "acknowledged_at": None,
    "version": 0,
}

_SYSTEM_PROMPT = (
    "你是系统综述导入复核的研究助理，只判断给定两侧题录是否指向同一篇文献。"
    "规则：\n"
    "1. 只能依据用户消息给出的题录字段作答，不得使用题录之外的信息；不确定就说不确定。\n"
    '2. 只输出一个 JSON 对象，不要解释文本或代码块：{"verdict":"same|different|unsure",'
    '"confidence":<0 到 1 的小数>,"rationale_zh":"<一句中文理由>",'
    '"field_picks":{"<字段>":"left|right|unsure"}}\n'
    "3. 证据不足时 verdict 必须是 unsure；绝不允许把不确定说成确定。\n"
    "4. field_picks 只在字段级差异时给出，键只能取 title,authors,year,journal,doi,abstract；"
    "没有字段级判断时给 {}。"
)

_AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS import_ai_suggestions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  batch_id TEXT NOT NULL,
  item_kind TEXT NOT NULL CHECK(item_kind IN ('duplicate_group','metadata_diff')),
  item_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  request_digest TEXT NOT NULL,
  verdict TEXT NOT NULL CHECK(verdict IN ('same','different','unsure')),
  confidence REAL,
  rationale_zh TEXT NOT NULL,
  field_picks_json TEXT,
  warnings_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL CHECK(status IN ('ok','degraded')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_import_ai_suggestions_request
  ON import_ai_suggestions(task_id,batch_id,item_kind,item_id,request_digest);
"""

_AUDIT_UPSERT = (
    "INSERT INTO import_ai_suggestions(task_id,batch_id,item_kind,item_id,provider,model,"
    "prompt_version,request_digest,verdict,confidence,rationale_zh,field_picks_json,"
    "warnings_json,status,created_at,updated_at) "
    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
    "ON CONFLICT(task_id,batch_id,item_kind,item_id,request_digest) DO UPDATE SET "
    "verdict=excluded.verdict,confidence=excluded.confidence,"
    "rationale_zh=excluded.rationale_zh,field_picks_json=excluded.field_picks_json,"
    "warnings_json=excluded.warnings_json,status=excluded.status,"
    "updated_at=excluded.updated_at"
)

_AUDIT_LOOKUP = (
    "SELECT id FROM import_ai_suggestions WHERE task_id=? AND batch_id=? AND item_kind=? "
    "AND item_id=? AND request_digest=?"
)

_SAFE_WARNING_RE = re.compile(r"[^a-z0-9_]")


# ---------------------------------------------------------------------------
# 任务级设置（与 ai_suggest_settings.json 同落点=任务目录，可加且幂等）
# ---------------------------------------------------------------------------

def _settings_path(info) -> Path:
    return info.dir / _SETTINGS_FILENAME


def load_settings(info) -> dict:
    settings = _load_json_defaults(_settings_path(info), _DEFAULT_SETTINGS)
    settings["enabled"] = bool(settings.get("enabled"))
    try:
        settings["version"] = max(0, int(settings.get("version") or 0))
    except (TypeError, ValueError):
        settings["version"] = 0
    return settings


# ---------------------------------------------------------------------------
# 题录白名单（出站只发这些；负钉见 build/tests/test_2_1_5_import_ai_assist.py）
# ---------------------------------------------------------------------------

def _clip(value: Any, limit: int) -> str:
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()[:limit]


def _year_value(value: Any) -> int | str:
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return value
    text = _clip(value, 20)
    return int(text) if text.isdigit() else text


def _bibliographic(article: dict | None) -> dict:
    """从任意题录/文章行中只挑出出站白名单字段，其余（笔记、全文、决定、
    raw/url/键）一概不出现——这是"只发题录/摘要级数据"的服务端强制点。"""
    article = article if isinstance(article, dict) else {}
    return {
        "title": _clip(article.get("title"), MAX_TITLE_CHARS),
        "authors": _clip(article.get("authors"), MAX_AUTHORS_CHARS),
        "year": _year_value(article.get("year")),
        "journal": _clip(article.get("journal"), MAX_JOURNAL_CHARS),
        "doi": _clip(article.get("doi"), MAX_DOI_CHARS),
        "abstract": _clip(article.get("abstract"), MAX_ABSTRACT_CHARS),
    }


def _parse_json_object(text: Any) -> dict | None:
    if isinstance(text, dict):
        return text
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _item_not_found(detail: str) -> HTTPException:
    return HTTPException(404, {"code": "import_ai_assist_item_not_found", "detail": detail})


def _item_unavailable(detail: str) -> HTTPException:
    return HTTPException(409, {"code": "import_ai_assist_item_unavailable", "detail": detail})


# ---------------------------------------------------------------------------
# 从导入复核既有状态取两侧题录（只读；不写任何复核决定）
# ---------------------------------------------------------------------------

def load_item_payload(conn: sqlite3.Connection, batch_id: str, kind: str, item_id: str) -> dict:
    """返回发给模型的用户载荷：duplicate_group → members 列表；
    metadata_diff → left(现有)/right(导入)。每条只含 ``SENT_FIELDS``。"""
    if kind == "duplicate_group":
        try:
            candidate = store.candidate_row(conn, batch_id, item_id)
        except KeyError as exc:
            raise _item_not_found("重复组不存在或不属于该批次。") from exc
        except ValueError as exc:  # canonical 解析失败/环：可重试的数据状态，不 500
            raise _item_unavailable("重复组当前无法解析，请刷新导入复核后重试。") from exc
        if candidate.get("status") not in ("pending", "undecided"):
            raise _item_not_found("重复组已处理，无需 AI 建议。")
        members: list[dict] = []
        for member in candidate.get("members") or []:
            article = (member.get("article") if member.get("ref_type") == "active"
                       else (member.get("record") or {}).get("article"))
            if isinstance(article, dict):
                members.append(_bibliographic(article))
        if len(members) < 2:
            raise _item_unavailable("重复组可用题录不足两条。")
        return {"kind": "duplicate_group", "members": members}

    # metadata_diff：记录必须属于该批次；两侧取既有提案的 before/incoming 快照。
    membership = conn.execute(
        "SELECT 1 FROM workflow_import_members WHERE batch_id=? AND record_id=?",
        (batch_id, item_id)).fetchone()
    if membership is None:
        raise _item_not_found("元数据差异条目不存在或不属于该批次。")
    row = conn.execute(
        "SELECT before_json,incoming_json FROM workflow_import_metadata "
        "WHERE record_id=? AND action IS NULL",
        (item_id,)).fetchone()
    if row is None:
        raise _item_not_found("元数据差异条目不存在（可能已处理）。")
    before, incoming = _parse_json_object(row["before_json"]), _parse_json_object(row["incoming_json"])
    if before is None or incoming is None:
        raise _item_unavailable("元数据差异快照损坏，请刷新导入复核后重试。")
    return {"kind": "metadata_diff", "left": _bibliographic(before),
            "right": _bibliographic(incoming)}


# ---------------------------------------------------------------------------
# 模型输出解析：低置信/解析失败 → unsure（绝不硬给建议）
# ---------------------------------------------------------------------------

def _confidence(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):  # NaN/Inf
        return None
    return min(1.0, max(0.0, number))


def _unsure(rationale: str, warnings: list[str]) -> dict:
    return {"verdict": "unsure", "confidence": None, "rationale_zh": rationale,
            "field_picks": None, "warnings": list(warnings), "degraded": True}


def parse_reply(content: Any, kind: str) -> dict:
    """解析模型回复；返回 ``{verdict,confidence,rationale_zh,field_picks,warnings,degraded}``。

    任何不能确证的形状都降级为 ``unsure``：非 JSON/缺 verdict/缺 confidence/
    置信度 < ``MIN_CONFIDENCE`` 一律不给 same/different。
    """
    text = str(content or "").strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    parsed = None
    if start >= 0 and end > start:
        parsed = _parse_json_object(text[start:end + 1])
    if parsed is None:
        return _unsure("模型输出无法解析，无法判断。", ["provider_response_not_json"])

    warnings: list[str] = []
    degraded = False
    verdict = str(parsed.get("verdict") or "").strip().lower()
    rationale = _clip(parsed.get("rationale_zh"), MAX_RATIONALE_CHARS)
    confidence = _confidence(parsed.get("confidence"))

    if verdict not in ("same", "different", "unsure"):
        verdict, degraded = "unsure", True
        warnings.append("invalid_verdict")
    if confidence is None:
        verdict, degraded = "unsure", True
        warnings.append("confidence_missing")
    elif verdict != "unsure" and confidence < MIN_CONFIDENCE:
        verdict, degraded = "unsure", True
        warnings.append("low_confidence")
        note = f"置信度 {confidence:.2f} 低于 {MIN_CONFIDENCE:.2f}，无法判断。"
        rationale = note + (f"（模型理由：{rationale}）" if rationale else "")

    field_picks = None
    if kind == "metadata_diff" and parsed.get("field_picks") is not None:
        raw_picks = parsed.get("field_picks")
        if isinstance(raw_picks, dict):
            field_picks = {}
            for key, value in raw_picks.items():
                text_value = str(value or "").strip().lower()
                if key in FIELD_PICK_KEYS and text_value in ("left", "right", "unsure"):
                    field_picks[key] = text_value
                else:
                    warnings.append("field_pick_dropped")
            field_picks = field_picks or None
        else:
            warnings.append("field_picks_invalid")

    if not rationale:
        rationale = "证据不足，无法判断。" if verdict == "unsure" else "模型未给出理由。"
    # 去重且只保留机器可读 token，避免把模型文本原样带进日志/界面提示。
    clean: list[str] = []
    for warning in warnings:
        token = _SAFE_WARNING_RE.sub("", str(warning).lower())[:40]
        if token and token not in clean:
            clean.append(token)
    return {"verdict": verdict, "confidence": confidence, "rationale_zh": rationale,
            "field_picks": field_picks, "warnings": clean, "degraded": degraded}


def _request_digest(system: str, user: str, model: str) -> str:
    canonical = json.dumps({"prompt_version": PROMPT_VERSION, "model": model,
                            "system": system, "user": user},
                           ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical[:MAX_DIGEST_INPUT_CHARS].encode("utf-8")).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

class AssistItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batch_id: str = Field(min_length=1, max_length=128)
    kind: Literal["duplicate_group", "metadata_diff"]
    item_id: str = Field(min_length=1, max_length=128)


class AssistIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # 上限 1000 只为挡住异常载荷；产品上限 MAX_ITEMS_PER_REQUEST=20 走 400 结构化口径。
    items: list[AssistItem] = Field(min_length=1, max_length=1000)


class AssistSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    enabled: bool = False


def register_import_ai_assist_routes(app, require_user, task_lookup, data_dir) -> None:
    """注册导入复核 AI 助手路由（依赖注入与 ai_suggest_api 同款模式）。"""
    data_root = Path(data_dir)

    def _workflow():
        return app.state.review_workflow

    def _context(task_id: str, user: dict):
        ctx = _workflow().context(task_id, user)  # 非任务成员 → 403
        if ctx.owner is None:
            # 导入复核数据按属主库权威（与 review_imports_api.read_context 同口径）。
            raise HTTPException(403, {"code": "import_ai_assist_owner_required",
                                      "detail": "导入复核需要已验证的任务属主。"})
        return ctx

    def _credentials(info, user: dict):
        """复用 ai_suggest 的钥匙口径（个人 > 共享 > 无）。"""
        ai_settings = _load_settings(info)
        prefs = _load_prefs(info, user["username"])
        credentials, key_source = resolve_task_credentials(
            info, ai_settings, prefs, user, data_root)
        model = ((credentials or {}).get("model") or "").strip() or _FALLBACK_MODEL
        return credentials, key_source, model

    def _settings_view(info, user: dict, ctx, settings: dict) -> dict:
        credentials, key_source, model = _credentials(info, user)
        return {
            "enabled": bool(settings.get("enabled")),
            "version": int(settings.get("version") or 0),
            "can_manage": bool(ctx.can_manage),
            "has_key": credentials is not None,
            "key_source": key_source,
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "min_confidence": MIN_CONFIDENCE,
            "max_items": MAX_ITEMS_PER_REQUEST,
            "sent_fields": list(SENT_FIELDS),
            "acknowledged_at": settings.get("acknowledged_at"),
        }

    @app.get("/api/tasks/{task_id}/import-review/ai-assist/settings")
    def get_assist_settings(task_id: str, user: dict = Depends(require_user)) -> dict:
        """参与者可读（入口显隐依据：enabled + has_key）；绝无钥匙材料。"""
        info = task_lookup(task_id)
        ctx = _context(task_id, user)
        return _settings_view(info, user, ctx, load_settings(info))

    @app.put("/api/tasks/{task_id}/import-review/ai-assist/settings")
    def put_assist_settings(task_id: str, body: AssistSettingsIn,
                            user: dict = Depends(require_user)) -> dict:
        """负责人开关（默认关）；expected_version 乐观锁。"""
        info = task_lookup(task_id)
        ctx = _context(task_id, user)
        if not ctx.can_manage:
            raise HTTPException(403, {"code": "import_ai_assist_settings_owner_only",
                                      "detail": "只有任务负责人可以修改导入复核 AI 助手开关。"})
        with tasks_mod._task_json_lock(info.dir):  # noqa: SLF001  读改写走同一条任务锁
            settings = load_settings(info)
            if body.expected_version != settings["version"]:
                raise HTTPException(409, {"code": "import_ai_assist_settings_version_conflict",
                                          "version": settings["version"]})
            was_enabled = bool(settings["enabled"])
            settings["enabled"] = bool(body.enabled)
            settings["version"] = int(settings["version"]) + 1
            if body.enabled and not was_enabled and not settings.get("acknowledged_at"):
                settings["acknowledged_at"] = _utc_now_iso()
            _atomic_write_json(_settings_path(info), settings)
        return _settings_view(info, user, ctx, settings)

    @app.post("/api/tasks/{task_id}/import-review/ai-assist")
    def assist(task_id: str, body: AssistIn, user: dict = Depends(require_user)) -> dict:
        info = task_lookup(task_id)
        ctx = _context(task_id, user)

        # 1) 开关（默认关）——先于任何出站与条目解析。
        settings = load_settings(info)
        if not settings["enabled"]:
            raise HTTPException(404, {
                "code": "import_ai_assist_disabled",
                "detail": "任务未启用导入复核 AI 助手。",
                "guidance": "导入复核 AI 助手默认关闭；请由任务负责人开启后重试。",
            })

        # 2) 条目上限（批量预算）——先于条目解析与出站。
        if len(body.items) > MAX_ITEMS_PER_REQUEST:
            raise HTTPException(400, {
                "code": "import_ai_assist_too_many_items",
                "detail": f"单次最多 {MAX_ITEMS_PER_REQUEST} 条待处理项。",
                "max_items": MAX_ITEMS_PER_REQUEST,
            })
        seen: set[tuple[str, str, str]] = set()
        for ref in body.items:
            key = (ref.batch_id, ref.kind, ref.item_id)
            if key in seen:
                raise HTTPException(400, {
                    "code": "import_ai_assist_duplicate_item",
                    "detail": "同一条待处理项不能在一次请求里重复出现。",
                })
            seen.add(key)

        # 3) 钥匙（无钥匙 → 404 结构化 guidance；零出站）。
        credentials, key_source, model = _credentials(info, user)
        if credentials is None:
            raise HTTPException(404, {
                "code": "import_ai_assist_key_missing",
                "detail": "尚未配置可用的 AI 钥匙。",
                "guidance": "请先配置个人 AI 钥匙，或由任务负责人配置任务共享钥匙后再试。",
            })
        try:  # 存量钥匙记录的非法 base_url 映射 422，不冒泡 500。
            base_url = ai_keys.base_url_of(credentials)
        except ai_keys.KeyShapeError as exc:
            raise HTTPException(422, str(exc)) from None

        # 4) 审计表与既有导入复核表就位（幂等；先于出站，出站前失败不花预算）。
        store.migrate(ctx.owner_db)
        with closing(workflow.connect(ctx.owner_db)) as conn:
            _ensure_audit_schema(conn)

        # 5) 一次性解析全部条目（只读）：任何无效引用 → 404，绝不部分出站。
        try:
            with closing(workflow.connect(ctx.owner_db, read_only=True)) as conn:
                payloads = [load_item_payload(conn, ref.batch_id, ref.kind, ref.item_id)
                            for ref in body.items]
        except sqlite3.OperationalError as exc:
            raise _item_not_found("导入复核数据不可用，请刷新后重试。") from exc

        # 6) 逐条出站（短超时；失败逐条降级 unsure，连续失败即停止出站）。
        results: list[dict] = []
        consecutive_failures = 0
        for ref, payload in zip(body.items, payloads):
            user_message = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            digest = _request_digest(_SYSTEM_PROMPT, user_message, model)
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                suggestion = _unsure("模型连续调用失败，已跳过该条以避免持续消耗额度。",
                                     ["provider_unavailable"])
            else:
                try:
                    reply = _call_provider(_SYSTEM_PROMPT, user_message,
                                           api_key=credentials["key"], base_url=base_url,
                                           model=model, timeout=OUTBOUND_TIMEOUT_SECONDS)
                    suggestion = parse_reply(reply.get("content"), ref.kind)
                    consecutive_failures = 0
                    if key_source == "personal":
                        ai_keys.touch_last_used(ai_keys.personal_path(
                            user["user_id"], data_root))
                    else:
                        ai_keys.touch_last_used(ai_keys.shared_path(
                            info.task_id, data_root))
                except ProviderError:
                    suggestion = _unsure("模型调用失败，无法判断。", ["provider_error"])
                    consecutive_failures += 1
            suggestion.update({
                "batch_id": ref.batch_id,
                "kind": ref.kind,
                "item_id": ref.item_id,
                "model": model,
                "request_digest": digest,
            })
            results.append(suggestion)

        # 7) 审计留痕（唯一索引幂等；这里是本端点对库的唯一写入）。
        now = _utc_now_iso()
        provider = credentials.get("provider") or ""
        with closing(workflow.connect(ctx.owner_db)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            for entry in results:
                status = "degraded" if entry["degraded"] else "ok"
                conn.execute(_AUDIT_UPSERT, (
                    info.task_id, entry["batch_id"], entry["kind"], entry["item_id"],
                    provider, model, PROMPT_VERSION, entry["request_digest"],
                    entry["verdict"], entry["confidence"], entry["rationale_zh"],
                    json.dumps(entry["field_picks"], ensure_ascii=False)
                    if entry["field_picks"] is not None else None,
                    json.dumps(entry["warnings"], ensure_ascii=False), status,
                    now, now))
                audit_row = conn.execute(_AUDIT_LOOKUP, (
                    info.task_id, entry["batch_id"], entry["kind"], entry["item_id"],
                    entry["request_digest"])).fetchone()
                entry["audit_id"] = int(audit_row[0])

        counts = {"same": 0, "different": 0, "unsure": 0}
        for entry in results:
            counts[entry["verdict"]] += 1
        return {
            "status": "ok",
            "read_only": True,               # 建议只读：确认仍走导入复核既有动作端点
            "prompt_version": PROMPT_VERSION,
            "model": model,
            "key_source": key_source,
            "suggestions": [{
                "batch_id": entry["batch_id"], "kind": entry["kind"],
                "item_id": entry["item_id"], "verdict": entry["verdict"],
                "confidence": entry["confidence"], "rationale_zh": entry["rationale_zh"],
                "field_picks": entry["field_picks"], "degraded": entry["degraded"],
                "warnings": entry["warnings"], "audit_id": entry["audit_id"],
            } for entry in results],
            "counts": counts,
        }


def _ensure_audit_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_AUDIT_SCHEMA)
