"""Frequentist P-score ranking from a fitted consistency network model."""

from __future__ import annotations

import math
from numbers import Real

import numpy as np

from coscreen.network_gls import RATIOS


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise ValueError(f"{label} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def rank_network_pscores(result: dict, *, benefit: str) -> dict:
    """Rank a random-effects consistency NMA result by frequentist P-scores.

    ``benefit`` is required and says whether larger or smaller effects are
    favorable. The result must include the model's full coefficient covariance.
    """
    if not isinstance(benefit, str) or benefit not in ("higher", "lower"):
        raise ValueError("benefit must be 'higher' or 'lower'")
    if not isinstance(result, dict):
        raise ValueError("P-scores require a network_random consistency result")
    source_model = result.get("model")
    if not isinstance(source_model, str) or source_model != "random_reml_common_tau":
        raise ValueError("P-scores require a network_random consistency result")

    measure = result.get("measure")
    if not isinstance(measure, str) or measure not in RATIOS | {"RD", "MD", "SMD"}:
        raise ValueError("P-scores require a supported network effect measure")
    expected_scale = "log" if measure in RATIOS else "natural"

    treatments = result.get("treatments")
    reference = result.get("reference")
    if (not isinstance(treatments, list) or len(treatments) < 2
            or any(not isinstance(item, str) or not item.strip() for item in treatments)
            or len(set(treatments)) != len(treatments)):
        raise ValueError("network treatments must be unique non-empty labels")
    if not isinstance(reference, str) or reference not in treatments:
        raise ValueError("network reference must match one treatment label")

    covariance_result = result.get("treatment_effect_covariance")
    if not isinstance(covariance_result, dict):
        raise ValueError("full treatment_effect_covariance is required for P-scores")
    if (not isinstance(covariance_result.get("scale"), str)
            or covariance_result["scale"] != expected_scale):
        raise ValueError(f"treatment covariance scale must be {expected_scale!r}")
    coefficient_labels = covariance_result.get("treatments")
    if (not isinstance(coefficient_labels, list)
            or any(not isinstance(item, str) or not item.strip() for item in coefficient_labels)
            or len(set(coefficient_labels)) != len(coefficient_labels)
            or set(coefficient_labels) != set(treatments) - {reference}):
        raise ValueError("treatment covariance labels must match the reference-coded coefficients")

    dimension = len(coefficient_labels)
    raw_matrix = covariance_result.get("matrix")
    if (not isinstance(raw_matrix, list) or len(raw_matrix) != dimension
            or any(not isinstance(row, list) or len(row) != dimension for row in raw_matrix)):
        raise ValueError("treatment covariance must be square and match its treatment labels")
    covariance = np.asarray([
        [_finite_number(value, "treatment covariance value") for value in row]
        for row in raw_matrix
    ], dtype=float)
    magnitude = float(np.max(np.abs(covariance))) if covariance.size else 0.0
    relative_tolerance = min(
        1e-12, np.finfo(float).eps * max(1, dimension) * 100
    )
    tolerance = max(magnitude * relative_tolerance, np.finfo(float).tiny)
    if not np.allclose(covariance, covariance.T, rtol=1e-10, atol=tolerance):
        raise ValueError("treatment covariance must be symmetric positive semidefinite")
    covariance = (covariance + covariance.T) / 2
    try:
        eigenvalues = np.linalg.eigvalsh(covariance)
    except np.linalg.LinAlgError as exc:
        raise ValueError("treatment covariance must be symmetric positive semidefinite") from exc
    if not np.isfinite(eigenvalues).all() or float(eigenvalues[0]) < -tolerance:
        raise ValueError("treatment covariance must be symmetric positive semidefinite")

    coefficients = {reference: 0.0}
    pairwise = result.get("estimates")
    if not isinstance(pairwise, list):
        raise ValueError("complete pairwise network estimates are required")
    expected_pairs = {
        frozenset((left, right))
        for index, left in enumerate(treatments)
        for right in treatments[index + 1:]
    }
    seen_pairs = set()
    pair_effects = []
    for row in pairwise:
        if not isinstance(row, dict):
            raise ValueError("each pairwise network estimate must be an object")
        treatment, comparator = row.get("treatment"), row.get("comparator")
        if (not isinstance(treatment, str) or not isinstance(comparator, str)
                or treatment not in treatments or comparator not in treatments
                or treatment == comparator):
            raise ValueError("pairwise estimate labels must match distinct network treatments")
        pair = frozenset((treatment, comparator))
        if pair in seen_pairs:
            raise ValueError("pairwise network estimates must be unique")
        seen_pairs.add(pair)
        estimate = _finite_number(row.get("estimate"), "pairwise estimate")
        if measure in RATIOS:
            if estimate <= 0:
                raise ValueError("ratio estimates must be positive")
            estimate = math.log(estimate)
        pair_effects.append((treatment, comparator, estimate))
        if treatment == reference:
            coefficients[comparator] = -estimate
        elif comparator == reference:
            coefficients[treatment] = estimate

    if seen_pairs != expected_pairs:
        raise ValueError("complete pairwise network estimates are required")
    if set(coefficients) != set(treatments):
        raise ValueError("reference-coded treatment effects could not be recovered")
    for treatment, comparator, estimate in pair_effects:
        difference = coefficients[treatment] - coefficients[comparator]
        if (not math.isfinite(difference)
                or not math.isclose(estimate, difference, rel_tol=1e-10, abs_tol=1e-12)):
            raise ValueError("pairwise estimates must agree with reference-coded treatment effects")

    coefficient_index = {label: index for index, label in enumerate(coefficient_labels)}
    scores: dict[str, list[float]] = {label: [] for label in treatments}
    direction = 1.0 if benefit == "higher" else -1.0
    for index, left in enumerate(treatments):
        for right in treatments[index + 1:]:
            difference = coefficients[left] - coefficients[right]
            if not math.isfinite(difference):
                raise ValueError("pairwise network effect differences must be finite")
            left_index = coefficient_index.get(left)
            right_index = coefficient_index.get(right)
            try:
                with np.errstate(over="raise", invalid="raise"):
                    variance = (
                        (covariance[left_index, left_index] if left_index is not None else 0.0)
                        + (covariance[right_index, right_index] if right_index is not None else 0.0)
                        - (2 * covariance[left_index, right_index]
                           if left_index is not None and right_index is not None else 0.0)
                    )
            except FloatingPointError as exc:
                raise ValueError("pairwise network variance must remain finite") from exc
            if not math.isfinite(float(variance)):
                raise ValueError("pairwise network variance must remain finite")
            if variance < -tolerance:
                raise ValueError("treatment covariance implies a negative pairwise variance")
            variance = max(0.0, float(variance))
            favored_difference = direction * difference
            if variance == 0.0:
                probability = 1.0 if favored_difference > 0 else (
                    0.0 if favored_difference < 0 else 0.5
                )
            else:
                z = favored_difference / math.sqrt(variance)
                probability = 0.5 * math.erfc(-z / math.sqrt(2.0))
            scores[left].append(probability)
            scores[right].append(1.0 - probability)

    ordered = sorted(
        ((label, math.fsum(values) / (len(treatments) - 1))
         for label, values in scores.items()),
        key=lambda item: -item[1],
    )
    ranking = []
    rank = 0
    previous_score = None
    for ordinal, (treatment, score) in enumerate(ordered, start=1):
        if previous_score is None or not math.isclose(
            score, previous_score, rel_tol=0.0, abs_tol=1e-12
        ):
            rank = ordinal
        ranking.append({"rank": rank, "treatment": treatment, "p_score": score})
        previous_score = score

    return {
        "method": "frequentist P-score",
        "source_model": source_model,
        "measure": measure,
        "scale": expected_scale,
        "benefit": benefit,
        "ranking": ranking,
        "warnings": [
            "P-scores summarize pairwise normal-model certainty; they are not probabilities of being best and do not establish transitivity, clinical superiority, or clinical importance."
        ],
    }
