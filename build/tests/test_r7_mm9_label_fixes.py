"""r7 修复泳道「MM9-14 效应量尺度标签」钉子测试。

MM9-14（deepseek 复审确认，详录 out/external_reviews/mm9_verify_g3.md §3）：
``effect_scale_label`` 的 ``"; results shown as X"`` 后缀描述的是合成路径
（``review_analysis._synthesize_rows``）独有的显示变换（FISHER_Z→tanh、
LOG_*→exp、LOGIT_PROP→expit），却被原样接到从不套用该变换的 meta 回归与
漏斗路径（``advanced_analysis.meta_regression`` / ``small_study_effects``），
导出 JSON 对数值尺度作出错误陈述（z 值被标 "shown as r"、log 值被标
"shown as ratio of means"）。修复把共享标签收敛为纯分析尺度（对照
``measure_registry`` 的 scale 词表 'log'|'natural'|'fisher_z'|'logit'），
"以何尺度呈现"后缀只在合成路径拼接（``_synthesis_effect_scale_label``）。

把对应修复回退，下列用例必须变红：
- LOG_ROM 漏斗：points[].effect 为对数值而标签只说 "log"（回退后标签变
  "log; results shown as ratio of means"，与对数点值矛盾，用例红）；
- FISHER_Z 路径：effect 为 z 而标签不再说 "shown as r"（回退后标签变
  "Fisher z; results shown as r"，用例红）；
- 合成路径（_synthesize_rows，经 synthesize/子群等真实入口）标签字面
  保持修复前原样（后缀被挪走、改写或双重拼接，用例红）；
- 共享标签与 measure_registry 词表逐一对照（手写链漂移即红）。
"""
from __future__ import annotations

import math
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen import db as db_mod
from coscreen.advanced_analysis import meta_regression, small_study_effects
from coscreen.measure_registry import MEASURE_REGISTRY
from coscreen.models import Article
from coscreen.review_analysis import (
    MEASURES,
    effect_scale_label,
    save_effect,
    save_study,
    synthesize,
)

_NOW = "2026-01-01T00:00:00"

# 修复前 _synthesize_rows 输出的 effect_scale 字面（git 5cb6314 口径）；
# 合成路径是后缀唯一为真的地方，钉死这些字面防回归。
_PRE_FIX_SYNTHESIS_LABELS = {
    "RR": "log", "OR": "log", "HR": "log", "RATE_RATIO": "log",
    "FISHER_Z": "Fisher z; results shown as r",
    "LOGIT_PROP": "logit; results shown as proportion",
    "LOG_RATE": "log; results shown as rate",
    "LOG_ROM": "log; results shown as ratio of means",
    "PAIRED_OR": "log; results shown as matched-pair odds ratio",
    "RD": "natural", "MD": "natural", "SMD": "natural", "MEAN": "natural",
    "PAIRED_RD": "natural", "PHI": "natural", "R_EQUIV_APPROX": "natural",
    "SMCR": "natural", "SMCC": "natural",
}


class Mm9LabelFixture(unittest.TestCase):
    """12 研究 × FISHER_Z / LOG_ROM 双层 + 数值协变量的独立临时库。"""

    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="mm9fix_labels_"))
        cls.db = cls.dir / "review.db"
        db_mod.init_db(cls.db)
        db_mod.upsert_articles(
            cls.db, [Article(zotero_key=f"K{i:02d}", title=f"T{i:02d}") for i in range(1, 13)], [])
        for i in range(1, 13):
            save_study(cls.db, f"S{i:02d}", f"Study {i:02d}", [f"K{i:02d}"])
        # FISHER_Z 层：estimate 已是 z；LOG_ROM 层：estimate 已是 log 尺度值。
        # 两个 measure 都不在 RATIOS，advanced 路径 y=estimate 原值直通。
        for i in range(1, 13):
            save_effect(cls.db, f"S{i:02d}", "CF", "OF", "TF", "FISHER_Z",
                        0.10 + 0.01 * i, 0.20 + 0.01 * i, f"K{i:02d}")
            save_effect(cls.db, f"S{i:02d}", "CR", "OF", "TR", "LOG_ROM",
                        -0.30 + 0.01 * i, 0.10 + 0.005 * i, f"K{i:02d}")
        with closing(db_mod._connect(cls.db)) as conn, conn:
            cursor = conn.execute(
                "INSERT INTO coding_dimensions(name,dtype,options_json,multi_select,unit,"
                "description,section,position,created_at) VALUES('mm9fix_dose','numeric','[]',"
                "0,'','','',0,?)", (_NOW,))
            cls.dimension_id = cursor.lastrowid
            for i in range(1, 13):
                conn.execute("INSERT INTO coding_values(dimension_id,zotero_key,value,updated_at) "
                             "VALUES(?,?,?,?)", (cls.dimension_id, f"K{i:02d}", str(i), _NOW))


class Mm9AdvancedLabelTests(Mm9LabelFixture):
    """MM9-14 主钉：advanced 两条路径只声明纯分析尺度。"""

    def test_mm9_14_log_rom_funnel_points_are_log_and_label_says_log_only(self):
        """LOG_ROM 漏斗点值是对数，标签必须只说 "log"。

        修前：标签 "log; results shown as ratio of means"，但 points[].effect
        逐值等于入库的 log 估计，从未 exp——后缀是不实陈述。"""
        funnel = small_study_effects(self.db, "CR", "OF", "TR", "LOG_ROM")
        # 回退修复 → "log; results shown as ratio of means" → 红
        self.assertEqual(funnel["effect_scale"], "log")
        self.assertNotIn("shown as", funnel["effect_scale"])
        by_study = {point["study_id"]: point for point in funnel["points"]}
        self.assertEqual(len(by_study), 12)
        for i in range(1, 13):
            raw_log = -0.30 + 0.01 * i
            effect = by_study[f"S{i:02d}"]["effect"]
            self.assertAlmostEqual(effect, raw_log, msg=f"S{i:02d} effect 应为原 log 值")
            self.assertNotAlmostEqual(effect, math.exp(raw_log),
                                      msg=f"S{i:02d} effect 从未指数化")

    def test_mm9_14_fisher_z_paths_report_z_scale_without_shown_as_r(self):
        """FISHER_Z 漏斗点值与 meta 回归都在 z 尺度，标签不得再说 "shown as r"。

        修前：两条路径标签 "Fisher z; results shown as r"，但 points[].effect
        逐值等于入库 z（≠tanh(z)），slope 也是每单位 z——后缀是不实陈述。"""
        funnel = small_study_effects(self.db, "CF", "OF", "TF", "FISHER_Z")
        regression = meta_regression(self.db, "CF", "OF", "TF", "FISHER_Z", self.dimension_id)
        # 回退修复 → "Fisher z; results shown as r" → 红
        self.assertEqual(funnel["effect_scale"], "fisher_z")
        self.assertEqual(regression["effect_scale"], "fisher_z")
        self.assertNotIn("shown as", funnel["effect_scale"])
        self.assertNotIn("shown as", regression["effect_scale"])
        by_study = {point["study_id"]: point for point in funnel["points"]}
        for i in range(1, 13):
            raw_z = 0.10 + 0.01 * i
            effect = by_study[f"S{i:02d}"]["effect"]
            self.assertAlmostEqual(effect, raw_z, msg=f"S{i:02d} effect 应为原 z 值")
            self.assertNotAlmostEqual(effect, math.tanh(raw_z),
                                      msg=f"S{i:02d} effect 从未变换成 r")


class Mm9SynthesisLabelRegressionTests(Mm9LabelFixture):
    """MM9-14 防回归钉：合成路径标签字面保持修复前原样，后缀在此为真。"""

    def test_mm9_14_synthesis_labels_unchanged_and_suffix_true_here(self):
        """合成标签逐字面不变；且展示值确实等于显示变换(分析值)。

        后缀被挪到共享标签（修复回退方向一）→ 字面不等红；后缀从合成
        拼接里丢失（过度修正方向二）→ 字面不等同样红。"""
        fisher = synthesize(self.db, "CF", "OF", "TF", "FISHER_Z")
        rom = synthesize(self.db, "CR", "OF", "TR", "LOG_ROM")
        self.assertEqual(fisher["effect_scale"], "Fisher z; results shown as r")
        self.assertEqual(rom["effect_scale"], "log; results shown as ratio of means")
        # 后缀陈述只在合成路径为真：pooled 展示值 == tanh/exp(分析值)。
        self.assertAlmostEqual(fisher["pooled"], math.tanh(fisher["pooled_analysis"]))
        self.assertAlmostEqual(rom["pooled"], math.exp(rom["pooled_analysis"]))

    def test_mm9_14_synthesis_label_table_for_every_measure(self):
        """全词表钉：每个 MEASURES 代码的合成标签等于修复前字面。"""
        from coscreen.review_analysis import _synthesis_effect_scale_label

        for measure in sorted(MEASURES):
            self.assertEqual(_synthesis_effect_scale_label(measure),
                             _PRE_FIX_SYNTHESIS_LABELS[measure], measure)


class Mm9RegistryAlignmentTests(unittest.TestCase):
    """共享标签对照 measure_registry 词表（'log'|'natural'|'fisher_z'|'logit'）。"""

    def test_mm9_14_shared_label_is_registry_scale_for_every_measure(self):
        """effect_scale_label 逐码等于注册表 scale；手写链漂移（如把
        FISHER_Z 标回 "natural" 或携带展示后缀）即红。"""
        for measure in sorted(MEASURES):
            self.assertIn(measure, MEASURE_REGISTRY, f"{measure} 未注册")
            self.assertEqual(effect_scale_label(measure),
                             MEASURE_REGISTRY[measure]["scale"], measure)

    def test_mm9_14_shared_label_carries_no_display_suffix(self):
        """纯分析尺度标签不得携带任何合成展示后缀。"""
        for measure in sorted(MEASURES):
            self.assertNotIn("shown as", effect_scale_label(measure), measure)


if __name__ == "__main__":
    unittest.main()
