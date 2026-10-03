"""Random-effects design-by-treatment inconsistency for network meta-analysis."""

from __future__ import annotations

import copy
import math
from numbers import Real

import numpy as np
from scipy.stats import chi2

from coscreen.network_gls import RATIOS, synthesize_network, validate_study
from coscreen.network_random import synthesize_network_random


def _finite_preset_tau2(value: object) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise ValueError("tau2 must be a finite non-negative number")
    try:
        tau2 = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError("tau2 must be a finite non-negative number") from exc
    if not math.isfinite(tau2) or tau2 < 0:
        raise ValueError("tau2 must be a finite non-negative number")
    return tau2


def _parse_blocks(studies: list[dict], measure: str, common_columns: list[str]) -> list[dict]:
    blocks = []
    for study in studies:
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
        arms = sorted({arm for row in contrasts
                       for arm in (row["treatment"].strip(), row["comparator"].strip())})
        incidence = np.asarray([
            [int(row["treatment"].strip() == arm) - int(row["comparator"].strip() == arm)
             for arm in arms]
            for row in contrasts
        ], dtype=float)
        heterogeneity = 0.5 * incidence @ incidence.T
        row_keys = []
        row_directions = []
        for row in contrasts:
            pair = tuple(sorted((
                row["treatment"].strip(), row["comparator"].strip())))
            row_keys.append((treatments, pair))
            row_directions.append(1.0 if row["treatment"].strip() == pair[1] else -1.0)
        blocks.append({
            "study_id": study["study_id"],
            "treatments": treatments,
            "design": design,
            "common_design": common_design,
            "values": values,
            "covariance": covariance,
            "heterogeneity": heterogeneity,
            "row_keys": row_keys,
            "row_directions": row_directions,
        })
    return blocks


def _alternative_design(block: dict, offsets: dict, alternative_columns: int) -> np.ndarray:
    design = np.zeros((len(block["values"]), alternative_columns), dtype=float)
    offset = offsets[block["treatments"]]
    width = block["design"].shape[1]
    design[:, offset:offset + width] = block["design"]
    return design


def _marginal_blocks(blocks: list[dict], design_key: str, tau2: float) -> list[tuple]:
    fit_blocks = []
    for block in blocks:
        try:
            with np.errstate(over="raise", invalid="raise"):
                marginal = block["covariance"] + tau2 * block["heterogeneity"]
        except (FloatingPointError, OverflowError):
            raise ValueError(
                "random-effects marginal covariance is outside the finite numerical range"
            ) from None
        sign, logdet = np.linalg.slogdet(marginal)
        if sign <= 0 or not math.isfinite(float(logdet)):
            raise ValueError("random-effects marginal covariance must remain positive definite")
        fit_blocks.append((block[design_key], block["values"], marginal))
    return fit_blocks


def _gls_residual_q(fit_blocks: list[tuple]) -> float:
    width = fit_blocks[0][0].shape[1]
    information = np.zeros((width, width), dtype=float)
    score = np.zeros(width, dtype=float)
    for design, values, marginal in fit_blocks:
        weighted_design = np.linalg.solve(marginal, design)
        weighted_values = np.linalg.solve(marginal, values)
        information += design.T @ weighted_design
        score += design.T @ weighted_values
    coefficients = np.linalg.solve(information, score)
    q = 0.0
    for design, values, marginal in fit_blocks:
        residual = values - design @ coefficients
        q += float(residual @ np.linalg.solve(marginal, residual))
    if not math.isfinite(q):
        raise ValueError("random-effects Q statistic must remain finite")
    return max(0.0, q)


def _within_designs_moment(blocks: list[dict]) -> dict:
    """DerSimonian-Laird moment tau-squared from within-designs heterogeneity only.

    Mirrors the netmeta ``tau.within`` construction: a saturated
    (design, comparison) level fit under fixed-effect weights provides the
    within-designs Q, and the moment denominator contrasts the total
    heterogeneity trace with the trace lost to fitting the pooled effects.
    """
    keys = sorted({key for block in blocks for key in block["row_keys"]})
    index = {key: position for position, key in enumerate(keys)}
    information = np.zeros((len(keys), len(keys)), dtype=float)
    score = np.zeros(len(keys), dtype=float)
    cross = np.zeros((len(keys), len(keys)), dtype=float)
    trace = 0.0
    q = 0.0
    n_contrasts = 0
    for block in blocks:
        count = len(block["values"])
        design = np.zeros((count, len(keys)), dtype=float)
        for row, key in enumerate(block["row_keys"]):
            design[row, index[key]] = block["row_directions"][row]
        covariance = block["covariance"]
        try:
            weighted_design = np.linalg.solve(covariance, design)
            weighted_values = np.linalg.solve(covariance, block["values"])
            weighted_heterogeneity = np.linalg.solve(covariance, block["heterogeneity"])
        except np.linalg.LinAlgError as exc:
            raise ValueError("study covariance could not be solved numerically") from exc
        information += design.T @ weighted_design
        score += design.T @ weighted_values
        cross += weighted_design.T @ block["heterogeneity"] @ weighted_design
        trace += float(np.trace(weighted_heterogeneity))
        n_contrasts += count
    df = n_contrasts - len(keys)
    coefficients = np.linalg.solve(information, score)
    for block in blocks:
        count = len(block["values"])
        design = np.zeros((count, len(keys)), dtype=float)
        for row, key in enumerate(block["row_keys"]):
            design[row, index[key]] = block["row_directions"][row]
        residual = block["values"] - design @ coefficients
        q += float(residual @ np.linalg.solve(block["covariance"], residual))
    if not math.isfinite(q):
        raise ValueError("within-designs Q statistic must remain finite")
    q = max(0.0, q)
    denominator = trace - float(np.trace(np.linalg.solve(information, cross)))
    estimable = math.isfinite(denominator) and denominator > 1e-12 * max(1.0, trace)
    tau2 = max(0.0, (q - df) / denominator) if estimable and df > 0 else 0.0
    return {"tau2": float(tau2), "q_within": q, "df": df, "estimable": bool(estimable)}


def assess_network_random_inconsistency(
    studies: list[dict], measure: str, reference: str, *, tau2: float | None = None
) -> dict:
    """Decompose the random-effects network Q into within- and between-design parts.

    Studies use the same contrast-block schema as
    ``coscreen.network_random.synthesize_network_random``. Between-study
    variation is one common contrast-level ``tau²`` under the project's
    independent arm-deviation structure, so each study's marginal covariance is
    ``M_i = V_i + tau² * 0.5 * A_i A_i'``. With those marginal weights the total
    consistency-model residual ``Q_total`` and the design-specific residual
    ``Q_within`` are block GLS optima, and ``Q_between = Q_total - Q_within`` on
    ``df_between = rank(Z) - rank(X)`` degrees of freedom is the design-by-
    treatment interaction inconsistency statistic.

    By default ``tau²`` is a DerSimonian-Laird moment estimate from
    within-designs heterogeneity only, matching the convention of the
    ``netmeta::decomp.design`` random-effects row: inconsistency cannot inflate
    the variance that defines the null distribution. Pass ``tau2`` explicitly to
    evaluate the decomposition at a preset common variance (for example the
    consistency REML estimate, which ``netmeta`` exposes through its
    ``tau.preset`` argument).

    Raises for a disconnected network, an unidentifiable common or
    design-specific treatment design, or non-positive residual degrees of
    freedom. ``status: "not_estimable"`` is returned when the design-specific
    model adds no estimable degrees of freedom.
    """
    if not isinstance(studies, list) or not studies:
        raise ValueError("studies must be a non-empty list")
    validate_study(studies[0], measure)
    tau2_preset = None if tau2 is None else _finite_preset_tau2(tau2)
    network = synthesize_network(studies, measure, reference)
    reference = network["reference"]
    nodes = network["treatments"]
    common_columns = [node for node in nodes if node != reference]

    blocks = _parse_blocks(studies, measure, common_columns)
    study_ids = [block["study_id"] for block in blocks]
    design_studies: dict[tuple[str, ...], list[str]] = {}
    design_contrasts: dict[tuple[str, ...], int] = {}
    for block in blocks:
        design_studies.setdefault(block["treatments"], []).append(block["study_id"])
        design_contrasts[block["treatments"]] = (
            design_contrasts.get(block["treatments"], 0) + len(block["values"]))

    stacked_common = np.vstack([block["common_design"] for block in blocks])
    common_rank = int(np.linalg.matrix_rank(stacked_common))
    if common_rank != len(common_columns):
        raise ValueError("network treatment effects are not identifiable")

    designs = sorted(design_studies)
    offsets = {}
    alternative_columns = 0
    for treatments in designs:
        offsets[treatments] = alternative_columns
        alternative_columns += len(treatments) - 1
    stacked_alternative = np.vstack([
        _alternative_design(block, offsets, alternative_columns) for block in blocks
    ])
    alternative_rank = int(np.linalg.matrix_rank(stacked_alternative))
    if alternative_rank != alternative_columns:
        raise ValueError("design-specific network effects are not identifiable")

    n_contrasts = sum(len(block["values"]) for block in blocks)
    df_total = n_contrasts - common_rank
    if df_total <= 0:
        raise ValueError(
            "random-effects network inconsistency requires positive residual "
            "degrees of freedom"
        )

    consistency = None
    consistency_reason = None
    try:
        consistency = synthesize_network_random(studies, measure, reference)
    except ValueError as exc:
        consistency_reason = str(exc)
    moment = _within_designs_moment(blocks)

    if tau2_preset is not None:
        tau2_used = tau2_preset
        tau2_source = "preset"
    else:
        tau2_used = moment["tau2"]
        tau2_source = "within_designs_moment"

    def decompose(tau2_value: float) -> tuple[float, float, dict]:
        q_total = _gls_residual_q(_marginal_blocks(blocks, "common_design", tau2_value))
        q_within = 0.0
        per_design = {}
        for treatments in designs:
            members = [block for block in blocks if block["treatments"] == treatments]
            q_design = _gls_residual_q(_marginal_blocks(members, "design", tau2_value))
            contrasts_design = sum(len(block["values"]) for block in members)
            per_design[treatments] = {
                "q": q_design,
                "df": contrasts_design - (len(treatments) - 1),
            }
            q_within += q_design
        return q_total, q_within, per_design

    q_total, q_within, per_design = decompose(tau2_used)
    df_within = n_contrasts - alternative_rank
    df_between = alternative_rank - common_rank
    q_between = q_total - q_within
    tolerance = 1e-9 * max(1.0, q_total, q_within)
    if q_between < -tolerance:
        raise ValueError("design-specific residual Q exceeded total network Q")
    q_between = max(0.0, q_between)

    estimable = df_between > 0
    if estimable:
        p_value = float(chi2.sf(q_between, df_between))
        if not math.isfinite(p_value):
            raise ValueError("inconsistency p-value must remain finite")
    else:
        p_value = None

    at_reml_tau2 = None
    tau2_reml = None
    boundary = None
    reml_log_likelihood = None
    if consistency is not None:
        tau2_reml = consistency["fit"]["tau2"]
        boundary = consistency["fit"]["boundary"]
        reml_log_likelihood = consistency["fit"]["reml_log_likelihood"]
        q_total_reml, q_within_reml, _ = decompose(tau2_reml)
        q_between_reml = max(0.0, q_total_reml - q_within_reml)
        at_reml_tau2 = {
            "tau2": tau2_reml,
            "q_total": q_total_reml,
            "q_within_designs": q_within_reml,
            "q_between_designs": q_between_reml,
            "p_value": float(chi2.sf(q_between_reml, df_between)) if estimable else None,
        }

    warnings = [
        "The between-designs statistic is compared with a chi-square distribution conditional "
        "on the assumed common tau-squared; it does not assess transitivity or effect-modifier "
        "comparability.",
        "The default tau-squared is a DerSimonian-Laird moment estimate from within-designs "
        "heterogeneity only, following the netmeta decomp.design random-effects convention; a "
        "tau-squared estimated from the whole network absorbs inconsistency and makes this test "
        "conservative.",
        "Studies are independent blocks; each supplied within-study covariance matrix is used "
        "as given.",
        "The design is inferred from treatment labels present in each study's contrasts.",
    ]
    if tau2_source == "preset":
        warnings.append(
            "The decomposition uses the caller-supplied common tau-squared; the default "
            "within-designs moment estimate is reported alongside for comparison."
        )
    if tau2_used == 0.0:
        warnings.append(
            "The assumed heterogeneity variance is zero, so this random-effects decomposition "
            "coincides with the fixed-effect between-design decomposition."
        )
    if consistency is None:
        warnings.append(
            f"The consistency REML fit was unavailable ({consistency_reason}); its summary is "
            "reported as null and only the moment or preset variance decomposition is returned."
        )
    elif tau2_reml == 0.0:
        warnings.append(
            "The consistency REML heterogeneity estimate is at the zero boundary."
        )
    if estimable and df_between == 1:
        warnings.append(
            "Between-design inconsistency has only one estimable degree of freedom for this "
            "network, so the test has little power and is sensitive to individual designs."
        )
    if any(len(treatments) > 2 for treatments in designs):
        warnings.append(
            "For multi-arm studies this module uses the supplied contrast covariance and "
            "arm-deviation heterogeneity structure, while netmeta rebuilds the covariance from "
            "arm-level standard errors; exact agreement with decomp.design is expected only for "
            "two-arm contrast networks."
        )
    if not moment["estimable"]:
        warnings.append(
            "The within-designs moment tau-squared was not estimable for this network and was "
            "set to zero."
        )

    result = {
        "method": "random-effects design-by-treatment interaction Q decomposition",
        "test": "global between-design inconsistency under a common-tau-squared random-effects model",
        "status": "available" if estimable else "not_estimable",
        "measure": measure,
        "reference": reference,
        "treatments": nodes,
        "n_studies": len(blocks),
        "n_contrasts": n_contrasts,
        "study_ids": study_ids,
        "designs": [
            {"treatments": list(treatments),
             "study_ids": design_studies[treatments],
             "n_studies": len(design_studies[treatments]),
             "n_contrasts": design_contrasts[treatments],
             "n_parameters": len(treatments) - 1,
             "q_within": per_design[treatments]["q"],
             "df_within": per_design[treatments]["df"]}
            for treatments in designs
        ],
        "rank_common": common_rank,
        "rank_design_specific": alternative_rank,
        "heterogeneity": {
            "tau2": tau2_used,
            "source": tau2_source,
            "scale": "log" if measure in RATIOS else "natural",
            "within_designs_moment": moment,
            "consistency_reml": {
                "status": "fitted" if consistency is not None else "unavailable",
                "reason": consistency_reason,
                "tau2": tau2_reml,
                "boundary": boundary,
                "reml_log_likelihood": reml_log_likelihood,
            },
        },
        "q_total": q_total,
        "df_total": df_total,
        "q_within_designs": q_within,
        "df_within_designs": df_within,
        "q_between_designs": q_between,
        "df_between_designs": df_between,
        "p_value": p_value,
        "at_reml_tau2": at_reml_tau2,
        "reason": None if estimable else (
            "The design-specific model adds no estimable degrees of freedom, so "
            "between-design inconsistency cannot be tested for this network."
        ),
        "warnings": warnings,
        "input_data": copy.deepcopy(studies),
    }
    return result
