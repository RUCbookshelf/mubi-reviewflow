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
  POST 200（修前键不存在 → 400 红）；对照：旧 313 键本身仍被 Pydantic
  契约拒绝（422，两态恒然——死锁形态演示）。
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
        # 迁移后的合规键可决策：修前库内无此键 → 400（红）；
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


if __name__ == "__main__":
    unittest.main()
