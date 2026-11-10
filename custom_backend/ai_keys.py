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

__all__ = [
    "DECISION_DEFAULT_BASE_URL",
    "DEFAULT_BASE_URL",
    "KEY_KINDS",
    "KIND_DECISION",
    "KIND_LLM",
    "KeyShapeError",
    "base_url_of",
    "delete_personal_key",
    "delete_shared_key",
    "env_key",
    "keys_dir",
    "kind_of",
    "load_personal_key",
    "load_shared_key",
    "mask_key",
    "personal_path",
    "save_personal_key",
    "save_shared_key",
    "shared_path",
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


def _clean_base_url(base_url: str | None, *, kind: str = KIND_LLM) -> str:
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
    # B9 F12/R-6 缓解：默认阻断链路本地与云元数据地址（含 IPv6 映射/十进制 IPv4/
    # 尾点等字面变体；不做 DNS 解析——域名绕过按 accept 披露，见 docs）。
    # loopback 保留——本地模型是一期卖点。
    host = (parts.hostname or "").strip().strip("[]").lower().rstrip(".")
    if host in ("metadata.google.internal", "metadata.goog",
                "100.100.100.100", "100.100.100.200"):
        raise KeyShapeError("base_url 不允许指向链路本地/云元数据地址。")
    addr = _parse_ip_literal(host)
    if addr is not None:
        mapped = getattr(addr, "ipv4_mapped", None)  # ::ffff:a.b.c.d -> 检查内层 v4
        target = mapped if mapped is not None else addr
        if not target.is_loopback and (
                target.is_link_local
                or (target.version == 6 and target in _ULA_V6)
                or (target.version == 4 and target in _LINK_LOCAL_V4)):
            raise KeyShapeError("base_url 不允许指向链路本地/云元数据地址。")
    if len(text) > 500:
        raise KeyShapeError("base_url 过长（上限 500 字符）。")
    return text.rstrip("/")


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


def base_url_of(record: dict | None) -> str:
    """从钥匙记录取规范 base_url（缺省按记录的 kind 回落对应官方端点）。"""
    record = record or {}
    return _clean_base_url(record.get("base_url"), kind=kind_of(record))


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
          model: str | None, kind: str | None = None) -> dict:
    previous = _load(path)
    cleaned_kind = _clean_kind(kind)
    record = {
        "provider": _clean_provider(provider),
        "key": _clean_key(key),
        "base_url": _clean_base_url(base_url, kind=cleaned_kind),
        "model": _clean_model(model),
        "kind": cleaned_kind,
        "added_at": previous.get("added_at") if previous else _now_iso(),
        "last_used_at": previous.get("last_used_at") if previous else None,
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
                      kind: str | None = None,
                      data_root: Path | None = None) -> dict:
    """保存/覆盖本人钥匙（PUT 单向写入，无读取明文路径）。"""
    return _save(personal_path(user_id, data_root), provider=provider, key=key,
                 base_url=base_url, model=model, kind=kind)


def load_personal_key(user_id: int, data_root: Path | None = None) -> dict | None:
    """读本人钥匙记录（明文仅限服务进程内部使用）。"""
    return _load(personal_path(user_id, data_root))


def delete_personal_key(user_id: int, data_root: Path | None = None) -> bool:
    return _delete(personal_path(user_id, data_root))


# ---------------------------------------------------------------------------
# 任务共享钥匙（owner 配置，成员只见元数据）
# ---------------------------------------------------------------------------

def save_shared_key(task_id: str, *, provider: str, key: str,
                    base_url: str | None = None, model: str | None = "",
                    kind: str | None = None,
                    data_root: Path | None = None) -> dict:
    return _save(shared_path(task_id, data_root), provider=provider, key=key,
                 base_url=base_url, model=model, kind=kind)


def load_shared_key(task_id: str, data_root: Path | None = None) -> dict | None:
    return _load(shared_path(task_id, data_root))


def delete_shared_key(task_id: str, data_root: Path | None = None) -> bool:
    return _delete(shared_path(task_id, data_root))


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
        "added_at": None,
        "last_used_at": None,
    }


def touch_last_used(path: Path) -> None:
    """成功出站后更新 last_used_at（读改写 + 原子替换；失败静默）。"""
    record = _load(path)
    if record is None:
        return
    record["last_used_at"] = _now_iso()
    try:
        _atomic_write_0600(path, record)
    except (OSError, ValueError):
        return
