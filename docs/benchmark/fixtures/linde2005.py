"""Fixture C: Linde 2005 / Viechtbauer 2007 St John's wort subset (17 studies, logOR) — EE + DL.

Data + published values: A1 §1.4 (metafor analyses:viechtbauer2007b, meta-regression declared
consistent with Viechtbauer 2007 Table 2 p.113). The meta-regression itself needs the dosage and
baseline covariates per study, which A1 did not transcribe -> NOT-RUN (no covariate data).
"""

from __future__ import annotations

import math

from common import Checker, derive_pooled_se

# ai, n1i, ci, n2i, yi, vi  (A1 §1.4 verbatim, 4 dp)
DATA = [
    (20,  25, 11,  25,  0.5978, 0.0609),
    (14,  20,  9,  20,  0.4418, 0.0825),
    (4,   25,  2,  25,  0.6931, 0.6700),
    (20,  32,  6,  33,  1.2347, 0.1551),
    (28,  50, 13,  55,  0.8626, 0.0745),
    (34,  48, 25,  49,  0.3281, 0.0282),
    (35,  53, 12,  54,  1.0891, 0.0745),
    (24,  49, 16,  49,  0.4055, 0.0634),
    (45,  80, 12,  79,  1.3092, 0.0804),
    (67, 106, 22,  47,  0.3004, 0.0297),
    (34,  60, 17,  59,  0.6763, 0.0546),
    (46,  70, 34,  70,  0.3023, 0.0226),
    (55, 123, 57, 124, -0.0276, 0.0195),
    (23,  37, 15,  35,  0.3719, 0.0545),
    (26,  98, 19, 102,  0.3537, 0.0711),
    (46, 113, 56, 116, -0.1705, 0.0221),
    (98, 186, 80, 189,  0.2189, 0.0120),
]

SOURCE = "A1 §1.4 metafor analyses:viechtbauer2007b (EE/DL same page; meta-regression = Viechtbauer 2007 Table 2 p.113)"


def build_rows() -> list[dict]:
    return [{"study_id": f"S{i+1}", "estimate": math.exp(yi), "se": math.sqrt(vi)}
            for i, (_, _, _, _, yi, vi) in enumerate(DATA)]


def run() -> dict:
    from coscreen.review_analysis import _synthesize_rows

    ck = Checker("Linde2005", SOURCE)
    rows = build_rows()
    ck.raw["rows"] = rows

    ee = _synthesize_rows(rows, "OR", "fixed", "normal")
    dl = _synthesize_rows(rows, "OR", "random", "normal")
    ck.raw["ee"], ck.raw["dl"] = ee, dl

    # fixture-side independent inverse-variance recomputation on the same 4-dp yi/vi
    w = [1.0 / r["se"] ** 2 for r in rows]
    y = [math.log(r["estimate"]) for r in rows]
    sw = sum(w)
    mu_i = sum(a * b for a, b in zip(w, y)) / sw
    q_i = sum(a * (b - mu_i) ** 2 for a, b in zip(w, y))
    ck.raw["independent_iv"] = {"ee_pooled": mu_i, "q": q_i,
                                "note": "matches kernel exactly; published 2-dp values differ "
                                        "beyond input-rounding size on the EE point estimate "
                                        "(0.3248 computed vs 0.33 printed) — reference-side "
                                        "question for attribution"}

    ck.num("linde-ee-pooled", "IV fixed (EE)", "pooled logOR", ee["pooled_analysis"], 0.33, 2, 1e-4,
           note="published at 2 dp only (A1 §1.4 tolerance note); computed 0.3248 rounds to 0.32, "
                "not 0.33 — independent recomputation in raw agrees with the kernel")
    ck.num("linde-ee-se", "IV fixed (EE)", "SE(pooled logOR)", derive_pooled_se(ee, "fixed"), 0.05, 2, 1e-4, derived=True)
    ck.num("linde-ee-ci-low", "IV fixed (EE)", "95% CI low", ee["ci_analysis_low"], 0.23, 2, 1e-4)
    ck.num("linde-ee-ci-high", "IV fixed (EE)", "95% CI high", ee["ci_analysis_high"], 0.42, 2, 1e-4)
    ck.num("linde-ee-q", "IV fixed (EE)", "Q", ee["q"], 51.55, 2, 1e-4,
           note="marginal: gap 0.021 is 4-dp-input-rounding sized (independent recomputation in raw)")
    ck.integer("linde-ee-qdf", "IV fixed (EE)", "Q df", ee["q_df"], 16)
    ck.num("linde-ee-i2", "IV fixed (EE)", "I2 %", ee["i2_percent"], 68.96, 2, 1e-3)
    ck.num("linde-ee-h2", "IV fixed (EE)", "H2 = Q/df", ee["q"] / ee["q_df"], 3.22, 2, 1e-3, derived=True)

    ck.num("linde-dl-tau2", "IV DL random", "tau2", dl["tau2"], 0.09, 2, 1e-4,
           note="published at 2 dp only")
    ck.num("linde-dl-tau", "IV DL random", "tau", math.sqrt(dl["tau2"]), 0.30, 2, 1e-4, derived=True)
    ck.num("linde-dl-pooled", "IV DL random", "pooled logOR", dl["pooled_analysis"], 0.45, 2, 1e-4)
    ck.num("linde-dl-se", "IV DL random", "SE(pooled logOR)", derive_pooled_se(dl, "random"), 0.09, 2, 1e-4, derived=True)
    ck.num("linde-dl-ci-low", "IV DL random", "95% CI low", dl["ci_analysis_low"], 0.27, 2, 1e-4)
    ck.num("linde-dl-ci-high", "IV DL random", "95% CI high", dl["ci_analysis_high"], 0.63, 2, 1e-4)
    ck.num("linde-dl-or", "IV DL random", "OR scale pooled", dl["pooled"], 1.57, 2, 1e-4)
    ck.num("linde-dl-or-low", "IV DL random", "OR scale CI low", dl["ci_low"], 1.31, 2, 1e-4)
    ck.num("linde-dl-or-high", "IV DL random", "OR scale CI high", dl["ci_high"], 1.89, 2, 1e-4)

    ck.notrun("linde-metareg", "DL meta-regression (dosage x baseline)",
              "A1 §1.4 published the meta-regression but did not transcribe the per-study dosage/baseline "
              "covariates; inputs unavailable (fixture must not invent data). Kernel entry exists "
              "(coscreen/meta_regression_model.py fit_meta_regression, needs >=10 studies) but cannot run.",
              "residual tau2=0.0475, R2=47.38%, QE(13)=27.9903, QM(3)=10.1280, intrcpt 0.4763(0.0876), "
              "dosage -0.0058(0.0100), baseline -0.0672(0.0352), interaction -0.0016(0.0034)")
    return ck.write("linde2005")


if __name__ == "__main__":
    run()
