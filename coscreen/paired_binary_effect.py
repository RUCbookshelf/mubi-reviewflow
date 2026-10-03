"""Paired-design binary effects: matched-pair odds ratio and paired risk difference."""

from __future__ import annotations

import copy
import math
from numbers import Integral, Real
from statistics import NormalDist

_FORMAT = "paired_binary_2x2"
_DIRECTION = "treatment_minus_control"
_CORRECTION = "add_half_discordant"
_CELL_KEYS = frozenset({"a", "b", "c", "d"})
_MAX_COUNT = 2**63 - 1


def _cell_count(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError(f"cell {name} must be an integer")
    if value < 0 or value > _MAX_COUNT:
        raise ValueError(f"cell {name} must be between 0 and {int(_MAX_COUNT)}")
    return int(value)


def paired_binary_effect(cells: dict, *, direction: str, level: float = 0.95,
                         or_correction: str | None = None,
                         source_provenance: dict | None = None) -> dict:
    """Calculate a matched-pair odds ratio and a paired risk difference.

    ``cells`` maps the paired 2x2 counts: ``a`` = both measurements positive,
    ``b`` = only the treatment/target measurement positive, ``c`` = only the
    control/baseline measurement positive, ``d`` = both measurements negative.
    ``direction`` must be ``"treatment_minus_control"`` so the b/c odds ratio
    and the (b - c)/n risk difference have an explicit sign convention.

    The matched-pair odds ratio uses only the discordant pairs: the estimate is
    b/c on the natural scale with log-scale SE sqrt(1/b + 1/c). With zero
    discordants in one arm the uncorrected main result is refused; the caller
    may explicitly select ``or_correction="add_half_discordant"`` to add 0.5 to
    both b and c, which is reported as a sensitivity analysis. The paired risk
    difference (b - c)/n uses SE sqrt((b + c - (b - c)^2/n)/n^2) on the natural
    scale. McNemar chi-squared values are returned for single-table
    description only and must never enter pooling.
    """
    if not isinstance(cells, dict) or set(cells) != _CELL_KEYS:
        raise ValueError("cells must contain exactly the keys a, b, c and d")
    counts = {name: _cell_count(cells[name], name) for name in ("a", "b", "c", "d")}
    a, b, c, d = counts["a"], counts["b"], counts["c"], counts["d"]
    n = a + b + c + d
    if n < 2:
        raise ValueError("the paired table must contain at least n = 2 pairs")

    if direction != _DIRECTION:
        raise ValueError("direction must be 'treatment_minus_control'")
    if isinstance(level, bool) or not isinstance(level, Real) or not math.isfinite(float(level)) \
            or not 0.0 < float(level) < 1.0:
        raise ValueError("level must be a number strictly between 0 and 1")
    if or_correction is not None and or_correction != _CORRECTION:
        raise ValueError(f'or_correction must be None or "{_CORRECTION}"')
    if b == 0 and c == 0:
        raise ValueError(
            "b = c = 0 gives no discordant pairs; the matched-pair odds ratio "
            "is undefined and the paired risk difference has no information"
        )
    if (b == 0 or c == 0) and or_correction is None:
        raise ValueError(
            "a discordant count is zero and no correction was selected; the "
            f'uncorrected main result is refused, pass or_correction="{_CORRECTION}" '
            "to request the explicit sensitivity value"
        )
    if or_correction == _CORRECTION and not (b == 0 or c == 0):
        raise ValueError(
            f'or_correction="{_CORRECTION}" is only available when b = 0 or c = 0; '
            "both discordant counts are positive"
        )
    if source_provenance is not None and not isinstance(source_provenance, dict):
        raise ValueError("source_provenance must be an object")

    # The half correction is an explicitly selected sensitivity for the odds
    # ratio only; the risk difference always uses the observed counts.
    b_adj, c_adj = (b + 0.5, c + 0.5) if or_correction == _CORRECTION else (float(b), float(c))

    try:
        z = NormalDist().inv_cdf((1.0 + float(level)) / 2.0)
        log_or = math.log(b_adj) - math.log(c_adj)
        se_log_or = math.sqrt(math.fsum((1.0 / b_adj, 1.0 / c_adj)))
        or_estimate = math.exp(log_or)
        or_ci_low = math.exp(log_or - z * se_log_or)
        or_ci_high = math.exp(log_or + z * se_log_or)
    except (OverflowError, ValueError) as exc:
        raise ValueError("the matched-pair odds ratio must be finite") from exc

    # Verified form: sqrt((b + c - (b - c)^2 / n) / n^2); Monte Carlo-checked
    # against multinomial simulation. Zero variance only when the table has no
    # concordant pairs and one discordant count is zero.
    var_rd = math.fsum((float(b + c), -((b - c) ** 2) / n)) / n**2
    if not math.isfinite(var_rd) or var_rd <= 0:
        raise ValueError(
            "the paired risk difference has zero or unrepresentable variance; "
            "the table carries no information for it"
        )
    risk_difference = (b - c) / n
    se_rd = math.sqrt(var_rd)
    rd_ci_low = risk_difference - z * se_rd
    rd_ci_high = risk_difference + z * se_rd

    warnings = []
    if or_correction == _CORRECTION:
        warnings.append(
            "The selected add-half correction was applied to both discordant "
            "counts because one of them is zero; the corrected result is a "
            "sensitivity analysis and not a main result."
        )
        warnings.append(
            "One discordant count is zero, so the matched-pair odds ratio rests "
            "on very few informative pairs; treat the interval as unreliable "
            "and prefer an exact conditional interval when pooling."
        )

    return {
        "measure": "PAIRED_OR",
        "estimate": or_estimate,
        "log_estimate": log_or,
        "se": se_log_or,
        "se_scale": "log",
        "or_ci_low": or_ci_low,
        "or_ci_high": or_ci_high,
        "rd_measure": "PAIRED_RD",
        "risk_difference": risk_difference,
        "rd_se": se_rd,
        "rd_ci_low": rd_ci_low,
        "rd_ci_high": rd_ci_high,
        "ci_level": float(level),
        "ci_method": "normal_wald",
        "discordant_total": b + c,
        "p_target": (a + b) / n,
        "p_control": (a + c) / n,
        "mcnemar_chi2_descriptive_only": (b - c) ** 2 / (b + c),
        "mcnemar_chi2_continuity_descriptive_only": (abs(b - c) - 1) ** 2 / (b + c),
        "inverse_variance_ready": True,
        "rd_inverse_variance_ready": True,
        "or_correction": or_correction,
        "or_correction_applied": or_correction == _CORRECTION,
        "cells": counts,
        "direction": _DIRECTION,
        "entry_method": _FORMAT,
        "input_data": {
            "cells": dict(counts),
            "direction": _DIRECTION,
            "level": float(level),
            "or_correction": or_correction,
            "source_provenance": copy.deepcopy(source_provenance) if source_provenance is not None else None,
        },
        "source_provenance": copy.deepcopy(source_provenance) if source_provenance is not None else None,
        "assumptions": [
            "Cell semantics: a = both measurements positive, b = only the "
            "treatment/target measurement positive, c = only the control/baseline "
            "measurement positive, d = both negative; direction is treatment minus control.",
            "The matched-pair odds ratio depends only on the discordant pairs b and c; "
            "concordant pairs do not enter its estimate or variance.",
            "Standard errors and intervals are large-sample normal (Wald) approximations.",
            "The McNemar chi-squared values describe this single table only and must not "
            "enter meta-analytic pooling.",
            "The paired analysis itself does not detect or correct crossover residual "
            "(carryover) effects; use design-level review to exclude biased periods.",
        ]
        + (['or_correction="add_half_discordant" adds 0.5 to both b and c when one of them is zero.']
           if or_correction == _CORRECTION else ["No continuity correction is applied."]),
        "warnings": warnings,
    }
