"""Fixture D: Raudenbush & Bryk 1985 teacher expectancy (19 studies, SMD) — REML set 2 of 3.

Data + published values: A1 §2.2 (metafor analyses:raudenbush1985, declared consistent with
R&B 1985 eq.(27) restricted likelihood results; data checked against metadat::dat.raudenbush1985).
Also runs the published REML meta-regression on prior contact weeks capped at 3 (A1 gives the
weeks column), via coscreen/meta_regression_model.fit_meta_regression (needs >=10 studies; 19 ok).
"""

from __future__ import annotations

from common import Checker, derive_pooled_se

# study, weeks, yi, vi   (A1 §2.2 verbatim)
DATA = [
    ("Rosenthal et al. 1974",        2,  0.0300, 0.0156),
    ("Conn et al. 1968",            21,  0.1200, 0.0216),
    ("Jose & Cody 1971",            19, -0.1400, 0.0279),
    ("Pellegrini & Hicks 1972a",     0,  1.1800, 0.1391),
    ("Pellegrini & Hicks 1972b",     0,  0.2600, 0.1362),
    ("Evans & Rosenthal 1969",       3, -0.0600, 0.0106),
    ("Fielder et al. 1971",         17, -0.0200, 0.0106),
    ("Claiborn 1969",               24, -0.3200, 0.0484),
    ("Kester 1969",                  0,  0.2700, 0.0269),
    ("Maxwell 1970",                 1,  0.8000, 0.0630),
    ("Carter 1970",                  0,  0.5400, 0.0912),
    ("Flowers 1966",                 0,  0.1800, 0.0497),
    ("Keshock 1970",                 1, -0.0200, 0.0835),
    ("Henrikson 1970",               2,  0.2300, 0.0841),
    ("Fine 1972",                   17, -0.1800, 0.0253),
    ("Grieger 1970",                 5, -0.0600, 0.0279),
    ("Rosenthal & Jacobson 1968",    1,  0.3000, 0.0193),
    ("Fleming & Anttonen 1971",      2,  0.0700, 0.0088),
    ("Ginsburg 1970",                7, -0.0700, 0.0303),
]

SOURCE = "A1 §2.2 metafor analyses:raudenbush1985 (declared consistent with R&B 1985 eq.(27))"


def build_rows() -> list[dict]:
    return [{"study_id": name, "measure": "SMD", "estimate": yi, "se": vi ** 0.5}
            for name, _, yi, vi in DATA]


def run() -> dict:
    from coscreen.review_analysis import _synthesize_rows
    from coscreen.meta_regression_model import fit_meta_regression, profile_reml_tau2

    ck = Checker("Raudenbush1985", SOURCE)
    rows = build_rows()
    ck.raw["rows"] = rows

    reml = _synthesize_rows(rows, "SMD", "random_reml", "normal")
    ck.raw["reml"] = reml

    ck.num("rb-reml-tau2", "IV REML random", "tau2", reml["tau2"], 0.019, 3, 1e-4)
    ck.num("rb-reml-pooled", "IV REML random", "pooled SMD", reml["pooled_analysis"], 0.084, 3, 1e-4)
    ck.num("rb-reml-se", "IV REML random", "SE(pooled)", derive_pooled_se(reml, "random_reml"), 0.052, 3, 1e-4, derived=True)
    ck.num("rb-reml-z", "IV REML random", "z = pooled/SE",
           reml["pooled_analysis"] / derive_pooled_se(reml, "random_reml"), 1.621, 3, 1e-4, derived=True)
    ck.num("rb-reml-ci-low", "IV REML random", "95% CI low", reml["ci_analysis_low"], -0.018, 3, 1e-4)
    ck.num("rb-reml-ci-high", "IV REML random", "95% CI high", reml["ci_analysis_high"], 0.185, 3, 1e-4)
    ck.num("rb-reml-q", "IV REML random", "Q", reml["q"], 35.830, 3, 1e-4)
    ck.integer("rb-reml-qdf", "IV REML random", "Q df", reml["q_df"], 18)
    # I2/H2: page prints 41.86%/1.72 but its own Q(18)=35.830 implies (Q-df)/Q=49.76%/Q/df=1.99 —
    # same discrepancy class as BCG I2 ⚠ (A1 §1.1). Primary = Q-derived, secondary = page-printed.
    ck.num("rb-reml-i2", "IV REML random", "I2 % (derived from published Q)",
           reml["i2_percent"], (35.830 - 18) / 35.830 * 100, 2, 1e-3, derived=True,
           note="A1 recomputation policy; page prints 41.86% (see secondary row)")
    ck.num("rb-reml-i2-page", "IV REML random", "I2 % (page-printed, secondary)",
           reml["i2_percent"], 41.86, 2, 1e-3,
           note="page value not derivable from the page's own Q=35.830 (df=18); flagged for attribution")
    ck.num("rb-reml-h2", "IV REML random", "H2 = Q/df (from published Q)",
           reml["q"] / reml["q_df"], 35.830 / 18, 2, 1e-3, derived=True,
           note="page prints 1.72 (see secondary row)")
    ck.num("rb-reml-h2-page", "IV REML random", "H2 (page-printed, secondary)",
           reml["q"] / reml["q_df"], 1.72, 2, 1e-3, derived=True,
           note="page value not derivable from the page's own Q; flagged for attribution")

    tau2_direct, objective = profile_reml_tau2([r["estimate"] for r in rows],
                                               [r["se"] ** 2 for r in rows])
    ck.raw["profile_reml_tau2_direct"] = {"tau2": tau2_direct, "objective": objective}
    ck.num("rb-reml-direct-tau2", "profile_reml_tau2 (direct)", "tau2", tau2_direct, 0.019, 3, 1e-4)

    # ---- REML meta-regression on weeks capped at 3 (A1 §2.2) --------------------
    capped = {name: {"weeks": (3 if w > 3 else w)} for name, w, _, _ in DATA}
    mr = fit_meta_regression(rows, capped, {"weeks": "continuous"}, references={}, interactions=[])
    ck.raw["metareg"] = mr
    coeffs = {c["name"]: c for c in mr["coefficients"]}
    ck.num("rb-mr-intrcpt", "REML meta-regression (weeks capped 3)", "intrcpt",
           coeffs["(Intercept)"]["estimate"], 0.407, 3, 1e-4)
    ck.num("rb-mr-intrcpt-se", "REML meta-regression (weeks capped 3)", "intrcpt SE",
           coeffs["(Intercept)"]["se"], 0.087, 3, 1e-4)
    ck.num("rb-mr-slope", "REML meta-regression (weeks capped 3)", "weeks slope",
           coeffs["weeks"]["estimate"], -0.157, 3, 1e-4)
    ck.num("rb-mr-slope-se", "REML meta-regression (weeks capped 3)", "weeks slope SE",
           coeffs["weeks"]["se"], 0.036, 3, 1e-4)
    ck.num("rb-mr-tau2", "REML meta-regression (weeks capped 3)", "residual tau2",
           mr["tau2"], 0.000, 3, 1e-4, note="published as 0.000 (boundary)")
    ck.num("rb-mr-qe", "REML meta-regression (weeks capped 3)", "QE",
           mr["residual_q"], 16.571, 3, 1e-4)
    ck.integer("rb-mr-qe-df", "REML meta-regression (weeks capped 3)", "QE df", mr["residual_df"], 17)
    ck.num("rb-mr-qm", "REML meta-regression (weeks capped 3)", "QM (joint Wald chi2)",
           mr["moderator_test"]["statistic"], 19.258, 3, 1e-4)
    ck.integer("rb-mr-qm-df", "REML meta-regression (weeks capped 3)", "QM df",
               mr["moderator_test"]["df1"], 1)
    return ck.write("raudenbush1985")


if __name__ == "__main__":
    run()
