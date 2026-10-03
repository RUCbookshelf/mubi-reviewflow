"""Exclusion sensitivity analyses using recorded overall risk-of-bias judgements."""

from __future__ import annotations

from pathlib import Path

from coscreen import review_analysis as ra

DbPath = str | Path


def analyze(db: DbPath, comparison: str, outcome: str, timepoint: str, measure: str,
            framework: str, reviewer: str, exclude_judgements: list[str] | set[str],
            model: str = "fixed", ci_method: str = "normal") -> dict:
    """Compare all selected effects with an analysis excluding specified RoB labels.

    Assessments must identify the exact synthesis stratum and selected effect source.
    The current schema cannot link an assessment to one of multiple saved effect variants.
    """
    if framework not in {"RoB 2", "ROBINS-I"}:
        raise ValueError("overall-judgement sensitivity supports RoB 2 and ROBINS-I")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError("reviewer must be specified")
    if isinstance(exclude_judgements, (str, bytes)):
        raise ValueError("exclude_judgements must be a non-empty collection of labels")
    excluded_labels = set(exclude_judgements)
    allowed_judgements = ra.ROB[framework][1]
    if not excluded_labels or not excluded_labels <= allowed_judgements:
        raise ValueError("exclude_judgements must contain valid labels for the selected framework")

    effects = ra.selected_effects(db, comparison, outcome, timepoint, measure)
    all_analysis = ra.synthesize(db, comparison, outcome, timepoint, measure, model, ci_method)
    effect_ids = {effect["study_id"] for effect in effects}

    variants = [effect for effect in ra.list_effects(db)
                if effect["comparison"] == comparison and effect["outcome"] == outcome
                and effect["timepoint"] == timepoint]
    by_study: dict[str, list[dict]] = {}
    for effect in variants:
        by_study.setdefault(effect["study_id"], []).append(effect)
    # ponytail: require one saved variant per study stratum; add result_id to review_rob to support variants.
    ambiguous = sorted(study_id for study_id in effect_ids if len(by_study.get(study_id, [])) != 1)
    if ambiguous:
        raise ValueError("risk-of-bias assessments cannot be linked to selected effects with saved variants: "
                         + ", ".join(ambiguous))

    assessments = [row for row in ra.list_rob(db)
                   if row["comparison"] == comparison and row["outcome"] == outcome
                   and row["timepoint"] == timepoint and row["framework"] == framework
                   and row["reviewer"] == reviewer.strip()]
    rob_by_study = {row["study_id"]: row for row in assessments}
    source_rows = {}
    for effect in effects:
        study_id = effect["study_id"]
        assessment = rob_by_study.get(study_id)
        if assessment is None:
            raise ValueError(f"missing matching risk-of-bias assessment for study {study_id}")
        if assessment["source_key"] != effect["source_key"]:
            raise ValueError(f"risk-of-bias source does not match selected effect source for study {study_id}")
        source_rows[study_id] = {
            "effect_source_key": effect["source_key"],
            "effect_source_locator": effect["source_locator"],
            "risk_of_bias_source_key": assessment["source_key"],
            "risk_of_bias_source_locator": assessment["source_locator"],
            "framework": framework,
            "reviewer": reviewer.strip(),
            "overall_judgement": assessment["overall"],
            "overall_rationale": assessment["overall_rationale"],
        }

    retained = [effect for effect in effects
                if source_rows[effect["study_id"]]["overall_judgement"] not in excluded_labels]
    if len(retained) < 2:
        raise ValueError("at least two studies must remain after excluding the selected judgements")
    sensitivity = ra._synthesize_rows(retained, measure, model, ci_method)

    def with_provenance(result: dict, rows: list[dict], excluded: list[str]) -> dict:
        result["included_study_ids"] = [row["study_id"] for row in rows]
        result["excluded_study_ids"] = excluded
        result["study_sources"] = {row["study_id"]: source_rows[row["study_id"]] for row in rows}
        result["excluded_sources"] = {study_id: source_rows[study_id] for study_id in excluded}
        return result

    excluded_ids = sorted(effect_ids - {effect["study_id"] for effect in retained})
    return {
        "framework": framework,
        "reviewer": reviewer.strip(),
        "excluded_judgements": sorted(excluded_labels),
        "all_studies": with_provenance(all_analysis, effects, []),
        "exclusion_sensitivity": with_provenance(sensitivity, retained, excluded_ids),
    }
