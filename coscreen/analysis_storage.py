"""Additive storage for effect history and immutable analysis runs.

The existing review_effects table remains the live legacy API. History is
captured by SQLite triggers so all current writers share the same rule.
"""

from __future__ import annotations

import json
import hashlib
import sqlite3
import uuid
from datetime import datetime, timezone


_EFFECT_FIELDS = (
    "result_id", "study_id", "comparison", "outcome", "timepoint", "measure",
    "estimate", "se", "source_key", "input_json", "entry_method", "selected",
    "source_locator",
)

_METHOD_SOURCE_REGISTRY_VERSION = 1
_METHOD_SOURCES = {
    "harrer_inverse_variance": {
        "source_version": "2022, 1st ed.",
        "citation": "Harrer et al. (2022), Doing Meta-Analysis with R, chapters 3–5.",
    },
    "borenstein_inverse_variance": {
        "source_version": "2021, 2nd ed.",
        "citation": "Borenstein et al. (2021), Introduction to Meta-Analysis, 2nd ed., chapters 11–14.",
    },
    "higgins_thompson_i2": {
        "source_version": "2002",
        "citation": "Higgins & Thompson (2002), Quantifying heterogeneity in a meta-analysis, Statistics in Medicine 21:1539–1558. DOI: 10.1002/sim.1186.",
        "doi": "10.1002/sim.1186",
    },
    "dersimonian_laird": {
        "source_version": "1986",
        "citation": "DerSimonian & Laird (1986), Meta-analysis in clinical trials, Controlled Clinical Trials 7(3):177–188. DOI: 10.1016/0197-2456(86)90046-2.",
        "doi": "10.1016/0197-2456(86)90046-2",
    },
    "paule_mandel": {
        "source_version": "1982",
        "citation": "Paule & Mandel (1982), Consensus values and weighting factors, Journal of Research of the National Bureau of Standards 87:377–385.",
    },
    "harrer_reml": {
        "source_version": "2022, 1st ed.",
        "citation": "Harrer et al. (2022), Doing Meta-Analysis with R, section 4.1.2.1, pp. 102–103.",
    },
    "metafor_rma_uni": {
        "source_version": "metafor documentation, accessed 2026-09-30",
        "citation": "metafor rma.uni documentation (REML estimator).",
        "url": "https://wviechtb.github.io/metafor/reference/rma.uni.html",
    },
    "knapp_hartung": {
        "source_version": "2003",
        "citation": "Knapp & Hartung (2003), Improved tests for a random effects meta-regression with a single covariate, Statistics in Medicine 22:2693–2710. DOI: 10.1002/sim.1482.",
        "doi": "10.1002/sim.1482",
    },
    "cheung_multivariate_gls": {
        "source_version": "2015",
        "citation": "Cheung (2015), Meta-Analysis: A Structural Equation Modeling Approach, chapter 5, pp. 121–126.",
    },
    "metafor_rma_mv": {
        "source_version": "metafor documentation, accessed 2026-09-30",
        "citation": "metafor rma.mv documentation (fixed-effects GLS with supplied sampling covariance).",
        "url": "https://wviechtb.github.io/metafor/reference/rma.mv.html",
    },
    "bell_mccaffrey_cr2": {
        "source_version": "2002",
        "citation": "Bell & McCaffrey (2002), Bias reduction in standard errors for linear regression with multi-stage samples, Survey Methodology 28(2):169–181.",
    },
    "pustejovsky_tipton_cluster_robust": {
        "source_version": "2018",
        "citation": "Pustejovsky & Tipton (2018), Small-sample methods for cluster-robust variance estimation and hypothesis testing in fixed effects models, Journal of Business & Economic Statistics 36(4):672–683. DOI: 10.1080/07350015.2016.1247004.",
        "doi": "10.1080/07350015.2016.1247004",
    },
    "tipton_pustejovsky_htz": {
        "source_version": "2015",
        "citation": "Tipton & Pustejovsky (2015), Small-sample adjustments for tests of moderators and model fit using RVE in meta-regression, Journal of Educational and Behavioral Statistics 40(6):604–634. DOI: 10.3102/1076998615606099.",
        "doi": "10.3102/1076998615606099",
    },
    "cheung_three_level": {
        "source_version": "2014",
        "citation": "Cheung (2014), Modeling dependent effect sizes with three-level meta-analyses: A structural equation modeling approach, Psychological Methods 19(2):211–229. DOI: 10.1037/a0032968.",
        "doi": "10.1037/a0032968",
    },
    "konstantopoulos_three_level": {
        "source_version": "2011",
        "citation": "Konstantopoulos (2011), Fixed effects and variance components estimation in three-level meta-analysis, Research Synthesis Methods 2(1):61–76. DOI: 10.1002/jrsm.35.",
        "doi": "10.1002/jrsm.35",
    },
    "cochrane_rob_ch7": {
        "source_version": "current online handbook, chapter 7",
        "citation": "Cochrane Handbook for Systematic Reviews of Interventions, chapter 7, sections 7.4–7.6 (risk-of-bias assessments).",
        "url": "https://www.cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-07",
    },
    "cochrane_rob_ch8": {
        "source_version": "current online handbook, chapter 8",
        "citation": "Cochrane Handbook for Systematic Reviews of Interventions, chapter 8 (assessing risk of bias in a randomized trial).",
        "url": "https://www.cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-08",
    },
    "robins_i_2016": {
        "source_version": "2016, original ROBINS-I",
        "citation": "Sterne et al. (2016), ROBINS-I: a tool for assessing risk of bias in non-randomised studies of interventions, BMJ 355:i4919. DOI: 10.1136/bmj.i4919.",
        "doi": "10.1136/bmj.i4919",
    },
    "quadas_2_2011": {
        "source_version": "2011, QUADAS-2",
        "citation": "Whiting et al. (2011), QUADAS-2: a revised tool for the quality assessment of diagnostic accuracy studies, Annals of Internal Medicine 155:529–536. DOI: 10.7326/0003-4819-155-8-201110180-00009.",
        "doi": "10.7326/0003-4819-155-8-201110180-00009",
    },
    "cochrane_rob_sensitivity": {
        "source_version": "current online handbook, section 10.14",
        "citation": "Cochrane Handbook for Systematic Reviews of Interventions, chapter 10, section 10.14 (sensitivity analyses).",
        "url": "https://www.cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-10",
    },
}


def attach_method_sources(result: dict, input_snapshot: dict, source_ids: list[str]) -> None:
    """Freeze registered citations into both the run result and its snapshot."""
    refs = []
    for source_id in dict.fromkeys(source_ids):
        source = _METHOD_SOURCES[source_id]
        refs.append({"registry": "coscreen.method_sources",
                     "registry_version": _METHOD_SOURCE_REGISTRY_VERSION,
                     "source_id": source_id, **source})
    citations = [ref["citation"] for ref in refs]
    result["method_sources"] = citations
    result["method_source_refs"] = refs
    input_snapshot["method_sources"] = citations
    input_snapshot["method_source_refs"] = refs


def synthesis_method_source_ids(model: str, ci_method: str) -> list[str]:
    """Return only the general synthesis sources used by the selected options."""
    sources = ["harrer_inverse_variance", "borenstein_inverse_variance", "higgins_thompson_i2"]
    if model == "random":
        sources.append("dersimonian_laird")
    elif model == "random_pm":
        sources.append("paule_mandel")
    elif model == "random_reml":
        sources.extend(("harrer_reml", "metafor_rma_uni"))
    if ci_method == "hksj":
        sources.append("knapp_hartung")
    return sources

_SCHEMA = """
CREATE TABLE IF NOT EXISTS review_effect_versions (
  effect_version_id INTEGER PRIMARY KEY AUTOINCREMENT,
  result_id INTEGER NOT NULL,
  revision INTEGER NOT NULL,
  study_id TEXT NOT NULL, comparison TEXT NOT NULL, outcome TEXT NOT NULL,
  timepoint TEXT NOT NULL, measure TEXT NOT NULL, estimate REAL NOT NULL,
  se REAL NOT NULL, source_key TEXT NOT NULL, input_json TEXT NOT NULL,
  entry_method TEXT NOT NULL, selected INTEGER NOT NULL,
  source_locator TEXT NOT NULL, changed_at TEXT NOT NULL,
  UNIQUE(result_id, revision));
CREATE INDEX IF NOT EXISTS idx_review_effect_versions_result
  ON review_effect_versions(result_id, revision);
CREATE TABLE IF NOT EXISTS review_effect_design (
  result_id INTEGER PRIMARY KEY, cluster_id TEXT NOT NULL,
  sample_id TEXT NOT NULL, structure_note TEXT NOT NULL,
  revision INTEGER NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_review_effect_design_cluster
  ON review_effect_design(cluster_id);
CREATE TABLE IF NOT EXISTS review_result_moderators (
  moderator_version_id INTEGER PRIMARY KEY AUTOINCREMENT,
  result_id INTEGER NOT NULL, name TEXT NOT NULL,
  revision INTEGER NOT NULL, value_json TEXT NOT NULL,
  value_type TEXT NOT NULL CHECK(value_type IN ('continuous','categorical')),
  source_key TEXT NOT NULL, source_locator TEXT NOT NULL,
  changed_at TEXT NOT NULL,
  UNIQUE(result_id,name,revision));
CREATE INDEX IF NOT EXISTS idx_result_moderators_result
  ON review_result_moderators(result_id,name,revision);
CREATE TRIGGER IF NOT EXISTS review_result_moderators_no_update
BEFORE UPDATE ON review_result_moderators
  BEGIN SELECT RAISE(ABORT, 'result moderators are immutable'); END;
CREATE TRIGGER IF NOT EXISTS review_result_moderators_no_delete
BEFORE DELETE ON review_result_moderators
  BEGIN SELECT RAISE(ABORT, 'result moderators are immutable'); END;
CREATE TABLE IF NOT EXISTS analysis_runs (
  run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
  specification_json TEXT NOT NULL, input_snapshot_json TEXT NOT NULL,
  result_json TEXT NOT NULL, software_version TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS analysis_runs_no_update BEFORE UPDATE ON analysis_runs
  BEGIN SELECT RAISE(ABORT, 'analysis runs are immutable'); END;
CREATE TRIGGER IF NOT EXISTS analysis_runs_no_delete BEFORE DELETE ON analysis_runs
  BEGIN SELECT RAISE(ABORT, 'analysis runs are immutable'); END;
"""


def ensure_analysis_storage(conn: sqlite3.Connection) -> None:
    """Upgrade a database copy or task database without rewriting legacy rows."""
    conn.executescript(_SCHEMA)
    fields = ",".join(_EFFECT_FIELDS)
    conn.execute(
        "INSERT INTO review_effect_versions (" + fields + ",revision,changed_at) "
        "SELECT " + ",".join("e." + field for field in _EFFECT_FIELDS) +
        ",1,datetime('now') FROM review_effects e WHERE NOT EXISTS ("
        "SELECT 1 FROM review_effect_versions v WHERE v.result_id=e.result_id)"
    )
    new_values = ",".join("NEW." + field for field in _EFFECT_FIELDS)
    changed = " OR ".join("OLD." + field + " IS NOT NEW." + field for field in _EFFECT_FIELDS[1:])
    conn.executescript(f"""
CREATE TRIGGER IF NOT EXISTS review_effects_version_insert AFTER INSERT ON review_effects
BEGIN
  INSERT INTO review_effect_versions ({fields},revision,changed_at)
  VALUES ({new_values},1,datetime('now'));
END;
CREATE TRIGGER IF NOT EXISTS review_effects_version_update AFTER UPDATE ON review_effects
WHEN {changed}
BEGIN
  INSERT INTO review_effect_versions ({fields},revision,changed_at)
  VALUES ({new_values},
    (SELECT COALESCE(MAX(revision),0)+1 FROM review_effect_versions WHERE result_id=NEW.result_id),
    datetime('now'));
END;
""")


def effect_versions(conn: sqlite3.Connection, result_ids: list[int]) -> list[dict]:
    """Return the exact current version of each requested result in caller order."""
    if len(set(result_ids)) != len(result_ids):
        raise ValueError("result IDs must be unique")
    if not result_ids:
        raise ValueError("at least one result ID is required")
    rows = []
    for result_id in result_ids:
        row = conn.execute(
            "SELECT effect_version_id,revision," + ",".join(_EFFECT_FIELDS) +
            " FROM review_effect_versions WHERE result_id=? ORDER BY revision DESC LIMIT 1",
            (result_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown result ID: {result_id}")
        names = ("effect_version_id", "revision", *_EFFECT_FIELDS)
        item = dict(zip(names, row))
        item["input_data"] = json.loads(item.pop("input_json"))
        rows.append(item)
    return rows


def effect_history(conn: sqlite3.Connection, result_ids: list[int]) -> list[dict]:
    """Expose exact saved versions for explicit result IDs without changing the legacy list."""
    if not result_ids or len(result_ids) > 500 or len(set(result_ids)) != len(result_ids) \
            or any(type(result_id) is not int or result_id <= 0 for result_id in result_ids):
        raise ValueError("select 1 to 500 unique positive result IDs")
    rows = []
    for result_id in result_ids:
        found = conn.execute(
            "SELECT effect_version_id,revision," + ",".join(_EFFECT_FIELDS) +
            " FROM review_effect_versions WHERE result_id=? ORDER BY revision", (result_id,),
        ).fetchall()
        if not found:
            raise ValueError(f"unknown result ID: {result_id}")
        for raw in found:
            item = dict(zip(("effect_version_id", "revision", *_EFFECT_FIELDS), raw))
            item["input_data"] = json.loads(item.pop("input_json"))
            rows.append(item)
    return rows


def set_effect_design(conn: sqlite3.Connection, result_id: int, cluster_id: str,
                      sample_id: str, structure_note: str,
                      expected_revision: int | None = None) -> dict:
    """Bind an effect to an explicitly named independent sample/cluster."""
    cluster_id, sample_id, structure_note = (value.strip() for value in
                                             (cluster_id, sample_id, structure_note))
    if not all((cluster_id, sample_id, structure_note)):
        raise ValueError("cluster, sample and independence rationale are required")
    if conn.execute("SELECT 1 FROM review_effects WHERE result_id=?", (result_id,)).fetchone() is None:
        raise ValueError("unknown result ID")
    conflict = conn.execute(
        "SELECT 1 FROM review_effect_design WHERE result_id<>? AND "
        "((cluster_id=? AND sample_id<>?) OR (sample_id=? AND cluster_id<>?)) LIMIT 1",
        (result_id, cluster_id, sample_id, sample_id, cluster_id),
    ).fetchone()
    if conflict:
        raise ValueError("cluster and sample IDs must map one-to-one")
    existing = conn.execute("SELECT revision FROM review_effect_design WHERE result_id=?",
                            (result_id,)).fetchone()
    if expected_revision is not None and expected_revision != (existing[0] if existing else 0):
        raise ValueError("effect design revision conflict")
    revision = existing[0] + 1 if existing else 1
    conn.execute(
        "INSERT INTO review_effect_design VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(result_id) DO UPDATE SET cluster_id=excluded.cluster_id,"
        "sample_id=excluded.sample_id,structure_note=excluded.structure_note,"
        "revision=excluded.revision,updated_at=excluded.updated_at",
        (result_id, cluster_id, sample_id, structure_note, revision,
         datetime.now(timezone.utc).isoformat()),
    )
    return dict(result_id=result_id, cluster_id=cluster_id, sample_id=sample_id,
                structure_note=structure_note, revision=revision)


def effect_designs(conn: sqlite3.Connection, result_ids: list[int]) -> list[dict]:
    """Require explicit design for every requested effect, preserving order."""
    if len(set(result_ids)) != len(result_ids) or not result_ids:
        raise ValueError("unique result IDs are required")
    rows = []
    for result_id in result_ids:
        row = conn.execute(
            "SELECT cluster_id,sample_id,structure_note,revision FROM review_effect_design "
            "WHERE result_id=?", (result_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"independent cluster is not recorded for result {result_id}")
        rows.append(dict(zip(("cluster_id", "sample_id", "structure_note", "revision"), row),
                         result_id=result_id))
    return rows


def save_result_moderator(conn: sqlite3.Connection, result_id: int, name: str,
                          value: float | str, value_type: str, source_key: str,
                          source_locator: str, expected_revision: int) -> dict:
    """Append an explicitly sourced result-level covariate revision."""
    import math
    if type(result_id) is not int or result_id <= 0 or type(expected_revision) is not int \
            or expected_revision < 0:
        raise ValueError("positive result ID and nonnegative expected revision required")
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise ValueError("moderator name is required (at most 120 characters)")
    name = name.strip()
    if value_type == "continuous":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("continuous moderator needs a finite number")
    elif value_type == "categorical":
        if not isinstance(value, str) or not value.strip() or len(value) > 300:
            raise ValueError("categorical moderator needs a nonempty level")
        value = value.strip()
    else:
        raise ValueError("moderator type must be continuous or categorical")
    if not isinstance(source_key, str) or not source_key.strip() or \
            not isinstance(source_locator, str) or not source_locator.strip():
        raise ValueError("moderator source report and locator are required")
    effect = conn.execute("SELECT study_id FROM review_effects WHERE result_id=?", (result_id,)).fetchone()
    if effect is None:
        raise ValueError("unknown result ID")
    linked = conn.execute(
        "SELECT 1 FROM review_reports WHERE study_id=? AND zotero_key=?",
        (effect[0], source_key.strip()),
    ).fetchone()
    if linked is None:
        raise ValueError("moderator source report is not linked to this study")
    previous = conn.execute(
        "SELECT revision FROM review_result_moderators WHERE result_id=? AND name=? "
        "ORDER BY revision DESC LIMIT 1", (result_id, name)).fetchone()
    revision = previous[0] if previous else 0
    if revision != expected_revision:
        raise ValueError("result moderator revision conflict")
    cursor = conn.execute(
        "INSERT INTO review_result_moderators "
        "(result_id,name,revision,value_json,value_type,source_key,source_locator,changed_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (result_id, name, revision + 1, json.dumps(value, allow_nan=False), value_type,
         source_key.strip(), source_locator.strip(), datetime.now(timezone.utc).isoformat()))
    return {"moderator_version_id": cursor.lastrowid, "result_id": result_id,
            "name": name, "revision": revision + 1, "value": value,
            "value_type": value_type, "source_key": source_key.strip(),
            "source_locator": source_locator.strip()}


def result_moderators(conn: sqlite3.Connection, result_ids: list[int]) -> list[dict]:
    if not result_ids or len(result_ids) > 500 or len(set(result_ids)) != len(result_ids) \
            or any(type(value) is not int or value <= 0 for value in result_ids):
        raise ValueError("select 1 to 500 unique positive result IDs")
    rows = conn.execute(
        "SELECT moderator_version_id,result_id,name,revision,value_json,value_type,"
        "source_key,source_locator,changed_at FROM review_result_moderators "
        f"WHERE result_id IN ({','.join('?' for _ in result_ids)}) "
        "ORDER BY result_id,name,revision", result_ids).fetchall()
    names = ("moderator_version_id", "result_id", "name", "revision", "value",
             "value_type", "source_key", "source_locator", "changed_at")
    items = [dict(zip(names, row)) for row in rows]
    for item in items:
        item["value"] = json.loads(item["value"])
    return items


def save_analysis_run(conn: sqlite3.Connection, specification: dict,
                      input_snapshot: dict, result: dict, software_version: str) -> str:
    """Persist one completed run; callers must supply the actual input snapshot."""
    run_id = str(uuid.uuid4())
    payload = [json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
               for value in (specification, input_snapshot, result)]
    conn.execute(
        "INSERT INTO analysis_runs VALUES (?,?,?,?,?,?)",
        (run_id, datetime.now(timezone.utc).isoformat(), *payload, software_version),
    )
    return run_id


def load_analysis_run(conn: sqlite3.Connection, run_id: str) -> dict | None:
    row = conn.execute(
        "SELECT run_id,created_at,specification_json,input_snapshot_json,result_json,software_version "
        "FROM analysis_runs WHERE run_id=?", (run_id,),
    ).fetchone()
    if row is None:
        return None
    return dict(run_id=row[0], created_at=row[1], specification=json.loads(row[2]),
                input_snapshot=json.loads(row[3]), result=json.loads(row[4]),
                software_version=row[5])


def create_synthesis_run(db_path, specification: dict, project_id: str,
                         coding_freeze: dict | None = None) -> dict:
    """Fit from one locked, exact selection and persist the fitted result."""
    from contextlib import closing

    from coscreen import __version__, db, review_analysis
    from coscreen.pool_compatibility import require_compatible_effects

    comparison, outcome, timepoint = (specification[key] for key in
                                      ("comparison", "outcome", "timepoint"))
    measure, model, ci_method = (specification[key] for key in
                                 ("measure", "model", "ci_method"))
    if not all(isinstance(value, str) and value.strip() for value in specification.values()):
        raise ValueError("all synthesis fields are required")
    if (measure not in review_analysis.MEASURES or
        model not in {"fixed", "random", "random_pm", "random_reml"} or
        ci_method not in {"normal", "hksj"} or
        (model == "fixed" and ci_method == "hksj")):
        raise ValueError("unsupported measure or model")
    with closing(db._connect(db_path)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        raw = conn.execute(
            "SELECT result_id,study_id,comparison,outcome,timepoint,measure,estimate,se,source_key,"
            "input_json,entry_method,selected,source_locator FROM review_effects "
            "WHERE comparison=? AND outcome=? AND timepoint=? ORDER BY study_id,result_id",
            (comparison, outcome, timepoint),
        ).fetchall()
        groups: dict[str, list[tuple]] = {}
        for row in raw:
            groups.setdefault(row[1], []).append(row)
        if any(sum(row[11] for row in variants) != 1 for variants in groups.values()):
            raise ValueError("each study must have exactly one selected result in this stratum")
        chosen = [row for row in raw if row[11]]
        if len(chosen) < 2:
            raise ValueError("at least two independent studies are required")
        names = ("result_id", "study_id", "comparison", "outcome", "timepoint", "measure",
                 "estimate", "se", "source_key", "input_data", "entry_method", "selected",
                 "source_locator")
        rows = []
        for raw_row in chosen:
            row = dict(zip(names, raw_row))
            row["input_data"] = json.loads(row["input_data"])
            rows.append(row)
        require_compatible_effects(rows, measure)
        if any(row["measure"] != measure for row in rows):
            raise ValueError("selected effect measures do not match the requested measure")
        versions = effect_versions(conn, [row["result_id"] for row in rows])
        article_fields = ("zotero_key", "title", "authors", "journal", "year", "doi", "url")
        source_articles = {}
        for key in sorted({row["source_key"] for row in rows}):
            source = conn.execute(
                "SELECT " + ",".join(article_fields) + " FROM articles WHERE zotero_key=?",
                (key,),
            ).fetchone()
            if source is None:
                raise ValueError(f"source report is missing: {key}")
            source_articles[key] = dict(zip(article_fields, source))
        result = review_analysis._synthesize_rows(rows, measure, model, ci_method)
        run_spec = {**specification, "project_id": project_id,
                    "algorithm_version": "legacy_inverse_variance_v1",
                    "result_selection": "selected_exact_stratum", "exclusion_rules": [],
                    "random_seed": None}
        snapshot = {"effects": versions, "source_articles": source_articles,
                    "n_effects": len(versions),
                    "n_studies": len(groups), "n_clusters": len(groups),
                    "cluster_assumption": "one independent study per selected effect",
                    "coding_freeze": coding_freeze}
        attach_method_sources(result, snapshot, synthesis_method_source_ids(model, ci_method))
        snapshot["data_sha256"] = hashlib.sha256(json.dumps(
            snapshot, ensure_ascii=False, allow_nan=False, sort_keys=True).encode("utf-8")).hexdigest()
        run_id = save_analysis_run(conn, run_spec, snapshot, result, __version__)
    return {"run_id": run_id, "specification": run_spec,
            "input_snapshot": snapshot, "result": result}
