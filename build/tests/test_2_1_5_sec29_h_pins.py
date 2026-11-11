# -*- coding: utf-8 -*-
"""2.1.5 安全修复波 H 组（fix/2.1.5-sec29）· deepseek 复审四发现钉组（专属新文件）。

与 test_2_1_5_streak.py 同款 ``COBOOKSHELF_DATA_DIR`` 导入期钉约定：本文件
必须在**独立 pytest 进程**运行（仓库门禁/CI 对 2.1.5 专属钉文件本就分段）。

H组#2（低危·订阅令牌无 TTL）：

- 过期令牌（``expires_at`` 已过）→ ICS 订阅源 404，且与「未知任务」的
  404 **逐字节同形**（状态码+响应体+content-type，与撤销同形状，不新增
  可区分错误形状）；未过期（``expires_at`` 在未来）同一令牌仍 200（对照
  组：404 确由过期驱动，不是形状被改坏）。
- 存量记录（无 ``expires_at``）按 ``created_at + ICS_TOKEN_TTL_DAYS`` 兜底
  有界：400 天前创建 → 404 同形；刚创建 → 200。

H组#3（低危·令牌表无上限）：

- 每成员活跃令牌上限 ``ICS_TOKENS_PER_MEMBER_LIMIT``：第 11 枚生成后，
  列表恰剩 10 行（全部 ``revoked_at=None``）、最旧一枚的订阅源立即 404
  （同形）、最新一枚仍 200；其他成员不受影响。
- 写时裁剪已撤销条目：手动撤销的记录在下一次**生成**写入后从文件消失
  （再撤销该 id → 404）；紧接着的重复撤销仍幂等 200（刚撤销的记录保留
  到下一次生成写入，既有终审钉语义不破坏）。

H组#1（低危·ICS 404 时序差）：

- 任务不存在路径补等量固定工作（令牌表读取+摘要比对）后，未知任务/
  已知任务+坏令牌/形状非法 id 三类 404 仍逐字节同形（垫片绝不改变响应
  形状；形状非法 id 不做读取，防未校验 id 拼路径的读探针）。

H组#4（低危·users.db 权限）：

- 建库（首次 ``_connect``）即 ``os.chmod(path, 0o600)``：mock ``os.chmod``
  断言被调用且实参为 ``(str(users.db 路径), 0o600)``；库已存在时不重复
  chmod；chmod 抛 ``OSError``（Windows/FAT/网络盘 no-op 等价）不影响
  建号/鉴权主路径。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

fixture = tempfile.TemporaryDirectory(prefix="rf_215sec29h_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient  # noqa: E402

from coscreen import tasks as tasks_mod  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend import streak_api  # noqa: E402
from custom_backend.main import app, DATA_DIR  # noqa: E402


def _utc(seconds_from_now: float) -> str:
    return (datetime.now(timezone.utc)
            + timedelta(seconds=seconds_from_now)).isoformat(timespec="seconds")


class _HGroupApiMixin:
    """三个 API 钉类共用的最小环境（负责人 + 一名成员）。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        # 三个钉类共用同一 fixture 库：用户名按类名加后缀防撞（重复建号
        # 会撞 users.db 唯一约束）。
        tag = cls.__name__.lower()
        cls.users = []
        for i in range(2):
            user = auth_mod.create_user(
                f"{tag}{i}@example.test", "R215H-Sec29-Pins-2026")
            cls.users.append({
                "user_id": user["id"],
                "username": user["username"],
                "token": auth_mod.create_access_token(user["id"], user["username"]),
            })

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def auth(self, index=0):
        return {"Authorization": "Bearer " + self.users[index]["token"]}

    def setUp(self):
        self.task = self.client.post(
            "/api/tasks", json={"name": self._testMethodName},
            headers=self.auth()).json()["task_id"]
        self.info = tasks_mod.find_task(self.task, DATA_DIR)
        self.assertIsNotNone(self.info)

    # ---- 便捷封装 -------------------------------------------------------

    def _create_token(self, index=0) -> dict:
        response = self.client.post(
            f"/api/tasks/{self.task}/screening/ics-tokens",
            headers=self.auth(index))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _tokens_file(self) -> Path:
        return self.info.dir / "daily_nudge_tokens.json"

    def _rewrite_token(self, token: str, **overrides):
        """按明文令牌定位记录并覆盖字段后写回（模拟存量/手工编辑库）。"""
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        path = self._tokens_file()
        data = json.loads(path.read_text(encoding="utf-8"))
        record = next(row for row in data["tokens"] if row["sha256"] == digest)
        for key, value in overrides.items():
            if value is None:
                record.pop(key, None)
            else:
                record[key] = value
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return record

    def _fetch(self, task_id: str, token: str):
        response = self.client.get(
            f"/api/tasks/{task_id}/screening/daily-status.ics?token={token}")
        return (response.status_code, response.content,
                response.headers.get("content-type"))

    def _unknown_task_404(self, token: str = "bad-token"):
        prefix, _, _ = self.task.rpartition("-")
        unknown = prefix + "-ffff"
        self.assertNotEqual(unknown, self.task)
        result = self._fetch(unknown, token)
        self.assertEqual(result[0], 404)
        self.assertEqual(result[1], b'{"detail":"Not Found"}', result)
        return result


class IcsTokenTtlPins(_HGroupApiMixin, unittest.TestCase):
    """H组#2：TTL 过期与撤销/未知任务同 404 形状；未过期不受影响。"""

    def test_expired_token_404_identical_to_unknown_task(self):
        created = self._create_token()
        self.assertIn("expires_at", created)  # 新令牌显式带过期时间
        self.assertIn("token", created)
        # 未动库时订阅源正常（对照组基线）
        self.assertEqual(self._fetch(self.task, created["token"])[0], 200)

        self._rewrite_token(created["token"], expires_at=_utc(-5))  # 5 秒前过期
        expired = self._fetch(self.task, created["token"])
        self.assertEqual(expired, self._unknown_task_404(),
                         "过期令牌必须与未知任务的 404 逐字节同形")

    def test_future_expiry_token_still_serves(self):
        created = self._create_token()
        self._rewrite_token(created["token"], expires_at=_utc(3600))
        served = self._fetch(self.task, created["token"])
        self.assertEqual(served[0], 200, served)
        self.assertTrue(served[2].startswith("text/calendar"))

    def test_legacy_record_without_expires_at_is_bounded_by_created_at(self):
        ttl = streak_api.ICS_TOKEN_TTL_DAYS
        self.assertGreater(ttl, 0)

        created = self._create_token()
        # 存量形状：无 expires_at；created_at 早于 TTL → 过期 404（同形）
        self._rewrite_token(created["token"], expires_at=None,
                            created_at=_utc(-(ttl + 1) * 86400))
        stale = self._fetch(self.task, created["token"])
        self.assertEqual(stale, self._unknown_task_404(),
                         "legacy 令牌按 created_at+TTL 兜底过期，同形 404")

        # 刚创建的 legacy 形状（无 expires_at）仍可用：不误杀存量有效令牌
        fresh = self._create_token()
        self._rewrite_token(fresh["token"], expires_at=None)
        self.assertEqual(self._fetch(self.task, fresh["token"])[0], 200)


class IcsTokenCapPins(_HGroupApiMixin, unittest.TestCase):
    """H组#3：每成员活跃令牌上限 + 写时裁剪已撤销条目。"""

    def test_cap_revokes_oldest_and_prunes_on_write(self):
        limit = streak_api.ICS_TOKENS_PER_MEMBER_LIMIT
        self.assertGreaterEqual(limit, 1)
        created = [self._create_token() for _ in range(limit + 1)]

        # 列表恰剩 limit 行，全部活跃（行形状不变，不新增字段）
        listed = self.client.get(
            f"/api/tasks/{self.task}/screening/ics-tokens",
            headers=self.auth()).json()
        self.assertTrue(listed["available"])
        self.assertEqual(len(listed["tokens"]), limit, listed)
        for row in listed["tokens"]:
            self.assertEqual(set(row), {"token_id", "created_at", "revoked_at"})
            self.assertIsNone(row["revoked_at"])

        # 最旧一枚（超出上限被先撤最旧）订阅源立即失效，404 与未知任务同形
        self.assertEqual(self._fetch(self.task, created[0]["token"]),
                         self._unknown_task_404())
        # 最新一枚（本次生成）仍可用
        self.assertEqual(self._fetch(self.task, created[-1]["token"])[0], 200)

        # 文件层面：该成员恰 limit 条活跃记录，且没有任何已撤销条目残留
        data = json.loads(self._tokens_file().read_text(encoding="utf-8"))
        own = [row for row in data["tokens"]
               if row["username"] == self.users[0]["username"]]
        self.assertEqual(len(own), limit, own)
        self.assertFalse(any(row.get("revoked_at") for row in data["tokens"]),
                         data["tokens"])
        # 被裁掉的最旧令牌摘要不再落盘（明文令牌本就永不落盘）
        dropped = hashlib.sha256(created[0]["token"].encode("utf-8")).hexdigest()
        self.assertNotIn(dropped, self._tokens_file().read_text(encoding="utf-8"))

    def test_cap_is_per_member(self):
        limit = streak_api.ICS_TOKENS_PER_MEMBER_LIMIT
        invited = self.client.post(
            f"/api/tasks/{self.task}/collaboration/invite",
            json={"username": self.users[1]["username"], "role": "coder"},
            headers=self.auth())
        self.assertEqual(invited.status_code, 201, invited.text)
        for _ in range(limit):
            self._create_token(0)
        member_token = self._create_token(1)  # 成员首枚不受负责人占额影响
        self.assertEqual(
            self._fetch(self.task, member_token["token"])[0], 200)

    def test_revoked_entries_pruned_on_next_write_and_re_revoke_idempotent(self):
        created = self._create_token()
        token_id = created["token_id"]
        # 手动撤销 → 200；紧接着重复撤销仍幂等 200（刚撤销的记录保留到
        # 下一次生成写入——既有终审钉语义不因裁剪破坏）
        first = self.client.delete(
            f"/api/tasks/{self.task}/screening/ics-tokens/{token_id}",
            headers=self.auth())
        self.assertEqual(first.status_code, 200, first.text)
        again = self.client.delete(
            f"/api/tasks/{self.task}/screening/ics-tokens/{token_id}",
            headers=self.auth())
        self.assertEqual(again.status_code, 200, again.text)

        # 下一次生成写入把已撤销条目裁掉：文件无 revoked 残留，再撤销该
        # id 只能 404（记录已不在表内）
        self._create_token()
        data = json.loads(self._tokens_file().read_text(encoding="utf-8"))
        self.assertFalse(any(row.get("revoked_at") for row in data["tokens"]),
                         data["tokens"])
        self.assertTrue(data["tokens"])
        after_prune = self.client.delete(
            f"/api/tasks/{self.task}/screening/ics-tokens/{token_id}",
            headers=self.auth())
        self.assertEqual(after_prune.status_code, 404, after_prune.text)


class IcsTimingWorkShapePins(_HGroupApiMixin, unittest.TestCase):
    """H组#1：任务不存在路径补固定工作后，三类 404 仍逐字节同形。"""

    def test_unknown_task_bad_token_and_malformed_id_share_404(self):
        self._create_token()  # 令牌表存在（已知任务路径读真实文件）
        unknown = self._unknown_task_404()
        bad_token = self._fetch(self.task, "bad-token")
        self.assertEqual(bad_token, unknown,
                         "已知任务+坏令牌必须与未知任务逐字节同形")
        malformed = self.client.get(
            "/api/tasks/..%2F..%2Fetc/screening/daily-status.ics?token=bad-token")
        self.assertEqual(
            (malformed.status_code, malformed.content,
             malformed.headers.get("content-type")), unknown,
            "形状非法 id 不做等量读取，但 404 形状不得改变")

    def test_wellformed_unknown_task_404_shape_unchanged(self):
        """形状合法但不存在的任务 id（等量固定工作真正执行的路径）同形。"""
        prefix, _, _ = self.task.rpartition("-")
        for suffix in ("-ffff", "-0000", "-" + "z" * 8):
            with self.subTest(task_id=prefix + suffix):
                self.assertEqual(self._fetch(prefix + suffix, "bad-token"),
                                 self._unknown_task_404())


class UsersDbPermissionPins(unittest.TestCase):
    """H组#4：建库即 chmod 0600（mock os.chmod 断言被调用；POSIX 语义钉）。"""

    def test_users_db_chmod_0600_on_creation_only(self):
        with tempfile.TemporaryDirectory(prefix="rf_215h_auth_") as data_root:
            db_path = Path(data_root) / "users.db"
            with mock.patch.dict(os.environ,
                                 {"COBOOKSHELF_DATA_DIR": data_root}), \
                 mock.patch.object(auth_mod.os, "chmod") as chmod_mock:
                first = auth_mod.create_user(
                    "h29pin1@example.test", "H29-Pin-Chmod-2026")
                self.assertIn("id", first)
                # 建库即收紧：恰一次，目标就是 users.db，模式 0600
                chmod_mock.assert_called_once_with(str(db_path), 0o600)
                # 库已存在：后续连接不再重复 chmod
                second = auth_mod.create_user(
                    "h29pin2@example.test", "H29-Pin-Chmod-2026")
                self.assertIn("id", second)
                chmod_mock.assert_called_once()
            self.assertTrue(db_path.exists())

    def test_chmod_failure_is_noop_for_auth_path(self):
        """Windows/FAT/网络盘等 chmod OSError：no-op，建号/验证不受影响。"""
        with tempfile.TemporaryDirectory(prefix="rf_215h_auth2_") as data_root:
            with mock.patch.dict(os.environ,
                                 {"COBOOKSHELF_DATA_DIR": data_root}), \
                 mock.patch.object(auth_mod.os, "chmod",
                                   side_effect=OSError("no POSIX bits")):
                user = auth_mod.create_user(
                    "h29pin3@example.test", "H29-Pin-Noop-2026")
                # chmod 失败被吞掉：账号建好、口令可验（主路径不受影响）
                self.assertIn("id", user)
                record = auth_mod.get_user("h29pin3@example.test")
                self.assertIsNotNone(record)
                self.assertTrue(auth_mod.verify_password(
                    "H29-Pin-Noop-2026", record["password_hash"]))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
