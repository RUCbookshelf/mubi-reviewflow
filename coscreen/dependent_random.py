"""Numerics for bivariate correlated-outcome random-effects REML."""

from __future__ import annotations

import math
import time

import numpy as np
from scipy.optimize import minimize
from scipy.stats import norm


_Z_975 = float(norm.ppf(.975))
# F3-51: the expected-REML-information check may build the marginal covariance
# and its three derivative matrices densely, costing 4*n_effects**2*8 bytes.
# Up to this many effects that is at most 32 MiB and the historical dense
# computation is kept verbatim; above it the identical quantity is accumulated
# block by block so memory stays bounded by the largest study block.
_DENSE_IDENTIFIABILITY_MAX_EFFECTS = 1000
# F3-62: the stationarity gate used to be an absolute bound on the projected
# gradient. A ratio measure shifts every analysis value by a constant
# c = log(k): the model is mathematically unchanged (the outcome means absorb
# c), the REML objective changes only through its 2*n*log(scale) and log|X'X|
# terms, but the scaled sampling variance contracts by e^-2c so the reachable
# gradient grows with the data magnitude. Large, perfectly fittable RR/OR/HR
# data sets (10-300x) were therefore refused from every start. The 1e-4
# constant is kept, but measured against the objective magnitude - the same
# quantity L-BFGS-B's relative ftol stopping rule is scaled by - so the gate
# never narrows acceptance for ordinary inputs and stops punishing scale.
_GRADIENT_RTOL = 1e-4
# F3-54/F3-58: the multi-start REML fits are synchronous and previously had no
# wall-clock ceiling, so one legitimate request could occupy a shared worker
# thread for tens of minutes (measured 47.66 s and 8.62 s on 100-study inputs,
# extrapolating to 114.7 min and 25.9 min at maxiter=5000). Each objective
# evaluation is cheap and independent, so the budget is checked per evaluation
# and per start: once it expires the remaining starts are skipped, the fit that
# already met every convergence diagnostic is returned, and only a run with no
# acceptable fit reports the budget. Fittable data therefore still fits: the
# largest documented input (100 blocks x 8 outcomes, the DependentSynthesisIn
# ceiling) completes in 97.8 s on this reference machine, so 600 s leaves about
# sixfold headroom and cannot truncate a fit that would have succeeded, while
# the extrapolated pathological search is capped at 10 min instead of 114.7.
_BUDGET_SECONDS = 600.0
_CITATION_REMINDER = (
    "Report the source and assumptions for each within-study covariance matrix; "
    "cite Cheung (2015), Chapter 5, §5.3, pp. 127–133."
)


class _EvaluationBudget:
    """Wall-clock budget shared by the starts of one REML fit (F3-54 / F3-58)."""

    def __init__(self, seconds: float | None = None) -> None:
        self.seconds = _BUDGET_SECONDS if seconds is None else seconds
        self.started = time.monotonic()
        self.exhausted = False

    def expired(self) -> bool:
        if not self.exhausted and time.monotonic() - self.started >= self.seconds:
            self.exhausted = True
        return self.exhausted

    def guard(self, value):
        """Objective wrapper: stop the optimizer once the budget is spent.

        Returning +inf (as the objective already does for unidentifiable
        parameter vectors) makes L-BFGS-B terminate the current start cleanly
        instead of raising through scipy's internals.
        """
        if self.expired():
            return math.inf
        return value


def _optimizer_options() -> dict:
    """Identical L-BFGS-B options for every REML carrier, plus the budget stop."""
    return {"ftol": 1e-15, "gtol": 1e-10, "maxiter": 5000, "maxls": 100,
            "finite_diff_rel_step": 1e-5}


def _pairwise_overlap_counts(pair_counts: dict, outcome_order: list[str]) -> list[dict]:
    """Report per-pair overlapping-study counts (F3-56).

    The label pair used to be flattened into one string with "|", which is not
    injective: an outcome literally named ``"X|Y"`` collided with the pair
    ``("X", "Y")`` and one overlap count silently overwrote the other (dossier
    measured 10 pairs collapsing to 9 keys, and pair (X, Y|Z)=19 overwritten by
    (X|Y, Z)=20). Keeping the two outcome labels as separate fields makes the
    shape unique for any label content. Nothing else in the tree consumes this
    payload, so the report is free to change shape.
    """
    counts = []
    for left, right in pair_counts:
        if isinstance(left, str):
            # Callers may key by the labels themselves (legacy "|" join).
            counts.append({"outcomes": [left, right], "n_studies": pair_counts[(left, right)]})
        else:
            counts.append({"outcomes": [outcome_order[left], outcome_order[right]],
                           "n_studies": pair_counts[(left, right)]})
    return counts


def _coefficient_standard_errors(coefficient_covariance: np.ndarray) -> list[float]:
    """Square roots of a fitted coefficient-covariance diagonal (F3-50 / F3-60 family).

    Every carrier derived the reported SEs straight from
    ``math.sqrt(diag(coefficient_covariance))``. A negative diagonal - which is
    never a valid estimate, only a symptom of a broken fit - reached
    ``math.sqrt`` as a bare ``ValueError: math domain error`` with no
    indication of which check failed. The sibling kernel already refuses
    non-positive sampling variances with a named domain error; this is the same
    guard at the reporting boundary: non-finite or negative diagonals are
    rejected with a message, and only a genuine (non-negative) diagonal is
    square-rooted. Converged fits are unaffected, so no legal analysis is
    refused.
    """
    diagonal = np.diag(coefficient_covariance).astype(float, copy=False)
    if not np.isfinite(diagonal).all() or np.any(diagonal < 0.0):
        raise ValueError(
            "random-effects REML coefficient covariance diagonal must be finite and "
            "non-negative; the fit is not usable"
        )
    return [math.sqrt(float(value)) for value in diagonal]


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
    if len(outcome_order) != 2 or len(set(outcome_order)) != 2:
        # F3-53: repeated labels collapsed through the outcome->index map, so a
        # block could end up with two rows pointing at the same outcome, the
        # design column count for the other outcome was 0, and math.log(0)
        # escaped as a bare "math domain error". The two/three/four/five-plus
        # modules already require distinct labels; report it the same way.
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

    budget = _EvaluationBudget()

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
    # F3-52: build each pair in outcome_order, not in the block's row order.
    # The old code paired values[0]/values[1] positionally while centring with
    # the mean of outcome 0/1, so a block stored in the opposite order swapped
    # the pairing and distorted the rho starting value (measured: canonical
    # rho 0.9 vs mixed-order 0.3524 for the same data). Placing every residual
    # at its own outcome's slot and centring with that outcome's mean makes the
    # result independent of row order; in-order blocks are unchanged bit for bit.
    residual_centres = [float(np.mean(residuals)) if residuals.size else 0.0
                        for residuals in marginal_residuals]
    paired_residuals = []
    for values, outcomes in zip(values_by_block, outcomes_by_block):
        if len(outcomes) != 2:
            continue
        pair = [0.0, 0.0]
        for value, index in zip(values, outcomes):
            pair[index] = float(value) - residual_centres[index]
        paired_residuals.append((pair[0], pair[1]))
    paired_array = np.asarray(paired_residuals)
    if np.any(np.std(paired_array, axis=0) <= 1e-12):
        rho_initial = 0.0
    else:
        rho_initial = float(np.corrcoef(paired_array.T)[0, 1])
    if not math.isfinite(rho_initial):
        rho_initial = 0.0
    rho_initial = min(.9, max(-.9, rho_initial))
    initial = np.asarray([*initial_sds, rho_initial], dtype=float)

    def budgeted(parameters: np.ndarray) -> float:
        # F3-54/F3-58: an exhausted budget surfaces as +inf, which is the same
        # value the objective already uses for unidentifiable parameter vectors,
        # so L-BFGS-B ends the search on its ordinary "no improvement" path.
        return budget.guard(evaluate(parameters))

    try:
        optimized = minimize(
            budgeted,
            initial,
            method="L-BFGS-B",
            jac="3-point",
            bounds=((0.0, None), (0.0, None), (-1.0, 1.0)),
            options=_optimizer_options(),
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
    tau = fitted["tau"]
    if total <= _DENSE_IDENTIFIABILITY_MAX_EFFECTS:
        design = np.zeros((total, 2), dtype=float)
        marginal_covariance = np.zeros((total, total), dtype=float)
        derivative_matrices = [np.zeros((total, total), dtype=float) for _ in range(3)]
        offset = 0
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
    else:
        try:
            eigenvalues = _blockwise_information_eigenvalues(outcomes_by_block, scaled_matrices, tau)
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
    standard_errors = _coefficient_standard_errors(coefficient_covariance)
    for index, outcome in enumerate(outcome_order):
        se = standard_errors[index]
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


def _blockwise_information_eigenvalues(
    outcomes_by_block: list[list[int]],
    scaled_matrices: list[np.ndarray],
    tau: np.ndarray,
) -> np.ndarray:
    """Eigenvalues of the correlation-scaled Fischer information, block by block.

    F3-51: the historical formulation built the full marginal covariance and its
    three derivative matrices, i.e. ``4 * n_effects**2 * 8`` bytes (128 MiB at
    2000 effects, 12.8 GiB at 20000). Both are block diagonal by study, so the
    same trace is accumulated over per-study blocks of at most a handful of
    rows: memory is bounded by the largest study, and the arithmetic per block
    is the identical ``trace(P L P R)`` with the same operation order, so the
    eigenvalues (and hence the reported condition number) are unchanged.
    """
    block_terms = []
    fixed_information = np.zeros((2, 2), dtype=float)
    for outcomes, sampling_covariance in zip(outcomes_by_block, scaled_matrices):
        count = len(outcomes)
        design = np.zeros((count, 2), dtype=float)
        design[np.arange(count), outcomes] = 1.0
        marginal = sampling_covariance + tau[np.ix_(outcomes, outcomes)]
        weight = np.linalg.inv(marginal)
        weighted_design = weight @ design
        fixed_information += design.T @ weighted_design
        derivatives = []
        for derivative_index in range(3):
            derivative = np.zeros((count, count), dtype=float)
            if derivative_index < 2:
                for local_index, outcome in enumerate(outcomes):
                    if outcome == derivative_index:
                        derivative[local_index, local_index] = 1.0
            elif count == 2:
                derivative[0, 1] = derivative[1, 0] = 1.0
            derivatives.append(derivative)
        block_terms.append((weight, weighted_design, derivatives))
    fixed_covariance = np.linalg.inv(fixed_information)
    fisher = np.zeros((3, 3), dtype=float)
    for left_index in range(3):
        for right_index in range(3):
            total_trace = 0.0
            for weight, weighted_design, derivatives in block_terms:
                projection = weight - weighted_design @ fixed_covariance @ weighted_design.T
                projected_left = projection @ derivatives[left_index]
                projected_right = projection @ derivatives[right_index]
                total_trace += float(np.trace(projected_left @ projected_right))
            fisher[left_index, right_index] = 0.5 * total_trace
    fisher_scale = np.sqrt(np.diag(fisher))
    if (not np.isfinite(fisher).all() or np.any(fisher_scale <= 0)
            or not np.isfinite(fisher_scale).all()):
        raise np.linalg.LinAlgError
    return np.linalg.eigvalsh(fisher / np.outer(fisher_scale, fisher_scale))
