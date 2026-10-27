"""r6 复审修复回归测试 —— 去重与键回归泳道（分诊确认 still_open 六项）。

对应 r5 执行报告 ④ 节终态复审新确认条目（去重引擎单元 #1/#2/#3/#4/#5/#7/
#8/#9/#10）与 r6 分派 dedup-engine.md 的 R6-D1/R6-D2（方案 2）：

- R6-D1（#1/#7）：键相等且双方 DOI 为空时，"标题 token_set_ratio>=90 即自动
  合并"删除——改为标题规范化后完全相等且首作者兼容才合并；一词之差
  （women/men、phase2/phase3、retraction 前缀、词序反转、renal/hepatic）
  一律 exact_key_conflict 候选，两条都进筛选、DB 两行共存、无覆盖；
- R6-D2（#8/#15，方案 2）：auto-* 键公式变更不再断裂既有库重导幂等——
  无 Key 记录入库前按（规范化标题+首作者姓氏+年份）在库内既有行匹配
  （不看键形），命中沿用库内键走 UPDATE；验收 = 2.1.3 公式预置库重导
  同一 RIS/CSV 断言零新增行（铁律 R1 标准用例）；
- 复审 #2/#9（stabilize 成员覆盖）：精确组保留键全集进占用集，被 >1 组
  使用的键加确定性后缀（绝不改名为组键）；pairs 刷新剔除自指配对；
  KA/SHARED×d1/d2 与"库内 K2/K3 + 三条同 DOI"两个 4/5 条 RIS 复现；
- 复审 #3（CLI 库内去重缺失）：known 纳入 DOI/键匹配（与 living_review
  对齐），跨批次同 DOI 走 duplicate 标记不重复插入；
- 复审 #4/#10（模糊候选 n² 内存）：候选边并查集生成树去重（每连通分量
  至多 n-1 条）+ 总量上限 50000 对（超出截断 + 显式警告）+ n=2000 回归
  （时长与内存断言放宽阈值）；
- 复审 #5（living_review 口径）：duplicates_removed 只按 is_duplicate_of
  标记计，待审候选归 pending_candidates，与 prisma_task_metrics 统一。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
import tempfile
import time
import tracemalloc
import unittest
from contextlib import closing, redirect_stdout, redirect_stderr
from io import StringIO
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen import db as db_mod
from coscreen import dedup as dedup_mod
from coscreen.dedup import find_duplicates, stabilize_conflicting_keys
from coscreen.living_review import process_incremental_articles, process_incremental_update
from coscreen.models import Article
from coscreen.parsers import parse_file


def _article(key: str, title: str, authors: str = "", doi: str = "",
             year: int | None = None, raw: dict | None = None) -> Article:
    return Article(zotero_key=key, title=title, authors=authors, doi=doi,
                   year=year, raw=raw if raw is not None else {})


def _write_ris(path: Path, records: list[dict]) -> None:
    """写一个最小 RIS 文件：records 每项为 {au,ti,py,do,id} 的可选键。"""
    lines: list[str] = []
    for rec in records:
        lines.append("TY  - JOUR")
        for tag in ("au", "ti", "py", "do", "id"):
            if rec.get(tag):
                lines.append(f"{tag.upper()}  - {rec[tag]}")
        lines.append("ER  -\n")
    path.write_text("\n".join(lines), encoding="utf-8")


def _run_cli(args: list[str]):
    """同进程直调 CLI 主入口（与 test_dedup_u1_regressions 同一手法）。"""
    import subprocess

    from coscreen import cli as cli_module

    buf_out, buf_err = StringIO(), StringIO()
    with redirect_stdout(buf_out), redirect_stderr(buf_err):
        try:
            code = cli_module.main(list(args))
        except SystemExit as exc:
            code = int(exc.code or 0)
    return subprocess.CompletedProcess(args, int(code), buf_out.getvalue(), buf_err.getvalue())


def _legacy_auto_key(doi: str, title: str) -> str:
    """2.1.3 及更早的生成键公式：``auto- + sha1(f"{doi}|{title}")[:12]``。

    doi/title 为解析器规范化后的存储值——与 2.1.3 代码从同一批值计算
    content_hash 再截断的口径一致（git show 501e1f3:coscreen/normalize.py
    build_zotero_key / models.py compute_content_hash 核对）。
    """
    return "auto-" + hashlib.sha1(f"{doi}|{title}".encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# R6-D1（复审 #1/#7）：键相等 + 双方 DOI 空 → 标题完全相等且首作者兼容
# ---------------------------------------------------------------------------

class KeyMergeStrictTests(unittest.TestCase):
    """一词之差不再凭 token_set_ratio>=90 自动合并，改报键冲突候选。"""

    CONFLICT_TITLE_PAIRS = [
        # (复审实测 token_set_ratio)
        ("Effects of drug X on mortality in women",
         "Effects of drug X on mortality in men"),              # 98.41
        ("A phase 2 trial of drug X for diabetes",
         "A phase 3 trial of drug X for diabetes"),             # 93.18
        ("Retraction: Effects of drug X on mortality",
         "Effects of drug X on mortality"),                     # 100（前缀）
        ("Impact of diabetes on blood pressure control",
         "Impact of blood pressure control on diabetes"),       # 100（词序反转）
        ("Metformin and renal outcomes in patients with type 2 diabetes",
         "Metformin and hepatic outcomes in patients with type 2 diabetes"),
    ]

    def test_one_word_title_difference_same_key_becomes_conflict(self):
        for title_a, title_b in self.CONFLICT_TITLE_PAIRS:
            with self.subTest(titles=(title_a, title_b)):
                arts = [
                    _article("WM1", title_a, "Wang, Wei", year=2005),
                    _article("WM1", title_b, "Wang, Wei", year=2006),
                ]
                kept, report = find_duplicates(arts)
                self.assertEqual(len(kept), 2)            # 两条都保留
                self.assertEqual(report.exact_pairs, [])  # 无自动合并
                self.assertEqual(len(report.key_conflict_pairs), 1)
                self.assertEqual(report.key_conflict_pairs[0].method,
                                 "exact_key_conflict")

    def test_same_title_different_first_author_conflicts(self):
        # 标题完全相等但首作者不同（同键无 DOI）→ 冲突候选，不合并。
        arts = [
            _article("SHARE", "Editorial", "Smith, John", year=2020),
            _article("SHARE", "Editorial", "Jones, Anna", year=2021),
        ]
        kept, report = find_duplicates(arts)
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(report.key_conflict_pairs), 1)

    def test_same_title_one_side_author_missing_conflicts(self):
        # 缺作者 ≠ 作者相同（与模糊层作者门同一口径）：单侧缺作者不合并。
        arts = [
            _article("SHARE", "Same Title Here", "Smith, John"),
            _article("SHARE", "Same Title Here", ""),
        ]
        kept, report = find_duplicates(arts)
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(report.key_conflict_pairs), 1)

    def test_identical_records_still_auto_merge(self):
        # 全同记录（标题完全相等 + 首作者兼容）仍自动合并——重导幂等保留。
        for authors in ("Smith, John", ""):
            with self.subTest(authors=authors):
                arts = [
                    _article("K1", "Alpha beta gamma study", authors),
                    _article("K1", "Alpha beta gamma study", authors),
                ]
                kept, report = find_duplicates(arts)
                self.assertEqual(len(kept), 1)
                self.assertEqual(report.key_conflict_pairs, [])

    def test_case_and_whitespace_variants_of_same_title_merge(self):
        # 同一标题的大小写/空白变体仍是"完全相等"（比较形式小写+空白折叠）。
        arts = [
            _article("K1", "Effects of Drug  X on Mortality", "Wang, Wei"),
            _article("K1", "effects of drug x on mortality", "Wei Wang"),
        ]
        kept, report = find_duplicates(arts)
        self.assertEqual(len(kept), 1)  # 姓氏同为 wang，兼容 → 合并
        self.assertEqual(report.key_conflict_pairs, [])

    def test_cli_women_men_same_key_both_survive_without_overwrite(self):
        # 复审 #7 实测复现：两条 ID - WM1 的 women(2005)/men(2006) 无 DOI
        # 曾"精确重复 1"静默覆盖只剩 men 行；现键冲突候选 + 两行共存。
        with tempfile.TemporaryDirectory(prefix="rf_r6d1_") as tmp:
            ris = Path(tmp) / "wm.ris"
            db = str(Path(tmp) / "wm.db")
            _write_ris(ris, [
                {"au": "Wang, Wei", "ti": "Effects of drug X on mortality in women", "py": "2005", "id": "WM1"},
                {"au": "Wang, Wei", "ti": "Effects of drug X on mortality in men", "py": "2006", "id": "WM1"},
            ])
            proc = _run_cli(["import", "--file", str(ris), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("键冲突候选 1 条", proc.stdout)
            self.assertIn("键消歧 1 处", proc.stdout)
            self.assertIn("进入筛选 2 条", proc.stdout)
            with closing(sqlite3.connect(db)) as conn:
                rows = conn.execute(
                    "SELECT title, is_duplicate_of FROM articles ORDER BY import_order"
                ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertIn("women", rows[0][0])
            self.assertIn("men", rows[1][0])
            self.assertEqual([r[1] for r in rows], [None, None])  # 无覆盖/无标记

# ---------------------------------------------------------------------------
# 复审 #2/#9：stabilize 成员覆盖（保留键全集 + 后缀消歧 + 自指禁）
# ---------------------------------------------------------------------------

class StabilizeMemberOverwriteTests(unittest.TestCase):
    def test_two_exact_groups_shared_member_key_all_rows_survive(self):
        # 复审 #2 实测复现（KA/SHARED×d1、KC/SHARED×d2）：SHARED 行内容曾由
        # Alpha 被静默覆盖成 Gamma、Alpha 重复行消失（DB 只剩 3 行）。
        with tempfile.TemporaryDirectory(prefix="rf_r6ov1_") as tmp:
            ris = Path(tmp) / "shared.ris"
            db = str(Path(tmp) / "shared.db")
            _write_ris(ris, [
                {"au": "Alpha, Ann", "ti": "Unrelated Paper Alpha", "do": "10.9/d1", "id": "KA"},
                {"au": "Alpha, Ann", "ti": "Shared Study One", "do": "10.9/d1", "id": "SHARED"},
                {"au": "Gamma, Gina", "ti": "Unrelated Paper Gamma", "do": "10.9/d2", "id": "KC"},
                {"au": "Gamma, Gina", "ti": "Shared Study Two", "do": "10.9/d2", "id": "SHARED"},
            ])
            proc = _run_cli(["import", "--file", str(ris), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("重复标记 2 条", proc.stdout)
            self.assertIn("键消歧 1 处", proc.stdout)
            with closing(sqlite3.connect(db)) as conn:
                rows = dict(conn.execute(
                    "SELECT zotero_key, title FROM articles").fetchall())
                dup_of = dict(conn.execute(
                    "SELECT zotero_key, is_duplicate_of FROM articles").fetchall())
            self.assertEqual(len(rows), 4)               # 4 行全部共存
            shared_keys = [k for k in rows if k.startswith("SHARED")]
            self.assertEqual(len(shared_keys), 2)        # 一个原键 + 一个后缀键
            suffixed = next(k for k in shared_keys if k != "SHARED")
            self.assertTrue(suffixed.startswith("SHARED-"))
            self.assertEqual(rows["SHARED"], "Shared Study One")   # Alpha 内容未被覆盖
            self.assertEqual(rows[suffixed], "Shared Study Two")   # Gamma 内容保留
            self.assertEqual(dup_of["SHARED"], "KA")
            self.assertEqual(dup_of[suffixed], "KC")

    def test_members_renamed_to_own_suffix_not_group_key(self):
        # 复审 #9 实测复现：库含 K2/K3 无关文献，导入 K1/K2/K3 三条同 DOI
        # 曾被统一改名为组键 K1 → 互相覆盖只剩 ('K1','Title Three')，组根
        # Title One/Two 全丢，pairs 自指 ('K1','K1') 致重复标记被跳过。
        with tempfile.TemporaryDirectory(prefix="rf_r6ov2_") as tmp:
            seed = Path(tmp) / "seed.ris"
            batch = Path(tmp) / "batch.ris"
            db = str(Path(tmp) / "grp.db")
            _write_ris(seed, [
                {"au": "Doe, Jane", "ti": "Unrelated Paper Alpha", "id": "K2"},
                {"au": "Ray, Ruth", "ti": "Unrelated Paper Beta", "id": "K3"},
            ])
            _write_ris(batch, [
                {"au": "Smith, Ann", "ti": "Title One", "do": "10.1/d1", "id": "K1"},
                {"au": "Jones, Jake", "ti": "Title Two", "do": "10.1/d1", "id": "K2"},
                {"au": "Kim, Kai", "ti": "Title Three", "do": "10.1/d1", "id": "K3"},
            ])
            self.assertEqual(_run_cli(["import", "--file", str(seed), "--db", db]).returncode, 0)
            proc = _run_cli(["import", "--file", str(batch), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("键消歧 2 处", proc.stdout)
            self.assertIn("重复标记 2 条", proc.stdout)
            with closing(sqlite3.connect(db)) as conn:
                rows = dict(conn.execute(
                    "SELECT zotero_key, title FROM articles").fetchall())
                dup_of = dict(conn.execute(
                    "SELECT zotero_key, is_duplicate_of FROM articles").fetchall())
            self.assertEqual(len(rows), 5)               # 库内 2 + 本批 3，零丢失
            self.assertEqual(rows["K1"], "Title One")    # 组根内容未被成员覆盖
            self.assertEqual(rows["K2"], "Unrelated Paper Alpha")  # 库内原行未被触碰
            self.assertEqual(rows["K3"], "Unrelated Paper Beta")
            new_k2 = next(k for k in rows if k.startswith("K2-"))
            new_k3 = next(k for k in rows if k.startswith("K3-"))
            self.assertEqual(rows[new_k2], "Title Two")
            self.assertEqual(rows[new_k3], "Title Three")
            self.assertEqual(dup_of["K1"], None)
            self.assertEqual(dup_of[new_k2], "K1")       # 成员后缀独立行 + 标记
            self.assertEqual(dup_of[new_k3], "K1")

            # 消歧后缀确定性：重导同一批文件沿用后缀键，行数不变（幂等）
            proc2 = _run_cli(["import", "--file", str(batch), "--db", db])
            self.assertEqual(proc2.returncode, 0, proc2.stderr)
            self.assertIn("新增 0 条", proc2.stdout)
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0], 5)

    def test_stabilize_never_produces_self_referential_pairs(self):
        # pairs 刷新禁自指：改名后 kept_key == dup_key 的配对从清单剔除。
        arts = [
            _article("K2", "Unrelated Paper Alpha", "Doe, Jane"),
            _article("K1", "Title One", "Smith, Ann", "10.1/d1"),
            _article("K2", "Title Two", "Jones, Jake", "10.1/d1"),
        ]
        known = [arts[0]]
        batch = arts[1:]
        _, report = find_duplicates(batch)
        pairs = report.exact_pairs + report.fuzzy_pairs + report.key_conflict_pairs
        stabilize_conflicting_keys(arts, pairs, known=known)
        for pair in pairs:
            self.assertNotEqual(pair.kept_key, pair.dup_key, pair)

    def test_stabilize_dedupes_redundant_group_edges(self):
        # 复审 #2 的 stabilize 直调视角：两个 exact 组 + 一条键冲突边，
        # 成员 SHARED 键被两组使用时后到组加后缀。
        arts = [
            _article("KA", "Unrelated Paper Alpha", "Alpha, Ann", "10.9/d1"),
            _article("SHARED", "Shared Study One", "Alpha, Ann", "10.9/d1"),
            _article("KC", "Unrelated Paper Gamma", "Gamma, Gina", "10.9/d2"),
            _article("SHARED", "Shared Study Two", "Gamma, Gina", "10.9/d2"),
        ]
        _, report = find_duplicates(arts)
        pairs = report.exact_pairs + report.key_conflict_pairs
        renames = stabilize_conflicting_keys(arts, pairs)
        keys = [a.zotero_key for a in arts]
        self.assertEqual(len(set(keys)), 4)             # 无键坍缩
        self.assertEqual(renames, [("SHARED", arts[3].zotero_key)])
        self.assertTrue(arts[3].zotero_key.startswith("SHARED-"))
