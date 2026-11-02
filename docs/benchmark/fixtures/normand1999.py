"""Fixture A: Normand 1999 stroke-unit units (9 studies, MD) — EE + DL + REML.

Data + published values: A1 §1.2 (metafor analyses:normand1999, stated identical to
Normand et al. 1999 Table X p.352; data double-checked against metadat::dat.normand1999).
vi is computed per A1 §1.2 with the pooled-SD (vtype="HO") formula, fixture-side.
"""

from __future__ import annotations

import math

from common import Checker, RESULT_DIR, derive_pooled_se, md_variance_ho  # noqa: F401 (RESULT_DIR for standalone run)

# study, n1, m1, sd1, n2, m2, sd2  (specialist organised stroke unit care vs conventional care)
DATA = [
    ("Edinburgh",           155, 55, 47, 156,  75, 64),
    ("Orpington-Mild",       31, 27,  7,  32,  29,  4),
    ("Orpington-Moderate",   75, 64, 17,  71, 119, 29),
    ("Orpington-Severe",     18, 66, 20,  18, 137, 48),
    ("Montreal-Home",         8, 14,  8,  13,  18, 11),
    ("Montreal-Transfer",    57, 19,  7,  52,  18,  4),
    ("Newcastle",            34, 52, 45,  33,  41, 34),
    ("Umea",                110, 21, 16, 183,  31, 27),
    ("Uppsala",              60, 30, 27,  52,  23, 20),
]

SOURCE = "A1 §1.2 metafor analyses:normand1999 (declared consistent with Normand 1999 Table X p.352)"


def build_rows() -> list[dict]:
    rows = []
    for study, n1, m1, sd1, n2, m2, sd2 in DATA:
        yi = m1 - m2
        vi = md_variance_ho(n1, sd1, n2, sd2)
        rows.append({"study_id": study, "estimate": yi, "se": math.sqrt(vi)})
    return rows


def run() -> dict:
    from coscreen.review_analysis import _synthesize_rows

    ck = Checker("Normand1999", SOURCE)
    rows = build_rows()
    ck.raw["rows"] = rows

    out = {}
    for model, key in (("fixed", "ee"), ("random", "dl"), ("random_reml", "reml")):
        res = _synthesize_rows(rows, "MD", model, "normal")
        out[key] = res
        ck.raw[key] = {k: res[k] for k in
                       ("pooled_analysis", "ci_analysis_low", "ci_analysis_high", "q", "q_df",
                        "q_p", "i2_percent", "tau2", "n_studies", "method")}

    # ---- EE (fixed effect) ----------------------------------------------------
    ee = out["ee"]
    ck.num("normand-ee-pooled", "IV fixed (EE)", "pooled MD", ee["pooled_analysis"], -3.49, 2, 1e-4)
    ck.num("normand-ee-se", "IV fixed (EE)", "SE(pooled)", derive_pooled_se(ee, "fixed"), 0.78, 2, 1e-4, derived=True)
    ck.num("normand-ee-ci-low", "IV fixed (EE)", "95% CI low", ee["ci_analysis_low"], -5.03, 2, 1e-4)
    ck.num("normand-ee-ci-high", "IV fixed (EE)", "95% CI high", ee["ci_analysis_high"], -1.96, 2, 1e-4)
    ck.num("normand-ee-q", "IV fixed (EE)", "Q", ee["q"], 241.06, 2, 1e-4)
    ck.integer("normand-ee-qdf", "IV fixed (EE)", "Q df", ee["q_df"], 8)
    ck.num("normand-ee-i2", "IV fixed (EE)", "I2 %", ee["i2_percent"], 96.68, 2, 1e-3)

    # ---- DL random -------------------------------------------------------------
    dl = out["dl"]
    ck.num("normand-dl-tau2", "IV DL random", "tau2", dl["tau2"], 218.72, 2, 1e-4)
    ck.num("normand-dl-tau", "IV DL random", "tau", math.sqrt(dl["tau2"]), 14.79, 2, 1e-4, derived=True)
    ck.num("normand-dl-pooled", "IV DL random", "pooled MD", dl["pooled_analysis"], -14.10, 2, 1e-4)
    ck.num("normand-dl-se", "IV DL random", "SE(pooled)", derive_pooled_se(dl, "random"), 5.28, 2, 1e-4, derived=True)
    ck.num("normand-dl-ci-low", "IV DL random", "95% CI low", dl["ci_analysis_low"], -24.45, 2, 1e-4)
    ck.num("normand-dl-ci-high", "IV DL random", "95% CI high", dl["ci_analysis_high"], -3.75, 2, 1e-4)

    # ---- REML random -----------------------------------------------------------
    reml = out["reml"]
    ck.num("normand-reml-tau2", "IV REML random", "tau2", reml["tau2"], 685.20, 2, 1e-4)
    ck.num("normand-reml-tau", "IV REML random", "tau", math.sqrt(reml["tau2"]), 26.18, 2, 1e-4, derived=True)
    ck.num("normand-reml-pooled", "IV REML random", "pooled MD", reml["pooled_analysis"], -15.12, 2, 1e-4)
    ck.num("normand-reml-se", "IV REML random", "SE(pooled)", derive_pooled_se(reml, "random_reml"), 8.95, 2, 1e-4, derived=True)
    ck.num("normand-reml-ci-low", "IV REML random", "95% CI low", reml["ci_analysis_low"], -32.67, 2, 1e-4)
    ck.num("normand-reml-ci-high", "IV REML random", "95% CI high", reml["ci_analysis_high"], 2.43, 2, 1e-4)
    # I2/H2: the page's REML row prints I2=98.92%/H2=92.26, which is NOT derivable from the
    # same page's Q(df=8)=241.06 ((Q-df)/Q=96.68%, Q/df=30.13). Same class of discrepancy as
    # the BCG I2 ⚠ in A1 §1.1. Per the A1 recomputation policy the primary check uses the
    # Q-derived values; the page-printed pair is kept as secondary rows for attribution.
    ck.num("normand-reml-i2", "IV REML random", "I2 % (derived from published Q)",
           reml["i2_percent"], (241.06 - 8) / 241.06 * 100, 2, 1e-3, derived=True,
           note="A1 recomputation policy; page prints 98.92% (see secondary row)")
    ck.num("normand-reml-i2-page", "IV REML random", "I2 % (page-printed, secondary)",
           reml["i2_percent"], 98.92, 2, 1e-3,
           note="page value not derivable from the page's own Q=241.06 (df=8); flagged for attribution")
    ck.num("normand-reml-h2", "IV REML random", "H2 = Q/df (from published Q)",
           reml["q"] / reml["q_df"], 241.06 / 8, 2, 1e-3, derived=True,
           note="page prints 92.26 (see secondary row)")
    ck.num("normand-reml-h2-page", "IV REML random", "H2 (page-printed, secondary)",
           reml["q"] / reml["q_df"], 92.26, 2, 1e-3, derived=True,
           note="page value not derivable from the page's own Q; flagged for attribution")
    se_reml = derive_pooled_se(reml, "random_reml")
    ck.num("normand-reml-p", "IV REML random", "two-sided p (z=pooled/SE)",
           math.erfc(abs(reml["pooled_analysis"] / se_reml) / math.sqrt(2)), 0.09, 2, 1e-2,
           derived=True)
    # A1 §2 note: metafor/SAS SE(tau2) differ (expected Hessian 360.37 vs observed ~380); the
    # kernel does not expose SE(tau2), so no check row is possible.

    # SE(tau2) not exposed by the kernel -> nothing to compare (documented in A1 §2 note).
    return ck.write("normand1999", extra_raw={"vi_used": [r["se"] ** 2 for r in rows]})


if __name__ == "__main__":
    run()
