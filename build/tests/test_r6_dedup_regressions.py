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

评审 R1 返修（faithful 2.1.3 旧库兼容 + CLI 副作用）：
- FB1/FB3：旧库行身份匹配分层——raw["_fallback_id"]（字节相同文件）→
  content_hash（2.1.3 起持久化，不受 authors 缺失/年份口径漂移影响）→
  （标题+姓氏+年份）；预置库一律 faithful（CSV authors 空、2.1.3 年份
  口径、raw 标记齐备），重导同一文件断言零新增行；
- FB2：单侧缺作者=元数据补全（基线 4a06688 同为 True），保留键走
  UPDATE + 显式警告；
- FB4：模糊层只扫本批、候选展示只保留含本批 key 的配对；
- FB5：多条记录匹配同一库内行→坍缩语义保留（零新增）+ 显式警告；
- FB6：沿用库内键/同键更新的行不计入"进入筛选"；
- FB7：自指配对在调用方 report 列表赋值处真正过滤，经真实 CLI/
  living_review 路径 spy upsert 实参断言。

评审 R2 返修（adoption 预扫描未排除库内行的三处后果）：
- FB-A（阻断）：库内旧行（auto-* 同样命中 _is_generated_key_article）
  经索引自匹配/互匹配被计入 adopted_targets，把库内行当"本批记录"误报
  坍缩警告（1 旧+1 新报"本批 2 条"、1 旧+2 新报"本批 3 条"、库内两条
  同哈希行+无关导入也告警）。修复=预扫描跳过 id(art) in known_ids；
  回归钉住条数（"本批 2 条"）与三个负例（含 living change_report）。
- FB-B（低）：content/_fallback 分层命中且库行缺作者时同样发"元数据
  补全"警告（此前只并入旧键、静默补 authors）。
- FB-C（低）：预扫描仅处理本批生成键记录，不再对库内行互相 uf.union
  形成与 exact_pairs 无关的阴影组；"库内同哈希行+无关导入"回归覆盖。
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
        for tag in ("au", "ti", "py", "jo", "do", "id"):
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

    def test_same_title_one_side_author_missing_merges_as_completion(self):
        # 评审 R1 返修·FB2：单侧缺作者=元数据补全，不再判冲突（基线
        # 4a06688 的 _key_merge_confirmed 对同对为 True——标题 token>=100
        # 即合并，作者不参与裁决；2.1.3 库 CSV 行 authors 恒空，U2-P1）。
        # 模糊层作者门不受影响：缺作者仍不放行标题相似候选。
        arts = [
            _article("SHARE", "Same Title Here", "Smith, John"),
            _article("SHARE", "Same Title Here", ""),
        ]
        kept, report = find_duplicates(arts)
        self.assertEqual(len(kept), 1)              # 同键+标题完全相等 → 合并
        self.assertEqual(report.key_conflict_pairs, [])
        # 双方都有作者且不相似才冲突（Smith vs Jones）
        conflict = [
            _article("SHARE", "Same Title Here", "Smith, John"),
            _article("SHARE", "Same Title Here", "Jones, Anna"),
        ]
        _, report2 = find_duplicates(conflict)
        self.assertEqual(len(report2.key_conflict_pairs), 1)

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
        # 曾"精确重复 1"静默覆盖只剩 men 行；集成对账（r6-parsers 53a3823
        # 同文件重复键降级）后：第二条在解析层降级为 auto 键，两条均进入
        # 筛选并报模糊候选，不再静默合并/覆盖——安全不变量保持。
        with tempfile.TemporaryDirectory(prefix="rf_r6d1_") as tmp:
            ris = Path(tmp) / "wm.ris"
            db = str(Path(tmp) / "wm.db")
            _write_ris(ris, [
                {"au": "Wang, Wei", "ti": "Effects of drug X on mortality in women", "py": "2005", "id": "WM1"},
                {"au": "Wang, Wei", "ti": "Effects of drug X on mortality in men", "py": "2006", "id": "WM1"},
            ])
            proc = _run_cli(["import", "--file", str(ris), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("模糊候选 1 条", proc.stdout)
            self.assertIn("未自动合并", proc.stdout)
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
# R6-D2 方案 2（复审 #8/#15）：2.1.3 公式预置库重导同一文件零新增行（R1）
# ---------------------------------------------------------------------------

class LegacyAutoKeyReimportTests(unittest.TestCase):
    """auto-* 键公式变更后，旧库按内容匹配沿用既有键，重导幂等。"""

    @staticmethod
    def _preset_legacy_db(db: str, articles: list, keys: list, *,
                          authors: str | None = None, raw_markers: bool = True,
                          content_hash: str | None = None,
                          year_override: int | None = None) -> None:
        """按 2.1.3 键形态预置库内行（模拟旧版本入库结果，评审 R1 返修·FB1）。

        faithful 要点（git show 501e1f3 核对）：
        - 2.1.3 对无 Key 行写 raw["_generated_key"/"_fallback_key"/
          "_fallback_id"]（与当前同格式），raw_markers=True 时如实预置；
        - 2.1.3 的 CSV "Author" 列不映射（U2-P1）→ 旧行 authors=''——
          authors="" 显式模拟该形态（authors=None 用当前解析值，RIS 在
          2.1.3 即解析作者，faithful）；
        - 2.1.3 的 extract_year 取首个 1500-2100 年份（当前为括号/句末
          优先，FB3）——year_override 显式模拟旧口径取值；
        - content_hash 可显式覆盖（模拟哈希不一致旧行，验证降级匹配层）。
        """
        db_mod.init_db(db)
        preset = []
        for art, key in zip(articles, keys):
            raw = {}
            if raw_markers:
                raw = {
                    "_generated_key": True,
                    "_fallback_key": key,
                    "_fallback_id": str(art.raw.get("_fallback_id") or ""),
                }
            preset.append(Article(
                zotero_key=key, item_type=art.item_type, title=art.title,
                authors=art.authors if authors is None else authors,
                journal=art.journal,
                year=year_override if year_override is not None else art.year,
                doi=art.doi, abstract=art.abstract, url=art.url,
                source_format=art.source_format, raw=raw,
                content_hash=content_hash if content_hash is not None else art.content_hash,
            ))
        db_mod.upsert_articles(db, preset, [])

    def test_ris_no_key_no_doi_reimport_zero_new_rows(self):
        # 复审 #15 实测复现：预置 auto-<sha1(doi|title)> 旧行后重导同一
        # RIS 曾得到新公式键并"新增 1 条"致同内容两行。
        with tempfile.TemporaryDirectory(prefix="rf_r6d2a_") as tmp:
            ris = Path(tmp) / "leg.ris"
            db = str(Path(tmp) / "leg.db")
            _write_ris(ris, [
                {"au": "Wang, Wei", "ti": "Effects of drug X on mortality in women", "py": "2005"},
                {"au": "Park, Su", "ti": "Brand new trial on statins", "py": "2006"},
            ])
            articles = parse_file(ris)
            legacy_keys = [_legacy_auto_key(a.doi, a.title) for a in articles]
            self.assertTrue(all(k.startswith("auto-") for k in legacy_keys))
            self.assertNotEqual(legacy_keys[0], articles[0].zotero_key)  # 公式确已不同
            self._preset_legacy_db(db, articles, legacy_keys)

            proc = _run_cli(["import", "--file", str(ris), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("新增 0 条", proc.stdout)   # R1：零新增行
            self.assertIn("进入筛选 0 条", proc.stdout)  # FB6：沿用键更新不计入
            # 评审 R2 返修·FB-A：1 旧 + 1 新（幂等重导）不得报"本批 N 条
            # 匹配到同一库内行"——库内行不得被当成"本批记录"计数
            self.assertNotIn("匹配到同一库内行", proc.stderr)
            with closing(sqlite3.connect(db)) as conn:
                rows = conn.execute(
                    "SELECT zotero_key, is_duplicate_of FROM articles ORDER BY import_order"
                ).fetchall()
            self.assertEqual([r[0] for r in rows], legacy_keys)  # 沿用旧键
            self.assertEqual([r[1] for r in rows], [None, None])

    def test_ris_no_key_with_doi_reimport_zero_new_rows(self):
        # 带 DOI 的无 Key 记录：exact_doi 并入库内行 + 生成键成员沿用库内键。
        with tempfile.TemporaryDirectory(prefix="rf_r6d2b_") as tmp:
            ris = Path(tmp) / "doi.ris"
            db = str(Path(tmp) / "doi.db")
            _write_ris(ris, [
                {"au": "Kim, Lee", "ti": "Title One", "py": "2020", "do": "10.9/t1"},
                {"au": "Roe, Rae", "ti": "Title Two", "py": "2021", "do": "10.9/t2"},
            ])
            articles = parse_file(ris)
            legacy_keys = [_legacy_auto_key(a.doi, a.title) for a in articles]
            self._preset_legacy_db(db, articles, legacy_keys)

            proc = _run_cli(["import", "--file", str(ris), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("新增 0 条", proc.stdout)
            self.assertIn("进入筛选 0 条", proc.stdout)  # FB6
            with closing(sqlite3.connect(db)) as conn:
                rows = conn.execute(
                    "SELECT zotero_key, is_duplicate_of FROM articles").fetchall()
            self.assertEqual(sorted(r[0] for r in rows), sorted(legacy_keys))
            self.assertEqual([r[1] for r in rows], [None, None])

    def test_csv_no_key_reimport_zero_new_rows_faithful(self):
        # 评审 R1 返修·FB1（阻断）：faithful 2.1.3 形态——"Author" 列在
        # 2.1.3 不映射（U2-P1），旧行 authors=''，（标题+姓氏+年份）匹配
        # 必然落空；由 _fallback_id（字节相同文件）→ content_hash 分层命中，
        # 重导零新增行。此前的预置用了当前解析的 authors，属假通过。
        with tempfile.TemporaryDirectory(prefix="rf_r6d2c_") as tmp:
            csv_path = Path(tmp) / "leg.csv"
            db = str(Path(tmp) / "leg.db")
            csv_path.write_text(
                "Title,Author,Publication Year\n"
                "Effects of drug X on mortality in women,\"Wang, Wei\",2005\n"
                "Brand new trial on statins,\"Park, Su\",2006\n",
                encoding="utf-8",
            )
            articles = parse_file(csv_path)
            self.assertEqual(articles[0].authors, "Wang, Wei")  # 当前解析有作者
            legacy_keys = [_legacy_auto_key(a.doi, a.title) for a in articles]
            self._preset_legacy_db(db, articles, legacy_keys, authors="")  # 旧行 authors 空

            proc = _run_cli(["import", "--file", str(csv_path), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("新增 0 条", proc.stdout)          # R1：零新增行
            self.assertIn("进入筛选 0 条", proc.stdout)       # FB6：沿用键更新不计入
            # 评审 R2 返修·FB-B：content/_fallback 分层命中且库行缺作者时
            # 必须发"元数据补全"警告，不得静默把 authors='' 补成有值
            self.assertIn("补全", proc.stderr)
            self.assertNotIn("匹配到同一库内行", proc.stderr)   # FB-A：无误报
            with closing(sqlite3.connect(db)) as conn:
                keys = {r[0] for r in conn.execute("SELECT zotero_key FROM articles")}
            self.assertEqual(keys, set(legacy_keys))          # 沿用旧键

    def test_living_review_legacy_reimport_no_new_articles(self):
        # 复审 #6：living_review 同样复制新行；现沿用库内键，new_articles=[]。
        with tempfile.TemporaryDirectory(prefix="rf_r6d2d_") as tmp:
            ris = Path(tmp) / "leg.ris"
            db = str(Path(tmp) / "leg.db")
            _write_ris(ris, [
                {"au": "Wang, Wei", "ti": "Effects of drug X on mortality in women", "py": "2005"},
                {"au": "Park, Su", "ti": "Brand new trial on statins", "py": "2006"},
            ])
            articles = parse_file(ris)
            legacy_keys = [_legacy_auto_key(a.doi, a.title) for a in articles]
            self._preset_legacy_db(db, articles, legacy_keys)

            result = process_incremental_update(
                db, str(ris), "A vs B", "mortality", "12 months", "RR")
            self.assertEqual(result.new_articles, [])
            self.assertEqual(result.duplicates_removed, 0)
            # 评审 R2 返修·FB-A：living 变更报告同样不得出现库内行误报
            self.assertNotIn("匹配到同一库内行", result.change_report)
            with closing(sqlite3.connect(db)) as conn:
                rows = conn.execute(
                    "SELECT zotero_key, is_duplicate_of FROM articles").fetchall()
            self.assertEqual(sorted(r[0] for r in rows), sorted(legacy_keys))
            self.assertEqual([r[1] for r in rows], [None, None])


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
            # 集成对账（r6-parsers 53a3823 同文件重复键降级）：SHARED 第二次出现
            # 在解析层即降级为内容稳定 auto 键，不再走 stabilize 后缀消歧——
            # 安全不变量不变：4 行全存活、无内容覆盖、重复标记指向各自组友。
            with closing(sqlite3.connect(db)) as conn:
                rows = dict(conn.execute(
                    "SELECT zotero_key, title FROM articles").fetchall())
                dup_of = dict(conn.execute(
                    "SELECT zotero_key, is_duplicate_of FROM articles").fetchall())
            self.assertEqual(len(rows), 4)               # 4 行全部共存
            shared_keys = [k for k in rows if k.startswith("SHARED")]
            self.assertEqual(len(shared_keys), 1)        # 首个 SHARED 保留原键
            self.assertEqual(rows["SHARED"], "Shared Study One")   # Alpha 内容未被覆盖
            # 第二个 SHARED（Gamma 侧）被解析层降级为内容稳定 auto 键：
            demoted = [k for k in rows if k.startswith("auto-")]
            self.assertEqual(len(demoted), 1)
            self.assertEqual(rows[demoted[0]], "Shared Study Two")  # Gamma 内容保留
            self.assertEqual(dup_of["SHARED"], "KA")
            self.assertEqual(dup_of[demoted[0]], "KC")

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


# ---------------------------------------------------------------------------
# 复审 #3：CLI 库内去重（known 纳入 DOI/键匹配，与 living_review 对齐）
# ---------------------------------------------------------------------------

class CliLibraryDedupTests(unittest.TestCase):
    def test_cross_batch_same_doi_marked_not_reinserted(self):
        # 复审 #3 实测复现：种子 K1 DOI 10.1/x 后导入 K2 同 DOI/标题曾
        # 输出"新增 1 条、重复标记 0"且两行 is_duplicate_of 均 NULL。
        with tempfile.TemporaryDirectory(prefix="rf_r6lib_") as tmp:
            seed = Path(tmp) / "seed.ris"
            batch = Path(tmp) / "batch.ris"
            db = str(Path(tmp) / "lib.db")
            _write_ris(seed, [{"au": "Kim, Lee", "ti": "Some study", "do": "10.1/x", "id": "K1"}])
            _write_ris(batch, [{"au": "Kim, Lee", "ti": "Some study", "do": "10.1/x", "id": "K2"}])
            self.assertEqual(_run_cli(["import", "--file", str(seed), "--db", db]).returncode, 0)
            proc = _run_cli(["import", "--file", str(batch), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("重复标记 1 条", proc.stdout)
            with closing(sqlite3.connect(db)) as conn:
                rows = conn.execute(
                    "SELECT zotero_key, is_duplicate_of FROM articles ORDER BY import_order"
                ).fetchall()
            self.assertEqual(rows, [("K1", None), ("K2", "K1")])

    def test_reimport_same_file_is_idempotent_with_source_keys(self):
        # 同一文件重导（带来源键）：行数不变、顺序不变（db.py 承诺的幂等）。
        with tempfile.TemporaryDirectory(prefix="rf_r6lib2_") as tmp:
            ris = Path(tmp) / "a.ris"
            db = str(Path(tmp) / "idem.db")
            _write_ris(ris, [
                {"au": "Kim, Lee", "ti": "Title One", "do": "10.9/t1", "id": "K1"},
                {"au": "Roe, Rae", "ti": "Title Two", "py": "2021", "id": "K2"},
            ])
            self.assertEqual(_run_cli(["import", "--file", str(ris), "--db", db]).returncode, 0)
            proc = _run_cli(["import", "--file", str(ris), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("新增 0 条", proc.stdout)
            with closing(sqlite3.connect(db)) as conn:
                rows = conn.execute(
                    "SELECT zotero_key, is_duplicate_of FROM articles ORDER BY import_order"
                ).fetchall()
            self.assertEqual(rows, [("K1", None), ("K2", None)])


# ---------------------------------------------------------------------------
# 复审 #4/#10：模糊候选生成树去重 + 总量上限
# ---------------------------------------------------------------------------

class FuzzyCandidateMemoryTests(unittest.TestCase):
    def test_two_thousand_similar_titles_produce_spanning_tree_only(self):
        # 复审 #4 实测：n=2000 同模板标题曾产生 1,999,000 对 / 16.35s /
        # 386.5MB（tracemalloc 峰值）。生成树去重后至多 n-1 条边。
        # 时长/内存断言按放宽阈值（泳道指示），防环境抖动误报。
        arts = [
            _article(f"K{i}", f"Filler paper number {i} about topic {i}", "Doe, Jane")
            for i in range(1, 2001)
        ]
        tracemalloc.start()
        started = time.monotonic()
        kept, report = find_duplicates(arts)
        duration = time.monotonic() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertEqual(len(kept), 2000)               # 全部保留（只出候选）
        self.assertEqual(len(report.fuzzy_pairs), 1999)  # 生成树边 = n - 1
        self.assertTrue(all(p.method == "fuzzy" for p in report.fuzzy_pairs))
        # 每个非根节点恰出现一次为 dup（--auto-merge-fuzzy 的移除计数口径）
        dups = [p.dup_key for p in report.fuzzy_pairs]
        self.assertEqual(len(dups), len(set(dups)))
        self.assertLess(duration, 30.0)                 # 放宽阈值（原全量 16.35s 起）
        self.assertLess(peak, 200 * 1024 * 1024)        # 放宽阈值（原 386.5MB）

    def test_fuzzy_pair_limit_truncates_with_explicit_note(self):
        arts = [
            _article(f"K{i}", f"Filler paper number {i} about topic {i}", "Doe, Jane")
            for i in range(1, 9)
        ]
        with mock.patch.object(dedup_mod, "FUZZY_PAIR_LIMIT", 3):
            kept, report = find_duplicates(arts)
        self.assertEqual(len(report.fuzzy_pairs), 3)    # 截断到上限
        self.assertEqual(len(kept), 8)
        self.assertIn("已截断", report.note)
        self.assertIn("3", report.note)

    def test_connectivity_preserved_for_candidate_grouping(self):
        # 生成树去重不改变连通性：候选复核组仍把同模板标题聚合为一组。
        from coscreen.living_review import _candidate_review_groups

        arts = [
            _article(f"K{i}", f"Filler paper number {i} about topic {i}", "Doe, Jane")
            for i in range(1, 7)
        ]
        _, report = find_duplicates(arts)
        groups = _candidate_review_groups(
            report.fuzzy_pairs, arts, {a.zotero_key for a in arts})
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["members"]), 6)


# ---------------------------------------------------------------------------
# 复审 #5：living_review 口径（duplicates_removed 只按 is_duplicate_of 计）
# ---------------------------------------------------------------------------

class LivingReviewCountingTests(unittest.TestCase):
    def test_pending_candidates_not_counted_as_removed(self):
        # 复审 #5 实测复现：导入 WM1/WM2（模糊候选）后 manuscript 层把
        # "待人工确认"计成"已移除"；IncrementalResult 本身必须口径干净：
        # duplicates_removed=0、候选归 pending_candidates、DB 无标记。
        with tempfile.TemporaryDirectory(prefix="rf_r6lr_") as tmp:
            db = str(Path(tmp) / "lr.db")
            db_mod.init_db(db)
            db_mod.upsert_articles(db, [
                _article("WM1", "Effects of drug X on mortality in women",
                         "Wang, Wei", year=2005),
            ], [])
            result = process_incremental_articles(
                db,
                [_article("WM2", "Effects of drug X on mortality in men",
                          "Wang, Wei", year=2006)],
                "A vs B", "mortality", "12 months", "RR")
            self.assertEqual(result.duplicates_removed, 0)
            self.assertEqual(result.pending_candidates, 1)
            with closing(sqlite3.connect(db)) as conn:
                marks = dict(conn.execute(
                    "SELECT zotero_key, is_duplicate_of FROM articles").fetchall())
            self.assertEqual(marks, {"WM1": None, "WM2": None})

    def test_duplicates_removed_counts_only_marked_rows(self):
        # 与库内同 DOI 的跨批次重复：K2 打 is_duplicate_of 标记 → 计 1；
        # 同键自然坍缩（无标记）不计入——与 prisma_task_metrics 的
        # is_duplicate_of 口径一致。
        with tempfile.TemporaryDirectory(prefix="rf_r6lr2_") as tmp:
            db = str(Path(tmp) / "lr.db")
            db_mod.init_db(db)
            db_mod.upsert_articles(db, [
                _article("K1", "Some study", "Kim, Lee", "10.1/x"),
            ], [])
            result = process_incremental_articles(
                db,
                [_article("K2", "Some study", "Kim, Lee", "10.1/x")],
                "A vs B", "mortality", "12 months", "RR")
            self.assertEqual(result.duplicates_removed, 1)
            self.assertEqual(result.new_articles, [])
            with closing(sqlite3.connect(db)) as conn:
                marks = dict(conn.execute(
                    "SELECT zotero_key, is_duplicate_of FROM articles").fetchall())
            self.assertEqual(marks, {"K1": None, "K2": "K1"})


# ---------------------------------------------------------------------------
# 评审 R1 返修：faithful 2.1.3 旧库兼容（FB1/FB2/FB3）与 CLI 副作用（FB4-FB7）
# ---------------------------------------------------------------------------

class ReviewFeedbackR1Tests(unittest.TestCase):
    """按评审探针手法原样回放：faithful 预置 + 真实 CLI。"""

    def test_source_key_csv_legacy_row_author_completion_zero_new(self):
        # FB2：2.1.3 CSV 有 Source Key（Key 列映射不变）但 "Author" 列不映射
        # → 旧行 authors=''。重导同一文件（当前解析 authors='Kim, Lee'）曾因
        # 单侧缺作者被判冲突，输出「键消歧 1 处 + 键冲突候选 + 新增 1 条」，
        # DB 2 行。现按元数据补全：保留键走 UPDATE + 显式警告 + 零新增
        # （与基线 4a06688 的 _key_merge_confirmed=True 口径一致）。
        from coscreen.dedup import _key_merge_confirmed

        with tempfile.TemporaryDirectory(prefix="rf_r6fb2_") as tmp:
            csv_path = Path(tmp) / "k.csv"
            db = str(Path(tmp) / "k.db")
            csv_path.write_text(
                "Key,Title,Author,DOI\n"
                "K1,Some study on statins,\"Kim, Lee\",10.9/x\n",
                encoding="utf-8",
            )
            articles = parse_file(csv_path)
            self.assertEqual(articles[0].zotero_key, "K1")
            self.assertEqual(articles[0].authors, "Kim, Lee")
            # faithful 旧行：Key/Title/DOI 已解析，Author 列丢失 → authors=''
            self._preset_articles(db, [
                Article(zotero_key="K1", title=articles[0].title,
                        authors="", doi=articles[0].doi,
                        content_hash=articles[0].content_hash),
            ])
            # 基线口径对照：同一对在基线（token>=90 即合并）为 True，
            # 收紧后仍须为 True（单侧缺作者=元数据补全，不判冲突）
            known = db_mod.list_articles(db, include_duplicates=True)
            self.assertEqual(known[0].authors, "")
            self.assertTrue(_key_merge_confirmed(known[0], articles[0]))

            proc = _run_cli(["import", "--file", str(csv_path), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("新增 0 条", proc.stdout)
            self.assertNotIn("键消歧", proc.stdout)
            self.assertNotIn("键冲突候选", proc.stdout)
            self.assertIn("补全", proc.stderr)  # 元数据补全显式警告（R3）
            with closing(sqlite3.connect(db)) as conn:
                rows = conn.execute(
                    "SELECT zotero_key, authors FROM articles").fetchall()
            self.assertEqual(rows, [("K1", "Kim, Lee")])  # 单行，元数据已补全

            # 对照组："Creator" 列（2.1.3 也解析作者）→ 同样零新增
            csv2 = Path(tmp) / "creator.csv"
            db2 = str(Path(tmp) / "creator.db")
            csv2.write_text(
                "Key,Title,Creator,DOI\n"
                "K1,Some study on statins,\"Kim, Lee\",10.9/x\n",
                encoding="utf-8",
            )
            articles2 = parse_file(csv2)
            self.assertEqual(articles2[0].authors, "Kim, Lee")
            self._preset_articles(db2, [
                Article(zotero_key="K1", title=articles2[0].title,
                        authors="Kim, Lee", doi=articles2[0].doi,
                        content_hash=articles2[0].content_hash),
            ])
            proc2 = _run_cli(["import", "--file", str(csv2), "--db", db2])
            self.assertEqual(proc2.returncode, 0, proc2.stderr)
            self.assertIn("新增 0 条", proc2.stdout)
            self.assertIn("更新 1 条", proc2.stdout)
            self.assertNotIn("键消歧", proc2.stdout)

    @staticmethod
    def _preset_articles(db: str, articles: list) -> None:
        db_mod.init_db(db)
        db_mod.upsert_articles(db, articles, [])

    def test_year_extraction_drift_anchors_zero_new_rows(self):
        # r2 评审 R1：年份取值口径（extract_year 括号/句末优先）在版本间
        # 可能漂移——同文件字节重导经 _fallback_id 层**零新增**（FB3 验收
        # 保持）；r8 复审年份门：**跨文件**且双方年份均明确不同时不静默
        # 沿用（新行＋显式警告，不自动继承既有筛选决策）。
        from coscreen.normalize import extract_year
        self.assertEqual(extract_year("Volume 1850, issued 2019"), 2019)
        self.assertEqual(extract_year("Reprint of the 1905 original (2012)"), 2012)
        with tempfile.TemporaryDirectory(prefix="rf_r6fb3_") as tmp:
            y1 = Path(tmp) / "y1.ris"
            y2 = Path(tmp) / "y2.ris"
            db_path = str(Path(tmp) / "y.db")
            _write_ris(y1, [{"au": "Doe, Jane", "ti": "Year drift anchor",
                             "py": "Volume 1850, issued 2019"}])
            _write_ris(y2, [{"au": "Doe, Jane", "ti": "Year drift anchor",
                             "py": "Reprint of the 1905 original (2012)"}])
            proc1 = _run_cli(["import", "--file", str(y1), "--db", db_path])
            self.assertEqual(proc1.returncode, 0, proc1.stderr)
            with closing(sqlite3.connect(db_path)) as _c:
                _year1 = _c.execute(
                    "SELECT year FROM articles ORDER BY import_order").fetchall()
            self.assertEqual(_year1, [(2019,)])  # 当前口径
            # 同文件重导：_fallback_id 层命中 → 零新增（FB3 保持）
            proc_same = _run_cli(["import", "--file", str(y1), "--db", db_path])
            self.assertEqual(proc_same.returncode, 0, proc_same.stderr)
            self.assertIn("新增 0 条", proc_same.stdout)
            # 跨文件、年份 2019→2012：不静默沿用（新行＋警告，不继承决策）
            proc2 = _run_cli(["import", "--file", str(y2), "--db", db_path])
            self.assertEqual(proc2.returncode, 0, proc2.stderr)
            self.assertIn("新增 1 条", proc2.stdout)
            self.assertIn("年份不同", proc2.stderr)
            with closing(sqlite3.connect(db_path)) as _c:
                _years = sorted(r[0] for r in _c.execute(
                    "SELECT year FROM articles").fetchall())
            self.assertEqual(_years, [2012, 2019])

    def test_title_author_year_tier_still_matches_when_hash_differs(self):
        # 分层兜底：content_hash 不一致（旧库行被外部改写）时退回
        # （标题+姓氏+年份）——三层的最后一层仍保证身份连续。
        with tempfile.TemporaryDirectory(prefix="rf_r6fb3b_") as tmp:
            ris = Path(tmp) / "t3.ris"
            db = str(Path(tmp) / "t3.db")
            _write_ris(ris, [
                {"au": "Roe, Rae", "ti": "Distinct cohort follow up", "py": "2018"},
            ])
            arts = parse_file(ris)
            legacy = _legacy_auto_key(arts[0].doi, arts[0].title)
            LegacyAutoKeyReimportTests._preset_legacy_db(
                db, arts, [legacy], raw_markers=False,
                content_hash="0" * 40)  # 哈希不一致：前两层落空
            proc = _run_cli(["import", "--file", str(ris), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("新增 0 条", proc.stdout)
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0], 1)

    def test_unrelated_import_into_similar_library_prints_no_candidates(self):
        # FB4：库内已有 WM1/WM2（相似标题对），导入无关 P1 不得把库内
        # 既有配对当本批候选打印（living_review/_title_review_groups 均
        # 过滤"无本批成员"的组，CLI 此前未过滤且对全库重复 O(n^2) 扫描）。
        with tempfile.TemporaryDirectory(prefix="rf_r6fb4_") as tmp:
            lib = Path(tmp) / "lib.ris"
            other = Path(tmp) / "p1.ris"
            db = str(Path(tmp) / "lib.db")
            _write_ris(lib, [
                {"au": "Wang, Wei", "ti": "Effects of drug X on mortality in women", "py": "2005", "id": "WM1"},
                {"au": "Wang, Wei", "ti": "Effects of drug X on mortality in men", "py": "2006", "id": "WM2"},
            ])
            _write_ris(other, [{"au": "Doe, Jane", "ti": "Unrelated paper on statins", "py": "2020", "id": "P1"}])
            first = _run_cli(["import", "--file", str(lib), "--db", db])
            self.assertIn("模糊候选 1 条", first.stdout)  # 首导批内候选照常
            proc = _run_cli(["import", "--file", str(other), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("模糊候选", proc.stdout)     # 库内既有对不重印
            self.assertNotIn("WM1", proc.stdout)
            self.assertNotIn("WM2", proc.stdout)
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0], 3)

    def test_multiple_records_matching_same_library_row_warns(self):
        # FB5：同一库内行被本批多条记录命中时沿用旧版坍缩语义（零新增行，
        # 满足 R1 验收），但必须显式警告"后写覆盖前写"（R3），不得静默。
        with tempfile.TemporaryDirectory(prefix="rf_r6fb5_") as tmp:
            ris = Path(tmp) / "dup.ris"
            db = str(Path(tmp) / "legacy.db")
            _write_ris(ris, [
                {"au": "Doe, Jane", "ti": "Shared cohort analysis", "py": "2020",
                 "jo": "Journal One"},
                {"au": "Doe, Jane", "ti": "Shared cohort analysis", "py": "2020",
                 "jo": "Journal Two"},
            ])
            arts = parse_file(ris)
            self.assertEqual(arts[0].zotero_key, arts[1].zotero_key)  # 公式输入全同
            legacy = _legacy_auto_key(arts[0].doi, arts[0].title)
            LegacyAutoKeyReimportTests._preset_legacy_db(db, arts[:1], [legacy])

            proc = _run_cli(["import", "--file", str(ris), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("新增 0 条", proc.stdout)                  # 零新增（R1 兼容）
            self.assertIn("更新 2 条", proc.stdout)
            # 评审 R2 返修·FB-A：警告条数必须只数本批命中（1 旧 + 2 新
            # → "本批 2 条"，此前库内行被计入报"本批 3 条"）
            self.assertIn("本批 2 条记录内容匹配到同一库内行", proc.stderr)
            self.assertIn("覆盖", proc.stderr)
            self.assertNotIn("本批 3 条", proc.stderr)
            with closing(sqlite3.connect(db)) as conn:
                rows = conn.execute(
                    "SELECT zotero_key, journal, COUNT(*) FROM articles"
                    " GROUP BY zotero_key, journal").fetchall()
            self.assertEqual(rows, [(legacy, "Journal Two", 1)])     # 1 行，后写生效

            # 对照：同一文件导入空库 → 两行共存（批内消歧后缀）
            db2 = str(Path(tmp) / "fresh.db")
            proc2 = _run_cli(["import", "--file", str(ris), "--db", db2])
            self.assertEqual(proc2.returncode, 0, proc2.stderr)
            self.assertIn("键消歧 1 处", proc2.stdout)
            with closing(sqlite3.connect(db2)) as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0], 2)

    def test_unrelated_import_into_duplicate_hash_library_no_warning(self):
        # 评审 R2 返修·FB-A 探针3/FB-C：库内已有两条 content_hash 相同、
        # 键不同、无 _fallback_id 的 auto-* 旧行，导入一条完全无关的新
        # 记录——修复前两条库内行经 hash 索引互相命中被计入 adopted_targets
        # （stderr 误报"本批 2 条…auto-aaaaaaaaaaaa"，DB 零改动），且形成
        # 与 exact_pairs 无关的阴影合并组。修复后：无警告、无改写。
        with tempfile.TemporaryDirectory(prefix="rf_r6r2c_") as tmp:
            other = Path(tmp) / "new.ris"
            db = str(Path(tmp) / "lib.db")
            title = "Identical title here"
            same_hash = hashlib.sha1(f"|{title}".encode()).hexdigest()
            db_mod.init_db(db)
            db_mod.upsert_articles(db, [
                Article(zotero_key="auto-aaaaaaaaaaaa", title=title,
                        authors="Doe, Jane", year=2020,
                        raw={"_generated_key": True}, content_hash=same_hash),
                Article(zotero_key="auto-bbbbbbbbbbbb", title=title,
                        authors="Doe, Jane", year=2020,
                        raw={"_generated_key": True}, content_hash=same_hash),
            ], [])
            _write_ris(other, [{"au": "Rae, Kim", "ti": "Totally different subject",
                                "py": "2021", "id": "NEW1"}])
            proc = _run_cli(["import", "--file", str(other), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("匹配到同一库内行", proc.stderr)
            self.assertNotIn("补全", proc.stderr)
            with closing(sqlite3.connect(db)) as conn:
                after = conn.execute(
                    "SELECT zotero_key, title, is_duplicate_of FROM articles ORDER BY import_order"
                ).fetchall()
            self.assertEqual(after, [
                ("auto-aaaaaaaaaaaa", title, None),      # 既有两行零改动
                ("auto-bbbbbbbbbbbb", title, None),
                ("NEW1", "Totally different subject", None),  # 仅新增无关记录
            ])

    def test_living_review_multi_hit_warning_in_change_report(self):
        # 评审 R2 返修·FB-A：living_review 正例——1 旧 + 2 新命中同一库内行
        # 时变更报告出现"本批 2 条"坍缩警告（此前库内行被计入会报 3 条）。
        with tempfile.TemporaryDirectory(prefix="rf_r6r2d_") as tmp:
            ris = Path(tmp) / "dup.ris"
            db = str(Path(tmp) / "lr.db")
            _write_ris(ris, [
                {"au": "Doe, Jane", "ti": "Shared cohort analysis", "py": "2020",
                 "jo": "Journal One"},
                {"au": "Doe, Jane", "ti": "Shared cohort analysis", "py": "2020",
                 "jo": "Journal Two"},
            ])
            arts = parse_file(ris)
            legacy = _legacy_auto_key(arts[0].doi, arts[0].title)
            LegacyAutoKeyReimportTests._preset_legacy_db(db, arts[:1], [legacy])
            result = process_incremental_update(
                db, str(ris), "A vs B", "mortality", "12 months", "RR")
            self.assertIn("本批 2 条记录内容匹配到同一库内行", result.change_report)
            self.assertNotIn("本批 3 条", result.change_report)
            self.assertEqual(result.new_articles, [])   # 坍缩语义：零新增
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0], 1)

    def test_mark_pairs_contain_no_self_pairs_through_real_cli(self):
        # FB7：自指配对过滤必须落到调用方真正消费的 report 列表——经真实
        # CLI 路径 spy upsert 的 duplicate_pairs 实参断言（同键同 DOI 两条
        # 的自然坍缩会产生自指事件）。
        from unittest import mock as _mock

        recorded: dict = {}
        real = db_mod.upsert_articles

        def spy(db, arts, pairs, *args, **kwargs):
            recorded["pairs"] = list(pairs)
            return real(db, arts, pairs, *args, **kwargs)

        with tempfile.TemporaryDirectory(prefix="rf_r6fb7_") as tmp:
            ris = Path(tmp) / "same.ris"
            db = str(Path(tmp) / "same.db")
            _write_ris(ris, [
                {"au": "Kim, Lee", "ti": "Title One", "do": "10.9/same", "id": "K1"},
                {"au": "Kim, Lee", "ti": "Title One", "do": "10.9/SAME ", "id": "K1"},
            ])
            with _mock.patch.object(db_mod, "upsert_articles", side_effect=spy):
                proc = _run_cli(["import", "--file", str(ris), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            for pair in recorded["pairs"]:
                self.assertNotEqual(pair.kept_key, pair.dup_key, pair)
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0], 1)

    def test_living_review_passes_no_self_pairs_to_upsert(self):
        # FB7·living 路径：process_incremental_articles 传给 upsert 的
        # exact_pairs 不含自指配对（同键同 DOI 坍缩不产生标记）。
        from unittest import mock as _mock

        recorded: dict = {}
        real = db_mod.upsert_articles

        def spy(db, arts, pairs, *args, **kwargs):
            recorded.setdefault("pairs", []).extend(pairs)
            return real(db, arts, pairs, *args, **kwargs)

        with tempfile.TemporaryDirectory(prefix="rf_r6fb7b_") as tmp:
            db = str(Path(tmp) / "lr.db")
            db_mod.init_db(db)
            incoming = [
                _article("K1", "Title A", "Smith, John", "10.9/same"),
                _article("K1", "Title A", "Smith, John", "10.9/SAME "),
            ]
            with _mock.patch.object(db_mod, "upsert_articles", side_effect=spy):
                process_incremental_articles(
                    db, incoming, "A vs B", "mortality", "12 months", "RR")
            for pair in recorded["pairs"]:
                self.assertNotEqual(pair.kept_key, pair.dup_key, pair)
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0], 1)


# ---------------------------------------------------------------------------
# 集成轮复审回归（D1 跨批/D2 作者门/D3 姓名提取）：复现手法原样回放
# ---------------------------------------------------------------------------

class CrossBatchStableKeyTests(unittest.TestCase):
    """R6-D1 复审：跨批次同来源键不再静默覆盖（r5 原 ID=1 手法已因 1-2 位
    纯数字键降级失效，此处按复审改用 5 位合法 PMID 形态稳定键原样回放）。"""

    def test_cross_batch_phase2_phase3_same_stable_key_both_survive(self):
        # 复审实测：$TMP/a/p2.ris 与 p3.ris（ID - 12345，无 DOI，A phase 2/
        # Smith/2020 与 A phase 3/Jones/2021）——修复前第二次导入「新增 0
        # 条，更新 1 条」，DB 仅剩 phase 3 行、phase 2 无候选提示。
        with tempfile.TemporaryDirectory(prefix="rf_r6d1cb_") as tmp:
            p2, p3 = Path(tmp) / "p2.ris", Path(tmp) / "p3.ris"
            db = str(Path(tmp) / "cb.db")
            _write_ris(p2, [{"au": "Smith, John", "ti": "A phase 2 trial of drug X",
                             "py": "2020", "id": "12345"}])
            _write_ris(p3, [{"au": "Jones, Jane", "ti": "A phase 3 trial of drug X",
                             "py": "2021", "id": "12345"}])
            self.assertEqual(_run_cli(["import", "--file", str(p2), "--db", db]).returncode, 0)
            proc = _run_cli(["import", "--file", str(p3), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("新增 1 条", proc.stdout)  # 不得「更新 1 条」覆盖
            with closing(sqlite3.connect(db)) as conn:
                rows = conn.execute(
                    "SELECT title, is_duplicate_of FROM articles ORDER BY import_order"
                ).fetchall()
            self.assertEqual(len(rows), 2)          # 两行共存
            self.assertIn("phase 2", rows[0][0])    # phase 2 行原样保留
            self.assertIn("phase 3", rows[1][0])
            self.assertEqual([r[1] for r in rows], [None, None])  # 无静默合并

    def test_cross_batch_same_surname_different_given_both_survive(self):
        # 复审实测：同题『Editorial』Smith, John vs Smith, Jane（同键同年无
        # DOI）修复前只剩 1 行（R6-D4：名无区分度）。
        with tempfile.TemporaryDirectory(prefix="rf_r6d1sj_") as tmp:
            e1, e2 = Path(tmp) / "e1.ris", Path(tmp) / "e2.ris"
            db = str(Path(tmp) / "sj.db")
            _write_ris(e1, [{"au": "Smith, John", "ti": "Editorial",
                             "py": "2020", "id": "EDIT1"}])
            _write_ris(e2, [{"au": "Smith, Jane", "ti": "Editorial",
                             "py": "2020", "id": "EDIT1"}])
            self.assertEqual(_run_cli(["import", "--file", str(e1), "--db", db]).returncode, 0)
            proc = _run_cli(["import", "--file", str(e2), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("新增 1 条", proc.stdout)
            with closing(sqlite3.connect(db)) as conn:
                rows = conn.execute(
                    "SELECT title, is_duplicate_of FROM articles ORDER BY import_order"
                ).fetchall()
            self.assertEqual(len(rows), 2)          # 两行共存不坍缩
            self.assertEqual([r[1] for r in rows], [None, None])


class LegacyHashAuthorGateTests(unittest.TestCase):
    """R6-D2 复审回归：content_hash 命中必须叠加首作者门（分支修复自身
    引入的坍缩面——哈希只含 doi|title，同题不同作者曾直接沿用库内键）。"""

    def test_hash_hit_incompatible_author_not_adopted(self):
        # 复审实测：预置 2.1.3 键 auto-ab84b05d60c2 的行（无 ID/无 DOI，
        # 标题『Editorial』，Smith, John/2020），再导入同标题但
        # Jones, Jane/2021 → 曾『精确重复 1』DB 仅 1 行且零警告。
        import hashlib
        from coscreen.models import Article
        import coscreen.db as db_mod

        base = "auto-" + hashlib.sha1("|Editorial".encode()).hexdigest()[:12]
        with tempfile.TemporaryDirectory(prefix="rf_r6d2ag_") as tmp:
            j = Path(tmp) / "j.ris"
            db = str(Path(tmp) / "ag.db")
            _write_ris(j, [{"au": "Jones, Jane", "ti": "Editorial", "py": "2021"}])
            db_mod.init_db(db)
            db_mod.upsert_articles(
                db, [Article(zotero_key=base, title="Editorial",
                             authors="Smith, John", year=2020, raw={})], [])
            proc = _run_cli(["import", "--file", str(j), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("新增 1 条", proc.stdout)     # 不沿用：新行
            self.assertIn("首作者不兼容", proc.stderr)   # 显式警告（R3）
            with closing(sqlite3.connect(db)) as conn:
                rows = conn.execute(
                    "SELECT zotero_key, title FROM articles ORDER BY import_order"
                ).fetchall()
            self.assertEqual(len(rows), 2)              # 两行各自成行
            self.assertEqual(rows[0][0], base)          # 旧键行原样保留

    def test_hash_hit_same_author_adopts_legacy_key(self):
        # 对照（FB1/FB3 前提）：同作者同题的 2.1.3 预置行重导仍沿用旧键，
        # 零新增——作者门不得误伤元数据补全与正常重导。
        import hashlib
        from coscreen.models import Article
        import coscreen.db as db_mod

        base = "auto-" + hashlib.sha1("|Editorial".encode()).hexdigest()[:12]
        with tempfile.TemporaryDirectory(prefix="rf_r6d2ok_") as tmp:
            j = Path(tmp) / "j.ris"
            db = str(Path(tmp) / "ok.db")
            _write_ris(j, [{"au": "Smith, John", "ti": "Editorial", "py": "2020"}])
            db_mod.init_db(db)
            db_mod.upsert_articles(
                db, [Article(zotero_key=base, title="Editorial",
                             authors="Smith, John", year=2020, raw={})], [])
            proc = _run_cli(["import", "--file", str(j), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("新增 0 条", proc.stdout)     # 沿用旧键走更新
            with closing(sqlite3.connect(db)) as conn:
                rows = conn.execute(
                    "SELECT zotero_key, title FROM articles").fetchall()
            self.assertEqual(rows, [(base, "Editorial")])


class SurnameExtractionTests(unittest.TestCase):
    """R6-D3 复审回归：1-2 字母真实姓氏不得当缩写（表驱动原样回放）。"""

    def test_two_letter_surnames_recognized(self):
        from coscreen.normalize import (
            first_author_given_names, first_author_surname,
        )
        cases = [
            # (输入, 期望姓, 期望名)
            ("John Wu", "wu", "john"),       # 修复前姓='john'
            ("Wu, John", "wu", "john"),
            ("Wei Li", "li", "wei"),         # 修复前姓='wei'
            ("Li, Wei", "li", "wei"),
            ("Smith, J. A.", "smith", "j a"),
            ("Smith JA", "smith", "ja"),     # PubMed 缩写尾仍是名
            ("Maria de la Cruz", "cruz", "maria de la"),
        ]
        for name, want_surname, want_given in cases:
            with self.subTest(name=name):
                self.assertEqual(first_author_surname(name), want_surname)
                self.assertEqual(first_author_given_names(name), want_given)


if __name__ == "__main__":
    unittest.main()
