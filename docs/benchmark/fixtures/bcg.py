"""Fixture E: BCG vaccine / TB (Colditz 1994, 13 trials, logRR) — Q/I2/tau2 + escalc yi/vi + trim-and-fill.

Data + published values: A1 §1.1 (metadat::dat.bcg 2x2; yi/vi 8-dp from Viechtbauer 2010 JSS
escalc(measure="RR")). REML published; BCG-specific EE/DL published values do not exist (A1 §1.1)
-> no EE/DL check rows against BCG (covered by Normand/D&K/Linde).
I2 published page value 92.22% conflicts with the exact (Q-df)/Q = 92.12% derivable from the
same page (A1 ⚠); A1 instructs to compare against the recomputed value and record both.
Trim-and-fill published: EE model, right side, L0 missing=4, adjusted EE estimate -0.2910
[-0.3660, -0.2160] (metafor trimfill reference page).
"""

from __future__ import annotations

import math

from common import Checker

# author-year, tpos, tneg, cpos, cneg  (A1 §1.1 verbatim)
TWOBYTWO = [
    ("Aronson 1948",               4,   119,  11,   128),
    ("Ferguson & Simes 1949",      6,   300,  29,   274),
    ("Rosenthal et al 1960",       3,   228,  11,   209),
    ("Hart & Sutherland 1977",    62, 13536, 248, 12619),
    ("Frimodt-Moller et al 1973", 33,  5036,  47,  5761),
    ("Stein & Aronson 1953",     180,  1361, 372,  1079),
    ("Vandiviere et al 1973",      8,  2537,  10,   619),
    ("TPT Madras 1980",          505, 87886, 499, 87892),
    ("Coetzee & Berjak 1968",     29,  7470,  45,  7232),
    ("Rosenthal et al 1961",      17,  1699,  65,  1600),
    ("Comstock et al 1974",      186, 50448, 141, 27197),
    ("Comstock & Webster 1969",    5,  2493,   3,  2338),
    ("Comstock et al 1976",       27, 16886,  29, 17825),
]

# yi / vi at 8 dp (A1 §1.1, Viechtbauer 2010 JSS escalc(measure="RR") output)
YI_VI = [
    (-0.88931133, 0.325584765),
    (-1.58538866, 0.194581121),
    (-1.34807315, 0.415367965),
    (-1.44155119, 0.020010032),
    (-0.21754732, 0.051210172),
    (-0.78611559, 0.006905618),
    (-1.62089822, 0.223017248),
    (0.01195233, 0.003961579),
    (-0.46941765, 0.056434210),
    (-1.37134480, 0.073024794),
    (-0.33935883, 0.012412214),
    (0.44591340, 0.532505845),
    (-0.01731395, 0.071404660),
]

SOURCE = "A1 §1.1 metadat::dat.bcg + Viechtbauer 2010 JSS (yi/vi); A1 §5 metafor trimfill reference page"


def build_rows() -> list[dict]:
    rows = []
    names = [name for name, *_ in TWOBYTWO]
    for name, (yi, vi) in zip(names, YI_VI):
        rows.append({"study_id": name, "estimate": math.exp(yi), "se": math.sqrt(vi)})
    return rows


def run() -> dict:
    from coscreen.review_analysis import _synthesize_rows, binary_effect
    from coscreen.meta_regression_model import profile_reml_tau2
    from coscreen.trim_fill import trim_and_fill

    ck = Checker("BCG", SOURCE)

    # ---- escalc-level per-trial effects via product binary_effect (A2 §1.1) ----
    # All 13 trials have non-zero cells -> no continuity correction anywhere, so the
    # product RR formula must reproduce the published 8-dp yi/vi exactly.
    escalc_obs = []
    for (name, tpos, tneg, cpos, cneg), (yi_pub, vi_pub) in zip(TWOBYTWO, YI_VI):
        eff = binary_effect(tpos, tpos + tneg, cpos, cpos + cneg, "RR")
        yi_obs = math.log(eff["estimate"])
        vi_obs = eff["se"] ** 2
        escalc_obs.append((yi_obs, vi_obs))
        ck.num(f"bcg-yi-{len(escalc_obs):02d}", "binary_effect (escalc RR)", f"logRR yi [{name}]",
               yi_obs, yi_pub, 8, 1e-6)
        ck.num(f"bcg-vi-{len(escalc_obs):02d}", "binary_effect (escalc RR)", f"vi [{name}]",
               vi_obs, vi_pub, 9, 1e-6, note="published vi given to 9 dp")
    ck.raw["escalc_binary_effect"] = escalc_obs

    # pool from the published yi/vi table (identical to escalc output within 1e-6)
    rows = build_rows()
    ck.raw["rows"] = rows

    ee = _synthesize_rows(rows, "RR", "fixed", "normal")
    reml = _synthesize_rows(rows, "RR", "random_reml", "normal")
    dl = _synthesize_rows(rows, "RR", "random", "normal")
    ck.raw["ee"], ck.raw["reml"], ck.raw["dl"] = ee, reml, dl

    # ---- Q / I2 / tau2 (REML) — published reference values ----------------------
    ck.num("bcg-reml-pooled-log", "IV REML random", "pooled logRR", reml["pooled_analysis"], -0.7145, 4, 1e-4)
    ck.num("bcg-reml-ci-low", "IV REML random", "95% CI low (log)", reml["ci_analysis_low"], -1.0669, 4, 1e-4)
    ck.num("bcg-reml-ci-high", "IV REML random", "95% CI high (log)", reml["ci_analysis_high"], -0.3622, 4, 1e-4)
    ck.num("bcg-reml-tau2", "IV REML random", "tau2", reml["tau2"], 0.3132, 4, 1e-4)
    ck.num("bcg-reml-rr", "IV REML random", "RR scale pooled", reml["pooled"], 0.4894, 4, 1e-4)
    ck.num("bcg-reml-rr-low", "IV REML random", "RR scale CI low", reml["ci_low"], 0.3441, 4, 1e-4)
    ck.num("bcg-reml-rr-high", "IV REML random", "RR scale CI high", reml["ci_high"], 0.6962, 4, 1e-4)

    ck.num("bcg-q", "heterogeneity", "Q", reml["q"], 152.2330, 4, 1e-4)
    ck.integer("bcg-qdf", "heterogeneity", "Q df", reml["q_df"], 12)
    # A1 ⚠: page prints I2=92.22% but exact (Q-df)/Q from Q=152.2330, df=12 is 92.12%.
    # Check runs against the recomputed value; the ⚠ page value is recorded in raw only.
    ck.num("bcg-i2", "heterogeneity", "I2 %", reml["i2_percent"], 92.12, 2, 1e-3,
           note="A1 ⚠: page value 92.22% contradicts exact (Q-df)/Q from same page; recomputed used")
    ck.raw["i2_page_value_warn"] = {
        "page_printed": 92.22, "observed": reml["i2_percent"],
        "note": "A1 §1.1 ⚠ mismatch between page-extracted I2 and exact derivation from published Q",
    }
    ck.num("bcg-h2", "heterogeneity", "H2 = Q/df", reml["q"] / reml["q_df"], 12.69, 2, 1e-3, derived=True)

    tau2_direct, objective = profile_reml_tau2(
        [math.log(r["estimate"]) for r in rows], [r["se"] ** 2 for r in rows])
    ck.raw["profile_reml_tau2_direct"] = {"tau2": tau2_direct, "objective": objective}
    ck.num("bcg-reml-direct-tau2", "profile_reml_tau2 (direct)", "tau2", tau2_direct, 0.3132, 4, 1e-4)

    # ---- trim-and-fill (EE model, right side, L0) --------------------------------
    tf_rows = [{"study_id": name, "measure": "RR", "estimate": math.exp(yi), "se": math.sqrt(vi)}
               for (name, _t, _n, _c, _cn), (yi, vi) in zip(TWOBYTWO, YI_VI)]
    tf = trim_and_fill(tf_rows, "RR", "right")
    ck.raw["trim_fill"] = tf
    ck.integer("bcg-tf-missing", "trim-and-fill (EE, right, L0)", "n_missing", tf["n_missing"], 4)
    ck.num("bcg-tf-adjusted", "trim-and-fill (EE, right, L0)", "adjusted pooled logRR",
           math.log(tf["adjusted"]["pooled"]), -0.2910, 4, 1e-4, derived=True)
    ck.num("bcg-tf-ci-low", "trim-and-fill (EE, right, L0)", "adjusted 95% CI low (log)",
           math.log(tf["adjusted"]["ci_low"]), -0.3660, 4, 1e-4, derived=True)
    ck.num("bcg-tf-ci-high", "trim-and-fill (EE, right, L0)", "adjusted 95% CI high (log)",
           math.log(tf["adjusted"]["ci_high"]), -0.2160, 4, 1e-4, derived=True)

    ck.notrun("bcg-tf-r0", "trim-and-fill R0 estimator / R0 test",
              "kernel implements L0 only (coscreen/trim_fill.py estimator='L0'); R0 count 4 (SE 3.1623) "
              "and R0 test p=0.0312 have no kernel entry")
    ck.notrun("bcg-tf-re", "trim-and-fill RE model (missing=1, adjusted -0.6571) and augmented-RE rerun (-0.3422)",
              "kernel trim_and_fill is fixed-effect only (pooling_method='FE', observed_tau2=0)")
    ck.notrun("bcg-eb", "EB (empirical Bayes) pooling + EB meta-regression (A1 §1.5)",
              "kernel has no EB tau2 estimator; A1 §1.5 also notes the Berkey smoothed-variance "
              "procedure was not transcribed, so inputs are unavailable anyway")
    ck.notrun("bcg-ee-dl-published", "BCG-specific EE/DL published values",
              "A1 §1.1: metafor publishes only REML/EB for BCG; EE/DL verified on Normand/D&K/Linde instead "
              "(EE/DL outputs stored in results JSON for reference)")
    return ck.write("bcg")


if __name__ == "__main__":
    run()
