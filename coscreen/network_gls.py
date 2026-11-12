"""Fixed-effect network GLS for studies with supplied contrast covariances."""

from __future__ import annotations

import math
from numbers import Real
from typing import Mapping

import numpy as np
from scipy.stats import chi2, norm

RATIOS = {"RR", "OR"}
MEASURES = RATIOS | {"RD", "MD", "SMD"}

# L-24（review67）：95% CI 的 z 乘数统一为精确 norm.ppf(0.975)——此前本文件与
# network_random 硬编码 1.96，与 meta_regression_model.py 的精确值不一致（相对差
# 1.8e-5）。本模块持有唯一定义，network_random 复用同一常量。
Z_975 = float(norm.ppf(0.975))
# MM10-12 反向配对方向告警的固定前缀；network_random 以此识别并把该类告警并入
# random 结果（review67 M-09：random 路径曾用静态文案整体覆盖 warnings，丢方向告警）。
DIRECTION_WARNING_PREFIX = "Effect direction check needed:"


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise ValueError(f"{label} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _require_finite(value: object, label: str) -> None:
    if not np.all(np.isfinite(value)):
        raise ValueError(f"{label} must remain finite")


def _parse_study(study: dict, measure: str) -> tuple[str, list[tuple[str, str, float]], np.ndarray]:
    if not isinstance(study, dict):
        raise ValueError("each study must be an object")
    study_id = study.get("study_id")
    if not isinstance(study_id, str) or not study_id.strip():
        raise ValueError("study_id must be a non-empty string")
    expected_scale = "log" if measure in RATIOS else "natural"
    if study.get("covariance_scale") != expected_scale:
        raise ValueError(f"{measure} covariance_scale must be exactly {expected_scale!r}")

    raw_contrasts = study.get("contrasts")
    if not isinstance(raw_contrasts, list) or not raw_contrasts:
        raise ValueError("contrasts must be a non-empty list")
    contrasts = []
    treatments = set()
    for index, item in enumerate(raw_contrasts):
        if not isinstance(item, Mapping):
            raise ValueError("each contrast must be an object")
        treatment, comparator = item.get("treatment"), item.get("comparator")
        if not isinstance(treatment, str) or not treatment.strip() or \
           not isinstance(comparator, str) or not comparator.strip():
            raise ValueError("contrast treatments must be non-empty strings")
        treatment, comparator = treatment.strip(), comparator.strip()
        if treatment == comparator:
            raise ValueError("a contrast must compare different treatments")
        estimate = _finite_number(item.get("estimate"), f"contrast {index} estimate")
        if measure in RATIOS and estimate <= 0:
            raise ValueError("ratio estimates must be positive")
        contrasts.append((treatment, comparator, estimate))
        treatments.update((treatment, comparator))

    m = len(contrasts)
    if m > len(treatments) - 1:
        raise ValueError("a study may have at most treatments minus one independent contrasts")
    local_nodes = sorted(treatments)
    local_columns = local_nodes[1:]
    local_design = np.asarray(
        [[int(a == node) - int(b == node) for node in local_columns]
         for a, b, _ in contrasts], dtype=float)
    if np.linalg.matrix_rank(local_design) != m:
        raise ValueError("contrasts must be linearly independent within each study")

    raw_covariance = study.get("covariance")
    if not isinstance(raw_covariance, list) or len(raw_covariance) != m or \
       any(not isinstance(row, list) or len(row) != m for row in raw_covariance):
        raise ValueError("covariance must be a square matrix matching the contrast order")
    covariance = np.asarray(
        [[_finite_number(value, "covariance value") for value in row]
         for row in raw_covariance], dtype=float)
    if not np.allclose(covariance, covariance.T, rtol=1e-10, atol=1e-12):
        raise ValueError("covariance must be symmetric positive definite")
    covariance = covariance / 2 + covariance.T / 2
    _require_finite(covariance, "covariance")
    try:
        factor = np.linalg.cholesky(covariance)
    except (FloatingPointError, np.linalg.LinAlgError) as exc:
        raise ValueError("covariance must be symmetric positive definite") from exc
    _require_finite(factor, "covariance factor")
    return study_id, contrasts, covariance


def validate_study(study: dict, measure: str) -> None:
    """Validate one study and its analysis-scale contrast covariance."""
    if not isinstance(measure, str) or measure not in MEASURES:
        raise ValueError("network synthesis supports RR, OR, RD, MD or SMD")
    _parse_study(study, measure)


def synthesize_network(studies: list[dict], measure: str, reference: str) -> dict:
    """Fit a fixed-effect network model using study-level GLS covariance blocks.

    Ratio estimates are supplied on their natural positive scale and returned on
    that scale; their standard errors remain on the log scale. Covariances for
    ratios must already be on the log scale.
    """
    if not isinstance(measure, str) or measure not in MEASURES:
        raise ValueError("network synthesis supports RR, OR, RD, MD or SMD")
    if not isinstance(studies, list) or not studies:
        raise ValueError("studies must be a non-empty list")
    if not isinstance(reference, str) or not reference.strip():
        raise ValueError("reference must be a non-empty treatment name")
    reference = reference.strip()

    parsed = [_parse_study(study, measure) for study in studies]
    study_ids = [item[0] for item in parsed]
    if len(study_ids) != len(set(study_ids)):
        raise ValueError("study_id values must be unique")
    # MM10-12: unordered treatment pairs seen in both orientations across
    # studies (within one study the linear-independence rank check already
    # rejects them); collected for the effect-direction warning below.
    pair_orientations: dict[frozenset, tuple[str, str]] = {}
    reversed_pairs: dict[frozenset, tuple[str, str]] = {}
    for _, contrasts, _ in parsed:
        for a, b, _ in contrasts:
            pair = frozenset((a, b))
            if pair in pair_orientations and pair_orientations[pair] != (a, b):
                reversed_pairs.setdefault(pair, (a, b))
            pair_orientations.setdefault(pair, (a, b))
    nodes = sorted({node for _, contrasts, _ in parsed
                    for a, b, _ in contrasts for node in (a, b)})
    if len(nodes) < 3 or reference not in nodes:
        raise ValueError("network needs at least three treatments and a present reference")

    adjacency = {node: set() for node in nodes}
    for _, contrasts, _ in parsed:
        for a, b, _ in contrasts:
            adjacency[a].add(b)
            adjacency[b].add(a)
    reached, frontier = set(), [reference]
    while frontier:
        node = frontier.pop()
        if node not in reached:
            reached.add(node)
            frontier.extend(adjacency[node] - reached)
    if reached != set(nodes):
        raise ValueError("treatment network is disconnected")

    columns = [node for node in nodes if node != reference]
    p = len(columns)
    information = np.zeros((p, p), dtype=float)
    score = np.zeros(p, dtype=float)
    blocks = []
    for _, contrasts, covariance in parsed:
        design = np.asarray(
            [[int(a == node) - int(b == node) for node in columns]
             for a, b, _ in contrasts], dtype=float)
        values = np.asarray([math.log(value) if measure in RATIOS else value
                             for _, _, value in contrasts], dtype=float)
        try:
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                weighted_design = np.linalg.solve(covariance, design)
                weighted_values = np.linalg.solve(covariance, values)
        except np.linalg.LinAlgError as exc:
            raise ValueError("study covariance could not be solved numerically") from exc
        except FloatingPointError as exc:
            raise ValueError("study covariance solve overflowed") from exc
        _require_finite(weighted_design, "weighted design")
        _require_finite(weighted_values, "weighted estimates")
        try:
            with np.errstate(over="raise", invalid="raise"):
                information += design.T @ weighted_design
                score += design.T @ weighted_values
        except FloatingPointError as exc:
            raise ValueError("network information overflowed") from exc
        _require_finite(information, "network information")
        _require_finite(score, "network score")
        blocks.append((design, values, covariance))

    # review67 M-10: information 矩阵此前只靠 solve 的 LinAlgError 拒绝奇异，无
    # 条数/秩护栏——方差跨度 ≥~16 个数量级时 information 在 float64 下数值秩亏
    # （实测 cond=inf、det=7.6e+272、slogdet 符号仍为正）而 solve 照常返回，静默
    # 产出错误 SE 与方向反转的点估计（探针 probe_stats_net6：SE 错 125 个数量级）。
    # 与 network_random 的同阈值护栏对齐：条件数超 1e12 即结构化拒绝。
    condition = float(np.linalg.cond(information))
    if not math.isfinite(condition) or condition > 1e12:
        raise ValueError("network information matrix is numerically ill-conditioned")
    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            parameter_covariance = np.linalg.solve(information, np.eye(p))
            effects = np.linalg.solve(information, score)
    except np.linalg.LinAlgError as exc:
        raise ValueError("network is not numerically estimable") from exc
    except FloatingPointError as exc:
        raise ValueError("network solution overflowed") from exc
    _require_finite(parameter_covariance, "network covariance")
    _require_finite(effects, "network effects")

    residual_q = 0.0
    n_contrasts = 0
    for design, values, covariance in blocks:
        try:
            with np.errstate(over="raise", invalid="raise"):
                residual = values - design @ effects
        except FloatingPointError as exc:
            raise ValueError("network residual overflowed") from exc
        _require_finite(residual, "network residual")
        try:
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                q_block = float(residual @ np.linalg.solve(covariance, residual))
        except np.linalg.LinAlgError as exc:
            raise ValueError("study covariance could not be solved numerically") from exc
        except FloatingPointError as exc:
            raise ValueError("residual Q overflowed") from exc
        _require_finite(q_block, "residual Q")
        with np.errstate(over="raise", invalid="raise"):
            residual_q += q_block
        _require_finite(residual_q, "residual Q")
        n_contrasts += len(values)
    residual_q = max(0.0, residual_q)
    residual_df = n_contrasts - p
    estimates = []
    for i, treatment in enumerate(nodes):
        for comparator in nodes[i + 1:]:
            contrast = np.asarray([int(treatment == node) - int(comparator == node)
                                   for node in columns], dtype=float)
            try:
                with np.errstate(over="raise", invalid="raise"):
                    effect = float(contrast @ effects)
                    variance = float(contrast @ parameter_covariance @ contrast)
            except FloatingPointError as exc:
                raise ValueError("pairwise estimate or variance overflowed") from exc
            _require_finite((effect, variance), "pairwise estimate and variance")
            if variance <= 0:
                raise ValueError("pairwise standard error must be positive and finite")
            se = math.sqrt(variance)
            if not math.isfinite(se) or se <= 0:
                raise ValueError("pairwise standard error must be positive and finite")
            # L-24（review67）：z 乘数用精确 norm.ppf(0.975)，不再硬编码 1.96。
            low, high = effect - Z_975 * se, effect + Z_975 * se
            _require_finite((low, high), "pairwise confidence interval")
            try:
                if measure in RATIOS:
                    estimate, ci_low, ci_high = math.exp(effect), math.exp(low), math.exp(high)
                else:
                    estimate, ci_low, ci_high = effect, low, high
            except OverflowError as exc:
                raise ValueError("network result overflows on the ratio scale") from exc
            _require_finite((estimate, ci_low, ci_high), "pairwise result")
            if measure in RATIOS and min(estimate, ci_low, ci_high) <= 0:
                raise ValueError("ratio estimates and intervals must remain positive and finite")
            estimates.append({"treatment": treatment, "comparator": comparator,
                              "estimate": estimate, "se": se,
                              "se_scale": "log" if measure in RATIOS else "natural",
                              "ci_low": ci_low, "ci_high": ci_high})

    residual_p = float(chi2.sf(residual_q, residual_df)) if residual_df > 0 else None
    if residual_p is not None:
        _require_finite(residual_p, "residual p-value")
    warnings = [
        "Requires transitivity and comparable effect modifiers; this model does not assess either assumption.",
        "Residual Q is total fixed-effect lack of fit, combining within-design heterogeneity and possible inconsistency; it is not a formal inconsistency test.",
        "Study blocks are assumed independent, and each supplied covariance matrix is used on its declared analysis scale.",
    ]
    # MM10-12: same reversed-orientation flag as coscreen.network_analysis. A
    # reversed contrast with a negated estimate is the same contrast (legal
    # input, pooled correctly); same-signed estimates from both orientations
    # average toward a meaningless midpoint, so the pair is named here instead
    # of being rejected.
    for pair, orientation in reversed_pairs.items():
        a, b = orientation
        warnings.append(
            f"{DIRECTION_WARNING_PREFIX} the unordered treatment pair "
            f"'{a} vs {b}' appears with both orientations; same-signed estimates from "
            "the two rows are averaged as opposite design rows, which is only "
            "meaningful if the reversed entry carries the negated estimate.")
    return {
        "method": "fixed-effect contrast-based network generalized least squares",
        "measure": measure,
        "reference": reference,
        "treatments": nodes,
        "n_studies": len(parsed),
        "n_contrasts": n_contrasts,
        "estimates": estimates,
        "residual_q": residual_q,
        "residual_df": residual_df,
        "residual_p": residual_p,
        "warnings": warnings,
    }
