"""Zotero CSV 导出解析器 —— pandas 读取，UTF-8（含 BOM），列名大小写不敏感（见 SPEC.md §3）。

缺失字段一律映射为 ""（不是 NaN）；解析失败抛 ParseError，消息携带行号。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

from coscreen.models import Article, compute_content_hash
from coscreen.normalize import (
    build_zotero_key,
    extract_year,
    normalize_authors,
    normalize_doi,
    normalize_title,
)


class ParseError(Exception):
    """解析失败异常：文件级错误（不存在/非合法 CSV）与行级错误均携带定位信息。"""


# 规范化表头（小写、压缩空白）-> 内部字段名；涵盖 SPEC §3 的 Zotero 列名映射
_COLUMN_ALIASES: dict[str, str] = {
    "key": "zotero_key",
    "item type": "item_type",
    "title": "title",
    "creator": "authors",
    "publication title": "journal",
    "publication year": "year",
    "date": "date",
    "doi": "doi",
    "abstract note": "abstract",
    "url": "url",
}

# 字段级必需列（内部字段名）：缺 Title 则整个文件无法使用
_REQUIRED_FIELDS: tuple[str, ...] = ("title",)


def _normalize_header(name: str) -> str:
    """表头规范化：压缩内部空白、去首尾空白、小写。"""
    return " ".join(str(name).split()).lower()


def _build_column_map(columns: list[str]) -> dict[str, str]:
    """内部字段名 -> 原始列名；同名列取首个，保证确定性。"""
    colmap: dict[str, str] = {}
    for col in columns:
        field = _COLUMN_ALIASES.get(_normalize_header(col))
        if field is not None:
            colmap.setdefault(field, col)
    return colmap


def _row_getter(row: dict, colmap: dict[str, str]):
    """返回按内部字段名取值的取值函数（缺失列/缺失值一律返回 ""）。"""

    def get(field: str) -> str:
        original = colmap.get(field)
        if original is None:
            return ""
        value = row.get(original, "")
        return "" if value is None else str(value).strip()

    return get


def parse_csv(path: str | Path) -> list[Article]:
    """解析 Zotero CSV 导出，返回与数据行数一致的 Article 列表（不去重）。"""
    p = Path(path)
    if not p.is_file():
        raise ParseError(f"文件不存在: {p}")
    source_digest = hashlib.sha256(p.read_bytes()).hexdigest()
    try:
        frame = pd.read_csv(p, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    except (pd.errors.ParserError, pd.errors.EmptyDataError, ValueError) as exc:
        raise ParseError(f"CSV 解析失败（{p.name}）: {exc}") from exc

    colmap = _build_column_map(list(frame.columns))
    missing = [field for field in _REQUIRED_FIELDS if field not in colmap]
    if missing:
        raise ParseError(
            f"CSV 缺少必需列 Title（实际表头: {list(frame.columns)}）"
        )

    articles: list[Article] = []
    for idx, row in enumerate(frame.to_dict("records")):
        get = _row_getter(row, colmap)

        title = normalize_title(get("title"))
        doi = normalize_doi(get("doi"))
        key_raw = get("zotero_key")

        # 无 Key 且无 DOI/标题（无法构建 content_hash）的行视为损坏数据
        if not key_raw and not title and not doi:
            raise ParseError(
                f"CSV 第 {idx + 2} 行数据缺少 Key、Title 与 DOI，无法构建唯一标识"
            )

        year = extract_year(get("year"))
        if year is None:
            year = extract_year(get("date"))

        content_hash = compute_content_hash(doi, title)
        zotero_key = build_zotero_key(row, content_hash)
        raw = dict(row)
        if not key_raw:
            raw["_generated_key"] = True
            raw["_fallback_key"] = zotero_key
            raw["_fallback_id"] = f"{source_digest}:{idx + 2}"
        articles.append(
            Article(
                zotero_key=zotero_key,
                item_type=get("item_type"),
                title=title,
                authors=normalize_authors(get("authors")),
                journal=get("journal"),
                year=year,
                doi=doi,
                abstract=get("abstract"),
                url=get("url"),
                source_format="csv",
                raw=raw,
                content_hash=content_hash,
            )
        )
    return articles
