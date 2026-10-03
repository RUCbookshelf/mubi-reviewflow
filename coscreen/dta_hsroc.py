"""Bivariate <-> HSROC parameterization equivalence for a fitted DTA model.

The bivariate binomial-logit normal model fitted by ``coscreen.dta_analysis``
and the hierarchical SROC (HSROC) model of Rutter and Gatsonis are two
parameterizations of the same likelihood when no covariates are fitted
(Harbord et al. 2007; Arends et al. 2008; Cochrane DTA Handbook ch. 10,
version 1.0, section 10.5.2.3).  This module converts the five fitted
bivariate parameters into the five HSROC parameters and back.

HSROC model (Rutter and Gatsonis 2001; Cochrane DTA Handbook ch. 10 v1.0
section 10.5.2.3, equation on page 26): for study i and disease status
dis = +0.5 (diseased) / -0.5 (non-diseased),

    logit(pi_ij) = (theta_i + alpha_i * dis_ij) * exp(-beta * dis_ij)

with independent random effects alpha_i ~ Normal(lambda, variance_alpha)
and theta_i ~ Normal(theta_bar, variance_theta), where ``lambda`` is the
mean accuracy (log DOR) ``Lambda``, ``theta_bar`` the mean threshold
``Theta``, and ``beta`` the shape (scale) parameter.  ``beta = 0`` gives a
symmetric SROC curve on which the diagnostic odds ratio is constant.

This module is a pure parameterization change: it does not refit the model,
adds no inference, and propagates no estimation uncertainty.
"""

from __future__ import annotations

import copy
import math

_BOUNDARY_TAU_SD = 0.002
_BOUNDARY_ABS_CORRELATION = 0.99
_ROUNDTRIP_TOLERANCE = 1e-8
_BETA_ZERO_TOLERANCE = 1e-12

_BIVARIATE_KEYS = ("logit_sensitivity_mean", "logit_specificity_mean",
                   "tau_sensitivity", "tau_specificity", "random_effect_correlation")
_HSROC_KEYS = ("lambda", "theta", "beta", "variance_alpha", "variance_theta")

_METHOD = ("Rutter-Gatsonis HSROC parameterization via the Harbord et al. (2007) "
           "bivariate-HSROC equivalence transformation (Biostatistics 8:239-251); "
           "model equations per Cochrane DTA Handbook ch.10 v1.0 (2010) "
           "section 10.5.2.3; verified against the published metandi "
           "scheidler_LAG example (Harbord & Whiting 2009, Stata Journal 9:211-229)")

_FORMULAS = {
    "model_hsroc": "logit(pi_ij) = (theta_i + alpha_i*dis_ij) * exp(-beta*dis_ij), "
                   "dis = +0.5 diseased / -0.5 non-diseased; alpha_i ~ N(Lambda, "
                   "variance_alpha), theta_i ~ N(Theta, variance_theta), independent",
    "beta": "beta = ln(tau_specificity / tau_sensitivity)",
    "variance_alpha": "variance_alpha = 2 * tau_sensitivity * tau_specificity * (1 + rho)",
    "variance_theta": "variance_theta = 0.5 * tau_sensitivity * tau_specificity * (1 - rho)",
    "lambda": "lambda (Lambda) = mu_sensitivity * exp(beta/2) + mu_specificity * exp(-beta/2)",
    "theta": "theta (Theta) = (mu_sensitivity * exp(beta/2) - mu_specificity * exp(-beta/2)) / 2",
    "inverse": "mu_sensitivity = (lambda/2 + theta) * exp(-beta/2); "
               "mu_specificity = (lambda/2 - theta) * exp(beta/2); "
               "variance(logit Se) = exp(-beta) * (variance_theta + variance_alpha/4); "
               "variance(logit Sp) = exp(beta) * (variance_theta + variance_alpha/4); "
               "covariance = variance_alpha/4 - variance_theta",
    "summary_sroc": "logit(sensitivity) = lambda * exp(-beta/2) + "
                    "exp(-beta) * logit(1 - specificity) "
                    "(Cochrane DTA Handbook ch.10 v1.0 section 10.5.2.3, p.27)",
}


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


def _require_keys(params: dict, keys: tuple[str, ...], label: str) -> None:
    if not isinstance(params, dict):
        raise ValueError(f"params must be a mapping with the five {label} keys")
    missing = [key for key in keys if key not in params]
    if missing:
        raise ValueError(f"params is missing required {label} keys: {', '.join(missing)}")


def _validated_bivariate(params: dict) -> tuple[float, float, float, float, float]:
    _require_keys(params, _BIVARIATE_KEYS, "bivariate")
    mu_s = _finite_float("logit_sensitivity_mean", params["logit_sensitivity_mean"])
    mu_c = _finite_float("logit_specificity_mean", params["logit_specificity_mean"])
    tau_s = _finite_float("tau_sensitivity", params["tau_sensitivity"])
    tau_c = _finite_float("tau_specificity", params["tau_specificity"])
    rho = _finite_float("random_effect_correlation", params["random_effect_correlation"])
    if tau_s <= _BOUNDARY_TAU_SD or tau_c <= _BOUNDARY_TAU_SD:
        raise ValueError("HSROC transformation unavailable for a near-boundary between-study SD")
    if abs(rho) >= _BOUNDARY_ABS_CORRELATION:
        raise ValueError("HSROC transformation unavailable for a near-boundary correlation")
    variance_s, variance_c, covariance = tau_s * tau_s, tau_c * tau_c, rho * tau_s * tau_c
    if not all(math.isfinite(value) for value in (variance_s, variance_c, covariance)):
        raise ValueError("between-study covariance must be finite and positive definite")
    if variance_s <= 0 or variance_c <= 0 or abs(rho) >= 1:
        raise ValueError("between-study covariance must be finite and positive definite")
    return mu_s, mu_c, tau_s, tau_c, rho


def _hsroc_values_to_bivariate(lambda_: float, theta: float, beta: float,
                               variance_alpha: float, variance_theta: float) -> dict:
    exp_up, exp_down = math.exp(0.5 * beta), math.exp(-0.5 * beta)
    common = variance_theta + 0.25 * variance_alpha
    variance_s = exp_down * exp_down * common
    variance_c = exp_up * exp_up * common
    covariance = 0.25 * variance_alpha - variance_theta
    if not all(math.isfinite(value) for value in (variance_s, variance_c, covariance)):
        raise ValueError("derived between-study covariance must be finite and positive definite")
    if variance_s <= 0 or variance_c <= 0:
        raise ValueError("derived between-study covariance must be finite and positive definite")
    sd_s, sd_c = math.sqrt(variance_s), math.sqrt(variance_c)
    rho = covariance / (sd_s * sd_c)
    if not math.isfinite(rho) or abs(rho) >= 1:
        raise ValueError("derived between-study covariance must be finite and positive definite")
    return {"logit_sensitivity_mean": (0.5 * lambda_ + theta) * exp_down,
            "logit_specificity_mean": (0.5 * lambda_ - theta) * exp_up,
            "tau_sensitivity": sd_s,
            "tau_specificity": sd_c,
            "random_effect_correlation": rho}


def _beta_warning(beta: float) -> str:
    if abs(beta) <= _BETA_ZERO_TOLERANCE:
        return ("HSROC shape parameter beta is 0 (tau_sensitivity equals tau_specificity): "
                "the SROC curve is symmetric and the diagnostic odds ratio is constant "
                "along the curve.")
    return (f"HSROC shape parameter beta is {beta:.6g}, not 0: accuracy varies with "
            "threshold, so the SROC curve is asymmetric (beta = 0, equivalently scale "
            "factor exp(beta) = 1, gives the symmetric curve). No significance test on "
            "beta is performed.")


def _shared_warnings(beta: float) -> list[str]:
    return [_beta_warning(beta),
            "Results are conditional on the supplied fitted parameters; the "
            "transformation neither refits the model nor propagates estimation "
            "uncertainty.",
            "The returned parameters are a reparameterization of the same fit, not a "
            "confidence or prediction region; no confidence interval is produced for "
            "them."]


def hsroc_from_bivariate(params: dict, *, source_provenance=None) -> dict:
    """Transform fitted bivariate DTA parameters into the HSROC parameterization.

    ``params`` must supply the five keys used by ``coscreen.dta_analysis.synthesize``:
    ``logit_sensitivity_mean``, ``logit_specificity_mean``, ``tau_sensitivity``,
    ``tau_specificity`` (between-study SDs on the logit scale) and
    ``random_effect_correlation``.  Validation thresholds match
    ``coscreen.dta_prediction_region`` (tau > 0.002, |rho| < 0.99, finite values).

    Returns ``lambda`` (mean accuracy Lambda), ``theta`` (mean threshold Theta),
    ``beta`` (shape; 0 gives the symmetric SROC curve), ``variance_alpha``
    (accuracy random-effect variance), ``variance_theta`` (threshold
    random-effect variance), the SROC summary-curve coefficients, a
    ``roundtrip_check`` (maximum absolute difference between the input
    parameters and a full HSROC -> bivariate return pass), a deep copy of
    ``input_data``, ``warnings``, ``method``, and the formula identification.
    """
    mu_s, mu_c, tau_s, tau_c, rho = _validated_bivariate(params)
    product = tau_s * tau_c
    beta = math.log(tau_c / tau_s)
    scale_up, scale_down = math.sqrt(tau_c / tau_s), math.sqrt(tau_s / tau_c)
    variance_alpha = 2.0 * product * (1.0 + rho)
    variance_theta = 0.5 * product * (1.0 - rho)
    lambda_ = mu_s * scale_up + mu_c * scale_down
    theta = 0.5 * (mu_s * scale_up - mu_c * scale_down)
    if not all(math.isfinite(value) for value in
               (beta, lambda_, theta, variance_alpha, variance_theta)):
        raise ValueError("HSROC parameters must be finite for valid bivariate inputs")

    reverse = _hsroc_values_to_bivariate(lambda_, theta, beta, variance_alpha, variance_theta)
    roundtrip = max(abs(reverse[key] - value)
                    for key, value in zip(_BIVARIATE_KEYS, (mu_s, mu_c, tau_s, tau_c, rho)))
    if not roundtrip <= _ROUNDTRIP_TOLERANCE:
        raise RuntimeError("round-trip check failed; transformation is numerically unstable "
                           "for these parameters")

    return {"lambda": lambda_, "theta": theta, "beta": beta,
            "variance_alpha": variance_alpha, "variance_theta": variance_theta,
            "summary_sroc_curve": {
                "logit_intercept": lambda_ * scale_down,
                "logit_slope_fpr": scale_down * scale_down,
                "formula": _FORMULAS["summary_sroc"]},
            "roundtrip_check": roundtrip,
            "input_data": copy.deepcopy(params),
            "source_provenance": copy.deepcopy(source_provenance) if source_provenance is not None else None,
            "warnings": _shared_warnings(beta),
            "method": _METHOD,
            "formulas": dict(_FORMULAS)}


def bivariate_from_hsroc(params: dict, *, source_provenance=None) -> dict:
    """Inverse transform: HSROC parameters back to the bivariate parameterization.

    ``params`` must supply ``lambda``, ``theta``, ``beta``, ``variance_alpha``
    and ``variance_theta`` (both variances strictly positive).  Returns the
    five bivariate field names of ``coscreen.dta_analysis.synthesize``, a
    ``roundtrip_check`` against the supplied HSROC parameters, a deep copy of
    ``input_data``, ``warnings``, ``method``, and the formula identification.
    """
    _require_keys(params, _HSROC_KEYS, "HSROC")
    lambda_ = _finite_float("lambda", params["lambda"])
    theta = _finite_float("theta", params["theta"])
    beta = _finite_float("beta", params["beta"])
    variance_alpha = _finite_float("variance_alpha", params["variance_alpha"])
    variance_theta = _finite_float("variance_theta", params["variance_theta"])
    if variance_alpha <= 0 or variance_theta <= 0:
        raise ValueError("HSROC variances must be positive")

    reverse = _hsroc_values_to_bivariate(lambda_, theta, beta, variance_alpha, variance_theta)
    forward_ratio = reverse["tau_specificity"] / reverse["tau_sensitivity"]
    forward_beta = math.log(forward_ratio)
    forward_product = reverse["tau_sensitivity"] * reverse["tau_specificity"]
    forward_rho = reverse["random_effect_correlation"]
    forward_up = math.sqrt(forward_ratio)
    forward_down = math.sqrt(1.0 / forward_ratio)
    forward_lambda = (reverse["logit_sensitivity_mean"] * forward_up +
                      reverse["logit_specificity_mean"] * forward_down)
    forward_theta = 0.5 * (reverse["logit_sensitivity_mean"] * forward_up -
                           reverse["logit_specificity_mean"] * forward_down)
    forward_alpha = 2.0 * forward_product * (1.0 + forward_rho)
    forward_vartheta = 0.5 * forward_product * (1.0 - forward_rho)
    roundtrip = max(abs(forward_lambda - lambda_), abs(forward_theta - theta),
                    abs(forward_beta - beta), abs(forward_alpha - variance_alpha),
                    abs(forward_vartheta - variance_theta))
    if not roundtrip <= _ROUNDTRIP_TOLERANCE:
        raise RuntimeError("round-trip check failed; transformation is numerically unstable "
                           "for these parameters")

    return {**reverse, "roundtrip_check": roundtrip,
            "input_data": copy.deepcopy(params),
            "source_provenance": copy.deepcopy(source_provenance) if source_provenance is not None else None,
            "warnings": _shared_warnings(beta),
            "method": _METHOD,
            "formulas": dict(_FORMULAS)}
