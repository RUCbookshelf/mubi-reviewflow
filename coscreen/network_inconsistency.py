"""Fixed-effect between-design Q test for network inconsistency."""

from __future__ import annotations

import math

import numpy as np
from scipy.stats import chi2

from coscreen.network_gls import RATIOS, synthesize_network, validate_study


def _fit_q(blocks: list[tuple[np.ndarray, np.ndarray, np.ndarray]], rank: int) -> float:
    """Fit block GLS and return its residual Q without stacking covariances."""
    information = np.zeros((rank, rank), dtype=float)
    score = np.zeros(rank, dtype=float)
    for design, values, covariance in blocks:
        weighted_design = np.linalg.solve(covariance, design)
        weighted_values = np.linalg.solve(covariance, values)
        information += design.T @ weighted_design
        score += design.T @ weighted_values

    coefficients = np.linalg.solve(information, score)
    q = 0.0
    for design, values, covariance in blocks:
        residual = values - design @ coefficients
        q += float(residual @ np.linalg.solve(covariance, residual))
    if not math.isfinite(q):
        raise ValueError("network inconsistency statistic must remain finite")
    return max(0.0, q)


def assess_network_inconsistency(studies: list[dict], measure: str) -> dict:
    """Decompose fixed-effect network Q into within- and between-design parts.

    Each study is an independent covariance block in the same input format as
    ``synthesize_network``. The study's design is inferred from its contrast
    treatment labels. A chi-square between-design test is returned only when
    the design-specific model adds estimable degrees of freedom.
    """
    if not isinstance(studies, list) or not studies:
        raise ValueError("studies must be a non-empty list")
    validate_study(studies[0], measure)
    reference = studies[0]["contrasts"][0]["treatment"].strip()
    network = synthesize_network(studies, measure, reference)

    nodes = network["treatments"]
    common_columns = [node for node in nodes if node != reference]
    parsed = []
    study_ids = []
    design_studies: dict[tuple[str, ...], list[str]] = {}
    design_contrasts: dict[tuple[str, ...], int] = {}
    for study in studies:
        study_id = study["study_id"]
        contrasts = study["contrasts"]
        treatments = tuple(sorted({
            arm.strip()
            for contrast in contrasts
            for arm in (contrast["treatment"], contrast["comparator"])
        }))
        columns = treatments[1:]
        design = np.asarray([
            [int(row["treatment"].strip() == node)
             - int(row["comparator"].strip() == node) for node in columns]
            for row in contrasts
        ], dtype=float)
        common_design = np.asarray([
            [int(row["treatment"].strip() == node)
             - int(row["comparator"].strip() == node) for node in common_columns]
            for row in contrasts
        ], dtype=float)
        values = np.asarray([
            math.log(float(row["estimate"])) if measure in RATIOS
            else float(row["estimate"])
            for row in contrasts
        ], dtype=float)
        covariance = np.asarray(study["covariance"], dtype=float)
        parsed.append((study_id, treatments, design, common_design, values, covariance))
        study_ids.append(study_id)
        design_studies.setdefault(treatments, []).append(study_id)
        design_contrasts[treatments] = design_contrasts.get(treatments, 0) + len(contrasts)

    designs = sorted(design_studies)
    offsets = {}
    alternative_columns = 0
    for treatments in designs:
        offsets[treatments] = alternative_columns
        alternative_columns += len(treatments) - 1

    common_blocks = []
    alternative_blocks = []
    stacked_common = []
    stacked_alternative = []
    for _, treatments, design, common_design, values, covariance in parsed:
        alternative_design = np.zeros((len(values), alternative_columns), dtype=float)
        offset = offsets[treatments]
        alternative_design[:, offset:offset + design.shape[1]] = design
        common_blocks.append((common_design, values, covariance))
        alternative_blocks.append((alternative_design, values, covariance))
        stacked_common.append(common_design)
        stacked_alternative.append(alternative_design)

    common_rank = int(np.linalg.matrix_rank(np.vstack(stacked_common)))
    fitted_common_rank = len(common_columns)
    if common_rank != fitted_common_rank:
        raise ValueError("network treatment effects are not identifiable")
    alternative_rank = int(np.linalg.matrix_rank(np.vstack(stacked_alternative)))
    if alternative_rank != alternative_columns:
        raise ValueError("design-specific network effects are not identifiable")

    q_total = _fit_q(common_blocks, common_rank)
    q_within = _fit_q(alternative_blocks, alternative_rank)
    n_contrasts = sum(len(block[1]) for block in common_blocks)
    df_total = n_contrasts - common_rank
    df_within = n_contrasts - alternative_rank
    df_between = alternative_rank - common_rank
    q_between = q_total - q_within
    tolerance = 1e-9 * max(1.0, q_total, q_within)
    if q_between < -tolerance:
        raise ValueError("design-specific residual Q exceeded total network Q")
    q_between = max(0.0, q_between)

    estimable = df_between > 0
    result = {
        "method": "fixed-effect between-design Q decomposition",
        "test": "global between-design inconsistency",
        "status": "available" if estimable else "not_estimable",
        "measure": measure,
        "n_studies": len(parsed),
        "n_contrasts": n_contrasts,
        "study_ids": study_ids,
        "designs": [
            {"treatments": list(treatments),
             "study_ids": design_studies[treatments],
             "n_studies": len(design_studies[treatments]),
             "n_contrasts": design_contrasts[treatments]}
            for treatments in designs
        ],
        "q_total": q_total,
        "df_total": df_total,
        "q_within_designs": q_within,
        "df_within_designs": df_within,
        "q_between_designs": q_between,
        "df_between_designs": df_between,
        "p_value": float(chi2.sf(q_between, df_between)) if estimable else None,
        "reason": None if estimable else (
            "The design-specific model adds no estimable degrees of freedom, so "
            "between-design inconsistency cannot be tested for this network."
        ),
        "warnings": [
            "This fixed-effect test does not assess transitivity or effect-modifier comparability.",
            "Studies are independent blocks; each supplied within-study covariance matrix is used as given.",
            "The design is inferred from treatment labels present in each study's contrasts.",
        ],
    }
    if result["p_value"] is not None and not math.isfinite(result["p_value"]):
        raise ValueError("network inconsistency p-value must remain finite")
    return result
