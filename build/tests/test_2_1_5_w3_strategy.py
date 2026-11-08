# -*- coding: utf-8 -*-
"""2.1.5 Wave G1/W3 · 检索式管理后端钉子（专属新文件，撤实现必红）。

覆盖方案书 W3 红钉清单：
1. translate 纯函数形状钉（PubMed→ERIC/WoS 各 ≥4 例 + 不可译项降级）；
2. preview-counts 仅 PubMed/ERIC 两域 + 单库失败降级（永不 500）；
3. 快照不可变（内容列改写/删除被 SQLite 触发器拒绝；命中数回填是唯一写）；
4. 状态机钉（draft→final→historical 版本链；final/historical 不可删、
   不可回退编辑；revise 开新草稿）；
5. 迁移钉（行数守恒 / 幂等二跑 / 备份存在且校验 / 旧文件未动 / 字段映射）；
6. 权限钉（成员读 200、写 403）；
7. 附录导出与 PRISMA 预填（只读返回、公式注入防护）；
8. 既有 test_w3_living_review_pubmed 零回归由该套件自身保证（本文件不重复）。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
fixture = tempfile.TemporaryDirectory(prefix="rf_215_w3_strategy_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient  # noqa: E402

from coscreen import search_strategy as core  # noqa: E402
from coscreen import tasks as tasks_mod  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend import search_strategy_api  # noqa: E402
from custom_backend.eric_search_api import EricSearchError  # noqa: E402
from custom_backend.main import DATA_DIR, app  # noqa: E402


# ---------------------------------------------------------------------------
# 1. translate 纯函数钉（直接调用，不经 HTTP）
# ---------------------------------------------------------------------------

class TranslatePins(unittest.TestCase):
    def _by_db(self, payload):
        return {entry["database"]: entry for entry in payload["strategies"]}

    def test_four_plus_pubmed_to_eric_wos_shapes(self):
        cases = [
            ('"network meta-analysis"[tiab]',
             '(title:("network meta-analysis") OR description:("network meta-analysis"))',
             'TS=("network meta-analysis")', False),
            ("cancer[ti] AND therapy[ab]",
             "(title:(cancer) AND description:(therapy))",
             "(TI=(cancer) AND AB=(therapy))", False),
            ("Smith[au] AND 2020[dp]",
             "(author:(Smith) AND pubdate:(2020))",
             "(AU=(Smith) AND PY=(2020))", False),
            ("(living review[tiab] OR update[tiab]) AND review[pt]",
             "((((title:(living) OR description:(living)) AND"
             " (title:(review) OR description:(review))) OR"
             " (title:(update) OR description:(update)))"
             " AND publicationtype:(review))",
             "(((TS=(living) AND TS=(review)) OR TS=(update)) AND DT=(review))",
             True),
        ]
        for query, eric, wos, degraded in cases:
            with self.subTest(query=query):
                payload = core.translate_pubmed_query(query)
                by_db = self._by_db(payload)
                self.assertEqual(by_db["pubmed"]["query_text"], query)
                self.assertTrue(by_db["pubmed"]["url"].startswith(
                    "https://pubmed.ncbi.nlm.nih.gov/?term="))
                self.assertEqual(by_db["eric"]["query_text"], eric)
                self.assertEqual(by_db["wos"]["query_text"], wos)
                self.assertEqual(by_db["eric"]["degraded"], degraded)
                self.assertIn("https://eric.ed.gov/?q=", by_db["eric"]["url"])
                self.assertEqual(by_db["wos"]["url"], "")

    def test_pubmed_left_to_right_precedence_is_parenthesized(self):
        """P4：PubMed 同级左结合；输出必须显式括号，不能靠目标库的 AND 优先级。"""
        payload = core.translate_pubmed_query("a[tiab] OR b[tiab] AND c[tiab]")
        by_db = self._by_db(payload)
        self.assertEqual(
            by_db["eric"]["query_text"],
            "(((title:(a) OR description:(a)) OR"
            " (title:(b) OR description:(b))) AND"
            " (title:(c) OR description:(c)))")
        self.assertEqual(by_db["wos"]["query_text"],
                         "((TS=(a) OR TS=(b)) AND TS=(c))")
        self.assertFalse(by_db["eric"]["degraded"])
        # 未加引号的多词 + 字段标签：保留 AND 但显式降级提示短语歧义
        implicit = self._by_db(core.translate_pubmed_query("living review[tiab]"))
        self.assertIn(" AND ", implicit["eric"]["query_text"])
        self.assertTrue(implicit["eric"]["degraded"])
        self.assertIn({"code": "search_strategy.implicit_phrase_ambiguous",
                       "params": {"database": "eric"}},
                      implicit["eric"]["warnings"])
        # NOT 只有二元形态可转写；一元/前导 NOT 在 PubMed/WoS 均非法 → 原样降级
        binary = self._by_db(core.translate_pubmed_query("a[tiab] NOT b[tiab]"))
        self.assertEqual(
            binary["eric"]["query_text"],
            "((title:(a) OR description:(a)) NOT"
            " (title:(b) OR description:(b)))")
        unary = self._by_db(core.translate_pubmed_query("NOT a[tiab]"))
        self.assertTrue(unary["eric"]["degraded"])
        self.assertEqual(unary["eric"]["query_text"], "NOT a[tiab]")
        self.assertEqual(unary["eric"]["warnings"][0]["code"],
                         "search_strategy.query_incomplete")

    def test_trailing_operator_is_incomplete_not_silently_dropped(self):
        """R3-P1：'a[tiab] AND' 必须在解析阶段被判不完整，不能丢运算符。"""
        payload = core.translate_pubmed_query("a[tiab] AND")
        by_db = self._by_db(payload)
        for database in ("eric", "wos"):
            with self.subTest(database=database):
                entry = by_db[database]
                self.assertTrue(entry["degraded"])
                self.assertEqual(entry["query_text"], "a[tiab] AND")
                self.assertEqual(entry["warnings"][0]["code"],
                                 "search_strategy.query_incomplete")
        # 反向（前导运算符）同样不完整
        leading = self._by_db(core.translate_pubmed_query("OR a[tiab]"))
        self.assertTrue(leading["eric"]["degraded"])
        self.assertEqual(leading["eric"]["query_text"], "OR a[tiab]")

    def test_mesh_degrades_on_wos_with_structured_warning(self):
        payload = core.translate_pubmed_query('"Adult"[Mesh]')
        by_db = self._by_db(payload)
        self.assertEqual(by_db["eric"]["query_text"], 'subject:("Adult")')
        self.assertEqual(by_db["wos"]["query_text"], 'TS=("Adult")')
        self.assertTrue(by_db["wos"]["degraded"])
        self.assertIn({"code": "search_strategy.mesh_unmapped",
                       "params": {"database": "wos"}}, by_db["wos"]["warnings"])

    def test_unmappable_field_degrades_to_free_text(self):
        payload = core.translate_pubmed_query("cancer[cp] AND therapy[tiab]")
        by_db = self._by_db(payload)
        self.assertIn("cancer", by_db["eric"]["query_text"])
        self.assertTrue(by_db["eric"]["degraded"])
        self.assertTrue(by_db["wos"]["degraded"])
        codes = {w["code"] for w in payload["warnings"]}
        self.assertEqual(codes, {"search_strategy.field_unmapped"})

    def test_unbalanced_input_returns_original_with_warning(self):
        payload = core.translate_pubmed_query("(unbalanced[tiab]")
        by_db = self._by_db(payload)
        self.assertEqual(by_db["eric"]["query_text"], "(unbalanced[tiab]")
        self.assertTrue(by_db["eric"]["degraded"])
        self.assertEqual(by_db["eric"]["warnings"][0]["code"],
                         "search_strategy.query_unbalanced")

    def test_translate_is_pure_and_pico_generates_five_databases(self):
        payload = core.translate_pico([{
            "key": "population", "value": "adults",
            "synonyms": ["grown-ups"], "mesh_terms": ["Adult"],
        }])
        self.assertEqual([s["database"] for s in payload["strategies"]],
                         ["pubmed", "embase", "cochrane", "eric", "wos"])
        eric = next(s for s in payload["strategies"] if s["database"] == "eric")
        self.assertIn('subject:("Adult")', eric["query_text"])
        wos = next(s for s in payload["strategies"] if s["database"] == "wos")
        self.assertIn('TS=("grown-ups")', wos["query_text"])


# ---------------------------------------------------------------------------
# 2. 存储/状态机/迁移纯函数钉（直接操作 coscreen 模块）
# ---------------------------------------------------------------------------

class StoragePins(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rf_w3_store_")
        self.dir = Path(self.tmp.name)
        self.conn = core.ensure_library(self.dir, migrate_legacy=False)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _draft(self, title="t", database="pubmed", query="q[tiab]"):
        return core.create_strategy(self.conn, {
            "title": title, "database": database, "query_text": query,
            "subscribe": True,
        }, actor="alice")

    def test_snapshot_content_is_immutable_but_counts_are_fillable(self):
        snapshot = core.create_snapshot(self.conn, {
            "pico": [{"key": "population", "value": "adults"}],
            "strategies": [{"database": "pubmed", "query_text": "q[tiab]"}],
            "make_current": True,
        }, actor="alice")
        digest = snapshot["content_sha256"]
        with self.assertRaises(sqlite3.IntegrityError):
            with self.conn:
                self.conn.execute(
                    "UPDATE search_strategy_snapshots SET pico_json = '[]' WHERE id = ?",
                    (snapshot["id"],))
        with self.assertRaises(sqlite3.IntegrityError):
            with self.conn:
                self.conn.execute("DELETE FROM search_strategy_snapshots WHERE id = ?",
                                  (snapshot["id"],))
        filled = core.set_snapshot_counts(self.conn, snapshot["id"],
                                          {"pubmed": 42, "eric": None})
        self.assertEqual(filled["counts"], {"pubmed": 42})
        self.assertEqual(filled["counts_source"], "manual")
        self.assertEqual(filled["content_sha256"], digest)
        # 标记当前不改内容
        core.set_current_snapshot(self.conn, snapshot["id"])
        self.assertTrue(core.get_snapshot(self.conn, snapshot["id"])["is_current"])

    def test_state_machine_version_chain_and_delete_guards(self):
        first = self._draft("v1")
        self.assertEqual(first["status"], "draft")
        edited = core.update_draft(self.conn, first["id"], {"title": "v1 edited"})
        self.assertEqual(edited["title"], "v1 edited")

        finalized = core.finalize_strategy(self.conn, first["id"], {
            "executed_at": "2026-11-01", "hit_count": 10, "hit_source": "manual",
        })
        self.assertEqual(finalized["strategy"]["status"], "final")
        self.assertEqual(finalized["chain"], {"prev_id": None, "version": 1})

        # 定稿不可删、不可编辑、不可回退
        with self.assertRaises(core.SearchStrategyError) as caught:
            core.delete_draft(self.conn, first["id"])
        self.assertEqual(caught.exception.code, "final_not_deletable")
        with self.assertRaises(core.SearchStrategyError) as caught:
            core.update_draft(self.conn, first["id"], {"title": "nope"})
        self.assertEqual(caught.exception.code, "strategy_not_draft")
        with self.assertRaises(sqlite3.IntegrityError):
            with self.conn:
                self.conn.execute("DELETE FROM search_strategies WHERE id = ?",
                                  (first["id"],))

        revised = core.revise_strategy(self.conn, first["id"], actor="alice")
        self.assertEqual(revised["status"], "draft")
        self.assertEqual(revised["version"], 2)
        self.assertEqual(revised["prev_id"], first["id"])

        second = core.finalize_strategy(self.conn, revised["id"], {
            "executed_at": "2026-11-08", "hit_count": 12,
        })
        self.assertEqual(second["strategy"]["status"], "final")
        self.assertEqual(second["strategy"]["version"], 2)
        self.assertEqual(second["strategy"]["prev_id"], first["id"])
        self.assertEqual(second["superseded"], [first["id"]])
        old = core.get_strategy(self.conn, first["id"])
        self.assertEqual(old["status"], "historical")
        # 版本链推进时旧定稿自动退订，订阅视图只留当前 final
        self.assertFalse(old["subscribe"])
        self.assertEqual([row["strategy_id"] for row in core.compat_records(self.conn)],
                         [second["strategy"]["id"]])
        # 历史条目允许手动重新订阅/退订（只影响订阅视图，不改证据）
        self.assertTrue(core.set_subscription(self.conn, first["id"], True)["subscribe"])
        target = core.resolve_check_target(self.conn, query_text="q[tiab]")
        self.assertEqual(target["id"], second["strategy"]["id"],
                         "check-now 回退必须命中当前 final，而非被顶替的历史条目")
        core.set_subscription(self.conn, first["id"], False)
        # 删除仍拒绝
        with self.assertRaises(core.SearchStrategyError) as caught:
            core.delete_draft(self.conn, first["id"])
        self.assertEqual(caught.exception.code, "final_not_deletable")
        # 草稿仍可删
        third = self._draft("scratch")
        core.delete_draft(self.conn, third["id"])
        with self.assertRaises(core.SearchStrategyError):
            core.get_strategy(self.conn, third["id"])

    def test_schema_is_not_rewritten_on_every_open(self):
        """R3-P7：ensure_library 二次打开不得再跑 DDL（读端点零 schema 写）。"""
        before = self.conn.execute("PRAGMA schema_version").fetchone()[0]
        again = core.ensure_library(self.dir, migrate_legacy=False)
        try:
            after = again.execute("PRAGMA schema_version").fetchone()[0]
        finally:
            again.close()
        self.assertEqual(before, after)

    def test_final_note_is_immutable(self):
        draft = self._draft()
        core.finalize_strategy(self.conn, draft["id"], {
            "executed_at": "2026-01-01", "note": "final note",
        })
        with self.assertRaises(sqlite3.IntegrityError):
            with self.conn:
                self.conn.execute(
                    "UPDATE search_strategies SET note = 'x' WHERE id = ?",
                    (draft["id"],))

    def test_final_evidence_is_immutable_against_check_cursor(self):
        """P1/P8：增量检查只推进订阅游标；定稿 executed_at/hit 字段只读。"""
        draft = self._draft()
        finalized = core.finalize_strategy(self.conn, draft["id"], {
            "executed_at": "2026-01-01", "hit_count": 9,
        })["strategy"]
        self.assertEqual(finalized["executed_at"], "2026-01-01")
        core.record_check(self.conn, draft["id"], fetched=3,
                          now="2026-11-08T12:00:00")
        checked = core.get_strategy(self.conn, draft["id"])
        self.assertEqual(checked["executed_at"], "2026-01-01",
                         "check-now 不得改写定稿证据日期")
        self.assertEqual(checked["hit_count"], 9)
        self.assertEqual(checked["last_run_date"], "2026-11-08")
        self.assertEqual(checked["last_count"], 3)
        with self.assertRaises(sqlite3.IntegrityError):
            with self.conn:
                self.conn.execute(
                    "UPDATE search_strategies SET executed_at = '2026-12-31'"
                    " WHERE id = ?", (draft["id"],))
        with self.assertRaises(sqlite3.IntegrityError):
            with self.conn:
                self.conn.execute(
                    "UPDATE search_strategies SET hit_count = 99 WHERE id = ?",
                    (draft["id"],))
        with self.assertRaises(sqlite3.IntegrityError):
            with self.conn:
                self.conn.execute(
                    "UPDATE search_strategies SET version = 99 WHERE id = ?",
                    (draft["id"],))
        with self.assertRaises(sqlite3.IntegrityError):
            with self.conn:
                self.conn.execute(
                    "UPDATE search_strategies SET prev_id = NULL WHERE id = ?",
                    (draft["id"],))

    def test_compat_upsert_reuses_row_and_preserves_evidence(self):
        """P6：迁移后的旧条目再 save-search 不得复制订阅行/改写证据。"""
        legacy = {
            "search_records": [{
                "database": "pubmed", "query_string": "b[tiab]\n",
                "last_run_date": "2024-01-01", "total_hits_last": 7,
                "saved_at": "2024-01-02T00:00:00",
            }],
            "history": [],
        }
        (self.dir / core.LEGACY_FILENAME).write_text(
            json.dumps(legacy), encoding="utf-8")
        core.migrate_legacy_saved_searches(self.dir, self.conn,
                                           now="2026-11-08T00:00:00")
        first = core.list_strategies(self.conn)[0]
        saved = core.compat_upsert(self.conn, database="pubmed",
                                   query_string="b[tiab]",
                                   last_run_date="2025-05-05",
                                   total_hits_last=0)
        rows = core.list_strategies(self.conn)
        self.assertEqual(len(rows), 1, "同库同检索式不得产生第二订阅行")
        self.assertEqual(rows[0]["id"], first["id"])
        self.assertEqual(rows[0]["last_run_date"], "2025-05-05")
        self.assertEqual(rows[0]["executed_at"], "2024-01-01",
                         "兼容层保存不得改写定稿证据日期")
        self.assertEqual(rows[0]["hit_count"], 7)
        self.assertEqual(saved["strategy_id"], first["id"])
        # P7：旧记录尾部空白不阻断 check-now 的精确匹配
        target = core.resolve_check_target(self.conn, query_text="b[tiab]")
        self.assertIsNotNone(target)
        self.assertEqual(target["id"], first["id"])

    def test_snapshot_note_and_strategy_fields_are_read_only(self):
        snapshot = core.create_snapshot(self.conn, {
            "pico": [], "strategies": [{"database": "pubmed",
                                        "query_text": "q[tiab]"}],
        }, actor="alice")
        with self.assertRaises(sqlite3.IntegrityError):
            with self.conn:
                self.conn.execute(
                    "UPDATE search_strategy_snapshots SET note = 'tampered'"
                    " WHERE id = ?", (snapshot["id"],))
        with self.assertRaises(core.SearchStrategyError) as caught:
            core.create_strategy(self.conn, {
                "title": "bad", "database": "pubmed",
                "query_text": "q[tiab]", "snapshot_id": 999999,
            }, actor="alice")
        self.assertEqual(caught.exception.code, "snapshot_not_found")

    def test_duplicate_legacy_rows_are_all_preserved(self):
        """P6 行数守恒：两条完全相同的旧记录也必须各落一行。"""
        record = {"database": "pubmed", "query_string": "dup[tiab]",
                  "last_run_date": "2024-01-01", "total_hits_last": 1,
                  "saved_at": "2024-01-02T00:00:00"}
        (self.dir / core.LEGACY_FILENAME).write_text(json.dumps({
            "search_records": [record, dict(record)], "history": [],
        }), encoding="utf-8")
        result = core.migrate_legacy_saved_searches(
            self.dir, self.conn, now="2026-11-08T00:00:00")
        self.assertEqual((result["rows_before"], result["rows_after"]), (2, 2))
        self.assertEqual(len(core.list_strategies(self.conn)), 2)
        second = core.migrate_legacy_saved_searches(
            self.dir, self.conn, now="2026-11-09T00:00:00")
        self.assertFalse(second["migrated"])
        self.assertEqual(len(core.list_strategies(self.conn)), 2)

    def test_legacy_migration_conserves_rows_and_is_idempotent(self):
        legacy = {
            "search_records": [
                {"database": "pubmed", "query_string": "a[tiab]",
                 "last_run_date": "2024-01-01", "total_hits_last": 5,
                 "saved_at": "2024-01-02T00:00:00"},
                {"database": "manual", "query_string": "b",
                 "last_run_date": "", "total_hits_last": 0,
                 "saved_at": "2024-02-02T00:00:00"},
            ],
            "history": [],
        }
        path = self.dir / core.LEGACY_FILENAME
        path.write_text(json.dumps(legacy), encoding="utf-8")
        before_bytes = path.read_bytes()

        first = core.migrate_legacy_saved_searches(self.dir, self.conn,
                                                   now="2026-11-08T00:00:00")
        self.assertTrue(first["migrated"])
        self.assertEqual((first["rows_before"], first["rows_migrated"],
                          first["rows_after"]), (2, 2, 2))
        rows = core.list_strategies(self.conn)
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["database"] for row in rows}, {"pubmed", "other"})
        self.assertTrue(all(row["status"] == "final" for row in rows))
        self.assertTrue(all(row["source"] == "legacy_subscription" for row in rows))
        self.assertTrue(all(row["subscribe"] for row in rows))
        by_query = {row["query_text"]: row for row in rows}
        self.assertEqual(by_query["a[tiab]"]["hit_count"], 5)
        self.assertEqual(by_query["a[tiab]"]["hit_source"], "manual")
        self.assertEqual(by_query["a[tiab]"]["executed_at"], "2024-01-01")
        self.assertEqual(by_query["a[tiab]"]["last_count"], 5)

        backup = self.dir / (core.LEGACY_FILENAME + core.LEGACY_BACKUP_SUFFIX)
        self.assertTrue(backup.is_file(), "迁移必须留下 .bak 备份")
        self.assertEqual(hashlib.sha256(backup.read_bytes()).hexdigest(),
                         hashlib.sha256(before_bytes).hexdigest(),
                         "备份校验和必须与旧文件一致")
        self.assertEqual(path.read_bytes(), before_bytes, "旧文件必须原样保留")

        second = core.migrate_legacy_saved_searches(self.dir, self.conn,
                                                    now="2026-11-09T00:00:00")
        self.assertFalse(second["migrated"])
        self.assertEqual(len(core.list_strategies(self.conn)), 2,
                         "幂等二跑不得新增行")
        info = core.migration_info(self.conn)
        self.assertEqual(info["rows_before"], 2)
        self.assertEqual(info["rows_migrated"], 2)
        self.assertEqual(info["rows_after"], 2)
        self.assertTrue(info["migrated"])
        self.assertEqual(info["source"], "living_review.json")
        self.assertEqual(info["backup_sha256"], hashlib.sha256(
            before_bytes).hexdigest())


# ---------------------------------------------------------------------------
# 3. HTTP 端点钉（真实 TestClient）
# ---------------------------------------------------------------------------

def _make_user(prefix):
    username = f"{prefix}@example.test"
    user = auth_mod.create_user(username, f"{prefix}-Pass-2026!")
    return user, {"Authorization": "Bearer " +
                  auth_mod.create_access_token(user["id"], user["username"])}


class EndpointPins(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        cls.owner, cls.auth = _make_user("w3strategy-owner")
        cls.member, cls.member_auth = _make_user("w3strategy-member")
        cls.outsider, cls.outsider_auth = _make_user("w3strategy-outsider")

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        response = self.client.post(
            "/api/tasks", json={"name": f"w3-strategy-{self._testMethodName}"},
            headers=self.auth)
        self.assertIn(response.status_code, (200, 201), response.text)
        self.task = response.json()["task_id"]
        self.base = f"/api/tasks/{self.task}/search-strategy"
        invite = self.client.post(
            f"/api/tasks/{self.task}/collaboration/invite",
            json={"username": self.member["username"], "role": "coder"},
            headers=self.auth)
        self.assertEqual(invite.status_code, 201, invite.text)

    def _create(self, **overrides):
        payload = {"title": "主检索式", "database": "pubmed",
                   "query_text": "network meta-analysis[tiab]"}
        payload.update(overrides)
        response = self.client.post(f"{self.base}/strategies",
                                    json=payload, headers=self.auth)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["strategy"]

    # ---- translate / preview -------------------------------------------
    def test_translate_endpoint_and_ambiguity(self):
        response = self.client.post(f"{self.base}/translate", headers=self.auth,
                                    json={"query_text": "cancer[tiab]"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["source"], "query_text")
        response = self.client.post(f"{self.base}/translate", headers=self.auth,
                                    json={"pico": [{"key": "population",
                                                    "value": "adults"}]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["source"], "pico")
        both = self.client.post(f"{self.base}/translate", headers=self.auth,
                                json={"query_text": "x", "pico": []})
        self.assertEqual(both.status_code, 400)
        self.assertEqual(both.json()["detail"]["error_code"], "input_ambiguous")

    def test_preview_counts_only_two_domains_and_degrades(self):
        from unittest import mock

        with mock.patch.object(search_strategy_api, "count_pubmed_hits",
                               return_value=123) as pubmed, \
             mock.patch.object(search_strategy_api, "search_eric_preview",
                               return_value={"total_hits": 7}) as eric:
            response = self.client.post(
                f"{self.base}/preview-counts", headers=self.auth,
                json={"items": [
                    {"database": "pubmed", "query_text": "q1"},
                    {"database": "eric", "query_text": "q2"},
                    {"database": "wos", "query_text": "q3"},
                ]})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["counts"], {"pubmed": 123, "eric": 7, "wos": None})
        self.assertEqual([row["status"] for row in body["results"]],
                         ["counted", "counted", "manual_required"])
        self.assertEqual(body["outbound"],
                         ["eutils.ncbi.nlm.nih.gov", "api.ies.ed.gov"])
        pubmed.assert_called_once()
        eric.assert_called_once()

        with mock.patch.object(
                search_strategy_api, "count_pubmed_hits",
                side_effect=search_strategy_api.PubmedCountError("down")), \
             mock.patch.object(
                search_strategy_api, "search_eric_preview",
                side_effect=EricSearchError("timeout", code="eric_timeout")):
            response = self.client.post(
                f"{self.base}/preview-counts", headers=self.auth,
                json={"items": [
                    {"database": "pubmed", "query_text": "q1"},
                    {"database": "eric", "query_text": "q2"},
                ]})
        self.assertEqual(response.status_code, 200, "单库失败绝不 500")
        results = {row["database"]: row for row in response.json()["results"]}
        self.assertEqual(results["pubmed"]["status"], "unavailable")
        self.assertEqual(results["pubmed"]["error_code"], "pubmed_unavailable")
        self.assertEqual(results["eric"]["status"], "unavailable")
        self.assertEqual(results["eric"]["error_code"], "eric_timeout")

    # ---- 状态机 + 权限 ---------------------------------------------------
    def test_finalize_chain_and_delete_guard_via_http(self):
        first = self._create()
        response = self.client.put(f"{self.base}/strategies/{first['id']}",
                                   headers=self.auth,
                                   json={"note": "edited"})
        self.assertEqual(response.status_code, 200, response.text)
        finalized = self.client.post(
            f"{self.base}/strategies/{first['id']}/finalize",
            headers=self.auth,
            json={"executed_at": "2026-11-01", "hit_count": 10})
        self.assertEqual(finalized.status_code, 200, finalized.text)
        body = finalized.json()
        self.assertEqual(body["strategy"]["status"], "final")
        self.assertEqual(body["chain"], {"prev_id": None, "version": 1})

        deleted = self.client.delete(
            f"{self.base}/strategies/{first['id']}", headers=self.auth)
        self.assertEqual(deleted.status_code, 409)
        self.assertEqual(deleted.json()["detail"]["message_key"],
                         "search_strategy.final_not_deletable")

        revised = self.client.post(
            f"{self.base}/strategies/{first['id']}/revise", headers=self.auth)
        self.assertEqual(revised.status_code, 201, revised.text)
        new_id = revised.json()["strategy"]["id"]
        second = self.client.post(
            f"{self.base}/strategies/{new_id}/finalize", headers=self.auth,
            json={"executed_at": "2026-11-08", "hit_count": 12})
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["strategy"]["version"], 2)
        self.assertEqual(second.json()["strategy"]["prev_id"], first["id"])

        listed = self.client.get(f"{self.base}/strategies",
                                 headers=self.auth).json()
        statuses = {row["id"]: row["status"] for row in listed["strategies"]}
        self.assertEqual(statuses[first["id"]], "historical")
        self.assertEqual(statuses[new_id], "final")

        # 历史条目允许重新订阅（版本链推进时已自动退订），但仍不可删
        subscribe = self.client.put(
            f"{self.base}/strategies/{first['id']}/subscribe",
            headers=self.auth, json={"subscribe": True})
        self.assertEqual(subscribe.status_code, 200, subscribe.text)
        self.assertTrue(subscribe.json()["strategy"]["subscribe"])
        delete_old = self.client.delete(
            f"{self.base}/strategies/{first['id']}", headers=self.auth)
        self.assertEqual(delete_old.status_code, 409)

    def test_member_read_but_write_forbidden(self):
        created = self._create()
        read = self.client.get(f"{self.base}/strategies", headers=self.member_auth)
        self.assertEqual(read.status_code, 200, read.text)
        self.assertFalse(read.json()["can_manage"])
        self.assertEqual(len(read.json()["strategies"]), 1)

        write = self.client.post(f"{self.base}/strategies", headers=self.member_auth,
                                 json={"title": "member", "database": "pubmed",
                                       "query_text": "x[tiab]"})
        self.assertEqual(write.status_code, 403)
        self.assertEqual(write.json()["detail"]["error_code"], "write_forbidden")
        update = self.client.put(
            f"{self.base}/strategies/{created['id']}", headers=self.member_auth,
            json={"title": "hijack"})
        self.assertEqual(update.status_code, 403)
        finalize = self.client.post(
            f"{self.base}/strategies/{created['id']}/finalize",
            headers=self.member_auth, json={})
        self.assertEqual(finalize.status_code, 403)
        subscribe = self.client.put(
            f"{self.base}/strategies/{created['id']}/subscribe",
            headers=self.member_auth, json={"subscribe": True})
        self.assertEqual(subscribe.status_code, 403)

        # P3：兼容层 save-search 与统一库同一写闸（成员 403，不得绕过）
        compat_write = self.client.post(
            f"/api/tasks/{self.task}/living-review/save-search",
            headers=self.member_auth,
            json={"database": "pubmed", "query_string": "member[tiab]",
                  "last_run_date": "", "total_hits_last": 0})
        self.assertEqual(compat_write.status_code, 403, compat_write.text)
        self.assertEqual(compat_write.json()["detail"]["error_code"],
                         "write_forbidden")

        owner_read = self.client.get(f"{self.base}/strategies", headers=self.auth)
        self.assertTrue(owner_read.json()["can_manage"])

        # P9：非参与者在进入端点前由 require_user 拒绝，但 W3 路径错误体结构化
        outsider = self.client.get(f"{self.base}/strategies",
                                   headers=self.outsider_auth)
        self.assertEqual(outsider.status_code, 403, outsider.text)
        self.assertEqual(outsider.json()["detail"]["error_code"],
                         "task_forbidden")
        self.assertEqual(outsider.json()["detail"]["message_key"],
                         "search_strategy.task_forbidden")

    # ---- 快照 / 导出 / 预填 ---------------------------------------------
    def test_snapshot_round_trip_manual_count_and_to_strategy(self):
        created = self.client.post(f"{self.base}/snapshots", headers=self.auth, json={
            "pico": [{"key": "population", "value": "adults"}],
            "strategies": [
                {"database": "pubmed", "query_text": "a[tiab]"},
                {"database": "eric", "query_text": 'title:("a")'},
            ],
            "make_current": True,
        })
        self.assertEqual(created.status_code, 201, created.text)
        snapshot = created.json()["snapshot"]
        self.assertTrue(snapshot["is_current"])
        self.assertTrue(snapshot["content_sha256"])

        filled = self.client.put(
            f"{self.base}/snapshots/{snapshot['id']}/manual-count",
            headers=self.auth, json={"counts": {"pubmed": 99},
                                     "source": "hax"})
        self.assertEqual(filled.status_code, 200, filled.text)
        self.assertEqual(filled.json()["snapshot"]["counts"], {"pubmed": 99})
        self.assertEqual(filled.json()["snapshot"]["counts_source"], "manual",
                         "manual-count 的 counts_source 恒为 manual")
        self.assertEqual(filled.json()["snapshot"]["content_sha256"],
                         snapshot["content_sha256"])

        # 内容改写没有端点：快照列表只读；直接 SQL 改写由 StoragePins 覆盖。
        made = self.client.post(
            f"{self.base}/snapshots/{snapshot['id']}/to-strategy",
            headers=self.auth, json={"databases": ["pubmed"], "subscribe": True})
        self.assertEqual(made.status_code, 201, made.text)
        strategy = made.json()["strategies"][0]
        self.assertEqual(strategy["source"], "builder")
        self.assertEqual(strategy["snapshot_id"], snapshot["id"])
        self.assertTrue(strategy["subscribe"])

        bad = self.client.post(
            f"{self.base}/snapshots/{snapshot['id']}/to-strategy",
            headers=self.auth, json={"databases": ["wos"]})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.json()["detail"]["error_code"], "databases_invalid")

        missing = self.client.put(
            f"{self.base}/snapshots/999999/current", headers=self.auth)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["detail"]["error_code"],
                         "snapshot_not_found")

    def test_appendix_and_prisma_prefill(self):
        first = self._create(query_text='=1+1[tiab]')
        self.client.post(f"{self.base}/strategies/{first['id']}/finalize",
                         headers=self.auth,
                         json={"executed_at": "2026-11-01", "hit_count": 5})
        text = self.client.get(f"{self.base}/appendix", headers=self.auth)
        self.assertEqual(text.status_code, 200, text.text)
        self.assertIn("=1+1", text.text)
        self.assertIn("命中数：5", text.text)
        csv_body = self.client.get(f"{self.base}/appendix",
                                   params={"format": "csv"}, headers=self.auth)
        self.assertEqual(csv_body.status_code, 200)
        self.assertIn("'=1+1", csv_body.text, "CSV 公式注入必须前置单引号")
        bad_format = self.client.get(f"{self.base}/appendix",
                                     params={"format": "pdf"}, headers=self.auth)
        self.assertEqual(bad_format.status_code, 400)

        prefill = self.client.post(f"{self.base}/prisma-prefill",
                                   headers=self.auth, json={})
        self.assertEqual(prefill.status_code, 200, prefill.text)
        body = prefill.json()
        self.assertEqual(body["filled"], 1)
        self.assertEqual(body["rows"][0]["source_type"], "database")
        self.assertEqual(body["rows"][0]["name"], "PubMed")
        self.assertEqual(body["rows"][0]["n"], 5)

    def test_storage_path_escape_is_503(self):
        """R4：库路径越出任务目录时与其它 storage_unavailable 同码（503）。"""
        from unittest import mock

        outside = Path(tempfile.gettempdir()) / "w3-outside-strategy.sqlite3"
        with mock.patch.object(search_strategy_api.core, "store_path",
                               return_value=outside):
            response = self.client.get(f"{self.base}/strategies",
                                       headers=self.auth)
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["detail"]["error_code"],
                         "storage_unavailable")

    def test_storage_unavailable_is_always_503(self):
        """R3-P5：storage_unavailable 统一 503（不再 400/409 混用）。"""
        from unittest import mock

        with mock.patch.object(
                search_strategy_api.core, "ensure_library",
                side_effect=core.SearchStrategyError("storage_unavailable")):
            response = self.client.get(f"{self.base}/strategies",
                                       headers=self.auth)
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["detail"]["error_code"],
                         "storage_unavailable")
        self.assertEqual(response.json()["detail"]["message_key"],
                         "search_strategy.storage_unavailable")

    def test_empty_library_appendix_is_structured_409(self):
        response = self.client.get(f"{self.base}/appendix", headers=self.auth)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["error_code"],
                         "no_final_strategies")

    def test_check_now_uses_subscription_cursor_not_evidence_date(self):
        """P1 配套：无 since_date 时取订阅游标 last_run_date，而非定稿 executed_at。"""
        from unittest import mock

        from custom_backend import living_review_plugin as lr_plugin

        self.client.post(
            f"/api/tasks/{self.task}/living-review/save-search",
            headers=self.auth,
            json={"database": "pubmed", "query_string": "cursor[tiab]",
                  "last_run_date": "2024-05-05", "total_hits_last": 0})
        with mock.patch.object(lr_plugin, "fetch_pubmed_updates",
                               return_value=[]) as fetch:
            response = self.client.post(
                f"/api/tasks/{self.task}/living-review/check-now",
                headers=self.auth, json={})
        self.assertEqual(response.status_code, 200, response.text)
        fetch.assert_called_once_with("cursor[tiab]", "2024-05-05")
        self.assertEqual(response.json()["since_date"], "2024-05-05")

    def test_check_now_old_path_matches_pubmed_only(self):
        """R3-P2：同文本的 embase 条目不得抢占旧客户端的 PubMed 检查。"""
        from unittest import mock

        from custom_backend import living_review_plugin as lr_plugin

        embase = self._create(database="embase", query_text="same[tiab]")
        self.client.post(f"{self.base}/strategies/{embase['id']}/finalize",
                         headers=self.auth,
                         json={"executed_at": "2026-01-01"})
        self.client.post(
            f"/api/tasks/{self.task}/living-review/save-search",
            headers=self.auth,
            json={"database": "pubmed", "query_string": "same[tiab]",
                  "last_run_date": "2024-06-06", "total_hits_last": 0})
        with mock.patch.object(lr_plugin, "fetch_pubmed_updates",
                               return_value=[]) as fetch:
            response = self.client.post(
                f"/api/tasks/{self.task}/living-review/check-now",
                headers=self.auth, json={"query_string": "same[tiab]"})
        self.assertEqual(response.status_code, 200, response.text)
        fetch.assert_called_once_with("same[tiab]", "2024-06-06")
        self.assertEqual(response.json()["source"], "pubmed")

    def test_check_now_dispatches_by_library_database(self):
        """本波仅 PubMed：ERIC 条目 409（W4），未知 id 404，均零出站。"""
        from unittest import mock

        eric = self._create(database="eric", query_text='title:("adults")')
        from custom_backend import living_review_plugin as lr_plugin

        with mock.patch.object(lr_plugin, "fetch_pubmed_updates") as fetch:
            response = self.client.post(
                f"/api/tasks/{self.task}/living-review/check-now",
                headers=self.auth, json={"strategy_id": eric["id"]})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["message_key"],
                         "living_review.eric_incremental_pending")
        fetch.assert_not_called()

        missing = self.client.post(
            f"/api/tasks/{self.task}/living-review/check-now",
            headers=self.auth, json={"strategy_id": 999999})
        self.assertEqual(missing.status_code, 404, missing.text)
        self.assertEqual(missing.json()["detail"]["message_key"],
                         "search_strategy.strategy_not_found")

    # ---- 迁移经 HTTP 触发 ------------------------------------------------
    def test_migration_runs_on_first_endpoint_access(self):
        info = tasks_mod.find_task(self.task, DATA_DIR)
        self.assertIsNotNone(info)
        legacy = {
            "search_records": [
                {"database": "pubmed", "query_string": "legacy[tiab]",
                 "last_run_date": "2024-03-01", "total_hits_last": 8,
                 "saved_at": "2024-03-02T00:00:00"},
            ],
            "history": [{"when": "2024-03-02", "source": "pubmed"}],
        }
        legacy_path = Path(info.dir) / core.LEGACY_FILENAME
        legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
        before = legacy_path.read_bytes()

        listed = self.client.get(f"{self.base}/strategies", headers=self.auth)
        self.assertEqual(listed.status_code, 200, listed.text)
        body = listed.json()
        self.assertEqual(body["counts"]["total"], 1)
        self.assertEqual(body["strategies"][0]["query_text"], "legacy[tiab]")
        self.assertEqual(body["strategies"][0]["source"], "legacy_subscription")
        self.assertEqual(body["migration"]["rows_before"], 1)
        self.assertEqual(body["migration"]["rows_migrated"], 1)
        self.assertEqual(body["migration"]["rows_after"], 1)

        compat = self.client.get(
            f"/api/tasks/{self.task}/living-review/search-records",
            headers=self.auth).json()
        self.assertEqual(len(compat["search_records"]), 1)
        record = compat["search_records"][0]
        self.assertEqual(record["database"], "pubmed")
        self.assertEqual(record["query_string"], "legacy[tiab]")
        self.assertEqual(record["last_run_date"], "2024-03-01")
        self.assertEqual(record["total_hits_last"], 8)

        # 二跑不新增；旧文件未动；备份存在
        again = self.client.get(f"{self.base}/strategies", headers=self.auth)
        self.assertEqual(len(again.json()["strategies"]), 1)
        self.assertEqual(legacy_path.read_bytes(), before)
        backup = Path(info.dir) / (core.LEGACY_FILENAME
                                   + core.LEGACY_BACKUP_SUFFIX)
        self.assertTrue(backup.is_file())
        self.assertEqual(backup.read_bytes(), before)

        # 活体综述 save-search 兼容层写入同一库
        saved = self.client.post(
            f"/api/tasks/{self.task}/living-review/save-search",
            headers=self.auth,
            json={"database": "pubmed", "query_string": "new[tiab]",
                  "last_run_date": "2025-01-01", "total_hits_last": 0})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(len(saved.json()["search_records"]), 2)


if __name__ == "__main__":
    unittest.main()
