# -*- coding: utf-8 -*-
"""2.1.6 角色II-A修复轮2/5（R2）定向回归钉子 —— 新增独立测试文件，仅覆盖 B1/B2/B3。

依据 flow-role-stage2a-fix-r2-brief.md 必需新回归第 1-5 条与
review_role_fix_r1_ds.md / review_role_fix_r1_sol.md 的真实缺口证据：

- B1/P1（高亮 add/delete 撤销窗口职责门误放行）：在途请求于 canonical 任务锁前
  挂起 → 真实撤销 API（DELETE collaboration/roles）先完成 → 放行后必须 403 且
  DB 零变更。两物理分支都覆盖：
  * quarantine rename 成功 → 旧 {slug}.db 路径不存在且**不被在途请求重建**，
    隔离副本数据保留；
  * 确定性 rename OSError 失败（定点注入，只作用于目标 actor 库路径）→
    旧库保留但 highlight 不新增/不删除。
  角色切换/关参与的既有正向拒绝保留；合法 owner/coder/开参与 reconciler 增删
  仍 200；非参与者 403；personal/legacy batch 行为保持；不存在 task/条目原
  404 形状不回退。故障注入仅两类：一次性 canonical 锁闸（时序）与定点物理
  rename OSError——**不 mock 任何授权返回**（revoke/SQLite/权限查询全走真实
  代码路径）。POSIX 兼容：不依赖「Windows 持句柄必使 rename 失败」假设。

- B2/P2（readonly 把 Corrupt 子类当 Unavailable 父类折叠成 503）：
  WorkflowRuntime._readonly_task_kind 的 has_non_owner_grants 缝隙注入真实
  部分损坏（合成 fixture：owner 表可读、grants 表/索引页清零）后，desktop
  最终 API 必须按既有修库合同映射 409（而非 503「稍后重试」），server 维持
  fail-closed 503；Unavailable（非损坏暂时故障）恒 503 不回归。

- B3/P2-P3（_screening_responsibility_gate 同类父类捕获）：外层 owner/fast
  gate 真实通过，仅**锁内**职责查询（caller 定点注入 + 调用轨迹落盘）抛
  Corrupt 时，desktop 最终 409 / server 503；不能用外层拒绝冒充锁内验证；
  Unavailable 仍 503；无注入对照 200。

自包含（不依赖 build/tests/conftest 与 Safety 夹具）：数据根隔离/资源收回
沿用 test_2_1_6_team_business_role 的类级清理模式；REPO_ROOT 由 ``__file__``
定位——在隔离纯源码箱内运行时自动指向箱根（主仓 app/API/pytest 不参与）。
静态消费者仅为 unittest/pytest 框架；本文件只新增 test 符号，不改任何既有
产品/测试符号。
"""

from __future__ import annotations

import gc
import hashlib
import json
import os
import pathlib
import sqlite3
import sys
import tempfile
import threading
import traceback
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
from custom_backend import review_workflow_api as rwa_mod  # noqa: E402
from custom_backend.main import _screener_db, _task_or_404, app  # noqa: E402

PASSWORD = "R216-Role-Fix-R2!!"

#: 本文件断言的三个缺口标签（红侧失败输出按此定位真实原缺口）。
TAG_B1_REVOKE_WINDOW = "R2-B1 revoke-window contract"
TAG_B1_PHYSICAL = "R2-B1 physical-branch contract"
TAG_B2_CORRUPT_409 = "R2-B2 desktop corrupt-409 contract"
TAG_B3_CORRUPT_409 = "R2-B3 inner-gate desktop corrupt-409 contract"


def _sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _scalar(db_path, sql, params=()):
    """只读标量查询（role22 同款）：文件/表缺失返回 None，绝不误创建。"""
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


def _committed_scalar(db_path, sql, params=()):
    """SELECT-only 读取可能带遗留 -wal/-shm 的库（如 quarantine 改名后的隔离副本）。

    普通连接按 SQLite 恢复/检查点语义读到**已提交**数据；mode=ro 在 -shm 陈旧时
    会静默回退到主文件、漏读仅存于 WAL 的已提交行（本箱探针实证）。仅作用于
    本测试 scratch 内的隔离副本，不触碰任何任务在用库。"""
    if not Path(db_path).is_file():
        return None
    with closing(sqlite3.connect(str(db_path))) as conn:
        try:
            return conn.execute(sql, params).fetchone()[0]
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return None
            raise


def _seed_article(db_path, key: str, title: str) -> None:
    with closing(db_mod._connect(db_path)) as conn, conn:
        conn.execute(
            "INSERT OR IGNORE INTO articles(zotero_key,title) VALUES (?,?)",
            (key, title))


def _add_payload(key: str = "Z1", text: str = "r2 event", page: int = 1) -> dict:
    """add_highlights 合法批事件（MM5-13：rect 恰 4 分量）。"""
    return {"zotero_key": key,
            "events": [{"kind": "highlight", "page": page, "text": text,
                        "rects": [[0.1, 0.1, 0.5, 0.5]]}]}


def _fail_rename_for(target: Path):
    """定点物理故障：仅当 rename 的源路径确为目标 actor 库时抛确定性 OSError。

    其余一切 rename 委托真实实现——真实撤销/SQLite/其余物理操作不受影响；
    这是「确定性物理 rename 失败」注入，不是授权 mock（POSIX 兼容）。"""
    real_rename = pathlib.Path.rename
    want = str(target).casefold()

    def failing(self, target_):
        if str(self).casefold() == want:
            raise OSError(13, "simulated deterministic quarantine rename "
                              "failure (r2 fault injection, target actor path only)")
        return real_rename(self, target_)

    return mock.patch.object(pathlib.Path, "rename", failing)


class _R2FixtureBase(unittest.TestCase):
    """隔离数据根 + 箱身份/来源核验（brief 第 5 条：__file__/sourcehash/defaultdata）。

    REVIEWFLOW_MODE=server 为类默认（协作管理/撤销端点 server-only）；
    个别用例按 _server_mode() 每次读 env 的特性做 desktop 定点切换并成对收回。
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="rf_216_rolefix_r2_")
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
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        cls.addClassCleanup(cls.client.__exit__, None, None, None)
        assert Path(main_mod.DATA_DIR).resolve() == cls._root.resolve()
        assert Path(auth_mod.data_dir()).resolve() == cls._root.resolve()

        # 箱身份：导入面必须全部落在 REPO_ROOT（隔离纯源码箱）内，主仓不参与。
        for module in (main_mod, rwa_mod, collaboration_mod, db_mod):
            src = Path(module.__file__).resolve()
            assert src.is_relative_to(REPO_ROOT), (module.__name__, str(src))
        print("R2_SOURCE_MAIN_SHA", _sha256(main_mod.__file__))
        print("R2_SOURCE_REVIEW_WORKFLOW_API_SHA", _sha256(rwa_mod.__file__))
        print("R2_SOURCE_TASK_COLLABORATION_SHA", _sha256(collaboration_mod.__file__))
        print("R2_REPO_ROOT", str(REPO_ROOT))
        print("R2_DEFAULT_DATA_ABSENT_PRE", not (REPO_ROOT / "data").exists())
        assert not (REPO_ROOT / "data").exists()
        cls.addClassCleanup(cls._post_box_checks)

    @classmethod
    def _post_box_checks(cls):
        absent = not (REPO_ROOT / "data").exists()
        print("R2_DEFAULT_DATA_ABSENT_POST", absent)
        assert absent, "隔离箱内不得落 default data 目录"

    @classmethod
    def _mkuser(cls, name: str) -> dict:
        user = auth_mod.create_user(name, PASSWORD)
        return {**user, "headers": {"Authorization": "Bearer " + auth_mod.create_access_token(
            user["id"], user["username"])}}

    # -- 共用小工具 ---------------------------------------------------------
    @staticmethod
    def _detail(resp):
        try:
            body = resp.json()
        except ValueError:
            return None
        return body.get("detail") if isinstance(body, dict) else body

    @classmethod
    def _code(cls, resp):
        detail = cls._detail(resp)
        return detail.get("error_code") if isinstance(detail, dict) else None

    @staticmethod
    def _actor_db_path(info, username: str) -> Path:
        """actor 个人库物理路径（只拼路径，不触发 ensure/建库）。"""
        return info.dir / f"{tasks_mod.slugify_screener(username)}.db"

    @staticmethod
    def _revoked_files(info, username: str) -> list:
        """隔离改名产生的**主库文件**列表（不含 -journal/-wal/-shm 边车）。

        产品 _quarantine_member_db 是尽力而为的主库+边车整组改名（既有行为）；
        本合同只计 actor 主库文件，边车不计入。"""
        slug = tasks_mod.slugify_screener(username)
        return sorted(p for p in info.dir.glob(f"{slug}.db.revoked-*")
                      if not p.name.endswith(("-journal", "-wal", "-shm")))

    @staticmethod
    def _mode(tmp_mode: str):
        """desktop/server 定点切换上下文（_server_mode 每次调用读 env）。"""
        return mock.patch.dict(os.environ, {"REVIEWFLOW_MODE": tmp_mode})


class TeamRoleFixR2HighlightRevocationTests(_R2FixtureBase):
    """B1：高亮 add/delete 撤销窗口（真实 revoke 在先、放行在后）+ 两物理分支。

    顺序 test_01..test_08； racing 成员互不复用（各单 grant，撤销即完全脱离，
    quarantine 分支确定可达）。合法路径/既有拒绝/personal/legacy/404 形状为
    保持性对照（红绿两侧都应通过）。
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = cls._mkuser("r2-owner@example.test")
        cls.m_pos = cls._mkuser("r2-pos@example.test")
        cls.m_add = cls._mkuser("r2-add@example.test")
        cls.m_add2 = cls._mkuser("r2-add2@example.test")
        cls.m_del = cls._mkuser("r2-del@example.test")
        cls.m_del2 = cls._mkuser("r2-del2@example.test")
        cls.m_switch = cls._mkuser("r2-switch@example.test")
        cls.m_recon = cls._mkuser("r2-recon@example.test")
        cls.outsider = cls._mkuser("r2-out@example.test")

        cls.team = cls.client.post(
            "/api/tasks", json={"name": "r2 revoke window team"},
            headers=cls.owner["headers"]).json()["task_id"]
        cls.personal = cls.client.post(
            "/api/tasks", json={"name": "r2 personal batch"},
            headers=cls.owner["headers"]).json()["task_id"]
        cls.legacy = cls.client.post(
            "/api/tasks", json={"name": "r2 legacy desktop"},
            headers=cls.owner["headers"]).json()["task_id"]

        # 播种成员 grant（真实 API；invite 会 latch 团队）。
        for member, role in ((cls.m_pos, "coder"), (cls.m_add, "coder"),
                             (cls.m_add2, "coder"), (cls.m_del, "coder"),
                             (cls.m_del2, "coder"), (cls.m_switch, "coder"),
                             (cls.m_recon, "reconciler")):
            resp = cls.client.post(
                f"/api/tasks/{cls.team}/collaboration/invite",
                json={"username": member["username"], "role": role},
                headers=cls.owner["headers"])
            assert resp.status_code == 201, (member["username"], resp.text)

        # 属主业务角色 coder：owner 合法筛选增删链路（保持性对照的基线）。
        put = cls.client.put(
            f"/api/tasks/{cls.team}/collaboration/business-role/{cls.owner['id']}",
            json={"business_role": "coder"}, headers=cls.owner["headers"])
        assert put.status_code == 200, put.text
        # 仲裁者成员初筛参与开（test_07 关闭前基线）。
        toggle = cls.client.put(
            f"/api/tasks/{cls.team}/collaboration/members/{cls.m_recon['id']}/screening",
            json={"screening_enabled": True}, headers=cls.owner["headers"])
        assert toggle.status_code == 200, toggle.text

        # 每个参与者个人库建库 + 文章播种（高亮外键依赖）。
        for member in (cls.owner, cls.m_pos, cls.m_add, cls.m_add2,
                       cls.m_del, cls.m_del2, cls.m_switch, cls.m_recon):
            db = _screener_db(_task_or_404(cls.team), member["username"])
            _seed_article(db, "Z1", "r2 seed one")
            _seed_article(db, "Z2", "r2 seed two")
        pdb = _screener_db(_task_or_404(cls.personal), cls.owner["username"])
        _seed_article(pdb, "Z1", "r2 personal seed")
        ldb = _screener_db(_task_or_404(cls.legacy), cls.owner["username"])
        _seed_article(ldb, "Z1", "r2 legacy seed")

        # m_del / m_del2 基线高亮走真实 API add（兼任成员正向对照）。
        cls.m_del_base_id = cls._api_add_one(cls.team, cls.m_del, "r2-del-base")
        cls.m_del2_base_id = cls._api_add_one(cls.team, cls.m_del2, "r2-del2-base")
        cls.collab_path = collaboration_mod.shared_store_path(_task_or_404(cls.team).dir)
        runtime = app.state.review_workflow
        assert runtime.is_team(cls.team) is True

    @classmethod
    def _api_add_one(cls, task_id, user, text):
        resp = cls.client.post(f"/api/tasks/{task_id}/highlights",
                               json=_add_payload(text=text), headers=user["headers"])
        assert resp.status_code == 200, resp.text
        assert resp.json()["saved"] == 1, resp.text
        return resp.json()["highlights"][0]["id"]

    # -- 时序闸门与在途线程 -------------------------------------------------
    def _gate(self, canonical_task_id):
        """一次性 canonical 锁闸：在途请求于**获取真锁之前**挂起（延迟包装，
        首次命中即自解除）——撤销方随后经原锁完成真实撤销，被闸线程放行后才
        进锁，精确复现「require_user 已过 → 撤销完成 → 处理续跑」的 TOCTOU 缝。
        竞争双方均为真实 API/真实锁/真实 SQLite；注入仅时序。"""
        runtime = app.state.review_workflow
        real_lock = runtime.lock
        entered, release = threading.Event(), threading.Event()
        state = {"armed": True}

        def gated(task_id):
            if state["armed"] and task_id == canonical_task_id:
                state["armed"] = False
                entered.set()
                assert release.wait(timeout=25), "r2 gate release timeout"
            return real_lock(task_id)

        patcher = mock.patch.object(runtime, "lock", gated)
        patcher.start()
        self.addCleanup(release.set)
        self.addCleanup(patcher.stop)
        return entered, release

    def _inflight(self, action, *, user, payload=None, highlight_id=None):
        """线程内独立 TestClient 跑真实高亮端点（不重复 lifespan）。"""
        outcome = {}

        def run():
            client = TestClient(app, raise_server_exceptions=False)
            try:
                if action == "add":
                    r = client.post(f"/api/tasks/{self.team}/highlights",
                                    json=payload, headers=user["headers"])
                else:
                    r = client.delete(
                        f"/api/tasks/{self.team}/highlights/{highlight_id}",
                        headers=user["headers"])
                try:
                    body = r.json()
                except ValueError:
                    body = None
                outcome["status"] = r.status_code
                outcome["body"] = body
            except BaseException:
                outcome["error"] = traceback.format_exc()
            finally:
                client.close()

        th = threading.Thread(target=run, name="r2-inflight", daemon=True)
        th.start()
        return th, outcome

    def _run_revoke_window(self, member, *, action, payload=None,
                           highlight_id=None, fail_rename=False):
        """真实撤销窗口：在途挂起 → 真实 revoke 200 → （可选 rename 定点故障）
        → 放行。返回 (在途线程, outcome, info, target_db)。"""
        info = _task_or_404(self.team)
        target_db = self._actor_db_path(info, member["username"])
        entered, release = self._gate(_task_or_404(self.team).task_id)
        th, outcome = self._inflight(action, user=member, payload=payload,
                                     highlight_id=highlight_id)
        assert entered.wait(timeout=25), "in-flight did not reach save lock"
        fault_ctx = _fail_rename_for(target_db) if fail_rename else None
        if fault_ctx is not None:
            fault_ctx.__enter__()
            self.addCleanup(fault_ctx.__exit__, None, None, None)
        try:
            resp = self.client.delete(
                f"/api/tasks/{self.team}/collaboration/roles/{member['id']}/coder"
                if member is not self.m_recon else
                f"/api/tasks/{self.team}/collaboration/roles/{member['id']}/reconciler",
                headers=self.owner["headers"])
            assert resp.status_code == 200, resp.text
            assert resp.json()["revoked"] is True, resp.text
            assert collaboration_mod.is_participant(
                self.collab_path, member["id"], task_id=info.task_id) is False, \
                "真实撤销后该成员必须已脱离（数据层核验）"
            release.set()
        finally:
            th.join(timeout=25)
        assert not th.is_alive(), "in-flight thread did not finish"
        print("R2_RACE", action, "fail_rename" if fail_rename else "rename-real",
              json.dumps(outcome, ensure_ascii=False, default=str))
        return outcome, info, target_db

    # -- 01 正向矩阵与既有形状（保持性对照，红绿都绿） ----------------------
    def test_01_positive_matrix_and_error_shapes(self):
        # owner-coder 合法 add（batch 2 事件）→ 200。
        resp = self.client.post(f"/api/tasks/{self.team}/highlights",
                                json={"zotero_key": "Z1", "events": [
                                    {"kind": "highlight", "page": 1, "text": "r2-owner-a",
                                     "rects": [[0.0, 0.0, 0.2, 0.2]]},
                                    {"kind": "highlight", "page": 2, "text": "r2-owner-b",
                                     "rects": [[0.1, 0.1, 0.3, 0.3]]}]},
                                headers=self.owner["headers"])
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["saved"], 2, resp.text)
        owner_db = self._actor_db_path(_task_or_404(self.team), self.owner["username"])
        self.assertEqual(_scalar(owner_db, "SELECT COUNT(*) FROM highlights"), 2)
        first_id = resp.json()["highlights"][0]["id"]
        # owner 合法 delete → 200，行数回落。
        resp = self.client.delete(f"/api/tasks/{self.team}/highlights/{first_id}",
                                  headers=self.owner["headers"])
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["deleted"], resp.text)
        self.assertEqual(_scalar(owner_db, "SELECT COUNT(*) FROM highlights"), 1)
        # 成员 coder 正向 add/delete。
        pos_id = self._api_add_one(self.team, self.m_pos, "r2-pos-cycle")
        resp = self.client.delete(f"/api/tasks/{self.team}/highlights/{pos_id}",
                                  headers=self.m_pos["headers"])
        self.assertEqual(resp.status_code, 200, resp.text)
        # 非参与者直接请求 → require_user 参与者门 403（既有形状不变）。
        resp = self.client.post(f"/api/tasks/{self.team}/highlights",
                                json=_add_payload(text="r2-outsider"),
                                headers=self.outsider["headers"])
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertEqual(resp.json()["detail"], "当前账号不是该任务的参与者。")
        resp = self.client.delete(f"/api/tasks/{self.team}/highlights/1",
                                  headers=self.outsider["headers"])
        self.assertEqual(resp.status_code, 403, resp.text)
        # 不存在任务 → 404；不存在条目 → 404（原错误形状不回退）。
        resp = self.client.post(f"/api/tasks/r2-no-such-task/highlights",
                                json=_add_payload(), headers=self.owner["headers"])
        self.assertEqual(resp.status_code, 404, resp.text)
        resp = self.client.delete(f"/api/tasks/{self.team}/highlights/424242",
                                  headers=self.owner["headers"])
        self.assertEqual(resp.status_code, 404, resp.text)
        self.assertEqual(resp.json()["detail"], "高亮不存在或已删除。")
        # personal 合法 batch（多事件）→ 200 saved 2（server 模式属主可见）。
        resp = self.client.post(f"/api/tasks/{self.personal}/highlights",
                                json={"zotero_key": "Z1", "events": [
                                    {"kind": "highlight", "page": 1, "text": "r2-pa",
                                     "rects": [[0.0, 0.0, 0.2, 0.2]]},
                                    {"kind": "note", "page": 1, "text": "r2-pb"}]},
                                headers=self.owner["headers"])
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["saved"], 2, resp.text)

    # -- 02 B1：撤销窗口 add，quarantine rename 成功 → 403 且旧路径不重建 ---
    def test_02_add_revoked_inflight_rename_success_no_recreate(self):
        info = _task_or_404(self.team)
        target_db = self._actor_db_path(info, self.m_add["username"])
        self.assertTrue(target_db.is_file(), "夹具期成员库必须已存在")
        base = _scalar(target_db, "SELECT COUNT(*) FROM highlights")
        outcome, info2, target2 = self._run_revoke_window(
            self.m_add, action="add", payload=_add_payload(text="r2-revoked-window-add"))
        # 放行前：rename 真实成功 → 旧路径已消失、隔离副本存在且数据保留。
        self.assertFalse(target2.is_file(),
                         "撤销成功（无持柄注入）后旧 {slug}.db 必须已被隔离改名")
        revoked = self._revoked_files(info2, self.m_add["username"])
        self.assertEqual(len(revoked), 1, [p.name for p in revoked])
        self.assertEqual(
            _committed_scalar(revoked[0], "SELECT COUNT(*) FROM highlights"), base,
            "隔离副本必须保留撤销前全部数据（含仅存于遗留 WAL 的已提交行）")
        # 放行后核心断言（红侧：500 + 旧路径被在途请求重建为空库）。
        self.assertEqual(
            outcome.get("status"), 403,
            f"{TAG_B1_REVOKE_WINDOW}：真实撤销完成后在途 add 必须先 403 且不落盘；"
            f"实际={outcome}")
        self.assertFalse(
            target2.is_file(),
            f"{TAG_B1_PHYSICAL}：rename 成功分支旧路径不得被在途请求重新创建；"
            f"exists={target2.is_file()} outcome={outcome}")
        self.assertEqual(len(self._revoked_files(info2, self.m_add["username"])), 1)
        # 同身份全新请求 → 参与者门 403（既有行为）。
        resp = self.client.post(f"/api/tasks/{self.team}/highlights",
                                json=_add_payload(text="r2-after-revoke"),
                                headers=self.m_add["headers"])
        self.assertEqual(resp.status_code, 403, resp.text)

    # -- 03 B1：撤销窗口 delete，rename 成功 → 403 且旧路径不重建 -----------
    def test_03_delete_revoked_inflight_rename_success_no_recreate(self):
        info = _task_or_404(self.team)
        target_db = self._actor_db_path(info, self.m_del["username"])
        base = _scalar(target_db, "SELECT COUNT(*) FROM highlights")
        self.assertEqual(base, 1, "m_del 基线恰 1 行")
        outcome, info2, target2 = self._run_revoke_window(
            self.m_del, action="delete", highlight_id=self.m_del_base_id)
        self.assertFalse(target2.is_file())
        revoked = self._revoked_files(info2, self.m_del["username"])
        self.assertEqual(len(revoked), 1, [p.name for p in revoked])
        self.assertEqual(
            _committed_scalar(revoked[0], "SELECT COUNT(*) FROM highlights"), 1,
            "隔离副本保留基线行（含仅存于遗留 WAL 的已提交行）")
        self.assertEqual(
            outcome.get("status"), 403,
            f"{TAG_B1_REVOKE_WINDOW}：真实撤销完成后在途 delete 必须先 403；实际={outcome}")
        self.assertFalse(
            target2.is_file(),
            f"{TAG_B1_PHYSICAL}：delete 侧 rename 成功分支旧路径不得重建；outcome={outcome}")

    # -- 04 B1：撤销窗口 add，rename 定点 OSError 失败 → 403 且旧库零变更 ---
    def test_04_add_revoked_inflight_rename_failure_oserror(self):
        info = _task_or_404(self.team)
        target_db = self._actor_db_path(info, self.m_add2["username"])
        base = _scalar(target_db, "SELECT COUNT(*) FROM highlights")
        outcome, info2, target2 = self._run_revoke_window(
            self.m_add2, action="add",
            payload=_add_payload(text="r2-rename-fault-add"), fail_rename=True)
        # 定点故障：revoke 仍真实 200/revoked，旧库保留、无隔离副本产生。
        self.assertTrue(target2.is_file(), "注入 OSError 后旧库必须保留")
        self.assertEqual(self._revoked_files(info2, self.m_add2["username"]), [],
                         "rename 失败不得产生隔离副本")
        self.assertEqual(
            outcome.get("status"), 403,
            f"{TAG_B1_REVOKE_WINDOW}：rename 失败分支放行后 add 必须 403；实际={outcome}")
        self.assertEqual(
            _committed_scalar(target2, "SELECT COUNT(*) FROM highlights"), base,
            f"{TAG_B1_PHYSICAL}：旧库保留但 highlight 不得新增；outcome={outcome}")

    # -- 05 B1：撤销窗口 delete，rename 定点 OSError 失败 → 403 且行保留 ----
    def test_05_delete_revoked_inflight_rename_failure_oserror(self):
        info = _task_or_404(self.team)
        target_db = self._actor_db_path(info, self.m_del2["username"])
        outcome, info2, target2 = self._run_revoke_window(
            self.m_del2, action="delete", highlight_id=self.m_del2_base_id,
            fail_rename=True)
        self.assertTrue(target2.is_file())
        self.assertEqual(self._revoked_files(info2, self.m_del2["username"]), [])
        self.assertEqual(
            outcome.get("status"), 403,
            f"{TAG_B1_REVOKE_WINDOW}：rename 失败分支放行后 delete 必须 403；实际={outcome}")
        self.assertEqual(
            _committed_scalar(target2, "SELECT COUNT(*) FROM highlights"), 1,
            f"{TAG_B1_PHYSICAL}：基线高亮行不得被在途 delete 移除；outcome={outcome}")

    # -- 06 既有正向拒绝保持：锁内角色切换 → 403 screening_not_enabled ------
    def test_06_role_switch_inflight_rejected_preserved(self):
        info = _task_or_404(self.team)
        target_db = self._actor_db_path(info, self.m_switch["username"])
        base = _scalar(target_db, "SELECT COUNT(*) FROM highlights")
        entered, release = self._gate(_task_or_404(self.team).task_id)
        th, outcome = self._inflight(action="add", user=self.m_switch,
                                     payload=_add_payload(text="r2-switch-window"))
        self.assertTrue(entered.wait(timeout=25), "in-flight did not reach save lock")
        resp = self.client.put(
            f"/api/tasks/{self.team}/collaboration/business-role/{self.m_switch['id']}",
            json={"business_role": "reconciler"}, headers=self.owner["headers"])
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["role"]["business_role"], "reconciler", resp.text)
        release.set()
        th.join(timeout=25)
        self.assertFalse(th.is_alive())
        print("R2_RACE switch", json.dumps(outcome, ensure_ascii=False, default=str))
        self.assertEqual(outcome.get("status"), 403,
                         f"角色切换后放行在途 add 须拒（既有正向拒绝）；实际={outcome}")
        self.assertEqual(self._code_from(outcome), "screening_not_enabled",
                         f"既有错误形状保持；实际={outcome}")
        self.assertEqual(_scalar(target_db, "SELECT COUNT(*) FROM highlights"), base,
                         "被拒在途请求不得落盘")
        self.assertTrue(collaboration_mod.is_participant(
            self.collab_path, self.m_switch["id"], task_id=info.task_id),
            "切换不等于撤销：成员关系保留")

    # -- 07 既有正向拒绝保持：锁内关参与 → 403 screening_not_enabled --------
    def test_07_participation_off_inflight_rejected_preserved(self):
        info = _task_or_404(self.team)
        target_db = self._actor_db_path(info, self.m_recon["username"])
        base = _scalar(target_db, "SELECT COUNT(*) FROM highlights")
        entered, release = self._gate(_task_or_404(self.team).task_id)
        th, outcome = self._inflight(action="add", user=self.m_recon,
                                     payload=_add_payload(text="r2-partoff-window"))
        self.assertTrue(entered.wait(timeout=25), "in-flight did not reach save lock")
        resp = self.client.put(
            f"/api/tasks/{self.team}/collaboration/members/{self.m_recon['id']}/screening",
            json={"screening_enabled": False}, headers=self.owner["headers"])
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertFalse(resp.json()["member"]["screening_enabled"], resp.text)
        release.set()
        th.join(timeout=25)
        self.assertFalse(th.is_alive())
        print("R2_RACE partoff", json.dumps(outcome, ensure_ascii=False, default=str))
        self.assertEqual(outcome.get("status"), 403,
                         f"关参与后放行在途 add 须拒（既有正向拒绝）；实际={outcome}")
        self.assertEqual(self._code_from(outcome), "screening_not_enabled",
                         f"既有错误形状保持；实际={outcome}")
        self.assertEqual(_scalar(target_db, "SELECT COUNT(*) FROM highlights"), base,
                         "被拒在途请求不得落盘")

    # -- 08 desktop personal/legacy batch 保持 + 404 形状 --------------------
    def test_08_desktop_personal_legacy_batch_preserved(self):
        # 先在 server 模式补一个 batch add（此后删除隔离副本无碍断言）。
        resp = self.client.post(f"/api/tasks/{self.personal}/highlights",
                                json=_add_payload(text="r2-personal-server"),
                                headers=self.owner["headers"])
        self.assertEqual(resp.status_code, 200, resp.text)
        # legacy = 协作库无属主记录：清掉本 scratch 夹具任务的 collaboration.db。
        legacy_info = _task_or_404(self.legacy)
        legacy_collab = collaboration_mod.shared_store_path(legacy_info.dir)
        legacy_collab.unlink()
        with self._mode("desktop"):
            # desktop personal batch：2 事件 → 200 saved 2。
            resp = self.client.post(f"/api/tasks/{self.personal}/highlights",
                                    json={"zotero_key": "Z1", "events": [
                                        {"kind": "highlight", "page": 3, "text": "r2-pd1",
                                         "rects": [[0.2, 0.2, 0.4, 0.4]]},
                                        {"kind": "highlight", "page": 4, "text": "r2-pd2",
                                         "rects": [[0.3, 0.3, 0.5, 0.5]]}]},
                                    headers=self.owner["headers"])
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertEqual(resp.json()["saved"], 2, resp.text)
            # desktop legacy batch：owner 仍可达且可删（历史行为保持）。
            resp = self.client.post(f"/api/tasks/{self.legacy}/highlights",
                                    json=_add_payload(text="r2-legacy-desktop"),
                                    headers=self.owner["headers"])
            self.assertEqual(resp.status_code, 200, resp.text)
            legacy_id = resp.json()["highlights"][0]["id"]
            resp = self.client.delete(f"/api/tasks/{self.legacy}/highlights/{legacy_id}",
                                      headers=self.owner["headers"])
            self.assertEqual(resp.status_code, 200, resp.text)

    @staticmethod
    def _code_from(outcome):
        body = outcome.get("body") or {}
        detail = body.get("detail") if isinstance(body, dict) else None
        if isinstance(detail, dict):
            return detail.get("error_code")
        return None


class TeamRoleFixR2CorruptUnavailableClassificationTests(_R2FixtureBase):
    """B2：``_readonly_task_kind`` 的 has_non_owner_grants 缝隙——Corrupt 子类
    不得被父类 Unavailable 折叠为 503「稍后重试」；desktop 最终 API 409 修库
    指引 / server 503 fail-closed；Unavailable 恒 503。

    部分损坏 fixture 为本箱 scratch 数据根内**新建**合成库（owner 表可读、
    grants 表/索引页清零；方法只读借鉴 DS probe9，不动任何旧证据）。
    前置事实：任务未 latch + 存在非属主 grant（latch 先行会短路 grants 读）。
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = cls._mkuser("r2c-owner@example.test")
        cls.member = cls._mkuser("r2c-member@example.test")
        cls.task = cls.client.post(
            "/api/tasks", json={"name": "r2 corrupt classification"},
            headers=cls.owner["headers"]).json()["task_id"]
        info = _task_or_404(cls.task)
        cls.collab = collaboration_mod.shared_store_path(info.dir)
        # 属主库先建（_readonly_task_kind 先读 owner_db latch，再读协作库 grants）。
        odb = _screener_db(info, cls.owner["username"])
        _seed_article(odb, "Z1", "r2 corrupt seed")
        # 数据层直授（真实 grant_role；不经 invite API → 不 latch）。
        collaboration_mod.grant_role(
            cls.collab, actor_user_id=cls.owner["id"], user_id=cls.member["id"],
            role="coder", task_id=cls.task)
        runtime = app.state.review_workflow
        # 损坏前事实核验（真实缝隙）：未 latch + 非属主 grant → is_team True。
        assert collaboration_mod.has_non_owner_grants(
            cls.collab, task_id=cls.task, owner_user_id=cls.owner["id"]) is True
        assert runtime.is_team(cls.task) is True
        print("R2_B2_PRECORRUPT_IS_TEAM", True)
        # 未损坏控制：真实 API 决策写被角色门拒（owner 未配置 → 409 结构化）。
        resp = cls.client.post(f"/api/tasks/{cls.task}/decisions",
                               json={"zotero_key": "Z1", "decision": "include"},
                               headers=cls.owner["headers"])
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["error_code"] == "team.role.unconfigured", resp.text
        print("R2_B2_PRECORRUPT_CONTROL", resp.status_code, resp.text)
        # 合成部分损坏：清零 grants 表及其索引的根页（owner/任务表不动）。
        cls._corrupted_pages = cls._zero_grant_pages(cls.collab)
        print("R2_B2_CORRUPTED_ROOT_PAGES", cls._corrupted_pages)

    @classmethod
    def _zero_grant_pages(cls, collab: Path) -> list:
        conn = sqlite3.connect(collab)
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        roots = [row[0] for row in conn.execute(
            "SELECT rootpage FROM sqlite_master "
            "WHERE tbl_name='collaboration_role_grants' AND rootpage>0")]
        conn.close()
        with open(collab, "r+b") as fh:
            for root in roots:
                fh.seek((root - 1) * page_size)
                fh.write(b"\xab" * page_size)
        return roots

    # -- 01 数据层缝隙：真实损坏字节 → 精确 Corrupt 类型（非父类折叠）-------
    def test_01_datalayer_corrupt_exact_type_and_owner_still_readable(self):
        with self.assertRaises(collaboration_mod.CollaborationStoreCorruptError) as ctx:
            collaboration_mod.has_non_owner_grants(
                self.collab, task_id=self.task, owner_user_id=self.owner["id"])
        self.assertIs(type(ctx.exception), collaboration_mod.CollaborationStoreCorruptError,
                      "必须是 Corrupt 子类本类，不得被换成父类")
        self.assertIsInstance(ctx.exception, collaboration_mod.OwnerUnavailableError,
                              "Corrupt 是 Unavailable 子类（继承关系既有）")
        print("R2_B2_DATALAYER", type(ctx.exception).__name__, str(ctx.exception))
        # 部分损坏前提：owner 表仍可读（get_owner 正常）——「不能靠 get_owner
        # 可读就当库完好」的对照事实。
        owner_row = collaboration_mod.get_owner(self.collab, task_id=self.task)
        self.assertIsNotNone(owner_row)
        self.assertEqual(int(owner_row["owner_user_id"]), self.owner["id"])

    # -- 02 B2 核心：desktop 最终 API 必须映射 409 修库指引（红侧 503 即缺口）-
    def test_02_desktop_business_role_corrupt_maps_409(self):
        with self._mode("desktop"):
            resp = self.client.get(
                f"/api/tasks/{self.task}/collaboration/business-role",
                headers=self.owner["headers"])
        print("R2_B2_DESKTOP_CORRUPT", resp.status_code,
              json.dumps(self._detail(resp), ensure_ascii=False))
        self.assertEqual(
            resp.status_code, 409,
            f"{TAG_B2_CORRUPT_409}：desktop 下 grants 部分损坏必须按既有修库合同映射 409；"
            f"实际={resp.status_code} body={resp.text[:200]}")
        self.assertEqual(self._detail(resp), main_mod.COLLABORATION_STORE_CORRUPT_MESSAGE,
                         f"409 必须携带修库指引文案；实际={self._detail(resp)}")

    # -- 03 server fail-closed：Corrupt → 503（既有 handler，不回归）---------
    def test_03_server_business_role_corrupt_maps_503(self):
        resp = self.client.get(
            f"/api/tasks/{self.task}/collaboration/business-role",
            headers=self.owner["headers"])
        print("R2_B2_SERVER_CORRUPT", resp.status_code)
        self.assertEqual(resp.status_code, 503,
                         f"server Corrupt 维持 fail-closed 503；实际={resp.status_code}")

    # -- 04 server 经 _task_is_team 缝隙（与 desktop 同一 B2 折叠点）仍 503 --
    def test_04_server_decisions_corrupt_via_taskkind_seam_503(self):
        resp = self.client.post(f"/api/tasks/{self.task}/decisions",
                                json={"zotero_key": "Z1", "decision": "include"},
                                headers=self.owner["headers"])
        print("R2_B2_SERVER_DECISIONS_CORRUPT", resp.status_code)
        self.assertEqual(resp.status_code, 503,
                         "server 经 readonly task_kind 缝隙的 Corrupt 维持 503 fail-closed")
        odb = _screener_db(_task_or_404(self.task), self.owner["username"])
        self.assertEqual(_scalar(odb, "SELECT COUNT(*) FROM decisions"), 0,
                         "损坏窗口内不得有决策落盘")

    # -- 05 Unavailable（非损坏暂时故障）恒 503，desktop/server 均不回归 -----
    def test_05_unavailable_maps_503_both_modes(self):
        def unavailable(store, **kw):
            raise collaboration_mod.OwnerUnavailableError(
                "simulated transient collaboration lookup failure (r2 fault injection)")

        with mock.patch.object(collaboration_mod, "has_non_owner_grants", unavailable):
            with self._mode("desktop"):
                resp = self.client.get(
                    f"/api/tasks/{self.task}/collaboration/business-role",
                    headers=self.owner["headers"])
            self.assertEqual(resp.status_code, 503,
                             f"desktop Unavailable 恒 503；实际={resp.status_code}")
            with self._mode("server"):
                resp = self.client.post(f"/api/tasks/{self.task}/decisions",
                                        json={"zotero_key": "Z1", "decision": "include"},
                                        headers=self.owner["headers"])
            self.assertEqual(resp.status_code, 503,
                             f"server Unavailable 恒 503；实际={resp.status_code}")
        print("R2_B2_UNAVAILABLE_503", "desktop+server OK")


class TeamRoleFixR2ScreeningGateCorruptTests(_R2FixtureBase):
    """B3：``_screening_responsibility_gate``（锁内职责复查）Corrupt 子类保真。

    外层 owner/可见性/快速职责门全部**真实通过**（无注入对照 200 佐证），
    仅 caller 定点注入使锁内 ``team_screening_permission`` 抛 Corrupt/Unavailable
    ——调用轨迹全部落盘，证明 4xx/5xx 来自锁内缝隙而非上游拒绝冒充。
    desktop 最终 409 / server 503；Unavailable 恒 503；注入窗口内零落盘。
    """

    INNER_GATE = "_screening_responsibility_gate"
    OUTER_GATE = "_screening_gate"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = cls._mkuser("r2g-owner@example.test")
        cls.member = cls._mkuser("r2g-member@example.test")
        cls.task = cls.client.post(
            "/api/tasks", json={"name": "r2 screening gate classification"},
            headers=cls.owner["headers"]).json()["task_id"]
        resp = cls.client.post(
            f"/api/tasks/{cls.task}/collaboration/invite",
            json={"username": cls.member["username"], "role": "coder"},
            headers=cls.owner["headers"])
        assert resp.status_code == 201, resp.text  # invite → latch
        put = cls.client.put(
            f"/api/tasks/{cls.task}/collaboration/business-role/{cls.owner['id']}",
            json={"business_role": "coder"}, headers=cls.owner["headers"])
        assert put.status_code == 200, put.text
        odb = _screener_db(_task_or_404(cls.task), cls.owner["username"])
        for key in ("Z1", "Z2", "Z3"):
            _seed_article(odb, key, f"r2 gate seed {key}")
        # 无注入对照（夹具期）：真实全链 200 + 真实落盘。
        resp = cls.client.post(f"/api/tasks/{cls.task}/decisions",
                               json={"zotero_key": "Z1", "decision": "include"},
                               headers=cls.owner["headers"])
        assert resp.status_code == 200, resp.text
        cls.owner_db = odb

    def _inject_inner(self, calls: list, exc: Exception):
        """caller 定点注入：仅当调用方为锁内职责门时抛给定异常；外层快门与
        其他调用全部透传真实实现。每笔调用的 caller/行为记录进 calls 落盘。"""
        real_tsp = collaboration_mod.team_screening_permission

        def gated(store, **kw):
            caller = sys._getframe(1).f_code.co_name
            if caller == self.INNER_GATE:
                calls.append((caller, "raise", type(exc).__name__))
                raise exc
            calls.append((caller, "passthrough"))
            return real_tsp(store, **kw)

        return mock.patch.object(collaboration_mod, "team_screening_permission", gated)

    def _decisions_count(self) -> int:
        return _scalar(self.owner_db, "SELECT COUNT(*) FROM decisions")

    # -- 01 无注入对照：外层与锁内全真实 → 200（证明载体干净）---------------
    def test_01_no_injection_control_200(self):
        resp = self.client.post(f"/api/tasks/{self.task}/decisions",
                                json={"zotero_key": "Z2", "decision": "include"},
                                headers=self.owner["headers"])
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["saved"], resp.text)
        self.assertEqual(self._decisions_count(), 2, "对照决策真实落盘")

    # -- 02 B3 核心：锁内职责查询 Corrupt → desktop 最终 409（红侧 503 缺口）-
    def test_02_inner_corrupt_desktop_maps_409(self):
        calls: list = []
        with self._inject_inner(calls, collaboration_mod.CollaborationStoreCorruptError(
                "simulated corrupt at inner screening duty query (r2 fault injection)")):
            with self._mode("desktop"):
                resp = self.client.post(f"/api/tasks/{self.task}/decisions",
                                        json={"zotero_key": "Z3", "decision": "include"},
                                        headers=self.owner["headers"])
        print("R2_B3_CALLTRACE", json.dumps(calls))
        print("R2_B3_DESKTOP_CORRUPT", resp.status_code,
              json.dumps(self._detail(resp), ensure_ascii=False))
        # 缝隙定位：外层快门真实通过在先，锁内缝隙抛 Corrupt 恰一次。
        self.assertTrue(calls, "注入未命中任何 team_screening_permission 调用")
        self.assertEqual(calls[0][0], self.OUTER_GATE,
                         f"首个权限查询必须来自外层快门（真实透传）；calls={calls}")
        self.assertTrue(all(c[1] == "passthrough" for c in calls if c[0] == self.OUTER_GATE),
                        f"外层快门不得被注入；calls={calls}")
        self.assertEqual(
            [c for c in calls if c[0] == self.INNER_GATE],
            [(self.INNER_GATE, "raise", "CollaborationStoreCorruptError")],
            f"锁内职责门必须恰好被注入一次 Corrupt；calls={calls}")
        self.assertEqual(
            resp.status_code, 409,
            f"{TAG_B3_CORRUPT_409}：锁内 Corrupt 在 desktop 必须按修库合同映射 409；"
            f"实际={resp.status_code} body={resp.text[:200]}")
        self.assertEqual(self._detail(resp), main_mod.COLLABORATION_STORE_CORRUPT_MESSAGE)
        self.assertEqual(self._decisions_count(), 2, "注入窗口内零落盘")

    # -- 03 server：锁内 Corrupt → 503 fail-closed（既有合同不回归）---------
    def test_03_inner_corrupt_server_maps_503(self):
        calls: list = []
        with self._inject_inner(calls, collaboration_mod.CollaborationStoreCorruptError(
                "simulated corrupt at inner screening duty query (r2 fault injection)")):
            resp = self.client.post(f"/api/tasks/{self.task}/decisions",
                                    json={"zotero_key": "Z3", "decision": "include"},
                                    headers=self.owner["headers"])
        print("R2_B3_CALLTRACE_SERVER", json.dumps(calls))
        self.assertTrue(any(c[0] == self.INNER_GATE and c[1] == "raise" for c in calls),
                        f"server 侧注入必须命中锁内缝隙；calls={calls}")
        self.assertEqual(resp.status_code, 503,
                         f"server 锁内 Corrupt 维持 503 fail-closed；实际={resp.status_code}")
        self.assertEqual(self._decisions_count(), 2)

    # -- 04 Unavailable（暂时故障）恒 503：desktop/server 均保持 ------------
    def test_04_inner_unavailable_maps_503_both_modes(self):
        for tmp_mode in ("desktop", "server"):
            calls: list = []
            with self._inject_inner(calls, collaboration_mod.OwnerUnavailableError(
                    "simulated transient screening lookup failure (r2 fault injection)")):
                with self._mode(tmp_mode):
                    resp = self.client.post(f"/api/tasks/{self.task}/decisions",
                                            json={"zotero_key": "Z3", "decision": "include"},
                                            headers=self.owner["headers"])
            print("R2_B3_UNAVAILABLE", tmp_mode, resp.status_code,
                  json.dumps(self._detail(resp), ensure_ascii=False))
            self.assertTrue(any(c[0] == self.INNER_GATE and c[1] == "raise"
                                for c in calls), f"calls={calls}")
            self.assertEqual(resp.status_code, 503,
                             f"{tmp_mode} 真实 Unavailable 仍 503；实际={resp.status_code}")
        self.assertEqual(self._decisions_count(), 2)


if __name__ == "__main__":
    unittest.main()
