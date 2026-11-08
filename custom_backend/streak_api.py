"""每日打卡 / 提醒后端（2.1.5 Wave C2）——目标设置、状态、连击、ICS、通知。

端点（全部挂在任务下；除 ICS 订阅外的错误体只有
``error_code`` / ``message_key`` / ``params``，界面文案由前端词条渲染）：

- ``GET  /api/tasks/{task_id}/screening/daily-status?screener=``
  本人或负责人可读（默认本人）。返回今日已筛/目标、当前连击、历史最长
  连击、最近 14 天逐日达标布尔、剩余未筛数、提醒设置与建议目标。
- ``GET  /api/tasks/{task_id}/screening/daily-status/suggestion?deadline=``
  只读目标建议（``ceil(剩余未筛数 / 剩余天数)``），**绝不自动写回**设置；
  未提供截止日期时返回 ``suggestion_available=false``。
- ``PUT  /api/tasks/{task_id}/screening/daily-target``
  保存**本人**每日目标（每日篇数 0=未设 / 提醒时间 HH:MM / 提醒开关默认关 /
  可选截止日期与 IANA 时区）。
- ``POST /api/tasks/{task_id}/screening/ics-tokens``
  生成一次性明文订阅令牌（库内只存 SHA-256 摘要）；``GET`` 列本人令牌、
  ``DELETE …/{token_id}`` 撤销。
- ``GET  /api/tasks/{task_id}/screening/daily-status.ics?token=``
  **认证外只读** ICS（未来 90 天每日提醒）；摘要只含数字，绝不含文献内容；
  令牌缺失/超长/无效/撤销/属主已非任务成员一律 404（同一形状）。
- ``GET/PUT /api/tasks/{task_id}/screening/notify/channels``
  负责人级通知通道配置（默认关）；凭据只存环境变量名。
- ``POST /api/tasks/{task_id}/screening/notify/test``
  负责人显式触发一条测试摘要（只发数字）；通道不可用返回结构化结果，
  绝不 500。

落点与幂等：每成员设置 ``{task_dir}/daily_nudge_{slug}.json``（与既有
``al_settings_{slug}.json`` 同口径）；令牌库 ``daily_nudge_tokens.json``；
通道配置 ``daily_nudge_channels.json``。三者都是可加文件，缺失/损坏按默认
值降级（写入用临时文件 + ``os.replace`` 原子替换）。

红线：SQL 全常量字面量 + 只读连接；出站默认关、显式触发、只发数字摘要；
失败优雅降级；本波不做定时调度（ICS 订阅由日历客户端轮询；服务端调度见
契约的下一波/运维 cron 说明）。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import tempfile
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import Body, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from coscreen import ics_feed, notify, streak
from coscreen.streak import DEFAULT_REMIND_TIME, MAX_DAILY_TARGET

__all__ = ["register_streak_routes"]

_REMIND_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_USERNAME_MAX = 64

_DEFAULT_SETTINGS: dict[str, Any] = {
    "daily_target": 0,
    "remind_time": DEFAULT_REMIND_TIME,
    "remind_enabled": False,
    "deadline": "",
    "timezone": "",
}

_DEFAULT_CHANNELS: dict[str, Any] = {
    "version": 1,
    "enabled": False,
    "type": "webhook",
    "url": "",
    "auth_token_env": "",
    "url_env": "",
    "send_key_env": "",
    "host": "",
    "port": 587,
    "from_addr": "",
    "to_addr": "",
    "username": "",
    "password_env": "",
    "use_tls": True,
    "updated_at": "",
}

_REQUIRED_CHANNEL_FIELDS = {
    "webhook": ("url",),
    "serverchan": ("send_key_env",),
    "apprise": ("url_env",),
    "smtp": ("host", "from_addr", "to_addr"),
}

_ENV_FIELDS = ("auth_token_env", "url_env", "send_key_env", "password_env")

#: 令牌库是 JSON 读-改-写；uvicorn 线程池会并发执行同步端点，进程内串行化
#: 避免两个"生成/撤销"互相覆盖（跨进程仍为最后写入者胜出，见契约）。
_TOKEN_LOCK = threading.Lock()


class DailyTargetIn(BaseModel):
    daily_target: int = 0
    remind_time: str = DEFAULT_REMIND_TIME
    remind_enabled: bool = False
    deadline: str = ""
    timezone: str = ""


class ChannelConfigIn(BaseModel):
    enabled: bool = False
    type: str = "webhook"
    url: str = ""
    auth_token_env: str = ""
    url_env: str = ""
    send_key_env: str = ""
    host: str = ""
    port: int = 587
    from_addr: str = ""
    to_addr: str = ""
    username: str = ""
    password_env: str = ""
    use_tls: bool = True


class NotifyTestIn(BaseModel):
    screener: str | None = None


def register_streak_routes(
    app: Any,
    require_user: Callable[..., dict],
    task_or_404: Callable[[str], Any],
    task_visible: Callable[[Any, dict], bool] | None = None,
    screener_db: Callable[..., Any] | None = None,
    require_owner: Callable[[str, dict], Any] | None = None,
) -> None:
    """在 ``app`` 上注册打卡/提醒全部端点（main.py 注册区一行调用）。"""

    # ---- 结构化错误 -------------------------------------------------------
    def _error(status: int, code: str, *, params: dict | None = None) -> HTTPException:
        return HTTPException(status, {
            "error_code": code,
            "message_key": f"daily_nudge.{code.removeprefix('daily_nudge_')}",
            "params": params or {},
        })

    def _info(task_id: str, user: dict):
        info = task_or_404(task_id)
        if task_visible is not None and not task_visible(info, user):
            raise _error(403, "daily_nudge_task_forbidden")
        return info

    def _owner_gate(task_id: str, user: dict) -> None:
        if require_owner is None:
            raise _error(403, "daily_nudge_task_forbidden")
        require_owner(task_id, user)  # 既有属主闸门：非属主 403 / legacy 语义

    def _is_owner(task_id: str, user: dict) -> bool:
        """非属主返回 False；属主不可确认/库损坏等可重试故障照常上抛。"""
        if require_owner is None:
            return False
        try:
            require_owner(task_id, user)
            return True
        except HTTPException as exc:
            if exc.status_code in (401, 403):
                return False
            raise

    def _check_username(value: object) -> str:
        name = value.strip() if isinstance(value, str) else ""
        if (not name or len(name) > _USERNAME_MAX
                or any(ord(ch) < 32 or ch in "/\\\x00" for ch in name)):
            raise _error(400, "daily_nudge_invalid_username")
        return name

    def _resolve_screener(info, requested: str | None, user: dict) -> str:
        """默认本人；他人必须由负责人读取，且必须是任务登记成员。"""
        name = _check_username(requested if requested else user["username"])
        if name != user["username"]:
            _owner_gate(info.task_id, user)
            if name not in info.screeners:
                raise _error(404, "daily_nudge_member_unknown", params={"screener": name})
        return name

    # ---- 文件落点与原子读写 ------------------------------------------------
    def _settings_path(info, screener: str) -> Path:
        slug = _slug(screener)
        path = info.dir / f"daily_nudge_{slug}.json"
        _ensure_within(path, info.dir)
        return path

    def _tokens_path(info) -> Path:
        path = info.dir / "daily_nudge_tokens.json"
        _ensure_within(path, info.dir)
        return path

    def _channels_path(info) -> Path:
        path = info.dir / "daily_nudge_channels.json"
        _ensure_within(path, info.dir)
        return path

    def _slug(screener: str) -> str:
        # 与 tasks.slugify_screener 同口径（延迟导入避免测试环境差异）。
        from coscreen import tasks as tasks_mod

        return tasks_mod.slugify_screener(screener)

    def _ensure_within(path: Path, root: Path) -> None:
        try:
            path.resolve().relative_to(root.resolve())
        except (OSError, ValueError):
            raise _error(400, "daily_nudge_invalid_path") from None

    def _read_json(path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    def _atomic_write(path: Path, payload: Any) -> None:
        fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp",
                                         dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def _save_or_409(path: Path, payload: Any) -> None:
        try:
            _atomic_write(path, payload)
        except OSError:
            raise _error(409, "daily_nudge_storage_unavailable") from None

    # ---- 每成员设置 --------------------------------------------------------
    def _load_settings(info, screener: str) -> tuple[dict[str, Any], bool]:
        settings = dict(_DEFAULT_SETTINGS)
        path = _settings_path(info, screener)
        try:
            stored = _read_json(path)
        except (OSError, ValueError):
            return settings, False
        if stored is None:
            return settings, True
        if not isinstance(stored, dict):
            return settings, False
        target = stored.get("daily_target")
        if type(target) is int and 0 <= target <= MAX_DAILY_TARGET:
            settings["daily_target"] = target
        remind_time = stored.get("remind_time")
        if isinstance(remind_time, str) and _REMIND_TIME_RE.fullmatch(remind_time):
            settings["remind_time"] = remind_time
        if isinstance(stored.get("remind_enabled"), bool):
            settings["remind_enabled"] = stored["remind_enabled"]
        deadline = stored.get("deadline")
        if deadline in ("", None):
            settings["deadline"] = ""
        elif isinstance(deadline, str) and streak.parse_date(deadline):
            settings["deadline"] = deadline
        tz_name = stored.get("timezone")
        if isinstance(tz_name, str) and (not tz_name or streak.resolve_timezone(tz_name)):
            settings["timezone"] = tz_name
        return settings, True

    def _validate_target(body: DailyTargetIn) -> dict[str, Any]:
        target = body.daily_target
        if type(target) is not int or not 0 <= target <= MAX_DAILY_TARGET:
            raise _error(400, "daily_nudge_invalid_daily_target",
                         params={"max": MAX_DAILY_TARGET})
        remind_time = (body.remind_time or "").strip() or DEFAULT_REMIND_TIME
        if not _REMIND_TIME_RE.fullmatch(remind_time):
            raise _error(400, "daily_nudge_invalid_remind_time")
        if not isinstance(body.remind_enabled, bool):
            raise _error(400, "daily_nudge_invalid_remind_enabled")
        deadline = (body.deadline or "").strip()
        if deadline and streak.parse_date(deadline) is None:
            raise _error(400, "daily_nudge_invalid_deadline")
        tz_name = (body.timezone or "").strip()
        if tz_name and streak.resolve_timezone(tz_name) is None:
            raise _error(400, "daily_nudge_invalid_timezone", params={"timezone": tz_name})
        return {
            "daily_target": target,
            "remind_time": remind_time,
            "remind_enabled": body.remind_enabled,
            "deadline": deadline,
            "timezone": tz_name,
        }

    # ---- 只读取数 ----------------------------------------------------------
    def _screener_path(info, screener: str) -> Path:
        if screener_db is None:
            raise _error(409, "daily_nudge_storage_unavailable")
        try:
            return screener_db(info, screener, ensure=False)
        except TypeError:  # 注入方未支持 ensure 关键字（测试替身）
            return screener_db(info, screener)

    def _owner_path(info) -> Path | None:
        """只读解析属主个人库（共享语料总数用）；**绝不建库**。

        直接读协作库的属主记录再拼 ``{slug}.db``，返回存在才用。任何异常
        （锁/损坏/legacy 无属主）都降级为 ``None``——读路径不因属主库失败
        而 500，也不会在公开 ICS 请求里产生写副作用。
        """
        try:
            from coscreen import task_collaboration as collaboration_mod

            store = collaboration_mod.shared_store_path(info.dir)
            owner = collaboration_mod.get_owner(store, task_id=info.task_id)
            if not owner:
                return None
            username = str(owner.get("owner_username") or "")
            if not username:
                return None
            path = info.dir / f"{_slug(username)}.db"
            _ensure_within(path, info.dir)
            return path if path.is_file() else None
        except Exception:  # noqa: BLE001 - 只读路径绝不因属主库故障失败
            return None

    def _member_numbers(info, screener: str) -> tuple[list[str], int, bool]:
        """返回 ``(screened_at 列表, 已筛条数, 读库成功)``；缺库按空处理。"""
        path = _screener_path(info, screener)
        try:
            timestamps = streak.load_decision_timestamps(path)
        except FileNotFoundError:
            return [], 0, True
        except (sqlite3.Error, OSError):
            return [], 0, False
        return timestamps, len(timestamps), True

    def _remaining(info, screener: str, *, use_owner: bool) -> int:
        path = _screener_path(info, screener)
        owner_path = _owner_path(info) if use_owner else None
        return streak.remaining_unscreened(path, owner_path)

    # ---- 状态载荷 ----------------------------------------------------------
    def _status_payload(info, screener: str, *, use_owner: bool = True) -> dict:
        settings, settings_ok = _load_settings(info, screener)
        tz = streak.resolve_timezone(settings["timezone"])
        today = _today(tz)
        timestamps, _decided, db_ok = _member_numbers(info, screener)
        stats = streak.daily_status(
            timestamps, today=today, daily_target=settings["daily_target"], tz=tz)
        try:
            remaining = _remaining(info, screener, use_owner=use_owner)
        except (sqlite3.Error, OSError):
            remaining = 0
            db_ok = False
        suggestion = None
        deadline = streak.parse_date(settings["deadline"])
        if deadline is not None:
            suggestion = streak.suggest_daily_target(
                remaining, streak.remaining_days(deadline, today))
            suggestion["deadline"] = deadline.isoformat()
            suggestion["basis"] = "settings"
        return {
            "available": db_ok,
            "reason": None if db_ok else "screener_db_unavailable",
            "screener": screener,
            "date": today.isoformat(),
            "timezone": settings["timezone"] or None,
            "daily_target": stats["daily_target"],
            "target_set": stats["target_set"],
            "today_screened": stats["today_screened"],
            "today_met": stats["today_met"],
            "streak": stats["streak"],
            "longest_streak": stats["longest_streak"],
            "last_14_days": stats["last_14_days"],
            "remaining_unscreened": remaining,
            "remind_time": settings["remind_time"],
            "remind_enabled": settings["remind_enabled"],
            "deadline": settings["deadline"] or None,
            "settings_available": settings_ok,
            "suggestion": suggestion,
        }

    # ---- 令牌库 ------------------------------------------------------------
    def _load_tokens(info) -> list[dict]:
        stored = _read_json(_tokens_path(info))
        if stored is None:
            return []
        if not isinstance(stored, dict) or not isinstance(stored.get("tokens"), list):
            raise _error(409, "daily_nudge_storage_unavailable")
        return [record for record in stored["tokens"] if isinstance(record, dict)]

    def _token_digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _find_token(records: list[dict], token: str) -> dict | None:
        digest = _token_digest(token)
        for record in records:
            if record.get("revoked_at"):
                continue
            stored = record.get("sha256")
            if isinstance(stored, str) and hmac.compare_digest(digest, stored):
                return record
        return None

    def _ics_member_active(info, record: dict) -> bool:
        """令牌属主是否仍是任务参与者（被移出任务后订阅立即失效）。

        - 有属主记录：按 ``is_participant`` 校验（含 owner）；旧令牌缺
          ``user_id`` 时仅允许属主本人，其余 fail-closed。
        - legacy 无属主记录（desktop 历史任务）：按 task.json 筛选员登记判定，
          与既有 legacy 可见性口径一致。
        - 读取失败（锁/损坏）：认证外端点 fail-closed → False（404）。
        """
        username = record.get("username")
        if not isinstance(username, str) or not username:
            return False
        try:
            from coscreen import task_collaboration as collaboration_mod

            store = collaboration_mod.shared_store_path(info.dir)
            owner = collaboration_mod.get_owner(store, task_id=info.task_id)
            if owner is None:
                return username in info.screeners
            user_id = record.get("user_id")
            if type(user_id) is int and collaboration_mod.is_participant(
                    store, user_id, task_id=info.task_id):
                return True
            return str(owner.get("owner_username") or "") == username
        except Exception:  # noqa: BLE001 - 公开只读源 fail-closed
            return False

    # ---- 端点 --------------------------------------------------------------
    @app.get("/api/tasks/{task_id}/screening/daily-status")
    def daily_status(task_id: str, screener: str | None = None,
                     user: dict = Depends(require_user)) -> dict:
        info = _info(task_id, user)
        target = _resolve_screener(info, screener, user)
        return _status_payload(info, target)

    @app.get("/api/tasks/{task_id}/screening/daily-status/suggestion")
    def daily_target_suggestion(task_id: str, screener: str | None = None,
                                deadline: str | None = None,
                                user: dict = Depends(require_user)) -> dict:
        info = _info(task_id, user)
        target = _resolve_screener(info, screener, user)
        settings, _ok = _load_settings(info, target)
        chosen = (deadline or "").strip() or settings["deadline"]
        parsed = streak.parse_date(chosen) if chosen else None
        if chosen and parsed is None:
            raise _error(400, "daily_nudge_invalid_deadline")
        tz = streak.resolve_timezone(settings["timezone"])
        today = _today(tz)
        try:
            remaining = _remaining(info, target, use_owner=True)
        except (sqlite3.Error, OSError):
            remaining = 0
        if parsed is None:
            return {
                "available": True,
                "suggestion_available": False,
                "reason": "deadline_required",
                "screener": target,
                "deadline": None,
                "basis": None,
                "remaining_unscreened": remaining,
                "remaining_days": None,
                "suggested_target": None,
            }
        days = streak.remaining_days(parsed, today)
        result = streak.suggest_daily_target(remaining, days)
        return {
            "available": True,
            "suggestion_available": True,
            "reason": None,
            "screener": target,
            "deadline": parsed.isoformat(),
            "basis": "query" if (deadline or "").strip() else "settings",
            **result,
        }

    @app.put("/api/tasks/{task_id}/screening/daily-target")
    def save_daily_target(task_id: str, body: DailyTargetIn,
                          user: dict = Depends(require_user)) -> dict:
        info = _info(task_id, user)
        settings = _validate_target(body)
        path = _settings_path(info, user["username"])
        _save_or_409(path, settings)
        return {"saved": True, "settings": settings}

    @app.post("/api/tasks/{task_id}/screening/ics-tokens")
    def create_ics_token(task_id: str, user: dict = Depends(require_user)) -> dict:
        info = _info(task_id, user)
        token = secrets.token_urlsafe(32)
        digest = _token_digest(token)
        token_id = digest[:16]
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with _TOKEN_LOCK:
            records = _load_tokens(info)
            if any(record.get("id") == token_id for record in records):
                token_id = digest[:24]
            records.append({
                "id": token_id,
                "sha256": digest,
                "username": user["username"],
                "user_id": user["user_id"],
                "created_at": created_at,
                "revoked_at": None,
            })
            _save_or_409(_tokens_path(info), {"version": 1, "tokens": records})
        return {
            "token": token,
            "token_id": token_id,
            "created_at": created_at,
            "feed_path": (f"/api/tasks/{info.task_id}/screening/"
                          f"daily-status.ics?token={token}"),
        }

    @app.get("/api/tasks/{task_id}/screening/ics-tokens")
    def list_ics_tokens(task_id: str, user: dict = Depends(require_user)) -> dict:
        info = _info(task_id, user)
        try:
            with _TOKEN_LOCK:
                records = _load_tokens(info)
        except HTTPException:
            return {"tokens": [], "available": False}
        own = [
            {"token_id": record.get("id"), "created_at": record.get("created_at"),
             "revoked_at": record.get("revoked_at")}
            for record in records if record.get("username") == user["username"]
        ]
        return {"tokens": own, "available": True}

    @app.delete("/api/tasks/{task_id}/screening/ics-tokens/{token_id}")
    def revoke_ics_token(task_id: str, token_id: str,
                         user: dict = Depends(require_user)) -> dict:
        info = _info(task_id, user)
        with _TOKEN_LOCK:
            records = _load_tokens(info)
            found = None
            for record in records:
                if record.get("id") == token_id and record.get("username") == user["username"]:
                    found = record
                    break
            if found is None:
                # 负责人可回收本任务内任何成员的订阅令牌；非属主保持 404，
                # 不泄露令牌是否存在（_is_owner 只把 401/403 折叠为 False，
                # 503/409 等可重试故障照常上抛）。
                candidate = next(
                    (record for record in records if record.get("id") == token_id), None)
                if candidate is not None and _is_owner(info.task_id, user):
                    found = candidate
            if found is None:
                raise _error(404, "daily_nudge_token_not_found")
            revoked_at = found.get("revoked_at") or datetime.now(timezone.utc).isoformat(
                timespec="seconds")
            found["revoked_at"] = revoked_at
            _save_or_409(_tokens_path(info), {"version": 1, "tokens": records})
        return {"revoked": True, "token_id": token_id, "revoked_at": revoked_at}

    @app.get("/api/tasks/{task_id}/screening/daily-status.ics")
    def daily_status_ics(task_id: str,
                         token: str = Query("")) -> Response:
        """认证外的只读订阅源：令牌即能力；无效/撤销/属主已非成员一律 404。

        长度在 handler 内判定（不用 Query 校验），保证任何坏令牌都得到同一个
        404 形状，而不是 422。
        """
        if not token or len(token) > 256:
            raise HTTPException(404, "Not Found")
        info = task_or_404(task_id)
        try:
            records = _load_tokens(info)
        except HTTPException:
            records = []
        record = _find_token(records, token)
        if record is None or not _ics_member_active(info, record):
            raise HTTPException(404, "Not Found")
        screener = record.get("username")
        if not isinstance(screener, str) or not screener:
            raise HTTPException(404, "Not Found")
        try:
            payload = _status_payload(info, screener, use_owner=True)
        except Exception:  # noqa: BLE001 - 认证外只读源绝不 500：降级为默认数字
            payload = {
                "date": _today().isoformat(),
                "timezone": None,
                "daily_target": 0,
                "today_screened": 0,
                "remaining_unscreened": 0,
                "streak": 0,
                "longest_streak": 0,
                "remind_time": DEFAULT_REMIND_TIME,
            }
        numbers = {
            "screened_today": payload["today_screened"],
            "daily_target": payload["daily_target"],
            "remaining": payload["remaining_unscreened"],
            "streak": payload["streak"],
            "longest_streak": payload["longest_streak"],
        }
        text = ics_feed.build_daily_ics(
            task_id=info.task_id,
            start=date.fromisoformat(payload["date"]),
            remind_time=payload["remind_time"],
            timezone_name=payload["timezone"] or "",
            numbers=numbers,
            now_utc=datetime.now(timezone.utc),
        )
        return Response(
            content=text,
            media_type="text/calendar; charset=utf-8",
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": 'inline; filename="reviewflow-daily.ics"',
            },
        )

    # ---- 通知通道（负责人级） ---------------------------------------------
    def _load_channels(info) -> tuple[dict[str, Any], bool]:
        config = dict(_DEFAULT_CHANNELS)
        try:
            stored = _read_json(_channels_path(info))
        except (OSError, ValueError):
            return config, False
        if stored is None:
            return config, True
        if not isinstance(stored, dict):
            return config, False
        for key in config:
            if key in stored:
                config[key] = stored[key]
        if config["type"] not in notify.CHANNEL_TYPES:
            config["type"] = "webhook"
        config["enabled"] = stored.get("enabled") is True
        try:
            config["port"] = int(stored.get("port") or 587)
        except (TypeError, ValueError):
            config["port"] = 587
        config["use_tls"] = stored.get("use_tls") is not False
        return config, True

    def _channel_configured(config: dict[str, Any]) -> bool:
        for field in _REQUIRED_CHANNEL_FIELDS.get(config.get("type", ""), ()):
            value = config.get(field)
            if not isinstance(value, str) or not value.strip():
                return False
        return True

    def _public_view(config: dict[str, Any], available: bool) -> dict[str, Any]:
        view = {key: config[key] for key in _DEFAULT_CHANNELS}
        view["configured"] = _channel_configured(config)
        view["available"] = available
        return view

    def _validate_channel(body: ChannelConfigIn) -> dict[str, Any]:
        channel_type = (body.type or "").strip()
        if channel_type not in notify.CHANNEL_TYPES:
            raise _error(400, "daily_nudge_channel_type_invalid",
                         params={"type": channel_type})
        config = dict(_DEFAULT_CHANNELS)
        config.update({
            "enabled": bool(body.enabled),
            "type": channel_type,
            "url": (body.url or "").strip(),
            "auth_token_env": (body.auth_token_env or "").strip(),
            "url_env": (body.url_env or "").strip(),
            "send_key_env": (body.send_key_env or "").strip(),
            "host": (body.host or "").strip(),
            "port": int(body.port or 587),
            "from_addr": (body.from_addr or "").strip(),
            "to_addr": (body.to_addr or "").strip(),
            "username": (body.username or "").strip(),
            "password_env": (body.password_env or "").strip(),
            "use_tls": bool(body.use_tls),
        })
        for field in _ENV_FIELDS:
            value = config[field]
            if value and not _ENV_NAME_RE.fullmatch(value):
                raise _error(400, "daily_nudge_channel_env_invalid",
                             params={"field": field})
        if not 1 <= config["port"] <= 65535:
            raise _error(400, "daily_nudge_channel_incomplete",
                         params={"field": "port"})
        if config["enabled"]:
            for field in _REQUIRED_CHANNEL_FIELDS[channel_type]:
                if not str(config.get(field) or "").strip():
                    raise _error(400, "daily_nudge_channel_incomplete",
                                 params={"field": field})
        if channel_type == "webhook" and config["url"]:
            try:
                notify.validate_public_url(config["url"])
            except notify.NotifyError as exc:
                raise _error(400, "daily_nudge_channel_url_invalid",
                             params={"reason": exc.code}) from None
        if channel_type == "smtp" and config["host"] and config["enabled"]:
            try:
                notify.validate_public_hostname(config["host"], port=config["port"])
            except notify.NotifyError as exc:
                raise _error(400, "daily_nudge_channel_url_invalid",
                             params={"reason": exc.code}) from None
        config["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return config

    @app.get("/api/tasks/{task_id}/screening/notify/channels")
    def get_notify_channels(task_id: str, user: dict = Depends(require_user)) -> dict:
        info = _info(task_id, user)
        _owner_gate(task_id, user)
        config, available = _load_channels(info)
        return _public_view(config, available)

    @app.put("/api/tasks/{task_id}/screening/notify/channels")
    def save_notify_channels(task_id: str, body: ChannelConfigIn,
                             user: dict = Depends(require_user)) -> dict:
        info = _info(task_id, user)
        _owner_gate(task_id, user)
        config = _validate_channel(body)
        _save_or_409(_channels_path(info), config)
        return {"saved": True, "channels": _public_view(config, True)}

    @app.post("/api/tasks/{task_id}/screening/notify/test")
    def test_notify_channel(task_id: str, body: NotifyTestIn | None = Body(default=None),
                            user: dict = Depends(require_user)) -> dict:
        info = _info(task_id, user)
        _owner_gate(task_id, user)
        config, _available = _load_channels(info)
        if not config.get("enabled"):
            raise _error(409, "daily_nudge_channel_disabled")
        screener = _check_username((body.screener if body else None) or user["username"])
        if screener != user["username"] and screener not in info.screeners:
            raise _error(404, "daily_nudge_member_unknown", params={"screener": screener})
        payload = _status_payload(info, screener)
        summary = notify.build_summary(
            {
                "screened_today": payload["today_screened"],
                "daily_target": payload["daily_target"],
                "remaining": payload["remaining_unscreened"],
                "streak": payload["streak"],
                "longest_streak": payload["longest_streak"],
            },
            event_code=2,  # 2 = 手工测试摘要
            day=_today(streak.resolve_timezone(payload["timezone"] or "")),
        )
        result = notify.send(config, summary)
        return {
            "ok": result["ok"],
            "status": result["status"],
            "channel": result["channel"],
            "reason": result["reason"],
            "screener": screener,
            "summary": summary,
        }


#: 测试注入点：返回"今天"（默认按目标时区取本地日）。
def _today(tz=None) -> date:
    return streak.today_in(tz)
