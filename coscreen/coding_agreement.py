"""Field-specific, two-coder agreement summaries for selected independent records."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping
from fractions import Fraction

#: 无 ``field_key`` 键的记录归入同一个"未标注"字段；与带标注记录混比仍算跨字段。
_NO_FIELD_KEY = object()


def _field_key(record: object) -> object:
    """取记录的字段键；非映射记录或不可哈希键一律转 ValueError（库内契约）。"""
    if not isinstance(record, Mapping):
        raise ValueError("records must be mappings with item_key and rater_id")
    if "field_key" not in record:
        return _NO_FIELD_KEY
    key = record["field_key"]
    try:
        hash(key)
    except TypeError:
        raise ValueError("field_key must be hashable") from None
    return key


def _numeric_difference(left: int | float, right: int | float) -> float | int | None:
    """绝对差；不可用 JSON/IEEE-754 double 表示时返回 None（优雅降级）。

    两个有限操作数的差可能超出 double 上限（如 ±1.7e308 对得 3.4e308，整数
    ±10**308 对得 2*10**308）。原式分别得 ``inf``（响应渲染期抛 ValueError，
    发生在端点 except 之外）与 ``OverflowError``（穿透端点的 except ValueError）
    → 两条路径都 500。这里按库内既有 display()/b01·F2-84 的降级口径把不可
    表示量置 None：端点照常 200，输入（合法有限数）不被拒绝（R2）；可表示的
    差值（含整数差原样保留 int）逐位不变。
    """
    difference = abs(left - right)
    if isinstance(difference, int):
        try:
            float(difference)
        except OverflowError:
            return None
        return difference
    return difference if math.isfinite(difference) else None


def _mean_absolute_difference(differences: list) -> float | None:
    """均值；常规输入保持原式逐位不变，仅原式求和溢出时改走精确有理数均值。

    若干大差值相加可能溢出为 inf，而真实均值仍可表示（如两个 1.7e308 差值
    → 1.7e308）；此时用精确有理数算均值。任一逐项差值已不可表示（None）或
    精确均值仍不可表示时返回 None（与逐项同口径降级）。
    """
    if any(value is None for value in differences):
        return None
    count = len(differences)
    try:
        mean = sum(differences) / count
    except OverflowError:
        mean = None
    if mean is not None and math.isfinite(mean):
        return mean
    try:
        mean = float(sum(Fraction(value) for value in differences) / count)
    except OverflowError:
        return None
    return mean if math.isfinite(mean) else None


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
    field_keys = {_field_key(record) for record in records}
    if len(field_keys) > 1:
        raise ValueError("compare one field at a time")
    for record in records:
        item, rater = record.get("item_key"), record.get("rater_id")
        if (not isinstance(item, str) or not item or not isinstance(rater, str)
                or rater not in rater_ids):
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
            difference = _numeric_difference(left, right)
            # 差值超出 double 表示范围（None）时不得视为"在容差内"。
            matched = difference is not None and difference <= absolute_tolerance
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
            _mean_absolute_difference(
                [item["absolute_difference"] for item in result["item_results"]])
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
