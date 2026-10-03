"""Fixed-effect Mantel–Haenszel pooling for independent binary-arm studies."""

from __future__ import annotations

import math
from collections.abc import Mapping
from numbers import Integral
from statistics import NormalDist

_Z_975 = NormalDist().inv_cdf(0.975)
_COUNT_FIELDS = ("events_t", "total_t", "events_c", "total_c")


def _validated_studies(studies: list[dict]) -> list[dict]:
    if not isinstance(studies, (list, tuple)) or not studies:
        raise ValueError("studies must be a non-empty list")

    validated = []
    seen_ids = set()
    for index, study in enumerate(studies):
        if not isinstance(study, Mapping):
            raise ValueError(f"study at index {index} must be a mapping")
        study_id = study.get("study_id")
        if not isinstance(study_id, str) or not study_id.strip():
            raise ValueError(f"study at index {index} needs a non-empty study_id")
        study_id = study_id.strip()
        if study_id in seen_ids:
            raise ValueError(f"duplicate study_id: {study_id}")
        seen_ids.add(study_id)

        counts = {}
        for field in _COUNT_FIELDS:
            value = study.get(field)
            if isinstance(value, bool) or not isinstance(value, Integral):
                raise ValueError(f"{field} for study {study_id} must be an integer")
            if value < 0:
                raise ValueError(f"{field} for study {study_id} must be non-negative")
            try:
                number = float(value)
            except (OverflowError, ValueError) as exc:
                raise ValueError(f"counts for study {study_id} are too large") from exc
            if not math.isfinite(number):
                raise ValueError(f"counts for study {study_id} are too large")
            counts[field] = number

        if counts["total_t"] <= 0 or counts["total_c"] <= 0:
            raise ValueError(f"arm totals for study {study_id} must be positive")
        if counts["events_t"] > counts["total_t"] or counts["events_c"] > counts["total_c"]:
            raise ValueError(f"events cannot exceed arm totals for study {study_id}")

        validated.append({"study_id": study_id, **counts})
    return validated


def _finite(value: float, label: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} is not finite")
    return value


def pool_mantel_haenszel(studies: list[dict], measure: str) -> dict:
    """Pool RR, OR, or RD with the fixed-effect equations in RevMan 5.

    RR and OR standard errors are on the log scale; RD's is on the natural
    scale. No continuity correction is applied.
    """
    if not isinstance(measure, str) or measure not in {"RR", "OR", "RD"}:
        raise ValueError("measure must be RR, OR, or RD")
    rows = _validated_studies(studies)
    included = []
    excluded = []

    try:
        if measure == "OR":
            cells = []
            for row in rows:
                a, b = row["events_t"], row["total_t"] - row["events_t"]
                c, d = row["events_c"], row["total_c"] - row["events_c"]
                n = a + b + c + d
                numerator, denominator = a * d / n, b * c / n
                if numerator == denominator == 0:
                    excluded.append({
                        "study_id": row["study_id"],
                        "reason": "zero MH numerator and denominator contributions",
                    })
                    continue
                included.append({"study_id": row["study_id"]})
                cells.append((a, b, c, d, n))

            r = math.fsum(a * d / n for a, b, c, d, n in cells)
            s = math.fsum(b * c / n for a, b, c, d, n in cells)
            if r <= 0 or s <= 0:
                raise ValueError("pooled OR is not finite and identifiable")
            e = math.fsum((a + d) * a * d / n**2 for a, b, c, d, n in cells)
            f = math.fsum((a + d) * b * c / n**2 for a, b, c, d, n in cells)
            g = math.fsum((b + c) * a * d / n**2 for a, b, c, d, n in cells)
            h = math.fsum((b + c) * b * c / n**2 for a, b, c, d, n in cells)
            variance = 0.5 * (e / r**2 + (f + g) / (r * s) + h / s**2)
            log_pooled = math.log(r) - math.log(s)
            if variance <= 0:
                raise ValueError("pooled OR variance is zero; its confidence interval is not identifiable")
            se = math.sqrt(_finite(variance, "OR variance"))
            pooled = math.exp(log_pooled)
            ci_low = math.exp(log_pooled - _Z_975 * se)
            ci_high = math.exp(log_pooled + _Z_975 * se)
            se_scale = "log"

        elif measure == "RR":
            cells = []
            for row in rows:
                a, b = row["events_t"], row["total_t"] - row["events_t"]
                c, d = row["events_c"], row["total_c"] - row["events_c"]
                n1, n2, n = a + b, c + d, a + b + c + d
                if a == 0 and c == 0:
                    excluded.append({
                        "study_id": row["study_id"],
                        "reason": "zero events in both arms contribute no RR numerator or denominator",
                    })
                    continue
                included.append({"study_id": row["study_id"]})
                cells.append((a, b, c, d, n1, n2, n))

            r = math.fsum(a * n2 / n for a, b, c, d, n1, n2, n in cells)
            s = math.fsum(c * n1 / n for a, b, c, d, n1, n2, n in cells)
            if r <= 0 or s <= 0:
                raise ValueError("pooled RR is not finite and identifiable")
            # Algebraically equivalent to [n1*n2*(a+c) - a*c*N] / N**2,
            # but this form avoids cancellation and stays non-negative.
            p = math.fsum((a * n1 * d + c * n2 * b) / n**2
                          for a, b, c, d, n1, n2, n in cells)
            variance = p / (r * s)
            log_pooled = math.log(r) - math.log(s)
            if variance <= 0:
                raise ValueError("pooled RR variance is zero; its confidence interval is not identifiable")
            se = math.sqrt(_finite(variance, "RR variance"))
            pooled = math.exp(log_pooled)
            ci_low = math.exp(log_pooled - _Z_975 * se)
            ci_high = math.exp(log_pooled + _Z_975 * se)
            se_scale = "log"

        else:
            cells = []
            for row in rows:
                a, b = row["events_t"], row["total_t"] - row["events_t"]
                c, d = row["events_c"], row["total_c"] - row["events_c"]
                n1, n2, n = a + b, c + d, a + b + c + d
                included.append({"study_id": row["study_id"]})
                cells.append((a, b, c, d, n1, n2, n))

            k = math.fsum(n1 * n2 / n for a, b, c, d, n1, n2, n in cells)
            numerator = math.fsum((a * n2 - c * n1) / n
                                  for a, b, c, d, n1, n2, n in cells)
            j = math.fsum((a * b * n2**3 + c * d * n1**3) / (n1 * n2 * n**2)
                          for a, b, c, d, n1, n2, n in cells)
            if k <= 0:
                raise ValueError("pooled RD is not identifiable")
            pooled = numerator / k
            variance = j / k**2
            if variance <= 0:
                raise ValueError("pooled RD variance is zero; its confidence interval is not identifiable")
            se = math.sqrt(_finite(variance, "RD variance"))
            ci_low, ci_high = pooled - _Z_975 * se, pooled + _Z_975 * se
            se_scale = "natural"

    except (OverflowError, ZeroDivisionError) as exc:
        raise ValueError("MH estimate or confidence interval is not representable") from exc

    for label, value in (("pooled estimate", pooled), ("standard error", se),
                         ("lower confidence limit", ci_low), ("upper confidence limit", ci_high)):
        _finite(value, label)

    return {
        "pooled": pooled,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "se": se,
        "se_scale": se_scale,
        "measure": measure,
        "n_studies": len(included),
        "included_studies": included,
        "excluded_studies": excluded,
        "method": "Mantel-Haenszel fixed-effect (Cochrane RevMan 5 equations)",
        "warnings": [
            "No continuity correction is applied. RevMan documents adding 0.5 to all four cells when empty cells cause individual estimates or standard errors to fail; this implementation keeps raw counts and records strata with no MH contribution as excluded."
        ],
    }
