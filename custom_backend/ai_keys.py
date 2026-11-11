"""AI 钥匙保险柜（BYOK）：个人/任务共享 API 钥匙的唯一落盘点（B4 §0 R2 存放终案）。

- 位置：``{DATA_DIR}/.ai_keys/``（.jwt_secret 先例推广为目录，绝不进任务目录，
  因此天然不进任务导出包/备份包——B5a 隔离契约第三条）；
- 文件：``personal_{user_id}.json``（仅本人 JWT 可 PUT/DELETE）、
  ``shared_{task_id}.json``（仅任务 owner 可 PUT/DELETE）；
- 写法：``custom_backend/auth.py`` 的 0600 + 独占/临时文件 + ``os.replace``
  原子写（Windows 下 0600 语义弱化为尽力而为，README 隐私说明如实披露）；
- 红线：钥匙明文只在本模块与出站客户端内部存在，绝不经日志、返回值、
  轨迹载荷或异常文本外泄；对外只回掩码（``***abcd`` 式，仅尾 4 位）；
- 环境变量覆盖：``REVIEWFLOW_AI_KEY``（服务器管理员模式，B4 §2）。
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from custom_backend.auth import data_dir

#: base_url 出站缓解用的地址段（B9 R-6）：IPv6 唯一本地与 IPv4 链路本地（169.254/16）。
_ULA_V6 = ipaddress.IPv6Network("fc00::/7")
_LINK_LOCAL_V4 = ipaddress.IPv4Network("169.254.0.0/16")

#: sec29 F-5①：RFC1918 私网段与 RFC 6598 CGNAT 段（100.64.0.0/10，阿里云
#: 元数据面 100.100.100.200 亦在其中——Python ``is_private`` 对 CGNAT 为假，
#: 与 pdf_fetch_api._CGNAT_V4 / notify._CGNAT 同一常量口径）。
_RFC1918_V4 = tuple(ipaddress.IPv4Network(net) for net in
                    ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
_CGNAT_V4 = ipaddress.IPv4Network("100.64.0.0/10")

#: sec29 F-5①：字面 IP / DNS 复核的被拦原因 → 用户可读消息（422 回显）。
_BASE_URL_BLOCK_MESSAGES = {
    "metadata_host": "base_url 不允许指向链路本地/云元数据地址。",
    "link_local": "base_url 不允许指向链路本地/云元数据地址。",
    "ula": "base_url 不允许指向链路本地/云元数据地址。",
    "private": "base_url 不允许指向内网地址（RFC1918/CGNAT）。",
    "unspecified": "base_url 不允许指向未指定地址（0.0.0.0/::）。",
    "loopback": "base_url 不允许指向回环地址（服务器部署；桌面模式可配置本地 Ollama）。",
}

__all__ = [
    "BaseUrlResolveError",
    "DECISION_DEFAULT_BASE_URL",
    "DEFAULT_BASE_URL",
    "FORMAT_ANTHROPIC",
    "FORMAT_OPENAI_CHAT",
    "FORMAT_OPENAI_RESPONSES",
    "FORMAT_SYSTEMONE",
    "KEY_FORMATS",
    "KEY_KINDS",
    "KIND_DECISION",
    "KIND_LLM",
    "KeyShapeError",
    "base_url_of",
    "default_format_for_kind",
    "delete_personal_key",
    "delete_shared_key",
    "env_key",
    "format_of",
    "keys_dir",
    "kind_of",
    "load_personal_key",
    "load_shared_key",
    "mask_key",
    "outcome_path_for",
    "personal_path",
    "personal_slot_path",
    "pinned_outbound_base_url",
    "record_outcome",
    "save_personal_key",
    "save_shared_key",
    "shared_path",
    "shared_slot_path",
    "touch_last_used",
]

#: OpenAI 兼容端点的默认 base_url（用户可改填 DeepSeek/月之暗面/本地 Ollama 等）。
DEFAULT_BASE_URL = "https://api.openai.com/v1"

#: decision 钥匙的默认 base_url：官方 hosted Jev（systemone）端点。**缺省必须
#: 按 kind 区分落盘**（评审 BLOCKER：此前 decision 钥匙不填 base_url 会被写成
#: OpenAI 缺省，把 TypeSafe 钥匙发往 api.openai.com）。单一事实源在此，
#: decision_model_adapter 从本模块引用（反向 import 会成环）。
DECISION_DEFAULT_BASE_URL = "https://api.typesafe.ai"

#: 钥匙的模型类型（2026-11-10 用户裁定：Jev 决策模型提前到 2.1.5）。
#: ``llm``=生成式 LLM（OpenAI 兼容 chat/completions，缺省与存量行为不变）；
#: ``decision``=决策模型（Jev 类 systemone 端点，只允许导入复核 AI 助手的
#: duplicate_group 与 merge_conflict 两场景，越界由调用端硬闸拒绝）。
KIND_LLM = "llm"
KIND_DECISION = "decision"
KEY_KINDS = (KIND_LLM, KIND_DECISION)

#: wire 格式（aiux ②·2026-11-12 用户裁定补齐后端双协议）：钥匙记录新增可选
#: ``format`` 字段——同一 kind 下选择出站协议形态。缺省按 kind 落传统映射
#: （llm→openai_chat、decision→systemone），存量记录无此字段读侧同映射，
#: 字节级行为不变。
FORMAT_OPENAI_CHAT = "openai_chat"
FORMAT_OPENAI_RESPONSES = "openai_responses"
FORMAT_ANTHROPIC = "anthropic"
FORMAT_SYSTEMONE = "systemone"
#: 各 kind 允许的格式集合（decision 固定 systemone——Jev 端点唯一协议）。
KEY_FORMATS: dict[str, tuple[str, ...]] = {
    KIND_LLM: (FORMAT_OPENAI_CHAT, FORMAT_OPENAI_RESPONSES, FORMAT_ANTHROPIC),
    KIND_DECISION: (FORMAT_SYSTEMONE,),
}


def default_format_for_kind(kind: str) -> str:
    """kind 的传统缺省格式（存量兼容的单一事实源）。"""
    return FORMAT_SYSTEMONE if kind == KIND_DECISION else FORMAT_OPENAI_CHAT

_KEY_RE = re.compile(r"^[^/\\\x00]{1,4096}$")
_TASK_ID_RE = re.compile(r"^[^/\\\x00]{1,200}$")

# 环境变量覆盖（服务器管理员模式）：存在即视为已配置个人钥匙。
ENV_KEY = "REVIEWFLOW_AI_KEY"
ENV_PROVIDER = "REVIEWFLOW_AI_PROVIDER"
ENV_BASE_URL = "REVIEWFLOW_AI_BASE_URL"
ENV_MODEL = "REVIEWFLOW_AI_MODEL"
ENV_MODEL_KIND = "REVIEWFLOW_AI_MODEL_KIND"


class KeyDeletionError(RuntimeError):
    """钥匙文件删除失败（文件被占用/权限拒绝等）——绝不向用户假报成功（B9 F2）。"""


class KeyShapeError(ValueError):
    """钥匙/服务商参数形状非法（消息可回给用户，不含任何明文）。"""


class BaseUrlResolveError(RuntimeError):
    """出站前 base_url 域名解析失败（sec29b W4②：失败关闭）。

    保存/元数据口径对解析失败仍放行（隔离网络里的合法自托管域名不能当攻击
    拦）；只有**出站侧**（:func:`pinned_outbound_base_url`）把解析失败升级为
    本错误——绝不带着「无法解析」的域名裸发（连接期二次解析可被重绑定到
    服务器回环，复审探针 probe_servfail/conf13 场景 B 实测）。出站层把它映射
    为可重试的结构化 503（DNS 瞬时故障语义），不是配置错误 422。
    """


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _chmod_best_effort(path: Path, mode: int) -> None:
    """POSIX 设权限；Windows 忽略细粒位属正常（尽力而为，B4 §0 R2）。"""
    try:
        path.chmod(mode)
    except OSError:
        pass


def keys_dir(data_root: Path | None = None) -> Path:
    """保险柜目录 ``{data_dir}/.ai_keys``（只在写入时创建）。

    ``data_root`` 由 API 层显式传入（与 main.DATA_DIR 同一绑定）；缺省回落
    环境变量解析（``auth.data_dir``），保证独立调用（脚本/测试）行为一致。
    """
    return (data_root or data_dir()) / ".ai_keys"


def personal_path(user_id: int, data_root: Path | None = None) -> Path:
    if type(user_id) is not int or user_id <= 0:
        raise KeyShapeError("user_id 必须是正整数。")
    return keys_dir(data_root) / f"personal_{user_id}.json"


def shared_path(task_id: str, data_root: Path | None = None) -> Path:
    if not _TASK_ID_RE.match(task_id or ""):
        raise KeyShapeError("task_id 含非法字符。")
    return keys_dir(data_root) / f"shared_{task_id}.json"


def personal_slot_path(user_id: int, kind: str, data_root: Path | None = None) -> Path:
    """A（用户裁定 2026-11-12）：个人钥匙按 kind 分槽——llm 与 decision 并存。

    槽文件与旧扁平文件同目录同格式（仅文件名带 kind 后缀）；读取侧旧文件
    按「kind 匹配则视作该槽」回落（存量用户零迁移零丢失），写入侧落槽文件
    并在成功后中和同 kind 的旧扁平文件（防旧值继续被读到）。
    """
    if kind not in KEY_KINDS:
        raise KeyShapeError("kind 须为 llm 或 decision。")
    if type(user_id) is not int or user_id <= 0:
        raise KeyShapeError("user_id 必须是正整数。")
    return keys_dir(data_root) / f"personal_{user_id}_{kind}.json"


def shared_slot_path(task_id: str, kind: str, data_root: Path | None = None) -> Path:
    """共享钥匙同口径分槽（owner 可为任务同时配 llm 与 decision 两把）。"""
    if kind not in KEY_KINDS:
        raise KeyShapeError("kind 须为 llm 或 decision。")
    if not _TASK_ID_RE.match(task_id or ""):
        raise KeyShapeError("task_id 含非法字符。")
    return keys_dir(data_root) / f"shared_{task_id}_{kind}.json"


def _neutralize_legacy(base_path: Path, kind: str) -> None:
    """槽写入成功后删除同 kind 的旧扁平文件（尽力而为，失败静默——读取侧
    永远槽文件优先，旧文件残留只是不再被读到）。"""
    record = _load(base_path)
    if record is not None and kind_of(record) == kind:
        try:
            base_path.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 字段校验（异常消息绝不回显明文）
# ---------------------------------------------------------------------------

def _clean_provider(provider: str) -> str:
    text = str(provider or "").strip()
    if not 1 <= len(text) <= 50:
        raise KeyShapeError("provider 须为 1~50 个字符。")
    return text


def _clean_key(key: str) -> str:
    text = str(key or "").strip()
    if not 8 <= len(text) <= 4096 or not _KEY_RE.match(text):
        raise KeyShapeError("API key 须为 8~4096 个字符（不含路径分隔符）。")
    return text


def _server_mode() -> bool:
    """服务器部署判定（与 custom_backend.main._server_mode 同一环境口径）。

    回环例外按运行模式分流（sec29 F-5①）：desktop 单机模式放行本地
    Ollama（http://127.0.0.1:11434/v1 是既有卖点）；server 模式下任何被邀请
    成员都可配置 base_url 并触发服务器出站——回环即服务器自身服务面，
    一律拦截。每次现读环境（不缓存），测试可用 mock.patch.dict 钉住模式。
    """
    return os.environ.get("REVIEWFLOW_MODE", "desktop") == "server"


def _resolve_host(host: str, port: int = 443) -> list[str]:
    """DNS 解析缝（seam，sec29 F-5②）：返回地址串；测试以桩替换。

    先例：``pdf_fetch_api._resolve_host`` / ``coscreen.notify._resolve_host``。
    """
    import socket

    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def _embedded_ipv4(host: str) -> "ipaddress.IPv4Address | None":
    """IPv6 低 32 位嵌 IPv4 的形态归一（sec29b W4③）。

    Python 3.11 的 ``ipaddress`` 对 ``::7f00:1``（IPv4 兼容回环）与
    ``64:ff9b::7f00:1``（RFC 6052 NAT64 前缀）判 ``is_global=True``——
    单靠 is_global/原生段判定会把「回环/私网的 IPv6 兼容写法」当公网放行
    （复审探针 probe_rebind §4 实测 ALLOWED）。本函数把两种形态的低 32 位
    映射回 IPv4，供三处判定（ai_keys._address_block_reason、
    pdf_fetch_api._blocked_host_reason / _is_private_address）按内层 IPv4
    再判一次：

    - ``::x.y.z.w``：高 96 位全零且低 32 位非零（``::1`` 等原生回环/未指定
      由调用方的原生判定先行接住，映射只做**追加**判定，不改写原判定）；
    - ``64:ff9b::x.y.z.w``：NAT64 well-known prefix ``64:ff9b::/96``。

    其余形态（含 ``::ffff:a.b.c.d``——由 ``ipv4_mapped`` 属性原生覆盖）
    返回 None。
    """
    try:
        addr = ipaddress.ip_address(str(host).split("%", 1)[0])
    except ValueError:
        return None
    if addr.version != 6:
        return None
    value = int(addr)
    low = value & 0xFFFFFFFF
    if value >> 32 == 0 and low != 0:
        return ipaddress.IPv4Address(low)          # ::x.y.z.w（IPv4 兼容）
    if value >> 96 == 0x64FF9B:
        return ipaddress.IPv4Address(low)          # 64:ff9b::x.y.z.w（NAT64 /96）
    return None


def _address_block_reason(value: str) -> str | None:
    """单个字面 IP 的被拦原因（None=放行）；保存与出站（DNS 复核）共用。

    被拦集合：链路本地（169.254/16、fe80::/10、IPv4 映射形态）、云元数据段
    （CGNAT 100.64/10，含 100.100.100.200）、IPv6 ULA（fc00::/7）、RFC1918
    私网段、未指定地址（0.0.0.0/::——多数协议栈实际连到本机）；回环按运行
    模式例外（desktop 放行本地 Ollama，server 拦截）。
    sec29b W4③：IPv4 兼容 ``::x.y.z.w`` 与 NAT64 ``64:ff9b::x.y.z.w`` 取低
    32 位映射回 IPv4 追加判定（``::7f00:1``≡``127.0.0.1``、
    ``::a9fe:a9fe``≡``169.254.169.254``——原生段判定对二者均为 False）。
    解析不出 IP 返回 None（域名的拦截在 DNS 复核层）。
    """
    try:
        addr = ipaddress.ip_address(str(value).split("%", 1)[0])
    except ValueError:
        return None
    mapped = getattr(addr, "ipv4_mapped", None)      # ::ffff:a.b.c.d → 检查内层 v4
    target = mapped if mapped is not None else addr
    if target.is_loopback:
        return "loopback" if _server_mode() else None
    if target.is_unspecified:
        return "unspecified"
    if target.is_link_local:
        return "link_local"
    if target.version == 6 and target in _ULA_V6:
        return "ula"
    if target.version == 4 and (target in _CGNAT_V4
                                 or any(target in net for net in _RFC1918_V4)):
        return "private"
    if target is addr and target.version == 6:
        embedded = _embedded_ipv4(str(target))
        if embedded is not None:
            return _address_block_reason(str(embedded))
    return None


def _unbracketed_ipv6_authority(parts) -> bool:
    """authority 是否为**未括号 IPv6**（sec29b W4③）。

    ``urlsplit('http://fd00::2/v1')`` 只把 ``fd00`` 当主机（``urlsplit.hostname
    = 'fd00'``，复审探针 probe_unbracketed 实测），字面判定在错误的片段上做，
    ULA/回环拦截整体错位逃逸；而 urllib 连接层仍按 ``fd00::2`` 出站。凡
    authority（去掉 userinfo）含 ≥2 个冒号且不以 ``[`` 开头即按未括号 IPv6
    拒收（合法 IPv4:port / 域名:port 只含 1 个冒号；IPv6 必须走
    ``http://[::1]:11434/v1`` 方括号形式——urlsplit 才能正确取主机）。
    """
    authority = (parts.netloc or "").rpartition("@")[2]
    if authority.startswith("["):
        return False
    return authority.count(":") >= 2


def _dns_pins_and_block(host: str, port: int | None) -> tuple[list[str], bool]:
    """域名 DNS 复核（sec29 F-5② + sec29b W4②）：``(地址表, 解析失败)``。

    解析到内网/元数据/链路本地由调用方按 :func:`_address_block_reason` 判；
    解析失败在此只标记——保存/元数据口径仍放行（隔离网络里的合法自托管
    域名解析不出来不能当攻击拦），**出站侧**（pinned_outbound_base_url）
    对解析失败失败关闭（BaseUrlResolveError，可重试 503），绝不裸发。
    """
    try:
        addresses = _resolve_host(host, port if port else 443)
    except (OSError, ValueError, UnicodeError):
        return [], True
    return list(addresses or []), not (addresses or [])


def _base_url_guard_result(base_url: str | None, *, kind: str,
                           dns_recheck: bool = True
                           ) -> tuple[str, list[str] | None, str | None, bool]:
    """base_url 完整闸（sec29b W4① 单一实现）：``(规范串, 钉扎表, 被拦原因, 解析失败)``。

    - 结构非法（控制字符/非 http(s)/畸形/未括号 IPv6/超长/云元数据主机）直接
      抛 :class:`KeyShapeError`；
    - 字面 IP：钉扎表 = 该 IP 的规范形（十进制 IPv4 归一为点分）；
    - 域名（dns_recheck=True）：**单次**解析，地址表随行返回供连接层钉扎；
      任一地址被拦 → 原因；解析失败 → dns_failed=True（口径见
      :func:`_dns_pins_and_block`）；
    - ``dns_recheck=False``（只读元数据路径，r7 复审#1）：不联网、不钉扎。
    """
    from urllib.parse import urlsplit

    # 空值缺省按钥匙类型回落：decision → hosted Jev；llm → OpenAI 兼容缺省
    # （绝不把 TypeSafe 钥匙的落盘缺省写成 api.openai.com——评审 BLOCKER 修复）。
    fallback = DECISION_DEFAULT_BASE_URL if kind == KIND_DECISION else DEFAULT_BASE_URL
    text = str(base_url or "").strip() or fallback
    # MM-09：C0/C1 控制字符（含 \r\n 头注入尝试）在入库前拒绝——否则会一路放到
    # 出站时才被 http.client 以 InvalidURL 拒绝并冒泡成用户可达 500。
    # （strip() 只去首尾空白，中间的控制字符必须显式检查。）
    if re.search(r"[\x00-\x1f\x7f-\x9f]", text):
        raise KeyShapeError("base_url 不得包含控制字符。")
    try:
        parts = urlsplit(text)
    except ValueError as exc:  # r7 复审（第2轮）：'http://[::1' 等畸形 URL 抛裸 ValueError
        raise KeyShapeError("base_url 不是合法 URL。") from exc
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise KeyShapeError("base_url 必须是 http(s) URL（本地 Ollama 可填 http://127.0.0.1:11434/v1）。")
    # sec29b W4③：未括号 IPv6（http://fd00::2/v1 → urlsplit 主机='fd00' 错位）
    # 一律拒收，提示方括号形式。
    if _unbracketed_ipv6_authority(parts):
        raise KeyShapeError(
            "base_url 的 IPv6 主机必须使用方括号形式（如 http://[::1]:11434/v1）。")
    # B9 F12/R-6 缓解 + sec29 F-5：阻断云元数据/链路本地/ULA 地址（含 IPv6
    # 映射/十进制 IPv4/尾点等字面变体）；sec29 起 RFC1918/CGNAT 私网段同样
    # 阻断，域名再做一次 DNS 复核（解析到内网/元数据/链路本地即拒）。
    # 回环按运行模式例外：desktop 放行本地 Ollama，server 拦截。
    host = (parts.hostname or "").strip().strip("[]").lower().rstrip(".")
    if host in ("metadata.google.internal", "metadata.goog",
                "100.100.100.100", "100.100.100.200"):
        raise KeyShapeError("base_url 不允许指向链路本地/云元数据地址。")
    if len(text) > 500:
        raise KeyShapeError("base_url 过长（上限 500 字符）。")
    literal = _parse_ip_literal(host)
    if literal is not None:
        literal_reason = _address_block_reason(str(literal))
        if literal_reason is not None:
            return text, None, literal_reason, False
        return text, [str(literal)], None, False
    if not dns_recheck:
        return text, None, None, False
    try:
        port = parts.port
    except ValueError:
        port = None
    addresses, dns_failed = _dns_pins_and_block(host, port)
    if dns_failed:
        return text, None, None, True
    for address in addresses:
        address_reason = _address_block_reason(address)
        if address_reason is not None:
            return text, None, address_reason, False
    return text, addresses, None, False


def _clean_base_url(base_url: str | None, *, kind: str = KIND_LLM,
                    dns_recheck: bool = True) -> str:
    """保存/出站/元数据共用的 base_url 规范化（结构校验 + 拦截 + 可选 DNS 复核）。

    sec29b W4②：解析失败在本（保存/元数据）口径仍放行——隔离网络里的合法
    自托管域名不能当攻击拦；**出站侧**改走 :func:`pinned_outbound_base_url`
    （解析失败失败关闭，绝不裸发）。
    """
    text, _pins, reason, _dns_failed = _base_url_guard_result(
        base_url, kind=kind, dns_recheck=dns_recheck)
    if reason is not None:
        raise KeyShapeError(
            _BASE_URL_BLOCK_MESSAGES.get(reason, "base_url 不允许指向内网/链路本地/云元数据地址。"))
    return text.rstrip("/")


def pinned_outbound_base_url(base_url: str | None, *, kind: str = KIND_LLM
                             ) -> tuple[str, list[str]]:
    """出站侧专用（sec29b W4①/W4②）：``(规范 base_url, 钉扎地址表)``。

    - **单次**解析 + 全地址复核，得到的地址表**随行返回**——出站连接层必须
      钉住这张表、对原域名**零次**再解析（堵 check-then-use 重绑定缝：
      guard 答公网、连接期二次解析答回环，复审探针 conf13 场景 A 实测
      Bearer 钥匙直达服务器回环）；
    - 解析被拦 → :class:`KeyShapeError`（配置错误 422 口径，与
      ``_clean_base_url`` 同一消息表）；解析失败 → :class:`BaseUrlResolveError`
      （失败关闭：可重试的结构化降级，出站层映射 503，绝不带着未钉扎的
      域名裸发——W4②）。
    """
    text, pins, reason, dns_failed = _base_url_guard_result(
        base_url, kind=kind, dns_recheck=True)
    if reason is not None:
        raise KeyShapeError(
            _BASE_URL_BLOCK_MESSAGES.get(reason, "base_url 不允许指向内网/链路本地/云元数据地址。"))
    if dns_failed or not pins:
        raise BaseUrlResolveError(
            "base_url 域名暂时无法解析（已失败关闭，未发起出站），请稍后重试。")
    return text.rstrip("/"), list(pins)


def _parse_ip_literal(host: str):
    """把字面 IP（含十进制 IPv4 与 IPv6 映射）解析为 ipaddress 对象；非字面返回 None。"""
    import ipaddress

    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    if host.isdigit():
        import socket

        try:
            return ipaddress.ip_address(socket.inet_ntoa(socket.inet_aton(host)))
        except (OSError, ValueError):
            return None
    return None


def _clean_model(model: str | None) -> str:
    text = str(model or "").strip()
    if len(text) > 100:
        raise KeyShapeError("model 名称过长（上限 100 字符）。")
    return text


def _clean_kind(kind: str | None) -> str:
    """写入侧校验：空=llm（旧客户端缺省）；其余必须在 ``KEY_KINDS`` 内。"""
    text = str(kind or "").strip().lower()
    if not text:
        return KIND_LLM
    if text not in KEY_KINDS:
        raise KeyShapeError("kind 须为 llm 或 decision。")
    return text


def kind_of(record: dict | None) -> str:
    """读取侧归一（绝不抛错）：缺失/大小写/非法值 → ``llm``（存量钥匙兼容）。"""
    text = str((record or {}).get("kind") or "").strip().lower()
    return text if text in KEY_KINDS else KIND_LLM


def _clean_format(format_: str | None, kind: str) -> str:
    """写入侧校验（aiux ②）：空=按 kind 传统缺省；须在该 kind 允许集内。"""
    text = str(format_ or "").strip().lower()
    if not text:
        return default_format_for_kind(kind)
    if text not in KEY_FORMATS[kind]:
        allowed = "、".join(KEY_FORMATS[kind])
        raise KeyShapeError(f"format 须为 {allowed}。")
    return text


def format_of(record: dict | None) -> str:
    """读取侧归一（绝不抛错）：缺失/非法/与 kind 不符 → 该 kind 传统缺省。"""
    record = record or {}
    kind = kind_of(record)
    text = str(record.get("format") or "").strip().lower()
    return text if text in KEY_FORMATS.get(kind, ()) else default_format_for_kind(kind)


def base_url_of(record: dict | None, *, dns_recheck: bool = True) -> str:
    """从钥匙记录取规范 base_url（缺省按记录的 kind 回落对应官方端点）。

    ``dns_recheck=False`` 供只读元数据路径跳过 DNS 复核（r7 复审#1：读取
    路径绝不联网、绝不因环境配置抛错）；保存与出站两处保持缺省复核。
    sec29b W4①：需要**钉扎连接**的出站方（ai_suggest 的 provider 链）改用
    :func:`pinned_outbound_base_url`——那里解析失败失败关闭（W4②）。
    """
    record = record or {}
    return _clean_base_url(record.get("base_url"), kind=kind_of(record),
                           dns_recheck=dns_recheck)


def mask_key(key: str) -> str:
    """掩码：仅尾 4 位可见（``***abcd`` 式，不暴露前缀）；短钥匙整体打码（B9 F22）。"""
    text = str(key or "").strip()
    if len(text) < 9:
        return "***"
    return f"***{text[-4:]}"


# ---------------------------------------------------------------------------
# 原子写（0600 临时文件 + os.replace；参照 auth.py .jwt_secret 与 main.py AL 设置）
# ---------------------------------------------------------------------------

def _atomic_write_0600(path: Path, payload: dict) -> None:
    vault = path.parent
    vault.mkdir(parents=True, exist_ok=True)
    _chmod_best_effort(vault, 0o700)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=vault)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
        _chmod_best_effort(temp, 0o600)
        os.replace(temp, path)
    finally:
        # os.replace 成功后 temp 已不存在；失败时清理半成品。
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass


def _load(path: Path) -> dict | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("key"), str) or not raw.get("provider"):
        return None
    # 存量钥匙（2.1.5 决策模型波之前落盘）没有 kind 字段 → 读作 llm（兼容）。
    if raw.get("kind") not in KEY_KINDS:
        raw["kind"] = KIND_LLM
    return raw


def _save(path: Path, *, provider: str, key: str, base_url: str | None,
          model: str | None, kind: str | None = None,
          format: str | None = None) -> dict:
    previous = _load(path)
    cleaned_kind = _clean_kind(kind)
    record = {
        "provider": _clean_provider(provider),
        "key": _clean_key(key),
        "base_url": _clean_base_url(base_url, kind=cleaned_kind),
        "model": _clean_model(model),
        "kind": cleaned_kind,
        "format": _clean_format(format, cleaned_kind),
        "added_at": previous.get("added_at") if previous else _now_iso(),
        "last_used_at": previous.get("last_used_at") if previous else None,
        # D/H（用户裁定 2026-11-12）：出站健康与用量计数随记录持久化；
        # 覆盖保存（换钥匙）即清零重计。
        "consecutive_failures": previous.get("consecutive_failures", 0) if previous else 0,
        "call_count": previous.get("call_count", 0) if previous else 0,
        "total_tokens": previous.get("total_tokens") or 0 if previous else 0,
    }
    _atomic_write_0600(path, record)
    return record


def _delete(path: Path) -> bool:
    """删除钥匙文件：不存在=幂等成功（False）；删除失败抛 KeyDeletionError（B9 F2）。"""
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise KeyDeletionError(
            f"钥匙文件删除失败（{type(exc).__name__}）：文件可能被占用，请关闭占用程序后重试。") from exc
    return True


# ---------------------------------------------------------------------------
# 个人钥匙（BYOK）
# ---------------------------------------------------------------------------

def save_personal_key(user_id: int, *, provider: str, key: str,
                      base_url: str | None = None, model: str | None = "",
                      kind: str | None = None, format: str | None = None,
                      data_root: Path | None = None) -> dict:
    """保存/覆盖本人钥匙（PUT 单向写入，无读取明文路径）。

    A 双槽：按 kind 落对应槽文件；旧扁平文件同 kind 即在写成功后中和。
    """
    cleaned_kind = _clean_kind(kind)
    record = _save(personal_slot_path(user_id, cleaned_kind, data_root),
                   provider=provider, key=key, base_url=base_url, model=model,
                   kind=cleaned_kind, format=format)
    _neutralize_legacy(personal_path(user_id, data_root), cleaned_kind)
    return record


def load_personal_key(user_id: int, data_root: Path | None = None,
                      *, kind: str | None = None) -> dict | None:
    """读本人钥匙记录（明文仅限服务进程内部使用）。

    给定 kind → 只读该槽（槽文件优先；旧扁平文件 kind 匹配则回落视作该槽，
    存量用户零迁移零丢失）；kind 缺省 → 旧单钥匙视图（llm 槽→decision 槽→
    旧扁平文件），老调用方行为不变。
    """
    if kind is not None:
        cleaned = _clean_kind(kind)
        record = _load(personal_slot_path(user_id, cleaned, data_root))
        if record is not None and kind_of(record) == cleaned:
            return record
        legacy = _load(personal_path(user_id, data_root))
        return legacy if legacy is not None and kind_of(legacy) == cleaned else None
    for candidate in KEY_KINDS:
        record = _load(personal_slot_path(user_id, candidate, data_root))
        if record is not None and kind_of(record) == candidate:
            return record
    return _load(personal_path(user_id, data_root))


def delete_personal_key(user_id: int, data_root: Path | None = None,
                        *, kind: str | None = None) -> bool:
    """删除本人钥匙：给定 kind → 只删该槽（含同 kind 旧扁平文件）；缺省 →
    全删（A 之前的单钥匙语义——老客户端「删除钥匙」按钮）。"""
    removed = False
    if kind is not None:
        cleaned = _clean_kind(kind)
        removed = _delete(personal_slot_path(user_id, cleaned, data_root))
        legacy = _load(personal_path(user_id, data_root))
        if legacy is not None and kind_of(legacy) == cleaned:
            removed = _delete(personal_path(user_id, data_root)) or removed
        return removed
    for candidate in KEY_KINDS:
        removed = _delete(personal_slot_path(user_id, candidate, data_root)) or removed
    removed = _delete(personal_path(user_id, data_root)) or removed
    return removed


# ---------------------------------------------------------------------------
# 任务共享钥匙（owner 配置，成员只见元数据）
# ---------------------------------------------------------------------------

def save_shared_key(task_id: str, *, provider: str, key: str,
                    base_url: str | None = None, model: str | None = "",
                    kind: str | None = None, format: str | None = None,
                    data_root: Path | None = None) -> dict:
    """共享钥匙同口径分槽（owner 可为任务同时配 llm 与 decision 两把）。"""
    cleaned_kind = _clean_kind(kind)
    record = _save(shared_slot_path(task_id, cleaned_kind, data_root),
                   provider=provider, key=key, base_url=base_url, model=model,
                   kind=cleaned_kind, format=format)
    _neutralize_legacy(shared_path(task_id, data_root), cleaned_kind)
    return record


def load_shared_key(task_id: str, data_root: Path | None = None,
                    *, kind: str | None = None) -> dict | None:
    """读共享钥匙（kind 语义同 :func:`load_personal_key`）。"""
    if kind is not None:
        cleaned = _clean_kind(kind)
        record = _load(shared_slot_path(task_id, cleaned, data_root))
        if record is not None and kind_of(record) == cleaned:
            return record
        legacy = _load(shared_path(task_id, data_root))
        return legacy if legacy is not None and kind_of(legacy) == cleaned else None
    for candidate in KEY_KINDS:
        record = _load(shared_slot_path(task_id, candidate, data_root))
        if record is not None and kind_of(record) == candidate:
            return record
    return _load(shared_path(task_id, data_root))


def delete_shared_key(task_id: str, data_root: Path | None = None,
                      *, kind: str | None = None) -> bool:
    """删除共享钥匙：给定 kind → 只删该槽；缺省 → 全删。"""
    removed = False
    if kind is not None:
        cleaned = _clean_kind(kind)
        removed = _delete(shared_slot_path(task_id, cleaned, data_root))
        legacy = _load(shared_path(task_id, data_root))
        if legacy is not None and kind_of(legacy) == cleaned:
            removed = _delete(shared_path(task_id, data_root)) or removed
        return removed
    for candidate in KEY_KINDS:
        removed = _delete(shared_slot_path(task_id, candidate, data_root)) or removed
    removed = _delete(shared_path(task_id, data_root)) or removed
    return removed


# ---------------------------------------------------------------------------
# 环境变量覆盖（服务器管理员模式）
# ---------------------------------------------------------------------------

def env_key() -> dict | None:
    """``REVIEWFLOW_AI_KEY`` 覆盖：存在即返回合成记录（不落盘、不可 DELETE）。"""
    key = os.environ.get(ENV_KEY, "").strip()
    if not key:
        return None
    # r7 复审#1：环境 base_url 不在此清洗——env_key() 被 GET /api/ai/key 等读取
    # 路径调用，控制字符等非法值若在此抛 KeyShapeError 会让读取端点 500；
    # 合法性改由出站前的 base_url_of 校验（各出站点均已映射 422）。
    # r7 复审（第2轮）：model 同理透传——超长等非法值出站时由 provider 拒绝
    # （ProviderError/502），读取路径绝不因环境配置抛错。
    # 缺省语义与 _clean_base_url 一致：空值回落官方端点。
    # kind 与 base_url 的缺省联动：decision 钥匙不设 REVIEWFLOW_AI_BASE_URL 时
    # 缺省必须是 hosted Jev 端点（同 _save 的按 kind 落盘缺省——评审 BLOCKER）。
    kind = (KIND_DECISION if os.environ.get(
        ENV_MODEL_KIND, "").strip().lower() == KIND_DECISION else KIND_LLM)
    fallback = DECISION_DEFAULT_BASE_URL if kind == KIND_DECISION else DEFAULT_BASE_URL
    return {
        "provider": os.environ.get(ENV_PROVIDER, "").strip() or "managed",
        "key": key,
        "base_url": (os.environ.get(ENV_BASE_URL, "").strip() or fallback),
        "model": os.environ.get(ENV_MODEL, "").strip(),
        # kind 与 model/base_url 同理透传归一：非法值读作 llm，读取路径绝不抛错。
        "kind": kind,
        # aiux ②：env 钥匙同口径带缺省 format（不落盘、不可改——env 单钥匙）。
        "format": default_format_for_kind(kind),
        "added_at": None,
        "last_used_at": None,
    }


def touch_last_used(path: Path) -> None:
    """成功出站后更新 last_used_at（读改写 + 原子替换；失败静默）。

    aiux D/H 起改由 :func:`record_outcome` 承担（连败计数+用量计数）；
    本函数保留为最薄兼容层（等价 ok=True、无用量）。
    """
    record_outcome(path, ok=True)


def record_outcome(path: Path, *, ok: bool, usage: dict | None = None) -> None:
    """D/H（用户裁定 2026-11-12）：出站结果记账（读改写+原子替换，失败静默）。

    ok=True：last_used_at 刷新、consecutive_failures 清零、call_count +1、
    usage.total_tokens（数值>0 时）累计；ok=False：仅 consecutive_failures +1。
    只记账已存在的记录——env 钥匙不落盘（无文件）天然不计数。
    """
    record = _load(path)
    if record is None:
        return
    if ok:
        record["last_used_at"] = _now_iso()
        record["consecutive_failures"] = 0
        record["call_count"] = int(record.get("call_count") or 0) + 1
        tokens = (usage or {}).get("total_tokens")
        if isinstance(tokens, (int, float)) and tokens > 0:
            record["total_tokens"] = int(record.get("total_tokens") or 0) + int(tokens)
    else:
        record["consecutive_failures"] = int(record.get("consecutive_failures") or 0) + 1
    try:
        _atomic_write_0600(path, record)
    except (OSError, ValueError):
        return


def outcome_path_for(source: str, *, kind: str, user_id: int,
                     task_id: str | None = None,
                     data_root: Path | None = None) -> Path | None:
    """出站结果记账的路径解析（aiux D/H）：personal/shared 槽文件优先、
    旧扁平文件回落（存量钥匙未重存前记账不丢）；env/未知源 → None（不记账，
    env 本就不落盘）。"""
    cleaned = _clean_kind(kind)
    if source == "personal":
        slot = personal_slot_path(user_id, cleaned, data_root)
        return slot if slot.exists() else personal_path(user_id, data_root)
    if source == "shared" and task_id:
        slot = shared_slot_path(task_id, cleaned, data_root)
        return slot if slot.exists() else shared_path(task_id, data_root)
    return None
