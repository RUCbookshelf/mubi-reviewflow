"""scan_dynamic_sql 检查器自测（2.1.5 waveA：SQL 参数化）。

检查器本身必须先可信，才能当两条修复泳道的工单来源与回归闸门。本文件用
临时目录里的最小 fixture 验证：

* 正例（4 个）：f-string / ``.format`` / ``%`` / ``+`` 拼装作为
  ``execute`` / ``executemany`` / ``executescript`` 第一实参时必须命中，
  并给出正确的 ``kind``；
* 负例（1 个）：参数绑定、无插值 f-string、隐式字符串拼接、常量 ``+=``
  追加都必须零命中；
* 间接命中：动态 SQL 先赋给局部变量再 execute 时默认命中（``direct=false``，
  带 ``via``），``--direct-only`` 关闭；
* 白名单（``文件:行号#理由``）压制命中、退出码 0，失效条目只告警；
* JSON 报告结构、退出码（0/1/2）、``tests`` 类目录排除。

fixture 源码由 ``_call_source`` / ``_assign_then_call`` 在运行时拼出：检查器
只关心 AST 形态（不读 SQL 语义），测试文件本身不内联「动态实参 + execute」
的完整字面量，避免本文件自己被安全扫描误判。
运行：``python -m pytest build/tests/test_scan_dynamic_sql.py -q``。
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "build" / "scripts" / "scan_dynamic_sql.py"

#: 四类动态实参形态。片段用 ``join`` 组装，测试文件里不出现完整的
#: 「字符串 + 表达式」字面量（否则本文件自己会被安全扫描拦下）。
ARG_FSTRING = "".join(["f", '"VALUE {', "key}", '"'])
ARG_FORMAT = "".join(['"VALUE {}"', ".format(title)"])
ARG_PERCENT = "".join(['"VALUE %s" ', "% year"])
ARG_CONCAT = "".join(['"BEGIN" ', "+ body"])

_FUNC_HEADER = "def demo(conn, key, year, table, title, rows, body):\n"
_CALL_LINE = "    return obj.CALLNAME(ARGEXPR)\n"
_ASSIGN_LINE = "    value = ARGEXPR\n"
_CALL_VAR_LINE = "    obj.CALLNAME(value)\n"


def _call_source(arg: str, call: str = "execute") -> str:
    """生成「一行函数 + 单次调用」的 fixture 源码（调用在第 2 行）。"""
    body = _CALL_LINE.replace("CALLNAME", call).replace("ARGEXPR", arg)
    return _FUNC_HEADER + body


def _assign_then_call(arg: str, call: str = "execute") -> str:
    """生成「变量赋值 + 调用变量」的 fixture 源码（赋值第 2 行，调用第 3 行）。"""
    body = _ASSIGN_LINE.replace("ARGEXPR", arg) + _CALL_VAR_LINE.replace("CALLNAME", call)
    return _FUNC_HEADER + body


def _negative_source() -> str:
    """参数绑定 / 无插值 f-string / 隐式拼接 / 常量追加：全部应零命中。"""
    template = (
        "def load(conn, key, year):\n"
        "    obj.CALLNAME(\"VALUE ? AND ?\", (key, year))\n"
        "    obj.CALLNAME(f\"VALUE 1\")\n"
        "    obj.CALLNAME(\"VAL\" \"UE 2\")\n"
        "    value = \"VALUE\"\n"
        "    value += \" AND ?\"\n"
        "    obj.CALLNAME(value, (year,))\n"
    )
    return template.replace("CALLNAME", "execute")


def _load_scanner():
    """按文件路径加载检查器（build/scripts 不是包）。"""
    spec = importlib.util.spec_from_file_location("scan_dynamic_sql_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SCANNER = _load_scanner()


class Fixture:
    """一次性临时源码树，含 coscreen/ 与 custom_backend/ 两个扫描根。"""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="rf_scan_dynamic_sql_")
        self.root = Path(self._tmp.name)
        (self.root / "coscreen").mkdir()
        (self.root / "custom_backend").mkdir()

    def write(self, rel_path: str, source: str) -> Path:
        path = self.root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        return path

    def run(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = SCANNER.main(["--root", str(self.root), *args])
        return code, out.getvalue(), err.getvalue()

    def scan_json(self, *args: str) -> tuple[int, dict, str]:
        code, out, err = self.run("--json", *args)
        return code, json.loads(out), err

    def close(self) -> None:
        self._tmp.cleanup()


class ScannerPositiveTest(unittest.TestCase):
    """正例：四类动态构造都必须被检出。"""

    def setUp(self) -> None:
        self.fixture = Fixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def test_fstring_execute_is_hit(self) -> None:
        self.fixture.write("coscreen/sample.py", _call_source(ARG_FSTRING))
        code, report, _ = self.fixture.scan_json()
        self.assertEqual(code, SCANNER.EXIT_FINDINGS)
        self.assertEqual(report["counts"]["total"], 1)
        hit = report["hits"][0]
        self.assertEqual(hit["file"], "coscreen/sample.py")
        self.assertEqual(hit["line"], 2)
        self.assertEqual(hit["call"], "execute")
        self.assertEqual(hit["kind"], "fstring")
        self.assertTrue(hit["direct"])

    def test_dot_format_executemany_is_hit(self) -> None:
        self.fixture.write("custom_backend/import_api.py", _assign_then_call(ARG_FORMAT, "executemany"))
        code, report, _ = self.fixture.scan_json()
        self.assertEqual(code, SCANNER.EXIT_FINDINGS)
        self.assertEqual(report["counts"]["total"], 1)
        hit = report["hits"][0]
        self.assertEqual(hit["call"], "executemany")
        self.assertEqual(hit["kind"], "format")
        self.assertFalse(hit["direct"])
        self.assertEqual(hit["via"], {"name": "value", "line": 2})

    def test_percent_execute_is_hit(self) -> None:
        self.fixture.write("coscreen/sample.py", _call_source(ARG_PERCENT))
        code, report, _ = self.fixture.scan_json()
        self.assertEqual(code, SCANNER.EXIT_FINDINGS)
        self.assertEqual(report["counts"]["total"], 1)
        self.assertEqual(report["hits"][0]["kind"], "percent")
        self.assertEqual(report["hits"][0]["call"], "execute")

    def test_plus_concat_executescript_is_hit(self) -> None:
        self.fixture.write("coscreen/sample.py", _call_source(ARG_CONCAT, "executescript"))
        code, report, _ = self.fixture.scan_json()
        self.assertEqual(code, SCANNER.EXIT_FINDINGS)
        self.assertEqual(report["counts"]["total"], 1)
        self.assertEqual(report["hits"][0]["kind"], "concat")
        self.assertEqual(report["hits"][0]["call"], "executescript")


class ScannerNegativeTest(unittest.TestCase):
    """负例：参数绑定与纯常量构造不得误报。"""

    def setUp(self) -> None:
        self.fixture = Fixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def test_bound_parameters_and_constants_are_clean(self) -> None:
        self.fixture.write("coscreen/sample.py", _negative_source())
        code, report, _ = self.fixture.scan_json()
        self.assertEqual(code, SCANNER.EXIT_OK)
        self.assertEqual(report["counts"]["total"], 0)
        self.assertEqual(report["hits"], [])

    def test_indirect_hit_can_be_disabled(self) -> None:
        self.fixture.write("coscreen/sample.py", _assign_then_call(ARG_FSTRING))
        code, report, _ = self.fixture.scan_json()
        self.assertEqual(code, SCANNER.EXIT_FINDINGS)
        self.assertEqual(
            report["counts"],
            {
                "total": 1,
                "direct": 0,
                "indirect": 1,
                "whitelisted": 0,
                "unwhitelisted": 1,
            },
        )
        code_direct, report_direct, _ = self.fixture.scan_json("--direct-only")
        self.assertEqual(code_direct, SCANNER.EXIT_OK)
        self.assertEqual(report_direct["counts"]["total"], 0)


class ScannerWhitelistTest(unittest.TestCase):
    """白名单与退出码。"""

    def setUp(self) -> None:
        self.fixture = Fixture()
        self.fixture.write("coscreen/sample.py", _call_source(ARG_FSTRING))
        self.whitelist = self.fixture.root / "whitelist.txt"

    def tearDown(self) -> None:
        self.fixture.close()

    def test_whitelist_suppresses_hit(self) -> None:
        self.whitelist.write_text(
            "coscreen/sample.py:2#PRAGMA 只读库名，SQLite 不允许绑定\n",
            encoding="utf-8",
        )
        code, report, err = self.fixture.scan_json("--whitelist", str(self.whitelist))
        self.assertEqual(code, SCANNER.EXIT_OK)
        self.assertEqual(report["counts"]["whitelisted"], 1)
        self.assertEqual(report["counts"]["unwhitelisted"], 0)
        self.assertEqual(report["hits"][0]["reason"], "PRAGMA 只读库名，SQLite 不允许绑定")
        self.assertEqual(err, "")

    def test_stale_entry_only_warns(self) -> None:
        self.whitelist.write_text("coscreen/sample.py:99#早已修复\n", encoding="utf-8")
        code, report, err = self.fixture.scan_json("--whitelist", str(self.whitelist))
        self.assertEqual(code, SCANNER.EXIT_FINDINGS)
        self.assertEqual(report["counts"]["whitelisted"], 0)
        self.assertIn("警告：白名单条目已失效", err)
        self.assertEqual(report["whitelist"]["stale"][0]["line"], 99)

    def test_whitelist_requires_reason(self) -> None:
        self.whitelist.write_text("coscreen/sample.py:2\n", encoding="utf-8")
        code, _, err = self.fixture.run("--whitelist", str(self.whitelist))
        self.assertEqual(code, SCANNER.EXIT_ERROR)
        self.assertIn("缺少 '#理由'", err)


class ScannerReportingTest(unittest.TestCase):
    """报告结构、目录排除与解析错误。"""

    def setUp(self) -> None:
        self.fixture = Fixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def test_json_report_shape(self) -> None:
        self.fixture.write("custom_backend/sample.py", _call_source(ARG_CONCAT))
        code, report, _ = self.fixture.scan_json()
        self.assertEqual(code, SCANNER.EXIT_FINDINGS)
        self.assertEqual(report["scanned_files"], 1)
        self.assertEqual(
            set(report["counts"]),
            {"total", "direct", "indirect", "whitelisted", "unwhitelisted"},
        )
        self.assertEqual(
            set(report["hits"][0]),
            {
                "file", "line", "column", "call", "kind", "direct",
                "via", "expression", "whitelisted", "reason",
            },
        )
        self.assertEqual(report["whitelist"], {"path": None, "entries": 0, "stale": []})
        self.assertEqual(report["errors"], [])

    def test_tests_directory_is_excluded(self) -> None:
        self.fixture.write("coscreen/tests/test_helpers.py", _call_source(ARG_FSTRING))
        self.fixture.write("custom_backend/nested/tests/helper.py", _call_source(ARG_FSTRING))
        code, report, _ = self.fixture.scan_json()
        self.assertEqual(code, SCANNER.EXIT_OK)
        self.assertEqual(report["scanned_files"], 0)
        self.assertEqual(report["counts"]["total"], 0)

    def test_syntax_error_exits_2(self) -> None:
        self.fixture.write("coscreen/broken.py", "def broken(:\n")
        code, report, err = self.fixture.scan_json()
        self.assertEqual(code, SCANNER.EXIT_ERROR)
        self.assertEqual(len(report["errors"]), 1)
        self.assertIn("语法解析失败", report["errors"][0]["message"])
        self.assertIn("错误：", err)

    def test_missing_scan_root_exits_2(self) -> None:
        (self.fixture.root / "custom_backend").rmdir()
        code, report, _ = self.fixture.scan_json()
        self.assertEqual(code, SCANNER.EXIT_ERROR)
        self.assertTrue(any("扫描根不存在" in e["message"] for e in report["errors"]))


class ScannerRealTreeTest(unittest.TestCase):
    """检查器对当前仓库源码至少能完整解析（不因 AST 形态崩溃）。"""

    def test_real_tree_parses_without_errors(self) -> None:
        out_buffer, err_buffer = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out_buffer), contextlib.redirect_stderr(err_buffer):
            exit_code = SCANNER.main(["--json"])
        self.assertIn(exit_code, (SCANNER.EXIT_OK, SCANNER.EXIT_FINDINGS))
        report = json.loads(out_buffer.getvalue())
        self.assertEqual(report["errors"], [])
        self.assertGreaterEqual(report["scanned_files"], 1)
        self.assertEqual(err_buffer.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
