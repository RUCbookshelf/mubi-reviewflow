"""Random-effects subgroup analysis with a shared or separate tau-squared.

Each row is one independent selected study effect with its sampling standard
error on one common analysis scale and a non-empty subgroup label. Within
subgroups the module pools by inverse variance with DerSimonian-Laird
tau-squared (self-implemented, same formula as ``review_analysis``), and it
tests subgroup differences with the Q partition and shared-tau-squared z-tests
described in Borenstein et al. (2021), chapter 21, and Cochrane Handbook
section 10.11. The caller must choose ``tau2_policy`` explicitly; there is no
default because pooling tau-squared is a methodological decision.
"""

from __future__ import annotations

import copy
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence

from scipy.stats import chi2, norm

_MODELS = {"fixed", "random"}
_POLICIES = {"pooled", "separate"}
_ROW_KEYS = ("effect", "se", "subgroup")
_ID_KEYS = ("study_id", "study")


def _number(value: object, name: str, index: int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"row {index} {name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"row {index} {name} must be a finite number")
    return number


def _validated_rows(rows: Sequence[Mapping[str, object]]) -> list[dict]:
    if isinstance(rows, (str, bytes, Mapping)) or not isinstance(rows, Sequence):
        raise ValueError("rows must be a sequence of per-study effect records")
    if not rows:
        raise ValueError("at least one study effect record is required")
    seen_ids: set[tuple[str, str]] = set()
    seen_records: set[tuple] = set()
    cleaned: list[dict] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"row {index} must be a mapping with effect, se and subgroup")
        missing = [key for key in _ROW_KEYS if key not in row]
        if missing:
            raise ValueError(f"row {index} is missing required key(s): {', '.join(missing)}")
        effect = _number(row["effect"], "effect", index)
        se = _number(row["se"], "se", index)
        if se <= 0:
            raise ValueError(f"row {index} se must be positive")
        label = row["subgroup"]
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"row {index} subgroup must be a non-empty string")
        identifiers = tuple(sorted((str(key), str(row[key])) for key in _ID_KEYS
                                   if row.get(key) is not None))
        if identifiers and (duplicate := seen_ids & set(identifiers)):
            raise ValueError(f"duplicate study identifier {sorted(duplicate)[0][1]!r}: "
                             "each study must contribute exactly one effect")
        record = tuple(sorted((str(key), repr(value)) for key, value in row.items()))
        if record in seen_records:
            raise ValueError(f"row {index} is an exact duplicate of an earlier row")
        seen_ids.update(identifiers)
        seen_records.add(record)
        cleaned.append({"effect": effect, "se": se, "subgroup": label.strip()})
    return cleaned


def _weighted_sums(effects: list[float], variances: list[float], tau2: float = 0.0):
    weights = [1 / (v + tau2) for v in variances]
    total_w = sum(weights)
    total_wy = sum(a * b for a, b in zip(weights, effects))
    total_wy2 = sum(a * b * b for a, b in zip(weights, effects))
    return total_w, total_wy, total_wy2


def _dl_components(effects: list[float], variances: list[float]) -> tuple[float, float, float, float]:
    """Fixed-effect Q, df, C and the DerSimonian-Laird tau-squared for one set."""
    weights = [1 / v for v in variances]
    total_w = sum(weights)
    total_wy = sum(a * b for a, b in zip(weights, effects))
    total_wy2 = sum(a * b * b for a, b in zip(weights, effects))
    q = total_wy2 - total_wy * total_wy / total_w
    df = len(effects) - 1
    c = total_w - sum(w * w for w in weights) / total_w
    tau2 = max(0.0, (q - df) / c) if c > 0 else 0.0
    return q, df, c, tau2


def subgroup_random_analysis(rows: Sequence[Mapping[str, object]], *, tau2_policy: str,
                             model: str = "random", level: float = 0.95) -> dict:
    """Subgroup synthesis with a random-effects (or fixed-effect) within-group model.

    Each element of ``rows`` is one independent selected study effect: a mapping
    with ``effect`` (finite estimate on the analysis scale), ``se`` (finite and
    positive) and ``subgroup`` (non-empty label). Rows carrying an identifier
    key (``study_id`` or ``study``) must have unique identifiers, and exact
    duplicate rows are rejected; a study contributes one effect to one subgroup.

    ``tau2_policy`` has no default and must be passed explicitly: ``"pooled"``
    estimates one DerSimonian-Laird tau-squared shared by all subgroups
    (recommended for the between-subgroups test; Borenstein et al. 2021,
    chapter 21, eq. 21.38) while ``"separate"`` estimates tau-squared inside
    each subgroup. ``model`` selects the within-subgroup weights: ``"random"``
    (default; DL tau-squared by the chosen policy) or ``"fixed"`` (inverse
    variance only; its between-subgroups test then also uses fixed-effect
    weights and is flagged as such in ``warnings``). ``level`` is the
    confidence level for every interval.

    Rejected with ``ValueError``: fewer than four studies in total, fewer than
    two distinct subgroups, any subgroup with fewer than two studies, and
    missing, duplicate or illegal row values.

    Returns per-subgroup estimates (``k``, ``pooled``, ``se``, ``ci``, ``q``,
    ``i2_percent``, ``tau2`` plus diagnostics), the overall Q decomposition
    (``q_total``, ``q_within``, ``q_between``, ``q_between_p``), pairwise
    shared-tau-squared z-tests with Bonferroni-adjusted p-values, the
    proportion of tau-squared explained by subgroup membership
    (``r2_variance_explained``, pooled policy only), a deep copy of
    ``input_data`` and ``warnings``.
    """
    if model not in _MODELS:
        raise ValueError("model must be 'random' or 'fixed'")
    if tau2_policy not in _POLICIES:
        raise ValueError("tau2_policy must be 'pooled' or 'separate'; pass it explicitly")
    if isinstance(level, bool) or not isinstance(level, (int, float)) or not 0.0 < float(level) < 1.0:
        raise ValueError("level must be a number strictly between 0 and 1")
    level = float(level)

    cleaned = _validated_rows(rows)
    if len(cleaned) < 4:
        raise ValueError("subgroup analysis requires at least four independent studies")
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in cleaned:
        grouped[row["subgroup"]].append(row)
    if len(grouped) < 2:
        raise ValueError("subgroup analysis needs at least two distinct subgroups")
    if any(len(group) < 2 for group in grouped.values()):
        raise ValueError("each subgroup needs at least two independent studies")
    labels = sorted(grouped)

    random_model = model == "random"
    own_tau2: dict[str, float] = {}
    for label in labels:
        group = grouped[label]
        _, _, _, own_tau2[label] = _dl_components(
            [row["effect"] for row in group], [row["se"] ** 2 for row in group])

    if random_model:
        if tau2_policy == "pooled":
            sum_q = sum_d = sum_c = 0.0
            for label in labels:
                group = grouped[label]
                q, df, c, _ = _dl_components(
                    [row["effect"] for row in group], [row["se"] ** 2 for row in group])
                sum_q, sum_d, sum_c = sum_q + q, sum_d + df, sum_c + c
            shared_tau2 = max(0.0, (sum_q - sum_d) / sum_c) if sum_c > 0 else 0.0
            applied = {label: shared_tau2 for label in labels}
        else:
            applied = dict(own_tau2)
    else:
        applied = {label: 0.0 for label in labels}

    critical = float(norm.ppf(1 - (1 - level) / 2))

    subgroups: dict[str, dict] = {}
    q_within = 0.0
    all_effects = [row["effect"] for row in cleaned]
    all_variances = [row["se"] ** 2 for row in cleaned]
    for label in labels:
        group = grouped[label]
        effects = [row["effect"] for row in group]
        variances = [row["se"] ** 2 for row in group]
        tau2 = applied[label]
        q, q_df, _, _ = _dl_components(effects, variances)
        total_w, total_wy, total_wy2 = _weighted_sums(effects, variances, tau2)
        q_weights = total_wy2 - total_wy * total_wy / total_w
        q_within += q_weights
        pooled = total_wy / total_w
        pooled_se = math.sqrt(1 / total_w)
        i2 = max(0.0, (q - q_df) / q) * 100 if q > 0 else 0.0
        subgroups[label] = {
            "k": len(group),
            "pooled": pooled,
            "se": pooled_se,
            "ci": [pooled - critical * pooled_se, pooled + critical * pooled_se],
            "q": q,
            "q_df": q_df,
            "q_p": float(chi2.sf(q, q_df)),
            "i2_percent": i2,
            "tau2": tau2,
            "tau2_own_dl": own_tau2[label],
            "q_weights": q_weights,
        }

    if random_model:
        row_tau2 = [applied[row["subgroup"]] for row in cleaned]
        total_w, total_wy, total_wy2 = _weighted_sums(
            all_effects, [v + t for v, t in zip(all_variances, row_tau2)])
    else:
        total_w, total_wy, total_wy2 = _weighted_sums(all_effects, all_variances)
    overall_pooled = total_wy / total_w
    overall_se = math.sqrt(1 / total_w)
    q_total = total_wy2 - total_wy * total_wy / total_w
    df_between = len(labels) - 1
    q_between = max(0.0, q_total - q_within)
    overall = {
        "pooled": overall_pooled,
        "se": overall_se,
        "ci": [overall_pooled - critical * overall_se, overall_pooled + critical * overall_se],
        "q_total": q_total,
        "q_within": q_within,
        "q_between": q_between,
        "q_between_df": df_between,
        "q_between_p": float(chi2.sf(q_between, df_between)),
        "i2_between_percent": max(0.0, (q_between - df_between) / q_between) * 100
        if q_between > 0 else 0.0,
    }
    if random_model:
        q_total_fixed, _, _, _ = _dl_components(all_effects, all_variances)
        q_within_fixed = sum(subgroups[label]["q"] for label in labels)
        q_between_fixed = max(0.0, q_total_fixed - q_within_fixed)
        overall.update(q_total_fixed=q_total_fixed, q_within_fixed=q_within_fixed,
                       q_between_fixed=q_between_fixed,
                       q_between_fixed_p=float(chi2.sf(q_between_fixed, df_between)))

    pairwise = []
    n_pairs = len(labels) * (len(labels) - 1) // 2
    for i, label_a in enumerate(labels):
        for label_b in labels[i + 1:]:
            diff = subgroups[label_b]["pooled"] - subgroups[label_a]["pooled"]
            se_diff = math.sqrt(subgroups[label_a]["se"] ** 2 + subgroups[label_b]["se"] ** 2)
            z_stat = diff / se_diff
            p_value = float(2 * norm.sf(abs(z_stat)))
            pairwise.append({
                "subgroup_a": label_a,
                "subgroup_b": label_b,
                "diff": diff,
                "se_diff": se_diff,
                "z": z_stat,
                "p": p_value,
                "p_bonferroni": min(1.0, p_value * n_pairs),
                "ci": [diff - critical * se_diff, diff + critical * se_diff],
            })

    r2_variance_explained = None
    if random_model and tau2_policy == "pooled":
        _, _, _, tau2_total = _dl_components(all_effects, all_variances)
        if tau2_total > 0:
            r2_variance_explained = min(1.0, max(0.0, 1 - applied[labels[0]] / tau2_total))

    warnings: list[str] = []
    if random_model:
        if tau2_policy == "pooled":
            warnings.append(
                "The between-subgroups test uses one DerSimonian-Laird tau-squared shared "
                "by all subgroups; subgroups should be prespecified in the review protocol "
                "(Cochrane Handbook section 10.11) and each study must contribute to one "
                "subgroup only.")
        else:
            warnings.append(
                "Each subgroup uses its own DerSimonian-Laird tau-squared; with few studies "
                "per subgroup these estimates are imprecise and a pooled tau-squared is "
                "usually preferable below roughly 10-20 studies per subgroup "
                "(Borenstein 2021, chapter 21).")
        if tau2_policy == "pooled" and applied[labels[0]] <= 0.0:
            warnings.append(
                "Pooled tau-squared was truncated at zero; the shared-tau-squared "
                "random-effects analysis coincides with the fixed-effect analysis.")
        zero_tau2 = [label for label in labels if own_tau2[label] <= 0.0]
        if zero_tau2:
            warnings.append(
                "tau-squared was truncated at zero for subgroup(s) "
                f"{', '.join(zero_tau2)}: their within-subgroup dispersion does not "
                "exceed sampling error.")
    else:
        warnings.append(
            "model='fixed' fits common-effect synthesis within subgroups, so the "
            "between-subgroups test uses fixed-effect weights and can yield false-positive "
            "subgroup differences when true heterogeneity is present (Higgins & Thompson "
            "2004); use model='random' with tau2_policy='pooled' for the shared-tau-squared "
            "random-effects test.")
    for label in labels:
        if subgroups[label]["k"] < 5:
            warnings.append(f"Subgroup '{label}' has only {subgroups[label]['k']} studies; "
                            "within-subgroup heterogeneity estimates are imprecise.")
    if len(pairwise) >= 2:
        warnings.append(
            f"{len(pairwise)} pairwise subgroup comparisons are multiple tests; only the "
            "omnibus test (overall.q_between_p) controls the type-I error across subgroups, "
            "and Bonferroni-adjusted p-values are provided for the pairwise z-tests.")
    if len(cleaned) < 10:
        warnings.append(
            "Fewer than ten studies: subgroup analyses have low power and tau-squared "
            "estimates are imprecise (Cochrane Handbook section 10.11.5.1).")

    method = ("random-effects within subgroups (DerSimonian-Laird tau-squared "
              f"{'pooled across' if tau2_policy == 'pooled' else 'estimated separately in'} "
              "subgroups); Q partition and z-tests on the model weights"
              if random_model else
              "fixed-effect within subgroups; Q partition and z-tests on fixed-effect weights")
    return {
        "model": model,
        "tau2_policy": tau2_policy if random_model else "not_used_by_fixed_model",
        "tau2_estimator": "DerSimonian-Laird" if random_model else None,
        "level": level,
        "method": method,
        "n_studies": len(cleaned),
        "subgroups": subgroups,
        "overall": overall,
        "pairwise": pairwise,
        "r2_variance_explained": r2_variance_explained,
        "input_data": copy.deepcopy(rows),
        "warnings": warnings,
    }
