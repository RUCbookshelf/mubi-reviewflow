"""P06　可信来源身份 token 与历史路径定位（C05 前半，纯身份层）。

范围（2.1.6 P06，见 docs/superpowers/plans/2026-11-16-2.1.6-team-contracts.md
C05 与 2026-11-16-2.1.6-team-work-packages.md §P06）：

- ``make_source_identity``：服务端为 ``task_dir`` 内的成员库文件签发 opaque
  token（canonical JSON，version=1、base='task_dir'、POSIX 相对路径、
  source_user_id、source_library_id）。token 由服务端从 ``task_dir`` 基准推导，
  不接受客户端提交的路径文本。
- ``resolve_source_identity``：把可信登记（P03 ``team_terminal_registry``
  公开读 API 产出的 source 记录）或可信任务/导入来源元数据中的 token 定位回
  ``task_dir`` 内的物理文件，并最终核：实际 library ID（P04
  ``team_vote_ledger.library_identity_header_on`` 有界点查）、UID 与登记
  generation（对可信元数据交叉核对）、物理 claims（包含关系/regular 文件/
  实际身份一致）。

明确不在本包（P14 生命周期）：``prepare_team_protocol_storage`` /
``adopt_team_protocol_on``、登记/退休/watermark、任何 DB 写。本模块不登记、
不 prepare、不读全账本（唯一数据库触达＝对解析出的成员库开只读连接读
P04 身份头，meta 单行点查）；不做任何 collab/registry 连接操作。

硬性纪律（C05 条款 2/工作包核对项）：

- 老 slug 只能来自 token 自带的相对路径（原登记/可信元数据）——本模块绝不
  按当前 username 猜测或重写旧 slug：不 import getpass、不读
  os.getlogin()/USERNAME 类环境变量、不把 token 原文当 Path 直接使用。
- 不能解析的旧 token 保持 issue（``SourceIdentityIssue`` typed 异常，见
  REASONS），不吞、不静默跳过、不返回 None 冒充定位成功。
- 无账号/无 grant 的文件不被自动忽略或隔离：本模块不扫描目录、不移动/
  删除/改名任何文件，失败只以 typed 异常呈现。
- inode（st_dev/st_ino）只是当次物理见证，不充当永久逻辑 UUID：本模块
  无任何跨调用可变状态，同 inode 出现在不同文件/不同 task_dir 不构成全局
  禁止；身份按组合（source_user_id + source_library_id + 登记 generation/
  registration 声明 + 实际 library 身份）判定，绝不按 inode 单独判定。

token canonical 形态＝``json.dumps(value, ensure_ascii=False, sort_keys=True,
separators=(",", ":"), allow_nan=False)``；解析侧要求解析结果重新序列化后与
原文逐字节等值（拒绝非 canonical 文本、重复键、尾随空白与 NaN/Infinity）。

接受/拒绝矩阵（token/定位锚；两函数共用）：

============================  =====================================
输入情形                       结果
============================  =====================================
canonical v1 五键精确键集      接受，进入后续核验
非 JSON / 非 JSON 对象         ``token_unparsed`` issue
JSON 但非 canonical 形态      ``token_unparsed`` issue（legacy 未映射）
键集/version/base/值域不符    ``token_shape_invalid`` issue
relative 绝对路径/NUL/反斜杠  ``path_traversal`` issue
relative 含 ``.``/``..``/空分量 ``path_traversal`` issue
relative 分量含 ``:``（drive/ ``path_traversal`` issue
ADS 形态）
越界 symlink/junction（realpath ``path_escape`` issue
越出 task_dir）
目标缺失/目录/非 regular 文件  ``not_regular_file`` issue
登记 UID/library/generation 等 ``identity_mismatch`` issue
与 token 冲突或值域非法
成员库只读打开/身份头不可读    ``library_unverifiable`` issue
（unmigrated/meta_corrupt/IO）
实际 library_id ≠ token 声明   ``library_mismatch`` issue
（跨 UID 物理别名/错库）
参数类型误用（非 Path/非       ``TypeError``/``ValueError``（编程错误，
Mapping/非绝对路径/uid 非法    不伪装业务 issue）
等）
============================  =====================================

C02 借用纪律：本模块对自建的只读连接自行 BEGIN/CLOSE（自建非借用）；调用
P04 ``*_on`` 公开 API 前置好其连接合同（native 连接、活动事务、Row 工厂）；
不写任何库、不改 PRAGMA、不迁移、不重连。清理失败不吞主错（add_note 附加，
镜像 P03 ``_attach_cleanup`` 语义）。
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from coscreen import team_terminal_registry
from coscreen import team_vote_ledger

__all__ = [
    "SourceIdentityIssue",
    "make_source_identity",
    "resolve_source_identity",
]

#: token 版本（C05：version=1）。
_TOKEN_VERSION = 1
#: token 路径基准（C05：base='task_dir'）。
_TOKEN_BASE = "task_dir"
#: token 精确键集（多键少键均 ``token_shape_invalid``）。
_TOKEN_KEYS = frozenset(
    {"version", "base", "relative", "source_user_id", "source_library_id"}
)
#: library_id 合法字符（32 位小写 hex，镜像 P04 规则）。
_LIBRARY_HEX_CHARS = frozenset("0123456789abcdef")


class SourceIdentityIssue(RuntimeError):
    """来源身份定位的结构化 fail-closed issue（typed 异常通道）。

    风格对齐主仓 ``TeamFlowUnavailableError`` / P03
    ``TerminalRegistryUnavailable``：``reason`` 取值见 :data:`REASONS`
    （固定合同词表），``detail`` 为不含原始字节的诊断文本，
    ``source_user_id``/``relative`` 为定位上下文（None 键不进 ``params``）。
    HTTP 层映射由调用方（后续 P14/C 层）负责；本类不吞、不降级、不把
    issue 伪装成"未找到即忽略"。
    """

    REASONS = (
        "token_unparsed",        # 非 canonical v1 JSON token（含 legacy 未映射来源）
        "token_shape_invalid",   # canonical JSON 但键集/version/base/值域不符
        "path_traversal",        # 绝对路径/drive/NUL/.././空分量/反斜杠/冒号分量
        "path_escape",           # symlink/junction 的 realpath 越出 task_dir
        "not_regular_file",      # 目标缺失/目录/设备/非 regular 文件
        "identity_mismatch",     # token 与登记/元数据 UID、library、generation 冲突
        "library_unverifiable",  # 实际 library 身份头不可读（unmigrated/corrupt/IO）
        "library_mismatch",      # 实际 library_id ≠ token 声明（跨 UID 物理别名等）
    )

    def __init__(
        self,
        reason: str,
        *,
        detail: str = "",
        source_user_id: int | None = None,
        relative: str | None = None,
    ) -> None:
        if reason not in self.REASONS:
            raise ValueError(
                f"未知 reason: {reason!r}，必须为 {self.REASONS} 之一")
        text = f"[{reason}] {detail}" if detail else f"[{reason}]"
        super().__init__(text)
        self.reason = reason
        self.detail = detail
        self.source_user_id = source_user_id
        self.relative = relative

    @property
    def params(self) -> dict[str, object]:
        """结构化载荷（reason 必带；上下文字段仅在实际携带时出现）。"""
        payload: dict[str, object] = {"reason": self.reason}
        if self.source_user_id is not None:
            payload["source_user_id"] = self.source_user_id
        if self.relative is not None:
            payload["relative"] = self.relative
        return payload


def _issue(
    reason: str,
    detail: str,
    *,
    source_user_id: int | None = None,
    relative: str | None = None,
) -> SourceIdentityIssue:
    """``SourceIdentityIssue`` 工厂（镜像 P03 ``_payload`` 的用途）。"""
    return SourceIdentityIssue(
        reason, detail=detail, source_user_id=source_user_id, relative=relative
    )


def _canonical_json_text(value: Mapping[str, Any]) -> str:
    """canonical JSON 序列化（镜像 P03 ``_canonical_json`` 的参数）。"""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _valid_source_user_id(value: object) -> bool:
    """UID 值域：真 int（拒 bool）、1..MAX_INT64（P03 公开常量同界）。"""
    if type(value) is not int:
        return False
    return 1 <= value <= team_terminal_registry.MAX_INT64


def _valid_library_id(value: object) -> bool:
    """library_id 值域：str、恰 32 位、全小写 hex。"""
    if not isinstance(value, str) or len(value) != 32:
        return False
    for ch in value:
        if ch not in _LIBRARY_HEX_CHARS:
            return False
    return True


def _require_real_task_dir(task_dir: object) -> Path:
    """校验 task_dir（Path、绝对、已存在目录）并返回其 realpath 物理根。

    物理根是后续包含判定与相对路径推导的唯一基准；本函数只做参数与
    文件系统事实核验，不产生任何写。
    """
    if not isinstance(task_dir, Path):
        raise TypeError("task_dir 必须为 pathlib.Path")
    if not task_dir.is_absolute():
        raise ValueError("task_dir 必须为绝对路径")
    real_root = Path(os.path.realpath(task_dir))
    if not real_root.is_dir():
        raise ValueError("task_dir 必须为已存在的目录")
    return real_root


def _lexical_relative(relative: str) -> str:
    """token relative 的词法校验（POSIX 相对路径；逐分量拒绝表见模块矩阵）。

    校验顺序固定：非空 → 无 NUL → 无反斜杠 → 非绝对 → 逐分量非空且非
    ``.``/``..`` 且不含 ``:``（drive 盘符/NTFS ADS 形态一并拒绝）。
    """
    if not relative:
        raise _issue("path_traversal", "relative 为空")
    if "\x00" in relative:
        raise _issue("path_traversal", "relative 含 NUL")
    if "\\" in relative:
        raise _issue("path_traversal", "relative 含反斜杠（须为 POSIX 相对路径）")
    if relative.startswith("/"):
        raise _issue("path_traversal", "relative 为绝对路径")
    for component in relative.split("/"):
        if component == "":
            raise _issue("path_traversal", "relative 含空分量（// 或尾随斜杠）")
        if component == "." or component == "..":
            raise _issue("path_traversal", "relative 含 . 或 .. 分量")
        if ":" in component:
            raise _issue("path_traversal", "relative 分量含冒号（drive/ADS 形态）")
    return relative


def _token_payload(token: str) -> dict[str, Any]:
    """token 严格解析：JSON → canonical 等值 → 精确键集 → 逐字段值域。

    顺序固定：JSON 解析失败/非对象/非 canonical → ``token_unparsed``（不可
    映射的 legacy 来源在此保持 issue）；键集 → version → base → relative
    词法（``path_traversal``）→ source_user_id → source_library_id 值域
    （``token_shape_invalid``）。返回全部通过校验的五字段 payload。
    """
    try:
        parsed = json.loads(token)
    except ValueError as exc:
        raise _issue(
            "token_unparsed", "token 不是合法 JSON（legacy 未映射来源保持 issue）"
        ) from exc
    if not isinstance(parsed, dict):
        raise _issue("token_unparsed", "token 不是 JSON 对象")
    try:
        canonical = _canonical_json_text(parsed)
    except ValueError as exc:
        raise _issue(
            "token_unparsed", "token 含不可 canonical 序列化的数值（NaN/Infinity）"
        ) from exc
    if canonical != token:
        raise _issue(
            "token_unparsed",
            "token 非服务端 canonical 形态（重复键/空白/非紧凑序列化）",
        )
    if set(parsed) != _TOKEN_KEYS:
        raise _issue("token_shape_invalid", "token 键集与 v1 合同不符")
    version = parsed["version"]
    if type(version) is not int or version != _TOKEN_VERSION:
        raise _issue("token_shape_invalid", "token version 不受支持")
    base = parsed["base"]
    if not isinstance(base, str) or base != _TOKEN_BASE:
        raise _issue("token_shape_invalid", "token base 不受支持")
    relative = parsed["relative"]
    if not isinstance(relative, str):
        raise _issue("token_shape_invalid", "token relative 必须为文本")
    relative = _lexical_relative(relative)
    source_user_id = parsed["source_user_id"]
    if not _valid_source_user_id(source_user_id):
        raise _issue("token_shape_invalid", "token source_user_id 值域非法")
    source_library_id = parsed["source_library_id"]
    if not _valid_library_id(source_library_id):
        raise _issue("token_shape_invalid", "token source_library_id 值域非法")
    return {
        "version": version,
        "base": base,
        "relative": relative,
        "source_user_id": source_user_id,
        "source_library_id": source_library_id,
    }


def _check_registration_claims(
    source: Mapping[str, object], payload: Mapping[str, Any]
) -> None:
    """token 与可信登记/导入元数据的身份组合交叉核对（C05 条款 2）。

    ``source`` 是 P03 公开读 API 产出的 source 记录或可信导入来源元数据：
    ``source_path_identity`` 之外的下列键为可选身份声明，一旦出现即须与
    token 一致且值域合法——``source_user_id``、``source_library_id``、
    ``generation``（1..MAX_INT64）、``registration_id``（非空无 NUL 文本）、
    ``status``（active/retired）。不一致/值域非法 → ``identity_mismatch``。
    UID 只以该可信来源为准，绝不从当前 username/环境推导；generation 是
    逐登记声明，不跨代次继承。
    """
    source_user_id = payload["source_user_id"]
    claimed_uid = source.get("source_user_id")
    if claimed_uid is not None:
        if type(claimed_uid) is not int or claimed_uid != source_user_id:
            raise _issue(
                "identity_mismatch",
                "登记/元数据 source_user_id 与 token 不一致",
                source_user_id=source_user_id,
            )
    claimed_library = source.get("source_library_id")
    if claimed_library is not None:
        if not isinstance(claimed_library, str) \
                or claimed_library != payload["source_library_id"]:
            raise _issue(
                "identity_mismatch",
                "登记/元数据 source_library_id 与 token 不一致",
                source_user_id=source_user_id,
            )
    generation = source.get("generation")
    if generation is not None:
        if type(generation) is not int or not (
            1 <= generation <= team_terminal_registry.MAX_INT64
        ):
            raise _issue(
                "identity_mismatch",
                "登记 generation 值域非法",
                source_user_id=source_user_id,
            )
    registration_id = source.get("registration_id")
    if registration_id is not None:
        if (
            not isinstance(registration_id, str)
            or not registration_id
            or "\x00" in registration_id
        ):
            raise _issue(
                "identity_mismatch",
                "登记 registration_id 形状非法",
                source_user_id=source_user_id,
            )
    status = source.get("status")
    if status is not None:
        if status not in ("active", "retired"):
            raise _issue(
                "identity_mismatch",
                "登记 status 非法",
                source_user_id=source_user_id,
            )


def _contained_real_path(candidate: Path, real_root: Path) -> Path:
    """物理包含判定：realpath(candidate) 必须位于 real_root 之内。

    越界 symlink/junction（含中间分量与最终分量）经 realpath 解析后越出
    real_root → ``path_escape``。比较用 ``os.path.normcase`` 前缀法
    （Windows 大小写/分隔符归一；POSIX 恒等），并以 ``os.sep`` 防前缀伪造。
    界内 symlink 不在此拒绝（其物理目标仍在界内）。
    """
    real_candidate = Path(os.path.realpath(candidate))
    root_text = os.path.normcase(str(real_root))
    candidate_text = os.path.normcase(str(real_candidate))
    if candidate_text != root_text and not candidate_text.startswith(
        root_text + os.sep
    ):
        raise _issue("path_escape", "解析目标越出 task_dir（越界 symlink/junction）")
    return real_candidate


def _require_regular_file(real_target: Path) -> None:
    """物理 claims 之一：目标必须存在且为 regular 文件（跟随最终链接后）。"""
    try:
        mode = os.stat(real_target).st_mode
    except OSError as exc:
        raise _issue(
            "not_regular_file",
            f"目标不可访问（缺失或 stat 失败）: {getattr(exc, 'strerror', None) or exc}",
        ) from exc
    if not stat.S_ISREG(mode):
        raise _issue(
            "not_regular_file", "目标不是 regular 文件（目录/设备/套接字）")


def _actual_library_id(real_target: Path) -> str:
    """读取成员库实际 library 身份头（P04 公开 API，meta 单行有界点查）。

    自建只读连接（URI mode=ro）：设置 Row 工厂并显式 BEGIN 以满足
    ``library_identity_header_on`` 的 C02 借用连接合同；连接为本函数自有，
    确定性 CLOSE，清理失败不吞主错（``add_note`` 附加到原异常，镜像 P03
    ``_attach_cleanup``）。身份头失败（unmigrated/meta_corrupt/IO）→
    ``library_unverifiable``；其余异常原样上抛。返回实际 library_id。
    """
    uri = real_target.as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise _issue("library_unverifiable", f"成员库只读打开失败: {exc}") from exc
    primary: BaseException | None = None
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        header = team_vote_ledger.library_identity_header_on(conn)
    except team_vote_ledger.VoteLedgerUnavailable as exc:
        primary = exc
        raise _issue(
            "library_unverifiable", f"实际 library 身份头不可核: {exc}") from exc
    except sqlite3.Error as exc:
        primary = exc
        raise _issue(
            "library_unverifiable", f"实际 library 身份头读取失败: {exc}") from exc
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            conn.close()
        except sqlite3.Error as cleanup_exc:
            if primary is None:
                raise
            primary.add_note(
                f"source identity close failure: {type(cleanup_exc).__name__}: "
                f"{cleanup_exc}")
    return header.library_id


def make_source_identity(
    *,
    task_dir: Path,
    source_path: Path,
    source_user_id: int,
    source_library_id: str,
) -> str:
    """为 task_dir 内的成员库文件签发服务端 opaque token（canonical v1）。

    锚（C05 条款 1）：token 的相对路径以 task_dir 为基准由服务端推导；
    绝对路径入参须物理位于 task_dir 内——越界 symlink/junction、缺失、
    目录或非 regular 文件直接拒绝（``path_escape``/``not_regular_file``）。
    参数形状误用（非 Path/非绝对/UID 或 library_id 值域非法）→
    ``TypeError``/``ValueError``。本函数不打开数据库、不写任何状态；返回
    canonical JSON 文本（见模块 docstring 的精确格式）。
    """
    real_root = _require_real_task_dir(task_dir)
    if not isinstance(source_path, Path):
        raise TypeError("source_path 必须为 pathlib.Path")
    if not source_path.is_absolute():
        raise ValueError("source_path 必须为绝对路径")
    if not _valid_source_user_id(source_user_id):
        raise ValueError(
            "source_user_id 必须为 1..MAX_INT64 的整数（拒 bool）")
    if not _valid_library_id(source_library_id):
        raise ValueError("source_library_id 必须为 32 位小写 hex")
    real_target = _contained_real_path(source_path, real_root)
    _require_regular_file(real_target)
    relative = os.path.relpath(str(real_target), str(real_root))
    relative = relative.replace(os.sep, "/")
    relative = _lexical_relative(relative)
    return _canonical_json_text(
        {
            "version": _TOKEN_VERSION,
            "base": _TOKEN_BASE,
            "relative": relative,
            "source_user_id": source_user_id,
            "source_library_id": source_library_id,
        }
    )


def resolve_source_identity(
    *,
    task_dir: Path,
    source: Mapping[str, object],
) -> Path:
    """把可信登记/导入元数据中的 token 定位回 task_dir 内的物理文件。

    ``source`` 须为 Mapping（P03 source 记录或可信任务/导入来源元数据），
    其 ``source_path_identity`` 携带 token；UID/library/generation 等
    可选声明按 :func:`_check_registration_claims` 交叉核对。定位流程：
    token 严格解析（不可解析的旧 token 保持 ``token_unparsed`` issue，
    绝不按当前 username 猜旧 slug、不把 token 原文直接当 Path）→ 词法
    校验 → 物理包含（越界 symlink/junction 拒绝）→ regular 文件 →
    实际 library 身份头点查并与 token 声明比对（跨 UID 物理别名在此
    落网：``library_mismatch``）。全部通过时返回物理定位 Path（绝对、
    realpath 已核）；任何失败以 :class:`SourceIdentityIssue` typed 上抛，
    无账号/无 grant 的文件不会被自动忽略或隔离。本函数零 DB 写、零
    全账本读取。
    """
    real_root = _require_real_task_dir(task_dir)
    if not isinstance(source, Mapping):
        raise TypeError(
            "source 必须为 Mapping（P03 登记记录或可信导入来源元数据）")
    token = source.get("source_path_identity")
    if not isinstance(token, str) or not token:
        raise _issue(
            "token_unparsed",
            "source_path_identity 缺失或非文本（不可映射的 legacy 来源）",
        )
    payload = _token_payload(token)
    _check_registration_claims(source, payload)
    relative = payload["relative"]
    candidate = real_root.joinpath(*relative.split("/"))
    real_target = _contained_real_path(candidate, real_root)
    _require_regular_file(real_target)
    actual_library_id = _actual_library_id(real_target)
    if actual_library_id != payload["source_library_id"]:
        raise _issue(
            "library_mismatch",
            "实际 library 身份与 token/登记声明不符"
            "（跨 UID 物理别名或错库）",
            source_user_id=payload["source_user_id"],
            relative=relative,
        )
    return real_target


# ===========================================================================
# P14　来源准备、adoption、名册屏障（C05 后半 + C10 管理协调器）
# ===========================================================================
#
# 合同权威：docs/superpowers/plans/2026-11-16-2.1.6-team-contracts.md C05
# （登记生命周期/前向覆盖/退休归档）、C09（回执/归档查重）、C10（名册管理）
# 与 2026-11-16-2.1.6-team-work-packages.md §P14。
#
# 本节新增三个公开入口（P06 纯身份层原样保留，不改动上方任何行为）：
#
# - ``prepare_team_protocol_storage``：显式 schema 准备（F1/F2/receipts 的
#   prepare API），与业务请求分离；可重复调用（幂等）；不登记协议、不写
#   任何业务行（C05 条款 3/4/5——纯 GET 与普通失败保存不 prepare）。
# - ``adopt_team_protocol_on``：legacy 任务的显式 adoption。在受保护 collab
#   目标事务内：登记 pending 协议（``creation_path='legacy_adoption'``）→
#   登记可核来源 → 五字段 adoption 证据 → ``mark_legacy_verified_on`` →
#   同 commit 内部 receipt（evidence_ref 指向它）。任一必要来源不可核 →
#   如实保持 ``legacy_pending_verification``（typed 结果，不吞、不降级、
#   不单键自动 verified——C05 条款 6/7/8）。
# - ``run_team_management``：五入口共用的 collab 目标管理协调器（C10）。
#   ``action`` ∈ ``add``/``remove``/``toggle``/``business_role``，映射 P01
#   四个 keyword-only ``conn=None`` API（只调用、绝不修改）。固定序：
#   调用前现读 owner/目标合法性 → 原 receipt 幂等 → 保旧集 → 归档旧集
#   actor seals/receipts → 定向枚举旧 required 票键并集（初筛到复筛）→
#   旧集观察（protocol.roster_revision=previous_revision，管理事务中观察
#   用旧 revision）→ 四 borrowed writer（P01 API，conn=collab）→ 实际
#   roster/source 变化才 ``set_roster_revision_on`` 推进 revision → 新集
#   首次一致（未完成部分才用新 revision；已封存键不动——完成阶段保留当时
#   名单/意见/结论，2026-11-15 用户裁定锚）→ receipt → 退休
#   （``retire_source_on``，watermark 指向同 commit 枚举证据）→ 一次
#   collab commit（由 P10 窗口在正常退出时完成）。
#
# 管理 plane 的键域纪律：管理动作的业务键域可为空，但绝不把空集传给普通
# item guard（P12 ``_materialize_item_keys`` 拒绝空集）；开窗用**非空候选
# 块**＝窗口内以保留连接枚举的旧 required 票键并集（初筛 stage1 与复筛
# stage2 的全量 ``vote_snapshot`` 键并集）求值，开窗调用本身用哨兵键
# ``__team_management__`` 仅作窗口机械载体（绝不写入、绝不进业务域）。
# 管理路径不调用 ``require_team_write``（管理配置入口不受普通成员业务门
# 锁死，C05 条款 5）；属主资格由 P01 API 自身强制，本协调器只做前置现读。
#
# 退休枚举证明（C05 退休优化段）：完整 ``list_seals_on`` 本 instance 全部
#   actor seals（不凭 watermark 判完整），逐条 ``archive_seals_on`` 副本进
#   collab 并核每条 seal_id/payload_sha256/sealed_at 与源一致、数量相等，
#   加上 actor 原产 receipts ``archive_receipts_on`` 归档；完整枚举证明与
#   每条 ID/hash/原时间记入同 commit 管理 receipt 的 ``internal_evidence``；
#   ``archive_watermark`` 只指向该证据（sha256），读取者核证据而非 watermark。
#   切断的来源（成员库保留集不可得/枚举失败）无法确认 → typed 失败 →
#   **整次角色变更回滚**；合法无账号/历史身份保留（不自动物理清理任何
#   文件，幂等 ``remove`` 对已无 grant 者 revoked:false 不新增无条件动作）。
#
# 失败语义镜像 P13：窗口内任何失败原样上抛、同 collab 目标回滚；commit
#   不可判定报 ``outcome='unknown'`` 绝不重放；成功后清理故障仅作
#   ``cleanup_errors`` 诊断。
# ===========================================================================

from collections.abc import Sequence  # noqa: E402  (P14 section-local import)

from coscreen import task_collaboration  # noqa: E402
from coscreen import tasks as tasks_mod  # noqa: E402
from coscreen.team_flow_receipts import (  # noqa: E402
    TeamFlowReceiptConflict,
    append_receipt_on,
    archive_receipts_on,
    read_receipt_on,
)
from coscreen.team_flow_types import TeamOperation  # noqa: E402
from coscreen.team_terminal_facts import (  # noqa: E402
    OBSERVATION_PAYLOAD_SCHEMA,
    TerminalFactsUnavailable,
    append_seal_on,
    archive_seals_on,
    list_seals_on,
    read_seal_on,
    sha256 as seal_sha256,
)
from coscreen.team_terminal_registry import (  # noqa: E402
    active_source_on,
    list_sources_on,
    mark_legacy_verified_on,
    prepare_team_terminal_registry,
    read_protocol_on,
    register_protocol_on,
    register_source_on,
    retire_source_on,
    set_roster_revision_on,
)
from coscreen.team_vote_ledger import (  # noqa: E402
    library_identity_header_on,
    vote_snapshot,
    vote_snapshot_keys_on,
)

_P14_ALL = [
    "TeamManagementError",
    "prepare_team_protocol_storage",
    "adopt_team_protocol_on",
    "run_team_management",
]
__all__ = __all__ + _P14_ALL  # noqa: F821  (P06 已定义 __all__)

#: 管理动作词表（映射 P01 四 API；invite/revoke 等入口词由 P18 接线层归并）。
_MANAGEMENT_ACTIONS = frozenset({"add", "remove", "toggle", "business_role"})
#: 开窗用的候选块操作载体（非 P12 业务 operation；plan 只做绑定/选择，不授权）。
_MANAGEMENT_OPERATION_NAME = "team_management"
_MANAGEMENT_OPERATION_STAGE = "screen"
#: 空票键并集时的非空哨兵键（仅窗口机械载体，绝不写入/绝不进业务域）。
_MANAGEMENT_SENTINEL_KEY = "__team_management__"
#: 旧 required 票键并集的枚举阶段（初筛到复筛）。
_ENUM_STAGES = ("stage1", "stage2")
_MAX_REQUEST_ID = 128
_HEX64 = set("0123456789abcdef")


class TeamManagementError(RuntimeError):
    """管理协调器参数/编程级违约（typed；业务拒绝原样透传 P01/F1/F2/P07）。"""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"team management error: {code}: {detail}")


def _utc_now() -> str:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _validated_request_pair(request_id: str, request_sha256: str) -> None:
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= _MAX_REQUEST_ID:
        raise TeamManagementError(
            "invalid_request_id", "request_id must be 1..128 characters")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in request_id):
        raise TeamManagementError(
            "invalid_request_id", "request_id must not contain control characters")
    if (not isinstance(request_sha256, str) or len(request_sha256) != 64
            or not set(request_sha256) <= _HEX64):
        raise TeamManagementError(
            "invalid_request_sha256", "request_sha256 must be 64 lowercase hex")


def _owner_of(inputs) -> int:
    state = inputs.scope.role_state()
    owner = state.get("owner") or {}
    owner_id = owner.get("owner_user_id")
    if isinstance(owner_id, bool) or type(owner_id) is not int or owner_id <= 0:
        raise TeamManagementError("unconfirmed_task_state", "owner identity missing")
    if state.get("classification") != "owner_match":
        raise TeamManagementError(
            "unconfirmed_task_state", "role state not owner_match")
    return owner_id


def _participants(inputs) -> list[dict]:
    return _participants_of(inputs.scope.role_state())


def _participants_of(state: Mapping) -> list[dict]:
    return [entry for entry in state.get("participants") or []
            if isinstance(entry, Mapping)]


def _usernames(inputs) -> dict[int, str]:
    accounts_by_id, _casefold = inputs.scope.accounts()
    return {uid: name for uid, name in accounts_by_id.items()
            if isinstance(uid, int) and isinstance(name, str)}


def _roster_and_required(inputs) -> tuple[list[dict], list[int]]:
    """F1 口径 roster/required（镜像 P13 ``_roster_and_required`` 语义）。"""
    return _roster_from_state(
        inputs.scope.role_state(), _usernames(inputs))


def _roster_from_state(state: Mapping,
                       usernames: Mapping) -> tuple[list[dict], list[int]]:
    """从角色状态聚合 F1 口径 roster/required。

    状态可来自 scope 缓存（写前名单）或 P01 ``read_team_role_state_on``
    的 barrier 现读——管理事务里 writers 之后必须用现读（scope 缓存停留
    在写前名单，不能当作变更后事实）。
    """
    roster: list[dict] = []
    required: list[int] = []
    for entry in _participants_of(state):
        if entry.get("config") != "ok" or entry.get("role") not in ("coder", "reconciler"):
            continue
        role = entry["role"]
        required_screener = entry.get("required_screener") is True
        screening_enabled = entry.get("screening_enabled")
        if role == "reconciler":
            if not isinstance(screening_enabled, bool) \
                    or screening_enabled != required_screener:
                continue
        else:
            if not required_screener:
                continue
            screening_enabled = None if screening_enabled is None \
                else bool(screening_enabled)
        roster.append({
            "uid": entry["user_id"],
            "username": usernames.get(entry["user_id"], ""),
            "role": role,
            "is_owner": entry.get("is_owner") is True,
            "screening_enabled": screening_enabled,
            "required_screener": required_screener,
            "source": str(entry.get("source") or "grant"),
            "config": "ok",
        })
        if required_screener:
            required.append(entry["user_id"])
    return roster, sorted(set(required))


def _roster_fingerprint(roster: list[dict], required: list[int]) -> str:
    return seal_sha256({"roster": roster, "required_uids": sorted(set(required))})


def _member_connection(inputs, uid: int, username: str):
    """成员保留连接（只从 P10 保留映射取；不可得返回 None）。"""
    task_dir = getattr(inputs.ctx.info, "dir", None)
    if task_dir is None or not username:
        return None
    path = Path(task_dir) / f"{tasks_mod.slugify_screener(username)}.db"
    try:
        return inputs._reserved.connection(path)  # noqa: SLF001 - documented window channel
    except Exception:  # noqa: BLE001 - 保留集不含该成员＝不可确认（非协议失败）
        return None


def _member_path(info, username: str) -> Path | None:
    task_dir = getattr(info, "dir", None)
    if task_dir is None or not username:
        return None
    return Path(task_dir) / f"{tasks_mod.slugify_screener(username)}.db"


def _opinion_from_row(uid: int, library_id: str,
                      row: Mapping[str, object]) -> dict[str, object]:
    decision = row.get("decision")
    if not isinstance(decision, str):
        decision = ""
    exclusion_reason = row.get("exclusion_reason") or ""
    notes = row.get("notes") or ""
    source_row = {
        "tags": row.get("tags") or "",
        "screened_at": row.get("screened_at"),
        "updated_at": row.get("updated_at"),
    }
    return {
        "uid": uid,
        "decision": decision,
        "exclusion_reason": exclusion_reason,
        "notes": notes,
        "opinion_sha256": seal_sha256({
            "decision": decision.strip().lower(),
            "exclusion_reason": exclusion_reason,
            "notes": notes,
        }),
        "library_id": library_id,
        "revision": int(row.get("revision") or 0),
        "source_row": source_row,
        "source_row_sha256": seal_sha256(source_row),
    }


def _fresh_opinion(inputs, uid: int, username: str, stage: str, key: str):
    """单成员单键 fresh 票面（保留连接；缺席/不可核返回 None）。"""
    conn = _member_connection(inputs, uid, username)
    if conn is None:
        return None
    try:
        snapshot = vote_snapshot_keys_on(conn, stage, (key,))
    except Exception:  # noqa: BLE001 - ledger typed 失败＝不可确认
        return None
    if key in snapshot.issues:
        return None
    row = snapshot.rows.get(key)
    if row is None:
        return None
    return _opinion_from_row(uid, snapshot.library_id, row)


def _task_block(inputs, binding) -> dict[str, object]:
    owner_username = ""
    owner = inputs.scope.role_state().get("owner") or {}
    if isinstance(owner.get("owner_username"), str):
        owner_username = owner["owner_username"]
    return {
        "task_id": binding.task_id,
        "task_instance_id": binding.task_instance_id,
        "owner_user_id": binding.owner_user_id,
        "owner_username": owner_username,
        "owner_library_id": binding.owner_library_id,
    }


def _source_bindings(inputs, instance: str, uids):
    """F2 active 源绑定（registration/generation/library）；不完整返回 None。"""
    try:
        records = list_sources_on(
            inputs.collab_conn, task_instance_id=instance, status="active")
    except Exception:  # noqa: BLE001 - F2 不可读＝观察不可构造（fail-closed）
        return None
    by_uid = {}
    for record in records:
        by_uid[int(record["source_user_id"])] = {
            "registration_id": record["registration_id"],
            "generation": int(record["generation"]),
            "source_library_id": record["source_library_id"],
        }
    if not set(uids) <= set(by_uid):
        return None
    return {uid: by_uid[uid] for uid in uids}


def _append_set_observation(inputs, *, instance: str, stage: str, key: str,
                            roster: list, required_uids: list,
                            roster_revision, observer_uid: int,
                            request_id: str, request_sha256: str,
                            trigger: str, sealed: list) -> bool:
    """为一个 (stage,key) 追加当前一致观察 seal（roster_revision 由调用方给）。

    已有持久 seal（完成见证）→ 跳过（完成阶段保留当时名单/意见/结论——
    2026-11-15 裁定锚）；任一 required 成员 fresh 票面缺席/分歧 → 不是
    一致，不造完成档案（C03 fail-closed）；F2 绑定不完整 → 不伪造。
    """
    try:
        persisted = read_seal_on(
            inputs.target_conn, task_instance_id=instance,
            stage=stage, item_key=key)
    except TerminalFactsUnavailable:
        persisted = None
    if persisted is not None:
        return False
    usernames = _usernames(inputs)
    opinions = {}
    for uid in sorted(set(required_uids)):
        opinion = _fresh_opinion(
            inputs, uid, usernames.get(uid, ""), stage, key)
        if opinion is None:
            return False
        opinions[uid] = opinion
    decisions = {op["decision"] for op in opinions.values()}
    if len(decisions) != 1 or next(iter(decisions)) not in ("include", "exclude"):
        return False
    bindings = _source_bindings(
        inputs, instance, set(required_uids) | {observer_uid})
    if bindings is None:
        return False
    binding = inputs.plan.binding
    payload = {
        "schema": OBSERVATION_PAYLOAD_SCHEMA,
        "task": _task_block(inputs, binding),
        "stage": stage,
        "item_key": key,
        "round_id": None,
        "kind": "unanimous",
        "trigger": trigger,
        "final_decision": next(iter(decisions)),
        "roster": roster,
        "required_uids": sorted(set(required_uids)),
        "opinions": [opinions[uid] for uid in sorted(set(required_uids))],
        "arbitration": None,
        "protocol": {
            "source_registration_id": bindings[observer_uid]["registration_id"],
            "roster_revision": roster_revision,
        },
        "source_request": {
            "operation": "completion_observe",
            "request_id": request_id,
            "request_sha256": request_sha256,
        },
        "sealed_at": _utc_now(),
        "observation": {
            "host_operation": _MANAGEMENT_OPERATION_NAME,
            "host_stage": _MANAGEMENT_OPERATION_STAGE,
            "host_item_keys": [key],
            "observed_by_user_id": observer_uid,
            "basis": "current_snapshot",
        },
        "source_bindings": [
            {
                "source_user_id": uid,
                "registration_id": bindings[uid]["registration_id"],
                "generation": bindings[uid]["generation"],
                "source_library_id": bindings[uid]["source_library_id"],
            }
            for uid in sorted(bindings)
        ],
    }
    record = append_seal_on(inputs.target_conn, payload, origin="collab")
    sealed.append({
        "kind": "unanimous", "trigger": trigger, "stage": stage,
        "item_key": key, "seal_id": record["seal_id"],
    })
    return True


def _enumerate_actor_receipts(conn) -> list:
    """枚举成员库 actor 原产 receipts（静态列 SQL；只读借用连接）。

    P07 未提供 list API；此处以固定列集 ``payload_json/receipt_id/
    payload_sha256`` 的静态 SELECT 枚举（无动态列/无拼接标识符），解析后
    仅保留 actor 原产（source_registration_id/source_generation 成对在
    场）记录并重构成 archive 输入形状。任何 sqlite/JSON 失败 → typed
    失败（切断的来源无法确认 → 整次回滚，由调用方保证）。
    """
    rows: list = []
    cursor = conn.execute(
        "SELECT payload_json, receipt_id, payload_sha256 "
        "FROM team_flow_receipts")
    try:
        raw_rows = cursor.fetchall()
    finally:
        try:
            cursor.close()
        except sqlite3.Error:
            pass
    for payload_json, receipt_id, payload_sha256 in raw_rows:
        parsed = json.loads(payload_json)
        if not isinstance(parsed, dict):
            raise TeamManagementError(
                "source_receipts_unreadable", "receipt payload is not an object")
        if parsed.get("source_registration_id") is None \
                and parsed.get("source_generation") is None:
            continue  # collab 原产不属于 actor 归档面
        record = dict(parsed)
        record["receipt_id"] = receipt_id
        record["payload_sha256"] = payload_sha256
        rows.append(record)
    return rows


def _archive_retiring_source(inputs, *, instance: str, uid: int, username: str,
                             generation: int, registration_id: str) -> dict:
    """旧集完整归档：本 instance 全部 actor seals/receipts → collab 副本。

    完整枚举证明核实每条副本与代次（数量相等 + 每条 seal_id/
    payload_sha256 与源一致 + F2 registration/generation），不凭
    watermark；成员保留连接不可得＝切断的来源无法确认 → typed 失败
    （整次角色变更回滚）。
    """
    conn = _member_connection(inputs, uid, username)
    if conn is None:
        raise TeamManagementError(
            "retirement_source_unavailable",
            f"member library not reserved; cannot confirm cut source uid={uid}")
    seals = list_seals_on(conn, task_instance_id=instance)
    copies = archive_seals_on(inputs.target_conn, seals)
    if len(copies) != len(seals):
        raise TeamManagementError(
            "retirement_archive_incomplete",
            f"archived {len(copies)} of {len(seals)} seals")
    for source_seal, copy in zip(seals, copies):
        if source_seal["seal_id"] != copy["seal_id"] \
                or source_seal["payload_sha256"] != copy["payload_sha256"]:
            raise TeamManagementError(
                "retirement_copy_mismatch",
                f"seal copy differs: {source_seal['seal_id']}")
    receipts = _enumerate_actor_receipts(conn)
    archived_receipts = archive_receipts_on(inputs.target_conn, receipts)
    if len(archived_receipts) != len(receipts):
        raise TeamManagementError(
            "retirement_archive_incomplete", "receipt archive count differs")
    return {
        "enumeration": "list_seals_on(full instance)+archive_seals_on+"
                       "archive_receipts_on(actor origin)",
        "registration_id": registration_id,
        "generation": generation,
        "seal_count": len(seals),
        "seals": [_retiring_seal_evidence(seal, uid=uid) for seal in seals],
        "receipt_ids": [receipt["receipt_id"] for receipt in archived_receipts],
        "verified_per_copy": True,
    }


def _retiring_seal_evidence(seal, *, uid: int) -> dict:
    """归档证据条目：``_record_from_row``/``_build_record`` 记录形状合同。

    F1 记录的顶层键是 ``payload/seal_id/content_sha256/payload_sha256/
    origin/sealed_at/archived_at``；``stage``/``item_key`` 是 **payload 内**
    字段（coscreen/team_terminal_facts.py ``_record_from_row``/``_build_record``
    的返回形状）。HIGH#2 修复：此前按顶层 ``seal["stage"]`` 取值 →
    持完成 seal 的成员移除必然 KeyError('stage')（503 team_management_
    uncommitted 泄内部键名）。主行为＝从 payload 正确取值；真正意外的
    记录形状（读路径合同之外的坏数据）→ typed 拒绝（结构化 code，不裸泄
    内部键名/异常细节），由调用方整次回滚。
    """
    payload = seal.get("payload") if isinstance(seal, Mapping) else None
    if not isinstance(payload, Mapping):
        raise TeamManagementError(
            "retirement_source_unreadable",
            f"actor seal record shape is unreadable (uid={uid})")
    try:
        return {
            "seal_id": seal["seal_id"],
            "stage": payload["stage"],
            "item_key": payload["item_key"],
            "payload_sha256": seal["payload_sha256"],
            "sealed_at": seal["sealed_at"],
        }
    except KeyError as exc:
        raise TeamManagementError(
            "retirement_source_unreadable",
            f"actor seal record shape is unreadable (uid={uid})") from exc


def _register_member_source(inputs, *, instance: str, uid: int,
                            username: str) -> dict:
    """登记/复登 active 来源（P06 token 为身份；缺失成员库 → pending 如实）。"""
    path = _member_path(inputs.ctx.info, username)
    if path is None or not path.is_file():
        return {"status": "pending", "issue": "member_library_missing"}
    existing = active_source_on(
        inputs.collab_conn, task_instance_id=instance, source_user_id=uid)
    uri = path.as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise TeamManagementError(
            "source_library_unverifiable",
            f"member library open failed: {exc}") from exc
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        library_id = library_identity_header_on(conn).library_id
    except Exception as exc:  # noqa: BLE001 - 身份不可核＝pending（C05 条款 5）
        return {"status": "pending",
                "issue": f"library_identity_unverifiable:{type(exc).__name__}"}
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass
    if existing is not None:
        return {"status": "active",
                "registration_id": existing["registration_id"],
                "generation": int(existing["generation"]),
                "source_library_id": existing["source_library_id"]}
    token = make_source_identity(
        task_dir=Path(inputs.ctx.info.dir), source_path=path,
        source_user_id=uid, source_library_id=library_id)
    record = register_source_on(
        inputs.collab_conn, task_instance_id=instance, source_user_id=uid,
        source_library_id=library_id, source_path_identity=token)
    return {"status": "active",
            "registration_id": record["registration_id"],
            "generation": int(record["generation"]),
            "source_library_id": record["source_library_id"]}


def _ensure_protocol(inputs, *, instance_hint: str) -> tuple:
    """现读/必要时登记 pending 协议；返回 (protocol_record, created)。"""
    record = read_protocol_on(
        inputs.collab_conn, task_instance_id=instance_hint)
    if record is not None:
        return record, False
    binding = inputs.plan.binding
    record = register_protocol_on(
        inputs.collab_conn,
        task_id=binding.task_id,
        task_instance_id=binding.task_instance_id,
        owner_user_id=binding.owner_user_id,
        owner_library_id=binding.owner_library_id,
        creation_path="legacy_adoption")
    return record, True


def _payload_role(payload: Mapping) -> str:
    role = payload.get("role", payload.get("business_role"))
    if role is None:
        role = "coder"
    if not isinstance(role, str) or role not in ("coder", "reconciler"):
        raise TeamManagementError(
            "invalid_payload", "role must be 'coder' or 'reconciler'")
    return role


def _candidate_keys(inputs, old_required: list, usernames: Mapping) -> tuple:
    """定向枚举旧 required 票键并集（初筛到复筛；窗口内以保留连接求值）。"""
    union: set = set()
    for uid in sorted(set(old_required)):
        conn = _member_connection(inputs, uid, usernames.get(uid, ""))
        if conn is None:
            continue
        for stage in _ENUM_STAGES:
            try:
                _library, rows = vote_snapshot(conn, stage)
            except Exception:  # noqa: BLE001 - 贡献者缺席不阻塞观察构造
                continue
            union.update(str(key) for key in rows)
    return tuple(sorted(union))


# ---------------------------------------------------------------------------
# 公开入口 1：显式 schema 准备（与业务请求分离；幂等；零登记零业务写）
# ---------------------------------------------------------------------------

def prepare_team_protocol_storage(*, collab_path: Path,
                                  source_paths: Sequence) -> None:
    """C05 显式准备：collab 装 F2 registry + receipts + F1 facts，各成员库装 F1 + receipts。

    collab 侧 F1 facts 是管理协调器的观察/归档目标
    （``read_seal_on``/``append_seal_on``/``archive_seals_on`` 都以
    collab 为 target_conn；R1/D-1 修复：此前漏装、仅测试夹具手工补齐）。
    参数形状误用 → TypeError/ValueError；任何 prepare 失败原样上抛（不吞、
    不降级）；已准备 schema 幂等保留（不重复迁移、不夸大为跨库原子——
    C05 条款 4 的独立元数据阶段）。
    """
    if not isinstance(collab_path, Path):
        raise TypeError("collab_path 必须为 pathlib.Path")
    if not collab_path.is_absolute():
        raise ValueError("collab_path 必须为绝对路径")
    for path in source_paths:
        if not isinstance(path, Path):
            raise TypeError("source_paths 的每一项必须为 pathlib.Path")
        if not path.is_absolute():
            raise ValueError("source_paths 的每一项必须为绝对路径")
    from coscreen.team_flow_receipts import prepare_team_flow_receipts
    from coscreen.team_terminal_facts import prepare_team_terminal_facts
    prepare_team_terminal_registry(collab_path)
    prepare_team_flow_receipts(collab_path)
    prepare_team_terminal_facts(collab_path)
    for path in source_paths:
        prepare_team_terminal_facts(path)
        prepare_team_flow_receipts(path)


# ---------------------------------------------------------------------------
# 公开入口 2：显式 legacy adoption（受保护 collab 目标事务内）
# ---------------------------------------------------------------------------

def adopt_team_protocol_on(inputs, *, request_id: str,
                           request_sha256: str) -> Mapping:
    """legacy 任务的显式 adoption（C05 条款 4/6/7/8；typed 结果）。

    前提：``inputs`` 来自 P10 ``retained_team_write_inputs`` 且
    ``target_kind='collab'``（受保护 collab 目标事务）。固定序：原 receipt
    幂等（同 ``team_protocol:adopt`` scope）→ 现读/登记 pending 协议 →
    登记/复登全部 roster 来源（任一必要来源不可核 → 如实 pending，不
    verified、不单键自动升级）→ 五字段证据 + ``mark_legacy_verified_on``
    （仅显式 adoption 调用；prior_history_unknown 恒 True）→ 同 commit
    内部 receipt（evidence_ref=receipt_id，正向覆盖起点=adopted_at）。
    """
    _validated_request_pair(request_id, request_sha256)
    actor_user_id = inputs.ctx.user["user_id"]
    instance = inputs.plan.binding.task_instance_id
    existing = read_receipt_on(
        inputs.target_conn, task_instance_id=instance,
        actor_user_id=actor_user_id,
        operation="team_protocol:adopt", request_id=request_id)
    if existing is not None:
        if existing["request_sha256"] != request_sha256:
            raise TeamFlowReceiptConflict(
                "receipt already exists for this scope with different content")
        return {
            "coverage_state": "idempotent_replay",
            "idempotent": True,
            "request_id": request_id,
            "result": existing["result"],
            "receipt": existing,
        }
    protocol, created = _ensure_protocol(inputs, instance_hint=instance)
    usernames = _usernames(inputs)
    roster, _required = _roster_and_required(inputs)
    sources: list = []
    pending_issues: list = []
    for entry in roster:
        uid = int(entry["uid"])
        registration = _register_member_source(
            inputs, instance=instance, uid=uid,
            username=usernames.get(uid, entry.get("username", "")))
        if registration.get("status") == "active":
            sources.append({
                "source_user_id": uid,
                "registration_id": registration["registration_id"],
                "generation": registration["generation"],
                "source_library_id": registration["source_library_id"],
            })
        else:
            pending_issues.append(
                {"uid": uid, "issue": registration.get("issue")})
    coverage_state = protocol["coverage_state"]
    if coverage_state == "legacy_verified":
        return {
            "coverage_state": coverage_state,
            "idempotent": False,
            "request_id": request_id,
            "sources": sources,
            "receipt": None,
        }
    if pending_issues or not sources:
        # 必要来源不可核 → 保持 pending（不 verified、不吞、不单键自动升级）
        return {
            "coverage_state": "legacy_pending_verification",
            "idempotent": False,
            "request_id": request_id,
            "sources": sources,
            "pending_issues": pending_issues,
            "receipt": None,
        }
    sources.sort(key=lambda item: (item["source_user_id"], item["generation"]))
    evidence = {
        "sources": sources,
        "verification_scope": (
            "all roster participants; full active source enumeration of "
            f"task_instance {instance}"),
        "verification_method": (
            "explicit owner adoption in protected collab transaction; "
            "per-source registration/generation/library identity verified"),
        "conflict_handling": "typed fail-closed; pending on unverifiable source",
        "prior_history_unknown": True,
    }
    forward_start = _utc_now()
    receipt = append_receipt_on(inputs.target_conn, {
        "schema": "team_flow_receipt.v1",
        "task_instance_id": instance,
        "actor_user_id": actor_user_id,
        "operation": "team_protocol:adopt",
        "request_id": request_id,
        "request_sha256": request_sha256,
        "result": {
            "request_id": request_id,
            "operation": "team_protocol:adopt",
            "coverage_before": coverage_state,
            "sources": sources,
            "pending_issues": pending_issues,
        },
        "internal_evidence": {
            "target_kind": "collab",
            "adoption": True,
            "created_protocol": created,
            "forward_coverage_start": forward_start,
        },
        "recorded_at": _utc_now(),
        "source_registration_id": None,
        "source_generation": None,
    })
    verified = mark_legacy_verified_on(
        inputs.collab_conn,
        task_instance_id=instance,
        expected_roster_revision=int(protocol["roster_revision"]),
        adoption_evidence_ref=receipt["receipt_id"],
        adoption_evidence=evidence)
    return {
        "coverage_state": verified["coverage_state"],
        "idempotent": False,
        "request_id": request_id,
        "sources": sources,
        "forward_coverage_start": verified.get("adopted_at"),
        "receipt": receipt,
    }


# ---------------------------------------------------------------------------
# 公开入口 3：名册管理协调器（C10；五入口共用一个 collab 目标）
# ---------------------------------------------------------------------------

def run_team_management(
    runtime: object,
    info: object,
    user: Mapping,
    *,
    action: str,
    target_user_id: int,
    payload: Mapping | None,
    request_id: str,
    request_sha256: str,
) -> Mapping:
    """团队名册管理协调器（typed 结果；通知由调用方锁外处理）。

    返回映射：``outcome``（'committed'|'unknown'）、``idempotent``、
    ``request_id``、``result``、``receipt``、``seals``、``cleanup_errors``、
    ``roster_revision``（变更后 revision；未变更为 previous）。属主/目标
    合法性由 P01 API 强制；业务拒绝原样透传（同 collab 目标回滚由 P10
    窗口完成）。
    """
    _validated_request_pair(request_id, request_sha256)
    if action not in _MANAGEMENT_ACTIONS:
        raise TeamManagementError(
            "invalid_action",
            f"action must be one of {sorted(_MANAGEMENT_ACTIONS)}")
    if isinstance(target_user_id, bool) or type(target_user_id) is not int \
            or target_user_id <= 0:
        raise TeamManagementError(
            "invalid_target", "target_user_id must be a positive int")
    if payload is not None and not isinstance(payload, Mapping):
        raise TeamManagementError("invalid_payload", "payload must be a Mapping")
    payload = dict(payload or {})
    user_id = user.get("user_id") if isinstance(user, Mapping) else None
    if isinstance(user_id, bool) or type(user_id) is not int or user_id <= 0:
        raise TeamManagementError("invalid_user", "authenticated user_id missing")
    op_key = f"team_management:{action}"
    # 延迟导入：P09 ``team_screening_write`` 反向导入本模块（P06 符号），
    # 模块级导入会成环；函数内解析也保留测试可替换的产品接缝。
    from custom_backend.team_screening_write import retained_team_write_inputs

    window_inputs = None
    body_completed = False
    result_payload: dict | None = None
    try:
        with retained_team_write_inputs(
                runtime, info, dict(user), target_kind="collab",
                operation=TeamOperation(
                    name=_MANAGEMENT_OPERATION_NAME,
                    stage=_MANAGEMENT_OPERATION_STAGE),
                item_keys=(_MANAGEMENT_SENTINEL_KEY,)) as inputs:
            window_inputs = inputs
            instance = inputs.plan.binding.task_instance_id
            task_id = inputs.plan.binding.task_id

            # 1. 原 receipt 幂等（纯读）
            existing = read_receipt_on(
                inputs.target_conn, task_instance_id=instance,
                actor_user_id=user_id, operation=op_key, request_id=request_id)
            if existing is not None:
                if existing["request_sha256"] != request_sha256:
                    raise TeamFlowReceiptConflict(
                        "receipt already exists for this scope "
                        "with different content")
                result_payload = {
                    "outcome": "committed", "idempotent": True,
                    "request_id": request_id,
                    "result": existing["result"], "receipt": existing,
                    "seals": (), "roster_revision": None,
                }
                body_completed = True
            else:
                # 2. 调用前现读 owner/目标成员合法性（P01 自身随后再强制）
                owner_id = _owner_of(inputs)
                if user_id != owner_id:
                    raise TeamManagementError(
                        "not_owner", "only the task owner may manage the roster")
                protocol, _created = _ensure_protocol(
                    inputs, instance_hint=instance)
                previous_revision = int(protocol["roster_revision"])
                usernames = _usernames(inputs)
                old_roster, old_required = _roster_and_required(inputs)
                candidate_keys = _candidate_keys(inputs, old_required, usernames)

                sealed: list = []
                archive_evidence = None
                retire_registration = None

                # 3. remove：先归档旧集再动角色（切断来源未知 → 整次回滚）
                if action == "remove":
                    active = active_source_on(
                        inputs.collab_conn, task_instance_id=instance,
                        source_user_id=target_user_id)
                    if active is not None:
                        retire_registration = active
                        archive_evidence = _archive_retiring_source(
                            inputs, instance=instance, uid=target_user_id,
                            username=usernames.get(target_user_id, ""),
                            generation=int(active["generation"]),
                            registration_id=active["registration_id"])

                # 4. 旧集观察：管理事务中用旧 revision（断言锚）
                for key in candidate_keys:
                    for stage in _ENUM_STAGES:
                        _append_set_observation(
                            inputs, instance=instance, stage=stage, key=key,
                            roster=old_roster, required_uids=old_required,
                            roster_revision=previous_revision,
                            observer_uid=owner_id, request_id=request_id,
                            request_sha256=request_sha256,
                            trigger="current_observation", sealed=sealed)

                # 5. 四 borrowed writer（P01 四 API；conn=collab 屏障连接）
                store = str(inputs.ctx.collaboration_db)
                if action == "add":
                    writer_result = task_collaboration.grant_role(
                        store, actor_user_id=owner_id, user_id=target_user_id,
                        role=_payload_role(payload), task_id=task_id,
                        conn=inputs.collab_conn)
                elif action == "toggle":
                    enabled = payload.get("enabled")
                    if not isinstance(enabled, bool):
                        raise TeamManagementError(
                            "invalid_payload",
                            "toggle requires payload.enabled bool")
                    writer_result = task_collaboration.set_member_screening(
                        store, actor_user_id=owner_id, user_id=target_user_id,
                        enabled=enabled, task_id=task_id,
                        conn=inputs.collab_conn)
                elif action == "business_role":
                    writer_result = task_collaboration.set_business_role(
                        store, actor_user_id=owner_id, user_id=target_user_id,
                        business_role=_payload_role(payload), task_id=task_id,
                        conn=inputs.collab_conn)
                else:  # remove
                    revoked = task_collaboration.revoke_role(
                        store, actor_user_id=owner_id, user_id=target_user_id,
                        role=_payload_role(payload), task_id=task_id,
                        conn=inputs.collab_conn)
                    writer_result = {"user_id": target_user_id,
                                     "revoked": revoked}

                # 6. 实际 roster/source 变化才推进 revision（writers 之后
                # 用 P01 只读聚合在 collab 屏障上现读新集——scope 缓存停在
                # 写前名单，不能当作变更后事实）
                fresh_state = task_collaboration.read_team_role_state_on(
                    inputs.collab_conn, task_id=task_id)
                new_roster, new_required = _roster_from_state(
                    fresh_state, usernames)
                changed = (_roster_fingerprint(old_roster, old_required)
                           != _roster_fingerprint(new_roster, new_required))
                if action == "remove":
                    changed = changed or retire_registration is not None
                if action == "add":
                    registration = _register_member_source(
                        inputs, instance=instance, uid=target_user_id,
                        username=usernames.get(
                            target_user_id, str(payload.get("username", ""))))
                    writer_result = dict(writer_result)
                    writer_result["source_registration"] = registration
                    if registration.get("status") == "active":
                        changed = True
                new_revision = previous_revision
                if changed:
                    new_revision = int(set_roster_revision_on(
                        inputs.collab_conn, task_instance_id=instance,
                        expected_revision=previous_revision)["roster_revision"])

                # 7. 新集首次一致：变更成功后才为未完成部分用新 revision
                if changed:
                    for key in candidate_keys:
                        for stage in _ENUM_STAGES:
                            _append_set_observation(
                                inputs, instance=instance, stage=stage,
                                key=key, roster=new_roster,
                                required_uids=new_required,
                                roster_revision=new_revision,
                                observer_uid=owner_id, request_id=request_id,
                                request_sha256=request_sha256,
                                trigger="current_observation", sealed=sealed)

                # 8. 管理 receipt（collab 原产；绑定对 None＝已知现状，P22 义务）
                receipt = append_receipt_on(inputs.target_conn, {
                    "schema": "team_flow_receipt.v1",
                    "task_instance_id": instance,
                    "actor_user_id": user_id,
                    "operation": op_key,
                    "request_id": request_id,
                    "request_sha256": request_sha256,
                    "result": {
                        "request_id": request_id,
                        "operation": op_key,
                        "action": action,
                        "target_user_id": target_user_id,
                        "writer": writer_result,
                        "seals": [dict(item) for item in sealed],
                        "roster_changed": changed,
                    },
                    "internal_evidence": {
                        "target_kind": "collab",
                        "idempotent": False,
                        "previous_roster_revision": previous_revision,
                        "new_roster_revision": new_revision,
                        "candidate_keys": list(candidate_keys),
                        "archive_evidence": archive_evidence,
                    },
                    "recorded_at": _utc_now(),
                    "source_registration_id": None,
                    "source_generation": None,
                })

                # 9. 退休：watermark 只指向同 commit 枚举证据（sha256）
                if retire_registration is not None \
                        and archive_evidence is not None:
                    retire_source_on(
                        inputs.collab_conn,
                        registration_id=retire_registration["registration_id"],
                        retired_reason=f"owner_{action}:{request_id}",
                        archive_watermark=seal_sha256(archive_evidence))

                result_payload = {
                    "outcome": "committed", "idempotent": False,
                    "request_id": request_id,
                    "result": receipt["result"], "receipt": receipt,
                    "seals": tuple(dict(item) for item in sealed),
                    "roster_revision": new_revision,
                }
                body_completed = True
    except BaseException as exc:  # noqa: BLE001 - 区分 body 失败与 commit 不可判定
        if body_completed:
            unknown_cleanup = (window_inputs.cleanup_errors
                               if window_inputs is not None else ())
            return {
                "outcome": "unknown", "idempotent": False,
                "request_id": request_id, "result": None, "receipt": None,
                "seals": (), "roster_revision": None,
                "cleanup_errors": tuple(unknown_cleanup),
                "error": f"{type(exc).__name__}: {exc}",
            }
        raise
    assert result_payload is not None
    cleanup = window_inputs.cleanup_errors if window_inputs is not None else ()
    result_payload["cleanup_errors"] = tuple(cleanup)
    return result_payload
