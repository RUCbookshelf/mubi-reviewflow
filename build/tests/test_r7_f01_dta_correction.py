"""r7 F-01 钉子：DTA 似然比 0.5 连续性校正只作用于含零格的四格表。

缺陷（deepseek 实证，out/external_reviews/luna2_verify_num.md F-01）：
``coscreen/dta_likelihood_ratios.py`` 的 ``pool_likelihood_ratios`` 在
``correction=0.5`` 时对全部保留行统一加 0.5（含零格的表和完整表一起被改写），
而模块契约（docstring / ``_FORMULAS_POOLED["zero_cells"]`` /
``zero_cell_policy`` 文案）与 ``metafor::escalc(add=0.5, to="only0")``
语义均为“只对含零格的表加 0.5”。

修复后必须成立的钉子（把修复撤掉、恢复全池加 0.5 时，①②必须变红；
把修复做过头、零格表也不校正时，①③必须变红；correction="none" 路径
任何情况下逐位不变，由④钉死）：

①混合池（零格表+完整表）、correction=0.5：完整行的 lnLR±/varLR± 与
  correction="none" 基线逐位一致；零格行仍被校正（与外部复审记录的
  契约常量逐位一致）。
②纯完整池、correction=0.5：除设置标签 ``zero_cell_policy`` 外的全部输出
  与 correction="none" 逐位一致，且没有校正/排除类误报警告。
③纯零格池、correction=0.5：每行四格仍各加 0.5（回归保护，与修复前
  行为逐位一致）。
④correction="none"：完整表按原公式逐位计算，零格表被显式排除，
  纯零格池直接报错——该路径不受本修复影响。
"""
import math
import sys
import unittest
from pathlib import Path
from statistics import NormalDist

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen.dta_likelihood_ratios import pool_likelihood_ratios  # noqa: E402

# 数据形态取自外部复审探针（luna2_verify_num.md F-01 §A/§B）。
ZERO_TABLE = {"study_id": "zero_cell", "tp": 10, "fp": 0, "fn": 2, "tn": 20}
COMPLETE_TABLE = {"study_id": "complete", "tp": 10, "fp": 5, "fn": 2, "tn": 20}
COMPLETE_TABLE_2 = {"study_id": "complete2", "tp": 8, "fp": 4, "fn": 3, "tn": 25}
ZERO_TABLE_2 = {"study_id": "zero_cell2", "tp": 0, "fp": 4, "fn": 3, "tn": 25}

# 外部复审记录的契约常量（F-01 §A/§B 原样输出，作为独立于本仓库实现的锚点）。
ZERO_ROW_CORRECTED = (3.5240955179853093, 1.9706959706959706,
                      -1.6245610740083207, 0.3242383632627535)
COMPLETE_ROW_UNCORRECTED = (1.4271163556401458, 0.17666666666666667,
                            -1.5686159179138452, 0.4266666666666667)
COMPLETE2_ROW_UNCORRECTED = (1.6625477377480486, 0.2496081504702194,
                             -1.1508629790119875, 0.24794148380355274)
PURE_COMPLETE_POOLED_NONE = (4.5937159559482765, 0.27133490215853356)

_FIELDS = ("ln_lr_positive", "var_ln_lr_positive", "ln_lr_negative", "var_ln_lr_negative")


def _contract_row(table, half):
    """按公开契约独立计算单表 log LR（to="only0"：仅含零格的表在 half 时 +0.5）。

    运算次序镜像 ``_study_log_ratios``，以便逐位（bit-exact）对拍。
    """
    correct = bool(half) and 0 in (table["tp"], table["fp"], table["fn"], table["tn"])
    tp, fp, fn, tn = (float(table[key]) for key in ("tp", "fp", "fn", "tn"))
    if correct:
        tp, fp, fn, tn = tp + 0.5, fp + 0.5, fn + 0.5, tn + 0.5
    diseased, non_diseased = tp + fn, tn + fp
    ln_plus = math.log(tp) - math.log(diseased) - math.log(fp) + math.log(non_diseased)
    var_plus = 1.0 / tp - 1.0 / diseased + 1.0 / fp - 1.0 / non_diseased
    ln_minus = math.log(fn) - math.log(diseased) - math.log(tn) + math.log(non_diseased)
    var_minus = 1.0 / fn - 1.0 / diseased + 1.0 / tn - 1.0 / non_diseased
    return {"ln_lr_positive": ln_plus, "var_ln_lr_positive": var_plus,
            "ln_lr_negative": ln_minus, "var_ln_lr_negative": var_minus}


def _contract_arm(pairs, confidence=0.95):
    """按公开契约（_FORMULAS_POOLED["pooling"]）镜像固定效应逆方差合并。"""
    weights = [1.0 / variance for _, variance in pairs]
    total = sum(weights)
    estimate = sum(weight * value for weight, (value, _) in zip(weights, pairs)) / total
    se = math.sqrt(1.0 / total)
    z = NormalDist().inv_cdf(1.0 - (1.0 - confidence) / 2.0)
    return {"ln_lr": estimate, "se_ln_lr": se,
            "ci_lr": [math.exp(estimate - z * se), math.exp(estimate + z * se)]}


def _per_study_by_id(result):
    return {row["study_id"]: row for row in result["per_study"]}


class DtaF01HalfCorrectionNails(unittest.TestCase):
    maxDiff = None

    def test_dta_f01_nail1_mixed_pool_half_correction_hits_only_zero_cell_rows(self):
        """①混合池 + correction=0.5：完整行逐位等于 none 基线，零格行被校正。"""
        mixed = [ZERO_TABLE, COMPLETE_TABLE]
        half = pool_likelihood_ratios(mixed, correction=0.5)
        # none 基线池：同一零格表 + 两张完整表（none 下零格表被排除后仍需 ≥2 表）。
        none = pool_likelihood_ratios([ZERO_TABLE, COMPLETE_TABLE, COMPLETE_TABLE_2],
                                      correction="none")

        half_rows, none_rows = _per_study_by_id(half), _per_study_by_id(none)
        self.assertEqual(set(half_rows), {"zero_cell", "complete"})
        self.assertEqual(set(none_rows), {"complete", "complete2"})  # 零格表被排除

        # 完整行：与 correction="none" 基线逐位一致（撤掉逐行判定即红）。
        for field in _FIELDS:
            self.assertEqual(half_rows["complete"][field],
                             none_rows["complete"][field],
                             f"complete row field {field} changed by correction=0.5")
            self.assertEqual(half_rows["complete"][field],
                             _contract_row(COMPLETE_TABLE, half=True)[field])
        # 完整行与外部复审契约常量逐位一致。
        self.assertEqual(tuple(half_rows["complete"][field] for field in _FIELDS),
                         COMPLETE_ROW_UNCORRECTED)

        # 零格行：仍被 +0.5 校正（做过头“谁都不校正”即红），且与复审常量逐位一致。
        self.assertEqual(tuple(half_rows["zero_cell"][field] for field in _FIELDS),
                         ZERO_ROW_CORRECTED)
        for field, value in zip(_FIELDS, ZERO_ROW_CORRECTED):
            self.assertEqual(half_rows["zero_cell"][field],
                             _contract_row(ZERO_TABLE, half=True)[field])
            self.assertEqual(half_rows["zero_cell"][field], value)

        # 混合池合并值 = 契约镜像（零格行 +0.5、完整行原值）。
        for key, fields in (("positive", ("ln_lr_positive", "var_ln_lr_positive")),
                            ("negative", ("ln_lr_negative", "var_ln_lr_negative"))):
            pairs = [(_contract_row(t, half=True)[fields[0]],
                      _contract_row(t, half=True)[fields[1]]) for t in mixed]
            expected = _contract_arm(pairs)
            for out_field in ("ln_lr", "se_ln_lr"):
                self.assertEqual(half[key][out_field], expected[out_field],
                                 f"pooled {key}.{out_field} deviates from only0 contract")
            self.assertEqual(half[key]["ci_lr"], expected["ci_lr"])

        # 文案与实际行为一致：恰好校正 1 张含零格的表，完整表未被提及。
        self.assertEqual(half["zero_cell_policy"],
                         "0.5 added to all four cells of tables with a zero only")
        self.assertEqual(half["excluded_studies"], [])
        correction_warnings = [w for w in half["warnings"]
                               if "0.5 correction was added" in w]
        self.assertEqual(len(correction_warnings), 1)
        self.assertIn("all four cells of 1 table(s) containing a zero",
                      correction_warnings[0])
        self.assertEqual(half["n_used"], 2)

    def test_dta_f01_nail2_pure_complete_pool_half_option_changes_nothing(self):
        """②纯完整池 + correction=0.5：全部输出与 none 一致（除设置标签），无误报。"""
        complete_pool = [COMPLETE_TABLE, COMPLETE_TABLE_2]
        half = pool_likelihood_ratios(complete_pool, correction=0.5)
        none = pool_likelihood_ratios(complete_pool, correction="none")

        half_view, none_view = dict(half), dict(none)
        # zero_cell_policy 是用户所选设置的标签，两设置本就不同；其余必须逐位一致。
        self.assertEqual(half_view.pop("zero_cell_policy"),
                         "0.5 added to all four cells of tables with a zero only")
        self.assertEqual(none_view.pop("zero_cell_policy"),
                         "none: tables with a zero cell excluded")
        self.assertEqual(half_view, none_view)

        # 逐研究值与复审契约常量逐位一致（none 基线，F-01 §B）。
        rows = _per_study_by_id(none)
        self.assertEqual(tuple(rows["complete"][f] for f in _FIELDS),
                         COMPLETE_ROW_UNCORRECTED)
        self.assertEqual(tuple(rows["complete2"][f] for f in _FIELDS),
                         COMPLETE2_ROW_UNCORRECTED)
        self.assertEqual((none["lr_positive"], none["lr_negative"]),
                         PURE_COMPLETE_POOLED_NONE)

        # 无校正/排除类误报警告（撤掉逐行判定后数值即红，文案红线在此）。
        for keyword in ("0.5 correction was added", "zero cell were excluded"):
            self.assertFalse(any(keyword in w for w in half["warnings"]),
                             f"false-alarm warning about {keyword!r} on a zero-free pool")
        self.assertEqual(half["n_used"], 2)
        self.assertEqual(half["excluded_studies"], [])

    def test_dta_f01_nail3_pure_zero_cell_pool_half_correction_unchanged(self):
        """③纯零格池 + correction=0.5：每行仍 +0.5（与修复前行为逐位一致的回归保护）。"""
        zero_pool = [ZERO_TABLE, ZERO_TABLE_2]
        half = pool_likelihood_ratios(zero_pool, correction=0.5)

        rows = _per_study_by_id(half)
        self.assertEqual(set(rows), {"zero_cell", "zero_cell2"})
        for table in zero_pool:
            expected = _contract_row(table, half=True)
            for field in _FIELDS:
                self.assertEqual(rows[table["study_id"]][field], expected[field],
                                 f"{table['study_id']}.{field} must keep the historical "
                                 "all-cells +0.5 correction for zero-cell tables")
        # 与复审常量逐位一致（零格表路径修复前后逐位不变）。
        self.assertEqual(tuple(rows["zero_cell"][f] for f in _FIELDS), ZERO_ROW_CORRECTED)

        # 合并值 = 全部 +0.5 后的契约镜像。
        for key, fields in (("positive", ("ln_lr_positive", "var_ln_lr_positive")),
                            ("negative", ("ln_lr_negative", "var_ln_lr_negative"))):
            pairs = [(_contract_row(t, half=True)[fields[0]],
                      _contract_row(t, half=True)[fields[1]]) for t in zero_pool]
            expected = _contract_arm(pairs)
            for out_field in ("ln_lr", "se_ln_lr"):
                self.assertEqual(half[key][out_field], expected[out_field])
            self.assertEqual(half[key]["ci_lr"], expected["ci_lr"])

        self.assertEqual(half["zero_cell_policy"],
                         "0.5 added to all four cells of tables with a zero only")
        self.assertIn("all four cells of 2 table(s) containing a zero",
                      " ".join(half["warnings"]))
        self.assertEqual(half["n_used"], 2)
        self.assertEqual(half["excluded_studies"], [])

        # 随机效应设置下逐研究值同样逐位不变（下游不应受本修复影响）。
        half_dl = pool_likelihood_ratios(zero_pool, correction=0.5, random_effects=True)
        dl_rows = _per_study_by_id(half_dl)
        for table in zero_pool:
            expected = _contract_row(table, half=True)
            for field in _FIELDS:
                self.assertEqual(dl_rows[table["study_id"]][field], expected[field])

    def test_dta_f01_nail4_correction_none_bitwise_contract(self):
        """④correction="none"：完整表按原公式逐位计算，零格表显式排除，路径不变。"""
        # 纯完整池：逐研究值与合并值全部等于契约镜像与复审常量。
        none = pool_likelihood_ratios([COMPLETE_TABLE, COMPLETE_TABLE_2],
                                      correction="none")
        rows = _per_study_by_id(none)
        for table in (COMPLETE_TABLE, COMPLETE_TABLE_2):
            expected = _contract_row(table, half=False)
            for field in _FIELDS:
                self.assertEqual(rows[table["study_id"]][field], expected[field])
        self.assertEqual(tuple(rows["complete"][f] for f in _FIELDS),
                         COMPLETE_ROW_UNCORRECTED)
        self.assertEqual(tuple(rows["complete2"][f] for f in _FIELDS),
                         COMPLETE2_ROW_UNCORRECTED)
        self.assertEqual((none["lr_positive"], none["lr_negative"]),
                         PURE_COMPLETE_POOLED_NONE)
        self.assertEqual(none["zero_cell_policy"], "none: tables with a zero cell excluded")
        self.assertEqual(none["excluded_studies"], [])

        # 混合池：零格表被显式排除并给出原因，完整表仍逐位等于契约。
        # （[ZERO_TABLE, COMPLETE_TABLE] 两表池在 none 下只剩 1 表，按契约报错。）
        with self.assertRaises(ValueError):
            pool_likelihood_ratios([ZERO_TABLE, COMPLETE_TABLE], correction="none")
        mixed_none = pool_likelihood_ratios([ZERO_TABLE, COMPLETE_TABLE, COMPLETE_TABLE_2],
                                            correction="none")
        mixed_rows = _per_study_by_id(mixed_none)
        self.assertEqual(set(mixed_rows), {"complete", "complete2"})
        for table in (COMPLETE_TABLE, COMPLETE_TABLE_2):
            expected = _contract_row(table, half=False)
            for field in _FIELDS:
                self.assertEqual(mixed_rows[table["study_id"]][field], expected[field])
        self.assertEqual([e["study_id"] for e in mixed_none["excluded_studies"]],
                         ["zero_cell"])
        self.assertEqual(mixed_none["excluded_studies"][0]["reason"],
                         "a cell is zero, so at least one of ln LR+/ln LR- is 0 or "
                         "infinite (undefined on the log scale)")
        self.assertEqual(mixed_none["n_used"], 2)
        self.assertIn("1 table(s) with a zero cell were excluded", mixed_none["warnings"][2])
        self.assertFalse(any("0.5 correction was added" in w
                             for w in mixed_none["warnings"]))

        # 混合池 none 与纯完整池 none 在相同完整表上逐位一致。
        for study_id in ("complete", "complete2"):
            for field in _FIELDS:
                self.assertEqual(mixed_rows[study_id][field], rows[study_id][field])

        # 纯零格池：排除后不足两表，按既有契约直接报错。
        with self.assertRaises(ValueError):
            pool_likelihood_ratios([ZERO_TABLE, ZERO_TABLE_2], correction="none")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
