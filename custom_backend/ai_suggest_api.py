"""AI 抽取建议（BYOK）路由：钥匙解析、任务设置、建议生成与逐条决策留痕。

对应 B4 设计包 §2（设置与密钥管理）、§3（建议生成/存储/轨迹/审计导出/Methods 报告）、
§4（Provider 客户端）与 §0 修订 R1（无 Key 零影响）/R2（双 Key 模式）/R3（辅助方式自定义）。

治理落点（B3）：
- P2 默认关：任务级 ``ai_suggest_settings.json`` 默认 ``enabled=false``；
- P3 BYOK：钥匙只存 ``{DATA_DIR}/.ai_keys/``（custom_backend/ai_keys.py），API 永回掩码；
- P4 无溯源不展示：无 quote 的建议在服务端直接丢弃；
- P5 逐条确认：决定端点只回传建议值给前端填表单，绝不写编码值；
- P6/P7 留痕可复现：``ai_suggestion_generated`` / ``ai_suggestion_decided`` 走既有
  ``research_trace.capture_event``（stage='coding'），provider/model/prompt_version 随建议存储；
- P9 字段分级：白名单为代码常量表（7 个研究特征字段），扩展需走方法学评审；
- 出站仅 stdlib ``urllib``（OpenAI 兼容 chat/completions，base_url 可指向本地 Ollama 实现零出网）。
"""
from __future__ import annotations

import http.client
import json
import os
import re
import sqlite3
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from coscreen import research_trace
from coscreen import tasks as tasks_mod
from coscreen import fulltext as fulltext_mod
from custom_backend import ai_keys

__all__ = [
    "FIELD_WHITELIST",
    "PROMPT_VERSION",
    "ProviderError",
    "register_ai_suggest_routes",
]

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 一期字段白名单（B4 §1，P9）：仅研究特征类；扩展白名单需走方法学评审。
FIELD_WHITELIST: dict[str, dict[str, str]] = {
    "sample_size": {"label": "样本量", "type": "text"},
    "study_design": {"label": "研究设计", "type": "text"},
    "country_setting": {"label": "国家/场景", "type": "text"},
    "publication_year": {"label": "发表年份", "type": "text"},
    "funding_source": {"label": "资助来源", "type": "text"},
    "inclusion_criteria": {"label": "纳入标准要点", "type": "text"},
    "study_objective": {"label": "研究目的", "type": "text"},
}

PROMPT_VERSION = "ai-suggest/v1"
TRACE_SOURCE = "ai_extract"
DEFAULT_TIMEOUT_SECONDS = 60.0          # B4 §3：超时 60s
PROVIDER_RETRIES = 2                    # 初次 + 重试 1 次
MAX_CONTEXT_CHARS = 60_000              # 发给 provider 的文本载荷截断预算
MAX_VALUE_CHARS = 4_000
MAX_QUOTE_CHARS = 2_000
MAX_PROVIDER_BYTES = 2 * 1024 * 1024      # B9 F11：provider 响应读取上限（2 MiB）
#: full 核验的引文最小长度（MM2-01/MM4-03）：短于该长度的引文即使逐字命中原文
#: 也只标 ``partial``——核验本就只证明"引文存在"，3~7 字符的常用短词（如 "the"）
#: 连这一点证据强度都不足以支撑对外标"完全核验"（value 从不参与核验，短引文
#: 极易成为给捏造值发 full 的通道）。恰达 8 字符与前缀路径的既有门槛一致。
MIN_FULL_QUOTE_CHARS = 8
MAX_CONTEXT_PROMPT_CHARS = 2_000        # R3：综述背景语境上限
USER_AGENT = "ReviewFlow-AI-Suggest/2.1.4"
_FALLBACK_MODEL = "gpt-4o-mini"         # 用户未填模型名时的兜底（仅 OpenAI 兼容协议缺省）

_KEY_RE = re.compile(r"^[^/\\\x00]{1,300}$")

_SETTINGS_FILENAME = "ai_suggest_settings.json"
_DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": False,                    # P2 默认关
    "key_mode": "personal",              # R2：personal | shared
    "allowed_field_ids": [],             # 任务白名单开关（⊆ FIELD_WHITELIST）
    "context_prompt": "",                # R3：owner 附加综述背景（≤2000 字）
    "acknowledged_at": None,
    "version": 0,
}

_DEFAULT_PREFS: dict[str, Any] = {
    "use_personal_key": True,            # R2：成员可切"我想用自己的 Key"
    "trigger": "manual",                 # R3：手动（默认）/ auto
    "field_ids": [],                     # R3：字段子集（空 = 全部任务允许字段）
    "presentation": "card",              # R3：常驻卡（默认）/ badge
}

#: 建议存储（B4 §3：个人层，不进共享库；report_evidence 的惰性建表模式）。
_SUGGESTIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_suggestions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  zotero_key TEXT NOT NULL,
  field_id TEXT NOT NULL,
  value TEXT NOT NULL,
  quote TEXT NOT NULL,
  pages_json TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  verified TEXT NOT NULL DEFAULT 'partial' CHECK(verified IN ('full','partial')),
  final_value TEXT,
  status TEXT NOT NULL CHECK(status IN ('pending','adopted','ignored','edited')),
  created_at TEXT NOT NULL,
  decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_suggestions_subject
  ON ai_suggestions(task_id,actor_id,zotero_key,field_id,id);
"""


class ProviderError(Exception):
    """出站调用失败（消息已脱敏：绝不含 API key 或完整请求载荷）。"""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(str(message)[:400])
        self.status = status


# ---------------------------------------------------------------------------
# 任务设置 / 个人偏好（JSON 文件，原子写参照 main.py 的 AL 设置先例）
# ---------------------------------------------------------------------------

def _settings_path(info) -> Path:
    return info.dir / _SETTINGS_FILENAME


def _prefs_path(info, username: str) -> Path:
    slug = tasks_mod.slugify_screener(username)
    return info.dir / f"ai_suggest_prefs_{slug}.json"


def _load_json_defaults(path: Path, defaults: dict) -> dict:
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = None
    merged = {**defaults}
    if isinstance(stored, dict):
        for key in defaults:
            if key in stored and stored[key] is not None:
                merged[key] = stored[key]
    return merged


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
        os.replace(temp, path)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass


def _load_settings(info) -> dict:
    settings = _load_json_defaults(_settings_path(info), _DEFAULT_SETTINGS)
    if settings["key_mode"] not in ("personal", "shared"):
        settings["key_mode"] = "personal"
    settings["allowed_field_ids"] = [f for f in settings["allowed_field_ids"] if f in FIELD_WHITELIST]
    settings["context_prompt"] = str(settings["context_prompt"] or "")[:MAX_CONTEXT_PROMPT_CHARS]
    return settings


def _save_settings(info, settings: dict) -> None:
    with tasks_mod._task_json_lock(info.dir):  # noqa: SLF001  与 main.py AL 设置同一条锁路径
        _atomic_write_json(_settings_path(info), settings)


def _load_prefs(info, username: str) -> dict:
    prefs = _load_json_defaults(_prefs_path(info, username), _DEFAULT_PREFS)
    if prefs["trigger"] not in ("manual", "auto"):
        prefs["trigger"] = "manual"
    if prefs["presentation"] not in ("card", "badge"):
        prefs["presentation"] = "card"
    prefs["use_personal_key"] = bool(prefs["use_personal_key"])
    prefs["field_ids"] = [f for f in prefs["field_ids"] if f in FIELD_WHITELIST]
    return prefs


# ---------------------------------------------------------------------------
# 建议存储（个人库）
# ---------------------------------------------------------------------------

def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    conn.executescript(_SUGGESTIONS_SCHEMA)
    try:  # 旧库补列（一期均为临时库，防御性兼容；列已存在则忽略）
        # MM2-01：缺省不得为 'full'——缺省 full 会让漏给 verified 的写入自动获得最强标记。
        conn.execute("ALTER TABLE ai_suggestions "
                     "ADD COLUMN verified TEXT NOT NULL DEFAULT 'partial'")
    except sqlite3.OperationalError:
        pass
    _retire_legacy_verified_full_default(conn)
    return conn


def _verified_column_default(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT dflt_value FROM pragma_table_info('ai_suggestions') "
                       "WHERE name='verified'").fetchone()
    return (row[0] or "").strip("'") if row is not None else None


#: r7 复审#4：存量 2.1.4 库（verified 列已存在且 DEFAULT 'full'）的同构重建迁移。
#: 逐语句执行（不用 executescript——它会先隐式 COMMIT，破坏原子性）；列清单
#: 与 _SUGGESTIONS_SCHEMA 完全一致，仅缺省为 'partial'；存量行按列名逐字复制
#: （verified 的既有取值不改写——变的是缺省，不是数据）。旧索引随 RENAME 跟随
#: 旧表、随 DROP 一并消失，最后按原名重建在新表上。
_LEGACY_VERIFIED_FULL_REBUILD = (
    """CREATE TABLE ai_suggestions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  zotero_key TEXT NOT NULL,
  field_id TEXT NOT NULL,
  value TEXT NOT NULL,
  quote TEXT NOT NULL,
  pages_json TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  verified TEXT NOT NULL DEFAULT 'partial' CHECK(verified IN ('full','partial')),
  final_value TEXT,
  status TEXT NOT NULL CHECK(status IN ('pending','adopted','ignored','edited')),
  created_at TEXT NOT NULL,
  decided_at TEXT
)""",
    """INSERT INTO ai_suggestions(
  id, task_id, actor_id, zotero_key, field_id, value, quote, pages_json,
  provider, model, prompt_version, verified, final_value, status, created_at, decided_at)
SELECT id, task_id, actor_id, zotero_key, field_id, value, quote, pages_json,
  provider, model, prompt_version, verified, final_value, status, created_at, decided_at
FROM ai_suggestions_legacy_vfull""",
    "DROP TABLE ai_suggestions_legacy_vfull",
    "CREATE INDEX IF NOT EXISTS idx_ai_suggestions_subject "
    "ON ai_suggestions(task_id,actor_id,zotero_key,field_id,id)",
)


def _retire_legacy_verified_full_default(conn: sqlite3.Connection) -> None:
    """MM2-01（r7 复审#4）：把存量库 verified 列的 DEFAULT 'full' 迁移为 'partial'。

    只在检测到该缺省时执行一次（迁移后幂等）；失败（他连接并发先迁/临时锁等
    OperationalError）回滚且不阻断连接——该缺省当前仅是潜伏面（唯一 INSERT
    显式传值），读取/写入永远优先。"""
    try:
        if _verified_column_default(conn) != "full":
            return
        conn.execute("BEGIN IMMEDIATE")
        if _verified_column_default(conn) != "full":  # 双检：他连接可能已迁移
            conn.execute("COMMIT")
            return
        conn.execute("ALTER TABLE ai_suggestions RENAME TO ai_suggestions_legacy_vfull")
        for statement in _LEGACY_VERIFIED_FULL_REBUILD:
            conn.execute(statement)
        conn.execute("COMMIT")
    except sqlite3.OperationalError:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass


def _row_to_dict(row: sqlite3.Row) -> dict:
    record = dict(row)
    try:
        record["pages"] = json.loads(record.pop("pages_json") or "[]")
    except (TypeError, ValueError):
        record["pages"] = []
    return record


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Provider 客户端（OpenAI 兼容，stdlib urllib；测试注入 fake 替换 _provider_generate）
# ---------------------------------------------------------------------------

class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """拒绝跟随 3xx 重定向（B9 F10）：防止 Authorization 钥匙被转发到未配置的主机。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def _provider_generate(system: str, user: str, *, api_key: str, base_url: str,
                       model: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict:
    """最小 completion 调用；返回 ``{"content": str, "usage": dict, "model": str}``。

    日志红线（B4 §4）：本函数抛出的任何错误消息都不得包含 api_key 或请求载荷；
    只落 provider、状态码、耗时语义。安全红线（B9 F10/F11）：不跟随重定向
    （30x 视为错误）；响应体读取上限 2 MiB（无上限读取可被用于内存 DoS）。
    """
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310  base_url 经用户配置并限 http(s)
        url, data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        })
    try:
        with _OPENER.open(request, timeout=timeout) as response:  # noqa: S310
            raw = response.read(MAX_PROVIDER_BYTES + 1)
            if len(raw) > MAX_PROVIDER_BYTES:
                raise ProviderError(
                    f"provider 响应超过 {MAX_PROVIDER_BYTES // 1024} KiB 读取上限。")
            body = json.loads(raw.decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise ProviderError(
                f"provider HTTP {exc.code}: 重定向被拒绝（为保护 API 钥匙不外发，请直接配置最终端点）。") from None
        # MM-06：非 2xx 只回状态码＋固定文案，provider 响应体绝不回显——本出口被
        # verify=true / 生成 / 测试连接共用，回显（原先前 200 字符）构成内网 SSRF
        # 读预言机（docs §4 披露口径：仅"据响应差异探测"，不含响应体内容）。
        raise ProviderError(f"provider HTTP {exc.code}（详情不回显）", status=exc.code) from None
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        text = f"provider 连接失败: {reason}" if reason else "provider 连接失败"
        raise ProviderError(text) from None
    except http.client.HTTPException as exc:  # MM-09：InvalidURL 等 URL 级错误绝不冒泡 500
        raise ProviderError(f"provider URL 非法: {type(exc).__name__}") from None
    except (TimeoutError, OSError) as exc:
        raise ProviderError(f"provider 超时（{int(timeout)}s）: {type(exc).__name__}") from None
    except ValueError as exc:
        raise ProviderError("provider 响应不是合法 JSON。") from None
    try:
        content = body["choices"][0]["message"]["content"]
        usage = body.get("usage") or {}
    except (KeyError, TypeError, IndexError) as exc:
        raise ProviderError("provider 响应缺少 choices/content。") from exc
    if not isinstance(content, str):
        raise ProviderError("provider 返回的 content 不是文本。")
    return {"content": content, "usage": usage, "model": str(body.get("model") or model)}


def _call_provider(system: str, user: str, *, api_key: str, base_url: str,
                   model: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict:
    """带重试（1 次）与脱敏兜底的包装；测试通过替换 ``_provider_generate`` 注入 fake。"""
    last: ProviderError | None = None
    for attempt in range(PROVIDER_RETRIES):
        try:
            return _provider_generate(system, user, api_key=api_key, base_url=base_url,
                                      model=model, timeout=timeout)
        except ProviderError as exc:
            last = exc
            transient = exc.status is None or exc.status >= 500 or exc.status == 429
            if not transient or attempt == PROVIDER_RETRIES - 1:
                break
    message = str(last or "provider 调用失败")
    if api_key and api_key in message:  # 双保险：错误消息绝不携带明文
        message = message.replace(api_key, "***")
    raise ProviderError(message, status=last.status if last else None)


def _provider_http_error(exc: ProviderError, api_key: str) -> HTTPException:
    """ProviderError -> 502（错误摘要进 detail；key 绝不进错误信息，B4 §3）。"""
    message = str(exc)
    if api_key and api_key in message:
        message = message.replace(api_key, "***")
    return HTTPException(502, {"code": "ai_provider_error", "detail": message[:300]})


# ---------------------------------------------------------------------------
# 提示词组装与解析
# ---------------------------------------------------------------------------

def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().casefold()


def _build_messages(field_ids: list[str], article: dict, page_texts: list[str],
                    abstract: str, context_prompt: str) -> tuple[str, str, str]:
    """返回 ``(system, prefix, user)``；prefix=题录+摘要（截断时永不裁掉，B9 F16）。"""
    definitions = {fid: {"label": FIELD_WHITELIST[fid]["label"], "type": "text"} for fid in field_ids}
    system_parts = [
        "你是一名系统综述的研究助理，从单篇研究文献的原文中提取研究特征字段。",
        "规则：",
        "1. 只依据用户消息提供的原文内容作答；原文中没有充分依据时该字段必须返回 null。",
        "2. 每个非 null 字段必须给出 quote（原文逐字引文，不超过 300 字符）与 pages"
        "（引文所在 PDF 页码的整数数组，从 1 起；无页码信息时给 []）。"
        "quote 必须逐字复制自原文——服务端会核验，核验不过的建议将被丢弃。",
        "3. 不得编造、不得外推、不得使用原文之外的知识；不确定就返回 null。",
        '4. 只输出一个 JSON 对象，不要任何解释文本或代码块：'
        '{"<field_id>": {"value": <字符串或 null>, "quote": <字符串或 null>, "pages": [<整数>, ...]}}。',
        f"待提取字段定义：{json.dumps(definitions, ensure_ascii=False)}",
    ]
    if context_prompt.strip():
        system_parts.append(f"任务负责人提供的综述背景（仅供参考，不得替代原文依据）：{context_prompt.strip()}")
    system = "\n".join(system_parts)

    citation = {
        "title": article.get("title") or "",
        "authors": article.get("authors") or "",
        "journal": article.get("journal") or "",
        "year": article.get("year") or "",
    }
    user_parts = [f"文献题录：{json.dumps(citation, ensure_ascii=False)}"]
    if abstract.strip():
        user_parts.append(f"摘要：{abstract.strip()}")
    prefix = "\n\n".join(user_parts)
    if page_texts:
        user_parts.append("全文（按页给出）：")
        for index, text in enumerate(page_texts, start=1):
            user_parts.append(f"[第 {index} 页]\n{text}")
    return system, prefix, "\n\n".join(user_parts)


def _parse_provider_content(content: str, field_ids: list[str],
                            page_texts: list[str],
                            abstract: str = "") -> tuple[list[dict], list[str], int]:
    """解析模型 JSON 并做引文核验（P4；B9 F1/F9；MM2-01 门槛分级）。

    - 无 quote 的建议直接丢弃（P4）；
    - **quote 不在原文（全文任一页或摘要）中出现的建议同样丢弃**（服务端归一化
      包含校验；先试全文引文、再试前 80 字符）；
    - 核验只证明引文存在于原文，**value 从不参与核验**：短于
      ``MIN_FULL_QUOTE_CHARS`` 的命中引文降级 ``partial``（MM2-01）；
    - pages 一律以服务端定位结果为准（模型给的 pages 仅作参考不再采信）：
      命中 PDF 页返回页号列表；仅命中摘要返回 ``[]``（无页可跳，前端标注
      摘要来源——MM2-04）；
    - provider 载荷是不可信输入：形状非法（pages 非数组/非整数值等）按无效处理，
      绝不让 TypeError 冒泡成 500。

    返回 ``(保留建议, warnings, 被丢弃条数)``；模型明示 null（原文无依据）不计丢弃。
    """
    warnings: list[str] = []
    text = (content or "").strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return [], ["provider_response_not_json"], 0
    try:
        parsed = json.loads(text[start:end + 1])
    except ValueError:
        return [], ["provider_response_not_json"], 0
    if not isinstance(parsed, dict):
        return [], ["provider_response_not_json"], 0

    normalized_pages = [_normalize_text(p) for p in page_texts]
    normalized_abstract = _normalize_text(abstract)

    def locate_quote(quote: str) -> tuple[list[int], str] | None:
        """核验引文来源（B9 R-1 收紧；MM2-01 门槛分级）。

        返回 ``(pages, verified)``：pages 为页号列表（摘要命中=[]）；
        verified ∈ {"full","partial"}。原文中完全找不到返回 None（伪造）。

        - full：完整归一化引文（允许去尾标点变体）逐字命中，且引文长度达到
          ``MIN_FULL_QUOTE_CHARS``（8 字符）——短引文证据强度不足以支撑
          "完全核验"的对外语义（MM2-01：3 字符常用词曾可给捏造值发 full）；
        - partial：≥3 字符但短于 ``MIN_FULL_QUOTE_CHARS`` 的完整命中；或引文
          超过 80 字符时前 80 字符前缀命中（真前缀+编造尾段无法冒充 full）；
          建议保留但标注"部分核验"。
        - <3 字符：不参与定位（按伪造丢弃）。
        """
        needle = _normalize_text(quote)
        strip_punct = lambda s: s.rstrip(".,;:!?'\u201d\u2019。，；：！？\u3002")  # noqa: E731

        def find(candidate: str) -> list[int] | None:
            hits = sorted({i for i, page in enumerate(normalized_pages, start=1)
                           if candidate in page})
            if hits:
                return hits
            if candidate in normalized_abstract:
                return []
            return None

        for exact in (needle, strip_punct(needle)):
            if len(exact) >= 3:
                found = find(exact)
                if found is not None:
                    # MM2-01：full 需达到最小引文长度；短引文命中降级 partial。
                    return found, "full" if len(exact) >= MIN_FULL_QUOTE_CHARS else "partial"
        if len(needle) > 80:
            for prefix in (needle[:80], strip_punct(needle[:80])):
                if len(prefix) >= 8:
                    found = find(prefix)
                    if found is not None:
                        return found, "partial"
        return None

    kept: list[dict] = []
    dropped = 0
    for fid in field_ids:
        entry = parsed.get(fid)
        if not isinstance(entry, dict):
            continue
        quote = str(entry.get("quote") or "").strip()
        value = entry.get("value")
        if value is None:
            continue  # 模型明示原文无依据 -> 无建议
        if not quote:
            dropped += 1  # P4：有值但无引文 -> 服务端直接丢弃
            continue
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            dropped += 1
            continue
        located = locate_quote(quote)
        if located is None:
            # 引文核验不过（不在任何一页也不在摘要）-> 丢弃；计数走
            # unverified_quote warnings，与"无引文"分开（B9 R-10）。
            warnings.append(f"unverified_quote:{fid}")
            continue
        pages, verified = located
        item = {
            "field_id": fid,
            "value": str(value).strip()[:MAX_VALUE_CHARS],
            "quote": quote[:MAX_QUOTE_CHARS],
            "pages": pages,
            "verified": verified,
        }
        if verified == "partial":
            warnings.append(f"partial_quote:{fid}")
        kept.append(item)
    return kept, warnings, dropped


def _truncate_user_message(prefix: str, page_texts: list[str]) -> tuple[str, int]:
    """组装并按预算截断（B9 F16）：题录+摘要（prefix）永不裁掉，先裁全文页。"""
    if not page_texts:
        return prefix, 0
    blocks = [f"[第 {i + 1} 页]\n{p}" for i, p in enumerate(page_texts)]
    total_len = len(prefix) + sum(len(b) + 2 for b in blocks)
    if total_len <= MAX_CONTEXT_CHARS:
        return prefix + "\n\n" + "\n\n".join(blocks), 0
    kept: list[str] = []
    used = len(prefix)
    for block in blocks:
        if used + len(block) + 2 > MAX_CONTEXT_CHARS:
            break
        kept.append(block)
        used += len(block) + 2
    message = "\n\n".join([prefix, *kept]) if kept else prefix
    return message, max(0, total_len - len(message))


# ---------------------------------------------------------------------------
# 轨迹（P6：走既有 capture_event，不改 research_trace.py 任何行为）
# ---------------------------------------------------------------------------

def _trace_generated(conn: sqlite3.Connection, task_id: str, user: dict, zotero_key: str,
                     kept: list[dict], dropped: int, provider: str, model: str,
                     usage: dict, truncated_chars: int, warnings: list[str]) -> None:
    context = research_trace.TraceContext(task_id=task_id, actor_id=user["user_id"],
                                          source=TRACE_SOURCE)
    research_trace.ensure_default_recording(conn, context)
    research_trace.capture_event(
        conn, context, stage="coding", action="ai_suggestion_generated",
        subject_id=zotero_key, before=None,
        after={
            "value": "ai_suggestions",
            "count": len(kept),
            "dropped_no_quote": dropped,
            "fields": [{"field_id": s["field_id"], "value": s["value"][:200]} for s in kept],
            "provider": provider, "model": model, "prompt_version": PROMPT_VERSION,
            "usage": usage or {}, "truncated_chars": truncated_chars,
            "warnings": warnings[:10],
        })


def _trace_decided(conn: sqlite3.Connection, task_id: str, user: dict, row: dict,
                   status: str, final_value: str | None) -> None:
    context = research_trace.TraceContext(task_id=task_id, actor_id=user["user_id"],
                                          source=TRACE_SOURCE)
    research_trace.ensure_default_recording(conn, context)
    before = {"field_id": row["field_id"], "value": str(row["value"])[:200],
              "status": "pending", "provider": row["provider"], "model": row["model"]}
    after: dict[str, Any] = {"field_id": row["field_id"], "status": status}
    if final_value is not None:
        after["final_value"] = final_value[:200]
    research_trace.capture_event(conn, context, stage="coding",
                                 action="ai_suggestion_decided", subject_id=row["zotero_key"],
                                 before=before, after=after)


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

class KeyPutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=1, max_length=50)
    key: str = Field(min_length=8, max_length=4096)
    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=100)
    # B4 §2"校验连通后保存"以显式选项实现：默认不强制出站（离线/内网可先保存）。
    verify: bool = False


class TaskSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    enabled: bool = False
    key_mode: Literal["personal", "shared"] = "personal"
    allowed_field_ids: list[str] = Field(default_factory=list, max_length=32)
    context_prompt: str = Field(default="", max_length=MAX_CONTEXT_PROMPT_CHARS)


class PrefsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    use_personal_key: bool = True
    trigger: Literal["manual", "auto"] = "manual"
    field_ids: list[str] = Field(default_factory=list, max_length=32)
    presentation: Literal["card", "badge"] = "card"


class SuggestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    zotero_key: str = Field(min_length=1, max_length=300)
    field_ids: list[str] = Field(min_length=1, max_length=32)
    expected_policy_version: int | None = Field(default=None, ge=1)


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["adopted", "ignored", "edited"]
    final_value: str | None = Field(default=None, max_length=MAX_VALUE_CHARS)


def register_ai_suggest_routes(app, require_user, task_lookup, screener_db, data_dir) -> None:
    """注册 AI 抽取建议路由（依赖注入与 workspace_undo_api 同款模式）。"""
    # 钥匙保险柜根与 main.DATA_DIR 同一绑定（显式传参，不二次读环境变量）。
    data_root = Path(data_dir)

    def _workflow():
        return app.state.review_workflow

    def _context(task_id: str, user: dict):
        return _workflow().context(task_id, user)

    def _owner_context(task_id: str, user: dict):
        ctx = _context(task_id, user)
        if not ctx.can_manage:
            raise HTTPException(403, "Only the task owner can manage AI suggestion settings.")
        return ctx

    def _base_url_meta(record: dict | None) -> str | None:
        """元数据读取路径的 base_url（r7 复审#1：绝不 500）。

        存量钥匙记录可能带着历史版本接受的非法 base_url（如控制字符）——此时
        回传原始串（本人的配置，便于排查重存）；非法值只在出站前校验并映射 422。
        捕 ValueError 而非仅 KeyShapeError：urlsplit 对 'http://[::1' 等畸形 URL
        抛裸 ValueError（复审第 2 轮），KeyShapeError 亦是其子类，一并兜住。"""
        if record is None:
            return None
        try:
            return ai_keys.base_url_of(record)
        except ValueError:
            raw = record.get("base_url")
            return raw if isinstance(raw, str) and raw else None

    def _masked_metadata(record: dict | None, source: str | None) -> dict:
        if record is None:
            return {"configured": False, "provider": None, "masked": None,
                    "base_url": None, "model": None, "source": None,
                    "added_at": None, "last_used_at": None}
        return {"configured": True, "provider": record.get("provider"),
                "masked": ai_keys.mask_key(record.get("key", "")),
                "base_url": _base_url_meta(record),
                "model": record.get("model") or "", "source": source,
                "added_at": record.get("added_at"), "last_used_at": record.get("last_used_at")}

    def _verify_credentials(provider: str, key: str, base_url: str | None, model: str | None) -> None:
        """保存前的连通性校验（最小 completion；异常映射 502，key 不进错误）。"""
        try:
            normalized = ai_keys.base_url_of({"base_url": base_url})
        except ai_keys.KeyShapeError as exc:
            raise HTTPException(422, str(exc)) from None
        try:
            _call_provider(
                "You are a connectivity check. Reply with a JSON object.",
                '{"ping": true}', api_key=key, base_url=normalized,
                model=(str(model or "").strip() or _FALLBACK_MODEL), timeout=15.0)
        except ProviderError as exc:
            raise _provider_http_error(exc, key) from None

    # -- 个人钥匙（仅本人 JWT） --------------------------------------------

    @app.get("/api/ai/key")
    def get_personal_key(user: dict = Depends(require_user)) -> dict:
        env = ai_keys.env_key()
        if env is not None:
            return _masked_metadata(env, "env")
        record = ai_keys.load_personal_key(user["user_id"], data_root)
        return _masked_metadata(record, "file" if record else None)

    @app.put("/api/ai/key")
    def put_personal_key(body: KeyPutIn, user: dict = Depends(require_user)) -> dict:
        if body.verify:
            _verify_credentials(body.provider, body.key, body.base_url, body.model)
        try:
            record = ai_keys.save_personal_key(
                user["user_id"], provider=body.provider, key=body.key,
                base_url=body.base_url, model=body.model, data_root=data_root)
        except ai_keys.KeyShapeError as exc:
            raise HTTPException(422, str(exc)) from None
        return _masked_metadata(record, "file")

    @app.delete("/api/ai/key")
    def delete_personal_key(user: dict = Depends(require_user)) -> dict:
        try:
            ai_keys.delete_personal_key(user["user_id"], data_root)
        except ai_keys.KeyDeletionError as exc:  # B9 F2：删除失败绝不假报成功
            raise HTTPException(503, {"code": "ai_key_delete_failed", "detail": str(exc)}) from None
        return _masked_metadata(None, None)

    @app.post("/api/ai/key/test")
    def test_personal_key(user: dict = Depends(require_user)) -> dict:
        record = ai_keys.env_key() or ai_keys.load_personal_key(user["user_id"], data_root)
        if record is None:
            raise HTTPException(409, {"code": "ai_key_missing",
                                      "detail": "尚未配置个人 AI 钥匙。"})
        try:  # MM-09：历史存量记录若含非法 base_url，映射 422 而非未捕获 500。
            base_url = ai_keys.base_url_of(record)
        except ai_keys.KeyShapeError as exc:
            raise HTTPException(422, str(exc)) from None
        started = time.monotonic()
        try:
            result = _call_provider(
                "You are a connectivity check. Reply with a JSON object.",
                '{"ping": true}', api_key=record["key"], base_url=base_url,
                model=(record.get("model") or _FALLBACK_MODEL), timeout=15.0)
        except ProviderError as exc:
            raise _provider_http_error(exc, record["key"]) from None
        ai_keys.touch_last_used(ai_keys.personal_path(user["user_id"], data_root))
        return {"ok": True, "provider": record["provider"],
                "model": result.get("model"), "latency_ms": int((time.monotonic() - started) * 1000),
                "usage": result.get("usage") or {}}

    # -- 任务共享钥匙（owner 写；所有人只见元数据） --------------------------

    @app.get("/api/tasks/{task_id}/ai-suggest/shared-key")
    def get_shared_key(task_id: str, user: dict = Depends(require_user)) -> dict:
        info = task_lookup(task_id)
        _context(task_id, user)  # 参与者可见性闸门（require_user 已做，双检无害）
        record = ai_keys.load_shared_key(info.task_id, data_root)
        return {"enabled": record is not None,
                "provider_name": record.get("provider") if record else None}

    @app.put("/api/tasks/{task_id}/ai-suggest/shared-key")
    def put_shared_key(task_id: str, body: KeyPutIn,
                       user: dict = Depends(require_user)) -> dict:
        info = task_lookup(task_id)
        _owner_context(task_id, user)
        if body.verify:
            _verify_credentials(body.provider, body.key, body.base_url, body.model)
        try:
            record = ai_keys.save_shared_key(
                info.task_id, provider=body.provider, key=body.key,
                base_url=body.base_url, model=body.model, data_root=data_root)
        except ai_keys.KeyShapeError as exc:
            raise HTTPException(422, str(exc)) from None
        return {"enabled": True, "provider_name": record["provider"]}

    @app.delete("/api/tasks/{task_id}/ai-suggest/shared-key")
    def delete_shared_key(task_id: str, user: dict = Depends(require_user)) -> dict:
        info = task_lookup(task_id)
        _owner_context(task_id, user)
        try:
            ai_keys.delete_shared_key(info.task_id, data_root)
        except ai_keys.KeyDeletionError as exc:  # B9 F2
            raise HTTPException(503, {"code": "ai_key_delete_failed", "detail": str(exc)}) from None
        return {"enabled": False, "provider_name": None}

    @app.get("/api/tasks/{task_id}/ai-suggest/status")
    def get_ai_status(task_id: str, user: dict = Depends(require_user)) -> dict:
        """参与者可读的任务 AI 状态（B7 审查修复：成员在 personal 模式下也需知道任务已启用）。

        只暴露非敏感配置（B4 §0 R2 披露条款）：开关、钥匙模式、白名单子集、共享钥匙
        存在性与 provider 名、字段标签表；任何钥匙元数据/明文一概不出。
        """
        info = task_lookup(task_id)
        ctx = _context(task_id, user)
        settings = _load_settings(info)
        shared = ai_keys.load_shared_key(info.task_id, data_root)
        shared_mode = (settings.get("key_mode") or "personal") == "shared"
        return {
            "enabled": bool(settings.get("enabled")),
            "key_mode": settings.get("key_mode") or "personal",
            "allowed_field_ids": sorted(settings.get("allowed_field_ids") or []),
            "field_labels": {fid: FIELD_WHITELIST[fid]["label"] for fid in FIELD_WHITELIST},
            # B9 F23：仅 shared 模式下暴露共享钥匙元数据；F13：legacy 无 owner 任务明示迁移提示。
            "shared_available": shared is not None and shared_mode,
            "shared_provider_name": (shared.get("provider") if shared else None) if shared_mode else None,
            "owner_unset": ctx.owner is None,
        }

    # -- 任务 AI 设置（owner；expected_version 乐观锁，B4 §2） ----------------

    @app.get("/api/tasks/{task_id}/ai-suggest/settings")
    def get_task_settings(task_id: str, user: dict = Depends(require_user)) -> dict:
        info = task_lookup(task_id)
        _owner_context(task_id, user)
        settings = _load_settings(info)
        shared = ai_keys.load_shared_key(info.task_id, data_root)
        return {**settings, "field_whitelist": {fid: FIELD_WHITELIST[fid]["label"] for fid in FIELD_WHITELIST},
                "shared_key_enabled": shared is not None,
                "shared_provider_name": shared.get("provider") if shared else None}

    @app.put("/api/tasks/{task_id}/ai-suggest/settings")
    def put_task_settings(task_id: str, body: TaskSettingsIn,
                          user: dict = Depends(require_user)) -> dict:
        info = task_lookup(task_id)
        _owner_context(task_id, user)
        unknown = sorted(set(body.allowed_field_ids) - set(FIELD_WHITELIST))
        if unknown:
            raise HTTPException(422, {"code": "ai_field_not_in_whitelist", "fields": unknown})
        with tasks_mod._task_json_lock(info.dir):  # noqa: SLF001  读改写走同一条任务锁
            settings = _load_settings(info)
            if body.expected_version != settings["version"]:
                raise HTTPException(409, {"code": "ai_settings_version_conflict",
                                          "version": settings["version"]})
            was_enabled = bool(settings["enabled"])
            settings.update({
                "enabled": body.enabled,
                "key_mode": body.key_mode,
                "allowed_field_ids": sorted(set(body.allowed_field_ids)),
                "context_prompt": body.context_prompt[:MAX_CONTEXT_PROMPT_CHARS],
                "version": int(settings["version"]) + 1,
            })
            if body.enabled and not was_enabled and not settings.get("acknowledged_at"):
                settings["acknowledged_at"] = _utc_now_iso()
            _atomic_write_json(_settings_path(info), settings)
        return {**settings, "field_whitelist": {fid: FIELD_WHITELIST[fid]["label"] for fid in FIELD_WHITELIST}}

    # -- 个人辅助方式偏好（R3；每人自己的文件） -------------------------------

    @app.get("/api/tasks/{task_id}/ai-suggest/preferences")
    def get_prefs(task_id: str, user: dict = Depends(require_user)) -> dict:
        info = task_lookup(task_id)
        _context(task_id, user)
        return _load_prefs(info, user["username"])

    @app.put("/api/tasks/{task_id}/ai-suggest/preferences")
    def put_prefs(task_id: str, body: PrefsIn, user: dict = Depends(require_user)) -> dict:
        info = task_lookup(task_id)
        _context(task_id, user)
        unknown = sorted(set(body.field_ids) - set(FIELD_WHITELIST))
        if unknown:
            raise HTTPException(422, {"code": "ai_field_not_in_whitelist", "fields": unknown})
        prefs = {"use_personal_key": body.use_personal_key, "trigger": body.trigger,
                 "field_ids": sorted(set(body.field_ids)), "presentation": body.presentation}
        _atomic_write_json(_prefs_path(info, user["username"]), prefs)
        return prefs

    # -- 钥匙解析（R2 优先级：个人 > 任务共享 > 无） ---------------------------

    def _resolve_credentials(info, settings: dict, prefs: dict, user: dict) -> tuple[dict | None, str | None]:
        if prefs.get("use_personal_key", True):
            env = ai_keys.env_key()
            if env is not None:
                return env, "personal"
            record = ai_keys.load_personal_key(user["user_id"], data_root)
            if record is not None:
                return record, "personal"
        if settings.get("key_mode") == "shared":
            record = ai_keys.load_shared_key(info.task_id, data_root)
            if record is not None:
                return record, "shared"
        return None, None

    # -- 建议生成（B4 §3） ----------------------------------------------------

    @app.post("/api/tasks/{task_id}/coding/ai-suggestions")
    def generate_suggestions(task_id: str, body: SuggestIn,
                             user: dict = Depends(require_user)) -> dict:
        info = task_lookup(task_id)
        ctx = _context(task_id, user)
        settings = _load_settings(info)
        if not settings.get("enabled"):
            # P2 默认关：403 携带机器可读 code（B4 §3）。
            raise HTTPException(403, {"code": "ai_suggest_disabled",
                                      "detail": "任务未启用 AI 抽取建议。"})
        prefs = _load_prefs(info, user["username"])
        credentials, key_source = _resolve_credentials(info, settings, prefs, user)
        if credentials is None:
            # R1/无 Key 零影响：返回明确 disabled 状态而非抛错（B6 任务书钥匙优先级条款）。
            return {"status": "disabled", "code": "ai_key_missing", "reason": "ai_key_missing",
                    "suggestions": [], "usage": None, "warnings": ["ai_key_missing"]}

        requested = sorted({f for f in body.field_ids if f.strip()})
        if not requested:
            raise HTTPException(422, "field_ids 不能为空。")
        if body.expected_policy_version is not None and body.expected_policy_version != settings["version"]:
            raise HTTPException(409, {"code": "ai_policy_version_conflict",   # B9 F15
                                      "detail": "任务 AI 设置已变更，请刷新后重试。",
                                      "version": settings["version"]})
        not_in_whitelist = [f for f in requested if f not in FIELD_WHITELIST]
        allowed_by_task = set(settings.get("allowed_field_ids") or [])
        not_allowed = [f for f in requested if f in FIELD_WHITELIST and f not in allowed_by_task]
        # B9 F8：三级白名单在服务端强制——个人偏好勾选了字段子集时，请求必须落在子集内。
        pref_fields = set(prefs.get("field_ids") or [])
        not_in_prefs = [f for f in requested if pref_fields and f not in pref_fields]
        if not_in_whitelist or not_allowed or not_in_prefs:
            raise HTTPException(403, {"code": "ai_field_not_allowed",
                                      "fields": not_in_whitelist + not_allowed + not_in_prefs})

        article = _workflow().article_row(ctx.owner_db, body.zotero_key)
        if article is None or article.get("is_duplicate_of") is not None:
            raise HTTPException(404, {"code": "article_not_found",
                                      "detail": "文献不存在或不是有效主记录。"})
        abstract = str(article.get("abstract") or "")
        page_texts: list[str] = []
        for source_db in (ctx.actor_db, ctx.owner_db):
            meta = fulltext_mod.get_fulltext(source_db, body.zotero_key)
            if meta is not None and Path(meta.path).is_file():
                page_texts = fulltext_mod.pdf_text(meta.path)
                if page_texts and page_texts[0] != fulltext_mod.EXTRACT_FAILED_TEXT:
                    break
                page_texts = []
        if not page_texts and not abstract.strip():
            raise HTTPException(409, {"code": "ai_no_source_text",
                                      "detail": "该文献既无全文 PDF 也无摘要可用。"})

        system, prefix, _user_full = _build_messages(requested, article, page_texts, abstract,
                                                     str(settings.get("context_prompt") or ""))
        user_message, truncated_chars = _truncate_user_message(prefix, page_texts)
        try:  # MM-09：历史存量记录若含非法 base_url，映射 422 而非未捕获 500。
            base_url = ai_keys.base_url_of(credentials)
        except ai_keys.KeyShapeError as exc:
            raise HTTPException(422, str(exc)) from None
        model = (credentials.get("model") or "").strip() or _FALLBACK_MODEL
        try:
            result = _call_provider(system, user_message, api_key=credentials["key"],
                                    base_url=base_url, model=model,
                                    timeout=DEFAULT_TIMEOUT_SECONDS)
        except ProviderError as exc:
            raise _provider_http_error(exc, credentials["key"]) from None
        ai_keys.touch_last_used(ai_keys.personal_path(user["user_id"], data_root)
                                if key_source == "personal"
                                else ai_keys.shared_path(info.task_id, data_root))

        kept, warnings, dropped = _parse_provider_content(result["content"], requested,
                                                          page_texts, abstract)
        if dropped:
            warnings.append(f"dropped_no_quote:{dropped}")
        if truncated_chars:
            warnings.append(f"context_truncated:{truncated_chars}")
        provider = credentials.get("provider") or ""
        model_used = str(result.get("model") or model)
        generated_at = _utc_now_iso()

        db = screener_db(info, user["username"])
        with closing(_connect(db)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            stored: list[dict] = []
            for suggestion in kept:
                cursor = conn.execute(
                    "INSERT INTO ai_suggestions(task_id,actor_id,zotero_key,field_id,value,quote,"
                    "pages_json,provider,model,prompt_version,verified,final_value,status,created_at,decided_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL,'pending',?,NULL)",
                    (info.task_id, str(user["user_id"]), body.zotero_key, suggestion["field_id"],
                     suggestion["value"], suggestion["quote"],
                     json.dumps(suggestion["pages"]), provider, model_used,
                     PROMPT_VERSION, suggestion.get("verified") or "partial", generated_at))
                stored.append({**suggestion, "id": int(cursor.lastrowid),
                               "status": "pending", "generated_at": generated_at})
            _trace_generated(conn, info.task_id, user, body.zotero_key, kept,
                             dropped=dropped,
                             provider=provider, model=model_used,
                             usage=result.get("usage") or {}, truncated_chars=truncated_chars,
                             warnings=warnings)
        return {
            "status": "ok", "key_source": key_source,
            "suggestions": [{**s, "provider": provider, "model": model_used,
                             "prompt_version": PROMPT_VERSION} for s in stored],
            "usage": result.get("usage") or {}, "warnings": warnings,
            "truncated_chars": truncated_chars, "generated_at": generated_at,
        }

    @app.get("/api/tasks/{task_id}/coding/ai-suggestions/{zotero_key}")
    def list_suggestions(task_id: str, zotero_key: str,
                         user: dict = Depends(require_user)) -> dict:
        if not _KEY_RE.match(zotero_key or ""):
            raise HTTPException(400, "非法文献键。")
        info = task_lookup(task_id)
        ctx = _context(task_id, user)
        with closing(_connect(ctx.actor_db)) as conn:
            rows = conn.execute(
                "SELECT * FROM ai_suggestions WHERE task_id=? AND actor_id=? AND zotero_key=? "
                "ORDER BY id DESC", (info.task_id, str(user["user_id"]), zotero_key)).fetchall()
        return {"suggestions": [_row_to_dict(row) for row in rows], "total": len(rows)}

    # -- 建议决定（P1/P5：adopt 只回传值，绝不写编码值） -----------------------

    @app.post("/api/tasks/{task_id}/coding/ai-suggestions/{suggestion_id}/decision")
    def decide_suggestion(task_id: str, suggestion_id: int, body: DecisionIn,
                          user: dict = Depends(require_user)) -> dict:
        if suggestion_id <= 0:
            raise HTTPException(404, "建议不存在。")
        info = task_lookup(task_id)
        ctx = _context(task_id, user)
        db = screener_db(info, user["username"])
        with closing(_connect(db)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM ai_suggestions WHERE id=? AND task_id=? AND actor_id=?",
                (suggestion_id, info.task_id, str(user["user_id"]))).fetchone()
            if row is None:
                raise HTTPException(404, "建议不存在或不属于当前用户。")
            record = _row_to_dict(row)
            if record["status"] != "pending":
                raise HTTPException(409, {"code": "ai_suggestion_already_decided",
                                          "status": record["status"]})
            final_value: str | None = None
            if body.status == "edited":
                if body.final_value is None or not str(body.final_value).strip():
                    raise HTTPException(400, {"code": "ai_final_value_required",
                                              "detail": "编辑后采纳需提供最终值。"})
                final_value = str(body.final_value).strip()[:MAX_VALUE_CHARS]
            decided_at = _utc_now_iso()
            conn.execute(
                "UPDATE ai_suggestions SET status=?, final_value=?, decided_at=? WHERE id=?",
                (body.status, final_value, decided_at, suggestion_id))
            _trace_decided(conn, info.task_id, user, record, body.status, final_value)
        # P1：adopt 不写编码值——只把建议值随响应返回给前端去填表单，
        # 落库仍走既有 coding 保存路径（values-batch / save_form）。
        return {"suggestion": {**record, "status": body.status, "final_value": final_value,
                               "decided_at": decided_at},
                "value": final_value if final_value is not None else record["value"]}

    # -- 审计导出（P8）与 Methods 报告（P7） ----------------------------------

    @app.get("/api/tasks/{task_id}/ai-suggestions/export")
    def export_decisions(task_id: str, format: Literal["csv", "json"] = Query("csv"),
                         user: dict = Depends(require_user)) -> Response:
        info = task_lookup(task_id)
        ctx = _context(task_id, user)
        with closing(_connect(ctx.actor_db)) as conn:
            rows = conn.execute(
                "SELECT * FROM ai_suggestions WHERE task_id=? AND actor_id=? ORDER BY id",
                (info.task_id, str(user["user_id"]))).fetchall()
        records = [_row_to_dict(row) for row in rows]
        if format == "json":
            payload = json.dumps({"schema": "reviewflow.ai-suggestions/1",
                                  "prompt_version": PROMPT_VERSION, "records": records},
                                 ensure_ascii=False, indent=2)
            return Response(payload.encode("utf-8"), media_type="application/json",
                            headers={"Content-Disposition": 'attachment; filename="ai-suggestions.json"'})
        import csv as csv_mod
        import io as io_mod
        out = io_mod.StringIO(newline="")
        writer = csv_mod.writer(out)
        writer.writerow(["suggestion_id", "zotero_key", "field_id", "field_label",
                         "suggested_value", "final_value", "status", "provider", "model",
                         "prompt_version", "created_at", "decided_at", "quote", "pages"])
        for record in records:
            values = [record["id"], record["zotero_key"], record["field_id"],
                      FIELD_WHITELIST.get(record["field_id"], {}).get("label", ""),
                      record["value"], record["final_value"], record["status"],
                      record["provider"], record["model"], record["prompt_version"],
                      record["created_at"], record["decided_at"], record["quote"],
                      "|".join(str(p) for p in record["pages"])]
            # 公式注入防护对齐既有 CSV 先例（report_evidence export）。
            writer.writerow(["'" + str(v) if str(v or "").lstrip().startswith(("=", "+", "-", "@")) else v
                             for v in values])
        return Response(("\ufeff" + out.getvalue()).encode("utf-8"),
                        media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="ai-suggestions.csv"'})

    @app.get("/api/tasks/{task_id}/ai-suggestions/methods-report")
    def methods_report(task_id: str, user: dict = Depends(require_user)) -> dict:
        from coscreen import __version__ as app_version

        info = task_lookup(task_id)
        ctx = _context(task_id, user)
        settings = _load_settings(info)
        with closing(_connect(ctx.actor_db)) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total, SUM(status='adopted') AS adopted, "
                "SUM(status='edited') AS edited, SUM(status='ignored') AS ignored, "
                "SUM(status='pending') AS pending, MIN(created_at) AS first_at, "
                "MAX(created_at) AS last_at FROM ai_suggestions WHERE task_id=? AND actor_id=?",
                (info.task_id, str(user["user_id"]))).fetchone()
            models = [r[0] for r in conn.execute(
                "SELECT DISTINCT model FROM ai_suggestions WHERE task_id=? AND actor_id=? "
                "ORDER BY model", (info.task_id, str(user["user_id"]))).fetchall()]
        total, adopted = int(row["total"] or 0), int(row["adopted"] or 0)
        fields = ", ".join(FIELD_WHITELIST[f]["label"] for f in settings.get("allowed_field_ids") or []) or "（未启用字段）"
        span = f'{row["first_at"]} 至 {row["last_at"]}' if total else "（尚无建议记录）"
        shared_note = ("任务共享钥匙（由任务负责人配置，以任务名义经服务器发出）"
                       if settings.get("key_mode") == "shared" else "成员各自携带钥匙（BYOK）")
        models_note = "；所用模型：" + "、".join(m for m in models if m) if models else ""
        report_zh = (
            f"本段统计为当前登录成员本人的 AI 建议使用记录（按成员分别统计；团队汇总请各成员各自导出）。"
            f"本综述在编码阶段使用了木笔 ReviewFlow（v{app_version}）的 AI 抽取建议功能。"
            f"建议由大语言模型生成（提示词版本 {PROMPT_VERSION}），仅覆盖研究特征类字段：{fields}。"
            f"使用期间：{span}；共生成 {total} 条建议，其中 {adopted} 条被直接采纳、"
            f'{int(row["edited"] or 0)} 条经人工编辑后采纳、{int(row["ignored"] or 0)} 条被忽略。'
            f"密钥模式：{shared_note}{models_note}。每条建议均附原文引文：引文取自全文正文的建议附服务端定位的页码，"
            "仅取自摘要的建议标注为摘要来源（无页码）。服务端核验仅确认引文逐字存在于原文，"
            "不代表核验了建议值本身；无引文或引文未能在原文中核验的建议在服务端被丢弃，"
            "引文过短或超长（按可核验前缀放行）的建议标注为部分核验；"
            "采纳由人工逐条确认，最终编码值均由研究者经既有保存链路写入并计入研究轨迹。"
            "模型输出未经独立验证，仅作辅助参考（验证方式：__________）。")
        report_en = (
            f"The counts below reflect the AI-suggestion usage of the currently signed-in member only "
            f"(statistics are per member; for a team summary, each member exports their own record). "
            f"During the coding stage of this review, the AI-assisted extraction feature of "
            f"ReviewFlow (v{app_version}) was used. Suggestions were generated by a large language "
            f"model (prompt version {PROMPT_VERSION}) and restricted to study-characteristic "
            f"fields: {fields}. Period: {span}; {total} suggestions were generated, of which "
            f'{adopted} were adopted directly, {int(row["edited"] or 0)} adopted after manual '
            f'editing, and {int(row["ignored"] or 0)} dismissed. Key mode: '
            f"{'a task-shared key configured by the task owner' if settings.get('key_mode') == 'shared' else 'bring-your-own-key (BYOK)'}"
            f"{' (models: ' + ', '.join(m for m in models if m) + ')' if models else ''}. "
            "Every suggestion carried a source quote: suggestions quoting the full text carried "
            "server-located page numbers, while quotes located only in the abstract were "
            "labelled as abstract-sourced (no page numbers). Server-side verification only "
            "confirmed that the quote appeared verbatim in the source text and did not validate "
            "the suggested value itself; suggestions without a quote, or whose quote could not "
            "be verified against the source text, were discarded server-side, and overly short "
            "or overlong quotes (accepted on a verifiable prefix) were flagged as partially "
            "verified. Adoption required per-item human confirmation, and "
            "final coding values were saved by researchers through the regular audit-trailed "
            "workflow. Model outputs were not independently validated and served as assistance "
            "only (validation: __________).")
        return {"report_zh": report_zh, "report_en": report_en,
                "counts": {"total": total, "adopted": adopted,
                           "edited": int(row["edited"] or 0), "ignored": int(row["ignored"] or 0),
                           "pending": int(row["pending"] or 0)},
                "prompt_version": PROMPT_VERSION, "app_version": app_version}
