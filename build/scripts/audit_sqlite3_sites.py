"""Canonical sqlite3 call-site coverage auditor (S5, 2026-11-21).

统一收口此前散落各证据箱的审计工具变体（audit_cover.py 原版只认
``store.connect`` 单名；store-scan 箱扩展了别名接收者与
migrate/has_table 属性族）。口径：

- 站点 = ``<alias>.<attr>(...)``，attr ∈ connect / connect_for_initial_migration
  / migrate / has_table，alias ∈ store / workflow / workflow_store /
  import_store / review_workflow / sqlite3 / _sqlite3 / db_mod；
- 覆盖 = 站点词法上处于某 Try 体内，且该 Try 至少一个 handler 捕获
  sqlite3.Error / DatabaseError / **OperationalError**（子类计入——消除
  ``except sqlite3.OperationalError`` 精确收口站点的误报，如
  import_ai_assist）或 Exception / BaseException 超集；
- 只读报告，零产品改动。

用法::

    python -I -B build/scripts/audit_sqlite3_sites.py <file.py> [...]

来源：store 全域扫描（2026-11-21，REPORT §5 S5）裁定"工具口径修订"项；
扩展版基线 = E:/rf216-store-scan-20261120/audit_cover_aliases.py。
"""
import ast
import sys

SQLITE_BROAD = {"Error", "DatabaseError", "OperationalError"}
SUPERSETS = {"Exception", "BaseException"}
RECEIVERS = {"store", "workflow", "workflow_store", "import_store",
             "review_workflow", "sqlite3", "_sqlite3", "db_mod"}
ATTRS = {"connect", "connect_for_initial_migration", "migrate", "has_table"}


def handler_catches_sqlite(handler) -> bool:
    names = []
    for node in ast.walk(handler.type or ast.Pass()):
        if isinstance(node, ast.Attribute):
            names.append(node.attr)
        elif isinstance(node, ast.Name):
            names.append(node.id)
    return any(n in SQLITE_BROAD or n in SUPERSETS for n in names)


def covered_by_try(node_line: int, func) -> bool:
    if func is None:
        return False
    for sub in ast.walk(func):
        if isinstance(sub, ast.Try):
            for h in sub.handlers:
                if handler_catches_sqlite(h):
                    for stmt in sub.body:
                        if stmt.lineno <= node_line <= max(
                                getattr(stmt, "end_lineno", stmt.lineno),
                                stmt.lineno):
                            return True
    return False


class Visitor(ast.NodeVisitor):
    def __init__(self):
        self.func_stack = []
        self.missing = 0

    def visit_FunctionDef(self, node):
        self.func_stack.append(node)
        self.generic_visit(node)
        self.func_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node):
        target = node.func
        if (isinstance(target, ast.Attribute) and target.attr in ATTRS
                and isinstance(target.value, ast.Name)
                and target.value.id in RECEIVERS):
            func = self.func_stack[-1] if self.func_stack else None
            ok = covered_by_try(node.lineno, func)
            if not ok:
                self.missing += 1
            print(f"{node.lineno:5d}  {'COVERED' if ok else 'MISSING':7s}  "
                  f"{target.value.id}.{target.attr}  "
                  f"{(func and func.name) or '<module>'}")
        self.generic_visit(node)


def main(argv):
    total_missing = 0
    for path in argv[1:]:
        src = open(path, encoding="utf-8").read()
        visitor = Visitor()
        visitor.visit(ast.parse(src))
        total_missing += visitor.missing
    print(f"audit_sqlite3_sites: {len(argv) - 1} file(s), MISSING={total_missing}")
    return 1 if total_missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
