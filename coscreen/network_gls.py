"""Fixed-effect network GLS for studies with supplied contrast covariances."""

from __future__ import annotations

import math
from numbers import Real
from typing import Mapping

import numpy as np
from scipy.stats import chi2

RATIOS = {"RR", "OR"}
MEASURES = RATIOS | {"RD", "MD", "SMD"}


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


def _require_finite(value: object, label: str) -> None:
    if not np.all(np.isfinite(value)):
        raise ValueError(f"{label} must remain finite")


def _parse_study(study: dict, measure: str) -> tuple[str, list[tuple[str, str, float]], np.ndarray]:
    if not isinstance(study, dict):
        raise ValueError("each study must be an object")
    study_id = study.get("study_id")
    if not isinstance(study_id, str) or not study_id.strip():
        raise ValueError("study_id must be a non-empty string")
    expected_scale = "log" if measure in RATIOS else "natural"
    if study.get("covariance_scale") != expected_scale:
        raise ValueError(f"{measure} covariance_scale must be exactly {expected_scale!r}")

    raw_contrasts = study.get("contrasts")
    if not isinstance(raw_contrasts, list) or not raw_contrasts:
        raise ValueError("contrasts must be a non-empty list")
    contrasts = []
    treatments = set()
    for index, item in enumerate(raw_contrasts):
        if not isinstance(item, Mapping):
            raise ValueError("each contrast must be an object")
        treatment, comparator = item.get("treatment"), item.get("comparator")
        if not isinstance(treatment, str) or not treatment.strip() or \
           not isinstance(comparator, str) or not comparator.strip():
            raise ValueError("contrast treatments must be non-empty strings")
        treatment, comparator = treatment.strip(), comparator.strip()
        if treatment == comparator:
            raise ValueError("a contrast must compare different treatments")
        estimate = _finite_number(item.get("estimate"), f"contrast {index} estimate")
        if measure in RATIOS and estimate <= 0:
            raise ValueError("ratio estimates must be positive")
        contrasts.append((treatment, comparator, estimate))
        treatments.update((treatment, comparator))

    m = len(contrasts)
    if m > len(treatments) - 1:
        raise ValueError("a study may have at most treatments minus one independent contrasts")
    local_nodes = sorted(treatments)
    local_columns = local_nodes[1:]
    local_design = np.asarray(
        [[int(a == node) - int(b == node) for node in local_columns]
         for a, b, _ in contrasts], dtype=float)
    if np.linalg.matrix_rank(local_design) != m:
        raise ValueError("contrasts must be linearly independent within each study")

    raw_covariance = study.get("covariance")
    if not isinstance(raw_covariance, list) or len(raw_covariance) != m or \
       any(not isinstance(row, list) or len(row) != m for row in raw_covariance):
        raise ValueError("covariance must be a square matrix matching the contrast order")
    covariance = np.asarray(
        [[_finite_number(value, "covariance value") for value in row]
         for row in raw_covariance], dtype=float)
    if not np.allclose(covariance, covariance.T, rtol=1e-10, atol=1e-12):
        raise ValueError("covariance must be symmetric positive definite")
    covariance = covariance / 2 + covariance.T / 2
    _require_finite(covariance, "covariance")
    try:
        factor = np.linalg.cholesky(covariance)
    except (FloatingPointError, np.linalg.LinAlgError) as exc:
        raise ValueError("covariance must be symmetric positive definite") from exc
    _require_finite(factor, "covariance factor")
    return study_id, contrasts, covariance


def validate_study(study: dict, measure: str) -> None:
    """Validate one study and its analysis-scale contrast covariance."""
    if not isinstance(measure, str) or measure not in MEASURES:
        raise ValueError("network synthesis supports RR, OR, RD, MD or SMD")
    _parse_study(study, measure)


def synthesize_network(studies: list[dict], measure: str, reference: str) -> dict:
    """Fit a fixed-effect network model using study-level GLS covariance blocks.

    Ratio estimates are supplied on their natural positive scale and returned on
    that scale; their standard errors remain on the log scale. Covariances for
    ratios must already be on the log scale.
    """
    if not isinstance(measure, str) or measure not in MEASURES:
        raise ValueError("network synthesis supports RR, OR, RD, MD or SMD")
    if not isinstance(studies, list) or not studies:
        raise ValueError("studies must be a non-empty list")
    if not isinstance(reference, str) or not reference.strip():
        raise ValueError("reference must be a non-empty treatment name")
    reference = reference.strip()

    parsed = [_parse_study(study, measure) for study in studies]
    study_ids = [item[0] for item in parsed]
    if len(study_ids) != len(set(study_ids)):
        raise ValueError("study_id values must be unique")
    nodes = sorted({node for _, contrasts, _ in parsed
                    for a, b, _ in contrasts for node in (a, b)})
    if len(nodes) < 3 or reference not in nodes:
        raise ValueError("network needs at least three treatments and a present reference")

    adjacency = {node: set() for node in nodes}
    for _, contrasts, _ in parsed:
        for a, b, _ in contrasts:
            adjacency[a].add(b)
            adjacency[b].add(a)
    reached, frontier = set(), [reference]
    while frontier:
        node = frontier.pop()
        if node not in reached:
            reached.add(node)
            frontier.extend(adjacency[node] - reached)
    if reached != set(nodes):
        raise ValueError("treatment network is disconnected")

    columns = [node for node in nodes if node != reference]
    p = len(columns)
    information = np.zeros((p, p), dtype=float)
    score = np.zeros(p, dtype=float)
    blocks = []
    for _, contrasts, covariance in parsed:
        design = np.asarray(
            [[int(a == node) - int(b == node) for node in columns]
             for a, b, _ in contrasts], dtype=float)
        values = np.asarray([math.log(value) if measure in RATIOS else value
                             for _, _, value in contrasts], dtype=float)
        try:
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                weighted_design = np.linalg.solve(covariance, design)
                weighted_values = np.linalg.solve(covariance, values)
        except np.linalg.LinAlgError as exc:
            raise ValueError("study covariance could not be solved numerically") from exc
        except FloatingPointError as exc:
            raise ValueError("study covariance solve overflowed") from exc
        _require_finite(weighted_design, "weighted design")
        _require_finite(weighted_values, "weighted estimates")
        try:
            with np.errstate(over="raise", invalid="raise"):
                information += design.T @ weighted_design
                score += design.T @ weighted_values
        except FloatingPointError as exc:
            raise ValueError("network information overflowed") from exc
        _require_finite(information, "network information")
        _require_finite(score, "network score")
        blocks.append((design, values, covariance))

    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            parameter_covariance = np.linalg.solve(information, np.eye(p))
            effects = np.linalg.solve(information, score)
    except np.linalg.LinAlgError as exc:
        raise ValueError("network is not numerically estimable") from exc
    except FloatingPointError as exc:
        raise ValueError("network solution overflowed") from exc
    _require_finite(parameter_covariance, "network covariance")
    _require_finite(effects, "network effects")

    residual_q = 0.0
    n_contrasts = 0
    for design, values, covariance in blocks:
        try:
            with np.errstate(over="raise", invalid="raise"):
                residual = values - design @ effects
        except FloatingPointError as exc:
            raise ValueError("network residual overflowed") from exc
        _require_finite(residual, "network residual")
        try:
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                q_block = float(residual @ np.linalg.solve(covariance, residual))
        except np.linalg.LinAlgError as exc:
            raise ValueError("study covariance could not be solved numerically") from exc
        except FloatingPointError as exc:
            raise ValueError("residual Q overflowed") from exc
        _require_finite(q_block, "residual Q")
        with np.errstate(over="raise", invalid="raise"):
            residual_q += q_block
        _require_finite(residual_q, "residual Q")
        n_contrasts += len(values)
    residual_q = max(0.0, residual_q)
    residual_df = n_contrasts - p
    estimates = []
    for i, treatment in enumerate(nodes):
        for comparator in nodes[i + 1:]:
            contrast = np.asarray([int(treatment == node) - int(comparator == node)
                                   for node in columns], dtype=float)
            try:
                with np.errstate(over="raise", invalid="raise"):
                    effect = float(contrast @ effects)
                    variance = float(contrast @ parameter_covariance @ contrast)
            except FloatingPointError as exc:
                raise ValueError("pairwise estimate or variance overflowed") from exc
            _require_finite((effect, variance), "pairwise estimate and variance")
            if variance <= 0:
                raise ValueError("pairwise standard error must be positive and finite")
            se = math.sqrt(variance)
            if not math.isfinite(se) or se <= 0:
                raise ValueError("pairwise standard error must be positive and finite")
            low, high = effect - 1.96 * se, effect + 1.96 * se
            _require_finite((low, high), "pairwise confidence interval")
            try:
                if measure in RATIOS:
                    estimate, ci_low, ci_high = math.exp(effect), math.exp(low), math.exp(high)
                else:
                    estimate, ci_low, ci_high = effect, low, high
            except OverflowError as exc:
                raise ValueError("network result overflows on the ratio scale") from exc
            _require_finite((estimate, ci_low, ci_high), "pairwise result")
            if measure in RATIOS and min(estimate, ci_low, ci_high) <= 0:
                raise ValueError("ratio estimates and intervals must remain positive and finite")
            estimates.append({"treatment": treatment, "comparator": comparator,
                              "estimate": estimate, "se": se,
                              "se_scale": "log" if measure in RATIOS else "natural",
                              "ci_low": ci_low, "ci_high": ci_high})

    residual_p = float(chi2.sf(residual_q, residual_df)) if residual_df > 0 else None
    if residual_p is not None:
        _require_finite(residual_p, "residual p-value")
    return {
        "method": "fixed-effect contrast-based network generalized least squares",
        "measure": measure,
        "reference": reference,
        "treatments": nodes,
        "n_studies": len(parsed),
        "n_contrasts": n_contrasts,
        "estimates": estimates,
        "residual_q": residual_q,
        "residual_df": residual_df,
        "residual_p": residual_p,
        "warnings": [
            "Requires transitivity and comparable effect modifiers; this model does not assess either assumption.",
            "Residual Q is total fixed-effect lack of fit, combining within-design heterogeneity and possible inconsistency; it is not a formal inconsistency test.",
            "Study blocks are assumed independent, and each supplied covariance matrix is used on its declared analysis scale.",
        ],
    }
