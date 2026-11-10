"""Mimosa 安全约束检查器：定位「动态拼装 SQL」的 execute 调用点。

背景
----
Mimosa 生成前钩子（2026-11-07）要求：数据库查询时所有外部输入必须使用
参数绑定；不得用拼接、format 或 f-string 组装 SQL。2.1.5 waveA 的任务是
逐处把 SQL 构造改成「常量字面量 + 绑定参数」，且对任意输入产生的 SQL
文本必须逐字节不变。本脚本只做**静态定位**（不修改任何代码），作为两条
修复泳道的工单来源与回归闸门。

判定规则
--------
扫描 ``coscreen/`` 与 ``custom_backend/`` 下的 .py（不进入 ``build/``，
因此 ``build/tests`` 天然排除；另跳过 ``tests``、``__pycache__`` 等目录）。
对 ``execute`` / ``executemany`` / ``executescript``（方法调用或裸函数调用）
的**第一实参**，以下四类记为命中：

* ``fstring``  —— ``ast.JoinedStr`` 且至少含一个 ``FormattedValue``
  （无插值的 f-string 仍是纯常量，不记）；
* ``format``   —— ``ast.Call`` 且 func 为 ``.format`` 属性；
* ``percent``  —— ``ast.BinOp(op=Mod)``；
* ``concat``   —— ``ast.BinOp(op=Add)``；
* ``join``     —— ``''.join((…))`` 之类 ``str.join``，且可迭代对象是
  **字面量**容器（tuple/list/set）或推导式/生成器表达式，其中至少一个
  元素是上述动态构造（含嵌套动态 join），或（作用域内）是已追踪的动态
  别名变量。纯常量元素不记：``','.join('?' for _ in ids)`` 占位符生成器、
  ``'?' * n`` 重复、以及 join 一个未知来源的裸变量（``join(parts)``，
  即使该变量由动态元素构成）都与既有「不做跨函数数据流」口径一致地放行。

此外（默认开启，``--direct-only`` 可关闭）做**函数内简单别名追踪**：
若第一实参是局部/外层/模块级变量，而该变量经赋值或 ``+=`` 来自上述动态
表达式（含函数作用域内 ``+= <已追踪别名>`` 的追加传播），则以 ``direct=false`` 记一条
间接命中（``via`` 带变量名与赋值行号）。这类写法与直接拼装等价，必须一
并修复，否则工单不完整；追踪只在语法作用域内做，不做跨函数参数的数据流
分析（见「已知局限」）。

纯常量片段（含隐式字符串拼接 ``"a" "b"``、常量 ``+=`` 追加）不记命中。

白名单
------
``--whitelist FILE`` 每行一条 ``文件:行号#理由``；``#`` 之后为必填理由
（缺失会告警）。文件路径相对仓库根，允许 ``/`` 或 ``\\`` 分隔及绝对路径。
SQLite 语法确实无法绑定处（如 ``PRAGMA``、``ATTACH`` 的只读库名）应在
修复时改为「白名单/校验后的字面量」并登记于此。已登记但当前无命中的条目
以 ``stale`` 告警输出（不改变退出码）。

输出与退出码
------------
* 默认文本：每条命中一行 ``文件:行:列: 类型 调用 :: 表达式片段``，末行汇总；
* ``--json``：stdout 输出单个 JSON 对象（含 counts/hits/whitelist/errors），
  文本汇总改走 stderr；
* 退出码：``0`` = 无未白名单命中；``1`` = 存在未白名单命中；
  ``2`` = 用法/解析错误（无法给出可信结论）。等价于「非零即未过闸」。

用法::

    python build/scripts/scan_dynamic_sql.py
    python build/scripts/scan_dynamic_sql.py --json > hits.json
    python build/scripts/scan_dynamic_sql.py --whitelist build/scripts/scan_dynamic_sql.whitelist
    python build/scripts/scan_dynamic_sql.py --root /tmp/fixture --json
    python build/scripts/scan_dynamic_sql.py --direct-only

已知局限
--------
* 不做跨函数数据流：``build_sql(...)`` 之类 helper 返回的动态 SQL、或经
  函数参数传入再 execute 的 SQL 不会被识别；
* ``join`` 只检**字面量**可迭代对象：``''.join(parts)`` 的 ``parts`` 是
  变量时不记（即使该变量由动态元素构成）；``str(...)`` /
  ``textwrap.dedent(...)`` 等任意非 execute 族名 Call 包装同样不记。
  当前树中 23 处 ``join`` 站点的元素均为常量子句或 ``'?'`` 占位符生成器，
  语义上是常量拼接（终审 #60 复核），因此零命中；把已白名单化的拼接改写
  成「字面量元素含动态构造」的 join 会被本检查器抓住；
* 模块顶层的 ``+=`` 常量组合（db.py 的 ``_SCHEMA`` 系列 schema 惯用形）不
  做别名传播，只检函数作用域内的 ``q += <已追踪别名>``。

本模块导入无副作用，测试可直接 import 复用 ``scan_tree`` / ``main``。
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

#: 仓库根（build/scripts/ 上两级）。测试可用 --root 覆盖。
ROOT = Path(__file__).resolve().parents[2]

#: 默认扫描的源码目录（相对仓库根）。
DEFAULT_SCAN_DIRS = ("coscreen", "custom_backend")

#: 目标调用名（方法调用取属性名，裸调用取标识符）。
TARGET_CALLS = frozenset({"execute", "executemany", "executescript"})

#: 递归扫描时跳过的目录名（大小写不敏感）。build/tests 不在扫描根内，
#: 这里再兜底排除 roots 内部可能存在的测试/缓存目录。
EXCLUDED_DIR_NAMES = frozenset(
    {"__pycache__", "tests", "test", ".git", "node_modules", ".venv", "venv", "build", "dist"}
)

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2

_MAX_EXPR_CHARS = 240


@dataclass(frozen=True)
class Alias:
    """某个变量名当前已知的动态 SQL 构造方式与来源行。"""

    kind: str
    line: int


@dataclass
class Hit:
    """一条动态 SQL 命中。"""

    file: str
    line: int
    column: int
    call: str
    kind: str
    direct: bool
    expression: str
    via: Alias | None = None
    via_name: str | None = None
    whitelisted: bool = False
    reason: str | None = None

    @property
    def key(self) -> tuple[str, int]:
        return (self.file, self.line)

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "call": self.call,
            "kind": self.kind,
            "direct": self.direct,
            "via": None if self.via is None else {"name": self.via_name, "line": self.via.line},
            "expression": self.expression,
            "whitelisted": self.whitelisted,
            "reason": self.reason,
        }


@dataclass
class ScanError:
    file: str
    line: int | None
    message: str

    def to_dict(self) -> dict:
        return {"file": self.file, "line": self.line, "message": self.message}


def _iterable_elements(node: ast.AST) -> list[ast.AST] | None:
    """join 可迭代对象的元素表达式；非字面量容器/推导式返回 None。"""
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return list(node.elts)
    if isinstance(node, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
        return [node.elt]
    return None


def _join_kind(call: ast.AST) -> str | None:
    """``str.join`` 形态判定（终审 #60）：字面量元素含动态构造才记 ``join``。

    纯常量元素不记：``','.join('?' for _ in ids)``（占位符生成器）、
    ``'?' * n``（BinOp(Mult)）、join 一个未知来源的裸变量——与「不做跨
    函数数据流」的既有口径一致。
    """
    if not (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "join"
        and len(call.args) == 1
    ):
        return None
    elements = _iterable_elements(call.args[0])
    if elements is None:
        return None
    for element in elements:
        if isinstance(element, ast.Starred):
            element = element.value
        if dynamic_kind(element) is not None:
            return "join"
    return None


def dynamic_kind(node: ast.AST) -> str | None:
    """返回动态 SQL 构造类别；纯常量返回 None。"""
    if isinstance(node, ast.JoinedStr) and any(
        isinstance(value, ast.FormattedValue) for value in node.values
    ):
        return "fstring"
    if isinstance(node, ast.Call):
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "format"
        ):
            return "format"
        kind = _join_kind(node)
        if kind is not None:
            return kind
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Mod):
            return "percent"
        if isinstance(node.op, ast.Add):
            return "concat"
    return None


def _call_name(func: ast.AST) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _snippet(source: str, node: ast.AST) -> str:
    try:
        text = ast.get_source_segment(source, node)
    except Exception:  # pragma: no cover - 极端 AST 畸形时退回 unparse
        text = None
    if text is None:
        try:
            text = ast.unparse(node)
        except Exception:  # pragma: no cover
            text = "<unavailable>"
    text = " ".join(text.split())
    if len(text) > _MAX_EXPR_CHARS:
        text = text[: _MAX_EXPR_CHARS - 3] + "..."
    return text


class _ScopeVisitor(ast.NodeVisitor):
    """按语法作用域收集动态变量别名，并在 execute 调用点判定命中。"""

    def __init__(self, rel_path: str, source: str, *, resolve_locals: bool) -> None:
        self.rel_path = rel_path
        self.source = source
        self.resolve_locals = resolve_locals
        self.hits: list[Hit] = []
        self._scopes: list[dict[str, Alias]] = [{}]

    # ---- 作用域 -------------------------------------------------------
    def _push(self) -> None:
        self._scopes.append({})

    def _pop(self) -> None:
        self._scopes.pop()

    def _lookup(self, name: str) -> Alias | None:
        for scope in reversed(self._scopes):
            alias = scope.get(name)
            if alias is not None:
                return alias
        return None

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        # 装饰器/默认值在外层作用域求值，先按外层访问；函数体独立作用域。
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in list(node.args.defaults) + [d for d in node.args.kw_defaults if d]:
            self.visit(default)
        self._push()
        try:
            for stmt in node.body:
                self.visit(stmt)
        finally:
            self._pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        self._push()
        try:
            for default in list(node.args.defaults) + [d for d in node.args.kw_defaults if d]:
                self.visit(default)
            self.visit(node.body)
        finally:
            self._pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        self._push()
        try:
            for stmt in node.body:
                self.visit(stmt)
        finally:
            self._pop()

    # ---- 赋值追踪 -----------------------------------------------------
    def _scoped_kind(self, node: ast.AST) -> str | None:
        """``dynamic_kind`` + 作用域内可见的 join 别名元素。

        join 的字面量可迭代对象中若有一个元素是**已追踪的动态别名变量**
        （如 ``sql = ''.join(("SELECT ", q))`` 的 ``q``），同样记 ``join``；
        纯形态检查（``dynamic_kind``）看不到作用域，这一层补齐。
        """
        kind = dynamic_kind(node)
        if kind is not None:
            return kind
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "join"
            and len(node.args) == 1
        ):
            return None
        elements = _iterable_elements(node.args[0])
        if elements is None:
            return None
        for element in elements:
            if isinstance(element, ast.Starred):
                element = element.value
            if isinstance(element, ast.Name) and self._lookup(element.id) is not None:
                return "join"
        return None

    def _bind(self, target: ast.AST, value: ast.AST, kind: str | None) -> None:
        scope = self._scopes[-1]
        if isinstance(target, ast.Name):
            if kind is not None:
                scope[target.id] = Alias(kind, value.lineno)
            elif isinstance(value, ast.Name):
                alias = self._lookup(value.id)
                if alias is not None:
                    scope[target.id] = alias
                else:
                    scope.pop(target.id, None)
            else:
                scope.pop(target.id, None)
            return
        if (
            isinstance(target, (ast.Tuple, ast.List))
            and isinstance(value, (ast.Tuple, ast.List))
            and len(target.elts) == len(value.elts)
        ):
            for sub_target, sub_value in zip(target.elts, value.elts):
                if isinstance(sub_target, ast.Starred):
                    continue
                self._bind(sub_target, sub_value, dynamic_kind(sub_value))

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        kind = self._scoped_kind(node.value)
        for target in node.targets:
            self._bind(target, node.value, kind)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        if node.value is not None:
            self._bind(node.target, node.value, self._scoped_kind(node.value))
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:  # noqa: N802
        if isinstance(node.op, ast.Add) and isinstance(node.target, ast.Name):
            name = node.target.id
            if self._lookup(name) is None:
                # 常量基串 + 动态追加 → 整体已是动态 SQL；记追加行号。
                # 终审 #60：追加值也可以是已追踪别名（``q += part``），与
                # ``=`` 赋值的别名传播（_bind）同口径——但只在函数作用域
                # 内传播：模块顶层的 ``+=`` 是本仓库固定 schema 常量组合
                # 的惯用形（db.py 的 _SCHEMA 系列），不按动态 SQL 处理。
                alias = None
                kind = self._scoped_kind(node.value)
                if kind is not None:
                    alias = Alias(kind, node.lineno)
                elif len(self._scopes) > 1 and isinstance(node.value, ast.Name):
                    alias = self._lookup(node.value.id)
                if alias is not None:
                    self._scopes[-1][name] = alias
        self.generic_visit(node)

    # ---- 调用点 -------------------------------------------------------
    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        name = _call_name(node.func)
        if name in TARGET_CALLS and node.args:
            arg = node.args[0]
            kind = self._scoped_kind(arg)
            if kind is not None:
                self.hits.append(
                    Hit(
                        file=self.rel_path,
                        line=node.lineno,
                        column=node.col_offset + 1,
                        call=name,
                        kind=kind,
                        direct=True,
                        expression=_snippet(self.source, arg),
                    )
                )
            elif self.resolve_locals and isinstance(arg, ast.Name):
                alias = self._lookup(arg.id)
                if alias is not None:
                    hit = Hit(
                        file=self.rel_path,
                        line=node.lineno,
                        column=node.col_offset + 1,
                        call=name,
                        kind=alias.kind,
                        direct=False,
                        expression=_snippet(self.source, arg),
                        via=alias,
                        via_name=arg.id,
                    )
                    self.hits.append(hit)
        self.generic_visit(node)


def _rel_posix(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:  # pragma: no cover - 发现阶段已保证在 root 内
        return path.as_posix()


def discover_files(root: Path, scan_dirs: tuple[str, ...] = DEFAULT_SCAN_DIRS) -> tuple[list[Path], list[ScanError]]:
    """按确定性顺序列出待扫描文件；缺失扫描根视为用法错误。"""
    files: list[Path] = []
    errors: list[ScanError] = []
    for dirname in scan_dirs:
        base = root / dirname
        if not base.is_dir():
            errors.append(ScanError(dirname, None, "扫描根不存在或不是目录"))
            continue
        for path in sorted(base.rglob("*.py")):
            rel_parts = [part.casefold() for part in path.relative_to(base).parts[:-1]]
            if any(part in EXCLUDED_DIR_NAMES for part in rel_parts):
                continue
            files.append(path)
    return files, errors


def scan_file(path: Path, root: Path, *, resolve_locals: bool = True) -> tuple[list[Hit], ScanError | None]:
    rel = _rel_posix(path, root)
    try:
        with tokenize.open(path) as handle:
            source = handle.read()
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        return [], ScanError(rel, None, f"读取失败：{exc}")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [], ScanError(rel, exc.lineno, f"语法解析失败：{exc.msg}")
    visitor = _ScopeVisitor(rel, source, resolve_locals=resolve_locals)
    try:
        visitor.visit(tree)
    except RecursionError:  # pragma: no cover - 防御性
        return [], ScanError(rel, None, "AST 递归过深，无法扫描")
    return visitor.hits, None


def scan_tree(
    root: Path,
    *,
    scan_dirs: tuple[str, ...] = DEFAULT_SCAN_DIRS,
    resolve_locals: bool = True,
) -> tuple[list[Hit], list[ScanError], int]:
    """扫描整棵源码树，返回 (命中, 错误, 文件数)。命中按确定性键排序。"""
    files, errors = discover_files(root, scan_dirs)
    hits: list[Hit] = []
    for path in files:
        file_hits, error = scan_file(path, root, resolve_locals=resolve_locals)
        hits.extend(file_hits)
        if error is not None:
            errors.append(error)
    hits.sort(key=lambda hit: (hit.file, hit.line, hit.column, hit.call, hit.kind))
    return hits, errors, len(files)


def _normalize_rel(value: str, root: Path) -> str:
    value = value.strip().replace("\\", "/")
    path = Path(value)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return value
    while value.startswith("./"):
        value = value[2:]
    return value


def load_whitelist(path: Path, root: Path) -> tuple[dict[tuple[str, int], str], list[ScanError]]:
    """解析 ``文件:行号#理由`` 白名单。返回 ({(file,line): reason}, errors)。"""
    entries: dict[tuple[str, int], str] = {}
    errors: list[ScanError] = []
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return {}, [ScanError(str(path), None, f"白名单读取失败：{exc}")]
    for lineno, raw in enumerate(raw_lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        location, sep, reason = line.partition("#")
        location = location.strip()
        reason = reason.strip()
        if not sep or not reason:
            errors.append(ScanError(str(path), lineno, "缺少 '#理由'，条目被忽略"))
            continue
        file_part, colon, line_part = location.rpartition(":")
        if not colon or not file_part:
            errors.append(ScanError(str(path), lineno, f"无法解析位置：{location!r}"))
            continue
        try:
            line_no = int(line_part.strip())
        except ValueError:
            errors.append(ScanError(str(path), lineno, f"行号不是整数：{line_part!r}"))
            continue
        key = (_normalize_rel(file_part, root), line_no)
        if key in entries:
            errors.append(ScanError(str(path), lineno, f"重复条目：{key[0]}:{key[1]}"))
            continue
        entries[key] = reason
    return entries, errors


def apply_whitelist(hits: list[Hit], entries: dict[tuple[str, int], str]) -> list[tuple[str, int, str]]:
    """标记白名单命中；返回已登记但无命中的 stale 条目。"""
    matched: set[tuple[str, int]] = set()
    for hit in hits:
        reason = entries.get(hit.key)
        if reason is not None:
            hit.whitelisted = True
            hit.reason = reason
            matched.add(hit.key)
    return sorted(
        (file, line, reason)
        for (file, line), reason in entries.items()
        if (file, line) not in matched
    )


def build_report(
    root: Path,
    hits: list[Hit],
    errors: list[ScanError],
    files_scanned: int,
    *,
    whitelist_path: Path | None = None,
    whitelist_entries: int = 0,
    stale: list[tuple[str, int, str]] | None = None,
) -> dict:
    direct = sum(1 for hit in hits if hit.direct)
    whitelisted = sum(1 for hit in hits if hit.whitelisted)
    return {
        "root": str(root),
        "scanned_files": files_scanned,
        "counts": {
            "total": len(hits),
            "direct": direct,
            "indirect": len(hits) - direct,
            "whitelisted": whitelisted,
            "unwhitelisted": len(hits) - whitelisted,
        },
        "hits": [hit.to_dict() for hit in hits],
        "whitelist": {
            "path": None if whitelist_path is None else str(whitelist_path),
            "entries": whitelist_entries,
            "stale": [
                {"file": file, "line": line, "reason": reason} for file, line, reason in (stale or [])
            ],
        },
        "errors": [error.to_dict() for error in errors],
    }


def _format_hit(hit: Hit) -> str:
    prefix = "[whitelisted] " if hit.whitelisted else ""
    via = ""
    if hit.via is not None:
        via = f" via={hit.via_name}@{hit.via.line}"
    return (
        f"{prefix}{hit.file}:{hit.line}:{hit.column}: {hit.kind} {hit.call}{via} :: "
        f"{hit.expression}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="扫描 coscreen/ 与 custom_backend/ 中动态拼装 SQL 的 execute 调用点。",
    )
    parser.add_argument("--root", type=Path, default=ROOT, help="仓库根（默认：本脚本上两级）")
    parser.add_argument("--whitelist", type=Path, default=None, help="白名单文件（文件:行号#理由）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 报告到 stdout")
    parser.add_argument(
        "--direct-only",
        action="store_true",
        help="只看第一实参直接为动态表达式，关闭局部变量别名追踪",
    )
    args = parser.parse_args(argv)

    root: Path = args.root
    if not root.is_dir():
        print(f"错误：--root 不是目录：{root}", file=sys.stderr)
        return EXIT_ERROR

    hits, errors, files_scanned = scan_tree(
        root, resolve_locals=not args.direct_only
    )

    whitelist_path: Path | None = None
    entry_count = 0
    stale: list[tuple[str, int, str]] = []
    if args.whitelist is not None:
        entries, wl_errors = load_whitelist(args.whitelist, root)
        errors.extend(wl_errors)
        stale = apply_whitelist(hits, entries)
        whitelist_path = args.whitelist
        entry_count = len(entries)

    report = build_report(
        root,
        hits,
        errors,
        files_scanned,
        whitelist_path=whitelist_path,
        whitelist_entries=entry_count,
        stale=stale,
    )

    unwhitelisted = report["counts"]["unwhitelisted"]

    if args.json:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        for hit in hits:
            print(_format_hit(hit))
        print(
            "动态 SQL 扫描：文件 {files}，命中 {total}（直接 {direct}，间接 {indirect}），"
            "白名单 {whitelisted}，未白名单 {unwhitelisted}".format(
                files=files_scanned,
                total=report["counts"]["total"],
                direct=report["counts"]["direct"],
                indirect=report["counts"]["indirect"],
                whitelisted=report["counts"]["whitelisted"],
                unwhitelisted=unwhitelisted,
            )
        )

    for file, line, reason in stale:
        print(f"警告：白名单条目已失效（无对应命中）：{file}:{line}#{reason}", file=sys.stderr)
    for error in errors:
        location = error.file if error.line is None else f"{error.file}:{error.line}"
        print(f"错误：{location}：{error.message}", file=sys.stderr)

    if errors:
        return EXIT_ERROR
    if unwhitelisted:
        return EXIT_FINDINGS
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
