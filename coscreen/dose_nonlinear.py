"""Two-stage restricted cubic spline dose-response synthesis.

Each study's contrast effects are reduced to two covariance-aware GLS
coefficients using one shared, reference-centered spline basis. The resulting
coefficient vectors and their sampling covariance matrices are combined with
an unstructured bivariate random-effects REML model.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


_RATIO_MEASURES = {"RR", "OR", "HR", "RATE_RATIO"}
_DOSE_MEASURES = {"RR", "OR", "RD", "MD", "SMD", "RATE_RATIO"}
_TERMS = ("linear", "nonlinear")


def _spline_basis(dose: float, knots: tuple[float, float, float]):
    """Return a normalized three-knot restricted cubic spline basis."""
    import numpy as np

    lower, middle, upper = knots
    span = upper - lower
    x = (float(dose) - lower) / span
    t = (middle - lower) / span
    nonlinear = (
        max(x, 0.0) ** 3
        - max(x - t, 0.0) ** 3 / (1.0 - t)
        + t * max(x - 1.0, 0.0) ** 3 / (1.0 - t)
    )
    return np.asarray([x, nonlinear], dtype=float)


def _finite_float(value: object, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{field} must be finite") from None
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _validate_inputs(curves: Sequence[dict], knots: Sequence[float], prediction_reference_dose,
                     np):
    if (not isinstance(curves, Sequence) or isinstance(curves, (str, bytes))
            or len(curves) < 3):
        raise ValueError("nonlinear dose-response synthesis requires at least three studies")
    if not isinstance(knots, Sequence) or isinstance(knots, (str, bytes)) or len(knots) != 3:
        raise ValueError("exactly three increasing common knots are required")
    parsed_knots = tuple(_finite_float(value, "knots") for value in knots)
    if not (parsed_knots[0] < parsed_knots[1] < parsed_knots[2]):
        raise ValueError("exactly three increasing common knots are required")

    first = curves[0]
    try:
        endpoint_values = (first["outcome"], first["timepoint"], first["measure"])
    except (KeyError, TypeError):
        raise ValueError("each curve needs outcome, timepoint and measure metadata") from None
    if any(value is None or not str(value).strip() for value in endpoint_values):
        raise ValueError("each curve needs outcome, timepoint and measure metadata")
    endpoint = tuple(str(value) for value in endpoint_values)
    measure = endpoint[2]
    if measure not in _DOSE_MEASURES:
        raise ValueError("unsupported dose-response measure")
    if first.get("dose_unit") is None:
        raise ValueError("outcome, timepoint and dose unit are required")
    dose_unit = str(first.get("dose_unit", "")).strip()
    if not dose_unit or not endpoint[0].strip() or not endpoint[1].strip():
        raise ValueError("outcome, timepoint and dose unit are required")

    study_ids = []
    parsed = []
    for row in curves:
        try:
            row_endpoint_values = (row["outcome"], row["timepoint"], row["measure"])
            if any(value is None or not str(value).strip() for value in row_endpoint_values):
                raise ValueError("missing endpoint metadata")
            row_endpoint = tuple(str(value) for value in row_endpoint_values)
            if row["study_id"] is None or row["source_key"] is None or row["dose_unit"] is None:
                raise ValueError("missing study metadata")
            study_id = str(row["study_id"])
            source_key = str(row["source_key"])
            row_unit = str(row["dose_unit"]).strip()
            reference = _finite_float(row["reference_dose"], "reference dose")
            contrasts = row["contrasts"]
            covariance = np.asarray(row["covariance"], dtype=float)
        except (KeyError, TypeError, ValueError, OverflowError):
            raise ValueError("each curve needs complete dose-analysis metadata and finite values") from None
        if row_endpoint != endpoint:
            raise ValueError("all curves must share outcome, timepoint and measure")
        if row_unit != dose_unit:
            raise ValueError("all studies must use the same dose unit")
        if not study_id.strip() or not source_key.strip():
            raise ValueError("study ID and source key are required")
        study_ids.append(study_id)
        if not isinstance(contrasts, Sequence) or isinstance(contrasts, (str, bytes)) or len(contrasts) < 3:
            raise ValueError("each study needs at least three dose contrasts")
        try:
            doses = [_finite_float(item["dose"], "contrast dose") for item in contrasts]
            estimates = [_finite_float(item["estimate"], "contrast estimate") for item in contrasts]
        except (KeyError, TypeError):
            raise ValueError("each contrast needs a finite dose and estimate") from None
        if any(left >= right for left, right in zip(doses, doses[1:])):
            raise ValueError("dose contrasts must be strictly increasing")
        if reference in doses:
            raise ValueError("reference dose must differ from every contrast dose")
        if measure in _RATIO_MEASURES and any(value <= 0 for value in estimates):
            raise ValueError("ratio estimates must be positive")
        if covariance.shape != (len(doses), len(doses)) or not np.isfinite(covariance).all():
            raise ValueError("covariance matrix must match the contrasts and be finite")
        if not np.allclose(covariance, covariance.T, rtol=0, atol=1e-10):
            raise ValueError("within-study covariance must be symmetric positive definite")
        try:
            eigenvalues = np.linalg.eigvalsh(covariance)
            condition = float(np.linalg.cond(covariance))
        except np.linalg.LinAlgError:
            raise ValueError("within-study covariance must be symmetric positive definite") from None
        if eigenvalues[0] <= 0 or not math.isfinite(condition) or condition > 1e12:
            raise ValueError("within-study covariance must be symmetric positive definite and well-scaled")
        if any(value < parsed_knots[0] or value > parsed_knots[2] for value in [reference, *doses]):
            raise ValueError("reference and contrast doses must be within the knot boundary range")
        parsed.append({
            "study_id": study_id,
            "source_key": source_key,
            "source_locator": (row.get("input_data") or {}).get("source_locator", ""),
            "reference_dose": reference,
            "doses": doses,
            "estimates": estimates,
            "covariance": covariance,
        })
    if len(set(study_ids)) != len(study_ids):
        raise ValueError("each independent study must appear once")

    references = {row["reference_dose"] for row in parsed}
    if prediction_reference_dose is None:
        if len(references) != 1:
            raise ValueError("a prediction reference dose is required when study reference doses differ")
        prediction_reference = next(iter(references))
    else:
        prediction_reference = _finite_float(prediction_reference_dose, "prediction reference dose")
    if not parsed_knots[0] <= prediction_reference <= parsed_knots[2]:
        raise ValueError("prediction reference dose must be within the knot boundary range")
    return parsed_knots, endpoint[0], endpoint[1], measure, dose_unit, parsed, prediction_reference


def _fit_study(row: dict, measure: str, knots: tuple[float, float, float], np):
    design = np.asarray([
        _spline_basis(dose, knots) - _spline_basis(row["reference_dose"], knots)
        for dose in row["doses"]
    ])
    if not np.isfinite(design).all():
        raise ValueError(f"spline design for study {row['study_id']} is outside the finite range")
    singular_values = np.linalg.svd(design, compute_uv=False)
    if (len(singular_values) != 2 or singular_values[-1] <= 0
            or singular_values[0] / singular_values[-1] > 1e10):
        raise ValueError(f"study {row['study_id']} has an insufficiently ranked spline design")
    y = np.asarray([
        math.log(value) if measure in _RATIO_MEASURES else value
        for value in row["estimates"]
    ], dtype=float)
    try:
        weighted_design = np.linalg.solve(row["covariance"], design)
        weighted_y = np.linalg.solve(row["covariance"], y)
        information = design.T @ weighted_design
        information_condition = float(np.linalg.cond(information))
        if not math.isfinite(information_condition) or information_condition > 1e10:
            raise np.linalg.LinAlgError
        coefficient_covariance = np.linalg.inv(information)
        coefficients = coefficient_covariance @ design.T @ weighted_y
    except np.linalg.LinAlgError:
        raise ValueError(f"study {row['study_id']} has an insufficiently ranked spline design") from None
    if not np.isfinite(coefficients).all() or not np.isfinite(coefficient_covariance).all():
        raise ValueError(f"study {row['study_id']} spline estimates are outside the finite range")
    return coefficients, coefficient_covariance


def _fixed_q(coefficients: list, covariances: list, np):
    information = np.zeros((2, 2), dtype=float)
    score = np.zeros(2, dtype=float)
    inverses = []
    for beta, covariance in zip(coefficients, covariances):
        inverse = np.linalg.inv(covariance)
        inverses.append(inverse)
        information += inverse
        score += inverse @ beta
    pooled = np.linalg.solve(information, score)
    q = sum(float((beta - pooled) @ inverse @ (beta - pooled))
            for beta, inverse in zip(coefficients, inverses))
    return max(0.0, q), 2 * len(coefficients) - 2


def fit_nonlinear_dose_response(curves: Sequence[dict], knots: Sequence[float],
                                prediction_reference_dose: float | None = None) -> dict:
    """Fit a reference-centered three-knot spline dose-response meta-analysis.

    ``curves`` uses the row contract returned by ``dose_analysis.list_curves``.
    ``knots`` are common lower, interior and upper knots in ``dose_unit``.
    Ratio measures require estimates and covariance on the log-ratio scale;
    returned coefficients and curve values remain on that analysis scale.
    """
    import numpy as np
    from scipy.stats import chi2, norm

    (parsed_knots, outcome, timepoint, measure, dose_unit, rows,
     prediction_reference) = _validate_inputs(
        curves, knots, prediction_reference_dose, np,
    )
    if parsed_knots[1] - parsed_knots[0] <= 1e-12 * (parsed_knots[2] - parsed_knots[0]) or (
        parsed_knots[2] - parsed_knots[1] <= 1e-12 * (parsed_knots[2] - parsed_knots[0])
    ):
        raise ValueError("three increasing common knots must be well-scaled")

    study_coefficients = []
    study_covariances = []
    studies = []
    for row in rows:
        coefficients, coefficient_covariance = _fit_study(row, measure, parsed_knots, np)
        study_coefficients.append(coefficients)
        study_covariances.append(coefficient_covariance)
        studies.append({
            "study_id": row["study_id"],
            "source_key": row["source_key"],
            "source_locator": row["source_locator"],
            "reference_dose": row["reference_dose"],
            "contrast_doses": row["doses"],
            "contrasts": [
                {"dose": dose, "estimate": estimate}
                for dose, estimate in zip(row["doses"], row["estimates"])
            ],
            "sampling_covariance": row["covariance"].tolist(),
            "coefficients": [
                {"term": term, "estimate": float(value)}
                for term, value in zip(_TERMS, coefficients)
            ],
            "coefficient_covariance": {
                "terms": list(_TERMS),
                "scale": "log" if measure in _RATIO_MEASURES else "identity",
                "matrix": coefficient_covariance.tolist(),
            },
        })

    q_value, q_df = _fixed_q(study_coefficients, study_covariances, np)
    fixed_result = {
        "analysis_scale": "log" if measure in _RATIO_MEASURES else "identity",
        "estimates": [{"term": "linear_plus_nonlinear"}, {"term": "nonlinear"}],
        "q": {
            "statistic": q_value,
            "df": q_df,
            "p_value": float(chi2.sf(q_value, q_df)),
        },
    }
    # An invertible shear avoids a zero-correlation stationary start in the
    # shared bivariate REML optimizer. Transforming both coefficients and
    # sampling covariance leaves the model unchanged; results are mapped back.
    basis_transform = np.asarray([[1.0, 1.0], [0.0, 1.0]])
    inverse_transform = np.asarray([[1.0, -1.0], [0.0, 1.0]])
    transformed_coefficients = [basis_transform @ beta for beta in study_coefficients]
    transformed_covariances = [basis_transform @ covariance @ basis_transform.T
                               for covariance in study_covariances]
    internal_terms = ("linear_plus_nonlinear", "nonlinear")
    rows_by_study = [[
        {"outcome": term, "estimate": float(value)}
        for term, value in zip(internal_terms, coefficients)
    ] for coefficients in transformed_coefficients]
    # The helper's measure controls inverse-link handling; spline coefficients
    # are already expressed on their final analysis scale, so use identity.
    from coscreen.dependent_random import synthesize_random_reml_un
    pooled = synthesize_random_reml_un(
        fixed_result, rows_by_study, transformed_covariances, list(internal_terms), "MD",
    )
    transformed_coefficient_covariance = np.asarray(
        pooled["coefficient_covariance"]["matrix"], dtype=float,
    )
    transformed_coefficient_vector = np.asarray(
        [row["estimate"] for row in pooled["estimates"]], dtype=float,
    )
    coefficient_vector = inverse_transform @ transformed_coefficient_vector
    coefficient_covariance = (
        inverse_transform @ transformed_coefficient_covariance @ inverse_transform.T
    )
    transformed_between_study_covariance = np.asarray(
        pooled["between_study_covariance"]["matrix"], dtype=float,
    )
    between_study_covariance = (
        inverse_transform @ transformed_between_study_covariance @ inverse_transform.T
    )
    grid = sorted(set(np.linspace(parsed_knots[0], parsed_knots[2], 51).tolist()
                      + list(parsed_knots) + [prediction_reference]))
    curve = []
    z_975 = float(norm.ppf(.975))
    reference_basis = _spline_basis(prediction_reference, parsed_knots)
    for dose in grid:
        contrast_basis = _spline_basis(dose, parsed_knots) - reference_basis
        estimate = float(contrast_basis @ coefficient_vector)
        variance = float(contrast_basis @ coefficient_covariance @ contrast_basis)
        standard_error = math.sqrt(max(0.0, variance))
        curve.append({
            "dose": float(dose),
            "estimate": estimate,
            "se": standard_error,
            "ci_low": estimate - z_975 * standard_error,
            "ci_high": estimate + z_975 * standard_error,
        })

    pooled_coefficients = []
    for index, term in enumerate(_TERMS):
        estimate = float(coefficient_vector[index])
        standard_error = math.sqrt(float(coefficient_covariance[index, index]))
        pooled_coefficients.append({
            "term": term,
            "estimate": estimate,
            "se": standard_error,
            "ci_low": estimate - z_975 * standard_error,
            "ci_high": estimate + z_975 * standard_error,
        })
    return {
        "method": "Two-stage restricted cubic spline dose-response GLS with unstructured random-effects REML",
        "model": "two_stage_rcs_random_reml_un",
        "outcome": outcome,
        "timepoint": timepoint,
        "measure": measure,
        "dose_unit": dose_unit,
        "analysis_scale": fixed_result["analysis_scale"],
        "n_studies": len(rows),
        "knots": list(parsed_knots),
        "reference_dose": prediction_reference,
        "basis": {
            "name": "restricted_cubic_spline",
            "terms": list(_TERMS),
            "dose_normalization": "(dose - lower_knot) / (upper_knot - lower_knot)",
        },
        "studies": studies,
        "pooled_coefficients": pooled_coefficients,
        "coefficient_covariance": {
            "outcomes": list(_TERMS),
            "scale": fixed_result["analysis_scale"],
            "matrix": coefficient_covariance.tolist(),
        },
        "between_study_covariance": {
            "outcomes": list(_TERMS),
            "scale": fixed_result["analysis_scale"],
            "matrix": between_study_covariance.tolist(),
        },
        "curve": curve,
        "q": pooled["q"],
        "fit": pooled["fit"],
        "warnings": pooled["warnings"],
        "limitations": [
            "All studies use the same three knots and the same restricted cubic spline basis.",
            "Within-study covariance matrices are treated as known and must already use the stated analysis scale.",
            "The two-stage fit requires each study's spline design to identify both coefficients and at least three studies to identify an unstructured between-study covariance.",
            "Curve intervals use a large-sample normal approximation and describe the mean curve, not a prediction interval for a new study.",
            "The spline and resulting curve are exploratory when dose coverage or the number of studies is limited.",
        ],
    }
