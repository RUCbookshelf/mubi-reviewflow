# -*- coding: utf-8 -*-
"""FLOW-01 阶段I·团队逐条筛选资格纯核心钉子（专属新文件，撤实现必红）。

纯 unittest、零 I/O、零第三方依赖：只测 ``coscreen/team_screening_eligibility.py``
的逐条状态机与跨阶段链。断言全部为**具体场景的具体期望**（状态/缺席名单/
采信记录/过期原因/阻断令牌），不复刻实现算法自我比较。

钉住的合同红线（TEAM_FLOW_REPAIR_CONTRACT_20261114 §3-§4 +
OWNER_BUSINESS_ROLE_DECISION_20261114 + 复审 F1-F5 修复裁定）：
- 空必需集/角色未配置/双角色 = awaiting_config，不得空集自动满足；
- 属主无默认业务角色：role 未配置即阻塞，不因「全员已投」放行；
- 非空已声明集合 1 人合法（不借旧 CSV 2-9 包限制）；
- 全员一致自动终局且优先于仲裁记录；历史 maybe=未完成（awaiting_members
  + 逐成员机读原因），不得计入「已完成」、不得进仲裁或经快照终局；
- 分歧只认逐条绑定（名集/字段/时间）的授权结论：B 变不废 A、A 自身变废 A、
  最新不适用不能遮蔽仍适用的旧记录；最新适用=数值 record_id 最大（同store
  追加序，created_at 只作绑定时间证明），record_id 合同为正整数；
- 仲裁采信要求 task_id 双方显式非空且相等（None/空串不是当前任务通配）；
- Iterable 输入（required/keys）构造时物化：生成器可重复评估不耗尽；
- 链上映射缺键=not_evaluated 缺输入诊断（与真实 awaiting_config 区分）；
- 仲裁终局 include 后曾个人 exclude 的成员同样获得下游资格；
- coding/appraisal 需复筛与初筛**当前都** final_include（防旧复筛终局绕过
  变化的上游）。
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from coscreen.team_screening_eligibility import (  # noqa: E402
    AWAITING_ARBITRATION,
    AWAITING_CONFIG,
    AWAITING_MEMBERS,
    FINAL_EXCLUDE,
    FINAL_INCLUDE,
    MISSING_VOTE,
    NON_FORMAL_VALUE,
    STALE_PARTICIPANTS,
    STALE_RECORD_TIME,
    STALE_VOTE_TIME,
    STALE_VOTES,
    ArbitrationRecord,
    ChainItem,
    ItemStatus,
    Participant,
    StageInputs,
    Vote,
    cleared_keys,
    configuration_status,
    evaluate_chain,
    evaluate_stage,
)

_T0 = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)


def _vote(decision: str, *, at: datetime = _T0, reason: str = "", notes: str = "") -> Vote:
    return Vote(decision=decision, exclusion_reason=reason, notes=notes, decided_at=at)


def _row(votes: dict[str, Vote], *, final: str, status: str = "adjudicated") -> dict:
    """按合并表列形构造快照行（decision_<name>/exclusion_reason_<name>/notes_<name>）。"""
    row: dict[str, object] = {}
    for name, vote in votes.items():
        row[f"decision_{name}"] = vote.decision
        row[f"exclusion_reason_{name}"] = vote.exclusion_reason
        row[f"notes_{name}"] = vote.notes
    row["arbitration_status"] = status
    row["final_decision"] = final
    return row


def _record(
    record_id: int,
    rows: dict[str, dict],
    *,
    created_at: datetime | None = _T0 + timedelta(hours=1),
    stage: str = "stage1",
    round_id: str | None = None,
    task_id: str | None = "T1",
) -> ArbitrationRecord:
    return ArbitrationRecord(
        record_id=record_id, stage=stage, created_at=created_at,
        arbitrator_id="arb-1", source="session", task_id=task_id,
        round_id=round_id, rows=rows)


def _team_inputs(**overrides):
    """两名 coder（alice/bob）+ 未开参与的仲裁者 carol 的标准名册。"""
    participants = (
        Participant("alice", "coder"),
        Participant("bob", "coder"),
        Participant("carol", "reconciler"),
    )
    base = dict(
        stage="stage1", participants=participants, required=("alice", "bob"),
        votes={}, arbitrations=(), task_id="T1", round_id=None, keys=None)
    base.update(overrides)
    return StageInputs(**base)


class ConfigurationGateTests(unittest.TestCase):
    """awaiting_config：空集/属主未选角色/双角色——不猜选、不给默认。"""

    def test_empty_required_is_awaiting_config_for_every_key(self):
        inputs = _team_inputs(required=(), votes={"alice": {"K1": _vote("include")}})
        result = evaluate_stage(inputs)
        self.assertEqual(result["K1"].status, AWAITING_CONFIG)
        status = configuration_status(inputs)
        self.assertFalse(status.ok)
        self.assertTrue(status.empty_required)
        self.assertEqual(status.detail, "no_required_participants")

    def test_owner_without_business_role_blocks_even_when_members_agree(self):
        # 属主无默认业务角色：即便两名 coder 全员一致，也必须先配置。
        participants = (
            Participant("owner", None, is_owner=True),
            Participant("alice", "coder"),
            Participant("bob", "coder"),
        )
        inputs = _team_inputs(
            participants=participants, required=("alice", "bob"),
            votes={"alice": {"K1": _vote("include")}, "bob": {"K1": _vote("include")}})
        result = evaluate_stage(inputs)
        self.assertEqual(result["K1"].status, AWAITING_CONFIG)
        self.assertEqual(
            configuration_status(inputs).detail, "role_unconfigured:owner")

    def test_dual_role_conflict_is_awaiting_config(self):
        participants = (
            Participant("alice", "coder"),
            Participant("bob", None, dual_role=True),
        )
        inputs = _team_inputs(
            participants=participants, required=("alice", "bob"),
            votes={"alice": {"K1": _vote("include")}, "bob": {"K1": _vote("include")}})
        self.assertEqual(evaluate_stage(inputs)["K1"].status, AWAITING_CONFIG)
        self.assertEqual(
            configuration_status(inputs).detail, "role_conflict:bob")

    def test_complete_configuration_is_ok(self):
        status = configuration_status(_team_inputs())
        self.assertTrue(status.ok)
        self.assertEqual(status.required, ("alice", "bob"))
        self.assertEqual(status.detail, "")

    def test_single_required_member_is_a_legal_declared_set(self):
        # 非空已声明集合允许 1 人；旧 CSV 合并工具的 2-9 包限制不借入。
        inputs = _team_inputs(
            required=("alice",), votes={"alice": {"K1": _vote("include")}})
        item = evaluate_stage(inputs)["K1"]
        self.assertEqual(item.status, FINAL_INCLUDE)
        self.assertEqual(item.decided_by, "consensus")


class MemberCompletionTests(unittest.TestCase):
    """awaiting_members：无决定成员仍是待完成者；逐条互不拖累。"""

    def test_missing_member_is_reported_with_names(self):
        inputs = _team_inputs(votes={
            "alice": {"K1": _vote("include"), "K2": _vote("include")},
            "bob": {"K1": _vote("include")},  # bob 未完成 K2
        })
        result = evaluate_stage(inputs)
        self.assertEqual(result["K2"].status, AWAITING_MEMBERS)
        self.assertEqual(result["K2"].pending_members, ("bob",))
        # 单条合格不被另一条未完成整批锁住。
        self.assertEqual(result["K1"].status, FINAL_INCLUDE)

    def test_unvoted_key_counts_every_required_member_pending(self):
        inputs = _team_inputs(votes={"alice": {"K1": _vote("include")}})
        result = evaluate_stage(inputs)
        self.assertEqual(result["K1"].status, AWAITING_MEMBERS)
        self.assertEqual(result["K1"].pending_members, ("bob",))

    def test_non_required_roster_member_votes_never_block(self):
        # 未开参与的仲裁者 carol 有历史意见：不参与终局，也不阻塞。
        inputs = _team_inputs(votes={
            "alice": {"K1": _vote("include")},
            "bob": {"K1": _vote("include")},
            "carol": {"K1": _vote("exclude")},
        })
        item = evaluate_stage(inputs)["K1"]
        self.assertEqual(item.status, FINAL_INCLUDE)
        self.assertEqual(item.decided_by, "consensus")

    def test_unvoted_key_reason_is_missing_vote(self):
        inputs = _team_inputs(votes={"alice": {"K1": _vote("include")}})
        item = evaluate_stage(inputs)["K1"]
        self.assertEqual(item.pending_reasons, (("bob", MISSING_VOTE),))

    def test_unanimous_maybe_is_awaiting_members_even_with_bound_record(self):
        # F1：历史 maybe 是未完成输入——全 maybe=全员待完成；即使存在字段/
        # 时间/成员都吻合的仲裁记录也不能经快照终局、不得进入仲裁。
        votes = {"alice": _vote("maybe"), "bob": _vote("maybe")}
        record = _record(7, {"K1": _row(votes, final="include")})
        inputs = _team_inputs(votes={
            "alice": {"K1": votes["alice"]}, "bob": {"K1": votes["bob"]}},
            arbitrations=(record,))
        item = evaluate_stage(inputs)["K1"]
        self.assertEqual(item.status, AWAITING_MEMBERS)
        self.assertEqual(item.pending_members, ("alice", "bob"))
        self.assertEqual(item.pending_reasons,
                         (("alice", NON_FORMAL_VALUE), ("bob", NON_FORMAL_VALUE)))
        self.assertEqual(item.stale_arbitrations, ())
        self.assertIsNone(item.applied_arbitration)
        self.assertIsNone(item.decided_by)

    def test_include_with_maybe_is_awaiting_members_not_arbitration(self):
        # F1（原「include vs maybe=分歧」期望纠正）：maybe 持有者未完成二选一，
        # 绑定吻合的仲裁 include 不能替其终局。
        votes = {"alice": _vote("include"), "bob": _vote("maybe")}
        record = _record(7, {"K1": _row(votes, final="include")})
        inputs = _team_inputs(votes={
            "alice": {"K1": votes["alice"]}, "bob": {"K1": votes["bob"]}},
            arbitrations=(record,))
        item = evaluate_stage(inputs)["K1"]
        self.assertEqual(item.status, AWAITING_MEMBERS)
        self.assertEqual(item.pending_members, ("bob",))
        self.assertEqual(item.pending_reasons, (("bob", NON_FORMAL_VALUE),))
        self.assertEqual(item.stale_arbitrations, ())
        self.assertIsNone(item.applied_arbitration)
        self.assertIsNone(item.decided_by)

    def test_exclude_with_maybe_stays_awaiting_members_with_bound_record(self):
        votes = {"alice": _vote("exclude", reason="r"), "bob": _vote("maybe")}
        record = _record(7, {"K1": _row(votes, final="exclude")})
        inputs = _team_inputs(votes={
            "alice": {"K1": votes["alice"]}, "bob": {"K1": votes["bob"]}},
            arbitrations=(record,))
        item = evaluate_stage(inputs)["K1"]
        self.assertEqual(item.status, AWAITING_MEMBERS)
        self.assertEqual(item.pending_members, ("bob",))
        self.assertIsNone(item.applied_arbitration)

    def test_stage2_maybe_is_also_awaiting_members(self):
        # F1：初筛/复筛同规则。
        inputs = _team_inputs(stage="stage2", votes={
            "alice": {"K1": _vote("include")}, "bob": {"K1": _vote("maybe")}})
        item = evaluate_stage(inputs)["K1"]
        self.assertEqual(item.status, AWAITING_MEMBERS)
        self.assertEqual(item.pending_members, ("bob",))
        self.assertEqual(item.pending_reasons, (("bob", NON_FORMAL_VALUE),))


class ConsensusTests(unittest.TestCase):
    """全员一致自动终局（正式二选一判断之间）。"""

    def test_unanimous_include_and_exclude_finalize(self):
        agree_in = _team_inputs(votes={
            "alice": {"K1": _vote("include")}, "bob": {"K1": _vote("include")}})
        agree_out = _team_inputs(votes={
            "alice": {"K1": _vote("exclude", reason="r")},
            "bob": {"K1": _vote("exclude", reason="r")}})
        self.assertEqual(evaluate_stage(agree_in)["K1"].status, FINAL_INCLUDE)
        self.assertEqual(evaluate_stage(agree_out)["K1"].status, FINAL_EXCLUDE)

    def test_consensus_beats_applicable_arbitration_record(self):
        # 成员改票后全员一致：以当前一致为准，不再消费旧仲裁结论。
        old_votes = {"alice": _vote("exclude"), "bob": _vote("include")}
        record = _record(7, {"K1": _row(old_votes, final="exclude")})
        inputs = _team_inputs(votes={
            "alice": {"K1": _vote("include", at=_T0 + timedelta(hours=2))},
            "bob": {"K1": _vote("include", at=_T0 + timedelta(hours=2))}},
            arbitrations=(record,))
        item = evaluate_stage(inputs)["K1"]
        self.assertEqual(item.status, FINAL_INCLUDE)
        self.assertEqual(item.decided_by, "consensus")
        self.assertIsNone(item.applied_arbitration)


class ArbitrationBindingTests(unittest.TestCase):
    """分歧只认逐条绑定的授权结论：B 变不废 A、A 变废 A、最新不遮旧。"""

    def test_disagreement_without_records_awaits_arbitration(self):
        inputs = _team_inputs(votes={
            "alice": {"K1": _vote("include")}, "bob": {"K1": _vote("exclude", reason="r")}})
        item = evaluate_stage(inputs)["K1"]
        self.assertEqual(item.status, AWAITING_ARBITRATION)
        self.assertEqual(item.stale_arbitrations, ())

    def test_applicable_record_finalizes_conflict(self):
        votes = {"alice": _vote("include"), "bob": _vote("exclude", reason="r")}
        record = _record(7, {"K1": _row(votes, final="include")})
        inputs = _team_inputs(
            votes={"alice": {"K1": votes["alice"]}, "bob": {"K1": votes["bob"]}},
            arbitrations=(record,))
        item = evaluate_stage(inputs)["K1"]
        self.assertEqual(item.status, FINAL_INCLUDE)
        self.assertEqual(item.decided_by, "arbitration")
        self.assertEqual(item.applied_arbitration, 7)

    def test_other_item_change_does_not_void_this_item_record(self):
        # 同一记录覆盖 K1/K2：仅 K2 判断变化 → K1 结论仍有效（B 变不废 A）。
        k1_votes = {"alice": _vote("include"), "bob": _vote("exclude", reason="r")}
        k2_votes = {"alice": _vote("include"), "bob": _vote("include")}
        record = _record(7, {
            "K1": _row(k1_votes, final="include"),
            "K2": _row(k2_votes, final="include"),
        })
        inputs = _team_inputs(votes={
            "alice": {"K1": _vote("include"), "K2": _vote("include")},
            # bob 对 K2 改票 → K2 快照过期；K1 不受影响。
            "bob": {"K1": _vote("exclude", reason="r"), "K2": _vote("exclude")},
        }, arbitrations=(record,))
        result = evaluate_stage(inputs)
        self.assertEqual(result["K1"].status, FINAL_INCLUDE)
        self.assertEqual(result["K1"].applied_arbitration, 7)
        self.assertEqual(result["K2"].status, AWAITING_ARBITRATION)
        self.assertEqual(result["K2"].stale_arbitrations, ((7, STALE_VOTES),))

    def test_own_vote_change_voids_own_record(self):
        k1_votes = {"alice": _vote("include"), "bob": _vote("exclude", reason="r")}
        record = _record(7, {"K1": _row(k1_votes, final="include")})
        inputs = _team_inputs(votes={
            # alice 补了排除理由附注（判断字段变化，分歧仍在）→ 该键旧结论
            # 过期（A 自身变化废 A），不能继续放行旧仲裁 include。
            "alice": {"K1": _vote("include", reason="r2")},
            "bob": {"K1": _vote("exclude", reason="r")},
        }, arbitrations=(record,))
        item = evaluate_stage(inputs)["K1"]
        self.assertEqual(item.status, AWAITING_ARBITRATION)
        self.assertEqual(item.stale_arbitrations, ((7, STALE_VOTES),))

    def test_latest_inapplicable_record_does_not_mask_older_applicable(self):
        votes = {"alice": _vote("include"), "bob": _vote("exclude", reason="r")}
        older = _record(7, {"K1": _row(votes, final="include")})
        # 更新的记录绑定了另一套参与集（含 carol）→ 对当前状态不适用。
        newer_votes = {**votes, "carol": _vote("include")}
        newer = _record(
            9, {"K1": _row(newer_votes, final="exclude")},
            created_at=_T0 + timedelta(hours=2))
        inputs = _team_inputs(
            votes={"alice": {"K1": votes["alice"]}, "bob": {"K1": votes["bob"]}},
            arbitrations=(newer, older))
        item = evaluate_stage(inputs)["K1"]
        self.assertEqual(item.status, FINAL_INCLUDE)
        self.assertEqual(item.applied_arbitration, 7)
        self.assertIn((9, STALE_PARTICIPANTS), item.stale_arbitrations)

    def test_latest_applicable_record_wins(self):
        votes = {"alice": _vote("include"), "bob": _vote("exclude", reason="r")}
        older = _record(7, {"K1": _row(votes, final="include")})
        newer = _record(
            9, {"K1": _row(votes, final="exclude")},
            created_at=_T0 + timedelta(hours=2))
        inputs = _team_inputs(
            votes={"alice": {"K1": votes["alice"]}, "bob": {"K1": votes["bob"]}},
            arbitrations=(older, newer))
        item = evaluate_stage(inputs)["K1"]
        self.assertEqual(item.status, FINAL_EXCLUDE)
        self.assertEqual(item.applied_arbitration, 9)

    def test_same_timestamp_ids_9_and_10_pick_numeric_10(self):
        # F2：同 created_at 时按数值 record_id 取最新（原字符串字典序会取 9）。
        votes = {"alice": _vote("include"), "bob": _vote("exclude", reason="r")}
        at = _T0 + timedelta(hours=1)
        rec_9 = _record(9, {"K1": _row(votes, final="include")}, created_at=at)
        rec_10 = _record(10, {"K1": _row(votes, final="exclude")}, created_at=at)
        inputs = _team_inputs(
            votes={"alice": {"K1": votes["alice"]}, "bob": {"K1": votes["bob"]}},
            arbitrations=(rec_9, rec_10))
        item = evaluate_stage(inputs)["K1"]
        self.assertEqual(item.status, FINAL_EXCLUDE)
        self.assertEqual(item.applied_arbitration, 10)

    def test_same_timestamp_ids_99_and_100_pick_numeric_100(self):
        votes = {"alice": _vote("include"), "bob": _vote("exclude", reason="r")}
        at = _T0 + timedelta(hours=1)
        rec_99 = _record(99, {"K1": _row(votes, final="include")}, created_at=at)
        rec_100 = _record(100, {"K1": _row(votes, final="exclude")}, created_at=at)
        inputs = _team_inputs(
            votes={"alice": {"K1": votes["alice"]}, "bob": {"K1": votes["bob"]}},
            arbitrations=(rec_99, rec_100))
        item = evaluate_stage(inputs)["K1"]
        self.assertEqual(item.status, FINAL_EXCLUDE)
        self.assertEqual(item.applied_arbitration, 100)

    def test_higher_id_with_earlier_created_at_wins(self):
        # F2：追加序（数值 id）优先——较新 id 的快照时间略早但仍满足绑定时
        # 取 id 大者；墙钟回拨不改变追加事实。
        votes = {"alice": _vote("include"), "bob": _vote("exclude", reason="r")}
        higher_id_earlier = _record(
            12, {"K1": _row(votes, final="exclude")},
            created_at=_T0 + timedelta(minutes=30))
        lower_id_later = _record(
            8, {"K1": _row(votes, final="include")},
            created_at=_T0 + timedelta(hours=2))
        inputs = _team_inputs(
            votes={"alice": {"K1": votes["alice"]}, "bob": {"K1": votes["bob"]}},
            arbitrations=(lower_id_later, higher_id_earlier))
        item = evaluate_stage(inputs)["K1"]
        self.assertEqual(item.status, FINAL_EXCLUDE)
        self.assertEqual(item.applied_arbitration, 12)

    def test_record_id_contract_rejects_non_positive_and_non_int(self):
        # F2：record_id 合同为正整数——bool（int 子类）/字符串/0/负数/None
        # 一律 ValueError，不给无法验证数值顺序的 ID 授予「最新」含义。
        votes = {"alice": _vote("include"), "bob": _vote("exclude", reason="r")}
        rows = {"K1": _row(votes, final="include")}
        for bad in (True, "9", 0, -3, None):
            with self.subTest(bad=bad):
                record = ArbitrationRecord(
                    record_id=bad, stage="stage1",
                    created_at=_T0 + timedelta(hours=1), arbitrator_id="arb-1",
                    source="session", task_id="T1", round_id=None, rows=rows)
                with self.assertRaises(ValueError):
                    evaluate_stage(_team_inputs(votes={
                        "alice": {"K1": votes["alice"]},
                        "bob": {"K1": votes["bob"]}},
                        arbitrations=(record,)))

    def test_record_from_different_explicit_task_is_irrelevant(self):
        # F3：双方都显式但任务不同=无关记录（不采信、不入过期清单）。
        votes = {"alice": _vote("include"), "bob": _vote("exclude", reason="r")}
        record = _record(7, {"K1": _row(votes, final="include")}, task_id="OTHER")
        inputs = _team_inputs(
            votes={"alice": {"K1": votes["alice"]}, "bob": {"K1": votes["bob"]}},
            arbitrations=(record,))
        item = evaluate_stage(inputs)["K1"]
        self.assertEqual(item.status, AWAITING_ARBITRATION)
        self.assertEqual(item.stale_arbitrations, ())
        self.assertIsNone(item.applied_arbitration)

    def test_undeclared_input_task_accepts_no_records(self):
        # F3：调用方漏声明 task（None）——任何有/无 task 的记录都不能采信。
        votes = {"alice": _vote("include"), "bob": _vote("exclude", reason="r")}
        with_task = _record(7, {"K1": _row(votes, final="include")}, task_id="T1")
        without_task = _record(8, {"K1": _row(votes, final="include")}, task_id=None)
        for record in (with_task, without_task):
            with self.subTest(record_task=record.task_id):
                inputs = _team_inputs(
                    task_id=None,
                    votes={"alice": {"K1": votes["alice"]},
                           "bob": {"K1": votes["bob"]}},
                    arbitrations=(record,))
                item = evaluate_stage(inputs)["K1"]
                self.assertEqual(item.status, AWAITING_ARBITRATION)
                self.assertEqual(item.stale_arbitrations, ())
                self.assertIsNone(item.applied_arbitration)

    def test_record_without_explicit_task_is_not_accepted(self):
        # F3：输入声明了任务、记录未声明（None）——绑定不可验证即不采信。
        votes = {"alice": _vote("include"), "bob": _vote("exclude", reason="r")}
        record = _record(7, {"K1": _row(votes, final="include")}, task_id=None)
        inputs = _team_inputs(
            votes={"alice": {"K1": votes["alice"]}, "bob": {"K1": votes["bob"]}},
            arbitrations=(record,))
        item = evaluate_stage(inputs)["K1"]
        self.assertEqual(item.status, AWAITING_ARBITRATION)
        self.assertIsNone(item.applied_arbitration)

    def test_both_sides_without_task_is_not_bound(self):
        # F3：两侧都 None 也不算已绑定（None≠当前任务）。
        votes = {"alice": _vote("include"), "bob": _vote("exclude", reason="r")}
        record = _record(7, {"K1": _row(votes, final="include")}, task_id=None)
        inputs = _team_inputs(
            task_id=None,
            votes={"alice": {"K1": votes["alice"]}, "bob": {"K1": votes["bob"]}},
            arbitrations=(record,))
        item = evaluate_stage(inputs)["K1"]
        self.assertEqual(item.status, AWAITING_ARBITRATION)
        self.assertIsNone(item.applied_arbitration)

    def test_empty_task_identifier_is_not_a_wildcard(self):
        # F3：空串不是显式任务标识——任一侧为空都不采信。
        votes = {"alice": _vote("include"), "bob": _vote("exclude", reason="r")}
        cases = (
            {"task_id": "", "record_task": "T1"},
            {"task_id": "T1", "record_task": ""},
        )
        for case in cases:
            with self.subTest(**case):
                scoped = _record(
                    7, {"K1": _row(votes, final="include")},
                    task_id=case["record_task"])
                inputs = _team_inputs(
                    task_id=case["task_id"],
                    votes={"alice": {"K1": votes["alice"]},
                           "bob": {"K1": votes["bob"]}},
                    arbitrations=(scoped,))
                item = evaluate_stage(inputs)["K1"]
                self.assertEqual(item.status, AWAITING_ARBITRATION)
                self.assertIsNone(item.applied_arbitration)

    def test_vote_newer_than_snapshot_is_stale_even_if_fields_match(self):
        votes = {"alice": _vote("include"), "bob": _vote("exclude", reason="r")}
        record = _record(7, {"K1": _row(votes, final="include")})
        # 判断字段相同但落库时间晚于快照 → 过期（fail-closed）。
        later = _T0 + timedelta(hours=5)
        inputs = _team_inputs(votes={
            "alice": {"K1": _vote("include", at=later)},
            "bob": {"K1": _vote("exclude", reason="r", at=later)},
        }, arbitrations=(record,))
        item = evaluate_stage(inputs)["K1"]
        self.assertEqual(item.status, AWAITING_ARBITRATION)
        self.assertEqual(item.stale_arbitrations, ((7, STALE_VOTE_TIME),))

    def test_record_without_created_at_is_stale(self):
        votes = {"alice": _vote("include"), "bob": _vote("exclude", reason="r")}
        record = _record(7, {"K1": _row(votes, final="include")}, created_at=None)
        inputs = _team_inputs(
            votes={"alice": {"K1": votes["alice"]}, "bob": {"K1": votes["bob"]}},
            arbitrations=(record,))
        item = evaluate_stage(inputs)["K1"]
        self.assertEqual(item.status, AWAITING_ARBITRATION)
        self.assertEqual(item.stale_arbitrations, ((7, STALE_RECORD_TIME),))

    def test_participant_set_change_makes_record_stale(self):
        old_votes = {"alice": _vote("include"), "bob": _vote("exclude", reason="r")}
        record = _record(7, {"K1": _row(old_votes, final="include")})
        # 新增必需成员 dave（本轮参与集变化）→ 旧结论过期。
        participants = (
            Participant("alice", "coder"), Participant("bob", "coder"),
            Participant("carol", "reconciler"), Participant("dave", "coder"))
        inputs = _team_inputs(
            participants=participants,
            required=("alice", "bob", "dave"),
            votes={"alice": {"K1": _vote("include")},
                   "bob": {"K1": _vote("exclude", reason="r")},
                   "dave": {"K1": _vote("include")}},
            arbitrations=(record,))
        item = evaluate_stage(inputs)["K1"]
        self.assertEqual(item.status, AWAITING_ARBITRATION)
        self.assertEqual(item.stale_arbitrations, ((7, STALE_PARTICIPANTS),))

    def test_round_scope_mismatch_is_irrelevant_not_stale(self):
        votes = {"alice": _vote("include"), "bob": _vote("exclude", reason="r")}
        record = _record(7, {"K1": _row(votes, final="include")}, round_id="round-2")
        inputs = _team_inputs(
            votes={"alice": {"K1": votes["alice"]}, "bob": {"K1": votes["bob"]}},
            arbitrations=(record,), round_id="round-1")
        item = evaluate_stage(inputs)["K1"]
        self.assertEqual(item.status, AWAITING_ARBITRATION)
        self.assertEqual(item.stale_arbitrations, ())

    def test_non_adjudicated_row_is_rejected(self):
        votes = {"alice": _vote("include"), "bob": _vote("exclude", reason="r")}
        record = _record(7, {"K1": _row(votes, final="include", status="unresolved")})
        inputs = _team_inputs(
            votes={"alice": {"K1": votes["alice"]}, "bob": {"K1": votes["bob"]}},
            arbitrations=(record,))
        item = evaluate_stage(inputs)["K1"]
        self.assertEqual(item.status, AWAITING_ARBITRATION)
        self.assertEqual(item.stale_arbitrations, ((7, "invalid_final"),))


class ChainEligibilityTests(unittest.TestCase):
    """上游链：复筛需初筛终局纳入；编码/评价需复筛+初筛都终局纳入。"""

    @staticmethod
    def _stage1_eval(statuses: dict[str, str]) -> dict[str, ItemStatus]:
        return {
            key: ItemStatus(key=key, stage="stage1", status=status)
            for key, status in statuses.items()}

    def test_stage2_open_only_after_stage1_final_include(self):
        stage1 = self._stage1_eval({"K1": FINAL_INCLUDE, "K2": AWAITING_ARBITRATION,
                                    "K3": FINAL_EXCLUDE, "K4": AWAITING_MEMBERS})
        stage2 = {}
        chain = evaluate_chain(stage1, stage2, "stage2")
        self.assertTrue(chain["K1"].cleared)
        self.assertIsNone(chain["K1"].blocked_reason)
        self.assertEqual(
            chain["K2"].blocked_reason, "upstream_stage1_awaiting_arbitration")
        self.assertEqual(
            chain["K3"].blocked_reason, "upstream_stage1_final_exclude")
        self.assertEqual(
            chain["K4"].blocked_reason, "upstream_stage1_awaiting_members")

    def test_arbitration_include_grants_downstream_to_member_who_excluded(self):
        # alice 曾 exclude，经仲裁终局 include：条目下游资格与个人意见无关。
        stage1 = self._stage1_eval({"K1": FINAL_INCLUDE})
        stage2 = {
            "K1": ItemStatus(
                key="K1", stage="stage2", status=FINAL_INCLUDE,
                applied_arbitration=7, decided_by="arbitration")}
        for target in ("coding", "appraisal"):
            chain = evaluate_chain(stage1, stage2, target)
            self.assertTrue(chain["K1"].cleared, target)
        self.assertEqual(cleared_keys(evaluate_chain(stage1, stage2, "coding")), ["K1"])

    def test_coding_requires_current_stage1_final_not_stale_one(self):
        # 复筛已终局，但上游初筛随后变化 → 编码仍被阻断（不能用旧复筛绕过）。
        stage1 = self._stage1_eval({"K1": AWAITING_ARBITRATION})
        stage2 = {"K1": ItemStatus(
            key="K1", stage="stage2", status=FINAL_INCLUDE, decided_by="consensus")}
        chain = evaluate_chain(stage1, stage2, "coding")
        self.assertFalse(chain["K1"].cleared)
        self.assertEqual(
            chain["K1"].blocked_reason, "upstream_stage1_awaiting_arbitration")

    def test_coding_blocked_when_stage2_not_final_include(self):
        stage1 = self._stage1_eval({"K1": FINAL_INCLUDE})
        stage2 = {"K1": ItemStatus(
            key="K1", stage="stage2", status=FINAL_EXCLUDE, decided_by="consensus")}
        chain = evaluate_chain(stage1, stage2, "appraisal")
        self.assertIsInstance(chain["K1"], ChainItem)
        self.assertFalse(chain["K1"].cleared)
        self.assertEqual(
            chain["K1"].blocked_reason, "upstream_stage2_final_exclude")

    def test_chain_key_domain_defaults_to_union(self):
        stage1 = self._stage1_eval({"K1": FINAL_INCLUDE})
        stage2 = {"K2": ItemStatus(
            key="K2", stage="stage2", status=FINAL_INCLUDE)}
        chain = evaluate_chain(stage1, stage2, "coding")
        self.assertEqual(sorted(chain), ["K1", "K2"])
        # K2 不在初筛键域=未评估（缺输入诊断），不是配置缺失。
        self.assertEqual(chain["K2"].blocked_reason, "upstream_stage1_not_evaluated")
        self.assertIsNone(chain["K2"].stage1_status)

    def test_missing_stage1_key_reports_not_evaluated(self):
        # F5：缺键=未评估（not_evaluated），不得误报为「角色待配置」。
        stage2 = {"K1": ItemStatus(
            key="K1", stage="stage2", status=FINAL_INCLUDE)}
        chain = evaluate_chain({}, stage2, "stage2")
        item = chain["K1"]
        self.assertFalse(item.cleared)
        self.assertIsNone(item.stage1_status)
        self.assertEqual(item.blocked_reason, "upstream_stage1_not_evaluated")

    def test_missing_stage2_key_reports_not_evaluated_for_coding(self):
        stage1 = self._stage1_eval({"K1": FINAL_INCLUDE})
        chain = evaluate_chain(stage1, {}, "coding")
        item = chain["K1"]
        self.assertFalse(item.cleared)
        self.assertIsNone(item.stage2_status)
        self.assertEqual(item.blocked_reason, "upstream_stage2_not_evaluated")

    def test_real_awaiting_config_is_not_relabelled(self):
        # F5 反向钉：真实配置缺失仍是 awaiting_config，不被 not_evaluated 误改。
        stage1 = self._stage1_eval({"K1": AWAITING_CONFIG})
        chain = evaluate_chain(stage1, {}, "stage2")
        item = chain["K1"]
        self.assertFalse(item.cleared)
        self.assertEqual(item.stage1_status, AWAITING_CONFIG)
        self.assertEqual(item.blocked_reason, "upstream_stage1_awaiting_config")


class InputMaterializationTests(unittest.TestCase):
    """Iterable 输入构造时物化：生成器不耗尽，同一输入可重复评估（F4）。"""

    def test_required_generator_supports_repeated_evaluation(self):
        # required 生成器：先 configuration_status 再 evaluate_stage，成员与
        # 状态不变（原实现第二次评估会 awaiting_config）。
        inputs = _team_inputs(
            required=(name for name in ("alice", "bob")),
            votes={"alice": {"K1": _vote("include")},
                   "bob": {"K1": _vote("include")}})
        status = configuration_status(inputs)
        self.assertTrue(status.ok)
        self.assertEqual(status.required, ("alice", "bob"))
        self.assertEqual(inputs.required, ("alice", "bob"))
        item = evaluate_stage(inputs)["K1"]
        self.assertEqual(item.status, FINAL_INCLUDE)
        self.assertEqual(item.decided_by, "consensus")

    def test_keys_generator_supports_repeated_evaluation(self):
        # keys 生成器：连续两次 evaluate_stage 键域不变、结果相等（原实现
        # 第二次会静默空域）。
        inputs = _team_inputs(
            keys=(key for key in ("K1",)),
            votes={"alice": {"K1": _vote("include"), "K2": _vote("include")},
                   "bob": {"K1": _vote("include"), "K2": _vote("include")}})
        first = evaluate_stage(inputs)
        second = evaluate_stage(inputs)
        self.assertEqual(sorted(first), ["K1"])
        self.assertEqual(sorted(second), ["K1"])
        self.assertEqual(first, second)
        self.assertEqual(first["K1"].status, FINAL_INCLUDE)
        self.assertEqual(first["K1"].decided_by, "consensus")


class InputContractTests(unittest.TestCase):
    """显式解析契约：调用方输入不自洽即 ValueError（而不是静默猜测）。"""

    def test_required_off_roster_is_rejected(self):
        inputs = _team_inputs(required=("alice", "ghost"))
        with self.assertRaises(ValueError):
            evaluate_stage(inputs)

    def test_duplicate_participant_names_are_rejected(self):
        participants = (Participant("alice", "coder"), Participant("alice", "coder"))
        with self.assertRaises(ValueError):
            evaluate_stage(_team_inputs(participants=participants))

    def test_invalid_role_value_is_rejected(self):
        with self.assertRaises(ValueError):
            evaluate_stage(_team_inputs(participants=(
                Participant("alice", "admin"), Participant("bob", "coder"))))

    def test_dual_role_with_role_set_is_rejected(self):
        with self.assertRaises(ValueError):
            evaluate_stage(_team_inputs(participants=(
                Participant("alice", "coder", dual_role=True),
                Participant("bob", "coder"))))

    def test_vote_decision_outside_domain_is_rejected(self):
        with self.assertRaises(ValueError):
            evaluate_stage(_team_inputs(votes={"alice": {"K1": _vote("yes")}}))

    def test_votes_from_off_roster_name_are_rejected(self):
        with self.assertRaises(ValueError):
            evaluate_stage(_team_inputs(votes={"ghost": {"K1": _vote("include")}}))

    def test_unknown_stage_is_rejected(self):
        with self.assertRaises(ValueError):
            evaluate_stage(_team_inputs(stage="stage3"))

    def test_unknown_chain_target_is_rejected(self):
        with self.assertRaises(ValueError):
            evaluate_chain({}, {}, "screening")


class KeyDomainTests(unittest.TestCase):
    """键域与返回形状。"""

    def test_explicit_keys_restrict_evaluation(self):
        inputs = _team_inputs(
            keys=("K1",),
            votes={"alice": {"K1": _vote("include"), "K2": _vote("include")},
                   "bob": {"K1": _vote("include"), "K2": _vote("include")}})
        self.assertEqual(sorted(evaluate_stage(inputs)), ["K1"])

    def test_default_domain_is_union_of_vote_keys(self):
        inputs = _team_inputs(votes={
            "alice": {"K2": _vote("include")}, "bob": {"K1": _vote("include")}})
        self.assertEqual(sorted(evaluate_stage(inputs)), ["K1", "K2"])


if __name__ == "__main__":
    unittest.main()
