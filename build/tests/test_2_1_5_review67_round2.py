# -*- coding: utf-8 -*-
"""review67 round2 · deepseek 处置复审 7 项 blockers 的修复钉。

- M-15：真实 _OPENER + 本地 HTTP 服务——配置端点自托管回环/RFC1918 经
  钉扎路径可用（allow_private_pin 豁免 peer 公网复核）；外部载荷口径不变。
- M-11：matrix **表头**（维度名）转义；load_decisions_csv 不无条件剥外部撇号。
- M-12：只读打开对 0 字节/早期库回落自愈（200 恢复），仍坏才 503。
- M-08：disk I/O error（非字面 unable to open）现存文件 → 503 不再缺员。
- L-47：prisma unable-to-open/disk I/O → 503（对齐 M-08 口径）。
- L-32：检索式词中括号 field_tag_malformed 常驻钉（deepseek：无钉则撤实现不红）。
"""
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

fixture = tempfile.TemporaryDirectory(prefix="rf_r67_r2_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
# 桌面模式（缺省）——回环=自托管卖点放行；不设 REVIEWFLOW_MODE。

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient  # noqa: E402

from coscreen import export as export_mod  # noqa: E402
from coscreen import screening_arbitration as arb_mod  # noqa: E402
from coscreen import search_strategy as strategy_core  # noqa: E402
from custom_backend import pdf_fetch_api as pdf_mod  # noqa: E402
from custom_backend import prisma_task_metrics as prisma_mod  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend.main import DATA_DIR, app  # noqa: E402

DATA_ROOT = Path(DATA_DIR)
PW = "R67-R2-2026!!"


class _LoopbackHandler(BaseHTTPRequestHandler):
    reply = {"is_oa": False}
    hits: list[str] = []

    def do_GET(self):  # noqa: N802
        type(self).hits.append(self.path)
        payload = json.dumps(type(self).reply).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        return


class M15SelfHostedPinnedPins(unittest.TestCase):
    """真实 _OPENER：自托管回环经钉扎路径可用（deepseek 阻断项回归）。"""

    def test_loopback_config_endpoint_reachable_via_real_opener(self):
        server = HTTPServer(("127.0.0.1", 0), _LoopbackHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        try:
            settings = {"unpaywall_base_url": f"http://127.0.0.1:{port}",
                        "email": "a@example.test"}
            candidates, rejected = pdf_mod._unpaywall_pdf_candidates(
                "10.12345/r67r2", settings)
            # is_oa=False → 空候选零拒绝；关键断言=未抛 OutboundError 且
            # 本地服务真实收到 /v2/ 请求（连接经钉扎到达回环）。
            self.assertEqual(candidates, [])
            self.assertEqual(rejected, [])
            self.assertTrue(any(p.startswith(f"/v2/") for p in _LoopbackHandler.hits),
                            f"本地服务未收到请求: {_LoopbackHandler.hits}")
        finally:
            server.shutdown()
            _LoopbackHandler.hits.clear()

    def test_allow_private_flag_wired_on_request(self):
        # 单元级：_get_bytes 把 allow_private_pin 随钉扎地址写上请求对象，
        # handler 据此放行 peer——旗子断链即刻红。
        import urllib.request
        req = urllib.request.Request("http://example.invalid/x")
        captured = {}

        class _FakeHandler:
            def http_open(self, r):
                captured["flag"] = getattr(r, "allow_private_pin", "MISSING")
                raise pdf_mod.OutboundError("unreachable", "stop")

        fake_opener = mock.Mock()
        fake_opener.open = mock.Mock(
            side_effect=lambda request, timeout=None: _FakeHandler().http_open(request))
        with mock.patch.object(pdf_mod, "_OPENER", fake_opener):
            with self.assertRaises(pdf_mod.OutboundError):
                pdf_mod._get_bytes("http://example.invalid/x", timeout=1,
                                   max_bytes=64, accept="application/json",
                                   pinned_address=["127.0.0.1"], allow_private_pin=True)
        self.assertIs(captured["flag"], True)


class M11MatrixHeaderPins(unittest.TestCase):
    def test_dimension_name_header_escaped(self):
        import pandas as pd
        table = {"columns": ["zotero_key", "=HYPERLINK(\"http://e\",\"c\")", "+dim x"],
                 "rows": [{"zotero_key": "K1"}]}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "matrix.csv"
            df = pd.DataFrame(table["rows"], columns=table["columns"])
            df.columns = [export_mod._csv_escape_cell(str(c)) for c in df.columns]
            df.to_csv(out, index=False, encoding="utf-8-sig")
            text = out.read_text(encoding="utf-8-sig")
        self.assertIn("'=HYPERLINK", text)      # 表头已转义
        self.assertNotIn(",=HYPERLINK", text)   # 无裸公式表头
        self.assertIn("'+dim x", text)


class M12ReadonlySelfHealPins(unittest.TestCase):
    def test_zero_byte_store_heals_to_200(self):
        from coscreen import tasks as tasks_mod
        client = TestClient(app)
        client.__enter__()
        try:
            user = auth_mod.create_user("r67r2-strat@example.test", PW)
            headers = {"Authorization": "Bearer " + auth_mod.create_access_token(
                user["id"], user["username"])}
            task = client.post("/api/tasks", json={"name": "r67r2-strat"},
                               headers=headers).json()["task_id"]
            store = strategy_core.store_path(tasks_mod.tasks_root(DATA_DIR) / task)
            store.parent.mkdir(parents=True, exist_ok=True)
            store.write_bytes(b"")  # 0 字节残留（写路径中断形态）
            resp = client.get(f"/api/tasks/{task}/search-strategy/strategies",
                              headers=headers)
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertEqual(resp.json().get("strategies", []), [])
        finally:
            client.__exit__(None, None, None)


class M08DiskIoErrorPins(unittest.TestCase):
    def test_disk_io_error_on_existing_file_raises_unavailable(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "member.db"
            db.write_bytes(b"x" * 64)
            with mock.patch.object(
                    arb_mod.sqlite3, "connect",
                    side_effect=sqlite3.OperationalError("disk I/O error")):
                with self.assertRaises(arb_mod.ScreenerLibraryUnavailableError):
                    arb_mod._decision_rows_read_only(db, "stage1")
            # 对照：不存在的路径维持「无决策」降级（return []）。
            with mock.patch.object(
                    arb_mod.sqlite3, "connect",
                    side_effect=sqlite3.OperationalError("unable to open database file")):
                self.assertEqual(
                    arb_mod._decision_rows_read_only(Path(tmp) / "ghost.db", "stage1"), [])


class L47PrismaIoPins(unittest.TestCase):
    def test_unable_to_open_maps_503(self):
        import sqlite3
        from fastapi import HTTPException
        # decisions 有 is_file 前置闸——须用真实存在的文件路径。
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "member.db"
            db.write_bytes(b"x" * 64)
            # decisions 经 workflow.connect(read_only=True) 连库——mock 打在
            # workflow.connect 上（round2 修正：首版打 sqlite3.connect 未命中）。
            with mock.patch.object(
                    prisma_mod.workflow, "connect",
                    side_effect=sqlite3.OperationalError(
                        "unable to open database file")):
                with self.assertRaises(HTTPException) as caught:
                    prisma_mod.decisions(db, "decisions")
            self.assertEqual(caught.exception.status_code, 503)
            with mock.patch.object(
                    prisma_mod.workflow, "connect",
                    side_effect=sqlite3.OperationalError("disk I/O error")):
                with self.assertRaises(HTTPException) as caught:
                    prisma_mod.decisions(db, "decisions")
            self.assertEqual(caught.exception.status_code, 503)


class L32FieldTagMalformedPins(unittest.TestCase):
    def test_midword_bracket_degrades_with_warning(self):
        # degraded/warnings 在逐库 strategies 条目上（round2 修正：非顶层）。
        result = strategy_core.translate_pubmed_query('cancer[tiab]x')
        entries = result.get("strategies") or []
        self.assertTrue(entries)
        flagged = [e for e in entries
                   if e.get("database") != "pubmed" and e.get("degraded")
                   and "field_tag_malformed" in json.dumps(e.get("warnings") or [])]
        self.assertTrue(flagged, "转写目标库（eric/wos）须 degraded+field_tag_malformed")
        pubmed = next(e for e in entries if e.get("database") == "pubmed")
        self.assertFalse(pubmed.get("degraded"))  # 原样透传库不降级
        # 对照：合法形态不误报。
        ok = strategy_core.translate_pubmed_query("cancer[tiab] AND review[pt]")
        self.assertFalse(any(
            "field_tag_malformed" in json.dumps(e.get("warnings") or [])
            for e in (ok.get("strategies") or [])))


if __name__ == "__main__":
    unittest.main()
