# -*- coding: utf-8 -*-
"""2.1.5 Wave C2 · 每日打卡与提醒后端红钉（专属新文件，撤实现必红）。

覆盖：

1. 连击口径钉：今天未达标但今天没过完 → 连击保留（从昨天起算）；昨天未
   达标才断；跨时区按本地日切分；历史最长连击。
2. 目标建议计算钉：``ceil(剩余未筛数 / 剩余天数)``；截止日=今天 → 1 天；
   逾期 → 0 天；无截止日期 → 只读端点返回 ``suggestion_available=false``
   且绝不写回设置。
3. daily-status 权限：本人可读；他人仅负责人可读；非参与者 403。
4. ICS 生成钉：事件数 = 90；摘要/描述只含数字（无题录/摘要/筛选决定）；
   令牌明文不落盘、撤销后 404、无效令牌 404；DST 切换窗口内仍是同一本地
   墙钟时间（VTIMEZONE 观测段）。
5. 通道抽象钉：默认关；webhook 载荷经捕获型假 opener 断言只含数字字段
   （URL 公网校验真跑，DNS 解析桩指向公网 IP——2.1.5 安全修复后不再把
   ``validate_public_url`` mock 成恒放行）；Apprise 未安装 → 结构化
   ``channel_unavailable/apprise_missing``（不崩）。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

fixture = tempfile.TemporaryDirectory(prefix="rf_215c_streak_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient  # noqa: E402

from coscreen import db as db_mod  # noqa: E402
from coscreen import ics_feed  # noqa: E402
from coscreen import notify as notify_core  # noqa: E402
from coscreen import streak as streak_core  # noqa: E402
from coscreen import task_collaboration  # noqa: E402
from coscreen import tasks as tasks_mod  # noqa: E402
from coscreen.models import Article  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend import streak_api  # noqa: E402
from custom_backend.main import app, DATA_DIR  # noqa: E402

TODAY = date(2026, 11, 8)
SECRET_TITLE = "TOP-SECRET-TITLE-ALPHA"
SECRET_ABSTRACT = "SECRET-ABSTRACT-BETA"


# ---------------------------------------------------------------------------
# 1. 纯计算：连击口径 / 目标建议
# ---------------------------------------------------------------------------

class StreakComputationTests(unittest.TestCase):
    def _stamps(self, *items):
        return [stamp for stamp, count in items for _ in range(count)]

    def test_today_in_progress_does_not_break_streak(self):
        """今天未达标但今天还没过完：连击从昨天起算，不中断。"""
        stamps = self._stamps(("2026-11-06T10:00:00", 2),
                              ("2026-11-07T10:00:00", 2))
        status = streak_core.daily_status(stamps, today=TODAY, daily_target=2)
        assert status["today_screened"] == 0
        assert status["today_met"] is False
        assert status["streak"] == 2, status  # 11-06、11-07 连续达标

    def test_streak_breaks_only_after_yesterday_missed(self):
        """昨天未达标才断：今天达标只从今天起算。"""
        stamps = self._stamps(("2026-11-06T10:00:00", 2),
                              ("2026-11-08T09:00:00", 2))
        status = streak_core.daily_status(stamps, today=TODAY, daily_target=2)
        assert status["today_met"] is True
        assert status["streak"] == 1, status  # 11-07 空白 → 断

    def test_streak_zero_when_today_and_yesterday_missed(self):
        stamps = self._stamps(("2026-11-05T10:00:00", 2))
        status = streak_core.daily_status(stamps, today=TODAY, daily_target=2)
        assert status["streak"] == 0
        assert status["longest_streak"] == 1

    def test_longest_streak_and_last_14_days(self):
        stamps = self._stamps(
            ("2026-11-01T10:00:00", 2),
            ("2026-11-02T10:00:00", 2),
            ("2026-11-03T10:00:00", 2),
            ("2026-11-07T10:00:00", 2),
        )
        status = streak_core.daily_status(stamps, today=TODAY, daily_target=2)
        assert status["longest_streak"] == 3
        assert status["streak"] == 1
        days = status["last_14_days"]
        assert len(days) == 14
        assert days[-1]["date"] == "2026-11-08"
        assert days[0]["date"] == "2026-10-26"
        by_date = {row["date"]: row for row in days}
        assert by_date["2026-11-02"]["met"] is True
        assert by_date["2026-11-04"]["met"] is False

    def test_target_unset_means_no_streak(self):
        status = streak_core.daily_status(
            ["2026-11-08T10:00:00"], today=TODAY, daily_target=0)
        assert status["target_set"] is False
        assert status["today_met"] is False
        assert status["streak"] == 0
        assert status["longest_streak"] == 0
        assert all(row["met"] is False for row in status["last_14_days"])

    def test_local_day_boundary_and_timezone(self):
        """跨时区按本地日：26-11-07T23:30 与 11-08T00:10 分属两天；UTC 时间
        换算到上海时区后也落在正确的本地日。"""
        status = streak_core.daily_status(
            ["2026-11-07T23:30:00", "2026-11-08T00:10:00"],
            today=TODAY, daily_target=1)
        by_date = {row["date"]: row for row in status["last_14_days"]}
        assert by_date["2026-11-07"]["screened"] == 1
        assert by_date["2026-11-08"]["screened"] == 1

        shanghai = streak_core.resolve_timezone("Asia/Shanghai")
        assert shanghai is not None
        # UTC 2026-11-07T17:00 = 上海 2026-11-08T01:00
        assert streak_core.parse_local_date("2026-11-07T17:00:00+00:00",
                                            shanghai) == date(2026, 11, 8)
        # 同一时间戳按 UTC 日是 11-07 —— 证明切分随时区变化
        assert streak_core.parse_local_date(
            "2026-11-07T17:00:00+00:00",
            streak_core.resolve_timezone("UTC")) == date(2026, 11, 7)

    def test_suggest_daily_target_ceiling_and_edges(self):
        assert streak_core.suggest_daily_target(10, 3)["suggested_target"] == 4
        assert streak_core.suggest_daily_target(9, 3)["suggested_target"] == 3
        assert streak_core.suggest_daily_target(0, 3)["suggested_target"] == 0
        assert streak_core.suggest_daily_target(5, 0)["suggested_target"] == 5
        assert streak_core.suggest_daily_target(-2, 3)["suggested_target"] == 0
        assert streak_core.remaining_days(date(2026, 11, 8), TODAY) == 1
        assert streak_core.remaining_days(date(2026, 11, 10), TODAY) == 3
        assert streak_core.remaining_days(date(2026, 11, 1), TODAY) == 0


# ---------------------------------------------------------------------------
# 4. ICS 生成（纯函数部分）
# ---------------------------------------------------------------------------

class IcsGenerationTests(unittest.TestCase):
    def test_event_count_and_numbers_only(self):
        text = ics_feed.build_daily_ics(
            task_id="task-abc",
            start=date(2026, 11, 8),
            days=90,
            remind_time="08:15",
            timezone_name="Asia/Shanghai",
            numbers={"screened_today": 2, "daily_target": 3, "remaining": 41,
                     "streak": 5, "longest_streak": 9},
            now_utc=datetime(2026, 11, 8, 0, 0, tzinfo=timezone.utc),
        )
        assert text.count("BEGIN:VEVENT") == 90
        assert text.count("END:VEVENT") == 90
        assert "DTSTART;TZID=Asia/Shanghai:20261108T081500" in text
        assert "TZID:Asia/Shanghai" in text
        assert "2/3" in text and "streak 5" in text and "remaining 41" in text
        assert "screened_today=2" in text
        assert "20261108@reviewflow.invalid" in text

    def test_dst_transition_keeps_wall_clock(self):
        """2026-11-01 美东 DST 结束：VTIMEZONE 必须含 -0400 → -0500 观测段，
        且切换后事件仍是本地 09:00（不是 UTC 09:00）。"""
        text = ics_feed.build_daily_ics(
            task_id="task-dst", start=date(2026, 11, 1), days=5,
            remind_time="09:00", timezone_name="America/New_York",
            numbers={"screened_today": 1, "daily_target": 2, "remaining": 7,
                     "streak": 1, "longest_streak": 3},
            now_utc=datetime(2026, 10, 31, 0, 0, tzinfo=timezone.utc))
        assert "TZID:America/New_York" in text
        assert "TZOFFSETFROM:-0400" in text
        assert "TZOFFSETTO:-0500" in text
        # 切换日之后的每日事件仍标注本地 09:00
        assert "DTSTART;TZID=America/New_York:20261103T090000" in text
        assert text.count("DTSTART;TZID=America/New_York:") == 5

    def test_no_timezone_uses_floating_local_time(self):
        text = ics_feed.build_daily_ics(
            task_id="task-float", start=date(2026, 11, 8), days=2,
            remind_time="09:00", timezone_name="",
            numbers={}, now_utc=datetime(2026, 11, 8, tzinfo=timezone.utc))
        assert "BEGIN:VTIMEZONE" not in text
        assert "\r\nDTSTART:20261108T090000\r\n" in text
        assert "\r\nDTSTART:20261109T090000\r\n" in text


class NotifyTransportTests(unittest.TestCase):
    class _RaisingOpener:
        def __init__(self, exc):
            self.exc = exc

        def open(self, request, timeout=None):
            raise self.exc

    def _send(self, exc):
        summary = notify_core.build_summary(
            {"screened_today": 1, "daily_target": 2, "remaining": 3,
             "streak": 0, "longest_streak": 0})
        config = {"enabled": True, "type": "webhook",
                  "url": "https://example.test/hook"}
        # 2.1.5 安全修复：不再把 validate_public_url mock 成恒放行——只桩
        # DNS 解析（指向公网 IP），URL 校验本身（scheme/内嵌凭据/私网字
        # 面量）真跑（F2 钉组见 test_2_1_5_notify_sec_pins.py）。
        with mock.patch.object(notify_core, "_resolve_host",
                               lambda host, port=443: ["93.184.216.34"]):
            return notify_core.send(config, summary,
                                    opener=self._RaisingOpener(exc))

    def test_read_phase_timeout_is_classified_as_timeout(self):
        result = self._send(TimeoutError("timed out"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "timeout")
        # 契约 §5.3：timeout/unreachable 共用 network 文案键，但 status 精确。
        result = self._send(OSError("connection reset"))
        self.assertEqual(result["status"], "unreachable")


# ---------------------------------------------------------------------------
# 2/3/5. API 集成（权限 / 建议 / ICS / 通道）
# ---------------------------------------------------------------------------


class DailyNudgeApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.users = []
        for i in range(3):
            user = auth_mod.create_user(f"r215c{i}@example.test", "R215C-Streak-Test-2026")
            cls.users.append({
                "user_id": user["id"],
                "username": user["username"],
                "token": auth_mod.create_access_token(user["id"], user["username"]),
            })

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    def auth(self, index=0):
        return {"Authorization": "Bearer " + self.users[index]["token"]}

    def setUp(self):
        self._today_patch = mock.patch.object(
            streak_api, "_today", lambda tz=None: TODAY)
        self._today_patch.start()
        self.task = self.client.post(
            "/api/tasks", json={"name": self._testMethodName},
            headers=self.auth()).json()["task_id"]
        self.info = tasks_mod.find_task(self.task, DATA_DIR)
        response = self.client.post(
            f"/api/tasks/{self.task}/collaboration/invite",
            json={"username": self.users[1]["username"], "role": "coder"},
            headers=self.auth())
        self.assertEqual(response.status_code, 201, response.text)
        self.articles = [
            Article(zotero_key=f"K{i}", title=SECRET_TITLE if i == 0 else f"Study {i}",
                    abstract=SECRET_ABSTRACT if i == 0 else "")
            for i in range(10)
        ]
        for index in (0, 1):
            db = self._member_db(index)
            db_mod.init_db(db)
            db_mod.upsert_articles(db, self.articles, [])
        self.addCleanup(self._today_patch.stop)

    def _member_db(self, index: int) -> Path:
        slug = tasks_mod.slugify_screener(self.users[index]["username"])
        return self.info.dir / f"{slug}.db"

    def _seed(self, index: int, entries):
        """entries = [(zotero_key, 'YYYY-MM-DDTHH:MM:SS', decision?)]。"""
        db = self._member_db(index)
        for entry in entries:
            decision = entry[2] if len(entry) > 2 else "include"
            db_mod.save_decision(db, entry[0], decision)
        with closing(sqlite3.connect(db)) as conn:
            for entry in entries:
                conn.execute(
                    "UPDATE decisions SET screened_at = ?, updated_at = ? WHERE zotero_key = ?",
                    (entry[1], entry[1], entry[0]))
            conn.commit()

    def _put_target(self, index, **overrides):
        body = {"daily_target": 3, "remind_time": "09:00",
                "remind_enabled": True, "deadline": "", "timezone": ""}
        body.update(overrides)
        response = self.client.put(
            f"/api/tasks/{self.task}/screening/daily-target",
            json=body, headers=self.auth(index))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _put_channel(self, body):
        return self.client.put(
            f"/api/tasks/{self.task}/screening/notify/channels",
            json=body, headers=self.auth(0))

    # ---- daily-status / 权限 ------------------------------------------

    def test_daily_status_self_and_settings(self):
        self._put_target(1, daily_target=3)
        self._seed(1, [
            ("K0", "2026-11-06T10:00:00"),
            ("K1", "2026-11-06T11:00:00"),
            ("K2", "2026-11-06T12:00:00"),
            ("K3", "2026-11-07T10:00:00"),
            ("K4", "2026-11-07T11:00:00"),
            ("K5", "2026-11-07T12:00:00"),
            ("K6", "2026-11-08T09:00:00"),
            ("K7", "2026-11-08T09:30:00"),
        ])
        response = self.client.get(
            f"/api/tasks/{self.task}/screening/daily-status",
            headers=self.auth(1))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["screener"], self.users[1]["username"])
        self.assertEqual(body["date"], "2026-11-08")
        self.assertEqual(body["today_screened"], 2)
        self.assertEqual(body["daily_target"], 3)
        self.assertFalse(body["today_met"])
        self.assertEqual(body["streak"], 2)      # 11-06、11-07；今天未过完不断
        self.assertEqual(body["longest_streak"], 2)
        self.assertEqual(len(body["last_14_days"]), 14)
        self.assertEqual(body["remaining_unscreened"], 2)  # 10 篇 − 已筛 8
        self.assertEqual(body["remind_time"], "09:00")
        self.assertTrue(body["remind_enabled"])

    def test_daily_status_permissions(self):
        # 本人可读
        self.assertEqual(self.client.get(
            f"/api/tasks/{self.task}/screening/daily-status",
            headers=self.auth(1)).status_code, 200)
        # 成员读他人（负责人）→ 403
        self.assertEqual(self.client.get(
            f"/api/tasks/{self.task}/screening/daily-status?screener={self.users[0]['username']}",
            headers=self.auth(1)).status_code, 403)
        # 负责人读成员 → 200
        self.assertEqual(self.client.get(
            f"/api/tasks/{self.task}/screening/daily-status?screener={self.users[1]['username']}",
            headers=self.auth(0)).status_code, 200)
        # 非参与者 → 403
        self.assertEqual(self.client.get(
            f"/api/tasks/{self.task}/screening/daily-status",
            headers=self.auth(2)).status_code, 403)

    def test_target_suggestion_is_read_only(self):
        self._put_target(1, daily_target=4, deadline="")
        self._seed(1, [(f"K{i}", "2026-11-07T10:00:00") for i in range(4)])
        response = self.client.get(
            f"/api/tasks/{self.task}/screening/daily-status/suggestion?deadline=2026-11-10",
            headers=self.auth(1))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["suggestion_available"])
        self.assertEqual(body["remaining_unscreened"], 6)  # 10 − 4
        self.assertEqual(body["remaining_days"], 3)        # 11-08..11-10
        self.assertEqual(body["suggested_target"], 2)      # ceil(6/3)
        self.assertEqual(body["basis"], "query")
        # 建议端点绝不写回设置：目标仍是 4
        status = self.client.get(
            f"/api/tasks/{self.task}/screening/daily-status",
            headers=self.auth(1)).json()
        self.assertEqual(status["daily_target"], 4)

    def test_target_suggestion_requires_deadline(self):
        self._put_target(1, daily_target=4, deadline="")
        response = self.client.get(
            f"/api/tasks/{self.task}/screening/daily-status/suggestion",
            headers=self.auth(1))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["suggestion_available"])
        self.assertEqual(body["reason"], "deadline_required")
        # 非法截止日期 → 400
        self.assertEqual(self.client.get(
            f"/api/tasks/{self.task}/screening/daily-status/suggestion?deadline=2026-13-40",
            headers=self.auth(1)).status_code, 400)

    def test_settings_missing_is_normal_and_corrupt_degrades(self):
        # 缺失 = 首次使用的正常默认态（settings_available=true）
        response = self.client.get(
            f"/api/tasks/{self.task}/screening/daily-status", headers=self.auth(1))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["settings_available"])
        # 损坏 = 默认值 + settings_available=false
        slug = tasks_mod.slugify_screener(self.users[1]["username"])
        (self.info.dir / f"daily_nudge_{slug}.json").write_text(
            "{not-json", encoding="utf-8")
        response = self.client.get(
            f"/api/tasks/{self.task}/screening/daily-status", headers=self.auth(1))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["settings_available"])
        self.assertEqual(response.json()["daily_target"], 0)

    def test_owner_lookup_failure_maps_503_not_403(self):
        with mock.patch.object(
                task_collaboration, "get_owner",
                side_effect=task_collaboration.OwnerUnavailableError("locked")):
            response = self.client.get(
                f"/api/tasks/{self.task}/screening/daily-status?screener={self.users[1]['username']}",
                headers=self.auth(0))
        self.assertEqual(response.status_code, 503, response.text)

    # ---- ICS ----------------------------------------------------------

    def test_ics_feed_90_days_numbers_only_and_revoke(self):
        self._put_target(1, daily_target=3, remind_time="08:15",
                         timezone="Asia/Shanghai")
        self._seed(1, [(f"K{i}", "2026-11-07T10:00:00") for i in range(3)]
                   + [("K3", "2026-11-08T08:00:00")])
        created = self.client.post(
            f"/api/tasks/{self.task}/screening/ics-tokens", headers=self.auth(1))
        self.assertEqual(created.status_code, 200, created.text)
        token = created.json()["token"]
        token_id = created.json()["token_id"]
        feed_path = created.json()["feed_path"]

        # 明文令牌不落盘
        store = (self.info.dir / "daily_nudge_tokens.json").read_text(encoding="utf-8")
        self.assertNotIn(token, store)
        self.assertIn("sha256", store)

        response = self.client.get(feed_path)  # 认证外
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.headers["content-type"].startswith("text/calendar"))
        text = response.content.decode("utf-8")
        self.assertEqual(text.count("BEGIN:VEVENT"), 90)
        self.assertIn("DTSTART;TZID=Asia/Shanghai:20261108T081500", text)
        self.assertIn("screened_today=1", text)
        self.assertIn("daily_target=3", text)
        # 绝不含文献内容/筛选决定
        self.assertNotIn(SECRET_TITLE, text)
        self.assertNotIn(SECRET_ABSTRACT, text)
        self.assertNotIn("include", text)
        self.assertNotIn("exclude", text)
        self.assertNotIn("exclusion_reason", text)

        # 无效令牌 → 404
        self.assertEqual(self.client.get(
            f"/api/tasks/{self.task}/screening/daily-status.ics?token=not-a-real-token").status_code, 404)

        # 撤销后 → 404（明文 URL 立即失效）
        revoked = self.client.delete(
            f"/api/tasks/{self.task}/screening/ics-tokens/{token_id}",
            headers=self.auth(1))
        self.assertEqual(revoked.status_code, 200, revoked.text)
        self.assertEqual(self.client.get(feed_path).status_code, 404)
        # 重复撤销幂等（本人再次撤销仍 200）
        self.assertEqual(self.client.delete(
            f"/api/tasks/{self.task}/screening/ics-tokens/{token_id}",
            headers=self.auth(1)).status_code, 200)

    def test_ics_overlong_token_returns_404_not_422(self):
        response = self.client.get(
            f"/api/tasks/{self.task}/screening/daily-status.ics?token=" + "a" * 300)
        self.assertEqual(response.status_code, 404, response.text)

    def test_ics_token_dies_when_member_removed_and_owner_can_revoke(self):
        invited = self.client.post(
            f"/api/tasks/{self.task}/collaboration/invite",
            json={"username": self.users[2]["username"], "role": "coder"},
            headers=self.auth())
        self.assertEqual(invited.status_code, 201, invited.text)
        created = self.client.post(
            f"/api/tasks/{self.task}/screening/ics-tokens", headers=self.auth(1))
        self.assertEqual(created.status_code, 200, created.text)
        token_a = created.json()["token"]
        token_id_a = created.json()["token_id"]
        feed_a = created.json()["feed_path"]
        self.assertEqual(self.client.get(feed_a).status_code, 200)
        # 另一个成员不能撤销他人令牌
        self.assertEqual(self.client.delete(
            f"/api/tasks/{self.task}/screening/ics-tokens/{token_id_a}",
            headers=self.auth(2)).status_code, 404)
        # 负责人可以回收成员令牌
        self.assertEqual(self.client.delete(
            f"/api/tasks/{self.task}/screening/ics-tokens/{token_id_a}",
            headers=self.auth(0)).status_code, 200)
        self.assertEqual(self.client.get(feed_a).status_code, 404)

        # 新令牌：成员被移出任务后订阅立即失效（而不是继续 200）
        created = self.client.post(
            f"/api/tasks/{self.task}/screening/ics-tokens", headers=self.auth(1))
        feed_b = created.json()["feed_path"]
        self.assertEqual(self.client.get(feed_b).status_code, 200)
        removed = self.client.delete(
            f"/api/tasks/{self.task}/collaboration/roles/{self.users[1]['user_id']}/coder",
            headers=self.auth())
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(self.client.get(feed_b).status_code, 404)
        self.assertIn("revoked", removed.json())

    # ---- 通知通道 ------------------------------------------------------

    def test_channel_default_disabled(self):
        response = self.client.get(
            f"/api/tasks/{self.task}/screening/notify/channels", headers=self.auth(0))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["enabled"])
        self.assertEqual(body["type"], "webhook")
        self.assertFalse(body["configured"])
        # 默认关：测试发送直接 409，零出站
        self.assertEqual(self.client.post(
            f"/api/tasks/{self.task}/screening/notify/test",
            headers=self.auth(0)).status_code, 409)
        # 成员不能读负责人通道
        self.assertEqual(self.client.get(
            f"/api/tasks/{self.task}/screening/notify/channels",
            headers=self.auth(1)).status_code, 403)

    def test_member_cannot_configure_channel(self):
        response = self.client.put(
            f"/api/tasks/{self.task}/screening/notify/channels",
            json={"enabled": True, "type": "webhook", "url": "https://example.test/hook"},
            headers=self.auth(1))
        self.assertEqual(response.status_code, 403, response.text)

    def test_webhook_payload_is_numbers_only(self):
        """载荷钉（2.1.5 安全修复后不再恒放行）：URL 公网校验真跑（DNS 解析
        桩指向公网 IP），出站经捕获型假 opener（零真实出站），断言载荷仍
        只含整数白名单字段——出站防护钉组见 test_2_1_5_notify_sec_pins.py。"""
        captured: list[dict] = []

        class _CaptureOpener:
            class _Resp:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def read(self, n):
                    return b"{}"

            def open(self, req, timeout=None):
                captured.append({
                    "url": req.full_url,
                    "headers": {key.lower(): value
                                for key, value in req.header_items()},
                    "raw": (req.data or b"").decode("utf-8", "replace"),
                })
                return self._Resp()

        self._seed(1, [("K0", "2026-11-08T09:00:00")])
        self._put_target(1, daily_target=2)
        with mock.patch.object(notify_core, "_resolve_host",
                               lambda host, port=443: ["93.184.216.34"]), \
             mock.patch.object(notify_core, "_OPENER", _CaptureOpener()):
            saved = self._put_channel({"enabled": True, "type": "webhook",
                                       "url": "https://hooks.example.test/hook"})
            self.assertEqual(saved.status_code, 200, saved.text)
            response = self.client.post(
                f"/api/tasks/{self.task}/screening/notify/test",
                json={"screener": self.users[1]["username"]}, headers=self.auth(0))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["status"], "sent")
        self.assertEqual(len(captured), 1)
        received = captured[0]
        payload = json.loads(received["raw"])
        self.assertEqual(set(payload), {
            "event_code", "date_yyyymmdd", "screened_today", "daily_target",
            "remaining", "streak", "longest_streak"})
        for key, value in payload.items():
            self.assertIs(type(value), int, f"{key} 不是整数：{value!r}")
        self.assertEqual(payload["event_code"], 2)  # 手工测试摘要
        self.assertEqual(payload["screened_today"], 1)
        self.assertEqual(payload["daily_target"], 2)
        self.assertTrue(received["headers"]["user-agent"].startswith("ReviewFlow/"))
        self.assertNotIn(SECRET_TITLE, received["raw"])
        self.assertNotIn(SECRET_ABSTRACT, received["raw"])

    def test_apprise_missing_degrades_instead_of_crashing(self):
        # 2.1.5 安全修复后凭据环境变量名必须以 REVIEWFLOW_NOTIFY_ 开头
        # （F1；非法前缀保存即 400，见 test_2_1_5_notify_sec_pins.py）。
        os.environ["REVIEWFLOW_NOTIFY_TEST_APPRISE_URL"] = "json://example.test"
        self.addCleanup(os.environ.pop, "REVIEWFLOW_NOTIFY_TEST_APPRISE_URL", None)
        saved = self._put_channel({
            "enabled": True, "type": "apprise",
            "url_env": "REVIEWFLOW_NOTIFY_TEST_APPRISE_URL"})
        self.assertEqual(saved.status_code, 200, saved.text)
        response = self.client.post(
            f"/api/tasks/{self.task}/screening/notify/test",
            headers=self.auth(0))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["status"], "channel_unavailable")
        self.assertEqual(body["reason"], "apprise_missing")
        self.assertEqual(body["channel"], "apprise")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
