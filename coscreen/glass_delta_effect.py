"""Calculate an independent-arm Glass's delta from summary statistics."""

from __future__ import annotations

import math

_FORMAT = "glass_delta_two_arm"
_FIELDS = {"mean_t", "mean_c", "sd_c", "n_t", "n_c", "direction"}
_OPTIONAL_FIELDS = {"source_provenance"}

#: 审计载荷（``input_data``/``source_provenance``）的嵌套深度上限。
#: deepcopy 深度族的族内统一常量（与 ``cluster_trial_effects`` /
#: ``binary_correlation_conversion`` 同值同形，三载体须同步修改）。
_MAX_PROVENANCE_DEPTH = 32
_DEEP_PROVENANCE_PLACEHOLDER = "<nesting too deep>"


def _sanitize_provenance(value, _depth: int = 0):
    """source_provenance 有界拷贝（deepcopy 族 F4-56 + 零校验族 F2-67 的统一入口）。

    客户端可控的 ``source_provenance`` 此前由 ``copy.deepcopy`` 整树复制：
    嵌套约 500 层即抛 ``RecursionError``（非 ValueError），穿出只捕
    ValueError 的端点 → 500（4.5KB 请求即可）。此处以深度上限 + 有限性
    清洗替代递归 deepcopy：
    - 容器递归重建成新的 dict/list（不再走 deepcopy 的递归实现）；
    - 深度达到 ``_MAX_PROVENANCE_DEPTH`` 的子树整体替换为占位字符串——
      不拒绝输入（R2：客户端可能携带任意嵌套的来源信息，拒绝即新增严格性）；
    - 非有限浮点（nan/±inf）替换为占位字符串：裸 NaN 落库后 GET 回读 null
      （同族 F2-84 口径）。
    """
    if isinstance(value, float) and not math.isfinite(value):
        return f"<non-finite {value!r}>"
    if isinstance(value, (dict, list, tuple)):
        if _depth >= _MAX_PROVENANCE_DEPTH:
            return _DEEP_PROVENANCE_PLACEHOLDER
        if isinstance(value, dict):
            return {key: _sanitize_provenance(item, _depth + 1)
                    for key, item in value.items()}
        cleaned = [_sanitize_provenance(item, _depth + 1) for item in value]
        return tuple(cleaned) if isinstance(value, tuple) else cleaned
    return value


def _copy_input(values: dict) -> dict:
    """有界结构化拷贝（替代递归 ``copy.deepcopy``，深/宽结构一律新容器）。

    provenance 走同一清洗器（F4-56 与 F2-66/F2-67 同族同入口）；容器全部
    重建，返回结果不与调用方输入共享可变对象，与旧 deepcopy 的可变隔离
    语义等价。
    """
    return _sanitize_provenance(values)


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _count(value: object, name: str) -> int:
    if type(value) is not int or value < 2:
        raise ValueError(f"{name} must be an integer >= 2")
    try:
        if not math.isfinite(float(value)):
            raise ValueError(f"{name} must be a finite integer")
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite integer") from exc
    return value


def calculate_glass_delta_effect(values: dict) -> dict:
    """Calculate Glass's delta and its large-sample variance approximation.

    Treatment is group t, control is group c, and direction is treatment minus
    control. The control-arm sample SD is the standardizer.
    """
    if (not isinstance(values, dict) or set(values) - (_FIELDS | _OPTIONAL_FIELDS)
            or not _FIELDS <= values.keys()):
        raise ValueError(
            "values must contain mean_t, mean_c, sd_c, n_t, n_c and direction; "
            "source_provenance is optional"
        )

    data = _copy_input(values)
    if data["direction"] != "treatment_minus_control":
        raise ValueError("direction must be 'treatment_minus_control'")
    if "source_provenance" in data and not isinstance(data["source_provenance"], dict):
        raise ValueError("source_provenance must be an object")

    mean_t = _number(data["mean_t"], "mean_t")
    mean_c = _number(data["mean_c"], "mean_c")
    sd_c = _number(data["sd_c"], "sd_c")
    n_t = _count(data["n_t"], "n_t")
    n_c = _count(data["n_c"], "n_c")
    if sd_c <= 0:
        raise ValueError("sd_c must be > 0")

    try:
        difference = mean_t - mean_c
        estimate = difference / sd_c
        mean_se = math.sqrt(math.fsum((1 / n_t, 1 / n_c)))
        denominator_se = estimate / math.sqrt(n_c) / math.sqrt(2)
        se = math.hypot(mean_se, denominator_se)
        variance = se * se
    except (OverflowError, ValueError) as exc:
        raise ValueError("the resulting effect and variance must be finite") from exc
    if not all(math.isfinite(value) for value in (difference, estimate, variance, se)):
        raise ValueError("the resulting effect and variance must be finite")
    if variance <= 0 or se <= 0:
        raise ValueError("the resulting variance and standard error must be positive")

    return {
        "measure": "SMD",
        "estimate": estimate,
        "variance": variance,
        "se": se,
        "se_scale": "natural",
        "standardizer": "control_arm_sd",
        "direction": "treatment_minus_control",
        "input_data": data,
        "entry_method": _FORMAT,
    }
