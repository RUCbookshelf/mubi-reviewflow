"""r9 L4b 证据地图钉子（破坏即红）。

清单：``out/release_plan/inventory_夜扫286确认分组.md`` §3.4。
证据原文：``out/external_reviews/night_verify_batch_01.md`` F4-27、
``night_verify_batch_14.md`` F4-29 / F4-30 / F4-33（F4-32 为 F4-29 的
known-duplicate，本文件按同一根因一并钉住）。

条目 ↔ 测试类：

| 条目 | 载体 | 测试类 |
|---|---|---|
| b01·F4-27 | evidence_mapper.py 稠密化无上界 | ``F427DenseBudgetNails`` |
| b14·F4-29（含 F4-32） | evidence_mapper.py 哨兵标签坍缩 | ``F429SentinelLabelNails`` |
| b14·F4-30 | 两条失败路径零覆盖 | ``F430FailurePathCoverageNails`` |
| b14·F4-33 | _YEAR_RE 的 \\b 在 CJK 处不成立 | ``F433YearRegexNails`` |

R1 的验证方式：把基线（bfd07ae）的 ``coscreen/evidence_mapper.py`` 取到
临时目录、同解释器加载为独立模块，与当前模块**逐字段对比**同一语料的
输出（不含修复触发条件时输出必须逐位相同）。基线文件由 ``git show``
在测试里现取，因此工作树无需带着副本。
"""

from __future__ import annotations

import builtins
import importlib.util
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from coscreen import evidence_mapper as EM  # noqa: E402

#: 基线提交（本泳道开工时的 C 波合入终态）。
BASELINE = "bfd07ae"


def _load_baseline_module():
    """把基线版 evidence_mapper.py 加载成独立模块（不可用时返回 None）。"""
    try:
        src = subprocess.run(
            ["git", "show", f"{BASELINE}:coscreen/evidence_mapper.py"],
            cwd=str(REPO), capture_output=True, text=True, timeout=60,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    if not src.strip():
        return None
    tmp = Path(tempfile.mkdtemp(prefix="r9l4b_emap_"))
    path = tmp / "evidence_mapper_baseline.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("r9l4b_emap_baseline", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASELINE_MODULE = _load_baseline_module()


def _corpus(n_records: int = 60, seed: int = 5) -> list[dict]:
    """确定性合成语料：无 ``other``/``unknown`` 字面期刊、年份均可识别。"""
    rnd = random.Random(seed)
    words = [f"w{i}" for i in range(200)]
    records = []
    for i in range(n_records):
        records.append({
            "zotero_key": f"k{i}",
            "title": " ".join(rnd.choice(words) for _ in range(10)),
            "abstract": " ".join(rnd.choice(words) for _ in range(16)),
            "journal": rnd.choice([f"J{i % 6}", f"Q{i % 4}", "", "Lancet"]),
            "year": rnd.choice([2019, "2020-05", None, 2021]),
        })
    return records


# ======================================================================
# b01·F4-27 —— 稠密化无 max_features 上界
# ======================================================================
class F427DenseBudgetNails(unittest.TestCase):
    """修前实测（当前树复现，``p_emap.py`` 同型语料）：``matrix`` 为
    ``np.zeros((n, V))``，无任何列数上限——n=400/V=16801 即 53.8 MB 稠密
    矩阵、峰值 167 MB；外推 5000 篇 × 百万词表 ≈ 40 GB。

    修复：``MAX_DENSE_FEATURES`` + ``MAX_DENSE_BYTES`` 双预算，超限按
    「总权重降序、同权词典序」保留前 K 列，并在响应里加
    ``feature_limit`` 如实登记。未超限时**不加该键**、输出逐位不变。
    """

    def test_budget_constants_exist_and_are_bounded(self):
        self.assertIsInstance(EM.MAX_DENSE_FEATURES, int)
        self.assertGreater(EM.MAX_DENSE_FEATURES, 0)
        self.assertLessEqual(EM.MAX_DENSE_BYTES, 512 * 1024 * 1024,
                             "字节预算应把单次稠密分配限制在 512 MiB 量级以内")

    def test_column_limit_shrinks_with_row_count(self):
        """行数越多，允许的列数越少——保证 n×cols×8 有上界。"""
        small = EM.dense_column_limit(100)
        large = EM.dense_column_limit(5000)
        self.assertLess(large, small)
        self.assertLessEqual(large * 5000 * 8, EM.MAX_DENSE_BYTES)
        # 旧实现下 5000 × 1e6 × 8 = 40 GB；预算把单次分配压到 ≤ MAX_DENSE_BYTES。
        self.assertLessEqual(EM.MAX_DENSE_BYTES, 512 * 1024 * 1024)
        self.assertLess(large, 10 ** 6, "百万词表仍按全量稠密化")
        self.assertGreaterEqual(EM.dense_column_limit(0), 1, "零行也要给正数上限")

    def test_degradation_is_reported_in_response(self):
        """触发上限时响应必须带 feature_limit（不静默降级）。"""
        records = [
            {"zotero_key": f"b{i}", "title": f"uniqueterm{i} alpha beta",
             "abstract": "x", "journal": "J", "year": 2020}
            for i in range(80)
        ]
        saved = EM.MAX_DENSE_BYTES
        EM.MAX_DENSE_BYTES = 80 * 8 * 40  # 强制只保留 40 列
        try:
            result = EM.generate_evidence_map(records, "journal", "year", 2)
        finally:
            EM.MAX_DENSE_BYTES = saved
        self.assertIn("feature_limit", result, "列数被截断却未在响应中登记")
        limit = result["feature_limit"]
        self.assertEqual(limit["kept"], 40)
        self.assertGreater(limit["total"], limit["kept"])
        # 截断后仍产出完整结构
        self.assertTrue(result["matrix"])
        self.assertEqual(len(result["x_labels"]), len(result["matrix"]))

    def test_no_feature_limit_key_below_budget(self):
        """常规对照（R1）：未触发预算时响应形状与基线逐位相同。"""
        records = _corpus(40)
        result = EM.generate_evidence_map(records, "journal", "year", 3)
        self.assertNotIn("feature_limit", result,
                         "未降级时不应新增键（会让下游/快照比较误判）")

    def test_degradation_is_deterministic(self):
        """同一语料两次调用（含降级路径）结果必须完全相同。"""
        records = [
            {"zotero_key": f"b{i}", "title": f"uniqueterm{i} alpha beta",
             "abstract": "y", "journal": "J", "year": 2020}
            for i in range(60)
        ]
        saved = EM.MAX_DENSE_BYTES
        EM.MAX_DENSE_BYTES = 60 * 8 * 30
        try:
            first = EM.generate_evidence_map(records, "journal", "year", 2)
            second = EM.generate_evidence_map(records, "journal", "year", 2)
        finally:
            EM.MAX_DENSE_BYTES = saved
        self.assertEqual(first, second)


# ======================================================================
# b14·F4-29（F4-32 同根因）—— 哨兵标签与真实值坍缩
# ======================================================================
class F429SentinelLabelNails(unittest.TestCase):
    """修前实测（当前树复现，58 篇：30 篇 journal=``other``、5 篇
    ``unknown``、3 篇缺失、20 个 ``Journal NN``）：

    .. code-block:: text

        x_labels: [..., 'other'] 出现两次；row totals: [0, 8, ...1, 32]
        label 'other' idx 0 row total 0      ← dict 覆盖留下的 0 计数假缺口
        label 'unknown' idx 1 row total 8    ← 真实 5 + 缺失 3 合并
        gaps count: 1                        ← 虚假的"研究缺口"

    修复：真实值与哨兵同名的加 ``" (literal)"`` 后缀；标签互异、桶不混合。
    """

    @staticmethod
    def _dataset():
        articles = []
        for i in range(30):
            articles.append({"zotero_key": f"o{i}", "journal": "other",
                             "title": "study alpha", "year": 2020})
        for i in range(5):
            articles.append({"zotero_key": f"u{i}", "journal": "unknown",
                             "title": "study beta", "year": 2020})
        for i in range(3):
            articles.append({"zotero_key": f"m{i}", "journal": "",
                             "title": "study gamma", "year": 2020})
        for i in range(20):
            articles.append({"zotero_key": f"j{i}", "journal": f"Journal {i:02d}",
                             "title": "study delta", "year": 2020})
        return articles

    def test_labels_have_no_duplicates(self):
        result = EM.generate_evidence_map(self._dataset(), "journal", "study_design", 2)
        labels = result["x_labels"]
        self.assertEqual(len(labels), len(set(labels)),
                         f"x_labels 仍有重复项：{[l for l in labels if labels.count(l) > 1]}")

    def test_real_other_not_merged_into_tail_bucket(self):
        result = EM.generate_evidence_map(self._dataset(), "journal", "study_design", 2)
        labels = result["x_labels"]
        literal_row = labels.index(EM.OTHER_LABEL + EM.LITERAL_SUFFIX)
        merged_row = labels.index(EM.OTHER_LABEL)
        self.assertEqual(sum(result["matrix"][literal_row]), 30,
                         "真实期刊名 other 的 30 篇未独立成行")
        # 23 个互异标签（other(literal)、unknown(literal)、unknown、Journal 00..19）
        # 保留前 20，长尾 3 篇并入 other 桶——不再混入真实 "other" 的 30 篇。
        self.assertEqual(sum(result["matrix"][merged_row]), 3,
                         "长尾归并桶应只含被截断的 3 个期刊")

    def test_real_unknown_not_merged_into_missing_bucket(self):
        result = EM.generate_evidence_map(self._dataset(), "journal", "study_design", 2)
        labels = result["x_labels"]
        literal_row = labels.index(EM.UNKNOWN_LABEL + EM.LITERAL_SUFFIX)
        missing_row = labels.index(EM.UNKNOWN_LABEL)
        self.assertEqual(sum(result["matrix"][literal_row]), 5,
                         "真实期刊名 unknown 的 5 篇未独立成行")
        self.assertEqual(sum(result["matrix"][missing_row]), 3,
                         "缺失桶应只含 3 篇空期刊")

    def test_no_spurious_gap_row(self):
        """修前 0 计数的假 'other' 行会产出虚假研究缺口。"""
        result = EM.generate_evidence_map(self._dataset(), "journal", "study_design", 2)
        for row, label in enumerate(result["x_labels"]):
            self.assertGreater(sum(result["matrix"][row]), 0,
                               f"行 {label!r} 全 0（虚假缺口行）")
        self.assertEqual(result["gaps"], [], "同一数据集出现与矩阵自相矛盾的缺口")

    def test_cell_members_not_mixed(self):
        """成员表与矩阵同口径：literal 行不该混入缺失桶的键。"""
        result = EM.generate_evidence_map(self._dataset(), "journal", "study_design", 2)
        labels = result["x_labels"]
        literal_row = labels.index(EM.OTHER_LABEL + EM.LITERAL_SUFFIX)
        members = [k for cell in result["cell_members"][literal_row] for k in cell]
        self.assertEqual(len(members), 30)
        self.assertTrue(all(k.startswith("o") for k in members), members[:3])

    def test_regular_corpus_bit_identical(self):
        """常规对照（R1）：无同名真实值时输出与基线逐位相同。"""
        if BASELINE_MODULE is None:  # pragma: no cover
            self.skipTest("基线模块不可用（git 不可读）")
        records = _corpus(70)
        for x, y in (("journal", "study_design"), ("year", "population"),
                     ("journal", "year"), ("cluster", "journal")):
            with self.subTest(x=x, y=y):
                new = EM.generate_evidence_map(records, x, y, 3)
                old = BASELINE_MODULE.generate_evidence_map(records, x, y, 3)
                for key in ("matrix", "cell_members", "x_labels", "y_labels",
                            "clusters", "gaps"):
                    self.assertEqual(new[key], old[key], f"{key} 变了")
                self.assertEqual(set(new), set(old), "响应键集变了")


# ======================================================================
# b14·F4-30 —— 两条失败路径零测试覆盖
# ======================================================================
class F430FailurePathCoverageNails(unittest.TestCase):
    """修前实测：``grep generate_evidence_map build/tests/`` 只有前端
    桩（``test_r8_fl5_frontend.py:157`` 是 JS 假响应），
    ``grep evidence_mapper build/tests/`` 只命中 JS 文件名——后端模块的
    两条失败路径（MemoryError 型资源耗尽 / 懒导入 ImportError）零覆盖。
    本类即为该防线缺口补钉子。
    """

    def test_import_error_becomes_value_error(self):
        """缺 numpy/sklearn 时端点应拿到可映射 400 的 ValueError，不是裸 ImportError。"""
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "numpy" or name.startswith("sklearn"):
                raise ImportError(f"simulated missing {name}")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = fake_import
        try:
            with self.assertRaises(ValueError) as ctx:
                EM.generate_evidence_map(
                    [{"zotero_key": "k1", "title": "randomised controlled trial alpha"}],
                    "year", "journal", 1,
                )
        finally:
            builtins.__import__ = real_import
        message = str(ctx.exception)
        self.assertIn("numpy", message)
        self.assertIn("scikit-learn", message)

    def test_empty_corpus_returns_empty_structure_without_import(self):
        """空语料在懒导入之前返回（不依赖 numpy/sklearn）。"""
        self.assertEqual(
            EM.generate_evidence_map([], "year", "journal", 3),
            {"matrix": [], "cell_members": [], "x_labels": [], "y_labels": [],
             "clusters": [], "gaps": []},
        )

    def test_all_empty_texts_fall_back_to_single_cluster(self):
        """全空文本 → 词表为空 → 全体入簇 0（也是懒导入前的分支）。"""
        result = EM.generate_evidence_map(
            [{"zotero_key": "k1", "title": "", "abstract": ""},
             {"zotero_key": "k2", "title": None, "abstract": None}],
            "cluster", "year", 4,
        )
        self.assertEqual(len(result["clusters"]), 1)
        self.assertEqual(result["clusters"][0]["id"], 0)
        self.assertEqual(result["clusters"][0]["top_terms"], [])

    def test_budget_path_does_not_raise(self):
        """预算降级是优雅降级：不抛 MemoryError、不抛 ValueError。"""
        records = [
            {"zotero_key": f"b{i}", "title": f"t{i} alpha beta gamma",
             "abstract": "z", "journal": "J", "year": 2020}
            for i in range(70)
        ]
        saved = EM.MAX_DENSE_BYTES
        EM.MAX_DENSE_BYTES = 70 * 8 * 20
        try:
            result = EM.generate_evidence_map(records, "journal", "year", 2)
        finally:
            EM.MAX_DENSE_BYTES = saved
        self.assertIn("feature_limit", result)
        self.assertEqual(sum(sum(row) for row in result["matrix"]), 70,
                         "降级后仍须把全部文献计入矩阵")


# ======================================================================
# b14·F4-33 —— _YEAR_RE 的 \b 在 CJK 相邻处不成立
# ======================================================================
class F433YearRegexNails(unittest.TestCase):
    """修前实测（当前树复现）：``'2020年' -> None``、``'约2020' -> None``、
    ``'x2020y' -> None``；只有纯 ASCII 分隔的年份可用。修复：改用
    ASCII 环视，CJK 相邻照常识别，字母包裹的编号仍不误判。"""

    def test_cjk_adjacent_year_recognised(self):
        for text in ("2020年", "约2020", "2020年度", "发表于2019"):
            with self.subTest(text=text):
                self.assertEqual(EM._year_label(text), text[text.index("20" if "20" in text else "19"):][:4])

    def test_label_formats_still_work(self):
        for text, expected in (("2020", "2020"), ("2020-05", "2020"),
                               ("2019/12", "2019"), (2020, "2020"), ("1899", "1899")):
            with self.subTest(text=text):
                self.assertEqual(EM._year_label(text), expected)

    def test_alphanumeric_wrapped_number_not_year(self):
        """R2 边界：字母/数字包裹的编号仍不当成年份（原意保留）。"""
        for text in ("x2020y", "AB2020", "2020123", "120201"):
            with self.subTest(text=text):
                self.assertEqual(EM._year_label(text), EM.UNKNOWN_LABEL)

    def test_missing_and_out_of_range_unchanged(self):
        for value in (None, "", "n/a", 1700, "1799"):
            with self.subTest(value=value):
                self.assertEqual(EM._year_label(value), EM.UNKNOWN_LABEL)

    def test_year_dimension_labels_include_cjk_value(self):
        """端到端：``"2020年"`` 的文献落到 2020 行而不是 unknown 行。"""
        articles = [
            {"zotero_key": "a", "year": "2020年", "journal": "J", "title": "t"},
            {"zotero_key": "b", "year": None, "journal": "J", "title": "t"},
        ]
        result = EM.generate_evidence_map(articles, "year", "journal", 1)
        self.assertIn("2020", result["x_labels"])
        self.assertIn(EM.UNKNOWN_LABEL, result["x_labels"])


if __name__ == "__main__":
    unittest.main()
