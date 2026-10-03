"""多编码员编码合并与一致性统计（SPEC §15.4 引擎化）：纯 pandas + 既有 kappa 引擎。

契约（签名由 SPEC §15.4 / 合并页多编码员区固定，不得偏离）：

- :func:`merge_coding`：N>=2 份编码矩阵（列含 ``zotero_key``，其余列为维度名；
  单选 = 选项串，多选 = ``"a|b"``，文本 = 自由文本）按 ``(zotero_key, dimension)``
  外连接成长表，逐格给出 ``n_coded`` 与 ``status``；
- :func:`summarize_coding`：长表 + ``dtype_map``（维度 -> 类型）-> 逐维度 κ / Light；
- :func:`agreement_payload`：摘要 -> JSON 安全 dict（无 tuple 键；stats 导出用）；
- 导出助手：:func:`merged_coding_csv` / :func:`conflicts_csv` / :func:`stats_csv`。

口径（与 SPEC §15.4 逐条对应）：

- ``status``：``n_coded>=2`` 且全部相同 = ``agree``；``n_coded>=2`` 且不全同 =
  ``conflict``；``n_coded==1`` = ``incomplete``（仅一位编码员填写）；
- 只保留至少一位编码员填写的格（``n_coded>=1``）——无人填写的格不入表；
- 取值按"去首尾空白后的原样字符串"比较（多选不重排选项：编码工作台在保存时已按
  选项顺序规范化，见 coscreen/coding.py:604-626，引擎不二次归一）；
- 单选：逐对（pair）以**两侧选项并集**为 labels 计算多类 Cohen's κ，维度 κ = 各对
  κ 的算术平均；Light = 同一组 κ 的算术平均（Light's kappa 的定义即两两 κ 的平均，
  因此**单选维度 κ 与 light 在数学上恒等**，两列并存是为了与多选维度口径对齐）；
- 多选：逐 option 取 0/1 指示序列（未填格记为"缺失"不参与该 option 的比对，与
  :func:`coscreen.stats.kappa.cohen_kappa` 丢弃空配对的口径一致），逐对计算二分类 κ；
  **无共现**（两位编码员没有任何一篇同时选中该 option）的 (option, 配对) 跳过；
  维度 κ = 有可算值的 option 的 κ 均值（option 等权，全无共现 -> ``None``）；
  Light = 每个配对先对"该对可算的 option"取均值，再对有可算值的配对取均值
  （pair 等权；无任何可算配对 -> ``None``）——与 κ 在 option 网格不完整时不同值；
- 文本：κ / light 恒为 ``None``，``detail`` 保留逐格两侧取值并排供人工比对。

类型口径：``dtype_map`` 由调用方给出（UI 的类型启发式 / 手动下拉覆盖），引擎**不
推断类型**——``merged`` 的 ``dtype`` 列只是 :func:`merge_coding` 按取值形态给出的
**结构性缺省**（含 ``"|"`` -> multi、含空白或超长 -> text、其余 -> choice，与 ui/5
既有 ``classify_dimension`` 同口径，见 ui/5_合并仲裁.py:454-472），仅供导出阅读；
统计一律以 ``dtype_map`` 为准，未覆盖的维度 -> 中文 ``ValueError``。

kappa 引擎复用：逐对计算一律调用 :func:`coscreen.stats.cohen_kappa`（纯 Python，
与 sklearn 数学等价）；``pairwise_kappa`` / ``light_kappa`` 的 labels 固定为
``DEFAULT_LABELS``（include/exclude/maybe），无法承载任意编码选项，故 Light 按
``light_kappa`` 的同一口径（可算配对等权平均、无配对 -> None）在本模块内实现，
两者在 labels 兼容时数值相同（见 tests/test_coding_agreement_engine.py 对照用例）。

导出文本：三个导出助手均返回 **utf-8-sig 文本**（首字符 U+FEFF；``text.encode("utf-8")``
即标准 utf-8-sig 文件字节）。落盘建议::

    Path("merged_coding.csv").write_text(merged_coding_csv(merged), encoding="utf-8")
    # 若坚持用 encoding="utf-8-sig"，请先 text.removeprefix("\\ufeff")，否则写出双 BOM。

所有输出确定性：维度/编码员/键排序固定，同一输入两次调用字节级一致。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import pandas as pd

from coscreen.stats.kappa import KappaResult, cohen_kappa

__all__ = [
    "BINARY_LABELS",
    "CODING_DTYPES",
    "CodingAgreementSummary",
    "CodingCellStatus",
    "DIMENSION_COLUMN",
    "DTYPE_CHOICE",
    "DTYPE_COLUMN",
    "DTYPE_MULTI",
    "DTYPE_TEXT",
    "KEY_COLUMN",
    "MULTI_SEPARATOR",
    "NON_DIMENSION_COLUMNS",
    "N_CODED_COLUMN",
    "SHAPE_TEXT_MAXLEN",
    "STATS_CSV_COLUMNS",
    "STATUS_COLUMN",
    "TITLE_COLUMN",
    "agreement_payload",
    "conflicts_csv",
    "merge_coding",
    "merged_coding_csv",
    "stats_csv",
    "summarize_coding",
]


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 合并长表的单元格一致性状态（SPEC §15.4）
CodingCellStatus = ("agree", "conflict", "incomplete")

#: 编码矩阵的键列 / 标题列（不参与维度比对；与 coscreen/export.py 的矩阵导出口径一致）
KEY_COLUMN = "zotero_key"
TITLE_COLUMN = "title"
NON_DIMENSION_COLUMNS = (KEY_COLUMN, TITLE_COLUMN)

#: 多选取值分隔符（与 coscreen.coding.VALUE_SEPARATOR、ui/5 的 AGR_VALUE_SEPARATOR
#: 同口径；stats 子包不反向依赖数据层与 UI 包，故在此独立定义）
MULTI_SEPARATOR = "|"

#: 维度类型：choice=单选、multi=多选（值以 MULTI_SEPARATOR 连接）、text=自由文本。
#: 统计口径由调用方 dtype_map 给出，引擎不推断（见模块 docstring）。
DTYPE_CHOICE = "choice"
DTYPE_MULTI = "multi"
DTYPE_TEXT = "text"
CODING_DTYPES = (DTYPE_CHOICE, DTYPE_MULTI, DTYPE_TEXT)

#: 结构性类型判定的文本阈值（与 ui/5 的 AGR_TEXT_MAXLEN 同口径）
SHAPE_TEXT_MAXLEN = 24

#: 多选逐 option 的 0/1 指示变量 κ 的 labels
BINARY_LABELS = ("0", "1")

#: 合并长表的固定列名（value_<coder> 列在两者之间，数量随编码员数变化）
DIMENSION_COLUMN = "dimension"
DTYPE_COLUMN = "dtype"
N_CODED_COLUMN = "n_coded"
STATUS_COLUMN = "status"

#: value_<coder> 列的前缀
VALUE_PREFIX = "value_"

#: 一致性统计 CSV 的固定列序（scope=all 一行总计 + 每维度一行）
STATS_CSV_COLUMNS = (
    "scope", "dimension", "dtype", "n_cells", "n_agree", "n_conflict",
    "n_incomplete", "agreement_rate", "kappa", "light", "detail",
)


# ---------------------------------------------------------------------------
# 取值规整与结构性类型
# ---------------------------------------------------------------------------

def _cell(value: object) -> str:
    """单元格 -> 去首尾空白字符串；None / NaN / pd.NA 一律空串（空串 = 未填）。

    与 ui/5 的 ``_agr_cell`` 同口径（见 ui/5_合并仲裁.py:390-394）：避免 "nan"
    混进标签，也让矩阵 CSV 的空单元格（``keep_default_na=False``）与真缺失等价。
    """
    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):  # 极少数标量（如自定义对象）不可判空
        pass
    return str(value).strip()


def _multi_parts(value: str) -> list[str]:
    """多选取值 -> 选项列表（按 MULTI_SEPARATOR 拆分、去空白、丢空段、保持原序）。"""
    return [part.strip() for part in value.split(MULTI_SEPARATOR) if part.strip()]


def _shape_dtype(values: Iterable[str]) -> str:
    """按取值形态给出结构性类型（合并长表的 ``dtype`` 列，非统计口径）：

    1) 任一侧取值含 ``"|"`` -> ``multi``；
    2) 任一侧取值含空白字符或长度超过 :data:`SHAPE_TEXT_MAXLEN` -> ``text``；
    3) 其余 -> ``choice``。
    """
    items = [value for value in values if value]
    if any(MULTI_SEPARATOR in value for value in items):
        return DTYPE_MULTI
    if any(
        len(value) > SHAPE_TEXT_MAXLEN or any(ch.isspace() for ch in value)
        for value in items
    ):
        return DTYPE_TEXT
    return DTYPE_CHOICE


def _mean(values: Sequence[float]) -> float | None:
    """算术平均（空序列 -> None；确定性）。"""
    return sum(values) / len(values) if values else None


# ---------------------------------------------------------------------------
# 合并（N>=2 份编码矩阵 -> 长表）
# ---------------------------------------------------------------------------

def merge_coding(named_matrices: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """N>=2 份编码矩阵长表化：一行 = 一个 (zotero_key, 维度) 格。

    ``named_matrices`` 为有序映射 ``{"<编码员名>": 矩阵}``（编码员名即
    ``value_<编码员名>`` 列的后缀，顺序决定列序与 title 取值优先级），少于 2 份
    或编码员名为空 -> 中文 ValueError。矩阵要求：含 ``KEY_COLUMN`` 列，另有至少
    一个维度列（除 ``zotero_key``/``title`` 外）；**各矩阵的维度列必须完全一致**
    （不做并集也不补空）——不一致 -> ValueError 逐一列出维度及其缺失方。

    输出列固定为 ``zotero_key, title, dimension, dtype, value_<coder>...(N 列),
    n_coded, status``，行按 ``(zotero_key, dimension)`` 升序（确定性）。

    - 行集合 = 至少一位编码员填写了该格的 (键, 维度)（``n_coded>=1``）；键集合为
      各矩阵键的并集（"单边键"因此天然保留，该格因 ``n_coded==1`` 标 incomplete）；
    - 取值按去首尾空白后的原样字符串比较（多选不重排选项，见模块 docstring）；
    - 重复 zotero_key 保留首行（矩阵导出按 import_order 排列，重复一般为手工拼表
      所致；"保留首行"是确定性约定，与 ui/5 的 ``matrix_index`` 一致）；
    - 空键（``zotero_key`` 为空串）的行一律跳过；
    - ``title`` 取按 ``named_matrices`` 顺序首个非空值；
    - ``dtype`` 为结构性缺省（见 :func:`_shape_dtype`）：**仅供阅读/导出**，
      :func:`summarize_coding` 的统计口径一律由调用方 ``dtype_map`` 给出；
    - ``status``：``n_coded>=2`` 且全部相同 -> ``agree``；不全同 -> ``conflict``；
      ``n_coded==1`` -> ``incomplete``。

    全部矩阵都没有键行（真正的空矩阵）-> 中文 ValueError；矩阵有行但没有任何编码
    值时可返回空的规范长表（列齐备、零行），交由调用方决定如何提示。
    """
    names = [str(name).strip() for name in named_matrices]
    if any(not name for name in names):
        raise ValueError("编码员名不能为空（named_matrices 的键即编码员名）")
    duplicated = sorted({name for name in names if names.count(name) > 1})
    if duplicated:  # 去空白后同名会互相覆盖（无声丢数据），一律拒绝
        raise ValueError(f"编码员名重复（去首尾空白后相同）: {duplicated}")
    if len(names) < 2:
        raise ValueError(
            f"merge_coding 至少需要 2 位编码员的编码矩阵，实际收到 {len(names)} 份"
        )

    frames: dict[str, pd.DataFrame] = {}
    dims_of: dict[str, list[str]] = {}
    for name, matrix in zip(names, named_matrices.values()):
        if not isinstance(matrix, pd.DataFrame):
            raise ValueError(
                f"编码员「{name}」的矩阵必须是 pandas.DataFrame，"
                f"实际为 {type(matrix).__name__}"
            )
        columns = [str(column) for column in matrix.columns]
        if len(set(columns)) != len(columns):
            raise ValueError(f"编码员「{name}」的矩阵存在同名列: {columns}")
        if KEY_COLUMN not in columns:
            raise ValueError(
                f"编码员「{name}」的矩阵缺少必需列 {KEY_COLUMN!r}（现有列：{columns}）"
            )
        dims = [column for column in columns if column not in NON_DIMENSION_COLUMNS]
        if not dims:
            raise ValueError(
                f"编码员「{name}」的矩阵没有维度列"
                f"（除 {list(NON_DIMENSION_COLUMNS)} 外，现有列：{columns}）"
            )
        frames[name] = matrix
        dims_of[name] = dims

    all_dims = sorted(set().union(*dims_of.values()))
    mismatched: list[str] = []
    for dim in all_dims:
        lacking = [name for name in names if dim not in dims_of[name]]
        if lacking:
            mismatched.append(f"{dim!r}（缺少：{'、'.join(lacking)}）")
    if mismatched:
        raise ValueError(
            "编码矩阵的维度列不一致，无法按维度对齐：" + "；".join(mismatched)
            + "。请用同一套编码方案导出矩阵后重试"
        )

    index: dict[str, dict[str, dict[str, str]]] = {}
    titles: dict[str, dict[str, str]] = {}
    for name in names:
        frame = frames[name]
        columns_of = {str(column): column for column in frame.columns}
        per_key: dict[str, dict[str, str]] = {}
        per_title: dict[str, str] = {}
        for record in frame.to_dict("records"):
            key = _cell(record[columns_of[KEY_COLUMN]])
            if not key or key in per_key:
                continue
            per_key[key] = {
                dim: _cell(record[columns_of[dim]]) for dim in dims_of[name]
            }
            if TITLE_COLUMN in columns_of:
                per_title[key] = _cell(record[columns_of[TITLE_COLUMN]])
        index[name] = per_key
        titles[name] = per_title

    if not any(index[name] for name in names):
        raise ValueError("全部编码矩阵均为空（没有任何 zotero_key 行），无法合并")

    dim_dtype = {
        dim: _shape_dtype(
            index[name][key][dim] for name in names for key in index[name]
        )
        for dim in all_dims
    }

    keys = sorted(set().union(*(index[name] for name in names)))
    rows: list[dict[str, Any]] = []
    for key in keys:
        title = next(
            (titles[name][key] for name in names if titles[name].get(key)), ""
        )
        for dim in all_dims:  # keys / all_dims 均已排序 -> 行序即 (key, dimension) 升序
            values = {
                name: index[name].get(key, {}).get(dim, "") for name in names
            }
            coded = [value for value in values.values() if value]
            n_coded = len(coded)
            if n_coded == 0:
                continue
            if n_coded == 1:
                status = "incomplete"
            elif len(set(coded)) == 1:
                status = "agree"
            else:
                status = "conflict"
            row: dict[str, Any] = {
                KEY_COLUMN: key,
                TITLE_COLUMN: title,
                DIMENSION_COLUMN: dim,
                DTYPE_COLUMN: dim_dtype[dim],
            }
            row.update({f"{VALUE_PREFIX}{name}": values[name] for name in names})
            row[N_CODED_COLUMN] = n_coded
            row[STATUS_COLUMN] = status
            rows.append(row)

    columns = [
        KEY_COLUMN,
        TITLE_COLUMN,
        DIMENSION_COLUMN,
        DTYPE_COLUMN,
        *[f"{VALUE_PREFIX}{name}" for name in names],
        N_CODED_COLUMN,
        STATUS_COLUMN,
    ]
    frame = pd.DataFrame(rows, columns=columns)
    frame[N_CODED_COLUMN] = pd.Series(
        [int(row[N_CODED_COLUMN]) for row in rows], dtype="int64"
    )
    return frame.reset_index(drop=True)


def _merged_coder_names(merged: pd.DataFrame) -> list[str]:
    """合并长表的编码员名（``value_<coder>`` 列，按列序）。"""
    names = [
        str(column)[len(VALUE_PREFIX):]
        for column in merged.columns
        if str(column).startswith(VALUE_PREFIX)
    ]
    if len(names) < 2:
        raise ValueError(
            "需要 merge_coding 的输出（至少 2 列 value_<coder>，"
            f"实际收到 {len(names)} 列）"
        )
    return names


def _require_merged(merged: pd.DataFrame) -> list[str]:
    """校验长表（:func:`merge_coding` 的输出）并返回编码员名。"""
    if not isinstance(merged, pd.DataFrame):
        raise ValueError(
            f"merged 必须是 pandas.DataFrame，实际为 {type(merged).__name__}"
        )
    missing = [
        column
        for column in (KEY_COLUMN, DIMENSION_COLUMN, STATUS_COLUMN)
        if column not in merged.columns
    ]
    if missing:
        raise ValueError(f"需要 merge_coding 的输出（缺少列: {missing}）")
    names = _merged_coder_names(merged)
    unknown = sorted(
        {str(value) for value in merged[STATUS_COLUMN]} - set(CodingCellStatus)
    )
    if unknown:
        raise ValueError(
            f"合并长表含非法 status: {unknown}，合法值为 {list(CodingCellStatus)}"
        )
    return names


# ---------------------------------------------------------------------------
# 逐维度一致性（单选 / 多选 / 文本）
# ---------------------------------------------------------------------------

def _coder_pairs(names: Sequence[str]) -> list[tuple[str, str]]:
    """编码员两两配对（按传入顺序 i<j，确定性）。"""
    return [
        (names[i], names[j])
        for i in range(len(names))
        for j in range(i + 1, len(names))
    ]


def _choice_stats(
    values_by_coder: dict[str, list[str]], names: Sequence[str]
) -> tuple[float | None, float | None, dict]:
    """单选维度：逐对多类 κ（labels = 两侧选项并集）-> (维度 κ, Light, detail)。

    维度 κ 与 Light 均为"可算配对等权平均"（Light's kappa 的定义），故两者恒等；
    没有任何可算配对（两侧非空取值均为空）时均为 ``None``。
    """
    entries: list[dict] = []
    kappas: list[float] = []
    for left, right in _coder_pairs(names):
        values_a, values_b = values_by_coder[left], values_by_coder[right]
        options = sorted({value for value in (*values_a, *values_b) if value})
        result: KappaResult | None = (
            cohen_kappa(values_a, values_b, labels=options) if options else None
        )
        if result is None or result.n == 0:
            entries.append(
                {"coders": [left, right], "kappa": None, "po": None,
                 "pe": None, "n": 0}
            )
            continue
        entries.append(
            {"coders": [left, right], "kappa": result.kappa, "po": result.po,
             "pe": result.pe, "n": result.n}
        )
        kappas.append(result.kappa)
    options_all = sorted(
        {value for name in names for value in values_by_coder[name] if value}
    )
    kappa = _mean(kappas)
    return kappa, kappa, {"options": options_all, "pairs": entries}


def _option_flags(values: Sequence[str], option: str) -> list[str]:
    """多选格的 0/1 指示取值：未填 -> ``""``（不参与该 option 的比对）。"""
    return [
        "" if not value else ("1" if option in _multi_parts(value) else "0")
        for value in values
    ]


def _multi_stats(
    values_by_coder: dict[str, list[str]], names: Sequence[str]
) -> tuple[float | None, float | None, dict]:
    """多选维度：逐 option 0/1 逐对 κ -> (维度 κ, Light, detail)。

    - 维度 κ：有可算值的 option 的 κ 均值（option 等权；全无共现 -> ``None``）；
    - Light：每个配对先对"该对可算的 option"取均值，再对有可算值的配对取均值
      （pair 等权；无任何可算配对 -> ``None``）；
    - detail 保留逐 option 结果（含无共现项 na=True）与逐对均值。
    """
    pairs = _coder_pairs(names)
    options = sorted(
        {
            option
            for name in names
            for value in values_by_coder[name]
            for option in _multi_parts(value)
        }
    )
    option_entries: list[dict] = []
    option_kappas: list[float] = []
    pair_kappas: dict[tuple[str, str], list[float]] = {pair: [] for pair in pairs}
    for option in options:
        flags = {name: _option_flags(values_by_coder[name], option) for name in names}
        option_pairs: list[dict] = []
        option_values: list[float] = []
        for left, right in pairs:
            flags_a, flags_b = flags[left], flags[right]
            joint = sum(
                1
                for item_a, item_b in zip(flags_a, flags_b)
                if item_a == "1" and item_b == "1"
            )
            if joint == 0:  # 无共现：该选项在这一对编码员之间无法评估，跳过
                continue
            result = cohen_kappa(flags_a, flags_b, labels=BINARY_LABELS)
            if result.n == 0:
                continue
            option_pairs.append(
                {"coders": [left, right], "kappa": result.kappa, "po": result.po,
                 "pe": result.pe, "n": result.n}
            )
            option_values.append(result.kappa)
            pair_kappas[(left, right)].append(result.kappa)
        option_kappa = _mean(option_values)
        if option_kappa is not None:
            option_kappas.append(option_kappa)
        option_entries.append(
            {"option": option, "kappa": option_kappa, "na": option_kappa is None,
             "n_pairs": len(option_values), "pairs": option_pairs}
        )

    pair_means = [_mean(pair_kappas[pair]) for pair in pairs]
    detail = {
        "options": option_entries,
        "na_options": [entry["option"] for entry in option_entries if entry["na"]],
        "pairs": [
            {"coders": [left, right], "kappa": mean}
            for (left, right), mean in zip(pairs, pair_means)
        ],
    }
    kappa = _mean(option_kappas)
    light = _mean([value for value in pair_means if value is not None])
    return kappa, light, detail


def _text_stats(
    keys: Sequence[str], values_by_coder: dict[str, list[str]], names: Sequence[str]
) -> dict:
    """文本维度：κ 不适用，detail 给出逐格两侧取值并排（供人工比对）。

    ``same`` = 该格已填取值互不相同者为 False（仅一位编码员填写时无冲突可言 ->
    True）；``n_single_coded`` = 仅一位编码员填写的格数（对应 incomplete）。
    """
    rows: list[dict] = []
    n_same = n_mismatch = n_single_coded = 0
    for position, key in enumerate(keys):
        values = {name: values_by_coder[name][position] for name in names}
        coded = [value for value in values.values() if value]
        same = len(set(coded)) <= 1
        if same:
            n_same += 1
        else:
            n_mismatch += 1
        if len(coded) == 1:
            n_single_coded += 1
        rows.append({"zotero_key": key, "values": values, "same": same})
    return {
        "rows": rows,
        "n_same": n_same,
        "n_mismatch": n_mismatch,
        "n_single_coded": n_single_coded,
    }


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------

@dataclass
class CodingAgreementSummary:
    """编码一致性摘要（:func:`summarize_coding` 的输出）。

    - ``n_cells`` = 长表格数（至少一位编码员填写的 (键, 维度) 格）；三个状态计数
      之和恒等于 ``n_cells``；
    - ``agreement_rate`` = 共有格一致率 = ``n_agree / (n_agree + n_conflict)``，
      仅统计 >=2 位编码员填写的格；分母为 0 时取 ``0.0``（与
      :class:`coscreen.stats.merge.MultiMergeSummary` 的约定一致）；
    - ``per_dimension``：按维度名升序，每项
      ``{dimension, dtype, n, n_agree, n_conflict, n_incomplete, agreement_rate,
      kappa, light, detail}``；``n`` = 该维度的格数；``kappa`` / ``light`` 为
      ``float | None``（文本维度恒 None，无可算配对时 None）；``detail`` 为
      JSON 安全的逐维度细节（单选：options + 逐对 κ；多选：逐 option κ +
      逐对均值；文本：逐格两侧取值并排）。
    """

    n_cells: int
    n_agree: int
    n_conflict: int
    n_incomplete: int
    agreement_rate: float
    per_dimension: list[dict] = field(default_factory=list)


def _clean_dtype_map(dtype_map: dict[str, str]) -> dict[str, str]:
    """校验 dtype_map（类型必须是 CODING_DTYPES 之一），返回规整副本。"""
    if not isinstance(dtype_map, dict):
        raise ValueError(
            f"dtype_map 必须是 dict，实际为 {type(dtype_map).__name__}"
        )
    cleaned: dict[str, str] = {}
    invalid: list[str] = []
    for dimension, dtype in dtype_map.items():
        value = "" if dtype is None else str(dtype).strip()
        if value not in CODING_DTYPES:
            invalid.append(f"{str(dimension)!r}: {dtype!r}")
        else:
            cleaned[str(dimension)] = value
    if invalid:
        raise ValueError(
            f"dtype_map 含非法类型（合法值为 {list(CODING_DTYPES)}）: "
            + "；".join(invalid)
        )
    return cleaned


def summarize_coding(
    merged: pd.DataFrame, dtype_map: dict[str, str]
) -> CodingAgreementSummary:
    """逐维度统计一致性：状态计数 + 单选/多选 κ、Light + 文本并排细节。

    ``merged`` 必须是 :func:`merge_coding` 的输出（含 ``zotero_key`` /
    ``dimension`` / ``status`` 与 >=2 列 ``value_<coder>``）；``dtype_map`` 给出
    每个维度的类型（``choice`` / ``multi`` / ``text``，见模块 docstring）——
    **引擎不推断类型**：未覆盖 ``merged`` 中出现的维度、或类型取值非法 ->
    中文 ValueError（逐一列出维度），``dtype_map`` 中多出的维度被忽略。

    统计口径见模块 docstring（单选多类 κ、多选逐 option 二分类 κ、文本不计算 κ）；
    维度按名称升序，配对按 ``value_<coder>`` 列序 i<j，确定性。
    """
    names = _require_merged(merged)
    types = _clean_dtype_map(dtype_map)
    dims = sorted({str(value) for value in merged[DIMENSION_COLUMN]})
    uncovered = [dim for dim in dims if dim not in types]
    if uncovered:
        raise ValueError(
            f"dtype_map 未覆盖维度: {uncovered}"
            "（引擎不推断类型：请由 UI 的类型启发式/手动下拉给出该维度的 "
            "choice/multi/text，或显式取 merged 表 dtype 列的结构性缺省）"
        )

    statuses = [str(value) for value in merged[STATUS_COLUMN]]
    n_agree = sum(1 for value in statuses if value == "agree")
    n_conflict = sum(1 for value in statuses if value == "conflict")
    n_incomplete = sum(1 for value in statuses if value == "incomplete")
    denominator = n_agree + n_conflict

    per_dimension: list[dict] = []
    for dim in dims:
        mask = merged[DIMENSION_COLUMN].astype(str) == dim
        part = merged.loc[mask]
        dim_statuses = [str(value) for value in part[STATUS_COLUMN]]
        dim_agree = sum(1 for value in dim_statuses if value == "agree")
        dim_conflict = sum(1 for value in dim_statuses if value == "conflict")
        dim_incomplete = sum(1 for value in dim_statuses if value == "incomplete")
        dim_denominator = dim_agree + dim_conflict
        keys = [str(value) for value in part[KEY_COLUMN]]
        values_by_coder = {
            name: [_cell(value) for value in part[f"{VALUE_PREFIX}{name}"]]
            for name in names
        }
        dtype = types[dim]
        if dtype == DTYPE_TEXT:
            kappa: float | None = None
            light: float | None = None
            detail = _text_stats(keys, values_by_coder, names)
        elif dtype == DTYPE_MULTI:
            kappa, light, detail = _multi_stats(values_by_coder, names)
        else:
            kappa, light, detail = _choice_stats(values_by_coder, names)
        per_dimension.append(
            {
                "dimension": dim,
                "dtype": dtype,
                "n": int(len(part)),
                "n_agree": dim_agree,
                "n_conflict": dim_conflict,
                "n_incomplete": dim_incomplete,
                "agreement_rate": (
                    dim_agree / dim_denominator if dim_denominator > 0 else 0.0
                ),
                "kappa": kappa,
                "light": light,
                "detail": detail,
            }
        )

    return CodingAgreementSummary(
        n_cells=int(len(merged)),
        n_agree=n_agree,
        n_conflict=n_conflict,
        n_incomplete=n_incomplete,
        agreement_rate=(n_agree / denominator) if denominator > 0 else 0.0,
        per_dimension=per_dimension,
    )


# ---------------------------------------------------------------------------
# JSON 载荷与导出文本
# ---------------------------------------------------------------------------

def _json_safe(value: Any) -> Any:
    """递归转换为 JSON 安全值。

    - dict 的键一律 ``str``（JSON 只接受字符串键，tuple 键因此被消解）；
    - tuple -> list（JSON 无元组）；
    - set / frozenset -> 按 ``str`` 排序的 list（避免哈希顺序带来的不确定性）；
    - 非有限浮点（inf / nan）-> None（``json.dumps`` 会写出非法 JSON）；
    - numpy 标量 -> ``.item()``；其余未知类型退化为 ``str``。
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_json_safe(item) for item in sorted(value, key=lambda item: str(item))]
    if hasattr(value, "item"):  # numpy 标量等
        return _json_safe(value.item())
    return str(value)


def agreement_payload(summary: CodingAgreementSummary) -> dict:
    """摘要 -> JSON 安全 dict（无 tuple 键），供 stats 导出与接口复用。

    结构：``{n_cells, n_agree, n_conflict, n_incomplete, agreement_rate,
    n_dimensions, per_dimension: [{dimension, dtype, n, n_agree, n_conflict,
    n_incomplete, agreement_rate, kappa, light, detail}, ...]}``；
    ``json.dumps(payload, ensure_ascii=False)`` 必定成功（元素均为 JSON 原生类型，
    配对以 ``{"coders": [左, 右]}`` 而非元组键表达）。
    """
    payload = {
        "n_cells": summary.n_cells,
        "n_agree": summary.n_agree,
        "n_conflict": summary.n_conflict,
        "n_incomplete": summary.n_incomplete,
        "agreement_rate": summary.agreement_rate,
        "n_dimensions": len(summary.per_dimension),
        "per_dimension": summary.per_dimension,
    }
    return _json_safe(payload)


def _csv_text(frame: pd.DataFrame) -> str:
    """DataFrame -> utf-8-sig 文本（首字符 U+FEFF；``encode("utf-8")`` 即标准字节）。

    列序保持 ``frame`` 列序，行序由调用方排定（本模块各导出助手均先排序），
    ``\n`` 行尾、最小引用（pandas 默认），同一输入两次调用字节级一致。
    """
    return "\ufeff" + frame.to_csv(index=False)


def merged_coding_csv(merged: pd.DataFrame) -> str:
    """合并长表 -> utf-8-sig CSV 文本（列序 = 传入列序，行按 (zotero_key, dimension)）。"""
    _require_merged(merged)
    ordered = merged.sort_values([KEY_COLUMN, DIMENSION_COLUMN], kind="stable")
    return _csv_text(ordered)


def conflicts_csv(merged: pd.DataFrame) -> str:
    """仅 ``status == "conflict"`` 的行 -> utf-8-sig CSV 文本（列序同上，排序同上）。

    列与 :func:`merged_coding_csv` 完全一致（含各编码员的取值），便于逐条仲裁。
    """
    _require_merged(merged)
    conflicts = merged.loc[merged[STATUS_COLUMN].astype(str) == "conflict"]
    ordered = conflicts.sort_values([KEY_COLUMN, DIMENSION_COLUMN], kind="stable")
    return _csv_text(ordered)


def _fmt_number(value: float | None) -> str:
    """κ / Light / 一致率 -> CSV 单元格（None 留空；其余定点 4 位小数，确定性）。"""
    return "" if value is None else f"{float(value):.4f}"


def stats_csv(summary: CodingAgreementSummary) -> str:
    """一致性统计 -> utf-8-sig CSV 文本（一行总计 scope=all + 每维度一行）。

    列序见 :data:`STATS_CSV_COLUMNS`；维度按名称升序（确定性）；``kappa`` /
    ``light`` 无值为空串、其余按 :func:`_fmt_number` 定点 4 位；``detail`` 列为
    该维度 detail 的 JSON 文本（``ensure_ascii=False, sort_keys=True``，从 CSV
    侧可直接 ``json.loads`` 还原）。
    """
    rows: list[dict[str, str]] = [
        {
            "scope": "all",
            "dimension": "",
            "dtype": "",
            "n_cells": str(int(summary.n_cells)),
            "n_agree": str(int(summary.n_agree)),
            "n_conflict": str(int(summary.n_conflict)),
            "n_incomplete": str(int(summary.n_incomplete)),
            "agreement_rate": _fmt_number(summary.agreement_rate),
            "kappa": "",
            "light": "",
            "detail": "",
        }
    ]
    for item in sorted(summary.per_dimension, key=lambda entry: str(entry["dimension"])):
        detail = item.get("detail") or {}
        rows.append(
            {
                "scope": "dimension",
                "dimension": str(item["dimension"]),
                "dtype": str(item["dtype"]),
                "n_cells": str(int(item["n"])),
                "n_agree": str(int(item["n_agree"])),
                "n_conflict": str(int(item["n_conflict"])),
                "n_incomplete": str(int(item.get("n_incomplete", 0))),
                "agreement_rate": _fmt_number(item.get("agreement_rate")),
                "kappa": _fmt_number(item.get("kappa")),
                "light": _fmt_number(item.get("light")),
                "detail": json.dumps(detail, ensure_ascii=False, sort_keys=True),
            }
        )
    frame = pd.DataFrame(rows, columns=list(STATS_CSV_COLUMNS))
    return _csv_text(frame)
