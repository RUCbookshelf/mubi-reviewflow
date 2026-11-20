# -*- coding: utf-8 -*-
"""2.1.6 S1a 持久版本账本（team_vote_ledger + db.py 两挂点）独立回归 —— 新增独立测试文件。

合同依据（全部只读引用，本文不编辑任何合同文件）：
- `.superpowers/sdd/2026-11-14-2.1.6-maintenance/flow-ledger-s1a-tests-brief.md`
  （RELEASED_TEST_PREPARATION，本文件唯一授权交付）
- `.superpowers/sdd/2026-11-14-2.1.6-maintenance/flow-ledger-s1a-brief.md`
  （RELEASED_PHASE2_IMPLEMENTATION，含 PHASE2 接口/验证补充）
- `out/maintenance216/s1a_vote_ledger/s1a_phase1_freeze.md` §6（八 API 冻结稿 +
  db.py 两挂点冻结 + DDL 冻结）
- `out/maintenance216/team_flow_integration_design.md` §11.1/11.2（DDL/安装事务）、
  §12.4（S1a 测试接口九项）、§13.6/Sol 裁定（结构验证/broken 不自修/局部兼容捕获）
- `out/maintenance216/team_vote_ledger_conservation_ruling_sol.md`（2026-11-15 守恒
  修复裁定 = S1a 最新合同，覆盖旧 format1：format2 + meta seeded_stage1/seeded_stage2/
  advances 非负整数控 + Σ全部revision(含墓碑)==seeded1+seeded2+advances +
  Python 流式精确整数禁 SQL SUM/REAL + 全部可信入口同 snapshot 验证 +
  reason 可增 conservation_mismatch + format1 不原 ID 就地升级）

覆盖合同九组必要回归 + PHASE2 补充边界（主控 2026-11-15 指令）：
  G1 新旧库安装、UUID、seed=1、SELECT* 列形状不变；两票表不齐 str|None 空态不置 READY。
  G2 INSERT/同值 UPDATE/改回/改 key/DELETE 墓碑/DELETE 再 INSERT/undo 形/重放零推进；
     版本单调不复活（允许同 key UPDATE +2，不断言恰 +2）。
  G3 SQLite 整数溢出真实触发（revision 与 advances 各自），票与 ledger 同业务事务
     回滚（真实 SQLite 行为，不凭推论；SQLite 约束不自动回滚此前语句——回滚义务
     在调用方，测试验证产品写路径确实整体回滚）。
  G4 并发安装同 library_id；异常注入/进程 kill 半装完整回滚；调用方未结事务不被
     install 提交或回滚。
  G5 同路径换库（cookie 恰同）：无 ledger 换入重装新 ID；meta 在而 trigger 坏不误可信。
  G6 坏 meta/trigger/row fail-closed；同名错误定义触发器必 broken（不只核名字）；
     broken 不以原 library_id 自动补好；unmigrated 与 path_absent 分开。
  G7 超长键迁移：旧 key 墓碑与新 key 共存，ledger item_key 不被迁移扫描误改。
  G8 binary_reset/maybe 删除墓碑；同事务回滚一致（无幻影墓碑）。
  G9 安装/验证失败仅挂点局部兼容降级：个人原业务可继续、后次重试成功；不吞基础
     schema/其他必要迁移/原业务错误。
  G10 守恒防护（Sol 定稿七项验收）：任意 ledger 行删除/revision 降值（含墓碑行）
     造成 Σ 缺口后，普通个人写（无论是否先读过坏态）都不能洗白——state/libid/
     revisions/vote_snapshot/幂等install 全部可信入口持续拒绝，旧 library_id 不得
     重获可信，差额经多次正常写保持；各持久整数合法而合计超 64 位时 Python 精确
     验证不误报（不 SQL SUM）；format1 拒绝原 ID 就地升级；并发正常写不产生跨
     snapshot 假 broken。所有人工改值均为自有 fixture 且保持守恒一致（健康构造）
     或如实制造损坏（损坏构造），不伪造产品输出。

诚实记录（不伪造红）：账本 API（coscreen/team_vote_ledger.py 与 db.py 挂点）为 S1a
PHASE2 新增；before 冻结（HEAD 80dd35f，R2 产品箱）不存在该模块——before 侧按合同
如实记录"API 未实现、无法运行"，不以 ImportError 冒充行为红证。真实 before 红/after
绿在隔离箱以冻结候选运行（运行协议：全新纯源码箱、venv312 Python 3.12.13 -I -B、
绝对 scratch DATA、短 E TEMP/TMP/TMPDIR/RF_R9_TMP*、记录 argv/cwd/env 覆盖/exit/
stdout/stderr；禁主仓 app/API/pytest、真实 data/8613/网络/凭据/旧证据箱写入）。

自包含（不依赖 conftest/Safety 夹具、不触 HTTP 层）：REPO_ROOT 由 ``__file__`` 定位，
隔离箱内自动指向箱根。断言钉不变式（单调、fail-closed、同事务回滚、无半装态），
不镜像触发器操作数（不钉恰 +2）、不镜像实现内部文本。无 skip/无全局 ignore/无容差
放宽。静态消费者仅为 unittest 框架；本文件只新增 test 符号，不改任何既有符号。
"""

from __future__ import annotations

import gc
import hashlib
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from coscreen import binary_reset as binary_reset_mod  # noqa: E402
from coscreen import db as db_mod  # noqa: E402
from coscreen import dedup as dedup_mod  # noqa: E402
from coscreen import fulltext as fulltext_mod  # noqa: E402
from coscreen import research_trace as trace_mod  # noqa: E402
from coscreen import review_workflow as workflow_mod  # noqa: E402
from coscreen import team_vote_ledger as ledger  # noqa: E402

INT64_MAX = 2 ** 63 - 1
STAGE1, STAGE2 = "stage1", "stage2"
KEY_MAX_LEN = dedup_mod.KEY_MAX_LEN
#: 决策表 7 列合同形状（不变式断言用，不镜像 ledger 内部）。
DECISION_COLUMNS = (
    "zotero_key", "decision", "exclusion_reason", "notes",
    "tags", "screened_at", "updated_at",
)
_HEX32 = re.compile(r"^[0-9a-f]{32}$")

#: 红侧失败输出定位标签（真实缺口按标签归组）。
TAG_INSTALL_SEED = "S1a-G1 install/uuid/seed/shape"
TAG_TRIGGER_MATRIX = "S1a-G2 trigger matrix monotonic tombstone"
TAG_OVERFLOW_ROLLBACK = "S1a-G3 overflow same-transaction rollback"
TAG_CONCURRENT_INSTALL = "S1a-G4 concurrent install / half-install / caller txn"
TAG_PATH_SWAP = "S1a-G5 same-path swap cache hardening"
TAG_FAIL_CLOSED = "S1a-G6 broken fail-closed / definition-level / path_absent"
TAG_SNAPSHOT = "S1a-G6b same-snapshot vote+id+revision / caller txn join"
TAG_OVERLONG = "S1a-G7 overlong-key migration tombstone coexist"
TAG_BINARY_RESET = "S1a-G8 binary_reset tombstone / rollback consistency"
TAG_COMPAT = "S1a-G9 local degrade / no swallowed errors / no silent revival"
TAG_CONSERVATION = "S1a-G10 conservation / no whitewash / python-exact / format2"

#: 损坏检出的合同内 reason 域：账行被删同时构成 JOIN 缺行（row_missing）与 Σ 缺口
#: （conservation_mismatch），两者的优先归因属产品合同内自由度（Sol：可增
#: conservation_mismatch；明确缺行可 row_missing）；降值（行仍在值变小）无缺行
#: 分量，钉 conservation_mismatch；非法计数值归 meta_corrupt（Sol 原文）。
REASONS_DAMAGE_DELETE = ("row_missing", "conservation_mismatch")
REASON_DEVALUE = "conservation_mismatch"


def _sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _raw_conn(db_path) -> sqlite3.Connection:
    """裸 sqlite3 连接（不触产品 _connect；触发器是库级对象，任何连接写入都会触发）。"""
    conn = sqlite3.connect(str(db_path), timeout=15)
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _decision_row(db_path, key):
    with closing(_raw_conn(db_path)) as conn:
        return conn.execute(
            "SELECT zotero_key,decision,exclusion_reason,notes,tags,screened_at,updated_at "
            "FROM decisions WHERE zotero_key=?", (key,)).fetchone()


def _ledger_revisions_raw(db_path, stage):
    with closing(_raw_conn(db_path)) as conn:
        return dict(conn.execute(
            "SELECT item_key,revision FROM team_vote_revisions WHERE stage=? "
            "ORDER BY item_key", (stage,)).fetchall())


def _ledger_object_rows(db_path):
    """账本相关对象 {name: (type, sql)}（meta/revisions 表 + 六触发器名）。"""
    with closing(_raw_conn(db_path)) as conn:
        rows = conn.execute(
            "SELECT name,type,sql FROM sqlite_master WHERE name LIKE 'team_vote%' "
            "OR name LIKE '%_tvr_%'").fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def _ledger_trigger_names(db_path):
    return sorted(n for n, (t, _) in _ledger_object_rows(db_path).items() if t == "trigger")


def _decision_shape(db_path):
    with closing(_raw_conn(db_path)) as conn:
        out = []
        for table in ("decisions", "stage2_decisions"):
            out.append(conn.execute(
                'SELECT cid,name,type,"notnull",dflt_value,pk FROM pragma_table_info(?) '
                "ORDER BY cid", (table,)).fetchall())
    return out


def _seed_article(db_path, key, title="s1a fixture article"):
    with closing(db_mod._connect(db_path)) as conn, conn:
        conn.execute(
            "INSERT OR IGNORE INTO articles(zotero_key,title) VALUES (?,?)", (key, title))


def _old_style_db(db_path, stage1_keys=(), stage2_keys=()):
    """真实旧库形态：手工建 articles+两决策表（当前列形状、含 tags），无任何账本对象。

    旧库本就没有 ledger——这不是"破坏"，而是 before 侧迁移起点的忠实构造。"""
    with closing(_raw_conn(db_path)) as conn:
        conn.executescript("""
CREATE TABLE articles(
  zotero_key TEXT PRIMARY KEY, title TEXT NOT NULL, doi TEXT, abstract TEXT,
  content_hash TEXT, is_duplicate_of TEXT, import_order INTEGER);
CREATE TABLE decisions(
  zotero_key TEXT PRIMARY KEY REFERENCES articles(zotero_key),
  decision TEXT NOT NULL, exclusion_reason TEXT DEFAULT '',
  notes TEXT DEFAULT '', tags TEXT DEFAULT '',
  screened_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE stage2_decisions(
  zotero_key TEXT PRIMARY KEY REFERENCES articles(zotero_key),
  decision TEXT NOT NULL, exclusion_reason TEXT DEFAULT '',
  notes TEXT DEFAULT '', tags TEXT DEFAULT '',
  screened_at TEXT NOT NULL, updated_at TEXT NOT NULL);
""")
        for key in stage1_keys:
            conn.execute(
                "INSERT INTO articles(zotero_key,title) VALUES (?,?)", (key, "old-" + key))
            conn.execute(
                "INSERT INTO decisions(zotero_key,decision,screened_at,updated_at) "
                "VALUES (?, 'include','2025-01-01T00:00:00','2025-01-01T00:00:00')", (key,))
        for key in stage2_keys:
            if not conn.execute("SELECT 1 FROM articles WHERE zotero_key=?",
                                (key,)).fetchone():
                conn.execute(
                    "INSERT INTO articles(zotero_key,title) VALUES (?,?)", (key, "old-" + key))
            conn.execute(
                "INSERT INTO stage2_decisions(zotero_key,decision,screened_at,updated_at) "
                "VALUES (?, 'exclude','2025-01-02T00:00:00','2025-01-02T00:00:00')", (key,))
        conn.commit()


def _ctx(request_id=None, task="s1a-ledger-task", actor="s1a-actor"):
    return trace_mod.TraceContext(task_id=task, actor_id=actor, request_id=request_id)


class _InjectableConn:
    """sqlite3.connect 工厂返回的委托包装（Python 3.12 的 sqlite3.Connection
    类型不可变、不能类级 patch——run1/run2 实证 TypeError；故改 patch 模块级
    connect 工厂，返回包装+真实连接）。仅 execute 前缀匹配注入；row_factory/
    in_transaction/cursor 等全部属性经 __getattr__ 动态转发到真实连接（包装器
    自身仅持下划线私有名，不遮蔽任何真实属性），事务上下文管理器显式转发。"""

    def __init__(self, conn, prefixes, on_hit=None):
        self._conn = conn
        self._prefixes = tuple(prefixes)
        self._on_hit = on_hit

    def execute(self, sql, *args, **kwargs):
        norm = " ".join(str(sql).strip().upper().split())
        for prefix in self._prefixes:
            if norm.startswith(prefix):
                if self._on_hit is not None:
                    self._on_hit()
                raise sqlite3.OperationalError(
                    "injected deterministic failure (s1a fixture, contract step: %s)" % prefix)
        return self._conn.execute(sql, *args, **kwargs)

    def __enter__(self):
        return self._conn.__enter__()

    def __exit__(self, *exc):
        return self._conn.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _fail_execute_at(*upper_prefixes):
    """定点语句注入：仅当 SQL 归一化后以指定前缀开头才抛确定性 OperationalError，
    其余语句全部委托真实连接（其余产品/SQLite 行为不受影响——R2 同款注入先例）。
    合同锚点：meta 行 INSERT 是安装事务"最后完成标记"（§11.1）；CREATE TRIGGER 是
    触发器建立步。注入位置来自合同步序，不镜像实现行号。"""
    real_connect = sqlite3.connect

    def factory(*args, **kwargs):
        return _InjectableConn(real_connect(*args, **kwargs), upper_prefixes)

    return mock.patch.object(sqlite3, "connect", factory)


_META_INSERT_U = "INSERT INTO TEAM_VOTE_REVISION_META"
_CREATE_TRIGGER_U = "CREATE TRIGGER"


def _remove_sidecars(db_path):
    for suffix in ("-wal", "-shm", "-journal"):
        p = Path(str(db_path) + suffix)
        for attempt in range(8):
            try:
                if p.exists():
                    p.unlink()
                break
            except PermissionError:
                time.sleep(0.15 * (attempt + 1))


def _swap_file(src, dst, attempts=10):
    """Windows 安全换入：先清 dst 边车再 os.replace（权限抖动短退避重试）。"""
    _remove_sidecars(dst)
    last = None
    for attempt in range(attempts):
        try:
            os.replace(str(src), str(dst))
            return
        except PermissionError as exc:
            last = exc
            time.sleep(0.2 * (attempt + 1))
    raise last


def _backup_db(src, dst):
    """SQLite backup API 一致性快照复制（源可为 WAL 库）。"""
    with closing(_raw_conn(src)) as s, closing(sqlite3.connect(str(dst))) as d:
        s.backup(d)


class _LedgerBox(unittest.TestCase):
    """隔离数据根 + 箱身份/来源核验 + 进程内缓存隔离（contract 隔离与交付节）。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="rf216_s1a_ledger_")
        cls.root = Path(cls._tmp.name)

        def _cleanup():
            gc.collect()
            cls._tmp.cleanup()

        cls.addClassCleanup(_cleanup)
        # 箱身份：导入面必须全部落在 REPO_ROOT（隔离纯源码箱）内，主仓不参与。
        for module in (db_mod, ledger, binary_reset_mod, fulltext_mod, trace_mod):
            src = Path(module.__file__).resolve()
            assert src.is_relative_to(REPO_ROOT), (module.__name__, str(src))
        assert Path(db_mod.__file__).resolve().parent == (REPO_ROOT / "coscreen").resolve()
        print("S1A_TEST_FILE_SHA", _sha256(__file__))
        print("S1A_SOURCE_DB_SHA", _sha256(db_mod.__file__))
        print("S1A_SOURCE_LEDGER_SHA", _sha256(ledger.__file__))
        print("S1A_SOURCE_BINARY_RESET_SHA", _sha256(binary_reset_mod.__file__))
        print("S1A_SOURCE_FULLTEXT_SHA", _sha256(fulltext_mod.__file__))
        print("S1A_REPO_ROOT", str(REPO_ROOT))
        print("S1A_PYTHON", sys.version.split()[0])
        print("S1A_SQLITE_VERSION", sqlite3.sqlite_version)
        print("S1A_DEFAULT_DATA_ABSENT_PRE", not (REPO_ROOT / "data").exists())
        assert not (REPO_ROOT / "data").exists(), "隔离箱内不得落 default data 目录"
        cls.addClassCleanup(cls._post_box_checks)

    @classmethod
    def _post_box_checks(cls):
        absent = not (REPO_ROOT / "data").exists()
        print("S1A_DEFAULT_DATA_ABSENT_POST", absent)
        assert absent, "隔离箱内不得落 default data 目录"

    def setUp(self):
        self.base = self.root / self.id().rsplit(".", 1)[-1]
        self.base.mkdir(parents=True, exist_ok=True)
        # 进程内迁移/READY 缓存逐用例清空（缓存语义本身是 G5 的被测对象）。
        db_mod._MIGRATED_SCHEMAS.clear()
        db_mod._LEDGER_READY.clear()

    # -- 常用构造 -----------------------------------------------------------
    def _db(self, name="member.db") -> Path:
        return self.base / name

    def _installed_db(self, name="member.db") -> Path:
        """新库：init_db 即带账本（init_db 挂点合同路径）。"""
        db = self._db(name)
        db_mod.init_db(db)
        conn = self._pc(db)
        try:
            self.assertEqual(ledger.ledger_state(conn), "installed", TAG_INSTALL_SEED)
        finally:
            conn.close()
        return db

    def _pc(self, db_path) -> sqlite3.Connection:
        """产品连接工厂（自愈链+挂点；测试中显式 close）。"""
        return db_mod._connect(db_path)

    def _assert_hex32(self, value, tag=TAG_INSTALL_SEED):
        self.assertIsInstance(value, str, tag)
        self.assertRegex(value, _HEX32, tag)

    def _revs(self, conn, stage):
        return ledger.revisions(conn, stage)

    def _seq(self, db, key, stage=STAGE1):
        with closing(self._pc(db)) as conn:
            return self._revs(conn, stage).get(key)

    def _seq_with(self, db):
        with closing(self._pc(db)) as conn:
            return {STAGE1: self._revs(conn, STAGE1), STAGE2: self._revs(conn, STAGE2)}

    def _assert_all_trust_entries_reject(self, db, stage, old_lib, key, check_no):
        """强断言（可重复，Sol：所有可信入口 fail-closed，不因 reason 优先级变
        installed）：损坏后账本必须持续 broken；library_id/revisions/vote_snapshot
        必须精确 VoteLedgerUnavailable（reason 合同域内）；幂等 install 不得完成
        验证返回（拒绝异常限合同内类型族，防无关异常冒充 fail-closed）。"""
        conn = self._pc(db)
        try:
            state = ledger.ledger_state(conn)
            self.assertEqual(state, "broken",
                             "check#%d key=%s：损坏后必须持续 broken（实得 %r）: %s"
                             % (check_no, key, state, TAG_CONSERVATION))
            for caller, name in ((lambda: ledger.library_id(conn), "library_id"),
                                 (lambda: ledger.revisions(conn, stage), "revisions"),
                                 (lambda: ledger.vote_snapshot(conn, stage),
                                  "vote_snapshot")):
                with self.assertRaises(
                        ledger.VoteLedgerUnavailable,
                        msg="check#%d key=%s：%s 必须精确 VoteLedgerUnavailable: %s"
                            % (check_no, key, name, TAG_CONSERVATION)) as caught:
                    caller()
                self.assertIn(caught.exception.reason, ledger.REASONS,
                              "check#%d key=%s：%s reason 必须在合同域: %s"
                              % (check_no, key, name, TAG_CONSERVATION))
            with self.assertRaises(
                    ledger.VoteLedgerUnavailable,
                    msg="check#%d key=%s：broken 下 install 必须精确 "
                        "VoteLedgerUnavailable 拒修: %s" % (check_no, key, TAG_CONSERVATION)) as caught_i:
                ledger.install(conn)
            self.assertIn(caught_i.exception.reason, ledger.REASONS,
                          "check#%d key=%s：install 拒绝 reason 必须在合同域: %s"
                          % (check_no, key, TAG_CONSERVATION))
        finally:
            conn.close()

    def _meta_row(self, db):
        with closing(_raw_conn(db)) as raw:
            return raw.execute(
                "SELECT singleton,format_version,library_id,seeded_stage1,"
                "seeded_stage2,advances FROM team_vote_revision_meta").fetchone()


class T1_InstallSeedShapeTests(_LedgerBox):
    """G1：新旧库安装、UUID、seed=1、SELECT* 列形状不变、两票表不齐空态。"""

    def test_01_new_db_init_db_installs_full(self):
        db = self._db()
        db_mod.init_db(db)
        conn = self._pc(db)
        try:
            self.assertEqual(ledger.ledger_state(conn), "installed", TAG_INSTALL_SEED)
            lib = ledger.library_id(conn)
            self._assert_hex32(lib)
            # meta 单例：恰一行且 singleton=1、format 受支持、library_id 合法。
            with closing(_raw_conn(db)) as raw:
                meta = raw.execute(
                    "SELECT singleton,format_version,library_id "
                    "FROM team_vote_revision_meta").fetchall()
            self.assertEqual(len(meta), 1, TAG_INSTALL_SEED)
            self.assertEqual(meta[0][0], 1, TAG_INSTALL_SEED)
            self.assertEqual(meta[0][1], ledger.LEDGER_FORMAT_VERSION, TAG_INSTALL_SEED)
            self.assertEqual(meta[0][2], lib, TAG_INSTALL_SEED)
            # 六触发器齐备（两票表各 INSERT/UPDATE/DELETE）。
            triggers = _ledger_trigger_names(db)
            self.assertEqual(len(triggers), 6, TAG_INSTALL_SEED)
            # seed 空（新库无票）。
            self.assertEqual(self._revs(conn, STAGE1), {}, TAG_INSTALL_SEED)
            self.assertEqual(self._revs(conn, STAGE2), {}, TAG_INSTALL_SEED)
            # 幂等：install 返回现值（32hex），meta 仍单行。
            self.assertEqual(ledger.install(conn), lib, TAG_INSTALL_SEED)
            with closing(_raw_conn(db)) as raw:
                self.assertEqual(raw.execute(
                    "SELECT count(*) FROM team_vote_revision_meta").fetchone()[0],
                    1, TAG_INSTALL_SEED)
            self.assertIn(Path(db).resolve(), db_mod._LEDGER_READY, TAG_INSTALL_SEED)
        finally:
            conn.close()

    def test_02_old_db_connect_installs_seed1(self):
        db = self._db()
        _old_style_db(db, stage1_keys=("OLD1", "OLD2"), stage2_keys=("OLD3",))
        # before：确无任何账本对象（旧库真实形态）。
        self.assertEqual(_ledger_object_rows(db), {}, TAG_INSTALL_SEED)
        conn = self._pc(db)
        try:
            self.assertEqual(ledger.ledger_state(conn), "installed", TAG_INSTALL_SEED)
            self._assert_hex32(ledger.library_id(conn))
            # seed=1 精确钉住（合同：seed 现有票 1 为基线）。
            self.assertEqual(self._revs(conn, STAGE1),
                             {"OLD1": 1, "OLD2": 1}, TAG_INSTALL_SEED)
            self.assertEqual(self._revs(conn, STAGE2), {"OLD3": 1}, TAG_INSTALL_SEED)
        finally:
            conn.close()

    def test_03_select_star_shape_unchanged(self):
        db = self._db()
        _old_style_db(db, stage1_keys=("S1", "S2"), stage2_keys=("S3",))
        shape_before = _decision_shape(db)
        with closing(_raw_conn(db)) as raw:
            rows_before = (
                raw.execute("SELECT * FROM decisions ORDER BY zotero_key").fetchall(),
                raw.execute("SELECT * FROM stage2_decisions ORDER BY zotero_key").fetchall())
        conn = self._pc(db)
        try:
            self.assertEqual(ledger.ledger_state(conn), "installed", TAG_INSTALL_SEED)
        finally:
            conn.close()
        self.assertEqual(_decision_shape(db), shape_before,
                         "安装不得改决策表列形状（无新列）: " + TAG_INSTALL_SEED)
        with closing(_raw_conn(db)) as raw:
            rows_after = (
                raw.execute("SELECT * FROM decisions ORDER BY zotero_key").fetchall(),
                raw.execute("SELECT * FROM stage2_decisions ORDER BY zotero_key").fetchall())
        self.assertEqual(rows_after, rows_before, TAG_INSTALL_SEED)

    def test_04_two_tables_absent_noop_none_no_ready(self):
        db = self._db()
        # 只有 articles（缺 decisions）：两票表不齐 → 安装空态 None，不建任何账本对象。
        with closing(_raw_conn(db)) as raw:
            raw.execute("CREATE TABLE articles("
                        "zotero_key TEXT PRIMARY KEY, title TEXT NOT NULL)")
            raw.commit()
        conn = self._pc(db)
        try:
            result = ledger.install(conn)
            self.assertIsNone(result,
                              "两票表不齐须返回 None（str|None 空态合同）: " + TAG_INSTALL_SEED)
            self.assertEqual(ledger.both_decision_tables_present(conn), False,
                             TAG_INSTALL_SEED)
        finally:
            conn.close()
        self.assertEqual(_ledger_object_rows(db), {}, TAG_INSTALL_SEED)
        self.assertNotIn(db.resolve(), db_mod._LEDGER_READY,
                         "None 空态不得置 READY: " + TAG_INSTALL_SEED)
        # 正面：两票表齐 → 探针 True。
        db2 = self._installed_db("full.db")
        conn2 = self._pc(db2)
        try:
            self.assertEqual(ledger.both_decision_tables_present(conn2), True,
                             TAG_INSTALL_SEED)
        finally:
            conn2.close()

    def test_05_uuid_unique_per_library(self):
        db_a = self._installed_db("a.db")
        db_b = self._installed_db("b.db")
        with closing(self._pc(db_a)) as ca, closing(self._pc(db_b)) as cb:
            id_a, id_b = ledger.library_id(ca), ledger.library_id(cb)
        self._assert_hex32(id_a)
        self._assert_hex32(id_b)
        self.assertNotEqual(id_a, id_b, "每个库唯一 32hex library_id: " + TAG_INSTALL_SEED)


class T2_TriggerMatrixTests(_LedgerBox):
    """G2：全写形触发矩阵；版本单调不复活；不镜像操作数（不钉恰 +2）。"""

    def test_01_insert_starts_and_advances(self):
        db = self._installed_db()
        _seed_article(db, "K1")
        db_mod.save_decision(db, "K1", "include")
        rev1 = self._seq(db, "K1")
        self.assertEqual(rev1, 1, "首笔 INSERT 起账 1（seed 空库合同 DDL 常量）: "
                         + TAG_TRIGGER_MATRIX)
        db_mod.save_decision(db, "K1", "exclude")
        self.assertGreater(self._seq(db, "K1"), rev1, TAG_TRIGGER_MATRIX)

    def test_02_same_value_rewrite_monotonic(self):
        db = self._installed_db()
        _seed_article(db, "K1")
        seq = []
        for _ in range(4):
            db_mod.save_decision(db, "K1", "include", notes="same-value rewrite")
            seq.append(self._seq(db, "K1"))
        for prev, cur in zip(seq, seq[1:]):
            self.assertGreater(cur, prev,
                               "同值重写必须推进（覆盖改回）: " + TAG_TRIGGER_MATRIX)

    def test_03_change_back_monotonic(self):
        db = self._installed_db()
        _seed_article(db, "K1")
        seq = []
        for decision in ("include", "exclude", "include"):
            db_mod.save_decision(db, "K1", decision)
            seq.append(self._seq(db, "K1"))
        for prev, cur in zip(seq, seq[1:]):
            self.assertGreater(cur, prev, "改回（A→B→A）逐次单调推进: " + TAG_TRIGGER_MATRIX)

    def test_04_key_change_update_tombstone_and_new(self):
        db = self._installed_db()
        _seed_article(db, "KOLD")
        _seed_article(db, "KNEW")
        db_mod.save_decision(db, "KOLD", "include")
        rev_old_before = self._seq(db, "KOLD")
        with closing(_raw_conn(db)) as raw, raw:
            raw.execute("UPDATE decisions SET zotero_key='KNEW' WHERE zotero_key='KOLD'")
        revs = self._seq_with(db)
        # 旧 key 墓碑仍在且不小于变更前；新 key 起新账（>=1）。
        self.assertGreaterEqual(revs[STAGE1].get("KOLD", 0), rev_old_before,
                                "改 key 后旧 key 墓碑必须保留: " + TAG_TRIGGER_MATRIX)
        self.assertGreaterEqual(revs[STAGE1].get("KNEW", 0), 1,
                                "改 key 后新 key 必须有自己的账行: " + TAG_TRIGGER_MATRIX)
        with closing(_raw_conn(db)) as raw:
            self.assertIsNone(raw.execute(
                "SELECT 1 FROM decisions WHERE zotero_key='KOLD'").fetchone())

    def test_05_delete_leaves_tombstone(self):
        db = self._installed_db()
        _seed_article(db, "K1")
        db_mod.save_decision(db, "K1", "include")
        rev_before = self._seq(db, "K1")
        with closing(_raw_conn(db)) as raw, raw:
            raw.execute("DELETE FROM decisions WHERE zotero_key='K1'")
        rev_after = self._seq(db, "K1")
        self.assertIsNotNone(rev_after, "DELETE 后墓碑行必须留存: " + TAG_TRIGGER_MATRIX)
        self.assertGreater(rev_after, rev_before, TAG_TRIGGER_MATRIX)
        with closing(_raw_conn(db)) as raw:
            self.assertIsNone(raw.execute(
                "SELECT 1 FROM decisions WHERE zotero_key='K1'").fetchone())

    def test_06_delete_then_insert_no_resurrection(self):
        db = self._installed_db()
        _seed_article(db, "K1")
        db_mod.save_decision(db, "K1", "include")
        rev1 = self._seq(db, "K1")
        with closing(_raw_conn(db)) as raw, raw:
            raw.execute("DELETE FROM decisions WHERE zotero_key='K1'")
        rev2 = self._seq(db, "K1")
        self.assertGreater(rev2, rev1, TAG_TRIGGER_MATRIX)
        db_mod.save_decision(db, "K1", "include")
        rev3 = self._seq(db, "K1")
        self.assertGreater(rev3, rev2,
                           "DELETE 再 INSERT 版本必须继续推进、绝不复活回 1: "
                           + TAG_TRIGGER_MATRIX)
        self.assertNotEqual(rev3, 1, TAG_TRIGGER_MATRIX)

    def test_07_undo_shape_delete_insert_same_txn(self):
        """undo 的真实写形 = 同事务 DELETE+INSERT（workspace_undo.undo 语句形，§11.2）。"""
        db = self._installed_db()
        _seed_article(db, "K1")
        db_mod.save_decision(db, "K1", "include")
        rev_before = self._seq(db, "K1")
        with closing(_raw_conn(db)) as raw:
            raw.execute("BEGIN IMMEDIATE")
            raw.execute("DELETE FROM decisions WHERE zotero_key='K1'")
            raw.execute(
                "INSERT INTO decisions(zotero_key,decision,screened_at,updated_at) "
                "VALUES ('K1','include','2025-01-01T00:00:00','2025-01-01T00:00:00')")
            raw.commit()
        self.assertGreater(self._seq(db, "K1"), rev_before,
                           "undo 形 DELETE+INSERT 同事务推进（+2 允许，不钉恰值）: "
                           + TAG_TRIGGER_MATRIX)

    def test_08_stage2_matrix_isolated(self):
        db = self._installed_db()
        _seed_article(db, "K1")
        fulltext_mod.save_stage2(db, "K1", "include")
        rev1 = self._seq(db, "K1", STAGE2)
        self.assertEqual(rev1, 1, TAG_TRIGGER_MATRIX)
        fulltext_mod.save_stage2(db, "K1", "exclude")
        rev2 = self._seq(db, "K1", STAGE2)
        self.assertGreater(rev2, rev1, TAG_TRIGGER_MATRIX)
        with closing(_raw_conn(db)) as raw, raw:
            raw.execute("DELETE FROM stage2_decisions WHERE zotero_key='K1'")
        rev3 = self._seq(db, "K1", STAGE2)
        self.assertIsNotNone(rev3, "stage2 DELETE 墓碑留存: " + TAG_TRIGGER_MATRIX)
        self.assertGreater(rev3, rev2, TAG_TRIGGER_MATRIX)
        # 跨 stage 隔离：stage1 账全程零行。
        self.assertEqual(self._seq_with(db)[STAGE1], {}, TAG_TRIGGER_MATRIX)

    def test_09_replay_zero_advance(self):
        db = self._installed_db()
        _seed_article(db, "K1")
        ctx = _ctx(request_id="req-replay-1")
        db_mod.save_decision(db, "K1", "include", trace_context=ctx)
        rev1 = self._seq(db, "K1")
        result = db_mod.save_decision(db, "K1", "include", trace_context=ctx)
        self.assertIsNone(result, "重放分支返回 None: " + TAG_TRIGGER_MATRIX)
        self.assertEqual(self._seq(db, "K1"), rev1,
                         "重放（无业务 SQL）零推进: " + TAG_TRIGGER_MATRIX)
        # restore 通道同钉（CLI 回导幂等重放）。
        ctx2 = _ctx(request_id="req-replay-2")
        db_mod.restore_decision(db, "K1", "exclude", screened_at="2025-01-01T00:00:00",
                                trace_context=ctx2)
        rev2 = self._seq(db, "K1")
        db_mod.restore_decision(db, "K1", "exclude", screened_at="2025-01-01T00:00:00",
                                trace_context=ctx2)
        self.assertEqual(self._seq(db, "K1"), rev2,
                         "restore 重放零推进: " + TAG_TRIGGER_MATRIX)

    def test_10_no_business_sql_no_advance(self):
        db = self._installed_db()
        _seed_article(db, "K1")
        db_mod.save_decision(db, "K1", "include")
        rev1 = self._seq(db, "K1")
        with closing(self._pc(db)) as conn:
            conn.execute("SELECT count(*) FROM decisions").fetchone()
            conn.execute("SELECT count(*) FROM team_vote_revisions").fetchone()
        self.assertEqual(self._seq(db, "K1"), rev1,
                         "无业务 SQL 则零推进: " + TAG_TRIGGER_MATRIX)


class T3_OverflowRollbackTests(_LedgerBox):
    """G3：整数溢出真实触发；票与 ledger 同业务事务回滚（真实 SQLite 行为）。"""

    def _max_out(self, db, key):
        with closing(_raw_conn(db)) as raw, raw:
            raw.execute(
                "UPDATE team_vote_revisions SET revision=? WHERE stage=? AND item_key=?",
                (INT64_MAX, STAGE1, key))

    def test_01_raw_business_txn_rolls_back_both(self):
        db = self._installed_db()
        _seed_article(db, "K1")
        db_mod.save_decision(db, "K1", "include")
        rev_before = self._seq(db, "K1")
        row_before = _decision_row(db, "K1")
        with closing(_raw_conn(db)) as raw:
            raw.execute("BEGIN IMMEDIATE")
            raw.execute(
                "UPDATE team_vote_revisions SET revision=? WHERE stage=? AND item_key=?",
                (INT64_MAX, STAGE1, "K1"))
            with self.assertRaises(sqlite3.Error,
                                   msg="溢出必须真实失败（合同：不凭加法推论）: "
                                       + TAG_OVERFLOW_ROLLBACK):
                raw.execute("UPDATE decisions SET notes=notes||'!' WHERE zotero_key='K1'")
            raw.rollback()
        self.assertEqual(_ledger_revisions_raw(db, STAGE1).get("K1"), rev_before,
                         "溢出后 ledger 必须随事务回滚: " + TAG_OVERFLOW_ROLLBACK)
        self.assertEqual(_decision_row(db, "K1"), row_before,
                         "溢出后票必须随事务回滚: " + TAG_OVERFLOW_ROLLBACK)

    def test_02_product_writer_overflow_rolls_back_both(self):
        db = self._installed_db()
        _seed_article(db, "K1")
        db_mod.save_decision(db, "K1", "include")
        self._max_out(db, "K1")
        row_before = _decision_row(db, "K1")
        with self.assertRaises(sqlite3.Error,
                               msg="产品写路径触发器溢出必须失败: " + TAG_OVERFLOW_ROLLBACK):
            db_mod.save_decision(db, "K1", "exclude", notes="overflow-probe")
        self.assertEqual(_decision_row(db, "K1"), row_before,
                         "业务事务必须整体回滚（票不动）: " + TAG_OVERFLOW_ROLLBACK)
        self.assertEqual(_ledger_revisions_raw(db, STAGE1).get("K1"), INT64_MAX,
                         "ledger 同事务回滚（保持溢出前置值）: " + TAG_OVERFLOW_ROLLBACK)

    def test_03_check_constraints_enforced(self):
        """结构约束级验证（合同 DDL CHECK：revision>=1 整数、library_id 长 32、
        守恒三计数器非负整数——Sol format2 DDL）。"""
        db = self._installed_db()
        _seed_article(db, "K1")
        db_mod.save_decision(db, "K1", "include")
        with closing(_raw_conn(db)) as raw:
            with self.assertRaises(sqlite3.Error, msg=TAG_FAIL_CLOSED):
                with raw:
                    raw.execute("UPDATE team_vote_revisions SET revision=0 "
                                "WHERE stage=? AND item_key=?", (STAGE1, "K1"))
            with self.assertRaises(sqlite3.Error, msg=TAG_FAIL_CLOSED):
                with raw:
                    raw.execute("UPDATE team_vote_revision_meta SET library_id='short'")
            with self.assertRaises(sqlite3.Error, msg=TAG_FAIL_CLOSED):
                with raw:
                    raw.execute("UPDATE team_vote_revisions SET revision=1.5 "
                                "WHERE stage=? AND item_key=?", (STAGE1, "K1"))
            for counter in ("seeded_stage1", "seeded_stage2", "advances"):
                with self.assertRaises(sqlite3.Error, msg=TAG_CONSERVATION):
                    with raw:
                        raw.execute("UPDATE team_vote_revision_meta SET %s=-1" % counter)
                with self.assertRaises(sqlite3.Error, msg=TAG_CONSERVATION):
                    with raw:
                        raw.execute("UPDATE team_vote_revision_meta SET %s=0.5" % counter)


class T4_ConcurrentAtomicInstallTests(_LedgerBox):
    """G4：并发安装同 ID；异常注入/进程 kill 半装整回滚；调用方未结事务不被触碰。"""

    _CHILD = r'''
import os, sys
repo, dbpath, mode = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, repo)
from coscreen import team_vote_ledger as ledger
import sqlite3
_real_connect = sqlite3.connect

class _KillAtMetaMarker:
    """connect 工厂包装（Connection 类型不可变不能类级赋值）：到达合同最后完成
    标记语句（meta 单例行 INSERT）时打印 marker+当前事务态并预定 os._exit(9)。"""
    def __init__(self, conn):
        self._conn = conn
    def execute(self, sql, *a, **k):
        norm = " ".join(str(sql).strip().upper().split())
        if norm.startswith("INSERT INTO TEAM_VOTE_REVISION_META"):
            sys.stdout.write("REACHED_META_MARKER in_txn=%s\n" % self._conn.in_transaction)
            sys.stdout.flush()
            os._exit(9)
        return self._conn.execute(sql, *a, **k)
    def __getattr__(self, name):
        return getattr(self._conn, name)

if mode == "kill":  # 仅 kill 模式注入；install 模式走真实完整安装。
    def _factory(*a, **k):
        return _KillAtMetaMarker(_real_connect(*a, **k))
    sqlite3.connect = _factory
result = ledger.install_in_db(dbpath)
sys.stdout.write("CHILD_RESULT " + str(result) + "\n")
sys.stdout.flush()
'''

    def _child_script(self) -> Path:
        path = self.base / "_s1a_child_install.py"
        path.write_text(self._CHILD, encoding="utf-8")
        return path

    def _run_child(self, script, db, mode):
        return subprocess.run(
            [sys.executable, "-I", "-B", str(script), str(REPO_ROOT), str(db), mode],
            cwd=str(self.base), capture_output=True, text=True, timeout=120)

    def test_01_double_thread_install_same_id(self):
        db = self._db()
        _old_style_db(db, stage1_keys=("C1",), stage2_keys=())
        results = {}
        barrier = threading.Barrier(2)

        def worker():
            barrier.wait(timeout=30)  # 同步起跑，制造真实并发竞争。
            results[threading.get_ident()] = ledger.install_in_db(db)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        self.assertEqual(len(results), 2, TAG_CONCURRENT_INSTALL)
        ids = set(results.values())
        self.assertEqual(len(ids), 1, "并发安装必须收敛到同一 library_id: "
                         + TAG_CONCURRENT_INSTALL)
        (only,) = ids
        self._assert_hex32(only, TAG_CONCURRENT_INSTALL)
        with closing(_raw_conn(db)) as raw:
            self.assertEqual(raw.execute(
                "SELECT count(*) FROM team_vote_revision_meta").fetchone()[0],
                1, TAG_CONCURRENT_INSTALL)

    def test_02_double_process_install_same_id(self):
        db = self._db()
        _old_style_db(db, stage1_keys=("P1",), stage2_keys=())
        script = self._child_script()
        # 真并发：两进程同时起跑（Popen 后统一 wait），制造 BEGIN IMMEDIATE 竞争。
        procs = [subprocess.Popen(
            [sys.executable, "-I", "-B", str(script), str(REPO_ROOT), str(db), "install"],
            cwd=str(self.base), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True) for _ in range(2)]
        outs = []
        for proc in procs:
            stdout, stderr = proc.communicate(timeout=120)
            outs.append((proc.returncode, stdout, stderr))
        for returncode, stdout, stderr in outs:
            self.assertEqual(returncode, 0, "子进程安装失败：%s" % stderr[-800:])
        ids = set()
        for returncode, stdout, _stderr in outs:
            match = re.search(r"^CHILD_RESULT (\S+)$", stdout, re.M)
            self.assertIsNotNone(match, stdout[-800:])
            ids.add(match.group(1))
        self.assertEqual(len(ids), 1,
                         "双进程并发安装必须同一已提交 library_id: " + TAG_CONCURRENT_INSTALL)
        (only,) = ids
        self._assert_hex32(only, TAG_CONCURRENT_INSTALL)
        with closing(_raw_conn(db)) as raw:
            self.assertEqual(raw.execute(
                "SELECT library_id FROM team_vote_revision_meta").fetchone()[0],
                only, TAG_CONCURRENT_INSTALL)

    def test_03_injected_fail_meta_marker_full_rollback(self):
        db = self._db()
        _old_style_db(db, stage1_keys=("F1",), stage2_keys=())
        with _fail_execute_at(_META_INSERT_U):
            with self.assertRaises(sqlite3.Error,
                                   msg="完成标记步失败必须上抛: " + TAG_CONCURRENT_INSTALL):
                ledger.install_in_db(db)
        # 无半装态：meta/revisions/触发器一个不剩，状态 absent，可重装。
        # （absent 观测用裸连接——产品 _connect 的 hook 会合法懒安装改变事实。）
        self.assertEqual(_ledger_object_rows(db), {},
                         "任一步失败必须整体回滚（无半装态）: " + TAG_CONCURRENT_INSTALL)
        with closing(_raw_conn(db)) as raw:
            self.assertEqual(ledger.ledger_state(raw), "absent", TAG_CONCURRENT_INSTALL)
        id1 = ledger.install_in_db(db)
        self._assert_hex32(id1, TAG_CONCURRENT_INSTALL)
        self.assertEqual(ledger.install_in_db(db), id1, "重试幂等: " + TAG_CONCURRENT_INSTALL)
        self.assertNotIn(db.resolve(), db_mod._LEDGER_READY,
                         "失败不得置 READY: " + TAG_CONCURRENT_INSTALL)

    def test_04_injected_fail_trigger_step_full_rollback(self):
        db = self._db()
        _old_style_db(db, stage1_keys=("F2",), stage2_keys=())
        with _fail_execute_at(_CREATE_TRIGGER_U):
            with self.assertRaises(sqlite3.Error, msg=TAG_CONCURRENT_INSTALL):
                ledger.install_in_db(db)
        self.assertEqual(_ledger_object_rows(db), {}, TAG_CONCURRENT_INSTALL)
        with closing(_raw_conn(db)) as raw:
            self.assertEqual(ledger.ledger_state(raw), "absent", TAG_CONCURRENT_INSTALL)
        self._assert_hex32(ledger.install_in_db(db), TAG_CONCURRENT_INSTALL)

    def test_05_process_kill_mid_install_leaves_absent(self):
        db = self._db()
        _old_style_db(db, stage1_keys=("K9",), stage2_keys=())
        script = self._child_script()
        proc = self._run_child(script, db, "kill")
        # 预定死亡：恰在目标完成标记语句内 os._exit(9)，且当时处于安装事务内
        # （in_txn=True）——任意非零退出不得冒充到达目标阶段。
        self.assertEqual(proc.returncode, 9,
                         "kill 必须为预定 exit9（实得 %r, stderr=%r）: %s"
                         % (proc.returncode, proc.stderr[-500:], TAG_CONCURRENT_INSTALL))
        self.assertIn("REACHED_META_MARKER", proc.stdout,
                      "子进程必须真实到达 meta 完成标记语句: " + TAG_CONCURRENT_INSTALL)
        self.assertIn("in_txn=True", proc.stdout,
                      "死亡时必须处于 BEGIN IMMEDIATE 安装事务内: " + TAG_CONCURRENT_INSTALL)
        # 裸 RO 观测（不触发自愈）：无半装对象、原票保留。
        with closing(_raw_conn(db)) as raw:
            objs = raw.execute(
                "SELECT name,type FROM sqlite_master WHERE name LIKE 'team_vote%' "
                "OR name LIKE '%_tvr_%'").fetchall()
            votes = raw.execute("SELECT count(*) FROM decisions WHERE zotero_key='K9'"
                                ).fetchone()[0]
        self.assertEqual(objs, [],
                         "进程死亡不得留下半装对象（裸RO观测）: " + TAG_CONCURRENT_INSTALL)
        self.assertEqual(votes, 1, "原票必须原样保留: " + TAG_CONCURRENT_INSTALL)
        # 单独合法重装：absent（裸RO观测）→ 32hex 新身份。
        with closing(_raw_conn(db)) as raw:
            self.assertEqual(ledger.ledger_state(raw), "absent", TAG_CONCURRENT_INSTALL)
        id_fresh = ledger.install_in_db(db)
        self._assert_hex32(id_fresh, TAG_CONCURRENT_INSTALL)
        with closing(_raw_conn(db)) as raw:
            self.assertEqual(raw.execute(
                "SELECT library_id FROM team_vote_revision_meta").fetchone()[0],
                id_fresh, TAG_CONCURRENT_INSTALL)

    def test_06_install_never_touches_caller_open_txn(self):
        """前置说明：_pc（产品 _connect 自愈 hook）在本测试开始前已合法安装账本
        （两票表齐的旧式库触达即装=合同懒升级路径）。violation 语义钉：install
        运行时违约必须 RuntimeError（PHASE2 文档化合同，不得折叠兼容族）、不得
        终结/提交/回滚调用方事务、安装面（meta 单行+6触发器+身份）违约前后零变。"""
        db = self._db()
        _old_style_db(db, stage1_keys=(), stage2_keys=())
        _seed_article(db, "CX")
        conn = self._pc(db)
        try:
            lib_before = ledger.library_id(conn)
            trigger_count_before = len(_ledger_trigger_names(db))
            self.assertEqual(trigger_count_before, 6,
                             "前置：hook 已合法安装六触发器: " + TAG_CONCURRENT_INSTALL)
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO decisions(zotero_key,decision,screened_at,updated_at) "
                "VALUES ('CX','include','2025-01-01T00:00:00','2025-01-01T00:00:00')")
            # 调用方未结事务内 install = 编程违约：RuntimeError，不吞不提交不回滚。
            with self.assertRaises(RuntimeError,
                                   msg="未结事务内 install 必须 RuntimeError: "
                                       + TAG_CONCURRENT_INSTALL) as caught:
                ledger.install(conn)
            self.assertNotIsInstance(
                caught.exception, ledger.VoteLedgerUnavailable,
                "编程违约不得折叠进兼容降级错误族: " + TAG_CONCURRENT_INSTALL)
            self.assertTrue(conn.in_transaction,
                            "install 不得终结调用方事务: " + TAG_CONCURRENT_INSTALL)
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM decisions WHERE zotero_key='CX'").fetchone()[0], 1,
                "调用方未提交写入必须仍在其事务内: " + TAG_CONCURRENT_INSTALL)
            conn.rollback()
            self.assertFalse(conn.in_transaction)
        finally:
            conn.close()
        # 安装面违约前后零变（meta 单行同身份+6触发器）。
        with closing(_raw_conn(db)) as raw:
            meta = raw.execute(
                "SELECT count(*),library_id FROM team_vote_revision_meta").fetchall()
            trigs = len(raw.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND name LIKE '%_tvr_%'").fetchall())
        self.assertEqual(meta, [(1, lib_before)],
                         "违约后 meta 单行同身份零变: " + TAG_CONCURRENT_INSTALL)
        self.assertEqual(trigs, trigger_count_before,
                         "违约后触发器面零变: " + TAG_CONCURRENT_INSTALL)
        # 变体：调用方自行 commit —— 未结写入落库，账本面仍零变（install 未曾提交它）。
        _seed_article(db, "CX2")
        conn = self._pc(db)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO decisions(zotero_key,decision,screened_at,updated_at) "
                "VALUES ('CX2','include','2025-01-01T00:00:00','2025-01-01T00:00:00')")
            with self.assertRaises(RuntimeError, msg=TAG_CONCURRENT_INSTALL):
                ledger.install(conn)
            conn.commit()
        finally:
            conn.close()
        self.assertIsNone(_decision_row(db, "CX"),
                          "变体1调用方已回滚自己的未结写入——CX 不得在库: "
                          + TAG_CONCURRENT_INSTALL)
        self.assertEqual(_decision_row(db, "CX2")[1], "include",
                         "变体2调用方 commit 的未结写入必须落库: " + TAG_CONCURRENT_INSTALL)
        with closing(_raw_conn(db)) as raw:
            meta = raw.execute(
                "SELECT count(*),library_id FROM team_vote_revision_meta").fetchall()
        self.assertEqual(meta, [(1, lib_before)], TAG_CONCURRENT_INSTALL)

    def test_07_install_in_db_absent_path_never_creates(self):
        missing = self.base / "not-created.db"
        with self.assertRaises(ledger.VoteLedgerUnavailable) as caught:
            ledger.install_in_db(missing)
        self.assertEqual(caught.exception.reason, "path_absent",
                         "缺路径必须报 path_absent（mode=rw 拒绝）: " + TAG_FAIL_CLOSED)
        self.assertFalse(missing.exists(),
                         "install_in_db 绝不建空库: " + TAG_FAIL_CLOSED)


class T5_PathSwapCacheHardeningTests(_LedgerBox):
    """G5：同路径换库（cookie 恰同）不误可信；meta 在而 trigger 坏不误 READY。"""

    def _cookie_of(self, db) -> int:
        cookie = db_mod._MIGRATED_SCHEMAS.get(Path(db).resolve())
        self.assertIsNotNone(cookie, "完整自愈后必有 cookie: " + TAG_PATH_SWAP)
        return cookie

    def test_01_swap_to_unledgered_same_cookie_reinstalls_new_id(self):
        db = self._db()
        db_mod.init_db(db)
        _seed_article(db, "A1")
        db_mod.save_decision(db, "A1", "include")
        id_old = self._cookie_lib(db)
        cookie = self._cookie_of(db)
        # 同路径换入无账本同 schema_version 旧式库。
        swapped = self.base / "swapped-no-ledger.db"
        _old_style_db(swapped, stage1_keys=("B1", "B2"), stage2_keys=("B3",))
        with closing(_raw_conn(swapped)) as raw, raw:
            raw.execute("PRAGMA schema_version = %d" % int(cookie))
        _swap_file(swapped, db)
        conn = self._pc(db)
        try:
            self.assertEqual(ledger.ledger_state(conn), "installed",
                             "探测失败必须回完整自愈并重装: " + TAG_PATH_SWAP)
            id_new = ledger.library_id(conn)
            self._assert_hex32(id_new, TAG_PATH_SWAP)
            self.assertNotEqual(id_new, id_old,
                                "换入库不得沿用旧 library_id: " + TAG_PATH_SWAP)
            self.assertEqual(self._revs(conn, STAGE1), {"B1": 1, "B2": 1},
                             "重装 seed=1: " + TAG_PATH_SWAP)
            self.assertEqual(self._revs(conn, STAGE2), {"B3": 1}, TAG_PATH_SWAP)
        finally:
            conn.close()
        self.assertIn(db.resolve(), db_mod._LEDGER_READY,
                      "重装成功后重新置 READY: " + TAG_PATH_SWAP)
        db_mod.save_decision(db, "B1", "exclude")  # 个人流正常。

    def _cookie_lib(self, db):
        with closing(self._pc(db)) as conn:
            return ledger.library_id(conn)

    def test_02_swap_to_broken_same_cookie_not_trusted(self):
        db = self._db()
        db_mod.init_db(db)
        _seed_article(db, "A1")
        db_mod.save_decision(db, "A1", "include")
        id_old = self._cookie_lib(db)
        cookie = self._cookie_of(db)
        broken = self.base / "swapped-broken.db"
        _backup_db(db, broken)
        with closing(_raw_conn(broken)) as raw, raw:
            for name in ("decisions_tvr_insert", "decisions_tvr_update",
                         "decisions_tvr_delete", "stage2_decisions_tvr_insert",
                         "stage2_decisions_tvr_update", "stage2_decisions_tvr_delete"):
                raw.execute("DROP TRIGGER IF EXISTS %s" % name)
            raw.execute("PRAGMA schema_version = %d" % int(cookie))
        _swap_file(broken, db)
        # 反复活用循环：重复连接+个人写，坏态持续、绝不自动补好、绝不沿用旧 ID。
        for round_no in range(3):
            conn = self._pc(db)
            try:
                self.assertEqual(ledger.ledger_state(conn), "broken",
                                 "round%d meta 在而触发器坏必须 broken: %s"
                                 % (round_no, TAG_PATH_SWAP))
                with self.assertRaises(ledger.VoteLedgerUnavailable) as caught:
                    ledger.library_id(conn)
                self.assertIn(caught.exception.reason, ledger.REASONS, TAG_PATH_SWAP)
            finally:
                conn.close()
            self.assertEqual(len(_ledger_trigger_names(db)), 0,
                             "round%d _connect 不得静默重建触发器: %s"
                             % (round_no, TAG_PATH_SWAP))
            db_mod.save_decision(db, "A1", "exclude", notes="round%d" % round_no)
        self.assertNotIn(db.resolve(), db_mod._LEDGER_READY,
                         "broken 必须清 READY: " + TAG_PATH_SWAP)
        with closing(_raw_conn(db)) as raw:
            self.assertEqual(
                raw.execute("SELECT library_id FROM team_vote_revision_meta").fetchone()[0],
                id_old, "broken 不轮换身份（轮换须独立授权，本阶段不提供）: " + TAG_PATH_SWAP)

    def test_03_intact_reopen_keeps_id_no_reinstall(self):
        db = self._installed_db()
        _seed_article(db, "A1")
        db_mod.save_decision(db, "A1", "include")
        id1 = self._cookie_lib(db)
        db_mod._MIGRATED_SCHEMAS.clear()
        db_mod._LEDGER_READY.clear()
        id2 = self._cookie_lib(db)
        self.assertEqual(id1, id2, "完好库重开不得换身份（硬化不得误伤）: " + TAG_PATH_SWAP)
        with closing(_raw_conn(db)) as raw:
            self.assertEqual(raw.execute(
                "SELECT count(*) FROM team_vote_revision_meta").fetchone()[0], 1,
                TAG_PATH_SWAP)


class T6_FailClosedBrokenTests(_LedgerBox):
    """G6：坏 meta/trigger/row fail-closed；同名错误定义必 broken；broken 不自补；
    unmigrated 与 path_absent 分开。"""

    TRIGGERS = ("decisions_tvr_insert", "decisions_tvr_update", "decisions_tvr_delete",
                "stage2_decisions_tvr_insert", "stage2_decisions_tvr_update",
                "stage2_decisions_tvr_delete")

    def _broken_by_drop(self, name="member.db", victim="decisions_tvr_insert") -> Path:
        db = self._db(name)
        db_mod.init_db(db)
        _seed_article(db, "A1")
        db_mod.save_decision(db, "A1", "include")
        with closing(_raw_conn(db)) as raw, raw:
            raw.execute("DROP TRIGGER %s" % victim)
        return db

    def _assert_unavailable(self, db, stage=STAGE1, reason=None, tag=TAG_FAIL_CLOSED):
        conn = self._pc(db)
        try:
            self.assertEqual(ledger.ledger_state(conn), "broken", tag)
            with self.assertRaises(ledger.VoteLedgerUnavailable) as caught:
                ledger.library_id(conn)
            exc = caught.exception
            self.assertEqual(exc.status, 503, tag)
            self.assertEqual(exc.code, "team.flow.revision_unavailable", tag)
            self.assertEqual(exc.message_key, "team.flow.revision_unavailable", tag)
            self.assertIn("member", exc.params, tag)
            self.assertIn("reason", exc.params, tag)
            self.assertIn(exc.reason, ledger.REASONS, tag)
            with self.assertRaises(ledger.VoteLedgerUnavailable) as caught2:
                ledger.vote_snapshot(conn, stage)
            exc2 = caught2.exception
            self.assertIn(exc2.reason, ledger.REASONS, tag)
            if reason is not None:
                self.assertEqual(exc2.reason, reason, tag)
        finally:
            conn.close()

    def test_01_trigger_dropped_broken_not_repaired(self):
        db = self._broken_by_drop()
        # 损坏面精确记录：恰缺被 DROP 的那一个，其余五个原定义保留；meta 身份经
        # 裸 RO 读取（broken 态 library_id 拒绝=产品 fail-closed 正确行为）。
        remaining_before = _ledger_trigger_names(db)
        self.assertEqual(len(remaining_before), 5, TAG_FAIL_CLOSED)
        with closing(_raw_conn(db)) as raw:
            meta_before = raw.execute(
                "SELECT count(*),library_id FROM team_vote_revision_meta").fetchall()
        self.assertEqual(len(meta_before[0][1]), 32, TAG_FAIL_CLOSED)
        self._assert_unavailable(db, reason="trigger_missing")
        # broken 不以原 library_id 自动补好：install/init_db 皆不得静默修复——
        # 触发器集合精确等于损坏后集合（缺的那个不得回归）、meta 单行同身份。
        with closing(self._pc(db)) as conn:
            with self.assertRaises(ledger.VoteLedgerUnavailable,
                                   msg="broken 下 install 必须拒修: " + TAG_FAIL_CLOSED):
                ledger.install(conn)
        db_mod.init_db(db)
        self.assertEqual(_ledger_trigger_names(db), remaining_before,
                         "init_db 不得补齐 broken 触发器（集合精确不变）: " + TAG_FAIL_CLOSED)
        with closing(_raw_conn(db)) as raw:
            self.assertEqual(
                raw.execute("SELECT count(*),library_id FROM team_vote_revision_meta"
                            ).fetchall(),
                meta_before, "meta 单行同身份不被改写: " + TAG_FAIL_CLOSED)
        self._assert_unavailable(db, reason="trigger_missing")

    def test_02_same_name_wrong_body_broken(self):
        """同名触发器但定义不符受支持规范（无 upsert 直插）→ 必 broken（不只核名字）。"""
        db = self._db()
        db_mod.init_db(db)
        _seed_article(db, "A1")
        db_mod.save_decision(db, "A1", "include")
        with closing(_raw_conn(db)) as raw, raw:
            raw.execute("DROP TRIGGER decisions_tvr_insert")
            raw.execute(
                "CREATE TRIGGER decisions_tvr_insert AFTER INSERT ON decisions BEGIN "
                "INSERT INTO team_vote_revisions(stage,item_key,revision) "
                "VALUES('stage1',NEW.zotero_key,1); END")
        self._assert_unavailable(db, reason="trigger_missing")

    def test_03_same_name_cross_stage_body_broken(self):
        """同名且形似 upsert、但 stage 常量错位（decisions 触发器写 stage2）→ broken。"""
        db = self._db()
        db_mod.init_db(db)
        _seed_article(db, "A1")
        db_mod.save_decision(db, "A1", "include")
        with closing(_raw_conn(db)) as raw, raw:
            raw.execute("DROP TRIGGER decisions_tvr_insert")
            raw.execute(
                "CREATE TRIGGER decisions_tvr_insert AFTER INSERT ON decisions BEGIN "
                "INSERT INTO team_vote_revisions(stage,item_key,revision) "
                "VALUES('stage2',NEW.zotero_key,1) "
                "ON CONFLICT(stage,item_key) DO UPDATE SET revision=revision+1; END")
        self._assert_unavailable(db, reason="trigger_missing")

    def test_04_meta_row_missing_broken(self):
        db = self._db()
        db_mod.init_db(db)
        _seed_article(db, "A1")
        db_mod.save_decision(db, "A1", "include")
        with closing(_raw_conn(db)) as raw, raw:
            raw.execute("DELETE FROM team_vote_revision_meta")
        # meta 表在而单例行缺 = 行不完整 → broken（非 absent）。
        conn = self._pc(db)
        try:
            self.assertEqual(ledger.ledger_state(conn), "broken",
                             "meta 表在而单例行缺必须 broken: " + TAG_FAIL_CLOSED)
        finally:
            conn.close()

    def test_05_meta_format_and_id_variants_broken(self):
        db = self._db()
        db_mod.init_db(db)
        _seed_article(db, "A1")
        db_mod.save_decision(db, "A1", "include")
        lib = self._pc_call(db, ledger.library_id)
        weak_ddl = ("CREATE TABLE team_vote_revision_meta("
                    "singleton INTEGER PRIMARY KEY, format_version INTEGER, "
                    "library_id TEXT)")
        weak_cols = "(singleton,format_version,library_id)"
        # (a) format_version 不受支持（当前合同 format 2 → 用常量+1 构造不支持值，
        #     不硬编码世代号）：重建无 CHECK meta 写入不受支持版本。
        rebuilt = self.base / "meta-badformat.db"
        self._rebuild_meta(db, rebuilt, weak_ddl, weak_cols,
                           "(1, %d, '%s')" % (ledger.LEDGER_FORMAT_VERSION + 1, lib))
        self._assert_unavailable(rebuilt, reason="meta_corrupt")
        # (b) library_id 非 32hex（合法 32hex 合同）。
        rebuilt2 = self.base / "meta-badid.db"
        self._rebuild_meta(db, rebuilt2, weak_ddl, weak_cols,
                           "(1, %d, '%s')" % (ledger.LEDGER_FORMAT_VERSION, "z" * 32))
        self._assert_unavailable(rebuilt2, reason="meta_corrupt")
        # (c) 双 singleton 行（唯一合法 singleton 合同；无 CHECK 时可构造）。
        rebuilt3 = self.base / "meta-dup.db"
        self._rebuild_meta(db, rebuilt3, weak_ddl, weak_cols,
                           "(1, %d, '%s'),(2, %d, '%s')"
                           % (ledger.LEDGER_FORMAT_VERSION, lib,
                              ledger.LEDGER_FORMAT_VERSION, lib))
        self._assert_unavailable(rebuilt3, reason="meta_corrupt")
        # (d) 守恒计数值非法（负数；无 CHECK 弱表可构造，真实损坏形态）→ meta_corrupt
        #     （Sol：坏 counter 归 meta_corrupt）。
        rebuilt4 = self.base / "meta-badcounter.db"
        self._rebuild_meta(
            db, rebuilt4,
            "CREATE TABLE team_vote_revision_meta("
            "singleton INTEGER PRIMARY KEY, format_version INTEGER, library_id TEXT, "
            "seeded_stage1 INTEGER, seeded_stage2 INTEGER, advances INTEGER)",
            "(singleton,format_version,library_id,seeded_stage1,seeded_stage2,advances)",
            "(1, %d, '%s', 0, 0, -3)" % (ledger.LEDGER_FORMAT_VERSION, lib))
        self._assert_unavailable(rebuilt4, reason="meta_corrupt")

    def _pc_call(self, db, fn, *args):
        with closing(self._pc(db)) as conn:
            return fn(conn, *args)

    def _rebuild_meta(self, src, dst, ddl, columns, values):
        """复制库并把 meta 表重建为指定（可损坏）形态——制造 SQL CHECK 拦不出的
        历史坏态（真实世界损坏形态，验证结构验证而非依赖 CHECK 兜底）。"""
        _backup_db(src, dst)
        with closing(_raw_conn(dst)) as raw, raw:
            raw.execute("DROP TABLE team_vote_revision_meta")
            raw.execute(ddl)
            raw.execute("INSERT INTO team_vote_revision_meta%s VALUES %s"
                        % (columns, values))

    def test_06_revisions_table_missing_broken_no_rebuild(self):
        db = self._db()
        db_mod.init_db(db)
        _seed_article(db, "A1")
        db_mod.save_decision(db, "A1", "include")
        with closing(_raw_conn(db)) as raw, raw:
            raw.execute("DROP TABLE team_vote_revisions")
        self._assert_unavailable(db)
        db_mod.init_db(db)
        with closing(_raw_conn(db)) as raw:
            self.assertIsNone(raw.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='team_vote_revisions'").fetchone(),
                "revisions 表缺失不得被静默重建（broken 不自补）: " + TAG_FAIL_CLOSED)

    def test_07_partial_objects_without_meta_broken_not_absent(self):
        db = self._db()
        _old_style_db(db, stage1_keys=("W1",), stage2_keys=())
        with closing(_raw_conn(db)) as raw, raw:
            raw.execute(
                "CREATE TABLE team_vote_revisions(stage TEXT, item_key TEXT, "
                "revision INTEGER, PRIMARY KEY(stage,item_key)) WITHOUT ROWID")
            raw.execute(
                "CREATE TRIGGER decisions_tvr_insert AFTER INSERT ON decisions BEGIN "
                "INSERT INTO team_vote_revisions(stage,item_key,revision) "
                "VALUES('stage1',NEW.zotero_key,1); END")
        conn = self._pc(db)
        try:
            self.assertEqual(ledger.ledger_state(conn), "broken",
                             "部分对象存在而无 meta = broken（absent/broken 分清）: "
                             + TAG_FAIL_CLOSED)
        finally:
            conn.close()

    def test_08_row_missing_fail_closed(self):
        db = self._db()
        db_mod.init_db(db)
        _seed_article(db, "A1")
        db_mod.save_decision(db, "A1", "include")
        with closing(_raw_conn(db)) as raw, raw:
            raw.execute("DELETE FROM team_vote_revisions WHERE stage=? AND item_key=?",
                        (STAGE1, "A1"))
        conn = self._pc(db)
        try:
            # 票行缺账行（同时构成 Σ 缺口）→ 可信消费拒读；归因可 row_missing 或
            # conservation_mismatch（合同内两 reason 都覆盖该损坏形状）。
            self.assertEqual(ledger.ledger_state(conn), "broken",
                             "账行被删（Σ 缺口）必须 broken: " + TAG_FAIL_CLOSED)
            with self.assertRaises(ledger.VoteLedgerUnavailable) as caught:
                ledger.vote_snapshot(conn, STAGE1)
            exc = caught.exception
            self.assertIn(exc.reason, REASONS_DAMAGE_DELETE, TAG_FAIL_CLOSED)
            self.assertIn("member", exc.params, TAG_FAIL_CLOSED)
        finally:
            conn.close()

    def test_09_unmigrated_vs_path_absent_distinct(self):
        old_db = self._db()
        _old_style_db(old_db, stage1_keys=("U1",), stage2_keys=())
        # 裸 RO 观测（不触产品 _connect——其 hook 会合法懒安装改变事实）。
        raw = _raw_conn(old_db)
        try:
            self.assertEqual(ledger.ledger_state(raw), "absent", TAG_FAIL_CLOSED)
            with self.assertRaises(ledger.VoteLedgerUnavailable) as caught:
                ledger.vote_snapshot(raw, STAGE1)
            self.assertEqual(caught.exception.reason, "unmigrated",
                             "旧库未装明确 unmigrated: " + TAG_FAIL_CLOSED)
            with self.assertRaises(ledger.VoteLedgerUnavailable) as caught2:
                ledger.library_id(raw)
            self.assertEqual(caught2.exception.reason, "unmigrated", TAG_FAIL_CLOSED)
        finally:
            raw.close()
        # 正面：产品 _connect 触达即合法懒安装（absent → installed，seed=1）。
        conn = self._pc(old_db)
        try:
            self.assertEqual(ledger.ledger_state(conn), "installed", TAG_FAIL_CLOSED)
            self.assertEqual(self._revs(conn, STAGE1), {"U1": 1}, TAG_FAIL_CLOSED)
        finally:
            conn.close()
        missing = self.base / "absent-path.db"
        with self.assertRaises(ledger.VoteLedgerUnavailable) as caught3:
            ledger.install_in_db(missing)
        self.assertEqual(caught3.exception.reason, "path_absent",
                         "文件不存在与未迁移严格分开: " + TAG_FAIL_CLOSED)
        self.assertFalse(missing.exists(), TAG_FAIL_CLOSED)


class T7_SnapshotConsistencyTests(_LedgerBox):
    """G6b：同一 SQLite snapshot 读验证+ID+票+revision；caller 事务加入不终结。"""

    def test_01_snapshot_matches_tables_both_stages(self):
        db = self._db()
        db_mod.init_db(db)
        _seed_article(db, "K1")
        _seed_article(db, "K2")
        _seed_article(db, "K3")
        db_mod.save_decision(db, "K1", "include", notes="n1", tags="t1")
        db_mod.save_decision(db, "K2", "exclude", exclusion_reason="r2")
        db_mod.save_decision(db, "K1", "exclude", notes="n1b")
        fulltext_mod.save_stage2(db, "K3", "include")
        fulltext_mod.save_stage2(db, "K1", "maybe")
        conn = self._pc(db)
        try:
            lib = ledger.library_id(conn)
            id1, snap1 = ledger.vote_snapshot(conn, STAGE1)
            id2, snap2 = ledger.vote_snapshot(conn, STAGE2)
            self.assertEqual(id1, lib, TAG_SNAPSHOT)
            self.assertEqual(id2, lib, TAG_SNAPSHOT)
            revs1, revs2 = self._revs(conn, STAGE1), self._revs(conn, STAGE2)
            self.assertEqual(set(snap1), set(revs1), TAG_SNAPSHOT)
            self.assertEqual(set(snap2), set(revs2), TAG_SNAPSHOT)
            with closing(_raw_conn(db)) as raw:
                for stage, snap, table, revs_map in (
                        (STAGE1, snap1, "decisions", revs1),
                        (STAGE2, snap2, "stage2_decisions", revs2)):
                    rows = {r[0]: r for r in raw.execute(
                        "SELECT zotero_key,decision,exclusion_reason,notes,tags,"
                        "screened_at,updated_at FROM %s" % table).fetchall()}
                    self.assertEqual(set(snap), set(rows), TAG_SNAPSHOT)
                    for key, row in snap.items():
                        self.assertEqual(row["revision"], revs_map[key],
                                         "同 snapshot revision 必须与账一致(%s.%s): %s"
                                         % (stage, key, TAG_SNAPSHOT))
                        for col_index, col in enumerate(DECISION_COLUMNS):
                            self.assertEqual(row[col], rows[key][col_index],
                                             "同 snapshot 票面必须与表一致(%s.%s): %s"
                                             % (stage, col, TAG_SNAPSHOT))
        finally:
            conn.close()

    def test_02_joins_caller_txn_without_ending_it(self):
        db = self._installed_db()
        _seed_article(db, "A1")
        raw = _raw_conn(db)
        try:
            raw.execute("BEGIN IMMEDIATE")
            raw.execute(
                "INSERT INTO decisions(zotero_key,decision,screened_at,updated_at) "
                "VALUES ('A1','include','2025-01-01T00:00:00','2025-01-01T00:00:00')")
            lib, snap = ledger.vote_snapshot(raw, STAGE1)
            self.assertIn("A1", snap,
                           "caller 现事务未提交写入必须可见（同快照加入）: " + TAG_SNAPSHOT)
            self.assertTrue(raw.in_transaction,
                            "vote_snapshot 不得终结调用方事务: " + TAG_SNAPSHOT)
            raw.rollback()
            self.assertFalse(raw.in_transaction)
            conn = self._pc(db)
            try:
                _, snap_after = ledger.vote_snapshot(conn, STAGE1)
                self.assertNotIn("A1", snap_after,
                                 "回滚后不得残留幻影票: " + TAG_SNAPSHOT)
            finally:
                conn.close()
        finally:
            raw.close()

    def test_03_own_read_cleanup_no_txn_left(self):
        db = self._installed_db()
        _seed_article(db, "A1")
        db_mod.save_decision(db, "A1", "include")
        conn = self._pc(db)
        try:
            self.assertFalse(conn.in_transaction)
            ledger.vote_snapshot(conn, STAGE1)
            ledger.revisions(conn, STAGE2)
            self.assertFalse(conn.in_transaction,
                             "自有读事务只收尾自身: " + TAG_SNAPSHOT)
        finally:
            conn.close()


class T8_OverlongKeyMigrationTests(_LedgerBox):
    """G7：先装 ledger 再超长键迁移：旧 key 墓碑与新 key 共存；item_key 不被误迁移。"""

    def _overlong(self) -> str:
        return "OV" + "x" * (KEY_MAX_LEN + 10)

    def test_01_tombstone_and_new_key_coexist(self):
        db = self._installed_db()
        bad = self._overlong()
        _seed_article(db, bad)
        db_mod.save_decision(db, bad, "include")
        revs_before = _ledger_revisions_raw(db, STAGE1)
        self.assertIn(bad, revs_before, TAG_OVERLONG)
        rev_bad_before = revs_before[bad]
        db_mod.init_db(db)  # 挂点序：ledger 已装 → 本轮 migrate_overlong_keys 迁移。
        with closing(_raw_conn(db)) as raw:
            arts = {r[0] for r in raw.execute("SELECT zotero_key FROM articles").fetchall()}
            dec = {r[0] for r in raw.execute("SELECT zotero_key FROM decisions").fetchall()}
        self.assertNotIn(bad, arts, "迁移后 articles 旧键必须改名: " + TAG_OVERLONG)
        self.assertNotIn(bad, dec, TAG_OVERLONG)
        self.assertEqual(len(arts), 1, TAG_OVERLONG)
        new_key = next(iter(arts))
        self.assertLessEqual(len(new_key), KEY_MAX_LEN, TAG_OVERLONG)
        revs_after = _ledger_revisions_raw(db, STAGE1)
        # 旧 key 墓碑仍以原键名留存（item_key 不在迁移扫描白名单——不被搬走）。
        self.assertIn(bad, revs_after,
                      "ledger 旧 key 墓碑不得被超长键迁移清除/改写: " + TAG_OVERLONG)
        self.assertGreaterEqual(revs_after[bad], rev_bad_before,
                                "墓碑 revision 只进不退: " + TAG_OVERLONG)
        # 新 key 有自己的账行（迁移 UPDATE 触发器记账）。
        self.assertIn(new_key, revs_after, TAG_OVERLONG)
        self.assertGreaterEqual(revs_after[new_key], 1, TAG_OVERLONG)
        self.assertNotEqual(bad, new_key, TAG_OVERLONG)

    def test_02_migration_idempotent_zero_advance(self):
        db = self._installed_db()
        bad = self._overlong()
        _seed_article(db, bad)
        db_mod.save_decision(db, bad, "include")
        db_mod.init_db(db)  # 第一次迁移。
        revs_1 = _ledger_revisions_raw(db, STAGE1)
        db_mod.init_db(db)  # 第二次：幂等零推进。
        revs_2 = _ledger_revisions_raw(db, STAGE1)
        self.assertEqual(revs_2, revs_1,
                         "迁移幂等，账本零额外推进: " + TAG_OVERLONG)


class T9_BinaryResetLedgerTests(_LedgerBox):
    """G8：binary_reset/maybe 删除墓碑；幂等零推进；同事务回滚无幻影墓碑。"""

    def _reset_ready_db(self, name="member.db") -> Path:
        """binary_reset.migrate 前置 = 真实产品迁移链：review_workflow.migrate 建立
        workflow_migrations 基线（否则 binary_reset 按合同报 unmigrated 跳过）。"""
        db = self._installed_db(name)
        workflow_mod.migrate(db)
        return db

    def test_01_reset_maybe_tombstones(self):
        db = self._reset_ready_db()
        _seed_article(db, "A-MAYBE")
        _seed_article(db, "B-KEEP")
        db_mod.save_decision(db, "A-MAYBE", "maybe")
        db_mod.save_decision(db, "B-KEEP", "include")
        fulltext_mod.save_stage2(db, "A-MAYBE", "include")   # 级联删（初筛 maybe）
        fulltext_mod.save_stage2(db, "B-KEEP", "maybe")      # stage2 maybe 直删
        revs_before = {STAGE1: _ledger_revisions_raw(db, STAGE1),
                       STAGE2: _ledger_revisions_raw(db, STAGE2)}
        summary = binary_reset_mod.migrate(db)
        self.assertTrue(summary.get("applied"), TAG_BINARY_RESET)
        revs_after = {STAGE1: _ledger_revisions_raw(db, STAGE1),
                      STAGE2: _ledger_revisions_raw(db, STAGE2)}
        # 被删键全部得墓碑且不小于删除前。
        for stage, key in ((STAGE1, "A-MAYBE"), (STAGE2, "A-MAYBE"),
                           (STAGE2, "B-KEEP")):
            self.assertIn(key, revs_after[stage],
                          "被删票必须留墓碑(%s.%s): %s" % (stage, key, TAG_BINARY_RESET))
            self.assertGreaterEqual(revs_after[stage][key],
                                    revs_before[stage].get(key, 1), TAG_BINARY_RESET)
        # 保留票账行原样。
        self.assertEqual(revs_after[STAGE1]["B-KEEP"], revs_before[STAGE1]["B-KEEP"],
                         TAG_BINARY_RESET)
        with closing(_raw_conn(db)) as raw:
            self.assertIsNone(raw.execute(
                "SELECT 1 FROM decisions WHERE decision='maybe'").fetchone())
            self.assertIsNone(raw.execute(
                "SELECT 1 FROM stage2_decisions WHERE zotero_key='A-MAYBE'").fetchone())

    def test_02_reset_idempotent_zero_advance(self):
        db = self._reset_ready_db()
        _seed_article(db, "A-MAYBE")
        db_mod.save_decision(db, "A-MAYBE", "maybe")
        binary_reset_mod.migrate(db)
        revs_1 = {STAGE1: _ledger_revisions_raw(db, STAGE1),
                  STAGE2: _ledger_revisions_raw(db, STAGE2)}
        summary2 = binary_reset_mod.migrate(db)
        self.assertFalse(summary2.get("applied"), TAG_BINARY_RESET)
        revs_2 = {STAGE1: _ledger_revisions_raw(db, STAGE1),
                  STAGE2: _ledger_revisions_raw(db, STAGE2)}
        self.assertEqual(revs_2, revs_1, "重置幂等零推进: " + TAG_BINARY_RESET)

    def test_03_rollback_leaves_no_phantom_tombstone(self):
        db = self._reset_ready_db()
        _seed_article(db, "A1")
        db_mod.save_decision(db, "A1", "include")
        revs_before = _ledger_revisions_raw(db, STAGE1)
        with closing(_raw_conn(db)) as raw:
            raw.execute("BEGIN IMMEDIATE")
            raw.execute("DELETE FROM decisions WHERE zotero_key='A1'")
            # 同一未结事务内：DELETE 的墓碑记账已随业务写发生（票与账本同事务）。
            rev_in_txn = dict(raw.execute(
                "SELECT item_key,revision FROM team_vote_revisions WHERE stage=?",
                (STAGE1,)).fetchall())
            self.assertGreater(rev_in_txn["A1"], revs_before["A1"],
                               "DELETE 记账必须与业务写同事务发生: " + TAG_BINARY_RESET)
            raw.rollback()
        self.assertEqual(_ledger_revisions_raw(db, STAGE1), revs_before,
                         "回滚后无幻影墓碑（账本与票同事务）: " + TAG_BINARY_RESET)


class T10_PersonalCompatBoundaryTests(_LedgerBox):
    """G9：挂点局部降级不吞错；个人流可继续可重试；坏态检出后无静默复活（重复检测）。"""

    def test_01_install_fail_degrade_then_retry_success(self):
        db = self._db()
        with _fail_execute_at(_META_INSERT_U):
            db_mod.init_db(db)  # 降级：不得上抛（挂点局部 catch 合同）。
        # 失败后全部裸 RO 观测（不走产品 _connect——其 hook 会合法懒安装改变事实）。
        with closing(_raw_conn(db)) as raw:
            self.assertIsNotNone(raw.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='decisions'").fetchone(),
                "基础 _SCHEMA 不受账本失败影响: " + TAG_COMPAT)
            self.assertIsNone(raw.execute(
                "SELECT 1 FROM sqlite_master WHERE name='team_vote_revision_meta'"
                ).fetchone(), "失败后无账本对象（裸RO）: " + TAG_COMPAT)
        self.assertEqual(_ledger_object_rows(db), {}, TAG_COMPAT)
        self.assertNotIn(db.resolve(), db_mod._LEDGER_READY,
                         "失败不置 READY: " + TAG_COMPAT)
        # 个人原业务照常：裸连接种文章（不经 _connect 注入状态变化）。
        with closing(_raw_conn(db)) as raw, raw:
            raw.execute("INSERT OR IGNORE INTO articles(zotero_key,title) "
                        "VALUES ('A1','s1a fixture article')")
        db_mod.save_decision(db, "A1", "include")  # 成功即个人流未受降级影响。
        fulltext_mod.save_stage2(db, "A1", "exclude")
        self.assertEqual(len(db_mod.list_decisions(db)), 1, TAG_COMPAT)
        # 该连接期 hook 已合法懒安装（READY 未闩死、每次连接结构验证）：installed+seed=1。
        conn = self._pc(db)
        try:
            self.assertEqual(ledger.ledger_state(conn), "installed", TAG_COMPAT)
            self._assert_hex32(ledger.library_id(conn), TAG_COMPAT)
            self.assertEqual(self._revs(conn, STAGE1), {"A1": 1},
                             "懒安装 seed=1（降级期个人票入基线）: " + TAG_COMPAT)
        finally:
            conn.close()
        # 后次 init_db 幂等重试成功（身份稳定）。
        db_mod.init_db(db)
        conn = self._pc(db)
        try:
            self.assertEqual(ledger.ledger_state(conn), "installed", TAG_COMPAT)
        finally:
            conn.close()

    def test_02_broken_no_autorepair_loop_with_writes(self):
        """insert 触发器缺失期间，新票写入不得产生幻影账行；坏态持续不自动补；
        重复多轮（不写"只检一次"弱测）。UPDATE 触发器仍在，已有票更新照常记账。"""
        db = self._db()
        db_mod.init_db(db)
        _seed_article(db, "A1")
        db_mod.save_decision(db, "A1", "include")
        rev_a1_before = _ledger_revisions_raw(db, STAGE1)["A1"]
        with closing(_raw_conn(db)) as raw, raw:
            raw.execute("DROP TRIGGER decisions_tvr_insert")
        for round_no in range(3):
            key = "BL%d" % round_no
            _seed_article(db, key)
            db_mod.save_decision(db, key, "include")
            conn = self._pc(db)
            try:
                self.assertEqual(ledger.ledger_state(conn), "broken",
                                 "round%d 持续 broken（不写只检一次弱测）: %s"
                                 % (round_no, TAG_COMPAT))
                with self.assertRaises(ledger.VoteLedgerUnavailable):
                    ledger.library_id(conn)
            finally:
                conn.close()
            self.assertEqual(len(_ledger_trigger_names(db)), 5,
                             "round%d 无静默补触发器: %s" % (round_no, TAG_COMPAT))
            with closing(_raw_conn(db)) as raw:
                self.assertIsNone(raw.execute(
                    "SELECT 1 FROM team_vote_revisions WHERE stage=? AND item_key=?",
                    (STAGE1, key)).fetchone(),
                    "insert 触发器坏期间新票不得产生幻影账行: " + TAG_COMPAT)
        # 既有票 UPDATE 经仍完好的 update 触发器照常单调记账。
        db_mod.save_decision(db, "A1", "exclude")
        self.assertGreater(_ledger_revisions_raw(db, STAGE1)["A1"], rev_a1_before,
                           TAG_COMPAT)

    def test_03_damage_detection_then_ordinary_write_never_oldid_trusted(self):
        """主控 PHASE2 真实 gap 强断言（Sol 守恒验收第 1 项，重复检测）：

        已复现反例形状——票 r0=3（三笔真实写）→ 账行被删 → vote_snapshot 检出
        （row_missing/conservation_mismatch 域）→ 一笔普通个人 UPDATE 经合同
        UPDATE 触发器重建账行（rev=2 < r0=3，历史缺口不可补）→ 守恒合同下 Σ 缺口
        永久保留：全部可信入口（state/library_id/revisions/vote_snapshot/幂等
        install）此后持续拒绝，旧 library_id 不得重获可信。多轮多键多次复检。"""
        old_lib = None
        for key in ("R1", "R2"):
            # 每损坏 key 独立健康 fixture（Sol：首 key 全库 broken 后不得再用可信
            # API 取第二 key 前置——本构造两 key 互不共享库，前置读取各自健康态）。
            db = self._db("damage-%s.db" % key)
            db_mod.init_db(db)
            _seed_article(db, key)
            db_mod.save_decision(db, key, "include")
            db_mod.save_decision(db, key, "exclude")  # 存 2 次 → r0=3（1 + UPDATE 双 upsert +2）
            conn = self._pc(db)
            try:
                r0 = self._revs(conn, STAGE1)[key]
                self.assertEqual(r0, 3, "前置：两笔写 r0=3（同 key UPDATE 双记账）: "
                                 + TAG_COMPAT)
                old_lib = ledger.library_id(conn)
            finally:
                conn.close()
            # 损坏注入（模拟未记账丢失）：删已有票的账行。
            with closing(_raw_conn(db)) as raw, raw:
                raw.execute("DELETE FROM team_vote_revisions WHERE stage=? AND item_key=?",
                            (STAGE1, key))
            # 检出：损坏必须先被可信消费观察到，否则注入无效。
            conn = self._pc(db)
            try:
                with self.assertRaises(ledger.VoteLedgerUnavailable) as caught:
                    ledger.vote_snapshot(conn, STAGE1)
                self.assertIn(caught.exception.reason, REASONS_DAMAGE_DELETE,
                              "损坏必须被检出（key=%s）: %s" % (key, TAG_COMPAT))
            finally:
                conn.close()
            # 损坏检出后仅普通个人写（无任何修复/授权动作）。
            db_mod.save_decision(db, key, "include")
            self._assert_all_trust_entries_reject(db, STAGE1, old_lib, key, 1)
            # 重复：再次普通写后再复检（不写只检一次弱测）。
            db_mod.save_decision(db, key, "exclude")
            self._assert_all_trust_entries_reject(db, STAGE1, old_lib, key, 2)
            # 重复：全新产品连接再复检。
            self._assert_all_trust_entries_reject(db, STAGE1, old_lib, key, 3)
            # 身份不静默轮换（本阶段合同）：meta 原文保持旧 ID；轮换须独立授权。
            with closing(_raw_conn(db)) as raw:
                self.assertEqual(
                    raw.execute("SELECT library_id FROM team_vote_revision_meta"
                                ).fetchone()[0],
                    old_lib, "meta 身份不得被普通写静默轮换: " + TAG_COMPAT)

    def test_04_base_schema_failure_propagates(self):
        """挂点 catch 范围合同：只包账本安装/验证——基础 _SCHEMA 失败必须上抛。

        确定性破坏子：预建缺列 articles 表 → _connect 自愈链不受影响 →
        _SCHEMA 的 CREATE INDEX idx_articles_doi ON articles(doi) 因缺列失败
        → init_db 照常上抛（schema 缺失不可降级，db.py:711 既有合同）。"""
        db = self._db()
        with closing(_raw_conn(db)) as raw, raw:
            raw.execute("CREATE TABLE articles(id INTEGER PRIMARY KEY)")
        with self.assertRaises(sqlite3.Error,
                               msg="基础 schema 失败不得被挂点吞掉: " + TAG_COMPAT):
            db_mod.init_db(db)
        self.assertEqual(_ledger_object_rows(db), {}, TAG_COMPAT)

    def test_05_business_errors_propagate(self):
        """原业务错误必须穿透（挂点 catch 不覆盖业务 SQL）。"""
        db = self._installed_db()
        _seed_article(db, "A1")
        with self.assertRaises(ValueError,
                               msg="非法 decision 校验必须照常上抛: " + TAG_COMPAT):
            db_mod.save_decision(db, "A1", "bogus-decision")
        with self.assertRaises(sqlite3.Error,
                               msg="外键违约必须照常上抛: " + TAG_COMPAT):
            db_mod.save_decision(db, "NO-SUCH-KEY", "include")
        with self.assertRaises(ValueError, msg=TAG_COMPAT):
            fulltext_mod.save_stage2(db, "A1", "bogus-decision")
        # 正常个人流保持。
        db_mod.save_decision(db, "A1", "include")
        self.assertEqual(len(db_mod.list_decisions(db)), 1, TAG_COMPAT)


class T11_ConservationIntegrityTests(_LedgerBox):
    """G10：Sol 守恒裁定七项验收（format2 + Σ==seeded1+seeded2+advances）。

    所有人工改值均为自有 fixture 且保持检查所需一致（健康构造各值合法且
    Σ==RHS；损坏构造如实制造缺口），不伪造产品输出。"""

    def test_01_format2_and_normal_flows_conserve(self):
        """验收 4：正常 seed/同 key 及改 key UPDATE/DELETE 重建/replay 零 SQL/
        业务 rollback 全程守恒——每步之后 ledger_state 均 installed。"""
        self.assertEqual(ledger.LEDGER_FORMAT_VERSION, 2,
                         "Sol 定稿：LEDGER_FORMAT_VERSION=2: " + TAG_CONSERVATION)
        db = self._installed_db()
        for key in ("N1", "N2", "N2B"):
            _seed_article(db, key)
        conn = self._pc(db)
        try:
            lib0 = ledger.library_id(conn)
        finally:
            conn.close()
        steps = [
            ("seed-insert", lambda: db_mod.save_decision(db, "N1", "include")),
            ("same-key-update", lambda: db_mod.save_decision(db, "N1", "exclude")),
            ("second-key", lambda: db_mod.save_decision(db, "N2", "include")),
            ("key-change", lambda: raw_key_rename(db, "N2", "N2B")),
            ("delete-rebuild", lambda: (
                raw_delete_vote(db, "N1"),
                db_mod.save_decision(db, "N1", "include"))),
            ("replay-zero", lambda: db_mod.save_decision(
                db, "N1", "include", trace_context=_ctx(request_id="req-cons-1"))),
            ("rollback-probe", lambda: raw_rollback_probe(db, "N1")),
        ]
        for step_no, (name, action) in enumerate(steps, start=1):
            action()
            conn = self._pc(db)
            try:
                self.assertEqual(ledger.ledger_state(conn), "installed",
                                 "step%d(%s) 后守恒必须成立: %s"
                                 % (step_no, name, TAG_CONSERVATION))
                self.assertEqual(ledger.library_id(conn), lib0,
                                 "step%d 正常流不得换身份: %s"
                                 % (step_no, TAG_CONSERVATION))
                ledger.vote_snapshot(conn, STAGE1)  # 可信快照随时可用。
            finally:
                conn.close()
        # replay 零 SQL 复钉（同一 request_id 重放零推进）。
        rev_before = _ledger_revisions_raw(db, STAGE1)
        db_mod.save_decision(db, "N1", "include", trace_context=_ctx(request_id="req-cons-1"))
        self.assertEqual(_ledger_revisions_raw(db, STAGE1), rev_before,
                         "重放零推进: " + TAG_CONSERVATION)

    def _big_sigma_fixture(self, name="big-sigma.db") -> Path:
        """健康构造（各持久整数合法、Σ==RHS 且 Σ>2^63-1）：

        旧式两票行库安装（seeded_stage1=2, 两行 rev=1,1）→ 加一票 k3（adv=1,
        Σ=3=2+0+1）→ fixture 定点改值保持一致：k1 rev=MAX、advances=MAX →
        Σ=MAX+1+1=MAX+2，RHS=2+0+MAX=MAX+2。SQL SUM 在此必溢出/升 REAL，
        Python 流式精确和必须正常验证为 installed（Sol：禁 SQL SUM/REAL）。"""
        db = self._db(name)
        _old_style_db(db, stage1_keys=("B1", "B2"), stage2_keys=())
        self._assert_hex32(ledger.install_in_db(db), TAG_CONSERVATION)
        _seed_article(db, "B3")
        db_mod.save_decision(db, "B3", "include")  # rev1，adv=1。
        with closing(_raw_conn(db)) as raw, raw:
            raw.execute("UPDATE team_vote_revisions SET revision=? "
                        "WHERE stage=? AND item_key=?", (INT64_MAX, STAGE1, "B1"))
            raw.execute("UPDATE team_vote_revision_meta SET advances=?", (INT64_MAX,))
        return db

    def test_02_python_exact_sum_above_64bit(self):
        """验收 5b：合法持久整数合计超 64 位 → Python 仍精确，不误报 broken。"""
        db = self._big_sigma_fixture()
        conn = self._pc(db)
        try:
            self.assertEqual(ledger.ledger_state(conn), "installed",
                             "Σ=MAX+2（各值合法）必须验证为 installed（禁 SQL SUM）: "
                             + TAG_CONSERVATION)
            self._assert_hex32(ledger.library_id(conn), TAG_CONSERVATION)
            self.assertEqual(self._revs(conn, STAGE1),
                             {"B1": INT64_MAX, "B2": 1, "B3": 1}, TAG_CONSERVATION)
            lib, snap = ledger.vote_snapshot(conn, STAGE1)
            self.assertEqual(snap["B1"]["revision"], INT64_MAX, TAG_CONSERVATION)
            self._assert_hex32(lib, TAG_CONSERVATION)
        finally:
            conn.close()

    def test_03_advances_overflow_rolls_back_business_txn(self):
        """验收 5a：advances 溢出（adv=MAX 再 +1）→ 当前票语句+trigger 失败 →
        产品写路径整体业务回滚（票/账/计数原地不动；SQLite 约束不自动回滚
        此前语句——回滚义务由产品 writer 的同事务承担，此处实测）。"""
        db = self._big_sigma_fixture()
        row_before = _decision_row(db, "B3")
        revs_before = _ledger_revisions_raw(db, STAGE1)
        adv_before = self._meta_row(db)[5]
        with self.assertRaises(sqlite3.Error,
                               msg="advances 溢出必须真实失败: " + TAG_CONSERVATION):
            db_mod.save_decision(db, "B3", "exclude", notes="adv-overflow-probe")
        self.assertEqual(_decision_row(db, "B3"), row_before,
                         "票必须整体回滚: " + TAG_CONSERVATION)
        self.assertEqual(_ledger_revisions_raw(db, STAGE1), revs_before,
                         "账必须整体回滚: " + TAG_CONSERVATION)
        self.assertEqual(self._meta_row(db)[5], adv_before,
                         "advances 必须整体回滚: " + TAG_CONSERVATION)

    def _healthy_three_votes(self, name="devalue.db") -> Path:
        db = self._db(name)
        db_mod.init_db(db)
        for key in ("D1", "D2", "D3"):
            _seed_article(db, key)
        db_mod.save_decision(db, "D1", "include")
        db_mod.save_decision(db, "D1", "exclude")  # D1 rev=3
        db_mod.save_decision(db, "D2", "include")
        db_mod.save_decision(db, "D3", "include")
        return db

    def test_04_revision_devalue_deficit_persists(self):
        """验收 3：既有 revision 降值（合法值、行仍在）→ Σ 缺口；多次普通
        INSERT/UPDATE/DELETE 后差额保持，全部可信入口持续拒绝。"""
        db = self._healthy_three_votes()
        old_lib = self._pc_call_lib(db)
        with closing(_raw_conn(db)) as raw, raw:
            raw.execute("UPDATE team_vote_revisions SET revision=2 "
                        "WHERE stage=? AND item_key=?", (STAGE1, "D1"))  # 3→2，-1 缺口
        conn = self._pc(db)
        try:
            with self.assertRaises(ledger.VoteLedgerUnavailable) as caught:
                ledger.vote_snapshot(conn, STAGE1)
            self.assertEqual(caught.exception.reason, REASON_DEVALUE,
                             "降值无缺行分量，归因 conservation_mismatch: "
                             + TAG_CONSERVATION)
        finally:
            conn.close()
        for round_no in range(3):
            db_mod.save_decision(db, "D2", "exclude")          # 普通 UPDATE
            _seed_article(db, "DX%d" % round_no)
            db_mod.save_decision(db, "DX%d" % round_no, "include")  # 普通 INSERT
            _seed_article(db, "DY%d" % round_no)
            db_mod.save_decision(db, "DY%d" % round_no, "maybe")
            raw_delete_vote(db, "DY%d" % round_no)             # 普通 DELETE（墓碑）
            self._assert_all_trust_entries_reject(db, STAGE1, old_lib,
                                                  "D1", round_no + 1)
        with closing(_raw_conn(db)) as raw:
            self.assertEqual(
                raw.execute("SELECT library_id FROM team_vote_revision_meta"
                            ).fetchone()[0],
                old_lib, "缺口保持期间身份不得轮换: " + TAG_CONSERVATION)

    def _pc_call_lib(self, db):
        with closing(self._pc(db)) as conn:
            return ledger.library_id(conn)

    def test_05_tombstone_delete_deficit_persists(self):
        """验收 3（墓碑行被删亦覆盖）：先产生墓碑，再删墓碑账行 → Σ 缺口；
        多次普通写后差额保持。"""
        db = self._db()
        db_mod.init_db(db)
        _seed_article(db, "T1")
        _seed_article(db, "T2")
        db_mod.save_decision(db, "T1", "include")
        raw_delete_vote(db, "T1")  # T1 墓碑 rev=2
        old_lib = self._pc_call_lib(db)
        self.assertIn("T1", _ledger_revisions_raw(db, STAGE1),
                      "前置：墓碑行存在: " + TAG_CONSERVATION)
        with closing(_raw_conn(db)) as raw, raw:
            raw.execute("DELETE FROM team_vote_revisions WHERE stage=? AND item_key=?",
                        (STAGE1, "T1"))  # 删墓碑账行 → Σ 缺口
        for round_no in range(3):
            db_mod.save_decision(db, "T2", "exclude")
            _seed_article(db, "TN%d" % round_no)
            db_mod.save_decision(db, "TN%d" % round_no, "include")
            self._assert_all_trust_entries_reject(db, STAGE1, old_lib, "T1", round_no + 1)

    def test_06_whitewash_without_prior_read(self):
        """验收 2：不先读取坏态——删账行后直接普通写，首次读取即全部拒绝
        （守恒结构性拒绝，不依赖任何"观察后标记"）；再次写后仍拒绝。"""
        db = self._db()
        db_mod.init_db(db)
        _seed_article(db, "W1")
        db_mod.save_decision(db, "W1", "include")
        db_mod.save_decision(db, "W1", "exclude")
        old_lib = self._pc_call_lib(db)
        with closing(_raw_conn(db)) as raw, raw:
            raw.execute("DELETE FROM team_vote_revisions WHERE stage=? AND item_key=?",
                        (STAGE1, "W1"))
        # 关键：此处不做任何读取/检出——直接普通个人写。
        db_mod.save_decision(db, "W1", "include")
        self._assert_all_trust_entries_reject(db, STAGE1, old_lib, "W1", 1)
        db_mod.save_decision(db, "W1", "maybe")
        self._assert_all_trust_entries_reject(db, STAGE1, old_lib, "W1", 2)

    def test_07_format1_refused_inplace_upgrade(self):
        """验收 7：format1（首轮未发布形态，仅 scratch 构造）视为
        unsupported/broken，不得以原 library_id 就地补 counter 升级。"""
        db = self._db()
        db_mod.init_db(db)
        _seed_article(db, "F1")
        db_mod.save_decision(db, "F1", "include")
        old_lib = self._pc_call_lib(db)
        # 构造首轮 format1 忠实形态（同身份：format1 meta + 无守恒记账触发器——
        # 真实首轮库的触发器不引用 advances 列；六触发器移除即其触发器面）。
        with closing(_raw_conn(db)) as raw, raw:
            for name in ("decisions_tvr_insert", "decisions_tvr_update",
                         "decisions_tvr_delete", "stage2_decisions_tvr_insert",
                         "stage2_decisions_tvr_update", "stage2_decisions_tvr_delete"):
                raw.execute("DROP TRIGGER IF EXISTS %s" % name)
            raw.execute("DROP TABLE team_vote_revision_meta")
            raw.execute(
                "CREATE TABLE team_vote_revision_meta("
                "singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                "format_version INTEGER NOT NULL, "
                "library_id TEXT NOT NULL CHECK(length(library_id)=32))")
            raw.execute("INSERT INTO team_vote_revision_meta("
                        "singleton,format_version,library_id) VALUES (1,1,?)",
                        (old_lib,))
        conn = self._pc(db)
        try:
            self.assertEqual(ledger.ledger_state(conn), "broken",
                             "format1 必须 broken: " + TAG_CONSERVATION)
            with self.assertRaises(ledger.VoteLedgerUnavailable):
                ledger.library_id(conn)
        finally:
            conn.close()
        # 就地升级禁止：init_db/_connect/install 尝试后 meta 仍为 3 列 format1。
        db_mod.init_db(db)
        with closing(self._pc(db)) as conn:
            self.assertEqual(ledger.ledger_state(conn), "broken", TAG_CONSERVATION)
            with self.assertRaises(
                    ledger.VoteLedgerUnavailable,
                    msg="format1 下 install 必须精确 VoteLedgerUnavailable 拒绝就地升级: "
                        + TAG_CONSERVATION) as caught_fmt:
                ledger.install(conn)
            self.assertIn(caught_fmt.exception.reason, ledger.REASONS, TAG_CONSERVATION)
        with closing(_raw_conn(db)) as raw:
            cols = [r[0] for r in raw.execute(
                "SELECT name FROM pragma_table_info('team_vote_revision_meta')").fetchall()]
            ver = raw.execute("SELECT format_version FROM team_vote_revision_meta"
                              ).fetchone()[0]
        self.assertEqual(cols, ["singleton", "format_version", "library_id"],
                         "format1 不得被就地补计数列升级: " + TAG_CONSERVATION)
        self.assertEqual(ver, 1, TAG_CONSERVATION)
        # 个人流照常（局部兼容降级）。
        db_mod.save_decision(db, "F1", "exclude")
        self.assertEqual(_decision_row(db, "F1")[1], "exclude", TAG_CONSERVATION)

    def test_08_concurrent_writes_no_false_broken(self):
        """验收 6：并发正常提交中的读验证不得跨 snapshot 假 broken；读写线程
        生命周期各自保留。"""
        db = self._db()
        db_mod.init_db(db)
        for idx in range(3):
            _seed_article(db, "C%d" % idx)
        errors = []
        stop = threading.Event()

        def writer():
            try:
                for idx in range(6):
                    db_mod.save_decision(db, "C%d" % (idx % 3),
                                         "include" if idx % 2 == 0 else "exclude")
            except Exception as exc:  # noqa: BLE001 — 线程内收集，主线程断言
                errors.append(("writer", exc))

        def reader():
            try:
                while not stop.is_set():
                    conn = self._pc(db)
                    try:
                        state = ledger.ledger_state(conn)
                        if state != "installed":
                            errors.append(("reader-state", state))
                            return
                        ledger.vote_snapshot(conn, STAGE1)
                    finally:
                        conn.close()
                    time.sleep(0.01)
            except Exception as exc:  # noqa: BLE001
                errors.append(("reader", exc))

        wt = threading.Thread(target=writer)
        rt = threading.Thread(target=reader)
        rt.start()
        wt.start()
        wt.join(timeout=120)
        stop.set()
        rt.join(timeout=120)
        self.assertEqual(errors, [],
                         "并发正常写期间读验证不得假 broken/失败: %r — %s"
                         % (errors, TAG_CONSERVATION))
        conn = self._pc(db)
        try:
            self.assertEqual(ledger.ledger_state(conn), "installed", TAG_CONSERVATION)
            ledger.vote_snapshot(conn, STAGE1)
        finally:
            conn.close()

    def test_09_meta_singleton_missing_trigger_aborts_write(self):
        """Sol：trigger 检查 meta 单例存在，缺失 RAISE(ABORT) → 票写失败零记账；
        恢复同值 meta 行后守恒成立（证明失败写未留下任何未记账变更）。"""
        db = self._installed_db()
        _seed_article(db, "M1")
        db_mod.save_decision(db, "M1", "include")
        meta_before = self._meta_row(db)
        revs_before = _ledger_revisions_raw(db, STAGE1)
        with closing(_raw_conn(db)) as raw, raw:
            raw.execute("DELETE FROM team_vote_revision_meta")
        with self.assertRaises(sqlite3.Error,
                               msg="meta 单例缺失必须 RAISE 拒写: " + TAG_CONSERVATION):
            db_mod.save_decision(db, "M1", "exclude")
        self.assertEqual(_decision_row(db, "M1")[1], "include",
                         "被拒写不得落库: " + TAG_CONSERVATION)
        # 同值恢复（自有 fixture 修复构造，非产品行为）→ 守恒成立证明零暗账。
        with closing(_raw_conn(db)) as raw, raw:
            raw.execute("INSERT INTO team_vote_revision_meta("
                        "singleton,format_version,library_id,seeded_stage1,"
                        "seeded_stage2,advances) VALUES (?,?,?,?,?,?)", meta_before)
        conn = self._pc(db)
        try:
            self.assertEqual(ledger.ledger_state(conn), "installed",
                             "同值恢复后守恒成立（失败写零记账）: " + TAG_CONSERVATION)
            self.assertEqual(self._revs(conn, STAGE1), revs_before, TAG_CONSERVATION)
        finally:
            conn.close()


def raw_key_rename(db, old, new):
    with closing(_raw_conn(db)) as raw, raw:
        raw.execute("UPDATE decisions SET zotero_key=? WHERE zotero_key=?", (new, old))


def raw_delete_vote(db, key):
    with closing(_raw_conn(db)) as raw, raw:
        raw.execute("DELETE FROM decisions WHERE zotero_key=?", (key,))


def raw_rollback_probe(db, key):
    """业务回滚探针：未提交 UPDATE 后回滚，账与票同退（守恒不受扰动）。"""
    with closing(_raw_conn(db)) as raw:
        raw.execute("BEGIN IMMEDIATE")
        raw.execute("UPDATE decisions SET notes=notes||'!' WHERE zotero_key=?", (key,))
        raw.rollback()


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
