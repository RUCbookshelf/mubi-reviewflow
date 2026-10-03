"""字段规范化工具 —— DOI/标题/作者/年份/唯一键 的清洗与派生（见 SPEC.md §3）。

所有函数均为纯函数：无 IO、无随机性、无时间依赖，输出确定性。
"""

from __future__ import annotations

import re
import string

# DOI 前缀（大小写不敏感）：https://doi.org/ 、http://doi.org/ 、https://dx.doi.org/ 、doi:
_DOI_PREFIX_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi\s*:)", re.IGNORECASE)
# 连续空白（含全角空格与各类 Unicode 空白）
_MULTI_WS_RE = re.compile(r"\s+")
# 独立的 4 位数字（前后都不是数字，避免从 12345 中截取）
_YEAR_RE = re.compile(r"(?<!\d)(\d{4})(?!\d)")

# 标题首尾需剥离的字符：ASCII 标点 + 空白 + 常用中英文排版标点
_TITLE_STRIP_CHARS = string.punctuation + string.whitespace + "，。、；：？！“”‘’《》〈〉【】（）·—…．"

# build_zotero_key 在 raw 中查找的候选键名（小写）
_KEY_FIELDS: tuple[str, ...] = ("key", "id", "an", "accession_number")


def normalize_doi(doi: str) -> str:
    """规范化 DOI：去首尾空白、小写、剥离 doi.org / doi: 前缀；空值返回 ""。

    重复剥离前缀（如 "https://doi.org/doi:10.x/y"），并移除所有内部空白。
    """
    if not doi:
        return ""
    s = _MULTI_WS_RE.sub("", str(doi).strip()).lower()
    while True:
        m = _DOI_PREFIX_RE.match(s)
        if not m:
            break
        s = s[m.end():]
    return s


def normalize_title(title: str) -> str:
    """规范化标题：压缩内部空白、剥离首尾标点与空白。

    不做小写化——小写在去重比较时进行（见 SPEC.md §4 / dedup.py）。
    """
    if not title:
        return ""
    s = _MULTI_WS_RE.sub(" ", str(title)).strip()
    return s.strip(_TITLE_STRIP_CHARS).strip()


def _clean_part(part: object) -> str:
    """清洗单个作者名：压缩空白、去首尾空白。"""
    return _MULTI_WS_RE.sub(" ", str(part)).strip()


def normalize_authors(raw: str | list | None) -> str:
    """规范化作者字段：list → "a; b"；str 按分号拆分后清洗再拼回。

    空段丢弃；无有效作者时返回 ""。与 Zotero "Creator" 列的 "; " 分隔约定一致。
    """
    if raw is None:
        return ""
    if isinstance(raw, (list, tuple)):
        parts = [_clean_part(p) for p in raw]
    else:
        parts = [_clean_part(p) for p in str(raw).split(";")]
    return "; ".join(p for p in parts if p)


def extract_year(date_str: str) -> int | None:
    """提取字符串中首个位于 1500-2100 的 4 位年份；找不到返回 None。

    能处理 "2020-12-01"、"April 2021"、"1999/05"、"© 2011 Springer" 等杂乱格式。
    """
    if date_str is None:
        return None
    for m in _YEAR_RE.finditer(str(date_str)):
        year = int(m.group(1))
        if 1500 <= year <= 2100:
            return year
    return None


def build_zotero_key(raw: dict, content_hash: str) -> str:
    """从原始行/记录中取唯一键；缺失时返回 "auto-" + content_hash 前 12 位。

    候选键名（大小写不敏感）：Key（CSV）/ ID、AN（RIS 及其 rispy 映射名）。
    """
    if raw:
        lowered = {str(k).strip().lower(): v for k, v in raw.items()}
        for field in _KEY_FIELDS:
            value = lowered.get(field)
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                value = value[0] if value else ""
            text = str(value).strip()
            if text:
                return text
    return "auto-" + (content_hash or "")[:12]
