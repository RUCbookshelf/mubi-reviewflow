"""r7-lunafix L-02 钉子的 pytest 接线（node 行为级验证）。

真正的钉子在 ``import_workspace_activation.test.cjs``：真实
import_review_loading.js / import_review_updates.js + 从
unified_import_workspace.js 切出的真实 I.open/activate，验证「启用失败后
后台分页继续到 complete；对照组不激活照常 complete」。修前（activate 用
++ticket 烧毁加载器代次）该 harness 以非零退出（红）。
"""
import pathlib
import shutil
import subprocess
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
HARNESS = pathlib.Path(__file__).resolve().parent / "import_workspace_activation.test.cjs"

NODE_CANDIDATES = (
    r"C:\Users\16073\AppData\Local\hermes\node\node.exe",
)


def _node_exe():
    for exe in (*NODE_CANDIDATES, shutil.which("node")):
        if exe and pathlib.Path(exe).is_file():
            return str(exe)
    return None


class LunaImportActivationLoaderTests(unittest.TestCase):

    def test_l02_activation_failure_does_not_stall_background_paging(self):
        exe = _node_exe()
        if exe is None:
            raise unittest.SkipTest("本机无 node 可执行文件（L-02 行为级钉子不可运行）")
        proc = subprocess.run(
            [exe, str(HARNESS)], cwd=str(REPO), capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=90)
        self.assertEqual(
            proc.returncode, 0,
            "L-02 钉子失败（启用失败后后台分页未恢复到 complete）：\n"
            + (proc.stdout + proc.stderr)[-3000:])
        self.assertIn("activation failure no longer stalls background paging",
                      proc.stdout)


if __name__ == "__main__":
    unittest.main()
