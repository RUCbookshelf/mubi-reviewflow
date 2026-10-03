"""统一内部数据结构 —— 全模块共享的 Article 契约（见 SPEC.md §2）。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


def compute_content_hash(doi: str, title: str) -> str:
    """sha1(f"{doi}|{normalized_title}")，输入须为规范化后的值。"""
    return hashlib.sha1(f"{doi}|{title}".encode("utf-8")).hexdigest()


@dataclass
class Article:
    zotero_key: str
    item_type: str = ""
    title: str = ""
    authors: str = ""
    journal: str = ""
    year: int | None = None
    doi: str = ""
    abstract: str = ""
    url: str = ""
    source_format: str = ""  # "csv" | "ris"
    raw: dict = field(default_factory=dict)
    content_hash: str = ""

    def __post_init__(self) -> None:
        if not self.content_hash:
            self.content_hash = compute_content_hash(self.doi, self.title)

    def to_dict(self) -> dict:
        d = {
            "zotero_key": self.zotero_key,
            "item_type": self.item_type,
            "title": self.title,
            "authors": self.authors,
            "journal": self.journal,
            "year": self.year,
            "doi": self.doi,
            "abstract": self.abstract,
            "url": self.url,
            "source_format": self.source_format,
            "raw_json": json.dumps(self.raw, ensure_ascii=False, sort_keys=True),
            "content_hash": self.content_hash,
        }
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Article":
        raw = d.get("raw")
        if raw is None:
            raw = json.loads(d.get("raw_json") or "{}")
        return cls(
            zotero_key=d["zotero_key"],
            item_type=d.get("item_type", "") or "",
            title=d.get("title", "") or "",
            authors=d.get("authors", "") or "",
            journal=d.get("journal", "") or "",
            year=d.get("year"),
            doi=d.get("doi", "") or "",
            abstract=d.get("abstract", "") or "",
            url=d.get("url", "") or "",
            source_format=d.get("source_format", "") or "",
            raw=raw if isinstance(raw, dict) else {},
            content_hash=d.get("content_hash", "") or "",
        )
