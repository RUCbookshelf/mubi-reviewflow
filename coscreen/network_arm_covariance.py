"""Convert independent multi-arm trial summaries to network GLS inputs."""

from __future__ import annotations

import math
from numbers import Real
from typing import Mapping

from coscreen.network_gls import RATIOS, _parse_study
from coscreen.network_study_data import _validate

_MAX_EXACT_INTEGER = 2**53


def _finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, Real):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


def _arm_fields(arm: object, expected: set[str]) -> dict:
    if not isinstance(arm, Mapping) or set(arm) != expected:
        raise ValueError("arm fields do not match the selected measure")
    treatment = arm["treatment"]
    if not isinstance(treatment, str) or not treatment.strip():
        raise ValueError("each arm needs a nonempty treatment")
    return {**arm, "treatment": treatment.strip()}


def arm_summaries_to_network_study(study_id: str, arms: list[dict], measure: str,
                                   reference: str) -> dict:
    """Build n-1 reference contrasts and their sampling covariance from arm data.

    Arms must have independent participants, as in a parallel-group randomized
    trial. MD accepts ``n``, ``mean`` and sample ``sd``; RR and OR accept
    ``events`` and ``total``. Binary zero-event and all-event arms are rejected
    because no continuity correction policy is implicit here.
    """
    if not isinstance(study_id, str) or not study_id.strip():
        raise ValueError("study_id must be a nonempty string")
    if not isinstance(reference, str) or not reference.strip():
        raise ValueError("reference must be a nonempty treatment name")
    if not isinstance(measure, str):
        raise ValueError("measure must be MD, RR or OR")
    if measure == "MD":
        expected = {"treatment", "n", "mean", "sd"}
    elif measure in RATIOS:
        expected = {"treatment", "events", "total"}
    else:
        raise ValueError("measure must be MD, RR or OR")
    if not isinstance(arms, list) or len(arms) < 3:
        raise ValueError("at least three independent treatment arms are required")

    clean_arms = [_arm_fields(arm, expected) for arm in arms]
    treatments = [arm["treatment"] for arm in clean_arms]
    if len(treatments) != len(set(treatments)):
        raise ValueError("arm treatments must be unique")
    reference = reference.strip()
    if reference not in treatments:
        raise ValueError("reference must match one arm treatment")
    ref = next(arm for arm in clean_arms if arm["treatment"] == reference)
    active = [arm for arm in clean_arms if arm["treatment"] != reference]

    if measure == "MD":
        for arm in clean_arms:
            n, mean, sd = arm["n"], arm["mean"], arm["sd"]
            if type(n) is not int or not 2 <= n <= _MAX_EXACT_INTEGER:
                raise ValueError("each MD arm needs an integer n from 2 through 2**53")
            if not _finite_number(mean) or not _finite_number(sd) or sd < 0:
                raise ValueError("each MD arm needs a finite mean and nonnegative sample SD")
        active_variances = [(float(arm["sd"]) * float(arm["sd"])) / arm["n"] for arm in active]
        reference_variance = (float(ref["sd"]) * float(ref["sd"])) / ref["n"]
        from coscreen.review_analysis import continuous_effect

        try:
            contrasts = [{
                "treatment": arm["treatment"], "comparator": reference,
                "estimate": continuous_effect(
                    arm["n"], float(arm["mean"]), float(arm["sd"]),
                    ref["n"], float(ref["mean"]), float(ref["sd"]), "MD")["estimate"],
            } for arm in active]
        except OverflowError as exc:
            raise ValueError("derived MD and covariance values must remain finite") from exc
        covariance_scale = "natural"
    else:
        for arm in clean_arms:
            events, total = arm["events"], arm["total"]
            if type(events) is not int or type(total) is not int or \
               total <= 0 or total > _MAX_EXACT_INTEGER or not 0 <= events <= total:
                raise ValueError("events and total must be integers with 0 <= events <= total <= 2**53")
            if events == 0 or events == total:
                raise ValueError("zero-event or all-event arms require an explicit correction policy")
        from coscreen.review_analysis import binary_effect

        if measure == "RR":
            active_variances = [1 / arm["events"] - 1 / arm["total"] for arm in active]
            reference_variance = 1 / ref["events"] - 1 / ref["total"]
        else:
            active_variances = [1 / arm["events"] + 1 / (arm["total"] - arm["events"])
                                for arm in active]
            reference_variance = 1 / ref["events"] + 1 / (ref["total"] - ref["events"])
        contrasts = [{
            "treatment": arm["treatment"], "comparator": reference,
            "estimate": binary_effect(arm["events"], arm["total"],
                                       ref["events"], ref["total"], measure)["estimate"],
        } for arm in active]
        covariance_scale = "log"

    covariance = [
        [reference_variance + (active_variances[i] if i == j else 0.0)
         for j in range(len(active))]
        for i in range(len(active))
    ]
    contrasts, covariance = _validate(contrasts, covariance, measure, covariance_scale)
    study = {"study_id": study_id.strip(), "contrasts": contrasts,
             "covariance": covariance, "covariance_scale": covariance_scale}
    _parse_study(study, measure)
    return study
