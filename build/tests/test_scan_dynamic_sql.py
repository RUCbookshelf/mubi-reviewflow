"""scan_dynamic_sql 检查器自测（2.1.5 waveA：SQL 参数化）。

检查器本身必须先可信，才能当两条修复泳道的工单来源与回归闸门。本文件用
临时目录里的最小 fixture 验证：

* 正例（5 类形态）：f-string / ``.format`` / ``%`` / ``+`` 拼装，以及终审
  #60 增强的 ``join`` 形态（字面量可迭代对象含动态构造/已追踪别名元素）与
  函数作用域 ``+= <已追踪别名>`` 追加，作为 ``execute`` / ``executemany`` /
  ``executescript`` 第一实参时必须命中，并给出正确的 ``kind``；
* 负例：参数绑定、无插值 f-string、隐式字符串拼接、常量 ``+=`` 追加、
  纯常量 join（``'?'`` 占位符生成器 / ``'?' * n`` / 未知来源变量）都必须
  零命中；
* 间接命中：动态 SQL 先赋给局部变量再 execute 时默认命中（``direct=false``，
  带 ``via``），``--direct-only`` 关闭；
* 白名单（``文件:行号#理由``）压制命中、退出码 0，失效条目只告警；
* JSON 报告结构、退出码（0/1/2）、``tests`` 类目录排除；
* 真实树闸门（终审 #59）：以**生产白名单**运行必须 exit 0、未白名单 0、
  失效条目 0；白名单条目集被钉死（新增必须显式更新本文件），且工作区中
  白名单文件不得有未提交改动。真实树全量扫描经会话级缓存只跑一次。

fixture 源码由 ``_call_source`` / ``_assign_then_call`` 等模板在运行时拼出：
检查器只关心 AST 形态（不读 SQL 语义），测试文件本身不内联「动态实参 +
execute」的完整字面量，避免本文件自己被安全扫描误判。
运行：``python -m pytest build/tests/test_scan_dynamic_sql.py -q``。
"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "build" / "scripts" / "scan_dynamic_sql.py"
WHITELIST = ROOT / "build" / "scripts" / "scan_dynamic_sql.whitelist"

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

#: join 形态片段（终审 #60）。SEP 拼出 ``''.join``；f-string 片段的拼法沿用
#: 上方 ``ARG_FSTRING`` 的粒度（``f`` 单独成段），本文件不内联完整的
#: 「动态实参 + execute」字面量。
_JOIN_SEP = "".join(["'", "'", ".", "jo", "in"])
_JOIN_FSTRING = "".join(["f", '"WHERE a={', "key}", '"'])
_JOIN_DYNAMIC_ARGS = '("SELECT * FROM t ", ' + _JOIN_FSTRING + ")"
_JOIN_ALIAS_HEADER = "def demo(conn, key, year):\n"
_JOIN_ALIAS_LINE = "    part = PARTSPEC\n"
_JOIN_DIRECT_LINE = "    conn.CALLNAME(SEP(JOINARGS))\n"
_JOIN_ASSIGN_LINE = "    VARNAME = SEP(JOINARGS)\n"
_JOIN_CALLVAR_LINE = "    conn.CALLNAME(VARNAME)\n"
_JOIN_BASE_LINE = '    BASENAME = "SELECT * FROM t "\n'
_JOIN_AUG_LINE = "    BASENAME += PARTNAME\n"


def _call_source(arg: str, call: str = "execute") -> str:
    """生成「一行函数 + 单次调用」的 fixture 源码（调用在第 2 行）。"""
    body = _CALL_LINE.replace("CALLNAME", call).replace("ARGEXPR", arg)
    return _FUNC_HEADER + body


def _assign_then_call(arg: str, call: str = "execute") -> str:
    """生成「变量赋值 + 调用变量」的 fixture 源码（赋值第 2 行，调用第 3 行）。"""
    body = _ASSIGN_LINE.replace("ARGEXPR", arg) + _CALL_VAR_LINE.replace("CALLNAME", call)
    return _FUNC_HEADER + body


def _join_direct_source(call: str = "execute") -> str:
    """形态1（join 直塞）：字面量元组 join 的元素含 f-string 构造。"""
    body = (_JOIN_DIRECT_LINE.replace("CALLNAME", call).replace("SEP", _JOIN_SEP)
            .replace("JOINARGS", _JOIN_DYNAMIC_ARGS))
    return _JOIN_ALIAS_HEADER + body


def _join_assign_source() -> str:
    """形态2（join 间接）：join 结果先赋值给变量再进第一实参。"""
    body = (_JOIN_ASSIGN_LINE.replace("VARNAME", "sql").replace("SEP", _JOIN_SEP)
            .replace("JOINARGS", _JOIN_DYNAMIC_ARGS)
            + _JOIN_CALLVAR_LINE.replace("CALLNAME", "execute").replace("VARNAME", "sql"))
    return _JOIN_ALIAS_HEADER + body


def _join_alias_element_source() -> str:
    """形态3（join 别名元素）：字面量元组 join 的元素是已追踪 f-string 别名。"""
    body = (_JOIN_ALIAS_LINE.replace("PARTNAME", "part").replace("PARTSPEC", _JOIN_FSTRING)
            + _JOIN_DIRECT_LINE.replace("CALLNAME", "execute").replace("SEP", _JOIN_SEP)
            .replace("JOINARGS", '("SELECT * FROM t ", part)'))
    return _JOIN_ALIAS_HEADER + body


def _aug_assign_alias_source() -> str:
    """形态4（+= 别名追加）：函数作用域内常量基串追加已追踪别名后进第一实参。"""
    body = (_JOIN_ALIAS_LINE.replace("PARTNAME", "part").replace("PARTSPEC", _JOIN_FSTRING)
            + _JOIN_BASE_LINE.replace("BASENAME", "q")
            + _JOIN_AUG_LINE.replace("BASENAME", "q").replace("PARTNAME", "part")
            + _JOIN_CALLVAR_LINE.replace("CALLNAME", "execute").replace("VARNAME", "q"))
    return _JOIN_ALIAS_HEADER + body


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

#: 生产白名单当前登记的全部条目（文件:行号，posix 相对仓库根）。终审 #59：
#: 任何新增/漂移都必须显式更新这里并走评审——「exit 0 + 白名单不新增」自此
#: 成为仓库级永久闸门（条目内容见 build/scripts/scan_dynamic_sql.whitelist）。
EXPECTED_WHITELIST_KEYS = frozenset({
    ("custom_backend/import_review_actions.py", 163),
    ("custom_backend/research_trace_api.py", 324),
    ("custom_backend/research_trace_api.py", 360),
    ("custom_backend/research_trace_api.py", 382),
    # 2026-11-13 随正名（r67 波 + 四线汇合）：M-04 注册表 DDL 平移 749→751、
    # 1344→1345 并新增 db.py:557；M-03 独立性守卫新增 analysis_storage.py:517
    # ——白名单文件已同批登记（9 条），本钉集随正。
    ("coscreen/db.py", 557),
    ("coscreen/db.py", 751),
    ("coscreen/analysis_storage.py", 517),
    ("coscreen/db.py", 1345),
    ("coscreen/review_workflow.py", 158),
})

#: 会话级缓存：真实树全量扫描只跑一次（闸门运行被多个用例复用）。
_REAL_TREE_LOCK = threading.Lock()
_REAL_TREE_CACHE: dict[str, tuple[int, dict, str]] = {}


def _real_tree_gate() -> tuple[int, dict, str]:
    """以生产白名单对真实树跑一次检查器（缓存），返回 (exit, report, err)。"""
    with _REAL_TREE_LOCK:
        if "gate" not in _REAL_TREE_CACHE:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = SCANNER.main(["--json", "--whitelist", str(WHITELIST)])
            _REAL_TREE_CACHE["gate"] = (code, json.loads(out.getvalue()), err.getvalue())
        return _REAL_TREE_CACHE["gate"]


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

    def test_constant_join_shapes_are_clean(self) -> None:
        """终审 #60 负例：树内真实惯用形（常量 join）必须零命中——增强不得
        误伤（当前树 23 处 join 站点全为此类，白名单因此零新增）。

        等价于树内形态：``marks = <逗号join>('?' for _ in ids)``、
        常量元素 join 含 ``'?' * 3`` 重复、join 结果赋值再进第一实参。"""
        comma_join = "".join(["','", ".", "jo", "in"])
        source = (
            "def load(conn, ids):\n"
            "    marks = " + comma_join + "('?' for _ in ids)\n"
            "    value = " + _JOIN_SEP + "((\"SELECT x FROM t WHERE k IN (\", marks, \")\"))\n"
            "    conn.CALLNAME(value)\n"
            "    conn.CALLNAME(" + _JOIN_SEP + "((\"SELECT \", \"?\" * 3, \" FROM t\")))\n"
        ).replace("CALLNAME", "execute")
        self.fixture.write("coscreen/sample.py", source)
        code, report, _ = self.fixture.scan_json()
        self.assertEqual(code, SCANNER.EXIT_OK)
        self.assertEqual(report["counts"]["total"], 0, report["hits"])

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


class ScannerJoinShapeTest(unittest.TestCase):
    """终审 #60 正例：与拼接语义等价的 join / += 别名形态必须被检出。"""

    def setUp(self) -> None:
        self.fixture = Fixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def test_join_with_dynamic_element_is_hit(self) -> None:
        self.fixture.write("coscreen/sample.py", _join_direct_source())
        code, report, _ = self.fixture.scan_json()
        self.assertEqual(code, SCANNER.EXIT_FINDINGS)
        self.assertEqual(report["counts"]["total"], 1)
        hit = report["hits"][0]
        self.assertEqual(hit["kind"], "join")
        self.assertEqual(hit["call"], "execute")
        self.assertTrue(hit["direct"])

    def test_join_assignment_then_execute_is_hit(self) -> None:
        self.fixture.write("custom_backend/sample.py", _join_assign_source())
        code, report, _ = self.fixture.scan_json()
        self.assertEqual(code, SCANNER.EXIT_FINDINGS)
        self.assertEqual(report["counts"]["total"], 1)
        hit = report["hits"][0]
        self.assertEqual(hit["kind"], "join")
        self.assertFalse(hit["direct"])
        self.assertEqual(hit["via"], {"name": "sql", "line": 2})

    def test_join_with_alias_element_is_hit(self) -> None:
        self.fixture.write("coscreen/sample.py", _join_alias_element_source())
        code, report, _ = self.fixture.scan_json()
        self.assertEqual(code, SCANNER.EXIT_FINDINGS)
        self.assertEqual(report["counts"]["total"], 1)
        hit = report["hits"][0]
        self.assertEqual(hit["kind"], "join")
        self.assertTrue(hit["direct"])

    def test_aug_assign_from_alias_is_hit(self) -> None:
        self.fixture.write("coscreen/sample.py", _aug_assign_alias_source())
        code, report, _ = self.fixture.scan_json()
        self.assertEqual(code, SCANNER.EXIT_FINDINGS)
        self.assertEqual(report["counts"]["total"], 1)
        hit = report["hits"][0]
        self.assertFalse(hit["direct"])
        self.assertEqual(hit["via"]["name"], "q")

    def test_mutation_join_detection_removed_pin_goes_red(self) -> None:
        """破坏性变异：还原「四类形态版」dynamic_kind（撤掉 join 检测）→
        join 直塞形态漏报——上一组 join 钉必红（检测是承重墙）。"""

        def four_kind_only(node):  # noqa: ANN001 - ast.AST
            if isinstance(node, ast.JoinedStr) and any(
                    isinstance(value, ast.FormattedValue) for value in node.values):
                return "fstring"
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "format"):
                return "format"
            if isinstance(node, ast.BinOp):
                if isinstance(node.op, ast.Mod):
                    return "percent"
                if isinstance(node.op, ast.Add):
                    return "concat"
            return None

        self.fixture.write("coscreen/sample.py", _join_direct_source())
        with mock.patch.object(SCANNER, "dynamic_kind", four_kind_only):
            code, report, _ = self.fixture.scan_json()
        self.assertEqual(report["counts"]["total"], 0,
                         "变异后必须漏报（证明 join 钉承重；基线行为即如此）")

    def test_mutation_aug_tracking_removed_pin_goes_red(self) -> None:
        """破坏性变异：``+=`` 追踪整体撤掉 → 别名追加形态漏报——上一颗
        += 钉必红。"""
        self.fixture.write("coscreen/sample.py", _aug_assign_alias_source())
        with mock.patch.object(SCANNER._ScopeVisitor, "visit_AugAssign",
                               lambda self, node: None):
            code, report, _ = self.fixture.scan_json()
        self.assertEqual(report["counts"]["total"], 0,
                         "变异后必须漏报（证明 += 别名钉承重）")


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
    """真实树闸门（终审 #59）：exit 0 + 未白名单 0 + 白名单不新增。

    修复前唯一真实树用例不带 ``--whitelist`` 且显式接受 ``EXIT_FINDINGS``，
    「scanner exit 0」的封版结论只存在于人工运行中；任何未登记的动态拼装
    SQL 或白名单行号漂移都不会让测试变红。本类把闸门钉死：以**生产白名单**
    运行必须 exit 0、未白名单 0、失效条目 0；白名单条目集（7 条）被钉在
    ``EXPECTED_WHITELIST_KEYS``，新增/漂移必须显式更新本文件并走评审；
    工作区中白名单文件不得有未提交改动。全树扫描经会话级缓存只跑一次。
    """

    def test_real_tree_parses_without_errors(self) -> None:
        _, report, err = _real_tree_gate()
        self.assertEqual(report["errors"], [])
        self.assertGreaterEqual(report["scanned_files"], 1)
        self.assertEqual(err, "")

    def test_real_tree_gate_exit_zero_with_production_whitelist(self) -> None:
        code, report, err = _real_tree_gate()
        self.assertEqual(code, SCANNER.EXIT_OK,
                         "真实树必须以生产白名单过闸（exit 0）；新增动态拼装"
                         " SQL 必须修复或走白名单评审流程")
        counts = report["counts"]
        self.assertEqual(counts["unwhitelisted"], 0, counts)
        self.assertGreater(counts["whitelisted"], 0)
        self.assertEqual(report["whitelist"]["stale"], [],
                         "白名单失效条目（行号漂移/已修复）必须清理")
        self.assertEqual(err, "")

    def test_production_whitelist_entry_set_is_pinned(self) -> None:
        entries, wl_errors = SCANNER.load_whitelist(WHITELIST, ROOT)
        self.assertEqual(wl_errors, [])
        self.assertEqual(frozenset(entries), EXPECTED_WHITELIST_KEYS,
                         "白名单条目集变化必须显式更新 EXPECTED_WHITELIST_KEYS"
                         "（登记须附理由并走评审）")

    def test_production_whitelist_has_no_uncommitted_edits(self) -> None:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "status", "--porcelain", "--",
             "build/scripts/scan_dynamic_sql.whitelist"],
            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0,
                         "git 不可用或不在仓库内，无法核对白名单未改动")
        self.assertEqual(result.stdout.strip(), "",
                         "白名单文件不得有未提交改动（登记必须显式提交并同步"
                         "更新 EXPECTED_WHITELIST_KEYS）")

    def test_mutation_whitelist_ignored_pin_goes_red(self) -> None:
        """破坏性变异：白名单被清空（等效绕过登记）→ 真实树 7 处已登记命中
        全部变成未白名单、exit 1——闸门钉必红。"""
        with mock.patch.object(SCANNER, "load_whitelist",
                               lambda path, root: ({}, [])):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = SCANNER.main(["--json", "--whitelist", str(WHITELIST)])
        report = json.loads(out.getvalue())
        self.assertEqual(code, SCANNER.EXIT_FINDINGS)
        self.assertEqual(report["counts"]["unwhitelisted"],
                         len(EXPECTED_WHITELIST_KEYS))


if __name__ == "__main__":
    unittest.main()
