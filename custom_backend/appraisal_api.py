"""文献评价 API（2.1.5 Wave B2）——工具注册表 / 按设计推荐 / 记录存取。

四个端点（全部挂在任务下，复用 ``require_user`` + 任务可见性闸门）：

- ``GET  /api/tasks/{task_id}/appraisal/tools``
  注册表摘要（条目数 / 版本 / 官方引用 / 许可 / link_only 清单）。
- ``GET  /api/tasks/{task_id}/appraisal/recommendation?article_key=``
  按研究设计推荐工具（主选 + 备选 + 理由键）。
- ``GET  /api/tasks/{task_id}/appraisal/{article_key}?tool=``
  取某篇文献某工具的完整条目定义 + 已存应答 + 当前汇总。
- ``PUT  /api/tasks/{task_id}/appraisal/{article_key}``
  保存应答（``responses`` + ``comments`` + ``variant`` + ``note`` + 可选人工
  总体判定），校验 tool / item / option 全在册。

治理红线落实：

- **存储与决策同库**：个人库 ``{task_dir}/{slug}.db``（``_screener_db`` 口径），
  新表 ``appraisal_records`` 可加、幂等；旧版程序不查询即忽略。
  题录（文章元数据）由导入统一写**属主**库，推荐端点从属主库只读解析；
  评价记录仍写当前评价人的个人库（团队成员各存各的）。
- **SQL 全参数绑定**：建表是常量 ``executescript``，读写全部 ``?`` 绑定；
  无任何拼接/format/f-string。
- **失败优雅降级绝不 500**：注册表不可用 / 读库失败 → GET 返回
  ``available=false``；写库失败 → 409 结构化错误；校验失败 → 400。
- **文案只走 message_key**：错误体只有 ``error_code`` / ``message_key`` /
  ``params``，不写死任何界面文字。
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

from fastapi import Body, Depends, HTTPException, Query

from coscreen import appraisal as core

__all__ = ["register_appraisal_routes"]


def register_appraisal_routes(
    app: Any,
    require_user: Callable[..., dict],
    task_or_404: Callable[[str], Any],
    task_visible: Callable[[Any, dict], bool] | None = None,
    screener_db: Callable[..., Any] | None = None,
) -> None:
    """在 ``app`` 上注册评价四端点（main.py 注册区单行调用）。"""

    def _error(code: str, message_key: str, *, params: dict | None = None,
               status: int = 400) -> HTTPException:
        return HTTPException(status, {"error_code": code, "message_key": message_key,
                                      "params": params or {}})

    def _info(task_id: str, user: dict):
        info = task_or_404(task_id)
        if task_visible is not None and not task_visible(info, user):
            raise _error("appraisal_task_forbidden", "appraisal.task_forbidden",
                         status=403)
        return info

    def _db_path(info, user: dict):
        if screener_db is None:  # 注册方未注入个人库解析器：显式 409，绝不 500
            raise _error("appraisal_storage_unavailable",
                         "appraisal.storage_unavailable", status=409)
        return screener_db(info, user["username"])

    def _tool_or_error(tool_id: object) -> dict:
        tool = core.get_tool(tool_id)
        if tool is not None:
            return tool
        link_only = {entry["id"] for entry in core.LINK_ONLY}
        if isinstance(tool_id, str) and tool_id.strip() in link_only:
            raise _error("appraisal_tool_link_only", "appraisal.tool_link_only",
                         params={"tool": tool_id})
        raise _error("appraisal_tool_unknown", "appraisal.tool_unknown",
                     params={"tool": tool_id})

    def _read_record(path, article_key: str, tool_id: str) -> dict | None:
        from coscreen import db as db_mod

        with closing(db_mod._connect(path)) as conn:
            core.ensure_appraisal_storage(conn)
            return core.load_record(conn, article_key, tool_id)

    def _find_article(info, user: dict, key: str):
        """按任务题录权威库找文章（团队成员也能拿到，而非只看自己的库）。

        题录由导入写入**属主**个人库；评价记录才写当前评价人的个人库。
        这里只拼属主库路径（legacy 任务归当前用户），**不建库**：文件不
        存在就跳过，避免只读推荐端点产生写副作用。
        """
        from coscreen import db as db_mod

        candidates: list[Path] = []
        try:
            from coscreen import task_collaboration as collaboration_mod
            from coscreen.tasks import slugify_screener

            collab = collaboration_mod.shared_store_path(info.dir)
            owner = collaboration_mod.get_owner(collab, task_id=info.task_id)
            owner_name = (str(owner["owner_username"]) if owner is not None
                          else user["username"])
            candidates.append(info.dir / (slugify_screener(owner_name) + ".db"))
        except Exception:  # noqa: BLE001 —— 解析失败退回当前用户库
            pass
        if screener_db is not None:
            try:
                # ensure=False：只拼路径，不为「找文章」新建个人库。
                candidates.append(Path(screener_db(
                    info, user["username"], ensure=False)))
            except TypeError:  # 注入方为旧签名（无 ensure 形参）
                try:
                    candidates.append(Path(screener_db(info, user["username"])))
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001 —— 个人库路径不可得：跳过
                pass

        seen: set[Path] = set()
        for path in candidates:
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            article = db_mod.get_article(path, key)
            if article is not None:
                return article
        return None

    # -- 1. 注册表摘要 --------------------------------------------------------
    @app.get("/api/tasks/{task_id}/appraisal/tools")
    def appraisal_tools(task_id: str, user: dict = Depends(require_user)) -> dict:
        _info(task_id, user)
        try:
            tools = core.tool_summaries()
            link_only = [dict(entry) for entry in core.LINK_ONLY]
        except Exception:  # noqa: BLE001 —— 只读摘要：降级为空表，绝不 500
            return {"available": False, "count": 0, "tools": [], "link_only": [],
                    "link_only_count": 0, "rule_version": core.RULE_VERSION,
                    "message_key": "appraisal.registry_unavailable"}
        return {
            "available": True,
            "count": len(tools),
            "tools": tools,
            "link_only": link_only,
            "link_only_count": len(link_only),
            "rule_version": core.RULE_VERSION,
        }

    # -- 2. 按设计推荐 --------------------------------------------------------
    @app.get("/api/tasks/{task_id}/appraisal/recommendation")
    def appraisal_recommendation(
        task_id: str,
        article_key: str = Query(...),
        user: dict = Depends(require_user),
    ) -> dict:
        info = _info(task_id, user)
        try:
            key = core.validate_article_key(article_key)
        except core.AppraisalError as exc:
            raise HTTPException(exc.status, exc.to_detail()) from exc

        try:
            article = _find_article(info, user, key)
        except core.AppraisalError:
            raise
        except Exception:  # noqa: BLE001 —— 读库失败：降级为不可用，绝不 500
            return {"available": False, "article_key": key, "primary": None,
                    "alternatives": [], "rule_version": core.RULE_VERSION,
                    "message_key": "appraisal.storage_unavailable"}

        if article is None:
            raise _error("appraisal_article_not_found",
                         "appraisal.article_not_found", status=404,
                         params={"article_key": key})

        try:
            result = core.recommend(article.to_dict())
        except core.AppraisalError as exc:
            raise HTTPException(exc.status, exc.to_detail()) from exc
        except Exception:  # noqa: BLE001 —— 分类/映射内部错误：降级，绝不 500
            return {"available": False, "article_key": key, "primary": None,
                    "alternatives": [], "rule_version": core.RULE_VERSION,
                    "message_key": "appraisal.unavailable"}
        return {"article_key": key, **result}

    # -- 3. 读取记录（含完整工具定义） ----------------------------------------
    @app.get("/api/tasks/{task_id}/appraisal/{article_key}")
    def appraisal_get(
        task_id: str,
        article_key: str,
        tool: str | None = Query(default=None),
        user: dict = Depends(require_user),
    ) -> dict:
        info = _info(task_id, user)
        try:
            key = core.validate_article_key(article_key)
        except core.AppraisalError as exc:
            raise HTTPException(exc.status, exc.to_detail()) from exc
        if not tool:
            raise _error("appraisal_tool_required", "appraisal.tool_required")
        tool_def = _tool_or_error(tool)

        try:
            record = _read_record(_db_path(info, user), key, tool_def["id"])
        except core.AppraisalError:
            raise
        except Exception:  # noqa: BLE001 —— 读库失败：available=false，绝不 500
            return {"available": False, "article_key": key,
                    "tool_id": tool_def["id"], "tool": tool_def,
                    "responses": {}, "comments": {}, "variant": None,
                    "note": "", "overall": None,
                    "message_key": "appraisal.storage_unavailable"}

        record = record or {}
        responses = record.get("responses") or {}
        comments = record.get("comments") or {}
        variant = record.get("variant")
        stored = record.get("overall") or {}
        override = stored.get("override") if isinstance(stored, dict) else None

        try:
            overall = core.aggregate(tool_def["id"], responses, variant=variant,
                                     comments=comments, overall=override)
        except core.AppraisalError as exc:
            # 注册表升级导致旧应答失效：如实返回校验错误，不伪造汇总。
            raise HTTPException(exc.status, exc.to_detail()) from exc
        except Exception:  # noqa: BLE001 —— 汇总内部错误：只读降级，绝不 500
            return {"available": False, "article_key": key,
                    "tool_id": tool_def["id"], "tool": tool_def,
                    "responses": responses, "comments": comments,
                    "variant": variant, "note": record.get("note") or "",
                    "overall": None, "saved": bool(record),
                    "message_key": "appraisal.unavailable"}

        return {
            "available": True,
            "article_key": key,
            "tool_id": tool_def["id"],
            "tool": tool_def,
            "responses": responses,
            "comments": comments,
            "variant": variant,
            "note": record.get("note") or "",
            "overall": overall,
            "saved": bool(record),
            "updated_at": record.get("updated_at"),
        }

    # -- 4. 保存记录 ----------------------------------------------------------
    @app.put("/api/tasks/{task_id}/appraisal/{article_key}")
    def appraisal_put(
        task_id: str,
        article_key: str,
        body: Any = Body(...),
        user: dict = Depends(require_user),
    ) -> dict:
        info = _info(task_id, user)
        try:
            key = core.validate_article_key(article_key)
        except core.AppraisalError as exc:
            raise HTTPException(exc.status, exc.to_detail()) from exc

        if not isinstance(body, dict):
            raise _error("appraisal_body_invalid", "appraisal.body_invalid")
        tool_id = body.get("tool")
        if not isinstance(tool_id, str) or not tool_id.strip():
            raise _error("appraisal_tool_required", "appraisal.tool_required")
        tool_id = tool_id.strip()
        tool_def = _tool_or_error(tool_id)

        note = body.get("note", "")
        if note is None:
            note = ""
        if not isinstance(note, str) or len(note) > core.MAX_NOTE_CHARS:
            raise _error("appraisal_note_invalid", "appraisal.note_invalid",
                         params={"max_chars": core.MAX_NOTE_CHARS})

        responses = body.get("responses", {})
        comments = body.get("comments", {})
        variant = body.get("variant")
        override = body.get("overall")

        try:
            overall = core.aggregate(tool_id, responses, variant=variant,
                                     comments=comments, overall=override)
            normalized, chosen = core.normalize_responses(tool_def, responses,
                                                          variant=variant)
        except core.AppraisalError as exc:
            raise HTTPException(exc.status, exc.to_detail()) from exc
        except Exception as exc:  # noqa: BLE001 —— 汇总内部错误：结构化 409，绝不 500
            raise _error("appraisal_unavailable", "appraisal.unavailable",
                         status=409) from exc

        clean_comments: dict[str, str] = {}
        if isinstance(comments, dict):
            known = {item["id"] for item in core.iter_items(tool_def, chosen)}
            for item_id, comment in comments.items():
                if str(item_id) not in known:
                    raise _error("appraisal_item_unknown", "appraisal.item_unknown",
                                 params={"item_ids": [str(item_id)]})
                if isinstance(comment, str) and comment.strip():
                    clean_comments[str(item_id)] = comment.strip()[:core.MAX_NOTE_CHARS]

        stored_overall = {"aggregate": overall, "override": override,
                          "variant": chosen}
        try:
            from coscreen import db as db_mod

            path = _db_path(info, user)
            with closing(db_mod._connect(path)) as conn, conn:
                core.ensure_appraisal_storage(conn)
                saved = core.save_record(
                    conn, key, tool_id,
                    responses=normalized,
                    comments=clean_comments,
                    variant=chosen,
                    overall=stored_overall,
                    note=note,
                )
        except core.AppraisalError:
            raise
        except (sqlite3.Error, OSError) as exc:
            raise _error("appraisal_storage_unavailable",
                         "appraisal.storage_unavailable", status=409,
                         params={"article_key": key}) from exc
        except Exception as exc:  # noqa: BLE001 —— 兜底：绝不 500
            raise _error("appraisal_unavailable", "appraisal.unavailable",
                         status=409) from exc

        return {
            "available": True,
            "article_key": key,
            "tool_id": tool_id,
            "tool": tool_def,
            "responses": saved["responses"],
            "comments": saved["comments"],
            "variant": saved["variant"],
            "note": saved["note"],
            "overall": overall,
            "saved": True,
            "updated_at": saved["updated_at"],
        }
