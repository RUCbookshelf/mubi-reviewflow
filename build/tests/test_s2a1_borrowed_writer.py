# -*- coding: utf-8 -*-
"""S2-A1 borrowed-writer 合同钉子测试（g-flash 接力；合同依据
out/maintenance216/s2a1_borrowed_writer_design.md §2/§3/§8 与其 brief，场景
依据 s2a1_test_implementation_plan_gflash.md（含 §4.1 主控补充）。

合同：三 writer 新增 keyword-only ``conn=None``（db.save_decision /
fulltext.save_stage2 / workspace_undo.undo），两 helper 新增 keyword-only
``prepared=False``（workspace_undo.begin_save /
research_trace.begin_capture_transaction）。本文件只按冻结合同驱动行为，
不钉任何实现行号（S2-A1 产品源并行实施中）。

场景图（方法数＝实数）：
  C1  borrowed save_decision：全部语句落在 caller conn＋双连接未提交不可见
  C2  borrowed save_stage2：同上（fulltext 域）
  C3  borrowed undo：复用外层锁定事务（返回 dict 形状不变）
  C4  缺 journal 表：fail-closed 零 DDL，外层回滚全撤
  C5  旧形 journal 缺列：借用 undo 零 ALTER fail-closed；列在正控通过
  C6  外层回滚：business/ledger advances+revisions（含墓碑面）/undo/trace/
      request 同撤，library_id 保持
  C7  外层回滚撤 borrowed undo 的恢复
  C8  replay：返回 None 不提交、无重复
  C9  standalone save 旧语义正控（缺省路径应 PASS）
  C10 standalone undo 旧语义正控（独立 BEGIN IMMEDIATE / 旧签名 begin_save /
      手建条目）
  C11 prepared helper 无活动事务拒绝（任何 SQL 前，零语句）
  C12 prepared begin_capture 缺 trace schema fail-closed（不被折叠 False 吞）
  C13 prepared 现读 setting（不缓存 prepare 时快照）
  C14 无活动事务任何 conn 在任何 SQL 前拒（两 save trace_context=None 同样；
      undo 传合法位置 ctx）
  C12b 各自独立缺 trace_events / trace_write_operations 的 fail-closed
  C16 两 standalone save 的连接前受控时钟采样（无 sleep）
  C15 conn 非 sqlite3.Connection → TypeError；已关闭连接 → ProgrammingError；
      零降级零 fallback

观测（§4.1）：真实 ``sqlite3.Connection`` 子类经 ``sqlite3.connect(factory=…)``
创建（isinstance 恒真），仅观测 writer 调用增量（测试自身
prepare/BEGIN/rollback/观测 SELECT 一律不计入 writer 越权）；禁止薄
``__getattr__`` 代理（isinstance 为假＝TypeError）或让产品放宽类型迎合测试。

协议（Luna/9b5 工具合同，字段名不变；slim logger，非 165KB suite 复制）：
真实产品首次导入后一次性 stdout flush ``S1C_RUNTIME_SNAPSHOT_JSON=<ASCII
JSON>``：``S1C_SOURCE_MANIFEST_INPUT`` 设置时 ``files``＝实测来源清单；未设
置＝``files`` 空（不做全清单快照、收集期零 IO）；``modules`` 恒按实际已加载
输出 coscreen.db / coscreen.fulltext / coscreen.workspace_undo /
coscreen.research_trace / coscreen.coding / coscreen.coding_evidence /
coscreen.coding_consensus + ``__test__`` 八项（实际 ``__file__``＋实测
sha256）。快照成功后仅一次注册 atexit 输出 ``S1C_EXIT_STATE_JSON=<ASCII
JSON>``（含 default_data{cwd_data,box_data} 实测存在性，不控制退出码）。
本 suite 模块收尾另输出 ``S2A1_MODULE_POSTCLEANUP_JSON``＝实际清理状态
（scope/目录存在性；无伪造键）。

边界：本文件不导入 auth/main/API/team_screening_runtime；产品导入惰性
（setUpModule 执行期、安全 scope 内）；收集期零产品导入/零造库/零 env 改写；
显式 tearDownModule 驱动 ``unittest.doModuleCleanups``（pytest 不代跑
addModuleCleanup 队列——R8 缺口不复犯），setUp 任一步失败立即收尾已登记
资源后原样重抛；只删除自建目录，绝不删除借用根（pytest 会话根 / bare
scope 根）；不 reset helper、不清 sys.modules；真实 data/8613 不触碰。
"""
from __future__ import annotations

import atexit
import contextlib
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import uuid

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TESTS_DIR = Path(__file__).resolve().parent
for _p in (str(_REPO_ROOT), str(_TESTS_DIR)):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

TASK = "s2a1-borrowed-writer-task"
ACTOR = 4242

# ---------------------------------------------------------------------------
# Luna/9b5 交接协议（slim logger；字段名与既有 S1C 合同一致）
# ---------------------------------------------------------------------------

_PROTOCOL_ENV_KEYS = (
    "COBOOKSHELF_DATA_DIR", "TEMP", "TMP", "TMPDIR", "RF_R9_TMP", "RF_R9_TMP_ROOT",
)
_PROTOCOL_MODE_KEY = "REVIEWFLOW_MODE"  # 可另 MODE；null 合法

_SNAPSHOT_EMITTED = False
_EXIT_REGISTERED = False
_EXIT_EMITTED = False
_POSTCLEANUP_EMITTED = False
_MODULE_DIRS: list[str] = []      # 本模块自建目录（创建即登记；只删这些）
_SCOPE_KIND: str | None = None    # "bare" | "pytest" | None（实际）
_SCOPE_LEFT = False              # actual __exit__ return, never an owner-state substitute
_SCOPE_CM = None
_SCOPE_GENERATION = None
_SCOPE_ROOT: str | None = None
_SCOPE_OWNED = False
_TEMPLATE: dict = {}
_PRODUCT: dict | None = None


def _sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _emit_runtime_snapshot() -> None:
    """真实产品导入完成后一次性快照（幂等哨兵）。``files``＝``S1C_SOURCE_MANIFEST_INPUT``
    设置时的实测来源清单（相对 .py 数组）；未设置＝空 dict（不做全清单快照）。
    ``modules``＝本 suite 实际加载的七个产品模块＋``__test__`` 实测 path/hash。"""
    global _SNAPSHOT_EMITTED
    if _SNAPSHOT_EMITTED:
        return
    _SNAPSHOT_EMITTED = True
    files: dict = {}
    manifest_input = os.environ.get("S1C_SOURCE_MANIFEST_INPUT") or ""
    if manifest_input.strip():
        try:
            entries = json.loads(Path(manifest_input).read_text(encoding="utf-8"))
            for rel in entries:
                target = (_REPO_ROOT / str(rel)).resolve()
                files[str(rel)] = _sha256_file(target)
        except (OSError, ValueError) as exc:  # 输入不可读＝如实报错不伪快照
            print("S1C_RUNTIME_SNAPSHOT_ERROR", repr(exc), flush=True)
            files = {}
    modules: dict = {}
    for import_name in (
        "coscreen.db", "coscreen.fulltext",
        "coscreen.workspace_undo", "coscreen.research_trace",
        "coscreen.coding", "coscreen.coding_evidence", "coscreen.coding_consensus",
    ):
        module = sys.modules.get(import_name)
        if module is not None and getattr(module, "__file__", None):
            resolved = Path(module.__file__).resolve()
            modules[import_name] = {"file": str(resolved), "sha256": _sha256_file(resolved)}
    test_file = Path(__file__).resolve()
    modules["__test__"] = {"file": str(test_file), "sha256": _sha256_file(test_file)}
    env = {key: os.environ.get(key) for key in _PROTOCOL_ENV_KEYS}
    env[_PROTOCOL_MODE_KEY] = os.environ.get(_PROTOCOL_MODE_KEY)
    payload = {
        "version": 1,
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "prefix": sys.prefix,
        "base_prefix": sys.base_prefix,
        "sample_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "monotonic_ns": time.monotonic_ns(),
        "cwd": os.getcwd(),
        "python": sys.executable,
        "flags": {"isolated": bool(sys.flags.isolated),
                  "dont_write_bytecode": bool(sys.flags.dont_write_bytecode)},
        "env": env,
        "files": files,
        "modules": modules,
    }
    print("S1C_RUNTIME_SNAPSHOT_JSON=" + json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")), flush=True)
    global _EXIT_REGISTERED
    if not _EXIT_REGISTERED:
        atexit.register(_emit_exit_state)
        _EXIT_REGISTERED = True


def _emit_exit_state() -> None:
    """进程退出末态（快照成功后仅一次注册；不拦异常/不改退出码）。"""
    global _EXIT_EMITTED
    if _EXIT_EMITTED:
        return
    _EXIT_EMITTED = True
    env = {key: os.environ.get(key) for key in _PROTOCOL_ENV_KEYS}
    env[_PROTOCOL_MODE_KEY] = os.environ.get(_PROTOCOL_MODE_KEY)
    payload = {
        "version": 1,
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "prefix": sys.prefix,
        "sample_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "monotonic_ns": time.monotonic_ns(),
        "cwd": os.getcwd(),
        "flags": {"isolated": bool(sys.flags.isolated),
                  "dont_write_bytecode": bool(sys.flags.dont_write_bytecode)},
        "env": env,
        "default_data": {
            "cwd_data": (Path.cwd() / "data").exists(),
            "box_data": (_REPO_ROOT / "data").exists(),
        },
    }
    print("S1C_EXIT_STATE_JSON=" + json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")), flush=True)


def _emit_postcleanup(phase: str, queue_drained: bool) -> None:
    """Sample actual owned-directory/cache/owner state after the cleanup queue."""
    dirs = {d: Path(d).exists() for d in _MODULE_DIRS}
    owner_status = getattr(_SCOPE_GENERATION, "status", None)
    root_exists = Path(_SCOPE_ROOT).exists() if _SCOPE_ROOT else None
    root_ok = (root_exists == (not _SCOPE_OWNED)) if _SCOPE_ROOT else True
    owner_ok = (_SCOPE_LEFT and owner_status == "closed") if _SCOPE_GENERATION is not None else True
    clean = (queue_drained and not any(dirs.values()) and _PRODUCT is None
             and not _TEMPLATE and root_ok and owner_ok)
    payload = {
        "version": 1, "pid": os.getpid(), "ppid": os.getppid(), "prefix": sys.prefix,
        "module": "build.tests.test_s2a1_borrowed_writer", "phase": phase,
        "scope_kind": _SCOPE_KIND, "exit_returned": _SCOPE_LEFT,
        "owner_status": owner_status, "root": _SCOPE_ROOT,
        "root_owned": _SCOPE_OWNED, "root_exists": root_exists,
        "queue_drained": queue_drained, "teardown_done": clean,
        "product_imported": _PRODUCT is not None,
        "template_ready": bool(_TEMPLATE), "module_dirs": dirs,
    }
    print("S2A1_MODULE_POSTCLEANUP_JSON=" + json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")), flush=True)
    assert clean, payload


def _module_finish(phase: str) -> None:
    """pytest and unittest both drain the real queue; failed cleanup fails the suite."""
    global _POSTCLEANUP_EMITTED
    drained = False
    try:
        unittest.doModuleCleanups()
        drained = True
    finally:
        if not _POSTCLEANUP_EMITTED:
            _POSTCLEANUP_EMITTED = True
            _emit_postcleanup(phase, drained)


def tearDownModule() -> None:
    _module_finish("teardown")


def _import_product_modules() -> dict:
    """唯一产品导入入口（setUpModule 执行期、安全 scope 内调用）。"""
    from coscreen import db as db_mod, fulltext as fulltext_mod  # noqa: PLC0415
    from coscreen import research_trace as trace_mod, workspace_undo as undo_mod
    from coscreen import coding as coding_mod, coding_evidence as evidence_mod, coding_consensus as consensus_mod
    from coscreen.models import Article
    return {
        "coding_mod": coding_mod,
        "evidence_mod": evidence_mod,
        "consensus_mod": consensus_mod,
        "db_mod": db_mod,
        "fulltext_mod": fulltext_mod,
        "trace_mod": trace_mod,
        "undo_mod": undo_mod,
        "Article": Article,
        "TraceContext": trace_mod.TraceContext,
        "save_decision": db_mod.save_decision,
        "save_stage2": fulltext_mod.save_stage2,
        "undo": undo_mod.undo,
        "begin_save": undo_mod.begin_save,
        "begin_capture_transaction": trace_mod.begin_capture_transaction,
        "ensure_default_recording": trace_mod.ensure_default_recording,
        "update_settings": trace_mod.update_settings,
        "get_settings": trace_mod.get_settings,
        "STAGES": trace_mod.STAGES,
    }


def _build_template_bytes() -> bytes:
    """init_db＋K1 种入 → 库字节（执行期；目录创建即登记清理）。模板库**无**
    workspace_undo 表、**无** trace schema；含 init_db 安装的 format2 账本。"""
    d = tempfile.mkdtemp(prefix="rf_s2a1_tpl_")
    _own_directory(d, unittest.addModuleCleanup)
    p = Path(d) / "task" / "user.db"
    p.parent.mkdir(parents=True)
    _PRODUCT["db_mod"].init_db(str(p))
    _PRODUCT["db_mod"].upsert_articles(
        str(p), [_PRODUCT["Article"](zotero_key="K1", title="Study", doi="10.x/1")], [])
    return p.read_bytes()


def _leave_bare_scope() -> None:
    global _SCOPE_CM, _SCOPE_LEFT
    cm, _SCOPE_CM = _SCOPE_CM, None
    if cm is not None:
        cm.__exit__(None, None, None)
        _SCOPE_LEFT = True  # owner_status is separately sampled; PENDING is not CLOSED


def setUpModule() -> None:
    """Execution-only setup, with immediate cleanup registration and failure unwind."""
    global _PRODUCT, _SCOPE_KIND, _SCOPE_CM, _SCOPE_GENERATION, _SCOPE_ROOT, _SCOPE_OWNED
    complete = False
    try:
        import _bare_env
        if "pytest" in sys.modules:
            _SCOPE_KIND = "pytest"
            _SCOPE_ROOT = os.environ.get("COBOOKSHELF_DATA_DIR")
        else:
            _SCOPE_KIND = "bare"
            _SCOPE_CM = _bare_env.bare_scope("rf_s2a1_bare_")
            _SCOPE_GENERATION = _SCOPE_CM.__enter__()
            unittest.addModuleCleanup(_leave_bare_scope)
            _SCOPE_ROOT = str(_SCOPE_GENERATION.root)
            _SCOPE_OWNED = _SCOPE_GENERATION.owned_root is not None
        _bare_env.ensure_safe_for_execution()
        _PRODUCT = _import_product_modules()
        unittest.addModuleCleanup(_forget_product)  # before any fallible snapshot/build
        _emit_runtime_snapshot()
        _TEMPLATE["bytes"] = _build_template_bytes()
        complete = True
    finally:
        if not complete:
            _module_finish("setup_abort")


def _forget_product() -> None:
    global _PRODUCT
    _PRODUCT = None
    _TEMPLATE.clear()


def _remove_owned_directory(directory: str) -> None:
    """Only the fresh mkdtemp directory is eligible; deletion errors propagate."""
    shutil.rmtree(directory)
    assert not Path(directory).exists(), directory


def _own_directory(directory: str, register) -> None:
    _MODULE_DIRS.append(directory)
    register(_remove_owned_directory, directory)


class _OuterRollbackInjected(Exception):
    """Only this exact test-owned instance is caught; product errors propagate."""


# ---------------------------------------------------------------------------
# 观测：真实 sqlite3.Connection 子类（factory 创建；isinstance 恒真）
# ---------------------------------------------------------------------------

_TXN_OWNERSHIP_HEADS = ("begin", "commit", "savepoint", "release", "rollback", "end")
_DDL_HEADS = ("create", "alter", "drop", "attach", "detach", "vacuum", "reindex")
_READONLY_PRAGMAS = {
    "table_info", "table_list", "index_info", "index_list", "index_xinfo",
    "foreign_key_list", "foreign_key_check", "database_list", "schema_version",
}


class RecordedConnection(sqlite3.Connection):
    """真实连接子类（经 ``sqlite3.connect(path, factory=…)`` 创建）：isinstance
    恒真（满足设计 §2.1 类型合同），记录 writer 范围的语句与事务/生命周期调用；
    测试自身操作经 writer 增量切片排除，不计越权。真 SQL 照常执行。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records: list[tuple[str, str]] = []

    def _rec(self, kind: str, detail: str = "") -> None:
        self.records.append((kind, detail))

    def execute(self, sql, *a, **k):
        self._rec("execute", str(sql))
        return super().execute(sql, *a, **k)

    def executemany(self, sql, *a, **k):
        self._rec("executemany", str(sql))
        return super().executemany(sql, *a, **k)

    def executescript(self, *a, **k):
        self._rec("executescript", str(a[0]) if a else "")
        return super().executescript(*a, **k)

    def commit(self):
        self._rec("commit")
        return super().commit()

    def rollback(self):
        self._rec("rollback")
        return super().rollback()

    def close(self):
        self._rec("close")
        return super().close()

    def __enter__(self):
        self._rec("enter")
        return super().__enter__()

    def __exit__(self, *exc_info):
        self._rec("exit")
        return super().__exit__(*exc_info)


def _sql_violations(records) -> list:
    """writer 增量内禁项：事务所有权（BEGIN/COMMIT/SAVEPOINT/RELEASE/ROLLBACK/
    END）、DDL（CREATE/ALTER/DROP/ATTACH/DETACH/VACUUM/REINDEX）、executescript
    （隐式提交）、生命周期（commit/rollback/close/enter/exit）、写型 PRAGMA。
    只读探测 PRAGMA（table_info 等合同允许的观测面）放行。"""
    bad = []
    for kind, detail in records:
        if kind in ("commit", "rollback", "close", "enter", "exit", "executescript"):
            bad.append((kind, detail))
            continue
        if kind not in ("execute", "executemany"):
            continue
        parts = detail.strip().split(None, 1)
        head = parts[0].lower() if parts else ""
        if head == "pragma":
            name = parts[1].split("(")[0].strip().lower() if len(parts) > 1 else ""
            if name not in _READONLY_PRAGMAS:
                bad.append((kind, detail))
        elif head in _TXN_OWNERSHIP_HEADS or head in _DDL_HEADS:
            bad.append((kind, detail))
    return bad


# ---------------------------------------------------------------------------
# 用例基类与 fixture 工厂
# ---------------------------------------------------------------------------

class S2A1ContractCase(unittest.TestCase):
    """模板库字节复制＋三态/legacy fixture 工厂＋记录型窗口＋观测助手。"""

    def setUp(self) -> None:
        self.assertIsNotNone(_PRODUCT, "setUpModule 未完成产品导入")
        fixture = tempfile.mkdtemp(prefix="rf_s2a1_case_")
        _own_directory(fixture, self.addCleanup)
        self.root = Path(fixture)

    # -- 访问助手 -------------------------------------------------------
    def P(self) -> dict:
        assert _PRODUCT is not None
        return _PRODUCT

    def ctx(self, request_id=None, source: str = "manual"):
        return self.P()["TraceContext"](task_id=TASK, actor_id=ACTOR,
                                        source=source, request_id=request_id)

    # -- 造库（全部真实产品路径） ---------------------------------------
    def new_db(self, kind: str = "raw", stage: str = "screen") -> Path:
        """raw=模板原样（无 journal 表/无 trace schema）；trace=＋默认 recording
        （仍无 journal 表）；prepared=＋prepare 事务（旧签名 begin_save＋
        begin_capture，screen+fulltext）＝journal 表列/trace schema/settings 全在；
        legacy=trace 基础上手工建旧八列 journal（缺 request_id/superseded）。"""
        directory = tempfile.mkdtemp(prefix=f"db_{kind}_", dir=str(self.root))
        _own_directory(directory, self.addCleanup)
        d = Path(directory)
        db = d / "user.db"
        db.write_bytes(_TEMPLATE["bytes"])
        p = self.P()
        if kind in ("trace", "prepared", "legacy"):
            with closing(p["db_mod"]._connect(str(db))) as conn:
                p["ensure_default_recording"](conn, self.ctx())
        if kind == "prepared":
            conn = p["db_mod"]._connect(str(db))
            try:
                conn.execute("BEGIN IMMEDIATE")
                p["begin_save"](conn)  # 旧签名（prepared 缺省 False）：journal 表/列
                p["begin_capture_transaction"](conn, self.ctx(), stage)
                p["begin_capture_transaction"](conn, self.ctx(), "fulltext")
                conn.commit()
            finally:
                conn.close()
        if kind == "legacy":
            with closing(p["db_mod"]._connect(str(db))) as conn, conn:
                conn.execute(
                    "CREATE TABLE workspace_undo ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, "
                    "actor_id TEXT NOT NULL, stage TEXT NOT NULL, zotero_key TEXT NOT NULL, "
                    "before_json TEXT NOT NULL, after_json TEXT NOT NULL, "
                    "undone INTEGER NOT NULL DEFAULT 0)")
                conn.execute(
                    "INSERT INTO workspace_undo(task_id,actor_id,stage,zotero_key,"
                    "before_json,after_json) VALUES(?,?,?,?,?,?)",
                    (TASK, str(ACTOR), "screen", "K1", "{}", "{}"))
        return db

    # -- 观测连接 -------------------------------------------------------
    def window(self, db: Path, deferred: bool = False) -> RecordedConnection:
        """记录型真连接窗口：外层开锁事务，提交/回滚权在测试。测试自身的
        BEGIN/回滚/观测 SELECT 均在 writer 增量切片之外。"""
        conn = sqlite3.connect(str(db), factory=RecordedConnection)
        self.addCleanup(conn.close)
        conn.execute("BEGIN" if deferred else "BEGIN IMMEDIATE")
        return conn

    def writer_delta(self, w: RecordedConnection):
        """标记观测起点；返回取增量的闭包——writer 调用范围外的测试自身
        prepare/BEGIN/rollback/观测 SQL 不计入越权判定。"""
        mark = len(w.records)

        def take():
            return w.records[mark:]

        return take

    @contextlib.contextmanager
    def connect_counter(self):
        """``_connect`` 计数 patch（coscreen.db 与 coscreen.fulltext 两处绑定）；
        计数包装器委托真实工厂。借用调用期必须双零。"""
        p = self.P()
        counts = {"db": 0, "fulltext": 0}
        real_db = p["db_mod"]._connect

        def wrap_db(*a, **k):
            counts["db"] += 1
            return real_db(*a, **k)

        cms = [mock.patch.object(p["db_mod"], "_connect", wrap_db)]
        if hasattr(p["fulltext_mod"], "_connect"):
            real_ft = p["fulltext_mod"]._connect

            def wrap_ft(*a, **k):
                counts["fulltext"] += 1
                return real_ft(*a, **k)

            cms.append(mock.patch.object(p["fulltext_mod"], "_connect", wrap_ft))
        with contextlib.ExitStack() as stack:
            for cm in cms:
                stack.enter_context(cm)
            yield counts

    # -- 断言助手 -------------------------------------------------------
    def q(self, db, sql: str, params=()):
        """独立全新读取连接（裸 sqlite3；不用产品 _connect——计数保持干净，
        也不把测试读计进任何 writer 观测）。"""
        conn = sqlite3.connect(str(db))
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def q_one(self, db, sql: str, params=()):
        """One row or None, materialized before the independent reader is closed."""
        rows = self.q(db, sql, params)
        return rows[0] if rows else None

    def table_exists(self, db, name: str) -> bool:
        return bool(self.q(db, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                           (name,)))

    def latest_entry(self, db, stage: str):
        rows = self.q(db, "SELECT id FROM workspace_undo WHERE task_id=? AND actor_id=? "
                          "AND stage=? AND undone=0 ORDER BY id DESC LIMIT 1",
                      (TASK, str(ACTOR), stage))
        return rows[0][0] if rows else None

    def ledger_snapshot(self, db):
        """format2 账本实测快照：(meta 行, revisions 全行集（含墓碑面）)。"""
        meta = self.q(db, "SELECT library_id, seeded_stage1, seeded_stage2, advances "
                          "FROM team_vote_revision_meta WHERE singleton=1")
        self.assertTrue(
            meta, "fixture 前提：init_db 已安装 team_vote_ledger（decisions/stage2 两表齐）")
        revs = self.q(db, "SELECT stage, item_key, revision FROM team_vote_revisions "
                          "ORDER BY stage, item_key")
        return (meta[0], tuple(revs))


class BorrowedRoutingTests(S2A1ContractCase):
    """C1-C3：借用路径全部语句落在 caller conn＋双连接未提交不可见。"""

    def test_c1_borrowed_save_decision_all_sql_on_caller_conn(self):
        p = self.P()
        db = self.new_db("prepared", stage="screen")
        baseline_events = self.q(db, "SELECT * FROM trace_events ORDER BY sequence")
        rid = str(uuid.uuid4())
        w = self.window(db)
        delta = self.writer_delta(w)
        with self.connect_counter() as counts:
            result = p["save_decision"](str(db), "K1", "include",
                                        trace_context=self.ctx(request_id=rid), conn=w)
        records = delta()
        self.assertIsNone(result)
        self.assertEqual(counts, {"db": 0, "fulltext": 0})
        self.assertTrue(w.in_transaction)
        self.assertEqual(_sql_violations(records), [])
        for table in ("decisions", "workspace_undo", "trace_events", "trace_write_operations"):
            if table == "decisions":
                sql, params = "SELECT 1 FROM decisions WHERE zotero_key=?", ("K1",)
            else:
                sql, params = f"SELECT 1 FROM {table} WHERE request_id=?", (rid,)
            self.assertIsNotNone(w.execute(sql, params).fetchone(), table)
            self.assertIsNone(self.q_one(db, sql, params), "uncommitted writer escaped: " + table)
        self.assertEqual(self.q(db, "SELECT * FROM trace_events ORDER BY sequence"), baseline_events)
        w.commit()
        self.assertIsNotNone(self.q_one(db, "SELECT 1 FROM decisions WHERE zotero_key='K1'"))
        for table in ("workspace_undo", "trace_events", "trace_write_operations"):
            self.assertEqual(self.q_one(db, f"SELECT COUNT(*) FROM {table} WHERE request_id=?", (rid,))[0], 1)
        self.assertEqual(self.q_one(db, "SELECT COUNT(*) FROM trace_events")[0], len(baseline_events) + 1)

    def test_c2_borrowed_save_stage2_all_sql_on_caller_conn(self):
        p = self.P()
        db = self.new_db("prepared", stage="fulltext")
        w = self.window(db)
        delta = self.writer_delta(w)
        with self.connect_counter() as counts:
            result = p["save_stage2"](str(db), "K1", "include",
                                      trace_context=self.ctx(request_id=str(uuid.uuid4())),
                                      conn=w)
        self.assertIsNone(result)
        self.assertEqual(counts, {"db": 0, "fulltext": 0})
        self.assertTrue(w.in_transaction)
        self.assertEqual(_sql_violations(delta()), [])
        self.assertIsNotNone(w.execute("SELECT 1 FROM stage2_decisions WHERE zotero_key='K1'").fetchone())
        self.assertIsNotNone(w.execute("SELECT 1 FROM workspace_undo WHERE stage='fulltext'").fetchone())
        self.assertIsNotNone(w.execute("SELECT 1 FROM trace_events WHERE stage='fulltext'").fetchone())
        self.assertIsNotNone(w.execute("SELECT 1 FROM trace_write_operations WHERE stage='fulltext'").fetchone())
        for sql in ("SELECT 1 FROM stage2_decisions WHERE zotero_key='K1'",
                    "SELECT 1 FROM workspace_undo WHERE stage='fulltext'",
                    "SELECT 1 FROM trace_events WHERE stage='fulltext'",
                    "SELECT 1 FROM trace_write_operations WHERE stage='fulltext'"):
            self.assertIsNone(self.q_one(db, sql), f"commit 前他连接可见＝逃逸：{sql}")
        w.commit()
        self.assertIsNotNone(self.q_one(db, "SELECT 1 FROM stage2_decisions WHERE zotero_key='K1'"))
        self.assertEqual(self.q(db, "SELECT COUNT(*) FROM workspace_undo")[0][0], 1)
        self.assertEqual(self.q(db, "SELECT COUNT(*) FROM trace_write_operations")[0][0], 1)

    def test_c3_borrowed_undo_reuses_outer_locked_transaction(self):
        p = self.P()
        db = self.new_db("prepared", stage="screen")
        p["save_decision"](str(db), "K1", "include", trace_context=self.ctx())  # committed
        entry = self.latest_entry(db, "screen")
        self.assertIsNotNone(entry)
        w = self.window(db)
        delta = self.writer_delta(w)
        result = p["undo"](str(db), self.ctx(source="undo"), "screen", entry, conn=w)
        records = delta()
        self.assertIsInstance(result, dict)
        self.assertEqual(set(result), {"undone", "zotero_key", "latest", "access_context", "decision"},
                         "undo 返回 dict 形状不变")
        self.assertTrue(result["undone"])
        self.assertTrue(w.in_transaction, "借用 undo 不得提交（return ≠ commit）")
        self.assertEqual(_sql_violations(records), [], "借用 undo 再 BEGIN/close 即违反所有权")
        self.assertIsNone(w.execute("SELECT 1 FROM decisions WHERE zotero_key='K1'").fetchone())
        self.assertIsNotNone(w.execute("SELECT 1 FROM workspace_undo WHERE id=? AND undone=1",
                                       (entry,)).fetchone())
        self.assertIsNotNone(w.execute("SELECT 1 FROM trace_events WHERE source='undo'").fetchone())
        self.assertEqual(self.q_one(db, "SELECT decision FROM decisions WHERE zotero_key='K1'"),
                         ("include",), "other readers retain the committed pre-undo state")
        w.commit()
        self.assertIsNone(self.q_one(db, "SELECT 1 FROM decisions WHERE zotero_key='K1'"))
        self.assertEqual(self.q(db, "SELECT undone FROM workspace_undo WHERE id=?", (entry,))[0][0], 1)
        self.assertIsNotNone(self.q_one(db, "SELECT 1 FROM trace_events WHERE source='undo'"))


class BorrowedFailClosedTests(S2A1ContractCase):
    """C4-C5：借用路径零 DDL；缺表/缺列 fail-closed 原样上抛。"""

    def test_c4_borrowed_save_failclosed_missing_journal_no_ddl(self):
        p = self.P()
        db = self.new_db("trace")  # committed prepare events must survive rollback
        baseline_events = self.q(db, "SELECT * FROM trace_events ORDER BY sequence")
        self.assertTrue(baseline_events, "prepare must create real baseline events")
        self.assertFalse(self.table_exists(db, "workspace_undo"))
        w = self.window(db)
        delta = self.writer_delta(w)
        with self.assertRaises(sqlite3.OperationalError):
            p["save_decision"](str(db), "K1", "include", trace_context=self.ctx(), conn=w)
        self.assertEqual(_sql_violations(delta()), [], "fail-closed 同样零 DDL/零事务所有权")
        self.assertFalse(self.table_exists(db, "workspace_undo"), "借用路径不得 CREATE journal 表")
        self.assertTrue(w.in_transaction, "语句失败不自动中止事务——外层回滚合法且必须")
        w.rollback()
        self.assertIsNone(self.q_one(db, "SELECT 1 FROM decisions WHERE zotero_key='K1'"),
                          "外层 rollback 必须撤尽窗口内业务写")
        self.assertEqual(self.q(db, "SELECT * FROM trace_events ORDER BY sequence"), baseline_events)
        self.assertEqual(self.q(db, "SELECT COUNT(*) FROM trace_write_operations")[0][0], 0)

    def test_c5_borrowed_undo_no_hidden_alter_on_legacy_journal(self):
        p = self.P()
        # 负半：旧八列 journal（缺 request_id/superseded）→ 借用 undo fail-closed 零 ALTER
        db = self.new_db("legacy")
        cols_before = self.q(db, "SELECT name FROM pragma_table_info('workspace_undo') ORDER BY cid")
        self.assertNotIn("superseded", {r[0] for r in cols_before})
        w = self.window(db)
        delta = self.writer_delta(w)
        with self.assertRaises(sqlite3.OperationalError):
            p["undo"](str(db), self.ctx(source="undo"), "screen", 1, conn=w)
        self.assertEqual(_sql_violations(delta()), [])
        cols_after = self.q(db, "SELECT name FROM pragma_table_info('workspace_undo') ORDER BY cid")
        self.assertEqual(cols_before, cols_after, "借用 undo 不得 ALTER 补列（隐藏 DDL 禁止）")
        self.assertTrue(w.in_transaction)
        w.rollback()
        self.assertEqual(self.q(db, "SELECT COUNT(*) FROM workspace_undo")[0][0], 1)
        self.assertEqual(self.q(db, "SELECT undone FROM workspace_undo WHERE id=1")[0][0], 0)
        # 正控：prepare 路径补列（旧签名 begin_save）＋真实 save 提交 → 借用 undo 通过
        db2 = self.new_db("legacy")
        conn = p["db_mod"]._connect(str(db2))
        try:
            p["begin_save"](conn)  # 旧签名：BEGIN IMMEDIATE＋CREATE IF NOT EXISTS＋ALTER 补列
            conn.commit()
        finally:
            conn.close()
        p["save_decision"](str(db2), "K1", "include", trace_context=self.ctx())
        entry = self.latest_entry(db2, "screen")
        self.assertIsNotNone(entry)
        w2 = self.window(db2)
        result = p["undo"](str(db2), self.ctx(source="undo"), "screen", entry, conn=w2)
        self.assertTrue(result["undone"], "prepare 已保证列在 → 借用 undo 应通过")
        self.assertTrue(w2.in_transaction)
        w2.commit()
        self.assertIsNone(self.q_one(db2, "SELECT 1 FROM decisions WHERE zotero_key='K1'"))
        self.assertEqual(self.q(db2, "SELECT undone FROM workspace_undo WHERE id=?", (entry,))[0][0], 1)


class OuterRollbackTests(S2A1ContractCase):
    """C6-C7：外层 rollback 同撤 business/ledger/undo/trace/request；library_id 保持。"""

    def test_c6_outer_rollback_undoes_business_ledger_undo_trace_request(self):
        p = self.P()
        db = self.new_db("prepared", stage="screen")
        p["save_decision"](str(db), "K1", "include", trace_context=self.ctx())
        baseline = {table: self.q(db, f"SELECT * FROM {table} ORDER BY rowid")
                    for table in ("decisions", "workspace_undo", "trace_events", "trace_write_operations")}
        ledger_before = self.ledger_snapshot(db)
        rid = str(uuid.uuid4())
        injected = _OuterRollbackInjected("C6 outer rollback")
        caught = False
        w = self.window(db)
        delta = self.writer_delta(w)
        try:
            result = p["save_decision"](str(db), "K1", "exclude",
                                        trace_context=self.ctx(request_id=rid), conn=w)
            records = delta()  # exclude every test observation and final rollback
            self.assertIsNone(result)
            self.assertEqual(_sql_violations(records), [])
            self.assertTrue(w.in_transaction)
            self.assertEqual(w.execute("SELECT decision FROM decisions WHERE zotero_key='K1'").fetchone()[0], "exclude")
            self.assertIsNotNone(w.execute("SELECT 1 FROM trace_write_operations WHERE request_id=?", (rid,)).fetchone())
            self.assertIsNotNone(w.execute("SELECT 1 FROM workspace_undo WHERE request_id=?", (rid,)).fetchone())
            self.assertIsNotNone(w.execute("SELECT 1 FROM trace_events WHERE request_id=?", (rid,)).fetchone())
            self.assertIsNone(self.q_one(db, "SELECT 1 FROM trace_write_operations WHERE request_id=?", (rid,)))
            rev = w.execute("SELECT revision FROM team_vote_revisions WHERE stage='stage1' AND item_key='K1'").fetchone()[0]
            before_rev = next(r[2] for r in ledger_before[1] if r[:2] == ("stage1", "K1"))
            adv = w.execute("SELECT advances FROM team_vote_revision_meta WHERE singleton=1").fetchone()[0]
            self.assertEqual(rev, before_rev + 2, "same-key UPDATE accounts for both NEW and OLD")
            self.assertEqual(adv, ledger_before[0][3] + 2)
            raise injected
        except _OuterRollbackInjected as exc:
            self.assertIs(exc, injected)
            caught = True
        finally:
            w.rollback()
        self.assertTrue(caught, "writer and all window checks must precede injection")
        for table, rows in baseline.items():
            self.assertEqual(self.q(db, f"SELECT * FROM {table} ORDER BY rowid"), rows, table)
        self.assertEqual(self.ledger_snapshot(db), ledger_before)

    def test_c7_outer_rollback_undoes_borrowed_undo_restoration(self):
        p = self.P()
        db = self.new_db("prepared", stage="screen")
        rid = str(uuid.uuid4())
        p["save_decision"](str(db), "K1", "include", trace_context=self.ctx(request_id=rid))
        entry = self.latest_entry(db, "screen")
        self.assertIsNotNone(entry)
        baseline = {table: self.q(db, f"SELECT * FROM {table} ORDER BY rowid")
                    for table in ("decisions", "workspace_undo", "trace_events", "trace_write_operations")}
        self.assertIsNotNone(self.q_one(db, "SELECT 1 FROM trace_write_operations WHERE request_id=?", (rid,)))
        ledger_before = self.ledger_snapshot(db)
        before_rev = next(r[2] for r in ledger_before[1] if r[:2] == ("stage1", "K1"))
        injected = _OuterRollbackInjected("C7 outer rollback")
        caught = False
        w = self.window(db)
        delta = self.writer_delta(w)
        try:
            result = p["undo"](str(db), self.ctx(source="undo"), "screen", entry, conn=w)
            records = delta()
            self.assertTrue(result["undone"])
            self.assertEqual(_sql_violations(records), [])
            self.assertTrue(w.in_transaction)
            self.assertIsNone(w.execute("SELECT 1 FROM decisions WHERE zotero_key='K1'").fetchone())
            self.assertEqual(w.execute("SELECT undone FROM workspace_undo WHERE id=?", (entry,)).fetchone()[0], 1)
            self.assertIsNone(w.execute("SELECT 1 FROM trace_write_operations WHERE request_id=?", (rid,)).fetchone())
            self.assertIsNotNone(w.execute("SELECT 1 FROM trace_events WHERE source='undo'").fetchone())
            self.assertIsNotNone(self.q_one(db, "SELECT 1 FROM trace_write_operations WHERE request_id=?", (rid,)))
            self.assertEqual(w.execute("SELECT revision FROM team_vote_revisions WHERE stage='stage1' AND item_key='K1'").fetchone()[0], before_rev + 1)
            self.assertEqual(w.execute("SELECT advances FROM team_vote_revision_meta WHERE singleton=1").fetchone()[0], ledger_before[0][3] + 1)
            raise injected
        except _OuterRollbackInjected as exc:
            self.assertIs(exc, injected)
            caught = True
        finally:
            w.rollback()
        self.assertTrue(caught)
        for table, rows in baseline.items():
            self.assertEqual(self.q(db, f"SELECT * FROM {table} ORDER BY rowid"), rows, table)
        self.assertEqual(self.ledger_snapshot(db), ledger_before)


class ReplayNoCommitTests(S2A1ContractCase):
    """C8：replay 返回 None 不代表提交；无重复行。"""

    def test_c8_borrowed_replay_return_does_not_commit(self):
        p = self.P()
        db = self.new_db("prepared", stage="screen")
        rid = str(uuid.uuid4())
        p["save_decision"](str(db), "K1", "include", exclusion_reason="r", notes="n",
                           trace_context=self.ctx(request_id=rid))
        row_before = self.q(db, "SELECT stage,operation,subject_id,payload_sha256,result_json "
                                "FROM trace_write_operations WHERE request_id=?", (rid,))
        journal_before = self.q(db, "SELECT COUNT(*) FROM workspace_undo")[0][0]
        self.assertEqual(len(row_before), 1)
        # 甲：replay → None；无写；不提交
        w = self.window(db)
        delta = self.writer_delta(w)
        result = p["save_decision"](str(db), "K1", "include", exclusion_reason="r", notes="n",
                                    trace_context=self.ctx(request_id=rid), conn=w)
        self.assertIsNone(result, "replay 与正常 return 语义一致：均不代表提交")
        self.assertTrue(w.in_transaction)
        writes = [r for r in delta()
                  if r[0] in ("execute", "executemany")
                  and r[1].strip().lower().startswith(("insert", "update", "delete"))]
        self.assertEqual(writes, [], "replay 不得重写 business/journal/摘要")
        w.rollback()
        self.assertEqual(self.q(db, "SELECT stage,operation,subject_id,payload_sha256,result_json "
                                    "FROM trace_write_operations WHERE request_id=?", (rid,)),
                         row_before, "摘要行未被改写")
        self.assertEqual(self.q(db, "SELECT COUNT(*) FROM workspace_undo")[0][0], journal_before)
        self.assertEqual(self.q(db, "SELECT decision FROM decisions WHERE zotero_key='K1'")[0][0],
                         "include")
        # 乙：replay 窗口提交 → 亦无重复
        w2 = self.window(db)
        p["save_decision"](str(db), "K1", "include", exclusion_reason="r", notes="n",
                           trace_context=self.ctx(request_id=rid), conn=w2)
        w2.commit()
        self.assertEqual(self.q(db, "SELECT COUNT(*) FROM trace_write_operations WHERE request_id=?",
                                (rid,))[0][0], 1, "replay 不产生重复摘要行")
        self.assertEqual(self.q(db, "SELECT COUNT(*) FROM workspace_undo")[0][0], journal_before)
        self.assertEqual(self.q(db, "SELECT decision FROM decisions WHERE zotero_key='K1'")[0][0],
                         "include")


class StandaloneCompatTests(S2A1ContractCase):
    """C9-C10：缺省路径旧语义正控（旧/新产品都应 PASS）。"""

    def test_c9_standalone_save_default_commit_and_zero_side_effect(self):
        p = self.P()
        db = self.new_db("prepared", stage="screen")
        p["save_decision"](str(db), "K1", "include", exclusion_reason="e", tags="a,b",
                           trace_context=self.ctx())
        screened_first = self.q(db, "SELECT screened_at FROM decisions WHERE zotero_key='K1'")[0][0]
        p["save_decision"](str(db), "K1", "maybe", notes="n2",
                           trace_context=self.ctx())  # tags=None → 保留原值
        row = self.q(db, "SELECT decision, exclusion_reason, notes, tags, screened_at "
                         "FROM decisions WHERE zotero_key='K1'")[0]
        self.assertEqual((row[0], row[1], row[2], row[3]), ("maybe", "", "n2", "a,b"),
                         "缺省 upsert 语义不变：decision 更新/tags 保留/screened_at 首筛保留")
        self.assertEqual(row[4], screened_first)
        self.assertIsNotNone(self.q_one(db, "SELECT 1 FROM workspace_undo WHERE stage='screen'"))
        self.assertIsNotNone(self.q_one(db, "SELECT 1 FROM trace_events WHERE stage='screen'"))
        # 校验先于连接创建：非法 decision 零副作用（不建库文件）
        missing = self.root / "ghost" / "user.db"
        self.assertFalse(missing.exists())
        with self.assertRaises(ValueError):
            p["save_decision"](str(missing), "K1", "bogus-decision")
        self.assertFalse(missing.exists(), "非法 decision 不得创建库文件")

    def test_c10_standalone_undo_old_semantics_positive(self):
        p = self.P()
        db = self.new_db("prepared", stage="screen")
        p["save_decision"](str(db), "K1", "include", trace_context=self.ctx())
        entry = self.latest_entry(db, "screen")
        result = p["undo"](str(db), self.ctx(source="undo"), "screen", entry)
        self.assertEqual(set(result), {"undone", "zotero_key", "latest", "access_context", "decision"})
        self.assertTrue(result["undone"], "standalone undo 独立 BEGIN IMMEDIATE 旧语义不变")
        self.assertIsNone(self.q_one(db, "SELECT 1 FROM decisions WHERE zotero_key='K1'"))
        self.assertEqual(self.q(db, "SELECT undone FROM workspace_undo WHERE id=?", (entry,))[0][0], 1)
        # 旧签名 begin_save 直调路径＋手建条目（复制真实捕获面）standalone undo 同样通过
        db2 = self.new_db("prepared", stage="screen")
        p["save_decision"](str(db2), "K1", "include", trace_context=self.ctx())
        real_before, real_after = self.q_one(
            db2, "SELECT before_json,after_json FROM workspace_undo ORDER BY id DESC LIMIT 1")
        self.assertIsNone(json.loads(real_before)["record"], "first save has an absent before row")
        with closing(p["db_mod"]._connect(str(db2))) as conn, conn:
            p["begin_save"](conn)  # 旧签名直调（prepared 缺省）＝旧消费口径
            conn.execute("INSERT INTO workspace_undo(task_id,actor_id,stage,zotero_key,"
                         "before_json,after_json,request_id) VALUES(?,?,?,?,?,?,NULL)",
                         (TASK, str(ACTOR), "screen", "K1", real_before, real_after))
            crafted = conn.execute(
                "SELECT id FROM workspace_undo ORDER BY id DESC LIMIT 1").fetchone()[0]
        result2 = p["undo"](str(db2), self.ctx(source="undo"), "screen", crafted)
        self.assertTrue(result2["undone"], "手建（真实捕获面）条目 standalone undo 旧语义通过")
        self.assertIsNone(self.q_one(db2, "SELECT 1 FROM decisions WHERE zotero_key='K1'"))


    def test_c16_standalone_save_samples_time_before_connect(self):
        p = self.P()
        for module_key, writer_key, table in (
            ("db_mod", "save_decision", "decisions"),
            ("fulltext_mod", "save_stage2", "stage2_decisions"),
        ):
            with self.subTest(writer=writer_key):
                db = self.new_db("raw")
                module = p[module_key]
                real_connect = module._connect
                screened_first = None
                for index, decision in enumerate(("include", "exclude")):
                    early = f"2026-11-15T01:00:0{index}+00:00"
                    late = f"2026-11-15T02:00:0{index}+00:00"
                    clock = {"value": early}
                    calls = []

                    def wrapped_connect(*args, **kwargs):
                        calls.append((args, kwargs))
                        clock["value"] = late  # simulate connection/migration delay, no sleep
                        return real_connect(*args, **kwargs)

                    with mock.patch.object(module, "_now", side_effect=lambda: clock["value"]), \
                            mock.patch.object(module, "_connect", wrapped_connect):
                        p[writer_key](str(db), "K1", decision)
                    self.assertEqual(len(calls), 1, "standalone uses the wrapped real connector")
                    self.assertEqual(clock["value"], late, "connection really advanced the controlled clock")
                    row = self.q_one(db, f"SELECT screened_at,updated_at FROM {table} WHERE zotero_key='K1'")
                    if screened_first is None:
                        screened_first = early
                    self.assertEqual(row, (screened_first, early),
                                     "timestamps sample before connect; updates retain first screened_at")


class PreparedHelperGateTests(S2A1ContractCase):
    """C11-C13：prepared=True 合同（无事务拒 / 缺 schema fail-closed / setting 现读）。"""

    def test_c11_prepared_helpers_require_open_transaction(self):
        p = self.P()
        db = self.new_db("prepared")
        w = self.window(db)
        w.rollback()  # 测试自收：真实无事务态
        delta = self.writer_delta(w)
        with self.assertRaisesRegex(RuntimeError, "prepared=True requires an open transaction"):
            p["begin_save"](w, prepared=True)
        with self.assertRaisesRegex(RuntimeError, "prepared=True requires an open transaction"):
            p["begin_capture_transaction"](w, self.ctx(), "screen", prepared=True)
        stmts = [r for r in delta()
                 if r[0] in ("execute", "executemany", "executescript")]
        self.assertEqual(stmts, [], "无事务拒绝必须在任何 SQL 前（不得暗 BEGIN）")
        self.assertFalse(w.in_transaction)

    def test_c12_prepared_begin_capture_failclosed_missing_trace_schema(self):
        p = self.P()
        db = self.new_db("raw")  # 模板库：无 trace_settings
        self.assertFalse(self.table_exists(db, "trace_settings"))
        w = self.window(db)
        delta = self.writer_delta(w)
        with self.assertRaises(sqlite3.OperationalError):
            p["begin_capture_transaction"](w, self.ctx(), "screen", prepared=True)
        self.assertEqual(_sql_violations(delta()), [], "缺 schema fail-closed：零 ensure_schema/零 DDL")
        self.assertFalse(self.table_exists(db, "trace_settings"),
                         "不得把 is_recording 的缺表折叠 False 冒称已读当前 setting（§8）")
        self.assertFalse(self.table_exists(db, "trace_store"), "不建 trace schema、不写默认 settings")
        self.assertTrue(w.in_transaction)
        w.rollback()

    def test_c13_prepared_begin_capture_reads_current_setting(self):
        p = self.P()
        # 甲：prepare 时 recording=ON → 翻转 OFF → prepared 必返 False（现读非快照）
        db_a = self.new_db("prepared", stage="screen")
        self._flip_setting(db_a, "off", "s2a1-flipA-off")
        w_a = self.window(db_a)
        delta_a = self.writer_delta(w_a)
        self.assertFalse(
            p["begin_capture_transaction"](w_a, self.ctx(), "screen", prepared=True),
            "必须在调用时刻现读 setting（prepare 时快照是 ON，现值 OFF）")
        self.assertTrue(any("trace_settings" in d for k, d in delta_a() if k == "execute"),
                        "setting 读取发生在 caller conn（记录面可证）")
        self.assertTrue(w_a.in_transaction)
        w_a.rollback()
        # 乙：prepare 后翻 OFF 再翻 recording → prepared 必返 True
        db_b = self.new_db("prepared", stage="screen")
        self._flip_setting(db_b, "off", "s2a1-flipB-off")
        self._flip_setting(db_b, "recording", "s2a1-flipB-on")
        w_b = self.window(db_b)
        self.assertTrue(p["begin_capture_transaction"](w_b, self.ctx(), "screen", prepared=True))
        self.assertTrue(w_b.in_transaction)
        w_b.rollback()

    def test_c12b_prepared_capture_rejects_each_missing_event_or_request_table(self):
        p = self.P()
        for missing in ("trace_events", "trace_write_operations"):
            with self.subTest(missing=missing):
                db = self.new_db("prepared")
                self.assertTrue(self.table_exists(db, "trace_settings"))
                for table in ("trace_events", "trace_write_operations"):
                    self.assertTrue(self.table_exists(db, table))
                with closing(sqlite3.connect(str(db))) as prepare, prepare:
                    prepare.execute(f"DROP TABLE {missing}")
                other = "trace_write_operations" if missing == "trace_events" else "trace_events"
                self.assertTrue(self.table_exists(db, other), "only one required table is removed")
                self.assertIsNotNone(self.q_one(db, "SELECT 1 FROM trace_settings WHERE task_id=? AND actor_id=?", (TASK, str(ACTOR))))
                w = self.window(db)
                delta = self.writer_delta(w)
                try:
                    with self.assertRaises(sqlite3.OperationalError):
                        p["begin_capture_transaction"](w, self.ctx(), "screen", prepared=True)
                    records = delta()
                    self.assertEqual(_sql_violations(records), [])
                    self.assertTrue(w.in_transaction)
                    self.assertIsNone(w.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (missing,)).fetchone())
                    self.assertFalse(self.table_exists(db, missing), "must fail closed without rebuilding")
                    self.assertTrue(self.table_exists(db, other))
                finally:
                    w.rollback()
                self.assertFalse(self.table_exists(db, missing))

    def _flip_setting(self, db: Path, status: str, operation_id: str) -> None:
        """经产品 API 在独立连接翻 settings（own-txn 提交；窗口开启前完成，
        规避 WAL 写者互斥）。"""
        p = self.P()
        conn = p["db_mod"]._connect(str(db))
        try:
            version = p["get_settings"](conn, TASK, str(ACTOR))["version"]
            p["update_settings"](conn, self.ctx(), stages=list(p["STAGES"]), status=status,
                                 operation_id=operation_id, expected_version=version)
        finally:
            conn.close()


class TransactionOwnershipTests(S2A1ContractCase):
    """C14-C15：任何 SQL 前核活动事务；类型/关闭连接零降级零 fallback。"""

    def test_c14_borrowed_requires_open_transaction_before_any_sql(self):
        p = self.P()
        db = self.new_db("prepared", stage="screen")
        calls = {
            "save_decision": lambda w: p["save_decision"](str(db), "K1", "include", conn=w),
            "save_stage2": lambda w: p["save_stage2"](str(db), "K1", "include", conn=w),
            "undo": lambda w: p["undo"](str(db), self.ctx(source="undo"), "screen", 1, conn=w),
        }
        for name, call in calls.items():
            w = self.window(db)
            w.rollback()  # 真实无活动事务
            delta = self.writer_delta(w)
            with self.connect_counter() as counts:
                with self.assertRaises(RuntimeError,
                                       msg=f"{name}: 无活动事务必须 RuntimeError（编程违约零 SQL）"):
                    call(w)
            stmts = [r for r in delta()
                     if r[0] in ("execute", "executemany", "executescript")]
            self.assertEqual(stmts, [], f"{name}: 任何 SQL 前拒（不得让 SQLite 隐式 BEGIN）")
            self.assertEqual(counts, {"db": 0, "fulltext": 0}, f"{name}: 零 _connect")
            self.assertFalse(w.in_transaction, f"{name}: 不得隐式开启事务")

    def test_c15_borrowed_conn_type_and_closed_conn_no_fallback(self):
        p = self.P()
        db = self.new_db("prepared", stage="screen")
        closed = p["db_mod"]._connect(str(db))
        closed.close()
        with self.connect_counter() as counts:
            with self.assertRaises(TypeError):
                p["save_decision"](str(db), "K1", "include",
                                   trace_context=self.ctx(), conn="user.db")
            with self.assertRaises(sqlite3.ProgrammingError):
                p["save_stage2"](str(db), "K1", "include",
                                 trace_context=self.ctx(), conn=closed)
        self.assertEqual(counts, {"db": 0, "fulltext": 0},
                         "非法/已关闭 conn 零降级零 fallback（不得自开连接续跑）")


# S2-A2: nine additional contract methods, using the original Recorder and lifecycle.
A2_CONSENSUS_PREPARE_NAME = "prepare_coding_consensus_storage"


def a2_modules():
    """Execution-only real imports; no product IO/import at fragment collection."""
    from coscreen import coding, coding_evidence, coding_consensus
    return {"coding": coding, "evidence": coding_evidence, "consensus": coding_consensus}


def a2_png(color=(20, 80, 160)):
    # Real validated image bytes, made only when a future test runs.
    import io
    from PIL import Image
    stream = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(stream, format="PNG")
    return stream.getvalue()


class A2ContractCase(S2A1ContractCase):
    def assert_a2_connection_gates(self, db, first):
        modules = a2_modules()
        coding, evidence, consensus = modules["coding"], modules["evidence"], modules["consensus"]
        fulltext = self.P()["fulltext_mod"]
        data = a2_png()
        calls = {
            "single": lambda w: coding.save_coding_value(db, first, "K1", "v", conn=w),
            "batch": lambda w: coding.save_coding_values_batch(db, "K1", {first: "v"}, trace_context=self.ctx(), conn=w),
            "form": lambda w: evidence.save_form(db, "K1", {first: "v"}, notes=None, evidence=None, trace_context=self.ctx(), conn=w),
            "upload": lambda w: evidence.stage_upload(db, TASK, ACTOR, "K1", first, data, conn=w),
            "highlight_add": lambda w: fulltext.add_highlight_rects(db, "K1", [], conn=w),
            "highlight_delete": lambda w: fulltext.delete_highlight(db, 1, conn=w),
            "rater": lambda w: consensus.append_rater_record(db, item_key="K1", field_key="f", rater_id=ACTOR, value="v", source=self.source(), conn=w),
            "consensus": lambda w: consensus.append_consensus_record(db, item_key="K1", field_key="f", reconciler_id=999, value="v", source=self.source(), rationale="reason", conn=w),
            "freeze": lambda w: consensus.freeze_consensus(db, name="n", frozen_by=999, consensus_ids=[1], conn=w),
        }
        # An old API rejecting conn= is API incompatibility, never a passing type gate.
        import inspect
        for writer in (coding.save_coding_value, coding.save_coding_values_batch,
                       evidence.save_form, evidence.stage_upload,
                       fulltext.add_highlight_rects, fulltext.delete_highlight,
                       consensus.append_rater_record, consensus.append_consensus_record,
                       consensus.freeze_consensus):
            parameters = inspect.signature(writer).parameters
            self.assertIn("conn", parameters, f"API_SIGNATURE_INCOMPATIBLE: {writer.__name__}")
            self.assertEqual(parameters["conn"].kind, inspect.Parameter.KEYWORD_ONLY)
        for name, call in calls.items():
            with self.subTest(writer=name, gate="inactive"):
                w = self.window(db)
                w.rollback()
                delta = self.writer_delta(w)
                with self.no_a2_reconnect(), self.assertRaises(RuntimeError):
                    call(w)
                self.assertEqual(delta(), [], "inactive native connection must fail before any SQL")
                self.assertFalse(w.in_transaction)
            with self.subTest(writer=name, gate="type"):
                with self.no_a2_reconnect(), self.assertRaises(TypeError):
                    call("not a native connection")
            with self.subTest(writer=name, gate="closed"):
                w = self.window(db)
                w.close()
                delta = self.writer_delta(w)
                with self.no_a2_reconnect(), self.assertRaises(sqlite3.ProgrammingError):
                    call(w)
                self.assertEqual(delta(), [])

    def research_fixture(self):
        db = self.new_db("prepared", stage="coding")
        modules = a2_modules()
        modules["evidence"].migrate(db)  # prepare BEFORE any recorded writer window
        coding = modules["coding"]
        first = coding.add_dimension(db, "A2 free text", "text", [])
        second = coding.add_dimension(db, "A2 second text", "text", [])
        return db, first, second

    def snapshot_tables(self, db, tables):
        return {table: self.q(db, f"SELECT * FROM {table} ORDER BY rowid") for table in tables}

    def assert_restored(self, db, before):
        for table, rows in before.items():
            self.assertEqual(self.q(db, f"SELECT * FROM {table} ORDER BY rowid"), rows, table)

    @contextlib.contextmanager
    def no_a2_reconnect(self):
        modules = a2_modules()
        # Every wrapper delegates to the actual factory if called; no fake writers.
        targets = [(modules["coding"], "_connect"), (self.P()["fulltext_mod"], "_connect"),
                   (modules["consensus"], "_connect"), (modules["evidence"].workflow, "connect"),
                   (modules["evidence"], "migrate"), (self.P()["db_mod"], "_connect")]
        calls = []
        with contextlib.ExitStack() as stack:
            for module, name in targets:
                real = getattr(module, name)
                def counted(*args, _real=real, _label=module.__name__ + "." + name, **kwargs):
                    calls.append(_label)
                    return _real(*args, **kwargs)
                stack.enter_context(mock.patch.object(module, name, counted))
            yield calls
        self.assertEqual(calls, [], "borrowed writer must use only caller connection")

    def consensus_fixture(self):
        modules = a2_modules()
        directory = tempfile.mkdtemp(prefix="a2cc-", dir=str(self.root))
        _own_directory(directory, self.addCleanup)
        db = Path(directory) / "collaboration.db"  # sole business target, never screener.db
        prepare = getattr(modules["consensus"], A2_CONSENSUS_PREPARE_NAME)
        prepare(db)  # complete independent preparation/commit before window
        with closing(sqlite3.connect(str(db))) as conn:
            self.P()["ensure_default_recording"](conn, self.ctx())
        return db

    def consensus_window(self, db):
        # Existing consensus algorithms address sqlite3.Row by column name.
        w = self.window(db)
        w.row_factory = sqlite3.Row
        return w

    def source(self):
        # Actual adjacent consensus contract requires a dict; no new provenance rule.
        return {"kind": "manual", "ref": "A2 synthetic source"}


class A2CodingTests(A2ContractCase):
    def test_a2_coding_single_and_batch_share_real_window(self):
        coding = a2_modules()["coding"]
        gate_db, gate_first, _ = self.research_fixture()
        self.assert_a2_connection_gates(gate_db, gate_first)
        for batch in (False, True):
            with self.subTest(batch=batch):
                db, first, second = self.research_fixture()
                tables = ("coding_values", "workspace_undo", "trace_events", "trace_write_operations",
                          "trace_field_lifecycles")
                before = self.snapshot_tables(db, tables)
                rid = str(uuid.uuid4())
                w = self.window(db)
                delta = self.writer_delta(w)
                try:
                    if batch:
                        save = lambda: coding.save_coding_values_batch(
                            db, "K1", {first: "one", second: "two"},
                            trace_context=self.ctx(request_id=rid), conn=w)
                    else:
                        save = lambda: coding.save_coding_value(
                            db, first, "K1", "one", trace_context=self.ctx(request_id=rid), conn=w)
                    with self.no_a2_reconnect():
                        result = save()
                    records = delta()
                    self.assertIsNone(result)
                    self.assertTrue(w.in_transaction)
                    self.assertEqual(_sql_violations(records), [])
                    count = w.execute("SELECT COUNT(*) FROM coding_values").fetchone()[0]
                    self.assertEqual(count, 2 if batch else 1)
                    for table in ("workspace_undo", "trace_events", "trace_write_operations"):
                        self.assertIsNotNone(w.execute(f"SELECT 1 FROM {table} WHERE request_id=?", (rid,)).fetchone())
                    self.assert_restored(db, before)  # independent reader sees committed baseline
                    saved = {table: w.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                             for table in tables}
                    replay_delta = self.writer_delta(w)
                    with self.no_a2_reconnect():
                        self.assertIsNone(save())
                    replay_records = replay_delta()
                    self.assertEqual(_sql_violations(replay_records), [])
                    self.assertFalse(any(kind in ("execute", "executemany") and
                                         sql.lstrip().split(None, 1)[0].upper() in ("INSERT", "UPDATE", "DELETE", "REPLACE")
                                         for kind, sql in replay_records), "replay must perform no writes")
                    self.assertTrue(w.in_transaction)
                    self.assertEqual({table: w.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                                      for table in tables}, saved)
                    self.assert_restored(db, before)
                finally:
                    w.rollback()
                self.assert_restored(db, before)

    def test_a2_batch_second_write_failure_rolls_back_all_domains(self):
        coding = a2_modules()["coding"]
        db, first, second = self.research_fixture()
        # Fixture trigger is prepared/committed before observation. Both dimensions
        # validate normally; abort only on the second actual business INSERT.
        with closing(sqlite3.connect(str(db))) as prepare, prepare:
            prepare.execute(f"CREATE TRIGGER a2_abort_second BEFORE INSERT ON coding_values "
                            f"WHEN NEW.dimension_id={int(second)} BEGIN SELECT RAISE(ABORT,'A2 second write'); END")
        before = self.snapshot_tables(db, ("coding_values", "workspace_undo", "trace_events", "trace_write_operations",
                                           "trace_field_lifecycles"))
        w = self.window(db)
        rid = str(uuid.uuid4())
        delta = self.writer_delta(w)
        try:
            with self.no_a2_reconnect():
                with self.assertRaisesRegex(sqlite3.IntegrityError, "A2 second write"):
                    coding.save_coding_values_batch(db, "K1", {first: "first", second: "second"},
                                                   trace_context=self.ctx(request_id=rid), conn=w)
            records = delta()
            self.assertEqual(_sql_violations(records), [])
            self.assertTrue(w.in_transaction)
            self.assertEqual(w.execute("SELECT value FROM coding_values WHERE dimension_id=?", (first,)).fetchone(), ("first",))
            for table in ("trace_events", "trace_write_operations"):
                self.assertIsNotNone(w.execute(f"SELECT 1 FROM {table} WHERE request_id=?", (rid,)).fetchone(),
                                     "first write must reach trace/request before the second write aborts")
            self.assert_restored(db, before)
        finally:
            w.rollback()
        self.assert_restored(db, before)

    def test_a2_coding_default_commits_normalized_value(self):
        db, first, second = self.research_fixture()
        coding = a2_modules()["coding"]
        coding.save_coding_value(db, first, "K1", "  old default  ")
        self.assertEqual(self.q_one(db, "SELECT value FROM coding_values WHERE dimension_id=?", (first,)), ("old default",))
        coding.save_coding_values_batch(db, "K1", {first: "new", second: "batch"}, trace_context=self.ctx())
        self.assertEqual(self.q(db, "SELECT value FROM coding_values ORDER BY dimension_id"), [("new",), ("batch",)])


class A2EvidenceTests(A2ContractCase):
    def test_a2_normal_and_submission_form_visibility_and_rollback(self):
        evidence = a2_modules()["evidence"]
        for submission in (False, True):
            with self.subTest(submission_evidence=submission):
                db, first, _ = self.research_fixture()
                image_bytes = a2_png()
                upload = evidence.stage_upload(db, TASK, ACTOR, "K1", first, image_bytes)
                image_path = Path(self.q_one(db, "SELECT path FROM workflow_evidence_uploads WHERE id=?",
                                             (upload["upload_id"],))[0])
                tables = ("coding_values", "coding_notes", "coding_note_images",
                          "workflow_evidence_uploads", "workspace_undo", "trace_events",
                          "workflow_form_requests", "trace_field_lifecycles")
                before = self.snapshot_tables(db, tables)
                rid = str(uuid.uuid4())
                w = self.window(db)
                delta = self.writer_delta(w)
                try:
                    save = lambda: evidence.save_form(db, "K1", {} if submission else {first: "value"},
                                                      notes={first: "note"},
                                                      evidence={first: {"upload_ids": [upload["upload_id"]]}},
                                                      trace_context=self.ctx(request_id=rid),
                                                      submission_evidence=submission, conn=w)
                    with self.no_a2_reconnect():
                        result = save()
                    records = delta()
                    self.assertIsNone(result)
                    self.assertTrue(w.in_transaction)
                    self.assertEqual(_sql_violations(records), [])
                    self.assertEqual(w.execute("SELECT text FROM coding_notes WHERE dimension_id=?", (first,)).fetchone(), ("note",))
                    self.assertEqual(w.execute("SELECT attached FROM workflow_evidence_uploads WHERE id=?",
                                               (upload["upload_id"],)).fetchone(), (1,))
                    self.assertEqual(w.execute("SELECT path,sha256 FROM coding_note_images").fetchone(),
                                     (str(image_path), hashlib.sha256(image_bytes).hexdigest()))
                    self.assertEqual(image_path.read_bytes(), image_bytes)
                    summary = w.execute("SELECT 1 FROM workflow_form_requests WHERE request_id=?", (rid,)).fetchone()
                    self.assertIsNotNone(summary)
                    row = w.execute("SELECT before_json,after_json FROM workspace_undo WHERE request_id=?", (rid,)).fetchone()
                    self.assertIsNotNone(row)
                    if submission:
                        self.assertEqual(json.loads(row[0])["access_context"], self.P()["undo_mod"].SUBMISSION_EVIDENCE)
                        self.assertEqual(json.loads(row[1])["access_context"], self.P()["undo_mod"].SUBMISSION_EVIDENCE)
                    self.assert_restored(db, before)
                    saved = {table: w.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                             for table in tables}
                    replay_delta = self.writer_delta(w)
                    with self.no_a2_reconnect():
                        self.assertIsNone(save())
                    replay_records = replay_delta()
                    self.assertEqual(_sql_violations(replay_records), [])
                    self.assertFalse(any(kind in ("execute", "executemany") and
                                         sql.lstrip().split(None, 1)[0].upper() in ("INSERT", "UPDATE", "DELETE", "REPLACE")
                                         for kind, sql in replay_records), "form replay must perform no writes")
                    self.assertTrue(w.in_transaction)
                    self.assertEqual({table: w.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                                      for table in tables}, saved)
                    self.assertEqual(image_path.read_bytes(), image_bytes)
                    self.assert_restored(db, before)
                finally:
                    w.rollback()
                self.assert_restored(db, before)

    def test_a2_borrowed_upload_has_no_gc_and_rollback_keeps_committed_file(self):
        evidence = a2_modules()["evidence"]
        db, first, _ = self.research_fixture()
        committed_bytes = a2_png()
        old = evidence.stage_upload(db, TASK, ACTOR, "K1", first, committed_bytes)
        old_row = self.q_one(db, "SELECT path,sha256 FROM workflow_evidence_uploads WHERE id=?", (old["upload_id"],))
        old_path = Path(old_row[0])
        before = self.snapshot_tables(db, ("workflow_evidence_uploads",))
        w = self.window(db)
        delta = self.writer_delta(w)
        real_gc = evidence.cleanup_staging
        with mock.patch.object(evidence, "cleanup_staging", wraps=real_gc) as gc:
            try:
                with self.no_a2_reconnect():
                    result = evidence.stage_upload(db, TASK, ACTOR, "K1", first, a2_png((140, 30, 10)), conn=w)
                records = delta()
                self.assertEqual(_sql_violations(records), [])
                self.assertTrue(w.in_transaction)
                gc.assert_not_called()
                self.assertNotEqual(result["upload_id"], old["upload_id"])
                row = w.execute("SELECT path,sha256 FROM workflow_evidence_uploads WHERE id=?", (result["upload_id"],)).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(hashlib.sha256(Path(row[0]).read_bytes()).hexdigest(), row[1])
                self.assert_restored(db, before)
            finally:
                w.rollback()
        self.assert_restored(db, before)
        self.assertEqual(old_path.read_bytes(), committed_bytes)
        # Deliberately NO assertion deleting the new physical file on rollback.
        # Its orphan status is the documented file/SQLite non-atomic boundary.

    def test_a2_upload_failure_never_deletes_committed_referenced_file(self):
        evidence = a2_modules()["evidence"]
        db, first, _ = self.research_fixture()
        data = a2_png()
        upload = evidence.stage_upload(db, TASK, ACTOR, "K1", first, data)
        evidence.save_form(db, "K1", {}, notes={first: "attached"},
                           evidence={first: {"upload_ids": [upload["upload_id"]]}},
                           trace_context=self.ctx(request_id=str(uuid.uuid4())))
        row = self.q_one(db, "SELECT path,sha256 FROM workflow_evidence_uploads WHERE id=?", (upload["upload_id"],))
        path = Path(row[0])
        before = self.snapshot_tables(db, ("workflow_evidence_uploads", "coding_note_images"))
        path.write_bytes(b"damaged synthetic bytes")  # force old-upload repair path
        injected = OSError("A2 failure after real atomic file replace")
        real_write = evidence._write_atomic
        def write_then_fail(target, payload):
            real_write(target, payload)
            raise injected
        with mock.patch.object(evidence, "_write_atomic", side_effect=write_then_fail):
            with self.assertRaises(OSError) as caught:
                evidence.stage_upload(db, TASK, ACTOR, "K1", first, data)
        self.assertIs(caught.exception, injected)
        self.assert_restored(db, before)
        self.assertEqual(path.read_bytes(), data, "committed upload/image reference still owns this file")

    def test_a2_default_upload_gc_observes_commit_and_failure_preserves_result(self):
        evidence = a2_modules()["evidence"]
        db, first, _ = self.research_fixture()
        data = a2_png()
        injected = OSError("A2 postcommit GC failure")
        calls = []
        def fail_gc(conn, target):
            self.assertTrue(conn.in_transaction, "cleanup SQL has its own transaction")
            rows = self.q(db, "SELECT id,path,sha256 FROM workflow_evidence_uploads")
            self.assertEqual(len(rows), 1, "GC starts only after another connection sees business commit")
            self.assertEqual(Path(rows[0][1]).read_bytes(), data)
            calls.append(rows[0])
            raise injected
        with mock.patch.object(evidence, "cleanup_staging", side_effect=fail_gc), \
                self.assertLogs(evidence.__name__, level="WARNING") as diagnostic:
            result = evidence.stage_upload(db, TASK, ACTOR, "K1", first, data)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result, {"upload_id": calls[0][0], "sha256": hashlib.sha256(data).hexdigest(),
                                 "preview_url": f"/api/tasks/{TASK}/coding/evidence/uploads/{calls[0][0]}"})
        self.assertEqual(self.q(db, "SELECT id,path,sha256 FROM workflow_evidence_uploads"), calls)
        self.assertEqual(calls[0][2], result["sha256"])
        self.assertEqual(Path(calls[0][1]).read_bytes(), data)
        self.assertTrue(any(record.exc_info and record.exc_info[1] is injected
                            for record in diagnostic.records), "cleanup failure must be diagnosed, not returned")


class A2HighlightTests(A2ContractCase):
    def test_a2_highlight_add_and_delete_caller_visibility_and_rollback(self):
        db = self.new_db("raw")
        fulltext = self.P()["fulltext_mod"]
        event = {"kind": "highlight", "page": 1, "text": "A2 selection", "color": "jade", "rects": [[1, 2, 11, 12]]}
        before = self.snapshot_tables(db, ("highlights",))
        w = self.window(db)
        delta = self.writer_delta(w)
        try:
            with self.no_a2_reconnect():
                count = fulltext.add_highlight_rects(db, "K1", [event], conn=w)
            records = delta()
            self.assertEqual(count, 1)
            self.assertTrue(w.in_transaction)
            self.assertEqual(_sql_violations(records), [])
            self.assertEqual(w.execute("SELECT text FROM highlights").fetchone(), (event["text"],))
            self.assert_restored(db, before)
        finally:
            w.rollback()
        self.assert_restored(db, before)
        self.assertEqual(fulltext.add_highlight_rects(db, "K1", [event]), 1)  # default positive
        committed = self.snapshot_tables(db, ("highlights",))
        hid = self.q_one(db, "SELECT id FROM highlights")[0]
        w2 = self.window(db)
        delta = self.writer_delta(w2)
        try:
            with self.no_a2_reconnect():
                deleted = fulltext.delete_highlight(db, hid, conn=w2)
            records = delta()
            self.assertTrue(deleted)
            self.assertTrue(w2.in_transaction)
            self.assertEqual(_sql_violations(records), [])
            self.assertIsNone(w2.execute("SELECT 1 FROM highlights WHERE id=?", (hid,)).fetchone())
            self.assert_restored(db, committed)
        finally:
            w2.rollback()
        self.assert_restored(db, committed)
        self.assertTrue(fulltext.delete_highlight(db, hid))
        self.assertFalse(fulltext.delete_highlight(db, hid))


class A2ConsensusTests(A2ContractCase):
    def test_a2_consensus_prepare_atomic_ddl_failure_and_idempotent_success(self):
        consensus = a2_modules()["consensus"]
        db = self.root / "prepare-atomic.db"
        real_connect = sqlite3.connect
        with closing(real_connect(str(db))) as seed, seed:
            seed.execute("CREATE TABLE a2_existing(value TEXT)")
            seed.execute("INSERT INTO a2_existing VALUES('preserved')")
        baseline = self.q(db, "SELECT type,name,sql FROM sqlite_master ORDER BY type,name")
        opened = []

        class PrepareConnection(RecordedConnection):
            def executescript(self, sql, *args, **kwargs):
                try:
                    return super().executescript(sql, *args, **kwargs)
                except sqlite3.Error as exc:
                    self.ddl_error = exc
                    self.failed_in_transaction = self.in_transaction
                    self.partial_objects = self.execute("SELECT name FROM sqlite_master").fetchall()
                    raise

        def observed_connect(*args, **kwargs):
            conn = real_connect(*args, factory=PrepareConnection, **kwargs)
            opened.append(conn)
            return conn

        # The failure is real SQLite SQL after valid original DDL, not a fake writer.
        broken_schema = consensus._SCHEMA + (
            "\nCREATE TABLE a2_prepare_partial(value TEXT);\n"
            "SELECT * FROM a2_missing_prepare_failure;\n")
        with mock.patch.object(consensus.sqlite3, "connect", side_effect=observed_connect), \
                mock.patch.object(consensus, "_SCHEMA", broken_schema):
            with self.assertRaisesRegex(sqlite3.OperationalError, "a2_missing_prepare_failure") as caught:
                consensus.prepare_coding_consensus_storage(db)
        self.assertEqual(len(opened), 1)
        failed = opened[0]
        self.assertIs(caught.exception, failed.ddl_error, "preserve the actual SQLite exception instance")
        self.assertTrue(failed.failed_in_transaction)
        self.assertIn(("a2_prepare_partial",), failed.partial_objects)
        self.assertIn(("coding_rater_records",), failed.partial_objects)
        self.assertEqual(sum(kind == "rollback" for kind, _ in failed.records), 1)
        self.assertFalse(any(kind == "commit" for kind, _ in failed.records))
        self.assertEqual(sum(kind == "close" for kind, _ in failed.records), 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            failed.execute("SELECT 1")
        self.assertEqual(self.q(db, "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"), baseline)
        self.assertEqual(self.q(db, "SELECT value FROM a2_existing"), [("preserved",)])

        with mock.patch.object(consensus.sqlite3, "connect", side_effect=observed_connect):
            self.assertIsNone(consensus.prepare_coding_consensus_storage(db))
            complete = self.q(db, "SELECT type,name,sql FROM sqlite_master ORDER BY type,name")
            self.assertIsNone(consensus.prepare_coding_consensus_storage(db))
        self.assertEqual(self.q(db, "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"), complete)
        names = {row[1] for row in complete}
        self.assertTrue({"coding_rater_records", "coding_consensus_records", "coding_consensus_sources", "coding_freezes",
                         "idx_coding_rater_records_cell", "idx_coding_consensus_cell",
                         "coding_rater_records_no_update", "coding_rater_records_no_delete",
                         "coding_consensus_records_no_update", "coding_consensus_records_no_delete",
                         "coding_consensus_sources_no_update", "coding_consensus_sources_no_delete",
                         "coding_freezes_no_update", "coding_freezes_no_delete"} <= names)
        # The independent reader above also used the observed real factory. Only
        # preparation connections execute the schema script and own a commit.
        successful = [conn for conn in opened[1:] if any(kind == "executescript" for kind, _ in conn.records)]
        self.assertEqual(len(successful), 2)
        for conn in successful:
            self.assertEqual(sum(kind == "commit" for kind, _ in conn.records), 1)
            self.assertFalse(any(kind == "rollback" for kind, _ in conn.records))
            self.assertEqual(sum(kind == "close" for kind, _ in conn.records), 1)
            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")
        self.assertEqual(self.q(db, "SELECT value FROM a2_existing"), [("preserved",)])

    def test_a2_all_three_consensus_writers_use_only_collaboration_window(self):
        consensus = a2_modules()["consensus"]
        db = self.consensus_fixture()
        tables = ("coding_rater_records", "coding_consensus_records", "coding_consensus_sources", "coding_freezes",
                  "trace_events", "trace_write_operations")
        before = self.snapshot_tables(db, tables)
        other_db = self.consensus_fixture()
        other_before = self.snapshot_tables(other_db, tables)
        for gate in ("row_factory", "main_target"):
            for writer in ("rater", "consensus", "freeze"):
                with self.subTest(writer=writer, gate=gate):
                    bad = self.window(db) if gate == "row_factory" else self.consensus_window(db)
                    factory = bad.row_factory
                    target = db if gate == "row_factory" else other_db
                    if gate == "main_target":
                        # Matching an attached file is insufficient: the business target is main.
                        bad.execute("ATTACH DATABASE ? AS a2_other", (str(other_db),))
                    delta = self.writer_delta(bad)
                    try:
                        error, message = ((TypeError, "requires sqlite3.Row") if gate == "row_factory"
                                          else (ValueError, "canonical main database target"))
                        with self.no_a2_reconnect(), self.assertRaisesRegex(error, message):
                            if writer == "rater":
                                consensus.append_rater_record(target, item_key="K1", field_key="A2-field",
                                    rater_id=ACTOR, value="negative", source=self.source(), conn=bad)
                            elif writer == "consensus":
                                consensus.append_consensus_record(target, item_key="K1", field_key="A2-field",
                                    reconciler_id=999, value="negative", source=self.source(), rationale="reason", conn=bad)
                            else:
                                consensus.freeze_consensus(target, name="negative", frozen_by=999, consensus_ids=[1], conn=bad)
                        records = delta()
                        self.assertEqual(_sql_violations(records), [])
                        self.assertTrue(all(kind == "execute" and sql == "PRAGMA database_list"
                                            for kind, sql in records), "identity checks must precede all business SQL")
                        self.assertTrue(bad.in_transaction)
                        self.assertIs(bad.row_factory, factory, "writer must not change caller row_factory")
                        for table in tables:
                            self.assertEqual([tuple(row) for row in bad.execute(f"SELECT * FROM {table} ORDER BY rowid")],
                                             before[table])
                        self.assert_restored(db, before)
                        self.assert_restored(other_db, other_before)
                    finally:
                        bad.rollback()
                        bad.close()
        # A normalized alias of the same actual main file remains valid.
        canonical_db = str(db.parent) + "/./" + db.name
        w = self.consensus_window(db)
        rid = str(uuid.uuid4())
        try:
            calls = [
                lambda: consensus.append_rater_record(canonical_db, item_key="K1", field_key="A2-field",
                    rater_id=ACTOR, value="rater", source=self.source(),
                    trace_context=self.ctx(request_id=rid), conn=w),
            ]
            delta = self.writer_delta(w)
            with self.no_a2_reconnect():
                rater = calls[0]()
            self.assertEqual(_sql_violations(delta()), [])
            self.assertTrue(w.in_transaction)
            self.assertIsNotNone(w.execute("SELECT 1 FROM coding_rater_records WHERE id=?", (rater["id"],)).fetchone())
            for table in ("trace_events", "trace_write_operations"):
                self.assertIsNotNone(w.execute(f"SELECT 1 FROM {table} WHERE request_id=?", (rid,)).fetchone())
            delta = self.writer_delta(w)
            with self.no_a2_reconnect():
                self.assertEqual(calls[0](), rater)  # replay does not commit or duplicate
            self.assertEqual(_sql_violations(delta()), [])
            self.assertEqual(w.execute("SELECT COUNT(*) FROM coding_rater_records").fetchone()[0], 1)
            self.assert_restored(db, before)
            delta = self.writer_delta(w)
            with self.no_a2_reconnect():
                record = consensus.append_consensus_record(canonical_db, item_key="K1", field_key="A2-field",
                    reconciler_id=999, value="consensus", source=self.source(), rationale="A2 rationale",
                    rater_record_ids=[rater["id"]], conn=w)
            self.assertEqual(_sql_violations(delta()), [])
            self.assertTrue(w.in_transaction)
            self.assertIsNotNone(w.execute("SELECT 1 FROM coding_consensus_records WHERE id=?", (record["id"],)).fetchone())
            self.assert_restored(db, before)
            delta = self.writer_delta(w)
            with self.no_a2_reconnect():
                freeze = consensus.freeze_consensus(canonical_db, name="A2 snapshot", frozen_by=999,
                                                     consensus_ids=[record["id"]], conn=w)
            self.assertEqual(_sql_violations(delta()), [])
            self.assertTrue(w.in_transaction)
            self.assertIsNotNone(w.execute("SELECT 1 FROM coding_freezes WHERE id=?", (freeze["id"],)).fetchone())
            self.assertIs(w.row_factory, sqlite3.Row)
            self.assert_restored(db, before)
        finally:
            w.rollback()
        self.assert_restored(db, before)
        # Compact default control, no historical-version/latest policy expansion.
        rater = consensus.append_rater_record(db, item_key="K1", field_key="A2-field",
                                             rater_id=ACTOR, value="default", source=self.source())
        record = consensus.append_consensus_record(db, item_key="K1", field_key="A2-field",
                    reconciler_id=999, value="default", source=self.source(), rationale="default control",
                    rater_record_ids=[rater["id"]])
        frozen = consensus.freeze_consensus(db, name="default", frozen_by=999, consensus_ids=[record["id"]])
        self.assertIsNotNone(self.q_one(db, "SELECT 1 FROM coding_freezes WHERE id=?", (frozen["id"],)))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
