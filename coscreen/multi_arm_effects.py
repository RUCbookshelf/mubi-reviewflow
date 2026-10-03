"""Combine eligible, mutually exclusive trial arms into one pairwise effect.

For continuous outcomes the combined sample variance includes within-arm and
between-arm variation, as in Harrer et al. §17.9 and Cochrane §6.5.2.10.
Eligibility and clinical comparability remain the reviewer's decision.
"""

from __future__ import annotations

import math

_MAX_EXACT_INTEGER = 2**53


def _finite_real(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def _label(arm: dict, expected: set[str]) -> str:
    if not isinstance(arm, dict) or set(arm) != expected:
        raise ValueError("arm fields do not match the selected measure")
    label = arm["label"]
    if not isinstance(label, str) or not label.strip():
        raise ValueError("each arm needs a nonempty label")
    return label


def _validate_arms(arms: list[dict], expected: set[str]) -> None:
    for arm in arms:
        _label(arm, expected)
        if expected == {"label", "events", "total"}:
            events, total = arm["events"], arm["total"]
            if type(events) is not int or type(total) is not int or \
               events > _MAX_EXACT_INTEGER or total > _MAX_EXACT_INTEGER or \
               total <= 0 or not 0 <= events <= total:
                raise ValueError("events must be integers between zero and a positive group total")
        else:
            n, mean, sd = arm["n"], arm["mean"], arm["sd"]
            if type(n) is not int or n < 2 or n > _MAX_EXACT_INTEGER:
                raise ValueError("each continuous arm needs an integer n from 2 through 2**53")
            if not _finite_real(mean) or not _finite_real(sd):
                raise ValueError("each continuous arm needs finite mean and SD")
            if sd < 0:
                raise ValueError("continuous arm SD must be nonnegative")


def _combine_continuous(arms: list[dict]) -> dict:
    total_n = sum(arm["n"] for arm in arms)
    if total_n > _MAX_EXACT_INTEGER:
        raise ValueError("pooled sample size exceeds the exact integer range (2**53)")
    try:
        mean = math.fsum((arm["n"] / total_n) * float(arm["mean"]) for arm in arms)
        within = math.fsum((arm["n"] - 1) * float(arm["sd"]) ** 2 for arm in arms)
        between = math.fsum(arm["n"] * (float(arm["mean"]) - mean) ** 2 for arm in arms)
        variance = (within + between) / (total_n - 1)
    except (OverflowError, ValueError) as exc:
        raise ValueError("combined continuous arm values must remain finite") from exc
    if not math.isfinite(mean) or not math.isfinite(variance) or variance < 0:
        raise ValueError("combined continuous arm values must remain finite")
    return {"n": total_n, "mean": mean, "sd": math.sqrt(variance)}


def _combine_binary(arms: list[dict]) -> dict:
    events = sum(arm["events"] for arm in arms)
    total = sum(arm["total"] for arm in arms)
    if events > _MAX_EXACT_INTEGER or total > _MAX_EXACT_INTEGER:
        raise ValueError("pooled counts exceed the exact integer range (2**53)")
    return {"events": events, "total": total}


def calculate_multi_arm_effect(measure: str, intervention_arms: list[dict],
                              comparator_arms: list[dict], combination_rationale: str) -> dict:
    """Combine one or both sides, then use the existing two-arm effect formula."""
    if measure in {"RR", "OR", "RD"}:
        expected = {"label", "events", "total"}
    elif measure in {"MD", "SMD"}:
        expected = {"label", "n", "mean", "sd"}
    else:
        raise ValueError("measure must be RR, OR, RD, MD or SMD")
    if not isinstance(intervention_arms, list) or not intervention_arms or \
       not isinstance(comparator_arms, list) or not comparator_arms:
        raise ValueError("at least one intervention and one comparator arm are required")
    if len(intervention_arms) < 2 and len(comparator_arms) < 2:
        raise ValueError("combine at least two arms on one side to create a multi-arm comparison")
    if not isinstance(combination_rationale, str) or not combination_rationale.strip():
        raise ValueError("combination rationale is required")

    _validate_arms(intervention_arms, expected)
    _validate_arms(comparator_arms, expected)
    labels = [_label(arm, expected).strip().casefold()
              for arm in (*intervention_arms, *comparator_arms)]
    if len(set(labels)) != len(labels):
        raise ValueError("duplicate arm labels are not allowed")

    if measure in {"RR", "OR", "RD"}:
        combined_intervention = _combine_binary(intervention_arms)
        combined_comparator = _combine_binary(comparator_arms)
        from coscreen.review_analysis import binary_effect

        effect = binary_effect(combined_intervention["events"], combined_intervention["total"],
                               combined_comparator["events"], combined_comparator["total"], measure)
        method = "sum events and totals"
    else:
        combined_intervention = _combine_continuous(intervention_arms)
        combined_comparator = _combine_continuous(comparator_arms)
        total_n = combined_intervention["n"] + combined_comparator["n"]
        if total_n > _MAX_EXACT_INTEGER:
            raise ValueError("combined sample size exceeds the exact integer range (2**53)")
        from coscreen.review_analysis import continuous_effect

        if measure == "SMD":
            try:
                pooled_variance = math.fsum((group["n"] - 1) * group["sd"] ** 2
                                             for group in (combined_intervention, combined_comparator)) / (total_n - 2)
            except (OverflowError, ValueError) as exc:
                raise ValueError("SMD pooled SD must remain finite") from exc
            if not math.isfinite(pooled_variance) or pooled_variance <= 0:
                raise ValueError("SMD pooled SD must remain positive and finite")
        try:
            effect = continuous_effect(combined_intervention["n"], combined_intervention["mean"],
                                       combined_intervention["sd"], combined_comparator["n"],
                                       combined_comparator["mean"], combined_comparator["sd"], measure)
        except OverflowError as exc:
            raise ValueError("combined effect exceeds the finite calculation range") from exc
        method = "sample-size weighted mean and pooled sample SD including within- and between-arm variation"
    if not math.isfinite(effect["estimate"]) or not math.isfinite(effect["se"]) or effect["se"] <= 0:
        raise ValueError("combined effect must have a finite estimate and positive finite SE")
    return {
        **effect,
        "input_data": {
            "intervention_arms": [dict(arm) for arm in intervention_arms],
            "comparator_arms": [dict(arm) for arm in comparator_arms],
            "combination_rationale": combination_rationale,
            "combination_method": method,
            "combined_intervention_arm": combined_intervention,
            "combined_comparator_arm": combined_comparator,
        },
    }
