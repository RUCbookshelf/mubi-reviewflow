"""Diagnostic test accuracy data and a bivariate binomial-normal random-effects model.

Each 2×2 table belongs to one study, index test, threshold and reference standard.
The likelihood uses binomial counts directly, including zero cells; it does not
borrow the intervention-effect inverse-variance engine.
"""

from __future__ import annotations

import math
from contextlib import closing
from pathlib import Path

from coscreen.db import _add_column_if_missing, _connect
from coscreen.review_analysis import _linked

DbPath = str | Path


def _ensure_locator(conn) -> None:
    _add_column_if_missing(conn, "review_dta_results", "source_locator",
                           "ALTER TABLE review_dta_results ADD COLUMN source_locator TEXT NOT NULL DEFAULT ''")


def save_result(db: DbPath, study_id: str, index_test: str, target_condition: str,
                threshold: str, reference_standard: str, tp: int, fp: int, fn: int,
                tn: int, source_key: str, source_locator: str = "") -> None:
    if not all(str(v).strip() for v in (study_id, index_test, target_condition,
                                       threshold, reference_standard, source_key)):
        raise ValueError("study, test, condition, threshold, reference and source report are required")
    if any(type(v) is not int or v < 0 for v in (tp, fp, fn, tn)):
        raise ValueError("TP/FP/FN/TN must be nonnegative integers")
    if tp + fn == 0 or tn + fp == 0:
        raise ValueError("both diseased and non-diseased groups must contain participants")
    with closing(_connect(db)) as conn, conn:
        _ensure_locator(conn)
        _linked(conn, study_id, source_key)
        conn.execute("INSERT INTO review_dta_results "
                     "(study_id,index_test,target_condition,threshold,reference_standard,tp,fp,fn,tn,source_key,source_locator) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                     "ON CONFLICT(study_id,index_test,target_condition,threshold,reference_standard) "
                     "DO UPDATE SET tp=excluded.tp,fp=excluded.fp,fn=excluded.fn,tn=excluded.tn,"
                     "source_locator=CASE WHEN source_key=excluded.source_key "
                     "THEN COALESCE(NULLIF(excluded.source_locator,''),source_locator) ELSE excluded.source_locator END,"
                     "source_key=excluded.source_key",
                     (study_id, index_test.strip(), target_condition.strip(), threshold.strip(),
                      reference_standard.strip(), tp, fp, fn, tn, source_key, source_locator.strip()))


def _binomial_ci(k: int, n: int) -> tuple[float, float]:
    from scipy.stats import beta
    return (0.0 if k == 0 else float(beta.ppf(.025, k, n-k+1)),
            1.0 if k == n else float(beta.ppf(.975, k+1, n-k)))


def list_results(db: DbPath) -> list[dict]:
    with closing(_connect(db)) as conn, conn:
        _ensure_locator(conn)
        rows = conn.execute("SELECT study_id,index_test,target_condition,threshold,reference_standard,"
                            "tp,fp,fn,tn,source_key,source_locator FROM review_dta_results "
                            "ORDER BY index_test,target_condition,threshold,study_id").fetchall()
    keys = ("study_id", "index_test", "target_condition", "threshold", "reference_standard",
            "tp", "fp", "fn", "tn", "source_key", "source_locator")
    result = []
    for row in rows:
        data = dict(zip(keys, row))
        data.update(sensitivity=row[5]/(row[5]+row[7]), specificity=row[8]/(row[8]+row[6]),
                    sensitivity_ci=_binomial_ci(row[5], row[5]+row[7]),
                    specificity_ci=_binomial_ci(row[8], row[8]+row[6]))
        result.append(data)
    return result


def synthesize(db: DbPath, index_test: str, target_condition: str, threshold: str | None = None,
               reference_standard: str | None = None) -> dict:
    import numpy as np
    from numpy.polynomial.hermite import hermgauss
    from scipy.optimize import minimize
    from scipy.special import expit, gammaln, log_expit, logsumexp

    threshold = (threshold or "").strip() or None
    reference_standard = (reference_standard or "").strip() or None

    rows = [r for r in list_results(db) if r["index_test"] == index_test and
            r["target_condition"] == target_condition]
    if not rows:
        raise ValueError("no diagnostic accuracy results for this test and condition")
    thresholds = {r["threshold"] for r in rows}
    if threshold is None and len(thresholds) != 1:
        raise ValueError("select one threshold; multiple thresholds from a study are correlated")
    threshold = threshold or next(iter(thresholds))
    rows = [r for r in rows if r["threshold"] == threshold]
    standards = {r["reference_standard"] for r in rows}
    if reference_standard is None and len(standards) != 1:
        raise ValueError("select one reference standard")
    reference_standard = reference_standard or next(iter(standards))
    rows = [r for r in rows if r["reference_standard"] == reference_standard]
    if len(rows) < 5:
        raise ValueError("bivariate synthesis requires at least five independent studies")
    if len({r["study_id"] for r in rows}) != len(rows):
        raise ValueError("one selected threshold per study is required")

    counts = np.asarray([[r[k] for k in ("tp", "fn", "tn", "fp")] for r in rows], dtype=float)
    tp, fn, tn, fp = counts.T
    diseased, non_diseased = tp+fn, tn+fp
    log_choose = gammaln(diseased+1)-gammaln(tp+1)-gammaln(fn+1) + \
                 gammaln(non_diseased+1)-gammaln(tn+1)-gammaln(fp+1)
    nodes, weights = hermgauss(11)
    z1, z2 = np.meshgrid(nodes*math.sqrt(2), nodes*math.sqrt(2), indexing="ij")
    log_weights = np.log(np.outer(weights, weights)/math.pi)

    def negative_log_likelihood(params):
        mu_s, mu_c, log_sd_s, log_sd_c, z_rho = params
        sd_s, sd_c, rho = np.exp(log_sd_s), np.exp(log_sd_c), np.tanh(z_rho)
        theta_s = mu_s + sd_s*z1
        theta_c = mu_c + sd_c*(rho*z1 + np.sqrt(1-rho*rho)*z2)
        ll = (tp[:, None, None]*log_expit(theta_s) + fn[:, None, None]*log_expit(-theta_s)
              + tn[:, None, None]*log_expit(theta_c) + fp[:, None, None]*log_expit(-theta_c)
              + log_weights + log_choose[:, None, None])
        return -float(logsumexp(ll, axis=(1, 2)).sum())

    def logit(p):
        p = min(max(float(p), 1e-4), 1-1e-4)
        return math.log(p/(1-p))

    start = [logit(tp.sum()/diseased.sum()), logit(tn.sum()/non_diseased.sum()),
             math.log(.2), math.log(.2), 0.0]
    bounds = [(-8, 8), (-8, 8), (-7, 2), (-7, 2), (-3, 3)]
    fits = [minimize(negative_log_likelihood, [*start[:2], math.log(sd), math.log(sd), rho],
                     method="L-BFGS-B", bounds=bounds, options={"maxiter": 500, "ftol": 1e-10})
            for sd, rho in ((.1, 0.0), (.5, 0.0), (.5, 1.0))]
    fit = min((f for f in fits if f.success and np.isfinite(f.fun)),
              key=lambda f: f.fun, default=None)
    if fit is None:
        raise ValueError("bivariate model did not converge; do not report a pooled estimate")
    mu_s, mu_c, ls, lc, zr = fit.x
    warnings = ["Check threshold units and clinical comparability before interpreting the summary."]
    # 门控用结构化标志，不依赖警告文案（改文案不得翻转统计逻辑，P2-13）
    covariance_near_boundary = min(math.exp(ls), math.exp(lc)) < .002 or abs(math.tanh(zr)) > .99
    if covariance_near_boundary:
        warnings.append("Between-study covariance is near a boundary; correlation and uncertainty may be unstable.")
    # Observed-information Wald intervals are exploratory; a near-boundary fit has no reliable interval.
    pooled_ci = None
    mean_covariance = None
    if not covariance_near_boundary and all(
            lo + .01 < value < hi - .01 for value, (lo, hi) in zip(fit.x, bounds)):
        step = 1e-3
        hessian = np.zeros((5, 5))
        center = negative_log_likelihood(fit.x)
        for i in range(5):
            delta_i = np.eye(5)[i] * step
            hessian[i, i] = (negative_log_likelihood(fit.x + delta_i) - 2*center +
                             negative_log_likelihood(fit.x - delta_i)) / step**2
            for j in range(i):
                delta_j = np.eye(5)[j] * step
                hessian[i, j] = hessian[j, i] = (
                    negative_log_likelihood(fit.x + delta_i + delta_j)
                    - negative_log_likelihood(fit.x + delta_i - delta_j)
                    - negative_log_likelihood(fit.x - delta_i + delta_j)
                    + negative_log_likelihood(fit.x - delta_i - delta_j)) / (4*step**2)
        if np.linalg.eigvalsh(hessian).min() > 1e-7:
            covariance = np.linalg.inv(hessian)
            mean_covariance = covariance[:2, :2].tolist()
            pooled_ci = {"sensitivity": [float(expit(mu_s + z*1.96*math.sqrt(covariance[0, 0])))
                                         for z in (-1, 1)],
                         "specificity": [float(expit(mu_c + z*1.96*math.sqrt(covariance[1, 1])))
                                         for z in (-1, 1)]}
        else:
            warnings.append("Observed information is singular; pooled confidence intervals are unavailable.")
    else:
        warnings.append("Pooled confidence intervals are unavailable for a boundary fit.")
    return {"method": "bivariate binomial-logit normal random effects (11-point Gauss-Hermite)",
            "n_studies": len(rows), "study_ids": [r["study_id"] for r in rows],
            "index_test": index_test, "target_condition": target_condition,
            "threshold": threshold, "reference_standard": reference_standard,
            "sensitivity": float(expit(mu_s)), "specificity": float(expit(mu_c)),
            "pooled_ci_95": pooled_ci,
            "mean_covariance": mean_covariance,
            "ci_method": "observed-information Wald on logit scale" if pooled_ci else None,
            "logit_sensitivity_mean": float(mu_s), "logit_specificity_mean": float(mu_c),
            "tau_sensitivity": math.exp(ls), "tau_specificity": math.exp(lc),
            "random_effect_correlation": math.tanh(zr),
            "log_likelihood": -float(fit.fun), "warnings": warnings,
            "studies": rows}
