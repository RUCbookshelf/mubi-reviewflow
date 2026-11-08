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
    "MAX_SET_NAME_LENGTH",
    "MAX_SETS",
    "MAX_TERM_LENGTH",
    "MAX_TERMS_PER_SET",
    "MAX_TEXT_LENGTH",
    "compute_highlights",
]

#: 偏移口径标识：字符（Unicode 码点）偏移为准，UTF-8 字节偏移并行返回。
ENCODING = "unicode-code-points"

#: 单次 compute 的文本长度上限（防御性上限；超限由 API 层返回结构化 400）。
MAX_TEXT_LENGTH = 200_000

#: 每成员最多保存的关键词组数。
MAX_SETS = 20

#: 每组最多词条数。
MAX_TERMS_PER_SET = 500

#: 单个词条最大字符数（去首尾空白后）。
MAX_TERM_LENGTH = 200

#: 组名最大字符数。
MAX_SET_NAME_LENGTH = 120

_ASCII_WORD = re.compile(r"[A-Za-z0-9_]")


def _is_ascii_word(char: str) -> bool:
    return bool(_ASCII_WORD.match(char))


@lru_cache(maxsize=2048)
def _term_pattern(term: str) -> re.Pattern[str]:
    """词条 → 正则（转义字面量；英文首尾加 ASCII 词边界环视）。"""
    body = re.escape(term)
    prefix = r"(?<![A-Za-z0-9_])" if _is_ascii_word(term[0]) else ""
    suffix = r"(?![A-Za-z0-9_])" if _is_ascii_word(term[-1]) else ""
    return re.compile(prefix + body + suffix, re.IGNORECASE)


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
        中的下标（API 层用它把区间映射回词条图例）。
    :return: ``[{"start", "end", "term_id", "byte_start", "byte_end"}, ...]``；
        ``start``/``end`` 为 Unicode 码点偏移，``byte_*`` 为 UTF-8 字节偏移。
    """
    if not isinstance(text, str) or not text:
        return []

    candidates: list[tuple[int, int, int]] = []
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
