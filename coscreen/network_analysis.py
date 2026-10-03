"""Fixed-effect contrast-based network meta-analysis for independent two-arm trials."""

from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path

from coscreen.review_analysis import RATIOS, list_effects, selected_effects


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
    for row in rows:
        arms = [part.strip() for part in row["comparison"].split(" vs ")]
        if len(arms) != 2 or not all(arms) or arms[0] == arms[1]:
            raise ValueError("every network comparison must use 'treatment vs comparator'")
        if row["study_id"] in seen_studies:
            raise ValueError("multi-arm or repeated study contrasts need a covariance model")
        seen_studies.add(row["study_id"])
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
    weights = np.asarray([1/row["se"]**2 for _, _, row in contrasts])
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
    return {"method": "fixed-effect contrast-based network weighted least squares",
            "measure": measure, "outcome": outcome, "timepoint": timepoint,
            "reference": reference, "n_studies": len(rows), "treatments": nodes,
            "estimates": estimates, "residual_q": q, "residual_df": df,
            "residual_p": float(chi2.sf(q, df)) if df > 0 else None,
            "warning": "Requires transitivity and comparable effect modifiers. Residual Q is total fixed-effect lack of fit: it combines within-design heterogeneity and possible inconsistency, and is not a consistency test. Multi-arm studies are rejected."}
