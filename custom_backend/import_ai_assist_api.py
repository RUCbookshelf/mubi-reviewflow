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

2.1.5 G5 第二段（``kind=merge_conflict``，AI 冲突建议）：
- 服务端从候选组取保留者（请求给定或 B2 的 ``suggested_keeper``）与被合并
  成员题录，算出冲突字段：两边都有值但不同；或保留条目某字段为空、多个被
  合并成员给出不同值（设计 §七.3）。
- 两层处理（省额度、行为可预期）：**确定性预判层**零出站（DOI 规范化后
  相等、题名仅大小写不同、作者一侧 et al. 且第一作者一致、摘要一侧是另一侧
  前缀且被截断、保留条目空字段多成员取值按完整度/导入顺序）；判不出的才进
  **AI 层**，且只有六字段白名单（标题/作者/年份/期刊/DOI/摘要）里的冲突字段
  出站——url、item_type 等永不出站（=保留条目值）。
- 预填语义（用户裁定四条件）：逐字段给 ``prefill``（仅非 unsure 时）、
  ``source``（rule/ai）、``rationale``；本端点**不写任何题录**，执行只能经
  第一段确认接口由人发起并携带 ``ai_suggestion_id``；审计逐字段可查
  ``rule_pick`` / ``ai_pick`` / ``human_final``（``human_final`` 由留痕表
  ``workflow_import_field_merges`` 经 ``ai_suggestion_id`` 关联得出）。
  「是不是重复」（duplicate_group 的 verdict）仍只高亮、不预选。
- I-6：本文件全部结构化错误都带 ``message_key`` 与 ``params``，``code`` /
  ``detail`` / ``guidance`` 保持兼容。

2.1.5 决策模型波（2026-11-10 用户裁定，Jev 提前到 2.1.5）：
- 钥匙柜条目按 ``kind`` 分派（``custom_backend/ai_keys.py``：llm=缺省/存量，
  decision=Jev 类决策模型）；本端点 ``duplicate_group`` 与 ``merge_conflict``
  在 decision 钥匙下走 ``custom_backend/decision_model_adapter.py``（systemone
  协议，统一输出形状与 LLM 适配器一致），其余路径零变化。
- **范围铁律**：决策模型只用于这两场景；``metadata_diff`` 在 decision 钥匙下
  零出站、逐条降级 unsure（``decision_kind_not_supported``）；其他 AI 端点
  （编码建议等）拿 decision 钥匙被 ``ai_suggest_api.decision_key_not_allowed``
  硬闸拒绝（409 ``ai.decision_key_not_allowed``）。
- 审计行新增 ``model_kind``（llm/decision），决策两场景用独立提示词版本
  ``import-ai-assist-decision/v1`` / ``import-ai-assist-merge-decision/v1``。
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from coscreen import review_imports as store
from coscreen import review_workflow as workflow
from coscreen import tasks as tasks_mod
from coscreen.dedup import _norm_doi, is_journal_pattern_doi
from coscreen.normalize import first_author_surname
from custom_backend import ai_keys
from custom_backend import decision_model_adapter as decision_model
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
#: merge_conflict 的建议提示词版本（与 D1 两 kind 分开：改 PROMPT_VERSION 会
#: 让既有审计行的 request_digest 与前端披露同时漂移）。
MERGE_PROMPT_VERSION = "import-ai-assist-merge/v1"
#: 单次请求条目上限（批量模式预算；超出 → HTTP 400）。
MAX_ITEMS_PER_REQUEST = 20
#: 出站短超时：导入复核是交互式逐条建议，宁可快速降级也不长时间挂起。
OUTBOUND_TIMEOUT_SECONDS = 10.0
#: 低于该置信度一律 unsure（低置信明说无法判断）。决策模型通道沿用同一阈值
#: （概率→confidence 映射后判定）；**阈值待用历史已确认样本校准**——需要用户
#: 服务器上已确认的重复/非重复历史数据，校准本身本波不做。
MIN_CONFIDENCE = 0.6
#: 连续出站失败达到该次数后，剩余条目不再出站（避免持续消耗额度/时间）。
MAX_CONSECUTIVE_FAILURES = 2

#: 出站题录字段白名单（设计裁定：只发题录/摘要级数据）。
SENT_FIELDS: tuple[str, ...] = ("title", "authors", "year", "journal", "doi", "abstract")
#: metadata_diff 的逐字段选择键（与 SENT_FIELDS 一致）。
FIELD_PICK_KEYS = frozenset(SENT_FIELDS)
#: merge_conflict 的 AI 候选字段（只在这六字段里出站；url/item_type 永不出站）。
MERGE_FIELD_KEYS = frozenset(SENT_FIELDS)

#: merge_conflict 逐字段展示长度上限（与题录白名单同口径；url 只做展示截断）。
_FIELD_LIMITS = {
    "title": 300, "authors": 300, "journal": 200, "doi": 120,
    "abstract": 2_000, "year": 20, "url": 500, "item_type": 60,
}

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

#: merge_conflict 的提示词（设计 §三 模板原文：信息完整优先、其次格式规范、
#: 两值都完整规范但实质不同 → unsure；只选不写、不得编造）。
_MERGE_SYSTEM_PROMPT = (
    "你是文献题录合并助手。下面是同一篇文献的两条题录（left=保留记录，right=另一条），\n"
    "只列出两边都有值但内容不同的字段。对每个字段从 left / right 中选一个，规则按优先级：\n"
    "\n"
    "1. 信息更完整的优先：未截断 > 截断（以\"...\"或半句结尾）；含副标题 > 无副标题；\n"
    "   作者全列 > \"et al.\"；全名 > 仅首字母缩写；期刊全称 > 缩写；完整摘要 > 节选。\n"
    "2. 信息量相当时，格式更规范的优先：正常大小写 > 全大写；无乱码、无 HTML 残留、\n"
    "   无多余前缀（如\"Abstract:\"、版权声明）；作者\"姓, 名\"格式统一；DOI 为标准形式。\n"
    "3. 以下情况必须回答 unsure：两个值都完整规范但实质内容不同（如两个不同的期刊名、\n"
    "   两个不同的合法年份、内容不同的两份摘要）；或无法确定哪个更好。\n"
    "\n"
    "硬约束：只能选 left、right 或 unsure；不得改写、合并、补写或编造任何值；\n"
    "不得用外部知识\"纠正\"题录。\n"
    "输出 JSON：{\"field_picks\": {字段: \"left\"|\"right\"|\"unsure\"},\n"
    "           \"confidence\": 0~1, \"rationale_zh\": \"一句话理由\"}"
)

#: merge_conflict 审计行的 verdict 占位（该 kind 不做「是不是重复」判断，
#: 审计 verdict 列用 n/a 明示，绝不伪装成 same/different）。
MERGE_VERDICT = "n/a"

#: decision 钥匙下 metadata_diff 的零出站范围降级行的 prompt_version 标记：
#: 该行从未出站、不属于任何提示词版本域（同 MERGE_VERDICT 的 n/a 先例），
#: 绝不记成 LLM 版本以免误导审计（评审低危项修复）。
NOT_SENT_PROMPT_VERSION = "n/a"

_AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS import_ai_suggestions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  batch_id TEXT NOT NULL,
  item_kind TEXT NOT NULL CHECK(item_kind IN ('duplicate_group','metadata_diff','merge_conflict')),
  item_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  model_kind TEXT NOT NULL DEFAULT 'llm' CHECK(model_kind IN ('llm','decision')),
  prompt_version TEXT NOT NULL,
  request_digest TEXT NOT NULL,
  verdict TEXT NOT NULL CHECK(verdict IN ('same','different','unsure','n/a')),
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

#: 2.1.5 决策模型波之前的审计表没有 model_kind 列 → ALTER 幂等加列（回填
#: 'llm'：决策模型钥匙在该波之前不存在，存量行只能是 LLM 通道；DDL 在
#: ``_ensure_audit_schema`` 内以字面量执行，无任何外部输入拼接）。

_AUDIT_COPY_SQL = (
    "INSERT OR IGNORE INTO import_ai_suggestions(id,task_id,batch_id,item_kind,item_id,"
    "provider,model,prompt_version,request_digest,verdict,confidence,rationale_zh,"
    "field_picks_json,warnings_json,status,created_at,updated_at) "
    "SELECT id,task_id,batch_id,item_kind,item_id,provider,model,prompt_version,"
    "request_digest,verdict,confidence,rationale_zh,field_picks_json,warnings_json,"
    "status,created_at,updated_at FROM import_ai_suggestions_legacy;"
)
_AUDIT_UPSERT = (
    "INSERT INTO import_ai_suggestions(task_id,batch_id,item_kind,item_id,provider,model,"
    "model_kind,prompt_version,request_digest,verdict,confidence,rationale_zh,"
    "field_picks_json,warnings_json,status,created_at,updated_at) "
    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
    "ON CONFLICT(task_id,batch_id,item_kind,item_id,request_digest) DO UPDATE SET "
    "verdict=excluded.verdict,confidence=excluded.confidence,"
    "rationale_zh=excluded.rationale_zh,field_picks_json=excluded.field_picks_json,"
    "warnings_json=excluded.warnings_json,status=excluded.status,"
    "model_kind=excluded.model_kind,updated_at=excluded.updated_at"
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
    # 终审 #09 附带观察：'²'.isdigit() 为 True 但 int() 抛 ValueError（与
    # tables._resolve_fields 同形状）——不可解析的 Unicode 数字串原样作为
    # 字符串返回，绝不 500。
    try:
        return int(text) if text.isdigit() else text
    except ValueError:
        return text


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


def _error(status: int, code: str, message_key: str, params: dict | None = None,
           detail: str = "", guidance: str | None = None,
           **extra: Any) -> HTTPException:
    """结构化错误（I-6）：``code`` / ``message_key`` / ``params`` / ``detail``。

    ``guidance`` 只用于 404 降级指引（入口隐藏类），其余错误不带。
    ``extra`` 是既有契约里的顶层兼容字段（如 ``max_items``、``version``），
    与 ``params`` 同值并列给出，旧前端读法不破。
    """
    body = {"code": code, "message_key": message_key,
            "params": dict(params or {}), "detail": detail}
    for key, value in extra.items():
        body.setdefault(key, value)
    if guidance:
        body["guidance"] = guidance
    return HTTPException(status, body)


def _item_not_found(detail: str, **params) -> HTTPException:
    return _error(404, "import_ai_assist_item_not_found",
                  "import_ai_assist.item_not_found", params, detail)


def _item_unavailable(detail: str, **params) -> HTTPException:
    return _error(409, "import_ai_assist_item_unavailable",
                  "import_ai_assist.item_unavailable", params, detail)


def _keeper_invalid(detail: str, reason: str) -> HTTPException:
    return _error(400, "import_ai_assist_keeper_invalid",
                  "import_ai_assist.keeper_invalid", {"reason": reason}, detail)


_STORAGE_UNAVAILABLE_DETAIL = (
    "任务数据库暂时不可用（可能正被其他操作占用，或磁盘只读/已满），请稍后重试。")


def _storage_unavailable() -> HTTPException:
    """sqlite/IO 故障 → 结构化 503（终审 #32，与 pdf_fetch._storage_http_error
    同语义；出站前后两个阶段的存储故障都不再落全局 500 兜底）。"""
    return _error(503, "import_ai_assist_storage_unavailable",
                  "import_ai_assist.storage_unavailable", {},
                  _STORAGE_UNAVAILABLE_DETAIL)


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
            raise _item_not_found("重复组不存在或不属于该批次。", batch_id=batch_id,
                                  kind=kind, item_id=item_id) from exc
        except ValueError as exc:  # canonical 解析失败/环：可重试的数据状态，不 500
            raise _item_unavailable("重复组当前无法解析，请刷新导入复核后重试。",
                                    batch_id=batch_id, kind=kind,
                                    item_id=item_id) from exc
        if candidate.get("status") not in ("pending", "undecided"):
            raise _item_not_found("重复组已处理，无需 AI 建议。", batch_id=batch_id,
                                  kind=kind, item_id=item_id)
        members: list[dict] = []
        for member in candidate.get("members") or []:
            article = (member.get("article") if member.get("ref_type") == "active"
                       else (member.get("record") or {}).get("article"))
            if isinstance(article, dict):
                members.append(_bibliographic(article))
        if len(members) < 2:
            raise _item_unavailable("重复组可用题录不足两条。", batch_id=batch_id,
                                    kind=kind, item_id=item_id,
                                    reason="insufficient_members")
        return {"kind": "duplicate_group", "members": members}

    # metadata_diff：记录必须属于该批次；两侧取既有提案的 before/incoming 快照。
    membership = conn.execute(
        "SELECT 1 FROM workflow_import_members WHERE batch_id=? AND record_id=?",
        (batch_id, item_id)).fetchone()
    if membership is None:
        raise _item_not_found("元数据差异条目不存在或不属于该批次。", batch_id=batch_id,
                              kind=kind, item_id=item_id)
    row = conn.execute(
        "SELECT before_json,incoming_json FROM workflow_import_metadata "
        "WHERE record_id=? AND action IS NULL",
        (item_id,)).fetchone()
    if row is None:
        raise _item_not_found("元数据差异条目不存在（可能已处理）。", batch_id=batch_id,
                              kind=kind, item_id=item_id)
    before, incoming = _parse_json_object(row["before_json"]), _parse_json_object(row["incoming_json"])
    if before is None or incoming is None:
        raise _item_unavailable("元数据差异快照损坏，请刷新导入复核后重试。",
                                batch_id=batch_id, kind=kind, item_id=item_id)
    return {"kind": "metadata_diff", "left": _bibliographic(before),
            "right": _bibliographic(incoming)}


# ---------------------------------------------------------------------------
# merge_conflict：冲突字段 + 确定性预判层（零出站）
#
# 口径与第一段同一份事实源：``store.METADATA_FIELDS``（冲突键域）、
# ``store.is_blank``（「空」的定义）、``store.completeness``（多被合并者取值
# 优先级）、``store._candidate_merged_members``（谁是被合并成员及其导入顺序）。
# ---------------------------------------------------------------------------

_ET_AL_RE = re.compile(r"\bet\s*al\b", re.IGNORECASE)
_ELLIPSIS_TAIL_RE = re.compile(r"(?:\.{2,}|\u2026)\s*$")
_SENTENCE_END_RE = re.compile(r"[.!?\u3002\uff01\uff1f\u2026][\"'\u201d\u2019)\]]?\s*$")

#: 规则层/降级层的理由文案（中文展示；机器键见 rationale_key）。
_RULE_RATIONALES = {
    "doi_normalized": "两个 DOI 规范化后相同，取书写更规范的一侧。",
    "title_case": "两侧题名仅大小写不同，取非全大写的一侧。",
    "authors_et_al": "第一作者一致且一侧为 et al. 截断，取作者全列的一侧。",
    "abstract_truncated": "一侧摘要为另一侧前缀且被截断，取完整的一侧。",
    "member_completeness": "保留条目该字段为空、多个被合并记录取值不同，"
                           "按完整度与导入顺序取第一份。",
}
_AI_RATIONALES = {
    "left": "模型建议采用保留条目值。",
    "right": "模型建议采用另一条记录的值。",
}
_RATIONALE_UNSURE = "无法判断，按默认使用保留条目值。"
_RATIONALE_NOT_SENT = "该字段不出站（不在六字段白名单内），按默认使用保留条目值。"


def _field_text(field: str, value: Any) -> str:
    """字段展示/出站文本（字符串化 + 按字段截断；空值 → ""）。"""
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()[:_FIELD_LIMITS.get(field, 500)]


def _comparable(value: Any) -> str:
    """比较形式：NFKC + 压缩空白 + casefold（空/占位串由调用方先排除）。"""
    return " ".join(unicodedata.normalize("NFKC", str(value)).split()).casefold()


def _identical(value: Any) -> str:
    """冲突判定的「值相同」：NFKC + 压缩空白，**保留大小写**。

    仅大小写不同不是同一个值（题名/期刊/DOI 的「全大写」变体必须能被看见，
    才轮到规则层或 AI 判「取非全大写/规范写法」）。
    """
    return " ".join(unicodedata.normalize("NFKC", str(value)).split())


def _is_all_caps(text: str) -> bool:
    letters = [ch for ch in str(text or "") if ch.isalpha()]
    return bool(letters) and all(ch.isupper() for ch in letters)


def _has_et_al(text: Any) -> bool:
    return bool(_ET_AL_RE.search(str(text or "")))


def _truncation_core(text: str) -> tuple[str, bool]:
    """摘要比较形式：返回（去掉截断标记后的文本, 是否呈现截断）。

    截断 = 以省略号（"..." / "…"）结尾，或没有收尾标点（半句截断）。
    """
    normalized = " ".join(unicodedata.normalize("NFKC", str(text or "")).split())
    truncated = bool(_ELLIPSIS_TAIL_RE.search(normalized))
    core = _ELLIPSIS_TAIL_RE.sub("", normalized).strip()
    if not truncated and not _SENTENCE_END_RE.search(normalized):
        truncated = True
    return core.casefold(), truncated


def _abstract_pick(left: str, right: str) -> str | None:
    left_core, left_truncated = _truncation_core(left)
    right_core, right_truncated = _truncation_core(right)
    if not left_core or not right_core or left_core == right_core:
        return None
    if left_truncated and right_core.startswith(left_core):
        return "right"
    if right_truncated and left_core.startswith(right_core):
        return "left"
    return None


def rule_pick(field: str, left: Any, right: Any) -> dict | None:
    """确定性预判：返回 ``{'pick','rationale_key','rationale'}`` 或 None（判不出）。

    四条规则（设计 §三 + 本波指令 2）：DOI 规范化后相等 → 取规范书写的一侧；
    题名忽略大小写相等 → 取非全大写；作者一侧 et al. 且第一作者一致 → 取全列；
    摘要一侧是另一侧前缀且被截断 → 取完整者。零出站、纯函数。
    """
    if store.is_blank(left) or store.is_blank(right):
        return None
    pick, key = None, None
    if field == "doi":
        normalized_left, normalized_right = _norm_doi(str(left)), _norm_doi(str(right))
        if normalized_left and normalized_left == normalized_right:
            raw_left, raw_right = str(left).strip(), str(right).strip()
            left_canonical = raw_left == normalized_left
            right_canonical = raw_right == normalized_right
            if left_canonical != right_canonical:
                pick = "left" if left_canonical else "right"
            else:  # 都不是（或都是）规范写法：取更短的一侧（装饰更少），平分取保留条目。
                pick = "left" if len(raw_left) <= len(raw_right) else "right"
            key = "doi_normalized"
    elif field == "title":
        if (_comparable(left) == _comparable(right)
                and _is_all_caps(str(left)) != _is_all_caps(str(right))):
            pick = "right" if _is_all_caps(str(left)) else "left"
            key = "title_case"
    elif field == "authors":
        if _has_et_al(left) != _has_et_al(right):
            first_left = first_author_surname(str(left))
            first_right = first_author_surname(str(right))
            if first_left and first_left == first_right:
                pick = "right" if _has_et_al(left) else "left"
                key = "authors_et_al"
    elif field == "abstract":
        chosen = _abstract_pick(str(left), str(right))
        if chosen:
            pick, key = chosen, "abstract_truncated"
    if not pick or not key:
        return None
    return {"pick": pick, "rationale_key": "import_merge.ai.rule." + key,
            "rationale": _RULE_RATIONALES[key]}


def merge_conflict_fields(keeper_meta: dict, merged: list[dict]) -> list[dict]:
    """算冲突字段（纯函数，不读写库）。

    ``merged`` = ``[{'record_id','article'}]``（已按导入顺序）。冲突判定：

    - 两边都有值但不同（``conflict_kind='two_sided'``）；或
    - 保留条目该字段为空、多个被合并成员给出不同值
      （``conflict_kind='member_conflict'``，设计 §七.3）。

    保留条目为空且只有一个候选值的字段不算冲突（第一段「只补空」会自动补入）。
    期刊样式 DOI 与第一段 DOI 守卫一致：不作为可写入候选。
    """
    keeper_meta = keeper_meta if isinstance(keeper_meta, dict) else {}
    merged = [m for m in (merged or []) if isinstance(m, dict) and m.get("record_id")]
    scores = {m["record_id"]: store.completeness(
        m.get("article") if isinstance(m.get("article"), dict) else {})
        for m in merged}
    fields: list[dict] = []
    for field in store.METADATA_FIELDS:
        keeper_value = keeper_meta.get(field)
        entries = []
        for member in merged:
            article = member.get("article") if isinstance(member.get("article"), dict) else {}
            value = article.get(field)
            if store.is_blank(value):
                continue
            if field == "doi" and is_journal_pattern_doi(str(value)):
                continue  # 期刊样式 DOI 不是单篇身份，第一段也不会写入
            entries.append({"record_id": member["record_id"], "value": value})
        if not entries:
            continue
        if store.is_blank(keeper_value):
            if len({_identical(item["value"]) for item in entries}) < 2:
                continue  # 单一取值 → 「只补空」自动补入，不算冲突
            differing, conflict_kind = entries, "member_conflict"
        else:
            differing = [item for item in entries
                         if _identical(item["value"]) != _identical(keeper_value)]
            if not differing:
                continue
            conflict_kind = "two_sided"
        # 多被合并者：完整度降序、原顺序（稳定排序）取第一份。
        chosen = sorted(differing, key=lambda item: scores[item["record_id"]],
                        reverse=True)[0]
        if conflict_kind == "member_conflict":
            rule = {"pick": "right",
                    "rationale_key": "import_merge.ai.rule.member_completeness",
                    "rationale": _RULE_RATIONALES["member_completeness"]}
        else:
            rule = rule_pick(field, keeper_value, chosen["value"])
        fields.append({
            "field": field,
            "left": _field_text(field, keeper_value),
            "right": _field_text(field, chosen["value"]),
            "right_record_id": chosen["record_id"],
            "candidates": [{"record_id": item["record_id"],
                            "value": _field_text(field, item["value"])}
                           for item in entries],
            "conflict_kind": conflict_kind,
            "rule_pick": rule["pick"] if rule else None,
            "rule_rationale_key": rule["rationale_key"] if rule else None,
            "ai_pick": None,
            "prefill": None,
            "source": None,
            "fallback": None,
            "rationale_key": rule["rationale_key"] if rule else None,
            "rationale": rule["rationale"] if rule else "",
        })
    return fields


def _merge_ai_fields(fields: list[dict]) -> list[dict]:
    """需要出站的冲突字段：规则层判不出 + 属于六字段白名单 + 两边都有值。"""
    return [row for row in fields
            if not row["rule_pick"] and row["field"] in MERGE_FIELD_KEYS
            and row["conflict_kind"] == "two_sided"]


def merge_outbound_message(fields: list[dict]) -> str:
    """出站用户消息：只含待判冲突字段的 left/right（逐项仅三个键）。"""
    return json.dumps({
        "kind": "merge_conflict",
        "conflicts": [{"field": row["field"], "left": row["left"], "right": row["right"]}
                      for row in fields],
    }, ensure_ascii=False, sort_keys=True)


def _resolve_keeper(conn: sqlite3.Connection, members: list[dict],
                   keeper_key: str | None, keeper_record_id: str | None) -> dict:
    """确定保留者（请求给定或 B2 的 suggested_keeper），并读出其题录。"""
    active = [m for m in members if m.get("ref_type") == "active"]
    records = [m for m in members if m.get("ref_type") == "record"]
    if keeper_key and keeper_record_id:
        raise _keeper_invalid("保留者只能给 keeper_key 或 keeper_record_id 之一。", "both")
    if len(active) > 1:
        raise _item_unavailable("组内已入任务文献超过一条，不允许合并独立已保存工作。",
                                reason="active_conflict")
    source = "request"
    if keeper_key:
        try:
            canonical_key = store.canonical(conn, keeper_key)
            member_keys = {store.canonical(conn, m["ref_id"]) for m in active}
        except ValueError as exc:
            raise _keeper_invalid("保留者不是本组成员。", "not_member") from exc
        if canonical_key not in member_keys:
            raise _keeper_invalid("保留者不是本组的已入任务成员。", "not_member")
        row = conn.execute("SELECT * FROM articles WHERE zotero_key=?",
                           (canonical_key,)).fetchone()
        if row is None:
            raise _item_unavailable("保留条目已不存在，请刷新导入复核后重试。",
                                    reason="keeper_missing")
        meta = dict(row)
        keeper = {"keeper_key": canonical_key, "keeper_record_id": None,
                  "reasons": ["active_member"], "kind": "active", "ref": canonical_key}
    elif keeper_record_id:
        if keeper_record_id not in {m["ref_id"] for m in records}:
            raise _keeper_invalid("保留者不是本组的待审记录。", "not_member")
        if active:
            raise _keeper_invalid("组内已有入任务文献，保留者必须是它。",
                                  "active_member_required")
        row = conn.execute("SELECT article_json FROM workflow_source_records WHERE id=?",
                           (keeper_record_id,)).fetchone()
        if row is None:
            raise _item_unavailable("保留条目已不存在，请刷新导入复核后重试。",
                                    reason="keeper_missing")
        meta = _parse_json_object(row[0]) or {}
        keeper = {"keeper_key": None, "keeper_record_id": keeper_record_id,
                  "reasons": store.completeness_reasons(meta),
                  "kind": "record", "ref": keeper_record_id}
    else:
        # 延迟导入：与候选载荷同一份建议保留者实现（单一事实源）。
        from custom_backend.import_review_presentation import suggested_keeper
        suggestion = suggested_keeper(members)
        source = "suggested"
        if suggestion.get("keeper_key"):
            keeper = {"keeper_key": suggestion["keeper_key"], "keeper_record_id": None,
                      "reasons": suggestion.get("reasons") or [], "kind": "active",
                      "ref": suggestion["keeper_key"]}
        elif suggestion.get("keeper_record_id"):
            keeper = {"keeper_record_id": suggestion["keeper_record_id"],
                      "keeper_key": None, "reasons": suggestion.get("reasons") or [],
                      "kind": "record", "ref": suggestion["keeper_record_id"]}
        else:
            raise _item_unavailable("重复组没有可用题录，无法给出合并建议。",
                                    reason="insufficient_members")
        if keeper["kind"] == "active":
            try:  # 建议保留者给的是成员 ref_id；按 canonical 口径取现有条目。
                canonical_key = store.canonical(conn, keeper["keeper_key"])
            except ValueError:
                canonical_key = keeper["keeper_key"]
            keeper["keeper_key"] = canonical_key
            keeper["ref"] = canonical_key
            row = conn.execute("SELECT * FROM articles WHERE zotero_key=?",
                               (canonical_key,)).fetchone()
            meta = dict(row) if row is not None else None
        else:
            row = conn.execute("SELECT article_json FROM workflow_source_records WHERE id=?",
                               (keeper["keeper_record_id"],)).fetchone()
            meta = _parse_json_object(row[0]) if row is not None else None
        if meta is None:
            raise _item_unavailable("保留条目已不存在，请刷新导入复核后重试。",
                                    reason="keeper_missing")
    keeper["source"] = source
    keeper["meta"] = meta
    return keeper


def load_merge_conflict(conn: sqlite3.Connection, batch_id: str, item_id: str,
                        keeper_key: str | None = None,
                        keeper_record_id: str | None = None) -> dict:
    """merge_conflict 的分析载荷：保留者题录 + 被合并成员 + 冲突字段（只读）。"""
    try:
        candidate = store.candidate_row(conn, batch_id, item_id)
    except KeyError as exc:
        raise _item_not_found("重复组不存在或不属于该批次。", batch_id=batch_id,
                              kind="merge_conflict", item_id=item_id) from exc
    except ValueError as exc:
        raise _item_unavailable("重复组当前无法解析，请刷新导入复核后重试。",
                                batch_id=batch_id, kind="merge_conflict",
                                item_id=item_id) from exc
    if candidate.get("status") not in ("pending", "undecided"):
        raise _item_not_found("重复组已处理，无需 AI 建议。", batch_id=batch_id,
                              kind="merge_conflict", item_id=item_id)
    members = candidate.get("members") or []
    if len(members) < 2:
        raise _item_unavailable("重复组可用题录不足两条。", batch_id=batch_id,
                                kind="merge_conflict", item_id=item_id,
                                reason="insufficient_members")
    keeper = _resolve_keeper(conn, members, keeper_key, keeper_record_id)
    # 被合并成员与导入顺序取第一段的同一实现（不在这里另写一份口径）。
    merged = store._candidate_merged_members(  # noqa: SLF001  同一个「谁被合并」
        conn, members, keeper.get("keeper_record_id"))
    if not merged:
        raise _item_unavailable("重复组没有可合并的待审记录。", batch_id=batch_id,
                                kind="merge_conflict", item_id=item_id,
                                reason="no_merged_members")
    fields = merge_conflict_fields(keeper["meta"], merged)
    return {"kind": "merge_conflict", "keeper": keeper, "fields": fields}


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


def parse_merge_reply(content: Any, asked_fields: list[str]) -> dict:
    """解析 merge_conflict 的模型回复；返回
    ``{picks, confidence, rationale_zh, warnings, degraded}``。

    硬约束（本波指令 4）：值不在 {left,right,unsure} 的字段丢弃按 unsure
    （``field_picks_invalid``）；缺答字段按 unsure；未给置信度或置信度低于
    ``MIN_CONFIDENCE`` → 本次 AI 层全部 unsure（``confidence_missing`` /
    ``low_confidence``）。绝不硬给建议。
    """
    text = str(content or "").strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    parsed = (_parse_json_object(text[start:end + 1])
              if 0 <= start < end else None)
    if parsed is None:
        return {"picks": {}, "confidence": None,
                "rationale_zh": "模型输出无法解析，无法判断。",
                "warnings": ["provider_response_not_json"], "degraded": True}

    warnings: list[str] = []
    degraded = False
    rationale = _clip(parsed.get("rationale_zh"), MAX_RATIONALE_CHARS)
    confidence = _confidence(parsed.get("confidence"))
    picks: dict[str, str] = {}
    raw_picks = parsed.get("field_picks")
    if isinstance(raw_picks, dict):
        for key, value in raw_picks.items():
            token = str(value or "").strip().lower()
            if key not in asked_fields or token not in ("left", "right", "unsure"):
                warnings.append("field_picks_invalid")
                degraded = True
                continue
            picks[key] = token
    else:
        warnings.append("field_picks_invalid")
        degraded = True
    missing = [field for field in asked_fields if field not in picks]
    if missing:
        warnings.append("field_picks_incomplete")
        degraded = True
    if confidence is None:
        warnings.append("confidence_missing")
        degraded = True
        picks = {field: "unsure" for field in asked_fields}
        rationale = ("模型未给出置信度，无法判断。"
                     + (f"（模型理由：{rationale}）" if rationale else ""))
    elif confidence < MIN_CONFIDENCE:
        warnings.append("low_confidence")
        degraded = True
        picks = {field: "unsure" for field in asked_fields}
        note = f"置信度 {confidence:.2f} 低于 {MIN_CONFIDENCE:.2f}，无法判断。"
        rationale = note + (f"（模型理由：{rationale}）" if rationale else "")
    if not rationale:
        rationale = "证据不足，无法判断。"
    clean: list[str] = []
    for warning in warnings:
        token = _SAFE_WARNING_RE.sub("", str(warning).lower())[:40]
        if token and token not in clean:
            clean.append(token)
    return {"picks": picks, "confidence": confidence, "rationale_zh": rationale,
            "warnings": clean, "degraded": degraded}


def _merge_unsure_parsed(rationale: str, warning: str) -> dict:
    """出站不可用/连续失败时的整体降级（AI 层全部 unsure）。"""
    return {"picks": {}, "confidence": None, "rationale_zh": rationale,
            "warnings": [warning], "degraded": True}


def apply_merge_decisions(fields: list[dict], parsed: dict | None) -> tuple[list[str], bool]:
    """把规则层/模型层结论写进逐字段行（返回（warnings, degraded））。

    - 规则层判定：``source='rule'``，永不改判、也永不发 AI；
    - 六字段白名单外（url、item_type 等）：``fallback='default'``（=保留条目值）；
    - 模型 left/right：``source='ai'`` + ``prefill``；unsure/缺答：回落保留值。
    """
    warnings = list((parsed or {}).get("warnings") or [])
    degraded = bool((parsed or {}).get("degraded"))
    picks = (parsed or {}).get("picks") or {}
    # 低置信/缺置信/出站失败时模型结论整体不可用：``ai_pick`` 一律不记
    # （记下被丢弃的建议会误导审计，读起来像"AI 推荐了 X 但人没采纳"）。
    confidence = (parsed or {}).get("confidence")
    usable = confidence is not None and confidence >= MIN_CONFIDENCE
    for row in fields:
        if row["rule_pick"]:
            row["prefill"] = row["rule_pick"]
            row["source"] = "rule"
            row["fallback"] = None
            row["rationale_key"] = row["rule_rationale_key"]
            row["rationale"] = _RULE_RATIONALES[
                row["rule_rationale_key"].rsplit(".", 1)[-1]]
            continue
        if row["field"] not in MERGE_FIELD_KEYS:
            row["fallback"] = "default"
            row["rationale_key"] = "import_merge.ai.field_not_sent"
            row["rationale"] = _RATIONALE_NOT_SENT
            continue
        pick = picks.get(row["field"])
        row["ai_pick"] = (pick if usable and pick in ("left", "right", "unsure")
                          else None)
        if pick in ("left", "right"):
            row["prefill"] = pick
            row["source"] = "ai"
            row["fallback"] = None
            row["rationale_key"] = "import_merge.ai.ai_pick." + pick
            row["rationale"] = _AI_RATIONALES[pick]
        else:
            row["fallback"] = "default"
            row["rationale_key"] = "import_merge.ai.unsure"
            row["rationale"] = _RATIONALE_UNSURE
    return warnings, degraded


def merge_suggestion(plan: dict, parsed: dict | None) -> dict:
    """组装 merge_conflict 的建议（只读载荷；不含任何出站内容）。"""
    fields = plan["fields"]
    keeper = plan["keeper"]
    warnings, degraded = apply_merge_decisions(fields, parsed)
    picks = {row["field"]: (row["prefill"] or "unsure") for row in fields}
    suggested = {row["field"]: ("keeper" if row["prefill"] == "left"
                                else row["right_record_id"])
                 for row in fields if row["prefill"]}
    if parsed:
        confidence = parsed.get("confidence")
        rationale = parsed.get("rationale_zh") or ""
    else:
        confidence, rationale = None, ""
    if not rationale:
        if not fields:
            rationale = "两侧题录没有冲突字段。"
        elif not _merge_ai_fields(fields):
            rationale = "规则层已判定全部冲突字段。"
        else:
            rationale = "证据不足，无法判断。"
    return {
        "verdict": None,            # 该 kind 不做「是不是重复」判断（不预选）
        "confidence": confidence,
        "rationale_zh": rationale,
        "field_picks": picks or None,
        "fields": fields,
        "keeper": {key: keeper[key] for key in
                   ("keeper_key", "keeper_record_id", "reasons", "source")},
        "suggested_conflict_picks": suggested,
        "degraded": degraded,
        "warnings": warnings,
    }


def _request_digest(system: str, user: str, model: str,
                    prompt_version: str = PROMPT_VERSION) -> str:
    canonical = json.dumps({"prompt_version": prompt_version, "model": model,
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
    kind: Literal["duplicate_group", "metadata_diff", "merge_conflict"]
    item_id: str = Field(min_length=1, max_length=128)
    #: merge_conflict 专用：保留者（可选；缺省用 B2 的 suggested_keeper）。
    #: 只允许在 merge_conflict 出现 keeper 字段（其余 kind → 400 item_invalid）。
    keeper_key: str | None = Field(default=None, max_length=128)
    keeper_record_id: str | None = Field(default=None, max_length=128)

    @property
    def has_keeper(self) -> bool:
        return bool(self.keeper_key or self.keeper_record_id)


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
        try:
            ctx = _workflow().context(task_id, user)  # 非任务成员 → 403
        except HTTPException:
            raise
        except (sqlite3.Error, OSError) as exc:
            # 终审 #32（场景 A）：工作流上下文要开协作库 + 属主/个人库并可能跑
            # 迁移，锁忙/损坏此前未捕获 → 三端点全局 500。结构化 503（可重试）。
            raise _storage_unavailable() from exc
        if ctx.owner is None:
            # 导入复核数据按属主库权威（与 review_imports_api.read_context 同口径）。
            raise _error(403, "import_ai_assist_owner_required",
                         "import_ai_assist.owner_required", {},
                         "导入复核需要已验证的任务属主。")
        return ctx

    def _credentials(info, user: dict):
        """复用 ai_suggest 的钥匙口径（个人 > 共享 > 无）。

        返回 ``(credentials, key_source, model, model_kind)``；decision 钥匙的
        model 兜底是 Jev 缺省模型名（gpt 兜底对 systemone 端点无意义）。
        """
        ai_settings = _load_settings(info)
        prefs = _load_prefs(info, user["username"])
        credentials, key_source = resolve_task_credentials(
            info, ai_settings, prefs, user, data_root,
            kinds=(ai_keys.KIND_DECISION, ai_keys.KIND_LLM))
        model_kind = ai_keys.kind_of(credentials)
        fallback = (decision_model.DEFAULT_DECISION_MODEL
                    if model_kind == ai_keys.KIND_DECISION else _FALLBACK_MODEL)
        model = ((credentials or {}).get("model") or "").strip() or fallback
        return credentials, key_source, model, model_kind

    def _settings_view(info, user: dict, ctx, settings: dict) -> dict:
        credentials, key_source, model, model_kind = _credentials(info, user)
        return {
            "enabled": bool(settings.get("enabled")),
            "version": int(settings.get("version") or 0),
            "can_manage": bool(ctx.can_manage),
            "has_key": credentials is not None,
            "key_source": key_source,
            "key_kind": model_kind,
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "prompt_versions": {"duplicate_group": PROMPT_VERSION,
                                "metadata_diff": PROMPT_VERSION,
                                "merge_conflict": MERGE_PROMPT_VERSION},
            "decision_prompt_versions": {
                "duplicate_group": decision_model.DECISION_PROMPT_VERSION,
                "merge_conflict": decision_model.DECISION_MERGE_PROMPT_VERSION},
            "kinds": ["duplicate_group", "metadata_diff", "merge_conflict"],
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
            raise _error(403, "import_ai_assist_settings_owner_only",
                         "import_ai_assist.settings_owner_only", {},
                         "只有任务负责人可以修改导入复核 AI 助手开关。")
        with tasks_mod._task_json_lock(info.dir):  # noqa: SLF001  读改写走同一条任务锁
            settings = load_settings(info)
            if body.expected_version != settings["version"]:
                raise _error(409, "import_ai_assist_settings_version_conflict",
                             "import_ai_assist.settings_version_conflict",
                             {"version": settings["version"]},
                             "开关版本已变化，请刷新后重试。",
                             version=settings["version"])
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
            raise _error(
                404, "import_ai_assist_disabled", "import_ai_assist.disabled", {},
                "任务未启用导入复核 AI 助手。",
                guidance="导入复核 AI 助手默认关闭；请由任务负责人开启后重试。")

        # 2) 条目上限（批量预算）——先于条目解析与出站。
        if len(body.items) > MAX_ITEMS_PER_REQUEST:
            raise _error(
                400, "import_ai_assist_too_many_items",
                "import_ai_assist.too_many_items",
                {"max_items": MAX_ITEMS_PER_REQUEST},
                f"单次最多 {MAX_ITEMS_PER_REQUEST} 条待处理项。",
                max_items=MAX_ITEMS_PER_REQUEST)
        seen: set[tuple[str, str, str]] = set()
        for ref in body.items:
            key = (ref.batch_id, ref.kind, ref.item_id)
            if key in seen:
                raise _error(
                    400, "import_ai_assist_duplicate_item",
                    "import_ai_assist.duplicate_item",
                    {"batch_id": ref.batch_id, "kind": ref.kind, "item_id": ref.item_id},
                    "同一条待处理项不能在一次请求里重复出现。")
            seen.add(key)
            if ref.has_keeper and ref.kind != "merge_conflict":
                raise _error(
                    400, "import_ai_assist_item_invalid", "import_ai_assist.item_invalid",
                    {"kind": ref.kind, "item_id": ref.item_id},
                    "只能为 merge_conflict 指定保留者。")

        # 3) 钥匙（无钥匙 → 404 结构化 guidance；零出站）。
        credentials, key_source, model, model_kind = _credentials(info, user)
        if credentials is None:
            raise _error(
                404, "import_ai_assist_key_missing", "import_ai_assist.key_missing", {},
                "尚未配置可用的 AI 钥匙。",
                guidance="请先配置个人 AI 钥匙，或由任务负责人配置任务共享钥匙后再试。")
        try:  # 存量钥匙记录的非法 base_url 映射 422，不冒泡 500。
            if model_kind == ai_keys.KIND_DECISION:
                base_url = decision_model.decision_base_url_of(credentials)
            else:
                base_url = ai_keys.base_url_of(credentials)
        except ai_keys.KeyShapeError as exc:
            raise _error(422, "import_ai_assist_key_invalid",
                         "import_ai_assist.key_invalid", {},
                         str(exc)) from None

        # 4) 审计表与既有导入复核表就位（幂等；先于出站，出站前失败不花预算）。
        try:
            store.migrate(ctx.owner_db)
            with closing(workflow.connect(ctx.owner_db)) as conn:
                _ensure_audit_schema(conn)
        except (sqlite3.Error, OSError) as exc:
            # 终审 #32（场景 B）：migrate/审计建表在 try 外——锁属主库时未捕获
            # → 500。同样 503（此刻仍零出站，额度未消耗）。
            raise _storage_unavailable() from exc

        # 5) 一次性解析全部条目（只读）：任何无效引用 → 404，绝不部分出站。
        try:
            with closing(workflow.connect(ctx.owner_db, read_only=True)) as conn:
                payloads = [
                    load_merge_conflict(conn, ref.batch_id, ref.item_id,
                                        ref.keeper_key, ref.keeper_record_id)
                    if ref.kind == "merge_conflict"
                    else load_item_payload(conn, ref.batch_id, ref.kind, ref.item_id)
                    for ref in body.items]
        except sqlite3.Error as exc:
            # 终审 #32：解析阶段的锁忙/库故障此前误映射 404（说成"条目不
            # 存在"）；存储类故障一律 503 可重试，条目真正缺失仍走各 loader
            # 自己的结构化 404。
            raise _storage_unavailable() from exc

        def _touch_key(usage: dict | None = None) -> None:
            # aiux D/H（2026-11-12）：成功出站记账（槽文件优先/旧扁平回落；
            # usage 有则累计 tokens）。
            path = ai_keys.outcome_path_for(
                key_source or "", kind=ai_keys.kind_of(credentials),
                user_id=user["user_id"], task_id=info.task_id, data_root=data_root)
            if path is not None:
                ai_keys.record_outcome(path, ok=True, usage=usage)

        def _fail_key() -> None:
            # aiux D：出站失败连败+1（钥匙健康横幅数据源；env 钥匙不落盘不记）。
            path = ai_keys.outcome_path_for(
                key_source or "", kind=ai_keys.kind_of(credentials),
                user_id=user["user_id"], task_id=info.task_id, data_root=data_root)
            if path is not None:
                ai_keys.record_outcome(path, ok=False)

        def _call(system: str, message: str) -> dict:
            return _call_provider(system, message, api_key=credentials["key"],
                                  base_url=base_url, model=model,
                                  timeout=OUTBOUND_TIMEOUT_SECONDS,
                                  format=ai_keys.format_of(credentials))

        def _call_decision(body_out: dict) -> dict:
            return decision_model.call_decision(
                body_out, api_key=credentials["key"], base_url=base_url,
                model=model, timeout=OUTBOUND_TIMEOUT_SECONDS)

        # 6) 逐条出站（短超时；失败逐条降级 unsure，连续失败即停止出站）。
        #    按钥匙的 kind 分派：llm → chat/completions + prompt 解析；
        #    decision → systemone + 结构化答案解析（统一输出形状）。metadata_diff
        #    不在决策模型范围（2026-11-10 用户裁定）→ decision 钥匙零出站降级。
        results: list[dict] = []
        consecutive_failures = 0
        for ref, payload in zip(body.items, payloads):
            if ref.kind == "merge_conflict":
                # 6a) merge_conflict：先跑确定性预判层（零出站），只有判不出的
                #     六字段白名单冲突才出站；全部被规则层判出 → 一次都不调用。
                needs = _merge_ai_fields(payload["fields"])
                user_message = merge_outbound_message(needs) if needs else ""
                fallback_state = json.dumps(
                    {"kind": "merge_conflict",
                     "fields": [{"field": row["field"], "left": row["left"],
                                 "right": row["right"]}
                                for row in payload["fields"]]},
                    ensure_ascii=False, sort_keys=True)
                if model_kind == ai_keys.KIND_DECISION:
                    merge_prompt_version = decision_model.DECISION_MERGE_PROMPT_VERSION
                    decision_body = decision_model.merge_request(needs, model=model) if needs else None
                    digest = _request_digest(
                        json.dumps(decision_body["questions"], ensure_ascii=False, sort_keys=True)
                        if decision_body else {},
                        json.dumps(decision_body["state"], ensure_ascii=False, sort_keys=True)
                        if decision_body else fallback_state,
                        model, prompt_version=merge_prompt_version)
                else:
                    merge_prompt_version = MERGE_PROMPT_VERSION
                    digest = _request_digest(
                        _MERGE_SYSTEM_PROMPT,
                        user_message or fallback_state,
                        model, prompt_version=MERGE_PROMPT_VERSION)
                parsed = None
                if needs:
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        parsed = _merge_unsure_parsed(
                            "模型连续调用失败，已跳过该条以避免持续消耗额度。",
                            "provider_unavailable")
                    elif model_kind == ai_keys.KIND_DECISION:
                        try:
                            answer = _call_decision(decision_body)
                            parsed = decision_model.parse_merge_answers(
                                answer, [row["field"] for row in needs],
                                min_confidence=MIN_CONFIDENCE)
                            consecutive_failures = 0
                            _touch_key()
                        except ProviderError:
                            parsed = _merge_unsure_parsed(
                                "模型调用失败，无法判断。", "provider_error")
                            consecutive_failures += 1
                            _fail_key()
                    else:
                        try:
                            reply = _call(_MERGE_SYSTEM_PROMPT, user_message)
                            parsed = parse_merge_reply(
                                reply.get("content"),
                                [row["field"] for row in needs])
                            consecutive_failures = 0
                            _touch_key()
                        except ProviderError:
                            parsed = _merge_unsure_parsed(
                                "模型调用失败，无法判断。", "provider_error")
                            consecutive_failures += 1
                            _fail_key()
                suggestion = merge_suggestion(payload, parsed)
                suggestion.update({
                    "batch_id": ref.batch_id,
                    "kind": ref.kind,
                    "item_id": ref.item_id,
                    "model": model,
                    "model_kind": model_kind,
                    "request_digest": digest,
                    "prompt_version": merge_prompt_version,
                    "audit_payload": {"keeper": suggestion["keeper"],
                                      "fields": suggestion["fields"]},
                })
                results.append(suggestion)
                continue

            user_message = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            if model_kind == ai_keys.KIND_DECISION and ref.kind == "metadata_diff":
                # 范围铁律（2026-11-10 用户裁定）：决策模型只用于 duplicate_group
                # 与 merge_conflict；metadata_diff 不出站、逐条降级 unsure。
                # 审计行 prompt_version 记 n/a：该行从未出站，不属于任何提示词
                # 版本域（digest 以显式标记串计算，仍保证幂等唯一）。
                suggestion = _unsure(
                    "决策模型钥匙不支持元数据差异条目，无法判断；请改用生成式 LLM 钥匙。",
                    ["decision_kind_not_supported"])
                digest = _request_digest(
                    "not-sent:decision-kind-not-supported", user_message, model,
                    prompt_version=NOT_SENT_PROMPT_VERSION)
                suggestion.update({
                    "batch_id": ref.batch_id,
                    "kind": ref.kind,
                    "item_id": ref.item_id,
                    "model": model,
                    "model_kind": model_kind,
                    "request_digest": digest,
                    "prompt_version": NOT_SENT_PROMPT_VERSION,
                })
                results.append(suggestion)
                continue
            if model_kind == ai_keys.KIND_DECISION:
                decision_body = decision_model.duplicate_request(payload, model=model)
                digest = _request_digest(
                    json.dumps(decision_body["questions"], ensure_ascii=False, sort_keys=True),
                    json.dumps(decision_body["state"], ensure_ascii=False, sort_keys=True),
                    model, prompt_version=decision_model.DECISION_PROMPT_VERSION)
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    suggestion = _unsure("模型连续调用失败，已跳过该条以避免持续消耗额度。",
                                         ["provider_unavailable"])
                else:
                    try:
                        answer = _call_decision(decision_body)
                        suggestion = decision_model.parse_duplicate_answer(
                            answer, min_confidence=MIN_CONFIDENCE)
                        consecutive_failures = 0
                        _touch_key()
                    except ProviderError:
                        suggestion = _unsure("模型调用失败，无法判断。", ["provider_error"])
                        consecutive_failures += 1
                        _fail_key()
                suggestion.update({
                    "batch_id": ref.batch_id,
                    "kind": ref.kind,
                    "item_id": ref.item_id,
                    "model": model,
                    "model_kind": model_kind,
                    "request_digest": digest,
                    "prompt_version": decision_model.DECISION_PROMPT_VERSION,
                })
                results.append(suggestion)
                continue
            digest = _request_digest(_SYSTEM_PROMPT, user_message, model)
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                suggestion = _unsure("模型连续调用失败，已跳过该条以避免持续消耗额度。",
                                     ["provider_unavailable"])
            else:
                try:
                    reply = _call(_SYSTEM_PROMPT, user_message)
                    suggestion = parse_reply(reply.get("content"), ref.kind)
                    consecutive_failures = 0
                    _touch_key()
                except ProviderError:
                    suggestion = _unsure("模型调用失败，无法判断。", ["provider_error"])
                    consecutive_failures += 1
            suggestion.update({
                "batch_id": ref.batch_id,
                "kind": ref.kind,
                "item_id": ref.item_id,
                "model": model,
                "model_kind": model_kind,
                "request_digest": digest,
                "prompt_version": PROMPT_VERSION,
            })
            results.append(suggestion)

        # 7) 审计留痕（唯一索引幂等；这里是本端点对库的唯一写入）。
        now = _utc_now_iso()
        provider = credentials.get("provider") or ""
        try:
            with closing(workflow.connect(ctx.owner_db)) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                for entry in results:
                    status = "degraded" if entry["degraded"] else "ok"
                    stored = entry.get("audit_payload")
                    if stored is not None:
                        stored_json = json.dumps(stored, ensure_ascii=False)
                    elif entry["field_picks"] is not None:
                        stored_json = json.dumps(entry["field_picks"], ensure_ascii=False)
                    else:
                        stored_json = None
                    conn.execute(_AUDIT_UPSERT, (
                        info.task_id, entry["batch_id"], entry["kind"], entry["item_id"],
                        provider, model,
                        entry.get("model_kind") or ai_keys.KIND_LLM,
                        entry["prompt_version"], entry["request_digest"],
                        entry["verdict"] or MERGE_VERDICT,
                        entry["confidence"], entry["rationale_zh"], stored_json,
                        json.dumps(entry["warnings"], ensure_ascii=False), status,
                        now, now))
                    audit_row = conn.execute(_AUDIT_LOOKUP, (
                        info.task_id, entry["batch_id"], entry["kind"], entry["item_id"],
                        entry["request_digest"])).fetchone()
                    entry["audit_id"] = int(audit_row[0])
        except (sqlite3.Error, OSError) as exc:
            # 终审 #32（场景 C）：出站后的审计写未收敛——撞锁时 500 且这一次
            # 已消耗额度的建议整批丢失。映射 503（诚实告知"请稍后重试"，
            # 不假 200；额度损耗在契约披露）。
            raise _storage_unavailable() from exc

        counts = {"same": 0, "different": 0, "unsure": 0}
        for entry in results:
            if entry["verdict"] in counts:
                counts[entry["verdict"]] += 1
        # 顶层 prompt_version(s) 按钥匙 kind 返回（评审中危项修复）：decision
        # 钥匙下与行级/审计一致（metadata_diff 为 null=该 kind 不出站）；llm
        # 钥匙零变化。行级 prompt_version 始终是权威值。
        if model_kind == ai_keys.KIND_DECISION:
            top_prompt_version = decision_model.DECISION_PROMPT_VERSION
            top_prompt_versions = {
                "duplicate_group": decision_model.DECISION_PROMPT_VERSION,
                "metadata_diff": None,
                "merge_conflict": decision_model.DECISION_MERGE_PROMPT_VERSION}
        else:
            top_prompt_version = PROMPT_VERSION
            top_prompt_versions = {"duplicate_group": PROMPT_VERSION,
                                   "metadata_diff": PROMPT_VERSION,
                                   "merge_conflict": MERGE_PROMPT_VERSION}
        return {
            "status": "ok",
            "read_only": True,               # 建议只读：确认仍走导入复核既有动作端点
            "prompt_version": top_prompt_version,
            "prompt_versions": top_prompt_versions,
            "model": model,
            "model_kind": model_kind,
            "key_source": key_source,
            "suggestions": [_suggestion_view(entry) for entry in results],
            "counts": counts,
        }

    @app.get("/api/tasks/{task_id}/import-review/ai-assist/audit/{audit_id}")
    def assist_audit(task_id: str, audit_id: int,
                     user: dict = Depends(require_user)) -> dict:
        """审计导出：逐字段可查 rule_pick / ai_pick / human_final（参与者可读）。

        ``human_final`` 由第一段留痕表 ``workflow_import_field_merges`` 经
        ``ai_suggestion_id``（= 本审计行 id 的字符串）关联得出；该组尚未被
        人工确认时 ``executed=false`` 且 ``human_final=null``。
        """
        info = task_lookup(task_id)
        ctx = _context(task_id, user)
        record = None
        owner_db = Path(ctx.owner_db)
        if owner_db.is_file():
            try:
                with closing(workflow.connect(owner_db, read_only=True)) as conn:
                    row = conn.execute(
                        "SELECT * FROM import_ai_suggestions WHERE id=? AND task_id=?",
                        (audit_id, info.task_id)).fetchone()
                    record = dict(row) if row is not None else None
                    if record is not None:
                        record["_trail"] = []
                        record["_candidate_status"] = None
                        if record["item_kind"] == "merge_conflict" and workflow.has_table(
                                conn, "workflow_import_field_merges"):
                            record["_trail"] = [dict(trail) for trail in conn.execute(
                                "SELECT field,source_record_id,after_value,applied,undone "
                                "FROM workflow_import_field_merges WHERE batch_id=? AND "
                                "candidate_id=? AND ai_suggestion_id=? ORDER BY id",
                                (record["batch_id"], record["item_id"],
                                 str(audit_id)))]
                            candidate = conn.execute(
                                "SELECT status FROM workflow_import_candidates WHERE id=?",
                                (record["item_id"],)).fetchone()
                            record["_candidate_status"] = candidate[0] if candidate else None
            except sqlite3.OperationalError:
                record = None
        if record is None:
            raise _error(404, "import_ai_assist_audit_not_found",
                         "import_ai_assist.audit_not_found", {"audit_id": audit_id},
                         "审计记录不存在。")
        return _audit_view(record)


def _suggestion_view(entry: dict) -> dict:
    """HTTP 建议载荷（与审计存储分开：merge_conflict 的审计还含逐字段判定）。"""
    view = {
        "batch_id": entry["batch_id"], "kind": entry["kind"],
        "item_id": entry["item_id"], "verdict": entry["verdict"],
        "confidence": entry["confidence"], "rationale_zh": entry["rationale_zh"],
        "field_picks": entry["field_picks"], "degraded": entry["degraded"],
        "warnings": entry["warnings"], "audit_id": entry["audit_id"],
    }
    if entry["kind"] == "merge_conflict":
        view["keeper"] = entry.get("keeper")
        view["fields"] = entry.get("fields")
        view["suggested_conflict_picks"] = entry.get("suggested_conflict_picks")
    return view


def _json_list(text: Any) -> list:
    try:
        value = json.loads(text) if text else []
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _audit_view(record: dict) -> dict:
    """审计导出视图：非 merge_conflict 给扁平 field_picks；merge_conflict 给逐字段三栏。"""
    kind = record["item_kind"]
    payload = _parse_json_object(record.get("field_picks_json")) or {}
    view = {
        "audit_id": record["id"],
        "ai_suggestion_id": str(record["id"]),
        "task_id": record["task_id"],
        "batch_id": record["batch_id"],
        "kind": kind,
        "item_id": record["item_id"],
        "provider": record["provider"],
        "model": record["model"],
        "model_kind": record.get("model_kind") or "llm",
        "prompt_version": record["prompt_version"],
        "status": record["status"],
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
        "verdict": None if record["verdict"] == MERGE_VERDICT else record["verdict"],
        "confidence": record["confidence"],
        "rationale_zh": record["rationale_zh"],
        "warnings": _json_list(record["warnings_json"]),
        "field_picks": None,
        "keeper": None,
        "executed": False,
        "candidate_status": None,
        "fields": [],
    }
    if kind != "merge_conflict":
        view["field_picks"] = payload or None
        return view
    view["keeper"] = payload.get("keeper")
    trail = record.get("_trail") or []
    candidate_status = record.get("_candidate_status")
    executed = candidate_status == "duplicate"
    by_field: dict[str, list[dict]] = {}
    for row in trail:
        by_field.setdefault(row.get("field"), []).append(row)
    fields = []
    for row in payload.get("fields") or []:
        rows = by_field.get(row.get("field")) or []
        applied = [item for item in rows if item.get("applied") and not item.get("undone")]
        undone = bool(rows) and not applied
        if not executed:
            human_final, human_value = None, None
        elif applied:
            human_final = applied[-1].get("source_record_id") or "keeper"
            human_value = applied[-1].get("after_value")
        else:
            human_final, human_value = "keeper", row.get("left")
        fields.append({**row, "human_final": human_final,
                       "human_final_value": human_value, "undone": undone})
    view["fields"] = fields
    view["executed"] = executed
    view["candidate_status"] = candidate_status
    view["field_picks"] = {row.get("field"): (row.get("prefill") or "unsure")
                           for row in fields}
    return view


def _ensure_audit_schema(conn: sqlite3.Connection) -> None:
    """建表 + 旧 CHECK 约束升级（幂等、可断点重入）。

    2.1.5 G5 之前建的 ``import_ai_suggestions`` 只允许
    ``duplicate_group``/``metadata_diff`` 与 same/different/unsure；SQLite 不能
    ALTER CHECK，故重建：改名 → 建新表 → 拷行（``INSERT OR IGNORE``）→ 删旧表。
    任一步中断后重入都能继续（旧表在时先拷贝再删）。
    2.1.5 决策模型波给新表加 ``model_kind`` 列；G5 形状（无该列）的存量表用
    ALTER 幂等补列（回填 'llm'：决策模型钥匙在该波之前不存在，存量行只能
    是 LLM 通道），列已存在时忽略（并发先加列无碍）。
    """
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    legacy = "import_ai_suggestions_legacy" in tables
    sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='import_ai_suggestions'"
    ).fetchone()
    if sql_row is not None and "merge_conflict" not in str(sql_row[0] or ""):
        conn.executescript(
            "DROP INDEX IF EXISTS idx_import_ai_suggestions_request;\n"
            "ALTER TABLE import_ai_suggestions RENAME TO import_ai_suggestions_legacy;")
        legacy = True
    conn.executescript(_AUDIT_SCHEMA)
    if legacy:
        # 列清单是模块内固定常量（无任何外部输入），见 _AUDIT_COPY_SQL。
        conn.executescript(_AUDIT_COPY_SQL)
        conn.execute("DROP TABLE import_ai_suggestions_legacy")
    try:
        conn.execute("ALTER TABLE import_ai_suggestions "
                     "ADD COLUMN model_kind TEXT NOT NULL DEFAULT 'llm'")
    except sqlite3.OperationalError:
        pass  # 列已存在（新建表自带 / 他连接并发先加列）——幂等。
