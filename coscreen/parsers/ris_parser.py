"""Zotero/标准 RIS 导出解析器 —— rispy 读取，兼容 UTF-8/GBK 编码（见 SPEC.md §3）。

RIS 作者为列表，经 normalize_authors 以 "; " 连接；记录边界预校验与 rispy
文法逐字对齐（_validate_record_terminators），rispy 会静默丢弃的在途记录
在导入前显式报错，合法续行（"ER -" 单空格以外的行）不会被误拒。
"""

from __future__ import annotations

import sys
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
from coscreen.parsers.csv_parser import (
    ParseError,
    content_fallback_id,
    decode_bytes_with_confidence,
    is_legal_source_key,
)

# rispy 0.10 将标签映射为可读键名；此处同时兼容可读键名与原始标签两种取值。
# 注意 TI 与 T1 都要覆盖：Zotero RIS 用 T1（rispy 映射为 primary_title），
# Web of Science RIS 用 TI（rispy 映射为 title）——漏掉后者曾导致整库"无标题"。
_TITLE_KEYS = ("primary_title", "title", "T1", "TI")
# 期刊候选（U2-P6）：J2/JA（rispy 映射 alternate_title1/2）补齐；JF/JO/T2
# 原有。SO 不在 rispy 默认映射（落 unknown_tag），经 _record_view 兜底回读。
_JOURNAL_KEYS = (
    "journal_name",
    "secondary_title",
    "alternate_title1",
    "alternate_title2",
    "alternate_title3",
    "JO",
    "JF",
    "T2",
    "SO",
)
# 年份候选（U2-P10）：Y1（rispy 映射 publication_year）补齐；PY 原有。
_YEAR_KEYS = ("year", "publication_year", "PY")
_DATE_KEYS = ("date", "DA")
# DOI 候选（U2-P9）：WoS 的 DI 标签不在 rispy 默认映射（落 unknown_tag），
# 经 _record_view 兜底回读；DO（rispy 映射 doi）原有。
_DOI_KEYS = ("doi", "DI", "DO")
_ABSTRACT_KEYS = ("abstract", "notes_abstract", "AB", "N2")
_URL_KEYS = ("urls", "UR")
_KEY_KEYS = ("id", "ID", "accession_number", "AN")


def _ris_tag(line: str) -> str | None:
    """按 rispy RisParser.parse_line 的文法逐字判定标签行，返回两字符标签。

    rispy 只认 line[2:5] == "  -" 且前两字符为大写字母的行；其余——包括
    "ER -"（单空格）、"    TY - ..."（缩进续行）、"ER-positive ..."——都是
    续行。任何比这更宽/更严的识别都会与 rispy 分叉（r5 复审 #12/#17）。
    """
    if line[2:5] == "  -" and line[:2].isupper() and line[0:1].isalpha():
        return line[:2]
    return None


def _validate_record_terminators(text: str, filename: str) -> None:
    """预校验记录边界，与 rispy 0.10 的解析状态机逐字对齐（U2-P8 + 复审 #12/#13/#17）。

    rispy 的实际行为：任何以 "TY" 开头的行都会开启一条记录；记录仅在遇到
    文法合法的 "ER  - " 标签行时结算；生成器耗尽时在途记录被静默丢弃。
    此处按同一状态机复演：

    - 仅当一条「由文法合法 TY 行开启、直到文件末尾都未遇到 ER」的记录
      （即 rispy 必定静默丢弃的真实记录）时抛错并给出定位；
    - 在途记录中再次出现文法合法的 TY 行时，rispy 会把新记录的标签并入
      旧记录生成嵌合记录（作者污染、后一条标题静默消失）——rispy 不丢弃
      记录（返回数与 ER 数一致），故不整文件拒绝，只打可定位的 stderr
      告警（R3：能警告不改变行为）；
    - 除此之外不附加任何校验——纯文本幻影记录（如 ER 之后以 "TY" 开头
      的注释行）rispy 本就丢弃，不视为数据丢失，不得借预校验整文件拒绝。
    """
    in_record = False
    open_line = 0
    ty_count = 0
    chimera_warned = False
    # 与 rispy.parse 的 newline="\\n" 口径一致（CRLF 行尾的 \\r 不影响判定）
    for lineno, line in enumerate(text.split("\n"), start=1):
        if not in_record:
            if line.startswith("TY") and _ris_tag(line) == "TY":
                in_record = True
                open_line = lineno
                ty_count += 1
        else:
            tag = _ris_tag(line)
            if tag == "ER":
                in_record = False
            elif tag == "TY" and not chimera_warned:
                chimera_warned = True
                print(
                    f"警告：{filename} 第 {lineno} 行出现新的 TY 标签，但第 {open_line} 行"
                    "起的记录尚未以 \"ER  -\" 结束；rispy 会把两条记录合并为一条嵌合记录"
                    "（后续记录的标题会被静默吞并），请检查导出文件的完整性。",
                    file=sys.stderr,
                )
    if in_record:
        raise ParseError(
            f"RIS 解析失败（{filename}）: 第 {ty_count} 条记录"
            f"（自第 {open_line} 行起）缺少结束标签 ER（正确写法为两个空格: "
            '"ER  -"），rispy 解析会静默丢弃该条记录；'
            "导出文件可能被截断，请检查文件完整性后重试。"
        )


def _read_text(path: Path) -> tuple[str, list[str]]:
    """按置信度回退链解码 RIS 字节（见 decode_bytes_with_confidence，U2-P11）。"""
    return decode_bytes_with_confidence(path.read_bytes(), path.name)


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


def _record_view(record: dict) -> dict:
    """在 rispy 记录之上补一层「原始标签」视图（U2-P6/U2-P9）。

    rispy 默认映射不含 WoS 的 SO/DI 等标签，它们的值落在
    record["unknown_tag"]（{标签: [值]}）。把原始标签以大写键并入视图
    （不覆盖 rispy 已映射的可读键名），使 _first 的候选元组能用原始
    标签名直接兜底取值，不再静默丢失期刊与 DOI。
    """
    view = dict(record)
    unknown = record.get("unknown_tag")
    if isinstance(unknown, dict):
        for tag, values in unknown.items():
            view.setdefault(str(tag).upper(), values)
    return view


def parse_ris(path: str | Path) -> list[Article]:
    """解析 RIS 导出，返回与 "ER  -" 记录数一致的 Article 列表（不去重）。"""
    p = Path(path)
    if not p.is_file():
        raise ParseError(f"文件不存在: {p}")
    text, encoding_warnings = _read_text(p)
    if encoding_warnings:
        for warning in encoding_warnings:
            print(f"警告：{warning}", file=sys.stderr)
    _validate_record_terminators(text, p.name)
    try:
        records = rispy.loads(text)
    except Exception as exc:  # rispy 异常类型随版本变化，统一包装并保留定位信息
        raise ParseError(f"RIS 解析失败（{p.name}）: {exc}") from exc
    if not records:
        # 合法 RIS 至少含一条 "TY  - ... / ER  -" 记录；零记录说明不是 RIS 文件
        raise ParseError(f"RIS 解析失败（{p.name}）: 未找到任何记录（缺少 TY/ER 标签）")

    articles: list[Article] = []
    seen_keys: dict[str, str] = {}
    for record_no, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise ParseError(f"RIS 第 {record_no} 条记录格式异常：非键值结构")

        view = _record_view(record)
        title = normalize_title(_first(view, _TITLE_KEYS))
        doi = normalize_doi(_first(view, _DOI_KEYS))

        year = extract_year(_first(view, _YEAR_KEYS))
        if year is None:
            year = extract_year(_first(view, _DATE_KEYS))
        # 作者候选（U2-P10）：AU（rispy 映射 authors）优先，A1（rispy 映射
        # first_authors）兜底——此前只读 authors，A1-only 记录作者整列丢失
        authors_value = record.get("authors") or record.get("first_authors") or []
        authors = normalize_authors(authors_value)
        journal = _first(view, _JOURNAL_KEYS)
        url = _first(view, _URL_KEYS)

        content_hash = compute_content_hash(doi, title)

        # raw 剔除 "_" 前缀键（R6-P2 防御对称；rispy 正常不产生此类键名）
        raw = {k: v for k, v in record.items() if not str(k).startswith("_")}
        record_warnings: list[str] = list(encoding_warnings)

        # 来源键降级（U2-P2/R6-P3）：AN 在 Zotero 导出里是 archiveLocation
        # 自由文本（可重复、可含 "/" 或超长），ID 是 Zotero 导入时忽略的标签。
        # 非法键（后端 _KEY_RE 不可达）或同文件内同键不同文献时按无 Key
        # 处理走派生键，原值保留在 raw 供追溯——绝不静默沿用导致整条覆盖。
        source_key = _first(view, _KEY_KEYS)
        demoted = False
        if source_key:
            if not is_legal_source_key(source_key):
                demoted = True
                reason = "含非法字符（/、\\ 或超长），经后端接口不可达"
            elif source_key in seen_keys and seen_keys[source_key] != content_hash:
                demoted = True
                reason = "与本文件内另一条文献相同，不是行级唯一标识"
            else:
                seen_keys[source_key] = content_hash
        if demoted:
            raw["_demoted_source_key"] = source_key
            record_warnings.append(
                f"RIS 第 {record_no} 条记录：来源键 {source_key!r} {reason}，"
                "已回退为自动生成键（原值保留在 raw['_demoted_source_key']）"
            )
            print(f"警告：{record_warnings[-1]}", file=sys.stderr)
            # 派生输入剔除键字段，避免 build_zotero_key 把降级前的原值取回
            build_input = {
                k: v
                for k, v in record.items()
                if k not in ("id", "accession_number") and not str(k).startswith("_")
            }
            zotero_key = build_zotero_key(build_input, content_hash)
        else:
            zotero_key = source_key or build_zotero_key(record, content_hash)
        if not source_key or demoted:
            # Retain the legacy key as a base for compatibility, but give each
            # source row a stable identity so same-title rows are not collapsed.
            # _fallback_id 为记录内容稳定标识（U2-P5）：同一记录重新导出
            # （文件字节/行号变化）标识不变，重导不再漂移出新后缀键。
            raw["_generated_key"] = True
            raw["_fallback_key"] = zotero_key
            raw["_fallback_id"] = content_fallback_id(
                doi=doi,
                title=title,
                authors=authors,
                journal=journal,
                year=year,
                url=url,
            )
        if record_warnings:
            raw["_parse_warnings"] = record_warnings
        articles.append(
            Article(
                zotero_key=zotero_key,
                item_type=str(record.get("type_of_reference", "") or ""),
                title=title,
                authors=authors,
                journal=journal,
                year=year,
                doi=doi,
                abstract=_first(view, _ABSTRACT_KEYS),
                url=url,
                source_format="ris",
                raw=raw,
                content_hash=content_hash,
            )
        )
    return articles
