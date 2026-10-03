"""Conditional between-study prediction region for a fitted bivariate DTA model."""

from __future__ import annotations

import math

_CHI_SQUARE_2_95 = 5.991464547107979
_BOUNDARY_TAU_SD = 0.002
_BOUNDARY_ABS_CORRELATION = 0.99
_BOUNDARY_SEGMENTS = 120


def _finite_float(name: str, value: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite")
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _expit(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1.0 + exp_value)


def prediction_region(logit_sensitivity_mean: float, logit_specificity_mean: float,
                      tau_sensitivity: float, tau_specificity: float,
                      random_effect_correlation: float) -> dict:
    """Return the 95% ellipse of latent study effects, transformed to probability scale.

    Inputs use the corresponding field names from ``dta_analysis.synthesize``.
    The returned region is conditional on the supplied fitted parameters.
    """
    mu_s = _finite_float("logit_sensitivity_mean", logit_sensitivity_mean)
    mu_c = _finite_float("logit_specificity_mean", logit_specificity_mean)
    tau_s = _finite_float("tau_sensitivity", tau_sensitivity)
    tau_c = _finite_float("tau_specificity", tau_specificity)
    rho = _finite_float("random_effect_correlation", random_effect_correlation)

    if tau_s <= _BOUNDARY_TAU_SD or tau_c <= _BOUNDARY_TAU_SD:
        raise ValueError("prediction region unavailable for a near-boundary between-study SD")
    if abs(rho) >= _BOUNDARY_ABS_CORRELATION:
        raise ValueError("prediction region unavailable for a near-boundary correlation")

    variance_s = tau_s * tau_s
    variance_c = tau_c * tau_c
    covariance = rho * tau_s * tau_c
    if not all(math.isfinite(value) for value in (variance_s, variance_c, covariance)):
        raise ValueError("between-study covariance must be finite and positive definite")
    if variance_s <= 0 or variance_c <= 0 or abs(rho) >= 1:
        raise ValueError("between-study covariance must be finite and positive definite")

    radius = math.sqrt(_CHI_SQUARE_2_95)
    chol_c = tau_c * math.sqrt(1.0 - rho * rho)
    points = []
    for index in range(_BOUNDARY_SEGMENTS):
        angle = 2.0 * math.pi * index / _BOUNDARY_SEGMENTS
        unit_s, unit_c = radius * math.cos(angle), radius * math.sin(angle)
        logit_s = mu_s + tau_s * unit_s
        logit_c = mu_c + tau_c * rho * unit_s + chol_c * unit_c
        if not math.isfinite(logit_s) or not math.isfinite(logit_c):
            raise ValueError("prediction region coordinates must be finite")
        points.append({"logit_sensitivity": logit_s,
                       "logit_specificity": logit_c,
                       "sensitivity": _expit(logit_s),
                       "specificity": _expit(logit_c)})
    points.append(points[0].copy())

    return {
        "region_type": "conditional_between_study_prediction_region",
        "confidence_level": 0.95,
        "chi_square_df": 2,
        "chi_square_critical_value": _CHI_SQUARE_2_95,
        "parameters": {
            "logit_sensitivity_mean": mu_s,
            "logit_specificity_mean": mu_c,
            "tau_sensitivity": tau_s,
            "tau_specificity": tau_c,
            "random_effect_correlation": rho,
            "covariance_matrix": [[variance_s, covariance],
                                  [covariance, variance_c]],
        },
        "points": points,
    }
