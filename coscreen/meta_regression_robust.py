"""Robust (sandwich) variance estimation and small-sample inference for study-level
meta-regression, one independent result per study.

This module complements ``coscreen.meta_regression_model`` (which provides the
mixed-effects model itself, profile REML tau2, and Knapp-Hartung intervals).
Point estimates and tau2 estimators are identical to that module; the difference
is inference: instead of the model-based covariance ``(X'WX)^-1`` or the
modified Knapp-Hartung rescaling, this module uses cluster-robust sandwich
variance estimators (CR0/CR1/CR2) in which each study is its own cluster, with
Satterthwaite degrees of freedom for t tests and intervals.

References
----------
- Tipton, E. (2015). Small sample adjustments for robust variance estimation
  with meta-regression. Psychological Methods, 20(3), 375-393.
  doi:10.1037/met0000011
- Tipton, E., & Pustejovsky, J. E. (2015). Small-sample adjustments for tests
  of moderators and model fit using robust variance estimation in
  meta-regression. Journal of Educational and Behavioral Statistics, 40(6),
  604-634. doi:10.3102/1076998615606099
- Pustejovsky, J. E., & Tipton, E. (2018). Small-sample methods for
  cluster-robust variance estimation and hypothesis testing in fixed effects
  models. Journal of Business & Economic Statistics, 36(4), 672-683.
  doi:10.1080/07350015.2016.1247004 (CR0/CR1 eq. 4; CR2 adjustment eqs. 6-8;
  Satterthwaite degrees of freedom eq. 11, arXiv:1601.01981v2 numbering)
- Bell, R. M., & McCaffrey, D. F. (2002). Bias reduction in standard errors
  for linear regression with multi-stage samples. Survey Methodology, 28(2),
  169-181. (bias-reduced linearization, the origin of CR2)
- Eicker (1963), Huber (1967), White (1980): the heteroskedasticity-robust
  sandwich for independent rows; Liang & Zeger (1986) and Cameron & Miller
  (2015): the cluster-robust generalization and CR0/CR1 naming.
- DerSimonian, R., & Laird, N. (1986). Meta-analysis in clinical trials.
  Controlled Clinical Trials, 7(3), 177-188. (DL tau2)
"""

from __future__ import annotations

import copy
import math
from numbers import Real
from typing import Mapping, Sequence

import numpy as np
from scipy.stats import t as student_t

from coscreen.meta_regression_model import _number, _reml_tau2

MODELS = {"fixed", "dl", "reml"}
VCOV_TYPES = {"cr0", "cr1", "cr2"}
# clubSandwich treats eigenvalues of the leverage-adjustment target below
# 10^-12 as zero, which sets the CR2 adjustment to zero for near-saturated
# rows; we mirror that convention for singleton clusters.
_LEVERAGE_TOL = 1e-12


def _dl_tau2(y: np.ndarray, x: np.ndarray, sampling_variance: np.ndarray) -> float:
    """DerSimonian-Laird moment estimator for the meta-regression model.

    tau2 = max(0, (RSS - (k - p)) / tr(P)), where P = W - WX(X'WX)^-1 X'W with
    W = diag(1/v_i). This is the matrix generalization used by
    ``metafor::rma(method = "DL")``; for an intercept-only design it reduces to
    the classic tau2 = (Q - (k - 1)) / (sum(w) - sum(w^2)/sum(w)) of
    DerSimonian & Laird (1986).
    """
    weight = 1.0 / sampling_variance
    information = x.T @ (weight[:, None] * x)
    try:
        m = np.linalg.inv(information)
        projection = weight[:, None] * np.eye(len(y)) - (weight[:, None] * x) @ m @ (weight[:, None] * x).T
        rss = float(y @ projection @ y)
        trace_p = float(np.trace(projection))
    except (np.linalg.LinAlgError, ValueError, FloatingPointError) as exc:
        raise ValueError("DL tau2 estimation failed") from exc
    if not math.isfinite(rss) or not math.isfinite(trace_p) or trace_p <= 0:
        raise ValueError("DL tau2 estimation produced a non-finite result")
    return max(0.0, (rss - (len(y) - x.shape[1])) / trace_p)


def _moderator_columns(rows: Sequence[Mapping], moderators: Mapping) -> tuple[list[str], np.ndarray, dict]:
    """Build the design matrix from per-row moderator values.

    ``moderators`` maps a name to a specification:
    - ``"continuous"``: a numeric column;
    - ``"categorical"``: a reference-coded factor whose reference level is the
      first level appearing in ``rows``;
    - ``{"type": "categorical", "reference": level}``: a factor with an
      explicit reference level;
    - a sequence of exactly two moderator names: their interaction (products
      of the two terms' columns), labelled ``a:b``.
    """
    if not isinstance(moderators, Mapping):
        raise ValueError("moderators must map moderator names to specifications")
    names: list[str] = ["(Intercept)"]
    columns: list[np.ndarray] = [np.ones(len(rows), dtype=float)]
    encoded: dict[str, list[tuple[str, np.ndarray]]] = {}

    for name, spec in moderators.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("moderator names must be non-empty strings")
        name = name.strip()
        if isinstance(spec, (list, tuple)):
            continue  # interactions are appended after all main-effect columns
        values = []
        if isinstance(spec, Mapping):
            kind = spec.get("type")
            reference = spec.get("reference")
            if kind != "categorical":
                raise ValueError(f"moderator {name} must be categorical when a mapping is given")
        elif spec == "continuous":
            kind, reference = "continuous", None
        elif spec == "categorical":
            kind, reference = "categorical", None
        else:
            raise ValueError(f"unsupported specification for moderator {name}")

        for index, row in enumerate(rows):
            if name not in row:
                raise ValueError(f"missing moderator {name} for study row {index}")
            value = row[name]
            if kind == "continuous":
                values.append(_number(value, f"moderator {name} for study row {index}"))
            elif isinstance(value, str) and value.strip():
                values.append(value.strip())
            else:
                raise ValueError(f"categorical moderator {name} needs non-empty string values")
        if kind == "continuous":
            encoded[name] = [(name, np.asarray(values, dtype=float))]
        else:
            levels = list(dict.fromkeys(values))
            if len(levels) < 2:
                raise ValueError(f"categorical moderator {name} needs at least two levels")
            if reference is None:
                reference = levels[0]
            else:
                reference = str(reference).strip()
                if reference not in levels:
                    raise ValueError(f"reference level {reference!r} is not present for {name}")
            encoded[name] = [(f"{name}[{level}]", np.asarray([v == level for v in values], dtype=float))
                             for level in levels if level != reference]
        for column_name, column in encoded[name]:
            names.append(column_name)
            columns.append(column)

    seen_interactions: set[frozenset] = set()
    for name, spec in moderators.items():
        if not isinstance(spec, (list, tuple)):
            continue
        if len(spec) != 2 or any(not isinstance(part, str) or part.strip() not in encoded for part in spec):
            raise ValueError(f"interaction {name!r} must name two moderators with columns")
        left, right = (part.strip() for part in spec)
        if left == right:
            raise ValueError("interactions require two different moderators")
        pair_key = frozenset((left, right))
        if pair_key in seen_interactions:
            raise ValueError("duplicate interaction")
        seen_interactions.add(pair_key)
        for left_name, left_values in encoded[left]:
            for right_name, right_values in encoded[right]:
                names.append(f"{left_name}:{right_name}")
                columns.append(left_values * right_values)

    if len(names) != len(set(names)):
        raise ValueError("design column names are ambiguous")
    return names, np.column_stack(columns), encoded


def _validate_rows(rows: object) -> tuple[list[str], np.ndarray, np.ndarray, list[dict]]:
    if not isinstance(rows, (list, tuple)) or len(rows) < 2:
        raise ValueError("robust meta-regression requires at least two study rows")
    study_ids: list[str] = []
    effects: list[float] = []
    standard_errors: list[float] = []
    clean_rows: list[dict] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError("each study row must be a mapping")
        study_id = row.get("study_id")
        if study_id is None:
            study_id = f"study_{index + 1}"
        if not isinstance(study_id, str) or not study_id.strip():
            raise ValueError("study_id values must be non-empty strings when provided")
        study_ids.append(study_id.strip())
        effects.append(_number(row.get("effect"), f"effect for study {study_id}"))
        standard_error = _number(row.get("se"), f"SE for study {study_id}")
        if standard_error <= 0:
            raise ValueError("each SE must be positive")
        standard_errors.append(standard_error)
        clean_rows.append(dict(row))
    if len(study_ids) != len(set(study_ids)):
        raise ValueError("duplicate study id; each study must appear once")
    return study_ids, np.asarray(effects, dtype=float), np.asarray(standard_errors, dtype=float), clean_rows


def robust_meta_regression(rows, moderators, *, model, vcov_type, level=0.95) -> dict:
    """Study-level meta-regression with cluster-robust (sandwich) inference.

    Each row of ``rows`` is one independent study: ``{"effect": ..., "se": ...}``
    plus one value per moderator name. ``moderators`` maps moderator names to
    specifications (see :func:`_moderator_columns`); an empty mapping fits an
    intercept-only model. Effects are used on the scale given (log ratios or
    Fisher z must be transformed by the caller, as in
    ``coscreen.meta_regression_model``).

    ``model`` selects the between-study variance estimator used for the WLS
    weights ``w_i = 1 / (v_i + tau2)`` and is applied exactly as in
    ``coscreen.meta_regression_model``: ``"fixed"`` (tau2 = 0), ``"dl"``
    (DerSimonian-Laird moment estimator), or ``"reml"`` (profile restricted
    maximum likelihood). ``vcov_type`` (required) selects the sandwich
    estimator with each study as its own cluster: ``"cr0"`` (Eicker-Huber-
    White / Liang-Zeger), ``"cr1"`` (CR0 times ``k / (k - 1)``), or ``"cr2"``
    (Bell-McCaffrey bias-reduced linearization; the recommended small-sample
    choice per Pustejovsky & Tipton 2018). ``level`` is the interval coverage.

    Coefficients are the same WLS point estimates as the model-based fit; only
    standard errors, tests, and intervals change. Every coefficient receives a
    Satterthwaite t test (Tipton 2015; Pustejovsky & Tipton 2018, eq. 11) with
    per-coefficient degrees of freedom, including for CR0/CR1 (for which the
    degrees of freedom coincide). CR2 also raises the leverage-adjusted robust
    covariance; ``model_based_se`` reports the non-robust ``(X'WX)^-1``
    standard errors side by side.

    Returns a dict with coefficients, robust and model-based covariances,
    tau2, ``input_data`` (deep copy), and ``warnings`` (few studies, robust
    inference does not change point estimates, relation to Knapp-Hartung).
    """
    method = model.lower() if isinstance(model, str) else ""
    if method not in MODELS:
        raise ValueError("model must be fixed, dl, or reml")
    vcov = vcov_type.lower() if isinstance(vcov_type, str) else ""
    if vcov not in VCOV_TYPES:
        raise ValueError("vcov_type must be one of: cr0, cr1, cr2")
    if isinstance(level, (bool, np.bool_)) or not isinstance(level, Real):
        raise ValueError("level must be strictly between 0 and 1")
    level = float(level)
    if not math.isfinite(level) or not 0.0 < level < 1.0:
        raise ValueError("level must be strictly between 0 and 1")

    study_ids, y, se, clean_rows = _validate_rows(rows)
    k = len(y)
    sampling_variance = se**2
    if not np.isfinite(sampling_variance).all() or np.any(sampling_variance <= 0):
        raise ValueError("squared SE values must be finite and positive")
    design_columns, x, _ = _moderator_columns(clean_rows, moderators)
    try:
        if np.linalg.matrix_rank(x) != x.shape[1]:
            raise ValueError("design matrix is rank deficient")
    except np.linalg.LinAlgError as exc:
        raise ValueError("design matrix rank could not be determined") from exc
    p = x.shape[1]
    if k - p < 1:
        raise ValueError("robust meta-regression requires positive residual degrees of freedom")

    warnings: list[str] = []
    if method == "fixed":
        tau2 = 0.0
    elif method == "dl":
        tau2 = _dl_tau2(y, x, sampling_variance)
        if tau2 == 0.0:
            warnings.append("DL tau2 was floored at zero; robust intervals then protect against "
                            "variance misspecification but not against real between-study heterogeneity.")
    else:
        tau2, _ = _reml_tau2(y, x, sampling_variance)
        if tau2 == 0.0:
            warnings.append("Profile REML tau2 was floored at zero; robust intervals then protect "
                            "against variance misspecification but not against real between-study "
                            "heterogeneity.")

    weight = 1.0 / (sampling_variance + tau2)
    try:
        information = x.T @ (weight[:, None] * x)
        model_covariance = np.linalg.inv(information)
        beta = model_covariance @ (x.T @ (weight * y))
    except (np.linalg.LinAlgError, ValueError, FloatingPointError) as exc:
        raise ValueError("meta-regression information matrix could not be inverted") from exc
    residual = y - x @ beta

    # Leverage and CR2 adjustment; each study is its own cluster.
    leverage = weight * np.einsum("ij,jk,ik->i", x, model_covariance, x)
    adjusted = np.ones(k)
    if vcov == "cr2":
        one_minus_h = 1.0 - leverage
        clipped = one_minus_h <= _LEVERAGE_TOL
        # clubSandwich's matrix_power maps eigenvalues <= 1e-12 (including
        # negative ones produced by rounding) to zero, so the CR2 adjustment
        # of a near-saturated row is set to zero rather than inflated.
        safe = np.where(clipped, 1.0, one_minus_h)
        adjusted = np.where(clipped, 0.0, 1.0 / np.sqrt(safe))
        if clipped.any():
            warnings.append("At least one study had leverage within 1e-12 of one; its CR2 "
                            "adjustment was set to zero, matching the clubSandwich convention.")
    elif vcov == "cr1":
        adjusted = np.full(k, math.sqrt(k / (k - 1.0)))

    meat = np.zeros((p, p))
    for index in range(k):
        u = adjusted[index] * weight[index] * residual[index] * x[index]
        meat += np.outer(u, u)
    robust_covariance = model_covariance @ meat @ model_covariance

    # Satterthwaite degrees of freedom (Pustejovsky & Tipton 2018, eq. 11),
    # mirroring clubSandwich's get_GH/get_P_array for singleton clusters.
    weight_scale = float(np.mean(weight))
    scaled_weight = weight / weight_scale
    m_u = weight_scale * model_covariance  # equals (X' W_scaled X)^-1
    a_matrix = x @ model_covariance.T
    weights_adjusted = scaled_weight * adjusted
    quadratic_form = x @ m_u @ x.T
    df = np.empty(p)
    for coefficient in range(p):
        trace = float(np.sum(scaled_weight * adjusted**2 * (1.0 - leverage)
                             * a_matrix[:, coefficient] ** 2))
        p_matrix = np.diag(scaled_weight * adjusted**2 * a_matrix[:, coefficient] ** 2)
        p_matrix -= np.outer(weights_adjusted * a_matrix[:, coefficient],
                             weights_adjusted * a_matrix[:, coefficient]) * quadratic_form
        norm_squared = float(np.sum(p_matrix**2))
        if not math.isfinite(trace) or not math.isfinite(norm_squared) or norm_squared <= 0:
            raise ValueError("Satterthwaite degrees of freedom could not be computed")
        df[coefficient] = trace * trace / norm_squared

    if not np.isfinite(beta).all() or not np.isfinite(df).all() or np.any(df <= 0):
        raise ValueError("robust meta-regression produced non-finite inference statistics")
    robust_se = np.sqrt(np.diag(robust_covariance))
    model_se = np.sqrt(np.diag(model_covariance))
    if not np.isfinite(robust_se).all() or np.any(robust_se <= 0):
        raise ValueError("robust meta-regression produced non-finite standard errors")

    alpha = 1.0 - level
    coefficients = []
    for index, name in enumerate(design_columns):
        statistic = float(beta[index] / robust_se[index])
        critical = float(student_t.ppf(1.0 - alpha / 2.0, df[index]))
        coefficients.append({
            "name": name,
            "estimate": float(beta[index]),
            "robust_se": float(robust_se[index]),
            "model_based_se": float(model_se[index]),
            "se_ratio": float(robust_se[index] / model_se[index]),
            "statistic": statistic,
            "df": float(df[index]),
            "ci_low": float(beta[index] - critical * robust_se[index]),
            "ci_high": float(beta[index] + critical * robust_se[index]),
            "p_value": float(2.0 * student_t.sf(abs(statistic), df[index])),
        })
    for coefficient in coefficients:
        if not all(math.isfinite(coefficient[key]) for key in
                   ("estimate", "robust_se", "model_based_se", "statistic", "df",
                    "ci_low", "ci_high", "p_value")):
            raise ValueError("robust meta-regression produced non-finite coefficient statistics")

    if k < 20:
        warnings.append(
            f"Only {k} independent studies were analyzed. Robust variance estimation is "
            "designed for few-study meta-regressions, and CR2 with Satterthwaite degrees of "
            "freedom holds its nominal level best of the CR family (Tipton & Pustejovsky 2015; "
            "Pustejovsky & Tipton 2018), but power is low and results are sensitive to "
            "influential studies.")
    warnings.append("Robust inference changes standard errors, tests, and intervals only; the "
                    "point estimates and tau2 are identical to the model-based fit.")
    warnings.append("This robust approach is not Knapp-Hartung: KH (and its modified variant in "
                    "coscreen.meta_regression_model) rescales the model-based covariance by a "
                    "residual heterogeneity factor, while the sandwich estimators here are "
                    "consistent under variance misspecification with each study as its own "
                    "cluster. CR2 with Satterthwaite df, rather than modified KH, is the "
                    "recommended small-sample default in the cluster-robust literature "
                    "(Pustejovsky & Tipton 2018; Imbens & Kolesar 2016).")

    vcov_labels = {
        "cr0": "CR0 sandwich (Eicker 1963; Huber 1967; White 1980; Liang & Zeger 1986)",
        "cr1": "CR1 sandwich, CR0 times k/(k-1) (Cameron & Miller 2015)",
        "cr2": "CR2 bias-reduced linearization (Bell & McCaffrey 2002; Pustejovsky & Tipton 2018)",
    }
    model_labels = {
        "fixed": "fixed-effect inverse-variance WLS (tau2 = 0)",
        "dl": "DerSimonian-Laird moment estimator for tau2 (metafor-compatible generalization)",
        "reml": "profile REML estimator for tau2 (identical to coscreen.meta_regression_model)",
    }
    return {
        "n_studies": k,
        "study_ids": study_ids,
        "design_columns": design_columns,
        "model": method,
        "vcov_type": vcov,
        "level": level,
        "tau2": float(tau2),
        "residual_df": k - p,
        "coefficients": coefficients,
        "robust_covariance": [[float(value) for value in row] for row in robust_covariance],
        "model_based_covariance": [[float(value) for value in row] for row in model_covariance],
        "leverage": [float(value) for value in leverage],
        "input_data": {"rows": copy.deepcopy(clean_rows), "moderators": copy.deepcopy(dict(moderators))},
        "method": "study-level meta-regression with cluster-robust sandwich variance, "
                  "each study its own cluster, Satterthwaite t inference",
        "vcov_method": vcov_labels[vcov],
        "tau2_method": model_labels[method],
        "df_method": "Satterthwaite (Tipton 2015; Pustejovsky & Tipton 2018, eq. 11)",
        "warnings": warnings,
    }
