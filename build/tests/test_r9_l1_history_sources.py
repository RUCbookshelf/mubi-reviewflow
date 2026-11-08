"""r9-L1 钉 —— 导入历史库 / 来源迁移 / CLI 契约（F4-71·72·73·74·75、F4-83·84·85·86·87·88、F2-74·77·78·79）。

每条钉子都按权威清单（out/release_plan/inventory_夜扫286确认分组.md §3.1）
原文的探针形态复现，并附「破坏即红」断言与合法输入对照组（R2）：

- F4-71  历史库连接层 busy_timeout 原为默认 5s、journal=delete：另一进程持
         写锁 7s 时 ``save_import`` 5.48s 后抛 OperationalError。修后切 WAL +
         15s 忙等（持锁 4.5s 已足够覆盖原失败窗），且失败时给中文 ValueError。
- F4-72  历史库只增不删：模块无删除 API，失败导入永久保留原件 BLOB。修后
         ``delete_import`` + 作用域保留策略 + 失败导入清理原件。
- F4-73  ``update_import_stats`` 按裸 import_id 改写（跨 screener 可写）、未知
         id 静默 no-op。修后支持作用域过滤，0 行匹配抛 ValueError。
- F4-74  ``stats_json`` 逐行 json.loads 无防护：一行坏数据令整端点 500。修后
         逐行降级为空摘要、健康行与分页完好。
- F4-75  ``limit=0`` 游标不前进（无限空转）、``limit=-1`` next 为负。修后
         模块层拒绝非法分页参数。
- F4-83  迁移版本检查在写事务外：双进程并发首升级，一方 IntegrityError。修后
         事务内复检 → 两方均无异常、版本各落一次。
- F4-84  迁移体对行级脏数据零防护：坏 original_json / 孤立代理 filename 令
         迁移每次失败、版本 4/5 永不落地。修后坏行降级，迁移照常完成。
- F4-85  升级前备份「存在即跳过」不校验：176B 伪 .bak 被永久信任。修后校验
         可打开 + quick_check，坏备份被可用备份替换。
- F4-86  ``prepare_files`` 无条件覆写 API 已校验的 source_id。修后显式 id 保留，
         ``auto``/空 仍按文件名派生（现行前端行为不变）。
- F4-87  ``first()`` 200 字符截断 vs ``detect()`` 完整名：同一文件两个来源身份。
         修后 ``first`` 不截断，迁移路径与上传路径同一 sid。
- F4-88  ``ensure()`` named 回退可命中 UNASSIGNED 保留行并改写其显示名。修后
         保留行原样、不挂 detail。
- F2-74  merge 校验 ValueError 逃出 try：traceback + exit 1。修后中文 + exit 2，
         合法对照 exit 0。
- F2-77  export/merge 写路径无异常处理、cmd_export 先写盘后计数。修后统一
         翻译成 exit 2；先计数后写盘。
- F2-78  stats/import/export/restore/simulate 对坏路径/非 coscreen 库无兜底。
         修后预检 + 中文 + exit 2。
- F2-79  黄金 CSV 重复键静默 last-wins。修后冲突标签显式拒绝（exit 2），同值
         重复行仍接受。

对照组逐位不变：正常导入/导出/合并/历史列表/迁移的返回与行态在修前修后一致。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen import db as db_mod
from coscreen import import_history
from coscreen import import_sources
from coscreen import review_imports
from coscreen import review_workflow as workflow
from coscreen.models import Article

ROOT = Path(__file__).resolve().parents[2]


def _run_cli(args: list[str]):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, "-B", "-m", "coscreen", *[str(a) for a in args]],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def _decisions_csv(path: Path, rows: list[tuple[str, str]]) -> Path:
    path.write_text(
        "zotero_key,decision\n" + "".join(f"{k},{d}\n" for k, d in rows),
        encoding="utf-8-sig")
    return path


def _import_csv(path: Path, rows: list[tuple[str, str]]) -> Path:
    header = "Key,Title,Author,Publication Year,DOI,Abstract,Publication Title,Url\n"
    body = "".join(
        f"{key},{title},Smith J,2020,10.1/{key},A,Journal,http://x\n"
        for key, title in rows)
    path.write_text(header + body, encoding="utf-8-sig")
    return path


# ---------------------------------------------------------------------------
# F4-71 · 历史库忙等/WAL
# ---------------------------------------------------------------------------
class ImportHistoryLockTests(unittest.TestCase):
    def test_connect_uses_wal_and_extended_busy_timeout(self):
        with tempfile.TemporaryDirectory(prefix="rf_r9l1_71_") as tmp:
            db = Path(tmp) / "history.sqlite3"
            with closing(import_history._connect(db)) as conn:
                self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
                self.assertGreaterEqual(
                    conn.execute("PRAGMA busy_timeout").fetchone()[0], 15000)

    def test_save_import_survives_short_lock_and_translates_long_lock(self):
        with tempfile.TemporaryDirectory(prefix="rf_r9l1_71b_") as tmp:
            db = Path(tmp) / "history.sqlite3"
            import_history.save_import(
                db, task_id="t", screener="s", import_type="batch",
                stats={}, files=[])

            def hold(seconds: float) -> None:
                conn = sqlite3.connect(db, timeout=0)
                conn.execute("BEGIN EXCLUSIVE")
                time.sleep(seconds)
                conn.commit()
                conn.close()

            # 修前默认忙等 5s → 持锁 6.5s 时抛 OperationalError（红）
            t = threading.Thread(target=hold, args=(6.5,))
            t.start()
            time.sleep(0.3)
            try:
                import_history.save_import(
                    db, task_id="t", screener="s", import_type="batch",
                    stats={"ok": True}, files=[])
            finally:
                t.join()
            rows, _ = import_history.list_imports(db, task_id="t", screener="s")
            self.assertTrue(any(row["result"] == {"ok": True} for row in rows))


# ---------------------------------------------------------------------------
# F4-72 · 历史库保留策略 / 失败导入清理原件
# ---------------------------------------------------------------------------
class ImportHistoryRetentionTests(unittest.TestCase):
    def test_delete_api_and_retention_prune_remove_original_bytes(self):
        with tempfile.TemporaryDirectory(prefix="rf_r9l1_72_") as tmp:
            db = Path(tmp) / "history.sqlite3"
            first = import_history.save_import(
                db, task_id="t", screener="s", import_type="batch",
                stats={}, files=[("a.csv", b"payload-a")])
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM import_files").fetchone()[0], 1)
            self.assertTrue(import_history.delete_import(db, first, task_id="t", screener="s"))
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM imports").fetchone()[0], 0)
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM import_files").fetchone()[0], 0)

    def test_retention_prunes_oldest_rows_per_scope(self):
        with tempfile.TemporaryDirectory(prefix="rf_r9l1_72b_") as tmp:
            db = Path(tmp) / "history.sqlite3"
            for index in range(import_history.MAX_RETAINED_IMPORTS + 3):
                import_history.save_import(
                    db, task_id="t", screener="s", import_type="single",
                    stats={"n": index}, files=[("f.csv", b"x")])
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM imports").fetchone()[0],
                    import_history.MAX_RETAINED_IMPORTS)
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM import_files").fetchone()[0],
                    import_history.MAX_RETAINED_IMPORTS)
            # 对照组：另一作用域不受裁剪影响
            import_history.save_import(
                db, task_id="t2", screener="s", import_type="single",
                stats={}, files=[("f.csv", b"x")])
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM imports WHERE task_id='t2'").fetchone()[0], 1)

    def test_failed_originals_pruned_after_retention_window_only(self):
        """失败原件按**保留期**清理，而不是写入失败时立刻删（R2）。"""
        with tempfile.TemporaryDirectory(prefix="rf_r9l1_72c_") as tmp:
            db = Path(tmp) / "history.sqlite3"
            hid = import_history.save_import(
                db, task_id="t", screener="s", import_type="batch",
                stats={"status": "preparing"}, files=[("a.csv", b"blob")])
            import_history.update_import_stats(
                db, hid, {"status": "failed", "error": "boom"})
            rows, _ = import_history.list_imports(db, task_id="t", screener="s")
            self.assertEqual(rows[0]["result"]["status"], "failed")
            # 保留期内：原件仍在（可重试的 409 不会丢掉原始文件）
            self.assertEqual([f["name"] for f in rows[0]["files"]], ["a.csv"])
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM import_files").fetchone()[0], 1)
            # 越过保留期后再写入：原件被清理，历史行与失败原因保留
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.execute(
                    "UPDATE imports SET imported_at='2000-01-01T00:00:00.000000+00:00' "
                    "WHERE import_id=?", (hid,))
            import_history.save_import(
                db, task_id="t", screener="s", import_type="single",
                stats={}, files=[])
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM import_files").fetchone()[0], 0)
            rows, _ = import_history.list_imports(db, task_id="t", screener="s")
            failed = [row for row in rows if row["id"] == hid]
            self.assertEqual(failed[0]["result"]["status"], "failed")


# ---------------------------------------------------------------------------
# F4-73 / F4-74 / F4-75 · 统计更新作用域、坏行降级、分页守卫
# ---------------------------------------------------------------------------
class ImportHistoryScopeAndDecodeTests(unittest.TestCase):
    def test_update_stats_scoped_and_rejects_unknown_or_foreign_ids(self):
        with tempfile.TemporaryDirectory(prefix="rf_r9l1_73_") as tmp:
            db = Path(tmp) / "history.sqlite3"
            bob = import_history.save_import(
                db, task_id="t", screener="bob", import_type="single",
                stats={"n": "bob"}, files=[])
            with self.assertRaises(ValueError):
                import_history.update_import_stats(db, "does-not-exist", {"hijacked": True})
            # 跨 screener：作用域不匹配 → 拒绝（修前静默改写 bob 的行）
            with self.assertRaises(ValueError):
                import_history.update_import_stats(
                    db, bob, {"hijacked": True}, screener="alice")
            rows, _ = import_history.list_imports(db, task_id="t", screener="bob")
            self.assertEqual(rows[0]["result"], {"n": "bob"})
            # 合法对照：同作用域更新成功
            import_history.update_import_stats(db, bob, {"n": 1}, screener="bob")
            rows, _ = import_history.list_imports(db, task_id="t", screener="bob")
            self.assertEqual(rows[0]["result"], {"n": 1})

    def test_corrupt_stats_row_degrades_instead_of_failing_list(self):
        with tempfile.TemporaryDirectory(prefix="rf_r9l1_74_") as tmp:
            db = Path(tmp) / "history.sqlite3"
            good = import_history.save_import(
                db, task_id="t", screener="s", import_type="single",
                stats={"ok": 1}, files=[])
            bad = import_history.save_import(
                db, task_id="t", screener="s", import_type="single",
                stats={"ok": 2}, files=[])
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.execute(
                    "UPDATE imports SET stats_json='{not json' WHERE import_id=?", (bad,))
            rows, next_offset = import_history.list_imports(db, task_id="t", screener="s")
            self.assertEqual(len(rows), 2)
            self.assertIsNone(next_offset)
            by_id = {row["id"]: row["result"] for row in rows}
            self.assertEqual(by_id[good], {"ok": 1})
            self.assertEqual(by_id[bad], {})

    def test_invalid_pagination_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="rf_r9l1_75_") as tmp:
            db = Path(tmp) / "history.sqlite3"
            import_history.save_import(
                db, task_id="t", screener="s", import_type="single", stats={}, files=[])
            # 修前：limit=0 → rows=0 next=0（三次连调无限空转）；limit=-1 → next=-1
            with self.assertRaises(ValueError):
                import_history.list_imports(db, task_id="t", screener="s", limit=0)
            with self.assertRaises(ValueError):
                import_history.list_imports(db, task_id="t", screener="s", limit=-1)
            with self.assertRaises(ValueError):
                import_history.list_imports(db, task_id="t", screener="s", offset=-1)
            # 合法对照：分页语义不变
            rows, next_offset = import_history.list_imports(
                db, task_id="t", screener="s", limit=1)
            self.assertEqual(len(rows), 1)
            self.assertIsNone(next_offset)
            rows, next_offset = import_history.list_imports(
                db, task_id="t", screener="s", limit=20)
            self.assertEqual(len(rows), 1)


# ---------------------------------------------------------------------------
# F4-83 / F4-84 / F4-85 / F4-86 / F4-87 / F4-88 · 来源迁移
# ---------------------------------------------------------------------------
class ImportSourceMigrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rf_r9l1_src_")
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _legacy_db(self, name: str = "legacy.db") -> Path:
        db = self.tmp / name
        workflow.migrate(db)
        db_mod.init_db(db)
        # 真实导入溯源表（workflow_source_records / workflow_import_files …）
        review_imports.migrate(db)
        # 完整升级到 v4/v5（建 workflow_source_details）；需要「旧库」形态的
        # 用例再用 _pending_v4v5 退回。
        import_sources.migrate(db)
        return db

    def _pending_v4v5(self, db: Path) -> Path:
        """把已升级的库退回「v4/v5 待升级」形态（模拟 a080fde 旧库）。"""
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute("DELETE FROM workflow_migrations WHERE version IN (4,5)")
        for backup in db.parent.glob(db.name + ".pre-*.bak"):
            backup.unlink()
        return db

    @staticmethod
    def _migrate_subprocess(db: Path) -> None:
        """在**新解释器**里跑迁移（new process → 非 fresh 库，走备份分支）。

        复审探针同款形态：进程内新建的库被标记为 fresh，备份按设计跳过，
        因此「旧库升级」必须用独立进程模拟。
        """
        script = "\n".join((
            "import sys",
            "sys.path.insert(0, r'%s')" % ROOT,
            "from coscreen import import_sources as ims",
            "ims.migrate(r'%s')" % db,
        )) + "\n"
        proc = subprocess.run(
            [sys.executable, "-I", "-c", script], capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        if proc.returncode != 0:
            raise AssertionError(proc.stderr)

    def _versions(self, db: Path) -> list[int]:
        with closing(sqlite3.connect(db)) as conn:
            return [row[0] for row in conn.execute(
                "SELECT version FROM workflow_migrations ORDER BY version")]

    def test_bad_original_json_and_surrogate_filename_do_not_block_migration(self):
        db = self._legacy_db()
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute(
                "INSERT INTO workflow_source_records VALUES"
                "('r1',?,'dg',0,'f.csv','{not json','{}','k1','k1',NULL,NULL,"
                "'new','staged','t')",
                (workflow.UNASSIGNED,))
            conn.execute(
                "INSERT INTO articles(zotero_key,title,raw_json,is_duplicate_of,import_order)"
                " VALUES('K','T',?,NULL,0)",
                (json.dumps({"source_file": "x" * 10 + "\ud800"}),))
            # 显式挂 UNASSIGNED 链接：本夹具的 workflow.migrate 早于 articles
            # 建表（catalog 触发器未建），不会自动写 workflow_article_sources。
            conn.execute(
                "INSERT OR IGNORE INTO workflow_article_sources VALUES('K',?)",
                (workflow.UNASSIGNED,))
            conn.execute("DELETE FROM workflow_migrations WHERE version IN (4,5)")
        # 修前：JSONDecodeError / UnicodeEncodeError，版本 4/5 永不落地（红）
        import_sources.migrate(db)
        self.assertEqual(self._versions(db), sorted([1, review_imports.MIGRATION_VERSION, import_sources.VERSION, import_sources.FILENAME_VERSION]))
        with closing(sqlite3.connect(db)) as conn:
            source_id = conn.execute(
                "SELECT source_id FROM workflow_source_records WHERE id='r1'").fetchone()[0]
        self.assertNotEqual(source_id, workflow.UNASSIGNED)

    def test_corrupt_backup_is_replaced_by_usable_snapshot(self):
        db = self._pending_v4v5(self._legacy_db())
        backup = Path(str(db) + ".pre-source-v4.bak")
        backup.write_bytes(b"NOT-A-SQLITE-DATABASE" * 8)
        self.assertFalse(import_sources._backup_usable(backup))
        # 修前：坏 .bak 被 exists() 信任 → 迁移照做、备份仍 176B 不可打开（红）
        self._migrate_subprocess(db)
        self.assertEqual(self._versions(db), sorted([1, review_imports.MIGRATION_VERSION, import_sources.VERSION, import_sources.FILENAME_VERSION]))
        self.assertTrue(import_sources._backup_usable(backup))
        self.assertGreater(backup.stat().st_size, 1024)

    def test_healthy_backup_is_reused_not_overwritten(self):
        db = self._pending_v4v5(self._legacy_db())
        backup = Path(str(db) + ".pre-source-v4.bak")
        with closing(sqlite3.connect(backup)) as target:
            with closing(workflow.connect(db, read_only=True)) as source:
                source.backup(target)
        before = hashlib.sha256(backup.read_bytes()).hexdigest()
        self._migrate_subprocess(db)
        self.assertEqual(hashlib.sha256(backup.read_bytes()).hexdigest(), before,
                         "可用备份不得被覆盖")

    def test_prepare_files_keeps_explicit_source_id(self):
        db = self._legacy_db()
        with closing(workflow.connect(db)) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO workflow_sources VALUES"
                "('explicit-src','PubMed','pubmed',1,datetime('now'))")
            conn.commit()
            files = [{"name": "alpha.csv", "digest": "d1", "articles": [1],
                      "source_id": "explicit-src", "source_ids": ["explicit-src"]}]
            import_sources.prepare_files(conn, files)
            self.assertEqual(files[0]["source_id"], "explicit-src")
            self.assertEqual(files[0]["source_ids"], ["explicit-src"])
            # 对照组：'auto' 仍按文件名派生（现行前端行为逐位不变）
            auto = [{"name": "beta.csv", "digest": "d2", "articles": [1, 2],
                     "source_id": "auto", "source_ids": []}]
            import_sources.prepare_files(conn, auto)
            derived = auto[0]["source_id"]
            self.assertNotEqual(derived, "auto")
            self.assertEqual(auto[0]["source_ids"], [derived, derived])
            self.assertEqual(
                derived,
                str(__import__("uuid").uuid5(
                    __import__("uuid").NAMESPACE_URL,
                    "reviewflow:source:filename:beta.csv")))

    def test_first_does_not_truncate_so_identity_is_single(self):
        long_name = "L" * 230 + ".ris"
        self.assertEqual(len(import_sources.first({"source_file": long_name}, "source_file")),
                         len(long_name))
        db = self._legacy_db()
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute(
                "INSERT INTO articles(zotero_key,title,raw_json,is_duplicate_of,import_order)"
                " VALUES('K2','T2',?,NULL,0)", (json.dumps({"source_file": long_name}),))
            conn.execute(
                "INSERT INTO workflow_article_sources VALUES('K2',?)", (workflow.UNASSIGNED,))
            conn.execute("DELETE FROM workflow_migrations WHERE version IN (4,5)")
        import_sources.migrate(db)
        with closing(workflow.connect(db)) as conn:
            legacy_sid = conn.execute(
                "SELECT source_id FROM workflow_article_sources WHERE zotero_key='K2'"
            ).fetchone()[0]
            upload_files = [{"name": long_name, "digest": "d9", "articles": [1],
                             "source_id": "auto", "source_ids": []}]
            import_sources.prepare_files(conn, upload_files)
            conn.commit()
            self.assertEqual(upload_files[0]["source_id"], legacy_sid,
                             "迁移路径与上传路径必须派生出同一来源")
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM workflow_sources WHERE normalized_name=?",
                (workflow.normalized(long_name),)).fetchone()[0], 1)

    def test_ensure_never_rewrites_reserved_unassigned_row(self):
        db = self._legacy_db()
        with closing(workflow.connect(db)) as conn:
            sid = import_sources.ensure(conn, import_sources.detect({}, "unassigned", "d"))
            conn.commit()
            self.assertEqual(sid, workflow.UNASSIGNED)
            reserved = conn.execute(
                "SELECT name,normalized_name FROM workflow_sources WHERE id=?",
                (workflow.UNASSIGNED,)).fetchone()
            self.assertEqual(tuple(reserved), ("Unassigned", "unassigned"))
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM workflow_source_details WHERE source_id=?",
                (workflow.UNASSIGNED,)).fetchone())
            # 对照组：普通文件名的来源解析逐位不变（按 identity 派生、可重复）
            first = import_sources.ensure(conn, import_sources.detect({}, "alpha.csv", "d"))
            second = import_sources.ensure(conn, import_sources.detect({}, "alpha.csv", "d"))
            self.assertEqual(first, second)
            self.assertNotEqual(first, workflow.UNASSIGNED)


class ImportSourceRaceWindowTests(unittest.TestCase):
    """F4-83 决定性复现：外层版本检查通过后、写事务开始前，另一 worker 完成迁移。

    复审探针是真实双进程 8/8 轮复现；此处把窗口精确落在「外层只读检查之后、
    BEGIN IMMEDIATE 之前」——这正是双 worker 并发时后到者所处的时刻。修前
    迁移体不复检版本，会在 INSERT INTO workflow_migrations 上撞主键；修后
    事务内复检发现另一进程已完成，整体回滚为无操作。
    """

    def test_version_rechecked_inside_write_transaction(self):
        with tempfile.TemporaryDirectory(prefix="rf_r9l1_83w_") as tmp:
            db = Path(tmp) / "legacy.db"
            workflow.migrate(db)
            db_mod.init_db(db)
            review_imports.migrate(db)
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.execute("DELETE FROM workflow_migrations WHERE version IN (4,5)")
            with closing(sqlite3.connect(db)) as conn:
                state_before = conn.execute(
                    "SELECT version,catalog_revision FROM workflow_state "
                    "WHERE singleton=1").fetchone()
            original = import_sources.workflow.connect_for_initial_migration

            def racing_connect(path):
                connection = original(path)
                # 「另一 worker」在外层检查之后完成 v4/v5
                with closing(sqlite3.connect(path)) as other, other:
                    other.execute(
                        "INSERT OR IGNORE INTO workflow_migrations VALUES(4,'t')")
                    other.execute(
                        "INSERT OR IGNORE INTO workflow_migrations VALUES(5,'t')")
                return connection

            import_sources.workflow.connect_for_initial_migration = racing_connect
            try:
                # 修前：IntegrityError: UNIQUE constraint failed: workflow_migrations.version
                import_sources.migrate(db)
            finally:
                import_sources.workflow.connect_for_initial_migration = original
            with closing(sqlite3.connect(db)) as conn:
                versions = [row[0] for row in conn.execute(
                    "SELECT version FROM workflow_migrations ORDER BY version")]
                state = conn.execute(
                    "SELECT version,catalog_revision FROM workflow_state "
                    "WHERE singleton=1").fetchone()
            self.assertEqual(versions, sorted([1, review_imports.MIGRATION_VERSION, import_sources.VERSION, import_sources.FILENAME_VERSION]))
            # 另一进程已完成 → 本次迁移体必须整体回滚为无操作（修前重跑两次
            # v4/v5 迁移体：state.version +1、catalog_revision +1）。
            self.assertEqual(tuple(state), (state_before[0], state_before[1]))

    def test_v5_version_rechecked_inside_write_transaction(self):
        """同一窗口的 v5 分支（v4 已完成、v5 待升级时另一 worker 抢先）。"""
        with tempfile.TemporaryDirectory(prefix="rf_r9l1_83v5_") as tmp:
            db = Path(tmp) / "legacy.db"
            workflow.migrate(db)
            db_mod.init_db(db)
            review_imports.migrate(db)
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.execute("DELETE FROM workflow_migrations WHERE version IN (4,5)")
            original = import_sources.workflow.connect_for_initial_migration
            calls = {"n": 0}
            state_before = [None]

            def racing_connect(path):
                calls["n"] += 1
                connection = original(path)
                if calls["n"] == 2:  # v5 的写连接：另一 worker 此刻完成 v5
                    # v4 已提交：此刻的 state 即「本次 v5 之前的基线」
                    with closing(sqlite3.connect(path)) as peek:
                        state_before[0] = peek.execute(
                            "SELECT version,catalog_revision FROM workflow_state "
                            "WHERE singleton=1").fetchone()
                    with closing(sqlite3.connect(path)) as other, other:
                        other.execute(
                            "INSERT OR IGNORE INTO workflow_migrations VALUES(5,'t')")
                return connection

            import_sources.workflow.connect_for_initial_migration = racing_connect
            try:
                import_sources.migrate(db)
            finally:
                import_sources.workflow.connect_for_initial_migration = original
            with closing(sqlite3.connect(db)) as conn:
                state = conn.execute(
                    "SELECT version,catalog_revision FROM workflow_state "
                    "WHERE singleton=1").fetchone()
            # 修前：v5 迁移体重跑 → version/catalog_revision 各 +1（红）
            self.assertIsNotNone(state_before[0])
            self.assertEqual(tuple(state), tuple(state_before[0]))


class ImportSourceConcurrencyTests(unittest.TestCase):
    """F4-83：双进程并发首升级，两方都不得抛（修前一方 IntegrityError）。"""

    WORKER = (
        "import sys, time\n"
        "sys.path.insert(0, r'{root}')\n"
        "from coscreen import import_sources as ims\n"
        "start = float(sys.argv[1])\n"
        "while time.time() < start:\n"
        "    time.sleep(0.001)\n"
        "try:\n"
        "    ims.migrate(r'{db}')\n"
        "    print('OK')\n"
        "except Exception as exc:\n"
        "    print('EXC', type(exc).__name__, exc)\n"
    )

    def test_two_processes_upgrade_once_without_errors(self):
        with tempfile.TemporaryDirectory(prefix="rf_r9l1_83_") as tmp:
            base = Path(tmp) / "base.db"
            workflow.migrate(base)
            db_mod.init_db(base)
            review_imports.migrate(base)
            with closing(sqlite3.connect(base)) as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            for trial in range(2):
                db = Path(tmp) / f"trial{trial}.db"
                db.write_bytes(base.read_bytes())
                for suffix in ("-wal", "-shm"):
                    side = Path(str(db) + suffix)
                    if side.exists():
                        side.unlink()
                start = time.time() + 0.6
                script = self.WORKER.format(root=str(ROOT), db=str(db))
                procs = [
                    subprocess.Popen(
                        [sys.executable, "-I", "-c", script, repr(start)],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        text=True, encoding="utf-8", errors="replace",
                        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
                    for _ in range(2)
                ]
                outputs = [proc.communicate(timeout=180)[0].strip() for proc in procs]
                self.assertEqual(outputs, ["OK", "OK"], outputs)
                with closing(sqlite3.connect(db)) as conn:
                    versions = [row[0] for row in conn.execute(
                        "SELECT version FROM workflow_migrations ORDER BY version")]
                self.assertEqual(versions, sorted([1, review_imports.MIGRATION_VERSION, import_sources.VERSION, import_sources.FILENAME_VERSION]))


# ---------------------------------------------------------------------------
# F2-74 / F2-77 / F2-78 / F2-79 · CLI 契约
# ---------------------------------------------------------------------------
class CliContractTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rf_r9l1_cli_")
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_merge_validation_error_exits_two_not_one(self):
        a = _decisions_csv(self.tmp / "a.csv", [("K1", "include"), ("K2", "exclude")])
        bad = _decisions_csv(self.tmp / "b_space.csv", [("K1 ", "include"), ("K2", "exclude")])
        good = _decisions_csv(self.tmp / "b_ok.csv", [("K1", "include"), ("K2", "include")])
        # 修前：ValueError 逃出 main → traceback + exit 1（红）
        proc = _run_cli(["merge", "--a", a, "--b", bad, "--outdir", self.tmp / "o1"])
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("无首尾空格", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        # 合法对照：exit 0、四文件齐全（行为不变）
        proc = _run_cli(["merge", "--a", a, "--b", good, "--outdir", self.tmp / "o2"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for name in ("merged_detail.csv", "conflicts.csv", "stats.json", "prisma.dot"):
            self.assertTrue((self.tmp / "o2" / name).is_file(), name)

    def test_merge_outdir_colliding_with_file_exits_two(self):
        a = _decisions_csv(self.tmp / "a.csv", [("K1", "include")])
        b = _decisions_csv(self.tmp / "b.csv", [("K1", "include")])
        blocker = self.tmp / "blocker.txt"
        blocker.write_text("x", encoding="utf-8")
        # 修前：FileExistsError [WinError 183] 逃出 → exit 1（红）
        proc = _run_cli(["merge", "--a", a, "--b", b, "--outdir", blocker])
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_export_to_unwritable_target_exits_two(self):
        source = _import_csv(self.tmp / "imp.csv", [("K1", "T1")])
        db = self.tmp / "g.db"
        self.assertEqual(_run_cli(["import", "--file", source, "--db", db]).returncode, 0)
        # 修前：PermissionError 逃出 → exit 1（红）
        proc = _run_cli(["export", "--db", db, "--out", self.tmp])
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        # 合法对照：正常导出 exit 0 且文件存在
        out = self.tmp / "exp.csv"
        proc = _run_cli(["export", "--db", db, "--out", out])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(out.is_file())

    def test_bad_db_paths_and_non_db_files_exit_two(self):
        fake = self.tmp / "fake.db"
        fake.write_bytes(b"this is not a database" * 10)
        source = _import_csv(self.tmp / "imp.csv", [("K1", "T1")])
        missing_parent = self.tmp / "nodir" / "deep" / "x.db"
        # 修前：DatabaseError/OperationalError 逃出 → exit 1（红）
        for args in (
            ["stats", "--db", fake],
            ["import", "--file", source, "--db", missing_parent],
            ["export", "--db", fake, "--out", self.tmp / "x.csv"],
            ["simulate", "--db", fake, "--gold", _decisions_csv(
                self.tmp / "gold.csv", [("K1", "include")])],
        ):
            proc = _run_cli(args)
            self.assertEqual(proc.returncode, 2, f"{args}: {proc.stderr}")
            self.assertNotIn("Traceback", proc.stderr, args)
        self.assertFalse(missing_parent.exists(),
                         "坏路径导入不得留下数据库文件")

    def test_conflicting_gold_duplicate_keys_are_rejected(self):
        source = _import_csv(self.tmp / "imp.csv", [("K1", "T1"), ("K2", "T2"), ("K3", "T3")])
        db = self.tmp / "g.db"
        self.assertEqual(_run_cli(["import", "--file", source, "--db", db]).returncode, 0)
        gold_inc_last = _decisions_csv(
            self.tmp / "gold_inc.csv",
            [("K1", "include"), ("K2", "exclude"), ("K3", "include"), ("K1", "exclude")])
        gold_exc_last = _decisions_csv(
            self.tmp / "gold_exc.csv",
            [("K1", "exclude"), ("K2", "exclude"), ("K3", "include"), ("K1", "include")])
        # 修前：两种行序都 exit 0 且指标不同（相关 1 vs 2）——静默 last-wins（红）
        for gold in (gold_inc_last, gold_exc_last):
            proc = _run_cli(["simulate", "--db", db, "--gold", gold])
            self.assertEqual(proc.returncode, 2, proc.stdout)
            self.assertIn("冲突标签", proc.stderr)
        # 合法对照：无冲突（含同值重复行）照常成功、指标稳定
        dup_same = _decisions_csv(
            self.tmp / "gold_same.csv",
            [("K1", "include"), ("K1", "include"), ("K2", "exclude"), ("K3", "include")])
        reordered = _decisions_csv(
            self.tmp / "gold_same2.csv",
            [("K3", "include"), ("K1", "include"), ("K2", "exclude")])
        first = _run_cli(["simulate", "--db", db, "--gold", dup_same])
        second = _run_cli(["simulate", "--db", db, "--gold", reordered])
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)


# ---------------------------------------------------------------------------
# 打开 legacy 库时用到的 source-record 表（与 review_imports.workflow_source_records
# 同构；此处只需要迁移读取的列）。
# ---------------------------------------------------------------------------
_SOURCE_RECORD_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_source_records(
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES workflow_sources(id), digest TEXT NOT NULL,
 row_index INTEGER NOT NULL, filename TEXT NOT NULL, original_json TEXT NOT NULL, article_json TEXT NOT NULL,
 incoming_key TEXT NOT NULL, staged_key TEXT NOT NULL, canonical_key TEXT,
 target_record_id TEXT REFERENCES workflow_source_records(id), outcome TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'staged', created_at TEXT NOT NULL,
 UNIQUE(source_id,digest,row_index));
"""


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
