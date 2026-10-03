"""Study-level influence diagnostics for independent inverse-variance inputs."""

from __future__ import annotations

import math
from collections.abc import Mapping
from numbers import Real

from coscreen.review_analysis import MEASURES, RATIOS, _synthesize_rows


_MODELS = {"fixed", "random", "random_pm", "random_reml"}


def influence_diagnostics(rows: list[dict], measure: str, model: str = "fixed") -> list[dict]:
    """Calculate externally studentized residuals and Cook's distance by study.

    Each input row must be one selected effect from a distinct independent study.
    Effect estimates and SEs are interpreted on the scale accepted by
    ``review_analysis._synthesize_rows``.
    """
    if measure not in MEASURES or model not in _MODELS:
        raise ValueError("unsupported measure or model")
    if not isinstance(rows, list) or len(rows) < 3:
        raise ValueError("influence diagnostics require at least three independent studies")

    checked = []
    seen = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("each effect must be a mapping")
        study_id = row.get("study_id")
        estimate, se = row.get("estimate"), row.get("se")
        if not isinstance(study_id, str) or not study_id.strip():
            raise ValueError("each effect needs a study id")
        if study_id in seen:
            raise ValueError("influence diagnostics require one effect per independent study")
        seen.add(study_id)
        if (isinstance(estimate, bool) or not isinstance(estimate, Real)
                or isinstance(se, bool) or not isinstance(se, Real)):
            raise ValueError("effect estimates and SEs must be finite numbers")
        try:
            estimate, se = float(estimate), float(se)
            sampling_variance = se * se
        except (OverflowError, TypeError, ValueError) as exc:
            raise ValueError("effect estimates and SEs must be finite numbers") from exc
        if (not math.isfinite(estimate) or not math.isfinite(se)
                or not math.isfinite(sampling_variance) or sampling_variance <= 0
                or se <= 0 or (measure in RATIOS and estimate <= 0)):
            raise ValueError("effect estimates must be finite; SEs and ratio estimates must be positive")
        checked.append({**row, "estimate": estimate, "se": se})

    fitted = _synthesize_rows(checked, measure, model, "normal")
    full_tau2 = float(fitted["tau2"] or 0.0)
    full_weights = [1 / (row["se"]**2 + full_tau2) for row in checked]
    sum_full_weights = sum(full_weights)
    full_variance = 1 / sum_full_weights
    result = []
    for index, row in enumerate(checked):
        reduced = checked[:index] + checked[index + 1:]
        deleted = _synthesize_rows(reduced, measure, model, "normal")
        deleted_tau2 = float(deleted["tau2"] or 0.0)
        deleted_variance = 1 / sum(
            1 / (other["se"]**2 + deleted_tau2) for other in reduced
        )
        effect = math.log(row["estimate"]) if measure in RATIOS else row["estimate"]
        deleted_residual = effect - deleted["pooled_analysis"]
        residual_variance = row["se"]**2 + deleted_tau2 + deleted_variance
        change = fitted["pooled_analysis"] - deleted["pooled_analysis"]
        result.append({
            "measure": measure,
            "model": model,
            "study_id": row["study_id"],
            "result_id": row.get("result_id"),
            "source_key": row.get("source_key"),
            "source_locator": row.get("source_locator", ""),
            "effect_analysis": effect,
            "weight_percent": 100 * full_weights[index] / sum_full_weights,
            "hat_value": full_weights[index] / sum_full_weights,
            "deleted_residual": deleted_residual,
            "externally_standardized_residual": deleted_residual / math.sqrt(residual_variance),
            "cooks_distance": change * change / full_variance,
            "covariance_ratio": deleted_variance / full_variance,
            "leave_one_out_pooled_analysis": deleted["pooled_analysis"],
            "leave_one_out_tau2": deleted_tau2,
            "leave_one_out_q": deleted["q"],
        })
    return result
