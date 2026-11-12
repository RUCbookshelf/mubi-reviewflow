"""Study-level synthesis data and manual, result-specific bias assessments.

The imported article is a *report*. A study may have several reports, but an
analysis accepts only one selected result per study and outcome/time point.
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter
from contextlib import closing
from datetime import datetime
from pathlib import Path

from coscreen.db import _add_column_if_missing, _connect
from coscreen.measure_registry import MEASURE_REGISTRY
from coscreen.pool_compatibility import NON_DIRECTIONAL_MEASURES, require_compatible_effects

DbPath = str | Path

ROB = {
    "RoB 2": ({"randomization process", "deviations from intended interventions", "missing outcome data", "measurement of outcome", "selection of reported result"},
              {"low", "some concerns", "high"}),
    "ROBINS-I": ({"confounding", "selection of participants", "classification of interventions", "deviations from intended interventions", "missing data", "measurement of outcomes", "selection of reported result"},
                 {"low", "moderate", "serious", "critical", "no information"}),
    "QUADAS-2": ({"patient selection", "index test", "reference standard", "flow and timing",
                  "applicability: patient selection", "applicability: index test",
                  "applicability: reference standard"},
                 {"low", "high", "unclear"}),
}
RATIOS = {"RR", "OR", "HR", "RATE_RATIO"}
SINGLE_GROUP = {"MEAN", "LOGIT_PROP", "LOG_RATE", "SMCR", "SMCC"}
MEASURES = RATIOS | {"RD", "MD", "SMD", "FISHER_Z", "LOG_ROM", "PAIRED_OR", "PAIRED_RD", "PHI", "R_EQUIV_APPROX"} | SINGLE_GROUP

# L-05（review67）：逆方差权重 w=1/se² 的「平方溢出」阈值——任一 w 超过它则
# w²（以及 Σw²）超出 float64 上限。此域由 save_effect 的合法小 se（<~2.7e-78）
# 触发，字面 c=Σw−Σw²/Σw 会因 Σw²=inf 而变负，τ² 被静默钳 0。
_W_SQUARE_OVERFLOW_LIMIT = math.sqrt(sys.float_info.max)


def binary_effect(events_t: int, total_t: int, events_c: int, total_c: int, measure: str) -> dict:
    """Two independent arms; zero event cells require an explicit external decision."""
    if measure not in {"RR", "OR", "RD"}:
        raise ValueError("binary measure must be RR, OR or RD")
    # B08-N4: bool is an int subclass; True passed the isinstance check and was
    # pooled as 1 event. Counts must be genuine ints.
    if any(isinstance(x, bool) or not isinstance(x, int) for x in (events_t, total_t, events_c, total_c)) or \
       not (0 <= events_t <= total_t and 0 <= events_c <= total_c and total_t > 0 and total_c > 0):
        raise ValueError("events must be integers between zero and group total")
    a, b, c, d = events_t, total_t-events_t, events_c, total_c-events_c
    if measure in RATIOS and min(a, b, c, d) == 0:
        raise ValueError("zero cell: choose and document a continuity correction or another method")
    pt, pc = a/total_t, c/total_c
    if measure == "RR":
        estimate = pt/pc
        variance = 1/a - 1/total_t + 1/c - 1/total_c
    elif measure == "OR":
        estimate = a*d/(b*c)
        variance = 1/a + 1/b + 1/c + 1/d
    else:
        estimate = pt-pc
        variance = pt*(1-pt)/total_t + pc*(1-pc)/total_c
    if variance <= 0:
        raise ValueError("zero sampling variance: this result needs a different method")
    return {"measure": measure, "estimate": estimate, "se": math.sqrt(variance),
            "se_scale": "log" if measure in RATIOS else "natural"}


def continuous_effect(n_t: int, mean_t: float, sd_t: float,
                      n_c: int, mean_c: float, sd_c: float, measure: str) -> dict:
    if measure not in {"MD", "SMD"}:
        raise ValueError("continuous measure must be MD or SMD")
    # B08-N4: same bool-as-count guard for the continuous arm sizes.
    if any(isinstance(n, bool) or not isinstance(n, int) or n < 2 for n in (n_t, n_c)) or \
       any(not math.isfinite(x) for x in (mean_t, sd_t, mean_c, sd_c)) or min(sd_t, sd_c) < 0:
        raise ValueError("each arm needs n >= 2, finite mean and nonnegative SD")
    diff = mean_t-mean_c
    # b14·F4-100：:91 的门只做 isfinite，合法有限但极大的臂级 SD（IPD 的
    # ±1.5e308 参与者值经 stdev 得 1.73e308）在下面的 ``sd_t**2`` 处抛
    # OverflowError(34)（CPython 的幂运算先抛错，``x*x`` 才是 inf）。该异常
    # 不在端点 ``except ValueError`` 覆盖内 → HTTP 500。算式逐字保留（正常
    # 输入的估计与 SE 逐位不变），只在外面把溢出翻译成同一条 ValueError 契约；
    # 乘法路径溢出成 inf 的中间量由随后的有限性判定接住。
    try:
        if measure == "MD":
            estimate, variance = diff, sd_t**2/n_t + sd_c**2/n_c
        else:
            total = n_t+n_c
            df = total-2
            pooled_sd = math.sqrt(((n_t-1)*sd_t**2+(n_c-1)*sd_c**2)/df)
            if pooled_sd == 0:
                raise ValueError("SMD needs a positive pooled SD")
            correction = 1-3/(4*df-1)
            d = diff/pooled_sd
            estimate = correction*d
            variance = correction**2 * (total/(n_t*n_c) + d**2/(2*total))
    except OverflowError as exc:
        raise ValueError("arm summaries are too large to represent: the sampling variance "
                         "exceeds the finite range") from exc
    if variance <= 0:
        raise ValueError("zero sampling variance: this result needs a different method")
    return {"measure": measure, "estimate": estimate, "se": math.sqrt(variance), "se_scale": "natural"}


def _linked(conn, study_id: str, source_key: str) -> None:
    if conn.execute("SELECT 1 FROM review_reports WHERE study_id=? AND zotero_key=?",
                    (study_id, source_key)).fetchone() is None:
        raise ValueError("source report must be linked to this study")


def save_study(db: DbPath, study_id: str, label: str, reports: list[str]) -> None:
    study_id, label = study_id.strip(), label.strip()
    if not study_id or not label or not reports:
        raise ValueError("study id, label and at least one report are required")
    if len(set(reports)) != len(reports):
        raise ValueError("duplicate report key")
    with closing(_connect(db)) as conn, conn:
        for key in reports:
            row = conn.execute("SELECT study_id FROM review_reports WHERE zotero_key=?", (key,)).fetchone()
            if row and row[0] != study_id:
                raise ValueError(f"report {key} already linked to another study")
            article = conn.execute("SELECT is_duplicate_of FROM articles WHERE zotero_key=?", (key,)).fetchone()
            if article is None or article[0] is not None:
                raise ValueError(f"report {key} is missing or marked as duplicate")
        old_keys = {row[0] for row in conn.execute(
            "SELECT zotero_key FROM review_reports WHERE study_id=?", (study_id,))}
        removed = old_keys - set(reports)
        source_tables = ["review_effects", "review_rob", "review_dta_results",
                         "review_qual_findings", "review_dose_curves"]
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_binary_arms'").fetchone():
            source_tables.append("review_binary_arms")
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_network_studies'").fetchone():
            source_tables.append("review_network_studies")
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_single_group_counts'").fetchone():
            source_tables.append("review_single_group_counts")
        for key in removed:
            # 表名来自模块内固定枚举 → 逐条字面量选择（标识符不拼接）。
            uses_result = False
            for table in source_tables:
                if table == "review_effects":
                    hit = conn.execute(
                        "SELECT 1 FROM review_effects WHERE study_id=? AND source_key=? LIMIT 1",
                        (study_id, key)).fetchone()
                elif table == "review_rob":
                    hit = conn.execute(
                        "SELECT 1 FROM review_rob WHERE study_id=? AND source_key=? LIMIT 1",
                        (study_id, key)).fetchone()
                elif table == "review_dta_results":
                    hit = conn.execute(
                        "SELECT 1 FROM review_dta_results WHERE study_id=? AND source_key=? LIMIT 1",
                        (study_id, key)).fetchone()
                elif table == "review_qual_findings":
                    hit = conn.execute(
                        "SELECT 1 FROM review_qual_findings WHERE study_id=? AND source_key=? LIMIT 1",
                        (study_id, key)).fetchone()
                elif table == "review_dose_curves":
                    hit = conn.execute(
                        "SELECT 1 FROM review_dose_curves WHERE study_id=? AND source_key=? LIMIT 1",
                        (study_id, key)).fetchone()
                elif table == "review_binary_arms":
                    hit = conn.execute(
                        "SELECT 1 FROM review_binary_arms WHERE study_id=? AND source_key=? LIMIT 1",
                        (study_id, key)).fetchone()
                elif table == "review_network_studies":
                    hit = conn.execute(
                        "SELECT 1 FROM review_network_studies WHERE study_id=? AND source_key=? LIMIT 1",
                        (study_id, key)).fetchone()
                elif table == "review_single_group_counts":
                    hit = conn.execute(
                        "SELECT 1 FROM review_single_group_counts WHERE study_id=? AND source_key=? LIMIT 1",
                        (study_id, key)).fetchone()
                else:
                    hit = None
                if hit is not None:
                    uses_result = True
                    break
            if uses_result:
                raise ValueError(f"report {key} is used by an analysis result; update that source first")
        conn.execute("INSERT INTO review_studies(id,label) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET label=excluded.label",
                     (study_id, label))
        for key in removed:
            conn.execute("DELETE FROM review_reports WHERE zotero_key=?", (key,))
        for key in reports:
            conn.execute("INSERT INTO review_reports(zotero_key,study_id) VALUES (?,?) ON CONFLICT(zotero_key) DO NOTHING",
                         (key, study_id))


def list_studies(db: DbPath) -> list[dict]:
    with closing(_connect(db)) as conn:
        studies = conn.execute("SELECT id,label FROM review_studies ORDER BY id").fetchall()
        return [{"id": sid, "label": label, "reports": [r[0] for r in conn.execute(
            "SELECT zotero_key FROM review_reports WHERE study_id=? ORDER BY zotero_key", (sid,))]}
            for sid, label in studies]


def coding_summary(db: DbPath, dimension_id: int, kind: str = "categorical") -> dict:
    """Summarize a selected characteristic once per linked study.

    Conflicting values across a study's reports are reported, not silently picked.
    """
    if kind not in {"categorical", "numeric"}:
        raise ValueError("kind must be categorical or numeric")
    with closing(_connect(db)) as conn:
        dim = conn.execute("SELECT name,unit FROM coding_dimensions WHERE id=?", (dimension_id,)).fetchone()
        if dim is None:
            raise ValueError("dimension does not exist")
        studies = conn.execute("SELECT id FROM review_studies ORDER BY id").fetchall()
        values, missing, conflicts = [], [], []
        for (sid,) in studies:
            vals = {r[0].strip() for r in conn.execute(
                "SELECT cv.value FROM review_reports rr JOIN coding_values cv ON cv.zotero_key=rr.zotero_key "
                "WHERE rr.study_id=? AND cv.dimension_id=? AND trim(cv.value)<>''", (sid, dimension_id))}
            if len(vals) > 1:
                conflicts.append(sid)
            elif not vals:
                missing.append(sid)
            else:
                values.append((sid, vals.pop()))
    result = {"dimension": dim[0], "unit": dim[1], "kind": kind, "n_studies": len(studies),
              "n_observed": len(values), "missing_studies": missing, "conflicting_studies": conflicts}
    if kind == "categorical":
        result["counts"] = dict(sorted(Counter(v for _, v in values).items()))
        result["percentages"] = {key: count / len(values) * 100 for key, count in result["counts"].items()}
    else:
        numbers = []
        for sid, value in values:
            try:
                number = float(value)
            except ValueError as exc:
                raise ValueError(f"non-numeric value in study {sid}") from exc
            if not math.isfinite(number):
                raise ValueError(f"non-finite value in study {sid}")
            numbers.append(number)
        numbers.sort()
        result.update(n=len(numbers), mean=sum(numbers) / len(numbers) if numbers else None,
                      median=(numbers[(len(numbers)-1)//2] + numbers[len(numbers)//2]) / 2 if numbers else None,
                      minimum=numbers[0] if numbers else None, maximum=numbers[-1] if numbers else None)
        result["sd_sample"] = (math.sqrt(sum((x-result["mean"])**2 for x in numbers)/(len(numbers)-1))
                               if len(numbers) > 1 else None)
    return result


def coding_cross_tab(db: DbPath, dimension_a: int, dimension_b: int) -> dict:
    if dimension_a == dimension_b:
        raise ValueError("cross-tab needs two different dimensions")
    with closing(_connect(db)) as conn:
        names = [conn.execute("SELECT name FROM coding_dimensions WHERE id=?", (d,)).fetchone()
                 for d in (dimension_a, dimension_b)]
        if any(name is None for name in names):
            raise ValueError("dimension does not exist")
        # A study contributes once only when all linked reports agree on each selected value.
        rows = conn.execute("SELECT rr.study_id,cv.dimension_id,cv.value FROM review_reports rr "
                            "JOIN coding_values cv ON cv.zotero_key=rr.zotero_key "
                            "WHERE cv.dimension_id IN (?,?) AND trim(cv.value)<>''",
                            (dimension_a, dimension_b)).fetchall()
        groups: dict[str, dict[int, set[str]]] = {}
        for sid, dim, value in rows:
            groups.setdefault(sid, {}).setdefault(dim, set()).add(value.strip())
        counts: dict[str, dict[str, int]] = {}
        excluded = []
        studies = list_studies(db)
        for study in studies:
            sid = study["id"]
            a, b = (groups.get(sid, {}).get(d, set()) for d in (dimension_a, dimension_b))
            if len(a) != 1 or len(b) != 1:
                excluded.append(sid)
                continue
            av, bv = next(iter(a)), next(iter(b))
            counts.setdefault(av, {})[bv] = counts.setdefault(av, {}).get(bv, 0) + 1
    return {"dimensions": [names[0][0], names[1][0]], "counts": counts,
            "n_studies": len(studies), "excluded_studies": excluded}


def year_distribution(db: DbPath, dimension_id: int) -> dict:
    summary = coding_summary(db, dimension_id, "categorical")
    counts = {}
    for value, count in summary["counts"].items():
        if not (len(value) == 4 and value.isascii() and value.isdecimal()):
            raise ValueError("study-year dimension must contain four-digit years")
        counts[int(value)] = count
    return {"dimension": summary["dimension"], "counts": dict(sorted(counts.items())),
            "n_studies": summary["n_studies"], "missing_studies": summary["missing_studies"],
            "conflicting_studies": summary["conflicting_studies"]}


def _ensure_rob_locator(conn) -> None:
    _add_column_if_missing(conn, "review_rob", "source_locator",
                           "ALTER TABLE review_rob ADD COLUMN source_locator TEXT NOT NULL DEFAULT ''")


def save_rob(db: DbPath, study_id: str, outcome: str, timepoint: str, framework: str,
             reviewer: str, domains: dict, overall: str, source_key: str,
             comparison: str = "", overall_rationale: str = "", source_locator: str = "") -> None:
    if framework not in ROB:
        raise ValueError("unsupported risk-of-bias framework")
    allowed_domains, allowed_judgements = ROB[framework]
    if not all((study_id, outcome.strip(), timepoint.strip(), reviewer.strip(), domains, overall, source_key)):
        raise ValueError("all result, reviewer, judgement and source fields are required")
    if overall not in allowed_judgements:
        raise ValueError("invalid overall judgement")
    if not overall_rationale.strip():
        raise ValueError("overall rationale is required")
    for name, item in domains.items():
        if name not in allowed_domains or not isinstance(item, dict) or item.get("judgement") not in allowed_judgements:
            raise ValueError(f"invalid domain judgement: {name}")
        if not str(item.get("rationale", "")).strip():
            raise ValueError(f"domain rationale is required: {name}")
    if set(domains) != allowed_domains:
        raise ValueError("all framework domains are required for a completed assessment")
    with closing(_connect(db)) as conn, conn:
        _ensure_rob_locator(conn)
        _linked(conn, study_id, source_key)
        conn.execute("INSERT INTO review_rob "
                     "(study_id,comparison,outcome,timepoint,framework,reviewer,domains_json,overall,"
                     "overall_rationale,source_key,source_locator,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                     "ON CONFLICT(study_id,comparison,outcome,timepoint,framework,reviewer) DO UPDATE SET "
                     "domains_json=excluded.domains_json,overall=excluded.overall,overall_rationale=excluded.overall_rationale,"
                     "source_locator=CASE WHEN source_key=excluded.source_key "
                     "THEN COALESCE(NULLIF(excluded.source_locator,''),source_locator) ELSE excluded.source_locator END,"
                     "source_key=excluded.source_key,updated_at=excluded.updated_at",
                     (study_id, comparison.strip(), outcome.strip(), timepoint.strip(), framework, reviewer.strip(),
                      json.dumps(domains, ensure_ascii=False), overall, overall_rationale.strip(),
                      source_key, source_locator.strip(), datetime.now().isoformat(timespec="seconds")))


def list_rob(db: DbPath, study_id: str | None = None, outcome: str | None = None) -> list[dict]:
    clauses = []
    args = []
    if study_id is not None:
        clauses.append("study_id=?"); args.append(study_id)
    if outcome is not None:
        clauses.append("outcome=?"); args.append(outcome)
    with closing(_connect(db)) as conn, conn:
        _ensure_rob_locator(conn)
        rows = conn.execute(
            "".join((
                "SELECT study_id,comparison,outcome,timepoint,framework,reviewer,domains_json,"
                "overall,overall_rationale,source_key,source_locator,updated_at FROM review_rob WHERE 1=1",
                " AND " if clauses else "",
                " AND ".join(clauses),
                " ORDER BY study_id,comparison,outcome,timepoint,framework,reviewer",
            )),
            args,
        ).fetchall()
    keys = ("study_id", "comparison", "outcome", "timepoint", "framework", "reviewer", "domains", "overall", "overall_rationale", "source_key", "source_locator", "updated_at")
    return [dict(zip(keys, (*r[:6], json.loads(r[6]), *r[7:]))) for r in rows]


def save_effect(db: DbPath, study_id: str, comparison: str, outcome: str, timepoint: str,
                measure: str, estimate: float, se: float, source_key: str,
                input_data: dict | None = None, entry_method: str = "manual", *,
                append: bool = False, source_locator: str = "") -> int:
    if measure not in MEASURES or not all((comparison.strip(), outcome.strip(), timepoint.strip())):
        raise ValueError("measure, comparison, outcome and timepoint are required")
    if not math.isfinite(estimate) or not math.isfinite(se) or se <= 0 or (measure in RATIOS and estimate <= 0):
        raise ValueError("estimate and SE must be finite; SE and ratio estimates must be positive")
    if measure in {"LOG_RATE", "LOG_ROM", "PAIRED_OR"} and not -745 < estimate < 709:
        raise ValueError("log rate or ratio is outside the representable positive display range")
    if measure in {"PHI", "R_EQUIV_APPROX"} and not -1 < estimate < 1:
        raise ValueError("binary correlation estimate must be strictly between -1 and 1")
    if measure == "SMD":
        if input_data is None:
            input_data = {}
        elif isinstance(input_data, dict):
            input_data = dict(input_data)
        else:
            input_data = {"legacy_input_data": input_data}
        metadata = input_data.get("estimator_metadata")
        if not isinstance(metadata, dict) or not isinstance(metadata.get("estimator"), str):
            from coscreen.smd_contract import legacy_smd_metadata

            input_data["estimator_metadata"] = legacy_smd_metadata(entry_method, input_data)
    with closing(_connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        _linked(conn, study_id, source_key)
        stratum = (study_id, comparison.strip(), outcome.strip(), timepoint.strip())
        saved = conn.execute(
            "SELECT result_id,selected FROM review_effects WHERE study_id=? AND comparison=? AND outcome=? AND timepoint=?",
            stratum,
        ).fetchall()
        payload = (measure, estimate, se, source_key, json.dumps(input_data or {}, ensure_ascii=False),
                   entry_method, source_locator.strip())
        if append:
            selected = int(not saved)
            cursor = conn.execute(
                "INSERT INTO review_effects "
                "(study_id,comparison,outcome,timepoint,measure,estimate,se,source_key,input_json,entry_method,selected,source_locator) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (*stratum, *payload[:6], selected, payload[6]),
            )
            return cursor.lastrowid
        selected_ids = [result_id for result_id, is_selected in saved if is_selected]
        if len(selected_ids) > 1:
            raise ValueError("multiple selected effects exist for this study/comparison/outcome/timepoint")
        if saved and not selected_ids:
            raise ValueError("saved result variants have no selected effect; select a result before updating")
        if selected_ids:
            result_id = selected_ids[0]
            conn.execute(
                "UPDATE review_effects SET measure=?,estimate=?,se=?,source_key=?,input_json=?,entry_method=?,"
                "source_locator=CASE WHEN source_key=? THEN COALESCE(NULLIF(?,''),source_locator) ELSE ? END "
                "WHERE result_id=?",
                (*payload[:6], source_key, payload[6], payload[6], result_id),
            )
            return result_id
        cursor = conn.execute(
            "INSERT INTO review_effects "
            "(study_id,comparison,outcome,timepoint,measure,estimate,se,source_key,input_json,entry_method,selected,source_locator) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,1,?)",
            (*stratum, *payload[:6], payload[6]),
        )
        return cursor.lastrowid


def save_arm_effect(db: DbPath, study_id: str, comparison: str, outcome: str, timepoint: str,
                    measure: str, source_key: str, arms: dict, *,
                    append: bool = False, source_locator: str = "", effect_direction: str | None = None) -> int:
    binary = measure in {"RR", "OR", "RD"}
    keys = ("events_t", "total_t", "events_c", "total_c") if binary else \
           ("n_t", "mean_t", "sd_t", "n_c", "mean_c", "sd_c")
    if measure not in {"RR", "OR", "RD", "MD", "SMD"} or set(arms) != set(keys):
        raise ValueError("arm inputs do not match the selected measure")
    result = binary_effect(*(arms[k] for k in keys), measure) if binary else \
             continuous_effect(*(arms[k] for k in keys), measure)
    return save_effect(db, study_id, comparison, outcome, timepoint, measure, result["estimate"],
                       result["se"], source_key, {**arms, **({"effect_direction": effect_direction} if effect_direction else {})},
                       "binary arms" if binary else "continuous arms",
                       append=append, source_locator=source_locator)


def list_effects(db: DbPath) -> list[dict]:
    with closing(_connect(db)) as conn:
        rows = conn.execute(
            "SELECT result_id,study_id,comparison,outcome,timepoint,measure,estimate,se,source_key,input_json,"
            "entry_method,selected,source_locator FROM review_effects "
            "ORDER BY study_id,comparison,outcome,timepoint,result_id"
        ).fetchall()
    keys = ("result_id", "study_id", "comparison", "outcome", "timepoint", "measure", "estimate", "se",
            "source_key", "input_data", "entry_method", "selected", "source_locator")
    return [dict(zip(keys, (*row[:9], json.loads(row[9]), *row[10:]))) for row in rows]


def select_effect(db: DbPath, result_id: int) -> int:
    """Atomically select one saved result within its study and analysis stratum."""
    with closing(_connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        target = conn.execute(
            "SELECT study_id,comparison,outcome,timepoint FROM review_effects WHERE result_id=?", (result_id,)
        ).fetchone()
        if target is None:
            raise ValueError("effect result does not exist")
        conn.execute(
            "UPDATE review_effects SET selected=0 WHERE study_id=? AND comparison=? AND outcome=? AND timepoint=?",
            target,
        )
        conn.execute("UPDATE review_effects SET selected=1 WHERE result_id=?", (result_id,))
    return result_id


def confirm_effect_direction(db: DbPath, result_id: int, effect_direction: str,
                             source_locator: str = "", *, confirmed_by: str | None = None,
                             confirmed_at: str | None = None) -> int:
    """Confirm direction on a legacy effect without changing its estimate or SE."""
    with closing(_connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT input_json,source_locator,measure FROM review_effects WHERE result_id=?", (result_id,)
        ).fetchone()
        if row is None:
            raise ValueError("effect result does not exist")
        input_data = json.loads(row[0] or "{}")
        if not isinstance(input_data, dict):
            raise ValueError("effect input data is invalid")
        if "effect_direction" in input_data:
            raise ValueError("effect already has an effect direction")
        if row[2] in NON_DIRECTIONAL_MEASURES:
            raise ValueError("effect measure is nondirectional")
        if effect_direction not in {"first_vs_second", "second_vs_first"}:
            raise ValueError("effect direction must be first_vs_second or second_vs_first")
        locator = source_locator.strip() or (row[1] or "").strip()
        if not locator:
            raise ValueError("a nonempty source locator is required")
        input_data.update(effect_direction=effect_direction, direction_source_checked=True)
        if confirmed_by is not None:
            input_data["direction_source_checked_by"] = confirmed_by
        if confirmed_at is not None:
            input_data["direction_source_checked_at"] = confirmed_at
        conn.execute(
            "UPDATE review_effects SET input_json=?,source_locator=? WHERE result_id=?",
            (json.dumps(input_data, ensure_ascii=False), locator, result_id),
        )
    return result_id


def _reject_shared_recorded_design(recorded_designs: list[tuple]) -> None:
    """选层「已登记 cluster/sample 互异」独立性守卫（review67 M-03 双路径复用）。

    selected_effects（读/synthesize 路径）与 analysis_storage.create_synthesis_run
    （合成运行持久化路径）必须共用同一判定：此前只有读路径生效，同一选层
    GET 400 而 POST 落库成功，且落库快照写死独立性声明，形成读写不一致。
    """
    # ponytail: this catches only explicitly recorded sample overlap; detecting unrecorded overlap needs adjudicated sample IDs.
    for column in (0, 1):
        values = [row[column] for row in recorded_designs]
        if len(values) != len(set(values)):
            raise ValueError(
                "selected effects share a recorded cluster or sample; use a dependent-effects analysis"
            )


def selected_effects(db: DbPath, comparison: str, outcome: str, timepoint: str,
                     measure: str | None = None) -> list[dict]:
    """Return one explicitly selected result per independent study in an exact stratum."""
    with closing(_connect(db)) as conn:
        counts = conn.execute(
            "SELECT study_id,COUNT(*),SUM(selected) FROM review_effects "
            "WHERE comparison=? AND outcome=? AND timepoint=? GROUP BY study_id ORDER BY study_id",
            (comparison, outcome, timepoint),
        ).fetchall()
        for study_id, _, n_selected in counts:
            if n_selected == 0:
                raise ValueError(f"study {study_id} has result variants but no selected effect")
            if n_selected != 1:
                raise ValueError(f"study {study_id} has multiple selected effects in this stratum")
        rows = conn.execute(
            "SELECT result_id,study_id,comparison,outcome,timepoint,measure,estimate,se,source_key,input_json,"
            "entry_method,selected,source_locator FROM review_effects "
            "WHERE comparison=? AND outcome=? AND timepoint=? AND selected=1 ORDER BY study_id",
            (comparison, outcome, timepoint),
        ).fetchall()
        recorded_designs = conn.execute(
            "SELECT d.cluster_id,d.sample_id FROM review_effect_design d "
            "JOIN review_effects e ON e.result_id=d.result_id "
            "WHERE e.comparison=? AND e.outcome=? AND e.timepoint=? AND e.selected=1",
            (comparison, outcome, timepoint),
        ).fetchall()
    # ponytail 守卫语义原样迁入 _reject_shared_recorded_design（review67 M-03
    # 提取为双路径共用），本读路径与 create_synthesis_run 持久化路径同判定。
    _reject_shared_recorded_design(recorded_designs)
    keys = ("result_id", "study_id", "comparison", "outcome", "timepoint", "measure", "estimate", "se",
            "source_key", "input_data", "entry_method", "selected", "source_locator")
    effects = [dict(zip(keys, (*row[:9], json.loads(row[9]), *row[10:]))) for row in rows]
    require_compatible_effects(effects, measure)
    if measure is not None and any(row["measure"] != measure for row in effects):
        raise ValueError(f"incompatible selected effect measures for this comparison/outcome/timepoint; expected {measure}")
    return effects


def synthesize(db: DbPath, comparison: str, outcome: str, timepoint: str, measure: str,
               model: str = "fixed", ci_method: str = "normal") -> dict:
    """Inverse-variance synthesis; DL random effects, normal 95% CI.

    The caller must select a common outcome, time point, comparison and measure.
    The input SE is on the log scale for RR/OR, natural scale otherwise.
    """
    if measure not in MEASURES or model not in {"fixed", "random", "random_pm", "random_reml"} or \
       ci_method not in {"normal", "hksj"} or (model == "fixed" and ci_method == "hksj"):
        raise ValueError("unsupported measure or model")
    rows = selected_effects(db, comparison, outcome, timepoint, measure)
    if len(rows) < 2:
        raise ValueError("at least two independent studies are required")
    return _synthesize_rows(rows, measure, model, ci_method)


def effect_scale_label(measure: str) -> str:
    """效应量的纯分析尺度标签（MM5-08/09 公共口径；MM9-14 收敛词表）。

    词表对照 ``measure_registry`` 的 ``scale`` 字段（'log' | 'natural' |
    'fisher_z' | 'logit'）：只描述数值所在的**分析尺度**，不携带任何展示
    变换声明。``"; results shown as X"`` 后缀描述的是合成路径独有的显示
    变换（FISHER_Z→tanh、LOG_*→exp、LOGIT_PROP→expit，见
    ``_synthesize_rows``），由 :func:`_synthesis_effect_scale_label` 仅在
    合成结果里拼接；meta 回归与漏斗路径（``advanced_analysis``）从不套用
    该变换，直接用本函数，不得再出现该后缀（MM9-14：曾把合成后缀原样接
    到这两条路径，导出 JSON 声称 z/log 数值「shown as r / ratio of means」）。
    """
    return MEASURE_REGISTRY.get(measure, {}).get("scale", "natural")


# 合成路径（_synthesize_rows）的展示口径（MM5-08 历史字面；MM9-14 起后缀
# 只在此拼接）：后缀声明的显示变换只作用于合成结果的 pooled/ci/prediction
# 展示值。分析尺度词沿用历史展示字面（"Fisher z" 带空格），保证合成 JSON
# 的 effect_scale 字面不因 MM9-14 收敛词表而变动（防回归钉子锁死旧字面）。
_SYNTHESIS_SHOWN_AS_SUFFIX = {
    "FISHER_Z": "; results shown as r",
    "LOGIT_PROP": "; results shown as proportion",
    "LOG_RATE": "; results shown as rate",
    "LOG_ROM": "; results shown as ratio of means",
    "PAIRED_OR": "; results shown as matched-pair odds ratio",
}
_SYNTHESIS_SCALE_DISPLAY = {"fisher_z": "Fisher z"}


def _synthesis_effect_scale_label(measure: str) -> str:
    """合成结果的 effect_scale：纯分析尺度 + 展示变换声明（仅此路径）。"""
    scale = effect_scale_label(measure)
    return (_SYNTHESIS_SCALE_DISPLAY.get(scale, scale)
            + _SYNTHESIS_SHOWN_AS_SUFFIX.get(measure, ""))


def _screened_inverse_variance_weights(rows: list[dict]) -> list[float]:
    """Inverse-variance weights ``1/se²`` with degenerate-SE screening (MM10-01).

    Two degenerate bands escape the ValueError→4xx API contract as raw 500s:
    ``se ** 2`` raises OverflowError for se ≳ 1.34e154, and underflows to an
    exact 0.0 for se ≲ 2.2e-162 making ``1/se**2`` a ZeroDivisionError. Neither
    is a ValueError, so the read endpoints' ``except ValueError`` never fires.
    Screen them here — the same "supported numeric range" rejection
    publication_bias already applies to its precisions — and also reject
    non-finite/non-positive SEs (the write side, save_effect, already refuses
    those; this is the read-side backstop of the same contract, not new
    strictness against legal inputs). The subnormal band in between
    (e.g. se=1e-160 → 1/se² = inf → downstream NaN → null) is deliberately
    passed through: that is the known MM5-05 200+null behavior. The weight
    expression stays literally ``1/se**2`` so legal inputs keep bit-identical
    results.
    """
    weights = []
    for row in rows:
        se = row["se"]
        if not math.isfinite(se) or se <= 0:
            raise ValueError("every study SE must be finite and positive for inverse-variance weighting")
        try:
            weights.append(1 / se**2)
        except (OverflowError, ZeroDivisionError) as exc:
            raise ValueError("study standard errors are outside the supported numeric range "
                             "for inverse-variance weighting") from exc
    return weights


def _synthesize_rows(rows: list[dict], measure: str, model: str, ci_method: str) -> dict:
    require_compatible_effects(rows, measure)
    from scipy.stats import chi2, norm, t  # statistical dependency, installed with statsmodels

    y = [math.log(r["estimate"]) if measure in RATIOS else r["estimate"] for r in rows]
    w = _screened_inverse_variance_weights(rows)
    fixed = sum(a*b for a, b in zip(w, y)) / sum(w)
    q = sum(a*(b-fixed)**2 for a, b in zip(w, y))
    df = len(rows) - 1
    c = sum(w) - sum(a*a for a in w) / sum(w)
    # L-05（review67）：权重极端悬殊（任一 w>√float_max≈1.34e154，即 se<~2.7e-78
    # 的荒谬域，save_effect 合法输入）时 Σw² 溢出为 inf，上面的字面式把 c 变成
    # -inf，τ² 被静默钳 0——实测双研究 se=1e-80 输出 i2=100% 但 CI 宽为 0 的自
    # 相矛盾结果。此时改用数学等价的成对式 c = 2·Σw·Σᵢ<ⱼ rᵢrⱼ（r=w/Σw）按可
    # 用精度重算（各 r≤1，成对积求和既不溢出也不吞掉小权重对应的 catastrophic
    # cancellation）；正常域仍走字面式，合法输入逐位不变（MM10-01 口径）。
    if not math.isfinite(c):
        total_w = sum(w)
        shares = [weight / total_w for weight in w]
        pair_sum = sum(shares[i] * shares[j]
                       for i in range(len(shares)) for j in range(i + 1, len(shares)))
        c = 2.0 * total_w * pair_sum
    tau2 = max(0.0, (q-df)/c) if c > 0 else 0.0
    if model == "random_pm":
        import numpy as np
        from statsmodels.stats.meta_analysis import combine_effects
        variances = [r["se"]**2 for r in rows]
        if max(w) > _W_SQUARE_OVERFLOW_LIMIT:
            # L-05（review67）：同域下 statsmodels 的 Paule-Mandel 不可用——固定
            # 效应预计算 1/v 的平方溢出为 inf（RuntimeWarning 后静默 τ²=0），而
            # 其 Newton 迭代的导数项 w² 在 subnormal 域精度崩塌、步长过冲真解
            # 后按 ee<0 截 0（缩放重调亦复现，实测停在 2^50 或返回 0）。PM 的
            # 估计方程 q(τ²)=Σ(yᵢ−μ̂(τ²))²/(vᵢ+τ²)=k−1 本身各项数值健康且对
            # τ² 单调减，故在此域改用 brentq 求同方程的根——与正常域 statsmodels
            # 的 "iterated"（DerSimonian-Kacker 2007 App.8 迭代解同一方程）是
            # 同一估计量；正常域保持既有 statsmodels 调用不变。
            k = len(y)

            def pm_equation(tau2_value: float) -> float:
                weights_re = [1.0 / (v_i + tau2_value) for v_i in variances]
                total = sum(weights_re)
                mean_re = sum(w_i * y_i for w_i, y_i in zip(weights_re, y)) / total
                return sum(w_i * (y_i - mean_re) ** 2
                           for w_i, y_i in zip(weights_re, y)) - (k - 1)

            if q - df <= 0:
                tau2 = 0.0  # PM 边界解，与 statsmodels 的 ee<0 截 0 同语义
            else:
                from scipy.optimize import brentq
                upper = 2.0 * max(tau2, max(variances))
                while pm_equation(upper) > 0:
                    upper *= 2.0
                    if not math.isfinite(upper):
                        raise ValueError("study standard errors are outside the supported "
                                         "numeric range for the Paule-Mandel estimator")
                tau2 = float(brentq(pm_equation, 0.0, upper))
            if not math.isfinite(tau2):
                raise ValueError("study standard errors are outside the supported numeric "
                                 "range for the Paule-Mandel estimator")
        else:
            tau2 = float(combine_effects(np.asarray(y), np.asarray(variances),
                                         method_re="iterated").tau2)
    elif model == "random_reml":
        from coscreen.meta_regression_model import profile_reml_tau2
        tau2, _ = profile_reml_tau2(y, [r["se"]**2 for r in rows])
    i2 = max(0.0, (q-df)/q) * 100 if q > 0 else 0.0
    weights = w if model == "fixed" else [1/(r["se"]**2+tau2) for r in rows]
    pooled = sum(a*b for a, b in zip(weights, y)) / sum(weights)
    pooled_se = math.sqrt(1/sum(weights))
    if ci_method == "hksj":
        scale = sum(a*(b-pooled)**2 for a, b in zip(weights, y))/df
        pooled_se *= math.sqrt(max(1.0, scale))
        critical = float(t.ppf(.975, df))
    else:
        critical = float(norm.ppf(.975)) if model == "random_reml" else 1.96
    if measure == "LOGIT_PROP":
        from scipy.special import expit
        transform = lambda value: float(expit(value))
    else:
        transform = math.exp if measure in RATIOS | {"LOG_RATE", "LOG_ROM", "PAIRED_OR"} else math.tanh if measure == "FISHER_Z" else float

    def display(value: float) -> float | None:
        # 大 SE 的 log 类指标置信界可超出 exp 的可表示范围（>709）；
        # 展示层降级为 None + warning，而不是让 API 抛 OverflowError 500。
        try:
            out = transform(value)
        except OverflowError:
            return None
        return out if math.isfinite(out) else None

    prediction = None
    prediction_analysis = None
    if model != "fixed" and len(rows) >= 3:
        spread = float(t.ppf(.975, len(rows)-2)) * math.sqrt(tau2 + pooled_se**2)
        prediction_analysis = [pooled-spread, pooled+spread]
        prediction = [display(value) for value in prediction_analysis]
    warnings = ["Random-effects uncertainty may be understated with few studies; check a Hartung-Knapp sensitivity analysis."] if model != "fixed" and ci_method == "normal" and len(rows) < 10 else []
    if measure not in NON_DIRECTIONAL_MEASURES:
        unknown = [row["study_id"] for row in rows if not isinstance(row.get("input_data"), dict)
                   or not row["input_data"].get("effect_direction")]
        if unknown:
            warnings.append("Effect direction is unconfirmed for: " + ", ".join(unknown) +
                            ". Check each source against Comparison before interpreting this pool.")
    ci_low_analysis, ci_high_analysis = pooled-critical*pooled_se, pooled+critical*pooled_se
    pooled_display = display(pooled)
    ci_low_display, ci_high_display = display(ci_low_analysis), display(ci_high_analysis)
    if any(value is None for value in
           (pooled_display, ci_low_display, ci_high_display)) or (
            prediction is not None and any(value is None for value in prediction)):
        warnings.append("A pooled estimate or confidence/prediction limit is outside the "
                        "representable display range for this measure; analysis-scale "
                        "values remain valid.")
    return {"n_studies": len(rows), "study_ids": [r["study_id"] for r in rows],
            "measure": measure, "model": model,
            "method": {"fixed":"inverse variance", "random":"DerSimonian-Laird inverse variance",
                       "random_pm":"Paule-Mandel inverse variance",
                       "random_reml":"restricted maximum likelihood inverse variance"}[model],
            "ci_method": "modified HKSJ 95%" if ci_method == "hksj" else "normal 95%",
            "effect_scale": _synthesis_effect_scale_label(measure),
            "pooled_analysis": pooled,
            "ci_analysis_low": ci_low_analysis,
            "ci_analysis_high": ci_high_analysis,
            "prediction_interval_analysis_95": prediction_analysis,
            "pooled": pooled_display, "ci_low": ci_low_display,
            "ci_high": ci_high_display,
            "prediction_interval_95": prediction, "q": q, "q_df": df,
            "q_p": float(chi2.sf(q, df)),
            "i2_percent": i2, "tau2": tau2 if model != "fixed" else None,
            "warnings": warnings,
            "effects": rows}


def leave_one_out(db: DbPath, comparison: str, outcome: str, timepoint: str, measure: str,
                  model: str = "fixed", ci_method: str = "normal") -> list[dict]:
    rows = selected_effects(db, comparison, outcome, timepoint, measure)
    if len(rows) < 3:
        raise ValueError("leave-one-out requires at least three independent studies")
    return [{"excluded_study": row["study_id"], "excluded_result_id": row["result_id"],
             "excluded_source_key": row["source_key"],
             "excluded_source_locator": row["source_locator"],
             **_synthesize_rows(rows[:i]+rows[i+1:], measure, model, ci_method)}
            for i, row in enumerate(rows)]
