"""Fixture J: Senn 2013 diabetes network (26 studies, 10 treatments, 53 arm rows) — NMA.

Data + published values: A1 §7.1 (metadat::dat.senn2013 full arm table; published values are the
page's contrast-based RANDOM-EFFECTS NMA vs placebo, tau2=0.0999, QE(18)=96.98, P-scores).

Fixture-side contrast construction (documented, deterministic):
- every 2-arm study contributes one contrast; placebo-comparator studies use (active - placebo);
  the 7 studies without a placebo arm contribute the explicit active-vs-active contrast listed
  in ACTIVE_ACTIVE below (sign convention fixed there);
- contrast variance = UNPOOLED MD variance sd1^2/n1 + sd2^2/n2 — the metafor escalc(measure="MD")
  default convention. Verified numerically during fixture bring-up: this convention reproduces
  the published tau2=0.0999, all nine vs-placebo estimates to 4 dp and QE=96.98; the pooled-SD
  (HO) variant does not (both variants' outputs stored in raw for attribution);
- Willms 1999 is 3-arm (metformin, acarbose, placebo): both contrasts vs placebo share the
  placebo arm, covariance block off-diagonal = s_pbo^2/n_pbo (27 contrasts, 9 non-reference
  parameters -> df 18 = published).

Kernel paths exercised:
- coscreen/network_gls.synthesize_network  (FE GLS, full covariance blocks): its residual_q is
  the inverse-sampling-variance weighted network Q — the same model-independent quantity
  metafor prints as QE -> CHECKED vs 96.98 (FE estimates themselves have no published reference)
- coscreen/network_random.synthesize_network_random (RE common tau2, profile REML) -> estimates
  and tau2 CHECKED; its residual_q is an RE-weighted diagnostic (different statistic) -> raw only
- coscreen/network_pscores.rank_network_pscores  (benefit="lower") -> CHECKED
- coscreen/network_analysis.synthesize (DB WLS; multi-arm rejected -> Willms split into two
  pseudo-studies, approximation documented) -> raw (INFO)
"""

from __future__ import annotations

import math
import os
from contextlib import closing

from common import Checker, RESULT_DIR

# study, [(treatment, ni, mi, sdi), ...]  — A1 §7.1 verbatim (53 arm rows)
ARMS = [
    ("De Fronzo (1995)", [("metformin", 213, -1.70, 1.459), ("placebo", 209, 0.20, 1.446)]),
    ("Lewin (2007)", [("metformin", 431, -0.74, 1.106), ("placebo", 144, 0.08, 1.004)]),
    ("Willms (1999)", [("metformin", 29, -2.50, 0.862), ("acarbose", 31, -2.30, 1.782),
                       ("placebo", 29, -1.30, 1.831)]),
    ("Davidson (2007)", [("rosiglitazone", 117, -1.20, 1.097), ("placebo", 116, 0.14, 1.093)]),
    ("Wolffenbuttel (1999)", [("rosiglitazone", 183, -0.90, 1.100), ("placebo", 192, 0.20, 1.110)]),
    ("Kipnes (2001)", [("pioglitazone", 182, -1.20, 1.369), ("placebo", 181, 0.10, 1.024)]),
    ("Kerenyi (2004)", [("rosiglitazone", 160, -0.91, 0.990), ("placebo", 154, -0.14, 0.920)]),
    ("Hanefeld (2004)", [("pioglitazone", 319, 7.61, 1.072), ("metformin", 320, 7.45, 1.073)]),
    ("Derosa (2004)", [("pioglitazone", 45, 6.80, 0.800), ("rosiglitazone", 42, 6.70, 0.900)]),
    ("Baksi (2004)", [("rosiglitazone", 218, -1.20, 1.112), ("placebo", 233, 0.10, 1.036)]),
    ("Rosenstock (2008)", [("rosiglitazone", 59, -1.17, 1.229), ("placebo", 57, -0.08, 1.208)]),
    ("Zhu (2003)", [("rosiglitazone", 210, -1.90, 1.470), ("placebo", 105, -0.40, 1.300)]),
    ("Yang (2003)", [("rosiglitazone", 102, -1.09, 1.650), ("metformin", 96, -0.95, 1.500)]),
    ("Vongthavaravat (2002)", [("rosiglitazone", 164, -1.10, 1.559), ("sulfonylurea", 170, 0.10, 0.992)]),
    ("Oyama (2008)", [("acarbose", 41, -0.70, 0.800), ("sulfonylurea", 43, -0.30, 0.600)]),
    ("Costa (1997)", [("acarbose", 36, -1.10, 0.360), ("placebo", 29, -0.30, 0.700)]),
    ("Hermansen (2007)", [("sitagliptin", 106, -0.30, 0.940), ("placebo", 106, 0.27, 0.940)]),
    ("Garber (2008)", [("vildagliptin", 132, -0.63, 1.034), ("placebo", 144, 0.07, 1.080)]),
    ("Alex (1998)", [("metformin", 291, 0.13, 1.428), ("sulfonylurea", 300, 0.50, 1.450)]),
    ("Johnston (1994)", [("miglitol", 68, -0.41, 1.072), ("placebo", 63, 0.33, 1.032)]),
    ("Johnston (1998a)", [("miglitol", 91, -0.43, 0.954), ("placebo", 43, 0.98, 1.311)]),
    ("Kim (2007)", [("rosiglitazone", 57, -1.10, 1.341), ("metformin", 56, -1.10, 1.139)]),
    ("Johnston (1998b)", [("miglitol", 49, -0.12, 1.400), ("placebo", 34, 0.56, 1.166)]),
    ("Gonzalez-Ortiz (2004)", [("metformin", 34, -1.30, 1.861), ("placebo", 37, -0.90, 1.803)]),
    ("Stucci (1996)", [("benfluorex", 28, 8.03, 1.290), ("placebo", 30, 8.26, 1.350)]),
    ("Moulin (2006)", [("benfluorex", 161, -0.82, 1.028), ("placebo", 156, 0.19, 1.374)]),
]

# studies without a placebo arm: explicit (treatment, comparator) sign convention
ACTIVE_ACTIVE = {
    "Hanefeld (2004)": ("pioglitazone", "metformin"),
    "Derosa (2004)": ("pioglitazone", "rosiglitazone"),
    "Yang (2003)": ("rosiglitazone", "metformin"),
    "Vongthavaravat (2002)": ("rosiglitazone", "sulfonylurea"),
    "Oyama (2008)": ("acarbose", "sulfonylurea"),
    "Alex (1998)": ("metformin", "sulfonylurea"),
    "Kim (2007)": ("rosiglitazone", "metformin"),
}

PUBLISHED_RE = {  # MD vs placebo, A1 §7.1 (random-effects NMA)
    "rosiglitazone": -1.2337,
    "metformin": -1.1284,
    "pioglitazone": -1.1294,
    "miglitol": -0.9499,
    "acarbose": -0.8414,
    "benfluorex": -0.7369,
    "vildagliptin": -0.7000,
    "sitagliptin": -0.5700,
    "sulfonylurea": -0.4175,
}
PUBLISHED_TAU2 = 0.0999
PUBLISHED_QE = 96.98
PUBLISHED_PSCORES = {"rosiglitazone": 0.898, "placebo": 0.012}

SOURCE = "A1 §7.1 metadat::dat.senn2013 (published values = page's random-effects NMA vs placebo)"


def _lookup(study: str, treatment: str):
    arms = {name: (n, mi, sdi) for name, n, mi, sdi in dict(ARMS)[study]}
    return arms[treatment]


def _md_contrast(study: str, trt: str, cmp_: str, pooled: bool = False) -> tuple[float, float]:
    """estimate and variance for trt - cmp_; unpooled escalc-MD default, or pooled-SD (HO)."""
    n1, m1, s1 = _lookup(study, trt)
    n2, m2, s2 = _lookup(study, cmp_)
    if pooled:
        sp2 = ((n1 - 1) * s1 * s1 + (n2 - 1) * s2 * s2) / (n1 + n2 - 2)
        var = sp2 * (1.0 / n1 + 1.0 / n2)
    else:
        var = s1 * s1 / n1 + s2 * s2 / n2
    return m1 - m2, var


def build_studies(pooled: bool = False) -> list[dict]:
    """GLS study blocks; multi-arm Willms gets a proper 2x2 covariance block."""
    out = []
    for study, arms in ARMS:
        by_name = {name: (n, mi, sdi) for name, n, mi, sdi in arms}
        if study == "Willms (1999)":
            n_pbo, _, s_pbo = by_name["placebo"]
            e_m, v_m = _md_contrast(study, "metformin", "placebo", pooled)
            e_a, v_a = _md_contrast(study, "acarbose", "placebo", pooled)
            cov = s_pbo * s_pbo / n_pbo
            out.append({
                "study_id": study,
                "covariance_scale": "natural",
                "contrasts": [
                    {"treatment": "metformin", "comparator": "placebo", "estimate": e_m},
                    {"treatment": "acarbose", "comparator": "placebo", "estimate": e_a},
                ],
                "covariance": [[v_m, cov], [cov, v_a]],
            })
            continue
        if study in ACTIVE_ACTIVE:
            trt, cmp_ = ACTIVE_ACTIVE[study]
        else:
            non_pbo = [name for name in by_name if name != "placebo"]
            assert len(non_pbo) == 1
            trt, cmp_ = non_pbo[0], "placebo"
        est, var = _md_contrast(study, trt, cmp_, pooled)
        out.append({
            "study_id": study,
            "covariance_scale": "natural",
            "contrasts": [{"treatment": trt, "comparator": cmp_, "estimate": est}],
            "covariance": [[var]],
        })
    return out


def flat_contrasts_for_db() -> list[tuple[str, str, float, float]]:
    """(study_id, comparison, estimate, se); Willms split into two pseudo-studies."""
    rows = []
    for block in build_studies():
        variances = block["covariance"]
        for i, contrast in enumerate(block["contrasts"]):
            sid = block["study_id"]
            if block["study_id"] == "Willms (1999)":
                sid = f"Willms (1999) [{contrast['treatment']}]"
            rows.append((sid, f"{contrast['treatment']} vs {contrast['comparator']}",
                         contrast["estimate"], math.sqrt(variances[i][i])))
    return rows


def run() -> dict:
    from coscreen.db import _connect, init_db
    from coscreen.network_gls import synthesize_network
    from coscreen.network_random import synthesize_network_random
    from coscreen.network_pscores import rank_network_pscores
    from coscreen.network_analysis import synthesize as db_network_synthesize
    from coscreen.review_analysis import save_effect, save_study

    ck = Checker("Senn2013", SOURCE)
    studies = build_studies()
    ck.raw["studies"] = studies
    n_contrasts = sum(len(s["contrasts"]) for s in studies)
    ck.raw["n_contrasts"] = n_contrasts

    # ---- fixed-effect GLS: residual_q is the model-independent network QE ----------
    fe = synthesize_network(studies, "MD", "placebo")
    ck.raw["fe"] = {"residual_q": fe["residual_q"], "residual_df": fe["residual_df"],
                    "estimates": fe["estimates"]}
    ck.num("senn-fe-qe", "NMA fixed-effect GLS (QE statistic)", "QE", fe["residual_q"],
           PUBLISHED_QE, 2, 1e-4)
    ck.integer("senn-fe-qe-df", "NMA fixed-effect GLS (QE statistic)", "QE df", fe["residual_df"], 18)
    ck.info("senn-fe-estimates", "NMA fixed-effect GLS estimates",
            "executed; A1 §7.1 publishes no FE vs-placebo estimates (random-effects only), so no "
            "verdict assignable; FE estimates stored in raw for attribution", "MD vs placebo x9")

    # ---- random-effects NMA (matches the published model class) ------------------
    re = synthesize_network_random(studies, "MD", "placebo")
    ck.raw["re"] = {"tau2": re["random_effects"]["tau2"], "residual_q": re["residual_q"],
                    "residual_df": re["residual_df"], "n_studies": re["n_studies"],
                    "n_contrasts": re["n_contrasts"], "estimates": re["estimates"]}

    by_contrast = {(e["treatment"], e["comparator"]): e for e in re["estimates"]}
    for trt, pub in PUBLISHED_RE.items():
        est = by_contrast[(trt, "placebo")] if (trt, "placebo") in by_contrast \
            else by_contrast[("placebo", trt)]
        value = est["estimate"] if est["treatment"] == trt else -est["estimate"]
        ck.num(f"senn-re-{trt}", "NMA random (common tau2 REML)", f"MD {trt} vs placebo",
               value, pub, 4, 1e-4)
    ck.num("senn-re-tau2", "NMA random (common tau2 REML)", "tau2",
           re["random_effects"]["tau2"], PUBLISHED_TAU2, 4, 1e-4)
    ck.integer("senn-re-nstudies", "NMA random (common tau2 REML)", "n_studies", re["n_studies"], 26)
    ck.info("senn-re-residual-q", "NMA random residual_q (RE-weighted diagnostic)",
            f"executed; observed {re['residual_q']:.6f} (df {re['residual_df']}) — this RE-weighted "
            "diagnostic is a different statistic from metafor's model-independent QE=96.98 "
            "(checked above on the FE GLS path), so no verdict is assigned here")

    # auxiliary: pooled-SD (HO) variance variant, kept for attribution (does NOT match published)
    studies_ho = build_studies(pooled=True)
    fe_ho = synthesize_network(studies_ho, "MD", "placebo")
    re_ho = synthesize_network_random(studies_ho, "MD", "placebo")
    ck.raw["variant_pooled_sd_ho"] = {
        "note": "contrast variances sp2*(1/n1+1/n2) instead of escalc-MD default; kept for "
                "attribution — published values match the unpooled variant above",
        "fe_qe": fe_ho["residual_q"],
        "re_tau2": re_ho["random_effects"]["tau2"],
        "re_estimates_vs_placebo": {
            t: (e["estimate"] if e["treatment"] == t else -e["estimate"])
            for e in re_ho["estimates"] for t in [e["treatment"], e["comparator"]]
            if "placebo" in (e["treatment"], e["comparator"])},
    }

    ps = rank_network_pscores(re, benefit="lower")
    ck.raw["pscores"] = ps
    score_map = {row["treatment"]: row["p_score"] for row in ps["ranking"]}
    for trt, pub in PUBLISHED_PSCORES.items():
        ck.num(f"senn-pscore-{trt}", "P-score (lower=better)", f"P-score {trt}",
               score_map[trt], pub, 3, 1e-3)

    # ---- DB WLS entry (A2 §6.1); Willms split into two pseudo-studies -------------
    db_path = RESULT_DIR / "senn2013_tmp.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    init_db(str(db_path))
    for i, (sid, comp, est, se) in enumerate(flat_contrasts_for_db(), 1):
        key = f"zot-senn-{i:02d}"
        with closing(_connect(str(db_path))) as conn, conn:
            conn.execute("INSERT INTO articles(zotero_key, import_order) VALUES (?,?)", (key, 100 + i))
        save_study(str(db_path), sid, sid, [key])
        save_effect(str(db_path), sid, comp, "hba1c", "post", "MD", est, se, key,
                    entry_method="manual")
    db_net = db_network_synthesize(str(db_path), "hba1c", "post", "MD", "placebo")
    ck.raw["db_wls"] = {"residual_q": db_net["residual_q"], "residual_df": db_net["residual_df"],
                        "n_studies": db_net["n_studies"], "estimates": db_net["estimates"]}
    try:
        os.remove(str(db_path))
    except PermissionError:
        pass  # sqlite handle held open on Windows; the next run recreates the file
    ck.info("senn-db-wls", "NMA DB WLS (network_analysis.synthesize)",
            "executed with the A2 §1.4 DB fixture recipe; multi-arm studies are rejected by this "
            "entry, so Willms 1999 was split into two pseudo-studies (placebo arm double-counted, "
            "within-study covariance ignored) — a documented approximation, no verdict assignable; "
            "outputs stored in raw")

    ck.notrun("senn-bayes", "Bayesian NMA (gemtc thrombolytics, A1 §7.2)",
              "A1 §7.2: arm-level data only obtainable via the gemtc R package; no R runtime on this "
              "machine and no network access; MCMC posteriors only sanity-level tolerance anyway")
    ck.notrun("senn-netmeta", "netmeta graph-theoretic NMA (A1 §7.3)",
              "A1 §7.3: contrast-level TE/seTE table and versus-placebo printout not obtained "
              "(vignette PDF unparseable in A1 environment); only fragments I2=84%, p=0.0021")
    return ck.write("senn2013")


if __name__ == "__main__":
    run()
