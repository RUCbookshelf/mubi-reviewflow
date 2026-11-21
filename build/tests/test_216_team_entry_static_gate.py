# -*- coding: utf-8 -*-
"""P4 静态防复发闸（test 216）——两缺口族根治收尾包的三层静态闸。

底版：主仓 ``E:/mubi-reviewflow`` **现行工作树**（P1-P4 未提交整合后的状态，
勘察日 2026-11-21）。本文件为 candidate 交付件（箱
``E:/rf216-p4-staticgate-20261121/build/tests/``），只新增、零产品改动；
落地主仓时放 ``build/tests/test_216_team_entry_static_gate.py`` 即按
``__file__`` 向上自动定位产品树。

三闸（全部纯 AST，不 import 任何产品模块 → 对现有套件结构性零干扰）：

闸 1【入场路由完备性】扫 custom_backend 全部 FastAPI 写路由；凡路径命中
   协作/整包导入模式（``/collaboration/`` 或 ``import-package``）的
   POST/PUT/DELETE/PATCH 路由都是"入场候选"，必须显式登记为三类之一：
   入场（须可达准备触发）/ 豁免（设计上无准备触发，理由登记在案）。
   未登记的新候选 → 红（评审可见性）；已登记入场路由的调用链 BFS 不可达
   任何准备触发（``_prepare_invited_member_storage`` /
   ``_ensure_owner_team_storage`` / ``_ensure_member_team_storage`` /
   ``prepare_team_protocol_storage`` / ``_ensure_business_role_schema`` /
   ``_prepare_imported_team_storage``）→ 红。新入口忘接线必红。

闸 2【异常捕获完备性】``typed_team_http``（team_http_errors 统一映射根）
   必须引用勘察并集四族（TeamFlowReceiptUnavailable /
   TerminalFactsUnavailable / TerminalRegistryUnavailable /
   VoteLedgerUnavailable）；三个异常面函数（``_mutate_team`` /
   ``_team_http_for`` / ``_team_management_http_error``）对四族的覆盖 =
   自身 except 元组 ∪（经 ``typed_team_http`` 委托则继承根覆盖）。
   任一函数漏任一族 → 红。

闸 3【准备函数纯调用白名单】prepare 族六个函数在产品代码
   （custom_backend/*.py + coscreen/*.py）中的每个调用点都按
   （文件, 所在函数, 被调名, 次数）显式登记；多出/减少/换位置 → 红。
   新增调用点必须显式改登记清单 = 评审可见性。

跑法（逐文件，隔离解释器）::

    venv312/Scripts/python.exe -I -B build/tests/test_216_team_entry_static_gate.py
    # 或 pytest 逐文件：venv312 -I -B -m pytest build/tests/test_216_team_entry_static_gate.py

变异自证（在箱内副本树上做，不碰主仓）：设
``RF216_STATIC_GATE_ROOT`` 指向箱内变异树（custom_backend/ + coscreen/
task_collaboration.py 的拷贝，各闸各篡改一处），三闸应分别转红；
未设该环境变量时按 ``__file__`` 向上定位主仓产品树。
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

# ======================================================================
# 产品树定位与共享 AST 基础设施
# ======================================================================

#: 闸 1/闸 2 调用图覆盖的模块集合：custom_backend 全部模块 + 协作库
#: schema 原语所在文件（准备触发 ``_ensure_business_role_schema`` 定义处）。
GRAPH_COSCREEEN_FILES = ("task_collaboration.py",)

#: 闸 3 的产品代码扫描范围（文本预筛 + AST 计数）：custom_backend 全部 +
#: coscreen 全部。准备族函数只可能在这些目录被调用（desktop 后端域）。
GATE3_SCAN_DIRS = ("custom_backend", "coscreen")

#: 勘察并集族（survey §3.1-3.4）：写路径存储层四族。任何写通道异常面
#: 漏掉其一，对应确定性未准备/损坏条件会落裸 500 或被误说成"稍后再试"。
UNION_FAMILY_NAMES = (
    "TeamFlowReceiptUnavailable",
    "TerminalFactsUnavailable",
    "TerminalRegistryUnavailable",
    "VoteLedgerUnavailable",
)

#: 准备触发函数（闸 1 可达性目标 = 闸 3 白名单被调名族）。
PREPARE_TRIGGER_NAMES = frozenset({
    "_prepare_invited_member_storage",
    "_ensure_owner_team_storage",
    "_ensure_member_team_storage",
    "prepare_team_protocol_storage",
    "_ensure_business_role_schema",
    "_prepare_imported_team_storage",
})

_WRITE_METHODS = frozenset({"post", "put", "delete", "patch"})
#: 入场候选的路径模式（写协作/成员/团队资格/整包导入）。
_ENTRY_PATH_MARKERS = ("/collaboration/", "import-package")
_FUNC_DECOS = {"app.post", "app.put", "app.delete", "app.patch", "app.get"}


def _locate_repo_root() -> Path:
    """定位被闸的产品树根（含 custom_backend/main.py 与 coscreen/）。

    优先级：环境变量 ``RF216_STATIC_GATE_ROOT``（变异自证专用，指向箱内
    副本树）> 从 ``__file__`` 向上回溯（落地主仓 build/tests/ 时自动命中）。
    """
    env = os.environ.get("RF216_STATIC_GATE_ROOT")
    if env:
        root = Path(env).resolve()
        if not (root / "custom_backend" / "main.py").is_file():
            raise RuntimeError(
                f"RF216_STATIC_GATE_ROOT={root} 下没有 custom_backend/main.py，"
                "变异树需保留 custom_backend/ 目录布局")
        return root
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        if ((parent / "custom_backend" / "main.py").is_file()
                and (parent / "coscreen").is_dir()):
            return parent
    raise RuntimeError(
        "无法定位产品树根（custom_backend/main.py + coscreen/）：既没有设置 "
        "RF216_STATIC_GATE_ROOT，也从本测试文件向上找不到仓库布局。")


_REPO_ROOT = _locate_repo_root()
_BACKEND = _REPO_ROOT / "custom_backend"
_COSCREEN = _REPO_ROOT / "coscreen"


def _rel(path: Path) -> str:
    return path.resolve().relative_to(_REPO_ROOT).as_posix()


def _backend_py_files() -> list[Path]:
    return sorted(_BACKEND.glob("*.py"))


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _all_function_defs(tree: ast.Module):
    """(名称, 节点) 全部函数/方法（含嵌套 handler）。"""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(node)
    return out


def _names_in_subtree(fn: ast.AST) -> set[str]:
    """函数子树内的调用点名称（Call func 名 + 函数作为实参传递的引用名）。

    注意：只收 Call 形位与**调用实参**形位的名字，不收普通变量 LOAD——
    变量名与函数名的偶然重名会制造假边（勘察发现 ``metadata``/
    ``present_record`` 等变量名经裸名合并撞进 ``mutate`` 咽喉，虚增豁免
    路由的"可达性"）。函数作为值传递（``run_in_threadpool(_apply_import,
    ...)``）不构成 ast.Call，但同样是调用链事实，经实参形位覆盖。
    """
    out: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
            for a in node.args:
                if isinstance(a, ast.Name):
                    out.add(a.id)
    return out


class _Graph:
    """按 import 语义解析的跨模块函数调用图（裸名最后一段只在受控形位生效）。

    解析规则（保守=宁可断边不造假边）：
    - 裸名 ``n``：同文件已定义函数；或本文件 ``from <已扫模块> import n``。
    - ``self.attr``/``cls.attr``：同文件内同名函数/方法。
    - ``<模块别名>.attr``：别名指到已扫模块时，attr 须是该模块已定义函数
      （``team_lifecycle.prepare_team_protocol_storage``、
      ``main_mod._prepare_invited_member_storage`` 等真实跨模块边）。
    - 其余属性调用（``payload.get``/``store.rename``/``runtime.mutate``/
      ``conn.execute`` 等运行时对象方法）一律不建边——动态分派不可静态
      证实，断边只可能漏报触发（对闸 1 而言这些形位没有准备触发语义）。
    """

    def __init__(self):
        self.scanned_rel: set[str] = set()
        self.defs: dict[str, set[str]] = {}          # rel -> 函数名集
        self.node_of: dict[tuple[str, str], ast.FunctionDef] = {}
        self.module_alias: dict[str, dict[str, str]] = {}
        self.symbol_import: dict[str, dict[str, tuple[str, str]]] = {}
        self._trees: dict[str, ast.Module] = {}
        # 两遍吸收：先全量收 defs/节点（符号导入解析依赖目标模块的 defs
        # 已就位——文件字母序会让 team_repair_api 先于
        # team_source_lifecycle 被吸收而漏边），再统一处理 import。
        files = [*_backend_py_files()]
        collab = _COSCREEN / "task_collaboration.py"
        if collab.is_file():
            files.append(collab)
        for py in files:
            self._collect_defs(py)
        for py in files:
            self._collect_imports(py)

    def _collect_defs(self, py: Path):
        rel = _rel(py)
        self.scanned_rel.add(rel)
        tree = _parse(py)
        self._trees[rel] = tree
        for fn in _all_function_defs(tree):
            self.defs.setdefault(rel, set()).add(fn.name)
            self.node_of[(rel, fn.name)] = fn
        self.module_alias.setdefault(rel, {})
        self.symbol_import.setdefault(rel, {})

    def _collect_imports(self, py: Path):
        rel = _rel(py)
        tree = self._trees[rel]
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if isinstance(node, ast.Import):
                for a in node.names:
                    mod = self._dotted_to_rel(a.name)
                    if mod:
                        self.module_alias[rel][a.asname or a.name.split(".")[0]] = mod
            else:
                # 逐名处理：先试“from <包> import 子模块 [as 别名]”（覆盖
                # ``from custom_backend import main as main_mod`` /
                # ``from coscreen import task_collaboration`` 这类包内子模块
                # 导入——此时 node.module 是包名，本身不是已扫模块），
                # 再退回“from <已扫模块> import 函数 [as 别名]”。
                mod = self._dotted_to_rel(node.module or "")
                for a in node.names:
                    if a.name == "*":
                        continue
                    sub = self._dotted_to_rel(f"{node.module}.{a.name}")
                    if sub:
                        self.module_alias[rel][a.asname or a.name] = sub
                        continue
                    if mod and a.name in self.defs.get(mod, ()):
                        # from <模块> import 函数 [as 别名]
                        self.symbol_import[rel][a.asname or a.name] = (mod, a.name)

    @staticmethod
    def _dotted_to_rel(dotted: str) -> str | None:
        """点路径 → 已扫模块的相对路径（取最短后缀匹配）。"""
        parts = dotted.split(".")
        for i in range(len(parts)):
            cand = "/".join(parts[i:])
            for prefix in ("custom_backend/", "coscreen/"):
                rel = prefix + cand + ".py"
                if rel in _Graph._scanned_rel_cache():
                    return rel
        return None

    @staticmethod
    def _scanned_rel_cache() -> set[str]:
        if not hasattr(_Graph, "_SRC_CACHE"):
            _Graph._SRC_CACHE = (
                {p.resolve().relative_to(_REPO_ROOT).as_posix()
                 for p in _backend_py_files()}
                | {"coscreen/task_collaboration.py"})
        return _Graph._SRC_CACHE

    def _edge(self, module: str, name: str):
        """裸名/属性最后一段在 module 内的解析结果（rel, name) 或 None。"""
        if name in self.defs.get(module, ()):
            return (module, name)
        tgt = self.symbol_import.get(module, {}).get(name)
        if tgt and tgt[1] in self.defs.get(tgt[0], ()):
            return tgt
        return None

    def resolve_attr(self, module: str, base: str, attr: str):
        if base in ("self", "cls"):
            if attr in self.defs.get(module, ()):
                return (module, attr)
            return None
        mod = self.module_alias.get(module, {}).get(base)
        if mod and attr in self.defs.get(mod, ()):
            return (mod, attr)
        return None

    def callees(self, module: str, fn: ast.FunctionDef):
        out: set[tuple[str, str]] = set()
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if isinstance(f, ast.Name):
                e = self._edge(module, f.id)
                if e:
                    out.add(e)
            elif isinstance(f, ast.Attribute):
                if isinstance(f.value, ast.Name):
                    e = self.resolve_attr(module, f.value.id, f.attr)
                    if e:
                        out.add(e)
            for a in node.args:
                if isinstance(a, ast.Name):
                    e = self._edge(module, a.id)
                    if e:
                        out.add(e)
        return out


_GRAPH_BUILT: _Graph | None = None


def _call_graph() -> _Graph:
    global _GRAPH_BUILT
    if _GRAPH_BUILT is None:
        _GRAPH_BUILT = _Graph()
    return _GRAPH_BUILT


def _reachable_triggers(module: str, handler: ast.FunctionDef,
                        graph: _Graph) -> set[str]:
    """BFS 调用链（import 语义解析）可达的准备触发集合。"""
    seen: set[tuple[str, str]] = set()
    stack = [(module, handler)]
    hits: set[str] = set()
    while stack:
        mod, cur = stack.pop()
        for callee_mod, callee_name in graph.callees(mod, cur):
            if callee_name in PREPARE_TRIGGER_NAMES:
                hits.add(callee_name)
            key = (callee_mod, callee_name)
            if key in seen:
                continue
            seen.add(key)
            node = graph.node_of.get(key)
            if node is not None:
                stack.append((callee_mod, node))
    return hits


# ======================================================================
# 闸 1：入场路由完备性
# ======================================================================

#: 已登记入场路由（module, METHOD, path）：调用链必须 BFS 可达准备触发。
REGISTERED_ENTRY_ROUTES = frozenset({
    ("custom_backend/main.py", "POST",
     "/api/tasks/{task_id}/collaboration/invite"),
    ("custom_backend/main.py", "POST",
     "/api/tasks/{task_id}/collaboration/roles"),
    ("custom_backend/main.py", "DELETE",
     "/api/tasks/{task_id}/collaboration/roles/{target_user_id}/{role}"),
    ("custom_backend/main.py", "PUT",
     "/api/tasks/{task_id}/collaboration/business-role/{target_user_id}"),
    ("custom_backend/main.py", "POST",
     "/api/tasks/{task_id}/collaboration/rebind"),
    ("custom_backend/main.py", "PUT",
     "/api/tasks/{task_id}/collaboration/members/{target_user_id}/screening"),
    ("custom_backend/task_package_api.py", "POST", "/api/tasks/import-package"),
    ("custom_backend/team_repair_api.py", "POST",
     "/api/tasks/{task_id}/collaboration/repair/force-remove"),
})

#: 已登记豁免路由：设计上不触发准备（理由在案），调用链不可达触发不算红。
REGISTERED_WAIVER_ROUTES = {
    ("custom_backend/main.py", "POST",
     "/api/tasks/{task_id}/collaboration/repair"):
        "collaboration_repair_store=R6-C4 修库隔离：仅把损坏 collaboration.db "
        "改名隔离，零存储写入、不产生成员/资格状态，无准备触发属设计事实。",
    ("custom_backend/team_repair_api.py", "POST",
     "/api/tasks/{task_id}/collaboration/repair/seal-conflict"):
        "repair_seal_conflict=R6 seal 冲突修复：只作用于既有 collab store"
        "（读 seal/归档/append receipt），不能对不存在的库运行，无准备触发。",
    ("custom_backend/task_package_api.py", "POST",
     "/api/tasks/import-package/preflight"):
        "import_package_preflight=纯校验 dry-run：只解析包体/校验哈希，"
        "零落盘零成员状态（只读语义的 POST 上传端点）。",
}


class _Route(ast.AST):
    __slots__ = ("module", "method", "path", "handler", "node", "line")

    def __init__(self, module, method, path, handler, node, line):
        self.module = module
        self.method = method
        self.path = path
        self.handler = handler
        self.node = node
        self.line = line


def _collect_routes() -> list[_Route]:
    routes: list[_Route] = []
    for py in _backend_py_files():
        tree = _parse(py)
        for fn in _all_function_defs(tree):
            for dec in fn.decorator_list:
                dec_node = dec.func if isinstance(dec, ast.Call) else dec
                if not (isinstance(dec_node, ast.Attribute)
                        and isinstance(dec_node.value, ast.Name)):
                    continue
                qual = f"{dec_node.value.id}.{dec_node.attr}"
                if qual not in _FUNC_DECOS:
                    continue
                path = None
                if isinstance(dec, ast.Call) and dec.args \
                        and isinstance(dec.args[0], ast.Constant):
                    path = dec.args[0].value
                if path is None:
                    continue
                routes.append(_Route(_rel(py), dec_node.attr, path,
                                     fn.name, fn, fn.lineno))
    return routes


def _build_call_graph():
    """（兼容壳）构造 import 语义解析调用图。"""
    return _call_graph()


def test_gate1_entry_routes_reach_prepare_trigger():
    routes = _collect_routes()
    graph = _call_graph()

    candidates, registered, waived = [], [], []
    for r in routes:
        if r.method.lower() not in _WRITE_METHODS:
            continue
        if not any(m in r.path for m in _ENTRY_PATH_MARKERS):
            continue
        key = (r.module, r.method.upper(), r.path)
        candidates.append(key)
        if key in REGISTERED_ENTRY_ROUTES:
            registered.append((key, r))
        elif key in REGISTERED_WAIVER_ROUTES:
            waived.append((key, r))

    # 1a. 未登记的入场候选必红（新入口必须先登记再评审）。
    unregistered = sorted(set(candidates)
                          - REGISTERED_ENTRY_ROUTES
                          - set(REGISTERED_WAIVER_ROUTES))
    assert not unregistered, (
        "闸1/未登记入场路由（缺登记）：以下写协作/整包导入路径的候选路由既不在 "
        "REGISTERED_ENTRY_ROUTES 也不在 REGISTERED_WAIVER_ROUTES。新入口必须先"
        "登记（入场=须可达准备触发；豁免=设计事实+理由）。缺：\n  "
        + "\n  ".join(f"{m} {meth} {p}" for m, meth, p in unregistered))

    # 1b. 登记清单里的路由必须真实存在（防登记悬空=闸失明）。
    missing = sorted(REGISTERED_ENTRY_ROUTES - set(candidates))
    assert not missing, (
        "闸1/登记悬空（多登记）：REGISTERED_ENTRY_ROUTES 中这些路由已不存在于"
        "产品路由表，请更新登记清单。多：\n  "
        + "\n  ".join(f"{m} {meth} {p}" for m, meth, p in missing))

    # 1c. 每条已登记入场路由的调用链必须可达准备触发。
    gaps = []
    for key, r in registered:
        hits = _reachable_triggers(r.module, r.node, graph)
        if not hits:
            gaps.append(
                f"  {key[0]}:{r.line} {key[1]} {key[2]} handler={r.handler} "
                f"(零准备触发)")
    assert not gaps, (
        "闸1/入场路由未接线（缺准备触发）：以下已登记入场路由的调用链静态不可达"
        "任何准备触发（_prepare_invited_member_storage/_ensure_owner_team_storage/"
        "_ensure_member_team_storage/prepare_team_protocol_storage/"
        "_ensure_business_role_schema/_prepare_imported_team_storage）。缺：\n"
        + "\n".join(gaps))

    # 1d. 豁免登记不得虚增（豁免路由若真的可达触发，说明行为变了，须改登记）。
    stale = []
    for key, r in waived:
        hits = _reachable_triggers(r.module, r.node, graph)
        if hits:
            stale.append(f"  {key[0]}:{r.line} {key[1]} {key[2]} "
                         f"handler={r.handler} 可达 {sorted(hits)}")
    assert not stale, (
        "闸1/豁免失真（多行为）：登记为豁免（无准备触发）的路由现在可达准备触发"
        "——行为已变更，请改为入场登记。多：\n" + "\n".join(stale))


# ======================================================================
# 闸 2：异常捕获完备性
# ======================================================================

_TYPED_TEAM_HTTP_MODULE = "custom_backend/team_http_errors.py"
_TYPED_TEAM_HTTP_FUNC = "typed_team_http"
_CONTRACT_REASON_NAME = "CONTRACT_OF_BREACH_REASON"

_EXCEPT_SURFACE_TARGETS = (
    # (文件, 函数名, 角色) —— 三个异常面适配器
    ("custom_backend/review_workflow_api.py", "_mutate_team",
     "成员写主通道适配器"),
    ("custom_backend/arbitration_session_api.py", "_team_http_for",
     "仲裁适配器映射器"),
    ("custom_backend/main.py", "_team_management_http_error",
     "管理端点协调器映射器"),
)


def _except_tuple_names(fn: ast.FunctionDef) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            for sub in ast.walk(node.type):
                if isinstance(sub, ast.Name):
                    out.add(sub.id)
    return out


def _references(fn: ast.FunctionDef, name: str) -> bool:
    return any(isinstance(n, ast.Name) and n.id == name
               for n in ast.walk(fn))


def _find_function(module: str, name: str) -> tuple[Path, ast.FunctionDef]:
    path = _REPO_ROOT / module
    tree = _parse(path)
    for fn in _all_function_defs(tree):
        if fn.name == name:
            return path, fn
    pytest.fail(f"闸2/目标函数缺失：{module} 中找不到 {name}（函数被重命名/删除？"
                "请同步更新闸2目标清单。）")


def test_gate2_typed_team_http_covers_union_families():
    path, fn = _find_function(_TYPED_TEAM_HTTP_MODULE, _TYPED_TEAM_HTTP_FUNC)
    body_names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    missing = [f for f in UNION_FAMILY_NAMES if f not in body_names]
    assert not missing, (
        f"闸2/统一映射根缺族：{_TYPED_TEAM_HTTP_MODULE}::{_TYPED_TEAM_HTTP_FUNC} "
        f"不再引用勘察并集族 {missing}——四族之一脱离统一映射（漏一个族必红）。"
        f"缺：{missing}")


def test_gate2_except_surface_covers_union_families():
    _path, root_fn = _find_function(_TYPED_TEAM_HTTP_MODULE,
                                    _TYPED_TEAM_HTTP_FUNC)
    root_names = {n.id for n in ast.walk(root_fn) if isinstance(n, ast.Name)}
    root_covers = {f for f in UNION_FAMILY_NAMES if f in root_names}

    gaps = []
    for module, fname, role in _EXCEPT_SURFACE_TARGETS:
        path, fn = _find_function(module, fname)
        direct = _except_tuple_names(fn)
        covered = {f for f in UNION_FAMILY_NAMES if f in direct}
        if _references(fn, _TYPED_TEAM_HTTP_FUNC):
            covered |= root_covers
        missing = [f for f in UNION_FAMILY_NAMES if f not in covered]
        if missing:
            gaps.append(
                f"  {module}::{fname}（{role}，line {fn.lineno}）缺 {missing}"
                f"（自身 except 面={sorted(direct & set(UNION_FAMILY_NAMES))}"
                f"；typed_team_http 委托={'有' if _references(fn, _TYPED_TEAM_HTTP_FUNC) else '无'}）")
    assert not gaps, (
        "闸2/异常面漏族：以下异常适配器对勘察并集四族的覆盖不完备"
        "（漏一个族必红）。缺：\n" + "\n".join(gaps))


# ======================================================================
# 闸 3：准备函数纯调用白名单
# ======================================================================

#: (相对文件, 所在函数, 被调名) → 期望调用次数（勘察日 2026-11-21，主仓
#: 现行工作树）。新增调用点必须在此显式登记（评审可见性）；未登记模块/
#: 函数出现 prepare 族调用 → 红。
REGISTERED_PREPARE_CALL_SITES = {
    ("custom_backend/main.py", "_run_team_management_entry",
     "_ensure_owner_team_storage"): 1,
    ("custom_backend/main.py", "_prepare_invited_member_storage",
     "prepare_team_protocol_storage"): 1,
    ("custom_backend/main.py", "_ensure_owner_team_storage",
     "prepare_team_protocol_storage"): 1,
    ("custom_backend/main.py", "_ensure_owner_team_storage",
     "_ensure_business_role_schema"): 1,
    ("custom_backend/main.py", "_register_fresh_team_protocol",
     "prepare_team_protocol_storage"): 1,
    ("custom_backend/main.py", "collaboration_invite",
     "_prepare_invited_member_storage"): 1,
    ("custom_backend/main.py", "_member_mutate_notified",
     "_ensure_owner_team_storage"): 1,
    ("custom_backend/main.py", "team_progress",
     "_ensure_owner_team_storage"): 1,
    ("custom_backend/main.py", "collaboration_grant_role",
     "_prepare_invited_member_storage"): 1,
    ("custom_backend/review_workflow_api.py", "_ensure_member_team_storage",
     "prepare_team_protocol_storage"): 1,
    ("custom_backend/review_workflow_api.py", "mutate",
     "_ensure_member_team_storage"): 1,
    ("custom_backend/task_package_api.py", "_import_team_provenance",
     "prepare_team_protocol_storage"): 1,
    ("custom_backend/task_package_api.py", "_prepare_imported_team_storage",
     "_prepare_invited_member_storage"): 1,
    ("custom_backend/task_package_api.py", "_prepare_imported_team_storage",
     "prepare_team_protocol_storage"): 1,
    ("custom_backend/task_package_api.py", "_apply_import",
     "_prepare_imported_team_storage"): 1,
    ("custom_backend/team_source_lifecycle.py", "prepare_team_protocol_storage",
     "_ensure_business_role_schema"): 1,
    ("custom_backend/team_source_lifecycle.py", "_prepare_retiring_source_storage",
     "prepare_team_protocol_storage"): 1,
    ("coscreen/task_collaboration.py", "set_business_role",
     "_ensure_business_role_schema"): 1,
}


def _gate3_scan_files() -> list[Path]:
    files: list[Path] = []
    for d in GATE3_SCAN_DIRS:
        base = _REPO_ROOT / d
        if not base.is_dir():
            continue
        files.extend(base.rglob("*.py"))
    return [p for p in files if "__pycache__" not in p.parts]


def test_gate3_prepare_family_call_site_whitelist():
    found: dict[tuple[str, str, str], int] = {}
    for py in _gate3_scan_files():
        text = py.read_text(encoding="utf-8", errors="replace")
        if not any(nm in text for nm in PREPARE_TRIGGER_NAMES):
            continue  # 文本预筛：无族名文件零解析成本
        rel = _rel(py)
        tree = ast.parse(text, filename=str(py))

        def visit(node, stack):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    visit(child, stack + [child.name])
                    continue
                if isinstance(child, ast.Call):
                    f = child.func
                    nm = (f.id if isinstance(f, ast.Name)
                          else f.attr if isinstance(f, ast.Attribute) else None)
                    if nm in PREPARE_TRIGGER_NAMES:
                        key = (rel, stack[-1] if stack else "<module>", nm)
                        found[key] = found.get(key, 0) + 1
                visit(child, stack)

        visit(tree, [])

    extra = sorted(set(found) - set(REGISTERED_PREPARE_CALL_SITES))
    missing = sorted(set(REGISTERED_PREPARE_CALL_SITES) - set(found))
    drift = sorted(
        (k for k in set(found) & set(REGISTERED_PREPARE_CALL_SITES)
         if found[k] != REGISTERED_PREPARE_CALL_SITES[k]))

    problems = []
    if extra:
        problems.append(
            "多（未登记的新调用点——新增调用必须显式改 "
            "REGISTERED_PREPARE_CALL_SITES 登记清单）：\n  "
            + "\n  ".join(f"{k[0]}::{k[1]} 调 {k[2]} x{found[k]}" for k in extra))
    if missing:
        problems.append(
            "缺（登记清单中的调用点已消失——准备接线被拆/函数被改名）：\n  "
            + "\n  ".join(f"{k[0]}::{k[1]} 调 {k[2]} 应 x{REGISTERED_PREPARE_CALL_SITES[k]}"
                          for k in missing))
    if drift:
        problems.append(
            "漂移（同位置调用次数与登记不符）：\n  "
            + "\n  ".join(f"{k[0]}::{k[1]} 调 {k[2]} 实 x{found[k]} 应 "
                          f"x{REGISTERED_PREPARE_CALL_SITES[k]}" for k in drift))
    assert not problems, (
        "闸3/prepare 族纯调用白名单失配。\n" + "\n".join(problems))


# ======================================================================
# 逐文件直跑入口（python -I -B test_...py 时给出可读结论）
# ======================================================================

if __name__ == "__main__":
    import sys
    failures = 0
    for gate in (test_gate1_entry_routes_reach_prepare_trigger,
                 test_gate2_typed_team_http_covers_union_families,
                 test_gate2_except_surface_covers_union_families,
                 test_gate3_prepare_family_call_site_whitelist):
        try:
            gate()
        except AssertionError as exc:
            failures += 1
            print(f"[RED] {gate.__name__}\n{exc}\n", file=sys.stderr)
        else:
            print(f"[GREEN] {gate.__name__}")
    print(f"product tree = {_REPO_ROOT}")
    sys.exit(1 if failures else 0)
