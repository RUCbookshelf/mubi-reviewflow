"""Exploratory bivariate logit meta-regression on a numeric test threshold."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path

DbPath = str | Path


def _number(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite numeric threshold")
    try:
        result = float(str(value).strip())
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be a finite numeric threshold") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite numeric threshold")
    return result


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value.strip()


def synthesize(db: DbPath, index_test: str, target_condition: str,
               reference_standard: str | None = None,
               threshold_by_study: Mapping[str, str | float] | None = None) -> dict:
    """Fit a bivariate threshold meta-regression using one row per study.

    If a study has reported multiple thresholds, ``threshold_by_study`` must
    choose one numeric threshold for it.  The model assumes a linear
    association between threshold and both logit sensitivity and logit
    specificity, with correlated normal study random effects.
    """
    import numpy as np
    from numpy.polynomial.hermite import hermgauss
    from scipy.optimize import minimize
    from scipy.special import expit, gammaln, log_expit, logsumexp

    from coscreen.dta_analysis import list_results

    index_test = _required_text(index_test, "index_test")
    target_condition = _required_text(target_condition, "target_condition")
    if reference_standard is not None:
        reference_standard = _required_text(reference_standard, "reference_standard")
    rows = [row for row in list_results(db)
            if row["index_test"] == index_test and row["target_condition"] == target_condition]
    if not rows:
        raise ValueError("no diagnostic accuracy results for this test and condition")
    if threshold_by_study is not None and not isinstance(threshold_by_study, Mapping):
        raise ValueError("threshold_by_study must map study IDs to numeric thresholds")

    standards = {row["reference_standard"] for row in rows}
    if reference_standard is None:
        if len(standards) != 1:
            raise ValueError("select one reference standard")
        reference_standard = next(iter(standards))
    elif reference_standard not in standards:
        raise ValueError("no diagnostic accuracy results for this reference standard")
    rows = [row for row in rows if row["reference_standard"] == reference_standard]

    grouped = defaultdict(list)
    for row in rows:
        row = dict(row)
        for key in ("study_id", "index_test", "target_condition", "reference_standard", "threshold",
                    "source_key"):
            row[key] = _required_text(row[key], key)
        grouped[row["study_id"]].append(row)

    if threshold_by_study is not None:
        if any(not isinstance(study_id, str) or not study_id.strip()
               for study_id in threshold_by_study):
            raise ValueError("threshold_by_study keys must be non-empty study IDs")
        unknown = set(threshold_by_study) - set(grouped)
        if unknown:
            raise ValueError("threshold_by_study contains unknown study IDs")

    selected = []
    for study_id, study_rows in grouped.items():
        choice = threshold_by_study.get(study_id) if threshold_by_study is not None else None
        if choice is None:
            if len(study_rows) != 1:
                raise ValueError(
                    f"study {study_id} has multiple thresholds; select one in threshold_by_study")
            row = study_rows[0]
            row["threshold_value"] = _number(row["threshold"], "threshold")
            selected.append(row)
            continue
        value = _number(choice, f"threshold for study {study_id}")
        matches = []
        for row in study_rows:
            try:
                threshold_value = _number(row["threshold"], "threshold")
            except ValueError:
                continue
            if threshold_value == value:
                row["threshold_value"] = threshold_value
                matches.append(row)
        if len(matches) != 1:
            raise ValueError(f"selected threshold for study {study_id} must match exactly one result")
        selected.append(matches[0])

    if len(selected) < 10:
        raise ValueError("threshold meta-regression requires at least 10 independent studies")
    threshold_values = np.asarray([row["threshold_value"] for row in selected], dtype=float)
    for row in selected:
        values = [row[key] for key in ("tp", "fp", "fn", "tn")]
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("TP/FP/FN/TN must be nonnegative integers")
        if row["tp"] + row["fn"] == 0 or row["tn"] + row["fp"] == 0:
            raise ValueError("each study must contain diseased and non-diseased participants")
    distinct_thresholds = set(threshold_values.tolist())
    if len(distinct_thresholds) < 3:
        raise ValueError("threshold meta-regression requires at least 3 distinct numeric thresholds")
    magnitude = float(np.max(np.abs(threshold_values)))
    if not math.isfinite(magnitude) or magnitude == 0:
        raise ValueError("threshold values must vary across studies")
    scaled_thresholds = threshold_values / magnitude
    scaled_center = float(scaled_thresholds.mean())
    scaled_sd = float(scaled_thresholds.std())
    center = magnitude * scaled_center
    scale = magnitude * scaled_sd
    if not math.isfinite(center) or not math.isfinite(scale) or scale <= 0:
        raise ValueError("threshold values must have finite nonzero spread")
    x = (scaled_thresholds - scaled_center) / scaled_sd
    if not np.isfinite(x).all():
        raise ValueError("threshold standardization produced non-finite values")

    counts = np.asarray([[row[k] for k in ("tp", "fn", "tn", "fp")] for row in selected],
                        dtype=float)
    tp, fn, tn, fp = counts.T
    diseased, non_diseased = tp + fn, tn + fp
    if np.any(diseased <= 0) or np.any(non_diseased <= 0):
        raise ValueError("each study must contain diseased and non-diseased participants")
    log_choose = (gammaln(diseased + 1) - gammaln(tp + 1) - gammaln(fn + 1)
                  + gammaln(non_diseased + 1) - gammaln(tn + 1) - gammaln(fp + 1))

    # ponytail: fixed 11-point quadrature can underresolve high-heterogeneity
    # tails; use adaptive nodes if a benchmark shows this error matters.
    nodes, weights = hermgauss(11)
    z1, z2 = np.meshgrid(nodes * math.sqrt(2), nodes * math.sqrt(2), indexing="ij")
    log_weights = np.log(np.outer(weights, weights) / math.pi)

    def negative_log_likelihood(params):
        mu_s, mu_c, beta_s, beta_c, log_sd_s, log_sd_c, z_rho = params
        sd_s, sd_c, rho = np.exp(log_sd_s), np.exp(log_sd_c), np.tanh(z_rho)
        theta_s = mu_s + beta_s * x[:, None, None] + sd_s * z1
        theta_c = (mu_c + beta_c * x[:, None, None]
                   + sd_c * (rho * z1 + np.sqrt(1 - rho * rho) * z2))
        ll = (tp[:, None, None] * log_expit(theta_s)
              + fn[:, None, None] * log_expit(-theta_s)
              + tn[:, None, None] * log_expit(theta_c)
              + fp[:, None, None] * log_expit(-theta_c)
              + log_weights + log_choose[:, None, None])
        return -float(logsumexp(ll, axis=(1, 2)).sum())

    def logit(p):
        p = min(max(float(p), 1e-4), 1 - 1e-4)
        return math.log(p / (1 - p))

    initial = [logit(tp.sum() / diseased.sum()), logit(tn.sum() / non_diseased.sum()),
               0.0, 0.0]
    bounds = [(-8, 8), (-8, 8), (-8, 8), (-8, 8), (-7, 2), (-7, 2), (-3, 3)]
    starts = [minimize(negative_log_likelihood,
                       [*initial, math.log(sd), math.log(sd), z_rho],
                       method="L-BFGS-B", bounds=bounds,
                       options={"maxiter": 700, "ftol": 1e-10})
              for sd, z_rho in ((.1, 0.0), (.5, 0.0), (.5, 1.0), (.5, -1.0))]
    fit = min((candidate for candidate in starts
               if candidate.success and np.isfinite(candidate.fun)),
              key=lambda candidate: candidate.fun, default=None)
    if fit is None:
        raise ValueError("bivariate threshold model did not converge; do not report estimates")

    mu_s, mu_c, beta_s, beta_c, log_sd_s, log_sd_c, z_rho = fit.x
    if not np.isfinite(fit.x).all():
        raise ValueError("bivariate threshold model returned non-finite parameters")
    tau_s, tau_c, rho = math.exp(log_sd_s), math.exp(log_sd_c), math.tanh(z_rho)
    warnings = [
        "Threshold associations are study-level and observational; they do not establish causality.",
        "Confirm threshold units, scale, direction, and clinical comparability across studies.",
        "The model assumes linear threshold associations on both logit scales and one independent threshold per study.",
    ]
    boundary = any(lo + .01 >= value or value >= hi - .01
                   for value, (lo, hi) in zip(fit.x, bounds))
    if tau_s < .002 or tau_c < .002 or abs(rho) > .99:
        warnings.append("Between-study covariance is near a boundary; random-effect estimates may be unstable.")
        boundary = True

    covariance = None
    if not boundary:
        # ponytail: finite-difference information is approximate; use profile
        # likelihood if interval accuracy proves inadequate.
        step = 1e-3
        hessian = np.zeros((7, 7))
        center_value = negative_log_likelihood(fit.x)
        basis = np.eye(7) * step
        for i in range(7):
            hessian[i, i] = (negative_log_likelihood(fit.x + basis[i]) - 2 * center_value
                             + negative_log_likelihood(fit.x - basis[i])) / step**2
            for j in range(i):
                hessian[i, j] = hessian[j, i] = (
                    negative_log_likelihood(fit.x + basis[i] + basis[j])
                    - negative_log_likelihood(fit.x + basis[i] - basis[j])
                    - negative_log_likelihood(fit.x - basis[i] + basis[j])
                    + negative_log_likelihood(fit.x - basis[i] - basis[j])) / (4 * step**2)
        if not np.isfinite(hessian).all():
            raise ValueError("observed-information Hessian is non-finite")
        try:
            minimum_eigenvalue = float(np.linalg.eigvalsh(hessian).min())
        except np.linalg.LinAlgError as exc:
            raise ValueError("observed-information Hessian could not be decomposed") from exc
        if minimum_eigenvalue > 1e-7:
            try:
                candidate_covariance = np.linalg.inv(hessian)
            except np.linalg.LinAlgError as exc:
                raise ValueError("observed-information covariance could not be computed") from exc
            if not np.isfinite(candidate_covariance).all():
                raise ValueError("observed-information covariance is non-finite")
            if np.any(np.diag(candidate_covariance) < 0):
                raise ValueError("observed-information covariance has negative variances")
            covariance = candidate_covariance
        else:
            warnings.append("Observed information is singular; confidence intervals are unavailable.")
    else:
        warnings.append("Confidence intervals are unavailable for a boundary fit.")

    def coefficient(estimate, variance, divisor=1.0):
        error = math.sqrt(max(0.0, float(variance))) / divisor if covariance is not None else None
        estimate = float(estimate) / divisor
        if not math.isfinite(estimate) or (error is not None and not math.isfinite(error)):
            raise ValueError("threshold model coefficients are non-finite")
        return {"estimate": estimate, "se": error,
                "ci_95": [estimate - 1.96 * error, estimate + 1.96 * error]
                if error is not None else None}

    public_studies = [{key: row[key] for key in
                       ("study_id", "threshold", "threshold_value", "tp", "fp", "fn", "tn", "source_key", "source_locator")}
                      for row in selected]
    return {
        "method": "bivariate binomial-logit normal random-effects meta-regression (11-point Gauss-Hermite)",
        "n_studies": len(selected),
        "n_distinct_thresholds": len(distinct_thresholds),
        "threshold_center": center,
        "threshold_scale": scale,
        "threshold_range": [float(threshold_values.min()), float(threshold_values.max())],
        "index_test": index_test,
        "target_condition": target_condition,
        "reference_standard": reference_standard,
        "fixed_effects": {
            "logit_sensitivity_at_mean_threshold": coefficient(mu_s, covariance[0, 0]
                                                                 if covariance is not None else 0.0),
            "logit_specificity_at_mean_threshold": coefficient(mu_c, covariance[1, 1]
                                                                 if covariance is not None else 0.0),
            "logit_sensitivity_slope_per_threshold_unit": coefficient(
                beta_s, covariance[2, 2] if covariance is not None else 0.0, scale),
            "logit_specificity_slope_per_threshold_unit": coefficient(
                beta_c, covariance[3, 3] if covariance is not None else 0.0, scale),
        },
        "sensitivity_at_mean_threshold": float(expit(mu_s)),
        "specificity_at_mean_threshold": float(expit(mu_c)),
        "tau_sensitivity": tau_s,
        "tau_specificity": tau_c,
        "random_effect_correlation": rho,
        "log_likelihood": -float(fit.fun),
        "warnings": warnings,
        "studies": public_studies,
    }
