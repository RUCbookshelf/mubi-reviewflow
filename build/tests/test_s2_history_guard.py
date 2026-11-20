# -*- coding: utf-8 -*-
"""S2 裁决后轨迹/历史只读守卫（team_history_guard）独立回归。

合同依据（只读引用）：
- `docs/maintenance/TEAM_FLOW_REPAIR_CONTRACT_20261114.md` §8；
- `out/maintenance216/team_flow_integration_design.md` §16；
- `.superpowers/sdd/2026-11-14-2.1.6-maintenance/s2-history-reader-brief.md`。

原则：本文件只新增测试符号，不改任何既有产品/测试；导入期不建库、不执行
业务操作。夹具全部使用真实临时 SQLite、既有
``prepare_arbitration_attestation_storage`` + ``append_decision_arbitration_attested``
合法 attestation 流建立；损坏/孤儿/结构矛盾场景使用同一真实库的裸 SQL
注入，不用 mock、不钉源码字符串、不镜像哈希算法。

覆盖：
  T1 include/exclude 两类裁决均锁；普通共识行不锁。
  T2 agreed/unresolved 行不锁；无 adjudicated 行的 attestation 返回空。
  T3 task/stage 精确隔离（screen→stage1、fulltext→stage2）。
  T4 generic-only（无 attestation）不是锁来源；缺 attestation 表合法空。
  T5 空表/空行合法空；读取不建表。
  T6 调用方未提交 attestation 可读；guard 不提交/不回滚。
  T7 陈旧 proof、旧 round 仍锁（不按当前状态解锁）。
  T8 adjudicated 键未被 input_proof 覆盖 → typed unavailable。
  T9 adjudicated 行缺有效 key → typed unavailable。
  T10 adjudicated 行 final 非二选一 → typed unavailable。
  T11 孤儿 attestation → typed unavailable。
  T12 generic records_json 损坏 → typed unavailable。
  T13 ProgrammingError 原样透传（不换壳）。
  T14 借用连接契约：非 Connection / 未 BEGIN → RuntimeError，零副作用。
  T15 参数校验；generator 只物化一次；非法批次不触库。
  T16 批量混合命中整次拒绝、零业务写；未命中返回 None。
  T17 row_factory、事务态、连接生命周期、schema 集不变。
  T18 错误类 code/message_key/排序去重交集键契约。
  T19 coding 阶段无来源：零查询、返回空、不锁。
  T20 generic record stage 与 attestation stage 不一致 → typed unavailable。

运行协议（由主控后续隔离放行）：isolated Python 3.12 + 全新纯源码箱；本
文件当前仅在作者侧做 AST/static 检查，未运行 pytest/unittest、未启动产品
/API、未触真实 data/8613/旧证据。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from coscreen import screening_arbitration as sarb  # noqa: E402
from coscreen import team_history_guard as guard  # noqa: E402

FIXED_AT = "2026-11-15T00:00:00.000Z"
TASK = "task-history"


def _sha64(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _hex32(seed: str) -> str:
    return _sha64(seed)[:32]


def _reviewer_frame(rows) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["zotero_key", "decision", "exclusion_reason", "notes", "tags", "screened_at"],
    )


def _merged_two_reviewers() -> pd.DataFrame:
    """K1 agree(include)、K2 conflict、K3 agree(exclude)、K4 conflict。"""
    alice = _reviewer_frame([
        ("K1", "include", "", "", "", "2026-11-14T10:00:00Z"),
        ("K2", "include", "", "", "", "2026-11-14T10:00:01Z"),
        ("K3", "exclude", "人群不符", "", "", "2026-11-14T10:00:02Z"),
        ("K4", "exclude", "研究设计不符", "", "", "2026-11-14T10:00:03Z"),
    ])
    bob = _reviewer_frame([
        ("K1", "include", "", "", "", "2026-11-14T11:00:00Z"),
        ("K2", "exclude", "主题不符合", "", "", "2026-11-14T11:00:01Z"),
        ("K3", "exclude", "人群不符", "", "", "2026-11-14T11:00:02Z"),
        ("K4", "include", "", "", "", "2026-11-14T11:00:03Z"),
    ])
    return sarb.merge_decision_packages({"alice": alice, "bob": bob})


def _base_resolutions() -> list:
    return [{"zotero_key": "K2", "decision": "include", "rationale": "采信 alice 的纳入判断"}]


def _multi_resolutions() -> list:
    return [
        {"zotero_key": "K2", "decision": "include", "rationale": "采信 alice 的纳入判断"},
        {"zotero_key": "K4", "decision": "exclude", "rationale": "研究设计不符，仲裁排除"},
    ]


def _proof(keys=("K2",), revisions=None) -> dict:
    revs = {"alice": 1, "bob": 1}
    revs.update(revisions or {})
    return {
        key: {
            name: {"library_id": _hex32("lib-" + name), "revision": revs[name]}
            for name in ("alice", "bob")
        }
        for key in keys
    }


def _meta(*, task_id=TASK, round=None, proof_keys=("K2",), revisions=None) -> dict:
    return {
        "task_id": task_id,
        "round": round,
        "required_names": ["alice", "bob"],
        "arbitrator_user_id": 101,
        "arbitrator_role": "reconciler",
        "input_proof": _proof(proof_keys, revisions),
    }


def _insert_generic_raw(conn, *, stage="stage1", records=None, records_json=None,
                        seed="raw-generic") -> int:
    payload = records_json if records_json is not None else json.dumps(
        records, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    cursor = conn.execute(
        "INSERT INTO screening_arbitrations "
        "(stage,merge_sha256,result_sha256,supersedes_id,arbitrator_id,records_json,created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (stage, _sha64(seed + "-merge"), _sha64(seed + "-result"), None,
         "alice", payload, FIXED_AT),
    )
    return int(cursor.lastrowid)


def _insert_attest_raw(conn, *, record_id, task_id=TASK, stage="stage1",
                       proof=None, required=None, seed="raw-attest") -> None:
    conn.execute(
        "INSERT INTO screening_arbitration_attestations "
        "(record_id,task_id,round_id,stage,arbitrator_user_id,arbitrator_role,"
        "required_names_json,input_proof_json,resolutions_digest,attest_sha256,attested_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (record_id, task_id, None, stage, 101, "reconciler",
         json.dumps(required if required is not None else ["alice", "bob"],
                    ensure_ascii=False, separators=(",", ":")),
         json.dumps(proof if proof is not None else {},
                    ensure_ascii=False, sort_keys=True, separators=(",", ":")),
         _sha64(seed + "-digest"), _sha64(seed + "-attest"), FIXED_AT),
    )


class _CountingConnection(sqlite3.Connection):
    """原生 Connection 子类：统计 execute 次数，用于证明非法批次/ coding 零触库。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.executions = 0

    def execute(self, sql, parameters=()):
        self.executions += 1
        return super().execute(sql, parameters)


class _ProgrammingErrorConnection(sqlite3.Connection):
    """原生 Connection 子类：在读取 attestation 表时注入真实 ProgrammingError。"""

    def execute(self, sql, parameters=()):
        if "FROM screening_arbitration_attestations" in sql:
            raise sqlite3.ProgrammingError("injected caller programming error")
        return super().execute(sql, parameters)


class HistoryGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="rf216_s2_history_guard_")
        cls.root = Path(cls._tmp.name)
        cls.addClassCleanup(cls._tmp.cleanup)
        print("S2_HISTORY_GUARD_TEST_FILE_SHA", _sha256_file(__file__))
        print("S2_HISTORY_GUARD_PRODUCT_SHA", _sha256_file(guard.__file__))
        print("S2_HISTORY_GUARD_REPO_ROOT", str(REPO_ROOT))
        print("S2_HISTORY_GUARD_PYTHON", sys.version.split()[0])
        print("S2_HISTORY_GUARD_SQLITE", sqlite3.sqlite_version)
        print("S2_HISTORY_GUARD_ISOLATED", sys.flags.isolated)
        print("S2_HISTORY_GUARD_DONT_WRITE_BC", sys.flags.dont_write_bytecode)

    def setUp(self):
        self.base = self.root / self.id().rsplit(".", 1)[-1]
        self.base.mkdir(parents=True, exist_ok=True)
        self.store = self.base / "collab.db"

    # -- 夹具 ---------------------------------------------------------------
    def _prepare(self, name="collab.db") -> Path:
        store = self.base / name
        sarb.prepare_arbitration_attestation_storage(store)
        return store

    def _caller(self, store=None, *, row_factory=sqlite3.Row, begin=True, factory=None):
        kwargs = {"timeout": 15}
        if factory is not None:
            kwargs["factory"] = factory
        conn = sqlite3.connect(str(store or self.store), **kwargs)
        if row_factory is not None:
            conn.row_factory = row_factory
        if begin:
            conn.execute("BEGIN IMMEDIATE")
        return conn

    def _append_committed(self, store=None, *, task_id=TASK, stage="stage1",
                          resolutions=None, proof_keys=("K2",), round=None,
                          revisions=None, merged=None) -> dict:
        conn = self._caller(store)
        try:
            result = sarb.append_decision_arbitration_attested(
                conn,
                stage=stage,
                merged=_merged_two_reviewers() if merged is None else merged,
                resolutions=_base_resolutions() if resolutions is None else resolutions,
                arbitrator_id="alice",
                attestation_meta=_meta(
                    task_id=task_id, round=round,
                    proof_keys=proof_keys, revisions=revisions),
            )
            conn.commit()
            return result
        finally:
            conn.close()

    def _counts(self, store=None):
        with closing(sqlite3.connect(str(store or self.store))) as conn:
            generic = conn.execute(
                "SELECT count(*) FROM screening_arbitrations").fetchone()[0]
            present = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='screening_arbitration_attestations'").fetchone()
            attest = None
            if present:
                attest = conn.execute(
                    "SELECT count(*) FROM screening_arbitration_attestations").fetchone()[0]
        return int(generic), (None if attest is None else int(attest))

    def _assert_locked(self, conn, stage, expected, *, task_id=TASK):
        keys = guard.adjudicated_history_keys_on(conn, task_id=task_id, stage=stage)
        self.assertEqual(keys, frozenset(expected))
        return keys

    # -- T1/T2：哪些行是锁来源 ----------------------------------------------
    def test_01_include_and_exclude_both_lock(self):
        self._prepare()
        self._append_committed(resolutions=_multi_resolutions(), proof_keys=("K2", "K4"))
        conn = self._caller()
        try:
            before_factory = conn.row_factory
            before_changes = conn.total_changes
            keys = guard.adjudicated_history_keys_on(conn, task_id=TASK, stage="screen")
            self.assertEqual(keys, frozenset({"K2", "K4"}))
            self.assertNotIn("K1", keys)
            self.assertNotIn("K3", keys)
            self.assertIs(conn.row_factory, before_factory)
            self.assertTrue(conn.in_transaction)
            self.assertEqual(conn.total_changes, before_changes)
        finally:
            conn.rollback()
            conn.close()

    def test_02_agree_and_unresolved_rows_not_lock(self):
        self._prepare()
        self._append_committed(resolutions=_base_resolutions(), proof_keys=("K2",))
        conn = self._caller()
        try:
            keys = self._assert_locked(conn, "screen", {"K2"})
            self.assertFalse(keys & {"K1", "K3", "K4"})
        finally:
            conn.rollback()
            conn.close()

        store = self._prepare("no_adjudications.db")
        self._append_committed(store, resolutions=[], proof_keys=("K2",))
        conn = self._caller(store)
        try:
            self.assertEqual(
                guard.adjudicated_history_keys_on(conn, task_id=TASK, stage="screen"),
                frozenset(),
            )
        finally:
            conn.rollback()
            conn.close()

    # -- T3：task/stage 隔离 -------------------------------------------------
    def test_03_stage_and_task_isolation(self):
        self._prepare()
        self._append_committed(task_id="task-A", stage="stage1",
                               resolutions=_base_resolutions(), proof_keys=("K2",))
        self._append_committed(task_id="task-A", stage="stage2",
                               resolutions=_multi_resolutions()[1:], proof_keys=("K4",))
        self._append_committed(task_id="task-B", stage="stage1",
                               resolutions=_base_resolutions(), proof_keys=("K2",))
        conn = self._caller()
        try:
            self._assert_locked(conn, "screen", {"K2"}, task_id="task-A")
            self.assertEqual(
                guard.adjudicated_history_keys_on(conn, task_id="task-A", stage="fulltext"),
                frozenset({"K4"}),
            )
            self.assertEqual(
                guard.adjudicated_history_keys_on(conn, task_id="task-B", stage="screen"),
                frozenset({"K2"}),
            )
            self.assertEqual(
                guard.adjudicated_history_keys_on(conn, task_id="task-B", stage="fulltext"),
                frozenset(),
            )
            self.assertEqual(
                guard.adjudicated_history_keys_on(conn, task_id="task-C", stage="screen"),
                frozenset(),
            )
        finally:
            conn.rollback()
            conn.close()

    # -- T4/T5：非锁来源与合法空 ---------------------------------------------
    def test_04_generic_only_legacy_not_lock_source(self):
        legacy = self.base / "legacy.db"
        sarb.append_decision_arbitration(
            legacy,
            stage="stage1",
            merged=_merged_two_reviewers(),
            resolutions=_multi_resolutions(),
            arbitrator_id="alice",
        )
        self.assertEqual(self._counts(legacy), (1, None))
        conn = self._caller(legacy)
        try:
            self.assertEqual(
                guard.adjudicated_history_keys_on(conn, task_id=TASK, stage="screen"),
                frozenset(),
            )
        finally:
            conn.rollback()
            conn.close()

        prepared = self._prepare("prepared_generic_only.db")
        sarb.append_decision_arbitration(
            prepared,
            stage="stage1",
            merged=_merged_two_reviewers(),
            resolutions=_multi_resolutions(),
            arbitrator_id="alice",
        )
        self.assertEqual(self._counts(prepared), (1, 0))
        conn = self._caller(prepared)
        try:
            self.assertEqual(
                guard.adjudicated_history_keys_on(conn, task_id=TASK, stage="screen"),
                frozenset(),
            )
        finally:
            conn.rollback()
            conn.close()

    def test_05_empty_store_and_missing_attestation_table_legal_empty(self):
        empty = self.base / "empty.db"
        conn = sqlite3.connect(str(empty))
        try:
            conn.execute("BEGIN")
            before = conn.execute(
                "SELECT count(*) FROM sqlite_master").fetchone()[0]
            self.assertEqual(
                guard.adjudicated_history_keys_on(conn, task_id=TASK, stage="screen"),
                frozenset(),
            )
            after = conn.execute(
                "SELECT count(*) FROM sqlite_master").fetchone()[0]
            self.assertEqual(after, before)
            self.assertEqual(after, 0)
        finally:
            conn.rollback()
            conn.close()

        self._prepare()
        conn = self._caller()
        try:
            self.assertEqual(
                guard.adjudicated_history_keys_on(conn, task_id=TASK, stage="screen"),
                frozenset(),
            )
            self.assertEqual(
                guard.adjudicated_history_keys_on(conn, task_id=TASK, stage="fulltext"),
                frozenset(),
            )
        finally:
            conn.rollback()
            conn.close()

    # -- T6：调用方未提交事实可见 --------------------------------------------
    def test_06_caller_uncommitted_attestation_is_readable(self):
        self._prepare()
        conn = self._caller()
        try:
            sarb.append_decision_arbitration_attested(
                conn,
                stage="stage1",
                merged=_merged_two_reviewers(),
                resolutions=_base_resolutions(),
                arbitrator_id="alice",
                attestation_meta=_meta(),
            )
            self.assertTrue(conn.in_transaction)
            self.assertIs(conn.row_factory, sqlite3.Row)
            changes = conn.total_changes
            self.assertEqual(
                guard.adjudicated_history_keys_on(conn, task_id=TASK, stage="screen"),
                frozenset({"K2"}),
            )
            self.assertEqual(conn.total_changes, changes)
            self.assertTrue(conn.in_transaction)
            conn.rollback()
        finally:
            conn.close()
        self.assertEqual(self._counts(), (0, 0))

    # -- T7：陈旧 proof / 旧 round 仍锁 --------------------------------------
    def test_07_stale_proof_and_old_round_still_lock(self):
        self._prepare()
        self._append_committed(round="round-old",
                               resolutions=_base_resolutions(), proof_keys=("K2",),
                               revisions={"alice": 1, "bob": 1})
        self._append_committed(round=None,
                               resolutions=_base_resolutions(), proof_keys=("K2",),
                               revisions={"alice": 7, "bob": 7})
        with closing(sqlite3.connect(str(self.store))) as raw:
            raw.execute(
                "CREATE TABLE IF NOT EXISTS team_vote_revisions "
                "(member TEXT NOT NULL, revision INTEGER NOT NULL)")
            raw.execute(
                "INSERT INTO team_vote_revisions VALUES ('alice', 99)")
            raw.commit()
        conn = self._caller()
        try:
            self._assert_locked(conn, "screen", {"K2"})
        finally:
            conn.rollback()
            conn.close()

    # -- T8-T12：结构矛盾与损坏 fail-closed ----------------------------------
    def test_08_proof_coverage_contradiction_fail_closed(self):
        self._prepare()
        self._append_committed(resolutions=_multi_resolutions(), proof_keys=("K2",))
        conn = self._caller()
        try:
            with self.assertRaises(sarb.ArbitrationAttestationStoreUnavailable):
                guard.adjudicated_history_keys_on(conn, task_id=TASK, stage="screen")
        finally:
            conn.rollback()
            conn.close()

    def test_09_adjudicated_row_missing_key_fail_closed(self):
        self._prepare()
        with closing(sqlite3.connect(str(self.store))) as raw:
            record_id = _insert_generic_raw(raw, records=[
                {"arbitration_status": "adjudicated", "final_decision": "include"},
            ])
            _insert_attest_raw(raw, record_id=record_id,
                               proof=_proof(("K9",)), seed="missing-key")
            raw.commit()
        conn = self._caller()
        try:
            with self.assertRaises(sarb.ArbitrationAttestationStoreUnavailable):
                guard.adjudicated_history_keys_on(conn, task_id=TASK, stage="screen")
        finally:
            conn.rollback()
            conn.close()

    def test_10_adjudicated_non_binary_final_fail_closed(self):
        self._prepare()
        with closing(sqlite3.connect(str(self.store))) as raw:
            record_id = _insert_generic_raw(raw, records=[
                {"zotero_key": "K9", "arbitration_status": "adjudicated",
                 "final_decision": "maybe"},
            ])
            _insert_attest_raw(raw, record_id=record_id,
                               proof=_proof(("K9",)), seed="bad-final")
            raw.commit()
        conn = self._caller()
        try:
            with self.assertRaises(sarb.ArbitrationAttestationStoreUnavailable):
                guard.adjudicated_history_keys_on(conn, task_id=TASK, stage="screen")
        finally:
            conn.rollback()
            conn.close()

    def test_11_orphan_attestation_fail_closed(self):
        self._prepare()
        with closing(sqlite3.connect(str(self.store))) as raw:
            _insert_attest_raw(raw, record_id=999999,
                               proof=_proof(("K9",)), seed="orphan")
            raw.commit()
        conn = self._caller()
        try:
            with self.assertRaises(sarb.ArbitrationAttestationStoreUnavailable):
                guard.adjudicated_history_keys_on(conn, task_id=TASK, stage="screen")
        finally:
            conn.rollback()
            conn.close()

    def test_12_corrupt_records_json_fail_closed(self):
        self._prepare()
        with closing(sqlite3.connect(str(self.store))) as raw:
            record_id = _insert_generic_raw(raw, records_json="not-json",
                                            seed="corrupt-records")
            _insert_attest_raw(raw, record_id=record_id,
                               proof=_proof(("K9",)), seed="corrupt-records")
            raw.commit()
        conn = self._caller()
        try:
            with self.assertRaises(sarb.ArbitrationAttestationStoreUnavailable):
                guard.adjudicated_history_keys_on(conn, task_id=TASK, stage="screen")
        finally:
            conn.rollback()
            conn.close()

    def test_13_programming_error_passthrough(self):
        self._prepare()
        self._append_committed(resolutions=_base_resolutions(), proof_keys=("K2",))
        conn = sqlite3.connect(str(self.store), factory=_ProgrammingErrorConnection)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            with self.assertRaises(sqlite3.ProgrammingError):
                guard.adjudicated_history_keys_on(conn, task_id=TASK, stage="screen")
        finally:
            if conn.in_transaction:
                conn.rollback()
            conn.close()

    # -- T14/T15：借用契约与参数 --------------------------------------------
    def test_14_connection_contract_requires_borrowed_transaction(self):
        missing = self.base / "must-not-open.db"
        with self.assertRaises(RuntimeError):
            guard.adjudicated_history_keys_on(None, task_id=TASK, stage="screen")
        with self.assertRaises(RuntimeError):
            guard.assert_history_writable_on(
                str(missing), task_id=TASK, stage="screen", item_keys=["K1"])
        self.assertFalse(missing.exists())

        self._prepare()
        conn = sqlite3.connect(str(self.store))
        conn.row_factory = sqlite3.Row
        try:
            self.assertFalse(conn.in_transaction)
            names_before = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
            with self.assertRaises(RuntimeError):
                guard.adjudicated_history_keys_on(conn, task_id=TASK, stage="screen")
            with self.assertRaises(RuntimeError):
                guard.assert_history_writable_on(
                    conn, task_id=TASK, stage="screen", item_keys=["K1"])
            self.assertFalse(conn.in_transaction)
            names_after = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
            self.assertEqual(names_after, names_before)
        finally:
            conn.close()

    def test_15_parameter_validation_and_single_materialization(self):
        self._prepare()
        self._append_committed(resolutions=_base_resolutions(), proof_keys=("K2",))
        conn = sqlite3.connect(str(self.store), factory=_CountingConnection)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.executions = 0
            for bad_task in ("", None, 123):
                with self.subTest(task_id=bad_task):
                    with self.assertRaises(ValueError):
                        guard.adjudicated_history_keys_on(
                            conn, task_id=bad_task, stage="screen")
            self.assertEqual(conn.executions, 0)
            for bad_stage in ("stage1", "stage2", "appraisal", "", None):
                with self.subTest(stage=bad_stage):
                    with self.assertRaises(ValueError):
                        guard.adjudicated_history_keys_on(
                            conn, task_id=TASK, stage=bad_stage)
            self.assertEqual(conn.executions, 0)
            for bad_keys in (None, 123, "K1", ["K1", ""], ["K1", None], ["K1", b"K2"]):
                with self.subTest(item_keys=bad_keys):
                    with self.assertRaises(ValueError):
                        guard.assert_history_writable_on(
                            conn, task_id=TASK, stage="screen", item_keys=bad_keys)
            self.assertEqual(conn.executions, 0)

            seen = []

            def keys():
                for key in ("K1", "K2"):
                    seen.append(key)
                    yield key

            with self.assertRaises(guard.TeamHistoryReadOnlyError) as caught:
                guard.assert_history_writable_on(
                    conn, task_id=TASK, stage="screen", item_keys=keys())
            self.assertEqual(seen, ["K1", "K2"])
            self.assertEqual(caught.exception.item_keys, ("K2",))
        finally:
            if conn.in_transaction:
                conn.rollback()
            conn.close()

    # -- T16/T17：整批拒绝零写与连接保真 ------------------------------------
    def test_16_whole_batch_rejection_zero_business_writes(self):
        self._prepare()
        self._append_committed(resolutions=_base_resolutions(), proof_keys=("K2",))
        before_counts = self._counts()
        conn = self._caller()
        try:
            conn.execute(
                "CREATE TABLE business_state (id INTEGER PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO business_state (value) VALUES ('sentinel')")
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            before_changes = conn.total_changes
            names_before = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
            with self.assertRaises(guard.TeamHistoryReadOnlyError) as caught:
                guard.assert_history_writable_on(
                    conn, task_id=TASK, stage="screen",
                    item_keys=["K1", "K2", "K3", "K2"])
            self.assertEqual(caught.exception.item_keys, ("K2",))
            self.assertEqual(conn.total_changes, before_changes)
            self.assertTrue(conn.in_transaction)
            self.assertEqual(
                {row[0] for row in conn.execute("SELECT name FROM sqlite_master")},
                names_before,
            )
            self.assertEqual(
                [tuple(row) for row in conn.execute(
                    "SELECT id, value FROM business_state")],
                [(1, "sentinel")],
            )
            generic = conn.execute(
                "SELECT count(*) FROM screening_arbitrations").fetchone()[0]
            attest = conn.execute(
                "SELECT count(*) FROM screening_arbitration_attestations").fetchone()[0]
            self.assertEqual((generic, attest), before_counts)

            self.assertIsNone(guard.assert_history_writable_on(
                conn, task_id=TASK, stage="screen", item_keys=["K1", "K3"]))
            self.assertEqual(conn.total_changes, before_changes)
            self.assertTrue(conn.in_transaction)
            conn.commit()
        finally:
            if conn.in_transaction:
                conn.rollback()
            conn.close()

    def test_17_row_factory_and_connection_lifecycle_preserved(self):
        self._prepare()
        self._append_committed(resolutions=_base_resolutions(), proof_keys=("K2",))
        for factory in (None, sqlite3.Row):
            with self.subTest(row_factory=factory):
                conn = sqlite3.connect(str(self.store))
                if factory is not None:
                    conn.row_factory = factory
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    before_factory = conn.row_factory
                    before_changes = conn.total_changes
                    names_before = {
                        row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
                    self.assertEqual(
                        guard.adjudicated_history_keys_on(
                            conn, task_id=TASK, stage="screen"),
                        frozenset({"K2"}),
                    )
                    self.assertIs(conn.row_factory, before_factory)
                    self.assertTrue(conn.in_transaction)
                    self.assertEqual(conn.total_changes, before_changes)
                    self.assertEqual(
                        {row[0] for row in conn.execute("SELECT name FROM sqlite_master")},
                        names_before,
                    )
                    self.assertEqual(conn.execute("SELECT 1").fetchone()[0], 1)
                    conn.rollback()
                finally:
                    conn.close()

    # -- T18/T19/T20：错误契约、coding、record stage 矛盾 --------------------
    def test_18_error_class_contract(self):
        self.assertTrue(issubclass(guard.TeamHistoryReadOnlyError, RuntimeError))
        error = guard.TeamHistoryReadOnlyError(
            "task-x", "screen", ["K2", "K1", "K2"])
        self.assertEqual(error.task_id, "task-x")
        self.assertEqual(error.stage, "screen")
        self.assertEqual(error.item_keys, ("K1", "K2"))
        self.assertEqual(error.code, "history_read_only_after_arbitration")
        self.assertEqual(error.message_key, "team.history.read_only_after_arbitration")
        self.assertIn("已裁决", str(error))

    def test_19_coding_stage_never_queries_or_locks(self):
        self._prepare()
        self._append_committed(resolutions=_base_resolutions(), proof_keys=("K2",))
        conn = sqlite3.connect(str(self.store), factory=_CountingConnection)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.executions = 0
            self.assertEqual(
                guard.adjudicated_history_keys_on(
                    conn, task_id=TASK, stage="coding"),
                frozenset(),
            )
            self.assertEqual(conn.executions, 0)
            self.assertIsNone(guard.assert_history_writable_on(
                conn, task_id=TASK, stage="coding", item_keys=["K2"]))
            self.assertEqual(conn.executions, 0)
            self.assertTrue(conn.in_transaction)
        finally:
            if conn.in_transaction:
                conn.rollback()
            conn.close()

    def test_20_record_stage_mismatch_fail_closed(self):
        self._prepare()
        with closing(sqlite3.connect(str(self.store))) as raw:
            record_id = _insert_generic_raw(raw, stage="stage2", records=[
                {"zotero_key": "K9", "arbitration_status": "adjudicated",
                 "final_decision": "include"},
            ], seed="stage-mismatch")
            _insert_attest_raw(raw, record_id=record_id, stage="stage1",
                               proof=_proof(("K9",)), seed="stage-mismatch")
            raw.commit()
        conn = self._caller()
        try:
            with self.assertRaises(sarb.ArbitrationAttestationStoreUnavailable):
                guard.adjudicated_history_keys_on(conn, task_id=TASK, stage="screen")
        finally:
            conn.rollback()
            conn.close()


if __name__ == "__main__":
    unittest.main()
