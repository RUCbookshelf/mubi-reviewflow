"""Study-stratified Cox treatment effect from in-memory IPD rows."""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from collections.abc import Mapping, Sequence
from math import exp, isfinite, log, sqrt
from numbers import Real

from scipy.optimize import brentq
from scipy.stats import norm


def _treatment_probability(risk_control: float, risk_treatment: float, log_hr: float) -> float:
    if risk_treatment <= 0:
        return 0.0
    if risk_control <= 0:
        return 1.0
    value = log_hr + log(risk_treatment) - log(risk_control)
    if value >= 0:
        return 1 / (1 + exp(-value))
    odds = exp(value)
    return odds / (1 + odds)


def _exp_or_none(value: float) -> float | None:
    try:
        result = exp(value)
    except OverflowError:
        return None
    return result or None


def fit_stratified_cox(
    participants: Sequence[Mapping[str, object]],
    study_sources: Mapping[str, str],
    *,
    ties: str = "efron",
) -> dict:
    """Fit a common treatment log hazard ratio with a separate baseline per study.

    Participant rows must contain exactly ``study_id``, ``duration``, ``event``,
    and ``treatment``. ``study_sources`` maps every represented study ID to its
    source key. Rows are used only during this calculation and are never returned.
    """
    if ties not in {"efron", "breslow"}:
        raise ValueError("ties must be 'efron' or 'breslow'")
    if (
        not isinstance(participants, Sequence)
        or isinstance(participants, (str, bytes))
        or not participants
    ):
        raise ValueError("participants must be a non-empty sequence of rows")
    if not isinstance(study_sources, Mapping):
        raise ValueError("study source mapping is required")

    rows_by_study: dict[str, list[tuple[float, int, int]]] = defaultdict(list)
    events_by_arm = [0, 0]
    for row in participants:
        if not isinstance(row, Mapping) or set(row) != {"study_id", "duration", "event", "treatment"}:
            raise ValueError("each IPD row needs study_id, duration, event, and treatment")
        study_id = row["study_id"]
        duration = row["duration"]
        event = row["event"]
        treatment = row["treatment"]
        if not isinstance(study_id, str) or not study_id.strip():
            raise ValueError("study_id must be a non-empty string")
        if isinstance(duration, bool) or not isinstance(duration, Real):
            raise ValueError("duration must be a finite number greater than zero")
        try:
            duration = float(duration)
        except (OverflowError, TypeError, ValueError):
            raise ValueError("duration must be a finite number greater than zero") from None
        if not isfinite(duration) or duration <= 0:
            raise ValueError("duration must be a finite number greater than zero")
        if isinstance(event, bool) or not isinstance(event, Real) or event not in (0, 1):
            raise ValueError("event must be 0 or 1")
        if isinstance(treatment, bool) or not isinstance(treatment, Real) or treatment not in (0, 1):
            raise ValueError("treatment must be 0 or 1")
        event, treatment = int(event), int(treatment)
        rows_by_study[study_id].append((duration, event, treatment))
        events_by_arm[treatment] += event

    study_ids = set(rows_by_study)
    if set(study_sources) != study_ids or any(
        not isinstance(source, str) or not source.strip() for source in study_sources.values()
    ):
        raise ValueError("study source mapping must map every study ID to a non-empty source key")
    if not all(events_by_arm):
        raise ValueError("at least one event in both treatment arms is required")

    # Each tuple stores tied failures and arm-specific risk set sizes at one study-time.
    risk_groups: list[tuple[int, int, int, int, int, int]] = []
    for observations in rows_by_study.values():
        durations = ([], [])
        event_ties: dict[float, list[int]] = defaultdict(lambda: [0, 0])
        for duration, event, treatment in observations:
            durations[treatment].append(duration)
            if event:
                event_ties[duration][treatment] += 1
        durations[0].sort()
        durations[1].sort()
        for event_time, deaths in event_ties.items():
            risk_control = len(durations[0]) - bisect_left(durations[0], event_time)
            risk_treatment = len(durations[1]) - bisect_left(durations[1], event_time)
            risk_groups.append(
                (sum(deaths), deaths[1], risk_control, risk_treatment, deaths[0], deaths[1])
            )

    def score_information(log_hr: float) -> tuple[float, float]:
        score = 0.0
        information = 0.0
        for (
            deaths,
            treatment_deaths,
            risk_control,
            risk_treatment,
            control_deaths,
            treated_deaths,
        ) in risk_groups:
            score += treatment_deaths
            if ties == "breslow":
                probability = _treatment_probability(risk_control, risk_treatment, log_hr)
                score -= deaths * probability
                information += deaths * probability * (1 - probability)
            else:
                for tied_index in range(deaths):
                    fraction = tied_index / deaths
                    probability = _treatment_probability(
                        risk_control - fraction * control_deaths,
                        risk_treatment - fraction * treated_deaths,
                        log_hr,
                    )
                    score -= probability
                    information += probability * (1 - probability)
        return score, information

    lower, upper = -1.0, 1.0
    while score_information(lower)[0] <= 0 and lower > -64:
        lower *= 2
    while score_information(upper)[0] >= 0 and upper < 64:
        upper *= 2
    if score_information(lower)[0] <= 0 or score_information(upper)[0] >= 0:
        raise ValueError("treatment effect is separated or not identifiable")

    log_hr = brentq(lambda value: score_information(value)[0], lower, upper)
    _, information = score_information(log_hr)
    # ponytail: |log(HR)| >= 30 is treated as a numerical boundary; use a penalized Cox fit for such effects.
    if not isfinite(log_hr) or abs(log_hr) >= 30 or not isfinite(information) or information <= 1e-12:
        raise ValueError("treatment effect is extreme, separated, or not identifiable")
    se = sqrt(1 / information)
    critical = norm.ppf(0.975)
    log_hr_ci_low, log_hr_ci_high = log_hr - critical * se, log_hr + critical * se

    return {
        "log_hr": log_hr,
        "se": se,
        "log_hr_ci_low": log_hr_ci_low,
        "log_hr_ci_high": log_hr_ci_high,
        "hazard_ratio": _exp_or_none(log_hr),
        "hr_ci_low": _exp_or_none(log_hr_ci_low),
        "hr_ci_high": _exp_or_none(log_hr_ci_high),
        "confidence_level": 0.95,
        "n": len(participants),
        "events": sum(events_by_arm),
        "studies": len(study_ids),
        "study_sources": dict(study_sources),
        "ties": ties,
        "method": "study-stratified Cox proportional hazards model",
        "assumptions": [
            "common treatment log hazard ratio across studies",
            "proportional hazards within each study",
            "independent right censoring",
            "no covariate adjustment",
        ],
    }
