"""Exact-version, stored three-level REML meta-regression runs."""

from __future__ import annotations

import hashlib
import json
import math
from contextlib import closing

import numpy as np

from coscreen import __version__, db
from coscreen.analysis_storage import attach_method_sources, effect_designs, save_analysis_run
from coscreen.dependent_effects import (
    KernelError,
    build_working_covariance,
    transform_effect_directions,
)
from coscreen.multilevel_meta_regression import fit_multilevel_meta_regression
from coscreen.pool_compatibility import require_compatible_effects
from coscreen.review_analysis import RATIOS


def _positive_ids(refs: object, key: str, maximum: int) -> list[dict]:
    if not isinstance(refs, list) or not 2 <= len(refs) <= maximum:
        raise ValueError(f"select 2 to {maximum} exact effect references")
    result = []
    for ref in refs:
        if not isinstance(ref, dict) or any(type(ref.get(field)) is not int or ref[field] <= 0
                                            for field in ("result_id", key)):
            raise ValueError(f"effect references need positive result_id and {key}")
        result.append({"result_id": ref["result_id"], key: ref[key]})
    if len({ref["result_id"] for ref in result}) != len(result):
        raise ValueError("select each result ID once")
    return result


def _moderator_design(conn, result_ids: list[int], request: dict) -> tuple[list[list[float]], list[str], list[dict]]:
    moderators = request.get("moderators", [])
    refs = request.get("moderator_refs", [])
    if not isinstance(moderators, list) or len(moderators) > 20 or \
            not isinstance(refs, list) or len(refs) > 10_000:
        raise ValueError("select at most 20 moderators and 10000 exact moderator references")
    names = []
    for item in moderators:
        if not isinstance(item, dict) or set(item) - {"name", "kind", "reference"}:
            raise ValueError("moderator specs need name, kind and optional reference")
        name, kind = item.get("name"), item.get("kind")
        if not isinstance(name, str) or not name.strip() or len(name) > 120 or name == "Intercept" \
                or kind not in {"continuous", "categorical"}:
            raise ValueError("moderator needs a valid name and continuous or categorical kind")
        if name in names:
            raise ValueError("moderator names must be unique")
        if kind == "continuous" and item.get("reference") is not None:
            raise ValueError("continuous moderator cannot have a reference level")
        if kind == "categorical" and (not isinstance(item.get("reference"), str)
                                      or not item["reference"].strip()):
            raise ValueError("categorical moderator requires a reference level")
        names.append(name)
    keyed = {}
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) != {"result_id", "name", "moderator_version_id"} \
                or type(ref["result_id"]) is not int or type(ref["moderator_version_id"]) is not int \
                or ref["result_id"] <= 0 or ref["moderator_version_id"] <= 0 \
                or ref["name"] not in names:
            raise ValueError("moderator references need exact result ID, name and version ID")
        key = (ref["result_id"], ref["name"])
        if key in keyed:
            raise ValueError("duplicate result-level moderator reference")
        keyed[key] = ref["moderator_version_id"]
    expected = {(result_id, name) for result_id in result_ids for name in names}
    if set(keyed) != expected:
        raise ValueError("supply one exact moderator version for every selected result and moderator")
    selected = {}
    for key, version_id in keyed.items():
        row = conn.execute(
            "SELECT moderator_version_id,result_id,name,revision,value_json,value_type,"
            "source_key,source_locator,changed_at FROM review_result_moderators "
            "WHERE moderator_version_id=? AND result_id=? AND name=?",
            (version_id, *key)).fetchone()
        if row is None:
            raise ValueError(f"moderator version is unavailable for result {key[0]} / {key[1]}")
        selected[key] = dict(zip(("moderator_version_id", "result_id", "name", "revision",
                                  "value", "value_type", "source_key", "source_locator",
                                  "changed_at"), row))
        selected[key]["value"] = json.loads(selected[key]["value"])
    design = [[1.0] for _ in result_ids]
    columns = ["Intercept"]
    for item in moderators:
        name, kind = item["name"], item["kind"]
        values = [selected[result_id, name]["value"] for result_id in result_ids]
        if any(selected[result_id, name]["value_type"] != kind for result_id in result_ids):
            raise ValueError(f"moderator {name} has a saved type mismatch")
        if kind == "continuous":
            if any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or not math.isfinite(value) for value in values):
                raise ValueError(f"moderator {name} needs finite numeric values")
            columns.append(name)
            for row, value in zip(design, values):
                row.append(float(value))
        else:
            reference = item["reference"].strip()
            if reference not in values:
                raise ValueError(f"reference level {reference} is absent for moderator {name}")
            for level in sorted(set(values) - {reference}):
                columns.append(f"{name}[{level}]")
                for row, value in zip(design, values):
                    row.append(float(value == level))
    return design, columns, [selected[key] for key in sorted(selected)]


def create_multilevel_run(db_path, task_id: str, request: dict,
                          coding_freeze: dict | None = None) -> dict:
    """Fit from selected effect/moderator revisions, then store the exact run."""
    if not isinstance(request, dict) or request.get("construct_confirmed") is not True:
        raise ValueError("confirm construct, units and effect direction before fitting")
    refs = _positive_ids(request.get("effect_refs"), "effect_version_id", 500)
    measure = request.get("measure")
    if not isinstance(measure, str) or not measure:
        raise ValueError("a common measure is required")
    recode_directions = request.get("recode_directions", False)
    if type(recode_directions) is not bool:
        raise ValueError("recode_directions must be a boolean")
    ids = [ref["result_id"] for ref in refs]
    with closing(db._connect(db_path)) as conn:
        conn.execute("BEGIN")
        effects = []
        for ref in refs:
            row = conn.execute(
                "SELECT result_id,effect_version_id,revision,study_id,comparison,outcome,timepoint,"
                "measure,estimate,se,source_key,input_json,entry_method,selected,source_locator "
                "FROM review_effect_versions WHERE result_id=? AND effect_version_id=?",
                (ref["result_id"], ref["effect_version_id"])).fetchone()
            if row is None:
                raise ValueError(f"result/version pair is unavailable: {ref}")
            item = dict(zip(("result_id", "effect_version_id", "revision", "study_id",
                             "comparison", "outcome", "timepoint", "measure", "estimate", "se",
                             "source_key", "input_data", "entry_method", "selected",
                             "source_locator"), row))
            item["input_data"] = json.loads(item["input_data"])
            effects.append(item)
        if recode_directions:
            effect_directions = []
            compatible_effects = []
            for effect in effects:
                input_data = effect.get("input_data")
                direction = input_data.get("effect_direction") if isinstance(input_data, dict) else None
                if not isinstance(direction, str) or direction not in {
                        "first_vs_second", "second_vs_first"}:
                    raise ValueError(
                        "recode_directions requires a valid effect_direction on every selected result"
                    )
                effect_directions.append(direction)
                compatible_effects.append({
                    **effect,
                    "input_data": {**input_data, "effect_direction": "first_vs_second"},
                })
            require_compatible_effects(compatible_effects, measure)
        else:
            effect_directions = None
            require_compatible_effects(effects, measure)
        if any(item["measure"] != measure for item in effects):
            raise ValueError("all selected effects must use the declared measure")
        designs = effect_designs(conn, ids)
        design_matrix, design_columns, moderator_versions = _moderator_design(conn, ids, request)
        source_articles = {}
        for key in sorted({item["source_key"] for item in effects}):
            row = conn.execute(
                "SELECT zotero_key,title,authors,journal,year,doi,url FROM articles WHERE zotero_key=?",
                (key,)).fetchone()
            if row is None:
                raise ValueError(f"source report is missing: {key}")
            source_articles[key] = dict(zip(("zotero_key", "title", "authors", "journal",
                                            "year", "doi", "url"), row))
    design_by_id = {item["result_id"]: item for item in designs}
    rows = []
    for effect in effects:
        estimate = effect["estimate"]
        if measure in RATIOS:
            if estimate <= 0:
                raise ValueError("ratio estimate must be positive for log-scale analysis")
            estimate = math.log(estimate)
        rows.append({"effect_id": str(effect["result_id"]), "study_id": effect["study_id"],
                     "cluster_id": design_by_id[effect["result_id"]]["cluster_id"],
                     "source_id": effect["source_key"], "estimate": estimate,
                     "variance": effect["se"] ** 2})
    covariance = request.get("working_covariance", {"kind": "independent"})
    if not isinstance(covariance, dict):
        raise ValueError("working_covariance must be an object")
    kind = covariance.get("kind", "independent")
    if kind != "independent" and (not isinstance(covariance.get("source_note"), str)
                                      or not covariance["source_note"].strip()):
        raise ValueError("non-independent covariance needs a source or assumption note")
    built = build_working_covariance(
        [row["effect_id"] for row in rows], [row["variance"] for row in rows],
        [row["cluster_id"] for row in rows], kind=kind, rho=covariance.get("rho"),
        matrix=covariance.get("matrix"),
        covariance_effect_ids=[str(value) for value in covariance["effect_ids"]]
        if "effect_ids" in covariance else None)
    matrix = np.asarray(built["matrix"])
    direction_recoding = None
    if recode_directions:
        effect_ids = [row["effect_id"] for row in rows]
        direction_recoding = transform_effect_directions(
            effect_ids, [row["estimate"] for row in rows], effect_directions, matrix)
        for row, estimate in zip(rows, direction_recoding["estimates"]):
            row["estimate"] = estimate
        matrix = np.asarray(direction_recoding["covariance"])
        built = {**built, "matrix": matrix.tolist()}
    by_study = {}
    for index, row in enumerate(rows):
        by_study.setdefault(row["study_id"], []).append(index)
    for left, row in enumerate(rows):
        for right in range(left):
            if row["study_id"] != rows[right]["study_id"] and abs(matrix[left, right]) > 1e-12:
                raise KernelError("crossed_sampling_covariance",
                                  "sampling covariance across random-effect studies is unsupported")
    blocks = {study: matrix[np.ix_(indices, indices)].tolist()
              for study, indices in by_study.items()}
    inference = request.get("inference", {})
    if not isinstance(inference, dict):
        raise ValueError("inference must be an object")
    result = fit_multilevel_meta_regression(
        rows, design_matrix, design_columns,
        design_effect_ids=[str(value) for value in ids],
        within_study_covariances=blocks,
        constraints=inference.get("constraints"),
        constraint_names=inference.get("constraint_names"),
        null_values=inference.get("null_values"), level=inference.get("level", .95))
    result["analysis_scale"] = "log" if measure in RATIOS else "natural"
    if direction_recoding is not None:
        result["direction_recoding"] = {
            "target_effect_direction": direction_recoding["target_effect_direction"],
            "effect_ids": direction_recoding["effect_ids"],
            "source_directions": effect_directions,
            "direction_signs": direction_recoding["direction_signs"],
            "sampling_covariance_transformed": True,
        }
    snapshot = {"effects": effects, "effect_designs": designs,
                "moderator_versions": moderator_versions,
                "design_effect_ids": ids, "design_matrix": design_matrix,
                "design_columns": design_columns, "working_covariance": built,
                "source_articles": source_articles, "coding_freeze": coding_freeze,
                "n_effects": result["n_effects"], "n_studies": result["n_studies"],
                "n_clusters": result["n_clusters"]}
    if direction_recoding is not None:
        snapshot["analysis_effects"] = rows
        snapshot["direction_recoding"] = result["direction_recoding"]
    method_sources = ["cheung_three_level", "konstantopoulos_three_level",
                      "metafor_rma_mv", "bell_mccaffrey_cr2",
                      "pustejovsky_tipton_cluster_robust"]
    joint_test = result.get("robust_inference", {}).get("joint_test") or {}
    if joint_test.get("method") == "HTZ":
        method_sources.append("tipton_pustejovsky_htz")
    attach_method_sources(result, snapshot, method_sources)
    snapshot["data_sha256"] = hashlib.sha256(json.dumps(
        snapshot, ensure_ascii=False, allow_nan=False, sort_keys=True).encode("utf-8")).hexdigest()
    spec = {**request, "project_id": task_id,
            "algorithm_version": "three_level_reml_cr2_v1",
            "model_scope": "three_level_random_intercept_meta_regression",
            "random_seed": None}
    with closing(db._connect(db_path)) as conn, conn:
        run_id = save_analysis_run(conn, spec, snapshot, result, __version__)
    return {"run_id": run_id, "specification": spec,
            "input_snapshot": snapshot, "result": result}
