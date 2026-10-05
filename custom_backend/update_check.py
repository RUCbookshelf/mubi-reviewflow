"""Public GitHub release lookup for the desktop update notice."""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import HTTPException

_REPOSITORY = "RUCbookshelf/mubi-reviewflow"
_CACHE_SECONDS = 900
_cache_lock = threading.Lock()
_cache: tuple[float, dict] | None = None


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
        if re.fullmatch(r"\d+\.\d+\.\d+", value):
            return value
    return "0.0.0"


def _version_tuple(value: str) -> tuple[int, ...]:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", value)
    return tuple(map(int, match.groups())) if match else (0, 0, 0)


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
        with urlopen(request, timeout=5) as response:
            release = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise HTTPException(503, "Could not check GitHub releases. Please try again later.") from exc

    tag = str(release.get("tag_name", ""))
    remote_version = _version_tuple(tag)
    local_version = current_version()
    asset = _platform_asset(release.get("assets") or [])
    data = {
        "current_version": local_version,
        "latest_version": tag.lstrip("v"),
        "has_update": remote_version > _version_tuple(local_version),
        "release_name": str(release.get("name") or tag),
        "release_notes": str(release.get("body") or ""),
        "published_at": release.get("published_at"),
        "release_url": str(release.get("html_url") or ""),
        "asset_url": str(asset.get("browser_download_url") or "") if asset else "",
        "asset_name": str(asset.get("name") or "") if asset else "",
    }
    with _cache_lock:
        _cache = (time.monotonic(), data)
    return dict(data)
