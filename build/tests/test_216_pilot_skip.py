# -*- coding: utf-8 -*-
"""216 跳过工单·训练侧红钉（skip / 沉底 / maybe 退役 / effective 金标准门）。

目标产品候选（本文件唯一针对的实现，candidate2）：
- E:/rf216-scratch/skip-backend-pilot-20261118T151122Z-ol6p/candidate2/pilot_screening.py
  SHA-256 35931cf36c9cd5ddea61aff8ea08a16e80c1ae6f95fd7e82628b137e56d65175
- E:/rf216-scratch/skip-backend-pilot-20261118T151122Z-ol6p/candidate2/pilot_screening_api.py
  SHA-256 702989a358d72ec4b9b98af3d0fedbb8ebc7be83021a1f87e8a6c86cf0c00e1f

主仓基线（coscreen/pilot_screening.py 849b4662… / custom_backend/
pilot_screening_api.py 8f578b96… / 本文件同目录 test_2_1_5_w2_pilot.py
b1981336…）不含跳过接口与 maybe 退役：core 层用例将 AttributeError
（skip_item 不存在）、HTTP 层用例 404（端点未注册）——诚实红，不以
ImportError 冒充行为红证；test_2_1_5 的两处合同驱动钉更新（:517
ATTEMPT_ITEM_FIELDS 加 skip_seq、:710-721 maybe 金标准负控换新合同）在
基线上同为诚实红（maybe 写入旧基线 200 ≠ 新钉 400）。

合同依据（只读引用）：out/maintenance216/SKIP_FEATURE_DESIGN_20261118.md
裁定 #2（跳过不算作答）/ #3（沉底 overlay，重复跳过越跳越后）/ #6（完成门
missing 自然含跳过）/ #9（金标准 maybe 退役、存量 maybe 行=未完成标注）；
OC-DS r1 复审 B1（publish 门切 effective 三处同口径）/ N1（旧库缺
pilot_skips 表只读 GET 降级「无跳过」不 503）/ N3（skip 前值校验
attempt_mismatch，防旧页面迟到跳过落进新尝试）。

夹具自含：不 import custom_backend.main、不动 COBOOKSHELF_* 环境、不依赖
conftest/Safety 夹具；HTTP 层用最小 FastAPI app 直挂
register_pilot_screening_routes（桩 require_user/task_lookup/task_visible），
存储全部位于测试自有 TemporaryDirectory。存量 maybe 行（金标准/作答）经
直连 SQL INSERT 构造——这是对「退役前旧数据」的忠实模拟，不伪造产品输出。

r2 隔离验证修订（2026-11-19，run_216 实证）：KEYS 6→10——产品
SAMPLE_LIMITS=(10, 100) 下限 10，6 会被 create_set 以 size_out_of_range
拒绝（测试编写缺陷，非产品缺陷）；取下限 10 顺带钉住含边界的下沿。用例
语义不变，沉底/seq/完成门断言按 10 条目重推；其余产品常量面自查通过
（reason/note 远低于 _MAX_REASON/_MAX_NOTE、无阈值覆盖、curated 无
seed 依赖、user_id 满足 CHECK(>0)）。

撰写协议（C12）：新文件全部类/方法符号写入前逐项运行 GitNexus CLI
upstream impact（--file build/tests/test_216_pilot_skip.py，均如预期
not found，raw 存箱 impact_raw/r2_newfile_*.raw.txt；r3 修订补
r3_SkipCorePins.raw.txt 时戳存证）。

未运行声明：作者仅静态撰写（ast.parse/compile 自检），未运行任何测试/
SQL/API；执行留给独立 runner 在应用 candidate2 后进行。禁主仓/前端/真实
data/旧证据箱写入。
"""
from __future__ import annotations

import atexit
import shutil
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen import pilot_screening as pilot  # noqa: E402

#: 条目数必须落在产品 SAMPLE_LIMITS=(10, 100) 合法域内（含边界）——
#: 取下限 10（6 会被 create_set 以 size_out_of_range 拒绝，r2 实证）。
KEYS = [f"K{i:02d}" for i in range(1, 11)]


def _legacy_gold_maybe(conn: sqlite3.Connection, set_id: int,
                       key: str) -> None:
    """直插一行存量 maybe 金标准（退役前旧数据的忠实构造）。"""
    conn.execute(
        "INSERT INTO pilot_gold(set_id, zotero_key, decision, reason,"
        " decided_by, decided_at) VALUES(?, ?, 'maybe', '', 1,"
        " '2026-01-01T00:00:00Z')", (int(set_id), key))


def _legacy_answer_maybe(conn: sqlite3.Connection, attempt_id: int,
                         key: str) -> None:
    """直插一行存量 maybe 作答（退役前旧数据的忠实构造）。"""
    conn.execute(
        "INSERT INTO pilot_decisions(attempt_id, zotero_key, decision,"
        " reason, answered_at) VALUES(?, ?, 'maybe', '',"
        " '2026-01-01T00:00:00Z')", (int(attempt_id), key))


class SkipCorePins(unittest.TestCase):
    """core 层（coscreen.pilot_screening）：跳过语义 / 沉底 / 完成门 / 金标准门。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rf216-skip-core-")
        self.addCleanup(self._tmp.cleanup)
        self.store = pilot.store_path(self._tmp.name)
        self.conn = pilot.connect(self.store)
        self.addCleanup(self.conn.close)
        pilot.ensure_schema(self.conn)
        created = pilot.create_set(self.conn, actor_id=1, available_keys=KEYS,
                                   size=len(KEYS), sampling="curated",
                                   curated_keys=KEYS)
        self.set_id = int(created["id"])

    def _ready(self) -> int:
        """全 include 金标准 + publish + 开尝试，返回 attempt id。"""
        for key in KEYS:
            pilot.save_gold(self.conn, self.set_id, zotero_key=key,
                            decision="include", actor_id=1)
        pilot.publish_set(self.conn, self.set_id)
        return int(pilot.start_attempt(self.conn, self.set_id,
                                       user_id=7)["attempt"]["id"])

    def test_skip_sinks_queue_and_repeat_skip_moves_further_back(self):
        attempt_id = self._ready()
        first = pilot.skip_item(self.conn, self.set_id, user_id=7,
                                zotero_key="K03", attempt_id=attempt_id)
        self.assertEqual(first, {"skipped": True, "zotero_key": "K03",
                                 "skip_seq": 1})
        second = pilot.skip_item(self.conn, self.set_id, user_id=7,
                                 zotero_key="K01")
        self.assertEqual(second["skip_seq"], 2)
        # 重复跳过 = 序号更新（越跳越后，裁定 #3）
        again = pilot.skip_item(self.conn, self.set_id, user_id=7,
                                zotero_key="K03")
        self.assertEqual(again["skip_seq"], 3)
        queue = pilot.attempt_queue(self.conn, self.set_id, user_id=7)
        order = [item["zotero_key"] for item in queue["items"]]
        self.assertEqual(order, ["K02", "K04", "K05", "K06", "K07", "K08",
                                 "K09", "K10", "K01", "K03"])
        seqs = {item["zotero_key"]: item["skip_seq"]
                for item in queue["items"]}
        self.assertEqual(seqs, {"K02": None, "K04": None, "K05": None,
                                "K06": None, "K07": None, "K08": None,
                                "K09": None, "K10": None,
                                "K01": 2, "K03": 3})
        # position 保持集合规范序（仅列表顺序分区，不改写条目坐标）。
        positions = {item["zotero_key"]: item["position"]
                     for item in queue["items"]}
        self.assertEqual(positions["K01"], 0)
        self.assertEqual(positions["K03"], 2)
        # 裁定 #2：跳过不算作答。
        self.assertEqual(queue["progress"], {"total": 10, "answered": 0})

    def test_skip_requires_item_unanswered(self):
        self._ready()
        pilot.save_item(self.conn, self.set_id, user_id=7, zotero_key="K02",
                        decision="include")
        with self.assertRaises(pilot.PilotScreeningError) as ctx:
            pilot.skip_item(self.conn, self.set_id, user_id=7,
                            zotero_key="K02")
        self.assertEqual(ctx.exception.code, "item_already_answered")
        queue = pilot.attempt_queue(self.conn, self.set_id, user_id=7)
        k02 = next(item for item in queue["items"]
                   if item["zotero_key"] == "K02")
        self.assertIsNone(k02["skip_seq"], "已答条目不得进入沉底分区")
        self.assertEqual(k02["answer"], "include")

    def test_skip_validation_paths(self):
        # 未开尝试 → attempt_not_started（set 存在先于状态判定）
        with self.assertRaises(pilot.PilotScreeningError) as ctx:
            pilot.skip_item(self.conn, self.set_id, user_id=8,
                            zotero_key="K01")
        self.assertEqual(ctx.exception.code, "attempt_not_started")
        attempt_id = self._ready()
        # 不在集合的键 → item_not_in_set
        with self.assertRaises(pilot.PilotScreeningError) as ctx:
            pilot.skip_item(self.conn, self.set_id, user_id=7,
                            zotero_key="NOPE")
        self.assertEqual(ctx.exception.code, "item_not_in_set")
        # N3：前值校验——过期 attempt_id 的迟到跳过 → attempt_mismatch
        with self.assertRaises(pilot.PilotScreeningError) as ctx:
            pilot.skip_item(self.conn, self.set_id, user_id=7,
                            zotero_key="K01", attempt_id=attempt_id + 999)
        self.assertEqual(ctx.exception.code, "attempt_mismatch")
        ok = pilot.skip_item(self.conn, self.set_id, user_id=7,
                             zotero_key="K01", attempt_id=attempt_id)
        self.assertTrue(ok["skipped"])
        # 交卷后 → attempt_submitted
        for key in KEYS:
            pilot.save_item(self.conn, self.set_id, user_id=7,
                            zotero_key=key, decision="include")
        pilot.submit_attempt(self.conn, self.set_id, user_id=7)
        with self.assertRaises(pilot.PilotScreeningError) as ctx:
            pilot.skip_item(self.conn, self.set_id, user_id=7,
                            zotero_key="K02")
        self.assertEqual(ctx.exception.code, "attempt_submitted")

    def test_maybe_retired_and_submit_gate_counts_skip_and_legacy_maybe(self):
        attempt_id = self._ready()
        # 作答收窄（裁定 #9）：maybe → maybe_retired（负控）
        with self.assertRaises(pilot.PilotScreeningError) as ctx:
            pilot.save_item(self.conn, self.set_id, user_id=7,
                            zotero_key="K02", decision="maybe")
        self.assertEqual(ctx.exception.code, "maybe_retired")
        for key in (k for k in KEYS if k not in ("K01", "K06")):
            pilot.save_item(self.conn, self.set_id, user_id=7,
                            zotero_key=key, decision="include")
        # 存量 maybe 作答（K06）+ 跳过（K01）→ 提交门 missing=2（裁定 #6/#9）
        _legacy_answer_maybe(self.conn, attempt_id, "K06")
        pilot.skip_item(self.conn, self.set_id, user_id=7, zotero_key="K01")
        with self.assertRaises(pilot.PilotScreeningError) as ctx:
            pilot.submit_attempt(self.conn, self.set_id, user_id=7)
        self.assertEqual(ctx.exception.code, "attempt_not_answered")
        self.assertEqual(ctx.exception.params, {"missing": 2})
        # 重答（含覆盖存量 maybe 行）后交卷成功
        pilot.save_item(self.conn, self.set_id, user_id=7, zotero_key="K01",
                        decision="include", attempt_id=attempt_id)
        pilot.save_item(self.conn, self.set_id, user_id=7, zotero_key="K06",
                        decision="include")
        result = pilot.submit_attempt(self.conn, self.set_id, user_id=7)
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(result["counts_for_clearance"], True)

    def test_gold_maybe_retired_and_publish_gate_is_effective(self):
        # 金标准收窄（裁定 #9）：maybe → maybe_retired（负控，不落库）
        with self.assertRaises(pilot.PilotScreeningError) as ctx:
            pilot.save_gold(self.conn, self.set_id, zotero_key="K01",
                            decision="maybe", actor_id=1)
        self.assertEqual(ctx.exception.code, "maybe_retired")
        self.assertEqual(pilot.get_set(self.conn, self.set_id)["gold_count"], 0)
        for key in KEYS[:5]:
            pilot.save_gold(self.conn, self.set_id, zotero_key=key,
                            decision="include", actor_id=1)
        # 缺 5 条 → gold_incomplete missing=5（原有门的计数语义）
        with self.assertRaises(pilot.PilotScreeningError) as ctx:
            pilot.publish_set(self.conn, self.set_id)
        self.assertEqual(ctx.exception.code, "gold_incomplete")
        self.assertEqual(ctx.exception.params, {"missing": 5})
        # 存量 maybe 行：get_set 与 list_sets 的 effective 口径同步可见
        _legacy_gold_maybe(self.conn, self.set_id, "K06")
        detail = pilot.get_set(self.conn, self.set_id)
        self.assertEqual(detail["gold_count"], 6)
        self.assertEqual(detail["gold_effective_count"], 5)
        self.assertFalse(detail["gold_complete"])
        row = next(r for r in pilot.list_sets(self.conn)
                   if r["id"] == self.set_id)
        self.assertFalse(row["gold_complete"])
        self.assertEqual(row["gold_effective_count"], 5)
        # 其余条目补齐后：raw 已满 10/10，但存量 maybe 使 effective=9 →
        # 仍拦截（B1 锐边：raw 口径会放行）
        for key in KEYS[6:]:
            pilot.save_gold(self.conn, self.set_id, zotero_key=key,
                            decision="include", actor_id=1)
        detail = pilot.get_set(self.conn, self.set_id)
        self.assertEqual(detail["gold_count"], 10)
        self.assertEqual(detail["gold_effective_count"], 9)
        with self.assertRaises(pilot.PilotScreeningError) as ctx:
            pilot.publish_set(self.conn, self.set_id)
        self.assertEqual(ctx.exception.code, "gold_incomplete")
        self.assertEqual(ctx.exception.params, {"missing": 1})
        # 重标覆盖（ON CONFLICT DO UPDATE）→ effective 追平 → publish 放行
        pilot.save_gold(self.conn, self.set_id, zotero_key="K06",
                        decision="include", actor_id=1)
        published = pilot.publish_set(self.conn, self.set_id)
        self.assertTrue(published["gold_complete"])
        self.assertEqual(published["gold_effective_count"], 10)

    def test_attempt_queue_tolerates_store_without_skips_table(self):
        """N1：升级前旧库（有 pilot 表、无 pilot_skips）只读队列降级为无跳过。"""
        self._ready()
        pilot.skip_item(self.conn, self.set_id, user_id=7, zotero_key="K03")
        pilot.save_item(self.conn, self.set_id, user_id=7, zotero_key="K01",
                        decision="include")
        self.conn.close()
        with closing(pilot.connect(self.store)) as legacy:
            legacy.execute("DROP TABLE pilot_skips")
        with closing(pilot.connect(self.store, readonly=True)) as conn:
            queue = pilot.attempt_queue(conn, self.set_id, user_id=7)
        # 不再 OperationalError→503：按「无跳过」呈规范化 position 序
        self.assertEqual([item["zotero_key"] for item in queue["items"]],
                         KEYS)
        self.assertTrue(all(item["skip_seq"] is None
                            for item in queue["items"]))
        self.assertEqual(queue["progress"]["answered"], 1)


class SkipHttpPins(unittest.TestCase):
    """HTTP 层（最小 app 直挂路由）：skip 端点接线 / 错误翻译 / 旧库 GET 不 503。"""

    @classmethod
    def setUpClass(cls):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from custom_backend import pilot_screening_api as pilot_api

        cls._task_dirs = {}

        def task_lookup(task_id: str):
            return types.SimpleNamespace(
                dir=cls._task_dirs.setdefault(task_id, _new_task_dir()),
                task_id=task_id)

        app = FastAPI()
        pilot_api.register_pilot_screening_routes(
            app,
            require_user=lambda: {"user_id": 7, "username": "t7"},
            task_lookup=task_lookup,
            task_visible=lambda info, user: True,
            server_mode=lambda: False,
            screener_db=None,
            user_lookup=lambda user_id: (
                {"username": "t7"} if user_id == 7 else None),
        )
        cls.client = TestClient(app)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rf216-skip-http-")
        self.addCleanup(self._tmp.cleanup)
        self.task_id = f"t216-{self._testMethodName}"
        SkipHttpPins._task_dirs[self.task_id] = Path(self._tmp.name)
        self.store = pilot.store_path(self._tmp.name)
        with closing(pilot.connect(self.store)) as conn:
            pilot.ensure_schema(conn)
            created = pilot.create_set(conn, actor_id=1, available_keys=KEYS,
                                       size=len(KEYS), sampling="curated",
                                       curated_keys=KEYS)
            self.set_id = int(created["id"])
            for key in KEYS:
                pilot.save_gold(conn, self.set_id, zotero_key=key,
                                decision="include", actor_id=1)
            pilot.publish_set(conn, self.set_id)

    def _base(self) -> str:
        return f"/api/tasks/{self.task_id}/pilot/sets/{self.set_id}"

    def _start(self) -> int:
        response = self.client.post(f"{self._base()}/attempts")
        self.assertEqual(response.status_code, 200, response.text)
        return int(response.json()["attempt"]["id"])

    def test_skip_endpoint_flow_and_error_translation(self):
        base = self._base()
        # 未开尝试 → 409 attempt_not_started
        cold = self.client.post(f"{base}/my-attempt/skip",
                                json={"zotero_key": "K03"})
        self.assertEqual(cold.status_code, 409, cold.text)
        self.assertEqual(cold.json()["detail"]["error_code"],
                         "attempt_not_started")
        attempt_id = self._start()
        # 正常跳过 → 200，载荷带 skip_seq（裁定 #2/#3）
        skip = self.client.post(f"{base}/my-attempt/skip",
                                json={"zotero_key": "K03",
                                      "attempt_id": attempt_id})
        self.assertEqual(skip.status_code, 200, skip.text)
        self.assertEqual(skip.json(), {"skipped": True, "zotero_key": "K03",
                                       "skip_seq": 1})
        # 队列沉底分区：未跳在前（position 序）、跳过在后（skip_seq）
        queue = self.client.get(f"{base}/my-attempt").json()
        self.assertEqual([item["zotero_key"] for item in queue["items"]],
                         ["K01", "K02", "K04", "K05", "K06", "K07", "K08",
                          "K09", "K10", "K03"])
        self.assertEqual(queue["items"][-1]["skip_seq"], 1)
        self.assertEqual(queue["progress"], {"total": 10, "answered": 0})
        # N3：过期 attempt_id → 409 attempt_mismatch
        stale = self.client.post(f"{base}/my-attempt/skip",
                                 json={"zotero_key": "K04",
                                       "attempt_id": attempt_id + 999})
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["detail"]["error_code"],
                         "attempt_mismatch")
        # 作答收窄：maybe → 400 maybe_retired（message_key 供十语词典）
        retired = self.client.post(f"{base}/my-attempt/item",
                                   json={"zotero_key": "K01",
                                         "decision": "maybe"})
        self.assertEqual(retired.status_code, 400, retired.text)
        self.assertEqual(retired.json()["detail"]["error_code"],
                         "maybe_retired")
        self.assertEqual(retired.json()["detail"]["message_key"],
                         "pilot.maybe_retired")
        # 已答条目再跳 → 409 item_already_answered（新错误码入 _STATUS）
        answered = self.client.post(f"{base}/my-attempt/item",
                                    json={"zotero_key": "K01",
                                          "decision": "include"})
        self.assertEqual(answered.status_code, 200, answered.text)
        blocked = self.client.post(f"{base}/my-attempt/skip",
                                   json={"zotero_key": "K01"})
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(blocked.json()["detail"]["error_code"],
                         "item_already_answered")
        self.assertEqual(blocked.json()["detail"]["message_key"],
                         "pilot.item_already_answered")
        # 完成门：跳过条目计入 missing（裁定 #6）——10 条目仅 K01 已答，
        # 其余 9（含跳过的 K03）全部计入。
        submit = self.client.post(f"{base}/my-attempt/submit")
        self.assertEqual(submit.status_code, 409, submit.text)
        self.assertEqual(submit.json()["detail"]["error_code"],
                         "attempt_not_answered")
        self.assertEqual(submit.json()["detail"]["params"]["missing"], 9)

    def test_reskip_moves_further_back_then_gate_requires_clearing(self):
        base = self._base()
        self._start()
        first = self.client.post(f"{base}/my-attempt/skip",
                                 json={"zotero_key": "K03"}).json()
        second = self.client.post(f"{base}/my-attempt/skip",
                                  json={"zotero_key": "K03"}).json()
        self.assertEqual((first["skip_seq"], second["skip_seq"]), (1, 2))
        self.client.post(f"{base}/my-attempt/skip", json={"zotero_key": "K01"})
        queue = self.client.get(f"{base}/my-attempt").json()
        self.assertEqual([item["zotero_key"] for item in queue["items"]],
                         ["K02", "K04", "K05", "K06", "K07", "K08", "K09",
                          "K10", "K03", "K01"])
        # 除跳过条目外全部作答 → 提交仍被 missing=2 挡（#6）
        for key in (k for k in KEYS if k not in ("K01", "K03")):
            self.client.post(f"{base}/my-attempt/item",
                             json={"zotero_key": key, "decision": "include"})
        blocked = self.client.post(f"{base}/my-attempt/submit")
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(blocked.json()["detail"]["params"]["missing"], 2)
        # 清零（补答两个跳过条目）→ 提交成功
        for key in ("K03", "K01"):
            self.client.post(f"{base}/my-attempt/item",
                             json={"zotero_key": key, "decision": "include"})
        done = self.client.post(f"{base}/my-attempt/submit")
        self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual(done.json()["result"]["verdict"], "pass")

    def test_my_attempt_on_store_without_skips_table_is_not_503(self):
        """N1：旧库只读 GET my-attempt 降级「无跳过」，绝不由缺表变 503。"""
        base = self._base()
        self._start()
        self.assertEqual(self.client.post(
            f"{base}/my-attempt/skip",
            json={"zotero_key": "K03"}).status_code, 200)
        with closing(pilot.connect(self.store)) as conn:
            conn.execute("DROP TABLE pilot_skips")
        response = self.client.get(f"{base}/my-attempt")
        self.assertEqual(response.status_code, 200, response.text)
        queue = response.json()
        self.assertEqual([item["zotero_key"] for item in queue["items"]],
                         KEYS)
        self.assertTrue(all(item["skip_seq"] is None
                            for item in queue["items"]))


def _new_task_dir() -> Path:
    directory = tempfile.mkdtemp(prefix="rf216-skip-taskdir-")
    atexit.register(shutil.rmtree, directory, True)
    return Path(directory)


if __name__ == "__main__":
    unittest.main(verbosity=2)
