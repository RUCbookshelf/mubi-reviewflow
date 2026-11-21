"""E7/P3 整包导入准备对齐测试（GAP_DESIGN_SURVEY 2026-11-20 §2 E7+§7 P3）。

首用例 = survey §8.1 残余风险首条（限时未实测形状）：「旧包成员库无 F1 +
choice 残留」端到端——真实团队任务导出包 → 模拟旧包（成员库剥 F1/receipts
四表，模拟 P14 前旧部署；collab business-role **choice** 不清——
``_rewrite_collaboration_store`` 只清 grants/settings）→ 导入 → 属主（coder
choice 命中同 uid，典型=单人部署自导自恢复）首次 appraisal 不得 500（修复
前 = 直调 ``runtime.mutate`` 无 A hook → actor receipts 读
TeamFlowReceiptUnavailable 未处理 500）。

覆盖：
1. 首用例：旧包导入后属主首次 appraisal 真实 HTTP 200（红→绿）；
2. 失配重登记：包内成员库被换为全新 ledger 身份（旧 source 行失配）→ 导入
   后 retire 旧行 + 原行 uid/真实身份重登记（F2 与库身份对齐）；
3. 正常包导入回归：未改包导入 200，provenance 如实上报 storage_prepared，
   属主/成员库团队 schema 齐（幂等复备）；
4. fresh 导入回归：无协作库包导入 200（trusted_new_task），属主库仍备齐；
5. 权限沿用族：未认证导入 401。

夹具配方移植 test_216_owner_auto_prepare.py / test_2_1_5_w5_archive_package.py
（真实 HTTP/真实建任务/真实导出；``python -I -B`` 可载；env 白名单
``COBOOKSHELF_DATA_DIR`` + ``REVIEWFLOW_MODE=server`` 由 conftest 钉）。包
改写（剥表/换库 + manifest 重签 sha256）仅为构造「旧部署包」输入形状，
导入/准备/对齐全部走产品路径，无手写 schema。
"""
from __future__ import annotations

import io
import json
import sqlite3
import unittest
import uuid
import zipfile
from contextlib import closing
from pathlib import Path

_OWNER = "p3pkgowner"
_MEMBER = "p3pkgmember"
_TOOL = "casp-qualitative"
_KEY = "P3K1"
_MEMBER_TEAM_TABLES = (
    "team_flow_receipts", "team_flow_receipts_meta",
    "team_terminal_seals", "team_terminal_facts_meta")
_SEED = "2026-01-01T00:00:00Z"


def _strip_team_tables(db_bytes: bytes, tables: tuple[str, ...]) -> bytes:
    """模拟 P14 前旧部署成员库：剥团队四表，其余（articles/decisions/appraisal
    records/vote ledger 身份）原样保留。"""
    src = Path("_strip_src.db")
    src.write_bytes(db_bytes)
    try:
        with closing(sqlite3.connect(str(src))) as conn:
            existing = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            for table in tables:
                if table in existing:
                    conn.execute(f"DROP TABLE {table}")
            conn.commit()
        return src.read_bytes()
    finally:
        src.unlink(missing_ok=True)


def _inject_stale_source_row(db_bytes: bytes, instance: str, uid: int,
                             token: str) -> bytes:
    """夹具：向包内 collab 注入一枚 active source 行（library_id 指向不存
    在的旧库身份 f*32、token=make_source_identity 真实产物）——模拟 survey
    E7「旧 source 行指向旧库身份」漂移形状（invite/roles 实测登记恒
    pending/member_library_missing，见 REPORT C-S10；夹具口径与
    test_216_appraisal_consensus_routes 的 register_source_on 同款）。"""
    src = Path("_collab_fix.db")
    src.write_bytes(db_bytes)
    try:
        with closing(sqlite3.connect(str(src))) as conn:
            conn.execute(
                "INSERT INTO team_source_registry(registration_id,"
                "task_instance_id,source_user_id,source_library_id,"
                "source_path_identity,generation,status,registered_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, instance, uid, "f" * 32,
                 token, 1, "active", "2025-01-01T00:00:00.000Z"))
            conn.commit()
        return src.read_bytes()
    finally:
        src.unlink(missing_ok=True)


def _resign_manifest(archive: zipfile.ZipFile, payloads: dict[str, bytes],
                     dropped: set[str]) -> bytes:
    """按改写后实际字节重签 manifest members（sha256/size），其余字段原样。"""
    import hashlib
    manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
    members = []
    for item in manifest["members"]:
        path = item["path"]
        if path in dropped:
            continue
        if path in payloads:
            data = payloads[path]
            item = {**item, "sha256": hashlib.sha256(data).hexdigest(),
                    "size": len(data)}
        members.append(item)
    manifest["members"] = members
    return json.dumps(manifest, ensure_ascii=False,
                      sort_keys=True).encode("utf-8")


class TeamPackageMemberPrepareTests(unittest.TestCase):
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
            user = auth_mod.create_user(name, "P3-Pkg-Fix-2026")
            cls.users[name] = {
                "user_id": user["id"], "username": user["username"],
                "token": auth_mod.create_access_token(
                    user["id"], user["username"])}
        cls.tasks_mod = __import__("coscreen.tasks", fromlist=["tasks"])
        cls.package_api = __import__(
            "custom_backend.task_package_api", fromlist=["task_package_api"])
        cls.exported = cls._build_and_export()

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
    def _db_tables(cls, path: Path) -> set:
        with closing(sqlite3.connect(str(path))) as conn:
            return {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}

    @classmethod
    def _build_and_export(cls) -> bytes:
        """真实建任务→邀请→属主 coder choice→真实 appraisal→整包导出。

        导出后属主/成员库均含团队 schema（当前产品入场即准备）；choice
        （business-role 行）随 collab、appraisal records 随属主库留存。
        """
        client = cls.client
        owner = cls.users[_OWNER]
        task_id = client.post(
            "/api/tasks", json={"name": "p3pkg-" + uuid.uuid4().hex[:8]},
            headers=cls._auth(_OWNER)).json()["task_id"]
        cls.source_task = task_id
        resp = client.post(
            f"/api/tasks/{task_id}/collaboration/invite",
            json={"username": _MEMBER, "role": "coder"},
            headers=cls._auth(_OWNER))
        assert resp.status_code == 201, resp.text
        # 二次 add 补登记（survey E4/E6：首邀登记 pending/member_library_
        # missing——成员库在协调器提交后才建；二次 add 时库已在 → active），
        # 使导出包 collab 携带 active source 行（F2 对齐用例的输入形状）。
        resp = client.post(
            f"/api/tasks/{task_id}/collaboration/invite",
            json={"username": _MEMBER, "role": "coder"},
            headers=cls._auth(_OWNER))
        assert resp.status_code in (200, 201), resp.text
        resp = client.put(
            f"/api/tasks/{task_id}/collaboration/business-role/"
            f"{owner['user_id']}",
            json={"business_role": "coder"}, headers=cls._auth(_OWNER))
        assert resp.status_code == 200, resp.text
        # 属主库真实文章 + 已决筛/复筛票（与 test_216_appraisal_consensus_
        # routes 同款直插夹具；保证导出包内 appraisal 可复跑）。
        owner_db = cls._task_dir(task_id) / (
            cls.tasks_mod.slugify_screener(owner["username"]) + ".db")
        cls.owner_slug = cls.tasks_mod.slugify_screener(owner["username"])
        cls.member_slug = cls.tasks_mod.slugify_screener(_MEMBER)
        with closing(sqlite3.connect(str(owner_db))) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(
                "INSERT INTO articles(zotero_key,title,is_duplicate_of,"
                "import_order) VALUES(?,?,NULL,0)", (_KEY, "P3 seed title"))
            for table in ("decisions", "stage2_decisions"):
                conn.execute(
                    f"INSERT INTO {table}(zotero_key,decision,"
                    "exclusion_reason,notes,tags,screened_at,updated_at)"
                    f" VALUES(?,'include','','','', ?, ?)", (_KEY, _SEED, _SEED))
            conn.commit()
        # 复筛链要求 required 筛选员全员有票：成员库同款夹具票（articles/
        # decisions/stage2_decisions 表按属主库 sqlite_master 的产品原句
        # DDL 补建——成员库无文章导入时产品本就不建）。
        member_db = cls._task_dir(task_id) / f"{cls.member_slug}.db"
        from coscreen import db as _db_mod
        _db_mod.init_db(member_db)  # 真 ledger（vote_snapshot 可读）
        with closing(sqlite3.connect(str(owner_db))) as conn:
            ddl = {row[0]: row[1] for row in conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table'"
                " AND name IN ('articles','decisions','stage2_decisions')")}
        with closing(sqlite3.connect(str(member_db))) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            existing = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ("articles", "decisions", "stage2_decisions"):
                if table not in existing:
                    conn.execute(ddl[table])
            conn.execute(
                "INSERT INTO articles(zotero_key,title,is_duplicate_of,"
                "import_order) VALUES(?,?,NULL,0)", (_KEY, "P3 seed title"))
            for table in ("decisions", "stage2_decisions"):
                conn.execute(
                    f"INSERT INTO {table}(zotero_key,decision,"
                    "exclusion_reason,notes,tags,screened_at,updated_at)"
                    f" VALUES(?,'include','','','', ?, ?)", (_KEY, _SEED, _SEED))
            conn.commit()
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

    # -- 包改写辅助 ------------------------------------------------------
    @classmethod
    def _old_shape_package(cls, *, swap_member_fresh_identity: bool = False,
                           drop_collab: bool = False) -> bytes:
        """导出包 → 旧部署包形状：成员库剥团队四表 + manifest 重签。"""
        source = cls.package_api
        buf = io.BytesIO(cls.exported)
        payloads: dict[str, bytes] = {}
        dropped: set[str] = set()
        with zipfile.ZipFile(buf) as archive:
            # 失配形状需先读 protocol 行 instance（注入失配行的挂靠键）。
            stale_instance: str | None = None
            if swap_member_fresh_identity:
                collab_name = next(
                    n for n in archive.namelist()
                    if n.rsplit("/", 1)[-1] == "collaboration.db")
                tmp = Path("_collab_peek.db")
                tmp.write_bytes(archive.read(collab_name))
                try:
                    with closing(sqlite3.connect(str(tmp))) as mem:
                        stale_instance = mem.execute(
                            "SELECT task_instance_id FROM team_task_protocol"
                            " LIMIT 1").fetchone()[0]
                finally:
                    tmp.unlink(missing_ok=True)
            for name in archive.namelist():
                stem = name.rsplit("/", 1)[-1]
                if stem.endswith(".db") and stem != "collaboration.db":
                    payloads[name] = _strip_team_tables(
                        archive.read(name), _MEMBER_TEAM_TABLES)
                if swap_member_fresh_identity and cls.member_slug in stem:
                    # 失配形状：成员库换为全新 ledger 身份（模拟旧部署重建
                    # 后未 rebind 即打包）——identity 与旧 source 行相异。
                    fresh = Path("_fresh_member.db")
                    fresh.unlink(missing_ok=True)
                    from coscreen import db as db_mod
                    db_mod.init_db(fresh)
                    payloads[name] = fresh.read_bytes()
                    fresh.unlink(missing_ok=True)
                if swap_member_fresh_identity and stem == "collaboration.db":
                    # 失配行 token 与运行时同口径（make_source_identity），
                    # 相对路径基名=成员库文件名（对齐认领的连接键）。
                    from custom_backend.team_source_lifecycle import (
                        make_source_identity)
                    src_dir = cls._task_dir(cls.source_task)
                    token = make_source_identity(
                        task_dir=src_dir,
                        source_path=src_dir / f"{cls.member_slug}.db",
                        source_user_id=cls.users[_MEMBER]["user_id"],
                        source_library_id="f" * 32)
                    payloads[name] = _inject_stale_source_row(
                        archive.read(name), stale_instance,
                        cls.users[_MEMBER]["user_id"], token)
                if drop_collab and stem == "collaboration.db":
                    dropped.add(name)
            manifest = _resign_manifest(archive, payloads, dropped)
            out = io.BytesIO()
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
                for name in archive.namelist():
                    if name in dropped or name == "manifest.json":
                        continue
                    target.writestr(name, payloads.get(
                        name, archive.read(name)))
                target.writestr("manifest.json", manifest)
            return out.getvalue()

    def _import(self, package: bytes):
        return self.client.post(
            "/api/tasks/import-package",
            files={"package": ("pkg.zip", package, "application/zip")},
            headers=self._auth(_OWNER))

    # ------------------------------------------------------------------
    # 1. 首用例（survey §8.1）：旧包成员库无 F1 + choice 残留 → 属主首次
    #    appraisal 不得 500
    # ------------------------------------------------------------------
    def test_first_case_old_package_choice_residue_owner_appraisal(self):
        resp = self._import(self._old_shape_package())
        self.assertEqual(resp.status_code, 200, resp.text)
        new_id = resp.json()["task_id"]
        # 导入上报 storage_prepared（属主+成员库两步准备）——首用例红点①：
        # 底版无此字段（导入只备 collab，成员库原样落盘）。
        prepared = resp.json()["team_provenance"]["storage_prepared"]
        self.assertEqual(prepared["owner_slug"], self.owner_slug)
        self.assertIn(self.member_slug, prepared["member_slugs"])
        # 红点②（survey §8.1 形状）：旧包成员库无 F1 → 导入后 schema 齐
        # （底版 = 包内原样落盘，四表缺失）。
        new_dir = self._task_dir(new_id)
        for slug in (self.owner_slug, self.member_slug):
            tables = self._db_tables(new_dir / f"{slug}.db")
            for table in _MEMBER_TEAM_TABLES + ("appraisal_records",):
                self.assertIn(table, tables, f"{slug}.db 缺 {table}")
        # choice 残留实证：协作库 business-role 行未被清（E7 定性）。
        with closing(sqlite3.connect(str(new_dir / "collaboration.db"))) as c:
            rows = c.execute(
                "SELECT COUNT(*) FROM collaboration_business_role").fetchone()
        self.assertGreaterEqual(rows[0], 1, "前置失真：choice 应随包残留")
        # 属主首次 appraisal（choice 命中同 uid → 角色门可过）：**200**。
        # R8（用户裁定 a，2026-11-21）起首用例升级：修前此处是 typed 503
        # completion_source_unresolved（协议行 task_id 仍为旧部署本地绑定，
        # 守卫触发器 team_task_protocol_guard_update 禁止改绑）；candidate 在
        # 导入组装窗口内受控重绑协议 → 该墙消失，属主团队写直接 200。
        resp = self.client.put(
            f"/api/tasks/{new_id}/appraisal/{_KEY}",
            json={"tool": _TOOL, "responses": {"1": "Yes"}},
            headers=self._auth(_OWNER))
        self.assertEqual(
            resp.status_code, 200,
            f"E7 首用例：属主首次 appraisal 必须 200（R8 重绑后）：{resp.text}")

    # ------------------------------------------------------------------
    # 2. F2 失配 → 重登记（成员库换新 ledger 身份）
    # ------------------------------------------------------------------
    def test_source_identity_mismatch_retires_and_reregisters(self):
        from coscreen.team_vote_ledger import library_identity_header_on
        resp = self._import(self._old_shape_package(
            swap_member_fresh_identity=True))
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        new_id = body["task_id"]
        alignment = body["team_provenance"]["storage_prepared"][
            "source_alignment"]
        self.assertGreaterEqual(alignment["active_rows"], 1)
        self.assertGreaterEqual(
            alignment["re-registered"], 1,
            f"失配未重登记：{alignment}")
        new_dir = self._task_dir(new_id)
        with closing(sqlite3.connect(
                str(new_dir / f"{self.member_slug}.db"))) as ro:
            ro.row_factory = sqlite3.Row  # C02 借用合同
            ro.execute("BEGIN")
            fresh_lib = library_identity_header_on(ro).library_id
        with closing(sqlite3.connect(str(new_dir / "collaboration.db"))) as c:
            c.row_factory = sqlite3.Row
            active = c.execute(
                "SELECT source_library_id, status FROM team_source_registry"
                " WHERE status='active'").fetchall()
        libs = {r["source_library_id"] for r in active}
        self.assertIn(
            fresh_lib, libs,
            "重登记行未指向成员库真实 ledger 身份")
        with closing(sqlite3.connect(str(new_dir / "collaboration.db"))) as c:
            retired = c.execute(
                "SELECT COUNT(*) FROM team_source_registry"
                " WHERE status='retired' AND retired_reason="
                "'imported_source_identity_mismatch'").fetchone()
        self.assertGreaterEqual(
            retired[0], 1, "失配旧行未按 imported_source_identity_mismatch 失效")

    # ------------------------------------------------------------------
    # 3. 正常包导入回归（未改包；准备幂等复备）
    # ------------------------------------------------------------------
    def test_normal_package_import_regression(self):
        resp = self._import(self.exported)
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        prepared = body["team_provenance"]["storage_prepared"]
        self.assertEqual(prepared["owner_slug"], self.owner_slug)
        self.assertIn(self.member_slug, prepared["member_slugs"])
        new_dir = self._task_dir(body["task_id"])
        tables = self._db_tables(new_dir / f"{self.owner_slug}.db")
        for table in _MEMBER_TEAM_TABLES + ("appraisal_records",):
            self.assertIn(table, tables)

    # ------------------------------------------------------------------
    # 4. fresh 导入回归（无协作库）：trusted_new_task 且属主库仍备齐
    # ------------------------------------------------------------------
    def test_fresh_import_without_collab_still_prepares(self):
        """fresh 导入（无协作库）：trusted_new_task，无团队语境 → 准备如实
        skip（legacy 包成员库可为任意形状，按包原样落盘是既有契约）。"""
        resp = self._import(self._old_shape_package(drop_collab=True))
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["team_provenance"]["registered"])
        prepared = body["team_provenance"]["storage_prepared"]
        self.assertEqual(
            prepared.get("skipped"), "fresh_import_no_team_history",
            f"fresh 导入不应做团队准备：{prepared}")
        self.assertEqual(prepared["owner_slug"], self.owner_slug)

    # ------------------------------------------------------------------
    # 5. 权限沿用族：未认证导入 401
    # ------------------------------------------------------------------
    def test_import_requires_authentication(self):
        resp = self.client.post(
            "/api/tasks/import-package",
            files={"package": ("pkg.zip", self.exported, "application/zip")})
        self.assertEqual(resp.status_code, 401, resp.text)


if __name__ == "__main__":
    unittest.main()
