"""Estimate an arm's mean and SD from a reported median with range or IQR.

Many trials report only the median with the minimum/maximum or with the first
and third quartiles. This module reconstructs an arm-level mean and standard
deviation for such studies with published formula families, so the study can
enter an MD/SMD synthesis. The estimates are data-entry aids, not observed
summary statistics and not a new effect-size definition. See
docs/methodology/summary_stats_estimation.md for the formulas, the source
papers, and the labeling obligations before pooling.

Implemented methods (explicit ``method`` argument, no default):

- ``"hozo_2005"``: Hozo, Djulbegovic and Hozo 2005, median + range only.
- ``"wan_2014"``: Wan, Wang, Liu and Tong 2014, median + range and median + IQR.
- ``"luo_2018"``: Luo, Wan, Liu and Tong 2018 mean estimator paired with the
  Wan 2014 SD estimator (Luo et al. propose no SD estimator; this pairing is
  the combination used by the authors' own online calculator).

Shi et al. 2020 is deliberately not offered as a method: their improved SD
estimator requires the full five-number summary (range AND IQR together),
which neither entry function receives, and for IQR-only data their paper
endorses the Wan 2014 estimator used here.
"""

from __future__ import annotations

import copy
import math
from statistics import NormalDist

_INV_CDF = NormalDist().inv_cdf

_RANGE_METHODS = ("hozo_2005", "wan_2014", "luo_2018")
_IQR_METHODS = ("wan_2014", "luo_2018")

# Wan et al. 2014, Table 1: xi(n) = 2*E(Z_(n)) for n = 1..50, indexed by n - 1.
_WAN_XI_TABLE = (
    0.0, 1.128, 1.693, 2.059, 2.326, 2.534, 2.704, 2.847, 2.970, 3.078,
    3.173, 3.259, 3.336, 3.407, 3.472, 3.532, 3.588, 3.640, 3.689, 3.735,
    3.778, 3.819, 3.858, 3.895, 3.931, 3.964, 3.997, 4.027, 4.057, 4.086,
    4.113, 4.139, 4.165, 4.189, 4.213, 4.236, 4.259, 4.280, 4.301, 4.322,
    4.341, 4.361, 4.379, 4.398, 4.415, 4.433, 4.450, 4.466, 4.482, 4.498,
)

# Wan et al. 2014, Table 2: eta(n) = 2*E(Z_(3Q+1)) for Q = 1..50 (n = 4Q + 1),
# indexed by Q - 1.
_WAN_ETA_TABLE = (
    0.990, 1.144, 1.206, 1.239, 1.260, 1.274, 1.284, 1.292, 1.298, 1.303,
    1.307, 1.311, 1.313, 1.316, 1.318, 1.320, 1.322, 1.323, 1.324, 1.326,
    1.327, 1.328, 1.329, 1.330, 1.330, 1.331, 1.332, 1.332, 1.333, 1.333,
    1.334, 1.334, 1.335, 1.335, 1.336, 1.336, 1.336, 1.337, 1.337, 1.337,
    1.338, 1.338, 1.338, 1.338, 1.339, 1.339, 1.339, 1.339, 1.339, 1.340,
)

_ESTIMATE_WARNING = (
    "mean_estimate and sd_estimate are formula-based estimates reconstructed "
    "from the reported median (and range or IQR), not observed statistics; "
    "label them as estimated in any synthesis and consider a sensitivity "
    "analysis before pooling them with studies reporting observed means and SDs"
)

_WAN_RANGE_SD_NOTE = (
    "the SD estimate divides the range by Wan et al.'s xi(n); their "
    "simulations show the range-based SD estimator degrades for strongly "
    "skewed data"
)


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _count(value: object, name: str) -> int:
    if type(value) is not int or value < 2:
        raise ValueError(f"{name} must be an integer >= 2")
    try:
        if not math.isfinite(float(value)):
            raise ValueError(f"{name} must be a finite integer")
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite integer") from exc
    return value


def _wan_xi(n: int) -> float:
    """Wan et al. 2014 range divisor: Table 1 for n <= 50, Blom eq. (9) above."""
    if n <= 50:
        return _WAN_XI_TABLE[n - 1]
    return 2.0 * _INV_CDF((n - 0.375) / (n + 0.25))


def _wan_eta(n: int) -> float:
    """Wan et al. 2014 IQR divisor: Table 2 for n = 4Q+1 <= 201, Blom eq. (16) else."""
    if n % 4 == 1 and n <= 201:
        return _WAN_ETA_TABLE[(n - 1) // 4 - 1]
    return 2.0 * _INV_CDF((0.75 * n - 0.125) / (n + 0.25))


def _wan_range_sd_detail(n: int) -> tuple[str, str]:
    if n <= 50:
        return ("formula_7_sd_table1_xi",
                "sd = (b - a) / xi(n), xi(n) from Wan et al. 2014 Table 1")
    return ("formula_9_sd_blom",
            "sd = (b - a) / (2 * invnorm((n - 0.375) / (n + 0.25))), "
            "Wan et al. 2014 eq. (9) for n > 50")


def _wan_iqr_sd_detail(n: int) -> tuple[str, str]:
    if n % 4 == 1 and n <= 201:
        return ("formula_15_sd_table2_eta",
                "sd = (q3 - q1) / eta(n), eta(n) from Wan et al. 2014 "
                "Table 2 (n = 4Q + 1 <= 201)")
    return ("formula_16_sd_blom",
            "sd = (q3 - q1) / (2 * invnorm((0.75 * n - 0.125) / (n + 0.25))), "
            "Wan et al. 2014 eq. (16)")


def _hozo_parts(n: int, a: float, m: float, b: float) -> dict:
    """Hozo et al. 2005 median + range rules (formula (5), (16), Table 3)."""
    if n <= 25:
        mean_id = "formula_5_mean_n_le_25"
        mean_formula = "mean = (a + 2m + b) / 4  [Hozo 2005 formula (5)]"
    else:
        mean_id = "table3_median_n_gt_25"
        mean_formula = "mean = median  [Hozo 2005 Table 3 rule for n > 25]"
    if n <= 15:
        sd_id = "formula_16_sd_n_le_15"
        sd_formula = ("sd = sqrt(((b - a)^2 + (a - 2m + b)^2 / 4) / 12)  "
                      "[Hozo 2005 formula (16), variance form]")
    elif n <= 70:
        sd_id = "table3_range_over_4_sd"
        sd_formula = "sd = (b - a) / 4  [Hozo 2005 Table 3 rule for 15 < n <= 70]"
    else:
        sd_id = "table3_range_over_6_sd"
        sd_formula = "sd = (b - a) / 6  [Hozo 2005 Table 3 rule for n > 70]"
    assumptions = [
        "Hozo et al. 2005 is distribution-free: it assumes nothing about the "
        "data distribution, but the variance derivation requires non-negative "
        "data and assumes an odd sample size for symmetry",
        "Hozo et al. Table 3 (unknown distribution): mean by formula (5) for "
        "n <= 25 and by the median for n > 25; SD by formula (16) for n <= 15, "
        "by range/4 for 15 < n <= 70, and by range/6 for n > 70",
    ]
    warnings = [_ESTIMATE_WARNING]
    if n <= 15:
        warnings.append(
            "Hozo formula (16) is intended for small samples (n <= 15); its "
            "derivation assumes equidistantly spaced data, and the estimate "
            "is crude"
        )
    elif n <= 70:
        warnings.append(
            "Hozo's range/4 rule assumes an approximately normal distribution "
            "(the range covers about 4 SDs); it underestimates the SD for "
            "skewed or heavy-tailed data"
        )
    else:
        warnings.append(
            "Hozo's range/6 rule is the Chebyshev worst case for any "
            "distribution and typically underestimates the SD of real data"
        )
    if a < 0:
        warnings.append(
            "the minimum is negative: Hozo et al. derived their variance "
            "formulas under a non-negative-data assumption, so treat the SD "
            "estimate with extra caution"
        )
    return {
        "method": None,  # composed by the caller
        "mean_id": mean_id,
        "sd_id": sd_id,
        "mean_formula": mean_formula,
        "sd_formula": sd_formula,
        "assumptions": assumptions,
        "warnings": warnings,
    }


def _wan_range_parts(n: int, a: float, m: float, b: float) -> dict:
    """Wan et al. 2014 median + range: mean eq. (3), SD eqs. (7)-(9) + Table 1."""
    sd_id, sd_formula = _wan_range_sd_detail(n)
    assumptions = [
        "Wan et al. 2014 derive the SD estimator under a normal-theory model "
        "(expectations of standard-normal order statistics); their "
        "simulations cover normal, log-normal, beta(9,4), exponential and "
        "Weibull data",
        "the mean estimator eq. (3) equals Hozo's formula (5); Wan et al. "
        "apply it for any n, unlike Hozo's switch to the median at n = 25",
        "Wan et al. Table 1 gives xi(n) for n <= 50 and their Blom "
        "approximation eq. (9) is stated for n > 50",
    ]
    warnings = [_ESTIMATE_WARNING, _WAN_RANGE_SD_NOTE]
    if n > 50:
        warnings.append(
            "for n > 50 the SD estimate uses Wan et al.'s Blom approximation "
            "eq. (9) instead of the tabulated xi(n)"
        )
    return {
        "method": None,
        "mean_id": "formula_3_mean",
        "sd_id": sd_id,
        "mean_formula": "mean = (a + 2m + b) / 4  [Wan 2014 eq. (3)]",
        "sd_formula": sd_formula,
        "assumptions": assumptions,
        "warnings": warnings,
    }


def _luo_range_parts(n: int, a: float, m: float, b: float) -> dict:
    """Luo et al. 2018 median + range: mean eqs. (6)-(7); SD paired from Wan."""
    sd_id, sd_formula = _wan_range_sd_detail(n)
    assumptions = [
        "Luo et al. 2018 derive the optimal weight under a normal-theory "
        "model (normal order statistics); their simulations cover normal, "
        "log-normal, beta, exponential and Weibull data",
        "Luo et al. 2018 provide a mean estimator only; the SD estimator is "
        "paired from Wan et al. 2014, the same combination used by the "
        "authors' online calculator",
        "the approximated optimal weight eq. (6) is 4/(4 + n^0.75) on the "
        "mid-range: about 0.26 at n = 25 and about 0.11 at n = 101, so the "
        "mean estimate approaches the median as n grows",
    ]
    warnings = [
        _ESTIMATE_WARNING,
        "the SD estimate is Wan et al. (2014), not a Luo et al. estimator: "
        "cite Wan et al. for the SD and Luo et al. for the mean when "
        "reporting this method",
        _WAN_RANGE_SD_NOTE,
    ]
    return {
        "method": None,
        "mean_id": "formula_7_mean_eq6_weight",
        "sd_id": sd_id,
        "mean_formula": ("mean = (4 / (4 + n^0.75)) * (a + b) / 2 + "
                         "(n^0.75 / (4 + n^0.75)) * m  "
                         "[Luo 2018 eqs. (6)-(7)]"),
        "sd_formula": sd_formula,
        "assumptions": assumptions,
        "warnings": warnings,
    }


def _wan_iqr_parts(n: int, q1: float, m: float, q3: float) -> dict:
    """Wan et al. 2014 median + IQR: mean eq. (14), SD eqs. (15)-(16) + Table 2."""
    sd_id, sd_formula = _wan_iqr_sd_detail(n)
    assumptions = [
        "Wan et al. 2014 derive the IQR estimators under a normal-theory "
        "model with n = 4Q + 1 (median an observed value, quartile blocks of "
        "equal size); other quartile conventions add approximation error",
        "the mean estimator eq. (14) gives weight 1/3 to q1, m and q3; the "
        "SD estimator divides the IQR by eta(n), which converges to about "
        "1.35 (Wan eq. (17), the Cochrane Handbook shortcut) as n grows",
        "Wan et al. Table 2 gives eta(n) for n = 4Q + 1 <= 201 and their Blom "
        "approximation eq. (16) otherwise",
    ]
    warnings = [_ESTIMATE_WARNING]
    if n < 21:
        warnings.append(
            "for small samples the IQR-based SD estimate is unstable; Wan et "
            "al. note the tabulated eta(n) (formula (15)) is more accurate "
            "than the 1.35 shortcut for small n"
        )
    return {
        "method": None,
        "mean_id": "formula_14_mean",
        "sd_id": sd_id,
        "mean_formula": "mean = (q1 + m + q3) / 3  [Wan 2014 eq. (14)]",
        "sd_formula": sd_formula,
        "assumptions": assumptions,
        "warnings": warnings,
    }


def _luo_iqr_parts(n: int, q1: float, m: float, q3: float) -> dict:
    """Luo et al. 2018 median + IQR: mean eqs. (10)-(11); SD paired from Wan."""
    sd_id, sd_formula = _wan_iqr_sd_detail(n)
    assumptions = [
        "Luo et al. 2018 derive the optimal weight under a normal-theory "
        "model with n = 4Q + 1; the approximated optimal weight eq. (10) is "
        "0.7 + 0.39/n on the mid-quartile range, with theoretical limit about "
        "0.699 as n grows",
        "Luo et al. 2018 provide a mean estimator only; the SD estimator is "
        "paired from Wan et al. 2014, the same combination used by the "
        "authors' online calculator",
    ]
    warnings = [
        _ESTIMATE_WARNING,
        "the SD estimate is Wan et al. (2014), not a Luo et al. estimator: "
        "cite Wan et al. for the SD and Luo et al. for the mean when "
        "reporting this method",
    ]
    return {
        "method": None,
        "mean_id": "formula_11_mean_eq10_weight",
        "sd_id": sd_id,
        "mean_formula": ("mean = (0.7 + 0.39 / n) * (q1 + q3) / 2 + "
                         "(0.3 - 0.39 / n) * m  [Luo 2018 eqs. (10)-(11)]"),
        "sd_formula": sd_formula,
        "assumptions": assumptions,
        "warnings": warnings,
    }


def _finalize(n: int, values: dict, method: str, parts: dict, mean: float,
              sd: float, entry: str) -> dict:
    try:
        variance = sd * sd
    except (OverflowError, ValueError) as exc:
        raise ValueError("the resulting estimates must be finite") from exc
    if not all(math.isfinite(value) for value in (mean, sd, variance)):
        raise ValueError("the resulting estimates must be finite")
    if sd <= 0:
        raise ValueError("the resulting SD estimate must be positive")
    method_label = f"{method}:mean={parts['mean_id']};sd={parts['sd_id']}"
    return {
        "mean_estimate": mean,
        "sd_estimate": sd,
        "variance_estimate": variance,
        "method": method_label,
        "formula_detail": {
            "mean": f"{method} {parts['mean_id']}: {parts['mean_formula']}",
            "sd": f"{method} {parts['sd_id']}: {parts['sd_formula']}",
        },
        "entry_method": entry,
        "n": n,
        "input_data": copy.deepcopy(values),
        "assumptions": parts["assumptions"],
        "warnings": parts["warnings"],
    }


def estimate_from_median_range(n, median, min_val, max_val, *, method,
                               source_provenance=None) -> dict:
    """Estimate an arm's mean and SD from median + minimum/maximum.

    ``method`` is required and must be one of ``"hozo_2005"``,
    ``"wan_2014"`` or ``"luo_2018"``. All numeric inputs must be finite,
    ``n`` must be an integer >= 2 and ``min_val <= median <= max_val`` must
    hold. The range must be positive (``min_val < max_val``) so that a
    positive SD is estimable. ``source_provenance`` is an optional free-form
    object recorded verbatim inside the returned ``input_data``.
    """
    n = _count(n, "n")
    a = _number(min_val, "min_val")
    m = _number(median, "median")
    b = _number(max_val, "max_val")
    if a > m:
        raise ValueError("min_val must be <= median")
    if m > b:
        raise ValueError("median must be <= max_val")
    if a >= b:
        raise ValueError(
            "min_val must be < max_val: a positive range is required to "
            "estimate a positive SD"
        )
    if not isinstance(method, str) or method not in _RANGE_METHODS:
        raise ValueError(
            "method must be one of "
            f"{', '.join(repr(name) for name in _RANGE_METHODS)} for "
            "median+range inputs"
        )
    if source_provenance is not None and not isinstance(source_provenance, dict):
        raise ValueError("source_provenance must be an object")

    values = {
        "n": n,
        "median": m,
        "min_val": a,
        "max_val": b,
        "method": method,
    }
    if source_provenance is not None:
        values["source_provenance"] = source_provenance

    if method == "hozo_2005":
        parts = _hozo_parts(n, a, m, b)
        mean_value = (a + 2.0 * m + b) / 4.0 if n <= 25 else m
        if n <= 15:
            sd_value = math.sqrt(
                ((b - a) ** 2 + (a - 2.0 * m + b) ** 2 / 4.0) / 12.0
            )
        elif n <= 70:
            sd_value = (b - a) / 4.0
        else:
            sd_value = (b - a) / 6.0
    elif method == "wan_2014":
        parts = _wan_range_parts(n, a, m, b)
        mean_value = (a + 2.0 * m + b) / 4.0
        sd_value = (b - a) / _wan_xi(n)
    else:
        parts = _luo_range_parts(n, a, m, b)
        weight = 4.0 / (4.0 + n ** 0.75)
        mean_value = weight * (a + b) / 2.0 + (1.0 - weight) * m
        sd_value = (b - a) / _wan_xi(n)
    return _finalize(n, values, method, parts, mean_value, sd_value,
                     "median_range")


def estimate_from_median_iqr(n, median, q1, q3, *, method,
                             source_provenance=None) -> dict:
    """Estimate an arm's mean and SD from median + first/third quartiles.

    ``method`` is required and must be ``"wan_2014"`` or ``"luo_2018"``;
    Hozo et al. 2005 provides no median+IQR estimator. All numeric inputs must
    be finite, ``n`` must be an integer >= 2 and ``q1 <= median <= q3`` must
    hold. The IQR must be positive (``q1 < q3``) so that a positive SD is
    estimable. ``source_provenance`` is an optional free-form object recorded
    verbatim inside the returned ``input_data``.
    """
    n = _count(n, "n")
    first = _number(q1, "q1")
    m = _number(median, "median")
    third = _number(q3, "q3")
    if first > m:
        raise ValueError("q1 must be <= median")
    if m > third:
        raise ValueError("median must be <= q3")
    if first >= third:
        raise ValueError(
            "q1 must be < q3: a positive IQR is required to estimate a "
            "positive SD"
        )
    if not isinstance(method, str) or method not in _IQR_METHODS:
        raise ValueError(
            "method must be one of "
            f"{', '.join(repr(name) for name in _IQR_METHODS)} for "
            "median+IQR inputs; hozo_2005 provides no median+IQR estimator"
        )
    if source_provenance is not None and not isinstance(source_provenance, dict):
        raise ValueError("source_provenance must be an object")

    values = {
        "n": n,
        "median": m,
        "q1": first,
        "q3": third,
        "method": method,
    }
    if source_provenance is not None:
        values["source_provenance"] = source_provenance

    if method == "wan_2014":
        parts = _wan_iqr_parts(n, first, m, third)
        mean_value = (first + m + third) / 3.0
    else:
        parts = _luo_iqr_parts(n, first, m, third)
        weight = 0.7 + 0.39 / n
        mean_value = weight * (first + third) / 2.0 + (1.0 - weight) * m
    sd_value = (third - first) / _wan_eta(n)
    return _finalize(n, values, method, parts, mean_value, sd_value,
                     "median_iqr")
