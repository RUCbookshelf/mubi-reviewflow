"""Three-level random-intercept REML for multiple effects per independent study."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from numbers import Real

import numpy as np
from scipy.optimize import minimize
from scipy.stats import norm

_MAX_EFFECTS = 500


def _number(value: object, label: str) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise ValueError(f"{label} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError):
        raise ValueError(f"{label} must be a finite number") from None
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _study_covariances(
    rows: list[dict],
    variances: np.ndarray,
    supplied: Mapping[str, Sequence[Sequence[float]]] | None,
) -> tuple[np.ndarray, dict[str, list[list[float]]]]:
    study_ids = list(dict.fromkeys(row["study_id"] for row in rows))
    rows_by_study = {study_id: [] for study_id in study_ids}
    for index, row in enumerate(rows):
        rows_by_study[row["study_id"]].append(index)

    if supplied is not None:
        if not isinstance(supplied, Mapping) or set(supplied) != set(study_ids):
            raise ValueError("supply one complete covariance matrix per study")

    sampling = np.zeros((len(rows), len(rows)), dtype=float)
    snapshot: dict[str, list[list[float]]] = {}
    for study_id, indices in rows_by_study.items():
        size = len(indices)
        if supplied is None:
            matrix = np.diag(variances[indices])
        else:
            try:
                matrix = np.asarray(supplied[study_id], dtype=float)
            except (TypeError, ValueError, OverflowError):
                raise ValueError(f"study {study_id} covariance must be a finite square matrix") from None
            if matrix.shape != (size, size) or not np.isfinite(matrix).all():
                raise ValueError(f"study {study_id} covariance must be a finite {size} by {size} matrix")
            magnitude = max(float(np.max(np.abs(matrix))), np.finfo(float).tiny)
            if float(np.max(np.abs(matrix - matrix.T))) > magnitude * 1e-10:
                raise ValueError(f"study {study_id} covariance must be symmetric")
            matrix = (matrix + matrix.T) / 2
            diagonal_tolerance = max(float(np.max(variances[indices])) * 1e-12,
                                     np.finfo(float).tiny)
            if not np.allclose(np.diag(matrix), variances[indices], rtol=1e-7,
                               atol=diagonal_tolerance):
                raise ValueError(f"study {study_id} covariance diagonal must match effect variances")
            try:
                eigenvalues = np.linalg.eigvalsh(matrix)
            except np.linalg.LinAlgError:
                raise ValueError(f"study {study_id} covariance must be positive semidefinite") from None
            if float(eigenvalues[0]) < -magnitude * 1e-10:
                raise ValueError(f"study {study_id} covariance must be positive semidefinite")

        sampling[np.ix_(indices, indices)] = matrix
        snapshot[study_id] = matrix.tolist()
    return sampling, snapshot


def fit_three_level_random_intercept(
    effects: Sequence[Mapping],
    within_study_covariances: Mapping[str, Sequence[Sequence[float]]] | None = None,
) -> dict:
    """Fit a common-mean, three-level random-intercept meta-analysis by REML.

    Each effect row needs ``study_id``, ``source_id``, ``estimate``, and the
    known sampling ``variance``. Optional ``effect_id`` and ``source_locator``
    values are copied into the input snapshot. Covariance matrices, when given,
    must include one complete matrix per study in that study's input-row order.
    """
    if not isinstance(effects, (list, tuple)) or not effects:
        raise ValueError("effects must be a non-empty sequence of rows")
    # ponytail: dense REML capped at 500 effects (~2 MB per float matrix); blockwise REML if larger reviews need it.
    if len(effects) > _MAX_EFFECTS:
        raise ValueError(f"at most {_MAX_EFFECTS} effects are supported per fit")

    rows = []
    estimates, variances = [], []
    for index, effect in enumerate(effects):
        if not isinstance(effect, Mapping):
            raise ValueError("each effect must be a mapping")
        study_id, source_id = effect.get("study_id"), effect.get("source_id")
        for value, label in ((study_id, "study_id"), (source_id, "source_id")):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"effect {index + 1} needs a non-empty {label}")
        estimate = _number(effect.get("estimate"), f"estimate for effect {index + 1}")
        variance = _number(effect.get("variance"), f"variance for effect {index + 1}")
        if variance <= 0:
            raise ValueError("each known sampling variance must be positive")
        row = {
            "study_id": study_id,
            "source_id": source_id,
            "estimate": estimate,
            "variance": variance,
        }
        for optional in ("effect_id", "source_locator"):
            if optional in effect:
                row[optional] = effect[optional]
        rows.append(row)
        estimates.append(estimate)
        variances.append(variance)

    study_ids = list(dict.fromkeys(row["study_id"] for row in rows))
    rows_per_study = {study_id: 0 for study_id in study_ids}
    for row in rows:
        rows_per_study[row["study_id"]] += 1
    if len(study_ids) < 3:
        raise ValueError("at least three independent studies are required")
    if sum(count > 1 for count in rows_per_study.values()) < 2:
        raise ValueError("at least two studies must contribute multiple effects")

    y = np.asarray(estimates, dtype=float)
    variance = np.asarray(variances, dtype=float)
    sampling, covariance_snapshot = _study_covariances(
        rows, variance, within_study_covariances)
    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            center = float(np.mean(y))
            effect_scale = float(np.var(y, ddof=1))
    except (FloatingPointError, OverflowError, ValueError):
        raise ValueError("REML inputs are outside the finite numerical range") from None
    scale = max(effect_scale, float(np.max(variance)))
    if not math.isfinite(center) or not math.isfinite(scale) or scale <= 0:
        raise ValueError("REML inputs are outside the finite numerical range")

    try:
        sampling_scaled = sampling / scale
        y_scaled = (y - center) / math.sqrt(scale)
    except (FloatingPointError, OverflowError, ValueError):
        raise ValueError("REML inputs are outside the finite numerical range") from None
    if not np.isfinite(sampling_scaled).all() or not np.isfinite(y_scaled).all():
        raise ValueError("REML inputs are outside the finite numerical range")

    study_blocks = []
    for study_id in study_ids:
        study_blocks.append(np.asarray(
            [index for index, row in enumerate(rows) if row["study_id"] == study_id], dtype=int))
    identity = np.eye(len(rows), dtype=float)
    same_study = np.zeros_like(identity)
    for indices in study_blocks:
        same_study[np.ix_(indices, indices)] = 1.0

    def objective(parameters: np.ndarray) -> tuple[float, np.ndarray]:
        effect_variance, study_variance = parameters
        if not np.isfinite(parameters).all() or np.any(parameters < 0):
            return math.inf, np.zeros(2, dtype=float)
        marginal = sampling_scaled + effect_variance * identity + study_variance * same_study
        try:
            with np.errstate(over="raise", divide="raise", invalid="raise"):
                sign, logdet = np.linalg.slogdet(marginal)
                if sign <= 0 or not math.isfinite(float(logdet)):
                    return math.inf, np.zeros(2, dtype=float)
                inverse = np.linalg.solve(marginal, identity)
                ones = np.ones(len(rows), dtype=float)
                inverse_ones = inverse @ ones
                information = float(ones @ inverse_ones)
                if information <= 0 or not math.isfinite(information):
                    return math.inf, np.zeros(2, dtype=float)
                projection = inverse - np.outer(inverse_ones, inverse_ones) / information
                alpha = projection @ y_scaled
                quadratic = float(y_scaled @ alpha)
                if not math.isfinite(quadratic):
                    return math.inf, np.zeros(2, dtype=float)
                value = float(logdet + math.log(information) + quadratic)
                projected_information = [
                    float(np.trace(projection)),
                    sum(float(np.sum(projection[np.ix_(block, block)]))
                        for block in study_blocks),
                ]
                projected_quadratic = [
                    float(alpha @ alpha),
                    sum(float(np.sum(alpha[block]) ** 2) for block in study_blocks),
                ]
                gradient = np.asarray(projected_information) - np.asarray(projected_quadratic)
        except (FloatingPointError, np.linalg.LinAlgError, ValueError, OverflowError):
            return math.inf, np.zeros(2, dtype=float)
        if not math.isfinite(value) or not np.isfinite(gradient).all():
            return math.inf, np.zeros(2, dtype=float)
        return value, gradient

    fits = []
    for start in ([.1, .1], [.01, 1.0], [1.0, .01], [1.0, 1.0]):
        fit = minimize(objective, np.asarray(start), method="L-BFGS-B", jac=True,
                       bounds=((0.0, None), (0.0, None)),
                       options={"ftol": 1e-15, "gtol": 1e-10, "maxiter": 3000, "maxls": 50})
        if fit.success and math.isfinite(float(fit.fun)) and np.isfinite(fit.x).all():
            gradient = np.asarray(fit.jac, dtype=float)
            projected_gradient = gradient.copy()
            projected_gradient[(fit.x <= 1e-9) & (gradient > 0)] = 0.0
            if float(np.linalg.norm(projected_gradient, ord=np.inf)) <= 1e-7:
                fits.append(fit)
    if not fits:
        raise ValueError("REML variance-component optimization did not converge")
    best = min(fits, key=lambda fit: float(fit.fun))
    estimated_effect = 0.0 if best.x[0] <= 1e-9 else float(best.x[0])
    estimated_study = 0.0 if best.x[1] <= 1e-9 else float(best.x[1])
    objective_value, _ = objective(np.asarray([estimated_effect, estimated_study]))
    if not math.isfinite(objective_value):
        raise ValueError("REML produced a singular marginal covariance matrix")

    marginal = sampling_scaled + estimated_effect * identity + estimated_study * same_study
    try:
        with np.errstate(over="raise", divide="raise", invalid="raise"):
            inverse = np.linalg.solve(marginal, identity)
            ones = np.ones(len(rows), dtype=float)
            inverse_ones = inverse @ ones
            information = float(ones @ inverse_ones)
            projection = inverse - np.outer(inverse_ones, inverse_ones) / information
            beta_scaled = float(inverse_ones @ y_scaled / information)
            q = max(0.0, float(y_scaled @ projection @ y_scaled))
            mean = center + beta_scaled * math.sqrt(scale)
            mean_se = math.sqrt(scale / information)
            fisher_information = .5 * np.asarray([
                [float(np.trace(projection @ projection)),
                 float(np.trace(projection @ same_study @ projection))],
                [float(np.trace(same_study @ projection @ projection)),
                 float(np.trace(projection @ same_study @ projection @ same_study))],
            ])
            fisher_eigenvalues = np.linalg.eigvalsh(fisher_information)
    except (FloatingPointError, np.linalg.LinAlgError, ValueError, OverflowError):
        raise ValueError("REML estimates are outside the finite numerical range") from None

    if not np.isfinite([mean, mean_se, q]).all() or mean_se <= 0:
        raise ValueError("REML estimates are outside the finite numerical range")
    if fisher_eigenvalues[0] <= 0 or not np.isfinite(fisher_eigenvalues).all():
        raise ValueError("REML variance components are not separately identifiable")
    information_condition = float(fisher_eigenvalues[-1] / fisher_eigenvalues[0])
    # ponytail: reject variance-component information above 1e12; profile likelihood diagnostics if finer precision is needed.
    if not math.isfinite(information_condition) or information_condition > 1e12:
        raise ValueError("REML variance components are not separately identifiable")

    # 基不变归一化的 REML（含 +½ln(n) 项）：与 metafor/nlme 的 logLik 数值
    # 一致（回归用例逐位对齐），比教科书 Harville 常数多该项——约定见
    # docs/methodology/multilevel_random_intercept.md。
    reml_log_likelihood = -.5 * (
        (len(rows) - 1) * math.log(2 * math.pi)
        + (len(rows) - 1) * math.log(scale)
        + objective_value
    ) + .5 * math.log(len(rows))
    critical = float(norm.ppf(.975))
    ci_lower, ci_upper = mean - critical * mean_se, mean + critical * mean_se
    if not np.isfinite([reml_log_likelihood, ci_lower, ci_upper]).all():
        raise ValueError("REML confidence limits are outside the finite numerical range")

    return {
        "model": "three_level_random_intercept",
        "n_studies": len(study_ids),
        "n_effects": len(rows),
        "estimate": mean,
        "se": mean_se,
        "ci_95": [ci_lower, ci_upper],
        "variance_components": {
            "study": estimated_study * scale,
            "effect_within_study": estimated_effect * scale,
        },
        "sampling_error_assumption": (
            "independent_within_studies" if within_study_covariances is None
            else "supplied_known_within_study_covariance"
        ),
        "fit": {
            "method": "REML",
            "converged": True,
            "optimizer": "L-BFGS-B",
            "iterations": int(best.nit),
            "reml_log_likelihood": reml_log_likelihood,
            "log_likelihood_convention": "basis-invariant REML normalization (+0.5*ln(n) for the intercept-only design; numerically identical to metafor/nlme logLik, offset from the textbook Harville constant by that term)",
            "variance_component_information_condition": information_condition,
            "boundary": {
                "study": estimated_study == 0.0,
                "effect_within_study": estimated_effect == 0.0,
            },
        },
        "input_snapshot": {
            "effects": rows,
            "within_study_covariances": covariance_snapshot,
        },
        "limitations": [
            "Assumes independent normally distributed study and effect-within-study random intercepts and a common mean.",
            "Sampling errors are independent within each study unless a complete known within-study covariance is supplied; studies are independent.",
            "Sampling variances and any supplied covariances are treated as known; intervals for the common mean use a normal approximation.",
        ],
    }
