"""Bounded, versioned ZIP export/import helpers for personal research traces.

This module only packages trace snapshots. It never reads or writes screening or
coding decision tables. Snapshot acquisition and event import are handled by
``coscreen.research_trace`` in the caller's transaction.
"""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import re
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Any, Mapping


PACKAGE_SCHEMA = "reviewflow.research-trace/1"
MAX_PACKAGE_BYTES = 32 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
MAX_EVENTS = 50_000
MAX_EVENT_BYTES = 256 * 1024
MAX_MEMBERS = 13
MAX_COMPRESSION_RATIO = 2_000

PACKAGE_MEMBERS = frozenset({
    "manifest.json", "events.jsonl", "events.csv", "coverage.csv",
    "summary.csv", "activity.csv", "activity.svg", "activity.png",
    "report.html", "report.md", "readable-events.csv", "README.md",
    "data-dictionary.json",
})
_OPTIONAL_PACKAGE_MEMBERS = frozenset({"readable-events.csv"})
EVENT_COLUMNS = (
    "event_id", "store_id", "sequence", "task_id", "actor_id", "actor_alias",
    "stage", "action", "subject_id", "before", "after", "field", "source",
    "source_record_id", "source_revision_id", "batch_id", "request_id", "recorded_at",
    "schema_version", "app_version", "origin",
)

_PRIVATE_KEY = re.compile(
    r"(^|[_\-])(private|note|notes|quote|quotes|excerpt|excerpts|ocr|fulltext|full_text|token|password|"
    r"secret|url|uri|path|filepath|file_path|ip|email|metadata|actor|user|reviewer|"
    r"username|identity|owner|alias|author|task|task_id|store|store_id|request|request_id|"
    r"source_record_id|source_revision_id|batch_id|source_metadata)([_\-]|$)", re.I
)
_URL = re.compile(r"\b(?:https?|ftp)://[^\s<>\"']+", re.I)
_WINDOWS_PATH = re.compile(r"\b[A-Za-z]:\\[^\s<>\"']+")
_UNC_PATH = re.compile(r"(?<!\S)(?:\\\\|//)[^\\/\s<>\"']+(?:[\\/][^\\/\s<>\"']+)+")
_ABSOLUTE_PATH = re.compile(
    r"(?<![\w:])/(?:[^/\s<>\"']+/)+[^/\s<>\"']+|"
    r"(?<![\w:])/[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,12}(?=[\s<>\"']|$)"
)
_RELATIVE_PATH = re.compile(
    r"(?<![\w@])(?:\.{1,2}/|~/|~[A-Za-z0-9._-]+/|"
    r"(?:[A-Za-z0-9._-]+/)+[A-Za-z0-9._-]+\.[A-Za-z0-9]{1,12})[^\s<>\"']*"
)
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.I)
_SECRET_ASSIGNMENT = re.compile(
    r"\b(?:api[_-]?key|access[_-]?token|secret[_-]?key|authorization)\s*[:=]\s*"
    r"(?:Bearer\s+)?[^\s,;]+",
    re.I,
)
_SECRET_TOKEN = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}", re.I)
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_FORMULA = re.compile(r"^[\s\u0000-\u001f]*[=+@-]")


class TracePackageError(ValueError):
    """Invalid, unsupported, or unsafe trace package."""


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def _json_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(value)


def _text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = _SECRET_ASSIGNMENT.sub("[redacted secret]", text)
    text = _BEARER.sub("[redacted credential]", text)
    text = _SECRET_TOKEN.sub("[redacted secret]", text)
    text = _URL.sub("[redacted URL]", text)
    text = re.sub(r"\bfile:(?://)?[^\s<>\"']+", "[redacted URL]", text, flags=re.I)
    text = _WINDOWS_PATH.sub("[redacted path]", text)
    text = _UNC_PATH.sub("[redacted path]", text)
    text = _ABSOLUTE_PATH.sub("[redacted path]", text)
    text = _RELATIVE_PATH.sub("[redacted path]", text)
    return _EMAIL.sub("[redacted email]", text)


def _redact(value: Any, *, key: str = "", include_private_fields: frozenset[str] = frozenset()) -> Any:
    """Remove private fields recursively and scrub paths/URLs from public values."""
    normalized_key = key.casefold().replace("-", "_")
    opted_note = "notes" in include_private_fields and normalized_key in {
        "note", "notes", "private_note", "private_notes",
    }
    opted_quote = "source_quotes" in include_private_fields and normalized_key in {
        "quote", "quotes", "source_quote", "source_quotes", "source_excerpt", "source_excerpts", "excerpt", "excerpts", "ocr_text",
    }
    protected = bool(_PRIVATE_KEY.search(key))
    # Explicitly chosen private notes/quotes may be included, while path/URL/token,
    # identity and metadata keys remain protected even inside an opted-in value.
    dangerous = bool(re.search(
        r"(^|[_\-])(url|uri|path|filepath|file_path|token|password|secret|metadata|actor|user|reviewer|"
        r"username|identity|owner|task|store|request|request_id|source_record_id|source_revision_id|batch_id)([_\-]|$)",
        key, re.I,
    ))
    if key and protected and not ((opted_note or opted_quote) and not dangerous):
        return _REDACT
    if isinstance(value, Mapping):
        note_event = bool(re.search(
            r"(?:^|_)coding_note_(?:created|changed|cleared)$|(?:^|_)baseline_coding_note$",
            str(value.get("action", "")),
        ))
        cleaned = {}
        for child_key, child in value.items():
            name = str(child_key)
            if note_event and name in {"before", "after"} and isinstance(child, Mapping) and "text" in child:
                child = dict(child)
                note_text = _redact(child.pop("text"), key="notes", include_private_fields=include_private_fields)
                if note_text is not _REDACT:
                    child["text"] = note_text
            safe = _redact(child, key=name, include_private_fields=include_private_fields)
            if safe is not _REDACT:
                cleaned[name] = safe
        return cleaned
    if isinstance(value, (list, tuple)):
        return [safe for item in value if (safe := _redact(item, include_private_fields=include_private_fields)) is not _REDACT]
    if isinstance(value, str):
        return _text(value)
    return value


class _Redacted:
    pass


_REDACT = _Redacted()


def _csv_safe(value: Any) -> str:
    text = _json_cell(value)
    return "'" + text if _FORMULA.match(text) else text


def _csv_bytes(headers: tuple[str, ...], rows: list[Mapping[str, Any]]) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_csv_safe(row.get(name)) for name in headers])
    return out.getvalue().encode("utf-8")


def _event_rows(snapshot: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Flatten the documented multi-store snapshot shape into events and partitions."""
    stores = snapshot.get("stores")
    if not isinstance(stores, list):
        stores = [{
            "partition": "personal", "store_id": snapshot.get("store_id"),
            "settings": snapshot.get("settings", {}), "summary": snapshot.get("summary", {}),
            "cutoff_sequence": snapshot.get("cutoff_sequence"),
            "events": snapshot.get("events", []),
        }]
    events: list[dict[str, Any]] = []
    partitions: list[dict[str, Any]] = []
    for store in stores:
        if not isinstance(store, Mapping):
            raise TracePackageError("Snapshot store entry must be an object.")
        rows = store.get("events", [])
        if not isinstance(rows, list):
            raise TracePackageError("Snapshot events must be a list.")
        summary = store.get("summary") or {}
        settings = store.get("settings") or {}
        if not isinstance(summary, Mapping) or not isinstance(settings, Mapping):
            raise TracePackageError("Snapshot settings and summary must be objects.")
        partitions.append({
            "partition": str(store.get("partition", "personal")),
            "store_id": store.get("store_id"),
            "status": settings.get("status", summary.get("status", "off")),
            "stages": settings.get("stages", summary.get("stages", [])),
            "cutoff_sequence": store.get("cutoff_sequence", summary.get("last_sequence")),
            "event_count": len(rows),
            "history_event_count": summary.get("history_event_count", summary.get("event_count", len(rows))),
            "first_sequence": summary.get("first_sequence"),
            "last_sequence": summary.get("last_sequence"),
            "first_at": summary.get("first_at"),
            "last_at": summary.get("last_at"),
            "selected_first_at": summary.get("selected_first_at"),
            "selected_last_at": summary.get("selected_last_at"),
            "pause_count": int(summary.get("pause_count", 0)),
            "history_clear_count": int(summary.get("history_clear_count", 0)),
            "coverage_start_at": summary.get("coverage_start_at"),
            "coverage_start_ambiguous": bool(summary.get("coverage_start_ambiguous", False)),
            "recent_controls": summary.get("recent_controls", []),
            "recent_controls_truncated": bool(summary.get("recent_controls_truncated", False)),
        })
        events.extend(rows)
    if len(events) > MAX_EVENTS:
        raise TracePackageError(f"A trace package may contain at most {MAX_EVENTS} events.")
    return events, partitions


def _event_dict(event: Mapping[str, Any]) -> dict[str, Any]:
    """Convert a normalized DB event to the stable package event shape."""
    normalized = dict(event)
    # The store reader returns decoded before/after JSON. Older readers may expose
    # their encoded form; keep the normalized package field names either way.
    allowed = set(EVENT_COLUMNS)
    result = {key: normalized.get(key) for key in EVENT_COLUMNS if key in normalized}
    if isinstance(normalized.get("subject_title"), str):
        result["subject_title"] = normalized["subject_title"]
    for required in ("event_id", "store_id", "sequence", "task_id", "actor_id",
                     "stage", "action", "subject_id", "recorded_at"):
        if required not in result:
            raise TracePackageError(f"Snapshot event is missing {required}.")
    return result


def _public_snapshot(events: list[dict[str, Any]], partitions: list[dict[str, Any]],
                     snapshot: Mapping[str, Any], author_alias: str,
                     include_private_fields: frozenset[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    alias = _text(author_alias.strip()) or "Reviewer"
    store_aliases = {str(row.get("store_id")): f"Store {i + 1}"
                     for i, row in enumerate(partitions)}
    for index, row in enumerate(partitions):
        row["store_id"] = store_aliases.get(str(row.get("store_id")), "Store")
        row["partition"] = f"Partition {index + 1}"
    output = []
    for raw in events:
        event = _redact(raw, include_private_fields=include_private_fields)
        if not isinstance(event, dict):
            continue
        event.pop("task_id", None)
        event["actor_alias"] = alias
        event.pop("actor_id", None)
        event["store_id"] = store_aliases.get(str(raw.get("store_id")), "Store")
        output.append(event)
    public_origin = {
        "task_alias": "Review task",
        "author_alias": alias,
    }
    return output, partitions, public_origin


def _coverage_rows(partitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    columns = (
        "partition", "store_id", "status", "stages", "cutoff_sequence", "event_count",
        "history_event_count", "first_sequence", "last_sequence", "first_at", "last_at",
        "selected_first_at", "selected_last_at", "pause_count", "history_clear_count",
        "coverage_start_at", "coverage_start_ambiguous", "recent_controls",
        "recent_controls_truncated",
    )
    return [{key: _json_cell(row.get(key)) for key in columns} for row in partitions]


def _summary_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_stage: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        by_stage.setdefault(str(event.get("stage", "unknown")), []).append(event)
    rows = [{"stage": "all", "metric": "event_count", "value": len(events)},
            {"stage": "all", "metric": "unique_subject_count",
             "value": len({str(e.get("subject_id")) for e in events})}]
    for stage, stage_events in sorted(by_stage.items()):
        rows.extend((
            {"stage": stage, "metric": "event_count", "value": len(stage_events)},
            {"stage": stage, "metric": "unique_subject_count",
             "value": len({str(e.get("subject_id")) for e in stage_events})},
        ))
    return rows


_ACTIVITY_STAGES = ("screen", "fulltext", "coding")
_ACTIVITY_COLORS = {"screen": "#3569b0", "fulltext": "#db8b26", "coding": "#32936f"}
_ACTIVITY_EXCLUDED = ("baseline_", "recording_", "field_", "imported_baseline_", "imported_field_")


def research_operations(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return saved research operations, excluding control and baseline snapshots."""
    operations = [event for event in events if event.get("stage") in _ACTIVITY_STAGES
                  and not str(event.get("action", "")).startswith(("baseline_", "imported_baseline_"))]
    return sorted(operations, key=lambda event: (str(event.get("recorded_at", "")), event.get("sequence", 0)), reverse=True)


def _activity_data(events: list[dict[str, Any]]) -> tuple[list[str], dict[str, dict[str, int]]]:
    values: dict[str, dict[str, int]] = {}
    for event in events:
        stage, action = str(event.get("stage", "")), str(event.get("action", ""))
        if stage not in _ACTIVITY_STAGES or action.startswith(_ACTIVITY_EXCLUDED):
            continue
        day = str(event.get("recorded_at", ""))[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            continue
        values.setdefault(day, {name: 0 for name in _ACTIVITY_STAGES})[stage] += 1
    days = sorted(values)
    return days, values


def _activity_csv(days: list[str], values: dict[str, dict[str, int]]) -> bytes:
    rows = [{"date": day, "stage": stage, "event_count": values[day][stage]}
            for day in days for stage in _ACTIVITY_STAGES]
    return _csv_bytes(("date", "stage", "event_count"), rows)


_ACTIVITY_LABELS = {
    "en": ("Research trace activity by day and stage", "Stacked daily event counts. Full resolution rows are available in activity.csv.", "Research trace changes by day", "Showing latest 120 active dates; see activity.csv for all dates.", "No recorded research changes in this selection.", {"screen": "Screening", "fulltext": "Full-text", "coding": "Coding"}),
    "zh-CN": ("研究轨迹按日期和阶段汇总", "按日堆叠的事件数；完整日期数据见 activity.csv。", "每日研究轨迹变更", "显示最近 120 个有活动日期；完整数据见 activity.csv。", "此筛选范围内没有已记录的研究变更。", {"screen": "初筛", "fulltext": "全文筛选", "coding": "编码"}),
    "fr": ("Activité du suivi de recherche par jour et par étape", "Nombre quotidien d’événements empilés ; lignes complètes dans activity.csv.", "Modifications du suivi de recherche par jour", "120 derniers jours actifs affichés ; toutes les dates dans activity.csv.", "Aucune modification enregistrée dans cette sélection.", {"screen": "Sélection", "fulltext": "Texte intégral", "coding": "Codage"}),
    "ru": ("Активность трассировки по дням и этапам", "Ежедневные числа событий, сгруппированные по этапам; все даты и этапы есть в activity.csv.", "Изменения трассировки по дням", "Показаны последние 120 активных дат; все даты в activity.csv.", "В выбранных данных нет записанных изменений.", {"screen": "Отбор", "fulltext": "Полный текст", "coding": "Кодирование"}),
    "es": ("Actividad del seguimiento de la investigación por día y etapa", "Recuentos diarios apilados; todas las fechas están en activity.csv.", "Cambios en el seguimiento de la investigación por día", "Se muestran los últimos 120 días activos; todas las fechas en activity.csv.", "No hay cambios registrados en esta selección.", {"screen": "Selección", "fulltext": "Texto completo", "coding": "Codificación"}),
    "ja": ("日付・段階別の研究トレース活動", "日ごとのイベント数。全日付のデータは activity.csv にあります。", "日ごとの研究トレース変更", "直近120活動日を表示。全日付は activity.csv を参照。", "この選択範囲に記録された変更はありません。", {"screen": "一次選別", "fulltext": "全文選別", "coding": "コーディング"}),
    "pt": ("Atividade de acompanhamento da pesquisa por dia e etapa", "Contagens diárias empilhadas de eventos; todas as datas estão em activity.csv.", "Alterações no acompanhamento da pesquisa por dia", "Últimos 120 dias ativos; todas as datas em activity.csv.", "Nenhuma alteração registrada nesta seleção.", {"screen": "Triagem", "fulltext": "Texto completo", "coding": "Codificação"}),
    "de": ("Trace-Aktivität nach Tag und Phase", "Gestapelte tägliche Ereigniszahlen; alle Tages- und Phasendaten stehen in activity.csv.", "Trace-Änderungen pro Tag", "Letzte 120 aktiven Tage; alle Daten in activity.csv.", "Keine aufgezeichneten Änderungen in dieser Auswahl.", {"screen": "Screening", "fulltext": "Volltext", "coding": "Kodierung"}),
    "sr": ("Активност трага по дану и фази", "Дневни број догађаја; сви датуми су у activity.csv.", "Промене трага по дану", "Приказано је последњих 120 активних датума; сви датуми су у activity.csv.", "Нема забележених промена у овом избору.", {"screen": "Селекција", "fulltext": "Пун текст", "coding": "Кодирање"}),
    "ko": ("날짜 및 단계별 연구 추적 활동", "일별 이벤트 수이며 전체 날짜는 activity.csv에 있습니다.", "일별 연구 추적 변경", "최근 활동 날짜 120개 표시; 전체 날짜는 activity.csv 참조.", "이 선택 항목에 기록된 변경이 없습니다.", {"screen": "선별", "fulltext": "전문", "coding": "코딩"}),
}

_REPORT_GUIDANCE = {
    "en": {
        "open_heading": "Open and use this report",
        "open": "Extract the ZIP file, then open report.html in a web browser. This report works offline.",
        "submission": "Use the method draft as a starting point for your Methods section or supplement. Check it against your protocol and complete every item marked as missing. This trace format is not required by PRISMA.",
        "statistics": "An event is a saved change. Daily counts and pause boundaries do not measure reading time or time spent on a study.",
        "boundaries": "Recording captures changes only while it is enabled for the listed stages. Pauses and disabled periods are gaps; clearing history removes earlier records. A blank current-start time means recording is not active. A pause count gives no pause duration.",
        "fields_help": "Each row shows when a saved change was recorded, its research stage and type, the affected item and coding field, and the saved values before and after the change. Only the first 200 selected events are shown here; events.csv contains the complete selection.",
        "field": "Coding field", "before": "Previous saved values", "after": "New saved values",
        "no_previous": "No earlier value in this record", "no_value": "No value recorded",
        "all_events": "All recorded events", "status_now": "Status at export",
        "selection_fields": {"stage": "Stage", "action": "Action", "subject_id": "Item", "since": "From", "until": "To"},
        "field_names": {"decision": "Decision", "exclusion_reason": "Exclusion reason", "reason": "Reason", "notes": "Notes", "tags": "Tags", "screened_at": "Saved at", "value": "Coding value", "text": "Text", "rationale": "Rationale", "source": "Source", "version": "Revision", "supersedes_id": "Replaces revision", "protocol_version": "Protocol version", "status": "Recording status", "stages": "Enabled stages", "event_count": "Event count", "options": "Options", "type": "Field type", "name": "Field name", "description": "Description", "section": "Section", "unit": "Unit", "multi_select": "Multiple choices"},
        "decision_values": {"include": "Include", "exclude": "Exclude", "maybe": "Maybe"},
        "action_nouns": {"decision": "Screening decision", "fulltext": "Full-text file", "coding_value": "Coding value", "coding_note": "Coding note", "coding_note_image": "Coding note image", "coding_image_ocr": "Recognized image text", "field_definition": "Coding field", "rater_record": "Reviewer coding record"},
        "verbs": {"created": "created", "changed": "changed", "imported": "imported", "added": "added", "replaced": "replaced", "cleared": "cleared", "removed": "removed", "deleted": "deleted"},
        "baseline": "State recorded when tracing started", "baseline_nouns": {"decision": "screening decision", "item": "literature record", "field": "coding field", "value": "coding value", "coding_note": "coding note", "coding_image": "coding image", "rater_revision": "reviewer revision", "protocol": "protocol"},
        "controls": {"recording_started": "Recording started", "recording_resumed": "Recording resumed", "recording_paused": "Recording paused", "recording_stopped": "Recording stopped", "stages_changed": "Enabled stages changed", "history_cleared": "Trace history cleared"},
        "control_sources": {"manual": "Manual change", "import": "Imported change", "baseline": "Starting snapshot", "control": "Recording control"},
        "privacy_fields": {"notes": "Notes", "source_quotes": "Source quotations"},
    },
    "zh-CN": {
        "open_heading": "打开与使用本报告",
        "open": "先解压 ZIP 文件，再用浏览器打开 report.html；这份报告可离线查看。",
        "submission": "可将方法报告草稿作为论文“方法”部分或补充材料的起点。请对照研究方案核实，并补全所有标为“未提供”的内容。PRISMA 不要求使用这种轨迹格式。",
        "statistics": "一条事件表示一次已保存的变更。每日次数和暂停边界不代表阅读时长或单篇文献耗时。",
        "boundaries": "只有在轨迹已启用且所列阶段处于启用状态时，变更才会被记录。暂停或关闭期间属于记录空档；清除历史会移除较早记录。当前记录起点为空表示导出时没有正在记录。暂停次数不包含暂停时长。",
        "fields_help": "每行显示一次已保存变更的时间、研究阶段和操作类型、受影响的文献与编码字段，以及变更前后的已保存值。此处只显示所选事件的前 200 条；完整筛选结果见 events.csv。",
        "field": "编码字段", "before": "变更前的已保存值", "after": "变更后的已保存值",
        "no_previous": "此记录中没有更早的值", "no_value": "没有记录值",
        "all_events": "全部已记录事件", "status_now": "导出时状态",
        "selection_fields": {"stage": "阶段", "action": "操作", "subject_id": "文献", "since": "起始时间", "until": "结束时间"},
        "field_names": {"decision": "筛选结论", "exclusion_reason": "排除理由", "reason": "理由", "notes": "备注", "tags": "标签", "screened_at": "保存时间", "value": "编码值", "text": "文本", "rationale": "判定依据", "source": "来源", "version": "修订版本", "supersedes_id": "替代的版本", "protocol_version": "方案版本", "status": "记录状态", "stages": "已启用阶段", "event_count": "事件数", "options": "选项", "type": "字段类型", "name": "字段名称", "description": "说明", "section": "分组", "unit": "单位", "multi_select": "允许多选"},
        "decision_values": {"include": "纳入", "exclude": "排除", "maybe": "待定"},
        "action_nouns": {"decision": "筛选结论", "fulltext": "全文材料", "coding_value": "编码值", "coding_note": "编码备注", "coding_note_image": "编码备注图片", "coding_image_ocr": "图片识别文字", "field_definition": "编码字段", "rater_record": "评审者编码记录"},
        "verbs": {"created": "新增", "changed": "修改", "imported": "导入", "added": "添加", "replaced": "替换", "cleared": "清空", "removed": "移除", "deleted": "删除"},
        "baseline": "记录启用时的已有状态", "baseline_nouns": {"decision": "筛选结论", "item": "文献记录", "field": "编码字段", "value": "编码值", "coding_note": "编码备注", "coding_image": "编码图片", "rater_revision": "评审者修订", "protocol": "研究方案"},
        "controls": {"recording_started": "开始记录", "recording_resumed": "恢复记录", "recording_paused": "暂停记录", "recording_stopped": "停止记录", "stages_changed": "变更已启用阶段", "history_cleared": "清除轨迹历史"},
        "control_sources": {"manual": "手动修改", "import": "导入的修改", "baseline": "启用时快照", "control": "记录控制"},
        "privacy_fields": {"notes": "备注", "source_quotes": "来源引文"},
    },
    "fr": {
        "open_heading": "Ouvrir et utiliser ce rapport",
        "open": "Extrayez le fichier ZIP, puis ouvrez report.html dans un navigateur. Le rapport fonctionne hors ligne.",
        "submission": "Utilisez le brouillon de la section Méthodes comme point de départ pour votre manuscrit ou un supplément. Vérifiez-le au regard de votre protocole et complétez les éléments manquants. PRISMA n’exige pas ce format de suivi.",
        "statistics": "Un événement correspond à une modification enregistrée. Les nombres quotidiens et les pauses ne mesurent pas le temps de lecture ni le temps passé sur une étude.",
        "boundaries": "Les modifications ne sont enregistrées que lorsque le suivi est activé pour les étapes indiquées. Les pauses et les périodes désactivées créent des lacunes ; l’effacement supprime les anciennes entrées. L’absence d’heure de début pour l’enregistrement en cours signifie que celui-ci n’est pas actif. Le nombre de pauses n’indique pas leur durée.",
        "fields_help": "Chaque ligne indique l’heure d’une modification enregistrée, son étape et son type, l’élément et le champ de codage concernés, puis les valeurs enregistrées avant et après. Seuls les 200 premiers événements sélectionnés sont affichés ; events.csv contient toute la sélection.",
        "field": "Champ de codage", "before": "Valeurs enregistrées avant", "after": "Valeurs enregistrées après",
        "no_previous": "Aucune valeur antérieure dans cet enregistrement", "no_value": "Aucune valeur enregistrée",
        "all_events": "Tous les événements enregistrés", "status_now": "État à l’export",
        "selection_fields": {"stage": "Étape", "action": "Action", "subject_id": "Document", "since": "Depuis", "until": "Jusqu’à"},
        "field_names": {"decision": "Décision", "exclusion_reason": "Motif d’exclusion", "reason": "Motif", "notes": "Notes", "tags": "Étiquettes", "screened_at": "Enregistré le", "value": "Valeur codée", "text": "Texte", "rationale": "Justification", "source": "Source", "version": "Révision", "supersedes_id": "Remplace la révision", "protocol_version": "Version du protocole", "status": "État de l’enregistrement", "stages": "Étapes activées", "event_count": "Nombre d’événements", "options": "Options", "type": "Type de champ", "name": "Nom du champ", "description": "Description", "section": "Section", "unit": "Unité", "multi_select": "Choix multiples"},
        "decision_values": {"include": "Inclure", "exclude": "Exclure", "maybe": "À déterminer"},
        "action_nouns": {"decision": "Décision de sélection", "fulltext": "Document en texte intégral", "coding_value": "Valeur de codage", "coding_note": "Note de codage", "coding_note_image": "Image de note de codage", "coding_image_ocr": "Texte reconnu dans l’image", "field_definition": "Champ de codage", "rater_record": "Codage de l’évaluateur"},
        "verbs": {"created": "création", "changed": "modification", "imported": "importation", "added": "ajout", "replaced": "remplacement", "cleared": "effacement", "removed": "retrait", "deleted": "suppression"},
        "baseline": "État existant au début du suivi", "baseline_nouns": {"decision": "décision de sélection", "item": "notice bibliographique", "field": "champ de codage", "value": "valeur de codage", "coding_note": "note de codage", "coding_image": "image de codage", "rater_revision": "révision de l’évaluateur", "protocol": "protocole"},
        "controls": {"recording_started": "Suivi démarré", "recording_resumed": "Suivi repris", "recording_paused": "Suivi mis en pause", "recording_stopped": "Suivi arrêté", "stages_changed": "Étapes suivies modifiées", "history_cleared": "Historique du suivi effacé"},
        "control_sources": {"manual": "Modification manuelle", "import": "Modification importée", "baseline": "État initial", "control": "Contrôle du suivi"},
        "privacy_fields": {"notes": "Notes", "source_quotes": "Citations de source"},
    },
    "ru": {
        "open_heading": "Как открыть и использовать отчёт",
        "open": "Распакуйте ZIP-файл и откройте report.html в браузере. Отчёт работает без подключения к сети.",
        "submission": "Используйте черновик описания методов как основу для раздела «Методы» или приложения. Сверьте его с протоколом и заполните все пункты без данных. PRISMA не требует использования этого формата трассировки.",
        "statistics": "Событие — это сохранённое изменение. Суточные числа и границы пауз не измеряют время чтения или работы с исследованием.",
        "boundaries": "Изменения записываются, только когда трассировка включена для указанных этапов. Паузы и выключенные периоды создают пробелы; очистка удаляет прежние записи. Пустое начало текущей записи означает, что запись сейчас не ведётся. Число пауз не показывает их длительность.",
        "fields_help": "В строке указаны время сохранённого изменения, этап и его тип, затронутая публикация и поле кодирования, а также сохранённые значения до и после. Здесь показаны первые 200 выбранных событий; полный набор находится в events.csv.",
        "field": "Поле кодирования", "before": "Сохранённые значения до", "after": "Сохранённые значения после",
        "no_previous": "В этой записи нет предыдущего значения", "no_value": "Значение не записано",
        "all_events": "Все записанные события", "status_now": "Статус на момент экспорта",
        "selection_fields": {"stage": "Этап", "action": "Действие", "subject_id": "Публикация", "since": "С", "until": "По"},
        "field_names": {"decision": "Решение", "exclusion_reason": "Причина исключения", "reason": "Причина", "notes": "Заметки", "tags": "Метки", "screened_at": "Сохранено", "value": "Значение кодирования", "text": "Текст", "rationale": "Обоснование", "source": "Источник", "version": "Версия", "supersedes_id": "Заменяет версию", "protocol_version": "Версия протокола", "status": "Статус записи", "stages": "Включённые этапы", "event_count": "Число событий", "options": "Варианты", "type": "Тип поля", "name": "Название поля", "description": "Описание", "section": "Раздел", "unit": "Единица", "multi_select": "Несколько вариантов"},
        "decision_values": {"include": "Включить", "exclude": "Исключить", "maybe": "Возможно"},
        "action_nouns": {"decision": "Решение отбора", "fulltext": "Полный текст", "coding_value": "Значение кодирования", "coding_note": "Заметка кодирования", "coding_note_image": "Изображение к заметке", "coding_image_ocr": "Распознанный текст изображения", "field_definition": "Поле кодирования", "rater_record": "Запись кодирования рецензента"},
        "verbs": {"created": "создание", "changed": "изменение", "imported": "импорт", "added": "добавление", "replaced": "замена", "cleared": "очистка", "removed": "удаление", "deleted": "удаление"},
        "baseline": "Состояние на момент начала записи", "baseline_nouns": {"decision": "решение отбора", "item": "библиографическая запись", "field": "поле кодирования", "value": "значение кодирования", "coding_note": "заметка кодирования", "coding_image": "изображение кодирования", "rater_revision": "версия рецензента", "protocol": "протокол"},
        "controls": {"recording_started": "Запись начата", "recording_resumed": "Запись возобновлена", "recording_paused": "Запись приостановлена", "recording_stopped": "Запись остановлена", "stages_changed": "Изменены этапы записи", "history_cleared": "История трассировки очищена"},
        "control_sources": {"manual": "Ручное изменение", "import": "Импортированное изменение", "baseline": "Начальный снимок", "control": "Управление записью"},
        "privacy_fields": {"notes": "Заметки", "source_quotes": "Цитаты из источника"},
    },
    "es": {
        "open_heading": "Abrir y usar este informe",
        "open": "Descomprima el ZIP y abra report.html en un navegador. El informe funciona sin conexión.",
        "submission": "Use el borrador metodológico como punto de partida para Métodos o un suplemento. Compárelo con el protocolo y complete los apartados sin datos. PRISMA no exige este formato de trazabilidad.",
        "statistics": "Un evento es un cambio guardado. Los conteos diarios y los límites de pausa no miden el tiempo de lectura ni el tiempo dedicado a un estudio.",
        "boundaries": "Solo se registran cambios cuando el seguimiento está activo para las etapas indicadas. Las pausas y los periodos desactivados dejan lagunas en el registro; borrar el historial elimina registros anteriores. La ausencia de una hora de inicio actual indica que no se está registrando. El número de pausas no indica su duración.",
        "fields_help": "Cada fila muestra cuándo se guardó un cambio, su etapa y tipo, el documento y campo de codificación afectados, y los valores guardados antes y después. Aquí se muestran los primeros 200 eventos seleccionados; events.csv contiene toda la selección.",
        "field": "Campo de codificación", "before": "Valores guardados anteriores", "after": "Valores guardados nuevos",
        "no_previous": "No hay un valor anterior en este registro", "no_value": "No se registró ningún valor",
        "all_events": "Todos los eventos registrados", "status_now": "Estado al exportar",
        "selection_fields": {"stage": "Etapa", "action": "Acción", "subject_id": "Documento", "since": "Desde", "until": "Hasta"},
        "field_names": {"decision": "Decisión", "exclusion_reason": "Motivo de exclusión", "reason": "Motivo", "notes": "Notas", "tags": "Etiquetas", "screened_at": "Guardado el", "value": "Valor de codificación", "text": "Texto", "rationale": "Justificación", "source": "Fuente", "version": "Revisión", "supersedes_id": "Sustituye la revisión", "protocol_version": "Versión del protocolo", "status": "Estado del registro", "stages": "Etapas activadas", "event_count": "Número de eventos", "options": "Opciones", "type": "Tipo de campo", "name": "Nombre del campo", "description": "Descripción", "section": "Sección", "unit": "Unidad", "multi_select": "Selección múltiple"},
        "decision_values": {"include": "Incluir", "exclude": "Excluir", "maybe": "Dudoso"},
        "action_nouns": {"decision": "Decisión de selección", "fulltext": "Archivo de texto completo", "coding_value": "Valor de codificación", "coding_note": "Nota de codificación", "coding_note_image": "Imagen de nota de codificación", "coding_image_ocr": "Texto reconocido de imagen", "field_definition": "Campo de codificación", "rater_record": "Registro de codificación del revisor"},
        "verbs": {"created": "creación", "changed": "modificación", "imported": "importación", "added": "adición", "replaced": "sustitución", "cleared": "borrado", "removed": "retirada", "deleted": "eliminación"},
        "baseline": "Estado existente al iniciar el registro", "baseline_nouns": {"decision": "decisión de selección", "item": "registro bibliográfico", "field": "campo de codificación", "value": "valor de codificación", "coding_note": "nota de codificación", "coding_image": "imagen de codificación", "rater_revision": "revisión del evaluador", "protocol": "protocolo"},
        "controls": {"recording_started": "Registro iniciado", "recording_resumed": "Registro reanudado", "recording_paused": "Registro pausado", "recording_stopped": "Registro detenido", "stages_changed": "Etapas registradas modificadas", "history_cleared": "Historial de trazabilidad borrado"},
        "control_sources": {"manual": "Cambio manual", "import": "Cambio importado", "baseline": "Estado inicial", "control": "Control del registro"},
        "privacy_fields": {"notes": "Notas", "source_quotes": "Citas de la fuente"},
    },
    "ja": {
        "open_heading": "レポートの開き方と使い方",
        "open": "ZIPファイルを展開し、ブラウザーで report.html を開いてください。オフラインで閲覧できます。",
        "submission": "方法報告の草案を論文の方法欄または補足資料の出発点として使えます。研究計画と照合し、未記載の項目を補ってください。PRISMA はこのトレース形式を要求していません。",
        "statistics": "イベントは保存された変更を表します。日ごとの件数や一時停止境界は、読書時間や研究ごとの作業時間を示しません。",
        "boundaries": "記録は、対象段階でトレースが有効な間の変更のみを保存します。一時停止中や無効中は記録の空白となり、履歴を消去すると過去の記録が削除されます。現在の開始時刻が空欄なら記録中ではありません。一時停止回数から停止時間は分かりません。",
        "fields_help": "各行には、保存された変更の時刻、段階と操作の種類、対象文献とコーディング項目、変更前後の保存値を示します。ここには選択されたイベントの先頭200件を表示します。全件は events.csv にあります。",
        "field": "コーディング項目", "before": "変更前の保存値", "after": "変更後の保存値",
        "no_previous": "この記録に以前の値はありません", "no_value": "値は記録されていません",
        "all_events": "記録されたすべてのイベント", "status_now": "エクスポート時の状態",
        "selection_fields": {"stage": "段階", "action": "操作", "subject_id": "文献", "since": "開始", "until": "終了"},
        "field_names": {"decision": "判定", "exclusion_reason": "除外理由", "reason": "理由", "notes": "メモ", "tags": "タグ", "screened_at": "保存日時", "value": "コーディング値", "text": "テキスト", "rationale": "根拠", "source": "出典", "version": "改訂", "supersedes_id": "置き換えた改訂", "protocol_version": "プロトコル版", "status": "記録状態", "stages": "有効な段階", "event_count": "イベント数", "options": "選択肢", "type": "項目の種類", "name": "項目名", "description": "説明", "section": "セクション", "unit": "単位", "multi_select": "複数選択"},
        "decision_values": {"include": "採用", "exclude": "除外", "maybe": "保留"},
        "action_nouns": {"decision": "選別判定", "fulltext": "全文ファイル", "coding_value": "コーディング値", "coding_note": "コーディングメモ", "coding_note_image": "メモ画像", "coding_image_ocr": "画像認識テキスト", "field_definition": "コーディング項目", "rater_record": "評価者のコーディング記録"},
        "verbs": {"created": "作成", "changed": "変更", "imported": "インポート", "added": "追加", "replaced": "置換", "cleared": "消去", "removed": "削除", "deleted": "削除"},
        "baseline": "記録開始時の既存状態", "baseline_nouns": {"decision": "選別判定", "item": "文献レコード", "field": "コーディング項目", "value": "コーディング値", "coding_note": "コーディングメモ", "coding_image": "コーディング画像", "rater_revision": "評価者の改訂", "protocol": "プロトコル"},
        "controls": {"recording_started": "記録開始", "recording_resumed": "記録再開", "recording_paused": "記録一時停止", "recording_stopped": "記録停止", "stages_changed": "記録段階を変更", "history_cleared": "トレース履歴を消去"},
        "control_sources": {"manual": "手動変更", "import": "インポートした変更", "baseline": "開始時の状態", "control": "記録制御"},
        "privacy_fields": {"notes": "メモ", "source_quotes": "出典の引用"},
    },
    "pt": {
        "open_heading": "Abrir e usar este relatório",
        "open": "Extraia o ZIP e abra report.html em um navegador. O relatório funciona offline.",
        "submission": "Use o rascunho de métodos como ponto de partida para a seção Métodos ou um suplemento. Confira com o protocolo e complete os itens sem dados. O PRISMA não exige este formato de registro.",
        "statistics": "Um evento é uma alteração salva. Contagens diárias e limites de pausa não medem tempo de leitura nem tempo dedicado a um estudo.",
        "boundaries": "As alterações só são registradas quando o rastreamento está ativo nas etapas indicadas. Pausas e períodos desativados são lacunas; limpar o histórico remove registros anteriores. Um início atual em branco significa que o registro não está ativo. A contagem de pausas não informa sua duração.",
        "fields_help": "Cada linha mostra quando uma alteração salva foi registrada, sua etapa e tipo, o documento e campo de codificação afetados, e os valores salvos antes e depois. Aqui aparecem os primeiros 200 eventos selecionados; events.csv contém toda a seleção.",
        "field": "Campo de codificação", "before": "Valores salvos anteriores", "after": "Novos valores salvos",
        "no_previous": "Sem valor anterior neste registro", "no_value": "Nenhum valor registrado",
        "all_events": "Todos os eventos registrados", "status_now": "Estado na exportação",
        "selection_fields": {"stage": "Etapa", "action": "Ação", "subject_id": "Documento", "since": "De", "until": "Até"},
        "field_names": {"decision": "Decisão", "exclusion_reason": "Motivo da exclusão", "reason": "Motivo", "notes": "Notas", "tags": "Etiquetas", "screened_at": "Salvo em", "value": "Valor de codificação", "text": "Texto", "rationale": "Justificativa", "source": "Fonte", "version": "Revisão", "supersedes_id": "Substitui revisão", "protocol_version": "Versão do protocolo", "status": "Estado do registro", "stages": "Etapas ativadas", "event_count": "Número de eventos", "options": "Opções", "type": "Tipo de campo", "name": "Nome do campo", "description": "Descrição", "section": "Seção", "unit": "Unidade", "multi_select": "Múltipla escolha"},
        "decision_values": {"include": "Incluir", "exclude": "Excluir", "maybe": "Talvez"},
        "action_nouns": {"decision": "Decisão de seleção", "fulltext": "Arquivo de texto completo", "coding_value": "Valor de codificação", "coding_note": "Nota de codificação", "coding_note_image": "Imagem da nota", "coding_image_ocr": "Texto reconhecido da imagem", "field_definition": "Campo de codificação", "rater_record": "Registro de codificação do avaliador"},
        "verbs": {"created": "criação", "changed": "alteração", "imported": "importação", "added": "adição", "replaced": "substituição", "cleared": "limpeza", "removed": "remoção", "deleted": "exclusão"},
        "baseline": "Estado existente no início do registro", "baseline_nouns": {"decision": "decisão de seleção", "item": "registro bibliográfico", "field": "campo de codificação", "value": "valor de codificação", "coding_note": "nota de codificação", "coding_image": "imagem de codificação", "rater_revision": "revisão do avaliador", "protocol": "protocolo"},
        "controls": {"recording_started": "Registro iniciado", "recording_resumed": "Registro retomado", "recording_paused": "Registro pausado", "recording_stopped": "Registro encerrado", "stages_changed": "Etapas registradas alteradas", "history_cleared": "Histórico do registro apagado"},
        "control_sources": {"manual": "Alteração manual", "import": "Alteração importada", "baseline": "Estado inicial", "control": "Controle do registro"},
        "privacy_fields": {"notes": "Notas", "source_quotes": "Citações da fonte"},
    },
    "de": {
        "open_heading": "Bericht öffnen und verwenden",
        "open": "Entpacken Sie die ZIP-Datei und öffnen Sie report.html im Browser. Der Bericht funktioniert offline.",
        "submission": "Nutzen Sie den Methodenentwurf als Ausgangspunkt für den Methodenteil oder einen Anhang. Prüfen Sie ihn anhand des Protokolls und ergänzen Sie alle fehlenden Angaben. PRISMA verlangt dieses Trace-Format nicht.",
        "statistics": "Ein Ereignis ist eine gespeicherte Änderung. Tageszahlen und Pausengrenzen messen weder Lesezeit noch die Arbeitszeit pro Studie.",
        "boundaries": "Änderungen werden nur erfasst, wenn die Aufzeichnung für die angegebenen Phasen aktiv ist. Pausen und deaktivierte Zeiträume sind Lücken; beim Löschen des Verlaufs werden frühere Einträge entfernt. Ein leerer aktueller Start bedeutet, dass gerade nicht aufgezeichnet wird. Die Pausenzahl enthält keine Dauer.",
        "fields_help": "Jede Zeile zeigt den Zeitpunkt einer gespeicherten Änderung, Phase und Art, den betroffenen Literaturdatensatz und das Kodierfeld sowie die vorherigen und neuen gespeicherten Werte. Hier werden nur die ersten 200 ausgewählten Ereignisse angezeigt; events.csv enthält die gesamte Auswahl.",
        "field": "Kodierfeld", "before": "Vorherige gespeicherte Werte", "after": "Neue gespeicherte Werte",
        "no_previous": "Kein vorheriger Wert in diesem Eintrag", "no_value": "Kein Wert aufgezeichnet",
        "all_events": "Alle aufgezeichneten Ereignisse", "status_now": "Status beim Export",
        "selection_fields": {"stage": "Phase", "action": "Aktion", "subject_id": "Literaturdatensatz", "since": "Von", "until": "Bis"},
        "field_names": {"decision": "Entscheidung", "exclusion_reason": "Ausschlussgrund", "reason": "Grund", "notes": "Notizen", "tags": "Schlagwörter", "screened_at": "Gespeichert am", "value": "Kodierwert", "text": "Text", "rationale": "Begründung", "source": "Quelle", "version": "Revision", "supersedes_id": "Ersetzt Revision", "protocol_version": "Protokollversion", "status": "Aufzeichnungsstatus", "stages": "Aktive Phasen", "event_count": "Ereigniszahl", "options": "Optionen", "type": "Feldtyp", "name": "Feldname", "description": "Beschreibung", "section": "Abschnitt", "unit": "Einheit", "multi_select": "Mehrfachauswahl"},
        "decision_values": {"include": "Einschließen", "exclude": "Ausschließen", "maybe": "Unklar"},
        "action_nouns": {"decision": "Screening-Entscheidung", "fulltext": "Volltextdatei", "coding_value": "Kodierwert", "coding_note": "Kodiernotiz", "coding_note_image": "Bild zur Kodiernotiz", "coding_image_ocr": "Erkannter Bildtext", "field_definition": "Kodierfeld", "rater_record": "Kodierung der prüfenden Person"},
        "verbs": {"created": "erstellt", "changed": "geändert", "imported": "importiert", "added": "hinzugefügt", "replaced": "ersetzt", "cleared": "geleert", "removed": "entfernt", "deleted": "gelöscht"},
        "baseline": "Vorhandener Stand beim Aufzeichnungsbeginn", "baseline_nouns": {"decision": "Screening-Entscheidung", "item": "Literaturdatensatz", "field": "Kodierfeld", "value": "Kodierwert", "coding_note": "Kodiernotiz", "coding_image": "Kodierbild", "rater_revision": "Revision der prüfenden Person", "protocol": "Protokoll"},
        "controls": {"recording_started": "Aufzeichnung gestartet", "recording_resumed": "Aufzeichnung fortgesetzt", "recording_paused": "Aufzeichnung pausiert", "recording_stopped": "Aufzeichnung beendet", "stages_changed": "Aufgezeichnete Phasen geändert", "history_cleared": "Trace-Verlauf gelöscht"},
        "control_sources": {"manual": "Manuelle Änderung", "import": "Importierte Änderung", "baseline": "Anfangsstand", "control": "Aufzeichnungssteuerung"},
        "privacy_fields": {"notes": "Notizen", "source_quotes": "Quellzitate"},
    },
    "sr": {
        "open_heading": "Како отворити и користити извештај",
        "open": "Распакујте ZIP датотеку, па отворите report.html у веб прегледачу. Извештај ради и без интернета.",
        "submission": "Користите нацрт метода као полазну тачку за одељак Методе или додатак. Упоредите га са протоколом и допуните све ставке без података. PRISMA не захтева овај формат трага.",
        "statistics": "Догађај је сачувана измена. Дневни бројеви и границе пауза не мере време читања нити време рада на студији.",
        "boundaries": "Измене се бележе само док је праћење укључено за наведене фазе. Паузе и искључени периоди су празнине; брисање историје уклања раније записе. Празан почетак текућег бележења значи да бележење није активно. Број пауза не показује њихово трајање.",
        "fields_help": "Сваки ред приказује када је сачувана измена забележена, њену фазу и врсту, документ и поље кодирања на које се односи, као и сачуване вредности пре и после. Овде је приказано првих 200 изабраних догађаја; events.csv садржи цео избор.",
        "field": "Поље кодирања", "before": "Претходне сачуване вредности", "after": "Нове сачуване вредности",
        "no_previous": "Нема претходне вредности у овом запису", "no_value": "Вредност није забележена",
        "all_events": "Сви забележени догађаји", "status_now": "Статус при извозу",
        "selection_fields": {"stage": "Фаза", "action": "Радња", "subject_id": "Референца", "since": "Од", "until": "До"},
        "field_names": {"decision": "Одлука", "exclusion_reason": "Разлог искључења", "reason": "Разлог", "notes": "Белешке", "tags": "Ознаке", "screened_at": "Сачувано", "value": "Вредност кодирања", "text": "Текст", "rationale": "Образложење", "source": "Извор", "version": "Ревизија", "supersedes_id": "Замењује ревизију", "protocol_version": "Верзија протокола", "status": "Статус бележења", "stages": "Укључене фазе", "event_count": "Број догађаја", "options": "Опције", "type": "Врста поља", "name": "Назив поља", "description": "Опис", "section": "Одељак", "unit": "Јединица", "multi_select": "Вишеструки избор"},
        "decision_values": {"include": "Укључити", "exclude": "Искључити", "maybe": "Неодлучено"},
        "action_nouns": {"decision": "Одлука о селекцији", "fulltext": "Датотека пуног текста", "coding_value": "Вредност кодирања", "coding_note": "Белешка кодирања", "coding_note_image": "Слика уз белешку", "coding_image_ocr": "Препознати текст слике", "field_definition": "Поље кодирања", "rater_record": "Запис кодирања рецензента"},
        "verbs": {"created": "креирање", "changed": "измена", "imported": "увоз", "added": "додавање", "replaced": "замена", "cleared": "пражњење", "removed": "уклањање", "deleted": "брисање"},
        "baseline": "Постојеће стање на почетку бележења", "baseline_nouns": {"decision": "одлука о селекцији", "item": "библиографски запис", "field": "поље кодирања", "value": "вредност кодирања", "coding_note": "белешка кодирања", "coding_image": "слика кодирања", "rater_revision": "ревизија рецензента", "protocol": "протокол"},
        "controls": {"recording_started": "Бележење започето", "recording_resumed": "Бележење настављено", "recording_paused": "Бележење паузирано", "recording_stopped": "Бележење заустављено", "stages_changed": "Промењене праћене фазе", "history_cleared": "Историја трага обрисана"},
        "control_sources": {"manual": "Ручна измена", "import": "Увезена измена", "baseline": "Почетно стање", "control": "Контрола бележења"},
        "privacy_fields": {"notes": "Белешке", "source_quotes": "Цитати из извора"},
    },
    "ko": {
        "open_heading": "보고서 열기 및 사용",
        "open": "ZIP 파일을 푼 뒤 브라우저에서 report.html을 여세요. 오프라인에서도 볼 수 있습니다.",
        "submission": "방법 보고서 초안을 논문의 방법 절이나 보충 자료의 출발점으로 사용하세요. 연구 계획서와 대조하고 자료가 없는 항목을 채우세요. PRISMA는 이 추적 형식을 요구하지 않습니다.",
        "statistics": "이벤트 하나는 저장된 변경 하나입니다. 일별 건수와 일시중지 경계는 읽은 시간이나 연구별 작업 시간을 나타내지 않습니다.",
        "boundaries": "표시된 단계에서 추적이 켜져 있을 때만 변경이 기록됩니다. 일시중지 또는 꺼진 기간은 기록 공백이며, 이력을 지우면 이전 기록이 삭제됩니다. 현재 시작 시간이 비어 있으면 내보낼 때 기록 중이 아닙니다. 일시중지 횟수로 중지 시간을 알 수 없습니다.",
        "fields_help": "각 행에는 저장된 변경의 시각, 연구 단계와 작업 종류, 영향을 받은 문헌과 코딩 항목, 변경 전후의 저장 값이 표시됩니다. 여기에는 선택된 이벤트 중 처음 200개만 표시되며 전체 선택은 events.csv에 있습니다.",
        "field": "코딩 항목", "before": "이전 저장 값", "after": "새 저장 값",
        "no_previous": "이 기록에 이전 값이 없습니다", "no_value": "기록된 값이 없습니다",
        "all_events": "기록된 모든 이벤트", "status_now": "내보낼 때 상태",
        "selection_fields": {"stage": "단계", "action": "작업", "subject_id": "문헌", "since": "시작", "until": "종료"},
        "field_names": {"decision": "판정", "exclusion_reason": "제외 이유", "reason": "이유", "notes": "메모", "tags": "태그", "screened_at": "저장 시각", "value": "코딩 값", "text": "텍스트", "rationale": "판정 근거", "source": "출처", "version": "수정 버전", "supersedes_id": "대체한 버전", "protocol_version": "프로토콜 버전", "status": "기록 상태", "stages": "활성 단계", "event_count": "이벤트 수", "options": "선택지", "type": "항목 유형", "name": "항목 이름", "description": "설명", "section": "구역", "unit": "단위", "multi_select": "복수 선택"},
        "decision_values": {"include": "포함", "exclude": "제외", "maybe": "보류"},
        "action_nouns": {"decision": "선별 판정", "fulltext": "전문 파일", "coding_value": "코딩 값", "coding_note": "코딩 메모", "coding_note_image": "코딩 메모 이미지", "coding_image_ocr": "이미지 인식 텍스트", "field_definition": "코딩 항목", "rater_record": "검토자 코딩 기록"},
        "verbs": {"created": "생성", "changed": "변경", "imported": "가져옴", "added": "추가", "replaced": "대체", "cleared": "비움", "removed": "삭제", "deleted": "삭제"},
        "baseline": "기록 시작 시 기존 상태", "baseline_nouns": {"decision": "선별 판정", "item": "문헌 기록", "field": "코딩 항목", "value": "코딩 값", "coding_note": "코딩 메모", "coding_image": "코딩 이미지", "rater_revision": "검토자 수정본", "protocol": "프로토콜"},
        "controls": {"recording_started": "기록 시작", "recording_resumed": "기록 재개", "recording_paused": "기록 일시중지", "recording_stopped": "기록 중지", "stages_changed": "기록 단계 변경", "history_cleared": "추적 이력 삭제"},
        "control_sources": {"manual": "수동 변경", "import": "가져온 변경", "baseline": "시작 상태", "control": "기록 제어"},
        "privacy_fields": {"notes": "메모", "source_quotes": "출처 인용"},
    },
}


def _action_label(action: Any, language: str, guidance: Mapping[str, Any]) -> str:
    """Translate trace action keys into short labels for people reading the report."""
    name = str(action or "").casefold()
    controls = guidance["controls"]
    if name in controls:
        return controls[name]
    imported = name.startswith("imported_")
    if imported:
        name = name[len("imported_"):]
    if name.startswith("baseline_"):
        topic = name[len("baseline_"):]
        baseline = guidance["baseline_nouns"].get(topic, topic.replace("_", " "))
        return f"{guidance['baseline']}: {baseline}"
    prefixes = (
        ("coding_note_image", "coding_note_image"), ("coding_image_ocr", "coding_image_ocr"),
        ("field_definition", "field_definition"), ("rater_record", "rater_record"),
        ("coding_value", "coding_value"), ("coding_note", "coding_note"),
        ("decision", "decision"), ("fulltext", "fulltext"),
    )
    for prefix, label in prefixes:
        if name.startswith(prefix + "_"):
            verb = name[len(prefix) + 1:]
            noun = guidance["action_nouns"][label]
            verb_label = guidance["verbs"].get(verb, verb.replace("_", " "))
            result = f"{noun}: {verb_label}"
            return f"{guidance['verbs']['imported']}: {result}" if imported else result
    return str(action or "").replace("_", " ").capitalize()


def _report_selection(selection: Mapping[str, Any] | None, language: str,
                      guidance: Mapping[str, Any]) -> str:
    if not selection:
        return guidance["all_events"]
    names = guidance["selection_fields"]
    parts = []
    for key in ("stage", "action", "subject_id", "since", "until"):
        value = selection.get(key)
        if value is None:
            continue
        if key == "action":
            value = _action_label(value, language, guidance)
        elif key == "stage":
            value = _ACTIVITY_LABELS.get(language, _ACTIVITY_LABELS["en"])[5].get(str(value), value)
        parts.append(f"{names.get(key, key)}: {_text(_json_cell(value))}")
    return "; ".join(parts) or guidance["all_events"]


def _report_value(value: Any, key: str, language: str, guidance: Mapping[str, Any],
                  stage_names: Mapping[str, str], status_names: Mapping[str, str]) -> str:
    if key == "decision" and isinstance(value, str):
        return guidance["decision_values"].get(value.casefold(), value)
    if key == "status" and isinstance(value, str):
        return status_names.get(value, value)
    if key == "stages" and isinstance(value, (list, tuple)):
        return ", ".join(stage_names.get(str(stage), str(stage)) for stage in value)
    return _json_cell(value)


def _report_state(value: Any, *, previous: bool, language: str,
                  guidance: Mapping[str, Any], stage_names: Mapping[str, str],
                  status_names: Mapping[str, str]) -> str:
    if value is None:
        label = guidance["no_previous"] if previous else guidance["no_value"]
        return f"<span class=\"muted\">{html.escape(label)}</span>"
    if isinstance(value, Mapping):
        items = value.items()
    else:
        items = (("value", value),)
    cells = []
    for key, item in items:
        field = guidance["field_names"].get(str(key), str(key).replace("_", " "))
        displayed = _report_value(item, str(key), language, guidance, stage_names, status_names)
        cells.append(f"<div><strong>{html.escape(field)}:</strong> {html.escape(_text(displayed))}</div>")
    return "".join(cells) or f"<span class=\"muted\">{html.escape(guidance['no_value'])}</span>"


def _report_field(event: Mapping[str, Any], guidance: Mapping[str, Any]) -> str:
    field = event.get("field")
    if not isinstance(field, Mapping):
        return ""
    name = field.get("name") or field.get("label")
    field_type = field.get("type") or field.get("dtype")
    details = [str(value) for value in (name, field_type) if value not in (None, "")]
    return _text(" — ".join(details)) if details else ""


def _report_state_text(value: Any, *, previous: bool, language: str,
                       guidance: Mapping[str, Any], stage_names: Mapping[str, str],
                       status_names: Mapping[str, str]) -> str:
    if value is None:
        return guidance["no_previous"] if previous else guidance["no_value"]
    items = value.items() if isinstance(value, Mapping) else (("value", value),)
    parts = []
    for key, item in items:
        label = guidance["field_names"].get(str(key), str(key).replace("_", " "))
        displayed = _report_value(item, str(key), language, guidance, stage_names, status_names)
        parts.append(f"{label}: {_text(displayed)}")
    return "; ".join(parts) or guidance["no_value"]


def _readable_events_csv(events: list[dict[str, Any]], language: str,
                         stage_names: Mapping[str, str], status_names: Mapping[str, str],
                         guidance: Mapping[str, Any]) -> bytes:
    headings = {
        "en": ("Time (ISO 8601)", "Research stage", "Action", "Literature item", "Coding field", "Previous saved values", "New saved values"),
        "zh-CN": ("时间（ISO 8601）", "研究阶段", "操作", "文献", guidance["field"], guidance["before"], guidance["after"]),
        "fr": ("Heure (ISO 8601)", "Étape de recherche", "Action", "Document", guidance["field"], guidance["before"], guidance["after"]),
        "ru": ("Время (ISO 8601)", "Этап исследования", "Действие", "Публикация", guidance["field"], guidance["before"], guidance["after"]),
        "es": ("Hora (ISO 8601)", "Etapa de investigación", "Acción", "Documento", guidance["field"], guidance["before"], guidance["after"]),
        "ja": ("時刻（ISO 8601）", "研究段階", "操作", "文献", guidance["field"], guidance["before"], guidance["after"]),
        "pt": ("Hora (ISO 8601)", "Etapa da pesquisa", "Ação", "Documento", guidance["field"], guidance["before"], guidance["after"]),
        "de": ("Zeit (ISO 8601)", "Forschungsphase", "Aktion", "Literaturdatensatz", guidance["field"], guidance["before"], guidance["after"]),
        "sr": ("Време (ISO 8601)", "Faza istraživanja", "Радња", "Референца", guidance["field"], guidance["before"], guidance["after"]),
        "ko": ("시간 (ISO 8601)", "연구 단계", "작업", "문헌", guidance["field"], guidance["before"], guidance["after"]),
    }.get(language, ("Time (ISO 8601)", "Research stage", "Action", "Literature item", "Coding field", "Previous saved values", "New saved values"))
    rows = []
    for event in events:
        stage = stage_names.get(str(event.get("stage", "")), str(event.get("stage", "")))
        subject = event.get("subject_title") or event.get("subject_id", "")
        rows.append({
            "time": event.get("recorded_at", ""), "stage": stage,
            "action": _action_label(event.get("action"), language, guidance),
            "subject": subject, "field": _report_field(event, guidance),
            "before": _report_state_text(event.get("before"), previous=True, language=language,
                                         guidance=guidance, stage_names=stage_names,
                                         status_names=status_names),
            "after": _report_state_text(event.get("after"), previous=False, language=language,
                                        guidance=guidance, stage_names=stage_names,
                                        status_names=status_names),
        })
    localized_rows = [dict(zip(headings, (row[key] for key in ("time", "stage", "action", "subject", "field", "before", "after"))))
                      for row in rows]
    return b"\xef\xbb\xbf" + _csv_bytes(headings, localized_rows)


def _activity_svg(days: list[str], values: dict[str, dict[str, int]], language: str = "en") -> bytes:
    # Keep vector output bounded while retaining the complete, unaggregated activity.csv.
    shown = days[-120:]
    width, height, left, right, top, bottom = 1000, 440, 70, 24, 38, 82
    plot_w, plot_h = width - left - right, height - top - bottom
    totals = [sum(values[day].values()) for day in shown]
    maximum = max(totals, default=0) or 1
    slot = plot_w / max(1, len(shown))
    bar_width = max(2, min(16, slot * 0.66))
    chart_title, chart_desc, plot_title, truncation, empty_label, stage_labels = _ACTIVITY_LABELS.get(language, _ACTIVITY_LABELS["en"])
    elements = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
                f'<title>{html.escape(chart_title)}</title>',
                f'<desc>{html.escape(chart_desc)}</desc>',
                f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
                '<style>text{font-family:sans-serif;fill:#263445;font-size:12px}.grid{stroke:#dbe2ea;stroke-width:1}.axis{stroke:#526173;stroke-width:1}</style>',
                f'<text x="70" y="22" font-size="16">{html.escape(plot_title)}</text>']
    for tick in range(5):
        y_value = maximum * tick / 4
        y = top + plot_h * (1 - tick / 4)
        elements.append(f'<line class="grid" x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}"/>')
        elements.append(f'<text x="{left-10}" y="{y+4:.1f}" text-anchor="end">{y_value:.1f}</text>')
    elements.append(f'<line class="axis" x1="{left}" y1="{top}" x2="{left}" y2="{top+plot_h}"/>')
    elements.append(f'<line class="axis" x1="{left}" y1="{top+plot_h}" x2="{width-right}" y2="{top+plot_h}"/>')
    for index, day in enumerate(shown):
        x = left + index * slot + (slot - bar_width) / 2
        cursor_y = top + plot_h
        for stage in _ACTIVITY_STAGES:
            count = values[day][stage]
            bar_h = plot_h * count / maximum
            cursor_y -= bar_h
            if count:
                elements.append(f'<rect x="{x:.1f}" y="{cursor_y:.1f}" width="{bar_width:.1f}" height="{bar_h:.1f}" fill="{_ACTIVITY_COLORS[stage]}"/>')
        if len(shown) <= 20 or index in {0, len(shown)-1} or index % max(1, len(shown)//10) == 0:
            elements.append(f'<text x="{x + bar_width/2:.1f}" y="{top+plot_h+22}" text-anchor="middle">{html.escape(day)}</text>')
    legend_x = left
    for stage in _ACTIVITY_STAGES:
        elements.append(f'<rect x="{legend_x}" y="{height-25}" width="12" height="12" fill="{_ACTIVITY_COLORS[stage]}"/>')
        elements.append(f'<text x="{legend_x+18}" y="{height-15}">{html.escape(stage_labels[stage])}</text>')
        legend_x += 150
    if len(days) > len(shown):
        elements.append(f'<text x="{width-right}" y="22" text-anchor="end">{html.escape(truncation)}</text>')
    elif not days:
        elements.append(f'<text x="{left+12}" y="{top+30}">{html.escape(empty_label)}</text>')
    elements.append("</svg>")
    return "\n".join(elements).encode("utf-8")


def _activity_png(days: list[str], values: dict[str, dict[str, int]], language: str = "en") -> bytes:
    """Rasterize the labeled SVG chart so PNG and SVG show the same plot."""
    from coscreen.svg_render import svg_to_png
    from pathlib import Path
    from xml.etree import ElementTree as ET
    from PIL import Image, ImageDraw, ImageFont

    # Cairo's toy text API cannot reliably fall back to CJK fonts. Rasterize
    # geometry once, then draw the same SVG labels with our bundled font.
    root = ET.fromstring(_activity_svg(days, values, language))
    labels = list(root.findall("{http://www.w3.org/2000/svg}text"))
    for label in labels:
        root.remove(label)
    image = Image.open(io.BytesIO(svg_to_png(ET.tostring(root), output_width=2000))).convert("RGB")
    draw = ImageDraw.Draw(image)
    font_path = Path(__file__).parent / "assets" / ("trace-chart.otf" if language in {"zh-CN", "ja", "ko"} else "trace-chart-latin.ttf")
    for label in labels:
        size = int(label.get("font-size", "12")) * 2
        font = ImageFont.truetype(str(font_path), size)
        anchor = {"middle": "ms", "end": "rs"}.get(label.get("text-anchor"), "ls")
        draw.text((float(label.get("x", "0"))*2, float(label.get("y", "0"))*2),
                  label.text or "", font=font, fill="#263445", anchor=anchor)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _reports(events: list[dict[str, Any]], partitions: list[dict[str, Any]],
             *, public: bool, export_id: str, selection: Mapping[str, Any] | None = None,
             language: str = "en", included_private_fields: tuple[str, ...] = ()) -> tuple[str, str, bytes]:
    translations = {
        "en": {
            "public": "Public research material", "private": "Private complete backup", "coverage": "Coverage and boundaries",
            "events": "Selected events (first 200)", "methods": "Method report draft", "reference": "Reporting reference",
            "partition": "Partition", "status": "Status", "stages": "Stages", "selected": "Selected events",
            "history": "Recorded history", "pauses": "Pause boundaries", "clears": "History resets",
            "start": "Current recording start", "first": "History from", "last": "History to",
            "time": "Time (ISO 8601)", "stage": "Stage", "action": "Action", "subject": "Subject",
            "export_id": "Export ID", "selection": "Selection", "private_included": "Opted-in notes/quotes",
            "none": "None", "adopted": "Adopted review methods", "reviewers": "Independent reviewers",
            "automation": "Automation or screening tools", "protocol": "Protocol registration",
            "change_source": "Recorded modification sources and actions", "adjudication": "Adjudication source",
            "materials": "Materials location", "limitations": "Coverage limitations", "not_provided": "Not provided by trace data; do not infer.",
            "placeholder": "[Author to provide repository or archive identifier].", "unrecorded": "The trace contains saved changes only; it does not reconstruct work performed while recording was off.",
            "disclaimer": "This is an export of recorded trace events. It cannot establish that unrecorded work did not occur. SHA-256 values detect changes but are not signatures.",
            "timezone": "Timestamps use ISO 8601. Export time and newly recorded trace boundaries use UTC (Z). Imported or legacy event times retain their source representation; a missing offset means the timezone is unknown.",
            "summary": "Selected events: {selected}; recorded history at these per-store cutoffs: {history}. Pause boundaries: {pauses}; history resets: {clears}. Unavailable partitions: {unavailable}.",
            "stage_names": {"screen": "Screening", "fulltext": "Full-text", "coding": "Coding", "control": "Control"},
            "status_names": {"recording": "Recording", "paused": "Paused", "off": "Off", "unavailable": "Unavailable"},
            "adjudication_none": "Not provided by trace data; do not infer.",
        },
        "zh-CN": {
            "public": "公开研究材料", "private": "个人完整备份", "coverage": "记录覆盖与边界",
            "events": "所选事件（前 200 条）", "methods": "方法报告草稿", "reference": "报告规范参考",
            "partition": "分区", "status": "状态", "stages": "阶段", "selected": "所选事件",
            "history": "已有记录", "pauses": "暂停边界", "clears": "历史重置",
            "start": "当前记录起点", "first": "记录开始", "last": "记录结束",
            "time": "时间（ISO 8601）", "stage": "阶段", "action": "操作", "subject": "文献",
            "export_id": "导出编号", "selection": "筛选条件", "private_included": "主动选择的备注/引文",
            "none": "无", "adopted": "采用的综述方法", "reviewers": "独立评审人数",
            "automation": "自动化或筛选工具", "protocol": "方案注册", "change_source": "已记录的修改来源与操作",
            "adjudication": "裁决来源", "materials": "材料存放位置", "limitations": "记录覆盖限制",
            "not_provided": "轨迹数据未提供；请勿推断。", "placeholder": "[请作者填写材料仓库或档案编号]。",
            "unrecorded": "轨迹只记录已保存的变更，无法还原暂停或未启用期间完成的工作。",
            "disclaimer": "本材料导出已记录的轨迹事件，不能证明未记录的工作没有发生。SHA-256 可检测变更，但不是数字签名。",
            "timezone": "时间采用 ISO 8601。导出时间及新记录的轨迹边界使用 UTC（Z）。导入或旧事件保留来源时间格式；缺少时区偏移时，时区未知。",
            "summary": "所选事件：{selected}；各存储分区截止位置的已有记录：{history}。暂停边界：{pauses}；历史重置：{clears}；不可用分区：{unavailable}。",
            "stage_names": {"screen": "初筛", "fulltext": "全文筛选", "coding": "编码", "control": "记录控制"},
            "status_names": {"recording": "记录中", "paused": "已暂停", "off": "未启用", "unavailable": "不可用"},
            "adjudication_none": "轨迹数据未提供裁决来源；请勿推断。",
        },
        "fr": {
            "public": "Matériel de recherche public", "private": "Sauvegarde personnelle complète", "coverage": "Couverture et limites",
            "events": "Événements sélectionnés (200 premiers)", "methods": "Brouillon de la section Méthodes", "reference": "Référence pour le compte rendu",
            "partition": "Partition", "status": "Statut", "stages": "Étapes", "selected": "Événements sélectionnés",
            "history": "Historique enregistré", "pauses": "Limites de pause", "clears": "Réinitialisations", "start": "Début de l’enregistrement actuel",
            "first": "Historique depuis", "last": "Historique jusqu’à", "time": "Heure (ISO 8601)", "stage": "Étape", "action": "Action", "subject": "Document",
            "export_id": "ID d’export", "selection": "Sélection", "private_included": "Notes/extraits inclus à la demande", "none": "Aucun",
            "adopted": "Méthodes de revue adoptées", "reviewers": "Évaluateurs indépendants", "automation": "Outils automatisés ou de sélection",
            "protocol": "Enregistrement du protocole", "change_source": "Sources et actions de modification enregistrées", "adjudication": "Source d’arbitrage",
            "materials": "Emplacement des matériaux", "limitations": "Limites de couverture", "not_provided": "Non indiqué dans les données de suivi ; ne pas en tirer de conclusion.",
            "placeholder": "[L’auteur doit indiquer le dépôt ou l’archive].", "unrecorded": "Le suivi ne contient que les changements enregistrés et ne reconstitue pas le travail effectué pendant les interruptions.",
            "disclaimer": "Export d’événements enregistrés ; il ne prouve pas qu’aucun travail non enregistré n’a eu lieu. Les empreintes SHA-256 détectent les changements mais ne sont pas des signatures.",
            "timezone": "Les dates suivent ISO 8601. L’heure d’export et les nouvelles limites de trace utilisent UTC (Z). Les événements importés ou anciens conservent leur format source ; sans décalage, le fuseau est inconnu.",
            "summary": "Événements sélectionnés : {selected} ; historique aux limites de chaque partition : {history}. Pauses : {pauses} ; réinitialisations : {clears} ; partitions indisponibles : {unavailable}.",
            "stage_names": {"screen": "Sélection", "fulltext": "Texte intégral", "coding": "Codage", "control": "Contrôle"},
            "status_names": {"recording": "En cours", "paused": "En pause", "off": "Désactivé", "unavailable": "Indisponible"}, "adjudication_none": "Aucune information sur l’arbitrage dans les données de suivi.",
        },
        "ru": {
            "public": "Открытые исследовательские материалы", "private": "Личная полная резервная копия", "coverage": "Охват и границы",
            "events": "Выбранные события (первые 200)", "methods": "Черновик отчёта о методах", "reference": "Ссылка для отчётности",
            "partition": "Раздел", "status": "Статус", "stages": "Этапы", "selected": "Выбранные события", "history": "Сохранённая история",
            "pauses": "Границы пауз", "clears": "Сбросы истории", "start": "Начало текущей записи", "first": "История с", "last": "История по",
            "time": "Время (ISO 8601)", "stage": "Этап", "action": "Действие", "subject": "Публикация", "export_id": "ID экспорта",
            "selection": "Отбор", "private_included": "Выбранные заметки/цитаты", "none": "Нет", "adopted": "Принятые методы обзора",
            "reviewers": "Независимые рецензенты", "automation": "Автоматизация или инструменты отбора", "protocol": "Регистрация протокола",
            "change_source": "Источники и действия изменений", "adjudication": "Источник разрешения разногласий", "materials": "Место хранения материалов",
            "limitations": "Ограничения охвата", "not_provided": "Не указано в данных трассировки; не делать выводов.",
            "placeholder": "[Автору указать репозиторий или архив].", "unrecorded": "Трассировка содержит только сохранённые изменения и не восстанавливает работу во время пауз.",
            "disclaimer": "Экспорт записанных событий не доказывает отсутствие незаписанной работы. SHA-256 обнаруживает изменения, но не является подписью.",
            "timezone": "Время указано в ISO 8601. Время экспорта и новые границы трассировки используют UTC (Z). Импортированные и старые события сохраняют исходный формат; без смещения часовой пояс неизвестен.",
            "summary": "Выбрано событий: {selected}; история на границах разделов: {history}. Паузы: {pauses}; сбросы истории: {clears}; недоступные разделы: {unavailable}.",
            "stage_names": {"screen": "Отбор", "fulltext": "Полный текст", "coding": "Кодирование", "control": "Управление"},
            "status_names": {"recording": "Запись", "paused": "Пауза", "off": "Выключено", "unavailable": "Недоступно"}, "adjudication_none": "Не указано в данных трассировки.",
        },
        "es": {
            "public": "Material de investigación público", "private": "Copia personal completa", "coverage": "Cobertura y límites",
            "events": "Eventos seleccionados (primeros 200)", "methods": "Borrador de la sección Métodos", "reference": "Referencia para la presentación de informes",
            "partition": "Partición", "status": "Estado", "stages": "Etapas", "selected": "Eventos seleccionados", "history": "Historial registrado",
            "pauses": "Límites de pausa", "clears": "Reinicios del historial", "start": "Inicio del registro actual", "first": "Historial desde", "last": "Historial hasta",
            "time": "Hora (ISO 8601)", "stage": "Etapa", "action": "Acción", "subject": "Documento", "export_id": "ID de exportación",
            "selection": "Selección", "private_included": "Notas/citas incluidas por elección del usuario", "none": "Ninguna", "adopted": "Métodos de revisión adoptados",
            "reviewers": "Revisores independientes", "automation": "Automatización o herramientas de selección", "protocol": "Registro del protocolo",
            "change_source": "Fuentes y acciones de modificación registradas", "adjudication": "Fuente de adjudicación", "materials": "Ubicación de materiales",
            "limitations": "Límites de cobertura", "not_provided": "No consta en los datos de seguimiento; no se debe inferir.",
            "placeholder": "[El autor debe indicar el repositorio o archivo].", "unrecorded": "El seguimiento contiene solo cambios guardados y no reconstruye el trabajo realizado durante las pausas.",
            "disclaimer": "Exportación de eventos registrados; no demuestra que no hubiera trabajo sin registrar. SHA-256 detecta cambios, pero no es una firma.",
            "timezone": "Las horas usan ISO 8601. La hora de exportación y los nuevos límites de traza usan UTC (Z). Los eventos importados o antiguos conservan su formato de origen; sin desfase, la zona horaria es desconocida.",
            "summary": "Eventos seleccionados: {selected}; historial en los cortes de cada partición: {history}. Pausas: {pauses}; reinicios: {clears}; particiones no disponibles: {unavailable}.",
            "stage_names": {"screen": "Selección", "fulltext": "Texto completo", "coding": "Codificación", "control": "Control"},
            "status_names": {"recording": "Grabando", "paused": "En pausa", "off": "Desactivado", "unavailable": "No disponible"}, "adjudication_none": "No consta información sobre la adjudicación en los datos de seguimiento.",
        },
        "ja": {
            "public": "公開研究資料", "private": "個人用完全バックアップ", "coverage": "記録範囲と境界",
            "events": "選択イベント（先頭200件）", "methods": "方法報告の草案", "reference": "報告基準",
            "partition": "区分", "status": "状態", "stages": "段階", "selected": "選択イベント", "history": "記録履歴",
            "pauses": "一時停止境界", "clears": "履歴リセット", "start": "現在の記録開始", "first": "履歴開始", "last": "履歴終了",
            "time": "時刻（ISO 8601）", "stage": "段階", "action": "操作", "subject": "文献", "export_id": "エクスポートID",
            "selection": "選択条件", "private_included": "選択して含めたメモ/引用", "none": "なし", "adopted": "採用したレビュー方法",
            "reviewers": "独立評価者", "automation": "自動化または選別ツール", "protocol": "プロトコル登録",
            "change_source": "記録された変更元と操作", "adjudication": "判定の情報源", "materials": "資料の保管場所",
            "limitations": "記録範囲の制限", "not_provided": "トレースデータに未記載。推測しないでください。",
            "placeholder": "[著者がリポジトリまたは保管番号を記入]。", "unrecorded": "トレースは保存された変更のみを記録し、記録停止中の作業は再構成しません。",
            "disclaimer": "記録イベントのエクスポートです。未記録の作業がなかったことを証明しません。SHA-256は変更検出用で署名ではありません。",
            "timezone": "時刻はISO 8601形式です。エクスポート時刻と新規トレース境界はUTC（Z）です。インポートまたは旧イベントは元の形式を維持し、オフセットがなければタイムゾーンは不明です。",
            "summary": "選択イベント：{selected}；各保存区分のカットオフ時点の履歴：{history}。一時停止境界：{pauses}；履歴リセット：{clears}；利用不可の区分：{unavailable}。",
            "stage_names": {"screen": "一次選別", "fulltext": "全文選別", "coding": "コーディング", "control": "記録制御"},
            "status_names": {"recording": "記録中", "paused": "一時停止", "off": "無効", "unavailable": "利用不可"}, "adjudication_none": "トレースデータに未記載です。",
        },
        "pt": {
            "public": "Material de pesquisa público", "private": "Backup pessoal completo", "coverage": "Cobertura e limites",
            "events": "Eventos selecionados (primeiros 200)", "methods": "Rascunho da seção Métodos", "reference": "Referência para o relato",
            "partition": "Partição", "status": "Estado", "stages": "Etapas", "selected": "Eventos selecionados", "history": "Histórico registrado",
            "pauses": "Limites de pausa", "clears": "Reinicializações do histórico", "start": "Início do registro atual", "first": "Histórico desde", "last": "Histórico até",
            "time": "Hora (ISO 8601)", "stage": "Etapa", "action": "Ação", "subject": "Documento", "export_id": "ID de exportação",
            "selection": "Seleção", "private_included": "Notas/citações incluídas por escolha do usuário", "none": "Nenhum", "adopted": "Métodos de revisão adotados",
            "reviewers": "Revisores independentes", "automation": "Automação ou ferramentas de triagem", "protocol": "Registro do protocolo",
            "change_source": "Fontes e ações de alteração registradas", "adjudication": "Fonte de adjudicação", "materials": "Local dos materiais",
            "limitations": "Limites de cobertura", "not_provided": "Não consta nos dados do registro de acompanhamento; não se deve inferir.",
            "placeholder": "[Autor deve informar repositório ou arquivo].", "unrecorded": "O registro contém apenas alterações salvas e não reconstrói trabalho feito durante pausas.",
            "disclaimer": "Exportação de eventos registrados; não prova que não houve trabalho sem registro. SHA-256 detecta alterações, mas não é assinatura.",
            "timezone": "Os horários seguem ISO 8601. O horário de exportação e novos limites do registro usam UTC (Z). Eventos importados ou antigos mantêm o formato original; sem deslocamento, o fuso é desconhecido.",
            "summary": "Eventos selecionados: {selected}; histórico nos cortes por partição: {history}. Pausas: {pauses}; reinicializações: {clears}; partições indisponíveis: {unavailable}.",
            "stage_names": {"screen": "Triagem", "fulltext": "Texto completo", "coding": "Codificação", "control": "Controle"},
            "status_names": {"recording": "Gravando", "paused": "Pausado", "off": "Desativado", "unavailable": "Indisponível"}, "adjudication_none": "Não consta informação sobre adjudicação nos dados do registro de acompanhamento.",
        },
        "de": {
            "public": "Öffentliches Forschungsmaterial", "private": "Vollständige persönliche Sicherung", "coverage": "Abdeckung und Grenzen",
            "events": "Ausgewählte Ereignisse (erste 200)", "methods": "Entwurf des Methodenberichts", "reference": "Berichtsreferenz",
            "partition": "Partition", "status": "Status", "stages": "Phasen", "selected": "Ausgewählte Ereignisse", "history": "Aufgezeichneter Verlauf",
            "pauses": "Pausengrenzen", "clears": "Verlaufszurücksetzungen", "start": "Beginn der aktuellen Aufzeichnung", "first": "Verlauf ab", "last": "Verlauf bis",
            "time": "Zeit (ISO 8601)", "stage": "Phase", "action": "Aktion", "subject": "Literaturdatensatz", "export_id": "Export-ID",
            "selection": "Auswahl", "private_included": "Ausgewählte Notizen/Zitate", "none": "Keine", "adopted": "Verwendete Methoden der systematischen Übersichtsarbeit",
            "reviewers": "Unabhängige Prüfende", "automation": "Automatisierung oder Screening-Werkzeuge", "protocol": "Protokollregistrierung",
            "change_source": "Erfasste Änderungsquellen und Aktionen", "adjudication": "Quelle für die Klärung von Meinungsverschiedenheiten", "materials": "Ablageort der Materialien",
            "limitations": "Abdeckungsgrenzen", "not_provided": "In den Trace-Daten nicht angegeben; nicht ableiten.",
            "placeholder": "[Autor:in ergänzt Repository oder Archivkennung].", "unrecorded": "Der Trace enthält nur gespeicherte Änderungen und rekonstruiert keine Arbeit während Aufzeichnungspausen.",
            "disclaimer": "Export aufgezeichneter Ereignisse; er beweist nicht, dass keine nicht aufgezeichnete Arbeit stattfand. SHA-256 erkennt Änderungen, ist aber keine Signatur.",
            "timezone": "Zeitangaben folgen ISO 8601. Exportzeit und neu erfasste Trace-Grenzen verwenden UTC (Z). Importierte oder ältere Ereignisse behalten ihr Quellformat; ohne Offset ist die Zeitzone unbekannt.",
            "summary": "Ausgewählte Ereignisse: {selected}; Verlauf an den Partitionsgrenzen: {history}. Pausen: {pauses}; Zurücksetzungen: {clears}; nicht verfügbare Partitionen: {unavailable}.",
            "stage_names": {"screen": "Screening", "fulltext": "Volltext", "coding": "Kodierung", "control": "Steuerung"},
            "status_names": {"recording": "Aufzeichnung", "paused": "Pausiert", "off": "Aus", "unavailable": "Nicht verfügbar"}, "adjudication_none": "In den Trace-Daten nicht angegeben.",
        },
        "sr": {
            "public": "Јавни истраживачки материјал", "private": "Лична потпуна резервна копија", "coverage": "Обухват и границе",
            "events": "Изабрани догађаји (првих 200)", "methods": "Нацрт извештаја о методама", "reference": "Референца за извештавање",
            "partition": "Партиција", "status": "Статус", "stages": "Фазе", "selected": "Изабрани догађаји", "history": "Забележена историја",
            "pauses": "Границе пауза", "clears": "Поништавања историје", "start": "Почетак тренутног бележења", "first": "Историја од", "last": "Историја до",
            "time": "Време (ISO 8601)", "stage": "Фаза", "action": "Радња", "subject": "Референца", "export_id": "ID извоза",
            "selection": "Избор", "private_included": "Изабране белешке/цитати", "none": "Нема", "adopted": "Усвојене методе прегледа",
            "reviewers": "Независни рецензенти", "automation": "Аутоматизација или алати за селекцију", "protocol": "Регистрација протокола",
            "change_source": "Забележени извори и радње измена", "adjudication": "Извор решавања неслагања", "materials": "Локација материјала",
            "limitations": "Ограничења обухвата", "not_provided": "Није наведено у подацима трага; не закључивати.",
            "placeholder": "[Аутор треба да наведе репозиторијум или архивску ознаку].", "unrecorded": "Траг садржи само сачуване измене и не реконструише рад током пауза.",
            "disclaimer": "Извоз забележених догађаја не доказује да није било незабележеног рада. SHA-256 открива измене, али није потпис.",
            "timezone": "Времена користе ISO 8601. Време извоза и нове границе трага користе UTC (Z). Увезени или стари догађаји задржавају изворни формат; без помераја временска зона је непозната.",
            "summary": "Изабрани догађаји: {selected}; историја на границама партиција: {history}. Паузе: {pauses}; ресетовања: {clears}; недоступне партиције: {unavailable}.",
            "stage_names": {"screen": "Селекција", "fulltext": "Пун текст", "coding": "Кодирање", "control": "Контрола"},
            "status_names": {"recording": "Бележење", "paused": "Паузирано", "off": "Искључено", "unavailable": "Недоступно"}, "adjudication_none": "Није наведено у подацима трага.",
        },
        "ko": {
            "public": "공개 연구 자료", "private": "개인 전체 백업", "coverage": "기록 범위와 경계",
            "events": "선택된 이벤트 (처음 200개)", "methods": "방법 보고서 초안", "reference": "보고 기준 참고",
            "partition": "구분", "status": "상태", "stages": "단계", "selected": "선택 이벤트", "history": "기록된 이력",
            "pauses": "일시중지 경계", "clears": "이력 초기화", "start": "현재 기록 시작", "first": "이력 시작", "last": "이력 종료",
            "time": "시간 (ISO 8601)", "stage": "단계", "action": "작업", "subject": "문헌", "export_id": "내보내기 ID",
            "selection": "선택 조건", "private_included": "선택 포함된 메모/인용", "none": "없음", "adopted": "채택한 검토 방법",
            "reviewers": "독립 검토자", "automation": "자동화 또는 선별 도구", "protocol": "프로토콜 등록",
            "change_source": "기록된 수정 출처와 작업", "adjudication": "판정 출처", "materials": "자료 보관 위치",
            "limitations": "기록 범위 제한", "not_provided": "추적 데이터에 제공되지 않음. 추론하지 마세요.",
            "placeholder": "[저자가 저장소 또는 아카이브 식별자를 입력].", "unrecorded": "추적에는 저장된 변경만 포함되며 기록 중지 기간의 작업은 복원하지 않습니다.",
            "disclaimer": "기록된 추적 이벤트의 내보내기이며 기록되지 않은 작업이 없었다는 증거가 아닙니다. SHA-256은 변경 감지용이며 서명이 아닙니다.",
            "timezone": "시간은 ISO 8601 형식입니다. 내보내기 시간과 새 추적 경계는 UTC (Z)를 사용합니다. 가져온 이벤트나 이전 이벤트는 원본 형식을 유지하며 오프셋이 없으면 시간대를 알 수 없습니다.",
            "summary": "선택 이벤트: {selected}; 구간별 컷오프 시점의 기록 이력: {history}. 일시중지 경계: {pauses}; 이력 초기화: {clears}; 사용할 수 없는 구간: {unavailable}.",
            "stage_names": {"screen": "선별", "fulltext": "전문", "coding": "코딩", "control": "기록 제어"},
            "status_names": {"recording": "기록 중", "paused": "일시중지", "off": "꺼짐", "unavailable": "사용 불가"}, "adjudication_none": "추적 데이터에 제공되지 않았습니다.",
        },
    }
    t = translations.get(language, translations["en"])
    operation_copy = {'zh-CN': {'heading': '近期操作（最多 200 条）', 'help': '这里显示最近保存的筛选与编码操作，包括文献、操作类型以及修改前后的值。启用时保留的起始状态和记录控制不计入本表；完整记录请用 Excel 或 WPS 打开 readable-events.csv 查看。'}, 'en': {'heading': 'Recent actions (up to 200)', 'help': 'This table shows the most recently saved screening and coding actions, including the reference, action type, and values before and after each change. The baseline state retained when tracking is enabled and recording-control events are not included. For the complete history, open readable-events.csv in Excel or WPS.'}, 'fr': {'heading': 'Opérations récentes (200 maximum)', 'help': 'Ce tableau présente les opérations de sélection et de codage enregistrées le plus récemment, avec la référence, le type d’opération et les valeurs avant et après chaque modification. L’état de référence conservé lors de l’activation du suivi et les événements de contrôle de l’enregistrement ne figurent pas dans ce tableau ; pour consulter l’historique complet, ouvrez readable-events.csv dans Excel ou WPS.'}, 'ru': {'heading': 'Последние действия (не более 200)', 'help': 'В этой таблице показаны последние сохранённые операции отбора и кодирования: публикация, тип операции, а также значения до и после изменения. Исходное состояние, сохранённое при включении записи, и события управления записью в таблицу не входят. Полную историю смотрите в файле readable-events.csv, открыв его в Excel или WPS.'}, 'es': {'heading': 'Operaciones recientes (máximo 200)', 'help': 'Esta tabla muestra las operaciones de selección y codificación guardadas más recientemente, con la referencia, el tipo de operación y los valores antes y después de cada cambio. El estado inicial conservado al activar el registro y los eventos de control del registro no aparecen en esta tabla. Para consultar el historial completo, abra readable-events.csv en Excel o WPS.'}, 'ja': {'heading': '最近の操作（最大200件）', 'help': 'この表には、最近保存されたスクリーニングとコーディングの操作が表示されます。文献、操作の種類、変更前後の値を確認できます。記録を有効にした際に保持された開始時の状態と、記録制御の操作はこの表に含まれません。全記録は readable-events.csv を Excel または WPS で開いて確認してください。'}, 'pt': {'heading': 'Operações recentes (até 200)', 'help': 'Esta tabela mostra as operações de triagem e codificação salvas mais recentemente, incluindo a referência, o tipo de operação e os valores antes e depois de cada alteração. O estado inicial preservado quando o registro foi ativado e os eventos de controle do registro não aparecem nesta tabela. Para consultar o histórico completo, abra readable-events.csv no Excel ou WPS.'}, 'de': {'heading': 'Letzte Vorgänge (max. 200)', 'help': 'Diese Tabelle zeigt die zuletzt gespeicherten Screening- und Codierungsvorgänge, einschließlich Referenz, Vorgangstyp sowie der Werte vor und nach einer Änderung. Der beim Aktivieren gespeicherte Ausgangszustand und Ereignisse zur Aufzeichnungssteuerung sind nicht in dieser Tabelle enthalten. Den vollständigen Verlauf finden Sie in readable-events.csv, geöffnet in Excel oder WPS.'}, 'sr': {'heading': "Недавне операције (највише 200)", 'help': "У овој табели приказане су најскорије сачуване операције селекције и кодирања, укључујући референцу, врсту операције и вредности пре и после измене. Почетно стање сачувано при укључивању бележења и догађаји контроле бележења нису обухваћени табелом. Комплетну евиденцију погледајте у датотеци readable-events.csv, отвореној у програму Excel или WPS."}, 'ko': {'heading': '최근 작업 (최대 200개)', 'help': '이 표에는 최근 저장된 선별 및 코딩 작업과 문헌, 작업 유형, 변경 전후 값이 표시됩니다. 기록을 켤 때 보존된 기준 상태와 기록 제어 이벤트는 이 표에 포함되지 않습니다. 전체 기록은 readable-events.csv를 Excel 또는 WPS에서 열어 확인하세요.'}}
    preview_copy = operation_copy.get(language, operation_copy["en"])
    t["events"] = preview_copy["heading"]
    guidance = _REPORT_GUIDANCE.get(language, _REPORT_GUIDANCE["en"])
    reference_note, checklist_label = {
        "en": ("PRISMA describes review reporting; it does not prescribe this trace format.", "Official checklist"),
        "zh-CN": ("PRISMA 规定综述报告的内容，并未要求采用这种轨迹格式。", "官方清单"),
        "fr": ("PRISMA décrit le compte rendu des revues ; il n’impose pas ce format de suivi.", "Liste officielle"),
        "ru": ("PRISMA описывает отчётность обзоров и не предписывает этот формат истории действий.", "Официальный контрольный список"),
        "es": ("PRISMA describe cómo informar las revisiones; no exige este formato de registro.", "Lista oficial"),
        "ja": ("PRISMA はレビューの報告項目を定めており、この履歴形式を指定していません。", "公式チェックリスト"),
        "pt": ("PRISMA descreve como relatar revisões; não exige este formato de registro.", "Lista oficial"),
        "de": ("PRISMA beschreibt die Berichterstattung über Reviews und schreibt dieses Verlaufsformat nicht vor.", "Offizielle Checkliste"),
        "sr": ("PRISMA описује извештавање о прегледима и не прописује овај формат евиденције.", "Званична контролна листа"),
        "ko": ("PRISMA는 검토 보고 항목을 설명하며 이 기록 형식을 요구하지 않습니다.", "공식 체크리스트"),
    }.get(language, ("PRISMA describes review reporting; it does not prescribe this trace format.", "Official checklist"))
    mode = t["public"] if public else t["private"]
    selection_text = _report_selection(selection, language, guidance)
    source_counts: dict[str, int] = {}
    action_names: set[str] = set()
    adjudication_actions: set[str] = set()
    for event in events:
        raw_source = _text(_json_cell(event.get("source")) or "unknown")
        source = guidance["control_sources"].get(raw_source, raw_source)
        source_counts[source] = source_counts.get(source, 0) + 1
        action = str(event.get("action") or "")
        if action:
            action_names.add(_action_label(action, language, guidance))
            if "adjudicat" in action.casefold() or "consensus" in action.casefold():
                adjudication_actions.add(_action_label(action, language, guidance))
    source_text = "; ".join(f"{source} ({count})" for source, count in sorted(source_counts.items())) or t["not_provided"]
    action_text = "; ".join(sorted(action_names)) or t["not_provided"]
    adjudication_text = "; ".join(sorted(adjudication_actions)) or t["adjudication_none"]
    pause_count = sum(int(row.get("pause_count", 0) or 0) for row in partitions)
    clear_count = sum(int(row.get("history_clear_count", 0) or 0) for row in partitions)
    unavailable_count = sum(row.get("status") == "unavailable" for row in partitions)
    history_count = sum(int(row.get("history_event_count", row.get("event_count", 0)) or 0) for row in partitions)
    method_fields = [
        (t["adopted"], t["not_provided"]),
        (t["reviewers"], t["not_provided"]),
        (t["automation"], t["not_provided"]),
        (t["protocol"], t["not_provided"]),
            (t["change_source"], f"{source_text}; {action_text}."),
        (t["adjudication"], adjudication_text),
        (t["materials"], t["placeholder"]),
        (t["limitations"], t["summary"].format(
            selected=len(events), history=history_count, pauses=pause_count,
            clears=clear_count, unavailable=unavailable_count) + " " + t["unrecorded"]),
    ]
    methods_html = "".join(
        f"<li><strong>{html.escape(label)}:</strong> {html.escape(value)}</li>"
        for label, value in method_fields)
    methods_md = "\n".join(f"- **{label}:** {value}" for label, value in method_fields)
    included_text = ", ".join(guidance["privacy_fields"].get(name, name)
                               for name in included_private_fields) if included_private_fields else t["none"]
    status_names = t["status_names"]
    stage_names = t["stage_names"]
    coverage_labels = (t["partition"], f"{t['status']} ({guidance['status_now']})", t["stages"], t["selected"], t["history"],
                       t["pauses"], t["clears"], t["start"], t["first"], t["last"])
    coverage_rows = []
    for row in partitions:
        stages = row.get("stages") if isinstance(row.get("stages"), list) else []
        stage_text = ", ".join(stage_names.get(str(stage), str(stage)) for stage in stages)
        values = [row.get("partition", ""), status_names.get(str(row.get("status", "off")), str(row.get("status", "off"))),
                  stage_text, row.get("event_count", 0), row.get("history_event_count", row.get("event_count", 0)),
                  row.get("pause_count", 0), row.get("history_clear_count", 0),
                  row.get("coverage_start_at") or "", row.get("first_at") or "", row.get("last_at") or ""]
        coverage_rows.append(values)
    coverage_html = "\n".join(
        "<tr>" + "".join(f"<td>{html.escape(_text(_json_cell(value)))}</td>" for value in row) + "</tr>"
        for row in coverage_rows)
    preview_rows = []
    for event in research_operations(events)[:200]:
        stage = stage_names.get(str(event.get("stage", "")), str(event.get("stage", "")))
        preview_rows.append([
            event.get("recorded_at", ""), stage, _action_label(event.get("action"), language, guidance),
            event.get("subject_title") or event.get("subject_id", ""), _report_field(event, guidance),
            event.get("before"), event.get("after"),
        ])
    preview_html = "\n".join(
        "<tr>"
        + "".join(f"<td>{html.escape(_text(_json_cell(value)))}</td>" for value in row[:5])
        + f"<td>{_report_state(row[5], previous=True, language=language, guidance=guidance, stage_names=stage_names, status_names=status_names)}</td>"
        + f"<td>{_report_state(row[6], previous=False, language=language, guidance=guidance, stage_names=stage_names, status_names=status_names)}</td>"
        + "</tr>" for row in preview_rows)
    selection_label = html.escape(t["selection"])
    html_doc = (
        f"<!doctype html><html lang=\"{html.escape(language)}\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{html.escape(mode)}</title><style>"
        "body{margin:0;background:#f4f6f8;color:#1d2939;font:16px/1.55 system-ui,sans-serif}"
        "main{max-width:1200px;margin:auto;padding:24px}section{margin:18px 0;padding:18px;background:#fff;border:1px solid #d8e0e8;border-radius:10px}"
        "h1,h2{line-height:1.25}h1{font-size:1.8rem}h2{font-size:1.25rem;margin-top:0}"
        "table{border-collapse:collapse;width:100%;font-size:.92rem}th,td{padding:9px 10px;border:1px solid #d8e0e8;text-align:left;vertical-align:top}"
        "th{background:#edf2f7;position:sticky;top:0}tbody tr:nth-child(even){background:#f8fafc}"
        ".table-wrap{overflow-x:auto}.callout{border-left:4px solid #3569b0;padding:10px 14px;background:#eef5ff}"
        ".muted{color:#667085}dl{display:grid;grid-template-columns:minmax(150px,220px) 1fr;gap:8px 18px}dt{font-weight:650}dd{margin:0}"
        "@media(max-width:640px){main{padding:12px}section{padding:12px}dl{display:block}dd{margin:0 0 12px}}"
        "</style></head><body><main>"
        f"<h1>{html.escape(mode)}</h1>"
        f"<p>{html.escape(t['disclaimer'])}</p><p>{html.escape(t['timezone'])}</p>"
        f"<p>{html.escape(t['export_id'])}: {html.escape(export_id)}; {html.escape(t['selected'])}: {len(events)}; "
        f"{html.escape(t['history'])}: {history_count}.</p>"
        f"<p>{selection_label}: {html.escape(selection_text)}</p>"
        f"<p>{html.escape(t['private_included'])}: {html.escape(included_text)}.</p>"
        f"<section><h2>{html.escape(guidance['open_heading'])}</h2><p>{html.escape(guidance['open'])}</p>"
        f"<p class=\"callout\">{html.escape(guidance['statistics'])}</p>"
        f"<p><strong>{html.escape(t['methods'])}:</strong> {html.escape(guidance['submission'])}</p></section>"
        f"<section><h2>{html.escape(t['coverage'])}</h2><p>{html.escape(guidance['boundaries'])}</p>"
        "<div class=\"table-wrap\"><table><thead><tr>"
        + "".join(f"<th scope=\"col\">{html.escape(label)}</th>" for label in coverage_labels)
        + "</tr></thead>"
        f"<tbody>{coverage_html}</tbody></table></div></section>"
        f"<section><h2>{html.escape(t['events'])}</h2><p>{html.escape(preview_copy['help'])}</p>"
        "<div class=\"table-wrap\"><table><thead><tr>"
        + "".join(f"<th scope=\"col\">{html.escape(label)}</th>" for label in
                  (t["time"], t["stage"], t["action"], t["subject"], guidance["field"], guidance["before"], guidance["after"]))
        + f"</tr></thead><tbody>{preview_html}</tbody></table></div></section>"
        f"<section><h2>{html.escape(t['methods'])}</h2><ul>{methods_html}</ul></section>"
        f"<section><h2>{html.escape(t['reference'])}</h2><p>Page MJ et al. PRISMA 2020 statement. BMJ. 2021;372:n71. doi:10.1136/bmj.n71. "
        f"{html.escape(reference_note)} "
        f"<a href=\"https://www.prisma-statement.org/s/PRISMA_2020_checklist-ab3g.pdf\">{html.escape(checklist_label)}</a>.</p></section></main></body></html>"
    )
    md_lines = [f"# {mode}", "", t["disclaimer"], t["timezone"], "",
                f"{t['export_id']}: `{export_id}`", f"{t['selected']}: {len(events)}", "",
                f"{t['selection']}: `{selection_text}`", "",
                f"{t['private_included']}: {included_text}", "", f"## {t['coverage']}", "",
                "| " + " | ".join(coverage_labels) + " |",
                "|" + "|".join(["---"] * len(coverage_labels)) + "|"]
    for row in coverage_rows:
        md_lines.append("| " + " | ".join(_text(_json_cell(value)).replace("|", "\\|").replace("\n", " ")
                                              for value in row) + " |")
    md_lines.extend(("", f"## {t['events']}", "",
                     "| " + " | ".join((t["time"], t["stage"], t["action"], t["subject"])) + " |",
                     "|---|---|---|---|"))
    for row in preview_rows:
        md_lines.append("| " + " | ".join(_text(_json_cell(value)).replace("|", "\\|").replace("\n", " ")
                                              for value in row[:4]) + " |")
    md_lines.extend(("", f"## {t['methods']}", "", methods_md, "",
                     f"## {t['reference']}", "",
                     "Page MJ et al. PRISMA 2020 statement. BMJ. 2021;372:n71. doi:10.1136/bmj.n71. "
                     f"{reference_note} "
                     f"[{checklist_label}](https://www.prisma-statement.org/s/PRISMA_2020_checklist-ab3g.pdf)."))
    readable_csv = _readable_events_csv(events, language, stage_names, status_names, guidance)
    return html_doc, "\n".join(md_lines) + "\n", readable_csv


def build_export_zip(snapshot: Mapping[str, Any], *, public: bool = False,
                     author_alias: str = "Reviewer", language: str = "en",
                     include_private_fields: tuple[str, ...] | list[str] = ()) -> bytes:
    """Build a deterministic-content (except export timestamp/ID) trace ZIP.

    ``snapshot`` must contain actor/task metadata, fixed per-store event cutoffs,
    normalized event rows, settings and summaries from a single capture. All
    reports and counts are generated from these supplied rows.
    """
    if not isinstance(snapshot, Mapping):
        raise TracePackageError("Snapshot must be an object.")
    raw_events, partitions = _event_rows(snapshot)
    events = [_event_dict(event) for event in raw_events if isinstance(event, Mapping)]
    if len(events) != len(raw_events):
        raise TracePackageError("Every snapshot event must be an object.")
    export_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    origin: dict[str, Any] = {
        "task_id": str(snapshot.get("task_id", "")),
        "actor_id": str(snapshot.get("actor_id", "")),
        "store_ids": [str(row.get("store_id", "")) for row in partitions],
    }
    selected_private_fields = frozenset(include_private_fields)
    if selected_private_fields - {"notes", "source_quotes"}:
        raise TracePackageError("Only notes and source quotes can be explicitly included.")
    if not public:
        selected_private_fields = frozenset()
    if public:
        events, partitions, origin = _public_snapshot(
            events, partitions, snapshot, author_alias, selected_private_fields)
    event_jsonl = b"".join(_json_bytes(event) for event in events)
    if len(event_jsonl) > MAX_UNCOMPRESSED_BYTES:
        raise TracePackageError("Trace event data exceeds the export size limit.")
    event_csv = _csv_bytes(EVENT_COLUMNS, events)
    coverage_csv = _csv_bytes(("partition", "store_id", "status", "stages", "cutoff_sequence",
                               "event_count", "history_event_count", "first_sequence", "last_sequence",
                               "first_at", "last_at", "selected_first_at", "selected_last_at", "pause_count",
                               "history_clear_count", "coverage_start_at", "coverage_start_ambiguous",
                               "recent_controls", "recent_controls_truncated"),
                              _coverage_rows(partitions))
    summary_csv = _csv_bytes(("stage", "metric", "value"), _summary_rows(events))
    activity_days, activity_values = _activity_data(events)
    activity_csv = _activity_csv(activity_days, activity_values)
    activity_svg = _activity_svg(activity_days, activity_values, language)
    activity_png = _activity_png(activity_days, activity_values, language)
    selection = snapshot.get("selection", {})
    if not isinstance(selection, Mapping):
        raise TracePackageError("Export selection must be an object.")
    selection_for_report = _redact(dict(selection)) if public else dict(selection)
    html_report, markdown_report, readable_events_csv = _reports(
        events, partitions, public=public, export_id=export_id,
        selection=selection_for_report, language=language,
        included_private_fields=tuple(sorted(selected_private_fields)))
    source_counts: dict[str, int] = {}
    method_actions: set[str] = set()
    adjudication_actions: set[str] = set()
    for event in events:
        source = _text(_json_cell(event.get("source")) or "unknown")
        source_counts[source] = source_counts.get(source, 0) + 1
        action = str(event.get("action") or "")
        if action:
            method_actions.add(_text(action))
            if "adjudicat" in action.casefold() or "consensus" in action.casefold():
                adjudication_actions.add(_text(action))
    method_draft = {
        "adopted_review_methods": "Not provided",
        "independent_reviewers": "Not provided",
        "automation_or_screening_tools": "Not provided",
        "protocol_registration": "Not provided",
        "recorded_modification_sources": source_counts or "Not provided",
        "recorded_modification_actions": sorted(method_actions) or "Not provided",
        "adjudication_source": sorted(adjudication_actions) or "Not provided",
        "materials_location": "[Author to provide repository or archive identifier]",
        "coverage_limitations": "Saved trace events only; recording gaps and imported history are not reconstructed as past actions.",
    }
    selection_filters = {key: value for key, value in selection_for_report.items()
                         if key in {"stage", "action", "subject_id", "since", "until"} and value is not None}
    files: dict[str, bytes] = {
        "events.jsonl": event_jsonl,
        "events.csv": event_csv,
        "coverage.csv": coverage_csv,
        "summary.csv": summary_csv,
        "activity.csv": activity_csv,
        "activity.svg": activity_svg,
        "activity.png": activity_png,
        "report.html": html_report.encode("utf-8"),
        "report.md": markdown_report.encode("utf-8"),
        "readable-events.csv": readable_events_csv,
        "README.md": (
            f"# {_REPORT_GUIDANCE.get(language, _REPORT_GUIDANCE['en'])['open_heading']}\n\n"
            f"{_REPORT_GUIDANCE.get(language, _REPORT_GUIDANCE['en'])['open']}\n\n"
            "`readable-events.csv` · Excel / WPS\n\n"
            f"Schema: `{PACKAGE_SCHEMA}`. Privacy mode: `{'public' if public else 'complete'}`.\n\n"
            "For a human-readable report, open `report.html` in a browser. For all events in a spreadsheet, open `readable-events.csv`; `events.csv` and `events.jsonl` retain the machine-readable format.\n\n"
            "The package contains only trace events and derived reports; importing it does not modify screening or coding decisions. "
            "Manifest SHA-256 values detect accidental changes but are not digital signatures.\n"
        ).encode("utf-8"),
        "data-dictionary.json": _json_bytes({
            "schema": PACKAGE_SCHEMA,
            "event_columns": list(EVENT_COLUMNS),
            "activity_chart": "Stacked changes by day and stage. activity.csv retains every included date; SVG labels are provided in activity.svg.",
            "privacy_modes": {"public": "Private identifiers and fields are removed or aliased.",
                              "complete": "Personal backup; may contain private information."},
            "hash_note": "SHA-256 checksums detect changes; they do not authenticate the creator.",
        }),
    }
    manifest = {
        "schema": PACKAGE_SCHEMA,
        "export_id": export_id,
        "exported_at": now,
        "privacy_mode": "public" if public else "complete",
        "origin": origin,
        "selection": selection_for_report,
        "selection_scope": "filtered_subset" if selection_filters else "all_recorded_events",
        "capture_consistency": "fixed read snapshot within each available SQLite partition; partitions captured sequentially and are not a simultaneous cross-store instant",
        "report_language": language,
        "timestamp_note": "ISO 8601; export time and newly recorded control boundaries use UTC Z. Imported/legacy timestamps preserve their source representation; absent offset means timezone unknown.",
        "included_private_fields": sorted(selected_private_fields) if public else [],
        "method_report_draft": method_draft,
        "stores": partitions,
        "event_count": len(events),
        "files": {name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())},
    }
    files["manifest.json"] = _json_bytes(manifest)
    if sum(map(len, files.values())) > MAX_UNCOMPRESSED_BYTES:
        raise TracePackageError("Generated trace package exceeds the uncompressed size limit.")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, files[name])
    result = out.getvalue()
    if len(result) > MAX_PACKAGE_BYTES:
        raise TracePackageError("Generated trace ZIP exceeds the package size limit.")
    return result


def _strict_json(raw: bytes, *, label: str) -> Any:
    def invalid_constant(_value: str):
        raise TracePackageError(f"{label} contains a non-standard JSON number.")
    try:
        return json.loads(raw.decode("utf-8"), parse_constant=invalid_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TracePackageError(f"{label} is not valid UTF-8 JSON.") from exc


def _validate_event(event: Any, index: int) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise TracePackageError(f"Event {index} must be an object.")
    allowed = set(EVENT_COLUMNS) | {"subject_title"}
    if set(event) - allowed:
        raise TracePackageError(f"Event {index} has unsupported fields.")
    required = {"event_id", "store_id", "sequence", "stage", "action", "subject_id", "recorded_at"}
    if not required.issubset(event):
        raise TracePackageError(f"Event {index} is missing required fields.")
    try:
        uuid.UUID(str(event["event_id"]))
    except (ValueError, TypeError, AttributeError):
        raise TracePackageError(f"Event {index} has an invalid event UUID.") from None
    if isinstance(event["sequence"], bool) or not isinstance(event["sequence"], int) or event["sequence"] < 0:
        raise TracePackageError(f"Event {index} has an invalid sequence.")
    for field in ("store_id", "stage", "action", "subject_id", "recorded_at"):
        value = event.get(field)
        if not isinstance(value, str) or not value or len(value) > 2_000:
            raise TracePackageError(f"Event {index} has an invalid {field}.")
    if "subject_title" in event and (not isinstance(event["subject_title"], str) or len(event["subject_title"]) > 10_000):
        raise TracePackageError(f"Event {index} has an invalid subject title.")
    for field in ("task_id", "actor_id", "actor_alias", "source", "field", "source_record_id",
                  "source_revision_id", "batch_id", "request_id", "schema_version", "app_version"):
        if field in event and event[field] is not None and not isinstance(event[field], (str, dict, list, int)):
            raise TracePackageError(f"Event {index} has an invalid {field} type.")
    if "actor_id" not in event and "actor_alias" not in event:
        raise TracePackageError(f"Event {index} does not identify its source actor.")
    if len(_json_bytes(event)) > MAX_EVENT_BYTES:
        raise TracePackageError(f"Event {index} exceeds the maximum event size.")
    return event


def validate_import_package(package: bytes | bytearray | memoryview) -> dict[str, Any]:
    """Validate ZIP paths, bounded content, schema, UUIDs, and all member hashes."""
    raw = bytes(package)
    if not raw or len(raw) > MAX_PACKAGE_BYTES:
        raise TracePackageError("Package is empty or exceeds the upload size limit.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw), "r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise TracePackageError("Upload is not a valid ZIP package.") from exc
    with archive:
        infos = archive.infolist()
        if not infos or len(infos) > MAX_MEMBERS:
            raise TracePackageError("ZIP member count is outside the supported range.")
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise TracePackageError("ZIP contains duplicate member names.")
        member_names = frozenset(names)
        if member_names not in {PACKAGE_MEMBERS, PACKAGE_MEMBERS - _OPTIONAL_PACKAGE_MEMBERS}:
            raise TracePackageError("ZIP contains missing or unsupported members.")
        total_size = 0
        for info in infos:
            name = info.filename
            if (name.startswith(("/", "\\")) or "\\" in name or ":" in name or
                    any(part in {"", ".", ".."} for part in name.split("/"))):
                raise TracePackageError("ZIP contains an unsafe member path.")
            if info.flag_bits & 0x1:
                raise TracePackageError("Encrypted ZIP members are not supported.")
            if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                raise TracePackageError("ZIP uses an unsupported compression method.")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise TracePackageError("ZIP symlinks are not supported.")
            total_size += info.file_size
            if total_size > MAX_UNCOMPRESSED_BYTES:
                raise TracePackageError("Uncompressed package exceeds the size limit.")
            if info.file_size and (info.compress_size == 0 or
                                   info.file_size > max(1, info.compress_size) * MAX_COMPRESSION_RATIO):
                raise TracePackageError("ZIP compression ratio exceeds the safety limit.")
        data = {}
        actual_total = 0
        try:
            for info in infos:
                chunks = []
                member_size = 0
                with archive.open(info, "r") as member:
                    while True:
                        remaining = MAX_UNCOMPRESSED_BYTES - actual_total - member_size
                        chunk = member.read(min(64 * 1024, remaining + 1))
                        if not chunk:
                            break
                        member_size += len(chunk)
                        if member_size > info.file_size or actual_total + member_size > MAX_UNCOMPRESSED_BYTES:
                            raise TracePackageError("ZIP member exceeds its declared or allowed size.")
                        chunks.append(chunk)
                if member_size != info.file_size:
                    raise TracePackageError("ZIP member size does not match its directory entry.")
                if member_size and (info.compress_size == 0 or
                                    member_size > max(1, info.compress_size) * MAX_COMPRESSION_RATIO):
                    raise TracePackageError("ZIP compression ratio exceeds the safety limit.")
                data[info.filename] = b"".join(chunks)
                actual_total += member_size
        except TracePackageError:
            raise
        except (OSError, RuntimeError, zipfile.BadZipFile, EOFError) as exc:
            raise TracePackageError("ZIP member could not be read safely.") from exc
    manifest = _strict_json(data["manifest.json"], label="manifest.json")
    if not isinstance(manifest, dict) or manifest.get("schema") != PACKAGE_SCHEMA:
        raise TracePackageError("Unsupported trace package schema.")
    if manifest.get("privacy_mode") not in {"public", "complete"}:
        raise TracePackageError("Manifest privacy mode is invalid.")
    try:
        uuid.UUID(str(manifest.get("export_id", "")))
    except (ValueError, TypeError, AttributeError):
        raise TracePackageError("Manifest export ID is invalid.") from None
    hashes = manifest.get("files")
    expected_hashes = set(data) - {"manifest.json"}
    if not isinstance(hashes, dict) or set(hashes) != expected_hashes:
        raise TracePackageError("Manifest file list is incomplete or unsupported.")
    for name, expected in hashes.items():
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise TracePackageError(f"Manifest hash for {name} is invalid.")
        actual = hashlib.sha256(data[name]).hexdigest()
        if actual != expected:
            raise TracePackageError(f"Hash mismatch for {name}.")
    lines = data["events.jsonl"].splitlines()
    if len(lines) > MAX_EVENTS:
        raise TracePackageError("Package contains too many events.")
    events = []
    for index, line in enumerate(lines, start=1):
        if not line or len(line) > MAX_EVENT_BYTES:
            raise TracePackageError(f"Event {index} is empty or too large.")
        event = _validate_event(_strict_json(line, label=f"event {index}"), index)
        events.append(event)
    if manifest.get("event_count") != len(events):
        raise TracePackageError("Manifest event count does not match events.jsonl.")
    return {"manifest": manifest, "events": events,
            "package_sha256": hashlib.sha256(raw).hexdigest(),
            "origin_actors": sorted({str(e.get("actor_id", e.get("actor_alias", ""))) for e in events})}
