"""编码方案工具（数据提取阶段）—— 维度 / 编码值 / 图文备注 / 队列（SPEC §15.1）。

- 四表 coding_dimensions / coding_values / coding_notes / coding_note_images 由
  :mod:`coscreen.db` 统一建表（``_SCHEMA`` + ``_connect`` 幂等自愈），本模块只读写；
- 维度校验（非空名称、重名、非法 dtype、选项型不足 2 项、选项含分隔符）在
  :func:`add_dimension` / :func:`update_dimension` / :func:`import_scheme` 三处
  共用同一套 ``_validate_dimension``，失败一律抛**中文** ``ValueError``；
- 方案 JSON（:func:`export_scheme` / :func:`import_scheme`）确定性：字段固定、
  维度按 ``(section, position, id)`` 排序，可跨库/跨编码员传递；导入是
  all-or-nothing（校验先全部通过再落库）；
- 编码值：``choice`` 逐项校验 ∈ options（多选以 ``"|"`` 连接，保存时按选项顺序
  规范化并去重），``numeric`` 只接受有限数值，``text`` 原样保存（仅去首尾空白）；
  空串表示"未填/清空"；
- 删除维度级联清空 values / notes / images，并 best-effort 删除磁盘图片文件；
- 图片落盘 ``{db 所在目录}/coding_assets/``（见 :func:`coding_assets_dir`），
  魔数校验 PNG / JPEG / WebP + sha256，文件名 ``note{note_id}_{sha256[:12]}.{ext}``
  （内容寻址：同图重复上传幂等覆盖同一文件）；
- 队列 :func:`coding_queue`：``mode`` 缺省为字面量默认 ``"stage2"``（UI 设置键
  ``coding_queue_mode`` 尚未落地——后端无该设置存储，接入点在
  :func:`coding_queue` 的 ``mode=None`` 分支）；
  ``"stage2"`` = 复筛纳入，为空时回退初筛纳入；``"stage1"`` = 初筛纳入；
- 所有列表/队列输出确定性排序；所有函数每次调用自行开闭连接（``with closing``，
  WAL，外键约束开启）。

除 SPEC §15.1 列出的接口外，本模块另附三个**追加式**小工具供页面/导出层复用
（不改变任何既有签名）：:func:`coding_assets_dir`、:func:`get_coding_note`、
:func:`list_coding_notes`。
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import unicodedata
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Sequence

from coscreen.db import (
    StrPath,
    _connect,  # noqa: PLC2701  同包内复用唯一连接工厂（WAL/外键/旧库自愈）
    list_decisions,
)
from coscreen.fulltext import list_stage2_decisions  # 复筛纳入键的唯一口径（SPEC §14）
from coscreen.security import safe_path

if TYPE_CHECKING:
    from coscreen.research_trace import TraceContext

__all__ = [
    "CODING_ASSETS_DIRNAME",
    "CODING_QUEUE_MODES",
    "DEFAULT_CODING_QUEUE_MODE",
    "DIMENSION_DTYPES",
    "IMAGE_EXTENSIONS",
    "OCR_STATUSES",
    "QUEUE_MODE_STAGE1",
    "QUEUE_MODE_STAGE2",
    "SCHEME_VERSION",
    "VALUE_SEPARATOR",
    "add_dimension",
    "add_note_image",
    "coding_assets_dir",
    "coding_progress",
    "coding_queue",
    "delete_dimension",
    "export_scheme",
    "get_coding_note",
    "get_coding_values",
    "import_scheme",
    "list_coding_notes",
    "list_dimensions",
    "list_note_images",
    "save_coding_note",
    "save_coding_value",
    "set_dimension_order",
    "set_ocr_result",
    "update_dimension",
]


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 方案 JSON 版本（export_scheme 恒写 1，import_scheme 校验）
SCHEME_VERSION = 1

#: 合法维度类型：choice=选项（单选/多选），text=自由填空，numeric=数值
DIMENSION_DTYPES = ("choice", "text", "numeric")

#: 多选值的连接分隔符（choice 多选；选项本身不得含该字符）
VALUE_SEPARATOR = "|"

#: 队列模式（SPEC §15.1）：stage2=复筛纳入（空则回退初筛纳入），stage1=初筛纳入
QUEUE_MODE_STAGE2 = "stage2"
QUEUE_MODE_STAGE1 = "stage1"
CODING_QUEUE_MODES = (QUEUE_MODE_STAGE2, QUEUE_MODE_STAGE1)

#: mode 缺省值（设置键 coding_queue_mode 尚未落地时的字面量默认）
DEFAULT_CODING_QUEUE_MODE = QUEUE_MODE_STAGE2

#: set_ocr_result 的合法状态（pending 为入库默认态，仅由 add_note_image 产生）
OCR_STATUSES = ("done", "failed", "skipped")

#: 图片资产目录名（相对 db 文件所在目录）
CODING_ASSETS_DIRNAME = "coding_assets"

#: 允许的图片魔数 -> 落盘扩展名（其余格式一律拒绝）
IMAGE_EXTENSIONS = {"png": "png", "jpeg": "jpg", "webp": "webp"}

#: 各编码表列（查询/回填共用，避免手写列名漂移）
_DIM_COLUMNS = (
    "id", "name", "dtype", "options_json", "multi_select",
    "unit", "description", "section", "position", "created_at",
)
_VALUE_COLUMNS = ("dimension_id", "zotero_key", "value", "updated_at")
_NOTE_COLUMNS = (
    "id", "dimension_id", "zotero_key", "text", "created_at", "updated_at",
)
_IMAGE_COLUMNS = (
    "id", "note_id", "path", "sha256", "ocr_text", "ocr_status", "created_at",
)

#: update_dimension 允许的字段（其余字段名 -> ValueError）
_UPDATABLE_FIELDS = (
    "name", "dtype", "options", "multi_select", "unit",
    "description", "section", "position",
)

#: 图片识别失败时的中文补充指引（不含任何英文栈/异常正文）
_TROUBLE_HINT = "请检查图片是否完整，或稍后重试；首次识别需要已下载的 MinerU 模型权重。"


def _now() -> str:
    """ISO8601 本地时间戳（与 coscreen.db._now 同口径）。"""
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 入参校验（维度）
# ---------------------------------------------------------------------------

def _clean_text(value: Any) -> str:
    """统一把外部传入值规整为去首尾空白的字符串（None -> ""）。"""
    return "" if value is None else str(value).strip()


def _normalize_dtype(dtype: Any) -> str:
    """校验维度类型：仅 choice / text / numeric，否则中文 ValueError。"""
    cleaned = _clean_text(dtype)
    if cleaned not in DIMENSION_DTYPES:
        raise ValueError(
            f"非法维度类型: {dtype!r}，必须为 {DIMENSION_DTYPES} 之一"
        )
    return cleaned


def _normalize_options(dtype: str, options: Iterable[Any] | None) -> list[str]:
    """规整选项列表：去空白、丢空项、按首现顺序去重。

    - 非 ``choice`` 类型：选项无意义，一律折算为 ``[]``；
    - ``dtype == "choice"``：去重后至少 2 项，否则中文 ValueError；
    - 选项内不得含多选分隔符 ``"|"``（否则多选值无法无歧义解析）。
    """
    if dtype != "choice":
        return []
    cleaned: list[str] = []
    for raw in options or []:
        item = _clean_text(raw)
        if not item:
            continue
        if VALUE_SEPARATOR in item:
            raise ValueError(
                f"选项不能包含分隔符 {VALUE_SEPARATOR!r}: {item!r}（该字符用于连接多选值）"
            )
        if item not in cleaned:
            cleaned.append(item)
    if len(cleaned) < 2:
        raise ValueError(
            f"选项型维度至少需要 2 个非空选项，当前仅 {len(cleaned)} 个"
        )
    return cleaned


def _validate_dimension(
    name: Any, dtype: Any, options: Iterable[Any] | None,
    multi_select: Any = False, unit: Any = "", description: Any = "",
    section: Any = "",
) -> dict:
    """维度字段全套校验（三处调用共用），返回规整后的字段字典。"""
    clean_name = _clean_text(name)
    if not clean_name:
        raise ValueError("维度名称不能为空")
    clean_dtype = _normalize_dtype(dtype)
    clean_options = _normalize_options(clean_dtype, options)
    return {
        "name": clean_name,
        "dtype": clean_dtype,
        "options": clean_options,
        "multi_select": bool(multi_select) and clean_dtype == "choice",
        "unit": _clean_text(unit),
        "description": _clean_text(description),
        "section": _clean_text(section),
    }


def _row_to_dimension(row: Sequence[Any]) -> dict:
    """coding_dimensions 行 -> 公开字典（options 解析为 list，multi_select 为 bool）。"""
    record = dict(zip(_DIM_COLUMNS, row))
    try:
        options = json.loads(record["options_json"] or "[]")
    except ValueError:  # 库内数据被外部改坏时不炸，退回空选项
        options = []
    if not isinstance(options, list):
        options = []
    return {
        "id": int(record["id"]),
        "name": record["name"] or "",
        "dtype": record["dtype"] or "text",
        "options": [str(o) for o in options],
        "multi_select": bool(record["multi_select"]),
        "unit": record["unit"] or "",
        "description": record["description"] or "",
        "section": record["section"] or "",
        "position": int(record["position"]),
        "created_at": record["created_at"] or "",
    }


def _options_json(options: list[str]) -> str:
    """选项列表 -> JSON 文本（确定性：固定顺序、中文不转义）。"""
    return json.dumps(list(options), ensure_ascii=False)


def _fetch_dimension(conn: sqlite3.Connection, dim_id: int) -> dict | None:
    """按 id 取维度（公开字典形态）；不存在返回 None。"""
    row = conn.execute(
        "SELECT id, name, dtype, options_json, multi_select, unit, description, section, "
        "position, created_at FROM coding_dimensions WHERE id = ?",
        (int(dim_id),),
    ).fetchone()
    return _row_to_dimension(row) if row else None


def _require_dimension(conn: sqlite3.Connection, dim_id: int) -> dict:
    """按 id 取维度，不存在则中文 ValueError。"""
    dim = _fetch_dimension(conn, dim_id)
    if dim is None:
        raise ValueError(f"维度不存在: id={dim_id}")
    return dim


def _trace_dimension(conn: sqlite3.Connection, context: TraceContext, dim: dict) -> dict:
    from coscreen.research_trace import field_lifecycle

    definition = {
        "legacy_id": int(dim["id"]), "name": dim["name"], "type": dim["dtype"],
        "options": list(dim["options"]), "multi_select": bool(dim["multi_select"]),
        "unit": dim["unit"], "description": dim["description"],
        "section": dim["section"], "position": int(dim["position"]),
        "created_at": dim.get("created_at", ""),
    }
    definition["field_id"] = field_lifecycle(conn, context, dim["id"], definition)
    return definition


def _ensure_name_free(
    conn: sqlite3.Connection, name: str, *, exclude_id: int | None = None
) -> None:
    """维度名称唯一性预检查（同名 -> 中文 ValueError；DB UNIQUE 兜底）。"""
    if exclude_id is None:
        row = conn.execute(
            "SELECT id FROM coding_dimensions WHERE name = ?", (name,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id FROM coding_dimensions WHERE name = ? AND id <> ?",
            (name, int(exclude_id)),
        ).fetchone()
    if row is not None:
        raise ValueError(f"维度名称已存在: {name!r}（请改用其他名称）")


# ---------------------------------------------------------------------------
# 维度 CRUD
# ---------------------------------------------------------------------------

def add_dimension(
    db: StrPath,
    name: str,
    dtype: str,
    options: list[str],
    multi_select: bool = False,
    unit: str = "",
    description: str = "",
    section: str = "",
    *,
    trace_context: TraceContext | None = None,
) -> int:
    """新增一个编码维度，返回其 id（position 追加到全局末尾）。

    - ``name`` 去首尾空白后不得为空，且不得与既有维度重名 -> ValueError（中文）；
    - ``dtype`` 必须为 ``"choice"`` / ``"text"`` / ``"numeric"``；
    - ``dtype == "choice"`` 时 ``options`` 去空白/去重后至少 2 项，
      每项不得含多选分隔符 ``"|"``；``dtype == "text"`` 时选项一律忽略（存 ``[]``）；
    - ``multi_select`` 仅对 ``choice`` 生效。
    """
    fields = _validate_dimension(
        name, dtype, options, multi_select, unit, description, section
    )
    with closing(_connect(db)) as conn, conn:
        if trace_context is not None:
            from coscreen.research_trace import begin_capture_transaction, begin_write_request

            begin_capture_transaction(conn, trace_context, "coding")
            replay, result = begin_write_request(
                conn, trace_context, stage="coding", operation="coding_dimension_add",
                subject_id="scheme",
                payload={"name": fields["name"], "dtype": fields["dtype"],
                         "options": fields["options"], "multi_select": fields["multi_select"],
                         "unit": fields["unit"], "description": fields["description"],
                         "section": fields["section"]},
            )
            if replay:
                return int(result)
        _ensure_name_free(conn, fields["name"])
        row = conn.execute(
            "SELECT COALESCE(MAX(position), -1) FROM coding_dimensions"
        ).fetchone()
        position = int(row[0]) + 1
        try:
            cur = conn.execute(
                "INSERT INTO coding_dimensions "
                "(name, dtype, options_json, multi_select, unit, description, "
                "section, position, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    fields["name"], fields["dtype"], _options_json(fields["options"]),
                    int(fields["multi_select"]), fields["unit"],
                    fields["description"], fields["section"], position, _now(),
                ),
            )
        except sqlite3.IntegrityError as exc:  # UNIQUE(name) 兜底
            raise ValueError(
                f"维度名称已存在: {fields['name']!r}（请改用其他名称）"
            ) from exc
        if trace_context is not None:
            from coscreen.research_trace import capture_event, is_recording

            if is_recording(conn, trace_context, "coding"):
                field = _trace_dimension(conn, trace_context, _require_dimension(conn, int(cur.lastrowid)))
                capture_event(
                    conn, trace_context, stage="coding", action="field_definition_created",
                    subject_id=field["field_id"], before=None, after=field, field=field,
                )
            from coscreen.research_trace import finish_write_request

            finish_write_request(conn, trace_context, int(cur.lastrowid))
        return int(cur.lastrowid)


def list_dimensions(db: StrPath) -> list[dict]:
    """列出全部维度，按 ``(section, position, id)`` 升序（确定性）。

    每项为含全部字段的字典，其中 ``options`` 已解析为 list、``multi_select``
    为 bool、``position``/``id`` 为 int。
    """
    with closing(_connect(db)) as conn:
        rows = conn.execute(
            "SELECT id, name, dtype, options_json, multi_select, unit, description, section, "
            "position, created_at FROM coding_dimensions ORDER BY section, position, id"
        ).fetchall()
    return [_row_to_dimension(r) for r in rows]


def _reconcile_saved_values(
    conn: sqlite3.Connection, dim_id: int, new_dim: dict
) -> list[tuple[str, str, str]]:
    """维度定义变更时校验并规范化其存量编码值（r9 F2-90）。

    在**同一事务内**先全量校验、再统一改写，因此失败不会留下半迁移状态：
    - 存量值按新定义走 :func:`_normalize_value`（choice 逐项 ∈ 新选项且单选
      不多值；numeric 必须有限数值）——任一值非法 -> 中文 ValueError，列出
      受影响文献键，本次改型整体拒绝（不让库内值与新定义静默矛盾）；
    - 全部通过后只把"规范化形态与原文不同"的行改写为新形态（选项顺序/首尾
      空白/多选顺序），其余行原样保留（含 updated_at，不制造伪变更）；
    - 该维度设为必需证据字段时，备注/图片（evidence）不依赖 dtype，不改动。

    返回被改写的 ``(zotero_key, old, new)`` 列表，供调用方补发审计事件。
    """
    rows = conn.execute(
        "SELECT zotero_key, value FROM coding_values WHERE dimension_id=? "
        "ORDER BY zotero_key",
        (int(dim_id),),
    ).fetchall()
    normalized: list[tuple[str, str, str]] = []  # (key, old, new)
    rejected: list[str] = []
    for key, value in rows:
        try:
            new_value = _normalize_value(new_dim, value)
        except ValueError:
            rejected.append(str(key))
            continue
        if new_value != (value or ""):
            normalized.append((str(key), value or "", new_value))
    if rejected:
        shown = ", ".join(rejected[:10]) + (" …" if len(rejected) > 10 else "")
        raise ValueError(
            f"维度「{new_dim['name']}」已有 {len(rejected)} 条保存值不符合新定义"
            f"（{shown}）。请先清除这些值或另选类型后再改型，已保存的研究不会被自动改写。"
        )
    for key, _old, new_value in normalized:
        conn.execute(
            "UPDATE coding_values SET value=?, updated_at=? "
            "WHERE dimension_id=? AND zotero_key=?",
            (new_value, _now(), int(dim_id), key),
        )
    return normalized


def update_dimension(
    db: StrPath,
    dim_id: int,
    *,
    trace_context: TraceContext | None = None,
    **fields,
) -> None:
    """按字段更新一个维度（只写传入字段，其余保持原值）。

    - 校验规则与 :func:`add_dimension` 完全一致（对"更新后的整体取值"校验）；
    - 允许字段：name / dtype / options / multi_select / unit / description /
      section / position，未知字段 -> 中文 ValueError；
    - 类型切换到非 ``choice`` 时选项自动清空；切到 ``choice`` 必须
      同时给出至少 2 个选项（本次未给且原选项不足则 ValueError）；
    - 维度不存在 -> ValueError；改名撞上其他维度 -> ValueError（中文）。
    """
    unknown = [k for k in fields if k not in _UPDATABLE_FIELDS]
    if unknown:
        raise ValueError(
            f"不支持的维度字段: {sorted(unknown)}，合法字段为 {list(_UPDATABLE_FIELDS)}"
    )
    with closing(_connect(db)) as conn, conn:
        if trace_context is not None:
            from coscreen.research_trace import begin_capture_transaction, begin_write_request

            begin_capture_transaction(conn, trace_context, "coding")
            replay, _ = begin_write_request(
                conn, trace_context, stage="coding", operation="coding_dimension_update",
                subject_id=str(dim_id), payload=fields,
            )
            if replay:
                return None
        current = _require_dimension(conn, dim_id)
        old_field = None
        if trace_context is not None:
            from coscreen.research_trace import is_recording

            if is_recording(conn, trace_context, "coding"):
                old_field = _trace_dimension(conn, trace_context, current)

        merged = {
            "name": current["name"],
            "dtype": current["dtype"],
            "options": list(current["options"]),
            "multi_select": current["multi_select"],
            "unit": current["unit"],
            "description": current["description"],
            "section": current["section"],
        }
        merged.update(fields)
        # 类型切换的选项兜底：切到 text 时选项无意义（清空）；切到 choice 且
        # 本次未显式给 options 时沿用原选项，仍不足 2 项则校验阶段报错。
        if "dtype" in fields and "options" not in fields:
            merged["options"] = (
                [] if _clean_text(fields["dtype"]) == "text" else list(current["options"])
            )

        validated = _validate_dimension(
            merged["name"], merged["dtype"], merged["options"],
            merged["multi_select"], merged["unit"], merged["description"],
            merged["section"],
        )

        position = current["position"]
        if "position" in fields:
            try:
                position = int(fields["position"])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"position 必须为整数: {fields['position']!r}"
                ) from exc

        # r9 F2-90：类型/选项口径变了就必须同步处理已保存的编码值——旧实现
        # 只改定义，库内 'A|B' 之类在新定义下已非法的值照旧被 complete_keys
        # 计入"已完成"并逐字进导出（数据污染，且读取侧不再有任何校验）。
        # 每个存量值按新定义重新校验：仍合法则写成规范化形态（选项顺序/空白
        # 归一，与 save_coding_value 同口径）；已非法则整体拒绝本次改型并
        # 指明文献键，让用户先清值或另选类型（与 import_scheme 拒绝"自动改写
        # 已保存研究"的既有政策一致，同 docs/WORKFLOW_CONTROLS.md:85）。
        definition_changed = (
            validated["dtype"] != current["dtype"]
            or validated["options"] != list(current["options"])
            or bool(validated["multi_select"]) != bool(current["multi_select"])
        )
        migrated: list[tuple[str, str, str]] = []
        if definition_changed:
            migrated = _reconcile_saved_values(conn, int(dim_id), validated)

        _ensure_name_free(conn, validated["name"], exclude_id=int(dim_id))
        try:
            conn.execute(
                "UPDATE coding_dimensions SET name=?, dtype=?, options_json=?, "
                "multi_select=?, unit=?, description=?, section=?, position=? "
                "WHERE id=?",
                (
                    validated["name"], validated["dtype"],
                    _options_json(validated["options"]),
                    int(validated["multi_select"]), validated["unit"],
                    validated["description"], validated["section"], position,
                    int(dim_id),
                ),
            )
        except sqlite3.IntegrityError as exc:  # UNIQUE(name) 兜底
            raise ValueError(
                f"维度名称已存在: {validated['name']!r}（请改用其他名称）"
            ) from exc
        if old_field is not None:
            from coscreen.research_trace import capture_event

            after_field = _trace_dimension(conn, trace_context, _require_dimension(conn, dim_id))
            # 定义变更同时迁移了存量值的规范化形态时，逐条留下值变更事件
            # （subject 与 save_coding_value 同为 zotero_key，action 同集合），
            # 使"改型 + 改写"在审计链上可追溯、可撤销定位。
            for key, old_value, new_value in migrated:
                capture_event(
                    conn, trace_context, stage="coding",
                    action="coding_value_cleared" if not new_value else "coding_value_changed",
                    subject_id=key, before={"value": old_value},
                    after={"value": new_value}, field=after_field,
                )
            capture_event(
                conn, trace_context, stage="coding", action="field_definition_changed",
                subject_id=after_field["field_id"], before=old_field, after=after_field,
                field=after_field,
            )
        if trace_context is not None:
            from coscreen.research_trace import finish_write_request

            finish_write_request(conn, trace_context)


def set_dimension_order(
    db: StrPath,
    ids: list[int],
    *,
    trace_context: TraceContext | None = None,
) -> None:
    """整体重排维度：按 ``ids`` 顺序写入 position = 0,1,2...（同一事务）。

    - ``ids`` 中的每个 id 都必须存在（否则中文 ValueError 列出未知 id）；
    - 同一 id 不得重复出现；空列表是合法的无操作；
    - 未出现在 ``ids`` 中的维度保持原 position 不变（列表仍按
      ``(section, position, id)`` 确定性排序）。
    """
    if not ids:
        return
    normalized = [int(i) for i in ids]
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"ids 中存在重复维度: {normalized}")
    with closing(_connect(db)) as conn, conn:
        if trace_context is not None:
            from coscreen.research_trace import begin_capture_transaction, begin_write_request

            begin_capture_transaction(conn, trace_context, "coding")
            replay, _ = begin_write_request(
                conn, trace_context, stage="coding", operation="coding_dimension_order",
                subject_id="scheme", payload=normalized,
            )
            if replay:
                return None
        old_fields = {}
        if trace_context is not None:
            from coscreen.research_trace import is_recording

            if is_recording(conn, trace_context, "coding"):
                for dim_id in normalized:
                    old_fields[dim_id] = _trace_dimension(conn, trace_context, _require_dimension(conn, dim_id))
        known = {
            int(r[0]) for r in conn.execute("SELECT id FROM coding_dimensions")
        }
        unknown = [i for i in normalized if i not in known]
        if unknown:
            raise ValueError(f"维度不存在: {unknown}")
        for position, dim_id in enumerate(normalized):
            conn.execute(
                "UPDATE coding_dimensions SET position=? WHERE id=?",
                (position, dim_id),
            )
        if old_fields:
            from coscreen.research_trace import capture_event

            for dim_id, before in old_fields.items():
                after = _trace_dimension(conn, trace_context, _require_dimension(conn, dim_id))
                capture_event(
                    conn, trace_context, stage="coding", action="field_definition_changed",
                    subject_id=after["field_id"], before=before, after=after, field=after,
                )
        if trace_context is not None:
            from coscreen.research_trace import finish_write_request

            finish_write_request(conn, trace_context)


def _cascade_delete_dimensions(
    conn: sqlite3.Connection,
    ids: Sequence[int],
    *,
    trace_context: TraceContext | None = None,
    trace_fields: dict[int, dict] | None = None,
) -> list[str]:
    """删除给定维度的值/备注/图片行，返回待清理的磁盘图片路径（不删文件）。

    调用方在事务提交后 best-effort 删除返回的路径。
    """
    if not ids:
        return []
    marks = ", ".join("?" * len(ids))
    if trace_context is not None and trace_fields:
        from coscreen.research_trace import capture_event

        for dim_id in ids:
            field = trace_fields.get(int(dim_id))
            if field is None:
                continue
            for key, value in conn.execute(
                f"SELECT zotero_key,value FROM coding_values WHERE dimension_id=? ORDER BY zotero_key",
                (int(dim_id),),
            ):
                capture_event(
                    conn, trace_context, stage="coding", action="coding_value_cleared",
                    subject_id=str(key), before={"value": value or ""}, after={"value": ""},
                    field=field,
                )
            for note_id, key, text in conn.execute(
                "SELECT id,zotero_key,text FROM coding_notes WHERE dimension_id=? ORDER BY id",
                (int(dim_id),),
            ):
                for image_id, digest, ocr_text, ocr_status in conn.execute(
                    "SELECT id,sha256,ocr_text,ocr_status FROM coding_note_images "
                    "WHERE note_id=? ORDER BY id", (int(note_id),),
                ):
                    capture_event(
                        conn, trace_context, stage="coding", action="coding_note_image_removed",
                        subject_id=str(key),
                        before={"image_id": int(image_id), "sha256": digest,
                                "ocr_text": ocr_text or "", "ocr_status": ocr_status},
                        after=None, field=field,
                    )
                capture_event(
                    conn, trace_context, stage="coding", action="coding_note_cleared",
                    subject_id=str(key), before={"note_id": int(note_id), "text": text or ""},
                    after=None, field=field,
                )
    paths = [
        r[0]
        for r in conn.execute(
            "".join((
                "SELECT i.path FROM coding_note_images i JOIN coding_notes n ",
                "ON n.id = i.note_id WHERE n.dimension_id IN (", marks, ")",
            )),
            tuple(ids),
        ).fetchall()
    ]
    conn.execute(
        "".join((
            "DELETE FROM coding_note_images WHERE note_id IN ",
            "(SELECT id FROM coding_notes WHERE dimension_id IN (", marks, "))",
        )),
        tuple(ids),
    )
    conn.execute(
        "".join(("DELETE FROM coding_notes WHERE dimension_id IN (", marks, ")")), tuple(ids)
    )
    conn.execute(
        "".join(("DELETE FROM coding_values WHERE dimension_id IN (", marks, ")")), tuple(ids)
    )
    return [p for p in paths if p]


def _remove_assets(paths: Iterable[str]) -> None:
    """Retain binaries because historical Undo entries may still reference them."""
    # Associations are removed transactionally; binaries remain recoverable by Undo.
    return


def delete_dimension(
    db: StrPath,
    dim_id: int,
    *,
    trace_context: TraceContext | None = None,
) -> None:
    """删除一个维度，并级联清空其编码值 / 备注 / 备注图片（含磁盘文件）。

    维度不存在 -> 中文 ValueError；磁盘文件删除为 best-effort（失败不影响
    数据库一致性）。
    """
    with closing(_connect(db)) as conn, conn:
        if trace_context is not None:
            from coscreen.research_trace import begin_capture_transaction, begin_write_request

            begin_capture_transaction(conn, trace_context, "coding")
            replay, _ = begin_write_request(
                conn, trace_context, stage="coding", operation="coding_dimension_delete",
                subject_id=str(dim_id), payload={"dimension_id": int(dim_id)},
            )
            if replay:
                return None
        dim = _fetch_dimension(conn, dim_id)
        if dim is None:
            raise ValueError(f"维度不存在: id={dim_id}")
        if conn.execute("SELECT 1 FROM coding_values WHERE dimension_id=? UNION ALL SELECT 1 FROM coding_notes WHERE dimension_id=? LIMIT 1", (dim_id, dim_id)).fetchone():
            raise ValueError("This field contains saved research. Preserve it and add a new field instead.")
        field = None
        if trace_context is not None:
            from coscreen.research_trace import is_recording

            if is_recording(conn, trace_context, "coding"):
                field = _trace_dimension(conn, trace_context, dim)
        if field is not None:
            from coscreen.research_trace import capture_event, retire_field_lifecycle

            capture_event(
                conn, trace_context, stage="coding", action="field_definition_deleted",
                subject_id=field["field_id"], before=field, after=None, field=field,
            )
            retire_field_lifecycle(conn, trace_context, dim_id)
        paths = _cascade_delete_dimensions(
            conn, [int(dim_id)],
            trace_context=trace_context if field is not None else None,
            trace_fields={int(dim_id): field} if field is not None else None,
        )
        conn.execute("DELETE FROM coding_dimensions WHERE id = ?", (int(dim_id),))
        from coscreen.review_workflow import has_table
        if has_table(conn, 'workflow_coding_fields'):
            conn.execute('DELETE FROM workflow_coding_fields WHERE dimension_id=?', (dim_id,))
        if trace_context is not None:
            from coscreen.research_trace import finish_write_request

            finish_write_request(conn, trace_context)
    _remove_assets(paths)


# ---------------------------------------------------------------------------
# 方案 JSON（导出 / 导入）
# ---------------------------------------------------------------------------

def _scheme_entry(dim: dict) -> dict:
    """维度 -> 方案 JSON 条目（固定键序，不含 DB 本地 id/created_at）。"""
    return {
        "name": dim["name"],
        "dtype": dim["dtype"],
        "options": list(dim["options"]),
        "multi_select": bool(dim["multi_select"]),
        "unit": dim["unit"],
        "description": dim["description"],
        "section": dim["section"],
        "position": int(dim["position"]),
    }


def export_scheme(db: StrPath) -> dict:
    """导出编码方案：``{"version": 1, "dimensions": [...]}``（确定性）。

    - 维度顺序 = :func:`list_dimensions` 的 ``(section, position, id)`` 序，
      与共享给另一位编码员的方案 JSON 完全一致；
    - 条目键集**固定**为 name/dtype/options/multi_select/unit/description/
      section/position/field_id（不含本地 id/created_at）——``field_id`` 是稳定
      字段 UUID，导入端据此精确匹配；维度尚无 UUID 时该键取 ``null``（导入端
      按"无显式身份"处理并分配新 UUID），**但不省略**；
    - r9 F2-98：旧实现按 ``d.get("field_id")`` 有无合并该键，于是同一逻辑状态
      在"已跑 ensure_fields 与没跑过"两种库上导出 9 键 vs 8 键两套载荷，与
      文档宣称的固定键集矛盾。现在恒输出同一键集，"同一入库状态两次导出
      逐键相等"成立，按固定键集解析的消费方不会再遇到意外缺失键；
    - 落盘时建议 ``json.dumps(scheme, ensure_ascii=False, indent=2, sort_keys=True)``
      以获得字节级确定的文件。
    """
    from coscreen.coding_evidence import field_rows
    return {
        "version": SCHEME_VERSION,
        "dimensions": [{**_scheme_entry(d), "field_id": d.get("field_id") or None}
                       for d in field_rows(db)],
    }


def _validated_scheme_entries(data: Any) -> list[dict]:
    """校验方案 JSON 载荷，返回规整后的条目列表（全部通过才算通过）。"""
    if not isinstance(data, dict):
        raise ValueError(f"方案 JSON 必须为对象，实际为 {type(data).__name__}")
    version = data.get("version", SCHEME_VERSION)
    if str(version) != str(SCHEME_VERSION):
        raise ValueError(
            f"不支持的方案版本: {version!r}，本程序仅支持 {SCHEME_VERSION}"
        )
    raw_dims = data.get("dimensions")
    if not isinstance(raw_dims, list):
        raise ValueError(
            f"方案 JSON 缺少 dimensions 列表（实际为 {type(raw_dims).__name__}）"
        )
    entries: list[dict] = []
    seen_names: set[str] = set()
    for index, raw in enumerate(raw_dims):
        if not isinstance(raw, dict):
            raise ValueError(f"第 {index + 1} 个维度条目必须为对象")
        fields = _validate_dimension(
            raw.get("name", ""), raw.get("dtype", ""), raw.get("options", []),
            raw.get("multi_select", False), raw.get("unit", ""),
            raw.get("description", ""), raw.get("section", ""),
        )
        if fields["name"] in seen_names:
            raise ValueError(f"方案 JSON 中存在重名维度: {fields['name']!r}")
        seen_names.add(fields["name"])
        raw_position = raw.get("position", index)
        try:
            position = int(raw_position)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"维度 {fields['name']!r} 的 position 必须为整数: {raw_position!r}"
            ) from exc
        fields["position"] = position
        if raw.get('field_id') is not None:
            import uuid
            try:
                fields['field_id'] = str(uuid.UUID(str(raw['field_id'])))
            except (ValueError, TypeError) as exc:
                raise ValueError('field_id must be a UUID.') from exc
            if fields['field_id'] in {e.get('field_id') for e in entries}:
                raise ValueError('Scheme field IDs must be unique.')
        entries.append(fields)
    return entries


def _scheme_snapshot(conn: sqlite3.Connection) -> list[dict]:
    """import_scheme 的字段快照：在**调用方事务内**读，与落库共用同一快照。

    与 :func:`coscreen.coding_evidence.field_rows` 同形（维度公开字典 + 已有
    ``field_id``），但复用调用方连接，避免事务外陈旧快照（r9 F2-89）。
    """
    from coscreen.review_workflow import has_table

    fields: dict[int, str] = {}
    if has_table(conn, "workflow_coding_fields"):
        fields = {
            int(row[0]): row[1]
            for row in conn.execute("SELECT dimension_id, field_id FROM workflow_coding_fields")
        }
    rows = conn.execute(
        "SELECT id, name, dtype, options_json, multi_select, unit, description, section, "
        "position, created_at FROM coding_dimensions ORDER BY section, position, id"
    ).fetchall()
    snapshot = []
    for row in rows:
        dim = _row_to_dimension(row)
        if dim["id"] in fields:
            dim["field_id"] = fields[dim["id"]]
        snapshot.append(dim)
    return snapshot


def _required_field_ids(conn: sqlite3.Connection) -> set[str]:
    """事务内读取工作流必需字段 ID（与 review_workflow.settings 同口径）。"""
    from coscreen.review_workflow import has_table

    if not has_table(conn, "workflow_state"):
        return set()
    row = conn.execute(
        "SELECT evidence_json FROM workflow_state WHERE singleton=1"
    ).fetchone()
    if row is None:
        return set()
    try:
        values = json.loads(row[0] or "[]")
    except ValueError:  # 库内设置被外部改坏：按"无必需字段"处理，不阻断导入
        return set()
    return {str(value) for value in values} if isinstance(values, list) else set()


def import_scheme(
    db: StrPath,
    data: dict,
    replace: bool = True,
    *,
    trace_context: TraceContext | None = None,
) -> int:
    """Import atomically, retaining numeric IDs and research for exact matching fields.

    Explicit stable UUIDs travel with exports. Legacy entries only match exact
    unique semantic definitions. Replacement may remove unused fields, but
    refuses removal or automatic reinterpretation of saved research.
    """
    from coscreen import coding_evidence as evidence
    from coscreen.research_trace import begin_capture_transaction, begin_write_request, capture_event, finish_write_request
    import uuid
    entries = _validated_scheme_entries(data)
    evidence.migrate(db)
    with closing(_connect(db)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        # r9 F2-89：字段映射的全部决策（existing/required/同名冲突）必须在
        # 写事务**内**基于同一快照读取。旧实现在 BEGIN IMMEDIATE 之前各开
        # 一条只读连接取快照（coding.py:780-781 旧行号）：跨进程（uvicorn
        # --workers N，进程内 schema_guard 互斥不覆盖 worker 之间）并发
        # add_dimension/import_scheme 可使快照与落库之间出现同名字段，随后
        # 的 INSERT 撞 UNIQUE(name) 裸抛 IntegrityError → 端点只捕 ValueError
        # → 500。现改为事务内快照 + INSERT 的 IntegrityError 统一翻译为
        # ValueError（与 add_dimension/update_dimension 同文案口径）。
        existing = _scheme_snapshot(conn)
        required = _required_field_ids(conn)
        if trace_context is not None:
            begin_capture_transaction(conn, trace_context, 'coding')
            replay, result = begin_write_request(conn, trace_context, stage='coding', operation='coding_scheme_import',
                subject_id='scheme', payload={'replace': bool(replace), 'dimensions': entries})
            if replay:
                return int(result)
        retained = set()
        matches = []
        for entry in entries:
            explicit = [d for d in existing if entry.get('field_id') and d.get('field_id') == entry['field_id']]
            exact = [d for d in existing if evidence.definition(d) == evidence.definition(entry)]
            candidates = explicit or exact
            if len(candidates) > 1 or (candidates and evidence.definition(candidates[0]) != evidence.definition(entry)):
                raise ValueError('Scheme field mapping is ambiguous or its definition changed. Review the field before import.')
            old = candidates[0] if candidates else None
            if old:
                if not replace:
                    raise ValueError('Append import cannot replace an existing field.')
                retained.add(old['id'])
                if old.get('field_id') in required and entry.get('field_id', old.get('field_id')) != old.get('field_id'):
                    raise ValueError('A required field identity cannot be replaced by scheme import.')
            elif any(d['name'] == entry['name'] for d in existing):
                raise ValueError('A same-name field has a different definition; saved values cannot be remapped.')
            matches.append((entry, old))
        removed = [d for d in existing if replace and d['id'] not in retained]
        for old in removed:
            if conn.execute('SELECT 1 FROM coding_values WHERE dimension_id=? UNION ALL SELECT 1 FROM coding_notes WHERE dimension_id=? LIMIT 1', (old['id'], old['id'])).fetchone():
                raise ValueError('Scheme replacement would remove saved research. Preserve its fields.')
            # r9 F2-91：删除"必需但尚无数据"的字段旧实现直接放行——被删维度的
            # field_id 仍留在 workflow_evidence_requirements 与
            # settings()['evidence_required_field_ids']，mapping() 随即报
            # required_field_missing，complete_keys 逢 issues 返回空集，全任务
            # 证据被判未完成、coded 恒 0（且导入当时无任何提示）。与上方
            # "A required field identity cannot be replaced" 同一政策：移除
            # 必需字段必须先由属主在 workflow-settings 复核需求清单（该清空
            # /调整路径已存在），方案导入不得代为静默移除。
            if old.get('field_id', str(old['id'])) in required:
                raise ValueError(
                    'Scheme replacement would remove a required evidence field. '
                    'Review the task evidence requirements before importing this scheme.'
                )
        for old in removed:
            conn.execute('DELETE FROM coding_dimensions WHERE id=?', (old['id'],))
            conn.execute('DELETE FROM workflow_coding_fields WHERE dimension_id=?', (old['id'],))
            if trace_context:
                capture_event(conn, trace_context, stage='coding', action='field_definition_deleted', subject_id=old.get('field_id', str(old['id'])), before=old, after=None)
        offset = int(conn.execute('SELECT COALESCE(MAX(position),-1)+1 FROM coding_dimensions').fetchone()[0])
        for entry, old in matches:
            if old:
                did = old['id']
                conn.execute('UPDATE coding_dimensions SET description=?,section=?,position=? WHERE id=?',
                    (entry['description'], entry['section'], entry['position'], did))
            else:
                # Never recycle a deleted numeric dimension ID retained by Undo/mappings.
                ceiling = max([d['id'] for d in existing] + [int(r[0]) for r in conn.execute("SELECT seq FROM sqlite_sequence WHERE name='coding_dimensions'")] + [0])
                current = conn.execute('SELECT COALESCE(MAX(id),0) FROM coding_dimensions').fetchone()[0]
                did = max(ceiling, current) + 1
                try:
                    conn.execute('INSERT INTO coding_dimensions(id,name,dtype,options_json,multi_select,unit,description,section,position,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (did, entry['name'], entry['dtype'], _options_json(entry['options']), int(entry['multi_select']), entry['unit'], entry['description'], entry['section'], entry['position'] if replace else entry['position']+offset, _now()))
                except sqlite3.IntegrityError as exc:  # UNIQUE(name)/PRIMARY KEY 兜底
                    # r9 F2-89：与 add_dimension/update_dimension 同口径翻译为
                    # 中文业务 ValueError（端点只捕 ValueError，不再裸 500）。
                    if 'name' in str(exc):
                        raise ValueError(
                            f"维度名称已存在: {entry['name']!r}（请改用其他名称）"
                        ) from exc
                    raise ValueError(
                        'Scheme import conflicted with another field change. Reload the scheme and retry.'
                    ) from exc
            fid = entry.get('field_id') or (old or {}).get('field_id') or str(uuid.uuid4())
            try:
                conn.execute('INSERT INTO workflow_coding_fields VALUES(?,?,?) ON CONFLICT(dimension_id) DO UPDATE SET field_id=excluded.field_id,definition_json=excluded.definition_json',
                    (did, fid, evidence.definition(entry)))
            except sqlite3.IntegrityError as exc:  # UNIQUE(field_id) 兜底
                raise ValueError(
                    'Scheme field identity is already used by another field. Reload the scheme and retry.'
                ) from exc
            if trace_context:
                # r9 F2-92：field 实参先于 capture_event 求值，而 _trace_dimension
                # -> field_lifecycle 会无条件 INSERT/UPDATE trace_field_lifecycles
                # （research_trace.py:504 起自身无 is_recording 门控）。所以必须
                # 在传参前先判录制状态，与同模块其余调用点（add_dimension:351、
                # update_dimension:414、delete_dimension:653、save_coding_value:929）
                # 以及 begin_write_request 的 "Recording-off writes do not create
                # rows" 边界同口径。录制关闭时 capture_event 本就返回 None，故
                # 事件面逐位不变，只是不再产生无事件的 lifecycle 痕迹写。
                from coscreen.research_trace import is_recording

                if is_recording(conn, trace_context, 'coding'):
                    capture_event(conn, trace_context, stage='coding', action='field_definition_changed' if old else 'field_definition_created',
                        subject_id=fid, before=_scheme_entry(old) if old else None, after=_scheme_entry(entry), field=_trace_dimension(conn, trace_context, _require_dimension(conn, did)))
        if trace_context:
            finish_write_request(conn, trace_context, len(entries))
    return len(entries)


# ---------------------------------------------------------------------------
# 编码值
# ---------------------------------------------------------------------------

def _canonical_numeric_text(text: str) -> str:
    """把"通过 float() 校验但非 ASCII 常规写法"的数值规整为可交换文本（r9 F2-93）。

    Python 的 ``float()`` 接受下划线分组（``"1_000"``）与非 ASCII 数字
    （``"١٢٣"``、``"１２３"``）。旧实现用它判合法却把**原文**入库，导出
    （export.py 逐字输出）后 R/SPSS/Excel 读成文本或解析失败，且同值的多种
    文本表示（``"1e3"``/``"1000"``/``"1_000"``）互相不等。这里只做"去掉
    下划线分组 + 非 ASCII 十进制数字折成 ASCII 数字"，**保留用户书写的数值
    写法与精度**（不做 Decimal.normalize 之类的指数化重排）：
    - 已是纯 ASCII 且无下划线的文本逐字保留（既有库值、导出与值比较逐位
      不变；不新增对合法输入的拒绝）；
    - 折 ASCII 后若 ``float()`` 值与原文本不同（理论上不会发生），放弃改写
      并原样返回，宁可保留原文也不篡改数值。
    """
    if text.isascii() and "_" not in text:
        return text
    cleaned = text.replace("_", "")
    converted: list[str] = []
    for char in cleaned:
        if char.isascii():
            converted.append(char)
            continue
        try:
            digit = unicodedata.digit(char)
        except (TypeError, ValueError):
            return text  # 未知的非 ASCII 形态：不猜，保留原文
        converted.append(str(digit))
    candidate = "".join(converted)
    try:
        if float(candidate) != float(text):
            return text
    except (OverflowError, ValueError):
        return text
    return candidate


def _normalize_value(dim: dict, value: str) -> str:
    """按维度类型规整编码值（choice 校验 + 规范化；text/numeric 去首尾空白）。"""
    text = _clean_text(value)
    if dim["dtype"] == "numeric":
        if not text:
            return ""
        try:
            valid = math.isfinite(float(text))
        except (OverflowError, ValueError):
            valid = False
        if not valid:
            raise ValueError(f"维度「{dim['name']}」必须保存有限数值: {value!r}")
        return _canonical_numeric_text(text)
    if dim["dtype"] != "choice":
        return text
    if not text:
        return ""  # 空串 = 未填 / 清空
    parts = [p.strip() for p in text.split(VALUE_SEPARATOR)]
    parts = [p for p in parts if p]
    if not parts:
        return ""
    if not dim["multi_select"] and len(parts) > 1:
        raise ValueError(
            f"维度「{dim['name']}」为单选，不能保存多个选项: {value!r}"
        )
    unknown = [p for p in parts if p not in dim["options"]]
    if unknown:
        raise ValueError(
            f"维度「{dim['name']}」不含选项 {unknown}，合法选项为 {dim['options']}"
        )
    # 按选项顺序规范化 + 去重：多选值 "|" 连接顺序与界面点击顺序无关（确定性）
    picked = set(parts)
    return VALUE_SEPARATOR.join(o for o in dim["options"] if o in picked)


def save_coding_value(
    db: StrPath,
    dim_id: int,
    key: str,
    value: str,
    *,
    trace_context: TraceContext | None = None,
) -> None:
    """保存一条（维度 × 文献）编码值（upsert，updated_at 刷新）。

    - 维度不存在 -> 中文 ValueError；
    - ``choice``：值必须逐项 ∈ options（多选以 ``"|"`` 连接，逐项校验），
      单选维度给多个选项 -> ValueError；保存时按选项顺序规范化并去重；
      空串表示未填 / 清空；
    - ``text``：任意文本（含换行）原样保存，仅去首尾空白；
    - ``key`` 不在 articles 表时因外键约束抛 ``sqlite3.IntegrityError``。
    """
    with closing(_connect(db)) as conn, conn:
        if trace_context is not None:
            from coscreen.workspace_undo import begin_save, snapshot, record_save
            begin_save(conn)
            from coscreen.research_trace import begin_capture_transaction, begin_write_request

            begin_capture_transaction(conn, trace_context, "coding")
            replay, _ = begin_write_request(
                conn, trace_context, stage="coding", operation="coding_value_save",
                subject_id=key, payload={"dimension_id": int(dim_id), "value": value},
            )
            if replay:
                return None
            undo_before = snapshot(conn, "coding", key, [dim_id])
        dim = _require_dimension(conn, dim_id)
        stored = _normalize_value(dim, value)
        old = conn.execute(
            "SELECT value FROM coding_values WHERE dimension_id=? AND zotero_key=?",
            (int(dim_id), key),
        ).fetchone()
        before = {"value": old[0] or ""} if old else {"value": ""}
        conn.execute(
            "INSERT INTO coding_values (dimension_id, zotero_key, value, updated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(dimension_id, zotero_key) DO UPDATE SET "
            "value=excluded.value, updated_at=excluded.updated_at",
            (int(dim_id), key, stored, _now()),
        )
        if trace_context is not None:
            record_save(conn, trace_context, "coding", key, undo_before,
                        snapshot(conn, "coding", key, [dim_id]))
            from coscreen.research_trace import capture_event, is_recording

            if is_recording(conn, trace_context, "coding"):
                field = _trace_dimension(conn, trace_context, dim)
                action = (
                    "coding_value_cleared" if old and old[0] and not stored else
                    "coding_value_created" if old is None else "coding_value_changed"
                )
                capture_event(
                    conn, trace_context, stage="coding", action=action,
                    subject_id=key, before=before, after={"value": stored}, field=field,
                )
            from coscreen.research_trace import finish_write_request

            finish_write_request(conn, trace_context)


def save_coding_values_batch(
    db: StrPath, key: str, values: dict[int, str], *, trace_context: TraceContext,
) -> None:
    """Save a whole form atomically, with one Undo entry and existing field traces."""
    from coscreen.workspace_undo import begin_save, snapshot, record_save
    from coscreen.research_trace import (
        begin_capture_transaction, begin_write_request, capture_event,
        finish_write_request, is_recording,
    )
    with closing(_connect(db)) as conn, conn:
        begin_save(conn)
        begin_capture_transaction(conn, trace_context, "coding")
        replay, _ = begin_write_request(
            conn, trace_context, stage="coding", operation="coding_form_save", subject_id=key,
            payload={"values": {str(dim): value for dim, value in sorted(values.items())}},
        )
        if replay:
            return
        prepared = []
        for dim_id, value in sorted(values.items()):
            dim = _require_dimension(conn, dim_id)
            prepared.append((dim, _normalize_value(dim, value)))
        before = snapshot(conn, "coding", key, list(values))
        recording = is_recording(conn, trace_context, "coding")
        for dim, stored in prepared:
            dim_id = int(dim["id"])
            old = before["values"][str(dim_id)]
            conn.execute(
                "INSERT INTO coding_values(dimension_id,zotero_key,value,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(dimension_id,zotero_key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                (dim_id, key, stored, _now()),
            )
            if recording:
                action = ("coding_value_cleared" if old and old[0] and not stored else
                          "coding_value_created" if old is None else "coding_value_changed")
                capture_event(conn, trace_context, stage="coding", action=action, subject_id=key,
                              before={"value": old[0] or ""} if old else {"value": ""},
                              after={"value": stored}, field=_trace_dimension(conn, trace_context, dim))
        record_save(conn, trace_context, "coding", key, before,
                    snapshot(conn, "coding", key, list(values)))
        finish_write_request(conn, trace_context)


def get_coding_values(db: StrPath, key: str) -> dict[int, str]:
    """取一篇文献的全部编码值：``{dim_id: value}``（按 dim_id 升序）。"""
    with closing(_connect(db)) as conn:
        rows = conn.execute(
            "SELECT dimension_id, value FROM coding_values "
            "WHERE zotero_key = ? ORDER BY dimension_id",
            (key,),
        ).fetchall()
    return {int(r[0]): (r[1] or "") for r in rows}


def get_coding_values_all(db: StrPath) -> dict[str, dict[int, str]]:
    """全库编码值一次取回：``{zotero_key: {dim_id: value}}``（导出批量路径用，
    替代逐篇各开一条连接的 N+1 访问）。"""
    with closing(_connect(db)) as conn:
        rows = conn.execute(
            "SELECT zotero_key, dimension_id, value FROM coding_values "
            "ORDER BY zotero_key, dimension_id"
        ).fetchall()
    values: dict[str, dict[int, str]] = {}
    for key, dim_id, value in rows:
        values.setdefault(key, {})[int(dim_id)] = value or ""
    return values


# ---------------------------------------------------------------------------
# 备注（文字）与备注图片
# ---------------------------------------------------------------------------

def save_coding_note(
    db: StrPath,
    dim_id: int,
    key: str,
    text: str,
    *,
    trace_context: TraceContext | None = None,
) -> int:
    """保存一条（维度 × 文献）备注文字（upsert），返回 note_id。

    - 维度不存在 -> 中文 ValueError；
    - 同一 (dimension_id, zotero_key) 只有一条备注：重复保存更新原文，
      ``created_at`` 保留首次时间、``updated_at`` 每次刷新，返回同一个 note_id；
    - ``text`` 允许为空串（清空备注文字，行保留以便挂载图片）。
    """
    with closing(_connect(db)) as conn, conn:
        if trace_context is not None:
            from coscreen.research_trace import begin_capture_transaction, begin_write_request

            begin_capture_transaction(conn, trace_context, "coding")
            note_text = "" if text is None else str(text)
            replay, result = begin_write_request(
                conn, trace_context, stage="coding", operation="coding_note_save",
                subject_id=key, payload={"dimension_id": int(dim_id), "text": note_text},
            )
            if replay:
                return int(result)
        dim = _require_dimension(conn, dim_id)
        old = conn.execute(
            "SELECT text FROM coding_notes WHERE dimension_id=? AND zotero_key=?",
            (int(dim_id), key),
        ).fetchone()
        before = {"text": old[0] or ""} if old else {"text": ""}
        note_text = "" if text is None else str(text)
        now = _now()
        conn.execute(
            "INSERT INTO coding_notes "
            "(dimension_id, zotero_key, text, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(dimension_id, zotero_key) "
            "DO UPDATE SET text=excluded.text, updated_at=excluded.updated_at",
            (int(dim_id), key, note_text, now, now),
        )
        row = conn.execute(
            "SELECT id FROM coding_notes WHERE dimension_id = ? AND zotero_key = ?",
            (int(dim_id), key),
        ).fetchone()
        if trace_context is not None:
            from coscreen.research_trace import capture_event, is_recording

            if is_recording(conn, trace_context, "coding"):
                field = _trace_dimension(conn, trace_context, dim)
                capture_event(
                    conn, trace_context, stage="coding",
                    action="coding_note_created" if old is None else "coding_note_changed",
                    subject_id=key, before=before, after={"text": note_text}, field=field,
                )
        if trace_context is not None:
            from coscreen.research_trace import finish_write_request

            finish_write_request(conn, trace_context, int(row[0]))
        return int(row[0])


def get_coding_note(db: StrPath, dim_id: int, key: str) -> dict | None:
    """（追加式工具）取一条备注；不存在返回 None。

    返回 ``{"id", "dimension_id", "zotero_key", "text", "created_at",
    "updated_at"}``。
    """
    with closing(_connect(db)) as conn:
        row = conn.execute(
            "SELECT id, dimension_id, zotero_key, text, created_at, updated_at "
            "FROM coding_notes WHERE dimension_id = ? AND zotero_key = ?",
            (int(dim_id), key),
        ).fetchone()
    return dict(zip(_NOTE_COLUMNS, row)) if row else None


def list_coding_notes(db: StrPath) -> list[dict]:
    """（追加式工具）列出全部备注，按 ``(dimension_id, id)`` 升序（确定性）。

    供长表 / Markdown 导出按 (维度, 文献) 建索引使用。
    """
    with closing(_connect(db)) as conn:
        rows = conn.execute(
            "SELECT id, dimension_id, zotero_key, text, created_at, updated_at "
            "FROM coding_notes ORDER BY dimension_id, id"
        ).fetchall()
    return [dict(zip(_NOTE_COLUMNS, r)) for r in rows]


def coding_assets_dir(db: StrPath) -> Path:
    """Return the actor-private assets directory derived from the database identity."""
    from coscreen.coding_evidence import assets_dir
    return assets_dir(db)


def _detect_image_extension(data: bytes) -> str | None:
    """按魔数判定图片格式，返回落盘扩展名；非 PNG/JPEG/WebP 返回 None。"""
    raw = data or b""
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return IMAGE_EXTENSIONS["png"]
    if raw.startswith(b"\xff\xd8\xff"):
        return IMAGE_EXTENSIONS["jpeg"]
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return IMAGE_EXTENSIONS["webp"]
    return None


def add_note_image(
    db: StrPath,
    note_id: int,
    data: bytes,
    assets_dir: StrPath | None = None,
    *,
    trace_context: TraceContext | None = None,
) -> int:
    """给一条备注新增图片，返回其行 id。

    - 魔数校验：仅接受 PNG / JPEG / WebP，其余（含空数据）-> 中文 ValueError；
    - 字节写入 ``{assets_dir}/{note_id}_{sha256[:12]}.{ext}``，``assets_dir``
      缺省（None）时按 :func:`coding_assets_dir` 随 db 所在目录推导
      （``data/coding_assets/``，目录不存在自动创建）；
    - 文件名为内容寻址（sha256 前缀）——同一张图重复上传幂等覆盖同一文件；
    - 备注不存在 -> 中文 ValueError（先校验再落盘，避免产生孤儿文件）；
    - 新行 ``ocr_status`` 默认 ``"pending"``，识别结果由
      :func:`set_ocr_result` 回写。
    """
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError("图片数据不能为空")
    raw = bytes(data)
    from coscreen.coding_evidence import validate_image
    extension = validate_image(raw)
    digest = hashlib.sha256(raw).hexdigest()
    directory = Path(assets_dir) if assets_dir else coding_assets_dir(db)
    # 安全加固 §4：文件名 = note{id}_{sha 前缀}.{ext}（id 已 int 化、digest 为
    # 十六进制），理论上不可能越界；safe_path 作防御性回归护栏。
    path = safe_path(directory, f"note{int(note_id)}_{digest[:12]}.{extension}")
    created_file = False
    created_directory = not directory.exists()
    try:
        with closing(_connect(db)) as conn, conn:
            if trace_context is not None:
                from coscreen.research_trace import begin_capture_transaction, begin_write_request

                begin_capture_transaction(conn, trace_context, "coding")
                replay, result = begin_write_request(
                    conn, trace_context, stage="coding", operation="coding_note_image_add",
                    subject_id=str(int(note_id)), payload={"sha256": digest, "extension": extension},
                )
                if replay:
                    return int(result)
            note = conn.execute(
                "SELECT dimension_id,zotero_key FROM coding_notes WHERE id=?", (int(note_id),)
            ).fetchone()
            if note is None:
                raise ValueError(f"备注不存在: id={note_id}（请先保存备注再上传图片）")
            # Validate the note before creating its assets directory. This keeps
            # invalid IDs side-effect free while the actual lookup remains in the
            # same write transaction when trace capture is enabled.
            directory.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_bytes(raw)
                created_file = True
            cur = conn.execute(
                "INSERT INTO coding_note_images "
                "(note_id, path, sha256, ocr_text, ocr_status, created_at) "
                "VALUES (?, ?, ?, '', 'pending', ?)",
                (int(note_id), str(path), digest, _now()),
            )
            if trace_context is not None:
                from dataclasses import replace
                from coscreen.research_trace import capture_event, finish_write_request, is_recording

                if is_recording(conn, trace_context, "coding"):
                    dim = _require_dimension(conn, int(note[0]))
                    field = _trace_dimension(conn, trace_context, dim)
                    event_context = replace(trace_context, source_revision_id=f"coding_note_image:{cur.lastrowid}")
                    capture_event(
                        conn, event_context, stage="coding", action="coding_note_image_added",
                        subject_id=str(note[1]), before=None,
                        after={"image_id": int(cur.lastrowid), "sha256": digest, "ocr_status": "pending"},
                        field=field,
                    )
                finish_write_request(conn, trace_context, int(cur.lastrowid))
            return int(cur.lastrowid)
    except BaseException:
        if created_file:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        if created_directory:
            try:
                directory.rmdir()
            except OSError:
                pass
        raise


def list_note_images(db: StrPath, note_id: int | None = None) -> list[dict]:
    """列出备注图片（按 id 升序，确定性）。

    ``note_id`` 缺省（None）时列出全部图片（追加式扩展：长表/Markdown 导出
    可一次取回全库图片）；每项含全部数据库字段，另附 ``filename``（文件名）
    与 ``exists``（磁盘文件是否仍在）。
    """
    sql = "SELECT id, note_id, path, sha256, ocr_text, ocr_status, created_at FROM coding_note_images"
    params: tuple = ()
    if note_id is not None:
        sql += " WHERE note_id = ?"
        params = (int(note_id),)
    sql += " ORDER BY id"
    with closing(_connect(db)) as conn:
        rows = conn.execute(sql, params).fetchall()
    images: list[dict] = []
    for row in rows:
        record = dict(zip(_IMAGE_COLUMNS, row))
        record["id"] = int(record["id"])
        record["note_id"] = int(record["note_id"])
        record["ocr_text"] = record["ocr_text"] or ""
        record["filename"] = Path(record["path"]).name
        record["exists"] = Path(record["path"]).is_file()
        images.append(record)
    return images


def set_ocr_result(
    db: StrPath,
    image_id: int,
    ocr_text: str,
    status: str,
    *,
    trace_context: TraceContext | None = None,
) -> None:
    """回写一张图片的 OCR 结果（``ocr_text`` + ``ocr_status``）。

    - ``status`` 必须为 ``"done"`` / ``"failed"`` / ``"skipped"``，否则中文 ValueError；
    - 图片行不存在 -> 中文 ValueError；
    - 文本允许为空（``failed`` / ``skipped`` 时常为空串）。
    """
    clean_status = _clean_text(status)
    if clean_status not in OCR_STATUSES:
        raise ValueError(
            f"非法 OCR 状态: {status!r}，必须为 {OCR_STATUSES} 之一"
        )
    clean_text = "" if ocr_text is None else str(ocr_text)
    with closing(_connect(db)) as conn, conn:
        if trace_context is not None:
            from coscreen.research_trace import begin_capture_transaction, begin_write_request

            begin_capture_transaction(conn, trace_context, "coding")
            replay, _ = begin_write_request(
                conn, trace_context, stage="coding", operation="coding_image_ocr",
                subject_id=str(int(image_id)),
                payload={"ocr_text": clean_text, "ocr_status": clean_status},
            )
            if replay:
                return None
        row = conn.execute(
            "SELECT i.ocr_text,i.ocr_status,n.dimension_id,n.zotero_key "
            "FROM coding_note_images i JOIN coding_notes n ON n.id=i.note_id WHERE i.id = ?",
            (int(image_id),),
        ).fetchone()
        if row is None:
            raise ValueError(f"图片记录不存在: id={image_id}")
        conn.execute(
            "UPDATE coding_note_images SET ocr_text = ?, ocr_status = ? WHERE id = ?",
            (clean_text, clean_status, int(image_id)),
        )
        if trace_context is not None:
            from coscreen.research_trace import capture_event, finish_write_request, is_recording

            if is_recording(conn, trace_context, "coding"):
                field = _trace_dimension(
                    conn, trace_context, _require_dimension(conn, int(row[2])),
                )
                capture_event(
                    conn, trace_context, stage="coding", action="coding_image_ocr_changed",
                    subject_id=str(row[3]),
                    before={"image_id": int(image_id), "ocr_text": row[0] or "",
                            "ocr_status": row[1]},
                    after={"image_id": int(image_id), "ocr_text": clean_text,
                           "ocr_status": clean_status},
                    field=field,
                )
            finish_write_request(conn, trace_context)


# ---------------------------------------------------------------------------
# 队列与进度
# ---------------------------------------------------------------------------

def _include_keys(rows: Iterable[dict]) -> list[str]:
    """按传入顺序（已是 import_order 序）筛出 decision == "include" 的键。"""
    return [r["zotero_key"] for r in rows if r.get("decision") == "include"]


def coding_queue(db: StrPath, mode: str | None = None) -> list[str]:
    """构造编码队列（zotero_key 列表，按文献导入顺序、确定性）。

    - ``mode=None``（缺省）：字面量默认 ``"stage2"``（UI 设置键
      ``coding_queue_mode`` 尚未落地，后端无设置存储；落地后在此接入读取）；
    - ``mode="stage2"``：复筛 ``stage2_decisions`` 中 ``decision == "include"``
      的键；**为空时回退**初筛 ``decisions`` 的纳入键（复筛尚未开始时可直接编码）；
      回退时排除**已被复筛明确处置**的文献（``exclude`` 与 ``maybe`` 都排除，
      r9 F2-95：旧实现只排 ``maybe``，复筛刚起步、include 仍为 0 时被复筛排除
      的文献会经初筛纳入路径重回编码队列，与"有 include 时 exclude 正确缺席"
      的主路径自相矛盾）；
    - ``mode="stage1"``：初筛 ``decisions`` 的纳入键；
    - 其他显式 mode -> 中文 ValueError。
    """
    resolved = DEFAULT_CODING_QUEUE_MODE if mode is None else _clean_text(mode)
    if resolved not in CODING_QUEUE_MODES:
        raise ValueError(
            f"未知队列模式: {mode!r}，必须为 {list(CODING_QUEUE_MODES)} 之一"
        )
    if resolved == QUEUE_MODE_STAGE1:
        return _include_keys(list_decisions(db))
    stage2_rows = list_stage2_decisions(db)
    stage2_keys = _include_keys(stage2_rows)
    if stage2_keys:
        return stage2_keys
    stage2_decided = {row["zotero_key"] for row in stage2_rows}  # exclude / maybe
    return [key for key in _include_keys(list_decisions(db)) if key not in stage2_decided]


def coding_progress(db: StrPath, *, owner_db: StrPath | None = None) -> dict:
    """编码进度（相对当前队列与当前方案）：

    - ``total``：队列篇数（:func:`coding_queue`，含回退逻辑）；
    - ``coded``：全部维度都已填非空值的篇数（方案为空时为 0，避免"什么都没填
      也算完成"）；
    - ``remaining``：``total - coded``；
    - ``per_dimension``：``{dim_id: 该维度已填（非空值）的队列内篇数}``。
    """
    queue = coding_queue(db)
    dimensions = list_dimensions(db)
    dim_ids = [int(d["id"]) for d in dimensions]
    queue_set = set(queue)

    filled: dict[int, set[str]] = {}
    with closing(_connect(db)) as conn:
        rows = conn.execute(
            "SELECT dimension_id, zotero_key FROM coding_values "
            "WHERE TRIM(COALESCE(value, '')) <> ''"
        ).fetchall()
    for dim_id, key in rows:
        key = str(key)
        if key in queue_set:
            filled.setdefault(int(dim_id), set()).add(key)

    per_dimension = {dim_id: len(filled.get(dim_id, set())) for dim_id in dim_ids}
    from coscreen.coding_evidence import complete_keys, current_owner_db
    coded = len(complete_keys(db, owner_db or current_owner_db(db), queue))
    return {
        "total": len(queue),
        "coded": coded,
        "remaining": len(queue) - coded,
        "per_dimension": per_dimension,
    }
