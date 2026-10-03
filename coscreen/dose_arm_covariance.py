"""Arm-level dose-response summaries to Greenland-Longnecker within-study contrasts.

Builds, for one study, the non-reference dose contrasts and their sampling
covariance matrix that the two-stage GLS dose-response methods in
``dose_analysis`` and ``dose_nonlinear`` require as input. The covariance
follows Greenland & Longnecker (1992): each contrast shares the reference
arm, so every off-diagonal element contains only the reference-arm
contribution. For log ratios the variances use the corrected forms in
Orsini et al. (2012); for mean differences the (co)variances follow Crippa
& Orsini (2016).
"""

from __future__ import annotations

import copy
import math
from numbers import Real
from typing import Mapping

_MAX_EXACT_INTEGER = 2**53

_LOG_RATIO_MEASURES = {"RR", "OR"}


def _finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, Real):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


def _finite_float(value: object, field: str) -> float:
    if not _finite_number(value):
        raise ValueError(f"{field} must be a finite number")
    return float(value)


def _dose(arm: Mapping) -> float:
    dose = _finite_float(arm["dose"], "arm dose")
    if dose < 0:
        raise ValueError("arm doses must be nonnegative; negative exposures are rejected")
    return dose


def _validate_reference_dose(reference_dose: object, doses: list[float]) -> float:
    reference = _finite_float(reference_dose, "reference dose")
    if sum(1 for dose in doses if dose == reference) != 1:
        raise ValueError("reference dose must match exactly one arm dose")
    return reference


def _validate_events_arm(arm: Mapping) -> None:
    events, total = arm["events"], arm["total"]
    if type(events) is not int or type(total) is not int or \
       total <= 0 or total > _MAX_EXACT_INTEGER or not 0 <= events <= total:
        raise ValueError("events and total must be integers with 0 <= events <= total <= 2**53")
    if events == 0 or events == total:
        raise ValueError("zero-event or all-event arms require an explicit correction policy: "
                         "the Greenland-Longnecker covariance has 1/events terms and the "
                         "verified sources define no continuity correction")


def _validate_rate_arm(arm: Mapping) -> None:
    events, person_time = arm["events"], arm["person_time"]
    if type(events) is not int or events <= 0 or events > _MAX_EXACT_INTEGER:
        raise ValueError("each rate arm needs a positive integer event count: the "
                         "Greenland-Longnecker covariance has 1/events terms and the "
                         "verified sources define no continuity correction")
    if not _finite_number(person_time) or float(person_time) <= 0:
        raise ValueError("each rate arm needs a positive person-time")


def _validate_md_arm(arm: Mapping) -> None:
    n, mean, sd = arm["n"], arm["mean"], arm["sd"]
    if type(n) is not int or not 2 <= n <= _MAX_EXACT_INTEGER:
        raise ValueError("each MD arm needs an integer n from 2 through 2**53")
    if not _finite_number(mean):
        raise ValueError("each MD arm needs a finite mean")
    if not _finite_number(sd) or float(sd) <= 0:
        raise ValueError("each MD arm needs a positive sample SD so the shared-reference "
                         "covariance stays nonsingular")


def arm_summaries_to_dose_curve(study_id: str, arms: list[dict], measure: str,
                                dose_unit: str, reference_dose: float | None = None) -> dict:
    """Build non-reference dose contrasts and their within-study covariance.

    ``arms`` are the dose categories of one study ordered arbitrarily. For
    ``measure="RR"`` each arm needs ``dose``, integer ``events`` and either
    integer ``total`` (cumulative-incidence risks) or ``person_time``
    (incidence-rate data); for ``measure="OR"``, ``dose``, ``events`` and
    ``total`` (cases and controls); for ``measure="MD"``, ``dose``, integer
    ``n >= 2``, ``mean`` and positive sample ``sd``. Arms must have
    independent participants and at least three dose levels (reference plus
    two contrasts) are required so the result can enter two-stage synthesis.

    ``reference_dose`` selects the referent among the arm doses; by default
    the lowest dose is the referent, as in the Greenland & Longnecker
    examples. Doses must be finite, unique and nonnegative (zero is the
    canonical no-exposure referent). Zero-event or all-event arms are
    rejected because the covariance has 1/events terms and no verified
    source defines a zero-cell continuity convention.

    Returns ``study_id``, ``measure``, ``contrasts`` (``dose`` and
    ``estimate`` keys only, ascending dose, referent excluded, directly
    accepted by ``dose_analysis.save_curve``), the ``covariance`` matrix
    (log scale for RR and OR), ``reference_dose``, ``dose_unit``,
    ``covariance_scale``, and ``input_data``, a deep copy of the original
    arms.
    """
    if not isinstance(study_id, str) or not study_id.strip():
        raise ValueError("study_id must be a nonempty string")
    if not isinstance(dose_unit, str) or not dose_unit.strip():
        raise ValueError("dose_unit must be a nonempty string")
    if measure not in _LOG_RATIO_MEASURES | {"MD"}:
        raise ValueError("measure must be RR, OR or MD; the Greenland-Longnecker "
                         "covariance is defined for log ratios and mean differences")
    if not isinstance(arms, list) or len(arms) < 3:
        raise ValueError("at least three dose arms (reference plus two contrasts) are required")

    if measure == "MD":
        expected = {"dose", "n", "mean", "sd"}
    elif measure == "RR":
        expected = ({"dose", "events", "person_time"} if "person_time" in arms[0]
                    else {"dose", "events", "total"})
    else:
        expected = {"dose", "events", "total"}
    clean_arms = []
    for arm in arms:
        if not isinstance(arm, Mapping) or set(arm) != expected:
            if measure == "RR":
                raise ValueError("RR arm fields must be exactly ['dose', 'events', 'total'] or "
                                 "['dose', 'events', 'person_time'], consistently across arms")
            raise ValueError(f"arm fields must be exactly {sorted(expected)} for measure {measure}")
        clean_arms.append(dict(arm))
    rate_arm = measure == "RR" and "person_time" in expected
    for arm in clean_arms:
        _dose(arm)
        if rate_arm:
            _validate_rate_arm(arm)
        elif measure == "MD":
            _validate_md_arm(arm)
        else:
            _validate_events_arm(arm)

    doses = [float(arm["dose"]) for arm in clean_arms]
    if len(set(doses)) != len(doses):
        raise ValueError("arm doses must be unique")
    clean_arms = [arm for _, arm in sorted(zip(doses, clean_arms), key=lambda pair: pair[0])]
    if reference_dose is None:
        reference = float(clean_arms[0]["dose"])
    else:
        reference = _validate_reference_dose(reference_dose, [float(arm["dose"]) for arm in clean_arms])
    ref = next(arm for arm in clean_arms if float(arm["dose"]) == reference)
    active = [arm for arm in clean_arms if arm is not ref]

    if measure == "MD":
        # MD contrasts share the referent arm: Var(Y_i - Y_0) = sd_i^2/n_i +
        # sd_0^2/n_0 and Cov(Y_i - Y_0, Y_l - Y_0) = sd_0^2/n_0 — the same
        # own-variance structure as network_arm_covariance, positive definite
        # for any arm SDs. (dosresmeta's covar.smd pools the SD for the
        # diagonal only; mixing that pooled estimate with the referent arm's
        # own sd_0^2/n_0 off-diagonal is not positive definite when the
        # referent SD dominates, so legitimate studies were rejected.)
        ref_variance = float(ref["sd"]) ** 2 / ref["n"]
        contrasts = [{"dose": float(arm["dose"]),
                      "estimate": float(arm["mean"]) - float(ref["mean"])} for arm in active]
        variances = [float(arm["sd"]) ** 2 / arm["n"] + ref_variance for arm in active]
        covariance_scale = "natural"
    elif rate_arm:
        ref_variance = 1 / ref["events"]
        contrasts = [{"dose": float(arm["dose"]),
                      "estimate": (arm["events"] / float(arm["person_time"]))
                      / (ref["events"] / float(ref["person_time"]))} for arm in active]
        variances = [1 / arm["events"] for arm in active]
        covariance_scale = "log"
    elif measure == "RR":
        ref_variance = 1 / ref["events"] - 1 / ref["total"]
        contrasts = [{"dose": float(arm["dose"]),
                      "estimate": (arm["events"] / arm["total"])
                      / (ref["events"] / ref["total"])} for arm in active]
        variances = [1 / arm["events"] - 1 / arm["total"] for arm in active]
        covariance_scale = "log"
    else:
        ref_variance = 1 / ref["events"] + 1 / (ref["total"] - ref["events"])
        contrasts = [{"dose": float(arm["dose"]),
                      "estimate": (arm["events"] / (arm["total"] - arm["events"]))
                      / (ref["events"] / (ref["total"] - ref["events"]))} for arm in active]
        variances = [1 / arm["events"] + 1 / (arm["total"] - arm["events"]) for arm in active]
        covariance_scale = "log"

    if measure == "MD":
        covariance = [
            [variances[i] if i == j else ref_variance
             for j in range(len(active))]
            for i in range(len(active))
        ]
    else:
        covariance = [
            [ref_variance + (variances[i] if i == j else 0.0)
             for j in range(len(active))]
            for i in range(len(active))
        ]
    from coscreen.dose_analysis import _validate

    _validate(contrasts, covariance, reference, measure)
    return {
        "study_id": study_id.strip(),
        "measure": measure,
        "contrasts": contrasts,
        "covariance": covariance,
        "reference_dose": reference,
        "dose_unit": dose_unit.strip(),
        "covariance_scale": covariance_scale,
        "input_data": copy.deepcopy(arms),
    }
