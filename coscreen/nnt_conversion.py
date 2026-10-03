"""Convert risk differences, odds ratios and risk ratios to NNT with NNTB/NNTH semantics."""

from __future__ import annotations

import copy
import math

# Sign convention used throughout this module: RD = risk_treatment - risk_control
# for the recorded binary event. A positive RD means the treatment INCREASES the
# event probability, which is harm (NNTH); a negative RD means the treatment
# lowers the event probability, which is benefit (NNTB). NNT magnitudes are never
# reported as negative numbers; direction is carried by the semantics labels
# (Altman 1998; Cochrane Handbook v6.5, Section 15.4).

_RISK_LIMITS = (-1.0, 1.0)
_CROSSING_WARNING = (
    "The risk-difference interval spans zero, so the NNT interval is unbounded and "
    "contains both infinitely large NNTB and NNTH values; it is reported in the "
    "Altman (1998) form 'NNTH a to infinity to NNTB b'."
)
_ZERO_LIMIT_WARNING = (
    "A risk-difference confidence limit is exactly zero; the corresponding NNT bound "
    "is infinite and is reported as an unbounded one-sided interval."
)
_OUTSIDE_WARNING = (
    "The point estimate lies outside the supplied confidence interval; check the inputs."
)
_CER_WARNING = (
    "The NNT depends on the supplied control event rate (CER); recompute it with the "
    "actual control event rate of the target population before use."
)


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _level(value: object) -> float:
    level = _number(value, "level")
    if not 0 < level < 1:
        raise ValueError("level must lie strictly between 0 and 1")
    return level


def _limits(values: object, name: str) -> list[float]:
    if not isinstance(values, (list, tuple)) or len(values) != 2:
        raise ValueError(f"{name} must be a two-element sequence (lower, upper)")
    lower = _number(values[0], f"{name}[0]")
    upper = _number(values[1], f"{name}[1]")
    if not lower < upper:
        raise ValueError(f"{name} limits are out of order: lower must be strictly less than upper")
    return [lower, upper]


def _format(value: float) -> str:
    return format(value, ".4g")


def _point(rd: float) -> dict:
    """Turn one non-zero risk difference into a directed NNT point estimate."""
    value = 1.0 / -rd if rd < 0 else 1.0 / rd
    semantics = "NNTB" if rd < 0 else "NNTH"
    # Round away representation noise before ceiling so an exact integer NNT
    # computed in floating point (e.g. 1/(0.25*(1-0.9))) does not round up.
    return {
        "value": value,
        "semantics": semantics,
        "rounded_up": math.ceil(round(value, 9)),
        "display": f"{semantics} {_format(value)}",
    }


def _interval(lower: float, upper: float) -> dict:
    """Map an RD interval to the NNT scale with Altman (1998) NNTB/NNTH semantics."""
    if lower < 0 < upper:
        nnth_bound = 1.0 / upper
        nntb_bound = 1.0 / -lower
        return {
            "lower": nnth_bound,
            "upper": nntb_bound,
            "lower_semantics": "NNTH",
            "upper_semantics": "NNTB",
            "crosses_zero": True,
            "unbounded": True,
            "semantics": f"NNTH {_format(nnth_bound)} to infinity to NNTB {_format(nntb_bound)}",
        }
    if lower > 0:
        near, far = 1.0 / upper, 1.0 / lower
        return {
            "lower": near,
            "upper": far,
            "lower_semantics": "NNTH",
            "upper_semantics": "NNTH",
            "crosses_zero": False,
            "unbounded": False,
            "semantics": f"NNTH {_format(near)} to {_format(far)}",
        }
    if upper < 0:
        near, far = 1.0 / -lower, 1.0 / -upper
        return {
            "lower": near,
            "upper": far,
            "lower_semantics": "NNTB",
            "upper_semantics": "NNTB",
            "crosses_zero": False,
            "unbounded": False,
            "semantics": f"NNTB {_format(near)} to {_format(far)}",
        }
    # One limit is exactly zero: a one-sided interval extending to infinity.
    if lower == 0:
        bound = 1.0 / upper
        return {
            "lower": bound,
            "upper": None,
            "lower_semantics": "NNTH",
            "upper_semantics": "NNTH",
            "crosses_zero": False,
            "unbounded": True,
            "semantics": f"NNTH {_format(bound)} to infinity",
        }
    bound = 1.0 / -lower
    return {
        "lower": bound,
        "upper": None,
        "lower_semantics": "NNTB",
        "upper_semantics": "NNTB",
        "crosses_zero": False,
        "unbounded": True,
        "semantics": f"NNTB {_format(bound)} to infinity",
    }


def _rd_checks(rd: float, limits: list[float]) -> list[str]:
    warnings = []
    if not limits[0] <= rd <= limits[1]:
        warnings.append(_OUTSIDE_WARNING)
    if limits[0] < 0 < limits[1]:
        warnings.append(_CROSSING_WARNING)
    if limits[0] == 0 or limits[1] == 0:
        warnings.append(_ZERO_LIMIT_WARNING)
    return warnings


def _result(path: str, formula: str, rd_point: float, rd_limits: list[float],
            eer_point: float | None, eer_limits: list[float] | None,
            assumptions: list, warnings: list, input_data: dict) -> dict:
    return {
        "measure": "NNT",
        "entry_path": path,
        "level": input_data["level"],
        "formula": formula,
        "nnt_point": _point(rd_point),
        "nnt_ci": _interval(rd_limits[0], rd_limits[1]),
        "rd_point": rd_point,
        "rd_ci": [rd_limits[0], rd_limits[1]],
        "eer_point": eer_point,
        "eer_ci": [eer_limits[0], eer_limits[1]] if eer_limits is not None else None,
        "input_data": copy.deepcopy(input_data),
        "entry_method": f"nnt_conversion_{path}",
        "assumptions": assumptions,
        "warnings": warnings,
    }


_COMMON_ASSUMPTIONS = [
    "The outcome is binary and the same event definition applies to both arms.",
    "RD is the risk in the treatment arm minus the risk in the control arm, so a "
    "positive RD means the treatment increases the event probability (harm, NNTH) "
    "and a negative RD means it lowers it (benefit, NNTB).",
    "Confidence limits are transformed by applying the same formula to each limit "
    "(Daly substitution); the level of the supplied interval is taken as given and "
    "is not re-derived, and the resulting NNT interval does not reflect uncertainty "
    "in the assumed control event rate.",
]

_CER_ASSUMPTION = (
    "The control event rate (CER) is an assumption imported from outside the data "
    "supplied here; the converted NNT changes whenever a different CER is used."
)

_OR_ASSUMPTION = (
    "The odds ratio is not collapsible: with the same OR the converted NNT still "
    "depends on the chosen CER, so report the NNT only for a CER that is relevant "
    "to the population of interest."
)


def nnt_from_rd(rd, rd_ci, level=0.95) -> dict:
    """Convert a risk difference and its confidence interval to an NNT.

    ``rd`` is ``risk_treatment - risk_control`` on the natural scale; a positive
    value means the treatment increases the event probability (harm). ``rd_ci``
    holds the (lower, upper) confidence limits of that risk difference and
    ``level`` records their coverage. An ``rd`` of exactly zero is rejected
    because the NNT is infinite there; a confidence limit of exactly zero yields
    a one-sided unbounded interval with a warning. When the interval spans zero
    the result is the unbounded Altman form ``NNTH a to infinity to NNTB b``.
    """
    level_value = _level(level)
    rd_value = _number(rd, "rd")
    if not _RISK_LIMITS[0] < rd_value < _RISK_LIMITS[1]:
        raise ValueError("rd must lie strictly between -1 and 1")
    if rd_value == 0:
        raise ValueError(
            "rd is exactly zero: the NNT is infinite and no finite point estimate "
            "exists; report the comparison as showing no effect instead"
        )
    limits = _limits(rd_ci, "rd_ci")
    if abs(limits[0]) > 1 or abs(limits[1]) > 1:
        raise ValueError("rd_ci limits must lie within [-1, 1]")

    warnings = _rd_checks(rd_value, limits)
    input_data = {"rd": rd_value, "rd_ci": [limits[0], limits[1]], "level": level_value}
    return _result(
        "rd",
        "NNT = 1/|RD|; RD = risk_treatment - risk_control (Cochrane Handbook v6.5, "
        "Section 15.4.4.1; Altman 1998)",
        rd_value,
        limits,
        None,
        None,
        list(_COMMON_ASSUMPTIONS),
        warnings,
        input_data,
    )


def _effect_to_rd(value: float, cer: float, label: str, formula_eer) -> tuple:
    eer = formula_eer(value, cer)
    if eer >= 1.0:
        raise ValueError(
            f"{label} = {value} with CER = {cer} converts to an experimental event "
            f"rate of {eer}, which leaves the probability domain (EER must be < 1); "
            "the supplied estimate and CER are inconsistent"
        )
    return eer, eer - cer


def _rr_eer(value: float, cer: float) -> float:
    return value * cer


def _or_eer(value: float, cer: float) -> float:
    return value * cer / (1.0 - cer + value * cer)


def _convert_relative(path: str, label: str, estimate, ci, cer, level, eer_function,
                      formula: str, assumptions: list) -> dict:
    level_value = _level(level)
    point = _number(estimate, label)
    if point <= 0:
        raise ValueError(f"{label} must be strictly positive")
    limits = _limits(ci, f"{label}_ci")
    if limits[0] <= 0 or limits[1] <= 0:
        raise ValueError(f"{label}_ci limits must be strictly positive")
    cer_value = _number(cer, "cer")
    if not 0 < cer_value < 1:
        raise ValueError("cer must lie strictly between 0 and 1")

    eer_point, rd_point = _effect_to_rd(point, cer_value, label, eer_function)
    if rd_point == 0:
        raise ValueError(
            f"{label} is exactly 1 (EER equals CER): the NNT is infinite and no "
            "finite point estimate exists; report the comparison as showing no "
            "effect instead"
        )
    eer_limits = []
    rd_limits = []
    for index, limit in enumerate(limits):
        eer_limit, rd_limit = _effect_to_rd(limit, cer_value, f"{label}_ci[{index}]",
                                            eer_function)
        eer_limits.append(eer_limit)
        rd_limits.append(rd_limit)

    warnings = _rd_checks(rd_point, rd_limits)
    warnings.append(_CER_WARNING)
    input_data = {
        label: point,
        f"{label}_ci": [limits[0], limits[1]],
        "cer": cer_value,
        "level": level_value,
    }
    return _result(path, formula, rd_point, rd_limits, eer_point, eer_limits,
                   assumptions, warnings, input_data)


def nnt_from_or(or_, or_ci, cer, level=0.95) -> dict:
    """Convert an odds ratio, its confidence interval and an assumed CER to an NNT.

    ``cer`` is mandatory and is not defaulted: the odds ratio is not collapsible,
    so the NNT depends on the control event rate (``EER = OR*CER/(1-CER+OR*CER)``,
    ``NNT = 1/|EER - CER|``; Cochrane Handbook v6.5, Section 15.4.4.3). An ``or_``
    of exactly 1 is rejected because it implies an infinite NNT at every CER. A
    sign convention note: positive converted RD values mean the treatment
    increases the event probability (NNTH semantics).
    """
    return _convert_relative(
        "or",
        "or",
        or_,
        or_ci,
        cer,
        level,
        _or_eer,
        "EER = OR*CER/(1-CER+OR*CER); NNT = 1/|EER-CER| (Cochrane Handbook v6.5, "
        "Section 15.4.4.3; Altman 1998)",
        list(_COMMON_ASSUMPTIONS) + [_CER_ASSUMPTION, _OR_ASSUMPTION],
    )


def nnt_from_rr(rr, rr_ci, cer, level=0.95) -> dict:
    """Convert a risk ratio, its confidence interval and an assumed CER to an NNT.

    ``cer`` is mandatory and is not defaulted (``EER = RR*CER``,
    ``NNT = 1/|EER - CER|``; Cochrane Handbook v6.5, Section 15.4.4.2). An ``rr``
    of exactly 1 is rejected because it implies an infinite NNT at every CER, and
    estimates whose converted experimental event rate reaches 1 are rejected as
    leaving the probability domain. Positive converted RD values mean the
    treatment increases the event probability (NNTH semantics).
    """
    return _convert_relative(
        "rr",
        "rr",
        rr,
        rr_ci,
        cer,
        level,
        _rr_eer,
        "EER = RR*CER; NNT = 1/|EER-CER| (Cochrane Handbook v6.5, Section 15.4.4.2; "
        "Altman 1998)",
        list(_COMMON_ASSUMPTIONS) + [_CER_ASSUMPTION],
    )
