"""摘要关键词高亮规则计算（2.1.5 Wave D2）——纯函数，无 I/O。

蓝本裁定：服务端负责「设置存储 + 高亮规则计算 + 契约」，前端只按返回的
**偏移范围**渲染 ``<mark>``，不再自行分词/匹配。

匹配口径（逐条落实泳道 D2）：

- **大小写不敏感**：``re.IGNORECASE``（Unicode 大小写折叠；德语 ß、土耳其
  İ 等长度会变的字符由正则引擎保持原文偏移，不手工 lower 文本）。
- **词边界**：英文（词首/词尾是 ASCII 字母、数字或下划线）按 ASCII 词边界
  判定——``(?<![A-Za-z0-9_])term(?![A-Za-z0-9_])``。因此 ``cat`` 不命中
  ``category``/``concatenate``；中英混排时紧邻汉字的英文词仍能命中（例如
  ``随机randomized对照`` 中的 ``randomized``）。中文词不做边界判定，按
  **直接子串**匹配（``随机`` 命中 ``随机对照``）。
- **短语优先于子词**：所有候选区间按「长度降序 → 起点升序 → term_id 升序」
  贪心接受互不重叠的区间；``meta-analysis``（13 字符）先于 ``analysis``
  （8 字符）被接受，后者与之重叠即丢弃。
- **重叠取最长**：与上一条同一机制；等长重叠时取起点更早者（并列再取
  ``term_id`` 更小者），结果确定可复现。
- 返回区间按 ``start`` 升序、两两不重叠。

资源红线（终审 #11）：词条数与命中数相乘会把候选集合放大到千万级
（500 词条 × 66,666 命中实测 38s CPU / GB 级内存），因此收集候选时对
**候选命中总数**设硬预算 :data:`MAX_CANDIDATE_MATCHES`，超限即刻抛
:class:`HighlightMatchBudgetExceeded`（API 层映射为 400 结构化错误）；
保存侧另按**匹配等价**判定去重词条（:func:`term_dedup_key` /
:func:`terms_match_identically`，见 ``custom_backend/highlight_api.py``），
只合并命中语义完全相同的词条，近形词（如 ``k`` 与 U+212A）不合并。

偏移口径（契约关键）：``start``/``end`` 是 **Unicode 码点偏移**，即 Python
``text[start:end]`` / JS ``Array.from(text).slice(start, end)`` 的口径，前端
可直接按码点渲染（对 BMP 文本与 UTF-16 下标一致；含增补平面字符时须按
码点遍历，不得用 ``String.prototype.slice``）。同时返回
``byte_start``/``byte_end``（**UTF-8 字节偏移**），供需要字节切片的调用方
使用——``text.encode("utf-8")[byte_start:byte_end].decode("utf-8")`` 必须
还原同一段文本。两种口径都在响应顶层 ``encoding`` 字段标明。
"""

from __future__ import annotations

import re
from bisect import bisect_left
from functools import lru_cache
from typing import Iterable

__all__ = [
    "ENCODING",
    "HighlightMatchBudgetExceeded",
    "MAX_CANDIDATE_MATCHES",
    "MAX_SET_NAME_LENGTH",
    "MAX_SETS",
    "MAX_TERM_LENGTH",
    "MAX_TERMS_PER_SET",
    "MAX_TEXT_LENGTH",
    "compute_highlights",
    "term_dedup_key",
    "terms_match_identically",
]

#: 偏移口径标识：字符（Unicode 码点）偏移为准，UTF-8 字节偏移并行返回。
ENCODING = "unicode-code-points"

#: 单次 compute 的文本长度上限（防御性上限；超限由 API 层返回结构化 400）。
MAX_TEXT_LENGTH = 200_000

#: 单次 compute 的**候选命中总数**硬预算（各词条 ``finditer`` 命中数之和）。
#: 词条数（≤500）× 命中数（≤文本长度）相乘可达千万级候选，收集/排序/
#: 贪心接受都会被放大（终审 #11 实测 100 词条 × 66,666 命中 → 38s CPU、
#: 1.3GB 峰值）；预算取文本上限的 5 倍——单词条满配（1 字符词 × 200k
#: 文本 = 200k 候选）及其数倍重叠仍宽裕，乘积放大则即刻熔断。
MAX_CANDIDATE_MATCHES = 5 * MAX_TEXT_LENGTH

#: 每成员最多保存的关键词组数。
MAX_SETS = 20

#: 每组最多词条数。
MAX_TERMS_PER_SET = 500

#: 单个词条最大字符数（去首尾空白后）。
MAX_TERM_LENGTH = 200

#: 组名最大字符数。
MAX_SET_NAME_LENGTH = 120


class HighlightMatchBudgetExceeded(Exception):
    """候选命中总数超出 :data:`MAX_CANDIDATE_MATCHES` 硬预算。

    :ivar limit: 预算上限（抛出时的 :data:`MAX_CANDIDATE_MATCHES` 值）。
    :ivar count: 超限瞬间的候选计数（恒为 ``limit + 1``——超限即刻抛出，
        不收集完整集合）。
    """

    def __init__(self, limit: int, count: int) -> None:
        super().__init__(
            f"highlight candidate matches {count} exceed budget {limit}")
        self.limit = limit
        self.count = count

_ASCII_WORD = re.compile(r"[A-Za-z0-9_]")


def _is_ascii_word(char: str) -> bool:
    return bool(_ASCII_WORD.match(char))


def _boundary_flags(term: str) -> tuple[bool, bool]:
    """词条首/末字符是否 ASCII 词字符——决定是否加词边界（唯一判定源）。

    :func:`_term_pattern` 的环视与保存侧去重等价判定（:func:`term_dedup_key`）
    都从这里取值，保证「同一词条 → 同一边词语义」处处一致。
    """
    return _is_ascii_word(term[0]), _is_ascii_word(term[-1])


@lru_cache(maxsize=2048)
def _term_pattern(term: str) -> re.Pattern[str]:
    """词条 → 正则（转义字面量；英文首尾加 ASCII 词边界环视）。"""
    body = re.escape(term)
    prefix_flag, suffix_flag = _boundary_flags(term)
    prefix = r"(?<![A-Za-z0-9_])" if prefix_flag else ""
    suffix = r"(?![A-Za-z0-9_])" if suffix_flag else ""
    return re.compile(prefix + body + suffix, re.IGNORECASE)


def term_dedup_key(term: str) -> tuple[bool, bool, str]:
    """保存侧去重的分组键：**(首词边界标志, 末词边界标志, 小写折叠)**。

    边界标志与 :func:`_term_pattern` 同源（:func:`_boundary_flags`），因此
    「lower 相等但边界语义不同」的近形词不会落进同一组——全 Unicode 只有
    U+212A KELVIN SIGN（``"K"`` 小写折叠为 ``"k"``，但原字符非 ASCII 词
    字符 → 不加词边界）与 U+0130（小写为 ``"i\\u0307"``，首字符从非 ASCII
    变 ASCII ``i``）两个字符会触发该情形；纯 ``lower()`` 键会把它们与
    ASCII 孪生词误合并，从而改变命中结果。
    """
    prefix_flag, suffix_flag = _boundary_flags(term)
    return prefix_flag, suffix_flag, term.lower()


def terms_match_identically(a: str, b: str) -> bool:
    """两个词条（已去首尾空白、非空）的**匹配语义是否完全等价**（可安全合并）。

    判定（全部同源于匹配实现本身）：

    1. 词边界标志相同（:func:`_boundary_flags`）——否则一个加 ``(?<!...)``
       环视、另一个不加，命中集合必然不同；
    2. 小写折叠相同（``str.lower``，与 ``re.IGNORECASE`` 的折叠表同源）；
    3. 忽略大小写**双向全匹配**（字面量转义后互为 fullmatch）——兜住
       ``lower()`` 与正则折叠在个别字符上的残余差异。

    等价词条生成的候选区间逐点相同；贪心并列（等长同起点）取 ``term_id``
    最小者，而首次出现的词条下标最小，因此「去重保留首次出现」与未去重
    基线选出**完全相同**的区间与 ``term_id``——合并不改变任何命中结果。
    """
    if a == b:
        return True
    if term_dedup_key(a) != term_dedup_key(b):
        return False
    return (re.fullmatch(re.escape(a), b, re.IGNORECASE) is not None
            and re.fullmatch(re.escape(b), a, re.IGNORECASE) is not None)


def _utf8_byte_positions(text: str, positions: Iterable[int]) -> dict[int, int]:
    """一次线性扫描，把码点下标映射为 UTF-8 字节前缀长度。"""
    wanted = sorted(set(positions))
    result: dict[int, int] = {}
    if not wanted:
        return result
    cursor = 0
    if wanted[cursor] == 0:
        result[0] = 0
        cursor += 1
    if cursor >= len(wanted):
        return result
    total = 0
    for index, char in enumerate(text):
        # surrogatepass：孤立代理项也能给出确定的字节宽度，绝不抛异常。
        total += len(char.encode("utf-8", "surrogatepass"))
        offset = index + 1
        while cursor < len(wanted) and wanted[cursor] == offset:
            result[offset] = total
            cursor += 1
        if cursor >= len(wanted):
            break
    return result


def compute_highlights(text: str, terms: Iterable[str]) -> list[dict]:
    """计算 ``text`` 中所有 ``terms`` 的命中区间（升序、互不重叠）。

    :param text: 原文（空串 → ``[]``）。
    :param terms: 词条序列；非字符串/空白词条跳过；``term_id`` 即其在序列
        中的下标（API 层用它把区间映射回词条图例）。重复词条不在此去重
        （保存侧负责）；每条重复词条照常计入候选预算。
    :return: ``[{"start", "end", "term_id", "byte_start", "byte_end"}, ...]``；
        ``start``/``end`` 为 Unicode 码点偏移，``byte_*`` 为 UTF-8 字节偏移。
    :raises HighlightMatchBudgetExceeded: 候选命中总数超过
        :data:`MAX_CANDIDATE_MATCHES`（超限即刻抛出，见模块 docstring）。
    """
    if not isinstance(text, str) or not text:
        return []

    candidates: list[tuple[int, int, int]] = []
    count = 0
    for term_id, raw in enumerate(terms or ()):
        if not isinstance(raw, str):
            continue
        term = raw.strip()
        if not term:
            continue
        for match in _term_pattern(term).finditer(text):
            start, end = match.span()
            if end > start:
                candidates.append((start, end, term_id))
                count += 1
                if count > MAX_CANDIDATE_MATCHES:
                    # 词条数 × 命中数的乘积放大即刻熔断（终审 #11）：
                    # 不等收集完整个候选集合就抛出，内存停留在预算量级。
                    raise HighlightMatchBudgetExceeded(
                        MAX_CANDIDATE_MATCHES, count)

    if not candidates:
        return []

    # 最长优先 → 起点靠前 → term_id 小者优先，保证结果确定。
    candidates.sort(key=lambda item: (-(item[1] - item[0]), item[0], item[2]))

    accepted: list[tuple[int, int, int]] = []
    starts: list[int] = []
    for start, end, term_id in candidates:
        position = bisect_left(starts, start)
        if position > 0 and accepted[position - 1][1] > start:
            continue  # 与前一个已接受区间重叠
        if position < len(accepted) and accepted[position][0] < end:
            continue  # 与后一个已接受区间重叠
        accepted.insert(position, (start, end, term_id))
        starts.insert(position, start)

    offsets = _utf8_byte_positions(
        text, (value for item in accepted for value in (item[0], item[1])))
    return [
        {
            "start": start,
            "end": end,
            "term_id": term_id,
            "byte_start": offsets[start],
            "byte_end": offsets[end],
        }
        for start, end, term_id in accepted
    ]
