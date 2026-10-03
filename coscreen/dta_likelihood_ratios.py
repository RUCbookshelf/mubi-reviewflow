"""Pooled likelihood ratios (LR+ / LR-) for diagnostic test accuracy meta-analysis.

Two explicitly labelled synthesis paths are provided.

``summary_likelihood_ratios`` derives LR+ and LR- at the summary operating
point of an already fitted bivariate model, with confidence intervals from the
delta method on the log scale using the *estimation covariance of the two
fitted means* (the same covariance semantics as the ``Sigma`` argument of
``mada::SummaryPts``).  This is the recommended path: the Cochrane DTA
Handbook states that summary values and confidence intervals can be derived
for the positive and negative likelihood ratios at the summary point
(ch. 10, version 1.0, section 10.5.2, p. 23) and warns that separate pooling
of likelihood ratios "ignores correlations between positive and negative
likelihood ratios, and theoretically can produce estimates which are
impossible" (section 10.4.2, pp. 19-20; Zwinderman & Bossuyt, Stat Med
2008;27:687-697).

``pool_likelihood_ratios`` performs the exploratory direct inverse-variance
pooling of per-study log likelihood ratios (Irwig et al. 1995), fixed effect
or DerSimonian-Laird random effects, with the project's explicit zero-cell
policy.  It is labelled as the approach the Handbook advises against as a
routine summary.

Likelihood ratios are defined as in Handbook section 10.2.3.3 (p. 10-11):
LR+ = sensitivity / (1 - specificity), LR- = (1 - sensitivity) / specificity.
No Bayesian post-test probability update is performed anywhere in this
module.
"""

from __future__ import annotations

import copy
import math
from statistics import NormalDist

_EXTREME_PROBABILITY = 0.005  # near-boundary warning threshold for Se / Sp
_ZERO_CORRECTION = 0.5
_DEFAULT_SEED = 20260924

_METHOD_SUMMARY = ("positive and negative likelihood ratios derived at the bivariate "
                   "summary point by the delta method on the log scale using the "
                   "estimation covariance of the fitted means (Zwinderman & Bossuyt "
                   "Stat Med 2008;27:687-697; covariance semantics of mada::SummaryPts; "
                   "definitions and derivation licence per Cochrane DTA Handbook ch.10 "
                   "v1.0 (2010) sections 10.2.3.3 and 10.5.2)")
_METHOD_POOLED = ("inverse-variance pooling of per-study log likelihood ratios "
                  "(Irwig et al. J Clin Epidemiol 1995;48:119-130), fixed effect or "
                  "DerSimonian-Laird random effects; exploratory only - separate "
                  "pooling of likelihood ratios ignores their correlation (Cochrane "
                  "DTA Handbook ch.10 v1.0 (2010) section 10.4.2; Zwinderman & "
                  "Bossuyt Stat Med 2008;27:687-697)")

_FORMULAS_SUMMARY = {
    "definitions": "LR+ = Se/(1-Sp), LR- = (1-Se)/Sp (Handbook ch.10 v1.0 section 10.2.3.3)",
    "log_scale": "ln LR+ = ln Se - ln(1-Sp); ln LR- = ln(1-Se) - ln Sp, evaluated at the "
                 "fitted means Se = expit(mu_s), Sp = expit(mu_c)",
    "delta_variance": "grad ln LR+ = (1-Se, Sp), grad ln LR- = (-Se, -(1-Sp)); "
                      "Var(ln LR) = g Var g' with the 2x2 estimation covariance of the "
                      "fitted means, so the covariance term (rho) is included",
    "ci": "exp(ln LR +/- z * SE(ln LR)), z = NormalDist().inv_cdf(1 - (1-confidence)/2)",
    "covariance_semantics": "mean_covariance is the 2x2 estimation covariance of the "
                            "fitted means (inverse observed information), not the "
                            "between-study covariance matrix",
}
_FORMULAS_POOLED = {
    "per_study_ln_lr_plus": "ln(TP/(TP+FN)) - ln(FP/(FP+TN))",
    "per_study_var_ln_lr_plus": "1/TP - 1/(TP+FN) + 1/FP - 1/(FP+TN)",
    "per_study_ln_lr_minus": "ln(FN/(TP+FN)) - ln(TN/(FP+TN))",
    "per_study_var_ln_lr_minus": "1/FN - 1/(TP+FN) + 1/TN - 1/(FP+TN)",
    "pooling": "inverse variance: w = 1/v, pooled = sum(w*y)/sum(w), SE = sqrt(1/sum(w))",
    "random_effects": "DerSimonian-Laird moment estimate of tau^2 on the log LR scale; "
                      "weights 1/(v + tau^2)",
    "zero_cells": "explicit choice: correction='none' excludes tables with any zero cell "
                  "(at least one log LR is 0 or infinite); correction=0.5 adds 0.5 to all "
                  "four cells of such tables only (mada::madad correction.control='single', "
                  "metafor::escalc add=0.5 to='only0')",
}
_FAGAN_WARNING = ("Likelihood ratios turn a pre-test probability into a post-test "
                  "probability only in combination with a setting-specific pre-test "
                  "probability (Fagan nomogram / Bayes theorem). This module reports the "
                  "pooled ratios only and performs no Bayesian post-test probability "
                  "calculation (Handbook ch.10 v1.0 section 10.2.3.3).")
_SEPARATE_POOLING_WARNING = ("Separate pooling of likelihood ratios ignores the "
                             "correlation between LR+ and LR- and can produce impossible "
                             "(LR+, LR-) pairs; the Cochrane DTA Handbook lists it among "
                             "methods not routinely used (ch.10 v1.0 section 10.4.2, "
                             "Zwinderman & Bossuyt Stat Med 2008;27:687-697). Prefer "
                             "deriving the ratios at a fitted bivariate summary point "
                             "(summary_likelihood_ratios); use this result only for "
                             "exploratory comparison.")


def _finite_float(name: str, value) -> float:
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


def _log_expit(value: float) -> float:
    # ln(expit(value)), stable at both ends.
    if value < 0:
        return value - math.log1p(math.exp(value))
    return -math.log1p(math.exp(-value))


def _log_one_minus_expit(value: float) -> float:
    # ln(1 - expit(value)), stable at both ends.
    return _log_expit(-value)


def _z_value(confidence: float) -> float:
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must lie strictly between 0 and 1")
    return NormalDist().inv_cdf(1.0 - (1.0 - confidence) / 2.0)


def _validated_covariance(mean_covariance) -> tuple[float, float, float] | None:
    if mean_covariance is None:
        return None
    if isinstance(mean_covariance, (int, float, bool)) or not hasattr(mean_covariance, "__len__"):
        raise ValueError("mean_covariance must be a 2x2 covariance matrix of the fitted means")
    if len(mean_covariance) != 2 or any(len(row) != 2 for row in mean_covariance):
        raise ValueError("mean_covariance must be a 2x2 covariance matrix of the fitted means")
    values = [[_finite_float("mean_covariance entry", entry) for entry in row]
              for row in mean_covariance]
    variance_s, covariance = values[0]
    _, variance_c = values[1]
    tolerance = 1e-9 * max(1.0, abs(values[0][1]), abs(values[1][0]))
    if abs(values[0][1] - values[1][0]) > tolerance:
        raise ValueError("mean_covariance must be symmetric")
    if variance_s <= 0 or variance_c <= 0:
        raise ValueError("mean_covariance variances must be positive")
    if variance_s * variance_c - covariance * covariance <= 0:
        raise ValueError("mean_covariance must be positive definite")
    return variance_s, variance_c, covariance


def summary_likelihood_ratios(logit_sensitivity_mean, logit_specificity_mean,
                              mean_covariance=None, *, confidence: float = 0.95,
                              n_samples: int = 0, random_seed: int = _DEFAULT_SEED,
                              source_provenance=None) -> dict:
    """Derive LR+ and LR- at a fitted bivariate summary point.

    Inputs use the field names of ``coscreen.dta_analysis.synthesize``:
    ``logit_sensitivity_mean`` and ``logit_specificity_mean`` are the fitted
    means on the logit scale; ``mean_covariance`` is the 2x2 estimation
    covariance of those two means (inverse observed information of the fit -
    the block ``covariance[:2, :2]`` computed inside ``synthesize`` - not the
    between-study covariance).  The same covariance semantics as the
    ``Sigma`` argument of ``mada::SummaryPts``: the standard errors of the
    fitted means and their covariance.  Without it, point estimates are
    returned with a warning and no confidence interval.

    The delta method evaluates the gradient of each log ratio at the summary
    point and propagates the full covariance, so the correlation between the
    two fitted means enters both interval widths.  Algebraically recombining
    the separate sensitivity and specificity intervals would drop that term
    and is deliberately not offered.

    ``n_samples > 0`` adds a Monte Carlo percentile cross-check that draws the
    fitted means from their bivariate normal estimation distribution (the
    documented procedure of ``mada::SummaryPts``).
    """
    mu_s = _finite_float("logit_sensitivity_mean", logit_sensitivity_mean)
    mu_c = _finite_float("logit_specificity_mean", logit_specificity_mean)
    validated = _validated_covariance(mean_covariance)
    z = _z_value(confidence)
    if n_samples:
        if type(n_samples) is not int or n_samples < 1:
            raise ValueError("n_samples must be a positive integer")

    se_pooled = _expit(mu_s)
    sp_pooled = _expit(mu_c)
    if min(se_pooled, 1.0 - se_pooled, sp_pooled, 1.0 - sp_pooled) <= 1e-12:
        raise ValueError("pooled sensitivity or specificity is at the numerical boundary of "
                         "the probability scale (0 or 1 within floating point precision); "
                         "the corresponding likelihood ratio is 0 or infinite and no finite "
                         "summary ratio exists")
    extreme = min(se_pooled, sp_pooled) < _EXTREME_PROBABILITY or \
        max(se_pooled, sp_pooled) > 1.0 - _EXTREME_PROBABILITY

    ln_lr_plus = _log_expit(mu_s) - _log_one_minus_expit(mu_c)
    ln_lr_minus = _log_one_minus_expit(mu_s) - _log_expit(mu_c)
    # Analytic gradients at the summary point; both carry the covariance term.
    gradient_plus = (1.0 - se_pooled, sp_pooled)
    gradient_minus = (-se_pooled, -(1.0 - sp_pooled))

    se_ln_plus = se_ln_minus = None
    ci_plus = ci_minus = None
    warnings = [_SEPARATE_POOLING_WARNING, _FAGAN_WARNING]
    if validated is not None:
        variance_s, variance_c, cov = validated
        se_ln_plus = math.sqrt(gradient_plus[0] ** 2 * variance_s +
                               gradient_plus[1] ** 2 * variance_c +
                               2.0 * gradient_plus[0] * gradient_plus[1] * cov)
        se_ln_minus = math.sqrt(gradient_minus[0] ** 2 * variance_s +
                                gradient_minus[1] ** 2 * variance_c +
                                2.0 * gradient_minus[0] * gradient_minus[1] * cov)
        ci_plus = [math.exp(ln_lr_plus - z * se_ln_plus), math.exp(ln_lr_plus + z * se_ln_plus)]
        ci_minus = [math.exp(ln_lr_minus - z * se_ln_minus),
                    math.exp(ln_lr_minus + z * se_ln_minus)]
    else:
        warnings.append("The estimation covariance of the fitted means was not supplied, so "
                        "no confidence interval is reported; the point ratios are conditional "
                        "on the supplied fit. Pass the 2x2 inverse observed information block "
                        "for the two means of the bivariate fit to obtain intervals.")

    if extreme:
        warnings.append("A pooled sensitivity or specificity is within "
                        f"{_EXTREME_PROBABILITY} of 0 or 1: the likelihood ratio is extreme "
                        "and the Wald interval on the log scale is unreliable here; report "
                        "the summary point itself instead.")
    if n_samples:
        warnings.append("The Monte Carlo percentile cross-check is an approximation "
                        "diagnostic only; the reported intervals are the delta-method ones.")

    result = {"method": _METHOD_SUMMARY,
              "lr_positive": math.exp(ln_lr_plus), "lr_negative": math.exp(ln_lr_minus),
              "ln_lr_positive": ln_lr_plus, "ln_lr_negative": ln_lr_minus,
              "se_ln_lr_positive": se_ln_plus, "se_ln_lr_negative": se_ln_minus,
              "ci_lr_positive": ci_plus, "ci_lr_negative": ci_minus,
              "ci_method": ("delta method (Wald) on the log scale using the estimation "
                            "covariance of the fitted means" if validated is not None else None),
              "confidence": confidence,
              "summary_point": {"sensitivity": se_pooled, "specificity": sp_pooled,
                                "logit_sensitivity_mean": mu_s,
                                "logit_specificity_mean": mu_c},
              "logit_gradient_ln_lr_positive": list(gradient_plus),
              "logit_gradient_ln_lr_negative": list(gradient_minus),
              "mean_covariance": [[validated[0], validated[2]],
                                  [validated[2], validated[1]]] if validated else None,
              "input_data": {"logit_sensitivity_mean": mu_s,
                             "logit_specificity_mean": mu_c,
                             "mean_covariance": copy.deepcopy(mean_covariance),
                             "confidence": confidence, "n_samples": n_samples},
              "source_provenance": copy.deepcopy(source_provenance)
              if source_provenance is not None else None,
              "warnings": warnings,
              "formulas": dict(_FORMULAS_SUMMARY)}
    if confidence == 0.95:
        result["ci_95_lr_positive"] = ci_plus
        result["ci_95_lr_negative"] = ci_minus
    if n_samples:
        result["sampling_cross_check"] = _sampling_cross_check(
            mu_s, mu_c, validated, n_samples, random_seed, confidence)
    return result


def _sampling_cross_check(mu_s: float, mu_c: float, covariance, n_samples: int,
                          random_seed: int, confidence: float) -> dict:
    import numpy as np

    variance_s, variance_c, cov = covariance
    rng = np.random.default_rng(random_seed)
    mean = np.array([mu_s, mu_c])
    sigma = np.array([[variance_s, cov], [cov, variance_c]])
    draws = rng.multivariate_normal(mean, sigma, size=n_samples)
    logit_s, logit_c = draws.T
    ln_plus = -np.logaddexp(0.0, -logit_s) + np.logaddexp(0.0, logit_c)
    ln_minus = -np.logaddexp(0.0, logit_s) + np.logaddexp(0.0, -logit_c)
    level = 100.0 * confidence
    lower, upper = (100.0 - level) / 2.0, 100.0 - (100.0 - level) / 2.0
    result = {"method": "percentile Monte Carlo: fitted means drawn from their bivariate "
                       "normal estimation distribution, likelihood ratios evaluated per "
                       "draw (documented mada::SummaryPts procedure, rmvnorm equivalent)",
              "n_samples": n_samples, "random_seed": random_seed,
              "ci_lr_positive": [float(np.exp(np.percentile(ln_plus, lower))),
                                 float(np.exp(np.percentile(ln_plus, upper)))],
              "ci_lr_negative": [float(np.exp(np.percentile(ln_minus, lower))),
                                 float(np.exp(np.percentile(ln_minus, upper)))],
              "mean_lr_positive": float(np.exp(ln_plus).mean()),
              "mean_lr_negative": float(np.exp(ln_minus).mean()),
              "median_lr_positive": float(np.exp(np.percentile(ln_plus, 50.0))),
              "median_lr_negative": float(np.exp(np.percentile(ln_minus, 50.0)))}
    if confidence == 0.95:
        result["ci_95_lr_positive"] = result["ci_lr_positive"]
        result["ci_95_lr_negative"] = result["ci_lr_negative"]
    return result


def _validated_tables(tables) -> list[dict]:
    if isinstance(tables, dict) or not hasattr(tables, "__iter__"):
        raise ValueError("tables must be an iterable of mappings with tp/fp/fn/tn counts")
    validated = []
    for index, table in enumerate(tables):
        if not isinstance(table, dict):
            raise ValueError("each table must be a mapping with tp/fp/fn/tn counts")
        counts = {}
        for cell in ("tp", "fp", "fn", "tn"):
            if cell not in table:
                raise ValueError(f"table {index + 1} is missing the '{cell}' count")
            value = table[cell]
            if isinstance(value, bool) or type(value) is not int:
                raise ValueError("TP/FP/FN/TN must be nonnegative integers")
            if value < 0:
                raise ValueError("TP/FP/FN/TN must be nonnegative integers")
            counts[cell] = value
        if counts["tp"] + counts["fn"] == 0 or counts["tn"] + counts["fp"] == 0:
            raise ValueError("both diseased and non-diseased groups must contain participants")
        study_id = table.get("study_id", f"study {index + 1}")
        validated.append({"study_id": str(study_id), **counts,
                          "raw": {key: copy.deepcopy(value) for key, value in table.items()}})
    return validated


def _study_log_ratios(table: dict, corrected: bool) -> dict:
    tp, fp = float(table["tp"]), float(table["fp"])
    fn, tn = float(table["fn"]), float(table["tn"])
    if corrected:
        tp, fp, fn, tn = tp + 0.5, fp + 0.5, fn + 0.5, tn + 0.5
    diseased, non_diseased = tp + fn, tn + fp
    ln_plus = math.log(tp) - math.log(diseased) - math.log(fp) + math.log(non_diseased)
    var_plus = 1.0 / tp - 1.0 / diseased + 1.0 / fp - 1.0 / non_diseased
    ln_minus = math.log(fn) - math.log(diseased) - math.log(tn) + math.log(non_diseased)
    var_minus = 1.0 / fn - 1.0 / diseased + 1.0 / tn - 1.0 / non_diseased
    return {"ln_lr_positive": ln_plus, "var_ln_lr_positive": var_plus,
            "ln_lr_negative": ln_minus, "var_ln_lr_negative": var_minus}


def _inverse_variance(values: list[tuple[float, float]]) -> dict:
    weights = [1.0 / variance for _, variance in values]
    total = sum(weights)
    estimate = sum(weight * value for weight, (value, _) in zip(weights, values)) / total
    se = math.sqrt(1.0 / total)
    q = sum(weight * (value - estimate) ** 2 for weight, (value, _) in zip(weights, values))
    df = len(values) - 1
    tau2 = 0.0
    if df > 0 and q > df:
        c = total - sum(weight * weight for weight in weights) / total
        tau2 = (q - df) / c
    return {"estimate": estimate, "se": se, "q": q, "q_df": df,
            "i2_percent": max(0.0, (q - df) / q) * 100.0 if q > 0 else 0.0,
            "tau2": tau2}


def pool_likelihood_ratios(tables, *, correction="none", random_effects: bool = False,
                           confidence: float = 0.95, source_provenance=None) -> dict:
    """Pool per-study log likelihood ratios by inverse-variance weighting.

    ``tables`` is an iterable of mappings with nonnegative integer
    ``tp``/``fp``/``fn``/``tn`` counts and an optional ``study_id``.  Each
    study contributes ln LR+ and ln LR- with delta-method variances
    (Irwig et al. 1995); the two ratios are pooled in two separate
    inverse-variance syntheses (fixed effect, or DerSimonian-Laird random
    effects with ``random_effects=True``).

    ``correction`` is required and must be ``"none"`` or ``0.5``, matching
    ``coscreen.zero_cell_effects``.  With ``"none"`` (the default) any table
    containing a zero cell is excluded, because at least one of its log
    likelihood ratios is 0 or infinite; zero cells are never silently
    imputed.  With ``0.5``, 0.5 is added to all four cells of tables that
    contain a zero (and only of those), as in ``mada::madad`` with
    ``correction.control="single"`` and ``metafor::escalc(add=0.5, to="only0")``.

    The result is explicitly labelled exploratory: pooling LR+ and LR-
    separately ignores their correlation (Handbook section 10.4.2;
    Zwinderman & Bossuyt 2008).
    """
    if correction == "none":
        correction_value = None
    elif isinstance(correction, bool):
        raise ValueError('correction must be "none" or 0.5')
    elif isinstance(correction, (int, float)) and correction == _ZERO_CORRECTION:
        correction_value = _ZERO_CORRECTION
    else:
        raise ValueError('correction must be "none" or 0.5')

    rows = _validated_tables(tables)
    z = _z_value(confidence)
    if len(rows) < 2:
        raise ValueError("at least two studies are required to pool likelihood ratios")

    excluded, zero_rows, kept = [], [], []
    for row in rows:
        cells = (row["tp"], row["fp"], row["fn"], row["tn"])
        if 0 in cells:
            if correction_value is None:
                excluded.append({"study_id": row["study_id"],
                                 "tp": row["tp"], "fp": row["fp"],
                                 "fn": row["fn"], "tn": row["tn"],
                                 "reason": "a cell is zero, so at least one of ln LR+/ln LR- "
                                           "is 0 or infinite (undefined on the log scale)"})
                continue
            zero_rows.append(row)
        kept.append(row)
    if len(kept) < 2:
        raise ValueError("at least two studies with defined log likelihood ratios are "
                         "required after zero-cell handling; lower the exclusion count by "
                         "choosing an explicit correction or collect further studies")

    per_study, plus_pairs, minus_pairs = [], [], []
    for row in kept:
        ratios = _study_log_ratios(row, corrected=correction_value is not None)
        per_study.append({"study_id": row["study_id"], **ratios})
        plus_pairs.append((ratios["ln_lr_positive"], ratios["var_ln_lr_positive"]))
        minus_pairs.append((ratios["ln_lr_negative"], ratios["var_ln_lr_negative"]))

    def _arm(pairs):
        fixed = _inverse_variance(pairs)
        weights_used = random_effects and fixed["tau2"] > 0.0
        if weights_used:
            adjusted = [(value, variance + fixed["tau2"]) for value, variance in pairs]
            pooled = _inverse_variance(adjusted)
        else:
            pooled = fixed
        return {"ln_lr": pooled["estimate"], "se_ln_lr": pooled["se"],
                "ci_lr": [math.exp(pooled["estimate"] - z * pooled["se"]),
                          math.exp(pooled["estimate"] + z * pooled["se"])],
                "heterogeneity": {key: fixed[key] for key in ("q", "q_df", "i2_percent", "tau2")}}

    positive, negative = _arm(plus_pairs), _arm(minus_pairs)

    warnings = [_SEPARATE_POOLING_WARNING, _FAGAN_WARNING]
    if excluded:
        warnings.append(f"{len(excluded)} table(s) with a zero cell were excluded because at "
                        "least one log likelihood ratio is 0 or infinite; zero cells are "
                        "never silently imputed. Choosing correction=0.5 keeps them with an "
                        "explicit continuity correction; compare both settings.")
    if zero_rows:
        warnings.append(f"The selected 0.5 correction was added to all four cells of "
                        f"{len(zero_rows)} table(s) containing a zero; compare this "
                        "sensitivity with the uncorrected result.")
    if random_effects:
        warnings.append("Random-effects weights are DerSimonian-Laird moment estimates on "
                        "the log likelihood ratio scale; with few studies tau^2 itself is "
                        "uncertain and the intervals should be read as indicative.")

    return {"method": _METHOD_POOLED,
            "effect_model": ("random effects (DerSimonian-Laird)" if random_effects
                             else "fixed effect (inverse variance)"),
            "n_studies": len(rows), "n_used": len(kept),
            "excluded_studies": excluded,
            "zero_cell_policy": ("none: tables with a zero cell excluded"
                                 if correction_value is None else
                                 "0.5 added to all four cells of tables with a zero only"),
            "positive": positive, "negative": negative,
            "lr_positive": math.exp(positive["ln_lr"]),
            "lr_negative": math.exp(negative["ln_lr"]),
            "confidence": confidence, "per_study": per_study,
            "input_data": [row["raw"] for row in rows],
            "source_provenance": copy.deepcopy(source_provenance)
            if source_provenance is not None else None,
            "warnings": warnings,
            "formulas": dict(_FORMULAS_POOLED)}
