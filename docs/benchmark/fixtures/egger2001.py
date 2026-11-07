"""Fixture H: Egger 2001 magnesium MI trials (dat.egger2001), ISIS-4 dropped -> k=15.

Data + published values: A1 §8 (metafor regtest reference page example; data from
metadat::dat.egger2001). Published: underlying REML model tau2=0.1911, pooled logOR=-0.8745
CI [-1.2822,-0.4669]; regtest rma-version z=-2.8062 p=0.0050; classic lm-version t=-3.1783
df=13 p=0.0073.
yi/vi computed fixture-side with the metafor escalc(measure="OR") default convention
(add=0.5, to="only0"): tables without zero cells are used raw; only Bertschat (0/22 vs 1/21)
is adjusted. The kernel Egger entry (small_study_effects) is DB-backed (A2 §7) -> DB fixture.
"""

from __future__ import annotations

import math
import os
from contextlib import closing

from common import Checker, RESULT_DIR, logit_or_variance

# study, year, ai, n1i, ci, n2i  (A1 §8 verbatim; row 16 ISIS-4 excluded from the analysis)
DATA = [
    ("Morton",       1984,   1,   40,   2,   36),
    ("Rasmussen",    1986,   9,  135,  23,  135),
    ("Smith",        1986,   2,  200,   7,  200),
    ("Abraham",      1987,   1,   48,   1,   46),
    ("Feldstedt",    1988,  10,  150,   8,  148),
    ("Shechter",     1989,   1,   59,   9,   56),
    ("Ceremuzynski", 1989,   1,   25,   3,   23),
    ("Bertschat",    1989,   0,   22,   1,   21),
    ("Singh",        1990,   6,   76,  11,   75),
    ("Pereira",      1990,   1,   27,   7,   27),
    ("Shechter2",    1991,   2,   89,  12,   80),
    ("Golf",         1991,   5,   23,  13,   33),
    ("Thogersen",    1991,   4,  130,   8,  122),
    ("LIMIT-2",      1992,  90, 1159, 118, 1157),
    ("Shechter3",    1995,   4,  107,  17,  108),
]

SOURCE = "A1 §8 metafor regtest reference page + metadat::dat.egger2001"


def escalc_or(ai: int, n1: int, ci: int, n2: int) -> tuple[float, float]:
    """logOR and variance with metafor escalc default add=0.5, to='only0'."""
    a, b, c, d = ai, n1 - ai, ci, n2 - ci
    if min(a, b, c, d) == 0:
        a, b, c, d = a + 0.5, b + 0.5, c + 0.5, d + 0.5
    return math.log((a * d) / (b * c)), logit_or_variance(a, b, c, d)


def build_rows() -> list[dict]:
    rows = []
    for study, _year, ai, n1, ci, n2 in DATA:
        yi, vi = escalc_or(ai, n1, ci, n2)
        rows.append({"study_id": study, "estimate": math.exp(yi), "se": math.sqrt(vi)})
    return rows


def run() -> dict:
    from coscreen.db import _connect, init_db
    from coscreen.review_analysis import _synthesize_rows, save_effect, save_study
    from coscreen.advanced_analysis import small_study_effects

    ck = Checker("Egger2001", SOURCE)
    rows = build_rows()
    ck.raw["rows"] = rows
    ck.raw["yi_vi"] = [(math.log(r["estimate"]), r["se"] ** 2) for r in rows]

    # ---- underlying REML model via the pure-path synthesizer ---------------------
    reml = _synthesize_rows(rows, "OR", "random_reml", "normal")
    ck.raw["reml"] = {k: reml[k] for k in ("pooled_analysis", "ci_analysis_low", "ci_analysis_high",
                                           "tau2", "q", "i2_percent")}
    ck.num("egger-reml-tau2", "IV REML random (underlying model)", "tau2", reml["tau2"], 0.1911, 4, 1e-4)
    ck.num("egger-reml-pooled", "IV REML random (underlying model)", "pooled logOR",
           reml["pooled_analysis"], -0.8745, 4, 1e-4)
    ck.num("egger-reml-ci-low", "IV REML random (underlying model)", "95% CI low",
           reml["ci_analysis_low"], -1.2822, 4, 1e-4)
    ck.num("egger-reml-ci-high", "IV REML random (underlying model)", "95% CI high",
           reml["ci_analysis_high"], -0.4669, 4, 1e-4)

    # ---- Egger test through the DB-backed public entry (A2 §7) -------------------
    db_path = RESULT_DIR / "egger2001_tmp.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    init_db(str(db_path))
    for i, (row, (study, _y, _a, _n1, _c, _n2)) in enumerate(zip(rows, DATA), 1):
        key = f"zot-egger-{i:02d}"
        with closing(_connect(str(db_path))) as conn, conn:
            conn.execute("INSERT INTO articles(zotero_key, import_order) VALUES (?,?)", (key, i))
        save_study(str(db_path), study, study, [key])
        save_effect(str(db_path), study, "Mg vs control", "mortality", "post", "OR",
                    row["estimate"], row["se"], key, entry_method="manual")
    sse = small_study_effects(str(db_path), "Mg vs control", "mortality", "post", "OR")
    ck.raw["small_study_effects"] = {k: sse[k] for k in ("n_studies", "egger_intercept", "egger_p")}
    ck.integer("egger-k", "Egger regression (small_study_effects)", "n_studies", sse["n_studies"], 15)

    # coscreen regresses y/se on 1/se: its intercept is the asymmetry coefficient, i.e. the
    # analogue of the tested coefficient in metafor regtest (z=-2.8062 rma version; t=-3.1783
    # classic lm version). The kernel exposes only intercept + p -> compare p against both
    # published variants; statistic itself is recorded in raw for attribution.
    ck.num("egger-p-rma", "Egger regression (small_study_effects)",
           "p (vs regtest rma-version z=-2.8062)",
           sse["egger_p"], 0.0050, 4, 1e-2,
           note="kernel regresses y/se on 1/se unweighted (classic Egger 1997 scaled form), "
                "not the REML-weighted metafor regtest default")
    ck.num("egger-p-lm", "Egger regression (small_study_effects)",
           "p (vs regtest classic lm t=-3.1783)",
           sse["egger_p"], 0.0073, 4, 1e-2,
           note="secondary: same observed p against the classic lm variant - the form closest "
                "to the kernel construction")

    ck.notrun("egger-z", "Egger regression test statistic",
              "small_study_effects returns intercept and p only; no z/t statistic field to compare "
              "(z=-2.8062 / t=-3.1783 recorded here)")
    ck.notrun("egger-limit-estimates", "regtest limit estimates (as sei->0)",
              "no kernel output corresponds to the limit estimate b (-0.1639 rma / -0.1512 lm)")
    ck.notrun("egger-tau2-se", "SE(tau2)=0.2106",
              "kernel does not expose SE(tau2) (A1 §8 publishes 0.2106)")
    try:
        os.remove(db_path)
    except PermissionError:
        ck.raw["note_db_cleanup"] = "temp db left in results/ (file handle held open on Windows)"
    return ck.write("egger2001")


if __name__ == "__main__":
    run()
