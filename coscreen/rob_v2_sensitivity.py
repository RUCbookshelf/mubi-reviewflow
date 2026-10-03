"""Result-version-linked RoB exclusion sensitivity over the legacy pooler."""

from __future__ import annotations

import json
import hashlib
from contextlib import closing
from pathlib import Path

from coscreen import __version__, db, review_analysis
from coscreen.analysis_storage import (
    attach_method_sources,
    save_analysis_run,
    synthesis_method_source_ids,
)
from coscreen.rob_storage import list_records
from coscreen.risk_of_bias_contract import (
    RobContractError,
    build_chart_data,
    from_legacy_row,
    get_mapping,
)

DbPath = str | Path
_MAX_SQLITE_ID = 2**63 - 1


class RobV2SensitivityError(ValueError):
    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code = code
        self.details = details


_RECONCILIATION_POLICY = {
    "missing_assessment": "error",
    "ambiguous_assessment": "error",
    "pending_reconciliation": "error",
    "not_recorded_target": "error",
}


def _positive_id(value, field: str) -> int:
    if isinstance(value, bool) or not (
        isinstance(value, int) and 0 < value <= _MAX_SQLITE_ID or
        isinstance(value, str) and value.strip().isdecimal() and len(value.strip()) <= 19
        and 0 < int(value.strip()) <= _MAX_SQLITE_ID
    ):
        raise RobV2SensitivityError("invalid_result_ref", f"{field} must be a positive integer", field=field)
    return int(value)


def _normalise_refs(result_refs) -> list[dict]:
    if not isinstance(result_refs, list) or not 1 <= len(result_refs) <= 500:
        raise RobV2SensitivityError("result_selection_required",
                                    "Select 1 to 500 exact result and effect-version IDs")
    refs = []
    seen = set()
    for item in result_refs:
        if not isinstance(item, dict):
            raise RobV2SensitivityError("invalid_result_ref", "Each result ref must be an object")
        ref = {
            "result_id": _positive_id(item.get("result_id"), "result_id"),
            "effect_version_id": _positive_id(item.get("effect_version_id"), "effect_version_id"),
        }
        key = (ref["result_id"], ref["effect_version_id"])
        if key in seen:
            raise RobV2SensitivityError("duplicate_result_ref", "Result refs must be unique", result_ref=ref)
        seen.add(key)
        refs.append(ref)
    return refs


def _legacy_records(conn) -> list[dict]:
    fields = ("study_id", "comparison", "outcome", "timepoint", "framework", "reviewer",
              "domains_json", "overall", "overall_rationale", "source_key", "source_locator", "updated_at")
    rows = conn.execute(
        "SELECT " + ",".join(fields) + " FROM review_rob "
        "ORDER BY study_id,comparison,outcome,timepoint,framework,reviewer"
    ).fetchall()
    return [from_legacy_row(dict(zip(fields, row))) for row in rows]


def _load_effect(conn, ref: dict) -> dict | None:
    fields = ("effect_version_id", "revision", "result_id", "study_id", "comparison", "outcome",
              "timepoint", "measure", "estimate", "se", "source_key", "input_json", "entry_method",
              "selected", "source_locator", "changed_at")
    row = conn.execute(
        "SELECT " + ",".join(fields) + " FROM review_effect_versions "
        "WHERE result_id=? AND effect_version_id=?",
        (ref["result_id"], ref["effect_version_id"]),
    ).fetchone()
    if row is None:
        return None
    effect = dict(zip(fields, row))
    try:
        effect["input_data"] = json.loads(effect.pop("input_json"))
    except (TypeError, json.JSONDecodeError) as exc:
        raise RobV2SensitivityError("invalid_effect_snapshot", "Effect version has invalid input JSON",
                                    result_ref=ref) from exc
    return effect


def _mapping_and_target(framework: str, tool_version: str, variant: str,
                        judgement_target: str) -> tuple[dict, list[str]]:
    values = {"framework": framework, "tool_version": tool_version, "variant": variant}
    invalid = [field for field, value in values.items()
               if not isinstance(value, str) or not value.strip()]
    if invalid:
        raise RobV2SensitivityError("invalid_mapping_request", "Framework, tool version, and variant are required",
                                    fields=invalid)
    framework, tool_version, variant = (values[field].strip()
                                        for field in ("framework", "tool_version", "variant"))
    if not isinstance(judgement_target, str) or not judgement_target.strip():
        raise RobV2SensitivityError("invalid_judgement_target", "Choose overall or a registered domain")
    judgement_target = judgement_target.strip()
    try:
        mapping = get_mapping(framework, tool_version, variant)
    except RobContractError as exc:
        raise RobV2SensitivityError(exc.code, str(exc), **exc.details) from exc
    if judgement_target == "overall":
        if not mapping["overall_supported"]:
            raise RobV2SensitivityError(
                "overall_not_supported", "The selected mapping has no framework overall judgement",
                framework=framework, mapping_id=mapping["mapping_id"],
            )
        return mapping, mapping["judgements"]["overall"]
    domain = next((item for item in mapping["domains"] if item["key"] == judgement_target), None)
    if domain is None:
        raise RobV2SensitivityError("invalid_judgement_target", "Target must be overall or a registered domain",
                                    judgement_target=judgement_target,
                                    allowed_domains=[item["key"] for item in mapping["domains"]])
    if domain["type"] != "risk_of_bias":
        raise RobV2SensitivityError("invalid_judgement_target",
                                    "Applicability domains are not risk-of-bias exclusion targets",
                                    judgement_target=judgement_target, domain_type=domain["type"])
    return mapping, domain["judgements"]


def _validate_exclusions(exclude_judgements, allowed: list[str]) -> list[str]:
    if isinstance(exclude_judgements, (str, bytes)) or not isinstance(exclude_judgements, (list, tuple, set)):
        raise RobV2SensitivityError("invalid_excluded_judgements",
                                    "Choose one or more labels from the selected judgement target")
    labels = list(exclude_judgements)
    if not labels or any(not isinstance(label, str) or not label for label in labels):
        raise RobV2SensitivityError("invalid_excluded_judgements",
                                    "Choose one or more labels from the selected judgement target")
    if len(labels) != len(set(labels)):
        raise RobV2SensitivityError("duplicate_excluded_judgement", "Excluded labels must be unique")
    invalid = sorted(set(labels) - set(allowed))
    if invalid:
        raise RobV2SensitivityError("invalid_excluded_judgement", "Excluded label is not valid for this target",
                                    invalid=invalid, allowed=allowed)
    return sorted(labels)


def _normalise_selector(selector) -> dict:
    if not isinstance(selector, dict):
        raise RobV2SensitivityError("invalid_assessor_selector", "Choose one reviewer or consensus set")
    kind = selector.get("kind")
    identity = {"reviewer": "reviewer_id", "consensus": "consensus_set_id"}.get(kind)
    if identity is None or set(selector) != {"kind", identity}:
        raise RobV2SensitivityError("invalid_assessor_selector",
                                    "Choose exactly one reviewer or consensus-set identity")
    value = selector.get(identity)
    if not isinstance(value, str) or not value.strip():
        raise RobV2SensitivityError("invalid_assessor_selector", f"{identity} must be a non-empty string")
    return {"kind": kind, identity: value.strip()}


def analyze(db_path: DbPath, *, result_refs: list[dict], framework: str, tool_version: str,
            variant: str, selector: dict, judgement_target: str,
            exclude_judgements: list[str] | set[str] | tuple[str, ...],
            model: str, ci_method: str) -> dict:
    """Analyze exact effect versions with one matching v2 assessment per study.

    Missing, ambiguous, unmapped, stale, and legacy assessments fail closed with
    structured ``RobV2SensitivityError`` codes. This pooler treats one study as
    one independent unit and refuses multiple selected effects from that study.
    """
    refs = _normalise_refs(result_refs)
    mapping, allowed = _mapping_and_target(framework, tool_version, variant, judgement_target)
    framework, tool_version, variant = framework.strip(), tool_version.strip(), variant.strip()
    judgement_target = judgement_target.strip()
    exclusions = _validate_exclusions(exclude_judgements, allowed)
    assessor = _normalise_selector(selector)
    if not isinstance(model, str) or model not in {"fixed", "random", "random_pm", "random_reml"} or \
       not isinstance(ci_method, str) or ci_method not in {"normal", "hksj"} or \
       (model == "fixed" and ci_method == "hksj"):
        raise RobV2SensitivityError("unsupported_synthesis_options", "Unsupported model or interval method",
                                    model=model, ci_method=ci_method)

    with closing(db._connect(db_path)) as conn, conn:
        # Old databases may predate the source locator added to review_rob.
        review_analysis._ensure_rob_locator(conn)
        conn.execute("BEGIN IMMEDIATE")
        effects = [_load_effect(conn, ref) for ref in refs]
        missing_effects = [ref for ref, effect in zip(refs, effects) if effect is None]
        if missing_effects:
            raise RobV2SensitivityError("effect_version_unavailable", "A selected result version is unavailable",
                                        missing_result_refs=missing_effects)
        effects = [effect for effect in effects if effect is not None]
        strata = {(effect["comparison"], effect["outcome"], effect["timepoint"]) for effect in effects}
        if len(strata) != 1:
            raise RobV2SensitivityError("mixed_result_strata",
                                        "Selected result versions must share comparison, outcome, and time point")
        measures = {effect["measure"] for effect in effects}
        if len(measures) != 1:
            raise RobV2SensitivityError("mixed_measure", "Selected result versions must use one effect measure",
                                        measures=sorted(measures))
        measure = next(iter(measures))
        if measure not in review_analysis.MEASURES:
            raise RobV2SensitivityError("unsupported_measure", "Stored effect measure is unsupported", measure=measure)
        by_study: dict[str, list[dict]] = {}
        for effect in effects:
            by_study.setdefault(effect["study_id"], []).append(effect)
        # ponytail: one effect per study; use dependent synthesis when clustered effects are needed.
        multiple = sorted(study_id for study_id, rows in by_study.items() if len(rows) > 1)
        if multiple:
            raise RobV2SensitivityError("multiple_effects_per_study",
                                        "The legacy pooling kernel requires one selected effect per study",
                                        study_ids=multiple)
        placeholders = ",".join("?" for _ in effects)
        designs = conn.execute(
            "SELECT cluster_id,sample_id FROM review_effect_design WHERE result_id IN (" +
            placeholders + ")", tuple(effect["result_id"] for effect in effects)).fetchall()
        if any(len({row[column] for row in designs}) != len(designs) for column in (0, 1)):
            raise RobV2SensitivityError("dependent_effects_unsupported",
                                        "Selected effects share a recorded cluster or sample")
        if len(effects) < 2:
            raise RobV2SensitivityError("too_few_studies", "At least two independent studies are required",
                                        n_studies=len(effects))

        records = list_records(conn) + _legacy_records(conn)
        try:
            chart = build_chart_data(
                records, effects, framework=framework, tool_version=tool_version, variant=variant,
                selector=assessor, result_refs=refs, statistical_unit="assessment_record",
            )
        except RobContractError as exc:
            raise RobV2SensitivityError(exc.code, str(exc), **exc.details) from exc

        by_ref: dict[tuple[int, int], list[dict]] = {}
        for row in chart["rows"]:
            for link in row["result_links"]:
                key = (int(link["result_id"]), int(link["effect_version_id"]))
                by_ref.setdefault(key, []).append(row)
        missing_assessments, ambiguous_assessments = [], []
        for ref in refs:
            key = (ref["result_id"], ref["effect_version_id"])
            matches = by_ref.get(key, [])
            if not matches:
                missing_assessments.append(ref)
            elif len(matches) > 1:
                ambiguous_assessments.append({"result_ref": ref,
                                              "record_ids": [row["record_id"] for row in matches]})
        pending = chart["pending_reconciliation"]
        if pending or missing_assessments or ambiguous_assessments:
            code = "pending_reconciliation" if pending else \
                "assessment_ambiguous" if ambiguous_assessments else "assessment_missing"
            raise RobV2SensitivityError(
                code, "RoB records need reconciliation before this sensitivity analysis can run",
                pending=pending, missing_result_refs=missing_assessments,
                ambiguous=ambiguous_assessments, policy=_RECONCILIATION_POLICY,
            )

        record_by_id = {record["record_id"]: record for record in records}
        assessment_by_ref = {}
        for ref, effect in zip(refs, effects):
            key = (ref["result_id"], ref["effect_version_id"])
            chart_row = by_ref[key][0]
            record = record_by_id[chart_row["record_id"]]
            cell = chart_row["overall"] if judgement_target == "overall" else \
                chart_row["domains"][judgement_target]
            if cell is None or cell["status"] == "not_recorded":
                raise RobV2SensitivityError("judgement_not_recorded",
                                            "Target judgement is not recorded for an exact result version",
                                            result_ref=ref, record_id=chart_row["record_id"],
                                            judgement_target=judgement_target,
                                            policy=_RECONCILIATION_POLICY["not_recorded_target"])
            judgement = cell["judgement"]
            assessment_by_ref[key] = {
                "record_id": chart_row["record_id"], "revision": chart_row["revision"],
                "result_ref": ref,
                "study_id": effect["study_id"], "reviewer_id": chart_row["reviewer_id"],
                "consensus_set_id": chart_row["consensus_set_id"], "source": chart_row["source"],
                "effect_source": {"report_id": effect["source_key"],
                                  "locator": effect["source_locator"]},
                "framework": framework, "tool_version": tool_version, "variant": variant,
                "mapping_id": record["mapping_id"], "judgement_target": judgement_target,
                "judgement": judgement, "rationale": cell["rationale"],
                "excluded": judgement in exclusions,
            }
        excluded_refs = {key for key, item in assessment_by_ref.items() if item["excluded"]}
        retained = [effect for effect, ref in zip(effects, refs)
                    if (ref["result_id"], ref["effect_version_id"]) not in excluded_refs]
        if len(retained) < 2:
            raise RobV2SensitivityError("too_few_studies_after_exclusion",
                                        "At least two studies must remain after excluding the selected judgements",
                                        included_study_ids=[row["study_id"] for row in retained],
                                        excluded_study_ids=[effect["study_id"] for effect, ref in zip(effects, refs)
                                                            if (ref["result_id"], ref["effect_version_id"])
                                                            in excluded_refs])

        try:
            all_result = review_analysis._synthesize_rows(effects, measure, model, ci_method)
            excluded_result = review_analysis._synthesize_rows(retained, measure, model, ci_method)
        except (KeyError, ValueError) as exc:
            raise RobV2SensitivityError("synthesis_failed", str(exc), model=model,
                                        ci_method=ci_method, measure=measure) from exc

        all_ids = [effect["study_id"] for effect in effects]
        retained_ids = [effect["study_id"] for effect in retained]
        retained_id_set = set(retained_ids)
        excluded_ids = [study for study in all_ids if study not in retained_id_set]
        assessment_by_study = {item["study_id"]: item for item in assessment_by_ref.values()}

        def add_provenance(result: dict, study_ids: list[str], excluded_ids: list[str]) -> dict:
            provenance_ids = [*study_ids, *excluded_ids]
            return {**result, "n_effects": len(study_ids), "n_clusters": len(study_ids),
                    "included_study_ids": study_ids, "excluded_study_ids": excluded_ids,
                    "assessment_sources": {study_id: assessment_by_study[study_id]
                                            for study_id in provenance_ids}}

        result = {
            "framework": framework, "tool_version": tool_version, "variant": variant,
            "mapping_id": mapping["mapping_id"], "selector": assessor,
            "judgement_target": judgement_target, "excluded_judgements": exclusions,
            "n_effects": len(effects), "n_studies": len(by_study), "n_clusters": len(by_study),
            "reconciliation": {"status": "complete", "policy": _RECONCILIATION_POLICY,
                               "matched_result_refs": refs, "pending": []},
            "all_studies": add_provenance(all_result, all_ids, []),
            "exclusion_sensitivity": add_provenance(excluded_result, retained_ids, excluded_ids),
        }
        used_records = [record_by_id[item["record_id"]] for item in assessment_by_ref.values()]
        specification = {
            "analysis_type": "rob_v2_exclusion_sensitivity", "algorithm_version": "legacy_inverse_variance_v1",
            "result_refs": refs, "framework": framework, "tool_version": tool_version,
            "variant": variant, "mapping_id": mapping["mapping_id"], "selector": assessor,
            "judgement_target": judgement_target, "exclude_judgements": exclusions,
            "measure": measure, "model": model, "ci_method": ci_method,
            "result_selection": "explicit_result_effect_version_refs",
            "cluster_assumption": "one independent study per effect",
            "reconciliation_policy": _RECONCILIATION_POLICY, "random_seed": None,
        }
        source_articles = {}
        for key in sorted({effect["source_key"] for effect in effects}):
            row = conn.execute(
                "SELECT zotero_key,title,authors,journal,year,doi,url FROM articles WHERE zotero_key=?",
                (key,)).fetchone()
            if row is None:
                raise RobV2SensitivityError("source_report_missing",
                                            "A selected effect source report is unavailable", source_key=key)
            source_articles[key] = dict(zip(
                ("zotero_key", "title", "authors", "journal", "year", "doi", "url"), row))
        snapshot = {
            "effects": effects, "assessments": used_records,
            "source_articles": source_articles,
            "n_effects": len(effects), "n_studies": len(by_study), "n_clusters": len(by_study),
            "reconciliation": result["reconciliation"],
        }
        framework_source = {"RoB 2": "cochrane_rob_ch8", "ROBINS-I": "robins_i_2016",
                            "QUADAS-2": "quadas_2_2011"}[framework]
        attach_method_sources(result, snapshot, [
            *synthesis_method_source_ids(model, ci_method),
            "cochrane_rob_ch7", framework_source, "cochrane_rob_sensitivity",
        ])
        snapshot["data_sha256"] = hashlib.sha256(json.dumps(
            snapshot, ensure_ascii=False, allow_nan=False, sort_keys=True).encode("utf-8")).hexdigest()
        run_id = save_analysis_run(conn, specification, snapshot, result, __version__)
        return {"run_id": run_id, "specification": specification,
                "input_snapshot": snapshot, "result": result}
