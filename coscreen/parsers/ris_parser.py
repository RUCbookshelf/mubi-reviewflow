"""Zotero/标准 RIS 导出解析器 —— rispy 读取，兼容 UTF-8/GBK 编码（见 SPEC.md §3）。

RIS 作者为列表，经 normalize_authors 以 "; " 连接。记录边界处理与 rispy
文法逐字对齐：单空格 "ER -" 终止行先规范化再交给 rispy（该记录真正导入）；
被截断（缺 ER）的记录不整文件拒绝——完整记录照常导入，被丢弃记录逐条
告警（r2 评审 R3）；合法续行（"ER-positive…"、缩进 "    TY - …"）不误拒。
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import rispy

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
from coscreen.parsers.csv_parser import (
    ParseError,
    content_record_id,
    decode_bytes_with_confidence,
    is_global_stable_identifier,
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


# 单空格记录终止行 "ER -"（无内容）：Zotero 自身导入正则允许 1-2 个空格，
# rispy 只认两个空格——不规范化会使末条记录被当作未终止而静默丢弃（#13）
_BARE_ER_LINE_RE = re.compile(r"ER[ ]{1,2}-[ ]*")


def _normalize_er_terminators(text: str) -> str:
    """把「ER -」（1 个空格、无内容）的终止行规范为 rispy 文法的 "ER  -"。

    只规范内容为空的终止行（含 2 空格原样重写为等价形式）：带内容的
    "ER - xxx" 仍按 rispy 语义视为续行——如 "ER - negative findings…"
    出现在摘要中是合法续行，不得因规范化提前截断记录（R2：宁可少改，
    不可误伤）。行尾的 \\r（CRLF 文件按 \\n 切分残留）原样保留。
    """
    lines = text.split("\n")
    changed = False
    for i, line in enumerate(lines):
        body = line[:-1] if line.endswith("\r") else line
        if _BARE_ER_LINE_RE.fullmatch(body):
            lines[i] = "ER  -" + ("\r" if line.endswith("\r") else "")
            changed = True
    return "\n".join(lines) if changed else text


def _in_flight_title(lines: list[str], start_index: int) -> str:
    """从未终止记录起始行（0 基下标）起提取首个 TI/T1 标题值，供告警定位。"""
    for line in lines[start_index:]:
        tag = _ris_tag(line)
        if tag in ("TI", "T1"):
            return line[6:].strip()
    return ""


def file_level_warnings(text: str, filename: str) -> list[str]:
    """按 rispy 状态机找出截断/嵌合记录，返回文件级告警并打到 stderr。

    rispy 的实际行为：以 "TY" 开头的行开启记录；记录仅在文法合法的
    "ER  - " 行结算；生成器耗尽时在途记录被静默丢弃。r2 评审 R3 裁定
    「绝不整文件拒绝」：完整记录照常导入，被丢弃的记录逐条告警（含
    记录序号、起始行号、标题与修复指引），不再抛 ParseError 把前面
    已完整的记录一并丢弃（此前实测 CLI rc=2、0 行入库）。

    集成轮复审（B3）：告警除 stderr 外**作为返回值回流**——调用方把它
    附到每条记录的 raw["_parse_warnings"]，Web/API 路径不再只看到
    「少一条」；在途记录中的嵌合 TY 由 _split_chimera_records 预先
    拆分（两条各自导入，不再生成字段互相污染的嵌合记录）。
    """
    warnings: list[str] = []
    lines = text.split("\n")
    in_record = False
    open_line = 0
    ty_count = 0
    for lineno, line in enumerate(lines, start=1):
        if not in_record:
            if line.startswith("TY") and _ris_tag(line) == "TY":
                in_record = True
                open_line = lineno
                ty_count += 1
        else:
            tag = _ris_tag(line)
            if tag == "ER":
                in_record = False
            elif tag == "TY":
                warnings.append(
                    f"{filename} 第 {lineno} 行出现新的 TY 标签，但第 {open_line} 行"
                    "起的记录尚未以 \"ER  -\" 结束；已在该处补全 ER 把两条记录拆分"
                    "导入（否则 rispy 会把它们合并为一条字段互相污染的嵌合记录）。"
                )
    if in_record:
        title = _in_flight_title(lines, open_line - 1)
        title_hint = f"（标题：{title}）" if title else ""
        warnings.append(
            f"{filename} 第 {ty_count} 条记录{title_hint}（自第 {open_line} 行起）"
            "缺少结束标签 ER（正确写法为两个空格: \"ER  -\"），该条记录未被导入；"
            "前面已完整的记录照常入库。导出文件可能被截断，请补全 ER 后单独重导该条。"
        )
    for warning in warnings:
        print(f"警告：{warning}", file=sys.stderr)
    return warnings


def _split_chimera_records(text: str) -> str:
    """在「在途记录中的文法合法 TY 行」前补全合成 ER 终止行（复审 B3）。

    rispy 对未结算记录中的新 TY 会吞并后续记录生成嵌合记录（前条的
    作者/DOI 被后条标题污染）。此处按同一状态机扫描，在嵌合点插入
    "ER  -"：前条按「截至此处已完整的字段」正常导入，后条独立解析——
    不丢任何一条（R3），只附文件级告警说明拆分。其余行为不变：纯文本
    幻影记录（ER 后以 "TY" 开头的注释行）rispy 本就丢弃，不插入。
    """
    out: list[str] = []
    in_record = False
    for line in text.split("\n"):
        tag = _ris_tag(line)
        if in_record and tag == "TY":
            out.append("ER  -")
            in_record = False
        if not in_record and line.startswith("TY") and tag == "TY":
            in_record = True
        elif in_record and tag == "ER":
            in_record = False
        out.append(line)
    return "\n".join(out)


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
    """解析 RIS 导出，返回与 "ER  -" 记录数一致的 Article 列表（不去重）。

    被截断（缺 ER）的末条记录不整文件拒绝（r2 评审 R3）：完整记录照常
    返回入库，未终止记录逐条打显式告警；单空格 "ER -" 终止行先规范化
    为 "ER  -" 再交给 rispy，使该记录真正导入。
    """
    p = Path(path)
    if not p.is_file():
        raise ParseError(f"文件不存在: {p}")
    text, encoding_warnings = _read_text(p)
    if encoding_warnings:
        for warning in encoding_warnings:
            print(f"警告：{warning}", file=sys.stderr)
    text = _normalize_er_terminators(text)
    file_warnings = file_level_warnings(text, p.name)
    # 嵌合拆分在告警计算之后（告警行号对应原始文本）
    text = _split_chimera_records(text)
    # 2.1.3 出货形态（r2 评审 R1）：_fallback_id 用整文件摘要，保持逐字一致
    source_digest = hashlib.sha256(p.read_bytes()).hexdigest()
    try:
        records = rispy.loads(text)
    except Exception as exc:  # rispy 异常类型随版本变化，统一包装并保留定位信息
        raise ParseError(f"RIS 解析失败（{p.name}）: {exc}") from exc
    if not records:
        # 合法 RIS 至少含一条 "TY  - ... / ER  -" 记录；零记录说明不是 RIS 文件
        raise ParseError(f"RIS 解析失败（{p.name}）: 未找到任何记录（缺少 TY/ER 标签）")

    articles: list[Article] = []
    # 同文件来源键身份（复审 B1/B7）：content_hash 只含 doi|title，同题异
    # 作者不触发降级——记录 (content_hash, 姓氏, 名, 年份)，作者/年份能
    # 证明不同才降级，缺一侧按元数据补全口径交给去重层裁定
    seen_keys: dict[str, tuple[str, str, str, object]] = {}
    record_id_occurrences: dict[str, int] = {}
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
        # 文件级告警（截断/嵌合拆分）随每条记录回流 raw（复审 B3：Web/API
        # 路径不再只看到「少一条」），去重保持首次出现顺序
        record_warnings: list[str] = list(encoding_warnings)
        for warning in file_warnings:
            if warning not in record_warnings:
                record_warnings.append(warning)

        # 来源键降级（U2-P2/R6-P3）：AN 在 Zotero 导出里是 archiveLocation
        # 自由文本（可重复、可含 "/" 或超长），ID 是 Zotero 导入时忽略的标签。
        # 非法键（后端 _KEY_RE 不可达）、非全局稳定标识（自由文本/短序号，
        # 跨文件撞键覆盖）或同文件内同键异文（内容哈希不同，或同题但作者/
        # 年份可证不同——content_hash 只含 doi|title，同题异作者不会触发，
        # 复审 B1/B7）时按无 Key 处理走派生键，原值保留在 raw 供追溯——
        # 绝不静默沿用导致整条覆盖。
        source_key = _first(view, _KEY_KEYS)
        demoted = False
        if source_key:
            surname = first_author_surname(authors)
            given = first_author_given_names(authors)
            if not is_legal_source_key(source_key):
                demoted = True
                reason = "含非法字符（/、\\ 或超长），经后端接口不可达"
            elif not is_global_stable_identifier(source_key):
                demoted = True
                reason = "为自由文本/序号形态（含空白或短纯数字），不是全局稳定标识"
            elif source_key in seen_keys:
                prev_hash, prev_surname, prev_given, prev_year = seen_keys[source_key]
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
                seen_keys[source_key] = (content_hash, surname, given, year)
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
            # _fallback_id 为 v2.1.3 逐字一致的行定位标识（sha256(整文件):
            # 记录序号；R1：2.1.3 库重导幂等依赖 _stabilize 按它反查键）。
            raw["_generated_key"] = True
            raw["_fallback_key"] = zotero_key
            raw["_fallback_id"] = f"{source_digest}:{record_no}"
            # 内容稳定标识（U2-P5）：与文件字节/行号无关，同文件内相同内容
            # 第 2+ 条加 "#N" occurrence 区分（不折叠真实重复行）；消费归
            # 去重引擎 R6-D5，解析器与既有下游行为不因本字段改变
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
