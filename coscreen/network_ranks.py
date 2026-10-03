"""Monte Carlo SUCRA, p-best, and rankogram ranking from a fitted network model.

Resamples the reference-coded treatment effects of an existing
``network_random`` consistency result from their fitted multivariate normal
distribution ``N(coefficients, treatment_effect_covariance)`` and summarizes
the induced rank distribution (Salanti et al. 2011).
"""

from __future__ import annotations

import copy
import math
from numbers import Integral, Real

import numpy as np

from coscreen.network_gls import RATIOS
from coscreen.network_pscores import rank_network_pscores


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


def _validated_options(n_sim: object, seed: object, benefit: object) -> tuple[int, int, str]:
    if isinstance(n_sim, (bool, np.bool_)) or not isinstance(n_sim, Integral):
        raise ValueError("n_sim must be an integer of at least 2")
    n_sim = int(n_sim)
    if n_sim < 2:
        raise ValueError("n_sim must be an integer of at least 2")
    if isinstance(seed, (bool, np.bool_)) or not isinstance(seed, Integral):
        raise ValueError("seed must be a non-negative integer")
    seed = int(seed)
    if seed < 0:
        raise ValueError("seed must be a non-negative integer")
    if not isinstance(benefit, str) or benefit not in ("higher", "lower"):
        raise ValueError("benefit must be 'higher' or 'lower'")
    return n_sim, seed, benefit


def _validated_model(result: object) -> tuple:
    """Validate the network result and return (treatments, reference, measure, scale)."""
    if not isinstance(result, dict):
        raise ValueError("ranking requires a network_random consistency result")
    source_model = result.get("model")
    if not isinstance(source_model, str) or source_model != "random_reml_common_tau":
        raise ValueError("ranking requires a network_random consistency result")

    measure = result.get("measure")
    if not isinstance(measure, str) or measure not in RATIOS | {"RD", "MD", "SMD"}:
        raise ValueError("ranking requires a supported network effect measure")
    expected_scale = "log" if measure in RATIOS else "natural"

    treatments = result.get("treatments")
    reference = result.get("reference")
    if not isinstance(treatments, list) or len(treatments) < 2:
        raise ValueError("network ranking requires at least two treatments")
    if (any(not isinstance(item, str) or not item.strip() for item in treatments)
            or len(set(treatments)) != len(treatments)):
        raise ValueError("network treatments must be unique non-empty labels")
    if not isinstance(reference, str) or reference not in treatments:
        raise ValueError("network reference must match one treatment label")
    return treatments, reference, measure, expected_scale


def _validated_covariance(result: object, treatments: list[str],
                          reference: str, expected_scale: str) -> np.ndarray:
    covariance_result = result.get("treatment_effect_covariance")
    if not isinstance(covariance_result, dict):
        raise ValueError("full treatment_effect_covariance is required for ranking")
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
        eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    except np.linalg.LinAlgError as exc:
        raise ValueError("treatment covariance must be symmetric positive semidefinite") from exc
    if not np.isfinite(eigenvalues).all() or float(eigenvalues[0]) < -tolerance:
        raise ValueError("treatment covariance must be symmetric positive semidefinite")
    # Rebuild with clipped eigenvalues so sampling stays inside the PSD cone the
    # validation just accepted; the change is below the numerical tolerance.
    return (eigenvectors * np.clip(eigenvalues, 0.0, None)) @ eigenvectors.T


def _validated_coefficients(result: object, treatments: list[str],
                            reference: str, measure: str) -> dict[str, float]:
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
    return coefficients


def network_ranks(result: dict, *, n_sim: int = 100000, seed: int = 20260925,
                  benefit: str = "higher") -> dict:
    """Rank treatments of a random-effects consistency NMA result by SUCRA.

    Draws ``n_sim`` samples from the fitted multivariate normal
    ``N(coefficients, treatment_effect_covariance)`` of the result (reference
    effect fixed at zero, full covariance including its correlations), ranks the
    treatments within every sample (rank 1 = best under ``benefit``), and
    summarizes the resulting rank distribution per treatment:

    - ``rankogram``: n-vector of rank frequencies, entry ``r - 1`` counts how
      often the treatment took rank ``r`` (rank 1 = best);
    - ``p_best``: relative frequency of rank 1;
    - ``sucra``: SUCRA = sum(cumulative probability of rank <= r for
      r = 1..n-1) / (n - 1) (Salanti et al. 2011), equivalently
      ``(n - mean_rank) / (n - 1)``.

    The result also carries a deterministic P-score comparison (P-scores are
    the closed-form frequentist analogue of SUCRA, close but not identical),
    Monte Carlo standard errors, and an ``input_data`` deep copy. Ranking
    conditions on the fitted heterogeneity variance and does not propagate its
    estimation uncertainty. ``n_sim`` must be at least 2 so the reported
    Monte Carlo standard errors stay defined.
    """
    n_sim, seed, benefit = _validated_options(n_sim, seed, benefit)
    treatments, reference, measure, expected_scale = _validated_model(result)
    covariance = _validated_covariance(result, treatments, reference, expected_scale)
    coefficients = _validated_coefficients(result, treatments, reference, measure)

    coefficient_labels = result["treatment_effect_covariance"]["treatments"]
    coefficient_index = {label: index for index, label in enumerate(coefficient_labels)}
    count = len(treatments)
    reference_index = treatments.index(reference)
    mean_vector = np.zeros(count, dtype=float)
    sampling_matrix = np.zeros((count, count), dtype=float)
    for index, treatment in enumerate(treatments):
        if treatment == reference:
            continue
        source = coefficient_index[treatment]
        mean_vector[index] = coefficients[treatment]
        for other in range(count):
            if treatments[other] == reference:
                continue
            sampling_matrix[index, other] = covariance[source, coefficient_index[treatments[other]]]

    rng = np.random.default_rng(seed)
    samples = rng.multivariate_normal(
        mean_vector, sampling_matrix, size=n_sim, check_valid="ignore", method="svd",
    )
    samples[:, reference_index] = 0.0
    # Larger sampled effect is better for benefit="higher"; negating the
    # samples lets one descending rank definition serve both directions.
    direction = -1.0 if benefit == "lower" else 1.0
    order = np.argsort(-direction * samples, axis=1, kind="stable")
    ranks = np.empty_like(order)
    np.put_along_axis(ranks, order, np.arange(1, count + 1), axis=1)

    rank_values = np.arange(1, count + 1)
    frequency = (ranks[:, :, None] == rank_values[None, None, :]).sum(axis=0)
    probability = frequency / float(n_sim)
    cumulative = np.cumsum(probability, axis=1)
    sucra_values = cumulative[:, :-1].sum(axis=1) / (count - 1)
    p_best_values = probability[:, 0]
    mean_rank_values = probability @ rank_values
    sucra_se_values = ranks.std(axis=0, ddof=1) / ((count - 1) * math.sqrt(n_sim))
    p_best_se_values = np.sqrt(p_best_values * (1.0 - p_best_values) / n_sim)

    p_score_result = rank_network_pscores(result, benefit=benefit)
    p_scores = {row["treatment"]: row["p_score"] for row in p_score_result["ranking"]}

    rows = []
    for index, treatment in enumerate(treatments):
        rows.append({
            "treatment": treatment,
            "sucra": float(sucra_values[index]),
            "p_best": float(p_best_values[index]),
            "mean_rank": float(mean_rank_values[index]),
            "rankogram": [int(value) for value in frequency[index]],
        })
    rows.sort(key=lambda row: (-row["sucra"], row["treatment"]))
    rank = 0
    previous_sucra = None
    for ordinal, row in enumerate(rows, start=1):
        if previous_sucra is None or not math.isclose(
            row["sucra"], previous_sucra, rel_tol=0.0, abs_tol=1e-12
        ):
            rank = ordinal
        row["rank"] = rank
        previous_sucra = row["sucra"]

    max_difference = max(abs(row["sucra"] - p_scores[row["treatment"]]) for row in rows)
    max_sucra_se = max(float(value) for value in sucra_se_values)
    max_p_best_se = max(float(value) for value in p_best_se_values)
    warnings = [
        f"Monte Carlo ranking with n_sim={n_sim} leaves estimated standard errors up to "
        f"{max_sucra_se:.3g} for SUCRA and {max_p_best_se:.3g} for p-best; "
        "rank summaries remain subject to simulation error that shrinks as 1/sqrt(n_sim).",
        "SUCRA and p-best are relative model-based ranking summaries; the top-ranked "
        "treatment is not necessarily the best choice and the ranking does not establish "
        "clinical superiority or importance.",
        "netmeta::rankogram / netrank(method=\"SUCRA\") resample independent normal effects "
        "from the diagonal of the fitted covariance and pair them with netmeta's own "
        "tau-squared convention; this module resamples the full reference-coded covariance "
        "of network_random, so the two implementations coincide only up to Monte Carlo "
        "error when the fitted correlations are negligible.",
        "Ranking conditions on the fitted common tau-squared and the normal approximation, "
        "does not propagate heterogeneity estimation uncertainty, and inherits the "
        "consistency model's transitivity assumptions.",
    ]

    return {
        "method": "Monte Carlo SUCRA with rankogram (multivariate normal resampling)",
        "source_model": "random_reml_common_tau",
        "measure": measure,
        "scale": expected_scale,
        "benefit": benefit,
        "n_sim": n_sim,
        "seed": seed,
        "reference": reference,
        "treatments": list(treatments),
        "ranking": rows,
        "p_score_consistency": {
            "p_scores": p_scores,
            "max_abs_sucra_minus_p_score": float(max_difference),
            "note": (
                "Under the same fitted multivariate normal distribution, P-scores equal "
                "expected SUCRA when pairwise ties have probability zero; the reported "
                "difference is finite-simulation Monte Carlo error. With degenerate "
                "deterministic ties, sampled ranks break ties by treatment order, while "
                "P-scores assign a 0.5 tie probability."
            ),
        },
        "mc_error": {
            "sucra_se": {
                treatment: float(sucra_se_values[index])
                for index, treatment in enumerate(treatments)
            },
            "p_best_se": {
                treatment: float(p_best_se_values[index])
                for index, treatment in enumerate(treatments)
            },
        },
        "input_data": copy.deepcopy(result),
        "warnings": warnings,
    }
