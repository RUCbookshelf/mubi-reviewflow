"""Fixture K: Fleiss kappa / Light kappa — honest NOT-RUN probe.

A1 §6.2/§6.3: published values are complete (Fleiss 0.430 / exact 0.428 / 3-rater 0.534 +
per-category; Light 0.378 / 0.555) but the 30x6 `diagnoses` rating matrix lives only in the
irr R package binary (rda). This machine has no R runtime (Rscript not found) and fixtures may
not touch the network, so the matrix cannot be obtained -> NOT-RUN. Nothing is fabricated.
The probe below re-verifies R absence at run time and records the evidence.
A2 §5 also notes: Fleiss has no kernel implementation in E:/rf214-ai (grep 'fleiss' = 0 hits);
multi-rater agreement is served by light_kappa (pairwise Cohen mean).
"""

from __future__ import annotations

import shutil
import subprocess

from common import Checker

SOURCE = "A1 §6.2/§6.3 irr vignette + Datanovia; data blocked on irr::diagnoses export"


def r_available() -> bool:
    return shutil.which("Rscript") is not None


def probe_irr_export() -> str:
    """If an R runtime existed with irr, this one-liner would export the matrix."""
    return ('Rscript -e \'data(diagnoses, package="irr"); write.csv(diagnoses, "diagnoses.csv")\''
            " (A1 §6.2 recommended export command)")


def run() -> dict:
    ck = Checker("FleissLightKappa", SOURCE)
    available = r_available()
    ck.raw["r_available"] = available
    ck.raw["export_command"] = probe_irr_export()
    if available:
        try:
            out = subprocess.run(
                ["Rscript", "-e", 'cat(requireNamespace("irr", quietly=TRUE))'],
                capture_output=True, text=True, timeout=60)
            ck.raw["irr_installed"] = out.stdout.strip()
        except Exception as exc:  # pragma: no cover - runtime probe
            ck.raw["irr_probe_error"] = str(exc)

    reason_no_r = (
        "no R runtime on this machine (Rscript not found) and fixtures must not use the network; "
        "irr::diagnoses (30x6 matrix, Fleiss 1971) exists only as an in-package rda binary — "
        "only its first 4 rows are public in A1. Data unavailable => not fabricated."
    )
    reason_no_impl = ("A2 §5: no Fleiss kappa implementation exists in E:/rf214-ai "
                      "(case-insensitive grep 'fleiss' over *.py = 0 hits)")

    ck.notrun("fleiss-6raters", "Fleiss kappa (6 raters)",
              reason_no_r + "; additionally " + reason_no_impl,
              "kappa=0.430, z=2.533, p=0.011")
    ck.notrun("fleiss-exact", "Fleiss kappa exact (irr exact=TRUE)", reason_no_r,
              "kappa=0.428, z=2.521, p=0.012")
    ck.notrun("fleiss-3raters", "Fleiss kappa (first 3 raters subset)", reason_no_r,
              "kappa=0.534, z=9.89, p=0; per-category Depression 0.416 / PD 0.591 / "
              "Schizophrenia 0.577 / Neurosis 0.236 / Other 1.000")
    ck.notrun("fleiss-wiki", "Fleiss kappa Wikipedia 10x14x5 worked example",
              "A1 §6.2: Wikipedia full table unreachable in the A1 environment; only "
              "kappa~=0.21 (0.2099) known, no full input matrix", "kappa≈0.2099")
    ck.notrun("light-6raters", "Light kappa (6 raters)",
              reason_no_r + "; kernel light_kappa exists (coscreen/stats/kappa.py:147) but the "
              "diagnoses matrix is unavailable",
              "kappa=0.378, z=2.622, p=0.009")
    ck.notrun("light-3raters", "Light kappa (first 3 raters)", reason_no_r,
              "kappa=0.555 (z/p shown as NaN on the source page)")
    return ck.write("kappa_fleiss_light")


if __name__ == "__main__":
    run()
