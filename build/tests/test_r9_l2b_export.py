"""r9 L2b 泳道钉子 —— 导出层（b05·F4-35/F4-38、b14·F4-36/F4-39）。

每条钉子按 `inventory_夜扫286确认分组.md` §3.2 的探针形态复现；把对应修复
回退，该用例必红：

- **F4-35【高】** ``export_coding_matrix`` 把维度名直接当列名：编码维度恰好叫
  ``title`` 时表头出现两个 ``title``（``['zotero_key','title','title']``），
  行字典后写者覆盖先写者、真实题名丢失（``['K1','5.2','5.2']``）。修法：与
  文献元数据列（zotero_key/title）同名的维度列加 ``" [维度]"`` 限定后缀，
  且保证加后缀后列名仍全局唯一。
- **F4-38【高】** 待人工复核的标题候选组成员（``imported_in_batch=1`` 且组
  状态 pending）经 ``list_articles`` 的缺省过滤被连带排除，已编码的文献在
  matrix / long / counts 三路导出里静默消失（``counts studies 1→0``）。修法：
  行集合只排除**已确认**重复（``is_duplicate_of`` 非空）。
- **F4-36** ``_notes_index`` 回退分支裸 ``sqlite3.connect`` 对缺失路径建出空库
  （``exists=True size=0``），损坏库的读取错误被折叠成「没有备注」。修法：
  缺失路径直接返回空索引、只读 URI 打开、损坏库抛中文 ValueError。
- **F4-39** 高亮 Markdown 只把 ``\\n`` 换成空格，CRLF 文本残留裸 ``\\r``
  （``'alpha\\r\\nbeta' -> b'alpha\\r beta\\r\\n'``），多行备注撕裂列表结构。

全部使用临时目录/临时库（``REVIEWFLOW_TEST_TMP`` 可把根目录指到非 C 盘），
不触碰真实数据。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen import coding as coding_mod
from coscreen import db as db_mod
from coscreen import export as export_mod
from coscreen import fulltext as fulltext_mod
from coscreen.models import Article


def _temp_root() -> str:
    base = os.environ.get("REVIEWFLOW_TEST_TMP")
    if base:
        Path(base).mkdir(parents=True, exist_ok=True)
        return base
    return tempfile.gettempdir()


def _new_db(root: Path, name: str) -> Path:
    db = root / name
    db_mod.init_db(str(db))
    return db


def _pending_group(*keys: str) -> list[dict]:
    """与复审探针同形的待复核候选组：首个为库内旧行，其余为本批新行。"""
    return [{
        "normalized_title": "g:" + "-".join(keys),
        "members": [
            {"zotero_key": key, "imported_in_batch": i > 0}
            for i, key in enumerate(keys)
        ],
    }]


class CodingMatrixTitleCollision(unittest.TestCase):
    """F4-35：维度名不得覆盖 title / zotero_key 列。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rf_r9_l2b_f435_", dir=_temp_root(),
                                              ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_dimension_named_title_does_not_duplicate_header(self):
        db = _new_db(self.root, "collide.db")
        db_mod.upsert_articles(
            str(db), [Article(zotero_key="K1", title="RealTitle")], [])
        dim = coding_mod.add_dimension(str(db), "title", "text", [])
        coding_mod.save_coding_value(str(db), dim, "K1", "5.2")

        out = self.root / "matrix.csv"
        export_mod.export_coding_matrix(str(db), str(out))
        lines = out.read_text(encoding="utf-8-sig").splitlines()
        header = lines[0].split(",")
        row = lines[1].split(",")

        # 复现口径：header ['zotero_key','title','title'] / 行 ['K1','5.2','5.2']
        self.assertEqual(len(header), len(set(header)), f"表头仍有重复列: {header}")
        self.assertEqual(header[:2], ["zotero_key", "title"])
        self.assertIn("title [维度]", header, f"冲突维度列未加限定后缀: {header}")
        # 真实题名必须留在 title 列，维度值落在后缀列
        self.assertEqual(row[header.index("title")], "RealTitle")
        self.assertEqual(row[header.index("title [维度]")], "5.2")

    def test_dimension_named_zotero_key_is_disambiguated(self):
        db = _new_db(self.root, "keystore.db")
        db_mod.upsert_articles(
            str(db), [Article(zotero_key="K1", title="RealTitle")], [])
        dim = coding_mod.add_dimension(str(db), "zotero_key", "text", [])
        coding_mod.save_coding_value(str(db), dim, "K1", "ZZ")

        out = self.root / "matrix2.csv"
        export_mod.export_coding_matrix(str(db), str(out))
        header = out.read_text(encoding="utf-8-sig").splitlines()[0].split(",")
        self.assertEqual(header, ["zotero_key", "title", "zotero_key [维度]"])

    def test_collision_suffix_itself_colliding_stays_unique(self):
        db = _new_db(self.root, "keystore2.db")
        db_mod.upsert_articles(
            str(db), [Article(zotero_key="K1", title="RealTitle")], [])
        coding_mod.add_dimension(str(db), "title", "text", [])
        coding_mod.add_dimension(str(db), "title [维度]", "text", [])

        out = self.root / "matrix3.csv"
        export_mod.export_coding_matrix(str(db), str(out))
        header = out.read_text(encoding="utf-8-sig").splitlines()[0].split(",")
        self.assertEqual(len(header), len(set(header)), f"表头仍有重复列: {header}")

    def test_non_conflicting_dimension_header_unchanged(self):
        """对照组：普通维度名 -> 表头/行逐字节与修前契约一致。"""
        db = _new_db(self.root, "plain.db")
        db_mod.upsert_articles(
            str(db), [Article(zotero_key="K1", title="RealTitle")], [])
        dim = coding_mod.add_dimension(str(db), "Design", "text", [])
        coding_mod.save_coding_value(str(db), dim, "K1", "RCT")

        out = self.root / "plain.csv"
        export_mod.export_coding_matrix(str(db), str(out))
        text = out.read_text(encoding="utf-8-sig")
        # 只钉列集与取值：pandas.to_csv 的行尾随平台/版本变化，不属于本钉子
        # 的断言对象（修前修后同一表达式产出同一文本）。
        self.assertEqual(text.splitlines(), [
            "zotero_key,title,Design", "K1,RealTitle,RCT"])


class PendingCandidateNotDroppedFromCodingExport(unittest.TestCase):
    """F4-38：pending 候选组成员已编码的数据不得在三路导出中静默消失。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rf_r9_l2b_f438_", dir=_temp_root(),
                                              ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.db = _new_db(self.root, "pending.db")
        db_mod.upsert_articles(
            str(self.db),
            [Article(zotero_key="D1", title="Dup title"),
             Article(zotero_key="D2", title="Dup title"),
             Article(zotero_key="D3", title="Other")],
            [],
            title_review_groups=_pending_group("D1", "D2"),
        )
        self.dim = coding_mod.add_dimension(str(self.db), "Design", "text", [])
        for key in ("D1", "D2", "D3"):
            coding_mod.save_coding_value(str(self.db), self.dim, key, f"v-{key}")

    def tearDown(self):
        self._tmp.cleanup()

    def _matrix_keys(self) -> list[str]:
        out = self.root / "matrix.csv"
        export_mod.export_coding_matrix(str(self.db), str(out))
        return [line.split(",")[0]
                for line in out.read_text(encoding="utf-8-sig").splitlines()[1:]]

    def _long_keys(self) -> list[str]:
        out = self.root / "long.csv"
        export_mod.export_coding_long(str(self.db), str(out))
        return [line.split(",")[0]
                for line in out.read_text(encoding="utf-8-sig").splitlines()[1:]]

    def test_pending_member_keeps_all_three_exports(self):
        expected = ["D1", "D2", "D3"]
        self.assertEqual(self._matrix_keys(), expected)
        self.assertEqual(self._long_keys(), expected)
        self.assertEqual(
            export_mod.coding_export_counts(str(self.db))["studies"], 3,
            "pending 期 counts studies 掉到 1（复现口径 1→0 的三行版）")

    def test_confirmed_duplicate_still_excluded(self):
        with sqlite3.connect(str(self.db)) as conn:
            review_id = conn.execute(
                "SELECT review_id FROM title_duplicate_reviews").fetchone()[0]
        self.assertTrue(db_mod.decide_title_duplicate_review(
            str(self.db), review_id, "duplicate", keeper_key="D1"))
        self.assertEqual(self._matrix_keys(), ["D1", "D3"])
        self.assertEqual(
            export_mod.coding_export_counts(str(self.db))["studies"], 2)

    def test_not_duplicate_restores_member(self):
        with sqlite3.connect(str(self.db)) as conn:
            review_id = conn.execute(
                "SELECT review_id FROM title_duplicate_reviews").fetchone()[0]
        self.assertTrue(db_mod.decide_title_duplicate_review(
            str(self.db), review_id, "not_duplicate"))
        self.assertEqual(self._matrix_keys(), ["D1", "D2", "D3"])


class NotesIndexReadOnlyContract(unittest.TestCase):
    """F4-36：读备注不得建库；损坏库不得折叠成「没有备注」。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rf_r9_l2b_f436_", dir=_temp_root(),
                                              ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_path_is_not_created(self):
        missing = self.root / "absent" / "nodb.db"
        missing.parent.mkdir(parents=True)
        self.assertEqual(export_mod._notes_index(str(missing)), {})
        self.assertFalse(missing.exists(), "只读备注读取建出了空库文件")

    def test_corrupt_database_raises_value_error(self):
        corrupt = self.root / "corrupt.db"
        corrupt.write_bytes(b"not a sqlite database at all")
        with self.assertRaisesRegex(ValueError, "编码备注无法读取"):
            export_mod._notes_index(str(corrupt))

    def test_healthy_database_returns_notes_and_is_untouched(self):
        """对照组：正常库读回备注且文件字节不变（只读不写）。"""
        db = _new_db(self.root, "healthy.db")
        db_mod.upsert_articles(str(db), [Article(zotero_key="K1", title="T")], [])
        dim = coding_mod.add_dimension(str(db), "Design", "text", [])
        coding_mod.save_coding_note(str(db), dim, "K1", "关键备注")
        before = db.read_bytes()
        index = export_mod._notes_index(str(db))
        self.assertEqual(index, {(dim, "K1"): {"id": 1, "text": "关键备注"}})
        self.assertEqual(db.read_bytes(), before, "读备注改动了库文件")


class HighlightMarkdownNewlineFold(unittest.TestCase):
    """F4-39：Markdown 导出不得残留裸 \\r。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rf_r9_l2b_f439_", dir=_temp_root(),
                                              ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.db = _new_db(self.root, "hl.db")
        db_mod.upsert_articles(str(self.db), [Article(zotero_key="H1", title="T")], [])

    def tearDown(self):
        self._tmp.cleanup()

    def _export(self) -> bytes:
        out = self.root / "notes.md"
        export_mod.export_highlights_markdown(str(self.db), str(out))
        return out.read_bytes()

    def test_crlf_text_and_note_lose_bare_carriage_return(self):
        fulltext_mod.add_highlight(
            str(self.db), "H1", 3, "alpha\r\nbeta", "jade", note="line1\r\nline2")
        body = self._export().decode("utf-8")
        self.assertIn("- 【玉青】alpha beta", body)
        self.assertIn("  - 笔记：line1 line2", body)
        self.assertNotIn("\r\r", body)  # 修前：alpha\r beta\r\n + 笔记行尾双 \r
        self.assertNotRegex(body, r"\r(?!\n)")  # 任何位置都不得有裸 \r

    def test_lf_only_text_and_note_are_folded_to_single_line(self):
        """LF 文本：高亮选段与修前逐字一致，笔记内换行也压成一行。"""
        fulltext_mod.add_highlight(
            str(self.db), "H1", 3, "alpha\nbeta", "jade", note="line1\nline2")
        body = self._export().decode("utf-8")
        # 高亮选段：修前即把 \n 换成空格，本条为逐字对照（不因修复而变）
        self.assertIn("- 【玉青】alpha beta", body)
        # 笔记：修前原样写出 \n，列表项被撕裂成两行；修复后同样折叠
        self.assertIn("  - 笔记：line1 line2", body)
        self.assertNotRegex(body, r"\r(?!\n)")

    def test_plain_single_line_content_unchanged(self):
        """对照组：无换行内容导出文本与修前逐字节相同。"""
        fulltext_mod.add_highlight(
            str(self.db), "H1", 3, "alpha", "jade", note="single note")
        out = self.root / "plain.md"
        export_mod.export_highlights_markdown(str(self.db), str(out))
        self.assertEqual(out.read_text(encoding="utf-8"), (
            "# 高亮笔记（全文复筛标注）\n"
            "\n"
            "- 文献数：1；高亮数：1\n"
            "\n"
            "## T\n"
            "\n"
            "- zotero_key：`H1`\n"
            "\n"
            "### 第 3 页\n"
            "\n"
            "- 【玉青】alpha\n"
            "  - 笔记：single note\n"
        ))


if __name__ == "__main__":
    unittest.main()
