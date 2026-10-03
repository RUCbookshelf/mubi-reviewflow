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
        f"SELECT {', '.join(_DIM_COLUMNS)} FROM coding_dimensions WHERE id = ?",
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
            f"SELECT {', '.join(_DIM_COLUMNS)} FROM coding_dimensions "
            "ORDER BY section, position, id"
        ).fetchall()
    return [_row_to_dimension(r) for r in rows]


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
            f"SELECT i.path FROM coding_note_images i JOIN coding_notes n "
            f"ON n.id = i.note_id WHERE n.dimension_id IN ({marks})",
            tuple(ids),
        ).fetchall()
    ]
    conn.execute(
        f"DELETE FROM coding_note_images WHERE note_id IN "
        f"(SELECT id FROM coding_notes WHERE dimension_id IN ({marks}))",
        tuple(ids),
    )
    conn.execute(
        f"DELETE FROM coding_notes WHERE dimension_id IN ({marks})", tuple(ids)
    )
    conn.execute(
        f"DELETE FROM coding_values WHERE dimension_id IN ({marks})", tuple(ids)
    )
    return [p for p in paths if p]


def _remove_assets(paths: Iterable[str]) -> None:
    """best-effort 删除磁盘图片（文件已丢失/无权限时静默跳过）。"""
    for raw in paths:
        try:
            Path(raw).unlink(missing_ok=True)
        except OSError:
            continue


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
    - 条目字段固定为 name/dtype/options/multi_select/unit/description/section/
      position（不含本地 id/created_at），因此同一入库状态两次导出逐键相等；
    - 落盘时建议 ``json.dumps(scheme, ensure_ascii=False, indent=2, sort_keys=True)``
      以获得字节级确定的文件。
    """
    return {
        "version": SCHEME_VERSION,
        "dimensions": [_scheme_entry(d) for d in list_dimensions(db)],
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
        entries.append(fields)
    return entries


def import_scheme(
    db: StrPath,
    data: dict,
    replace: bool = True,
    *,
    trace_context: TraceContext | None = None,
) -> int:
    """导入方案 JSON，返回导入的维度数。

    - 载荷校验（版本 / dimensions 列表 / 逐条目字段）**先全部完成**再落库：
      任一条目不合法则整个导入不生效（all-or-nothing），原方案不受影响；
    - ``replace=True``（默认）整体替换：原有维度连同其 values / notes / images
      一起级联清空（磁盘图片 best-effort 删除），position 按载荷原样还原
      （保证 "导出 -> 导入" 往返一致）；``replace=False`` 追加：重名维度
      -> ValueError，新维度 position 顺延到既有最大值之后（保持相对顺序）；
    - 方案版本仅支持 :data:`SCHEME_VERSION`（缺省视为 1）。
    """
    entries = _validated_scheme_entries(data)
    with closing(_connect(db)) as conn, conn:
        if trace_context is not None:
            from coscreen.research_trace import begin_capture_transaction, begin_write_request

            begin_capture_transaction(conn, trace_context, "coding")
            replay, result = begin_write_request(
                conn, trace_context, stage="coding", operation="coding_scheme_import",
                subject_id="scheme",
                payload={"replace": bool(replace), "dimensions": entries},
            )
            if replay:
                return int(result)
        removed_paths: list[str] = []
        trace_schema = False
        if trace_context is not None:
            from coscreen.research_trace import is_recording

            trace_schema = is_recording(conn, trace_context, "coding")
        if replace:
            existing = [int(r[0]) for r in conn.execute("SELECT id FROM coding_dimensions")]
            old_fields = {}
            if trace_schema:
                for old_id in existing:
                    old_fields[old_id] = _trace_dimension(conn, trace_context, _require_dimension(conn, old_id))
                from coscreen.research_trace import capture_event, retire_field_lifecycle

                for old_id, field in old_fields.items():
                    capture_event(
                        conn, trace_context, stage="coding", action="field_definition_deleted",
                        subject_id=field["field_id"], before=field, after=None, field=field,
                    )
                    retire_field_lifecycle(conn, trace_context, old_id)
            removed_paths = _cascade_delete_dimensions(
                conn, existing, trace_context=trace_context if trace_schema else None,
                trace_fields=old_fields if trace_schema else None,
            )
            conn.execute("DELETE FROM coding_dimensions")
            offset = 0
        else:
            for entry in entries:
                _ensure_name_free(conn, entry["name"])
            row = conn.execute(
                "SELECT COALESCE(MAX(position), -1) FROM coding_dimensions"
            ).fetchone()
            offset = int(row[0]) + 1
        for entry in entries:
            cur = conn.execute(
                "INSERT INTO coding_dimensions "
                "(name, dtype, options_json, multi_select, unit, description, "
                "section, position, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry["name"], entry["dtype"], _options_json(entry["options"]),
                    int(entry["multi_select"]), entry["unit"],
                    entry["description"], entry["section"],
                    entry["position"] + offset if not replace else entry["position"],
                    _now(),
                ),
            )
            if trace_schema:
                from coscreen.research_trace import capture_event

                field = _trace_dimension(conn, trace_context, _require_dimension(conn, int(cur.lastrowid)))
                capture_event(
                    conn, trace_context, stage="coding", action="field_definition_created",
                    subject_id=field["field_id"], before=None, after=field, field=field,
                )
        if trace_context is not None:
            from coscreen.research_trace import finish_write_request

            finish_write_request(conn, trace_context, len(entries))
    _remove_assets(removed_paths)
    return len(entries)


# ---------------------------------------------------------------------------
# 编码值
# ---------------------------------------------------------------------------

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
        return text
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
            from coscreen.research_trace import begin_capture_transaction, begin_write_request

            begin_capture_transaction(conn, trace_context, "coding")
            replay, _ = begin_write_request(
                conn, trace_context, stage="coding", operation="coding_value_save",
                subject_id=key, payload={"dimension_id": int(dim_id), "value": value},
            )
            if replay:
                return None
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
            f"SELECT {', '.join(_NOTE_COLUMNS)} FROM coding_notes "
            "WHERE dimension_id = ? AND zotero_key = ?",
            (int(dim_id), key),
        ).fetchone()
    return dict(zip(_NOTE_COLUMNS, row)) if row else None


def list_coding_notes(db: StrPath) -> list[dict]:
    """（追加式工具）列出全部备注，按 ``(dimension_id, id)`` 升序（确定性）。

    供长表 / Markdown 导出按 (维度, 文献) 建索引使用。
    """
    with closing(_connect(db)) as conn:
        rows = conn.execute(
            f"SELECT {', '.join(_NOTE_COLUMNS)} FROM coding_notes "
            "ORDER BY dimension_id, id"
        ).fetchall()
    return [dict(zip(_NOTE_COLUMNS, r)) for r in rows]


def coding_assets_dir(db: StrPath) -> Path:
    """（追加式工具）编码图片资产目录：``{db 所在目录}/coding_assets``。

    与 SPEC §15.1 的 ``data/coding_assets/`` 一致（db 在 ``data/`` 下时即
    得到 ``data/coding_assets``）；db 路径为内存库（``:memory:``）时退化为
    当前目录下的 ``coding_assets``。
    """
    db_file = Path(str(db))
    parent = db_file.parent if str(db_file.parent) not in ("", ".") else Path(".")
    return parent / CODING_ASSETS_DIRNAME


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
    extension = _detect_image_extension(raw)
    if extension is None:
        raise ValueError("不支持的图片格式（仅支持 PNG / JPEG / WebP）")
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
    sql = f"SELECT {', '.join(_IMAGE_COLUMNS)} FROM coding_note_images"
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
    stage2_keys = _include_keys(list_stage2_decisions(db))
    if stage2_keys:
        return stage2_keys
    return _include_keys(list_decisions(db))


def coding_progress(db: StrPath) -> dict:
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
    if dim_ids:
        coded = sum(
            1
            for key in queue
            if all(key in filled.get(dim_id, set()) for dim_id in dim_ids)
        )
    else:
        coded = 0
    return {
        "total": len(queue),
        "coded": coded,
        "remaining": len(queue) - coded,
        "per_dimension": per_dimension,
    }
