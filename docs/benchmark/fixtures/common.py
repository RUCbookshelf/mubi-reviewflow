"""Shared helpers for the A3 benchmark fixtures (statistical kernel third-party audit).

- Code under test: static copy E:/rf214-ai  (fix/2.1.4 @ eb11741). Nothing there is modified.
- Inputs are hard-coded literals transcribed from A1_datasets.md (no network access).
- Each dataset script writes results/<name>.json; reruns overwrite (idempotent).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

CODE_UNDER_TEST = r"E:/rf214-ai"
FIXTURE_DIR = Path(__file__).resolve().parent
RESULT_DIR = FIXTURE_DIR / "results"
RESULT_DIR.mkdir(parents=True, exist_ok=True)

if CODE_UNDER_TEST not in sys.path:
    sys.path.insert(0, CODE_UNDER_TEST)


class Checker:
    """Collects per-metric check rows (observed vs published) for one dataset."""

    def __init__(self, dataset: str, source: str):
        self.dataset = dataset
        self.default_source = source
        self.checks: list[dict] = []
        self.raw: dict = {}

    # ------------------------------------------------------------------ numeric
    def num(
        self,
        check_id: str,
        method: str,
        metric: str,
        observed: float,
        published: float,
        published_decimals: int,
        rel_tol: float,
        source: str = "",
        derived: bool = False,
        note: str = "",
    ) -> None:
        """Compare two floats.

        rel_tol comes from A1 §9. The effective tolerance also respects the published value's
        rounding: eff_rel = max(rel_tol, 0.5*10^-d / |published|) — agreement can never be
        certified beyond the published precision (e.g. a 2-dp printed estimate cannot support
        a 1e-4 relative check), while a finer published value keeps the A1 tolerance.
        """
        observed = float(observed)
        published = float(published)
        abs_diff = abs(observed - published)
        denom = max(abs(published), 1e-300)
        rel_diff = abs_diff / denom if published != 0.0 else None
        if published == 0.0:
            # zero reference (e.g. boundary tau2 = 0.000): compare in absolute terms
            eff_abs = 0.5 * (10.0 ** (-published_decimals))
            verdict = "PASS" if abs_diff <= eff_abs else "FAIL"
            self.checks.append({
                "id": check_id, "dataset": self.dataset, "method": method, "metric": metric,
                "observed": observed, "published": published,
                "published_decimals": published_decimals,
                "abs_diff": abs_diff, "rel_diff": None,
                "rel_tol_a1": rel_tol, "eff_rel_tol": None, "eff_abs_tol": eff_abs,
                "verdict": verdict, "derived": derived,
                "source": source or self.default_source,
                "note": (note + " " if note else "") + "published value is zero; absolute tolerance used",
            })
            return
        rounding_floor = 0.5 * (10.0 ** (-published_decimals)) / denom
        eff = max(rel_tol, rounding_floor)
        verdict = "PASS" if rel_diff <= eff else "FAIL"
        self.checks.append({
            "id": check_id,
            "dataset": self.dataset,
            "method": method,
            "metric": metric,
            "observed": observed,
            "published": published,
            "published_decimals": published_decimals,
            "abs_diff": abs_diff,
            "rel_diff": rel_diff,
            "rel_tol_a1": rel_tol,
            "eff_rel_tol": eff,
            "eff_abs_tol": None,
            "verdict": verdict,
            "derived": derived,
            "source": source or self.default_source,
            "note": note,
        })

    def integer(
        self,
        check_id: str,
        method: str,
        metric: str,
        observed: int,
        published: int,
        source: str = "",
        note: str = "",
    ) -> None:
        self.checks.append({
            "id": check_id,
            "dataset": self.dataset,
            "method": method,
            "metric": metric,
            "observed": int(observed),
            "published": int(published),
            "published_decimals": 0,
            "abs_diff": abs(int(observed) - int(published)),
            "rel_diff": 0.0 if int(observed) == int(published) else float("inf"),
            "rel_tol_a1": 0.0,
            "eff_rel_tol": 0.0,
            "eff_abs_tol": None,
            "verdict": "PASS" if int(observed) == int(published) else "FAIL",
            "derived": False,
            "source": source or self.default_source,
            "note": note or "integer equality required (A1 §9)",
        })

    def info(
        self,
        check_id: str,
        method: str,
        note: str,
        metric: str = "",
    ) -> None:
        """Executed but no valid published reference for a PASS/FAIL verdict."""
        self.checks.append({
            "id": check_id,
            "dataset": self.dataset,
            "method": method,
            "metric": metric,
            "observed": None,
            "published": None,
            "published_decimals": None,
            "abs_diff": None,
            "rel_diff": None,
            "rel_tol_a1": None,
            "eff_rel_tol": None,
            "eff_abs_tol": None,
            "verdict": "INFO",
            "derived": False,
            "source": self.default_source,
            "note": note,
        })

    def notrun(
        self,
        check_id: str,
        method: str,
        reason: str,
        metric: str = "",
        published: str = "",
    ) -> None:
        self.checks.append({
            "id": check_id,
            "dataset": self.dataset,
            "method": method,
            "metric": metric,
            "observed": None,
            "published": published,
            "published_decimals": None,
            "abs_diff": None,
            "rel_diff": None,
            "rel_tol_a1": None,
            "eff_rel_tol": None,
            "eff_abs_tol": None,
            "verdict": "NOT-RUN",
            "derived": False,
            "source": self.default_source,
            "note": reason,
        })

    # ------------------------------------------------------------------- output
    def write(self, name: str, extra_raw: dict | None = None) -> dict:
        payload = {
            "dataset": self.dataset,
            "code_under_test": CODE_UNDER_TEST,
            "n_checks": len(self.checks),
            "counts": {
                "PASS": sum(1 for c in self.checks if c["verdict"] == "PASS"),
                "FAIL": sum(1 for c in self.checks if c["verdict"] == "FAIL"),
                "NOT-RUN": sum(1 for c in self.checks if c["verdict"] == "NOT-RUN"),
                "INFO": sum(1 for c in self.checks if c["verdict"] == "INFO"),
            },
            "checks": self.checks,
            "raw": self.raw if extra_raw is None else {**self.raw, **extra_raw},
        }
        path = RESULT_DIR / f"{name}.json"
        path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"[{self.dataset}] {payload['counts']} -> {path}")
        return payload


# --------------------------------------------------------------------- derived helpers

def derive_pooled_se(result: dict, model: str) -> float:
    """Recover the pooled SE from the CI exactly the way _synthesize_rows built it.

    review_analysis.py:515 uses critical=norm.ppf(.975) for random_reml and 1.96 otherwise.
    """
    critical = _norm975() if model == "random_reml" else 1.96
    return (result["ci_analysis_high"] - result["ci_analysis_low"]) / (2 * critical)


def _norm975() -> float:
    from scipy.stats import norm

    return float(norm.ppf(0.975))


def h2_from_q(q: float, df: int) -> float:
    return q / df


def md_variance_ho(n1: float, sd1: float, n2: float, sd2: float) -> float:
    """metafor escalc(measure="MD") sampling variance under vtype="HO" (pooled SD).

    A1 §1.2 states the Normand1999 published values use the pooled-SD variance.
    """
    df = n1 + n2 - 2
    sp2 = ((n1 - 1) * sd1 * sd1 + (n2 - 1) * sd2 * sd2) / df
    return sp2 * (1.0 / n1 + 1.0 / n2)


def logit_or_variance(a: int, b: int, c: int, d: int) -> float:
    return 1.0 / a + 1.0 / b + 1.0 / c + 1.0 / d
