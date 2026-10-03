"""Stored, version-exact GLS runs for dependent effect estimates."""

from __future__ import annotations

import hashlib
import json
import math
from contextlib import closing

from coscreen import __version__, db
from coscreen.analysis_storage import attach_method_sources, effect_designs, save_analysis_run
from coscreen.dependent_effects import fit_dependent_gls, leave_one_cluster_out, rho_sensitivity
from coscreen.pool_compatibility import require_compatible_effects
from coscreen.review_analysis import RATIOS


def create_dependent_gls_run(db_path, task_id: str, request: dict,
                             coding_freeze: dict | None = None) -> dict:
    """Read exact versions, fit a declared GLS model, and save an immutable run."""
    if request.get("construct_confirmed") is not True:
        raise ValueError("confirm the selected results share a construct, units and direction")
    refs = request.get("effect_refs")
    if not isinstance(refs, list) or not 2 <= len(refs) <= 500:
        raise ValueError("select between 2 and 500 exact effect/result versions")
    try:
        ids = [int(ref["result_id"]) for ref in refs]
        versions = [int(ref["effect_version_id"]) for ref in refs]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("effect_refs need integer result_id and effect_version_id") from exc
    if any(value <= 0 for value in ids + versions) or len(set(ids)) != len(ids):
        raise ValueError("effect_refs must name unique positive result IDs")
    measure = request.get("measure")
    if not isinstance(measure, str) or not measure:
        raise ValueError("a common measure is required")
    recode_directions = request.get("recode_directions", False)
    if type(recode_directions) is not bool:
        raise ValueError("recode_directions must be a boolean")
    with closing(db._connect(db_path)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        selected = []
        for result_id, version_id in zip(ids, versions):
            row = conn.execute(
                "SELECT result_id,effect_version_id,revision,study_id,comparison,outcome,timepoint,"
                "measure,estimate,se,source_key,input_json,entry_method,selected,source_locator "
                "FROM review_effect_versions WHERE result_id=? AND effect_version_id=?",
                (result_id, version_id),
            ).fetchone()
            if row is None:
                raise ValueError(f"result/version pair is unavailable: {result_id}/{version_id}")
            item = dict(zip(("result_id", "effect_version_id", "revision", "study_id",
                             "comparison", "outcome", "timepoint", "measure", "estimate", "se",
                             "source_key", "input_data", "entry_method", "selected",
                             "source_locator"), row))
            item["input_data"] = json.loads(item["input_data"])
            selected.append(item)
        if recode_directions:
            effect_directions = []
            compatible_rows = []
            for row in selected:
                input_data = row.get("input_data")
                direction = input_data.get("effect_direction") if isinstance(input_data, dict) else None
                if not isinstance(direction, str) or direction not in {
                        "first_vs_second", "second_vs_first"}:
                    raise ValueError(
                        "recode_directions requires a valid effect_direction on every selected result"
                    )
                effect_directions.append(direction)
                compatible_rows.append({
                    **row,
                    "input_data": {**input_data, "effect_direction": "first_vs_second"},
                })
            require_compatible_effects(compatible_rows, measure)
        else:
            effect_directions = None
            require_compatible_effects(selected, measure)
        if any(row["measure"] != measure for row in selected):
            raise ValueError("all selected results must use the declared measure")
        designs = effect_designs(conn, ids)
        source_articles = {}
        for key in sorted({row["source_key"] for row in selected}):
            source = conn.execute(
                "SELECT zotero_key,title,authors,journal,year,doi,url FROM articles WHERE zotero_key=?",
                (key,),
            ).fetchone()
            if source is None:
                raise ValueError(f"source report is missing: {key}")
            source_articles[key] = dict(zip(("zotero_key", "title", "authors", "journal",
                                             "year", "doi", "url"), source))
        conn.commit()

    design_ids = request.get("design_effect_ids")
    if not isinstance(design_ids, list) or len(design_ids) != len(ids) or \
            len(set(map(str, design_ids))) != len(ids) or set(map(str, design_ids)) != set(map(str, ids)):
        raise ValueError("design_effect_ids must match selected result IDs exactly")
    raw_design = request.get("design_matrix")
    if not isinstance(raw_design, list) or len(raw_design) != len(ids):
        raise ValueError("design_matrix needs one row per design_effect_id")
    matrix_by_id = dict(zip(map(str, design_ids), raw_design))
    design = [matrix_by_id[str(result_id)] for result_id in ids]
    names = request.get("design_columns")
    if not isinstance(names, list) or not names:
        raise ValueError("design_columns must be a nonempty list")
    design_by_id = {row["result_id"]: row for row in designs}
    effects = []
    for row in selected:
        estimate = row["estimate"]
        if measure in RATIOS:
            if estimate <= 0:
                raise ValueError("ratio estimates must be positive before log transformation")
            estimate = math.log(estimate)
        effects.append(dict(effect_id=str(row["result_id"]), study_id=row["study_id"],
                            cluster_id=design_by_id[row["result_id"]]["cluster_id"],
                            estimate=estimate, variance=row["se"] ** 2))

    covariance = request.get("working_covariance") or {}
    if not isinstance(covariance, dict):
        raise ValueError("working_covariance must be an object")
    kind = covariance.get("kind", "independent")
    note = covariance.get("source_note")
    if kind != "independent" and (not isinstance(note, str) or not note.strip()):
        raise ValueError("non-independent working covariance needs a source or assumption note")
    additive = request.get("additive_covariance")
    if additive is not None and (not isinstance(additive, dict) or
                                 not isinstance(additive.get("source_note"), str) or
                                 not additive["source_note"].strip()):
        raise ValueError("additive covariance needs a source or fitted-model note")
    inference = request.get("inference") or {}
    if not isinstance(inference, dict):
        raise ValueError("inference must be an object")
    fit_options = dict(covariance_kind=kind, rho=covariance.get("rho"),
                       supplied_covariance=covariance.get("matrix"),
                       covariance_effect_ids=[str(value) for value in covariance.get("effect_ids", [])]
                       if "effect_ids" in covariance else None,
                       covariance_source_note=note,
                       additive_covariance=additive.get("matrix") if additive else None,
                       additive_covariance_effect_ids=[str(value) for value in additive.get("effect_ids", [])]
                       if additive and "effect_ids" in additive else None,
                       effect_directions=effect_directions,
                       vcov_type=inference.get("vcov_type", "cr2"), level=inference.get("level", .95),
                       constraints=inference.get("constraints"),
                       constraint_names=inference.get("constraint_names"),
                       null_values=inference.get("null_values"))
    result = fit_dependent_gls(effects, design, names, **fit_options)
    result["analysis_scale"] = "log" if measure in RATIOS else "natural"
    diagnostics = request.get("diagnostics") or {}
    if not isinstance(diagnostics, dict):
        raise ValueError("diagnostics must be an object")
    if diagnostics.get("loso"):
        if result["n_clusters"] > 30:
            raise ValueError("cluster-deletion diagnostics currently support at most 30 clusters")
        result["cluster_deletion"] = leave_one_cluster_out(effects, design, names, **fit_options)
    rho_values = diagnostics.get("rho_values")
    if rho_values is not None:
        if not isinstance(rho_values, list) or not 1 <= len(rho_values) <= 20:
            raise ValueError("rho_values must contain between 1 and 20 values")
        result["rho_sensitivity"] = rho_sensitivity(
            effects, design, names, rho_values,
            **{key: value for key, value in fit_options.items() if key != "rho"})
    analysis_effects = result["input_snapshot"]["effects"] if recode_directions else effects
    snapshot = {"effects": selected, "effect_designs": designs,
                "coding_freeze": coding_freeze,
                "source_articles": source_articles, "analysis_effects": analysis_effects,
                "design_effect_ids": ids, "design_matrix": design, "design_columns": names,
                "n_effects": result["n_effects"], "n_studies": result["n_studies"],
                "n_clusters": result["n_clusters"]}
    if recode_directions:
        snapshot["direction_recoding"] = result["direction_recoding"]
    method_sources = ["cheung_multivariate_gls", "metafor_rma_mv",
                      "pustejovsky_tipton_cluster_robust"]
    if fit_options["vcov_type"].lower() == "cr2":
        method_sources.append("bell_mccaffrey_cr2")
    joint_test = result.get("robust_inference", {}).get("joint_test") or {}
    if joint_test.get("method") == "HTZ":
        method_sources.append("tipton_pustejovsky_htz")
    attach_method_sources(result, snapshot, method_sources)
    snapshot["data_sha256"] = hashlib.sha256(json.dumps(
        snapshot, ensure_ascii=False, allow_nan=False, sort_keys=True).encode("utf-8")).hexdigest()
    specification = {**request, "project_id": task_id,
                     "algorithm_version": "fixed_gls_cluster_robust_v1",
                     "model_scope": "fixed_gls_with_supplied_total_covariance",
                     "random_seed": None}
    with closing(db._connect(db_path)) as conn, conn:
        run_id = save_analysis_run(conn, specification, snapshot, result, __version__)
    return {"run_id": run_id, "specification": specification,
            "input_snapshot": snapshot, "result": result}
