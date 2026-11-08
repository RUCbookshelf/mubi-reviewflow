"""通知通道抽象（每日打卡提醒）——只发摘要数字，默认关、显式触发。

通道类型（负责人级配置，任务级 ``daily_nudge_channels.json``，默认
``enabled=false``）：

- ``apprise``：Apprise 引擎（**lazy import**；未安装时返回结构化的
  ``channel_unavailable / apprise_missing``，绝不抛错）。Apprise URL 常内嵌
  凭据，因此**只从环境变量读**（配置里存的是变量名 ``url_env``）。
- ``serverchan``：Server 酱，stdlib ``urllib``；sendkey 只从环境变量读
  （配置里存 ``send_key_env``）。
- ``webhook``：通用 JSON POST；载荷**只有数字字段**（键名固定、值转 int），
  显式 UA、5s 短超时、64KiB 读取上限、拒绝跟随重定向、出站前校验 host
  为公网 http/https（拒绝 localhost/环回/私网/保留/CGNAT）。
- ``smtp``：stdlib ``smtplib``；密码只从环境变量读（``password_env``），
  SMTP 主机同样做公网校验（拒绝本机/内网目标）。

安全口径：

- **凭据零落盘**：配置文件只保存环境变量名，不保存 sendkey / SMTP 密码 /
  Apprise URL；示例与测试均不写可用凭据字面量。
- **数据最小化**：出站载荷只包含 ``screened_today / daily_target /
  remaining / streak / longest_streak`` 等整数与事件码/日期数字；绝不包含
  文献题录、摘要、全文、筛选决定或排除理由。
- **失败降级**：任何通道故障都折叠为 ``{"ok": False, "status": ...}``，
  绝不把网络异常抛成 500；错误信息不回显 URL/凭据。

已知边界（契约如实披露）：本波不做定时调度；``send`` 只由负责人显式触发
的测试端点调用，或由运维 cron 在下波接入。我们自有的 webhook/serverchan/
smtp 在连接前做 host 公网校验，但 DNS 校验与真正连接之间存在理论上的
rebinding 窗口（与既有 ERIC 出站实现同口径，靠短超时 + 禁止重定向收窄；
PDF 获取另做了地址钉扎）。Apprise 由第三方引擎按 owner 提供的 URL 自行
投递（其 service 标识不一定是主机名），我们只拦截**字面量**本机/私网
地址，其余传输策略由 Apprise 负责。
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import smtplib
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from email.message import EmailMessage
from typing import Any, Callable, Mapping

__all__ = [
    "CHANNEL_TYPES",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_RESPONSE_BYTES",
    "NotifyError",
    "USER_AGENT",
    "build_summary",
    "send",
    "summary_text",
    "validate_public_hostname",
    "validate_public_url",
]

#: 支持的通道类型（顺序即前端表单顺序）。
CHANNEL_TYPES = ("apprise", "serverchan", "webhook", "smtp")

#: 出站短超时（秒）。
DEFAULT_TIMEOUT_SECONDS = 5.0

#: 单响应读取上限。
MAX_RESPONSE_BYTES = 64 * 1024

#: 显式 User-Agent（与既有出站模块同一口径）。
USER_AGENT = "ReviewFlow/2.1.5 (+daily-nudge)"

#: 载荷白名单：只有这些整数字段允许出站。
SUMMARY_FIELDS = (
    "screened_today",
    "daily_target",
    "remaining",
    "streak",
    "longest_streak",
)

_EVENT_DAILY_REMINDER = 1
_EVENT_TEST = 2

_BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


class NotifyError(Exception):
    """通道级故障（带机器可读 code / reason；消息不含凭据）。"""

    def __init__(self, code: str, *, reason: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.reason = reason


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """拒绝跟随 3xx（防止已校验的公网地址被重定向到内网）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


_OPENER = urllib.request.build_opener(_NoRedirectHandler)


# ---------------------------------------------------------------------------
# 摘要构造（唯一允许出站的数据）
# ---------------------------------------------------------------------------

def build_summary(
    numbers: Mapping[str, object],
    *,
    event_code: int = _EVENT_DAILY_REMINDER,
    day: date | None = None,
) -> dict[str, int]:
    """把状态数字整理成纯整数载荷（webhook 直接序列化它）。

    键固定为 ``event_code / date_yyyymmdd / screened_today / daily_target /
    remaining / streak / longest_streak``，值全部是 ``int``；不认识的键忽略。
    """
    payload: dict[str, int] = {"event_code": int(event_code)}
    if day is not None:
        payload["date_yyyymmdd"] = int(day.strftime("%Y%m%d"))
    for key in SUMMARY_FIELDS:
        try:
            payload[key] = int(numbers.get(key, 0))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            payload[key] = 0
    return payload


def _sanitize_payload(summary: Mapping[str, object]) -> dict[str, int]:
    """只保留白名单键并转 int（webhook 载荷的最后一道闸）。

    ``screened_today / daily_target / remaining / streak / longest_streak``
    恒出现（缺失补 0），保证载荷 schema 固定；``event_code`` /
    ``date_yyyymmdd`` 仅在调用方提供时出现。
    """
    payload: dict[str, int] = {}
    for key in ("event_code", "date_yyyymmdd"):
        if key not in summary:
            continue
        try:
            payload[key] = int(summary[key])  # type: ignore[arg-type]
        except (TypeError, ValueError):
            payload[key] = 0
    for key in SUMMARY_FIELDS:
        try:
            payload[key] = int(summary.get(key, 0))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            payload[key] = 0
    return payload


def summary_text(summary: Mapping[str, int]) -> str:
    """给 apprise/serverchan/smtp 的纯数字摘要文本（无题录/决定）。"""
    return (
        "ReviewFlow daily screening: "
        f"{int(summary.get('screened_today', 0))}/{int(summary.get('daily_target', 0))} screened, "
        f"streak {int(summary.get('streak', 0))}, "
        f"remaining {int(summary.get('remaining', 0))}."
    )


# ---------------------------------------------------------------------------
# 出站 host 校验（SSRF 最小防线）
# ---------------------------------------------------------------------------

def _is_private_address(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return True
    if ip.version == 4:
        return bool(
            ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified or ip in _CGNAT
        )
    return bool(
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
        or ip.is_multicast or ip.is_unspecified
    )


def _resolve_host(host: str, port: int = 443) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def _host_identity(hostname: str | None) -> str:
    return (hostname or "").strip().rstrip(".").lower()


def _check_host_literal(hostname: str) -> str | None:
    host = _host_identity(hostname)
    if not host:
        return "invalid_host"
    if host == "localhost" or host.endswith(_BLOCKED_HOST_SUFFIXES):
        return "private_host"
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return None
    return "private_host" if _is_private_address(host) else None


def validate_public_hostname(
    hostname: str,
    *,
    port: int = 443,
    resolver: Callable[[str, int], list[str]] | None = None,
) -> None:
    """校验主机名为公网可达（拒绝本机/私网/保留/CGNAT/不可解析）。

    抛 :class:`NotifyError`（``private_host`` / ``invalid_host`` /
    ``unresolvable_host``）；成功返回 ``None``。
    """
    reason = _check_host_literal(hostname)
    if reason == "invalid_host":
        raise NotifyError("invalid_host")
    if reason == "private_host":
        raise NotifyError("private_host")
    try:
        addresses = (resolver or _resolve_host)(_host_identity(hostname), port)
    except (OSError, UnicodeError):
        raise NotifyError("unresolvable_host") from None
    if not addresses:
        raise NotifyError("unresolvable_host")
    if any(_is_private_address(str(address)) for address in addresses):
        raise NotifyError("private_host")


def validate_public_url(
    url: object,
    *,
    resolver: Callable[[str, int], list[str]] | None = None,
) -> str:
    """校验出站 URL：只允许 http/https 且 host 为公网；返回规范化 URL。"""
    if not isinstance(url, str) or not url.strip():
        raise NotifyError("invalid_url")
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise NotifyError("invalid_url")
    if parts.username or parts.password:
        raise NotifyError("invalid_url")  # 凭据不得出现在 URL 里
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        raise NotifyError("invalid_url") from None
    validate_public_hostname(parts.hostname, port=port, resolver=resolver)
    return url.strip()


# ---------------------------------------------------------------------------
# 环境变量凭据（只读名字，不落盘值）
# ---------------------------------------------------------------------------

def _env_credential(name: object) -> str:
    env_name = name if isinstance(name, str) else ""
    if not _ENV_NAME_RE.fullmatch(env_name):
        raise NotifyError("credential_missing", reason="invalid_env_name")
    value = os.environ.get(env_name, "")
    if not value:
        raise NotifyError("credential_missing", reason="env_not_set")
    return value


# ---------------------------------------------------------------------------
# HTTP 传输（stdlib urllib；可注入 opener 供测试）
# ---------------------------------------------------------------------------

def _open(request: urllib.request.Request, *, timeout: float,
          opener: Any = None) -> bytes:
    target = opener or _OPENER
    try:
        with target.open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise NotifyError("redirect_refused") from None
        raise NotifyError("http_error", reason=str(int(exc.code))) from None
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
            raise NotifyError("timeout") from None
        raise NotifyError("unreachable") from None
    except TimeoutError:  # 连接后读取阶段的 socket.timeout（Py3.10+ 即 TimeoutError）
        raise NotifyError("timeout") from None
    except OSError as exc:
        if "timed out" in str(exc).lower():
            raise NotifyError("timeout") from None
        raise NotifyError("unreachable") from None
    except Exception:  # noqa: BLE001 - 注入的 opener 可能抛任意异常
        raise NotifyError("channel_error") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise NotifyError("response_too_large")
    return raw


def _json_post(url: str, payload: bytes, headers: dict[str, str], *,
               timeout: float, opener: Any = None) -> bytes:
    request = urllib.request.Request(  # noqa: S310 - host 已通过公网校验
        url, data=payload, headers={"User-Agent": USER_AGENT, **headers},
        method="POST")
    return _open(request, timeout=timeout, opener=opener)


# ---------------------------------------------------------------------------
# 各通道实现
# ---------------------------------------------------------------------------

def _send_webhook(config: Mapping[str, Any], summary: Mapping[str, int], *,
                  timeout: float, opener: Any = None) -> None:
    url = validate_public_url(config.get("url"))
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    token_env = config.get("auth_token_env")
    if token_env:
        headers["Authorization"] = "Bearer " + _env_credential(token_env)
    body = json.dumps(dict(summary), ensure_ascii=False, sort_keys=True).encode("utf-8")
    _json_post(url, body, headers, timeout=timeout, opener=opener)


def _send_serverchan(config: Mapping[str, Any], text: str, *,
                     timeout: float, opener: Any = None) -> None:
    key = _env_credential(config.get("send_key_env"))
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", key):
        raise NotifyError("credential_missing", reason="invalid_send_key")
    url = f"https://sctapi.ftqq.com/{key}.send"
    body = urllib.parse.urlencode({
        "title": "ReviewFlow daily screening",
        "desp": text,
    }).encode("utf-8")
    raw = _json_post(url, body,
                     {"Content-Type": "application/x-www-form-urlencoded"},
                     timeout=timeout, opener=opener)
    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        payload = None
    if isinstance(payload, dict) and payload.get("code") not in (None, 0):
        raise NotifyError("upstream_rejected")


def _send_apprise(config: Mapping[str, Any], text: str, *, timeout: float) -> None:
    url = _env_credential(config.get("url_env"))
    # Apprise URL 的 host 位置随 scheme 而异（discord://<id>/<token> 的 <id>
    # 不是主机名），因此这里只做**字面量**本机/私网地址拦截，不做 DNS：
    # 拦截 json://127.0.0.1、smtp://localhost 之类；公网/非主机形态的 service
    # 标识不受影响。Apprise 引擎自身的传输由其实现负责（契约已披露）。
    authority = url.split("://", 1)[1].split("/", 1)[0] if "://" in url else ""
    host = authority.rsplit("@", 1)[-1]
    if host and not host.startswith("["):
        host = host.split(":", 1)[0]
    if host and _check_host_literal(host) == "private_host":
        raise NotifyError("private_host")
    try:
        import apprise  # lazy import：未安装不影响其余功能
    except Exception:  # noqa: BLE001 - ImportError / 依赖内部错误
        raise NotifyError("channel_unavailable", reason="apprise_missing") from None
    try:
        client = apprise.Apprise()
        if not client.add(url):
            raise NotifyError("credential_missing", reason="apprise_url_invalid")
        if not client.notify(body=text, title="ReviewFlow daily screening"):
            raise NotifyError("upstream_rejected")
    except NotifyError:
        raise
    except Exception:  # noqa: BLE001 - apprise 内部异常一律结构化
        raise NotifyError("channel_error", reason="apprise_failed") from None


def _send_smtp(config: Mapping[str, Any], text: str, *, timeout: float) -> None:
    host = config.get("host")
    if not isinstance(host, str) or not host.strip():
        raise NotifyError("channel_invalid", reason="smtp_host_missing")
    try:
        port = int(config.get("port") or 587)
    except (TypeError, ValueError):
        raise NotifyError("channel_invalid", reason="smtp_port_invalid") from None
    if not 1 <= port <= 65535:
        raise NotifyError("channel_invalid", reason="smtp_port_invalid")
    validate_public_hostname(host.strip(), port=port)
    from_addr = config.get("from_addr")
    to_addr = config.get("to_addr")
    if not isinstance(from_addr, str) or not from_addr.strip():
        raise NotifyError("channel_invalid", reason="smtp_from_missing")
    if not isinstance(to_addr, str) or not to_addr.strip():
        raise NotifyError("channel_invalid", reason="smtp_to_missing")
    username = config.get("username") if isinstance(config.get("username"), str) else ""
    password = _env_credential(config.get("password_env")) if username else ""
    message = EmailMessage()
    message["Subject"] = "ReviewFlow daily screening"
    message["From"] = from_addr.strip()
    message["To"] = to_addr.strip()
    message.set_content(text)
    use_tls = config.get("use_tls", True) is not False
    try:
        with smtplib.SMTP(host.strip(), port, timeout=timeout) as client:
            client.ehlo()
            if use_tls:
                client.starttls()
                client.ehlo()
            if username:
                client.login(username, password)
            client.send_message(message)
    except NotifyError:
        raise
    except (smtplib.SMTPException, OSError):
        raise NotifyError("channel_error", reason="smtp_failed") from None


# ---------------------------------------------------------------------------
# 统一发送入口
# ---------------------------------------------------------------------------

def send(
    config: Mapping[str, Any] | None,
    summary: Mapping[str, int],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    opener: Any = None,
) -> dict[str, Any]:
    """按配置发送一条摘要；**永不抛异常**，结果永远是结构化字典。

    返回 ``{"ok": bool, "status": str, "channel": str, "reason": str|None}``：

    - 未启用 → ``status="disabled"``（调用方随后可映射 409）；
    - Apprise 未安装 → ``status="channel_unavailable", reason="apprise_missing"``；
    - 凭据环境变量缺失 → ``status="credential_missing"``；
    - 网络/上游失败 → ``timeout / unreachable / http_error / redirect_refused``
      / ``upstream_rejected / channel_error``。
    """
    if not isinstance(config, Mapping):
        return {"ok": False, "status": "channel_invalid", "channel": "",
                "reason": "missing_config"}
    channel = config.get("type")
    channel_name = channel if isinstance(channel, str) else ""
    if not config.get("enabled"):
        return {"ok": False, "status": "disabled", "channel": channel_name,
                "reason": None}
    if channel_name not in CHANNEL_TYPES:
        return {"ok": False, "status": "channel_invalid", "channel": channel_name,
                "reason": "unknown_type"}
    payload = _sanitize_payload(summary)
    text = summary_text(payload)
    try:
        if channel_name == "webhook":
            _send_webhook(config, payload, timeout=timeout, opener=opener)
        elif channel_name == "serverchan":
            _send_serverchan(config, text, timeout=timeout, opener=opener)
        elif channel_name == "apprise":
            _send_apprise(config, text, timeout=timeout)
        else:
            _send_smtp(config, text, timeout=timeout)
    except NotifyError as exc:
        return {"ok": False, "status": exc.code, "channel": channel_name,
                "reason": exc.reason}
    except Exception as exc:  # noqa: BLE001 - 最后一道降级闸
        return {"ok": False, "status": "channel_error", "channel": channel_name,
                "reason": type(exc).__name__}
    return {"ok": True, "status": "sent", "channel": channel_name, "reason": None}
