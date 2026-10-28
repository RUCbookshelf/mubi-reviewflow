"""r7-MM6 修复泳道回归钉：MM6-03 存量超长消歧键的一次性幂等迁移。

外部复审（minimax_findings_verification_6.md MM6-03，deepseek 复现 confirmed）：
G-02 截断只约束**新生成**键，库内既有的 301~313 字符旧消歧键原样留存——
文章列表可见，但 Web 端 ``DecisionIn.zotero_key``（max_length=300）与
``_KEY_RE`` 对其 422/400，初筛决策/全文/编码全部不可达（死锁行）；
真实 ASGI 探针（仅覆盖鉴权依赖）实测 313 键 422、300 键 404（进入业务层）。

修法（复审建议、任务书 R1 明确允许）：一次性幂等迁移，把超长键重命名为
现行公式的合规键（截断+哈希后缀形态），并在同一事务内同步更新全部
引用 zotero_key 的表；对无超长键的正常库零改动。

钉子（破坏即红）：
- 迁移单元：预置真实旧库（300 基键对照行 + 313 旧消歧键行 + decisions/
  highlights/coding_values/review_reports/title_duplicate_members/keeper_key/
  is_duplicate_of 全类引用）→ 迁移后键 ≤300 且为「截断+哈希后缀」形态、
  全部引用同步、行数零损、PRAGMA foreign_key_check 干净（修前无迁移、
  313 键原样留存 → 红）；幂等：第二次运行 0 改动；
- 逐字节守护（恒绿）：无超长键的正常库经迁移后主库文件 sha256 与全表
  逻辑 dump 逐字节一致——迁移对正常库零写入；
- 端到端：预置 313 旧键的真实任务库，Web 决策端点对**迁移后合规键**
  POST 200（修前键不在库、write_guard 先判 article_not_eligible → 404/409
  非 200，红）；对照：旧 313 键本身仍被 Pydantic 契约拒绝（422，两态恒然
  ——死锁形态演示）。

r1 评审返修补充钉（评审探针 probe_fatal/probe_migration 复现的迁移缺口）：
- members_json 同步（阻断项）：预置 313 键 + pending 标题候选组
  （title_duplicate_reviews.members_json 与 title_duplicate_members 均含
  旧键）→ 迁移后 members_json 键值同步，组仍可经真实 decide 端点决策
  200（修前 _title_review_snapshot 对旧键 fetchone=None → list(None)
  TypeError → 500，整组永久不可决策，红）；
- 既存悬挂外键容忍（中低项）：313 键 + 与重命名无关的悬挂 decisions 行
  → 迁移照常完成（修前全库 foreign_key_check 一票否决 → init_db 每请求
  500 / CLI import 抛异常，红）；悬挂行原样保留（不修不删）；
- 非外键键引用同步（低项）：workflow_source_records/workflow_import_
  metadata 的 canonical_key、workflow_import_candidate_members 的
  ref_id（仅 ref_type='active' 行是文章键，'record' 行不得误改）、
  迁移前已写入的 title_duplicate_undo 快照（before/after_json）；
- two_stage 的 unscreened 透传钉在 test_r7_mm6_prisma_caliber.py。
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# —— 服务端端到端段环境（与 test_dedup_key_len_g02.py 同一手法）——
# 必须在首次导入 custom_backend.main 之前设置（全套 pytest 单进程共享模块缓存）。
_fixture = tempfile.TemporaryDirectory(prefix="rf_mm6_migrate_")
os.environ["COBOOKSHELF_DATA_DIR"] = _fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from coscreen import db as db_mod
from coscreen.dedup import compliant_key_for_overlong
from coscreen.models import Article

KEY_MAX = 300


def _legacy_key(base: str, seed: str) -> str:
    """313 字符历史消歧键形态：base(300) + "-" + 12hex 后缀。"""
    return f"{base}-{hashlib.sha256(seed.encode()).hexdigest()[:12]}"


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _logical_dump(db_path: str) -> str:
    """全表逻辑快照（确定性排序），零损/零改动的第二证据。"""
    with closing(sqlite3.connect(db_path)) as conn:
        parts = []
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for table in tables:
            rows = conn.execute(f'SELECT * FROM "{table}"').fetchall()
            parts.append(f"{table}: {sorted(map(repr, rows))!r}")
        return "\n".join(parts)


def _checkpoint(db_path: str) -> None:
    """WAL 落盘并截断，使主库文件哈希可比。"""
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


# ---------------------------------------------------------------------------
# 迁移单元钉子：重命名 + 全引用同步 + 零损 + 幂等
# ---------------------------------------------------------------------------
class OverlongKeyMigrationTests(unittest.TestCase):
    def _seed_legacy_db(self, db: str) -> tuple[str, str]:
        """真实旧库夹具：300 基键对照行 A + 313 旧消歧键行 B（含全类引用）。"""
        key_a = "A" * 300
        legacy_b = _legacy_key("B" * 300, "mm6-unit-b")
        assert len(legacy_b) == 313
        db_mod.init_db(db)
        db_mod.upsert_articles(db, [
            Article(zotero_key=key_a, title="Control Article A", doi="10.1/a"),
            Article(zotero_key=legacy_b, title="Legacy Article B", doi="10.1/b"),
        ], [])
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(
                "INSERT INTO decisions(zotero_key,decision,exclusion_reason,notes,"
                "tags,screened_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (legacy_b, "include", "", "", "", "t", "t"))
            conn.execute(
                "INSERT INTO highlights(zotero_key,page,text,color,note,created_at) "
                "VALUES(?,?,?,?,?,?)", (legacy_b, 1, "hl", "jade", "", "t"))
            conn.execute(
                "INSERT INTO coding_dimensions(name,dtype,options_json,multi_select,"
                "unit,description,section,position,created_at) "
                "VALUES('d1','text','[]',0,'','','',1,'t')")
            dim_id = conn.execute(
                "SELECT id FROM coding_dimensions WHERE name='d1'").fetchone()[0]
            conn.execute(
                "INSERT INTO coding_values(dimension_id,zotero_key,value,updated_at) "
                "VALUES(?,?,?,?)", (dim_id, legacy_b, "v", "t"))
            conn.execute("INSERT INTO review_studies(id,label) VALUES('s1','S1')")
            conn.execute(
                "INSERT INTO review_reports(zotero_key,study_id) VALUES(?,?)",
                (legacy_b, "s1"))
            conn.execute(
                "INSERT INTO title_duplicate_reviews(review_id,normalized_title,"
                "status,members_json,keeper_key,decision_by,created_at,updated_at) "
                "VALUES('r1','t','pending','[]',?,'u','t','t')", (legacy_b,))
            conn.execute(
                "INSERT INTO title_duplicate_members(review_id,zotero_key,"
                "imported_in_batch) VALUES('r1',?,1)", (legacy_b,))
            conn.execute(
                "UPDATE articles SET is_duplicate_of=? WHERE zotero_key=?",
                (legacy_b, key_a))
        return key_a, legacy_b

    def test_migrate_renames_overlong_key_and_syncs_all_references(self):
        with tempfile.TemporaryDirectory(prefix="rf_mm6_mig_") as tmp:
            db = str(Path(tmp) / "legacy.db")
            key_a, legacy_b = self._seed_legacy_db(db)
            renamed = db_mod.migrate_overlong_keys(db)
            self.assertEqual(renamed, 1)  # 修前无该迁移（313 键原样留存）→ 红
            expected_new = "B" * (KEY_MAX - 13) + "-" + legacy_b[-12:]
            self.assertEqual(compliant_key_for_overlong(legacy_b), expected_new)
            self.assertLessEqual(len(expected_new), KEY_MAX)
            with closing(sqlite3.connect(db)) as conn:
                conn.execute("PRAGMA foreign_keys=ON")
                keys = {r[0] for r in conn.execute("SELECT zotero_key FROM articles")}
                self.assertEqual(keys, {key_a, expected_new},
                                 "300 对照键必须逐字节不变，313 键必须重命名")
                # 全部引用列同步：旧键在任何表中都不再出现，新键处处在场
                references = [
                    ("decisions", "zotero_key"),
                    ("highlights", "zotero_key"),
                    ("coding_values", "zotero_key"),
                    ("review_reports", "zotero_key"),
                    ("title_duplicate_members", "zotero_key"),
                    ("title_duplicate_reviews", "keeper_key"),
                    ("articles", "is_duplicate_of"),
                ]
                for table, col in references:
                    vals = [r[0] for r in conn.execute(
                        f'SELECT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL')]
                    self.assertIn(expected_new, vals, f"{table}.{col} 未同步到新键")
                    self.assertNotIn(legacy_b, vals, f"{table}.{col} 仍持旧键")
                # 零损：各表行数不变（articles 两行，其余引用表各一行）
                for table, expected in [("articles", 2)] + [
                    (t, 1) for t in ("decisions", "highlights", "coding_values",
                                     "review_reports", "title_duplicate_members",
                                     "title_duplicate_reviews")
                ]:
                    self.assertEqual(
                        conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0],
                        expected, f"{table} 行数变化（零损被破坏）")
                # 外键完整性（FK OFF 迁移后的强制自检口径）
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            # 幂等：第二次运行零改动（键集逐字节稳定）
            self.assertEqual(db_mod.migrate_overlong_keys(db), 0)
            with closing(sqlite3.connect(db)) as conn:
                keys = {r[0] for r in conn.execute("SELECT zotero_key FROM articles")}
                self.assertEqual(keys, {key_a, expected_new})

    def test_migration_survives_repeated_calls_and_target_collision(self):
        # 目标键已被占用的形态（MM6-04 双行场景）：313 旧键 + 既有合规 300 键
        # 并存 → 迁移按 serial 重试取自由键，绝不覆盖既有行（零损）。
        with tempfile.TemporaryDirectory(prefix="rf_mm6_col_") as tmp:
            db = str(Path(tmp) / "collide.db")
            base = "B" * 300
            suffix = hashlib.sha256(b"mm6-unit-b").hexdigest()[:12]
            legacy_b = f"{base}-{suffix}"
            compliant = base[:KEY_MAX - 13] + "-" + suffix
            db_mod.init_db(db)
            db_mod.upsert_articles(db, [
                Article(zotero_key=compliant, title="Compliant Row", doi="10.1/x"),
                Article(zotero_key=legacy_b, title="Legacy Row", doi="10.1/b"),
            ], [])
            renamed = db_mod.migrate_overlong_keys(db)
            self.assertEqual(renamed, 1)
            with closing(sqlite3.connect(db)) as conn:
                keys = sorted(r[0] for r in conn.execute("SELECT zotero_key FROM articles"))
                self.assertEqual(len(keys), 2)  # 不覆盖、不合并、不丢行
                self.assertIn(compliant, keys)
                migrated = next(k for k in keys if k != compliant)
                self.assertLessEqual(len(migrated), KEY_MAX)
                self.assertNotEqual(migrated, compliant)
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_normal_db_byte_identical_after_migration(self):
        # 恒绿守护：无超长键的正常库经迁移后逐字节零变化（主库文件哈希 +
        # 全表逻辑 dump 双证据）——迁移对正常库零写入。
        with tempfile.TemporaryDirectory(prefix="rf_mm6_zero_") as tmp:
            db = str(Path(tmp) / "normal.db")
            db_mod.init_db(db)
            db_mod.upsert_articles(db, [
                Article(zotero_key="K1", title="Normal A", doi="10.1/a"),
                Article(zotero_key="K2", title="Normal B", doi="10.1/b"),
            ], [])
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.execute(
                    "INSERT INTO decisions(zotero_key,decision,exclusion_reason,"
                    "notes,tags,screened_at,updated_at) "
                    "VALUES('K1','include','','','','t','t')")
            # 预热进程内 schema 快路径（_connect 自愈序列只跑一次）并落盘
            db_mod.list_articles(db)
            _checkpoint(db)
            before_hash, before_dump = _file_hash(Path(db)), _logical_dump(db)
            self.assertEqual(db_mod.migrate_overlong_keys(db), 0)
            _checkpoint(db)
            self.assertEqual(_file_hash(Path(db)), before_hash,
                             "迁移对无超长键的正常库写入了字节")
            self.assertEqual(_logical_dump(db), before_dump)


# ---------------------------------------------------------------------------
# 合规键公式单元钉子：截断+哈希后缀形态（现行公式）
# ---------------------------------------------------------------------------
class CompliantKeyFormulaTests(unittest.TestCase):
    def test_suffix_form_overlong_key_keeps_suffix_truncates_base(self):
        base, suffix = "B" * 300, "ab12cd34ef56"
        got = compliant_key_for_overlong(f"{base}-{suffix}")
        self.assertEqual(got, base[:KEY_MAX - 13] + "-" + suffix)
        self.assertLessEqual(len(got), KEY_MAX)

    def test_serial_form_and_raw_overlong_key_bounded(self):
        suffix = "ab12cd34ef56"
        serial_form = compliant_key_for_overlong("B" * 300 + f"-{suffix}-2")
        self.assertLessEqual(len(serial_form), KEY_MAX)
        self.assertTrue(serial_form.endswith(f"-{suffix}-2"))
        raw = compliant_key_for_overlong("Z" * 313)  # 非消歧形态的超长键
        self.assertLessEqual(len(raw), KEY_MAX)
        self.assertRegex(raw, r"-[0-9a-f]{12}$")


# ---------------------------------------------------------------------------
# 端到端钉子：预置 313 旧键的真实任务库，迁移后 Web 决策 200
# ---------------------------------------------------------------------------
class WebDecisionAfterMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from custom_backend import main as backend_main
        from custom_backend import auth as auth_mod

        cls.backend_main = backend_main
        cls.client = TestClient(backend_main.app)
        cls.client.__enter__()
        user = auth_mod.create_user("mm6migrate@example.test", "MM6-Migrate-2026")
        cls.user = {"user_id": user["id"], "username": user["username"],
                    "token": auth_mod.create_access_token(user["id"], user["username"])}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        _fixture.cleanup()

    def auth(self):
        return {"Authorization": "Bearer " + self.user["token"]}

    def setUp(self):
        from custom_backend.main import TaskLockMiddleware
        from coscreen import tasks as tasks_mod

        TaskLockMiddleware._held.clear()
        self.task = self.client.post(
            "/api/tasks", json={"name": self._testMethodName},
            headers=self.auth()).json()["task_id"]
        self.info = tasks_mod.find_task(self.task, self.backend_main.DATA_DIR)
        self.assertIsNotNone(self.info)
        slug = tasks_mod.slugify_screener(self.user["username"])
        self.db = str(self.info.dir / f"{slug}.db")

    def tearDown(self):
        from custom_backend.main import TaskLockMiddleware
        from coscreen import tasks as tasks_mod

        TaskLockMiddleware._held.clear()
        TaskLockMiddleware._deleting.clear()
        TaskLockMiddleware._list_readers = 0
        for path in list(tasks_mod._HELD_LOCKS):
            tasks_mod.release_task_lock(path, force=True)

    def test_legacy_313_key_decidable_after_migration(self):
        # 修前死锁形态（MM6-03 复现）：313 键预置入库，决策端点 422
        base = "C" * 300
        suffix = hashlib.sha256(b"mm6-web").hexdigest()[:12]
        legacy = f"{base}-{suffix}"
        self.assertEqual(len(legacy), 313)
        # 预置「修复前已导入」形态（init_db 只建表——articles 基表不在
        # _connect 自愈序列内；迁移在 init_db 尾部执行，此刻库为空 = no-op）
        db_mod.init_db(self.db)
        db_mod.upsert_articles(self.db, [
            Article(zotero_key=legacy, title="Legacy deadlocked article",
                    doi="10.1/c")], [])
        r_old = self.client.post(
            f"/api/tasks/{self.task}/decisions",
            json={"zotero_key": legacy, "decision": "include"},
            headers=self.auth())
        self.assertEqual(r_old.status_code, 422,
                         "超长键本身仍应被契约层拒绝（两态恒然，死锁形态演示）")
        # 迁移后的合规键可决策：修前库内无此键，write_guard 先于 DB 层判
        # article_not_eligible → 409（评审 r1 实测口径；红）；
        # 修后 _screener_db 的 init_db 触发幂等迁移 → 键在场 → 200
        compliant = base[:KEY_MAX - 13] + "-" + suffix
        r_new = self.client.post(
            f"/api/tasks/{self.task}/decisions",
            json={"zotero_key": compliant, "decision": "include"},
            headers=self.auth())
        self.assertEqual(r_new.status_code, 200, r_new.text)
        self.assertTrue(r_new.json().get("saved"))
        arts = db_mod.list_articles(self.db, include_duplicates=True)
        self.assertEqual([a.zotero_key for a in arts], [compliant])
        self.assertLessEqual(len(compliant), KEY_MAX)

    def test_title_duplicate_group_decidable_after_migration(self):
        # 评审 r1 探针 probe_fatal 场景端到端回放：预置 313 键 + pending
        # 标题候选组（members_json 与成员表均含旧键）→ 迁移后
        # GET 候选组列表 200；POST not_duplicate 与 duplicate（keeper=新键）
        # 均 200（修前 _title_review_snapshot 对 members_json 滞留旧键
        # fetchone=None → list(None) TypeError → 500，整组永久不可决策，红）。
        import json as _json

        base = "D" * 300
        suffix = hashlib.sha256(b"mm6-r1-web").hexdigest()[:12]
        legacy = f"{base}-{suffix}"
        compliant = base[:KEY_MAX - 13] + "-" + suffix
        key_a = "DA" * 150  # 300 字符对照成员键
        db_mod.init_db(self.db)
        db_mod.upsert_articles(self.db, [
            Article(zotero_key=key_a, title="R1 Shared Title", doi="10.1/a"),
            Article(zotero_key=legacy, title="R1 Shared Title", doi="10.1/b"),
        ], [])
        members = [{"zotero_key": key_a, "imported_in_batch": 1},
                   {"zotero_key": legacy, "imported_in_batch": 1}]
        with closing(sqlite3.connect(self.db)) as conn, conn:
            for review_id in ("rr1", "rr2"):
                conn.execute(
                    "INSERT INTO title_duplicate_reviews(review_id,normalized_title,"
                    "status,members_json,keeper_key,decision_by,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (review_id, f"r1-{review_id}", "pending",
                     _json.dumps(members, ensure_ascii=False), "", "", "t", "t"))
                conn.executemany(
                    "INSERT INTO title_duplicate_members(review_id,zotero_key,"
                    "imported_in_batch) VALUES(?,?,1)",
                    [(review_id, key_a), (review_id, legacy)])
        r_list = self.client.get(
            f"/api/tasks/{self.task}/title-duplicate-reviews?view=all",
            headers=self.auth())
        self.assertEqual(r_list.status_code, 200, r_list.text)
        r_not_dup = self.client.post(
            f"/api/tasks/{self.task}/title-duplicate-reviews/rr1",
            json={"decision": "not_duplicate"}, headers=self.auth())
        self.assertEqual(r_not_dup.status_code, 200, r_not_dup.text)
        r_dup = self.client.post(
            f"/api/tasks/{self.task}/title-duplicate-reviews/rr2",
            json={"decision": "duplicate", "keeper_key": compliant},
            headers=self.auth())
        self.assertEqual(r_dup.status_code, 200, r_dup.text)
        # 决策后文献解除 article_not_eligible 卡死（阻断项后果链闭环）：
        # B（新键）在 not_duplicate 组里不构成排除；对 compliant 键的
        # 筛选决策可写入。
        r_decide = self.client.post(
            f"/api/tasks/{self.task}/decisions",
            json={"zotero_key": compliant, "decision": "include"},
            headers=self.auth())
        self.assertEqual(r_decide.status_code, 200, r_decide.text)


# ---------------------------------------------------------------------------
# r1 评审返修：members_json 同步（阻断项）——标题候选组迁移后必须仍可决策
# ---------------------------------------------------------------------------
class MembersJsonSyncTests(unittest.TestCase):
    def _seed_group_db(self, db: str) -> tuple[str, str, str]:
        """评审 probe_fatal 夹具：313 键 + pending 标题候选组。

        members_json（JSON 列，与 title_duplicate_members 冗余存键）与
        成员表均含旧键——迁移若只同步列名白名单，members_json 滞留旧键。
        """
        import json as _json

        key_a = "A" * 300
        legacy_b = _legacy_key("B" * 300, "mm6-r1-b")
        db_mod.init_db(db)
        db_mod.upsert_articles(db, [
            Article(zotero_key=key_a, title="Shared Title", doi="10.1/a"),
            Article(zotero_key=legacy_b, title="Shared Title", doi="10.1/b"),
        ], [])
        members = [
            {"zotero_key": key_a, "imported_in_batch": 1},
            {"zotero_key": legacy_b, "imported_in_batch": 1},
        ]
        with closing(sqlite3.connect(db)) as conn, conn:
            for review_id in ("r1", "r2"):
                conn.execute(
                    "INSERT INTO title_duplicate_reviews(review_id,normalized_title,"
                    "status,members_json,keeper_key,decision_by,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (review_id, f"title-{review_id}", "pending",
                     _json.dumps(members, ensure_ascii=False), "", "", "t", "t"))
                conn.executemany(
                    "INSERT INTO title_duplicate_members(review_id,zotero_key,"
                    "imported_in_batch) VALUES(?,?,1)",
                    [(review_id, key_a), (review_id, legacy_b)])
        return key_a, legacy_b, "B" * (KEY_MAX - 13) + "-" + legacy_b[-12:]

    def test_migration_syncs_members_json_and_group_stays_decidable(self):
        import json as _json

        with tempfile.TemporaryDirectory(prefix="rf_mm6_r1a_") as tmp:
            db = str(Path(tmp) / "legacy.db")
            key_a, legacy_b, new_b = self._seed_group_db(db)
            self.assertEqual(db_mod.migrate_overlong_keys(db), 1)
            # members_json 键值同步（修前滞留 313 旧键 → 红）
            with closing(sqlite3.connect(db)) as conn:
                for review_id in ("r1", "r2"):
                    raw = conn.execute(
                        "SELECT members_json FROM title_duplicate_reviews "
                        "WHERE review_id=?", (review_id,)).fetchone()[0]
                    keys = {m["zotero_key"] for m in _json.loads(raw)}
                    self.assertEqual(keys, {key_a, new_b},
                                     f"{review_id} members_json 未同步: {keys}")
            # 组仍可决策（修前 _title_review_snapshot 对旧键 fetchone=None
            # → list(None) TypeError，decide 抛异常 → 红）
            self.assertTrue(db_mod.decide_title_duplicate_review(
                db, "r1", "not_duplicate", decided_by="u"))
            self.assertTrue(db_mod.decide_title_duplicate_review(
                db, "r2", "duplicate", keeper_key=new_b, decided_by="u"))
            # 决策写入的撤销快照可安全回放（键域一致，无「已有后续变更」）
            self.assertTrue(db_mod.undo_title_duplicate_review(db, "u"))

    def test_preexisting_undo_snapshot_restorable_after_migration(self):
        # 迁移前已写入的撤销快照（before/after_json 含旧键）经迁移同步后
        # 仍可撤销：修前快照滞留旧键 vs 库内新键 → snapshot != after →
        # 「相关记录已有后续变更」ValueError → 红。
        with tempfile.TemporaryDirectory(prefix="rf_mm6_r1b_") as tmp:
            db = str(Path(tmp) / "undo.db")
            key_a, legacy_b, _new_b = self._seed_group_db(db)
            # 迁移前先做一次决策（写入含 313 旧键的撤销快照）
            self.assertTrue(db_mod.decide_title_duplicate_review(
                db, "r1", "duplicate", keeper_key=key_a, decided_by="u"))
            self.assertEqual(db_mod.migrate_overlong_keys(db), 1)
            self.assertTrue(db_mod.undo_title_duplicate_review(db, "u"))
            with closing(sqlite3.connect(db)) as conn:
                status = conn.execute(
                    "SELECT status FROM title_duplicate_reviews "
                    "WHERE review_id='r1'").fetchone()[0]
                self.assertEqual(status, "pending", "撤销未恢复到决策前状态")
                dup = conn.execute(
                    "SELECT is_duplicate_of FROM articles WHERE zotero_key=?",
                    ("B" * (KEY_MAX - 13) + "-" + legacy_b[-12:],)).fetchone()[0]
                self.assertIsNone(dup, "撤销未清除 is_duplicate_of 标记")


# ---------------------------------------------------------------------------
# r1 评审返修：既存悬挂外键不得阻断迁移（中低项）
# ---------------------------------------------------------------------------
class PreexistingDanglingFkToleranceTests(unittest.TestCase):
    def test_unrelated_dangling_fk_does_not_block_migration(self):
        # 评审 probe_fatal 场景 1：313 键 + 一条悬挂 decisions 行 'GHOST'
        # （引用不存在的文章键，与本次重命名无关的既存脏数据）→
        # 迁移照常完成（修前全库 foreign_key_check 一票否决 raise →
        # init_db 尾部每请求重跑 → Web 全接口 500 / CLI import 抛异常，红）。
        with tempfile.TemporaryDirectory(prefix="rf_mm6_r1c_") as tmp:
            db = str(Path(tmp) / "dangling.db")
            legacy = _legacy_key("B" * 300, "mm6-r1-ghost")
            db_mod.init_db(db)
            db_mod.upsert_articles(db, [
                Article(zotero_key=legacy, title="Legacy Article", doi="10.1/b")], [])
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.execute(  # 悬挂行：raw sqlite3 默认 FK off 可写入
                    "INSERT INTO decisions(zotero_key,decision,exclusion_reason,"
                    "notes,tags,screened_at,updated_at) "
                    "VALUES('GHOST','include','','','','t','t')")
            self.assertEqual(db_mod.migrate_overlong_keys(db), 1)  # 修前抛 ValueError
            with closing(sqlite3.connect(db)) as conn:
                keys = [r[0] for r in conn.execute("SELECT zotero_key FROM articles")]
                self.assertTrue(all(len(k) <= KEY_MAX for k in keys), keys)
                # 悬挂行原样保留（既存脏数据不修不删，与 758da65 行为一致）
                ghost = conn.execute(
                    "SELECT zotero_key,decision FROM decisions "
                    "WHERE zotero_key='GHOST'").fetchone()
                self.assertEqual(ghost, ("GHOST", "include"))
            # init_db（Web 每请求入口）在该库上不再抛
            db_mod.init_db(db)
            # 幂等
            self.assertEqual(db_mod.migrate_overlong_keys(db), 0)


# ---------------------------------------------------------------------------
# r1 评审返修：非外键键引用同步（低项）——canonical_key / ref_id(active) /
# keeper_key（既有白名单）全覆盖，'record' ref_id 不得误改
# ---------------------------------------------------------------------------
class NonForeignKeyReferenceSyncTests(unittest.TestCase):
    def test_canonical_key_and_active_ref_id_synced(self):
        from coscreen import review_imports as _rim
        from coscreen.review_imports import canonical as _canonical

        with tempfile.TemporaryDirectory(prefix="rf_mm6_r1d_") as tmp:
            db = str(Path(tmp) / "workflow.db")
            db_mod.init_db(db)
            _rim.migrate(db)
            legacy = _legacy_key("B" * 300, "mm6-r1-wf")
            new_key = "B" * (KEY_MAX - 13) + "-" + legacy[-12:]
            db_mod.upsert_articles(db, [
                Article(zotero_key=legacy, title="Workflow Article", doi="10.1/b")], [])
            with closing(sqlite3.connect(db)) as conn, conn:
                # workflow_source_records / workflow_import_metadata 的
                # canonical_key（非外键、语义为文章键）
                conn.execute(
                    "INSERT INTO workflow_source_records(id,source_id,digest,row_index,"
                    "filename,original_json,article_json,incoming_key,staged_key,"
                    "canonical_key,outcome,state,created_at) "
                    "VALUES('rec1','s1','d1',0,'f.csv','{}','{}',?,?,?,'accepted',"
                    "'active','t')", (legacy, legacy, legacy))
                conn.execute(
                    "INSERT INTO workflow_import_metadata(record_id,canonical_key,"
                    "before_json,incoming_json) VALUES('rec1',?,'{}','{}')", (legacy,))
                # 候选组成员：ref_type='active' 的 ref_id 是文章键；
                # 'record' 的 ref_id 是 record id，绝不能被迁移误改
                conn.execute(
                    "INSERT INTO workflow_import_candidates(id,signature,kind,status,"
                    "keeper_key,decided_by,created_at,updated_at) "
                    "VALUES('c1','sig1','exact_key','pending',?,'','t','t')", (legacy,))
                conn.executemany(
                    "INSERT INTO workflow_import_candidate_members(candidate_id,"
                    "ref_type,ref_id) VALUES('c1',?,?)",
                    [("active", legacy), ("record", "rec1")])
            self.assertEqual(db_mod.migrate_overlong_keys(db), 1)
            with closing(sqlite3.connect(db)) as conn:
                rec = conn.execute(
                    "SELECT canonical_key FROM workflow_source_records "
                    "WHERE id='rec1'").fetchone()[0]
                meta = conn.execute(
                    "SELECT canonical_key FROM workflow_import_metadata "
                    "WHERE record_id='rec1'").fetchone()[0]
                keeper = conn.execute(
                    "SELECT keeper_key FROM workflow_import_candidates "
                    "WHERE id='c1'").fetchone()[0]
                refs = dict(conn.execute(
                    "SELECT ref_type,ref_id FROM workflow_import_candidate_members "
                    "WHERE candidate_id='c1'").fetchall())
                self.assertEqual(rec, new_key, "workflow_source_records.canonical_key 未同步")
                self.assertEqual(meta, new_key, "workflow_import_metadata.canonical_key 未同步")
                self.assertEqual(keeper, new_key, "workflow_import_candidates.keeper_key 未同步")
                self.assertEqual(refs, {"active": new_key, "record": "rec1"},
                                 f"ref_id 同步越界或缺失: {refs}")
                # 引用链闭合：canonical() 不再抛 'no longer exists'
                self.assertEqual(_canonical(conn, new_key), new_key)


if __name__ == "__main__":
    unittest.main()
