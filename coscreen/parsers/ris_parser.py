"""Zotero/标准 RIS 导出解析器 —— rispy 读取，兼容 UTF-8/GBK 编码（见 SPEC.md §3）。

RIS 作者为列表，经 normalize_authors 以 "; " 连接；记录数与 "ER -" 记录数一致。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import rispy

from coscreen.models import Article, compute_content_hash
from coscreen.normalize import (
    build_zotero_key,
    extract_year,
    normalize_authors,
    normalize_doi,
    normalize_title,
)
from coscreen.parsers.csv_parser import ParseError

# rispy 0.10 将标签映射为可读键名；此处同时兼容可读键名与原始标签两种取值。
# 注意 TI 与 T1 都要覆盖：Zotero RIS 用 T1（rispy 映射为 primary_title），
# Web of Science RIS 用 TI（rispy 映射为 title）——漏掉后者曾导致整库"无标题"。
_TITLE_KEYS = ("primary_title", "title", "T1", "TI")
_JOURNAL_KEYS = ("journal_name", "secondary_title", "alternate_title3", "JO", "JF", "T2")
_YEAR_KEYS = ("year", "PY")
_DATE_KEYS = ("date", "DA")
_DOI_KEYS = ("doi", "DO")
_ABSTRACT_KEYS = ("abstract", "notes_abstract", "AB", "N2")
_URL_KEYS = ("urls", "UR")
_KEY_KEYS = ("id", "ID", "accession_number", "AN")


def _read_text(path: Path) -> str:
    """依次尝试 UTF-8（含 BOM）与 GBK 解码，均失败则以替换模式兜底。"""
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _first(record: dict, keys: tuple[str, ...]) -> str:
    """按候选键顺序取首个非空值；列表值取首个非空元素。"""
    for key in keys:
        value = record.get(key)
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            value = next((item for item in value if str(item).strip()), "")
        text = str(value).strip()
        if text:
            return text
    return ""


def _validate_record_terminators(text: str, filename: str) -> None:
    """预校验 TY/ER 记录边界（U2-P8 + 复审新发现：中部缺 ER 静默吞并后续记录）。

    rispy 只在遇到 ER 时结算记录：末条缺 ER 会静默丢弃该条；中部缺 ER
    会把下一条的标签并入上一条，生成嵌合记录（作者污染、后续整条消失，
    无任何告警）。在交给 rispy 之前按行统计 TY 与 ER，两者不等则拒绝
    整个文件并指出首个未终止记录的行号——宁可显式失败，不可静默丢数据。
    """
    open_ty: list[tuple[int, int]] = []  # (该 TY 的行号, 第几条 TY)
    ty_count = er_count = 0
    for lineno, line in enumerate(text.splitlines(), start=1):
        tag = line.split("-", 1)[0].strip().upper() if "-" in line else ""
        if tag == "TY":
            ty_count += 1
            open_ty.append((lineno, ty_count))
        elif tag == "ER":
            er_count += 1
            if open_ty:
                open_ty.pop()
    if open_ty:
        bad_line, bad_no = open_ty[0]
        raise ParseError(
            f"RIS 解析失败（{filename}）: 共 {ty_count} 个 TY 但只有 {er_count} 个 ER；"
            f"第 {bad_no} 条记录（自第 {bad_line} 行起）缺少结束标签 ER，"
            "导出文件可能被截断，请检查文件完整性后重试。"
        )
    if er_count > ty_count:
        raise ParseError(
            f"RIS 解析失败（{filename}）: ER 标签数（{er_count}）多于 TY 标签数（{ty_count}），"
            "文件结构异常，请检查导出文件。"
        )


def parse_ris(path: str | Path) -> list[Article]:
    """解析 RIS 导出，返回与 "ER -" 记录数一致的 Article 列表（不去重）。"""
    p = Path(path)
    if not p.is_file():
        raise ParseError(f"文件不存在: {p}")
    text = _read_text(p)
    _validate_record_terminators(text, p.name)
    source_digest = hashlib.sha256(p.read_bytes()).hexdigest()
    try:
        records = rispy.loads(text)
    except Exception as exc:  # rispy 异常类型随版本变化，统一包装并保留定位信息
        raise ParseError(f"RIS 解析失败（{p.name}）: {exc}") from exc
    if not records:
        # 合法 RIS 至少含一条 "TY - ... / ER -" 记录；零记录说明不是 RIS 文件
        raise ParseError(f"RIS 解析失败（{p.name}）: 未找到任何记录（缺少 TY/ER 标签）")

    articles: list[Article] = []
    for record_no, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise ParseError(f"RIS 第 {record_no} 条记录格式异常：非键值结构")

        title = normalize_title(_first(record, _TITLE_KEYS))
        doi = normalize_doi(_first(record, _DOI_KEYS))

        year = extract_year(_first(record, _YEAR_KEYS))
        if year is None:
            year = extract_year(_first(record, _DATE_KEYS))

        content_hash = compute_content_hash(doi, title)
        source_key = _first(record, _KEY_KEYS)
        zotero_key = source_key or build_zotero_key(record, content_hash)
        raw = dict(record)
        if not source_key:
            # Retain the legacy key as a base for compatibility, but give each
            # source row a stable identity so same-title rows are not collapsed.
            raw["_generated_key"] = True
            raw["_fallback_key"] = zotero_key
            raw["_fallback_id"] = f"{source_digest}:{record_no}"

        articles.append(
            Article(
                zotero_key=zotero_key,
                item_type=str(record.get("type_of_reference", "") or ""),
                title=title,
                authors=normalize_authors(record.get("authors", [])),
                journal=_first(record, _JOURNAL_KEYS),
                year=year,
                doi=doi,
                abstract=_first(record, _ABSTRACT_KEYS),
                url=_first(record, _URL_KEYS),
                source_format="ris",
                raw=raw,
                content_hash=content_hash,
            )
        )
    return articles
