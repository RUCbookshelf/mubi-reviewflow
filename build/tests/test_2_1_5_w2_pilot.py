# -*- coding: utf-8 -*-
"""2.1.5 G2/W2 红钉：筛选训练（Pilot Screening）。

权威设计：``out/release_plan/drafts/design_2.1.5_pilot_screening.md`` v1.0
（语义 S1–S12 全部生效）。本文件按设计 §八 红钉清单成文，先于实现写成：

1. 计算：κ/PABAK 数值锚全套（po=0.978 低纳入率经典例 κ≈0.47/PABAK≈0.96、
   κ=1.0、κ=0 对称不一致、0.61/0.70/0.75 恰好压线的三档边界各一例）；
   阈值随结果快照存档（S8）。
2. 盲纪律：my-attempt 载荷零金标准（逐字段断言）；未提交 my-result 409；
   publish 后 gold 写入 409；成员间互不可见他人在途尝试。
3. 状态机：draft 金标准可改 → publish 只读 → archive 不可新尝试（恢复在途）、
   多尝试隔离。
4. 零污染：全流程前后正式 decisions / stage2_decisions / coding_values /
   轨迹表逐字节不变（审计钉）；pilot_* 只落协作库。
5. enforce：默认不拦；开启后未达标 403、达标放行、临界且留痕放行、
   负责人豁免、pilot 关闭/无已发布集时优雅放行；与 W1 maybe 闸同入口共存。
6. 权限：金标准与建集仅负责人；成员只见自己；随机抽样 seed 可复现。
7. 方法学报告段：数字全部来自快照，事后改库不重算。

运行：
    python -m pytest build/tests/test_2_1_5_w2_pilot.py -q
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# 与既有服务端夹具同法：先设环境再导入 main。
fixture = tempfile.TemporaryDirectory(prefix="rf_2_1_5_w2_pilot_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient  # noqa: E402

from coscreen import db as db_mod  # noqa: E402
from coscreen import pilot_screening as pilot  # noqa: E402
from coscreen import research_trace  # noqa: E402
from coscreen import review_workflow as store  # noqa: E402
from coscreen import tasks as tasks_mod  # noqa: E402
from coscreen.models import Article  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend import pilot_screening_api  # noqa: E402
from custom_backend.main import DATA_DIR, app  # noqa: E402

USERS = ("w2owner@example.test", "w2member@example.test",
         "w2member2@example.test", "w2outsider@example.test")

#: 设计 §五 的数值锚：低纳入率经典例（1000 条，双方各纳入 21 条，
#: 双方一致纳入 10、一致排除 968，各 11 条单侧纳入）。
ANCHOR_MATRIX = {"a": 10, "b": 11, "c": 11, "d": 968}


def _binary_counts(a: int, b: int, c: int, d: int):
    """把 2x2 计数展开成 (gold, trainee) 两份 decision 映射。"""
    gold, trainee = {}, {}
    keys = [f"K{i:04d}" for i in range(a + b + c + d)]
    index = 0

    def take(count, left, right):
        nonlocal index
        for _ in range(count):
            key = keys[index]
            index += 1
            gold[key] = left
            trainee[key] = right

    take(a, "include", "include")
    take(b, "include", "exclude")
    take(c, "exclude", "include")
    take(d, "exclude", "exclude")
    return gold, trainee


class PilotMathPins(unittest.TestCase):
    """设计 §五 计算规范 + §五 数值锚（纯函数，不经 HTTP）。"""

    def test_low_prevalence_anchor_kappa_low_pabak_high(self):
        gold, trainee = _binary_counts(**ANCHOR_MATRIX)
        result = pilot.evaluate(gold, trainee)
        metrics = result["metrics"]
        self.assertEqual(result["n_items"], 1000)
        self.assertAlmostEqual(metrics["agreement"], 0.978, places=9)
        # 既有 coding_agreement 实现口径：κ≈0.465（设计写"≈0.47"）。
        self.assertAlmostEqual(metrics["kappa_binary"], 0.4649545211, places=8)
        self.assertAlmostEqual(metrics["pabak"], 0.956, places=9)
        self.assertEqual(metrics["pabak"], 2 * metrics["agreement_binary"] - 1)
        # 低纳入率下 κ 被压低、PABAK 不：两判据必须并列可辨。
        self.assertLess(metrics["kappa_binary"], 0.61)
        self.assertGreater(metrics["pabak"], 0.9)
        # κ 低于底线 → 未达标（哪怕 PABAK 很高）。
        self.assertEqual(result["verdict"], "fail")

    def test_perfect_agreement_is_kappa_one(self):
        gold, trainee = _binary_counts(6, 0, 0, 4)
        result = pilot.evaluate(gold, trainee)
        self.assertAlmostEqual(result["metrics"]["kappa_binary"], 1.0, places=12)
        self.assertAlmostEqual(result["metrics"]["agreement"], 1.0, places=12)
        self.assertAlmostEqual(result["metrics"]["pabak"], 1.0, places=12)
        self.assertEqual(result["verdict"], "pass")

    def test_symmetric_disagreement_is_kappa_zero(self):
        gold, trainee = _binary_counts(2, 2, 2, 2)
        result = pilot.evaluate(gold, trainee)
        self.assertAlmostEqual(result["metrics"]["kappa_binary"], 0.0, places=12)
        self.assertAlmostEqual(result["metrics"]["agreement"], 0.5, places=12)
        self.assertAlmostEqual(result["metrics"]["pabak"], 0.0, places=12)
        self.assertEqual(result["verdict"], "fail")

    def test_three_tier_boundaries_exact(self):
        # κ 恰好 0.61（min_kappa 压线）→ 临界，不是未达标。
        gold, trainee = _binary_counts(18, 2, 10, 35)
        result = pilot.evaluate(gold, trainee)
        self.assertAlmostEqual(result["metrics"]["kappa_binary"], 0.61, places=12)
        self.assertEqual(result["verdict"], "borderline")
        # 同一形态把一致度再降一点 → 未达标。
        gold, trainee = _binary_counts(18, 3, 10, 34)
        result = pilot.evaluate(gold, trainee)
        self.assertLess(result["metrics"]["kappa_binary"], 0.61)
        self.assertEqual(result["verdict"], "fail")
        # κ 恰好 0.70（target_kappa 压线）→ 达标。
        gold, trainee = _binary_counts(4, 1, 1, 9)
        result = pilot.evaluate(gold, trainee)
        self.assertAlmostEqual(result["metrics"]["kappa_binary"], 0.70, places=12)
        self.assertGreaterEqual(result["metrics"]["agreement"], 0.75)
        self.assertEqual(result["verdict"], "pass")
        # 0.61 < κ < 0.70 → 临界（讨论后进入的唯一通道）。
        gold, trainee = _binary_counts(6, 0, 2, 4)
        result = pilot.evaluate(gold, trainee)
        self.assertTrue(0.61 < result["metrics"]["kappa_binary"] < 0.70,
                        result["metrics"])
        self.assertEqual(result["verdict"], "borderline")

    def test_agreement_floor_boundary_exact(self):
        # 一致率恰好 0.75：二分类完美（maybe 归非 include），原始三分类
        # 5 条 maybe/exclude 分歧 → 一致率 15/20 = 0.75 压线 → 达标。
        gold = {}
        trainee = {}
        for i in range(10):
            gold[f"A{i}"] = "include"
            trainee[f"A{i}"] = "include"
        for i in range(5):
            gold[f"E{i}"] = "exclude"
            trainee[f"E{i}"] = "exclude"
        for i in range(5):
            gold[f"M{i}"] = "exclude"
            trainee[f"M{i}"] = "maybe"
        result = pilot.evaluate(gold, trainee)
        self.assertAlmostEqual(result["metrics"]["agreement"], 0.75, places=12)
        self.assertAlmostEqual(result["metrics"]["kappa_binary"], 1.0, places=12)
        self.assertEqual(result["metrics"]["maybe_trainee_count"], 5)
        self.assertEqual(result["metrics"]["maybe_gold_count"], 0)
        self.assertEqual(result["verdict"], "pass")
        # 再多 1 条不一致 → 一致率 0.70 < floor：S7 口径下"未达标"只由
        # κ<min 决定，因此这里降档为「临界」（不得判达标，需讨论后进入）。
        trainee["E0"] = "include"
        result = pilot.evaluate(gold, trainee)
        self.assertAlmostEqual(result["metrics"]["agreement"], 0.70, places=12)
        self.assertGreaterEqual(result["metrics"]["kappa_binary"], 0.70)
        self.assertEqual(result["verdict"], "borderline")

    def test_classify_verdict_inclusive_edges(self):
        thresholds = dict(pilot.DEFAULT_THRESHOLDS)
        self.assertEqual(pilot.classify_verdict(0.61, 0.75, thresholds), "borderline")
        self.assertEqual(pilot.classify_verdict(0.6099999, 0.9, thresholds), "fail")
        self.assertEqual(pilot.classify_verdict(0.70, 0.75, thresholds), "pass")
        # κ 压线但一致率低于 floor → 不判达标；S7"未达标=低于 min"，故临界。
        self.assertEqual(pilot.classify_verdict(0.70, 0.7499999, thresholds),
                         "borderline")
        # 一致率压线且 κ 压线 → 达标（双双含等号）。
        self.assertEqual(pilot.classify_verdict(0.70, 0.75, thresholds), "pass")

    def test_maybe_maps_to_non_include_and_raw_kappa_is_reported(self):
        gold, trainee = {}, {}
        for i in range(8):
            gold[f"I{i}"] = "include"
            trainee[f"I{i}"] = "include"
        for i in range(8):
            gold[f"X{i}"] = "exclude"
            trainee[f"X{i}"] = "maybe"
        for i in range(4):
            gold[f"B{i}"] = "maybe"
            trainee[f"B{i}"] = "maybe"
        result = pilot.evaluate(gold, trainee)
        metrics = result["metrics"]
        # 二分类（maybe→非 include，保守）：完全一致。
        self.assertEqual(metrics["kappa_binary"], 1.0)
        # 三分类原始 κ 明显更低（maybe 与 exclude 视为不同）。
        self.assertLess(metrics["kappa_raw"], metrics["kappa_binary"])
        self.assertEqual(metrics["maybe_gold_count"], 4)
        self.assertEqual(metrics["maybe_trainee_count"], 12)
        self.assertAlmostEqual(metrics["agreement"], 12 / 20, places=12)

    def test_items_detail_and_borderline_flags(self):
        result = pilot.evaluate(
            {"K1": {"decision": "include", "reason": "随机对照"},
             "K2": {"decision": "exclude", "reason": "动物实验"},
             "K3": {"decision": "maybe", "reason": "边界"}},
            {"K1": {"decision": "include", "reason": "同意"},
             "K2": {"decision": "maybe", "reason": "拿不准"},
             "K3": {"decision": "maybe", "reason": "边界"}},
        )
        items = {row["zotero_key"]: row for row in result["items"]}
        self.assertEqual(items["K1"]["disagreement"], False)
        self.assertEqual(items["K1"]["borderline"], False)
        self.assertEqual(items["K1"]["gold_reason"], "随机对照")
        self.assertEqual(items["K1"]["trainee_reason"], "同意")
        # 分歧 → borderline
        self.assertEqual(items["K2"]["disagreement"], True)
        self.assertEqual(items["K2"]["borderline"], True)
        # 任一方 maybe（即便双方一致）→ borderline
        self.assertEqual(items["K3"]["disagreement"], False)
        self.assertEqual(items["K3"]["borderline"], True)

    def test_threshold_validation(self):
        with self.assertRaises(pilot.PilotScreeningError) as ctx:
            pilot.normalize_thresholds({"min_kappa": 0.8, "target_kappa": 0.7,
                                        "agreement_floor": 0.75})
        self.assertEqual(ctx.exception.code, "threshold_invalid")
        with self.assertRaises(pilot.PilotScreeningError) as ctx:
            pilot.normalize_thresholds({"agreement_floor": 1.2})
        self.assertEqual(ctx.exception.code, "threshold_invalid")
        with self.assertRaises(pilot.PilotScreeningError) as ctx:
            pilot.normalize_thresholds({"min_kappa": "x"})
        self.assertEqual(ctx.exception.code, "threshold_invalid")
        self.assertEqual(pilot.normalize_thresholds(None), dict(pilot.DEFAULT_THRESHOLDS))

    def test_sampling_reproducible_and_curated(self):
        keys = [f"K{i:03d}" for i in range(60)]
        first = pilot.sample_keys(keys, size=20, sampling="random", seed=2026)
        second = pilot.sample_keys(list(reversed(keys)), size=20,
                                   sampling="random", seed=2026)
        self.assertEqual(first, second, "同 seed 必须可复现（与入参顺序无关）")
        other = pilot.sample_keys(keys, size=20, sampling="random", seed=2027)
        self.assertNotEqual(first, other)
        curated = pilot.sample_keys(keys, size=10, sampling="curated",
                                    curated_keys=keys[:10])
        self.assertEqual(curated, keys[:10])
        mixed = pilot.sample_keys(keys, size=20, sampling="mixed", seed=3,
                                  curated_keys=keys[:5])
        self.assertEqual(sorted(mixed[:5]), sorted(keys[:5]))
        self.assertEqual(len(mixed), 20)
        self.assertEqual(len(set(mixed)), 20)


class PilotApiBase(unittest.TestCase):
    """HTTP 夹具（server 模式 + 真实协作库）。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.users = []
        for name in USERS:
            try:
                user = auth_mod.create_user(name, "W2-Pilot-Test-2026")
            except auth_mod.AuthError:
                user = auth_mod.get_user(name)
            cls.users.append({
                "user_id": user["id"],
                "username": user["username"],
                "token": auth_mod.create_access_token(user["id"], user["username"]),
            })

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    def auth(self, index: int = 0) -> dict:
        return {"Authorization": "Bearer " + self.users[index]["token"]}

    def new_task(self, invite=1) -> None:
        response = self.client.post(
            "/api/tasks", json={"name": self._testMethodName}, headers=self.auth())
        self.assertEqual(response.status_code, 201, response.text)
        self.task = response.json()["task_id"]
        self.info = tasks_mod.find_task(self.task, DATA_DIR)
        self.owner_db = self.info.dir / (
            tasks_mod.slugify_screener(self.users[0]["username"]) + ".db")
        self.collab_db = self.info.dir / "collaboration.db"
        self.seed_articles(24)
        for index in range(1, max(invite, 0) + 1):
            invited = self.client.post(
                f"/api/tasks/{self.task}/collaboration/invite",
                json={"username": self.users[index]["username"], "role": "coder"},
                headers=self.auth())
            self.assertEqual(invited.status_code, 201, invited.text)
        self.member_db = self.info.dir / (
            tasks_mod.slugify_screener(self.users[1]["username"]) + ".db")

    def seed_articles(self, count: int) -> None:
        db_mod.init_db(self.owner_db)
        store.migrate(self.owner_db)
        db_mod.upsert_articles(
            self.owner_db,
            [Article(zotero_key=f"K{i:02d}", title=f"Study {i:02d}",
                     abstract=f"Abstract {i:02d}", journal="J Test",
                     year=2020, doi=f"10.1000/k{i:02d}")
             for i in range(1, count + 1)], [])

    def api(self, path: str, *, index: int = 0):
        return self.client.get(f"/api/tasks/{self.task}{path}",
                               headers=self.auth(index))

    def post(self, path: str, body: dict | None = None, *, index: int = 0):
        return self.client.post(f"/api/tasks/{self.task}{path}", json=body or {},
                                headers=self.auth(index))

    def put(self, path: str, body: dict, *, index: int = 0):
        return self.client.put(f"/api/tasks/{self.task}{path}", json=body,
                               headers=self.auth(index))

    # ---- 组合动作 ----------------------------------------------------
    def enable_pilot(self, *, enforce: bool = False) -> None:
        response = self.put("/pilot/settings",
                            {"pilot_enabled": True, "enforce_pass": enforce})
        self.assertEqual(response.status_code, 200, response.text)

    def create_set(self, *, sampling="curated", keys=None, size=None,
                   seed=None, thresholds=None) -> int:
        keys = list(keys or [f"K{i:02d}" for i in range(1, 13)])
        body = {"sampling": sampling, "curated_keys": keys,
                "size": size if size is not None else len(keys),
                "thresholds": thresholds or dict(pilot.DEFAULT_THRESHOLDS),
                "note": "pilot"}
        if seed is not None:
            body["seed"] = seed
        response = self.post("/pilot/sets", body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["set"]["id"]

    def gold_map(self, set_id: int) -> dict[str, str]:
        with closing(sqlite3.connect(self.collab_db)) as conn:
            return {row[0]: row[1] for row in conn.execute(
                "SELECT zotero_key, decision FROM pilot_gold WHERE set_id=?"
                " ORDER BY zotero_key", (set_id,))}

    def answer_gold(self, set_id: int, decisions: dict[str, str]) -> None:
        for key, decision in decisions.items():
            response = self.post(f"/pilot/sets/{set_id}/gold",
                                 {"zotero_key": key, "decision": decision,
                                  "reason": f"gold {decision}"})
            self.assertEqual(response.status_code, 200, response.text)

    def publish(self, set_id: int) -> None:
        response = self.post(f"/pilot/sets/{set_id}/publish")
        self.assertEqual(response.status_code, 200, response.text)

    def ready_set(self, decisions: dict[str, str] | None = None) -> int:
        """建集 + 答金标准 + publish，返回 set_id。"""
        keys = sorted(decisions) if decisions else [f"K{i:02d}" for i in range(1, 13)]
        set_id = self.create_set(keys=keys)
        if decisions is None:
            decisions = {key: ("include" if i % 2 else "exclude")
                         for i, key in enumerate(keys)}
        self.answer_gold(set_id, decisions)
        self.publish(set_id)
        return set_id

    def start_attempt(self, set_id: int, *, index: int = 1) -> dict:
        response = self.post(f"/pilot/sets/{set_id}/attempts", {}, index=index)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def answer_items(self, set_id: int, answers: dict[str, str], *,
                    index: int = 1) -> None:
        for key, decision in answers.items():
            response = self.post(f"/pilot/sets/{set_id}/my-attempt/item",
                                 {"zotero_key": key, "decision": decision},
                                 index=index)
            self.assertEqual(response.status_code, 200, response.text)

    def submit(self, set_id: int, *, index: int = 1) -> dict:
        response = self.post(f"/pilot/sets/{set_id}/my-attempt/submit", {},
                             index=index)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["result"]

    def member_copy_gold(self, set_id: int, *, index: int = 1) -> dict:
        """成员完全照抄金标准（完美一致，用于达标放行钉）。"""
        gold = self.gold_map(set_id)
        self.start_attempt(set_id, index=index)
        self.answer_items(set_id, dict(gold), index=index)
        return self.submit(set_id, index=index)


# ---------------------------------------------------------------------------
# 盲纪律（评审红线）
# ---------------------------------------------------------------------------

ATTEMPT_ITEM_FIELDS = {
    "zotero_key", "position", "title", "authors", "journal", "year", "doi",
    "item_type", "abstract", "answer", "reason",
}


def _walk_keys(payload) -> set[str]:
    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            found.add(str(key))
            found |= _walk_keys(value)
    elif isinstance(payload, list):
        for value in payload:
            found |= _walk_keys(value)
    return found


class BlindDisciplinePins(PilotApiBase):
    def setUp(self):
        self.new_task(invite=2)
        self.enable_pilot()
        self.set_id = self.ready_set()

    def test_my_attempt_payload_is_metadata_only(self):
        self.start_attempt(self.set_id)
        response = self.api(f"/pilot/sets/{self.set_id}/my-attempt", index=1)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(set(body), {"attempt", "set", "items", "progress"})
        self.assertEqual(set(body["attempt"]),
                         {"id", "status", "started_at", "submitted_at"})
        self.assertEqual(set(body["set"]),
                         {"id", "status", "size", "sampling", "note", "thresholds",
                          "item_count"})
        for item in body["items"]:
            self.assertEqual(set(item), ATTEMPT_ITEM_FIELDS, item)
            self.assertIsNone(item["answer"])
            self.assertEqual(item["reason"], "")
        # 盲纪律：载荷（含嵌套键名）不得出现金标准/结果字段。阈值键
        # min_kappa/target_kappa 是预登记公示口径（S8），不算泄露。
        keys = _walk_keys(body)
        self.assertFalse([key for key in keys if "gold" in key.lower()], keys)
        self.assertFalse(keys & {"verdict", "kappa_binary", "kappa_raw", "pabak",
                                 "agreement", "agreement_binary", "borderline",
                                 "disagreement", "decision", "snapshot_json"}, keys)
        # 题录元数据齐全（导师建集用的就是这个目录）。
        titles = {item["zotero_key"]: item["title"] for item in body["items"]}
        self.assertTrue(all(titles.values()), titles)
        abstracts = {item["zotero_key"]: item["abstract"] for item in body["items"]}
        self.assertTrue(all(abstracts.values()), abstracts)

    def test_member_set_status_is_not_clobbered_by_attempt_state(self):
        """集生命周期状态与本人尝试状态必须是两个键（评审实证：同名覆盖）。"""
        row = next(r for r in self.api("/pilot/sets", index=1).json()["sets"]
                   if r["id"] == self.set_id)
        self.assertEqual(row["status"], "published")
        self.assertIsNone(row["attempt_status"])
        self.assertIsNone(row["attempt_id"])
        started = self.start_attempt(self.set_id)
        row = next(r for r in self.api("/pilot/sets", index=1).json()["sets"]
                   if r["id"] == self.set_id)
        self.assertEqual(row["status"], "published", row)
        self.assertEqual(row["attempt_status"], "in_progress", row)
        self.assertEqual(row["attempt_id"], started["attempt"]["id"])
        detail = self.api(f"/pilot/sets/{self.set_id}", index=1).json()
        self.assertEqual(detail["set"]["status"], "published")
        self.assertEqual(detail["set"]["attempt_status"], "in_progress")
        # 负责人归档后成员仍看到集已归档（不被尝试状态掩盖）
        self.post(f"/pilot/sets/{self.set_id}/archive")
        row = next(r for r in self.api("/pilot/sets", index=1).json()["sets"]
                   if r["id"] == self.set_id)
        self.assertEqual(row["status"], "archived", row)
        self.assertEqual(row["attempt_status"], "in_progress", row)

    def test_my_result_before_submit_is_409(self):
        self.start_attempt(self.set_id)
        response = self.api(f"/pilot/sets/{self.set_id}/my-result", index=1)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["error_code"],
                         "attempt_not_submitted")
        self.assertEqual(response.json()["detail"]["message_key"],
                         "pilot.attempt_not_submitted")

    def test_gold_write_after_publish_is_409(self):
        before = self.gold_map(self.set_id)["K01"]
        response = self.post(f"/pilot/sets/{self.set_id}/gold",
                             {"zotero_key": "K01",
                              "decision": "include" if before == "exclude" else "exclude"})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["error_code"], "gold_locked")
        self.assertEqual(response.json()["detail"]["message_key"],
                         "pilot.gold_locked")
        self.assertEqual(self.gold_map(self.set_id)["K01"], before,
                         "publish 后金标准只读")

    def test_members_cannot_see_each_others_in_progress_attempts(self):
        first = self.start_attempt(self.set_id, index=1)
        self.answer_items(self.set_id, {"K01": "include"}, index=1)
        # 成员 2 未开始 → 409（只可能是自己的尝试）
        other = self.api(f"/pilot/sets/{self.set_id}/my-attempt", index=2)
        self.assertEqual(other.status_code, 409, other.text)
        self.assertEqual(other.json()["detail"]["error_code"], "attempt_not_started")
        second = self.start_attempt(self.set_id, index=2)
        self.assertNotEqual(first["attempt"]["id"], second["attempt"]["id"])
        # 成员 2 的队列只有自己的作答（这题未答）。
        queue = self.api(f"/pilot/sets/{self.set_id}/my-attempt", index=2).json()
        self.assertTrue(all(item["answer"] is None for item in queue["items"]))
        # 成员看不了负责人看板
        refused = self.api(f"/pilot/sets/{self.set_id}/results", index=1)
        self.assertEqual(refused.status_code, 403, refused.text)
        self.assertEqual(refused.json()["detail"]["error_code"], "owner_required")

    def test_member_sees_published_sets_only(self):
        draft = self.create_set(keys=[f"K{i:02d}" for i in range(1, 11)])
        listing = self.api("/pilot/sets", index=1)
        self.assertEqual(listing.status_code, 200, listing.text)
        body = listing.json()
        self.assertEqual({row["id"] for row in body["sets"]}, {self.set_id})
        self.assertTrue(body["settings"]["pilot_enabled"])
        self.assertFalse(body["can_manage"])
        hidden = self.api(f"/pilot/sets/{draft}", index=1)
        self.assertEqual(hidden.status_code, 409, hidden.text)
        self.assertEqual(hidden.json()["detail"]["error_code"], "not_published")
        # 成员越权：建集/金标准/publish 一律 403
        for path, payload in (
                ("/pilot/sets", {"size": 10, "sampling": "random", "seed": 1}),
                (f"/pilot/sets/{self.set_id}/gold",
                 {"zotero_key": "K01", "decision": "include"}),
                (f"/pilot/sets/{self.set_id}/publish", {}),
                ("/pilot/settings", {"pilot_enabled": False})):
            response = self.post(path, payload, index=1) \
                if not path.endswith("settings") else self.put(path, payload, index=1)
            self.assertEqual(response.status_code, 403, (path, response.text))
            self.assertEqual(response.json()["detail"]["error_code"],
                             "owner_required", path)


# ---------------------------------------------------------------------------
# 状态机 + 多尝试隔离
# ---------------------------------------------------------------------------

class StateMachinePins(PilotApiBase):
    def setUp(self):
        self.new_task(invite=1)
        self.enable_pilot()

    def test_draft_editable_then_publish_locks_and_requires_full_gold(self):
        set_id = self.create_set(keys=[f"K{i:02d}" for i in range(1, 11)])
        gold = {f"K{i:02d}": "include" for i in range(1, 10)}
        self.answer_gold(set_id, gold)
        # 未覆盖全部条目 → publish 409，且状态仍为 draft
        incomplete = self.post(f"/pilot/sets/{set_id}/publish")
        self.assertEqual(incomplete.status_code, 409, incomplete.text)
        self.assertEqual(incomplete.json()["detail"]["error_code"], "gold_incomplete")
        self.assertEqual(incomplete.json()["detail"]["params"]["missing"], 1)
        # draft 期可改金标准
        changed = self.post(f"/pilot/sets/{set_id}/gold",
                            {"zotero_key": "K01", "decision": "maybe",
                             "reason": "边界"})
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(self.gold_map(set_id)["K01"], "maybe")
        self.answer_gold(set_id, {"K10": "exclude"})
        self.publish(set_id)
        detail = self.api(f"/pilot/sets/{set_id}").json()
        self.assertEqual(detail["set"]["status"], "published")
        self.assertTrue(detail["set"]["gold_complete"])
        self.assertEqual(detail["set"]["gold_count"], 10)
        # 二次 publish → 409 set_not_draft
        again = self.post(f"/pilot/sets/{set_id}/publish")
        self.assertEqual(again.status_code, 409, again.text)
        self.assertEqual(again.json()["detail"]["error_code"], "set_not_draft")
        # 成员可开训
        started = self.start_attempt(set_id)
        self.assertEqual(started["attempt"]["status"], "in_progress")

    def test_archive_blocks_new_attempts_but_resumes_in_flight_and_allows_submit(self):
        set_id = self.ready_set()
        attempt = self.start_attempt(set_id)["attempt"]
        self.answer_items(set_id, dict(self.gold_map(set_id)))
        archived = self.post(f"/pilot/sets/{set_id}/archive")
        self.assertEqual(archived.status_code, 200, archived.text)
        self.assertEqual(archived.json()["set"]["status"], "archived")
        # 在途尝试仍可恢复（同一 attempt id），并可交卷
        resumed = self.start_attempt(set_id)
        self.assertEqual(resumed["attempt"]["id"], attempt["id"])
        self.assertTrue(resumed["resumed"])
        result = self.submit(set_id)
        self.assertEqual(result["verdict"], "pass")
        # 交卷后新尝试 → 409 set_archived
        blocked = self.post(f"/pilot/sets/{set_id}/attempts")
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(blocked.json()["detail"]["error_code"], "set_archived")

    def test_multiple_attempts_are_isolated(self):
        set_id = self.ready_set()
        gold = self.gold_map(set_id)
        self.start_attempt(set_id)
        # 第一次：故意全错 → fail
        wrong = {key: ("exclude" if decision == "include" else "include")
                 for key, decision in gold.items()}
        self.answer_items(set_id, wrong)
        first = self.submit(set_id)
        self.assertEqual(first["verdict"], "fail")
        # 第二次：新档案，答案从零开始
        second = self.start_attempt(set_id)
        self.assertNotEqual(second["attempt"]["id"], first["attempt_id"])
        queue = self.api(f"/pilot/sets/{set_id}/my-attempt", index=1).json()
        self.assertTrue(all(item["answer"] is None for item in queue["items"]))
        self.assertEqual(queue["progress"]["answered"], 0)
        # 未交卷的新尝试不覆盖已交卷结果：my-result 仍是第一次的历史归档
        still_first = self.api(f"/pilot/sets/{set_id}/my-result", index=1)
        self.assertEqual(still_first.status_code, 200, still_first.text)
        self.assertEqual(still_first.json()["result"]["attempt_id"],
                         first["attempt_id"])
        # 交卷第二次后切到最新
        self.answer_items(set_id, dict(gold))
        last = self.submit(set_id)
        self.assertEqual(last["verdict"], "pass")
        self.assertEqual(self.api(f"/pilot/sets/{set_id}/my-result",
                                  index=1).json()["result"]["attempt_id"],
                         last["attempt_id"])
        history = self.api("/pilot/my-history", index=1).json()
        self.assertEqual([row["attempt_id"] for row in history["attempts"]],
                         [last["attempt_id"], first["attempt_id"]])
        self.assertTrue(history["cleared"])

    def test_submit_requires_every_item_answered(self):
        set_id = self.ready_set()
        self.start_attempt(set_id)
        self.answer_items(set_id, {"K01": "include"})
        response = self.post(f"/pilot/sets/{set_id}/my-attempt/submit", index=1)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["error_code"],
                         "attempt_not_answered")
        self.assertEqual(response.json()["detail"]["params"]["missing"], 11)
        # 二次交卷 → 409 attempt_submitted
        self.answer_items(set_id, {f"K{i:02d}": "include" for i in range(2, 13)})
        self.submit(set_id)
        again = self.post(f"/pilot/sets/{set_id}/my-attempt/submit", index=1)
        self.assertEqual(again.status_code, 409, again.text)
        self.assertEqual(again.json()["detail"]["error_code"], "attempt_submitted")


# ---------------------------------------------------------------------------
# 零污染审计钉
# ---------------------------------------------------------------------------

FORMAL_TABLES = ("decisions", "stage2_decisions", "coding_dimensions",
                 "coding_values", "trace_events")


def _table_dump(path: Path, table: str):
    """整表逐行快照（表名来自本文件常量，非外部输入）。"""
    if not path.is_file():
        return None
    with closing(sqlite3.connect(path)) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,)).fetchone()
        if exists is None:
            return None
        return [tuple(row) for row in conn.execute(
            "SELECT * FROM " + table, ())]


class ZeroPollutionPins(PilotApiBase):
    def test_full_pilot_flow_never_touches_formal_data(self):
        self.new_task(invite=1)
        # 正式数据基线：负责人先做一条真实初筛决定 + 一条复筛决定。
        self.assertEqual(self.post("/decisions",
                                   {"zotero_key": "K03", "decision": "include"}).status_code,
                         200)
        self.assertEqual(self.post("/stage2/decisions",
                                   {"zotero_key": "K03", "decision": "include"}).status_code,
                         200)
        with closing(sqlite3.connect(self.owner_db)) as conn, conn:
            conn.execute(
                "INSERT INTO coding_dimensions"
                "(name,dtype,options_json,multi_select,unit,description,section,position,created_at)"
                " VALUES('dose','text','[]',0,'','','',1,'2026-01-01T00:00:00Z')")
            conn.execute("INSERT INTO coding_values(dimension_id,zotero_key,value,updated_at)"
                         " VALUES(1,'K03','12 mg','2026-01-01T00:00:00Z')")
            research_trace.ensure_schema(conn)
            conn.execute(
                "INSERT INTO trace_events(event_id, store_id, task_id, actor_id,"
                " stage, action, subject_id, before_json, after_json, source,"
                " recorded_at, schema_version, app_version)"
                " VALUES('e1','store','task','1','screen','screening_decision',"
                "'K03','{}','{}','test','2026-01-01T00:00:00Z','1','2.1.5')")
        self.assertIsNotNone(_table_dump(self.owner_db, "trace_events"),
                             "轨迹表必须存在，审计钉才有意义")
        before_owner = {table: _table_dump(self.owner_db, table)
                        for table in FORMAL_TABLES}
        before_member = {table: _table_dump(self.member_db, table)
                         for table in FORMAL_TABLES}

        self.enable_pilot()
        set_id = self.ready_set()
        result = self.member_copy_gold(set_id)
        self.assertEqual(result["verdict"], "pass")
        self.post(f"/pilot/sets/{set_id}/archive")

        after_owner = {table: _table_dump(self.owner_db, table)
                       for table in FORMAL_TABLES}
        after_member = {table: _table_dump(self.member_db, table)
                        for table in FORMAL_TABLES}
        self.assertEqual(before_owner, after_owner, "初筛/复筛/编码/轨迹必须逐字节不变")
        self.assertEqual(before_member, after_member)
        # 成员正式库仍为空（训练作答不得落正式 decisions）
        self.assertIsNone(_table_dump(self.member_db, "decisions"))
        # pilot_* 只在协作库出现
        with closing(sqlite3.connect(self.collab_db)) as conn:
            collab_tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        for name in ("pilot_sets", "pilot_items", "pilot_gold", "pilot_attempts",
                     "pilot_decisions", "pilot_results", "pilot_task_settings"):
            self.assertIn(name, collab_tables)
        with closing(sqlite3.connect(self.owner_db)) as conn:
            owner_tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertFalse({name for name in owner_tables if name.startswith("pilot")},
                         "pilot 表绝不进正式筛选库")


# ---------------------------------------------------------------------------
# enforce_pass 拦截（与 W1 maybe 闸同入口共存）
# ---------------------------------------------------------------------------

class EnforcePins(PilotApiBase):
    def setUp(self):
        self.new_task(invite=1)

    def test_default_off_changes_nothing(self):
        self.enable_pilot(enforce=False)
        self.ready_set()
        response = self.post("/decisions",
                             {"zotero_key": "K01", "decision": "include"})
        self.assertEqual(response.status_code, 200, response.text)

    def test_enforce_blocks_unqualified_member_but_not_owner(self):
        self.enable_pilot(enforce=True)
        set_id = self.ready_set()
        blocked = self.post("/decisions",
                            {"zotero_key": "K01", "decision": "include"}, index=1)
        self.assertEqual(blocked.status_code, 403, blocked.text)
        detail = blocked.json()["detail"]
        self.assertEqual(detail["error_code"], "enforce_blocked")
        self.assertEqual(detail["message_key"], "pilot.enforce_blocked")
        self.assertEqual(detail["params"]["set_id"], set_id)
        self.assertEqual(self.api("/decisions", index=1).json()["decisions"], [],
                         "被拦截的决定不得落盘")
        # 负责人本人豁免
        owner = self.post("/decisions",
                          {"zotero_key": "K01", "decision": "include"})
        self.assertEqual(owner.status_code, 200, owner.text)

    def test_pass_clears_the_gate(self):
        self.enable_pilot(enforce=True)
        set_id = self.ready_set()
        self.member_copy_gold(set_id)
        response = self.post("/decisions",
                             {"zotero_key": "K02", "decision": "exclude"}, index=1)
        self.assertEqual(response.status_code, 200, response.text)

    def test_borderline_needs_discussion_record_to_enter(self):
        self.enable_pilot(enforce=True)
        # a=6,b=0,c=2,d=4：κ=0.6667（临界），一致率 0.8333 ≥ floor。
        gold = {f"K{i:02d}": ("include" if i <= 6 else "exclude")
                for i in range(1, 13)}
        set_id = self.ready_set(gold)
        trainee = dict(gold)
        trainee["K07"] = "include"
        trainee["K08"] = "include"
        self.start_attempt(set_id)
        self.answer_items(set_id, trainee)
        result = self.submit(set_id)
        self.assertEqual(result["verdict"], "borderline", result["metrics"])
        blocked = self.post("/decisions",
                            {"zotero_key": "K01", "decision": "include"}, index=1)
        self.assertEqual(blocked.status_code, 403, blocked.text)
        # 明确落到"临界"档：先 409 verdict_not_borderline 不适用；留痕后放行。
        entered = self.post(f"/pilot/sets/{set_id}/my-result/enter-by-discussion",
                            {"note": "与导师讨论后统一口径"}, index=1)
        self.assertEqual(entered.status_code, 200, entered.text)
        self.assertTrue(entered.json()["result"]["entered_by_discussion"])
        allowed = self.post("/decisions",
                            {"zotero_key": "K01", "decision": "include"}, index=1)
        self.assertEqual(allowed.status_code, 200, allowed.text)

    def test_pass_verdict_cannot_be_marked_as_discussion_entered(self):
        self.enable_pilot(enforce=True)
        set_id = self.ready_set()
        self.member_copy_gold(set_id)
        response = self.post(f"/pilot/sets/{set_id}/my-result/enter-by-discussion",
                             {"note": "x"}, index=1)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["error_code"],
                         "verdict_not_borderline")

    def test_no_published_set_means_no_block(self):
        self.enable_pilot(enforce=True)
        response = self.post("/decisions",
                             {"zotero_key": "K01", "decision": "include"}, index=1)
        self.assertEqual(response.status_code, 200, response.text)

    def test_both_gates_live_on_the_same_write_entry(self):
        self.enable_pilot(enforce=True)
        self.ready_set()
        maybe = self.post("/decisions",
                          {"zotero_key": "K01", "decision": "maybe"}, index=1)
        self.assertEqual(maybe.status_code, 409, maybe.text)
        self.assertEqual(maybe.json()["detail"]["code"], "maybe_disabled")
        include = self.post("/decisions",
                            {"zotero_key": "K01", "decision": "include"}, index=1)
        self.assertEqual(include.status_code, 403, include.text)
        self.assertEqual(include.json()["detail"]["error_code"], "enforce_blocked")

    def test_enforce_requires_pilot_enabled(self):
        refused = self.put("/pilot/settings",
                           {"pilot_enabled": False, "enforce_pass": True})
        self.assertEqual(refused.status_code, 400, refused.text)
        self.assertEqual(refused.json()["detail"]["error_code"], "pilot_disabled")
        # 关闭 pilot 自动清掉 enforce（不留悬空闸）
        self.enable_pilot(enforce=True)
        off = self.put("/pilot/settings", {"pilot_enabled": False})
        self.assertEqual(off.status_code, 200, off.text)
        self.assertEqual(off.json()["settings"],
                         {"pilot_enabled": False, "enforce_pass": False})
        response = self.post("/decisions",
                             {"zotero_key": "K01", "decision": "include"}, index=1)
        self.assertEqual(response.status_code, 200, response.text)


# ---------------------------------------------------------------------------
# 权限 + 抽样复现 + 方法学段 + 快照
# ---------------------------------------------------------------------------

class OwnerScopePins(PilotApiBase):
    def setUp(self):
        self.new_task(invite=1)

    def test_outsider_is_forbidden(self):
        self.enable_pilot()
        for path in ("/pilot/settings", "/pilot/sets", "/pilot/my-history"):
            response = self.api(path, index=3)
            self.assertEqual(response.status_code, 403, (path, response.text))
            self.assertEqual(response.json()["detail"]["error_code"],
                             "task_forbidden")

    def test_read_endpoints_on_fresh_task_return_defaults(self):
        # 任务协作库已存在但还没有任何 pilot 表（升级前的任务）：读端必须
        # 200 默认值，不得 503，也不得在只读连接上跑 DDL。
        listing = self.api("/pilot/sets")
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertEqual(listing.json()["sets"], [])
        self.assertFalse(listing.json()["settings"]["pilot_enabled"])
        self.assertEqual(self.api("/pilot/my-history").json()["attempts"], [])
        self.assertEqual(self.api("/pilot/sets/999").status_code, 404)

    def test_set_listing_and_detail_include_gold_progress_for_owner(self):
        set_id = self.create_set(keys=[f"K{i:02d}" for i in range(1, 11)])
        self.answer_gold(set_id, {"K01": "include", "K02": "exclude"})
        detail = self.api(f"/pilot/sets/{set_id}").json()
        self.assertEqual(detail["set"]["gold_count"], 2)
        self.assertFalse(detail["set"]["gold_complete"])
        self.assertEqual(len(detail["items"]), 10)
        golds = {row["zotero_key"]: row["gold"] for row in detail["items"]}
        self.assertEqual(golds["K01"]["decision"], "include")
        self.assertIsNone(golds["K03"])
        listing = self.api("/pilot/sets").json()
        self.assertTrue(listing["can_manage"])
        self.assertEqual([row["id"] for row in listing["sets"]], [set_id])

    def test_random_sampling_seed_is_reproducible_through_api(self):
        first = self.create_set(sampling="random", keys=None, size=10, seed=99)
        second = self.create_set(sampling="random", keys=None, size=10, seed=99)
        third = self.create_set(sampling="random", keys=None, size=10, seed=100)
        with closing(sqlite3.connect(self.collab_db)) as conn:
            def items(set_id):
                return [row[0] for row in conn.execute(
                    "SELECT zotero_key FROM pilot_items WHERE set_id=? ORDER BY position",
                    (set_id,))]
            self.assertEqual(items(first), items(second))
            self.assertNotEqual(items(first), items(third))
            self.assertEqual(len(items(first)), 10)
            # seed 与抽样方式随集登记（方法学口径）
            row = conn.execute("SELECT sampling, seed FROM pilot_sets WHERE id=?",
                               (first,)).fetchone()
            self.assertEqual(row[0], "random")
            self.assertEqual(row[1], 99)

    def test_random_seed_is_generated_and_reproducible_when_omitted(self):
        # 留空 seed：后端必须生成整数并落库（否则"同 seed 可复现"无据可查）。
        first = self.post("/pilot/sets", {"size": 10, "sampling": "random"}).json()["set"]
        self.assertIsInstance(first["seed"], int, first)
        second = self.post("/pilot/sets", {"size": 10, "sampling": "random",
                                           "seed": first["seed"]}).json()["set"]
        with closing(sqlite3.connect(self.collab_db)) as conn:
            def items(set_id):
                return [row[0] for row in conn.execute(
                    "SELECT zotero_key FROM pilot_items WHERE set_id=?"
                    " ORDER BY position", (set_id,))]
            self.assertEqual(items(first["id"]), items(second["id"]))
            stored = conn.execute("SELECT seed FROM pilot_sets WHERE id=?",
                                  (first["id"],)).fetchone()[0]
            self.assertEqual(stored, first["seed"])
            # curated 不登记 seed
            curated = self.post("/pilot/sets", {
                "sampling": "curated", "size": 10,
                "curated_keys": [f"K{i:02d}" for i in range(1, 11)]}).json()["set"]
            self.assertIsNone(curated["seed"])

    def test_invalid_sampling_parameters_are_structured_400(self):
        cases = [
            ({"size": 5, "sampling": "random", "seed": 1}, "size_out_of_range"),
            ({"size": 10, "sampling": "bogus"}, "sampling_invalid"),
            ({"size": 10, "sampling": "random", "curated_keys": ["NOPE1"]},
             "curated_key_unknown"),
        ]
        for body, code in cases:
            response = self.post("/pilot/sets", body)
            self.assertEqual(response.status_code, 400, (body, response.text))
            self.assertEqual(response.json()["detail"]["error_code"], code)
            self.assertEqual(response.json()["detail"]["message_key"],
                             "pilot." + code)


class SnapshotPins(PilotApiBase):
    def setUp(self):
        self.new_task(invite=1)
        self.enable_pilot()
        self.set_id = self.ready_set()

    def test_result_snapshot_archives_thresholds_and_is_not_recomputed(self):
        gold = self.gold_map(self.set_id)
        self.start_attempt(self.set_id)
        self.answer_items(self.set_id, dict(gold))
        result = self.submit(self.set_id)
        self.assertEqual(result["thresholds"], dict(pilot.DEFAULT_THRESHOLDS))
        first_metrics = dict(result["metrics"])
        with closing(sqlite3.connect(self.collab_db)) as conn:
            snapshot_json, verdict = conn.execute(
                "SELECT snapshot_json, verdict FROM pilot_results WHERE attempt_id=?",
                (result["attempt_id"],)).fetchone()
        snapshot = json.loads(snapshot_json)
        self.assertEqual(snapshot["thresholds"], dict(pilot.DEFAULT_THRESHOLDS))
        self.assertEqual(snapshot["metrics"]["kappa_binary"], first_metrics["kappa_binary"])
        self.assertEqual(verdict, "pass")
        self.assertEqual(snapshot["verdict"], "pass")
        # S8：事后篡改金标准，结果与报告段不得重算。
        with closing(sqlite3.connect(self.collab_db)) as conn, conn:
            conn.execute("UPDATE pilot_gold SET decision='exclude' WHERE set_id=?",
                         (self.set_id,))
        again = self.api(f"/pilot/sets/{self.set_id}/my-result", index=1).json()
        self.assertEqual(again["result"]["metrics"], first_metrics)
        self.assertEqual(again["result"]["verdict"], "pass")

    def test_methodology_text_uses_snapshot_numbers(self):
        gold = self.gold_map(self.set_id)
        self.start_attempt(self.set_id)
        self.answer_items(self.set_id, dict(gold))
        result = self.submit(self.set_id)
        text_zh = result["methodology"]["zh"]
        text_en = result["methodology"]["en"]
        metrics = result["metrics"]
        self.assertIn("12", text_zh)
        self.assertIn("0.70", text_zh)
        self.assertIn("0.61", text_zh)
        self.assertIn("0.75", text_zh)
        self.assertIn(f"{metrics['kappa_binary']:.2f}", text_zh)
        self.assertIn(f"{metrics['agreement']:.2f}", text_zh)
        self.assertIn(f"{metrics['pabak']:.2f}", text_zh)
        self.assertIn("kappa", text_en.lower())
        self.assertIn("PABAK", text_en)
        self.assertIn("0.70", text_en)
        # 快照口径：数值来自 pilot_results.snapshot_json，不来自当前库表。
        with closing(sqlite3.connect(self.collab_db)) as conn:
            snapshot = json.loads(conn.execute(
                "SELECT snapshot_json FROM pilot_results WHERE attempt_id=?",
                (result["attempt_id"],)).fetchone()[0])
        self.assertEqual(text_zh, pilot.methodology_text(snapshot, lang="zh"))
        self.assertEqual(text_en, pilot.methodology_text(snapshot, lang="en"))

    def test_results_board_lists_members_latest_and_history(self):
        gold = self.gold_map(self.set_id)
        self.member_copy_gold(self.set_id)
        board = self.api(f"/pilot/sets/{self.set_id}/results").json()
        self.assertEqual(board["summary"]["submitted"], 1)
        self.assertEqual(board["summary"]["passed"], 1)
        member = board["members"][0]
        self.assertEqual(member["username"], self.users[1]["username"])
        self.assertEqual(len(member["attempts"]), 1)
        self.assertEqual(member["latest"]["verdict"], "pass")
        self.assertEqual(member["latest"]["kappa_binary"], 1.0)
        self.assertEqual(member["latest"]["thresholds"], dict(pilot.DEFAULT_THRESHOLDS))
        # 看板不含成员逐条作答（盲纪律只对成员；看板给结论不给原答案）
        self.assertNotIn("items", member["latest"])
        self.assertEqual(gold, self.gold_map(self.set_id))


class LegacyEnforcePins(unittest.TestCase):
    """legacy 任务（无属主记录）不得被自己的 enforce 闸拦住（评审 §5）。"""

    def test_task_without_owner_is_not_self_blocked(self):
        tmp = tempfile.TemporaryDirectory(prefix="rf_w2_legacy_")
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "collaboration.db"
        conn = pilot.connect(path)
        pilot.ensure_schema(conn)
        pilot.set_settings(conn, user_id=1, pilot_enabled=True, enforce_pass=True)
        keys = [f"K{i:02d}" for i in range(1, 11)]
        created = pilot.create_set(conn, actor_id=1, available_keys=keys,
                                   size=10, sampling="curated", curated_keys=keys)
        for key in keys:
            pilot.save_gold(conn, created["id"], zotero_key=key,
                            decision="include", actor_id=1)
        pilot.publish_set(conn, created["id"])
        # 先证明闸本身有效：给一个"非本人"属主 → 拦截。
        blocked, set_id = pilot.enforce_blocked(conn, user_id=2, owner_user_id=99)
        self.assertTrue(blocked)
        self.assertEqual(set_id, created["id"])
        conn.close()
        info = type("LegacyInfo", (), {"dir": Path(tmp.name),
                                       "task_id": "legacy-task"})()
        # 无属主记录（legacy）：整条闸不生效，绝不拦到开启者自己。
        self.assertIsNone(pilot_screening_api.enforce_gate(info, {"user_id": 2}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
