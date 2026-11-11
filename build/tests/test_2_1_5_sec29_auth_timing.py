# -*- coding: utf-8 -*-
"""sec29 回归钉（deepseek 异构安全复审 2026-11-11·凭据与令牌·登录时序）。

authenticate 的「用户不存在」路径此前每次现算 rounds=12 hashpw——缺失用户
= hashpw+checkpw 两次 bcrypt、真实用户 = checkpw 一次：~2× 反向时延差是
用户名枚举的时序侧信道，且未认证者可用随机用户名把每请求 CPU 放大一倍。
修复=哑哈希首次计算后复用（两条路径每请求恰好一次 checkpw、缺失路径零
hashpw）+ checkpw 输入统一截 72 字节（bcrypt 5.x 对超长抛 ValueError 曾
被折叠 False；截断与 bcrypt「前 72 字节等价」语义对齐，注册侧仍拒绝
超长口令）。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

fixture = tempfile.TemporaryDirectory(prefix="rf_sec29_auth_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import bcrypt as bcrypt_mod  # noqa: E402

from custom_backend import auth as auth_mod  # noqa: E402


class AuthTimingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 暖哑哈希缓存：首个 authenticate 允许计算一次，后续全部走复用。
        cls.warm = auth_mod.authenticate("no-such-user-warm", "whatever-0")
        cls.user = auth_mod.create_user("timinguser", "Timing-Pass-2026")

    @classmethod
    def tearDownClass(cls):
        fixture.cleanup()

    def test_missing_user_path_single_checkpw_no_hashpw(self):
        with mock.patch.object(auth_mod.bcrypt, "hashpw",
                               wraps=auth_mod.bcrypt.hashpw) as hash_spy, \
                mock.patch.object(auth_mod.bcrypt, "checkpw",
                                  wraps=auth_mod.bcrypt.checkpw) as check_spy:
            self.assertIsNone(auth_mod.authenticate("no-such-user-1", "whatever-1"))
            self.assertIsNone(auth_mod.authenticate("timinguser", "wrong-pass-x"))
        self.assertIsNone(self.warm)
        self.assertEqual(hash_spy.call_count, 0,
                         "缺失用户路径不得再现算 hashpw（哑哈希必须复用）")
        self.assertEqual(check_spy.call_count, 2,
                         "两条路径每请求恰好一次 checkpw")

    def test_dummy_hash_computed_once(self):
        first = auth_mod._timing_equalizer_hash()
        with mock.patch.object(auth_mod.bcrypt, "hashpw",
                               wraps=auth_mod.bcrypt.hashpw) as hash_spy:
            second = auth_mod._timing_equalizer_hash()
        self.assertEqual(first, second)
        self.assertEqual(hash_spy.call_count, 0)

    def test_verify_truncates_to_72_bytes(self):
        # bcrypt 72 字节等价：注册 72 字节口令后，同前缀超长输入截断仍匹配
        # （修复前 bcrypt 5.x ValueError→False，同一口令反而登不进）。
        base72 = "a" * 72
        auth_mod.create_user("trunc72", base72)
        self.assertIsNotNone(auth_mod.authenticate("trunc72", base72))
        self.assertIsNotNone(auth_mod.authenticate("trunc72", base72 + "EXTRA"))

    def test_bad_creds_none_and_good_roundtrip(self):
        self.assertIsNone(auth_mod.authenticate("timinguser", "wrong-pass-y"))
        self.assertIsNone(auth_mod.authenticate("ghost-user-z", "wrong-pass-z"))
        got = auth_mod.authenticate("timinguser", "Timing-Pass-2026")
        self.assertEqual(got["username"], "timinguser")

    def test_verify_password_never_raises(self):
        # 超长输入不抛（截断语义）；损坏哈希按不匹配（绝不异常上冒）。
        self.assertFalse(auth_mod.verify_password("x" * 300, "not-a-hash"))
        stored = bcrypt_mod.hashpw(b"b" * 72,
                                   bcrypt_mod.gensalt(rounds=4)).decode("ascii")
        self.assertTrue(auth_mod.verify_password("b" * 200, stored))


if __name__ == "__main__":
    unittest.main()
