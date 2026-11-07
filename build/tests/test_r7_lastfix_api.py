"""r7-lastfix 第 1 轮回归钉子（专属新文件，撤修复必红）。

- N-01(b10)（glm_verify_v2.md §14）：trace imports/commit 的 preview_sha256
  为 64 个非 ASCII 字符时，hmac.compare_digest 抛 TypeError 冒泡成 500；
  修复后在校验前做类型/ASCII 域检查收敛为 400，正确 64 位 hex 的既有
  对照行为逐位不变（匹配则放行到后续校验，不匹配 ASCII 仍 409）。
- GLM07-01（glm_verify_v2.md §8）：'Smith J.P.' 的内部带点两字母缩写被
  当姓氏（姓 'j.p'/名 'smith'，与 'Smith J. P.' 同人不同姓）；修复后
  _is_trailing_name_token 对内部带点缩写走带点分支，'Smith J.P.' 与
  带逗号写法 'Smith, J.P.' 同得姓 smith/名 j.p，既有用例逐位不变。
"""
import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
fixture = tempfile.TemporaryDirectory(prefix="rf_r7_lastfix_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient

from custom_backend.main import app
from custom_backend import auth as auth_mod
from coscreen import research_trace_export as exporter
from coscreen.normalize import (
    _is_trailing_name_token,
    first_author_given_names,
    first_author_surname,
)


class TraceImportCommitAsciiGuardTests(unittest.TestCase):
    """N-01(b10)：真实 app + TestClient（raise_server_exceptions=False，
    撤修复时 TypeError 以 500 响应形态现身使钉子变红，而不是在测试内抛出）。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        # 不走 /api/auth/register：全套 pytest 共享注册限流桶，直接建用户签票
        #（与 test_upload_security_regression.py 同款口径）。
        user = auth_mod.create_user("r7lastfix@example.test", "R7-LastFix-2026")
        cls.auth = {"Authorization": "Bearer " +
                    auth_mod.create_access_token(user["id"], user["username"])}
        # 单事件合法包（与 test_report_evidence._volume_snapshot 同款形态），
        # 走真实 build_export_zip/validate_import_package 两条产品路径。
        event = {"event_id": str(uuid.uuid4()), "store_id": "st-1", "sequence": 1,
                 "task_id": "T1", "actor_id": "42", "stage": "coding",
                 "action": "coding_value_changed", "subject_id": "S0",
                 "before": None, "after": {"field": "f0", "value": "v"},
                 "source": "manual", "recorded_at": "2026-01-01T00:00:00Z"}
        snapshot = {"stores": [{"partition": "personal", "store_id": "st-1",
                                "settings": {"status": "recording", "stages": ["coding"]},
                                "summary": {}, "events": [event]}]}
        cls.package = exporter.build_export_zip(snapshot, public=False)
        cls.parsed = exporter.validate_import_package(cls.package)
        cls.correct_sha = cls.parsed["package_sha256"]

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    def setUp(self):
        self.task = self.client.post(
            "/api/tasks", json={"name": self._testMethodName},
            headers=self.auth).json()["task_id"]
        self.base = f"/api/tasks/{self.task}/trace/imports/commit"

    def _commit(self, sha, with_aliases):
        data = {"preview_sha256": sha,
                "actor_aliases": json.dumps({"42": "Owner"}) if with_aliases else "{}",
                "subject_mappings": "{}", "confirm_unmapped": "true"}
        return self.client.post(
            self.base, files={"package": ("trace.zip", self.package, "application/zip")},
            data=data, headers=self.auth)

    def test_non_ascii_preview_sha256_is_400_not_500(self):
        # 撤修复必红：TypeError 曾经此处 500（glm_verify_v2.md §14 实测）。
        for sha in ("中" * 64, "a" * 63 + "中"):
            with self.subTest(sha=sha[:12] + "..."):
                r = self._commit(sha, with_aliases=True)
                self.assertEqual(r.status_code, 400,
                                 f"non-ASCII digest must be 4xx, got {r.status_code}: {r.text[:200]}")
                self.assertNotEqual(r.status_code, 500)
                self.assertIn("preview_sha256", r.text)

    def test_correct_ascii_digest_behavior_unchanged(self):
        # 对照（证据口径）：正确 64 位 hex 先过 hmac 门——不带别名时放行到
        # alias 校验得 400（glm_verify_v2.md §14 的 control 输出）；带全别名
        # 且确认未映射时完整提交 200。撤修复此用例仍绿（门行为不变）。
        no_alias = self._commit(self.correct_sha, with_aliases=False)
        self.assertEqual(no_alias.status_code, 400, no_alias.text[:200])
        self.assertIn("Confirm a display alias", no_alias.text)
        committed = self._commit(self.correct_sha, with_aliases=True)
        self.assertEqual(committed.status_code, 200, committed.text[:300])
        self.assertEqual(committed.json()["status"], "committed")
        self.assertEqual(committed.json()["imported"], 1)

    def test_wrong_ascii_digest_still_409(self):
        # 既有行为不变：合法 ASCII 但与包不符 -> 409（不因新增域检查改口径）。
        r = self._commit("ab" * 32, with_aliases=True)
        self.assertEqual(r.status_code, 409, r.text[:200])
        self.assertIn("differs from the reviewed preview", r.text)


class TrailingDottedInitialTests(unittest.TestCase):
    """GLM07-01：内部带点缩写在末位按缩写（名），撤修复必红。"""

    def test_smith_jp_no_comma_equals_comma_form(self):
        # 撤修复必红：修复前 'Smith J.P.' 得姓 'j.p'/名 'smith'（证据 §8 实测）。
        self.assertEqual(first_author_surname("Smith J.P."), "smith")
        self.assertEqual(first_author_given_names("Smith J.P."), "j.p")
        # 与带逗号写法逐位一致（同作者不再被拆成两种姓）。
        self.assertEqual(first_author_surname("Smith J.P."),
                         first_author_surname("Smith, J.P."))
        self.assertEqual(first_author_given_names("Smith J.P."),
                         first_author_given_names("Smith, J.P."))
        # 与 'Smith J. P.'（空格分隔缩写）同姓——docstring :232-234 宣称的口径。
        self.assertEqual(first_author_surname("Smith J.P."),
                         first_author_surname("Smith J. P."))

    def test_trailing_token_dotted_forms(self):
        # 撤修复必红：修复前 _is_trailing_name_token('J.P.') 为 False。
        self.assertTrue(_is_trailing_name_token("J.P."))
        self.assertTrue(_is_trailing_name_token("J.P"))
        self.assertTrue(_is_trailing_name_token("j.p."))
        # 既有真值表逐位不变（glm_verify_v2.md §8 动态复现列）。
        self.assertTrue(_is_trailing_name_token("Ja."))
        self.assertTrue(_is_trailing_name_token("ja"))
        self.assertTrue(_is_trailing_name_token("J."))
        self.assertTrue(_is_trailing_name_token("JA"))
        self.assertFalse(_is_trailing_name_token("Wu"))  # 混合大小写无点=真实姓氏
        self.assertFalse(_is_trailing_name_token("J.P.S."))  # 三字母仍超两字母域
        self.assertFalse(_is_trailing_name_token("..."))

    def test_existing_name_cases_bit_for_bit(self):
        # 既有全部用例逐位不变：R6-D3 表（test_r6_dedup_regressions 原表）+
        # 证据 §8 表 + U1-P2 机构/助词/空值口径。
        cases = [
            ("Smith J. P.", "smith", "j p"),
            ("Smith ja", "smith", "ja"),
            ("Smith, J.P.", "smith", "j.p"),
            ("Smith J.", "smith", "j"),
            ("John Wu", "wu", "john"),
            ("Wu, John", "wu", "john"),
            ("Wei Li", "li", "wei"),
            ("Li, Wei", "li", "wei"),
            ("Smith, J. A.", "smith", "j a"),
            ("Smith JA", "smith", "ja"),
            ("Maria de la Cruz", "cruz", "maria de la"),
            ("van der Berg, J.", "berg", "j"),
            ("van Dijk, A.", "dijk", "a"),
            ("Smith J.A.", "smith", "j.a"),
        ]
        for name, want_surname, want_given in cases:
            with self.subTest(name=name):
                self.assertEqual(first_author_surname(name), want_surname)
                self.assertEqual(first_author_given_names(name), want_given)
        # 机构作者与空值口径（U1-P2）不变。
        self.assertEqual(first_author_surname("The Cochrane Collaboration"),
                         "the cochrane collaboration")
        self.assertEqual(first_author_surname(""), "")
        self.assertEqual(first_author_surname(None), "")
        self.assertEqual(first_author_surname("; "), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
