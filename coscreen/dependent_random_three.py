"""Numerics for three-outcome correlated random-effects REML."""

from __future__ import annotations

import math

import numpy as np
from scipy.optimize import minimize
from scipy.stats import norm


_Z_975 = float(norm.ppf(.975))
_RATIOS = {"RR", "OR", "HR", "RATE_RATIO"}
_CITATION_REMINDER = (
    "Report the source and assumptions for each within-study covariance matrix; "
    "cite Cheung (2015), Chapter 5, §5.3, pp. 127–133."
)
_COVARIANCE_ELEMENTS = ((0, 0), (1, 1), (2, 2), (0, 1), (0, 2), (1, 2))


def synthesize_random_reml_un_three(
    fixed_result: dict,
    rows_by_block: list[list[dict]],
    matrices: list[np.ndarray],
    outcome_order: list[str],
    measure: str,
) -> dict:
    """Fit an unstructured 3x3 between-study covariance by REML.

    The caller has already validated each study's selected effects and known
    sampling covariance. Missing outcomes are handled by taking the observed
    principal submatrix of the between-study covariance for that study.
    """
    if len(outcome_order) != 3 or len(set(outcome_order)) != 3:
        raise ValueError("random_reml_un requires exactly three distinct outcomes")

    outcome_index = {outcome: index for index, outcome in enumerate(outcome_order)}
    pair_counts = {
        (left, right): sum(
            left in {outcome_index[row["outcome"]] for row in rows}
            and right in {outcome_index[row["outcome"]] for row in rows}
            for rows in rows_by_block
        )
        for left in range(3) for right in range(left + 1, 3)
    }
    if min(pair_counts.values()) < 3:
        raise ValueError(
            "between-study covariance is not identifiable with fewer than three "
            "overlapping studies for an outcome pair"
        )

    try:
        values_by_block = []
        outcomes_by_block = []
        analysis_values = []
        for rows in rows_by_block:
            values = np.asarray([
                math.log(float(row["estimate"])) if measure in _RATIOS
                else float(row["estimate"])
                for row in rows
            ], dtype=float)
            values_by_block.append(values)
            outcomes_by_block.append([outcome_index[row["outcome"]] for row in rows])
            analysis_values.extend(np.abs(values))
        scale = max(
            1.0,
            *(float(value) for value in analysis_values),
            *(math.sqrt(float(matrix[index, index]))
              for matrix in matrices for index in range(len(matrix))),
        )
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            values_by_block = [values / scale for values in values_by_block]
            scaled_matrices = [matrix / scale / scale for matrix in matrices]
        if (any(not np.isfinite(values).all() for values in values_by_block)
                or any(not np.isfinite(matrix).all() for matrix in scaled_matrices)):
            raise FloatingPointError
    except (FloatingPointError, OverflowError, ValueError):
        raise ValueError("random-effects REML inputs are outside the finite numerical range") from None

    n_effects = sum(len(rows) for rows in rows_by_block)
    outcome_counts = [sum(index == outcome for block in outcomes_by_block for index in block)
                      for outcome in range(3)]
    logdet_design = sum(math.log(count) for count in outcome_counts)
    sampling_scale = max(float(np.linalg.eigvalsh(matrix)[-1]) for matrix in scaled_matrices)

    def factor_matrix(parameters: np.ndarray) -> np.ndarray:
        return np.asarray([
            [parameters[0], 0.0, 0.0],
            [parameters[1], parameters[2], 0.0],
            [parameters[3], parameters[4], parameters[5]],
        ])

    def evaluate(parameters: np.ndarray, details: bool = False):
        if not np.isfinite(parameters).all() or np.any(parameters[[0, 2, 5]] < 0):
            return (math.inf, None) if details else math.inf
        try:
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                factor = factor_matrix(parameters)
                tau = factor @ factor.T
                information = np.zeros((3, 3), dtype=float)
                score = np.zeros(3, dtype=float)
                logdet_marginal = 0.0
                ywy = 0.0
                for values, outcomes, sampling_covariance in zip(
                    values_by_block, outcomes_by_block, scaled_matrices
                ):
                    design = np.zeros((len(outcomes), 3), dtype=float)
                    design[np.arange(len(outcomes)), outcomes] = 1.0
                    marginal = sampling_covariance + tau[np.ix_(outcomes, outcomes)]
                    sign, logdet = np.linalg.slogdet(marginal)
                    if sign <= 0 or not math.isfinite(float(logdet)):
                        return (math.inf, None) if details else math.inf
                    weighted_design = np.linalg.solve(marginal, design)
                    weighted_values = np.linalg.solve(marginal, values)
                    information += design.T @ weighted_design
                    score += design.T @ weighted_values
                    logdet_marginal += float(logdet)
                    ywy += float(values @ weighted_values)
                sign, logdet_information = np.linalg.slogdet(information)
                if sign <= 0 or not math.isfinite(float(logdet_information)):
                    return (math.inf, None) if details else math.inf
                coefficient_covariance = np.linalg.inv(information)
                coefficients = coefficient_covariance @ score
                residual_quadratic = max(0.0, ywy - float(score @ coefficients))
                objective = .5 * (
                    (n_effects - 3) * math.log(2 * math.pi)
                    + logdet_marginal + float(logdet_information) - logdet_design
                    + residual_quadratic
                )
        except (FloatingPointError, OverflowError, np.linalg.LinAlgError):
            return (math.inf, None) if details else math.inf
        if not math.isfinite(objective):
            return (math.inf, None) if details else math.inf
        fitted = {
            "tau": tau,
            "coefficients": coefficients,
            "coefficient_covariance": coefficient_covariance,
        }
        return (objective, fitted) if details else objective

    initial_diagonals = []
    for outcome in range(3):
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
        q = float(weights @ (residuals * residuals))
        c = float(weights.sum() - weights @ weights / weights.sum())
        tau2 = max(0.0, (q - (len(values) - 1)) / c) if c > 0 else 0.0
        initial_diagonals.append(max(math.sqrt(tau2), .05))
    initial = np.asarray([
        initial_diagonals[0], 0.0, initial_diagonals[1],
        0.0, 0.0, initial_diagonals[2],
    ])

    def correlated_start(correlation: float) -> np.ndarray:
        correlation_matrix = np.full((3, 3), correlation, dtype=float)
        np.fill_diagonal(correlation_matrix, 1.0)
        factor = np.diag(initial_diagonals) @ np.linalg.cholesky(correlation_matrix)
        return np.asarray([
            factor[0, 0], factor[1, 0], factor[1, 1],
            factor[2, 0], factor[2, 1], factor[2, 2],
        ])

    starts = (initial, correlated_start(.35), correlated_start(-.35))
    bounds = ((0.0, None), (None, None), (0.0, None),
              (None, None), (None, None), (0.0, None))
    valid_fits = []
    failures = []
    optimizer_succeeded = False
    boundary_reached = False
    boundary_objectives = []
    information_failed = False
    for start_index, start in enumerate(starts, start=1):
        try:
            optimized = minimize(
                evaluate,
                start,
                method="L-BFGS-B",
                jac="3-point",
                bounds=bounds,
                options={
                    "ftol": 1e-15,
                    "gtol": 1e-10,
                    "maxiter": 5000,
                    "maxls": 100,
                    "finite_diff_rel_step": 1e-5,
                },
            )
        except (FloatingPointError, OverflowError, ValueError) as exc:
            failures.append(str(exc))
            continue
        if not optimized.success or not math.isfinite(float(optimized.fun)):
            failures.append(str(optimized.message))
            continue
        optimizer_succeeded = True

        objective, fitted = evaluate(optimized.x, details=True)
        if fitted is None or not math.isfinite(objective):
            failures.append("optimizer returned no finite fit")
            continue
        gradient = np.asarray(optimized.jac, dtype=float).copy()
        if gradient.shape != (6,) or not np.isfinite(gradient).all():
            failures.append("convergence diagnostics are not finite")
            continue
        for index in (0, 2, 5):
            if optimized.x[index] <= 1e-7 and gradient[index] > 0:
                gradient[index] = 0.0
        gradient_norm = float(np.linalg.norm(gradient, ord=np.inf))
        if not math.isfinite(gradient_norm) or gradient_norm > 1e-4:
            failures.append(f"projected gradient norm {gradient_norm:.3g}")
            continue

        tau_eigenvalues = np.linalg.eigvalsh(fitted["tau"])
        tau_scale = max(float(tau_eigenvalues[-1]), sampling_scale, np.finfo(float).tiny)
        if tau_eigenvalues[0] <= 1e-8 * tau_scale:
            boundary_reached = True
            boundary_objectives.append(objective)
            failures.append("singular covariance boundary")
            continue
        try:
            information_condition = _check_covariance_information(
                outcomes_by_block, scaled_matrices, fitted["tau"]
            )
        except ValueError as exc:
            information_failed = True
            failures.append(str(exc))
            continue
        valid_fits.append({
            "objective": objective,
            "fitted": fitted,
            "optimized": optimized,
            "gradient_norm": gradient_norm,
            "information_condition": information_condition,
            "start_index": start_index,
        })

    if not valid_fits:
        if boundary_reached:
            raise ValueError(
                "random-effects REML reached a singular covariance boundary; "
                "boundary fits are unsupported for three outcomes"
            )
        if information_failed:
            raise ValueError("between-study covariance is not identifiable from the supplied studies")
        if not optimizer_succeeded:
            detail = failures[0] if failures else "no optimizer returned a fit"
            raise ValueError(f"random-effects REML did not converge from any starting point: {detail}")
        detail = failures[0] if failures else "no candidate passed fit diagnostics"
        raise ValueError(f"random-effects REML did not converge from any starting point: {detail}")

    best_fit = min(valid_fits, key=lambda candidate: candidate["objective"])
    if boundary_objectives and min(boundary_objectives) <= best_fit["objective"] + 1e-8 * max(
        1.0, abs(best_fit["objective"])
    ):
        raise ValueError(
            "random-effects REML reached a singular covariance boundary at the best fit; "
            "boundary fits are unsupported for three outcomes"
        )
    optimized = best_fit["optimized"]
    objective = best_fit["objective"]
    fitted = best_fit["fitted"]
    information_condition = best_fit["information_condition"]
    gradient_norm = best_fit["gradient_norm"]
    paired_studies = sum(len(rows) >= 2 for rows in rows_by_block)

    tau = fitted["tau"] * scale * scale
    coefficients = fitted["coefficients"] * scale
    coefficient_covariance = fitted["coefficient_covariance"] * scale * scale
    if (not np.isfinite(tau).all() or not np.isfinite(coefficients).all()
            or not np.isfinite(coefficient_covariance).all()):
        raise ValueError("random-effects REML estimates are outside the finite numerical range")
    tau_eigenvalues = np.linalg.eigvalsh(tau)
    tau_scale = max(float(tau_eigenvalues[-1]), np.finfo(float).tiny)
    if tau_eigenvalues[0] < -1e-10 * tau_scale:
        raise ValueError("estimated between-study covariance is not positive semidefinite")

    estimates = []
    for index, outcome in enumerate(outcome_order):
        se = math.sqrt(float(coefficient_covariance[index, index]))
        lower = float(coefficients[index]) - _Z_975 * se
        upper = float(coefficients[index]) + _Z_975 * se
        if not all(math.isfinite(value) for value in (se, lower, upper)):
            raise ValueError("random-effects REML confidence intervals are outside the finite numerical range")
        if measure in _RATIOS:
            try:
                estimate, ci_lower, ci_upper = map(
                    math.exp, (float(coefficients[index]), lower, upper)
                )
            except OverflowError:
                raise ValueError("ratio estimate or confidence limits are not representable as finite numbers") from None
            if any(not math.isfinite(value) or value <= 0
                   for value in (estimate, ci_lower, ci_upper)):
                raise ValueError("ratio estimate or confidence limits are not representable as finite numbers")
        else:
            estimate, ci_lower, ci_upper = float(coefficients[index]), lower, upper
        estimates.append({
            **fixed_result["estimates"][index],
            "estimate": float(estimate),
            "se": se,
            "ci_lower": float(ci_lower),
            "ci_upper": float(ci_upper),
        })

    pairwise_overlaps = {
        f"{outcome_order[left]}|{outcome_order[right]}": count
        for (left, right), count in pair_counts.items()
    }
    warnings = []
    if min(pair_counts.values()) < 5:
        warnings.append(
            "Fewer than five studies overlap for at least one outcome pair; the "
            "unstructured between-study covariance may be unstable."
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
            "starts_attempted": len(starts),
            "valid_starts": len(valid_fits),
            "selected_start": best_fit["start_index"],
            "reml_log_likelihood": float(-objective - (n_effects - 3) * math.log(scale)),
"log_likelihood_convention": "basis-invariant REML normalization (-0.5*log|X'X|; numerically identical to metafor/nlme logLik, offset from the textbook Harville constant by that term)",
            "gradient_norm": gradient_norm,
            "boundary": False,
            "covariance_information_condition": information_condition,
            "n_paired_studies": paired_studies,
            "n_complete_studies": sum(len(rows) == 3 for rows in rows_by_block),
            "pairwise_overlap_studies": pairwise_overlaps,
            "warnings": warnings,
        },
        "warnings": warnings,
        "q": {**fixed_result["q"], "scope": "sampling_error_heterogeneity"},
        "limitations": [
            "Fits three outcome-specific means and a positive definite unstructured between-study covariance matrix.",
            "Study blocks are independent, and supplied within-study covariance matrices are treated as known.",
            "The 95% confidence intervals and sampling-error heterogeneity Q use large-sample normal and chi-square approximations.",
            "Each outcome pair must overlap in at least three studies; singular boundary fits are unsupported.",
        ],
        "citation_reminder": _CITATION_REMINDER,
    }
    return result


def _check_covariance_information(
    outcomes_by_block: list[list[int]],
    scaled_matrices: list[np.ndarray],
    tau: np.ndarray,
) -> float:
    fixed_information = np.zeros((3, 3), dtype=float)
    block_information = []
    for outcomes, sampling_covariance in zip(outcomes_by_block, scaled_matrices):
        count = len(outcomes)
        design = np.zeros((count, 3), dtype=float)
        design[np.arange(count), outcomes] = 1.0
        marginal = sampling_covariance + tau[np.ix_(outcomes, outcomes)]
        try:
            weight = np.linalg.solve(marginal, np.eye(count))
        except np.linalg.LinAlgError:
            raise ValueError("between-study covariance is not identifiable from the supplied studies") from None
        weight = (weight + weight.T) / 2
        weighted_design = weight @ design
        fixed_information += design.T @ weighted_design
        local_index = {outcome: index for index, outcome in enumerate(outcomes)}
        derivatives = []
        for left, right in _COVARIANCE_ELEMENTS:
            derivative = np.zeros((count, count), dtype=float)
            if left == right and left in local_index:
                derivative[local_index[left], local_index[left]] = 1.0
            elif left != right and left in local_index and right in local_index:
                derivative[local_index[left], local_index[right]] = 1.0
                derivative[local_index[right], local_index[left]] = 1.0
            derivatives.append(derivative)
        block_information.append((weight, weighted_design, derivatives))
    try:
        fixed_covariance = np.linalg.solve(fixed_information, np.eye(3))
        block_derivative_sums = [np.zeros((3, 3), dtype=float) for _ in _COVARIANCE_ELEMENTS]
        for _, weighted_design, derivatives in block_information:
            for index, derivative in enumerate(derivatives):
                block_derivative_sums[index] += weighted_design.T @ derivative @ weighted_design

        fisher = np.zeros((len(_COVARIANCE_ELEMENTS), len(_COVARIANCE_ELEMENTS)), dtype=float)
        for left_index in range(len(_COVARIANCE_ELEMENTS)):
            for right_index in range(len(_COVARIANCE_ELEMENTS)):
                base = 0.0
                cross_left = 0.0
                cross_right = 0.0
                for weight, weighted_design, derivatives in block_information:
                    left = derivatives[left_index]
                    right = derivatives[right_index]
                    base += float(np.trace(weight @ left @ weight @ right))
                    cross_left += float(np.trace(
                        fixed_covariance @ weighted_design.T @ left @ weight @ right @ weighted_design
                    ))
                    cross_right += float(np.trace(
                        fixed_covariance @ weighted_design.T @ right @ weight @ left @ weighted_design
                    ))
                adjustment = float(np.trace(
                    fixed_covariance @ block_derivative_sums[left_index]
                    @ fixed_covariance @ block_derivative_sums[right_index]
                ))
                fisher[left_index, right_index] = .5 * (
                    base - cross_left - cross_right + adjustment
                )
        fisher = (fisher + fisher.T) / 2
        fisher_scale = np.sqrt(np.diag(fisher))
        if (not np.isfinite(fisher).all() or np.any(fisher_scale <= 0)
                or not np.isfinite(fisher_scale).all()):
            raise np.linalg.LinAlgError
        eigenvalues = np.linalg.eigvalsh(fisher / np.outer(fisher_scale, fisher_scale))
    except (FloatingPointError, OverflowError, np.linalg.LinAlgError):
        raise ValueError("between-study covariance is not identifiable from the supplied studies") from None
    if (not np.isfinite(eigenvalues).all()
            or eigenvalues[0] <= 1e-10 * eigenvalues[-1]):
        raise ValueError("between-study covariance is not identifiable from the supplied studies")
    return float(eigenvalues[-1] / eigenvalues[0])
