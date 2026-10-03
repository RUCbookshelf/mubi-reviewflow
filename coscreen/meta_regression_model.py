"""Study-level random-effects meta-regression for one independent result per study."""

from __future__ import annotations

import math
from numbers import Real
from typing import Mapping, Sequence

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.stats import chi2, f, norm, t

RATIOS = {"RR", "OR", "HR", "RATE_RATIO"}
MEASURES = RATIOS | {"RD", "MD", "SMD", "FISHER_Z"}


def _number(value: object, label: str) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise ValueError(f"{label} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _profile_objective(y: np.ndarray, x: np.ndarray, sampling_variance: np.ndarray,
                       tau2: float) -> float:
    """Return -2 profile restricted log likelihood, omitting constants."""
    try:
        with np.errstate(over="raise", divide="raise", invalid="raise"):
            variance = sampling_variance + tau2
            weight = 1.0 / variance
            information = x.T @ (weight[:, None] * x)
            sign, logdet_information = np.linalg.slogdet(information)
            if sign <= 0 or not math.isfinite(float(logdet_information)):
                return math.inf
            beta = np.linalg.solve(information, x.T @ (weight * y))
            residual = y - x @ beta
            value = float(np.log(variance).sum() + logdet_information + np.sum(weight * residual**2))
    except (FloatingPointError, np.linalg.LinAlgError, ValueError):
        return math.inf
    return value if math.isfinite(value) else math.inf


def _reml_tau2(y: np.ndarray, x: np.ndarray, sampling_variance: np.ndarray) -> tuple[float, float]:
    """Minimize the profile REML objective, including its tau2=0 boundary."""
    with np.errstate(over="ignore", invalid="ignore"):
        scale = max(float(np.var(y)), float(np.mean(sampling_variance)),
                    float(np.max(sampling_variance)))
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("REML variance scale is not finite and positive")

    def objective(scaled_tau2: float) -> float:
        return _profile_objective(y, x, sampling_variance, scaled_tau2 * scale)

    upper = 1.0
    for _ in range(40):
        try:
            fit = minimize_scalar(objective, bounds=(0.0, upper), method="bounded",
                                  options={"xatol": 1e-12, "maxiter": 1000})
        except (FloatingPointError, OverflowError, np.linalg.LinAlgError, ValueError) as exc:
            raise ValueError("REML profile optimization failed") from exc
        if not fit.success or not math.isfinite(float(fit.fun)):
            raise ValueError("REML profile optimization failed")
        if fit.x >= upper * (1.0 - 1e-6):
            upper *= 10.0
            if not math.isfinite(upper * scale):
                raise ValueError("REML profile did not find a finite interior estimate")
            continue
        at_zero = _profile_objective(y, x, sampling_variance, 0.0)
        tau2 = 0.0 if at_zero <= fit.fun else float(fit.x * scale)
        value = _profile_objective(y, x, sampling_variance, tau2)
        if not math.isfinite(value):
            raise ValueError("REML profile produced a non-finite estimate")
        return tau2, value
    raise ValueError("REML profile optimization failed to bracket its minimum")


def profile_reml_tau2(effects: Sequence[float], sampling_variances: Sequence[float]) -> tuple[float, float]:
    """Estimate intercept-only inverse-variance REML tau2 and its objective."""
    try:
        y = np.asarray(effects, dtype=float)
        variance = np.asarray(sampling_variances, dtype=float)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("effects and sampling variances must be finite numbers") from exc
    if y.ndim != 1 or variance.ndim != 1 or y.size < 2 or variance.size != y.size or \
       not np.isfinite(y).all() or not np.isfinite(variance).all() or np.any(variance <= 0):
        raise ValueError("REML needs matching finite effects and positive sampling variances")
    return _reml_tau2(y, np.ones((len(y), 1), dtype=float), variance)


def _make_design(rows: Sequence[Mapping], covariates: Mapping, spec: Mapping,
                 references: Mapping, interactions: Sequence) -> tuple[np.ndarray, list[str]]:
    names = list(spec)
    if not names or any(not isinstance(name, str) or not name.strip() for name in names):
        raise ValueError("spec must name at least one covariate")
    if any(not isinstance(kind, str) or kind not in {"continuous", "categorical"}
           for kind in spec.values()):
        raise ValueError("covariate types must be continuous or categorical")

    categorical = [name for name in names if spec[name] == "categorical"]
    if set(references) != set(categorical):
        raise ValueError("an explicit reference is required for each categorical covariate")

    encoded: dict[str, list[tuple[str, np.ndarray]]] = {}
    for name in names:
        values = []
        for row in rows:
            study_id = row["study_id"]
            if study_id not in covariates or not isinstance(covariates[study_id], Mapping) or \
               name not in covariates[study_id] or covariates[study_id][name] is None:
                raise ValueError(f"missing covariate {name} for study {study_id}")
            value = covariates[study_id][name]
            if spec[name] == "continuous":
                values.append(_number(value, f"covariate {name} for study {study_id}"))
            else:
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"categorical covariate {name} must be a non-empty string")
                values.append(value.strip())

        if spec[name] == "continuous":
            encoded[name] = [(name, np.asarray(values, dtype=float))]
            continue

        levels = list(dict.fromkeys(values))
        reference = references[name]
        if not isinstance(reference, str) or not reference.strip():
            raise ValueError(f"reference for {name} must be a non-empty category")
        reference = reference.strip()
        if len(levels) < 2:
            raise ValueError(f"categorical covariate {name} needs at least two levels")
        if reference not in levels:
            raise ValueError(f"reference level {reference!r} is not present for {name}")
        encoded[name] = [(f"{name}[{level}]", np.asarray([v == level for v in values], dtype=float))
                         for level in levels if level != reference]

    columns = ["(Intercept)"]
    arrays = [np.ones(len(rows), dtype=float)]
    for name in names:
        for column_name, values in encoded[name]:
            columns.append(column_name)
            arrays.append(values)

    seen_interactions = set()
    for pair in interactions:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            raise ValueError("each interaction must name two covariates")
        left, right = pair
        if not isinstance(left, str) or not isinstance(right, str) or \
           left not in encoded or right not in encoded or left == right:
            raise ValueError("interactions require two different covariates in spec")
        pair_key = frozenset((left, right))
        if pair_key in seen_interactions:
            raise ValueError("duplicate interaction")
        seen_interactions.add(pair_key)
        for left_name, left_values in encoded[left]:
            for right_name, right_values in encoded[right]:
                columns.append(f"{left_name}:{right_name}")
                arrays.append(left_values * right_values)

    if len(columns) != len(set(columns)):
        raise ValueError("design column names are ambiguous")
    return np.column_stack(arrays), columns


def fit_meta_regression(rows: list[dict], covariates: dict, spec: dict,
                        references: dict | None = None, interactions: list | None = None,
                        ci_method: str = "normal") -> dict:
    """Fit inverse-variance random-effects meta-regression with profile REML tau2.

    ``rows`` contains one effect and standard error per independent study. Ratio
    measures are modeled on the log scale, and their ``se`` must already be the
    standard error of the log ratio (for example, ``SE(log(RR))``); it is not
    transformed here. FISHER_Z is modeled on the z scale.
    ``ci_method="knha"`` uses the modified Knapp-Hartung adjustment
    ``max(1, Q_residual / df_residual)`` with a t reference distribution. Its
    joint moderator test is an F test with numerator df equal to the number of
    moderator coefficients and denominator df equal to residual df; the normal
    mode uses a joint Wald chi-square test.

    ``explained_heterogeneity_r2`` compares model and intercept-only REML
    estimates: ``max(0, 1 - tau2 / tau2_null)`` when ``tau2_null > 0``, and
    ``None`` when the intercept-only estimate is zero.
    """
    if not isinstance(rows, (list, tuple)) or len(rows) < 10:
        raise ValueError("meta-regression requires at least ten independent studies")
    if not isinstance(spec, Mapping):
        raise ValueError("spec must map covariate names to types")
    if not isinstance(covariates, Mapping):
        raise ValueError("covariates must map study ids to values")
    if references is None:
        references = {}
    if not isinstance(references, Mapping):
        raise ValueError("references must map categorical covariates to levels")
    if interactions is None:
        interactions = []
    if not isinstance(interactions, (list, tuple)):
        raise ValueError("interactions must be a list of covariate pairs")
    method = ci_method.lower() if isinstance(ci_method, str) else ""
    if method not in {"normal", "knha"}:
        raise ValueError("ci_method must be normal or KNHA")

    study_ids = []
    effects = []
    standard_errors = []
    measures = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("each effect row must be a mapping")
        study_id = row.get("study_id")
        if not isinstance(study_id, str) or not study_id.strip():
            raise ValueError("each effect row needs a study id")
        study_ids.append(study_id)
        measure = row.get("measure")
        if not isinstance(measure, str) or measure not in MEASURES:
            raise ValueError("unsupported effect measure")
        measures.add(measure)
        effect = _number(row.get("estimate"), f"effect for study {study_id}")
        standard_error = _number(row.get("se"), f"SE for study {study_id}")
        if standard_error <= 0:
            raise ValueError("each SE must be positive")
        if measure in RATIOS and effect <= 0:
            raise ValueError("ratio effect estimates must be positive")
        effects.append(math.log(effect) if measure in RATIOS else effect)
        standard_errors.append(standard_error)
    if len(study_ids) != len(set(study_ids)):
        raise ValueError("duplicate study id; rows must contain independent studies once")
    if len(measures) != 1:
        raise ValueError("all rows must use the same effect measure")

    measure = next(iter(measures))
    y = np.asarray(effects, dtype=float)
    se = np.asarray(standard_errors, dtype=float)
    with np.errstate(over="ignore", under="ignore", invalid="ignore"):
        sampling_variance = se**2
    if not np.isfinite(sampling_variance).all() or np.any(sampling_variance <= 0):
        raise ValueError("squared SE values must be finite and positive")
    x, design_columns = _make_design(rows, covariates, spec, references, interactions)
    try:
        if np.linalg.matrix_rank(x) != x.shape[1]:
            raise ValueError("design matrix is rank deficient")
    except np.linalg.LinAlgError as exc:
        raise ValueError("design matrix rank could not be determined") from exc
    residual_df = len(rows) - x.shape[1]
    if residual_df <= 0:
        raise ValueError("meta-regression requires positive residual degrees of freedom")

    tau2, reml_objective = _reml_tau2(y, x, sampling_variance)
    tau2_null, _ = _reml_tau2(y, np.ones((len(rows), 1), dtype=float), sampling_variance)
    explained_heterogeneity_r2 = (
        max(0.0, 1.0 - tau2 / tau2_null) if tau2_null > 0 else None
    )
    weight = 1.0 / (sampling_variance + tau2)
    try:
        with np.errstate(over="raise", divide="raise", invalid="raise"):
            information = x.T @ (weight[:, None] * x)
            covariance = np.linalg.inv(information)
            beta = covariance @ (x.T @ (weight * y))
            residual = y - x @ beta
            residual_q = float(np.sum(weight * residual**2))
    except (FloatingPointError, np.linalg.LinAlgError, ValueError) as exc:
        raise ValueError("meta-regression information matrix could not be inverted") from exc
    knha_scale = max(1.0, residual_q / residual_df) if method == "knha" else None
    moderator_df = len(design_columns) - 1
    moderator_covariance = covariance[1:, 1:]
    try:
        with np.errstate(over="raise", divide="raise", invalid="raise"):
            moderator_wald = float(beta[1:] @ np.linalg.solve(moderator_covariance, beta[1:]))
    except (FloatingPointError, np.linalg.LinAlgError) as exc:
        raise ValueError("moderator covariance matrix could not be inverted") from exc
    moderator_wald = max(0.0, moderator_wald)
    if method == "knha":
        moderator_statistic = moderator_wald / (moderator_df * knha_scale)
        moderator_test = {
            "statistic": moderator_statistic,
            "statistic_name": "F",
            "df1": moderator_df,
            "df2": residual_df,
            "p_value": float(f.sf(moderator_statistic, moderator_df, residual_df)),
            "method": "modified Knapp-Hartung joint F test",
        }
    else:
        moderator_test = {
            "statistic": moderator_wald,
            "statistic_name": "chi-square",
            "df1": moderator_df,
            "df2": None,
            "p_value": float(chi2.sf(moderator_wald, moderator_df)),
            "method": "joint Wald chi-square test",
        }
    if not math.isfinite(moderator_test["statistic"]) or not math.isfinite(moderator_test["p_value"]):
        raise ValueError("meta-regression produced a non-finite moderator test")
    with np.errstate(over="ignore", invalid="ignore"):
        standard_errors_beta = np.sqrt(np.diag(covariance) * (knha_scale or 1.0))
    if not np.isfinite(beta).all() or not np.isfinite(standard_errors_beta).all() or \
       np.any(standard_errors_beta <= 0) or not math.isfinite(residual_q):
        raise ValueError("meta-regression produced non-finite coefficient statistics")
    critical = float(t.ppf(0.975, residual_df)) if method == "knha" else float(norm.ppf(0.975))
    distribution = t.sf if method == "knha" else norm.sf
    coefficients = []
    for name, estimate, error in zip(design_columns, beta, standard_errors_beta):
        with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
            statistic = float(estimate / error)
            ci_low, ci_high = float(estimate - critical * error), float(estimate + critical * error)
            p_value = float(2 * distribution(abs(statistic), residual_df)
                            if method == "knha" else 2 * distribution(abs(statistic)))
        if not all(math.isfinite(value) for value in (statistic, ci_low, ci_high, p_value)):
            raise ValueError("meta-regression produced non-finite coefficient statistics")
        coefficients.append({"name": name, "estimate": float(estimate), "se": float(error),
                             "ci_low": ci_low, "ci_high": ci_high, "p": p_value})

    effect_scale = "log" if measure in RATIOS else "Fisher z" if measure == "FISHER_Z" else "natural"
    ci_label = ("modified Knapp-Hartung 95% (ad hoc residual scale floor at 1)"
                if method == "knha" else "normal 95% Wald")
    limitations = [
        "One effect per independent study for one outcome and time point; within-study covariance is not modeled.",
        "Study-level associations are ecological and do not establish participant-level effect modification or causality.",
        "Requires at least 10 studies, a full-rank design, and positive residual degrees of freedom.",
        "Coefficient estimates can be unstable with sparse covariate levels or near-collinearity.",
        "Explained heterogeneity R²* = max(0, 1 - tau2_model/tau2_null) when tau2_null > 0; it is None when tau2_null is zero.",
    ]
    if measure in RATIOS:
        limitations.append("For ratio measures, se must be the standard error on the log-ratio scale; it is not transformed from the raw ratio scale.")
    if method == "knha":
        limitations.append("Modified KNHA (ad hoc) uses max(1, residual Q/df) and a t reference distribution.")
    else:
        limitations.append("Normal Wald intervals may understate uncertainty in small meta-regressions.")
    return {"n_studies": len(rows), "study_ids": study_ids, "measure": measure,
            "effect_scale": effect_scale, "design_columns": design_columns,
            "coefficients": coefficients, "tau2": float(tau2),
            "tau2_null": float(tau2_null),
            "explained_heterogeneity_r2": explained_heterogeneity_r2,
            "moderator_test": moderator_test,
            "residual_q": residual_q, "residual_df": residual_df,
            "reml_objective": float(reml_objective), "method": "random-effects inverse-variance meta-regression with profile REML tau2",
            "ci_method": ci_label, "knha_scale": knha_scale, "limitations": limitations}
