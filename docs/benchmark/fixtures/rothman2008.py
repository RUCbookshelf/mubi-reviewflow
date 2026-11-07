"""Fixture F: Rothman 2008 Modern Epidemiology MH examples — 3 cases.

Data + published values: A1 §3.1 (metafor analyses:rothman2008; book pages 260/264/274-278).
Case 1 Tolbutamide (2 strata 2x2): MH RD + MH logRR.
Case 2 British doctors (5 strata person-time): MH IRD + MH IRR. coscreen pool_mantel_haenszel
accepts integer counts only, so person-time (tabulated in units of 10^4 person-years) is scaled
by 10000 to person-years — exact integers. Scale behaviour of the MH functional forms:
  IRR estimate: r = sum(a*n2/n) and s = sum(c*n1/n) are scale invariant in the denominators;
  log-IRR variance p = sum(a*n1*d + c*n2*b)/n^2 is also scale invariant (events unscaled,
  n1*n2 and n^2 both scale by lambda^2) -> SE(logIRR) is reported unscaled;
  IRD estimate and its SE scale as 1/lambda (numerator invariant, k = sum(n1*n2/n) ~ lambda),
  so kernel outputs are multiplied by 10^4 back to per-10^4-PY units.
Case 3 Spermicide/Down syndrome (2 strata 2x2): MH logOR.
Published CIs are 90% CIs; the kernel emits 95% CIs, so 90% CIs are derived fixture-side from
the kernel estimate/SE (z90=1.6448536269514722) and clearly marked derived.
CMH/MH test statistics (1.19 / 11.0162 / 5.81) have no kernel output -> NOT-RUN.
"""

from __future__ import annotations

import math

from common import Checker

Z90 = 1.6448536269514722  # NormalDist().inv_cdf(0.95)

# Case 1: Tolbutamide (Rothman Table 15-1 p.260); ai,bi,ci,di rows = treatment dead/alive, placebo dead/alive
TOLBUTAMIDE = [
    ("Age<55", 8, 98, 5, 115),
    ("Age 55+", 22, 76, 16, 69),
]

# Case 2: British doctors (Table 15-2 p.264), person-time in units of 10^4 person-years
DOCTORS = [  # x1i, t1i(万PY), x2i, t2i(万PY)
    (32, 5.2407, 2, 1.8790),
    (104, 4.3248, 12, 1.0673),
    (206, 2.8612, 28, 0.5710),
    (186, 1.2663, 28, 0.2585),
    (102, 0.5317, 31, 0.1462),
]

# Case 3: Spermicide & Down syndrome (Table 15-5 p.276)
SPERMICIDE = [
    ("Age<35", 3, 9, 104, 1059),
    ("Age 35+", 1, 3, 5, 86),
]

SOURCE = "A1 §3.1 metafor analyses:rothman2008 (Rothman 2008 Tables 15-1/15-2/15-5)"


def _studies_2x2(strata: list[tuple[str, int, int, int, int]]) -> list[dict]:
    out = []
    for name, ai, bi, ci, di in strata:
        out.append({"study_id": name, "events_t": ai, "total_t": ai + bi,
                    "events_c": ci, "total_c": ci + di})
    return out


def _studies_persontime(rows: list[tuple[float, float, float, float]]) -> list[dict]:
    """Scale 万PY -> person-years (x10000) so all counts are integers."""
    out = []
    for i, (x1, t1, x2, t2) in enumerate(rows, 1):
        t1_py, t2_py = round(t1 * 10000), round(t2 * 10000)
        assert abs(t1 * 10000 - t1_py) < 1e-6 and abs(t2 * 10000 - t2_py) < 1e-6
        out.append({"study_id": f"Age band {i}", "events_t": int(x1), "total_t": t1_py,
                    "events_c": int(x2), "total_c": t2_py})
    return out


def run() -> dict:
    from coscreen.mantel_haenszel import pool_mantel_haenszel

    ck = Checker("Rothman2008", SOURCE)

    # ---------------- Case 1: Tolbutamide ---------------------------------------
    studies1 = _studies_2x2(TOLBUTAMIDE)
    rd1 = pool_mantel_haenszel(studies1, "RD")
    rr1 = pool_mantel_haenszel(studies1, "RR")
    ck.raw["case1"] = {"RD": rd1, "RR": rr1}

    ck.num("roth1-rd", "MH RD", "pooled RD", rd1["pooled"], 0.035, 3, 1e-3)
    ck.num("roth1-rd-se", "MH RD", "SE(RD)", rd1["se"], 0.032, 3, 1e-3)
    ck.num("roth1-rr-log", "MH RR", "logRR", math.log(rr1["pooled"]), 0.28, 2, 1e-4, derived=True)
    ck.num("roth1-rr", "MH RR", "RR", rr1["pooled"], 1.33, 2, 1e-3)
    ck.num("roth1-rr-se", "MH RR", "SE(logRR)", rr1["se"], 0.26, 2, 1e-4)
    ck.num("roth1-rr-ci90-low", "MH RR", "90% CI low (derived z90)",
           rr1["pooled"] * math.exp(-Z90 * rr1["se"]), 0.87, 2, 1e-3, derived=True)
    ck.num("roth1-rr-ci90-high", "MH RR", "90% CI high (derived z90)",
           rr1["pooled"] * math.exp(Z90 * rr1["se"]), 2.03, 2, 1e-3, derived=True)
    ck.notrun("roth1-cmh", "CMH test (correct=FALSE)",
              "pool_mantel_haenszel returns no test statistic (A2 §2 output fields)", "chi2=1.19, p=0.28")

    # ---------------- Case 2: British doctors (person-time) ----------------------
    studies2 = _studies_persontime(DOCTORS)
    ck.raw["case2_studies"] = studies2
    rd2 = pool_mantel_haenszel(studies2, "RD")   # person-year rate difference
    rr2 = pool_mantel_haenszel(studies2, "RR")   # rate ratio
    ck.raw["case2"] = {"RD_py": rd2, "RR": rr2}

    ird = rd2["pooled"] * 10000.0       # per 10^4 person-years
    ird_se = rd2["se"] * 10000.0
    logirr = math.log(rr2["pooled"])    # scale invariant
    logirr_se = rr2["se"]               # scale invariant (see header derivation)

    ck.num("roth2-ird", "MH IRD (person-time)", "IRD per 10^4 PY", ird, 11.44, 2, 1e-3,
           note="person-time scaled x10000 to integers; result rescaled (identity documented in header)")
    ck.num("roth2-ird-se", "MH IRD (person-time)", "SE(IRD)", ird_se, 3.09, 2, 1e-3,
           note="Greenland-Robins SE reference")
    ck.num("roth2-logirr", "MH IRR (person-time)", "logIRR", logirr, 0.35, 2, 1e-4, derived=True)
    ck.num("roth2-irr", "MH IRR (person-time)", "IRR", rr2["pooled"], 1.42, 2, 1e-3)
    ck.num("roth2-irr-se", "MH IRR (person-time)", "SE(logIRR)", logirr_se, 0.11, 2, 1e-4,
           note="kernel SE(log RR functional form), scale invariant; metafor reference is the "
                "Greenland-Robins IRR SE — formula variant question flagged for attribution")
    ck.num("roth2-irr-ci90-low", "MH IRR (person-time)", "90% CI low (derived z90)",
           rr2["pooled"] * math.exp(-Z90 * logirr_se), 1.19, 2, 1e-3, derived=True)
    ck.num("roth2-irr-ci90-high", "MH IRR (person-time)", "90% CI high (derived z90)",
           rr2["pooled"] * math.exp(Z90 * logirr_se), 1.70, 2, 1e-3, derived=True)
    ck.notrun("roth2-mh-test", "MH test (correct=FALSE)",
              "no test statistic in pool_mantel_haenszel output", "chi2=11.0162, p=0.0009")
    ck.notrun("roth2-q", "IRR heterogeneity Q(4)=26.88",
              "pool_mantel_haenszel emits no Q statistic", "Q=26.88")

    # ---------------- Case 3: Spermicide / Down syndrome -------------------------
    studies3 = _studies_2x2(SPERMICIDE)
    or3 = pool_mantel_haenszel(studies3, "OR")
    ck.raw["case3"] = {"OR": or3}

    ck.num("roth3-logor", "MH OR", "logOR", math.log(or3["pooled"]), 1.33, 2, 1e-4, derived=True)
    ck.num("roth3-or", "MH OR", "OR", or3["pooled"], 3.78, 2, 1e-3)
    ck.num("roth3-or-se", "MH OR", "SE(logOR)", or3["se"], 0.59, 2, 1e-4)
    ck.num("roth3-or-ci90-low", "MH OR", "90% CI low (derived z90)",
           or3["pooled"] * math.exp(-Z90 * or3["se"]), 1.43, 2, 1e-3, derived=True)
    ck.num("roth3-or-ci90-high", "MH OR", "90% CI high (derived z90)",
           or3["pooled"] * math.exp(Z90 * or3["se"]), 10.00, 2, 1e-3, derived=True)
    ck.notrun("roth3-cmh", "CMH test", "no test statistic in pool_mantel_haenszel output",
              "chi2=5.81, p=0.02")
    return ck.write("rothman2008")


if __name__ == "__main__":
    run()
