"""Append-only resolution of conflicts from independent screening exports.

This operates on the merged snapshot and a task-scoped SQLite file. It never
writes back to any screener's personal ``decisions`` or ``stage2_decisions``.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from coscreen.config import DECISIONS
from coscreen.stats.merge import merge_decisions_multi

StrPath = str | Path

_ARBITRATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS screening_arbitrations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  stage TEXT NOT NULL CHECK(stage IN ('stage1', 'stage2')),
  merge_sha256 TEXT NOT NULL CHECK(length(merge_sha256) = 64),
  result_sha256 TEXT NOT NULL CHECK(length(result_sha256) = 64),
  supersedes_id INTEGER REFERENCES screening_arbitrations(id),
  arbitrator_id TEXT NOT NULL,
  records_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(stage, merge_sha256, result_sha256)
);
CREATE TRIGGER IF NOT EXISTS screening_arbitrations_no_update
BEFORE UPDATE ON screening_arbitrations
BEGIN SELECT RAISE(ABORT, 'screening arbitrations are append-only'); END;
CREATE TRIGGER IF NOT EXISTS screening_arbitrations_no_delete
BEFORE DELETE ON screening_arbitrations
BEGIN SELECT RAISE(ABORT, 'screening arbitrations are append-only'); END;
"""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _decision_package_sha256(frame: pd.DataFrame) -> str:
    """Fingerprint exported decision fields so a renamed duplicate upload is rejected."""
    required = {"zotero_key", "decision"}
    if not required <= set(frame.columns):
        raise ValueError("决策包必须包含 zotero_key 和 decision 列")
    columns = ("zotero_key", "decision", "exclusion_reason", "notes", "tags", "screened_at")
    records = []
    for row in frame.to_dict("records"):
        record = {name: "" if pd.isna(row.get(name, "")) else str(row.get(name, "")) for name in columns}
        record["decision"] = record["decision"].strip().lower()
        records.append(record)
    records.sort(key=lambda row: row["zotero_key"])
    return _sha256(records)


def merge_decision_packages(named_frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Merge 2–9 reviewer exports, rejecting exact duplicate packages first."""
    if not 2 <= len(named_frames) <= 9:
        raise ValueError("请提供 2 到 9 份筛选员决策包")
    fingerprints: dict[str, str] = {}
    normalized: dict[str, pd.DataFrame] = {}
    for name, frame in named_frames.items():
        if not isinstance(name, str):
            raise ValueError("筛选员名称必须为文本")
        label = name.strip()
        if not label or len(label) > 100 or label in normalized:
            raise ValueError("筛选员名称必须为 1 到 100 个字符")
        fingerprint = _decision_package_sha256(frame)
        if fingerprint in fingerprints:
            raise ValueError(
                f"{label} 与 {fingerprints[fingerprint]} 的决策包内容相同；请检查是否重复导入"
            )
        fingerprints[fingerprint] = label
        normalized[label] = frame
    return merge_decisions_multi(normalized)


def _validated_merge(merged: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    if not isinstance(merged, pd.DataFrame):
        raise ValueError("merged 必须是多人决策合并表")
    decision_columns = [column for column in merged.columns if column.startswith("decision_")]
    names = [column[len("decision_"):] for column in decision_columns]
    required = {"zotero_key", "status", "n_screened", *decision_columns}
    if (len(names) < 2 or len(names) != len(set(names))
            or any(not name or len(name) > 100 or name != name.strip() for name in names)
            or not required <= set(merged.columns)):
        raise ValueError("输入必须是 merge_decisions_multi 的合并结果")

    out = merged.copy()
    if out["zotero_key"].isna().any():
        raise ValueError("合并表含空 zotero_key")
    out["zotero_key"] = out["zotero_key"].astype(str)
    if out["zotero_key"].str.strip().eq("").any():
        raise ValueError("合并表含空 zotero_key")
    if out["zotero_key"].duplicated().any():
        raise ValueError("合并表 zotero_key 重复")

    # CSV readers commonly turn blank one-sided fields into NaN; normalize every
    # text/status column before hashing or JSON serialization.
    for column in out.columns:
        if column not in {"zotero_key", "n_screened"}:
            out[column] = out[column].where(out[column].notna(), "")

    for column in decision_columns:
        out[column] = out[column].fillna("").astype(str).str.strip().str.lower()
        invalid = sorted(set(out[column]) - {"", *DECISIONS})
        if invalid:
            raise ValueError(f"{column} 含非法决策: {invalid[:5]}")

    for _, row in out.iterrows():
        votes = [row[column] for column in decision_columns if row[column]]
        expected_status = (
            "incomplete" if len(votes) < 2 else "agree" if len(set(votes)) == 1 else "conflict"
        )
        try:
            n_screened = float(row["n_screened"])
        except (TypeError, ValueError):
            n_screened = -1
        if row["status"] != expected_status or n_screened != len(votes):
            raise ValueError(f"{row['zotero_key']} 的合并状态与原始决策不一致")
    return out, names


def resolve_decision_conflicts(
    merged: pd.DataFrame,
    resolutions: list[dict[str, Any]],
) -> pd.DataFrame:
    """Add final decisions while retaining every original screener vote."""
    if not isinstance(resolutions, list):
        raise ValueError("resolutions 必须为数组")
    out, names = _validated_merge(merged)
    by_key: dict[str, tuple[str, str]] = {}
    known = dict(zip(out["zotero_key"], out["status"]))
    for item in resolutions:
        if not isinstance(item, dict):
            raise ValueError("仲裁项必须为对象")
        key = item.get("zotero_key")
        decision = item.get("decision")
        rationale = item.get("rationale", "")
        if not isinstance(key, str) or not key or len(key) > 300 or key != key.strip():
            raise ValueError("仲裁项 zotero_key 必须为 1 到 300 个非空字符")
        if key not in known:
            raise ValueError(f"仲裁键不在本次合并结果中: {key}")
        if known[key] != "conflict":
            raise ValueError(f"仅可仲裁 conflict 行: {key}")
        if key in by_key:
            raise ValueError(f"仲裁键重复: {key}")
        if not isinstance(decision, str) or decision.strip().lower() not in DECISIONS:
            raise ValueError(f"{key} 含非法仲裁决定，必须为 include、exclude 或 maybe")
        if not isinstance(rationale, str) or len(rationale) > 4000:
            raise ValueError(f"{key} 的仲裁说明最多 4000 个字符")
        by_key[key] = (decision.strip().lower(), rationale)

    finals: list[str] = []
    statuses: list[str] = []
    rationales: list[str] = []
    for _, row in out.iterrows():
        key = row["zotero_key"]
        status = row["status"]
        if status == "agree":
            final = next(row[f"decision_{name}"] for name in names if row[f"decision_{name}"])
            finals.append(final)
            statuses.append("agreed")
            rationales.append("")
        elif key in by_key:
            finals.append(by_key[key][0])
            statuses.append("adjudicated")
            rationales.append(by_key[key][1])
        else:
            finals.append("")
            statuses.append("unresolved")
            rationales.append("")
    out["final_decision"] = finals
    out["arbitration_status"] = statuses
    out["arbitration_reason"] = rationales
    return out


def _read_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "stage": row["stage"],
        "merge_sha256": row["merge_sha256"],
        "result_sha256": row["result_sha256"],
        "supersedes_id": row["supersedes_id"],
        "arbitrator_id": row["arbitrator_id"],
        "created_at": row["created_at"],
        "records": json.loads(row["records_json"]),
    }


def append_decision_arbitration(
    store_path: StrPath,
    *,
    stage: str,
    merged: pd.DataFrame,
    resolutions: list[dict[str, Any]],
    arbitrator_id: str | int,
) -> dict[str, Any]:
    """Persist an immutable, idempotent resolution snapshot in task-scoped SQLite."""
    if not isinstance(stage, str) or stage not in {"stage1", "stage2"}:
        raise ValueError("stage 必须为 stage1 或 stage2")
    if not isinstance(arbitrator_id, (str, int)) or isinstance(arbitrator_id, bool):
        raise ValueError("arbitrator_id 必须来自已认证用户")
    actor = str(arbitrator_id).strip()
    if not actor or len(actor) > 128:
        raise ValueError("arbitrator_id 必须为 1 到 128 个字符")

    normalized, _ = _validated_merge(merged)
    source_records = json.loads(_canonical_json(normalized.to_dict("records")))
    resolved = resolve_decision_conflicts(normalized, resolutions)
    records = json.loads(_canonical_json(resolved.to_dict("records")))
    merge_sha = _sha256({"stage": stage, "records": source_records})
    result_sha = _sha256({"stage": stage, "merge_sha256": merge_sha,
                          "arbitrator_id": actor, "records": records})
    created_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    path = Path(store_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=15)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_ARBITRATION_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute(
                "SELECT * FROM screening_arbitrations "
                "WHERE stage=? AND merge_sha256=? AND result_sha256=?",
                (stage, merge_sha, result_sha),
            ).fetchone()
            if existing:
                result = _read_record(existing)
                conn.commit()
                return result
            previous = conn.execute(
                "SELECT id FROM screening_arbitrations WHERE stage=? AND merge_sha256=? "
                "ORDER BY id DESC LIMIT 1",
                (stage, merge_sha),
            ).fetchone()
            cursor = conn.execute(
                "INSERT INTO screening_arbitrations "
                "(stage,merge_sha256,result_sha256,supersedes_id,arbitrator_id,records_json,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (stage, merge_sha, result_sha, previous["id"] if previous else None,
                 actor, _canonical_json(records), created_at),
            )
            saved = conn.execute(
                "SELECT * FROM screening_arbitrations WHERE id=?", (cursor.lastrowid,)
            ).fetchone()
            result = _read_record(saved)
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise


def get_decision_arbitration(store_path: StrPath, record_id: int) -> dict[str, Any] | None:
    """Read one saved arbitration snapshot without mutating it."""
    path = Path(store_path)
    if not path.is_file():
        return None
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM screening_arbitrations WHERE id=?", (record_id,)
            ).fetchone()
        except sqlite3.OperationalError:
            return None
    return _read_record(row) if row else None


def latest_decision_arbitration(store_path: StrPath, stage: str) -> dict[str, Any] | None:
    if stage not in {"stage1", "stage2"}:
        raise ValueError("stage 必须为 stage1 或 stage2")
    path = Path(store_path)
    if not path.is_file():
        return None
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM screening_arbitrations WHERE stage=? ORDER BY id DESC LIMIT 1",
                (stage,),
            ).fetchone()
        except sqlite3.OperationalError:
            return None
    return _read_record(row) if row else None
