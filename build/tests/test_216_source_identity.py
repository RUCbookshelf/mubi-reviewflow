# -*- coding: utf-8 -*-
"""P06 可信历史路径定位（source identity）独立测试候选 —— 静态撰写，作者未运行。

目标产品候选（本文件唯一针对的实现）：
- E:/rf216-scratch/p06-source-id-20261118T151608Z-1482/candidate/team_source_lifecycle.py
- SHA-256 e4f9fb75015201897b75e9ee0b74a071e066ba576675b8c612f9ba7e8ea84b6f
  （513 行；已过 g-flash 产品复审 PASS-with-notes）
- 主仓基线不含 P06（已核：全仓无 team_source_lifecycle.py，custom_backend/
  下无同名文件）：本文件在测试箱内对候选副本（箱根/candidate/
  team_source_lifecycle.py，SHA 已核一致）经 importlib 文件位置加载运行
  （parents 协议同 P05/P07 测试箱）；若对主仓 build/tests 运行将在模块加载期
  因 candidate/ 缺位而诚实红（不以 ImportError 冒充行为红证；集成时由协调者
  迁移加载路径）。
- 候选 import coscreen.team_terminal_registry / team_vote_ledger（P03/P04
  只读消费点）：本文件把主仓根 E:/mubi-reviewflow 插入 sys.path 供其解析；
  运行解释器必须 -B（隔离模式不向只读主仓写 __pycache__）。主仓缺位时本文件
  以明确 RuntimeError 拒绝静默降级。

合同依据（全部只读引用，本文不编辑任何合同/产品文件）：
- docs/superpowers/plans/2026-11-16-2.1.6-team-contracts.md C05（来源身份
  token 与可信历史路径定位）全节；P06 包工单钉定本文件名与 5 个具名用例。
- 复审已定口径（写测试必须遵守，本文件逐条落实）：
  1. canonical 等值检查按实测复刻：键序/重复键/NaN/ASCII 转义/尾随空白全拒
     （token_unparsed，用例 5 C 节）；canonical 接受面含 Unicode relative
     正控（ensure_ascii=False 原文往返，用例 5 E 节）。
  2. 假前缀（task 根 vs 同级 teamx 前缀串）→ path_escape：make 侧物理包含
     按 root+os.sep 前缀法判（用例 2 E 节）。
  3. 设备名 → not_regular_file：本机 Windows 26200 实测 mid-path "NUL" 为
     字符设备（S_ISCHR，probe 存档）；CON/AUX 在本 build 非设备名（缺失
     路径，同归 not_regular_file，不单独钉）。相对 "NUL" 断言在两种世界下
     都落 not_regular_file（用例 2 F 节）。
  4. task_dir 不存在 → ValueError（N3 口径：调用方合同违规走 ValueError
     编程错误通道，不伪装 typed issue；SourceIdentityIssue 是 RuntimeError
     子类，assertRaises(ValueError) 即钉不伪装，用例 2 G 节）。
  5. P04 头兼容夹具照建：meta 表 canonical DDL（冻结字面量镜像，probe 已验
     与主仓 _META_DDL _norm 等值且真实 library_identity_header_on 点查通过）
     + 单例行（singleton=1, format_version=2, 32 位小写 hex library_id）；
     mode=ro+BEGIN+Row 借用连接合同由产品侧 _actual_library_id 自建处理，
     测试只建库文件。
  6. 用例 4 复审边界：同物理文件被两 UID 以相同且正确的 library_id 声明在
     物理层不可分辨（由 P03 登记唯一性兜底）——本用例不要求物理级 UID 分辨，
     钉"不全局禁止"语义。

用例 → 合同条款映射（5 具名用例，工单钉死名称）：
- OldSlugRenameTests.test_old_slug_after_account_rename
    C05 条款 1/2：token relative 以 task_dir 为基准原样记录/回放老 slug
    （含 Unicode slug）；make 经 relpath(realpath) 推导（界内 junction 别名
    路径签发 → token 记录真实目录而非别名）；resolve 只按 token 回放；
    账号重命名（username 环境全翻转 + 新 slug 位置放真实不同的库作诱饵）
    后定位不变、零 username 依赖；登记 UID 声明在场不引入 username。
- TokenTraversalJunctionTests.test_token_traversal_and_junction
    C05 拒绝矩阵：绝对/NUL/反斜杠/./.. /空分量/冒号分量（drive/ADS）→
    path_traversal（词法期 params 只带 reason）；canonical 但键集/version/
    base/值域不符 → token_shape_invalid；越界 junction（中间/末分量）→
    path_escape（claim 即外部库真身，包含判定为唯一拦截面）；界内 junction
    不拒且返回 realpath 已解析物理路径；假前缀拒；缺失/目录/设备名 →
    not_regular_file（resolve+make 双侧）；make 参数族 TypeError/ValueError
    与 UID/library 边界对（1 与 int64 上界合法）。
- CrossUidPhysicalAliasTests.test_cross_uid_physical_alias
    C05 最终核：实际 library 身份头 ≠ token 声明 → library_mismatch
    （params 携带 source_user_id/relative；跨 UID 物理别名在此落网）；
    成对正控：同文件正确声明放行、u101 老 token 不受影响；identity_mismatch
    邻接（登记声明冲突/值域非法）与正控；library_unverifiable 邻接族
    （空文件 unmigrated / 非 db 文本 / 值级 meta_corrupt 非 hex library_id）
    与优先序（不可读先于比对，声明错也不冒充 mismatch）。
- SameUuidDifferentFilesTests.test_same_uuid_different_files_not_globally_forbidden
    C05 身份按组合判：同 UID+同 library_id 出现在两个不同文件不全局禁止；
    inode 不充当逻辑 UUID（删除重建新 file index 后老 token 仍定位）；
    一次 library_mismatch 失败不毒化后续调用（零跨调用状态）；同一 token
    文本对另一 task_dir 的同 relative 同库副本各自定位（token 不内嵌 task
    身份，跨任务唯一性由 P03 兜底）；同物理文件两 UID 相同正确声明 → 不
    全局禁止（复审边界口径 6）。
- UnmappedLegacyIssueTests.test_unmapped_legacy_remains_issue
    C05 legacy 未映射保持 issue：source 缺 source_path_identity/非文本 →
    token_unparsed；非 JSON/非 JSON 对象 → token_unparsed；JSON 非 canonical
    全族（口径 1）→ token_unparsed；三重诱饵（username 可猜目录、token 原文
    同名根文件、legacy 裸路径文本恰指向真实库）均不触发按 username 猜测或
    把 token 原文当 Path；失败不吞不跳过不隔离（诱饵与真实库原位）；
    canonical 正控（含 Unicode relative）解析成功；typed 通道词汇表钉
    （REASONS 恰 8 词、未知 reason ValueError、params None 键省略）。

诚实记录（环境不可达/不可分辨项，不伪造测试）：
- 文件 symlink 无特权（本机 OSError 22，probe 存档 selfcheck/probe_env.log）：
  末分量"文件 symlink"越界不可构造；path_escape 以目录 junction（mklink /J，
  无特权要求，probe 实测可用）的中间分量与末分量（junction 指目录）两变体
  覆盖；目录 symlink 为 junction 不可用时的一致性退路（p06_make_junction）。
- 8.3 短名在本卷关闭（GetShortPathNameW 原样返回，probe 存档）：realpath
  推导钉法改用界内 junction 别名（等价覆盖"别名路径 → 真实路径"归一）。
- 值级 meta_corrupt（meta 行 format_version=3 等）被 canonical DDL 的 CHECK
  拦（probe 实测 IntegrityError）：以"canonical DDL + 非 hex 32 位
  library_id"值级变体覆盖 meta_corrupt → library_unverifiable 通道。
- CON/AUX 在本 Windows build 非设备名（缺失路径）：设备名代表取 NUL。
- 同物理文件两 UID 相同且正确 library_id 的物理不可分辨（口径 6）：只钉
  不全局禁止，不断言 UID 分辨。

撰写协议（同 P04/P05/P07 先例）：本文件每个 def/class 符号（共 21 项，与
raw/SYMBOLS.tsv 一致）在写入前均逐项运行 GitNexus CLI upstream impact
（--repo E:/mubi-reviewflow --file build/tests/test_216_source_identity.py
--kind Class/Method/Function --include-tests）；新文件预期 not found/UNKNOWN，
raw/rc/MANIFEST 存档于 E:/rf216-scratch/p06-source-tests-20261118T184919Z-5272/
raw/（共 21 项，每次 CLI 前经 ps 门确认无其他 gitnexus 作业）。

未运行声明：作者仅静态撰写（ast.parse/compile 与符号清单自检），未运行
任何测试用例/产品候选 API；唯一执行物为环境可行性探针（scripts/probe_env.py，
只读引用主仓 P04 头对探针自建库点查，日志存 selfcheck/probe_env.log）。
执行留给独立 runner 在应用产品候选后进行。禁主仓/前端/真实 data/旧证据箱
写入；夹具全部位于测试自有 TemporaryDirectory；零 mock（探针为真实文件
系统/junction/环境变量操作，非替换对象）。自包含：不依赖 conftest/Safety
夹具、不触 HTTP 层；静态消费者仅 unittest。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_P06_BOX_ROOT = Path(__file__).resolve().parents[2]
_P06_CANDIDATE = _P06_BOX_ROOT / "candidate" / "team_source_lifecycle.py"
_P06_REPO_ROOT = Path("E:/mubi-reviewflow")
if not _P06_REPO_ROOT.is_dir():
    raise RuntimeError(
        "P06 测试需要主仓 E:/mubi-reviewflow（候选 import coscreen P03/P04）；"
        "主仓缺位时本文件拒绝静默降级")
if str(_P06_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_P06_REPO_ROOT))
_P06_SPEC = importlib.util.spec_from_file_location(
    "team_source_lifecycle", _P06_CANDIDATE)
tsl = importlib.util.module_from_spec(_P06_SPEC)
_P06_SPEC.loader.exec_module(tsl)

#: 冻结候选 SHA-256（运行时打印实测值供核对，不做硬断言：候选后续修订经
#: 复审后 SHA 自然变化，硬断言会制造假红）。
P06_CANDIDATE_SHA = (
    "e4f9fb75015201897b75e9ee0b74a071e066ba576675b8c612f9ba7e8ea84b6f")

#: C05 v1 token 精确五键与 typed issue 词表（测试侧冻结常量，不取自模块，
#: 以独立钉住合同形状）。
P06_TOKEN_KEYS = frozenset(
    {"version", "base", "relative", "source_user_id", "source_library_id"})
P06_EXPECTED_REASONS = (
    "token_unparsed", "token_shape_invalid", "path_traversal", "path_escape",
    "not_regular_file", "identity_mismatch", "library_unverifiable",
    "library_mismatch",
)
P06_UID_MAX = 9223372036854775807

#: 确定性 32 位小写 hex library_id 取值（按用例区分，避免跨用例混淆）。
P06_LIB_OLD = "1a" * 16      # 用例 1：老 slug 成员库
P06_LIB_DECOY = "2b" * 16    # 用例 1：新 slug 诱饵库（≠ 老库）
P06_LIB_ESC = "3c" * 16      # 用例 2：越界目标库真身
P06_LIB_IN = "4d" * 16       # 用例 2：界内正控库
P06_LIB_PREFIX = "5e" * 16   # 用例 2：假前缀同级库
P06_LIB_A = "6f" * 16        # 用例 3：u101 真库/别名 token 声明
P06_LIB_B = "7a" * 16        # 用例 3：别名文件实际 meta
P06_LIB_SAME = "8b" * 16     # 用例 4：同 UID 多文件共享库 ID
P06_LIB_OTHER = "9c" * 16    # 用例 4：错声明干扰探针
P06_LIB_LEGACY = "0d" * 16   # 用例 5：legacy 正控库

#: P04 身份头所需 meta 表 canonical DDL 冻结镜像（coscreen/team_vote_ledger.py
#: _META_DDL，format 2；probe 已验 _norm 等值 + sqlite_master 存储等值 +
#: 真实 library_identity_header_on 点查通过）。revisions 表在 meta 存在时
#: 不参与身份头判定（_identity_header_checked 仅在 meta 缺失时区分部分安装），
#: 故夹具只建 meta 表即 P04 头兼容。
P06_META_DDL = (
    "CREATE TABLE team_vote_revision_meta (\n"
    "  singleton INTEGER PRIMARY KEY CHECK(singleton=1),\n"
    "  format_version INTEGER NOT NULL CHECK(format_version=2),\n"
    "  library_id TEXT NOT NULL CHECK(length(library_id)=32),\n"
    "  seeded_stage1 INTEGER NOT NULL"
    " CHECK(typeof(seeded_stage1)='integer' AND seeded_stage1>=0),\n"
    "  seeded_stage2 INTEGER NOT NULL"
    " CHECK(typeof(seeded_stage2)='integer' AND seeded_stage2>=0),\n"
    "  advances INTEGER NOT NULL"
    " CHECK(typeof(advances)='integer' AND advances>=0) DEFAULT 0\n"
    ")"
)

TAG_RENAME = "P06-old slug rename stability"
TAG_TRAVERSAL = "P06-token traversal/junction"
TAG_ALIAS = "P06-cross uid physical alias"
TAG_SAMEUUID = "P06-same uuid multi-file"
TAG_LEGACY = "P06-unmapped legacy issue"

_P06_PROVENANCE_PRINTED = False


def p06_canonical_json(value):
    """canonical JSON 镜像（独立重实现，钉候选 token 的冻结序列化参数）。"""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False)


def p06_canonical_token(relative="members/library.db", source_user_id=101,
                        source_library_id=P06_LIB_IN, extra=None, drop=()):
    """构造 canonical v1 token 文本（extra 合并覆盖字段、drop 删键做形状变体）。"""
    payload = {
        "version": 1,
        "base": "task_dir",
        "relative": relative,
        "source_user_id": source_user_id,
        "source_library_id": source_library_id,
    }
    if extra is not None:
        payload.update(extra)
    for key in drop:
        payload.pop(key, None)
    return p06_canonical_json(payload)


def p06_source(token, **claims):
    """构造 source Mapping：source_path_identity 必带 + 可选登记声明。"""
    record = {"source_path_identity": token}
    record.update(claims)
    return record


def p06_set_username_env(case, username):
    """翻转 username 类环境变量（USERNAME/USER/LOGNAME）并注册恢复。

    真实环境操作（非 mock）：用于行为性钉"零 username 依赖/不按 username 猜"。
    """
    saved = {key: os.environ.get(key) for key in ("USERNAME", "USER", "LOGNAME")}
    for key in saved:
        os.environ[key] = username
    case.addCleanup(p06_restore_env, saved)


def p06_restore_env(mapping):
    """恢复环境变量（None 表示原本不存在；addCleanup 挂接用）。"""
    for key, value in mapping.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def p06_make_junction(link, target):
    """目录链接夹具：junction 优先（cmd mklink /J，无特权要求，probe 实测可用）。

    mklink 不可行时退目录 symlink（开发者模式环境）；两者均失败时以明确
    RuntimeError 上抛（本环境无法构造 path_escape/界内别名夹具）——不静默
    跳过用例。文件 symlink 在本机无特权（诚实记录见模块 docstring），故
    越界/别名夹具一律走目录链接。
    """
    link = Path(link)
    target = Path(target)
    failure = ""
    try:
        proc = subprocess.run(
            ["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, timeout=30)
        if proc.returncode == 0 and link.is_dir():
            return link
        failure = (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()
    except OSError as exc:
        failure = repr(exc)
    try:
        os.symlink(str(target), str(link), target_is_directory=True)
        return link
    except OSError as exc:
        raise RuntimeError(
            "P06 夹具：目录 junction（mklink /J 失败：%s）与目录 symlink（%r）"
            "均不可用——无法构造 path_escape/界内别名夹具" % (failure, exc)) from exc


class SourceIdentityTestBase(unittest.TestCase):
    """公共基座：真实文件系统夹具（TemporaryDirectory + 真实成员库文件），零 mock。"""

    maxDiff = None

    def setUp(self):
        global _P06_PROVENANCE_PRINTED
        self._tmp = tempfile.TemporaryDirectory(prefix="rf216_p06_")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        if not _P06_PROVENANCE_PRINTED:
            _P06_PROVENANCE_PRINTED = True
            print("P06_TEST_FILE_SHA",
                  hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
            print("P06_CANDIDATE_SOURCE_SHA",
                  hashlib.sha256(_P06_CANDIDATE.read_bytes()).hexdigest())
            print("P06_CANDIDATE_PINNED_SHA", P06_CANDIDATE_SHA)
            print("P06_REPO_LEDGER_SHA", hashlib.sha256(
                (_P06_REPO_ROOT / "coscreen" / "team_vote_ledger.py")
                .read_bytes()).hexdigest())
            print("P06_REPO_REGISTRY_SHA", hashlib.sha256(
                (_P06_REPO_ROOT / "coscreen" / "team_terminal_registry.py")
                .read_bytes()).hexdigest())
            print("P06_PYTHON", sys.version.split()[0])
            print("P06_SQLITE_VERSION", sqlite3.sqlite_version)

    def _task_dir(self, name="task"):
        """新建任务目录（绝对路径、真实目录）。"""
        path = self.base / name
        path.mkdir()
        return path

    def _member_library(self, parent, filename, library_id):
        """P04 头兼容成员库夹具：canonical meta DDL + 单行（format 2、32hex）。

        测试只建库；mode=ro+BEGIN+Row 借用连接合同由产品侧 _actual_library_id
        自建连接满足。值级 corrupt 变体＝传入非 hex 的 32 位文本（过长度
        CHECK、被头实现 hex 校验拒）。
        """
        parent.mkdir(parents=True, exist_ok=True)
        path = parent / filename
        conn = sqlite3.connect(str(path))
        try:
            conn.execute(P06_META_DDL)
            conn.execute(
                "INSERT INTO team_vote_revision_meta"
                " (singleton, format_version, library_id, seeded_stage1,"
                " seeded_stage2, advances) VALUES (1, 2, ?, 0, 0, 0)",
                (library_id,))
            conn.commit()
        finally:
            conn.close()
        return path

    @staticmethod
    def _real(path):
        """realpath 归一（镜像产品包含判定/返回路径的物理基准）。"""
        return Path(os.path.realpath(str(path)))


class OldSlugRenameTests(SourceIdentityTestBase):
    """用例 1：token 原样记录/回放老 slug；账号重命名后定位不变、零 username 依赖。"""

    def test_old_slug_after_account_rename(self):
        task = self._task_dir()
        old_dir = task / "成员账号旧名"  # Unicode slug（probe 已验 as_uri+mode=ro 往返）
        old_lib = self._member_library(old_dir, "library.db", P06_LIB_OLD)
        token = tsl.make_source_identity(
            task_dir=task, source_path=old_lib,
            source_user_id=101, source_library_id=P06_LIB_OLD)
        parsed = json.loads(token)
        expected_rel = os.path.relpath(
            str(self._real(old_lib)), str(self._real(task))
        ).replace(os.sep, "/")
        self.assertEqual(set(parsed), P06_TOKEN_KEYS, TAG_RENAME)
        self.assertEqual(parsed["version"], 1, TAG_RENAME)
        self.assertEqual(parsed["base"], "task_dir", TAG_RENAME)
        self.assertEqual(parsed["source_user_id"], 101, TAG_RENAME)
        self.assertEqual(parsed["source_library_id"], P06_LIB_OLD, TAG_RENAME)
        self.assertEqual(parsed["relative"], expected_rel,
                         TAG_RENAME + ": 相对路径以 task_dir 为基准推导")
        self.assertEqual(expected_rel, "成员账号旧名/library.db",
                         TAG_RENAME + ": 老 slug 原样入 token")
        self.assertEqual(token, p06_canonical_json(parsed),
                         TAG_RENAME + ": token 即 canonical 文本")
        # make 经 relpath(realpath) 推导：界内 junction 别名路径签发 → 记录真实目录
        p06_make_junction(task / "alias_to_old", old_dir)
        token_via_alias = tsl.make_source_identity(
            task_dir=task, source_path=task / "alias_to_old" / "library.db",
            source_user_id=101, source_library_id=P06_LIB_OLD)
        self.assertEqual(token_via_alias, token,
                         TAG_RENAME + ": relpath(realpath) 推导，别名不进 token")
        # “账号重命名”：username 环境全翻转 + 新 slug 位置放一个真实且不同的库诱饵
        p06_set_username_env(self, "renamed-new-slug")
        decoy_lib = self._member_library(
            task / "renamed-new-slug", "library.db", P06_LIB_DECOY)
        resolved = tsl.resolve_source_identity(
            task_dir=task, source=p06_source(token))
        self.assertEqual(resolved, self._real(old_lib),
                         TAG_RENAME + ": 定位不变＝老 slug 物理文件")
        self.assertNotEqual(resolved, self._real(decoy_lib),
                            TAG_RENAME + ": 不按当前 username 改写定位")
        self.assertTrue(resolved.is_absolute(), TAG_RENAME)
        # 登记 UID 声明在场同样解析（交叉核对通道也不引入 username）
        resolved_with_uid = tsl.resolve_source_identity(
            task_dir=task, source=p06_source(token, source_user_id=101))
        self.assertEqual(resolved_with_uid, resolved, TAG_RENAME)
        # 再换一次 username：结果逐字节一致（零 username 依赖）
        p06_set_username_env(self, "completely-different-name")
        resolved_again = tsl.resolve_source_identity(
            task_dir=task, source=p06_source(token))
        self.assertEqual(resolved_again, resolved,
                         TAG_RENAME + ": 零 username 依赖")


class TokenTraversalJunctionTests(SourceIdentityTestBase):
    """用例 2：词法拒绝表 + shape 族 + junction 越界/界内 + 假前缀 + not_regular_file 族。"""

    def test_token_traversal_and_junction(self):
        task = self._task_dir()
        in_lib = self._member_library(task / "members", "library.db", P06_LIB_IN)
        # --- 正控：canonical token 定位成功 ---
        good = p06_canonical_token(
            relative="members/library.db", source_user_id=101,
            source_library_id=P06_LIB_IN)
        self.assertEqual(
            tsl.resolve_source_identity(task_dir=task, source=p06_source(good)),
            self._real(in_lib), TAG_TRAVERSAL + ": canonical 正控")
        # --- A. 词法拒绝表（全部 canonical、其余字段合法）→ path_traversal ---
        bad_relatives = (
            "", "/abs/lib.db", "C:/dir/lib.db", "C:\\dir\\lib.db",
            "dir\\lib.db", "a\x00b.db", "./lib.db", "members/./lib.db",
            "../lib.db", "members/../lib.db", "members//lib.db",
            "members/lib.db/", "members/lib.db//", "C:lib.db",
            "members/lib.db:ads", ":lib.db",
        )
        for bad in bad_relatives:
            with self.subTest(relative=repr(bad)):
                token = p06_canonical_token(relative=bad)
                with self.assertRaises(
                        tsl.SourceIdentityIssue, msg=TAG_TRAVERSAL) as caught:
                    tsl.resolve_source_identity(
                        task_dir=task, source=p06_source(token))
                self.assertEqual(caught.exception.reason, "path_traversal",
                                 TAG_TRAVERSAL + " " + repr(bad))
                self.assertEqual(
                    caught.exception.params, {"reason": "path_traversal"},
                    TAG_TRAVERSAL + ": 词法期不带 uid/relative 载荷")
        # --- B. canonical JSON 但键集/version/base/值域不符 → token_shape_invalid ---
        shape_cases = (
            ("缺 version", p06_canonical_token(drop=("version",))),
            ("缺 relative", p06_canonical_token(drop=("relative",))),
            ("多键", p06_canonical_token(extra={"extra": 1})),
            ("version=2", p06_canonical_token(extra={"version": 2})),
            ("version=True", p06_canonical_token(extra={"version": True})),
            ("version=1.0", p06_canonical_token(extra={"version": 1.0})),
            ("base=storage_root",
             p06_canonical_token(extra={"base": "storage_root"})),
            ("relative 非 str", p06_canonical_token(relative=123)),
            ("uid=0", p06_canonical_token(source_user_id=0)),
            ("uid=-1", p06_canonical_token(source_user_id=-1)),
            ("uid 超 int64",
             p06_canonical_token(source_user_id=9223372036854775808)),
            ("uid float", p06_canonical_token(source_user_id=101.0)),
            ("uid bool", p06_canonical_token(source_user_id=True)),
            ("uid str", p06_canonical_token(source_user_id="101")),
            ("library 大写", p06_canonical_token(source_library_id="1A" * 16)),
            ("library 31 位", p06_canonical_token(source_library_id="1" * 31)),
            ("library 非 hex",
             p06_canonical_token(source_library_id="g" * 32)),
        )
        for label, token in shape_cases:
            with self.subTest(shape=label):
                with self.assertRaises(
                        tsl.SourceIdentityIssue,
                        msg=TAG_TRAVERSAL) as caught:
                    tsl.resolve_source_identity(
                        task_dir=task, source=p06_source(token))
                self.assertEqual(caught.exception.reason, "token_shape_invalid",
                                 TAG_TRAVERSAL + " " + label)
        # --- C. 越界 junction → path_escape（claim 即外部库真身：包含判定是唯一拦水面）---
        outside_dir = self.base / "outside"
        self._member_library(outside_dir, "library.db", P06_LIB_ESC)
        p06_make_junction(task / "escape_junction", outside_dir)
        escape_middle = p06_canonical_token(
            relative="escape_junction/library.db", source_user_id=101,
            source_library_id=P06_LIB_ESC)
        with self.assertRaises(
                tsl.SourceIdentityIssue, msg=TAG_TRAVERSAL) as caught:
            tsl.resolve_source_identity(
                task_dir=task, source=p06_source(escape_middle))
        self.assertEqual(caught.exception.reason, "path_escape",
                         TAG_TRAVERSAL + ": 中间分量 junction 越界")
        p06_make_junction(task / "lib_as_junction", outside_dir)
        escape_final = p06_canonical_token(
            relative="lib_as_junction", source_user_id=101,
            source_library_id=P06_LIB_ESC)
        with self.assertRaises(
                tsl.SourceIdentityIssue, msg=TAG_TRAVERSAL) as caught:
            tsl.resolve_source_identity(
                task_dir=task, source=p06_source(escape_final))
        self.assertEqual(caught.exception.reason, "path_escape",
                         TAG_TRAVERSAL + ": 末分量 junction 越界（目标为目录）")
        # --- D. 界内 junction 不拒：别名 relative 定位到物理真实路径 ---
        p06_make_junction(task / "aliasdir", task / "members")
        alias_token = p06_canonical_token(
            relative="aliasdir/library.db", source_user_id=101,
            source_library_id=P06_LIB_IN)
        self.assertEqual(
            tsl.resolve_source_identity(
                task_dir=task, source=p06_source(alias_token)),
            self._real(in_lib),
            TAG_TRAVERSAL + ": 界内 symlink/junction 不拒，返回 realpath 物理路径")
        # --- E. 假前缀（team vs teamx）：make 侧物理包含按 root+分隔符判 ---
        prefix_root = self._task_dir("team")
        sibling_lib = self._member_library(
            self.base / "teamx", "library.db", P06_LIB_PREFIX)
        with self.assertRaises(
                tsl.SourceIdentityIssue, msg=TAG_TRAVERSAL) as caught:
            tsl.make_source_identity(
                task_dir=prefix_root, source_path=sibling_lib,
                source_user_id=1, source_library_id=P06_LIB_PREFIX)
        self.assertEqual(caught.exception.reason, "path_escape",
                         TAG_TRAVERSAL + ": 假前缀拒（前缀串不越界）")
        # --- F. 目标缺失/目录/设备名 → not_regular_file（resolve+make 双侧）---
        (task / "adir").mkdir()
        for relative in ("ghost/library.db", "adir", "NUL"):
            with self.subTest(not_regular=relative):
                token = p06_canonical_token(relative=relative)
                with self.assertRaises(
                        tsl.SourceIdentityIssue,
                        msg=TAG_TRAVERSAL) as caught:
                    tsl.resolve_source_identity(
                        task_dir=task, source=p06_source(token))
                self.assertEqual(caught.exception.reason, "not_regular_file",
                                 TAG_TRAVERSAL + " " + relative)
        for bad_path in (task / "ghost.db", task / "adir"):
            with self.subTest(make_not_regular=str(bad_path)):
                with self.assertRaises(
                        tsl.SourceIdentityIssue,
                        msg=TAG_TRAVERSAL) as caught:
                    tsl.make_source_identity(
                        task_dir=task, source_path=bad_path,
                        source_user_id=101, source_library_id=P06_LIB_IN)
                self.assertEqual(caught.exception.reason, "not_regular_file",
                                 TAG_TRAVERSAL + " " + str(bad_path))
        # --- G. 编程错误分离（N3 口径：task_dir 缺失→ValueError，不伪装 typed issue）---
        missing_dir = self.base / "no_such_task_dir"
        with self.assertRaises(ValueError, msg=TAG_TRAVERSAL):
            tsl.make_source_identity(
                task_dir=missing_dir, source_path=missing_dir / "x.db",
                source_user_id=1, source_library_id=P06_LIB_IN)
        with self.assertRaises(ValueError, msg=TAG_TRAVERSAL):
            tsl.resolve_source_identity(
                task_dir=missing_dir, source=p06_source(good))
        file_as_dir = self.base / "plain.db"
        file_as_dir.write_bytes(b"x")
        with self.assertRaises(ValueError, msg=TAG_TRAVERSAL):
            tsl.make_source_identity(
                task_dir=file_as_dir, source_path=file_as_dir,
                source_user_id=1, source_library_id=P06_LIB_IN)
        with self.assertRaises(TypeError, msg=TAG_TRAVERSAL):
            tsl.make_source_identity(
                task_dir=str(task), source_path=in_lib,
                source_user_id=1, source_library_id=P06_LIB_IN)
        with self.assertRaises(TypeError, msg=TAG_TRAVERSAL):
            tsl.make_source_identity(
                task_dir=task, source_path=str(in_lib),
                source_user_id=1, source_library_id=P06_LIB_IN)
        with self.assertRaises(ValueError, msg=TAG_TRAVERSAL):
            tsl.make_source_identity(
                task_dir=Path("relative/dir"), source_path=in_lib,
                source_user_id=1, source_library_id=P06_LIB_IN)
        with self.assertRaises(ValueError, msg=TAG_TRAVERSAL):
            tsl.make_source_identity(
                task_dir=task, source_path=Path("members/library.db"),
                source_user_id=1, source_library_id=P06_LIB_IN)
        with self.assertRaises(TypeError, msg=TAG_TRAVERSAL):
            tsl.resolve_source_identity(
                task_dir=task, source=["not", "a", "mapping"])
        # make 侧 UID/library 值域（拒 bool/越界/float/str/非 hex/长度差）。
        for bad_uid in (0, -1, 9223372036854775808, True, 1.5, "101"):
            with self.subTest(make_bad_uid=repr(bad_uid)):
                with self.assertRaises(ValueError, msg=TAG_TRAVERSAL):
                    tsl.make_source_identity(
                        task_dir=task, source_path=in_lib,
                        source_user_id=bad_uid, source_library_id=P06_LIB_IN)
        for bad_lib in ("1A" * 16, "1" * 31, "g" * 32, 123):
            with self.subTest(make_bad_lib=repr(bad_lib)):
                with self.assertRaises(ValueError, msg=TAG_TRAVERSAL):
                    tsl.make_source_identity(
                        task_dir=task, source_path=in_lib,
                        source_user_id=101, source_library_id=bad_lib)
        # 合法边界对：UID 1 与 int64 上界均签发且 token 互异（UID 入 token）。
        token_uid_min = tsl.make_source_identity(
            task_dir=task, source_path=in_lib,
            source_user_id=1, source_library_id=P06_LIB_IN)
        token_uid_max = tsl.make_source_identity(
            task_dir=task, source_path=in_lib,
            source_user_id=P06_UID_MAX, source_library_id=P06_LIB_IN)
        self.assertEqual(json.loads(token_uid_min)["source_user_id"], 1,
                         TAG_TRAVERSAL + ": UID 下边界合法")
        self.assertEqual(json.loads(token_uid_max)["source_user_id"], P06_UID_MAX,
                         TAG_TRAVERSAL + ": UID 上边界合法")
        self.assertNotEqual(token_uid_min, token_uid_max, TAG_TRAVERSAL)


class CrossUidPhysicalAliasTests(SourceIdentityTestBase):
    """用例 3：实际 library 身份 ≠ token 声明 → library_mismatch；与不可读族成对。"""

    def test_cross_uid_physical_alias(self):
        task = self._task_dir()
        lib_a = self._member_library(
            task / "users" / "u101", "library.db", P06_LIB_A)
        token_a = tsl.make_source_identity(
            task_dir=task, source_path=lib_a,
            source_user_id=101, source_library_id=P06_LIB_A)
        self.assertEqual(
            tsl.resolve_source_identity(task_dir=task, source=p06_source(token_a)),
            self._real(lib_a), TAG_ALIAS + ": u101 真库正控")
        # 跨 UID 物理别名：u202 位置实际放着 meta=LIB_B 的库文件，
        # token 声明 (u202, LIB_A)——物理身份与声明不符在此落网。
        alias_file = self._member_library(
            task / "users" / "u202", "library.db", P06_LIB_B)
        alias_relative = "users/u202/library.db"
        claim_token = p06_canonical_token(
            relative=alias_relative, source_user_id=202,
            source_library_id=P06_LIB_A)
        with self.assertRaises(
                tsl.SourceIdentityIssue, msg=TAG_ALIAS) as caught:
            tsl.resolve_source_identity(
                task_dir=task, source=p06_source(claim_token))
        issue = caught.exception
        self.assertEqual(issue.reason, "library_mismatch",
                         TAG_ALIAS + ": 实际 library ≠ token 声明")
        self.assertEqual(
            issue.params,
            {"reason": "library_mismatch", "source_user_id": 202,
             "relative": alias_relative},
            TAG_ALIAS + ": 结构化载荷携带 uid/relative")
        self.assertIn("[library_mismatch]", str(issue), TAG_ALIAS)
        # 成对正控 1：同文件声明改成真实 LIB_B → 放行（身份按组合判，不跨 UID 一票否决）
        ok_token = p06_canonical_token(
            relative=alias_relative, source_user_id=202,
            source_library_id=P06_LIB_B)
        self.assertEqual(
            tsl.resolve_source_identity(task_dir=task, source=p06_source(ok_token)),
            self._real(alias_file), TAG_ALIAS + ": 正确声明放行")
        # 成对正控 2：u101 老 token 不受别名文件影响
        self.assertEqual(
            tsl.resolve_source_identity(task_dir=task, source=p06_source(token_a)),
            self._real(lib_a), TAG_ALIAS + ": 老定位不受别名影响")
        # identity_mismatch 邻接：登记声明与 token 冲突/值域非法（先于物理定位）。
        with self.assertRaises(
                tsl.SourceIdentityIssue, msg=TAG_ALIAS) as caught:
            tsl.resolve_source_identity(
                task_dir=task,
                source=p06_source(claim_token, source_user_id=303))
        self.assertEqual(caught.exception.reason, "identity_mismatch",
                         TAG_ALIAS + ": 登记 UID 与 token 冲突")
        with self.assertRaises(
                tsl.SourceIdentityIssue, msg=TAG_ALIAS) as caught:
            tsl.resolve_source_identity(
                task_dir=task,
                source=p06_source(ok_token, source_library_id=P06_LIB_A))
        self.assertEqual(caught.exception.reason, "identity_mismatch",
                         TAG_ALIAS + ": 登记 library 与 token 冲突")
        with self.assertRaises(
                tsl.SourceIdentityIssue, msg=TAG_ALIAS) as caught:
            tsl.resolve_source_identity(
                task_dir=task, source=p06_source(ok_token, generation=0))
        self.assertEqual(caught.exception.reason, "identity_mismatch",
                         TAG_ALIAS + ": generation 值域非法")
        with self.assertRaises(
                tsl.SourceIdentityIssue, msg=TAG_ALIAS) as caught:
            tsl.resolve_source_identity(
                task_dir=task, source=p06_source(ok_token, status="zombie"))
        self.assertEqual(caught.exception.reason, "identity_mismatch",
                         TAG_ALIAS + ": status 非法")
        # 声明通道正控：全部可选声明一致时照常定位。
        self.assertEqual(
            tsl.resolve_source_identity(
                task_dir=task,
                source=p06_source(ok_token, source_user_id=202,
                                  source_library_id=P06_LIB_B, generation=5,
                                  registration_id="registration-7",
                                  status="active")),
            self._real(alias_file), TAG_ALIAS + ": 一致声明正控")
        # library_unverifiable 邻接族（身份头不可读 ≠ 身份不符）。
        empty_lib = task / "users" / "u203" / "library.db"
        empty_lib.parent.mkdir(parents=True)
        sqlite3.connect(str(empty_lib)).close()  # 空文件 → P04 侧 unmigrated
        with self.assertRaises(
                tsl.SourceIdentityIssue, msg=TAG_ALIAS) as caught:
            tsl.resolve_source_identity(
                task_dir=task,
                source=p06_source(p06_canonical_token(
                    relative="users/u203/library.db", source_user_id=203,
                    source_library_id=P06_LIB_A)))
        self.assertEqual(caught.exception.reason, "library_unverifiable",
                         TAG_ALIAS + ": 空文件 unmigrated")
        text_lib = task / "users" / "u204" / "library.db"
        text_lib.parent.mkdir(parents=True)
        text_lib.write_bytes(b"not a sqlite database at all" * 8)
        with self.assertRaises(
                tsl.SourceIdentityIssue, msg=TAG_ALIAS) as caught:
            tsl.resolve_source_identity(
                task_dir=task,
                source=p06_source(p06_canonical_token(
                    relative="users/u204/library.db", source_user_id=204,
                    source_library_id=P06_LIB_A)))
        self.assertEqual(caught.exception.reason, "library_unverifiable",
                         TAG_ALIAS + ": 非 db 文本文件（读取失败通道）")
        # 优先序：文件不可读且声明也错 → 仍 library_unverifiable（读不到实际
        # 身份无从比对，不冒充 library_mismatch）。
        with self.assertRaises(
                tsl.SourceIdentityIssue, msg=TAG_ALIAS) as caught:
            tsl.resolve_source_identity(
                task_dir=task,
                source=p06_source(p06_canonical_token(
                    relative="users/u204/library.db", source_user_id=204,
                    source_library_id=P06_LIB_OTHER)))
        self.assertEqual(caught.exception.reason, "library_unverifiable",
                         TAG_ALIAS + ": 不可读先于比对")
        # 值级 meta_corrupt：canonical DDL + 非 hex 32 位 library_id。
        self._member_library(
            task / "users" / "u205", "library.db", "z" * 32)
        with self.assertRaises(
                tsl.SourceIdentityIssue, msg=TAG_ALIAS) as caught:
            tsl.resolve_source_identity(
                task_dir=task,
                source=p06_source(p06_canonical_token(
                    relative="users/u205/library.db", source_user_id=205,
                    source_library_id=P06_LIB_A)))
        self.assertEqual(caught.exception.reason, "library_unverifiable",
                         TAG_ALIAS + ": meta library_id 非 hex（meta_corrupt）")


class SameUuidDifferentFilesTests(SourceIdentityTestBase):
    """用例 4：零跨调用状态、inode 不参与、身份按组合判——同组合多文件不全局禁止。"""

    def test_same_uuid_different_files_not_globally_forbidden(self):
        task = self._task_dir()
        f1 = self._member_library(
            task / "members" / "u404" / "primary", "library.db", P06_LIB_SAME)
        f2 = self._member_library(
            task / "members" / "u404" / "backup", "library.db", P06_LIB_SAME)
        t1 = tsl.make_source_identity(
            task_dir=task, source_path=f1,
            source_user_id=404, source_library_id=P06_LIB_SAME)
        t2 = tsl.make_source_identity(
            task_dir=task, source_path=f2,
            source_user_id=404, source_library_id=P06_LIB_SAME)
        rel1 = json.loads(t1)["relative"]
        rel2 = json.loads(t2)["relative"]
        self.assertNotEqual(rel1, rel2, TAG_SAMEUUID)
        r1 = tsl.resolve_source_identity(task_dir=task, source=p06_source(t1))
        r2 = tsl.resolve_source_identity(task_dir=task, source=p06_source(t2))
        self.assertEqual(r1, self._real(f1), TAG_SAMEUUID)
        self.assertEqual(r2, self._real(f2), TAG_SAMEUUID)
        self.assertNotEqual(r1, r2,
                            TAG_SAMEUUID + ": 同 UID+同 library 组合出现在两个不同文件，不全局禁止")
        # inode 不参与判定：删除重建 f1（新 file index）→ 老 token 仍定位成功。
        inode_before = os.stat(f1).st_ino
        f1.unlink()
        distractor = f1.with_name("distractor.tmp")
        distractor.write_bytes(b"x")
        distractor.unlink()  # 夹具自证：扰动 MFT 记录位，确保重建确为新索引
        self._member_library(f1.parent, "library.db", P06_LIB_SAME)
        inode_after = os.stat(f1).st_ino
        self.assertNotEqual(inode_after, inode_before,
                            TAG_SAMEUUID + ": 重建确为新 file index（夹具自证）")
        r1b = tsl.resolve_source_identity(task_dir=task, source=p06_source(t1))
        self.assertEqual(r1b, r1, TAG_SAMEUUID + ": inode 不充当逻辑 UUID")
        # 互不影响：一次 library_mismatch 失败不毒化后续调用（零跨调用状态）。
        wrong_claim = p06_canonical_token(
            relative=rel1, source_user_id=404, source_library_id=P06_LIB_OTHER)
        with self.assertRaises(
                tsl.SourceIdentityIssue, msg=TAG_SAMEUUID) as caught:
            tsl.resolve_source_identity(
                task_dir=task, source=p06_source(wrong_claim))
        self.assertEqual(caught.exception.reason, "library_mismatch", TAG_SAMEUUID)
        self.assertEqual(
            tsl.resolve_source_identity(task_dir=task, source=p06_source(t1)),
            r1, TAG_SAMEUUID + ": 失败后 t1 仍成功")
        self.assertEqual(
            tsl.resolve_source_identity(task_dir=task, source=p06_source(t2)),
            r2, TAG_SAMEUUID + ": 失败后 t2 仍成功")
        for _ in range(3):
            self.assertEqual(
                tsl.resolve_source_identity(task_dir=task, source=p06_source(t1)),
                r1, TAG_SAMEUUID + ": 重复解析一致")
        # 同一 token 文本 + 另一 task_dir 的同 relative 同库副本 → 各自定位
        #（token 不内嵌 task 身份；跨任务唯一性由 P03 登记兜底）。
        task2 = self._task_dir("task2")
        f3 = self._member_library(
            task2 / "members" / "u404" / "primary", "library.db", P06_LIB_SAME)
        self.assertEqual(
            tsl.resolve_source_identity(task_dir=task2, source=p06_source(t1)),
            self._real(f3), TAG_SAMEUUID + ": 组合按 task_dir 分别判")
        # 复审边界：同物理文件被两 UID 以相同且正确的 library_id 声明 →
        # 物理层不可分辨（UID 分辨归 P03 登记唯一性）；本层钉“不全局禁止”。
        other_uid_token = p06_canonical_token(
            relative=rel1, source_user_id=505, source_library_id=P06_LIB_SAME)
        self.assertEqual(
            tsl.resolve_source_identity(
                task_dir=task, source=p06_source(other_uid_token)),
            r1, TAG_SAMEUUID + ": 不按物理文件全局禁止 UID 组合")


class UnmappedLegacyIssueTests(SourceIdentityTestBase):
    """用例 5：不可映射 legacy token 保持 token_unparsed typed 上抛：不吞不跳过不隔离不猜。"""

    def test_unmapped_legacy_remains_issue(self):
        task = self._task_dir()
        legacy_lib = self._member_library(
            task / "legacy-slug", "library.db", P06_LIB_LEGACY)
        root_decoy = self._member_library(task, "old-server.db", P06_LIB_LEGACY)
        unicode_lib = self._member_library(
            task / "成员账号旧名", "library.db", P06_LIB_LEGACY)
        # 三重诱饵：username 可猜目录（legacy-slug）、token 原文同名根文件
        #（old-server.db）、legacy 裸路径恰指向真实库——正确实现一概不碰。
        p06_set_username_env(self, "legacy-slug")
        # --- A. source 缺 source_path_identity / 非文本 ---
        missing_sources = (
            {}, {"other": "x"}, {"source_path_identity": None},
            {"source_path_identity": ""}, {"source_path_identity": 12345},
        )
        for source in missing_sources:
            with self.subTest(source=repr(source)):
                with self.assertRaises(
                        tsl.SourceIdentityIssue, msg=TAG_LEGACY) as caught:
                    tsl.resolve_source_identity(task_dir=task, source=source)
                self.assertEqual(caught.exception.reason, "token_unparsed",
                                 TAG_LEGACY + " " + repr(source))
        # --- B. 非 JSON / 非 JSON 对象（legacy 裸路径在列）---
        legacy_texts = (
            "legacy-slug/library.db", "old-server.db", "{'version': 1}",
            "null", "[]", "42", '"token-text"', "not json at all",
        )
        for token in legacy_texts:
            with self.subTest(legacy=repr(token)):
                with self.assertRaises(
                        tsl.SourceIdentityIssue, msg=TAG_LEGACY) as caught:
                    tsl.resolve_source_identity(
                        task_dir=task, source=p06_source(token))
                self.assertEqual(caught.exception.reason, "token_unparsed",
                                 TAG_LEGACY + ": legacy 未映射保持 issue " + repr(token))
        # --- C. JSON 但非 canonical（复审矩阵：键序/重复键/NaN/转义/尾随空白全拒）---
        unicode_token = p06_canonical_token(
            relative="成员账号旧名/library.db", source_user_id=101,
            source_library_id=P06_LIB_LEGACY)
        payload = json.loads(unicode_token)
        noncanonical = (
            ("尾随换行", unicode_token + "\n"),
            ("尾随空白", unicode_token + " "),
            ("空白分隔", json.dumps(
                payload, ensure_ascii=False, sort_keys=True,
                separators=(", ", ": "))),
            ("键序", json.dumps(
                {"source_user_id": 101, "source_library_id": P06_LIB_LEGACY,
                 "relative": "成员账号旧名/library.db", "base": "task_dir",
                 "version": 1},
                ensure_ascii=False, separators=(",", ":"))),
            ("重复键",
             '{"base":"task_dir","base":"task_dir",'
             '"relative":"成员账号旧名/library.db","source_library_id":"'
             + P06_LIB_LEGACY + '","source_user_id":101,"version":1}'),
            ("NaN",
             '{"base":"task_dir","relative":"a.db","source_library_id":"'
             + P06_LIB_LEGACY + '","source_user_id":101,"version":NaN}'),
            ("ASCII 转义", json.dumps(
                payload, ensure_ascii=True, sort_keys=True,
                separators=(",", ":"))),
        )
        for label, token in noncanonical:
            with self.subTest(noncanonical=label):
                with self.assertRaises(
                        tsl.SourceIdentityIssue, msg=TAG_LEGACY) as caught:
                    tsl.resolve_source_identity(
                        task_dir=task, source=p06_source(token))
                self.assertEqual(caught.exception.reason, "token_unparsed",
                                 TAG_LEGACY + " " + label)
        # --- D. 不吞不隔离：全部失败后诱饵与真实库原位（未移动/改名/删除）---
        self.assertTrue(legacy_lib.is_file(),
                        TAG_LEGACY + ": 不隔离不移动真实库")
        self.assertTrue(root_decoy.is_file(), TAG_LEGACY + ": 根诱饵原位")
        self.assertTrue(unicode_lib.is_file(), TAG_LEGACY)
        # --- E. 成对正控：canonical（含 Unicode relative）→ 解析成功（家族边界）---
        good = p06_canonical_token(
            relative="legacy-slug/library.db", source_user_id=101,
            source_library_id=P06_LIB_LEGACY)
        self.assertEqual(
            tsl.resolve_source_identity(task_dir=task, source=p06_source(good)),
            self._real(legacy_lib), TAG_LEGACY + ": canonical 正控")
        self.assertEqual(
            tsl.resolve_source_identity(
                task_dir=task, source=p06_source(unicode_token)),
            self._real(unicode_lib),
            TAG_LEGACY + ": canonical Unicode relative 原文往返")
        # --- F. typed 通道词汇表钉 ---
        self.assertEqual(
            tsl.SourceIdentityIssue.REASONS, P06_EXPECTED_REASONS, TAG_LEGACY)
        with self.assertRaises(ValueError, msg=TAG_LEGACY):
            tsl.SourceIdentityIssue("no_such_reason")
        probe_issue = tsl.SourceIdentityIssue("token_unparsed", detail="d")
        self.assertEqual(probe_issue.params, {"reason": "token_unparsed"},
                         TAG_LEGACY + ": None 上下文键不进 params")
        self.assertEqual(str(probe_issue), "[token_unparsed] d", TAG_LEGACY)


if __name__ == "__main__":
    unittest.main()
