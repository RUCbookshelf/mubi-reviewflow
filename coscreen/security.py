"""安全工具 —— 用户输入触碰文件系统路径的统一防线（安全加固 §4）。

``safe_path`` 是所有「用户可控字符串 -> 服务器文件路径」拼装的强制关卡：

- 绝对路径、盘符、``..`` 上跳在 resolve 后一律落在 ``base_dir`` 之外 -> ValueError；
- 符号链接逃逸由 ``Path.resolve()`` 揭穿（真实目标越界即拒绝）；
- 合法输入返回 resolve 后的绝对路径（存在与否均可，适用于"即将创建"的文件）。

``sanitize_component`` 在**入库/落盘前**把任务名和筛选员名白名单清洗为
安全分量（第一道）；``safe_path`` 在**路径拼装后**
验证真实目标没有越出基准目录（第二道，防御性回归护栏）。两者叠加，
任何一层被未来改动削弱时另一层仍兜底。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

__all__ = ["safe_path", "sanitize_component"]

_SAFE_CHARS = re.compile(r"[0-9A-Za-z_\-.\u4e00-\u9fff\u3400-\u4dbf]")


def _clean(value: str) -> str:
    """白名单清洗：ASCII/数字/连字符/汉字，或任意 Unicode 字母（L* 类）。

    保留全部字母类字符（西里尔/假名/谚文等）是双盲正确性的前提：若全部
    清洗为空，不同筛选员会落入共享 fallback slug、共用同一个 `{slug}.db`，
    双盲失效且数据互相覆盖。
    """
    return "".join(
        ch for ch in (value or "").strip()
        if _SAFE_CHARS.fullmatch(ch) or unicodedata.category(ch).startswith("L")
    ).strip(".")


def sanitize_component(value: str, fallback: str) -> str:
    """清洗任务名或筛选员名，得到安全的单个文件名分量。

    清洗结果为空时回退 ``fallback`` 并追加原名的 sha256 前 8 位，保证
    不同原名回退后仍互不相同（fallback 碰撞即筛选员库碰撞）。
    """
    raw = (value or "").strip()
    cleaned = _clean(raw)
    if cleaned:
        return cleaned[:64]
    cleaned = _clean(fallback or "")[:55]
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"{cleaned or '_'}_{digest}"


def safe_path(base_dir: str | Path, user_input: str | Path) -> Path:
    """把 ``user_input`` 相对 ``base_dir`` 拼装并验证不越界，返回安全绝对路径。

    - ``user_input`` 必须是相对路径（绝对路径直接 ValueError——用户输入
      永远不该指定服务器绝对路径）；
    - 拼装结果经 ``resolve()``（解析符号链接与 ``..``）后必须仍位于
      ``base_dir`` 的真实路径之内（含 ``base_dir`` 本身）；
    - 越界（路径上跳、符号链接逃逸）抛 ``ValueError``（中英双语信息）；
    - 文件/目录是否存在不检查——调用方随后自行创建或写入。

    典型用法::

        out = safe_path(out_dir, sanitize_download_name(filename))
        out.write_bytes(payload)
    """
    base = Path(base_dir).resolve()
    candidate = Path(user_input)
    if candidate.is_absolute():
        raise ValueError(
            f"非法路径（不允许绝对路径）: {user_input!r} | "
            f"Illegal path (absolute paths are not allowed): {user_input!r}"
        )
    resolved = (base / candidate).resolve()
    if resolved != base and base not in resolved.parents:
        raise ValueError(
            f"非法路径（越出基准目录）: {user_input!r} | "
            f"Illegal path (escapes the base directory): {user_input!r}"
        )
    return resolved
