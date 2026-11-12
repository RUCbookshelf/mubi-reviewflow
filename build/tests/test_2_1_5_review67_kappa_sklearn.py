# -*- coding: utf-8 -*-
"""review67 L-25 · kappa 的 sklearn 基准常驻测试（评审 2026-11-12）。

评审报告（out/code_review_2026-11-12/REPORT.md L-25，unconfirmed）：
coscreen/stats/kappa.py:5 docstring 声称「sklearn 仅在测试中作基准，见
tests/test_kappa.py」，但该文件在全 git 历史中从未存在，套件内无可复验的
基准——声明悬空。本文件把该基准落成常驻测试（docstring 引用的 tests/
test_kappa.py 即本基准的落点；kappa.py 为产品文件，本次未改动）。

对拍口径（与产品保留规则严格一致，见 kappa.py cohen_kappa docstring）：
  - 输入按位置 zip，仅保留双方均非空（""/None 视为空）且均在 labels 内的
    配对，以保留样本为分母；
  - 固定种子随机用例 3580 个（评审泳道 probe_kappa_sklearn.py 同规模）逐例
    与 sklearn.metrics.cohen_kappa_score(a, b, labels=labels) 比对，
    max|产品κ−sklearnκ| ≤ 1e-15。

两类约定差异不参与对拍、由 test_documented_convention_differences 单独钉
（均为 kappa.py docstring 已声明的口径，产品内不可达/有意为之）：
  - pe==1（单一类别共线）：产品取 1.0/0.0，sklearn>=1.9 默认 nan；
  - n==0 / 不等长输入：产品返回 0.0 / 按 zip 截断，sklearn 抛异常。

sklearn 缺席时整类 skip：产品运行时禁止依赖 sklearn（kappa.py 模块头
SPEC §7），基准仅在本测试内引入；skip 只说明本机无法对拍，不代表基准通过。
"""
from pathlib import Path
import math
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen.stats.kappa import cohen_kappa  # noqa: E402

try:
    from sklearn.metrics import cohen_kappa_score as sk_kappa
    HAVE_SKLEARN = True
except ImportError:  # pragma: no cover - 依赖缺席分支
    HAVE_SKLEARN = False

#: 与评审泳道同规模的可对拍用例数（probe_kappa_sklearn.py：4000 生成例中
#: 3580 例进入对拍，其余为 n==0 / pe==1 约定差异例）。
N_CASES = 3580
#: 允许的最大绝对差（评审实测 max|Δ|=4.02e-16 ≈ 2 ulp，留 1e-15 裕量）。
TOLERANCE = 1e-15
LABELS = ('include', 'exclude', 'maybe')


def _random_case(rng):
    """一组等长随机决策列：混入空串/None/表外标签，只补空规则下可被丢弃。"""
    n = rng.randint(1, 60)
    noise = ['', None, 'other']
    pick = lambda: rng.choice(LABELS + tuple(noise)) if rng.random() < 0.85 else ''  # noqa: E731
    return [pick() for _ in range(n)], [pick() for _ in range(n)]


def _comparable_pairs(a, b):
    """复现产品保留规则：双方均非空且均在 labels 内，按位置 zip。"""
    label_set = set(LABELS)
    return [(x, y) for x, y in zip(a, b)
            if x and y and x in label_set and y in label_set]


@unittest.skipUnless(
    HAVE_SKLEARN,
    'sklearn 缺席：无法执行 kappa 基准对拍（产品实现本身禁止依赖 sklearn——'
    'SPEC §7，本基准只作交叉验证；缺席为环境限制，非基准通过）')
class KappaSklearnBenchmarkTests(unittest.TestCase):
    """cohen_kappa 与 sklearn 的数学一致性基准（review67 L-25）。"""

    def test_kappa_matches_sklearn_on_3580_random_cases(self):
        """3580 个随机用例对拍 sklearn：max|Δ| ≤ 1e-15。"""
        rng = random.Random(20261112)
        checked = n_zero = pe_one = 0
        max_diff = 0.0
        worst = None
        generated = 0
        while checked < N_CASES:
            generated += 1
            # 防御性上限：固定种子下 4000+ 例必够 3580（评审实测），留 3 倍余量。
            self.assertLess(generated, 3 * N_CASES, '随机用例生成异常：可对拍例不足')
            a, b = _random_case(rng)
            res = cohen_kappa(a, b, labels=LABELS)
            pairs = _comparable_pairs(a, b)
            self.assertEqual(res.n, len(pairs), '产品保留样本数与口径复现不一致')
            if res.n == 0:
                n_zero += 1  # 产品约定返回 0.0，sklearn 抛异常 → 不对拍
                continue
            if res.pe == 1.0:
                pe_one += 1  # 产品约定 1.0/0.0，sklearn>=1.9 默认 nan → 不对拍
                continue
            xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
            ref = sk_kappa(xs, ys, labels=list(LABELS))
            self.assertFalse(math.isnan(ref), 'pe<1 时 sklearn 出现 nan：对拍口径失效')
            diff = abs(res.kappa - ref)
            if diff > max_diff:
                max_diff, worst = diff, (res.kappa, ref, res.po, res.pe, res.n)
            checked += 1
        self.assertEqual(checked, N_CASES)
        self.assertLessEqual(
            max_diff, TOLERANCE,
            'kappa 与 sklearn 基准偏差超限：max|Δ|=%r（最差例 κ产品=%r κsklearn=%r '
            'po=%r pe=%r n=%d）' % ((max_diff,) + worst if worst else (max_diff, 0, 0, 0, 0, 0)))
        # 评审实测参考（probe_kappa_sklearn.py）：checked=3580、max|Δ|=4.02e-16。
        print('kappa sklearn benchmark: checked=%d max|diff|=%.3e '
              '(skipped n==0: %d, pe==1: %d)' % (checked, max_diff, n_zero, pe_one))

    def test_documented_convention_differences(self):
        """两类约定差异钉：pe==1、n==0、不等长——产品口径与 docstring 声明一致。"""
        import warnings
        # pe==1（双方边缘分布同为同一点质量，此时必有 po==1——精确算术下
        # 「pe==1 而 po<1」不可达，产品 0.0 分支为纯防御）：产品取 1.0；
        # sklearn>=1.9 默认 nan（UndefinedMetric，故基准对拍跳过 pe==1 例）。
        res = cohen_kappa(['include'] * 5, ['include'] * 5, labels=LABELS)
        self.assertEqual((res.kappa, res.pe, res.po, res.n), (1.0, 1.0, 1.0, 5))
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')  # sklearn 对 undefined metric 告警
            self.assertTrue(math.isnan(sk_kappa(['include'] * 5, ['include'] * 5,
                                                labels=list(LABELS))))
        # n==0：产品返回 kappa=0.0、n=0；sklearn 抛异常
        res = cohen_kappa(['include', ''], ['', 'exclude'], labels=LABELS)
        self.assertEqual((res.kappa, res.n), (0.0, 0))
        with self.assertRaises(Exception):
            sk_kappa([], [], labels=list(LABELS))
        # 不等长：产品按位置 zip 截断（docstring 声明）；sklearn 抛 ValueError
        res = cohen_kappa(['include', 'exclude'], ['include'], labels=LABELS)
        self.assertEqual((res.kappa, res.n), (1.0, 1))
        with self.assertRaises(ValueError):
            sk_kappa(['include', 'exclude'], ['include'], labels=list(LABELS))


if __name__ == '__main__':
    unittest.main(verbosity=2)
