"""Co-Bookshelf 协同文献筛选 —— 离线双盲文献筛选系统核心包。"""

from pathlib import Path
import sys


def _app_version() -> str:
    roots = [Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))]
    roots.append(Path(__file__).resolve().parents[1])
    for root in roots:
        try:
            value = (root / "VERSION").read_text(encoding="ascii").strip()
        except OSError:
            continue
        if value:
            return value
    return "0.0.0"


__version__ = _app_version()
