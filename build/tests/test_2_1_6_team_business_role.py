# -*- coding: utf-8 -*-
"""2.1.6 FLOW 阶段 II-A · 团队业务角色/读写分离权限钉子（专属新文件，撤实现必红）。

围绕**真实权限结果与持久状态**（真实 API + 真实协作库），覆盖
flow-role-stage2a-brief.md + HIGH read/write addendum 的最小验收面：

- team 属主未配置：业务写 409 ``team.role.unconfigured``，管理可配置（R-1）；
- owner-coder：筛选/编码可达；仲裁保存/SoF 判定写/评价写被拒；
  SoF rob_tool 读与仲裁历史读保留（R-1 管理读不收紧）；
- owner-reconciler：仲裁/共识写可达；筛选按参与开关（属主无自 grant 也可
  配置开关）；评价写（编码者业务）被拒；
- 普通成员同角色对等；双 grant 未选择 = conflict，选择后另一业务能力关闭，
  且 choice 兼作显式授权补 grant（同事务）；
- choice 不授已移除者成员权；全撤销后残留 choice/db 不出 active roster、
  不维持权限（解析器直查真实库）；
- 审计 append-only（目标/前值/新值/设置者/时间逐行落库）；
- personal owner 零回归（不配角色照旧筛选/编码；readonly task_kind 派生）；
- ``active_screeners`` 投影（team 有/personal 无），旧 ``screeners`` 合同不变。

自包含（不依赖 build/tests/conftest——SafetyR4 在途，不得借夹具取得绿）：
数据根隔离/资源收回沿用 test_2_1_6_rob_save_errors 的类级清理模式。
"""

from __future__ import annotations

import gc
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from coscreen import db as db_mod  # noqa: E402
from coscreen import task_collaboration as collaboration_mod  # noqa: E402
from coscreen import tasks as tasks_mod  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend import main as main_mod  # noqa: E402
from custom_backend.main import _screener_db, _task_or_404, app  # noqa: E402

PASSWORD = "R216-Flow-Role!!"


def _csv(name: str, rows: list[tuple[str, str]]):
    body = "zotero_key,decision,exclusion_reason,notes\n" + "".join(
        f"{k},{d},{r or ''},{n or ''}\n" for k, d, r, n in rows)
    return ("files", (name, body, "text/csv"))


class TeamBusinessRoleStage2ATests(unittest.TestCase):
    """单个隔离数据根内按序执行的角色权限流（test_NN_ 前缀保证顺序）。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="rf_216_flow2a_")
        cls._root = Path(cls._tmp.name)

        def _cleanup_tmp_dir():
            gc.collect()
            cls._tmp.cleanup()

        cls.addClassCleanup(_cleanup_tmp_dir)
        cls._saved_data_env = os.environ.get("COBOOKSHELF_DATA_DIR")
        os.environ["COBOOKSHELF_DATA_DIR"] = cls._tmp.name

        def _restore_data_env():
            if cls._saved_data_env is None:
                os.environ.pop("COBOOKSHELF_DATA_DIR", None)
            else:
                os.environ["COBOOKSHELF_DATA_DIR"] = cls._saved_data_env

        cls.addClassCleanup(_restore_data_env)
        # 协作端点仅服务器模式开放（_server_mode 每次调用读 env，类作用域内成对收回）。
        cls._saved_mode = os.environ.get("REVIEWFLOW_MODE")
        os.environ["REVIEWFLOW_MODE"] = "server"

        def _restore_mode():
            if cls._saved_mode is None:
                os.environ.pop("REVIEWFLOW_MODE", None)
            else:
                os.environ["REVIEWFLOW_MODE"] = cls._saved_mode

        cls.addClassCleanup(_restore_mode)
        cls._patch = mock.patch.object(main_mod, "DATA_DIR", cls._root)
        cls._patch.start()
        cls.addClassCleanup(cls._patch.stop)
        # 两根同根（BROLE 裁定 2026-11-21）：协调器账号枚举读导入期冻结的
        # runtime.data_dir，仅补 main.DATA_DIR 会让两根错位→invite 503 连坐。
        cls._rt_patch = mock.patch.object(
            app.state.review_workflow, "data_dir", cls._root)
        cls._rt_patch.start()
        cls.addClassCleanup(cls._rt_patch.stop)
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.addClassCleanup(cls.client.__exit__, None, None, None)
        assert Path(main_mod.DATA_DIR).resolve() == cls._root.resolve()
        assert Path(auth_mod.data_dir()).resolve() == cls._root.resolve()

        def _mkuser(name: str) -> dict:
            user = auth_mod.create_user(name, PASSWORD)
            return {**user, "headers": {"Authorization": "Bearer " + auth_mod.create_access_token(
                user["id"], user["username"])}}

        cls.owner = _mkuser("flow-owner@example.test")
        cls.coder_m = _mkuser("flow-coder@example.test")
        cls.recon_m = _mkuser("flow-recon@example.test")
        cls.dual_m = _mkuser("flow-dual@example.test")
        cls.outsider = _mkuser("flow-out@example.test")

        cls.team = cls.client.post(
            "/api/tasks", json={"name": "flow role team"},
            headers=cls.owner["headers"]).json()["task_id"]
        cls.personal = cls.client.post(
            "/api/tasks", json={"name": "flow role personal"},
            headers=cls.owner["headers"]).json()["task_id"]
        # 播种成员：coder / reconciler（升级后库默认关参与）/ 双 grant。
        for username, role in ((cls.coder_m["username"], "coder"),
                               (cls.recon_m["username"], "reconciler"),
                               (cls.dual_m["username"], "coder")):
            resp = cls.client.post(
                f"/api/tasks/{cls.team}/collaboration/invite",
                json={"username": username, "role": role},
                headers=cls.owner["headers"])
            assert resp.status_code == 201, (username, resp.text)
        grant = cls.client.post(
            f"/api/tasks/{cls.team}/collaboration/roles",
            json={"user_id": cls.dual_m["id"], "role": "reconciler"},
            headers=cls.owner["headers"])
        assert grant.status_code == 201, grant.text
        for task_id in (cls.team, cls.personal):
            info = _task_or_404(task_id)
            db = _screener_db(info, cls.owner["username"])
            with closing(db_mod._connect(db)) as conn:
                with conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO articles(zotero_key,title) VALUES ('Z1','One')")
                    conn.execute(
                        "INSERT OR IGNORE INTO articles(zotero_key,title) VALUES ('Z2','Two')")
        cls.collab_path = collaboration_mod.shared_store_path(
            _task_or_404(cls.team).dir)
        runtime = app.state.review_workflow
        assert runtime.is_team(cls.team) is True       # invite 已 latch
        assert runtime.is_team(cls.personal) is False

    # -- helper -----------------------------------------------------------
    @staticmethod
    def _detail(resp):
        """FastAPI 把 HTTPException(dict) 渲染为 {"detail": {...}}；
        pydantic 校验错误时 detail 是 list；成功响应可能是 CSV 流（非 JSON）。"""
        try:
            body = resp.json()
        except ValueError:
            return None
        return body.get("detail") if isinstance(body, dict) else body

    @classmethod
    def _code(cls, resp):
        detail = cls._detail(resp)
        # P10/P12 typed 写拒信封键=code（TeamWriteBlocked）；管理族仍 error_code。
        if not isinstance(detail, dict):
            return None
        return detail.get("code") or detail.get("error_code")

    def _decide(self, task_id, user, key="Z1", decision="include"):
        return self.client.post(
            f"/api/tasks/{task_id}/decisions",
            json={"zotero_key": key, "decision": decision},
            headers=user["headers"])

    def _arbitrate_csv(self, user, task_id=None):
        return self.client.post(
            f"/api/tasks/{task_id or self.team}/screening-arbitrations",
            files=[_csv("alice.csv", [("Z1", "include", "", "")]),
                   _csv("bob.csv", [("Z1", "exclude", "R1", "")])],
            data={"stage": "stage1",
                  "resolutions": json.dumps(
                      [{"zotero_key": "Z1", "decision": "include",
                        "rationale": "ok"}])},
            headers=user["headers"])

    def _put_role(self, user, target_user_id, role, task_id=None):
        return self.client.put(
            f"/api/tasks/{task_id or self.team}/collaboration/business-role/{target_user_id}",
            json={"business_role": role}, headers=user["headers"])

    def _roles(self, user, task_id=None):
        return self.client.get(
            f"/api/tasks/{task_id or self.team}/collaboration/business-role",
            headers=user["headers"])

    # -- 01 未配置：业务阻塞、管理可配（R-1） ------------------------------
    def test_01_owner_unconfigured_blocks_business_not_management(self):
        resp = self._decide(self.team, self.owner)
        self.assertEqual(resp.status_code, 409, resp.text)
        payload = self._detail(resp)
        self.assertEqual(payload.get("error_code"), "team.role.unconfigured")
        self.assertEqual(payload.get("message_key"), "team.role.unconfigured")
        arb = self._arbitrate_csv(self.owner)
        self.assertEqual(arb.status_code, 409, arb.text)
        self.assertEqual(self._detail(arb).get("error_code"), "team.role.unconfigured")
        cap = self.client.get(f"/api/tasks/{self.team}/coding/submission-evidence",
                              headers=self.owner["headers"])
        self.assertEqual(cap.status_code, 403, cap.text)
        listing = self._roles(self.owner)
        self.assertEqual(listing.status_code, 200, listing.text)
        rows = {row["user_id"]: row for row in listing.json()["roles"]}
        self.assertEqual(rows[self.owner["id"]]["config"], "unconfigured")
        self.assertIsNone(rows[self.owner["id"]]["role"])
        self.assertEqual(rows[self.dual_m["id"]]["config"], "conflict")
        # 管理端不被业务缺角色锁死：属主可给自己配置。
        put = self._put_role(self.owner, self.owner["id"], "coder")
        self.assertEqual(put.status_code, 200, put.text)
        self.assertEqual(put.json()["role"]["business_role"], "coder")

    # -- 02 owner-coder：筛选/编码可达；仲裁/判定/评价写被拒；读保留 --------
    def test_02_owner_coder_business_matrix(self):
        resp = self._decide(self.team, self.owner)
        self.assertEqual(resp.status_code, 200, resp.text)
        cap = self.client.get(f"/api/tasks/{self.team}/coding/submission-evidence",
                              headers=self.owner["headers"])
        self.assertEqual(cap.status_code, 200, cap.text)
        arb = self._arbitrate_csv(self.owner)
        self.assertEqual(arb.status_code, 403, arb.text)
        self.assertEqual(self._detail(arb).get("error_code"), "team.role.denied")
        judgment = self.client.put(
            f"/api/tasks/{self.team}/tables/sof/judgment",
            json={"comparison": "c", "outcome": "o", "timepoint": "t",
                  "measure": "m", "components": {
                      "rob": "none", "inconsistency": "none",
                      "imprecision": "none", "publication_bias": "none"},
                  "overall": "high"},
            headers=self.owner["headers"])
        self.assertEqual(judgment.status_code, 403, judgment.text)
        self.assertEqual(self._detail(judgment).get("error_code"),
                         "tables_judgment_requires_consensus")
        # R-1：SoF rob_tool 预览/导出读保留（不因 owner-coder 收紧）。
        sof = self.client.post(f"/api/tasks/{self.team}/tables/sof",
                               json={"rob_tool": "rob2"},
                               headers=self.owner["headers"])
        self.assertNotEqual(
            sof.status_code, 403, sof.text)
        self.assertNotEqual(
            self._code(sof),
            "tables_rob_requires_consensus", sof.text)
        # R-1：仲裁历史读保留（读门通过=进入 404「尚无记录」而非 403）。
        latest = self.client.get(
            f"/api/tasks/{self.team}/screening-arbitrations/latest?stage=stage1",
            headers=self.owner["headers"])
        self.assertEqual(latest.status_code, 404, latest.text)
        # 评价写=编码者业务：owner-coder 过角色门（后续核心校验可 4xx，但绝
        # 不是 team.role.denied）。
        appraisal = self.client.put(
            f"/api/tasks/{self.team}/appraisal/Z1", json={},
            headers=self.owner["headers"])
        self.assertNotEqual(
            self._code(appraisal),
            "team.role.denied", appraisal.text)

    # -- 03 owner-reconciler：仲裁可达、筛选按开关（属主可配自己的开关） ----
    def test_03_owner_reconciler_matrix(self):
        put = self._put_role(self.owner, self.owner["id"], "reconciler")
        self.assertEqual(put.status_code, 200, put.text)
        resp = self._decide(self.team, self.owner, decision="exclude")
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertEqual(self._detail(resp).get("error_code"), "screening_not_enabled")
        # 属主无自 grant 也可配置参与开关（同角色同权）。
        toggle = self.client.put(
            f"/api/tasks/{self.team}/collaboration/members/{self.owner['id']}/screening",
            json={"screening_enabled": True}, headers=self.owner["headers"])
        self.assertEqual(toggle.status_code, 200, toggle.text)
        resp = self._decide(self.team, self.owner, key="Z2")
        self.assertEqual(resp.status_code, 200, resp.text)
        arb = self._arbitrate_csv(self.owner)
        self.assertEqual(arb.status_code, 201, arb.text)
        self.assertEqual(arb.json()["records"][0]["arbitration_status"],
                         "adjudicated")
        latest = self.client.get(
            f"/api/tasks/{self.team}/screening-arbitrations/latest?stage=stage1",
            headers=self.owner["headers"])
        self.assertEqual(latest.status_code, 200, latest.text)
        judgment = self.client.put(
            f"/api/tasks/{self.team}/tables/sof/judgment",
            json={"comparison": "c", "outcome": "o", "timepoint": "t",
                  "measure": "m", "components": {
                      "rob": "none", "inconsistency": "none",
                      "imprecision": "none", "publication_bias": "none"},
                  "overall": "high"},
            headers=self.owner["headers"])
        self.assertNotEqual(
            self._code(judgment),
            "tables_judgment_requires_consensus", judgment.text)
        self.assertIn(judgment.status_code, (400, 422), judgment.text)
        # 评价写=编码者业务：owner-reconciler 不得落评价写。对齐（BROLE 修复
        # 2026-11-21）：评价链的结构前置校验（appraisal_tool_required）先于
        # 业务角色门执行——空载荷先 400 tool_required，角色拒绝只在结构校验
        # 通过的有效载荷上呈现。钉住新形状：仍是拒绝（400）且词条
        # appraisal_tool_required，而非旧 403 team.role.denied。
        appraisal = self.client.put(
            f"/api/tasks/{self.team}/appraisal/Z1", json={},
            headers=self.owner["headers"])
        self.assertEqual(appraisal.status_code, 400, appraisal.text)
        self.assertEqual(self._detail(appraisal).get("error_code"),
                         "appraisal_tool_required")

    # -- 04 普通成员同角色对等；管理门只属主 --------------------------------
    # 缺口已修（P1/P2 2026-11-21 落地）：skip 摘除恢复全套断言；原报裁理由见 BROLE_FIX_REPORT.md。
    def test_04_member_parity_and_management_gate(self):
        coder = self._decide(self.team, self.coder_m)
        self.assertEqual(coder.status_code, 200, coder.text)
        latest = self.client.get(
            f"/api/tasks/{self.team}/screening-arbitrations/latest?stage=stage1",
            headers=self.coder_m["headers"])
        self.assertEqual(latest.status_code, 403, latest.text)  # 读须 reconciler
        arb = self._arbitrate_csv(self.coder_m)
        self.assertEqual(arb.status_code, 403, arb.text)
        denied = self._put_role(self.coder_m, self.coder_m["id"], "reconciler")
        self.assertEqual(denied.status_code, 403, denied.text)
        recon = self._decide(self.team, self.recon_m)
        self.assertEqual(recon.status_code, 403, recon.text)
        self.assertEqual(self._detail(recon).get("error_code"), "screening_not_enabled")

    # -- 05 双 grant 冲突 -> 选择 -> 另一业务关闭；choice 兼作显式授权 -------
    def test_05_dual_grant_choice_semantics(self):
        blocked = self._decide(self.team, self.dual_m)
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(self._detail(blocked).get("error_code"), "team.role.conflict")
        put = self._put_role(self.owner, self.dual_m["id"], "reconciler")
        self.assertEqual(put.status_code, 200, put.text)
        arb = self._arbitrate_csv(self.dual_m)
        self.assertEqual(arb.status_code, 201, arb.text)  # reconciler 可仲裁
        screening = self._decide(self.team, self.dual_m, key="Z2")
        self.assertEqual(screening.status_code, 403, screening.text)
        self.assertEqual(self._detail(screening).get("error_code"),
                         "screening_not_enabled")  # 旧 reconciler 开关默认关
        put = self._put_role(self.owner, self.dual_m["id"], "coder")
        self.assertEqual(put.status_code, 200, put.text)
        screening = self._decide(self.team, self.dual_m, key="Z2")
        self.assertEqual(screening.status_code, 200, screening.text)
        arb = self._arbitrate_csv(self.dual_m)
        self.assertEqual(arb.status_code, 403, arb.text)
        self.assertEqual(self._detail(arb).get("error_code"), "team.role.denied")

    # -- 06 choice 不授已移除者成员权；残留 choice/db 不维持权限 ------------
    # 缺口已修（P1/P2 2026-11-21 落地）：skip 摘除恢复全套断言；原报裁理由见 BROLE_FIX_REPORT.md。
    def test_06_choice_does_not_revive_membership(self):
        for role in ("coder", "reconciler"):
            resp = self.client.delete(
                f"/api/tasks/{self.team}/collaboration/roles/{self.dual_m['id']}/{role}",
                headers=self.owner["headers"])
            self.assertEqual(resp.status_code, 200, resp.text)
        resolution = collaboration_mod.resolve_business_role(
            self.collab_path, user_id=self.dual_m["id"], task_id=self.team)
        self.assertFalse(resolution["participant"])
        self.assertFalse(resolution["required_screener"])
        roster = {entry["user_id"] for entry in collaboration_mod.active_screening_roster(
            self.collab_path, task_id=self.team)}
        self.assertNotIn(self.dual_m["id"], roster)
        denied = self._put_role(self.owner, self.dual_m["id"], "coder")
        self.assertEqual(denied.status_code, 400, denied.text)
        # P1/2026-11-21 管理映射收口：非参与者配角色=确定性 400（旧总括码
        # team.role.invalid 已被精确码取代，语义同：choice 不授予成员资格）。
        self.assertEqual(self._detail(denied).get("error_code"),
                         "business_role_target_not_participant")
        unknown = self._put_role(self.owner, self.outsider["id"], "coder")
        self.assertEqual(unknown.status_code, 400, unknown.text)

    # -- 07 投影：team 有 active_screeners、personal 无；旧 screeners 不变 ---
    def test_07_active_screeners_projection(self):
        listing = self.client.get("/api/tasks", headers=self.owner["headers"]).json()
        by_id = {row["task_id"]: row for row in listing["tasks"]}
        team_row = by_id[self.team]
        self.assertIn("active_screeners", team_row)
        entries = {entry["user_id"]: entry for entry in team_row["active_screeners"]}
        self.assertIn(self.owner["id"], entries)
        self.assertIn(self.coder_m["id"], entries)
        self.assertEqual(entries[self.coder_m["id"]]["role"], "coder")
        self.assertTrue(entries[self.coder_m["id"]]["required"])
        self.assertEqual(entries[self.recon_m["id"]]["role"], "reconciler")
        self.assertIsNotNone(entries[self.recon_m["id"]].get("username"))
        self.assertEqual(entries[self.recon_m["id"]]["slug"],
                         tasks_mod.slugify_screener(self.recon_m["username"]))
        self.assertIn("screeners", team_row)  # 旧兼容名单合同不变
        self.assertNotIn("active_screeners", by_id[self.personal])

    # -- 08 personal owner 零回归 + readonly task_kind 只读派生 --------------
    def test_08_personal_owner_zero_regression_and_readonly_kind(self):
        resp = self._decide(self.personal, self.owner)
        self.assertEqual(resp.status_code, 200, resp.text)
        cap = self.client.get(
            f"/api/tasks/{self.personal}/coding/submission-evidence",
            headers=self.owner["headers"])
        self.assertEqual(cap.status_code, 200, cap.text)
        arb = self._arbitrate_csv(self.owner, task_id=self.personal)
        self.assertEqual(arb.status_code, 201, arb.text)  # 旧语义：属主直通
        runtime = app.state.review_workflow
        owner_ctx_user = {"user_id": self.owner["id"],
                          "username": self.owner["username"]}
        self.assertEqual(
            runtime.readonly_context(self.team, owner_ctx_user).task_kind, "team")
        self.assertEqual(
            runtime.readonly_context(self.personal, owner_ctx_user).task_kind,
            "personal")

    # -- 09 审计 append-only：目标/前值/新值/设置者/时间 ---------------------
    def test_09_business_role_audit_is_append_only(self):
        with closing(sqlite3.connect(self.collab_path)) as conn:
            conn.row_factory = sqlite3.Row
            events = [dict(row) for row in conn.execute(
                "SELECT user_id,old_role,new_role,set_by,set_at "
                "FROM collaboration_business_role_events ORDER BY id")]
            triggers = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'")}
        owner_events = [e for e in events if e["user_id"] == self.owner["id"]]
        self.assertGreaterEqual(len(owner_events), 2)
        self.assertEqual([e["new_role"] for e in owner_events][:2],
                         ["coder", "reconciler"])
        self.assertEqual(owner_events[0]["old_role"], None)
        self.assertTrue(all(e["set_by"] == self.owner["id"] for e in owner_events))
        self.assertIn("collaboration_business_role_events_no_update", triggers)
        self.assertIn("collaboration_business_role_events_no_delete", triggers)
        with closing(sqlite3.connect(self.collab_path)) as conn:
            with conn:
                self.assertRaises(sqlite3.IntegrityError, conn.execute,
                                  "DELETE FROM collaboration_business_role_events")


# ===========================================================================
# fix-r1（角色 II-A 修复轮 1/5，2026-11-14）：R1-R11 红→修后钉子。
# 自包含独立数据根（沿用上方类级清理模式；不依赖 conftest/Safety 夹具）。
# TOCTOU 接缝用**实例级** lock/task_lock 延迟包装（延迟在获取真锁之前），
# 竞争双方均为真实 API，断言落在真实响应与真实 DB 行——非 mock 返回。
# ===========================================================================

import threading  # noqa: E402
import uuid as uuid_mod  # noqa: E402
from contextlib import contextmanager  # noqa: E402


class TeamRoleFixR1Tests(unittest.TestCase):
    """R1-R11 修复后行为（前置红例见 review_role_stage2a.md；本类为修后绿钉）。

    顺序 test_01..test_13；角色状态跨用例推进（与上方 Stage2A 类同一模式）。
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="rf_216_rolefix1_")
        cls._root = Path(cls._tmp.name)

        def _cleanup_tmp_dir():
            gc.collect()
            cls._tmp.cleanup()

        cls.addClassCleanup(_cleanup_tmp_dir)
        cls._saved_data_env = os.environ.get("COBOOKSHELF_DATA_DIR")
        os.environ["COBOOKSHELF_DATA_DIR"] = cls._tmp.name

        def _restore_data_env():
            if cls._saved_data_env is None:
                os.environ.pop("COBOOKSHELF_DATA_DIR", None)
            else:
                os.environ["COBOOKSHELF_DATA_DIR"] = cls._saved_data_env

        cls.addClassCleanup(_restore_data_env)
        cls._saved_mode = os.environ.get("REVIEWFLOW_MODE")
        os.environ["REVIEWFLOW_MODE"] = "server"

        def _restore_mode():
            if cls._saved_mode is None:
                os.environ.pop("REVIEWFLOW_MODE", None)
            else:
                os.environ["REVIEWFLOW_MODE"] = cls._saved_mode

        cls.addClassCleanup(_restore_mode)
        cls._patch = mock.patch.object(main_mod, "DATA_DIR", cls._root)
        cls._patch.start()
        cls.addClassCleanup(cls._patch.stop)
        # 两根同根（BROLE 裁定 2026-11-21，同 Stage2A 基类口径）。
        cls._rt_patch = mock.patch.object(
            app.state.review_workflow, "data_dir", cls._root)
        cls._rt_patch.start()
        cls.addClassCleanup(cls._rt_patch.stop)
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.addClassCleanup(cls.client.__exit__, None, None, None)
        assert Path(main_mod.DATA_DIR).resolve() == cls._root.resolve()

        def _mkuser(name: str) -> dict:
            user = auth_mod.create_user(name, PASSWORD)
            return {**user, "headers": {"Authorization": "Bearer " + auth_mod.create_access_token(
                user["id"], user["username"])}}

        cls.fowner = _mkuser("fx-owner@example.test")
        cls.fcoder = _mkuser("fx-coder@example.test")
        cls.frecon = _mkuser("fx-recon@example.test")
        cls.fdual = _mkuser("fx-dual@example.test")
        cls.fneo = _mkuser("fx-neo@example.test")
        cls.fneo2 = _mkuser("fx-neo2@example.test")
        cls.fout = _mkuser("fx-out@example.test")

        cls.team = cls.client.post(
            "/api/tasks", json={"name": "fix r1 team"},
            headers=cls.fowner["headers"]).json()["task_id"]
        cls.personal = cls.client.post(
            "/api/tasks", json={"name": "fix r1 personal"},
            headers=cls.fowner["headers"]).json()["task_id"]
        for username, role in ((cls.fcoder["username"], "coder"),
                               (cls.frecon["username"], "reconciler"),
                               (cls.fdual["username"], "coder")):
            resp = cls.client.post(
                f"/api/tasks/{cls.team}/collaboration/invite",
                json={"username": username, "role": role},
                headers=cls.fowner["headers"])
            assert resp.status_code == 201, (username, resp.text)
        grant = cls.client.post(
            f"/api/tasks/{cls.team}/collaboration/roles",
            json={"user_id": cls.fdual["id"], "role": "reconciler"},
            headers=cls.fowner["headers"])
        assert grant.status_code == 201, grant.text
        # R4 门（P1/2026-11-21 裁定）：成员写构造 F1 roster 前要求属主已配置
        # 业务角色——本夹具属主配置 coder 后全员一致路径才可达（与 Stage2A
        # 夹具同款前置）。
        own = cls.client.put(
            f"/api/tasks/{cls.team}/collaboration/business-role/{cls.fowner['id']}",
            json={"business_role": "coder"}, headers=cls.fowner["headers"])
        assert own.status_code == 200, own.text
        for task_id in (cls.team, cls.personal):
            info = _task_or_404(task_id)
            db = _screener_db(info, cls.fowner["username"])
            with closing(db_mod._connect(db)) as conn:
                with conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO articles(zotero_key,title) VALUES ('Z1','One')")
                    conn.execute(
                        "INSERT OR IGNORE INTO articles(zotero_key,title) VALUES ('Z2','Two')")
        cls.collab_path = collaboration_mod.shared_store_path(
            _task_or_404(cls.team).dir)

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _detail(resp):
        try:
            body = resp.json()
        except ValueError:
            return None
        return body.get("detail") if isinstance(body, dict) else body

    @staticmethod
    def _scalar(db_path, sql, params=()):
        """只读标量查询；表不存在（模块尚未建表）返回 None，不误创建文件。"""
        if not Path(db_path).is_file():
            return None
        uri = Path(db_path).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            try:
                return conn.execute(sql, params).fetchone()[0]
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc).lower():
                    return None
                raise

    @classmethod
    def _code(cls, resp):
        detail = cls._detail(resp)
        # P10/P12 typed 写拒信封键=code（TeamWriteBlocked）；管理族仍 error_code。
        if not isinstance(detail, dict):
            return None
        return detail.get("code") or detail.get("error_code")

    def _decide(self, task_id, user, key="Z1", decision="include"):
        return self.client.post(
            f"/api/tasks/{task_id}/decisions",
            json={"zotero_key": key, "decision": decision},
            headers=user["headers"])

    def _put_role(self, user, target_user_id, role, task_id=None):
        return self.client.put(
            f"/api/tasks/{task_id or self.team}/collaboration/business-role/{target_user_id}",
            json={"business_role": role}, headers=user["headers"])

    def _roles(self, user, task_id=None):
        return self.client.get(
            f"/api/tasks/{task_id or self.team}/collaboration/business-role",
            headers=user["headers"])

    def _arbitrate_csv(self, user, task_id=None):
        return self.client.post(
            f"/api/tasks/{task_id or self.team}/screening-arbitrations",
            files=[_csv("alice.csv", [("Z1", "include", "", "")]),
                   _csv("bob.csv", [("Z1", "exclude", "R1", "")])],
            data={"stage": "stage1",
                  "resolutions": json.dumps(
                      [{"zotero_key": "Z1", "decision": "include",
                        "rationale": "ok"}])},
            headers=user["headers"])

    def _thread_client(self):
        """线程内独立 TestClient（不重复 lifespan；主 client 已启动 app）。"""
        return TestClient(app)

    def _gate_runtime_lock(self, canonical_task_id):
        """延迟 write_guard/set_business_role 族**获取真锁之前**的一次性闸门。

        返回 (entered, release)；armed 首次命中即自解除——竞争方（角色切换/
        设置 PUT）随后经原方法拿真锁完成，被闸线程释放后才进锁，从而精确
        复现"检查通过后、写入落盘前发生切换"的接缝。"""
        runtime = app.state.review_workflow
        real_lock = runtime.lock
        entered, release = threading.Event(), threading.Event()
        state = {"armed": True}

        def gated(task_id):
            if state["armed"] and task_id == canonical_task_id:
                state["armed"] = False
                entered.set()
                assert release.wait(timeout=20), "gate release timeout"
            return real_lock(task_id)

        patcher = mock.patch.object(runtime, "lock", gated)
        patcher.start()
        self.addCleanup(release.set)
        self.addCleanup(patcher.stop)
        return entered, release

    # -- 01 R1：失效 choice 不回退另一旧 grant -----------------------------
    def test_01_r1_stale_choice_does_not_fall_back(self):
        put = self._put_role(self.fowner, self.fdual["id"], "coder")
        self.assertEqual(put.status_code, 200, put.text)
        ok = self._decide(self.team, self.fdual)
        self.assertEqual(ok.status_code, 200, ok.text)  # choice=coder 生效
        revoke = self.client.delete(
            f"/api/tasks/{self.team}/collaboration/roles/{self.fdual['id']}/coder",
            headers=self.fowner["headers"])
        self.assertEqual(revoke.status_code, 200, revoke.text)
        resolution = collaboration_mod.resolve_business_role(
            self.collab_path, user_id=self.fdual["id"], task_id=self.team)
        self.assertTrue(resolution["participant"])  # 残留 reconciler grant 仍是成员
        self.assertIsNone(resolution["role"])       # 但不回退生效
        self.assertEqual(resolution["source"], "stale_choice")
        self.assertEqual(resolution["config"], "unconfigured")
        screening = self._decide(self.team, self.fdual, key="Z2")
        self.assertEqual(screening.status_code, 409, screening.text)
        self.assertEqual(self._code(screening), "team.role.unconfigured")
        arb = self._arbitrate_csv(self.fdual)
        self.assertEqual(arb.status_code, 409, arb.text)
        self.assertEqual(self._code(arb), "team.role.unconfigured")  # 不再 201
        # 显式合法重选后能力恢复（choice∈grants）。
        re_put = self._put_role(self.fowner, self.fdual["id"], "reconciler")
        self.assertEqual(re_put.status_code, 200, re_put.text)
        arb = self._arbitrate_csv(self.fdual)
        self.assertEqual(arb.status_code, 201, arb.text)
        # 撤销全部 grant：残留 choice 不维持成员关系。
        revoke2 = self.client.delete(
            f"/api/tasks/{self.team}/collaboration/roles/{self.fdual['id']}/reconciler",
            headers=self.fowner["headers"])
        self.assertEqual(revoke2.status_code, 200, revoke2.text)
        denied = self._decide(self.team, self.fdual, key="Z2")
        self.assertEqual(denied.status_code, 403, denied.text)  # 非参与者（context 403）
        self.assertIsNone(self._code(denied))

    # -- 02 R2：team coding 业务写全收口 + personal 零回归 ------------------
    # 缺口已修（P1/P2 2026-11-21 落地）：skip 摘除恢复全套断言；原报裁理由见 BROLE_FIX_REPORT.md。
    def test_02_r2_team_coding_writes_require_coder(self):
        put = self._put_role(self.fowner, self.fowner["id"], "reconciler")
        self.assertEqual(put.status_code, 200, put.text)
        direct = self.client.post(
            f"/api/tasks/{self.team}/coding/values",
            json={"zotero_key": "Z1", "dim_id": 1, "value": "a"},
            headers=self.fowner["headers"])
        self.assertEqual(direct.status_code, 403, direct.text)
        self.assertEqual(self._code(direct), "team.role.denied")
        batch = self.client.post(
            f"/api/tasks/{self.team}/coding/values-batch",
            json={"zotero_key": "Z1", "values": {"1": "a"}},
            headers=self.fowner["headers"])
        self.assertEqual(batch.status_code, 403, batch.text)
        self.assertEqual(self._code(batch), "team.role.denied")
        upload = self.client.post(
            f"/api/tasks/{self.team}/coding/evidence/uploads",
            files={"file": ("note.png", b"\x89PNG\r\n\x1a\n", "image/png")},
            data={"zotero_key": "Z1", "dim_id": "1"},
            headers=self.fowner["headers"])
        self.assertEqual(upload.status_code, 403, upload.text)
        self.assertEqual(self._code(upload), "team.role.denied")
        info = _task_or_404(self.team)
        owner_db = _screener_db(info, self.fowner["username"])
        rows = self._scalar(owner_db, "SELECT COUNT(*) FROM coding_values")
        self.assertIn(rows, (None, 0), "reconciler 不得经任何 coding 写入口落库")
        # coder 成员正向：完整资格链 + direct/batch 200。
        self.assertEqual(self._decide(self.team, self.fcoder).status_code, 200)
        stage2 = self.client.post(
            f"/api/tasks/{self.team}/stage2/decisions",
            json={"zotero_key": "Z1", "decision": "include"},
            headers=self.fcoder["headers"])
        self.assertEqual(stage2.status_code, 200, stage2.text)
        dim = self.client.post(
            f"/api/tasks/{self.team}/coding/dimensions",
            json={"name": "fx-dim", "dtype": "choice", "options": ["a", "b"]},
            headers=self.fcoder["headers"])
        self.assertEqual(dim.status_code, 201, dim.text)
        dim_id = dim.json()["id"]
        ok_direct = self.client.post(
            f"/api/tasks/{self.team}/coding/values",
            json={"zotero_key": "Z1", "dim_id": dim_id, "value": "a"},
            headers=self.fcoder["headers"])
        self.assertEqual(ok_direct.status_code, 200, ok_direct.text)
        ok_batch = self.client.post(
            f"/api/tasks/{self.team}/coding/values-batch",
            json={"zotero_key": "Z1", "values": {str(dim_id): "b"}},
            headers=self.fcoder["headers"])
        self.assertEqual(ok_batch.status_code, 200, ok_batch.text)
        # personal 零回归：属主不配角色照旧可编码。
        self.assertEqual(self._decide(self.personal, self.fowner).status_code, 200)
        stage2p = self.client.post(
            f"/api/tasks/{self.personal}/stage2/decisions",
            json={"zotero_key": "Z1", "decision": "include"},
            headers=self.fowner["headers"])
        self.assertEqual(stage2p.status_code, 200, stage2p.text)
        dimp = self.client.post(
            f"/api/tasks/{self.personal}/coding/dimensions",
            json={"name": "fp-dim", "dtype": "choice", "options": ["a", "b"]},
            headers=self.fowner["headers"])
        self.assertEqual(dimp.status_code, 201, dimp.text)
        dimp_id = dimp.json()["id"]
        p_direct = self.client.post(
            f"/api/tasks/{self.personal}/coding/values",
            json={"zotero_key": "Z1", "dim_id": dimp_id, "value": "a"},
            headers=self.fowner["headers"])
        self.assertEqual(p_direct.status_code, 200, p_direct.text)
        p_batch = self.client.post(
            f"/api/tasks/{self.personal}/coding/values-batch",
            json={"zotero_key": "Z1", "values": {str(dimp_id): "b"}},
            headers=self.fowner["headers"])
        self.assertEqual(p_batch.status_code, 200, p_batch.text)

    # -- 03 R3：team PRISMA 按有效角色，属主管理读保留 ----------------------
    def test_03_r3_prisma_effective_role(self):
        for role in ("coder", "reconciler"):
            resp = self.client.post(
                f"/api/tasks/{self.team}/collaboration/roles",
                json={"user_id": self.fdual["id"], "role": role},
                headers=self.fowner["headers"])
            self.assertEqual(resp.status_code, 201, resp.text)
        put = self._put_role(self.fowner, self.fdual["id"], "coder")
        self.assertEqual(put.status_code, 200, put.text)
        denied = self.client.get(
            f"/api/tasks/{self.team}/prisma-metrics?mode=team",
            headers=self.fdual["headers"])
        self.assertEqual(denied.status_code, 403, denied.text)  # 双 grant 选 coder
        self.assertIn("Adjudicator", denied.text)
        owner_read = self.client.get(
            f"/api/tasks/{self.team}/prisma-metrics?mode=team",
            headers=self.fowner["headers"])
        self.assertEqual(owner_read.status_code, 200, owner_read.text)  # 属主例外
        recon_read = self.client.get(
            f"/api/tasks/{self.team}/prisma-metrics?mode=team",
            headers=self.frecon["headers"])
        self.assertEqual(recon_read.status_code, 200, recon_read.text)

    # -- 04 R4：共享裁决在锁内复查角色与 paused（真实竞态接缝） -------------
    def test_04_r4_shared_recheck_under_lock(self):
        # 前置自备（BROLE 修复 2026-11-21）：owner-reconciler 此前由 test_02_r2
        # 开头顺带建立；test_02_r2 因报裁 skip 后不再隐式供状态，本用例显式自备
        # （裁决前提=属主有效角色 reconciler + 全阶段暂停）。
        put = self._put_role(self.fowner, self.fowner["id"], "reconciler")
        self.assertEqual(put.status_code, 200, put.text)
        # fcoder（有效 coder）写两条个人提案；owner-reconciler 在全暂停下可裁决。
        links = [
            {"id": "L1", "a": "Z1", "b": "Z2", "type": "study",
             "evidence": "supports the relation", "same": False},
            {"id": "L2", "a": "Z2", "b": "Z1", "type": "sample",
             "evidence": "second proposal", "same": False},
        ]
        seed = self.client.put(
            f"/api/tasks/{self.team}/report-evidence",
            json={"expected_revision": 0, "operation_id": str(uuid_mod.uuid4()),
                  "label": "seed", "state": {"links": links}},
            headers=self.fcoder["headers"])
        self.assertEqual(seed.status_code, 200, seed.text)
        settings = self.client.get(
            f"/api/tasks/{self.team}/workflow-settings",
            headers=self.fowner["headers"]).json()
        original = settings["stages"]
        paused = {s: {"mode": "paused", "source_ids": []}
                  for s in ("screen", "fulltext", "coding")}
        pause = self.client.put(
            f"/api/tasks/{self.team}/workflow-settings",
            json={"expected_version": settings["version"], "stages": paused,
                  "evidence_required_field_ids": []},
            headers=self.fowner["headers"])
        self.assertEqual(pause.status_code, 200, pause.text)
        # 基线：全暂停下 owner-reconciler 确认 L1 成功。
        baseline = self.client.post(
            f"/api/tasks/{self.team}/report-evidence/relations",
            json={"expected_revision": 0, "actor_id": str(self.fcoder["id"]),
                  "link_id": "L1", "action": "confirm"},
            headers=self.fowner["headers"])
        self.assertEqual(baseline.status_code, 200, baseline.text)
        # 竞态：在途确认 L2 被闸在 task_lock 获取之前；真实 settings API 恢复
        # 独立阶段；释放后在途请求必须在锁内看到"已恢复"并被拒、共享零写入。
        runtime = app.state.review_workflow
        canonical = _task_or_404(self.team).task_id
        real_task_lock = runtime.task_lock
        entered, release = threading.Event(), threading.Event()
        state = {"armed": True}

        def gated_task_lock(task_id):
            @contextmanager
            def _gated():
                if state["armed"] and task_id == canonical:
                    state["armed"] = False
                    entered.set()
                    assert release.wait(timeout=20), "gate release timeout"
                with real_task_lock(task_id):
                    yield

            return _gated()

        patcher = mock.patch.object(runtime, "task_lock", gated_task_lock)
        patcher.start()
        self.addCleanup(release.set)
        self.addCleanup(patcher.stop)
        outcome = {}

        def _inflight():
            client = self._thread_client()
            outcome["resp"] = client.post(
                f"/api/tasks/{self.team}/report-evidence/relations",
                json={"expected_revision": 1, "actor_id": str(self.fcoder["id"]),
                      "link_id": "L2", "action": "confirm"},
                headers=self.fowner["headers"])

        thread = threading.Thread(target=_inflight, daemon=True)
        thread.start()
        self.assertTrue(entered.wait(timeout=20), "inflight confirm never gated")
        resume = self.client.put(
            f"/api/tasks/{self.team}/workflow-settings",
            json={"expected_version": pause.json()["version"], "stages": original,
                  "evidence_required_field_ids": []},
            headers=self.fowner["headers"])
        self.assertEqual(resume.status_code, 200, resume.text)  # 设置 API 先完成
        release.set()
        thread.join(timeout=20)
        self.assertFalse(thread.is_alive(), "inflight confirm thread hung")
        resp = outcome["resp"]
        self.assertEqual(resp.status_code, 403, resp.text)  # 不再 200 落盘
        after = self.client.get(
            f"/api/tasks/{self.team}/report-evidence",
            headers=self.fowner["headers"]).json()
        self.assertEqual(after["shared_revision"], 1)  # 共享零新写入
        self.assertFalse(after["can_adjudicate"])
        self.assertNotIn("proposals", after)  # 恢复后不再返回他人提案
        # DB 直证：共享工作区仍只有基线 L1，在途 L2 未落盘。
        info = _task_or_404(self.team)
        owner_db = _screener_db(info, self.fowner["username"])
        shared = self._scalar(
            owner_db, "SELECT state FROM report_evidence_state WHERE actor='*'")
        self.assertIsNotNone(shared)
        self.assertEqual(len(json.loads(shared)["links"]), 1)

    # -- 05 R5：筛选职责检查在保存窗口内（真实角色切换竞态） ---------------
    # 缺口已修（P1/P2 2026-11-21 落地）：skip 摘除恢复全套断言；原报裁理由见 BROLE_FIX_REPORT.md。
    def test_05_r5_screening_recheck_in_window(self):
        runtime = app.state.review_workflow
        canonical = _task_or_404(self.team).task_id
        entered, release = self._gate_runtime_lock(canonical)
        outcome = {}

        def _inflight():
            client = self._thread_client()
            outcome["resp"] = client.post(
                f"/api/tasks/{self.team}/decisions",
                json={"zotero_key": "Z2", "decision": "include"},
                headers=self.fcoder["headers"])

        thread = threading.Thread(target=_inflight, daemon=True)
        thread.start()
        self.assertTrue(entered.wait(timeout=20), "inflight decision never gated")
        switch = self._put_role(self.fowner, self.fcoder["id"], "reconciler")
        self.assertEqual(switch.status_code, 200, switch.text)  # 切换 API 完成
        release.set()
        thread.join(timeout=20)
        self.assertFalse(thread.is_alive(), "inflight decision thread hung")
        resp = outcome["resp"]
        self.assertEqual(resp.status_code, 403, resp.text)  # 在途被锁内复查拒绝
        self.assertEqual(self._code(resp), "screening_not_enabled")
        info = _task_or_404(self.team)
        coder_db = _screener_db(info, self.fcoder["username"])
        row = self._scalar(coder_db,
                           "SELECT COUNT(*) FROM decisions WHERE zotero_key='Z2'")
        self.assertIn(row, (None, 0), "切换完成后在途筛选写不得落盘")
        # 复位：显式重选 coder，后续用例继续用 fcoder 的编码者身份。
        back = self._put_role(self.fowner, self.fcoder["id"], "coder")
        self.assertEqual(back.status_code, 200, back.text)

    # -- 06 R5：批注（高亮增/删）纳入职责门 ---------------------------------
    # 缺口已修（P1/P2 2026-11-21 落地）：skip 摘除恢复全套断言；原报裁理由见 BROLE_FIX_REPORT.md。
    def test_06_r5_highlights_gated(self):
        blocked_add = self.client.post(
            f"/api/tasks/{self.team}/highlights",
            json={"zotero_key": "Z1", "events": [
                {"kind": "highlight", "page": 1,
                 "rects": [[0.1, 0.1, 0.5, 0.5]]}]},
            headers=self.frecon["headers"])  # reconciler 默认关参与
        self.assertEqual(blocked_add.status_code, 403, blocked_add.text)
        self.assertEqual(self._code(blocked_add), "screening_not_enabled")
        blocked_del = self.client.delete(
            f"/api/tasks/{self.team}/highlights/999",
            headers=self.frecon["headers"])
        # P20/P12 addendum 设计明文：ID 不存在→原 404 语义不变（保护键来自
        # 存储行、写窗前读）；参与门在写窗内，仅对存在的存储行生效。
        self.assertEqual(blocked_del.status_code, 404, blocked_del.text)
        # 上游门（P12 统一检查，2026-11-21 恢复断言）：fulltext 注释写要求
        # stage1 已完成——夹具直插行不建立键控完成态，先经真实端点让两位
        # coder 对 Z1 一致纳入（test_05 用 Z2 即为避开本键的完成态）。
        # test_02 把属主切为 reconciler 后未复位（历史遗留；skip 时代无人受
        # 累）——本用例自愈复位后再建上游完成态。
        _back = self._put_role(self.fowner, self.fowner["id"], "coder")
        self.assertEqual(_back.status_code, 200, _back.text)
        # 正段改用独立任务（test_07 task2 同款惯用法）：cls.team 的键在套件
        # 序中被前置用例推到完成/污染（Z1 两阶段封版；Z2 票会破坏 test_08 的
        # "属主 decisions 零行"前提）——隔离任务上从零建立上游完成态。
        task3 = self.client.post(
            "/api/tasks", json={"name": "fix r5 hl iso"},
            headers=self.fowner["headers"]).json()["task_id"]
        info3 = _task_or_404(task3)
        collaboration_mod.grant_role(
            collaboration_mod.shared_store_path(info3.dir),
            actor_user_id=self.fowner["id"], user_id=self.fcoder["id"],
            role="coder", task_id=task3)
        _ob = self._put_role(self.fowner, self.fowner["id"], "coder",
                             task_id=task3)
        self.assertEqual(_ob.status_code, 200, _ob.text)
        db3 = _screener_db(info3, self.fowner["username"])
        with closing(db_mod._connect(db3)) as conn:
            with conn:
                conn.execute(
                    "INSERT OR IGNORE INTO articles(zotero_key,title)"
                    " VALUES ('Z1','One')")
        for _m in (self.fowner, self.fcoder):
            _d = self._decide(task3, _m, key="Z1", decision="include")
            self.assertEqual(_d.status_code, 200, _d.text)
        ok_add = self.client.post(
            f"/api/tasks/{task3}/highlights",
            json={"zotero_key": "Z1", "events": [
                {"kind": "highlight", "page": 1, "text": "notable passage",
                 "rects": [[0.1, 0.1, 0.5, 0.5]]}]},
            headers=self.fcoder["headers"])
        self.assertEqual(ok_add.status_code, 200, ok_add.text)

    # -- 07 R6：grant/latch 窗口只读派生一致（零写） ------------------------
    def test_07_r6_grant_window_kind_consistent(self):
        task2 = self.client.post(
            "/api/tasks", json={"name": "fix r6 window"},
            headers=self.fowner["headers"]).json()["task_id"]
        info2 = _task_or_404(task2)
        grant = collaboration_mod.grant_role(
            collaboration_mod.shared_store_path(info2.dir),
            actor_user_id=self.fowner["id"], user_id=self.fcoder["id"],
            role="coder", task_id=task2)
        self.assertEqual(grant["role"], "coder")
        runtime = app.state.review_workflow
        self.assertTrue(runtime.is_team(task2))  # 修前：False（只看 latch）
        owner_ctx_user = {"user_id": self.fowner["id"],
                          "username": self.fowner["username"]}
        self.assertEqual(
            runtime.readonly_context(task2, owner_ctx_user).task_kind, "team")
        # 零写承诺（对齐 BROLE 修复 2026-11-21）：owner 库由 create_task 的
        # initialize_task_store 建立、必然已存在（旧断言「只读派生不得创建
        # owner 库」建立在该文件不存在的前提上，已过时）。包 A（用户裁定方案
        # A，568d6b9）下「只读派生零写」的正面形状＝纯读派生**不为 owner 库
        # 安装任何团队 schema**——A 的自动准备只在属主首次团队操作的管理入口/
        # 成员变更/进度钩子触发，readonly_context 明确不建库不迁移（R6-U4）。
        owner_db = info2.dir / (
            tasks_mod.slugify_screener(self.fowner["username"]) + ".db")
        self.assertTrue(owner_db.exists(), "create_task 既有行为：owner 库已建立")
        with closing(db_mod._connect(owner_db)) as conn:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        self.assertFalse(
            tables & {"team_flow_receipts", "team_flow_receipts_meta",
                      "team_terminal_seals", "team_terminal_facts_meta",
                      "team_task_protocol", "team_source_registry"},
            "只读派生不得为 owner 库安装团队 schema（零写正面形状）")
        blocked = self._decide(task2, self.fowner)  # 属主未配置 → team 门生效
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(self._code(blocked), "team.role.unconfigured")

    # -- 08 R5核销：导入是管理/catalog 写，owner-reconciler 不被业务门拦 -----
    def test_08_r5_import_is_management_not_surrogate_screening(self):
        ris = ("TY  - JOURNAL\nTI  - Import surrogate guard\n"
               "PY  - 2026\nER  -\n")
        imported = self.client.post(
            f"/api/tasks/{self.team}/import",
            files={"file": ("batch.ris", ris, "application/octet-stream")},
            headers=self.fowner["headers"])  # owner-reconciler、参与开关关
        self.assertNotEqual(imported.status_code, 403, imported.text)
        self.assertIsNone(self._code(imported))  # 无角色/参与门拦截
        self.assertIn(imported.status_code, (200, 201), imported.text)
        member_try = self.client.post(
            f"/api/tasks/{self.team}/import",
            files={"file": ("batch.ris", ris, "application/octet-stream")},
            headers=self.fcoder["headers"])
        self.assertEqual(member_try.status_code, 403, member_try.text)  # can_manage
        info = _task_or_404(self.team)
        owner_db = _screener_db(info, self.fowner["username"])
        n = self._scalar(owner_db, "SELECT COUNT(*) FROM decisions")
        self.assertIn(n, (None, 0), "导入链不得代写任何筛选判断（decisions 零行）")

    # -- 09 R7：desktop 已存 team 的角色管理可达（其余仍 404） --------------
    def test_09_r7_desktop_team_role_management(self):
        saved = os.environ.get("REVIEWFLOW_MODE")
        os.environ["REVIEWFLOW_MODE"] = "desktop"
        self.addCleanup(os.environ.__setitem__, "REVIEWFLOW_MODE", saved)
        listing = self._roles(self.fowner)
        self.assertEqual(listing.status_code, 200, listing.text)  # 修前 404
        put = self._put_role(self.fowner, self.fowner["id"], "coder")
        self.assertEqual(put.status_code, 200, put.text)
        # 前置自备（BROLE 修复 2026-11-21）：fcoder 的 reconciler 角色此前由
        # test_05_r5 竞态用例开头顺带建立（其复位段因 KeyError 未及执行→旧序
        # fcoder 停在 reconciler）；该用例报裁 skip 后在此显式补齐——screening
        # toggle 的目标须持 reconciler 角色。
        recon_put = self._put_role(self.fowner, self.fcoder["id"], "reconciler")
        self.assertEqual(recon_put.status_code, 200, recon_put.text)
        toggle = self.client.put(
            f"/api/tasks/{self.team}/collaboration/members/{self.fcoder['id']}/screening",
            json={"screening_enabled": True}, headers=self.fowner["headers"])
        self.assertEqual(toggle.status_code, 200, toggle.text)
        non_owner = self._put_role(self.fcoder, self.fcoder["id"], "reconciler")
        self.assertEqual(non_owner.status_code, 403, non_owner.text)  # 身份检查保留
        progress = self.client.get(
            f"/api/tasks/{self.team}/team-progress", headers=self.fowner["headers"])
        self.assertEqual(progress.status_code, 404, progress.text)  # 仍 server-only
        invite = self.client.post(
            f"/api/tasks/{self.team}/collaboration/invite",
            json={"username": self.fneo["username"], "role": "coder"},
            headers=self.fowner["headers"])
        self.assertEqual(invite.status_code, 404, invite.text)
        roles_list = self.client.get(
            f"/api/tasks/{self.team}/collaboration/roles",
            headers=self.fowner["headers"])
        self.assertEqual(roles_list.status_code, 404, roles_list.text)
        personal_mgmt = self._roles(self.fowner, task_id=self.personal)
        self.assertEqual(personal_mgmt.status_code, 404, personal_mgmt.text)

    # -- 10 R8：_coding_authorize 读 action 属主例外、写严格 ----------------
    def test_10_r8_owner_read_exception_strict_write(self):
        # fowner 当前 coder（test_09 置）：读恢复 200（修前 403）。
        freezes = self.client.get(
            f"/api/tasks/{self.team}/coding/freezes",
            headers=self.fowner["headers"])
        self.assertEqual(freezes.status_code, 200, freezes.text)
        self.assertEqual(freezes.json()["freezes"], [])
        rob_owner = self.client.get(
            f"/api/tasks/{self.team}/analysis/risk-of-bias/v2/shared",
            headers=self.fowner["headers"])
        self.assertEqual(rob_owner.status_code, 200, rob_owner.text)
        self.assertIn("legacy_pending", rob_owner.text)  # 全量视图（管理读）
        rob_coder = self.client.get(
            f"/api/tasks/{self.team}/analysis/risk-of-bias/v2/shared",
            headers=self.fcoder["headers"])
        self.assertEqual(rob_coder.status_code, 200, rob_coder.text)
        self.assertEqual(rob_coder.json()["records"], [])   # coder 仅自身视图
        rob_out = self.client.get(
            f"/api/tasks/{self.team}/analysis/risk-of-bias/v2/shared",
            headers=self.fout["headers"])
        self.assertEqual(rob_out.status_code, 403, rob_out.text)
        # 写不借读例外：owner-coder 冻结共识仍拒。
        freeze_try = self.client.post(
            f"/api/tasks/{self.team}/coding/freezes",
            json={"name": "fx", "consensus_ids": [1]},
            headers=self.fowner["headers"])
        self.assertEqual(freeze_try.status_code, 403, freeze_try.text)

    # -- 11 R9：投影不可读省略键；账户映射异常降级不 500 --------------------
    def test_11_r9_projection_omission_and_account_degradation(self):
        self.assertEqual(
            collaboration_mod.active_screening_roster(
                self.collab_path.with_name("collaboration.db.missing"),
                task_id=self.team), None)  # 数据层：不可读=None，非 []（保留原断言）
        # 注入只针对 self.team 的规范协作库 URI、真实 reader 调用帧和 task_id；
        # 其他任务的连接一律走真实 sqlite3.connect 并记录为 delegated。
        main_file = Path(main_mod.__file__).resolve()
        target_collab_uri = self.collab_path.resolve().as_uri() + "?mode=ro"
        real_connect = sqlite3.connect
        calls = []

        def scoped_roster_connect_fail(*args, **kwargs):
            caller = sys._getframe(1)
            uri = str(args[0]) if args else str(kwargs.get("database", ""))
            info = caller.f_locals.get("info")
            task_id = getattr(info, "task_id", None)
            caller_file = Path(caller.f_code.co_filename).resolve()
            hit = (caller.f_code.co_name == "_read_task_roster_snapshot"
                   and caller_file == main_file
                   and uri == target_collab_uri
                   and task_id == self.team)
            calls.append((caller.f_code.co_name, uri,
                          "injected" if hit else "delegated", task_id))
            if hit:
                raise sqlite3.OperationalError("database is locked")
            return real_connect(*args, **kwargs)

        def scoped_accounts_connect_fail(*args, **kwargs):
            caller = sys._getframe(1)
            caller_file = Path(caller.f_code.co_filename).resolve()
            uri = str(args[0]) if args else str(kwargs.get("database", ""))
            projection = caller.f_back
            projection_file = (Path(projection.f_code.co_filename).resolve()
                               if projection is not None else None)
            projection_info = (projection.f_locals.get("info")
                               if projection is not None else None)
            task_id = getattr(projection_info, "task_id", None)
            raw_user_ids = caller.f_locals.get("user_ids")
            user_ids = (tuple(raw_user_ids)
                        if isinstance(raw_user_ids, list) else ())
            hit = (caller.f_code.co_name == "_readonly_roster_usernames"
                   and caller_file == main_file
                   and uri == target_users_uri
                   and projection is not None
                   and projection.f_code.co_name == "_task_roster_projection"
                   and projection_file == main_file
                   and task_id == self.team
                   and isinstance(raw_user_ids, list)
                   and set(raw_user_ids) == target_user_ids)
            calls.append((caller.f_code.co_name, caller_file, uri,
                          "injected" if hit else "delegated", task_id, user_ids))
            if hit:
                raise sqlite3.OperationalError("database is locked")
            return real_connect(*args, **kwargs)

        # R9-a：已确认 team 后协作库存储不可读——投影必须**省略键**不伪装
        # []，任务行保留可见，列表不 500；旧缝不复活（死代码路径一触即红）。
        with mock.patch.object(sqlite3, "connect",
                               new=scoped_roster_connect_fail), \
                mock.patch.object(
                    collaboration_mod, "active_screening_roster",
                    side_effect=AssertionError("legacy roster path invoked")), \
                mock.patch.object(
                    auth_mod, "get_user_by_id",
                    side_effect=AssertionError("legacy auth path invoked")):
            listing = self.client.get("/api/tasks",
                                      headers=self.fowner["headers"])
        self.assertEqual(listing.status_code, 200, listing.text)
        injected = [record for record in calls if record[2] == "injected"]
        self.assertEqual(len(injected), 1, calls)  # 恰一次目标库真实故障
        self.assertEqual(injected[0][0], "_read_task_roster_snapshot", calls)
        self.assertEqual(injected[0][1], target_collab_uri, calls)
        self.assertEqual(injected[0][3], self.team, calls)
        first_hit = calls.index(injected[0])
        target_visibility_reads = [
            record for record in calls
            if record[0] == "_connect" and record[1] == target_collab_uri
            and record[2] == "delegated"
        ]
        self.assertEqual(len(target_visibility_reads), 1, calls)
        self.assertLess(calls.index(target_visibility_reads[0]), first_hit, calls)
        non_target_roster_reads = [
            record for record in calls
            if record[0] == "_read_task_roster_snapshot"
            and "collaboration.db" in record[1]
            and record[1] != target_collab_uri
        ]
        self.assertTrue(non_target_roster_reads, calls)
        self.assertTrue(all(record[2] == "delegated"
                            and record[3] != self.team
                            for record in non_target_roster_reads), calls)
        rows = {row["task_id"]: row for row in listing.json()["tasks"]}
        self.assertIn(self.team, rows)                       # 行保留（可见性正常）
        self.assertNotIn("active_screeners", rows[self.team])  # 省略键≠[]（修前 []）
        self.assertEqual(rows[self.team].get("active_screeners_status"),
                         "unavailable")
        self.assertIn("screeners", rows[self.team])            # 旧合同不变
        other_available_rows = {
            task_id: row for task_id, row in rows.items()
            if task_id != self.team
            and row.get("active_screeners_status") == "available"
        }
        self.assertTrue(other_available_rows, rows)
        self.assertTrue(all(row["active_screeners"]
                            for row in other_available_rows.values()), rows)

        # R9-b：账号库只读故障只注入被测 team 的名单映射；其他可用 team
        # 与鉴权 users.db 读取均真实委托，目标 team 保留完整 UID 并降级名称。
        calls.clear()
        oracle = collaboration_mod.active_screening_roster(
            self.collab_path, task_id=self.team)
        self.assertIsNotNone(oracle)
        target_user_ids = {entry["user_id"] for entry in oracle}
        self.assertTrue(target_user_ids)
        target_users_path = (auth_mod.data_dir() / "users.db").resolve()
        target_users_uri = target_users_path.as_uri() + "?mode=ro"
        auth_users_path = str(target_users_path)

        with mock.patch.object(sqlite3, "connect",
                               new=scoped_accounts_connect_fail), \
                mock.patch.object(auth_mod, "get_user_by_id") as legacy_lookup:
            listing = self.client.get("/api/tasks",
                                      headers=self.fowner["headers"])
        legacy_lookup.assert_not_called()
        self.assertEqual(listing.status_code, 200, listing.text)  # 修前 500
        injected = [record for record in calls if record[3] == "injected"]
        self.assertEqual(len(injected), 1, calls)  # 恰一次目标账号映射故障
        self.assertEqual(injected[0][0], "_readonly_roster_usernames", calls)
        self.assertEqual(injected[0][1], main_file, calls)
        self.assertEqual(injected[0][2], target_users_uri, calls)
        self.assertEqual(injected[0][4], self.team, calls)
        self.assertEqual(set(injected[0][5]), target_user_ids, calls)
        auth_connects = [
            record for record in calls
            if record[0] == "_connect"
            and record[1] == Path(auth_mod.__file__).resolve()
            and record[2] == auth_users_path
            and "mode=ro" not in record[2]
            and record[3] == "delegated"
        ]
        self.assertEqual(len(auth_connects), 1, calls)
        self.assertLess(calls.index(auth_connects[0]), calls.index(injected[0]), calls)
        roster_account_calls = [
            record for record in calls
            if record[0] == "_readonly_roster_usernames"
        ]
        self.assertTrue(roster_account_calls, calls)
        self.assertTrue(all(record[1] == main_file
                            and record[2] == target_users_uri
                            for record in roster_account_calls), calls)
        target_account_calls = [record for record in roster_account_calls
                                if record[4] == self.team]
        self.assertEqual(len(target_account_calls), 1, calls)
        self.assertEqual(target_account_calls[0][3], "injected", calls)
        rows = {row["task_id"]: row for row in listing.json()["tasks"]}
        self.assertIn(self.team, rows)
        self.assertEqual(rows[self.team].get("active_screeners_status"),
                         "available")
        entries = rows[self.team]["active_screeners"]
        self.assertEqual({entry["user_id"] for entry in entries},
                         target_user_ids)  # 全体目标 UID 保留
        self.assertIn(self.fowner["id"], target_user_ids)  # 属主保留
        for entry in entries:
            self.assertIsNone(entry["username"], entry)  # username 降级
            self.assertIsNone(entry["slug"], entry)      # slug 降级
        self.assertIn("screeners", rows[self.team])      # 旧合同不变

        other_available_rows = {
            task_id: row for task_id, row in rows.items()
            if task_id != self.team
            and row.get("active_screeners_status") == "available"
        }
        self.assertTrue(other_available_rows, rows)
        other_account_calls = [record for record in roster_account_calls
                               if record[4] != self.team]
        self.assertEqual({record[4] for record in other_account_calls},
                         set(other_available_rows), calls)
        self.assertTrue(all(record[3] == "delegated"
                            for record in other_account_calls), calls)
        for task_id, row in other_available_rows.items():
            self.assertTrue(row["active_screeners"], row)
            self.assertTrue(all(entry["username"] and entry["slug"]
                                for entry in row["active_screeners"]), row)
            matching_calls = [record for record in other_account_calls
                              if record[4] == task_id]
            self.assertEqual(len(matching_calls), 1, calls)
            self.assertEqual(set(matching_calls[0][5]),
                             {entry["user_id"]
                              for entry in row["active_screeners"]}, calls)

    # -- 11b R9：缺协作库＝compatibility（登记兼容，不证明历史） ------------
    def test_11b_r9_roster_missing_compatibility_desktop(self):
        # server 模式缺库行不可见（get_owner None→legacy fail-closed），
        # compatibility 仅可经 desktop legacy 可见性观测：纯真实文件故障，
        # 零 mock 零注入；finally 原样恢复库文件与 REVIEWFLOW_MODE，
        # 不触真实 data（本类数据根为 setUpClass 隔离临时目录）。
        saved_mode = os.environ.get("REVIEWFLOW_MODE")
        absent = self.collab_path.with_name("collaboration.db.rf216absent")
        os.environ["REVIEWFLOW_MODE"] = "desktop"
        try:
            self.collab_path.rename(absent)
            listing = self.client.get("/api/tasks",
                                      headers=self.fowner["headers"])
        finally:
            if absent.exists() and not self.collab_path.exists():
                absent.rename(self.collab_path)
            if saved_mode is None:
                os.environ.pop("REVIEWFLOW_MODE", None)
            else:
                os.environ["REVIEWFLOW_MODE"] = saved_mode
        self.assertEqual(listing.status_code, 200, listing.text)
        rows = {row["task_id"]: row for row in listing.json()["tasks"]}
        self.assertIn(self.team, rows)                     # desktop legacy 可见
        self.assertNotIn("active_screeners", rows[self.team])  # 省略键
        self.assertEqual(rows[self.team].get("active_screeners_status"),
                         "compatibility")
        self.assertIn("screeners", rows[self.team])        # 旧合同不变

    # -- 12 R10：previous_role/审计记切换前有效角色 -------------------------
    def test_12_r10_previous_role_effective(self):
        invite = self.client.post(
            f"/api/tasks/{self.team}/collaboration/invite",
            json={"username": self.fneo["username"], "role": "coder"},
            headers=self.fowner["headers"])
        self.assertEqual(invite.status_code, 201, invite.text)
        put = self._put_role(self.fowner, self.fneo["id"], "reconciler")
        self.assertEqual(put.status_code, 200, put.text)
        role = put.json()["role"]
        self.assertEqual(role["previous_role"], "coder")  # 修前 None（只读旧 choice）
        self.assertEqual(role["previous_config"], "ok")
        with closing(sqlite3.connect(self.collab_path)) as conn:
            conn.row_factory = sqlite3.Row
            event = conn.execute(
                "SELECT old_role,new_role FROM collaboration_business_role_events "
                "WHERE user_id=? ORDER BY id DESC LIMIT 1",
                (self.fneo["id"],)).fetchone()
        self.assertEqual(event["old_role"], "coder")
        # 冲突前状态显式：双 grant 无 choice 首切 → previous None + conflict。
        invite2 = self.client.post(
            f"/api/tasks/{self.team}/collaboration/invite",
            json={"username": self.fneo2["username"], "role": "coder"},
            headers=self.fowner["headers"])
        self.assertEqual(invite2.status_code, 201, invite2.text)
        grant2 = self.client.post(
            f"/api/tasks/{self.team}/collaboration/roles",
            json={"user_id": self.fneo2["id"], "role": "reconciler"},
            headers=self.fowner["headers"])
        self.assertEqual(grant2.status_code, 201, grant2.text)
        put2 = self._put_role(self.fowner, self.fneo2["id"], "coder")
        self.assertEqual(put2.status_code, 200, put2.text)
        role2 = put2.json()["role"]
        self.assertIsNone(role2["previous_role"])       # 冲突不伪造旧角色
        self.assertEqual(role2["previous_config"], "conflict")

    # -- 13 R11：非法 business_role 400 结构化；畸形载荷 422 ----------------
    def test_13_r11_invalid_role_400(self):
        for bad in ("admin", "", "   ", "x" * 300):
            resp = self._put_role(self.fowner, self.fowner["id"], bad)
            self.assertEqual(resp.status_code, 400, (bad, resp.text))
            payload = self._detail(resp)
            self.assertEqual(payload["error_code"], "team.role.invalid")
            self.assertEqual(payload["params"]["role"], bad[:64])
        wrong_type = self.client.put(
            f"/api/tasks/{self.team}/collaboration/business-role/{self.fowner['id']}",
            json={"business_role": 123}, headers=self.fowner["headers"])
        self.assertEqual(wrong_type.status_code, 422, wrong_type.text)
        missing = self.client.put(
            f"/api/tasks/{self.team}/collaboration/business-role/{self.fowner['id']}",
            json={}, headers=self.fowner["headers"])
        self.assertEqual(missing.status_code, 422, missing.text)
        ok = self._put_role(self.fowner, self.fowner["id"], "reconciler")
        self.assertEqual(ok.status_code, 200, ok.text)


if __name__ == "__main__":
    unittest.main()
