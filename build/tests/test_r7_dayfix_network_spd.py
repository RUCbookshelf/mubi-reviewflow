"""R7 dayfix nails (G4-01): the network-study STORAGE layer must reject covariance
matrices that are not symmetric positive definite.

The outer HTTP gate (custom_backend/main.py -> network_gls.validate_study) already
blocks non-SPD covariance, but save_network_study itself used to accept and persist
anything with a positive diagonal (verified by review wave minimax_findings_
verification_7.md: symmetric-indefinite and asymmetric matrices were both stored).
These nails call the storage layer DIRECTLY, so pre-fix they are red; a valid SPD
matrix is the always-green control proving legitimate data is unaffected (R2).
"""
from __future__ import annotations

import sys
from contextlib import closing
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from coscreen.db import _connect, init_db  # noqa: E402
from coscreen.network_study_data import (  # noqa: E402
    list_network_studies,
    save_network_study,
)

CONTRASTS_MD = [
    {"treatment": "A", "comparator": "Placebo", "estimate": 0.5},
    {"treatment": "B", "comparator": "Placebo", "estimate": 0.3},
]
# Same shapes the verification probe used: det([[0.2,0.3],[0.3,0.2]]) = -0.05 < 0.
INDEFINITE = [[0.20, 0.30], [0.30, 0.20]]
ASYMMETRIC = [[0.20, 0.05], [0.01, 0.30]]
VALID_SPD = [[0.05, 0.01], [0.01, 0.05]]


def _db_with_linked_study(tmp_path: Path) -> str:
    db = str(tmp_path / "rf-g4.sqlite3")
    init_db(db)
    with closing(_connect(db)) as conn, conn:
        conn.execute("INSERT INTO articles(zotero_key) VALUES ('K_G4')")
        conn.execute("INSERT INTO review_studies(id,label) VALUES ('S_G4','probe')")
        conn.execute(
            "INSERT INTO review_reports(zotero_key,study_id) VALUES ('K_G4','S_G4')"
        )
    return db


def _save(db: str, covariance: list[list[float]]) -> None:
    save_network_study(db, "S_G4", "pain", "1w", "MD", CONTRASTS_MD,
                       covariance, "natural", "K_G4")


def test_g4_01_indefinite_covariance_rejected_at_storage_layer(tmp_path):
    """Symmetric but not positive definite (negative determinant) must be refused
    by save_network_study itself, not only by the outer HTTP validation."""
    db = _db_with_linked_study(tmp_path)
    with pytest.raises(ValueError):
        _save(db, INDEFINITE)
    assert list_network_studies(db) == []


def test_g4_01_asymmetric_covariance_rejected_at_storage_layer(tmp_path):
    db = _db_with_linked_study(tmp_path)
    with pytest.raises(ValueError):
        _save(db, ASYMMETRIC)
    assert list_network_studies(db) == []


def test_g4_01_valid_spd_covariance_still_persists_round_trip(tmp_path):
    """R2 control: legitimate SPD covariance keeps flowing through unchanged."""
    db = _db_with_linked_study(tmp_path)
    _save(db, VALID_SPD)
    rows = list_network_studies(db)
    assert len(rows) == 1
    assert rows[0]["covariance"] == VALID_SPD
    assert rows[0]["contrasts"] == [
        {"treatment": "A", "comparator": "Placebo", "estimate": 0.5},
        {"treatment": "B", "comparator": "Placebo", "estimate": 0.3},
    ]
