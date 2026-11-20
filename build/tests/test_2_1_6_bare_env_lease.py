# -*- coding: utf-8 -*-
"""SafetyR4 A–D + SafetyR5/R6 · 共享数据根所有者生命周期契约钉（专属文件，
撤实现必红）。

被测对象：``_bare_env``（test-only 所有者 helper）+ ``conftest`` 会话建立/
收尾 + 六个接入模块（appraisal/ai_formats/auth_remember_reset/upload/
r6_undo/w4）的生命周期块。SafetyR3 复审（review_safety_r3.md A–D）证伪了
导入计数删根与永久休眠握手，本文件按 safetyr4-brief 用**真实框架路径**
（真实 pytest.main / 真实模块导入与模块夹具 / 真实 w4→auth→w4 交错块）
钉住新合同：

- A：warnings-as-errors 下初始化失败（后端导入异常 / 冻结硬门不匹配）叠加
  删除失败——原始异常类型保留、两键入口恢复、握手回滚、失败与重试状态
  可见且重试不再篡改已恢复的入口。
- B：自有根已消失=幂等清理完成（不重钉不存在路径、重复收尾可结束、
  pytest 不升格）；根仍在但拒删=重钉实际存在根+cleanup_pending+真实
  pytest rc 升格非零+连续失败+成功重试恢复。
- C：显式 ``bare_scope`` 内 w4→auth→w4 交错模块块全程同根零分叉；scope
  finally 恢复；关闭后再导入未缓存模块/再执行已缓存模块均**先于业务硬拒**；
  无显式 scope 的进程 owner 合同（返回≠所有者结束）；token 幂等/惰性。
- D：已有裸所有者（自有/借外部+desktop）时真实 pytest.main 在改环境前
  明确拒绝且不动原租约；无所有者的普通 pytest 成功、会话边界握手复原、
  会话后缓存根不匹配安全硬拒；初始化失败回滚可重入。
- SafetyR6（review_safety_r5_ds.md N1–N5 定向纠正，不重写上述生命周期）：
  r5_c3_slashes 的等价路径串构造跨平台化（Windows 前斜杠原反例 + POSIX 冗
  余 ``.`` 段，无 skip）；显式空/仅空白 DATA 在 bare 与真实 pytest 双入口
  先于建根/环境/业务写拒绝（r6_n2_*）；live RUNNING owner 的 acquire 身份
  前置——同名替换/环境漂移/根不在在导入期业务（r6 模板 init_db）之前拒
  （r6_n3_live_swap_acquire）；同路径不同身份用「旧目录改名保活+原路径新
  目录」的确定性构造（不赌 inode 复用，r5_c1_recreated）；_assert_ok 的
  helper_atexit 早退也先过场景身份断言（N4，仅豁免其不适用的 ok 字段）。
- SafetyR6 补充（review_safety_r6_ds.md F1 + N2 live 残余；safety_cap_
  decision_20261114.md 用户授权的一次窄修）：真实 pytest 会话代中 module
  import 触发的 acquire 先过运行期身份校验再施 mode/token（根同名替换/
  空串/空白/漂移/缺根全部先于模式、token 与 r6 导入期模板业务拒绝，
  r6_f1_pytest_live_acquire）；live cwd 根 owner 的 DATA 被改空在 acquire
  统一入口拒绝（r6_n2_live_blank_owner）。acquire docstring 与实际一致。
- SafetyR6 F2 收集期边界（review_safety_pytest_supplement_ds.md F2；
  collection_boundary_decision_20261114.md 用户授权的一次收集边界修复）：
  真实 build/tests conftest + 真实 pytest 子进程 --collect-only——自有
  fixture 的 mutator 模块按真实文件名序先于 acquirer 模块时，conftest 新增
  pytest_make_collect_report wrapper 在每个 pytest.Module 真实 collect/
  import 前后归还 COBOOKSHELF_* 会话基线（acquirer 导入期 acquire 看到
  会话根、收集全绿）；mutator 模块**内部**漂移后的 acquire 仍被 F1 运行期
  身份校验先拒；import 即抛的异常模块原收集异常如实保留为唯一收集错误，
  其后模块导入看到基线已恢复（修前：mutator env 泄漏 → acquirer 拒 +
  异常模块后第二条基线错误）。
- 混合回归：六模块 pytest 单批 + 裸 CLI 双模块。

全部场景经**独立子进程**驱动（``_safetyr4_driver.py``）：宿主 pytest 进程
自身处于会话代握手态，进程内直驱会把 pytest 共享根搅进来；子进程从干净
环境起跑，故障注入只在各自进程内局部生效。正式目录套件不拆进程、不吞
退出码（子进程 rc 原样断言）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent

#: 子进程专用短 TEMP 根：不用 pytest 深层 tmp_path——Windows MAX_PATH 下
#: upload 最长用例（任务名+research-trace 备份临时文件名）会超 260 字符而
#: 假红（SafetyR4 首跑实证）；短根使路径长度与正式 CI/本地跑法同量级。
SHORT_TEMP_ROOT = Path(tempfile.gettempdir()) / "r4-short"


def _scenario_tmp(scenario: str) -> Path:
    # driver 名归一（alias 场景共用驱动目录，避免双份短根）
    return SHORT_TEMP_ROOT / ("t-" + scenario.replace("a_conftest_init_gate", "a_gate"))


def _short_tmp(scenario: str) -> Path:
    d = _scenario_tmp(scenario)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d

_DRIVER = (
    "import sys; sys.path.insert(0, r'{tests_dir!s}'); "
    "import _safetyr4_driver as d; sys.exit(d.main())"
).format(tests_dir=TESTS_DIR)

#: 场景 → 预期 rc（None=断言别处）；extra_env 逐场景叠加。
SCENARIOS: dict[str, dict] = {
    # helper 合同（无后端）
    "helper_scope": {"rc": 0},
    "helper_external": {"rc": 0},
    "helper_failure_paths": {"rc": 0},
    # A：真实 pytest.main + 双重失败 + 警告升级
    "a_conftest_init": {"rc": 0, "args": ["import"], "env": {"PYTHONWARNINGS": "error::RuntimeWarning"}},
    "a_conftest_init_gate": {"rc": 0, "driver": "a_conftest_init", "args": ["gate"], "env": {"PYTHONWARNINGS": "error::RuntimeWarning"}},
    # B：真实 pytest.main
    "b_root_vanished": {"rc": 0},
    "b_refusal_rc1": {"rc": 0, "env": {"PYTHONWARNINGS": "error::RuntimeWarning"}},
    # C：真实 unittest 模块块
    "c_scope_framework": {"rc": 0},
    "c_process_owner": {"rc": 0},
    # D：真实 pytest.main 接管/边界
    "d_takeover": {"rc": 0, "args": ["owned"]},
    "d_takeover_external": {"rc": 0, "driver": "d_takeover", "args": ["external"]},
    "d_plain_pytest": {"rc": 0},
    "d_initfail_rollback": {"rc": 0},
    # SafetyR5（review_safety_r4.md A1/B1/B2/C1/C2/C3/D1/D2 逐项回归）
    "r5_a1_pre_adopt": {"rc": 0},
    "r5_b1_silent_pin": {"rc": 0},
    "r5_b2_abort_vanished": {"rc": 0},
    "r5_c1_recreated": {"rc": 0},
    "r5_c2_pending_import": {"rc": 0},
    "r5_c3_slashes": {"rc": 0},
    "r5_d1_package_alias": {"rc": 0},
    "r5_d2_pending_refusal": {"rc": 0},
    # SafetyR6（review_safety_r5_ds.md N2/N3；N1/N5 在改写的 r5_c3_slashes/
    # r5_c1_recreated 内钉住，N4 在 _assert_ok + 专项单测钉住）
    "r6_n2_blank_bare": {"rc": 0},
    "r6_n2_blank_pytest": {"rc": 0},
    "r6_n3_live_swap_acquire": {"rc": 0},
    # SafetyR6 补充（review_safety_r6_ds.md F1 + N2 live 残余；
    # safety_cap_decision_20261114.md 用户授权的一次窄修的永久回归）
    "r6_f1_pytest_live_acquire": {"rc": 0},
    "r6_n2_live_blank_owner": {"rc": 0},
    # SafetyR6 F2 收集期边界（review_safety_pytest_supplement_ds.md F2；
    # collection_boundary_decision_20261114.md 用户授权的一次收集边界修复
    # 的永久回归：真实 conftest pytest_make_collect_report wrapper 在
    # Module collect/import 边界归还 COBOOKSHELF_* 会话基线——mutator→
    # acquirer 真实文件名序收集全绿；异常模块原收集异常保留且其后模块
    # 基线已恢复；mutator 内部漂移 acquire 仍先拒）
    "r6_f2_collect_boundary": {"rc": 0},
    "r6_f2_collect_broken": {"rc": 0},
    # 混合回归
    "mixed_pytest": {"rc": 0, "timeout": 600},
}


def _run(scenario: str, spec: dict):
    env = {k: v for k, v in os.environ.items()
           if k not in ("COBOOKSHELF_DATA_DIR", "REVIEWFLOW_MODE")}
    env["TMP"] = env["TEMP"] = env["TMPDIR"] = str(_short_tmp(scenario))
    env["PYTHONUTF8"] = "1"
    env.update(spec.get("env", {}))
    driver_name = spec.get("driver", scenario)
    proc = subprocess.run(
        [sys.executable, "-c", _DRIVER, driver_name, *spec.get("args", [])],
        capture_output=True, text=True, env=env,
        timeout=spec.get("timeout", 300), cwd=str(TESTS_DIR.parents[1]),
    )
    payload = {}
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT "):
            payload = json.loads(line[len("RESULT "):])
    return proc, payload


def _assert_ok(scenario: str, spec: dict, proc, payload, err: str):
    assert proc.returncode == spec["rc"], (
        f"{scenario}: driver rc={proc.returncode} (want {spec['rc']})\n"
        f"stderr:\n{err[-3000:]}\nstdout:\n{proc.stdout[-2000:]}"
    )
    # Q1（R5）+ N4（R6）：真实断言场景身份——「payload 非空」不得吞掉场景
    # 不匹配；helper_atexit 的载荷形状早退也**先过身份断言**，仅豁免其载荷
    # 里不存在的 ok 字段。驱动别名场景按其实际 driver 名断言。
    driver_name = spec.get("driver", scenario)
    assert payload.get("scenario") == driver_name, (
        f"{scenario}: payload scenario identity mismatch: "
        f"{payload.get('scenario')!r} != {driver_name!r} (payload={payload})")
    if scenario == "helper_atexit":
        return payload
    assert payload.get("ok") is True, f"{scenario}: payload not ok: {payload}\nstderr:\n{err[-2000:]}"
    return payload


@pytest.mark.parametrize("scenario,spec", sorted(SCENARIOS.items()))
def test_safetyr4_scenario(scenario, spec):
    proc, payload = _run(scenario, spec)
    _assert_ok(scenario, spec, proc, payload, proc.stderr)
    leftovers = [p.name for p in _scenario_tmp(scenario).iterdir()
                 if p.name.startswith(("rf_bare_data_", "rf_pytest_data_"))]
    assert not leftovers, f"{scenario}: shared roots leaked: {leftovers}"


def test_helper_atexit_cleans_process_owner():
    spec = {"rc": 0}
    proc, payload = _run("helper_atexit", spec)
    _assert_ok("helper_atexit", spec, proc, payload, proc.stderr)
    root = payload.get("root")
    assert root, payload
    assert not Path(root).exists(), "atexit must close the process owner (root removed)"


def test_assert_ok_identity_gate_covers_helper_atexit_early_exit():
    """N4（R6）：helper_atexit 的载荷形状早退也必须先过场景身份断言。

    修前（R5 终态）：早退在身份断言之前——错 scenario 的非空载荷被静默放
    行（review_safety_r5_ds.md N4）。修后：身份断言先于早退；仅豁免该载荷
    形状不适用的 ok 字段；正向 atexit 根删除合同不变（上一测试保留）。
    """
    from types import SimpleNamespace
    proc = SimpleNamespace(returncode=0, stdout="", stderr="")
    spec = {"rc": 0}
    # 正确载荷形状（无 ok 字段）照常放行
    payload = _assert_ok("helper_atexit", spec, proc,
                         {"scenario": "helper_atexit", "root": "X"}, "")
    assert payload["root"] == "X"
    # 错 scenario 的非空载荷必须拒绝（修前被早退吞掉）
    with pytest.raises(AssertionError, match="scenario identity mismatch"):
        _assert_ok("helper_atexit", spec, proc,
                   {"scenario": "helper_scope", "ok": True, "root": "X"}, "")
    # 其他驱动名的载荷同样必须拒绝
    with pytest.raises(AssertionError, match="scenario identity mismatch"):
        _assert_ok("helper_atexit", spec, proc,
                   {"scenario": "b_root_vanished", "ok": True}, "")
    # 空载荷（驱动崩溃/无 RESULT 行）不得借早退蒙混
    with pytest.raises(AssertionError):
        _assert_ok("helper_atexit", spec, proc, {}, "")


def test_no_bare_roots_leak_across_scenarios():
    for scenario in ("helper_scope", "helper_failure_paths", "c_process_owner"):
        spec = SCENARIOS[scenario]
        proc, payload = _run(scenario, spec)
        _assert_ok(scenario, spec, proc, payload, proc.stderr)
    leftovers = [p.name for p in SHORT_TEMP_ROOT.rglob("rf_bare_data_*") if p.is_dir()]
    assert not leftovers, f"bare roots leaked: {leftovers}"


def test_bare_cli_two_modules_no_leftovers():
    """真实 CLI（进程 owner 正常结束点=进程退出）：双模块 + 零残留。"""
    tmp = _short_tmp("bare_cli")
    env = {k: v for k, v in os.environ.items()
           if k not in ("COBOOKSHELF_DATA_DIR", "REVIEWFLOW_MODE")}
    env["TMP"] = env["TEMP"] = env["TMPDIR"] = str(tmp)
    env["PYTHONUTF8"] = "1"
    proc = subprocess.run(
        [sys.executable, "-m", "unittest",
         "build.tests.test_2_1_5_auth_remember_reset",
         "build.tests.test_upload_security_regression"],
        capture_output=True, text=True, env=env, timeout=300,
        cwd=str(TESTS_DIR.parents[1]),
    )
    assert proc.returncode == 0, f"rc={proc.returncode}\n{proc.stderr[-2000:]}"
    assert "OK" in proc.stderr, proc.stderr[-800:]
    leftovers = [p.name for p in tmp.iterdir() if p.name.startswith("rf_bare_data_")]
    assert not leftovers, f"bare roots leaked: {leftovers}"
    assert not (TESTS_DIR.parents[1] / "data").exists(), "no default data/ in repo root"
