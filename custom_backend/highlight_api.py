"""摘要关键词高亮 API（2.1.5 Wave D2）——设置存储 + 规则计算契约。

三个端点（全部挂在任务下，复用 ``require_user`` + 任务可见性闸门）：

- ``GET  /api/tasks/{task_id}/highlight/sets``
  返回**本人**的关键词组与激活组（每成员独立；默认空）。
- ``PUT  /api/tasks/{task_id}/highlight/sets``
  声明式整组覆盖：``{"sets": [{"set_id"?, "name", "terms": [...]}, ...],
  "active_set_id": ...}``。同一请求体重复提交结果完全一致（set_id 由
  ``(序号, 组名, 词表)`` 哈希派生），因此幂等；``active_set_id`` 必须是
  本次提交中存在的组。
- ``POST /api/tasks/{task_id}/highlight/compute``
  二选一输入：``{"text": "..."}``，或 ``{"article_key": "...",
  "field": "title"|"abstract"}``（服务端从题录权威库取该字段原文再计算）。
  使用本人**激活组**的词表；返回逐词条区间（码点偏移 + UTF-8 字节偏移）、
  实际使用的词条图例与原文回显，前端只按范围渲染 ``<mark>``。

存储（可加、幂等、与决策同库）：个人库 ``{task_dir}/{slug}.db`` 新增两张
表——``keyword_highlight_sets``（组）与 ``keyword_highlight_state``（单行，
激活组）。旧版程序不查询即忽略；缺失表/缺库视为「默认空」，绝不因只读
GET 产生建库/建表副作用。

治理红线：

- SQL 全部常量字面量 + ``?`` 参数绑定，无拼接/format/f-string。
- 读设置走 ``mode=ro``（不建库、不跑自愈 DDL）；锁忙 → 503 结构化错误，
  永久损坏 → 200 + ``available=false``（不 500、不冒充成空设置覆盖）。
- 写失败 / 路径不可用 → 409 结构化错误；校验失败 → 400；未知文献 → 404。
- 错误体只有 ``error_code`` / ``message_key`` / ``params``，界面文案由
  前端词条渲染。
- 资源红线（终审 #11/#54）：词条保存前按**匹配等价**判定去重
  （``core.term_dedup_key`` 分组 + ``core.terms_match_identically`` 校验，
  与 ``_term_pattern`` 的词边界判定同源），等价同词只存一次；compute 对
  候选命中总数设硬预算 ``core.MAX_CANDIDATE_MATCHES``，超限 → 400
  ``highlight_too_many_matches``。
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

from fastapi import Body, Depends, HTTPException

from coscreen import db as db_mod
from coscreen import highlight as core

__all__ = ["register_highlight_routes"]

#: 本模块在个人库中新增的两张表（可加、幂等；旧版忽略）。
_SCHEMA = """
CREATE TABLE IF NOT EXISTS keyword_highlight_sets (
  set_id     TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  terms_json TEXT NOT NULL,
  position   INTEGER NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS keyword_highlight_state (
  singleton     INTEGER PRIMARY KEY CHECK (singleton = 1),
  active_set_id TEXT NOT NULL DEFAULT '',
  updated_at    TEXT NOT NULL
);
"""

#: 客户端自带 set_id 的形状（服务端派生 id 也满足该形状）。
_SET_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: 可高亮的题录字段（其余字段没有叙述文本，不开放）。
_FIELDS = ("title", "abstract")

_MAX_ARTICLE_KEY = 128
_READ_TIMEOUT = 5.0
_WRITE_TIMEOUT = 10.0


def _has_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _has_surrogate(value: str) -> bool:
    """孤立代理项（U+D800–U+DFFF）：sqlite 绑定/UTF-8 响应编码都会炸，提前拒绝。"""
    return any(0xD800 <= ord(char) <= 0xDFFF for char in value)


def _strip_surrogates(value: str) -> str:
    """题录原文里的孤立代理项替换为 U+FFFD：保证回显永不编码失败（不 500）。"""
    if not _has_surrogate(value):
        return value
    return "".join("\ufffd" if 0xD800 <= ord(char) <= 0xDFFF else char
                   for char in value)


def _derive_set_id(index: int, name: str, terms: list[str]) -> str:
    """由 ``(序号, 组名, 词表)`` 派生稳定 id：同一 PUT 重复提交 id 不变。"""
    material = str(index) + "\x1f" + name + "\x1f" + "\x1f".join(terms)
    return "hs_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def register_highlight_routes(
    app: Any,
    require_user: Callable[..., dict],
    task_or_404: Callable[[str], Any],
    task_visible: Callable[[Any, dict], bool] | None = None,
    screener_db: Callable[..., Any] | None = None,
) -> None:
    """在 ``app`` 上注册高亮设置/计算端点（main.py 注册区一行调用）。"""

    # ---- 结构化错误 -------------------------------------------------------
    def _error(status: int, code: str, params: dict | None = None) -> HTTPException:
        return HTTPException(status, {
            "error_code": code,
            "message_key": "highlight." + code.removeprefix("highlight_"),
            "params": params or {},
        })

    def _info(task_id: str, user: dict):
        info = task_or_404(task_id)
        if task_visible is not None and not task_visible(info, user):
            raise _error(403, "highlight_task_forbidden")
        return info

    # ---- 个人库路径 -------------------------------------------------------
    def _db_path(info, username: str, *, ensure: bool) -> Path:
        if screener_db is None:  # 注册方未注入个人库解析器：显式 409，绝不 500
            raise _error(409, "highlight_storage_unavailable")
        try:
            return Path(screener_db(info, username, ensure=ensure))
        except TypeError:  # 注入方为旧签名（无 ensure 形参）
            return Path(screener_db(info, username))

    # ---- 响应形状 ---------------------------------------------------------
    def _limits() -> dict:
        return {
            "max_sets": core.MAX_SETS,
            "max_terms_per_set": core.MAX_TERMS_PER_SET,
            "max_term_length": core.MAX_TERM_LENGTH,
            "max_set_name_length": core.MAX_SET_NAME_LENGTH,
            "max_text_length": core.MAX_TEXT_LENGTH,
        }

    def _state_payload(sets: list[dict], active: str | None,
                       updated_at: str, *, available: bool = True,
                       reason: str = "") -> dict:
        payload = {
            "available": available,
            "sets": sets,
            "active_set_id": active,
            "set_count": len(sets),
            "encoding": core.ENCODING,
            "limits": _limits(),
            "updated_at": updated_at,
        }
        if reason:
            payload["reason"] = reason
        return payload

    # ---- 只读加载 ---------------------------------------------------------
    def _load_state(info, username: str) -> dict:
        path = _db_path(info, username, ensure=False)
        if not path.is_file():
            return _state_payload([], None, "")
        try:
            uri = path.resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True,
                                         timeout=_READ_TIMEOUT)) as conn:
                rows = conn.execute(
                    "SELECT set_id, name, terms_json, position, updated_at "
                    "FROM keyword_highlight_sets ORDER BY position, set_id"
                ).fetchall()
                state = conn.execute(
                    "SELECT active_set_id, updated_at FROM keyword_highlight_state "
                    "WHERE singleton = 1"
                ).fetchone()
        except sqlite3.OperationalError as exc:
            detail = str(exc).casefold()
            if "no such table" in detail:
                return _state_payload([], None, "")
            if "locked" in detail or "busy" in detail:
                raise _error(503, "highlight_storage_unavailable") from exc
            # 其余不可读（权限/磁盘）——降级为不可用，不让 GET 变 500。
            return _state_payload([], None, "", available=False,
                                  reason="storage_unreadable")
        except sqlite3.DatabaseError:
            # 永久损坏：明确告知不可用（前端据此禁用编辑），而非冒充空设置。
            return _state_payload([], None, "", available=False,
                                  reason="storage_corrupt")
        except OSError as exc:
            raise _error(503, "highlight_storage_unavailable") from exc

        sets: list[dict] = []
        for row in rows:
            try:
                parsed = json.loads(row[2]) if row[2] else []
            except ValueError:
                parsed = []
            terms = [term for term in parsed if isinstance(term, str)] \
                if isinstance(parsed, list) else []
            sets.append({
                "set_id": row[0],
                "name": row[1],
                "terms": terms,
                "term_count": len(terms),
                "position": row[3],
                "updated_at": row[4],
            })
        active = (state[0] or None) if state else None
        known_ids = {item["set_id"] for item in sets}
        if active is not None and active not in known_ids:
            active = None  # 悬空激活组按未激活处理（不报错、不 500）
        updated_at = (state[1] if state else "") or ""
        return _state_payload(sets, active, updated_at)

    # ---- 校验 -------------------------------------------------------------
    def _validate_sets_body(body: Any) -> tuple[list[dict], str | None]:
        if not isinstance(body, dict):
            raise _error(400, "highlight_body_invalid")
        raw_sets = body.get("sets")
        if raw_sets is None:
            raw_sets = []
        if not isinstance(raw_sets, list):
            raise _error(400, "highlight_sets_invalid")
        if len(raw_sets) > core.MAX_SETS:
            raise _error(400, "highlight_sets_too_many", {"max": core.MAX_SETS})

        cleaned: list[dict] = []
        seen_ids: set[str] = set()
        for index, entry in enumerate(raw_sets):
            if not isinstance(entry, dict):
                raise _error(400, "highlight_set_invalid", {"set_index": index})
            name = entry.get("name")
            if not isinstance(name, str):
                raise _error(400, "highlight_set_name_invalid",
                             {"set_index": index})
            name = name.strip()
            if (not name or len(name) > core.MAX_SET_NAME_LENGTH
                    or _has_control(name) or _has_surrogate(name)):
                raise _error(400, "highlight_set_name_invalid",
                             {"set_index": index,
                              "max_chars": core.MAX_SET_NAME_LENGTH})

            raw_terms = entry.get("terms")
            if raw_terms is None:
                raw_terms = []
            if not isinstance(raw_terms, list):
                raise _error(400, "highlight_terms_invalid", {"set_index": index})
            if len(raw_terms) > core.MAX_TERMS_PER_SET:
                raise _error(400, "highlight_terms_too_many",
                             {"set_index": index,
                              "max": core.MAX_TERMS_PER_SET})
            terms: list[str] = []
            seen_terms: dict[tuple, list[str]] = {}
            for raw_term in raw_terms:
                if not isinstance(raw_term, str):
                    raise _error(400, "highlight_term_invalid",
                                 {"set_index": index})
                term = raw_term.strip()
                if (not term or len(term) > core.MAX_TERM_LENGTH
                        or _has_control(term) or _has_surrogate(term)):
                    raise _error(400, "highlight_term_invalid",
                                 {"set_index": index,
                                  "max_chars": core.MAX_TERM_LENGTH})
                # 同词只存一次（终审 #11），但只合并**匹配语义完全等价**的
                # 词条（core.terms_match_identically：词边界标志 + 小写折叠
                # + 忽略大小写双向全匹配，与 _term_pattern 同源）。纯 lower
                # 键会把 U+212A KELVIN / U+0130 这类 lower 相同而词边界语义
                # 不同的近形词误合并，改变命中结果（评审更正）；等价组合并
                # 保留首次出现（贪心并列取 term_id 最小 = 首次出现，结果与
                # 未去重基线逐段一致）。
                peers = seen_terms.setdefault(core.term_dedup_key(term), [])
                if any(core.terms_match_identically(peer, term)
                       for peer in peers):
                    continue
                peers.append(term)
                terms.append(term)

            set_id = entry.get("set_id")
            if set_id is None or set_id == "":
                set_id = _derive_set_id(index, name, terms)
            elif not isinstance(set_id, str) or not _SET_ID_RE.fullmatch(set_id):
                raise _error(400, "highlight_set_id_invalid",
                             {"set_index": index})
            if set_id in seen_ids:
                raise _error(400, "highlight_set_id_duplicate",
                             {"set_id": set_id})
            seen_ids.add(set_id)
            cleaned.append({"set_id": set_id, "name": name, "terms": terms})

        active = body.get("active_set_id")
        if active is None or active == "":
            active = None
        elif not isinstance(active, str) or active not in seen_ids:
            raise _error(400, "highlight_active_set_unknown",
                         {"active_set_id": active})
        return cleaned, active

    # ---- 写入 -------------------------------------------------------------
    def _save_state(info, username: str, sets: list[dict],
                    active: str | None) -> str:
        path = _db_path(info, username, ensure=True)
        updated_at = db_mod._now()
        try:
            with closing(sqlite3.connect(path, timeout=_WRITE_TIMEOUT)) as conn:
                conn.executescript(_SCHEMA)  # 幂等建表（可加）
                try:
                    conn.execute("DELETE FROM keyword_highlight_sets")
                    for position, item in enumerate(sets):
                        conn.execute(
                            "INSERT INTO keyword_highlight_sets "
                            "(set_id, name, terms_json, position, updated_at) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (item["set_id"], item["name"],
                             json.dumps(item["terms"], ensure_ascii=False),
                             position, updated_at),
                        )
                    conn.execute("DELETE FROM keyword_highlight_state")
                    conn.execute(
                        "INSERT INTO keyword_highlight_state "
                        "(singleton, active_set_id, updated_at) VALUES (1, ?, ?)",
                        (active or "", updated_at),
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
        except (sqlite3.Error, OSError) as exc:
            raise _error(409, "highlight_storage_unavailable") from exc
        return updated_at

    def _saved_payload(sets: list[dict], active: str | None,
                       updated_at: str) -> dict:
        return _state_payload([
            {"set_id": item["set_id"], "name": item["name"],
             "terms": list(item["terms"]), "term_count": len(item["terms"]),
             "position": position, "updated_at": updated_at}
            for position, item in enumerate(sets)
        ], active, updated_at)

    # ---- 题录取文本 -------------------------------------------------------
    def _find_article(info, user: dict, key: str):
        """按题录权威库找文献：属主库优先，再退回本人库（只拼路径，不建库）。

        返回 ``(article | None, storage_error)``；只有「确实读不到」才返回
        storage_error=True，交给调用方映射 503，避免把锁库误报成 404。
        """
        owner_name: str | None = None
        try:
            from coscreen import task_collaboration as collaboration_mod

            owner = collaboration_mod.get_owner(
                collaboration_mod.shared_store_path(info.dir),
                task_id=info.task_id,
            )
            if owner is not None:
                owner_name = str(owner["owner_username"])
        except Exception:  # noqa: BLE001 —— 属主解析失败退回本人库
            owner_name = None

        candidates: list[Path] = []
        seen: set[Path] = set()
        for name in (owner_name, user["username"]):
            if not name:
                continue
            try:
                # ensure=False：只拼路径，不为「找文章」新建个人库。
                path = _db_path(info, name, ensure=False)
            except HTTPException:
                continue
            except Exception:  # noqa: BLE001
                continue
            if path not in seen:
                seen.add(path)
                candidates.append(path)

        storage_error = False
        for path in candidates:
            if not path.is_file():
                continue
            try:
                article = db_mod.get_article(path, key)
            except (sqlite3.Error, OSError):
                storage_error = True
                continue
            if article is not None:
                return article, False
        return None, storage_error

    def _active_terms(info, user: dict) -> dict:
        """本人激活组的词表；设置库锁忙/损坏时降级为空组（compute 仍可用）。"""
        try:
            state = _load_state(info, user["username"])
        except HTTPException as exc:
            if exc.status_code == 503:
                return {"set_id": None, "set_name": "", "terms": [],
                        "available": False}
            raise
        if not state.get("available", True):
            return {"set_id": None, "set_name": "", "terms": [],
                    "available": False}
        active = state.get("active_set_id")
        if not active:
            return {"set_id": None, "set_name": "", "terms": [],
                    "available": True}
        for item in state["sets"]:
            if item["set_id"] == active:
                return {"set_id": active, "set_name": item["name"],
                        "terms": list(item["terms"]), "available": True}
        return {"set_id": None, "set_name": "", "terms": [], "available": True}

    # ---- 1. 读取设置 ------------------------------------------------------
    @app.get("/api/tasks/{task_id}/highlight/sets")
    def highlight_sets_get(task_id: str,
                           user: dict = Depends(require_user)) -> dict:
        info = _info(task_id, user)
        return _load_state(info, user["username"])

    # ---- 2. 整组覆盖（幂等） ----------------------------------------------
    @app.put("/api/tasks/{task_id}/highlight/sets")
    def highlight_sets_put(task_id: str, body: Any = Body(...),
                           user: dict = Depends(require_user)) -> dict:
        info = _info(task_id, user)
        sets, active = _validate_sets_body(body)
        updated_at = _save_state(info, user["username"], sets, active)
        return _saved_payload(sets, active, updated_at)

    # ---- 3. 计算 ----------------------------------------------------------
    @app.post("/api/tasks/{task_id}/highlight/compute")
    def highlight_compute(task_id: str, body: Any = Body(...),
                          user: dict = Depends(require_user)) -> dict:
        info = _info(task_id, user)
        if not isinstance(body, dict):
            raise _error(400, "highlight_body_invalid")

        text_value = body.get("text")
        article_key = body.get("article_key")
        field = body.get("field")
        has_text = text_value is not None
        has_article = article_key is not None or field is not None
        if has_text and has_article:
            raise _error(400, "highlight_input_conflict")
        if not has_text and not has_article:
            raise _error(400, "highlight_text_required")

        if has_text:
            if not isinstance(text_value, str) or _has_surrogate(text_value):
                raise _error(400, "highlight_text_invalid")
            if len(text_value) > core.MAX_TEXT_LENGTH:
                raise _error(400, "highlight_text_too_large",
                             {"max_chars": core.MAX_TEXT_LENGTH})
            source, text, key, field_name = "text", text_value, None, None
        else:
            if not isinstance(article_key, str) or not article_key.strip():
                raise _error(400, "highlight_article_key_invalid")
            key = article_key.strip()
            if len(key) > _MAX_ARTICLE_KEY or _has_control(key) or _has_surrogate(key):
                raise _error(400, "highlight_article_key_invalid")
            if not isinstance(field, str) or not field.strip():
                raise _error(400, "highlight_field_required")
            field_name = field.strip().lower()
            if field_name not in _FIELDS:
                raise _error(400, "highlight_field_unknown",
                             {"fields": list(_FIELDS)})
            article, storage_error = _find_article(info, user, key)
            if article is None:
                if storage_error:
                    raise _error(503, "highlight_storage_unavailable")
                raise _error(404, "highlight_article_unknown",
                             {"article_key": key})
            value = getattr(article, field_name, "")
            text = _strip_surrogates(value) if isinstance(value, str) else ""
            source = "article"

        selected = _active_terms(info, user)
        terms = selected["terms"]
        try:
            ranges = core.compute_highlights(text, terms)
        except core.HighlightMatchBudgetExceeded as exc:
            # 候选命中总数超硬预算（词条数 × 命中数乘积放大，终审 #11）：
            # 结构化 400，文案走 message_key，params 携带 limit/count。
            raise _error(400, "highlight_too_many_matches",
                         {"limit": exc.limit, "count": exc.count}) from exc
        return {
            "available": True,
            "source": source,
            "article_key": key,
            "field": field_name,
            "text": text,
            "text_length": len(text),
            "encoding": core.ENCODING,
            "set_id": selected["set_id"],
            "set_name": selected["set_name"],
            "settings_available": selected["available"],
            "terms": [{"term_id": index, "term": term}
                      for index, term in enumerate(terms)],
            "ranges": ranges,
            "range_count": len(ranges),
        }
