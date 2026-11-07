"""r9 L4b 主动学习载体的契约钉子（破坏即红）。

清单：``out/release_plan/inventory_夜扫286确认分组.md`` §3.4。
证据原文：``out/external_reviews/night_verify_batch_07.md``（F1-117、F1-118、
F1-120、F1-121、F1-124）、``night_verify_batch_09.md``（F2-14…F2-17）、
``night_verify_batch_10.md``（F2-20、F2-21、F2-26…F2-28、F2-37…F2-41）。

每条钉子都断言**修复后的行为**，撤掉修复即红；每个测试类末尾的
「常规对照」在基线 bfd07ae 上就是绿的，钉住 R1/R2（合法输入的逐位输出
不变、不新增对合法输入的拒绝）。

条目 ↔ 测试类：

| 条目 | 载体 | 测试类 |
|---|---|---|
| b07·F1-117/F1-118 | al/__init__.py、ranker.py（依赖与引用声明） | ``F117DependencyDisclosureNails`` |
| b07·F1-120 | balancer.py ndim | ``F120BalancerNdimNails`` |
| b07·F1-121 | balancer.py ratio 有限性 | ``F121BalancerRatioNails`` |
| b07·F1-124 | balancer.py 总和措辞 | ``F124BalancerSumWordingNails`` |
| b09·F2-14 | profiles.py 只读参数 | ``F214ProfileImmutabilityNails`` |
| b09·F2-15 | profiles.py 值域校验 | ``F215ProfileDomainNails`` |
| b09·F2-16 | profiles.py svm/cjk 文案 | ``F216CjkDescriptionNails`` |
| b09·F2-17 | profiles.py 引用可溯源 | ``F217ProfileProvenanceNails`` |
| b10·F2-20 | ranker.py 重复键 | ``F220RankerDuplicateKeysNails`` |
| b10·F2-21 | ranker.py min_labeled 下界 | ``F221MinLabeledBoundNails`` |
| b10·F2-26 | simulation.py 键唯一性 | ``F226SimulationUniqueKeysNails`` |
| b10·F2-27 | simulation.py 标签回显截断 | ``F227LabelEchoTruncationNails`` |
| b10·F2-28 | simulation.py 策略先于早退 | ``F228StrategyBeforeEarlyExitNails`` |
| b10·F2-37 | stop_registry.py 消费面声明 | ``F237RegistryConsumptionNails`` |
| b10·F2-38 | stop_registry.py 键集完整 | ``F238RegistryKeySetNails`` |
| b10·F2-39 | strategies.py mixed 复杂度 | ``F239MixedComplexityNails`` |
| b10·F2-40 | strategies.py 分数值域 | ``F240ScoreDomainNails`` |
| b10·F2-41 | strategies.py scores=None | ``F241ScoresNoneNails`` |
"""

from __future__ import annotations

import math
import random
import time
import unittest
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]

from coscreen.al.balancer import BalancedWeights  # noqa: E402
from coscreen.al.profiles import ALProfile, DEFAULT_PROFILE, PROFILES  # noqa: E402
from coscreen.al.ranker import ActiveLearningRanker  # noqa: E402
from coscreen.al.simulation import replay, simulate_random_baseline  # noqa: E402
from coscreen.al.stop_registry import STOP_RULES  # noqa: E402
from coscreen.al.strategies import query_order  # noqa: E402
from coscreen.models import Article  # noqa: E402


def _article(key: str, title: str | None = None) -> Article:
    return Article(zotero_key=key, title=title or f"title {key} alpha beta gamma")


def _reference_mixed(keys, scores, seed):
    """基线（bfd07ae）的 mixed 实现：逐行照抄，作为 R1 的逐位对照。"""
    rng = random.Random(seed)
    shuffled = list(keys)
    rng.shuffle(shuffled)
    if not scores:
        return shuffled
    order = sorted(keys, key=lambda k: scores.get(k, 0.0), reverse=True)
    pos = 0
    for i in range(len(order)):
        if rng.random() < 0.05 and pos < len(shuffled):
            key = shuffled[pos]
            j = order.index(key)
            order[i], order[j] = order[j], order[i]
            pos += 1
    return order


# ======================================================================
# b07·F1-117 / F1-118 —— 依赖与规格引用的如实声明
# ======================================================================
class F117DependencyDisclosureNails(unittest.TestCase):
    """修前实测（当前树复现）：``coscreen/al/__init__.py:3`` 写「纯 Python
    实现，运行时零第三方依赖（sklearn 仅用于测试基准）」，而
    ``balancer.py:31`` 顶层 ``import numpy``、``svm.py`` 构造时导入
    sklearn，``tuned``/``svm``/``cjk`` 三个预设在生产 ``/al/rank`` 上
    必走 numpy；``__init__.py:1`` 的「见 SPEC.md §12」全树 0 命中
    （``SPEC.md`` 在全部 git 历史都不存在，仓库内 22 处引用）。

    修复：包级 docstring 改为**分载体**列依赖（纯标准库 / numpy /
    sklearn），并指向真实存在的 ``docs/methodology/sim/README.md`` 与
    ``docs/methodology/results/al_upgrade_verification/REPORT.md``。
    """

    INIT = (REPO / "coscreen" / "al" / "__init__.py").read_text(encoding="utf-8")

    def test_no_unconditional_zero_dependency_claim(self):
        self.assertNotIn("运行时零第三方依赖（sklearn 仅用于测试基准）", self.INIT,
                         "包级 docstring 仍作「运行时零第三方依赖」的全局声明")
        self.assertNotIn("纯 Python 实现，运行时零第三方依赖", self.INIT)

    def test_declares_numpy_and_sklearn_carriers(self):
        self.assertIn("numpy", self.INIT, "应声明 balancer 的 numpy 依赖")
        self.assertIn("sklearn", self.INIT, "应声明 svm 的 sklearn 依赖")
        self.assertIn("balancer", self.INIT)
        self.assertIn("svm", self.INIT)

    def test_no_dangling_spec_md_reference(self):
        # 用拼接避免测试文件自身出现被禁字面量。
        dangling = "SPEC" + ".md"
        self.assertNotIn(dangling, self.INIT, "仍引用仓库中不存在的 SPEC.md")
        self.assertFalse((REPO / "SPEC.md").exists(), "前提：仓库确无 SPEC.md")

    def test_cited_docs_actually_exist(self):
        cited = {p for p in (
            "docs/methodology/sim/README.md",
            "docs/methodology/results/al_upgrade_verification/REPORT.md",
        ) if p in self.INIT}
        self.assertTrue(cited, "包级 docstring 应引用可复核的参数出处")
        for rel in cited:
            self.assertTrue((REPO / rel).is_file(), f"引用的 {rel} 不存在")

    def test_ranker_module_docstring_same_honesty(self):
        src = (REPO / "coscreen" / "al" / "ranker.py").read_text(encoding="utf-8")
        self.assertNotIn("运行时零第三方依赖（仅标准库）；sklearn 仅在 SVM", src)
        self.assertIn("numpy", src.split('"""')[1], "ranker 模块头应说明 numpy 依赖")

    def test_control_strict_stdlib_modules_still_stdlib(self):
        """常规对照：nb/strategies/simulation 确实只用标准库（声明未扩大）。"""
        for name in ("nb.py", "strategies.py", "simulation.py"):
            src = (REPO / "coscreen" / "al" / name).read_text(encoding="utf-8")
            self.assertNotIn("import numpy", src, f"{name} 不应引入 numpy")
            self.assertNotIn("import sklearn", src, f"{name} 不应引入 sklearn")


# ======================================================================
# b07·F1-120 —— compute() 的 ndim==1 校验
# ======================================================================
class F120BalancerNdimNails(unittest.TestCase):
    """修前实测（当前树复现）：2-D ``y`` 返回 ``shape (3, 2) sum 3.0``
    （两分支形状口径互斥：单类分支返回 ``(3,)``）。修复：非一维输入
    → ``ValueError``，消息含维度与 shape。"""

    def test_two_dimensional_input_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            BalancedWeights(9.8).compute(np.array([[1, 0], [0, 1], [1, 1]]))
        self.assertIn("一维", str(ctx.exception))
        self.assertIn("2", str(ctx.exception))

    def test_single_class_two_dimensional_input_rejected(self):
        """修前该分支返回 (3,)——与两类分支形状不一致，是最隐蔽的一半。"""
        with self.assertRaises(ValueError):
            BalancedWeights(9.8).compute(np.zeros((3, 2)))

    def test_regular_1d_output_bit_identical(self):
        """常规对照（R1）：1-D 输入的数值逐位不变（glm_verify_v2 §4 实测值）。"""
        w = BalancedWeights(9.8).compute(np.asarray([1, 1, 0]))
        self.assertEqual(w.shape, (3,))
        self.assertAlmostEqual(float(w[0]), 1.3611111111111112, delta=1e-15)
        self.assertAlmostEqual(float(w[2]), 0.2777777777777778, delta=1e-15)
        w6 = BalancedWeights(9.8).compute(np.asarray([1, 0, 0, 1, 0, 0]))
        self.assertEqual(w6.shape, (6,))
        self.assertAlmostEqual(float(w6[0]), 2.7222222222222223, delta=1e-15)

    def test_empty_and_single_class_1d_unchanged(self):
        self.assertEqual(BalancedWeights(9.8).compute([]).tolist(), [])
        self.assertEqual(BalancedWeights(9.8).compute([0, 0, 1]).shape, (3,))
        single = BalancedWeights(9.8).compute(np.zeros(3))
        self.assertEqual(single.tolist(), [1.0, 1.0, 1.0])


# ======================================================================
# b07·F1-121 —— ratio 有限性 + 极小 ratio 的数值兜底
# ======================================================================
class F121BalancerRatioNails(unittest.TestCase):
    """修前实测（当前树复现）：``ratio=inf`` → ``[4.0, 0, 0, 0]``（排除类
    总权重恒 0）；``ratio=1e-320`` → 500 个 NaN + RuntimeWarning。
    修复：``inf``/``nan`` 在构造期 ValueError；极小正 ratio（合法比例）
    仍在 compute 内以尺度等价公式兜住，产出有限权重。"""

    def test_infinite_ratio_rejected_at_construction(self):
        with self.assertRaises(ValueError) as ctx:
            BalancedWeights(float("inf"))
        self.assertIn("有限", str(ctx.exception))

    def test_nan_ratio_rejected(self):
        with self.assertRaises(ValueError):
            BalancedWeights(float("nan"))

    def test_negative_and_zero_ratio_still_rejected(self):
        """常规对照：原来的拒绝面保持不变（R2 不退让）。"""
        for bad in (0.0, -1.0):
            with self.subTest(ratio=bad), self.assertRaises(ValueError):
                BalancedWeights(bad)

    def test_tiny_ratio_produces_finite_weights(self):
        """修前 500 个 NaN；修后有限、比例仍是 ratio、总和仍是 n。"""
        with np.errstate(all="raise"):
            w = BalancedWeights(1e-320).compute(np.asarray([1, 0] * 500))
        self.assertEqual(int(np.isnan(w).sum()), 0, "极小 ratio 仍产出 NaN")
        self.assertEqual(int(np.isinf(w).sum()), 0, "极小 ratio 仍产出 inf")
        total = float(w.sum())
        self.assertAlmostEqual(total, 1000.0, delta=1e-6)
        ratio = float(w[0::2].sum() / w[1::2].sum())
        self.assertAlmostEqual(ratio, 1e-320, delta=1e-330)

    def test_normal_ratios_bit_identical(self):
        """常规对照（R1）：正常 ratio 走原路径，数值与基线逐位相同。"""
        cases = [
            (np.asarray([1, 1, 0]), 9.8, 3.0, 1.3611111111111112),
            (np.asarray([1, 0]), 9.8, 2.0000000000000004, 1.814814814814815),
            (np.asarray([1] * 50 + [0] * 4950), 9.8, 5000.000000000003,
             90.74074074074075),
        ]
        for y, ratio, expected_sum, first in cases:
            with self.subTest(n=len(y)):
                w = BalancedWeights(ratio).compute(y)
                self.assertEqual(float(w.sum()), expected_sum)
                self.assertEqual(float(w[0]), first)
        # ratio=1.0 与 sklearn balanced 等价（两类等量时权重全 1）
        self.assertEqual(
            BalancedWeights(1.0).compute(np.asarray([1, 0, 1, 0])).tolist(),
            [1.0, 1.0, 1.0, 1.0],
        )


# ======================================================================
# b07·F1-124 —— 「总和恰为样本数」是过度声明
# ======================================================================
class F124BalancerSumWordingNails(unittest.TestCase):
    """修前实测（当前树复现）：1560 组 (n_rel, n_ir, ratio) 中 989 组
    ``sum != n``（``[1,0]`` → ``2.0000000000000004``）。修复：docstring
    如实写成「≈ 样本数」并点明末位 ulp 偏差，不再承诺按位精确相等。
    """

    def test_docstrings_do_not_claim_exact_sum(self):
        module_doc = (REPO / "coscreen" / "al" / "balancer.py").read_text(encoding="utf-8")
        self.assertNotIn("``sum(weights) == n_samples``", module_doc)
        self.assertNotIn("总和恰为样本数", module_doc)
        compute_doc = BalancedWeights.compute.__doc__ or ""
        self.assertNotIn("总和恰为样本数", compute_doc)
        self.assertIn("浮点", compute_doc, "应如实说明浮点舍入偏差")

    def test_measured_deviation_is_reported_in_docstring(self):
        compute_doc = BalancedWeights.compute.__doc__ or ""
        self.assertIn("2.0000000000000004", compute_doc,
                      "docstring 应给出实测偏差例子，读者可自行复核")

    def test_deviation_still_exists_and_is_tiny(self):
        """常规对照：偏差确实存在且只在上一个 ulp 附近（不是数值错误）。"""
        w = BalancedWeights(9.8).compute(np.asarray([1, 0]))
        self.assertNotEqual(float(w.sum()), 2.0)
        self.assertLess(abs(float(w.sum()) - 2.0), 1e-15)


# ======================================================================
# b09·F2-14 —— frozen dataclass 内的 dict 字段全进程可写
# ======================================================================
class F214ProfileImmutabilityNails(unittest.TestCase):
    """修前实测（当前树复现）：``PROFILES["tuned"].classifier_params["alpha"]
    = 999`` 后全局可见 ``{'alpha': 999}``——frozen 只挡属性重绑定。
    修复：三个 dict 字段一律 ``MappingProxyType``。"""

    def test_params_are_read_only(self):
        for name, profile in PROFILES.items():
            with self.subTest(profile=name):
                for field in ("classifier_params", "balancer_params", "feature_params"):
                    mapping = getattr(profile, field)
                    with self.assertRaises(TypeError):
                        mapping["injected"] = object()

    def test_no_cross_profile_pollution(self):
        """修前就地改 tuned 的 alpha 会污染后续所有任务。"""
        p = PROFILES["tuned"]
        with self.assertRaises(TypeError):
            p.classifier_params["alpha"] = 999
        self.assertEqual(dict(PROFILES["tuned"].classifier_params), {"alpha": 3.822})
        self.assertEqual(dict(PROFILES["tuned"].feature_params)["max_df"], 0.95)

    def test_downstream_unpacking_still_works(self):
        """常规对照（R1）：``**`` 解包与 ``.get`` 照常工作（只读不影响消费方）。"""
        from coscreen.al.features import TfidfVectorizer

        vec = TfidfVectorizer(**PROFILES["tuned"].feature_params)
        self.assertEqual(vec.ngram_range, (1, 2))
        self.assertTrue(vec.sublinear_tf)
        self.assertEqual(vec.max_df, 0.95)
        self.assertEqual(PROFILES["tuned"].balancer_params.get("ratio"), 9.8)
        self.assertEqual(len(PROFILES), 4)

    def test_ranker_still_builds_every_profile(self):
        for name in sorted(PROFILES):
            with self.subTest(profile=name):
                ranker = ActiveLearningRanker(profile=name)
                self.assertIsNotNone(ranker._classifier)
                self.assertIsNotNone(ranker._vectorizer)


# ======================================================================
# b09·F2-15 —— classifier/balancer 值域无加载期校验
# ======================================================================
class F215ProfileDomainNails(unittest.TestCase):
    """修前实测（当前树复现）：``profiles.py`` 全文 ``assert|raise`` 0 命中，
    未知 classifier 静默落 NB、未知 balancer 静默降级为不平衡。修复：
    ``ALProfile.__post_init__`` 校验两个值域。"""

    def test_unknown_classifier_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            ALProfile(name="x", label="x", classifier="random_forest",
                      classifier_params={}, balancer=None, balancer_params={},
                      feature_params={}, description="")
        self.assertIn("classifier", str(ctx.exception))

    def test_unknown_balancer_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            ALProfile(name="x", label="x", classifier="nb", classifier_params={},
                      balancer="oversample", balancer_params={},
                      feature_params={}, description="")
        self.assertIn("balancer", str(ctx.exception))

    def test_shipped_profiles_all_valid(self):
        """常规对照：四个预设全部通过校验（加载期不误伤合法值）。"""
        self.assertEqual(sorted(PROFILES), ["cjk", "legacy", "svm", "tuned"])
        for name, profile in PROFILES.items():
            with self.subTest(profile=name):
                self.assertIn(profile.classifier, ("nb", "svm"))
                self.assertIn(profile.balancer, (None, "balanced"))

    def test_default_profile_unchanged(self):
        self.assertEqual(DEFAULT_PROFILE, "legacy")


# ======================================================================
# b09·F2-16 —— svm 与 cjk 预设运行时完全相同
# ======================================================================
class F216CjkDescriptionNails(unittest.TestCase):
    """修前实测（当前树复现）：两预设向量器四属性全等，cjk 却称
    「SVM + CJK 二元组,适合中英混合文献」，把默认能力当独有卖点。
    修复：cjk 的 description 如实写「与 SVM 模式运行时完全相同」。"""

    def test_cjk_description_is_honest(self):
        desc = PROFILES["cjk"].description
        self.assertIn("相同", desc, "cjk 描述应如实说明与 svm 运行时相同")
        self.assertNotIn("适合中英混合文献", desc,
                         "cjk 描述仍把默认能力当作独有卖点")

    def test_profiles_really_are_equivalent(self):
        """事实基础：两预设的分类器/平衡器/参数/向量器属性全等。"""
        from coscreen.al.features import TfidfVectorizer

        svm, cjk = PROFILES["svm"], PROFILES["cjk"]
        self.assertEqual(svm.classifier, cjk.classifier)
        self.assertEqual(svm.balancer, cjk.balancer)
        self.assertEqual(dict(svm.classifier_params), dict(cjk.classifier_params))
        v1 = TfidfVectorizer(**svm.feature_params)
        v2 = TfidfVectorizer(**cjk.feature_params)
        for attr in ("ngram_range", "sublinear_tf", "max_df", "cjk_bigram"):
            self.assertEqual(getattr(v1, attr), getattr(v2, attr), attr)

    def test_cjk_bigram_advantage_preserved(self):
        """常规对照（R2）：所有预设都不关闭 cjk_bigram（不因改文案丢优势）。"""
        from coscreen.al.features import TfidfVectorizer

        for name, profile in PROFILES.items():
            with self.subTest(profile=name):
                vec = TfidfVectorizer(**profile.feature_params)
                self.assertTrue(vec.cjk_bigram, f"{name} 关闭了 cjk_bigram")


class F216FrontendCopyNails(unittest.TestCase):
    """b09·F2-16 的前端面（r9 L4b 登记补齐）：后端 profiles.py 已把 cjk 的
    description 改成「与 SVM 模式运行时完全相同…」，而
    ``custom_frontend/al_profile_selector.js`` 仍在 label/description 两张表里
    宣称「中日韩文本」「字符二元组特征」——前端本地表会覆盖后端 label/
    description，下拉文案因此把已被否定的独有卖点又讲了一遍。

    修后：前端 cjk 的 description 逐字复用后端 description（任一侧漂移即红），
    label 不再带 CJK 专属措辞，两条文案都在十语言词典里有十个非空槽。
    """

    SELECTOR = REPO / "custom_frontend" / "al_profile_selector.js"
    LOCALE = REPO / "custom_frontend" / "workflow_locale.js"
    CJK_LABEL = "中文兼容预设"

    def _selector_source(self) -> str:
        return self.SELECTOR.read_text(encoding="utf-8")

    def _locale_rows(self) -> list[list[str]]:
        import json

        rows = []
        for line in self.LOCALE.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text.startswith("["):
                continue
            try:
                row = json.loads(text.rstrip(","))
            except ValueError:
                continue
            if isinstance(row, list):
                rows.append(row)
        return rows

    def _row_for(self, key: str) -> list[str] | None:
        for row in self._locale_rows():
            if row and row[0] == key:
                return row
        return None

    def test_frontend_cjk_description_is_the_backend_sentence(self):
        """前端 cjk 描述必须逐字等于后端口径（复制旧文案即红）。"""
        source = self._selector_source()
        self.assertIn(PROFILES["cjk"].description, source,
                      "前端 cjk 描述与 profiles.py 的新口径脱钩")
        self.assertIn("完全相同", source)
        self.assertNotIn("中日韩", source,
                         "前端仍宣称中日韩文本专属能力（已被后端否定）")
        self.assertNotIn("包含中日韩文本的字符二元组特征", source,
                         "前端仍把默认的字符二元组当作 cjk 独有卖点")

    def test_frontend_cjk_label_drops_the_exclusive_claim(self):
        """label 不再用「中日韩文本 SVM」这类专属措辞。"""
        source = self._selector_source()
        self.assertIn(f'"cjk": "{self.CJK_LABEL}"', source,
                      "cjk label 未改为兼容口径的表述")
        self.assertNotIn('"cjk": "中日韩文本 SVM 预设"', source)

    def test_both_cjk_strings_are_registered_ten_languages(self):
        """两条文案都必须在十语言词典里：10 槽、非空、占位符一致。"""
        import re

        for key in (self.CJK_LABEL, PROFILES["cjk"].description):
            with self.subTest(key=key[:24]):
                row = self._row_for(key)
                self.assertIsNotNone(row, f"词典缺少词条：{key!r}")
                self.assertEqual(10, len(row), f"词条不是十语言：{key!r}")
                self.assertTrue(all(slot.strip() for slot in row),
                                f"词条存在空槽：{key!r}")
                stamps = [sorted(re.findall(r"\{\d+\}", slot)) for slot in row]
                self.assertEqual([stamps[0]] * 10, stamps,
                                 f"词条占位符在十槽间不一致：{key!r}")

    def test_old_exclusive_wording_removed_from_dictionary(self):
        """旧词条随文案替换一并移除（避免死键继续被界面命中）。"""
        old = "线性 SVM，包含中日韩文本的字符二元组特征；效果取决于当前任务的数据与标签。"
        self.assertIsNone(self._row_for(old), "旧 cjk 描述词条应已删除")
        self.assertNotIn(old, self._selector_source())


# ======================================================================
# b09·F2-17 —— 规格书/验证报告引用全树不存在
# ======================================================================
class F217ProfileProvenanceNails(unittest.TestCase):
    """修前实测（当前树复现）：``profiles.py:6-7`` 引
    ``docs/handoff/2026-09-30-al-model-upgrade-spec.md``、``:20-22`` 引
    ``research/results/al_upgrade_verification/REPORT.md``——两者全树 0
    命中。修复：改引真实存在的
    ``docs/methodology/results/al_upgrade_verification/REPORT.md`` 与
    ``docs/methodology/sim/README.md``。"""

    SRC = (REPO / "coscreen" / "al" / "profiles.py").read_text(encoding="utf-8")

    def test_no_dangling_handoff_spec_reference(self):
        self.assertNotIn("2026-09-30-al-model-upgrade-spec.md", self.SRC,
                         "仍引用不存在的 handoff 规格书")
        self.assertFalse(
            (REPO / "docs" / "handoff" / "2026-09-30-al-model-upgrade-spec.md").exists()
        )

    def test_no_dangling_research_prefix(self):
        self.assertNotIn("research/results/al_upgrade_verification", self.SRC,
                         "仍引用不存在的 research/ 目录（仓库无该目录）")
        self.assertFalse((REPO / "research").exists(), "前提：仓库确无 research/")

    def test_every_cited_doc_exists(self):
        """反向钉子：修后引用的每个 docs/ 路径必须真实存在。"""
        import re

        cited = set(re.findall(
            r"``(docs/[A-Za-z0-9_./-]+\.(?:md|csv|json|py))``", self.SRC
        ))
        self.assertTrue(cited, "profiles.py 应引用可复核的出处")
        for rel in sorted(cited):
            self.assertTrue((REPO / rel).is_file(), f"引用的 {rel} 不存在")

    def test_quantified_claims_traceable_to_report(self):
        """量化声明（宏 WSS +0.580 / +0.495 / +0.148）在报告中确有出处。"""
        report = (REPO / "docs" / "methodology" / "results" /
                  "al_upgrade_verification" / "REPORT.md").read_text(encoding="utf-8")
        for number in ("+0.580", "+0.495", "+0.148"):
            self.assertIn(number, report, f"报告缺 {number} 的原始记录")
        for number in ("+0.580", "+0.495", "+0.148"):
            self.assertIn(number, self.SRC)


# ======================================================================
# b10·F2-20 —— ranker 重复键
# ======================================================================
class F220RankerDuplicateKeysNails(unittest.TestCase):
    """修前实测（当前树复现）：``articles=[k0,k1,k2,k0]`` → ``n_labeled=4``
    （3 篇唯一）；重复候选 ``k1`` → ``order=['k1','k1','k2']`` 而
    ``scores`` 只有 2 键。修复：训练/候选都按去重后的键序构造。"""

    def test_training_set_counts_unique_keys(self):
        arts = [_article("k0"), _article("k1"), _article("k2"), _article("k0")]
        result = ActiveLearningRanker(profile=None, min_labeled=1).rank(
            arts, {"k0": "include", "k1": "exclude", "k2": "exclude"}
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.n_labeled, 3, "重复键被双计进 n_labeled")
        self.assertEqual(result.model_meta["n_include"], 1)
        self.assertEqual(result.model_meta["n_exclude"], 2)

    def test_candidate_order_has_no_duplicates(self):
        arts = [_article("k0"), _article("k1"), _article("k1"), _article("k2")]
        result = ActiveLearningRanker(profile=None, min_labeled=1).rank(
            arts, {"k0": "include"}
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.order, list(dict.fromkeys(result.order)),
                         f"order 含重复键：{result.order}")
        self.assertEqual(set(result.order), set(result.scores),
                         "order 与 scores 键集不一致（评分被折叠）")

    def test_later_duplicate_content_wins(self):
        """键重复时后者覆盖前者内容（与主键语义一致）——不是丢弃后者。"""
        first = _article("k0", "entirely different words zebra")
        later = _article("dup", "first text words")
        replacement = _article("dup", "second text words")
        result = ActiveLearningRanker(profile=None, min_labeled=1).rank(
            [first, later, replacement, _article("k9")], {"k0": "include"}
        )
        self.assertIsNotNone(result)
        self.assertEqual(sorted(result.scores), ["dup", "k9"])

    def test_regular_input_bit_identical(self):
        """常规对照（R1）：无重复键时输出与基线逐位相同。"""
        arts = [_article("k0"), _article("k1"), _article("k2")]
        result = ActiveLearningRanker(profile=None, min_labeled=1).rank(
            arts, {"k0": "include", "k1": "exclude"}
        )
        self.assertEqual(result.order, ["k2"])
        self.assertEqual(result.n_labeled, 2)
        self.assertEqual(result.model_meta["n_include"], 1)
        self.assertEqual(result.model_meta["n_exclude"], 1)


# ======================================================================
# b10·F2-21 —— min_labeled 无下界
# ======================================================================
class F221MinLabeledBoundNails(unittest.TestCase):
    """修前实测（当前树复现）：``min_labeled=0/-1`` 且 0 标签时返回
    ``ValueError: 训练集为空``（由 NB.fit 抛出），而正确语义是 ``None``。
    修复：下界固定为 ``max(1, min_labeled)``。"""

    def test_zero_and_negative_min_labeled_return_none(self):
        arts = [_article("k0"), _article("k1")]
        for value in (0, -1, -100):
            with self.subTest(min_labeled=value):
                result = ActiveLearningRanker(
                    profile=None, min_labeled=value
                ).rank(arts, {})
                self.assertIsNone(result, "无标签时应返回 None 而非抛错")

    def test_positive_threshold_still_returns_none_below(self):
        """常规对照：阈值语义不变（5 条门槛、0 标签 → None）。"""
        self.assertIsNone(
            ActiveLearningRanker(profile=None, min_labeled=5).rank(
                [_article("k0")], {}
            )
        )
        result = ActiveLearningRanker(profile=None, min_labeled=1).rank(
            [_article("k0"), _article("k1")], {"k0": "include", "k1": "exclude"}
        )
        self.assertIsNotNone(result)


# ======================================================================
# b10·F2-26 —— simulation 的键唯一性
# ======================================================================
class F226SimulationUniqueKeysNails(unittest.TestCase):
    """修前实测（当前树复现）：9 条文献含重复键 ``dup`` →
    ``n_records=9``、``n_relevant=2`` 却报 ``r95=1.0``（同一记录被筛两次、
    另一条相关记录从未被筛）；去重对照 ``n_to_95=8``。修复：重复键
    → ``ValueError``（回放前提不再静默违反）。"""

    @staticmethod
    def _dataset():
        arts = [_article(f"r{i}") for i in range(7)] + [_article("dup"), _article("dup")]
        gold = {a.zotero_key: "exclude" for a in arts}
        gold["dup"] = "include"
        gold["r3"] = "include"
        return arts, gold

    def test_duplicate_keys_rejected(self):
        arts, gold = self._dataset()
        with self.assertRaises(ValueError) as ctx:
            replay(arts, gold, strategy="max")
        message = str(ctx.exception)
        self.assertIn("唯一", message)
        self.assertIn("dup", message, "消息应指出重复键，便于定位")

    def test_random_baseline_rejects_too(self):
        arts, gold = self._dataset()
        with self.assertRaises(ValueError):
            simulate_random_baseline(arts, gold)

    def test_deduplicated_corpus_gives_stable_metrics(self):
        """常规对照（R1）：去重后的同一语料照常回放，指标可用。"""
        arts = [_article(f"r{i}") for i in range(7)] + [_article("dup")]
        gold = {a.zotero_key: "exclude" for a in arts}
        gold["dup"] = "include"
        gold["r3"] = "include"
        result = replay(arts, gold, strategy="max")
        self.assertEqual(result.n_records, 8)
        self.assertEqual(result.n_relevant, 2)
        self.assertEqual(result.n_to_95, 8)
        self.assertAlmostEqual(result.wss95, -0.05, delta=1e-12)


# ======================================================================
# b10·F2-27 —— 非法黄金标签的整值回显
# ======================================================================
class F227LabelEchoTruncationNails(unittest.TestCase):
    """修前实测（当前树复现）：5000 字符非法标签 → 异常消息 5036 字符且
    含完整 token；同函数缺失键分支只列 5 个键。修复：回显截断到 60 字符。"""

    def test_long_label_message_is_truncated(self):
        token = "X" * 5000
        with self.assertRaises(ValueError) as ctx:
            replay([_article("k0")], {"k0": token})
        message = str(ctx.exception)
        self.assertLess(len(message), 200, f"异常消息仍过长：{len(message)} 字符")
        self.assertNotIn(token, message, "异常消息仍回显完整超长标签")

    def test_short_label_still_fully_reported(self):
        """常规对照（R1/R2）：短标签原样回显，诊断信息不缩水。"""
        with self.assertRaises(ValueError) as ctx:
            replay([_article("k0")], {"k0": "maybewrong"})
        self.assertIn("'maybewrong'", str(ctx.exception))

    def test_missing_key_branch_unchanged(self):
        with self.assertRaises(KeyError) as ctx:
            replay([_article(f"k{i}") for i in range(7)], {})
        message = str(ctx.exception)
        self.assertIn("7 条", message)
        self.assertIn("例如", message)


# ======================================================================
# b10·F2-28 —— n_relevant==0 早退绕过策略校验
# ======================================================================
class F228StrategyBeforeEarlyExitNails(unittest.TestCase):
    """修前实测（当前树复现）：全 exclude + ``strategy='bogus'`` →
    ``NO RAISE``，返回 ``SimResult(strategy='bogus')``；含 1 条 include 的
    对照才抛 ValueError。修复：策略校验先于所有早退。"""

    def test_unknown_strategy_rejected_with_zero_relevant(self):
        arts = [_article(f"k{i}") for i in range(3)]
        gold = {a.zotero_key: "exclude" for a in arts}
        with self.assertRaises(ValueError) as ctx:
            replay(arts, gold, strategy="bogus")
        self.assertIn("未知查询策略", str(ctx.exception))

    def test_unknown_strategy_rejected_even_for_empty_corpus(self):
        with self.assertRaises(ValueError):
            replay([], {}, strategy="bogus")

    def test_valid_strategy_zero_relevant_still_succeeds(self):
        """常规对照（R2）：合法策略 + 全 exclude 是正常输入，不得变为错误。"""
        arts = [_article(f"k{i}") for i in range(3)]
        gold = {a.zotero_key: "exclude" for a in arts}
        for strategy in ("max", "uncertainty", "mixed"):
            with self.subTest(strategy=strategy):
                result = replay(arts, gold, strategy=strategy)
                self.assertEqual(result.n_relevant, 0)
                self.assertEqual(result.strategy, strategy)
                self.assertEqual(result.n_to_95, 0)


# ======================================================================
# b10·F2-37 —— stop_registry 自称「供前端读取」
# ======================================================================
class F237RegistryConsumptionNails(unittest.TestCase):
    """修前实测（当前树复现）：``stop_registry.py:3-5`` 称「供 API 与前端
    停止面板读取」，但前端只请求 ``/al/stop-certificate/check``
    （``al_stop_panel.js:160``），从不读注册表。修复：文档如实写明唯一
    消费方是 ``stop-rules`` 端点、前端不读。"""

    SRC = (REPO / "coscreen" / "al" / "stop_registry.py").read_text(encoding="utf-8")

    def test_docstring_does_not_claim_frontend_reads_registry(self):
        module_doc = self.SRC.split('"""')[1]
        self.assertNotIn("与前端停止面板读取", module_doc,
                         "文档仍声称前端面板读取本注册表")
        self.assertIn("stop-rules", module_doc,
                      "应写明唯一消费方是 stop-rules 端点")

    def test_frontend_really_does_not_consume_it(self):
        """事实基础：前端源码里没有对 /al/stop-rules 的请求。"""
        panel = (REPO / "custom_frontend" / "al_stop_panel.js").read_text(encoding="utf-8")
        self.assertNotIn("stop-rules", panel)
        self.assertIn("stop-certificate/check", panel)

    def test_no_confidence_overclaim_in_certificate_description(self):
        """注册表不得再虚标固定 95% 置信度（与 main.py/面板已修口径一致）。"""
        self.assertNotIn("置信度保证总召回", self.SRC)
        self.assertNotIn("以 ≥95% 置信度", self.SRC)
        self.assertIn("并非固定 95%", self.SRC)


# ======================================================================
# b10·F2-38 —— 三条规则键集不齐
# ======================================================================
class F238RegistryKeySetNails(unittest.TestCase):
    """修前实测（当前树复现）：``manual`` 无 deprecated/recommended、
    ``streak`` 无 recommended、``certificate`` 无 deprecated；未约定
    缺键语义。修复：四条基础键齐全且为 bool。"""

    REQUIRED = ("label", "description", "deprecated", "recommended")

    def test_every_rule_has_full_key_set(self):
        self.assertLessEqual({"manual", "streak", "certificate"}, set(STOP_RULES))
        for name, rule in STOP_RULES.items():
            with self.subTest(rule=name):
                for key in self.REQUIRED:
                    self.assertIn(key, rule, f"{name} 缺键 {key}")
                self.assertIsInstance(rule["deprecated"], bool)
                self.assertIsInstance(rule["recommended"], bool)
                self.assertTrue(rule["label"])
                self.assertTrue(rule["description"])

    def test_flags_match_documented_semantics(self):
        self.assertTrue(STOP_RULES["streak"]["deprecated"])
        self.assertFalse(STOP_RULES["streak"]["recommended"])
        self.assertTrue(STOP_RULES["certificate"]["recommended"])
        self.assertFalse(STOP_RULES["certificate"]["deprecated"])
        self.assertFalse(STOP_RULES["manual"]["deprecated"])
        self.assertFalse(STOP_RULES["manual"]["recommended"])

    def test_missing_key_semantics_documented(self):
        module_doc = (REPO / "coscreen" / "al" / "stop_registry.py").read_text(
            encoding="utf-8").split('"""')[1]
        self.assertIn("False", module_doc, "应约定缺键按 False 读取")
        self.assertIn("bool", module_doc, "应写明两条布尔量的类型")


# ======================================================================
# b10·F2-39 —— mixed 的 O(n²)
# ======================================================================
class F239MixedComplexityNails(unittest.TestCase):
    """修前实测（当前树复现）：``order.index(key)`` 为 O(n)，整体 ~n²/20；
    8k→64k 每翻倍 ×3.7；40k 单次 0.324s（每秒 20 次随机命中 × 40k 查找）。
    修复：位置表 O(1)，40k 降到 0.023s（同一进程内 13.9× 加速）。

    断言方式：**同一进程内与基线算法（``_reference_mixed``，逐行照抄旧实现）
    对比耗时**——机器速度同时影响两者，比值抵消；阈值取 3×（实测 13.9×）。
    这样钉的是"二次查找已消失"这一机制，而不是绝对墙钟。
    """

    N = 40000
    REPS = 3

    @classmethod
    def _keys_scores(cls):
        keys = [f"k{i}" for i in range(cls.N)]
        scores = {k: (i % 997) / 997 for i, k in enumerate(keys)}
        return keys, scores

    @classmethod
    def _best(cls, fn):
        """对 ``fn(keys, scores, seed)`` 取 REPS 次中的最小耗时。"""
        keys, scores = cls._keys_scores()
        best = float("inf")
        for _ in range(cls.REPS):
            start = time.perf_counter()
            fn(keys, scores, 42)
            best = min(best, time.perf_counter() - start)
        return best

    @classmethod
    def _best_mixed(cls):
        keys, scores = cls._keys_scores()
        best = float("inf")
        for _ in range(cls.REPS):
            start = time.perf_counter()
            query_order(keys, scores, "mixed", 42)
            best = min(best, time.perf_counter() - start)
        return best

    def test_reference_implementation_is_measurable(self):
        """对照（前提复核）：基线算法在该规模下确有可测耗时。"""
        self.assertGreater(self._best(_reference_mixed), 0.0)

    def test_mixed_beats_reference_by_wide_margin(self):
        current = self._best_mixed()
        reference = self._best(_reference_mixed)
        self.assertLess(
            current * 3, reference,
            f"mixed 耗时 {current:.4f}s 未显著快于基线 {reference:.4f}s"
            f"（{reference / current:.1f}×，要求 ≥3×）——二次查找可能仍在",
        )

    def test_mixed_output_bit_identical_to_reference(self):
        """常规对照（R1）：改复杂度不得改输出——与基线实现逐位比对。"""
        rnd = random.Random(11)
        for n in (1, 2, 3, 17, 64, 200, 1000):
            for seed in (0, 1, 42, 7, 12345):
                keys = [f"k{i}" for i in range(n)]
                scores = {k: round(rnd.random(), 6) for k in keys}
                with self.subTest(n=n, seed=seed):
                    self.assertEqual(
                        query_order(keys, scores, "mixed", seed),
                        _reference_mixed(keys, scores, seed),
                    )

    def test_duplicate_keys_still_match_reference(self):
        """键有重复时退回旧语义，输出同样逐位相同。"""
        for keys in (["a", "a"], ["a", "b", "a", "c"], ["x", "x", "x"]):
            for seed in (1, 42, 99):
                scores = {"a": 0.3, "b": 0.9, "c": 0.1, "x": 0.5}
                with self.subTest(keys=keys, seed=seed):
                    self.assertEqual(
                        query_order(keys, scores, "mixed", seed),
                        _reference_mixed(keys, scores, seed),
                    )

    def test_permutation_contract_preserved(self):
        keys = [f"k{i}" for i in range(500)]
        scores = {k: (i % 13) / 13 for i, k in enumerate(keys)}
        order = query_order(keys, scores, "mixed", 42)
        self.assertEqual(sorted(order), sorted(keys))


# ======================================================================
# b10·F2-40 —— scores 值域零校验
# ======================================================================
class F240ScoreDomainNails(unittest.TestCase):
    """修前实测（当前树复现）：str/None 值三策略全抛裸 ``TypeError``；
    NaN 静默给出无定义序。修复：非数值 / NaN / 超大 int → 可读
    ``ValueError``；``±inf`` 仍接受（有确定序）。"""

    def test_non_numeric_values_raise_value_error(self):
        for bad in ("x", None, [1], {"a": 1}):
            for strategy in ("max", "uncertainty", "mixed"):
                with self.subTest(value=bad, strategy=strategy):
                    with self.assertRaises(ValueError):
                        query_order(["a", "b"], {"a": bad, "b": 0.5}, strategy, 42)

    def test_nan_rejected(self):
        for strategy in ("max", "uncertainty", "mixed"):
            with self.subTest(strategy=strategy):
                with self.assertRaises(ValueError) as ctx:
                    query_order(["a", "b"], {"a": float("nan"), "b": 0.5}, strategy, 42)
                self.assertIn("NaN", str(ctx.exception))

    def test_bool_is_still_accepted(self):
        """R2：``bool`` 是 ``int`` 子类，旧实现照常当 0/1 用——不放宽成拒绝。"""
        for strategy in ("max", "uncertainty", "mixed"):
            with self.subTest(strategy=strategy):
                order = query_order(["a", "b"], {"a": True, "b": False}, strategy, 42)
                self.assertEqual(sorted(order), ["a", "b"])
        self.assertEqual(
            query_order(["a", "b"], {"a": True, "b": 0.5}, "max", 42), ["a", "b"]
        )

    def test_oversized_int_raises_value_error(self):
        """10**400 会在比较时 OverflowError——同一异常族收口。"""
        with self.assertRaises(ValueError):
            query_order(["a", "b"], {"a": 10 ** 400, "b": 0.5}, "uncertainty", 42)

    def test_regular_scores_bit_identical(self):
        """常规对照（R1）：正常有限 float 的三种策略输出不变。"""
        keys, scores = ["a", "b", "c"], {"a": 0.9, "b": 0.5, "c": 0.1}
        self.assertEqual(query_order(keys, scores, "max", 42), ["a", "b", "c"])
        self.assertEqual(query_order(keys, scores, "uncertainty", 42), ["b", "a", "c"])
        self.assertEqual(query_order(keys, scores, "mixed", 42),
                         _reference_mixed(keys, scores, 42))

    def test_infinite_scores_still_accepted(self):
        """R2：±inf 有确定序，属合法输入，不得新增拒绝。"""
        self.assertEqual(
            query_order(["a", "b"], {"a": float("inf"), "b": 0.5}, "max", 42),
            ["a", "b"],
        )
        self.assertEqual(
            query_order(["a", "b"], {"a": float("-inf"), "b": 0.5}, "max", 42),
            ["b", "a"],
        )

    def test_missing_scores_default_zero_unchanged(self):
        self.assertEqual(query_order(["a", "b"], {}, "max", 42), ["a", "b"])
        self.assertEqual(query_order(["a", "b"], {"b": 0.1}, "max", 42), ["b", "a"])


# ======================================================================
# b10·F2-41 —— scores=None 三策略行为不一致
# ======================================================================
class F241ScoresNoneNails(unittest.TestCase):
    """修前实测（当前树复现）：max/uncertainty 抛 AttributeError，mixed
    静默退化为随机序。修复：三策略一律按"无任何分数"处理（等价空字典）。"""

    def test_all_strategies_treat_none_as_empty(self):
        keys = ["a", "b", "c"]
        for strategy in ("max", "uncertainty", "mixed"):
            with self.subTest(strategy=strategy):
                self.assertEqual(
                    query_order(keys, None, strategy, 42),
                    query_order(keys, {}, strategy, 42),
                    f"{strategy} 对 scores=None 与 scores={{}} 的结果不同",
                )

    def test_none_does_not_raise(self):
        for strategy in ("max", "uncertainty", "mixed"):
            with self.subTest(strategy=strategy):
                query_order(["a", "b"], None, strategy, 42)

    def test_regular_dicts_unchanged(self):
        """常规对照（R1）：真实分数字典的行为逐位不变。"""
        keys, scores = ["a", "b", "c"], {"a": 0.9, "b": 0.5, "c": 0.1}
        self.assertEqual(query_order(keys, scores, "max", 42), ["a", "b", "c"])
        self.assertEqual(query_order(keys, scores, "uncertainty", 42), ["b", "a", "c"])
        self.assertEqual(query_order(keys, scores, "mixed", 42),
                         _reference_mixed(keys, scores, 42))


if __name__ == "__main__":
    unittest.main()
