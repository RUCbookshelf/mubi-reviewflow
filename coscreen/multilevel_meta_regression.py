"""REML meta-regression for dependent effects with CR2/HTZ inference."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import numpy as np
from scipy.optimize import minimize
from scipy.stats import t as student_t

from .dependent_effects import KernelError, _htz_test, _symmetric_inverse_sqrt, _unavailable
from .multilevel_random_intercept import _number, _study_covariances

_MAX_EFFECTS = 500


def _cr2_inference(y, x, beta, model_covariance, working_covariance, cluster_ids,
                   names, level, constraints, constraint_names, null_values):
    clusters = list(dict.fromkeys(cluster_ids))
    if len(clusters) < 2:
        return _unavailable("insufficient_independent_clusters",
                             "at least two independent clusters are required for robust inference",
                             n_clusters=len(clusters))
    try:
        weights = np.linalg.solve(working_covariance, np.eye(len(y)))
        response_cholesky = np.linalg.cholesky(working_covariance)
        maps = []
        for cluster in clusters:
            indices = np.flatnonzero(np.asarray(cluster_ids, dtype=object) == cluster)
            v = working_covariance[np.ix_(indices, indices)]
            w = weights[np.ix_(indices, indices)]
            x_cluster = x[indices]
            chol = np.linalg.cholesky(v).T
            ih = np.eye(len(indices)) - x_cluster @ model_covariance @ x_cluster.T @ w
            adjustment_target = chol @ ih @ v @ chol.T
            adjustment = chol.T @ _symmetric_inverse_sqrt(
                adjustment_target, "CR2 residual covariance adjustment") @ chol
            residual_projection = -(x_cluster @ model_covariance @ x.T @ weights)
            residual_projection[:, indices] += np.eye(len(indices))
            maps.append(model_covariance @ x_cluster.T @ w @ adjustment @ residual_projection)
        robust_covariance = sum((np.outer(mapping @ y, mapping @ y) for mapping in maps),
                                start=np.zeros_like(model_covariance))
    except (KernelError, np.linalg.LinAlgError, FloatingPointError, ValueError):
        return _unavailable("cr2_adjustment_failed",
                             "CR2 adjustment is singular or numerically unstable for at least one cluster")
    if not np.isfinite(robust_covariance).all():
        return _unavailable("invalid_robust_covariance", "CR2 covariance contains non-finite values")

    tests = []
    for index, name in enumerate(names):
        variance = float(robust_covariance[index, index])
        if variance <= 0:
            return _unavailable("zero_robust_variance", f"robust variance for {name} is not positive",
                                coefficient=name)
        standardized_maps = [mapping @ response_cholesky for mapping in maps]
        cluster_rows = np.asarray([mapping[index] for mapping in standardized_maps])
        gram = cluster_rows @ cluster_rows.T
        trace, trace_squared = float(np.trace(gram)), float(np.sum(gram * gram))
        df = trace * trace / trace_squared if trace_squared > 0 else math.nan
        if not math.isfinite(df) or df <= 0:
            return _unavailable("satterthwaite_df_failed",
                                f"Satterthwaite degrees of freedom for {name} are unavailable",
                                coefficient=name)
        se = math.sqrt(variance)
        statistic = float(beta[index] / se)
        critical = float(student_t.ppf((1 + level) / 2, df))
        tests.append({
            "name": name, "estimate": float(beta[index]), "robust_se": se,
            "model_based_se": math.sqrt(float(model_covariance[index, index])),
            "statistic": statistic, "df": df,
            "ci_low": float(beta[index] - critical * se),
            "ci_high": float(beta[index] + critical * se),
            "p_value": float(2 * student_t.sf(abs(statistic), df)),
        })
    joint = None if constraints is None else _htz_test(
        beta, standardized_maps, model_covariance, robust_covariance, constraints, list(names),
        constraint_names, null_values)
    warnings = []
    if len(clusters) < 10:
        warnings.append(f"Only {len(clusters)} independent clusters; small-sample power is limited.")
    if any(test["df"] < 4 for test in tests):
        warnings.append("At least one coefficient has Satterthwaite df below 4; inference is highly small-sample-sensitive.")
    return {
        "status": "available", "vcov_type": "CR2",
        "covariance": robust_covariance.tolist(), "coefficient_tests": tests,
        "df_method": "Satterthwaite t", "joint_test": joint, "warnings": warnings,
    }


def fit_multilevel_meta_regression(
    effects: Sequence[Mapping],
    design_matrix: object,
    design_names: Sequence[str],
    *,
    design_effect_ids: Sequence[str],
    within_study_covariances: Mapping[str, Sequence[Sequence[float]]] | None = None,
    constraints: object | None = None,
    constraint_names: Sequence[str] | None = None,
    null_values: Sequence[float] | None = None,
    level: float = 0.95,
) -> dict:
    """Fit a three-level random-intercept meta-regression by REML.

    ``effects`` are ordered rows with ``effect_id``, ``study_id``,
    ``cluster_id``, ``source_id``, ``estimate``, and known sampling
    ``variance``. ``design_matrix`` is explicitly keyed by
    ``design_effect_ids`` and is reordered to effect input order. ``study_id``
    defines the nested random intercepts; ``cluster_id`` defines independent
    clusters for CR2 inference. Each study must belong to exactly one cluster.
    Optional covariance blocks are keyed by study and ordered like that
    study's rows in ``effects``.
    """
    if not isinstance(effects, (list, tuple)) or not effects:
        raise KernelError("missing_effects", "effects must be a non-empty sequence")
    if len(effects) > _MAX_EFFECTS:
        raise KernelError("unsupported_effect_count", f"at most {_MAX_EFFECTS} effects are supported",
                          n_effects=len(effects))

    rows, effect_ids, study_ids, cluster_ids = [], [], [], []
    estimates, variances = [], []
    for index, effect in enumerate(effects):
        if not isinstance(effect, Mapping):
            raise KernelError("invalid_effect", f"effect {index + 1} must be a mapping")
        row = {}
        for field in ("effect_id", "study_id", "cluster_id", "source_id"):
            value = effect.get(field)
            if not isinstance(value, str) or not value.strip():
                raise KernelError(f"missing_{field}", f"effect {index + 1} needs a non-empty {field}")
            row[field] = value.strip()
        row["estimate"] = _number(effect.get("estimate"), f"estimate for effect {index + 1}")
        row["variance"] = _number(effect.get("variance"), f"variance for effect {index + 1}")
        if row["variance"] <= 0:
            raise KernelError("nonpositive_variance", f"sampling variance for {row['effect_id']} must be positive")
        for optional in ("source_locator",):
            if optional in effect:
                row[optional] = effect[optional]
        rows.append(row)
        effect_ids.append(row["effect_id"])
        study_ids.append(row["study_id"])
        cluster_ids.append(row["cluster_id"])
        estimates.append(row["estimate"])
        variances.append(row["variance"])

    if len(effect_ids) != len(set(effect_ids)):
        raise KernelError("duplicate_effect_id", "effect_id values must be unique")
    studies = list(dict.fromkeys(study_ids))
    rows_per_study = {study_id: study_ids.count(study_id) for study_id in studies}
    if len(studies) < 3:
        raise KernelError("insufficient_studies", "at least three independent studies are required")
    if sum(count > 1 for count in rows_per_study.values()) < 2:
        raise KernelError("insufficient_within_study_replication",
                          "at least two studies must contribute multiple effects")
    study_cluster = {}
    for study_id, cluster_id in zip(study_ids, cluster_ids):
        if study_id in study_cluster and study_cluster[study_id] != cluster_id:
            raise KernelError("crossed_cluster_structure",
                              "each random-effects study must belong to exactly one independent cluster",
                              study_id=study_id)
        study_cluster[study_id] = cluster_id

    if not isinstance(design_effect_ids, (list, tuple, np.ndarray)):
        raise KernelError("invalid_design_effect_ids", "design_effect_ids must be a sequence")
    design_ids = list(design_effect_ids)
    if any(not isinstance(value, str) or not value.strip() for value in design_ids):
        raise KernelError("invalid_design_effect_ids", "design_effect_ids must contain non-empty strings")
    design_ids = [value.strip() for value in design_ids]
    if len(design_ids) != len(set(design_ids)) or set(design_ids) != set(effect_ids):
        raise KernelError("design_effect_id_mismatch",
                          "design_effect_ids must contain each selected effect_id exactly once")
    try:
        supplied_x = np.asarray(design_matrix, dtype=float)
    except (TypeError, ValueError, OverflowError):
        raise KernelError("invalid_design_matrix", "design_matrix must be finite and two-dimensional") from None
    names = list(design_names) if isinstance(design_names, (list, tuple)) else []
    if any(not isinstance(name, str) or not name.strip() for name in names) or len(names) != len(set(names)):
        raise KernelError("invalid_design_names", "design_names must be unique non-empty strings")
    if supplied_x.ndim != 2 or supplied_x.shape != (len(design_ids), len(names)) or \
       not names or not np.isfinite(supplied_x).all():
        raise KernelError("invalid_design_matrix", "design_matrix dimensions must match design_effect_ids and design_names")
    if len(rows) < len(names):
        raise KernelError("insufficient_effects", "effects must be at least as numerous as design columns")
    x_by_id = {effect_id: supplied_x[index] for index, effect_id in enumerate(design_ids)}
    x = np.asarray([x_by_id[effect_id] for effect_id in effect_ids], dtype=float)
    rank = int(np.linalg.matrix_rank(x))
    if rank != x.shape[1]:
        raise KernelError("rank_deficient_design", "design_matrix must have full column rank",
                          rank=rank, n_columns=int(x.shape[1]))

    y = np.asarray(estimates, dtype=float)
    sampling, covariance_snapshot = _study_covariances(rows, np.asarray(variances), within_study_covariances)
    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            scale = max(float(np.var(y, ddof=1)), float(np.max(variances)))
            if not math.isfinite(scale) or scale <= 0:
                raise ValueError
            sampling_scaled = sampling / scale
            # Remove an arbitrary least-squares fit before optimization; REML's
            # projection annihilates X, and restoring it improves large-offset stability.
            beta_offset = np.linalg.lstsq(x, y, rcond=None)[0]
            y_scaled = (y - x @ beta_offset) / math.sqrt(scale)
        if not np.isfinite(sampling_scaled).all() or not np.isfinite(y_scaled).all():
            raise ValueError
    except (FloatingPointError, np.linalg.LinAlgError, OverflowError, ValueError):
        raise KernelError("invalid_reml_scale", "REML inputs are outside the finite numerical range") from None

    identity = np.eye(len(rows))
    same_study = np.equal.outer(study_ids, study_ids).astype(float)

    def objective(parameters: np.ndarray) -> tuple[float, np.ndarray]:
        tau_effect, tau_study = parameters
        if not np.isfinite(parameters).all() or np.any(parameters < 0):
            return math.inf, np.zeros(2)
        marginal = sampling_scaled + tau_effect * identity + tau_study * same_study
        try:
            with np.errstate(over="raise", divide="raise", invalid="raise"):
                sign_v, logdet_v = np.linalg.slogdet(marginal)
                inverse = np.linalg.solve(marginal, identity)
                information = x.T @ inverse @ x
                sign_x, logdet_x = np.linalg.slogdet(information)
                if sign_v <= 0 or sign_x <= 0:
                    return math.inf, np.zeros(2)
                inverse_x = inverse @ x
                projection = inverse - inverse_x @ np.linalg.solve(information, inverse_x.T)
                alpha = projection @ y_scaled
                value = float(logdet_v + logdet_x + y_scaled @ alpha)
                components = (identity, same_study)
                gradient = np.asarray([
                    np.trace(projection @ component) - alpha @ component @ alpha
                    for component in components
                ], dtype=float)
        except (FloatingPointError, np.linalg.LinAlgError, ValueError, OverflowError):
            return math.inf, np.zeros(2)
        if not math.isfinite(value) or not np.isfinite(gradient).all():
            return math.inf, np.zeros(2)
        return value, gradient

    fits = []
    for start in ([.1, .1], [.01, 1.0], [1.0, .01], [1.0, 1.0]):
        fit = minimize(objective, np.asarray(start), method="L-BFGS-B", jac=True,
                       bounds=((0.0, None), (0.0, None)),
                       options={"ftol": 1e-15, "gtol": 1e-10, "maxiter": 3000, "maxls": 50})
        if fit.success and math.isfinite(float(fit.fun)) and np.isfinite(fit.x).all():
            gradient = np.asarray(fit.jac, dtype=float)
            projected = gradient.copy()
            projected[(fit.x <= 1e-9) & (gradient > 0)] = 0.0
            if float(np.linalg.norm(projected, ord=np.inf)) <= 1e-7:
                fits.append(fit)
    if not fits:
        raise KernelError("reml_nonconvergence", "REML variance-component optimization did not converge")
    best = min(fits, key=lambda fit: float(fit.fun))
    tau_effect, tau_study = np.where(best.x <= 1e-9, 0.0, best.x)
    value, _ = objective(np.asarray([tau_effect, tau_study]))
    if not math.isfinite(value):
        raise KernelError("singular_marginal_covariance", "REML produced a singular marginal covariance matrix")

    marginal = sampling_scaled + tau_effect * identity + tau_study * same_study
    try:
        inverse = np.linalg.solve(marginal, identity)
        information = x.T @ inverse @ x
        model_covariance_scaled = np.linalg.inv(information)
        inverse_x = inverse @ x
        projection = inverse - inverse_x @ model_covariance_scaled @ inverse_x.T
        fisher = .5 * np.asarray([
            [np.trace(projection @ left @ projection @ right)
             for right in (identity, same_study)]
            for left in (identity, same_study)
        ], dtype=float)
        fisher_eigenvalues = np.linalg.eigvalsh(fisher)
        sign_xx, logdet_xx = np.linalg.slogdet(x.T @ x)
        beta = beta_offset + math.sqrt(scale) * (model_covariance_scaled @ x.T @ inverse @ y_scaled)
        model_covariance = scale * model_covariance_scaled
    except (FloatingPointError, np.linalg.LinAlgError, ValueError, OverflowError):
        raise KernelError("invalid_reml_estimates", "REML estimates are outside the finite numerical range") from None
    if sign_xx <= 0 or fisher_eigenvalues[0] <= 0 or not np.isfinite(fisher_eigenvalues).all():
        raise KernelError("unidentifiable_variance_components", "REML variance components are not separately identifiable")
    information_condition = float(fisher_eigenvalues[-1] / fisher_eigenvalues[0])
    if not math.isfinite(information_condition) or information_condition > 1e12:
        raise KernelError("unidentifiable_variance_components", "REML variance components are not separately identifiable",
                          information_condition=information_condition)
    log_likelihood = -.5 * (
        (len(rows) - len(names)) * math.log(2 * math.pi)
        + (len(rows) - len(names)) * math.log(scale)
        + value - float(logdet_xx)
    )
    if not np.isfinite(beta).all() or not np.isfinite(model_covariance).all() or not math.isfinite(log_likelihood):
        raise KernelError("invalid_reml_estimates", "REML estimates are outside the finite numerical range")

    marginal_covariance = marginal * scale
    level_value = _number(level, "level")
    if not 0 < level_value < 1:
        raise KernelError("invalid_level", "level must be strictly between 0 and 1")
    robust_inference = _cr2_inference(
        y, x, beta, model_covariance, marginal_covariance, cluster_ids, names,
        level_value, constraints, constraint_names, null_values)
    return {
        "model": "three_level_random_intercept_meta_regression",
        "n_effects": len(rows), "n_studies": len(studies),
        "n_clusters": len(set(cluster_ids)), "cluster_ids": list(dict.fromkeys(cluster_ids)),
        "effect_ids": effect_ids, "design_columns": names,
        "coefficients": [{
            "name": name, "estimate": float(beta[index]),
            "model_based_se": math.sqrt(float(model_covariance[index, index])),
        } for index, name in enumerate(names)],
        "model_based_covariance": model_covariance.tolist(),
        "variance_components": {
            "study": float(tau_study * scale),
            "effect_within_study": float(tau_effect * scale),
        },
        "sampling_error_assumption": (
            "independent_within_studies" if within_study_covariances is None
            else "supplied_known_within_study_covariance"
        ),
        "fit": {
            "method": "REML", "converged": True, "optimizer": "L-BFGS-B",
            "iterations": int(best.nit), "reml_log_likelihood": float(log_likelihood),
            "variance_component_information_condition": information_condition,
            "boundary": {"study": float(tau_study) == 0.0,
                         "effect_within_study": float(tau_effect) == 0.0},
        },
        "robust_inference": robust_inference,
        "input_snapshot": {
            "effects": rows, "design_matrix": x.tolist(),
            "design_effect_ids": effect_ids, "design_columns": names,
            "within_study_covariances": covariance_snapshot,
            "fitted_marginal_covariance": marginal_covariance.tolist(),
        },
        "limitations": [
            "Fits study and effect-within-study random intercepts; moderators are fixed numeric design columns.",
            "CR2 clusters must be independent and each random-effects study must be nested in one cluster.",
            "CR2/HTZ use the fitted marginal covariance as their working target; variance components are treated as fitted.",
            "Sampling variances and supplied sampling covariances are treated as known.",
        ],
    }
