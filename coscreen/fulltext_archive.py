"""Canonical, human-readable archive names for exported full-text PDFs.

This module is the **single source of truth** for the archive naming rule used
by both export outlets (``fulltext/archive-export`` and ``package/export``).
It is intentionally pure: no file system access, no I/O, no configuration.

Naming rule (2.1.5 W5 design §二.C)::

    {key}-{first-author-surname}-{year}.pdf

* ``key`` is the bibliographic item key. Keys are unique per task, so several
  papers by the same author in the same year never collide (they differ in the
  key prefix).
* Missing author / year become ``unknown`` — an entry is never dropped for
  missing metadata.
* The whole stem is capped at 120 characters.
* Characters that are illegal on Windows/macOS (``<>:"/\\|?*``), C0/DEL
  control characters and line/paragraph separators are replaced with ``_``.
* If cleaning/truncation makes two different items produce the same name, an
  extra ``-{sha256 short}`` suffix disambiguates them (same discipline as
  MM-07 in ``coscreen.fulltext``). The caller passes the names already taken
  (``taken``); the function itself stays deterministic for a given input.

The archive name only exists in the **export copy**. Internal storage keeps
save_pdf's injective safe name (``coscreen/fulltext.py``) and the ``fulltexts``
table remains untouched.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Collection, Mapping
from typing import Any

__all__ = [
    "MAX_STEM_CHARS",
    "UNKNOWN_TOKEN",
    "archive_filename",
    "first_author_surname_display",
]

#: 主干（不含 ``.pdf``）字符上限；设计 §二.C 规定 120 字符。
MAX_STEM_CHARS = 120
#: 元数据缺失时的占位词（固定 ASCII，十语言界面无需翻译文件名）。
UNKNOWN_TOKEN = "unknown"

#: Windows + macOS 非法字符与 C0/DEL 控制字符；U+2028/U+2029 在部分
#: 归档工具里会被当成换行处理，一并清洗。
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f\u2028\u2029]')
_SPACES = re.compile(r"\s+")
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_YEAR_TOKEN = re.compile(r"(?<!\d)(\d{4})(?!\d)")

#: 常见复姓：单段中文姓名按两字取姓（"欧阳修" -> "欧阳"）。
_COMPOUND_SURNAMES = frozenset({
    "欧阳", "司马", "诸葛", "上官", "东方", "皇甫", "尉迟", "公孙", "慕容",
    "司徒", "令狐", "宇文", "长孙", "南宫", "西门", "夏侯", "万俟", "闻人",
    "澹台", "公冶", "宗政", "濮阳", "淳于", "单于", "太叔", "申屠", "仲孙",
    "钟离", "鲜于", "闾丘", "司寇", "端木", "公西", "漆雕", "乐正", "拓跋",
    "夹谷", "谷梁", "百里", "东郭", "南门", "呼延", "羊舌", "微生", "梁丘",
    "左丘", "东门", "第五",
})

#: 姓名世代后缀（不参与姓氏判定）。
_GENERATION_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})
#: 姓氏助词前缀（与 coscreen.normalize.first_author_surname 同口径）。
_NAME_PARTICLES = frozenset({
    "van", "von", "de", "der", "den", "del", "della", "di", "da", "dos",
    "du", "la", "le", "ten", "ter", "bin", "ibn", "al",
})
#: 机构作者判定词：命中时整串作为姓氏段（截断由主干上限兜底）。
_ORG_WORDS = frozenset({
    "collaboration", "group", "consortium", "committee", "association",
    "society", "university", "institute", "institution", "department",
    "centre", "center", "network", "team", "task", "force", "panel",
    "organization", "organisation", "foundation", "agency", "office",
})


def _clean_component(text: Any) -> str:
    """清洗单个文件名分量：非法/控制字符 -> ``_``，折叠空白，去尾部点空格。"""
    value = "" if text is None else str(text)
    value = _ILLEGAL.sub("_", value)
    value = _SPACES.sub(" ", value).strip()
    # Windows 不允许文件名以点/空格结尾；连续点同样有歧义。纯分隔符
    # 清洗（如作者字段 "../.."）得到 "_" 时同样退回 unknown，不产出
    # "K-_-2020.pdf" 这类噪声名。
    value = value.strip(" ._")
    return value


def _tokens(text: str) -> list[str]:
    return [token for token in re.split(r"\s+", text.strip()) if token]


def _is_initial(token: str) -> bool:
    """姓名缩写：``J`` / ``J.`` / ``JA``（PubMed 的 "Smith JA" 风格）。"""
    bare = token.replace(".", "").strip()
    if not bare or len(bare) > 2:
        return False
    if not bare.isalpha():
        return False
    return bare.isupper() or token.endswith(".")


def _strip_generation(words: list[str]) -> list[str]:
    while words and words[-1].rstrip(".").lower() in _GENERATION_SUFFIXES:
        words.pop()
    return words


def _strip_particles(words: list[str]) -> list[str]:
    index = 0
    while index < len(words) - 1 and words[index].lower() in _NAME_PARTICLES:
        index += 1
    return words[index:]


def _cjk_surname(token: str) -> str:
    """单段中文姓名字符串取姓：复姓取前两字，否则首字。"""
    if len(token) >= 2 and token[:2] in _COMPOUND_SURNAMES:
        return token[:2]
    return token[:1]


def _looks_institutional(words: list[str]) -> bool:
    lowered = {word.lower().strip(".,:") for word in words}
    if lowered & _ORG_WORDS:
        return True
    content = [word for word in lowered if word not in _NAME_PARTICLES]
    return len(content) >= 4 and not any(_is_initial(word) for word in words)


def first_author_surname_display(authors: Any) -> str:
    """第一作者姓氏的**展示形式**（保留原大小写/中文原样；缺失返回 ""）。

    与比较层 ``coscreen.normalize.first_author_surname`` 共用同一姓名顺序
    口径（逗号=姓在前、缩写尾=名在后），但本函数用于文件名，因此：
    保留原始大小写（``Wang`` 而不是 ``wang``）、中文姓氏输出原字
    （``王``/``欧阳``）、剥离 ``van/von/de`` 等助词前缀。
    """
    if authors is None:
        return ""
    if isinstance(authors, (list, tuple)):
        head = str(authors[0]).strip() if authors else ""
    else:
        head = str(authors).split(";")[0].strip()
    head = _clean_component(head)
    if not head:
        return ""

    if "," in head:
        left = head.split(",", 1)[0].strip()
        words = _strip_generation(_tokens(left))
        if not words:
            return ""
        if all(_CJK.search(word) for word in words):
            token = "".join(words)
            return _cjk_surname(token) if len(token) > 1 else token
        words = _strip_particles(words)
        return " ".join(words).strip(" .,:") if words else ""

    words = _strip_generation(_tokens(head))
    if not words:
        return ""

    cjk_words = [word for word in words if _CJK.search(word)]
    if cjk_words and len(cjk_words) == len(words):
        if len(words) == 1:
            token = words[0]
            return _cjk_surname(token) if len(token) > 1 else token
        # "王 小明"（空格分隔的名姓）→ 首段是姓（1–2 字），否则按整段取首字。
        first = words[0]
        if len(first) <= 2:
            return first
        return _cjk_surname("".join(words))

    if _looks_institutional(words):
        return " ".join(words)

    end = len(words)
    while end > 1 and _is_initial(words[end - 1]):
        end -= 1
    surname_words = words[:end] if end < len(words) else [words[-1]]
    surname_words = _strip_particles(surname_words)
    return " ".join(surname_words).strip(" .,:") if surname_words else ""


def _year_token(year: Any) -> str:
    """年份展示：正整数原样；其余（含 0/None/自由文本里的其他内容）→ unknown。"""
    if isinstance(year, bool) or year is None:
        return UNKNOWN_TOKEN
    if isinstance(year, int):
        return str(year) if year > 0 else UNKNOWN_TOKEN
    text = str(year).strip()
    if text.isdigit() and int(text) > 0:
        return text
    # "2020-01-01" / "Published 2020" 之类：只认独立的四位年份。
    match = _YEAR_TOKEN.search(text)
    return match.group(1) if match else UNKNOWN_TOKEN


def _collision_name(stem: str, seed: str, taken: Collection[str]) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    for length in (8, 12, 16, 24, 32, 64):
        candidate = f"{stem}-{digest[:length]}.pdf"
        if candidate not in taken:
            return candidate
    # 理论上不可达（需要 5 个同前缀同名条目且短哈希全撞）；保持确定性。
    counter = 2
    while True:
        candidate = f"{stem}-{digest}-{counter}.pdf"
        if candidate not in taken:
            return candidate
        counter += 1


def archive_filename(meta: Mapping[str, Any], *, taken: Collection[str] = ()) -> str:
    """``meta`` → ``{key}-{surname}-{year}.pdf``（纯函数，确定性）。

    参数
    ----
    meta:
        至少应含 ``key`` 或 ``zotero_key``。可选 ``authors``（或
        ``author``/``first_author``）与 ``year``（或 ``publication_year``）。
        缺失/空值按 ``unknown`` 处理，绝不跳过条目。
    taken:
        调用方已分配的归档名集合。清洗/截断使不同条目撞名时，后一个条目
        追加 ``-{sha256(seed) 短后缀}``（MM-07 纪律）。同一输入 + 同一
        ``taken`` 恒返回同一结果。

    返回的文件名保证：``.pdf`` 结尾、不含路径分隔符、不含控制字符、
    主干 ≤ 120 字符（含碰撞后缀可能略超，用于防碰优先）。
    """
    key = meta.get("key")
    if key in (None, ""):
        key = meta.get("zotero_key")
    authors = meta.get("authors")
    if authors in (None, ""):
        authors = meta.get("author")
    if authors in (None, ""):
        authors = meta.get("first_author")
    year = meta.get("year")
    if year in (None, ""):
        year = meta.get("publication_year")

    key_text = _clean_component(key) or UNKNOWN_TOKEN
    surname = _clean_component(first_author_surname_display(authors)) or UNKNOWN_TOKEN
    year_text = _clean_component(_year_token(year)) or UNKNOWN_TOKEN

    stem = f"{key_text}-{surname}-{year_text}"[:MAX_STEM_CHARS].strip(" .-")
    if not stem:
        stem = UNKNOWN_TOKEN  # 极端清洗空主干兜底（不产出 ".pdf"）
    name = f"{stem}.pdf"
    if name not in taken:
        return name
    seed = str(key if key not in (None, "") else stem)
    return _collision_name(stem, seed, taken)
