"""Two-stage linear dose-response meta-analysis with study-specific covariance."""

from __future__ import annotations

import json
import math
from contextlib import closing
from pathlib import Path

from coscreen.db import _connect
from coscreen.review_analysis import RATIOS, _linked, _synthesize_rows

DOSE_MEASURES = {"RR", "OR", "RD", "MD", "SMD", "RATE_RATIO"}

#: ``review_dose_curves`` 中以 JSON 文本存放的三列（读路径需逐行解码）。
_JSON_COLUMNS = ("contrasts", "covariance", "input_data")

#: ``json.loads`` 可抛出的异常族（``json.JSONDecodeError`` 是 ValueError 子类，
#: 但行值不是 str/bytes 时是 TypeError）。
_DECODE_ERRORS = (TypeError, ValueError)


def _validate(contrasts: list[dict], covariance: list[list[float]], reference_dose: float,
              measure: str) -> tuple[list[dict], "object"]:
    """校验一条曲线并返回可直接落库的规范值（F3-79）。

    校验用 ``float()`` 但落库必须存**规范化后的数值**：旧实现按原值存储，
    字符串剂量能进库、到 ``synthesize`` 的 ``dose - reference_dose`` 才抛
    TypeError（500）。另外 ``set(row)`` 对字符串行能通过键检查（
    ``set(["dose", "estimate"]) == {"dose", "estimate"}``），随后
    ``row["dose"]`` 在 list 上抛 TypeError——两种形状都必须按 ValueError
    拒绝，且数值转换失败也要统一成 ValueError。

    Returns
    -------
    (clean_contrasts, matrix)
        ``clean_contrasts`` 为 ``{"dose": float, "estimate": float}`` 行，
        ``matrix`` 为 float 版协方差（``numpy.asarray``）。
    """
    import numpy as np
    if measure not in DOSE_MEASURES or not math.isfinite(reference_dose):
        raise ValueError("valid measure and finite reference dose are required")
    if len(contrasts) < 2 or any(not isinstance(row, dict) or set(row) != {"dose", "estimate"}
                                 for row in contrasts):
        raise ValueError("at least two dose contrasts with dose and estimate are required")
    try:
        doses = [float(row["dose"]) for row in contrasts]
        estimates = [float(row["estimate"]) for row in contrasts]
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("dose and effect values must be finite numbers") from exc
    if (not all(math.isfinite(x) for x in doses + estimates) or len(set(doses)) != len(doses)
            or reference_dose in doses or (measure in RATIOS and min(estimates) <= 0)):
        raise ValueError("dose and effect values must be finite, distinct and valid")
    matrix = np.asarray(covariance, dtype=float)
    if matrix.shape != (len(doses), len(doses)) or not np.isfinite(matrix).all():
        raise ValueError("covariance matrix must match the contrasts and be finite")
    if not np.allclose(matrix, matrix.T, rtol=0, atol=1e-10) or np.linalg.eigvalsh(matrix).min() <= 0:
        raise ValueError("covariance matrix must be symmetric positive definite")
    clean = [{"dose": dose, "estimate": estimate} for dose, estimate in zip(doses, estimates)]
    return clean, matrix


def save_curve(db: str | Path, study_id: str, outcome: str, timepoint: str,
               measure: str, reference_dose: float, dose_unit: str,
               contrasts: list[dict], covariance: list[list[float]], source_key: str, *,
               input_data: dict | None = None) -> None:
    if not all(str(v).strip() for v in (study_id, outcome, timepoint, dose_unit, source_key)):
        raise ValueError("study, outcome, timepoint, dose unit and source are required")
    clean_contrasts, _matrix = _validate(contrasts, covariance, reference_dose, measure)
    if input_data is not None and not isinstance(input_data, dict):
        raise ValueError("dose-curve input_data must be an object")
    # 写路径防护（F3-76）：审计载荷不可序列化（set/自定义对象/NaN）时必须是
    # ValueError（端点 400），不能让 json.dumps 的 TypeError/ValueError 逃逸成 500。
    try:
        audit_json = json.dumps(input_data or {}, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("dose-curve input_data must be JSON-serializable with finite numbers") from exc
    # 只落规范化数值：字符串剂量进库后会在 synthesize 的减法处抛 TypeError（F3-79）。
    contrasts_json = json.dumps(clean_contrasts, allow_nan=False)
    with closing(_connect(db)) as conn, conn:
        _linked(conn, study_id, source_key)
        conn.execute("INSERT INTO review_dose_curves (study_id,outcome,timepoint,measure,reference_dose,dose_unit,contrasts_json,covariance_json,source_key,input_json) VALUES (?,?,?,?,?,?,?,?,?,?) "
                     "ON CONFLICT(study_id,outcome,timepoint,measure) DO UPDATE SET "
                     "reference_dose=excluded.reference_dose,dose_unit=excluded.dose_unit,"
                     "contrasts_json=excluded.contrasts_json,covariance_json=excluded.covariance_json,"
                     "source_key=excluded.source_key,input_json=excluded.input_json",
                     (study_id, outcome.strip(), timepoint.strip(), measure, reference_dose,
                      dose_unit.strip(), contrasts_json, json.dumps(covariance), source_key, audit_json))


def _decode_curve_row(row) -> dict:  # noqa: ANN001
    """把一行 ``review_dose_curves`` 解成曲线 dict，坏列降级而不抛异常（F3-76）。

    三列 JSON 文本任一损坏时，该列置为 ``None``（``input_data`` 置 ``{}``）
    并在 ``unreadable`` 里记录原因：GET 列表照常 200（用户能看到是哪条研究
    的存储坏了，重新录入即可覆盖修复），合成路径则跳过该行并给 warning。
    """
    study_id, outcome, timepoint, measure, reference_dose, dose_unit = row[:6]
    record = {"study_id": study_id, "outcome": outcome, "timepoint": timepoint,
              "measure": measure, "reference_dose": reference_dose, "dose_unit": dose_unit}
    raw = {"contrasts": row[6], "covariance": row[7], "input_data": row[9]}
    problems = {}
    for column in _JSON_COLUMNS:
        try:
            record[column] = json.loads(raw[column])
        except _DECODE_ERRORS as exc:
            record[column] = {} if column == "input_data" else None
            problems[column] = str(exc)
    record["source_key"] = row[8]
    if problems:
        record["unreadable"] = problems
    return record


def list_curves(db: str | Path) -> list[dict]:
    with closing(_connect(db)) as conn:
        rows = conn.execute("SELECT study_id,outcome,timepoint,measure,reference_dose,dose_unit,"
                            "contrasts_json,covariance_json,source_key,input_json FROM review_dose_curves "
                            "ORDER BY study_id,outcome,timepoint").fetchall()
    return [_decode_curve_row(row) for row in rows]


def _curve_key(row: dict) -> tuple:
    return (row["outcome"], row["timepoint"], row["measure"])


def _unreadable_study_names(rows: list[dict]) -> str:
    return ", ".join(sorted(str(row.get("study_id")) for row in rows))


def synthesize(db: str | Path, outcome: str, timepoint: str, measure: str,
               model: str = "random_pm") -> dict:
    import numpy as np
    if model not in {"fixed", "random", "random_pm"}:
        raise ValueError("invalid dose-response model")
    selected = [row for row in list_curves(db) if _curve_key(row) == (outcome, timepoint, measure)]
    curves = [row for row in selected if "unreadable" not in row]
    skipped = [row for row in selected if "unreadable" in row]
    if len(curves) < 2:
        if skipped:
            raise ValueError(
                "dose-response synthesis requires at least two independent studies with readable "
                f"stored curves; the stored curves for {_unreadable_study_names(skipped)} are "
                "unreadable (re-enter those studies to repair them)")
        raise ValueError("dose-response synthesis requires at least two independent studies")
    if len({row["dose_unit"] for row in curves}) != 1:
        raise ValueError("all studies need the same dose unit")
    slopes = []
    for row in curves:
        clean_contrasts, matrix = _validate(row["contrasts"], row["covariance"],
                                            row["reference_dose"], measure)
        x = np.asarray([item["dose"] - row["reference_dose"] for item in clean_contrasts])
        y = np.asarray([math.log(item["estimate"]) if measure in RATIOS else item["estimate"]
                        for item in clean_contrasts])
        inverse_x = np.linalg.solve(matrix, x)
        information = float(x @ inverse_x)
        if not math.isfinite(information) or information <= 0:
            # F3-75：次正规剂量间距把信息量下溢成 0.0，旧实现在 1/information
            # 处抛 ZeroDivisionError（非 ValueError）→ 端点 500。
            raise ValueError(
                f"study {row['study_id']} dose contrasts carry no usable information at the "
                "representable scale; the dose spacing is too small relative to the "
                "within-study covariance")
        slope = float(inverse_x @ y / information)
        se = math.sqrt(1 / information)
        if not math.isfinite(slope) or not math.isfinite(se):
            raise ValueError(f"study {row['study_id']} dose-response slope is outside the finite range")
        slopes.append({"study_id": row["study_id"], "estimate": slope,
                       "se": se, "source_key": row["source_key"],
                       "source_locator": (row["input_data"] or {}).get("source_locator", "")})
    pooled = _synthesize_rows(slopes, "MD", model, "hksj" if model != "fixed" else "normal")
    warning = ("Assumes a linear response and correctly specified within-study covariance; "
               "inspect dose shape before interpretation.")
    if skipped:
        warning += (" Stored curves for " + _unreadable_study_names(skipped) +
                    " are unreadable and were excluded; re-enter those studies to repair them.")
    return {"method": "two-stage linear dose-response GLS with supplied within-study covariance",
            "effect_scale": "log ratio per dose unit" if measure in RATIOS else "difference per dose unit",
            "dose_unit": curves[0]["dose_unit"], "study_slopes": slopes,
            "pooled_slope": pooled["pooled"], "ci_low": pooled["ci_low"],
            "ci_high": pooled["ci_high"], "tau2": pooled["tau2"],
            "unreadable_studies": [row["study_id"] for row in skipped],
            "warning": warning}
