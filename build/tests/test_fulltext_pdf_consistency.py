"""r7 修复泳道「全文 PDF 一致性」钉子测试（MM-07 / MM3-02）。

MM-07（minimax_findings_verification.md §MM-07）
    ``_safe_pdf_stem`` 是删除式清洗，天然非单射：'AB:123' / 'AB123' /
    'AB 123' / 'AB?123' 全部清洗成 'AB123'，'$$$' 与 'pdf' 都成 'pdf'。
    不同文献键的 PDF 落盘同名互相覆盖——一篇文献的全文被另一篇静默顶替，
    库内两行 fulltexts 指向同一路径且无任何异常（交付物级别错误）。
    修复口径：**落盘名**改单射（截断清洗主干 + sha256(原始键) 短后缀）＋
    事务内占用守卫（目标路径已被其他键登记时拒绝而非覆盖）；**展示名**
    （fulltexts.filename 列、下载 Content-Disposition）保持
    ``{清洗主干}.pdf`` 不变，外部契约零变化。

MM3-02（minimax_findings_verification_3.md §MM3-02）
    ``save_pdf`` 非轨迹分支的 ``os.replace`` 在 DB 提交**之后**：替换失败
    （Windows 目标文件被占用等）时接口抛错，但 fulltexts 行已记新
    sha256/n_pages、磁盘仍是旧文件，元数据与文件永久不一致。修复口径：
    备份与替换统一移入写事务（与轨迹分支同口径），任一步失败 → 事务
    回滚，DB 与磁盘保持一致。

全部使用临时目录/临时库，不触碰真实数据。
"""
import hashlib
import sys
import tempfile
import unittest
import uuid
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen import db as articles_db, fulltext
from coscreen.db import _connect
from coscreen.models import Article


def _pdf(n_pages: int = 1, marker: str = "") -> bytes:
    """pypdf 生成真实可解析 PDF；marker 写入文档元数据以保证字节身份可区分。"""
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(n_pages):
        writer.add_blank_page(width=200, height=200)
    if marker:
        writer.add_metadata({"/Title": marker})
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


class _OsReplaceBroken:
    """os 模块代理：仅把 os.replace 换成必抛 PermissionError，其余转发真 os。

    模拟 MM3-02 复现场景——Windows 上目标 PDF 被其他进程占用时
    ``os.replace`` 抛 PermissionError。
    """

    def __init__(self, real_os):
        self._real = real_os

    def __getattr__(self, name):
        return getattr(self._real, name)

    @staticmethod
    def replace(src, dst):  # noqa: ARG004  签名对齐 os.replace
        raise PermissionError(13, "file is open by another process", str(dst))


class FulltextPdfConsistencyTests(unittest.TestCase):
    def setUp(self):
        self._fixture = tempfile.TemporaryDirectory(prefix="rf_r7_fulltext_")
        self.root = Path(self._fixture.name)
        self.db = self.root / "task" / "user.db"
        self.db.parent.mkdir(parents=True)
        self.pdfs = self.root / "task" / "pdfs"
        articles_db.init_db(self.db)
        articles_db.upsert_articles(
            self.db,
            [
                Article(zotero_key=k, title=f"Study {k}", doi=f"10.x/{i}")
                for i, k in enumerate(
                    ["K1", "AB:123", "AB123", "AB 123", "AB?123", "AB$123", "$$$", "pdf"]
                )
            ],
            [],
        )

    def tearDown(self):
        self._fixture.cleanup()

    # ------------------------------------------------------------------
    # MM-07 钉子 1：清洗后同名的不同键 → 落盘名必须互异，各键读回自己的字节
    # （破坏即红：回退单射落盘名后，5 个键共享 AB123.pdf、2 个键共享
    #   pdf.pdf，len(set(paths))<7 且多数键读到别人的字节）
    # ------------------------------------------------------------------
    def test_mm07_colliding_keys_get_distinct_files(self):
        keys = ["AB:123", "AB123", "AB 123", "AB?123", "AB$123", "$$$", "pdf"]
        # 全部键清洗主干后只剩 2 个不同值（'AB123' / 'pdf'）——非单射碰撞族
        self.assertEqual(
            {fulltext._safe_pdf_stem(k) for k in keys}, {"AB123", "pdf"}
        )
        blobs = {k: _pdf(1, f"MM07-{k}") for k in keys}
        for k in keys:
            fulltext.save_pdf(self.db, k, blobs[k], pdfs_dir=self.pdfs)

        metas = {k: fulltext.get_fulltext(self.db, k) for k in keys}
        # 不同输入 → 不同落盘名（单射）
        self.assertEqual(
            len({m.path for m in metas.values()}),
            len(keys),
            f"落盘名仍非单射: {[ (k, Path(m.path).name) for k, m in metas.items() ]}",
        )
        # 各键读回**自己的**字节（一篇全文不被另一篇顶替）
        for k in keys:
            self.assertIsNotNone(metas[k], k)
            self.assertEqual(
                fulltext.load_pdf_bytes(metas[k]),
                blobs[k],
                f"键 {k!r} 的全文被其他键顶替（读到 {Path(metas[k].path).name}）",
            )
        # 展示名契约不变（fulltexts.filename / 下载 Content-Disposition 沿用清洗主干）
        self.assertEqual(metas["AB:123"].filename, "AB123.pdf")
        self.assertEqual(metas["$$$"].filename, "pdf.pdf")

    # ------------------------------------------------------------------
    # MM-07 钉子 2：超长键（_KEY_RE 允许 1..300 字符）截断后仍可保存，
    # 且截断不破坏单射（两键仅尾部不同 → 落盘名仍互异）
    # （破坏即红：回退后 300 字符主干直接撑爆 mkstemp 前缀 → OSError）
    # ------------------------------------------------------------------
    def test_mm07_long_key_truncated_but_injective(self):
        key1 = "L" + "x" * 299  # 300 字符，_KEY_RE 上限
        key2 = "L" + "x" * 298 + "y"  # 仅尾部一字符不同（截断主干完全相同）
        articles_db.upsert_articles(
            self.db,
            [Article(zotero_key=key1, title="Long A", doi="10.x/la"),
             Article(zotero_key=key2, title="Long B", doi="10.x/lb")],
            [],
        )
        pdf1, pdf2 = _pdf(1, "LONG-A"), _pdf(2, "LONG-B")
        meta1 = fulltext.save_pdf(self.db, key1, pdf1, pdfs_dir=self.pdfs)
        meta2 = fulltext.save_pdf(self.db, key2, pdf2, pdfs_dir=self.pdfs)
        # 文件系统名长安全（Windows 255 上限，含临时文件前缀余量）
        self.assertLessEqual(len(Path(meta1.path).name), 128)
        # 截断主干相同 → 必须靠区分段保持互异
        self.assertNotEqual(Path(meta1.path).name, Path(meta2.path).name)
        self.assertEqual(
            fulltext.load_pdf_bytes(fulltext.get_fulltext(self.db, key1)), pdf1
        )
        self.assertEqual(
            fulltext.load_pdf_bytes(fulltext.get_fulltext(self.db, key2)), pdf2
        )

    # ------------------------------------------------------------------
    # MM-07 钉子 3：目标路径已被**其他键**登记时拒绝，绝不静默覆盖
    # （哈希截段碰撞 / 历史脏行的等价态；正常流程下单射命名不会触发）
    # （破坏即红：回退守卫后 save_pdf 静默覆盖，assertRaises 失败）
    # ------------------------------------------------------------------
    def test_mm07_refuses_overwrite_when_path_claimed_by_other_key(self):
        keeper = _pdf(2, "KEEPER")
        meta = fulltext.save_pdf(self.db, "AB123", keeper, pdfs_dir=self.pdfs)
        # 人为把另一键 'AB:123' 的 fulltexts 行指到同一落盘路径（占用态）
        with closing(_connect(self.db)) as conn, conn:
            conn.execute(
                "INSERT INTO fulltexts (zotero_key, filename, path, sha256, n_pages, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("AB:123", "occupied.pdf", meta.path, "0" * 64, 1, "2026-01-01T00:00:00"),
            )
        with self.assertRaises(ValueError) as caught:
            fulltext.save_pdf(self.db, "AB123", _pdf(1, "NEW"), pdfs_dir=self.pdfs)
        self.assertIn("AB:123", str(caught.exception))
        # 占用者毫发无损：行还在、文件字节未动
        row = fulltext.get_fulltext(self.db, "AB:123")
        self.assertIsNotNone(row)
        self.assertEqual(Path(meta.path).read_bytes(), keeper)

    # ------------------------------------------------------------------
    # MM3-02 钉子：非轨迹分支 os.replace 失败 → DB 与文件必须一致
    # （复现报告 mm3_02 场景：第一次保存成功后，替换被 PermissionError 打断）
    # （破坏即红：回退后 DB 已提交新 sha256/页数、磁盘仍旧文件）
    # ------------------------------------------------------------------
    def test_mm3_02_replace_failure_keeps_db_and_file_consistent(self):
        pdf_a, pdf_b = _pdf(1, "MM3-02-A"), _pdf(2, "MM3-02-B")
        meta1 = fulltext.save_pdf(self.db, "K1", pdf_a, pdfs_dir=self.pdfs)
        self.assertEqual(
            fulltext.load_pdf_bytes(fulltext.get_fulltext(self.db, "K1")), pdf_a
        )

        import os as real_os

        fulltext.os = _OsReplaceBroken(real_os)
        try:
            with self.assertRaises(PermissionError):
                fulltext.save_pdf(self.db, "K1", pdf_b, pdfs_dir=self.pdfs)
        finally:
            fulltext.os = real_os

        after = fulltext.get_fulltext(self.db, "K1")
        self.assertIsNotNone(after)
        # DB 停在第一次保存的状态（不得记入第二次上传的元数据）
        self.assertEqual(after.sha256, hashlib.sha256(pdf_a).hexdigest())
        self.assertEqual(after.n_pages, 1)
        # 磁盘同样停在第一次保存的状态
        self.assertEqual(fulltext.load_pdf_bytes(after), pdf_a)
        # staging/backup 临时文件清理干净，目录里只有最终 PDF
        self.assertEqual(
            sorted(p.name for p in self.pdfs.iterdir()),
            [Path(meta1.path).name],
        )

    # ------------------------------------------------------------------
    # 既有语义回归（非钉子）：同键重复保存 = 幂等更新（同路径、库与文件同步更新）
    # ------------------------------------------------------------------
    def test_same_key_resave_is_idempotent_update(self):
        pdf_a, pdf_b = _pdf(1, "IDEM-A"), _pdf(2, "IDEM-B")
        meta1 = fulltext.save_pdf(self.db, "K1", pdf_a, pdfs_dir=self.pdfs)
        meta2 = fulltext.save_pdf(self.db, "K1", pdf_b, pdfs_dir=self.pdfs)
        self.assertEqual(meta1.path, meta2.path)
        after = fulltext.get_fulltext(self.db, "K1")
        self.assertEqual(after.sha256, hashlib.sha256(pdf_b).hexdigest())
        self.assertEqual(after.n_pages, 2)
        self.assertEqual(fulltext.load_pdf_bytes(after), pdf_b)
        self.assertEqual(
            sorted(p.name for p in self.pdfs.iterdir()), [Path(meta2.path).name]
        )

    # ------------------------------------------------------------------
    # 既有语义回归（非钉子）：轨迹分支在两条分支统一到事务内替换后仍正常
    # （首存 + 覆盖更新 + backup/tmp 清理）
    # ------------------------------------------------------------------
    def test_traced_save_and_replace_still_work(self):
        from coscreen.research_trace import TraceContext

        ctx = lambda: TraceContext(  # noqa: E731  测试内便捷构造
            task_id="r7-fulltext", actor_id=7, source="manual",
            request_id=str(uuid.uuid4()),
        )
        pdf_a, pdf_b = _pdf(1, "TRACED-A"), _pdf(2, "TRACED-B")
        fulltext.save_pdf(self.db, "K1", pdf_a, pdfs_dir=self.pdfs, trace_context=ctx())
        meta2 = fulltext.save_pdf(
            self.db, "K1", pdf_b, pdfs_dir=self.pdfs, trace_context=ctx()
        )
        after = fulltext.get_fulltext(self.db, "K1")
        self.assertEqual(after.sha256, hashlib.sha256(pdf_b).hexdigest())
        self.assertEqual(fulltext.load_pdf_bytes(after), pdf_b)
        self.assertEqual(
            sorted(p.name for p in self.pdfs.iterdir()), [Path(meta2.path).name]
        )


if __name__ == "__main__":
    unittest.main()
