"""P2 退休保留集与恢复矩阵（2026-11-21 用户裁定 R1/R2/R3/R6 落地验证）。

设计依据：E:/rf216-store-scan-20261120/GAP_DESIGN_SURVEY.md §4 C 矩阵（S1-S9）、
§5.3、§6 R1-R3/R6；交叉 E:/rf216-gap-survey-egg-20261121/GAP_DESIGN_SURVEY.md
块 C.1 与 D.3-2（空归档论证）。

矩阵（修后预期，红=主仓现行工作树、绿=箱内 candidate）：
- S4 关筛 reconciler 移除 → 200 归档（修前 503 retirement_source_unavailable）；
- S5 双 grant 冲突成员移除 → 200 归档（修前同 503）；
- S6 active∧库缺失 → 空归档放行＋watermark 指向空枚举证据（egg D.3-2；
  修前永久 503）；
- S7/S8 成员库 F1/receipts 缺表 → 锁外幂等准备前置治愈 → 200 归档
  （修前永久 503）；
- S9 库损坏（schema 失配）→ typed retirement_source_unverifiable +
  recovery 指引（修前裸异常）→ force=True 强制移除（零伪造证据+审计）；
- R1 持未决票成员移除 → receipt.result.pending_keys_at_removal 审计；
- R2 移除后重邀 → 按身份核对恢复被隔离旧库（.revoked-*），失败给指引；
- ensure_member_source_registered：幂等 + 库缺失降级 unavailable 不抛；
- R6 冲突 seal 修复入口：选权威 seal 归档进 collab + losers 审计。

解释器：validation-venv312 `python -I -B`（bare launcher 复刻 S2B 先例：
sys.path.insert(0, candidate_root) + importlib 载入 + TextTestRunner）。
候选根选择：环境变量 ``RF216_P2_CANDIDATE_ROOT``（红=主仓、绿=箱根）。
环境白名单：COBOOKSHELF_DATA_DIR + TEMP/TMP + REVIEWFLOW_MODE=server +
RF216_P2_CANDIDATE_ROOT（launcher 预建箱内目录）。零 mock——真实
WorkflowRuntime/真实 P10 写窗/真实 collab 组合库。
"""
from __future__ import annotations

import os
import sys

_CANDIDATE_ROOT = os.environ.get("RF216_P2_CANDIDATE_ROOT")
if _CANDIDATE_ROOT:
    sys.path.insert(0, _CANDIDATE_ROOT)

import hashlib
import importlib
import json
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

TASK_ID = "p2retire-task"
INSTANCE_ID = "p2retire-inst-1"
OWNER, BOB, CAROL = 1, 2, 3  # 真实 uid 由 auth 分配后覆写
ACCOUNT_NAMES = ("owner", "bob", "carol")
PASSWORD = "p2-retire-pass-1"
USERS = {}
_SEALED_AT_A = "2026-11-20T08:00:00.000Z"
_SEALED_AT_B = "2026-11-20T09:00:00.000Z"


def _canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _restore_env(key, previous):
    if previous is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = previous


class P2RetirementMatrixBase(unittest.TestCase):
    """真实 WorkflowRuntime ＋ 真实 P10 写窗基座（零 mock；P10 先例复刻）。"""

    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.auth = importlib.import_module("custom_backend.auth")
        cls.collab = importlib.import_module("coscreen.task_collaboration")
        cls.dbm = importlib.import_module("coscreen.db")
        cls.wf_mod = importlib.import_module("coscreen.review_workflow")
        cls.tasks = importlib.import_module("coscreen.tasks")
        cls.ledger = importlib.import_module("coscreen.team_vote_ledger")
        cls.facts = importlib.import_module("coscreen.team_terminal_facts")
        cls.registry = importlib.import_module(
            "coscreen.team_terminal_registry")
        cls.receipts = importlib.import_module("coscreen.team_flow_receipts")
        cls.flow_types = importlib.import_module("coscreen.team_flow_types")
        cls.lifecycle = importlib.import_module(
            "custom_backend.team_source_lifecycle")
        write_api = importlib.import_module(
            "custom_backend.review_workflow_api")
        cls.WorkflowRuntime = write_api.WorkflowRuntime

        users_root = Path(os.environ["COBOOKSHELF_DATA_DIR"]) / (
            "p2users-%s" % uuid.uuid4().hex[:12])
        users_root.mkdir(parents=True, exist_ok=True)
        cls.addClassCleanup(_restore_env, "COBOOKSHELF_DATA_DIR",
                            os.environ.get("COBOOKSHELF_DATA_DIR"))
        os.environ["COBOOKSHELF_DATA_DIR"] = str(users_root)
        cls.users_root = users_root
        for name in ACCOUNT_NAMES:
            created = cls.auth.create_user(name, PASSWORD)
            USERS[name] = int(created["id"])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rf216_p2_case_")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)

    # -- 夹具（真实产品 API） ------------------------------------------------

    def _make_task(self):
        task_dir = self.base / "task"
        task_dir.mkdir()
        self.task_dir = task_dir
        self.info = SimpleNamespace(task_id=TASK_ID, dir=task_dir,
                                    degraded=False)
        self.collab_path = task_dir / "collaboration.db"
        self.collab.initialize_task_store(
            task_dir, task_id=TASK_ID, owner_user_id=USERS["owner"],
            owner_username="owner")
        self.collab.set_business_role(
            self.collab_path, actor_user_id=USERS["owner"],
            user_id=USERS["owner"], business_role="reconciler",
            task_id=TASK_ID)
        self.user_paths = {}
        for name in ACCOUNT_NAMES:
            self.user_paths[name] = self._full_member_db(task_dir, name)
        self.lifecycle.prepare_team_protocol_storage(
            collab_path=self.collab_path, source_paths=())
        writer = self._borrowed_writer(self.collab_path)
        try:
            self.registry.register_protocol_on(
                writer, task_id=TASK_ID, task_instance_id=INSTANCE_ID,
                owner_user_id=USERS["owner"], owner_library_id="f" * 32,
                creation_path="trusted_new_task")
            writer.commit()
        finally:
            self._close_quietly(writer)
        info = SimpleNamespace(task_id=TASK_ID, dir=task_dir, degraded=False)
        self.runtime = self.WorkflowRuntime(
            _TaskLookup(TASK_ID, info), _screener_db, self.users_root,
            _visible_true)
        return task_dir

    def _full_member_db(self, task_dir, username):
        path = task_dir / ("%s.db" % self.tasks.slugify_screener(username))
        self.dbm.init_db(path)
        if username == "owner":
            self.wf_mod.migrate(path)
        conn = sqlite3.connect(str(path), timeout=15)
        try:
            conn.execute(
                "INSERT INTO articles (zotero_key, title, is_duplicate_of,"
                " import_order) VALUES ('K1', 'p2-K1', NULL, 0)")
            conn.commit()
        finally:
            conn.close()
        self.facts.prepare_team_terminal_facts(path)
        self.receipts.prepare_team_flow_receipts(path)
        return path

    def _borrowed_writer(self, path):
        conn = sqlite3.connect(str(path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        return conn

    def _close_quietly(self, conn):
        try:
            if conn.in_transaction:
                conn.rollback()
        except sqlite3.Error:
            pass
        try:
            conn.close()
        except sqlite3.Error:
            pass

    def _owner_user(self):
        return {"user_id": USERS["owner"], "username": "owner"}

    def _manage(self, *, action, target, payload=None, force=None,
                request_id=None):
        kwargs = {}
        if force is not None:
            kwargs["force"] = force
        return self.lifecycle.run_team_management(
            self.runtime, self.info, self._owner_user(),
            action=action, target_user_id=USERS[target],
            payload=payload or {}, request_id=request_id or (
                "req-%s-%s-%s" % (action, target, uuid.uuid4().hex[:8])),
            request_sha256="b" * 64, **kwargs)

    def _grant_coder(self, username, role="coder"):
        self.collab.grant_role(
            self.collab_path, actor_user_id=USERS["owner"],
            user_id=USERS[username], role=role, task_id=TASK_ID)
        self.collab.set_business_role(
            self.collab_path, actor_user_id=USERS["owner"],
            user_id=USERS[username], business_role=role, task_id=TASK_ID)

    def _add_member(self, username, role="coder"):
        self._grant_coder(username, role=role)
        outcome = self._manage(
            action="add", target=username,
            payload={"role": role, "username": username})
        self.assertEqual(outcome["outcome"], "committed")
        registration = outcome["result"]["writer"].get("source_registration")
        self.assertIsNotNone(
            registration, "add 后成员 F2 必须已登记（库已备齐的夹具）")
        self.assertEqual(registration["status"], "active")
        return outcome

    def _screening_toggle(self, username, enabled):
        self.collab.set_member_screening(
            self.collab_path, actor_user_id=USERS["owner"],
            user_id=USERS[username], enabled=enabled, task_id=TASK_ID)

    def _source_rows(self, uid):
        with closing(sqlite3.connect(str(self.collab_path))) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(
                "SELECT registration_id, generation, source_library_id,"
                " status, archive_watermark, retired_reason"
                " FROM team_source_registry WHERE task_instance_id=?"
                " AND source_user_id=? ORDER BY generation DESC",
                (INSTANCE_ID, uid))]

    def _seal_row(self, path, stage, item_key):
        with closing(sqlite3.connect(str(path))) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT seal_id, origin, payload_sha256 FROM"
                " team_terminal_seals WHERE stage=? AND item_key=?",
                (stage, item_key)).fetchone()
        return dict(row) if row is not None else None

    def _member_vote(self, username, decision="include"):
        path = self.user_paths[username]
        conn = sqlite3.connect(str(path), timeout=15)
        try:
            conn.execute(
                "INSERT INTO decisions (zotero_key, decision,"
                " exclusion_reason, notes, tags, screened_at, updated_at)"
                " VALUES ('K1', ?, '', '', '', '2026-11-20 10:00:00',"
                " '2026-11-20 10:00:00')", (decision,))
            conn.commit()
        finally:
            conn.close()

    def _append_holder_seal(self, username, *, stage, item_key, decision,
                            sealed_at, registration_id):
        path = self.user_paths[username]
        payload = _observation_payload(
            stage=stage, item_key=item_key, decision=decision,
            sealed_at=sealed_at, registration_id=registration_id,
            holder_username=username)
        writer = self._borrowed_writer(path)
        try:
            record = self.facts.append_seal_on(writer, payload, origin="actor")
            writer.commit()
            return record
        finally:
            self._close_quietly(writer)

    def _drop_team_tables(self, username, pattern):
        path = self.user_paths[username]
        conn = sqlite3.connect(str(path), timeout=15)
        try:
            names = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND name LIKE ?", (pattern,)).fetchall()]
            for name in names:
                conn.execute("DROP TABLE %s" % name)
            conn.commit()
        finally:
            conn.close()
        return names


class _TaskLookup:
    """宿主 task_lookup 真实载体（P10 同款；返回夹具 TaskInfo 值）。"""

    def __init__(self, task_id, info):
        self._task_id = task_id
        self._info = info

    def __call__(self, wanted):
        if wanted != self._task_id:
            raise KeyError(wanted)
        tasks = importlib.import_module("coscreen.tasks")
        return tasks.TaskInfo(task_id=self._info.task_id,
                              dir=self._info.dir,
                              display_name=self._info.task_id)


def _screener_db(info, screener):
    """宿主 screener_db 装配（复刻 main._screener_db 懒建语义）。"""
    tasks = importlib.import_module("coscreen.tasks")
    dbm = importlib.import_module("coscreen.db")
    slug = tasks.slugify_screener(screener)
    db = Path(info.dir) / ("%s.db" % slug)
    dbm.init_db(db)
    return db


def _visible_true(info, user):
    return True


def _observation_payload(*, stage, item_key, decision, sealed_at,
                         registration_id, holder_username):
    """v2 observation seal payload（high2/P08 配方浓缩；自洽最小名册）。"""
    facts = importlib.import_module("coscreen.team_terminal_facts")
    roster = [{
        "uid": USERS["owner"], "username": "owner", "role": "reconciler",
        "is_owner": True, "screening_enabled": False,
        "required_screener": False, "source": "owner-source", "config": "ok",
    }, {
        "uid": USERS[holder_username], "username": holder_username,
        "role": "coder", "is_owner": False, "screening_enabled": None,
        "required_screener": True, "source": "grant-source", "config": "ok",
    }]
    source_row = {"tags": "tag", "screened_at": "2026-01-02 03:04:05",
                  "updated_at": None}
    content = {"decision": decision, "exclusion_reason": "", "notes": ""}
    opinion = {
        "uid": USERS[holder_username], "decision": decision,
        "exclusion_reason": "", "notes": "",
        "library_id": "a" * 32, "revision": 7,
        "source_row": source_row,
        "opinion_sha256": hashlib.sha256(_canonical_json(content).encode(
            "utf-8")).hexdigest(),
        "source_row_sha256": hashlib.sha256(_canonical_json(
            source_row).encode("utf-8")).hexdigest(),
    }
    bindings = [
        {"source_user_id": USERS[holder_username],
         "registration_id": registration_id, "generation": 1,
         "source_library_id": "a" * 32},
    ]
    return {
        "schema": facts.OBSERVATION_PAYLOAD_SCHEMA,
        "task": {"task_id": TASK_ID, "task_instance_id": INSTANCE_ID,
                 "owner_user_id": USERS["owner"], "owner_username": "owner",
                 "owner_library_id": "f" * 32},
        "stage": stage, "item_key": item_key, "round_id": None,
        "kind": "unanimous", "trigger": "current_observation",
        "final_decision": decision, "roster": roster,
        "required_uids": [USERS[holder_username]], "opinions": [opinion],
        "arbitration": None,
        "protocol": {"source_registration_id": registration_id,
                     "roster_revision": 1},
        "source_request": {"operation": "completion_observe",
                           "request_id": "request-p2-1",
                           "request_sha256": "b" * 64},
        "sealed_at": sealed_at,
        "observation": {"host_operation": "completion_observe",
                        "host_stage": "screen", "host_item_keys": [item_key],
                        "observed_by_user_id": USERS[holder_username],
                        "basis": "current_snapshot"},
        "source_bindings": bindings,
    }


class NormalRemoveArchiveTests(P2RetirementMatrixBase):
    """S1 回归基线＋S4/S5（修前 503 的两态）修后 200 归档。"""

    def test_s4_screening_off_reconciler_remove_archives(self):
        self._make_task()
        self._add_member("carol", role="reconciler")
        self._screening_toggle("carol", enabled=False)
        outcome = self._manage(action="remove", target="carol",
                               payload={"role": "reconciler"})
        self.assertEqual(outcome["outcome"], "committed")
        rows = self._source_rows(USERS["carol"])
        self.assertEqual(rows[0]["status"], "retired")
        audit = outcome["result"].get("retirement_audit")
        self.assertIsNotNone(audit)
        self.assertEqual(audit["library_state"], "archived")

    def test_s5_dual_grant_conflict_remove_archives(self):
        self._make_task()
        self._add_member("bob")
        # 双 grant 冲突：bob 同时持 coder＋reconciler 两 grant（probe4 S5）。
        self.collab.grant_role(
            self.collab_path, actor_user_id=USERS["owner"],
            user_id=USERS["bob"], role="reconciler", task_id=TASK_ID)
        outcome = self._manage(action="remove", target="bob",
                               payload={"role": "coder"})
        self.assertEqual(outcome["outcome"], "committed")
        rows = self._source_rows(USERS["bob"])
        self.assertEqual(rows[0]["status"], "retired")
        self.assertEqual(
            outcome["result"].get("retirement_audit", {}).get("library_state"),
            "archived")

    def test_s1_required_member_remove_still_archives(self):
        self._make_task()
        self._add_member("bob")
        outcome = self._manage(action="remove", target="bob",
                               payload={"role": "coder"})
        self.assertEqual(outcome["outcome"], "committed")
        self.assertEqual(self._source_rows(USERS["bob"])[0]["status"],
                         "retired")


class MissingLibraryEmptyArchiveTests(P2RetirementMatrixBase):
    """S6/egg D.3-2：active∧库缺失 → 空归档放行＋watermark 指向空枚举证据。"""

    def test_s6_missing_library_empty_archive_watermark(self):
        self._make_task()
        self._add_member("bob")
        os.remove(self.user_paths["bob"])
        outcome = self._manage(action="remove", target="bob",
                               payload={"role": "coder"})
        self.assertEqual(outcome["outcome"], "committed")
        result = outcome["result"]
        audit = result.get("retirement_audit")
        self.assertIsNotNone(audit)
        self.assertEqual(audit["library_state"], "missing_empty_archive")
        self.assertEqual(result.get("pending_keys_at_removal"), [])
        evidence = outcome["receipt"]["internal_evidence"]["archive_evidence"]
        self.assertTrue(evidence.get("member_library_missing"))
        self.assertEqual(evidence.get("seal_count"), 0)
        rows = self._source_rows(USERS["bob"])
        self.assertEqual(rows[0]["status"], "retired")
        self.assertEqual(
            rows[0]["archive_watermark"],
            self.facts.sha256(evidence),
            "watermark 必须指向同 commit 空枚举证据的 sha256")


class MissingTablesPrepareHealTests(P2RetirementMatrixBase):
    """S7/S8：成员库 F1/receipts 缺表 → 锁外准备前置治愈 → 200 归档。"""

    def test_s7_missing_f1_tables_healed_then_archived(self):
        self._make_task()
        self._add_member("bob")
        dropped = self._drop_team_tables("bob", "team_terminal%")
        self.assertTrue(dropped, "夹具必须真的删掉 F1 表")
        outcome = self._manage(action="remove", target="bob",
                               payload={"role": "coder"})
        self.assertEqual(outcome["outcome"], "committed")
        self.assertEqual(
            outcome["result"]["retirement_audit"]["library_state"],
            "archived")

    def test_s8_missing_receipt_tables_healed_then_archived(self):
        self._make_task()
        self._add_member("bob")
        dropped = self._drop_team_tables("bob", "team_flow_receipt%")
        self.assertTrue(dropped, "夹具必须真的删掉 receipts 表")
        outcome = self._manage(action="remove", target="bob",
                               payload={"role": "coder"})
        self.assertEqual(outcome["outcome"], "committed")
        self.assertEqual(
            outcome["result"]["retirement_audit"]["library_state"],
            "archived")


class CorruptLibraryForceRemoveTests(P2RetirementMatrixBase):
    """S9/R3：库损坏 → typed 409 码＋指引；force=True 强制移除＋审计。"""

    def _corrupt_bob_f1(self):
        path = self.user_paths["bob"]
        conn = sqlite3.connect(str(path), timeout=15)
        try:
            conn.execute("DROP TABLE team_terminal_seals")
            conn.execute("CREATE TABLE team_terminal_seals (seal_id TEXT)")
            conn.commit()
        finally:
            conn.close()

    def test_s9_corrupt_then_typed_then_force(self):
        self._make_task()
        self._add_member("bob")
        self._corrupt_bob_f1()
        with self.assertRaises(self.lifecycle.TeamManagementError) as caught:
            self._manage(action="remove", target="bob",
                         payload={"role": "coder"})
        self.assertEqual(caught.exception.code,
                         "retirement_source_unverifiable")
        recovery = self.lifecycle.retirement_recovery_payload(
            caught.exception.code)
        self.assertIsNotNone(recovery)
        self.assertTrue(recovery["actions"])
        self.assertTrue(recovery["force_remove_available"])
        rows = self._source_rows(USERS["bob"])
        self.assertEqual(rows[0]["status"], "active", "拒绝路径不退休登记")
        forced = self._manage(action="remove", target="bob",
                              payload={"role": "coder"}, force=True)
        self.assertEqual(forced["outcome"], "committed")
        audit = forced["result"]["retirement_audit"]
        self.assertTrue(audit["forced"])
        self.assertEqual(audit["library_state"], "forced_no_verified_archive")
        evidence = (forced["receipt"]["internal_evidence"]["archive_evidence"])
        self.assertTrue(evidence.get("forced"))
        self.assertEqual(self._source_rows(USERS["bob"])[0]["status"],
                         "retired")


class PendingVoteAuditTests(P2RetirementMatrixBase):
    """R1：持未决票成员允许移除＋receipt 记 pending_keys_at_removal。

    pending 判据＝collab 权威视角：成员已投、collab 现行表无完成见证的键
    （成员库内 seal 未被 collab 归档时，从权威视角仍是未决——与归档契约
    一致，archive_seals_on 对已持非 archive 行的键 fail-closed）。
    """

    def test_r1_pending_keys_recorded(self):
        self._make_task()
        self._add_member("bob")
        self._member_vote("bob", decision="include")
        outcome = self._manage(action="remove", target="bob",
                               payload={"role": "coder"})
        self.assertEqual(outcome["outcome"], "committed")
        self.assertEqual(
            outcome["result"].get("pending_keys_at_removal"),
            [{"stage": "stage1", "item_keys": ["K1"]}])
        self.assertEqual(self._source_rows(USERS["bob"])[0]["status"],
                         "retired")

    def test_r1_no_votes_empty_pending(self):
        self._make_task()
        self._add_member("bob")
        outcome = self._manage(action="remove", target="bob",
                               payload={"role": "coder"})
        self.assertEqual(outcome["outcome"], "committed")
        self.assertEqual(
            outcome["result"].get("pending_keys_at_removal"), [])


class ReinviteRestoreTests(P2RetirementMatrixBase):
    """R2：重邀按身份核对恢复被隔离旧库；失败给明确指引。"""

    def test_r2_reinvite_restores_quarantined_library(self):
        self._make_task()
        add_outcome = self._add_member("bob")
        old_library = add_outcome["result"]["writer"][
            "source_registration"]["source_library_id"]
        old_generation = int(add_outcome["result"]["writer"][
            "source_registration"]["generation"])
        marker = self.user_paths["bob"]  # 旧库文件本体即数据载体
        conn = sqlite3.connect(str(marker), timeout=15)
        try:
            conn.execute("CREATE TABLE p2_marker (x TEXT)")
            conn.execute("INSERT INTO p2_marker VALUES ('keep-me')")
            conn.commit()
        finally:
            conn.close()
        removed = self._manage(action="remove", target="bob",
                               payload={"role": "coder"})
        self.assertEqual(removed["outcome"], "committed")
        quarantined = Path(str(marker) + ".revoked-20261121T000000Z")
        os.rename(marker, quarantined)
        self.assertFalse(marker.is_file())
        readd = self._manage(action="add", target="bob",
                             payload={"role": "coder", "username": "bob"})
        self.assertEqual(readd["outcome"], "committed")
        registration = readd["result"]["writer"]["source_registration"]
        self.assertEqual(registration["status"], "active")
        self.assertTrue(registration.get("restore", {}).get("restored"),
                        "重邀必须按身份核对恢复旧库")
        self.assertEqual(registration["source_library_id"], old_library)
        self.assertEqual(int(registration["generation"]), old_generation + 1)
        self.assertTrue(marker.is_file())
        with closing(sqlite3.connect(str(marker))) as conn:
            kept = conn.execute(
                "SELECT x FROM p2_marker").fetchone()
        self.assertEqual(kept[0], "keep-me", "旧库工作数据必须原样回来")

    def test_r2_identity_unconfirmed_gets_guidance(self):
        self._make_task()
        self._add_member("bob")
        removed = self._manage(action="remove", target="bob",
                               payload={"role": "coder"})
        self.assertEqual(removed["outcome"], "committed")
        quarantined = Path(str(self.user_paths["bob"])
                           + ".revoked-20261121T000000Z")
        os.rename(self.user_paths["bob"], quarantined)
        # 外力篡改库身份 → 身份核对必须失败（不恢复、给指引、新库不静默复用）。
        conn = sqlite3.connect(str(quarantined), timeout=15)
        try:
            conn.execute("UPDATE team_vote_revision_meta"
                         " SET library_id = '%s' WHERE singleton=1"
                         % ("b" * 32))
            conn.commit()
        finally:
            conn.close()
        readd = self._manage(action="add", target="bob",
                             payload={"role": "coder", "username": "bob"})
        self.assertEqual(readd["outcome"], "committed")
        registration = readd["result"]["writer"]["source_registration"]
        self.assertEqual(registration["status"], "pending")
        self.assertIn(registration.get("issue"),
                      ("restore_identity_unconfirmed",
                       "restore_identity_unverifiable",
                       "member_library_missing"))
        recovery = registration.get("recovery")
        self.assertIsNotNone(recovery)
        self.assertTrue(recovery.get("actions"))


class EnsureRegisteredTests(P2RetirementMatrixBase):
    """ensure_member_source_registered：幂等＋库缺失降级（P1 对接签名）。"""

    def test_idempotent_register(self):
        self._make_task()
        info = self.info
        first = self.lifecycle.ensure_member_source_registered(
            self.runtime, info, self._owner_user(), USERS["carol"])
        self.assertEqual(first["status"], "active")
        self.assertFalse(first["idempotent"])
        second = self.lifecycle.ensure_member_source_registered(
            self.runtime, info, self._owner_user(), USERS["carol"])
        self.assertEqual(second["status"], "active")
        self.assertTrue(second["idempotent"])
        self.assertEqual(first["registration_id"], second["registration_id"])

    def test_missing_library_unavailable_no_raise(self):
        self._make_task()
        os.remove(self.user_paths["bob"])  # 夹具默认建齐成员库；显式删库降级
        result = self.lifecycle.ensure_member_source_registered(
            self.runtime, self.info, self._owner_user(), USERS["bob"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["issue"], "member_library_missing")
        self.assertIsNotNone(result.get("recovery"))
        ghost = self.lifecycle.ensure_member_source_registered(
            self.runtime, self.info, self._owner_user(), 999999)
        self.assertEqual(ghost["status"], "unavailable")
        self.assertEqual(ghost["issue"], "account_unresolved")


class SealConflictRepairTests(P2RetirementMatrixBase):
    """R6：管理端冲突 seal 修复（选权威归档另一枚＋审计）。"""

    def _seed_divergent_seals(self):
        add_bob = self._add_member("bob")
        add_carol = self._add_member("carol")
        reg_bob = add_bob["result"]["writer"]["source_registration"][
            "registration_id"]
        reg_carol = add_carol["result"]["writer"]["source_registration"][
            "registration_id"]
        self._append_holder_seal(
            "bob", stage="stage1", item_key="K1", decision="include",
            sealed_at=_SEALED_AT_A, registration_id=reg_bob)
        self._append_holder_seal(
            "carol", stage="stage1", item_key="K1", decision="exclude",
            sealed_at=_SEALED_AT_B, registration_id=reg_carol)
        bob_seal = self._seal_row(self.user_paths["bob"], "stage1", "K1")
        carol_seal = self._seal_row(self.user_paths["carol"], "stage1", "K1")
        self.assertNotEqual(bob_seal["seal_id"], carol_seal["seal_id"])
        return bob_seal, carol_seal

    def test_r6_authoritative_archived_into_collab(self):
        self._make_task()
        bob_seal, carol_seal = self._seed_divergent_seals()
        outcome = self.lifecycle.run_team_seal_conflict_repair(
            self.runtime, self.info, self._owner_user(),
            stage="stage1", item_key="K1",
            authoritative_seal_id=bob_seal["seal_id"],
            request_id="req-r6-1", request_sha256="b" * 64)
        self.assertEqual(outcome["outcome"], "committed")
        collab_row = self._seal_row(self.collab_path, "stage1", "K1")
        self.assertIsNotNone(collab_row)
        self.assertEqual(collab_row["seal_id"], bob_seal["seal_id"])
        self.assertEqual(collab_row["origin"], "actor_archive")
        result = outcome["result"]
        self.assertEqual(result["authoritative_seal_id"], bob_seal["seal_id"])
        self.assertEqual(result["archived"][0]["seal_id"], bob_seal["seal_id"])
        self.assertEqual(len(result["losers"]), 1)
        self.assertEqual(result["losers"][0]["seal_id"], carol_seal["seal_id"])
        self.assertEqual(result["losers"][0]["holder"], "carol.db")

    def test_r6_invalid_authoritative_rejected(self):
        self._make_task()
        self._seed_divergent_seals()
        with self.assertRaises(self.lifecycle.TeamManagementError) as caught:
            self.lifecycle.run_team_seal_conflict_repair(
                self.runtime, self.info, self._owner_user(),
                stage="stage1", item_key="K1",
                authoritative_seal_id="no-such-seal",
                request_id="req-r6-2", request_sha256="b" * 64)
        self.assertEqual(caught.exception.code, "invalid_authoritative_seal")

    def test_r6_owner_gate(self):
        self._make_task()
        self._seed_divergent_seals()
        with self.assertRaises(self.lifecycle.TeamManagementError) as caught:
            self.lifecycle.run_team_seal_conflict_repair(
                self.runtime, self.info,
                {"user_id": USERS["bob"], "username": "bob"},
                stage="stage1", item_key="K1",
                authoritative_seal_id="whatever",
                request_id="req-r6-3", request_sha256="b" * 64)
        self.assertEqual(caught.exception.code, "not_owner")


if __name__ == "__main__":
    unittest.main(verbosity=2)
