"""Two-stage IPD reduction: study-stratified arm summaries, no raw row persistence."""

from __future__ import annotations

import math
from pathlib import Path
from statistics import mean, stdev

from coscreen.review_analysis import binary_effect, continuous_effect, save_effect


def import_study(db: str | Path, study_id: str, comparison: str, outcome: str,
                 timepoint: str, measure: str, source_key: str,
                 participants: list[dict], *, effect_direction: str) -> dict:
    if not 4 <= len(participants) <= 100000:
        raise ValueError("IPD import needs 4–100000 participant rows")
    arms: dict[str, list[float]] = {"treatment": [], "control": []}
    for row in participants:
        if set(row) != {"arm", "value"} or row["arm"] not in arms:
            raise ValueError("each IPD row needs arm (treatment/control) and value")
        value = row["value"]
        if type(value) not in {int, float} or not math.isfinite(value):
            raise ValueError("participant outcomes must be finite numbers")
        arms[row["arm"]].append(value)
    treatment, control = arms["treatment"], arms["control"]
    if min(len(treatment), len(control)) < 2:
        raise ValueError("each study arm needs at least two participants")
    if measure in {"RR", "OR", "RD"}:
        if any(value not in (0, 1) for value in treatment + control):
            raise ValueError("binary participant outcomes must be 0 or 1")
        summary = {"events_t": int(sum(treatment)), "total_t": len(treatment),
                   "events_c": int(sum(control)), "total_c": len(control)}
        effect = binary_effect(**summary, measure=measure)
    elif measure in {"MD", "SMD"}:
        summary = {"n_t": len(treatment), "mean_t": mean(treatment), "sd_t": stdev(treatment),
                   "n_c": len(control), "mean_c": mean(control), "sd_c": stdev(control)}
        effect = continuous_effect(**summary, measure=measure)
    else:
        raise ValueError("unsupported IPD effect measure")
    save_effect(db, study_id, comparison, outcome, timepoint, measure,
                effect["estimate"], effect["se"], source_key,
                input_data={**summary, "effect_direction": effect_direction}, entry_method="ipd_two_stage")
    return {"study_id": study_id, "measure": measure, "summary": summary,
            "estimate": effect["estimate"], "se": effect["se"],
            "method": "within-study arm reduction for two-stage IPD meta-analysis",
            "warning": "Raw participant rows are not retained. This supports unadjusted independent two-arm outcomes only."}
