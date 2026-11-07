"""r13 · Crossref 标题检索响应形状钉子（终扫低危 be-outbound-1）。

问题：``_crossref_message`` 的标题检索路径写 ``payload.get("message", {}).get("items")``。
当上游返回 ``{"message": null}`` 或 ``{"message": []}`` 时，``.get("message", {})``
拿到的是 ``None``/``list``（不是缺省 ``{}``），再 ``.get`` 抛 ``AttributeError``；
``metadata-suggestions`` 路由只捕获 ``OutboundError``/SQLite，异常上抛 → 500。

钉子口径：与 DOI 路径同的 ``isinstance`` 守卫——``message`` 非 dict 时优雅返回
「无建议」，不得 500。探针来源：``E:/rf214tmp/dwave/be-outbound/probe_crossref.py``。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from custom_backend import pdf_fetch_api  # noqa: E402

SETTINGS = {"crossref_base_url": "https://api.crossref.org", "email": ""}
ARTICLE = {"title": "Some Title Without Doi", "doi": "", "year": None, "abstract": ""}


class CrossrefShapeTests(unittest.TestCase):
    def _suggestions_for(self, payload):
        with mock.patch.object(pdf_fetch_api, "_get_json", return_value=payload):
            return pdf_fetch_api._metadata_suggestions(dict(ARTICLE), dict(SETTINGS))

    def test_message_null_returns_no_suggestions(self):
        result = self._suggestions_for({"message": None})
        self.assertEqual(result["suggestions"], {})
        self.assertEqual(result["matched_by"], "none")
        self.assertEqual(result["source"], "crossref")

    def test_message_list_returns_no_suggestions(self):
        result = self._suggestions_for({"message": []})
        self.assertEqual(result["suggestions"], {})
        self.assertEqual(result["matched_by"], "none")

    def test_non_dict_payload_returns_no_suggestions(self):
        result = self._suggestions_for(["not", "a", "dict"])
        self.assertEqual(result["suggestions"], {})
        self.assertEqual(result["matched_by"], "none")

    def test_message_dict_with_items_still_matches(self):
        result = self._suggestions_for({"message": {"items": [
            {"DOI": "10.9999/found", "title": ["Some Title Without Doi"]},
        ]}})
        self.assertEqual(result["matched_by"], "title")
        self.assertEqual(result["suggestions"].get("doi"), "10.9999/found")

    def test_message_dict_without_items_still_reports_bad_shape(self):
        """真·形状异常（dict 但无 items 列表）仍按上游坏响应降级，不是 500。"""
        with mock.patch.object(pdf_fetch_api, "_get_json",
                               return_value={"message": {"items": "oops"}}):
            with self.assertRaises(pdf_fetch_api.OutboundError):
                pdf_fetch_api._crossref_message(dict(ARTICLE), dict(SETTINGS))


if __name__ == "__main__":
    unittest.main()
