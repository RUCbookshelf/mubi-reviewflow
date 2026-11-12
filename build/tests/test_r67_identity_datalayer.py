"""review67 身份与数据层修复钉子（双泳道评审 2026-11-12，identity/data-layer）。

覆盖 main@2226cad 评审报告中本泳道名下六项可钉行为的修复：

- **M-04（db.py _ensure_effect_schema）**：measure CHECK 重建 DDL 改拼
  _MEASURE_CHECK_SQL（measure_registry 单一事实源）。钉「注册表追加第 23 码
  后旧库升级收敛」：重建后的 CHECK 含新码、清缓存冷启动不再重复整表重建
  （schema_version 不再抬升）、新码行可保存——修前硬编码 22 码导致
  layout() 永不满足、每进程冷启动重跑一次重建且新码被 CHECK 拒绝。
- **M-05（dedup.py 模糊层资格）**：资格过滤改用 _identity_doi 口径——期刊
  样式 DOI（"(issn)" 整刊标识）视同缺 DOI 进模糊层。钉：双方只持样式 DOI
  的同题同作者异键记录产出 fuzzy 候选（修前零候选静默漏报）；真实 DOI 仍
  排除出模糊层（回归保护）。
- **M-06（normalize.py et al. 截断标记）**：三形态（逗号 'Smith JA, et al.'
  / 无逗号 'Smith J et al.' / 四实词 'John Smith et al.'）解析前剥离——
  姓氏/名与未截断形态一致；端到端同键同题不再 exact_key_conflict、异键
  同题进入 fuzzy 候选。
- **M-07（normalize.py 全角分隔符）**：normalize_authors 按全角逗号/分号
  拆分，first_author_surname 的首段提取同口径——'王小明，李华' 的第一作者
  是 '王小明' 而非整串；端到端同键合并 + 异键模糊候选。
- **L-07（dedup.py FUZZY_PAIR_LIMIT 截断误报）**：边数恰等于上限时不报
  「已截断」（事件序列与不设限逐条相同）；真截断仍显式警告。
- **L-09（dedup.py _norm_title 双口径统一）**：NFKC/casefold/标点折叠与
  normalize_title_candidate 同口径——CJK 全/半角标点变体标题同键合并、
  异键进入模糊候选；ß/SS casefold 钉。

L-06（migrate_overlong_keys 死存储）、L-08（seen_doi 死变量）、L-10
（SPEC.md 悬空锚点）为无行为差的清理项，由源码 grep 与既有回归套件覆盖
（本文件无独立钉子）。
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen import db as dbm
from coscreen import dedup as dedup_mod
from coscreen.dedup import find_duplicates, normalize_title_candidate
from coscreen.normalize import (
    first_author_given_names,
    first_author_surname,
    normalize_authors,
)
from coscreen.models import Article

PATTERN_DOI = "10.1002/(issn)1545-7249"


def _article(key: str, title: str, authors: str = "", doi: str = "",
             year: int | None = None) -> Article:
    return Article(zotero_key=key, title=title, authors=authors, doi=doi,
                   year=year)


# ---------------------------------------------------------------------------
# M-04：measure 注册表漂移后旧库整表重建收敛
# ---------------------------------------------------------------------------

class MeasureDriftConvergenceTests(unittest.TestCase):
    """第 23 码（FAKE_X）注入注册表后，旧库经 _ensure_effect_schema 收敛。"""

    def setUp(self):
        self._fixture = tempfile.TemporaryDirectory(prefix="rf_r67_measure_")
        self.addCleanup(self._fixture.cleanup)
        self.db_path = Path(self._fixture.name) / "shelf.db"
        dbm.init_db(self.db_path)  # 健康基线：现行 22 码建表

    def _codes23(self) -> tuple[str, ...]:
        return tuple(dbm.MEASURE_CODES) + ("FAKE_X",)

    def _check_sql(self, codes: tuple[str, ...]) -> str:
        # 与 db.py 导入期由 MEASURE_CODES 生成 _MEASURE_CHECK_SQL 同公式
        return ("measure TEXT NOT NULL CHECK(measure IN ("
                + ",".join(f"'{code}'" for code in codes) + "))")

    def _effects_ddl(self) -> str:
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='review_effects'").fetchone()
        return row[0] if row else ""

    def _schema_version(self) -> int:
        with closing(sqlite3.connect(self.db_path)) as conn:
            return int(conn.execute("PRAGMA schema_version").fetchone()[0])

    def test_rebuilt_check_follows_registry_and_converges(self):
        codes = self._codes23()
        with mock.patch.object(dbm, "MEASURE_CODES", codes), \
                mock.patch.object(dbm, "_MEASURE_CHECK_SQL",
                                  self._check_sql(codes)):
            # 进程内缓存清空 == 模拟新进程冷启动（探针 probe_measure_drift_
            # child.py 的 D3 场景）；不清缓存时 _connect 稳态快路径直接跳过
            # 自愈序列，重建不会发生。
            dbm._MIGRATED_SCHEMAS.clear()
            dbm.init_db(self.db_path)

            # D1：重建后的 CHECK 含第 23 码（修前硬编码 22 码 → False）
            ddl = self._effects_ddl()
            self.assertIn("'FAKE_X'", ddl, "重建 DDL 必须跟随注册表")
            missing = [c for c in codes if f"'{c}'" not in ddl]
            self.assertEqual(missing, [], "重建后的 CHECK 不得缺任何注册码")

            # D2/D3：再冷启动一次，layout() 应满足 → 不再整表重建
            # （schema_version 不抬升；修前每次冷启动重建一次、永不收敛）
            dbm._MIGRATED_SCHEMAS.clear()
            before = self._schema_version()
            dbm.init_db(self.db_path)
            after = self._schema_version()
            self.assertEqual(after, before,
                             "升级后冷启动不得再次触发整表重建")

            # D4：新码行可保存（修前被硬编码 CHECK 拒绝）
            with closing(dbm._connect(self.db_path)) as conn, conn:
                conn.execute(
                    "INSERT INTO review_studies (id,label) VALUES ('SX','x')")
                conn.execute(
                    "INSERT INTO articles (zotero_key,title) VALUES ('AX','x')")
                conn.execute(
                    "INSERT INTO review_effects (study_id,comparison,outcome,"
                    "timepoint,measure,estimate,se,source_key,selected) "
                    "VALUES ('SX','C','O','T','FAKE_X',1.0,0.5,'AX',1)")

    def test_current_registry_codes_still_pinned_in_ddl(self):
        """现行 22 码在新建库与重建库两条路径的 CHECK 中逐一在位。"""
        ddl = self._effects_ddl()
        for code in dbm.MEASURE_CODES:
            self.assertIn(f"'{code}'", ddl)


# ---------------------------------------------------------------------------
# M-05：模糊层资格过滤改用 _identity_doi 口径
# ---------------------------------------------------------------------------

class FuzzyEligibilityPatternDoiTests(unittest.TestCase):
    """期刊样式 DOI 视同缺 DOI 进模糊层；真实 DOI 仍排除（回归保护）。"""

    TITLE = "Digital reading habits of adolescents"

    def test_same_pattern_doi_pair_enters_fuzzy_layer(self):
        arts = [
            _article("EJ1", self.TITLE, "Doe, Jane", PATTERN_DOI),
            _article("EJ2", self.TITLE, "Doe, Jane", PATTERN_DOI),
        ]
        kept, report = find_duplicates(arts)
        self.assertEqual(len(kept), 2, "样式 DOI 不得在精确层合并")
        self.assertEqual(report.exact_pairs, [])
        # 修前：双方 DOI 字面非空 → 双双被排除出模糊候选扫描 → fuzzy=[]
        self.assertEqual([(p.kept_key, p.dup_key) for p in report.fuzzy_pairs],
                         [("EJ1", "EJ2")], "同题同作者异键必须出模糊候选")

    def test_pattern_doi_versus_no_doi_enters_fuzzy_layer(self):
        arts = [
            _article("EJ1", self.TITLE, "Doe, Jane", PATTERN_DOI),
            _article("PM1", self.TITLE, "Doe, Jane", ""),
        ]
        _, report = find_duplicates(arts)
        self.assertEqual(len(report.fuzzy_pairs), 1,
                         "样式 DOI 一侧视同缺 DOI，与空 DOI 一侧同入模糊层")

    def test_real_doi_records_stay_out_of_fuzzy_layer(self):
        arts = [
            _article("K1", self.TITLE, "Doe, Jane", "10.1000/real.a"),
            _article("K2", self.TITLE, "Doe, Jane", "10.1000/real.b"),
        ]
        kept, report = find_duplicates(arts)
        self.assertEqual(len(kept), 2)
        self.assertEqual(report.exact_pairs, [], "真实 DOI 不同不得合并")
        self.assertEqual(report.fuzzy_pairs, [],
                         "持真实身份 DOI 的记录仍排除出模糊层")

    def test_pattern_doi_same_key_conflict_still_reported(self):
        """样式 DOI 失去身份键效力后，同键不同题仍报 exact_key_conflict。"""
        arts = [
            _article("K1", "First article", "Doe, Jane", PATTERN_DOI),
            _article("K1", "Second article", "Doe, Jane", PATTERN_DOI),
        ]
        kept, report = find_duplicates(arts)
        self.assertEqual(len(kept), 2)
        self.assertEqual([p.method for p in report.key_conflict_pairs],
                         ["exact_key_conflict"])
        # 标题不同 → 即便进入模糊层也不出候选（token_set_ratio < 90）
        self.assertEqual(report.fuzzy_pairs, [])


# ---------------------------------------------------------------------------
# M-06：'et al.' 截断标记三形态
# ---------------------------------------------------------------------------

class EtAlMarkerTests(unittest.TestCase):
    """et al. 标记不参与姓名解析：三形态与未截断形态同姓氏/名。"""

    def test_comma_form_keeps_surname_and_given(self):
        # 修前：surname 'smith ja' / given 'et al'
        self.assertEqual(first_author_surname("Smith JA, et al."), "smith")
        self.assertEqual(first_author_given_names("Smith JA, et al."), "ja")

    def test_commaless_form_keeps_surname(self):
        # 修前：'Smith J et al.' → 姓氏损坏成 'smith j et'
        self.assertEqual(first_author_surname("Smith J et al."), "smith")
        self.assertEqual(first_author_given_names("Smith J et al."), "j")
        self.assertEqual(first_author_surname("Smith J et al"), "smith")

    def test_four_word_form_not_institutional(self):
        # 修前：'John Smith et al.' 四实词被误判机构作者整串比较
        self.assertEqual(first_author_surname("John Smith et al."), "smith")
        self.assertEqual(first_author_given_names("John Smith et al."), "john")

    def test_et_al_alone_yields_empty(self):
        self.assertEqual(first_author_surname("et al."), "")
        self.assertEqual(first_author_given_names("Smith, et al."), "")

    def test_names_containing_et_or_al_unaffected(self):
        self.assertEqual(first_author_surname("Smith, Al"), "smith")
        self.assertEqual(first_author_surname("Wu, Jianet"), "wu")
        self.assertEqual(first_author_surname("Bertram, James"), "bertram")

    def test_same_key_truncated_author_merges(self):
        """端到端：同键同题，一侧作者被 et al. 截断 → 仍判同一文献合并。"""
        arts = [
            _article("K1", "A title about screening", "Smith JA, et al."),
            _article("K1", "A title about screening", "Smith JA"),
        ]
        # 修前：surname 'smith ja' vs 'smith' ratio 76.92 < 80 → 判可证不同
        kept, report = find_duplicates(arts)
        self.assertEqual(len(kept), 1)
        self.assertEqual([p.method for p in report.exact_pairs], ["exact_key"])
        self.assertEqual(report.key_conflict_pairs, [])

    def test_different_keys_truncated_author_yields_fuzzy(self):
        """端到端：异键同题，作者截断形态 vs 完整形态 → 模糊候选而非漏报。"""
        for truncated, full in (
                ("Smith JA, et al.", "Smith JA"),
                ("Smith J et al.", "Smith J"),
                ("John Smith et al.", "John Smith")):
            with self.subTest(truncated=truncated):
                arts = [
                    _article("K1", "A title about screening", truncated),
                    _article("K2", "A title about screening", full),
                ]
                _, report = find_duplicates(arts)
                self.assertEqual(
                    [(p.kept_key, p.dup_key) for p in report.fuzzy_pairs],
                    [("K1", "K2")],
                    f"{truncated!r} vs {full!r} 不得静默漏报")


# ---------------------------------------------------------------------------
# M-07：全角逗号 CJK 作者串分隔
# ---------------------------------------------------------------------------

class FullWidthSeparatorTests(unittest.TestCase):
    """全角逗号/分号是作者分隔符；ASCII 逗号仍是姓名内分隔符。"""

    def test_normalize_authors_splits_full_width_separators(self):
        self.assertEqual(normalize_authors("王小明，李华"), "王小明; 李华")
        self.assertEqual(normalize_authors("王小明；李华"), "王小明; 李华")
        self.assertEqual(normalize_authors("王小明，李华，张三"),
                         "王小明; 李华; 张三")
        # 回归：ASCII 分号与 Zotero "; " 约定不变
        self.assertEqual(normalize_authors("Doe, Jane; Roe, John"),
                         "Doe, Jane; Roe, John")

    def test_ascii_comma_is_not_an_author_separator(self):
        self.assertEqual(normalize_authors("Smith, John"), "Smith, John")

    def test_first_author_surname_takes_first_segment(self):
        # 修前：'王小明，李华' 整串成为姓氏（与 '王小明' ratio 66.67 < 80）
        self.assertEqual(first_author_surname("王小明，李华"), "王小明")
        self.assertEqual(first_author_surname("王小明；李华"), "王小明")
        self.assertEqual(first_author_surname("王小明，李华，张三"), "王小明")
        self.assertEqual(first_author_given_names("王小明，李华"), "")

    def test_same_key_full_width_vs_semicolon_merges(self):
        """端到端：同键同题，全角逗号串 vs 分号串 → 同一文献合并。"""
        arts = [
            _article("K1", "数字阅读与青少年发展", "王小明，李华"),
            _article("K1", "数字阅读与青少年发展", "王小明; 李华"),
        ]
        # 修前：整串姓氏 vs '王小明' 判可证不同 → exact_key_conflict
        kept, report = find_duplicates(arts)
        self.assertEqual(len(kept), 1)
        self.assertEqual([p.method for p in report.exact_pairs], ["exact_key"])
        self.assertEqual(report.key_conflict_pairs, [])

    def test_different_keys_full_width_string_yields_fuzzy(self):
        """端到端：异键同题，'王小明，李华' vs '王小明' → 模糊候选而非漏报。"""
        arts = [
            _article("K1", "数字阅读与青少年发展", "王小明，李华"),
            _article("K2", "数字阅读与青少年发展", "王小明"),
        ]
        _, report = find_duplicates(arts)
        self.assertEqual([(p.kept_key, p.dup_key) for p in report.fuzzy_pairs],
                         [("K1", "K2")])


# ---------------------------------------------------------------------------
# L-07：FUZZY_PAIR_LIMIT 截断误报
# ---------------------------------------------------------------------------

class FuzzyPairLimitTests(unittest.TestCase):

    @staticmethod
    def _same_template_articles(n: int) -> list[Article]:
        return [
            _article(f"K{i}", f"Filler paper number {i} about topic {i}",
                     "Doe, Jane")
            for i in range(1, n + 1)
        ]

    def test_edges_exactly_at_limit_report_no_truncation(self):
        arts = self._same_template_articles(6)   # 生成树边 = 6 - 1 = 5
        with mock.patch.object(dedup_mod, "FUZZY_PAIR_LIMIT", 5):
            kept, report = find_duplicates(arts)
        # 修前：len>=LIMIT 即置 truncated → 边数恰等于上限时误报「已截断」
        self.assertEqual(len(report.fuzzy_pairs), 5)
        self.assertEqual(report.note, "",
                         "边数恰等于上限时事件序列与不设限逐条相同，不得报截断")

    def test_real_truncation_still_reported(self):
        arts = self._same_template_articles(6)
        with mock.patch.object(dedup_mod, "FUZZY_PAIR_LIMIT", 4):
            kept, report = find_duplicates(arts)
        self.assertEqual(len(report.fuzzy_pairs), 4)  # 第 5 条边被真实丢弃
        self.assertIn("已截断", report.note)
        self.assertIn("4", report.note)


# ---------------------------------------------------------------------------
# L-09：_norm_title 与 normalize_title_candidate 双口径统一
# ---------------------------------------------------------------------------

class NormTitleUnificationTests(unittest.TestCase):

    def test_full_width_punctuation_titles_equal(self):
        # 修前：只做小写+空白折叠 → '（综述）' vs '(综述)' 判不等
        self.assertEqual(dedup_mod._norm_title("数字阅读（综述）"),
                         dedup_mod._norm_title("数字阅读(综述)"))
        self.assertEqual(dedup_mod._norm_title("数字阅读（综述）"),
                         normalize_title_candidate("数字阅读(综述)"))

    def test_casefold_parity(self):
        self.assertEqual(dedup_mod._norm_title("Eine Straße Studie"),
                         dedup_mod._norm_title("EINE STRASSE STUDIE"))

    def test_same_key_punctuation_variant_merges(self):
        """端到端：同键同题同作者，标题仅全/半角标点差 → 合并非冲突。"""
        arts = [
            _article("K1", "数字阅读（综述）", "王小明"),
            _article("K1", "数字阅读(综述)", "王小明"),
        ]
        # 修前：标题规范化后不完全相等 → exact_key_conflict（误判不同文献）
        kept, report = find_duplicates(arts)
        self.assertEqual(len(kept), 1)
        self.assertEqual([p.method for p in report.exact_pairs], ["exact_key"])
        self.assertEqual(report.key_conflict_pairs, [])

    def test_different_keys_punctuation_variant_yields_fuzzy(self):
        """端到端：异键无 DOI，标题仅标点差 → 模糊候选（修前 ratio 75 零候选）。"""
        arts = [
            _article("K1", "数字阅读（综述）", "王小明"),
            _article("K2", "数字阅读(综述)", "王小明"),
        ]
        _, report = find_duplicates(arts)
        self.assertEqual([(p.kept_key, p.dup_key) for p in report.fuzzy_pairs],
                         [("K1", "K2")])

    def test_word_difference_still_conflicts(self):
        """回归保护：一词之差（R6-D1）不因标点折叠而合并。"""
        arts = [
            _article("K1", "Effects in women", "Doe, Jane"),
            _article("K1", "Effects in men", "Doe, Jane"),
        ]
        kept, report = find_duplicates(arts)
        self.assertEqual(len(kept), 2)
        self.assertEqual([p.method for p in report.key_conflict_pairs],
                         ["exact_key_conflict"])
        self.assertEqual(report.exact_pairs, [])


if __name__ == "__main__":
    unittest.main()
