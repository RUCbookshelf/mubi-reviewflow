"""r7-lunafix L-03/L-04 钉子：release workflow 最小权限 + 发行说明版本一致。

- L-03（修前红）：workflow 级 ``contents: write`` 被 build/publish 两个作业
  继承，build 的 checkout 未关凭据持久化——构建/测试代码（pip 安装、pytest、
  校验器）持写令牌（供应链爆炸半径）。修后必须：
  workflow 级 ``contents: read``；build 解析后权限为 read 且其 checkout
  ``persist-credentials: false``；仅 publish 显式 ``contents: write``。
- L-04（修前红）：``VERSION`` 为 2.1.4 而 ``RELEASE_NOTES.md:1`` 标题仍是
  2.1.3——发布页（body_path 引用该文件）会带旧正文。修后标题必须与
  VERSION 一致，且带对应版本的变更摘要节（不改写旧版原文正文）。
"""
import unittest
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "release.yml"
NOTES = REPO / "RELEASE_NOTES.md"
VERSION = REPO / "VERSION"


class LunaReleaseWorkflowLeastPrivilegeTests(unittest.TestCase):
    """L-03：release.yml 最小权限（修前 build 解析为 contents: write = 红）。"""

    def setUp(self):
        yaml = pytest.importorskip("yaml")  # CI 由 release.yml 安装 pyyaml
        self.doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    def _checkout_steps(self, job):
        return [step for step in job.get("steps", [])
                if str(step.get("uses", "")).startswith("actions/checkout")]

    def test_l03_workflow_default_permissions_are_read_only(self):
        self.assertEqual(self.doc.get("permissions"), {"contents": "read"},
                         "workflow 级权限必须降为 contents: read（最小权限）")

    def test_l03_build_job_resolves_to_read_and_does_not_persist_credentials(self):
        build = self.doc["jobs"]["build"]
        resolved = build.get("permissions", self.doc.get("permissions"))
        self.assertEqual(resolved, {"contents": "read"},
                         "build 作业解析后权限必须为 contents: read"
                         "（修前继承 workflow 的 write）")
        checkouts = self._checkout_steps(build)
        self.assertTrue(checkouts, "build 作业必须存在 actions/checkout 步骤")
        for step in checkouts:
            self.assertEqual(
                (step.get("with") or {}).get("persist-credentials"), False,
                "build 作业的 checkout 必须 persist-credentials: false"
                "（默认 true 会把写令牌留进本地 git 配置）")

    def test_l03_publish_job_explicitly_grants_contents_write(self):
        publish = self.doc["jobs"]["publish"]
        self.assertEqual(publish.get("permissions"), {"contents": "write"},
                         "仅 publish 作业（打标签/推标签/建发行版）显式持写权限")


class LunaReleaseNotesVersionTests(unittest.TestCase):
    """L-04：RELEASE_NOTES.md 标题与 VERSION 一致（修前标题 2.1.3 = 红）。"""

    def test_l04_notes_title_matches_version_file(self):
        version = VERSION.read_text(encoding="utf-8").strip()
        self.assertTrue(version, "VERSION 文件为空")
        first_line = NOTES.read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(first_line, f"# Mubi·ReviewFlow {version}",
                         "发行说明 H1 必须与 VERSION 一致（body_path 直接发布该文件）")

    def test_l04_notes_carry_current_version_summary_section(self):
        version = VERSION.read_text(encoding="utf-8").strip()
        notes = NOTES.read_text(encoding="utf-8")
        self.assertIn(f"## {version} 变更摘要", notes,
                      "当前版本的变更摘要节必须存在（不改动旧版原文正文）")


if __name__ == "__main__":
    unittest.main()
