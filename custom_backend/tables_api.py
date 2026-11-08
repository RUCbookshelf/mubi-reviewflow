"""表生成 API（2.1.5 W7）——研究特征表（Table 1）与证据概览表（SoF）。

两个只读端点（文件流导出；``?detail=1`` 返回装配明细 JSON，供前端只读预览）：

- ``POST /api/tasks/{task_id}/tables/characteristics``：Table 1（CSV / docx）；
- ``POST /api/tasks/{task_id}/tables/sof``：Summary of Findings（CSV / docx）。

权威设计：``out/release_plan/drafts/design_2.1.5_tables.md`` v1.0。

实现约束（与设计 §一/§二 一致）：

- **Table 1 不新算任何数字**：行/列/值全部取自
  :func:`coscreen.export.coding_matrix_table`（编码矩阵 CSV 的同一事实源），
  本模块只做「纳入过滤 + 字段勾选/排序 + 排版」；
- **SoF 是装配器**：效应估计与 CI 复用 ``coscreen.review_analysis.synthesize``
  的合成结果，展示口径复用 ``custom_backend.synthesis_report`` 的
  ``_analysis_estimate`` / ``_display_value`` / ``_number``；研究局限性来自
  评价模块的 GRADE 输入聚合（与 ``GET /api/tasks/{id}/appraisal/grade-input``
  同一规则：多人评价取最严重档）；四构件降级建议原样调用
  ``coscreen.grade_assessor.suggest_*``（不四舍五入、不改写理由）；
- **缺数据格留空 + 脚注（未评价），绝不臆造、绝不填 0**；
- **带 ``rob_tool`` 的 SoF 与 grade-input 同一权限闸**：研究局限性列跨全员
  个人库聚合评价档位，故仅任务属主或 ``consensus:write`` 裁决员可读
  （``_consensus_gate`` 直接复用宿主同一判定函数）；普通成员 403
  ``tables.rob_requires_consensus`` 且响应不含任何档位。不带 ``rob_tool`` 的
  SoF 与 Table 1 仍对全部可见成员开放、不含评价数据；
- **不写业务数据**：不新建任务/筛选员库、不建表、不写轨迹、不改任何既有行；
  筛选员库缺失时 ``ensure=False``（不懒建库）。如实披露：读路径复用既有的
  ``coscreen.db._connect`` 连接工厂，对**已存在的旧库**它会执行幂等的自愈
  DDL（与既有 ``/export/*`` 端点同口径）；本模块自身没有任何 INSERT/UPDATE/
  DELETE，也没有「采纳为判定」之类的写入口（该写回路径属 v2）；
- **零联网**：无网络、无定时、无后台任务；
- SQL 全部为常量字面量 + ``?`` 绑定（本模块直接执行的 SQL 只有既有
  ``coscreen.appraisal.load_records_for_tool`` 常量查询；本文件内无 SQL 拼装）。
"""

from __future__ import annotations

import csv
import io
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import Depends, HTTPException, Query, Response
from pydantic import BaseModel

from coscreen import appraisal as appraisal_core
from coscreen import coding as coding_mod
from coscreen import db as db_mod
from coscreen import grade_assessor as grade
from coscreen import publication_bias as publication_bias_mod
from coscreen import review_analysis as analysis_mod
from coscreen.export import coding_matrix_table
from custom_backend.synthesis_report import _analysis_estimate, _display_value, _number

#: Table 1 CSV 的固定前两列（与编码矩阵一致，见 export.CODING_MATRIX_HEAD_COLUMNS）。
TABLE1_HEAD = ("zotero_key", "title")

#: SoF 列（中文表头，按此顺序输出）。
SOF_COLUMNS = (
    "结局（时间点）",
    "纳入研究数",
    "效应估计 (95% CI)",
    "研究局限性",
    "不一致性",
    "不精确性",
    "发表偏倚",
    "总体置信度",
)

#: GRADE 「研究局限性」档位 → ``suggest_rob_downgrade`` 的 overall 判定带。
#: 评价模块给出的是 GRADE 档位（low/serious/very_serious）；GRADE 建议器要的是
#: 工具原始 overall 判定。两者按既有口径对应（见 coscreen/appraisal.py
#: ``GRADE_ROB_MAP``：high→serious、critical→very_serious、low/some concerns 不降级），
#: 这里做的是**逆向的同带映射**，只用于把聚合档位喂给既有建议器。
_ROB_LEVEL_TO_JUDGEMENT = {"low": "low", "serious": "high", "very_serious": "critical"}

_DEFAULT_INITIAL_DESIGN = "rct"
_DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class CharacteristicsIn(BaseModel):
    """Table 1 请求体（设计 §三：``{fields, inclusion, format, sort}``）。"""

    fields: list[str | int] | None = None
    inclusion: Literal["stage1_stage2", "stage1", "matrix"] = "stage1_stage2"
    format: Literal["csv", "docx"] = "csv"
    sort: Literal["import_order", "author_year"] = "import_order"


class SofIn(BaseModel):
    """SoF 请求体（设计 §三：``{outcomes?, format}`` + 合成/评价参数）。"""

    outcomes: list[str] | None = None
    format: Literal["csv", "docx"] = "csv"
    model: Literal["fixed", "random", "random_pm", "random_reml"] = "fixed"
    ci_method: Literal["normal", "hksj"] = "normal"
    rob_tool: str | None = None
    initial_design: Literal["rct", "observational", "diagnostic"] = _DEFAULT_INITIAL_DESIGN


# ---------------------------------------------------------------------------
# 公共小工具
# ---------------------------------------------------------------------------

def _error(code: str, message_key: str, *, params: dict | None = None,
           status: int = 400) -> HTTPException:
    return HTTPException(status, {"error_code": code, "message_key": message_key,
                                  "params": params or {}})


def _consensus_gate(task_id: str, user: dict) -> None:
    """带 ``rob_tool`` 的 SoF 的权限闸：与 ``GET /appraisal/grade-input`` 同一判定。

    研究局限性列会跨全员个人库聚合评价档位（``_appraisal_levels``），而
    grade-input 对同一聚合只允许属主/裁决员（``consensus:write``）。因此这里
    **直接复用** ``main._arbitration_store`` 这一个判定函数（不复制、不另造
    口径）：非属主且无 ``consensus:write`` → 403；server 模式 legacy 任务 →
    既有 409 ``coding.collaboration_owner_unset``。

    403 改写为本端点的结构化键 ``tables.rob_requires_consensus``（响应体只有
    ``error_code/message_key/params``，不含任何档位）；409/503 原样透出。

    说明：``main.py`` 的注册区在本波冻结（不改），tables 的注册签名因此拿不到
    宿主注入的权限函数；在请求期从宿主模块取同一函数对象是唯一不产生第二份
    权限口径的接法（grade-input 用的就是它）。
    """
    from custom_backend.main import _arbitration_store

    try:
        _arbitration_store(task_id, user)
    except HTTPException as exc:
        if exc.status_code == 403:
            raise _error("tables_rob_requires_consensus",
                         "tables.rob_requires_consensus", status=403) from exc
        raise


def _rob_tool_or_error(tool_id: str) -> dict:
    """``rob_tool`` 校验：与 ``appraisal_api._tool_or_error`` 同口径。

    已注册工具 → 定义；``link_only`` 工具没有条目模板（评价 §0，本就不会有
    评价记录）→ 400 ``tables.rob_tool_link_only``；其余 → 400
    ``tables.rob_tool_unknown``。
    """
    tool_def = appraisal_core.get_tool(tool_id)
    if tool_def is not None:
        return tool_def
    link_only = {entry["id"] for entry in appraisal_core.LINK_ONLY}
    if tool_id.strip() in link_only:
        raise _error("tables_rob_tool_link_only", "tables.rob_tool_link_only",
                     params={"tool": tool_id})
    raise _error("tables_rob_tool_unknown", "tables.rob_tool_unknown",
                 params={"tool": tool_id})


def _today() -> str:
    return date.today().isoformat()


def _csv_cell(value: Any) -> str:
    """CSV 单元格：沿用既有导出的公式注入防护（MM-05 同款，见 main.py）。

    **数值字面量（带符号也算：``-0.5`` / ``+0.5`` / `` -3`` / ``-1e-3``）原样输出**：
    编码矩阵 CSV 由 pandas 直出、不做转义，若这里改写成 ``'-0.5``，同数据的两个
    出口就会给出不同的数字（评审 r1 Core 项）。口径与
    ``custom_backend/analysis_audit_report.py:85-93`` 的 ``cell()`` 一致并扩展到
    正号：先剥符号再试解析数值，能解析的不加前缀；``=1+1`` / ``+cmd`` 这类
    真正的公式仍然加 ``'``（评审 r2 项：``+0.5`` 曾仍被转义）。
    """
    text = "" if value is None else str(value)
    stripped = text.lstrip()
    if not stripped.startswith(("=", "+", "-", "@", "\t", "\r")):
        return text
    if stripped.startswith(("+", "-")):
        try:
            float(stripped)
        except ValueError:
            pass
        else:
            return text
    return "'" + text


def _csv_bytes(header: list[str], rows: list[list[Any]],
               footnotes: list[str] | None = None) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow([_csv_cell(value) for value in header])
    for row in rows:
        writer.writerow([_csv_cell(value) for value in row])
    if footnotes:
        writer.writerow([])
        for note in footnotes:
            writer.writerow([_csv_cell("脚注"), _csv_cell(note)])
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def _file_response(data: bytes, media_type: str, filename: str) -> Response:
    # 文件名只含 ASCII（task_id 与固定前缀），无需 RFC 5987 编码。
    return Response(content=data, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _display_len(value: Any) -> int:
    """表格列宽估算用显示宽度：CJK/全角记 2，其余记 1。"""
    return sum(2 if ord(char) > 0x2E7F else 1 for char in str(value))


def _surname(author: str) -> str:
    """单条作者串 → 姓：``"Smith, John"`` → Smith；``"Smith J"`` → Smith；
    无空格的姓名（如 ``"王小明"``）整体保留。"""
    author = author.strip()
    if "," in author:
        return author.split(",", 1)[0].strip() or author
    tokens = author.split()
    return tokens[0] if len(tokens) > 1 else author


def _author_label(article: Any) -> str:
    """作者-年份列规范：首作者姓氏 + 有**多位**作者时 ``et al.`` + ``(年份)``。

    存储形态来自 ``normalize_authors``（RIS/Zotero 以 ``"; "`` 连接），常见两种：
    ``"Smith, John; Doe, Jane"``（姓, 名）与 ``"Smith J; Doe A"``（姓 名首字母）。
    单作者（``"Smith, John"`` / ``"Lee K"``）**不加** ``et al.``；评审 r1 项。
    缺失作者时回退题名，题名也缺则回退 zotero_key（绝不写空占位）。
    """
    authors = (getattr(article, "authors", "") or "").strip() if article else ""
    year = getattr(article, "year", None) if article else None
    if authors:
        parts = [part.strip() for part in authors.replace("，", ";").split(";")
                 if part.strip()]
        # 分号是唯一作者分隔符（normalize_authors 以 "; " 连接）；逗号在单作者串里
        # 是「姓, 名」分隔，因此**不**按逗号拆分。已知局限：没有分号的
        # "Smith John, Doe Jane" 会被当成单作者（rare；见契约 §2.3）。
        if not parts:
            parts = [authors]
        label = _surname(parts[0])
        if len(parts) > 1:
            label = f"{label} et al."
    else:
        title = (getattr(article, "title", "") or "").strip()
        label = title or (getattr(article, "zotero_key", "") if article else "")
    return f"{label} ({year})" if year else label


def _grade_signal_text(level: str, signal: dict) -> str:
    """``suggest_*`` 返回值的原样渲染：档位标签 + ``键=值``（值不四舍五入）。"""
    parts = [f"{key}={value}" for key, value in signal.items() if key != "available"]
    return f"{grade.DOWNGRADE_LABELS[level]}｜" + "; ".join(parts)


# ---------------------------------------------------------------------------
# Table 1 装配
# ---------------------------------------------------------------------------

def _resolve_fields(dimensions: list[dict], fields: list[str | int] | None) -> list[dict]:
    if not fields:
        return list(dimensions)
    by_id = {int(dim["id"]): dim for dim in dimensions}
    by_name = {str(dim["name"]): dim for dim in dimensions}
    selected: list[dict] = []
    seen: set[int] = set()
    for raw in fields:
        dim = None
        if isinstance(raw, int):
            dim = by_id.get(raw)
        else:
            text = str(raw).strip()
            dim = by_id.get(int(text)) if text.isdigit() else None
            if dim is None:
                dim = by_name.get(text)
        if dim is None:
            raise _error("tables_field_unknown", "tables.field_unknown",
                         params={"field": str(raw)})
        if int(dim["id"]) not in seen:
            seen.add(int(dim["id"]))
            selected.append(dim)
    return selected


def _table1_payload(db_path: Path, body: CharacteristicsIn) -> dict:
    """Table 1 数据（列/行/分组/作者-年份），供三种出口共用。"""
    if not Path(db_path).is_file():
        # 无库时方案里没有任何维度：给了字段名就一定是未知字段（评审 r1 项：
        # 原实现在这里提前返回，`fields=["不存在的字段"]` 会错误地回到 200）。
        if body.fields:
            raise _error("tables_field_unknown", "tables.field_unknown",
                         params={"field": str(body.fields[0]), "available": []})
        return {"columns": ["zotero_key", "title"], "dimensions": [], "rows": [],
                "author_year": {}, "inclusion": body.inclusion, "sort": body.sort}
    include_keys = None
    if body.inclusion == "stage1":
        include_keys = coding_mod.coding_queue(str(db_path), "stage1")
    elif body.inclusion == "stage1_stage2":
        include_keys = coding_mod.coding_queue(str(db_path))  # 缺省 = 初筛+复筛均纳入
    table = coding_matrix_table(db_path, include_keys=include_keys)
    selected = _resolve_fields(table["dimensions"], body.fields)
    columns = list(TABLE1_HEAD) + [dim["column"] for dim in selected]
    rows = [{column: source.get(column, "") or "" for column in columns}
            for source in table["rows"]]
    articles = {article.zotero_key: article for article in
                db_mod.list_articles(db_path, include_duplicates=True)}
    author_year = {row["zotero_key"]: _author_label(articles.get(row["zotero_key"]))
                   for row in rows}
    if body.sort == "author_year":
        order = {key: index for index, key in enumerate(articles)}
        rows.sort(key=lambda row: (
            author_year.get(row["zotero_key"], "").casefold(),
            (getattr(articles.get(row["zotero_key"]), "year", None) or 0),
            order.get(row["zotero_key"], len(order)),
        ))
    return {"columns": columns, "dimensions": selected, "rows": rows,
            "author_year": author_year,
            "inclusion": body.inclusion, "sort": body.sort}


def _table1_notes() -> list[str]:
    return [f"自动生成于 {_today()}，字段以任务模板为准。",
            "空格表示该文献未提取该字段（不填 0、不臆造）。"]


def _table1_csv(payload: dict) -> bytes:
    """Table 1 CSV = 与编码矩阵同构的纯表格（无脚注行：表注只进 docx）。"""
    rows = [[row.get(column, "") for column in payload["columns"]] for row in payload["rows"]]
    return _csv_bytes(payload["columns"], rows)


def _table1_docx(payload: dict) -> bytes:
    from docx import Document

    document = Document()
    document.add_heading("研究特征表（Table 1）", 0)
    dimensions = payload["dimensions"]
    header = ["作者-年份"] + [dim["column"] for dim in dimensions]
    groups = ["文献"] + [(dim.get("section") or "其他字段") for dim in dimensions]
    author_year = payload.get("author_year") or {}
    rows = [[author_year.get(row["zotero_key"], row.get("title") or row["zotero_key"])]
            + [row.get(dim["column"], "") for dim in dimensions]
            for row in payload["rows"]]
    _add_table(document, header, groups, rows)
    for note in _table1_notes():
        document.add_paragraph(note)
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


# ---------------------------------------------------------------------------
# docx 排版（表头分组行 / 列宽自适应）
# ---------------------------------------------------------------------------

def _add_table(document, header: list[str], groups: list[str],
               rows: list[list[str]]) -> None:
    """写一张带「分区表头行 + 列名行 + 数据行」的表格。

    ``groups`` 与列一一对应；连续相同分区合并为一个跨列表头单元格。
    列宽按内容显示宽度自适应到页面可用宽度。
    """
    from docx.enum.section import WD_ORIENT
    from docx.shared import Emu

    section = document.sections[0]
    if len(header) > 6:  # 宽表转横向
        width, height = section.page_width, section.page_height
        if width < height:
            section.orientation = WD_ORIENT.LANDSCAPE
            section.page_width, section.page_height = height, width
    table = document.add_table(rows=0, cols=len(header))
    table.style = "Table Grid"
    table.autofit = False

    if any(group and group != groups[0] for group in groups):
        group_row = table.add_row()
        start = 0
        for index in range(1, len(groups) + 1):
            if index == len(groups) or groups[index] != groups[start]:
                cell = group_row.cells[start]
                if index - start > 1:
                    cell = cell.merge(group_row.cells[index - 1])
                cell.text = groups[start]
                for run in cell.paragraphs[0].runs:
                    run.bold = True
                start = index

    header_row = table.add_row()
    for cell, label in zip(header_row.cells, header):
        cell.text = label
        for run in cell.paragraphs[0].runs:
            run.bold = True
    for row in rows:
        cells = table.add_row().cells
        for cell, value in zip(cells, row):
            cell.text = "" if value is None else str(value)

    available = section.page_width - section.left_margin - section.right_margin
    widths = [max([_display_len(header[index])] + [max(1, _display_len(row[index]))
                                                  for row in rows]) for index in range(len(header))]
    widths = [max(4, min(28, width)) for width in widths]
    total = sum(widths)
    for index, weight in enumerate(widths):
        width = Emu(int(available * weight / total))
        for row in table.rows:
            row.cells[index].width = width


# ---------------------------------------------------------------------------
# SoF 装配
# ---------------------------------------------------------------------------

def _appraisal_levels(info: Any, tool_id: str) -> dict:
    """AGREE 到 ``GET /appraisal/grade-input`` 的同一聚合：多人评价取最严重档。

    只读扫描任务目录下的个人筛选员库；暂时不可读的库计数跳过（不把「读不到」
    当成「没有评价」，脚注会写明跳过数量）。
    """
    ranks = {level: index for index, level in enumerate(appraisal_core.GRADE_RISK_LEVELS)}
    ratings: dict[str, list[str | None]] = {}
    skipped = 0
    for path in sorted((Path(item) for item in info.db_files),
                       key=lambda item: (item.name.casefold(), str(item))):
        if path.suffix.casefold() != ".db" or path.name.casefold() == "collaboration.db":
            continue
        try:
            with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
                try:
                    records = appraisal_core.load_records_for_tool(conn, tool_id)
                except sqlite3.OperationalError as exc:
                    if "no such table" in str(exc).lower():
                        continue
                    raise
        except (sqlite3.Error, OSError):
            skipped += 1
            continue
        for record in records:
            try:
                rating = appraisal_core.study_rating_for_grade(tool_id, record.get("overall"))
            except ValueError:
                rating = {"risk_of_bias": None}
            ratings.setdefault(str(record["article_key"]), []).append(rating.get("risk_of_bias"))
    levels: dict[str, str] = {}
    for key, values in ratings.items():
        usable = [value for value in values if value in ranks]
        if usable:
            levels[key] = max(usable, key=lambda value: ranks[value])
    return {"levels": levels, "skipped": skipped}


def _selected_strata(db_path: Path) -> dict[tuple[str, str, str, str], list[dict]]:
    strata: dict[tuple[str, str, str, str], list[dict]] = {}
    for effect in analysis_mod.list_effects(str(db_path)):
        if not effect.get("selected"):
            continue
        key = (effect["comparison"], effect["outcome"], effect["timepoint"], effect["measure"])
        strata.setdefault(key, []).append(effect)
    return strata


def _effect_cell(db_path: Path, key: tuple[str, str, str, str], rows: list[dict],
                 body: SofIn) -> tuple[str, str, dict]:
    """返回 (单元格文本, 未评价原因（空串=有值）, 明细)。"""
    comparison, outcome, timepoint, measure = key
    detail: dict[str, Any] = {"measure": measure, "n_studies": len(rows)}
    if not rows:
        return "", "无已选结果（该分层存在变体但未选定，或选择异常）", detail
    if len(rows) >= 2:
        try:
            result = analysis_mod.synthesize(db_path, comparison, outcome, timepoint,
                                             measure, body.model, body.ci_method)
        except ValueError as exc:
            detail["synthesis_error"] = str(exc)
            return "", str(exc), detail
        detail.update({
            "pooled": result["pooled"], "ci_low": result["ci_low"], "ci_high": result["ci_high"],
            "pooled_analysis": result["pooled_analysis"],
            "ci_analysis_low": result["ci_analysis_low"],
            "ci_analysis_high": result["ci_analysis_high"],
            "i2_percent": result["i2_percent"], "q_p": result["q_p"], "tau2": result["tau2"],
            "model": result["model"], "ci_method": result["ci_method"],
            "result": result,
        })
        return (f"{measure} {_number(result['pooled'])} "
                f"(95% CI {_number(result['ci_low'])} to {_number(result['ci_high'])})",
                "", detail)
    # 单项研究：展示该研究自身估计与 95% CI（与 synthesis_report 研究行同口径），
    # 不冒充合并结果（脚注注明「未合并」）。
    effect = rows[0]
    center = _analysis_estimate(effect)
    low, high = center - 1.96 * effect["se"], center + 1.96 * effect["se"]
    detail.update({
        "pooled": _display_value(center, measure),
        "ci_low": _display_value(low, measure),
        "ci_high": _display_value(high, measure),
        "single_study": True,
    })
    return (f"{measure} {_number(_display_value(center, measure))} "
            f"(95% CI {_number(_display_value(low, measure))} "
            f"to {_number(_display_value(high, measure))})", "", detail)


def _publication_bias_signal(rows: list[dict], measure: str, n_studies: int) -> tuple:
    try:
        diagnosis = publication_bias_mod.diagnose_publication_bias(
            [{"study_id": row["study_id"], "estimate": row["estimate"], "se": row["se"],
              "measure": measure} for row in rows],
            measure, "rank_correlation")
    except ValueError as exc:
        return "none", {"available": False, "reason": str(exc)}
    return grade.suggest_publication_bias_downgrade(
        {"p_value": diagnosis["p_value"], "n_studies": n_studies})


def _sof_rows(db_path: Path, info: Any, body: SofIn) -> tuple[list[dict], list[str], dict]:
    """装配 SoF 行；返回 (rows, 全局脚注, 参数回显)。"""
    notes: list[str] = []
    params = {"model": body.model, "ci_method": body.ci_method,
              "rob_tool": body.rob_tool, "initial_design": body.initial_design,
              "outcomes": body.outcomes}
    if not Path(db_path).is_file():
        notes.append(f"自动生成于 {_today()}。")
        notes.append("效应：未评价（该任务还没有分析库）。")
        return [], notes, params

    strata = _selected_strata(db_path)
    if not strata:
        notes.append(f"自动生成于 {_today()}。")
        notes.append("效应：未评价（还没有已保存的分析条目）。")
        return [], notes, params

    available_outcomes = sorted({key[1] for key in strata}, key=str.casefold)
    if body.outcomes:
        wanted = [str(item).strip() for item in body.outcomes if str(item).strip()]
        unknown = [item for item in wanted if item not in available_outcomes]
        if unknown:
            raise _error("tables_outcome_unknown", "tables.outcome_unknown",
                         params={"outcomes": unknown, "available": available_outcomes})
        strata = {key: value for key, value in strata.items() if key[1] in set(wanted)}

    rob = (_appraisal_levels(info, body.rob_tool) if body.rob_tool
           else {"levels": {}, "skipped": 0})
    rows: list[dict] = []
    for key in sorted(strata, key=lambda item: (item[1].casefold(), item[2], item[0], item[3])):
        comparison, outcome, timepoint, measure = key
        group = strata[key]
        try:
            selected = analysis_mod.selected_effects(db_path, comparison, outcome,
                                                     timepoint, measure)
        except ValueError as exc:  # 变体未选定 / 设计重叠：如实留空，不 500
            selected = []
            selection_error = str(exc)
        else:
            selection_error = ""
        effect_text, effect_reason, effect_detail = _effect_cell(db_path, key, selected, body)
        result = effect_detail.get("result") or {}
        label = f"{outcome}（{timepoint}）· {measure}"
        row_notes: list[str] = []
        empty_cells: list[str] = []
        # 纳入研究数 = 该分层里的独立研究数（即使「已选」结果缺失/选择异常，
        # 研究数仍取自实际分层——不能把有数据的格子写成 0，评审 r1 项）。
        n_studies = len({str(item["study_id"]) for item in group})
        effect_detail["n_studies"] = n_studies

        # 研究局限性：档位聚合 + GRADE rob 建议（两者都为空则如实留空）
        source_keys = [row["source_key"] for row in selected if row.get("source_key")]
        if not source_keys:  # 选择异常时退回整层来源，仍按研究匹配评价记录
            source_keys = [row["source_key"] for row in group if row.get("source_key")]
        counts: dict[str, int] = {}
        for source_key in source_keys:
            level = rob["levels"].get(str(source_key))
            if level:
                counts[level] = counts.get(level, 0) + 1
        if counts:
            ranks = {level: index for index, level in
                     enumerate(appraisal_core.GRADE_RISK_LEVELS)}
            worst = max(counts, key=lambda level: ranks[level])
            judgement_counts: dict[str, int] = {}
            for level, count in counts.items():
                judgement = _ROB_LEVEL_TO_JUDGEMENT[level]
                judgement_counts[judgement] = judgement_counts.get(judgement, 0) + count
            level, signal = grade.suggest_rob_downgrade({"overall_counts": judgement_counts})
            n_rated = sum(counts.values())
            # 分母用**本行全部研究**：只给已评价者的比例会让读者误以为全覆盖
            # （评审 r1 项：4 项研究只 1 项有评价时曾写「1/1」）。
            rob_cell = (f"{grade.DOWNGRADE_LABELS[worst]}（{counts[worst]}/{n_studies} 项研究）"
                        f"｜{_grade_signal_text(level, signal)}")
            rob_detail = {"counts": counts, "judgement_counts": judgement_counts,
                          "worst": worst, "n_rated": n_rated, "n_studies": n_studies,
                          "suggestion": {"level": level, "signal": signal},
                          "tool": body.rob_tool, "skipped_libraries": rob["skipped"]}
            if n_rated < n_studies:
                row_notes.append(
                    f"「{label}」研究局限性：{n_studies - n_rated}/{n_studies} 项研究"
                    f"无评价记录（未评价），降级建议只按已评价的 {n_rated} 项研究汇总。")
        else:
            rob_cell = ""
            level, signal = grade.suggest_rob_downgrade(None)
            rob_detail = {"counts": {}, "n_rated": 0, "n_studies": n_studies,
                          "suggestion": {"level": level, "signal": signal},
                          "tool": body.rob_tool, "skipped_libraries": rob["skipped"]}
            empty_cells.append("rob")
            reason = ("未评价（未选择评价工具）" if not body.rob_tool
                      else "未评价（无该工具的评价记录）")
            row_notes.append(f"「{label}」研究局限性：{reason}。")

        suggestions: dict[str, dict] = {"rob": {"level": level, "signal": signal}}
        if result:
            inconsistency = grade.suggest_inconsistency_downgrade(
                {"i2_percent": result["i2_percent"], "q_p": result["q_p"]})
            imprecision = grade.suggest_imprecision_downgrade(
                {"ci_low": result["ci_analysis_low"],
                 "ci_high": result["ci_analysis_high"], "null_value": 0.0})
            publication_bias = _publication_bias_signal(selected, measure, len(selected))
        else:
            inconsistency = grade.suggest_inconsistency_downgrade(None)
            imprecision = grade.suggest_imprecision_downgrade(None)
            publication_bias = grade.suggest_publication_bias_downgrade(None)

        cells: dict[str, str] = {}
        for name, (level_value, signal_value) in (
                ("inconsistency", inconsistency),
                ("imprecision", imprecision),
                ("publication_bias", publication_bias)):
            suggestions[name] = {"level": level_value, "signal": signal_value}
            if signal_value.get("available"):
                cells[name] = _grade_signal_text(level_value, signal_value)
            else:
                cells[name] = ""
                empty_cells.append(name)
                row_notes.append(f"「{label}」{_FACTOR_LABELS[name]}："
                                 f"未评价（{signal_value.get('reason', '无可用数据')}）。")

        if effect_text:
            cells["effect"] = effect_text
        else:
            cells["effect"] = ""
            empty_cells.append("effect")
            row_notes.append(f"「{label}」效应估计：未评价（{effect_reason or '无可用数据'}）。")
        if effect_detail.get("single_study"):
            row_notes.append(f"「{label}」仅 1 项研究：展示该研究自身估计与 95% CI，未做合并。")

        # 总体置信度只在**有合并结果**（≥2 项研究、synthesize 成功）时计算：
        # - 没有效应数据（P4/P5）时四构件基本全空，按「无降级」算「高」是臆造；
        # - 单项研究行虽有估计值、但没有合成，I²/CI/发表偏倚都无从评估，同样
        #   不能拿「全构件无降级」凑出「高」（评审 r2 阻断项，探针 Q3）。
        counted_as_none = [
            _FACTOR_LABELS[name]
            for name in ("rob", "inconsistency", "imprecision", "publication_bias")
            if not suggestions[name]["signal"].get("available")
        ]
        if not result:
            certainty = ""
            quality_detail: dict[str, Any] = {
                "skipped": "no_synthesis" if effect_text else "no_effect"}
            empty_cells.append("certainty")
            reason = ("未评价（无效应数据）" if not effect_text
                      else "未评价（仅 1 项研究，未做 GRADE 合成评价）")
            row_notes.append(f"「{label}」总体置信度：{reason}。")
        else:
            try:
                quality = grade.final_quality(body.initial_design, {
                    "rob": suggestions["rob"]["level"],
                    "inconsistency": suggestions["inconsistency"]["level"],
                    "indirectness": "none",
                    "imprecision": suggestions["imprecision"]["level"],
                    "publication_bias": suggestions["publication_bias"]["level"],
                })
            except ValueError as exc:
                certainty = ""
                quality_detail = {"error": str(exc)}
                empty_cells.append("certainty")
                row_notes.append(f"「{label}」总体置信度：未评价（{exc}）。")
            else:
                certainty = (f"{grade.GRADE_LEVELS[quality['final_quality']]} "
                             f"{grade.CERTAINTY_SYMBOLS[quality['final_quality']]}")
                quality_detail = quality
                if counted_as_none:
                    row_notes.append(
                        f"「{label}」总体置信度：{'、'.join(counted_as_none)}未评价，"
                        "按「无降级」计入（不臆造降级档位）。")

        rows.append({
            "label": label,
            "outcome": outcome,
            "timepoint": timepoint,
            "comparison": comparison,
            "measure": measure,
            "n_studies": n_studies,
            "cells": {"label": label, "n_studies": str(n_studies), "effect": cells["effect"],
                      "rob": rob_cell, "inconsistency": cells["inconsistency"],
                      "imprecision": cells["imprecision"],
                      "publication_bias": cells["publication_bias"],
                      "certainty": certainty},
            "empty_cells": empty_cells,
            "footnotes": row_notes,
            "detail": {"synthesis": effect_detail, "rob": rob_detail,
                       "grade": {"initial_design": body.initial_design,
                                 "suggestions": suggestions, "quality": quality_detail}},
        })
        if selection_error:
            rows[-1]["footnotes"].append(
                f"「{label}」效应选择异常：{selection_error}（未合并）。")

    global_notes = [f"自动生成于 {_today()}。",
                    "效应估计为已保存研究效应的合并结果（模型/区间见参数）；"
                    "空单元格表示无可用数据，不填 0、不臆造。",
                    "降级建议由 grade_assessor 自动生成，仅供研究者参考；"
                    "「采纳为判定」写回属 v2，本端点不写任何数据。",
                    "间接性无法从数据自动推导，未评价，按无降级计入总体置信度。"]
    if body.initial_design == _DEFAULT_INITIAL_DESIGN:
        global_notes.append("研究设计未另行指定：总体置信度按 RCT（初始质量=高）计算；"
                            "可在向导中选择 observational / diagnostic 重算。")
    if body.rob_tool:
        global_notes.append(f"研究局限性来自评价模块 GRADE 输入聚合（工具：{body.rob_tool}），"
                            "多人评价取最严重档。")
        if rob["skipped"]:
            global_notes.append(f"评价库暂时不可读，已跳过 {rob['skipped']} 个库。")
    else:
        global_notes.append("未提供 rob_tool：研究局限性按「未评价」留空。")
    return rows, global_notes, params


_FACTOR_LABELS = {"rob": "研究局限性", "inconsistency": "不一致性",
                  "imprecision": "不精确性", "publication_bias": "发表偏倚"}


def _sof_matrix(rows: list[dict]) -> list[list[str]]:
    return [[row["cells"][key] for key in
             ("label", "n_studies", "effect", "rob", "inconsistency", "imprecision",
              "publication_bias", "certainty")] for row in rows]


def _sof_csv(rows: list[dict], notes: list[str]) -> bytes:
    return _csv_bytes(list(SOF_COLUMNS), _sof_matrix(rows), notes)


def _sof_docx(rows: list[dict], notes: list[str]) -> bytes:
    from docx import Document

    document = Document()
    document.add_heading("证据概览表（Summary of Findings）", 0)
    groups = (["结局与效应"] * 3) + (["GRADE 降级构件"] * 4) + ["总体置信度"]
    _add_table(document, list(SOF_COLUMNS), groups, _sof_matrix(rows))
    for note in notes:
        document.add_paragraph(note, style="List Bullet")
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------

def register_tables_routes(
    app: Any,
    require_user: Callable[..., dict],
    task_or_404: Callable[[str], Any],
    task_visible: Callable[[Any, dict], bool],
    screener_db: Callable[..., Path],
) -> None:
    """在 ``app`` 上注册 W7 两个表生成端点（main.py 注册区一行调用）。"""

    def _info(task_id: str, user: dict) -> Any:
        info = task_or_404(task_id)
        if not task_visible(info, user):
            raise _error("tables_task_forbidden", "tables.task_forbidden", status=403)
        return info

    @app.post("/api/tasks/{task_id}/tables/characteristics")
    def tables_characteristics(task_id: str, body: CharacteristicsIn,
                               screener: str | None = Query(default=None),
                               detail: int = Query(default=0),
                               user: dict = Depends(require_user)):
        info = _info(task_id, user)
        # 只读：ensure=False，库不存在时不懒建（零写入；缺库 = 空表）
        db_path = screener_db(info, screener or user["username"], ensure=False)
        payload = _table1_payload(Path(db_path), body)
        if detail:
            return {
                "table": "characteristics",
                "generated_at": _today(),
                "inclusion": payload["inclusion"],
                "sort": payload["sort"],
                "columns": [{"id": dim["id"], "name": dim["name"],
                             "column": dim["column"], "section": dim["section"]}
                            for dim in payload["dimensions"]],
                "rows": [{"zotero_key": row["zotero_key"], "title": row.get("title", ""),
                          "authors_year": payload["author_year"].get(row["zotero_key"], ""),
                          "values": {dim["column"]: row.get(dim["column"], "") or ""
                                     for dim in payload["dimensions"]}}
                         for row in payload["rows"]],
                "n_rows": len(payload["rows"]),
                "notes": _table1_notes(),
            }
        if body.format == "docx":
            return _file_response(_table1_docx(payload), _DOCX_MEDIA,
                                  f"table1-{task_id}.docx")
        return _file_response(_table1_csv(payload), "text/csv; charset=utf-8",
                              f"table1-{task_id}.csv")

    @app.post("/api/tasks/{task_id}/tables/sof")
    def tables_sof(task_id: str, body: SofIn,
                   screener: str | None = Query(default=None),
                   detail: int = Query(default=0),
                   user: dict = Depends(require_user)):
        info = _info(task_id, user)
        if body.rob_tool:
            # 研究局限性列聚合全员评价档位：权限与 grade-input 完全同一判定，
            # 不通过时在校验工具之前就 403（响应中不含任何档位）。
            _consensus_gate(task_id, user)
            _rob_tool_or_error(body.rob_tool)
        db_path = screener_db(info, screener or user["username"], ensure=False)
        rows, notes, params = _sof_rows(Path(db_path), info, body)
        if detail:
            return {
                "table": "sof",
                "generated_at": _today(),
                "params": params,
                "columns": list(SOF_COLUMNS),
                "rows": rows,
                "footnotes": notes + [note for row in rows for note in row["footnotes"]],
            }
        all_notes = notes + [note for row in rows for note in row["footnotes"]]
        if body.format == "docx":
            return _file_response(_sof_docx(rows, all_notes), _DOCX_MEDIA,
                                  f"sof-{task_id}.docx")
        return _file_response(_sof_csv(rows, all_notes), "text/csv; charset=utf-8",
                              f"sof-{task_id}.csv")
