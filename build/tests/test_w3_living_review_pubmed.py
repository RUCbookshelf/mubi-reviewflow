"""W3 检索订阅复活钉子（专属新文件，撤实现必红）。

覆盖三块：
1. 插件纯函数层：url_opener 注入的成功/失败/空结果；MM10-14 七类边界输入
   全部归一为 PubMedPluginError（None/int/list/bytes、opener 返回 str、
   opener 抛 TypeError、空检索式）；重定向拒绝与读取上限出站纪律。
2. check-now 端点（真实 TestClient）：成功路径真实拉取（注入桩）→ 虚拟 RIS
   → 受保护导入 staged 待审（PMID 稳定键进 workflow_source_records）、历史
   追加、检索式 last_run_date 推进；空结果不建批次；插件失败优雅 409
   pubmed_unavailable（绝不 500）；入参缺检索式/坏日期 400。
3. 虚拟 RIS 与 ris_parser 的往返：_pubmed_updates_to_ris 生成的文件经
   parse_ris 读回时 PMID-<pmid> 键不降级（全局稳定标识）。
"""
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from contextlib import closing
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
fixture = tempfile.TemporaryDirectory(prefix="rf_w3_pubmed_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient

import custom_backend.living_review_plugin as lr_plugin
from custom_backend import auth as auth_mod
from custom_backend.main import app, _pubmed_updates_to_ris
from coscreen.parsers.ris_parser import parse_ris

# ---- 共享夹具 -------------------------------------------------------------

MEDLINE = "\n".join([
    "PMID- 36934452",
    "OWN - NLM",
    "STAT- MEDLINE",
    "TI  - Network meta-analysis of living reviews.",
    "AU  - Doe J",
    "AU  - Smith A",
    "LA  - eng",
    "PT  - Journal Article",
    "DEP - 20230315",
    "DP  - 2023 Mar 15",
    "TA  - J Clin Epidemiol",
    "LID - 10.1016/j.jclinepi.2023.03.001 [doi]",
    "AB  - Background: Living reviews require periodic updates.",
    "     We evaluated automated surveillance.",
    "FAU - Doe, John",
    "",
    "PMID- 36934453",
    "TI  - Second synthetic record.",
    "AU  - Roe M",
    "DP  - 2022",
    "AID - 10.5555/second.002 [doi]",
    "",
    "",
])


def fake_articles():
    return lr_plugin.parse_medline_records(MEDLINE)


def esearch_bytes(pmids):
    return json.dumps({"esearchresult": {"idlist": pmids}}).encode("utf-8")


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def read(self, limit=-1):
        return self._data if limit < 0 else self._data[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ---- 1. 插件纯函数层 -------------------------------------------------------

class PluginContractTests(unittest.TestCase):
    """url_opener 注入钉子 + MM10-14 七类边界输入归一。"""

    def test_injected_opener_success_returns_article_shapes(self):
        calls, posts = [], []

        def opener(url, timeout, data=None):
            calls.append(url)
            if "esearch" in url:
                self.assertIsNone(data, "esearch 走 GET")
                return esearch_bytes(["36934452", "36934453"])
            self.assertIn("efetch", url)
            # efetch 走 POST（长 PMID 列表不进 URL 查询串）
            self.assertIsNotNone(data, "efetch 必须用 POST 表单")
            posts.append(data.decode("utf-8"))
            return MEDLINE.encode("utf-8")

        articles = lr_plugin.fetch_pubmed_updates(
            "network meta-analysis", "2024-01-01", url_opener=opener)
        self.assertEqual(len(calls), 2)  # 两个串行请求，无并发轰炸
        self.assertEqual([a["zotero_key"] for a in articles],
                         ["PMID-36934452", "PMID-36934453"])
        self.assertEqual(articles[0]["authors"], "Doe J; Smith A")
        self.assertEqual(articles[0]["doi"], "10.1016/j.jclinepi.2023.03.001")
        self.assertEqual(articles[0]["year"], 2023)
        self.assertTrue(articles[0]["url"].endswith("/36934452/"))
        # 日期过滤必须 mindate + maxdate 成对（r8-r3 blocking 钉子）：
        # 真网实测只给 mindate 时 NCBI 整段丢弃日期过滤（16849 = 无过滤）。
        self.assertIn("mindate=2024%2F01%2F01", calls[0])
        self.assertIn("maxdate=", calls[0])
        self.assertNotIn("maxdate=&", calls[0])
        self.assertIn("datetype=edat", calls[0])
        # 显式工具标识是出站礼仪（沿袭 ai_suggest 先例）
        self.assertIn("tool=ReviewFlow", calls[0])
        # efetch POST 表单携带全部 PMID（urlencode 后逗号 = %2C）
        self.assertEqual(len(posts), 1)
        self.assertIn("id=36934452%2C36934453", posts[0])
        self.assertIn("rettype=medline", posts[0])

    def test_efetch_pages_large_pmid_sets(self):
        """r8-r3 blocking 钉子：大批量必须分页（每批 <= EFETCH_PAGE_SIZE）。"""
        total = lr_plugin.EFETCH_PAGE_SIZE * 2 + 37
        pmids = [str(41000000 + i) for i in range(total)]
        efetch_calls = []

        def opener(url, timeout, data=None):
            if "esearch" in url:
                payload = {"esearchresult": {"count": str(total), "idlist": pmids}}
                return json.dumps(payload).encode()
            efetch_calls.append(len(data.decode().split("id=")[1].split("&")[0].split("%2C")))
            return MEDLINE.encode("utf-8")

        lr_plugin.fetch_pubmed_updates("q", "2024-01-01", url_opener=opener)
        self.assertEqual(len(efetch_calls), 3, efetch_calls)
        for size in efetch_calls:
            self.assertLessEqual(size, lr_plugin.EFETCH_PAGE_SIZE)

    def test_esearch_pages_when_hits_exceed_page_size(self):
        """esearch 超过 ESEARCH_PAGE_SIZE 时用 retstart 翻页取全量 PMID。"""
        page = lr_plugin.ESEARCH_PAGE_SIZE
        starts = []

        def opener(url, timeout, data=None):
            if "esearch" in url:
                retstart = int(url.split("retstart=")[1].split("&")[0])
                starts.append(retstart)
                ids = [str(42000000 + retstart + i) for i in range(min(page, 10))]
                count = page + 10
                return json.dumps({"esearchresult": {
                    "count": str(count), "idlist": ids}}).encode()
            return MEDLINE.encode("utf-8")

        lr_plugin.fetch_pubmed_updates("q", "2024-01-01", url_opener=opener)
        self.assertEqual(starts, [0, page], starts)

    def test_injected_opener_empty_idlist_returns_empty_without_efetch(self):
        calls = []

        def opener(url, timeout, data=None):
            calls.append(url)
            return esearch_bytes([])

        self.assertEqual(
            lr_plugin.fetch_pubmed_updates("q", "2024-01-01", url_opener=opener),
            [])
        self.assertEqual(len(calls), 1)  # 空结果：不发第二个请求

    def _assert_plugin_error(self, fn):
        with self.assertRaises(lr_plugin.PubMedPluginError) as ctx:
            fn()
        self.assertNotIsInstance(ctx.exception, (TypeError, AttributeError, ValueError))

    def test_mm10_14_seven_boundary_inputs_all_normalized(self):
        """MM10-14：final_verify_v3 §3.3 复现的 7 个未归一化输入全部归一。"""
        cases = {
            "parse(None)": lambda: lr_plugin.parse_medline_records(None),
            "parse(int)": lambda: lr_plugin.parse_medline_records(123),
            "parse(list)": lambda: lr_plugin.parse_medline_records(["PMID- 1"]),
            "parse(bytes)": lambda: lr_plugin.parse_medline_records(b"PMID- 1\nTI  - T\n\n"),
            "fetch(opener->str)": lambda: lr_plugin.fetch_pubmed_updates(
                "q", "2024-01-01", url_opener=lambda u, t: "not-bytes"),
            "fetch(opener-TypeError)": lambda: lr_plugin.fetch_pubmed_updates(
                "q", "2024-01-01",
                url_opener=lambda u, t: (_ for _ in ()).throw(TypeError("boom"))),
            "fetch(empty-query)": lambda: lr_plugin.fetch_pubmed_updates(
                "", "2024-01-01", url_opener=lambda u, t: esearch_bytes([])),
        }
        self.assertEqual(len(cases), 7)
        for name, fn in cases.items():
            with self.subTest(case=name):
                self._assert_plugin_error(fn)

    def test_opener_network_failures_are_plugin_errors(self):
        for factory in (
            lambda: urllib.error.URLError("name resolution failed"),
            lambda: urllib.error.HTTPError("u", 503, "Service Unavailable", None, None),
            lambda: TimeoutError("timed out"),
            lambda: OSError("broken pipe"),
        ):
            with self.subTest(exc=factory().__class__.__name__):
                with self.assertRaises(lr_plugin.PubMedPluginError):
                    lr_plugin.fetch_pubmed_updates(
                        "q", "2024-01-01",
                        url_opener=lambda u, t: (_ for _ in ()).throw(factory()))

    def test_bad_esearch_payload_is_plugin_error(self):
        for raw in (b"not json", b'{"esearchresult": {}}', b'[]',
                    b'{"esearchresult": {"idlist": "not-a-list"}}'):
            with self.subTest(raw=raw):
                with self.assertRaises(lr_plugin.PubMedPluginError):
                    lr_plugin.fetch_pubmed_updates(
                        "q", "2024-01-01", url_opener=lambda u, t: raw)

    def test_redirects_are_rejected_and_read_capped(self):
        """出站纪律：_NoRedirectHandler 拒 3xx；响应超 MAX_RESPONSE_BYTES 归一失败。"""
        handler = lr_plugin._NoRedirectHandler()
        self.assertIsNone(handler.redirect_request(None, None, 302, "", {}, "https://evil"))
        self.assertGreater(lr_plugin.MAX_RESPONSE_BYTES, 0)
        # _open 的两条失败出口：重定向（30x HTTPError）与超读取上限
        with mock.patch.object(lr_plugin, "_OPENER") as fake_opener:
            fake_opener.open.side_effect = urllib.error.HTTPError(
                "u", 302, "Found", None, None)
            with self.assertRaises(lr_plugin.PubMedPluginError) as ctx:
                lr_plugin._open("https://eutils.ncbi.nlm.nih.gov/x", 1.0)
            self.assertIn("重定向", str(ctx.exception))
        with mock.patch.object(lr_plugin, "_OPENER") as fake_opener:
            fake_opener.open.return_value = _FakeResponse(
                b"x" * (lr_plugin.MAX_RESPONSE_BYTES + 1))
            with self.assertRaises(lr_plugin.PubMedPluginError) as ctx:
                lr_plugin._open("https://eutils.ncbi.nlm.nih.gov/x", 1.0)
            self.assertIn("读取上限", str(ctx.exception))


# ---- 3. 虚拟 RIS 往返 ------------------------------------------------------

class VirtualRisRoundTripTests(unittest.TestCase):
    def test_pubmed_updates_to_ris_round_trips_stable_pmid_keys(self):
        articles = fake_articles()
        with tempfile.TemporaryDirectory(prefix="rf_w3_ris_") as tmp:
            path = Path(tmp) / "pubmed-update-test.ris"
            path.write_text(_pubmed_updates_to_ris(articles), encoding="utf-8")
            parsed = parse_ris(path)
        self.assertEqual([a.zotero_key for a in parsed],
                         ["PMID-36934452", "PMID-36934453"])
        self.assertEqual(parsed[0].authors, "Doe J; Smith A")
        self.assertEqual(parsed[0].doi, "10.1016/j.jclinepi.2023.03.001")
        self.assertEqual(parsed[0].year, 2023)
        self.assertEqual(parsed[0].journal, "J Clin Epidemiol")
        self.assertIn("Living reviews", parsed[0].abstract)
        # 键未被解析器降级（降级会置 _generated_key / _demoted_source_key）
        for article in parsed:
            self.assertNotIn("_generated_key", article.raw)
            self.assertNotIn("_demoted_source_key", article.raw)


# ---- 2. check-now 端点（真实 TestClient） ----------------------------------

class CheckNowEndpointTests(unittest.TestCase):
    """成功/失败/空结果 + 校验路径，全部真实 app（不 monkeypatch 路由）。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        user = auth_mod.create_user("w3pubmed@example.test", "W3-PubMed-2026!")
        cls.auth = {"Authorization": "Bearer " +
                    auth_mod.create_access_token(user["id"], user["username"])}
        cls.user = {"user_id": user["id"], "username": user["username"]}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        # 不在此处 cleanup 数据目录（r8-r3 评审 medium）：custom_backend.main.DATA_DIR
        # 在导入时已冻结为 fixture.name，后续测试模块（如 W2）新建任务会因目录
        # 消失而 401。临时目录交给 TemporaryDirectory 的进程退出/GC 回收；同一
        # 进程内保持可用，后面模块的运行环境与收集顺序无关。

    def setUp(self):
        self.task = self.client.post(
            "/api/tasks", json={"name": self._testMethodName},
            headers=self.auth).json()["task_id"]
        self.base = f"/api/tasks/{self.task}/living-review"

    def _save_search(self, query="network meta-analysis[tiab]", last_run="2024-01-01"):
        return self.client.post(f"{self.base}/save-search", headers=self.auth, json={
            "database": "pubmed", "query_string": query,
            "last_run_date": last_run, "total_hits_last": 0}).json()

    def _owner_conn(self):
        from coscreen import review_workflow as wf
        ctx = app.state.review_workflow.context(self.task, self.user)
        return ctx, wf.connect(ctx.owner_db, read_only=True)

    def _staged_records(self):
        ctx, conn = self._owner_conn()
        with closing(conn):
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table'"
                " AND name='workflow_source_records'").fetchone()
            if not exists:  # 失败路径未初始化任何库 = 没有副作用
                return []
            return conn.execute(
                "SELECT incoming_key, filename, state FROM workflow_source_records"
            ).fetchall()

    def _mark_researched(self):
        """把任务置为「已有研究」——受保护导入的 staged 待审由此触发。"""
        from coscreen import db as db_mod
        from coscreen import review_imports as store
        from coscreen import review_workflow as wf
        ctx = app.state.review_workflow.context(self.task, self.user)
        db_mod.init_db(ctx.owner_db)
        store.migrate(ctx.owner_db)
        with closing(wf.connect(ctx.owner_db)) as conn, conn:
            conn.execute("UPDATE workflow_import_state SET researched=1 WHERE singleton=1")

    def test_success_stages_protected_import_with_pmid_keys(self):
        self._save_search()
        self._mark_researched()
        articles = fake_articles()
        with mock.patch.object(lr_plugin, "fetch_pubmed_updates",
                               return_value=articles) as fetch:
            resp = self.client.post(f"{self.base}/check-now", headers=self.auth,
                                    json={"since_date": "2024-01-01"})
        self.assertEqual(resp.status_code, 200, resp.text)
        fetch.assert_called_once_with("network meta-analysis[tiab]", "2024-01-01")
        body = resp.json()
        self.assertTrue(body["checked"])
        self.assertEqual(body["fetched"], 2)
        # 已有研究 → 受保护导入 staged 待审（激活前不进筛选队列）
        self.assertTrue(body["import"]["staged"], body["import"])
        self.assertEqual(body["import"]["pending_records"], 2)
        self.assertEqual(body["import"]["progress"]["total"], 0)
        # 虚拟文件进了受保护导入：批次真实存在且记录 PMID 稳定键
        batch_id = body["import"]["batch_id"]
        detail = self.client.get(f"/api/tasks/{self.task}/import-batches/{batch_id}",
                                 headers=self.auth).json()
        self.assertEqual(detail["files"][0]["filename"], body["virtual_file"])
        rows = self._staged_records()
        self.assertEqual({row["incoming_key"] for row in rows},
                         {"PMID-36934452", "PMID-36934453"})
        self.assertTrue(all(row["state"] == "staged" for row in rows), rows)
        # 历史追加 + 检索式推进（读取同一条产品读路径）
        state = self.client.get(f"{self.base}/search-records",
                                headers=self.auth).json()
        # R3：用户录入的总命中数不被增量条数覆盖，另记本次拉取数
        self.assertEqual(state["search_records"][0]["total_hits_last"], 0)
        self.assertEqual(state["search_records"][0]["last_fetched_count"], 2)
        self.assertTrue(state["search_records"][0]["last_run_date"])
        history = self.client.get(f"{self.base}/history",
                                  headers=self.auth).json()["history"]
        self.assertEqual(history[0]["source"], "pubmed")
        # 口径（r8 评审 medium）：staged 批次未激活 → 新增 0、待审 2；
        # 不再把「拉回 2 条」冒充成「新增 2 条」。
        self.assertEqual(history[0]["n_new"], 0)
        self.assertEqual(history[0]["pending_new"], 2)
        self.assertEqual(history[0]["fetched"], 2)
        self.assertIn("受保护导入待审", history[0]["change_report"])
        self.assertIn("激活前不改变筛选队列", history[0]["change_report"])
        self.assertEqual(history[0]["label"], body["virtual_file"])
        self.assertFalse(history[0]["conclusion_changed"])

    def test_success_on_fresh_task_matches_manual_import_activation(self):
        """无研究任务：与手工上传同一文件同一行为（直接激活，非 staged 特例）。"""
        self._save_search()
        with mock.patch.object(lr_plugin, "fetch_pubmed_updates",
                               return_value=fake_articles()):
            resp = self.client.post(f"{self.base}/check-now", headers=self.auth,
                                    json={"since_date": "2024-01-01"})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertFalse(body["import"]["staged"])
        self.assertEqual(body["import"]["n_imported"], 2)
        self.assertEqual(body["import"]["progress"]["total"], 2)

    def test_plugin_failure_degrades_to_structured_409_never_500(self):
        self._save_search()
        for exc in (lr_plugin.PubMedPluginError("PubMed 不可达：simulated"),
                    lr_plugin.PubMedPluginError("PubMed 返回 HTTP 503")):
            with mock.patch.object(lr_plugin, "fetch_pubmed_updates", side_effect=exc):
                resp = self.client.post(f"{self.base}/check-now", headers=self.auth,
                                        json={"since_date": "2024-01-01"})
            with self.subTest(exc=str(exc)):
                self.assertEqual(resp.status_code, 409, resp.text)
                detail = resp.json()["detail"]
                self.assertEqual(detail["code"], "pubmed_unavailable")
                self.assertIn("受保护导入", detail["message"])
                self.assertIn("import_url", detail)
        # 降级路径没有留下任何批次/历史/检索式推进
        self.assertEqual(self._staged_records(), [])
        history = self.client.get(f"{self.base}/history",
                                  headers=self.auth).json()["history"]
        self.assertEqual(history, [])
        records = self.client.get(f"{self.base}/search-records",
                                  headers=self.auth).json()["search_records"]
        self.assertEqual(records[0]["last_run_date"], "2024-01-01")

    def test_empty_result_returns_zero_and_creates_no_batch(self):
        self._save_search()
        with mock.patch.object(lr_plugin, "fetch_pubmed_updates", return_value=[]):
            resp = self.client.post(f"{self.base}/check-now", headers=self.auth,
                                    json={"since_date": "2024-01-01"})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["fetched"], 0)
        self.assertIsNone(body["import"])
        self.assertIn("无新增", body["history_entry"]["change_report"])
        batches = self.client.get(f"/api/tasks/{self.task}/import-batches",
                                  headers=self.auth).json()
        self.assertEqual(batches["total"], 0, batches)
        self.assertEqual(self._staged_records(), [])

    def test_rerun_same_pmids_does_not_duplicate_source_records(self):
        """r8 评审 medium：重跑幂等——固定虚拟文件名，同 PMID 集合不新增源记录。"""
        self._save_search()
        self._mark_researched()
        with mock.patch.object(lr_plugin, "fetch_pubmed_updates",
                               return_value=fake_articles()):
            first = self.client.post(f"{self.base}/check-now", headers=self.auth,
                                     json={"since_date": "2024-01-01"})
            self.assertEqual(first.status_code, 200, first.text)
            rows_after_first = len(self._staged_records())
            for _ in range(3):
                again = self.client.post(f"{self.base}/check-now", headers=self.auth,
                                         json={"since_date": "2024-01-01"})
                self.assertEqual(again.status_code, 200, again.text)
            rows_after_reruns = len(self._staged_records())
        # 首次 2 行；重复 3 次后仍是 2 行（批次收据仍会新增，与手工重上传一致）
        self.assertEqual(rows_after_first, 2)
        self.assertEqual(rows_after_reruns, 2,
                         "same PMID set must not create new source records")

    def test_fresh_task_report_matches_actual_activation(self):
        """r8 评审 medium：fresh 任务实际会直接激活，报告必须如实这么说。"""
        self._save_search()
        with mock.patch.object(lr_plugin, "fetch_pubmed_updates",
                               return_value=fake_articles()):
            resp = self.client.post(f"{self.base}/check-now", headers=self.auth,
                                    json={"since_date": "2024-01-01"})
        body = resp.json()
        self.assertFalse(body["staged"])
        self.assertEqual(body["activated_new"], 2)
        self.assertEqual(body["pending_new"], 0)
        entry = body["history_entry"]
        self.assertEqual(entry["n_new"], 2)
        self.assertIn("直接导入", entry["change_report"])
        self.assertNotIn("受保护导入待审", entry["change_report"])

    def test_plugin_low_level_edges_are_normalized(self):
        """r8 评审 low：_open 的协议异常、since_date 的 None/空值都归一。"""
        import http.client

        class _BadResponse:
            def read(self, n=-1):
                raise http.client.IncompleteRead(b"x", 10)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class _BadOpener:
            def open(self, request, timeout=None):
                return _BadResponse()

        with mock.patch.object(lr_plugin, "_OPENER", _BadOpener()):
            with self.assertRaises(lr_plugin.PubMedPluginError):
                lr_plugin._open("https://eutils.ncbi.nlm.nih.gov/x", 1.0)
        for bad in (None, "", 123):
            with self.subTest(since=bad):
                with self.assertRaises(lr_plugin.PubMedPluginError):
                    lr_plugin.fetch_pubmed_updates(
                        "q", bad, url_opener=lambda u, t: b"{}")

    def test_truncated_result_is_refused_without_advancing_cursor(self):
        """r8-r3 评审 medium：命中达 RETMAX 上限=结果被截断 → 409 且不推进游标。"""
        self._save_search(last_run="2024-01-01")
        self._mark_researched()
        big = [dict(a) for a in fake_articles()]
        # 造出 len == RETMAX 的“截断”结果（内容重复无关，只关心条数与副作用）
        truncated = (big * (lr_plugin.RETMAX // 2 + 1))[:lr_plugin.RETMAX]
        with mock.patch.object(lr_plugin, "fetch_pubmed_updates", return_value=truncated):
            resp = self.client.post(f"{self.base}/check-now", headers=self.auth,
                                    json={"since_date": "2024-01-01"})
        self.assertEqual(resp.status_code, 409, resp.text)
        detail = resp.json()["detail"]
        self.assertEqual(detail["code"], "pubmed_unavailable")
        self.assertIn("截断", detail["message"])
        self.assertEqual(detail["hint"], "reduce_query_or_window")
        # 零副作用：无批次、无历史、游标不动
        self.assertEqual(self._staged_records(), [])
        self.assertEqual(self.client.get(f"{self.base}/history",
                                         headers=self.auth).json()["history"], [])
        records = self.client.get(f"{self.base}/search-records",
                                  headers=self.auth).json()["search_records"]
        self.assertEqual(records[0]["last_run_date"], "2024-01-01")

    def test_volume_failure_gives_actionable_hint(self):
        """r8-r3 评审 high：体量类失败给可执行指引，网络类才建议重试。"""
        self._save_search()
        for message, expected_hint in (
            ("PubMed 响应超过 32 MiB 读取上限。", "reduce_query_or_window"),
            ("PubMed 不可达：timed out", "reduce_query_or_window"),
            ("PubMed 返回 HTTP 503", "retry_or_manual_import"),
        ):
            with self.subTest(message=message):
                with mock.patch.object(lr_plugin, "fetch_pubmed_updates",
                                       side_effect=lr_plugin.PubMedPluginError(message)):
                    resp = self.client.post(f"{self.base}/check-now", headers=self.auth,
                                            json={"since_date": "2024-01-01"})
                self.assertEqual(resp.status_code, 409, resp.text)
                detail = resp.json()["detail"]
                self.assertEqual(detail["hint"], expected_hint)
                self.assertIn("limits", detail)
                self.assertEqual(detail["limits"]["max_records_per_check"],
                                 lr_plugin.RETMAX)
                if expected_hint == "reduce_query_or_window":
                    self.assertIn("缩小", detail["message"])

    def test_validation_and_auth_paths(self):
        # 未登录 401
        self.assertEqual(
            self.client.post(f"{self.base}/check-now", json={}).status_code, 401)
        # 无已保存检索式且无 query_string → 400
        resp = self.client.post(f"{self.base}/check-now", headers=self.auth, json={})
        self.assertEqual(resp.status_code, 400, resp.text)
        # 坏 since_date → 400
        self._save_search()
        resp = self.client.post(f"{self.base}/check-now", headers=self.auth,
                                json={"since_date": "2024/01/01"})
        self.assertEqual(resp.status_code, 400, resp.text)
        # 无任何 last_run_date 依据且请求未带 since_date → 400
        self._save_search(query="fresh query", last_run="")
        resp = self.client.post(f"{self.base}/check-now", headers=self.auth,
                                json={"query_string": "fresh query"})
        self.assertEqual(resp.status_code, 400, resp.text)
        # 任务不存在 → 404
        resp = self.client.post("/api/tasks/nope/living-review/check-now",
                                headers=self.auth, json={})
        self.assertEqual(resp.status_code, 404, resp.text)


if __name__ == "__main__":
    unittest.main()
