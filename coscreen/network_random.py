"""Common-heterogeneity random-effects network meta-analysis by profile REML."""

from __future__ import annotations

import math

import numpy as np
from scipy.optimize import minimize_scalar

from coscreen.network_gls import RATIOS, synthesize_network


_Z_975 = 1.96


def synthesize_network_random(studies: list[dict], measure: str, reference: str) -> dict:
    """Fit a consistency NMA with one common contrast-level heterogeneity tau².

    The fixed network function validates the public study/covariance contract.
    Random effects are independent arm-level deviations with variance tau²/2,
    which induces covariance tau² * 0.5 * A_i A_i' among a study's contrasts.
    """
    fixed_result = synthesize_network(studies, measure, reference)
    reference = fixed_result["reference"]
    nodes = fixed_result["treatments"]
    columns = [node for node in nodes if node != reference]
    p = len(columns)
    scale_name = "log" if measure in RATIOS else "natural"

    values_by_block = []
    designs_by_block = []
    sampling_by_block = []
    heterogeneity_by_block = []
    values_for_scale = []
    standard_errors_for_scale = []
    for study in studies:
        # 与 network_gls._parse_study 一致地 strip 治疗名：列名来自已 strip 的
        # 解析结果，未 strip 的原始行会与每个节点比较失败，被错误编码成全零行。
        contrasts = [
            {**row, "treatment": str(row["treatment"]).strip(),
             "comparator": str(row["comparator"]).strip()}
            for row in study["contrasts"]
        ]
        design = np.asarray([
            [int(row["treatment"] == node) - int(row["comparator"] == node)
             for node in columns]
            for row in contrasts
        ], dtype=float)
        values = np.asarray([
            math.log(float(row["estimate"])) if measure in RATIOS
            else float(row["estimate"])
            for row in contrasts
        ], dtype=float)
        arms = sorted({arm for row in contrasts
                       for arm in (row["treatment"], row["comparator"])})
        incidence = np.asarray([
            [int(row["treatment"] == arm) - int(row["comparator"] == arm)
             for arm in arms]
            for row in contrasts
        ], dtype=float)
        covariance = np.asarray(study["covariance"], dtype=float)
        condition = float(np.linalg.cond(covariance))
        if not math.isfinite(condition) or condition > 1e12:
            raise ValueError(f"study {study['study_id']} covariance is numerically ill-conditioned")
        heterogeneity = .5 * incidence @ incidence.T
        values_by_block.append(values)
        designs_by_block.append(design)
        sampling_by_block.append(covariance)
        heterogeneity_by_block.append(heterogeneity)
        values_for_scale.extend(np.abs(values))
        standard_errors_for_scale.extend(np.sqrt(np.diag(covariance)))

    n_contrasts = sum(len(values) for values in values_by_block)
    residual_df = n_contrasts - p
    if residual_df <= 0:
        raise ValueError("random-effects network REML requires positive residual degrees of freedom")

    design = np.vstack(designs_by_block)
    if np.linalg.matrix_rank(design) != p:
        raise ValueError("random-effects network treatment design is not identifiable")
    scale = max(
        max((float(value) for value in values_for_scale), default=0.0),
        max((float(value) for value in standard_errors_for_scale), default=0.0),
        math.sqrt(np.finfo(float).tiny),
    )
    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            values_by_block = [values / scale for values in values_by_block]
            sampling_by_block = [covariance / scale / scale for covariance in sampling_by_block]
        if (any(not np.isfinite(values).all() for values in values_by_block)
                or any(not np.isfinite(covariance).all() for covariance in sampling_by_block)):
            raise FloatingPointError
    except (FloatingPointError, OverflowError):
        raise ValueError("network REML inputs are outside the finite numerical range") from None

    design_information = design.T @ design
    sign, logdet_design = np.linalg.slogdet(design_information)
    if sign <= 0 or not math.isfinite(float(logdet_design)):
        raise ValueError("random-effects network treatment design is not identifiable")

    def evaluate(tau2: float, details: bool = False):
        if not math.isfinite(float(tau2)) or tau2 < 0:
            return (math.inf, None) if details else math.inf
        information = np.zeros((p, p), dtype=float)
        score = np.zeros(p, dtype=float)
        logdet_marginal = 0.0
        ywy = 0.0
        marginal_blocks = []
        weighted_designs = []
        weighted_values_by_block = []
        try:
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                for values, study_design, sampling, heterogeneity in zip(
                    values_by_block, designs_by_block, sampling_by_block, heterogeneity_by_block
                ):
                    marginal = sampling + tau2 * heterogeneity
                    sign, logdet = np.linalg.slogdet(marginal)
                    if sign <= 0 or not math.isfinite(float(logdet)):
                        return (math.inf, None) if details else math.inf
                    weighted_design = np.linalg.solve(marginal, study_design)
                    weighted_values = np.linalg.solve(marginal, values)
                    information += study_design.T @ weighted_design
                    score += study_design.T @ weighted_values
                    logdet_marginal += float(logdet)
                    ywy += float(values @ weighted_values)
                    if details:
                        marginal_blocks.append(marginal)
                        weighted_designs.append(weighted_design)
                        weighted_values_by_block.append(weighted_values)
                sign, logdet_information = np.linalg.slogdet(information)
                if sign <= 0 or not math.isfinite(float(logdet_information)):
                    return (math.inf, None) if details else math.inf
                coefficient_covariance = np.linalg.solve(information, np.eye(p))
                coefficients = coefficient_covariance @ score
                residual_q = max(0.0, ywy - float(score @ coefficients))
                objective = .5 * (
                    residual_df * math.log(2 * math.pi) + logdet_marginal
                    + float(logdet_information) - float(logdet_design) + residual_q
                )
                profile_gradient = None
                if details:
                    trace_weight_covariance = 0.0
                    coefficient_adjustment = 0.0
                    projected_quadratic = 0.0
                    for (study_design, heterogeneity, marginal, weighted_design,
                         weighted_values) in zip(
                            designs_by_block, heterogeneity_by_block, marginal_blocks,
                            weighted_designs, weighted_values_by_block,
                        ):
                        trace_weight_covariance += float(np.trace(
                            np.linalg.solve(marginal, heterogeneity)
                        ))
                        coefficient_adjustment += float(np.trace(
                            coefficient_covariance @ weighted_design.T
                            @ heterogeneity @ weighted_design
                        ))
                        projected_values = weighted_values - weighted_design @ coefficients
                        projected_quadratic += float(
                            projected_values @ heterogeneity @ projected_values
                        )
                    profile_gradient = .5 * (
                        trace_weight_covariance - coefficient_adjustment - projected_quadratic
                    )
        except (FloatingPointError, OverflowError, np.linalg.LinAlgError):
            return (math.inf, None) if details else math.inf
        if not math.isfinite(objective):
            return (math.inf, None) if details else math.inf
        fitted = {
            "coefficients": coefficients,
            "coefficient_covariance": coefficient_covariance,
            "residual_q": residual_q,
            "information": information,
            "marginal_blocks": marginal_blocks,
            "profile_gradient": profile_gradient,
        }
        return (objective, fitted) if details else objective

    objective_zero = evaluate(0.0)
    if not math.isfinite(objective_zero):
        raise ValueError("random-effects network REML could not evaluate the zero-heterogeneity boundary")
    upper = 1.0
    try:
        for _ in range(32):
            objective_upper = evaluate(upper)
            objective_half = evaluate(upper / 2)
            if (math.isfinite(objective_upper) and math.isfinite(objective_half)
                    and objective_upper > min(objective_zero, objective_half)):
                break
            if not math.isfinite(objective_upper) and math.isfinite(objective_half):
                objective_quarter = evaluate(upper / 4)
                if (math.isfinite(objective_quarter)
                        and objective_half > min(objective_zero, objective_quarter)):
                    upper /= 2
                    break
                raise ValueError(
                    "random-effects network REML could not bracket a finite profile likelihood"
                )
            if not math.isfinite(objective_half):
                raise ValueError(
                    "random-effects network REML could not bracket a finite profile likelihood"
                )
            upper *= 4
        else:
            raise ValueError("random-effects network REML could not bracket the profile likelihood")
        optimized = minimize_scalar(
            evaluate,
            method="bounded",
            bounds=(0.0, upper),
            options={"xatol": 1e-12, "maxiter": 1000},
        )
    except (FloatingPointError, OverflowError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("random-effects network REML"):
            raise
        raise ValueError(f"random-effects network REML optimization failed: {exc}") from None
    if not optimized.success or not math.isfinite(float(optimized.fun)):
        raise ValueError(f"random-effects network REML did not converge: {optimized.message}")

    tau2_scaled = float(optimized.x)
    objective = float(optimized.fun)
    boundary_tolerance = 1e-10 * max(1.0, abs(objective_zero))
    if objective_zero <= objective + boundary_tolerance:
        tau2_scaled = 0.0
        objective = float(objective_zero)
    objective, fitted = evaluate(tau2_scaled, details=True)
    if fitted is None:
        raise ValueError("random-effects network REML did not produce an identifiable fit")

    condition = float(np.linalg.cond(fitted["information"]))
    if not math.isfinite(condition) or condition > 1e12:
        raise ValueError("random-effects network treatment design is numerically ill-conditioned")
    expected_information = _expected_tau_information(
        designs_by_block,
        heterogeneity_by_block,
        fitted["marginal_blocks"],
        fitted["information"],
    )
    if not math.isfinite(expected_information) or expected_information <= 1e-12:
        raise ValueError("between-study variance is not identifiable from the supplied network")

    boundary = tau2_scaled == 0.0
    profile_gradient = float(fitted["profile_gradient"])
    gradient_norm = _projected_gradient_norm(profile_gradient, boundary)
    if not math.isfinite(gradient_norm) or gradient_norm > 1e-4:
        raise ValueError(f"random-effects network REML did not converge: profile gradient {gradient_norm:.3g}")

    tau2 = tau2_scaled * scale * scale
    coefficients = fitted["coefficients"] * scale
    coefficient_covariance = fitted["coefficient_covariance"] * scale * scale
    if (not math.isfinite(tau2) or not np.isfinite(coefficients).all()
            or not np.isfinite(coefficient_covariance).all()):
        raise ValueError("random-effects network estimates are outside the finite numerical range")

    estimates = []
    for treatment_index, treatment in enumerate(nodes):
        for comparator in nodes[treatment_index + 1:]:
            contrast = np.asarray([
                int(treatment == node) - int(comparator == node) for node in columns
            ], dtype=float)
            effect = float(contrast @ coefficients)
            variance = float(contrast @ coefficient_covariance @ contrast)
            if not math.isfinite(effect) or not math.isfinite(variance) or variance <= 0:
                raise ValueError("pairwise estimate or standard error is outside the finite numerical range")
            se = math.sqrt(variance)
            low, high = effect - _Z_975 * se, effect + _Z_975 * se
            if measure in RATIOS:
                try:
                    estimate, ci_low, ci_high = map(math.exp, (effect, low, high))
                except OverflowError:
                    raise ValueError("network result overflows on the ratio scale") from None
                if any(not math.isfinite(value) or value <= 0
                       for value in (estimate, ci_low, ci_high)):
                    raise ValueError("network ratio estimate or confidence limits are not finite and positive")
            else:
                estimate, ci_low, ci_high = effect, low, high
            estimates.append({
                "treatment": treatment,
                "comparator": comparator,
                "estimate": float(estimate),
                "se": se,
                "se_scale": scale_name,
                "ci_low": float(ci_low),
                "ci_high": float(ci_high),
            })

    residual_q = fitted["residual_q"]
    warnings = [
        "Requires transitivity and comparable effect modifiers; this model does not assess either assumption.",
        "Residual Q is a random-effects lack-of-fit diagnostic, not a formal inconsistency test; no chi-square p-value is reported.",
        "Uses independent arm-level study deviations with one common contrast-level tau-squared; treatment-specific heterogeneity is not modeled.",
        "Within-study covariance matrices must be provided on the declared analysis scale and are treated as known.",
        "Normal Wald intervals condition on the fitted heterogeneity variance and do not include its estimation uncertainty.",
    ]
    result = {
        **fixed_result,
        "model": "random_reml_common_tau",
        "method": "random-effects contrast-based network generalized least squares (profile REML)",
        "estimates": estimates,
        "random_effects": {
            "structure": "independent_arm_deviations",
            "tau2": tau2,
            "scale": scale_name,
            "covariance_rule": "C_i = 0.5 * A_i @ A_i.T",
        },
        "treatment_effect_covariance": {
            "treatments": columns,
            "scale": scale_name,
            "matrix": coefficient_covariance.tolist(),
        },
        "fit": {
            "method": "REML",
            "converged": True,
            "optimizer": "bounded_scalar_profile",
            "iterations": int(optimized.nfev),
            "reml_log_likelihood": float(-objective - residual_df * math.log(scale)),
            "tau2": tau2,
            "boundary": boundary,
            "gradient_norm": gradient_norm,
            "treatment_information_condition": condition,
            "expected_tau_information": expected_information,
        },
        "residual_q": residual_q,
        "residual_df": residual_df,
        "residual_p": None,
        "residual_q_scope": "random_effects_residual_diagnostic",
        "warnings": warnings,
    }
    return result


def _expected_tau_information(
    designs: list[np.ndarray],
    heterogeneity: list[np.ndarray],
    marginal: list[np.ndarray],
    information: np.ndarray,
) -> float:
    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            fixed_covariance = np.linalg.solve(information, np.eye(information.shape[0]))
            covariance_score = np.zeros_like(information)
            covariance_cross = np.zeros_like(information)
            covariance_trace = 0.0
            for study_design, ci, mi in zip(designs, heterogeneity, marginal):
                weight = np.linalg.solve(mi, np.eye(len(mi)))
                weighted_design = weight @ study_design
                covariance_trace += float(np.trace(weight @ ci @ weight @ ci))
                block_score = weighted_design.T @ ci @ weighted_design
                covariance_score += block_score
                covariance_cross += weighted_design.T @ ci @ weight @ ci @ weighted_design
            adjustment = float(np.trace(fixed_covariance @ covariance_cross))
            projected_adjustment = float(np.trace(
                fixed_covariance @ covariance_score @ fixed_covariance @ covariance_score
            ))
            value = .5 * (covariance_trace - 2 * adjustment + projected_adjustment)
    except (FloatingPointError, OverflowError, np.linalg.LinAlgError):
        raise ValueError("between-study variance is not identifiable from the supplied network") from None
    return value


def _projected_gradient_norm(gradient: float, boundary: bool) -> float:
    if boundary:
        return max(0.0, -gradient)
    return abs(gradient)
