"""终审 #62：shared_corpus 三段手写 SQL 字面量与常量枚举的一致性钉。

waveA 把 v2.1.4「由 ``_ARTICLE_COLUMNS`` / ``METADATA_FIELDS`` 常量推导」的
shared_corpus SQL 改成三段硬编码字面量（SELECT 14 列 / INSERT 14 列 + ON
CONFLICT / metadata UPDATE 8 字段）：「结构上不可能漂移」的保证消失了——
常量增删列时 SELECT/INSERT 会静默漏同步该列（成员库与属主库数据不一致），
metadata UPDATE 占位符错位要到运行时同步路径才抛绑定异常并回滚整次目录
同步（探针复现见 u7-glm-c3）。本文件钉三层：

* 静态钉：AST 抽取三段字面量，断言列清单 / 占位符数 / ON CONFLICT 覆盖与
  ``shared_corpus._ARTICLE_COLUMNS``、``review_imports.METADATA_FIELDS``
  逐项一致（顺序也一致——SELECT 的行直接喂 INSERT）；
* 同步链路钉：两库建表 → 属主库写入全列非空行 → sync → 成员库逐列一致、
  二次 sync 幂等；
* 变异钉：运行期给常量加一列（模拟「改常量忘了改字面量」的漂移），静态钉
  与链路钉都必须红——证明钉子承重。
"""
from __future__ import annotations

import ast
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen import db as db_mod
from coscreen import review_imports as import_store
from coscreen import tasks as tasks_mod
from custom_backend import shared_corpus


def _literal_statements() -> dict[str, str]:
    """AST 抽取 shared_corpus 源码中三段目标 SQL 常量字面量。"""
    source = Path(shared_corpus.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("execute", "executemany") and node.args):
            continue
        arg = node.args[0]
        if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
            continue
        text = " ".join(arg.value.split())
        if " FROM articles ORDER BY" in text and text.startswith("SELECT "):
            found["select"] = text
        elif text.startswith("INSERT INTO articles ("):
            found["insert"] = text
        elif text.startswith("UPDATE articles SET "):
            found["update"] = text
    return found


def _assert_literals_in_sync() -> None:
    """静态一致性检查（静态钉与变异钉共用；断言失败即钉红）。"""
    literals = _literal_statements()
    assert set(literals) == {"select", "insert", "update"}, sorted(literals)

    expected = list(shared_corpus._ARTICLE_COLUMNS)

    select_cols = [c.strip() for c in literals["select"].split(
            "SELECT ", 1)[1].split(" FROM articles", 1)[0].split(",")]
    assert select_cols == expected, ("SELECT 列漂移", select_cols, expected)

    insert_head, _, insert_tail = literals["insert"].partition(") VALUES ")
    insert_cols = [c.strip() for c in insert_head.split("(", 1)[1].split(",")]
    assert insert_cols == expected, ("INSERT 列漂移", insert_cols, expected)
    values_clause = insert_tail.split(" ON CONFLICT", 1)[0]
    placeholders = values_clause.count("?")
    assert placeholders == len(expected), ("INSERT 占位符数漂移", placeholders)

    conflict = literals["insert"].split("DO UPDATE SET ", 1)[1]
    set_cols = {name for name in expected
                if f"{name}=CASE WHEN articles.{name} IS NULL" in conflict}
    direct = {name for name in expected if f"{name}=excluded.{name}" in conflict}
    assert direct | set_cols == set(expected) - {"zotero_key"}, (
        "ON CONFLICT SET 覆盖漂移", sorted(direct | set_cols))

    update_head = literals["update"].split("UPDATE articles SET ", 1)[1].split(
        " WHERE ", 1)[0]
    update_cols = [pair.partition("=?")[0].strip() for pair in update_head.split(",")]
    assert update_cols == list(import_store.METADATA_FIELDS), (
        "metadata UPDATE 字段漂移", update_cols)
    assert literals["update"].count("=?") == len(import_store.METADATA_FIELDS) + 1, (
        "metadata UPDATE 占位符数漂移", literals["update"].count("=?"))


class SharedCorpusLiteralPins(unittest.TestCase):
    """三段字面量 ↔ 常量枚举的静态一致性。"""

    def test_literals_match_derived_column_constants(self) -> None:
        _assert_literals_in_sync()  # 任一断言失败即红

    def test_mutation_constant_drift_pin_goes_red(self) -> None:
        """破坏性变异：常量加列、字面量不动（waveA 之前的漂移形态）→
        静态钉必须红。"""
        drifted = tuple(shared_corpus._ARTICLE_COLUMNS) + ("extra_col",)
        with mock.patch.object(shared_corpus, "_ARTICLE_COLUMNS", drifted):
            with self.assertRaises(AssertionError):
                _assert_literals_in_sync()


class SharedCorpusSyncChainPins(unittest.TestCase):
    """同步链路：属主 14 列行 → 成员库逐列一致；二次 sync 幂等。"""

    OWNER = "ownerpin62"
    MEMBER = "memberpin62"

    def _owner_row(self) -> tuple:
        # 每列不同值（列交换/漏列即行值错位可检）；数值列给 int。
        return ("KEY62", "journal-article", "t-62", "a-62", "j-62", 2062,
                "10.62/x", "abs-62", "https://x/62", "ris", '{"k": 62}',
                "hash62", None, 62)

    def test_sync_copies_every_column_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rf_sec10_corpus_") as tmp:
            owner_db = Path(tmp) / f"{tasks_mod.slugify_screener(self.OWNER)}.db"
            db_mod.init_db(owner_db)
            import_store.migrate(owner_db)
            columns = list(shared_corpus._ARTICLE_COLUMNS)
            with closing(sqlite3.connect(owner_db)) as conn:
                marks = ",".join("?" for _ in columns)
                conn.execute(
                    f"INSERT INTO articles ({','.join(columns)}) VALUES ({marks})",
                    self._owner_row())
                conn.commit()
            with closing(sqlite3.connect(owner_db)) as conn:
                owner_row = conn.execute(
                    f"SELECT {','.join(columns)} FROM articles").fetchone()

            synced = shared_corpus.sync_shared_articles(
                tmp, owner_username=self.OWNER, member_username=self.MEMBER,
                catalog_revision=1)
            self.assertEqual(synced, 1)

            member_db = Path(tmp) / f"{tasks_mod.slugify_screener(self.MEMBER)}.db"
            with closing(sqlite3.connect(member_db)) as conn:
                member_row = conn.execute(
                    f"SELECT {','.join(columns)} FROM articles").fetchone()
            self.assertEqual(member_row, owner_row,
                             "成员库必须逐列复制属主行（列清单漂移即红）")

            again = shared_corpus.sync_shared_articles(
                tmp, owner_username=self.OWNER, member_username=self.MEMBER,
                catalog_revision=1)
            self.assertEqual(again, 0, "catalog_revision 相同的二次 sync 必须零操作")

    def test_mutation_constant_drift_breaks_sync_pin_goes_red(self) -> None:
        """破坏性变异：常量加列 → 属主/成员两侧的「列齐全」闸与 SELECT/INSERT
        字面量失配，sync 必须失败（而不是静默漏列）——链路钉承重。"""
        drifted = tuple(shared_corpus._ARTICLE_COLUMNS) + ("extra_col",)
        with tempfile.TemporaryDirectory(prefix="rf_sec10_corpus_mut_") as tmp:
            owner_db = Path(tmp) / f"{tasks_mod.slugify_screener(self.OWNER)}.db"
            db_mod.init_db(owner_db)
            import_store.migrate(owner_db)
            with mock.patch.object(shared_corpus, "_ARTICLE_COLUMNS", drifted):
                with self.assertRaises(ValueError):
                    shared_corpus.sync_shared_articles(
                        tmp, owner_username=self.OWNER, member_username=self.MEMBER)


if __name__ == "__main__":
    unittest.main()
