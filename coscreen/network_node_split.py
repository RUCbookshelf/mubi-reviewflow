"""Fixed-effect direct/indirect split for one network treatment contrast."""

from __future__ import annotations

import math

import numpy as np
from scipy.stats import norm

from coscreen.network_gls import MEASURES, RATIOS, _parse_study, synthesize_network


def _finite(values, label: str) -> None:
    values = values if isinstance(values, (tuple, list)) else (values,)
    if any(not np.all(np.isfinite(value)) for value in values):
        raise ValueError(f"{label} must remain finite")


def _name(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty treatment name")
    return value.strip()


def _effect(value: float, measure: str) -> float:
    return math.log(value) if measure in RATIOS else value


def _direct_pair_effect(contrasts, covariance: np.ndarray, measure: str,
                        treatment: str, comparator: str) -> tuple[float, float]:
    nodes = sorted({node for a, b, _ in contrasts for node in (a, b)})
    columns = nodes[1:]
    design = np.asarray(
        [[int(a == node) - int(b == node) for node in columns]
         for a, b, _ in contrasts], dtype=float)
    target = np.asarray(
        [int(treatment == node) - int(comparator == node) for node in columns],
        dtype=float)
    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            coefficients = np.linalg.lstsq(design.T, target, rcond=None)[0]
            residual = design.T @ coefficients - target
    except (FloatingPointError, np.linalg.LinAlgError) as exc:
        raise ValueError("direct pair contrast could not be solved within a study") from exc
    _finite((coefficients, residual), "direct pair contrast")
    if not np.allclose(residual, 0, rtol=1e-10, atol=1e-12):
        raise ValueError("direct pair contrast is not identifiable within a direct study")

    values = np.asarray([_effect(value, measure) for _, _, value in contrasts])
    try:
        with np.errstate(over="raise", invalid="raise"):
            estimate = float(coefficients @ values)
            variance = float(coefficients @ covariance @ coefficients)
    except FloatingPointError as exc:
        raise ValueError("direct pair estimate or variance overflowed") from exc
    _finite((estimate, variance), "direct pair estimate and variance")
    if variance <= 0:
        raise ValueError("direct pair variance must be positive and finite")
    return estimate, variance


def _reported_effect(effect: float, variance: float, measure: str,
                     n_studies: int) -> dict:
    _finite((effect, variance), "effect and variance")
    if variance <= 0:
        raise ValueError("effect variance must be positive and finite")
    se = math.sqrt(variance)
    low, high = effect - 1.96 * se, effect + 1.96 * se
    _finite((se, low, high), "effect interval")
    try:
        if measure in RATIOS:
            estimate, ci_low, ci_high = math.exp(effect), math.exp(low), math.exp(high)
        else:
            estimate, ci_low, ci_high = effect, low, high
    except OverflowError as exc:
        raise ValueError("network split result overflows on the ratio scale") from exc
    _finite((estimate, ci_low, ci_high), "reported effect")
    if measure in RATIOS and min(estimate, ci_low, ci_high) <= 0:
        raise ValueError("ratio estimates and intervals must remain positive and finite")
    return {"estimate": estimate, "se": se,
            "se_scale": "log" if measure in RATIOS else "natural",
            "ci_low": ci_low, "ci_high": ci_high,
            "n_studies": n_studies}


def split_network_contrast(studies: list[dict], measure: str,
                           treatment: str, comparator: str) -> dict:
    """Estimate direct and indirect evidence for one treatment pair.

    A study is direct when its submitted treatment design contains both requested
    treatments. Its pair effect is derived from the complete contrast block and
    covariance. The whole study is then excluded from the indirect network fit.
    """
    treatment, comparator = _name(treatment, "treatment"), _name(comparator, "comparator")
    if treatment == comparator:
        raise ValueError("treatment and comparator must be different")
    if not isinstance(studies, list) or not studies:
        raise ValueError("studies must be a non-empty list")
    if not isinstance(measure, str) or measure not in MEASURES:
        raise ValueError("network synthesis supports RR, OR, RD, MD or SMD")

    parsed = [_parse_study(study, measure) for study in studies]
    ids = [study_id for study_id, _, _ in parsed]
    if len(ids) != len(set(ids)):
        raise ValueError("study_id values must be unique")

    direct_ids, indirect_ids, indirect_studies = [], [], []
    direct_effects, direct_variances = [], []
    for study, (study_id, contrasts, covariance) in zip(studies, parsed):
        nodes = {node for a, b, _ in contrasts for node in (a, b)}
        if treatment in nodes and comparator in nodes:
            covariance = np.asarray(covariance, dtype=float)
            estimate, variance = _direct_pair_effect(
                contrasts, covariance, measure, treatment, comparator)
            direct_ids.append(study_id)
            direct_effects.append(estimate)
            direct_variances.append(variance)
        else:
            indirect_ids.append(study_id)
            indirect_studies.append(study)

    if not direct_ids:
        raise ValueError("no direct study design contains the requested treatment pair")
    if not indirect_studies:
        raise ValueError("no indirect evidence remains after excluding direct designs")

    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            direct_weights = 1 / np.asarray(direct_variances, dtype=float)
            direct_variance = float(1 / np.sum(direct_weights))
            direct_effect = float(np.dot(direct_weights, direct_effects) /
                                  np.sum(direct_weights))
    except FloatingPointError as exc:
        raise ValueError("direct evidence pooling overflowed") from exc
    _finite((direct_weights, direct_variance, direct_effect), "direct evidence pooling")

    indirect_nodes = sorted({node for study in indirect_studies
                             for contrast in study["contrasts"]
                             for node in (contrast["treatment"].strip(),
                                          contrast["comparator"].strip())})
    indirect_fit = synthesize_network(indirect_studies, measure, indirect_nodes[0])
    if treatment not in indirect_fit["treatments"] or comparator not in indirect_fit["treatments"]:
        raise ValueError("requested treatment pair is not identifiable from indirect designs")
    pair = next(row for row in indirect_fit["estimates"]
                if {row["treatment"], row["comparator"]} == {treatment, comparator})
    indirect_effect = _effect(pair["estimate"], measure)
    if pair["treatment"] != treatment:
        indirect_effect = -indirect_effect
    indirect_variance = pair["se"] ** 2

    # The study-ID partition makes these estimates independent under the block model.
    difference = direct_effect - indirect_effect
    difference_variance = direct_variance + indirect_variance
    difference_se = math.sqrt(difference_variance)
    z_value = difference / difference_se
    difference_low = difference - 1.96 * difference_se
    difference_high = difference + 1.96 * difference_se
    p_value = float(2 * norm.sf(abs(z_value)))
    _finite((indirect_effect, indirect_variance, difference, difference_variance,
             difference_se, z_value, difference_low, difference_high, p_value),
            "direct-indirect comparison")

    disagreement = {"estimate": difference, "scale": "log" if measure in RATIOS else "natural",
                    "se": difference_se,
                    "ci_low": difference_low, "ci_high": difference_high,
                    "z_value": z_value, "p_value": p_value}
    if measure in RATIOS:
        try:
            disagreement["direct_to_indirect_ratio"] = math.exp(difference)
        except OverflowError as exc:
            raise ValueError("direct-to-indirect ratio overflows") from exc
        _finite(disagreement["direct_to_indirect_ratio"], "direct-to-indirect ratio")
        if disagreement["direct_to_indirect_ratio"] <= 0:
            raise ValueError("direct-to-indirect ratio must remain positive and finite")

    return {
        "method": "fixed-effect contrast-level direct/indirect split",
        "measure": measure,
        "treatment": treatment,
        "comparator": comparator,
        "direct": {
            **_reported_effect(direct_effect, direct_variance, measure, len(direct_ids)),
            "study_ids": direct_ids,
        },
        "indirect": {
            **_reported_effect(indirect_effect, indirect_variance, measure,
                               indirect_fit["n_studies"]),
            "study_ids": indirect_ids,
            "n_contrasts": indirect_fit["n_contrasts"],
        },
        "disagreement": disagreement,
        "warnings": [
            "Fixed effects are assumed; direct and indirect between-study heterogeneity is not modeled.",
            "Each direct study contributes one pair contrast derived with its full covariance block; the whole study is excluded from the indirect fit.",
            "The Wald comparison is a local direct-indirect disagreement diagnostic; it does not assess transitivity or effect-modifier balance.",
        ],
    }
