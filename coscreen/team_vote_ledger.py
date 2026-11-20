"""成员库持久票版本账本（team vote ledger，S1a；format 2＝守恒版）。

设计权威：out/maintenance216/team_flow_integration_design.md §11.1-11.2（DDL/
触发器/安装契约）、§12.4（派工签名）、§13.6（Sol 修订：结构验证/READY/
局部 catch/broken 不自修）；主控 brief
.superpowers/sdd/2026-11-14-2.1.6-maintenance/flow-ledger-s1a-brief.md；
反恢复守恒修复裁定 out/maintenance216/team_vote_ledger_conservation_ruling_sol.md
（主控已批准，仅本模块修复，db.py 保持首轮冻结 0ae14c66…，真实库 repair
不在范围）。

合同要点（不放宽）：
- 唯一 32hex library_id；stage/item_key 持久墓碑；两票表六触发器与业务
  语句同 SQLite 事务推进；同值重写也推进；DELETE 后再 INSERT 不重置；
  溢出（revision+1 或 advances+1 越界升 REAL）经 CHECK(typeof='integer')
  拒收——仅保证当前票语句及触发器失败，**调用方/业务窗口必须回滚整个
  业务事务**，不得把约束失败误称自动回滚、更不得捕错后提交已写部分。
- **守恒不变量（format 2）**：全部 stage、全部 ledger 行（含墓碑）
  revision 之和 == seeded_stage1 + seeded_stage2 + advances。每条触发器
  upsert 后 advances 同事务 +1（同 key UPDATE=NEW+OLD 共 +2）；触发器先
  验 meta 单例存在，缺失 RAISE(ABORT)。孤立行删除/降值使左值永久短缺，
  正常写两边同增、差额不消失——旧 library_id 不能经普通个人 SQL 自动
  恢复可信；不依赖"先读取发现"，墓碑被删亦覆盖。不证明协同篡改/等额
  补偿/整文件回滚（§11.9 边界如实）。
- **精确整数**：验证禁用 SQL SUM/total()/SQL 端加法（64 位溢出/REAL 失
  精不可证）；同 snapshot 流式 SELECT revision 逐值合法性检查，Python
  任意精度求和；meta 三 counter 逐个合法非负 SQLite 整数，右值也 Python
  相加。
- **验证入口统一**：ledger_state 的 installed 判定必须含守恒（否则
  db._connect 会误 READY）；library_id/revisions/vote_snapshot/install
  幂等返回前均须同 snapshot 可信验证；同一链共享内部结果避免同 snapshot
  重复全表扫描，不跨 snapshot 缓存。票→ledger JOIN 缺行检查保留，不以
  总和取代 key 存在性。
- format 1（仅 scratch 存在过、未发布）视为 unsupported/broken：不原 ID
  就地补 counter、不从当前和反推 seeded/advances 洗白。真正零 ledger 对象
  的旧普通票库合法安装 format 2。不提供真实库自动 repair/删除/身份轮换。
- 安装：单 BEGIN IMMEDIATE，逐条 execute 禁 executescript；锁内复查；建
  meta/revisions 两表 → seed 现有票 1（seed 不推进 advances）并取实际分
  stage 数 → 建六触发器 → 最后插唯一 meta（新 UUID/seeded 两数/
  advances=0）＝完成标记 → 同事务验证守恒 → commit；任一步失败整回滚无
  半装态；并发后来者等待后复查返回首个已提交 id。broken 不自动补齐、不
  沿用旧 library_id。两票表不齐 → 返回 None（不伪造身份）。调用方连接已
  有未结事务＝编程违约：不吞、不提交、不回滚调用方事务。
- 读快照所有权：验证链与票面+library_id+revision 同一 SQLite snapshot
  ——调用方已有事务则加入且不结束；无事务自建 BEGIN（DEFERRED）只读快照
  并仅清理自己。票行缺 ledger 行＝row_missing fail-closed，无默认
  revision 1，不把不可读当缺票。

本模块仅依赖标准库，不 import coscreen.db（避免环）；db.py 的
init_db/_connect 挂点与 S2 bootstrap（owner-only）是仅有的安装触达路径；
TSR/readonly 等只读面禁止调用 install。每连接完整验证为 O(ledger 总行数
含墓碑)（守恒裁定明示成本，正确性优先）。
"""

from __future__ import annotations

import logging
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, NamedTuple
from uuid import uuid4

LEDGER_FORMAT_VERSION = 2

REASONS = (
    "unmigrated",            # 库未安装账本（absent）
    "meta_corrupt",          # meta 表/行（含 format 不受支持/坏 counter）或 revisions 表结构不完整或不符合规范
    "conservation_mismatch", # 守恒不变量破坏（含墓碑行删除/revision 降值后经正常写不可洗白）
    "trigger_missing",       # 触发器缺名或定义与受支持规范不符
    "row_missing",           # 同快照内票行缺对应 ledger 行（含计数不变量被破坏）
    "overflow",              # 保留：写路径溢出失败的业务映射令牌（S2 写窗口用）
    "path_absent",           # install_in_db 目标库文件不存在（拒绝创建）
)

_STAGE_TABLES: dict[str, str] = {"stage1": "decisions", "stage2": "stage2_decisions"}

_META_TABLE = "team_vote_revision_meta"
_REVISIONS_TABLE = "team_vote_revisions"

# 受支持规范 DDL（format 2；安装与验证同一来源。安装位于 BEGIN IMMEDIATE
# 锁内且已复查 absent，故不写 IF NOT EXISTS——sqlite_master 保存的语句文本
# 与下列规范逐字一致，验证按空白归一化比对）。
_META_DDL = (
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
_REVISIONS_DDL = (
    "CREATE TABLE team_vote_revisions (\n"
    "  stage TEXT NOT NULL CHECK(stage IN ('stage1','stage2')),\n"
    "  item_key TEXT NOT NULL,\n"
    "  revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision>=1),\n"
    "  PRIMARY KEY(stage, item_key)\n"
    ") WITHOUT ROWID"
)

# 触发器公共片段：meta 单例缺失即 ABORT（防 counter UPDATE 零命中后继续无
# 完整记账——完整性检查，不新增业务资格）；每条 ledger upsert 后 advances
# 同事务 +1。
_META_GUARD = (
    "SELECT RAISE(ABORT, 'team_vote_revision_meta singleton missing') "
    "WHERE NOT EXISTS (SELECT 1 FROM team_vote_revision_meta WHERE singleton=1)"
)
_ADVANCE = (
    "UPDATE team_vote_revision_meta SET advances=advances+1 WHERE singleton=1"
)


def _upsert(stage: str, key: str) -> str:
    return (
        "INSERT INTO team_vote_revisions(stage,item_key,revision) "
        f"VALUES('{stage}', {key}, 1) "
        "ON CONFLICT(stage,item_key) DO UPDATE SET revision=revision+1"
    )


def _trigger_ddl(table: str, stage: str) -> dict[str, str]:
    """六触发器中作用于单表的三条（§11.2 语义＋守恒 counter；UPDATE=NEW+OLD）。"""
    new_upsert = _upsert(stage, "NEW.zotero_key") + ";"
    old_upsert = _upsert(stage, "OLD.zotero_key") + ";"
    return {
        f"{table}_tvr_insert":
            f"CREATE TRIGGER {table}_tvr_insert AFTER INSERT ON {table} BEGIN "
            + _META_GUARD + "; " + new_upsert + " " + _ADVANCE + "; END",
        f"{table}_tvr_update":
            f"CREATE TRIGGER {table}_tvr_update AFTER UPDATE ON {table} BEGIN "
            + _META_GUARD + "; " + new_upsert + " " + _ADVANCE + "; "
            + old_upsert + " " + _ADVANCE + "; END",
        f"{table}_tvr_delete":
            f"CREATE TRIGGER {table}_tvr_delete AFTER DELETE ON {table} BEGIN "
            + _META_GUARD + "; " + old_upsert + " " + _ADVANCE + "; END",
    }


_TRIGGERS: dict[str, str] = {}
for _stage, _table in _STAGE_TABLES.items():
    _TRIGGERS.update(_trigger_ddl(_table, _stage))
_TRIGGER_ORDER = tuple(_TRIGGERS)  # decisions 三条在前，stage2 三条在后

_ALL_OBJECTS: tuple[str, ...] = (_META_TABLE, _REVISIONS_TABLE, *_TRIGGERS)

_LIBRARY_ID_RE = re.compile(r"[0-9a-f]{32}\Z")

# 流式守恒扫描批量（不一次载全表；内存与正确性优先于微优化）
_SCAN_BATCH = 512

_BROKEN_NOTED: set[Path] = set()

_INT64_MAX = 2 ** 63 - 1


class VoteLedgerUnavailable(RuntimeError):
    """成员库票版本账本不可用（fail-closed；HTTP 层映射 503 结构化载荷）。

    reason 取值见 :data:`REASONS`；code/message_key 恒为
    team.flow.revision_unavailable（不随 reason 变化）；params 供响应结构化。
    """

    status = 503
    code = "team.flow.revision_unavailable"
    message_key = "team.flow.revision_unavailable"

    def __init__(self, reason: str, *, member: str | None = None, detail: str = ""):
        if reason not in REASONS:
            raise ValueError(f"未知 reason: {reason!r}，必须为 {REASONS} 之一")
        super().__init__(f"[{reason}] {detail}" if detail else f"[{reason}]")
        self.reason = reason
        self.member = member
        self.detail = detail

    @property
    def params(self) -> dict[str, object]:
        return {"member": self.member, "reason": self.reason}


def _norm(sql: str | None) -> str:
    return " ".join((sql or "").split())


def _is_valid_library_id(value: object) -> bool:
    return isinstance(value, str) and _LIBRARY_ID_RE.fullmatch(value) is not None


def note_broken_once(path: object) -> None:
    """每进程每路径只记一次 broken 警告（供 db.py 挂点调用，避免每连接刷屏）。"""
    try:
        key = Path(path).resolve()
    except (OSError, ValueError, TypeError):
        key = Path(str(path))
    if key in _BROKEN_NOTED:
        return
    _BROKEN_NOTED.add(key)
    logging.getLogger(__name__).warning(
        "team_vote_ledger: %s 的账本结构为 broken：不自动修复、不沿用旧 "
        "library_id（此前可能存在未记账变更）；个人用途继续，team 可信消费"
        "保持 fail-closed 拒绝，修复需独立授权+备份+轮换 ID。", key)


def both_decision_tables_present(conn: sqlite3.Connection) -> bool:
    """两票表（decisions ∧ stage2_decisions）是否均已存在（纯读，不建表）。"""
    for table in _STAGE_TABLES.values():
        if not conn.execute(f'PRAGMA table_info("{table}")').fetchall():
            return False
    return True


class _Validation(NamedTuple):
    """同一次 snapshot 内的单遍验证结果（供同链复用，不跨 snapshot 缓存）。"""

    state: str              # 'absent' | 'installed' | 'broken'
    library_id: str | None  # installed 时有效
    reason: str | None      # broken 时的细分 reason（unmigrated 在 absent 时给出）
    detail: str


def _objects(conn: sqlite3.Connection) -> dict[str, str | None]:
    placeholders = ",".join("?" * len(_ALL_OBJECTS))
    rows = conn.execute(
        f"SELECT name, sql FROM sqlite_master WHERE name IN ({placeholders})",
        _ALL_OBJECTS,
    ).fetchall()
    return {name: sql for name, sql in rows}


def _validate_revision_rows(conn: sqlite3.Connection) -> tuple[int, str | None]:
    """流式扫描全部 ledger 行：逐值合法（int≥1、stage 受支持），Python 精确求和。

    返回 (sum, 细分说明)；不合法返回 (0, 说明)。禁 SQL SUM/total()（64 位
    溢出/REAL 失真不可证——SQLite SUM(integer) 可能 64 位溢出，total() 必
    失真）。
    """
    total = 0
    cursor = conn.execute(
        "SELECT stage, revision FROM team_vote_revisions")
    while True:
        batch = cursor.fetchmany(_SCAN_BATCH)
        if not batch:
            break
        for stage, revision in batch:
            if stage not in _STAGE_TABLES:
                return 0, f"非法 stage 行: {stage!r}"
            if not isinstance(revision, int) or isinstance(revision, bool) \
                    or revision < 1:
                return 0, f"非法 revision 行(stage={stage}): {revision!r}"
            total += revision
    return total, None


def _validate_meta_counters(
    seeded_stage1: object, seeded_stage2: object, advances: object,
) -> str | None:
    """meta 三 counter 逐个合法非负 SQLite 整数（右值由调用方 Python 相加）。"""
    for name, value in (("seeded_stage1", seeded_stage1),
                        ("seeded_stage2", seeded_stage2),
                        ("advances", advances)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0 \
                or value > _INT64_MAX:
            return f"meta counter {name} 非法: {value!r}"
    return None


def _validated_state(conn: sqlite3.Connection) -> _Validation:
    """单遍结构+meta+counter+守恒验证（须在调用方管理的 snapshot 内执行）。

    installed 判定包含守恒（否则 db._connect 会误 READY）。任何部分对象存
    在而不完整、定义不符、format 不受支持（含 format 1）、counter 坏、守
    恒破坏均＝broken；零对象＝absent。
    """
    objects = _objects(conn)
    present = [name for name in _ALL_OBJECTS if name in objects]
    if not present:
        return _Validation("absent", None, "unmigrated", "账本未安装（absent）")
    # 触发器缺名或定义不符优先归 trigger_missing（含部分对象场景），其后才
    # 是表级 meta_corrupt；所有路径一律 broken，不因 reason 优先级变 installed。
    wrong_triggers = [
        name for name, ddl in _TRIGGERS.items()
        if name not in objects or _norm(objects.get(name)) != _norm(ddl)
    ]
    if wrong_triggers:
        return _Validation("broken", None, "trigger_missing",
                           "触发器缺失或定义不符: " + ",".join(sorted(wrong_triggers)))
    if len(present) != len(_ALL_OBJECTS):
        missing = [n for n in _ALL_OBJECTS if n not in objects]
        return _Validation("broken", None, "meta_corrupt",
                           "账本对象不完整（部分存在）: " + ",".join(missing))
    if _norm(objects.get(_META_TABLE)) != _norm(_META_DDL) \
            or _norm(objects.get(_REVISIONS_TABLE)) != _norm(_REVISIONS_DDL):
        return _Validation("broken", None, "meta_corrupt",
                           "meta/revisions 表结构定义不符（或 format 不受支持）")
    rows = conn.execute(
        "SELECT singleton, format_version, library_id,"
        " seeded_stage1, seeded_stage2, advances"
        " FROM team_vote_revision_meta").fetchall()
    if len(rows) != 1:
        return _Validation("broken", None, "meta_corrupt",
                           f"meta 行数非法: {len(rows)}")
    singleton, format_version, library_id, s1, s2, adv = rows[0]
    if singleton != 1 or format_version != LEDGER_FORMAT_VERSION \
            or not _is_valid_library_id(library_id):
        return _Validation("broken", None, "meta_corrupt",
                           f"meta 行非法（format={format_version!r}，受支持为 "
                           f"{LEDGER_FORMAT_VERSION}；format 1 不就地升级/不洗白）")
    bad_counter = _validate_meta_counters(s1, s2, adv)
    if bad_counter:
        return _Validation("broken", None, "meta_corrupt", bad_counter)
    total, bad_row = _validate_revision_rows(conn)
    if bad_row:
        return _Validation("broken", None, "conservation_mismatch", bad_row)
    # 守恒：左值 Python 任意精度流式和；右值同样 Python 相加（禁 SQL 加法）
    rhs = int(s1) + int(s2) + int(adv)
    if total != rhs:
        return _Validation("broken", None, "conservation_mismatch",
                           f"守恒破坏：SUM(revision)={total} != "
                           f"seeded({int(s1)}+{int(s2)})+advances({int(adv)})={rhs}")
    return _Validation("installed", str(library_id), None, "")


@contextmanager
def _read_snapshot(conn: sqlite3.Connection) -> Iterator[None]:
    """快照所有权：调用方已有事务则加入且不结束；否则自建只读快照仅清理自己。"""
    own = not conn.in_transaction
    if own:
        conn.execute("BEGIN")
    try:
        yield
    finally:
        if own and conn.in_transaction:
            conn.execute("ROLLBACK")


def _state_checked(conn: sqlite3.Connection) -> _Validation:
    """在受管 snapshot 内做完整验证并对 broken 生成结构化异常。"""
    with _read_snapshot(conn):
        result = _validated_state(conn)
    if result.state == "installed":
        return result
    raise VoteLedgerUnavailable(
        result.reason or "meta_corrupt", detail=result.detail or "账本不可用")


def ledger_state(conn: sqlite3.Connection) -> str:
    """本次连接上的账本状态：'absent' | 'installed' | 'broken'。

    installed 判定含守恒（O(ledger 总行数含墓碑)，守恒裁定明示成本）。
    """
    with _read_snapshot(conn):
        return _validated_state(conn).state


def library_id(conn: sqlite3.Connection) -> str:
    """当前库的 library_id（幂等可信验证后返回）；未装/损坏抛结构化异常。"""
    return _state_checked(conn).library_id  # type: ignore[return-value]


def _stage_table(stage: str) -> str:
    try:
        return _STAGE_TABLES[stage]
    except KeyError:
        raise ValueError(
            f"未知 stage: {stage!r}，必须为 {tuple(_STAGE_TABLES)} 之一") from None


def install(conn: sqlite3.Connection) -> str | None:
    """安装账本并返回 library_id；两票表不齐返回 None（absent 无操作）。

    契约：调用方连接不得持有未结事务（编程违约→RuntimeError，不吞不提交
    不回滚调用方事务）；单 BEGIN IMMEDIATE；逐条 conn.execute DDL、禁
    executescript（隐式提交）；锁内复查；顺序＝建 meta/revisions 表 →
    seed 现有票 1（不推进 advances）并取实际分 stage 数 → 建六触发器 →
    最后插唯一 meta（新 UUID/seeded 两数/advances=0）＝完成标记 → **同事务
    验证守恒** → commit；任一步失败整回滚无半装态；并发后来者等待后复查
    见 meta → 返回首个已提交的 library_id；已装幂等返回现值（幂等返回前
    同样完整可信验证）；broken（含 format 1）不自动补齐、不原 ID 升级。
    事务外预检不替代锁内复查。
    """
    if conn.in_transaction:
        raise RuntimeError(
            "team_vote_ledger.install: 调用方连接存在未结事务（编程违约）；"
            "install 不提交、不回滚、不吞掉调用方事务")
    # 事务外预检（幂等/拒绝快速路径；非锁内复查的替代）。absent 继续走安装。
    with _read_snapshot(conn):
        pre = _validated_state(conn)
    if pre.state == "installed":
        return pre.library_id
    if pre.state == "broken":
        raise VoteLedgerUnavailable(
            pre.reason or "meta_corrupt", detail=pre.detail or "账本 broken，拒绝自动补齐")
    # absent：两票表齐才装（缺表＝None，不伪造身份、不建库）
    if not both_decision_tables_present(conn):
        return None
    conn.execute("BEGIN IMMEDIATE")
    try:
        # 锁内复查：并发先装者已提交 → 幂等返回首个已提交 id
        locked = _validated_state(conn)  # 已在 BEGIN IMMEDIATE 事务内（加入）
        if locked.state == "installed":
            conn.execute("ROLLBACK")  # 本事务未写入，释放保留
            return locked.library_id
        if locked.state == "broken":
            raise VoteLedgerUnavailable(
                locked.reason or "meta_corrupt",
                detail=locked.detail or "账本 broken，拒绝自动补齐")
        if not both_decision_tables_present(conn):
            conn.execute("ROLLBACK")
            return None
        conn.execute(_META_DDL)
        conn.execute(_REVISIONS_DDL)
        for stage, table in _STAGE_TABLES.items():
            conn.execute(
                "INSERT OR IGNORE INTO team_vote_revisions(stage,item_key,revision) "
                f"SELECT ?, zotero_key, 1 FROM {table}",
                (stage,),
            )
        # 实际分 stage 数（行计数；seed 与计数同锁状态，seed 不推进 advances）
        seeded = {
            stage: int(conn.execute(
                "SELECT COUNT(*) FROM team_vote_revisions WHERE stage=?",
                (stage,)).fetchone()[0])
            for stage in _STAGE_TABLES
        }
        for name in _TRIGGER_ORDER:
            conn.execute(_TRIGGERS[name])
        library_id = uuid4().hex
        conn.execute(
            "INSERT INTO team_vote_revision_meta("
            "singleton, format_version, library_id,"
            " seeded_stage1, seeded_stage2, advances) VALUES(1, ?, ?, ?, ?, 0)",
            (LEDGER_FORMAT_VERSION, library_id,
             seeded["stage1"], seeded["stage2"]))
        # 同事务验证守恒（失败→整回滚，无半装态）
        verified = _validated_state(conn)
        if verified.state != "installed":
            raise VoteLedgerUnavailable(
                verified.reason or "meta_corrupt",
                detail="安装事务内守恒验证失败: " + (verified.detail or ""))
        conn.execute("COMMIT")
        return library_id
    except BaseException:
        if conn.in_transaction:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass  # 连接已坏时尽力而为；原始异常优先上抛
        raise


def install_in_db(db_path: object) -> str | None:
    """对现存库文件自开短连接（mode=rw，绝不创建）调用 :func:`install`。

    init_db 挂点与 S2 owner bootstrap 共用；文件不存在 →
    VoteLedgerUnavailable(path_absent)。返回值语义同 :func:`install`。
    """
    path = Path(db_path)
    if not path.is_file():
        raise VoteLedgerUnavailable(
            "path_absent", detail=f"库文件不存在，拒绝创建：{path}")
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True, timeout=15.0)
    try:
        return install(conn)
    finally:
        conn.close()


def revisions(conn: sqlite3.Connection, stage: str) -> dict[str, int]:
    """某 stage 的 {item_key: revision} 只读快照（同 snapshot 可信验证后读取）。"""
    _stage_table(stage)  # 校验 stage
    with _read_snapshot(conn):
        _state_checked(conn)  # 同 snapshot 完整可信验证（含守恒）
        rows = conn.execute(
            "SELECT item_key, revision FROM team_vote_revisions WHERE stage=?",
            (stage,),
        ).fetchall()
        return {item_key: int(revision) for item_key, revision in rows}


def vote_snapshot(
    conn: sqlite3.Connection, stage: str,
) -> tuple[str, dict[str, dict]]:
    """(library_id, {zotero_key: 票行+revision})，同一 SQLite 读快照。

    快照所有权：调用方已有事务则加入且不结束；无事务自建只读快照仅清理
    自己。守恒/结构验证与票面+library_id+revision 同 snapshot（同链单遍
    验证复用，不重复全表扫描）。票行缺 ledger 行＝row_missing fail-closed；
    计数不变量（ledger 行数≥现存票行数）保留，不以总和取代 key 存在性。
    """
    table = _stage_table(stage)
    with _read_snapshot(conn):
        # JOIN 缺行检查先行（裁定项 1 的可观察行为：删行→row_missing；不以
        # 总和取代 key 存在性）。表缺失等结构性 sqlite 错误不让其裸抛——
        # 落回完整验证，由其给出结构化 reason。
        join_error: sqlite3.Error | None = None
        rows: list = []
        try:
            rows = conn.execute(
                f"SELECT d.zotero_key, d.decision, d.exclusion_reason, d.notes, "
                f"d.tags, d.screened_at, d.updated_at, r.revision "
                f"FROM {table} d LEFT JOIN team_vote_revisions r "
                f"ON r.stage=? AND r.item_key=d.zotero_key",
                (stage,),
            ).fetchall()
        except sqlite3.Error as exc:
            join_error = exc
        if join_error is None:
            missing = [row[0] for row in rows if row[7] is None]
            if missing:
                raise VoteLedgerUnavailable(
                    "row_missing",
                    detail=f"票行缺 ledger 行（前若干键）: {missing[:5]}")
        checked = _state_checked(conn)  # 单遍：结构+meta+counter+守恒
        if join_error is not None:
            raise join_error  # 结构验证却通过（非预期）→ 保留原始错误上抛
        ledger_count = conn.execute(
            "SELECT COUNT(*) FROM team_vote_revisions WHERE stage=?", (stage,),
        ).fetchone()[0]
        if ledger_count < len(rows):
            raise VoteLedgerUnavailable(
                "row_missing",
                detail=f"计数不变量破坏：ledger {ledger_count} 行 < 现存票 {len(rows)} 行")
        mapping = {
            row[0]: {
                "zotero_key": row[0],
                "decision": row[1],
                "exclusion_reason": row[2] or "",
                "notes": row[3] or "",
                "tags": row[4] or "",
                "screened_at": row[5],
                "updated_at": row[6],
                "revision": int(row[7]),
            }
            for row in rows
        }
        return checked.library_id, mapping


# ---------------------------------------------------------------------------
# P04 窄读接口（C04/C02 借用合同；设计参照 keyed_ledger_plan/PLAN_CC.txt 中
# 被 C02/C04 统一后的部分：不采用其自有 BEGIN/rollback 快照与"重复键拒绝"
# 分叉——本节遵循 C02 借用连接合同与 P00 normalize 去重语义）。
# ---------------------------------------------------------------------------

KEYED_VOTE_ISSUE_REASONS = ("row_missing", "unreadable")


class LedgerIdentityHeader(NamedTuple):
    """账本身份头（仅 meta schema/单例/format/32hex；不含任何健康声明）。"""

    library_id: str
    format_version: int


class KeyedVoteIssue(NamedTuple):
    """单键读取问题（reason 取 KEYED_VOTE_ISSUE_REASONS 之一；detail 不含原始字节）。"""

    reason: str
    detail: str = ""


class KeyedVoteSnapshot(NamedTuple):
    """按键票面快照：每个请求键恰落入 rows/absent_keys/issues 之一（互斥并集＝请求域）。"""

    stage: str
    library_id: str
    requested_keys: tuple[str, ...]
    rows: dict[str, dict]
    absent_keys: tuple[str, ...]
    issues: dict[str, KeyedVoteIssue]


def _require_borrowed_ledger_connection(
    conn: sqlite3.Connection, *, api: str,
) -> None:
    """C02 借用合同：真 native 连接/真实子类（拒 __class__ 伪装代理）、活动事务、Row 工厂。

    检查顺序固定：类型 → in_transaction（closed 连接在此抛出的
    sqlite3.ProgrammingError 原样透传，不包装）→ row_factory。本节所有 ``_on``
    接口均不 BEGIN/SAVEPOINT/commit/rollback/close 调用方连接，不改
    PRAGMA/factory，不重连/迁移。
    """
    if not issubclass(type(conn), sqlite3.Connection):
        raise TypeError(
            f"{api} requires a native sqlite3.Connection or native subclass")
    if not conn.in_transaction:
        raise RuntimeError(f"{api} requires an active caller transaction")
    if conn.row_factory is not sqlite3.Row:
        raise RuntimeError(f"{api} requires conn.row_factory is sqlite3.Row")


def _normalize_keyed_item_keys(item_keys: Iterable[str]) -> tuple[str, ...]:
    """C02 规范键域：拒 bare str/bytes、非 str、空串、NUL、非法 Unicode；去重保首现顺序。

    语义与 P00 ``custom_backend.team_keyed_domain.normalize_item_keys`` 逐条
    一致（含异常类型 ValueError）；coscreen 低层模块不反向 import backend，
    故按同一合同在此镜像。空集合拒绝——绝不把空请求当作"全部键"。
    """
    if isinstance(item_keys, (str, bytes)):
        raise ValueError("item_keys must be an iterable of strings, not str/bytes")
    ordered: list[str] = []
    seen: set[str] = set()
    try:
        iterator = iter(item_keys)
    except TypeError as exc:
        raise ValueError("item_keys must be an iterable of strings") from exc
    for key in iterator:
        if not isinstance(key, str):
            raise ValueError("each item key must be a string")
        if not key:
            raise ValueError("item keys must not be empty")
        if "\x00" in key:
            raise ValueError("item keys must not contain NUL")
        try:
            key.encode("utf-8", "strict")
        except UnicodeEncodeError as exc:
            raise ValueError("item keys must contain valid Unicode") from exc
        if key not in seen:
            seen.add(key)
            ordered.append(key)
    if not ordered:
        raise ValueError("item_keys must not be empty")
    return tuple(ordered)


def _identity_header_checked(conn: sqlite3.Connection) -> LedgerIdentityHeader:
    """身份头校验：仅 meta 表 canonical DDL、单例行、format2、32hex library_id。

    刻意不读 revisions 表体/行、票面、触发器 DDL 与 meta 三 counter（revisions
    表仅查名字以区分 absent/部分安装），不运行守恒或 installed 判定。
    """
    cursor = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE name IN (?, ?)",
        (_META_TABLE, _REVISIONS_TABLE),
    )
    try:
        objects = cursor.fetchall()
    finally:
        cursor.close()
    schema = {name: sql for name, sql in objects}
    if _META_TABLE not in schema:
        if _REVISIONS_TABLE in schema:
            raise VoteLedgerUnavailable(
                "meta_corrupt",
                detail="meta 表缺失而 revisions 表存在（部分账本对象）")
        raise VoteLedgerUnavailable("unmigrated", detail="账本未安装（absent）")
    if _norm(schema.get(_META_TABLE)) != _norm(_META_DDL):
        raise VoteLedgerUnavailable(
            "meta_corrupt", detail="meta 表结构定义不符（或 format 不受支持）")
    cursor = conn.execute(
        "SELECT singleton, format_version, library_id FROM team_vote_revision_meta")
    try:
        meta_rows = cursor.fetchall()
    finally:
        cursor.close()
    if len(meta_rows) != 1:
        raise VoteLedgerUnavailable(
            "meta_corrupt", detail=f"meta 行数非法: {len(meta_rows)}")
    singleton, format_version, library_id = meta_rows[0]
    if not isinstance(singleton, int) or isinstance(singleton, bool) \
            or singleton != 1:
        raise VoteLedgerUnavailable(
            "meta_corrupt", detail=f"meta singleton 非法: {singleton!r}")
    if not isinstance(format_version, int) or isinstance(format_version, bool) \
            or format_version != LEDGER_FORMAT_VERSION:
        raise VoteLedgerUnavailable(
            "meta_corrupt",
            detail=f"meta format_version 非法: {format_version!r}（受支持为 "
                   f"{LEDGER_FORMAT_VERSION}）")
    if not _is_valid_library_id(library_id):
        raise VoteLedgerUnavailable(
            "meta_corrupt", detail=f"meta library_id 非 32 位小写 hex: {library_id!r}")
    return LedgerIdentityHeader(
        library_id=str(library_id), format_version=int(format_version))


def library_identity_header_on(conn: sqlite3.Connection) -> LedgerIdentityHeader:
    """窄身份头：canonical meta/单例/format2/32hex；不读 revisions/票面/触发器/counter。

    已完成来源（F1 seal 自含）的身份确认不依赖 live 账本健康：revisions 行/
    守恒/触发器/counter 损坏不影响本接口，live 键读取仍由
    :func:`vote_snapshot_keys_on` 的全局校验拦截。不调用强
    library_id/ledger_state/vote_snapshot，不声称 installed/ready，不安装/
    修复/迁移/轮换 ID；对刻意复制的 meta 行不作密码学证明（与 S1a 同一边界）。

    失败分类：meta+revisions 表均缺 → VoteLedgerUnavailable('unmigrated')；
    meta 缺而 revisions 在（部分对象）、meta DDL 不符、行数≠1、
    singleton/format_version/library_id 非法 →
    VoteLedgerUnavailable('meta_corrupt')；其余 sqlite3.Error 原样上抛（源级）。
    借用合同见 :func:`_require_borrowed_ledger_connection`。
    """
    _require_borrowed_ledger_connection(conn, api="library_identity_header_on")
    return _identity_header_checked(conn)


def _decode_vote_text(stored_type: object, value: object, field: str) -> str | None:
    """严格解码单列票面文本（SQL 端 CAST(... AS BLOB) 取回，Python 端 strict UTF-8）。

    stored_type 为 SQL ``typeof()`` 结果：仅接受 'text'（'null' → None）；
    integer/real/blob 存储类型或非法 UTF-8 字节一律 ValueError，由调用方转为
    该键的 unreadable issue（detail 不含原始字节）。
    """
    if stored_type == "null":
        return None
    if stored_type != "text" or not isinstance(value, bytes):
        raise ValueError(f"{field} 存储类型非法: {stored_type!r}")
    try:
        return value.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{field} 无法严格解码为 UTF-8") from exc


def _vote_row_keys_on(
    conn: sqlite3.Connection, table: str, stage: str, key: str,
) -> sqlite3.Row | None:
    """按请求主键取单行票面+revision（CAST BLOB 避免取回期解码失败；只读本键）。

    自建 Cursor 完整物化（fetchone）并确定性关闭；不读取任何未请求键的行。
    """
    cursor = conn.execute(
        "SELECT typeof(d.zotero_key) AS key_type,"
        " CAST(d.zotero_key AS BLOB) AS key_bytes,"
        " typeof(d.decision) AS decision_type,"
        " CAST(d.decision AS BLOB) AS decision_bytes,"
        " typeof(d.exclusion_reason) AS exclusion_type,"
        " CAST(d.exclusion_reason AS BLOB) AS exclusion_bytes,"
        " typeof(d.notes) AS notes_type,"
        " CAST(d.notes AS BLOB) AS notes_bytes,"
        " typeof(d.tags) AS tags_type,"
        " CAST(d.tags AS BLOB) AS tags_bytes,"
        " typeof(d.screened_at) AS screened_type,"
        " CAST(d.screened_at AS BLOB) AS screened_bytes,"
        " typeof(d.updated_at) AS updated_type,"
        " CAST(d.updated_at AS BLOB) AS updated_bytes,"
        " r.revision AS revision "
        f"FROM {table} d LEFT JOIN team_vote_revisions r "
        "ON r.stage=? AND r.item_key=d.zotero_key "
        "WHERE d.zotero_key=?",
        (stage, key),
    )
    try:
        return cursor.fetchone()
    finally:
        cursor.close()


def vote_snapshot_keys_on(
    conn: sqlite3.Connection, stage: str,
    item_keys: Iterable[str],
) -> KeyedVoteSnapshot:
    """按键票面窄读：一次全局守恒校验 + 逐请求主键读票面与 revision。

    每次调用恰好执行一次 :func:`_state_checked`（结构/meta/counter/触发器/
    守恒；借用调用方活动事务，``_read_snapshot`` 在已有事务内不
    BEGIN/ROLLBACK），随后仅按请求主键逐键取票（``WHERE d.zotero_key=?``，
    不读任何未请求键）。不逐键重复全局校验、不建跨调用隐藏验证缓存、不调用
    强 library_id/revisions/vote_snapshot。

    每个请求键恰落入 rows/absent_keys/issues 之一：无票行＝absent（墓碑不是
    "从未投票"证明）；有票缺 revision 行＝row_missing（优先于 unreadable）；
    票面文本经 ``typeof`` 严格校验存储类型并以 CAST(... AS BLOB) 取回后
    strict UTF-8 解码，存储类型非法或解码失败＝unreadable。rows 行字典形状
    与旧 :func:`vote_snapshot` 完全一致（decision 保留 None；三个自由文本列
    NULL→''；两时间列保留 None；revision int>=1）。

    全局失败（含守恒破坏/counter 坏/触发器缺）＝VoteLedgerUnavailable 源级
    typed 失败，不返回伪空 DTO；逐键 SELECT 自身的 sqlite3.Error 原样上抛
    （ProgrammingError 最先透传）。借用合同见
    :func:`_require_borrowed_ledger_connection`。
    """
    table = _stage_table(stage)
    keys = _normalize_keyed_item_keys(item_keys)
    _require_borrowed_ledger_connection(conn, api="vote_snapshot_keys_on")
    checked = _state_checked(conn)
    rows: dict[str, dict] = {}
    absent: list[str] = []
    issues: dict[str, KeyedVoteIssue] = {}
    for key in keys:
        raw = _vote_row_keys_on(conn, table, stage, key)
        if raw is None:
            absent.append(key)
            continue
        revision = raw["revision"]
        if revision is None:
            issues[key] = KeyedVoteIssue(
                "row_missing", "票行缺对应 ledger 行")
            continue
        if not isinstance(revision, int) or isinstance(revision, bool) \
                or revision < 1:
            issues[key] = KeyedVoteIssue("unreadable", f"revision 非法: {revision!r}")
            continue
        try:
            row_key = _decode_vote_text(
                raw["key_type"], raw["key_bytes"], "zotero_key")
            if row_key != key:
                raise ValueError("zotero_key 解码后与请求键不一致")
            decision = _decode_vote_text(
                raw["decision_type"], raw["decision_bytes"], "decision")
            exclusion_reason = _decode_vote_text(
                raw["exclusion_type"], raw["exclusion_bytes"], "exclusion_reason")
            notes = _decode_vote_text(
                raw["notes_type"], raw["notes_bytes"], "notes")
            tags = _decode_vote_text(
                raw["tags_type"], raw["tags_bytes"], "tags")
            screened_at = _decode_vote_text(
                raw["screened_type"], raw["screened_bytes"], "screened_at")
            updated_at = _decode_vote_text(
                raw["updated_type"], raw["updated_bytes"], "updated_at")
        except ValueError as exc:
            issues[key] = KeyedVoteIssue("unreadable", str(exc))
            continue
        rows[key] = {
            "zotero_key": row_key,
            "decision": decision,
            "exclusion_reason": exclusion_reason or "",
            "notes": notes or "",
            "tags": tags or "",
            "screened_at": screened_at,
            "updated_at": updated_at,
            "revision": int(revision),
        }
    return KeyedVoteSnapshot(
        stage=stage,
        library_id=checked.library_id,  # type: ignore[arg-type]  # installed 必非 None
        requested_keys=keys,
        rows=rows,
        absent_keys=tuple(absent),
        issues=issues,
    )
