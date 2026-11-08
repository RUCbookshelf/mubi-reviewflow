"""每日目标 / 连续达标天数（打卡）——纯计算 + 只读取数。

本模块是 2.1.5 Wave C2「每日打卡与提醒」的计算核心，**不保存任何状态表**：
每日目标、连续达标天数、最近 14 天达标布尔全部由 ``decisions`` 的时间戳在
查询时现算（输入 = 某成员个人库 ``{slug}.db`` 的 ``decisions.screened_at``）。

连击口径（产品裁定，逐字落实）：

- **今天未达标但今天还没过完 → 连击不中断**：今日计数不足目标时，连击从
  *昨天* 起算；今天一旦达标，今天也计入。
- **昨天未达标才断**：昨天计数不足目标（或昨天没有任何筛选记录）时，连击
  从今天重新起算，不回补更早的连续天。
- **按本地日切分**：``db._now()`` 写入的是**无时区服务器本地时间** ISO 串，
  先按服务器本地时区补齐偏移；若目标时区（设置里的 ``timezone``）不同，
  再换算到目标时区取日期。带时区偏移的时间戳直接换算。跨时区按本地日。
- 目标为 0（未设目标）时不存在"达标"概念：连击/最长连击/逐日达标一律为
  False/0。
- 历史最长连击与当前连击都用**当前目标**回溯计算（本波不保存目标变更历史，
  因此修改目标会同时改变历史口径；契约中如实披露）。

目标建议（只读，不自动写）：``suggested = ceil(剩余未筛数 / 剩余天数)``；
剩余天数含今天（截止日=今天 → 1 天）；已逾期 → 0 天，建议值=剩余全部。

存储/取数约束：本模块所有 SQL 都是**常量字面量**，无拼接/format/f-string；
读库一律只读 URI（``mode=ro``），旧库缺表时按 0 处理而不是建表。
"""

from __future__ import annotations

import math
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Iterable, Sequence

__all__ = [
    "DEFAULT_REMIND_TIME",
    "DEFAULT_WINDOW_DAYS",
    "MAX_DAILY_TARGET",
    "count_articles",
    "count_decisions",
    "daily_counts",
    "daily_status",
    "load_decision_timestamps",
    "local_timezone",
    "parse_date",
    "parse_local_date",
    "remaining_days",
    "remaining_unscreened",
    "resolve_timezone",
    "suggest_daily_target",
    "today_in",
]

#: 提醒时间缺省值（HH:MM）；提醒开关默认仍是关。
DEFAULT_REMIND_TIME = "09:00"

#: 逐日达标布尔默认回看天数（含今天）。
DEFAULT_WINDOW_DAYS = 14

#: 每日目标上限（防御性上限，避免异常输入撑爆建议值）。
MAX_DAILY_TARGET = 10000


# ---------------------------------------------------------------------------
# 时区与日期
# ---------------------------------------------------------------------------

def resolve_timezone(name: str | None) -> tzinfo | None:
    """IANA 时区名 → tzinfo；空名/未知名返回 ``None``（调用方降级为本地时区）。

    ``zoneinfo`` 是标准库；Windows 上若缺少 tzdata 数据包会抛
    ``ZoneInfoNotFoundError``，这里统一折叠为 ``None``，绝不把查询变成 500。
    """
    if not isinstance(name, str):
        return None
    text = name.strip()
    if not text:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(text)
    except Exception:  # noqa: BLE001 - 未知时区/缺 tzdata 一律降级
        return None


def local_timezone() -> tzinfo | None:
    """服务器当前本地时区（用于把带偏移的时间戳换算成本地日）。"""
    try:
        return datetime.now().astimezone().tzinfo
    except Exception:  # noqa: BLE001 - 极端环境降级为 naive
        return None


def today_in(tz: tzinfo | None = None) -> date:
    """目标时区的"今天"。``tz`` 为空时取服务器本地日期。"""
    if tz is None:
        return datetime.now().date()
    return datetime.now(tz).date()


def parse_local_date(stamp: object, tz: tzinfo | None = None) -> date | None:
    """ISO8601 时间戳 → 本地日期；无法解析返回 ``None``。

    - 无时区（``db._now()`` 写入的服务器本地时间）先按服务器本地时区补齐
      偏移，再换算到目标时区；
    - 带时区偏移的直接换算；
    - ``tz`` 为空时用服务器本地时区（``local_timezone()``），此时无时区
      时间戳的日期保持不变。
    """
    if not isinstance(stamp, str):
        return None
    text = stamp.strip()
    if not text:
        return None
    moment: datetime | None = None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                moment = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if moment is None:
        return None
    if moment.tzinfo is None:
        source = local_timezone()
        if source is not None:
            moment = moment.replace(tzinfo=source)
    if moment.tzinfo is not None:
        target = tz if tz is not None else local_timezone()
        if target is not None:
            moment = moment.astimezone(target)
    return moment.date()


def parse_date(text: object) -> date | None:
    """严格解析 ``YYYY-MM-DD``（不接受 datetime 串）；非法返回 ``None``。"""
    if not isinstance(text, str):
        return None
    value = text.strip()
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        return None
    if not (value[:4].isdigit() and value[5:7].isdigit() and value[8:].isdigit()):
        return None
    try:
        return date(int(value[:4]), int(value[5:7]), int(value[8:]))
    except ValueError:
        return None


def remaining_days(deadline: date, today: date) -> int:
    """截止日前还剩几天（含今天）。截止日=今天 → 1；已逾期 → 0。"""
    span = (deadline - today).days + 1
    return span if span > 0 else 0


# ---------------------------------------------------------------------------
# 连击计算（纯函数）
# ---------------------------------------------------------------------------

def daily_counts(timestamps: Iterable[object], tz: tzinfo | None = None) -> dict[date, int]:
    """时间戳序列 → ``{本地日期: 当日筛选条数}``（无法解析的条目忽略）。"""
    counts: dict[date, int] = {}
    for stamp in timestamps:
        day = parse_local_date(stamp, tz)
        if day is not None:
            counts[day] = counts.get(day, 0) + 1
    return counts


def _is_met(counts: dict[date, int], day: date, target: int) -> bool:
    return target > 0 and counts.get(day, 0) >= target


def _current_streak(counts: dict[date, int], target: int, today: date) -> int:
    """当前连击（口径见模块 docstring：今天没过完不中断）。"""
    if target <= 0:
        return 0
    # 今天达标 → 从今天起算；今天未达标 → 今天不中断，从昨天起算。
    cursor = today if _is_met(counts, today, target) else today - timedelta(days=1)
    streak = 0
    while _is_met(counts, cursor, target):
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def _longest_streak(counts: dict[date, int], target: int, today: date) -> int:
    """历史最长连击（从首条筛选日到今天，缺失日按未达标断开）。"""
    if target <= 0 or not counts:
        return 0
    first = min(counts)
    if first > today:  # 未来时间戳（时钟回拨/手工数据）：不参与历史
        return 0
    cursor = first
    run = 0
    best = 0
    while cursor <= today:
        if _is_met(counts, cursor, target):
            run += 1
            best = max(best, run)
        else:
            run = 0
        cursor += timedelta(days=1)
    return best


def daily_status(
    timestamps: Sequence[object] | Iterable[object],
    *,
    today: date,
    daily_target: int,
    tz: tzinfo | None = None,
    window: int = DEFAULT_WINDOW_DAYS,
) -> dict:
    """计算某成员的当日状态 / 连击 / 最近 ``window`` 天逐日达标布尔。

    返回字段（全部为纯计算结果，不读不写任何状态表）：

    - ``daily_target`` / ``target_set``：当前每日目标与是否已设定；
    - ``today_screened`` / ``today_met``：今日已筛条数 / 今日是否达标；
    - ``streak``：当前连击（今天未达标但未过完 → 从昨天起算，不中断）；
    - ``longest_streak``：历史最长连击（按当前目标回溯）；
    - ``last_14_days``：``[{"date": "YYYY-MM-DD", "screened": n, "met": bool}]``
      按日期升序，最后一项恒为今天。
    """
    target = int(daily_target)
    if target < 0:
        target = 0
    if target > MAX_DAILY_TARGET:
        target = MAX_DAILY_TARGET
    counts = daily_counts(timestamps, tz)
    day_count = max(int(window), 1)
    days = []
    for offset in range(day_count - 1, -1, -1):
        day = today - timedelta(days=offset)
        screened = counts.get(day, 0)
        days.append({
            "date": day.isoformat(),
            "screened": screened,
            "met": _is_met(counts, day, target),
        })
    return {
        "daily_target": target,
        "target_set": target > 0,
        "today_screened": counts.get(today, 0),
        "today_met": _is_met(counts, today, target),
        "streak": _current_streak(counts, target, today),
        "longest_streak": _longest_streak(counts, target, today),
        "last_14_days": days,
    }


# ---------------------------------------------------------------------------
# 目标建议（只读）
# ---------------------------------------------------------------------------

def suggest_daily_target(remaining_unscreened: int, remaining_days: int) -> dict:
    """``ceil(剩余未筛数 / 剩余天数)``；剩余 0 → 0，天数 0（已逾期）→ 全部。

    只读建议值：调用方绝不把它自动写回设置。
    """
    remaining = int(remaining_unscreened)
    if remaining < 0:
        remaining = 0
    days = int(remaining_days)
    if days < 0:
        days = 0
    if remaining == 0:
        suggested = 0
    elif days == 0:
        suggested = remaining
    else:
        suggested = math.ceil(remaining / days)
    return {
        "remaining_unscreened": remaining,
        "remaining_days": days,
        "suggested_target": suggested,
    }


# ---------------------------------------------------------------------------
# 只读取数（常量 SQL；缺表/缺库按空处理）
# ---------------------------------------------------------------------------

def _readonly_connect(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def load_decision_timestamps(db_path: str | Path) -> list[str]:
    """只读取 ``decisions.screened_at`` 列表。

    - 库文件不存在 → ``FileNotFoundError``（调用方按"尚无筛选记录"降级）；
    - 旧库没有 decisions 表 → 返回空列表（不建表、不写库）；
    - 其他 SQLite/IO 故障向上抛出，由 API 层映射为 ``available=false``。
    """
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    with closing(_readonly_connect(path)) as conn:
        if not _has_table(conn, "decisions"):
            return []
        rows = conn.execute("SELECT screened_at FROM decisions").fetchall()
    return [str(row[0]) for row in rows if row and row[0] is not None]


def count_decisions(db_path: str | Path) -> int:
    """只读统计个人库已筛选条数；库/表缺失 → 0。"""
    path = Path(db_path)
    if not path.is_file():
        return 0
    with closing(_readonly_connect(path)) as conn:
        if not _has_table(conn, "decisions"):
            return 0
        row = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()
    return int(row[0]) if row else 0


def count_articles(db_path: str | Path | None) -> int | None:
    """只读统计非重复文献条数；库/表缺失 → ``None``（调用方可换库重试）。

    只统计 ``is_duplicate_of IS NULL`` 的行，与进度/队列口径一致。极旧库
    缺该列时退化为全表计数（不建列、不写库）。
    """
    if db_path is None:
        return None
    path = Path(db_path)
    if not path.is_file():
        return None
    with closing(_readonly_connect(path)) as conn:
        if not _has_table(conn, "articles"):
            return None
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM articles WHERE is_duplicate_of IS NULL"
            ).fetchone()
        except sqlite3.OperationalError:
            row = conn.execute("SELECT COUNT(*) FROM articles").fetchone()
    return int(row[0]) if row else 0


def remaining_unscreened(
    screener_db: str | Path,
    owner_db: str | Path | None = None,
) -> int:
    """某成员剩余未筛数 = 非重复文献总数 − 其个人库已筛条数（不小于 0）。

    文献总数优先取任务属主库（共享语料；成员库可能尚未同步），属主库不可用
    时退回成员个人库；已筛条数始终取成员个人库。两个库都缺 → 0。
    """
    total = count_articles(owner_db)
    if total is None:
        total = count_articles(screener_db)
    if total is None:
        total = 0
    decided = count_decisions(screener_db)
    return max(0, int(total) - int(decided))
