"""Explicit zero-cell sensitivity for two-arm binary inverse-variance effects."""

from __future__ import annotations

import math
from numbers import Integral, Real

_MAX_COUNT = 2**63 - 1
_COUNT_FIELDS = ("events_t", "total_t", "events_c", "total_c")


def calculate_zero_cell_effect(events_t: int, total_t: int, events_c: int, total_c: int,
                               measure: str, correction: str | float,
                               source_key: str | None = None) -> dict:
    """Calculate one two-arm log OR/RR with a caller-selected cell correction.

    ``correction`` is required and must be ``"none"`` or ``0.5``. The latter
    adds 0.5 to all four cells only when at least one raw cell is zero, matching
    ``metafor::escalc(add=0.5, to="only0")``. Undefined estimates are returned
    as ``None`` with warnings; observed counts and source provenance are kept.
    """
    counts = (events_t, total_t, events_c, total_c)
    if any(isinstance(value, bool) or not isinstance(value, Integral) or
           value < 0 or value > _MAX_COUNT for value in counts):
        raise ValueError("arm counts must be nonnegative SQLite-range integers")
    counts = tuple(int(value) for value in counts)
    events_t, total_t, events_c, total_c = counts
    if total_t == 0 or total_c == 0 or events_t > total_t or events_c > total_c:
        raise ValueError("arm totals must be positive and events cannot exceed totals")
    if not isinstance(measure, str) or measure not in {"OR", "RR"}:
        raise ValueError("measure must be OR or RR")
    if isinstance(correction, str) and correction == "none":
        correction_value = 0.0
        correction_label: str | float = "none"
    elif isinstance(correction, Real) and not isinstance(correction, bool) and correction == 0.5:
        correction_value = 0.5
        correction_label = 0.5
    else:
        raise ValueError('correction must be "none" or 0.5')
    if source_key is not None and (not isinstance(source_key, str) or not source_key.strip()):
        raise ValueError("source_key must be a non-empty string when supplied")

    a, b, c, d = events_t, total_t - events_t, events_c, total_c - events_c
    raw_cells = {"events_t": a, "non_events_t": b, "events_c": c, "non_events_c": d}
    zero_cells = [name for name, value in raw_cells.items() if value == 0]
    if not zero_cells:
        cell_status = "none"
    elif a == 0 and c == 0:
        cell_status = "double_zero"
    elif b == 0 and d == 0:
        cell_status = "all_event"
    elif len(zero_cells) == 1:
        cell_status = "single_zero"
    else:
        cell_status = "mixed_zeros"

    correction_applied = bool(zero_cells) and correction_value > 0
    adjusted = {name: value + correction_value if correction_applied else value
                for name, value in raw_cells.items()}
    a2, b2, c2, d2 = (adjusted[name] for name in
                      ("events_t", "non_events_t", "events_c", "non_events_c"))

    log_estimate = variance = None
    if measure == "OR" and min(a2, b2, c2, d2) > 0:
        log_estimate = math.log(a2) + math.log(d2) - math.log(b2) - math.log(c2)
        variance = 1 / a2 + 1 / b2 + 1 / c2 + 1 / d2
    elif measure == "RR" and a2 > 0 and c2 > 0:
        n_t, n_c = a2 + b2, c2 + d2
        log_estimate = math.log(a2) + math.log(n_c) - math.log(n_t) - math.log(c2)
        # Algebraically 1/a - 1/n per arm, written to avoid cancellation near all-event cells.
        variance = b2 / (a2 * n_t) + d2 / (c2 * n_c)

    estimate = math.exp(log_estimate) if log_estimate is not None else None
    se = math.sqrt(variance) if variance is not None and variance > 0 else None
    warnings = []
    if zero_cells:
        if correction_applied:
            warnings.append("The selected 0.5 correction was added to all four cells because the raw table contains a zero; compare this sensitivity with the uncorrected result.")
        else:
            warnings.append("The raw table contains zero cells; no continuity correction was applied.")
    if cell_status in {"double_zero", "all_event"}:
        warnings.append("Both arms have the same outcome status (no events or all events); Cochrane treats this table as uninformative for a relative effect. Any corrected value is a sensitivity calculation.")
    if log_estimate is None:
        warnings.append("The selected calculation cannot produce a finite log ratio; estimate and SE are unavailable.")
    elif se is None:
        warnings.append("The estimate is finite but its sampling variance is zero; inverse-variance weighting is unavailable.")

    assumptions = ["Treatment and control arms are independent; the treatment arm is the ratio numerator.",
                   "A large-sample inverse-variance approximation is used on the log ratio scale."]
    if correction_applied:
        assumptions.append("The selected 0.5 correction is added to all four cells when any raw cell is zero.")
    elif correction_value == 0.5:
        assumptions.append("The 0.5 correction was selected but no cells were zero, so the table was unchanged.")
    else:
        assumptions.append("No continuity correction is applied.")

    return {
        "measure": measure,
        "estimate": estimate,
        "log_estimate": log_estimate,
        "se": se,
        "se_scale": "log",
        "inverse_variance_ready": estimate is not None and se is not None,
        "cell_status": cell_status,
        "zero_cells": zero_cells,
        "correction": correction_label,
        "correction_applied": correction_applied,
        "original_counts": dict(zip(_COUNT_FIELDS, counts)),
        "original_cells": raw_cells,
        "adjusted_cells": adjusted,
        "source_key": source_key.strip() if source_key is not None else None,
        "assumptions": assumptions,
        "warnings": warnings,
    }
