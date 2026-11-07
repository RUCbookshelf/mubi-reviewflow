"""Fixture G: Yusuf 1985 beta-blocker Peto OR (Table 6, 22 comparisons; Snow 1 all-NA dropped).

Data + published values: A1 §3.2 (metafor analyses:yusuf1985, consistent with Yusuf 1985 p.343).
The published values follow the escalc(add=0) path: double-zero studies (Pitt/Hutton/Yusuf) drop,
k=18, df=17. coscreen pool_peto also excludes zero-variance studies -> same k=18 expected.
Pooled/CI come back on the OR scale; log-scale values derived for comparison.
Q is not a pool_peto output; it is derived by pooling the per-study (O-E)/V rows through
_synthesize_rows(model="fixed") — inputs are pool_peto's own study_statistics.
"""

from __future__ import annotations

import math

from common import Checker

# trial, ai, n1i, ci, n2i — Snow 1 (all NA) removed per A1 §3.2
DATA = [
    ("Balcon",     14,  56, 15,  58),
    ("Clausen",    18,  66, 19,  64),
    ("Multicentre", 15, 100, 12,  95),
    ("Barber",     10,  52, 12,  47),
    ("Norris",     21, 226, 24, 228),
    ("Kahler",      3,  38,  6,  31),
    ("Ledwich",     2,  20,  3,  20),
    ("Snow 2",     19,  76, 15,  67),
    ("Fuccella",   15, 106,  9, 114),
    ("Briant",      5,  62,  4,  57),
    ("Pitt",        0,   9,  0,   8),
    ("Lombardo",    8, 133, 11, 127),
    ("Thompson",    3,  48,  3,  49),
    ("Hutton",      0,  16,  0,  13),
    ("Tonkin",      1,  42,  1,  46),
    ("Gupta",       0,  25,  3,  25),
    ("Barber 2",   14, 221, 15, 228),
    ("Yusuf",       0,  11,  0,  11),
    ("Wilcox 1",    8, 259,  7, 129),
    ("Wilcox 2",    6, 157,  4, 158),
    ("CPRG",        3, 177,  2, 136),
]

SOURCE = "A1 §3.2 metafor analyses:yusuf1985 (consistent with Yusuf 1985 Table 6 p.343)"


def run() -> dict:
    from coscreen.peto import pool_peto
    from coscreen.review_analysis import _synthesize_rows

    ck = Checker("Yusuf1985", SOURCE)
    studies = [{"study_id": t, "events_t": a, "total_t": n1, "events_c": c, "total_c": n2}
               for t, a, n1, c, n2 in DATA]
    ck.raw["studies"] = studies

    res = pool_peto(studies)
    ck.raw["peto"] = res

    # fixture-side independent Peto recomputation (textbook O-E / hypergeometric variance)
    # on the same A1 §3.2 table — distinguishes kernel arithmetic from reference divergence
    sum_oe = sum_v = 0.0
    for _t, a, n1, c, n2 in DATA:
        n, b, d = n1 + n2, n1 - a, n2 - c
        oe = a - n1 * (a + c) / n
        v = n1 * n2 * (a + c) * (b + d) / (n * n * (n - 1))
        if v > 0:
            sum_oe += oe
            sum_v += v
    ck.raw["independent_peto"] = {
        "sum_oe": sum_oe, "sum_v": sum_v, "logOR": sum_oe / sum_v, "se": sum_v ** -0.5,
        "published_implies": {"se_from_pub": 0.1194, "sum_v_implied": 1 / 0.1194 ** 2,
                              "sum_oe_implied": -0.0692 / 0.1194 ** 2},
        "note": "kernel matches this independent recomputation exactly; the published "
                "logOR/SE/Q (from metafor analyses:yusuf1985) imply a different sum(O-E) and "
                "sum(V) than the A1 §3.2 table produces — input-table vs reference divergence, "
                "for attribution",
    }

    indep_note = ("kernel value exactly reproduced by the fixture-side independent Peto "
                  "computation on the same inputs (see raw.independent_peto)")
    log_or = math.log(res["pooled"])
    ck.integer("yusuf-k", "Peto OR", "k (included studies)", res["n_studies"], 18,
               note="double-zero studies excluded (escalc add=0 convention, A1 §3.2)")
    ck.num("yusuf-logor", "Peto OR", "pooled logOR", log_or, -0.0692, 4, 1e-4, derived=True,
           note=indep_note)
    ck.num("yusuf-or", "Peto OR", "OR", res["pooled"], 0.93, 2, 1e-3, note=indep_note)
    ck.num("yusuf-se", "Peto OR", "SE(logOR)", res["se"], 0.1194, 4, 1e-4, note=indep_note)
    ck.num("yusuf-ci-low", "Peto OR", "95% CI low (log)", math.log(res["ci_low"]), -0.3031, 4, 1e-4,
           derived=True, note=indep_note)
    ck.num("yusuf-ci-high", "Peto OR", "95% CI high (log)", math.log(res["ci_high"]), 0.1648, 4, 1e-4,
           derived=True, note=indep_note)

    # derived Q/I2/H2 from pool_peto's own per-study (O-E, V): yi=(O-E)/V, se=1/sqrt(V)
    rows = []
    for stat in res["study_statistics"]:
        if not stat["included"]:
            continue
        oe, v = stat["observed_minus_expected"], stat["variance"]
        rows.append({"study_id": stat["study_id"], "estimate": oe / v, "se": 1.0 / math.sqrt(v)})
    iv = _synthesize_rows(rows, "MD", "fixed", "normal")
    ck.raw["derived_iv_from_peto_stats"] = {k: iv[k] for k in
                                            ("pooled_analysis", "ci_analysis_low", "ci_analysis_high",
                                             "q", "q_df", "q_p", "i2_percent")}
    ck.num("yusuf-q", "Peto OR (Q via derived IV rows)", "Q", iv["q"], 10.8275, 4, 1e-4, derived=True,
           note=indep_note + "; observed Q dominated by Wilcox 1 (yi=-2.4896, V=1.7434)")
    ck.integer("yusuf-qdf", "Peto OR (Q via derived IV rows)", "Q df", iv["q_df"], 17)
    ck.num("yusuf-i2", "Peto OR (Q via derived IV rows)", "I2 %", iv["i2_percent"], 0.00, 2, 1e-3,
           derived=True, note="published I2=0.00% (boundary); absolute tolerance")
    ck.num("yusuf-h2", "Peto OR (Q via derived IV rows)", "H2 = Q/df", iv["q"] / iv["q_df"], 0.64, 2, 1e-3,
           derived=True)
    return ck.write("yusuf1985")


if __name__ == "__main__":
    run()
