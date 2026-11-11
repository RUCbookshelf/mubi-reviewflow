"""Public GitHub release lookup for the desktop update notice."""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from fastapi import HTTPException

_REPOSITORY = "RUCbookshelf/mubi-reviewflow"
_CACHE_SECONDS = 900
#: 响应体读取上限（sec29 F-1②）：无上限的 ``json.load(response)`` 可被恶意/
#: 动持上游用超大 body 撑内存。对齐 ``coscreen.notify.MAX_RESPONSE_BYTES``
#: 的 64KiB 有界读取口径，放宽到 256KiB——GitHub Release 说明正文可较长，
#: 256KiB 仍是有界内存且远超真实 release 元数据体量。
_MAX_RELEASE_JSON_BYTES = 256 * 1024
#: 出站短超时（与既有 5s 一致，显式化便于引用）。
_TIMEOUT_SECONDS = 5.0

_cache_lock = threading.Lock()
_cache: tuple[float, dict] | None = None


class _NoRedirectHandler(HTTPRedirectHandler):
    """拒绝跟随 3xx（sec29 F-1①）：默认 opener 会跟跳，30x 可把请求带往
    未预期主机。先例：``pdf_fetch_api._NoRedirectHandler``、
    ``coscreen.notify._NoRedirectHandler``（2.1.4/2.1.5 安全收紧项同款）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def _build_opener():
    """构造出站 opener（**显式空 ProxyHandler** + 拒重定向）。

    sec29 F-1①：``urlopen`` 用的默认 opener ①跟随重定向；②构建期读
    ``http(s)_proxy`` 环境变量——企业/校园网普遍预设代理，会把更新检查请求
    整包发往代理主机。先例：``pdf_fetch_api._build_opener``（:245-256）、
    ``notify._build_opener``（:125-135）。独立成工厂便于测试在代理环境下
    重建断言 handler 组成（模块级 ``_OPENER`` 在用例里会被受控桩替换）。
    """
    return build_opener(ProxyHandler({}), _NoRedirectHandler)


_OPENER = _build_opener()


def current_version() -> str:
    """Read the release version bundled beside the app sources."""
    candidates = []
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        candidates.append(Path(frozen_root) / "VERSION")
    candidates.append(Path(__file__).resolve().parents[1] / "VERSION")
    for path in candidates:
        try:
            value = path.read_text(encoding="ascii").strip()
        except OSError:
            continue
        if re.fullmatch(r"\d+\.\d+\.\d+(?:\.\d+)?", value):
            return value
    return "0.0.0"


def _version_tuple(value: str) -> tuple[int, ...]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?", value.strip())
    return tuple(int(part or 0) for part in match.groups()) if match else (0, 0, 0, 0)


def _platform_asset(assets: list[dict]) -> dict | None:
    if sys.platform == "win32":
        expected = "reviewflow-setup-x64.exe"
    elif sys.platform == "darwin":
        expected = "reviewflow.dmg"
    elif sys.platform.startswith("linux"):
        expected = "reviewflow-x86_64.appimage"
    else:
        return None
    return next((asset for asset in assets
                 if str(asset.get("name", "")).lower() == expected), None)


def _release_shape_ok(release: object) -> bool:
    """上游形状闸（sec29 F-1③）：顶层必须 dict；``assets`` 缺失视为空列表，
    **存在则**必须是 list-of-dict。MITM/恶意上游回 ``[]`` / 字符串 / 混入
    非字典条目时按「无更新」处理，绝不让 ``.get`` 抛 AttributeError→500。"""
    if not isinstance(release, dict):
        return False
    assets = release.get("assets")
    if assets is None:
        return True
    return isinstance(assets, list) and all(isinstance(item, dict) for item in assets)


def _no_update_payload(local_version: str) -> dict:
    """形状异常时的「无更新」兜底载荷（不 500、不缓存坏结果）。"""
    return {
        "current_version": local_version,
        "latest_version": local_version,
        "has_update": False,
        "release_name": "",
        "release_notes": "",
        "published_at": None,
        "release_url": None,
        "asset_url": None,
        "asset_name": "",
    }


def _http_url_or_none(value: object) -> str | None:
    """只回传 http/https URL（sec29 F-2）：``release_url`` / ``asset_url`` 会被
    前端 ``window.open`` 直开，``javascript:`` / ``file:`` / ``data:`` 等任意
    scheme 一律置 None；空/畸形 URL 同样置 None。"""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parts = urlsplit(text)
    except ValueError:                     # 'http://[::1' 等畸形输入
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    if any(ch.isspace() or ord(ch) < 0x20 for ch in text):
        return None                        # 空白/控制字符不得进 window.open
    return text


def check_latest_release() -> dict:
    """Return cached latest-release metadata and the matching platform download."""
    global _cache
    now = time.monotonic()
    with _cache_lock:
        if _cache and now - _cache[0] < _CACHE_SECONDS:
            return dict(_cache[1])

    request = Request(
        f"https://api.github.com/repos/{_REPOSITORY}/releases/latest",
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "ReviewFlow-update-check"},
    )
    try:
        with _OPENER.open(request, timeout=_TIMEOUT_SECONDS) as response:
            raw = response.read(_MAX_RELEASE_JSON_BYTES + 1)   # 有界读取（sec29 F-1②）
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        # 拒重定向后 30x 也走 HTTPError → 503（绝不跟跳）。
        raise HTTPException(503, "Could not check GitHub releases. Please try again later.") from exc
    if len(raw) > _MAX_RELEASE_JSON_BYTES:
        raise HTTPException(503, "GitHub release response exceeded the read limit.")
    try:
        release = json.loads(raw.decode("utf-8", "replace"))
    except ValueError as exc:              # json.JSONDecodeError ⊂ ValueError
        raise HTTPException(503, "Could not check GitHub releases. Please try again later.") from exc

    local_version = current_version()
    if not _release_shape_ok(release):
        # 形状异常按「无更新」降级（sec29 F-1③）；坏结果不进缓存。
        return _no_update_payload(local_version)

    tag = str(release.get("tag_name", ""))
    remote_version = _version_tuple(tag)
    asset = _platform_asset(release.get("assets") or [])
    data = {
        "current_version": local_version,
        "latest_version": tag.lstrip("v"),
        "has_update": remote_version > _version_tuple(local_version),
        "release_name": str(release.get("name") or tag),
        "release_notes": str(release.get("body") or ""),
        "published_at": release.get("published_at"),
        "release_url": _http_url_or_none(release.get("html_url")),
        "asset_url": _http_url_or_none(asset.get("browser_download_url")) if asset else None,
        "asset_name": str(asset.get("name") or "") if asset else "",
    }
    with _cache_lock:
        _cache = (time.monotonic(), data)
    return dict(data)
