"""Fixed-effect contrast-based network meta-analysis for independent two-arm trials."""

from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path

from coscreen.review_analysis import (
    RATIOS,
    _screened_inverse_variance_weights,
    list_effects,
    selected_effects,
)


def synthesize(db: str | Path, outcome: str, timepoint: str, measure: str,
               reference: str) -> dict:
    import numpy as np
    from scipy.stats import chi2

    if measure not in {"RR", "OR", "RD", "MD", "SMD"}:
        raise ValueError("network synthesis supports RR, OR, RD, MD or SMD")

    comparisons = {r["comparison"] for r in list_effects(db)
                   if (r["outcome"], r["timepoint"]) == (outcome, timepoint)}
    rows = [row for comparison in sorted(comparisons)
            for row in selected_effects(db, comparison, outcome, timepoint, measure)]
    contrasts = []
    seen_studies = set()
    # MM10-12: "A vs B" from one study and "B vs A" from another encode opposite
    # design rows; with same-signed estimates GLS silently averages them toward
    # a meaningless midpoint (~0) while residual Q alone hints at the problem.
    # A reversed string with a negated estimate is legitimate input (it is the
    # same contrast), so this is flagged in warnings instead of rejected.
    seen_pairs: dict[frozenset, tuple[str, str]] = {}
    reversed_pairs: list[tuple[str, str]] = []
    for row in rows:
        arms = [part.strip() for part in row["comparison"].split(" vs ")]
        if len(arms) != 2 or not all(arms) or arms[0] == arms[1]:
            raise ValueError("every network comparison must use 'treatment vs comparator'")
        if row["study_id"] in seen_studies:
            raise ValueError("multi-arm or repeated study contrasts need a covariance model")
        seen_studies.add(row["study_id"])
        pair = frozenset(arms)
        if pair in seen_pairs and seen_pairs[pair] != (arms[0], arms[1]):
            # one entry per unordered pair: a pair reversed by two studies
            # would otherwise be named twice in the message
            if (arms[0], arms[1]) not in reversed_pairs:
                reversed_pairs.append((arms[0], arms[1]))
        seen_pairs.setdefault(pair, (arms[0], arms[1]))
        contrasts.append((arms[0], arms[1], row))
    nodes = sorted({arm for a, b, _ in contrasts for arm in (a, b)})
    if len(nodes) < 3 or reference not in nodes:
        raise ValueError("network needs at least three treatments and a present reference")
    adjacency: dict[str, set[str]] = defaultdict(set)
    for a, b, _ in contrasts:
        adjacency[a].add(b)
        adjacency[b].add(a)
    reached, frontier = set(), [reference]
    while frontier:
        node = frontier.pop()
        if node not in reached:
            reached.add(node)
            frontier.extend(adjacency[node] - reached)
    if reached != set(nodes):
        raise ValueError("treatment network is disconnected")
    others = [node for node in nodes if node != reference]
    design = np.asarray([[int(a == node) - int(b == node) for node in others]
                         for a, b, _ in contrasts], dtype=float)
    values = np.asarray([math.log(row["estimate"]) if measure in RATIOS else row["estimate"]
                         for _, _, row in contrasts])
    # MM10-01: 1/se² on degenerate SEs raises OverflowError (se ≳ 1.34e154) or
    # ZeroDivisionError (se² underflows to 0), which are not ValueError and used
    # to escape the endpoint's 4xx contract as 500s. The shared screen converts
    # them to the library's normal ValueError rejection pattern; the subnormal
    # band (se=1e-160 → inf weight → NaN → null) keeps its known MM5-05 behavior.
    weights = np.asarray(_screened_inverse_variance_weights([row for _, _, row in contrasts]))
    information = design.T @ (weights[:, None] * design)
    covariance = np.linalg.inv(information)
    effects = covariance @ design.T @ (weights * values)
    q = float(np.sum(weights * (values - design @ effects)**2))
    df = len(rows) - len(others)
    estimates = []
    for i, a in enumerate(nodes):
        for b in nodes[i+1:]:
            vector = np.asarray([int(a == node) - int(b == node) for node in others])
            effect, se = float(vector @ effects), math.sqrt(float(vector @ covariance @ vector))
            transform = math.exp if measure in RATIOS else float
            estimates.append({"treatment": a, "comparator": b,
                              "estimate": transform(effect), "se": se,
                              "ci_low": transform(effect - 1.96*se),
                              "ci_high": transform(effect + 1.96*se)})
    warning = ("Requires transitivity and comparable effect modifiers. Residual Q is "
               "total fixed-effect lack of fit: it combines within-design heterogeneity "
               "and possible inconsistency, and is not a consistency test. Multi-arm "
               "studies are rejected.")
    if reversed_pairs:
        named = ", ".join(f"'{a} vs {b}'" for a, b in reversed_pairs)
        warning += (f" Effect direction check needed: the unordered treatment pair(s) "
                    f"{named} appear with both orientations; their same-signed estimates "
                    "are averaged as opposite design rows, which is only meaningful if "
                    "each reversed entry carries the negated estimate.")
    return {"method": "fixed-effect contrast-based network weighted least squares",
            "measure": measure, "outcome": outcome, "timepoint": timepoint,
            "reference": reference, "n_studies": len(rows), "treatments": nodes,
            "estimates": estimates, "residual_q": q, "residual_df": df,
            "residual_p": float(chi2.sf(q, df)) if df > 0 else None,
            "warning": warning}
