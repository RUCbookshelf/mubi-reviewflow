# -*- coding: utf-8 -*-
"""SafetyR4 A–D + SafetyR5 A1/B1/B2/C1/C2/C3/D1/D2 + SafetyR6 N1–N5 永久回归
的子进程驱动器（test-only，不被 pytest 收集）。

由 test_2_1_6_bare_env_lease.py 以独立进程逐场景驱动：
    python -c "import sys; sys.path.insert(0,<build/tests>); import
               _safetyr4_driver as d; sys.exit(d.main())" <scenario> [args...]
每个场景打印 RESULT <json>（末行），断言失败抛出（rc!=0）。真实框架路径
（pytest.main / TextTestRunner / 真实模块导入 / 真实模块夹具）为主；
故障注入只在驱动进程内局部生效。R5 场景（r5_*）逐项对应 review_safety_r4.md
清单；断言宿主环境原串/原异常身份/根实际存在与身份/owner 重试责任/未发生
业务写，不只断返回码。R6 场景（r6_*）+ 改写的 r5_c3_slashes/r5_c1_recreated
对应 review_safety_r5_ds.md N1–N5：跨平台路径等价构造（无 skip）、空白根
拒绝（bare+真实 pytest 双入口）、live acquire 身份前置（导入期业务零执
行）、同路径不同身份的确定性目录对象构造（改名保活，不赌 inode 复用）。
R6 补充场景（r6_f1_pytest_live_acquire / r6_n2_live_blank_owner，用户授权
的一次窄修）钉住 review_safety_r6_ds.md F1（真实 pytest 会话代中 module
import 触发的 acquire 先过运行期身份校验再施 mode/token）与 N2 live 残余
（live cwd 根 owner 的 DATA 被改空在 acquire 统一入口拒绝）。
F2 收集期边界场景（r6_f2_collect_boundary / r6_f2_collect_broken，
collection_boundary_decision_20261114.md 用户授权的一次收集边界修复）钉住
review_safety_pytest_supplement_ds.md F2：真实 build/tests conftest 的
pytest_make_collect_report wrapper 在每个 pytest.Module 真实 collect/import
前后归还 COBOOKSHELF_* 会话基线——真实文件名序 mutator→acquirer 的
--collect-only 全绿、mutator 模块内部漂移 acquire 仍先拒、异常模块原收集
异常如实保留且其后模块导入看到基线已恢复（修前两条收集错误）。
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
BOX = TESTS_DIR.parents[1]
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(BOX))
os.chdir(BOX)

DATA = "COBOOKSHELF_DATA_DIR"
MODE = "REVIEWFLOW_MODE"

import _bare_env  # noqa: E402

out: dict = {}


def check(cond, msg):
    if not cond:
        raise AssertionError(f"{out.get('scenario')}: {msg}")


def emit(**kw):
    out.update(kw)
    print("RESULT " + json.dumps(out), flush=True)


def leftovers():
    return sorted(p.name for p in Path(os.environ["TMP"]).iterdir()
                  if p.name.startswith("rf_bare_data_"))


def py_leftovers():
    return sorted(p.name for p in Path(os.environ["TMP"]).iterdir()
                  if p.name.startswith("rf_pytest_data_"))


def mod(name):
    return importlib.import_module("build.tests." + name)


def light(name):
    loader = unittest.TestLoader()
    return loader.loadTestsFromName(name)


W4_LIGHT = "build.tests.test_w4_study_design.StudyDesignBoundaryNails.test_non_dict_input_raises_value_error"
AUTH_LIGHT = ("build.tests.test_2_1_5_auth_remember_reset.ResetCodeStorePins."
              "test_issue_consume_one_shot_and_plaintext_never_at_rest")


class RootProbe(unittest.TestCase):
    """块间边界观测（C）：根存活、键钉、冻结==运行期==根（零分叉）。"""

    def test_probe(self):
        import custom_backend.main as m
        import custom_backend.auth as a
        root = os.environ.get(DATA)
        self.assertTrue(root and os.path.isdir(root), root)
        self.assertEqual(str(Path(m.DATA_DIR).resolve()), str(Path(root).resolve()))
        self.assertEqual(str(Path(a.data_dir()).resolve()), str(Path(root).resolve()))


def run_suite(parts):
    suite = unittest.TestSuite()
    for part in parts:
        if part == "probe":
            suite.addTest(RootProbe("test_probe"))
        else:
            suite.addTest(light(part))
    return unittest.TextTestRunner(verbosity=0).run(suite)


# ---------------------------------------------------------------- helper 合同


def sc_helper_scope():
    with _bare_env.bare_scope() as gen:
        root = str(gen.root)
        check(gen.owned_root is not None and os.environ.get(DATA) == root, "root pinned")
        t1 = _bare_env.acquire(mode="server")
        t2 = _bare_env.acquire(mode="server")
        check(t1 is not t2, "distinct tokens")
        _bare_env.release(t1)
        _bare_env.release(t1)  # 幂等重复释放
        _bare_env.release(t2)
        check(os.path.isdir(root), "owner keeps root after all borrows released")
        check(os.environ.get(DATA) == root, "env still pinned (owner contract)")
    check(not os.path.exists(root), "scope exit removed owned root")
    check(DATA not in os.environ and MODE not in os.environ, "entries restored")
    check(_bare_env.live_owner() is None, "no live owner after close")
    # 旧代 token 惰性：不得复活任何状态
    _bare_env.release(t1)
    check(_bare_env.live_owner() is None, "stale token stays inert")
    # 关闭后再裸 acquire：无缓存后端 → 新进程代允许；再显式结束
    t3 = _bare_env.acquire(mode="server")
    root3 = str(_bare_env._generation.root)
    check(os.path.isdir(root3) and root3 != root, "fresh process owner after close")
    _bare_env.close_owner()
    check(not os.path.exists(root3) and DATA not in os.environ, "explicit owner end")
    emit(ok=True)


def sc_helper_external():
    ext = Path(os.environ["TMP"]) / "ext_root"
    ext.mkdir(exist_ok=True)
    (ext / "sentinel.txt").write_text("keep", encoding="utf-8")
    os.environ[DATA] = str(ext)
    os.environ[MODE] = "desktop"
    with _bare_env.bare_scope():
        gen = _bare_env._generation
        check(gen.owned_root is None and str(gen.root) == str(ext), "borrowed external")
        t = _bare_env.acquire(mode="server")
        check(os.environ.get(MODE) == "server", "mode applied during run")
        _bare_env.release(t)
    check(os.environ.get(DATA) == str(ext), "external entry restored")
    check(os.environ.get(MODE) == "desktop", "mode restored (F4)")
    check((ext / "sentinel.txt").read_text(encoding="utf-8") == "keep", "external intact")
    emit(ok=True)


def sc_helper_failure_paths():
    real = shutil.rmtree

    def boom(path, *a, **k):
        raise OSError(32, "probe injected deletion failure", str(path))

    # 1) 连续拒删 ×2 → pending + 重钉（含键 pop / 改写两种中间态）+ 可观察
    with _bare_env.bare_scope() as gen:
        root = gen.owned_root
        shutil.rmtree = boom
        os.environ.pop(DATA, None)
        _bare_env.close_owner()
        check(os.environ.get(DATA) == root, "repinned after pop (pending)")
        check(os.path.isdir(root) and gen.status == _bare_env.PENDING, "pending kept")
        os.environ[DATA] = "E:/foreign/scratch"
        _bare_env.retry_cleanup()
        check(os.environ.get(DATA) == root and gen.status == _bare_env.PENDING,
              "repinned after rewrite; still pending")
        shutil.rmtree = real
        # 2) 根在 pending 中被外部删除 → 幂等完成（B），不重钉、不残留
        real(root)
        os.environ.pop(DATA, None)
        status = _bare_env.retry_cleanup()
        check(status == _bare_env.CLOSED and not os.path.exists(root), "B complete")
        check(DATA not in os.environ, "no dead path left in env")
    check(_bare_env.live_owner() is None, "owner gone after B completion")
    # 3) close 时根已先消失 → 直接幂等完成（B）
    with _bare_env.bare_scope() as gen:
        real(gen.owned_root)
        os.environ.pop(DATA, None)
    check(DATA not in os.environ and _bare_env.live_owner() is None, "B at close")
    emit(ok=True)


def sc_helper_atexit():
    # 进程 owner：无 release/无显式结束 → atexit 兜底（父进程验证无残留）
    _bare_env.acquire(mode="server")
    out["root"] = str(_bare_env._generation.owned_root)
    print("RESULT " + json.dumps(out), flush=True)
    sys.exit(0)


# ---------------------------------------------------------------- A：初始化


def _load_conftest_within_pytest(inject: str):
    """真实 pytest.main 路径驱动 conftest 初始化失败（A）。"""
    import pytest
    real = shutil.rmtree

    def boom(path, *a, **k):
        if "rf_pytest_data_" in str(path):
            raise OSError(32, "probe injected deletion failure", str(path))
        return real(path, *a, **k)

    scratch = None
    if inject == "gate":
        scratch = tempfile.mkdtemp(prefix="rf_probe_scratch_")
        os.environ[DATA] = str(scratch)
        import custom_backend.main  # noqa: F401  冻结到 scratch
        del os.environ[DATA]
    elif inject == "import":
        sys.modules["custom_backend"] = None
    shutil.rmtree = boom
    try:
        rc = pytest.main(["--co", "-q", "build/tests/test_w4_study_design.py",
                          "-p", "no:cacheprovider"])
    finally:
        shutil.rmtree = real
        sys.modules.pop("custom_backend", None)
    return rc, scratch


def sc_a_conftest_init():
    variant = sys.argv[2]
    rc, scratch = _load_conftest_within_pytest(variant)
    out["rc"] = int(rc)
    out["env_data_absent"] = DATA not in os.environ
    out["handshake_rolled_back"] = _bare_env._pytest_session is None
    pending = _bare_env.live_owner()
    out["pending_owner"] = bool(pending and pending.status == _bare_env.PENDING
                                and pending.cleanup_failure is not None)
    check(rc != 0, "init failure must fail the pytest run")
    check(out["env_data_absent"], "entry data key restored despite failures (A)")
    check(out["handshake_rolled_back"], "handshake rolled back (A/D)")
    check(out["pending_owner"], "deletion failure kept observable pending state")
    # 初始化已恢复入口 → 后续重试不得再篡改宿主入口（A）
    os.environ.pop(DATA, None)
    status = _bare_env.finish_session()
    check(status == _bare_env.CLOSED, "retry after abort completes")
    check(DATA not in os.environ, "retry must NOT re-pin after init restore (A)")
    check(not py_leftovers(), "no pytest roots left after retry")
    if scratch is not None:
        check(os.path.isdir(scratch), "gate-variant scratch untouched")
    emit(ok=True)


# ---------------------------------------------------------------- B：收尾


def _run_pytest_with(code: str, name: str):
    scratch_test = TESTS_DIR / name
    scratch_test.write_text(code, encoding="utf-8")
    import pytest
    try:
        return pytest.main([f"build/tests/{name}", "-q", "-p", "no:cacheprovider"])
    finally:
        scratch_test.unlink(missing_ok=True)


def sc_b_root_vanished():
    code = (
        "import os, shutil\n"
        "def test_vanish_owned_root_then_pop():\n"
        "    root = os.environ.get('COBOOKSHELF_DATA_DIR')\n"
        "    assert root and 'rf_pytest_data_' in root, root\n"
        "    shutil.rmtree(root)\n"
        "    os.environ.pop('COBOOKSHELF_DATA_DIR', None)\n"
    )
    rc = _run_pytest_with(code, "test_zz_r4_vanish.py")
    out["rc"] = int(rc)
    check(rc == 0, "vanishing root is idempotent completion, not a failure (B)")
    check(DATA not in os.environ, "entry restored (no dead path re-pin)")
    check(not py_leftovers(), "no leftover roots")
    # 重复收尾幂等
    check(_bare_env.finish_session() == _bare_env.CLOSED, "repeat teardown idempotent")
    check(DATA not in os.environ and not py_leftovers(), "still clean after repeat")
    emit(ok=True)


def sc_b_refusal_rc1():
    # PYTHONWARNINGS=error（父进程已设）：警告升级不得中断收尾控制流（A/B）
    code = (
        "import shutil\n"
        "def test_ok():\n"
        "    assert True\n"
        "def test_break_rmtree():\n"
        "    real = shutil.rmtree\n"
        "    def boom(path, *a, **k):\n"
        "        raise OSError(32, 'probe injected deletion failure', str(path))\n"
        "    shutil.rmtree = boom  # 不还原：带入 sessionfinish\n"
    )
    real = shutil.rmtree
    rc = _run_pytest_with(code, "test_zz_r4_refuse.py")
    out["rc"] = int(rc)
    pending = _bare_env.live_owner()
    out["pending"] = bool(pending and pending.status == _bare_env.PENDING)
    out["repinned_to_existing"] = bool(
        pending and os.environ.get(DATA) == pending.owned_root
        and os.path.isdir(pending.owned_root))
    check(rc == 1, f"real pytest rc must escalate to 1, got {rc}")
    check(out["pending"] and out["repinned_to_existing"],
          "pending + re-pinned to verified existing root")
    # 重复失败仍安全
    _bare_env.finish_session()
    check(_bare_env.live_owner() is not None, "repeat failure keeps pending")
    # 解除注入 → 重试成功：恢复入口并清失败态
    shutil.rmtree = real
    status = _bare_env.finish_session()
    check(status == _bare_env.CLOSED, "retry success completes")
    check(DATA not in os.environ and not py_leftovers(), "entries restored after retry")
    emit(ok=True)


# ---------------------------------------------------------------- C：套件


def sc_c_scope_framework():
    # 1) 显式 scope：真实导入 w4+auth，w4→auth→w4 交错块全程同根零分叉
    with _bare_env.bare_scope() as gen:
        root = str(gen.root)
        m_w4 = mod("test_w4_study_design")
        m_auth = mod("test_2_1_5_auth_remember_reset")
        result = run_suite([W4_LIGHT, "probe", AUTH_LIGHT, "probe", W4_LIGHT, "probe"])
        out["c_run"] = result.testsRun
        out["c_failures"] = len(result.failures)
        out["c_errors"] = len(result.errors)
        check(result.wasSuccessful(),
              f"w4→auth→w4 must pass in one scope: {result.failures[:1]}{result.errors[:1]}")
        check(os.path.isdir(root) and os.environ.get(DATA) == root,
              "root alive through interleaved blocks (C)")
    check(not os.path.exists(root) and DATA not in os.environ and MODE not in os.environ,
          "scope finally removed root + restored entries")
    check(_bare_env.live_owner() is None, "no owner after scope")
    # 2) scope 关闭后再导入未缓存模块 → 先于业务硬拒（C，无默认 data/ 写）
    try:
        mod("test_upload_security_regression")
        raise AssertionError("re-import after scope close must hard-reject")
    except RuntimeError as exc:
        out["reimport_rejected"] = "refusing" in str(exc)
    check(out.get("reimport_rejected"), f"hard-reject message: {out.get('reimport_rejected')}")
    check(not (BOX / "data").exists(), "no default data/ created on reject")
    # 3) 已缓存模块再执行（不重新导入）→ setUpModule 硬拒
    result = run_suite([W4_LIGHT])
    out["cached_rerun_errors"] = len(result.errors)
    check(len(result.errors) == 1 and "shared-root" in result.errors[0][1],
          f"setUpModule hard-reject for cached module: {result.errors[:1]}")
    check(not (BOX / "data").exists(), "no default data/ written before reject")
    emit(ok=True)


def sc_c_process_owner():
    # 无显式 scope 的嵌入式 TextTestRunner：进程 owner 持根；返回≠所有者结束
    m_w4 = mod("test_w4_study_design")
    m_auth = mod("test_2_1_5_auth_remember_reset")
    root = str(_bare_env._generation.root)
    result = run_suite([W4_LIGHT, "probe", AUTH_LIGHT, W4_LIGHT])
    check(result.wasSuccessful(), "process-owner run green")
    check(os.path.isdir(root) and os.environ.get(DATA) == root,
          "root alive after runner return (documented contract)")
    _bare_env.close_owner()  # 显式结束（或 atexit）
    check(not os.path.exists(root) and DATA not in os.environ,
          "explicit owner end removes root + restores entries")
    emit(ok=True, c_run=result.testsRun)


# ---------------------------------------------------------------- D：接管


def sc_d_takeover():
    variant = sys.argv[2]  # owned | external
    if variant == "external":
        ext = Path(os.environ["TMP"]) / "ext_root"
        ext.mkdir(exist_ok=True)
        (ext / "sentinel.txt").write_text("keep", encoding="utf-8")
        os.environ[DATA] = str(ext)
        os.environ[MODE] = "desktop"
    m_auth = mod("test_2_1_5_auth_remember_reset")  # 建立裸进程 owner
    bare_root = os.environ.get(DATA)
    check(_bare_env.live_owner() is not None, "bare owner live before pytest")
    import pytest
    rc = pytest.main([
        "build/tests/test_w4_study_design.py::StudyDesignBoundaryNails::"
        "test_non_dict_input_raises_value_error",
        "-q", "-p", "no:cacheprovider"])
    out["refusal_rc"] = int(rc)
    check(rc != 0, "pytest with live bare owner must refuse (D)")
    out["env_untouched"] = os.environ.get(DATA) == bare_root
    out["owner_still_live"] = _bare_env.live_owner() is not None
    check(out["env_untouched"], "refusal left env untouched (D)")
    check(out["owner_still_live"], "refusal left lease abilities intact (D)")
    # 原 lease 仍可正常结束；atexit 亦有效
    m_auth.tearDownModule()
    _bare_env.close_owner()
    if variant == "external":
        check(os.environ.get(MODE) == "desktop", "mode restored after close (D/F4)")
        check((ext / "sentinel.txt").read_text(encoding="utf-8") == "keep", "ext intact")
        check(os.environ.get(DATA) == str(ext),
              "external entry string restored (borrow semantics)")
    else:
        check(not os.path.exists(bare_root), "owned root removed by close after refusal")
        check(DATA not in os.environ, "entries restored (absent entry)")
    check(not leftovers(), "no bare roots left")
    emit(ok=True)


def sc_d_plain_pytest():
    import pytest
    rc = pytest.main(["build/tests/test_w4_study_design.py::"
                      "StudyDesignBoundaryNails::test_non_dict_input_raises_value_error",
                      "-q", "-p", "no:cacheprovider"])
    out["rc"] = int(rc)
    check(rc == 0, "plain pytest (no bare owner) must succeed")
    check(_bare_env._pytest_session is None, "handshake ended with session (D)")
    check(_bare_env.live_owner() is None, "no owner after clean session")
    check(DATA not in os.environ, "entries restored after session")
    # 会话结束后同进程再起裸套件：缓存根不匹配 → 安全硬拒（D）
    try:
        _bare_env.acquire(mode="server")
        raise AssertionError("bare acquire after pytest session must hard-reject")
    except RuntimeError as exc:
        out["post_session_reject"] = "refusing" in str(exc)
    check(out.get("post_session_reject"), "cached-root mismatch hard-reject (D)")
    emit(ok=True)


def sc_d_initfail_rollback():
    sys.modules["custom_backend"] = None
    import pytest
    try:
        rc = pytest.main(["--co", "-q", "build/tests/test_w4_study_design.py",
                          "-p", "no:cacheprovider"])
    finally:
        sys.modules.pop("custom_backend", None)
    out["rc"] = int(rc)
    out["handshake_none"] = _bare_env._pytest_session is None
    out["no_live_owner"] = _bare_env.live_owner() is None
    check(rc != 0 and out["handshake_none"] and out["no_live_owner"],
          "init failure: rc!=0 + handshake rolled back + no owner (A/D)")
    # 可重入：失败后裸套件在同进程仍可正常建立（后端未缓存成功）
    with _bare_env.bare_scope() as gen:
        check(gen.owned_root is not None, "bare scope usable after init failure")
    check(DATA not in os.environ, "entries restored")
    emit(ok=True)


# ---------------------------------------------------------------- 混合回归


def sc_mixed_pytest():
    import pytest
    rc = pytest.main([
        "build/tests/test_2_1_5_appraisal.py",
        "build/tests/test_2_1_5_ai_formats.py",
        "build/tests/test_2_1_5_auth_remember_reset.py",
        "build/tests/test_upload_security_regression.py",
        "build/tests/test_r6_undo_collab_fixes.py",
        "build/tests/test_w4_study_design.py",
        "-q", "-p", "no:cacheprovider"])
    out["rc"] = int(rc)
    check(rc == 0, f"six-module pytest batch must pass, rc={rc}")
    check(DATA not in os.environ, "session end restored entries")
    check(not (BOX / "data").exists(), "no default data/")
    emit(ok=True)


# ---------------------------------------------------------------- SafetyR5
# review_safety_r4.md A1/B1/B2/C1/C2/C3/D1/D2 的真实框架红→绿回归（修前
# 行为已在独立箱留证；此处钉修后合同）。


def sc_r5_a1_pre_adopt():
    # A1：资源创建后的每个异常窗口都有责任方兜底。修前：conftest 在
    # mkdtemp+写键之后、adopt 之前 resolve 异常 → abort 面对无根占位代，
    # 根/键留存、live_owner=None、finish 不能清理。修后：adopt 先登记责任，
    # resolve 在代内失败 → abort 恢复两键/删根（拒删则可观察 pending）/
    # 回滚握手，原异常对象/traceback 原样上抛。
    from pathlib import Path as _P
    real_resolve = _P.resolve
    seen = []

    def deny_owned(self, *a, **k):
        if self.name.startswith("rf_pytest_data_"):
            seen.append(str(self))
            raise PermissionError("R5 injected resolution failure inside adopt window")
        return real_resolve(self, *a, **k)

    import pytest
    real_abort = _bare_env.abort_session
    info = {}

    def traced_abort():
        before = sys.exc_info()
        try:
            return real_abort()
        finally:
            after = sys.exc_info()
            info.update(exc_type=type(before[1]).__name__ if before[1] else None,
                        same_exception=before[1] is after[1],
                        same_traceback=before[2] is after[2])

    _bare_env.abort_session = traced_abort
    _P.resolve = deny_owned
    try:
        rc = pytest.main(["--co", "-q", "build/tests/test_w4_study_design.py",
                          "-p", "no:cacheprovider"])
    finally:
        _P.resolve = real_resolve
        _bare_env.abort_session = real_abort
    out["rc"] = int(rc)
    root = seen[0] if seen else None
    check(rc != 0, "init failure must fail the pytest run (A1)")
    check(root is not None, "injection window reached: owned root created, resolve denied")
    check(info.get("exc_type") == "PermissionError",
          f"original exception type preserved through abort: {info}")
    check(info.get("same_exception") and info.get("same_traceback"),
          f"abort must not replace the active exception/traceback: {info}")
    check(DATA not in os.environ and MODE not in os.environ, "both entry keys restored (A1)")
    check(_bare_env._pytest_session is None, "handshake rolled back (A1)")
    check(not os.path.exists(root), "owned root cleaned by abort — no leak (A1)")
    check(_bare_env.live_owner() is None, "no live owner left behind (A1)")
    check(_bare_env.finish_session() == _bare_env.CLOSED, "repeat teardown idempotent")
    check(not py_leftovers(), "no pytest roots leaked")

    # 变体：同窗口 resolve 失败叠加拒删 → 入口先恢复 + 可观察 pending；其后
    # 的重试不得再篡改宿主环境（A1/B1 两合同区分）。
    real_remove = shutil.rmtree

    def deny_remove(path, *a, **k):
        if "rf_pytest_data_" in str(path):
            raise OSError(13, "R5 injected deletion denial")
        return real_remove(path, *a, **k)

    _P.resolve = deny_owned
    shutil.rmtree = deny_remove
    try:
        rc2 = pytest.main(["--co", "-q", "build/tests/test_w4_study_design.py",
                           "-p", "no:cacheprovider"])
    finally:
        _P.resolve = real_resolve
        shutil.rmtree = real_remove
    gen2 = _bare_env.live_owner()
    out["rc2"] = int(rc2)
    out["pending2"] = bool(gen2 and gen2.status == _bare_env.PENDING)
    check(rc2 != 0, "double failure must fail the pytest run")
    check(out["pending2"] and gen2.cleanup_failure is not None,
          "refused deletion keeps an observable pending owner (A1)")
    check(gen2.entries_restored and DATA not in os.environ and MODE not in os.environ,
          "entries restored first; pending holds only retry responsibility (A1)")
    host_dir = Path(os.environ["TMP"]) / "host-after-abort"
    os.environ[DATA] = str(host_dir)
    os.environ[MODE] = "host-after"
    status = _bare_env.retry_cleanup()
    check(status == _bare_env.CLOSED, "retry completes after abort (A1)")
    check(os.environ.get(DATA) == str(host_dir) and os.environ.get(MODE) == "host-after",
          "retry after restored entries must NOT re-pin/touch host env (A1/B1)")
    check(not os.path.exists(gen2.owned_root) and not py_leftovers(), "root gone after retry")
    os.environ.pop(DATA, None)
    os.environ.pop(MODE, None)
    emit(ok=True)


def sc_r5_b1_silent_pin():
    # B1：rmtree 静默失败（正常返回但根仍在）的 retry 也必须安全重钉到实际
    # 存在的自有根并保留可观察 pending（修前：pop DATA 后仅 pending 不重钉，
    # 键空会让后续写入回落默认 data/）。
    real = shutil.rmtree

    def boom(path, *a, **k):
        raise OSError(32, "R5 injected deletion failure", str(path))

    with _bare_env.bare_scope() as gen:
        root = gen.owned_root
        shutil.rmtree = boom
        _bare_env.close_owner()
        check(gen.status == _bare_env.PENDING, "pending after refused delete")
        os.environ.pop(DATA, None)

        def false_success(path, *a, **k):
            if str(path) == root:
                return None
            return real(path, *a, **k)

        shutil.rmtree = false_success
        status = _bare_env.retry_cleanup()
        out["silent_retry"] = status
        out["data_after_silent"] = os.environ.get(DATA)
        check(status == _bare_env.PENDING, "silent rmtree failure keeps pending (B1)")
        check(os.environ.get(DATA) == root and os.path.isdir(root),
              "silent failure must safely re-pin to the verified existing root (B1)")
        check(gen.cleanup_failure is not None and "still exists" in str(gen.cleanup_failure),
              f"observable failure updated for the silent denial: {gen.cleanup_failure}")
        shutil.rmtree = real
        status2 = _bare_env.retry_cleanup()
        check(status2 == _bare_env.CLOSED and not os.path.exists(root),
              "real retry completes (B1)")
        check(DATA not in os.environ, "entries restored after successful retry")
    check(_bare_env.live_owner() is None, "owner fully closed")
    emit(ok=True)


def sc_r5_b2_abort_vanished():
    # B2：abort 的 rmtree 删根后抛 FileNotFound → 根确已不存在=立即幂等完成
    # （修前：错误保留 pending，下一次 retry 才关闭）。
    import builtins
    real_import, real_remove = builtins.__import__, shutil.rmtree
    destroyed = []

    def no_backend(name, *a, **k):
        if name == "custom_backend.main":
            raise ModuleNotFoundError("R5 backend fail before vanished abort")
        return real_import(name, *a, **k)

    def vanish(path, *a, **k):
        if "rf_pytest_data_" in str(path):
            real_remove(path, *a, **k)
            destroyed.append(str(path))
            raise FileNotFoundError("removed root before injected child failure")
        return real_remove(path, *a, **k)

    import pytest
    builtins.__import__, shutil.rmtree = no_backend, vanish
    try:
        rc = pytest.main(["--co", "-q", "build/tests/test_w4_study_design.py",
                          "-p", "no:cacheprovider"])
    finally:
        builtins.__import__, shutil.rmtree = real_import, real_remove
    out["rc"] = int(rc)
    gen = _bare_env.live_owner()
    check(rc != 0, "backend import failure must fail the pytest run")
    check(destroyed and not os.path.exists(destroyed[0]), "root really removed on disk")
    check(gen is None, "vanished-during-abort completes immediately: no pending owner (B2)")
    check(DATA not in os.environ and MODE not in os.environ, "entries restored (B2)")
    check(_bare_env._pytest_session is None, "handshake rolled back (B2)")
    check(_bare_env.finish_session() == _bare_env.CLOSED, "repeat finish idempotent")
    emit(ok=True)


def sc_r5_c1_recreated():
    # C1：同一外部根的真实复用（身份可证）仍允许；根被删后在原路径重建空
    # 目录（inode 变）→ 新 scope 仅路径同名必须**先于环境/业务写**硬拒（修
    # 前：缓存后端按路径放行并往新空根写 auth_reset_codes.db）。
    ext = Path(os.environ["TMP"]) / "ext_keep"
    ext.mkdir()
    (ext / "sentinel.txt").write_text("keep", encoding="utf-8")
    os.environ[DATA] = str(ext)
    # 1) 外部根第一届：真实 auth 用例、后端冻结到 ext、身份基线建立
    with _bare_env.bare_scope():
        mod("test_2_1_5_auth_remember_reset")
        r1 = run_suite([AUTH_LIGHT])
        check(r1.wasSuccessful(), f"first suite green: {r1.errors[:1]}")
        check((ext / "auth_reset_codes.db").exists(), "db created in the external root")
    # 2) 同一目录（身份可证）复用：必须仍然放行（不过度拦截）
    os.environ[DATA] = str(ext)
    with _bare_env.bare_scope():
        r2 = run_suite([AUTH_LIGHT])
        check(r2.wasSuccessful(), f"same-dir reuse green (provable identity): {r2.errors[:1]}")
    out["same_dir_reuse"] = True
    # 3) 根被同名替换（R6 N5 确定性构造）：旧目录**改名保活**（inode 不回
    #    收池）+ 原路径新建独立目录——两个同时存在的目录对象身份必然不同，
    #    不依赖「删除后 inode 不被即时复用」的平台假设（POSIX 可能立即复用
    #    inode，NTFS 本机虽不同但不以此作跨平台断言）。新 scope 先于业务写
    #    硬拒（修前 R4：缓存后端按路径放行并往新空根写 auth_reset_codes.db）。
    aside = ext.with_name(ext.name + "-aside")
    os.rename(ext, aside)
    ext.mkdir()
    check(ext.stat().st_ino != aside.stat().st_ino,
          "replacement is a provably distinct directory object (rename-kept old dir alive)")
    check(not list(ext.iterdir()), "replacement root starts empty")
    os.environ[DATA] = str(ext)
    rejected = None
    try:
        with _bare_env.bare_scope():
            raise AssertionError("same-named recreated root must be rejected (C1)")
    except RuntimeError as exc:
        rejected = str(exc)
    out["recreated_rejected"] = bool(
        rejected and ("identity" in rejected or "generation" in rejected))
    check(out["recreated_rejected"], f"identity-based rejection message: {rejected}")
    check(not (ext / "auth_reset_codes.db").exists(),
          "NO business write into the recreated root (C1)")
    check(os.environ.get(DATA) == str(ext), "host env untouched by the refusal (C1)")
    check((ext / "sentinel.txt").exists() is False,
          "old sentinel gone from the path with the old dir (kept alive aside)")
    os.environ.pop(DATA, None)
    shutil.rmtree(ext, ignore_errors=True)   # 清理场景自建的替换目录
    shutil.rmtree(aside, ignore_errors=True)  # 与改名保活的旧目录
    emit(ok=True)


def sc_r5_c2_pending_import():
    # C2：pending owner 存在时模块导入（含导入期业务副作用，如 r6 模板库
    # init_db）必须在 acquire 即拒（修前：先发活 token、先建模板库，
    # setUpModule 才拒）。
    import coscreen.db as db
    real_remove, real_init = shutil.rmtree, db.init_db
    calls = []

    def deny(path, *a, **k):
        if "rf_bare_data_" in str(path):
            raise OSError(13, "R5 pending owner denial")
        return real_remove(path, *a, **k)

    def traced_init(*a, **k):
        calls.append(str(a[0] if a else k))
        return real_init(*a, **k)

    try:
        shutil.rmtree = deny
        with _bare_env.bare_scope() as gen:
            root = gen.owned_root
        check(gen.status == _bare_env.PENDING, "pending owner established")
        borrows_before = gen.borrows
        db.init_db = traced_init
        try:
            importlib.import_module("build.tests.test_r6_undo_collab_fixes")
            raise AssertionError("import against a pending owner must hard-reject (C2)")
        except RuntimeError as exc:
            out["import_rejected"] = "refusing" in str(exc)
        check(out.get("import_rejected"), f"C2 rejection message: {out.get('import_rejected')}")
        check(not calls, "NO import-time business write — init_db never ran (C2)")
        check(gen.borrows == borrows_before, "no borrow token issued (C2)")
        check(_bare_env.live_owner() is gen and gen.status == _bare_env.PENDING,
              "pending owner untouched by the refusal (C2)")
    finally:
        shutil.rmtree, db.init_db = real_remove, real_init
    status = _bare_env.retry_cleanup()
    check(status == _bare_env.CLOSED and not os.path.exists(root),
          "original owner retry closes after refusal")
    emit(ok=True)


def sc_r5_c3_slashes():
    # C3（+ R6 N1 可移植化）：构造「两串不同、resolve 同一目录」的合法外部
    # 根串，证明运行期校验按规范路径身份放行且入口恢复原串（修前 R4：字面
    # 比较假拒 w4/auth/w4 全部 setUpModule）。R5 版在 POSIX 必红——as_posix
    # 与 str(Path) 在 POSIX 相同，前置断言必失败；R6 按平台构造（无 skip）：
    # - Windows：整串前斜杠（E:/…，原始反例）——str(Path) 折叠为反斜杠串；
    # - POSIX：含冗余 ``.`` 段的合法路径串（…/./name）——pathlib 字符串形式
    #   折叠 ``.`` 段故两串不同，resolve 折叠 ``.`` 故同一目录。不写反斜杠
    #   （POSIX 下那是真实文件名字符，不是分隔符）。
    # 报告口径：Windows 为本机真实运行；POSIX 构造经 PurePosixPath/词法等价
    # 静态实证（见 probes/preflight），不声称本机原生 POSIX 运行。
    ext = Path(os.environ["TMP"]) / "ext_slash"
    ext.mkdir()
    (ext / "sentinel.txt").write_text("keep", encoding="utf-8")
    out["c3_platform"] = os.name
    if os.name == "nt":
        external_string = ext.as_posix()
        out["c3_variant"] = "nt_forward_slash"
    else:
        external_string = f"{ext.parent}/./{ext.name}"
        out["c3_variant"] = "posix_redundant_dot_segment"
    out["c3_external_string"] = external_string
    out["c3_path_str_form"] = str(Path(external_string))
    os.environ[DATA] = external_string
    check(external_string != str(Path(external_string)),
          "the two legal spellings must differ as strings (precondition)")
    check(str(Path(external_string).resolve()) == str(ext.resolve()),
          "both spellings must resolve to the same directory (precondition)")
    with _bare_env.bare_scope():
        mod("test_w4_study_design")
        mod("test_2_1_5_auth_remember_reset")
        result = run_suite([W4_LIGHT, AUTH_LIGHT, W4_LIGHT])
        out["c3_run"] = result.testsRun
        out["c3_errors"] = len(result.errors)
        check(result.wasSuccessful(),
              f"w4→auth→w4 must pass on the variant-spelled external root: "
              f"{result.errors[:1]}{result.failures[:1]}")
        check(result.testsRun == 3, f"all three blocks executed, got {result.testsRun}")
    check(os.environ.get(DATA) == external_string,
          "original external string preserved on entry restore (C3)")
    check(MODE not in os.environ, "mode entry restored (absent)")
    check((ext / "sentinel.txt").read_text(encoding="utf-8") == "keep", "external intact")
    os.environ.pop(DATA, None)
    shutil.rmtree(ext, ignore_errors=True)
    emit(ok=True)


def sc_r5_d1_package_alias():
    # D1：包路径 from build.tests import _bare_env 与顶层 import _bare_env 共
    # 享同一模块实例/账本（修前：两个对象，包路径建的 scope 对 pytest 不可
    # 见 → 真实 pytest rc0 错误放行）。修后：真实 pytest 对该 live owner
    # 明确拒绝，原 lease 仍可正常结束。
    pkg = importlib.import_module("build.tests._bare_env")
    import _bare_env as top
    out["same_object"] = pkg is top
    check(pkg is top, "package-path and top-level imports share one instance (D1)")
    check(sys.modules.get("build.tests._bare_env") is sys.modules.get("_bare_env"),
          "sys.modules entries aliased to the canonical instance (D1)")
    check(Path(top.__file__).resolve() == Path(pkg.__file__).resolve(), "same file")
    w4_node = (str(TESTS_DIR / "test_w4_study_design.py")
               + "::StudyDesignBoundaryNails::test_non_dict_input_raises_value_error")
    with pkg.bare_scope() as owner:
        root = str(owner.root)
        import pytest
        rc = pytest.main([w4_node, "-q", "-p", "no:cacheprovider"])
        out["refusal_rc"] = int(rc)
        check(rc != 0, "pytest must refuse a live package-path-created owner (D1)")
        check(top.live_owner() is owner,
              "shared ledger: the top-level module sees the package-path owner (D1)")
        check(os.environ.get(DATA) == root, "refusal left the pinned env untouched (D1)")
    check(not os.path.exists(root) and DATA not in os.environ,
          "owner closed normally after the refusal")
    check(top.live_owner() is None, "ledger clean after close")
    emit(ok=True)


def sc_r5_d2_pending_refusal():
    # D2：已有 pending 裸 owner 时 pytest 必须先于任何环境/租约/atexit 变更
    # 拒绝（修前：configure 先 retry_cleanup，把宿主 DATA 重钉到 pending 根
    # 后才拒）。重试责任归原 owner。
    real = shutil.rmtree

    def deny(path, *a, **k):
        if "rf_bare_data_" in str(path):
            raise OSError(13, "R5 existing pending owner denial")
        return real(path, *a, **k)

    try:
        shutil.rmtree = deny
        with _bare_env.bare_scope() as gen:
            root = gen.owned_root
        check(gen.status == _bare_env.PENDING, "pending owner established")
        original = str(Path(os.environ["TMP"]) / "host-external")
        os.environ[DATA] = original
        before_mode = os.environ.get(MODE)
        import pytest
        w4_node = (str(TESTS_DIR / "test_w4_study_design.py")
                   + "::StudyDesignBoundaryNails::test_non_dict_input_raises_value_error")
        rc = pytest.main([w4_node, "-q", "-p", "no:cacheprovider"])
        out["refusal_rc"] = int(rc)
        check(rc != 0, "pytest must refuse (D2)")
        check(os.environ.get(DATA) == original,
              "refusal left the host DATA string untouched — no sneaky re-pin (D2)")
        check(os.environ.get(MODE) == before_mode, "mode untouched (D2)")
        live = _bare_env.live_owner()
        check(live is gen and gen.status == _bare_env.PENDING and gen.borrows == 0,
              "pending owner + retry responsibility left to its original owner (D2)")
    finally:
        shutil.rmtree = real
    status = _bare_env.retry_cleanup()
    check(status == _bare_env.CLOSED and not os.path.exists(root),
          "the original owner's retry closes after the refusal")
    check(DATA not in os.environ, "entries restored after the retry")
    emit(ok=True)


# ---------------------------------------------------------------- SafetyR6
# review_safety_r5_ds.md N1–N5 的定向永久回归（只钉修复合同，不重写生命周期）。


def sc_r6_n2_blank_bare():
    # N2/I9（bare 入口）：显式空串/仅空白 DATA 不得被借为 cwd——bare_scope
    # 与 acquire 惰性进程 owner 都必须先于建根/环境改写/业务写拒绝，入口原
    # 串保留、零 owned 根、零 cwd 业务写；缺省（未设置）仍合法（控制对照）。
    for bad in ("", "   "):
        os.environ[DATA] = bad
        # bare_scope 入口
        rejected = None
        try:
            with _bare_env.bare_scope():
                raise AssertionError("blank DATA must be rejected at bare_scope entry (N2)")
        except RuntimeError as exc:
            rejected = str(exc)
        check(rejected is not None and "refusing" in rejected,
              f"blank DATA bare_scope rejection message: {rejected}")
        check(os.environ.get(DATA) == bad, "entry original string preserved (N2)")
        check(_bare_env.live_owner() is None, "no owner created by the refusal (N2)")
        check(not leftovers(), "no owned root created by the refusal (N2)")
        # acquire 惰性进程 owner 入口（同一 _new_generation 硬拒）
        rejected = None
        try:
            _bare_env.acquire(mode="server")
            raise AssertionError("blank DATA must be rejected at acquire (N2)")
        except RuntimeError as exc:
            rejected = str(exc)
        check(rejected is not None and "refusing" in rejected,
              f"blank DATA acquire rejection message: {rejected}")
        check(os.environ.get(DATA) == bad, "entry string still preserved (N2)")
        check(_bare_env.live_owner() is None, "no lazy process owner created (N2)")
        check(not leftovers(), "no owned root created (N2)")
    check(not (BOX / "data").exists(), "no business write into cwd (blank resolves to cwd) (N2)")
    # 控制对照：缺省（键未设置）仍合法——自有临时根
    os.environ.pop(DATA, None)
    with _bare_env.bare_scope() as gen:
        check(gen.owned_root is not None and os.path.isdir(gen.owned_root),
              "unset DATA still creates its own temporary root (N2 control)")
        tok = _bare_env.acquire(mode="server")
        _bare_env.release(tok)
    check(_bare_env.live_owner() is None and DATA not in os.environ
          and not leftovers(), "control owner closed clean (N2)")
    emit(ok=True)


def sc_r6_n2_blank_pytest():
    # N2/I9（真实 pytest 入口）：conftest adopt_session_root 对空串/仅空白根
    # 在改环境/建根/后端导入/业务写之前拒绝——真实 pytest.main 必失败、入口
    # 原串保留、握手回滚、零 owner/零 pytest 根/零 cwd 业务写。
    scratch = TESTS_DIR / "test_zz_r6_n2gate.py"
    scratch.write_text(
        "def test_never_reached():\n"
        "    raise AssertionError('session must be refused before tests run (N2)')\n",
        encoding="utf-8")
    import pytest
    try:
        for bad in ("", "   "):
            os.environ[DATA] = bad
            rc = pytest.main([f"build/tests/{scratch.name}", "-q",
                              "-p", "no:cacheprovider"])
            out[f"rc_{len(bad)}"] = int(rc)
            check(rc != 0, f"real pytest must refuse blank DATA ({bad!r}), rc={rc}")
            check(os.environ.get(DATA) == bad,
                  "entry original blank string preserved by the refusal (N2)")
            check(_bare_env._pytest_session is None, "handshake rolled back (N2)")
            check(_bare_env.live_owner() is None, "no session owner registered (N2)")
            check(not py_leftovers(), "no pytest root created (N2)")
    finally:
        scratch.unlink(missing_ok=True)
    check(not (BOX / "data").exists(), "no business write into cwd (N2)")
    check(not (TESTS_DIR / "test_zz_r6_n2gate.py").exists(), "scratch test cleaned")
    os.environ.pop(DATA, None)
    emit(ok=True)


def sc_r6_n3_live_swap_acquire():
    # N3/I10：live RUNNING owner 下外部根被同名替换（N5 确定性构造：旧目录
    # 改名保活+原路径新目录）→ acquire（六模块导入入口）必须在发放 token/
    # 施加模式之前复用运行期身份检查硬拒，导入期模板业务（r6 init_db）零执
    # 行；borrows/env/mode 原样；同根复用不误拒；环境漂移与根不在同样在导入
    # 期拒绝。修前（R5 终态）：acquire 放行+borrows 增、r6 导入完成且 init_db
    # 已跑、身份拒绝迟至 setUpModule。
    ext = Path(os.environ["TMP"]) / "ext_n3"
    shutil.rmtree(ext, ignore_errors=True)
    ext.mkdir()
    (ext / "sentinel.txt").write_text("keep", encoding="utf-8")
    os.environ[DATA] = str(ext)
    import coscreen.db as db
    real_init = db.init_db
    calls = []

    def traced_init(*a, **k):
        calls.append(str(a[0] if a else k))
        return real_init(*a, **k)

    drift = Path(os.environ["TMP"]) / "n3_drift"
    shutil.rmtree(drift, ignore_errors=True)
    drift.mkdir()
    try:
        with _bare_env.bare_scope() as gen:
            # 外部根第一届：真实 auth 用例、后端冻结到 ext、身份基线建立
            mod("test_2_1_5_auth_remember_reset")
            r1 = run_suite([AUTH_LIGHT])
            check(r1.wasSuccessful(), f"first suite green: {r1.errors[:1]}")
            # 同根复用不误拒（对照）：acquire 放行后立即归还
            borrows_before = gen.borrows
            tok = _bare_env.acquire(mode="server")
            check(gen.borrows == borrows_before + 1,
                  "same-root acquire must stay allowed (N3 control)")
            _bare_env.release(tok)
            check(gen.borrows == borrows_before, "control borrow released")
            # 同名替换（确定性：两个同时存在的目录对象身份必不同）
            aside = ext.with_name(ext.name + "-aside")
            os.rename(ext, aside)
            ext.mkdir()
            check(ext.stat().st_ino != aside.stat().st_ino,
                  "swap is a provably distinct directory object (N5 construction)")
            db.init_db = traced_init
            calls.clear()
            mode_before = os.environ.get(MODE)
            # 1) 直接 acquire（=模块导入入口同一路径）：必须拒，borrows/mode 原样
            rejected = None
            try:
                _bare_env.acquire(mode="n3_must_not_apply")
                raise AssertionError("acquire after live same-path swap must reject (N3)")
            except RuntimeError as exc:
                rejected = str(exc)
            check(rejected is not None and "refusing" in rejected.lower(),
                  f"acquire identity rejection message: {rejected}")
            check(gen.borrows == borrows_before,
                  "borrows unchanged by the refusal (N3)")
            check(os.environ.get(DATA) == str(ext),
                  "env original string untouched by the refusal (N3)")
            check(os.environ.get(MODE) == mode_before,
                  "mode not applied by the refused acquire (N3)")
            # 2) 真实未缓存模块导入（导入期业务）：同样在 acquire 即拒
            calls.clear()
            rejected = None
            try:
                importlib.import_module("build.tests.test_r6_undo_collab_fixes")
                raise AssertionError("module import after swap must reject at acquire (N3)")
            except RuntimeError as exc:
                rejected = str(exc)
            check(rejected is not None and "refusing" in rejected.lower(),
                  f"import-path rejection message: {rejected}")
            check(not calls,
                  "NO import-time business write — init_db never ran (N3)")
            check(gen.borrows == borrows_before,
                  "no borrow token issued via the failed import (N3)")
            # 3) 环境漂移：还原原目录后把 DATA 键挪到别处 → acquire 在导入期拒
            shutil.rmtree(ext)
            os.rename(aside, ext)  # 原目录（身份==基线）回到原路径
            os.environ[DATA] = str(drift)
            rejected = None
            try:
                _bare_env.acquire(mode="server")
                raise AssertionError("acquire with drifted env root must reject (N3)")
            except RuntimeError as exc:
                rejected = str(exc)
            check(rejected is not None and "refusing" in rejected.lower(),
                  f"env-drift rejection message: {rejected}")
            check(gen.borrows == borrows_before, "borrows still unchanged (N3)")
            os.environ[DATA] = str(ext)
            # 4) 根不在：删掉原目录 → acquire 在导入期拒
            shutil.rmtree(ext)
            rejected = None
            try:
                _bare_env.acquire(mode="server")
                raise AssertionError("acquire with missing root must reject (N3)")
            except RuntimeError as exc:
                rejected = str(exc)
            check(rejected is not None and "refusing" in rejected.lower(),
                  f"missing-root rejection message: {rejected}")
            check(gen.borrows == borrows_before, "borrows unchanged to the end (N3)")
    finally:
        db.init_db = real_init
    os.environ.pop(DATA, None)
    shutil.rmtree(ext, ignore_errors=True)
    shutil.rmtree(drift, ignore_errors=True)
    emit(ok=True)


def sc_r6_f1_pytest_live_acquire():
    # F1（review_safety_r6_ds.md；safety_cap_decision_20261114.md 用户授权的
    # 一次窄修）：真实 pytest 会话代（RUNNING）下，经 module import 触发的
    # acquire 必须先过 ensure_safe_for_execution 的运行期身份校验——根同名
    # 替换（N5 确定性构造：旧目录改名保活+原路径新目录）/DATA 空串/空白/
    # 漂移/缺根都在施加模式、发 token 与导入期业务（r6 模板 init_db）之前
    # 拒绝，mode/env/token/会话代原样；正常同根 module import 对照通过、
    # 拒绝后同根 acquire 恢复放行。修前（R6 终态）：会话分支只施 mode 就发
    # token——swap 后 r6 导入仍 ALLOWED、mode 施加、init_db 已跑
    # （n3-real-pytest.log rc0 实证）。
    scratch = TESTS_DIR / "test_zz_r6_f1gate.py"
    scratch.write_text(
        "import importlib\n"
        "import os\n"
        "import shutil\n"
        "from pathlib import Path\n"
        "\n"
        "import pytest\n"
        "\n"
        "DATA = 'COBOOKSHELF_DATA_DIR'\n"
        "MODE = 'REVIEWFLOW_MODE'\n"
        "\n"
        "\n"
        "def test_f1_live_session_acquire_gates():\n"
        "    import _bare_env\n"
        "    import coscreen.db as db\n"
        "\n"
        "    session = _bare_env._pytest_session\n"
        "    assert session is not None and session.status == _bare_env.RUNNING\n"
        "    root = Path(os.environ[DATA])\n"
        "    assert root.is_dir()\n"
        "    assert str(session.root) == str(root.resolve())\n"
        "    assert session.borrows == 0  # pytest 会话分支从不计借用\n"
        "\n"
        "    # 对照（正常同根 module import 通过）：未缓存模块导入 → acquire 放行、\n"
        "    # mode 施加、会话代/环境原串不变。\n"
        "    importlib.import_module('build.tests.test_2_1_5_ai_formats')\n"
        "    assert os.environ.get(MODE) == 'server'\n"
        "    assert _bare_env._pytest_session is session\n"
        "    assert session.borrows == 0\n"
        "    assert str(Path(os.environ[DATA]).resolve()) == str(root.resolve())\n"
        "\n"
        "    # 根同名替换（N5 确定性构造：两个同时存在的目录对象身份必不同）。\n"
        "    aside = root.with_name(root.name + '-aside')\n"
        "    os.rename(root, aside)\n"
        "    root.mkdir()\n"
        "    assert root.stat().st_ino != aside.stat().st_ino\n"
        "\n"
        "    calls = []\n"
        "    real_init = db.init_db\n"
        "\n"
        "    def traced_init(*a, **k):\n"
        "        calls.append(str(a[0] if a else k))\n"
        "        return real_init(*a, **k)\n"
        "\n"
        "    db.init_db = traced_init\n"
        "    drift = aside.parent / (root.name + '-drift')\n"
        "    try:\n"
        "        mode_pinned = os.environ.get(MODE)\n"
        "        data_pinned = os.environ[DATA]\n"
        "\n"
        "        # 1) 根同名替换：module import（导入期业务 r6 模板 init_db）→\n"
        "        #    acquire 先于 mode/token/业务拒绝（目录身份基线在会话建立时\n"
        "        #    已由 ensure_backend_session_root 记录，替换目录必不匹配）。\n"
        "        with pytest.raises(RuntimeError) as ei:\n"
        "            importlib.import_module('build.tests.test_r6_undo_collab_fixes')\n"
        "        assert 'identity' in str(ei.value) or 'generation' in str(ei.value)\n"
        "        assert calls == []  # 导入期业务零执行\n"
        "        assert os.environ.get(MODE) == mode_pinned\n"
        "        assert os.environ[DATA] == data_pinned\n"
        "        assert _bare_env._pytest_session is session\n"
        "        assert session.status == _bare_env.RUNNING and session.borrows == 0\n"
        "\n"
        "        # 2) 直接 acquire 同样拒（同一守卫路径）。\n"
        "        with pytest.raises(RuntimeError) as ei:\n"
        "            _bare_env.acquire(mode='f1_must_not_apply')\n"
        "        assert 'refusing' in str(ei.value).lower()\n"
        "        assert os.environ.get(MODE) == mode_pinned\n"
        "\n"
        "        # 3) DATA 空串/仅空白：入口 _reject_blank_data_root 先拒（F1/N2\n"
        "        #    统一入口），先于身份校验/模式/token/业务。\n"
        "        for bad in ('', '   '):\n"
        "            os.environ[DATA] = bad\n"
        "            with pytest.raises(RuntimeError) as ei:\n"
        "                importlib.import_module('build.tests.test_r6_undo_collab_fixes')\n"
        "            assert 'blank' in str(ei.value)\n"
        "            assert calls == []\n"
        "            with pytest.raises(RuntimeError) as ei:\n"
        "                _bare_env.acquire(mode='server')\n"
        "            assert 'blank' in str(ei.value)\n"
        "            assert os.environ.get(DATA) == bad  # 入口原串零副作用\n"
        "            assert os.environ.get(MODE) == mode_pinned\n"
        "        os.environ[DATA] = data_pinned\n"
        "\n"
        "        # 4) 漂移：DATA 挪到别的存在目录 → 身份校验拒（env 根 != 会话根）。\n"
        "        drift.mkdir()\n"
        "        os.environ[DATA] = str(drift)\n"
        "        with pytest.raises(RuntimeError) as ei:\n"
        "            importlib.import_module('build.tests.test_r6_undo_collab_fixes')\n"
        "        assert 'no longer pinned' in str(ei.value)\n"
        "        assert calls == []\n"
        "        assert os.environ.get(MODE) == mode_pinned\n"
        "        os.environ[DATA] = data_pinned\n"
        "\n"
        "        # 5) 缺根：替换目录整体删除 → isdir 拒（先于 mode/token/业务）。\n"
        "        shutil.rmtree(root)\n"
        "        with pytest.raises(RuntimeError) as ei:\n"
        "            importlib.import_module('build.tests.test_r6_undo_collab_fixes')\n"
        "        assert 'exists=False' in str(ei.value)\n"
        "        assert calls == []\n"
        "        assert os.environ.get(MODE) == mode_pinned\n"
        "        assert os.environ[DATA] == data_pinned\n"
        "\n"
        "        # 还原：原目录回到原路径 → 同根 acquire 恢复放行（对照闭合）。\n"
        "        os.rename(aside, root)\n"
        "        tok = _bare_env.acquire(mode='f1_restore_probe')\n"
        "        _bare_env.release(tok)\n"
        "        assert os.environ.get(MODE) == 'f1_restore_probe'\n"
        "        assert calls == []  # 全程零业务写（后端冻结根始终未被分叉使用）\n"
        "    finally:\n"
        "        db.init_db = real_init\n"
        "        if aside.exists() and not root.exists():\n"
        "            os.rename(aside, root)\n"
        "        elif aside.exists():\n"
        "            shutil.rmtree(aside, ignore_errors=True)\n"
        "        if drift.exists():\n"
        "            shutil.rmtree(drift, ignore_errors=True)\n"
        "\n",
        encoding="utf-8")
    import pytest
    try:
        rc = pytest.main([f"build/tests/{scratch.name}", "-q",
                          "-p", "no:cacheprovider"])
    finally:
        scratch.unlink(missing_ok=True)
    out["rc"] = int(rc)
    check(rc == 0, f"real pytest session must pass with in-session gates green, rc={rc}")
    check(_bare_env._pytest_session is None, "handshake ended with session (F1)")
    check(_bare_env.live_owner() is None, "no owner after clean session (F1)")
    check(DATA not in os.environ and MODE not in os.environ,
          "both entry keys restored (F1)")
    check(not py_leftovers(), "no pytest roots leaked (owned root removed at finish)")
    check(not (BOX / "data").exists(), "no default data/ (F1)")
    emit(ok=True)


def sc_r6_n2_live_blank_owner():
    # N2 live 残余（review_safety_r6_ds.md n2-live-blank 实证；与 F1 同一次
    # 窄修统一入口）：live RUNNING bare owner 事先显式以 cwd 作合法外部根后，
    # 将 DATA 改为空串/仅空白——acquire 统一入口的 _reject_blank_data_root
    # 必须先于 borrow/mode 拒绝（修前：空串解析为 cwd 恰与代根相等，运行期
    # 身份检查放行 → ALLOWED+施 mode）。借位语义/入口原串/释放幂等零变更；
    # 合法原根恢复后 acquire 仍放行（对照）。
    box_cwd = Path.cwd()
    check(str(box_cwd) == str(BOX), "driver cwd is the box/repo root (precondition)")
    os.environ[DATA] = str(box_cwd)
    os.environ[MODE] = "n2_entry_desktop"
    with _bare_env.bare_scope() as gen:
        check(gen.owned_root is None and str(gen.root) == str(box_cwd),
              "legal external root == cwd borrowed (precondition)")
        t1 = _bare_env.acquire(mode="n2_first_server")
        check(os.environ.get(MODE) == "n2_first_server",
              "first acquire on the legal root stays allowed (control)")
        check(gen.borrows == 1, "borrow registered (bare branch semantics kept)")
        _bare_env.release(t1)
        check(gen.borrows == 0, "release idempotent (unchanged)")
        for bad in ("", "   "):
            os.environ[DATA] = bad
            rejected = None
            try:
                _bare_env.acquire(mode="n2_blank_must_not_apply")
                raise AssertionError("blank DATA on live cwd-root owner must reject (N2 live)")
            except RuntimeError as exc:
                rejected = str(exc)
            check(rejected is not None and "blank" in rejected,
                  f"entry blank rejection message: {rejected}")
            check(os.environ.get(DATA) == bad,
                  "entry original blank string preserved (N2 live)")
            check(os.environ.get(MODE) == "n2_first_server",
                  "mode not applied by the refused acquire (N2 live)")
            check(gen.borrows == 0, "no borrow issued by the refusal (N2 live)")
            check(_bare_env.live_owner() is gen and gen.status == _bare_env.RUNNING,
                  "live owner untouched by the refusal (N2 live)")
        os.environ[DATA] = str(box_cwd)
        t2 = _bare_env.acquire(mode="n2_second_server")
        check(gen.borrows == 1 and os.environ.get(MODE) == "n2_second_server",
              "legal root re-acquire still allowed after refusals (control)")
        _bare_env.release(t2)
        check(gen.borrows == 0, "control borrow released")
    check(os.environ.get(DATA) == str(box_cwd), "external entry string restored (F4)")
    check(os.environ.get(MODE) == "n2_entry_desktop", "mode entry restored (F4)")
    check(_bare_env.live_owner() is None, "owner closed clean (N2 live)")
    check(not leftovers(), "no owned roots (borrowed root never deleted)")
    os.environ.pop(DATA, None)
    os.environ.pop(MODE, None)
    emit(ok=True)


def sc_r6_f2_collect_boundary():
    # F2 收集期边界（review_safety_pytest_supplement_ds.md F2；
    # collection_boundary_decision_20261114.md 用户授权的一次收集边界修复）：
    # 真实 build/tests conftest + 真实 pytest 子进程 --collect-only——
    # 自有 fixture 的 mutator 模块（import 期 TemporaryDirectory + DATA/MODE
    # 赋值，同形 2.1.5 agreement/session）按真实文件名序先于 acquirer 模块
    # （import 期 acquire，同形 auth）。修复合同：
    # 1) conftest 新增 pytest_make_collect_report wrapper 在真实
    #    collect/import 边界归还 COBOOKSHELF_* 会话基线——acquirer 导入期
    #    acquire 看到的是会话根（修前：前 mutator 的 fixture 根泄漏到此
    #    → F1 运行期身份校验拒，完整目录收集红）；
    # 2) mutator 模块**内部**自己漂移后的 acquire 仍被 F1 硬门先拒
    #    （收集边界修复不重开业务写窗口）；
    # 3) 会话收尾照常（无 rf_pytest_data_ 残留、BOX/data 不出现）。
    proc = _f2_collect_proc(
        ["test_zz_r6_f2a_mutate.py", "test_zz_r6_f2b_acquire.py"])
    check(proc.returncode == 0,
          f"boundary-fixed collection must pass, rc={proc.returncode}\n"
          f"stdout:\n{proc.stdout[-3000:]}\nstderr:\n{proc.stderr[-2000:]}")
    check("1 test collected" in proc.stdout,
          f"acquirer item must be collected: {proc.stdout[-1500:]}")
    check("ERROR collecting" not in proc.stdout,
          f"no collection errors expected: {proc.stdout[-1500:]}")
    check("test_zz_r6_f2b_acquire.py::TestF2Boundary::"
          "test_boundary_holds_session_root" in proc.stdout,
          f"acquirer test id present: {proc.stdout[-1500:]}")
    _f2_collect_cleanup()
    emit(ok=True)


def sc_r6_f2_collect_broken():
    # F2 收集异常路径：真实收集里 import 即抛的模块——原收集异常必须如实
    # 保留上报，且其后模块导入时 COBOOKSHELF_* 基线已被 wrapper 的 finally
    # 归还（修前：异常模块的 env 泄漏到其后模块 → 第二条「基线未恢复」
    # 收集错误；修后：仅剩原异常这一条错误）。REVIEWFLOW_MODE 的模块级
    # 赋值按冻结语义跨模块保留（conftest _TRACKED_PREFIXES=COBOOKSHELF_*，
    # r3 收集期赋值保证；其归属属会话代，收尾恢复）。
    proc = _f2_collect_proc(
        ["test_zz_r6_f2a_mutate.py", "test_zz_r6_f2c_broken.py",
         "test_zz_r6_f2d_after_broken.py"])
    check(proc.returncode == 2,
          f"broken-module collection must fail with rc=2, got "
          f"{proc.returncode}\nstdout:\n{proc.stdout[-3000:]}")
    check("boom-at-import (original collection exception)" in proc.stdout,
          f"original collection exception must be preserved verbatim: "
          f"{proc.stdout[-2000:]}")
    check("Interrupted: 1 error during collection" in proc.stdout,
          f"exactly the original error must remain (no second baseline "
          f"error): {proc.stdout[-2000:]}")
    check("ERROR collecting build/tests/test_zz_r6_f2d_after_broken.py"
          not in proc.stdout,
          "after-broken module must NOT fail: baseline restored by the "
          "collection boundary finally")
    check("ERROR collecting build/tests/test_zz_r6_f2a_mutate.py"
          not in proc.stdout,
          "mutator module must still collect cleanly (its in-module drift "
          "acquire is rejected and caught)")
    _f2_collect_cleanup()
    emit(ok=True)


_F2_SCRATCH_FILES = {
    "test_zz_r6_f2a_mutate.py": '''# -*- coding: utf-8 -*-
"""F2 driver scratch: import-time own-fixture mutator (same shape as the
real 2.1.5 agreement/session module heads) + the same-module drift
acquire-reject pin (F1 gate must still reject inside the mutating module)."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

fixture = tempfile.TemporaryDirectory(prefix='rf_f2_mutator_')
os.environ['COBOOKSHELF_DATA_DIR'] = fixture.name
os.environ['REVIEWFLOW_MODE'] = 'server'

import _bare_env  # noqa: E402

try:
    _bare_env.acquire(mode='f2_in_module_must_not_apply')
    raise AssertionError(
        'F2 same-module drift: acquire must be rejected by the F1 '
        'runtime identity gate even inside the mutating module')
except RuntimeError:
    pass
''',
    "test_zz_r6_f2b_acquire.py": '''# -*- coding: utf-8 -*-
"""F2 driver scratch: import-time acquirer (same shape as the real auth
module head). The import boundary must see the session root restored after
the preceding mutator module."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _bare_env  # noqa: E402

_BARE = _bare_env.acquire(mode='server')
_root_at_import = os.environ['COBOOKSHELF_DATA_DIR']
assert Path(_root_at_import).name.startswith('rf_pytest_data_'), (
    f'collection boundary did not restore the session baseline: '
    f'COBOOKSHELF_DATA_DIR={_root_at_import!r}')


def setUpModule():
    _bare_env.ensure_safe_for_execution()


def tearDownModule():
    _bare_env.release(_BARE)


class TestF2Boundary(unittest.TestCase):
    def test_boundary_holds_session_root(self):
        self.assertTrue(Path(
            os.environ['COBOOKSHELF_DATA_DIR']
        ).name.startswith('rf_pytest_data_'))
''',
    "test_zz_r6_f2c_broken.py": '''# -*- coding: utf-8 -*-
"""F2 driver scratch: mutates env then raises at import. The original
collection exception must be preserved and the COBOOKSHELF baseline
restored afterwards (checked by the after-broken module)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ['COBOOKSHELF_DATA_DIR'] = 'mutated-by-broken-module'
os.environ['REVIEWFLOW_MODE'] = 'server'
raise RuntimeError('boom-at-import (original collection exception)')
''',
    "test_zz_r6_f2d_after_broken.py": '''# -*- coding: utf-8 -*-
"""F2 driver scratch: collected AFTER the broken module; its import must
see the COBOOKSHELF_* session baseline restored by the wrapper finally
(REVIEWFLOW_MODE persistence is frozen r3 semantics)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_root = os.environ.get('COBOOKSHELF_DATA_DIR', '')
if (_root == ''
        or _root == 'mutated-by-broken-module'
        or Path(_root).name.startswith('rf_f2_mutator_')):
    raise AssertionError(
        f'COBOOKSHELF baseline not restored after broken-module '
        f'collection: COBOOKSHELF_DATA_DIR={_root!r}')
''',
}


def _f2_collect_proc(scratch_names):
    for name, text in _F2_SCRATCH_FILES.items():
        if name in scratch_names:
            (TESTS_DIR / name).write_text(text, encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if k not in ("COBOOKSHELF_DATA_DIR", "REVIEWFLOW_MODE")}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-p", "no:cacheprovider",
         *[f"build/tests/{name}" for name in scratch_names]],
        capture_output=True, text=True, env=env, timeout=300,
        cwd=str(BOX))


def _f2_collect_cleanup():
    for name in _F2_SCRATCH_FILES:
        (TESTS_DIR / name).unlink(missing_ok=True)
    for base in ("TMP", "TEMP", "TMPDIR"):
        tmp = os.environ.get(base)
        if not tmp:
            continue
        for p in Path(tmp).glob("rf_f2_mutator_*"):
            shutil.rmtree(p, ignore_errors=True)
    # 收集期零业务写：默认 data/ 不得出现**任何新条目**（目录本体允许因
    # 事故恢复等环境原因预先存在——F2 断言的是「收集不写」，不是目录
    # 不存在；既有 d_plain_pytest 的 not-exists 断言保持原样不动）。
    default_data = BOX / "data"
    check(not default_data.exists() or not any(default_data.iterdir()),
          f"default data/ must stay empty (F2 collection writes nothing): "
          f"{[p.name for p in default_data.iterdir()] if default_data.exists() else []}")
    check(not py_leftovers(), "no pytest roots leaked (F2 session finished)")


SCENARIOS = {
    "helper_scope": sc_helper_scope,
    "helper_external": sc_helper_external,
    "helper_failure_paths": sc_helper_failure_paths,
    "helper_atexit": sc_helper_atexit,
    "a_conftest_init": sc_a_conftest_init,
    "b_root_vanished": sc_b_root_vanished,
    "b_refusal_rc1": sc_b_refusal_rc1,
    "c_scope_framework": sc_c_scope_framework,
    "c_process_owner": sc_c_process_owner,
    "d_takeover": sc_d_takeover,
    "d_plain_pytest": sc_d_plain_pytest,
    "d_initfail_rollback": sc_d_initfail_rollback,
    "r5_a1_pre_adopt": sc_r5_a1_pre_adopt,
    "r5_b1_silent_pin": sc_r5_b1_silent_pin,
    "r5_b2_abort_vanished": sc_r5_b2_abort_vanished,
    "r5_c1_recreated": sc_r5_c1_recreated,
    "r5_c2_pending_import": sc_r5_c2_pending_import,
    "r5_c3_slashes": sc_r5_c3_slashes,
    "r5_d1_package_alias": sc_r5_d1_package_alias,
    "r5_d2_pending_refusal": sc_r5_d2_pending_refusal,
    # SafetyR6（review_safety_r5_ds.md N2/N3 定向回归；N1/N4/N5 在改写的
    # r5_c3_slashes/r5_c1_recreated 与 lease _assert_ok 内钉住）
    "r6_n2_blank_bare": sc_r6_n2_blank_bare,
    "r6_n2_blank_pytest": sc_r6_n2_blank_pytest,
    "r6_n3_live_swap_acquire": sc_r6_n3_live_swap_acquire,
    # SafetyR6 补充（review_safety_r6_ds.md F1 会话代 acquire 早返回漏身份
    # 检查 + N2 live blank 残余；safety_cap_decision_20261114.md 授权窄修）
    "r6_f1_pytest_live_acquire": sc_r6_f1_pytest_live_acquire,
    "r6_n2_live_blank_owner": sc_r6_n2_live_blank_owner,
    # SafetyR6 F2 收集期边界（review_safety_pytest_supplement_ds.md F2；
    # collection_boundary_decision_20261114.md 用户授权的一次收集边界修复：
    # 真实 conftest pytest_make_collect_report wrapper 在 Module collect/
    # import 边界归还 COBOOKSHELF_* 基线）
    "r6_f2_collect_boundary": sc_r6_f2_collect_boundary,
    "r6_f2_collect_broken": sc_r6_f2_collect_broken,
    "mixed_pytest": sc_mixed_pytest,
}


def main():
    out["scenario"] = sys.argv[1]
    SCENARIOS[out["scenario"]]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
