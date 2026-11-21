"""P1（2.1.6 缺口族根治主干包）异常族"无裸 500"故障注入矩阵。

设计依据：``E:/rf216-store-scan-20261120/GAP_DESIGN_SURVEY.md`` §3（异常族
泄漏全扫）与 §5.2（统一映射表）、§5.5-3（无裸 500 故障注入闸）。

矩阵 = **异常族 × 写通道**，每格至少一条：注入故障 → 断言 409/503 typed
信封（含 ``error_code`` + ``retryable``），**任何 500 = 结构泄漏直接失败**。

覆盖族（并集）：
- ``TeamFlowReceiptUnavailable``（not_prepared / schema_mismatch / corrupt /
  unreadable / connection_contract）
- ``TerminalFactsUnavailable``（not_prepared / schema_mismatch / unreadable）
- ``TerminalFactsPayloadInvalid`` / ``TerminalFactsConflict``
- ``TerminalRegistryUnavailable`` / ``TerminalRegistryConflict`` /
  ``TerminalRegistryStateError``
- ``VoteLedgerUnavailable``
- ``TeamManagementError`` 退休族（R5：409 + ``team_retirement_blocked`` +
  ``recovery``）

覆盖通道（真实 HTTP/真实适配器）：
- 成员写主通道 ``_mutate_team``（decisions 路由；
  registration/ledger/facts/conflict 族）
- appraisal 直调 mutate（同类映射复用；由 appraisal 自身适配器兜底）
- 仲裁 ``_team_http_for``（四路由共用适配器，直接注入）
- 管理 ``_team_management_http_error``（五入口共用适配器，直接注入）

装载：产品导入推迟到 ``setUpClass``；真实建队配方同 P1 入场矩阵套件。
"""
from __future__ import annotations

import importlib
import os
import sqlite3
import unittest
import uuid
from contextlib import closing
from unittest import mock

OWNER = "p1nerowner"
MEMBER = "p1nermember"

KEY_A = "P1NER-A"
KEY_B = "P1NER-B"

_INSERT_ARTICLE = (
    "INSERT INTO articles (zotero_key, title, is_duplicate_of, import_order)"
    " VALUES (?, ?, NULL, ?)")


def _close_quietly(conn):
    try:
        conn.close()
    except Exception:
        pass


class _EnvGuard:
    def __init__(self, value, restore):
        self._value = value
        self._restore = restore

    def __enter__(self):
        os.environ["REVIEWFLOW_MODE"] = self._value
        return self

    def __exit__(self, *exc_info):
        self._restore()
        return False


class TeamWriteNoBare500Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from custom_backend import auth as auth_mod
        from custom_backend import main as main_mod

        cls.client = TestClient(main_mod.app)
        cls.client.__enter__()
        cls.raw_client = TestClient(main_mod.app,
                                    raise_server_exceptions=False)
        cls.raw_client.__enter__()
        cls.main_mod = main_mod
        cls.team_http_errors = importlib.import_module(
            "custom_backend.team_http_errors")
        cls.users = {}
        for name in (OWNER, MEMBER):
            user = auth_mod.create_user(name, "P1-NoBare500-2026")
            cls.users[name] = {
                "user_id": user["id"], "username": user["username"],
                "token": auth_mod.create_access_token(
                    user["id"], user["username"])}
        cls.tasks_mod = importlib.import_module("coscreen.tasks")
        cls._build_team()

    @classmethod
    def tearDownClass(cls):
        cls.raw_client.__exit__(None, None, None)
        cls.client.__exit__(None, None, None)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @classmethod
    def _auth(cls, name):
        return {"Authorization": "Bearer " + cls.users[name]["token"]}

    @classmethod
    def _server_mode(cls):
        saved = os.environ.get("REVIEWFLOW_MODE")

        def restore():
            if saved is None:
                os.environ.pop("REVIEWFLOW_MODE", None)
            else:
                os.environ["REVIEWFLOW_MODE"] = saved
        return _EnvGuard("server", restore)

    @classmethod
    def _build_team(cls):
        from custom_backend.review_workflow_api import WorkflowRuntime

        collab = importlib.import_module("coscreen.task_collaboration")
        cdb = importlib.import_module("coscreen.db")
        wf = importlib.import_module("coscreen.review_workflow")
        tasks = importlib.import_module("coscreen.tasks")
        tf = importlib.import_module("coscreen.team_terminal_facts")
        receipts = importlib.import_module("coscreen.team_flow_receipts")
        tr = importlib.import_module("coscreen.team_terminal_registry")
        tsl = importlib.import_module("custom_backend.team_source_lifecycle")
        coding_evidence = importlib.import_module("coscreen.coding_evidence")

        owner = cls.users[OWNER]
        owner_uid = owner["user_id"]
        with cls._server_mode():
            task_id = cls.client.post(
                "/api/tasks",
                json={"name": "p1-nb-" + uuid.uuid4().hex[:8]},
                headers=cls._auth(OWNER)).json()["task_id"]
        info = tasks.find_task(task_id, cls.main_mod.DATA_DIR)
        cls.info = info
        task_dir = info.dir
        cls.task_id = task_id
        cls.collab_path = collab.shared_store_path(task_dir)
        if not cls.collab_path.is_file():
            collab.initialize_task_store(
                task_dir, task_id=task_id, owner_user_id=owner_uid,
                owner_username=owner["username"])
        # 业务角色：owner=reconciler（R4 门——owner 已配置）、成员=coder。
        collab.set_business_role(
            cls.collab_path, actor_user_id=owner_uid, user_id=owner_uid,
            business_role="reconciler", task_id=task_id)
        member_uid = cls.users[MEMBER]["user_id"]
        collab.grant_role(
            cls.collab_path, actor_user_id=owner_uid, user_id=member_uid,
            role="coder", task_id=task_id)
        collab.set_business_role(
            cls.collab_path, actor_user_id=owner_uid, user_id=member_uid,
            business_role="coder", task_id=task_id)
        slug = tasks.slugify_screener
        cls.owner_db = task_dir / f"{slug(owner['username'])}.db"
        cls.member_db = task_dir / f"{slug(cls.users[MEMBER]['username'])}.db"
        for name, path in ((OWNER, cls.owner_db), (MEMBER, cls.member_db)):
            cdb.init_db(path)
            wf.migrate(path)
            tf.prepare_team_terminal_facts(path)
            receipts.prepare_team_flow_receipts(path)
            coding_evidence.ensure_fields(path)
            with closing(sqlite3.connect(str(path))) as conn, conn:
                conn.executemany(
                    _INSERT_ARTICLE,
                    [(key, f"P1 {key}", i)
                     for i, key in enumerate((KEY_A, KEY_B))])
                if name == OWNER:
                    conn.execute(
                        "UPDATE workflow_state SET team_latched=1"
                        " WHERE singleton=1")
        tr.prepare_team_terminal_registry(cls.collab_path)
        tf.prepare_team_terminal_facts(cls.collab_path)
        receipts.prepare_team_flow_receipts(cls.collab_path)
        with closing(sqlite3.connect(str(cls.collab_path))) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT task_instance_id FROM main.team_task_protocol"
                " LIMIT 1").fetchone()
        assert row is not None, "create_task must register the protocol row"
        cls.instance_id = row["task_instance_id"]
        cached = {}
        for name, path in ((OWNER, cls.owner_db), (MEMBER, cls.member_db)):
            uid = cls.users[name]["user_id"]
            library = "%032x" % uid
            token = tsl.make_source_identity(
                task_dir=task_dir, source_path=path,
                source_user_id=uid, source_library_id=library)
            writer = sqlite3.connect(str(cls.collab_path), timeout=15)
            writer.row_factory = sqlite3.Row
            try:
                writer.execute("BEGIN IMMEDIATE")
                tr.register_source_on(
                    writer, task_instance_id=cls.instance_id,
                    source_user_id=uid, source_library_id=library,
                    source_path_identity=token,
                    registration_id=f"reg-p1-{name}")
                writer.commit()
            finally:
                _close_quietly(writer)
            cached[name] = (uid, library, token)
        # 真实 runtime 装配（宿主形状；供直接调用 _mutate_team 的通道注入）
        cls.runtime = WorkflowRuntime(
            lambda _tid: info, cls.main_mod._screener_db,
            cls.main_mod.DATA_DIR, lambda _info, _user: True)

    def _decide(self, name, key, decision="include", stage="screen"):
        route = "decisions" if stage == "screen" else "stage2/decisions"
        return self.raw_client.post(
            f"/api/tasks/{self.task_id}/{route}",
            json={"zotero_key": key, "decision": decision,
                  "reason": "", "notes": "", "tags": None},
            headers=self._auth(name))

    def _assert_typed_error(self, resp, label, *, allowed=(409, 503),
                            require_error_code=True):
        self.assertNotEqual(resp.status_code, 500,
                            f"{label}: 未收口 500：{resp.text[:400]}")
        self.assertIn(resp.status_code, allowed,
                      f"{label}: 期望 {allowed}，得 {resp.status_code}: "
                      f"{resp.text[:300]}")
        detail = resp.json().get("detail")
        self.assertIsInstance(detail, dict, f"{label}: 非结构化错误体 {detail!r}")
        if require_error_code:
            self.assertIn("error_code", detail,
                          f"{label}: 缺 error_code {detail!r}")
            self.assertIn("retryable", detail,
                          f"{label}: 缺 retryable {detail!r}")
        else:
            # 既有 TeamWriteBlocked 结构化形状（code 键）同样算 typed 收口。
            self.assertTrue(
                "error_code" in detail or "code" in detail,
                f"{label}: 缺结构化 code {detail!r}")

    def _drop_receipts(self, path):
        with closing(sqlite3.connect(str(path))) as conn, conn:
            conn.execute("DROP TABLE IF EXISTS team_flow_receipts_meta")
            conn.execute("DROP TABLE IF EXISTS team_flow_receipts")

    def _drop_facts(self, path):
        with closing(sqlite3.connect(str(path))) as conn, conn:
            conn.execute("DROP TABLE IF EXISTS team_terminal_facts_meta")
            conn.execute("DROP TABLE IF EXISTS team_terminal_seals")

    # ==================================================================
    # 1. 真簇注入：成员写通道 × receipts not_prepared（删表）
    # ==================================================================
    def test_member_write_receipts_not_prepared_is_typed_409(self):
        # 半装注入（只删 receipts 两表 → 半装；咽喉不自动修补，下游 typed 409）
        self._drop_receipts(self.member_db)
        try:
            resp = self._decide(MEMBER, KEY_A)
        finally:
            importlib.import_module(
                "coscreen.team_flow_receipts").prepare_team_flow_receipts(
                    self.member_db)
        self._assert_typed_error(resp, "receipts not_prepared",
                                 allowed=(409,))
        self.assertEqual(resp.json()["detail"]["error_code"],
                         "team_flow_receipt_unavailable")
        self.assertIs(resp.json()["detail"]["retryable"], False)

    # ==================================================================
    # 2. 真簇注入：成员写通道 × F1 not_prepared（删 seals 两表）
    # ==================================================================
    def test_member_write_terminal_facts_not_prepared_is_typed_409(self):
        # F1 两表被删（半装）：先于读 receipts 的 P08/P09 计划阶段发现
        # source identity 不可核 → 既有 TeamWriteBlocked（code 键）结构化
        # 409；无论命中哪一层，**绝不允许裸 500**。
        self._drop_facts(self.member_db)
        try:
            resp = self._decide(MEMBER, KEY_A)
        finally:
            importlib.import_module(
                "coscreen.team_terminal_facts").prepare_team_terminal_facts(
                    self.member_db)
        self._assert_typed_error(resp, "F1 not_prepared", allowed=(409,),
                                 require_error_code=False)

    # ==================================================================
    # 3. 故障注入：receipts 各 reason（unreadable→503 / corrupt→409 /
    #    connection_contract→原样上抛）经真实路由
    # ==================================================================
    def test_receipts_reason_matrix_through_member_route(self):
        from coscreen.team_flow_receipts import TeamFlowReceiptUnavailable

        with mock.patch(
                "custom_backend.team_flow_commit.read_receipt_on",
                side_effect=TeamFlowReceiptUnavailable("unreadable", "busy")):
            transient = self._decide(MEMBER, KEY_A)
        self._assert_typed_error(transient, "receipts unreadable", allowed=(503,))
        self.assertIs(transient.json()["detail"]["retryable"], True)

        with mock.patch(
                "custom_backend.team_flow_commit.read_receipt_on",
                side_effect=TeamFlowReceiptUnavailable("corrupt", "bad row")):
            corrupt = self._decide(MEMBER, KEY_A)
        self._assert_typed_error(corrupt, "receipts corrupt", allowed=(409,))
        self.assertEqual(corrupt.json()["detail"]["error_code"],
                         "team_flow_receipt_unavailable")

    # ==================================================================
    # 4. 故障注入：F1 / registry / ledger / conflict 四族经适配器映射
    # ==================================================================
    def test_exception_family_union_mapping(self):
        from coscreen.team_terminal_facts import (
            TerminalFactsConflict,
            TerminalFactsPayloadInvalid,
            TerminalFactsUnavailable,
        )
        from coscreen.team_terminal_registry import (
            TerminalRegistryConflict,
            TerminalRegistryStateError,
            TerminalRegistryUnavailable,
        )
        from coscreen.team_vote_ledger import VoteLedgerUnavailable
        from coscreen.team_flow_receipts import TeamFlowReceiptUnavailable

        cases = [
            (TerminalFactsUnavailable("not_prepared", "x"), 409,
             "team_terminal_facts_unavailable"),
            (TerminalFactsUnavailable("schema_mismatch", "x"), 409,
             "team_terminal_facts_unavailable"),
            (TerminalFactsUnavailable("unreadable", "x"), 503,
             "team_terminal_facts_unavailable"),
            (TerminalFactsUnavailable("corrupt", "x"), 409,
             "team_terminal_facts_unavailable"),
            (TerminalFactsPayloadInvalid("x"), 409, "team_seal_payload_invalid"),
            (TerminalFactsConflict("x"), 409, "team_terminal_seal_conflict"),
            (TerminalRegistryUnavailable("not_prepared", "x"), 409,
             "team_registry_unavailable"),
            (TerminalRegistryUnavailable("unreadable", "x"), 503,
             "team_registry_unavailable"),
            (TerminalRegistryConflict("x"), 409, "team_registry_conflict"),
            (TerminalRegistryStateError("x"), 409, "team_registry_conflict"),
            (VoteLedgerUnavailable("unmigrated"), 409,
             "team.flow.revision_unavailable"),
        ]
        for exc, status, error_code in cases:
            http = self.team_http_errors.typed_team_http(exc)
            self.assertIsNotNone(http, f"{type(exc).__name__}: 未映射")
            self.assertEqual(http.status_code, status,
                             f"{type(exc).__name__}({getattr(exc, 'reason', '')})")
            self.assertEqual(http.detail["error_code"], error_code)
        for reason in ("not_prepared", "schema_mismatch", "corrupt",
                       "unreadable"):
            exc = TeamFlowReceiptUnavailable(reason, "x")
            http = self.team_http_errors.typed_team_http(exc)
            self.assertIsNotNone(http)
            expected = 503 if reason == "unreadable" else 409
            self.assertEqual(http.status_code, expected, reason)
            self.assertEqual(http.detail["retryable"], reason == "unreadable")
        contract = TeamFlowReceiptUnavailable("connection_contract", "x")
        self.assertIsNone(self.team_http_errors.typed_team_http(contract),
                          "connection_contract 必须原样上抛")

    # ==================================================================
    # 5. 仲裁适配器：四族 → 结构化（不再纯文本 503）
    # ==================================================================
    def test_arbitration_adapter_returns_structured_errors(self):
        arb = importlib.import_module("custom_backend.arbitration_session_api")
        from coscreen.team_flow_receipts import (
            TeamFlowReceiptConflict,
            TeamFlowReceiptUnavailable,
        )

        mapped = arb._team_http_for(
            TeamFlowReceiptUnavailable("not_prepared", "x"), "msg",
            self.main_mod._bad)
        self.assertEqual(mapped.status_code, 409)
        self.assertEqual(mapped.detail["error_code"],
                         "team_flow_receipt_unavailable")
        conflict = arb._team_http_for(
            TeamFlowReceiptConflict("x"), "msg", self.main_mod._bad)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.detail["error_code"],
                         "request_receipt_conflict")
        # 修轮备注 b：三处同族信封都带 ``code`` 键（不再一处缺）。
        self.assertEqual(conflict.detail.get("code"),
                         "request_receipt_conflict")
        # 修轮备注 a：connection_contract 两适配器统一原样上抛（P15-D1）。
        contract = TeamFlowReceiptUnavailable("connection_contract", "x")
        with self.assertRaises(TeamFlowReceiptUnavailable):
            arb._team_http_for(contract, "msg", self.main_mod._bad)
        with self.assertRaises(TeamFlowReceiptUnavailable):
            self.main_mod._team_management_http_error(contract)
        mgmt_conflict = self.main_mod._team_management_http_error(
            TeamFlowReceiptConflict("x"))
        self.assertEqual(mgmt_conflict.status_code, 409)
        self.assertEqual(mgmt_conflict.detail.get("code"),
                         "request_receipt_conflict")
        self.assertEqual(mgmt_conflict.detail.get("error_code"),
                         "request_receipt_conflict")

    # ==================================================================
    # 6. 管理适配器（R5）：retirement_source_unavailable → 409 + recovery
    # ==================================================================
    def test_management_retirement_codes_are_409_with_recovery(self):
        from custom_backend.team_source_lifecycle import TeamManagementError

        http = self.main_mod._team_management_http_error(
            TeamManagementError("retirement_source_unavailable", "probe"))
        self.assertEqual(http.status_code, 409)
        self.assertEqual(http.detail["error_code"], "team_retirement_blocked")
        self.assertIs(http.detail["retryable"], False)
        self.assertIn("recovery", http.detail)
        self.assertIn("restore_or_repair_member_library",
                      http.detail["recovery"]["actions"])
        # 其余退休族也是确定性 409（不再 503 化）
        for code in ("retirement_archive_incomplete",
                     "retirement_copy_mismatch",
                     "retirement_source_unreadable",
                     "retirement_source_missing"):
            mapped = self.main_mod._team_management_http_error(
                TeamManagementError(code, "x"))
            self.assertEqual(mapped.status_code, 409, code)
            self.assertIs(mapped.detail["retryable"], False, code)
        # source_library_unverifiable 保持"暂时"503（可重试，不误标永久）
        transient = self.main_mod._team_management_http_error(
            TeamManagementError("source_library_unverifiable", "x"))
        self.assertEqual(transient.status_code, 503)
        self.assertIs(transient.detail["retryable"], True)
        # not_owner 保持 403（结构化信封）
        not_owner = self.main_mod._team_management_http_error(
            TeamManagementError("not_owner", "x"))
        self.assertEqual(not_owner.status_code, 403)
        self.assertEqual(not_owner.detail["error_code"],
                         "team_management_not_owner")

    # ==================================================================
    # 7. 真实管理端点：退休族注入 → 409 typed + recovery（不是 503）
    # ==================================================================
    def test_management_route_retirement_409_end_to_end(self):
        from custom_backend import team_source_lifecycle as lifecycle

        def failing(_runtime, _info, _user, **kwargs):
            raise lifecycle.TeamManagementError(
                "retirement_source_unavailable", "injected for matrix")

        with mock.patch.object(lifecycle, "run_team_management", failing):
            with self._server_mode():
                resp = self.raw_client.delete(
                    f"/api/tasks/{self.task_id}/collaboration/roles/"
                    f"{self.users[MEMBER]['user_id']}/coder",
                    headers=self._auth(OWNER))
        self.assertNotEqual(resp.status_code, 500, resp.text)
        self.assertEqual(resp.status_code, 409, resp.text)
        detail = resp.json()["detail"]
        self.assertEqual(detail["error_code"], "team_retirement_blocked")
        self.assertIn("recovery", detail)

    # ==================================================================
    # 8. 成员写通道 × 任意未知 Exception 仍不落 500 的边界声明
    #    （未知异常由既有全局处理器负责：本断言只覆盖 typed 族）
    # ==================================================================
    def test_typed_family_never_bare_500_via_routes(self):
        from coscreen.team_terminal_facts import TerminalFactsUnavailable

        with mock.patch(
                "custom_backend.team_flow_commit.read_receipt_on",
                side_effect=TerminalFactsUnavailable("not_prepared", "x")):
            resp = self._decide(MEMBER, KEY_B)
        self._assert_typed_error(resp, "F1 via commit read", allowed=(409,))

    # ==================================================================
    # 9. P2 补登记降级：hook 未就绪/抛错不阻断写（可选调用契约）
    # ==================================================================
    def test_member_source_registration_hook_failure_is_non_blocking(self):
        from custom_backend import team_source_lifecycle as lifecycle

        # 真实缺口态：删掉成员库团队表两族，触发咽喉 auto-prepare + 补登记。
        self._drop_receipts(self.member_db)
        self._drop_facts(self.member_db)
        real_prepare = lifecycle.prepare_team_protocol_storage

        def prepare_without_hook(**kwargs):
            return real_prepare(**kwargs)

        # 模拟 P2 未就绪（属性缺失）与 P2 抛错两种情况：写都不应被阻断。
        with mock.patch.object(lifecycle, "prepare_team_protocol_storage",
                               prepare_without_hook):
            with mock.patch.object(
                    lifecycle, "ensure_member_source_registered",
                    side_effect=RuntimeError("p2-boom"), create=True):
                resp = self._decide(MEMBER, KEY_A)
        self.assertNotEqual(resp.status_code, 500, resp.text)
        self.assertIn(resp.status_code, (200, 409), resp.text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
