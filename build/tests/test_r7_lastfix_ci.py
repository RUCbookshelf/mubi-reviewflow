"""R7-lastfix CI 守护钉子（MM11-01 / MM11-02）——修复被撤销即红。

MM11-01: 三个 node --test 单元文件（import_review_model / import_review_updates /
        ml_batch 的 .test.cjs，17 用例）此前只有文档引用、零自动执行。
        钉两件事：release.yml 校验步骤确有 node --test 接线（整段或任一
        文件被撤即红）；以与 CI 完全相同的命令本机实跑必须 17/17 全绿。
MM11-02: test_collaboration_hardening 的别名读守护旧形态在 POSIX 上为
        "精确序列 :371 FAIL / :372 PASS"（复审实测，非"永远绿"）。本文件
        在 POSIX 语义仿真下回放旧精确序列（红侧：旧 :371 的断言对象不
        存在、:372 恒真空转）；验证修复后形态在真实语义与 POSIX 语义下
        都绿（绿侧）；并证明锁键规范化被破坏回 URL 原始串时，修复后的
        assertNotIn(alias, keys) 必红（破坏即红）。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# 与 test_r7_mm7_relchain.py 相同的 node 解析口径（RF_NODE → PATH → 本机兜底）。
NODE = (
    os.environ.get("RF_NODE")
    or shutil.which("node")
    or r"C:\Users\16073\AppData\Local\hermes\node\node.exe"
)

NODE_TEST_FILES = (
    "build/tests/import_review_model.test.cjs",
    "build/tests/import_review_updates.test.cjs",
    "build/tests/ml_batch.test.cjs",
)


# --------------------------------------------------------------------------- MM11-01

def test_mm11_01_release_yml_wires_node_test_into_the_release_gate():
    """MM11-01 静态钉：release.yml 校验步骤必须接线 node --test 三文件。

    撤掉接线（整段 node --test 行，或漏掉任一 .test.cjs）本断言必红。"""
    text = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    start = text.index("Run static validators")
    end = text.index("actions/upload-artifact", start)
    step = text[start:end]
    # 锚点自检：仍是那条跑 pytest 的发布门禁步骤
    assert "python -m pytest build/tests -q" in step, (
        "锚点漂移：校验步骤不再包含 pytest 门禁"
    )
    assert "node --test" in step, (
        "MM11-01: release.yml 校验步骤必须运行 node --test——三个 .test.cjs "
        "（17 用例）此前零自动执行，只靠文档引用"
    )
    for rel in NODE_TEST_FILES:
        assert rel in step, f"MM11-01: {rel} 必须出现在 node --test 接线中"


@pytest.mark.skipif(not os.path.exists(NODE), reason="node runtime not found")
def test_mm11_01_node_test_suites_green_under_the_ci_command():
    """MM11-01 行为钉：以 CI 同款命令（cwd=仓库根）实跑三文件。

    17 用例必须全绿；删用例/改挂会让 pass 计数或退出码变化而变红。"""
    proc = subprocess.run(
        [NODE, "--test", *NODE_TEST_FILES],
        cwd=str(REPO), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out[-3000:]
    # TAP 汇总行的键名随 Node 大版本漂移（22: "# pass 17"；25: "tests 17"）。
    # 终扫 FS-02：只钉「全过 + 用例数不缩水」，不绑具体键名。
    fail_m = re.search(r"#\s*fail\s+(\d+)", out)
    assert fail_m is None or int(fail_m.group(1)) == 0, out[-3000:]
    counts = [int(m) for m in re.findall(r"#\s*(?:pass|tests)\s+(\d+)", out)]
    assert counts and max(counts) >= 17, (
        f"17 个用例的计数不得缩水（复审口径 7+5+5），实际汇总：{counts}"
    )


# --------------------------------------------------------------------------- MM11-02

def test_mm11_02_fixed_guard_shape_is_wired_in_the_collaboration_suite():
    """MM11-02 静态钉：test_collaboration_hardening 的修复形态不得回退。

    恢复旧形态（语义分支/规范读铺垫/POSIX 伴随用例被撤）即红；第 2 轮
    评审追加：report-evidence 别名读用例同样不得回到无条件 Windows 前提
    （旧形态 r2==200 在真实 POSIX 实测 404 != 200 必红）。"""
    text = (REPO / "build" / "tests" / "test_collaboration_hardening.py").read_text(
        encoding="utf-8")
    assert "test_alias_read_lock_key_guard_runs_on_posix_via_windows_equivalent_lookup" in text, (
        "MM11-02: POSIX 可运行的锁键规范化伴随用例不得移除——只留平台分支 "
        "会让 POSIX 上的锁键口径零覆盖"
    )
    assert "alias_resolves = tasks_mod.find_task(alias, DATA_DIR) is not None" in text, (
        "MM11-02: 别名读用例必须按实测解析语义分支断言，不得回到 "
        "恒真（POSIX :372）/恒红（POSIX :371）的旧形态"
    )
    assert 'f"/api/tasks/{alias}/report-evidence"' in text, (
        "MM11-02 第 2 轮：report-evidence 别名读必须走语义分支 + Windows "
        "等价 lookup 阶段（别名变量化），不得回到无条件 200 断言"
    )
    assert "self.task}./report-evidence" not in text, (
        "MM11-02 第 2 轮：report-evidence 别名读不得以 URL 原始串拼访问且 "
        "无条件断言 200（真实 POSIX 上该请求 404）"
    )


sys.path.insert(0, str(REPO))
# 与 test_collaboration_hardening 相同的沙箱口径：全套 pytest 下 main 的
# DATA_DIR 绑定首个导入模块的 fixture（该文件先被收集）；独立运行本文件
# 时由此处兜底。两种顺序下任务/用户都落在临时沙箱内。
_fixture = tempfile.TemporaryDirectory(prefix="rf_lastfix_ci_")
os.environ["COBOOKSHELF_DATA_DIR"] = _fixture.name
os.environ.setdefault("REVIEWFLOW_MODE", "server")

from fastapi.testclient import TestClient  # noqa: E402

from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend.main import app, DATA_DIR  # noqa: E402
from coscreen import tasks as tasks_mod  # noqa: E402


class MM1102AliasReadGuardNails(unittest.TestCase):
    """MM11-02 红绿两侧（复审实测口径：精确序列 :371 FAIL / :372 PASS）。"""

    @classmethod
    def setUpClass(cls):
        # raise_server_exceptions=False：红侧场景只关心锁表事实，端点自身
        # 的 4xx/5xx 一律还原成响应而不中止断言。
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        user = auth_mod.create_user(
            f"lastfix{uuid.uuid4().hex[:8]}@example.test", "Lastfix-Test-2026")
        cls.token = auth_mod.create_access_token(user["id"], user["username"])

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        _fixture.cleanup()

    def auth(self):
        return {"Authorization": "Bearer " + self.token}

    def _new_task(self) -> str:
        r = self.client.post("/api/tasks",
                             json={"name": "mm1102-" + uuid.uuid4().hex[:8]},
                             headers=self.auth())
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["task_id"]

    def _swap_find_task(self, replacement):
        original = tasks_mod.find_task
        tasks_mod.find_task = replacement
        self.addCleanup(setattr, tasks_mod, "find_task", original)
        return original

    def test_old_exact_sequence_is_red_on_posix_alias_semantics(self):
        """红侧：旧用例的精确序列（只发两个别名 GET）× POSIX 语义。

        复审 WSL 实测：':371 assertIn(self.task, keys)' FAIL、':372 恒真'。
        本钉子钉住该事实基础——旧形态在 POSIX CI runner（ubuntu/macos）
        上复活即红，且 :372 的守护从未真实执行。"""
        task = self._new_task()
        alias = task + "."
        original = self._swap_find_task(
            lambda tid, *args, **kwargs: (
                None if tid == alias else original(tid, *args, **kwargs)))
        r1 = self.client.get(f"/api/tasks/{alias}/coding/dimensions",
                             headers=self.auth())
        r2 = self.client.get(f"/api/tasks/{alias}/coding/scheme",
                             headers=self.auth())
        runtime = app.state.review_workflow
        self.assertEqual((r1.status_code, r2.status_code), (404, 404))
        self.assertNotIn(task, runtime._locks,
                         "旧 :371 的断言对象缺失——assertIn(self.task, keys) 必红")
        self.assertNotIn(alias, runtime._locks,
                         "旧 :372 因此恒真（空转）：别名读在取锁前已 404")

    def test_fixed_sequence_is_green_under_both_alias_semantics(self):
        """绿侧：修复后形态（语义分支 + 规范读铺垫）在真实语义与 POSIX
        语义下都成立——旧形态红、修复后不红，且断言两侧均非恒真。"""
        runtime = app.state.review_workflow
        # (1) 真实平台语义（Windows：尾点别名被 Win32 路径归一解析）
        task = self._new_task()
        alias = task + "."
        resolves = tasks_mod.find_task(alias, DATA_DIR) is not None
        statuses = tuple(
            self.client.get(f"/api/tasks/{alias}/{endpoint}",
                            headers=self.auth()).status_code
            for endpoint in ("coding/dimensions", "coding/scheme"))
        self.assertEqual(statuses, (200, 200) if resolves else (404, 404))
        canonical = self.client.get(f"/api/tasks/{task}/coding/dimensions",
                                    headers=self.auth())
        self.assertEqual(canonical.status_code, 200, canonical.text)
        self.assertIn(task, runtime._locks)
        self.assertNotIn(alias, runtime._locks)

        # (2) POSIX 语义仿真：尾点别名不解析（Linux/macOS runner 的行为）
        task2 = self._new_task()
        alias2 = task2 + "."
        original = self._swap_find_task(
            lambda tid, *args, **kwargs: (
                None if tid == alias2 else original(tid, *args, **kwargs)))
        for endpoint in ("coding/dimensions", "coding/scheme"):
            r = self.client.get(f"/api/tasks/{alias2}/{endpoint}",
                                headers=self.auth())
            self.assertEqual(r.status_code, 404, r.text)
        canonical2 = self.client.get(f"/api/tasks/{task2}/coding/dimensions",
                                     headers=self.auth())
        self.assertEqual(canonical2.status_code, 200, canonical2.text)
        self.assertIn(task2, runtime._locks)
        self.assertNotIn(alias2, runtime._locks)

    def test_report_evidence_old_form_red_on_posix_alias_semantics(self):
        """MM11-02 第 2 轮红侧：report-evidence 别名读旧形态（无条件断言
        200）× POSIX 语义。复审真实 POSIX（WSL Ubuntu）实测 404 != 200
        （:424 FAILED）；本钉子在 POSIX 语义仿真下回放同一精确序列并钉住
        红侧事实——发布门禁在 ubuntu/macos 跑该文件时旧形态必红。"""
        task = self._new_task()
        alias = task + "."
        original = self._swap_find_task(
            lambda tid, *args, **kwargs: (
                None if tid == alias else original(tid, *args, **kwargs)))
        r1 = self.client.get(f"/api/tasks/{task}/report-evidence",
                             headers=self.auth())
        r2 = self.client.get(f"/api/tasks/{alias}/report-evidence",
                             headers=self.auth())
        self.assertEqual(r1.status_code, 200, r1.text)          # 规范读基线可达
        self.assertEqual(r2.status_code, 404, r2.text)
        self.assertIn("任务不存在", r2.text)
        # 旧断言 assertEqual(r2.status_code, 200) 在该形态下必红（404 != 200）

    def test_report_evidence_fixed_form_green_under_both_alias_semantics(self):
        """MM11-02 第 2 轮绿侧：修复后形态——真实语义（Windows 200 +
        can_write 对齐）；POSIX 语义（404 分支）；POSIX 语义 + Windows
        等价 lookup（can_write 对齐在两种平台真实执行）。"""
        # (1) 真实平台语义
        task = self._new_task()
        alias = task + "."
        r1 = self.client.get(f"/api/tasks/{task}/report-evidence",
                             headers=self.auth())
        self.assertEqual(r1.status_code, 200, r1.text)
        self.assertTrue(r1.json()["can_write"], r1.text)
        resolves = tasks_mod.find_task(alias, DATA_DIR) is not None
        r2 = self.client.get(f"/api/tasks/{alias}/report-evidence",
                             headers=self.auth())
        if resolves:
            self.assertEqual(r2.status_code, 200, r2.text)
            self.assertEqual(r1.json()["can_write"], r2.json()["can_write"])
        else:
            self.assertEqual(r2.status_code, 404, r2.text)

        # (2) POSIX 语义仿真：别名读 404 fail-closed
        task2 = self._new_task()
        alias2 = task2 + "."
        original = self._swap_find_task(
            lambda tid, *args, **kwargs: (
                None if tid == alias2 else original(tid, *args, **kwargs)))
        c2 = self.client.get(f"/api/tasks/{task2}/report-evidence",
                             headers=self.auth())
        a2 = self.client.get(f"/api/tasks/{alias2}/report-evidence",
                             headers=self.auth())
        self.assertEqual(c2.status_code, 200, c2.text)
        self.assertEqual(a2.status_code, 404, a2.text)

        # (3) POSIX 语义 + Windows 等价 lookup（叠加覆盖 alias2 的解析）：
        # 别名读恢复 200 且 can_write 与规范读对齐——can_write 规范键
        # 守护在 POSIX 上真实执行（authorize_action 回归 URL 原始 id
        # 时该阶段必红）。
        real = original

        def windows_alias_semantics(tid, *args, **kwargs):
            return real(task2 if tid == alias2 else tid, *args, **kwargs)

        self._swap_find_task(windows_alias_semantics)
        a3 = self.client.get(f"/api/tasks/{alias2}/report-evidence",
                             headers=self.auth())
        self.assertEqual(a3.status_code, 200, a3.text)
        self.assertEqual(c2.json()["can_write"], a3.json()["can_write"])
        self.assertTrue(a3.json()["can_write"], a3.text)

    def test_split_lock_key_regression_recreates_the_red_condition(self):
        """破坏即红：R6-C3 修复前端点的可观测动作就是以 URL 原始串调用
        runtime.lock（lock('T.')）。本钉子在真实 runtime 单例上重放该生产
        调用（同 MM7-02 校验器"词典损坏即红"的注入式钉子口径），验证修复
        后断言的红侧条件真实成立：别名拼写一旦入表即被 assertNotIn 命中，
        且锁表条目不随释放消失（不被请求时序稀释）。

        注：不能经由"find_task 返回 task_id=别名 的 info"来构造——该形态
        会在端点前的 require_user 属主闸（409 legacy）先死，够不到锁，
        扰动面大于本钉子的守护对象。"""
        task = self._new_task()
        alias = task + "."
        runtime = app.state.review_workflow
        runtime.lock(alias)  # pre-R6-C3 形态：端点曾按 URL 原始串取锁
        self.assertIn(alias, runtime._locks, (
            "修复前形态会把别名拼写落进锁表——test_collaboration_hardening "
            "修复后的 assertNotIn(alias, keys) 正是在该形态下变红"))


if __name__ == "__main__":
    unittest.main()
