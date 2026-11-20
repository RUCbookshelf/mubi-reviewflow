# -*- coding: utf-8 -*-
"""build/tests 进程级数据目录钉 + 模块/用例级环境隔离夹具（2.1.6 TST-01 →
SafetyR4 A–D → SafetyR5 A1/C1/D1/D2 纠正轮）。

SafetyR5 增量（review_safety_r4.md）：A1 自有根创建后、登记责任前的异常
窗口（resolve 失败→根/键留存但 live_owner=None）——改为 mkdtemp 后立即
``adopt_session_root``，归一化/写键移入已登记代内；D2 pending 裸 owner 时
的「先 retry 再拒」会改写宿主 DATA——改为立即拒绝、零变更（重试责任归原
owner）；C1 冻结硬门补**目录身份**校验（同名重建空目录不再放行）。

背景（out/maintenance216/task_tests_impact.md §2 + review_safety_r3.md）：
- custom_backend/main.py 在**导入期**冻结 ``DATA_DIR``；auth/tasks 的
  ``data_dir()`` 运行期现读环境——两者必须同根，否则运行期写入回落默认
  ``data/`` 写穿真实数据（2026-11-14 事故）。
- SafetyR3 复审判定：conftest 的诊断告警在 warnings-as-errors 下会**遮住
  初始化原始异常并跳过入口恢复**（A）；自有根已消失时仍被当作删除失败
  重钉到不存在路径（B）；pytest 接管已有裸租约后 helper 永久休眠、真实
  收尾与 atexit 都不释放（D）。本轮全部按 safetyr4-brief 修正。

本文件职责（pytest 会话侧；裸 unittest 侧见 build/tests/_bare_env.py）：
1. **会话建立（pytest_configure，每会话一次）**，顺序契约：
   a. 清理能力最先建立（模块导入期即定义 ``_session_teardown`` 并注册
      atexit——先于任何环境改写/握手/建资源）；
   b. **安全拒绝先于一切变更**（D）：发现未了结的裸共享根所有者
      （running=已有裸套件在跑 / cleanup_pending=上一会话待删根）→
      pending 先重试一次，仍不了结则明确 RuntimeError 拒绝本会话（提示
      先结束裸套件或换进程）；拒绝时**未触碰**任何环境/租约/握手状态，
      原 lease/token/atexit 能力保持，宿主仍可正常结束裸套件。
   c. 入口快照（数据根键 + REVIEWFLOW_MODE 两键，先于握手变更）；
   d. 会话握手 begin（此后裸 acquire 只落模式、pytest 共享根不被模块
      删除；**有明确会话边界**，sessionfinish 与初始化失败都结束/回滚，
      不再永久休眠）；
   e. 钉根（外部预置则借用）+ 归一化 + 登记会话代 + **早导入后端** +
      冻结硬门（SafetyR2 P1 机制不变）。
2. **初始化失败异常安全（A）**：e 步任一异常（含硬门）→ ``abort_session``
   （两键无条件先恢复、自有根尽力删、失败留 pending 重试责任、握手回滚、
   诊断走非抛出 stderr/状态通道）→ **原样重抛原始异常**。诊断自身失败
   不得替换/吞掉原始异常、不得跳过恢复。
3. **会话收尾（pytest_sessionfinish + atexit 幂等）**：委托
   ``_bare_env.finish_session``——自有根删除成功或**已不存在（B：幂等
   完成，不重钉不存在路径）**→恢复两键入口原串/存在性；根仍在但拒删 →
   重钉到实际存在的自有根（键被中途 pop/改写同样重钉）+ cleanup_pending
   所有者 + 可观察失败，且在原本 OK 时把会话退出码升格为 TESTS_FAILED
   （pytest 9 wrap_session 于 sessionfinish 后才 return exitstatus，宿主
   可程序化感知）；重试成功恢复入口并清失败态。REVIEWFLOW_MODE 的归属：
   本轮起普通 pytest 会话结束也恢复该键入口值（运行期间模块的
   server/setdefault 策略不变——safetyr4-brief §2 授权）。
4. 模块/用例级 COBOOKSHELF_* 基线夹具（不变）。
5. **收集期导入边界（SafetyR6 F2，collection_boundary_decision_20261114.md
   用户授权的一次收集边界修复）**：模块级基线夹具只在**测试执行期**
   （setUpModule 前/tearDownModule 后）恢复基线，覆盖不到**收集期**的模块
   导入——pytest 按 scandir 文件名序收集时，前一个模块导入期的自有 fixture
   环境赋值（如 2.1.5 agreement/session 模块顶部 ``DATA=tempdir``）会泄漏到
   后一个模块的导入入口，令新 F1/N2 acquire 硬门（``ensure_safe_for_
   execution``）在真实完整目录收集时误拒（review_safety_pytest_supplement_
   ds.md F2 实证）。本文件新增 ``pytest_make_collect_report`` **wrapper**
   hook：仅对 ``pytest.Module`` collector（真实文件导入发生处），在真实
   collect/import 前、以及成功/收集异常后的 ``finally`` 中各归还一次
   ``_restore_tracked_env()``——模块边界（收集期）与夹具边界（执行期）
   语义统一为「前模块自有 fixture 变化不留给下一模块」；同一模块内部自己
   漂移后调用 acquire 仍被 F1 硬门先拒，业务写窗口不重开。时序已在本仓
   pytest 9.2.0 实证：wrapper 的 yield 前段先于模块导入执行、后段与
   finally 在导入完成后/导入抛出时执行，原收集异常原样保留上报；非
   Module collector（Session/Directory 等）原样透传。不用仅创建 collector
   的 ``pytest_pycollect_makemodule``（不覆盖实际 import）、不清
   sys.modules、不 reload、不屏蔽/吞掉任何收集异常。

边界：不放 DB 重置、不全局 reload、不清 sys.modules、不猴子补丁 unittest；
真实数据/8613 永不触碰；外部预置根只借不删。
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

DATA_DIR_ENV = "COBOOKSHELF_DATA_DIR"
MODE_ENV = "REVIEWFLOW_MODE"

#: 只跟踪数据目录域的键（REVIEWFLOW_MODE 的会话级归属见 _bare_env 会话代）。
_TRACKED_PREFIXES = ("COBOOKSHELF_",)

# -- SafetyR4 D：清理能力最先建立（先于任何握手/环境/资源变更） --------------
# 裸/会话共享根所有权与清理逻辑统一在 test-only helper（避免两套实现漂移，
# safetyr4-brief §4）；自身目录入 sys.path 保证任意导入路径同一模块实例。
_TESTS_DIR = str(Path(__file__).resolve().parent)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)
import _bare_env  # noqa: E402

#: 本会话入口快照（pytest_configure 每会话重拍；先于握手变更）。
_entry_data: str | None = None
_entry_mode: str | None = None

#: 本会话自建根（外部预置根时 None——只借不删）。
_owned_dir: str | None = None


def _session_teardown() -> None:
    """会话/进程收尾（sessionfinish 与 atexit 幂等复用）：委托 helper 的
    finish_session（B 幂等完成 / 拒删 pending / 两键恢复），绝不抛。"""
    try:
        _bare_env.finish_session()
    except BaseException as exc:  # 防御：收尾通道自身绝不上抛
        try:
            print(f"build/tests conftest: teardown defensive catch: {exc!r}",
                  file=sys.stderr)
        except BaseException:
            pass


atexit.register(_session_teardown)
# -- SafetyR4 D 清理能力就绪 --------------------------------------------------


def pytest_configure(config):  # noqa: ANN001
    """每会话一次的建立流程（拒绝→快照→握手→钉根→后端→硬门）。"""
    global _entry_data, _entry_mode, _owned_dir
    # b) 安全拒绝先于一切变更（D + R5 D2）：任何未了结裸所有者（running
    #    **或** cleanup_pending）→ 立即明确拒绝本会话。绝不预先 retry/收
    #    尾——pending 根的重试责任属于原 owner（其自身进程的 close_owner/
    #    retry_cleanup/atexit）；pytest 不得借拒绝之机悄悄改写宿主环境/
    #    租约/atexit 状态（R4 前版「先 retry 再拒」会把宿主 DATA 重钉到
    #    pending 根，D2 缺口）。
    live = _bare_env.live_owner()
    if live is not None:
        raise RuntimeError(
            "build/tests conftest: refusing to start this pytest session — "
            f"an unfinished bare shared-root owner is present (kind="
            f"{live.kind!r}, status={live.status!r}, root={live.root!s}). "
            "End the bare suite first (close its scope / finish its process "
            "run) or, for a cleanup_pending owner, let its original owner "
            "retry the deletion (_bare_env.retry_cleanup() in that owner's "
            "process), or run pytest in a separate process. (SafetyR4 D + "
            "R5 D2: refusal happens before any environment/lease/atexit "
            "change; no implicit retry is performed; existing lease/token/"
            "atexit abilities and both env keys are left untouched.)")
    # c) 入口快照（两键；先于握手变更）。
    _entry_data = os.environ.get(DATA_DIR_ENV)
    _entry_mode = os.environ.get(MODE_ENV)
    # d) 会话握手 begin（有边界的休眠；失败/收尾都会回滚，见 abort/finish）。
    _bare_env.pytest_session_begin()
    try:
        # e) 钉根（外部预置则借用）+ **责任先于变更**（R5 A1）：自有根一经
        #    mkdtemp 创建立即经 adopt_session_root 登记责任，归一化与键写
        #    入发生在已登记会话代**之内**——「根已建/键已改但 live_owner=
        #    None 无人清理」的窗口不复存在；外部根同样在改写键之前登记。
        #    其后任何异常窗口（resolve/写键/后端导入/硬门）都由
        #    abort_session 兜底：恢复两键原串/存在性 → 删自有根（拒删留
        #    可观察 pending）→ 回滚握手 → 原样重抛原异常。
        _owned_dir = None
        _root_candidate = os.environ.get(DATA_DIR_ENV)
        if _root_candidate is None:
            _owned_dir = tempfile.mkdtemp(prefix="rf_pytest_data_")
            _root_candidate = _owned_dir
        _bare_env.adopt_session_root(
            _root_candidate, owned_root=_owned_dir,
            entry_data=_entry_data, entry_mode=_entry_mode)
        # 会话级后端初始化（SafetyR2 P1）：conftest 先于任何测试模块加载导入
        # custom_backend.main，把导入期冻结值定到钉定根——此后无论哪个
        # 「导入期改写环境」的模块先被收集，其改写影响不到冻结值。
        _REPO_ROOT = str(Path(__file__).resolve().parents[2])
        if _REPO_ROOT not in sys.path:
            sys.path.insert(0, _REPO_ROOT)
        import custom_backend.main as _backend_main  # noqa: E402, F401

        # 冻结硬门（SafetyR2 P1 + R5 C1）：规范路径相等**且**同一目录身份
        # （同名重建的空目录不能伪装上一会话的数据根）；不一致立即拒绝起跑。
        _bare_env.ensure_backend_session_root(os.environ[DATA_DIR_ENV])
        _refresh_baseline()
    except BaseException:
        # A：初始化失败 → abort（两键无条件先恢复/自有根统一 B 语义删/拒删
        # 留 pending/握手回滚；全部非抛出）→ 原样重抛原始异常。
        _bare_env.abort_session()
        raise


#: COBOOKSHELF_* 基线（会话建立后拍）：模块/用例边界恢复的目标。
_BASELINE: dict[str, str] = {}


def _refresh_baseline() -> None:
    _BASELINE.clear()
    for key, value in os.environ.items():
        if key.startswith(_TRACKED_PREFIXES):
            _BASELINE[key] = value


def _restore_tracked_env() -> None:
    """把 COBOOKSHELF_* 键恢复到基线（多出的删除、变化的写回）。

    朴素两段循环（SafetyR3 期间由推导式改写，语义不变）：先收集多出的键
    再删除——不在 ``os.environ`` 迭代中边删边改。
    """
    extra = []
    for k in os.environ:
        if k.startswith(_TRACKED_PREFIXES) and k not in _BASELINE:
            extra.append(k)
    for key in extra:
        del os.environ[key]
    for key, value in _BASELINE.items():
        if os.environ.get(key) != value:
            os.environ[key] = value


@pytest.fixture(autouse=True, scope="module")
def _module_env_baseline():
    """模块边界兜底：进口（setUpModule 前）与出口（tearDownModule 后）各还原一次。"""
    _restore_tracked_env()
    yield
    _restore_tracked_env()


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(collector):  # noqa: ANN001
    """收集期模块导入边界（SafetyR6 F2）：每个 pytest.Module collector 的
    真实 collect/import 前后各归还一次 COBOOKSHELF_* 基线。

    只包 ``pytest.Module``（模块文件的实际 import 发生在本次 hook 的
    inner collect 里——pytest 9.2.0 实证时序：wrapper 的 yield 前段先于
    模块导入、后段/finally 在导入完成后或导入抛出时执行）；``try/finally``
    保证成功与收集异常两条路都恢复，异常原样向上传播（本 hook 绝不
    捕获/吞掉/替换收集异常，finally 只归还环境）。非 Module collector
    （Session/Directory/Package 等）不触碰、原样透传。基线本身仍是
    ``pytest_configure`` 建立的会话基线（``_restore_tracked_env``）——
    前模块收集期导入的自有 fixture 环境赋值在该模块导入结束后归还，
    不留给下一模块；同一模块内部自己漂移后的 acquire 仍由 helper 的
    运行期身份校验先拒（F1 合同不变）。
    """
    if not isinstance(collector, pytest.Module):
        return (yield)
    _restore_tracked_env()
    try:
        return (yield)
    finally:
        _restore_tracked_env()


@pytest.fixture(autouse=True)
def _case_env_snapshot():
    """用例边界兜底：快照拍于 setUp 之后段、还原于 tearDown 之后段。

    快照包含 setUpClass/tearDownClass 之外的类级钉（见模块 docstring 时序
    注记）——类级钉的还原不依赖本夹具。
    """
    snapshot = {}
    for key, value in os.environ.items():
        if key.startswith(_TRACKED_PREFIXES):
            snapshot[key] = value
    yield
    # 朴素两段循环（同 _restore_tracked_env：先收集再删，不在迭代中改 environ）。
    extra = []
    for k in os.environ:
        if k.startswith(_TRACKED_PREFIXES) and k not in snapshot:
            extra.append(k)
    for key in extra:
        del os.environ[key]
    for key, value in snapshot.items():
        if os.environ.get(key) != value:
            os.environ[key] = value


def pytest_sessionfinish(session):  # noqa: ANN001
    # 会话收尾（含收集失败）：finish_session 幂等——根删除成功或已不存在
    # （B 幂等完成）→两键恢复；根仍在但拒删→重钉+cleanup_pending+可观察。
    # 签名只取 session：pytest 8 起 hookspec 不再含 config（子集声明两版兼容）。
    _session_teardown()
    # F2（SafetyR3 起）：删除失败可观察的最后一级——pytest 9 的 wrap_session
    # 在 sessionfinish 之后才 return session.exitstatus，此处把「清理失败」
    # 升格为真实失败退出码（仅在原本 OK 时；不掩盖 INTERNAL_ERROR 等更严重
    # 状态；根已消失的幂等完成不升格——B）。
    live = _bare_env.live_owner()
    if (live is not None and live.status == _bare_env.PENDING
            and live.cleanup_failure is not None
            and session.exitstatus == pytest.ExitCode.OK):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
