"""R11 · ERIC 教育检索通道 —— 检索预览 / 检索并导入（api.ies.ed.gov 官方 API）。

背景（``out/benchmark/r11_retrieval_datasets_papers.md`` §1.5）：ERIC 官方 API
``https://api.ies.ed.gov/eric/`` 免 key、返回题录+摘要、覆盖 1966 年至今，是
教育/社科综述检索的主通道。本模块把「检索式 → ERIC 题录 → Article 形状 →
合成 RIS 虚拟文件 → 受保护导入管线」接通，让 ERIC 命中的文献与手工导入、
Zotero 直连、PubMed 增量走同一条去重/待审/激活/溯源管线。

治理红线（与 ``ai_suggest_api`` / ``living_review_plugin`` / ``zotero_local_api``
同一口径）：
- **默认关 + 显式手动触发**：本模块不注册任何后台线程、定时器或启动期网络
  调用；仅当用户在前端点「预览命中」或「检索并导入」时，端点才按请求出站。
- **出站只访问声明的官方端点**：URL 恒由常量 :data:`ERIC_API_URL` 拼出，用户
  输入只进入 ``search`` 查询参数，无法把请求引向其他主机。隐私披露：只访问
  ``api.ies.ed.gov``。
- **stdlib urllib**：显式 UA、短超时、单响应读取上限、拒绝跟随 3xx 重定向
  （``_NoRedirectHandler`` 先例 = ``custom_backend/ai_suggest_api.py:301``）。
- **失败优雅降级绝不 500**：不可达/超时/重定向/非 2xx/坏 JSON/坏结构/超限
  一律抛 :class:`EricSearchError`，路由层映射为可读 4xx；空结果在预览里是
  正常 200（命中 0），在导入里是 422 引导而非批次。
- **不直接入库**：导入端点把题录合成 RIS 虚拟文件后调用
  ``app.state.review_imports.upload()``（``review_imports_api.py:74`` 接缝），
  解析/去重/暂存待审/导入历史/来源溯源全部复用既有管线；本模块零数据库写入。

ERIC 响应要点（2026-11 实测，见测试夹具）：
- 成功：``{"response": {"numFound": N, "start": 0, "docs": [...]}}``；
- 检索语法错误：**HTTP 仍为 200**，响应体为
  ``{"error": {"msg": "undefined field doi", "code": 400}}``——必须按错误处理；
- ``doi`` 不是可请求字段（请求它会得到上述错误）；DOI 需从 ``url`` 的
  doi.org/dx.doi.org 链接提取；
- ``author`` 为作者数组（偶见分号串），``description`` 为摘要，
  ``source`` 为期刊/来源名，``publicationdateyear`` 为年份。
"""
from __future__ import annotations

import hashlib
import http.client
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from coscreen.normalize import normalize_authors, normalize_doi, normalize_title

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_PREVIEW_ROWS",
    "DEFAULT_TIMEOUT_SECONDS",
    "ERIC_API_URL",
    "MAX_IMPORT_RECORDS",
    "MAX_PREVIEW_ROWS",
    "MAX_QUERY_CHARS",
    "MAX_RESPONSE_BYTES",
    "PAGE_SIZE",
    "EricSearchError",
    "articles_to_ris",
    "doc_to_article",
    "fetch_eric_import",
    "register_eric_search_routes",
    "search_eric_preview",
]

# ---------------------------------------------------------------------------
# 常量（隐私披露：出站只访问声明的官方端点）
# ---------------------------------------------------------------------------

#: ERIC 官方 API 固定端点（免 key；不接受用户提供的 host/scheme）。
ERIC_API_URL = "https://api.ies.ed.gov/eric/"

#: 显式 UA（ERIC 无强制要求，但显式标识是公共 API 礼仪的一部分）。
USER_AGENT = "ReviewFlow-ERIC/2.1.4 (offline-first; contact: local-user)"

#: 单请求超时（秒）：网络不可达/挂起时尽快失败，端点降级为可读 4xx。
DEFAULT_TIMEOUT_SECONDS = 15.0

#: 单响应读取上限（内存 DoS 防线）。分页 200 条含摘要约 1 MiB，16 MiB 足够
#: 覆盖正常页并挡住异常大响应。
MAX_RESPONSE_BYTES = 16 * 1024 * 1024

#: 每页题录数（ERIC rows 实测支持 1000；取 200 控制单响应体量与失败粒度）。
PAGE_SIZE = 200

#: 预览最多返回的题录数（只读、不入库）。
MAX_PREVIEW_ROWS = 50
DEFAULT_PREVIEW_ROWS = 20

#: 单次「检索并导入」最多题录数。超过则明确 422 引导收窄检索式——绝不
#: 静默截断（截断后把部分结果当完整检索结果入库会污染筛选队列）。
MAX_IMPORT_RECORDS = 500

#: 检索式最大长度（与 living-review 的 PubMed 检索式上限同口径）。
MAX_QUERY_CHARS = 4000

#: 只请求题录转换真正消费的字段。注意 ``doi`` 不是 ERIC 可请求字段：请求它会
#: 返回 ``{"error": {"msg": "undefined field doi"}}``（HTTP 200），故 DOI 从
#: ``url`` 的 doi.org 链接提取。
ERIC_FIELDS = ",".join((
    "id",
    "title",
    "author",
    "description",
    "publicationdateyear",
    "source",
    "url",
    "publicationtype",
))

#: 孤立代理字符（U+D800–U+DFFF）：JSON 转义 ``"\ud800"`` 是合法 JSON，但
#: Python 得到不能 UTF-8 编码的 str——进 RIS 合成/响应序列化会抛
#: UnicodeEncodeError → 500。洗成 U+FFFD（同 zotero_local_api 口径）。
_LONE_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")

#: 与 security._UNSAFE_NAME_RE 同口径的非安全文件名字符集。
_UNSAFE_FILENAME_RE = re.compile(r"[\\/:*?\"<>|\x00-\x1f\x7f]")

#: RIS 文本里不能出现的 C0 控制字符（NUL 会被上传校验判成二进制伪装；其余
#: 控制符会破坏 rispy 行文法）。制表/换行/回车按空白折叠，不在此列。
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_YEAR_RE = re.compile(r"^(1[5-9]\d\d|20\d\d|21\d\d)$")


class EricSearchError(RuntimeError):
    """ERIC 检索失败（入参 / 网络 / HTTP / 响应结构 / 命中量）。

    路由层捕获后映射为可读 4xx（绝不 500）；``code`` 供前端分流提示。
    """

    def __init__(self, message: str, *, code: str = "eric_unavailable",
                 **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.extra = extra


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """拒绝跟随 3xx 重定向（先例：``custom_backend/ai_suggest_api.py:301``）。

    本模块只访问声明的 ``api.ies.ed.gov`` 端点；任何重定向都视为错误，防止
    请求被引导到未声明的主机。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


_OPENER = urllib.request.build_opener(_NoRedirectHandler)


# ---------------------------------------------------------------------------
# 出站客户端（测试经 url_opener=... 注入确定性夹具）
# ---------------------------------------------------------------------------

def _open(url: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> bytes:
    """GET ``url``：显式 UA + 拒绝重定向 + 读取上限，故障统一抛 EricSearchError。

    ``url`` 恒由 :data:`ERIC_API_URL` 加查询串拼出，绝不接受外部 host。
    """
    request = urllib.request.Request(  # noqa: S310  URL 恒为声明的官方常量
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        method="GET")
    try:
        with _OPENER.open(request, timeout=timeout) as response:  # noqa: S310
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise EricSearchError(
                f"ERIC 返回 HTTP {exc.code} 重定向，已拒绝（只访问声明的 "
                "api.ies.ed.gov 端点）。", code="eric_redirect") from None
        raise EricSearchError(
            f"ERIC 返回 HTTP {exc.code}（响应详情不回显）。",
            code="eric_http_error") from None
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None) or "连接失败"
        if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
            raise EricSearchError(
                f"ERIC 请求超时（{int(timeout)}s）。", code="eric_timeout") from None
        raise EricSearchError(
            f"ERIC 不可达：{reason}", code="eric_unreachable") from None
    except (TimeoutError, OSError) as exc:
        if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower():
            raise EricSearchError(
                f"ERIC 请求超时（{int(timeout)}s）。",
                code="eric_timeout") from None
        raise EricSearchError(
            f"ERIC 连接中断：{type(exc).__name__}。",
            code="eric_unreachable") from None
    except http.client.HTTPException as exc:
        raise EricSearchError(
            f"ERIC 协议错误：{type(exc).__name__}。",
            code="eric_bad_response") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise EricSearchError(
            f"ERIC 响应超过 {MAX_RESPONSE_BYTES // (1024 * 1024)} MiB 读取上限。",
            code="eric_response_too_large")
    return raw


def _wrap_opener(open_: Callable[..., bytes], timeout: float) -> Callable[[str], bytes]:
    """把任意 opener（含测试注入的假 opener）的故障统一成 EricSearchError。

    注入契约：``open_(url, timeout) -> bytes``。opener 抛任意异常（urllib 错误、
    超时、注入桩的 TypeError 等）或返回非 bytes，一律是可捕获的
    :class:`EricSearchError`，绝不硬逃逸成 500。
    """
    def _fetch(url: str) -> bytes:
        try:
            payload = open_(url, timeout)
        except EricSearchError:
            raise
        except urllib.error.HTTPError as exc:
            # 注入桩也可能直接模拟上游状态码；与生产 _open 同一分流。
            if 300 <= exc.code < 400:
                raise EricSearchError(
                    f"ERIC 返回 HTTP {exc.code} 重定向，已拒绝。",
                    code="eric_redirect") from exc
            raise EricSearchError(
                f"ERIC 返回 HTTP {exc.code}（响应详情不回显）。",
                code="eric_http_error") from exc
        except (TimeoutError, OSError) as exc:
            # 注入桩的 socket.timeout/TimeoutError 与生产同语义（不是「不可达」）。
            if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower():
                raise EricSearchError(
                    f"ERIC 请求超时（{int(timeout)}s）。",
                    code="eric_timeout") from exc
            raise EricSearchError(
                f"ERIC 不可达：{exc}", code="eric_unreachable") from exc
        except http.client.HTTPException as exc:
            raise EricSearchError(
                f"ERIC 协议错误：{type(exc).__name__}。",
                code="eric_bad_response") from exc
        except Exception as exc:  # noqa: BLE001  网络包装层：任意 opener 故障归一
            raise EricSearchError(
                f"ERIC 不可达：{exc}", code="eric_unreachable") from exc
        if not isinstance(payload, (bytes, bytearray)):
            raise EricSearchError(
                f"url_opener 返回 {type(payload).__name__}，预期 bytes。",
                code="eric_bad_response")
        return bytes(payload)

    return _fetch


def _normalize_query(query: object) -> str:
    """检索式校验与空白归一；非法输入抛 ``eric_bad_query``（路由 400）。"""
    if not isinstance(query, str):
        raise EricSearchError(
            f"检索式必须为字符串，得到 {type(query).__name__}。",
            code="eric_bad_query")
    # 孤立代理字符无法 UTF-8 编码（urlencode/文件名哈希都会抛 UnicodeEncodeError）；
    # C0 控制符会污染请求与虚拟文件名。入口统一洗一次，端点绝不因此 500。
    cleaned = _LONE_SURROGATE_RE.sub("\ufffd", query)
    text = " ".join(_CONTROL_RE.sub("", cleaned).split())
    if not text:
        raise EricSearchError("检索式不能为空。", code="eric_bad_query")
    if len(text) > MAX_QUERY_CHARS:
        raise EricSearchError(
            f"检索式超过 {MAX_QUERY_CHARS} 字符上限。", code="eric_bad_query")
    return text


def _api_message(payload: dict) -> str:
    """从 ERIC 的 ``error`` 对象提取可读消息（截断、洗代理字符）。"""
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("msg")
        if isinstance(message, str) and message.strip():
            return _LONE_SURROGATE_RE.sub("\ufffd", " ".join(message.split()))[:200]
    return "unknown error"


def _request_page(query: str, start: int, rows: int, *,
                  fetch: Callable[[str], bytes]) -> tuple[int, list[dict]]:
    """请求一页，返回 ``(numFound, docs)``；结构异常统一抛 EricSearchError。"""
    params = urllib.parse.urlencode({
        "search": query,
        "format": "json",
        "rows": rows,
        "start": start,
        "fields": ERIC_FIELDS,
    })
    raw = fetch(f"{ERIC_API_URL}?{params}")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise EricSearchError("ERIC 响应不是合法 JSON。",
                              code="eric_bad_response") from exc
    if not isinstance(payload, dict):
        raise EricSearchError("ERIC 响应不是 JSON 对象。", code="eric_bad_response")
    # 语法/字段错误也是 HTTP 200 + {"error": {...}}（实测：doi 不是可请求字段）。
    if payload.get("error"):
        raise EricSearchError(
            f"ERIC 拒绝了检索式：{_api_message(payload)}",
            code="eric_query_error")
    response = payload.get("response")
    if not isinstance(response, dict):
        raise EricSearchError("ERIC 响应缺少 response 对象。",
                              code="eric_bad_response")
    docs = response.get("docs")
    total = response.get("numFound")
    if isinstance(total, bool):
        raise EricSearchError("ERIC 响应的 numFound 非法。", code="eric_bad_response")
    try:
        total = int(total)
    except (TypeError, ValueError):
        raise EricSearchError("ERIC 响应的 numFound 非法。",
                              code="eric_bad_response") from None
    if total < 0 or not isinstance(docs, list):
        raise EricSearchError("ERIC 响应结构非法（numFound/docs）。",
                              code="eric_bad_response")
    if any(not isinstance(doc, dict) for doc in docs):
        raise EricSearchError("ERIC 响应的 docs 含非对象条目。",
                              code="eric_bad_response")
    return total, docs


# ---------------------------------------------------------------------------
# 题录转换（纯函数，无网络）
# ---------------------------------------------------------------------------

def _one_line(value: object) -> str:
    """压成单行（RIS 生成器与 rispy 续行文法的最小交集）。

    同时洗掉孤立代理字符（UUID 转义）与 C0 控制字符：前者无法 UTF-8 编码，
    后者会让上传校验把虚拟文件误判成二进制。
    """
    if value is None:
        return ""
    text = _LONE_SURROGATE_RE.sub("\ufffd", str(value))
    text = _CONTROL_RE.sub("", text)
    return " ".join(text.split())


def _first_text(value: object) -> str:
    """ERIC 字段可能是标量或数组：取首个非空值并压成单行。"""
    if isinstance(value, (list, tuple)):
        for item in value:
            text = _one_line(item)
            if text:
                return text
        return ""
    return _one_line(value)


def _author_string(value: object) -> str:
    """ERIC 作者 → ``"A; B"``。

    ERIC 通常返回作者数组；也兼容分号串（ERIC 导出/旧记录）。数组元素若自身
    含分号也拆开——最终交给 :func:`normalize_authors` 统一清洗。
    """
    raw: list[str] = []
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, str):
                raw.extend(item.split(";"))
    elif isinstance(value, str):
        raw = value.split(";")
    return normalize_authors(raw)


def _year(value: object) -> int | None:
    """ERIC ``publicationdateyear`` → int；异常形态宁缺毋错（返回 None）。"""
    text = _one_line(value)
    if not text:
        return None
    if _YEAR_RE.match(text):
        return int(text)
    return None


def _doi_from_doc(doc: dict) -> str:
    """从 ``url`` 的 doi.org/dx.doi.org 链接提取 DOI（``doi`` 不是可请求字段）。"""
    direct = doc.get("doi")
    if isinstance(direct, str) and direct.strip():
        return normalize_doi(_one_line(direct))
    raw_url = _one_line(doc.get("url"))
    if not raw_url:
        return ""
    lowered = raw_url.lower()
    if lowered.startswith("doi:"):
        return normalize_doi(raw_url)
    try:
        host = (urllib.parse.urlsplit(raw_url).hostname or "").lower()
    except ValueError:
        return ""
    if host in {"doi.org", "dx.doi.org", "www.doi.org"}:
        return normalize_doi(raw_url)
    return ""


def _ris_type(doc: dict) -> str:
    """ERIC ``publicationtype`` → RIS TY（未识别的类型按 GEN 泛型导入）。"""
    types = doc.get("publicationtype")
    if isinstance(types, str):
        types = [types]
    joined = " ".join(str(item).lower() for item in (types or []))
    if "journal article" in joined:
        return "JOUR"
    if "book" in joined:
        return "BOOK"
    if "dissertation" in joined or "thesis" in joined:
        return "THES"
    if "conference" in joined:
        return "CONF"
    if "report" in joined:
        return "RPRT"
    return "GEN"


def doc_to_article(doc: dict) -> dict | None:
    """ERIC 题录 → Article 字段形状的 dict；无稳定 ID 的记录返回 None。

    映射（蓝图逐字段）：
    - ``id`` → ``zotero_key`` / RIS ``ID``（ERIC ID 全局稳定，如 ``EJ1482166``）；
    - ``title`` → ``title``（``normalize_title`` 同口径）；
    - ``author`` → ``authors``（数组或分号串，``normalize_authors``）；
    - ``description`` → ``abstract``；
    - ``publicationdateyear`` → ``year``；
    - ``source`` → ``journal``；
    - ``url`` 的 doi.org 链接 → ``doi``（``doi`` 字段不可请求）；
    - 规范落地页 ``https://eric.ed.gov/?id=<id>`` → ``url``；
    - ``publicationtype`` → ``item_type``（RIS TY）。
    """
    if not isinstance(doc, dict):
        return None
    eric_id = _one_line(doc.get("id"))
    # 自由文本/纯序号不能当全局键（与 csv_parser.is_global_stable_identifier 同口径）
    if not eric_id or any(ch.isspace() for ch in eric_id):
        return None
    if eric_id.isdigit() and len(eric_id) <= 2:
        return None
    return {
        "zotero_key": eric_id,
        "item_type": _ris_type(doc),
        "title": normalize_title(_one_line(doc.get("title"))),
        "authors": _author_string(doc.get("author")),
        "journal": _first_text(doc.get("source")),
        "year": _year(doc.get("publicationdateyear")),
        "doi": _doi_from_doc(doc),
        "abstract": _one_line(doc.get("description")),
        "url": "https://eric.ed.gov/?id=" + urllib.parse.quote(eric_id, safe=""),
        "source_format": "eric",
        "eric_id": eric_id,
    }


def articles_to_ris(articles: list[dict]) -> str:
    """Article 形状 dict 列表 → RIS 文本（虚拟导入文件）。

    只写 ris_parser 会读回的标签：``ID`` 携带 ERIC ID 作为全局稳定来源键，
    使同一 ERIC 记录在去重层按精确键识别为既有文献。多行文本折叠为单行
    （标题/摘要/期刊），与 ``_pubmed_updates_to_ris`` 同一文法交集。
    """
    lines: list[str] = []
    for article in articles:
        if not isinstance(article, dict):
            continue
        key = _one_line(article.get("zotero_key"))
        if not key:
            continue
        lines.append("TY  - " + (_one_line(article.get("item_type")) or "GEN"))
        title = _one_line(article.get("title"))
        if title:
            lines.append(f"TI  - {title}")
        for author in str(article.get("authors") or "").split(";"):
            author = _one_line(author)
            if author:
                lines.append(f"AU  - {author}")
        journal = _one_line(article.get("journal"))
        if journal:
            lines.append(f"JO  - {journal}")
        year = article.get("year")
        try:
            year = int(year) if year else None
        except (TypeError, ValueError):
            year = None
        if year:
            lines.append(f"PY  - {year}")
        doi = _one_line(article.get("doi"))
        if doi:
            lines.append(f"DO  - {doi}")
        abstract = _one_line(article.get("abstract"))
        if abstract:
            lines.append(f"AB  - {abstract}")
        url = _one_line(article.get("url"))
        if url:
            lines.append(f"UR  - {url}")
        lines.append(f"ID  - {key}")
        lines.append("ER  - ")
    return "\n".join(lines) + ("\n" if lines else "")


def _truncate_utf8(text: str, limit: int) -> str:
    """按 UTF-8 **字节** 截断（不切断多字节字符）。

    文件名长度上限按字节计（ext4 单名 255 字节；Windows 按 UTF-16 单元）。
    只按字符截断时，60 个四字节字符（emoji/部分符号）会得到 240+ 字节的
    文件名，落盘 ENAMETOOLONG → 500。这里保证可见部分 ≤ ``limit`` 字节。
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", "ignore")


def _provenance_name(query: str) -> str:
    """检索式 → 可完整保留的虚拟文件名（来源溯源）。

    文件名 = 来源身份的一部分（``import_sources.detect`` 的
    ``identity='filename:…'``）。带检索式摘要哈希：同一检索式重复导入复用
    既有源记录；不同检索式不会互相冒充同一来源。可见部分按 UTF-8 字节
    截断（120 字节），整个文件名恒 < 255 字节，任何语言的检索式都能落盘。
    """
    squashed = " ".join(
        _LONE_SURROGATE_RE.sub("\ufffd", str(query or "")).split())
    cleaned = _UNSAFE_FILENAME_RE.sub("-", squashed).strip().strip(".") or "query"
    digest = hashlib.sha1(squashed.encode("utf-8")).hexdigest()[:8]
    visible = _truncate_utf8(cleaned, 120)
    return f"ERIC 检索 {visible} [{digest}].ris"


# ---------------------------------------------------------------------------
# 检索（纯函数 + 注入 opener）
# ---------------------------------------------------------------------------

def _validate_rows(value: object, *, default: int, minimum: int, maximum: int,
                   name: str = "rows") -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise EricSearchError(f"{name} 必须为整数。", code="eric_bad_query")
    if value < minimum or value > maximum:
        raise EricSearchError(
            f"{name} 必须在 {minimum}–{maximum} 之间。", code="eric_bad_query")
    return value


def search_eric_preview(
    query: str,
    rows: int = DEFAULT_PREVIEW_ROWS,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    url_opener: Callable[..., bytes] | None = None,
) -> dict:
    """检索预览：返回命中总数 + 前 ``rows`` 条题录（只读，不入库）。

    空结果不是错误：``total_hits == 0``、``records == []`` 的 200 响应。
    """
    text = _normalize_query(query)
    rows = _validate_rows(rows, default=DEFAULT_PREVIEW_ROWS,
                          minimum=1, maximum=MAX_PREVIEW_ROWS)
    fetch = _wrap_opener(url_opener or _open, timeout)
    total, docs = _request_page(text, 0, rows, fetch=fetch)
    if total and not docs:
        # 上游声称有命中却一页都不给：宁可报坏响应，不把「0 条可见」当预览。
        raise EricSearchError("ERIC 声称有命中但未返回任何题录。",
                              code="eric_bad_response")
    records = [article for article in (doc_to_article(doc) for doc in docs) if article]
    if docs and not records:
        # 上游给出 docs 却一条都转不出稳定题录：宁可报坏响应，不假装 0 命中。
        raise EricSearchError("ERIC 返回的题录均缺少稳定标识。",
                              code="eric_bad_response")
    return {
        "source": "eric",
        "endpoint": ERIC_API_URL,
        "query": text,
        "total_hits": total,
        "returned": len(records),
        "records": records,
    }


def fetch_eric_import(
    query: str,
    limit: int = MAX_IMPORT_RECORDS,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    url_opener: Callable[..., bytes] | None = None,
) -> dict:
    """检索并整备导入：分页取回全部命中（上限 ``limit``）。

    - ``numFound > limit``：抛 ``eric_too_many_hits``（附 total_hits/limit），
      由路由 422 引导收窄检索式——绝不静默截断后当完整结果入库；
    - 空结果：返回 ``records=[]``（路由 422 引导，不建批次）；
    - 分页取回数与 ``numFound`` 不一致：抛 ``eric_bad_response`` /
      ``eric_incomplete``，绝不把半截结果交给导入管线。
    """
    text = _normalize_query(query)
    limit = _validate_rows(limit, default=MAX_IMPORT_RECORDS,
                           minimum=1, maximum=MAX_IMPORT_RECORDS, name="limit")
    fetch = _wrap_opener(url_opener or _open, timeout)
    total, first_docs = _request_page(text, 0, min(PAGE_SIZE, limit), fetch=fetch)
    if total > limit:
        raise EricSearchError(
            f"ERIC 命中 {total} 条，超过单次导入上限 {limit} 条。",
            code="eric_too_many_hits", total_hits=total, limit=limit)
    raw_docs = list(first_docs)
    while raw_docs and len(raw_docs) < total:
        # 页大小不超过剩余请求量；ERIC 的 start 为 0 基偏移（实测）。
        start = len(raw_docs)
        _, docs = _request_page(
            text, start, min(PAGE_SIZE, limit - start), fetch=fetch)
        if not docs:
            break
        raw_docs.extend(docs)
        if start + len(docs) >= limit:
            break
    if total and len(raw_docs) < total:
        raise EricSearchError(
            f"ERIC 返回的题录不完整（{len(raw_docs)}/{total}），已停止导入。",
            code="eric_incomplete", total_hits=total)
    records = [article for article in (doc_to_article(doc) for doc in raw_docs) if article]
    if len(records) != total:
        raise EricSearchError(
            f"ERIC 题录转换不完整（{len(records)}/{total}），已停止导入。",
            code="eric_incomplete", total_hits=total)
    return {
        "source": "eric",
        "endpoint": ERIC_API_URL,
        "query": text,
        "total_hits": total,
        "records": records,
    }


# ---------------------------------------------------------------------------
# 受保护导入接缝（UploadFile 形状的最小虚拟文件）
# ---------------------------------------------------------------------------

class _VirtualRisUpload:
    """UploadFile 形状的最小虚拟文件（filename + async read）。

    ``review_imports.upload()`` → ``_parse_upload_to_articles`` 只消费这两个
    成员（main.py:2192-2197）；经它落盘 tmpdir 后由 parse_file 按 .ris 解析。
    """

    def __init__(self, filename: str, payload: bytes) -> None:
        self.filename = filename
        self._payload = payload

    async def read(self) -> bytes:
        return self._payload


# ---------------------------------------------------------------------------
# 路由注册（依赖注入与 zotero_local_api.register_zotero_local_routes 同款）
# ---------------------------------------------------------------------------

class _EricBody(BaseModel):
    """两端点共用请求体：检索式在 Pydantic 校验**之前**洗掉孤立代理字符。

    Pydantic v2 对 ``"\\ud800"`` 会报 ``string_unicode``；全局 422 处理器回显
    input 时再 UTF-8 编码就会 500（main.py:816）。在 before-validator 里先洗，
    让「合法 JSON 转义」不会变成 500。
    """

    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)

    @field_validator("query", mode="before")
    @classmethod
    def _sanitize_query(cls, value: object) -> object:
        if isinstance(value, str):
            return _LONE_SURROGATE_RE.sub("\ufffd", value)
        return value


class EricSearchBody(_EricBody):
    rows: int = Field(default=DEFAULT_PREVIEW_ROWS, ge=1, le=MAX_PREVIEW_ROWS)


class EricImportBody(_EricBody):
    limit: int = Field(default=MAX_IMPORT_RECORDS, ge=1, le=MAX_IMPORT_RECORDS)


#: 入参类错误 → 4xx；网络/上游类 → 409（可重试或改走手工导入）。
_CLIENT_ERROR_CODES = {"eric_bad_query": 400}
_QUERY_ERROR_CODES = {"eric_query_error": 422}


def register_eric_search_routes(app, require_user, task_lookup,
                                task_visible=None) -> None:
    """注册 ERIC 两端点（检索预览 / 检索并导入）。

    ``task_visible`` 与 Zotero W1 同款：每个端点先判任务可见性；生产注册行
    恒传 ``_task_visible_to``。
    """

    def _authorize(task_id: str, user: dict):
        info = task_lookup(task_id)
        if task_visible is not None and not task_visible(info, user):
            raise HTTPException(403, "Task access denied.")
        return info

    def _error(exc: EricSearchError, task_id: str) -> HTTPException:
        """EricSearchError → 可读 4xx（绝不 500），错误体供前端分流。"""
        if exc.code in _CLIENT_ERROR_CODES:
            status = _CLIENT_ERROR_CODES[exc.code]
        elif exc.code in _QUERY_ERROR_CODES:
            status = _QUERY_ERROR_CODES[exc.code]
        elif exc.code == "eric_too_many_hits":
            status = 422
        elif exc.code == "eric_no_results":
            status = 422
        else:
            status = 409
        message = _LONE_SURROGATE_RE.sub("\ufffd", str(exc))[:300]
        detail = {
            "code": exc.code,
            "message": message,
            "endpoint": ERIC_API_URL,
            "import_url": f"/api/tasks/{task_id}/import",
            "review_url": f"/api/tasks/{task_id}/import-batches",
        }
        if exc.code in {"eric_too_many_hits"}:
            detail.update({key: exc.extra[key]
                           for key in ("total_hits", "limit") if key in exc.extra})
            detail["hint"] = "narrow_query"
        elif exc.code in {"eric_unreachable", "eric_timeout", "eric_redirect",
                          "eric_http_error", "eric_bad_response",
                          "eric_response_too_large", "eric_incomplete",
                          "eric_unavailable"}:
            detail["hint"] = "retry_or_manual_import"
        elif exc.code == "eric_query_error":
            detail["hint"] = "fix_query"
        elif exc.code == "eric_no_results":
            detail["hint"] = "change_query"
        return HTTPException(status, detail)

    @app.post("/api/tasks/{task_id}/eric/search")
    async def eric_search(task_id: str, body: EricSearchBody,
                          user: dict = Depends(require_user)) -> dict:
        """检索预览（显式手动触发；只读，不写任何数据、不入库）。"""
        _authorize(task_id, user)
        try:
            return await _run_in_threadpool(
                search_eric_preview, body.query, body.rows)
        except EricSearchError as exc:
            raise _error(exc, task_id) from None
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001 —— never-500 最后一道闸门
            logger.exception("eric/search 未预期的失败（已降级为 409）")
            raise _error(EricSearchError(
                "检索 ERIC 时发生未预期的错误，请稍后重试或改用导出文件导入。",
                code="eric_unavailable"), task_id) from None

    @app.post("/api/tasks/{task_id}/eric/import")
    async def eric_import(task_id: str, body: EricImportBody,
                          screener: str | None = Query(default=None),
                          user: dict = Depends(require_user)) -> dict:
        """检索并导入：ERIC 题录 → 合成 RIS 虚拟文件 → 受保护导入管线。

        写库只经 ``app.state.review_imports.upload()``（去重/待审/激活/溯源
        全部复用）；本模块零数据库写入。联网前先做写入授权，未授权用户不会
        触发任何出站。
        """
        _authorize(task_id, user)
        # 与 check-now 同口径：先按受保护导入的写权限授权，再联网。
        app.state.review_imports.authorize(task_id, user, screener)
        try:
            fetched = await _run_in_threadpool(
                fetch_eric_import, body.query, body.limit)
        except EricSearchError as exc:
            raise _error(exc, task_id) from None
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001 —— never-500 最后一道闸门
            logger.exception("eric/import 检索阶段未预期的失败（已降级为 409）")
            raise _error(EricSearchError(
                "检索 ERIC 时发生未预期的错误，请稍后重试或改用导出文件导入。",
                code="eric_unavailable"), task_id) from None

        articles = fetched["records"]
        if not articles:
            raise _error(EricSearchError(
                f"ERIC 检索命中 0 条（检索式：{fetched['query']}）。",
                code="eric_no_results", total_hits=0), task_id)
        name = _provenance_name(fetched["query"])
        try:
            upload = _VirtualRisUpload(name, articles_to_ris(articles).encode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            logger.warning("eric/import RIS 合成失败（已降级为 409）：%r", exc)
            raise _error(EricSearchError(
                "ERIC 返回的题录无法转换为导入文件（数据含非法字符）。",
                code="eric_bad_response"), task_id) from None
        # 受保护导入接缝（review_imports_api.py:74）：单文件、来源 auto、无进度 job。
        # source_id 取舍：与 Zotero 直连一致——没有逐文件挑来源的交互面，交给
        # 既有管线按指纹/批次判定（'auto' 语义）。
        try:
            result = await app.state.review_imports.upload(
                task_id, user, [upload], batch=False, source_ids=None,
                job_id=None, screener=screener)
        except HTTPException:
            # 管线自身的可读 4xx（未授权/只读/解析失败等）原样透传。
            raise
        except Exception:  # noqa: BLE001 —— never-500 最后一道闸门
            logger.exception("eric/import 导入管线未预期的失败（已降级为 409）")
            raise _error(EricSearchError(
                "ERIC 题录写入导入管线时发生未预期的错误，请稍后重试或改用"
                "导出文件导入。", code="eric_unavailable"), task_id) from None
        result["eric"] = {
            "endpoint": fetched["endpoint"],
            "query": fetched["query"],
            "total_hits": fetched["total_hits"],
            "fetched": len(articles),
            "virtual_file": name,
        }
        return result


async def _run_in_threadpool(func: Callable[..., Any], *args: Any) -> Any:
    """延迟导入 starlette 的线程池工具（避免模块顶层额外依赖面）。"""
    from starlette.concurrency import run_in_threadpool

    return await run_in_threadpool(func, *args)
