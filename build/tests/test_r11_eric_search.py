# -*- coding: utf-8 -*-
"""R11 · ERIC 教育检索通道钉子（专属新文件，撤实现必红）。

覆盖：
1. 题录转换纯函数：ERIC 字段 → Article 形状逐字段（ID/title/author/description/
   publicationdateyear/source/doi/url/publicationtype），作者分号规范、DOI 从
   doi.org 链接提取、坏年份/缺 ID 兜底；合成 RIS 经真实 ris_parser 往返回读。
2. url_opener 注入的确定性夹具五路：成功 / 空结果 / 坏 JSON / 超时 / 非 2xx
   （另加 3xx 拒绝、ERIC ``error`` 载荷、读取上限）；出站 URL 恒为
   api.ies.ed.gov 且不请求 ``doi`` 字段。
3. 默认零真实网络：不传 url_opener 时只经模块 ``_open``；monkeypatch
   ``_OPENER`` 记录调用并抛错——端点可读 4xx、绝不 500、绝不外呼。
4. 真实 TestClient 端到端：预览不入库；导入走受保护管线（fresh 直接激活 /
   已有研究 staged 待审 + ERIC ID 稳定键 + 重导幂等 + 活跃语料精确去重）；
   零结果 / 超限 / 网络失败 / 管线未预期异常不建批次且绝不 500。
5. 静态治理：固定端点、拒绝重定向、读取上限、无后台线程/定时器。
6. 可选真网 smoke：``RF_ERIC_LIVE=1`` 才跑，默认 skip。
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from contextlib import closing
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
fixture = tempfile.TemporaryDirectory(prefix="rf_r11_eric_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend import eric_search_api as eric  # noqa: E402
from custom_backend.main import app  # noqa: E402
from coscreen.parsers.ris_parser import parse_ris  # noqa: E402


# ---------------------------------------------------------------------------
# 真实形状夹具（字段名/结构照 2026-11 ERIC API 实测）
# ---------------------------------------------------------------------------

DOC_JOURNAL = {
    "id": "EJ1482166",
    "title": "Instructed Second Language Acquisition and Second Language Pronunciation",
    "author": ["Dustin Crowther", "Shawn Loewen"],
    "description": "Instructed second language acquisition (ISLA) inquiry emphasizes "
                   "the ways in which systematic manipulation of learning conditions "
                   "may facilitate acquisition.",
    "source": "Language Learning",
    "publicationtype": ["Journal Articles", "Reports - Evaluative"],
    "publicationdateyear": 2025,
    "url": "http://dx.doi.org/10.1111/lang.12716",
    "peerreviewed": "T",
}

DOC_BOOK = {
    "id": "ED230060",
    "title": "Second Language Acquisition Studies.",
    "author": ["Bailey, Kathleen M., Ed.", "And Others"],
    "description": "This collection of 16 papers covers theoretical issues.",
    "source": "ERIC Publications",
    "publicationtype": ["Books", "Reports - Research"],
    "publicationdateyear": 1982,
    "url": "",
    "peerreviewed": "F",
}

DOC_SEMICOLON_AUTHORS = {
    "id": "EJ000001",
    "title": "Pragmatics in the classroom",
    "author": "Smith, John; Doe, Jane",
    "description": "A study of classroom pragmatics.",
    "source": "System",
    "publicationtype": "Journal Articles",
    "publicationdateyear": "2020",
    "url": "https://doi.org/10.1000/ABC.",
}

DOC_NO_ID = {"title": "Untitled record", "author": ["Nobody"]}
DOC_BAD_YEAR = {
    "id": "EJ000002", "title": "Bad year", "author": [],
    "publicationdateyear": "Spring 2020", "url": "",
}

DOCS = [DOC_JOURNAL, DOC_BOOK, DOC_SEMICOLON_AUTHORS, DOC_NO_ID, DOC_BAD_YEAR]
VALID_KEYS = ["EJ1482166", "ED230060", "EJ000001", "EJ000002"]


def eric_payload(docs, total=None, start=0):
    return json.dumps({
        "response": {
            "numFound": len(docs) if total is None else total,
            "start": start,
            "numFoundExact": True,
            "docs": docs,
        }
    }, ensure_ascii=False).encode("utf-8")


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def read(self, limit=-1):
        return self._data if limit < 0 else self._data[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _RecordingOpener:
    """记录 open() 调用并拒绝出站（证明零真实网络 / 固定端点）。"""

    def __init__(self, exc=None):
        self.calls = []
        self.exc = exc or urllib.error.URLError(OSError(10061, "Connection refused"))

    def open(self, request, timeout=None):
        self.calls.append((request.full_url, timeout))
        raise self.exc


class _BytesOpener:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def open(self, request, timeout=None):
        self.calls.append((request.full_url, timeout))
        return _FakeResponse(self.payload)


# ===========================================================================
# 1. 题录转换纯函数
# ===========================================================================

class ConversionTests(unittest.TestCase):
    def test_eric_fields_map_to_article_shape_field_by_field(self):
        article = eric.doc_to_article(DOC_JOURNAL)
        self.assertEqual(article["zotero_key"], "EJ1482166")
        self.assertEqual(article["item_type"], "JOUR")
        self.assertEqual(article["title"], DOC_JOURNAL["title"])
        self.assertEqual(article["authors"], "Dustin Crowther; Shawn Loewen")
        self.assertEqual(article["journal"], "Language Learning")
        self.assertEqual(article["year"], 2025)
        self.assertEqual(article["doi"], "10.1111/lang.12716")
        self.assertEqual(article["abstract"], DOC_JOURNAL["description"])
        self.assertEqual(article["url"], "https://eric.ed.gov/?id=EJ1482166")
        self.assertEqual(article["source_format"], "eric")
        self.assertEqual(article["eric_id"], "EJ1482166")

    def test_author_list_and_semicolon_string_are_normalized(self):
        self.assertEqual(eric.doc_to_article(DOC_JOURNAL)["authors"],
                         "Dustin Crowther; Shawn Loewen")
        self.assertEqual(eric.doc_to_article(DOC_SEMICOLON_AUTHORS)["authors"],
                         "Smith, John; Doe, Jane")
        # 数组元素自身含分号也要拆开（ERIC 导出形态混用）
        mixed = dict(DOC_JOURNAL, author=["A One; B Two", "C Three"])
        self.assertEqual(eric.doc_to_article(mixed)["authors"],
                         "A One; B Two; C Three")

    def test_title_normalized_and_year_strings_accepted(self):
        book = eric.doc_to_article(DOC_BOOK)
        self.assertEqual(book["title"], "Second Language Acquisition Studies")
        self.assertEqual(book["year"], 1982)
        self.assertEqual(book["url"], "https://eric.ed.gov/?id=ED230060")
        self.assertEqual(book["doi"], "")
        self.assertEqual(book["item_type"], "BOOK")
        self.assertEqual(eric.doc_to_article(DOC_SEMICOLON_AUTHORS)["year"], 2020)

    def test_bad_records_are_skipped_not_crashing(self):
        self.assertIsNone(eric.doc_to_article(DOC_NO_ID))
        self.assertIsNone(eric.doc_to_article({"id": "12"}))  # 纯序号不作全局键
        self.assertIsNone(eric.doc_to_article({"id": "E J 1"}))  # 含空白
        self.assertIsNone(eric.doc_to_article("not a dict"))
        bad_year = eric.doc_to_article(DOC_BAD_YEAR)
        self.assertIsNotNone(bad_year)
        self.assertIsNone(bad_year["year"])

    def test_ris_round_trip_keeps_eric_ids_and_fields(self):
        articles = [a for a in (eric.doc_to_article(d) for d in DOCS) if a]
        text = eric.articles_to_ris(articles)
        with tempfile.NamedTemporaryFile("w", suffix=".ris", delete=False,
                                         encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            path = handle.name
        try:
            parsed = parse_ris(path)
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertEqual([a.zotero_key for a in parsed], VALID_KEYS)
        first = parsed[0]
        self.assertEqual(first.title, DOC_JOURNAL["title"])
        self.assertEqual(first.authors, "Dustin Crowther; Shawn Loewen")
        self.assertEqual(first.journal, "Language Learning")
        self.assertEqual(first.year, 2025)
        self.assertEqual(first.doi, "10.1111/lang.12716")
        self.assertEqual(first.abstract, DOC_JOURNAL["description"])
        self.assertEqual(first.url, "https://eric.ed.gov/?id=EJ1482166")
        self.assertTrue(first.item_type)
        # ERIC ID 是全局稳定键：不得被解析器降级为 auto-* / 追加降级标记
        for article in parsed:
            self.assertNotIn("_fallback_key", article.raw)
            self.assertNotIn("_demoted_source_key", article.raw)
            self.assertFalse(article.zotero_key.startswith("auto-"))

    def test_control_and_surrogate_characters_never_reach_the_ris(self):
        dirty = dict(DOC_JOURNAL, title="T\x00itle\x01 with control",
                     description="Abs\x00tract")
        article = eric.doc_to_article(dirty)
        text = eric.articles_to_ris([article])
        self.assertNotIn("\x00", text)
        self.assertNotIn("\x01", text)
        self.assertIn("Title with control", text)

    def test_provenance_name_is_safe_and_query_specific(self):
        name = eric._provenance_name('title:"a/b" AND c:d')
        self.assertTrue(name.startswith("ERIC 检索 "))
        self.assertTrue(name.endswith(".ris"))
        self.assertNotIn("/", name)
        self.assertNotIn(":", name)
        self.assertEqual(name, eric._provenance_name('  title:"a/b"   AND c:d '))
        self.assertNotEqual(name, eric._provenance_name("another query"))
        # 孤立代理字符不得让文件名哈希抛 UnicodeEncodeError
        self.assertTrue(eric._provenance_name("x\ud800y").endswith(".ris"))

    def test_provenance_name_is_byte_safe_for_astral_queries(self):
        """emoji 检索式：可见部分按 UTF-8 字节截断，文件名恒 < 255 字节。

        只按字符截断（60 个四字节字符）会得到 240+ 字节文件名，ext4 上
        ENAMETOOLONG → 500；这里钉住字节上限与多字节字符不被切坏。
        """
        name = eric._provenance_name("😀" * 60)
        self.assertTrue(name.endswith(".ris"))
        self.assertLessEqual(len(name.encode("utf-8")), 200)
        self.assertNotIn("\ufffd", name)  # 不切断字符（截断点落在边界上）

    def test_query_validation_collapses_whitespace_and_rejects_abuse(self):
        self.assertEqual(eric._normalize_query("  a \n b  "), "a b")
        # 孤立代理字符/控制符在入口洗掉：urlencode 与文件名哈希都不能因此抛错。
        self.assertEqual(eric._normalize_query("x\ud800y\x00z"), "x\ufffdyz")
        for bad in ("", "   ", None, 42, ["x"], "x" * (eric.MAX_QUERY_CHARS + 1)):
            with self.subTest(bad=bad):
                with self.assertRaises(eric.EricSearchError) as ctx:
                    eric._normalize_query(bad)
                self.assertEqual(ctx.exception.code, "eric_bad_query")


# ===========================================================================
# 2. url_opener 注入：成功 / 空 / 坏 JSON / 超时 / 非 2xx
# ===========================================================================

class InjectedOpenerTests(unittest.TestCase):
    def test_success_returns_total_and_records(self):
        calls = []

        def opener(url, timeout):
            calls.append((url, timeout))
            return eric_payload(DOCS)

        result = eric.search_eric_preview("second language", 20, url_opener=opener)
        self.assertEqual(result["total_hits"], 5)
        self.assertEqual(result["returned"], 4)
        self.assertEqual([r["zotero_key"] for r in result["records"]], VALID_KEYS)
        self.assertEqual(result["source"], "eric")
        self.assertEqual(result["endpoint"], eric.ERIC_API_URL)
        self.assertEqual(len(calls), 1)
        parsed = urllib.parse.urlsplit(calls[0][0])
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.hostname, "api.ies.ed.gov")
        self.assertEqual(parsed.path, "/eric/")
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(query["search"], ["second language"])
        self.assertEqual(query["format"], ["json"])
        self.assertEqual(query["rows"], ["20"])
        self.assertEqual(query["start"], ["0"])
        # doi 不是可请求字段（请求它会 HTTP 200 + error）；绝不放进 fields
        self.assertNotIn("doi", query["fields"][0].split(","))

    def test_empty_result_is_not_an_error(self):
        result = eric.search_eric_preview(
            "no hits", 20, url_opener=lambda url, timeout: eric_payload([]))
        self.assertEqual(result["total_hits"], 0)
        self.assertEqual(result["records"], [])
        self.assertEqual(result["returned"], 0)

    def test_bad_json_degrades_with_bad_response(self):
        for payload in (b"not json", b"[]", b'{"response": {}}',
                        b'{"response": {"numFound": 1, "docs": [1]}}',
                        eric_payload([], total=3)):
            with self.subTest(payload=payload):
                with self.assertRaises(eric.EricSearchError) as ctx:
                    eric.search_eric_preview(
                        "x", 5, url_opener=lambda url, timeout, p=payload: p)
                self.assertEqual(ctx.exception.code, "eric_bad_response")

    def test_timeout_degrades_with_timeout_code(self):
        def opener(url, timeout):
            raise TimeoutError("timed out")

        with self.assertRaises(eric.EricSearchError) as ctx:
            eric.search_eric_preview("x", 5, url_opener=opener)
        self.assertEqual(ctx.exception.code, "eric_timeout")

    def test_non_2xx_degrades_with_http_error(self):
        def opener(url, timeout):
            raise urllib.error.HTTPError(url, 503, "Service Unavailable",
                                         {}, io.BytesIO(b""))

        with self.assertRaises(eric.EricSearchError) as ctx:
            eric.search_eric_preview("x", 5, url_opener=opener)
        self.assertEqual(ctx.exception.code, "eric_http_error")

    def test_redirect_is_refused(self):
        def opener(url, timeout):
            raise urllib.error.HTTPError(url, 302, "Found",
                                         {"Location": "https://evil.example/"},
                                         io.BytesIO(b""))

        with self.assertRaises(eric.EricSearchError) as ctx:
            eric.search_eric_preview("x", 5, url_opener=opener)
        self.assertEqual(ctx.exception.code, "eric_redirect")

    def test_eric_error_payload_is_query_error_even_on_http_200(self):
        payload = json.dumps({
            "error": {"metadata": ["error-class", "SolrException"],
                      "msg": "undefined field doi", "code": 400},
        }).encode("utf-8")
        with self.assertRaises(eric.EricSearchError) as ctx:
            eric.search_eric_preview(
                "doi:10.1/x", 5, url_opener=lambda url, timeout: payload)
        self.assertEqual(ctx.exception.code, "eric_query_error")
        self.assertIn("undefined field doi", str(ctx.exception))

    def test_opener_returning_non_bytes_is_normalized(self):
        with self.assertRaises(eric.EricSearchError) as ctx:
            eric.search_eric_preview(
                "x", 5, url_opener=lambda url, timeout: "<html>")
        self.assertEqual(ctx.exception.code, "eric_bad_response")

    def test_lone_surrogate_in_opener_error_never_escapes_raw(self):
        def opener(url, timeout):
            raise urllib.error.URLError("\ud800")

        with self.assertRaises(eric.EricSearchError) as ctx:
            eric.search_eric_preview("x", 5, url_opener=opener)
        self.assertEqual(ctx.exception.code, "eric_unreachable")


class ReadLimitTests(unittest.TestCase):
    def test_response_over_limit_is_refused_before_parsing(self):
        opener = _BytesOpener(b"x" * (eric.MAX_RESPONSE_BYTES + 1))
        with mock.patch.object(eric, "_OPENER", opener):
            with self.assertRaises(eric.EricSearchError) as ctx:
                eric._open(eric.ERIC_API_URL)
        self.assertEqual(ctx.exception.code, "eric_response_too_large")
        self.assertEqual(len(opener.calls), 1)

    def test_no_redirect_handler_really_refuses(self):
        handler = eric._NoRedirectHandler()
        self.assertIsNone(handler.redirect_request(None, None, 302, "", {}, "https://x"))


class _RaisingOpener:
    def __init__(self, exc):
        self.exc = exc
        self.calls = []

    def open(self, request, timeout=None):
        self.calls.append((request.full_url, timeout))
        raise self.exc


class ProductionOpenerTests(unittest.TestCase):
    """生产 ``_open``（非注入）的状态码/超时分流也要钉住。"""

    def test_redirect_status_is_refused(self):
        opener = _RaisingOpener(urllib.error.HTTPError(
            eric.ERIC_API_URL, 302, "Found", {"Location": "https://evil.example/"},
            io.BytesIO(b"")))
        with mock.patch.object(eric, "_OPENER", opener):
            with self.assertRaises(eric.EricSearchError) as ctx:
                eric._open(eric.ERIC_API_URL)
        self.assertEqual(ctx.exception.code, "eric_redirect")

    def test_non_2xx_is_http_error_without_body_echo(self):
        opener = _RaisingOpener(urllib.error.HTTPError(
            eric.ERIC_API_URL, 503, "down", {}, io.BytesIO(b"secret body")))
        with mock.patch.object(eric, "_OPENER", opener):
            with self.assertRaises(eric.EricSearchError) as ctx:
                eric._open(eric.ERIC_API_URL)
        self.assertEqual(ctx.exception.code, "eric_http_error")
        self.assertNotIn("secret body", str(ctx.exception))

    def test_timeout_and_unreachable_are_distinguished(self):
        with mock.patch.object(
                eric, "_OPENER",
                _RaisingOpener(urllib.error.URLError(TimeoutError("timed out")))):
            with self.assertRaises(eric.EricSearchError) as ctx:
                eric._open(eric.ERIC_API_URL)
        self.assertEqual(ctx.exception.code, "eric_timeout")
        with mock.patch.object(
                eric, "_OPENER",
                _RaisingOpener(urllib.error.URLError(OSError(10061, "refused")))):
            with self.assertRaises(eric.EricSearchError) as ctx:
                eric._open(eric.ERIC_API_URL)
        self.assertEqual(ctx.exception.code, "eric_unreachable")


# ===========================================================================
# 3. 默认零真实网络（不传注入 → 只经模块 _open；_OPENER 被替换）
# ===========================================================================

class ZeroNetworkTests(unittest.TestCase):
    def test_preview_without_injection_only_calls_module_opener(self):
        opener = _RecordingOpener()
        with mock.patch.object(eric, "_OPENER", opener):
            with self.assertRaises(eric.EricSearchError) as ctx:
                eric.search_eric_preview("second language")
        self.assertEqual(ctx.exception.code, "eric_unreachable")
        self.assertEqual(len(opener.calls), 1)
        self.assertTrue(opener.calls[0][0].startswith("https://api.ies.ed.gov/eric/?"))
        self.assertEqual(opener.calls[0][1], eric.DEFAULT_TIMEOUT_SECONDS)

    def test_endpoint_without_injection_only_calls_module_opener(self):
        """真实 TestClient + 无 url_opener 注入：只经模块 _OPENER，零真实外呼。"""
        client = TestClient(app, raise_server_exceptions=False)
        user = auth_mod.create_user("r11eric-net@example.test", "R11-ERIC-NET!")
        auth = {"Authorization": "Bearer " +
                auth_mod.create_access_token(user["id"], user["username"])}
        task = client.post("/api/tasks", json={"name": "r11-eric-zero-net"},
                           headers=auth).json()["task_id"]
        opener = _RecordingOpener()
        with mock.patch.object(eric, "_OPENER", opener):
            resp = client.post(f"/api/tasks/{task}/eric/search", headers=auth,
                               json={"query": "second language"})
        self.assertEqual(resp.status_code, 409, resp.text)
        self.assertEqual(resp.json()["detail"]["code"], "eric_unreachable")
        self.assertEqual(len(opener.calls), 1)
        self.assertTrue(opener.calls[0][0].startswith(
            "https://api.ies.ed.gov/eric/?"))

    def test_module_source_has_no_background_network_surface(self):
        source = Path(eric.__file__).read_text(encoding="utf-8")
        for token in ("threading", "Timer(", "create_task", "schedule",
                      "apscheduler", "BackgroundTasks"):
            self.assertNotIn(token, source, f"不得出现后台/定时网络面：{token}")
        self.assertIn('ERIC_API_URL = "https://api.ies.ed.gov/eric/"', source)
        self.assertNotIn("http://", source)


# ===========================================================================
# 4. 真实 TestClient 端到端
# ===========================================================================

class EricEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        user = auth_mod.create_user("r11eric@example.test", "R11-ERIC-2026!")
        cls.auth = {"Authorization": "Bearer " +
                    auth_mod.create_access_token(user["id"], user["username"])}
        cls.user = {"user_id": user["id"], "username": user["username"]}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        self.task = self.client.post(
            "/api/tasks", json={"name": self._testMethodName},
            headers=self.auth).json()["task_id"]
        self.base = f"/api/tasks/{self.task}/eric"

    def _mark_researched(self):
        from coscreen import db as db_mod
        from coscreen import review_imports as store
        from coscreen import review_workflow as wf
        ctx = app.state.review_workflow.context(self.task, self.user)
        db_mod.init_db(ctx.owner_db)
        store.migrate(ctx.owner_db)
        with closing(wf.connect(ctx.owner_db)) as conn, conn:
            conn.execute("UPDATE workflow_import_state SET researched=1 WHERE singleton=1")

    def _staged_records(self):
        from coscreen import review_workflow as wf
        ctx = app.state.review_workflow.context(self.task, self.user)
        with closing(wf.connect(ctx.owner_db, read_only=True)) as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table'"
                " AND name='workflow_source_records'").fetchone()
            if not exists:
                return []
            return conn.execute(
                "SELECT incoming_key, filename, state FROM workflow_source_records"
            ).fetchall()

    def _batches(self):
        return self.client.get(f"/api/tasks/{self.task}/import-batches",
                               headers=self.auth).json()

    def test_preview_does_not_write_anything(self):
        with mock.patch.object(eric, "_open", return_value=eric_payload(DOCS)):
            resp = self.client.post(f"{self.base}/search", headers=self.auth,
                                    json={"query": "second language", "rows": 20})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["total_hits"], 5)
        self.assertEqual([r["zotero_key"] for r in body["records"]], VALID_KEYS)
        self.assertEqual(body["source"], "eric")
        self.assertEqual(self._batches()["total"], 0)
        self.assertEqual(self._staged_records(), [])

    def test_preview_empty_result_is_200(self):
        with mock.patch.object(eric, "_open", return_value=eric_payload([])):
            resp = self.client.post(f"{self.base}/search", headers=self.auth,
                                    json={"query": "no hits", "rows": 5})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["total_hits"], 0)

    def test_preview_failure_matrix_is_4xx_never_500(self):
        payloads = [
            (b"not json", 409, "eric_bad_response"),
            (TimeoutError("timed out"), 409, "eric_timeout"),
            (urllib.error.HTTPError("https://api.ies.ed.gov/eric/", 503,
                                    "down", {}, io.BytesIO(b"")), 409,
             "eric_http_error"),
            (urllib.error.URLError(OSError(10061, "refused")), 409,
             "eric_unreachable"),
        ]
        for failure, expected_status, code in payloads:
            with self.subTest(code=code):
                if isinstance(failure, BaseException):
                    patcher = mock.patch.object(eric, "_open", side_effect=failure)
                else:
                    patcher = mock.patch.object(eric, "_open", return_value=failure)
                with patcher:
                    resp = self.client.post(f"{self.base}/search", headers=self.auth,
                                            json={"query": "x", "rows": 5})
                self.assertEqual(resp.status_code, expected_status, resp.text)
                self.assertEqual(resp.json()["detail"]["code"], code)
                self.assertIn("endpoint", resp.json()["detail"])
        self.assertEqual(self._batches()["total"], 0)

    def test_preview_query_error_is_422(self):
        payload = json.dumps({"error": {"msg": "undefined field doi",
                                        "code": 400}}).encode("utf-8")
        with mock.patch.object(eric, "_open", return_value=payload):
            resp = self.client.post(f"{self.base}/search", headers=self.auth,
                                    json={"query": "doi:10.1/x", "rows": 5})
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertEqual(resp.json()["detail"]["code"], "eric_query_error")

    def test_preview_rejects_blank_query_and_bad_rows(self):
        for body in ({"query": "   "}, {"query": "x", "rows": 0},
                     {"query": "x", "rows": 999}):
            with self.subTest(body=body):
                resp = self.client.post(f"{self.base}/search", headers=self.auth,
                                        json=body)
                self.assertIn(resp.status_code, (400, 422), resp.text)
        self.assertEqual(self._batches()["total"], 0)

    def test_lone_surrogate_query_never_500s(self):
        """JSON 的 ``"\\ud800"`` 是合法转义：入口洗成 U+FFFD，响应仍可序列化。

        httpx 的 ``json=`` 会在客户端 UTF-8 编码时先炸，因此这里发送
        ``ensure_ascii=True`` 的原始字节——与浏览器 JSON.stringify 的真实
        转义形态一致。
        """
        raw = json.dumps({"query": "x\ud800y"}).encode("ascii")
        headers = {**self.auth, "Content-Type": "application/json"}
        with mock.patch.object(eric, "_open", return_value=eric_payload([])):
            resp = self.client.post(f"{self.base}/search", headers=headers,
                                    content=raw)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["total_hits"], 0)
        with mock.patch.object(eric, "_open", return_value=eric_payload([])):
            resp = self.client.post(f"{self.base}/import", headers=headers,
                                    content=raw)
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertEqual(resp.json()["detail"]["code"], "eric_no_results")
        self.assertEqual(self._batches()["total"], 0)

    def test_import_calls_protected_pipeline_with_virtual_ris(self):
        """管线注入断言：单文件虚拟 RIS + 受保护导入的固定调用口径。"""
        original = app.state.review_imports.upload
        calls = []

        async def spy(task_id, user, files, **kwargs):
            calls.append((task_id, user, files, kwargs))
            return await original(task_id, user, files, **kwargs)

        with mock.patch.object(eric, "_open", return_value=eric_payload(DOCS[:2])), \
                mock.patch.object(app.state.review_imports, "upload", side_effect=spy):
            resp = self.client.post(f"{self.base}/import", headers=self.auth,
                                    json={"query": "second language"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(len(calls), 1)
        task_id, _user, files, kwargs = calls[0]
        self.assertEqual(task_id, self.task)
        self.assertEqual(kwargs, {"batch": False, "source_ids": None,
                                  "job_id": None, "screener": None})
        self.assertEqual(len(files), 1)
        self.assertIsInstance(files[0], eric._VirtualRisUpload)
        self.assertTrue(files[0].filename.startswith("ERIC 检索 "))
        self.assertTrue(files[0].filename.endswith(".ris"))

    def test_import_fresh_task_direct_activation(self):
        with mock.patch.object(eric, "_open", return_value=eric_payload(DOCS[:2])):
            resp = self.client.post(f"{self.base}/import", headers=self.auth,
                                    json={"query": "second language"})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertFalse(body["staged"])
        self.assertEqual(body["n_imported"], 2)
        self.assertEqual(body["progress"]["total"], 2)
        self.assertEqual(body["eric"]["total_hits"], 2)
        self.assertEqual(body["eric"]["fetched"], 2)
        self.assertEqual(body["eric"]["endpoint"], eric.ERIC_API_URL)
        detail = self.client.get(
            f"/api/tasks/{self.task}/import-batches/{body['batch_id']}",
            headers=self.auth).json()
        self.assertEqual(detail["files"][0]["filename"], body["eric"]["virtual_file"])

    def test_import_reimport_dedupes_against_active_corpus(self):
        """同一 ERIC 检索再导入：ERIC ID 作为精确键被去重，绝不重复入库。"""
        payload = eric_payload(DOCS[:2])
        with mock.patch.object(eric, "_open", return_value=payload):
            first = self.client.post(f"{self.base}/import", headers=self.auth,
                                     json={"query": "second language"})
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.json()["n_imported"], 2)
            second = self.client.post(f"{self.base}/import", headers=self.auth,
                                      json={"query": "second language"})
        self.assertEqual(second.status_code, 200, second.text)
        body = second.json()
        self.assertFalse(body["staged"])
        # 受保护管线把同键记录识别为既有文献：零新增激活、精确命中 2 条、
        # 去重后唯一数 0，筛选队列总数保持不变（n_imported 是该管线对
        # 「已处理导入行」的口径，含既有来源链接，不代表新激活本文）。
        self.assertEqual(body["activated_new"], 0, body)
        self.assertEqual(body["n_new"], 0, body)
        self.assertEqual(body["n_existing"], 2, body)
        self.assertEqual(body["exact_match_hits"], 2, body)
        self.assertEqual(body["exact_duplicate_excess"], 2, body)
        self.assertEqual(body["unique_after_exact_dedup"], 0, body)
        self.assertEqual(body["progress"]["total"], 2)
        self.assertEqual(self._batches()["total"], 2)

    def test_import_existing_research_stages_then_reimport_is_idempotent(self):
        self._mark_researched()
        with mock.patch.object(eric, "_open", return_value=eric_payload(DOCS[:2])):
            first = self.client.post(f"{self.base}/import", headers=self.auth,
                                     json={"query": "second language"})
            self.assertEqual(first.status_code, 200, first.text)
            body = first.json()
            self.assertTrue(body["staged"], body)
            self.assertEqual(body["pending_records"], 2)
            self.assertEqual(body["n_parsed"], 2)
            self.assertEqual(body["progress"]["total"], 0)
            rows = self._staged_records()
            self.assertEqual({row["incoming_key"] for row in rows},
                             {"EJ1482166", "ED230060"})
            self.assertTrue(all(row["state"] == "staged" for row in rows), rows)
            # 同一检索式重导：来源行复用，绝不新增第二条同 ID 待审记录
            second = self.client.post(f"{self.base}/import", headers=self.auth,
                                      json={"query": "second language"})
        self.assertEqual(second.status_code, 200, second.text)
        rows_after = self._staged_records()
        self.assertEqual(len(rows_after), len(rows), rows_after)
        self.assertEqual({row["incoming_key"] for row in rows_after},
                         {"EJ1482166", "ED230060"})

    def test_import_zero_results_is_422_without_batch(self):
        with mock.patch.object(eric, "_open", return_value=eric_payload([])):
            resp = self.client.post(f"{self.base}/import", headers=self.auth,
                                    json={"query": "no hits"})
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertEqual(resp.json()["detail"]["code"], "eric_no_results")
        self.assertEqual(self._batches()["total"], 0)

    def test_import_too_many_hits_is_422_without_upload(self):
        # numFound 600 > 500：只取首页也必须先判超限，绝不静默截断。
        payload = eric_payload(DOCS[:2], total=600)
        with mock.patch.object(eric, "_open", return_value=payload) as opened:
            resp = self.client.post(f"{self.base}/import", headers=self.auth,
                                    json={"query": "too broad"})
        self.assertEqual(resp.status_code, 422, resp.text)
        detail = resp.json()["detail"]
        self.assertEqual(detail["code"], "eric_too_many_hits")
        self.assertEqual(detail["total_hits"], 600)
        self.assertEqual(detail["limit"], eric.MAX_IMPORT_RECORDS)
        opened.assert_called_once()  # 首页即判超限，不进入分页
        self.assertEqual(self._batches()["total"], 0)

    def test_import_network_failure_is_4xx_without_batch(self):
        with mock.patch.object(
                eric, "_open",
                side_effect=urllib.error.URLError(OSError(10061, "refused"))):
            resp = self.client.post(f"{self.base}/import", headers=self.auth,
                                    json={"query": "x"})
        self.assertEqual(resp.status_code, 409, resp.text)
        self.assertEqual(resp.json()["detail"]["code"], "eric_unreachable")
        self.assertEqual(self._batches()["total"], 0)

    def test_import_pipeline_crash_is_409_never_500(self):
        """受保护管线抛出未预期异常：最后一道闸门降级为可读 409，绝不 500。"""
        async def boom(task_id, user, files, **kwargs):
            raise RuntimeError("disk full")

        with mock.patch.object(eric, "_open", return_value=eric_payload(DOCS[:2])), \
                mock.patch.object(app.state.review_imports, "upload", side_effect=boom):
            resp = self.client.post(f"{self.base}/import", headers=self.auth,
                                    json={"query": "second language"})
        self.assertEqual(resp.status_code, 409, resp.text)
        self.assertEqual(resp.json()["detail"]["code"], "eric_unavailable")
        self.assertEqual(resp.json()["detail"]["hint"], "retry_or_manual_import")
        self.assertEqual(self._batches()["total"], 0)

    def test_import_pagination_fetches_all_pages(self):
        """numFound=3、页大小被压到 1 时逐页取回 3 条（不返回半截结果）。"""
        calls = []

        def opener(url, timeout):
            calls.append(url)
            start = int(urllib.parse.parse_qs(
                urllib.parse.urlsplit(url).query)["start"][0])
            page = DOCS[:3][start:start + 1]
            return eric_payload(page, total=3, start=start)

        with mock.patch.object(eric, "PAGE_SIZE", 1), \
                mock.patch.object(eric, "_open", side_effect=opener):
            resp = self.client.post(f"{self.base}/import", headers=self.auth,
                                    json={"query": "paged", "limit": 3})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["eric"]["fetched"], 3)
        self.assertEqual(len(calls), 3)

    def test_unknown_task_is_404_and_unauthenticated_is_401(self):
        resp = self.client.post("/api/tasks/does-not-exist/eric/search",
                                headers=self.auth, json={"query": "x"})
        self.assertEqual(resp.status_code, 404, resp.text)
        resp = self.client.post(f"{self.base}/search", json={"query": "x"})
        self.assertEqual(resp.status_code, 401, resp.text)


# ===========================================================================
# 5. 可选真网 smoke（默认 skip；仅 RF_ERIC_LIVE=1 显式开启）
# ===========================================================================

@pytest.mark.skipif(os.environ.get("RF_ERIC_LIVE") != "1",
                    reason="set RF_ERIC_LIVE=1 to run the live ERIC smoke test")
def test_live_eric_smoke():
    result = eric.search_eric_preview('title:"second language"', 2)
    assert result["total_hits"] > 0
    assert result["records"], result
    assert re.fullmatch(r"(EJ|ED)\d{6,}", result["records"][0]["zotero_key"])
    assert result["records"][0]["title"]
