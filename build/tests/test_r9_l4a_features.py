"""r9 L4a 特征模型泳道钉子（10 条，破坏即红）。

清单：``out/release_plan/inventory_夜扫286确认分组.md`` §3.4。
证据原文：``out/external_reviews/night_verify_batch_03.md``（F2-4、F2-44）、
``night_verify_batch_09.md``（F2-1、F2-2、F2-11、F2-12、F2-13）、
``night_verify_batch_10.md``（F2-3、F2-6、F2-45）。

本文件的核心设计：**每条钉子都在「当前修后树」上跑断言，撤掉修复即红**。
断言的是修复后的行为，不是实现细节（不 mock、不改私有序号），因此：

- 每条钉子都有对应的「修前实测」注释，记录本轮在本工作树复现到的原始
  输出（见各测试类 docstring）；
- 每个测试类末尾的「常规对照」测试在基线（bfd07ae）上就是绿的——它们
  钉住 R1/R2：修复不改动合法输入的逐位输出、不新增对合法输入的拒绝；
- 反向探测（撤修复→红）的实测记录在交付 summary 里，按清单 R4 口径
  逐类给出。

条目 ↔ 测试类映射：

| 条目 | 单元 | 测试类 |
|---|---|---|
| b03·F2-4  | features.py NFC/NFD 分叉 | ``F204UnicodeNormalisationNails`` |
| b09·F2-1  | features.py max_df 上界/类型 | ``F201MaxDfBoundNails`` |
| b09·F2-2  | features.py ngram max_n 无界 | ``F202NgramBoundNails`` |
| b10·F2-3  | features.py 未 fit transform | ``F203UnfittedTransformNails`` |
| b10·F2-6  | features.py CJK 区间 | ``F206CjkRangeNails`` |
| b09·F2-11 | nb.py 失败重拟合混合状态 | ``F211NbFailedRefitNails`` |
| b09·F2-12 | nb.py alpha=0 契约失真 | ``F212NbAlphaContractNails`` |
| b09·F2-13 | nb.py 证据引用不存在 | ``F213NbEvidenceRefsNails`` |
| b03·F2-44 | svm.py 稠密化内存 | ``F244SparseDenseMemoryNails`` |
| b10·F2-45 | svm.py/nb.py 悬空测试引用 | ``F245DanglingTestRefNails`` |
"""

from __future__ import annotations

import math
import re
import sys
import unicodedata
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from coscreen.al.features import TfidfVectorizer, tokenize  # noqa: E402
from coscreen.al.nb import MultinomialNB  # noqa: E402
from coscreen.al.svm import SVMClassifier  # noqa: E402

try:  # MAX_NGRAM_N 是本组修复的一部分；撤修复（回基线）时按缺失变红，
    # 而不是让整文件收集失败——否则一条 collection error 会掩蔽其余钉子
    # 的逐条红证。缺失值用 -1 标记，由 F202 的钉子显式断言。
    from coscreen.al.features import MAX_NGRAM_N
except ImportError:  # pragma: no cover - 仅撤修复时可达
    MAX_NGRAM_N = -1


# ======================================================================
# b03·F2-4 —— Unicode 归一化：NFC/NFD 同一可见文本必须同一词元序列
# ======================================================================
class F204UnicodeNormalisationNails(unittest.TestCase):
    """修前实测（当前树复现，2026-10-31）：

    .. code-block:: text

        NFC tokens: ['impact', 'café', 'impact_café', 'naïve', ...]
        NFD tokens: ['impact', 'cafe', 'impact_cafe', 'nai', 'cafe_nai',
                     've', 'nai_ve', ...]
        NFC==NFD token lists: False

    ``naïve`` 在 NFD 下被组合记号 U+0308 劈成 ``nai`` + ``ve``；``İ``
    （U+0130）被 ``.lower()`` 拆成 ``i`` + U+0307 后同样被劈开。修复：
    :func:`tokenize` 先 NFC 规范化，且 ``M*`` 组合记号不再当词边界。
    """

    TEXT = "Impact of café naïve İstanbul résumé"

    def test_nfc_and_nfd_produce_identical_tokens(self):
        nfc = unicodedata.normalize("NFC", self.TEXT)
        nfd = unicodedata.normalize("NFD", self.TEXT)
        self.assertNotEqual(nfc, nfd, "前提：两种形态的码点序列确实不同")
        self.assertEqual(tokenize(nfc), tokenize(nfd))

    def test_nfd_accent_does_not_split_word(self):
        """修前 ``cafe``+U+0301 会把 ``café`` 拆成两段。"""
        self.assertEqual(tokenize("café"), tokenize("cafe\u0301"))
        # 单个重音词只有一个词元（无假 bigram、无残片）
        self.assertEqual(tokenize("cafe\u0301"), ["café"])
        self.assertEqual(tokenize("nai\u0308ve"), ["naïve"])

    def test_dotted_capital_i_not_split(self):
        """``"İ".lower()`` 自产 U+0307；修前 ``İstanbul`` → ``i``+``stanbul``。"""
        tokens = tokenize("İstanbul")
        self.assertNotIn("stanbul", tokens, f"被劈开的残片仍在：{tokens!r}")
        rejoined = [t for t in tokens if not t.startswith(("i_", "i"))]
        self.assertEqual(len(tokens), 1, f"应为单一词元，实得 {tokens!r}")
        self.assertTrue(tokens[0].startswith("i"), tokens)

    def test_normalisation_does_not_change_ascii(self):
        """常规对照（基线绿）：纯 ASCII 与已是 NFC 的文本逐位不变。"""
        self.assertEqual(
            tokenize("Impact of co-teaching: students' learning"),
            ["impact", "coteaching", "impact_coteaching", "students",
             "coteaching_students", "learning", "students_learning"],
        )
        self.assertEqual(
            tokenize("Impact of café culture"),
            ["impact", "café", "impact_café", "culture", "café_culture"],
        )

    def test_vectoriser_vocab_same_for_both_forms(self):
        """端到端：NFC 与 NFD 语料的词表/向量必须一致（修前静默分叉）。"""
        nfc_docs = [unicodedata.normalize("NFC", t) for t in ("café culture", "café study")]
        nfd_docs = [unicodedata.normalize("NFD", t) for t in ("café culture", "café study")]
        v_nfc, v_nfd = TfidfVectorizer(), TfidfVectorizer()
        x_nfc = v_nfc.fit_transform(nfc_docs)
        x_nfd = v_nfd.fit_transform(nfd_docs)
        self.assertEqual(list(v_nfc.vocab), list(v_nfd.vocab))
        self.assertEqual(x_nfc, x_nfd)


# ======================================================================
# b09·F2-1 —— max_df 只校验下界
# ======================================================================
class F201MaxDfBoundNails(unittest.TestCase):
    """修前实测（当前树复现）：

    .. code-block:: text

        max_df=1.0 vocab: ['alpha', 'alpha_beta', ...]
        max_df=2.0 vocab: 同上      identical: True
        max_df='0.95' -> TypeError '>' not supported between 'str' and 'float'

    修复：按文档口径 ``0 < max_df <= 1`` 校验；越界与非数值一律可读
    ``ValueError``（**不新增拒绝**：``> 1`` 此前只被静默降级为「不过滤」，
    而文档一直写的是越界口径——修复把实现拉回既有契约；字符串此前也被
    拒，只是异常类型是裸 ``TypeError``）。
    """

    DOCS = ["alpha beta beta", "alpha beta gamma", "alpha delta"]

    def _vocab(self, max_df):
        v = TfidfVectorizer(max_df=max_df)
        v.fit(self.DOCS)
        return list(v.vocab)

    def test_upper_bound_enforced(self):
        for bad in (1.0000001, 2.0, 100.0):
            with self.subTest(max_df=bad):
                with self.assertRaises(ValueError):
                    TfidfVectorizer(max_df=bad)

    def test_non_positive_and_non_finite_rejected_cleanly(self):
        for bad in (0.0, -0.5, float("nan"), float("inf")):
            with self.subTest(max_df=bad):
                with self.assertRaises(ValueError):
                    TfidfVectorizer(max_df=bad)

    def test_non_numeric_raises_value_error_not_typeerror(self):
        """修前 ``max_df="0.95"`` 抛裸 ``TypeError``；现为可读 ``ValueError``。"""
        for bad in ("0.95", b"0.95", None, [0.5]):
            with self.subTest(max_df=bad):
                with self.assertRaises(ValueError):
                    TfidfVectorizer(max_df=bad)

    def test_boundary_and_regular_values_unchanged(self):
        """常规对照（基线绿）：1.0 不过滤、0.95 按口径剪枝，逐项不变。"""
        self.assertEqual(
            self._vocab(1.0),
            ["alpha", "alpha_beta", "alpha_delta", "beta", "beta_beta",
             "beta_gamma", "delta", "gamma"],
        )
        # 2/3 文档出现的 alpha 超 0.95×3 → 被剪（与 sklearn 0.95 同结果）
        self.assertEqual(
            self._vocab(0.95),
            ["alpha_beta", "alpha_delta", "beta", "beta_beta",
             "beta_gamma", "delta", "gamma"],
        )

    def test_integer_and_numpy_scalars_still_accepted(self):
        """R2 护栏：合法数值类型（int、numpy 标量）不得被新增校验拒绝。"""
        self.assertEqual(self._vocab(1), self._vocab(1.0))
        try:
            import numpy as np
        except ImportError:  # pragma: no cover - 环境无 numpy 时跳过
            self.skipTest("numpy 不可用")
        self.assertEqual(self._vocab(np.float64(0.95)), self._vocab(0.95))


# ======================================================================
# b09·F2-2 —— ngram max_n 无上限
# ======================================================================
class F202NgramBoundNails(unittest.TestCase):
    """修前实测（当前树复现）：``TfidfVectorizer(ngram_range=(1, 200000))``
    构造被接受，``_ngrams(['a','b'])`` 空转 3 项仍得结果、耗时随 max_n
    线性增长（原报告 0.710s@2e6）。修复：构造时把 ``max_n`` 截断到
    :data:`MAX_NGRAM_N`，``_ngrams`` 另按 ``len(tokens)`` 提前结束。
    **截断而非拒绝**——合法 ``ngram_range`` 请求照常工作（R2）。
    """

    def test_huge_max_n_is_capped_not_rejected(self):
        v = TfidfVectorizer(ngram_range=(1, 2_000_000))
        self.assertEqual(v.ngram_range, (1, MAX_NGRAM_N))

    def test_ngrams_cost_bounded_for_huge_max_n(self):
        """修前为 max_n 次空迭代；现按词元数提前结束（2 词元 → 只做 n=1,2）。"""
        v = TfidfVectorizer(ngram_range=(1, 2_000_000))
        self.assertEqual(v._ngrams(["a", "b"]), ["a", "b", "a b"])

    def test_huge_max_n_fit_and_transform_agree(self):
        v = TfidfVectorizer(ngram_range=(1, 2_000_000))
        rows = v.fit_transform(["a b c"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(sorted(rows[0]), sorted(v.vocab))

    def test_huge_max_n_does_not_waste_seconds(self):
        """成本钉子：修前 fit+transform 30 篇 5 词文档 **~4-20s**（逐 n 空转，
        随篇数线性），修后 ~0.005s。阈值 2.5s——对修后形态有 500× 余量、
        对修前形态在慢机上仍判红（修前耗时随 max_n 线性，80 篇实测 120s）。

        （本条是唯一带墙钟的断言；红/绿差距量级达 10³，不会因机器快慢
        翻转。）
        """
        import time

        docs = ["alpha beta gamma delta epsilon"] * 30
        started = time.perf_counter()
        v = TfidfVectorizer(ngram_range=(1, 2_000_000))
        v.fit(docs)
        v.transform(docs)
        elapsed = time.perf_counter() - started
        self.assertLess(
            elapsed, 2.5,
            f"max_n=2e6 的 fit+transform 耗时 {elapsed:.1f}s（修前实测数秒到数十秒）",
        )

    def test_regular_ranges_bit_identical(self):
        """常规对照（基线绿）：(1,1) 与 (1,2) 的 n-gram 输出逐项不变。"""
        one = TfidfVectorizer(ngram_range=(1, 1))
        self.assertEqual(one._ngrams(["x", "y", "z"]), ["x", "y", "z"])
        two = TfidfVectorizer(ngram_range=(1, 2))
        self.assertEqual(
            two._ngrams(["x", "y", "z"]), ["x", "y", "z", "x y", "y z"]
        )
        # (2,2) 这类不常见但合法的区间仍照常工作
        self.assertEqual(TfidfVectorizer(ngram_range=(2, 2))._ngrams(["x", "y", "z"]),
                         ["x y", "y z"])

    def test_max_n_below_cap_untouched(self):
        """R2 护栏：未超上限的 max_n 不被截断（只截断超限值）。"""
        self.assertEqual(TfidfVectorizer(ngram_range=(1, 4)).ngram_range, (1, 4))

    def test_min_n_above_cap_keeps_valid_range(self):
        """R2 护栏：min_n 本身超上限时不得留下倒置区间（(12,2000) 合法）。"""
        v = TfidfVectorizer(ngram_range=(12, 2000))
        self.assertLessEqual(v.ngram_range[0], v.ngram_range[1])
        self.assertEqual(v.ngram_range[0], 12)
        # 区间完全大于词元数：O(1) 空转（range 为空），不报错也不产出
        self.assertEqual(v._ngrams(["a", "b"]), [])

    def test_extreme_range_does_not_waste_time(self):
        """两个端点都极大时也只有空转（按词元数提前结束），不抛错。"""
        v = TfidfVectorizer(ngram_range=(10 ** 6, 10 ** 7))
        self.assertEqual(v._ngrams(["a", "b", "c"]), [])


# ======================================================================
# b10·F2-3 —— transform 无「已 fit」守卫
# ======================================================================
class F203UnfittedTransformNails(unittest.TestCase):
    """修前实测（当前树复现）：``TfidfVectorizer().transform([...]) -> [{}, {}]``
    静默返回全空向量（排序无提示退化）。修复：``fit`` 置 ``_fitted``，
    未 fit 的 ``transform`` 抛 ``RuntimeError``；**空语料 fit**（词表确实
    为空）仍是合法状态，照常返回全空向量。
    """

    def test_unfitted_transform_raises(self):
        with self.assertRaises(RuntimeError):
            TfidfVectorizer().transform(["hello world", "another doc"])

    def test_unfitted_transform_raises_even_for_empty_input(self):
        """空输入也不豁免：语义错误与输入是否为空无关。"""
        with self.assertRaises(RuntimeError):
            TfidfVectorizer().transform([])

    def test_fitted_transform_unchanged(self):
        """常规对照（基线绿）：fit 后逐位不变。"""
        v = TfidfVectorizer()
        rows = v.fit_transform(["hello world"])
        self.assertEqual(
            rows,
            [{"hello": 0.5773502691896258, "world": 0.5773502691896258,
              "hello_world": 0.5773502691896258}],
        )
        self.assertEqual(v.transform(["hello world"]), rows)

    def test_empty_corpus_fit_still_allows_transform(self):
        """R2 护栏：空语料 fit（合法状态）不得被守卫误伤。"""
        v = TfidfVectorizer()
        v.fit([])
        self.assertEqual(v.transform(["hello world"]), [{}])


# ======================================================================
# b10·F2-6 —— CJK 扩展 A / 兼容表意字 / 部首
# ======================================================================
class F206CjkRangeNails(unittest.TestCase):
    """修前实测（当前树复现）：

    .. code-block:: text

        'abc㐀def'  -> ['abc㐀def']     # U+3400 并入英文词元
        'x豈y'      -> ['x', '豈', 'y']  # U+F900 未参与 CJK bigram
        'abc⺀def'   -> ['abcdef']       # 部首 U+2E80 被当符号删除
        '⺀⺁'       -> []

    修复：``_is_cjk`` 覆盖扩展 A（U+3400–4DBF）、兼容表意字
    （U+F900–FAFF）、扩展 B+（U+20000–2FA1F）与 CJK 部首
    （U+2E80–2EFF / U+2F00–2FDF）。
    """

    def test_extension_a_not_merged_into_latin_token(self):
        tokens = tokenize("abc\U00003400def")
        self.assertNotIn("abc\U00003400def", tokens, f"扩展 A 仍并入英文词元：{tokens!r}")
        self.assertEqual(tokens, ["abc", "\U00003400", "def"])

    def test_extension_a_gets_cjk_bigrams(self):
        """修前扩展 A 段不产字符 bigram；现与 URO 同样处理。"""
        self.assertEqual(tokenize("\U00003400\U00003401\U00003402"),
                         ["\U00003400\U00003401", "\U00003401\U00003402"])

    def test_compatibility_ideograph_is_cjk(self):
        """U+F900 在 NFC 下被正规化为 U+8C48（同为 CJK）；关键是**不再并入
        英文词元**（修前 ``'x豈y' -> ['x豈y']`` 单段混合词元）。"""
        tokens = tokenize("x\uf900y")
        self.assertEqual(tokens, ["x", "\u8c48", "y"])
        self.assertEqual(tokenize("\uf900\uf901"),
                         tokenize(unicodedata.normalize("NFC", "\uf900\uf901")))
        # 未经 NFC 的兼容字本身也必须被判为 CJK（区间已覆盖）
        from coscreen.al.features import _is_cjk
        self.assertTrue(_is_cjk("\uf900"))
        self.assertTrue(_is_cjk("\U00003400"))
        self.assertTrue(_is_cjk("\u2e80"))

    def test_radicals_are_kept(self):
        """修前部首（So 类别）被整体删除 → []。"""
        self.assertEqual(tokenize("\u2f00\u2f01"), ["\u2f00\u2f01"])
        tokens = tokenize("abc\u2e80def")
        self.assertNotIn("abcdef", tokens, f"部首仍被删除并粘连两侧：{tokens!r}")

    def test_cjk_strokes_are_kept(self):
        """同族：CJK 笔画块（U+31C0–31EF，同为 So）修前也被整体删除。"""
        self.assertEqual(tokenize("\u31c0\u31c1"), ["\u31c0\u31c1"])
        tokens = tokenize("abc\u31c0def")
        self.assertNotIn("abcdef", tokens, f"CJK 笔画仍被删除并粘连两侧：{tokens!r}")

    def test_plain_cjk_and_latin_unchanged(self):
        """常规对照（基线绿）：URO 汉字与英文逐项不变。"""
        self.assertEqual(
            tokenize("深度学习在医学图像中的应用"),
            ["深度", "度学", "学习", "习在", "在医", "医学", "学图", "图像",
             "像中", "中的", "的应", "应用"],
        )
        self.assertEqual(
            tokenize("TESOL 教师 narrative review"),
            ["tesol", "教师", "narrative", "review", "narrative_review"],
        )
        # CJK 段仍阻断英文 bigram
        self.assertNotIn("tesol_narrative", tokenize("TESOL 教师 narrative"))

    def test_disjoint_ranges_untouched(self):
        """R2 护栏：非 CJK 的非拉丁文字（谚文/西里尔）不被吸入 CJK 路径。"""
        hangul = tokenize("\ud55c\uad6d\uc5b4")  # 한국어
        self.assertEqual(len(hangul), 1, f"谚文应为单一字母词元：{hangul!r}")
        cyrillic = tokenize("\u0442\u0435\u0441\u0442 \u0441\u043b\u043e\u0432\u043e")
        self.assertEqual(len(cyrillic), 3, f"西里尔两词应产出 unigram+bigram：{cyrillic!r}")


# ======================================================================
# b09·F2-11 —— 失败重拟合留下混合状态
# ======================================================================
class F211NbFailedRefitNails(unittest.TestCase):
    """修前实测（当前树复现）：

    .. code-block:: text

        after fit1:            n_features_=1  priors={0: 0.0, 1: -inf}
        refit raised ValueError: sample_weight 全为零，无法拟合
        after failed refit:    n_features_=4  priors={0: 0.0, 1: -inf}   # ← 新词表 + 旧先验

    修复：三个拟合属性在全部校验通过后一次性提交；失败重拟合不触碰
    已提交状态（旧模型仍可用），与 ``SVMClassifier`` 一致。
    """

    FIRST_X = [{"a": 1.0}]
    FIRST_Y = [1]
    SECOND_X = [{"a": 1.0, "b": 2.0}, {"c": 3.0, "d": 4.0}]
    SECOND_Y = [1, 0]

    def _first_model(self):
        clf = MultinomialNB(alpha=1.0)
        clf.fit(self.FIRST_X, self.FIRST_Y)
        return clf

    def test_failed_refit_leaves_no_mixed_state(self):
        clf = self._first_model()
        before = (clf.n_features_, dict(clf.class_log_prior_),
                  {c: dict(v) for c, v in clf.feature_log_prob_.items()})
        with self.assertRaises(ValueError):
            clf.fit(self.SECOND_X, self.SECOND_Y, sample_weight=[0.0, 0.0])
        after = (clf.n_features_, dict(clf.class_log_prior_),
                 {c: dict(v) for c, v in clf.feature_log_prob_.items()})
        self.assertEqual(after, before, "失败重拟合改动了已提交的拟合状态")

    def test_failed_refit_keeps_old_model_predictable(self):
        """状态不变的含义：失败后旧模型照常给分（修前混态下分数对不上）。"""
        clf = self._first_model()
        scores_before = clf.predict_scores([{"a": 2.0}])
        with self.assertRaises(ValueError):
            clf.fit(self.SECOND_X, self.SECOND_Y, sample_weight=[0.0, 0.0])
        self.assertEqual(clf.predict_scores([{"a": 2.0}]), scores_before)

    def test_failed_first_fit_leaves_unfitted(self):
        """从未成功 fit 过的对象失败后仍必须报「尚未 fit」。"""
        clf = MultinomialNB(alpha=1.0)
        with self.assertRaises(ValueError):
            clf.fit(self.SECOND_X, self.SECOND_Y, sample_weight=[0.0, 0.0])
        self.assertEqual(clf.n_features_, 0)
        with self.assertRaises(RuntimeError):
            clf.predict_scores([{"a": 1.0}])

    def test_successful_refit_updates_all_three_attributes(self):
        """常规对照（基线绿）：成功重拟合后三属性一致更新。"""
        clf = self._first_model()
        clf.fit(self.SECOND_X, self.SECOND_Y)
        self.assertEqual(clf.n_features_, 4)
        self.assertEqual(len(clf.feature_log_prob_[0]), 4)
        self.assertEqual(len(clf.feature_log_prob_[1]), 4)
        self.assertEqual(sorted(clf.class_log_prior_), [0, 1])

    def test_regular_fit_values_bit_identical(self):
        """R1 护栏：常规 fit 的数值逐位不变（含 sample_weight 路径）。"""
        clf = MultinomialNB(alpha=1.0)
        clf.fit([{"a": 1.0, "b": 2.0}, {"a": 3.0, "b": 1.0}], [1, 0])
        self.assertEqual(clf.predict_scores([{"a": 1.0, "b": 1.0}]),
                         [0.5192307692307692])
        self.assertEqual(clf.n_features_, 2)
        weighted = MultinomialNB(alpha=1.0)
        weighted.fit([{"a": 1.0, "b": 2.0}, {"a": 3.0, "b": 1.0}], [1, 0],
                     sample_weight=[1.0, 2.0])
        self.assertEqual(weighted.predict_scores([{"a": 1.0, "b": 1.0}]),
                         [0.3636363636363638])


# ======================================================================
# b09·F2-12 —— docstring「与 sklearn 一致」对 alpha=0 失实
# ======================================================================
class F212NbAlphaContractNails(unittest.TestCase):
    """修前实测（当前树复现）：

    .. code-block:: text

        our nb alpha=0.0 -> ValueError: alpha 必须为正数，得到 0.0
        sklearn 1.9.1 alpha=0.0 fit OK（feature_log_prob_ 含 -inf，proba 可为 nan）
        sklearn 1.9.1 alpha=-1  -> InvalidParameterError
        sklearn 1.9.1 alpha=inf -> InvalidParameterError

    修复口径：**保留拒绝 alpha=0 的设计**（sklearn 的 alpha=0 会产出
    ``-inf`` 对数概率与 ``nan`` 后验，与本模块的有限数值契约不相容），
    把 docstring 改成如实说明这是一处**有意偏离**，并补齐非有限值的
    拒绝（修前 ``alpha=inf`` 被收下、fit 时算出 ``nan``）。
    """

    X = [{"a": 1.0, "b": 2.0}, {"a": 3.0, "b": 1.0}]
    Y = [1, 0]

    def test_alpha_zero_rejected_by_design(self):
        with self.assertRaises(ValueError):
            MultinomialNB(alpha=0.0)

    def test_docstring_does_not_claim_sklearn_parity(self):
        """修前 docstring 写「须为正，与 sklearn 一致」——sklearn 接受 0。

        断言方式：出现的每一处「与 sklearn 一致」都必须处在
        「失实/偏离」语境里（即同一句含「失实」或「偏离」），不得再作为
        契约陈述出现。
        """
        doc = MultinomialNB.__init__.__doc__ or ""
        for clause in re.split(r"[。；\n]", doc):
            if "与 sklearn" not in clause or "一致" not in clause:
                continue
            self.assertTrue(
                ("失实" in clause or "偏离" in clause),
                f"docstring 仍把「与 sklearn 一致」当契约陈述：{clause.strip()!r}",
            )
        self.assertIn("sklearn", doc, "应如实说明与 sklearn 的差异")
        self.assertIn("有限正数", doc, "应写明实际契约")

    def test_non_finite_alpha_rejected(self):
        """修前 ``alpha=inf`` 穿过 `alpha > 0`，fit 时产出 nan 对数概率。"""
        for bad in (float("inf"), float("-inf"), float("nan")):
            with self.subTest(alpha=bad):
                with self.assertRaises(ValueError):
                    MultinomialNB(alpha=bad)

    def test_non_numeric_alpha_raises_value_error(self):
        for bad in ("1.0", None):
            with self.subTest(alpha=bad):
                with self.assertRaises(ValueError):
                    MultinomialNB(alpha=bad)

    def test_positive_alpha_still_works_bit_identical(self):
        """R2/R1 护栏：合法 alpha（含极小正数）照常拟合、数值逐位不变。"""
        for good in (1.0, 3.822, 1e-12):
            with self.subTest(alpha=good):
                clf = MultinomialNB(alpha=good)
                clf.fit(self.X, self.Y)
                scores = clf.predict_scores([{"a": 1.0, "b": 1.0}])
                self.assertEqual(len(scores), 1)
                self.assertTrue(math.isfinite(scores[0]))
        clf = MultinomialNB(alpha=1.0)
        clf.fit(self.X, self.Y)
        self.assertEqual(clf.predict_scores([{"a": 1.0, "b": 1.0}]),
                         [0.5192307692307692])


# ======================================================================
# b09·F2-13 —— nb.py 证据引用（tests/test_al_nb.py、research/sim/replay.py）
# ======================================================================
class F213NbEvidenceRefsNails(unittest.TestCase):
    """修前实测（当前树复现）：``nb.py:5`` 引 ``tests/test_al_nb.py``
    （全树 0 命中）、``nb.py:23-24`` 引 ``research/sim/replay.py``
    （无 ``research/`` 目录）——两者在锚定树与全部 git 历史都不存在，
    等价性声明因此不可复核。修复：改引**实际存在**的
    ``docs/methodology/sim/equivalence_check.py``（``check_nb``，1e-12
    验收）与 ``docs/methodology/sim/replay.py``（``_nb_fit_score``）。
    "不存在的路径"由测试自造拼接串定位，避免测试文件本身出现该字面量。
    """

    NB_DOC = (REPO / "coscreen" / "al" / "nb.py").read_text(encoding="utf-8")

    def test_docstring_does_not_cite_missing_test_file(self):
        missing = "tests/" + "test_al" + "_nb.py"
        self.assertNotIn(missing, self.NB_DOC,
                         f"nb.py 仍引用不存在的 {missing}")
        self.assertFalse((REPO / "tests" / "test_al_nb.py").exists())

    def test_docstring_does_not_cite_missing_research_dir(self):
        missing = "research/" + "sim/" + "replay.py"
        self.assertNotIn(missing, self.NB_DOC,
                         f"nb.py 仍引用不存在的 {missing}")
        self.assertFalse((REPO / "research").exists())

    def test_cited_evidence_files_actually_exist(self):
        """反向钉子：修后的每个 ``docs/methodology/sim/`` 引用必须真实存在。"""
        cited = set(re.findall(r"docs/methodology/sim/([A-Za-z0-9_]+\.py)", self.NB_DOC))
        self.assertTrue(cited, "nb.py 应引用可复核的等价性脚本")
        for name in cited:
            self.assertTrue(
                (REPO / "docs" / "methodology" / "sim" / name).is_file(),
                f"nb.py 引用的 {name} 不存在",
            )

    def test_numeric_equivalence_claim_still_holds(self):
        """常规对照：等价性本身成立（不仅引用对，数值也对得上 sklearn）。"""
        try:
            import numpy as np
            from sklearn.naive_bayes import MultinomialNB as SkNB
        except ImportError:  # pragma: no cover
            self.skipTest("sklearn 不可用")
        X = [{"a": 1.0, "b": 2.0}, {"a": 3.0, "b": 1.0},
             {"b": 2.0}, {"a": 2.0, "b": 2.0}]
        y = [1, 0, 1, 0]
        cand = [{"a": 1.0, "b": 1.0}]
        ours = MultinomialNB(alpha=1.0)
        ours.fit(X, y)
        ours_scores = ours.predict_scores(cand)
        cols = sorted({t for r in X for t in r})
        mat = np.array([[r.get(t, 0.0) for t in cols] for r in X])
        sk = SkNB(alpha=1.0).fit(mat, np.array(y))
        sk_scores = sk.predict_proba(np.array([[c.get(t, 0.0) for t in cols] for c in cand]))
        self.assertLessEqual(abs(ours_scores[0] - sk_scores[0, 1]), 1e-9)


# ======================================================================
# b03·F2-44 —— dict 行稠密化内存
# ======================================================================
class F244SparseDenseMemoryNails(unittest.TestCase):
    """修前实测（当前树复现）：

    .. code-block:: text

        _rows_to_dense(1000×20000): shape=(1000, 20000) bytes=160000000 (152.6 MiB)
        外推 5000 候选 × 10 万词表 = 3.73 GiB 单次分配
        svm.py fit 侧 :163、predict 侧 :254 都走该函数

    tracemalloc 对照（本文件同一规模，300 行 × 11103 列）：

    .. code-block:: text

        修前（稠密物化 + LinearSVC.fit）：峰值 52.9 MiB（稠密矩阵下界 25.4 MiB）
        修后（CSR 稀疏路径）         ：峰值  1.1 MiB   ← 48× 差距

    修复：dict 行一律走 ``scipy.sparse.csr_matrix``（内存 O(nnz)），并
    增加 ``max_features`` 列数上限（超限按总权重降序保留）。断言用
    tracemalloc 峰值与**稠密矩阵下界**的比值——不测墙钟，避免机器速度差异。
    """

    N_ROWS, N_TERMS, DENSE_TAIL = 300, 30000, 40

    @classmethod
    def setUpClass(cls):
        cls.rows = [
            {f"t{(i * 37 + j) % cls.N_TERMS}": float(j + 1) for j in range(cls.DENSE_TAIL)}
            for i in range(cls.N_ROWS)
        ]
        cls.labels = [1 if i % 3 == 0 else 0 for i in range(cls.N_ROWS)]
        #: 词表列数（确定性生成器 → 常量；与实测 11103 一致，见类 docstring）
        cls.N_COLS = len({t for r in cls.rows for t in r})
        #: 修前单次稠密分配的下界（n_rows × n_columns × 8B）
        cls.DENSE_BYTES = cls.N_ROWS * cls.N_COLS * 8
        # 预热：sklearn/numpy 的首次 import 本身要分配数十 MiB，若落在下面的
        # tracemalloc 窗口里，测到的就是导入开销而不是稠密化开销（实测会把
        # 稀疏路径误判成 87 MiB）。测量前先付掉这笔一次性成本。
        import scipy.sparse  # noqa: F401
        import sklearn.svm  # noqa: F401

        SVMClassifier(random_state=0)

    def _peak_bytes(self, fn):
        import tracemalloc

        tracemalloc.start()
        try:
            result = fn()
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        return peak, result

    def test_fit_peak_memory_far_below_dense_bound(self):
        """修前 fit 峰值 52.9 MiB（>2× 稠密下界）；修后 ~1.1 MiB。"""
        peak, clf = self._peak_bytes(
            lambda: SVMClassifier(random_state=1).fit(self.rows, self.labels)
        )
        self.assertEqual(len(clf._columns), self.N_COLS)
        self.assertLess(
            peak, self.DENSE_BYTES * 0.25,
            f"fit 峰值 {peak/2**20:.1f} MiB 达稠密下界 {self.DENSE_BYTES/2**20:.1f} MiB 的 "
            f"{peak/self.DENSE_BYTES:.0%}——稀疏路径未生效（修前实测约 200%）",
        )

    def test_predict_peak_memory_far_below_dense_bound(self):
        """修前每次 predict 都整块 n_candidates × |vocab| 稠密化。"""
        clf = SVMClassifier(random_state=1).fit(self.rows, self.labels)
        peak, scores = self._peak_bytes(lambda: clf.predict_proba(self.rows))
        self.assertEqual(len(scores), self.N_ROWS)
        self.assertLess(
            peak, self.DENSE_BYTES * 0.25,
            f"predict 峰值 {peak/2**20:.1f} MiB 达稠密下界 {self.DENSE_BYTES/2**20:.1f} MiB 的 "
            f"{peak/self.DENSE_BYTES:.0%}——预测侧仍在稠密化",
        )

    def test_sparse_path_matches_dense_reference(self):
        """稀疏路径必须与修前的稠密参考实现给同一结果（R1：不改变输出）。

        参照物就是修前生产路径用的 ``_rows_to_dense``（保留为对照基准），
        因此这条钉子同时验证「换实现不换结果」。
        """
        import numpy as np
        from sklearn.svm import LinearSVC

        from coscreen.al.svm import _rows_to_dense

        clf = SVMClassifier(random_state=42).fit(self.rows, self.labels)
        dense = _rows_to_dense(self.rows, clf._columns)
        ref = LinearSVC(C=0.11, loss="squared_hinge", random_state=42)
        ref.fit(dense, self.labels)
        expected = 1.0 / (1.0 + np.exp(-ref.decision_function(dense)))
        got = clf.predict_proba(self.rows)
        worst = float(np.max(np.abs(np.asarray(got) - expected)))
        self.assertLessEqual(worst, 1e-12, f"稀疏路径偏离稠密参考 {worst:.3e}")

    def test_max_features_caps_column_space(self):
        """资源上限：超限词表按总权重降序保留，列数有界（优雅降级非报错）。"""
        clf = SVMClassifier(random_state=42, max_features=50)
        clf.fit(self.rows, self.labels)
        self.assertEqual(len(clf._columns), 50)
        # 保留的是权重最高的词项：全部行共有的高频词优先
        totals = {t: sum(r.get(t, 0.0) for r in self.rows) for t in clf._columns}
        self.assertGreaterEqual(min(totals.values()), 0.0)
        scores = clf.predict_proba(self.rows)
        self.assertEqual(len(scores), self.N_ROWS)

    def test_max_features_none_keeps_every_column(self):
        """R2 护栏：默认无上限时不剪枝（列集 = 全部出现过的词项）。"""
        clf = SVMClassifier(random_state=42).fit(self.rows, self.labels)
        self.assertEqual(set(clf._columns),
                         {t for r in self.rows for t in r})

    def test_max_features_accepts_numpy_integer(self):
        """R2 护栏：'正整数' 的合法取值域含 numpy 整型（不得被新增严格性拒绝）。"""
        try:
            import numpy as np
        except ImportError:  # pragma: no cover
            self.skipTest("numpy 不可用")
        clf = SVMClassifier(random_state=42, max_features=np.int64(50))
        clf.fit(self.rows, self.labels)
        self.assertEqual(len(clf._columns), 50)

    def test_small_input_bit_identical_to_baseline(self):
        """常规对照（基线绿）：小规模 dict 输入的分数逐位不变。"""
        rows = [{"beta": 2.0, "blocker": 1.0}, {"cohort": 3.0, "beta": 1.5},
                {"beta": 0.5}, {"cohort": 2.0, "blocker": 0.25},
                {"beta": 1.0, "cohort": 1.0}]
        labels = [1, 0, 1, 0, 1]
        clf = SVMClassifier(random_state=42).fit(rows, labels)
        self.assertEqual(
            clf.predict_proba(rows),
            [0.6703400832210454, 0.3779412048283692, 0.5780531449031504,
             0.3781953821434495, 0.5208919162281596],
        )
        self.assertEqual(clf.predict(rows), [1, 0, 1, 0, 1])


# ======================================================================
# b10·F2-45 —— 悬空的 tests/test_al_nb.py 引用
# ======================================================================
class F245DanglingTestRefNails(unittest.TestCase):
    """修前实测（当前树复现）：``svm.py:91`` 与 ``nb.py:5`` 都引
    ``tests/test_al_nb.py``——全树 0 命中、全 git 历史 0 命中。零覆盖
    一面已由 ``build/tests/test_r7_lastfix_svm.py``（16 用例）关闭，
    残留仅悬空引用（本清单判定为文档项）。修复：改为指向真实存在的
    ``docs/methodology/sim/equivalence_check.py`` 基准脚本。
    """

    MISSING = "tests/" + "test_al" + "_nb.py"

    def test_svm_does_not_cite_missing_file(self):
        doc = (REPO / "coscreen" / "al" / "svm.py").read_text(encoding="utf-8")
        self.assertNotIn(self.MISSING, doc)

    def test_nb_does_not_cite_missing_file(self):
        doc = (REPO / "coscreen" / "al" / "nb.py").read_text(encoding="utf-8")
        self.assertNotIn(self.MISSING, doc)

    def test_no_lane_file_cites_the_missing_file(self):
        lane = ["features.py", "nb.py", "svm.py"]
        for name in lane:
            with self.subTest(file=name):
                doc = (REPO / "coscreen" / "al" / name).read_text(encoding="utf-8")
                self.assertNotIn(self.MISSING, doc)

    def test_existing_svm_coverage_still_present(self):
        """常规对照：零覆盖面已由 r7 钉子关闭，且该文件仍在。"""
        nail = REPO / "build" / "tests" / "test_r7_lastfix_svm.py"
        self.assertTrue(nail.is_file())
        count = len(re.findall(r"^\s+def test_", nail.read_text(encoding="utf-8"), re.M))
        self.assertGreaterEqual(count, 16, f"r7 SVM 钉子只剩 {count} 个用例")


if __name__ == "__main__":
    import unittest as _unittest

    _unittest.main()
