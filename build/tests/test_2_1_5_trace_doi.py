"""wave D4：研究轨迹包 DOI 关联的「期刊样式 DOI」钉子（撤修复必红）。

r17 只把样式 DOI（``10.1002/(ISSN)1545-7249`` 这类出版社/期刊级标识）从
**去重/导入复核**的身份键里摘掉（``coscreen/dedup.py:is_journal_pattern_doi``），
轨迹侧未动：``custom_backend/research_trace_api._import_metadata`` 仍按
``normalize_doi`` 等值把 ``baseline_item.after.doi`` 与 ``articles.doi`` 相连，
而这两侧都可能携带样式 DOI：

- 来源侧：``baseline_item`` 事件的 ``after.doi`` 在正常导出时直接取自
  ``articles.doi``（``coscreen/research_trace.py:692``，不做任何过滤），
  导入时又原样来自用户上传的包（``validate_import_package`` 只校验字段
  类型/长度，不校验 DOI 语义，``coscreen/research_trace_export.py:1326``）；
- 目标侧：``articles.doi`` 按 r17 口径「原样保留在文献记录、导出与展示中」
  （``coscreen/dedup.py:176-184``），因此库里同刊不同文献共享同一个样式
  DOI 是常态。

结果：一个导入主体会被同时挂到该刊**所有**文献上（本文件第一条用例在
修复前实测 ``SRC-PATTERN -> [('ART-A','doi'), ('ART-B','doi')]``）。

口径（修复后）：样式 DOI 在关联匹配中视同「无 DOI」——两侧都先经
``is_journal_pattern_doi`` 剔除再等值连接；真实文章 DOI 的关联行为逐位不变；
``zotero_key`` 等值（``match_basis='key'``）不受影响。
"""
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
FIXTURE = tempfile.TemporaryDirectory(prefix="rf_d4_trace_doi_")
os.environ.setdefault("COBOOKSHELF_DATA_DIR", FIXTURE.name)

from coscreen import db as db_mod
from coscreen import research_trace_export as exporter
from coscreen.models import Article
from custom_backend.research_trace_api import _import_metadata

PATTERN_DOI = "10.1002/(ISSN)1545-7249"
REAL_DOI = "10.1002/tesq.1234"


def _baseline_event(sequence: int, subject: str, doi: str) -> dict:
    return {
        "event_id": str(uuid.uuid4()), "store_id": "st-1", "sequence": sequence,
        "task_id": "T1", "actor_id": "42", "stage": "screen", "action": "baseline_item",
        "subject_id": subject, "before": None,
        "after": {"zotero_key": subject, "doi": doi, "item_type": "journalArticle"},
        "source": "baseline", "recorded_at": "2026-01-01T00:00:00Z",
    }


class TraceDoiAssociationTests(unittest.TestCase):
    """``_import_metadata`` 是预览与提交两个端点共用的候选匹配实现
    （``custom_backend/research_trace_api.py:1307``、``:1339``）。"""

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="rf_d4_trace_doi_case_")
        self.path = os.path.join(self.directory, "personal.db")
        db_mod.init_db(self.path)
        db_mod.upsert_articles(self.path, [
            Article(zotero_key="ART-A", title="Paper Alpha", doi=PATTERN_DOI),
            Article(zotero_key="ART-B", title="Paper Beta", doi=PATTERN_DOI),
            Article(zotero_key="ART-C", title="Paper Gamma", doi=REAL_DOI),
            Article(zotero_key="ART-D", title="Paper Delta", doi="10.1002/tesq.5678"),
        ], [])

    def _metadata(self, *events):
        snapshot = {"stores": [{
            "partition": "personal", "store_id": "st-1",
            "settings": {"status": "recording", "stages": ["screen"]},
            "summary": {}, "events": list(events),
        }]}
        parsed = exporter.validate_import_package(
            exporter.build_export_zip(snapshot, public=False))
        return _import_metadata(SimpleNamespace(task_id="T1"), 42, parsed,
                                self.path, {"personal": self.path})

    @staticmethod
    def _candidates(metadata, subject):
        row = next(item for item in metadata["subjects"] if item["subject_id"] == subject)
        return [(candidate["subject_id"], candidate["match_basis"])
                for candidate in row["candidates"]]

    def test_pattern_doi_does_not_link_a_subject_to_two_documents(self):
        # 撤修复必红：修复前一个主体被同时挂到 ART-A 与 ART-B（同刊不同文献）。
        metadata = self._metadata(_baseline_event(1, "SRC-PATTERN", PATTERN_DOI))
        self.assertEqual(self._candidates(metadata, "SRC-PATTERN"), [])

    def test_pattern_doi_case_and_width_variants_do_not_link(self):
        # 判定走 normalize_doi（NFKC + 小写），"(ISSN)"/"（ISSN）"变体同口径剔除。
        for index, variant in enumerate(
                ("10.1002/(ISSN)1545-7249", "10.1002/（ISSN）1545-7249"), start=1):
            with self.subTest(variant=variant):
                metadata = self._metadata(_baseline_event(index, "SRC-V", variant))
                self.assertEqual(self._candidates(metadata, "SRC-V"), [])

    def test_real_doi_still_links(self):
        # 对照：真实文章 DOI 的关联行为逐位不变（修复前后同为绿）。
        metadata = self._metadata(_baseline_event(1, "SRC-REAL", REAL_DOI))
        self.assertEqual(self._candidates(metadata, "SRC-REAL"), [("ART-C", "doi")])

    def test_key_match_survives_pattern_doi(self):
        # 主体键命中 ART-A 且带样式 DOI：键关联保留，样式 DOI 不额外挂 ART-B。
        # 撤修复必红：修复前候选为 [('ART-A','key'), ('ART-B','doi')]。
        metadata = self._metadata(_baseline_event(1, "ART-A", PATTERN_DOI))
        self.assertEqual(self._candidates(metadata, "ART-A"), [("ART-A", "key")])

    def test_pattern_doi_stays_excluded_when_subject_key_is_unrelated(self):
        # 目标侧过滤：主体自带的真实 DOI 与库中样式 DOI 不同，不得连接；
        # 且不得因样式 DOI 在库中重复而产出任何 doi 候选。
        metadata = self._metadata(_baseline_event(1, "SRC-REAL-KEY", REAL_DOI))
        candidates = self._candidates(metadata, "SRC-REAL-KEY")
        self.assertEqual(candidates, [("ART-C", "doi")])
        self.assertNotIn("ART-A", [target for target, _basis in candidates])
        self.assertNotIn("ART-B", [target for target, _basis in candidates])

    def test_contract_documented_on_helper(self):
        # 文档钉子：口径写在匹配实现的 docstring 里，防未来扩散成「未说明的边角」。
        doc = _import_metadata.__doc__ or ""
        self.assertIn("期刊样式 DOI", doc)
        self.assertIn("is_journal_pattern_doi", doc)
        self.assertIn("zotero_key", doc)


if __name__ == "__main__":
    unittest.main()
