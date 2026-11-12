# -*- coding: utf-8 -*-
"""review67 M-14 · RoB 存储/合同全状态机常驻测试（探针 probe_rob.py 转正）。

coscreen.rob_storage + risk_of_bias_contract（连同共享 RoB API）自初始发布
以来零测试引用；本文件把泳道已验证的探针（create→revise→冲突→共识→chart，
10/10）转为常驻回归，并钉同批 L 项：

- L-39：``revise_assessment`` 的 ``changes.assessed_at`` 必须是可解析的
  ISO-8601 时间戳（``not-a-date`` 此前原样写入记录与修订台账）。
- L-40：``_required_text`` 去死条件三元后行为不变（strip 两种口径）。
- L-41：``_consensus_inputs`` 删无操作 ``except RobContractError: raise``
  包装后，consensus_set_id 校验仍按 invalid_field 上抛。
"""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen import analysis_storage, rob_storage  # noqa: E402
from coscreen.db import _SCHEMA as CORE_SCHEMA  # noqa: E402
from coscreen.risk_of_bias_contract import (  # noqa: E402
    RobContractError, _required_text, build_chart_data, create_assessment,
    from_legacy_row, mapping_status, revise_assessment,
)

VARIANT = "individually_randomized_parallel_group_assignment_effect"


def _payload(version: int = 1, overall: str = "low") -> dict:
    return {
        "framework": "RoB 2", "tool_version": "2019-08-22", "variant": VARIANT,
        "study_id": "S1", "comparison": "A vs B", "outcome": "Pain",
        "timepoint": "Week 12",
        "source": {"report_id": "Z1", "locator": "p3"},
        "result_links": [{"result_id": RID_HOLDER["rid"],
                          "effect_version_id": str(version)}],
        "domains": {key: {"judgement": "low", "rationale": "r"} for key in (
            "randomization process", "deviations from intended interventions",
            "missing outcome data", "measurement of outcome",
            "selection of reported result")},
        "overall": {"judgement": overall, "rationale": "ok"},
    }


RID_HOLDER: dict = {"rid": 1}  # setUpClass 里以真实 result_id 回填


class RobStateMachineTests(unittest.TestCase):
    """顺序状态机（setUpClass 建链）+ 逐断言测试方法。"""

    @classmethod
    def setUpClass(cls):
        cls._dir = tempfile.TemporaryDirectory(prefix="r67rob_")
        cls.conn = sqlite3.connect(Path(cls._dir.name) / "rob.db")
        conn = cls.conn
        conn.executescript(CORE_SCHEMA)
        analysis_storage.ensure_analysis_storage(conn)
        rob_storage.ensure_rob_storage(conn)

        conn.execute("INSERT INTO review_studies(id,label) VALUES ('S1','Study One')")
        conn.execute("INSERT INTO articles(zotero_key) VALUES ('Z1')")
        conn.execute("INSERT INTO review_reports(zotero_key,study_id) VALUES ('Z1','S1')")
        conn.execute(
            "INSERT INTO review_effects(study_id,comparison,outcome,timepoint,measure,"
            "estimate,se,source_key,input_json,entry_method,selected,source_locator) "
            "VALUES ('S1','A vs B','Pain','Week 12','SMD',0.5,0.1,'Z1','{}','manual',1,'')")
        # ensure_analysis_storage 自动回填版本 1；再补第二版本（estimate*2）。
        if conn.execute(
                "SELECT 1 FROM review_effect_versions "
                "WHERE result_id=(SELECT MAX(result_id) FROM review_effects)"
        ).fetchone() is None:
            conn.execute(
                "INSERT INTO review_effect_versions(result_id,revision,study_id,"
                "comparison,outcome,timepoint,measure,estimate,se,source_key,"
                "input_json,entry_method,selected,source_locator,changed_at) "
                "SELECT result_id,1,study_id,comparison,outcome,timepoint,measure,"
                "estimate,se,source_key,input_json,entry_method,selected,"
                "source_locator,'2026-01-01' FROM review_effects")
        conn.execute(
            "INSERT INTO review_effect_versions(result_id,revision,study_id,"
            "comparison,outcome,timepoint,measure,estimate,se,source_key,input_json,"
            "entry_method,selected,source_locator,changed_at) "
            "SELECT result_id,(SELECT MAX(revision)+1 FROM review_effect_versions "
            "WHERE result_id=review_effects.result_id),study_id,comparison,outcome,"
            "timepoint,measure,estimate*2,se,source_key,input_json,entry_method,"
            "selected,source_locator,'2026-02-01' FROM review_effects")
        conn.commit()
        RID_HOLDER["rid"] = conn.execute(
            "SELECT result_id FROM review_effects").fetchone()[0]
        cls.rid = RID_HOLDER["rid"]

        # --- 1-4：alice 建档 → 修订（rev 2 overall high）→ 历史 ---
        cls.alice = rob_storage.save_record(
            conn, _payload(), actor_id="alice", expected_revision=0)
        cls.alice_rev2 = rob_storage.save_record(
            conn, {"overall": {"judgement": "high", "rationale": "worse"}},
            actor_id="alice", record_id=cls.alice["record_id"], expected_revision=1)
        # --- bob 建档 ---
        cls.bob = rob_storage.save_record(
            conn, _payload(overall="high"), actor_id="bob", expected_revision=0)
        # --- carol 调和共识（rev 1）---
        cls.consensus = cls._save_consensus(overall="some concerns")
        # --- alice 再修订（rev 3）→ 共识吸收新源快照（rev 2）---
        rob_storage.save_record(
            conn, {"overall": {"judgement": "low", "rationale": "same"}},
            actor_id="alice", record_id=cls.alice["record_id"], expected_revision=2)
        cls.consensus_rev2 = cls._save_consensus(
            overall="high", record_id=cls.consensus["record_id"],
            expected_revision=1)
        # --- legacy 行 + chart 夹具 ---
        cls.legacy_row = from_legacy_row({
            "study_id": "S1", "comparison": "", "outcome": "Pain",
            "timepoint": "Week 12", "framework": "RoB 2", "reviewer": "alice",
            "domains_json": "{}", "overall": "high", "overall_rationale": "",
            "source_key": "Z1", "source_locator": "p1",
            "updated_at": "2025-01-01"})
        cls.effects = [dict(zip(
            ("result_id", "effect_version_id", "study_id", "comparison",
             "outcome", "timepoint"),
            conn.execute(
                "SELECT result_id,effect_version_id,study_id,comparison,outcome,"
                "timepoint FROM review_effect_versions "
                "WHERE result_id=? AND effect_version_id=1",
                (cls.rid,)).fetchone()))]

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls._dir.cleanup()

    # ---- 夹具辅助 -------------------------------------------------------
    @classmethod
    def _fresh(cls, record_id: str) -> dict:
        return json.loads(cls.conn.execute(
            "SELECT record_json FROM rob_assessments WHERE record_id=?",
            (record_id,)).fetchone()[0])

    @classmethod
    def _save_consensus(cls, *, overall: str, record_id: str | None = None,
                        expected_revision: int | None = 0,
                        owners: dict | None = None) -> dict:
        body = _payload(overall=overall)
        body["record_kind"] = "consensus"
        body["consensus_rationale"] = "both assessors agree"
        body["consensus_set_id"] = "CS-1"
        alice_now, bob_now = cls._fresh(cls.alice["record_id"]), cls._fresh(cls.bob["record_id"])
        owner_map = owners if owners is not None else {
            cls.alice["record_id"]: "alice", cls.bob["record_id"]: "bob"}
        return rob_storage.save_consensus(
            cls.conn, body,
            source_record_ids=[cls.alice["record_id"], cls.bob["record_id"]],
            source_records=[alice_now, bob_now],
            authenticated_source_owners=owner_map,
            reconciler_id="carol", expected_revision=expected_revision,
            record_id=record_id)

    # ---- 建档/修订/链接 -------------------------------------------------
    def test_create_independent_normalizes_links_and_history(self):
        rec = self.alice
        self.assertEqual((rec["revision"], rec["record_kind"]), (1, "independent"))
        link = rec["result_links"][0]
        self.assertEqual(link["result_id"], self.rid)
        self.assertIsInstance(link["result_id"], int)
        self.assertEqual(link["effect_version_id"], "1")
        self.assertEqual(
            [r["revision"] for r in rob_storage.record_history(
                self.conn, rec["record_id"])], [1, 2, 3])
        self.assertEqual(self.alice_rev2["overall"]["judgement"], "high")

    def test_links_table_tracks_current_only_revision_links_keep_all(self):
        record_id = self.alice["record_id"]
        links = self.conn.execute(
            "SELECT result_id, effect_version_id FROM rob_assessment_links "
            "WHERE record_id=?", (record_id,)).fetchall()
        revisions = self.conn.execute(
            "SELECT revision FROM rob_assessment_revision_links WHERE record_id=?",
            (record_id,)).fetchall()
        self.assertEqual(len(links), 1)
        self.assertEqual([row[0] for row in revisions], [1, 2, 3])

    def test_stale_expected_revision_rejected(self):
        with self.assertRaises(RobContractError) as caught:
            rob_storage.save_record(
                self.conn, {"overall": {"judgement": "low", "rationale": "x"}},
                actor_id="alice", record_id=self.alice["record_id"],
                expected_revision=1)
        self.assertEqual(caught.exception.code, "revision_conflict")

    def test_cross_reviewer_edit_rejected(self):
        with self.assertRaises(RobContractError) as caught:
            rob_storage.save_record(
                self.conn, {}, actor_id="bob",
                record_id=self.alice["record_id"], expected_revision=3)
        self.assertEqual(caught.exception.code, "reviewer_forbidden")

    def test_unlinked_source_report_rejected(self):
        payload = _payload()
        payload["source"]["report_id"] = "Z999"
        with self.assertRaises(RobContractError) as caught:
            rob_storage.save_record(self.conn, payload, actor_id="alice2",
                                    expected_revision=0)
        self.assertEqual(caught.exception.code, "source_unlinked")

    def test_missing_effect_version_rejected(self):
        with self.assertRaises(RobContractError) as caught:
            rob_storage.save_record(self.conn, _payload(version=99),
                                    actor_id="bob2", expected_revision=0)
        self.assertEqual(caught.exception.code, "result_version_missing")

    # ---- 共识 -----------------------------------------------------------
    def test_consensus_snapshot_semantics(self):
        crec = self.consensus
        self.assertEqual((crec["record_kind"], crec["revision"]), ("consensus", 1))
        self.assertEqual(crec["reconciled_by"], "carol")
        self.assertEqual(len(crec["source_assessments"]), 2)
        self.assertEqual(self.consensus_rev2["revision"], 2)

    def test_host_identity_fields_forbidden_in_consensus(self):
        body = _payload(overall="high")
        body["record_kind"] = "consensus"
        body["consensus_rationale"] = "both assessors agree"
        body["consensus_set_id"] = "CS-1"
        body["record_id"] = "fake"
        with self.assertRaises(RobContractError) as caught:
            rob_storage.save_consensus(
                self.conn, body,
                source_record_ids=[self.alice["record_id"], self.bob["record_id"]],
                source_records=[self._fresh(self.alice["record_id"]),
                                self._fresh(self.bob["record_id"])],
                authenticated_source_owners={self.alice["record_id"]: "alice",
                                             self.bob["record_id"]: "bob"},
                reconciler_id="carol", expected_revision=0)
        self.assertEqual(caught.exception.code, "host_fields_forbidden")

    def test_forged_owner_identity_rejected(self):
        with self.assertRaises(RobContractError) as caught:
            self._save_consensus(
                overall="high",
                owners={self.alice["record_id"]: "mallory",
                        self.bob["record_id"]: "bob"})
        self.assertEqual(caught.exception.code, "consensus_source_owner_mismatch")

    def test_consensus_source_revision_conflict_rejected(self):
        """同源同修订号但内容被改 → 不得静默顶替（probe 第 12 项）。"""
        stale = self._fresh(self.alice["record_id"])
        stale["domains"]["missing outcome data"] = {
            "status": "recorded", "judgement": "some concerns",
            "rationale": "changed"}
        body = _payload(overall="high")
        body["record_kind"] = "consensus"
        body["consensus_rationale"] = "both assessors agree"
        body["consensus_set_id"] = "CS-1"
        with self.assertRaises(RobContractError) as caught:
            rob_storage.save_consensus(
                self.conn, body,
                source_record_ids=[self.alice["record_id"], self.bob["record_id"]],
                source_records=[stale, self._fresh(self.bob["record_id"])],
                authenticated_source_owners={self.alice["record_id"]: "alice",
                                             self.bob["record_id"]: "bob"},
                reconciler_id="carol",
                record_id=self.consensus["record_id"], expected_revision=2)
        self.assertEqual(caught.exception.code, "consensus_source_revision_conflict")

    def test_empty_consensus_set_id_still_invalid_field(self):
        """review67 L-41 伴钉：删无操作重抛包装后校验照常生效。"""
        body = _payload(overall="high")
        body["record_kind"] = "consensus"
        body["consensus_rationale"] = "both assessors agree"
        body["consensus_set_id"] = "   "
        with self.assertRaises(RobContractError) as caught:
            rob_storage.save_consensus(
                self.conn, body,
                source_record_ids=[self.alice["record_id"], self.bob["record_id"]],
                source_records=[self._fresh(self.alice["record_id"]),
                                self._fresh(self.bob["record_id"])],
                authenticated_source_owners={self.alice["record_id"]: "alice",
                                             self.bob["record_id"]: "bob"},
                reconciler_id="carol", expected_revision=0)
        self.assertEqual(caught.exception.code, "invalid_field")
        self.assertEqual(caught.exception.details.get("field"), "consensus_set_id")

    # ---- chart ----------------------------------------------------------
    def test_chart_rows_pending_and_robvis(self):
        records = rob_storage.list_records(self.conn) + [self.legacy_row]
        chart = build_chart_data(
            records, self.effects, framework="RoB 2", tool_version="2019-08-22",
            variant=VARIANT,
            selector={"kind": "reviewer", "reviewer_id": "alice"},
            result_refs=[{"result_id": self.rid, "effect_version_id": 1}],
            statistical_unit="assessment_record")
        # alice 当前记录（rev 3，overall low）入选；legacy 行挂 pending。
        self.assertEqual(len(chart["rows"]), 1)
        self.assertEqual(chart["rows"][0]["reviewer_id"], "alice")
        self.assertTrue(any(p["reason"] == "legacy_result_unbound"
                            for p in chart["pending_reconciliation"]))
        self.assertEqual(chart["distributions"]["overall"]["counts"].get("low"), 1)
        self.assertEqual(chart["robvis_interchange"]["risk_of_bias"]["tool"], "ROB2")

    def test_study_unit_with_single_consensus_row_resolves(self):
        chart = build_chart_data(
            rob_storage.list_records(self.conn), self.effects,
            framework="RoB 2", tool_version="2019-08-22", variant=VARIANT,
            selector={"kind": "consensus", "consensus_set_id": "CS-1"},
            result_refs=[{"result_id": self.rid, "effect_version_id": 1}],
            statistical_unit="study")
        self.assertEqual(len(chart["rows"]), 1)

    def test_mapping_status_surface(self):
        self.assertEqual(mapping_status("RoB 2", "2019-08-22", "bogus"),
                         "unsupported_variant")
        self.assertEqual(mapping_status("RoB 2", "unknown", None),
                         "unknown_tool_version")
        self.assertEqual(mapping_status("CASP", "2024", "x"),
                         "unsupported_framework")

    # ---- review67 L-39：assessed_at 格式校验 -----------------------------
    def test_revise_rejects_non_iso_assessed_at(self):
        current = self._fresh(self.alice["record_id"])
        with self.assertRaises(RobContractError) as caught:
            revise_assessment(current, {"assessed_at": "not-a-date"},
                              expected_revision=current["revision"],
                              actor_id="alice")
        self.assertEqual(caught.exception.code, "invalid_field")
        self.assertEqual(caught.exception.details.get("field"), "assessed_at")

    def test_revise_rejects_non_string_assessed_at(self):
        current = self._fresh(self.alice["record_id"])
        with self.assertRaises(RobContractError) as caught:
            revise_assessment(current, {"assessed_at": 12345},
                              expected_revision=current["revision"],
                              actor_id="alice")
        self.assertEqual(caught.exception.code, "invalid_field")

    def test_revise_accepts_iso_assessed_at_and_syncs_updated_at(self):
        current = self._fresh(self.alice["record_id"])
        revised = revise_assessment(
            current, {"assessed_at": "2026-11-12T10:00:00+08:00"},
            expected_revision=current["revision"], actor_id="alice")
        self.assertEqual(revised["assessed_at"], "2026-11-12T10:00:00+08:00")
        self.assertEqual(revised["updated_at"], "2026-11-12T10:00:00+08:00")

    def test_save_record_route_validates_assessed_at(self):
        """rob_storage.save_record 把用户 body 原样作为 changes——同样受校验。"""
        with self.assertRaises(RobContractError) as caught:
            rob_storage.save_record(
                self.conn, {"assessed_at": "yesterday-ish"},
                actor_id="alice", record_id=self.alice["record_id"],
                expected_revision=3)
        self.assertEqual(caught.exception.code, "invalid_field")
        self.assertEqual(caught.exception.details.get("field"), "assessed_at")

    # ---- review67 L-40：_required_text 行为保持 --------------------------
    def test_required_text_strips_in_both_modes(self):
        self.assertEqual(_required_text("  ", "f", allow_empty=True), "")
        self.assertEqual(_required_text(" x ", "f"), "x")
        with self.assertRaises(RobContractError):
            _required_text("   ", "f")


if __name__ == "__main__":
    unittest.main()
