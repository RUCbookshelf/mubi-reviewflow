"""P1（2.1.6 缺口族根治主干包）入场 × 准备矩阵 + R4/R7 红绿钉子。

设计依据：``E:/rf216-store-scan-20261120/GAP_DESIGN_SURVEY.md`` §2（入场×
准备覆盖矩阵 E1-E10）、§5.1（咽喉 auto-prepare 修法）、§5.4（F2 补登记）、
§6 R4/R7 裁定。

本文件钉三件事（真实 HTTP / 真实建队 / 零 mock 建队配方）：

1. **入场族 × 首个团队操作**：每个"把人变成团队成员"的入口（legacy 首邀
   邀请口 E3 / legacy grant 口 E5 / P18 invite E4 / P18 grant E6 / owner
   直调 appraisal E1-E2 形状 / 读路径懒建裸库 E9 之后首写）→ 该成员的
   **首个团队团队写不得 500**，且 2xx 后成员库表集 ⊇ {F1 两表, receipts
   两表, appraisal_records}（prepared 位形 = 咽喉 auto-prepare 生效）。
2. **R4**：owner 未配置业务角色时，团队筛选写 = 409 typed
   （``team_owner_business_role_unconfigured`` + ``recovery``），不再落成
   F1 roster 缺项的裸 500；配置后同请求 2xx（红→绿）。
3. **R7**：legacy invite/grant 两个分支响应都携带 ``source_registration``
   字段，且与 P18 分支同形（``status`` ∈ {active,pending}）。

装载：产品导入全部推迟到 ``setUpClass``（``python -I -B`` 可载）；数据根与
模式由 session conftest 钉（``COBOOKSHELF_DATA_DIR`` + ``REVIEWFLOW_MODE=
server`` 由模块级夹具自管）。
"""
from __future__ import annotations

import importlib
import os
import sqlite3
import unittest
import uuid
from contextlib import closing
from pathlib import Path

OWNER = "p1mowner"
MEMBER = "p1mmember"
MEMBER2 = "p1mmember2"
MEMBER3 = "p1mmember3"
ANOTHER_OWNER = "p1mother"

_MEMBER_TEAM_TABLES = (
    "team_flow_receipts", "team_flow_receipts_meta",
    "team_terminal_seals", "team_terminal_facts_meta", "appraisal_records")

_UPSERT_LIBRARY = (
    "INSERT INTO team_vote_revision_meta (singleton, format_version,"
    " library_id, seeded_stage1, seeded_stage2, advances)"
    " VALUES (1, 2, ?, 0, 0, 0) ON CONFLICT(singleton) DO UPDATE"
    " SET library_id=excluded.library_id")
_INSERT_ARTICLE = (
    "INSERT INTO articles (zotero_key, title, is_duplicate_of, import_order)"
    " VALUES (?, ?, NULL, ?)")
_LATCH = "UPDATE workflow_state SET team_latched=1 WHERE singleton=1"

KEY_A = "P1MAP-A"
KEY_B = "P1MAP-B"
KEY_C = "P1MAP-C"
KEY_D = "P1MAP-D"


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


class TeamEntryPrepareMatrixTests(unittest.TestCase):
    """E1-E9 入场族 × 首个团队操作（真实 HTTP、真实建队、零 mock）。"""

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
        cls.auth_mod = auth_mod
        cls.users = {}
        for name in (OWNER, MEMBER, MEMBER2, MEMBER3, ANOTHER_OWNER):
            user = auth_mod.create_user(name, "P1-Entry-Matrix-2026")
            cls.users[name] = {
                "user_id": user["id"], "username": user["username"],
                "token": auth_mod.create_access_token(
                    user["id"], user["username"])}
        cls.tasks_mod = importlib.import_module("coscreen.tasks")

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

    def _new_task(self, owner=OWNER, *, articles=(KEY_A, KEY_B, KEY_C, KEY_D)):
        """真实建任务（server 模式；属主库仅 P04 ledger + articles）。"""
        with self._server_mode():
            resp = self.client.post(
                "/api/tasks",
                json={"name": "p1-entry-" + uuid.uuid4().hex[:8]},
                headers=self._auth(owner))
        self.assertEqual(resp.status_code, 201, resp.text)
        info = self.tasks_mod.find_task(resp.json()["task_id"],
                                        self.main_mod.DATA_DIR)
        self.assertIsNotNone(info)
        slug = self.tasks_mod.slugify_screener
        owner_db = info.dir / f"{slug(self.users[owner]['username'])}.db"
        with closing(sqlite3.connect(str(owner_db))) as conn, conn:
            conn.executemany(_INSERT_ARTICLE,
                             [(key, f"P1 {key}", i)
                              for i, key in enumerate(articles)])
        return info, owner_db

    def _add_articles(self, info, names=()):
        slug = self.tasks_mod.slugify_screener
        for name in names:
            path = info.dir / f"{slug(self.users[name]['username'])}.db"
            if not path.is_file():
                importlib.import_module("coscreen.db").init_db(path)
            with closing(sqlite3.connect(str(path))) as conn, conn:
                conn.executemany(_INSERT_ARTICLE,
                                 [(key, f"P1 {key}", i)
                                  for i, key in enumerate(
                                      (KEY_A, KEY_B, KEY_C, KEY_D))])

    def _member_db(self, info, name):
        slug = self.tasks_mod.slugify_screener(self.users[name]["username"])
        return info.dir / f"{slug}.db"

    def _tables(self, path):
        return self.main_mod._sqlite_tables_present(path)

    def _assert_prepared(self, path, label):
        tables = self._tables(path)
        for table in _MEMBER_TEAM_TABLES:
            self.assertIn(table, tables,
                          f"{label}: 成员库缺 {table}（咽喉 auto-prepare 未生效）")

    def _assert_typed_non_500(self, resp, label):
        """非 2xx 必须结构化（error_code/message_key 或既有 code 键），绝无 500。

        422（Pydantic 请求体形状校验）是框架层**请求**错误、不触及产品写
        路径，允许以其原生形状出现；本闸只针对产品异常泄漏。
        """
        self.assertNotEqual(resp.status_code, 500,
                            f"{label}: 出现未收口 500：{resp.text[:400]}")
        if resp.status_code >= 400 and resp.status_code != 422:
            try:
                detail = resp.json().get("detail")
            except Exception:  # noqa: BLE001 - 非 JSON 也按失败处理
                self.fail(f"{label}: 非 JSON 错误体：{resp.text[:200]}")
            self.assertTrue(
                isinstance(detail, dict)
                and ("error_code" in detail or "code" in detail),
                f"{label}: 错误体不是结构化信封：{detail!r}")

    def _set_business_role_direct(self, info, target_name, role):
        """直调协作库配置业务角色（绕开 HTTP 管理入口——避免触发属主 A
        方案 hook，保持"属主库未准备"的缺口前置）。"""
        collab = importlib.import_module("coscreen.task_collaboration")
        loop = self.users[target_name]
        owner_uid = self.users[OWNER]["user_id"]
        collab.set_business_role(
            self.main_mod._collaboration_store(info),
            actor_user_id=owner_uid, user_id=loop["user_id"],
            business_role=role, task_id=info.task_id)

    def _ensure_owner_role(self, info, role="reconciler"):
        """真实 HTTP 配置属主业务角色（R4 门要求；经管理入口，属主库随之
        经 A 方案 hook 备齐——本夹具只用它作为"已配置"前置）。"""
        self._set_business_role(info, OWNER, OWNER, role)

    def _set_business_role(self, info, actor_name, target_name, role):
        with self._server_mode():
            resp = self.client.put(
                f"/api/tasks/{info.task_id}/collaboration/business-role/"
                f"{self.users[target_name]['user_id']}",
                json={"business_role": role}, headers=self._auth(actor_name))
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def _decide(self, info, name, key, decision="include", stage="screen",
                expect=None):
        route = "decisions" if stage == "screen" else "stage2/decisions"
        resp = self.raw_client.post(
            f"/api/tasks/{info.task_id}/{route}",
            json={"zotero_key": key, "decision": decision,
                  "reason": "", "notes": "", "tags": None},
            headers=self._auth(name))
        if expect is not None:
            self.assertEqual(resp.status_code, expect,
                             (name, key, resp.status_code, resp.text[:400]))
        return resp

    # ==================================================================
    # E3（legacy 首邀邀请口）→ 首个团队写
    # ==================================================================
    def test_e3_legacy_invite_first_write_prepares_and_succeeds(self):
        info, _owner_db = self._new_task()
        with self._server_mode():
            invited = self.client.post(
                f"/api/tasks/{info.task_id}/collaboration/invite",
                json={"username": self.users[MEMBER]["username"],
                      "role": "coder"}, headers=self._auth(OWNER))
        self.assertEqual(invited.status_code, 201, invited.text)
        body = invited.json()
        self.assertNotIn("membership_committed", body)  # 真 legacy 形状
        # 前置：legacy 首邀不准备成员库（缺口态）
        member_db = self._member_db(info, MEMBER)
        self._add_articles(info, (MEMBER,))
        self.assertNotIn("team_flow_receipts", self._tables(member_db),
                         "前置失败：legacy 首邀后成员库已含团队表")
        # R4：属主业务角色已配置（直调协作库，不触发 A 方案 hook）
        self._set_business_role_direct(info, OWNER, "reconciler")
        first = self._decide(info, MEMBER, KEY_A)
        self._assert_typed_non_500(first, "E3 首写")
        self.assertEqual(first.status_code, 200, first.text)
        self._assert_prepared(member_db, "E3 首写后")

    # ==================================================================
    # E5（legacy grant 口，绕过 invite）→ 首个团队写
    # ==================================================================
    def test_e5_legacy_grant_first_write_prepares_and_succeeds(self):
        info, _owner_db = self._new_task()
        with self._server_mode():
            granted = self.client.post(
                f"/api/tasks/{info.task_id}/collaboration/roles",
                json={"user_id": self.users[MEMBER]["user_id"],
                      "role": "coder"}, headers=self._auth(OWNER))
        self.assertEqual(granted.status_code, 201, granted.text)
        self.assertNotIn("membership_committed", granted.json())
        member_db = self._member_db(info, MEMBER)
        self._add_articles(info, (MEMBER,))
        self.assertNotIn("team_flow_receipts", self._tables(member_db))
        self._set_business_role_direct(info, OWNER, "reconciler")
        first = self._decide(info, MEMBER, KEY_B)
        self._assert_typed_non_500(first, "E5 首写")
        self.assertEqual(first.status_code, 200, first.text)
        self._assert_prepared(member_db, "E5 首写后")

    # ==================================================================
    # E4（P18 invite 分支）→ 首个团队写（准备已做，写仍成功；幂等零动作）
    # ==================================================================
    def test_e4_p18_invite_first_write_succeeds(self):
        info, _owner_db = self._new_task()
        # 首邀走 legacy latch；随后 team 分支 invite 进协调器。
        with self._server_mode():
            first = self.client.post(
                f"/api/tasks/{info.task_id}/collaboration/invite",
                json={"username": self.users[MEMBER2]["username"],
                      "role": "coder"}, headers=self._auth(OWNER))
        self.assertEqual(first.status_code, 201, first.text)
        with self._server_mode():
            second = self.client.post(
                f"/api/tasks/{info.task_id}/collaboration/invite",
                json={"username": self.users[MEMBER]["username"],
                      "role": "coder"}, headers=self._auth(OWNER))
        self.assertEqual(second.status_code, 201, second.text)
        self.assertTrue(second.json()["membership_committed"])
        self.assertTrue(second.json()["library_prepared"])
        # 属主自身业务角色（R4 门）+ 成员库补 articles（P18 已备 schema）
        self._set_business_role(info, OWNER, OWNER, "reconciler")
        self._add_articles(info, (MEMBER2, MEMBER))
        member_db = self._member_db(info, MEMBER)
        self.assertIn("team_flow_receipts", self._tables(member_db))
        first_write = self._decide(info, MEMBER, KEY_C)
        self._assert_typed_non_500(first_write, "E4 首写")
        self.assertEqual(first_write.status_code, 200, first_write.text)
        self._assert_prepared(member_db, "E4 首写后")

    # ==================================================================
    # E9（读路径懒建裸库先于写）→ 首个团队写
    # ==================================================================
    def test_e9_read_creates_bare_library_then_first_write_prepares(self):
        info, _owner_db = self._new_task()
        with self._server_mode():
            invited = self.client.post(
                f"/api/tasks/{info.task_id}/collaboration/invite",
                json={"username": self.users[MEMBER]["username"],
                      "role": "coder"}, headers=self._auth(OWNER))
        self.assertEqual(invited.status_code, 201, invited.text)
        # 读路径（context() 懒建裸库）先跑：队列 200、成员库有全量筛选表
        # 但**没有**团队表。
        queue = self.raw_client.get(
            f"/api/tasks/{info.task_id}/work-queue",
            params={"stage": "screen"}, headers=self._auth(MEMBER))
        self.assertIn(queue.status_code, (200, 404), queue.text)
        member_db = self._member_db(info, MEMBER)
        # E9 形状：读路径按需懒建裸成员库（若本路由未触达则显式用 context
        # 走同一懒建链，等价"读先于写"事实）。
        if not member_db.is_file():
            self.client.get(
                f"/api/tasks/{info.task_id}/workflow-settings",
                headers=self._auth(MEMBER))
        self.assertTrue(member_db.is_file(), "读路径应已懒建裸成员库")
        self.assertNotIn("team_flow_receipts", self._tables(member_db))
        self._add_articles(info, (MEMBER,))
        self._set_business_role_direct(info, OWNER, "reconciler")
        first = self._decide(info, MEMBER, KEY_D)
        self._assert_typed_non_500(first, "E9 首写")
        self.assertEqual(first.status_code, 200, first.text)
        self._assert_prepared(member_db, "E9 首写后")

    # ==================================================================
    # E1/E2 形状：属主直调 appraisal（绕过 main.py A 方案 hook）
    # ==================================================================
    def test_e1_owner_direct_appraisal_prepares_owner_library(self):
        info, owner_db = self._new_task()
        with self._server_mode():
            invited = self.client.post(
                f"/api/tasks/{info.task_id}/collaboration/invite",
                json={"username": self.users[MEMBER]["username"],
                      "role": "coder"}, headers=self._auth(OWNER))
        self.assertEqual(invited.status_code, 201, invited.text)
        # 属主切 coder 业务角色（appraisal 是编码者业务写）。直调协作库：
        # HTTP 管理入口会先跑 A 方案 hook 备属主库，破坏"owner 直调
        # appraisal 绕过 A hook"的缺口前置。
        self._set_business_role_direct(info, OWNER, "coder")
        self.assertNotIn("team_flow_receipts", self._tables(owner_db),
                         "前置失败：属主库已含团队表（A hook 不应已触发）")
        put = self.raw_client.put(
            f"/api/tasks/{info.task_id}/appraisal/{KEY_A}",
            json={"tool": "rob2", "responses": {}, "note": ""},
            headers=self._auth(OWNER))
        # 载荷可能因 tool 形状 4xx——但绝不能是 500；若准备生效则属主库已备。
        self._assert_typed_non_500(put, "E1 属主直调 appraisal")
        self._assert_prepared(owner_db, "E1 属主直调 appraisal 后")

    # ==================================================================
    # R4：owner 未配置业务角色 → 筛选写 409 typed（不再裸 500）
    # ==================================================================
    def test_r4_owner_unconfigured_business_role_is_typed_409(self):
        info, _owner_db = self._new_task()
        with self._server_mode():
            invited = self.client.post(
                f"/api/tasks/{info.task_id}/collaboration/invite",
                json={"username": self.users[MEMBER]["username"],
                      "role": "coder"}, headers=self._auth(OWNER))
        self.assertEqual(invited.status_code, 201, invited.text)
        self._add_articles(info, (MEMBER,))
        # owner 未选业务角色：成员首写 = 409 typed + recovery（确定性）
        resp = self.raw_client.post(
            f"/api/tasks/{info.task_id}/decisions",
            json={"zotero_key": KEY_A, "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=self._auth(MEMBER))
        self.assertNotEqual(resp.status_code, 500, resp.text)
        self.assertEqual(resp.status_code, 409, resp.text)
        detail = resp.json()["detail"]
        self.assertEqual(detail["error_code"],
                         "team_owner_business_role_unconfigured")
        self.assertEqual(detail["message_key"],
                         "team.owner_business_role_unconfigured")
        self.assertIs(detail["retryable"], False)
        self.assertIn("recovery", detail)
        self.assertIn("owner_choose_business_role",
                      detail["recovery"]["actions"])
        # 零业务写
        with closing(sqlite3.connect(str(self._member_db(info, MEMBER)))) as conn:
            row = conn.execute(
                "SELECT 1 FROM decisions WHERE zotero_key=?", (KEY_A,)).fetchone()
        self.assertIsNone(row)
        # 解除：属主配置业务角色后同请求 200（红→绿闭环）
        self._set_business_role(info, OWNER, OWNER, "reconciler")
        retry = self._decide(info, MEMBER, KEY_A)
        self.assertEqual(retry.status_code, 200, retry.text)

    # ==================================================================
    # R4 修轮（复审 §1.2 FAIL）：annotation 族同属 F1 roster 构造族
    # （fulltext 观察路径经 _append_one_observation → _roster_and_required），
    # owner 未配置业务角色时必须同样 409 typed，而不是成功写入无属主项
    # roster 的 v2 观察载荷。
    # ==================================================================
    def test_r4_annotation_family_gated_when_owner_unconfigured(self):
        info, _owner_db = self._new_task()
        with self._server_mode():
            invited = self.client.post(
                f"/api/tasks/{info.task_id}/collaboration/invite",
                json={"username": self.users[MEMBER]["username"],
                      "role": "coder"}, headers=self._auth(OWNER))
        self.assertEqual(invited.status_code, 201, invited.text)
        self._add_articles(info, (MEMBER,))
        # 先把成员库团队 schema 备齐（模拟 P18 已准备完成的存量成员）——
        # 否则首次 annotation 会先被咽喉 auto-prepare 的其它前置挡住，无法
        # 单独隔离"R4 门是否覆盖 annotation 族"这一断言点。
        from custom_backend import team_source_lifecycle as tsl
        from custom_backend.appraisal_api import prepare_appraisal_storage_for
        member_db = self._member_db(info, MEMBER)
        prepare_appraisal_storage_for(info, self.users[MEMBER]["username"],
                                      self.main_mod._screener_db)
        tsl.prepare_team_protocol_storage(
            collab_path=self.main_mod._collaboration_store(info),
            source_paths=(member_db,))
        highlight = {"zotero_key": KEY_C, "events": [
            {"kind": "highlight", "page": 1, "text": "t", "note": "",
             "color": "jade", "rects": [[0.1, 0.2, 0.3, 0.4]]}]}
        # (a) member_annotation（fulltext 新建）：409 typed，零写
        resp = self.raw_client.post(
            f"/api/tasks/{info.task_id}/highlights",
            json=highlight, headers=self._auth(MEMBER))
        self.assertNotEqual(resp.status_code, 500, resp.text)
        self.assertEqual(resp.status_code, 409, resp.text)
        detail = resp.json()["detail"]
        self.assertEqual(detail.get("error_code"),
                         "team_owner_business_role_unconfigured",
                         f"annotation 族必须命中 R4 门，实际信封={detail!r}")
        self.assertIs(detail.get("retryable"), False)
        self.assertIn("recovery", detail)
        with closing(sqlite3.connect(str(self._member_db(info, MEMBER)))) as conn:
            rows = conn.execute(
                "SELECT COUNT(*) FROM highlights").fetchone()[0]
        self.assertEqual(rows, 0, "owner 未配置时 annotation 不得写入")
        # (b) member_annotation_note（fulltext note 更新）：同族门亦生效。
        # 需要一行既有 highlight（路由在写窗前按存储行解析保护键，行缺即
        # 404——那是 P20 既有语义，先种子一行再断言 R4 门）。
        with closing(sqlite3.connect(str(self._member_db(info, MEMBER)))) as conn, conn:
            conn.execute(
                "INSERT INTO highlights (zotero_key, page, text, color, note,"
                " created_at) VALUES (?, 1, 't', 'jade', '', ?)",
                (KEY_C, "2026-11-21T00:00:00.000Z"))
            row_id = conn.execute(
                "SELECT id FROM highlights WHERE zotero_key=?", (KEY_C,)
            ).fetchone()[0]
        note_resp = self.raw_client.patch(
            f"/api/tasks/{info.task_id}/highlights/{row_id}",
            json={"zotero_key": KEY_C, "note": "n",
                  "expected_note": ""},
            headers=self._auth(MEMBER))
        self.assertNotEqual(note_resp.status_code, 500, note_resp.text)
        self.assertEqual(note_resp.status_code, 409, note_resp.text)
        self.assertEqual(note_resp.json()["detail"].get("error_code"),
                         "team_owner_business_role_unconfigured")
        # 零写：note 保持原值
        with closing(sqlite3.connect(str(self._member_db(info, MEMBER)))) as conn:
            note_after = conn.execute(
                "SELECT note FROM highlights WHERE id=?", (row_id,)).fetchone()[0]
        self.assertEqual(note_after, "")
        # (c) 绿闭环：属主配置后同族操作放行——先补该键的 stage1 票（fulltext
        # 链前置），再走同一 annotation 新建请求 200。
        self._set_business_role(info, OWNER, OWNER, "reconciler")
        chain = self._decide(info, MEMBER, KEY_C)
        self.assertEqual(chain.status_code, 200, chain.text)
        ok = self.raw_client.post(
            f"/api/tasks/{info.task_id}/highlights",
            json=highlight, headers=self._auth(MEMBER))
        self.assertNotEqual(ok.status_code, 500, ok.text)
        self.assertEqual(ok.status_code, 200, ok.text)

    # ==================================================================
    # R7：legacy invite/grant 两分支响应携带 source_registration（同形）
    # ==================================================================
    def test_r7_legacy_invite_and_grant_carry_source_registration(self):
        info, _owner_db = self._new_task()
        with self._server_mode():
            invited = self.client.post(
                f"/api/tasks/{info.task_id}/collaboration/invite",
                json={"username": self.users[MEMBER]["username"],
                      "role": "coder"}, headers=self._auth(OWNER))
        self.assertEqual(invited.status_code, 201, invited.text)
        invite_body = invited.json()
        self.assertIn("source_registration", invite_body)
        self.assertIn(invite_body["source_registration"]["status"],
                      ("active", "pending"))
        with self._server_mode():
            granted = self.client.post(
                f"/api/tasks/{info.task_id}/collaboration/roles",
                json={"user_id": self.users[MEMBER2]["user_id"],
                      "role": "coder"}, headers=self._auth(OWNER))
        self.assertEqual(granted.status_code, 201, granted.text)
        grant_body = granted.json()
        self.assertIn("source_registration", grant_body)
        self.assertIn(grant_body["source_registration"]["status"],
                      ("active", "pending"))
        # 同形：两分支都有的键集合相同（status 必有；active 时三字段齐全）
        for body in (invite_body, grant_body):
            registration = body["source_registration"]
            self.assertIn("status", registration)
            if registration["status"] == "active":
                for key in ("registration_id", "generation",
                            "source_library_id"):
                    self.assertIn(key, registration)
            else:
                self.assertIn("issue", registration)

    # ==================================================================
    # E 族合取：任何入口的首个团队操作都不允许裸 500（矩阵扫描）
    # ==================================================================
    def test_entry_first_ops_never_bare_500(self):
        info, _owner_db = self._new_task()
        with self._server_mode():
            invited = self.client.post(
                f"/api/tasks/{info.task_id}/collaboration/invite",
                json={"username": self.users[MEMBER3]["username"],
                      "role": "coder"}, headers=self._auth(OWNER))
        self.assertEqual(invited.status_code, 201, invited.text)
        self._add_articles(info, (MEMBER3,))
        self._set_business_role_direct(info, OWNER, "reconciler")
        first_ops = [
            ("screen decide", lambda: self._decide(info, MEMBER3, KEY_A)),
            ("stage2 decide", lambda: self._decide(
                info, MEMBER3, KEY_B, stage="fulltext")),
            ("coding values", lambda: self.raw_client.post(
                f"/api/tasks/{info.task_id}/coding/values",
                json={"zotero_key": KEY_C, "dim_id": 1, "value": "x"},
                headers=self._auth(MEMBER3))),
            ("highlight add", lambda: self.raw_client.post(
                f"/api/tasks/{info.task_id}/highlights",
                json={"zotero_key": KEY_D, "kind": "highlight", "page": 1,
                      "text": "t", "note": "", "color": "jade",
                      "rects": [[0.1, 0.2, 0.3, 0.4]]},
                headers=self._auth(MEMBER3))),
            ("undo screen", lambda: self.raw_client.post(
                f"/api/tasks/{info.task_id}/undo/screen",
                json={"expected_entry_id": "no-such-entry"},
                headers=self._auth(MEMBER3))),
        ]
        for label, call in first_ops:
            resp = call()
            self._assert_typed_non_500(resp, f"E 族首操作 {label}")
        # 至少首写路径已把成员库准备到位（后续操作共享同一成员库）
        self._assert_prepared(self._member_db(info, MEMBER3), "E 族首操作后")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
