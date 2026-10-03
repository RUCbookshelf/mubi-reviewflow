"""Convert supported reported test statistics to effect estimates."""

from __future__ import annotations

import math

from scipy.stats import t as student_t
from scipy.special import poch


def _independent_t_result(t: float, n_t: int, n_c: int,
                          input_data: dict, entry_method: str,
                          reported_d: float | None = None) -> dict:
    df = n_t + n_c - 2
    try:
        n_total = float(n_t + n_c)
        df = float(df)
        inv_n = math.fsum((1.0 / n_t, 1.0 / n_c))
        j = poch((df - 1) / 2, .5) / math.sqrt(df / 2)
        cohen_d = reported_d if reported_d is not None else t * math.sqrt(inv_n)
        hedges_g = j * cohen_d
        # Scale before squaring so large, finite t values are rejected only
        # when the resulting variance itself is outside the float range.
        d_over_sqrt_2n = cohen_d / math.sqrt(n_total) / math.sqrt(2)
        variance_d = math.fsum((inv_n, d_over_sqrt_2n * d_over_sqrt_2n))
        variance_g = j * j * variance_d
        se = math.sqrt(variance_g)
    except (OverflowError, ValueError) as exc:
        raise ValueError("the resulting effect and standard error must be finite") from exc

    if not (0 < j <= 1) or not math.isfinite(cohen_d) or not math.isfinite(hedges_g):
        raise ValueError("the resulting effect and standard error must be finite")
    if not math.isfinite(se) or se <= 0:
        raise ValueError("the resulting standard error must be finite and positive")
    return {
        "measure": "SMD",
        "estimate": hedges_g,
        "cohen_d": cohen_d,
        "hedges_g": hedges_g,
        "se": se,
        "se_scale": "natural",
        "input_data": input_data,
        "entry_method": entry_method,
    }

def calculate_test_statistic_effect(format_name: str, values: dict) -> dict:
    """Convert supported independent-groups test statistics to Hedges' g."""
    if format_name not in {"independent_t_n", "independent_d_n", "independent_p_n", "independent_f_n"}:
        raise ValueError("unsupported test statistic effect format")
    required = {
        "independent_t_n": {"t", "n_t", "n_c"},
        "independent_d_n": {"d", "n_t", "n_c"},
        "independent_p_n": {"p", "n_t", "n_c", "direction"},
        "independent_f_n": {"f", "df_num", "df_den", "n_t", "n_c", "direction"},
    }[format_name]
    if not isinstance(values, dict) or set(values) != required:
        raise ValueError(f"values must contain exactly: {', '.join(sorted(required))}")

    counts = (values["n_t"], values["n_c"])
    if any(type(n) is not int or n < 2 for n in counts):
        raise ValueError("n_t and n_c must be integers >= 2")
    n_t, n_c = counts
    if format_name == "independent_t_n":
        t = values["t"]
        if isinstance(t, bool) or not isinstance(t, (int, float)):
            raise ValueError("t must be a finite number")
        try:
            t = float(t)
        except (OverflowError, ValueError) as exc:
            raise ValueError("t must be a finite number") from exc
        if not math.isfinite(t):
            raise ValueError("t must be a finite number")
        return _independent_t_result(t, n_t, n_c, dict(values), format_name)

    if format_name == "independent_d_n":
        d = values["d"]
        if isinstance(d, bool) or not isinstance(d, (int, float)):
            raise ValueError("d must be a finite number")
        try:
            d = float(d)
        except (OverflowError, ValueError) as exc:
            raise ValueError("d must be a finite number") from exc
        if not math.isfinite(d):
            raise ValueError("d must be a finite number")
        return _independent_t_result(0.0, n_t, n_c, dict(values), format_name, reported_d=d)

    if format_name == "independent_f_n":
        f_value = values["f"]
        if isinstance(f_value, bool) or not isinstance(f_value, (int, float)):
            raise ValueError("f must be a finite number >= 0")
        try:
            f_value = float(f_value)
        except (OverflowError, ValueError) as exc:
            raise ValueError("f must be a finite number >= 0") from exc
        if not math.isfinite(f_value) or f_value < 0:
            raise ValueError("f must be a finite number >= 0")
        df_num, df_den = values["df_num"], values["df_den"]
        if type(df_num) is not int or type(df_den) is not int:
            raise ValueError("df_num and df_den must be integers")
        if df_num != 1 or df_den != n_t + n_c - 2:
            raise ValueError("F degrees of freedom must be 1 and n_t + n_c - 2")
        direction = values["direction"]
        if not isinstance(direction, str) or direction not in {"treatment_higher", "control_higher"}:
            raise ValueError("direction must be 'treatment_higher' or 'control_higher'")
        t = math.sqrt(f_value)
        if direction == "control_higher":
            t = -t
        result = _independent_t_result(t, n_t, n_c, dict(values), format_name)
        result["reconstructed_t"] = t
        return result

    p = values["p"]
    if isinstance(p, bool) or not isinstance(p, (int, float)):
        raise ValueError("p must be a finite number in (0, 1]")
    try:
        p = float(p)
        df = float(n_t + n_c - 2)
    except (OverflowError, ValueError) as exc:
        raise ValueError("p must produce a finite t quantile") from exc
    if not math.isfinite(p) or not 0 < p <= 1:
        raise ValueError("p must be a finite number in (0, 1]")
    direction = values["direction"]
    if not isinstance(direction, str) or direction not in {"treatment_higher", "control_higher"}:
        raise ValueError("direction must be 'treatment_higher' or 'control_higher'")
    t_abs = 0.0 if p == 1 else float(student_t.isf(p / 2, df))
    if not math.isfinite(t_abs):
        raise ValueError("p must produce a finite t quantile")
    t = t_abs if direction == "treatment_higher" else -t_abs
    result = _independent_t_result(t, n_t, n_c, dict(values), format_name)
    result["reconstructed_t"] = t
    return result
