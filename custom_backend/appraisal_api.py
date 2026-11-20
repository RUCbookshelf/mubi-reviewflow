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

import hashlib
import json
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

from fastapi import Body, Depends, HTTPException, Query, Response

from coscreen import appraisal as core

__all__ = ["prepare_appraisal_storage_for", "register_appraisal_routes"]


def prepare_appraisal_storage_for(
    info: Any, username: str, screener_db: Callable[..., Any],
) -> Path:
    """P17 明确准备生命周期：在**写窗口外**为成员个人库幂等建评价存储。

    2.1.6 P17 需求 1：appraisal storage prepare 从 PUT 写窗内移出到独立的
    准备步骤——宿主（成员准备/角色授予流程，P18/P24 接线）在业务写发生前
    调用本函数，在自己的短连接里执行 ``ensure_appraisal_storage``（幂等
    DDL）。PUT 的 team 写窗内**不再** ``_connect``+``ensure``：窗口只做
    「表已准备」纯读检查，未准备时如实 409（``appraisal.storage_unavailable``），
    绝不在保留窗口里悄悄建表。

    personal/legacy 任务的旧 PUT 行为不变（窗口外自备连接、原地幂等建表），
    旧宿主不调用本函数也不受影响。
    """
    from coscreen import db as db_mod

    if screener_db is None:
        raise RuntimeError(
            "prepare_appraisal_storage_for requires the host screener_db resolver")
    path = Path(screener_db(info, username))
    with closing(db_mod._connect(path)) as conn, conn:
        core.ensure_appraisal_storage(conn)
    return path


def register_appraisal_routes(
    app: Any,
    require_user: Callable[..., dict],
    task_or_404: Callable[[str], Any],
    task_visible: Callable[[Any, dict], bool] | None = None,
    screener_db: Callable[..., Any] | None = None,
    team_role_gate: Callable[[Any, dict], None] | None = None,
    workflow_runtime: Any = None,
) -> None:
    """在 ``app`` 上注册评价四端点（main.py 注册区单行调用）。

    ``team_role_gate``（2.1.6 FLOW II-A，可选注入）：team 任务的评价写=
    编码者业务（PUT 前调用；None=宿主未注入，行为与旧版一致——独立注册/
    测试桩兼容）。GET（读取/汇总）不加门，历史与可见性口径不变。

    ``workflow_runtime``（2.1.6 P17，可选）：注入 ``WorkflowRuntime`` 后，
    team 任务的评价 PUT 改走 P15 ``runtime.mutate`` 真实 team 门
    （``member_appraisal``：coder 角色 + 两阶段链，**不加** paused/source/
    expected 门——C08 addendum 口径）。未注入时按 ``app.state.review_workflow``
    惰性解析；两者都缺省则保持 FLOW II-A ``team_role_gate`` 旧路径，
    personal/legacy 任务始终走旧路径（零变化）。"""

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

    # -- P17 team 写路径辅助 ---------------------------------------------------
    def _workflow_runtime():
        """P15 runtime 惰性解析：显式注入优先，其次 app.state.review_workflow。"""
        if workflow_runtime is not None:
            return workflow_runtime
        return getattr(app.state, "review_workflow", None)

    def _is_team_task(runtime, task_id: str) -> bool:
        """team 判定只认可信派生（P15 ``is_team`` 口径）；解析失败退旧路径。"""
        try:
            return bool(runtime.is_team(task_id))
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001 —— 无协作库/旧宿主：保持 legacy 行为
            return False

    def _appraisal_table_ready(conn) -> bool:
        """纯读检查：成员库是否已由准备生命周期建好 ``appraisal_records``。"""
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            ("appraisal_records",),
        ).fetchone()
        return row is not None

    def _require_same_member_store(conn, path: Path, key: str) -> None:
        """保留连接与宿主解析的成员库路径必须是同一文件（漂移=fail-closed）。"""
        main_file = next((row[2] for row in conn.execute("PRAGMA database_list")
                          if row[1] == "main"), None)
        try:
            same = (main_file is not None
                    and Path(main_file).resolve(strict=True)
                    .samefile(Path(path).resolve(strict=True)))
        except OSError:
            same = False
        if not same:
            raise _error("appraisal_storage_mismatch",
                         "appraisal.storage_unavailable", status=409,
                         params={"article_key": key})

    def _team_unknown_outcome_http(envelope) -> HTTPException:
        """S2：``outcome != 'committed'`` 的 mutate 信封 → 显式结构化 409。

        unknown（提交结果不可判定，可能已提交）绝不落进兜底 409 或 null：
        与 coding_consensus 路由同一 typed 载荷 + 可重试提示；幂等 receipt
        保证用相同 request_id 重试安全。
        """
        request_id = envelope.get("request_id") if isinstance(envelope, dict) else None
        details = (envelope.get("error") if isinstance(envelope, dict) else None
                   ) or "commit outcome could not be determined"
        return HTTPException(409, {
            "code": "team_flow_unknown_outcome",
            "message_key": "team.flow.unknown_outcome",
            "params": {"request_id": request_id},
            "details": details,
            "message": "提交结果暂不可判定（可能已提交）；请使用相同请求编号重试以确认结果。",
            "retryable": True,
        })

    def _team_drift_http(task_id: str, exc: Exception) -> HTTPException:
        """S4：模式漂移/窗口竞争的裸异常 → 结构化可重试 409（P15 同码）。"""
        return HTTPException(409, {
            "code": "task_mode_changed",
            "message_key": "team.flow.task_mode_changed",
            "params": {"task_id": task_id},
            "details": f"{type(exc).__name__}: {exc}",
            "message": "任务协作模式已变化，请重试本次操作。",
            "retryable": True,
        })

    def _save_via_team_window(
        runtime, task_id: str, user: dict, key: str, tool_id: str, *,
        normalized, clean_comments, chosen, stored_overall, note: str, path: Path,
        headers_out: dict | None = None,
    ):
        """team 评价写：P15 ``mutate`` + ``member_appraisal``（actor 目标）。

        窗口内零 ``_connect``/零 DDL：存储必须已由
        :func:`prepare_appraisal_storage_for` 在窗口外准备好；写经保留的
        ``actor_conn``（与检查/提交目标同一对象），业务返回保持旧形状。
        ``member_appraisal`` 的 op spec 是 policy=False（只加两阶段链），
        本路径不追加 paused/source/expected 任何额外门（C08 addendum）。
        unknown outcome（S2）与漂移竞态（S4）显式结构化上抛，绝不吞。
        """
        from coscreen.team_flow_types import TeamOperation

        operation = TeamOperation(name="member_appraisal", stage="coding")

        def writer(ctx):
            inputs = ctx.write_inputs
            conn = inputs.actor_conn  # member_appraisal 唯一目标=actor
            _require_same_member_store(conn, path, key)
            if not _appraisal_table_ready(conn):
                # 未走准备生命周期：如实 409，绝不在写窗内建表。
                raise _error("appraisal_storage_unavailable",
                             "appraisal.storage_unavailable", status=409,
                             params={"article_key": key})
            return core.save_record(
                conn, key, tool_id,
                responses=normalized,
                comments=clean_comments,
                variant=chosen,
                overall=stored_overall,
                note=note,
            )

        request_sha256 = hashlib.sha256(json.dumps(
            {"article_key": key, "tool": tool_id, "responses": normalized,
             "comments": clean_comments, "variant": chosen, "note": note},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            default=str).encode("utf-8")).hexdigest()
        try:
            envelope = runtime.mutate(
                task_id, user, operation=operation, item_keys=(key,),
                request_id=f"appraisal:{uuid.uuid4().hex}",
                request_sha256=request_sha256, writer=writer)
        except HTTPException:
            raise  # P12/P15 结构化拒绝原样透传
        except (sqlite3.Error, OSError):
            raise  # 存储错误交由外层映射 appraisal_storage_unavailable（同 legacy）
        except Exception as exc:  # noqa: BLE001 —— S4：漂移竞态绝不裸 500
            raise _team_drift_http(task_id, exc) from exc
        if not isinstance(envelope, dict) or envelope.get("outcome") != "committed":
            raise _team_unknown_outcome_http(envelope)
        # P24/S3：P15 信封头（X-Request-Id/X-Receipt-Id/X-Idempotent-Replay）
        # 交路由层透传到真实 HTTP 响应；仅 committed 才附。
        if headers_out is not None and isinstance(envelope.get("headers"), dict):
            headers_out.update(envelope["headers"])
        result = envelope.get("result")
        if isinstance(result, dict) and "writer" in result:
            return result["writer"]  # team envelope：receipt 结果包着 writer 原结果
        return result

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
        response: Response = None,
    ) -> dict:
        info = _info(task_id, user)
        # 2.1.6 P17：team 任务的评价写优先走 P15 mutate 真实 team 门
        # （member_appraisal：coder 角色+两阶段链，不加额外 policy 门）。
        # runtime 不可用/非 team 任务保持 FLOW II-A team_role_gate 旧行为，
        # personal/legacy 零变化。
        runtime = _workflow_runtime()
        via_team = runtime is not None and _is_team_task(runtime, task_id)
        if not via_team and team_role_gate is not None:
            team_role_gate(info, user)
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
        member_receipt_headers: dict = {}
        if via_team:
            # P17 需求 1：team 写窗内不 _connect+ensure——存储由
            # prepare_appraisal_storage_for 在窗口外准备；写经保留
            # actor_conn（与检查/提交目标同一对象）。
            try:
                saved = _save_via_team_window(
                    runtime, task_id, user, key, tool_id,
                    normalized=normalized, clean_comments=clean_comments,
                    chosen=chosen, stored_overall=stored_overall, note=note,
                    path=Path(_db_path(info, user)),
                    headers_out=member_receipt_headers)
            except HTTPException:
                # P12/P13/P15 结构化拒绝（链/角色/版本等载荷）原样透传。
                raise
            except (sqlite3.Error, OSError) as exc:
                raise _error("appraisal_storage_unavailable",
                             "appraisal.storage_unavailable", status=409,
                             params={"article_key": key}) from exc
            except Exception as exc:  # noqa: BLE001 —— 兜底：绝不 500
                raise _error("appraisal_unavailable", "appraisal.unavailable",
                             status=409) from exc
        else:
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

        if member_receipt_headers and response is not None:
            # S3：team 分支的 P15 信封头透传（solo 分支 headers 恒空、零变化）。
            for name, value in member_receipt_headers.items():
                response.headers[name] = str(value)
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
