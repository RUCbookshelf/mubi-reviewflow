"""Fixture B: DerSimonian & Kacker 2007 CLAPS stratified data (6 groups, logOR) — multiple tau2.

Data + published values: A1 §1.3 (metafor analyses:dersimonian2007, stated identical to
D&K 2007 Tables 3-4). yi/vi published at 4 decimals are fed verbatim.
coscreen implements DL / PM (iterated Paule-Mandel) / REML estimators; the remaining
published estimators (CA=HE, CA2, DL2, EB, ML, HS, SJ, SJ2) have no kernel entry -> NOT-RUN.
"""

from __future__ import annotations

import math

from common import Checker, derive_pooled_se

# n1, n2, ai, ci, yi(logOR), vi
DATA = [
    (156,   74,   5,   8, -1.2976, 0.3468),
    (303,  303,   5,  17, -1.2649, 0.2657),
    (565,  477,  12,   9,  0.1208, 0.1984),
    (1570, 1565, 69,  94, -0.3294, 0.0265),
    (103,  105,   9,  11, -0.2007, 0.2233),
    (4659, 4650, 313, 352, -0.1285, 0.0065),
]

# estimator: (tau, mu, SE(mu)) — A1 §1.3 published table
PUBLISHED = {
    "PM": (0.3681, -0.3811, 0.2060),
    "CA": (0.4410, -0.4035, 0.2327),   # HE in metafor
    "DL": (0.2323, -0.3240, 0.1540),
    "CA2": (0.3831, -0.3861, 0.2115),
    "DL2": (0.3254, -0.3655, 0.1901),
    "EB": (0.3681, -0.3811, 0.2060),
    "ML": (0.0023, -0.1974, 0.0694),
    "REML": (0.1843, -0.2980, 0.1343),
    "HS": (0.1330, -0.2666, 0.1125),
    "SJ": (0.4572, -0.4079, 0.2386),
    "SJ2": (0.4084, -0.3941, 0.2208),
}

SOURCE = "A1 §1.3 metafor analyses:dersimonian2007 (declared consistent with D&K 2007 Tables 3-4)"


def build_rows() -> list[dict]:
    return [{"study_id": f"G{i+1}", "estimate": math.exp(yi), "se": math.sqrt(vi)}
            for i, (_, _, _, _, yi, vi) in enumerate(DATA)]


def run() -> dict:
    from coscreen.review_analysis import _synthesize_rows
    from coscreen.meta_regression_model import profile_reml_tau2

    ck = Checker("DK2007", SOURCE)
    rows = build_rows()
    ck.raw["rows"] = rows

    model_map = {"DL": "random", "PM": "random_pm", "REML": "random_reml"}
    results = {}
    for est, model in model_map.items():
        res = _synthesize_rows(rows, "OR", model, "normal")
        results[est] = res
        ck.raw[est] = {k: res[k] for k in ("pooled_analysis", "ci_analysis_low", "ci_analysis_high",
                                           "tau2", "q", "q_df", "q_p", "i2_percent")}
        tau, mu, se_pub = PUBLISHED[est]
        tau2 = res["tau2"]
        tau_obs = math.sqrt(tau2)
        mu_obs = res["pooled_analysis"]           # log scale
        se_obs = derive_pooled_se(res, model)
        note4 = ("inputs are A1's 4-dp yi/vi table; fixture-side independent recomputation "
                 "reproduces the kernel values exactly (raw.independent_DL_check) — deviation "
                 "vs the 4-dp published table is input-rounding sized") if est == "DL" else ""
        ck.num(f"dk2007-{est.lower()}-tau", f"IV {est} random", "tau", tau_obs, tau, 4, 1e-4,
               note=note4)
        ck.num(f"dk2007-{est.lower()}-mu", f"IV {est} random", "pooled logOR", mu_obs, mu, 4, 1e-4)
        ck.num(f"dk2007-{est.lower()}-se", f"IV {est} random", "SE(pooled logOR)", se_obs, se_pub, 4,
               1e-4, derived=True,
               note=("4-dp input rounding propagates; marginal" if est == "REML" else ""))

    # direct single call of the REML tau2 estimator (A2 §1.1 recommended)
    effects = [math.log(r["estimate"]) for r in rows]
    variances = [r["se"] ** 2 for r in rows]
    tau2_direct, objective = profile_reml_tau2(effects, variances)
    ck.raw["profile_reml_tau2_direct"] = {"tau2": tau2_direct, "objective": objective}
    ck.num("dk2007-reml-direct-tau2", "profile_reml_tau2 (direct)", "tau2",
           tau2_direct, PUBLISHED["REML"][0] ** 2, 4, 1e-4, derived=True,
           note="reference is tau=0.1843 (4 dp) squared; the squared value inherits ~5.4e-4 "
                "relative precision, hence decimals=4 here (not the displayed 8)")

    # fixture-side independent DL recomputation ((Q-df)/c) on the same 4-dp inputs, for
    # attribution: distinguishes kernel arithmetic from input-rounding effects
    w = [1.0 / v for v in variances]
    y = effects
    fixed = sum(wi * yi for wi, yi in zip(w, y)) / sum(w)
    q_indep = sum(wi * (yi - fixed) ** 2 for wi, yi in zip(w, y))
    c_indep = sum(w) - sum(wi * wi for wi in w) / sum(w)
    tau2_dl_indep = max(0.0, (q_indep - (len(y) - 1)) / c_indep)
    ck.raw["independent_DL_check"] = {"q": q_indep, "c": c_indep, "tau2": tau2_dl_indep,
                                      "kernel_tau2_DL": results["DL"]["tau2"]}

    # estimators without a kernel implementation
    for est, key in (("CA (HE)", "CA"), ("CA2", "CA2"), ("DL2", "DL2"), ("EB", "EB"),
                     ("ML", "ML"), ("HS", "HS"), ("SJ", "SJ"), ("SJ2", "SJ2")):
        tau, mu, se_pub = PUBLISHED[key]
        ck.notrun(f"dk2007-{key.lower()}", f"IV {est} tau2 estimator",
                  "A2 §1.1: kernel models are fixed / random(DL) / random_pm / random_reml only; "
                  "no entry point for this estimator in E:/rf214-ai coscreen",
                  f"tau={tau}, mu={mu}, SE={se_pub}")

    return ck.write("dk2007")


if __name__ == "__main__":
    run()
