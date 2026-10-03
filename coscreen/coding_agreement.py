"""Field-specific, two-coder agreement summaries for selected independent records."""

from __future__ import annotations

import math
from collections import Counter


def summarize_agreement(records: list[dict], rater_ids: tuple[str, str],
                        field_type: str, *, absolute_tolerance: float | None = None) -> dict:
    """Compare one selected revision per item/coder; missing items stay visible."""
    if len(rater_ids) != 2 or rater_ids[0] == rater_ids[1]:
        raise ValueError("two different rater IDs are required")
    if any(not isinstance(rater, str) or not rater for rater in rater_ids):
        raise ValueError("rater IDs must be nonempty strings")
    if field_type not in {"categorical", "multiselect", "numeric", "text"}:
        raise ValueError("unsupported field type")
    if field_type == "numeric":
        try:
            valid_tolerance = (not isinstance(absolute_tolerance, bool)
                               and isinstance(absolute_tolerance, (int, float))
                               and math.isfinite(absolute_tolerance)
                               and absolute_tolerance >= 0)
        except OverflowError:
            valid_tolerance = False
        if not valid_tolerance:
            raise ValueError("numeric agreement needs a finite nonnegative absolute tolerance")
    elif absolute_tolerance is not None:
        raise ValueError("absolute tolerance applies only to numeric fields")
    by_item: dict[str, dict[str, object]] = {}
    field_keys = {record.get("field_key") for record in records if "field_key" in record}
    if len(field_keys) > 1:
        raise ValueError("compare one field at a time")
    for record in records:
        item, rater = record.get("item_key"), str(record.get("rater_id", ""))
        if not isinstance(item, str) or not item or rater not in rater_ids:
            raise ValueError("records need item keys and one of the selected rater IDs")
        if rater in by_item.setdefault(item, {}):
            raise ValueError("select one revision per item and rater")
        by_item[item][rater] = record.get("value")
    paired = [(item, row[rater_ids[0]], row[rater_ids[1]])
              for item, row in sorted(by_item.items()) if all(rater in row for rater in rater_ids)]
    missing = {rater: [item for item, row in sorted(by_item.items()) if rater not in row]
               for rater in rater_ids}
    result = {"field_type": field_type, "rater_ids": list(rater_ids),
              "n_items": len(by_item), "n_paired": len(paired),
              "missing_by_rater": missing, "item_results": []}
    first, second = [], []
    for item, left, right in paired:
        if field_type == "numeric":
            try:
                valid_numbers = all(not isinstance(value, bool) and isinstance(value, (int, float))
                                    and math.isfinite(value) for value in (left, right))
            except OverflowError:
                valid_numbers = False
            if not valid_numbers:
                raise ValueError("numeric fields require finite numbers")
            difference = abs(left - right)
            matched = difference <= absolute_tolerance
            item_result = {"item_key": item, "within_tolerance": matched,
                           "absolute_difference": difference}
        elif field_type == "multiselect":
            if any(not isinstance(value, list) or any(not isinstance(part, str) for part in value)
                   or len(value) != len(set(value))
                   for value in (left, right)):
                raise ValueError("multiselect fields require lists of unique strings")
            left_set, right_set = set(left), set(right)
            matched = left_set == right_set
            union = left_set | right_set
            item_result = {"item_key": item, "exact_match": matched,
                           "jaccard": len(left_set & right_set) / len(union) if union else 1.0}
        else:
            if not isinstance(left, str) or not isinstance(right, str):
                raise ValueError("categorical and text fields require strings")
            matched = left == right
            item_result = {"item_key": item, "exact_match": matched}
        result["item_results"].append(item_result)
        first.append(left); second.append(right)
    result["agreement_rate"] = (sum(item.get("within_tolerance", item.get("exact_match"))
                                    for item in result["item_results"]) / len(paired)) if paired else None
    if field_type == "numeric":
        result["absolute_tolerance"] = absolute_tolerance
        result["mean_absolute_difference"] = (
            sum(item["absolute_difference"] for item in result["item_results"]) / len(paired)
            if paired else None)
    elif field_type == "categorical":
        if len(paired) < 2:
            result["cohen_kappa"] = None
            result["kappa_status"] = "insufficient_pairs"
        else:
            a, b = Counter(first), Counter(second)
            expected = sum(a[key] * b[key] for key in a.keys() | b.keys()) / len(paired) ** 2
            result["cohen_kappa"] = ((result["agreement_rate"] - expected) / (1 - expected)
                                      if expected < 1 else None)
            result["kappa_status"] = "available" if expected < 1 else "degenerate_marginals"
    return result
