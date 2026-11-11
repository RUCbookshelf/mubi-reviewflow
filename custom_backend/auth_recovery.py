# -*- coding: utf-8 -*-
"""找回密码重置码库（2.1.5 记住我+找回密码，用户裁定 2026-11-11 双模式）。

存储：``{DATA_DIR}/auth_reset_codes.json``——sha256 落盘（明文码只在签发瞬间
出现一次）、15 分钟 TTL、每用户名最多 5 枚活码、进程内 ``_LOCK`` 串行读改写
+ 临时文件 ``os.replace`` 原子落盘、文件损坏 fail-closed（拒绝一切消费）。
模式照抄 streak_api 的订阅令牌库（2026-11-11 刚加固的口径）。

签发通道（不经 HTTP，防未认证枚举）：桌面=``build/windows_start.py
--reset-user``（弹窗显示）；服务器=管理员跑 ``build/scripts/reset_code.py``
（stdout）。消费：``POST /api/auth/reset``（见 main.py）。

安全口径：不存在用户名的消费走 ``dummy_verify`` 等时比对（对齐
authenticate 的哑哈希等化）；消费一次性（弹出即删）；重置成功后由
``auth.set_password`` 把 pwd_version+1 即时吊销全部长时令牌。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path

from custom_backend import auth

#: 重置码有效期：15 分钟（管理员/本机转交窗口足够，短窗压泄露面）。
RESET_CODE_TTL_SECONDS = 15 * 60
#: 每用户名同时活跃（未过期未消费）上限。
RESET_CODES_PER_USER = 5
#: 表内最多保留的条目数（含过期未清扫的），防无界增长。
_MAX_RECORDS = 512

_LOCK = threading.Lock()


def _codes_path() -> Path:
    return auth.data_dir() / "auth_reset_codes.json"


def _digest(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _load(path: Path) -> dict:
    """读库；不存在=空库，损坏=fail-closed（当作全量无效并原样保留现场）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "codes": []}
    except (OSError, ValueError):
        # fail-closed：损坏库拒绝一切消费（返回全过期形状，不动磁盘）。
        return {"version": 1, "codes": [], "_corrupt": True}
    if not isinstance(data, dict) or not isinstance(data.get("codes"), list):
        return {"version": 1, "codes": [], "_corrupt": True}
    return data


def _save(path: Path, data: dict) -> None:
    """原子落盘：临时文件 + os.replace（streak_api._atomic_write 同口径）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".auth_reset_codes.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _live(records: list[dict], username: str, now: float) -> list[dict]:
    return [r for r in records
            if r.get("username") == username
            and not r.get("used_at")
            and float(r.get("expires_at", 0)) > now]


def issue_reset_code(username: str, *, now: float | None = None) -> str | None:
    """为**已确认存在**的用户名签发一枚重置码；返回明文码（仅此一次出现）。

    调用方（CLI 通道）须先自行确认用户存在（不存在时不签发、不确认存在
    性——防未认证枚举）。超上限先撤最旧；写盘失败返回 None。
    """
    ts = time.time() if now is None else now
    code = "rf-" + secrets.token_urlsafe(24)
    path = _codes_path()
    with _LOCK:
        data = _load(path)
        if data.get("_corrupt"):
            return None
        records: list[dict] = data["codes"]
        mine = [r for r in records
                if r.get("username") == username and not r.get("used_at")
                and float(r.get("expires_at", 0)) > ts]
        while len(mine) >= RESET_CODES_PER_USER:
            oldest = min(mine, key=lambda r: float(r.get("created_at", 0)))
            records.remove(oldest)
            mine.remove(oldest)
        records.append({
            "sha256": _digest(code),
            "username": username,
            "created_at": ts,
            "expires_at": ts + RESET_CODE_TTL_SECONDS,
            "used_at": None,
        })
        # 过期/已用清扫（保序截断防无界）。
        records[:] = [r for r in records
                      if not r.get("used_at")
                      and float(r.get("expires_at", 0)) > ts][-_MAX_RECORDS:]
        data["codes"] = records
        try:
            _save(path, data)
        except OSError:
            return None
    return code


def consume_reset_code(username: str, code: str, *,
                       now: float | None = None) -> bool:
    """消费一枚重置码（一次性）：匹配且未用未过期 → 标记已用并落盘。"""
    ts = time.time() if now is None else now
    if not code:
        return False
    path = _codes_path()
    with _LOCK:
        data = _load(path)
        if data.get("_corrupt"):
            return False
        records: list[dict] = data["codes"]
        target = _digest(code)
        for record in records:
            if (record.get("username") == username
                    and not record.get("used_at")
                    and float(record.get("expires_at", 0)) > ts):
                if hmac.compare_digest(str(record.get("sha256", "")), target):
                    record["used_at"] = ts
                    data["codes"] = records
                    try:
                        _save(path, data)
                    except OSError:
                        return False
                    return True
        return False


def dummy_verify(code: str) -> None:
    """不存在用户名的等时垫（对齐 authenticate 哑哈希口径）。

    对输入码做同长摘要并跑固定次数 compare_digest，使「用户不存在」与
    「码不匹配」的消费时延同形；绝不抛错、结果不使用。
    """
    if not code:
        return
    target = _digest(code[:64])
    for index in range(RESET_CODES_PER_USER):
        hmac.compare_digest(_digest(f"timing-equalizer-{index}"), target)
