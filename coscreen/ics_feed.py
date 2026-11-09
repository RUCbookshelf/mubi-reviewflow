"""ICS 日历订阅（每日筛选提醒）——无 DST 陷阱的标准时区写法。

为某成员生成"未来 N 天、每天固定提醒时间"的只读日历（``text/calendar``）。
订阅令牌由成员自助生成/撤销；令牌只映射到摘要数字（目标/今日已筛/剩余/
连击），**事件里绝不含任何文献标题、摘要、筛选决定或排除理由**。

时区策略（避免 DST 陷阱）：

- 设置里配置了合法 IANA 时区（``timezone``）时：事件用
  ``DTSTART;TZID=<zone>:...``，并在 ``VTIMEZONE`` 中按 ``zoneinfo`` 实际
  偏移变化生成观测段（窗口内的 STANDARD/DAYLIGHT 切换逐条列出），因此
  夏令时切换后提醒仍落在**同一本地墙钟时间**。
- 未配置时区：事件使用 iCalendar 的 **floating time**（不带 ``Z``/``TZID``
  的本地时间），由订阅端按设备本地时区解释——同样不会因夏令时发生绝对
  时间漂移；契约中提示用户可填 IANA 时区以获得显式时区语义。

本模块不联网、不读数据库；输入全部由调用方（``streak_api``）给定，输出
是确定性字符串（唯一可变输入是 ``now_utc``，测试可固定）。
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Mapping

from coscreen.streak import DEFAULT_REMIND_TIME, resolve_timezone

__all__ = ["DEFAULT_ICS_DAYS", "build_daily_ics"]

#: 默认生成未来多少天的每日事件（含今天）。
DEFAULT_ICS_DAYS = 90

#: 摘要/描述里允许出现的数字字段（白名单；绝不接受自由文本）。
_NUMBER_FIELDS = (
    "screened_today",
    "daily_target",
    "remaining",
    "streak",
    "longest_streak",
)

_SUMMARY_TEMPLATE = (
    "ReviewFlow daily screening: {screened_today}/{daily_target} screened, "
    "streak {streak}, remaining {remaining}"
)


def _num(numbers: Mapping[str, object], key: str) -> int:
    try:
        return int(numbers.get(key, 0))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _clean_time(value: object) -> tuple[int, int]:
    """``HH:MM`` → (时, 分)；非法值回落到缺省提醒时间。"""
    text = value if isinstance(value, str) else ""
    fallback = DEFAULT_REMIND_TIME
    for candidate in (text.strip(), fallback):
        parts = candidate.split(":")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            hour, minute = int(parts[0]), int(parts[1])
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return hour, minute
    return 9, 0


def _escape_text(value: str) -> str:
    """RFC 5545 TEXT 转义（反斜杠/分号/逗号/换行）。"""
    return (value.replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\r\n", "\\n")
            .replace("\n", "\\n").replace("\r", "\\n"))


def _fmt_offset(value: timedelta) -> str:
    total = int(value.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return f"{sign}{total // 3600:02d}{(total % 3600) // 60:02d}"


def _fmt_local_naive(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%S")


def _fmt_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _offset_at(moment: datetime, zone: tzinfo) -> timedelta:
    return moment.astimezone(zone).utcoffset() or timedelta(0)


def _transitions(
    zone: tzinfo, start_utc: datetime, end_utc: datetime
) -> list[tuple[datetime, timedelta, timedelta, str, bool]]:
    """窗口内时区偏移切换点：``(UTC 时刻, 旧偏移, 新偏移, 新 TZNAME, 新是否 DST)``。

    按天采样找切换日，再二分到秒；窗口只有约 90 天，成本可忽略。
    """
    found: list[tuple[datetime, timedelta, timedelta, str, bool]] = []
    cursor = start_utc
    previous = _offset_at(cursor, zone)
    previous_moment = cursor
    step = timedelta(days=1)
    while cursor < end_utc:
        current = cursor + step
        if current > end_utc:
            current = end_utc
        offset = _offset_at(current, zone)
        if offset != previous:
            low, high = previous_moment, current
            while (high - low) > timedelta(seconds=1):
                middle = low + (high - low) / 2
                if _offset_at(middle, zone) == previous:
                    low = middle
                else:
                    high = middle
            onset = high.replace(microsecond=0)
            local = onset.astimezone(zone)
            found.append((onset, previous, offset, local.tzname() or "LOCAL",
                          bool(local.dst() and local.dst() != timedelta(0))))
            previous = offset
        previous_moment = current
        cursor = current
    return found


def _vtimezone_lines(zone: tzinfo, start_utc: datetime, end_utc: datetime) -> list[str]:
    """按实际偏移生成 VTIMEZONE（固定偏移 → 单个 STANDARD；有 DST → 逐段观测）。"""
    lines = ["BEGIN:VTIMEZONE", f"TZID:{zone.key}"]  # type: ignore[attr-defined]
    first = start_utc.astimezone(zone)
    first_offset = first.utcoffset() or timedelta(0)
    first_dst = bool(first.dst() and first.dst() != timedelta(0))
    lines.extend([
        "BEGIN:DAYLIGHT" if first_dst else "BEGIN:STANDARD",
        f"DTSTART:{_fmt_local_naive(first.replace(tzinfo=None))}",
        f"TZOFFSETFROM:{_fmt_offset(first_offset)}",
        f"TZOFFSETTO:{_fmt_offset(first_offset)}",
        f"TZNAME:{_escape_text(first.tzname() or 'LOCAL')}",
        "END:DAYLIGHT" if first_dst else "END:STANDARD",
    ])
    for onset, before, after, name, is_dst in _transitions(zone, start_utc, end_utc):
        local_after = (onset + after).replace(tzinfo=None)
        lines.extend([
            "BEGIN:DAYLIGHT" if is_dst else "BEGIN:STANDARD",
            f"DTSTART:{_fmt_local_naive(local_after)}",
            f"TZOFFSETFROM:{_fmt_offset(before)}",
            f"TZOFFSETTO:{_fmt_offset(after)}",
            f"TZNAME:{_escape_text(name)}",
            "END:DAYLIGHT" if is_dst else "END:STANDARD",
        ])
    lines.append("END:VTIMEZONE")
    return lines


def _fold(line: str, limit: int = 75) -> list[str]:
    """RFC 5545 折行：首行 75 字符，续行以单个空格开头。"""
    if len(line) <= limit:
        return [line]
    chunks = [line[:limit]]
    rest = line[limit:]
    while rest:
        chunks.append(" " + rest[: limit - 1])
        rest = rest[limit - 1:]
    return chunks


def build_daily_ics(
    *,
    task_id: str,
    start: date,
    days: int = DEFAULT_ICS_DAYS,
    remind_time: object = DEFAULT_REMIND_TIME,
    timezone_name: object = "",
    numbers: Mapping[str, object] | None = None,
    member: str = "",
    now_utc: datetime | None = None,
) -> str:
    """生成每日提醒 ICS 文本（默认未来 90 天，事件数 = ``days``）。

    ``numbers`` 只读取 :data:`_NUMBER_FIELDS` 白名单键并强制转 int；其余键
    一律忽略——这是"日历里绝不含文献内容"的最后一道闸。

    ``member``（终审 #17）：事件 UID 混入成员标识哈希前缀——同任务不同
    成员的订阅源不再生成相同 UID（RFC 5545 全局唯一要求；两个订阅进同
    一日历账户时数字不会被互相覆盖）。UID 只含任务 ID 与成员标识的
    SHA-256 前缀，不含成员名明文（隐私口径不变）；同成员同任务同日
    UID 恒定（重复拉取幂等），跨日/跨任务规则不变。
    """
    numbers = numbers or {}
    try:
        count = int(days)
    except (TypeError, ValueError):
        count = DEFAULT_ICS_DAYS
    count = max(1, min(count, 366))
    hour, minute = _clean_time(remind_time)
    zone_name = timezone_name.strip() if isinstance(timezone_name, str) else ""
    zone = resolve_timezone(zone_name)
    # 事件从今天 00:00 本地时间起算；用 UTC 采样窗口覆盖整个事件范围。
    start_local = datetime(start.year, start.month, start.day)
    if zone is not None:
        start_utc = start_local.replace(tzinfo=zone).astimezone(timezone.utc)
    else:
        start_utc = start_local.replace(tzinfo=timezone.utc)
    end_utc = start_utc + timedelta(days=count + 2)
    stamp = _fmt_utc(now_utc or datetime.now(timezone.utc))
    summary = _SUMMARY_TEMPLATE.format(**{key: _num(numbers, key) for key in _NUMBER_FIELDS})
    description = ";".join(f"{key}={_num(numbers, key)}" for key in _NUMBER_FIELDS)
    digest = hashlib.sha256(task_id.encode("utf-8"))
    if member:
        digest.update(b"\x1f")
        digest.update(hashlib.sha256(member.encode("utf-8")).digest()[:8])
    uid_base = digest.hexdigest()[:16]

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//ReviewFlow//Daily screening nudge 2.1.5//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:ReviewFlow daily screening",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
    ]
    if zone is not None:
        lines.extend(_vtimezone_lines(zone, start_utc, end_utc))
    for index in range(count):
        day = start + timedelta(days=index)
        day_text = day.strftime("%Y%m%d")
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid_base}-{day_text}@reviewflow.invalid",
            f"DTSTAMP:{stamp}",
        ])
        if zone is not None:
            # 显式 TZID + VTIMEZONE：DST 切换后仍是同一本地墙钟时间。
            lines.append(f"DTSTART;TZID={zone.key}:{day_text}T{hour:02d}{minute:02d}00")
        else:
            # floating time：跟随订阅端本地时钟，不做任何 UTC 换算。
            lines.append(f"DTSTART:{day_text}T{hour:02d}{minute:02d}00")
        lines.extend([
            "DURATION:PT15M",
            f"SUMMARY:{_escape_text(summary)}",
            f"DESCRIPTION:{_escape_text(description)}",
            "TRANSP:TRANSPARENT",
            "STATUS:CONFIRMED",
            "SEQUENCE:0",
            "END:VEVENT",
        ])
    lines.append("END:VCALENDAR")
    folded: list[str] = []
    for line in lines:
        folded.extend(_fold(line))
    return "\r\n".join(folded) + "\r\n"
