"""r13 数据层读锁泳道回归钉：L6d1（tailpieces_2.1.4.md 第②节）。

问题（修复前实测，E:/rf214tmp/dwave/tail/probe_db_lock.py）：

- ``coscreen/db.py::_add_column_if_missing`` 第一行就是 ``BEGIN IMMEDIATE``，
  即使列早已存在、调用来自纯读函数（``review_analysis.list_rob`` /
  ``dta_analysis.list_results``），每次读都先抢库级写锁；
- ``coscreen/analysis_storage.py::ensure_analysis_storage`` 无条件执行
  ``INSERT INTO review_effect_versions … SELECT … WHERE NOT EXISTS``，
  即使一条都不缺也要写锁；冷 ``_connect`` 会撞上它。

另一连接持 ``BEGIN IMMEDIATE``（模拟长导入）时，上述读路径在 5 秒 busy
超时后抛 ``OperationalError: database is locked``，直达 HTTP GET 成 500。

本文件用真实 SQLite 连接（WAL）复现三类钉子，撤修复必红：

1. 持写锁时，热缓存下 ``list_rob`` / ``list_results`` < 1 秒返回；
2. 持写锁时，清空 ``_MIGRATED_SCHEMAS`` 后 ``db._connect`` < 1 秒返回；
3. 持写锁时，``_add_column_if_missing``（列已存在）与
   ``ensure_analysis_storage``（无缺 version 行）< 1 秒返回且不抛异常。

同时钉住语义不变（防“修快路径时把真正要写的路径也跳过”）：

4. 无锁缺列 → 仍执行 ALTER，且既有行保留；
5. 两连接并发补同一列 → 幂等（都成功、列只出现一次）；
6. 确有缺 version 的 effect → 仍补插 ``revision=1`` 的历史版本行。
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen import analysis_storage, db as db_mod, dta_analysis, review_analysis

#: 钉子上限：正常 WAL 读在毫秒级；修复前是 5 秒 busy 超时。
READ_LOCK_BUDGET_SECONDS = 1.0

_PROBE_TABLE = "r13_read_lock_probe"


class _ReadLockCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="rf_r13_read_lock_")
        self.db = Path(self._tmp.name) / "coscreen.sqlite"
        db_mod.init_db(self.db)  # 建库 + 自愈迁移，热缓存登记 schema cookie
        self._holders: list[sqlite3.Connection] = []

    def tearDown(self) -> None:
        for conn in self._holders:
            try:
                conn.rollback()
            finally:
                conn.close()
        db_mod._MIGRATED_SCHEMAS.pop(self.db.resolve(), None)
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------
    def _hold_write_lock(self) -> sqlite3.Connection:
        """另一连接持 BEGIN IMMEDIATE 并写一行（模拟进行中的长导入）。"""
        conn = sqlite3.connect(self.db, timeout=0.2, isolation_level=None)
        self._holders.append(conn)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('r13_writer_holds_lock','1')"
        )
        return conn

    def _timed(self, fn, *args):
        start = time.perf_counter()
        value = fn(*args)
        return time.perf_counter() - start, value

    def _make_probe_table(self) -> None:
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("CREATE TABLE IF NOT EXISTS r13_read_lock_probe (a TEXT)")
            conn.execute("DELETE FROM r13_read_lock_probe")
            conn.execute("INSERT INTO r13_read_lock_probe(a) VALUES ('kept')")

    def _probe_columns(self) -> list[str]:
        with closing(sqlite3.connect(self.db)) as conn:
            rows = conn.execute("PRAGMA table_info(r13_read_lock_probe)").fetchall()
        return [row[1] for row in rows]

    def _add_probe_column(self, conn: sqlite3.Connection) -> None:
        db_mod._add_column_if_missing(
            conn, "r13_read_lock_probe", "b",
            "ALTER TABLE r13_read_lock_probe ADD COLUMN b TEXT DEFAULT ''",
        )


class MirrorReadPathsTests(_ReadLockCase):
    """① 热缓存下镜像读路径不抢写锁。"""

    def test_list_rob_returns_within_budget_while_writer_holds_lock(self) -> None:
        self._hold_write_lock()
        elapsed, rows = self._timed(review_analysis.list_rob, self.db)
        self.assertLess(
            elapsed,
            READ_LOCK_BUDGET_SECONDS,
            f"list_rob 在长写事务下耗时 {elapsed:.2f}s（应 <"
            f"{READ_LOCK_BUDGET_SECONDS}s，修复前为 5s busy 超时）",
        )
        self.assertEqual(rows, [])

    def test_list_dta_results_returns_within_budget_while_writer_holds_lock(self) -> None:
        self._hold_write_lock()
        elapsed, rows = self._timed(dta_analysis.list_results, self.db)
        self.assertLess(
            elapsed,
            READ_LOCK_BUDGET_SECONDS,
            f"list_results 在长写事务下耗时 {elapsed:.2f}s（应 <"
            f"{READ_LOCK_BUDGET_SECONDS}s，修复前为 5s busy 超时）",
        )
        self.assertEqual(rows, [])


class ColdConnectTests(_ReadLockCase):
    """② 冷连接迁移序列不抢写锁。"""

    def test_cold_connect_returns_within_budget_while_writer_holds_lock(self) -> None:
        db_mod._MIGRATED_SCHEMAS.pop(self.db.resolve(), None)  # 强制走完整自愈序列
        self._hold_write_lock()
        start = time.perf_counter()
        try:
            conn = db_mod._connect(self.db)
        except sqlite3.OperationalError as exc:
            elapsed = time.perf_counter() - start
            self.fail(f"冷 _connect 在长写事务下 {elapsed:.2f}s 后失败：{exc}")
            return
        elapsed = time.perf_counter() - start
        conn.close()
        self.assertLess(
            elapsed,
            READ_LOCK_BUDGET_SECONDS,
            f"冷 _connect 在长写事务下耗时 {elapsed:.2f}s（应 <"
            f"{READ_LOCK_BUDGET_SECONDS}s，修复前为 5s busy 超时）",
        )


class MigrationPrimitiveFastPathTests(_ReadLockCase):
    """③ 已就绪的迁移原语在持锁时是纯只读。"""

    def test_add_column_if_missing_existing_column_is_read_only(self) -> None:
        self._make_probe_table()
        with closing(sqlite3.connect(self.db)) as conn, conn:
            self._add_probe_column(conn)
        self.assertIn("b", self._probe_columns())

        holder = self._hold_write_lock()
        elapsed, _ = self._timed(self._add_probe_column, holder)
        self.assertLess(
            elapsed,
            READ_LOCK_BUDGET_SECONDS,
            f"列已存在时 _add_column_if_missing 仍耗时 {elapsed:.2f}s",
        )

    def test_ensure_analysis_storage_noop_is_read_only(self) -> None:
        # 先完成一次迁移；正常库此时没有缺 version 的 effect。
        with closing(sqlite3.connect(self.db)) as conn, conn:
            analysis_storage.ensure_analysis_storage(conn)
        with closing(sqlite3.connect(self.db)) as check:
            missing = check.execute(
                "SELECT COUNT(*) FROM review_effects e WHERE NOT EXISTS ("
                "SELECT 1 FROM review_effect_versions v WHERE v.result_id=e.result_id)"
            ).fetchone()[0]
        self.assertEqual(missing, 0)

        self._hold_write_lock()
        conn = sqlite3.connect(self.db, timeout=5.0)
        try:
            elapsed, _ = self._timed(analysis_storage.ensure_analysis_storage, conn)
            self.assertLess(
                elapsed,
                READ_LOCK_BUDGET_SECONDS,
                f"ensure_analysis_storage 无缺 version 行时仍耗时 {elapsed:.2f}s",
            )
        finally:
            conn.close()


class WritePathPreservedTests(_ReadLockCase):
    """④⑤⑥ 快路径不得吞掉真正的写入。"""

    def test_missing_column_is_still_added_without_contention(self) -> None:
        self._make_probe_table()
        with closing(sqlite3.connect(self.db)) as conn, conn:
            self._add_probe_column(conn)
            columns = [row[1] for row in conn.execute(
                "PRAGMA table_info(r13_read_lock_probe)")]
            self.assertEqual(columns.count("b"), 1)
            rows = conn.execute("SELECT a, b FROM r13_read_lock_probe").fetchall()
            self.assertEqual(rows, [("kept", "")])

    def test_concurrent_add_column_is_idempotent(self) -> None:
        self._make_probe_table()
        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                with closing(sqlite3.connect(self.db, timeout=5.0)) as conn, conn:
                    barrier.wait(timeout=5.0)
                    self._add_probe_column(conn)
            except BaseException as exc:  # noqa: BLE001 - 汇总到主线程断言
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10.0)
            self.assertFalse(thread.is_alive(), "补列线程未在超时内结束")
        self.assertEqual(errors, [], f"并发补列抛出异常：{errors!r}")
        self.assertEqual(self._probe_columns().count("b"), 1)

    def test_ensure_analysis_storage_backfills_legacy_effect(self) -> None:
        legacy = Path(self._tmp.name) / "legacy.sqlite"
        with closing(sqlite3.connect(legacy)) as conn, conn:
            conn.execute(
                "CREATE TABLE review_effects ("
                "result_id INTEGER PRIMARY KEY, study_id TEXT, comparison TEXT,"
                "outcome TEXT, timepoint TEXT, measure TEXT, estimate REAL,"
                "se REAL, source_key TEXT, input_json TEXT, entry_method TEXT,"
                "selected INTEGER, source_locator TEXT)"
            )
            conn.execute(
                "INSERT INTO review_effects VALUES ("
                "7,'s','c','o','t','MD',0.1,0.2,'k','{}','manual',1,'')"
            )
        with closing(sqlite3.connect(legacy)) as conn, conn:
            analysis_storage.ensure_analysis_storage(conn)
            versions = conn.execute(
                "SELECT result_id, revision, measure FROM review_effect_versions"
            ).fetchall()
        self.assertEqual(versions, [(7, 1, "MD")])


if __name__ == "__main__":
    unittest.main()
