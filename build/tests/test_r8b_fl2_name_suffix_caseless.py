"""r8b FL2 钉子 —— #10/#12/#16 残留：姓名规范化（后缀/助词对称/全小写真姓）。

复审复现口径（out/external_reviews/r6_verify_dedup.md #10/#12/#16，探针
`10`/`12`/`16`，真实 normalize 函数）：

- #10：'John Smith Jr'→姓 'jr'；'John Smith Jr.'→姓 'john smith'/名 'jr'；
  'Robert Downs III'→姓 'iii'；同键同题 'John Smith Jr' vs 'John Smith'
  被判 key_conflict（误拒合法重导）。
- #12：姓氏剥助词、名保留助词：'Maria de la Cruz' given='maria de la'，
  given_names_compatible('maria de la','maria')=False、('maria de la',
  'm de la')=False（同一人元数据变体被判不兼容）。
- #16 残留：'john wu'→姓 'john'（两字母全小写真实姓氏被当缩写），
  'John Wu' vs 'john wu' 同键同题判 key_conflict（J.P. 子项已由 982d901
  修复，须保持不回退）。

修复：判定前剔 Jr/Sr/III/IV 世代后缀；given_names_compatible 双侧剥助词
（与姓氏对称）；整串全小写时两字母无点末 token 按真实姓氏。撤修复必红。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen.dedup import find_duplicates
from coscreen.models import Article
from coscreen.normalize import (
    _is_trailing_name_token,
    first_author_given_names,
    first_author_surname,
    given_names_compatible,
)


class SuffixStrippingTests(unittest.TestCase):
    """#10：Jr/Sr/III 后缀剔除后再判定。撤修复必红。"""

    CASES = [
        # (输入, 期望姓, 期望名)
        ("John Smith Jr", "smith", "john"),        # 修复前姓 'jr'
        ("John Smith Jr.", "smith", "john"),       # 修复前姓 'john smith'/名 'jr'
        ("John Smith Sr", "smith", "john"),
        ("Robert Downs III", "downs", "robert"),   # 修复前姓 'iii'
        ("Robert Downs IV", "downs", "robert"),
        ("Smith Jr, John", "smith", "john"),
        ("Smith, John Jr", "smith", "john"),
    ]

    def test_suffixes_removed_from_surname_and_given(self):
        for name, want_surname, want_given in self.CASES:
            with self.subTest(name=name):
                self.assertEqual(first_author_surname(name), want_surname)
                self.assertEqual(first_author_given_names(name), want_given)

    def test_suffix_variant_not_key_conflict(self):
        # 同键同题 'John Smith Jr' vs 'John Smith'：不得判键冲突（误拒合法
        # 重导，R6-D3 验收口径）。
        arts = [
            Article(zotero_key="K1", title="Trial of X", authors="John Smith Jr",
                    year=2020),
            Article(zotero_key="K1", title="Trial of X", authors="John Smith",
                    year=2020),
        ]
        _, report = find_duplicates(arts, include_fuzzy=False)
        self.assertEqual(len(report.key_conflict_pairs), 0,
                         f"后缀变体不得判键冲突: {report.key_conflict_pairs}")
        self.assertEqual(report.unique, 1)


class CaselessTwoLetterSurnameTests(unittest.TestCase):
    """#16 残余：全小写两字母形态按**比较层双读容忍**收口（评审 R2 返工）。

    r8b 第 1 轮在解析层对「整串全小写 + 两字母无点」豁免缩写判定（'john wu'
    → 姓 wu），但反向把 PubMed 全小写导出的 'smith ja'（姓 smith/名 ja）判成
    姓 'ja'，同一文献的大小写规范化导出新增重复行（评审 R2 high）。现解析层
    恢复基线口径（不改任何解析/存储结果），由比较层对两词无逗号串做双读容忍：
    'John Wu' vs 'john wu'、'smith ja' vs 'Smith JA' 同键同题均视为同一作者。
    撤双读容忍（_first_authors_compatible 回退为单读）时下面的冲突用例变红。
    """

    def test_caseless_reading_is_tolerated_by_comparison(self):
        from coscreen.dedup import _first_authors_compatible

        def art(name: str) -> Article:
            return Article(zotero_key="K1", title="Trial of Y", authors=name,
                           year=2020)

        for left, right in (("John Wu", "john wu"),
                            ("Wei Li", "wei li"),
                            ("smith ja", "Smith JA"),
                            ("smith ja", "Smith ja")):
            with self.subTest(pair=(left, right)):
                self.assertTrue(_first_authors_compatible(art(left), art(right)),
                                f"{left!r} 与 {right!r} 是同一作者的不同写法")

    def test_parser_readings_unchanged_from_baseline(self):
        # R1/R2：解析层不因本项改变（'smith ja' 与 'john wu' 均按基线口径）。
        self.assertEqual(first_author_surname("smith ja"), "smith")
        self.assertEqual(first_author_given_names("smith ja"), "ja")

    def test_mixed_case_lowercase_initial_kept(self):
        # R6-D3 既有口径保持：混合大小写串的 'Smith ja' 仍按缩写（ja 是名）。
        self.assertEqual(first_author_surname("Smith ja"), "smith")
        self.assertEqual(first_author_given_names("Smith ja"), "ja")
        # _is_trailing_name_token 本体不变（r7 GLM07-01 钉住的形态）
        self.assertTrue(_is_trailing_name_token("ja"))
        self.assertFalse(_is_trailing_name_token("Wu"))

    def test_caseless_dotted_initial_still_initial(self):
        # 整串全小写但带点/单字母形态仍是缩写（不受新豁免影响）。
        self.assertEqual(first_author_surname("smith j.p."), "smith")
        self.assertEqual(first_author_surname("smith j"), "smith")

    def test_case_writing_variants_same_work_not_conflict(self):
        arts = [
            Article(zotero_key="K1", title="Trial of Y", authors="John Wu",
                    year=2020),
            Article(zotero_key="K1", title="Trial of Y", authors="john wu",
                    year=2020),
        ]
        _, report = find_duplicates(arts, include_fuzzy=False)
        self.assertEqual(len(report.key_conflict_pairs), 0,
                         f"'John Wu' vs 'john wu' 不得判键冲突: "
                         f"{report.key_conflict_pairs}")
        self.assertEqual(report.unique, 1)

    def test_two_letter_surname_reversal_not_merged(self):
        # 评审 R3·F1（high）：两位两字母姓氏作者的姓名倒序（'wu li' / 'li wu'）
        # 是不同作者，第 2 轮的无条件双读把它们判为同一作者并静默覆盖库内行
        # （实测单行、stderr 空）。撤「两侧全小写不启用双读」必红。
        from coscreen.dedup import _first_authors_compatible, _key_merge_confirmed

        def art(name: str) -> Article:
            return Article(zotero_key="K1", title="Editorial", authors=name,
                           year=2020)

        for left, right in (("wu li", "li wu"), ("xu he", "he xu"),
                            ("lu ma", "ma lu"), ("hu ye", "ye hu"),
                            ("du gu", "gu du")):
            with self.subTest(pair=(left, right)):
                self.assertFalse(_first_authors_compatible(art(left), art(right)),
                                 f"{left!r} 与 {right!r} 是不同作者（姓名倒序）")
                self.assertFalse(_key_merge_confirmed(art(left), art(right)))

    def test_two_letter_surname_reversal_end_to_end_no_overwrite(self):
        # 端到端（评审实测口径）：库内 K1='wu li' 重导 K1='li wu' 必须两行。
        arts = [
            Article(zotero_key="K1", title="Editorial", authors="wu li", year=2020),
            Article(zotero_key="K1", title="Editorial", authors="li wu", year=2020),
        ]
        _, report = find_duplicates(arts, include_fuzzy=False)
        self.assertEqual(report.unique, 2, "姓名倒序不得静默合并为一行")
        self.assertEqual(len(report.key_conflict_pairs), 1)

    def test_caseless_side_matches_capitalised_side(self):
        # 双读保留的部分：'wu li' vs 'Wu Li'（后者大写给出姓氏证据）仍应兼容。
        from coscreen.dedup import _first_authors_compatible

        def art(name: str) -> Article:
            return Article(zotero_key="K1", title="Editorial", authors=name,
                           year=2020)

        for left, right in (("john wu", "John Wu"), ("smith ja", "Smith JA"),
                            ("wu li", "Wu Li"), ("wei li", "Wei Li")):
            with self.subTest(pair=(left, right)):
                self.assertTrue(_first_authors_compatible(art(left), art(right)))

    def test_provable_different_authors_still_conflict(self):
        # 防线：双读容忍不得把可证不同的作者判兼容（评审 R2 的 F1 同型）。
        from coscreen.dedup import _first_authors_compatible

        def art(name: str) -> Article:
            return Article(zotero_key="K1", title="Trial of Y", authors=name,
                           year=2020)

        for left, right in (("Li, De", "Li, Di"), ("Li, De", "Li, John"),
                            ("Smith, John", "Smith, Jane"),
                            ("John Wu", "Smith ja")):
            with self.subTest(pair=(left, right)):
                self.assertFalse(_first_authors_compatible(art(left), art(right)))


class ParticleSymmetryTests(unittest.TestCase):
    """#12：名的助词剥离与姓氏对称。撤修复必红。"""

    def test_same_person_variants_compatible(self):
        self.assertEqual(first_author_surname("Maria de la Cruz"), "cruz")
        self.assertEqual(first_author_surname("Maria Cruz"), "cruz")
        self.assertTrue(given_names_compatible("maria de la", "maria"))
        self.assertTrue(given_names_compatible("maria de la", "m de la"))
        self.assertTrue(given_names_compatible("maria", "maria de la"))

    def test_different_given_names_still_incompatible(self):
        # 防线：助词对称只放宽同人变体，真不同名不得变兼容。
        self.assertFalse(given_names_compatible("maria de la", "maria anna"))
        self.assertFalse(given_names_compatible("john", "jane"))

    def test_van_der_variants_compatible(self):
        self.assertTrue(given_names_compatible("jan van der", "jan"))
        self.assertEqual(first_author_surname("van der Berg, J. A."), "berg")

    def test_given_name_that_is_itself_a_particle_kept(self):
        # 评审 R2·F1（high）：名本身就是助词（'Li, De' / 'Li, Di'）时剥离会
        # 清空 token 列表并落到「缺名=兼容」——可证不同的作者被静默合并覆盖。
        # 撤「剥空时保留原 token」必红。
        self.assertFalse(given_names_compatible("de", "di"))
        self.assertFalse(given_names_compatible("de", "john"))
        self.assertFalse(given_names_compatible("la", "le"))
        self.assertTrue(given_names_compatible("de", "de"))

    def test_particle_only_given_end_to_end_no_silent_overwrite(self):
        # 端到端（评审 R2 实测口径）：同 key K1/题 Editorial/年 2020，
        # 'Li, De' 后重导 'Li, Di' 必须两行/冲突候选，而不是单行被覆盖。
        arts = [
            Article(zotero_key="K1", title="Editorial", authors="Li, De",
                    year=2020),
            Article(zotero_key="K1", title="Editorial", authors="Li, Di",
                    year=2020),
        ]
        _, report = find_duplicates(arts, include_fuzzy=False)
        self.assertEqual(report.unique, 2,
                         "名=助词的可证不同作者不得静默合并")
        self.assertEqual(len(report.key_conflict_pairs), 1)


if __name__ == "__main__":
    unittest.main()
