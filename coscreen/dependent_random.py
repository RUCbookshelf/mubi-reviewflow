"""Numerics for bivariate correlated-outcome random-effects REML."""

from __future__ import annotations

import math

import numpy as np
from scipy.optimize import minimize
from scipy.stats import norm


_Z_975 = float(norm.ppf(.975))
_CITATION_REMINDER = (
    "Report the source and assumptions for each within-study covariance matrix; "
    "cite Cheung (2015), Chapter 5, §5.3, pp. 127–133."
)


def synthesize_random_reml_un(
    fixed_result: dict,
    rows_by_block: list[list[dict]],
    matrices: list[np.ndarray],
    outcome_order: list[str],
    measure: str,
) -> dict:
    """Replace the fixed fit with a bivariate UN random-effects REML fit.

    Inputs have already passed the shared selected-effect and covariance
    validation in ``dependent_synthesis``. Each study contributes its observed
    outcome submatrix, so missing outcomes are handled by selection.
    """
    if len(outcome_order) != 2:
        raise ValueError("random_reml_un requires exactly two distinct outcomes")

    paired_studies = sum(len(rows) == 2 for rows in rows_by_block)
    if paired_studies < 3:
        raise ValueError(
            "between-study covariance is not identifiable with fewer than three studies "
            "contributing both outcomes"
        )

    outcome_index = {outcome: index for index, outcome in enumerate(outcome_order)}
    scale = max(
        1.0,
        *(abs(math.log(float(row["estimate"])) if measure in {"RR", "OR", "HR", "RATE_RATIO"}
              else float(row["estimate"]))
          for rows in rows_by_block for row in rows),
        *(math.sqrt(float(matrix[index, index]))
          for matrix in matrices for index in range(len(matrix))),
    )
    values_by_block: list[np.ndarray] = []
    outcomes_by_block: list[list[int]] = []
    scaled_matrices: list[np.ndarray] = []
    for rows, matrix in zip(rows_by_block, matrices):
        values_by_block.append(np.asarray([
            (math.log(float(row["estimate"])) if measure in {"RR", "OR", "HR", "RATE_RATIO"}
             else float(row["estimate"])) / scale
            for row in rows
        ], dtype=float))
        outcomes_by_block.append([outcome_index[row["outcome"]] for row in rows])
        scaled_matrices.append(matrix / scale / scale)

    if any(not np.isfinite(values).all() for values in values_by_block) or any(
        not np.isfinite(matrix).all() for matrix in scaled_matrices
    ):
        raise ValueError("random-effects REML inputs are outside the finite numerical range")

    n_effects = sum(len(rows) for rows in rows_by_block)
    outcome_counts = [sum(index == outcome for block in outcomes_by_block for index in block)
                      for outcome in range(2)]
    logdet_design = sum(math.log(count) for count in outcome_counts)
    n_parameters = 2

    def covariance(parameters: np.ndarray) -> np.ndarray:
        sd1, sd2, rho = map(float, parameters)
        cov12 = rho * sd1 * sd2
        return np.asarray([[sd1 * sd1, cov12], [cov12, sd2 * sd2]])

    def evaluate(parameters: np.ndarray, details: bool = False):
        tau = covariance(parameters)
        information = np.zeros((2, 2), dtype=float)
        score = np.zeros(2, dtype=float)
        logdet_marginal = 0.0
        ywy = 0.0
        for values, outcomes, sampling_covariance in zip(
            values_by_block, outcomes_by_block, scaled_matrices
        ):
            design = np.zeros((len(outcomes), 2), dtype=float)
            design[np.arange(len(outcomes)), outcomes] = 1.0
            marginal = sampling_covariance + tau[np.ix_(outcomes, outcomes)]
            try:
                sign, logdet = np.linalg.slogdet(marginal)
                if sign <= 0 or not math.isfinite(float(logdet)):
                    return (math.inf, None) if details else math.inf
                weighted_design = np.linalg.solve(marginal, design)
                weighted_values = np.linalg.solve(marginal, values)
            except np.linalg.LinAlgError:
                return (math.inf, None) if details else math.inf
            information += design.T @ weighted_design
            score += design.T @ weighted_values
            logdet_marginal += float(logdet)
            ywy += float(values @ weighted_values)
        try:
            sign, logdet_information = np.linalg.slogdet(information)
            if sign <= 0 or not math.isfinite(float(logdet_information)):
                return (math.inf, None) if details else math.inf
            coefficient_covariance = np.linalg.inv(information)
            coefficients = coefficient_covariance @ score
        except np.linalg.LinAlgError:
            return (math.inf, None) if details else math.inf
        residual_quadratic = ywy - float(score @ coefficients)
        residual_quadratic = max(0.0, residual_quadratic)
        objective = .5 * (
            (n_effects - n_parameters) * math.log(2 * math.pi)
            + logdet_marginal + float(logdet_information) - logdet_design
            + residual_quadratic
        )
        if not math.isfinite(objective):
            return (math.inf, None) if details else math.inf
        fit = {
            "tau": tau,
            "coefficients": coefficients,
            "coefficient_covariance": coefficient_covariance,
            "residual_quadratic": residual_quadratic,
            "objective": objective,
        }
        return (objective, fit) if details else objective

    initial_sds = []
    marginal_residuals = []
    for outcome in range(2):
        values, variances = [], []
        for block_values, block_outcomes, matrix in zip(
            values_by_block, outcomes_by_block, scaled_matrices
        ):
            for row_index, index in enumerate(block_outcomes):
                if index == outcome:
                    values.append(float(block_values[row_index]))
                    variances.append(float(matrix[row_index, row_index]))
        weights = 1 / np.asarray(variances)
        observed = np.asarray(values)
        mean = float(weights @ observed / weights.sum())
        residuals = observed - mean
        marginal_residuals.append(residuals)
        q = float(weights @ (residuals * residuals))
        c = float(weights.sum() - weights @ weights / weights.sum())
        tau2 = max(0.0, (q - (len(values) - 1)) / c) if c > 0 else 0.0
        initial_sds.append(max(math.sqrt(tau2), 0.05))
    paired_residuals = [
        (float(values[0]) - float(np.mean(marginal_residuals[0])),
         float(values[1]) - float(np.mean(marginal_residuals[1])))
        for values, outcomes in zip(values_by_block, outcomes_by_block)
        if len(outcomes) == 2
    ]
    paired_array = np.asarray(paired_residuals)
    if np.any(np.std(paired_array, axis=0) <= 1e-12):
        rho_initial = 0.0
    else:
        rho_initial = float(np.corrcoef(paired_array.T)[0, 1])
    if not math.isfinite(rho_initial):
        rho_initial = 0.0
    rho_initial = min(.9, max(-.9, rho_initial))
    initial = np.asarray([*initial_sds, rho_initial], dtype=float)
    try:
        optimized = minimize(
            evaluate,
            initial,
            method="L-BFGS-B",
            jac="3-point",
            bounds=((0.0, None), (0.0, None), (-1.0, 1.0)),
            options={"ftol": 1e-15, "gtol": 1e-10, "maxiter": 5000, "maxls": 100,
                     "finite_diff_rel_step": 1e-5},
        )
    except (FloatingPointError, OverflowError, ValueError) as exc:
        raise ValueError(f"random-effects REML optimization failed: {exc}") from None
    if not optimized.success or not math.isfinite(float(optimized.fun)):
        raise ValueError(f"random-effects REML did not converge: {optimized.message}")
    objective, fitted = evaluate(optimized.x, details=True)
    if fitted is None:
        raise ValueError("random-effects REML did not produce an identifiable fit")

    # Check expected REML information for the three unique elements of T.
    total = n_effects
    design = np.zeros((total, 2), dtype=float)
    marginal_covariance = np.zeros((total, total), dtype=float)
    derivative_matrices = [np.zeros((total, total), dtype=float) for _ in range(3)]
    offset = 0
    tau = fitted["tau"]
    for outcomes, sampling_covariance in zip(outcomes_by_block, scaled_matrices):
        count = len(outcomes)
        rows = np.arange(offset, offset + count)
        design[rows, outcomes] = 1.0
        marginal_covariance[np.ix_(rows, rows)] = (
            sampling_covariance + tau[np.ix_(outcomes, outcomes)])
        for local_index, outcome in enumerate(outcomes):
            derivative_matrices[outcome][offset + local_index, offset + local_index] = 1.0
        if count == 2:
            derivative_matrices[2][offset, offset + 1] = 1.0
            derivative_matrices[2][offset + 1, offset] = 1.0
        offset += count
    try:
        weight = np.linalg.inv(marginal_covariance)
        fixed_information = design.T @ weight @ design
        weighted_design = weight @ design
        projection = weight - weighted_design @ np.linalg.solve(
            fixed_information, weighted_design.T)
        fisher = np.asarray([
            [0.5 * np.trace(projection @ left @ projection @ right)
             for right in derivative_matrices]
            for left in derivative_matrices
        ])
        fisher_scale = np.sqrt(np.diag(fisher))
        if (not np.isfinite(fisher).all() or np.any(fisher_scale <= 0)
                or not np.isfinite(fisher_scale).all()):
            raise np.linalg.LinAlgError
        fisher_correlation = fisher / np.outer(fisher_scale, fisher_scale)
        eigenvalues = np.linalg.eigvalsh(fisher_correlation)
    except np.linalg.LinAlgError:
        raise ValueError("between-study covariance is not identifiable from the supplied studies") from None
    if (not np.isfinite(eigenvalues).all() or eigenvalues[0] <= 1e-10 * eigenvalues[-1]):
        raise ValueError("between-study covariance is not identifiable from the supplied studies")
    information_condition = float(eigenvalues[-1] / eigenvalues[0])

    projected_gradient = np.asarray(optimized.jac, dtype=float).copy()
    for index, (lower, upper) in enumerate(((0.0, None), (0.0, None), (-1.0, 1.0))):
        if lower is not None and optimized.x[index] <= lower + 1e-7 and projected_gradient[index] > 0:
            projected_gradient[index] = 0.0
        if upper is not None and optimized.x[index] >= upper - 1e-7 and projected_gradient[index] < 0:
            projected_gradient[index] = 0.0
    gradient_norm = float(np.linalg.norm(projected_gradient, ord=np.inf))
    if not math.isfinite(gradient_norm):
        raise ValueError("random-effects REML convergence diagnostics are not finite")
    if gradient_norm > 1e-4:
        raise ValueError(f"random-effects REML did not converge: projected gradient norm {gradient_norm:.3g}")
    tau = fitted["tau"] * scale * scale
    coefficients = fitted["coefficients"] * scale
    coefficient_covariance = fitted["coefficient_covariance"] * scale * scale
    if (not np.isfinite(tau).all() or not np.isfinite(coefficients).all()
            or not np.isfinite(coefficient_covariance).all()):
        raise ValueError("random-effects REML estimates are outside the finite numerical range")
    if np.linalg.eigvalsh(tau)[0] < -1e-10 * max(1.0, float(np.max(np.diag(tau)))):
        raise ValueError("estimated between-study covariance is not positive semidefinite")

    estimates = []
    for index, outcome in enumerate(outcome_order):
        se = math.sqrt(float(coefficient_covariance[index, index]))
        lower = float(coefficients[index]) - _Z_975 * se
        upper = float(coefficients[index]) + _Z_975 * se
        if measure in {"RR", "OR", "HR", "RATE_RATIO"}:
            try:
                estimate, ci_lower, ci_upper = map(math.exp, (float(coefficients[index]), lower, upper))
            except OverflowError:
                raise ValueError("ratio estimate or confidence limits are not representable as finite numbers") from None
            if any(not math.isfinite(value) or value <= 0 for value in (estimate, ci_lower, ci_upper)):
                raise ValueError("ratio estimate or confidence limits are not representable as finite numbers")
        else:
            estimate, ci_lower, ci_upper = float(coefficients[index]), lower, upper
        original = fixed_result["estimates"][index]
        estimates.append({
            **original,
            "estimate": float(estimate),
            "se": se,
            "ci_lower": float(ci_lower),
            "ci_upper": float(ci_upper),
        })

    warnings = []
    if paired_studies < 5:
        warnings.append(
            "Fewer than five studies contribute both outcomes; the unstructured "
            "between-study covariance may be unstable."
        )
    result = {
        **fixed_result,
        "model": "random_reml_un",
        "estimates": estimates,
        "coefficient_covariance": {
            "outcomes": outcome_order,
            "scale": fixed_result["analysis_scale"],
            "matrix": coefficient_covariance.tolist(),
        },
        "between_study_covariance": {
            "outcomes": outcome_order,
            "scale": fixed_result["analysis_scale"],
            "matrix": tau.tolist(),
        },
        "fit": {
            "method": "REML",
            "converged": True,
            "optimizer": "L-BFGS-B",
            "iterations": int(optimized.nit),
            "reml_log_likelihood": float(-objective - (n_effects - n_parameters) * math.log(scale)),
"log_likelihood_convention": "basis-invariant REML normalization (-0.5*log|X'X|; numerically identical to metafor/nlme logLik, offset from the textbook Harville constant by that term)",
            "gradient_norm": gradient_norm,
            "boundary": bool(np.linalg.eigvalsh(tau)[0] <= 1e-8 * max(1.0, float(np.max(np.diag(tau))))),
            "covariance_information_condition": information_condition,
            "n_paired_studies": paired_studies,
            "warnings": warnings,
        },
        "warnings": warnings,
        "q": {**fixed_result["q"], "scope": "sampling_error_heterogeneity"},
        "limitations": [
            "Fits two outcome-specific means and a positive semidefinite unstructured between-study covariance matrix.",
            "Study blocks are independent, and supplied within-study covariance matrices are treated as known.",
            "The 95% confidence intervals and sampling-error heterogeneity Q use large-sample normal and chi-square approximations.",
            "An unstructured between-study covariance can be unstable with few studies, even when the fit is identifiable.",
        ],
        "citation_reminder": _CITATION_REMINDER,
    }
    return result
