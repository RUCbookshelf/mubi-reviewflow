"""Co-Bookshelf 协同文献筛选 —— 离线双盲文献筛选系统核心包。"""

from pathlib import Path
import sys


def _app_version() -> str:
    roots = [Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))]
    roots.append(Path(__file__).resolve().parents[1])
    for root in roots:
        try:
            # utf-8-sig：编辑器写入的 UTF-8 BOM 不再是致命错误（r9 F1-110：
            # 旧 encoding="ascii" 抛的 UnicodeDecodeError 是 ValueError 子类，
            # 冲出 except OSError，使整包模块级导入即崩）；UnicodeDecodeError
            # 另作兜底，覆盖非 UTF-8 残留字节（如 UTF-16 半个字节）。纯 ASCII
            # VERSION 的读取结果逐字节不变。
            value = (root / "VERSION").read_text(encoding="utf-8-sig").strip()
        except (OSError, UnicodeDecodeError):
            continue
        if value:
            return value
    return "0.0.0"


__version__ = _app_version()
