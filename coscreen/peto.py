"""Fixed-effect Peto odds-ratio pooling for independent binary-arm studies."""

from math import exp, sqrt
from statistics import NormalDist


def pool_peto(studies: list[dict]) -> dict:
    """Pool binary 2x2 studies with Peto's O-E and hypergeometric variance."""
    if not isinstance(studies, list) or not studies:
        raise ValueError("studies must be a non-empty list")

    included, excluded, statistics = [], [], []
    sum_oe = sum_variance = 0.0
    required = ("study_id", "events_t", "total_t", "events_c", "total_c")

    for index, study in enumerate(studies):
        if not isinstance(study, dict):
            raise ValueError(f"study at index {index} must be a dict")
        missing = [key for key in required if key not in study]
        if missing:
            raise ValueError(f"study at index {index} is missing: {', '.join(missing)}")
        study_id = study["study_id"]
        if not isinstance(study_id, str) or not study_id.strip():
            raise ValueError(f"study at index {index} must have a non-empty study_id")

        counts = {key: study[key] for key in required[1:]}
        if any(type(value) is not int or value < 0 for value in counts.values()):
            raise ValueError(f"study {study_id!r} counts must be non-negative integers")
        a, n1 = counts["events_t"], counts["total_t"]
        c, n2 = counts["events_c"], counts["total_c"]
        if n1 == 0 or n2 == 0 or a > n1 or c > n2:
            raise ValueError(f"study {study_id!r} requires positive totals and events no greater than totals")

        b, d = n1 - a, n2 - c
        total = n1 + n2
        oe = a - n1 * (a + c) / total
        variance = n1 * n2 * (a + c) * (b + d) / (total**2 * (total - 1))

        reason = None
        if variance == 0:
            reason = (
                "zero variance (no events in either arm)"
                if a + c == 0
                else "zero variance (events in all participants)"
            )
            excluded_row = {
                "study_id": study_id,
                "reason": reason,
                "observed_minus_expected": oe,
                "variance": variance,
            }
            excluded.append(excluded_row)
        else:
            row = {"study_id": study_id, "observed_minus_expected": oe, "variance": variance}
            included.append(row)
            sum_oe += oe
            sum_variance += variance

        statistics.append({
            "study_id": study_id,
            "observed_minus_expected": oe,
            "variance": variance,
            "included": reason is None,
            "exclusion_reason": reason,
        })

    if sum_variance == 0:
        raise ValueError("all studies have zero information (zero hypergeometric variance)")

    log_pooled = sum_oe / sum_variance
    se = 1 / sqrt(sum_variance)
    margin = NormalDist().inv_cdf(0.975) * se
    return {
        "pooled": exp(log_pooled),
        "ci_low": exp(log_pooled - margin),
        "ci_high": exp(log_pooled + margin),
        "se": se,
        "measure": "OR",
        "n_studies": len(included),
        "included_studies": included,
        "excluded_studies": excluded,
        "study_statistics": statistics,
        "method": "Peto fixed-effect odds ratio",
        "warnings": [
            "Peto estimates odds ratios only.",
            "Peto can be biased with large treatment effects or substantial arm-size imbalance; interpret cautiously.",
        ],
    }
