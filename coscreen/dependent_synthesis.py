"""Fixed/equal-effects GLS synthesis for dependent outcomes within studies."""

from __future__ import annotations

import math

import numpy as np
from scipy.stats import chi2, norm

from coscreen.review_analysis import RATIOS


_Z_975 = float(norm.ppf(.975))
_CITATION_REMINDER = (
    "Report the source and assumptions for each within-study covariance matrix; "
    "cite Cheung (2015), Chapter 5, pp. 121–126 (PDF pp. 145–150), especially Eqs. 5.1–5.9."
)


def synthesize_dependent_effects(effects: list[dict], blocks: list[dict], model: str = "fixed") -> dict:
    """Fit fixed GLS or correlated-outcome random-effects REML to study blocks."""
    if model not in {"fixed", "random_reml_un"}:
        raise ValueError("model must be fixed or random_reml_un")
    if len(blocks) < 2:
        raise ValueError("at least two independent studies are required")

    effect_by_id = {int(row["result_id"]): row for row in effects}
    if len(effect_by_id) != len(effects):
        raise ValueError("duplicate saved effect result IDs")

    seen_studies: set[str] = set()
    seen_results: set[int] = set()
    rows_by_block: list[list[dict]] = []
    matrices: list[np.ndarray] = []
    snapshot_blocks: list[dict] = []
    snapshot_effects: list[dict] = []
    dimensions: tuple[str, str, str] | None = None
    outcome_order: list[str] = []
    measure = ""

    for block in blocks:
        study_id = str(block.get("study_id", "")).strip()
        result_ids = block.get("result_ids")
        note = str(block.get("covariance_source_note", "")).strip()
        if not study_id or study_id in seen_studies:
            raise ValueError("study blocks must have distinct non-empty study IDs")
        seen_studies.add(study_id)
        if not isinstance(result_ids, list) or not result_ids:
            raise ValueError(f"study {study_id} needs at least one selected result ID")
        if not note:
            raise ValueError(f"study {study_id} needs a covariance source note")
        if len(set(result_ids)) != len(result_ids) or any(result_id in seen_results for result_id in result_ids):
            raise ValueError("result IDs must be unique within and across study blocks")

        rows = []
        for result_id in result_ids:
            row = effect_by_id.get(result_id)
            if row is None:
                raise ValueError(f"effect result {result_id} does not exist in this review")
            if row["selected"] != 1:
                raise ValueError(f"effect result {result_id} is not selected")
            if row["study_id"] != study_id:
                raise ValueError(f"effect result {result_id} does not belong to study {study_id}")
            if any(existing["outcome"] == row["outcome"] for existing in rows):
                raise ValueError(f"study {study_id} has more than one selected result for outcome {row['outcome']}")
            row_dimensions = (row["comparison"], row["timepoint"], row["measure"])
            if dimensions is None:
                dimensions = row_dimensions
                measure = row["measure"]
            elif row_dimensions != dimensions:
                raise ValueError("all selected effects must share the same comparison, timepoint and measure")
            if row["outcome"] not in outcome_order:
                outcome_order.append(row["outcome"])
            estimate, se = float(row["estimate"]), float(row["se"])
            if not math.isfinite(estimate) or not math.isfinite(se) or se <= 0:
                raise ValueError(f"effect result {result_id} must have a finite estimate and positive SE")
            if measure in RATIOS and estimate <= 0:
                raise ValueError(f"effect result {result_id} must be positive for a ratio measure")
            rows.append(row)
            seen_results.add(result_id)

        try:
            covariance = np.asarray(block.get("covariance"), dtype=float)
        except (TypeError, ValueError):
            raise ValueError(f"study {study_id} covariance must be a finite square matrix") from None
        size = len(rows)
        if covariance.shape != (size, size) or not np.isfinite(covariance).all():
            raise ValueError(f"study {study_id} covariance must be a finite {size} by {size} matrix")
        if not np.allclose(covariance, covariance.T, rtol=1e-10, atol=1e-12):
            raise ValueError(f"study {study_id} covariance must be symmetric")
        covariance = (covariance + covariance.T) / 2
        for index, row in enumerate(rows):
            variance = float(row["se"]) * float(row["se"])
            if not math.isfinite(variance) or variance <= 0:
                raise ValueError(f"effect result {row['result_id']} sampling variance is outside the finite range")
            if not math.isclose(covariance[index, index], variance,
                                rel_tol=1e-6, abs_tol=1e-14):
                raise ValueError(f"study {study_id} covariance diagonal must match each selected effect SE squared")
        try:
            np.linalg.cholesky(covariance)
            sd = np.sqrt(np.diag(covariance))
            correlation = covariance / np.outer(sd, sd)
            condition = np.linalg.cond(correlation)
        except np.linalg.LinAlgError:
            raise ValueError(f"study {study_id} covariance must be positive definite") from None
        # ponytail: condition-number limit 1e12; higher precision if near-singular covariance is legitimate.
        if not math.isfinite(float(condition)) or condition > 1e12:
            raise ValueError(f"study {study_id} covariance is numerically ill-conditioned")

        rows_by_block.append(rows)
        matrices.append(covariance)
        snapshot_blocks.append({
            "study_id": study_id,
            "result_ids": list(result_ids),
            "outcomes": [row["outcome"] for row in rows],
            "covariance": covariance.tolist(),
            "covariance_source_note": note,
        })
        snapshot_effects.extend({
            "result_id": row["result_id"],
            "study_id": row["study_id"],
            "comparison": row["comparison"],
            "outcome": row["outcome"],
            "timepoint": row["timepoint"],
            "measure": row["measure"],
            "estimate": row["estimate"],
            "se": row["se"],
            "source_key": row["source_key"],
            "source_locator": row["source_locator"],
        } for row in rows)

    if len(outcome_order) < 2:
        raise ValueError("at least two distinct outcomes are required")
    if not any(len(rows) >= 2 for rows in rows_by_block):
        raise ValueError("at least one study must contribute multiple outcomes")
    study_counts = {outcome: sum(outcome in {row["outcome"] for row in rows}
                                 for rows in rows_by_block)
                    for outcome in outcome_order}
    if any(count < 2 for count in study_counts.values()):
        raise ValueError("at least two independent studies are required for every outcome")

    outcome_index = {outcome: index for index, outcome in enumerate(outcome_order)}
    n_outcomes = len(outcome_order)
    information = np.zeros((n_outcomes, n_outcomes), dtype=float)
    score = np.zeros(n_outcomes, dtype=float)
    y_by_outcome: dict[str, list[tuple[float, float]]] = {outcome: [] for outcome in outcome_order}
    n_effects = 0
    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            for rows, covariance in zip(rows_by_block, matrices):
                design = np.zeros((len(rows), n_outcomes), dtype=float)
                values = np.empty(len(rows), dtype=float)
                for index, row in enumerate(rows):
                    outcome = row["outcome"]
                    design[index, outcome_index[outcome]] = 1
                    estimate = float(row["estimate"])
                    values[index] = math.log(estimate) if measure in RATIOS else estimate
                    y_by_outcome[outcome].append((values[index], covariance[index, index]))
                weighted_design = np.linalg.solve(covariance, design)
                weighted_values = np.linalg.solve(covariance, values)
                information += design.T @ weighted_design
                score += design.T @ weighted_values
                n_effects += len(rows)
            if not np.isfinite(information).all() or not np.isfinite(score).all():
                raise FloatingPointError
            coefficient_covariance = np.linalg.inv(information)
            coefficients = np.linalg.solve(information, score)
            np.linalg.cholesky(information)
            if not np.isfinite(coefficient_covariance).all() or not np.isfinite(coefficients).all():
                raise FloatingPointError
    except (np.linalg.LinAlgError, FloatingPointError, OverflowError):
        raise ValueError("dependent synthesis is numerically unstable for the supplied estimates/covariance") from None
    if np.any(np.diag(coefficient_covariance) <= 0):
        raise ValueError("outcomes are not jointly estimable from these studies")

    q = 0.0
    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            for rows, covariance in zip(rows_by_block, matrices):
                design = np.zeros((len(rows), n_outcomes), dtype=float)
                values = np.empty(len(rows), dtype=float)
                for index, row in enumerate(rows):
                    design[index, outcome_index[row["outcome"]]] = 1
                    estimate = float(row["estimate"])
                    values[index] = math.log(estimate) if measure in RATIOS else estimate
                residual = values - design @ coefficients
                q += float(residual @ np.linalg.solve(covariance, residual))
    except (np.linalg.LinAlgError, FloatingPointError, OverflowError):
        raise ValueError("dependent synthesis residual Q is outside the finite numerical range") from None
    q_df = n_effects - n_outcomes
    if not math.isfinite(q):
        raise ValueError("dependent synthesis residual Q is outside the finite numerical range")
    q = max(0.0, q)
    estimates = []
    for index, outcome in enumerate(outcome_order):
        se = math.sqrt(float(coefficient_covariance[index, index]))
        lower, upper = coefficients[index] - _Z_975 * se, coefficients[index] + _Z_975 * se
        if not math.isfinite(se) or not math.isfinite(float(lower)) or not math.isfinite(float(upper)):
            raise ValueError("dependent synthesis confidence intervals are outside the finite numerical range")
        if measure in RATIOS:
            try:
                estimate, ci_lower, ci_upper = math.exp(coefficients[index]), math.exp(lower), math.exp(upper)
            except OverflowError:
                raise ValueError("ratio estimate or confidence limits are not representable as finite numbers") from None
            if any(not math.isfinite(value) or value <= 0 for value in (estimate, ci_lower, ci_upper)):
                raise ValueError("ratio estimate or confidence limits are not representable as finite numbers")
        else:
            estimate, ci_lower, ci_upper = coefficients[index], lower, upper
        marginal = y_by_outcome[outcome]
        try:
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                weights = np.asarray([1 / variance for _, variance in marginal])
                values = np.asarray([value for value, _ in marginal])
                marginal_mean = float(weights @ values / weights.sum())
                marginal_q = float(weights @ ((values - marginal_mean) ** 2))
        except (FloatingPointError, OverflowError):
            raise ValueError("marginal Q is outside the finite numerical range") from None
        if not math.isfinite(marginal_mean) or not math.isfinite(marginal_q):
            raise ValueError("marginal Q is outside the finite numerical range")
        marginal_df = len(marginal) - 1
        estimates.append({
            "outcome": outcome,
            "n_studies": study_counts[outcome],
            "estimate": float(estimate),
            "se": se,
            "se_scale": "log" if measure in RATIOS else "identity",
            "ci_lower": float(ci_lower),
            "ci_upper": float(ci_upper),
            "q": {"statistic": marginal_q, "df": marginal_df,
                  "p_value": float(chi2.sf(marginal_q, marginal_df)),
                  "scope": "marginal_univariate"},
        })

    result = {
        "model": "fixed_equal_effects_gls",
        "algorithm_version": 1,
        "comparison": dimensions[0],
        "timepoint": dimensions[1],
        "measure": measure,
        "analysis_scale": "log" if measure in RATIOS else "identity",
        "n_studies": len(rows_by_block),
        "n_effects": n_effects,
        "estimates": estimates,
        "coefficient_covariance": {
            "outcomes": outcome_order,
            "scale": "log" if measure in RATIOS else "identity",
            "matrix": coefficient_covariance.tolist(),
        },
        "q": {"statistic": q, "df": q_df, "p_value": float(chi2.sf(q, q_df)),
              "scope": "joint_gls_residual"},
        "input_snapshot": {"blocks": snapshot_blocks, "effects": snapshot_effects},
        "limitations": [
            "Assumes a common true effect for each outcome across studies; no between-study heterogeneity is modeled.",
            "Study blocks are independent, and supplied within-study covariance matrices are treated as known.",
            "The Q test and 95% intervals use large-sample normal/chi-square approximations.",
        ],
        "citation_reminder": _CITATION_REMINDER,
    }
    if model == "random_reml_un":
        if 5 <= len(outcome_order) <= 8:
            from coscreen.dependent_random_fiveplus import synthesize_random_reml_un_fiveplus

            return synthesize_random_reml_un_fiveplus(
                result, rows_by_block, matrices, outcome_order, measure)
        if len(outcome_order) == 4:
            from coscreen.dependent_random_many import synthesize_random_reml_un_four

            return synthesize_random_reml_un_four(
                result, rows_by_block, matrices, outcome_order, measure)
        if len(outcome_order) == 3:
            from coscreen.dependent_random_three import synthesize_random_reml_un_three

            return synthesize_random_reml_un_three(
                result, rows_by_block, matrices, outcome_order, measure)
        if len(outcome_order) != 2:
            raise ValueError("random_reml_un supports two to eight outcomes")
        from coscreen.dependent_random import synthesize_random_reml_un

        return synthesize_random_reml_un(
            result, rows_by_block, matrices, outcome_order, measure)
    return result
