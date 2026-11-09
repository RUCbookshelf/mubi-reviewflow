# -*- coding: utf-8 -*-
"""2.1.5 Wave G2 · W4 ERIC 增量红钉（专属新文件，撤实现必红）。

方案书 W4 红钉清单（plan_2.1.5_execution_DAG.md §二 W4 + design §三）：
1. 出站域断言：mock 通道仅 ``api.ies.ed.gov`` 与 ``eutils.ncbi.nlm.nih.gov``
   两域可通；ERIC 增量只打前者，PubMed 链路仍只打后者。
2. 差集正确性：已知 ERIC accession 键（EJ…/ED…）不再作为增量出现；同页重复
   键也只出现一次。
3. 补录旧文献可被差集捕获：ERIC 请求不带任何日期参数（mindate/maxdate/
   datetype 一律不出现），1988 年的补录文献仍进入增量。
4. 上限 500 截断 + 诚实披露：numFound 大于上限时只比对前 500 条，
   ``scanned/limit/truncated`` 三个字段如实给出，并带结构化披露键。
5. 待审口径：增量 → 虚拟 RIS → ``review_imports.upload()`` 受保护导入；
   已有研究的任务 staged 待审、不直入 articles；首查基线提示。
6. PubMed 路径零回归（本文件只做分派冒烟；全量回归由
   test_w3_living_review_pubmed.py 自身保证，另在收官命令中执行）。
7. 失败 409 形状与 PubMed 一致（code/message/hint/limits/import_url/
   review_url），绝不 500；失败路径零副作用（无批次/无历史/游标不动）。
8. 权限沿既有 check-now 口径：非参与者 403；preview 只读、零写入。

另含：preview 端点（增量预览数=差集大小）、source 分派与冲突、
ERIC 键经 RIS 往返不被解析器降级。
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import urllib.parse
from contextlib import closing
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
fixture = tempfile.TemporaryDirectory(prefix="rf_215_w4_eric_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient  # noqa: E402

import custom_backend.eric_search_api as eric_api  # noqa: E402
import custom_backend.living_review_plugin as lr_plugin  # noqa: E402
from coscreen import db as db_mod  # noqa: E402
from coscreen import review_imports as review_store  # noqa: E402
from coscreen import review_workflow as wf  # noqa: E402
from coscreen.parsers.ris_parser import parse_ris  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend.main import DATA_DIR, app  # noqa: E402

# 未实现前下面这行即红：W4 的 ERIC 增量模块与纯函数。
from coscreen.eric_incremental import diff_new_records, normalize_key  # noqa: E402
from custom_backend import eric_incremental_api as eric_inc  # noqa: E402

# 账号在**首次用到时**才建（不是模块导入时）：auth 的数据目录取自
# 环境变量 ``COBOOKSHELF_DATA_DIR`` 的当前值，而混跑多套件时后面收集的模块
# 会覆盖该变量；导入期签发的令牌会因此与请求期密钥不一致（401）。延迟到
# setUpClass（收集全部完成后）创建，确保令牌与请求同一数据目录。
_CREDENTIALS: dict = {}


def _credentials():
    if not _CREDENTIALS:
        owner = auth_mod.create_user("w4eric@example.test", "W4-ERIC-2026!")
        outsider = auth_mod.create_user("w4outsider@example.test", "W4-ERIC-2026!")
        _CREDENTIALS.update({
            "auth": {"Authorization": "Bearer " + auth_mod.create_access_token(
                owner["id"], owner["username"])},
            "user": {"user_id": owner["id"], "username": owner["username"]},
            "outsider_auth": {
                "Authorization": "Bearer " + auth_mod.create_access_token(
                    outsider["id"], outsider["username"])},
        })
    return _CREDENTIALS


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

def eric_doc(eid, title="A study", year=2020, source="Journal of Education",
             url=None, ptype=("Journal Articles",)):
    return {
        "id": eid,
        "title": title,
        "author": ["Doe, Jane", "Roe, Richard"],
        "description": "Synthetic abstract for " + eid,
        "publicationdateyear": str(year),
        "source": source,
        "url": url or ("https://eric.ed.gov/?id=" + eid),
        "publicationtype": list(ptype),
    }


def eric_payload(docs, total=None):
    return json.dumps({"response": {
        "numFound": len(docs) if total is None else int(total),
        "start": 0,
        "docs": docs,
    }}).encode("utf-8")


def _request_start(url: str) -> int:
    """从 ERIC 请求 URL 里取 start 参数（断点续查钉的页起点断言用）。"""
    parsed = urllib.parse.urlsplit(url)
    return int(urllib.parse.parse_qs(parsed.query).get("start", ["0"])[0])


class FakeEricChannel:
    """按 start/rows 分页的 ERIC 假通道，记录每次请求的 URL。"""

    def __init__(self, docs, total=None):
        self.docs = list(docs)
        self.total = len(self.docs) if total is None else int(total)
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append(url)
        parsed = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qs(parsed.query)
        start = int(query.get("start", ["0"])[0])
        rows = int(query.get("rows", ["20"])[0])
        page = self.docs[start:start + rows]
        return eric_payload(page, total=self.total)


def snapshot_from(payload_docs, total=None):
    """把 docs 走生产的 doc_to_article 得到 fetch_eric_incremental 同形返回。"""
    records = [a for a in (eric_api.doc_to_article(doc) for doc in payload_docs) if a]
    return {
        "source": "eric",
        "endpoint": eric_api.ERIC_API_URL,
        "query": "title:(network)",
        "total_hits": len(payload_docs) if total is None else int(total),
        "scanned": len(payload_docs),
        "limit": eric_inc.ERIC_INCREMENT_LIMIT,
        "unstable": 0,
        "truncated": (len(payload_docs) if total is None else int(total))
        > len(payload_docs),
        "records": records,
    }


def medline_for_pmid(pmid):
    return "\n".join([
        "PMID- " + str(pmid),
        "TI  - Synthetic PubMed record.",
        "AU  - Doe J",
        "DP  - 2024",
        "TA  - J Test",
        "",
        "",
    ])


class AllowedDomainsChannel:
    """只放行白名单两域的通道；其他 host 立即断言失败。"""

    ALLOWED = {"api.ies.ed.gov", "eutils.ncbi.nlm.nih.gov"}

    def __init__(self, eric_docs, pmid="36934452"):
        self.eric_docs = list(eric_docs)
        self.pmid = pmid
        self.hosts = []

    def __call__(self, url, timeout, data=None):
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        if host not in self.ALLOWED:
            raise AssertionError(f"出站到非白名单域：{host}")
        self.hosts.append(host)
        if host == "api.ies.ed.gov":
            parsed = urllib.parse.urlsplit(url)
            query = urllib.parse.parse_qs(parsed.query)
            start = int(query.get("start", ["0"])[0])
            rows = int(query.get("rows", ["20"])[0])
            return eric_payload(self.eric_docs[start:start + rows],
                                total=len(self.eric_docs))
        if "esearch" in url:
            return json.dumps({"esearchresult": {
                "count": "1", "idlist": [self.pmid]}}).encode()
        return medline_for_pmid(self.pmid).encode()


# ---------------------------------------------------------------------------
# 1. 纯函数：键归一 + 差集
# ---------------------------------------------------------------------------

class DiffPins(unittest.TestCase):
    def test_normalize_key_is_whitespace_and_case_insensitive(self):
        self.assertEqual(normalize_key("  ej1482166\n"), "EJ1482166")
        self.assertEqual(normalize_key(None), "")

    def test_known_keys_are_excluded_and_no_duplicates(self):
        records = [
            {"zotero_key": "EJ0000001", "title": "known"},
            {"zotero_key": "EJ0000002", "title": "new"},
            {"zotero_key": "ej0000002", "title": "same new key again"},
            {"zotero_key": "EJ0000003", "title": "another new"},
        ]
        new = diff_new_records(records, {"ej0000001"})
        self.assertEqual([row["zotero_key"] for row in new],
                         ["EJ0000002", "EJ0000003"])
        # 差集结果内部也去重：同键不会重复出现
        self.assertEqual(len({normalize_key(row["zotero_key"]) for row in new}), 2)

    def test_records_without_stable_key_are_skipped(self):
        self.assertEqual(diff_new_records(
            [{"zotero_key": ""}, {}, {"title": "no key"}], set()), [])


class KnownKeysPins(unittest.TestCase):
    """已知键读取：缺失库=空基线（正常），损坏库=降级且必须披露。"""

    def test_missing_db_is_empty_baseline_not_degraded(self):
        with tempfile.TemporaryDirectory(prefix="rf_w4_known_") as tmp:
            known = eric_inc.collect_known_eric_keys(Path(tmp) / "none.db")
        self.assertEqual(known["keys"], set())
        self.assertTrue(known["baseline"])
        self.assertFalse(known["degraded"])

    def test_corrupt_db_is_degraded_and_disclosed(self):
        with tempfile.TemporaryDirectory(prefix="rf_w4_known_") as tmp:
            path = Path(tmp) / "broken.db"
            path.write_bytes(b"this is not a sqlite database")
            known = eric_inc.collect_known_eric_keys(path)
        self.assertTrue(known["degraded"])
        self.assertEqual(known["keys"], set())
        warnings = eric_inc.disclosure_warnings(
            {"truncated": False, "unstable": 0}, known)
        self.assertEqual([row["message_key"] for row in warnings],
                         ["living_review.eric.known_keys_unavailable"])


# ---------------------------------------------------------------------------
# 2. 出站域 + 无日期参数依赖（函数级，注入通道）
# ---------------------------------------------------------------------------

class FetchPins(unittest.TestCase):
    def test_outbound_only_two_domains(self):
        channel = AllowedDomainsChannel([eric_doc("EJ1482166")])
        eric_inc.fetch_eric_incremental("title:(x)", url_opener=channel)
        self.assertEqual(set(channel.hosts), {"api.ies.ed.gov"})

        lr_plugin.fetch_pubmed_updates("q", "2024-01-01", url_opener=channel)
        self.assertEqual(set(channel.hosts),
                         {"api.ies.ed.gov", "eutils.ncbi.nlm.nih.gov"})

    def test_no_date_parameters_are_sent(self):
        channel = FakeEricChannel([eric_doc("EJ1482166", year=1988)])
        payload = eric_inc.fetch_eric_incremental("title:(old)", url_opener=channel)
        self.assertEqual(len(channel.calls), 1)
        for call in channel.calls:
            query = urllib.parse.urlsplit(call).query.lower()
            for forbidden in ("mindate", "maxdate", "datetype", "since"):
                self.assertNotIn(forbidden, query, call)
        # 补录的 1988 年文献照样进增量（ID 差集天然覆盖）
        self.assertEqual([row["zotero_key"] for row in payload["records"]],
                         ["EJ1482166"])
        self.assertEqual(payload["records"][0]["year"], 1988)

    def test_cap_at_500_with_honest_disclosure(self):
        docs = [eric_doc(f"EJ{2000000 + i}") for i in range(800)]
        channel = FakeEricChannel(docs, total=800)
        payload = eric_inc.fetch_eric_incremental("title:(big)", url_opener=channel)
        self.assertEqual(payload["limit"], 500)
        self.assertEqual(payload["scanned"], 500)
        self.assertEqual(payload["total_hits"], 800)
        self.assertTrue(payload["truncated"])
        self.assertEqual(len(payload["records"]), 500)
        # 分页而不是单页请求（PAGE_SIZE=200）
        self.assertGreater(len(channel.calls), 1)

    def test_not_truncated_when_hits_fit(self):
        docs = [eric_doc(f"EJ{3000000 + i}") for i in range(3)]
        channel = FakeEricChannel(docs, total=3)
        payload = eric_inc.fetch_eric_incremental("title:(small)", url_opener=channel)
        self.assertFalse(payload["truncated"])
        self.assertEqual(payload["scanned"], 3)
        self.assertEqual(len(payload["records"]), 3)

    def test_eric_search_error_is_raised_not_leaked(self):
        with self.assertRaises(eric_api.EricSearchError):
            eric_inc.fetch_eric_incremental(
                "title:(x)", url_opener=lambda url, timeout: b"not json")
        with self.assertRaises(eric_api.EricSearchError):
            eric_inc.fetch_eric_incremental(
                "title:(x)",
                url_opener=lambda url, timeout: json.dumps(
                    {"error": {"msg": "undefined field doi"}}).encode())


# ---------------------------------------------------------------------------
# 3. 端点（真实 TestClient）
# ---------------------------------------------------------------------------

class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        creds = _credentials()
        cls.auth = creds["auth"]
        cls.user = creds["user"]
        cls.outsider_auth = creds["outsider_auth"]
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        response = self.client.post(
            "/api/tasks", json={"name": f"w4-eric-{self._testMethodName}"},
            headers=self.auth)
        self.assertIn(response.status_code, (200, 201), response.text)
        self.task = response.json()["task_id"]
        self.base = f"/api/tasks/{self.task}/living-review"
        self.strategy_base = f"/api/tasks/{self.task}/search-strategy"

    # ---- 夹具 ----
    def _eric_strategy(self, query='title:("network")', subscribe=True):
        response = self.client.post(
            f"{self.strategy_base}/strategies", headers=self.auth,
            json={"title": "ERIC 主检索", "database": "eric",
                  "query_text": query, "subscribe": subscribe})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["strategy"]

    def _pubmed_strategy(self, query="network meta-analysis[tiab]"):
        response = self.client.post(
            f"{self.base}/save-search", headers=self.auth,
            json={"database": "pubmed", "query_string": query,
                  "last_run_date": "2024-01-01", "total_hits_last": 0})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["search_record"]

    def _owner_ctx(self):
        return app.state.review_workflow.context(self.task, self.user)

    def _mark_researched(self):
        ctx = self._owner_ctx()
        db_mod.init_db(ctx.owner_db)
        review_store.migrate(ctx.owner_db)
        with closing(wf.connect(ctx.owner_db)) as conn, conn:
            conn.execute(
                "UPDATE workflow_import_state SET researched=1 WHERE singleton=1")

    def _source_records(self):
        ctx = self._owner_ctx()
        with closing(wf.connect(ctx.owner_db, read_only=True)) as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table'"
                " AND name='workflow_source_records'").fetchone()
            if not exists:
                return []
            return [dict(row) for row in conn.execute(
                "SELECT incoming_key, filename, state FROM workflow_source_records")]

    def _active_keys(self):
        ctx = self._owner_ctx()
        with closing(wf.connect(ctx.owner_db, read_only=True)) as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table'"
                " AND name='articles'").fetchone()
            if not exists:
                return set()
            return {row[0] for row in conn.execute("SELECT zotero_key FROM articles")}

    def _history(self):
        return self.client.get(f"{self.base}/history",
                               headers=self.auth).json()["history"]

    def _records(self):
        return self.client.get(f"{self.base}/search-records",
                               headers=self.auth).json()["search_records"]

    def _batch_total(self):
        return self.client.get(f"/api/tasks/{self.task}/import-batches",
                               headers=self.auth).json()["total"]

    def _task_dir(self) -> Path:
        from coscreen import tasks as tasks_mod

        return tasks_mod.find_task(self.task, DATA_DIR).dir


class CheckNowEricEndpointPins(_Base):
    def test_eri_increment_stages_protected_import(self):
        strategy = self._eric_strategy()
        self._mark_researched()
        docs = [eric_doc("EJ1482166"), eric_doc("EJ1482167"), eric_doc("EJ1482168")]
        snapshot = snapshot_from(docs)
        with mock.patch.object(eric_inc, "fetch_eric_incremental",
                               return_value=snapshot) as fetch:
            response = self.client.post(
                f"{self.base}/check-now", headers=self.auth,
                json={"strategy_id": strategy["id"]})
        self.assertEqual(response.status_code, 200, response.text)
        fetch.assert_called_once()
        body = response.json()
        self.assertTrue(body["checked"])
        self.assertEqual(body["source"], "eric")
        self.assertEqual(body["strategy_id"], strategy["id"])
        self.assertEqual(body["fetched"], 3)
        self.assertEqual(body["total_hits"], 3)
        self.assertEqual(body["scanned"], 3)
        self.assertFalse(body["truncated"])
        # 任务里还没有任何 ERIC accession → 首查（建立基线键集）
        self.assertTrue(body["baseline"])
        # 待审口径：已有研究 → staged；绝不直入 articles
        self.assertTrue(body["import"]["staged"], body["import"])
        self.assertEqual(body["import"]["pending_records"], 3)
        self.assertEqual(body["activated_new"], 0)
        self.assertEqual(body["pending_new"], 3)
        rows = self._source_records()
        self.assertEqual({row["incoming_key"] for row in rows},
                         {"EJ1482166", "EJ1482167", "EJ1482168"})
        self.assertTrue(all(row["state"] == "staged" for row in rows))
        self.assertNotIn("EJ1482166", self._active_keys())
        # 虚拟 RIS 文件名固定（来源身份稳定）
        self.assertEqual(body["virtual_file"], "eric-update.ris")
        # 历史条目按源 + 结构化披露键（不新增自由文案）
        entry = body["history_entry"]
        self.assertEqual(entry["source"], "eric")
        self.assertEqual(entry["fetched"], 3)
        self.assertEqual(entry["pending_new"], 3)
        self.assertEqual(entry["change_report"], "")
        self.assertEqual(entry["change_report_key"],
                         "living_review.eric.baseline")
        self.assertEqual(entry["change_report_params"]["increment"], 3)
        self.assertTrue(entry["change_report_params"]["staged"])
        self.assertEqual(self._history()[0]["source"], "eric")
        # 游标：命中数=增量数；日期推进到今天
        records = self._records()
        eric_row = next(row for row in records
                        if row["strategy_id"] == strategy["id"])
        self.assertEqual(eric_row["last_fetched_count"], 3)
        self.assertTrue(eric_row["last_run_date"])

    def test_first_run_is_baseline_and_all_hits_are_increment(self):
        strategy = self._eric_strategy()
        self._mark_researched()
        snapshot = snapshot_from([eric_doc("EJ1000001"), eric_doc("EJ1000002")])
        with mock.patch.object(eric_inc, "fetch_eric_incremental",
                               return_value=snapshot):
            response = self.client.post(
                f"{self.base}/check-now", headers=self.auth,
                json={"strategy_id": strategy["id"]})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["baseline"])
        self.assertEqual(body["fetched"], 2)
        self.assertEqual(body["message_key"], "living_review.eric.baseline")
        self.assertEqual(body["history_entry"]["baseline"], True)
        self.assertEqual(body["history_entry"]["change_report_key"],
                         "living_review.eric.baseline")

    def test_known_keys_are_never_reimported(self):
        """差集钉：第二轮同一快照 → 0 增量、0 新批次、0 新源记录。"""
        strategy = self._eric_strategy()
        self._mark_researched()
        snapshot = snapshot_from([eric_doc("EJ2000001"), eric_doc("EJ2000002")])
        with mock.patch.object(eric_inc, "fetch_eric_incremental",
                               return_value=snapshot):
            first = self.client.post(f"{self.base}/check-now", headers=self.auth,
                                     json={"strategy_id": strategy["id"]})
            self.assertEqual(first.status_code, 200, first.text)
            rows_after_first = len(self._source_records())
            batches_after_first = self._batch_total()
            second = self.client.post(f"{self.base}/check-now", headers=self.auth,
                                      json={"strategy_id": strategy["id"]})
        self.assertEqual(second.status_code, 200, second.text)
        body = second.json()
        self.assertEqual(body["fetched"], 0)
        self.assertEqual(body["message_key"], "living_review.eric.increment_none")
        self.assertIsNone(body["import"])
        self.assertEqual(len(self._source_records()), rows_after_first)
        self.assertEqual(self._batch_total(), batches_after_first)
        # 增量=0 时命中数也应为 0（命中数=增量数口径）
        eric_row = next(row for row in self._records()
                        if row["strategy_id"] == strategy["id"])
        self.assertEqual(eric_row["last_fetched_count"], 0)

    def test_later_run_uses_staged_message_key(self):
        """基线建立后的第二次检查：只报增量，并提示进受保护导入待审。"""
        strategy = self._eric_strategy()
        self._mark_researched()
        with mock.patch.object(eric_inc, "fetch_eric_incremental",
                               return_value=snapshot_from([eric_doc("EJ8000001")])):
            first = self.client.post(f"{self.base}/check-now", headers=self.auth,
                                     json={"strategy_id": strategy["id"]})
        self.assertEqual(first.status_code, 200, first.text)
        with mock.patch.object(eric_inc, "fetch_eric_incremental",
                               return_value=snapshot_from(
                                   [eric_doc("EJ8000001"), eric_doc("EJ8000002")])):
            second = self.client.post(f"{self.base}/check-now", headers=self.auth,
                                      json={"strategy_id": strategy["id"]})
        self.assertEqual(second.status_code, 200, second.text)
        body = second.json()
        self.assertFalse(body["baseline"])
        self.assertEqual(body["fetched"], 1)
        self.assertEqual(body["pending_new"], 1)
        self.assertEqual(body["message_key"],
                         "living_review.eric.increment_staged")
        self.assertEqual(body["message_params"]["increment"], 1)
        self.assertEqual(body["history_entry"]["change_report_key"],
                         "living_review.eric.increment_staged")
        # 已知键（EJ8000001）没有被重新导入
        keys = [row["incoming_key"] for row in self._source_records()]
        self.assertEqual(sorted(keys), ["EJ8000001", "EJ8000002"])

    def test_backfilled_old_article_is_captured(self):
        """无日期参数钉：旧年份（1988）补录文献进入增量。"""
        strategy = self._eric_strategy()
        self._mark_researched()
        docs = [eric_doc("EJ3000001", year=1988), eric_doc("EJ3000002", year=2024)]
        with mock.patch.object(eric_inc, "fetch_eric_incremental",
                               return_value=snapshot_from(docs)):
            response = self.client.post(f"{self.base}/check-now", headers=self.auth,
                                        json={"strategy_id": strategy["id"]})
        self.assertEqual(response.status_code, 200, response.text)
        keys = {row["incoming_key"] for row in self._source_records()}
        self.assertIn("EJ3000001", keys)

    def test_truncation_is_disclosed_with_limit(self):
        strategy = self._eric_strategy()
        self._mark_researched()
        docs = [eric_doc(f"EJ{4000000 + i}") for i in range(500)]
        snapshot = snapshot_from(docs, total=800)
        with mock.patch.object(eric_inc, "fetch_eric_incremental",
                               return_value=snapshot):
            response = self.client.post(f"{self.base}/check-now", headers=self.auth,
                                        json={"strategy_id": strategy["id"]})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["truncated"])
        self.assertEqual(body["limit"], 500)
        self.assertEqual(body["scanned"], 500)
        self.assertEqual(body["total_hits"], 800)
        # 旧 → 新（#61 断点续查）：warning params 追加 ``remaining``（剩余未
        # 比对条数），旧三键原样保留；响应新增 scan_start/remaining_hits/
        # scan_next（测试替身快照无 scan_next → 按剩余量推导）。
        self.assertIn({"message_key": "living_review.eric.truncated",
                       "params": {"scanned": 500, "limit": 500,
                                  "total_hits": 800, "remaining": 300}},
                      body["warnings"])
        self.assertTrue(body["history_entry"]["truncated"])

    def test_truncation_breakpoint_resumes_and_exhausts(self):
        """终审 #61（用户裁定：断点续查）：505 条 → 首查 500+剩余 5 → 续查只查 5。

        修复前：无持久化断点，每次 check-now 恒从 start=0 拉前 500 条
        （探针 u7-deepseek-c4/probe_eric_trunc.py：三次检查请求全为
        start=0/200/400，rank≥500 永不请求）；修复后断点落在
        living_review.json 的 ``eric_scan``，续查从 500 起且差集正确。
        """
        strategy = self._eric_strategy(query='title:("wide")')
        self._mark_researched()
        docs = [eric_doc(f"EJ{6000000 + i}") for i in range(505)]
        channel = FakeEricChannel(docs)
        with mock.patch.object(eric_api, "_open", channel):
            first = self.client.post(f"{self.base}/check-now", headers=self.auth,
                                     json={"strategy_id": strategy["id"]})
        self.assertEqual(first.status_code, 200, first.text)
        body = first.json()
        self.assertTrue(body["baseline"])
        self.assertEqual(body["scanned"], 500)
        self.assertTrue(body["truncated"])
        self.assertEqual(body["remaining_hits"], 5)
        self.assertEqual(body["scan_start"], 0)
        self.assertEqual(body["scan_next"], 500)
        self.assertEqual(body["fetched"], 500)
        self.assertEqual(body["import"]["pending_records"], 500)
        # 断点已持久化在 living_review.json（既有存储落点，幂等可重写）
        state = json.loads((self._task_dir() / "living_review.json")
                           .read_text(encoding="utf-8"))
        cursor = state["eric_scan"][f"s{strategy['id']}"]
        self.assertEqual(cursor["offset"], 500)
        self.assertEqual(cursor["total_hits"], 505)
        # 首查只请求 0/200/400 三页
        self.assertEqual([_request_start(url) for url in channel.calls],
                         [0, 200, 400])

        # 续查：从断点 500 继续，只请求 5 条；差集只含尾部 5 条（前 500
        # 已在待审源记录里 = 已知键，不重复）
        channel2 = FakeEricChannel(docs)
        with mock.patch.object(eric_api, "_open", channel2):
            second = self.client.post(f"{self.base}/check-now", headers=self.auth,
                                      json={"strategy_id": strategy["id"]})
        self.assertEqual(second.status_code, 200, second.text)
        tail = second.json()
        self.assertEqual(tail["scan_start"], 500)
        self.assertEqual(tail["scanned"], 5)
        self.assertFalse(tail["truncated"])
        self.assertEqual(tail["remaining_hits"], 0)
        self.assertEqual(tail["scan_next"], 0)
        self.assertEqual(tail["fetched"], 5)
        self.assertEqual([_request_start(url) for url in channel2.calls], [500])
        keys = [row["incoming_key"] for row in self._source_records()]
        self.assertEqual(len(keys), 505)
        self.assertEqual(len(set(keys)), 505, "已知键不得重复导入")
        # 整段比完 → 游标清零（下次回头部捕获新命中）
        state = json.loads((self._task_dir() / "living_review.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(state.get("eric_scan"), {})
        # 预览与 check-now 同一起点：此刻游标已清零 → 预览从 0 起且只读不推进
        channel3 = FakeEricChannel(docs)
        with mock.patch.object(eric_api, "_open", channel3):
            preview = self.client.get(
                f"{self.base}/preview", headers=self.auth,
                params={"source": "eric", "strategy_id": strategy["id"]})
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()["scan_start"], 0)
        self.assertEqual([_request_start(url) for url in channel3.calls],
                         [0, 200, 400])

    def test_shrunk_hits_clear_stale_cursor_without_error(self):
        """上游命中数缩到断点之下：不 409、增量 0、陈旧游标自愈清零。"""
        strategy = self._eric_strategy(query='title:("shrink")')
        self._mark_researched()
        docs = [eric_doc(f"EJ{7000000 + i}") for i in range(520)]
        channel = FakeEricChannel(docs)
        with mock.patch.object(eric_api, "_open", channel):
            first = self.client.post(f"{self.base}/check-now", headers=self.auth,
                                     json={"strategy_id": strategy["id"]})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["remaining_hits"], 20)
        # 上游重组后只剩 300 条（< 断点 500）：续查空页 → 增量 0、游标清零
        channel2 = FakeEricChannel(docs[:300], total=300)
        with mock.patch.object(eric_api, "_open", channel2):
            second = self.client.post(f"{self.base}/check-now", headers=self.auth,
                                      json={"strategy_id": strategy["id"]})
        self.assertEqual(second.status_code, 200, second.text)
        body = second.json()
        self.assertEqual(body["scanned"], 0)
        self.assertFalse(body["truncated"])
        self.assertEqual(body["message_key"], "living_review.eric.increment_none")
        state = json.loads((self._task_dir() / "living_review.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(state.get("eric_scan"), {})

    def test_preview_query_only_uses_same_cursor_as_check_now(self):
        """终审第 7 波复审（阻断）：query-only 预览与 check-now 同一起点。

        修复前（R1 实现）：check-now 把 query 精确匹配到订阅条目用 ``s<id>``
        游标键，preview(query only) 却用 ``q<指纹>`` 键 → 预览显示「从第 1 条
        起、预计新增 0 条」而紧接着的 check-now 从断点 500 续查并导入 5 条
        （复审探针 probe_61_preview_consistency.py 实测 RED）。
        """
        strategy = self._eric_strategy(query='title:("wide-rev")')
        self._mark_researched()
        docs = [eric_doc(f"EJ{6100000 + i}") for i in range(505)]
        channel = FakeEricChannel(docs)
        with mock.patch.object(eric_api, "_open", channel):
            first = self.client.post(f"{self.base}/check-now", headers=self.auth,
                                     json={"strategy_id": strategy["id"]})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["scan_next"], 500)
        # preview(strategy_id) 与 preview(query only) 与 check-now(query only)
        # 三者必须同一断点起点（500）
        for params, want_start, want_requests in (
                ({"source": "eric", "strategy_id": strategy["id"]}, 500, [500]),
                ({"source": "eric", "query": 'title:("wide-rev")'}, 500, [500])):
            ch = FakeEricChannel(docs)
            with mock.patch.object(eric_api, "_open", ch):
                preview = self.client.get(f"{self.base}/preview",
                                          headers=self.auth, params=params)
            self.assertEqual(preview.status_code, 200, preview.text)
            body = preview.json()
            self.assertEqual(body["scan_start"], want_start, params)
            self.assertEqual([_request_start(url) for url in ch.calls],
                             want_requests, params)
        ch = FakeEricChannel(docs)
        with mock.patch.object(eric_api, "_open", ch):
            check = self.client.post(f"{self.base}/check-now", headers=self.auth,
                                     json={"source": "eric",
                                           "query_string": 'title:("wide-rev")'})
        self.assertEqual(check.status_code, 200, check.text)
        self.assertEqual(check.json()["scan_start"], 500)
        self.assertEqual([_request_start(url) for url in ch.calls], [500])

    def test_edited_query_text_resets_breakpoint(self):
        """终审第 7 波复审（残留）：条目 query_text 原地修改 → 旧断点失效。

        修复前（R1 实现）：游标只按 strategy id 键控、不绑定文本；改检索式后
        旧断点（500）仍生效，新命中集被从头跳过 500 条。
        """
        strategy = self._eric_strategy(query='title:("before-edit")')
        self._mark_researched()
        docs = [eric_doc(f"EJ{6200000 + i}") for i in range(520)]
        channel = FakeEricChannel(docs)
        with mock.patch.object(eric_api, "_open", channel):
            first = self.client.post(f"{self.base}/check-now", headers=self.auth,
                                     json={"strategy_id": strategy["id"]})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["scan_next"], 500)
        state = json.loads((self._task_dir() / "living_review.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(state["eric_scan"][f"s{strategy['id']}"]["offset"], 500)
        # 条目检索式原地修改（同一 id、不同文本 → 不同命中集）
        from coscreen.search_strategy import store_path as strategy_store_path

        lib = strategy_store_path(self._task_dir())
        with closing(sqlite3.connect(lib)) as conn:
            conn.execute("UPDATE search_strategies SET query_text = ?"
                         " WHERE id = ?", ('title:("after-edit")', strategy["id"]))
            conn.commit()
        ch = FakeEricChannel(docs)
        with mock.patch.object(eric_api, "_open", ch):
            second = self.client.post(f"{self.base}/check-now", headers=self.auth,
                                      json={"strategy_id": strategy["id"]})
        self.assertEqual(second.status_code, 200, second.text)
        body = second.json()
        # 新检索式的首次检查从第 1 条起（旧断点不属于这份命中集）
        self.assertEqual(body["scan_start"], 0)
        self.assertEqual([_request_start(url) for url in ch.calls], [0, 200, 400])
        # 改回原文：每条目单槽游标已被改文本那次的断点覆盖（500/after-edit），
        # 指纹不匹配 → 头部重扫（安全自愈：重扫的是已知键，增量 0，不漏尾部）
        with closing(sqlite3.connect(lib)) as conn:
            conn.execute("UPDATE search_strategies SET query_text = ?"
                         " WHERE id = ?", ('title:("before-edit")', strategy["id"]))
            conn.commit()
        ch2 = FakeEricChannel(docs)
        with mock.patch.object(eric_api, "_open", ch2):
            third = self.client.post(f"{self.base}/check-now", headers=self.auth,
                                     json={"strategy_id": strategy["id"]})
        self.assertEqual(third.status_code, 200, third.text)
        self.assertEqual(third.json()["scan_start"], 0)
        self.assertEqual([_request_start(url) for url in ch2.calls], [0, 200, 400])
        state = json.loads((self._task_dir() / "living_review.json")
                           .read_text(encoding="utf-8"))
        cursor = state["eric_scan"][f"s{strategy['id']}"]
        self.assertEqual(cursor["offset"], 500)
        self.assertEqual(cursor["query_sha"],
                         eric_inc.scan_query_fingerprint('title:("before-edit")'))

    def test_preview_storage_failure_maps_503_both_addressings(self):
        """终审第 7 波 R3：检索式库损坏时 query-only 预览不得 500。

        R2 引入 match_subscribed_eric_strategy 后未包 ensure_library 的
        SearchStrategyError/sqlite3.Error：strategy_id 预览 503
        ``storage_unavailable`` 而 query-only 预览落到全局兜底 500
        （复审探针 probe_61_preview_broken_lib.py 实测）。
        """
        strategy = self._eric_strategy(query='title:("broken-lib")')
        from coscreen.search_strategy import store_path as strategy_store_path

        lib = strategy_store_path(self._task_dir())
        self.assertTrue(lib.is_file())
        lib.write_bytes(b"this is not a sqlite database" * 10)
        by_id = self.client.get(
            f"{self.base}/preview", headers=self.auth,
            params={"source": "eric", "strategy_id": strategy["id"]})
        self.assertEqual(by_id.status_code, 503, by_id.text)
        self.assertEqual(by_id.json()["detail"]["error_code"],
                         "storage_unavailable")
        by_query = self.client.get(
            f"{self.base}/preview", headers=self.auth,
            params={"source": "eric", "query": 'title:("broken-lib")'})
        # 同一存储故障：两种寻址同一 503 形状，绝不 500
        self.assertEqual(by_query.status_code, 503, by_query.text)
        self.assertEqual(by_query.json()["detail"], by_id.json()["detail"])

    def test_preview_query_only_library_side_effect_is_disclosed(self):
        """R3 披露钉：query-only 预览与 check-now 同解析（必要时建/迁检索式库）。

        预览是 GET 但与 check-now 用同一 writable ensure_library（保证条目
        匹配与断点键恒一致）；副作用 = 幂等建库/迁移，契约 §四已如实披露。
        """
        fresh = self.client.post(
            "/api/tasks", json={"name": "w4-preview-lib-side-effect"},
            headers=self.auth).json()["task_id"]
        from coscreen.search_strategy import store_path as strategy_store_path
        from coscreen import tasks as tasks_mod

        task_dir = tasks_mod.find_task(fresh, DATA_DIR).dir
        lib = strategy_store_path(task_dir)
        self.assertFalse(lib.is_file())
        channel = FakeEricChannel([eric_doc("EJ6300001")])
        with mock.patch.object(eric_api, "_open", channel):
            response = self.client.get(
                f"/api/tasks/{fresh}/living-review/preview", headers=self.auth,
                params={"source": "eric", "query": 'title:("any")'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(lib.is_file(), "契约披露的建库副作用必须如实发生")
        first_mtime = lib.stat().st_mtime_ns
        with mock.patch.object(eric_api, "_open", FakeEricChannel(
                [eric_doc("EJ6300001")])):
            again = self.client.get(
                f"/api/tasks/{fresh}/living-review/preview", headers=self.auth,
                params={"source": "eric", "query": 'title:("any")'})
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(lib.stat().st_mtime_ns, first_mtime,
                         "二次预览零额外写入（幂等）")

    def test_failure_409_shape_matches_pubmed(self):
        eric_strategy = self._eric_strategy()
        with mock.patch.object(
                eric_inc, "fetch_eric_incremental",
                side_effect=eric_api.EricSearchError("ERIC 请求超时（15s）。",
                                                     code="eric_timeout")):
            eric_resp = self.client.post(f"{self.base}/check-now", headers=self.auth,
                                         json={"strategy_id": eric_strategy["id"]})
        self.assertEqual(eric_resp.status_code, 409, eric_resp.text)
        detail = eric_resp.json()["detail"]
        self.assertEqual(detail["code"], "eric_unavailable")
        self.assertEqual(detail["message_key"], "living_review.eric.unavailable")
        self.assertEqual(detail["params"], {"reason": "eric_timeout"})
        self.assertEqual(detail["hint"], "retry_or_manual_import")
        self.assertEqual(detail["limits"]["max_records_per_check"], 500)
        self.assertTrue(detail["import_url"])
        self.assertTrue(detail["review_url"])

        # 与 PubMed 失败 409 同形（PubMed 既有键 ⊆ ERIC 键，且共同键语义一致）
        self._pubmed_strategy()
        with mock.patch.object(lr_plugin, "fetch_pubmed_updates",
                               side_effect=lr_plugin.PubMedPluginError(
                                   "PubMed 返回 HTTP 503")):
            pubmed_resp = self.client.post(f"{self.base}/check-now",
                                           headers=self.auth,
                                           json={"since_date": "2024-01-01"})
        self.assertEqual(pubmed_resp.status_code, 409)
        pubmed_detail = pubmed_resp.json()["detail"]
        self.assertTrue(set(pubmed_detail) <= set(detail),
                        (set(pubmed_detail), set(detail)))
        self.assertIn("limits", detail)
        # 失败零副作用
        self.assertEqual(self._source_records(), [])
        self.assertEqual(self._history(), [])
        self.assertEqual(self._batch_total(), 0)

    def test_pubmed_dispatch_zero_regression_smoke(self):
        strategy = self._pubmed_strategy()
        with mock.patch.object(lr_plugin, "fetch_pubmed_updates",
                               return_value=lr_plugin.parse_medline_records(
                                   medline_for_pmid("36934452"))):
            response = self.client.post(f"{self.base}/check-now",
                                        headers=self.auth,
                                        json={"since_date": "2024-01-01"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["source"], "pubmed")
        self.assertEqual(response.json()["fetched"], 1)
        self.assertEqual(strategy["database"], "pubmed")

    def test_other_database_entry_still_unsupported(self):
        response = self.client.post(
            f"{self.strategy_base}/strategies", headers=self.auth,
            json={"title": "Embase", "database": "embase",
                  "query_text": "x", "subscribe": True})
        self.assertEqual(response.status_code, 201, response.text)
        sid = response.json()["strategy"]["id"]
        resp = self.client.post(f"{self.base}/check-now", headers=self.auth,
                                json={"strategy_id": sid})
        self.assertEqual(resp.status_code, 409, resp.text)
        self.assertEqual(resp.json()["detail"]["error_code"], "source_unsupported")

    def test_source_param_dispatch_and_conflict(self):
        strategy = self._eric_strategy(query='title:("dispatch")')
        self._mark_researched()
        snapshot = snapshot_from([eric_doc("EJ5000001")])
        with mock.patch.object(eric_inc, "fetch_eric_incremental",
                               return_value=snapshot):
            # source=eric + query_string：按 ERIC 订阅条目精确匹配分派
            response = self.client.post(
                f"{self.base}/check-now", headers=self.auth,
                json={"source": "eric", "query_string": 'title:("dispatch")'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["source"], "eric")
        self.assertEqual(response.json()["strategy_id"], strategy["id"])
        # strategy_id 自带源与 source 冲突 → 400 结构化
        conflict = self.client.post(
            f"{self.base}/check-now", headers=self.auth,
            json={"strategy_id": strategy["id"], "source": "pubmed"})
        self.assertEqual(conflict.status_code, 400, conflict.text)
        self.assertEqual(conflict.json()["detail"]["message_key"],
                         "living_review.source_conflict")
        # source=eric 但任务没有任何 ERIC 条目/检索式 → 400 引导先存检索式
        fresh = self.client.post("/api/tasks",
                                 json={"name": f"w4-eric-empty-{self._testMethodName}"},
                                 headers=self.auth).json()["task_id"]
        missing = self.client.post(
            f"/api/tasks/{fresh}/living-review/check-now", headers=self.auth,
            json={"source": "eric"})
        self.assertEqual(missing.status_code, 400, missing.text)
        self.assertEqual(missing.json()["detail"]["message_key"],
                         "living_review.eric_source_required")


class PreviewEndpointPins(_Base):
    def test_preview_returns_diff_size_without_writes(self):
        strategy = self._eric_strategy()
        self._mark_researched()
        # 先制造已知键（staged）
        with mock.patch.object(eric_inc, "fetch_eric_incremental",
                               return_value=snapshot_from(
                                   [eric_doc("EJ6000001"), eric_doc("EJ6000002")])):
            first = self.client.post(f"{self.base}/check-now", headers=self.auth,
                                     json={"strategy_id": strategy["id"]})
        self.assertEqual(first.status_code, 200, first.text)
        history_before = self._history()
        records_before = self._records()
        batches_before = self._batch_total()

        docs = [eric_doc("EJ6000002"), eric_doc("EJ6000003"), eric_doc("EJ6000004")]
        with mock.patch.object(eric_inc, "fetch_eric_incremental",
                               return_value=snapshot_from(docs)):
            response = self.client.get(
                f"{self.base}/preview", headers=self.auth,
                params={"source": "eric", "strategy_id": strategy["id"]})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["source"], "eric")
        self.assertEqual(body["total_hits"], 3)
        self.assertEqual(body["new_count"], 2)  # EJ6000002 已知
        self.assertEqual([row["zotero_key"] for row in body["records"]],
                         ["EJ6000003", "EJ6000004"])
        self.assertEqual(body["message_key"], "living_review.eric.preview")
        self.assertEqual(body["outbound"], ["api.ies.ed.gov"])
        # 只读：不建批次、不写历史、不动游标
        self.assertEqual(self._history(), history_before)
        self.assertEqual(self._batch_total(), batches_before)
        self.assertEqual(self._records(), records_before)

    def test_preview_baseline_on_empty_task(self):
        strategy = self._eric_strategy()
        with mock.patch.object(eric_inc, "fetch_eric_incremental",
                               return_value=snapshot_from(
                                   [eric_doc("EJ7000001")])):
            response = self.client.get(
                f"{self.base}/preview", headers=self.auth,
                params={"source": "eric", "strategy_id": strategy["id"]})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["baseline"])
        self.assertEqual(body["new_count"], 1)
        self.assertEqual(body["known_keys"], 0)

    def test_preview_requires_eric_source(self):
        response = self.client.get(f"{self.base}/preview", headers=self.auth,
                                   params={"source": "pubmed"})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["message_key"],
                         "living_review.eric_source_required")

    def test_preview_failure_degrades_to_409(self):
        strategy = self._eric_strategy()
        with mock.patch.object(
                eric_inc, "fetch_eric_incremental",
                side_effect=eric_api.EricSearchError("ERIC 不可达：boom",
                                                     code="eric_unreachable")):
            response = self.client.get(
                f"{self.base}/preview", headers=self.auth,
                params={"source": "eric", "strategy_id": strategy["id"]})
        self.assertEqual(response.status_code, 409, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "eric_unavailable")
        self.assertEqual(detail["params"], {"reason": "eric_unreachable"})


class PermissionPins(_Base):
    def test_outsider_cannot_check_or_preview(self):
        strategy = self._eric_strategy()
        check = self.client.post(f"{self.base}/check-now", headers=self.outsider_auth,
                                 json={"strategy_id": strategy["id"]})
        self.assertEqual(check.status_code, 403, check.text)
        preview = self.client.get(f"{self.base}/preview", headers=self.outsider_auth,
                                  params={"source": "eric", "strategy_id": strategy["id"]})
        self.assertEqual(preview.status_code, 403, preview.text)
        # 未登录 401
        self.assertEqual(self.client.post(f"{self.base}/check-now",
                                          json={}).status_code, 401)
        self.assertEqual(self.client.get(f"{self.base}/preview",
                                         params={"source": "eric"}).status_code, 401)


# ---------------------------------------------------------------------------
# 4. ERIC 键 RIS 往返（不降级为生成键）
# ---------------------------------------------------------------------------

class EricRisRoundTripPins(unittest.TestCase):
    def test_eric_ids_survive_virtual_ris(self):
        articles = [eric_api.doc_to_article(eric_doc("EJ1482166")),
                    eric_api.doc_to_article(eric_doc("ED123456"))]
        payload = eric_api.articles_to_ris(articles)
        with tempfile.TemporaryDirectory(prefix="rf_w4_ris_") as tmp:
            path = Path(tmp) / "eric-update.ris"
            path.write_text(payload, encoding="utf-8")
            parsed = parse_ris(path)
        self.assertEqual([a.zotero_key for a in parsed], ["EJ1482166", "ED123456"])
        for article in parsed:
            self.assertNotIn("_generated_key", article.raw)
            self.assertNotIn("_demoted_source_key", article.raw)


if __name__ == "__main__":
    unittest.main()
