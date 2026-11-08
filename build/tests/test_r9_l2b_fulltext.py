"""r9 L2b 泳道钉子 —— 全文层（b06·F4-49/F4-53、b15·F4-50/F4-51/F4-52）。

每条钉子按 `inventory_夜扫286确认分组.md` §3.2 的探针形态复现；把对应修复
回退，该用例必红：

- **F4-49【高】** ``save_pdf`` 的备份清理（``backup.unlink``）在事务提交后
  仍处恢复守卫内：Windows 上备份被占用时抛 PermissionError → 刚提交的新文件
  被备份换回旧文件，DB 新 sha256 / 磁盘旧字节永久分叉；且提交+恢复都失败时
  备份是唯一可恢复副本却被删掉。修法：清理移到守卫外且失败不致命；只有
  「尚未替换」或「已成功恢复」才删备份。
- **F4-53【高】** 复筛进度与复筛队列口径分裂：进度排除 pending 候选组与已
  确认重复，``stage2_queue(mode="own")`` 不过滤——pending 期进度 total 2 vs
  队列 3；判 duplicate 后队列仍含重复键。修法：两者共用同一 SQL 谓词。
- **F4-50【高】** ``add_highlight_rects`` 先查后插（快照在事务外）且 DDL 无
  唯一约束，双线程并发提交同一批 400 事件各插一行（8/8 轮 rows=800）。
  修法：幂等判定与写入同处 ``BEGIN IMMEDIATE``，逐条在事务内重新判定。
- **F4-51** ``load_pdf_bytes`` 先整读后判 ``max_bytes``（60MB 文件 / 1MB 上限
  → 峰值 60MB）。修法：先 ``stat`` 判长度，超限不读。
- **F4-52** 畸形事件抛 ``AttributeError``/``TypeError`` 而非 docstring 承诺的
  ``ValueError``（``events=['abc']``、``rects=5``）。

全部使用临时目录/临时库（``REVIEWFLOW_TEST_TMP`` 可把根目录指到非 C 盘），
不触碰真实数据。
"""
from __future__ import annotations

import hashlib
import io
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen import db as db_mod
from coscreen import fulltext as fulltext_mod
from coscreen.models import Article


def _temp_root() -> str:
    base = os.environ.get("REVIEWFLOW_TEST_TMP")
    if base:
        Path(base).mkdir(parents=True, exist_ok=True)
        return base
    return tempfile.gettempdir()


def _pdf(marker: str) -> bytes:
    """pypdf 生成真实可解析 PDF；marker 进元数据，保证字节身份可区分。"""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_metadata({"/Title": marker})
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _new_db(root: Path, name: str, keys: list[str]) -> Path:
    db = root / name
    db_mod.init_db(str(db))
    db_mod.upsert_articles(
        str(db), [Article(zotero_key=k, title=f"Study {k}") for k in keys], [])
    return db


class SavePdfBackupLifecycle(unittest.TestCase):
    """F4-49：备份清理失败不得回滚已提交状态；恢复失败不得销毁唯一备份。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rf_r9_l2b_f449_", dir=_temp_root(),
                                                ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.db = _new_db(self.root, "pdf.db", ["P1"])
        self.pdfs = self.root / "pdfs"

    def tearDown(self):
        self._tmp.cleanup()

    def test_backup_unlink_failure_keeps_new_file_and_new_metadata(self):
        """复现口径：只读/占用备份注入 → 修前 PermissionError + db_sha≠disk_sha。"""
        pdf_a, pdf_b = _pdf("A"), _pdf("B")
        fulltext_mod.save_pdf(str(self.db), "P1", pdf_a, pdfs_dir=str(self.pdfs))

        real_unlink = Path.unlink

        def broken_unlink(self, *args, **kwargs):  # noqa: ARG001
            raise PermissionError(5, "injected unlink failure")

        Path.unlink = broken_unlink
        try:
            meta = fulltext_mod.save_pdf(
                str(self.db), "P1", pdf_b, pdfs_dir=str(self.pdfs))
        finally:
            Path.unlink = real_unlink

        on_disk = Path(meta.path).read_bytes()
        self.assertEqual(
            hashlib.sha256(on_disk).hexdigest(), meta.sha256,
            "DB 已提交新 sha256，磁盘却被恢复成旧文件（元数据与文件分叉）")
        self.assertEqual(on_disk, pdf_b)

    def test_commit_and_restore_failure_keeps_backup(self):
        """复现后半：文件已替换 + 提交失败 + 恢复失败 → 备份是唯一副本，不得删除。"""
        pdf_a, pdf_b = _pdf("A"), _pdf("B")
        fulltext_mod.save_pdf(
            str(self.db), "P1", pdf_a, pdfs_dir=str(self.pdfs))

        real_connect = fulltext_mod._connect
        real_replace = os.replace
        state = {"fail_commit": True}

        class _FailingCommit:
            """sqlite3.Connection 代理：__exit__ 在提交时抛错（模拟提交失败）。"""

            def __init__(self, inner):
                self._inner = inner

            def __getattr__(self, name):
                return getattr(self._inner, name)

            def __enter__(self):
                self._inner.__enter__()
                return self

            def __exit__(self, exc_type, exc, tb):
                if exc_type is None and state["fail_commit"]:
                    self._inner.rollback()
                    raise sqlite3.OperationalError("injected commit failure")
                return self._inner.__exit__(exc_type, exc, tb)

        def patched_connect(db_path):
            return _FailingCommit(real_connect(db_path))

        def failing_restore(src, dst):
            if ".backup" in Path(src).name:
                raise PermissionError(5, "injected restore failure")
            return real_replace(src, dst)

        fulltext_mod._connect = patched_connect
        os.replace = failing_restore
        try:
            with self.assertRaises(sqlite3.OperationalError):
                fulltext_mod.save_pdf(
                    str(self.db), "P1", pdf_b, pdfs_dir=str(self.pdfs))
        finally:
            fulltext_mod._connect = real_connect
            os.replace = real_replace

        backups = sorted(p for p in self.pdfs.iterdir() if ".backup" in p.name)
        self.assertTrue(
            backups,
            "文件已替换且恢复失败，备份却是唯一旧副本——不得删除")
        self.assertEqual(
            backups[0].read_bytes(), pdf_a,
            "残留备份不是旧文件内容（无法用于恢复）")

    def test_successful_save_leaves_no_backup_or_temp(self):
        """对照组：正常保存后目录只剩 PDF 本体（无 .backup/.tmp 残留）。"""
        fulltext_mod.save_pdf(
            str(self.db), "P1", _pdf("A"), pdfs_dir=str(self.pdfs))
        meta = fulltext_mod.save_pdf(
            str(self.db), "P1", _pdf("B"), pdfs_dir=str(self.pdfs))
        self.assertEqual([p.name for p in self.pdfs.iterdir()],
                         [Path(meta.path).name])


class Stage2QueueMatchesProgress(unittest.TestCase):
    """F4-53：复筛队列与复筛进度必须是同一文献集合。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rf_r9_l2b_f453_", dir=_temp_root(),
                                                ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.db = self.root / "stage2.db"
        db_mod.init_db(str(self.db))
        db_mod.upsert_articles(
            str(self.db),
            [Article(zotero_key="K1", title="Same title"),
             Article(zotero_key="K2", title="Same title"),
             Article(zotero_key="K3", title="Other")],
            [],
            title_review_groups=[{
                "normalized_title": "g:same",
                "members": [
                    {"zotero_key": "K1", "imported_in_batch": False},
                    {"zotero_key": "K2", "imported_in_batch": True},
                ],
            }],
        )
        for key in ("K1", "K2", "K3"):
            db_mod.save_decision(str(self.db), key, "include")

    def tearDown(self):
        self._tmp.cleanup()

    def _review_id(self) -> str:
        with closing(sqlite3.connect(str(self.db))) as conn:
            return conn.execute(
                "SELECT review_id FROM title_duplicate_reviews").fetchone()[0]

    def test_pending_candidate_not_in_own_queue(self):
        """复现口径：pending 时 ``初筛 total 2 vs 复筛 total 3``。"""
        stage1 = db_mod.get_progress(str(self.db))
        progress = fulltext_mod.get_stage2_progress(str(self.db))
        queue = fulltext_mod.stage2_queue(str(self.db), mode="own")
        self.assertEqual(stage1["total"], 2)  # 初筛本就排除 pending 候选
        self.assertEqual(progress["total"], stage1["total"])
        self.assertEqual(progress["total"], len(queue))
        self.assertEqual(queue, ["K1", "K3"])

    def test_confirmed_duplicate_not_in_own_queue(self):
        """复现口径：判 duplicate 后 ``own 队列仍含 K2``、进度已排除。"""
        self.assertTrue(db_mod.decide_title_duplicate_review(
            str(self.db), self._review_id(), "duplicate", keeper_key="K1"))
        progress = fulltext_mod.get_stage2_progress(str(self.db))
        queue = fulltext_mod.stage2_queue(str(self.db), mode="own")
        self.assertEqual(queue, ["K1", "K3"])
        self.assertEqual(progress["total"], len(queue))

    def test_not_duplicate_puts_member_back(self):
        self.assertTrue(db_mod.decide_title_duplicate_review(
            str(self.db), self._review_id(), "not_duplicate"))
        queue = fulltext_mod.stage2_queue(str(self.db), mode="own")
        self.assertEqual(queue, ["K1", "K2", "K3"])
        self.assertEqual(
            fulltext_mod.get_stage2_progress(str(self.db))["total"], len(queue))

    def test_excluded_decision_never_queued(self):
        """对照组：初筛 exclude 的文献照旧不进复筛队列（与修前一致）。"""
        db_mod.save_decision(str(self.db), "K3", "exclude")
        self.assertNotIn("K3", fulltext_mod.stage2_queue(str(self.db), mode="own"))

    def test_maybe_decision_not_queued(self):
        """2.1.5 W1 换钉：maybe 不再进复筛队列（旧钉为「Maybe 仍入队」）。"""
        db_mod.save_decision(str(self.db), "K3", "maybe")
        self.assertNotIn("K3", fulltext_mod.stage2_queue(str(self.db), mode="own"))
        # 进度 total 与队列同口径（仅 include）
        self.assertEqual(
            fulltext_mod.get_stage2_progress(str(self.db))["total"],
            len(fulltext_mod.stage2_queue(str(self.db), mode="own")))


class AddHighlightRectsConcurrency(unittest.TestCase):
    """F4-50：并发重复提交不得产生整批重复高亮行。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rf_r9_l2b_f450_", dir=_temp_root(),
                                                ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.db = _new_db(self.root, "hl.db", ["R1"])

    def tearDown(self):
        self._tmp.cleanup()

    @staticmethod
    def _events(tag: str) -> list[dict]:
        return [
            {"kind": "highlight", "page": 1, "text": f"{tag}-{i}", "color": "jade",
             "rects": [[1, 2, 3, 4]]}
            for i in range(400)
        ]

    def _rows(self, tag: str) -> int:
        with closing(sqlite3.connect(str(self.db))) as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM highlights WHERE zotero_key='R1' "
                "AND text LIKE ?", (f"{tag}-%",)).fetchone()[0]

    def test_two_threads_barrier_rounds_do_not_duplicate(self):
        """复现口径：Barrier 双线程 400 事件 8 轮，修前 3/8 轮 rows=800。"""
        errors: list[str] = []
        for rnd in range(8):
            tag = f"r{rnd}"
            barrier = threading.Barrier(2)

            def worker(tag=tag) -> None:
                try:
                    barrier.wait()
                    fulltext_mod.add_highlight_rects(str(self.db), "R1", self._events(tag))
                except BaseException as exc:  # noqa: BLE001
                    errors.append(f"{type(exc).__name__}: {exc}")

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(self._rows(tag), 400,
                             f"第 {rnd} 轮并发提交产生了重复高亮行")

        self.assertEqual(errors, [])

    def test_repeated_single_thread_submission_is_idempotent(self):
        """对照组：单线程重发同一批 400 事件仍只有 400 行。"""
        fulltext_mod.add_highlight_rects(str(self.db), "R1", self._events("s"))
        count = fulltext_mod.add_highlight_rects(str(self.db), "R1", self._events("s"))
        self.assertEqual(count, 0)
        self.assertEqual(self._rows("s"), 400)


class LoadPdfBytesBudget(unittest.TestCase):
    """F4-51：超上限的文件不得被整读进内存。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rf_r9_l2b_f451_", dir=_temp_root(),
                                                ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _meta(self, path: Path) -> "fulltext_mod.FulltextMeta":
        return fulltext_mod.FulltextMeta(
            zotero_key="B1", filename=path.name, path=str(path), sha256="0" * 64,
            n_pages=1, added_at="2026-01-01T00:00:00")

    def test_oversized_file_is_not_read(self):
        """复现口径：60MB 文件 / 1MB 上限 → 峰值 60MB（read_bytes 被整体调用）。"""
        big = self.root / "big.pdf"
        with open(big, "wb") as handle:
            handle.truncate(60 * 1024 * 1024)

        reads: list[str] = []
        real_read = Path.read_bytes

        def counting_read(self, *args, **kwargs):
            reads.append(str(self))
            return real_read(self, *args, **kwargs)

        Path.read_bytes = counting_read
        try:
            self.assertIsNone(fulltext_mod.load_pdf_bytes(
                self._meta(big), max_bytes=1024 * 1024))
        finally:
            Path.read_bytes = real_read
        self.assertEqual(reads, [], "超上限文件仍被整读进内存")

    def test_within_budget_returns_exact_bytes(self):
        """对照组：上限内的文件字节原样返回。"""
        data = b"%PDF-1.4 small payload"
        small = self.root / "small.pdf"
        small.write_bytes(data)
        self.assertEqual(fulltext_mod.load_pdf_bytes(
            self._meta(small), max_bytes=1024 * 1024), data)

    def test_missing_file_returns_none(self):
        """对照组：文件被删照旧返回 None（既有契约）。"""
        self.assertIsNone(fulltext_mod.load_pdf_bytes(
            self._meta(self.root / "gone.pdf")))


class HighlightEventValidation(unittest.TestCase):
    """F4-52：畸形事件按 docstring 契约抛 ValueError。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rf_r9_l2b_f452_", dir=_temp_root(),
                                                ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.db = _new_db(self.root, "hl.db", ["R1"])

    def tearDown(self):
        self._tmp.cleanup()

    def test_non_dict_event_raises_value_error(self):
        """复现口径：``events=['abc'] -> AttributeError``。"""
        with self.assertRaises(ValueError):
            fulltext_mod.add_highlight_rects(str(self.db), "R1", ["abc"])

    def test_non_list_rects_raises_value_error(self):
        """复现口径：``rects=5 -> TypeError``。"""
        with self.assertRaises(ValueError):
            fulltext_mod.add_highlight_rects(str(self.db), "R1", [
                {"kind": "highlight", "page": 1, "text": "x", "rects": 5}])

    def test_none_rects_still_accepted(self):
        """对照组：``rects=None`` 仍按空坐标处理（既有 ``or []`` 回退）。"""
        count = fulltext_mod.add_highlight_rects(str(self.db), "R1", [
            {"kind": "highlight", "page": 1, "text": "ok", "rects": None}])
        self.assertEqual(count, 1)

    def test_non_numeric_page_raises_value_error(self):
        with self.assertRaises(ValueError):
            fulltext_mod.add_highlight_rects(str(self.db), "R1", [
                {"kind": "highlight", "page": "abc", "text": "x"}])

    def test_existing_validation_messages_unchanged(self):
        """对照组：既有校验文案与拒绝行为不变。"""
        with self.assertRaisesRegex(ValueError, "非法高亮颜色"):
            fulltext_mod.add_highlight_rects(str(self.db), "R1", [
                {"kind": "highlight", "page": 1, "text": "x", "color": "blue"}])
        with self.assertRaisesRegex(ValueError, "高亮文本不能为空"):
            fulltext_mod.add_highlight_rects(str(self.db), "R1", [
                {"kind": "highlight", "page": 1, "text": "  "}])


if __name__ == "__main__":
    unittest.main()
