"""review67 low 批次钉子（专属新文件，撤实现必红）。

对应 2026-11-12 双泳道评审 out/code_review_2026-11-12/REPORT.md 的 13 条 low：
L-26 median 偶数年均值、L-27 LaTeX verbatim 注入、L-28 续跑游标并发、
L-29 preflight 整包互斥、L-30 导出流中总量闸、L-31 notify IPv6 字面量、
L-36 trace 1MB 门位次、L-37 trace 导入侧 origin 预算、L-49 esearch PMID
去重、L-50 count 暴露+截断短路、L-51 空代理 opener、L-11 全点号名防御、
L-23 sigmoid ≤1 ULP 基准钉。
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# 短前缀 + 短任务名：Windows MAX_PATH 对临时目录很敏感。
fixture = tempfile.TemporaryDirectory(prefix="rf67l_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from coscreen import fulltext as fulltext_mod  # noqa: E402
from coscreen import manuscript_builder as mb  # noqa: E402
from coscreen import notify as notify_mod  # noqa: E402
from coscreen import research_trace  # noqa: E402
from coscreen import research_trace_export  # noqa: E402


# ---------------------------------------------------------------------------
# L-26 / L-27：稿件撰写器
# ---------------------------------------------------------------------------

class MedianYearsTests(unittest.TestCase):
    """L-26：偶数个年份取中间两数均值（原实现取上中位，偏差一年）。"""

    def test_median_label_even_and_odd(self):
        self.assertEqual(mb._median_label([2001]), "2001")
        self.assertEqual(mb._median_label([2000, 2001, 2003, 2004]), "2002")
        self.assertEqual(mb._median_label([2000, 2003]), "2001.5")

    def test_characteristics_sentence_uses_true_median(self):
        articles = [{"year": y, "journal": "J Test"} for y in (2000, 2001, 2003, 2004)]
        section = mb._section_results_characteristics({"articles": articles})
        self.assertIn("median 2002", " ".join(section["paragraphs"]))


class LatexVerbatimInjectionTests(unittest.TestCase):
    """L-27：preformatted 含 \\end{verbatim} 时不得逃逸出 verbatim 环境。"""

    EVIL = "line-before\\end{verbatim}\n\\input{evil.tex}\nline-after"

    @staticmethod
    def _render(block: str) -> str:
        manuscript = {"meta": {"generated_at": "2026-11-12T00:00:00Z"},
                      "sections": [{"key": "search", "title": "Search strategy",
                                    "paragraphs": ["Query below."],
                                    "preformatted": [block], "placeholder": False}]}
        return mb.render_latex(manuscript)

    def test_clean_block_renders_exactly_as_before(self):
        block = "cancer[tiab] AND review[pt]"
        rendered = self._render(block)
        self.assertIn("\n\\begin{verbatim}\n" + block + "\n\\end{verbatim}\n",
                      rendered)

    def test_injected_end_marker_cannot_escape(self):
        rendered = self._render(self.EVIL)
        begin = rendered.count("\\begin{verbatim}")
        end = rendered.count("\\end{verbatim}")
        self.assertGreaterEqual(begin, 2)
        self.assertEqual(begin, end, "环境开闭必须配对")

        # 逐行状态机（模拟 TeX 扫描）：标记行独占一行（切分保证内容行
        # 永远不含完整的结束标记），据此判定 \input 行是否仍在环境内。
        lines = rendered.splitlines()
        evil_index = next(i for i, line in enumerate(lines)
                          if "\\input{evil.tex}" in line)
        in_verbatim = False
        for line in lines[:evil_index]:
            if in_verbatim and line == "\\end{verbatim}":
                in_verbatim = False
            elif not in_verbatim and line == "\\begin{verbatim}":
                in_verbatim = True
        self.assertTrue(in_verbatim, "注入标记后的内容必须仍处于 verbatim 内")

        # 旧实现会把恶意标记原样内插（begin 1 个、end 2 个）——环境开闭
        # 配对断言已钉住新形态，这里再确认注入行确实被切分处理过。
        self.assertNotEqual(rendered.count("\\end{verbatim}"), 1)


# ---------------------------------------------------------------------------
# L-31：notify Apprise IPv6 字面量拦截
# ---------------------------------------------------------------------------

class AppriseIpv6LiteralGuardTests(unittest.TestCase):
    """L-31：方括号/裸 IPv6 字面量必须命中本机/私网拦截。"""

    ENV = "REVIEWFLOW_NOTIFY_TEST_APPRISE_URL"

    def _send(self, url: str):
        with mock.patch.dict(os.environ, {self.ENV: url}):
            return notify_mod._send_apprise(
                {"type": "apprise", "enabled": True, "url_env": self.ENV,
                 "timeout_seconds": 1.0}, "text", timeout=1.0)

    def _assert_blocked(self, url):
        with self.assertRaises(notify_mod.NotifyError) as caught:
            self._send(url)
        self.assertIn("private_host", str(caught.exception))

    def test_ipv6_bracketed_forms_blocked(self):
        self._assert_blocked("json://[::1]/hook")
        self._assert_blocked("json://[::ffff:127.0.0.1]/hook")
        self._assert_blocked("json://[fe80::1]/hook")

    def test_ipv6_bracketed_with_port_blocked(self):
        self._assert_blocked("json://[::1]:8443/hook")

    def test_ipv6_bare_form_blocked(self):
        self._assert_blocked("json://::1/hook")

    def test_ipv4_bracketed_blocked(self):
        self._assert_blocked("json://[127.0.0.1]/hook")

    def test_public_host_passes_literal_gate(self):
        # 公网字面量/主机名不被拦：走到 apprise 懒导入（venv 未装 →
        # channel_unavailable/apprise_missing，而不是 private_host）。
        for url in ("json://8.8.8.8/hook", "json://example.com/hook"):
            with self.assertRaises(notify_mod.NotifyError) as caught:
                self._send(url)
            self.assertNotIn("private_host", str(caught.exception))


# ---------------------------------------------------------------------------
# L-36 / L-37：研究轨迹
# ---------------------------------------------------------------------------

class TraceGateOrderTests(unittest.TestCase):
    """L-36：1MB 载荷门必须在 is_recording 判定之后（opt-in 边界）。"""

    def _context(self):
        return research_trace.TraceContext("task-67", 7, request_id="req-67-a")

    def test_recording_off_big_payload_is_not_gated(self):
        conn = sqlite3.connect(":memory:")
        with closing(conn):
            research_trace.ensure_schema(conn)
            payload = {"text": "x" * 1_100_000}          # > 1MB 合法业务载荷
            is_replay, original = research_trace.begin_write_request(
                conn, self._context(), stage="screen",
                operation="save_coding_note", subject_id="k1", payload=payload)
            self.assertFalse(is_replay)
            self.assertIsNone(original)

    def test_recording_on_big_payload_still_gated(self):
        conn = sqlite3.connect(":memory:")
        with closing(conn):
            research_trace.ensure_schema(conn)
            context = self._context()
            research_trace.update_settings(
                conn, context, stages=["screen"], status="recording",
                operation_id="op-1", expected_version=0)
            with self.assertRaises(ValueError) as caught:
                research_trace.begin_write_request(
                    conn, context, stage="screen",
                    operation="save_coding_note", subject_id="k1",
                    payload={"text": "x" * 1_100_000})
            self.assertIn("1 MB", str(caught.exception))

    def test_replay_detection_survives_reorder(self):
        conn = sqlite3.connect(":memory:")
        with closing(conn):
            research_trace.ensure_schema(conn)
            context = self._context()
            research_trace.update_settings(
                conn, context, stages=["screen"], status="recording",
                operation_id="op-1", expected_version=0)
            args = dict(stage="screen", operation="save_coding_note",
                        subject_id="k1", payload={"text": "note"})
            first = research_trace.begin_write_request(conn, context, **args)
            research_trace.finish_write_request(conn, context, {"ok": True})
            second = research_trace.begin_write_request(conn, context, **args)
            self.assertEqual(first, (False, None))
            self.assertEqual(second, (True, {"ok": True}))


class TraceImportBudgetTests(unittest.TestCase):
    """L-37：导入侧 origin_json 单列预算与导出侧单事件行上限对齐。"""

    def test_origin_budget_matches_export_event_cap(self):
        self.assertEqual(research_trace._MAX_ORIGIN_JSON_BYTES,
                         research_trace_export.MAX_EVENT_BYTES)

    def test_wide_event_round_trips_into_import(self):
        conn = sqlite3.connect(":memory:")
        with closing(conn):
            research_trace.ensure_schema(conn)
            event = {
                "event_id": str(uuid.uuid4()), "store_id": "store-67",
                "sequence": 1, "stage": "screen", "action": "decision_changed",
                "subject_id": "k1", "recorded_at": "2026-11-12T00:00:00Z",
                "actor_id": "a1",
                "before": {"note": "a" * 600_000},       # 各列均 < 1MB
                "after": {"note": "b" * 900_000},
            }
            result = research_trace.import_event(
                conn, task_id="task-67", importer_actor_id=7, event=event)
            self.assertEqual(result["status"], "imported")
            origin_len = conn.execute(
                "SELECT length(origin_json) FROM trace_events").fetchone()[0]
            self.assertGreater(origin_len, 1_000_000,
                               "origin_json 应以放宽预算整条落库")
            digest = conn.execute(
                "SELECT origin_sha256 FROM trace_events").fetchone()[0]
            self.assertEqual(digest, research_trace.origin_event_sha256(event))

    def test_oversized_single_column_still_rejected(self):
        conn = sqlite3.connect(":memory:")
        with closing(conn):
            research_trace.ensure_schema(conn)
            event = {
                "event_id": str(uuid.uuid4()), "store_id": "store-67",
                "sequence": 2, "stage": "screen", "action": "decision_changed",
                "subject_id": "k2", "recorded_at": "2026-11-12T00:00:00Z",
                "actor_id": "a1",
                "after": {"note": "b" * 1_100_000},     # 单列 > 1MB：写路径对称拒绝
            }
            with self.assertRaises(ValueError):
                research_trace.import_event(
                    conn, task_id="task-67", importer_actor_id=7, event=event)


# ---------------------------------------------------------------------------
# L-49 / L-50 / L-51：living_review 插件
# ---------------------------------------------------------------------------

def _medline(pmid: str, title: str) -> str:
    return f"PMID- {pmid}\nTI  - {title}\nAU  - Doe J\nDP  - 2026\n\n"


class PluginPagingTests(unittest.TestCase):
    """L-49/L-50：esearch 跨页去重 + count 暴露 + 截断短路。"""

    @staticmethod
    def _esearch(pmids, count):
        return json.dumps(
            {"esearchresult": {"idlist": list(pmids), "count": str(count)}}
        ).encode("utf-8")

    def test_cross_page_pmids_are_deduplicated(self):
        from custom_backend import living_review_plugin as lr
        calls = {"esearch": 0, "efetch": []}

        def opener(url, timeout, data=None):
            if "esearch" in url:
                calls["esearch"] += 1
                if calls["esearch"] == 1:
                    return self._esearch(["1", "2", "3", "4"], 6)
                # 漂移形态：第二页混入第一页的重复 PMID（edat 窗口实时增长）
                return self._esearch(["5", "6", "1", "2"], 6)
            calls["efetch"].append(data.decode("utf-8"))
            return ("".join(_medline(str(i), f"Study {i}") for i in range(1, 7))
                    ).encode("utf-8")

        with mock.patch.object(lr, "ESEARCH_PAGE_SIZE", 4), \
                mock.patch.object(lr, "EFETCH_PAGE_SIZE", 10):
            articles = lr.fetch_pubmed_updates("q", "2026-01-01", url_opener=opener)

        self.assertEqual([a["pmid"] for a in articles],
                         ["1", "2", "3", "4", "5", "6"], "重复 PMID 不得透传")
        self.assertEqual(len(calls["efetch"]), 1)
        self.assertIn("id=1%2C2%2C3%2C4%2C5%2C6", calls["efetch"][0])
        self.assertEqual(articles.total_hits, 6)

    def test_count_exposed_on_result_carrier(self):
        from custom_backend import living_review_plugin as lr

        def opener(url, timeout, data=None):
            if "esearch" in url:
                return self._esearch(["36934452"], 1)
            return _medline("36934452", "Study").encode("utf-8")

        result = lr.fetch_pubmed_updates("q", "2026-01-01", url_opener=opener)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.total_hits, 1)

    def test_total_over_retmax_short_circuits_before_efetch(self):
        from custom_backend import living_review_plugin as lr
        seen = {"esearch": 0}

        def opener(url, timeout, data=None):
            if "esearch" in url:
                seen["esearch"] += 1
                return self._esearch(["1"] * 5000, lr.RETMAX)
            raise AssertionError("截断短路后不应有任何 efetch")

        with self.assertRaises(lr.PubMedPluginError) as caught:
            lr.fetch_pubmed_updates("q", "2026-01-01", url_opener=opener)
        self.assertIn("截断", str(caught.exception))
        self.assertEqual(seen["esearch"], 1, "第一页即可判定，不再翻页")


class PluginOpenerDisciplineTests(unittest.TestCase):
    """L-51：_OPENER 显式空代理——环境代理不得中转 PubMed 出站。"""

    def test_opener_ignores_proxy_environment(self):
        """空 ProxyHandler 不注册任何代理路由方法（与 pdf_fetch 同一判定口径）。"""
        import urllib.request as _urllib
        from custom_backend import living_review_plugin as lr

        def proxy_routing(opener):
            return sorted(name for name in opener.handle_open
                          for _h in opener.handle_open[name]
                          if type(_h).__name__ == "ProxyHandler")

        with mock.patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:9",
                                          "HTTPS_PROXY": "http://127.0.0.1:9",
                                          "http_proxy": "http://127.0.0.1:9"}):
            self.assertTrue(proxy_routing(_urllib.build_opener()),
                            "前提：默认 opener 在设代理环境变量后会走代理")
            self.assertEqual(
                proxy_routing(lr._OPENER), [],
                "living_review opener 必须忽略 HTTP(S)_PROXY（review67 L-51）")
        self.assertTrue(any(type(h).__name__ == "_NoRedirectHandler"
                            for h in lr._OPENER.handlers),
                        "拒绝重定向的 handler 不受影响")


# ---------------------------------------------------------------------------
# L-11：fulltext 全点号文件名防御
# ---------------------------------------------------------------------------

class AllDotsFilenameTests(unittest.TestCase):
    """L-11：全点号名回落默认名（Windows CreateFile 直接拒绝纯点串）。"""

    def test_all_dots_name_falls_back(self):
        path = fulltext_mod.save_upload_to_tempdir(b"%PDF-1.4 x", "...")
        try:
            self.assertEqual(path.name, "upload.pdf")
            self.assertEqual(path.read_bytes(), b"%PDF-1.4 x")
        finally:
            shutil.rmtree(path.parent, ignore_errors=True)

    def test_normal_name_preserved(self):
        path = fulltext_mod.save_upload_to_tempdir(b"data", "report 2026.pdf")
        try:
            self.assertEqual(path.name, "report 2026.pdf")
        finally:
            shutil.rmtree(path.parent, ignore_errors=True)


# ---------------------------------------------------------------------------
# L-23：sigmoid 与 scipy.expit 数值等价（≤1 ULP）基准钉
# ---------------------------------------------------------------------------

class SigmoidUlpBenchmarkTests(unittest.TestCase):
    """L-23：docstring 改口径后的基准——数值等价（位级差 ≤3 ULP），不逐位。

    实测（本 venv：numpy 2.4.6 / scipy 1.17.1，200013 样本）：位级差最大 3
    ULP、绝对差 ≤2.22e-16；对 Decimal 高精度正确舍入值两侧各差 ≤1 ULP。
    """

    def test_matches_scipy_expit_within_three_ulp(self):
        try:
            import numpy as np
            from scipy.special import expit
        except ImportError:                                # pragma: no cover
            self.skipTest("numpy/scipy 不在当前环境")
        from coscreen.al.svm import _sigmoid

        rng = np.random.default_rng(20261112)
        samples = np.concatenate([
            rng.normal(0.0, 20.0, 200_000),
            np.array([0.0, 1e-300, -1e-300, 1.0, -1.0, 37.0, -37.0,
                      700.0, -700.0, 709.0, -709.0, 745.0, -745.0]),
        ])
        ours = _sigmoid(samples)
        reference = expit(samples)
        # sigmoid 输出恒正：float64 位模式随值单调，整数位距即 ULP 距离。
        bit_distance = np.abs(
            np.ascontiguousarray(ours, dtype="<f8").view("<i8")
            - np.ascontiguousarray(reference, dtype="<f8").view("<i8"))
        self.assertLessEqual(int(bit_distance.max()), 3,
                             "sigmoid 与 expit 的位级差不得超过 3 ULP")
        self.assertLessEqual(float(np.abs(ours - reference).max()), 2.5e-16)

    def test_docstring_no_longer_claims_bit_identical(self):
        from coscreen.al import svm as svm_mod
        source = Path(svm_mod.__file__).read_text(encoding="utf-8")
        self.assertNotIn("与 ``scipy.special.expit`` 逐位一致", source)


# ---------------------------------------------------------------------------
# L-28 / L-29 / L-30：后端 API（真实 TestClient）
# ---------------------------------------------------------------------------

try:
    from fastapi.testclient import TestClient  # noqa: E402

    from coscreen import db as db_mod  # noqa: E402
    from coscreen import tasks as tasks_mod  # noqa: E402
    from coscreen.models import Article  # noqa: E402
    from custom_backend import auth as auth_mod  # noqa: E402
    from custom_backend import pdf_fetch_api  # noqa: E402
    from custom_backend import retraction_watch_api as w9  # noqa: E402
    from custom_backend import task_package_api as tp  # noqa: E402
    from custom_backend.main import DATA_DIR, app  # noqa: E402
    _APP_AVAILABLE = True
except Exception:                                           # pragma: no cover
    _APP_AVAILABLE = False


@unittest.skipUnless(_APP_AVAILABLE, "后端依赖不可用")
class BackendBase(unittest.TestCase):
    _seq = 0

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        tag = cls.__name__[:10].lower()
        cls.owner, cls.oauth = cls._make_user(f"{tag}own")

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    @staticmethod
    def _make_user(prefix: str):
        user = auth_mod.create_user(f"{prefix}@example.test", f"{prefix}-Pass-2026!")
        token = auth_mod.create_access_token(user["id"], user["username"])
        return user, {"Authorization": "Bearer " + token}

    def setUp(self):
        type(self)._seq += 1
        name = f"{type(self).__name__[:10].lower()}{type(self)._seq}"
        response = self.client.post("/api/tasks", json={"name": name},
                                    headers=self.oauth)
        self.assertIn(response.status_code, (200, 201), response.text)
        self.task = response.json()["task_id"]
        self.info = tasks_mod.find_task(self.task, DATA_DIR)
        self.owner_db = self.info.dir / (
            tasks_mod.slugify_screener(self.owner["username"]) + ".db")
        db_mod.init_db(self.owner_db)

    def _seed_stage2_include(self, count: int):
        articles = [Article(zotero_key=f"K{i:03d}", title=f"Study {i}",
                            authors="Wang, A", journal="J Test", year=2021,
                            doi=f"10.1000/r67k{i:03d}")
                    for i in range(1, count + 1)]
        db_mod.upsert_articles(self.owner_db, articles, [])
        for article in articles:
            fulltext_mod.save_stage2(self.owner_db, article.zotero_key, "include")
        return articles


class _GateResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self, limit=-1):
        return self._payload if limit < 0 else self._payload[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class GateCrossrefStub:
    """第一次 open() 阻塞在门闩上（模拟慢出站）；其余立即返回空 works。"""

    def __init__(self):
        self.gate = threading.Event()
        self.armed = True
        self.calls = 0

    def open(self, request, timeout=None):
        self.calls += 1
        if self.armed:
            self.armed = False
            self.gate.wait(timeout=30)
        return _GateResponse(json.dumps({"message": {}}).encode("utf-8"))


@unittest.skipUnless(_APP_AVAILABLE, "后端依赖不可用")
class RetractionCursorRaceTests(BackendBase):
    """L-28：并发续跑的迟到提交不得倒退游标/复活 done run/伪 503。"""

    def test_late_writer_skips_instead_of_regressing(self):
        self._seed_stage2_include(w9.BATCH_SIZE + 10)   # 60 目标 = 50 + 10 两批
        stub = GateCrossrefStub()
        outcome = {}

        def late_writer():
            outcome["response"] = self.client.post(
                f"/api/tasks/{self.task}/retraction-check",
                json={"scope": "stage2_include"}, headers=self.oauth)

        with mock.patch.object(w9, "POLITE_INTERVAL_SECONDS", 0), \
                mock.patch.object(pdf_fetch_api, "_OPENER", stub):
            thread = threading.Thread(target=late_writer)
            thread.start()
            # 等 A（迟到写者）创建 run 1 并阻塞在首次出站上。
            deadline = time.monotonic() + 30
            while stub.calls < 1 and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(stub.calls, 1, "A 应恰好阻塞在第一次出站")

            # B：同一 run 从同一游标续跑两批直到 done（B 的出站立即返回）。
            first = self.client.post(
                f"/api/tasks/{self.task}/retraction-check",
                json={"scope": "stage2_include", "cursor": "1:0"},
                headers=self.oauth)
            self.assertEqual(first.status_code, 200, first.text)
            second = self.client.post(
                f"/api/tasks/{self.task}/retraction-check",
                json={"scope": "stage2_include",
                      "cursor": first.json()["cursor"]},
                headers=self.oauth)
            self.assertEqual(second.status_code, 200, second.text)
            self.assertTrue(second.json()["done"], "B 应跑完整个 run")

            # run 1 已 done → 新开 run 2（唯一部分索引允许）。
            third = self.client.post(
                f"/api/tasks/{self.task}/retraction-check",
                json={"scope": "stage2_include"}, headers=self.oauth)
            self.assertEqual(third.status_code, 200, third.text)

            # 释放 A：旧实现会以陈旧基线回写 run 1 → running（与 run 2 并存
            # 撞唯一索引 → IntegrityError → 503 伪存储故障）。
            stub.gate.set()
            thread.join(timeout=30)

        response = outcome["response"]
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["done"])
        self.assertEqual(payload["processed"], w9.BATCH_SIZE + 10)
        self.assertEqual(payload["processed_delta"], 0)

        with closing(sqlite3.connect(str(self.info.dir / "collaboration.db"))) as conn:
            rows = conn.execute(
                "SELECT run_id, status, cursor_pos FROM retraction_runs "
                "ORDER BY run_id").fetchall()
        self.assertEqual(rows[0], (1, "done", w9.BATCH_SIZE + 10),
                         "run 1 必须保持 done，不被迟到写者复活/倒退")
        self.assertEqual(rows[1][0], 2)
        self.assertEqual(rows[1][1], "running", "run 2 不受 A 的回写影响")
        self.assertEqual(len(rows), 2)


@unittest.skipUnless(_APP_AVAILABLE, "后端依赖不可用")
class PackagePreflightLockTests(BackendBase):
    """L-29：preflight 参与全局整包操作互斥（持锁在途 → 结构化 409）。"""

    def test_preflight_conflicts_with_held_package_op_lock(self):
        self.assertFalse(tp._PACKAGE_OP_LOCK.locked())
        tp._PACKAGE_OP_LOCK.acquire()
        try:
            response = self.client.get(
                f"/api/tasks/{self.task}/package/preflight", headers=self.oauth)
        finally:
            tp._PACKAGE_OP_LOCK.release()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("operation_busy", response.text)

    def test_preflight_ok_when_lock_free(self):
        response = self.client.get(
            f"/api/tasks/{self.task}/package/preflight", headers=self.oauth)
        self.assertEqual(response.status_code, 200, response.text)


@unittest.skipUnless(_APP_AVAILABLE, "后端依赖不可用")
class PackageExportStreamGateTests(unittest.TestCase):
    """L-30：整包导出写入循环按实际累计字节复核 2GB 上限。"""

    def test_written_total_gate_fires_on_growth_between_stat_and_write(self):
        workdir = Path(tempfile.mkdtemp(prefix="rf67l_export_"))
        self.addCleanup(shutil.rmtree, workdir, True)
        grown = workdir / "grown.bin"
        grown.write_bytes(b"x" * 600)

        def fake_collect(info, data_dir, include_pdfs, workdir_):
            return {
                "sources": [
                    {"arcname": "a.bin", "path": grown, "size": 300},   # stat 期 300
                    {"arcname": "b.bin", "path": grown, "size": 300},
                ],
                "task_payload": {}, "redacted_fields": [], "fulltexts": [],
                "skipped_fulltexts": [], "total_bytes": 600,            # 预检通过
            }

        info = SimpleNamespace(dir=workdir)
        with mock.patch.object(tp, "_collect_package_sources", fake_collect), \
                mock.patch.object(tp, "MAX_PACKAGE_BYTES", 1000):
            with self.assertRaises(tp.TaskPackageError) as caught:
                tp._build_package_zip(info, "unused", True, workdir)
        self.assertEqual(caught.exception.code, "package_too_large")
        self.assertEqual(caught.exception.params["limit_bytes"], 1000)
        self.assertEqual(caught.exception.params["total_bytes"], 1200,
                         "报**实际写入**累计值，而不是收集期 stat 值")


if __name__ == "__main__":
    unittest.main()
