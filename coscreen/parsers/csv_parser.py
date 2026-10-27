"""Zotero CSV 导出解析器 —— pandas 读取，UTF-8（含 BOM），列名大小写不敏感（见 SPEC.md §3）。

缺失字段一律映射为 ""（不是 NaN）；解析失败抛 ParseError，消息携带物理行号
（U2-P12：按 csv 模块的物理行号计，引号内换行不再使行号偏移）。
"""

from __future__ import annotations

import csv
import hashlib
import io
import sys
from pathlib import Path

import pandas as pd

from coscreen.models import Article, compute_content_hash
from coscreen.normalize import (
    build_zotero_key,
    extract_year,
    first_author_given_names,
    first_author_surname,
    given_names_compatible,
    normalize_authors,
    normalize_doi,
    normalize_title,
    years_compatible,
)


class ParseError(Exception):
    """解析失败异常：文件级错误（不存在/非合法 CSV）与行级错误均携带定位信息。"""


# 规范化表头（小写、压缩空白）-> 内部字段名；涵盖 SPEC §3 的 Zotero 列名映射
_COLUMN_ALIASES: dict[str, str] = {
    "key": "zotero_key",
    "item type": "item_type",
    "title": "title",
    # "creator" 为新版 Zotero 导出列名；"author"/"authors" 为旧版及常见
    # 第三方导出的列名变体（U2-P1：Author 列被静默丢弃 → authors=""）。
    "creator": "authors",
    "author": "authors",
    "authors": "authors",
    "publication title": "journal",
    "publication year": "year",
    "date": "date",
    "doi": "doi",
    "abstract note": "abstract",
    "url": "url",
}

# 字段级必需列（内部字段名）：缺 Title 则整个文件无法使用
_REQUIRED_FIELDS: tuple[str, ...] = ("title",)

# 解析器注入 raw 的内部保留字段均以 "_" 开头（_generated_key/_fallback_key/
# _fallback_id/_parse_warnings 等）。用户 CSV 中的同名列一律剔除并告警，
# 不得透传——下游 _stabilize_generated_import_keys 会信任这些字段，
# 透传即允许注入者合并/重定向任意行键（R6-P2）。
_RESERVED_COLUMN_PREFIX = "_"

# build_zotero_key 读取的原始键列名（小写）；来源键被降级为派生键时，
# 需从派生输入中剔除，避免 build_zotero_key 把降级前的原值再取回去。
_SOURCE_KEY_COLUMN_NAMES = frozenset({"key", "id", "an", "accession_number"})

# ---------------------------------------------------------------------------
# 编码回退（U2-P11 + r5 复审 #11/#16）：回退必须可判定、可告警，不可静默乱码
# ---------------------------------------------------------------------------

# utf-8 失败后的回退候选，按序尝试；命中即回退并把告警附到解析结果
_ENCODING_FALLBACKS: tuple[str, ...] = ("gbk", "cp1252")
# 候选解码（errors="replace"）后的替换字符率上限：超过即视为该候选不可信
_MAX_REPLACEMENT_RATE = 0.001
# cp1252 候选的非 ASCII 占比上限：西文 ANSI 文本以 ASCII 为主体，而真实
# GBK 中文按 cp1252 解码会得到大比例 Latin 扩展字符——此时两个候选都
# 不可信，宁可不回退（抛可操作的 ParseError），不可选错边静默乱码。
_MAX_CP1252_NON_ASCII_RATE = 0.2


def _gbk_mojibake_markers() -> frozenset[str]:
    """计算「cp1252/西文 ANSI 字节被 GBK 误解码」的典型乱码字符集（一次性）。

    GBK 引导字节（0x80-0xFF，覆盖 cp1252 的弯引号 0x92、重音字母 0xFC 等）
    与常见 ASCII 跟随字节组成的双字节序列若解出单个 CJK 汉字，该汉字即
    mojibake 标记：0x92+'s' -> '抯'、0xFC+'l' -> '黮'。GB2312 常用汉字的
    两个字节均 >= 0xA1，正常中文文本几乎不会命中这些字符。
    """
    markers: set[str] = set()
    followers = b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 '\"(),.;:!?"
    for lead in range(0x80, 0x100):
        for follower in followers:
            try:
                decoded = bytes((lead, follower)).decode("gbk")
            except UnicodeDecodeError:
                continue
            if len(decoded) == 1 and "\u3400" <= decoded <= "\u9fff":
                markers.add(decoded)
    return frozenset(markers)


_GBK_MOJIBAKE_MARKERS = _gbk_mojibake_markers()


def decode_bytes_with_confidence(data: bytes, filename: str) -> tuple[str, list[str]]:
    """按 UTF-8(含 BOM) → GBK/cp1252（置信度门限）解码，返回 (文本, 告警列表)。

    回退仅在候选可安全判定时发生：解码替换字符率 < 0.1%，且结果不含典型
    GBK mojibake 字符（cp1252 候选另要求文本以 ASCII 为主体）。命中则返回
    文本与一条编码告警（调用方负责附到解析结果与 stderr）；两个候选都不
    可接受时抛 ParseError 列出已尝试的编码——宁可显式失败，不可静默乱码。
    """
    try:
        return data.decode("utf-8-sig"), []
    except UnicodeDecodeError:
        pass
    for encoding in _ENCODING_FALLBACKS:
        try:
            text = data.decode(encoding, errors="replace")
        except (UnicodeDecodeError, LookupError):
            continue
        replacement_rate = text.count("\ufffd") / len(text) if text else 0.0
        if replacement_rate >= _MAX_REPLACEMENT_RATE:
            continue
        if any(ch in _GBK_MOJIBAKE_MARKERS for ch in text):
            continue
        if encoding == "cp1252":
            non_ascii = sum(1 for ch in text if ord(ch) > 0x7F)
            if text and non_ascii / len(text) > _MAX_CP1252_NON_ASCII_RATE:
                continue
        return text, [
            f"{filename} 非 UTF-8 编码，已按 {encoding.upper()} 解码；"
            "若标题/作者出现异常字符，请将文件重新另存为 UTF-8 后导入"
        ]
    raise ParseError(
        f"无法安全判定 {filename} 的文本编码（已尝试 UTF-8 与 "
        "GBK/cp1252 候选：解码失败或疑似乱码）；请将其重新另存为 UTF-8 后再导入。"
    )


# ---------------------------------------------------------------------------
# 行级内容稳定标识（U2-P5）：独立字段 raw["_record_id"]，不改 _fallback_id
# ---------------------------------------------------------------------------


def content_record_id(
    *,
    doi: str,
    title: str,
    authors: str,
    journal: str,
    year: int | None,
    url: str,
) -> str:
    """由记录自身规范化字段派生的内容摘要（不含 occurrence），前缀 "rec-"。

    与文件字节/行号/顺序无关：同一条记录重新导出（时间戳、顺序、注释行
    变化）得到同一摘要；摘要不参与——摘要编辑不应改变记录身份。
    r2 评审 R1 裁定：``_fallback_id`` 必须保持 v2.1.3 出货形态
    （sha256(整文件字节):行号），否则 2.1.3 库重导同一文件会新增行；
    因此内容稳定标识放在**独立字段** ``raw["_record_id"]``（同文件内
    相同内容的记录由解析器加 "#2"/"#3" occurrence 后缀区分，不折叠
    真实重复行），消费与切换归去重引擎泳道 R6-D5——解析器与既有下游
    （_stabilize_generated_import_keys/_conflict_suffix_key）的行为
    不因本字段改变。
    """
    payload = "|".join(
        (doi, title, authors, journal, "" if year is None else str(year), url)
    )
    return "rec-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def is_legal_source_key(key: str) -> bool:
    """来源键是否可安全作为 zotero_key（与后端 _KEY_RE 逐字对齐，R6-P3）。

    custom_backend/main.py 的 _KEY_RE 为 ^[^/\\\\\\x00]{1,300}$：键为空、
    含 "/"、"\\"、NUL 或超过 300 字符时，该文献经 coding/fulltext 等端点
    不可达。此处只按同一口径判定，不附加更严的规则。
    """
    return (
        bool(key)
        and len(key) <= 300
        and "/" not in key
        and "\\" not in key
        and "\x00" not in key
    )


def is_global_stable_identifier(key: str) -> bool:
    """来源键是否为「全局稳定标识」（U2-P2：自由文本/序号不作键）。

    Zotero RIS 的 AN 映射 archiveLocation 自由文本（可含空格、每条文献
    各写各的），ID 是 Zotero 导入时明确忽略的标签，部分导出器逐条附加
    纯数字序号——这些值跨文件/跨导出不是全局唯一，撞在同一键上会让
    不同文献互相覆盖（r2 评审实测：两个单记录文件同 AN "Box 3"，第二
    次导入「更新 1 条」，DB 只剩 Men，Women 被静默覆盖）。保守判定，
    只降级明显非标识的形态，合法紧凑标识（Zotero 8 位键、WOS:…、
    PMC…、3 位及以上纯数字 PMID 形态）不受影响：
    - 含任意空白字符（archiveLocation 类自由文本，如 "Box 3"）；
    - 纯数字且长度 <= 2（逐条序号；r3 评审裁定 5 位纯数字 '12345' 是
      合法 PMID 形态的全局稳定标识，不得降级——阈值从 <=5 收窄）。
    """
    if any(ch.isspace() for ch in key):
        return False
    if key.isdigit() and len(key) <= 2:
        return False
    return True


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


def _is_blank_row(row: list[str]) -> bool:
    """与 pandas skip_blank_lines 的口径一致：整行空白（含纯空白单字段）跳过。"""
    return not row or (len(row) == 1 and not row[0].strip())


def _scan_physical_rows(text: str, filename: str) -> list[int]:
    """首遍扫描：校验每行字段数不超表头（U2-P3），并记录数据行物理行号。

    - pandas 对第一条数据行多出的字段会静默左移错位（Key/Title/DOI 全部
      错配且不报错），第二行起才抛 ParserError；此处用 csv 模块在交给
      pandas 之前统一校验：任何数据行字段数多于表头即抛 ParseError（带
      物理行号）——把静默数据错配显式化（铁律 R3 允许的场景）。
    - 数据行的物理行号取该行最后一个物理行的行号（reader.line_num），
      供后续错误消息使用（U2-P12）。
    """
    reader = csv.reader(io.StringIO(text, newline=""))
    header_len: int | None = None
    physical_lines: list[int] = []
    for row in reader:
        if _is_blank_row(row):
            continue  # pandas 同样跳过整行空白（含表头前导空行）
        if header_len is None:
            header_len = len(row)
            continue
        if len(row) > header_len:
            raise ParseError(
                f"CSV 解析失败（{filename}）: 第 {reader.line_num} 行数据包含 "
                f"{len(row)} 个字段，多于表头的 {header_len} 个；标题或字段值中"
                "可能存在未加引号的逗号，请检查该行或为字段补上引号后重试。"
            )
        physical_lines.append(reader.line_num)
    return physical_lines


def _read_csv_text(path: Path) -> tuple[str, list[str]]:
    """按置信度回退链解码 CSV 字节（见 decode_bytes_with_confidence）。"""
    return decode_bytes_with_confidence(path.read_bytes(), path.name)


def parse_csv(path: str | Path) -> list[Article]:
    """解析 Zotero CSV 导出，返回与数据行数一致的 Article 列表（不去重）。"""
    p = Path(path)
    if not p.is_file():
        raise ParseError(f"文件不存在: {p}")
    text, encoding_warnings = _read_csv_text(p)
    if encoding_warnings:
        for warning in encoding_warnings:
            print(f"警告：{warning}", file=sys.stderr)
    # 先于 pandas 校验字段数（U2-P3：pandas 对首行多余字段静默错位）
    physical_lines = _scan_physical_rows(text, p.name)

    try:
        frame = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    except (pd.errors.ParserError, pd.errors.EmptyDataError, ValueError) as exc:
        raise ParseError(f"CSV 解析失败（{p.name}）: {exc}") from exc

    colmap = _build_column_map(list(frame.columns))
    missing = [field for field in _REQUIRED_FIELDS if field not in colmap]
    if missing:
        raise ParseError(
            f"CSV 缺少必需列 Title（实际表头: {list(frame.columns)}）"
        )

    records = frame.to_dict("records")
    # 行号映射与 pandas 记录数不一致（极端引号/空白行差异）时退回 idx+2，
    # 仅影响错误消息的行号精度，不影响任何校验结果
    line_map_ok = len(physical_lines) == len(records)

    reserved_columns: dict[str, int] = {}
    # 同文件来源键身份（复审 B1/B7：content_hash 只含 doi|title，同题异作者
    # 不会触发降级——记录 (content_hash, 姓氏, 名, 年份)，作者/年份能证明
    # 不同才降级，缺一侧按元数据补全口径交给去重层裁定）
    seen_keys: dict[str, tuple[str, str, str, object]] = {}
    record_id_occurrences: dict[str, int] = {}
    # 2.1.3 出货形态（r2 评审 R1）：_fallback_id = sha256(整文件字节):行号，
    # 下游 _stabilize_generated_import_keys/_conflict_suffix_key 按该串反查
    # 键——保持逐字一致，2.1.3 库重导同一文件才能零新增
    source_digest = hashlib.sha256(p.read_bytes()).hexdigest()
    articles: list[Article] = []
    for idx, row in enumerate(records):
        get = _row_getter(row, colmap)
        physical_line = physical_lines[idx] if line_map_ok else idx + 2

        title = normalize_title(get("title"))
        doi = normalize_doi(get("doi"))
        key_raw = get("zotero_key")
        if not key_raw:
            # 口径对齐（复审：CSV 的 ID/AN 列不得绕过来源键降级门）：
            # build_zotero_key 的 _KEY_FIELDS 会从 raw 取 id/an/accession_number
            # 作字面键——同一批候选在此先过同一三道闸，避免同一 'Box 3' 在
            # RIS 路降级告警、CSV 路却静默用作主键（直调 upsert 即静默覆盖）。
            for col, value in row.items():
                lowered_col = str(col).strip().lower()
                if (lowered_col in ("id", "an", "accession_number")
                        and value is not None and str(value).strip()):
                    key_raw = str(value).strip()
                    break

        # 无 Key 且无 DOI/标题（无法构建 content_hash）的行视为损坏数据
        if not key_raw and not title and not doi:
            raise ParseError(
                f"CSV 第 {physical_line} 行数据缺少 Key、Title 与 DOI，无法构建唯一标识"
            )

        year = extract_year(get("year"))
        if year is None:
            year = extract_year(get("date"))
        authors = normalize_authors(get("authors"))
        journal = get("journal")
        url = get("url")

        content_hash = compute_content_hash(doi, title)

        # raw 剔除保留列名（R6-P2）：内部 "_" 前缀字段由解析器自行注入，
        # 用户同名列透传会让下游信任注入值（合并/重定向任意行键）
        raw: dict = {}
        record_warnings: list[str] = list(encoding_warnings)
        for col, value in row.items():
            if str(col).lstrip().startswith(_RESERVED_COLUMN_PREFIX):
                reserved_columns.setdefault(str(col), physical_line)
                continue
            raw[col] = value

        # 来源键降级（U2-P2/R6-P3）：非法键（后端不可达）、非全局稳定标识
        # （含空白的自由文本/短纯数字序号，跨文件会撞键覆盖）或同文件内
        # 同键异文（内容哈希不同，或同题但作者/年份可证不同）时按无 Key
        # 处理，走派生键；原值保留在 raw 供追溯
        demoted = False
        if key_raw:
            surname = first_author_surname(authors)
            given = first_author_given_names(authors)
            if not is_legal_source_key(key_raw):
                demoted = True
                reason = "含非法字符（/、\\ 或超长），经后端接口不可达"
            elif not is_global_stable_identifier(key_raw):
                demoted = True
                reason = "为自由文本/序号形态（含空白或短纯数字），不是全局稳定标识"
            elif key_raw in seen_keys:
                prev_hash, prev_surname, prev_given, prev_year = seen_keys[key_raw]
                if prev_hash != content_hash:
                    demoted = True
                    reason = "与本文件内另一条文献相同，不是行级唯一标识"
                elif ((prev_surname and surname and prev_surname != surname)
                        or (prev_given and given
                            and not given_names_compatible(prev_given, given))
                        or not years_compatible(prev_year, year)):
                    demoted = True
                    reason = "与本文件内另一条文献同键同题，但作者/年份不同，不是同一文献"
                # 兼容（含单侧缺作者/年份的元数据补全口径）：保留首见身份
                # 不覆盖——后续同键记录仍与首条比较，不被中间行的缺字段稀释
            else:
                seen_keys[key_raw] = (content_hash, surname, given, year)
        if demoted:
            raw["_demoted_source_key"] = key_raw
            record_warnings.append(
                f"CSV 第 {physical_line} 行：来源键 {key_raw!r} {reason}，"
                "已回退为自动生成键（原值保留在 raw['_demoted_source_key']）"
            )
            print(f"警告：{record_warnings[-1]}", file=sys.stderr)
            build_input = {
                col: value
                for col, value in raw.items()
                if str(col).strip().lower() not in _SOURCE_KEY_COLUMN_NAMES
            }
            zotero_key = build_zotero_key(build_input, content_hash)
        else:
            zotero_key = build_zotero_key(raw, content_hash)
        if not key_raw or demoted:
            raw["_generated_key"] = True
            raw["_fallback_key"] = zotero_key
            # v2.1.3 逐字一致的行定位标识（R1：2.1.3 库重导幂等依赖它）
            raw["_fallback_id"] = f"{source_digest}:{idx + 2}"
            # 内容稳定标识（U2-P5）：同文件内相同内容第 2+ 条加 "#N"
            # occurrence，不折叠真实重复行；消费归去重引擎 R6-D5
            base_record_id = content_record_id(
                doi=doi,
                title=title,
                authors=authors,
                journal=journal,
                year=year,
                url=url,
            )
            occurrence = record_id_occurrences.get(base_record_id, 0) + 1
            record_id_occurrences[base_record_id] = occurrence
            raw["_record_id"] = (
                base_record_id if occurrence == 1 else f"{base_record_id}#{occurrence}"
            )
        if record_warnings:
            raw["_parse_warnings"] = record_warnings
        articles.append(
            Article(
                zotero_key=zotero_key,
                item_type=get("item_type"),
                title=title,
                authors=authors,
                journal=journal,
                year=year,
                doi=doi,
                abstract=get("abstract"),
                url=url,
                source_format="csv",
                raw=raw,
                content_hash=content_hash,
            )
        )
    for col, line in sorted(reserved_columns.items(), key=lambda kv: kv[1]):
        print(
            f"警告：CSV 第 {line} 行起：忽略以下划线开头的保留列 {col!r}"
            "（该列不参与导入，内部字段由解析器生成）",
            file=sys.stderr,
        )
    return articles
