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

# F3-65: the immutable analysis_runs row echoed the whole unvalidated request
# body back on every GET, so an authenticated caller could park arbitrary
# payloads (measured 200,517 B) in a table with no_update/no_delete triggers.
# Only the keys the fit itself reads are persisted; an unrecognised key is
# dropped (the request still succeeds) so no legal analysis is refused.
_SPECIFICATION_KEYS = (
    "construct_confirmed", "effect_refs", "measure", "recode_directions",
    "design_effect_ids", "design_matrix", "design_columns", "working_covariance",
    "additive_covariance", "inference", "diagnostics",
)
_SUBOBJECT_KEYS = {
    "working_covariance": ("kind", "rho", "source_note", "matrix", "effect_ids"),
    "additive_covariance": ("source_note", "matrix", "effect_ids"),
    "inference": ("vcov_type", "level", "constraints", "constraint_names", "null_values"),
    "diagnostics": ("loso", "rho_values"),
}


def _exact_integer_ids(refs: list) -> tuple[list[int], list[int]]:
    """Read exact positive-integer result/version IDs, never truncating (F3-66).

    ``int(ref["result_id"])`` accepted 1.9 (silently truncated to 1), ``True``
    and numeric strings, so the immutable ``specification`` kept the value the
    caller sent while the ``input_snapshot`` recorded the truncated one - a
    self-contradicting audit trail. Integral JSON numbers still parse exactly
    (``5`` and ``5.0`` both mean 5), and a decimal string of digits is still
    read as that integer, so no previously valid request is refused; only
    values that would have been *silently changed* now raise.
    """
    ids, versions = [], []
    for ref in refs:
        if not isinstance(ref, dict):
            raise ValueError("effect_refs need integer result_id and effect_version_id")
        for key, target in (("result_id", ids), ("effect_version_id", versions)):
            value = ref.get(key)
            target.append(_exact_integer(value, f"effect_refs {key}"))
    return ids, versions


def _exact_integer(value: object, label: str) -> int:
    """Return ``value`` as an integer only when no digits are lost."""
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an integer, not a boolean")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    raise ValueError(f"{label} must be an integer, not {type(value).__name__}")


def _bounded_specification(request: dict, task_id: str) -> dict:
    """Keep the persisted specification to the keys the fit consumes (F3-65)."""
    specification = {}
    for key in _SPECIFICATION_KEYS:
        if key not in request:
            continue
        value = request[key]
        if key in _SUBOBJECT_KEYS and isinstance(value, dict):
            value = {name: value[name] for name in _SUBOBJECT_KEYS[key] if name in value}
        specification[key] = value
    specification.update({
        "project_id": task_id,
        "algorithm_version": "fixed_gls_cluster_robust_v1",
        "model_scope": "fixed_gls_with_supplied_total_covariance",
        "random_seed": None,
    })
    return specification


def _require_object(request: dict, key: str) -> dict:
    """Read an optional object field without swallowing falsy non-objects (F3-69).

    ``request.get(key) or {}`` turned ``[]``, ``0``, ``""`` and ``False`` into
    the empty default before the isinstance guard ever ran, so twelve falsy
    payloads were all accepted silently while ``"bogus"`` was rejected. An
    absent key - or an explicit ``null``, which is the JSON idiom for "not
    provided" and was already accepted - still means the documented default;
    any other present value must be an object.
    """
    value = request.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return value


def _sequence_of_ids(container: dict, field: str, key: str = "effect_ids") -> list[str] | None:
    """Coerce an optional ID sequence, rejecting non-list values (F3-63).

    The list comprehension iterated the raw value, so an int (``effect_ids=5``)
    or null raised TypeError, which the endpoint does not translate and became
    a 500. An absent key - and an explicit ``null`` - keep the documented "no
    separate ID list" default; any other present value must be a list.
    """
    value = container.get(key)
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a list of effect IDs")
    return [str(item) for item in value]


def _sampling_variance(se: object, result_id: int) -> float:
    """Square a stored SE into a sampling variance without an uncaught overflow (F3-68).

    ``save_effect`` accepts any finite SE > 0 (an explicit "write side keeps
    accepting se=1e200" contract), so ``se ** 2`` raised OverflowError and the
    endpoint - which only translates ValueError/KernelError - answered 500.
    The variance is computed under a range check and reported as an ordinary
    spec error, the same degradation guard style used on the synthesis read
    paths (MM10-01 band).
    """
    value = float(se)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"result {result_id} sampling SE must be a finite positive number")
    try:
        variance = value * value
    except OverflowError:
        variance = math.inf
    if not math.isfinite(variance) or variance <= 0:
        raise ValueError(
            f"result {result_id} sampling variance is outside the finite numerical range "
            f"(SE {value:.3g})"
        )
    return variance


def _clean_non_finite(value, path: str, findings: list) -> object:
    """Replace non-finite numbers with null and report where they were (F3-64 family).

    A stored ``input_json`` may contain the ``Infinity``/``NaN`` literals that
    ``json.dumps`` emits by default, so an otherwise fittable selection made the
    immutable snapshot unhashable (``allow_nan=False``) and the run failed as a
    bare "Out of range float values are not JSON compliant" with no clue which
    result was at fault. Cleaning here keeps the run possible, keeps the
    snapshot strictly serializable, and names every affected path; the stored
    effect version itself is untouched.
    """
    if isinstance(value, dict):
        return {key: _clean_non_finite(item, f"{path}.{key}", findings)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean_non_finite(item, f"{path}[{index}]", findings)
                for index, item in enumerate(value)]
    if isinstance(value, float) and not math.isfinite(value):
        findings.append(path)
        return None
    return value


def create_dependent_gls_run(db_path, task_id: str, request: dict,
                             coding_freeze: dict | None = None) -> dict:
    """Read exact versions, fit a declared GLS model, and save an immutable run."""
    if request.get("construct_confirmed") is not True:
        raise ValueError("confirm the selected results share a construct, units and direction")
    refs = request.get("effect_refs")
    if not isinstance(refs, list) or not 2 <= len(refs) <= 500:
        raise ValueError("select between 2 and 500 exact effect/result versions")
    ids, versions = _exact_integer_ids(refs)
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
        # F3-68: the write side accepts any finite se > 0 (se=1e200 is legal),
        # so `se ** 2` could raise OverflowError and escape the endpoint's
        # ValueError handler as a 500. Convert to the sampling variance with an
        # explicit range check: the same degradation guard the shared
        # _synthesize_rows path uses (MM10-01 band), reported as a spec error.
        variance = _sampling_variance(row["se"], row["result_id"])
        effects.append(dict(effect_id=str(row["result_id"]), study_id=row["study_id"],
                            cluster_id=design_by_id[row["result_id"]]["cluster_id"],
                            estimate=estimate, variance=variance))

    covariance = _require_object(request, "working_covariance")
    kind = covariance.get("kind", "independent")
    note = covariance.get("source_note")
    if kind != "independent" and (not isinstance(note, str) or not note.strip()):
        raise ValueError("non-independent working covariance needs a source or assumption note")
    additive = request.get("additive_covariance")
    if additive is not None and (not isinstance(additive, dict) or
                                 not isinstance(additive.get("source_note"), str) or
                                 not additive["source_note"].strip()):
        raise ValueError("additive covariance needs a source or fitted-model note")
    inference = _require_object(request, "inference")
    fit_options = dict(covariance_kind=kind, rho=covariance.get("rho"),
                       supplied_covariance=covariance.get("matrix"),
                       covariance_effect_ids=_sequence_of_ids(
                           covariance, "working_covariance.effect_ids"),
                       covariance_source_note=note,
                       additive_covariance=additive.get("matrix") if additive else None,
                       additive_covariance_effect_ids=(
                           _sequence_of_ids(additive, "additive_covariance.effect_ids")
                           if additive else None),
                       effect_directions=effect_directions,
                       vcov_type=inference.get("vcov_type", "cr2"), level=inference.get("level", .95),
                       constraints=inference.get("constraints"),
                       constraint_names=inference.get("constraint_names"),
                       null_values=inference.get("null_values"))
    result = fit_dependent_gls(effects, design, names, **fit_options)
    result["analysis_scale"] = "log" if measure in RATIOS else "natural"
    diagnostics = _require_object(request, "diagnostics")
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
    non_finite_paths: list[str] = []
    snapshot_payload = {"effects": selected, "effect_designs": designs,
                        "coding_freeze": coding_freeze,
                        "source_articles": source_articles,
                        "analysis_effects": analysis_effects,
                        "design_effect_ids": ids, "design_matrix": design,
                        "design_columns": names,
                        "n_effects": result["n_effects"],
                        "n_studies": result["n_studies"],
                        "n_clusters": result["n_clusters"]}
    if recode_directions:
        snapshot_payload["direction_recoding"] = result["direction_recoding"]
    snapshot = _clean_non_finite(snapshot_payload, "input_snapshot", non_finite_paths)
    specification = _clean_non_finite(_bounded_specification(request, task_id),
                                      "specification", non_finite_paths)
    if non_finite_paths:
        result["warnings"] = [
            *result.get("warnings", []),
            "Non-finite numbers in the stored effect input were recorded as null: "
            f"{', '.join(non_finite_paths)}",
        ]
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
    with closing(db._connect(db_path)) as conn, conn:
        run_id = save_analysis_run(conn, specification, snapshot, result, __version__)
    return {"run_id": run_id, "specification": specification,
            "input_snapshot": snapshot, "result": result}
