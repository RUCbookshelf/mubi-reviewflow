"""ERIC 增量差集（Living Review 多源化，2.1.5 Wave G2/W4）——纯计算。

ERIC 官方 API 没有日期过滤参数（``search/format/rows/start/fields`` 五参），
无法像 PubMed esearch 那样用 ``mindate`` 开增量窗口。本模块因此只提供
**ID 差集法**所需的纯函数：把 ERIC 检索命中的题录（``doc_to_article`` 形状）
与任务目录里已知的 ERIC accession 键（``EJ…``/``ED…``）求差集，差集即增量。

- 零 IO、零联网、零副作用：调用方（``custom_backend.eric_incremental_api``）
  负责取数与入库；这里只做键归一与去重，便于红钉直接调用。
- 键归一（去空白 + 大写）使同一 accession 的不同书写形态（旧客户端导出、
  大小写差异）不会互相冒充新文献。差集结果内部同样按键去重——上游分页
  重叠或同页重复键都不会让同一条文献出现两次。
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

__all__ = [
    "ERIC_ACCESSION_RE",
    "diff_new_records",
    "is_eric_accession",
    "normalize_key",
]

#: ERIC accession：EJ（期刊文章）与 ED（ERIC 文档）两种前缀 + 纯数字。
ERIC_ACCESSION_RE = re.compile(r"^E[JD]\d+$")


def normalize_key(value: object) -> str:
    """ERIC/来源键归一：去首尾空白并大写；``None`` 与非字符串按 ``str`` 处理。"""
    if value is None:
        return ""
    return str(value).strip().upper()


def is_eric_accession(value: object) -> bool:
    """是否形如 ERIC accession（``EJ1482166`` / ``ED123456``）。

    「首查基线」判定用它：任务里可能已有 PubMed（``PMID-…``）等其它来源的
    键，只有存在 ERIC accession 键才算已建立 ERIC 基线键集。
    """
    return bool(ERIC_ACCESSION_RE.match(normalize_key(value)))


def diff_new_records(records: Iterable[Mapping[str, Any]],
                     known_keys: Iterable[object]) -> list[dict[str, Any]]:
    """返回增量题录：排除已知键、跳过无稳定键的记录、结果内部按键去重。

    ``records`` 为 :func:`custom_backend.eric_search_api.doc_to_article` 的
    输出形状（``zotero_key`` 即 ERIC accession）。返回顺序保持上游命中顺序，
    便于前端按「最新命中优先」展示。
    """
    known: set[str] = {normalize_key(key) for key in known_keys or ()}
    known.discard("")
    new: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records or ():
        if not isinstance(record, Mapping):
            continue
        key = normalize_key(record.get("zotero_key"))
        if not key or key in known or key in seen:
            continue
        seen.add(key)
        new.append(dict(record))
    return new
