"""2.1.4 去重引擎（U1）泳道回归测试 —— 复现夜间首审的实测手法。

对应 core_audit_findings.md 单元 1 的 13 条结论（U1-P7 为验证性结论，无需
修复，此处保留传递闭包/重排确定性的守护断言）。所有行为断言都用真实
parse_ris / find_duplicates / db.upsert_articles / CLI 子进程复现首审证据：

- U1-P1/P10：模糊证据（仅标题相似）不再静默自动合并，只报候选；
- U1-P2/P11：第一作者门识别两种姓名顺序、剥 van/de 助词、空作者不通过、
  机构作者整串比；
- U1-P3：键相等先校验 DOI/标题冲突，冲突走候选并消歧键；
- U1-P4/P12：duplicates 计数改 total-unique 口径，PRISMA 勾稽恒成立；
- U1-P5/P8/P9：normalize_doi 删 Cf 类不可见字符、NFKC 折叠全角、剥
  scheme-less doi.org 前缀、去尾部标点，dedup._norm_doi 统一走它；
- U1-P6：auto 键掺入第一作者与年份；CLI 导入前调用键消歧；
- U1-P13：extract_year 保守化（括号/句末优先，无法确定返回 None）。

既有库兼容性：键生成算法只影响新导入（build_zotero_key 仅在解析时调用），
stabilize_conflicting_keys 对"与库内同键且兼容"的条目保留原键走更新路径，
不重写任何已有主键——由 test_stabilize_keeps_compatible_db_key 等钉住。
"""

from __future__ import annotations

from pathlib import Path
import gc
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout, redirect_stderr
from io import StringIO

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen import db as db_mod
from coscreen.dedup import (
    DedupReport,
    DuplicatePair,
    find_duplicates,
    stabilize_conflicting_keys,
    _norm_doi,
)
from coscreen.living_review import process_incremental_articles, process_incremental_update
from coscreen.models import Article, compute_content_hash
from coscreen.normalize import (
    build_zotero_key,
    extract_year,
    first_author_surname,
    normalize_doi,
)
from coscreen.parsers import parse_file
from coscreen.stats.prisma import prisma_from_merge


def _article(key: str, title: str, authors: str = "", doi: str = "",
             year: int | None = None, raw: dict | None = None) -> Article:
    return Article(zotero_key=key, title=title, authors=authors, doi=doi,
                   year=year, raw=raw if raw is not None else {})


def _write_ris(path: Path, records: list[dict]) -> None:
    """写一个最小 RIS 文件：records 每项为 {au,ti,py,do,id} 的可选键。"""
    lines: list[str] = []
    for rec in records:
        lines.append("TY  - JOUR")
        if rec.get("au"):
            lines.append(f"AU  - {rec['au']}")
        if rec.get("ti"):
            lines.append(f"TI  - {rec['ti']}")
        if rec.get("py"):
            lines.append(f"PY  - {rec['py']}")
        if rec.get("do"):
            lines.append(f"DO  - {rec['do']}")
        if rec.get("id"):
            lines.append(f"ID  - {rec['id']}")
        lines.append("ER  -\n")
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# U1-P5 / U1-P8 / U1-P9：DOI 规范化
# ---------------------------------------------------------------------------

class NormalizeDoiTests(unittest.TestCase):
    """normalize_doi 边界与 dedup._norm_doi 统一口径（首审实测值逐条钉住）。"""

    def test_fullwidth_and_prefix_variants_fold_to_canonical(self):
        cases = {
            "DOI：10.1000/abc": "10.1000/abc",          # 全角冒号（首审原样保留）
            "doi：10.1000/abc": "10.1000/abc",
            "１０.１０００/abc": "10.1000/abc",           # 全角数字（首审原样保留）
            "doi.org/10.1000/abc": "10.1000/abc",       # scheme-less（首审原样保留）
            "dx.doi.org/10.5555/fw": "10.5555/fw",
            "https://www.doi.org/10.1000/abc": "10.1000/abc",
            " https://doi.org/10.1000/ABC ": "10.1000/abc",
            "https://doi.org/doi:10.1000/abc": "10.1000/abc",  # 双前缀
            "10.1000/abc.": "10.1000/abc",              # 尾部句点
            "10.1000/abc,": "10.1000/abc",
            "doi: 10.1000 /abc": "10.1000/abc",         # 内部空白
            "10.9/x": "10.9/x",
            "": "",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_doi(raw), expected)

    def test_invisible_cf_characters_removed(self):
        # U+200B 零宽空格 / U+200D 零宽连接符 / U+FEFF BOM / U+00AD 软连字符
        # 均为 Cf 类，\s 不匹配；首审实测它们原样穿透两层去重。
        for ch in ("\u200b", "\u200d", "\ufeff", "\u00ad", "\u2060"):
            with self.subTest(ch=hex(ord(ch))):
                self.assertEqual(normalize_doi(f"10.1234/abc{ch}"), "10.1234/abc")
                self.assertEqual(normalize_doi(f"{ch}10.1234/abc"), "10.1234/abc")

    def test_trailing_unbalanced_bracket_stripped_balanced_kept(self):
        self.assertEqual(normalize_doi("10.1000/(abc).def)"), "10.1000/(abc).def")
        self.assertEqual(normalize_doi("10.1000/abc]"), "10.1000/abc")

    def test_dedup_norm_doi_delegates_to_normalize_doi(self):
        for raw in ("https://doi.org/10.1000/ABC", "doi.org/10.9/x", "10.1/x​"):
            with self.subTest(raw=raw):
                self.assertEqual(_norm_doi(raw), normalize_doi(raw))
        self.assertEqual(_norm_doi("https://doi.org/10.1000/ABC"), "10.1000/abc")

    def test_doi_variants_now_merge_in_exact_layer(self):
        # 首审 probe2：URL 形态 vs 裸形态曾返回 kept=2、0 对。
        arts = [
            _article("K1", "Some title", "Smith, John", "https://doi.org/10.1000/ABC"),
            _article("K2", "Some title", "Smith, John", "10.1000/ABC"),
        ]
        kept, report = find_duplicates(arts)
        self.assertEqual(len(kept), 1)
        self.assertEqual([p.method for p in report.exact_pairs], ["exact_doi"])

    def test_zero_width_doi_penetrating_both_layers_fixed(self):
        # 首审 U1-P8 实测：同标题同作者、DOI 仅差尾部 ZWSP → unique=2 无任何
        # 标记。规范化后精确层命中。
        arts = [
            _article("K1", "Same title", "Smith, John", "10.1234/abc\u200b"),
            _article("K2", "Same title", "Smith, John", "10.1234/abc"),
        ]
        kept, report = find_duplicates(arts)
        self.assertEqual(report.unique, 1)
        self.assertTrue(any(p.method == "exact_doi" for p in report.exact_pairs))


# ---------------------------------------------------------------------------
# U1-P2 / U1-P11：第一作者门
# ---------------------------------------------------------------------------

class FirstAuthorTokenTests(unittest.TestCase):
    def test_order_and_particle_handling(self):
        pairs_equal = [
            ("Smith, John", "John Smith"),          # 首审 P2(b)：曾漏配
            ("Smith, John", "Smith J"),
            ("von Neumann, John", "Neumann, J."),   # 首审 P11：曾漏配
            ("van der Berg, J.", "Berg, J."),
            ("de la Cruz, Maria", "Maria de la Cruz"),
            ("van Dijk", "Dijk, A."),               # "van Dijk" 无逗号
        ]
        for left, right in pairs_equal:
            with self.subTest(left=left, right=right):
                self.assertEqual(first_author_surname(left), first_author_surname(right))

    def test_distinct_particles_distinguishable(self):
        # 首审 P2(c)：'van der Berg' 与 'van Dijk' 曾都归一为 'van'（100 放行）。
        self.assertNotEqual(first_author_surname("van der Berg, J."),
                            first_author_surname("van Dijk, A."))
        self.assertEqual(first_author_surname("van der Berg, J."), "berg")
        self.assertEqual(first_author_surname("van Dijk, A."), "dijk")

    def test_institutional_authors_compared_whole(self):
        self.assertEqual(first_author_surname("The Cochrane Collaboration"),
                         "the cochrane collaboration")
        self.assertEqual(first_author_surname("World Health Organization"),
                         "world health organization")
        # 机构 vs 人名姓氏不再坍缩成 'the'/'collaboration' 一词
        self.assertNotEqual(first_author_surname("The Cochrane Collaboration"),
                            first_author_surname("Cochrane, Tom"))

    def test_empty_authors_return_empty_string(self):
        self.assertEqual(first_author_surname(""), "")
        self.assertEqual(first_author_surname(None), "")
        self.assertEqual(first_author_surname("; "), "")

    def test_fuzzy_gate_empty_authors_not_merged(self):
        # 首审 P2(a)/P11：双空作者 fuzz.ratio('','')=100 仅凭标题合并；单空
        # 则漏配。现在任一侧缺失都不产生候选合并（不自动合并、也不建议合并）。
        for authors in ("", "Smith, John"):
            arts = [
                _article("K1", "Same title both empty gate", authors),
                _article("K2", "Same title both empty gate", ""),
            ]
            with self.subTest(authors=authors):
                _, report = find_duplicates(arts)
                self.assertEqual(report.fuzzy_pairs, [])

    def test_fuzzy_gate_order_flip_now_matches_as_candidate(self):
        # 首审 P11：'Smith, John' vs 'John Smith' 同标题曾因 ratio=22 漏配；
        # 现在作者门通过、进入候选（不自动合并）。
        arts = [
            _article("K1", "Identical Title For Author Order Test", "Smith, John"),
            _article("K2", "Identical Title For Author Order Test", "John Smith"),
        ]
        kept, report = find_duplicates(arts)
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(report.fuzzy_pairs), 1)
        self.assertEqual(report.fuzzy_pairs[0].kept_key, "K1")
        self.assertEqual(report.fuzzy_pairs[0].dup_key, "K2")


# ---------------------------------------------------------------------------
# U1-P1 / U1-P10：模糊层只出候选，不再自动合并
# ---------------------------------------------------------------------------

class FuzzyCandidatesOnlyTests(unittest.TestCase):
    def test_companion_studies_not_merged(self):
        # 首审实测：token_set_ratio('...in women','...in men')=98.41 被自动
        # 合并，men 论文不再进入筛选。现在两条都保留，仅列候选。
        arts = [
            _article("K1", "Effects of drug X on mortality in women", "Wang, Wei", year=2005),
            _article("K2", "Effects of drug X on mortality in men", "Wang, Wei", year=2006),
        ]
        kept, report = find_duplicates(arts)
        self.assertEqual([a.zotero_key for a in kept], ["K1", "K2"])
        self.assertEqual(report.unique, 2)
        self.assertEqual(len(report.fuzzy_pairs), 1)
        self.assertEqual(report.fuzzy_pairs[0].method, "fuzzy")

    def test_word_order_and_subset_titles_are_candidates_only(self):
        # 首审实测词序反转=100 / token 子集=100 曾被合并。
        evidence_pairs = [
            ("Impact of diabetes on blood pressure control",
             "Impact of blood pressure control on diabetes"),
            ("deep learning review",
             "a review of deep learning methods"),
        ]
        for title_a, title_b in evidence_pairs:
            arts = [
                _article("K1", title_a, "Li, Ming"),
                _article("K2", title_b, "Li, Ming"),
            ]
            with self.subTest(titles=(title_a, title_b)):
                kept, report = find_duplicates(arts)
                self.assertEqual(report.unique, 2)
                self.assertEqual(len(report.fuzzy_pairs), 1)

    def test_identical_titles_also_require_human_confirmation(self):
        # 完全同标题同作者（无 DOI/键证据）也只是候选：自动合并仅保留
        # 规范化 DOI 相等与不冲突的键相等两类证据。
        arts = [
            _article("K1", "Exactly the same title", "Smith, John"),
            _article("K2", "Exactly the same title", "Smith, John"),
        ]
        kept, report = find_duplicates(arts)
        self.assertEqual(report.unique, 2)
        self.assertEqual(len(report.fuzzy_pairs), 1)

    def test_template_titles_not_collapsed(self):
        # 首审 probe10：70 条 'Filler paper number N about topic N' 模板标题
        # 曾合并成 1 组。取 8 条验证：全部保留、仅出候选。
        arts = [
            _article(f"K{i}", f"Filler paper number {i} about topic {i}", "Doe, Jane")
            for i in range(1, 9)
        ]
        kept, report = find_duplicates(arts)
        self.assertEqual(report.unique, 8)
        self.assertTrue(all(p.method == "fuzzy" for p in report.fuzzy_pairs))


# ---------------------------------------------------------------------------
# U1-P3：键冲突校验与消歧键
# ---------------------------------------------------------------------------

class ExactKeyConflictTests(unittest.TestCase):
    def test_same_key_different_doi_conflicts(self):
        # 首审实测：RIS 'ID - 1' 两条不同文献曾判 exact_key 且 CLI 覆盖丢行。
        arts = [
            _article("1", "Metformin and renal outcomes in type 2 diabetes",
                     "Smith, John", "10.1000/aaa"),
            _article("1", "Aspirin and cardiovascular outcomes in the elderly",
                     "Jones, Anna", "10.1000/bbb"),
        ]
        kept, report = find_duplicates(arts)
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(report.key_conflict_pairs), 1)
        self.assertEqual(report.key_conflict_pairs[0].method, "exact_key_conflict")
        self.assertEqual(report.exact_pairs, [])

    def test_same_key_case_variant_doi_merges(self):
        arts = [
            _article("REAL1", "Title One", "Smith, John", "10.5555/dup"),
            _article("REAL1", "Title One", "Smith, John", "10.5555/DUP "),
        ]
        kept, _ = find_duplicates(arts)
        self.assertEqual(len(kept), 1)

    def test_same_key_title_adjudicates_when_doi_absent(self):
        merged = [
            _article("K1", "Alpha beta gamma study"),
            _article("K1", "Alpha beta gamma study"),          # 同标题 → 合并
        ]
        kept, report = find_duplicates(merged)
        self.assertEqual(len(kept), 1)
        self.assertEqual(report.key_conflict_pairs, [])

        conflict = [
            _article("K1", "Alpha beta gamma study"),
            _article("K1", "Completely different subject matter"),  # 相似度不足
        ]
        kept, report = find_duplicates(conflict)
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(report.key_conflict_pairs), 1)

    def test_stabilize_renames_conflicting_batch_keys_deterministically(self):
        arts = [
            _article("1", "Title A", "Smith, John", "10.1000/aaa"),
            _article("1", "Title B", "Jones, Anna", "10.1000/bbb"),
        ]
        _, report = find_duplicates(arts)
        renames = stabilize_conflicting_keys(
            arts, report.exact_pairs + report.fuzzy_pairs + report.key_conflict_pairs)
        self.assertEqual(len(renames), 1)
        self.assertEqual(arts[0].zotero_key, "1")
        self.assertTrue(arts[1].zotero_key.startswith("1-"))
        # 确定性：同一输入重建得到同一后缀（重复导入稳定）
        again = [
            _article("1", "Title A", "Smith, John", "10.1000/aaa"),
            _article("1", "Title B", "Jones, Anna", "10.1000/bbb"),
        ]
        _, report2 = find_duplicates(again)
        stabilize_conflicting_keys(again, report2.exact_pairs + report2.fuzzy_pairs + report2.key_conflict_pairs)
        self.assertEqual(again[1].zotero_key, arts[1].zotero_key)
        # 冲突对键已同步刷新
        self.assertEqual(report.key_conflict_pairs[0].dup_key, arts[1].zotero_key)

    def test_stabilize_member_follows_renamed_group(self):
        # A(1,d1) 与 B(1,d2) 键冲突；C(2,d2) 与 B 同 DOI 合并。B 组消歧后
        # C 保留自己的键（2）作为重复行标记，不覆盖 A 的行。
        arts = [
            _article("1", "Title A", "Smith, John", "10.1/d1"),
            _article("1", "Title B", "Jones, Anna", "10.1/d2"),
            _article("2", "Title B two", "Jones, Anna", "10.1/d2"),
        ]
        _, report = find_duplicates(arts)
        stabilize_conflicting_keys(
            arts, report.exact_pairs + report.fuzzy_pairs + report.key_conflict_pairs)
        self.assertEqual(arts[0].zotero_key, "1")
        self.assertTrue(arts[1].zotero_key.startswith("1-"))
        self.assertEqual(arts[2].zotero_key, "2")
        doi_pair = next(p for p in report.exact_pairs if p.method == "exact_doi")
        self.assertEqual(doi_pair.kept_key, arts[1].zotero_key)
        self.assertEqual(doi_pair.dup_key, "2")

    def test_stabilize_keeps_compatible_db_key_for_update_path(self):
        # 与库内同键且 DOI 相等（重导同一文献）：保留原键走 upsert 更新，
        # 既有库主键不被重写。
        db_art = _article("WM1", "Effects of drug X on mortality in women",
                          "Wang, Wei", "10.9/wm1")
        incoming = [_article("WM1", "Effects of drug X on mortality in women",
                             "Wang, Wei", "10.9/wm1")]
        renames = stabilize_conflicting_keys(incoming, [], known=[db_art])
        self.assertEqual(renames, [])
        self.assertEqual(incoming[0].zotero_key, "WM1")

    def test_stabilize_disambiguates_conflicting_db_key(self):
        # 跨批次：库内 WM1 与新文件 WM1 是不同文献（DOI/标题都不符）→
        # 新条目加后缀，不再覆盖库内行。
        db_art = _article("WM1", "Effects of drug X on mortality in women",
                          "Wang, Wei")
        incoming = [_article("WM1", "A totally different study on statins",
                             "Doe, Jane", "10.1000/zzz")]
        renames = stabilize_conflicting_keys(incoming, [], known=[db_art])
        self.assertEqual(len(renames), 1)
        self.assertEqual(renames[0][0], "WM1")
        self.assertTrue(incoming[0].zotero_key.startswith("WM1-"))

    def test_stabilize_keeps_compatible_known_member_key(self):
        # 评审反馈（R2）：库内 K2 与导入 K2 是同一文献（DOI 相等），导入
        # 批内 K1+K2 同 DOI 合并——组成员不得被改名为组代表键（否则代表
        # 行被成员内容覆盖、重复关系丢失）。兼容的库内同键成员保留原键，
        # 由 upsert 更新既有行并打 is_duplicate_of 标记。
        db_art = _article("K2", "Title B", "Doe, Jane", "10.9/same")
        arts = [
            _article("K1", "Title A", "Smith, Ann", "10.9/same"),
            _article("K2", "Title B refined", "Doe, Jane", "10.9/same"),
        ]
        _, report = find_duplicates(arts)
        renames = stabilize_conflicting_keys(arts, report.exact_pairs, known=[db_art])
        self.assertEqual(renames, [])
        self.assertEqual([a.zotero_key for a in arts], ["K1", "K2"])
        self.assertEqual((report.exact_pairs[0].kept_key, report.exact_pairs[0].dup_key),
                         ("K1", "K2"))


# ---------------------------------------------------------------------------
# U1-P4 / U1-P12：重复计数口径
# ---------------------------------------------------------------------------

class DuplicateCountingTests(unittest.TestCase):
    def test_same_doi_and_key_counted_once_by_record(self):
        # 首审 probe3/probe8：两条 key 与 DOI 完全相同的记录曾产生
        # exact_doi+exact_key 两条事件、n_duplicates()=2。
        arts = [
            _article("K1", "Title X", "Smith, John", "10.1000/x"),
            _article("K1", "Title X", "Smith, John", "10.1000/x"),
        ]
        kept, report = find_duplicates(arts)
        self.assertEqual(len(report.exact_pairs), 2)  # 事件保留（透明）
        self.assertEqual(report.n_duplicates(), 1)    # 记录口径
        self.assertEqual(report.n_duplicates(), report.total - report.unique)

    def test_prisma_flow_identity_invariant_holds(self):
        # 首审实测 prisma_from_merge 曾返回 identified=2, duplicates=2,
        # screened=1（2-2 != 1）。现在 identified - duplicates == screened。
        import pandas as pd

        arts = [
            _article("K1", "Title X", "Smith, John", "10.1000/x"),
            _article("K1", "Title X", "Smith, John", "10.1000/x"),
        ]
        _, report = find_duplicates(arts)
        merged = pd.DataFrame({"status": ["agree"], "decision_A": ["include"]})
        flow = prisma_from_merge(merged, report)
        self.assertEqual(flow.identified - flow.duplicates_removed, flow.screened)
        self.assertEqual((flow.identified, flow.duplicates_removed, flow.screened), (2, 1, 1))

    def test_n_duplicates_never_negative(self):
        report = DedupReport(total=0, unique=0)
        self.assertEqual(report.n_duplicates(), 0)


# ---------------------------------------------------------------------------
# U1-P6：auto 键掺入第一作者与年份
# ---------------------------------------------------------------------------

class BuildZoteroKeyTests(unittest.TestCase):
    def test_same_title_different_authors_get_distinct_keys(self):
        # 首审 probe9：两条无 ID 无 DOI 的 'Editorial' 曾同为 auto-ab84b05d60c2。
        content_hash = compute_content_hash("", "Editorial")
        key_smith = build_zotero_key({}, content_hash, authors="Smith, John", year=2020)
        key_jones = build_zotero_key({}, content_hash, authors="Jones, Anna", year=2021)
        self.assertTrue(key_smith.startswith("auto-"))
        self.assertNotEqual(key_smith, key_jones)

    def test_same_title_same_author_different_year_distinct(self):
        content_hash = compute_content_hash("", "Editorial")
        self.assertNotEqual(
            build_zotero_key({}, content_hash, authors="Smith, John", year=2020),
            build_zotero_key({}, content_hash, authors="Smith, John", year=2021),
        )

    def test_raw_lookup_ris_style_record(self):
        # rispy 记录：authors 列表 + year 字段；不传显式参数也能掺入作者/年份。
        content_hash = compute_content_hash("", "Editorial")
        key = build_zotero_key({"authors": ["Smith, John"], "year": "2020"}, content_hash)
        self.assertEqual(
            key,
            build_zotero_key({}, content_hash, authors="Smith, John", year=2020),
        )
        self.assertNotEqual(
            key, build_zotero_key({"authors": ["Jones, Anna"], "year": "2020"}, content_hash)
        )

    def test_deterministic_and_source_key_preferred(self):
        content_hash = compute_content_hash("", "Editorial")
        self.assertEqual(
            build_zotero_key({}, content_hash, authors="Smith, John", year=2020),
            build_zotero_key({}, content_hash, authors="Smith, John", year=2020),
        )
        self.assertEqual(build_zotero_key({"id": "SOURCE1"}, content_hash), "SOURCE1")

    def test_ris_same_title_different_authors_both_imported(self):
        # 首审 probe9 端到端：ed.ris 两条同名 'Editorial' 曾只入库 1 条。
        with tempfile.TemporaryDirectory(prefix="rf_u1p6_") as tmp:
            ris = Path(tmp) / "ed.ris"
            _write_ris(ris, [
                {"au": "Smith, John", "ti": "Editorial", "py": "2020"},
                {"au": "Jones, Anna", "ti": "Editorial", "py": "2021"},
            ])
            articles = parse_file(ris)
            self.assertNotEqual(articles[0].zotero_key, articles[1].zotero_key)
            db = str(Path(tmp) / "ed.db")
            db_mod.init_db(db)
            _, report = find_duplicates(articles)
            db_mod.upsert_articles(db, articles, report.exact_pairs)
            with closing(sqlite3.connect(db)) as conn:
                rows = conn.execute(
                    "SELECT zotero_key, authors FROM articles").fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(report.unique, 2)


# ---------------------------------------------------------------------------
# U1-P13：extract_year 保守化
# ---------------------------------------------------------------------------

class ExtractYearTests(unittest.TestCase):
    def test_evidence_cases(self):
        cases = {
            "Reprint of the 1905 original (2012)": 2012,   # 首审取错为 1905
            "Volume 1850, issued 2019": 2019,              # 首审取错为 1850
            "2020-12-01": 2020,
            "April 2021": 2021,
            "1999/05": 1999,
            "© 2011 Springer": 2011,
            "2020": 2020,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(extract_year(text), expected)

    def test_undeterminable_returns_none(self):
        # 多个候选且无括号/句末信号 → 宁缺毋错，不再取首个数字。
        self.assertIsNone(extract_year("1905 original 2012 reprint"))
        self.assertIsNone(extract_year("serial numbers 12345 and 67890"))
        self.assertIsNone(extract_year(""))
        self.assertIsNone(extract_year(None))


# ---------------------------------------------------------------------------
# U1-P7（验证性守护）：传递闭包与重排确定性
# ---------------------------------------------------------------------------

class UnionFindGuardsTests(unittest.TestCase):
    def test_transitive_closure_keeps_first_record(self):
        arts = [
            _article("K1", "Alpha beta gamma"),
            _article("K1", "Alpha beta gamma", doi="10.1/d2"),   # 键合并（无 DOI 冲突）
            _article("K3", "Totally other", doi="10.1/d2"),      # DOI 合并
        ]
        kept, report = find_duplicates(arts)
        self.assertEqual([a.zotero_key for a in kept], ["K1"])
        self.assertEqual(
            [(p.kept_key, p.dup_key, p.method) for p in report.exact_pairs],
            [("K1", "K1", "exact_key"), ("K1", "K3", "exact_doi")],
        )

    def test_reordering_is_deterministic(self):
        arts = [
            _article("K1", "Alpha beta gamma"),
            _article("K1", "Alpha beta gamma", doi="10.1/d2"),
            _article("K3", "Totally other", doi="10.1/d2"),
        ]
        kept, _ = find_duplicates(list(reversed(arts)))
        self.assertEqual([a.zotero_key for a in kept], ["K3"])


# ---------------------------------------------------------------------------
# CLI 与活体综述对接（真实子进程/真实管线）
# ---------------------------------------------------------------------------

def _run_cli_subprocess(args: list[str]) -> subprocess.CompletedProcess:
    """真实子进程链路：``python -m coscreen``（端到端锚专用，冷启动 ~2s）。"""
    return subprocess.run(
        [sys.executable, "-m", "coscreen", *args],
        capture_output=True, text=True, cwd=str(Path(__file__).resolve().parents[2]),
    )


def _run_cli(args: list[str]) -> subprocess.CompletedProcess:
    """同进程直调 CLI 主入口（参数解析/stdout/stderr/退出码与子进程一致）。

    性能（集成轮返修）：全套 pytest 曾达 82-84s，超出返修 runner 的墙钟
    预算（实测在 67.74s/第 163 个测试处被中断，exit=2 Interrupted）；
    8 次 CLI 测试每次付 ~2s 的 Python+pandas+rispy 子进程冷启动。改为
    直调后参数解析与输出路径不变；真实子进程链路由
    test_cli_subprocess_end_to_end_anchor 保留一条端到端锚。
    """
    from coscreen import cli as cli_module

    buf_out, buf_err = StringIO(), StringIO()
    with redirect_stdout(buf_out), redirect_stderr(buf_err):
        try:
            code = cli_module.main(list(args))
        except SystemExit as exc:  # main 约定返回退出码；防御性兼容
            code = int(exc.code or 0)
    return subprocess.CompletedProcess(
        args, int(code), buf_out.getvalue(), buf_err.getvalue()
    )


class CliImportSemanticsTests(unittest.TestCase):
    def test_cli_subprocess_end_to_end_anchor(self):
        # 端到端锚：_run_cli 已改为同进程直调（全套时长超出返修 runner 墙钟
        # 预算），此处保留一条真实 `python -m coscreen` 子进程链路，并断言
        # 直调与子进程在退出码/关键输出/落库行为上一致。
        with tempfile.TemporaryDirectory(prefix="rf_u1cli_e2e_") as tmp:
            rows = [
                {"au": "Wang, Wei", "ti": "Effects of drug X on mortality in women", "py": "2005", "id": "WM1"},
                {"au": "Wang, Wei", "ti": "Effects of drug X on mortality in men", "py": "2006", "id": "WM2"},
            ]
            ris_sub = Path(tmp) / "wm_sub.ris"
            ris_dir = Path(tmp) / "wm_dir.ris"
            db_sub = str(Path(tmp) / "wm_sub.db")
            db_dir = str(Path(tmp) / "wm_dir.db")
            _write_ris(ris_sub, rows)
            _write_ris(ris_dir, rows)
            proc_sub = _run_cli_subprocess(["import", "--file", str(ris_sub), "--db", db_sub])
            proc_dir = _run_cli(["import", "--file", str(ris_dir), "--db", db_dir])
            self.assertEqual(proc_sub.returncode, 0, proc_sub.stderr)
            self.assertEqual(proc_dir.returncode, 0, proc_dir.stderr)
            self.assertEqual(proc_sub.returncode, proc_dir.returncode)
            self.assertIn("模糊候选 1 条", proc_sub.stdout)
            self.assertIn("模糊候选 1 条", proc_dir.stdout)
            for db_path in (db_sub, db_dir):
                with closing(sqlite3.connect(db_path)) as conn:
                    keys = conn.execute(
                        "SELECT zotero_key FROM articles ORDER BY import_order"
                    ).fetchall()
                self.assertEqual(keys, [("WM1",), ("WM2",)], db_path)

    def test_fuzzy_candidates_not_auto_merged_by_default(self):
        # 首审实测：wm.ris 曾输出 men 论文 is_duplicate_of=women 论文。
        with tempfile.TemporaryDirectory(prefix="rf_u1cli_") as tmp:
            ris = Path(tmp) / "wm.ris"
            db = str(Path(tmp) / "wm.db")
            _write_ris(ris, [
                {"au": "Wang, Wei", "ti": "Effects of drug X on mortality in women", "py": "2005", "id": "WM1"},
                {"au": "Wang, Wei", "ti": "Effects of drug X on mortality in men", "py": "2006", "id": "WM2"},
            ])
            proc = _run_cli(["import", "--file", str(ris), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("模糊候选 1 条", proc.stdout)
            self.assertIn("未自动合并", proc.stdout)
            with closing(sqlite3.connect(db)) as conn:
                rows = conn.execute(
                    "SELECT zotero_key, is_duplicate_of FROM articles ORDER BY import_order"
                ).fetchall()
            self.assertEqual(rows, [("WM1", None), ("WM2", None)])

    def test_auto_merge_fuzzy_flag_restores_legacy_marking(self):
        with tempfile.TemporaryDirectory(prefix="rf_u1cli2_") as tmp:
            ris = Path(tmp) / "wm.ris"
            db = str(Path(tmp) / "wm.db")
            _write_ris(ris, [
                {"au": "Wang, Wei", "ti": "Effects of drug X on mortality in women", "py": "2005", "id": "WM1"},
                {"au": "Wang, Wei", "ti": "Effects of drug X on mortality in men", "py": "2006", "id": "WM2"},
            ])
            proc = _run_cli(["import", "--file", str(ris), "--db", db, "--auto-merge-fuzzy"])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("已自动合并", proc.stdout)
            # 评审反馈（R5/FB5）：进入筛选数必须按合并后保留数打印，
            # 与 DB 可筛选行数一致（此前打印 unique=2 而 DB 只剩 1 条可筛）。
            self.assertIn("进入筛选 1 条", proc.stdout)
            with closing(sqlite3.connect(db)) as conn:
                dup_of = dict(conn.execute(
                    "SELECT zotero_key, is_duplicate_of FROM articles").fetchall())
                screenable = conn.execute(
                    "SELECT COUNT(*) FROM articles WHERE is_duplicate_of IS NULL"
                ).fetchone()[0]
            self.assertEqual(dup_of, {"WM1": None, "WM2": "WM1"})
            self.assertEqual(screenable, 1)

    def test_reimport_same_doi_group_keeps_titles_and_marks(self):
        # 评审反馈（R2/FB1）：库内已有 K2('Title B')，再导 K1('Title A',
        # 同 DOI)+K2('Title B refined', 同 DOI) → K1 保留 Title A、K2 更新
        # 为 refined 并标记 is_duplicate_of=K1；不得出现 K2->K1 改键。
        with tempfile.TemporaryDirectory(prefix="rf_u1cli5_") as tmp:
            seed = Path(tmp) / "seed.ris"
            batch = Path(tmp) / "batch.ris"
            db = str(Path(tmp) / "x.db")
            _write_ris(seed, [{"au": "Doe, Jane", "ti": "Title B", "do": "10.9/same", "id": "K2"}])
            _write_ris(batch, [
                {"au": "Smith, Ann", "ti": "Title A", "do": "10.9/same", "id": "K1"},
                {"au": "Doe, Jane", "ti": "Title B refined", "do": "10.9/same", "id": "K2"},
            ])
            self.assertEqual(_run_cli(["import", "--file", str(seed), "--db", db]).returncode, 0)
            proc = _run_cli(["import", "--file", str(batch), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("键消歧", proc.stdout)
            self.assertIn("重复标记 1 条", proc.stdout)
            self.assertIn("进入筛选 1 条", proc.stdout)
            with closing(sqlite3.connect(db)) as conn:
                rows = dict(conn.execute(
                    "SELECT zotero_key, title FROM articles").fetchall())
                dup_of = dict(conn.execute(
                    "SELECT zotero_key, is_duplicate_of FROM articles").fetchall())
            self.assertEqual(rows["K1"], "Title A")            # 代表行未被覆盖
            self.assertEqual(rows["K2"], "Title B refined")    # 重导更新
            self.assertEqual(dup_of, {"K1": None, "K2": "K1"})

    def test_same_id_different_articles_both_survive(self):
        # 首审实测：两条 ID - 1 不同文献曾静默覆盖只剩一条。
        # r6 解析器泳道（U2-P2，r2 评审判定）口径：'1' 为短纯数字序号，
        # 非全局稳定标识——解析侧即降级为内容派生键（跨文件同键也不再有
        # 「更新 1 条」覆盖）；两行都落库且重导幂等，跨文件键冲突仍走
        # 去重层键消歧路径不变。
        with tempfile.TemporaryDirectory(prefix="rf_u1cli3_") as tmp:
            ris = Path(tmp) / "id.ris"
            db = str(Path(tmp) / "id.db")
            _write_ris(ris, [
                {"au": "Smith, John", "ti": "Aspirin and cardiovascular outcomes in the elderly", "do": "10.1000/aaa", "id": "1"},
                {"au": "Jones, Anna", "ti": "Metformin and renal outcomes in type 2 diabetes", "do": "10.1000/bbb", "id": "1"},
            ])
            proc = _run_cli(["import", "--file", str(ris), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("导入 2 条", proc.stdout)
            # 解析侧降级告警可见（stderr），键在入库前已消歧
            self.assertIn("不是全局稳定标识", proc.stderr)
            with closing(sqlite3.connect(db)) as conn:
                rows = conn.execute(
                    "SELECT zotero_key, title, is_duplicate_of FROM articles ORDER BY import_order"
                ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(r[0].startswith("auto-") for r in rows))
            self.assertEqual([r[2] for r in rows], [None, None])
            # 重导同一文件幂等（不冲突不重复消歧，行数不变）
            proc2 = _run_cli(["import", "--file", str(ris), "--db", db])
            self.assertEqual(proc2.returncode, 0, proc2.stderr)
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0], 2)

    def test_cross_file_key_clash_does_not_overwrite_db_row(self):
        with tempfile.TemporaryDirectory(prefix="rf_u1cli4_") as tmp:
            first = Path(tmp) / "a.ris"
            second = Path(tmp) / "b.ris"
            db = str(Path(tmp) / "clash.db")
            _write_ris(first, [{"au": "Wang, Wei", "ti": "Effects of drug X on mortality in women", "py": "2005", "id": "WM1"}])
            _write_ris(second, [{"au": "Doe, Jane", "ti": "A totally different study on statins", "do": "10.1000/zzz", "id": "WM1"}])
            self.assertEqual(_run_cli(["import", "--file", str(first), "--db", db]).returncode, 0)
            proc = _run_cli(["import", "--file", str(second), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with closing(sqlite3.connect(db)) as conn:
                rows = conn.execute("SELECT zotero_key, title FROM articles ORDER BY import_order").fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0][0], "WM1")
            self.assertTrue(rows[1][0].startswith("WM1-"))
            self.assertIn("Effects", rows[0][1])  # 库内原行未被覆盖


class LivingReviewCandidateTests(unittest.TestCase):
    def test_batch_same_key_same_doi_counts_one_record(self):
        # 评审反馈（R2/FB3·U1-P4）：库内无该文献时，批内两条同键同 DOI
        # 曾计 duplicates_removed=2、new_articles=[]（DB 实际只新增 1 行、
        # DedupReport.n_duplicates()=1）。按合并组根口径：移除 1 条、
        # 保留 1 个代表进入新增。
        with tempfile.TemporaryDirectory(prefix="rf_u1lr2_") as tmp:
            db = str(Path(tmp) / "lr.db")
            db_mod.init_db(db)
            incoming = [
                _article("K1", "Title A", "Smith, John", "10.9/same"),
                _article("K1", "Title A", "Smith, John", "10.9/SAME "),
            ]
            # 同一输入的 DedupReport 口径基准
            _, report = find_duplicates(list(incoming))
            self.assertEqual(report.n_duplicates(), 1)

            result = process_incremental_articles(
                db, incoming, "A vs B", "mortality", "12 months", "RR")
            self.assertEqual(result.duplicates_removed, 1)
            self.assertEqual([a["zotero_key"] for a in result.new_articles], ["K1"])
            self.assertIn("新增文献：1 篇（去重移除 1 篇）", result.change_report)
            with closing(sqlite3.connect(db)) as conn:
                rows = conn.execute(
                    "SELECT zotero_key, is_duplicate_of FROM articles").fetchall()
            self.assertEqual(rows, [("K1", None)])

    def test_fuzzy_goes_to_candidate_list_exact_still_merges(self):
        with tempfile.TemporaryDirectory(prefix="rf_u1lr_") as tmp:
            db = str(Path(tmp) / "lr.db")
            db_mod.init_db(db)
            db_mod.upsert_articles(db, [
                _article("WM1", "Effects of drug X on mortality in women",
                         "Wang, Wei", year=2005),
            ], [])
            incoming = [
                _article("WM2", "Effects of drug X on mortality in men",
                         "Wang, Wei", year=2006),
                _article("EX1", "Some existing study", "Kim, Lee", "10.9/ex1"),
                _article("EX1B", "Some existing study", "Kim, Lee", "10.9/EX1"),
                _article("NEW1", "Brand new trial on statins", "Park, Su", "10.9/new1"),
            ]
            result = process_incremental_articles(
                db, incoming, "A vs B", "mortality", "12 months", "RR")

            # 精确 DOI 重复仍合并计入 removed；模糊只进候选
            self.assertEqual(result.duplicates_removed, 1)
            self.assertEqual(result.pending_candidates, 1)
            self.assertEqual(result.to_dict()["pending_candidates"], 1)
            self.assertEqual(
                sorted(a["zotero_key"] for a in result.new_articles),
                ["EX1", "NEW1", "WM2"])

            with closing(sqlite3.connect(db)) as conn:
                arts = conn.execute(
                    "SELECT zotero_key, is_duplicate_of FROM articles ORDER BY import_order"
                ).fetchall()
                reviews = conn.execute(
                    "SELECT normalized_title, status FROM title_duplicate_reviews").fetchall()
                members = conn.execute(
                    "SELECT zotero_key, imported_in_batch FROM title_duplicate_members").fetchall()
            self.assertEqual(arts, [
                ("WM1", None), ("WM2", None),
                ("EX1", None), ("EX1B", "EX1"), ("NEW1", None)])
            self.assertEqual(len(reviews), 1)
            self.assertTrue(reviews[0][0].startswith("fuzzy:"))
            self.assertEqual(reviews[0][1], "pending")
            self.assertEqual(sorted(members), [("WM1", 0), ("WM2", 1)])
            self.assertIn("待确认候选：1 组", result.change_report)


class WebImportConflictRegressionTests(unittest.TestCase):
    """授权窄范围两条的 TestClient 回归（真实 /import 端点）。

    - U1-P3-web：同键、双方无 DOI、标题冲突的两条文献，第二条必须进
      identity_conflict 候选（而非静默 exact_key 链接），判定后可作为
      独立文献激活；
    - U1-P12-web：exact_match_hits 按记录去重（同键+同 DOI 的两条事件
      只计 1），与 exact_duplicate_excess/canonical_unique 一致。
    """

    @classmethod
    def setUpClass(cls):
        cls._fixture = tempfile.TemporaryDirectory(prefix="rf_u1_web_")
        os.environ["COBOOKSHELF_DATA_DIR"] = cls._fixture.name
        from fastapi.testclient import TestClient
        from custom_backend.main import app
        import custom_backend.auth as auth_mod

        cls.client = TestClient(app)
        cls.client.__enter__()
        # 直接建用户签令牌，避开全套 pytest 共享限流桶（同
        # test_ux_frontend_review_fixes 的做法）
        user = auth_mod.create_user("u1web@example.test", "U1Web-2026-x")
        token = auth_mod.create_access_token(user["id"], user["username"])
        cls.client.headers["Authorization"] = "Bearer " + token
        cls.task = cls.client.post("/api/tasks", json={"name": "u1-web-reg"}).json()["task_id"]

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        gc.collect()
        cls._fixture.cleanup()

    @staticmethod
    def _ris(entries):
        return "".join(
            "TY  - JOUR\n" + "".join(f"{t}  - {v}\n" for t, v in e.items()) + "ER  -\n"
            for e in entries)

    def _upload(self, name, text):
        resp = self.client.post(
            f"/api/tasks/{self.task}/import",
            files={"file": (name, text.encode("utf-8"), "application/octet-stream")})
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def test_same_key_no_doi_title_conflict_becomes_candidate(self):
        # 评审实测复现：第二条曾 outcome=exact_key 静默链接（无候选、仅
        # 1 条 metadata 提案），无法作为独立文献激活。
        first = self._upload("w1.ris", self._ris([
            {"AU": "Smith, Ann", "TI": "Alpha study on glucose", "ID": "SHARE"}]))
        self.assertEqual(first["candidate_groups"], 0)
        second = self._upload("w2.ris", self._ris([
            {"AU": "Doe, Jane", "TI": "Beta trial on renal outcomes", "ID": "SHARE"}]))
        self.assertEqual(second["exact_match_hits"], 0)
        self.assertEqual(second["metadata_review_count"], 0)  # 不再静默提案
        self.assertGreaterEqual(second["pending_candidate_groups"], 1)
        batch_id = second["batch_id"]
        cands = self.client.get(
            f"/api/tasks/{self.task}/import-batches/{batch_id}/candidates?view=all"
        ).json()["candidates"]
        kinds = {c["kind"] for c in cands}
        self.assertIn("identity_conflict", kinds)

        # 判定 not_duplicate 后可激活为独立文献
        detail = self.client.get(
            f"/api/tasks/{self.task}/import-batches/{batch_id}?view=all&limit=200").json()
        conflict = next(c for c in cands if c["kind"] == "identity_conflict")
        decide = self.client.post(
            f"/api/tasks/{self.task}/import-batches/{batch_id}/candidates/{conflict['review_id']}",
            json={"status": "not_duplicate", "expected_policy_version": detail["policy_version"]})
        self.assertEqual(decide.status_code, 200, decide.text)
        detail = self.client.get(
            f"/api/tasks/{self.task}/import-batches/{batch_id}?view=all&limit=200").json()
        activate = self.client.post(
            f"/api/tasks/{self.task}/import-batches/{batch_id}/activate",
            json={"expected_policy_version": detail["policy_version"]})
        self.assertEqual(activate.status_code, 200, activate.text)
        self.assertGreaterEqual(activate.json().get("activated_new", 0), 1)
        rows = self.client.get(f"/api/tasks/{self.task}/articles?limit=50").json()
        arts = rows.get("articles") if isinstance(rows, dict) else rows
        titles = sorted(a.get("title") for a in arts)
        self.assertIn("Alpha study on glucose", titles)
        self.assertIn("Beta trial on renal outcomes", titles)

    def test_same_key_same_doi_variant_hits_counted_by_record(self):
        # 评审实测复现：两行同键同 DOI（大小写/空白变体）曾
        # exact_match_hits=2（事件口径），DB 实际 1 条 canonical。
        result = self._upload("w3.ris", self._ris([
            {"AU": "Kim, Lee", "TI": "Title One", "DO": "10.5555/dup", "ID": "REAL1"},
            {"AU": "Kim, Lee", "TI": "Title One", "DO": "10.5555/DUP ", "ID": "REAL1"},
        ]))
        self.assertEqual(result["input_rows"], 2)
        self.assertEqual(result["canonical_unique"], 1)
        self.assertEqual(result["exact_match_hits"], 1)      # 记录口径
        self.assertEqual(result["exact_duplicate_groups"], 1)
        self.assertEqual(result["exact_duplicate_excess"], 1)


    def test_same_key_hits_library_duplicate_row_not_overwritten(self):
        # 评审反馈（R3·U1-P3）：同键命中的是库内"已标记重复的组成员行"
        # （键不占任何合并组最终键）时，living_review 曾不消歧——upsert
        # 按主键把库内重复行内容覆盖成新文献且保留 is_duplicate_of，新
        # 文献永久不可筛选。修复：stabilize 传 known=existing。
        with tempfile.TemporaryDirectory(prefix="rf_u1lr3_") as tmp:
            db = str(Path(tmp) / "lr.db")
            db_mod.init_db(db)
            # 库内：Y1 与 Y2 同 DOI，Y2 已标记为 Y1 的重复（真实 upsert）
            db_mod.upsert_articles(db, [
                _article("Y1", "Canon", "Kim, Lee", "10.6/y"),
                _article("Y2", "Canon", "Kim, Lee", "10.6/y"),
            ], [DuplicatePair(kept_key="Y1", dup_key="Y2", method="exact_doi")])

            # 增量 RIS（真实文件路径入口）：ID - Y2、不同标题、无 DOI
            inc = Path(tmp) / "inc.ris"
            _write_ris(inc, [{"au": "Park, Su", "ti": "A totally different paper", "id": "Y2"}])
            result = process_incremental_update(
                db, str(inc), "A vs B", "mortality", "12 months", "RR")

            # 新条目为后缀独立键、进入新增；既有重复行内容/标记不变
            new_keys = [a["zotero_key"] for a in result.new_articles]
            self.assertEqual(len(new_keys), 1)
            self.assertTrue(new_keys[0].startswith("Y2-"), new_keys)
            self.assertNotEqual(new_keys[0], "Y2")
            self.assertEqual(result.duplicates_removed, 0)
            self.assertEqual(result.pending_candidates, 1)
            with closing(sqlite3.connect(db)) as conn:
                rows = dict(conn.execute(
                    "SELECT zotero_key, (title || '|' || COALESCE(doi,'') || '|' ||"
                    " COALESCE(is_duplicate_of,'')) FROM articles").fetchall())
            self.assertEqual(rows["Y1"], "Canon|10.6/y|")
            self.assertEqual(rows["Y2"], "Canon|10.6/y|Y1")  # 原行未被覆盖
            self.assertEqual(rows[new_keys[0]], "A totally different paper||")

            # 候选判定 not_duplicate 后新条目可筛选（list_articles 可见）
            with closing(sqlite3.connect(db)) as conn:
                review_id = conn.execute(
                    "SELECT review_id FROM title_duplicate_reviews").fetchone()[0]
            self.assertTrue(db_mod.decide_title_duplicate_review(db, review_id, "not_duplicate"))
            screenable = {a.zotero_key for a in db_mod.list_articles(db)}
            self.assertEqual(screenable, {"Y1", new_keys[0]})


if __name__ == "__main__":
    unittest.main()
