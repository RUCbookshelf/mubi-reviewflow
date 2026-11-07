"""Fixture I: Cohen kappa — 2 psychiatrists x 30 patients x 5 diagnostic categories.

Data + published values: A1 §6.1 (Datanovia/vcd::Kappa worked example).
A1 ⚠ resolution (verified during fixture bring-up): A1's hand recomputation used pe=208/900,
but the printed table's marginals are rows (13,10,2,1,4) and cols (7,9,5,5,4), so
pe = (13*7+10*9+2*5+1*5+4*4)/900 = 212/900 = 0.235556 — the 208 in A1 is an arithmetic slip
(sum of products is 212, not 208). With pe=212/900: kappa = (22/30 - 212/900)/(1 - 212/900)
= 0.651163, matching the page-printed 0.6512. So the page value is used as the reference and
the A1 ⚠ recomputed value 0.65318 is recorded in raw as superseded.
ASE/z/p/CI are not KappaResult fields -> NOT-RUN. Weighted kappa not implemented -> NOT-RUN.
The kernel cohen_kappa takes label sequences; the 5x5 table is expanded fixture-side.
"""

from __future__ import annotations

from common import Checker

LABELS = ["Depression", "PersDisorder", "Schizophrenia", "Neurosis", "Other"]

# rows = doctor1 diagnosis, cols = doctor2 (A1 §6.1 verbatim)
TABLE = [
    [7, 1, 2, 3, 0],
    [0, 8, 1, 1, 0],
    [0, 0, 2, 0, 0],
    [0, 0, 0, 1, 0],
    [0, 0, 0, 0, 4],
]

SOURCE = "A1 §6.1 Datanovia cohens-kappa-in-r worked example (vcd::Kappa output)"


def expand() -> tuple[list[str], list[str]]:
    a, b = [], []
    for i, row in enumerate(TABLE):
        for j, count in enumerate(row):
            a.extend([LABELS[i]] * count)
            b.extend([LABELS[j]] * count)
    return a, b


def run() -> dict:
    from coscreen.stats.kappa import cohen_kappa

    ck = Checker("CohenKappa", SOURCE)
    labels_a, labels_b = expand()
    assert len(labels_a) == 30 and len(labels_b) == 30

    res = cohen_kappa(labels_a, labels_b, labels=LABELS)
    ck.raw["cohen_kappa"] = {"kappa": res.kappa, "po": res.po, "pe": res.pe, "n": res.n,
                             "confusion": {f"{k[0]}|{k[1]}": v for k, v in res.confusion.items()}}

    ck.integer("kappa-n", "Cohen kappa", "n paired", res.n, 30)
    ck.num("kappa-po", "Cohen kappa", "po", res.po, 22.0 / 30.0, 17, 1e-3, derived=True,
           note="exact 22/30 from the printed table")
    ck.num("kappa-pe", "Cohen kappa", "pe", res.pe, 212.0 / 900.0, 19, 1e-3, derived=True,
           note="exact 212/900 = (13*7+10*9+2*5+1*5+4*4)/900; A1 §6.1's 208/900 was an "
                "arithmetic slip (see module header)")
    ck.num("kappa-value", "Cohen kappa", "kappa", res.kappa, 0.6512, 4, 1e-3,
           note="page-printed value; A1 ⚠ recomputation 0.65318 used the erroneous pe and is "
                "superseded (kept in raw)")
    ck.raw["kappa_page_printed"] = 0.6512
    ck.raw["a1_warn_recomputed_superseded"] = 0.65318

    ck.notrun("kappa-ase-z-p-ci", "Cohen kappa ASE/z/p/CI",
              "KappaResult carries kappa/po/pe/n/confusion only — no ASE, z, p or CI field "
              "(published: ASE=0.09968, z=6.532, p=6.474e-11, CI [0.4558, 0.8465])")
    ck.notrun("kappa-weighted", "Weighted kappa (equal-spacing weights)",
              "no weighted-kappa implementation in E:/rf214-ai (published: 0.6331, ASE 0.11939)")
    ck.notrun("kappa-mchugh", "McHugh 2012 formula example (kappa=0.85 from Pr(a)/Pr(e), n=222)",
              "no raw contingency data (only Pr(a)=0.94, Pr(e)=0.57 published); not a kernel input form")
    return ck.write("kappa_cohen")


if __name__ == "__main__":
    run()
