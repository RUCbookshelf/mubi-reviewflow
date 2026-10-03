"""Versioned manual RoB records and chart-data contract.

This module stores and maps human-entered judgments only. It does not contain
official signalling questions, decision rules, or automatic overall ratings.
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from collections import Counter
from datetime import datetime, timezone

CONTRACT_VERSION = "reviewflow.rob-record/1"
MAPPING_VERSION = "reviewflow.rob-mapping/1"


class RobContractError(ValueError):
    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code = code
        self.details = details


def _domain(key: str, kind: str, column: str, judgements: tuple[str, ...], label: str | None = None) -> dict:
    return {"key": key, "label": label or key, "type": kind, "column": column,
            "judgements": list(judgements)}


_ROB2_DOMAINS = (
    "randomization process", "deviations from intended interventions", "missing outcome data",
    "measurement of outcome", "selection of reported result",
)
_ROBINS_DOMAINS = (
    "confounding", "selection of participants", "classification of interventions",
    "deviations from intended interventions", "missing data", "measurement of outcomes",
    "selection of reported result",
)
_QUADAS_RISK = ("patient selection", "index test", "reference standard", "flow and timing")
_QUADAS_APPLICABILITY = (
    "applicability: patient selection", "applicability: index test",
    "applicability: reference standard",
)


def _mapping(framework: str, version: str, variant: str, domains: tuple[dict, ...],
             judgements: tuple[str, ...], overall_supported: bool, reference_url: str) -> dict:
    return {"framework": framework, "tool_version": version, "variant": variant,
            "mapping_id": f"{MAPPING_VERSION}:{framework.lower().replace('-', '').replace(' ', '')}:{version}:{variant}",
            "mapping_version": MAPPING_VERSION, "domains": list(domains),
            "judgements": {"domain": list(judgements),
                           "overall": list(judgements) if overall_supported else []},
            "overall_supported": overall_supported, "reference_url": reference_url}


_ROB2_REFERENCE = "https://www.riskofbias.info/welcome/rob-2-0-tool"
_ROBINS_I_REFERENCE = "https://www.riskofbias.info/welcome/home/original-2016-version-of-robins-i"
_QUADAS2_REFERENCE = "https://www.bristol.ac.uk/population-health-sciences/projects/quadas/history/quadas-2/"
_ROB2_JUDGEMENTS = ("low", "some concerns", "high")
_ROBINS_JUDGEMENTS = ("low", "moderate", "serious", "critical", "no information")
_QUADAS_JUDGEMENTS = ("low", "high", "unclear")

_MAPPINGS = {}
for _variant in ("individually_randomized_parallel_group_assignment_effect",
                 "individually_randomized_parallel_group_starting_and_adhering_effect"):
    _MAPPINGS[("RoB 2", "2019-08-22", _variant)] = _mapping(
        "RoB 2", "2019-08-22", _variant,
        tuple(_domain(key, "risk_of_bias", f"D{i}", _ROB2_JUDGEMENTS)
              for i, key in enumerate(_ROB2_DOMAINS, 1)),
        _ROB2_JUDGEMENTS, True, _ROB2_REFERENCE)
_MAPPINGS[("ROBINS-I", "2016", "not_applicable")] = _mapping(
    "ROBINS-I", "2016", "not_applicable",
    tuple(_domain(key, "risk_of_bias", f"D{i}", _ROBINS_JUDGEMENTS)
          for i, key in enumerate(_ROBINS_DOMAINS, 1)),
    _ROBINS_JUDGEMENTS, True, _ROBINS_I_REFERENCE)
_MAPPINGS[("QUADAS-2", "2011", "standard")] = _mapping(
    "QUADAS-2", "2011", "standard",
    tuple(_domain(key, "risk_of_bias", f"D{i}", _QUADAS_JUDGEMENTS)
          for i, key in enumerate(_QUADAS_RISK, 1)) +
    tuple(_domain(key, "applicability", f"A{i}", _QUADAS_JUDGEMENTS, key.split(": ", 1)[1])
          for i, key in enumerate(_QUADAS_APPLICABILITY, 1)),
    _QUADAS_JUDGEMENTS, False, _QUADAS2_REFERENCE)

_FRAMEWORKS = {"RoB 2", "ROBINS-I", "QUADAS-2"}
_TRAFFIC = {
    "low": ("low", "#2e7d32"),
    "some concerns": ("some_concerns", "#edb120"),
    "moderate": ("moderate", "#edb120"),
    "unclear": ("unclear", "#edb120"),
    "serious": ("serious", "#e87500"),
    "critical": ("critical", "#b2182b"),
    "high": ("high", "#b2182b"),
    "no information": ("no_information", "#777777"),
}
_ROBVIS_LABELS = {"low": "Low", "some concerns": "Some concerns", "high": "High",
                 "moderate": "Moderate", "serious": "Serious", "critical": "Critical",
                 "no information": "No information", "unclear": "Unclear"}


def mapping_status(framework: str, tool_version: str | None, variant: str | None) -> str:
    if framework not in _FRAMEWORKS:
        return "unsupported_framework"
    if not tool_version or tool_version == "unknown":
        return "unknown_tool_version"
    if not variant or variant == "unknown":
        return "unknown_variant"
    if (framework, tool_version, variant) in _MAPPINGS:
        return "supported"
    if any(key[0] == framework and key[1] == tool_version for key in _MAPPINGS):
        return "unsupported_variant"
    return "unsupported_tool_version"


def get_mapping(framework: str, tool_version: str | None, variant: str | None) -> dict:
    key = (framework, tool_version or "unknown", variant or "unknown")
    mapping = _MAPPINGS.get(key)
    if mapping is None:
        raise RobContractError("unsupported_mapping", "No registered manual mapping for this framework version and variant",
                               framework=framework, tool_version=key[1], variant=key[2],
                               mapping_status=mapping_status(*key))
    return copy.deepcopy(mapping)


def mapping_catalog() -> list[dict]:
    return [copy.deepcopy(_MAPPINGS[key]) for key in sorted(_MAPPINGS)]


def _json_copy(value, field: str):
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise RobContractError("invalid_json_value", f"{field} must contain finite JSON values", field=field) from exc


def _required_text(value, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise RobContractError("invalid_field", f"{field} must be a non-empty string", field=field)
    return value.strip() if not allow_empty else value.strip()


def _result_links(value) -> list[dict]:
    if not isinstance(value, list) or not value:
        raise RobContractError("result_link_required", "At least one exact result/version link is required")
    links, seen = [], set()
    for item in value:
        if not isinstance(item, dict):
            raise RobContractError("invalid_result_link", "Each result link must be an object")
        result_id = item.get("result_id")
        version_id = item.get("effect_version_id")
        if isinstance(result_id, bool) or not (isinstance(result_id, int) and result_id > 0 or
                                                isinstance(result_id, str) and result_id.strip()):
            raise RobContractError("invalid_result_link", "result_id must be a positive integer or non-empty string")
        version_id = _required_text(version_id, "effect_version_id")
        key = (str(result_id), version_id)
        if key in seen:
            raise RobContractError("duplicate_result_link", "Duplicate result/version link", result_id=result_id)
        seen.add(key)
        link = {"result_id": result_id, "effect_version_id": version_id}
        if item.get("analysis_run_id") is not None:
            link["analysis_run_id"] = _required_text(item["analysis_run_id"], "analysis_run_id")
        links.append(link)
    return links


def _normalize_overall(value, framework: str, mapping: dict | None):
    if value is None:
        return None
    if isinstance(value, str):
        value = {"judgement": value, "rationale": ""}
    if not isinstance(value, dict):
        raise RobContractError("invalid_overall", "overall must be an object or null")
    judgement = _required_text(value.get("judgement"), "overall.judgement")
    rationale = _required_text(value.get("rationale", ""), "overall.rationale")
    if framework == "QUADAS-2":
        return {"status": "recorded", "judgement": judgement, "rationale": rationale,
                "classification": "project_custom"}
    if mapping and mapping["overall_supported"] and judgement not in mapping["judgements"]["overall"]:
        raise RobContractError("invalid_judgement", "overall judgement is not valid for the selected framework",
                               framework=framework, judgement=judgement)
    return {"status": "recorded", "judgement": judgement, "rationale": rationale,
            "classification": "manual_framework_judgement" if mapping else "unmapped_manual_value"}


def _normalize_domains(value, mapping: dict | None):
    value = _json_copy(value, "domains")
    if not isinstance(value, dict):
        raise RobContractError("invalid_domains", "domains must be an object keyed by domain name")
    if mapping is None:
        return value
    allowed = {item["key"]: item for item in mapping["domains"]}
    extras = set(value) - set(allowed)
    if extras:
        raise RobContractError("invalid_domain", "Domain does not belong to the registered mapping",
                               domains=sorted(extras), mapping_id=mapping["mapping_id"])
    domains = {}
    for key, spec in allowed.items():
        item = value.get(key)
        if item is None:
            item = {"status": "not_recorded"}
        if not isinstance(item, dict):
            raise RobContractError("invalid_domain", "Each domain value must be an object", domain=key)
        status = item.get("status", "recorded" if item.get("judgement") is not None else "not_recorded")
        if status == "not_recorded":
            if item.get("judgement") not in (None, ""):
                raise RobContractError("invalid_domain_status", "not_recorded domains cannot carry a judgement", domain=key)
            judgement, rationale = None, ""
        elif status == "recorded":
            judgement = _required_text(item.get("judgement"), f"domains.{key}.judgement")
            if judgement.casefold() == "not applicable":
                raise RobContractError("unsupported_not_applicable",
                                       "This mapping has no not-applicable judgement; use its registered categories",
                                       domain=key, allowed=spec["judgements"])
            if judgement not in spec["judgements"]:
                raise RobContractError("invalid_judgement", "Judgement is not valid for the selected domain",
                                       domain=key, judgement=judgement, allowed=spec["judgements"])
            rationale = _required_text(item.get("rationale", ""), f"domains.{key}.rationale")
        else:
            raise RobContractError("invalid_domain_status", "status must be recorded or not_recorded",
                                   domain=key, status=status)
        domains[key] = {"type": spec["type"], "status": status,
                        "judgement": judgement, "rationale": rationale}
    return domains


def create_assessment(payload: dict, *, reviewer_id: str | None, actor_id: str | None = None,
                      assessed_at: str | None = None) -> dict:
    """Validate one manual assessment and issue its stable identity/revision 1."""
    if not isinstance(payload, dict):
        raise RobContractError("invalid_assessment", "Assessment must be an object")
    raw = _json_copy(payload, "assessment")
    framework = _required_text(raw.get("framework"), "framework")
    if framework not in _FRAMEWORKS:
        raise RobContractError("unsupported_framework", "Framework is not supported", framework=framework)
    version = _required_text(raw.get("tool_version") or "unknown", "tool_version")
    variant = _required_text(raw.get("variant") or "unknown", "variant")
    status = mapping_status(framework, version, variant)
    mapping = _MAPPINGS.get((framework, version, variant))
    study_id = _required_text(raw.get("study_id"), "study_id")
    comparison = _required_text(raw.get("comparison", ""), "comparison", allow_empty=True)
    outcome = _required_text(raw.get("outcome"), "outcome")
    timepoint = _required_text(raw.get("timepoint"), "timepoint")
    source = raw.get("source")
    if not isinstance(source, dict):
        raise RobContractError("invalid_source", "source must contain a report_id and locator")
    source = {"report_id": _required_text(source.get("report_id"), "source.report_id"),
              "locator": _required_text(source.get("locator", ""), "source.locator", allow_empty=True)}
    record_kind = raw.get("record_kind", "independent")
    if record_kind not in {"independent", "consensus"}:
        raise RobContractError("invalid_record_kind", "record_kind must be independent or consensus")
    if record_kind == "independent":
        rater = _required_text(reviewer_id, "authenticated reviewer")
        if raw.get("consensus_set_id"):
            raise RobContractError("invalid_record_kind", "Independent records cannot name a consensus set")
        consensus_set_id = None
    else:
        if reviewer_id:
            raise RobContractError("invalid_record_kind", "Consensus records cannot be attributed to one reviewer")
        rater = None
        consensus_set_id = _required_text(raw.get("consensus_set_id"), "consensus_set_id")
    actor = _required_text(actor_id or rater, "authenticated record creator")
    known = {"record_id", "revision", "record_kind", "reviewer_id", "consensus_set_id", "study_id",
             "comparison", "outcome", "timepoint", "framework", "tool_version", "variant",
             "mapping_version", "mapping_id", "mapping_status", "result_links", "source", "domains",
             "overall", "assessed_at", "created_at", "updated_at", "created_by", "updated_by",
             "contract_version"}
    # 未知键（扩展字段）按合同原样保留并随修订/导出往返——这是有意的扩展
    # 机制（见 test_export_round_trips_the_complete_record_without_dropping_extensions），
    # 不是缺陷；消费方必须对未知键做前向兼容解析。
    extras = {key: val for key, val in raw.items() if key not in known}
    record = {
        **extras,
        "contract_version": CONTRACT_VERSION,
        "record_id": str(uuid.uuid4()),
        "revision": 1,
        "record_kind": record_kind,
        "reviewer_id": rater,
        "consensus_set_id": consensus_set_id,
        "created_by": actor,
        "updated_by": actor,
        "study_id": study_id,
        "comparison": comparison,
        "outcome": outcome,
        "timepoint": timepoint,
        "framework": framework,
        "tool_version": version,
        "variant": variant,
        "mapping_version": MAPPING_VERSION if mapping else "unknown",
        "mapping_id": mapping["mapping_id"] if mapping else None,
        "mapping_status": status,
        "result_links": _result_links(raw.get("result_links")),
        "source": source,
        "domains": _normalize_domains(raw.get("domains", {}), mapping),
        "overall": _normalize_overall(raw.get("overall"), framework, mapping),
        "assessed_at": assessed_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    record["created_at"] = record["assessed_at"]
    record["updated_at"] = record["assessed_at"]
    return record


def revise_assessment(current: dict, changes: dict, *, expected_revision: int,
                      actor_id: str | None = None) -> dict:
    if expected_revision != current.get("revision"):
        raise RobContractError("revision_conflict", "Assessment has changed since it was read",
                               expected_revision=expected_revision, actual_revision=current.get("revision"))
    if not isinstance(changes, dict):
        raise RobContractError("invalid_revision", "Changes must be an object")
    immutable = {"record_id", "revision", "record_kind", "reviewer_id", "consensus_set_id",
                 "study_id", "created_by", "updated_by"}
    if immutable.intersection(changes):
        raise RobContractError("immutable_record_identity", "Record identity and assessor cannot change in a revision",
                               fields=sorted(immutable.intersection(changes)))
    merged = {**copy.deepcopy(current), **_json_copy(changes, "changes")}
    # The framework and mapping are fixed when a record is created; a different tool requires a new record.
    for field in ("framework", "tool_version", "variant", "mapping_version", "mapping_id"):
        if merged.get(field) != current.get(field):
            raise RobContractError("immutable_mapping", "Tool version or mapping changes require a new record", field=field)
    revised_at = merged.get("assessed_at") if "assessed_at" in changes else \
        datetime.now(timezone.utc).isoformat(timespec="seconds")
    new = create_assessment(merged, reviewer_id=current.get("reviewer_id"),
                            actor_id=actor_id or current.get("updated_by") or current.get("created_by"),
                            assessed_at=revised_at)
    new["record_id"] = current["record_id"]
    new["revision"] = current["revision"] + 1
    new["created_at"] = current.get("created_at", current["assessed_at"])
    new["updated_at"] = revised_at
    new["created_by"] = current.get("created_by", current.get("reviewer_id"))
    new["updated_by"] = _required_text(actor_id or current.get("reviewer_id") or current.get("created_by"),
                                       "authenticated record editor")
    return new


def from_legacy_row(row: dict) -> dict:
    """Preserve a pre-contract row while marking its unknown mapping/linkage."""
    raw = _json_copy(row, "legacy_row")
    identity = {key: raw.get(key, "") for key in
                ("study_id", "comparison", "outcome", "timepoint", "framework", "reviewer")}
    legacy_id = "legacy:" + hashlib.sha256(
        json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:24]
    framework = raw.get("framework", "unknown")
    old_overall = raw.get("overall")
    overall = None if not old_overall else {
        "status": "recorded", "judgement": old_overall,
        "rationale": raw.get("overall_rationale", ""),
        "classification": "legacy_project_custom" if framework == "QUADAS-2" else "legacy_origin_unknown",
    }
    domains = raw.get("domains", raw.get("domains_json", {}))
    if isinstance(domains, str):
        try:
            domains = json.loads(domains)
        except json.JSONDecodeError:
            domains = {"legacy_raw": domains}
    return {
        "contract_version": CONTRACT_VERSION,
        "record_id": legacy_id,
        "revision": None,
        "record_kind": "legacy",
        "reviewer_id": raw.get("reviewer"),
        "consensus_set_id": None,
        "study_id": raw.get("study_id"),
        "comparison": raw.get("comparison", ""),
        "outcome": raw.get("outcome"),
        "timepoint": raw.get("timepoint"),
        "framework": framework,
        "tool_version": "unknown",
        "variant": "unknown",
        "mapping_version": "unknown",
        "mapping_id": None,
        "mapping_status": "version_unknown",
        "result_links": [],
        "source": {"report_id": raw.get("source_key"), "locator": raw.get("source_locator", "")},
        "domains": domains,
        "overall": overall,
        "assessed_at": raw.get("updated_at"),
        "legacy_record": raw,
    }


def check_legacy_put_scope(existing_rows: list[dict], request: dict) -> dict:
    """Keep legacy exact-key upserts, rejecting a missing-comparison guess."""
    fields = ("study_id", "comparison", "outcome", "timepoint", "framework", "reviewer")
    key = tuple(request.get(field, "") for field in fields)
    exact = [row for row in existing_rows if tuple(row.get(field, "") for field in fields) == key]
    if len(exact) > 1:
        raise RobContractError("ambiguous_legacy_scope", "Legacy scope matches more than one record")
    if exact:
        return {"action": "update", "record_id": exact[0].get("record_id")}
    if not request.get("comparison"):
        broader = [row for row in existing_rows if all(
            row.get(field, "") == request.get(field, "")
            for field in ("study_id", "outcome", "timepoint", "framework", "reviewer"))]
        if broader:
            raise RobContractError("ambiguous_legacy_scope", "Legacy request omits comparison for result-specific records",
                                   candidate_count=len(broader))
    return {"action": "insert", "record_id": None}


def export_records(records: list[dict]) -> dict:
    clean = _json_copy(records, "records")
    return {"schema_version": CONTRACT_VERSION, "records": clean}


def _selector_key(selector: dict) -> tuple[str, str]:
    if not isinstance(selector, dict):
        raise RobContractError("selector_required", "Choose one reviewer or consensus set")
    kind = selector.get("kind")
    if kind == "reviewer":
        return (kind, _required_text(selector.get("reviewer_id"), "selector.reviewer_id"))
    if kind == "consensus":
        return (kind, _required_text(selector.get("consensus_set_id"), "selector.consensus_set_id"))
    raise RobContractError("selector_required", "Choose exactly one reviewer or consensus set")


def _ref_key(value: dict) -> tuple[str, str]:
    return (str(value.get("result_id")), str(value.get("effect_version_id")))


def _cell(item: dict | None, supported: bool) -> dict:
    if not item or item.get("status") == "not_recorded":
        return {"status": "not_recorded", "judgement": None, "rationale": "",
                "visual_category": "not_recorded", "color": "#b0b0b0"}
    judgement = item.get("judgement")
    if supported and judgement not in _TRAFFIC:
        raise RobContractError("invalid_judgement", "Chart data contains an unmapped judgement", judgement=judgement)
    category, color = _TRAFFIC.get(judgement, ("unmapped", "#777777"))
    return {"status": "recorded", "judgement": judgement, "rationale": item.get("rationale", ""),
            "visual_category": category, "color": color}


def build_chart_data(records: list[dict], effects: list[dict], *, framework: str,
                     tool_version: str, variant: str, selector: dict,
                     result_refs: list[dict], statistical_unit: str) -> dict:
    """Build equal-weight chart rows for one mapping, assessor, and result stratum."""
    mapping = get_mapping(framework, tool_version, variant)
    selector_kind, selector_id = _selector_key(selector)
    if statistical_unit not in {"assessment_record", "study"}:
        raise RobContractError("invalid_statistical_unit", "Choose assessment_record or study explicitly")
    if not isinstance(result_refs, list) or not result_refs:
        raise RobContractError("result_selection_required", "Select exact result/version IDs for charting")
    selected_refs = {_ref_key(item) for item in result_refs}
    ref_order = list(dict.fromkeys(_ref_key(item) for item in result_refs))
    if len(selected_refs) != len(result_refs):
        raise RobContractError("duplicate_result_link", "Selected result/version IDs must be unique")
    effects_by_ref = {}
    for item in effects:
        key = _ref_key(item)
        if key in effects_by_ref:
            raise RobContractError("duplicate_effect_version", "Effect result/version references must be unique",
                                   result_ref=key)
        effects_by_ref[key] = item
    missing = selected_refs - set(effects_by_ref)
    if missing:
        raise RobContractError("result_reference_missing", "A selected result/version is unavailable",
                               result_refs=sorted(missing))
    strata = {(item.get("comparison"), item.get("outcome"), item.get("timepoint"))
              for key, item in effects_by_ref.items() if key in selected_refs}
    if len(strata) != 1:
        raise RobContractError("mixed_result_strata", "Chart one comparison, outcome, and time point at a time")
    selected_effects = {key: effects_by_ref[key] for key in selected_refs}
    for effect in selected_effects.values():
        if not effect.get("study_id"):
            raise RobContractError("invalid_result_reference", "Selected effect is missing study_id")

    rows, pending, seen_record_ids = [], [], set()
    for record in records:
        record_id = record.get("record_id")
        if record_id in seen_record_ids:
            raise RobContractError("duplicate_record_revision", "Pass one explicit revision for each record_id",
                                   record_id=record_id)
        seen_record_ids.add(record_id)
        if selector_kind == "reviewer":
            selected_assessor = record.get("record_kind") in {"independent", "legacy"} and \
                record.get("reviewer_id") == selector_id
        else:
            selected_assessor = record.get("record_kind") == "consensus" and \
                record.get("consensus_set_id") == selector_id
        if not selected_assessor or record.get("framework") != framework:
            continue
        record_links = {_ref_key(link) for link in record.get("result_links", [])}
        linked = selected_refs.intersection(record_links)
        if not linked:
            selected_result_ids = {str(effects_by_ref[key].get("result_id")) for key in selected_refs}
            linked_result_ids = {key[0] for key in record_links}
            if selected_result_ids.intersection(linked_result_ids):
                pending.append({"record_id": record_id, "study_id": record.get("study_id"),
                                "reason": "result_version_mismatch"})
                continue
            if record.get("mapping_id") is None and record.get("study_id") in {
                    effect.get("study_id") for effect in selected_effects.values()} and \
                    record.get("outcome") in {None, "", next(iter(strata))[1]} and \
                    record.get("timepoint") in {None, "", next(iter(strata))[2]} and \
                    record.get("comparison") in {None, "", next(iter(strata))[0]}:
                pending.append({"record_id": record_id, "study_id": record.get("study_id"),
                                "reason": "legacy_result_unbound"})
            continue
        for key in linked:
            effect = selected_effects[key]
            if record.get("study_id") != effect.get("study_id"):
                raise RobContractError("result_link_study_mismatch", "Assessment and linked result belong to different studies",
                                       record_id=record_id, result_id=effect.get("result_id"))
        if record.get("mapping_status") != "supported" or record.get("mapping_id") != mapping["mapping_id"]:
            pending.append({"record_id": record_id, "study_id": record.get("study_id"),
                            "reason": record.get("mapping_status", "mapping_mismatch")})
            continue
        domains = record.get("domains", {})
        display_domains = {spec["key"]: _cell(domains.get(spec["key"]), True) for spec in mapping["domains"]}
        overall = _cell(record.get("overall"), True) if mapping["overall_supported"] else None
        rows.append({
            "record_id": record_id,
            "assessment_status": "recorded",
            "revision": record.get("revision"),
            "study_id": record.get("study_id"),
            "result_links": [{"result_id": selected_effects[key].get("result_id"),
                              "effect_version_id": selected_effects[key].get("effect_version_id"),
                              **{field: selected_effects[key][field] for field in
                                 ("comparison", "outcome", "timepoint") if field in selected_effects[key]}}
                             for key in ref_order if key in linked],
            "domains": display_domains,
            "overall": overall,
            "custom_overall": (copy.deepcopy(record.get("overall"))
                               if not mapping["overall_supported"] and record.get("overall") else None),
            "source": copy.deepcopy(record.get("source", {})),
            "reviewer_id": record.get("reviewer_id"),
            "consensus_set_id": record.get("consensus_set_id"),
            "created_by": record.get("created_by"),
            "updated_by": record.get("updated_by"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
        })
    covered_refs = {_ref_key(link) for row in rows for link in row["result_links"]}
    unassessed_refs = [key for key in ref_order if key not in covered_refs]
    if statistical_unit == "study":
        for key in unassessed_refs:
            effect = selected_effects[key]
            rows.append({
                "record_id": None, "revision": None, "assessment_status": "not_recorded",
                "study_id": effect["study_id"],
                "result_links": [{field: effect[field] for field in
                                  ("result_id", "effect_version_id", "comparison", "outcome", "timepoint")
                                  if field in effect}],
                "domains": {spec["key"]: _cell(None, True) for spec in mapping["domains"]},
                "overall": _cell(None, True) if mapping["overall_supported"] else None,
                "custom_overall": None, "source": {},
                "reviewer_id": selector_id if selector_kind == "reviewer" else None,
                "consensus_set_id": selector_id if selector_kind == "consensus" else None,
                "created_by": None, "updated_by": None, "created_at": None, "updated_at": None,
            })
    rows.sort(key=lambda row: (str(row["study_id"]), str(row["record_id"])))
    pending.sort(key=lambda row: (str(row.get("study_id")), str(row.get("record_id"))))
    if statistical_unit == "study":
        by_study = Counter(row["study_id"] for row in rows)
        ambiguous = sorted(study for study, count in by_study.items() if count > 1)
        if ambiguous:
            raise RobContractError("ambiguous_study_unit", "Multiple assessments for a study; filter to one exact result or record",
                                   study_ids=ambiguous)

    distributions = {"statistical_unit": statistical_unit, "weighting": "equal",
                     "n_selected_results": len(selected_refs),
                     "n_unassessed_results": len(unassessed_refs),
                     "n_units": len(rows), "risk_of_bias": [], "applicability": []}
    for spec in mapping["domains"]:
        values = [row["domains"][spec["key"]] for row in rows]
        counts = Counter(item["judgement"] if item["status"] == "recorded" else "not_recorded"
                         for item in values)
        distributions[spec["type"]].append({
            "key": spec["key"], "label": spec["label"], "counts": dict(counts),
            "n_total": len(values), "n_recorded": sum(item["status"] == "recorded" for item in values),
            "n_not_recorded": sum(item["status"] == "not_recorded" for item in values),
        })
    if mapping["overall_supported"]:
        overall_counts = Counter(row["overall"]["judgement"] if row["overall"]["status"] == "recorded"
                                 else "not_recorded" for row in rows)
        distributions["overall"] = {"counts": dict(overall_counts), "n_total": len(rows),
                                     "n_recorded": sum(row["overall"]["status"] == "recorded" for row in rows),
                                     "n_not_recorded": sum(row["overall"]["status"] == "not_recorded" for row in rows)}

    sidecar = [{"record_id": row["record_id"], "revision": row["revision"], "study_id": row["study_id"],
                "result_links": row["result_links"], "reviewer_id": row["reviewer_id"],
                "consensus_set_id": row["consensus_set_id"], "source": row["source"],
                "created_by": row["created_by"], "updated_by": row["updated_by"],
                "created_at": row["created_at"], "updated_at": row["updated_at"],
                "domains": copy.deepcopy(row["domains"]), "overall": copy.deepcopy(row["overall"]),
                "custom_overall": copy.deepcopy(row["custom_overall"])}
               for row in rows]

    def robvis_matrix(domain_specs: list[dict], tool: str, *, include_overall: bool = False) -> dict:
        has_overall = include_overall and bool(rows) and all(
            row["overall"] is not None and row["overall"]["status"] == "recorded" for row in rows)
        columns = [{"key": "Study", "label": "Study ID", "type": "study"}]
        columns.extend({"key": spec["column"],
                        "label": ("Applicability: " if spec["type"] == "applicability" else "") + spec["label"],
                        "type": spec["type"]} for spec in domain_specs)
        if has_overall:
            columns.append({"key": "Overall", "label": "Overall risk of bias", "type": "overall"})
        matrix_rows = []
        for row in rows:
            values = {"Study": row["study_id"]}
            for spec in domain_specs:
                cell = row["domains"][spec["key"]]
                value = cell["judgement"] if cell["status"] == "recorded" else "not_recorded"
                values[spec["column"]] = _ROBVIS_LABELS.get(value, value)
            if has_overall:
                values["Overall"] = _ROBVIS_LABELS.get(row["overall"]["judgement"],
                                                        row["overall"]["judgement"])
            matrix_rows.append(values)
        unique_studies = len({row["study_id"] for row in rows}) == len(rows)
        all_recorded = all(row["domains"][spec["key"]]["status"] == "recorded"
                           for row in rows for spec in domain_specs)
        can_import = bool(rows) and statistical_unit == "study" and unique_studies and all_recorded
        limitations = []
        if statistical_unit != "study" or not unique_studies:
            limitations.append("robvis expects one row per study")
        if not all_recorded:
            limitations.append("not_recorded is preserved and is not a framework judgement")
        if include_overall and not has_overall:
            limitations.append("overall is omitted; use overall=FALSE")
        return {"tool": tool, "overall": has_overall, "columns": columns,
                "rows": matrix_rows, "direct_import_possible": can_import,
                "limitations": limitations}

    risk_specs = [item for item in mapping["domains"] if item["type"] == "risk_of_bias"]
    applicability_specs = [item for item in mapping["domains"] if item["type"] == "applicability"]
    robvis_tool = {"RoB 2": "ROB2", "ROBINS-I": "ROBINS-I", "QUADAS-2": "QUADAS-2"}[framework]
    robvis_interchange = {
        "format": "reviewflow.robvis-interchange/1",
        "risk_of_bias": robvis_matrix(risk_specs, robvis_tool,
                                       include_overall=mapping["overall_supported"]),
        "source_sidecar": sidecar,
    }
    if applicability_specs:
        robvis_interchange["applicability"] = robvis_matrix(applicability_specs, "Generic")
        robvis_interchange["applicability"]["judgement_labels"] = ["Low", "Unclear", "High"]
    return {
        "schema_version": "reviewflow.rob-chart/1",
        "mapping": {key: mapping[key] for key in
                    ("framework", "tool_version", "variant", "mapping_version", "mapping_id",
                     "reference_url", "overall_supported")},
        "selection": {"selector": {"kind": selector_kind,
                                     "reviewer_id" if selector_kind == "reviewer" else "consensus_set_id": selector_id},
                      "result_refs": [dict(result_id=effects_by_ref[key].get("result_id"),
                                            effect_version_id=effects_by_ref[key].get("effect_version_id"))
                                      for key in ref_order],
                      "statistical_unit": statistical_unit, "weighting": "equal"},
        "rows": rows,
        "distributions": distributions,
        "pending_reconciliation": pending,
        "robvis_interchange": robvis_interchange,
    }
