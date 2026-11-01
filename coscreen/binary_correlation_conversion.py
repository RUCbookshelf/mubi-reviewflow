"""Convert a 2x2 binary table to correlation-scale association coefficients.

Two published transforms are implemented, both consuming the same hard-coded
input form (group t events/total and group c events/total, rows of the 2x2
table) and both returning natural-scale correlation estimates:

- ``transform="phi"``: the phi coefficient, the Pearson correlation computed
  from the 0/1 coded cells (Bonett 2021, Volume 3, equation 3.11).
- ``transform="r_equiv_approx"``: the Bonett and Price (2005) odds-ratio
  approximation to the tetrachoric (underlying Pearson) correlation,
  rho_star = cos(pi / (1 + w^c)) with the marginal-adaptive exponent c.

Both measures depend on the marginal split of each binary variable, so the
result always carries prominent warnings. The returned measure codes
``PHI`` and ``R_EQUIV_APPROX`` are deliberately distinct from ``FISHER_Z``:
these estimates must not be pooled with Fisher-z correlations derived from
continuous outcomes.
"""

from __future__ import annotations

import math
from fractions import Fraction

_FORMAT = "binary_correlation_conversion"
_TRANSFORMS = ("phi", "r_equiv_approx")
_FIELDS = {"events_t", "total_t", "events_c", "total_c", "transform"}
_OPTIONAL_FIELDS = {"source_provenance"}

#: 审计载荷（``input_data``/``source_provenance``）的嵌套深度上限。
#: deepcopy 深度族的族内统一常量（与 ``cluster_trial_effects`` /
#: ``glass_delta_effect`` 同值同形，三载体须同步修改）。
_MAX_PROVENANCE_DEPTH = 32
_DEEP_PROVENANCE_PLACEHOLDER = "<nesting too deep>"


def _sanitize_provenance(value, _depth: int = 0):
    """source_provenance 有界拷贝（deepcopy 族 F2-66 + 零校验族 F2-67 的统一入口）。

    客户端可控的 ``source_provenance`` 此前由 ``copy.deepcopy`` 整树复制：
    嵌套约 500 层即抛 ``RecursionError``（非 ValueError），穿出只捕
    ValueError 的端点 → 500。此处以深度上限 + 有限性清洗替代递归 deepcopy：
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

    provenance 走同一清洗器，故 ``source_provenance`` 的深度封顶与非有限
    清洗与其余字段口径一致（F2-66 / F2-67 同族同入口）；容器全部重建，
    返回结果不与调用方输入共享可变对象，与旧 deepcopy 的可变隔离语义等价。
    """
    return _sanitize_provenance(values)


def _exact_parts(a: int, b: int, c: int, d: int) -> tuple[int, int]:
    """返回 (phi 分子, 分母平方) 的精确整数形式。

    F2-68：|phi| 的判定必须在整数域做。``(a*d-b*c)**2 < (a+b)*(c+d)*(a+c)*(b+d)``
    等价于 |phi|<1 且无舍入——直接比较浮点商时，合法表（如 a=d≈4.5e16、
    b=c=1，数学上 phi = 1−4.4e-17）会因 int→float 舍入把商算成 1.0，被
    「完全分离」守卫误拒。计数上限 2**63，四项乘积 < 2**256，Python 整数
    无溢出。
    """
    numerator = a * d - b * c
    denominator_squared = (a + b) * (c + d) * (a + c) * (b + d)
    return numerator, denominator_squared


def _exact_one_minus_phi_sq(a: int, b: int, c: int, d: int) -> float:
    """精确的 1−phi²（浮点可表示的极小量，用于方差近似）。

    ``(D − N²)/D`` 用 Fraction 精确计算后再转 float：对接近 ±1 的表，
    直接浮点算 ``1 - phi*phi`` 只有一个 ulp 的精度（phi 舍入到 1.0 时为 0）。
    """
    numerator, denominator_squared = _exact_parts(a, b, c, d)
    return float(Fraction(denominator_squared - numerator * numerator,
                          denominator_squared))


def _count(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    try:
        if not math.isfinite(float(value)):
            raise ValueError(f"{name} must be a finite integer")
    except OverflowError as exc:
        raise ValueError(f"{name} must be a finite integer") from exc
    return value


def _marginals(values: dict) -> tuple[int, int, int, int]:
    """Validate counts and return the (a, b, c, d) cells of the 2x2 table.

    Rows are the two groups (t, c); columns are event / no-event.
    """
    total_t = _count(values["total_t"], "total_t")
    total_c = _count(values["total_c"], "total_c")
    events_t = _count(values["events_t"], "events_t")
    events_c = _count(values["events_c"], "events_c")
    if total_t == 0 or total_c == 0:
        raise ValueError("total_t and total_c must each be at least 1")
    if events_t > total_t or events_c > total_c:
        raise ValueError("events cannot exceed the matching group total")
    a, b = events_t, total_t - events_t
    c, d = events_c, total_c - events_c
    if a + c == 0 or b + d == 0:
        raise ValueError(
            "both the event and the no-event marginal totals must be positive; "
            "a constant outcome carries no association information"
        )
    if a + b == 0 or c + d == 0:
        raise ValueError(
            "both group totals must be positive for a 2x2 association table"
        )
    return a, b, c, d


def _warnings(transform: str, a: int, b: int, c: int, d: int,
              p_t: float, p_c: float, p_event: float, p_nonevent: float,
              data: dict) -> list[str]:
    warnings = [
        "The coefficient depends on the marginal split of each binary "
        f"variable, not only on the strength of association: this table has "
        f"row-marginal proportions {p_t:.3f}/{p_c:.3f} (t/c) and column-"
        f"marginal proportions {p_event:.3f}/{p_nonevent:.3f} "
        "(event/no-event). The same odds ratio produces a smaller absolute "
        "coefficient when either split is unbalanced; a 50/50 split "
        "maximizes the absolute value.",
        "Do not pool or directly compare this estimate with Fisher-z "
        "correlations from continuous outcomes. The measure codes PHI and "
        "R_EQUIV_APPROX are deliberately distinct from FISHER_Z, and the "
        "sampling variances are not interchangeable (Bonett 2021, Volume 3, "
        "section 3.4).",
    ]
    if min(a, b, c, d) == 0:
        if transform == "phi":
            warnings.append(
                "At least one cell of the 2x2 table is zero; the phi "
                "estimate and its large-sample standard error are unstable "
                "at this boundary."
            )
        else:
            warnings.append(
                "At least one cell of the 2x2 table is zero; a 0.5 "
                "continuity correction was applied to all four cells, as "
                "prescribed by Bonett and Price (2005)."
            )
    smallest = min(p_t, p_c, p_event, p_nonevent)
    largest = max(p_t, p_c, p_event, p_nonevent)
    if smallest < 0.1 or largest > 0.9:
        warnings.append(
            "At least one marginal split is extreme (a marginal proportion "
            "is below 0.1 or above 0.9). Both coefficients are strongly "
            "compressed toward zero under unbalanced splits and the "
            "tetrachoric approximation is least accurate in this region; "
            "report the raw 2x2 table alongside the conversion."
        )
    return warnings


def _phi(a: int, b: int, c: int, d: int, data: dict) -> dict:
    """phi coefficient and its approximate SE (Bonett 2021, eqs 3.11-3.12)."""
    n = a + b + c + d
    numerator, denominator_squared = _exact_parts(a, b, c, d)
    # F2-68：|phi|=1 ⇔ (ad−bc)² = (a+b)(c+d)(a+c)(b+d)，在整数域精确成立。
    # 该判定只用于复核浮点守卫的拒绝：守卫触发时才看精确值，据此区分
    # 「真分离」与「合法大表被舍入成 1」。守卫未触发时行为与基线与逐位一致
    # （只放宽、不收紧——不新增对合法输入的拒绝，R2）。
    exact_degenerate = numerator * numerator >= denominator_squared
    try:
        phi = numerator / math.sqrt(denominator_squared)
    except (OverflowError, ValueError) as exc:
        raise ValueError("the 2x2 counts must imply a finite phi") from exc
    if not math.isfinite(phi):
        raise ValueError("the 2x2 counts must imply a finite phi")
    saturated = False
    if phi <= -1 or phi >= 1:
        if exact_degenerate:
            raise ValueError(
                "the table implies complete separation (phi = +/-1); no "
                "correlation-scale conversion is reported for degenerate tables"
            )
        # 数学上 |phi|<1，但需计数 ≳2e16 才能与 1 区分，float64 商舍入到 1。
        # 取最接近的内点而不拒绝：合法表不得被误报为完全分离（R2）。
        saturated = True
        phi = math.nextafter(1.0, 0.0) if phi > 0 else math.nextafter(-1.0, 0.0)
    p_t, p_c = (a + b) / n, (c + d) / n
    p_event, p_nonevent = (a + c) / n, (b + d) / n
    row_diff = p_t - p_c
    col_diff = p_event - p_nonevent
    denominator = math.sqrt(p_t * p_c * p_event * p_nonevent)
    # 1−phi² 在饱和分支用精确有理数：phi 已舍入到 1 的邻域时浮点 1−phi²
    # 只剩 0 或 1 ulp 的意义。常规输入保持原浮点表达式，结果逐位不变。
    v1 = _exact_one_minus_phi_sq(a, b, c, d) if saturated else 1 - phi * phi
    v2 = phi + 0.5 * phi ** 3
    v3 = row_diff * col_diff / denominator
    v4 = 0.75 * phi * phi * (row_diff * row_diff / (p_t * p_c)
                             + col_diff * col_diff / (p_event * p_nonevent))
    variance = (v1 + v2 * v3 - v4) / n
    if not math.isfinite(variance) or variance <= 0:
        raise ValueError(
            "the counts must imply a finite positive variance approximation"
        )
    se = math.sqrt(variance)
    return {
        "measure": "PHI",
        "estimate": phi,
        "variance": variance,
        "se": se,
        "se_scale": "natural",
        "target_measure": "phi",
        "positive_estimate_means": "higher event proportion in group t",
        "marginal_proportions": {"t": p_t, "c": p_c,
                                 "event": p_event, "nonevent": p_nonevent},
        "input_data": data,
        "entry_method": _FORMAT,
        "warnings": _warnings("phi", a, b, c, d,
                              p_t, p_c, p_event, p_nonevent, data),
    }


def _r_equiv_approx(a: int, b: int, c: int, d: int, data: dict) -> dict:
    """Bonett and Price (2005) odds-ratio approximation to the tetrachoric r.

    rho_star = cos(pi / (1 + w^c)) with w the continuity-corrected odds
    ratio and c the marginal-adaptive exponent; the SE is the delta-method
    approximation on ln(w) used by the statpsych ci.tetra construction.
    """
    n = a + b + c + d
    log_w = (math.log(a + 0.5) + math.log(d + 0.5)
             - math.log(b + 0.5) - math.log(c + 0.5))
    s_t, s_c = (a + b + 1) / (n + 2), (c + d + 1) / (n + 2)
    s_event, s_nonevent = (a + c + 1) / (n + 2), (b + d + 1) / (n + 2)
    smallest = min(s_t, s_c, s_event, s_nonevent)
    exponent = (1 - abs(s_t - s_event) / 5 - (0.5 - smallest) ** 2) / 2
    try:
        t = math.exp(exponent * log_w)
        rho = math.cos(math.pi / (1 + t))
        derivative = (abs(math.sin(math.pi / (1 + t))) * math.pi * exponent * t
                      / (1 + t) ** 2)
        variance = derivative ** 2 * (1 / (a + 0.5) + 1 / (b + 0.5)
                                      + 1 / (c + 0.5) + 1 / (d + 0.5))
    except (OverflowError, ValueError) as exc:
        raise ValueError(
            "the 2x2 counts must imply a finite equivalent correlation"
        ) from exc
    if not math.isfinite(rho) or not math.isfinite(variance) or variance <= 0:
        raise ValueError(
            "the counts must imply a finite equivalent correlation with a "
            "positive variance approximation"
        )
    if rho <= -1 or rho >= 1:
        # F2-68：rho = cos(π/(1+t)) 对任意有限 t>0 都严格落在 (−1,1)，本分支
        # 只由 cos 在 0 附近舍入到 ±1 触发（合法大表被误报为退化）。真退化表
        # （如 20/20 vs 0/20）算出的是 0.997，不经此处。取最接近的内点而非
        # 拒绝：不新增对合法输入的拒绝（R2）。
        rho = math.nextafter(1.0, 0.0) if rho > 0 else math.nextafter(-1.0, 0.0)
    se = math.sqrt(variance)
    p_t, p_c = (a + b) / n, (c + d) / n
    p_event, p_nonevent = (a + c) / n, (b + d) / n
    return {
        "measure": "R_EQUIV_APPROX",
        "estimate": rho,
        "variance": variance,
        "se": se,
        "se_scale": "natural",
        "target_measure": "tetrachoric_approx",
        "positive_estimate_means": "higher event proportion in group t",
        "marginal_proportions": {"t": p_t, "c": p_c,
                                 "event": p_event, "nonevent": p_nonevent},
        "input_data": data,
        "entry_method": _FORMAT,
        "warnings": _warnings("r_equiv_approx", a, b, c, d,
                              p_t, p_c, p_event, p_nonevent, data),
    }


def calculate_binary_correlation_conversion(values: dict) -> dict:
    """Convert one 2x2 binary table to a correlation-scale coefficient.

    ``values`` must contain exactly ``events_t``, ``total_t``, ``events_c``,
    ``total_c`` and ``transform``; ``source_provenance`` is optional. The
    two groups are the rows of the table (t, c) and the outcome columns are
    event / no-event, so ``events_t`` is the number of events in group t.

    ``transform="phi"`` returns the phi coefficient (the Pearson correlation
    of the 0/1 coded table) with the approximate standard error of Bonett
    (2021, Volume 3, equations 3.11-3.12). ``transform="r_equiv_approx"``
    returns the Bonett and Price (2005) odds-ratio approximation to the
    tetrachoric correlation with its delta-method standard error. Both
    estimates are on the natural correlation scale; both are
    marginal-split-dependent and must never be pooled with FISHER_Z
    correlations from continuous outcomes.
    """
    if (not isinstance(values, dict) or set(values) - (_FIELDS | _OPTIONAL_FIELDS)
            or not _FIELDS <= values.keys()):
        raise ValueError(
            "values must contain events_t, total_t, events_c, total_c and "
            "transform; source_provenance is optional"
        )

    data = _copy_input(values)
    if not isinstance(data["transform"], str) or data["transform"] not in _TRANSFORMS:
        raise ValueError(f"transform must be one of {', '.join(_TRANSFORMS)}")
    if "source_provenance" in data and not isinstance(data["source_provenance"], dict):
        raise ValueError("source_provenance must be an object")

    a, b, c, d = _marginals(data)
    if data["transform"] == "phi":
        return _phi(a, b, c, d, data)
    return _r_equiv_approx(a, b, c, d, data)
