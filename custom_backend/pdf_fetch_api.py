"""W2 · PDF 自动获取（空白格 B1）路由：DOI→Unpaywall→开放 PDF→落盘+轨迹。

对应 plan.md AW2 / plan-session W2 泳道，治理红线（空白格计划原文）逐条落点：

- **默认关 + 显式手动触发**：任务级 ``pdf_fetch_settings.json`` 默认
  ``enabled=false``；**严格真值**（仅 ``True`` / ``"true"`` / ``1`` 算开，
  其余一律关——存量或手改 JSON 的任意非空值不得把功能打开）；未启用时
  fetch / fetch-batch / metadata-suggestions 一律 403 ``pdf_fetch_disabled``，
  且**零出站**（先判开关再碰网络）；
- **出站仅 stdlib urllib**（显式 UA、短超时、读取上限、拒绝重定向）：
  ``_NoRedirectHandler`` 先例 = custom_backend/ai_suggest_api.py:301；
  JSON 响应读取上限 2 MiB、PDF 读取上限 100 MiB（= security.MAX_UPLOAD_BYTES，
  与 check_pdf_bytes 同口径）；30x 视为上游错误（HTTPError），绝不跟随；
- **失败优雅降级绝不 500**：``_get_bytes`` 除 ``HTTPError``/``URLError``/
  ``TimeoutError``/``OSError`` 外还接住 **http.client.HTTPException 族**
  （BadStatusLine / IncompleteRead / RemoteDisconnected / 垃圾状态行——半开
  连接与反代脏响应的典型形态），并以兜底 ``except Exception`` 收口；映射
  502 ``pdf_fetch_upstream``（结构化 reason，消息脱敏——不含 email/完整载荷）；
  存储类失败（落盘 OSError、SQLite OperationalError/DatabaseError、设置
  原子写失败）→ 503 ``pdf_fetch_storage``；批量端点逐条收敛为 per-key
  outcome，单条失败绝不升级为整批 5xx；
- **批量入口的前置准备同样不裸 500**：``fetch-batch`` 在循环之前要做
  ``task context`` / ``screener_db``（每次都跑 ``init_db``）/ ``pdfs_dir_for``
  （``mkdir``）——SQLite 争用（``database is locked``）与磁盘只读/写满都是
  标准运行形态，全部纳入降级：整批无法开始时回**结构化 503**
  （``pdf_fetch_storage`` + 可读指引 + ``retryable``，明确「未执行任何获取」），
  而不是未捕获异常 500；单篇/建议/设置端点的 ``task context`` 同族处理
  （同一类缺陷，同一文件内一并收口）。
- **Crossref 元数据回填只读**：``metadata-suggestions`` 只返回缺失字段
  （DOI/年份/摘要）的建议值，绝不写库——采纳由用户走既有更新路径留痕；
- **隐私披露 / 回环限定**：出站只到①配置的 Unpaywall base②配置的 Crossref
  base③Unpaywall 响应给出的开放 PDF URL；③ 另做**公网地址白名单**——字面
  回环/私网/链路本地地址、``localhost``/``.local``/``.internal`` 名称，以及
  解析结果落在上述范围的域名一律拒绝（``pdf_fetch_unsafe_url``），防止被配置
  上游通过响应把服务端当作内网探针；③ 的解析**只做一次**并**钉住已校验 IP**
  发起连接（自定义连接类连钉扎地址，Host 头与 HTTPS 的 SNI/证书主机名仍是
  原主机名），连接建立后再核对 peer 地址仍属公网——堵住「校验时答公网、
  连接时答 127.0.0.1」的 DNS 重绑定（be-outbound-0）；
- **官方端点不可绕过**：官方 Unpaywall 判定按规范化 (scheme, hostname 去尾点
  小写, 端口) 比较，``https://API.unpaywall.org`` / ``:443`` / 尾点 /
  ``/.`` 等变体一律视同官方；官方端点**强制 https**且**必须**带合规 email
  参数（自托管回环端点可免）。服务启动时与每次读取都校验，堵住「改 base_url
  绕过 email 条款」的洞；
- **不静默覆盖**：目标文献已有全文时单篇端点返回 409
  ``pdf_fetch_would_replace``（带既有 sha256/页数），前端确认后带
  ``overwrite=1`` 重发；批量默认跳过已有全文（``skipped/already_has_pdf``）；
- **阶段门禁**：与既有 ``review_workflow.writable_reason`` 对齐——任务只读、
  阶段暂停、文献不在当前来源范围时拒绝写入（409 + 既有 code 与
  ``workflow_settings``）；纯筛查资格类判定（尚未初筛纳入/待定）不拦 PDF
  附件，避免把「补全文」误当成筛查决策；
- **请求级幂等键**：同一 HTTP 请求（前端每个写请求都带 X-Request-ID）内的
  批量多篇会写多条轨迹；``fulltext.begin_write_request`` 以 request_id 为幂等
  键、主体不同即抛 TraceConflict。本模块为每个 key 派生独立 request_id
  （``{rid}:{key}``），批量与重放都不再互相顶撞；TraceConflict 另立
  ``pdf_fetch_trace_conflict``，不再伪装成「坏 PDF」；
- **自托管**：``unpaywall_base_url`` / ``crossref_base_url`` 可配（默认官方
  域名；可指向回环自建代理），存任务 JSON（模式同 AI 设置，原子写 +
  ``tasks._task_json_lock`` 同一把锁 + expected_version 乐观锁；未提供
  expected_version 时跳过乐观锁而非恒 409）。

落盘复用既有链路（inventory §C 触点核验）：
- ``security.check_pdf_bytes``（custom_backend/security.py:411）：magic +
  100MB 上限，下载字节先过这道校验；
- ``fulltext.save_pdf``（coscreen/fulltext.py:238）：单射落盘名 +
  pypdf 页数解析 + ``fulltext_added``/``fulltext_replaced`` 轨迹事件
  （透传 ``trace_context`` 即得，W2 不另造轨迹形状）。
"""
from __future__ import annotations

import dataclasses
import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import sqlite3
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlsplit

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from coscreen import db as db_mod
from coscreen import fulltext as fulltext_mod
from coscreen import tasks as tasks_mod
from custom_backend.security import MAX_UPLOAD_BYTES, UploadRejected, check_pdf_bytes

__all__ = [
    "DEFAULT_CROSSREF_BASE",
    "DEFAULT_UNPAYWALL_BASE",
    "MAX_BATCH_KEYS",
    "OutboundError",
    "is_official_unpaywall",
    "normalize_doi",
    "register_pdf_fetch_routes",
]

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

USER_AGENT = "ReviewFlow-PDF-Fetch/2.1.4"
DEFAULT_UNPAYWALL_BASE = "https://api.unpaywall.org"
DEFAULT_CROSSREF_BASE = "https://api.crossref.org"
OFFICIAL_UNPAYWALL_HOST = "api.unpaywall.org"
API_TIMEOUT_SECONDS = 15.0            # 元数据 API（Unpaywall/Crossref）短超时
PDF_TIMEOUT_SECONDS = 30.0            # PDF 下载短超时
MAX_JSON_BYTES = 2 * 1024 * 1024      # API JSON 响应读取上限（2 MiB）
MAX_PDF_BYTES = MAX_UPLOAD_BYTES      # 下载字节上限 = 上传同口径（100 MB）
MAX_OA_CANDIDATES = 3                 # 每个 DOI 最多尝试的开放 PDF 候选数
MAX_BATCH_KEYS = 50                   # 批量端点单次上限
MAX_SUGGESTION_CHARS = 8_000          # Crossref 摘要建议截断预算
TITLE_MATCH_MIN_RATIO = 0.9           # 无 DOI 检索的标题保守匹配下限
CONSECUTIVE_FAILURES_TO_ABORT = 2     # 批量连续出站失败熔断阈值

#: 阶段门禁里真正拦「写」的三种状态（不含纯筛查资格判定，见模块注释）。
WRITE_BLOCKING_REASONS = ("task_read_only", "stage_paused", "article_outside_screen_scope",
                          "article_outside_scope")

PDF_HOSTS_NOTE = (
    "仅访问上方列出的两个端点，以及 Unpaywall 响应中给出的开放获取 PDF 链接"
    "（仅限 http/https 的公网地址；本机与内网地址一律拒绝）。"
)

_KEY_RE = re.compile(r"^[^/\\\x00]{1,300}$")       # 与 main._KEY_RE 同口径
_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
_EMAIL_RE = re.compile(r"^[^\s@,;:\"'<>]{1,64}@[^\s@,;:\"'<>]{1,190}\.[^\s@,;:\"'<>]{1,24}$")
_JATS_TAG_RE = re.compile(r"<[^>]+>")
_BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")

_SETTINGS_FILENAME = "pdf_fetch_settings.json"
_DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": False,                                  # 治理红线：默认关
    "email": "",                                       # Unpaywall email 参数（非密钥）
    "unpaywall_base_url": DEFAULT_UNPAYWALL_BASE,      # 自托管可配（存任务 JSON）
    "crossref_base_url": DEFAULT_CROSSREF_BASE,
    "version": 0,
    "acknowledged_at": None,
}


# ---------------------------------------------------------------------------
# 出站客户端（stdlib urllib；测试注入 fake 替换 _OPENER / _resolve_host）
# ---------------------------------------------------------------------------

class OutboundError(Exception):
    """出站失败（reason 供 HTTP 映射；消息已脱敏：不含 email/完整载荷）。"""

    def __init__(self, reason: str, message: str):
        super().__init__(str(message)[:300])
        self.reason = reason


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """拒绝跟随 3xx 重定向（先例 custom_backend/ai_suggest_api.py:301）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


class _PinnedConnectionMixin:
    """把 TCP 连接钉在**已校验**的 IP 上，其余语义（Host 头/证书校验）不变。

    ``http.client`` 在 ``connect()`` 里通过实例属性 ``_create_connection``
    （默认 = ``socket.create_connection``）建立连接；这里把它替换成钉扎版本：
    TCP 目标用 ``_pinned_address``，``self.host``（Host 头、HTTPS 的
    ``server_hostname``）保持原主机名。连接建立后再核对 peer 仍属公网，
    作为「钉扎后仍被中间设备/NAT 改写」的第二道防线。
    """

    _pinned_address: str | None = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if type(self)._pinned_address:
            self._create_connection = self._connect_pinned

    def _connect_pinned(self, address, timeout, source_address):
        sock = socket.create_connection(
            (self._pinned_address, address[1]), timeout, source_address)
        try:
            peer = str(sock.getpeername()[0])
        except OSError:
            sock.close()
            raise
        if _is_private_address(peer):
            sock.close()
            raise OSError("钉住的连接 peer 不属于公网地址。")
        return sock


class _PinnedHTTPConnection(_PinnedConnectionMixin, http.client.HTTPConnection):
    """明文 HTTP：钉住 IP 连接，Host 头仍是原主机名。"""


class _PinnedHTTPSConnection(_PinnedConnectionMixin, http.client.HTTPSConnection):
    """HTTPS：钉住 IP 连接，TLS 的 SNI/证书主机名校验仍用原主机名。"""


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    """按请求的 ``pinned_address`` 选择钉扎连接类（无钉扎时退回默认行为）。"""

    def http_open(self, req):
        pinned = getattr(req, "pinned_address", None)
        if not pinned:
            return super().http_open(req)
        conn_class = type("_BoundPinnedHTTPConnection",
                          (_PinnedHTTPConnection,), {"_pinned_address": pinned})
        return self.do_open(conn_class, req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    """HTTPS 版钉扎 handler；``context``/``check_hostname`` 原样透传。"""

    def https_open(self, req):
        pinned = getattr(req, "pinned_address", None)
        if not pinned:
            return super().https_open(req)
        conn_class = type("_BoundPinnedHTTPSConnection",
                          (_PinnedHTTPSConnection,), {"_pinned_address": pinned})
        return self.do_open(conn_class, req, context=self._context,
                            check_hostname=self._check_hostname)


#: 拒绝跟随 3xx（_NoRedirectHandler）+ **显式空 ProxyHandler**：urllib 默认读
#: http_proxy/HTTP_PROXY/https_proxy 环境变量，企业/校园网普遍预设，会把请求
#: 整包发往代理主机——DOI 与联系 email 随之落到第三方，『只访问①②③』的隐私
#: 披露变成假话，配置到回环的自托管端点也永远收不到请求（r3 复审②实测：请求
#: 打到 HTTP_PROXY 指向的本地代理，自托管服务端 hits=[]）。同批先例：
#: custom_backend/zotero_local_api.py:103-107 对回环探测用了同一写法。
def _build_opener():
    """构造出站 opener（空代理 + 拒重定向）。

    独立成函数便于测试断言 handler 组成——``_OPENER`` 在用例里会被替换成
    受控桩，直接读它拿不到真实 handler 列表。
    """
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirectHandler,
        _PinnedHTTPHandler(), _PinnedHTTPSHandler())


_OPENER = _build_opener()


def _open(request: urllib.request.Request, timeout: float):
    """模块级出站缝（seam）：测试以 fake opener 替换 _OPENER。"""
    return _OPENER.open(request, timeout=timeout)  # noqa: S310  URL 由白名单构造


def _resolve_host(host: str, port: int = 443) -> list[str]:
    """DNS 解析缝（seam）：返回地址串列表；测试以 stub 替换。"""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def _is_trace_conflict(exc: BaseException) -> bool:
    """``save_pdf`` 抛的 ValueError 里区分「轨迹幂等键冲突」与「坏 PDF」。

    两者同为 ValueError（TraceConflict 是 ValueError 子类），只能按类型判，
    不能按消息判——消息是内部英文串，不应回显给用户。
    """
    try:
        from coscreen.research_trace import TraceConflict
    except Exception:  # noqa: BLE001  可选模块不可用则无该类型可判
        return False
    return isinstance(exc, TraceConflict)


# ---------------------------------------------------------------------------
# base_url / 端点身份 / URL 白名单
# ---------------------------------------------------------------------------

def _normalize_base_url(value: Any, field: str) -> str:
    """base_url 校验/规范化：仅 http(s)、必须带主机、无查询/锚点/userinfo。"""
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{field} 不能为空。")
    if any(ch.isspace() or ord(ch) < 0x20 for ch in raw):
        raise ValueError(f"{field} 含非法字符。")
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"{field} 必须以 http:// 或 https:// 开头。")
    if not parts.hostname:
        raise ValueError(f"{field} 缺少主机名。")
    if parts.query or parts.fragment:
        raise ValueError(f"{field} 不允许携带查询串或锚点。")
    if parts.username or parts.password:
        raise ValueError(f"{field} 不允许携带 userinfo。")
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"


def _host_identity(hostname: str | None) -> str:
    """主机名规范形：小写 + 去所有尾点（``API.Unpaywall.org.`` → ``api.unpaywall.org``）。"""
    return (hostname or "").strip().rstrip(".").lower()


def _effective_port(parts) -> int | None:
    try:
        return parts.port
    except ValueError:          # 畸形端口（'https://x:abc'）
        return -1


def is_official_unpaywall(base_url: str) -> bool:
    """官方 Unpaywall 判定：**hostname 规范化后相等即官方，与端口/协议无关**。

    r3 复审③：旧实现要求 ``port in (None, 443)``，于是
    ``http://api.unpaywall.org:80`` / ``https://api.unpaywall.org:8443``
    被判为非官方——email 必填与 https 强制两条约束同时失效（实测 PUT 200
    且明文出站、无 email 参数）。官方主机就是官方主机：端口只决定是否
    另报 insecure，不能用来脱身。大小写、尾点、``/.`` 等写法同样归一。
    """
    parts = urlsplit(base_url or "")
    return _host_identity(parts.hostname) == OFFICIAL_UNPAYWALL_HOST


def is_secure_official_base(base_url: str) -> bool:
    """官方端点是否用了 https 且端口未偏离 443（否则按不安全拒绝）。"""
    parts = urlsplit(base_url or "")
    if not is_official_unpaywall(base_url):
        return True                      # 非官方（自托管）由 public-host 规则决定
    return parts.scheme == "https" and _effective_port(parts) in (None, 443)


def _is_private_address(value: str) -> bool:
    """非公网地址判定（fail closed）：**以 ``is_global`` 为唯一正典**。

    r3 复审①：旧实现用 ``is_loopback or is_private or is_link_local or is_reserved
    or is_multicast or is_unspecified``，而 Python 3.11 的 ``ipaddress`` 认为
    100.64.0.0/10（RFC 6598 CGNAT）**既非 private 也非 reserved**——阿里云 ECS
    元数据面 100.100.100.200 因此被判为「可访问」，服务端会真的发出请求
    （实测 ``ipaddress.ip_address('100.100.100.200').is_private is False``）。
    ``is_global`` 白名单口径天然覆盖 CGNAT、文档段、保留段与未来新增的非公网段。
    """
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return True                     # 解析不出 IP：按不可信处理（fail closed）
    return not ip.is_global


def _validate_and_pin(hostname: str, port: int) -> tuple[list[str] | None, str | None]:
    """字面规则 + **单次** DNS 解析 + 公网校验：``(已校验地址列表, 拒绝原因)``。

    两道关：① 字面名称（localhost / *.localhost / *.local / *.internal /
    *.home.arpa）与字面 IP 直接判；② DNS 解析结果任一落在回环/私网/链路本地/
    保留段即拒绝（防「域名解析到 127.0.0.1」与 DNS rebinding 常见形态）。
    解析失败同样拒绝（fail closed）——宁可提示无法获取，也不做内网探针。

    返回的地址列表就是本次**唯一一次**解析的结果；调用方必须钉住其中一个
    发起连接（见 ``_PinnedConnectionMixin``），连接阶段不得再次解析。
    """
    host = _host_identity(hostname)
    if not host:
        return None, "invalid_host"
    if host == "localhost" or host.endswith(_BLOCKED_HOST_SUFFIXES):
        return None, "private_host"
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return (None, "private_host") if _is_private_address(host) else ([host], None)
    try:
        addresses = _resolve_host(host, port)
    except (OSError, ValueError):
        return None, "unresolvable_host"
    if not addresses:
        return None, "unresolvable_host"
    if any(_is_private_address(address) for address in addresses):
        return None, "private_host"
    return addresses, None


def _public_host_reason(hostname: str) -> str | None:
    """公网地址校验门面（2 元组旧缝）：返回拒绝原因；None = 可访问。"""
    _addresses, reason = _validate_and_pin(hostname, 443)
    return reason


def _classify_fetchable_target(url: Any) -> tuple[dict | None, str | None]:
    """开放 PDF 候选 URL 分类 + 单次解析钉扎。

    返回 ``({"url","host","port","address"}, None)`` 或 ``(None, 拒绝原因)``。
    ``address`` 来自本 URL **唯一一次** DNS 解析且已通过公网校验；下载阶段
    必须用它连接（``_get_bytes(..., pinned_address=...)``），不得再解析。
    """
    if not isinstance(url, str) or not url.strip():
        return None, "invalid_url"
    candidate = url.strip()
    if any(ch.isspace() or ord(ch) < 0x20 for ch in candidate):
        return None, "invalid_url"
    parts = urlsplit(candidate)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None, "invalid_url"
    if parts.username or parts.password:
        return None, "invalid_url"
    port = _effective_port(parts)
    if port == -1:                      # 畸形端口（'https://x:abc'）
        return None, "invalid_url"
    if port is None:
        port = 443 if parts.scheme == "https" else 80
    addresses, reason = _validate_and_pin(parts.hostname, port)
    if reason:
        return None, reason
    return {"url": candidate, "host": _host_identity(parts.hostname),
            "port": port, "address": addresses[0]}, None


def _classify_fetchable_url(url: Any) -> tuple[str | None, str | None]:
    """开放 PDF 候选 URL 分类（2 元组旧缝）：返回 ``(url, None)`` 或 ``(None, 原因)``。"""
    target, reason = _classify_fetchable_target(url)
    return (None, reason) if target is None else (target["url"], None)


def _get_bytes(url: str, *, timeout: float, max_bytes: int, accept: str,
               pinned_address: str | None = None) -> bytes:
    """GET 指定 URL：显式 UA + 短超时 + 读取上限 + 拒绝重定向（30x=HTTPError）。

    ``pinned_address`` 给定（PDF 候选下载）时，连接钉住该已校验 IP——分类
    阶段已解析过一次，连接阶段不再解析，堵住 DNS 重绑定（be-outbound-0）。
    未给定时（配置的 Unpaywall/Crossref 端点，允许自托管回环）保持原行为。
    """
    request = urllib.request.Request(  # noqa: S310  URL 经上方白名单校验
        url, method="GET",
        headers={"User-Agent": USER_AGENT, "Accept": accept})
    if pinned_address:
        # 防御纵深：只接受公网 IP（正常调用方传的是分类阶段已校验的地址）。
        if _is_private_address(str(pinned_address)):
            raise OutboundError("private_host", "开放获取链接指向本机或内网地址，已拒绝访问。")
        request.pinned_address = pinned_address
    try:
        with _open(request, timeout) as response:  # noqa: S310
            data = response.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        # _NoRedirectHandler 把 30x 也变成 HTTPError——不跟随，按上游状态报降级。
        raise OutboundError("upstream_status", f"上游返回 HTTP {exc.code}。") from None
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise OutboundError("unreachable", f"无法连接上游（{type(reason).__name__}）。") from None
    except TimeoutError:
        raise OutboundError("timeout", "上游响应超时。") from None
    except http.client.HTTPException as exc:
        # 半开连接 / 反代垃圾状态行 / chunked 截断：BadStatusLine、IncompleteRead、
        # RemoteDisconnected 等既非 OSError 也非 ValueError，不接住就会 500。
        raise OutboundError("bad_response",
                            f"上游响应不完整或不是有效 HTTP（{type(exc).__name__}）。") from None
    except (OSError, ValueError) as exc:
        raise OutboundError("unreachable", f"出站请求失败（{type(exc).__name__}）。") from None
    except AssertionError:
        # r3 复审⑭：AssertionError 一律是程序/测试缺陷，不是上游形态。兜底
        # except Exception 会把它吞成 502 bad_response，使测试桩的「未声明端点
        # 即破坏即红」在生产代码里被降级成可容忍响应——自掩蔽。这里显式放行。
        raise
    except Exception as exc:  # noqa: BLE001  红线：绝不因上游异常形态 500
        raise OutboundError("bad_response", f"出站请求异常（{type(exc).__name__}）。") from None
    if len(data) > max_bytes:
        raise OutboundError("too_large", f"上游响应超过 {max_bytes // (1024 * 1024)} MiB 读取上限。")
    return data


def _get_json(url: str, *, timeout: float = API_TIMEOUT_SECONDS) -> Any:
    data = _get_bytes(url, timeout=timeout, max_bytes=MAX_JSON_BYTES,
                      accept="application/json")
    try:
        return json.loads(data.decode("utf-8", "replace"))
    except ValueError:
        raise OutboundError("bad_response", "上游返回的不是有效 JSON。") from None


# ---------------------------------------------------------------------------
# DOI / 元数据纯函数
# ---------------------------------------------------------------------------

def normalize_doi(text: Any) -> str | None:
    """常见 DOI 写法归一（URL 前缀/doi: 前缀）→ ``10.xxxx/…``；不合法返回 None。"""
    value = str(text or "").strip()
    for prefix in ("https://doi.org/", "http://doi.org/",
                   "https://dx.doi.org/", "http://dx.doi.org/", "doi:"):
        if value.lower().startswith(prefix):
            value = value[len(prefix):].strip()
            break
    return value if _DOI_RE.match(value) else None


def _norm_title(text: Any) -> str:
    return " ".join(str(text or "").lower().split())


def _titles_match(candidate: Any, expected: str) -> bool:
    """无 DOI 检索时的保守标题匹配：折叠比较后互相包含（长短差 ≤10%）。"""
    got = _norm_title(candidate)
    if not got or not expected:
        return False
    shorter, longer = sorted((got, expected), key=len)
    if shorter not in longer:
        return False
    return len(shorter) / max(len(longer), 1) >= TITLE_MATCH_MIN_RATIO


def _strip_jats(text: Any) -> str:
    return " ".join(_JATS_TAG_RE.sub(" ", str(text or "")).split())


def _crossref_year(message: dict) -> int | None:
    for key in ("issued", "published-print", "published-online"):
        parts = ((message.get(key) or {}).get("date-parts") or [[]])[0]
        if parts and isinstance(parts[0], int):
            return parts[0]
    return None


# ---------------------------------------------------------------------------
# 任务设置（JSON 文件 + 原子写 + 任务锁；模式同 ai_suggest 设置）
# ---------------------------------------------------------------------------

def _settings_path(info) -> Path:
    return info.dir / _SETTINGS_FILENAME


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


def _strict_bool(value: Any) -> bool:
    """红线口径的真值：只有 True / "true" / 1（不区分大小写的 "true"）算开。

    存量或手改 JSON 里的 ``"false"``、``"0"``、``{}``、``[]`` 等「非空即真」
    值一律按关处理——默认关不能被类型噪音翻成开。
    """
    if value is True:
        return True
    if isinstance(value, int) and not isinstance(value, bool):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def _load_settings(info) -> dict:
    try:
        stored = json.loads(_settings_path(info).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = None
    merged = {**_DEFAULT_SETTINGS}
    if isinstance(stored, dict):
        for key in _DEFAULT_SETTINGS:
            if key in stored and stored[key] is not None:
                merged[key] = stored[key]
    merged["enabled"] = _strict_bool(merged["enabled"])
    merged["email"] = str(merged["email"] or "").strip()[:254]
    try:  # 损毁文件防御：version 非整数时回落 0（绝不因坏 JSON 500）
        merged["version"] = int(merged["version"] or 0)
    except (TypeError, ValueError):
        merged["version"] = 0
    # 存量文件可能带历史非法 base_url：读路径宽容（回默认），写路径强校验。
    for field, default in (("unpaywall_base_url", DEFAULT_UNPAYWALL_BASE),
                           ("crossref_base_url", DEFAULT_CROSSREF_BASE)):
        try:
            merged[field] = _normalize_base_url(merged[field], field)
        except ValueError:
            merged[field] = default
    return merged


def _mask_email(email: str) -> str:
    """联系 email 掩码：``owner@team.example`` → ``o***@team.example``。

    保留首字符与域名（便于确认「配的是哪个域」），中间一律星号；形状不合规
    时整串打码（不得因畸形值泄露原文）。
    """
    value = str(email or "")
    local, _, domain = value.partition("@")
    if not local or not domain or len(value) > 254:
        return "***"
    return f"{local[0]}***@{domain}"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _email_error(value: str, official: bool) -> HTTPException | None:
    """启用校验：官方 Unpaywall 端点必须带合规 email（自托管可免）。"""
    if not value:
        return None if not official else HTTPException(
            422, {"code": "pdf_fetch_email_required",
                  "detail": "使用官方 Unpaywall 端点时必须填写联系 email（非密钥）。"})
    if not _EMAIL_RE.match(value):
        return HTTPException(422, {"code": "pdf_fetch_email_invalid",
                                   "detail": "email 格式不正确。"})
    return None


# ---------------------------------------------------------------------------
# Unpaywall / Crossref 查询
# ---------------------------------------------------------------------------

def _unpaywall_pdf_candidates(doi: str, settings: dict) -> tuple[list[dict], list[str]]:
    """Unpaywall v2 API → ``(可用 PDF 目标, 被拒原因列表)``。

    仅取 ``url_for_pdf``；只接受 http/https 公网地址（回环/私网/无法解析一律
    拒绝并记录原因，供上层区分「无 OA」与「链接不安全」）。每个目标的
    ``address`` 是本 URL 唯一一次 DNS 解析且已校验的公网 IP，下载必须钉住它。
    """
    base = settings["unpaywall_base_url"]
    email = settings["email"]
    query = f"?email={quote(email, safe='')}" if email else ""
    url = f"{base}/v2/{quote(doi, safe='')}{query}"
    payload = _get_json(url)
    if not isinstance(payload, dict):
        raise OutboundError("bad_response", "Unpaywall 响应形状异常。")
    if not payload.get("is_oa"):
        return [], []
    raw_urls: list[str] = []
    best = payload.get("best_oa_location")
    if isinstance(best, dict) and best.get("url_for_pdf"):
        raw_urls.append(str(best.get("url_for_pdf")))
    locations = payload.get("oa_locations")
    if isinstance(locations, list):
        for location in locations:
            if isinstance(location, dict) and location.get("url_for_pdf"):
                candidate = str(location.get("url_for_pdf"))
                if candidate not in raw_urls:
                    raw_urls.append(candidate)
    candidates: list[dict] = []
    rejected: list[str] = []
    for raw in raw_urls:
        target, reason = _classify_fetchable_target(raw)
        if target is not None:
            candidates.append(target)
        elif reason:
            rejected.append(reason)
    return candidates[:MAX_OA_CANDIDATES], rejected


def _crossref_message(article: dict, settings: dict) -> tuple[dict | None, str]:
    """Crossref works 查询：有 DOI 走 /works/{doi}；无 DOI 走标题检索保守匹配。

    返回 ``(message, how)``：``how`` ∈ {"doi","title",""}（""=未命中）。
    """
    base = settings["crossref_base_url"]
    doi = normalize_doi(article.get("doi"))
    if doi:
        payload = _get_json(f"{base}/works/{quote(doi, safe='')}")
        message = payload.get("message") if isinstance(payload, dict) else None
        return (message if isinstance(message, dict) else None), "doi"
    title = _norm_title(article.get("title"))
    if not title:
        return None, ""
    payload = _get_json(
        f"{base}/works?query.bibliographic={quote(title, safe='')}&rows=3")
    message = payload.get("message") if isinstance(payload, dict) else None
    if not isinstance(message, dict):
        # 上游偶发 {"message": null} / {"message": []}。与 DOI 路径同口径：
        # 非 dict 视为「无建议」，绝不让 .get 抛 AttributeError→500。
        return None, ""
    items = message.get("items")
    if not isinstance(items, list):
        raise OutboundError("bad_response", "Crossref 响应形状异常。")
    for item in items:
        if not isinstance(item, dict):
            continue
        titles = item.get("title")
        if isinstance(titles, list) and titles and _titles_match(titles[0], title):
            return item, "title"
    return None, ""


def _metadata_suggestions(article: dict, settings: dict) -> dict:
    """只读建议：仅返回当前缺失（DOI/年份/摘要）的字段值，绝不写库。"""
    message, how = _crossref_message(article, settings)
    if not message:
        return {"suggestions": {}, "matched_by": how or "none", "source": "crossref"}
    suggestions: dict[str, Any] = {}
    if not normalize_doi(article.get("doi")):
        doi = normalize_doi(message.get("DOI"))
        if doi:
            suggestions["doi"] = doi
    if article.get("year") in (None, ""):
        year = _crossref_year(message)
        if isinstance(year, int):
            suggestions["year"] = year
    if not str(article.get("abstract") or "").strip():
        abstract = _strip_jats(message.get("abstract"))[:MAX_SUGGESTION_CHARS]
        if abstract:
            suggestions["abstract"] = abstract
    return {"suggestions": suggestions, "matched_by": how, "source": "crossref"}


# ---------------------------------------------------------------------------
# 获取管线（单篇；网络/校验失败全部收敛为结构化结果，绝不裸 500）
# ---------------------------------------------------------------------------

def _key_trace_context(trace_context, key: str):
    """为每个 key 派生独立、**长度有界**的 request_id。

    ``fulltext.save_pdf`` 经 ``begin_write_request`` 以 request_id 做幂等键，
    同一请求内多篇共用同一 id 会在第二篇上抛 TraceConflict——批量因此在真实
    前端（每个写请求恒带 X-Request-ID）下必坏。派生后每篇各自幂等。

    r3 复审④：``TraceContext.normalized`` 的 request_id 上限是 200 字符
    （coscreen/research_trace.py:97/:117-125），直接拼 ``{rid}:{key}`` 会让
    key ≥164 字符的合法文献永久报错（36 字符 UUID + 1 + 127）。导入侧
    ``build_zotero_key`` 原样保留来源 ID 不截断，长键是可达的。故对 key 取
    sha256 前 16 位：总长 ≤ 36+1+16 = 53，且仍按 key 区分（碰撞概率可忽略）。
    """
    if trace_context is None:
        return None
    rid = getattr(trace_context, "request_id", None)
    if not rid:
        return trace_context
    digest = hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:16]
    derived = f"{rid}:" if len(rid) <= 160 else f"{rid[:160]}:"
    try:
        return dataclasses.replace(trace_context, request_id=derived + digest)
    except (TypeError, ValueError):
        return trace_context


def _fetch_one(db: Path, key: str, article: dict, *, settings: dict,
               pdfs_dir: Path, trace_context, overwrite: bool = False) -> dict:
    """执行单篇链路。返回 outcome dict；网络级失败抛 ``OutboundError``。"""
    existing = fulltext_mod.get_fulltext(db, key)
    if existing is not None and not overwrite:
        return {"zotero_key": key, "outcome": "skipped", "reason": "already_has_pdf",
                "existing": {"sha256": existing.sha256, "n_pages": existing.n_pages,
                             "filename": existing.filename, "added_at": existing.added_at}}
    doi = normalize_doi(article.get("doi"))
    if doi is None:
        return {"zotero_key": key, "outcome": "skipped", "reason": "doi_missing"}
    candidates, rejected = _unpaywall_pdf_candidates(doi, settings)  # 网络错误向上传播
    if not candidates:
        # r3 复审⑨：按原因分流——「指向本机/内网」是安全拒绝（blocked），
        # 「解析不出地址」是瞬时网络/DNS 问题（unresolvable），不该让用户以为
        # 链接是恶意的；file:// 等单纯不可用形态仍记无 OA。
        if "private_host" in rejected:
            return {"zotero_key": key, "outcome": "blocked", "reason": "private_host"}
        if "unresolvable_host" in rejected:
            return {"zotero_key": key, "outcome": "unresolvable",
                    "reason": "unresolvable_host"}
        return {"zotero_key": key, "outcome": "no_oa"}
    invalid = 0
    last_invalid = "下载内容不是可解析的 PDF。"
    context = _key_trace_context(trace_context, key)
    for candidate in candidates:
        url_pdf = candidate["url"]
        data = _get_bytes(url_pdf, timeout=PDF_TIMEOUT_SECONDS,
                          max_bytes=MAX_PDF_BYTES, accept="application/pdf,*/*",
                          pinned_address=candidate.get("address"))
        try:
            check_pdf_bytes(data)                 # security.py:411 同一道上传校验
        except UploadRejected as exc:
            invalid += 1
            last_invalid = str(exc)
            continue
        try:
            meta = fulltext_mod.save_pdf(          # coscreen/fulltext.py:238 落盘+轨迹
                db, key, data, pdfs_dir=pdfs_dir, trace_context=context)
        except ValueError as exc:                  # pypdf 0 页等 → 换下一候选
            if _is_trace_conflict(exc):
                # 幂等键冲突是「同一请求重放」语义问题，绝不是坏 PDF——单独成码。
                return {"zotero_key": key, "outcome": "trace_conflict",
                        "reason": "request_replay_conflict"}
            invalid += 1
            last_invalid = str(exc)
            continue
        return {"zotero_key": key, "outcome": "saved", "oa_url": url_pdf,
                "replaced": existing is not None,
                "filename": meta.filename, "sha256": meta.sha256,
                "n_pages": meta.n_pages, "added_at": meta.added_at}
    if invalid:
        return {"zotero_key": key, "outcome": "invalid", "reason": last_invalid}
    return {"zotero_key": key, "outcome": "no_oa"}


def _outbound_http_error(exc: OutboundError, settings: dict) -> HTTPException:
    """OutboundError → 502（结构化 reason；detail 脱敏——email 绝不进消息）。"""
    message = str(exc)
    email = settings.get("email") or ""
    if email and email in message:
        message = message.replace(email, "***")
    return HTTPException(502, {"code": "pdf_fetch_upstream",
                               "reason": exc.reason, "detail": message[:300]})


def _storage_http_error(_exc: Exception) -> HTTPException:
    return HTTPException(503, {"code": "pdf_fetch_storage",
                               "detail": "写入失败（磁盘或数据库暂时不可用），请稍后重试。"})


_BATCH_PREPARE_MESSAGE = (
    "任务数据库或数据目录暂时不可用（可能正被其他操作占用，或磁盘只读/已满）。"
    "本次批量没有执行任何获取，可稍后重试。")

_BATCH_CORRUPT_MESSAGE = (
    "任务的工作流/设置数据已损坏（无法解析）。本次批量没有执行任何获取；"
    "请先备份任务数据，再修复或重建该数据库后再试（重试不会自行恢复）。")

_SINGLE_CORRUPT_MESSAGE = (
    "任务的工作流/设置数据已损坏（无法解析）。请先备份任务数据，再修复或重建"
    "该数据库后再试（重试不会自行恢复）。")


def _corrupt_state_http_error() -> HTTPException:
    """非批量入口读到**无法解析**的持久化状态 → 503（可读修复指引）。

    与 ``_storage_http_error`` 区分：那不是「暂时不可用」，重试无用，因此
    不加 ``Retry-After``，文案明确指向备份/修复数据库。
    """
    return HTTPException(503, {"code": "pdf_fetch_storage",
                               "detail": _SINGLE_CORRUPT_MESSAGE,
                               # 与批量版同因：对象型 detail 在前端只会经
                               # ``detail.message`` 走可读文案。
                               "message": _SINGLE_CORRUPT_MESSAGE,
                               "retryable": False})


def _batch_prepare_http_error(_exc: Exception, *, corrupt: bool = False) -> HTTPException:
    """批量入口**准备阶段**失败（任务上下文/建库/目录）→ 结构化 503。

    与 ``_storage_http_error`` 同一 ``code``（前端与既有测试已认这个码），但
    语义不同因而文案分开：这里整批根本没开始，**没有写入任何文献**。把它折
    成逐条 ``failed`` 会误导用户去逐篇核对文献；伪装成功更不行。带
    ``retryable``/``executed`` 让调用方能区分「稍后原地重试」与「数据坏了」。

    ``corrupt=True`` 用于准备阶段读到的**持久化状态无法解析**（如
    ``workflow_state.stages_json`` 损坏）——那是永久性损坏，``retryable``
    必须为假，文案给的是修复指引而不是「稍后重试」。
    """
    message = _BATCH_CORRUPT_MESSAGE if corrupt else _BATCH_PREPARE_MESSAGE
    return HTTPException(503, {
        "code": "pdf_fetch_storage",
        "detail": message,
        # 对象型 detail 在前端会被 JSON.stringify 成技术串（index.html 的
        # errOf 只在 ``detail.message`` 存在时才走可读文案），故同时给
        # ``message``，保证降级时用户看到的是指引而不是转义 JSON。
        "message": message,
        "scope": "batch",
        "stage": "prepare",
        "executed": 0,
        "retryable": not corrupt,
    }, headers={"Retry-After": "5"} if not corrupt else None)


# ---------------------------------------------------------------------------
# 路由注册（依赖注入与 ai_suggest_api 同款模式）
# ---------------------------------------------------------------------------

class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    email: str = Field(default="", max_length=254)
    unpaywall_base_url: str = Field(default=DEFAULT_UNPAYWALL_BASE, max_length=500)
    crossref_base_url: str = Field(default=DEFAULT_CROSSREF_BASE, max_length=500)
    #: 提供该字段则走乐观锁；省略（None）表示调用方明确不做版本判定。
    expected_version: int | None = Field(default=None, ge=0)


class BatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keys: list[str] = Field(min_length=1, max_length=MAX_BATCH_KEYS)
    overwrite: bool = False


def register_pdf_fetch_routes(app, require_user, task_lookup, screener_db,
                              pdfs_dir_for: Callable, trace_context_for: Callable) -> None:
    """注册 W2 PDF 获取路由（main.py 追加式单行注册）。"""

    def _workflow():
        return app.state.review_workflow

    def _context(task_id: str, user: dict):
        return _workflow().context(task_id, user)   # 非成员 403 / 任务不存在 404

    def _owner_context(task_id: str, user: dict):
        ctx = _context(task_id, user)
        if not ctx.can_manage:
            raise HTTPException(403, "Only the task owner can manage PDF fetch settings.")
        return ctx

    def _settings_payload(info, settings: dict, *, mask_email: bool = False) -> dict:
        """设置回包。

        r3 复审⑪：联系 email 是负责人的个人 PII（且会被发给 Unpaywall 作为
        联系人标识）。按既有惯例（ai_suggest 的钥匙路径一律掩码回显）对
        **非 owner** 掩码：保留域名片段便于识别「是否已配置」，不泄露全串。
        """
        payload = {**settings,
                   "official_endpoint": is_official_unpaywall(settings["unpaywall_base_url"]),
                   "outbound_endpoints": [settings["unpaywall_base_url"],
                                          settings["crossref_base_url"]],
                   "pdf_hosts_note": PDF_HOSTS_NOTE,
                   "defaults": {"unpaywall_base_url": DEFAULT_UNPAYWALL_BASE,
                                "crossref_base_url": DEFAULT_CROSSREF_BASE}}
        if mask_email and payload.get("email"):
            payload["email"] = _mask_email(payload["email"])
            payload["email_masked"] = True
        return payload

    def _require_enabled(info) -> dict:
        settings = _load_settings(info)
        if not settings["enabled"]:
            raise HTTPException(403, {"code": "pdf_fetch_disabled",
                                      "detail": "任务未启用 PDF 自动获取（默认关；负责人可在任务设置开启）。"})
        if not is_secure_official_base(settings["unpaywall_base_url"]):
            raise HTTPException(422, {"code": "pdf_fetch_base_url_insecure",
                                      "detail": "官方 Unpaywall 端点必须使用 https:// 与 443 端口。"})
        if is_official_unpaywall(settings["unpaywall_base_url"]) and not settings["email"]:
            # 运行时防线（堵「改 base_url 绕过 email 条款」）：即便存量/手改文件
            # 把官方端点与空 email 并存，也在出站前 422。
            raise HTTPException(422, {"code": "pdf_fetch_email_missing",
                                      "detail": "缺少 Unpaywall 联系 email，请负责人在任务设置补填。"})
        return settings

    def _gate_write(ctx, key: str) -> None:
        """与既有 write_guard 对齐：只读/阶段暂停/来源范围外拒绝写入全文。"""
        reason = _workflow().writable_reason(ctx, "fulltext", key)
        if reason in WRITE_BLOCKING_REASONS:
            try:
                workflow_settings = _workflow().settings(ctx)
                version = workflow_settings.get("version")
            except Exception:  # noqa: BLE001  设置读取失败不得把门禁变成 500
                workflow_settings, version = None, None
            raise HTTPException(409, {"code": reason, "version": version,
                                      "workflow_settings": workflow_settings})

    # -- 任务设置（owner 写 / 成员读；expected_version 乐观锁） ---------------

    @app.get("/api/tasks/{task_id}/pdf-fetch/settings")
    def get_settings(task_id: str, user: dict = Depends(require_user)) -> dict:
        try:
            info = task_lookup(task_id)
            ctx = _context(info.task_id, user)
        except HTTPException:
            raise
        except (sqlite3.Error, OSError) as exc:
            raise _storage_http_error(exc) from None
        except (ValueError, TypeError):
            raise _corrupt_state_http_error() from None
        return _settings_payload(info, _load_settings(info),
                                 mask_email=not ctx.can_manage)

    @app.put("/api/tasks/{task_id}/pdf-fetch/settings")
    def put_settings(task_id: str, body: SettingsIn,
                     user: dict = Depends(require_user)) -> dict:
        try:
            info = task_lookup(task_id)
            _owner_context(info.task_id, user)
        except HTTPException:
            raise
        except (sqlite3.Error, OSError) as exc:
            raise _storage_http_error(exc) from None
        except (ValueError, TypeError):
            # ValueError ⊂ 损坏的 workflow_state.stages_json（JSONDecodeError）。
            # 此处只覆盖**上下文**读取；下方 base_url 归一化仍是独立 422。
            raise _corrupt_state_http_error() from None
        email = body.email.strip()
        try:
            unpaywall_base = _normalize_base_url(body.unpaywall_base_url, "Unpaywall base_url")
            crossref_base = _normalize_base_url(body.crossref_base_url, "Crossref base_url")
        except ValueError as exc:
            raise HTTPException(422, {"code": "pdf_fetch_base_url_invalid",
                                      "detail": str(exc)}) from None
        if not is_secure_official_base(unpaywall_base):
            # 官方端点强制 https 且 443（r3 复审③：端口换 80/8443 不得绕过）。
            raise HTTPException(422, {"code": "pdf_fetch_base_url_insecure",
                                      "detail": "官方 Unpaywall 端点必须使用 https:// 与 443 端口。"})
        official = is_official_unpaywall(unpaywall_base)
        if body.enabled or email:
            email_error = _email_error(email, official)
            if email_error is not None:
                raise email_error
        try:
            with tasks_mod._task_json_lock(info.dir):  # noqa: SLF001  与 AI 设置同一条任务锁
                settings = _load_settings(info)
                if body.expected_version is not None and body.expected_version != settings["version"]:
                    raise HTTPException(409, {"code": "pdf_fetch_settings_version_conflict",
                                              "version": settings["version"]})
                was_enabled = bool(settings["enabled"])
                settings.update({
                    "enabled": bool(body.enabled),
                    "email": email,
                    "unpaywall_base_url": unpaywall_base,
                    "crossref_base_url": crossref_base,
                    "version": int(settings["version"]) + 1,
                })
                if body.enabled and not was_enabled and not settings.get("acknowledged_at"):
                    settings["acknowledged_at"] = _utc_now_iso()
                _atomic_write_json(_settings_path(info), settings)
        except OSError as exc:      # 磁盘满/只读：503，不能把失败的保存说成成功
            raise _storage_http_error(exc) from None
        return _settings_payload(info, settings)

    # -- 单篇获取（显式手动触发） ---------------------------------------------

    @app.post("/api/tasks/{task_id}/fulltext/{zotero_key}/fetch")
    def fetch_one(task_id: str, zotero_key: str, screener: str | None = None,
                  overwrite: bool = False,
                  user: dict = Depends(require_user)) -> dict:
        if not _KEY_RE.match(zotero_key or ""):
            raise HTTPException(400, "非法文献键。")
        # r4 复审（高·同族）：``_context`` 会开协作库 + 属主/个人库并跑迁移，
        # 库锁/损坏同样可能在这里抛——它在先前的 try 之外，实测（跨进程
        # BEGIN EXCLUSIVE）单篇端点照样 500。工作流上下文与开关读取一并纳入
        # 降级；403/404/422 语义由 HTTPException 分支原样保留。
        try:
            info = task_lookup(task_id)
            ctx = _context(info.task_id, user)
            settings = _require_enabled(info)
        except HTTPException:
            raise
        except (sqlite3.Error, OSError) as exc:
            raise _storage_http_error(exc) from None
        except (ValueError, TypeError):
            # 同族：上下文读到损坏的持久化状态（stages_json 等无法解析）。
            raise _corrupt_state_http_error() from None
        # r3 复审⑤：读库/门禁也在 try 内——SQLite 争用（database is locked /
        # malformed）是标准形态，不能因为在 try 之前就变成 500。
        try:
            db = screener_db(info, screener or user["username"])
            article = db_mod.get_article(db, zotero_key)
            if article is None:
                raise HTTPException(404, {"code": "pdf_fetch_article_missing",
                                          "detail": "该文献键在当前筛选库中不存在。"})
            _gate_write(ctx, zotero_key)
            existing = fulltext_mod.get_fulltext(db, zotero_key)
        except HTTPException:
            raise
        except (sqlite3.Error, OSError) as exc:
            raise _storage_http_error(exc) from None
        if normalize_doi(article.doi) is None:
            raise HTTPException(422, {"code": "pdf_fetch_doi_missing",
                                      "detail": "该文献缺少可用的 DOI，无法自动获取全文。"})
        if existing is not None and not overwrite:
            # 不静默覆盖：已上传的全文可能有人工标注的高亮/笔记，先让用户裁决。
            raise HTTPException(409, {
                "code": "pdf_fetch_would_replace",
                "detail": "该文献已有全文 PDF，继续将覆盖既有文件。",
                "existing": {"sha256": existing.sha256, "n_pages": existing.n_pages,
                             "filename": existing.filename, "added_at": existing.added_at},
            })
        try:
            result = _fetch_one(db, zotero_key, article.to_dict(),
                                settings=settings, pdfs_dir=pdfs_dir_for(info),
                                trace_context=trace_context_for(info, user, "pdf_fetch"),
                                overwrite=overwrite)
        except OutboundError as exc:
            raise _outbound_http_error(exc, settings) from None
        except sqlite3.IntegrityError:
            raise HTTPException(400, {"code": "pdf_fetch_article_missing",
                                      "detail": "文献键不存在（请确认已导入该文献）。"}) from None
        except (sqlite3.OperationalError, sqlite3.DatabaseError) as exc:
            raise _storage_http_error(exc) from None
        except OSError as exc:
            raise _storage_http_error(exc) from None
        if result["outcome"] == "invalid":
            raise HTTPException(422, {"code": "pdf_fetch_invalid_pdf",
                                      "detail": result.get("reason") or
                                      "下载内容不是可解析的 PDF。"})
        if result["outcome"] == "blocked":
            raise HTTPException(422, {"code": "pdf_fetch_unsafe_url",
                                      "reason": result.get("reason"),
                                      "detail": "开放获取链接指向本机或内网地址，已拒绝访问。"})
        if result["outcome"] == "unresolvable":
            raise HTTPException(502, {"code": "pdf_fetch_upstream",
                                      "reason": "unresolvable_host",
                                      "detail": "开放获取链接的域名无法解析，请稍后重试。"})
        if result["outcome"] == "trace_conflict":
            raise HTTPException(409, {"code": "pdf_fetch_trace_conflict",
                                      "detail": "同一请求已记录过不同内容，请在界面上重试。"})
        return result

    # -- 任务级批量（显式手动触发；逐条 outcome，单条失败不拖垮整批） ----------

    @app.post("/api/tasks/{task_id}/fulltext/fetch-batch")
    def fetch_batch(task_id: str, body: BatchIn, screener: str | None = None,
                    user: dict = Depends(require_user)) -> dict:
        # r4 复审（高）：准备阶段同样是「可抛点」。任务解析/工作流上下文
        # （migrate、settings 读写）/``screener_db``（每次都跑 init_db）/
        # ``pdfs_dir_for``（mkdir）任一在 try 之外，SQLite 争用
        # （database is locked）或只读/写满磁盘就会变成未捕获异常 → 500。
        # 批量允许整体失败，但必须是**结构化 503 + 可读指引**，不是 500。
        try:
            info = task_lookup(task_id)
            ctx = _context(info.task_id, user)
            settings = _require_enabled(info)
            db = screener_db(info, screener or user["username"])
            pdfs_dir = pdfs_dir_for(info)
        except HTTPException:
            raise               # 404/403/422（未启用、非成员）语义原样保留
        except (sqlite3.Error, OSError) as exc:
            raise _batch_prepare_http_error(exc) from None
        except (ValueError, TypeError) as exc:
            # 准备阶段读到的**持久化状态无法解析**（workflow_state.stages_json
            # 等损坏：json.loads 抛 JSONDecodeError ⊂ ValueError）。这是永久
            # 损坏，必须给修复指引且 retryable=False，不能像库锁那样诱使重试。
            raise _batch_prepare_http_error(exc, corrupt=True) from None
        results: list[dict] = []
        breaker = False
        consecutive_failures = 0
        for key in body.keys:
            if not _KEY_RE.match(key or ""):
                results.append({"zotero_key": key, "outcome": "skipped",
                                "reason": "invalid_key"})
                continue
            if breaker:
                results.append({"zotero_key": key, "outcome": "skipped",
                                "reason": "aborted_upstream_unreachable"})
                continue
            # r3 复审⑤：读库与门禁同样可能在 try 之外抛 SQLite/OSError——
            # 批量要求整体不 5xx，故此处逐条折成 failed/storage。
            try:
                article = db_mod.get_article(db, key)
            except (sqlite3.Error, OSError):
                results.append({"zotero_key": key, "outcome": "failed",
                                "reason": "storage"})
                continue
            if article is None:
                results.append({"zotero_key": key, "outcome": "missing"})
                continue
            try:
                _gate_write(ctx, key)
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, dict) else {}
                results.append({"zotero_key": key, "outcome": "skipped",
                                "reason": detail.get("code") or "workflow_blocked"})
                continue
            except (sqlite3.Error, OSError):
                results.append({"zotero_key": key, "outcome": "failed",
                                "reason": "storage"})
                continue
            except (ValueError, TypeError):
                # 门禁读到损坏的 workflow_state.stages_json（JSONDecodeError）
                # 也只影响这一条：不能把整批（含已 saved 的条目）一起丢掉。
                results.append({"zotero_key": key, "outcome": "failed",
                                "reason": "storage"})
                continue
            try:
                item = _fetch_one(db, key, article.to_dict(), settings=settings,
                                  pdfs_dir=pdfs_dir,
                                  trace_context=trace_context_for(info, user, "pdf_fetch"),
                                  overwrite=body.overwrite)
                consecutive_failures = 0
            except OutboundError as exc:   # 单条网络失败不炸整批；连续失败熔断
                item = {"zotero_key": key, "outcome": "failed",
                        "reason": exc.reason}
                # 熔断按「连续出站失败」计，不限 unreachable/timeout：反代集体
                # 拦截常表现为 upstream_status / bad_response / too_large，同样
                # 说明上游此刻不可用，继续打完 50 条只是把同一错误重复 50 次。
                consecutive_failures += 1
                if consecutive_failures >= CONSECUTIVE_FAILURES_TO_ABORT:
                    breaker = True
            except sqlite3.IntegrityError:
                item = {"zotero_key": key, "outcome": "missing"}
            except (sqlite3.OperationalError, sqlite3.DatabaseError):
                item = {"zotero_key": key, "outcome": "failed",
                        "reason": "storage"}
            except OSError:
                item = {"zotero_key": key, "outcome": "failed",
                        "reason": "storage"}
            except HTTPException as exc:
                # 逐条调用链里唯一会抛 HTTPException 的是 ``trace_context_for``
                # （轨迹库不可用时 503「本次修改尚未保存」）。它只影响当前条目，
                # 不能把已完成的整批结果丢掉；按单条折算，5xx 归 storage。
                detail = exc.detail if isinstance(exc.detail, dict) else {}
                if (exc.status_code or 500) >= 500:
                    item = {"zotero_key": key, "outcome": "failed",
                            "reason": "storage"}
                else:
                    item = {"zotero_key": key, "outcome": "skipped",
                            "reason": detail.get("code") or "workflow_blocked"}
            results.append(item)
        summary = {outcome: sum(1 for r in results if r["outcome"] == outcome)
                   for outcome in ("saved", "no_oa", "failed", "skipped", "missing",
                                   "invalid", "blocked", "unresolvable",
                                   "trace_conflict")}
        return {"results": results, "summary": summary,
                "aborted": breaker,
                "outbound_endpoints": [settings["unpaywall_base_url"],
                                       settings["crossref_base_url"]]}

    # -- Crossref 元数据只读建议（绝不写库；采纳走既有更新路径） ---------------

    @app.get("/api/tasks/{task_id}/fulltext/{zotero_key}/metadata-suggestions")
    def metadata_suggestions(task_id: str, zotero_key: str,
                             screener: str | None = None,
                             user: dict = Depends(require_user)) -> dict:
        if not _KEY_RE.match(zotero_key or ""):
            raise HTTPException(400, "非法文献键。")
        # 同族缺陷：``_context`` 同样在 try 之外（协作库/迁移可抛；损坏的
        # workflow_state.stages_json 会抛 JSONDecodeError ⊂ ValueError）。
        try:
            info = task_lookup(task_id)
            _context(info.task_id, user)
            settings = _require_enabled(info)
        except HTTPException:
            raise
        except (sqlite3.Error, OSError) as exc:
            raise _storage_http_error(exc) from None
        except (ValueError, TypeError):
            raise _corrupt_state_http_error() from None
        try:
            db = screener_db(info, screener or user["username"])
            article = db_mod.get_article(db, zotero_key)
        except (sqlite3.Error, OSError) as exc:
            raise _storage_http_error(exc) from None
        if article is None:
            raise HTTPException(404, {"code": "pdf_fetch_article_missing",
                                      "detail": "该文献键在当前筛选库中不存在。"})
        try:
            payload = _metadata_suggestions(article.to_dict(), settings)
        except OutboundError as exc:
            raise _outbound_http_error(exc, settings) from None
        except (sqlite3.OperationalError, sqlite3.DatabaseError) as exc:
            raise _storage_http_error(exc) from None
        payload["read_only"] = True
        payload["note"] = ("建议仅供预览，不会写入数据库；采纳请通过既有的文献编辑/更新路径完成并留痕。")
        return payload
