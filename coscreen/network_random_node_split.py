"""Random-effects direct/indirect split for one network treatment contrast."""

from __future__ import annotations

import math

import numpy as np
from scipy.stats import norm

from coscreen.meta_regression_model import profile_reml_tau2
from coscreen.network_gls import MEASURES, RATIOS, _parse_study
from coscreen.network_node_split import (
    _direct_pair_effect,
    _effect,
    _finite,
    _name,
    _reported_effect,
)
from coscreen.network_random import synthesize_network_random


def _pool_direct_random(effects: list[float], variances: list[float]) -> tuple[float, float, float]:
    if len(effects) < 2:
        raise ValueError("at least two direct studies are required to estimate tau-squared by REML")
    try:
        tau2, _ = profile_reml_tau2(effects, variances)
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            weights = 1 / (np.asarray(variances, dtype=float) + tau2)
            total_weight = float(np.sum(weights))
            estimate = float(np.dot(weights, effects) / total_weight)
            variance = float(1 / total_weight)
    except (FloatingPointError, OverflowError, ZeroDivisionError, ValueError) as exc:
        raise ValueError(f"direct pair REML could not estimate an identifiable tau-squared: {exc}") from None
    _finite((tau2, weights, total_weight, estimate, variance), "direct random-effects pooling")
    if tau2 < 0 or total_weight <= 0 or variance <= 0:
        raise ValueError("direct pair REML did not produce an identifiable random-effects estimate")
    return estimate, variance, tau2


def split_network_contrast_random(studies: list[dict], measure: str,
                                  treatment: str, comparator: str) -> dict:
    """Compare direct and indirect evidence with partition-specific REML tau².

    Every direct design is reduced to the requested pair with its full supplied
    covariance, then excluded as a whole from the random-effects indirect fit.
    """
    treatment = _name(treatment, "treatment")
    comparator = _name(comparator, "comparator")
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
            effect, variance = _direct_pair_effect(
                contrasts, covariance, measure, treatment, comparator)
            direct_ids.append(study_id)
            direct_effects.append(effect)
            direct_variances.append(variance)
        else:
            indirect_ids.append(study_id)
            indirect_studies.append({
                "study_id": study_id,
                "covariance_scale": "log" if measure in RATIOS else "natural",
                "contrasts": [{"treatment": a, "comparator": b, "estimate": value}
                              for a, b, value in contrasts],
                "covariance": covariance.tolist(),
            })

    if not direct_ids:
        raise ValueError("no direct study design contains the requested treatment pair")
    if not indirect_studies:
        raise ValueError("no indirect evidence remains after excluding direct designs")

    direct_effect, direct_variance, direct_tau2 = _pool_direct_random(
        direct_effects, direct_variances)

    indirect_nodes = sorted({node for study in indirect_studies
                             for contrast in study["contrasts"]
                             for node in (contrast["treatment"].strip(),
                                          contrast["comparator"].strip())})
    indirect_fit = synthesize_network_random(
        indirect_studies, measure, indirect_nodes[0])
    if treatment not in indirect_fit["treatments"] or comparator not in indirect_fit["treatments"]:
        raise ValueError("requested treatment pair is not identifiable from indirect designs")
    pair = next(row for row in indirect_fit["estimates"]
                if {row["treatment"], row["comparator"]} == {treatment, comparator})
    indirect_effect = _effect(pair["estimate"], measure)
    if pair["treatment"] != treatment:
        indirect_effect = -indirect_effect
    indirect_variance = pair["se"] ** 2

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
    if difference_variance <= 0:
        raise ValueError("direct-indirect variance must be positive and finite")

    disagreement = {"estimate": difference,
                    "scale": "log" if measure in RATIOS else "natural",
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

    indirect_tau2 = indirect_fit["fit"]["tau2"]
    return {
        "method": "random-effects contrast-level direct/indirect split with partition-specific REML",
        "measure": measure,
        "treatment": treatment,
        "comparator": comparator,
        "direct": {
            **_reported_effect(direct_effect, direct_variance, measure, len(direct_ids)),
            "study_ids": direct_ids,
            "tau2": direct_tau2,
            "tau2_method": "REML",
            "tau2_scale": "log" if measure in RATIOS else "natural",
        },
        "indirect": {
            **_reported_effect(indirect_effect, indirect_variance, measure,
                               indirect_fit["n_studies"]),
            "study_ids": indirect_ids,
            "n_contrasts": indirect_fit["n_contrasts"],
            "tau2": indirect_tau2,
            "tau2_method": indirect_fit["fit"]["method"],
            "tau2_scale": indirect_fit["random_effects"]["scale"],
            "tau2_boundary": indirect_fit["fit"]["boundary"],
        },
        "disagreement": disagreement,
        "warnings": [
            "Direct and indirect study blocks are assumed independent; the comparison variance is their sum.",
            "Each partition estimates its own common tau-squared by REML; Wald intervals condition on those estimates.",
            "Partition-specific REML differs from netmeta's convention of using the full-network tau-squared for direct estimates.",
            "This is a local direct-indirect disagreement diagnostic; it does not assess transitivity or effect-modifier balance.",
            *indirect_fit["warnings"],
        ],
    }
