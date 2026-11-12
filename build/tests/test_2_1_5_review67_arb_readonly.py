# -*- coding: utf-8 -*-
"""review67 M-08 · 筛选员个人库「暂时不可读」契约收紧（仲裁读路径）。

- 现存文件却打不开（SQLite ``unable to open database file``：ACL 拒绝 /
  网络盘抖动 / WAL ``-shm`` 不可写）必须抛 ``ScreenerLibraryUnavailableError``
  （HTTP 层既有 503 映射），**不得**折叠成「该成员没有决策」——否则缺员
  面板的 κ/一致率照常计算，还可能被 server-save 落成 append-only 仲裁记录。
- 不存在 / 目录路径维持「无决策」静默跳过（生产入口 ``TaskInfo.db_files``
  上游过滤；既有钉依赖该口径且只读加载绝不创建个人库）。
- 同族收紧：``load_screener_decision_packages`` 的 ``path.resolve()`` OSError
  由静默 continue 改为上抛（review67 M-08）。
- 回归伴钉：锁忙仍上抛、永久损坏（非 SQLite 文件）仍降级剔除。
"""
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen import screening_arbitration  # noqa: E402
from coscreen import db as db_mod  # noqa: E402
from coscreen.models import Article  # noqa: E402


def _library_with_decision(name: str) -> Path:
    """建一个含单条决策的个人库（真实 SQLite，不经 HTTP）。"""
    path = Path(tempfile.mkdtemp(prefix="r67arb_")) / f"{name}.db"
    db_mod.init_db(path)
    db_mod.upsert_articles(
        path, [Article(zotero_key="K1", title="Study K1")], [])
    db_mod.save_decision(path, "K1", "include")
    return path


class UnableToOpenRaisesTests(unittest.TestCase):
    """现存文件 unable-to-open → ScreenerLibraryUnavailableError（M-08 主钉）。"""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory(prefix="r67arb_open_")
        self.root = Path(self._dir.name)

    def tearDown(self):
        self._dir.cleanup()

    def test_existing_file_unable_to_open_raises_not_silent_empty(self):
        """ACL 拒绝等「现存却打不开」不得返回 []（此前探针实测静默空）。"""
        existing = self.root / "acl.db"
        existing.write_bytes(b"placeholder-bytes")  # 现存文件（is_file() 真）
        with mock.patch.object(
                screening_arbitration.sqlite3, "connect",
                side_effect=sqlite3.OperationalError(
                    "unable to open database file")):
            with self.assertRaises(
                    screening_arbitration.ScreenerLibraryUnavailableError):
                screening_arbitration._decision_rows_read_only(
                    existing, "stage1")

    def test_load_with_unopenable_member_raises_before_short_panel(self):
        """两名有效成员 + 一名打不开：整体上抛，绝不产出缺员合并表。"""
        a = _library_with_decision("alpha")
        b = _library_with_decision("beta")
        blocked = self.root / "blocked.db"
        blocked.write_bytes(b"placeholder-bytes")
        try:
            with mock.patch.object(
                    screening_arbitration.sqlite3, "connect",
                    side_effect=sqlite3.OperationalError(
                        "unable to open database file")):
                with self.assertRaises(
                        screening_arbitration.ScreenerLibraryUnavailableError):
                    screening_arbitration.load_screener_decision_packages(
                        [a, b, blocked])
        finally:
            for path in (a, b):
                path.unlink(missing_ok=True)

    def test_missing_and_directory_paths_keep_silent_skip(self):
        """不存在/目录路径维持「无决策」跳过（真实 sqlite 路径，零 mock）。"""
        missing = self.root / "ghost.db"
        directory = self.root / "adir.db"
        directory.mkdir()
        self.assertEqual(
            screening_arbitration._decision_rows_read_only(missing, "stage1"),
            [])
        self.assertEqual(
            screening_arbitration._decision_rows_read_only(directory, "stage1"),
            [])
        self.assertFalse(missing.exists(), "只读读取不得创建个人库")


class ResolveOSErrorRaisesTests(unittest.TestCase):
    """同族收紧：resolve() 失败（权限/网络盘）不再静默 continue（M-08）。"""

    def test_resolve_oserror_raises_instead_of_silent_continue(self):
        a = _library_with_decision("alpha")
        b = _library_with_decision("beta")
        try:
            with mock.patch.object(Path, "resolve",
                                   side_effect=OSError("network drive")):
                with self.assertRaises(
                        screening_arbitration.ScreenerLibraryUnavailableError):
                    screening_arbitration.load_screener_decision_packages(
                        [a, b])
        finally:
            for path in (a, b):
                path.unlink(missing_ok=True)


class RegressionCompanionTests(unittest.TestCase):
    """既有口径回归伴钉：锁忙上抛、永久损坏降级、<2 有效 ValueError。"""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory(prefix="r67arb_reg_")
        self.root = Path(self._dir.name)
        self.paths = []
        for name, decision in (("alpha", "include"), ("beta", "exclude"),
                               ("carol", "include")):
            path = self.root / f"{name}.db"
            db_mod.init_db(path)
            db_mod.upsert_articles(
                path, [Article(zotero_key="K1", title="Study K1")], [])
            db_mod.save_decision(path, "K1", decision)
            self.paths.append(path)

    def tearDown(self):
        self._dir.cleanup()

    @contextmanager
    def _exclusive_lock(self, path: Path):
        with closing(sqlite3.connect(path)) as setup:
            setup.execute("PRAGMA journal_mode=DELETE")
        holder = sqlite3.connect(path, timeout=1)
        holder.execute("BEGIN EXCLUSIVE")
        try:
            yield
        finally:
            holder.rollback()
            holder.close()

    def test_locked_member_still_raises(self):
        with self._exclusive_lock(self.paths[2]), mock.patch.object(
                screening_arbitration, "SCREENER_READ_TIMEOUT", 0.2):
            with self.assertRaises(
                    screening_arbitration.ScreenerLibraryUnavailableError):
                screening_arbitration.load_screener_decision_packages(
                    self.paths)

    def test_corrupt_library_still_degrades_to_skip(self):
        corrupt = self.root / "broken.db"
        corrupt.write_bytes(b"not a sqlite database")
        packages = screening_arbitration.load_screener_decision_packages(
            self.paths + [corrupt])
        self.assertEqual(sorted(packages), ["alpha", "beta", "carol"])

    def test_fewer_than_two_valid_still_value_error(self):
        only = self.paths[0]
        ghost = self.root / "ghost.db"  # 不存在 → 跳过 → 有效成员 1 → ValueError
        with self.assertRaises(ValueError) as caught:
            screening_arbitration.load_screener_decision_packages([only, ghost])
        self.assertIn("2 到 9", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
