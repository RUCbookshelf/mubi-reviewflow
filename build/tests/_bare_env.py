# -*- coding: utf-8 -*-
"""test-only 裸 unittest/pytest 共享数据根所有者 helper（SafetyR3 F3/F4 →
SafetyR4 A–D → SafetyR5 A1/B1/B2/C1/C2/C3/D1/D2 → SafetyR6 N1–N5 纠正轮，
2.1.6）。

SafetyR5 状态不变量（review_safety_r4.md 全清单的修后合同）：

- I1 责任先于变更（A1）：自有根一经创建/入口键一经改写，必已登记 RUNNING
  代为其责任方；创建→登记之间的异常窗口不存在（pytest 侧归一化/写键移入
  ``adopt_session_root`` 的已登记代内执行），abort 兜底=恢复两键原串→删自
  有根（拒删留可观察 pending）→回滚握手→原样重抛原异常。
- I2 两种完成合同（B1）：凡「根仍在且拒删」（含 rmtree 静默失败）一律
  pending+可观察；**仅当** ``entries_restored`` 为假（运行中代）才把键安全
  重钉到实际存在的自有根；入口已恢复的 abort 代重试失败绝不再碰宿主环境。
- I3 根不存在=幂等完成（B2）：close/abort/retry 三个删除入口统一语义——
  删除前后根确已消失即立即 CLOSED，不 pending、不等下一次 retry、不重钉。
- I4 目录身份而非路径等值（C1）：缓存后端冻结根记录为（规范路径, st_ino,
  st_dev）；同路径目录身份不同（同名重建空目录）在环境/业务写前明确拒绝；
  身份不可证明而声称冻结路径时同样拒绝。可证明同目录复用仍允许。
- I5 pending 不外借（C2）：live owner 为 cleanup_pending 时 acquire 在模块
  导入期即硬拒——导入期业务副作用（模板库构建等）不得在 pending 根上发生。
- I6 规范身份比较（C3）：运行期校验按 resolve 后规范路径判定；入口恢复始
  终写回原始字符串（外部根的前斜杠/任意形式原样保留）。
- I7 规范模块身份（D1）：``import _bare_env`` 与 ``from build.tests import
  _bare_env`` 共享同一模块实例/账本（同文件才别名；不清/不 reload
  sys.modules、不覆写无关同名模块）。
- I8 拒绝先于变更（D2）：pytest 遇任何未了结裸所有者（running/pending）
  立即拒绝，不预 retry、不碰环境/租约/atexit；pending 根收尾责任归原 owner。

SafetyR6 纠正轮增量（review_safety_r5_ds.md N1–N5；仅定向修复，不重写上述
生命周期）：

- I9 显式空白根拒绝（N2）：``COBOOKSHELF_DATA_DIR`` 为显式空串/仅空白时，
  bare 新代（bare_scope 与 acquire 惰性进程 owner 共用 ``_new_generation``）
  与 pytest ``adopt_session_root`` 在任何环境改写/建根/业务写**之前**明确
  RuntimeError 拒绝——空串解析为 cwd（``Path("") == Path(".")``），借用它
  等于把业务写分叉进当前工作目录；拒绝本身零副作用（入口原串/原始异常/
  清理责任保持）。缺省（键未设置）仍合法：建自有临时根；合法非空外部根
  只借不删不变。不把显式空值悄悄转成默认路径。
- I10 导入入口身份前置（N3）：live RUNNING owner 的 ``acquire`` 在增加
  borrows 或施加模式**之前**复用 ``ensure_safe_for_execution`` 的运行期身份
  校验（环境根==代根、根在磁盘、与缓存后端冻结根规范路径+目录身份一致）；
  根同名替换/环境漂移/根不在在导入期业务（如模板库构建）之前拒绝，
  token/borrows/env/mode 原样。普通同根复用不误拒；pending 入口拒绝（I5）
  不回退。
- I11 场景身份先于早退（N4，lease 侧）：``_assert_ok`` 的 helper_atexit 专
  项早退也先过场景身份断言，仅豁免其载荷形状不适用的 ok 字段（实现与回归
  在 test_2_1_6_bare_env_lease.py）。
- 身份语义准确边界（N5，非阻断）：``(st_ino, st_dev)`` 是**首次观测**的身
  份指示，不是跨替换的持久代序——若目录在后端冻结之后、首次基线观测之前
  已被替换，基线会记录替换后的目录（该窗口内替换不设防）；POSIX 文件系统
  可能在删除后立即复用 inode（本机 NTFS 实证不即时复用；POSIX 未本机实证，
  不声称彻底证明），因此同路径身份检查的回归构造必须用「旧目录改名保活 +
  原路径新建独立目录」这类**可证明两目录对象不同**的构造（两个同时存在的
  目录身份必然不同），不赌 inode 复用时机、不依赖平台 skip；目录因 ACL
  不可 stat 时按「身份不可证=拒绝」处理（该形态本机未复现，不推论为已证
  生产事故）。


SafetyR3 复审（review_safety_r3.md C/D）证伪了「导入计数==0 即删根」模型：
unittest 的**导入**与**模块执行块**不是一一对应（同一单线程套件可 w4→auth→
w4 交错重复执行，tearDownModule 每块各触发一次；筛空模块不触发；已清理
账本可被后续 acquire 复用）；pytest 接管已有裸租约后若把 helper 永久置为
休眠，真实模块收尾与 atexit 都不再释放。本轮按 safetyr4-brief 改为**明确的
外层所有者（generation/代）模型**：

- 共享根由一届所有者持有：``bare_scope()``（嵌入式宿主的显式套件 owner，
  在导入相关模块**前**进入，finally 关闭：删自有根+恢复两键入口原串/存在
  性）、**进程 owner**（原生 ``python -m unittest``/TestLoader 直用没有显式
  scope 时，首个 acquire 惰性建立，atexit/显式 close_owner() 结束）、
  **pytest 会话代**（conftest 经 pytest_session_begin/adopt_session_root/
  finish_session 委托，根的删除与恢复复用同一套清理逻辑，避免两份实现漂移）。
- 模块的 import 是**借用**（acquire 返回独立 token；release 幂等、只减观测
  计数、**绝不删根**）；borrow 计数不再驱动删除——这就是 R3 的病根。
- 生命周期合同（公开，SafetyR4 起）：普通嵌入式 TextTestRunner **未使用显式
  scope** 时，其返回不代表所有者结束，不承诺两键已恢复；原生 CLI 的正常
  结束点是进程退出（atexit）。CLI 退出证据不冒充宿主返回证据。
- scope/会话关闭后再导入或再执行已缓存模块：仅当环境根与缓存后端冻结根
  可证明仍一致时允许；否则在 acquire（导入入口）或 ensure_safe_for_execution
  （模块 setUpModule 入口）**硬拒**——先于任何测试业务/API 写。绝不返回已
  清理账本、不重建同名空目录、不清 sys.modules、不自动 reload。
- pytest 遇到任何未了结的裸所有者（running/cleanup_pending）→ conftest 在
  改环境/握手/建资源**之前**安全拒绝（safetyr4-brief §2：确定拒绝分支，
  不做租约迁移）；原 lease/token/atexit 能力与两键原值保持，宿主仍可正常
  release/结束。普通 pytest 握手有明确会话边界（begin/end），sessionfinish
  与初始化失败都会结束/回滚，不再永久休眠。
- 删除失败（root 仍在但拒删）：重钉到**实际存在**的自有根、进入
  cleanup_pending、保留重试责任、stderr+状态可观察（不用会炸控制流的
  warnings 通道）；重试成功恢复入口并清失败态。**根已不存在=幂等清理完成**
  （B）：结束清理、恢复入口、不重钉不存在路径、不无限重试（记 note 诊断）。
- 诊断异常安全（A）：一切报告走非抛出通道；初始化失败方（conftest）保证
  原异常原样上抛、入口恢复优先。

边界：不 import 任何后端/产品代码（可被无后端环境直接驱动）；不猴子补丁
unittest；不清 sys.modules。外部预置根永远只借不删。
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

DATA_DIR_ENV = "COBOOKSHELF_DATA_DIR"
MODE_ENV = "REVIEWFLOW_MODE"

_BACKEND_MODULE = "custom_backend.main"

RUNNING = "running"
PENDING = "cleanup_pending"
CLOSED = "closed"


def _stderr(msg: str) -> None:
    """非抛出诊断通道（A：warnings-as-errors/stderr 写失败不得破坏控制流）。"""
    try:
        print(msg, file=sys.stderr)
    except BaseException:  # pragma: no cover - stderr 不可写也不得上抛
        pass


class _Token:
    """一次 acquire 一枚的独立借用凭据。

    - ``release`` 幂等（重复释放安全）；旧代 token 在代关闭/换代后惰性化，
      绝不能扣新代账本、更不能复活已清理状态。
    - pytest 委托期（会话代活跃）发放的 token 恒惰性：release 零操作。
    """

    __slots__ = ("generation", "serial", "released")

    def __init__(self, generation, serial: int):
        self.generation = generation
        self.serial = serial
        self.released = False


class _Generation:
    """一届共享根所有者账本（bare scope / bare 进程 owner / pytest 会话）。

    ``borrows`` 只是未释放借用数的观测值，**不驱动任何删除决策**。
    ``entries_restored`` 区分「入口已恢复、仅剩目录待重删」（如初始化失败
    路径）与「仍钉着待重试根」——前者重试成功前不得再改宿主环境（A/B）。
    """

    __slots__ = ("id", "kind", "status", "root", "owned_root", "borrowed",
                 "entry_data", "entry_mode", "borrows", "cleanup_failure",
                 "note", "entries_restored")

    def __init__(self, gen_id: int, kind: str, root: Path,
                 owned_root: str | None, entry_data: str | None,
                 entry_mode: str | None):
        self.id = gen_id
        self.kind = kind              # "scope" | "process" | "pytest"
        self.status = RUNNING
        self.root = root              # 本代钉定的（已归一化）数据根
        self.owned_root = owned_root  # 本代自建根；外部借用时 None
        self.borrowed = owned_root is None
        self.entry_data = entry_data
        self.entry_mode = entry_mode
        self.borrows = 0
        self.cleanup_failure: tuple[str, str] | None = None
        self.note: str | None = None
        self.entries_restored = False


_generation: _Generation | None = None   # 当前/最近一届（含 pending 残留）
_pytest_session: _Generation | None = None  # 活跃 pytest 会话代（=休眠标志）
_next_id = 0
_atexit_armed = False


def _backend_frozen_root() -> str | None:
    main = sys.modules.get(_BACKEND_MODULE)
    if main is None:
        return None
    try:
        return str(Path(main.DATA_DIR).resolve())
    except BaseException:
        return "?unresolvable?"


#: 缓存后端冻结根的**目录身份基线**（C1 generation identity）：
#: (规范路径, st_ino, st_dev)——首次观测到该冻结根时记录。仅路径等值不
#: 能证明是同一目录（同名重建的空目录 inode 不同），不能伪装旧数据。
#: N5 准确边界：这是**首次观测**的身份指示而非持久代序——冻结后、首次
#: 观测前的替换会被当作基线；POSIX inode 即时复用未本机实证；ACL 不可
#: stat 时视为「无证」拒绝（详见模块 docstring SafetyR6 节）。
_backend_identity: tuple[str, int, int] | None = None


def _stat_identity(path: str) -> tuple[int, int] | None:
    """目录 generation identity（st_ino, st_dev）；不可得（消失/FS 无身份）
    返回 None=「无证」。"""
    try:
        st = os.stat(path)
        ident = (st.st_ino, st.st_dev)
        return ident if ident[0] != 0 else None
    except OSError:
        return None


def _observe_backend_identity() -> tuple[int, int] | None:
    """首次观测冻结根（或后端换根重冻结）时记录身份基线，返回当前基线。"""
    global _backend_identity
    frozen = _backend_frozen_root()
    if frozen is None:
        return None
    if _backend_identity is None or _backend_identity[0] != frozen:
        ident = _stat_identity(frozen)
        _backend_identity = (frozen, *ident) if ident is not None else (frozen, 0, 0)
    return (_backend_identity[1], _backend_identity[2])


def _check_backend_compatible(prospective: str | None) -> None:
    """缓存后端一致性硬拒（C + R5 C1 合同）：仅当 prospective 与冻结根可
    证明为**同一目录**——规范路径相等**且**目录身份基线与当前 stat 一致——
    才放行。同名重建的空目录（路径同、inode 异）在建立任何环境/业务写之
    前明确拒绝；身份不可证明（基线目录已消失/FS 无身份）而声称冻结路径
    同样拒绝（无证不放行）。后端未缓存时维持原行为。"""
    frozen = _backend_frozen_root()
    if frozen is None:
        return
    if prospective is not None:
        same_path = False
        try:
            same_path = str(Path(prospective).resolve()) == frozen
        except BaseException:
            same_path = False
        if same_path:
            ledger = _observe_backend_identity()
            current = _stat_identity(frozen)
            if ledger is not None and current is not None and ledger == current:
                return  # 同一路径且同一目录身份：可证明一致，允许复用
            raise RuntimeError(
                "build/tests _bare_env: refusing to reuse the cached backend "
                f"against {prospective!r} — the frozen root {frozen} resolves "
                "to the same path but is NOT provably the same directory "
                "(generation identity mismatch: a same-named empty directory "
                "cannot impersonate the old data root / databases; or the "
                "identity cannot be established). Re-run in a fresh process "
                "or against the original, still-existing directory. "
                "(SafetyR5 C1)")
    raise RuntimeError(
        "build/tests _bare_env: refusing to start a bare suite against "
        f"{prospective!r} — custom_backend.main is already imported in this "
        f"process with frozen DATA_DIR={frozen}, which cannot stay consistent "
        "with a different or fresh data root (the previous suite likely ended "
        "and removed its root). Re-run in a fresh process "
        "(python -m unittest …) or a fresh host; do NOT reuse the cached "
        "backend against another data root and do NOT rebuild a same-named "
        "directory to fake the old database.")


def ensure_backend_session_root(root: str) -> None:
    """pytest 会话冻结硬门（SafetyR2 P1 机制 + R5 C1 身份）：后端冻结根与
    会话根必须规范路径相等**且**同一目录身份；仅路径等值不再放行（防止宿
    主在上一会话已删根的原路径重建空目录后伪装旧根续跑）。"""
    frozen = _backend_frozen_root()
    if frozen is None:
        return
    try:
        resolved_root = str(Path(root).resolve())
    except BaseException as exc:
        raise RuntimeError(
            "build/tests conftest: session backend init mismatch — resolving "
            f"the session root {root!r} failed: {exc!r}. (If this is a second "
            "pytest session in one process: the cached backend keeps the "
            "first session's frozen root — use a fresh process.)") from None
    if resolved_root != frozen:
        raise RuntimeError(
            "build/tests conftest: session backend init mismatch — "
            f"custom_backend.main.DATA_DIR resolves to {frozen!s} != "
            f"{DATA_DIR_ENV}={resolved_root!s}. (If this is a second pytest "
            "session in one process: the cached backend keeps the first "
            "session's frozen root — use a fresh process.)")
    ledger = _observe_backend_identity()
    current = _stat_identity(root)
    if ledger is None or current is None or ledger != current:
        raise RuntimeError(
            "build/tests conftest: session backend root identity mismatch — "
            f"{root!r} resolves to the frozen root path {frozen!s} but is "
            "not provably the same directory (generation identity mismatch "
            "or unprovable; a same-named recreated directory cannot "
            "impersonate the previous session's data root). Use a fresh "
            "process. (SafetyR5 C1)")


def _reject_blank_data_root(value: str | None, origin: str) -> None:
    """I9/N2：显式空串/仅空白的数据根一律拒绝（不得借为 cwd）。

    ``Path("") == Path(".")``：空串解析为当前工作目录，把它当外部根借用会让
    业务写分叉进 cwd（R5 复审 N2 实证的裸路径安全门回退）。拒绝发生在任何
    环境改写/建根/业务写之前，且零副作用（入口原串保留、无目录创建）；
    不把显式空值悄悄转成默认路径（调用方设错了就该被明确告知）。缺省
    （``None``=键未设置）合法：由所有者建自有临时根。
    """
    if value is not None and not value.strip():
        raise RuntimeError(
            "build/tests _bare_env: refusing an explicitly blank "
            f"{DATA_DIR_ENV}={value!r} as the data root ({origin}) — an "
            "empty/whitespace string resolves to the current working "
            "directory and would fork business writes into it. Set a "
            "non-empty absolute path, or unset the variable to let the "
            "owner create its own temporary root. The explicit blank "
            "value is NOT silently converted to any default. "
            "(SafetyR6 N2/I9)")


def _new_generation(kind: str, prefix: str) -> _Generation:
    """建立一届所有者：入口无键则建根（后端已缓存时全新根必不一致→先硬拒，
    早于任何环境改写）；有外部键则只借用（同冻结根时允许复用；显式空串/
    仅空白先按 I9/N2 拒绝）。失败时零副作用（未改环境/未建目录）。"""
    global _generation, _next_id
    entry_data = os.environ.get(DATA_DIR_ENV)
    entry_mode = os.environ.get(MODE_ENV)
    _reject_blank_data_root(entry_data, "bare new generation "
                            "(bare_scope / acquire lazy process owner)")
    if entry_data is None:
        _check_backend_compatible(None)
        owned = tempfile.mkdtemp(prefix=prefix)
        os.environ[DATA_DIR_ENV] = owned
    else:
        _check_backend_compatible(entry_data)
        owned = None
    _next_id += 1
    gen = _Generation(_next_id, kind, Path(os.environ[DATA_DIR_ENV]),
                      owned, entry_data, entry_mode)
    _generation = gen
    return gen


def live_owner() -> _Generation | None:
    """当前未了结的共享根所有者（running 或 cleanup_pending）；无则 None。"""
    if _generation is not None and _generation.status in (RUNNING, PENDING):
        return _generation
    return None


def _arm_atexit_once() -> None:
    global _atexit_armed
    if _atexit_armed:
        return
    _atexit_armed = True
    atexit.register(_atexit_close)


@contextmanager
def bare_scope(prefix: str = "rf_bare_data_"):
    """显式外层套件所有者：在导入任何相关模块**前**进入；finally 关闭。

    覆盖真实 TextTestRunner/TestSuite 调用；筛空/已导入未入选模块也由本
    scope 收尾，不依赖其 tearDownModule 被调用。已有未了结所有者时进入
    即拒绝（先结束上一届或换进程）。
    """
    _begin_owner("scope", prefix)
    try:
        yield _generation
    finally:
        close_owner()


def _begin_owner(kind: str, prefix: str) -> _Generation:
    live = live_owner()
    if live is not None:
        if live.status == PENDING:
            raise RuntimeError(
                "build/tests _bare_env: refusing new bare suite — the previous "
                f"shared root owner is cleanup_pending (root={live.root!s}). "
                "Retry its deletion first (_bare_env.retry_cleanup()), then "
                "start a new session.")
        raise RuntimeError(
            "build/tests _bare_env: refusing a second concurrent shared-root "
            f"owner (kind={live.kind!r}, root={live.root!s}) — end the current "
            "one (close the bare_scope / let the process owner finish) or use "
            "a separate process.")
    gen = _new_generation(kind, prefix)
    _arm_atexit_once()
    return gen


def acquire(mode: str = "server", *, prefer_existing: bool = False,
            prefix: str = "rf_bare_data_"):
    """模块导入期借用（不建所有权语义）：返回独立 token。

    - **入口先拒显式空/空白根（R6 补充/N2 统一入口）**：任何分支、borrow、
      mode 之前按 I9 合同拒绝 ``COBOOKSHELF_DATA_DIR`` 的显式空串/仅空白
      值（空串解析为 cwd；live owner 的根恰为合法 cwd 外部根时，运行期
      身份检查对「DATA 事后被改空」不再设防——n2-live-blank 实证残余）。
      缺省（``None``=键未设置）合法。拒绝零副作用：入口原串保留、无建根、
      无 token、无 mode。
    - pytest 会话代活跃（conftest 委托）：先经 ``ensure_safe_for_execution``
      做运行期身份校验（环境根==会话根、根在磁盘、与缓存后端冻结根规范路
      径+目录身份一致），通过后才施加模式策略；token 恒惰性——模块绝不能
      删除 pytest 共享根，会话结束后 token 自然失效不残留状态。根同名替换
      /环境漂移/根不在/空白在施加模式与导入期业务之前拒绝（R6 补充/F1：
      修前该分支不做任何身份检查，swap 后仍放行+施 mode——n3-real-pytest
      实证）；正常同根复用不误拒（会话建立时 ensure_backend_session_root
      已按同根记录身份基线）。
    - 无会话代：确保有一届所有者（无显式 scope 时惰性建立**进程 owner**，
      由 atexit/close_owner() 收尾），登记一次借用。
    - **cleanup_pending 的 live owner 不外借（R5 C2）**：导入期即硬拒——
      模块导入路径上的任何业务副作用（如模板库构建 init_db）都不得在
      pending 根上发生；先由原 owner retry_cleanup() 了结再导入。
    - **live RUNNING owner 先验身份（R6 N3/I10）**：借用增加/模式施加之前
      复用 ``ensure_safe_for_execution`` 的运行期身份校验——环境根==代根、
      根在磁盘、与缓存后端冻结根规范路径+目录身份一致；根同名替换/环境漂
      移/根不在在导入期业务之前拒绝，token/borrows/env/mode 原样。普通同
      根复用不误拒。
    - 上一届已 CLOSED/PENDING：新建会触发缓存后端一致性硬拒（C 合同），
      绝不返回/复活已清理账本。
    """
    global _generation
    # R6 补充（review_safety_r6_ds.md F1/N2-live 残余；
    # safety_cap_decision_20261114.md 用户授权提案）：acquire 统一入口先按
    # I9 合同拒显式空/空白根——先于任何分支/borrow/mode，覆盖「live owner
    # 根恰为 cwd 时 DATA 事后被改空」与「会话分支此前无空白拒绝」两类残余。
    _reject_blank_data_root(os.environ.get(DATA_DIR_ENV), "acquire "
                            "(module import borrow entry / pytest session branch)")
    if _pytest_session is not None:
        # F1/N3：会话代活跃时同样先过运行期身份校验——拒绝发生在施加模式/
        # 发 token/导入期业务之前；正常同根复用不误拒（同 ensure 既有合同）。
        ensure_safe_for_execution()
        _apply_mode(mode, prefer_existing)
        return _Token(_pytest_session, -1)
    gen = live_owner()
    if gen is None:
        gen = _begin_owner("process", prefix)
    elif gen.status == PENDING:
        raise RuntimeError(
            "build/tests _bare_env: refusing to hand out a borrow token — "
            f"the shared-root owner is cleanup_pending (root={gen.root!s}). "
            "Module import (including any import-time business setup) must "
            "not run against a pending root; retry the deletion first "
            "(_bare_env.retry_cleanup()) from the owner, then re-import in "
            "that process or use a fresh one. (SafetyR5 C2)")
    else:
        # I10/N3：live RUNNING owner——导入入口先复用运行期身份检查（同
        # setUpModule 守卫），根替换/环境漂移/根不在时在任何导入期业务与
        # borrows/mode 变更之前拒绝。
        ensure_safe_for_execution()
    gen.borrows += 1
    _apply_mode(mode, prefer_existing)
    return _Token(gen, gen.borrows)


def release(token) -> None:
    """归还一次借用：幂等；绝不删除根（删根只属于所有者收尾）。"""
    if token is None or getattr(token, "released", True):
        return
    token.released = True
    gen = token.generation
    if gen is None or gen.kind == "pytest":
        return  # pytest 委托期惰性 token：恒零操作
    if gen is not _generation or gen.status != RUNNING:
        return  # 旧代/已关代：零副作用，不扣新代账本
    gen.borrows = max(0, gen.borrows - 1)


def ensure_safe_for_execution() -> None:
    """模块执行入口（setUpModule）硬拒守卫：先于任何测试业务/API 写。

    校验当前有活跃所有者（pytest 会话代或 running 裸代）、环境键仍钉在该
    代的根上、根在磁盘上存在、且与缓存后端冻结根一致（若已缓存）。任一
    不满足即抛 RuntimeError——宁可硬拒不带分叉根跑测试。
    """
    if _pytest_session is not None:
        gen = _pytest_session
    else:
        gen = live_owner()
        if gen is None or gen.status != RUNNING:
            raise RuntimeError(
                "build/tests _bare_env: no live shared-root owner — the bare "
                "suite that imported this module has already ended (root "
                "removed / entries restored). Re-run in a fresh process or a "
                "new bare_scope(); refusing to execute module tests against a "
                "stale environment.")
    root = str(gen.root)
    current = os.environ.get(DATA_DIR_ENV)
    # C3：运行期校验按**规范路径身份**判定（前斜杠/字面形式差异不影响）；
    # 入口恢复仍写回原始字符串（_restore_entries 的责任，不受此处影响）。
    try:
        same = (current is not None
                and str(Path(current).resolve()) == str(Path(root).resolve()))
    except BaseException:
        same = False
    if not same or not os.path.isdir(root):
        raise RuntimeError(
            f"build/tests _bare_env: environment no longer pinned to the live "
            f"shared root — COBOOKSHELF_DATA_DIR={current!r}, owner root="
            f"{root!r} (exists={os.path.isdir(root)}). Refusing to run module "
            "tests: a stale/deleted root would fork writes into default data/.")
    frozen = _backend_frozen_root()
    if frozen is not None:
        if frozen != str(Path(root).resolve()):
            raise RuntimeError(
                f"build/tests _bare_env: cached custom_backend.main.DATA_DIR="
                f"{frozen} does not match the live shared root {root!r}. "
                "Refusing to run module tests (frozen/runtime fork); restart "
                "in a fresh process.")
        # C1：同路径还须同目录身份（运行中被删重建=分叉，宁可硬拒）。
        ledger = _observe_backend_identity()
        current_ident = _stat_identity(root)
        if ledger is None or current_ident is None or ledger != current_ident:
            raise RuntimeError(
                f"build/tests _bare_env: live shared root {root!r} is not "
                "provably the same directory the cached backend froze "
                f"(generation identity mismatch/unprovable; frozen={frozen}). "
                "Refusing to run module tests (identity fork); restart in a "
                "fresh process. (SafetyR5 C1)")


def close_owner() -> str:
    """结束当前所有者（scope finally / 进程 owner 显式结束 / 幂等重入）。

    返回终态 ``CLOSED`` 或 ``PENDING``；绝不抛（诊断走 _stderr）。
    """
    gen = _generation
    if gen is None or gen.status == CLOSED:
        return CLOSED
    if gen.status == PENDING:
        return _retry_pending(gen)
    return _finish(gen)


def retry_cleanup() -> str | None:
    """显式重试（仅对 cleanup_pending 所有者；运行中套件不受影响）。"""
    gen = live_owner()
    if gen is None or gen.status != PENDING:
        return None
    return _retry_pending(gen)


# -- pytest 会话委托（conftest 调用；根的钉定/删除由 conftest 主导，收尾
#    逻辑复用本模块，避免两套清理实现漂移） --------------------------------


def pytest_session_begin() -> None:
    """会话握手开始：此后 acquire 只落模式、发惰性 token（休眠）。

    防御性拒绝：存在未了结裸所有者时不得接管（conftest 在更早处已拒绝；
    此处兜底同一合同）。
    """
    global _pytest_session
    if _pytest_session is not None:
        raise RuntimeError("build/tests _bare_env: pytest session already begun")
    live = live_owner()
    if live is not None:
        raise RuntimeError(
            "build/tests _bare_env: refusing pytest takeover — an unfinished "
            f"bare shared-root owner is present (kind={live.kind!r}, status="
            f"{live.status!r}, root={live.root!s}). End the bare suite first "
            "or run pytest in a separate process.")
    gen = _Generation(0, "pytest", Path(), None, None, None)
    gen.status = CLOSED  # adopt_session_root 前无根；此处仅占位休眠标志
    _pytest_session = gen


def adopt_session_root(root: str, *, owned_root: str | None,
                       entry_data: str | None, entry_mode: str | None) -> None:
    """登记 pytest 会话代并承担其资源责任（R5 A1：责任先于变更）。

    新顺序契约：先把会话代登记为 RUNNING（root=conftest 刚建的自有根**或**
    外部入口原串）——自此任何异常都落在已登记的责任方上；**然后**才在代内
    归一化（``Path.resolve``）并改写环境键。归一化/写键失败的兜底由
    conftest 的 ``abort_session`` 完成：恢复两键入口原串/存在性→删自有根
    （拒删则保留可观察 cleanup_pending）→回滚握手→原样重抛原异常。
    """
    global _pytest_session, _generation, _next_id
    if _pytest_session is None:
        raise RuntimeError("build/tests _bare_env: adopt_session_root without pytest_session_begin")
    # I9/N2：显式空串/仅空白根在登记代之前拒绝——此时 conftest 尚未改任何
    # 环境键/未建任何目录（外部根路径不 mkdtemp），abort_session 只需回滚
    # 握手（占位代 CLOSED 早退），入口原串自然保持。
    _reject_blank_data_root(root, "pytest adopt_session_root")
    _next_id += 1
    gen = _Generation(_next_id, "pytest", Path(root), owned_root,
                      entry_data, entry_mode)
    gen.status = RUNNING
    _pytest_session = gen
    _generation = gen
    # A1：以下（可能抛出的）归一化与（首次/改写的）键写入都发生在已登记
    # 责任代之内——不存在「根已建/键已改但无人负责」的窗口。
    normalized = str(Path(root).resolve())
    os.environ[DATA_DIR_ENV] = normalized
    gen.root = Path(normalized)


def abort_session() -> None:
    """pytest 初始化失败清理（A：绝不抛、绝不吞/换 conftest 的原始异常）。

    顺序契约：入口两键**无条件先恢复**；自有根按统一 B 语义删除（根确已
    不存在=幂等完成 B2，不 pending；仍在且拒删=保留可观察 pending 重试责
    任，entries_restored=True ⇒ 后续重试不得再改宿主环境 B1）；最后结束
    会话代（握手回滚）。诊断全部走非抛出通道。
    """
    global _pytest_session, _generation
    gen = _pytest_session
    _pytest_session = None
    if gen is None or gen.kind != "pytest" or gen.status not in (RUNNING, PENDING):
        return
    try:
        _restore_entries(gen)
        if gen.owned_root is not None and not _delete_owned_root(gen):
            _generation = gen   # 拒删：pending 保留重试责任（可观察）
            return
        gen.status = CLOSED
        if _generation is gen:
            _generation = None
    except BaseException as exc:  # 防御：绝不改写 conftest 原始异常的控制流
        _stderr(f"build/tests _bare_env: abort_session defensive catch: {exc!r}")


def finish_session() -> str:
    """pytest 会话收尾（sessionfinish/atexit 幂等复用）：返回 CLOSED/PENDING。

    - CLOSED：完全了结（根删/借用外部根未删 + 两键恢复），代清空，休眠解除。
    - PENDING：自有根拒删——该会话代转为显式 cleanup_pending 所有者留在
      live_owner 位（阻止后续会话把待删根误当外部借用），休眠同样解除；
      重试成功后彻底了结。
    """
    global _pytest_session, _generation
    gen = _pytest_session
    if gen is None:
        gen = None
        if (_generation is not None and _generation.kind == "pytest"
                and _generation.status == PENDING):
            gen = _generation
        if gen is None:
            return CLOSED
    status = _retry_pending(gen) if gen.status == PENDING else _finish(gen)
    if status == CLOSED:
        if _pytest_session is gen:
            _pytest_session = None
        if _generation is gen:
            _generation = None
    else:
        # 会话已结束，但待删根仍是显式 pending 所有者；休眠必须解除
        _pytest_session = None
        _generation = gen
    return status


# -- 内部：模式/恢复/收尾 ----------------------------------------------------


def _apply_mode(mode: str, prefer_existing: bool) -> None:
    if prefer_existing:
        os.environ.setdefault(MODE_ENV, mode)
    else:
        os.environ[MODE_ENV] = mode


def _restore_entries(gen: _Generation) -> None:
    """两键恢复到本代入口原状（原串或不存在）；幂等。"""
    if gen.entries_restored:
        return
    if gen.entry_data is None:
        os.environ.pop(DATA_DIR_ENV, None)
    elif os.environ.get(DATA_DIR_ENV) != gen.entry_data:
        os.environ[DATA_DIR_ENV] = gen.entry_data
    if gen.entry_mode is None:
        os.environ.pop(MODE_ENV, None)
    elif os.environ.get(MODE_ENV) != gen.entry_mode:
        os.environ[MODE_ENV] = gen.entry_mode
    gen.entries_restored = True


def _enter_pending(gen: _Generation, err: object) -> str:
    """根仍在但拒删：pending + 可观察 + 按完成合同区分是否安全重钉（B1）。

    - 运行中代（entries_restored=False）：键可能已被中途 pop/改写——重钉
      到仍存在的自有根，绝不留「键空→后续写入回落默认 data/」；入口恢复
      推迟到重试成功。
    - 入口已恢复的代（abort 路径，entries_restored=True）：**不再改宿主
      环境**，仅保留 pending 重试责任（两合同不得混淆）。
    """
    gen.status = PENDING
    gen.cleanup_failure = (str(gen.owned_root), repr(err) if isinstance(err, BaseException) else str(err))
    pinned = False
    if (not gen.entries_restored) and os.path.isdir(gen.owned_root):
        os.environ[DATA_DIR_ENV] = gen.owned_root
        pinned = True
    posture = (
        "environment re-pinned to the verified existing root; entry env NOT "
        "restored yet (running-owner contract)" if pinned else
        "entries already restored; host environment left untouched "
        "(abort/entries-restored contract)")
    _stderr(
        f"build/tests _bare_env: FAILED to remove owned data root "
        f"{gen.owned_root!r}: {gen.cleanup_failure[1]} — {posture}; pending "
        "retry state retained (close_owner/retry_cleanup/atexit re-entry).")
    return PENDING


def _delete_owned_root(gen: _Generation) -> bool:
    """统一删除自有根（close/abort/retry 三入口共享）的 B/B1/B2 语义。

    返回 True=根已不存在（删除前/中消失或删除成功）=幂等完成；False=根仍
    在且拒删（异常**或 rmtree 静默失败**）=已 ``_enter_pending``（按合同安
    全重钉+可观察，重试责任保留）。
    """
    root = gen.owned_root
    if not os.path.exists(root):
        gen.note = "owned root already absent (idempotent completion)"
        _stderr(f"build/tests _bare_env: note — {gen.note}: {root!r}; "
                "no re-pin, no pending.")
        return True
    try:
        shutil.rmtree(root)
    except OSError as exc:
        if not os.path.exists(root):
            # B2：rmtree 删根后自身抛错（如 FileNotFound）→ 根确已消失，
            # 立即幂等完成，不 pending、不等下一次 retry。
            gen.note = f"root vanished during rmtree: {exc!r}"
            _stderr(f"build/tests _bare_env: note — {gen.note}; treating "
                    "cleanup as complete (idempotent, no re-pin).")
            return True
        _enter_pending(gen, exc)
        return False
    if os.path.exists(root):  # B1：静默失败（正常返回但根仍在）同样拒删语义
        _enter_pending(gen, "rmtree returned but root still exists")
        return False
    return True


def _finish(gen: _Generation) -> str:
    """running 代收尾：统一 B 语义（根已不存在=幂等完成；拒删=pending）。"""
    if gen.owned_root is not None and not _delete_owned_root(gen):
        return PENDING
    _restore_entries(gen)
    gen.status = CLOSED
    gen.cleanup_failure = None
    return CLOSED


def _retry_pending(gen: _Generation) -> str:
    """pending 重试：成功恢复入口并清失败态；根已消失=幂等完成（B2）；
    仍存在拒删（含静默失败）=pending 保留并按合同重钉（B1）。"""
    if gen.owned_root is None or _delete_owned_root(gen):
        _restore_entries(gen)
        gen.status = CLOSED
        gen.cleanup_failure = None
        return CLOSED
    return PENDING


def _atexit_close() -> None:
    # 进程 owner / bare scope 的兜底结束点（补充而非唯一机制）；pytest 会话
    # 代的收尾由 conftest 自己的 atexit 驱动（finish_session），此处不重复。
    if _pytest_session is not None:
        return
    gen = live_owner()
    if gen is not None and gen.kind in ("process", "scope"):
        try:
            close_owner()
        except BaseException as exc:  # pragma: no cover
            _stderr(f"build/tests _bare_env: atexit close defensive: {exc!r}")


# -- D1：合法导入入口共享同一模块实例/账本 ------------------------------------
# ``import _bare_env``（conftest/六模块/驱动，顶层名）与 ``from build.tests
# import _bare_env``（包路径名）执行的是同一文件；R4 之前两入口形成两个模
# 块对象/两份 owner 账本，包路径建的 scope 对 pytest 不可见（错误接管）。
# 约定规范名为 ``_bare_env``：包路径导入在模块体末尾把自身条目指向规范实
# 例；若规范名尚不存在（包路径先入）则把自身注册为规范实例。仅当两名确为
# 同一文件才别名；绝不删除/清空 sys.modules、不全局 reload、不覆写无关同
# 名模块。
_CANONICAL_NAME = "_bare_env"


def _alias_to_canonical_module() -> None:
    self_mod = sys.modules.get(__name__)
    canonical = sys.modules.get(_CANONICAL_NAME)
    if __name__ == _CANONICAL_NAME or self_mod is None:
        return
    if canonical is not None:
        try:
            same_file = (str(Path(canonical.__file__).resolve())
                         == str(Path(__file__).resolve()))
        except BaseException:
            same_file = False
        if same_file and canonical is not self_mod:
            sys.modules[__name__] = canonical
            _stderr(f"build/tests _bare_env: package-path import {__name__!r} "
                    "aliased to the canonical module instance "
                    "(SafetyR5 D1 shared ledger).")
        elif not same_file:
            _stderr(f"build/tests _bare_env: NOTE — canonical "
                    f"{_CANONICAL_NAME!r} resolves to a different file; "
                    f"keeping {__name__!r} as a separate module.")
    else:
        sys.modules[_CANONICAL_NAME] = self_mod
        _stderr(f"build/tests _bare_env: registered package-path "
                f"{__name__!r} as the canonical {_CANONICAL_NAME!r} "
                "instance (SafetyR5 D1 shared ledger).")


_alias_to_canonical_module()
