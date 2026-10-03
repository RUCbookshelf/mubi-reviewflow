"""Human-reviewed qualitative meta-aggregation with a complete source chain."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime
from pathlib import Path

from coscreen.db import _connect
from coscreen.review_analysis import _linked

DbPath = str | Path
CREDIBILITY = {"unequivocal", "credible", "unsupported"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def add_finding(db: DbPath, study_id: str, source_key: str, finding: str,
                illustration: str, locator: str, credibility: str, reviewer: str) -> int:
    if credibility not in CREDIBILITY:
        raise ValueError("invalid credibility judgement")
    if not all((study_id.strip(), source_key.strip(), finding.strip(), reviewer.strip())):
        raise ValueError("study, source, finding and reviewer are required")
    if credibility != "unsupported" and not (illustration.strip() and locator.strip()):
        raise ValueError("supported findings require a source illustration and locator")
    with closing(_connect(db)) as conn, conn:
        _linked(conn, study_id, source_key)
        cur = conn.execute("INSERT INTO review_qual_findings "
                           "(study_id,source_key,finding,illustration,locator,credibility,reviewer,created_at) "
                           "VALUES (?,?,?,?,?,?,?,?)",
                           (study_id, source_key, finding.strip(), illustration.strip(), locator.strip(),
                            credibility, reviewer.strip(), _now()))
        return int(cur.lastrowid)


def list_findings(db: DbPath) -> list[dict]:
    keys = ("id", "study_id", "source_key", "finding", "illustration", "locator",
            "credibility", "reviewer", "created_at")
    with closing(_connect(db)) as conn:
        rows = conn.execute("SELECT id,study_id,source_key,finding,illustration,locator,credibility,"
                            "reviewer,created_at FROM review_qual_findings ORDER BY id").fetchall()
    return [dict(zip(keys, row)) for row in rows]


def add_category(db: DbPath, label: str, finding_ids: list[int], reviewer: str) -> int:
    if not label.strip() or not reviewer.strip() or not finding_ids:
        raise ValueError("category label, reviewer and findings are required")
    if len(set(finding_ids)) != len(finding_ids):
        raise ValueError("duplicate finding")
    with closing(_connect(db)) as conn, conn:
        marks = ",".join("?" for _ in finding_ids)
        found = conn.execute(f"SELECT id,credibility FROM review_qual_findings WHERE id IN ({marks})",
                             finding_ids).fetchall()
        if len(found) != len(finding_ids):
            raise ValueError("finding does not exist")
        if any(credibility == "unsupported" for _, credibility in found):
            raise ValueError("unsupported findings cannot enter meta-aggregation")
        cur = conn.execute("INSERT INTO review_qual_categories(label,reviewer,created_at) VALUES (?,?,?)",
                           (label.strip(), reviewer.strip(), _now()))
        category_id = int(cur.lastrowid)
        conn.executemany("INSERT INTO review_qual_category_findings VALUES (?,?)",
                         [(category_id, fid) for fid in finding_ids])
        return category_id


def list_categories(db: DbPath) -> list[dict]:
    with closing(_connect(db)) as conn:
        rows = conn.execute("SELECT id,label,reviewer,created_at FROM review_qual_categories ORDER BY id").fetchall()
        return [{"id": cid, "label": label, "reviewer": reviewer, "created_at": created,
                 "finding_ids": [r[0] for r in conn.execute(
                     "SELECT finding_id FROM review_qual_category_findings WHERE category_id=? ORDER BY finding_id",
                     (cid,))]} for cid, label, reviewer, created in rows]


def add_synthesis(db: DbPath, finding: str, category_ids: list[int], reviewer: str) -> int:
    if not finding.strip() or not reviewer.strip() or not category_ids:
        raise ValueError("synthesized finding, reviewer and categories are required")
    if len(set(category_ids)) != len(category_ids):
        raise ValueError("duplicate category")
    with closing(_connect(db)) as conn, conn:
        marks = ",".join("?" for _ in category_ids)
        rows = conn.execute(f"SELECT id FROM review_qual_categories WHERE id IN ({marks})",
                            category_ids).fetchall()
        if len(rows) != len(category_ids):
            raise ValueError("category does not exist")
        cur = conn.execute("INSERT INTO review_qual_syntheses(finding,reviewer,created_at) VALUES (?,?,?)",
                           (finding.strip(), reviewer.strip(), _now()))
        synthesis_id = int(cur.lastrowid)
        conn.executemany("INSERT INTO review_qual_synthesis_categories VALUES (?,?)",
                         [(synthesis_id, cid) for cid in category_ids])
        return synthesis_id


def list_syntheses(db: DbPath) -> list[dict]:
    with closing(_connect(db)) as conn:
        rows = conn.execute("SELECT id FROM review_qual_syntheses ORDER BY id").fetchall()
    return [trace_synthesis(db, sid) for (sid,) in rows]


def trace_synthesis(db: DbPath, synthesis_id: int) -> dict:
    with closing(_connect(db)) as conn:
        row = conn.execute("SELECT finding,reviewer,created_at FROM review_qual_syntheses WHERE id=?",
                           (synthesis_id,)).fetchone()
        if row is None:
            raise ValueError("synthesis does not exist")
        categories = conn.execute("SELECT c.id,c.label FROM review_qual_categories c "
                                  "JOIN review_qual_synthesis_categories sc ON sc.category_id=c.id "
                                  "WHERE sc.synthesis_id=? ORDER BY c.id", (synthesis_id,)).fetchall()
        findings = conn.execute("SELECT DISTINCT f.id,f.study_id,f.source_key,f.finding,f.illustration,"
                                "f.locator,f.credibility,f.reviewer FROM review_qual_findings f "
                                "JOIN review_qual_category_findings cf ON cf.finding_id=f.id "
                                "JOIN review_qual_synthesis_categories sc ON sc.category_id=cf.category_id "
                                "WHERE sc.synthesis_id=? ORDER BY f.id", (synthesis_id,)).fetchall()
    keys = ("id", "study_id", "source_key", "finding", "illustration", "locator", "credibility", "reviewer")
    return {"id": synthesis_id, "finding": row[0], "reviewer": row[1], "created_at": row[2],
            "categories": [{"id": cid, "label": label} for cid, label in categories],
            "findings": [dict(zip(keys, r)) for r in findings]}
