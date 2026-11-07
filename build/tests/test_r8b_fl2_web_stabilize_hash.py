"""r8b FL2 钉子 —— #9：Web _stabilize_generated_import_keys 补 content_hash 层。

复审复现口径（out/external_reviews/r6_verify_dedup.md #9，探针 `9`）：
existing = Article(key="auto-a964f94381e5", …, raw={"_fallback_id":"oldhash:2"})
（2.1.3 形态，无 _record_id）；incoming 同 DOI/题/作者/年、
raw={"_generated_key":True,"_fallback_key":"auto-2ae7c40902ac",
"_fallback_id":"newhash:2","_record_id":"rec-abc"}（字节不同重导出）→
CLI stabilize_conflicting_keys 沿用旧键 auto-a964f94381e5，Web 修复前生成
新键 auto-2ae7c40902ac（幂等口径分叉、将插新行）。

修复：Web stabilize 在 _record_id/_fallback_id 之后补 content_hash 层
（带 CLI 同款首作者/年份门；不满足落 base_key/后缀路径）。撤修复必红。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen.models import Article, compute_content_hash


def _backend_stabilize():
    import custom_backend.main as backend_main

    return backend_main._stabilize_generated_import_keys


class WebStabilizeContentHashLayerTests(unittest.TestCase):
    def test_byte_drift_reimport_reuses_legacy_key_via_content_hash(self):
        stabilize = _backend_stabilize()
        existing = [Article(zotero_key="auto-a964f94381e5", title="Same Title",
                            authors="Smith, John", year=2020, doi="10.1/x",
                            raw={"_fallback_id": "oldhash:2"})]
        incoming = [Article(zotero_key="auto-2ae7c40902ac", title="Same Title",
                            authors="Smith, John", year=2020, doi="10.1/x",
                            raw={"_generated_key": True,
                                 "_fallback_key": "auto-2ae7c40902ac",
                                 "_fallback_id": "newhash:2",
                                 "_record_id": "rec-abc"})]
        self.assertEqual(existing[0].content_hash, incoming[0].content_hash)
        stabilize(existing, incoming)
        self.assertEqual(
            incoming[0].zotero_key, "auto-a964f94381e5",
            "字节不同重导出（_record_id 同、_fallback_id 变）应经 content_hash "
            "层沿用库内旧键（与 CLI 同口径；修复前生成新键将插新行）")

    def test_hash_layer_blocked_by_author_or_year_gate(self):
        # 门校验：content_hash 只含 doi|title——同题不同作者/异年不得沿用。
        stabilize = _backend_stabilize()
        existing = [Article(zotero_key="auto-legacy000001", title="Same Title",
                            authors="Smith, John", year=2020, doi="",
                            raw={"_fallback_id": "oldhash:9"})]
        same_hash = compute_content_hash("", "Same Title")
        diff_author = Article(
            zotero_key="auto-new00000001", title="Same Title",
            authors="Jones, Jane", year=2020,
            raw={"_generated_key": True, "_fallback_key": "auto-new00000001",
                 "_fallback_id": "newhash:9", "_record_id": "rec-1"})
        self.assertEqual(diff_author.content_hash, same_hash)
        stabilize(list(existing), [diff_author])
        self.assertEqual(diff_author.zotero_key, "auto-new00000001",
                         "同哈希但首作者不兼容：不得沿用库内键")

        diff_year = Article(
            zotero_key="auto-new00000002", title="Same Title",
            authors="Smith, John", year=2021,
            raw={"_generated_key": True, "_fallback_key": "auto-new00000002",
                 "_fallback_id": "newhash:8", "_record_id": "rec-2"})
        stabilize(list(existing), [diff_year])
        self.assertEqual(diff_year.zotero_key, "auto-new00000002",
                         "同哈希但双方年份均明确且不同：不得沿用库内键")

    def test_record_id_layer_still_wins_over_hash(self):
        # 层级保持：_record_id 命中优先（哈希层不得抢先改变既有行为）。
        stabilize = _backend_stabilize()
        existing = [Article(zotero_key="auto-legacy000002", title="Other Title",
                            authors="Doe, Jane", year=2019, doi="",
                            raw={"_record_id": "rec-fixed"})]
        incoming = [Article(zotero_key="auto-incoming0001", title="Same Title",
                            authors="Smith, John", year=2020, doi="",
                            raw={"_generated_key": True,
                                 "_fallback_key": "auto-incoming0001",
                                 "_fallback_id": "newhash:7",
                                 "_record_id": "rec-fixed"})]
        stabilize(existing, incoming)
        self.assertEqual(incoming[0].zotero_key, "auto-legacy000002")


if __name__ == "__main__":
    unittest.main()
