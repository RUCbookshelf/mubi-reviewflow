"""W1 · B5 Zotero 7 本地 API 直连路由 —— 连接测试 / 集合浏览 / 导入所选集合。

治理红线（空白格计划 W1，与 ai_suggest_api/living_review_plugin 同一口径）：
- **默认关 + 显式手动触发**：本模块不发起任何后台/定时/启动期网络请求；只有用户
  在导入页显式点击（连接测试 / 选择集合 / 导入所选集合）时，端点才按请求出站。
- **回环限定**：出站唯一地址是常量 ``http://127.0.0.1:23119``（Zotero 7 内置本地
  API，无需钥匙）。URL 恒由常量拼出，用户输入只允许进入已校验的路径段
  （集合键 ``[A-Za-z0-9]{1,32}``）；拒绝跟随重定向（_NoRedirectHandler 先例 =
  custom_backend/ai_suggest_api.py:301）——隐私披露：只访问声明的回环端点，
  绝不触碰 Zotero 云。
- **stdlib urllib**：显式 UA、短超时（5s）、单响应 8 MiB 读取上限。
- **失败优雅降级绝不 500**：不可达/超时/坏响应/重定向统一抛 :class:`ZoteroLocalError`，
  路由层映射 409（引导修复的 4xx，同 living-review check-now 409 引导先例）。

导入链路（去重/待审/确认/溯源全管线白得）：集合条目 → 合成 RIS 虚拟文件
（``ID`` 标签携带 Zotero 条目键，与 coscreen.parsers.ris_parser 的全局稳定
标识口径对齐）→ ``app.state.review_imports.upload()``（review_imports_api.py:74
接缝，UploadFile 形状 :88）走受保护导入：解析/去重/暂存待审/导入历史/来源
溯源全部复用既有管线，本模块零数据库写入。
"""
from __future__ import annotations

import asyncio
import http.client
import json
import logging
import re
import time
import urllib.error
import urllib.request

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

__all__ = [
    "BASE_URL",
    "LOOPBACK_HOST",
    "LOOPBACK_PORT",
    "MAX_COLLECTIONS_PER_IMPORT",
    "ZoteroLocalError",
    "fetch_collection_items",
    "fetch_collections",
    "item_to_ris_lines",
    "items_to_ris_bytes",
    "register_zotero_local_routes",
]

# ---------------------------------------------------------------------------
# 常量（隐私披露：出站只访问声明的本机回环端点）
# ---------------------------------------------------------------------------

#: Zotero 7 本地 API 固定回环地址（不需要 API 钥匙；不是 Zotero 云端地址）。
LOOPBACK_HOST = "127.0.0.1"
LOOPBACK_PORT = 23119
BASE_URL = f"http://{LOOPBACK_HOST}:{LOOPBACK_PORT}"

USER_AGENT = "ReviewFlow-ZoteroLocal/2.1.4"
DEFAULT_TIMEOUT_SECONDS = 5.0              # 回环不可达/挂起时尽快失败，端点优雅降级
MAX_RESPONSE_BYTES = 8 * 1024 * 1024       # 单次响应读取上限（内存 DoS 防线）
PAGE_LIMIT = 100                            # Zotero API 单页条目上限
MAX_PAGES_PER_REQUEST = 50                  # 单集合分页上限（≤ 5000 条）
MAX_COLLECTIONS_PER_IMPORT = 20             # 与受保护导入「1-20 个文件」上限对齐
MAX_ITEMS_PER_IMPORT = 5000                 # 单次导入条目上限（多集合合计）

#: 响应 JSON 允许的嵌套深度上限。真实 Zotero 条目 ≤ 6 层；此上限只为把
#: 「深层嵌套 → RecursionError」这条未捕获异常路径挡在解析之后（见
#: :func:`_sanitize_payload`）。``json.loads`` 自身的递归在 ~1000 层抛
#: RecursionError（实测 CPython 3.11：993 层可解析、1000 层抛），两处合计
#: 覆盖「解析期爆栈」与「解析后递归消费（序列化/拼接）爆栈」。
MAX_PAYLOAD_DEPTH = 32

#: 孤立代理字符（U+D800–U+DFFF 整个代理区）。JSON 里 ``"\ud800"`` 是**合法
#: JSON**（RFC 8259 允许转义任意 \uXXXX），但 Python 得到一个不能 UTF-8 编码的
#: str——落到 RIS 合成 / 响应序列化就抛 UnicodeEncodeError → 500。洗成 U+FFFD。
#: （合法配对代理会被 ``raw.decode("utf-8", "replace")`` 之前的 UTF-8 解码还原
#: 成真实码点，不会命中本正则；只有真正无法编码的孤立项才会被替换。）
_LONE_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")

#: 跳过的条目类型（/items/top 正常不返回；防御性双保险）。
_SKIPPED_ITEM_TYPES = {"note", "attachment"}

#: Zotero itemType → RIS TY（未列出的类型按 GEN 泛型导入）。
_RIS_TYPE_BY_ITEM_TYPE = {
    "journalArticle": "JOUR",
    "book": "BOOK",
    "bookSection": "CHAP",
    "conferencePaper": "CONF",
    "report": "RPRT",
    "thesis": "THES",
    "webpage": "ELEC",
}

_YEAR_RE = re.compile(r"(1[5-9]\d\d|20\d\d)")
_COLLECTION_KEY_RE = re.compile(r"[A-Za-z0-9]{1,32}")


class ZoteroLocalError(RuntimeError):
    """Zotero 本地 API 失败（不可达 / 超时 / 重定向 / HTTP 错误 / 坏响应）。

    路由层捕获后映射为 409 引导（绝不 500）；``code`` 供前端分流提示。
    """

    def __init__(self, message: str, *, code: str = "zotero_local_error") -> None:
        super().__init__(message)
        self.code = code


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """拒绝跟随 3xx 重定向（先例 custom_backend/ai_suggest_api.py:301）。

    本模块只访问声明的回环端点；任何重定向都视为错误，防止被引导到
    未声明的地址。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


#: 拒绝跟随 3xx（_NoRedirectHandler）+ **显式空 ProxyHandler**：urllib 默认读
#: http_proxy/HTTP_PROXY 环境变量，企业/校园网普遍预设，会把「只访问本机回环」的
#: 披露变成假话——请求被整包发往代理主机（先例 build/launcher.py:127 对同一回环
#: 探测就是这么做的）。空 ProxyHandler({}) 关掉代理发现，URL 恒直连 127.0.0.1。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirectHandler)


# ---------------------------------------------------------------------------
# 出站客户端（测试经 mock.patch.object(zotero_local_api, "_http_get"/"_fetch_json") 注入）
# ---------------------------------------------------------------------------

def _http_get(path: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> bytes:
    """GET ``BASE_URL + path``，任何网络层故障统一包装为 ZoteroLocalError。

    回环限定：``path`` 只允许站内路径（/api/…、无 scheme/host），完整 URL 恒由
    模块常量拼出——用户输入无法把请求引向 127.0.0.1:23119 之外的地址。
    """
    if (not path.startswith("/api/") or "://" in path or "\\" in path
            or ".." in path):
        raise ZoteroLocalError("非法的本地 API 路径。", code="zotero_bad_path")
    request = urllib.request.Request(  # noqa: S310  URL 恒为声明的回环常量
        BASE_URL + path,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        method="GET")
    try:
        with _OPENER.open(request, timeout=timeout) as response:  # noqa: S310
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise ZoteroLocalError(
                f"Zotero 本地 API 返回 HTTP {exc.code} 重定向，已拒绝"
                "（只访问声明的回环端点，不跟随跳转）。",
                code="zotero_redirect") from None
        raise ZoteroLocalError(
            f"Zotero 本地 API HTTP {exc.code}。", code="zotero_http_error") from None
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None) or "连接失败"
        raise ZoteroLocalError(
            f"本机 Zotero 不可达（{BASE_URL}）：{reason}",
            code="zotero_unreachable") from None
    except http.client.HTTPException as exc:
        # 复审 R4#1：协议层异常（BadStatusLine/InvalidURL/LineTooLong/
        # UnknownProtocol/IncompleteRead）**不是 OSError 子类**，此前漏捕会冒泡成
        # 500。23119 是本机任意进程可抢占的固定端口，非 Zotero 时极易命中。
        # 旁证：ai_suggest_api.py:353 的 MM-09 同一缺陷已按此法修过。
        raise ZoteroLocalError(
            f"Zotero 本地 API 协议层错误：{type(exc).__name__}",
            code="zotero_bad_response") from None
    except (TimeoutError, OSError) as exc:
        raise ZoteroLocalError(
            f"连接本机 Zotero 超时（{int(timeout)}s）：{type(exc).__name__}",
            code="zotero_timeout") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ZoteroLocalError(
            f"Zotero 本地 API 响应超过 {MAX_RESPONSE_BYTES // (1024 * 1024)} MiB"
            " 读取上限。", code="zotero_response_too_large") from None
    return raw


def _sanitize_payload(value: object, depth: int = 0) -> object:
    """把解析后的 JSON 洗成「任何下游都吃得住」的形状（绝不抛错）。

    复审 R8#W1-1（本组赛道的两条未捕获异常路径，均与 docs/API_REFERENCE.md
    的 never-500 契约矛盾）：

    1. **深层嵌套**：``json.loads`` 对 ≥1000 层嵌套抛 ``RecursionError``——
       它**不是 ValueError 子类**，旧的 ``except ValueError`` 漏捕 → 500。
       实测 CPython 3.11：993 层可解析、1000 层抛（本模块钉子里复现）。
    2. **孤立代理字符**：JSON 里 ``"\\ud800"`` 是合法转义，Python 得到不能
       UTF-8 编码的 str；落到 :func:`items_to_ris_bytes` 的 ``.encode("utf-8")``
       或响应序列化就抛 ``UnicodeEncodeError``（也不是 ValueError）→ 500。
       **实测**：集合名 / 条目 title / abstractNote / creators[*].name 任一处
       含代理字符，旧代码三端点依次复现 500。

    策略：超过 :data:`MAX_PAYLOAD_DEPTH` 的子树**整体替换为 None**（调用方
    全部按 ``isinstance`` 兜底，缺失等价于 Zotero 没给该字段），代理字符替换
    为 U+FFFD。洗数据而不是抛错——Zotero 库里有畸形条目时，其余正常条目仍应
    可导入（部分降级优于整单失败）。

    深度守卫的定位（如实标注）：**当前三条路由的返回值都是扁平原语**
    （key/name/parent/num_items、RIS 文本），实测 991 层嵌套的字段经
    ``str()`` 后回 200——即今天没有一条已观测的「解析成功后再爆栈」500 路径。
    深度守卫是纵深防线（32 层远宽于真实 Zotero 的 ≤6 层），防止后续把原始
    子树塞进响应/存储时重新打开同一类缺陷。本函数自身递归，但递归深度被
    ``MAX_PAYLOAD_DEPTH`` 硬性封顶。
    """
    if isinstance(value, str):
        return _LONE_SURROGATE_RE.sub("\ufffd", value)
    if isinstance(value, dict):
        if depth >= MAX_PAYLOAD_DEPTH:
            return None
        return {_LONE_SURROGATE_RE.sub("\ufffd", key)
                if isinstance(key, str) else key:
                _sanitize_payload(item, depth + 1)
                for key, item in value.items()}
    if isinstance(value, list):
        if depth >= MAX_PAYLOAD_DEPTH:
            return None
        return [_sanitize_payload(item, depth + 1) for item in value]
    return value


def _fetch_json(path: str) -> object:
    """GET 并解析 JSON；坏响应优雅失败（ZoteroLocalError，绝不 500）。

    复审 R8#W1-1：``RecursionError``（深层嵌套，**非 ValueError 子类**）此前
    冒泡成 500——单独捕获并映射 ``zotero_bad_response``；解析成功的对象再经
    :func:`_sanitize_payload` 洗掉超深子树与代理字符，堵住下游递归/编码爆栈。
    """
    raw = _http_get(path)
    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
    except RecursionError:
        raise ZoteroLocalError(
            "Zotero 本地 API 响应嵌套过深，已停止解析。",
            code="zotero_bad_response") from None
    except ValueError:
        raise ZoteroLocalError(
            "Zotero 本地 API 响应不是合法 JSON。",
            code="zotero_bad_response") from None
    return _sanitize_payload(payload)


def _fetch_paged(path: str) -> list[dict]:
    """按 start/limit 分页聚合 JSON 数组端点；形状异常优雅失败。

    复审 R4#2：绝不静默截断——跑满上限时**探测下一页**而不是想当然报错。
    复审 R5#1：上一版在跑满 ``MAX_PAGES_PER_REQUEST`` 页后无条件抛错，把
    「恰好 5000 条」这个本来合法的输入误拒了（端点侧
    ``total > MAX_ITEMS_PER_IMPORT`` 是严格大于，5000 合法）。现在第 50 页
    仍满时再取 1 条探针：探针为空 = 恰好到上限，正常返回；探针非空 = 确实
    还有更多，才抛 ``zotero_too_many_items``。这样「恰好边界」与「超出边界」
    被真正区分开，而不是靠页数猜测。
    """
    separator = "&" if "?" in path else "?"
    rows: list[dict] = []
    for page in range(MAX_PAGES_PER_REQUEST):
        payload = _fetch_json(
            f"{path}{separator}start={page * PAGE_LIMIT}&limit={PAGE_LIMIT}")
        if not isinstance(payload, list):
            raise ZoteroLocalError(
                "Zotero 本地 API 返回的数据形状异常（不是条目列表）。",
                code="zotero_bad_response")
        rows.extend(row for row in payload if isinstance(row, dict))
        if len(payload) < PAGE_LIMIT:
            return rows
    # 末页仍满：用 1 条探针区分「恰好等于上限」（合法）与「还有更多」（拒绝）。
    capped = MAX_PAGES_PER_REQUEST * PAGE_LIMIT
    probe = _fetch_json(f"{path}{separator}start={capped}&limit=1")
    if not isinstance(probe, list):
        raise ZoteroLocalError(
            "Zotero 本地 API 返回的数据形状异常（不是条目列表）。",
            code="zotero_bad_response")
    if probe:
        raise ZoteroLocalError(
            f"Zotero 返回的条目超过单次读取上限（{capped} 条），"
            "为避免静默丢失尾部条目已停止读取；请改用子集合分批导入。",
            code="zotero_too_many_items")
    return rows


# ---------------------------------------------------------------------------
# 拉取与转换（纯函数部分供测试直接调用）
# ---------------------------------------------------------------------------

def fetch_collections() -> list[dict]:
    """拉取本机 Zotero 库的全部集合（顶层在前、按名称排序）。"""
    rows = _fetch_paged("/api/users/0/collections?format=json")
    collections: list[dict] = []
    for row in rows:
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        key = str(row.get("key") or data.get("key") or "").strip()
        name = str(data.get("name") or "").strip()
        if not key or not name:
            continue
        parent = data.get("parentCollection")
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        num_items = meta.get("numItems")
        collections.append({
            "key": key,
            "name": name,
            "parent": parent if isinstance(parent, str) and parent else None,
            "num_items": num_items if isinstance(num_items, int) else None,
        })
    # 顶层集合在前、同名按本地化大小写不敏感排序；层级缩进由前端按 parent 链计算。
    collections.sort(key=lambda c: (0 if c["parent"] is None else 1,
                                    c["name"].casefold()))
    return collections


def fetch_collection_items(collection_key: str) -> list[dict]:
    """拉取一个集合的顶层条目（不含笔记/附件），返回 ``[{"key", "data"}]``。"""
    if not _COLLECTION_KEY_RE.fullmatch(collection_key or ""):
        raise ZoteroLocalError("集合键形状非法。", code="zotero_bad_collection_key")
    rows = _fetch_paged(
        f"/api/users/0/collections/{collection_key}/items/top?format=json")
    items: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        key = str(row.get("key") or "").strip()
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        item_type = str(data.get("itemType") or "").strip()
        if not key or key in seen or item_type in _SKIPPED_ITEM_TYPES or not item_type:
            continue
        seen.add(key)
        items.append({"key": key, "data": data})
    return items


def _one_line(value: object) -> str:
    """压成单行（RIS 值不跨行；换行/制表折叠为空格）。"""
    return " ".join(str(value if value is not None else "").split())


def _creator_text(creator: dict) -> str:
    """Zotero creator → 「姓, 名」（单字段机构名原样）。"""
    name = _one_line(creator.get("name"))
    if name:
        return name
    last = _one_line(creator.get("lastName"))
    first = _one_line(creator.get("firstName"))
    if last and first:
        return f"{last}, {first}"
    return last or first


def item_to_ris_lines(item: dict) -> list[str]:
    """一条 Zotero 条目（{"key", "data"}）→ RIS 标签行（含 TY/ER）。

    ``ID`` 携带 Zotero 条目键（8 位紧凑标识，经
    coscreen.parsers.csv_parser.is_global_stable_identifier 口径合法），
    重导同一库沿用同一键，去重/更新语义与手工导出 RIS 完全一致。
    """
    data = item.get("data") if isinstance(item.get("data"), dict) else {}
    lines = [f"TY  - {_RIS_TYPE_BY_ITEM_TYPE.get(str(data.get('itemType') or ''), 'GEN')}"]
    key = _one_line(item.get("key"))
    if key:
        lines.append(f"ID  - {key}")
    title = _one_line(data.get("title"))
    if title:
        lines.append(f"T1  - {title}")
    creators = data.get("creators")
    if isinstance(creators, list):
        for creator in creators:
            text = _creator_text(creator if isinstance(creator, dict) else {})
            if text:
                lines.append(f"AU  - {text}")
    journal = ""
    for field in ("publicationTitle", "proceedingsTitle", "bookTitle",
                  "seriesTitle", "reportTitle"):
        candidate = _one_line(data.get(field))
        if candidate:
            journal = candidate
            break
    if journal:
        lines.append(f"JO  - {journal}")
    date_text = str(data.get("date") or "")
    year = _YEAR_RE.search(date_text)
    if year:
        lines.append(f"PY  - {year.group(1)}")
    doi = _one_line(data.get("DOI"))
    if doi:
        lines.append(f"DO  - {doi}")
    abstract = _one_line(data.get("abstractNote"))
    if abstract:
        lines.append(f"AB  - {abstract}")
    url = _one_line(data.get("url"))
    if url:
        lines.append(f"UR  - {url}")
    lines.append("ER  -")
    return lines


def items_to_ris_bytes(items: list[dict]) -> bytes:
    """合成一份 RIS 虚拟文件（UTF-8），交给受保护导入管线解析。"""
    lines: list[str] = []
    for item in items:
        lines.extend(item_to_ris_lines(item))
    if not lines:
        return b""
    return ("\n".join(lines) + "\n").encode("utf-8")


class _VirtualRisUpload:
    """UploadFile 形状的最小虚拟文件（filename + async read）。

    review_imports.upload() → _parse_upload_to_articles 只消费这两个成员
    （main.py:2192-2197）；经它落盘 tmpdir 后由 parse_file 按 .ris 解析。
    """

    def __init__(self, filename: str, payload: bytes) -> None:
        self.filename = filename
        self._payload = payload

    async def read(self) -> bytes:
        return self._payload


#: 与 security._UNSAFE_NAME_RE（security.py:66）同口径的非安全字符集。
_UNSAFE_FILENAME_RE = re.compile(r"[\\/:*?\"<>|\x00-\x1f\x7f]")


def _provenance_name(collection_name: str, collection_key: str,
                     used: set[str]) -> str:
    """集合名 → 受保护导入可完整保留的虚拟文件名。

    复审 R5#2：交给管线的 ``clean_upload_name``（security.py:374-375）会把
    路径成分截到最后一个 ``/`` 之后并删掉非法字符——集合名含 ``/`` 时，
    ``raw.source_file`` 只剩斜杠后半截，``Zotero`` 前缀与集合全名一起丢失，
    且 ``A/x`` 与 ``B/x`` 两个集合会撞成同名文件，溯源被静默退化。
    因此在此**先按同一口径清洗**（非法字符换成 ``-``，不删字符，保留可读性），
    保证名称再经 clean_upload_name 时不被改写；同名（清洗后碰撞）再追加集合键
    区分。
    """
    squashed = " ".join(str(collection_name or "").split())
    cleaned = _UNSAFE_FILENAME_RE.sub("-", squashed).strip().strip(".") or collection_key
    base = f"Zotero {cleaned[:60]} [{collection_key}].ris"
    if base not in used:
        used.add(base)
        return base
    # 清洗后仍同名：加序号，绝不让两个集合的溯源指向同一个文件名。
    serial = 2
    while f"Zotero {cleaned[:60]} [{collection_key}] ({serial}).ris" in used:
        serial += 1
    base = f"Zotero {cleaned[:60]} [{collection_key}] ({serial}).ris"
    used.add(base)
    return base


class ZoteroImportBody(BaseModel):
    collection_keys: list[str] = Field(min_length=1, max_length=MAX_COLLECTIONS_PER_IMPORT)
# ---------------------------------------------------------------------------
# 路由注册（依赖注入与 ai_suggest_api.register_ai_suggest_routes 同款模式）
# ---------------------------------------------------------------------------

def register_zotero_local_routes(app, require_user, task_lookup,
                                 task_visible=None) -> None:
    """注册 Zotero 本地 API 三端点（连接测试 / 集合浏览 / 导入所选集合）。

    复审 R4#5：与同批 W4（main.py:5609 → study_design_api.py:32-39）对齐——显式
    注入 ``task_visible`` 并在每个端点先判任务可见性。此前门禁完全外包给全局
    ``require_user``：本模块的出站（真实本机抓取）就在端点函数体内，任何一处
    把它挂到不同依赖栈就会静默变成「任意登录用户可读本机 Zotero 库」。
    ``task_visible=None`` 只保留给独立单测；生产注册行恒传 ``_task_visible_to``。
    """

    def _authorize(task_id: str, user: dict):
        info = task_lookup(task_id)
        if task_visible is not None and not task_visible(info, user):
            raise HTTPException(403, "Task access denied.")
        return info

    def _guide(exc: ZoteroLocalError) -> HTTPException:
        """ZoteroLocalError → 409 引导（4xx 优雅降级，绝不 500）。

        错误体键名与前端 api() 的结构化错误口径对齐（message 供 T() 翻译、
        code 供前端分流提示，同 living-review check-now 409 引导先例）。

        复审 R8#W1-2：``str(exc)[:300]`` 会把**用户可控**内容拼进响应体——
        请求体里的集合键若含孤立代理字符（``"\\ud800"``，合法 JSON 转义），
        这里再序列化就是 ``UnicodeEncodeError`` → 500（实测三端点皆然）。
        洗一次代理字符，message 恒可 UTF-8 编码。"""
        return HTTPException(409, {
            "code": exc.code,
            "message": _LONE_SURROGATE_RE.sub("\ufffd", str(exc))[:300],
            "endpoint": BASE_URL,
        })

    @app.post("/api/tasks/{task_id}/zotero-local/test")
    async def zotero_local_test(task_id: str,
                                user: dict = Depends(require_user)) -> dict:
        """连接测试（显式手动触发；探活声明的回环端点，不写任何数据）。"""
        _authorize(task_id, user)
        try:
            started = time.monotonic()
            await asyncio.to_thread(
                _fetch_json, "/api/users/0/collections?format=json&limit=1")
            latency_ms = int((time.monotonic() - started) * 1000)
        except ZoteroLocalError as exc:
            raise _guide(exc) from None
        except Exception:  # noqa: BLE001 —— 兜底见 docstring
            # 复审 R8#W1-2：本端点的全部工作（DNS/协议/解析/清洗）都已各自收敛，
            # 这里是最后一道「未被想到的异常类型」闸门——never-500 是书面契约
            # （docs/API_REFERENCE.md），宁可给通用 409 引导也不给 500。
            logger.exception("zotero-local/test 未预期的失败（已降级为 409）")
            raise _guide(ZoteroLocalError(
                "读取本机 Zotero 时发生未预期的错误，请重试；若持续失败请"
                "改用导出文件导入。", code="zotero_bad_response")) from None
        return {"ok": True, "endpoint": BASE_URL, "latency_ms": latency_ms}

    @app.get("/api/tasks/{task_id}/zotero-local/collections")
    async def zotero_local_collections(task_id: str,
                                       user: dict = Depends(require_user)) -> dict:
        """集合浏览（显式手动触发；只读，不写任何数据）。"""
        _authorize(task_id, user)
        try:
            collections = await asyncio.to_thread(fetch_collections)
        except ZoteroLocalError as exc:
            raise _guide(exc) from None
        except Exception:  # noqa: BLE001 —— never-500 兜底，同上
            logger.exception("zotero-local/collections 未预期的失败（已降级为 409）")
            raise _guide(ZoteroLocalError(
                "读取本机 Zotero 时发生未预期的错误，请重试；若持续失败请"
                "改用导出文件导入。", code="zotero_bad_response")) from None
        return {"collections": collections, "count": len(collections),
                "endpoint": BASE_URL}

    @app.post("/api/tasks/{task_id}/zotero-local/import")
    async def zotero_local_import(task_id: str, body: ZoteroImportBody,
                                  screener: str | None = Query(default=None),
                                  user: dict = Depends(require_user)) -> dict:
        """导入所选集合：条目合成 RIS 虚拟文件 → 受保护导入管线（去重/待审/
        确认/溯源全部复用）；写库只经 app.state.review_imports.upload()。"""
        _authorize(task_id, user)
        # 复审 R8#W1-2：集合键来自请求体，可能含孤立代理字符（"\\ud800" 是合法
        # JSON 转义）。它既会进 409 message，也会原样回显到响应体
        # （result["zotero"]["collections"][*]["key"]）——任一处序列化即 500。
        # 入口先按 _sanitize_payload 同一口径洗一次，后续所有拼接都安全。
        keys = [_sanitize_payload(key) for key in body.collection_keys]
        try:
            by_key = {row["key"]: row for row in await asyncio.to_thread(fetch_collections)}
            missing = [key for key in keys if key not in by_key]
            if missing:
                raise ZoteroLocalError(
                    f"集合不存在或已删除：{', '.join(missing[:5])}"
                    + ("…" if len(missing) > 5 else ""),
                    code="zotero_collection_not_found")
            selected: list[dict] = []
            seen: set[str] = set()
            total = 0
            for collection_key in keys:
                items = await asyncio.to_thread(
                    fetch_collection_items, collection_key)
                fresh = [item for item in items if item["key"] not in seen]
                seen.update(item["key"] for item in fresh)
                total += len(fresh)
                if total > MAX_ITEMS_PER_IMPORT:
                    raise ZoteroLocalError(
                        f"所选集合合计超过单次导入上限（{MAX_ITEMS_PER_IMPORT} 条），"
                        "请分批导入。", code="zotero_too_many_items")
                selected.append({"key": collection_key,
                                 "name": by_key[collection_key]["name"],
                                 "items": fresh})
        except ZoteroLocalError as exc:
            raise _guide(exc) from None
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001 —— never-500 兜底（见模块 docstring）
            logger.exception("zotero-local/import 抓取/整备阶段未预期的失败（已降级为 409）")
            raise _guide(ZoteroLocalError(
                "读取本机 Zotero 时发生未预期的错误，请重试；若持续失败请"
                "改用导出文件导入。", code="zotero_bad_response")) from None

        uploads: list[_VirtualRisUpload] = []
        used_names: set[str] = set()
        try:
            for entry in selected:
                if not entry["items"]:
                    continue
                safe_name = _provenance_name(entry["name"], entry["key"], used_names)
                uploads.append(_VirtualRisUpload(
                    safe_name, items_to_ris_bytes(entry["items"])))
        except (UnicodeError, RecursionError, ValueError) as exc:
            # 合成层只消费 Zotero 数据（已过 _sanitize_payload）；这里再兜一层，
            # 保证「畸形条目」不会把整条导入打成 500，而是可读的 409 引导。
            logger.warning("zotero-local/import RIS 合成失败（已降级为 409）：%r", exc)
            raise _guide(ZoteroLocalError(
                "本机 Zotero 返回的条目内容无法转换为导入文件（数据含非法字符）。"
                "请检查该集合中的异常条目后重试。",
                code="zotero_bad_response")) from None
        if not uploads:
            raise HTTPException(422, {
                "code": "zotero_no_items",
                "message": "所选集合没有可导入的文献条目。",
                "endpoint": BASE_URL,
            })
        # 受保护导入接缝（review_imports_api.py:74）：1-20 个虚拟文件、来源 auto、
        # 无进度 job；授权/去重/暂存待审/导入历史/溯源全部走既有管线。
        #
        # source_id 取舍（复审 R4#5 要求写明）：**固定 None → 'auto'**。手工导入
        # 端点让用户在界面上挑目标来源（main.py:2416-2424 的 source_id Form），
        # 而 Zotero 直连没有"逐文件挑来源"的交互面；来源归属交给既有管线按
        # 指纹/批次判定（import_sources.prepare_files 对 'auto' 的语义），与
        # "导出 RIS 后手工导入但来源留空"完全同义。将来若要做逐集合指定来源，
        # 在此按 collection_keys 顺序对齐 source_ids 即可，接缝不变。
        result = await app.state.review_imports.upload(
            task_id, user, uploads, batch=True, source_ids=None,
            job_id=None, screener=screener)
        result["zotero"] = {
            "endpoint": BASE_URL,
            "total_items": sum(len(entry["items"]) for entry in selected),
            "collections": [{"key": entry["key"], "name": entry["name"],
                             "items": len(entry["items"])} for entry in selected],
        }
        return result
