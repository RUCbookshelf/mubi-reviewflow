"""文献导入解析器包 —— 统一入口 parse_file 与格式嗅探 sniff_format（见 SPEC.md §3）。

- parse_file(path)：按扩展名分发（.csv -> parse_csv；.ris/.txt -> parse_ris），失败抛 ParseError。
- sniff_format(path)：返回 "csv" | "ris"，扩展名 + 内容首行启发式双重判定。
"""

from __future__ import annotations

import re
from pathlib import Path

from coscreen.models import Article
from coscreen.parsers.csv_parser import ParseError, parse_csv
from coscreen.parsers.ris_parser import parse_ris

# RIS 首行形如 "TY  - JOUR"：两字母大写标签 + 空白 + "- " + 值
_RIS_FIRST_LINE_RE = re.compile(r"^[A-Z][A-Z0-9]\s+-\s+\S")

__all__ = ["ParseError", "parse_csv", "parse_file", "parse_ris", "sniff_format"]


def sniff_format(path: str | Path) -> str:
    """嗅探文件格式：返回 "csv" 或 "ris"。

    优先内容启发式（首个非空行匹配 RIS 标签模式），再按扩展名；
    两者均无法判定时，按首行是否含逗号兜底。
    """
    p = Path(path)
    ext = p.suffix.lower()

    first_line = ""
    try:
        with open(p, encoding="utf-8-sig", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    first_line = line
                    break
    except OSError:
        first_line = ""

    if _RIS_FIRST_LINE_RE.match(first_line):
        return "ris"
    if ext == ".csv":
        return "csv"
    if ext in (".ris", ".txt", ".enw"):
        return "ris"
    return "csv" if "," in first_line else "ris"


def parse_file(path: str | Path) -> list[Article]:
    """按格式分发解析；文件不存在或解析失败时抛 ParseError（消息含定位信息）。"""
    p = Path(path)
    if not p.is_file():
        raise ParseError(f"文件不存在: {p}")
    fmt = sniff_format(p)
    if fmt == "csv":
        return parse_csv(p)
    return parse_ris(p)
