"""r13 · W2/W4 测试卫生钉子（登记尾巴⑤）。

口径（``tailpieces_2.1.4.md`` §⑤）：
- 测试模块**导入期**需要临时数据目录来绑定导入期冻结的 ``main.DATA_DIR``，
  但必须**保存并恢复**进程环境（``REVIEWFLOW_MODE`` / ``COBOOKSHELF_DATA_DIR``），
  不得把临时目录或运行模式泄漏给同进程后续模块；
- W2/W4 都**不清理**共享数据目录（``main.DATA_DIR`` 在导入期冻结且可能被后续
  模块复用，删掉会让后续模块建任务/建用户 401/500）；
- 顺序无关由乱序/单跑证明（见交付说明），本文件提供可回归的结构钉与真实
  子进程行为钉。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
W2 = REPO / "build" / "tests" / "test_pdf_fetch_api.py"
W4 = REPO / "build" / "tests" / "test_w4_study_design.py"

_SUBPROCESS_SCRIPT = r"""
import importlib.util
import json
import os
import sys

repo = sys.argv[1]
sys.path.insert(0, repo)

SENTINEL_MODE = "r13-sentinel-mode"
SENTINEL_DIR = os.path.join(os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp",
                            "r13-sentinel-data")
os.environ["REVIEWFLOW_MODE"] = SENTINEL_MODE
os.environ["COBOOKSHELF_DATA_DIR"] = SENTINEL_DIR

result = {"sentinel": SENTINEL_DIR}
for label, rel, attr in (
        ("w2", "build/tests/test_pdf_fetch_api.py", "fixture"),
        ("w4", "build/tests/test_w4_study_design.py", "_fixture")):
    spec = importlib.util.spec_from_file_location("r13_" + label, os.path.join(repo, rel))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fixture_dir = getattr(getattr(module, attr, None), "name", None)
    module.tearDownModule()
    result[label] = {
        "mode": os.environ.get("REVIEWFLOW_MODE"),
        "dir": os.environ.get("COBOOKSHELF_DATA_DIR"),
        "fixture_dir": fixture_dir,
        "fixture_exists_after_teardown": bool(fixture_dir) and os.path.isdir(fixture_dir),
    }
print("R13_ENV_JSON:" + json.dumps(result))
"""


class TestHygieneSourceTests(unittest.TestCase):
    def test_w2_saves_restores_env_and_keeps_shared_dir(self):
        source = W2.read_text(encoding="utf-8")
        self.assertIn("_ORIGINAL_ENV", source, "导入期原值必须保存")
        self.assertIn("def tearDownModule(", source, "模块结束必须恢复环境")
        self.assertNotIn("fixture.cleanup()", source, "不得清理共享数据目录")
        self.assertIn("cls._saved_env", source, "类运行期也必须保存/恢复环境")

    def test_w4_saves_restores_env_and_keeps_shared_dir(self):
        source = W4.read_text(encoding="utf-8")
        self.assertIn("_ORIGINAL_ENV", source)
        self.assertIn("def tearDownModule(", source)
        self.assertNotIn("_fixture.cleanup()", source, "不得清理共享数据目录")
        self.assertIn("cls._saved_env", source)

    def test_import_phase_env_is_restored_in_fresh_process(self):
        """真实子进程：导入 W2/W4 后调用 tearDownModule，环境必须还原到哨兵值。

        这一步也间接证明数据目录在导入/收尾时未被删除（fixture 仍在）。
        """
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        proc = subprocess.run(
            [sys.executable, "-c", _SUBPROCESS_SCRIPT, str(REPO)],
            cwd=str(REPO), env=env, capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 0,
                         f"子进程失败：{proc.stdout}\n{proc.stderr}")
        marker = [line for line in proc.stdout.splitlines()
                  if line.startswith("R13_ENV_JSON:")]
        self.assertEqual(len(marker), 1, proc.stdout)
        result = json.loads(marker[0][len("R13_ENV_JSON:"):])
        for label in ("w2", "w4"):
            self.assertEqual(result[label]["mode"], "r13-sentinel-mode",
                             f"{label} 导入期改写的 REVIEWFLOW_MODE 未恢复")
            self.assertEqual(result[label]["dir"], result["sentinel"],
                             f"{label} 导入期改写的 COBOOKSHELF_DATA_DIR 未恢复")
            self.assertNotEqual(result[label]["dir"], result[label]["fixture_dir"],
                                f"{label} 环境仍指向自己的临时目录")
            self.assertTrue(result[label]["fixture_exists_after_teardown"],
                            f"{label} 不得在收尾时删除数据目录")


if __name__ == "__main__":
    unittest.main()
