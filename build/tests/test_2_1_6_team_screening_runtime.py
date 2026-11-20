# -*- coding: utf-8 -*-
"""2.1.6 S1c TSR（team_screening_runtime）正式回归 —— 8组×正反例 + Terra F1-F8 回归。

合同依据（只读引用）：
- out/maintenance216/s1c_runtime_ruling_sol.md（Sol 最终裁定 §1-§7）
- out/maintenance216/s1c_runtime_api_contract.md（冻结公开 API/错误族/DTO）
- out/maintenance216/s1c_runtime_design.md §F（scope 借用契约/键域/时间）
- out/maintenance216/review_s1c_runtime_terra.md（F1-F8 独审回归要求）

八组（每例目的→夹具→断言，正反例成对；不镜像旧 55/59/43/22，不复用作者探针）：
  G1 账户 reader 与全账户 casefold 冲突（>1 个不同 id；防 set 语义假阳/漏检）
  G2 身份/路径（slug 管道、保留名 collaboration.db、owner 快照名分叉、containment）
  G3 角色/required/known-team 分类（role reader tuple/Row、task_kind、awaiting_config、
     0/1 required、双 grant 冲突、失效 choice、member_unresolved、F5/F6 坏源 failclosed）
  G4 fresh scope（owner=成员同库合法去重、零创建、missing_vote 精确、F4 辅助分类不降级、
     F1 硬链接两账户同物理票库）
  G5 borrowed scope（caller 事务只读、未提交可见、connection_contract、missing_members、
     零 FS、F2 无事务连接构造拒绝）
  G6 S1b conn seam（未提交 attest 可见、事务收尾、真未准备才空、F7 缺 generic 表/缺列、
     synthetic 孤儿、path 公共锚点）
  G7 时间（非法 updated_at 不回退→vote_time_unverifiable 整体抛、真实重存恢复、naive）
  G8 逐 key 仲裁/canonical 域/DTO 零票面（A/B 逐 key、双 ID、个人 exclude→团队 include、
     域外票过滤、F8 整键 proof 缺失、loader_stale 两 reason）

诚实边界（不伪造红/不 skip）：
- [MUTATION] 标注 = 受控变异证明（F3 containment：monkeypatch pathlib.Path.resolve 把
  victim.db 解析重定向到箱内真实外部 scratch 库——**逻辑反例，非实机 symlink 复现**；
  Windows 无特权 symlink 创建 WinError1314，本轮实机 symlink 未测、仅此处记录）；
  另附真实路径正对照证明接缝本身不误伤合法路径。
- synthetic 损坏源（F5/F6/F7、孤儿、非法 attested_at）= 真实 schema 上按标签直插/DROP
  合成，与真实 append-only 路径边界清晰；不在真实 append-only 表上 UPDATE/DELETE 制造损坏。
- 本轮 BEFORE 产品（cc20/323/423）按 Terra 存在已知缺陷：F1-F8 对应用例**预期红**；
  其余用例按冻结合同钉行为。红绿语义：不把 ImportError/0收集当行为红。
- 无 skip；无 mock core；断言不镜像源码常量/触发器文本/错误文案（精确族异常断言）。
- pytest/unittest 双可发现；模块顶层零环境变更、零产品 imports（产品导入在 setUpClass
  环境 owned 之后）；经 build/tests/_bare_env 真实边界（acquire/release）借用共享根。
"""

from __future__ import annotations

import atexit
import gc
import hashlib
import importlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import types
import unittest
from uuid import uuid4
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = Path(__file__).resolve().parent
for _p in (str(REPO_ROOT), str(TESTS_DIR)):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

import _bare_env  # noqa: E402  build/tests 真实隔离边界（test 基建，非产品）

DATA_ENV = "COBOOKSHELF_DATA_DIR"
TRACKED_ENV = ("COBOOKSHELF_DATA_DIR", "REVIEWFLOW_MODE", "TEMP", "TMP",
               "TMPDIR", "RF_R9_TMP", "RF_R9_TMP_ROOT")
PASSWORD = "s1c-pass-9527"

#: 产品模块句柄在环境 owned 后由 _import_products() 填充（顶层零产品 imports）。
#: 快照交接哨兵：每进程一次（见 _emit_runtime_snapshot）。
_SNAPSHOT_EMITTED = False
#: atexit 退出回调注册哨兵：每进程一次（见 _register_exit_state）。
_EXIT_STATE_REGISTERED = False

#: 快照/来源记录覆盖的模块清单（label, 全局名, 导入名）；CORE 为**真实
#: 已导入模块对象**（不再 None 回退预期路径——TESTFIX-R3）。
_SOURCE_MODULES = (
    ("TSR", "tsr", "custom_backend.team_screening_runtime"),
    ("COLLAB", "collab", "coscreen.task_collaboration"),
    ("ARB", "sarb", "coscreen.screening_arbitration"),
    ("LEDGER", "ledger", "coscreen.team_vote_ledger"),
    ("CORE", "core_mod", "coscreen.team_screening_eligibility"),
    ("DBM", "dbm", "coscreen.db"),
)


def _emit_runtime_snapshot():
    """快照交接协议（s1c_final_harness_contract.md；测试 logger、非产品
    逻辑）：外层设置 ``S1C_SOURCE_MANIFEST_INPUT``（只读 JSON 绝对路径，
    内容＝显式相对 .py 数组）时，在真实产品导入完成后**一次性**向 stdout
    flush 单行 ``S1C_RUNTIME_SNAPSHOT_JSON=<ASCII-safe JSON>``。未设置＝零
    额外动作（正常收集无副作用）。"""
    global _SNAPSHOT_EMITTED
    if _SNAPSHOT_EMITTED:
        return
    manifest_input = os.environ.get("S1C_SOURCE_MANIFEST_INPUT") or ""
    if not manifest_input.strip():
        return
    payload_files = {}
    try:
        entries = json.loads(Path(manifest_input).read_text(encoding="utf-8"))
        for rel in entries:
            rel = str(rel)
            target = (REPO_ROOT / rel).resolve()
            payload_files[rel] = _sha256_file(target)
    except (OSError, ValueError) as exc:  # 输入不可读＝如实报错不伪快照
        print("S1C_RUNTIME_SNAPSHOT_ERROR", repr(exc), flush=True)
        return
    modules = {}
    for label, global_name, import_name in _SOURCE_MODULES:
        module = globals().get(global_name)
        if module is None:
            import importlib
            try:
                module = importlib.import_module(import_name)
            except ImportError:
                module = None
        if module is not None and getattr(module, "__file__", None):
            resolved = Path(module.__file__).resolve()
            modules[import_name] = {"file": str(resolved),
                                    "sha256": _sha256_file(resolved)}
    for import_name in ("custom_backend.auth", "build.tests.conftest"):
        module = sys.modules.get(import_name)
        if module is not None and getattr(module, "__file__", None):
            resolved = Path(module.__file__).resolve()
            modules[import_name] = {"file": str(resolved),
                                    "sha256": _sha256_file(resolved)}
    test_file = Path(__file__).resolve()
    modules["__test__"] = {"file": str(test_file),
                           "sha256": _sha256_file(test_file)}
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
        "env": {key: os.environ.get(key)
                for key in (DATA_ENV, "REVIEWFLOW_MODE", "TEMP", "TMP",
                            "TMPDIR", "RF_R9_TMP", "RF_R9_TMP_ROOT")},
        "files": payload_files,
        "modules": modules,
    }
    line = json.dumps(payload, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"))
    print("S1C_RUNTIME_SNAPSHOT_JSON=" + line, flush=True)
    _SNAPSHOT_EMITTED = True
    _register_exit_state()


def _emit_exit_state():
    """s1c_final_harness_contract（更新版）：**标准 atexit 回调**（测试
    logger 范围）——进程退出时 flush 单行 ``S1C_EXIT_STATE_JSON=<ASCII
    JSON>``。不拦异常/不改退出码/不重置 helper/不改 unittest/pytest 控制
    流；只读当前进程末态。"""
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
        "env": {key: os.environ.get(key)
                for key in (DATA_ENV, "REVIEWFLOW_MODE", "TEMP", "TMP",
                            "TMPDIR", "RF_R9_TMP", "RF_R9_TMP_ROOT")},
        "default_data": {
            "cwd_data": (Path.cwd() / "data").exists(),
            "box_data": (REPO_ROOT / "data").exists(),
        },
    }
    line = json.dumps(payload, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"))
    print("S1C_EXIT_STATE_JSON=" + line, flush=True)


def _register_exit_state():
    """runtime snapshot marker 成功后**仅一次**注册 atexit 退出回调。
    flag 未设置（S1C_SOURCE_MANIFEST_INPUT 空缺）→ 不注册、不动作
    （正常收集零副作用）。"""
    global _EXIT_STATE_REGISTERED
    if _EXIT_STATE_REGISTERED:
        return
    atexit.register(_emit_exit_state)
    _EXIT_STATE_REGISTERED = True

tsr = collab = sarb = ledger = dbm = wf = sec = tasks_mod = auth = core_mod = None


def _sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _import_products():
    """setUpClass 环境 owned 后的产品导入（唯一入口；箱身份核验）。"""
    global tsr, collab, sarb, ledger, dbm, wf, sec, tasks_mod, auth, core_mod
    if tsr is not None:
        _emit_runtime_snapshot()   # 每进程一次（幂等哨兵）
        return
    import custom_backend.team_screening_runtime as _tsr
    from coscreen import task_collaboration as _collab
    from coscreen import screening_arbitration as _sarb
    from coscreen import team_vote_ledger as _ledger
    from coscreen import db as _dbm
    from coscreen import review_workflow as _wf
    from coscreen import security as _sec
    from coscreen import tasks as _tasks
    import custom_backend.auth as _auth
    from coscreen import team_screening_eligibility as _core
    tsr, collab, sarb, ledger, dbm, wf, sec, tasks_mod, auth, core_mod = (
        _tsr, _collab, _sarb, _ledger, _dbm, _wf, _sec, _tasks, _auth, _core)
    for module in (tsr, collab, sarb, ledger, dbm, wf, sec, tasks_mod, auth):
        src = Path(module.__file__).resolve()
        assert src.is_relative_to(REPO_ROOT), ("product import escaped box", module.__name__, str(src))


#: 类级共享账号（真实 auth 注册 + 标注式直插：注册层按设计拒绝冲突名，
#: TSR 数据层仍须防御宿主库出现这类行）。
USERS: dict[str, int] = {}


def _ensure_users(data_root: Path):
    """真实 create_user 建核心账号；冲突名（同 casefold 对/保留名/全符号）
    由直插种子提供并在报告/注释标注（auth 注册层本应拒绝）。幂等：库已在
    （如复跑）时从真实库行装载 username→id 映射，不重复注册。"""
    if USERS:
        return
    users_db = data_root / "users.db"
    if not users_db.exists():
        for name in ("alice", "bob", "carol", "dave", "victim", "张三研"):
            info = auth.create_user(name, PASSWORD)
            USERS[info["username"]] = int(info["id"])
        seeded = [
            ("Dave", "与 dave 同 casefold 的宿主行（注册层拒绝；TSR 须防御）"),
            ("DAVE", "第三同 casefold 账号（>2 分组仍冲突）"),
            ("collaboration", "slug 撞协作库保留名（注册层 U4-P2 拒绝；TSR 须防御）"),
            ("!!!", "全非法字符→fallback slug（注册层拒绝；TSR 管道防御）"),
        ]
        with closing(sqlite3.connect(users_db)) as conn, conn:
            for name, why in seeded:
                cur = conn.execute(
                    "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                    (name, "seed-direct-insert-not-a-real-hash", "2026-11-15T00:00:00+00:00"))
                USERS[name] = int(cur.lastrowid)
        return
    # 复跑/已存在：真实库行是唯一事实源（含此前直插种子行）。
    with closing(sqlite3.connect(users_db)) as conn:
        for uid, name in conn.execute("SELECT id, username FROM users"):
            USERS[str(name)] = int(uid)
    missing = {"alice", "bob", "carol", "dave", "victim", "张三研",
               "Dave", "DAVE", "collaboration", "!!!"} - set(USERS)
    assert not missing, f"S1C-BOX users.db 缺种子账号: {sorted(missing)}"


def _restore_tracked_env(prev: dict) -> None:
    """T1：TRACKED_ENV（六 scratch 键＋REVIEWFLOW_MODE）整体还原。"""
    for key in TRACKED_ENV:
        val = prev.get(key)
        if val is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = val


# ---------------------------------------------------------------------------
# R2：模块级根生命周期——所有类共享同一根，避免"每类新根 × helper 进程
# owner/USERS 跨类"的第二类身份拒绝。pytest（conftest 会话根，DATA 预置）
# 只借用；direct unittest 由 ``_bare_env.bare_scope()`` 显式套件所有者持根
# （不改 helper、不重置其全局 owner、不清 sys.modules）。探针子进程直跑
# 单类（无 setUpModule）走类级 owned 兜底分支。模块收集期零 env 改写/零
# 产品导入。USERS 缓存随根同生命周期（模块收尾清理）。
# ---------------------------------------------------------------------------
_MODULE_SCOPE: dict | None = None


def _clear_module_scope(scope: dict) -> None:
    """TF3-2：**绑定该代** scope 的清理回调——仅当全局仍是同一 dict 时置
    None（防误清下一代）；不手动 reset helper registry/全局 owner。"""
    global _MODULE_SCOPE
    if _MODULE_SCOPE is scope:
        _MODULE_SCOPE = None


def setUpModule():
    global _MODULE_SCOPE
    if _MODULE_SCOPE is not None:
        return
    prev_env = {key: os.environ.get(key) for key in TRACKED_ENV}
    # 登记先行：任何环境改写/资源创建之前（失败路径也还原）。
    unittest.addModuleCleanup(_restore_tracked_env, dict(prev_env))
    complete = False
    try:
        preset = (os.environ.get(DATA_ENV) or "").strip()
        if preset:
            root = Path(preset)
            # 借用根内只建**唯一自有子目录**（外部预存内容不属本套件，绝不删）。
            subdir = root / f"s1c-runtime-tests-{uuid4().hex[:12]}"
            subdir.mkdir(parents=True, exist_ok=False)
            scope = {"borrowed": True, "root": root,
                     "class_dir": subdir, "work_root": subdir / "work"}
            unittest.addModuleCleanup(shutil.rmtree, subdir, ignore_errors=True)
        else:
            scope_ctx = _bare_env.bare_scope()      # 公共显式套件所有者
            generation = scope_ctx.__enter__()
            unittest.addModuleCleanup(scope_ctx.__exit__, None, None, None)
            root = Path(generation.root)
            os.environ[DATA_ENV] = str(root)
            subdir = root / "s1c-runtime-tests"
            subdir.mkdir(parents=True, exist_ok=True)
            scope = {"borrowed": False, "root": root,
                     "class_dir": subdir, "work_root": subdir / "work",
                     "_scope_ctx": scope_ctx}
            # bare_scope 根由 helper 自收，子目录随根，不单独删。
        # RF4-1：全部资源/键就绪**后**才发布全局指针；发布前先登记绑定代清理
        # （mkdir 失败→本函数抛→真实 doModuleCleanups 只清已建资源，指针未
        # 发布仍 None，可重入新代）。实际 LIFO 登记序（收尾执行序倒推）：
        # env 恢复最先登记（最后执行）→ rmtree/exit → _clear_module_scope →
        # USERS.clear 最后登记（最先执行）。
        scope["work_root"].mkdir(parents=True, exist_ok=True)
        scope["users_db"] = scope["class_dir"] / "users.db"
        unittest.addModuleCleanup(_clear_module_scope, scope)
        unittest.addModuleCleanup(USERS.clear)
        _MODULE_SCOPE = scope
        complete = True
    finally:
        if not complete:
            # pytest setup 失败不会调用 teardown；立即清已登记资源后传播失败。
            unittest.doModuleCleanups()


def tearDownModule():
    """R8：pytest/unittest 共用真实模块收尾；随后 unittest 的空队列调用幂等。"""
    scope = _MODULE_SCOPE
    assert scope is not None, "module teardown requires the active generation"
    unittest.doModuleCleanups()
    state = {
        "version": 1, "pid": os.getpid(), "ppid": os.getppid(),
        "prefix": sys.prefix,
        "scope_cleared": _MODULE_SCOPE is None, "users_empty": not USERS,
        "borrowed": scope["borrowed"],
        "class_dir": str(scope["class_dir"]),
        "class_dir_exists": scope["class_dir"].exists(),
        "root": str(scope["root"]), "root_exists": scope["root"].exists(),
    }
    print("S1C_MODULE_POSTCLEANUP_JSON=" + json.dumps(state, ensure_ascii=True,
                                                   sort_keys=True), flush=True)
    assert state["scope_cleared"] and state["users_empty"], state
    assert not state["class_dir_exists"], state
    assert state["root_exists"] == state["borrowed"], state


class _TSRBox(unittest.TestCase):
    """公共基座：会话根借用/owned scratch → 产品导入 → 箱身份/默认data核验。"""

    @classmethod
    def setUpClass(cls):
        if _MODULE_SCOPE is not None:
            # R2：模块级根生命周期（pytest 借 conftest 会话根 / direct 由
            # bare_scope 持根）——类只取模块路径并按代借用；类级零 env 改写。
            cls._borrowed_root = _MODULE_SCOPE["borrowed"]
            cls.data_root = _MODULE_SCOPE["root"]
            cls._class_dir = _MODULE_SCOPE["class_dir"]
            cls.work_root = _MODULE_SCOPE["work_root"]
            cls.users_db = _MODULE_SCOPE["users_db"]
        else:
            # 无模块 scope＝探针子进程单类直跑（或独立类调用）：类级 owned
            # 兜底（T1 失败恢复证明路径，保留；单类场景无跨类 owner 冲突）。
            cls._prev_env = {key: os.environ.get(key) for key in TRACKED_ENV}
            # 恢复登记先于**任何**环境改写/资源创建——acquire 失败也还原。
            cls.addClassCleanup(_restore_tracked_env, dict(cls._prev_env))
            cls._owned_box = tempfile.mkdtemp(prefix="s1c_runtime_owned_")
            cls.addClassCleanup(shutil.rmtree, cls._owned_box, ignore_errors=True)
            cls.data_root = Path(cls._owned_box)
            cls._class_dir = cls.data_root
            cls.work_root = cls.data_root / "work"
            cls.users_db = cls._class_dir / "users.db"
            cls._class_dir.mkdir(parents=True, exist_ok=True)
            cls.work_root.mkdir(parents=True, exist_ok=True)
            os.environ[DATA_ENV] = str(cls.data_root)
        cls._probe_fail_after_env_write()   # T1 故障注入钩子（缺省 no-op）
        cls._token = _bare_env.acquire()
        cls.addClassCleanup(_bare_env.release, cls._token)

        _import_products()
        # 借用/模块窗口内仅将 DATA 键临时指向类目录供 auth 真实建 users.db，
        # 随即恢复原值（acquire 的身份校验在恢复之后；owned 模式下两者同根）。
        _saved_data_env = os.environ.get(DATA_ENV)
        if str(_saved_data_env) != str(cls._class_dir):
            os.environ[DATA_ENV] = str(cls._class_dir)
        try:
            _ensure_users(cls._class_dir)
        finally:
            if str(os.environ.get(DATA_ENV)) != str(_saved_data_env):
                if _saved_data_env is None:
                    os.environ.pop(DATA_ENV, None)
                else:
                    os.environ[DATA_ENV] = _saved_data_env

        cls.addClassCleanup(cls._post_box_checks)
        print("S1C_TEST_FILE_SHA", _sha256_file(__file__))
        for label, module in (("TSR", tsr), ("COLLAB", collab), ("ARB", sarb),
                              ("LEDGER", ledger), ("CORE", core_mod), ("DBM", dbm)):
            assert module is not None and getattr(module, "__file__", None), \
                f"S1C_SOURCE_{label}: CORE/来源模块必须为真实已导入对象"
            path = Path(module.__file__).resolve()
            print(f"S1C_SOURCE_{label}_SHA", _sha256_file(path), str(path))
        _emit_runtime_snapshot()   # 真实导入完成后即时 emit（运行时实测 hash）
        print("S1C_REPO_ROOT", str(REPO_ROOT))
        print("S1C_CWD", str(Path.cwd()))
        print("S1C_PYTHON", sys.version.split()[0])
        print("S1C_SQLITE_VERSION", sqlite3.sqlite_version)
        print("S1C_ISOLATED_FLAG", sys.flags.isolated)
        print("S1C_DONT_WRITE_BC", sys.flags.dont_write_bytecode)
        print("S1C_DEFAULT_DATA_ABSENT_PRE", not (Path.cwd() / "data").exists())
        assert not (Path.cwd() / "data").exists(), "箱内不得落 default data 目录"
        assert cls.users_db.is_absolute()
        print("S1C_USERS_DB_SHA", _sha256_file(cls.users_db)
              if cls.users_db.exists() else "ABSENT")

    @classmethod
    def _probe_fail_after_env_write(cls):
        """T1 故障注入点（缺省 no-op）：子类覆写以在 env 改写后、acquire 前
        抛错，验证 class cleanup 真实恢复（见 _T1RecoveryProbe/子进程用例）。"""

    @classmethod
    def _post_box_checks(cls):
        gc.collect()
        absent = not (Path.cwd() / "data").exists()
        print("S1C_DEFAULT_DATA_ABSENT_POST", absent)
        assert absent, "箱内不得落 default data 目录"
        print("S1C_USERS_DB_SHA_POST", _sha256_file(cls.users_db)
              if cls.users_db.exists() else "ABSENT")

    def setUp(self):
        self.base = self.work_root / self.id().rsplit(".", 1)[-1]
        self.base.mkdir(parents=True, exist_ok=True)
        self.users_db = self._class_dir / "users.db"
        self.users_sha_pre = _sha256_file(self.users_db)

    # -- 场景构建 helper（全部真实公共 setup API） ---------------------------

    def _info(self, task_dir: Path, task_id: str):
        return types.SimpleNamespace(task_id=task_id, dir=task_dir)

    def _init_task(self, *, task_id="task-0001", owner_username="alice",
                   owner_uid=None) -> Path:
        """真实 initialize_task_store 建协作库；返回 task_dir。"""
        task_dir = self.base / f"{task_id.replace('-', '_')}"
        task_dir.mkdir(parents=True, exist_ok=True)
        collab.initialize_task_store(
            task_dir, task_id=task_id,
            owner_user_id=owner_uid if owner_uid is not None else USERS["alice"],
            owner_username=owner_username)
        return task_dir

    def _grant(self, task_dir: Path, task_id: str, username: str, role: str):
        collab.grant_role(task_dir / "collaboration.db", actor_user_id=USERS["alice"],
                          user_id=USERS[username], role=role, task_id=task_id)

    def _choice(self, task_dir: Path, task_id: str, username: str, role: str):
        """业务角色 choice 由属主配置（API 契约：仅属主可写）。"""
        collab.set_business_role(task_dir / "collaboration.db",
                                 actor_user_id=USERS["alice"], user_id=USERS[username],
                                 business_role=role, task_id=task_id)

    def _screener_db(self, task_dir: Path, username: str, *, articles=(),
                     votes=(), title_pending=(), dups=(), latch=False) -> Path:
        """真实 db.init_db（含账本安装）+ review_workflow.migrate（workflow_state）
        + 真实 schema 直插种子（articles/票/标题待审/latch）。
        votes: {key: (decision, updated_at, screened_at)}；INSERT 触发真实
        trigger 推进 revision（不手写 revision/meta）。"""
        path = task_dir / (tasks_mod.slugify_screener(username) + ".db")
        dbm.init_db(path)
        wf.migrate(path)
        conn = sqlite3.connect(path)
        try:
            base_order = 0
            for order, key in enumerate(articles):
                conn.execute(
                    "INSERT INTO articles (zotero_key, title, is_duplicate_of, import_order)"
                    " VALUES (?, ?, NULL, ?)", (key, f"title-{key}", order))
                base_order = order + 1
            for key, keeper in dups:
                conn.execute(
                    "INSERT INTO articles (zotero_key, title, is_duplicate_of, import_order)"
                    " VALUES (?, ?, ?, ?)", (key, f"title-{key}", keeper, 90 + base_order))
                base_order += 1
            for key in title_pending:
                conn.execute(
                    "INSERT INTO title_duplicate_reviews (review_id, normalized_title,"
                    " status, members_json, keeper_key, decision_by, created_at, updated_at)"
                    " VALUES (?, ?, 'pending', '[]', '', '', '2026-11-15T00:00:00+00:00',"
                    " '2026-11-15T00:00:00+00:00')", (f"rev-{key}", f"norm-{key}"))
                conn.execute(
                    "INSERT INTO title_duplicate_members (review_id, zotero_key,"
                    " imported_in_batch) VALUES (?, ?, 1)", (f"rev-{key}", key))
                conn.execute(
                    "INSERT INTO articles (zotero_key, title, is_duplicate_of, import_order)"
                    " VALUES (?, ?, NULL, ?)", (key, f"title-{key}", 80))
            if latch:
                conn.execute("UPDATE workflow_state SET team_latched = 1 WHERE singleton = 1")
            vote_map = votes if isinstance(votes, dict) else {}
            for key, (decision, updated, screened) in vote_map.items():
                conn.execute(
                    "INSERT INTO decisions (zotero_key, decision, exclusion_reason,"
                    " notes, tags, screened_at, updated_at) VALUES (?, ?, '', '', '', ?, ?)",
                    (key, decision, screened, updated))
            conn.commit()
        finally:
            conn.close()
        return path

    def _lib_rev(self, db_path: Path, stage: str, keys):
        """经真实 vote_snapshot 取 proof 所需 (library_id, {key: revision})。"""
        with closing(sqlite3.connect(db_path)) as conn:
            library_id, rows = ledger.vote_snapshot(conn, stage)
            return library_id, {key: int(rows[key]["revision"]) for key in keys}

    def _append_attest(self, collab_path: Path, task_id: str, *, stage="stage1",
                       row_keys=(), resolutions=(), proof_keys=(),
                       proof_entries=None, required_names=("alice", "bob"),
                       arbitrator_uid=None):
        """真实 prepare+append 写入授权 attestation（caller 事务内，真实持久
        JSON+sha256 链）。proof_entries=None 时按当前账本真实 library/revision
        构造 proof_keys 中各键的全体 required 条目；否则原样使用（供缺条目/
        整键缺失变体）。返回 append 响应 dict。"""
        import pandas as pd
        sarb.prepare_arbitration_attestation_storage(collab_path)
        member_dbs = {name: collab_path.parent / (tasks_mod.slugify_screener(name) + ".db")
                      for name in required_names}
        libs, revs = {}, {}
        for name, path in member_dbs.items():
            if path.exists():
                lib, per = self._lib_rev(path, stage, proof_keys)
                libs[name], revs[name] = lib, per
        proof = {}
        for key in proof_keys:
            if proof_entries is not None:
                proof[key] = proof_entries.get(key, {})
                continue
            proof[key] = {str(USERS[name]): {"library_id": libs[name],
                                             "revision": revs[name][key]}
                          for name in required_names if key in revs.get(name, {})}
        frame_rows = []
        for key in row_keys:
            votes = {}
            for name, path in member_dbs.items():
                if path.exists():
                    with closing(sqlite3.connect(path)) as conn:
                        row = conn.execute(
                            "SELECT decision FROM decisions WHERE zotero_key = ?",
                            (key,)).fetchone()
                    votes[name] = row[0] if row else ""
            values = [v for v in votes.values() if v]
            status = ("agree" if len(set(values)) == 1 else "conflict") if len(values) >= 2 else "incomplete"
            entry = {"zotero_key": key, "status": status, "n_screened": len(values)}
            for name in required_names:
                entry[f"decision_{name}"] = votes.get(name, "")
            frame_rows.append(entry)
        merged = pd.DataFrame(frame_rows)
        conn = sqlite3.connect(collab_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = sarb.append_decision_arbitration_attested(
                conn, stage=stage, merged=merged, resolutions=list(resolutions),
                arbitrator_id=USERS["carol"],
                attestation_meta={"task_id": task_id, "round": None,
                                  "required_names": list(required_names),
                                  "arbitrator_user_id": USERS["carol"],
                                  "arbitrator_role": "reconciler",
                                  "input_proof": proof})
            conn.commit()
        finally:
            conn.close()
        return result

    def _fresh_view(self, task_dir: Path, task_id: str, stage="stage1"):
        with tsr.fresh_team_read_scope(self._info(task_dir, task_id),
                                       accounts_db=self.users_db) as scope:
            return tsr.team_stage_view(scope, stage)

    def _fresh(self, task_dir: Path, task_id: str):
        return tsr.fresh_team_read_scope(self._info(task_dir, task_id),
                                         accounts_db=self.users_db)

    def _expect_tfue(self, callable_, *contains_reasons, tag=""):
        """精确族断言：TeamFlowUnavailableError；可选 reason 白名单。"""
        with self.assertRaises(tsr.TeamFlowUnavailableError, msg=tag) as ctx:
            callable_()
        exc = ctx.exception
        if contains_reasons:
            self.assertIn(exc.reason, contains_reasons,
                          f"{tag}: reason={exc.reason!r} 不在合同预期 {contains_reasons}")
        return exc

    def _listing(self, root: Path):
        return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


# ---------------------------------------------------------------------------
# G1 账户 reader 与全账户 casefold 冲突
# ---------------------------------------------------------------------------

class T1Lifecycle(_TSRBox):
    """T1：生命周期——失败 cleanup 真实恢复（独立子进程，不碰模块缓存）。"""

    def test_setupclass_failure_restores_env_and_owned_root(self):
        """setUpClass 在 env 改写后、acquire 前抛"根不一致" → class cleanup
        必须真实恢复：TRACKED_ENV 全还原、自有 owned 根删除、产品未导入、
        失败本身可见（1 error）。全部断言在**独立子进程**内完成（不靠
        sys.modules 清空/reload 冒充）。"""
        child_tmp = self.base / "child"
        child_tmp.mkdir(parents=True, exist_ok=True)
        child_env = {k: v for k, v in os.environ.items()
                     if k not in TRACKED_ENV and k != "S1C_CHILD_PROBE"}
        for key in ("TEMP", "TMP", "TMPDIR", "RF_R9_TMP", "RF_R9_TMP_ROOT"):
            child_env[key] = str(child_tmp)          # COBOOKSHELF_DATA_DIR 不设→owned 模式
        child_env["S1C_CHILD_PROBE"] = "1"
        child_env["PYTHONDONTWRITEBYTECODE"] = "1"
        proc = subprocess.run(
            [sys.executable, "-I", "-B", str(Path(__file__).resolve())],
            cwd=str(child_tmp), env=child_env, capture_output=True,
            text=True, errors="replace", timeout=180)
        self.assertEqual(proc.returncode, 0,
                         f"T1 child rc={proc.returncode}\nstdout={proc.stdout}\n"
                         f"stderr={proc.stderr}")
        for marker in ("T1_CHILD_RUN_ERRORS=1", "T1_CHILD_ENV_RESTORED=1",
                       "T1_CHILD_SCOPE_POINTER_CLEARED=1",
                       "T1_CHILD_ROOT_REMOVED=1",
                       "T1_CHILD_MODULE_DIR_REMOVED=1",
                       "T1_CHILD_TSR_NOT_IMPORTED=1",
                       "T1_CHILD_ERROR_TEXT_SEEN=1"):
            self.assertIn(marker, proc.stdout, f"T1 缺 {marker}: {proc.stdout}")
        actual_paths = {}
        for label in ("T1_CHILD_ACTUAL_ROOT", "T1_CHILD_ACTUAL_CLASS_DIR"):
            values = [line.split("=", 1)[1] for line in proc.stdout.splitlines()
                      if line.startswith(label + "=")]
            self.assertEqual(len(values), 1, f"T1 缺唯一 {label}: {proc.stdout}")
            self.assertTrue(values[0].strip(), f"T1 {label} 为空")
            actual_paths[label] = Path(values[0]).resolve()
            self.assertTrue(actual_paths[label].is_relative_to(child_tmp.resolve()))
            print(f"RELAY {label}={values[0]}")
        self.assertTrue(actual_paths["T1_CHILD_ACTUAL_CLASS_DIR"].is_relative_to(
            actual_paths["T1_CHILD_ACTUAL_ROOT"]))
        # stderr 为尽力而为（宿主读取线程偶发丢失时不影响"失败可见"判定，
        # 可见性由子进程 stdout 的 T1_CHILD_ERROR_TEXT_SEEN 承载）。
        if proc.stderr:
            self.assertIn("T1 root-inconsistency probe", proc.stderr)


def _make_t1_recovery_probe():
    """R1：探针工厂——类体**局部定义**，模块级 loader 永不发现（无 skip/
    deselect 豁免）；仅 S1C_CHILD_PROBE=1 子进程 __main__ 分支显式构造运行。"""

    class _T1RecoveryProbe(_TSRBox):
        """T1 故障注入：env 改写后、acquire 前抛错（真实失败恢复证明）。
        探针经 TestSuite 执行时模块 scope 已建立；记录真实 helper owner
        根及模块自有目录，并在清理前确认两者存在。"""

        def test_probe_placeholder(self):
            """占位（永不执行）：setUpClass 先抛 → class 级 error 即被测对象。"""

        @classmethod
        def _probe_fail_after_env_write(cls):
            scope = _MODULE_SCOPE
            assert scope is not None and not scope["borrowed"], \
                "T1 probe: 子进程须建立自有模块 scope"
            owner = _bare_env.live_owner()
            assert owner is not None and owner.owned_root, \
                "T1 probe: helper 自有 owner 根必须非空"
            root = Path(owner.root)
            class_dir = scope["class_dir"]
            assert str(root).strip() and str(class_dir).strip()
            assert root == scope["root"] and root.is_dir() and class_dir.is_dir()
            assert class_dir.resolve().is_relative_to(root.resolve())
            globals()["_T1_PROBE_ROOT"] = str(root)
            globals()["_T1_PROBE_CLASS_DIR"] = str(class_dir)
            raise RuntimeError("T1 root-inconsistency probe")

    return _T1RecoveryProbe


class T2RootLifecycle(_TSRBox):
    """R2/TF3：根生命周期——模块共享根正控＋"每类新根第二类被拒"机械化
    记录＋模块 scope 代清理同进程重入。全部子进程执行（不 teardown 父
    67-suite 的模块 scope）。"""

    @staticmethod
    def _child_env(base: Path) -> dict:
        """TF3-1：子进程 env——**保留五 TEMP 键**并显式指向各自专属
        scratch/tmp 子目录（子进程 mkdtemp/bare_scope 全落 scratch，不落
        宿主默认）；仅 DATA 受控移除（owned 语义）；REVIEWFLOW_MODE 沿旧
        口径移除；S1C 专属标记清空（不假造 session/scope 标记）。"""
        child_tmp = base / "tmp"
        child_tmp.mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in os.environ.items()
               if k not in (DATA_ENV, "REVIEWFLOW_MODE", "S1C_CHILD_PROBE")}
        for key in ("TEMP", "TMP", "TMPDIR", "RF_R9_TMP", "RF_R9_TMP_ROOT"):
            env[key] = str(child_tmp)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    @classmethod
    def _relay_diagnostics(cls, stdout: str, parent: Path,
                           path_labels: tuple[str, ...]) -> None:
        """R6：仅转发固定白名单；实际路径须唯一、非空且规范化后落本用例根。"""
        allowed_paths = ("CHILD_ACTUAL_TEMP", "CHILD_ACTUAL_ROOT",
                         "REENTRY_SCOPE1", "REENTRY_SCOPE2")
        assert all(label in allowed_paths for label in path_labels)
        lines = stdout.splitlines()
        for label in path_labels:
            values = [line.split("=", 1)[1] for line in lines
                      if line.startswith(label + "=")]
            assert len(values) == 1, f"缺唯一实际路径诊断 {label}: {values!r}"
            assert Path(values[0]).is_absolute(), f"{label} 必须为绝对路径"
            cls._assert_within(values[0], parent, label)
            print(f"RELAY {label}={values[0]}", flush=True)
        for line in lines:
            if line == "REENTRY_OK=1":
                print(f"RELAY {line}", flush=True)

    @contextmanager
    def _child_capture(self, proc: subprocess.CompletedProcess, parent: Path,
                       path_labels: tuple[str, ...]):
        """R6：核验/断言任一步失败均留完整 raw；finally 不吞异常或改退出码。"""
        complete = False
        try:
            self._relay_diagnostics(proc.stdout, parent, path_labels)
            yield
            complete = True
        finally:
            if proc.returncode != 0 or not complete:
                print(f"CHILD_RAW_BEGIN parent={parent} rc={proc.returncode}", flush=True)
                print("CHILD_STDOUT_BEGIN", flush=True)
                print(proc.stdout, end="", flush=True)
                print("\nCHILD_STDOUT_END\nCHILD_STDERR_BEGIN", flush=True)
                print(proc.stderr, end="", flush=True)
                print("\nCHILD_STDERR_END\nCHILD_RAW_END", flush=True)

    @staticmethod
    def _assert_within(actual: str, parent: Path, what: str) -> None:
        """RF4-2：规范化 Path 关系断言——``actual`` 必须是 ``parent`` 的
        后代（resolve 后 is_relative_to，非字符串包含）。"""
        assert actual and actual.strip(), f"{what} 必须非空实际值"
        assert Path(actual).resolve().is_relative_to(parent.resolve()), \
            f"{what}={actual!r} 不是 {parent} 的后代（规范化路径关系）"

    def test_lazy_owner_second_root_rejected_documentary(self):
        """残留动因记录（公共 API、不改 helper）：惰性进程 owner 根 A 借用
        归还后，DATA 改指根 B 再借用 → 身份校验 RuntimeError（"每类各自
        新根"旧形制的真实拒绝面）。独立子进程执行；断言子进程五 TEMP 键
        实际生效（gettempdir 落本用例 scratch）与 acquire 根记录。"""
        child = self.base / "doc-second-root"
        child.mkdir(parents=True, exist_ok=True)
        script = (
            "import sys, os, tempfile\n"
            "sys.path[:0] = [r'%s', r'%s']\n"
            "import _bare_env\n"
            "t = tempfile.gettempdir()\n"
            "assert t and t.strip(), 'CHILD_ACTUAL_TEMP empty'\n"
            "print('CHILD_ACTUAL_TEMP=', t, sep='')\n"
            "tok = _bare_env.acquire()\n"
            "r = str(_bare_env.live_owner().root)\n"
            "assert r and r.strip(), 'CHILD_ACTUAL_ROOT empty'\n"
            "print('CHILD_ACTUAL_ROOT=', r, sep='')\n"
            "_bare_env.release(tok)\n"
            "os.environ['COBOOKSHELF_DATA_DIR'] = tempfile.mkdtemp(prefix='other_root_')\n"
            "try:\n"
            "    _bare_env.acquire()\n"
            "except RuntimeError as exc:\n"
            "    print('DOC_SECOND_ROOT_REJECTED=1')\n"
            "    sys.exit(0)\n"
            "print('DOC_SECOND_ROOT_REJECTED=0')\n"
            "sys.exit(1)\n" % (str(REPO_ROOT), str(TESTS_DIR))
        )
        proc = subprocess.run([sys.executable, "-I", "-B", "-c", script],
                              cwd=str(child), capture_output=True, text=True,
                              errors="replace", timeout=180,
                              env=self._child_env(child))
        with self._child_capture(proc, child,
                                 ("CHILD_ACTUAL_TEMP", "CHILD_ACTUAL_ROOT")):
            self.assertEqual(proc.returncode, 0, f"doc child rc={proc.returncode}")
            self.assertIn("DOC_SECOND_ROOT_REJECTED=1", proc.stdout)

    def test_two_classes_shared_module_scope_owned(self):
        """正控（独立子进程）：同一直接 unittest 进程连跑两个真实类——模块
        ``bare_scope`` 共享根下两类全部通过（无第二类拒绝、零 skip）；子进程
        显式打印 actual temp/root 并由父以规范化路径关系断言（RF4-2）。"""
        # R7：从本 run 的绝对 TEMP 起短路径，避免嵌套两层完整方法名使
        # SQLite rollback journal 超过 Windows 路径限制；仅清本次自建目录。
        child_parent = Path(os.environ.get("TEMP") or tempfile.gettempdir())
        self.assertTrue(child_parent.is_absolute() and child_parent.is_dir())
        base = Path(tempfile.mkdtemp(prefix="tc-", dir=str(child_parent)))
        self.addCleanup(shutil.rmtree, base)  # 创建后立即登记，先于 child/DB 启动
        self._assert_within(str(base), child_parent, "two-class 自有短目录")
        test_abs = Path(__file__).resolve()
        proc = subprocess.run(
            [sys.executable, "-I", "-B", "-c",
             "import sys, tempfile, unittest\n"
             f"sys.path[:0] = [{str(test_abs.parent.parent.parent)!r}, {str(test_abs.parent)!r}]\n"
             "import test_2_1_6_team_screening_runtime as mod\n"
             "t = tempfile.gettempdir()\n"
             "assert t and t.strip(), 'CHILD_ACTUAL_TEMP empty'\n"
             "print('CHILD_ACTUAL_TEMP=', t, sep='')\n"
             "mod.setUpModule()\n"
             "scope = mod._MODULE_SCOPE\n"
             "owner = mod._bare_env.live_owner()\n"
             "assert scope is not None and owner is not None and owner.owned_root\n"
             "r = str(owner.root)\n"
             "assert r.strip() and owner.root == scope['root'] and owner.root.is_dir()\n"
             "print('CHILD_ACTUAL_ROOT=', r, sep='')\n"
             "unittest.main(module=mod, argv=['s1c', 'G1AccountsAndConflict', 'G3RolesRequired', '-v'])\n"],
            cwd=str(base), env=self._child_env(base), capture_output=True,
            text=True, errors="replace", timeout=300)
        with self._child_capture(proc, base,
                                 ("CHILD_ACTUAL_TEMP", "CHILD_ACTUAL_ROOT")):
            self.assertEqual(proc.returncode, 0, f"two-class child rc={proc.returncode}")
            self.assertIn("OK", proc.stderr)
            self.assertNotIn(" skipped", proc.stderr)

    def test_module_scope_generation_cleanup_and_reentry(self):
        """TF3-2：同进程两次 setUpModule→doModuleCleanups（独立子进程执行，
        不 teardown 父 suite 模块 scope）——清理后 ``_MODULE_SCOPE is None``、
        自有子目录已删、env 已还原；再次 setUpModule 得**全新有效 scope**
        （新 uuid 子目录，不命中 stale return）。"""
        child = self.base / "module-reentry"
        child.mkdir(parents=True, exist_ok=True)
        script = (
            "import sys, os, unittest\n"
            "sys.path[:0] = [r'%s', r'%s']\n"
            "import test_2_1_6_team_screening_runtime as mod\n"
            "assert mod._MODULE_SCOPE is None, 'pre: no scope'\n"
            "mod.setUpModule()\n"
            "s1 = mod._MODULE_SCOPE\n"
            "assert s1 is not None and s1['class_dir'].is_dir()\n"
            "print('REENTRY_SCOPE1=', s1['class_dir'], sep='')\n"
            "unittest.doModuleCleanups()   # 真实模块清理（USERS→代清理→目录→env）\n"
            "assert mod._MODULE_SCOPE is None, 'cleanup must clear generation pointer'\n"
            "assert not s1['class_dir'].exists(), 'own subdir removed'\n"
            "assert not os.environ.get('COBOOKSHELF_DATA_DIR'), 'env restored'\n"
            "mod.setUpModule()\n"
            "s2 = mod._MODULE_SCOPE\n"
            "assert s2 is not None and s2 is not s1, 'fresh scope, not stale return'\n"
            "assert s2['class_dir'].is_dir() and s2['class_dir'] != s1['class_dir']\n"
            "print('REENTRY_SCOPE2=', s2['class_dir'], sep='')\n"
            "print('REENTRY_OK=1')\n"
            "unittest.doModuleCleanups()\n"
            "assert mod._MODULE_SCOPE is None\n"
            "sys.exit(0)\n" % (str(REPO_ROOT), str(TESTS_DIR))
        )
        proc = subprocess.run([sys.executable, "-I", "-B", "-c", script],
                              cwd=str(child), capture_output=True, text=True,
                              errors="replace", timeout=180,
                              env=self._child_env(child))
        with self._child_capture(proc, child, ("REENTRY_SCOPE1", "REENTRY_SCOPE2")):
            self.assertEqual(proc.returncode, 0, f"reentry child rc={proc.returncode}")
            self.assertIn("REENTRY_OK=1", proc.stdout)

    def test_module_scope_partial_init_failcleanup(self):
        """RF4-1：两分支 ``work_root.mkdir`` 受控失败（窄 patch Path.mkdir
        **仅命中该代 work_root** 时 raise，不误伤其余 tmp/mkdtemp——独立
        受控子进程内执行，非真实权限拒绝）→ setUpModule 抛 → 真实
        doModuleCleanups 只清已建资源 → ``_MODULE_SCOPE`` 未发布仍 None →
        再 setUpModule 得全新有效 scope（可重入）。"""
        child = self.base / "module-partial-init"
        child.mkdir(parents=True, exist_ok=True)
        for branch in ("borrowed", "owned"):
            sub = child / branch
            sub.mkdir(parents=True, exist_ok=True)
            # 受控注入：只在该代 work_root 路径上使 mkdir 抛（窄命中）
            if branch == "borrowed":
                env_setup = f"os.environ['COBOOKSHELF_DATA_DIR'] = r'{sub / 'preset-root'}'\n"
                preset_mkdir = ("(sub / 'preset-root').mkdir(parents=True, exist_ok=True)\n"
                                "(sub / 'preset-root' / 'keep.txt').write_bytes(b'borrowed')\n")
            else:
                env_setup = "os.environ.pop('COBOOKSHELF_DATA_DIR', None)\n"
                preset_mkdir = ""
            script = (
                "import sys, os, unittest\n"
                "from pathlib import Path\n"
                "from unittest import mock\n"
                "sys.path[:0] = [r'%s', r'%s']\n"
                f"{env_setup}"
                f"sub = Path(r'{sub}')\n"
                f"{preset_mkdir}"
                "import test_2_1_6_team_screening_runtime as mod\n"
                "assert mod._MODULE_SCOPE is None\n"
                f"borrowed = {branch == 'borrowed'!r}\n"
                "entry = {key: os.environ.get(key) for key in mod.TRACKED_ENV}\n"
                "orig_mkdir = Path.mkdir\n"
                "failure = OSError('R5 controlled work_root mkdir failure')\n"
                "hits = []\n"
                "def failing_mkdir(self, *args, **kwargs):\n"
                "    name = self.parent.name\n"
                "    matches_name = name.startswith('s1c-runtime-tests-') if borrowed else name == 's1c-runtime-tests'\n"
                "    if self.name == 'work' and matches_name:\n"
                "        root = Path(os.environ[mod.DATA_ENV])\n"
                "        if self.parent.parent == root:\n"
                "            assert root.is_dir() and self.parent.is_dir() and not self.exists()\n"
                "            assert root.resolve().is_relative_to(sub.resolve())\n"
                "            if borrowed:\n"
                "                assert root == sub / 'preset-root'\n"
                "            else:\n"
                "                owner = mod._bare_env.live_owner()\n"
                "                assert owner is not None and owner.owned_root and owner.root == root\n"
                "            hits.append((root, self.parent, self))\n"
                "            raise failure\n"
                "    return orig_mkdir(self, *args, **kwargs)\n"
                "with mock.patch.object(Path, 'mkdir', failing_mkdir):\n"
                "    try:\n"
                "        mod.setUpModule()\n"
                "    except OSError as exc:\n"
                "        assert exc is failure, 'wrong failure'\n"
                "    else:\n"
                "        raise AssertionError('expected controlled failure')\n"
                "assert len(hits) == 1, 'controlled failure must hit exactly once'\n"
                "root1, dir1, work1 = hits[0]\n"
                "assert mod._MODULE_SCOPE is None, 'must not publish stale pointer on fail'\n"
                "assert not dir1.exists() and not work1.exists(), 'setup failure must clean immediately'\n"
                "assert (root1.is_dir() if borrowed else not root1.exists())\n"
                "assert {key: os.environ.get(key) for key in mod.TRACKED_ENV} == entry\n"
                "unittest.doModuleCleanups()  # already empty; repeated cleanup is harmless\n"
                "assert mod._MODULE_SCOPE is None and not dir1.exists() and not work1.exists()\n"
                "assert (root1.is_dir() if borrowed else not root1.exists())\n"
                "if borrowed:\n"
                "    assert (root1 / 'keep.txt').read_bytes() == b'borrowed'\n"
                "assert {key: os.environ.get(key) for key in mod.TRACKED_ENV} == entry\n"
                "mod.setUpModule()\n"
                "s2 = mod._MODULE_SCOPE\n"
                "assert s2 is not None and s2['work_root'].is_dir()\n"
                "assert s2['class_dir'] != dir1 and s2['borrowed'] == borrowed\n"
                "print('REENTRY_SCOPE2=', s2['class_dir'], sep='')\n"
                "unittest.doModuleCleanups()\n"
                "assert mod._MODULE_SCOPE is None and not s2['class_dir'].exists()\n"
                "assert (root1.is_dir() if borrowed else not s2['root'].exists())\n"
                "if borrowed:\n"
                "    assert (root1 / 'keep.txt').read_bytes() == b'borrowed'\n"
                "assert {key: os.environ.get(key) for key in mod.TRACKED_ENV} == entry\n"
                f"print('PARTIAL_INIT_OK_{branch}=1')\n"
                "sys.exit(0)\n" % (str(REPO_ROOT), str(TESTS_DIR))
            )
            proc = subprocess.run([sys.executable, "-I", "-B", "-c", script],
                                  cwd=str(sub), capture_output=True, text=True,
                                  errors="replace", timeout=180,
                                  env=self._child_env(sub))
            with self._child_capture(proc, sub, ("REENTRY_SCOPE2",)):
                self.assertEqual(proc.returncode, 0,
                                 f"partial-init [{branch}] rc={proc.returncode}")
                self.assertIn(f"PARTIAL_INIT_OK_{branch}=1", proc.stdout)


class G1AccountsAndConflict(_TSRBox):

    def test_accounts_readonly_positive_full_flow(self):
        tid = "g1-ok"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "bob", "coder")
        self._screener_db(task_dir, "alice", articles=("K1",),
                          votes={"K1": ("include", "2026-11-14T09:00:00+00:00",
                                        "2026-11-14T08:00:00+00:00")})
        self._screener_db(task_dir, "bob",
                          votes={"K1": ("include", "2026-11-14T09:05:00+00:00",
                                        "2026-11-14T08:05:00+00:00")})
        view = self._fresh_view(task_dir, tid)
        self.assertEqual(view.stage, "stage1")
        self.assertEqual(view.task_id, tid)
        self.assertTrue(view.config.ok)
        self.assertEqual(view.statuses["K1"].status, "final_include")
        users_sha_post = _sha256_file(self.users_db)
        self.assertEqual(self.users_sha_pre, users_sha_post,
                         "G1: accounts 只读——users.db 主文件哈希不得变化")

    def test_accounts_unreadable_family(self):
        tid = "g1-bad"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._screener_db(task_dir, "alice", articles=("K1",))
        cases = {}
        cases["missing"] = self.base / "no-such-users.db"          # 缺文件
        dir_case = self.base / "users-dir"; dir_case.mkdir(exist_ok=True)
        cases["directory"] = dir_case                               # 目录冒充
        broken = self.base / "users-broken.db"
        broken.write_bytes(b"not a sqlite file at all")             # 坏字节
        cases["garbage"] = broken
        dropped = self.base / "users-nousers.db"                    # 真实 users 库删表（synthetic）
        dropped.write_bytes(self.users_db.read_bytes())
        dconn = sqlite3.connect(dropped)
        try:
            dconn.execute("DROP TABLE users"); dconn.commit()
        finally:
            dconn.close()
        cases["no_users_table"] = dropped
        for label, accounts_db in cases.items():
            with self.subTest(case=label):
                with tsr.fresh_team_read_scope(self._info(task_dir, tid),
                                               accounts_db=accounts_db) as scope:
                    exc = self._expect_tfue(
                        lambda s=scope: tsr.team_stage_view(s, "stage1"),
                        "accounts_unreadable", tag=f"G1 accounts 故障族 {label}")
                self.assertEqual(exc.reason, "accounts_unreadable")

    def test_casefold_conflict_participant_typed(self):
        tid = "g1-conflict"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "dave", "coder")
        self._screener_db(task_dir, "alice", articles=("K1",))
        self._screener_db(task_dir, "dave",
                          votes={"K1": ("include", "2026-11-14T09:00:00+00:00",
                                        "2026-11-14T08:00:00+00:00")})
        exc = self._expect_tfue(lambda: self._fresh_view(task_dir, tid),
                                "storage_conflict", tag="G1 casefold 冲突")
        self.assertEqual(exc.member, "dave")
        self.assertEqual(exc.params.get("member"), "dave")

    def test_unique_slug_member_no_false_conflict(self):
        """防 set 语义假阳：成员自身 casefold 键当然在全账户键集合内——
        唯一 slug 成员不得因此被误判 storage_conflict（判定=键→id 集合>1）。"""
        tid = "g1-unique"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "carol", "coder")
        self._screener_db(task_dir, "alice", articles=("K1",),
                          votes={"K1": ("include", "2026-11-14T09:00:00+00:00",
                                        "2026-11-14T08:00:00+00:00")})
        self._screener_db(task_dir, "carol",
                          votes={"K1": ("include", "2026-11-14T09:00:00+00:00",
                                        "2026-11-14T08:00:00+00:00")})
        view = self._fresh_view(task_dir, tid)   # Dave/DAVE 等他者撞名不累及 carol/alice
        self.assertEqual(view.statuses["K1"].status, "final_include")

    def test_three_accounts_same_casefold_still_conflict(self):
        tid = "g1-three"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "dave", "coder")   # dave/Dave/DAVE 同键 3 id
        self._screener_db(task_dir, "alice", articles=("K1",))
        self._screener_db(task_dir, "dave",
                          votes={"K1": ("include", "2026-11-14T09:00:00+00:00",
                                        "2026-11-14T08:00:00+00:00")})
        self._expect_tfue(lambda: self._fresh_view(task_dir, tid),
                          "storage_conflict", tag="G1 三账号同 casefold 仍冲突")

    def test_reserved_collab_name_participant(self):
        tid = "g1-reserved"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        collab.grant_role(task_dir / "collaboration.db", actor_user_id=USERS["alice"],
                          user_id=USERS["collaboration"], role="coder", task_id=tid)
        self._screener_db(task_dir, "alice", articles=("K1",))
        exc = self._expect_tfue(lambda: self._fresh_view(task_dir, tid),
                                "storage_conflict", tag="G1 保留名 collaboration.db")
        self.assertEqual(exc.member, "collaboration")


# ---------------------------------------------------------------------------
# G2 身份 / 路径 / containment
# ---------------------------------------------------------------------------

class G2IdentityAndPaths(_TSRBox):

    def test_cjk_and_fallback_slug_pipeline(self):
        tid = "g2-slug"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "张三研", "coder")
        self._screener_db(task_dir, "alice", articles=("K1",),
                          votes={"K1": ("include", "2026-11-14T09:00:00+00:00",
                                        "2026-11-14T08:00:00+00:00")})
        zhang_db = self._screener_db(task_dir, "张三研",
                                     votes={"K1": ("include", "2026-11-14T09:05:00+00:00",
                                                   "2026-11-14T08:05:00+00:00")})
        self.assertEqual(zhang_db.name, tasks_mod.slugify_screener("张三研") + ".db")
        view = self._fresh_view(task_dir, tid)
        self.assertEqual(view.statuses["K1"].status, "final_include")

    def test_identity_unconfirmed_owner_snapshot_renamed(self):
        """属主当前账号名(alice)与协作库 owner 快照名(al1ce)映射不同库路径 →
        identity_unconfirmed，不改读新空库。"""
        tid = "g2-identity"
        task_dir = self._init_task(task_id=tid, owner_username="al1ce")
        self._choice(task_dir, tid, "alice", "coder")
        self._screener_db(task_dir, "alice", articles=("K1",),
                          votes={"K1": ("include", "2026-11-14T09:00:00+00:00",
                                        "2026-11-14T08:00:00+00:00")})
        exc = self._expect_tfue(lambda: self._fresh_view(task_dir, tid),
                                "identity_unconfirmed", tag="G2 owner 快照名分叉")
        self.assertEqual(exc.member, "alice")

    def test_f3_containment_mutation_outside_redirect(self):
        """[MUTATION] F3 containment 逻辑反例：受控 Path.resolve 接缝把成员库
        victim.db 重定向到**任务目录外**的真实 scratch 库（非实机 symlink；
        Windows 无特权 symlink 未测，仅记录）。合同：fresh 打开前做与 safe_path
        同义的真实路径 containment → typed 拒绝，不得读取目录外库。"""
        tid = "g3-containment"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "victim", "coder")
        self._screener_db(task_dir, "alice", articles=("K1",))
        self._screener_db(task_dir, "victim",
                          votes={"K1": ("include", "2026-11-14T09:00:00+00:00",
                                        "2026-11-14T08:00:00+00:00")})
        outside_dir = self.work_root / "f3-outside"
        outside_dir.mkdir(exist_ok=True)
        outside_db = self._screener_db_raw(outside_dir, "sneaky",
                                           votes={"K1": ("include",
                                                         "2026-11-14T10:00:00+00:00",
                                                         "2026-11-14T08:00:00+00:00")})
        original_resolve = Path.resolve

        def _redirect(self_path, *args, **kwargs):
            real = original_resolve(self_path, *args, **kwargs)
            if real.name == "victim.db":
                return outside_db.resolve()   # 模拟目录外 symlink 目标
            return real

        with mock.patch.object(Path, "resolve", _redirect):
            # T4：收窄为合同 typed 分类（reason=member_db_unreadable），不再
            # 容任意 ValueError。
            exc = self._expect_tfue(lambda: self._fresh_view(task_dir, tid),
                                    "member_db_unreadable",
                                    tag="F3[MUTATION] 目录外成员库 containment")
            self.assertEqual(exc.reason, "member_db_unreadable")

    def test_f3_containment_positive_control_inside(self):
        """[MUTATION] 正对照：同一接缝把 victim.db 重定向到任务目录内的真实
        库 → 正常装载（接缝不误伤合法路径；containment 只拒目录外）。"""
        tid = "g3-containment-pos"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "victim", "coder")
        self._screener_db(task_dir, "alice", articles=("K1",),
                          votes={"K1": ("include", "2026-11-14T09:00:00+00:00",
                                        "2026-11-14T08:00:00+00:00")})
        victim_real = self._screener_db(
            task_dir, "victim",
            votes={"K1": ("include", "2026-11-14T09:05:00+00:00",
                          "2026-11-14T08:05:00+00:00")})
        original_resolve = Path.resolve
        victim_resolved = victim_real.resolve()   # 补丁外预解析（防补丁内递归）

        def _redirect_inside(self_path, *args, **kwargs):
            real = original_resolve(self_path, *args, **kwargs)
            if real.name == "victim.db":
                return victim_resolved
            return real

        with mock.patch.object(Path, "resolve", _redirect_inside):
            view = self._fresh_view(task_dir, tid)
        self.assertEqual(view.statuses["K1"].status, "final_include")

    def _screener_db_raw(self, target_dir: Path, username: str, *, votes=()) -> Path:
        """在任意目录（含任务目录外）按真实产品链建筛选员库（仅 F3 反例用）。"""
        path = target_dir / (tasks_mod.slugify_screener(username) + ".db")
        dbm.init_db(path)
        conn = sqlite3.connect(path)
        try:
            conn.execute("INSERT INTO articles (zotero_key, title, import_order)"
                         " VALUES ('K1', 'title-K1', 0)")
            for key, (decision, updated, screened) in votes.items():
                conn.execute("INSERT INTO decisions (zotero_key, decision,"
                             " exclusion_reason, notes, tags, screened_at, updated_at)"
                             " VALUES (?, ?, '', '', '', ?, ?)",
                             (key, decision, screened, updated))
            conn.commit()
        finally:
            conn.close()
        return path


# ---------------------------------------------------------------------------
# G3 角色 / required / known-team 分类
# ---------------------------------------------------------------------------

class G3RolesRequired(_TSRBox):

    def _team_of_two(self, tid, *, reconciler="carol"):
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "bob", "coder")
        self._grant(task_dir, tid, reconciler, "reconciler")
        return task_dir

    def test_role_reader_tuple_and_row_compat(self):
        tid = "g3-reader"
        task_dir = self._team_of_two(tid)
        collab_path = task_dir / "collaboration.db"
        for factory in (None, sqlite3.Row):
            with self.subTest(factory=factory):
                conn = sqlite3.connect(collab_path)
                if factory is not None:
                    conn.row_factory = factory
                try:
                    state = collab.read_team_role_state_on(conn, task_id=tid)
                    self.assertEqual(conn.row_factory, factory,
                                     "G3: reader 不得改 caller row_factory")
                finally:
                    conn.close()
                self.assertEqual(state["classification"], "owner_match")
                self.assertEqual(state["owner"]["owner_username"], "alice")
                self.assertEqual(state["owner"]["owner_user_id"], USERS["alice"])
                self.assertEqual(state["owner"]["task_id"], tid)
                self.assertTrue(state["has_non_owner_grants"])
                uids = [p["user_id"] for p in state["participants"]]
                self.assertEqual(uids, sorted(uids))
                self.assertIn(USERS["alice"], uids)
                by_uid = {p["user_id"]: p for p in state["participants"]}
                bob = by_uid[USERS["bob"]]
                self.assertTrue(bob["participant"] and bob["role"] == "coder")
                self.assertTrue(bob["required_screener"] and bob["config"] == "ok")
                self.assertFalse(bob["is_owner"])
                carol = by_uid[USERS["carol"]]
                self.assertEqual(carol["role"], "reconciler")
                self.assertFalse(carol["screening_enabled"],
                                 "G3: 新 reconciler 授权默认关（回填后口径）")
                self.assertFalse(carol["required_screener"])
                owner = by_uid[USERS["alice"]]
                self.assertTrue(owner["is_owner"] and owner["role"] == "coder")

    def test_task_kind_branches(self):
        owner_match = {"classification": "owner_match", "has_non_owner_grants": False}
        self.assertEqual(tsr.task_kind(owner_match, owner_latched=True), "team")
        self.assertEqual(tsr.task_kind(
            {"classification": "owner_match", "has_non_owner_grants": True},
            owner_latched=False), "team")
        self.assertEqual(tsr.task_kind(
            {"classification": "owner_match", "has_non_owner_grants": False},
            owner_latched=False), "personal")
        self.assertEqual(tsr.task_kind(
            {"classification": "legacy_uninitialized"}, owner_latched=False), "legacy")
        with self.assertRaises(tsr.TeamFlowUnavailableError) as ctx:
            tsr.task_kind({"classification": "unconfirmed"}, owner_latched=False)
        self.assertEqual(ctx.exception.reason, "unconfirmed_task_state")
        with self.assertRaises(tsr.TeamFlowUnavailableError):
            tsr.task_kind({}, owner_latched=False)

    def test_awaiting_config_owner_unconfigured_keeps_participant(self):
        tid = "g3-config"
        task_dir = self._init_task(task_id=tid)   # owner 无 choice 无 grant
        self._screener_db(task_dir, "alice", articles=("K1",))
        view = self._fresh_view(task_dir, tid)
        self.assertFalse(view.config.ok)
        self.assertTrue(view.config.empty_required)
        self.assertIn("alice", view.config.unconfigured)
        self.assertEqual(view.statuses["K1"].status, "awaiting_config")
        self.assertEqual(
            tsr.team_item_gate_reason(view.statuses["K1"]),
            "team_item_awaiting_config")

    def test_single_required_consensus_and_missing(self):
        tid = "g3-single"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")   # 恰 1 required
        self._screener_db(task_dir, "alice", articles=("K1", "K2"),
                          votes={"K1": ("include", "2026-11-14T09:00:00+00:00",
                                        "2026-11-14T08:00:00+00:00")})
        view = self._fresh_view(task_dir, tid)
        self.assertTrue(view.config.ok)
        self.assertEqual(view.config.required, ("alice",))
        self.assertEqual(view.statuses["K1"].status, "final_include")
        self.assertEqual(view.statuses["K2"].status, "awaiting_members")
        self.assertEqual(view.statuses["K2"].pending_reasons, (("alice", "missing_vote"),))
        self.assertEqual(tsr.stage_final_keys(view), ["K1"])
        self.assertEqual(tsr.team_item_gate_reason(view.statuses["K2"]),
                         "team_item_awaiting_members")

    def test_dual_grant_conflict_then_choice_resolves(self):
        tid = "g3-dual"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "bob", "coder")
        self._grant(task_dir, tid, "bob", "reconciler")
        self._screener_db(task_dir, "alice", articles=("K1",))
        self._screener_db(task_dir, "bob", votes={"K1": ("include", "2026-11-14T09:00:00+00:00",
                                                         "2026-11-14T08:00:00+00:00")})
        with closing(sqlite3.connect(task_dir / "collaboration.db")) as conn:
            state = collab.read_team_role_state_on(conn, task_id=tid)
        bob = {p["user_id"]: p for p in state["participants"]}[USERS["bob"]]
        self.assertTrue(bob["conflict"] and bob["role"] is None)
        self.assertEqual(bob["config"], "conflict")
        # 单一显式 choice 已解析成功时不得强造 conflict
        self._choice(task_dir, tid, "bob", "coder")
        with closing(sqlite3.connect(task_dir / "collaboration.db")) as conn:
            state2 = collab.read_team_role_state_on(conn, task_id=tid)
        bob2 = {p["user_id"]: p for p in state2["participants"]}[USERS["bob"]]
        self.assertEqual(bob2["role"], "coder")
        self.assertFalse(bob2["conflict"])

    def test_stale_choice_not_silent_fallback(self):
        tid = "g3-stale"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "bob", "coder")
        self._grant(task_dir, tid, "bob", "reconciler")
        self._choice(task_dir, tid, "bob", "coder")
        collab.revoke_role(task_dir / "collaboration.db", actor_user_id=USERS["alice"],
                           user_id=USERS["bob"], role="coder", task_id=tid)
        with closing(sqlite3.connect(task_dir / "collaboration.db")) as conn:
            state = collab.read_team_role_state_on(conn, task_id=tid)
        bob = {p["user_id"]: p for p in state["participants"]}[USERS["bob"]]
        self.assertEqual(bob["source"], "stale_choice")
        self.assertIsNone(bob["role"])
        self.assertFalse(bob["required_screener"])
        self.assertNotEqual(bob["role"], "reconciler", "失效 choice 不得回退另一旧 grant")

    def test_member_unresolved_keeps_participant(self):
        tid = "g3-unresolved"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        collab.grant_role(task_dir / "collaboration.db", actor_user_id=USERS["alice"],
                          user_id=9999, role="coder", task_id=tid)
        self._screener_db(task_dir, "alice", articles=("K1",))
        exc = self._expect_tfue(lambda: self._fresh_view(task_dir, tid),
                                "member_unresolved", tag="G3 缺账号行")
        self.assertEqual(exc.participant, 9999)

    def test_latch_survives_grant_clear(self):
        tid = "g3-latch"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "bob", "coder")
        self._screener_db(task_dir, "alice", articles=("K1",), latch=True,
                          votes={"K1": ("include", "2026-11-14T09:00:00+00:00",
                                        "2026-11-14T08:00:00+00:00")})
        self._screener_db(task_dir, "bob", votes={"K1": ("include", "2026-11-14T09:05:00+00:00",
                                                         "2026-11-14T08:05:00+00:00")})
        collab.revoke_role(task_dir / "collaboration.db", actor_user_id=USERS["alice"],
                           user_id=USERS["bob"], role="coder", task_id=tid)
        with closing(sqlite3.connect(task_dir / "collaboration.db")) as conn:
            state = collab.read_team_role_state_on(conn, task_id=tid)
        self.assertFalse(state["has_non_owner_grants"])
        self.assertEqual(tsr.task_kind(state, owner_latched=True), "team",
                         "G3: 清空 grant 不抹 latch，known-team 仍 team")
        view = self._fresh_view(task_dir, tid)   # 入口不降级
        self.assertEqual(view.statuses["K1"].status, "final_include")

    def test_unconfirmed_collab_entry_fail_closed(self):
        """有 owner 表但 singleton 行被删（synthetic 损坏）→ unconfirmed，
        task_kind typed 错误、视图入口 fail-closed，不降 legacy/personal。"""
        tid = "g3-unconfirmed"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._screener_db(task_dir, "alice", articles=("K1",))
        conn = sqlite3.connect(task_dir / "collaboration.db")
        try:
            conn.execute("DELETE FROM collaboration_task WHERE singleton = 1")
            conn.commit()
        finally:
            conn.close()
        with closing(sqlite3.connect(task_dir / "collaboration.db")) as conn:
            state = collab.read_team_role_state_on(conn, task_id=tid)
        self.assertEqual(state["classification"], "unconfirmed")
        with self.assertRaises(tsr.TeamFlowUnavailableError):
            tsr.task_kind(state, owner_latched=False)
        exc = self._expect_tfue(lambda: self._fresh_view(task_dir, tid),
                                "unconfirmed_task_state", tag="G3 unconfirmed 入口")

    def test_f5_bad_owner_schema_typed(self):
        """[F5a][synthetic] collaboration_task 缺 owner_user_id 列（真实文件上
        DROP+重建畸形表）→ 结构损坏必须 typed/unconfirmed，不得裸
        OperationalError。"""
        tid = "g3-f5a"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._screener_db(task_dir, "alice", articles=("K1",))
        conn = sqlite3.connect(task_dir / "collaboration.db")
        try:
            conn.execute("DROP TABLE collaboration_task")
            conn.execute("CREATE TABLE collaboration_task ("
                         " singleton INTEGER PRIMARY KEY CHECK(singleton=1),"
                         " task_id TEXT NOT NULL UNIQUE, owner_username TEXT NOT NULL,"
                         " created_at TEXT NOT NULL)")
            conn.execute("INSERT INTO collaboration_task (singleton, task_id,"
                         " owner_username, created_at) VALUES (1, ?, 'alice', 'x')", (tid,))
            conn.commit()
        finally:
            conn.close()
        with tsr.fresh_team_read_scope(self._info(task_dir, tid),
                                       accounts_db=self.users_db) as scope:
            # 合同（Terra F5）：结构损坏→unconfirmed 分类（形状不变）或 typed，
            # 决不裸 OperationalError/DatabaseError。
            try:
                state = scope.role_state()
            except tsr.TeamFlowUnavailableError:
                return
            except sqlite3.Error as exc:
                self.fail(f"F5a: 坏 schema 裸抛 SQLite 错误未分类: {exc!r}")
            self.assertEqual(state["classification"], "unconfirmed",
                             "F5a: 坏 schema 须 unconfirmed/typed，非裸 SQLite 错误")

    def test_f5_grant_uid_zero_not_owner_match(self):
        """[F5b][synthetic] 合成损坏 grants 行 user_id=0（经 ignore_check
        模拟无约束旧库）→ 聚合前验证正整数，不得以 uid0 参与者返回 owner_match。"""
        tid = "g3-f5b"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._screener_db(task_dir, "alice", articles=("K1",))
        conn = sqlite3.connect(task_dir / "collaboration.db")
        try:
            conn.execute("PRAGMA ignore_check_constraints = ON")
            conn.execute("INSERT INTO collaboration_role_grants (user_id, role,"
                         " granted_by, granted_at) VALUES (0, 'coder', 1, 'x')")
            conn.commit()
        finally:
            conn.close()
        with tsr.fresh_team_read_scope(self._info(task_dir, tid),
                                       accounts_db=self.users_db) as scope:
            try:
                state = scope.role_state()
            except tsr.TeamFlowUnavailableError:
                return  # typed 拒绝亦符合合同
            uids = [p["user_id"] for p in state["participants"]]
            self.assertNotIn(0, uids, "F5b: uid0 不得作为参与者进入 owner_match")
            self.assertEqual(state["classification"], "unconfirmed")

    def test_f6_settings_missing_after_backfill_failclosed(self):
        """[F6][synthetic] 回填完成标记在场而 collaboration_member_settings
        表被删 → 严格分类：reader `unconfirmed`；视图层 typed
        `unconfirmed_task_state`（T4：删除 owner_match 宽容旁路）。"""
        tid = "g3-f6"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "carol", "reconciler")
        self._screener_db(task_dir, "alice", articles=("K1",))
        conn = sqlite3.connect(task_dir / "collaboration.db")
        try:
            marker = conn.execute(
                "SELECT COUNT(*) FROM collaboration_settings WHERE key=?",
                (collab.SCREENING_BACKFILL_KEY,)).fetchone()[0]
            self.assertEqual(marker, 1, "F6 前置：初始化后回填标记必须在场")
            conn.execute("DROP TABLE collaboration_member_settings")
            conn.commit()
        finally:
            conn.close()
        with tsr.fresh_team_read_scope(self._info(task_dir, tid),
                                       accounts_db=self.users_db) as scope:
            state = scope.role_state()
            self.assertEqual(state["classification"], "unconfirmed",
                             "F6: 已回填缺 settings 表必须是 unconfirmed（无旁路）")
        exc = self._expect_tfue(lambda: self._fresh_view(task_dir, tid),
                                "unconfirmed_task_state", tag="F6 视图层 typed")
        self.assertEqual(exc.reason, "unconfirmed_task_state")


    def test_r2_3_real_fraction_grant_strict(self):
        """[R2-3] 正常 schema INTEGER affinity 下 REAL 1.5 grant（CHECK(>0) 可过）
        不得被 int() 截断成 owner id 合并参与者；聚合前须严格正整数原值
        （owner ID 同规则）→ unconfirmed/typed，而非 owner_match-personal。"""
        tid = "g3-r23"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._screener_db(task_dir, "alice", articles=("K1",))
        conn = sqlite3.connect(task_dir / "collaboration.db")
        try:
            conn.execute("INSERT INTO collaboration_role_grants (user_id, role,"
                         " granted_by, granted_at) VALUES (1.5, 'coder', 1, 'x')")
            conn.commit()
        finally:
            conn.close()
        with tsr.fresh_team_read_scope(self._info(task_dir, tid),
                                       accounts_db=self.users_db) as scope:
            try:
                state = scope.role_state()
            except tsr.TeamFlowUnavailableError:
                return
            uids = [p_["user_id"] for p_ in state["participants"]]
            self.assertNotEqual(uids, [USERS["alice"]],
                                "R2-3: REAL 1.5 不得截断合并进 owner（须严格整数校验）")
            self.assertEqual(state["classification"], "unconfirmed")

    def test_r2_5_grants_missing_role_column_typed(self):
        """[R2-5][synthetic] grants 表缺 role 列（合法 owner 行在）→ 公开 role
        reader 不得裸 OperationalError；须覆盖 resolver 必需结构（unconfirmed）
        或 typed（busy/IO 不得伪 unconfirmed）。"""
        tid = "g3-r25"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._screener_db(task_dir, "alice", articles=("K1",))
        conn = sqlite3.connect(task_dir / "collaboration.db")
        try:
            conn.execute("DROP TABLE collaboration_role_grants")
            conn.execute("CREATE TABLE collaboration_role_grants (user_id INTEGER)")
            conn.execute("INSERT INTO collaboration_role_grants (user_id) VALUES (?)",
                         (USERS["bob"],))
            conn.commit()
        finally:
            conn.close()
        probe = sqlite3.connect(task_dir / "collaboration.db")
        try:
            try:
                state = collab.read_team_role_state_on(probe, task_id=tid)
            except sqlite3.OperationalError as exc:
                self.fail(f"R2-5: 公开 reader 裸抛 OperationalError: {exc}")
            except tsr.TeamFlowUnavailableError:
                return
        finally:
            probe.close()
        self.assertEqual(state["classification"], "unconfirmed",
                         "R2-5: 缺 role 列属结构损坏须 unconfirmed")


# ---------------------------------------------------------------------------
# G4 fresh scope
# ---------------------------------------------------------------------------

class G4FreshScope(_TSRBox):

    def _flow_two(self, tid):
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "bob", "coder")
        self._screener_db(task_dir, "alice", articles=("K1", "K2"),
                          votes={"K1": ("include", "2026-11-14T09:00:00+00:00",
                                        "2026-11-14T08:00:00+00:00"),
                                 "K2": ("exclude", "2026-11-14T09:00:00+00:00",
                                        "2026-11-14T08:00:00+00:00")})
        self._screener_db(task_dir, "bob",
                          votes={"K1": ("include", "2026-11-14T09:05:00+00:00",
                                        "2026-11-14T08:05:00+00:00"),
                                 "K2": ("include", "2026-11-14T09:05:00+00:00",
                                        "2026-11-14T08:05:00+00:00")})
        return task_dir

    def test_owner_required_same_db_legal_dedup(self):
        """owner 亦是 required 成员：catalog/票/角色同库属合法去重（非 F1 冲突）。"""
        tid = "g4-dedup"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")   # owner 即唯一 required
        self._screener_db(task_dir, "alice", articles=("K1",),
                          votes={"K1": ("include", "2026-11-14T09:00:00+00:00",
                                        "2026-11-14T08:00:00+00:00")})
        view = self._fresh_view(task_dir, tid)
        self.assertEqual(view.statuses["K1"].status, "final_include")

    def _logical_snapshot(self, path: Path) -> dict:
        """T6/R5：相关库**逻辑快照**——sqlite_master 对象（type/name/**原始
        sql 文本**，仅 strip 外缘空白、不压缩内部空白以掩语义差）＋逐表行集。
        允许 SQLite 合法 WAL/SHM 侧文件；不以主文件字节哈希冒充零写证明。"""
        if not path.exists():
            return {"__absent__": True}
        with closing(sqlite3.connect(path, timeout=15)) as conn:
            objects = sorted(
                (row[0], row[1], (row[2] or "").strip())
                for row in conn.execute(
                    "SELECT type, name, sql FROM sqlite_master"))
            tables = {}
            for kind, name, _sql in objects:
                if kind != "table":
                    continue
                rows = sorted(tuple(map(repr, row)) for row in conn.execute(
                    f'SELECT * FROM "{name}"'))
                tables[name] = rows
            return {"objects": objects, "tables": tables}

    def test_zero_creation_sidecars_recorded(self):
        tid = "g4-zero"
        task_dir = self._flow_two(tid)
        listing_pre = self._listing(task_dir)
        users_pre = self._listing(self._class_dir)
        # T6：本 fixture 全部在 scope 输入库（alice[owner=成员同库，物理去重]
        # /bob/collab/users）逻辑 schema/行 快照——按实际 scope 物理去重枚举，
        # 不硬钉数量、不遗漏 owner 源；非两主文件字节哈希。
        targets = list({p.resolve(): p for p in
                        [task_dir / "alice.db", task_dir / "bob.db",
                         task_dir / "collaboration.db", self.users_db]}.values())
        snap_pre = {str(p): self._logical_snapshot(p) for p in targets}
        default_pre = not (Path.cwd() / "data").exists()
        view = self._fresh_view(task_dir, tid)
        self.assertEqual(view.statuses["K1"].status, "final_include")
        listing_post = self._listing(task_dir)
        users_post = self._listing(self._class_dir)
        new_task = set(listing_post) - set(listing_pre)
        bad = [name for name in new_task
               if not name.endswith((".db-wal", ".db-shm"))]
        self.assertEqual(bad, [], f"G4: 仅允许 WAL 侧文件，出现 {bad}")
        new_users = set(users_post) - set(users_pre)
        bad_users = [name for name in new_users
                     if not name.endswith((".db-wal", ".db-shm"))]
        self.assertEqual(bad_users, [], f"G4: users 域仅允许 WAL 侧文件，出现 {bad_users}")
        for path in targets:
            self.assertEqual(self._logical_snapshot(path), snap_pre[str(path)],
                             f"T6: 相关库逻辑状态被读路径改变: {path.name}")
        # R5 反控一（独立 dummy 克隆，不动活 append-only 库）：ADD COLUMN →
        # 快照必差（含列定义/sql 变化可见）。
        clone = self.base / "snapctl-addcol.db"
        shutil.copy2(targets[0], clone)
        s1 = self._logical_snapshot(clone)
        with closing(sqlite3.connect(clone)) as conn:
            conn.execute("ALTER TABLE decisions ADD COLUMN snapctl_probe TEXT")
            conn.commit()
        self.assertNotEqual(self._logical_snapshot(clone), s1,
                            "R5: ADD COLUMN 必须改变逻辑快照（反控不假绿）")
        # R5 反控二：同名 trigger 以不同 SQL 重建（DROP 后 CREATE；SQLite 无
        # CREATE OR REPLACE）→ 快照必差；且仅内部空白差异的变体也必差
        # （sql 文本只 strip 外缘、不压缩内部空白）。
        trig = self.base / "snapctl-trigger.db"
        shutil.copy2(targets[0], trig)
        t1 = self._logical_snapshot(trig)
        with closing(sqlite3.connect(trig)) as conn:
            conn.execute("DROP TRIGGER decisions_tvr_insert")
            conn.execute(
                "CREATE TRIGGER decisions_tvr_insert AFTER INSERT ON decisions "
                "BEGIN INSERT INTO team_vote_revisions(stage,item_key,revision) "
                "VALUES('stage1', NEW.zotero_key, 1) ON CONFLICT(stage,item_key) "
                "DO UPDATE SET revision=revision+2; END")   # 语义不同的同体名
            conn.commit()
        t2 = self._logical_snapshot(trig)
        self.assertNotEqual(t2, t1, "R5: 同名异 SQL trigger 必须改变快照")
        with closing(sqlite3.connect(trig)) as conn:        # 仅内部空白差异变体
            conn.execute("DROP TRIGGER decisions_tvr_insert")
            conn.execute(
                "CREATE TRIGGER decisions_tvr_insert AFTER INSERT ON decisions "
                "BEGIN    INSERT INTO team_vote_revisions(stage,item_key,revision) "
                "VALUES('stage1', NEW.zotero_key, 1)   ON CONFLICT(stage,item_key) "
                "DO UPDATE SET revision=revision+2; END")
            conn.commit()
        t3 = self._logical_snapshot(trig)
        self.assertNotEqual(t3, t2,
                            "R5: 内部空白差异不被压缩掩蔽（原文本精确比较）")
        self.assertTrue(default_pre and not (Path.cwd() / "data").exists(),
                        "G4: 默认 data 目录前后均不得出现")

    def test_missing_vote_missing_file_zero_creation(self):
        tid = "g4-missing"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "bob", "coder")
        self._screener_db(task_dir, "alice", articles=("K1",),
                          votes={"K1": ("include", "2026-11-14T09:00:00+00:00",
                                        "2026-11-14T08:00:00+00:00")})
        self.assertFalse((task_dir / "bob.db").exists())
        view = self._fresh_view(task_dir, tid)
        item = view.statuses["K1"]
        self.assertEqual(item.status, "awaiting_members")
        self.assertEqual(item.pending_reasons, (("bob", "missing_vote"),))
        self.assertFalse((task_dir / "bob.db").exists(),
                         "G4: fresh 读不得创建缺失成员库")

    def test_member_db_directory_and_garbage_typed(self):
        tid = "g4-badb"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "bob", "coder")
        self._screener_db(task_dir, "alice", articles=("K1",))
        as_dir = task_dir / "bob.db"
        as_dir.mkdir()
        exc = self._expect_tfue(lambda: self._fresh_view(task_dir, tid),
                                "member_db_unreadable", tag="G4 目录冒充成员库")
        self.assertEqual(exc.member, "bob")
        as_dir.rmdir()
        as_dir.write_bytes(b"\xde\xad\xbe\xef" * 32)
        # T4：收窄——坏字节成员库必须映射 typed member_db_unreadable（产品
        # _own_conn 的 BEGIN DEFERRED 即触发 "file is not a database" 并按
        # 该 reason 包装）；不再容裸 sqlite3.DatabaseError。
        exc = self._expect_tfue(lambda: self._fresh_view(task_dir, tid),
                                "member_db_unreadable", tag="G4 坏字节成员库")
        self.assertEqual(exc.reason, "member_db_unreadable")
        self.assertEqual(exc.member, "bob")

    def test_p1_borrowed_garbage_member_typed_and_passthrough(self):
        """TF3-3（正式 P-1 borrowed 回归，经**完整 scope 现有 API**，不直调
        私有 helper）：真实 borrowed 坏字节成员库 → typed
        ``member_db_unreadable``/member=bob；全部 caller 事务存活、配置不
        变；窄注入 ProgrammingError 与 VoteLedgerUnavailable 经同一
        ``team_stage_view`` 路径**同对象透传**（证明捕获面只窄在
        sqlite3.DatabaseError）。"""
        tid = "g4-p1b"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "bob", "coder")
        self._screener_db(task_dir, "alice", articles=("K1",),
                          votes={"K1": ("include", "2026-11-14T09:00:00+00:00",
                                        "2026-11-14T08:00:00+00:00")})
        bob_db = task_dir / "bob.db"
        bob_db.write_bytes(b"\xde\xad\xbe\xef" * 32)   # 真实坏字节文件
        # RF4-5：每连接**创建返回后立即登记** per-conn cleanup（BEGIN 前即
        # 受保护——连接或 BEGIN 中途失败不漏已打开连接；不重置所有权）。
        def _safe_fini(conn):
            try:
                if conn.in_transaction:
                    conn.rollback()
            finally:
                conn.close()
        conns = {}
        for path in (self.users_db, task_dir / "collaboration.db",
                     task_dir / "alice.db"):
            conn = sqlite3.connect(path, timeout=15)
            conns[path] = conn
            self.addCleanup(_safe_fini, conn)   # 创建→立即登记→再 BEGIN
            conn.execute("BEGIN")
        # 坏库连接：connect 返回后**立即登记**（BEGIN DEFERRED 之前）；BEGIN
        # **DEFERRED**（IMMEDIATE 可能提前读页成 fixture 错；DEFERRED 延迟
        # 读页——坏字节在首个真实读爆）。
        bob_conn = sqlite3.connect(bob_db, timeout=15)
        conns[bob_db] = bob_conn
        self.addCleanup(_safe_fini, bob_conn)   # BEGIN 前即受保护
        bob_conn.execute("BEGIN DEFERRED")
        info = self._info(task_dir, tid)

        def _borrowed_view():
            scope = tsr.borrowed_team_read_scope(
                info, accounts_path=self.users_db,
                collab_path=task_dir / "collaboration.db",
                owner_path=task_dir / "alice.db",
                member_paths={USERS["alice"]: task_dir / "alice.db",
                              USERS["bob"]: bob_db},
                connections=conns, missing_members=frozenset())
            return tsr.team_stage_view(scope, "stage1")

        snapshot_cfg = {p: (c.in_transaction, c.row_factory)
                        for p, c in conns.items()}
        exc = self._expect_tfue(_borrowed_view, "member_db_unreadable",
                                tag="P-1 borrowed 坏字节")
        self.assertEqual(exc.member, "bob")
        for path, conn in conns.items():
            self.assertTrue(conn.in_transaction,
                            f"P-1: caller 事务被终结: {path}")
            self.assertEqual((conn.in_transaction, conn.row_factory),
                             snapshot_cfg[path],
                             f"P-1: caller 配置被改动: {path}")

        # 窄注入透传一：ProgrammingError（调用写法缺陷）先于 DatabaseError 放行
        prog_err = sqlite3.ProgrammingError("S1C injected programming error")
        with mock.patch.object(tsr, "vote_snapshot", side_effect=prog_err):
            with self.assertRaises(sqlite3.ProgrammingError) as ctx_prog:
                _borrowed_view()
            self.assertIs(ctx_prog.exception, prog_err,
                          "P-1: ProgrammingError 必须同对象透传")
        # 窄注入透传二：VoteLedgerUnavailable 完全不拦（同对象上抛）
        vlu = ledger.VoteLedgerUnavailable("meta_corrupt", member="bob")
        with mock.patch.object(tsr, "vote_snapshot", side_effect=vlu):
            with self.assertRaises(ledger.VoteLedgerUnavailable) as ctx_vlu:
                _borrowed_view()
            self.assertIs(ctx_vlu.exception, vlu,
                          "P-1: VoteLedgerUnavailable 必须同对象透传")
        # 注入作用域已退出：后续真实读取恢复 typed 行为（复核一次）
        exc2 = self._expect_tfue(_borrowed_view, "member_db_unreadable",
                                 tag="P-1 注入后恢复")
        self.assertEqual(exc2.member, "bob")
        for path, conn in conns.items():
            self.assertTrue(conn.in_transaction)

    def test_collab_missing_entry_legacy_not_missing_vote(self):
        tid = "g4-legacy"
        task_dir = self.base / f"{tid.replace('-', '_')}"
        task_dir.mkdir(parents=True, exist_ok=True)   # 无 collaboration.db
        self._screener_db(task_dir, "alice", articles=("K1",))
        with tsr.fresh_team_read_scope(self._info(task_dir, tid),
                                       accounts_db=self.users_db) as scope:
            state = scope.role_state()
            self.assertEqual(state["classification"], "legacy_uninitialized")
            self.assertEqual(tsr.task_kind(state, owner_latched=False), "legacy")
        exc = self._expect_tfue(lambda: self._fresh_view(task_dir, tid),
                                "unconfirmed_task_state", tag="G4 legacy 入口 fail-closed")
        self.assertIn("legacy", exc.detail)

    def test_f4_collab_directory_not_legacy(self):
        """[F4a] collaboration.db 为目录（无真实 store）→ 辅助分类不得报
        legacy（须 typed）；合同：仅首次 stat 明确 FileNotFound 才允许未初始化。"""
        tid = "g4-f4a"
        task_dir = self.base / f"{tid.replace('-', '_')}"
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "collaboration.db").mkdir()   # 目录冒充协作库（synthetic 场景）
        self._screener_db(task_dir, "alice", articles=("K1",))
        with tsr.fresh_team_read_scope(self._info(task_dir, tid),
                                       accounts_db=self.users_db) as scope:
            with self.assertRaises(tsr.TeamFlowUnavailableError,
                                   msg="F4a: 目录协作库不得折叠为 legacy"):
                scope.role_state()

    def test_f4_owner_catalog_missing_latched_typed(self):
        """[F4b] owner catalog 文件缺失 → latched 不得合成为 False（须 typed
        owner_db_unreadable）；键域读取同 fail-closed。"""
        tid = "g4-f4b"
        task_dir = self._init_task(task_id=tid)
        self._grant(task_dir, tid, "bob", "coder")
        self._screener_db(task_dir, "bob",
                          votes={"K1": ("include", "2026-11-14T09:00:00+00:00",
                                        "2026-11-14T08:00:00+00:00")})
        with tsr.fresh_team_read_scope(self._info(task_dir, tid),
                                       accounts_db=self.users_db) as scope:
            with self.assertRaises(tsr.TeamFlowUnavailableError) as ctx:
                scope.owner_latched()
            self.assertEqual(ctx.exception.reason, "owner_db_unreadable",
                             "F4b: catalog 缺失不得合成 latched=False")
            with self.assertRaises(tsr.TeamFlowUnavailableError) as ctx2:
                scope.canonical_keys()
            self.assertEqual(ctx2.exception.reason, "owner_db_unreadable")

    def test_f4_invalid_latch_value_typed(self):
        """[F4c][synthetic] team_latched 非法值（2）→ 来源错误，不得静默 False。"""
        tid = "g4-f4c"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._screener_db(task_dir, "alice", articles=("K1",))
        conn = sqlite3.connect(task_dir / "alice.db")
        try:
            conn.execute("UPDATE workflow_state SET team_latched = 2 WHERE singleton = 1")
            conn.commit()
        finally:
            conn.close()
        with tsr.fresh_team_read_scope(self._info(task_dir, tid),
                                       accounts_db=self.users_db) as scope:
            with self.assertRaises(tsr.TeamFlowUnavailableError) as ctx:
                scope.owner_latched()
            self.assertEqual(ctx.exception.reason, "owner_db_unreadable")

    def test_f1_hardlink_two_accounts_same_physical_typed(self):
        """[F1] Windows 硬链接：alice.db 与成员 bob.db 同一物理文件（不同
        resolve 路径）→ 不得按两独立票库计票得出伪共识；合同要求身份/存储
        冲突 typed（owner catalog 与本人票共库仍属合法去重，见上方 dedup 用例）。"""
        tid = "g4-f1"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "bob", "coder")
        alice_db = self._screener_db(task_dir, "alice", articles=("K1",),
                                     votes={"K1": ("include", "2026-11-14T09:00:00+00:00",
                                                   "2026-11-14T08:00:00+00:00")})
        bob_db = task_dir / "bob.db"
        os.link(alice_db, bob_db)   # 真实硬链接：samefile=True、resolve 路径不同
        try:
            with self.assertRaises(tsr.TeamFlowUnavailableError,
                                   msg="F1: 两账户同物理票库须 typed，不得伪共识") as ctx:
                self._fresh_view(task_dir, tid)
            self.assertIn(ctx.exception.reason,
                          ("storage_conflict", "identity_unconfirmed"),
                          f"F1: reason={ctx.exception.reason!r} 不在身份/存储冲突族")
        finally:
            try:
                bob_db.unlink()
            except OSError:
                pass


    def test_r2_1_collab_hardlink_member_purpose_conflict(self):
        """[R2-1][synthetic 混合 store] 协作库（安装 ledger+include 票）与成员库
        硬链接同一物理文件：物理身份必须登记用途——collab 与任何 owner catalog/
        票库同物理身份须 storage_conflict（同 owner UID catalog+本人票仍合法，
        见 G4 dedup 用例）。"""
        tid = "g4-r21"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "bob", "coder")
        collab_path = task_dir / "collaboration.db"
        dbm.init_db(collab_path)   # 真实链：把 screening 表并入协作库并装账本
        conn = sqlite3.connect(collab_path)
        try:
            conn.execute("INSERT INTO articles (zotero_key, title, import_order)"
                         " VALUES ('K1', 'title-K1', 0)")
            conn.execute("INSERT INTO decisions (zotero_key, decision, exclusion_reason,"
                         " notes, tags, screened_at, updated_at)"
                         " VALUES ('K1', 'include', '', '', '',"
                         " '2026-11-14T08:00:00+00:00', '2026-11-14T09:00:00+00:00')")
            conn.commit()
        finally:
            conn.close()
        self._screener_db(task_dir, "alice",
                          votes={"K1": ("include", "2026-11-14T09:00:00+00:00",
                                        "2026-11-14T08:00:00+00:00")})
        bob_db = task_dir / "bob.db"
        os.link(collab_path, bob_db)   # 成员库 = 协作库物理别名（samefile）
        try:
            with self.assertRaises(tsr.TeamFlowUnavailableError,
                                   msg="R2-1: collab×成员库同物理身份须 typed") as ctx:
                self._fresh_view(task_dir, tid)
            self.assertEqual(ctx.exception.reason, "storage_conflict")
        finally:
            try:
                bob_db.unlink()
            except OSError:
                pass

    def test_r2_2_ino0_unverifiable_identity_failclosed(self):
        """[R2-2][MUTATION] 受控模拟：两票路径 stat st_ino=0（平台身份不可证）+
        物理硬链接同库 → 合同要求 failclosed（不得仅凭路径不同信任分开计票）。
        本机真实 inode 非零，本例为受控变异模拟，不声称实机 inode0。"""
        tid = "g4-r22"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "bob", "coder")
        alice_db = self._screener_db(task_dir, "alice", articles=("K1",),
                                     votes={"K1": ("include", "2026-11-14T09:00:00+00:00",
                                                   "2026-11-14T08:00:00+00:00")})
        bob_db = task_dir / "bob.db"
        os.link(alice_db, bob_db)
        try:
            original_stat = Path.stat

            def _ino0_stat(self_path, *args, **kwargs):
                real = original_stat(self_path, *args, **kwargs)
                if self_path.name in ("alice.db", "bob.db"):
                    return os.stat_result((real.st_mode, 0, real.st_dev, real.st_nlink,
                                           real.st_uid, real.st_gid, real.st_size,
                                           real.st_atime_ns, real.st_mtime_ns,
                                           real.st_ctime_ns))
                return real

            with mock.patch.object(Path, "stat", _ino0_stat):
                with self.assertRaises(tsr.TeamFlowUnavailableError,
                                       msg="R2-2: 身份不可证须 failclosed，不伪共识"):
                    self._fresh_view(task_dir, tid)
        finally:
            try:
                bob_db.unlink()
            except OSError:
                pass

    def test_r2_4_workflow_state_empty_singleton_typed(self):
        """[R2-4][synthetic] workflow_state 表在而 singleton 行被清 → latched
        不得合成为 False；须 owner_db_unreadable（确证旧版无表兼容另分）。"""
        tid = "g4-r24"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._screener_db(task_dir, "alice", articles=("K1",))
        conn = sqlite3.connect(task_dir / "alice.db")
        try:
            conn.execute("DELETE FROM workflow_state WHERE singleton = 1")
            conn.commit()
        finally:
            conn.close()
        with tsr.fresh_team_read_scope(self._info(task_dir, tid),
                                       accounts_db=self.users_db) as scope:
            with self.assertRaises(tsr.TeamFlowUnavailableError) as ctx:
                scope.owner_latched()
            self.assertEqual(ctx.exception.reason, "owner_db_unreadable")


# ---------------------------------------------------------------------------
# G5 borrowed scope
# ---------------------------------------------------------------------------

class G5Borrowed(_TSRBox):

    def _flow_with_attest(self, tid):
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "bob", "coder")
        self._screener_db(task_dir, "alice", articles=("K1", "K2", "K3"),
                          votes={"K1": ("include", "2026-11-14T09:00:00+00:00",
                                        "2026-11-14T08:00:00+00:00"),
                                 "K2": ("exclude", "2026-11-14T09:00:00+00:00",
                                        "2026-11-14T08:00:00+00:00"),
                                 "K3": ("include", "2026-11-14T09:00:00+00:00",
                                        "2026-11-14T08:00:00+00:00")})
        self._screener_db(task_dir, "bob",
                          votes={"K1": ("include", "2026-11-14T09:05:00+00:00",
                                        "2026-11-14T08:05:00+00:00"),
                                 "K2": ("include", "2026-11-14T09:05:00+00:00",
                                        "2026-11-14T08:05:00+00:00"),
                                 "K3": ("exclude", "2026-11-14T09:05:00+00:00",
                                        "2026-11-14T08:05:00+00:00")})
        result = self._append_attest(
            task_dir / "collaboration.db", tid, row_keys=("K2", "K3"),
            resolutions=({"zotero_key": "K2", "decision": "include", "rationale": "t"},
                         {"zotero_key": "K3", "decision": "include", "rationale": "t"}),
            proof_keys=("K2", "K3"))
        return task_dir, result

    def _open_conns(self, task_dir, *, accounts=True):
        collab_path = task_dir / "collaboration.db"
        owner_path = task_dir / "alice.db"
        member_bob = task_dir / "bob.db"
        conns = {}
        paths = {}
        mapping = [("accounts", self.users_db if accounts else collab_path),
                   ("collab", collab_path), ("owner", owner_path),
                   ("bob", member_bob)]
        for label, path in mapping:
            conn = sqlite3.connect(path, timeout=15)
            conn.execute("BEGIN")   # caller 真实事务（合同：每个已有 caller 事务）
            conns[path] = conn
            paths[label] = path
        return conns, paths

    def test_uncommitted_votes_visible_from_caller_conn(self):
        tid = "g5-uncommitted"
        task_dir, _att = self._flow_with_attest(tid)
        conns, paths = self._open_conns(task_dir)
        for path, conn in conns.items():
            self.addCleanup(conn.close)
        bob_conn = conns[paths["bob"]]
        bob_conn.execute("UPDATE decisions SET decision='exclude',"
                         " updated_at='2026-11-14T11:00:00+00:00' WHERE zotero_key='K2'")
        scope = tsr.borrowed_team_read_scope(
            self._info(task_dir, tid),
            accounts_path=paths["accounts"], collab_path=paths["collab"],
            owner_path=paths["owner"],
            member_paths={USERS["alice"]: paths["owner"], USERS["bob"]: paths["bob"]},
            connections=conns, missing_members=frozenset())
        view = tsr.team_stage_view(scope, "stage1")
        # 未提交的 K2 真实从 caller conn 读取（磁盘上仍是 rev1 include）
        stale_keys = {(a, k, r) for (a, k, r) in view.loader_stale}
        self.assertTrue(any(r == "vote_revision_changed" and k == "K2"
                            for (_, k, r) in stale_keys),
                        f"G5: 未提交 revision 前进须逐键 stale: {view.loader_stale}")
        # 证明对 K2 失配后回落共识语义：alice=exclude、bob 未提交改 exclude
        # → agree exclude（B 键变化不整记录废除，仅本键失配）。
        self.assertEqual(view.statuses["K2"].status, "final_exclude")
        self.assertNotIn("K2", view.applied)
        self.assertIn("K3", view.applied, "G5: B 键变化不废 A 键适用性")
        self.assertTrue(bob_conn.in_transaction, "G5: caller 事务不得被结束")
        row = bob_conn.execute(
            "SELECT decision FROM decisions WHERE zotero_key='K2'").fetchone()
        self.assertEqual(row[0], "exclude", "G5: caller 须仍可见自己未提交写")
        bob_conn.rollback()
        fresh_view = self._fresh_view(task_dir, tid)
        self.assertIn("K2", fresh_view.applied, "G5: 回滚后磁盘旧态恢复 applied")

    def test_uncommitted_account_conflict_read_from_conn(self):
        tid = "g5-accounts"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "bob", "coder")
        self._screener_db(task_dir, "alice", articles=("K1",))
        self._screener_db(task_dir, "bob", votes={"K1": ("include", "2026-11-14T09:00:00+00:00",
                                                         "2026-11-14T08:00:00+00:00")})
        conns, paths = self._open_conns(task_dir)
        for conn in conns.values():
            self.addCleanup(conn.close)
        conns[paths["accounts"]].execute(
            "INSERT INTO users (username, password_hash, created_at)"
            " VALUES ('Bob', 'x', '2026-11-15T00:00:00+00:00')")   # 未提交撞 casefold
        scope = tsr.borrowed_team_read_scope(
            self._info(task_dir, tid),
            accounts_path=paths["accounts"], collab_path=paths["collab"],
            owner_path=paths["owner"], member_paths={USERS["alice"]: paths["owner"], USERS["bob"]: paths["bob"]},
            connections=conns, missing_members=frozenset())
        exc = self._expect_tfue(lambda: tsr.team_stage_view(scope, "stage1"),
                                "storage_conflict", tag="G5 未提交账户冲突须 conn 可见")
        self.assertEqual(exc.member, "bob")

    def test_f2_conns_without_txn_rejected_at_construction(self):
        """[F2] borrowed 收到的每个必需连接都必须已持 caller 事务；构造即拒绝
        且零 SQL 事务控制（不暗 BEGIN/不收尾）。"""
        tid = "g5-f2"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "bob", "coder")
        self._screener_db(task_dir, "alice", articles=("K1",))
        self._screener_db(task_dir, "bob", votes={"K1": ("include", "2026-11-14T09:00:00+00:00",
                                                         "2026-11-14T08:00:00+00:00")})
        collab_path = task_dir / "collaboration.db"
        owner_path = task_dir / "alice.db"
        member_bob = task_dir / "bob.db"
        conns = {}
        for path in (self.users_db, collab_path, owner_path, member_bob):
            conn = sqlite3.connect(path, timeout=15)   # 故意无 BEGIN
            conns[path] = conn
            self.addCleanup(conn.close)
        with self.assertRaises(tsr.TeamFlowUnavailableError,
                               msg="F2: 无事务连接必须构造即 connection_contract") as ctx:
            tsr.borrowed_team_read_scope(
                self._info(task_dir, tid),
                accounts_path=self.users_db, collab_path=collab_path,
                owner_path=owner_path, member_paths={USERS["bob"]: member_bob},
                connections=conns, missing_members=frozenset())
        self.assertEqual(ctx.exception.reason, "connection_contract")
        for path, conn in conns.items():
            self.assertFalse(conn.in_transaction,
                             f"F2: 构造拒绝不得施加事务 {path.name}")
            self.assertIsNotNone(conn.execute("SELECT 1").fetchone(),
                                 "F2: 连接必须原样存活可复用")

    def test_connection_contract_missing_mapping(self):
        tid = "g5-contract"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "bob", "coder")
        self._screener_db(task_dir, "alice", articles=("K1",))
        self._screener_db(task_dir, "bob", votes={"K1": ("include", "2026-11-14T09:00:00+00:00",
                                                         "2026-11-14T08:00:00+00:00")})
        conns, paths = self._open_conns(task_dir)
        for conn in conns.values():
            self.addCleanup(conn.close)
        with self.subTest(case="owner 缺键"):
            with self.assertRaises(tsr.TeamFlowUnavailableError) as ctx:
                tsr.borrowed_team_read_scope(
                    self._info(task_dir, tid),
                    accounts_path=paths["accounts"], collab_path=paths["collab"],
                    owner_path=task_dir / "ghost-owner.db",
                    member_paths={USERS["alice"]: paths["owner"], USERS["bob"]: paths["bob"]},
                    connections=conns, missing_members=frozenset())
            self.assertEqual(ctx.exception.reason, "connection_contract")
        with self.subTest(case="成员缺连接且不在 missing"):
            with self.assertRaises(tsr.TeamFlowUnavailableError) as ctx2:
                tsr.borrowed_team_read_scope(
                    self._info(task_dir, tid),
                    accounts_path=paths["accounts"], collab_path=paths["collab"],
                    owner_path=paths["owner"],
                    member_paths={USERS["bob"]: task_dir / "ghost-bob.db"},
                    connections=conns, missing_members=frozenset())
            self.assertEqual(ctx2.exception.reason, "connection_contract")
        with self.subTest(case="connections 值非 Connection"):
            with self.assertRaises(tsr.TeamFlowUnavailableError) as ctx3:
                tsr.borrowed_team_read_scope(
                    self._info(task_dir, tid),
                    accounts_path=paths["accounts"], collab_path=paths["collab"],
                    owner_path=paths["owner"],
                    member_paths={USERS["alice"]: paths["owner"], USERS["bob"]: paths["bob"]},
                    connections={**conns, paths["bob"]: "not-a-conn"},
                    missing_members=frozenset())
            self.assertEqual(ctx3.exception.reason, "connection_contract")

    def test_missing_members_semantics_and_zero_fs(self):
        """missing_members 由窗口确证传入→missing_vote；路径可不存在的零 FS
        证明（连接映射即全部输入）。"""
        tid = "g5-zerofs"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "bob", "coder")
        self._screener_db(task_dir, "alice", articles=("K1",),
                          votes={"K1": ("include", "2026-11-14T09:00:00+00:00",
                                        "2026-11-14T08:00:00+00:00")})
        self._screener_db(task_dir, "bob",
                          votes={"K1": ("include", "2026-11-14T09:05:00+00:00",
                                        "2026-11-14T08:05:00+00:00")})
        never = self.base / "never-exists"
        conns, paths = self._open_conns(task_dir, accounts=False)
        for conn in conns.values():
            self.addCleanup(conn.close)
        # accounts 用真实 users 连接；owner/member/collab 用假路径+真连接
        acc_conn = sqlite3.connect(self.users_db, timeout=15)
        acc_conn.execute("BEGIN")
        self.addCleanup(acc_conn.close)
        real_conns = {self.users_db: acc_conn, paths["collab"]: conns[paths["collab"]],
                      paths["owner"]: conns[paths["owner"]]}
        fake_collab = never / "collaboration.db"
        fake_owner = never / "alice.db"
        fake_member = never / "bob.db"
        real_conns[fake_collab] = conns[paths["collab"]]
        real_conns[fake_owner] = conns[paths["owner"]]
        real_conns[fake_member] = conns[paths["bob"]]
        scope = tsr.borrowed_team_read_scope(
            self._info(task_dir, tid),
            accounts_path=self.users_db, collab_path=fake_collab,
            owner_path=fake_owner,
            member_paths={USERS["alice"]: fake_owner, USERS["bob"]: fake_member},
            connections=real_conns, missing_members=frozenset())
        # T5/R3：哨兵先于 scope 构造生效——构造＋消费全程 stat/resolve/
        # path connect/open 全拦截；连接创建即登记收尾。
        with mock.patch.object(Path, "stat",
                               side_effect=AssertionError("T5: borrowed 不得 stat")), \
             mock.patch.object(Path, "resolve",
                               side_effect=AssertionError("T5: borrowed 不得 resolve")), \
             mock.patch("sqlite3.connect",
                        side_effect=AssertionError("T5: borrowed 不得 path 重连")), \
             mock.patch("os.open",
                        side_effect=AssertionError("T5: borrowed 不得 open")):
            scope = tsr.borrowed_team_read_scope(
                self._info(task_dir, tid),
                accounts_path=self.users_db, collab_path=fake_collab,
                owner_path=fake_owner,
                member_paths={USERS["alice"]: fake_owner,
                              USERS["bob"]: fake_member},
                connections=real_conns, missing_members=frozenset())
            view = tsr.team_stage_view(scope, "stage1")
        self.assertEqual(view.statuses["K1"].status, "final_include")
        self.assertFalse(never.exists(), "G5: borrowed 零 FS——不得 stat/open 假路径")

    def test_missing_members_missing_vote(self):
        """T5：missing 夹具**不创建缺库**（不 `_screener_db` 普通 connect）；
        调用前后 bob.db 确不存在；connections 不含其路径。"""
        tid = "g5-missing"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "bob", "coder")
        self._screener_db(task_dir, "alice", articles=("K1",),
                          votes={"K1": ("include", "2026-11-14T09:00:00+00:00",
                                        "2026-11-14T08:00:00+00:00")})
        bob_db = task_dir / "bob.db"
        self.assertFalse(bob_db.exists(), "T5: 夹具不得创建缺库 bob.db")
        collab_path = task_dir / "collaboration.db"
        conns = {}
        for path in (self.users_db, collab_path, task_dir / "alice.db"):
            conn = sqlite3.connect(path, timeout=15)
            conn.execute("BEGIN")
            conns[path] = conn
            self.addCleanup(conn.close)
        self.assertNotIn(bob_db, conns, "T5: connections 不含缺库路径")
        scope = tsr.borrowed_team_read_scope(
            self._info(task_dir, tid),
            accounts_path=self.users_db, collab_path=collab_path,
            owner_path=task_dir / "alice.db",
            member_paths={USERS["alice"]: task_dir / "alice.db"},
            connections=conns, missing_members=frozenset({USERS["bob"]}))
        view = tsr.team_stage_view(scope, "stage1")
        self.assertEqual(view.statuses["K1"].status, "awaiting_members")
        self.assertEqual(view.statuses["K1"].pending_reasons, (("bob", "missing_vote"),))
        self.assertFalse(bob_db.exists(), "T5: 调用后仍不得创建缺库 bob.db")

    def test_uncommitted_attest_consumed_via_full_borrowed_scope(self):
        """T2：caller 事务内 append **未提交** attest+generic → 完整 borrowed
        scope 的 stage/chain **实际消费**；禁止 path 重连（stat/resolve/connect
        哨兵）；全部 caller 事务与配置不变。"""
        tid = "g5-uncommitted-consume"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "bob", "coder")
        self._screener_db(task_dir, "alice", articles=("K1",),
                          votes={"K1": ("exclude", "2026-11-14T09:00:00+00:00",
                                        "2026-11-14T08:00:00+00:00")})
        self._screener_db(task_dir, "bob", articles=("K1",),
                          votes={"K1": ("include", "2026-11-14T09:05:00+00:00",
                                        "2026-11-14T08:05:00+00:00")})
        collab_path = task_dir / "collaboration.db"
        sarb.prepare_arbitration_attestation_storage(collab_path)
        # 真实 proof（两成员当前 library/revision）
        proof = {}
        for name in ("alice", "bob"):
            path = task_dir / f"{name}.db"
            lib, revs = self._lib_rev(path, "stage1", ("K1",))
            proof["K1"] = proof.get("K1", {})
            proof["K1"][str(USERS[name])] = {"library_id": lib,
                                             "revision": revs["K1"]}
        import pandas as pd
        merged = pd.DataFrame([
            {"zotero_key": "K1", "status": "conflict", "n_screened": 2,
             "decision_alice": "exclude", "decision_bob": "include",
             "exclusion_reason_alice": "", "exclusion_reason_bob": "",
             "notes_alice": "", "notes_bob": ""},
        ])
        # caller 事务内 append——**不提交**；R3：连接创建即登记收尾（任何
        # 后续断言失败不漏事务）。
        def _reg(conn):
            def _fini(_c=conn):
                try:
                    if _c.in_transaction:
                        _c.rollback()
                finally:
                    _c.close()
            self.addCleanup(_fini)
            return conn

        collab_conn = _reg(sqlite3.connect(collab_path, timeout=15))
        collab_conn.execute("BEGIN IMMEDIATE")
        combined = sarb.append_decision_arbitration_attested(
            collab_conn, stage="stage1", merged=merged,
            resolutions=[{"zotero_key": "K1", "decision": "include",
                          "rationale": "uncommitted team include"}],
            arbitrator_id=9,
            attestation_meta={"task_id": tid, "round": None,
                              "required_names": ["alice", "bob"],
                              "arbitrator_user_id": 9,
                              "arbitrator_role": "reconciler",
                              "input_proof": proof})
        att_id, rec_id = combined["attestation_id"], combined["record_id"]
        self.assertTrue(collab_conn.in_transaction, "T2: append 后保持未提交")
        conns = {collab_path: collab_conn}
        for path in (self.users_db, task_dir / "alice.db", task_dir / "bob.db"):
            conn = _reg(sqlite3.connect(path, timeout=15))
            conn.execute("BEGIN")
            conns[path] = conn
        snapshot_cfg = {p: (c.in_transaction, c.row_factory)
                        for p, c in conns.items()}
        # R3：哨兵先于 scope **构造**生效——构造＋stage/chain 消费全程零
        # stat/resolve/path connect/open。
        with mock.patch.object(Path, "stat",
                               side_effect=AssertionError("T2: borrowed 不得 stat")), \
             mock.patch.object(Path, "resolve",
                               side_effect=AssertionError("T2: borrowed 不得 resolve")), \
             mock.patch("sqlite3.connect",
                        side_effect=AssertionError("T2: borrowed 不得 path 重连")), \
             mock.patch("os.open",
                        side_effect=AssertionError("T2: borrowed 不得 open")):
            scope = tsr.borrowed_team_read_scope(
                self._info(task_dir, tid),
                accounts_path=self.users_db, collab_path=collab_path,
                owner_path=task_dir / "alice.db",
                member_paths={USERS["alice"]: task_dir / "alice.db",
                              USERS["bob"]: task_dir / "bob.db"},
                connections=conns, missing_members=frozenset())
            view = tsr.team_stage_view(scope, "stage1")
            chain = tsr.team_chain_view(scope, "stage2")   # 同 scope 两阶段
        self.assertEqual(view.statuses["K1"].status, "final_include")
        self.assertEqual(view.statuses["K1"].decided_by, "arbitration")
        self.assertEqual(view.applied.get("K1"), (att_id, rec_id))
        self.assertEqual(chain.stage1.statuses["K1"].applied_arbitration, att_id)
        self.assertTrue(chain.chain["K1"].cleared,
                        "T2: stage2 链在 stage1 终局 include 后 cleared")
        for path, conn in conns.items():
            still_txn, rf = conn.in_transaction, conn.row_factory
            self.assertTrue(still_txn, f"T2: caller 事务被终结: {path}")
            self.assertEqual((still_txn, rf), snapshot_cfg[path],
                             f"T2: caller 配置被改动: {path}")

    def test_caller_config_untouched(self):
        tid = "g5-config"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "bob", "coder")
        self._screener_db(task_dir, "alice", articles=("K1",))
        self._screener_db(task_dir, "bob", votes={"K1": ("include", "2026-11-14T09:00:00+00:00",
                                                         "2026-11-14T08:00:00+00:00")})
        conns, paths = self._open_conns(task_dir)
        for conn in conns.values():
            self.addCleanup(conn.close)
        before = {path: (conn.row_factory, conn.execute(
            "PRAGMA journal_mode").fetchone()[0]) for path, conn in conns.items()}
        scope = tsr.borrowed_team_read_scope(
            self._info(task_dir, tid),
            accounts_path=paths["accounts"], collab_path=paths["collab"],
            owner_path=paths["owner"], member_paths={USERS["alice"]: paths["owner"], USERS["bob"]: paths["bob"]},
            connections=conns, missing_members=frozenset())
        tsr.team_stage_view(scope, "stage1")
        tsr.team_chain_view(scope, "stage2")   # 两阶段同 scope
        for path, conn in conns.items():
            factory_after = conn.row_factory
            mode_after = conn.execute("PRAGMA journal_mode").fetchone()[0]
            self.assertEqual((factory_after, mode_after), before[path],
                             f"G5: caller 配置不得被改动 {path.name}")
            self.assertTrue(conn.in_transaction)


# ---------------------------------------------------------------------------
# G6 S1b conn seam
# ---------------------------------------------------------------------------

class G6Seam(_TSRBox):

    def _prepared_task(self, tid):
        task_dir = self._init_task(task_id=tid)
        collab_path = task_dir / "collaboration.db"
        sarb.prepare_arbitration_attestation_storage(collab_path)
        return task_dir, collab_path

    def test_uncommitted_attest_conn_visibility_and_txn_semantics(self):
        tid = "g6-uncommitted"
        task_dir, collab_path = self._prepared_task(tid)
        df_rows = [{"zotero_key": "K1", "status": "conflict", "n_screened": 2,
                    "decision_alice": "include", "decision_bob": "exclude"}]
        import pandas as pd
        conn = sqlite3.connect(collab_path, timeout=15)
        self.addCleanup(conn.close)
        conn.execute("BEGIN IMMEDIATE")
        result = sarb.append_decision_arbitration_attested(
            conn, stage="stage1", merged=pd.DataFrame(df_rows),
            resolutions=[{"zotero_key": "K1", "decision": "include",
                          "rationale": "team"}],
            arbitrator_id=USERS["carol"],
            attestation_meta={"task_id": tid, "round": None,
                              "required_names": ["alice", "bob"],
                              "arbitrator_user_id": USERS["carol"],
                              "arbitrator_role": "reconciler",
                              "input_proof": {"K1": {"1": {"library_id": "a" * 32,
                                                           "revision": 1},
                                                     "2": {"library_id": "b" * 32,
                                                           "revision": 1}}}})
        found = sarb.list_decision_arbitration_attestations_on(conn, stage="stage1",
                                                               task_id=tid)
        self.assertEqual(len(found), 1, "G6: caller 同事务未提交 attest 必须可见")
        self.assertEqual(found[0]["attestation_id"], result["attestation_id"])
        ro = sqlite3.connect(collab_path.as_uri() + "?mode=ro", uri=True, timeout=15)
        self.addCleanup(ro.close)
        self.assertEqual(sarb.list_decision_arbitration_attestations_on(ro), [],
                         "G6: 独立连接不得见未提交行")
        conn.commit()
        self.assertEqual(len(sarb.list_decision_arbitration_attestations_on(ro)), 1,
                         "G6: 提交后独立连接可见")
        self.assertFalse(conn.in_transaction, "G6: 提交后无残留事务")
        self.assertEqual(conn.execute("SELECT 1").fetchone()[0], 1)

    def test_seam_no_txn_self_snapshot_only(self):
        tid = "g6-selfsnap"
        task_dir, collab_path = self._prepared_task(tid)
        conn = sqlite3.connect(collab_path, timeout=15)
        self.addCleanup(conn.close)
        self.assertEqual(sarb.list_decision_arbitration_attestations_on(conn), [])
        self.assertFalse(conn.in_transaction, "G6: 无事务时 reader 只收尾自己的快照")
        self.assertIsNotNone(conn.execute("SELECT 1").fetchone(), "G6: 连接保持打开")

    def test_seam_contract_and_filter_valueerrors(self):
        task_dir, collab_path = self._prepared_task("g6-contract")
        with self.assertRaises(sarb.ArbitrationConnectionContractError):
            sarb.list_decision_arbitration_attestations_on(None)
        with self.assertRaises(sarb.ArbitrationConnectionContractError):
            sarb.list_decision_arbitration_attestations_on(str(collab_path))
        conn = sqlite3.connect(collab_path, timeout=15)
        self.addCleanup(conn.close)
        with self.assertRaises(ValueError):
            sarb.list_decision_arbitration_attestations_on(conn, stage="stage3")
        with self.assertRaises(ValueError):
            sarb.list_decision_arbitration_attestations_on(conn, record_id=0)

    def test_empty_only_when_truly_unprepared(self):
        tid_unprep = "g6-unprep"
        task_dir = self._init_task(task_id=tid_unprep)   # 未 prepare
        conn = sqlite3.connect(task_dir / "collaboration.db", timeout=15)
        self.addCleanup(conn.close)
        self.assertEqual(sarb.list_decision_arbitration_attestations_on(conn), [])
        _, collab_path = self._prepared_task("g6-prep")
        conn2 = sqlite3.connect(collab_path, timeout=15)
        self.addCleanup(conn2.close)
        self.assertEqual(sarb.list_decision_arbitration_attestations_on(conn2), [],
                         "G6: 已 prepare 空表=合法空")

    def test_f7_generic_table_missing_typed(self):
        """[F7][synthetic] attest 表存在（空）而 generic 表缺失 → 不得伪空，
        须 StoreUnavailable（同一 snapshot 确认 generic 表在场）。"""
        _, collab_path = self._prepared_task("g6-f7a")
        conn = sqlite3.connect(collab_path, timeout=15)
        self.addCleanup(conn.close)
        conn.execute("DROP TABLE screening_arbitrations")
        conn.commit()
        with self.assertRaises(sarb.ArbitrationAttestationStoreUnavailable,
                               msg="F7: 缺 generic 表不得伪空"):
            sarb.list_decision_arbitration_attestations_on(conn)

    def test_f7_generic_table_missing_columns_typed(self):
        """[F7b][synthetic] generic 表在但缺必需列 → 同样 StoreUnavailable。"""
        _, collab_path = self._prepared_task("g6-f7b")
        conn = sqlite3.connect(collab_path, timeout=15)
        self.addCleanup(conn.close)
        conn.execute("DROP TABLE screening_arbitrations")
        conn.execute("CREATE TABLE screening_arbitrations (id INTEGER PRIMARY KEY,"
                     " stage TEXT)")
        conn.commit()
        with self.assertRaises(sarb.ArbitrationAttestationStoreUnavailable,
                               msg="F7b: generic 缺必需列不得伪空"):
            sarb.list_decision_arbitration_attestations_on(conn)

    def test_synthetic_orphan_attest_typed(self):
        """[synthetic] 命中 attest 行缺 generic 来源行（直插合成，绕开
        append-only 正常路径以模拟存储损坏）→ typed StoreUnavailable。"""
        tid = "g6-orphan"
        task_dir, collab_path = self._prepared_task(tid)
        conn = sqlite3.connect(collab_path, timeout=15)
        self.addCleanup(conn.close)
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "INSERT INTO screening_arbitrations (stage, merge_sha256, result_sha256,"
            " supersedes_id, arbitrator_id, records_json, created_at)"
            " VALUES ('stage1', ?, ?, NULL, '3', '[]', '2026-11-15T00:00:00+00:00')",
            ("m" * 64, "r" * 64))
        conn.execute(
            "INSERT INTO screening_arbitration_attestations (record_id, task_id,"
            " round_id, stage, arbitrator_user_id, arbitrator_role, required_names_json,"
            " input_proof_json, resolutions_digest, attest_sha256, attested_at)"
            " VALUES (?, ?, NULL, 'stage1', 3, 'reconciler', '[]', '{}',"
            " ?, ?, '2026-11-15T00:00:00+00:00')",
            (cur.lastrowid + 100, tid, "d" * 64, "h" * 64))   # 孤儿引用
        conn.commit()
        with self.assertRaises(sarb.ArbitrationAttestationStoreUnavailable,
                               msg="G6: 命中孤儿须 typed，不伪空"):
            sarb.list_decision_arbitration_attestations_on(conn, task_id=tid)

    def test_path_public_anchors_unchanged(self):
        tid = "g6-path"
        task_dir, collab_path = self._prepared_task(tid)
        self.assertIsNone(sarb.get_decision_arbitration_attestation(
            self.base / "nope.db", 1), "G6 path: 缺文件→None")
        self.assertEqual(sarb.list_decision_arbitration_attestations(
            self.base / "nope.db"), [])
        self.assertIsNone(sarb.get_decision_arbitration_attestation(collab_path, 1),
                          "G6 path: 无该 ID→None")
        import pandas as pd
        conn = sqlite3.connect(collab_path, timeout=15)
        self.addCleanup(conn.close)
        conn.execute("BEGIN IMMEDIATE")
        result = sarb.append_decision_arbitration_attested(
            conn, stage="stage1",
            merged=pd.DataFrame(
                [{"zotero_key": "K1", "status": "conflict", "n_screened": 2,
                  "decision_alice": "include", "decision_bob": "exclude"}]),
            resolutions=[{"zotero_key": "K1", "decision": "include", "rationale": "t"}],
            arbitrator_id=USERS["carol"],
            attestation_meta={"task_id": tid, "round": None,
                              "required_names": ["alice", "bob"],
                              "arbitrator_user_id": USERS["carol"],
                              "arbitrator_role": "reconciler",
                              "input_proof": {"K1": {"1": {"library_id": "a" * 32,
                                                           "revision": 1}}}})
        conn.commit()
        got = sarb.get_decision_arbitration_attestation(collab_path,
                                                        result["attestation_id"])
        self.assertIsNotNone(got)
        self.assertEqual(got["record_id"], result["record_id"],
                         "G6 path: 双 ID 分列语义与 conn 版一致")


# ---------------------------------------------------------------------------
# G7 时间
# ---------------------------------------------------------------------------

class G7Time(_TSRBox):

    def _conflict_task(self, tid, alice_updated, alice_screened="2026-11-14T08:00:00+00:00"):
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "bob", "coder")
        self._screener_db(task_dir, "alice", articles=("K2",),
                          votes={"K2": ("include", alice_updated, alice_screened)})
        self._screener_db(task_dir, "bob",
                          votes={"K2": ("exclude", "2026-11-14T09:05:00+00:00",
                                        "2026-11-14T08:05:00+00:00")})
        return task_dir

    def test_garbage_updated_at_no_fallback_whole_call_raises(self):
        """非空非法 updated_at 不回退 screened_at 旧时间；就绪待裁键 required
        成员时间不可核 → 该次 team_stage_view 整体抛 vote_time_unverifiable
        （无部分 DTO/无第六状态）。"""
        tid = "g7-garbage"
        task_dir = self._conflict_task(tid, alice_updated="not-a-timestamp")
        exc = self._expect_tfue(lambda: self._fresh_view(task_dir, tid),
                                "vote_time_unverifiable", tag="G7 坏时间")
        self.assertEqual(exc.member, "alice")
        self.assertEqual(exc.key, "K2")
        self.assertEqual(exc.params.get("key"), "K2")

    def test_real_resave_restores_ready(self):
        """正对照（协调#4/T6）：成员真实保存使 updated_at/revision **真实
        前进**（数值断言）后恢复可裁 ready；再以新 revision 集落**新证明** →
        新 attest 生效（applied=新 id、键终局）——闭环不止 ready。"""
        tid = "g7-resave"
        task_dir = self._conflict_task(tid, alice_updated="not-a-timestamp")
        with self.assertRaises(tsr.TeamFlowUnavailableError):
            self._fresh_view(task_dir, tid)
        _, rev_before = self._lib_rev(task_dir / "alice.db", "stage1", ("K2",))
        # R6：经**现有公共保存入口**重存（零修改产品）——真实 _now() 时间＋
        # 真实 trigger 推进 revision（不再直接 UPDATE 改时间）。
        dbm.save_decision(task_dir / "alice.db", "K2", "include",
                          exclusion_reason="", notes="", tags=None)
        _, rev_after = self._lib_rev(task_dir / "alice.db", "stage1", ("K2",))
        self.assertGreater(rev_after["K2"], rev_before["K2"],
                           "T6: 真实重存必须推进 revision（数值断言）")
        view = self._fresh_view(task_dir, tid)
        self.assertEqual(view.statuses["K2"].status, "awaiting_arbitration")
        self.assertEqual(tsr.arbitration_ready_keys(view), ["K2"])
        # 新 proof（新 revision）→ 新 attest → 实际采信（重新绑定证明应用）
        att = self._append_attest(
            task_dir / "collaboration.db", tid, row_keys=("K2",),
            resolutions=({"zotero_key": "K2", "decision": "include",
                          "rationale": "resaved then arbitrated"},),
            proof_keys=("K2",))
        view2 = self._fresh_view(task_dir, tid)
        self.assertEqual(view2.statuses["K2"].status, "final_include")
        self.assertEqual(view2.statuses["K2"].decided_by, "arbitration")
        self.assertEqual(view2.applied.get("K2"),
                         (att["attestation_id"], att["record_id"]))

    def test_naive_local_time_accepted(self):
        """naive 本地时刻按读取部署的历史本地规则可核 → 不触发
        vote_time_unverifiable（精确 UTC 换算为内部口径，此处钉可核边界）。"""
        tid = "g7-naive"
        task_dir = self._conflict_task(tid, alice_updated="2026-07-01 12:00:00")
        view = self._fresh_view(task_dir, tid)
        self.assertEqual(view.statuses["K2"].status, "awaiting_arbitration")


# ---------------------------------------------------------------------------
# G8 逐 key 仲裁 / canonical 域 / DTO
# ---------------------------------------------------------------------------

class G8DomainDTOChain(_TSRBox):

    def _rich_task(self, tid):
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "bob", "coder")
        self._screener_db(
            task_dir, "alice",
            articles=("K5", "K1", "K2", "K3"),   # import_order 0..3
            dups=(("DK1", "K1"),),
            title_pending=("TK1",),
            votes={"K1": ("exclude", "2026-11-14T09:00:00+00:00",
                          "2026-11-14T08:00:00+00:00"),
                   "K2": ("exclude", "2026-11-14T09:00:00+00:00",
                          "2026-11-14T08:00:00+00:00"),
                   "K3": ("include", "2026-11-14T09:00:00+00:00",
                          "2026-11-14T08:00:00+00:00"),
                   "DK1": ("include", "2026-11-14T09:00:00+00:00",      # 域外历史票
                           "2026-11-14T08:00:00+00:00"),
                   "TK1": ("maybe", "2026-11-14T09:00:00+00:00",
                           "2026-11-14T08:00:00+00:00")})
        self._screener_db(
            task_dir, "bob",
            votes={"K1": ("include", "2026-11-14T09:05:00+00:00",
                          "2026-11-14T08:05:00+00:00"),
                   "K2": ("include", "2026-11-14T09:05:00+00:00",
                          "2026-11-14T08:05:00+00:00"),
                   "K3": ("exclude", "2026-11-14T09:05:00+00:00",
                          "2026-11-14T08:05:00+00:00")})
        return task_dir

    def test_canonical_domain_exact_order_and_out_of_domain_filtered(self):
        """canonical 键域=owner articles 按 import_order,zotero_key 序、排
        duplicate/title-pending；域外历史票过滤、不报错、不锁死任务。"""
        tid = "g8-domain"
        task_dir = self._rich_task(tid)
        view = self._fresh_view(task_dir, tid)
        self.assertEqual(view.keys, ("K5", "K1", "K2", "K3"),
                         "G8: import_order 保序（K5 首位插入序）")
        self.assertNotIn("DK1", view.keys)
        self.assertNotIn("TK1", view.keys)
        self.assertEqual(view.statuses["K1"].status, "awaiting_arbitration")

    def test_personal_exclude_team_include_chain_cleared(self):
        """个人 exclude 经合法团队仲裁 include：K1 终局 include 且下游 chain
        cleared；T3 双 ID **真实不同**——同值重存推进 revision→同 generic 内容
        复用 genericID、新 proof 产生新 attestID，断言 `attestation_id !=
        record_id`，applied/详情各按其 ID。"""
        tid = "g8-include"
        task_dir = self._rich_task(tid)
        resolutions = ({"zotero_key": "K1", "decision": "include",
                        "rationale": "team include"},
                       {"zotero_key": "K2", "decision": "exclude",
                        "rationale": "team exclude"},
                       {"zotero_key": "K3", "decision": "exclude",
                        "rationale": "team exclude"})
        att1 = self._append_attest(
            task_dir / "collaboration.db", tid, row_keys=("K1", "K2", "K3"),
            resolutions=resolutions, proof_keys=("K1", "K2", "K3"))
        view = self._fresh_view(task_dir, tid)
        self.assertEqual(view.statuses["K1"].status, "final_include")
        self.assertEqual(view.applied.get("K1"),
                         (att1["attestation_id"], att1["record_id"]))
        self.assertEqual(view.statuses["K1"].decided_by, "arbitration")
        # T3/R6：同值重存经**现有公共保存入口**（decision 不变、真实 _now()
        # 时间＋真实 trigger 推进 revision；不直接 UPDATE）→ 旧 proof 逐键失
        # 效；再 append 同 generic 字段/actor/结论 → generic 记录复用同 ID，
        # 新 attest 新 ID——双 ID 真实分离。
        dbm.save_decision(task_dir / "alice.db", "K1", "exclude",
                          exclusion_reason="", notes="", tags=None)
        att2 = self._append_attest(
            task_dir / "collaboration.db", tid, row_keys=("K1", "K2", "K3"),
            resolutions=resolutions, proof_keys=("K1", "K2", "K3"))
        self.assertEqual(att2["record_id"], att1["record_id"],
                         "T3: 同内容/actor 复用同 generic 记录")
        self.assertNotEqual(att2["attestation_id"], att1["attestation_id"],
                            "T3: 新 proof 必须产生新 attestation")
        self.assertNotEqual(att2["attestation_id"], att2["record_id"],
                            "T3: 双 ID 必须真实不同（非首库巧合相等）")
        view2 = self._fresh_view(task_dir, tid)
        self.assertEqual(view2.statuses["K1"].status, "final_include")
        self.assertEqual(view2.applied.get("K1"),
                         (att2["attestation_id"], att2["record_id"]),
                         "T3: applied 采信新 attest、generic 独立分列")
        self.assertIn((att1["attestation_id"], "K1", "vote_revision_changed"),
                      view2.loader_stale, "T3: 旧 proof 逐键过期可见")
        # generic 详情按 generic record_id 直读（不把 attestID 混入 detail API）
        generic = sarb.get_decision_arbitration(task_dir / "collaboration.db",
                                                att2["record_id"])
        self.assertIsNotNone(generic)
        self.assertEqual(generic["id"], att2["record_id"])
        with tsr.fresh_team_read_scope(self._info(task_dir, tid),
                                       accounts_db=self.users_db) as scope:
            chain = tsr.team_chain_view(scope, "stage2")
        self.assertTrue(chain.chain["K1"].cleared)
        self.assertEqual(chain.chain["K1"].stage1_status, "final_include")
        self.assertIsNone(tsr.team_item_gate_reason(chain.chain["K1"]))
        self.assertEqual(tsr.chain_cleared_keys(chain), ["K1"])
        with tsr.fresh_team_read_scope(self._info(task_dir, tid),
                                       accounts_db=self.users_db) as scope:
            coding = tsr.team_chain_view(scope, "coding")
        item = coding.chain["K1"]
        self.assertFalse(item.cleared)
        self.assertEqual(item.blocked_reason, "upstream_stage2_awaiting_members")
        self.assertEqual(tsr.team_item_gate_reason(item),
                         "team_item_upstream_stage2_awaiting_members")

    def test_per_key_b_change_keeps_a(self):
        """逐 key 裁剪：B 键 revision 前进只裁 B 键（vote_revision_changed），
        A 键 applied 不变、不整记录过滤。"""
        tid = "g8-perkey"
        task_dir = self._rich_task(tid)
        att = self._append_attest(
            task_dir / "collaboration.db", tid, row_keys=("K1", "K2", "K3"),
            resolutions=({"zotero_key": "K1", "decision": "include", "rationale": "t"},
                         {"zotero_key": "K2", "decision": "include", "rationale": "t"},
                         {"zotero_key": "K3", "decision": "exclude", "rationale": "t"}),
            proof_keys=("K1", "K2", "K3"))
        view = self._fresh_view(task_dir, tid)
        self.assertEqual(view.applied.get("K2"), (att["attestation_id"], att["record_id"]))
        conn = sqlite3.connect(task_dir / "bob.db")
        try:
            conn.execute("UPDATE decisions SET decision='exclude',"
                         " updated_at='2026-11-14T11:00:00+00:00' WHERE zotero_key='K2'")
            conn.commit()
        finally:
            conn.close()
        view2 = self._fresh_view(task_dir, tid)
        self.assertEqual(view2.loader_stale, ((att["attestation_id"], "K2",
                                               "vote_revision_changed"),),
                         "G8: 仅 B 键 stale")
        self.assertNotIn("K2", view2.applied)
        self.assertEqual(view2.applied.get("K1"), (att["attestation_id"], att["record_id"]),
                         "G8: A 键不受 B 变化影响")
        # B 的 revision 前进使证明对 K2 失配 → 回落共识 exclude（与 A 一致），
        # 非 awaiting_arbitration（旧证明不再适用）。
        self.assertEqual(view2.statuses["K2"].status, "final_exclude")

    def test_proof_missing_member_entry_stales_key(self):
        tid = "g8-proofmember"
        task_dir = self._rich_task(tid)
        lib_a, rev_a = self._lib_rev(task_dir / "alice.db", "stage1", ("K1",))
        att = self._append_attest(
            task_dir / "collaboration.db", tid, row_keys=("K1",),
            resolutions=({"zotero_key": "K1", "decision": "include", "rationale": "t"},),
            proof_keys=("K1",),
            proof_entries={"K1": {str(USERS["alice"]):
                                  {"library_id": lib_a, "revision": rev_a["K1"]}}})
        view = self._fresh_view(task_dir, tid)
        self.assertEqual(
            view.loader_stale, ((att["attestation_id"], "K1", "vote_proof_missing"),),
            "G8: 缺 required 成员条目 → vote_proof_missing")
        self.assertNotIn("K1", view.applied)
        self.assertEqual(view.statuses["K1"].status, "awaiting_arbitration")

    def test_f8_whole_key_proof_missing_stales_and_keeps_other(self):
        """[F8] 证明缺整键（K2 无任何条目）：该键须记 vote_proof_missing 且
        其他键（K3）适用性保留——不得静默跳过、不得整记录过滤。"""
        tid = "g8-f8"
        task_dir = self._rich_task(tid)
        att = self._append_attest(
            task_dir / "collaboration.db", tid, row_keys=("K1", "K2", "K3"),
            resolutions=({"zotero_key": "K1", "decision": "include", "rationale": "t"},
                         {"zotero_key": "K2", "decision": "include", "rationale": "t"},
                         {"zotero_key": "K3", "decision": "exclude", "rationale": "t"}),
            proof_keys=("K1", "K3"),   # K2 整键缺 proof
            proof_entries=None)
        view = self._fresh_view(task_dir, tid)
        stale = {(a, k, r) for (a, k, r) in view.loader_stale}
        self.assertIn((att["attestation_id"], "K2", "vote_proof_missing"), stale,
                      f"F8: 整键 proof 缺失须 loader_stale: {view.loader_stale}")
        self.assertIn("K3", view.applied, "F8: 其他键适用性保留")
        self.assertNotIn("K2", view.applied)

    def test_vote_domain_violation_dirty_decision(self):
        """域内键的非法 decision 值（参与计算的形状错误）→ vote_domain_violation。"""
        tid = "g8-domainv"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "bob", "coder")
        self._screener_db(task_dir, "alice", articles=("K1",),
                          votes={"K1": ("include", "2026-11-14T09:00:00+00:00",
                                        "2026-11-14T08:00:00+00:00")})
        self._screener_db(task_dir, "bob",
                          votes={"K1": ("banana", "2026-11-14T09:05:00+00:00",
                                        "2026-11-14T08:05:00+00:00")})
        exc = self._expect_tfue(lambda: self._fresh_view(task_dir, tid),
                                "vote_domain_violation", tag="G8 非法 decision")
        self.assertEqual(exc.member, "bob")
        self.assertEqual(exc.key, "K1")

    def test_dto_zero_leakage_and_chain_views(self):
        tid = "g8-dto"
        task_dir = self._rich_task(tid)
        # R4：fixture 注入私有 reason/notes 哨兵（经公共保存入口，真实持久）
        # ——DTO 各树不得出现（键或值）。
        dbm.save_decision(task_dir / "alice.db", "K1", "exclude",
                          exclusion_reason="S1C-PRIVATE-REASON-9f3a",
                          notes="S1C-PRIVATE-NOTES-7c2b", tags=None)
        att = self._append_attest(
            task_dir / "collaboration.db", tid, row_keys=("K1", "K2", "K3"),
            resolutions=({"zotero_key": "K1", "decision": "include", "rationale": "t"},
                         {"zotero_key": "K2", "decision": "exclude", "rationale": "t"},
                         {"zotero_key": "K3", "decision": "include", "rationale": "t"}),
            proof_keys=("K1", "K2", "K3"))
        with tsr.fresh_team_read_scope(self._info(task_dir, tid),
                                       accounts_db=self.users_db) as scope:
            view = tsr.team_stage_view(scope, "stage1")
            chain = tsr.team_chain_view(scope, "stage2")
        payload = json.loads(json.dumps(_dataclasses_asdict(view), default=str))
        self.assertEqual(set(payload), {"stage", "task_id", "round_id", "config",
                                        "statuses", "loader_stale", "keys", "applied",
                                        "issues", "completion",
                                        "completion_required_uids"})
        self.assertIsNone(view.round_id)
        five = {"final_include", "final_exclude", "awaiting_config",
                "awaiting_members", "awaiting_arbitration"}
        for item in view.statuses.values():
            self.assertIn(item.status, five)
            for _member, reason in item.pending_reasons:
                self.assertIn(reason, ("missing_vote", "non_formal_value"))
        chain_payload = json.loads(json.dumps(_dataclasses_asdict(chain), default=str))
        self.assertEqual(set(chain_payload), {"target", "chain", "stage1", "stage2",
                                              "issues", "completion"})
        # R4：递归防泄露（keys ∪ values）——白名单键集（任何层出现清单外键
        # 即败，捕获 decision_/notes_/exclusion_reason_ 成员列键）；值不出现
        # 成员票面列名与 fixture 注入的私有 reason/notes 哨兵串。
        _private_sentinels = ("S1C-PRIVATE-REASON-9f3a", "S1C-PRIVATE-NOTES-7c2b")
        allowed_keys = {
            "stage", "task_id", "round_id", "config", "statuses", "loader_stale",
            "keys", "applied", "key", "status", "pending_members",
            "pending_reasons", "stale_arbitrations", "applied_arbitration",
            "decided_by", "ok", "empty_required", "unconfigured",
            "conflicting", "required", "detail", "target", "chain",
            "stage1", "stage2", "cleared", "blocked_reason",
            "stage1_status", "stage2_status",
            "issues", "completion", "completion_required_uids",
            "reason", "source_role", "retryable",
            "seal_id", "kind", "trigger", "sealed_at",
            "final_decision", "persisted"}
        _member_column_markers = ("decision_", "notes_", "exclusion_reason_")

        def _walk(node, path="$", key_map=False):
            if isinstance(node, dict):
                for key, value in node.items():
                    self.assertIsInstance(key, str)
                    if key_map:
                        # 键映射层（statuses/chain/applied）：键=条目标识，只查
                        # 非成员列前缀；其值层恢复白名单。
                        for marker in _member_column_markers:
                            self.assertFalse(key.startswith(marker),
                                             f"R4: DTO 泄漏成员票面列键 {path}.{key}")
                        _walk(value, f"{path}.{key}", key_map=False)
                        continue
                    self.assertIn(key, allowed_keys,
                                  f"R4: DTO 递归出现白名单外键 {path}.{key!r}")
                    for marker in _member_column_markers:
                        self.assertFalse(key.startswith(marker),
                                         f"R4: DTO 泄漏成员票面列键 {path}.{key}")
                    _walk(value, f"{path}.{key}",
                          key_map=key in ("statuses", "chain", "applied"))
            elif isinstance(node, (list, tuple)):
                for index, value in enumerate(node):
                    _walk(value, f"{path}[{index}]")
            elif isinstance(node, str):
                self.assertFalse(node.startswith(_member_column_markers),
                                 f"R4: DTO 值泄漏成员票面列: {path}")
                for sentinel in _private_sentinels:
                    self.assertNotIn(sentinel, node,
                                     f"R4: DTO 泄漏私有票面哨兵: {path}")
                self.assertNotIn("input_proof", node, f"R4: DTO 值泄漏: {path}")

        _walk(payload)
        _walk(chain_payload)
        for entry in view.loader_stale:
            self.assertIn(entry[2], ("vote_revision_changed", "vote_proof_missing"))
        for pair in view.applied.values():
            self.assertEqual(len(pair), 2)
            self.assertNotIsInstance(pair[0], bool)
        self.assertEqual(chain.stage1.stage, "stage1")
        self.assertEqual(chain.stage2.stage, "stage2")
        _ = att

    def test_entry_valueerrors_and_helper_contract(self):
        tid = "g8-entry"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._screener_db(task_dir, "alice", articles=("K1",))
        with tsr.fresh_team_read_scope(self._info(task_dir, tid),
                                       accounts_db=self.users_db) as scope:
            with self.assertRaises(ValueError):
                tsr.team_stage_view(scope, "stage3")
            with self.assertRaises(ValueError):
                tsr.team_chain_view(scope, "nope")
        with self.assertRaises(TypeError):
            tsr.team_item_gate_reason("not-an-item")

    def test_attestation_time_invalid_synthetic(self):
        """[synthetic] 命中 attestation 的 attested_at 非法 → typed
        attestation_time_invalid，不静默采更旧证明。"""
        tid = "g8-atime"
        task_dir = self._init_task(task_id=tid)
        self._choice(task_dir, tid, "alice", "coder")
        self._grant(task_dir, tid, "bob", "coder")
        self._screener_db(task_dir, "alice", articles=("K1",),
                          votes={"K1": ("include", "2026-11-14T09:00:00+00:00",
                                        "2026-11-14T08:00:00+00:00")})
        self._screener_db(task_dir, "bob",
                          votes={"K1": ("exclude", "2026-11-14T09:05:00+00:00",
                                        "2026-11-14T08:05:00+00:00")})
        collab_path = task_dir / "collaboration.db"
        sarb.prepare_arbitration_attestation_storage(collab_path)
        conn = sqlite3.connect(collab_path, timeout=15)
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "INSERT INTO screening_arbitrations (stage, merge_sha256,"
                " result_sha256, supersedes_id, arbitrator_id, records_json, created_at)"
                " VALUES ('stage1', ?, ?, NULL, '3',"
                " '[{\"zotero_key\": \"K1\", \"decision_alice\": \"include\","
                " \"decision_bob\": \"exclude\"}]', '2026-11-15T00:00:00+00:00')",
                ("m" * 64, "r" * 64))
            conn.execute(
                "INSERT INTO screening_arbitration_attestations (record_id, task_id,"
                " round_id, stage, arbitrator_user_id, arbitrator_role,"
                " required_names_json, input_proof_json, resolutions_digest,"
                " attest_sha256, attested_at)"
                " VALUES (?, ?, NULL, 'stage1', 3, 'reconciler',"
                " '[\"alice\", \"bob\"]', '{}', ?, ?, 'garbage-time')",
                (cur.lastrowid, tid, "d" * 64, "h" * 64))
            conn.commit()
        finally:
            conn.close()
        exc = self._expect_tfue(lambda: self._fresh_view(task_dir, tid),
                                "attestation_time_invalid", tag="G8 非法 attested_at")


def _dataclasses_asdict(obj):
    import dataclasses
    return dataclasses.asdict(obj)


if __name__ == "__main__":
    if os.environ.get("S1C_CHILD_PROBE") == "1":
        # 入口快照必须先于模块 setup；runner 及显式 doModuleCleanups
        # 均走 unittest 的真实清理链（USERS→指针→目录→env）。
        _snap = {k: os.environ.get(k) for k in TRACKED_ENV}
        setUpModule()
        _loader = unittest.TestLoader()
        _suite = _loader.loadTestsFromTestCase(_make_t1_recovery_probe())
        _runner = unittest.TextTestRunner(verbosity=2)
        _result = _runner.run(_suite)
        unittest.doModuleCleanups()
        print(f"T1_CHILD_RUN_ERRORS={len(_result.errors)}")
        _restored = all(os.environ.get(k) == _snap[k] for k in TRACKED_ENV)
        print(f"T1_CHILD_ENV_RESTORED={1 if _restored else 0}")
        _root = globals().get("_T1_PROBE_ROOT", "")
        _class_dir = globals().get("_T1_PROBE_CLASS_DIR", "")
        _scope_cleared = _MODULE_SCOPE is None
        _root_removed = bool(_root.strip()) and not Path(_root).exists()
        _removed = bool(_class_dir.strip()) and not Path(_class_dir).exists()
        print(f"T1_CHILD_ACTUAL_ROOT={_root}")
        print(f"T1_CHILD_ACTUAL_CLASS_DIR={_class_dir}")
        print(f"T1_CHILD_SCOPE_POINTER_CLEARED={1 if _scope_cleared else 0}")
        print(f"T1_CHILD_ROOT_REMOVED={1 if _root_removed else 0}")
        print(f"T1_CHILD_MODULE_DIR_REMOVED={1 if _removed else 0}")
        _not_imported = ("custom_backend.team_screening_runtime"
                         not in sys.modules)
        print(f"T1_CHILD_TSR_NOT_IMPORTED={1 if _not_imported else 0}")
        # 失败可见性由子进程自身核对首条 error 回溯文本（不依赖宿主 stderr 管道）
        _error_text = _result.errors[0][1] if len(_result.errors) == 1 else ""
        _text_seen = "T1 root-inconsistency probe" in _error_text
        print(f"T1_CHILD_ERROR_TEXT_SEEN={1 if _text_seen else 0}")
        sys.exit(0 if (_restored and _root_removed and _removed and _scope_cleared
                       and _not_imported
                       and len(_result.errors) == 1
                       and _text_seen
                       and not _result.failures) else 1)
    unittest.main()
