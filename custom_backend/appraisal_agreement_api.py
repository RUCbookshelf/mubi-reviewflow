"""文献评价的双评一致性 / 导出 / GRADE 衔接（2.1.5 Wave C1）。

三个端点（全部建在 Wave B 的 ``coscreen.appraisal`` 之上，不重写注册表）：

- ``GET /api/tasks/{task_id}/appraisal/agreement?tool=``
  服务器模式下由任务属主/裁决员读取：遍历任务目录内全部 ``{slug}.db`` 的
  ``appraisal_records``（共享排序读全员库 + 锁忙 503 口径），按条目计算
  百分比一致与两两/平均 Cohen's κ（κ 计算复用
  ``coscreen.coding_agreement.summarize_agreement``，不重写公式）。
  分歧清单只含 ``article_key + item_id + 双方应答``，**不含**自由文本理由
  （理由与注释按盲评原则不跨人展示）。
- ``GET /api/tasks/{task_id}/appraisal/export?tool=``
  CSV 流式导出（不落盘），**一行一评价人**：列 =
  ``zotero_key,title,tool,<条目编号…>,overall,rater,updated_at``。
  ``overall`` 对 RoB 2/AMSTAR 2/CERQual 是算法结果（judgement/confidence），
  对 CASP/AXIS/MMAT 是逐条报告文本（``编号=应答``，以 ``; `` 连接）。
- ``GET /api/tasks/{task_id}/appraisal/grade-input?tool=``
  逐文献折算 GRADE「研究局限性」档位（纯函数
  ``coscreen.appraisal.study_rating_for_grade``），多人评价取最严重档并
  列出逐人明细；**只读**，不创建/修改任何 GRADE 草稿。

治理约束：

- 权限复用 ``_arbitration_store`` 的属主/裁决员判定（非属主且无
  ``consensus:write`` → 403；legacy 服务器任务 409），不另造权限口径。
- 读库一律 ``mode=ro``（绝不写成员个人库、不跑自愈 DDL）；锁忙/IO 暂时
  不可读 → 503 稍后重试（沿用 ``SCREENER_LIBRARY_UNAVAILABLE_MESSAGE``），
  永久损坏/缺表才降级为「该成员无评价」。
- SQL 全部常量字面量 + ``?`` 参数绑定，无拼接/format/f-string。
- 任何内部异常降级为 ``available=false`` 或结构化 4xx/503，绝不 500。
"""
from __future__ import annotations

import csv
import io
import sqlite3
from contextlib import closing
from itertools import combinations
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from fastapi import Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from coscreen import appraisal as core
from coscreen import coding_agreement as agreement_mod
from coscreen.screening_arbitration import (
    SCREENER_READ_TIMEOUT,
    ScreenerLibraryUnavailableError,
)
# 终审 #02：CSV 公式注入防护复用 tables_api._csv_cell（MM-05 同一实现），
# 不在评价导出里复制第三份口径——带符号纯数值豁免等语义随源函数演进。
from custom_backend.tables_api import _csv_cell  # noqa: E402


__all__ = ["register_appraisal_agreement_routes"]

#: 综合成对 κ 时 article 与 item 的连接符（不会出现在合法 article_key 中）。
_CELL_SEP = "\x1f"


def register_appraisal_agreement_routes(
    app: Any,
    require_user: Callable[..., dict],
    task_lookup: Callable[[str], Any],
    permission_check: Callable[[str, dict], Any],
    server_mode: Callable[[], bool],
    bad: Callable[[str, int], HTTPException],
    unavailable_message: str,
) -> None:
    """注册双评一致性 / 导出 / GRADE 衔接端点（main.py 注册区单行调用）。"""

    def _error(code: str, message_key: str, *, params: dict | None = None,
               status: int = 400) -> HTTPException:
        return HTTPException(status, {"error_code": code, "message_key": message_key,
                                      "params": params or {}})

    def _tool_or_error(tool: object) -> dict:
        if not isinstance(tool, str) or not tool.strip():
            raise _error("appraisal_tool_required", "appraisal.tool_required")
        tool_def = core.get_tool(tool)
        if tool_def is not None:
            return tool_def
        link_only = {entry["id"] for entry in core.LINK_ONLY}
        if tool.strip() in link_only:
            raise _error("appraisal_tool_link_only", "appraisal.tool_link_only",
                         params={"tool": tool})
        raise _error("appraisal_tool_unknown", "appraisal.tool_unknown",
                     params={"tool": tool})

    def _authorized_info(task_id: str, user: dict):
        """权限闸门（403/409 与仲裁端点同口径）+ 任务信息。"""
        permission_check(task_id, user)
        return task_lookup(task_id)

    def _open_ro(path: Path) -> sqlite3.Connection:
        return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True,
                               timeout=SCREENER_READ_TIMEOUT)

    def _read_rows(path: Path, tool_id: str) -> list[dict]:
        """只读读取单个个人库的某工具评价记录。

        缺表/永久损坏 → ``[]``（该成员无评价）；锁忙/IO → 503 语义异常，
        绝不把「暂时读不到」当成「没有评价」而算出缺员的 κ。
        """
        try:
            with closing(_open_ro(path)) as conn:
                try:
                    return core.load_records_for_tool(conn, tool_id)
                except sqlite3.OperationalError as exc:
                    if "no such table" in str(exc).lower():
                        return []
                    raise
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if "locked" in message or "busy" in message:
                raise ScreenerLibraryUnavailableError(
                    f"appraisal library is temporarily locked: {path.name}") from exc
            return []
        except OSError as exc:
            raise ScreenerLibraryUnavailableError(
                f"appraisal library is temporarily unreadable: {path.name}") from exc
        except sqlite3.DatabaseError:
            return []

    def _load_all(info, tool_id: str) -> dict[str, dict[str, dict]]:
        """``{rater_id: {article_key: record}}``；rater_id 取个人库文件名主干。"""
        ordered = sorted(
            (Path(path) for path in info.db_files),
            key=lambda path: (path.name.casefold(), str(path)),
        )
        loaded: dict[str, dict[str, dict]] = {}
        for path in ordered:
            if path.suffix.casefold() != ".db" or path.name.casefold() == "collaboration.db":
                continue
            if not path.is_file():
                continue
            rows = _read_rows(path, tool_id)
            if rows:
                loaded[path.stem] = {str(row["article_key"]): row for row in rows}
        return loaded

    def _load_titles(info) -> dict[str, str]:
        """题录标题（best-effort）：只读扫描各库 ``articles`` 表，缺失/锁忙即跳过。"""
        titles: dict[str, str] = {}
        for path in sorted((Path(p) for p in info.db_files),
                           key=lambda p: (p.name.casefold(), str(p))):
            if not path.is_file():
                continue
            try:
                with closing(_open_ro(path)) as conn:
                    try:
                        rows = conn.execute(
                            "SELECT zotero_key, title FROM articles").fetchall()
                    except sqlite3.OperationalError:
                        continue
            except (sqlite3.Error, OSError):
                continue
            for key, title in rows:
                key = str(key)
                if key not in titles:
                    titles[key] = "" if title is None else str(title)
        return titles

    # -- 共享：条目目录 / 应答清洗 ------------------------------------------
    def _item_catalog(tool_def: Mapping[str, Any]) -> tuple[list[str], dict[str, str]]:
        """工具条目的稳定顺序（默认变体在前，其余变体追加）+ 编号映射。"""
        order: list[str] = []
        numbers: dict[str, str] = {}

        def add(item: Mapping[str, Any]) -> None:
            item_id = item.get("id")
            if isinstance(item_id, str) and item_id not in numbers:
                order.append(item_id)
                numbers[item_id] = str(item.get("number") or item_id)

        for item in core.iter_items(tool_def):
            add(item)
        for domain in tool_def.get("domains") or []:
            for variant in (domain.get("variants") or {}).values():
                for item in variant.get("items") or []:
                    add(item)
        return order, numbers

    def _clean_responses(record: Mapping[str, Any], known: set[str]) -> dict[str, str]:
        responses = record.get("responses")
        if not isinstance(responses, Mapping):
            return {}
        return {str(key): value for key, value in responses.items()
                if str(key) in known and isinstance(value, str)}

    # -- 1. 双评一致性 ------------------------------------------------------
    def _pairwise(a: str, b: str,
                  cleaned: dict[str, dict[str, dict[str, str]]],
                  item_order: list[str]) -> dict:
        common = sorted(set(cleaned[a]) & set(cleaned[b]))
        records: list[dict] = []
        for article in common:
            for item_id in item_order:
                if item_id in cleaned[a][article]:
                    records.append({"item_key": f"{article}{_CELL_SEP}{item_id}",
                                    "rater_id": a,
                                    "value": cleaned[a][article][item_id]})
                if item_id in cleaned[b][article]:
                    records.append({"item_key": f"{article}{_CELL_SEP}{item_id}",
                                    "rater_id": b,
                                    "value": cleaned[b][article][item_id]})
        result = agreement_mod.summarize_agreement(records, (a, b), "categorical")
        return {
            "rater_a": a,
            "rater_b": b,
            "n_articles_shared": len(common),
            "n_paired_cells": result["n_paired"],
            "agreement_rate": result["agreement_rate"],
            "cohen_kappa": result["cohen_kappa"],
            "kappa_status": result["kappa_status"],
        }

    def _item_rows(raters: list[str],
                   cleaned: dict[str, dict[str, dict[str, str]]],
                   item_order: list[str],
                   numbers: dict[str, str]) -> list[dict]:
        rows: list[dict] = []
        for item_id in item_order:
            n_paired = 0
            n_matches = 0
            kappas: list[float] = []
            statuses: list[str] = []
            for a, b in combinations(raters, 2):
                common = sorted(set(cleaned[a]) & set(cleaned[b]))
                records: list[dict] = []
                for article in common:
                    value_a = cleaned[a][article].get(item_id)
                    value_b = cleaned[b][article].get(item_id)
                    if value_a is not None:
                        records.append({"item_key": article, "field_key": item_id,
                                        "rater_id": a, "value": value_a})
                    if value_b is not None:
                        records.append({"item_key": article, "field_key": item_id,
                                        "rater_id": b, "value": value_b})
                if not records:
                    continue
                result = agreement_mod.summarize_agreement(
                    records, (a, b), "categorical")
                n_paired += result["n_paired"]
                n_matches += sum(1 for entry in result["item_results"]
                                 if entry.get("exact_match"))
                statuses.append(str(result["kappa_status"]))
                if result["cohen_kappa"] is not None:
                    kappas.append(float(result["cohen_kappa"]))
            if kappas:
                kappa_status = "available"
            elif "degenerate_marginals" in statuses:
                kappa_status = "degenerate_marginals"
            else:
                kappa_status = "insufficient_pairs"
            rows.append({
                "item_id": item_id,
                "number": numbers.get(item_id, item_id),
                "n_paired_cells": n_paired,
                "n_disagreements": n_paired - n_matches,
                "agreement_rate": (n_matches / n_paired) if n_paired else None,
                "cohen_kappa": (sum(kappas) / len(kappas)) if kappas else None,
                "kappa_status": kappa_status,
            })
        return rows

    def _disagreements(raters: list[str],
                       cleaned: dict[str, dict[str, dict[str, str]]],
                       item_order: list[str],
                       numbers: dict[str, str]) -> list[dict]:
        articles: set[str] = set()
        for rater in raters:
            articles.update(cleaned[rater])
        rows: list[dict] = []
        for article in sorted(articles):
            for item_id in item_order:
                answers: dict[str, str] = {}
                for rater in raters:
                    value = cleaned[rater].get(article, {}).get(item_id)
                    if value is not None:
                        answers[rater] = value
                if len(answers) >= 2 and len(set(answers.values())) > 1:
                    # 只带条目应答：评论/理由/说明一律不跨人展示（盲评原则）。
                    rows.append({"article_key": article,
                                 "item_id": item_id,
                                 "number": numbers.get(item_id, item_id),
                                 "responses": answers})
        return rows

    def _agreement_payload(tool_def: Mapping[str, Any],
                           loaded: dict[str, dict[str, dict]]) -> dict:
        item_order, numbers = _item_catalog(tool_def)
        known = set(item_order)
        raters = sorted(loaded, key=str.casefold)
        cleaned = {
            rater: {article: _clean_responses(record, known)
                    for article, record in loaded[rater].items()}
            for rater in raters
        }
        rater_rows = [{
            "rater_id": rater,
            "articles_rated": len(cleaned[rater]),
            "items_answered": sum(len(responses) for responses in cleaned[rater].values()),
        } for rater in raters]

        pairwise = [_pairwise(a, b, cleaned, item_order)
                    for a, b in combinations(raters, 2)]
        kappas = [pair["cohen_kappa"] for pair in pairwise
                  if pair["cohen_kappa"] is not None]
        items = _item_rows(raters, cleaned, item_order, numbers)
        articles: set[str] = set()
        for rater in raters:
            articles.update(cleaned[rater])

        return {
            "available": True,
            "tool_id": tool_def["id"],
            "tool_title": tool_def["title"],
            "rule_version": core.RULE_VERSION,
            "agreement_basis": "per_item_categorical",
            "rater_count": len(raters),
            "article_count": len(articles),
            "item_count": len(item_order),
            "items_compared": sum(1 for row in items if row["n_paired_cells"]),
            "n_pairs": len(pairwise),
            "insufficient_raters": len(raters) < 2,
            "raters": rater_rows,
            "pairwise": pairwise,
            "mean_cohen_kappa": (sum(kappas) / len(kappas)) if kappas else None,
            "items": items,
            "disagreements": _disagreements(raters, cleaned, item_order, numbers),
        }

    @app.get("/api/tasks/{task_id}/appraisal/agreement")
    def appraisal_agreement(
        task_id: str,
        tool: str | None = Query(default=None),
        user: dict = Depends(require_user),
    ) -> dict:
        if not server_mode():
            raise _error("appraisal_agreement_desktop_mode",
                         "appraisal.agreement.desktop_mode", status=404)
        info = _authorized_info(task_id, user)  # 403 / legacy 409
        tool_def = _tool_or_error(tool)
        try:
            loaded = _load_all(info, tool_def["id"])
            return _agreement_payload(tool_def, loaded)
        except ScreenerLibraryUnavailableError as exc:
            # 成员库暂时不可读：503 稍后重试，绝不输出缺员 κ。
            raise bad(unavailable_message, 503) from exc
        except Exception:  # noqa: BLE001 —— 只读统计失败：降级，绝不 500
            return {"available": False, "tool_id": tool_def["id"],
                    "tool_title": tool_def["title"],
                    "rule_version": core.RULE_VERSION,
                    "message_key": "appraisal.agreement_unavailable"}

    # -- 2. 评价导出（CSV，流式，不落盘） ----------------------------------
    def _overall_cell(tool_def: Mapping[str, Any], record: Mapping[str, Any],
                      item_order: list[str], numbers: dict[str, str],
                      responses: dict[str, str]) -> str:
        """overall 列：算法工具取算法结果；无分工具取逐条报告文本。"""
        aggregate = record.get("overall")
        aggregate = (aggregate.get("aggregate")
                     if isinstance(aggregate, Mapping) else None)
        mode = tool_def.get("aggregation")
        if isinstance(aggregate, Mapping):
            if mode == "rob2_domains":
                value = aggregate.get("judgement")
                if isinstance(value, str):
                    return value
            elif mode in {"amstar2_confidence", "cerqual_confidence"}:
                value = aggregate.get("confidence")
                if isinstance(value, str):
                    return value
        return "; ".join(f"{numbers.get(item_id, item_id)}={responses[item_id]}"
                         for item_id in item_order if item_id in responses)

    def _csv_chunks(tool_def: Mapping[str, Any],
                    loaded: dict[str, dict[str, dict]],
                    titles: dict[str, str]) -> Iterator[str]:
        item_order, numbers = _item_catalog(tool_def)
        known = set(item_order)
        raters = sorted(loaded, key=str.casefold)
        header = ["zotero_key", "title", "tool",
                  *[numbers[item_id] for item_id in item_order],
                  "overall", "rater", "updated_at"]

        def render(values: list[str]) -> str:
            # 每个单元格（含表头）过 _csv_cell：成员可控文本（article_key、
            # 题录 title）不得以公式形状落格（终审 #02；=HYPERLINK/@SUM/-2+3
            # 一律加 ' 前缀，普通文本与带符号纯数值原样输出）。
            buffer = io.StringIO()
            csv.writer(buffer, lineterminator="\r\n").writerow(
                [_csv_cell(value) for value in values])
            return buffer.getvalue()

        yield "\ufeff" + render(header)
        for rater in raters:
            for article in sorted(loaded[rater]):
                record = loaded[rater][article]
                responses = _clean_responses(record, known)
                row = [article, titles.get(article, ""), str(tool_def["id"])]
                row.extend(responses.get(item_id, "") for item_id in item_order)
                row.append(_overall_cell(tool_def, record, item_order, numbers,
                                         responses))
                row.append(rater)
                updated_at = record.get("updated_at")
                row.append("" if updated_at is None else str(updated_at))
                yield render(row)

    @app.get("/api/tasks/{task_id}/appraisal/export")
    def appraisal_export(task_id: str, tool: str | None = Query(default=None),
                         user: dict = Depends(require_user)):
        info = _authorized_info(task_id, user)
        tool_def = _tool_or_error(tool)
        try:
            loaded = _load_all(info, tool_def["id"])
            titles = _load_titles(info)
        except ScreenerLibraryUnavailableError as exc:
            raise bad(unavailable_message, 503) from exc
        except Exception as exc:  # noqa: BLE001 —— 结构化 409，绝不 500
            raise _error("appraisal_export_unavailable",
                         "appraisal.export_unavailable", status=409) from exc
        filename = f"appraisal-{task_id}-{tool_def['id']}.csv"
        return StreamingResponse(
            _csv_chunks(tool_def, loaded, titles),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # -- 3. GRADE 衔接（只读） ---------------------------------------------
    _RISK_RANK = {level: index for index, level in enumerate(core.GRADE_RISK_LEVELS)}

    @app.get("/api/tasks/{task_id}/appraisal/grade-input")
    def appraisal_grade_input(
        task_id: str,
        tool: str | None = Query(default=None),
        user: dict = Depends(require_user),
    ) -> dict:
        info = _authorized_info(task_id, user)
        tool_def = _tool_or_error(tool)
        try:
            loaded = _load_all(info, tool_def["id"])
            titles = _load_titles(info)
        except ScreenerLibraryUnavailableError as exc:
            raise bad(unavailable_message, 503) from exc
        except Exception:  # noqa: BLE001 —— 只读派生失败：降级，绝不 500
            return {"available": False, "tool_id": tool_def["id"],
                    "tool_title": tool_def["title"],
                    "rule_version": core.RULE_VERSION,
                    "message_key": "appraisal.grade_input_unavailable"}

        raters = sorted(loaded, key=str.casefold)
        articles: set[str] = set()
        for rater in raters:
            articles.update(loaded[rater])
        studies: list[dict] = []
        for article in sorted(articles):
            ratings: list[dict] = []
            for rater in raters:
                record = loaded[rater].get(article)
                if record is None:
                    continue
                try:
                    rating = core.study_rating_for_grade(tool_def["id"],
                                                          record.get("overall"))
                except ValueError:
                    rating = {"tool": tool_def["id"], "available": False,
                              "risk_of_bias": None, "source_value": None,
                              "source": "none", "rule": "invalid_overall"}
                ratings.append({"rater_id": rater, **rating,
                                "updated_at": record.get("updated_at")})
            usable = [entry for entry in ratings
                      if entry.get("risk_of_bias") in _RISK_RANK]
            worst = (max(usable, key=lambda entry: _RISK_RANK[entry["risk_of_bias"]])
                     ["risk_of_bias"] if usable else None)
            if len(usable) > 1:
                basis = "worst_of_raters"
            elif usable:
                basis = "single_rater"
            else:
                basis = "unavailable"
            studies.append({
                "article_key": article,
                "title": titles.get(article, ""),
                "risk_of_bias": worst,
                "basis": basis,
                "ratings": ratings,
            })
        grade_ready = [{"article_key": study["article_key"],
                        "risk_of_bias": study["risk_of_bias"]}
                       for study in studies if study["risk_of_bias"] is not None]
        return {
            "available": True,
            "tool_id": tool_def["id"],
            "tool_title": tool_def["title"],
            "rule_version": core.RULE_VERSION,
            "grade_domain": "risk_of_bias",
            "writes_grade_draft": False,
            "rater_count": len(raters),
            "n_studies": len(studies),
            "n_ready": len(grade_ready),
            "studies": studies,
            "grade_ready": grade_ready,
        }
