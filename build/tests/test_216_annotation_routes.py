"""P20 annotation same-stage protection tests (C12 author side; 2.1.6 P20).

Four P20-named tests over the **real** app routes: real HTTP via TestClient,
real team building (移植 test_216_member_write_routes.py 的真实建队配方：
业务角色单选/holder 库 P04 ledger/F1 facts/F2 registry+真 source token/
create_task 自动登记 protocol 行——零 mock、零窗口 patch), real P10 window /
P13 commit protocol, and the real highlight writers
(``fulltext.add_highlight_rects`` / note UPDATE / ``delete_highlight`` all
execute on the borrowed target connection).  唯一观察点是响应与本人成员库
的行级事实（无任何产品接缝替换）。

覆盖（work-packages P20 节需求条目 1/2/3）：

1. ``test_add_note_delete_completed_fulltext``——已完成 fulltext 键上
   add/note/delete 全部 409 ``stage_completed_read_only``（拒绝后零业务
   写：行仍在、备注原样），vote 有的同阶段历史门注释同样有。
2. ``test_unfinished_and_other_key_positive_controls``——未完成键与其余
   开放键 add/note/delete 全部 200（不过度阻断），receipt 真实落账。
3. ``test_add_delete_do_not_gain_note_policy``——note 编辑保留原额外
   policy 门（stale ``expected_policy_version`` → 409
   ``workflow_version_conflict``），add/delete 不继承该门（带无效
   expected_policy_version 字段仍 200，模型不收该字段）。
4. ``test_highlight_id_spoof``——保护键来自 highlight 存储行（P11 同窗
   artifact 解析），不来自客户端伪造 zotero_key：伪造开放键 + 已完成键
   高亮 ID 的 note 编辑按**存储键**拒绝（item_keys==[stored]）；screener
   伪装 403；跨任务 ID 维持原 not-found/权限语义；solo 任务 add 走既有
   路径 200（solo 分支字节不变的冒烟）。

Product imports are deferred to ``setUpClass`` (loadable under ``python -I -B``).
"""
from __future__ import annotations

import importlib
import sqlite3
import unittest
import uuid
from contextlib import closing
from pathlib import Path

#: 三键分工：A=两阶段全完成（受保护）；B/C=仅 stage1 完成（fulltext 开放）。
KEY_DONE = "P20A"
KEY_OPEN = "P20B"
KEY_OTHER = "P20C"
_UPSERT_LIBRARY = (
    "INSERT INTO team_vote_revision_meta (singleton, format_version,"
    " library_id, seeded_stage1, seeded_stage2, advances)"
    " VALUES (1, 2, ?, 0, 0, 0) ON CONFLICT(singleton) DO UPDATE SET"
    " library_id=excluded.library_id")
_INSERT_ARTICLE = (
    "INSERT INTO articles (zotero_key, title, is_duplicate_of, import_order)"
    " VALUES (?, ?, NULL, ?)")
_LATCH = "UPDATE workflow_state SET team_latched=1 WHERE singleton=1"


def _event(text: str, note: str | None = None) -> dict:
    """一条合法高亮事件（rects 恰 4 分量——MM5-13 写入端口径）。"""
    event = {"kind": "highlight", "page": 1, "text": text,
             "color": "jade", "rects": [[0.1, 0.1, 0.5, 0.5]]}
    if note is not None:
        event["note"] = note
    return event


def _close_quietly(conn):
    try:
        conn.close()
    except Exception:
        pass


def _borrowed_writer(path):
    """真实产品写连接形态（F2/F1 append 入口要求）：Row 工厂＋IMMEDIATE。"""
    conn = sqlite3.connect(str(path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN IMMEDIATE")
    return conn


class AnnotationRoutesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from custom_backend import auth as auth_mod
        from custom_backend import main as main_mod

        cls.client = TestClient(main_mod.app)
        cls.client.__enter__()
        cls.main_mod = main_mod
        cls.users = {}
        for name in ("p20owner", "p20screener", "p20coder"):
            user = auth_mod.create_user(name, "P20-Annotation-Routes-2026")
            cls.users[name] = {
                "user_id": user["id"], "username": user["username"],
                "token": auth_mod.create_access_token(user["id"], user["username"])}
        cls._team_task()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    @classmethod
    def _auth(cls, name):
        return {"Authorization": "Bearer " + cls.users[name]["token"]}

    # ------------------------------------------------------------------
    # 真实建队（移植 P16 member_write_routes 基座配方，零 mock）
    # ------------------------------------------------------------------
    @classmethod
    def _team_task(cls):
        collab = importlib.import_module("coscreen.task_collaboration")
        cdb = importlib.import_module("coscreen.db")
        wf = importlib.import_module("coscreen.review_workflow")
        tasks = importlib.import_module("coscreen.tasks")
        tf = importlib.import_module("coscreen.team_terminal_facts")
        tr = importlib.import_module("coscreen.team_terminal_registry")
        receipts = importlib.import_module("coscreen.team_flow_receipts")
        tsl = importlib.import_module("custom_backend.team_source_lifecycle")
        coding_evidence = importlib.import_module("coscreen.coding_evidence")

        owner = cls.users["p20owner"]
        owner_uid = owner["user_id"]
        cls.task_id = cls.client.post(
            "/api/tasks", json={"name": "p20-annotation-" + uuid.uuid4().hex[:8]},
            headers=cls._auth("p20owner")).json()["task_id"]
        cls.info = tasks.find_task(cls.task_id, cls.main_mod.DATA_DIR)
        task_dir = cls.info.dir
        cls.collab_path = collab.shared_store_path(task_dir)
        if not cls.collab_path.is_file():
            collab.initialize_task_store(
                task_dir, task_id=cls.task_id, owner_user_id=owner_uid,
                owner_username=owner["username"])
        # 属主业务角色单选（reconciler）；两位成员 coder=必需筛选人。
        collab.set_business_role(
            cls.collab_path, actor_user_id=owner_uid, user_id=owner_uid,
            business_role="reconciler", task_id=cls.task_id)
        slug = tasks.slugify_screener
        cls.user_db = {name: task_dir / f"{slug(u['username'])}.db"
                       for name, u in cls.users.items()}
        for name in ("p20screener", "p20coder"):
            uid = cls.users[name]["user_id"]
            collab.grant_role(cls.collab_path, actor_user_id=owner_uid,
                              user_id=uid, role="coder", task_id=cls.task_id)
            collab.set_business_role(
                cls.collab_path, actor_user_id=owner_uid, user_id=uid,
                business_role="coder", task_id=cls.task_id)
        articles = [(k, f"P20 {k}", i)
                    for i, k in enumerate((KEY_DONE, KEY_OPEN, KEY_OTHER))]
        # 每库：init_db（P04 ledger 真实 meta DDL）+ migrate + F1 facts + P07
        # receipts + 确定性 library + articles（owner 另 latch——P10 配方）。
        for name, path in cls.user_db.items():
            cdb.init_db(path)
            wf.migrate(path)
            tf.prepare_team_terminal_facts(path)
            receipts.prepare_team_flow_receipts(path)
            conn = sqlite3.connect(str(path), timeout=15)
            try:
                conn.execute("PRAGMA foreign_keys=ON")
                uid = cls.users[name]["user_id"]
                if name != "p20owner":
                    # P18 S-3 同步：owner 库保留 create_task hook 安装的真实
                    # 账本身份；成员库用确定性 uid-hex 并与 source 登记一致。
                    conn.execute(_UPSERT_LIBRARY, ("%032x" % uid,))
                conn.executemany(_INSERT_ARTICLE, articles)
                if name == "p20owner":
                    conn.execute(_LATCH)
                conn.commit()
            finally:
                conn.close()
            coding_evidence.ensure_fields(path)
        # collab 组合库：F2 registry＋protocol 行（create_task 自动登记）+F1。
        tr.prepare_team_terminal_registry(cls.collab_path)
        with closing(sqlite3.connect(str(cls.collab_path))) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT task_instance_id FROM main.team_task_protocol"
                " LIMIT 1").fetchone()
        assert row is not None, (
            "create_task must have registered the team protocol row "
            "(P18 explicit registration hook)")
        cls.instance_id = row["task_instance_id"]
        tf.prepare_team_terminal_facts(cls.collab_path)
        # 真实 source 登记（P06 真 token——P10 _register_source 同款）。
        for name in ("p20screener", "p20coder"):
            uid = cls.users[name]["user_id"]
            library = "%032x" % uid
            token = tsl.make_source_identity(
                task_dir=task_dir, source_path=cls.user_db[name],
                source_user_id=uid, source_library_id=library)
            writer = _borrowed_writer(cls.collab_path)
            try:
                tr.register_source_on(
                    writer, task_instance_id=cls.instance_id,
                    source_user_id=uid, source_library_id=library,
                    source_path_identity=token,
                    registration_id=f"reg-p20-{name}")
                writer.commit()
            finally:
                _close_quietly(writer)

        # stage1 上游完成：两位必需筛选人各以真实 member_vote/screen 写入
        # 三键 include（fulltext 链由此开闸；A/B/C 全部 stage1 cleared）。
        for key in (KEY_DONE, KEY_OPEN, KEY_OTHER):
            for name in ("p20screener", "p20coder"):
                seeded = cls.client.post(
                    f"/api/tasks/{cls.task_id}/decisions",
                    json={"zotero_key": key, "decision": "include",
                          "reason": "", "notes": "", "tags": None},
                    headers=cls._auth(name))
                assert seeded.status_code == 200, seeded.text
        # 完成前先种高亮（此后 A 键 fulltext 完成即封版，只有既有行可攻击）：
        # H_DONE 挂 A（受保护键）；H_OPEN1/H_OPEN2 挂 B（开放键）。
        cls.h_done = cls._add(KEY_DONE, "P20 H DONE", note="seed-done")
        cls.h_open1 = cls._add(KEY_OPEN, "P20 H OPEN1", note="seed-open1")
        cls.h_open2 = cls._add(KEY_OPEN, "P20 H OPEN2", note="seed-open2")
        # stage2 完成：两位必需筛选人各以真实 member_vote/fulltext 写入
        # KEY_DONE 的 include——完成即封版（stage_completed_read_only）；
        # KEY_OPEN/KEY_OTHER 刻意不播 stage2，保持 fulltext 开放。
        for name in ("p20screener", "p20coder"):
            seeded = cls.client.post(
                f"/api/tasks/{cls.task_id}/stage2/decisions",
                json={"zotero_key": KEY_DONE, "decision": "include",
                      "reason": "", "notes": "", "tags": None},
                headers=cls._auth(name))
            assert seeded.status_code == 200, seeded.text

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @classmethod
    def _add(cls, key: str, text: str, *, note: str | None = None) -> int:
        """真实 POST /highlights 新增一条高亮，返回新行 ID。"""
        resp = cls.client.post(
            f"/api/tasks/{cls.task_id}/highlights",
            json={"zotero_key": key, "events": [_event(text, note)]},
            headers=cls._auth("p20screener"))
        assert resp.status_code == 200, resp.text
        row = next(h for h in resp.json()["highlights"] if h["text"] == text)
        return row["id"]

    def _note(self, highlight_id: int, key: str, *, note: str,
              expected_note: str, expected_policy_version=None,
              auth="p20screener"):
        body = {"zotero_key": key, "note": note, "expected_note": expected_note}
        if expected_policy_version is not None:
            body["expected_policy_version"] = expected_policy_version
        return self.client.patch(
            f"/api/tasks/{self.task_id}/highlights/{highlight_id}",
            json=body, headers=self._auth(auth))

    def _delete(self, highlight_id: int, *, auth="p20screener"):
        return self.client.delete(
            f"/api/tasks/{self.task_id}/highlights/{highlight_id}",
            headers=self._auth(auth))

    def _row(self, name: str, sql: str, args=()):
        with closing(sqlite3.connect(str(self.user_db[name]))) as conn:
            return conn.execute(sql, args).fetchone()

    def _receipt_count(self, name: str) -> int:
        return self._row(name, "SELECT COUNT(*) FROM team_flow_receipts")[0]

    @staticmethod
    def _completed_blocked(resp) -> dict:
        """统一 409 stage_completed_read_only 载荷断言，返回 detail。"""
        assert resp.status_code == 409, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "stage_completed_read_only", detail
        return detail

    # ------------------------------------------------------------------
    # 1. completed fulltext：add/note/delete 全拒（不只保护 vote）
    # ------------------------------------------------------------------
    def test_add_note_delete_completed_fulltext(self):
        actor_db = self.user_db["p20screener"]
        before_rows = self._row(
            "p20screener", "SELECT COUNT(*) FROM highlights",
            )[0]
        before_note = self._row(
            "p20screener", "SELECT note FROM highlights WHERE id=?",
            (self.h_done,))[0]
        receipts_before = self._receipt_count("p20screener")

        add = self.client.post(
            f"/api/tasks/{self.task_id}/highlights",
            json={"zotero_key": KEY_DONE,
                  "events": [_event("P20 blocked add")]},
            headers=self._auth("p20screener"))
        detail = self._completed_blocked(add)
        self.assertEqual(detail["item_keys"], [KEY_DONE], detail)

        note = self._note(self.h_done, KEY_DONE, note="tampered",
                          expected_note="seed-done")
        detail = self._completed_blocked(note)
        self.assertEqual(detail["item_keys"], [KEY_DONE], detail)

        delete = self._delete(self.h_done)
        detail = self._completed_blocked(delete)
        self.assertEqual(detail["item_keys"], [KEY_DONE], detail)

        # 拒绝=零业务写：行数不变、原备注原样、无新 receipt。
        self.assertEqual(self._row(
            "p20screener", "SELECT COUNT(*) FROM highlights")[0], before_rows)
        self.assertEqual(self._row(
            "p20screener", "SELECT note FROM highlights WHERE id=?",
            (self.h_done,))[0], before_note)
        self.assertEqual(self._receipt_count("p20screener"), receipts_before)

    # ------------------------------------------------------------------
    # 2. unfinished / other-key positive controls（不过度阻断）
    # ------------------------------------------------------------------
    def test_unfinished_and_other_key_positive_controls(self):
        receipts_before = self._receipt_count("p20screener")
        # 开放键 B：新增 → 200。
        new_id = self._add(KEY_OPEN, "P20 H OPEN3", note="seed-open3")
        # 开放键 B：编辑既有批注（expected_note 与存储值一致）→ 200。
        note = self._note(self.h_open1, KEY_OPEN, note="updated-open1",
                          expected_note="seed-open1")
        self.assertEqual(note.status_code, 200, note.text)
        self.assertEqual(note.json()["highlight"]["note"], "updated-open1")
        # 开放键 B：删除本测试新增的行 → 200。
        delete = self._delete(new_id)
        self.assertEqual(delete.status_code, 200, delete.text)
        self.assertTrue(delete.json()["deleted"])
        # 其余开放键 C（other key）：add 同样 200，随后删净保持现场干净。
        other_id = self._add(KEY_OTHER, "P20 H OTHER", note="seed-other")
        self.assertEqual(self._delete(other_id).status_code, 200)
        # 行级事实：H_OPEN1 备注已更新；新增行确已删除；receipt 真实落账。
        self.assertEqual(self._row(
            "p20screener", "SELECT note FROM highlights WHERE id=?",
            (self.h_open1,))[0], "updated-open1")
        self.assertIsNone(self._row(
            "p20screener", "SELECT id FROM highlights WHERE id=?", (new_id,)))
        self.assertGreater(self._receipt_count("p20screener"), receipts_before)

    # ------------------------------------------------------------------
    # 3. add/delete 不继承 note 的额外 policy 门
    # ------------------------------------------------------------------
    def test_add_delete_do_not_gain_note_policy(self):
        # note 编辑保留原额外 policy 门：stale expected_policy_version →
        # 409 workflow_version_conflict（P12 policy 检查在窗口内先于业务写）。
        note = self._note(self.h_open2, KEY_OPEN, note="never-written",
                          expected_note="seed-open2",
                          expected_policy_version=999999)
        self.assertEqual(note.status_code, 409, note.text)
        self.assertEqual(note.json()["detail"]["code"],
                         "workflow_version_conflict", note.text)
        # 拒绝零写入。
        self.assertEqual(self._row(
            "p20screener", "SELECT note FROM highlights WHERE id=?",
            (self.h_open2,))[0], "seed-open2")
        # add/delete 不获得该门：同键 body 带无效 expected_policy_version
        # 字段（模型不收）仍 200——版本/paused/source 只属于 note 编辑。
        added = self.client.post(
            f"/api/tasks/{self.task_id}/highlights",
            json={"zotero_key": KEY_OTHER, "events": [_event("P20 no-policy")],
                  "expected_policy_version": 999999},
            headers=self._auth("p20screener"))
        self.assertEqual(added.status_code, 200, added.text)
        row = next(h for h in added.json()["highlights"]
                   if h["text"] == "P20 no-policy")
        deleted = self._delete(row["id"])
        self.assertEqual(deleted.status_code, 200, deleted.text)
        # member_annotation 的 TeamOperation 不携带 expected_version
        # （team_write_checks._validated_stage 对非 policy operation 的
        # 非 None expected_version 直接 ValueError——产品侧恒传 None）。
        from custom_backend.team_write_checks import team_operation_target
        from coscreen.team_flow_types import TeamOperation
        self.assertEqual(
            team_operation_target(TeamOperation(
                name="member_annotation", stage="fulltext")), "actor")

    # ------------------------------------------------------------------
    # 4. highlight ID 伪造：保护键来自存储行（P11 同窗 artifact 解析）
    # ------------------------------------------------------------------
    def test_highlight_id_spoof(self):
        # (a) 已完成键上的高亮 ID + 客户端伪造的开放键 zotero_key：按
        #     **存储行键**拒绝——item_keys==[KEY_DONE]（断言锚：
        #     affected.item_key == stored_highlight_key），不是伪造键。
        spoof = self._note(self.h_done, KEY_OPEN, note="spoofed-key",
                           expected_note="seed-done")
        detail = self._completed_blocked(spoof)
        self.assertEqual(detail["item_keys"], [KEY_DONE], detail)
        self.assertNotEqual(detail["item_keys"], [KEY_OPEN], detail)
        # (b) delete 只有 ID（URL 无键可伪造）：仍按存储行键拒绝。
        detail = self._completed_blocked(self._delete(self.h_done))
        self.assertEqual(detail["item_keys"], [KEY_DONE], detail)
        # (c) 存储行未被 (a)/(b) 触碰。
        self.assertEqual(self._row(
            "p20screener", "SELECT note FROM highlights WHERE id=?",
            (self.h_done,))[0], "seed-done")
        # (d) screener 伪装（ID 属他人工作区入口）：require_user/写窗
        #     双层拦截，原 403 权限语义不变。
        spoofed_ws = self.client.post(
            f"/api/tasks/{self.task_id}/highlights?screener=p20coder",
            json={"zotero_key": KEY_OPEN, "events": [_event("P20 spoof ws")]},
            headers=self._auth("p20screener"))
        self.assertEqual(spoofed_ws.status_code, 403, spoofed_ws.text)
        # (e) 跨任务 ID：solo 任务先真实新增一条高亮（solo 分支冒烟 200），
        #     其 ID 打到 team 任务上 → 本人成员库无此行 → 原 404 语义。
        solo_id = self._solo_highlight()
        cross = self._note(solo_id, KEY_OPEN, note="cross-task",
                           expected_note="seed-done")
        self.assertEqual(cross.status_code, 404, cross.text)
        self.assertEqual(self._delete(solo_id).status_code, 404)

    def _solo_highlight(self) -> int:
        """solo 任务真实新增高亮（既有 solo 分支字节不变的冒烟）。

        返回一个**确定不存在于 team 本人成员库**的行 ID（solo 库行号推到
        team 库当前最大 id 之上），使跨任务 ID 语义＝team 工作区无此行。
        """
        cdb = importlib.import_module("coscreen.db")
        tasks = importlib.import_module("coscreen.tasks")
        slug = tasks.slugify_screener
        team_max = self._row(
            "p20screener", "SELECT COALESCE(MAX(id), 0) FROM highlights")[0]
        solo_task = self.client.post(
            "/api/tasks", json={"name": "p20-solo-" + uuid.uuid4().hex[:8]},
            headers=self._auth("p20owner")).json()["task_id"]
        info = tasks.find_task(solo_task, self.main_mod.DATA_DIR)
        db = info.dir / f"{slug(self.users['p20owner']['username'])}.db"
        cdb.init_db(db)
        with closing(sqlite3.connect(str(db))) as conn:
            conn.execute(_INSERT_ARTICLE, ("P20SOLO", "P20 solo article", 0))
            conn.commit()
        events = [_event(f"P20 solo H {i}") for i in range(team_max + 3)]
        resp = self.client.post(
            f"/api/tasks/{solo_task}/highlights",
            json={"zotero_key": "P20SOLO", "events": events},
            headers=self._auth("p20owner"))
        assert resp.status_code == 200, resp.text
        return max(h["id"] for h in resp.json()["highlights"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
