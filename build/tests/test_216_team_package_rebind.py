"""R8 带历史团队包导入：协议自动重绑 + 迁移审计 + 响应提示（用户裁定 a）。

背景（P3 复审探针实证）：带历史团队包导入后 ``team_task_protocol.task_id``
仍是源部署的本地绑定；产品守卫触发器 ``team_task_protocol_guard_update``
禁止 UPDATE 改绑（ABORT ``identity/state regression``）→ keyed 计划按新
task_id 解析落空 → 团队写 503 ``task_not_registered``（团队包"能看不能写"）。

裁定 a：导入组装窗口内（temp_dir 整体改名前）自动重绑协议 + 落迁移审计
receipt + 响应可见提示。本文件钉死：

1. 红→绿主用例：真实团队任务导出 → 同账号导入（真实 HTTP）→ 属主团队
   appraisal 写 200（修前 = typed 503 ``task_not_registered``）；
2. 审计与提示：``team_provenance.rebound=true`` + message 键/参数/兜底串，
   ``team_flow_receipts`` 落 ``team_protocol:import_rebind`` 行（旧/新
   task_id、时间、触发者=importer）；
3. 守卫不削弱：重绑后两条守卫触发器 SQL 与产品 canonical DDL 逐字一致，
   UPDATE/DELETE 仍 ABORT，registry 精确布局仍通过；
4. 退化保形：自邀（无其他成员 grant）personal 形状导入后属主写仍 200
   （重绑不得把个人任务变成团队语义）；
5. legacy/包内无协作库：不受扰（has_history False、无重绑字段）；
6. ``legacy_verified`` 行：绑定字段重算 adoption evidence（文档内嵌
   task_id/owner_user_id），重绑后强读不判 corrupt。

夹具配方移植 ``test_216_team_package_member_prepare.py``（真实 HTTP/真实
建任务/真实导出；``python -I -B`` 可载；env 白名单 ``COBOOKSHELF_DATA_DIR``
+ ``REVIEWFLOW_MODE=server`` 由 conftest 钉）。零 mock 业务路径。
"""
from __future__ import annotations

import json
import sqlite3
import unittest
import uuid
from contextlib import closing
from pathlib import Path

_OWNER = "r8pkgowner"
_MEMBER = "r8pkgmember"
_TOOL = "casp-qualitative"
_KEY = "R8K1"
#: 未投任何票的第二键：导入后成员团队写（member_vote/screen）的目标。
_KEY2 = "R8K2"
_SEED = "2026-01-01T00:00:00Z"


def _borrowed_writer(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN IMMEDIATE")
    return conn


class TeamPackageRebindTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from custom_backend import auth as auth_mod
        from custom_backend import main as main_mod

        cls.main_mod = main_mod
        cls.client = TestClient(main_mod.app)
        cls.client.__enter__()
        cls.users = {}
        for name in (_OWNER, _MEMBER):
            user = auth_mod.create_user(name, "R8-Rebind-Fix-2026")
            cls.users[name] = {
                "user_id": user["id"], "username": user["username"],
                "token": auth_mod.create_access_token(
                    user["id"], user["username"])}
        cls.tasks_mod = __import__("coscreen.tasks", fromlist=["tasks"])
        cls.package_api = __import__(
            "custom_backend.task_package_api", fromlist=["task_package_api"])
        cls.owner_slug = cls.tasks_mod.slugify_screener(_OWNER)
        cls.member_slug = cls.tasks_mod.slugify_screener(_MEMBER)
        cls.team_exported = cls._build_team_and_export()
        cls.solo_exported = cls._build_solo_and_export()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    @classmethod
    def _auth(cls, name):
        return {"Authorization": "Bearer " + cls.users[name]["token"]}

    @classmethod
    def _task_dir(cls, task_id: str) -> Path:
        info = cls.tasks_mod.find_task(task_id, cls.main_mod.DATA_DIR)
        return Path(info.dir)

    @classmethod
    def _seed_votes(cls, task_dir: Path, slug: str) -> None:
        """属主/成员库同款已决票夹具（真实表由产品迁移建；仅补行）。"""
        from coscreen import db as db_mod
        db = task_dir / f"{slug}.db"
        if not db.is_file():
            db_mod.init_db(db)
        with closing(sqlite3.connect(str(db))) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            existing = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "articles" not in existing:
                owner_db = task_dir / f"{cls.owner_slug}.db"
                with closing(sqlite3.connect(str(owner_db))) as own:
                    ddl = {row[0]: row[1] for row in own.execute(
                        "SELECT name, sql FROM sqlite_master WHERE type='table'"
                        " AND name IN ('articles','decisions','stage2_decisions')")}
                for table in ("articles", "decisions", "stage2_decisions"):
                    conn.execute(ddl[table])
            conn.execute(
                "INSERT OR IGNORE INTO articles(zotero_key,title,is_duplicate_of,"
                "import_order) VALUES(?,?,NULL,0)", (_KEY, "R8 seed title"))
            # 第二键只入目录、不投票：成员团队写的开放目标（无完成事实）。
            conn.execute(
                "INSERT OR IGNORE INTO articles(zotero_key,title,is_duplicate_of,"
                "import_order) VALUES(?,?,NULL,1)", (_KEY2, "R8 open title"))
            for table in ("decisions", "stage2_decisions"):
                conn.execute(
                    f"INSERT OR REPLACE INTO {table}(zotero_key,decision,"
                    "exclusion_reason,notes,tags,screened_at,updated_at)"
                    f" VALUES(?,'include','','','', ?, ?)", (_KEY, _SEED, _SEED))
            conn.commit()

    @classmethod
    def _build_team_and_export(cls) -> bytes:
        """真实建队（属主+成员 grant/latch/choice）→ 双票 → 真实 appraisal
        → 整包导出。包内 collab 携带 protocol 行（task_id=源任务）与 latched
        属主库——即 R8「带历史团队包」的输入形状。"""
        client = cls.client
        owner = cls.users[_OWNER]
        task_id = client.post(
            "/api/tasks", json={"name": "r8pkg-" + uuid.uuid4().hex[:8]},
            headers=cls._auth(_OWNER)).json()["task_id"]
        cls.team_source_task = task_id
        resp = client.post(
            f"/api/tasks/{task_id}/collaboration/invite",
            json={"username": _MEMBER, "role": "coder"},
            headers=cls._auth(_OWNER))
        assert resp.status_code == 201, resp.text
        resp = client.put(
            f"/api/tasks/{task_id}/collaboration/business-role/"
            f"{owner['user_id']}",
            json={"business_role": "coder"}, headers=cls._auth(_OWNER))
        assert resp.status_code == 200, resp.text
        task_dir = cls._task_dir(task_id)
        cls._seed_votes(task_dir, cls.owner_slug)
        cls._seed_votes(task_dir, cls.member_slug)
        # 首次团队上下文把属主库 latch 为 team（包内随存 → 导入后 is_team）；
        # team-progress 是属主侧的显式团队读入口（幂等准备+context）。
        resp = client.get(f"/api/tasks/{task_id}/team-progress",
                          headers=cls._auth(_OWNER))
        assert resp.status_code == 200, resp.text
        resp = client.put(
            f"/api/tasks/{task_id}/appraisal/{_KEY}",
            json={"tool": _TOOL, "responses": {"1": "Yes"}},
            headers=cls._auth(_OWNER))
        assert resp.status_code == 200, resp.text
        resp = client.post(
            f"/api/tasks/{task_id}/package/export",
            json={"include_pdfs": False}, headers=cls._auth(_OWNER))
        assert resp.status_code == 200, resp.text
        return resp.content

    @classmethod
    def _build_solo_and_export(cls) -> bytes:
        """自邀退化形状：无任何其他成员 grant（personal 派生），但协议行
        仍随包（create_task 登记 trusted_new_task）。"""
        client = cls.client
        task_id = client.post(
            "/api/tasks", json={"name": "r8solo-" + uuid.uuid4().hex[:8]},
            headers=cls._auth(_OWNER)).json()["task_id"]
        task_dir = cls._task_dir(task_id)
        from coscreen import db as db_mod
        db_mod.init_db(task_dir / f"{cls.owner_slug}.db")
        cls._seed_votes(task_dir, cls.owner_slug)
        resp = client.post(
            f"/api/tasks/{task_id}/package/export",
            json={"include_pdfs": False}, headers=cls._auth(_OWNER))
        assert resp.status_code == 200, resp.text
        return resp.content

    def _import(self, package: bytes):
        return self.client.post(
            "/api/tasks/import-package",
            files={"package": ("pkg.zip", package, "application/zip")},
            headers=self._auth(_OWNER))

    @classmethod
    def _protocol(cls, collab: Path) -> sqlite3.Row:
        with closing(sqlite3.connect(str(collab))) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(
                "SELECT * FROM team_task_protocol").fetchone()

    @classmethod
    def _sqlite_master_sql(cls, collab: Path, kind: str, name: str) -> str:
        with closing(sqlite3.connect(str(collab))) as conn:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type=? AND name=?",
                (kind, name)).fetchone()
        return " ".join((row[0] or "").split()) if row else ""

    # ------------------------------------------------------------------
    # 1. 红→绿主用例：带历史团队包导入后属主团队写 200
    # ------------------------------------------------------------------
    def test_imported_history_team_owner_write_succeeds(self):
        resp = self._import(self.team_exported)
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        new_id = body["task_id"]
        prov = body["team_provenance"]
        # 导入是「新任务」语义：协作库改绑按设计清掉源部署 role grants。
        # 本用例只隔离**协议绑定墙**，故经产品函数 grant_role 直接补回成员
        # 授权（grant_role 不依赖协议/写窗；HTTP invite 本身也走团队写窗，
        # 会在底版先撞同一面墙、遮蔽成员写红点）。
        from coscreen import task_collaboration as collab_mod
        collab_mod.grant_role(
            str(self._task_dir(new_id) / "collaboration.db"),
            actor_user_id=self.users[_OWNER]["user_id"],
            user_id=self.users[_MEMBER]["user_id"], role="coder",
            task_id=new_id)
        # 红点（修前 = typed 503 task_not_registered；P3 复审探针实证）：
        # **成员团队写**必须 200。member_vote/screen 是成员侧真实团队写路径
        # （非属主 grant 派生 team + keyed 计划按新 task_id 解析）；底版协议行
        # 仍绑旧 task_id → 写窗 keyed plan 直接 503 task_not_registered。
        # 断言置于 provenance 字段断言之前——底版的红必须落在「团队写 503」
        # 本身，而不是先因新增响应键缺失而 KeyError。
        member_write = self.client.post(
            f"/api/tasks/{new_id}/decisions",
            json={"zotero_key": _KEY2, "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=self._auth(_MEMBER))
        self.assertEqual(
            member_write.status_code, 200,
            f"导入后成员团队写失败（修前应为 typed 503 "
            f"task_not_registered）：{member_write.text}")
        # 属主侧团队写（appraisal 真实写路径）同样必须可达。
        owner_write = self.client.put(
            f"/api/tasks/{new_id}/appraisal/{_KEY}",
            json={"tool": _TOOL, "responses": {"1": "Yes"}},
            headers=self._auth(_OWNER))
        self.assertEqual(
            owner_write.status_code, 200,
            f"导入后属主团队写失败：{owner_write.text}")
        # 带历史分支 + 已重绑。
        self.assertTrue(prov["has_history"])
        self.assertTrue(prov["rebound"], f"协议未重绑：{prov}")
        self.assertEqual(prov["new_task_id"], new_id)
        self.assertNotEqual(prov["old_task_id"], new_id)
        # 落盘核验：协议行 task_id 已指向新任务，instance/owner_library 保留。
        new_dir = self._task_dir(new_id)
        row = self._protocol(new_dir / "collaboration.db")
        self.assertEqual(row["task_id"], new_id)
        self.assertEqual(row["task_instance_id"],
                         prov["preserved_task_instance_id"])
        self.assertEqual(row["owner_library_id"],
                         prov["preserved_owner_library_id"])
        self.assertEqual(row["owner_user_id"],
                         self.users[_OWNER]["user_id"])

    # ------------------------------------------------------------------
    # 2. 迁移审计 + 响应提示
    # ------------------------------------------------------------------
    def test_rebind_audit_receipt_and_message(self):
        resp = self._import(self.team_exported)
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        new_id = body["task_id"]
        prov = body["team_provenance"]
        # 提示字段（team_provenance 内增量；十语词条见 REPORT 清单）。
        self.assertEqual(prov["rebind_message_key"],
                         "task_package.team_protocol_rebound")
        self.assertIsInstance(prov["rebind_message"], str)
        self.assertTrue(prov["rebind_message"].strip())
        self.assertEqual(prov["rebind_params"]["new_task_id"], new_id)
        self.assertEqual(prov["rebind_params"]["old_task_id"],
                         prov["old_task_id"])
        self.assertRegex(prov["rebind_receipt_id"], r"^[0-9a-f]{64}$")
        # 审计行：receipt 落 collab receipts（append-only，collab 原产）。
        collab = self._task_dir(new_id) / "collaboration.db"
        with closing(sqlite3.connect(str(collab))) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM team_flow_receipts WHERE operation=?",
                ("team_protocol:import_rebind",)).fetchall()
        self.assertEqual(len(rows), 1, f"迁移审计行数异常：{len(rows)}")
        receipt = rows[0]
        self.assertEqual(receipt["receipt_id"], prov["rebind_receipt_id"])
        self.assertEqual(receipt["task_instance_id"],
                         prov["preserved_task_instance_id"])
        self.assertEqual(receipt["actor_user_id"],
                         self.users[_OWNER]["user_id"])
        payload = json.loads(receipt["payload_json"])
        self.assertEqual(payload["result"]["old_task_id"], prov["old_task_id"])
        self.assertEqual(payload["result"]["new_task_id"], new_id)
        self.assertEqual(payload["result"]["importer_user_id"],
                         self.users[_OWNER]["user_id"])
        self.assertRegex(payload["result"]["rebound_at"],
                         r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
        self.assertEqual(receipt["origin"], "collab")

    # ------------------------------------------------------------------
    # 3. 守卫不削弱：触发器逐字一致 + UPDATE/DELETE 仍 ABORT + 布局精确
    # ------------------------------------------------------------------
    def test_guard_triggers_not_weakened(self):
        from coscreen import team_terminal_registry as registry
        resp = self._import(self.team_exported)
        self.assertEqual(resp.status_code, 200, resp.text)
        collab = self._task_dir(resp.json()["task_id"]) / "collaboration.db"
        # 逐字一致：用 registry 自身 _norm_sql（空白归一 + 仅剥离 CREATE
        # 头的可选 main. 限定符）比较——与产品精确布局断言同一口径；
        # sqlite_master 存文本本就无 schema 前缀，绝不自行放宽比较。
        for name, expected in (
            ("team_task_protocol_guard_update",
             registry._TRIGGER_PROTOCOL_GUARD_SQL),
            ("team_task_protocol_no_delete",
             registry._TRIGGER_PROTOCOL_NO_DELETE_SQL),
        ):
            self.assertEqual(
                registry._norm_sql(
                    self._sqlite_master_sql(collab, "trigger", name)),
                registry._norm_sql(expected),
                f"守卫触发器被改写：{name}")
        # 精确布局（含触发器集合）仍通过产品准备断言。
        registry.prepare_team_terminal_registry(collab)
        # 守卫仍生效：UPDATE 改绑 / DELETE 均 ABORT。
        with closing(sqlite3.connect(str(collab))) as conn:
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("UPDATE team_task_protocol SET task_id='x'")
                conn.commit()
                self.fail("守卫失效：协议行 UPDATE 未被拒绝")
            except sqlite3.IntegrityError as exc:
                self.assertIn("identity/state regression", str(exc))
            finally:
                conn.rollback()
            try:
                conn.execute("DELETE FROM team_task_protocol")
                conn.commit()
                self.fail("守卫失效：协议行 DELETE 未被拒绝")
            except sqlite3.IntegrityError as exc:
                self.assertIn("delete forbidden", str(exc))
            finally:
                conn.rollback()
            # 重建中转表不得残留。
            leftover = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='team_task_protocol_pre_import'"
            ).fetchone()
            self.assertIsNone(leftover)

    # ------------------------------------------------------------------
    # 4. 退化保形：自邀 personal 形状导入后属主写仍 200
    # ------------------------------------------------------------------
    def test_self_invite_personal_shape_unchanged(self):
        resp = self._import(self.solo_exported)
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        new_id = body["task_id"]
        prov = body["team_provenance"]
        # 协议行随包 → 仍走重绑（new_covered 行），但任务派生态仍是 personal。
        self.assertTrue(prov["has_history"])
        self.assertTrue(prov["rebound"])
        resp = self.client.put(
            f"/api/tasks/{new_id}/appraisal/{_KEY}",
            json={"tool": _TOOL, "responses": {"1": "Yes"}},
            headers=self._auth(_OWNER))
        self.assertEqual(
            resp.status_code, 200,
            f"自邀 personal 形状被重绑破坏：{resp.text}")

    # ------------------------------------------------------------------
    # 5. legacy 包（无协作库）不受扰
    # ------------------------------------------------------------------
    def test_legacy_package_without_collab_untouched(self):
        import io
        import zipfile

        payloads: dict[str, bytes] = {}
        dropped = {"collaboration.db"}
        # 输入取自邀包（属主库含真实 P04 账本），仅剥协作库 = legacy 形状。
        with zipfile.ZipFile(io.BytesIO(self.solo_exported)) as archive:
            names = archive.namelist()
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            for name in names:
                if name == "manifest.json" or name in dropped:
                    continue
                payloads[name] = archive.read(name)
            manifest["members"] = [
                item for item in manifest["members"]
                if item["path"] not in dropped]
            # 改写成员的 manifest 摘要（非 sqlite 字节也按实际重签）。
            import hashlib
            for item in manifest["members"]:
                if item["path"] in payloads:
                    item["sha256"] = hashlib.sha256(
                        payloads[item["path"]]).hexdigest()
                    item["size"] = len(payloads[item["path"]])
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
            for name, data in payloads.items():
                target.writestr(name, data)
            target.writestr("manifest.json", json.dumps(
                manifest, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        resp = self._import(out.getvalue())
        self.assertEqual(resp.status_code, 200, resp.text)
        prov = resp.json()["team_provenance"]
        self.assertFalse(prov["has_history"])
        self.assertTrue(prov["registered"])
        self.assertNotIn("rebound", prov)

    # ------------------------------------------------------------------
    # 6. legacy_verified 行：绑定字段重算 adoption evidence
    # ------------------------------------------------------------------
    def test_legacy_verified_row_evidence_recomposed(self):
        import tempfile

        from coscreen import task_collaboration
        from coscreen import team_terminal_registry as registry
        from custom_backend import team_source_lifecycle as lifecycle

        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "legacy"
            task_dir.mkdir()
            collab = task_dir / "collaboration.db"
            task_collaboration.initialize_task_store(
                task_dir, task_id="legacy-src", owner_user_id=7,
                owner_username="legacy-owner")
            lifecycle.prepare_team_protocol_storage(
                collab_path=collab, source_paths=())
            writer = _borrowed_writer(collab)
            try:
                registry.register_protocol_on(
                    writer, task_id="legacy-src",
                    task_instance_id="legacy-inst-r8",
                    owner_user_id=7, owner_library_id="ab" * 16,
                    creation_path="legacy_adoption")
                writer.commit()
            finally:
                writer.close()
            writer = _borrowed_writer(collab)
            try:
                registry.mark_legacy_verified_on(
                    writer, task_instance_id="legacy-inst-r8",
                    expected_roster_revision=0,
                    adoption_evidence_ref="adopt-r8-ref",
                    adoption_evidence={
                        "sources": [],
                        "verification_scope": "r8 fixture",
                        "verification_method": "explicit owner adoption",
                        "conflict_handling": "typed fail-closed",
                        "prior_history_unknown": True,
                    })
                writer.commit()
            finally:
                writer.close()
            result = self.package_api._rebind_imported_team_protocol(
                task_dir, "r8-new-task-id", 42, "r8importer",
                "legacy-inst-r8")
            self.assertTrue(result["rebound"])
            self.assertTrue(result["evidence_recomposed"])
            with closing(sqlite3.connect(str(collab))) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("BEGIN")
                record = registry.read_protocol_for_task_on(
                    conn, task_id="r8-new-task-id")
                conn.rollback()
            self.assertIsNotNone(record)
            self.assertEqual(record["coverage_state"], "legacy_verified")
            document = json.loads(record["adoption_evidence_json"])
            self.assertEqual(document["task_id"], "r8-new-task-id")
            self.assertEqual(document["owner_user_id"], 42)
            self.assertEqual(record["adoption_evidence_ref"], "adopt-r8-ref")
            # 文档内 sources/scope/method/adopted_at 原样。
            self.assertEqual(document["sources"], [])
            self.assertEqual(document["verification_scope"], "r8 fixture")
            self.assertTrue(document["prior_history_unknown"])
            self.assertEqual(document["forward_coverage_start"],
                             record["adopted_at"])
            # 守卫仍逐字一致（legacy_verified 分支同样重建）。
            with closing(sqlite3.connect(str(collab))) as conn:
                sql = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='trigger'"
                    " AND name='team_task_protocol_guard_update'").fetchone()[0]
            self.assertEqual(
                registry._norm_sql(sql),
                registry._norm_sql(registry._TRIGGER_PROTOCOL_GUARD_SQL))


if __name__ == "__main__":
    unittest.main()
