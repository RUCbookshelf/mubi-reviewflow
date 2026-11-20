# -*- coding: utf-8 -*-
"""P22 当前团队结果测试候选（C12 作者侧静态撰写；工单钉死四具名用例）。

目标产品候选（本文件唯一针对的实现，冻结基线 SHA-256 前缀已独立核验）：
- 箱 candidate/custom_backend/team_current_results.py（新文件）
- 箱 candidate/custom_backend/prisma_task_metrics.py（修改版；主仓基线
  SHA-256 前缀 c3827dee，差异仅为 P22 team 投影分支 + summary runtime 形参
  + 新 helper 两个；decisions/outcomes 两函数 AST 级不变——本文件
  test_raw_exports_and_generic_latest_unchanged 钉定）。
- 消费面（主仓已集成，直接 import，不经箱内副本）：
  custom_backend/team_screening_runtime.py（f2281215）、
  custom_backend/team_keyed_reads.py（32a71432）、
  coscreen/arbitration_session.py（7e76b38e）。

装载协议（P03 candidate4 / P09 同配方）：主仓 E:/mubi-reviewflow 入
sys.path（coscreen 全族 + P09/P14 集成模块解析）；箱内两候选经
importlib 文件位置装载并在 exec 前注册
sys.modules['custom_backend.team_current_results'] /
['custom_backend.prisma_task_metrics']（同 stem 后注册＝装配错误，拒绝
静默顶替）；team_current_results 内部 import 的
custom_backend.team_screening_runtime 命中主仓集成版（P09 已整合）。
基线 prisma（主仓原文件）以别名 stem 装载为对照物，不占
custom_backend.prisma_task_metrics。运行解释器 -I -B，TEMP/TMP 指本箱。

用例 → 工单映射（四具名，名称逐字）：
- MultipleKeysOwnOutcomeTests.test_multiple_keys_use_own_valid_outcome
  每键各自用**自己的**有效当前结论：A=F1 见证 stage1 include + stage2
  live 全员一致 include（eligible/deciding_stage=2/stage1 attestation
  persisted=True）；B=live 分歧（blocked/awaiting_arbitration）；C=部分
  成员未投（partial/raw_pending_members=[bob]——raw 个人完成度≠团队结论）；
  D=F1 见证 stage1 exclude（eligible exclude/stage2 剪枝不读）。四态计数
  锚（工单逐字）：eligible+blocked+unavailable == known_total（blocked
  合并桶含 partial 细分）；两 stage 链一致（同一 scope 一次派生；stage2
  仅在 stage1 非终局排除时评估）。
- CompletedRosterAfterMemberChangesTests.test_completed_roster_after_member_changes
  2026-11-15 裁定锚（TEAM_FLOW_REPAIR_CONTRACT_20261114 §9）：完成条目
  （R=stage1 exclude 封存；A2=stage1+stage2 双封存）在新成员 carol 加入后
  保留完成时结论与名单（persisted seal/roster_basis=completion/
  completion_required_uids 不含 carol/raw_pending_members 不因 carol 重开）；
  未完成键 X 走当前名单（partial/pending 含 carol）。对照锚：基线
  outcomes() team 语义（当前名单全员一致才终局）对 R 无结论、对 A2 不读
  证——新投影不采该口径。
- PartialMetricsAndRecordsTests.test_partial_metrics_and_records
  source 坏不伪空/不排除：bob 库损坏（垃圾字节）→ live 键 B/C 逐键
  member_vote_unreadable=unavailable（A 封存键不受连坐，经 collab 见证读，
  不触成员库）；known_total=3 不缩水。metrics 集成：summary(ctx,'team',
  runtime) 的 current_team.counts 锚 + pending.team_unresolved + 来源坏
  warning；records 路由（真实 FastAPI 注册后直调 endpoint 函数，零 HTTP
  层）表头 12 列不变、Content-Disposition 不变。
- RawExportsAndGenericLatestUnchangedTests.test_raw_exports_and_generic_latest_unchanged
  旧路径零改动：①AST 锚——candidate 与基线的 decisions/outcomes 两函数
  ast.dump 全等（按人 raw 读 + generic latest 消费代码逐字不变）；②行为
  锚——reviewer 模式 outcomes==actor 自身 decisions（候选与基线相等）；
  team 模式 outcomes（旧当前名单共识 + latest_decision_arbitration 路径）
  候选与基线同夹具同输出（unanimous U 进结果、冲突 V unresolved=1）；
  ③登记锚——两条路由路径仍在（AST）。

夹具（零 mock；P09 test_216_keyed_team_views 同构）：真实 collab
initialize_task_store/set_business_role/grant_role；F2
prepare_team_terminal_registry+register_protocol_on；F1
prepare_team_terminal_facts+append_seal_on（v2 observation payload，同参
确定性）；成员库真实 dbm.init_db+静态 SQL 直插（INSERT 触发真实 trigger）；
users.db 真实 auth.create_user；runtime=真实 WorkflowRuntime+宿主形
task_lookup（真实 TaskInfo 值）。ctx=真实 runtime.context（测试自有 TEMP
目录内懒建属主/成员库，允许）。

SQL 预检纪律（P07 教训）：三个静态 SQL 常量与 P09 箱同文（该箱已在真实
init_db schema 上 EXPLAIN 验证）；本箱 scripts 预检重跑 EXPLAIN。

诚实记录：
1. records 路由以直调 endpoint 函数代替 HTTP 层（TestClient/httpx 不入箱；
   P09 先例：不触 HTTP 层）。FastAPI 实例真实注册后按 path 取 endpoint。
2. 基线 outcomes 的 generic latest 分支在本夹具无存量仲裁快照行——latest
   路径的行为不变由 AST 全等锚钉定（行为正控为其内层 unanimous/live 读）。
3. summary 的 team 分支依赖 runtime.context 懒建 owner 库（articles 表由
   真实产品 ensure 建）；workflow_source_records 无行 → CSV 仅表头（表头
   形状即本用例 records 侧断言面）。
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

_P22_BOX_ROOT = Path(__file__).resolve().parents[3]
_P22_CURRENT_SOURCE = _P22_BOX_ROOT / "candidate" / "custom_backend" / (
    "team_current_results.py")
_P22_PRISMA_SOURCE = _P22_BOX_ROOT / "candidate" / "custom_backend" / (
    "prisma_task_metrics.py")
_P22_REPO_ROOT = Path("E:/mubi-reviewflow")
_P22_BASELINE_PRISMA = _P22_REPO_ROOT / "custom_backend" / (
    "prisma_task_metrics.py")
if not _P22_REPO_ROOT.is_dir():
    raise RuntimeError(
        "P22 测试需要主仓 E:/mubi-reviewflow（coscreen 全族与 P09/P14 集成"
        "模块）；主仓缺位时本文件拒绝静默降级")
for _p22_path in (_P22_CURRENT_SOURCE, _P22_PRISMA_SOURCE,
                  _P22_BASELINE_PRISMA):
    if not _p22_path.is_file():
        raise RuntimeError(
            "P22 测试需要箱内候选两文件与主仓基线 prisma_task_metrics.py；"
            "缺位即诚实红，不冒充行为证据")
if str(_P22_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_P22_REPO_ROOT))


def _p22_load_candidate(stem, path):
    """P03/P09 candidate4 配方（r1/r2 修轮增强）：exec 前注册 stem＋
    identity-guard 回滚。

    等同件预注册放行：run 树带主仓 conftest 时，会话握手先
    ``import custom_backend.main`` → main 先 import
    ``custom_backend.prisma_task_metrics``（运行树内供给件）。若已注册
    stem 的 ``__file__`` 位于运行树内（本文件 parents[3] 之下）且其字节
    与箱候选全等（SHA-256 相等），则该注册就是本配方的供给件本体——放行
    复用、不重复 exec。防线保留：路径不在树内或字节不符（如主仓基线件
    顶替候选）＝异件注册，仍抛 loader collision（诚实红，不静默降级）。
    """
    previous = sys.modules.get(stem)
    if previous is not None:
        prev_file = getattr(previous, "__file__", None) or ""
        prev_path = Path(prev_file).resolve() if prev_file else None
        tree_root = Path(__file__).resolve().parents[3]
        identical = False
        if prev_path is not None and prev_path.is_file():
            in_tree = prev_path == tree_root or tree_root in prev_path.parents
            identical = in_tree and hashlib.sha256(
                prev_path.read_bytes()).hexdigest() == hashlib.sha256(
                Path(path).read_bytes()).hexdigest()
        if not identical:
            raise RuntimeError(
                "P22 loader collision: %s already registered by a different "
                "artifact (%r; expected identical candidate %s)" % (
                    stem, prev_file, path))
        return previous
    spec = importlib.util.spec_from_file_location(stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("P22 spec load failed: %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[stem] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if sys.modules.get(stem) is module:
            del sys.modules[stem]
        raise
    return module


tcr = _p22_load_candidate(
    "custom_backend.team_current_results", _P22_CURRENT_SOURCE)
ptm = _p22_load_candidate(
    "custom_backend.prisma_task_metrics", _P22_PRISMA_SOURCE)


def _p22_load_baseline_prisma():
    """主仓基线 prisma_task_metrics（别名 stem，不占 custom_backend 名）。"""
    spec = importlib.util.spec_from_file_location(
        "p22_baseline_prisma_task_metrics", _P22_BASELINE_PRISMA)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bptm = _p22_load_baseline_prisma()

from coscreen import db as dbm  # noqa: E402
from coscreen import task_collaboration as collab  # noqa: E402
from coscreen import tasks as tasks_mod  # noqa: E402
from coscreen import team_terminal_facts as tf  # noqa: E402
from coscreen import team_terminal_registry as tr  # noqa: E402
import custom_backend.auth as auth  # noqa: E402
from custom_backend.review_workflow_api import WorkflowRuntime  # noqa: E402

P22_TS1 = "2026-11-18T09:00:00.000Z"
P22_TS2 = "2026-11-18T10:00:00.000Z"
P22_VOTE_SCREENED = "2026-11-14T08:00:00+00:00"
P22_VOTE_UPDATED = "2026-11-14T09:00:00+00:00"
P22_OWNER_LIBRARY = "1a" * 16
P22_USER_NAMES = ("owner", "alice", "bob", "carol")
P22_PASSWORD = "p22-pass-9527"
USERS = {}
_P22_PROVENANCE_PRINTED = False

_P22_INSERT_ARTICLE = (
    "INSERT INTO articles (zotero_key, title, is_duplicate_of, import_order)"
    " VALUES (?, ?, NULL, ?)")
_P22_INSERT_STAGE1_VOTE = (
    "INSERT INTO decisions (zotero_key, decision, exclusion_reason, notes,"
    " tags, screened_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)")
_P22_INSERT_STAGE2_VOTE = (
    "INSERT INTO stage2_decisions (zotero_key, decision, exclusion_reason,"
    " notes, tags, screened_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)")

_P22_JSON_KW = {"ensure_ascii": False, "sort_keys": True,
                "separators": (",", ":"), "allow_nan": False}


def _p22_sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _p22_canonical_json(value):
    return json.dumps(value, **_P22_JSON_KW)


def _p22_close_quietly(conn):
    try:
        if conn.in_transaction:
            conn.rollback()
    except sqlite3.Error:
        pass
    try:
        conn.close()
    except sqlite3.Error:
        pass


def _p22_borrowed_writer(path):
    conn = sqlite3.connect(str(path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN IMMEDIATE")
    return conn


def _p22_member_library_id(uid):
    return "%032x" % uid


def _p22_default_roster():
    """F1 payload 完成时名单快照：owner=reconciler 非必需；alice/bob=coder
    必需筛选人（完成时名单保留侧的锚——不含 carol）。"""
    return [
        {"uid": USERS["owner"], "username": "owner", "role": "reconciler",
         "is_owner": True, "screening_enabled": False,
         "required_screener": False, "source": "owner-source", "config": "ok"},
        {"uid": USERS["alice"], "username": "alice", "role": "coder",
         "is_owner": False, "screening_enabled": None,
         "required_screener": True, "source": "import-source", "config": "ok"},
        {"uid": USERS["bob"], "username": "bob", "role": "coder",
         "is_owner": False, "screening_enabled": None,
         "required_screener": True, "source": "grant-source", "config": "ok"},
    ]


def _p22_make_opinion(uid, decision):
    opinion = {
        "uid": uid, "decision": decision, "exclusion_reason": "", "notes": "",
        "library_id": _p22_member_library_id(uid), "revision": uid + 10,
        "source_row": {"tags": "tag-%s" % uid,
                       "screened_at": "2026-01-02 03:04:05",
                       "updated_at": None if uid % 2 else "2026-01-03 04:05:06"},
        "opinion_sha256": "", "source_row_sha256": "",
    }
    opinion["opinion_sha256"] = hashlib.sha256(_p22_canonical_json({
        "decision": decision.strip().lower(), "exclusion_reason": "",
        "notes": ""}).encode("utf-8")).hexdigest()
    opinion["source_row_sha256"] = hashlib.sha256(_p22_canonical_json(
        opinion["source_row"]).encode("utf-8")).hexdigest()
    return opinion


def _p22_observation_payload(*, task_id, instance, stage, item_key, decision,
                             sealed_at=P22_TS1):
    roster = _p22_default_roster()
    required_uids = sorted(entry["uid"] for entry in roster
                           if entry["required_screener"])
    opinions = [_p22_make_opinion(uid, decision) for uid in required_uids]
    bindings = [
        {"source_user_id": uid, "registration_id": "registration-%d" % uid,
         "generation": 1, "source_library_id": _p22_member_library_id(uid)}
        for uid in sorted(set(required_uids) | {USERS["owner"]})
    ]
    return {
        "schema": tf.OBSERVATION_PAYLOAD_SCHEMA,
        "task": {"task_id": task_id, "task_instance_id": instance,
                 "owner_user_id": USERS["owner"], "owner_username": "owner",
                 "owner_library_id": P22_OWNER_LIBRARY},
        "stage": stage, "item_key": item_key, "round_id": None,
        "kind": "unanimous", "trigger": "current_observation",
        "final_decision": decision, "roster": roster,
        "required_uids": required_uids, "opinions": opinions,
        "arbitration": None,
        "protocol": {"source_registration_id": "registration-%d"
                     % USERS["owner"], "roster_revision": 7},
        "source_request": {"operation": "completion_observe",
                           "request_id": "request-p22-1",
                           "request_sha256": "b" * 64},
        "sealed_at": sealed_at,
        "observation": {"host_operation": "completion_observe",
                        "host_stage": "fulltext", "host_item_keys": [item_key],
                        "observed_by_user_id": USERS["owner"],
                        "basis": "current_snapshot"},
        "source_bindings": bindings,
    }


def _p22_restore_env(key, previous):
    if previous is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = previous


def _p22_visible_true(info, user):
    return True


class _P22TaskLookup:
    """宿主 task_lookup 真实载体（生产由 main._task_or_404 提供）。"""

    def __init__(self, task_id, info):
        self._task_id = task_id
        self._info = info

    def __call__(self, wanted):
        if wanted != self._task_id:
            raise KeyError(wanted)
        return self._info


class P22Base(unittest.TestCase):
    """类级真实 users.db（auth 建号）＋用例级真实任务夹具（零 mock）。"""

    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls._users_root_obj = tempfile.TemporaryDirectory(
            prefix="rf216_p22_users_")
        cls.addClassCleanup(cls._users_root_obj.cleanup)
        cls.users_root = Path(cls._users_root_obj.name)
        previous = os.environ.get("COBOOKSHELF_DATA_DIR")
        cls.addClassCleanup(_p22_restore_env, "COBOOKSHELF_DATA_DIR", previous)
        os.environ["COBOOKSHELF_DATA_DIR"] = str(cls.users_root)
        for name in P22_USER_NAMES:
            created = auth.create_user(name, P22_PASSWORD)
            USERS[name] = int(created["id"])
        global _P22_PROVENANCE_PRINTED
        if not _P22_PROVENANCE_PRINTED:
            _P22_PROVENANCE_PRINTED = True
            print("P22_TEST_FILE_SHA",
                  _p22_sha256_file(Path(__file__).resolve()))
            print("P22_CURRENT_CANDIDATE_SHA",
                  _p22_sha256_file(_P22_CURRENT_SOURCE))
            print("P22_PRISMA_CANDIDATE_SHA",
                  _p22_sha256_file(_P22_PRISMA_SOURCE))
            print("P22_PRISMA_BASELINE_SHA",
                  _p22_sha256_file(_P22_BASELINE_PRISMA))
            print("P22_REPO_RUNTIME_SHA", _p22_sha256_file(
                _P22_REPO_ROOT / "custom_backend" / (
                    "team_screening_runtime.py")))
            print("P22_REPO_KEYED_READS_SHA", _p22_sha256_file(
                _P22_REPO_ROOT / "custom_backend" / "team_keyed_reads.py"))
            print("P22_REPO_ARBITRATION_SESSION_SHA", _p22_sha256_file(
                _P22_REPO_ROOT / "coscreen" / "arbitration_session.py"))
            print("P22_PYTHON", sys.version.split()[0])
            print("P22_SQLITE_VERSION", sqlite3.sqlite_version)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rf216_p22_case_")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)

    # -- 场景构建 helper（全部真实公共 setup API；P09 同构） ---------------

    def _task_info(self, task_dir, task_id):
        return tasks_mod.TaskInfo(task_id=task_id, dir=task_dir,
                                  display_name=task_id)

    def _make_runtime(self, task_dir, task_id):
        info = self._task_info(task_dir, task_id)

        def screener_db(resolved_info, username):
            return Path(resolved_info.dir) / (
                tasks_mod.slugify_screener(username) + ".db")

        return WorkflowRuntime(_P22TaskLookup(task_id, info), screener_db,
                               self.users_root, _p22_visible_true)

    def _owner_user(self):
        return {"user_id": USERS["owner"], "username": "owner"}

    def _init_team_task(self, name, task_id):
        task_dir = self.base / name
        task_dir.mkdir()
        collab.initialize_task_store(
            task_dir, task_id=task_id, owner_user_id=USERS["owner"],
            owner_username="owner")
        collab.set_business_role(
            task_dir / "collaboration.db", actor_user_id=USERS["owner"],
            user_id=USERS["owner"], business_role="reconciler",
            task_id=task_id)
        return task_dir

    def _grant_member(self, task_dir, task_id, username, role="coder"):
        collab.grant_role(
            task_dir / "collaboration.db", actor_user_id=USERS["owner"],
            user_id=USERS[username], role=role, task_id=task_id)

    def _choice_member(self, task_dir, task_id, username,
                       business_role="coder"):
        collab.set_business_role(
            task_dir / "collaboration.db", actor_user_id=USERS["owner"],
            user_id=USERS[username], business_role=business_role,
            task_id=task_id)

    def _screener_db(self, task_dir, username, *, articles=(), votes1=(),
                     votes2=()):
        path = task_dir / (tasks_mod.slugify_screener(username) + ".db")
        dbm.init_db(path)
        vote_keys = [row[0] for row in votes1] + [row[0] for row in votes2]
        seeded = list(dict.fromkeys(list(articles) + vote_keys))
        conn = sqlite3.connect(str(path), timeout=15)
        try:
            for order, key in enumerate(seeded):
                conn.execute(_P22_INSERT_ARTICLE,
                             (key, "p22-" + key, order))
            for key, decision, updated, screened in votes1:
                conn.execute(_P22_INSERT_STAGE1_VOTE,
                             (key, decision, "", "", "", screened, updated))
            for key, decision, updated, screened in votes2:
                conn.execute(_P22_INSERT_STAGE2_VOTE,
                             (key, decision, "", "", "", screened, updated))
            conn.commit()
        finally:
            conn.close()
        return path

    def _prepare_f2(self, collab_path, task_id, instance):
        tr.prepare_team_terminal_registry(collab_path)
        writer = _p22_borrowed_writer(collab_path)
        try:
            tr.register_protocol_on(
                writer, task_id=task_id, task_instance_id=instance,
                owner_user_id=USERS["owner"],
                owner_library_id=P22_OWNER_LIBRARY,
                creation_path="trusted_new_task")
            writer.commit()
        finally:
            _p22_close_quietly(writer)

    def _prepare_f1(self, collab_path):
        tf.prepare_team_terminal_facts(collab_path)

    def _append_collab_seal(self, collab_path, payload):
        writer = _p22_borrowed_writer(collab_path)
        try:
            record = tf.append_seal_on(writer, payload, origin="collab")
            writer.commit()
            return record
        finally:
            _p22_close_quietly(writer)

    def _team_projection(self, task_dir, task_id, keys):
        return tcr.current_team_projection(
            self._make_runtime(task_dir, task_id),
            self._task_info(task_dir, task_id), self._owner_user(),
            item_keys=tuple(keys))

    @staticmethod
    def _assert_count_anchor(counts):
        """工单钉死断言锚（逐字语义）：三桶合并==known_total。"""
        assert (counts['eligible'] + counts['blocked']
                + counts['unavailable'] == counts['known_total']), counts


class MultipleKeysOwnOutcomeTests(P22Base):
    """用例 1：每键各自用自己的有效当前结论；四态计数锚；两 stage 链一致。"""

    def test_multiple_keys_use_own_valid_outcome(self):
        task_id = "p22-t1-own-outcome"
        instance = "inst-p22-t1"
        task_dir = self._init_team_task("t1", task_id)
        for name in ("alice", "bob"):
            self._grant_member(task_dir, task_id, name)
            self._choice_member(task_dir, task_id, name)
        self._screener_db(task_dir, "owner", articles=("A", "B", "C", "D"))
        self._screener_db(task_dir, "alice", votes1=(
            ("A", "include", P22_VOTE_UPDATED, P22_VOTE_SCREENED),
            ("B", "include", P22_VOTE_UPDATED, P22_VOTE_SCREENED),
            ("C", "include", P22_VOTE_UPDATED, P22_VOTE_SCREENED)),
            votes2=(("A", "include", P22_VOTE_UPDATED, P22_VOTE_SCREENED),))
        self._screener_db(task_dir, "bob", votes1=(
            ("A", "include", P22_VOTE_UPDATED, P22_VOTE_SCREENED),
            ("B", "exclude", P22_VOTE_UPDATED, P22_VOTE_SCREENED)),
            votes2=(("A", "include", P22_VOTE_UPDATED, P22_VOTE_SCREENED),))
        collab_path = task_dir / "collaboration.db"
        self._prepare_f2(collab_path, task_id, instance)
        self._prepare_f1(collab_path)
        self._append_collab_seal(collab_path, _p22_observation_payload(
            task_id=task_id, instance=instance, stage="stage1", item_key="A",
            decision="include", sealed_at=P22_TS1))
        self._append_collab_seal(collab_path, _p22_observation_payload(
            task_id=task_id, instance=instance, stage="stage1", item_key="D",
            decision="exclude", sealed_at=P22_TS1))
        projection = self._team_projection(task_dir, task_id, ("A", "B", "C", "D"))
        results = projection["results"]
        # —— 每键各自结论 ——
        self.assertEqual(results["A"]["status"], "eligible")
        self.assertEqual(results["A"]["current_decision"], "include")
        self.assertEqual(results["A"]["deciding_stage"], 2,
                         "A：stage1 F1 + stage2 live 一致 → 链结论在 stage2")
        self.assertTrue(results["A"]["attestation"]["stage1"]["persisted"])
        self.assertIsNotNone(results["A"]["attestation"]["stage1"]["seal_id"])
        self.assertFalse(results["A"]["attestation"]["stage2"]["persisted"],
                         "A stage2：可信当前意见（live 一致）非 F1")
        self.assertEqual(results["A"]["roster_basis"], "completion")
        self.assertEqual(results["B"]["status"], "blocked",
                         "B：全员正式分歧 → awaiting_arbitration 不可定团队")
        self.assertIsNone(results["B"]["current_decision"])
        self.assertEqual(results["C"]["status"], "partial")
        self.assertEqual(results["C"]["raw_pending_members"], ["bob"],
                         "C：raw 个人完成度≠团队结论，仅暴露缺席名单")
        self.assertEqual(results["D"]["status"], "eligible")
        self.assertEqual(results["D"]["current_decision"], "exclude")
        self.assertEqual(results["D"]["deciding_stage"], 1)
        self.assertIsNone(results["D"]["stage2"],
                          "D：stage1 终局排除 → stage2 剪枝不读")
        self.assertEqual(results["D"]["attestation"]["stage1"]["persisted"],
                         True)
        # —— 两 stage 链一致：stage2 仅在 stage1 非终局排除键上评估 ——
        self.assertEqual(
            sorted(k for k, v in results.items() if v["stage2"] is not None),
            ["A", "B", "C"],
            "stage2 视图键域=非 stage1-final-exclude 键（D 剪枝）")
        # —— 四态计数锚（工单逐字） ——
        counts = projection["counts"]
        self.assertEqual(counts["eligible"], 2)
        self.assertEqual(counts["blocked"], 2, "blocked 合并桶含 partial")
        self.assertEqual(counts["partial"], 1)
        self.assertEqual(counts["unavailable"], 0)
        self.assertEqual(counts["known_total"], 4)
        self._assert_count_anchor(counts)
        self.assertEqual(projection["keys"], ["A", "B", "C", "D"])


class CompletedRosterAfterMemberChangesTests(P22Base):
    """用例 2：完成条目保留完成时结论/名单；新名单只作用于未完成（裁定锚）。"""

    def test_completed_roster_after_member_changes(self):
        task_id = "p22-t2-roster"
        instance = "inst-p22-t2"
        task_dir = self._init_team_task("t2", task_id)
        for name in ("alice", "bob"):
            self._grant_member(task_dir, task_id, name)
            self._choice_member(task_dir, task_id, name)
        self._screener_db(task_dir, "owner", articles=("R", "A2", "X"))
        self._screener_db(task_dir, "alice", votes1=(
            ("X", "include", P22_VOTE_UPDATED, P22_VOTE_SCREENED),))
        # bob 无票（X 部分完成）；R/A2 只存在于 F1 完成事实。
        collab_path = task_dir / "collaboration.db"
        self._prepare_f2(collab_path, task_id, instance)
        self._prepare_f1(collab_path)
        self._append_collab_seal(collab_path, _p22_observation_payload(
            task_id=task_id, instance=instance, stage="stage1", item_key="R",
            decision="exclude", sealed_at=P22_TS1))
        self._append_collab_seal(collab_path, _p22_observation_payload(
            task_id=task_id, instance=instance, stage="stage1", item_key="A2",
            decision="include", sealed_at=P22_TS1))
        self._append_collab_seal(collab_path, _p22_observation_payload(
            task_id=task_id, instance=instance, stage="stage2", item_key="A2",
            decision="include", sealed_at=P22_TS2))
        # —— 成员变动：新成员 carol 加入（当前名单=alice/bob/carol） ——
        self._grant_member(task_dir, task_id, "carol")
        self._choice_member(task_dir, task_id, "carol")
        projection = self._team_projection(task_dir, task_id, ("R", "A2", "X"))
        results = projection["results"]
        self.assertEqual(results["R"]["status"], "eligible")
        self.assertEqual(results["R"]["current_decision"], "exclude")
        self.assertEqual(results["R"]["roster_basis"], "completion")
        self.assertEqual(results["R"]["raw_pending_members"], [],
                         "新成员缺席不重开已完成条目（§9）")
        self.assertEqual(results["A2"]["status"], "eligible")
        self.assertEqual(results["A2"]["current_decision"], "include")
        self.assertEqual(results["A2"]["deciding_stage"], 2)
        for stage in ("stage1", "stage2"):
            self.assertTrue(
                results["A2"]["attestation"][stage]["persisted"],
                "A2 %s：F1 双段封存" % stage)
            self.assertIsNotNone(
                results["A2"]["attestation"][stage]["seal_id"])
        self.assertEqual(results["A2"]["roster_basis"], "completion")
        completion_uids = set(projection["roster"]["completion_required_uids"])
        self.assertEqual(completion_uids,
                         {USERS["alice"], USERS["bob"]},
                         "完成时名单保留（payload required_uids，不含 carol）")
        self.assertEqual(results["X"]["status"], "partial")
        self.assertEqual(sorted(results["X"]["raw_pending_members"]),
                         ["bob", "carol"],
                         "未完成键走当前名单（新成员 carol 计入待完成）")
        self.assertEqual(results["X"]["roster_basis"], "current")
        self.assertEqual(sorted(projection["roster"]["required"]),
                         ["alice", "bob", "carol"])
        counts = projection["counts"]
        self.assertEqual(counts["eligible"], 2)
        self.assertEqual(counts["partial"], 1)
        self.assertEqual(counts["known_total"], 3)
        self._assert_count_anchor(counts)
        # —— 对照锚：基线 outcomes（旧当前名单共识口径）对 R/A2 无结论 ——
        runtime = self._make_runtime(task_dir, task_id)
        with runtime.lock(task_id):
            ctx = runtime.context(task_id, self._owner_user())
            old_first, old_unresolved = bptm.outcomes(ctx, "stage1", "team")
        self.assertNotIn("R", old_first,
                         "旧口径：R 无 live 票 → 不进结论（P22 修此缺口）")
        self.assertNotIn("A2", old_first)
        self.assertEqual(old_unresolved, 1,
                         "旧口径：X 仅 alice 一票（bob/carol 缺席）＝部分"
                         "覆盖 unresolved；R/A2 无 live 票则完全不进旧结论")


class PartialMetricsAndRecordsTests(P22Base):
    """用例 3：source 坏不伪空/排除；metrics 计数锚与 records 集成。"""

    def test_partial_metrics_and_records(self):
        task_id = "p22-t3-partial"
        instance = "inst-p22-t3"
        task_dir = self._init_team_task("t3", task_id)
        for name in ("alice", "bob"):
            self._grant_member(task_dir, task_id, name)
            self._choice_member(task_dir, task_id, name)
        self._screener_db(task_dir, "owner", articles=("A", "B", "C"))
        self._screener_db(task_dir, "alice", votes1=(
            ("B", "include", P22_VOTE_UPDATED, P22_VOTE_SCREENED),
            ("C", "include", P22_VOTE_UPDATED, P22_VOTE_SCREENED)))
        bob_path = self._screener_db(task_dir, "bob")
        # bob 库损坏：存在但不可读（垃圾字节）→ 逐键 member_vote_unreadable。
        bob_path.write_bytes(b"not-a-sqlite-database-p22")
        collab_path = task_dir / "collaboration.db"
        self._prepare_f2(collab_path, task_id, instance)
        self._prepare_f1(collab_path)
        self._append_collab_seal(collab_path, _p22_observation_payload(
            task_id=task_id, instance=instance, stage="stage1", item_key="A",
            decision="exclude", sealed_at=P22_TS1))
        projection = self._team_projection(task_dir, task_id, ("A", "B", "C"))
        results = projection["results"]
        self.assertEqual(results["A"]["status"], "eligible",
                         "封存键经 collab 见证读，不受坏成员库连坐")
        for key in ("B", "C"):
            self.assertEqual(results[key]["status"], "unavailable")
            self.assertEqual(results[key]["issue"]["reason"],
                             "member_vote_unreadable")
            self.assertIsNone(results[key]["current_decision"])
        counts = projection["counts"]
        self.assertEqual(counts["eligible"], 1)
        self.assertEqual(counts["unavailable"], 2)
        self.assertEqual(counts["known_total"], 3,
                         "来源坏键保留在 known 域（不伪空/不排除）")
        self._assert_count_anchor(counts)
        # —— outcomes/summary 集成 ——
        first, first_unresolved, second, second_unresolved = \
            tcr.outcomes_from_current_team(projection)
        self.assertEqual(first, {"A": {"decision": "exclude",
                                       "exclusion_reason": ""}})
        self.assertEqual(first_unresolved, 2,
                         "issue 键计入 unresolved（不少算）")
        self.assertEqual(second, {})
        self.assertEqual(second_unresolved, 0)
        runtime = self._make_runtime(task_dir, task_id)
        with runtime.lock(task_id):
            ctx = runtime.context(task_id, self._owner_user())
            payload = ptm.summary(ctx, "team", runtime=runtime)
        self.assertIn("current_team", payload)
        self._assert_count_anchor(payload["current_team"]["counts"])
        self.assertEqual(payload["pending"]["team_unresolved"], 2)
        self.assertEqual(payload["fields"]["records_screened"], 1)
        self.assertTrue(any("unreadable" in warning
                            for warning in payload["warnings"]),
                        "来源坏如实上抛 warning，不静默")
        # —— records 路由（真实注册后直调 endpoint，零 HTTP 层） ——
        from fastapi import FastAPI
        app = FastAPI()
        app.state.review_workflow = runtime

        def require_user():
            return self._owner_user()

        ptm.register_prisma_task_metrics(app, require_user)
        endpoint = next(route.endpoint for route in app.router.routes
                        if getattr(route, "path", None)
                        == "/api/tasks/{task_id}/prisma-metrics/records")
        response = endpoint(task_id=task_id, mode="team",
                            user=self._owner_user())
        header_line = response.body.decode("utf-8-sig").splitlines()[0]
        self.assertEqual(
            header_line,
            "source_record_id,source_id,source_name,source_type,filename,"
            "row_index,state,canonical_key,original_title,initial_outcome,"
            "fulltext_outcome,metric_mode",
            "records 表头 12 列不变")
        self.assertEqual(
            response.headers["Content-Disposition"],
            'attachment; filename="prisma-task-records.csv"')
        # reviewer 模式原路径不受影响（旧 outcomes 分支）。
        reviewer_response = endpoint(task_id=task_id, mode="reviewer",
                                     user=self._owner_user())
        self.assertIn("initial_outcome", reviewer_response.body.decode(
            "utf-8-sig"))

    def test_unregistered_team_projection_returns_typed_503(self):
        """N4（P22 复审）支撑锚：F2 未登记的团队任务 mode=team → typed
        503（completion_source_unresolved），不逃逸全局兜底成通用 500。"""
        from fastapi import FastAPI, HTTPException
        task_id = "p22-t3b-unregistered"
        task_dir = self._init_team_task("t3b", task_id)
        self._grant_member(task_dir, task_id, "alice")
        self._choice_member(task_dir, task_id, "alice")
        self._screener_db(task_dir, "owner", articles=("K",))
        # 不做 _prepare_f2：无 F2 protocol 行 → plan task_not_registered。
        runtime = self._make_runtime(task_dir, task_id)
        app = FastAPI()
        app.state.review_workflow = runtime

        def require_user():
            return self._owner_user()

        ptm.register_prisma_task_metrics(app, require_user)
        records_ep = next(
            route.endpoint for route in app.router.routes
            if getattr(route, "path", None)
            == "/api/tasks/{task_id}/prisma-metrics/records")
        metrics_ep = next(
            route.endpoint for route in app.router.routes
            if getattr(route, "path", None)
            == "/api/tasks/{task_id}/prisma-metrics")
        for endpoint in (records_ep, metrics_ep):
            with self.assertRaises(HTTPException) as raised:
                endpoint(task_id=task_id, mode="team",
                         user=self._owner_user())
            self.assertEqual(raised.exception.status_code, 503,
                             "typed 503 而非通用 500")
            self.assertEqual(raised.exception.detail["error_code"],
                             "team.flow.unavailable")
            self.assertEqual(
                raised.exception.detail["params"]["reason"],
                "completion_source_unresolved")
        # reviewer 模式同任务不受影响（旧路径 200 形）。
        response = records_ep(task_id=task_id, mode="reviewer",
                              user=self._owner_user())
        self.assertTrue(response.body.decode("utf-8-sig").startswith(
            "source_record_id"))


class RawExportsAndGenericLatestUnchangedTests(P22Base):
    """用例 4：按人 raw/generic latest/旧登记零改动（AST+行为双锚）。"""

    @staticmethod
    def _function_ast(module_source, name):
        tree = ast.parse(module_source)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return ast.dump(node)
        raise AssertionError("function not found: %s" % name)

    @staticmethod
    def _registered_paths(module_source):
        tree = ast.parse(module_source)
        paths = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and node.args[0].value.startswith("/api/")):
                paths.append(node.args[0].value)
        return sorted(set(paths))

    def test_raw_exports_and_generic_latest_unchanged(self):
        baseline_source = _P22_BASELINE_PRISMA.read_text(encoding="utf-8")
        candidate_source = _P22_PRISMA_SOURCE.read_text(encoding="utf-8")
        # ① AST 锚：按人 raw（decisions）与 generic latest 消费（outcomes）
        #    两函数逐字不变。
        for name in ("decisions", "outcomes"):
            self.assertEqual(
                self._function_ast(baseline_source, name),
                self._function_ast(candidate_source, name),
                "%s 必须与基线 AST 全等（raw/generic latest 零改动）" % name)
        # ② 登记锚：两条路由仍注册（既有登记不删）。
        self.assertEqual(
            sorted(self._registered_paths(candidate_source)),
            ["/api/tasks/{task_id}/prisma-metrics",
             "/api/tasks/{task_id}/prisma-metrics/records"])
        # ③ 行为锚：同夹具下候选与基线输出全等（reviewer+team 双模式）。
        task_id = "p22-t4-raw"
        instance = "inst-p22-t4"
        task_dir = self._init_team_task("t4", task_id)
        for name in ("alice", "bob"):
            self._grant_member(task_dir, task_id, name)
            self._choice_member(task_dir, task_id, name)
        self._screener_db(task_dir, "owner", articles=("U", "V"))
        self._screener_db(task_dir, "alice", votes1=(
            ("U", "include", P22_VOTE_UPDATED, P22_VOTE_SCREENED),
            ("V", "include", P22_VOTE_UPDATED, P22_VOTE_SCREENED)))
        self._screener_db(task_dir, "bob", votes1=(
            ("U", "include", P22_VOTE_UPDATED, P22_VOTE_SCREENED),
            ("V", "exclude", P22_VOTE_UPDATED, P22_VOTE_SCREENED)))
        runtime = self._make_runtime(task_dir, task_id)
        alice_user = {"user_id": USERS["alice"], "username": "alice"}
        with runtime.lock(task_id):
            alice_ctx = runtime.context(task_id, alice_user)
            candidate_reviewer, _ = ptm.outcomes(
                alice_ctx, "stage1", "reviewer")
            baseline_reviewer, _ = bptm.outcomes(
                alice_ctx, "stage1", "reviewer")
            owner_ctx = runtime.context(task_id, self._owner_user())
            candidate_team = ptm.outcomes(owner_ctx, "stage1", "team")
            baseline_team = bptm.outcomes(owner_ctx, "stage1", "team")
        self.assertEqual(candidate_reviewer, baseline_reviewer,
                         "reviewer 模式=按人 raw 读，零改动")
        self.assertEqual(
            sorted(candidate_reviewer),
            ["U", "V"],
            "reviewer 模式返回 actor 自身决策（raw 个人口径）")
        self.assertEqual(candidate_team, baseline_team,
                         "team 模式旧入口行为零改动（当前名单共识+generic "
                         "latest 同路径）")
        self.assertEqual(candidate_team[0],
                         {"U": {"decision": "include",
                                "exclusion_reason": ""}})
        self.assertEqual(candidate_team[1], 1,
                         "V 分歧无可用快照 → unresolved=1（旧口径保留）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
