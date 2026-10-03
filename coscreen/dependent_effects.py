"""ID-aligned working covariance and cluster-robust GLS inference.

This is an independent kernel for multiple dependent effects. It fits GLS with
the supplied total working covariance; it does not estimate random-effect
variance components. Upstream model code may pass its fitted marginal
covariance to :func:`fit_dependent_gls` for robust inference.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from numbers import Real

import numpy as np
from scipy.stats import f as f_distribution
from scipy.stats import t as student_t

_MAX_EFFECTS = 500
_RANK_TOL = 1e-12
#: HTZ 检验物化 q² 个 n×n 矩阵（内存 O(q²n²)；q=100/n=100 时约 865MB），
#: 超过该约束数的联合检验提前拒绝而不是 OOM。
_HTZ_MAX_CONSTRAINTS = 20


class KernelError(ValueError):
    """A validation error with a stable machine-readable code."""

    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def _finite(value: object, label: str) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise KernelError("invalid_number", f"{label} must be a finite number", field=label)
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError):
        raise KernelError("invalid_number", f"{label} must be a finite number", field=label) from None
    if not math.isfinite(result):
        raise KernelError("invalid_number", f"{label} must be a finite number", field=label)
    return result


def _ids(values: Sequence[object], label: str, *, nonempty: bool = True) -> list[str]:
    if not isinstance(values, (list, tuple, np.ndarray)):
        raise KernelError("invalid_ids", f"{label} must be a sequence", field=label)
    result = []
    for index, value in enumerate(values):
        if not isinstance(value, str) or (nonempty and not value.strip()):
            raise KernelError("invalid_ids", f"{label}[{index}] must be a non-empty string", field=label)
        result.append(value.strip())
    if len(result) != len(set(result)) and label in {
        "effect_ids", "covariance_effect_ids", "additive_covariance_effect_ids",
        "design_names",
    }:
        raise KernelError("duplicate_id", f"{label} must be unique", field=label)
    return result


def _square_matrix(value: object, label: str) -> np.ndarray:
    try:
        matrix = np.asarray(value, dtype=float)
    except (TypeError, ValueError, OverflowError):
        raise KernelError("invalid_covariance", f"{label} must be a finite square matrix") from None
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1] or not np.isfinite(matrix).all():
        raise KernelError("invalid_covariance", f"{label} must be a finite square matrix")
    return matrix


def _check_symmetric(matrix: np.ndarray, label: str) -> np.ndarray:
    if matrix.size == 0:
        raise KernelError("invalid_covariance", f"{label} cannot be empty")
    magnitude = max(float(np.max(np.abs(matrix))), np.finfo(float).tiny)
    asymmetry = float(np.max(np.abs(matrix - matrix.T)))
    if asymmetry > magnitude * 1e-10:
        raise KernelError("covariance_not_symmetric", f"{label} must be symmetric",
                          max_asymmetry=asymmetry)
    return (matrix + matrix.T) / 2


def transform_effect_directions(
    effect_ids: Sequence[str],
    estimates: Sequence[Real],
    effect_directions: Sequence[str],
    covariance: object,
    *,
    covariance_effect_ids: Sequence[str] | None = None,
) -> dict:
    """Recode signed effects to first-vs-second and apply ``D V D``.

    The input estimate order defines ``effect_ids``. A covariance may carry a
    different ID order; it is aligned before applying the diagonal sign matrix.
    Inputs are copied, so callers can retain the reported values and V.
    """
    ids = _ids(effect_ids, "effect_ids")
    if not ids:
        raise KernelError("invalid_ids", "effect_ids cannot be empty", field="effect_ids")
    try:
        estimate_values = list(estimates)
    except TypeError:
        raise KernelError("invalid_estimates", "estimates must contain one finite number per effect ID") from None
    if len(estimate_values) != len(ids):
        raise KernelError("invalid_estimates", "estimates must contain one finite number per effect ID")
    source_estimates = np.asarray([
        _finite(value, f"estimate for effect {effect_id}")
        for effect_id, value in zip(ids, estimate_values)
    ], dtype=float)
    try:
        directions = list(effect_directions)
    except TypeError:
        raise KernelError("input_length_mismatch", "effect directions must match effect IDs") from None
    if len(directions) != len(ids):
        raise KernelError("input_length_mismatch", "effect directions must match effect IDs")
    if any(not isinstance(direction, str) or direction not in {"first_vs_second", "second_vs_first"}
           for direction in directions):
        raise KernelError("invalid_effect_direction", "each effect direction must be first_vs_second or second_vs_first")

    source_ids = _ids(ids if covariance_effect_ids is None else covariance_effect_ids,
                      "covariance_effect_ids")
    source = _square_matrix(covariance, "covariance")
    if source.shape != (len(source_ids), len(source_ids)):
        raise KernelError("covariance_dimension_mismatch", "covariance dimensions must match covariance_effect_ids",
                          n_ids=len(source_ids), shape=list(source.shape))
    source = _check_symmetric(source, "covariance")
    source_index = {effect_id: index for index, effect_id in enumerate(source_ids)}
    missing = [effect_id for effect_id in ids if effect_id not in source_index]
    if missing:
        raise KernelError("covariance_effect_missing", "effects are missing from covariance_effect_ids",
                          effect_ids=missing)
    positions = [source_index[effect_id] for effect_id in ids]
    aligned = source[np.ix_(positions, positions)]
    signs = np.asarray([1.0 if direction == "first_vs_second" else -1.0
                        for direction in directions])
    transformed = signs[:, None] * aligned * signs[None, :]
    return {
        "effect_ids": ids,
        "target_effect_direction": "first_vs_second",
        "direction_signs": signs.tolist(),
        "estimates": (signs * source_estimates).tolist(),
        "covariance": transformed.tolist(),
    }


def _check_positive_definite(matrix: np.ndarray, label: str) -> None:
    try:
        eigenvalues = np.linalg.eigvalsh(matrix)
    except np.linalg.LinAlgError:
        raise KernelError("covariance_eigendecomposition_failed", f"{label} eigendecomposition failed") from None
    minimum = float(eigenvalues[0])
    if minimum <= 0:
        raise KernelError("covariance_not_positive_definite", f"{label} must be positive definite",
                          minimum_eigenvalue=minimum)
    try:
        np.linalg.cholesky(matrix)
    except np.linalg.LinAlgError:
        raise KernelError("covariance_not_positive_definite", f"{label} must be positive definite",
                          minimum_eigenvalue=minimum) from None
    # ponytail: condition ceiling 1e12; higher precision if near-singular V is legitimate.
    condition = float(np.linalg.cond(matrix))
    if not math.isfinite(condition) or condition > 1e12:
        raise KernelError("covariance_ill_conditioned", f"{label} is numerically ill-conditioned",
                          condition_number=condition)


def build_working_covariance(
    effect_ids: Sequence[str],
    variances: Sequence[Real],
    cluster_ids: Sequence[str],
    *,
    kind: str = "independent",
    rho: Real | None = None,
    matrix: object | None = None,
    covariance_effect_ids: Sequence[str] | None = None,
) -> dict:
    """Build a finite, positive-definite V aligned to the requested effect IDs.

    ``kind`` is ``independent``, ``block_correlation``, or ``user``. A supplied
    matrix carries its own ID order and may be reordered or subset by IDs. The
    result contains the exact selected order and matrix for an analysis
    snapshot. Equicorrelation is a user-selected working assumption.
    """
    selected_ids = _ids(effect_ids, "effect_ids")
    clusters = _ids(cluster_ids, "cluster_ids")
    if not selected_ids or len(selected_ids) > _MAX_EFFECTS:
        raise KernelError("unsupported_effect_count", f"between 1 and {_MAX_EFFECTS} effects are supported",
                          n_effects=len(selected_ids))
    if len(clusters) != len(selected_ids) or len(variances) != len(selected_ids):
        raise KernelError("input_length_mismatch", "effect IDs, variances, and cluster IDs must have equal length")
    clean_variances = np.asarray([
        _finite(value, f"variance for effect {effect_id}")
        for effect_id, value in zip(selected_ids, variances)
    ], dtype=float)
    if np.any(clean_variances <= 0):
        raise KernelError("nonpositive_variance", "each sampling variance must be positive")

    kind = kind.lower() if isinstance(kind, str) else ""
    if kind == "independent":
        covariance = np.diag(clean_variances)
        source_ids = selected_ids
        if matrix is not None or covariance_effect_ids is not None or rho is not None:
            raise KernelError("unused_covariance_option", "independent covariance does not accept matrix or rho")
    elif kind == "block_correlation":
        if matrix is not None or covariance_effect_ids is not None:
            raise KernelError("unused_covariance_option", "block correlation does not accept a user matrix")
        if rho is None:
            raise KernelError("missing_rho", "block correlation requires an explicit rho")
        rho_value = _finite(rho, "rho")
        counts = Counter(clusters)
        lower = max((-1.0 / (count - 1) for count in counts.values() if count > 1), default=-1.0)
        if not lower < rho_value < 1.0:
            raise KernelError("rho_out_of_range", "rho is outside the positive-definite block-correlation range",
                              rho=rho_value, lower_exclusive=lower, upper_exclusive=1.0)
        standard_errors = np.sqrt(clean_variances)
        covariance = np.diag(clean_variances)
        source_ids = selected_ids
        for left in range(len(selected_ids)):
            for right in range(left):
                if clusters[left] == clusters[right]:
                    covariance[left, right] = covariance[right, left] = (
                        rho_value * standard_errors[left] * standard_errors[right]
                    )
    elif kind == "user":
        if rho is not None:
            raise KernelError("unused_covariance_option", "user covariance does not accept rho")
        if matrix is None or covariance_effect_ids is None:
            raise KernelError("missing_covariance_ids", "user covariance requires matrix and covariance_effect_ids")
        source_ids = _ids(covariance_effect_ids, "covariance_effect_ids")
        source = _square_matrix(matrix, "user covariance")
        if source.shape != (len(source_ids), len(source_ids)):
            raise KernelError("covariance_dimension_mismatch", "user covariance dimensions must match covariance_effect_ids",
                              n_ids=len(source_ids), shape=list(source.shape))
        source = _check_symmetric(source, "user covariance")
        source_index = {effect_id: index for index, effect_id in enumerate(source_ids)}
        missing = [effect_id for effect_id in selected_ids if effect_id not in source_index]
        if missing:
            raise KernelError("covariance_effect_missing", "selected effects are missing from covariance_effect_ids",
                              effect_ids=missing)
        selected = [source_index[effect_id] for effect_id in selected_ids]
        covariance = source[np.ix_(selected, selected)]
    else:
        raise KernelError("unsupported_covariance_kind", "kind must be independent, block_correlation, or user",
                          kind=kind)

    covariance = _check_symmetric(covariance, "working covariance")
    diagonal_tolerance = max(float(np.max(clean_variances)) * 1e-12, np.finfo(float).tiny)
    if not np.allclose(np.diag(covariance), clean_variances, rtol=1e-7, atol=diagonal_tolerance):
        raise KernelError("covariance_diagonal_mismatch", "working covariance diagonal must match sampling variances")
    cluster_array = np.asarray(clusters, dtype=object)
    cross_cluster = cluster_array[:, None] != cluster_array[None, :]
    if np.any(np.abs(covariance[cross_cluster]) > max(float(np.max(np.abs(covariance))) * 1e-12, 1e-15)):
        raise KernelError("cross_cluster_covariance", "working covariance must be block diagonal by independent cluster")
    _check_positive_definite(covariance, "working covariance")
    return {
        "kind": kind,
        "effect_ids": selected_ids,
        "source_covariance_effect_ids": source_ids,
        "cluster_ids": clusters,
        "rho": float(rho) if kind == "block_correlation" else None,
        "matrix": covariance.tolist(),
    }


def _unavailable(code: str, message: str, **details: object) -> dict:
    return {"status": "unavailable", "reason_code": code, "reason": message,
            "details": details, "covariance": None, "coefficient_tests": [], "joint_test": None}


def _symmetric_inverse_sqrt(matrix: np.ndarray, label: str) -> np.ndarray:
    values, vectors = np.linalg.eigh((matrix + matrix.T) / 2)
    threshold = _RANK_TOL * max(np.finfo(float).tiny, float(np.max(np.abs(values))))
    if float(values[0]) <= threshold:
        raise KernelError("singular_robust_covariance", f"{label} is singular or numerically rank deficient",
                          minimum_eigenvalue=float(values[0]), tolerance=threshold)
    return (vectors * (1.0 / np.sqrt(values))) @ vectors.T


def _validate_fit_inputs(
    effects: Sequence[Mapping], design_matrix: object, design_names: Sequence[str],
    working_covariance: object | None = None,
) -> tuple[list[dict], list[str], list[str], np.ndarray, np.ndarray, np.ndarray | None]:
    if not isinstance(effects, (list, tuple)) or not effects:
        raise KernelError("missing_effects", "effects must be a non-empty sequence")
    if len(effects) > _MAX_EFFECTS:
        raise KernelError("unsupported_effect_count", f"at most {_MAX_EFFECTS} effects are supported",
                          n_effects=len(effects))
    rows, effect_ids, cluster_ids = [], [], []
    y, variances = [], []
    for index, row in enumerate(effects):
        if not isinstance(row, Mapping):
            raise KernelError("invalid_effect", f"effect {index + 1} must be a mapping")
        effect_id, cluster_id, study_id = row.get("effect_id"), row.get("cluster_id"), row.get("study_id")
        if not isinstance(effect_id, str) or not effect_id.strip():
            raise KernelError("missing_effect_id", f"effect {index + 1} needs a stable effect_id")
        if not isinstance(cluster_id, str) or not cluster_id.strip():
            raise KernelError("missing_cluster_id", f"effect {index + 1} needs an explicit independent cluster_id")
        if not isinstance(study_id, str) or not study_id.strip():
            raise KernelError("missing_study_id", f"effect {index + 1} needs a study_id")
        estimate = _finite(row.get("estimate"), f"estimate for effect {effect_id}")
        variance = _finite(row.get("variance"), f"variance for effect {effect_id}")
        if variance <= 0:
            raise KernelError("nonpositive_variance", f"variance for effect {effect_id} must be positive")
        rows.append({"effect_id": effect_id.strip(), "cluster_id": cluster_id.strip(),
                     "study_id": study_id.strip(),
                     "estimate": estimate, "variance": variance})
        effect_ids.append(effect_id.strip())
        cluster_ids.append(cluster_id.strip())
        y.append(estimate)
        variances.append(variance)
    _ids(effect_ids, "effect_ids")
    covariance = None
    if working_covariance is not None:
        covariance = _square_matrix(working_covariance, "working covariance")
        if covariance.shape != (len(rows), len(rows)):
            raise KernelError("covariance_dimension_mismatch", "working covariance must match the selected effect count",
                              n_effects=len(rows), shape=list(covariance.shape))
        covariance = _check_symmetric(covariance, "working covariance")
        _check_positive_definite(covariance, "working covariance")
        same_cluster = np.equal.outer(cluster_ids, cluster_ids)
        if np.any(np.abs(covariance[~same_cluster]) > max(float(np.max(np.abs(covariance))) * 1e-12, 1e-15)):
            raise KernelError("cross_cluster_covariance", "working covariance must be block diagonal by independent cluster")
    try:
        x = np.asarray(design_matrix, dtype=float)
    except (TypeError, ValueError, OverflowError):
        raise KernelError("invalid_design_matrix", "design_matrix must be finite and two-dimensional") from None
    names = _ids(design_names, "design_names")
    if len(names) != len(set(names)):
        raise KernelError("duplicate_design_name", "design column names must be unique")
    if x.ndim != 2 or x.shape[0] != len(rows) or x.shape[1] != len(names) or not np.isfinite(x).all():
        raise KernelError("invalid_design_matrix", "design_matrix dimensions must match effects and design_names")
    try:
        rank = int(np.linalg.matrix_rank(x))
    except np.linalg.LinAlgError:
        rank = -1
    if rank != x.shape[1]:
        raise KernelError("rank_deficient_design", "design_matrix must have full column rank",
                          rank=rank, n_columns=int(x.shape[1]))
    if len(rows) < x.shape[1]:
        raise KernelError("insufficient_effects", "the number of effects must be at least the number of design columns")
    return rows, effect_ids, cluster_ids, np.asarray(y), x, covariance


def _htz_test(
    beta: np.ndarray, cluster_maps: list[np.ndarray], model_covariance: np.ndarray,
    robust_covariance: np.ndarray,
    constraints: object, design_names: list[str], constraint_names: Sequence[str] | None,
    null_values: Sequence[Real] | None,
) -> dict:
    try:
        c = np.asarray(constraints, dtype=float)
    except (TypeError, ValueError, OverflowError):
        return {"status": "unavailable", "reason_code": "invalid_constraints",
                "reason": "constraints must be a finite two-dimensional matrix"}
    if c.ndim != 2 or c.shape[1] != len(beta) or c.shape[0] == 0 or not np.isfinite(c).all():
        return {"status": "unavailable", "reason_code": "invalid_constraints",
                "reason": "constraints must have one column per design coefficient"}
    q = c.shape[0]
    if q > _HTZ_MAX_CONSTRAINTS:
        return {"status": "unavailable", "reason_code": "too_many_constraints",
                "reason": (f"HTZ materializes q^2 dense n-by-n matrices "
                           f"(q={q}, memory O(q^2 n^2)); the cap is "
                           f"{_HTZ_MAX_CONSTRAINTS} constraints per test")}
    if np.linalg.matrix_rank(c) != q:
        return {"status": "unavailable", "reason_code": "rank_deficient_constraints",
                "reason": "constraints must have full row rank"}
    names = ([f"constraint_{index + 1}" for index in range(q)]
             if constraint_names is None else list(constraint_names))
    if len(names) != q or any(not isinstance(name, str) or not name.strip() for name in names):
        return {"status": "unavailable", "reason_code": "invalid_constraint_names",
                "reason": "constraint_names must contain one non-empty name per constraint"}
    if null_values is None:
        d = np.zeros(q)
    else:
        try:
            d = np.asarray([_finite(value, "null_value") for value in null_values], dtype=float)
        except KernelError as exc:
            return {"status": "unavailable", "reason_code": exc.code, "reason": str(exc)}
        if d.shape != (q,):
            return {"status": "unavailable", "reason_code": "invalid_null_values",
                    "reason": "null_values must have one value per constraint"}
    omega = c @ robust_covariance @ c.T
    try:
        omega_inverse = np.linalg.inv(omega)
    except (np.linalg.LinAlgError, KernelError) as exc:
        code = exc.code if isinstance(exc, KernelError) else "singular_robust_covariance"
        return {"status": "unavailable", "reason_code": code,
                "reason": "constraint robust covariance is singular or not estimable"}
    contrast = c @ beta - d
    statistic = float(contrast @ omega_inverse @ contrast)
    clusters = len(cluster_maps)
    if q * q * clusters * clusters * 8 > 64 * 1024 * 1024:
        return {"status": "unavailable", "reason_code": "htz_memory_limit",
                "reason": "HTZ cluster covariance array exceeds the 64 MiB limit"}
    maps = np.stack([c @ mapping for mapping in cluster_maps])
    p_array = np.einsum("jan,kbn->abjk", maps, maps, optimize=True)
    target_omega = np.trace(p_array, axis1=2, axis2=3)
    try:
        omega_inverse_sqrt = _symmetric_inverse_sqrt(
            target_omega, "constraint working-model covariance")
    except KernelError as exc:
        return {"status": "unavailable", "reason_code": exc.code,
                "reason": "constraint working-model covariance is singular or not estimable"}
    normalized = np.einsum("as,stjk,bt->abjk", omega_inverse_sqrt, p_array,
                           omega_inverse_sqrt, optimize=True)
    variance_matrix = np.empty((q, q), dtype=float)
    for left in range(q):
        for right in range(q):
            variance_matrix[left, right] = (
                float(np.sum(normalized[left, right] * normalized[right, left]))
                + float(np.sum(normalized[left, left] * normalized[right, right]))
            )
    total_variance = float(np.sum(variance_matrix))
    if not math.isfinite(total_variance) or total_variance <= 0:
        return {"status": "unavailable", "reason_code": "invalid_htz_variance",
                "reason": "HTZ denominator degrees of freedom could not be computed"}
    nu = q * (q + 1.0) / total_variance
    df_denominator = nu - q + 1.0
    if not math.isfinite(nu) or df_denominator <= 0:
        return {"status": "unavailable", "reason_code": "insufficient_htz_degrees_of_freedom",
                "reason": "HTZ denominator degrees of freedom are not positive",
                "df_numerator": q, "df_denominator": None if not math.isfinite(df_denominator) else df_denominator}
    delta = max(df_denominator / nu, 0.0)
    f_statistic = delta * statistic / q
    p_value = float(f_distribution.sf(f_statistic, q, df_denominator))
    return {
        "status": "available", "method": "HTZ", "statistic": statistic,
        "f_statistic": f_statistic, "df_numerator": q,
        "df_denominator": df_denominator, "satterthwaite_nu": nu,
        "p_value": p_value, "constraints": c.tolist(), "constraint_names": names,
        "coefficient_order": design_names,
        "null_values": d.tolist(), "covariance_method": "CR2 cluster sandwich",
    }


def fit_dependent_gls(
    effects: Sequence[Mapping],
    design_matrix: object,
    design_names: Sequence[str],
    *,
    covariance_kind: str = "independent",
    rho: Real | None = None,
    supplied_covariance: object | None = None,
    covariance_effect_ids: Sequence[str] | None = None,
    covariance_source_note: str | None = None,
    additive_covariance: object | None = None,
    additive_covariance_effect_ids: Sequence[str] | None = None,
    effect_directions: Sequence[str] | None = None,
    vcov_type: str = "cr2",
    level: Real = 0.95,
    constraints: object | None = None,
    constraint_names: Sequence[str] | None = None,
    null_values: Sequence[Real] | None = None,
) -> dict:
    """Fit fixed GLS and CR0/CR1/CR2 inference for independent clusters.

    Effects require ``effect_id``, explicit independent ``cluster_id``,
    ``estimate``, and sampling ``variance``. The working V is built from the
    selected structure or an ID-ordered supplied matrix. CR2 and coefficient
    tests use Satterthwaite t inference; a supplied multi-row contrast gets an
    HTZ joint test. Covariance or fit parameters are never jittered.
    """
    rows, effect_ids, cluster_ids, y, x, _ = _validate_fit_inputs(
        effects, design_matrix, design_names)
    covariance_spec = build_working_covariance(
        effect_ids, [row["variance"] for row in rows], cluster_ids,
        kind=covariance_kind, rho=rho, matrix=supplied_covariance,
        covariance_effect_ids=covariance_effect_ids,
    )
    sampling_covariance = np.asarray(covariance_spec["matrix"], dtype=float)
    direction_recoding = None
    if effect_directions is not None:
        direction_recoding = transform_effect_directions(
            effect_ids, y, effect_directions, sampling_covariance,
        )
        y = np.asarray(direction_recoding["estimates"], dtype=float)
        sampling_covariance = np.asarray(direction_recoding["covariance"], dtype=float)
        for row, estimate in zip(rows, y):
            row["estimate"] = float(estimate)
        covariance_spec["matrix"] = sampling_covariance.tolist()
    additive = np.zeros_like(sampling_covariance)
    if additive_covariance is not None:
        if additive_covariance_effect_ids is None:
            raise KernelError("missing_additive_covariance_ids",
                              "additive_covariance requires additive_covariance_effect_ids")
        source_ids = _ids(additive_covariance_effect_ids, "additive_covariance_effect_ids")
        source = _square_matrix(additive_covariance, "additive covariance")
        if source.shape != (len(source_ids), len(source_ids)):
            raise KernelError("covariance_dimension_mismatch",
                              "additive covariance dimensions must match additive_covariance_effect_ids",
                              n_ids=len(source_ids), shape=list(source.shape))
        source = _check_symmetric(source, "additive covariance")
        source_index = {effect_id: index for index, effect_id in enumerate(source_ids)}
        missing = [effect_id for effect_id in effect_ids if effect_id not in source_index]
        if missing:
            raise KernelError("covariance_effect_missing",
                              "selected effects are missing from additive_covariance_effect_ids",
                              effect_ids=missing)
        selected = [source_index[effect_id] for effect_id in effect_ids]
        additive = source[np.ix_(selected, selected)]
        magnitude = max(float(np.max(np.abs(additive))), np.finfo(float).tiny)
        try:
            minimum_eigenvalue = float(np.linalg.eigvalsh(additive)[0])
        except np.linalg.LinAlgError:
            raise KernelError("additive_covariance_eigendecomposition_failed",
                              "additive covariance eigendecomposition failed") from None
        if minimum_eigenvalue < -magnitude * 1e-10:
            raise KernelError("additive_covariance_not_psd", "additive covariance must be positive semidefinite",
                              minimum_eigenvalue=minimum_eigenvalue)
        same_cluster = np.equal.outer(cluster_ids, cluster_ids)
        if np.any(np.abs(additive[~same_cluster]) > max(magnitude * 1e-12, 1e-15)):
            raise KernelError("cross_cluster_covariance",
                              "additive covariance must be block diagonal by independent cluster")
        if effect_directions is not None:
            additive = np.asarray(transform_effect_directions(
                effect_ids, np.zeros(len(effect_ids)), effect_directions, additive,
            )["covariance"], dtype=float)
    working_covariance = sampling_covariance + additive
    _check_positive_definite(working_covariance, "total working covariance")
    method = vcov_type.lower() if isinstance(vcov_type, str) else ""
    if method not in {"cr0", "cr1", "cr2"}:
        raise KernelError("unsupported_vcov_type", "vcov_type must be cr0, cr1, or cr2", vcov_type=vcov_type)
    level_value = _finite(level, "level")
    if not 0 < level_value < 1:
        raise KernelError("invalid_level", "level must be strictly between 0 and 1")

    cluster_order = list(dict.fromkeys(cluster_ids))
    indices_by_cluster = [np.flatnonzero(np.asarray(cluster_ids, dtype=object) == cluster)
                          for cluster in cluster_order]
    try:
        x_white = np.empty_like(x)
        y_white = np.empty_like(y)
        for indices in indices_by_cluster:
            cholesky = np.linalg.cholesky(working_covariance[np.ix_(indices, indices)])
            x_white[indices] = np.linalg.solve(cholesky, x[indices])
            y_white[indices] = np.linalg.solve(cholesky, y[indices])
        information = x_white.T @ x_white
        model_covariance = np.linalg.inv(information)
        beta = model_covariance @ x_white.T @ y_white
    except (np.linalg.LinAlgError, FloatingPointError, ValueError):
        raise KernelError("gls_fit_failed", "GLS information matrix could not be solved") from None
    if not np.isfinite(beta).all() or not np.isfinite(model_covariance).all():
        raise KernelError("gls_fit_failed", "GLS fit produced non-finite estimates")
    model_se = np.sqrt(np.diag(model_covariance))
    coefficient_results = [{"name": name, "estimate": float(beta[index]),
                            "model_based_se": float(model_se[index])}
                           for index, name in enumerate(design_names)]

    warnings = []
    if len(cluster_order) < 2:
        inference = _unavailable("insufficient_independent_clusters",
                                 "at least two independent clusters are required for robust inference",
                                 n_clusters=len(cluster_order))
    else:
        robust_maps: list[np.ndarray] = []
        adjustment_matrices: list[np.ndarray] = []
        inference_error: KernelError | None = None
        for indices in indices_by_cluster:
            x_cluster = x_white[indices]
            h_cluster = x_cluster @ model_covariance @ x_cluster.T
            residual_covariance = (np.eye(len(indices)) - h_cluster)
            if method == "cr2":
                try:
                    adjustment = _symmetric_inverse_sqrt(residual_covariance,
                                                         "CR2 residual covariance adjustment")
                except KernelError as exc:
                    inference_error = KernelError(
                        "cr2_adjustment_failed",
                        "CR2 adjustment is singular or numerically unstable for at least one cluster",
                        cluster_id=cluster_ids[int(indices[0])], **exc.details,
                    )
                    break
            else:
                adjustment = np.eye(len(indices))
            row_projection = -(x_cluster @ model_covariance @ x_white.T)
            row_projection[:, indices] += np.eye(len(indices))
            mapping = model_covariance @ x_cluster.T @ adjustment @ row_projection
            if method == "cr1":
                mapping *= math.sqrt(len(cluster_order) / (len(cluster_order) - 1.0))
            adjustment_matrices.append(adjustment)
            robust_maps.append(mapping)

        if inference_error is not None:
            inference = _unavailable(inference_error.code, str(inference_error), **inference_error.details)
        else:
            contributions = [mapping @ y_white for mapping in robust_maps]
            robust_covariance = sum((np.outer(value, value) for value in contributions),
                                    start=np.zeros_like(model_covariance))
            if not np.isfinite(robust_covariance).all():
                inference = _unavailable("invalid_robust_covariance",
                                         "robust covariance contains non-finite values")
            else:
                tests = []
                for index, name in enumerate(design_names):
                    robust_variance = float(robust_covariance[index, index])
                    if robust_variance <= 0:
                        inference_error = KernelError(
                            "zero_robust_variance", f"robust variance for {name} is not positive",
                            coefficient=name,
                        )
                        break
                    cluster_rows = np.asarray([mapping[index] for mapping in robust_maps])
                    gram = cluster_rows @ cluster_rows.T
                    trace = float(np.trace(gram))
                    trace_squared = float(np.sum(gram * gram))
                    df = trace * trace / trace_squared if trace_squared > 0 else math.nan
                    if not math.isfinite(df) or df <= 0:
                        inference_error = KernelError(
                            "satterthwaite_df_failed", f"Satterthwaite degrees of freedom for {name} are unavailable",
                            coefficient=name,
                        )
                        break
                    se = math.sqrt(robust_variance)
                    statistic = float(beta[index] / se)
                    critical = float(student_t.ppf((1.0 + level_value) / 2.0, df))
                    tests.append({
                        "name": name, "estimate": float(beta[index]), "robust_se": se,
                        "model_based_se": float(model_se[index]), "statistic": statistic,
                        "df": df, "ci_low": float(beta[index] - critical * se),
                        "ci_high": float(beta[index] + critical * se),
                        "p_value": float(2.0 * student_t.sf(abs(statistic), df)),
                    })
                if inference_error is not None:
                    inference = _unavailable(inference_error.code, str(inference_error), **inference_error.details)
                else:
                    joint = None
                    if constraints is not None:
                        joint = _htz_test(beta, robust_maps, model_covariance, robust_covariance, constraints,
                                          list(design_names), constraint_names, null_values)
                    inference = {
                        "status": "available", "vcov_type": method.upper(),
                        "covariance": robust_covariance.tolist(),
                        "coefficient_tests": tests,
                        "df_method": "Satterthwaite t",
                        "joint_test": joint,
                        "warnings": warnings,
                    }
                    if len(cluster_order) < 10:
                        inference["warnings"].append(
                            f"Only {len(cluster_order)} independent clusters; small-sample power is limited."
                        )
                    if any(test["df"] < 4 for test in tests):
                        inference["warnings"].append(
                            "At least one coefficient has Satterthwaite df below 4; inference is highly small-sample-sensitive."
                        )

    # Confidence bounds use a large-sample normal reference only for the GLS
    # model-based diagnostic; robust intervals always use coefficient t dfs.
    result = {
        "model": "fixed_gls", "n_effects": len(rows),
        "n_studies": len({row["study_id"] for row in rows}),
        "n_clusters": len(cluster_order), "cluster_ids": cluster_order,
        "effect_ids": effect_ids, "design_columns": list(design_names),
        "coefficients": coefficient_results,
        "model_based_covariance": model_covariance.tolist(),
        "working_covariance": {
            **covariance_spec, "source_note": covariance_source_note,
            "sampling_matrix": sampling_covariance.tolist(),
            "additive_covariance_effect_ids": (
                effect_ids if effect_directions is not None
                else list(additive_covariance_effect_ids or effect_ids)
            ),
            "additive_covariance": additive.tolist(),
            "scale": "total_working_covariance", "matrix": working_covariance.tolist(),
        },
        "robust_inference": inference,
        "input_snapshot": {"effects": rows, "design_matrix": x.tolist(),
                           "design_columns": list(design_names)},
        "limitations": [
            "Fits GLS with a supplied total working covariance; it does not estimate random-effect variance components.",
            "CR2 inference assumes independent clusters and the supplied working covariance as its target model.",
            "HTZ is available for full-row-rank linear coefficient constraints when its denominator degrees of freedom are positive.",
        ],
    }
    if direction_recoding is not None:
        result["direction_recoding"] = {
            "target_effect_direction": direction_recoding["target_effect_direction"],
            "effect_ids": effect_ids,
            "source_directions": list(effect_directions),
            "direction_signs": direction_recoding["direction_signs"],
            "sampling_covariance_transformed": True,
            "additive_covariance_transformed": additive_covariance is not None,
        }
    return result


def leave_one_cluster_out(
    effects: Sequence[Mapping], design_matrix: object, design_names: Sequence[str], **fit_options: object,
) -> dict:
    """Delete each whole independent cluster, refit, and retain IDs and status."""
    x = np.asarray(design_matrix, dtype=float)
    # 与 fit 的规范化一致地 strip cluster_id：否则同一簇的空白变体被拆成
    # 两个"簇"，n_clusters/removed 失真（fit 内部按 strip 后的值分组）。
    cluster_order = list(dict.fromkeys(str(row["cluster_id"]).strip() for row in effects))
    results = []
    for cluster_id in cluster_order:
        removed = [row["effect_id"] for row in effects
                   if str(row["cluster_id"]).strip() == cluster_id]
        keep = [index for index, row in enumerate(effects)
                if str(row["cluster_id"]).strip() != cluster_id]
        subset = [effects[index] for index in keep]
        subset_options = dict(fit_options)
        if subset_options.get("effect_directions") is not None:
            directions = list(subset_options["effect_directions"])
            if len(directions) != len(effects):
                raise KernelError("input_length_mismatch", "effect directions must match effect IDs")
            subset_options["effect_directions"] = [directions[index] for index in keep]
        try:
            result = fit_dependent_gls(subset, x[keep], design_names, **subset_options)
        except KernelError as exc:
            results.append({"removed_cluster_id": cluster_id, "removed_effect_ids": removed,
                            "n_clusters_remaining": len(set(str(row["cluster_id"]).strip() for row in subset)),
                            "status": "failed", "reason_code": exc.code, "reason": str(exc)})
            continue
        results.append({
            "removed_cluster_id": cluster_id, "removed_effect_ids": removed,
            "n_clusters_remaining": result["n_clusters"],
            "status": ("available" if result["robust_inference"]["status"] == "available"
                       else "inference_unavailable"),
            "coefficients": result["coefficients"],
            "model_based_covariance": result["model_based_covariance"],
            "robust_inference": result["robust_inference"],
            "remaining_effect_ids": result["effect_ids"],
        })
    return {"method": "leave_one_independent_cluster_out", "reestimate": True,
            "n_clusters": len(cluster_order), "runs": results}


def rho_sensitivity(
    effects: Sequence[Mapping], design_matrix: object, design_names: Sequence[str],
    rho_values: Sequence[Real], **fit_options: object,
) -> dict:
    """Rebuild block-equicorrelation V and refit robust inference at each rho."""
    base_kind = fit_options.pop("covariance_kind", "block_correlation")
    if base_kind != "block_correlation":
        raise KernelError("unsupported_rho_sensitivity_covariance",
                          "rho_sensitivity requires block_correlation covariance")
    x = np.asarray(design_matrix, dtype=float)
    runs = []
    for rho in rho_values:
        try:
            value = _finite(rho, "rho")
            result = fit_dependent_gls(effects, x, design_names,
                                       covariance_kind="block_correlation", rho=value,
                                       **fit_options)
            runs.append({"rho": value,
                         "status": ("available" if result["robust_inference"]["status"] == "available"
                                    else "inference_unavailable"),
                         "result": result})
        except KernelError as exc:
            runs.append({"rho": rho if isinstance(rho, (int, float)) else None,
                         "status": "failed", "reason_code": exc.code, "reason": str(exc),
                         "details": exc.details})
    return {"method": "rho_sensitivity_reestimated_gls", "reestimate": True,
            "effect_ids": [row.get("effect_id") for row in effects],
            "cluster_ids": list(dict.fromkeys(row.get("cluster_id") for row in effects)),
            "runs": runs}
