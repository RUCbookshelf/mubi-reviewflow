# -*- coding: utf-8 -*-
"""216 跳过工单·正式筛选侧红钉（skip 覆盖层 / 沉底 / 决策隔离 / 撤销回待筛）。

目标产品候选（本文件唯一针对的实现，skip-backend-formal 箱 candidate/）：
- E:/rf216-scratch/skip-backend-formal-20261118T151141Z-3511/candidate/review_workflow.py
  SHA-256 d246553cfc0e6a2f92143f58f7ed4e004087a083e3b1fed2f352698cc94aaae7
- E:/rf216-scratch/skip-backend-formal-20261118T151141Z-3511/candidate/review_workflow_api.py
  SHA-256 986c714bf64f669dc9fc86ba7f76dd5c13f3160dda6189cb2d9c77df2fb41ed8

主仓基线（coscreen/review_workflow.py 905a139c486db3c… /
custom_backend/review_workflow_api.py e72fe1699662f8…，均经 SHA 复核）
不含跳过接口：core 层用例将 AttributeError（skip_queue_item /
SkipDecisionExistsError 不存在），HTTP 层用例 404（端点未注册）、
queue 载荷无 skipped_pending 键（KeyError）——诚实红，不以 ImportError
冒充行为红证（模块本身两版均可导入）。

合同依据（只读引用）：out/maintenance216/SKIP_FEATURE_DESIGN_20261118.md
裁定 #2（跳过不写任何决策记录）/ #3（沉底 overlay，重复跳过越跳越后）/
#4（跳过层与主序机制完全隔离，合成仅发生在读取时）/ #5（刷新/重排只作
用于主序，跳过状态是文献级属性不随队列重置清除）/ #7（跳过零参与
训练：留痕独立、无算法消费方——本文件以「不写决策表」钉其后端数据
面前提）/ #8（撤销决策回待筛后旧跳过记录重新生效）；#10（键盘快捷键）
为前端实施项，后端测试域外。#6 完成门的后端职责=计数正确
（pending 天然含跳过）+skipped_pending 下发，由 HTTP 层两用例钉。

夹具自含：不 import custom_backend.main、不动任何环境变量；存储全部
位于测试自有 TemporaryDirectory/mkdtemp。core 层直测
store.skip_queue_item 与 store.random_order 的交互（主序隔离）；HTTP
层用最小 FastAPI app 直挂 register_review_workflow_routes（桩
require_user/task_lookup/screener_db/visible 四个注入点回调，task_lookup
返回真实 tasks.TaskInfo——legacy 任务：任务目录无协作库 → get_owner
None → write_guard 跳过团队门），库经产品入口 coscreen.db.init_db 建
全量筛选 schema + store.migrate + binary_reset.migrate 补齐版本（夹具
只写 include/exclude 决策，binary_reset 对无 maybe 行的库零删除）。
articles/decisions/stage2_decisions 行经直连 SQL INSERT/DELETE 构造
——对「已决策/撤销后」状态的忠实模拟，不伪造产品输出；跳过断言全部
直读产品写出的 workflow_skip_order 行。零 mock（无 unittest.mock）。

撰写协议（C12）：新文件全部类/方法/辅助函数符号写入前逐项运行
GitNexus CLI upstream impact（--file build/tests/test_216_skip_formal.py，
22 个新符号〔唯一名 21，setUp 为两类同名方法〕均如预期 not found；
raw 存箱 impact_raw/newfile_*.raw.txt（另 probe_*.raw.txt 为首条探针）。
产品侧 skip_queue_item/skip_order/SkipDecisionExistsError 在主仓索引中
不存在=候选纯增量零上游调用者；register_review_workflow_routes 上游
LOW（custom_backend/main.py 直调）；queue(Method) 上游 LOW 2 直调——
无 HIGH/CRITICAL 警示项，详见箱内报告）。

未运行声明：作者仅静态撰写（ast.parse/compile 自检），未运行任何测试/
SQL/API；执行留给独立 runner 在应用 candidate 后进行。禁主仓/前端/真实
data/旧证据箱写入。
"""
from __future__ import annotations

import atexit
import itertools
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen import binary_reset, review_workflow as store  # noqa: E402
from coscreen import db as screening_db  # noqa: E402
from coscreen import tasks  # noqa: E402

#: 正式筛选队列无训练侧 SAMPLE_LIMITS 之类的尺寸约束；取 10 键使主序分区
#: （前 8 未跳 + 后 2 跳过）与 pending 分解在断言中可逐位读出。
KEYS = [f"K{i:02d}" for i in range(1, 11)]

ACTOR = "7"          # 与 API 层 str(user["user_id"]) 同口径
TASK = "t216-core"
#: 与 API 层 queue() 随机序调用同形状的 scope 键（裁定 #4：互不引用）。
SCOPE = json.dumps({"stage": "screen", "source_id": None}, sort_keys=True)


def _sinking_order(main_order: list[str], skips: dict[str, int]) -> list[str]:
    """裁定 #3 的测试侧合同陈述：未跳键按主序在前，跳过键按 skip_seq 沉底。"""
    return ([key for key in main_order if key not in skips]
            + [key for _, key in sorted((seq, key) for key, seq in skips.items())])


def _insert_articles(conn, keys) -> None:
    """直插筛选库条目（产品 articles schema 的忠实夹具构造）。"""
    conn.executemany(
        "INSERT INTO articles(zotero_key,item_type,title,year,import_order) "
        "VALUES(?,'journalArticle',?,2026,?)",
        [(key, f"Title {key}", index) for index, key in enumerate(keys)])


def _decide(conn, table: str, key: str, decision: str) -> None:
    """直插一行决策（真实 schema；「已决策」状态的忠实构造，非产品输出）。"""
    if table not in ("decisions", "stage2_decisions"):
        raise ValueError("Fixed decision table enum only.")
    conn.execute(
        f"INSERT INTO {table}(zotero_key,decision,screened_at,updated_at) "
        "VALUES(?,?,'2026-01-01T00:00:00','2026-01-01T00:00:00')",
        (key, decision))


def _skip_rows(conn, actor_id: str, task_id: str, stage: str) -> dict[str, int]:
    """直读产品写出的跳过覆盖层（断言用，非合成源）。"""
    return {row[0]: int(row[1]) for row in conn.execute(
        "SELECT zotero_key,skip_seq FROM workflow_skip_order "
        "WHERE actor_id=? AND task_id=? AND stage=?",
        (actor_id, task_id, stage))}


def _new_task_dir() -> Path:
    directory = tempfile.mkdtemp(prefix="rf216-skip-formal-taskdir-")
    atexit.register(shutil.rmtree, directory, True)
    return Path(directory)


class SkipFormalCorePins(unittest.TestCase):
    """core 层（coscreen.review_workflow）：跳过语义 / 沉底 / 隔离 / 自愈 / 撤销。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rf216-skip-formal-core-")
        self.addCleanup(self._tmp.cleanup)
        self._seq = itertools.count()

    def _new_db(self, *, screening_schema: bool = False) -> Path:
        """真实临时库；screening_schema=True 时先走产品建库入口 init_db。"""
        db = Path(self._tmp.name) / f"core-{next(self._seq)}.db"
        if screening_schema:
            screening_db.init_db(db)
        store.migrate(db)
        return db

    def _random_positions(self, db: Path) -> dict[str, int]:
        with closing(store.connect(db, read_only=True)) as conn:
            return {row[0]: int(row[1]) for row in conn.execute(
                "SELECT zotero_key,position FROM workflow_random_order "
                "WHERE actor_id=? AND task_id=? AND scope=?",
                (ACTOR, TASK, SCOPE))}

    def test_skip_sinks_skipped_keys_main_order_unchanged(self):
        db = self._new_db()
        pending = set(KEYS)
        main = store.random_order(db, ACTOR, TASK, SCOPE, KEYS, pending)
        # 主序 = 产品持久化的 position 序（首调 SystemRandom 洗牌后落库）。
        positions = self._random_positions(db)
        self.assertEqual(main, sorted(KEYS, key=positions.__getitem__))
        first = store.skip_queue_item(db, ACTOR, TASK, "screen", "K03")
        second = store.skip_queue_item(db, ACTOR, TASK, "screen", "K01")
        self.assertEqual((first, second), (1, 2))
        # 裁定 #4：跳过与主序机制完全隔离——主序复算与 position 落库零变化。
        self.assertEqual(store.random_order(db, ACTOR, TASK, SCOPE, KEYS, pending),
                         main)
        self.assertEqual(self._random_positions(db), positions)
        with closing(store.connect(db, read_only=True)) as conn:
            skips = _skip_rows(conn, ACTOR, TASK, "screen")
        self.assertEqual(skips, {"K03": 1, "K01": 2})
        # 裁定 #3：先跳的 K03 沉 N+1、后跳的 K01 沉 N+2（先跳在前）。
        self.assertEqual(_sinking_order(main, skips),
                         [key for key in main if key not in skips]
                         + ["K03", "K01"])
        # 裁定 #5：随机序刷新只重排主序；跳过状态是文献级属性，不随刷新清除。
        refreshed = store.random_order(db, ACTOR, TASK, SCOPE, KEYS, pending,
                                       shuffle=True)
        self.assertEqual(sorted(refreshed), sorted(KEYS))
        with closing(store.connect(db, read_only=True)) as conn:
            skips_after_refresh = _skip_rows(conn, ACTOR, TASK, "screen")
        self.assertEqual(skips_after_refresh, {"K03": 1, "K01": 2})
        self.assertEqual(_sinking_order(refreshed, skips_after_refresh),
                         [key for key in refreshed if key not in skips]
                         + ["K03", "K01"])

    def test_repeated_skip_bumps_seq_to_back(self):
        db = self._new_db()
        self.assertEqual(
            store.skip_queue_item(db, ACTOR, TASK, "screen", "K03"), 1)
        self.assertEqual(
            store.skip_queue_item(db, ACTOR, TASK, "screen", "K01"), 2)
        # 裁定 #3：同一篇重复跳过 = 四列主键 UPSERT 更新序号（max+1），
        # 越跳越后。
        self.assertEqual(
            store.skip_queue_item(db, ACTOR, TASK, "screen", "K03"), 3)
        with closing(store.connect(db, read_only=True)) as conn:
            skips = _skip_rows(conn, ACTOR, TASK, "screen")
        self.assertEqual(skips, {"K01": 2, "K03": 3})
        self.assertEqual(_sinking_order(KEYS, skips),
                         [key for key in KEYS if key not in skips]
                         + ["K01", "K03"])

    def test_skip_rejected_for_decided_item_screen_and_fulltext(self):
        db = self._new_db(screening_schema=True)
        with closing(store.connect(db)) as conn, conn:
            _insert_articles(conn, ("K07", "K08", "K09"))
            _decide(conn, "decisions", "K07", "include")
            _decide(conn, "stage2_decisions", "K08", "exclude")
        # 裁定 #2：跳过只对未决策文献有意义——两阶段已决策键均类型化拒绝。
        with self.assertRaises(store.SkipDecisionExistsError) as ctx:
            store.skip_queue_item(db, ACTOR, TASK, "screen", "K07")
        self.assertEqual(ctx.exception.error_code, "skip_decision_exists")
        self.assertEqual((ctx.exception.stage, ctx.exception.zotero_key),
                         ("screen", "K07"))
        with self.assertRaises(store.SkipDecisionExistsError) as ctx:
            store.skip_queue_item(db, ACTOR, TASK, "fulltext", "K08")
        self.assertEqual((ctx.exception.stage, ctx.exception.zotero_key),
                         ("fulltext", "K08"))
        # 拒绝不留痕：未决策键可正常跳过；已决策键无跳过行。
        self.assertEqual(
            store.skip_queue_item(db, ACTOR, TASK, "screen", "K09"), 1)
        with closing(store.connect(db, read_only=True)) as conn:
            self.assertEqual(_skip_rows(conn, ACTOR, TASK, "screen"),
                             {"K09": 1})
            self.assertEqual(_skip_rows(conn, ACTOR, TASK, "fulltext"), {})

    def test_skip_self_heals_legacy_store_without_skip_table(self):
        db = self._new_db()
        with closing(store.connect(db)) as conn, conn:
            conn.execute("DROP TABLE workflow_skip_order")
        # v1 存量库特征：迁移账本已记 v1，migrate 早返回、不重跑 _SCHEMA，
        # 表保持缺失（证明自愈只能来自 skip_queue_item 事务内的 DDL）。
        store.migrate(db)
        with closing(store.connect(db, read_only=True)) as conn:
            self.assertFalse(store.has_table(conn, "workflow_skip_order"))
        # 首次跳过：事务内幂等自愈建表（CREATE IF NOT EXISTS）并成功写入。
        self.assertEqual(
            store.skip_queue_item(db, ACTOR, TASK, "screen", "K01"), 1)
        with closing(store.connect(db, read_only=True)) as conn:
            self.assertTrue(store.has_table(conn, "workflow_skip_order"))
            self.assertEqual(_skip_rows(conn, ACTOR, TASK, "screen"),
                             {"K01": 1})

    def test_skip_writes_no_decision_rows(self):
        db = self._new_db(screening_schema=True)
        with closing(store.connect(db)) as conn, conn:
            _insert_articles(conn, ("K01", "K02", "K03"))
        self.assertEqual(
            store.skip_queue_item(db, ACTOR, TASK, "screen", "K01"), 1)
        self.assertEqual(
            store.skip_queue_item(db, ACTOR, TASK, "screen", "K02"), 2)
        self.assertEqual(
            store.skip_queue_item(db, ACTOR, TASK, "fulltext", "K03"), 1)
        # 裁定 #2/#7：跳过不是决策——两张决策表零行；留痕只在覆盖层，
        # 且 screen/fulltext 序号各自独立编号。
        with closing(store.connect(db, read_only=True)) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0], 0)
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM stage2_decisions").fetchone()[0], 0)
            self.assertEqual(_skip_rows(conn, ACTOR, TASK, "screen"),
                             {"K01": 1, "K02": 2})
            self.assertEqual(_skip_rows(conn, ACTOR, TASK, "fulltext"),
                             {"K03": 1})

    def test_undo_decision_keeps_skip_row_live(self):
        db = self._new_db(screening_schema=True)
        with closing(store.connect(db)) as conn, conn:
            _insert_articles(conn, ("K01", "K02", "K03"))
        self.assertEqual(
            store.skip_queue_item(db, ACTOR, TASK, "screen", "K03"), 1)
        # 跳过后补出决策（对已跳过文献做纳入/排除不是跳过路径的违规）。
        with closing(store.connect(db)) as conn, conn:
            _decide(conn, "decisions", "K03", "include")
        # 撤销决策（workspace_undo reopen 语义）：决策行删除 → 回待筛。
        with closing(store.connect(db)) as conn, conn:
            conn.execute("DELETE FROM decisions WHERE zotero_key=?", ("K03",))
        # 裁定 #8：旧跳过记录保留，读取端合成天然再次生效（无需清理代码）。
        with closing(store.connect(db, read_only=True)) as conn:
            self.assertEqual(_skip_rows(conn, ACTOR, TASK, "screen"),
                             {"K03": 1})
        # 回到 pending 后可再次跳过；序号在旧记录之上递增（max+1）。
        self.assertEqual(
            store.skip_queue_item(db, ACTOR, TASK, "screen", "K03"), 2)
        with closing(store.connect(db, read_only=True)) as conn:
            self.assertEqual(_skip_rows(conn, ACTOR, TASK, "screen"),
                             {"K03": 2})


class SkipFormalHttpPins(unittest.TestCase):
    """HTTP 层（最小 app 直挂路由）：skip 端点计数 / 409 结构化载荷 / 队列合成。"""

    @classmethod
    def setUpClass(cls):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from custom_backend import review_workflow_api as wf_api

        cls._task_dirs = {}

        def task_lookup(task_id: str):
            # 真实 TaskInfo；任务目录无协作库 → get_owner 返回 None →
            # legacy 路径（write_guard 跳过团队门，actor 库=属主库）。
            return tasks.TaskInfo(
                task_id=task_id,
                dir=cls._task_dirs.setdefault(task_id, _new_task_dir()),
                display_name=task_id)

        cls._data_dir = Path(tempfile.mkdtemp(prefix="rf216-skip-formal-data-"))
        atexit.register(shutil.rmtree, str(cls._data_dir), True)
        app = FastAPI()
        wf_api.register_review_workflow_routes(
            app,
            require_user=lambda: {"user_id": 7, "username": "t7"},
            task_lookup=task_lookup,
            screener_db=lambda info, username: (
                Path(info.dir) / f"{tasks.slugify_screener(username)}.db"),
            data_dir=cls._data_dir,
            visible=lambda info, user: True)
        cls.client = TestClient(app)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rf216-skip-formal-http-")
        self.addCleanup(self._tmp.cleanup)
        self.task_id = f"t216f-{self._testMethodName}"
        SkipFormalHttpPins._task_dirs[self.task_id] = Path(self._tmp.name)
        self.actor_db = (Path(self._tmp.name)
                         / f"{tasks.slugify_screener('t7')}.db")
        # 真实库 + 产品入口：init_db 全量筛选 schema → 条目 → store.migrate
        # （建 workflow 表并把条目回填关联 UNASSIGNED 来源）→ binary_reset
        # v8 补齐（请求路径 context() 对版本齐全库零额外迁移；本夹具只写
        # include/exclude 决策，reset 对无 maybe 行的库零删除）。
        screening_db.init_db(self.actor_db)
        with closing(store.connect(self.actor_db)) as conn, conn:
            _insert_articles(conn, KEYS)
        store.migrate(self.actor_db)
        binary_reset.migrate(self.actor_db, task_id=self.task_id, actor_id=7)

    def _skip_url(self, stage: str = "screen") -> str:
        return f"/api/tasks/{self.task_id}/work-queue/{stage}/skip"

    def _queue_url(self, stage: str = "screen") -> str:
        return f"/api/tasks/{self.task_id}/work-queue/{stage}"

    def test_skip_endpoint_counts_and_409_payload(self):
        # 成功跳过 → 200，返回 {pending, skipped_pending} 两计数（裁定 #6：
        # 完成门引导的后端数据面）。
        first = self.client.post(self._skip_url(), json={"zotero_key": "K03"})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json(), {"pending": 10, "skipped_pending": 1})
        second = self.client.post(self._skip_url(), json={"zotero_key": "K05"})
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json(), {"pending": 10, "skipped_pending": 2})
        # 重复跳过经端点放行：计数口径不变（seq 更新语义由 core 层钉）。
        again = self.client.post(self._skip_url(), json={"zotero_key": "K03"})
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json(), {"pending": 10, "skipped_pending": 2})
        # 已决策文献 → 结构化 409（含 pending/skipped_pending 计数）。
        with closing(store.connect(self.actor_db)) as conn, conn:
            _decide(conn, "decisions", "K07", "include")
        blocked = self.client.post(self._skip_url(), json={"zotero_key": "K07"})
        self.assertEqual(blocked.status_code, 409, blocked.text)
        detail = blocked.json()["detail"]
        self.assertEqual(detail["code"], "skip_decision_exists")
        self.assertEqual(detail["message_key"], "workflow.skip_decision_exists")
        self.assertEqual(detail["params"], {"zotero_key": "K07"})
        self.assertEqual((detail["zotero_key"], detail["stage"]),
                         ("K07", "screen"))
        self.assertEqual((detail["pending"], detail["skipped_pending"]),
                         (9, 2))
        self.assertTrue(detail["details"])
        self.assertTrue(detail["message"])
        # 409 不写跳过行：覆盖层仍只含 K03/K05 两键。
        with closing(store.connect(self.actor_db, read_only=True)) as conn:
            self.assertEqual(
                set(_skip_rows(conn, "7", self.task_id, "screen")),
                {"K03", "K05"})

    def test_queue_skipped_pending_partition(self):
        # 冷读：无跳过记录 = 原序、skipped_pending 0（零行为差异）。
        cold = self.client.get(self._queue_url())
        self.assertEqual(cold.status_code, 200, cold.text)
        cold_body = cold.json()
        self.assertEqual([item["zotero_key"] for item in cold_body["queue"]],
                         KEYS)
        self.assertEqual(cold_body["skipped_pending"], 0)
        self.assertEqual(cold_body["pending"], 10)
        self.assertEqual(cold_body["scope_progress"],
                         {"total": 10, "completed": 0, "pending": 10})
        # 跳两篇 + 决策一篇 → pending 分解（10 = 9 未决 + 其中 2 跳过）。
        self.assertEqual(self.client.post(
            self._skip_url(), json={"zotero_key": "K03"}).status_code, 200)
        self.assertEqual(self.client.post(
            self._skip_url(), json={"zotero_key": "K07"}).status_code, 200)
        with closing(store.connect(self.actor_db)) as conn, conn:
            _decide(conn, "decisions", "K01", "include")
        warm = self.client.get(self._queue_url())
        self.assertEqual(warm.status_code, 200, warm.text)
        warm_body = warm.json()
        # 裁定 #3：未跳键保持导入主序在前（已决策键不因决策换位），跳过键
        # 按 skip_seq 沉底（先跳的 K03 在 N+1、后跳的 K07 在 N+2）。
        self.assertEqual([item["zotero_key"] for item in warm_body["queue"]],
                         ["K01", "K02", "K04", "K05", "K06", "K08", "K09",
                          "K10", "K03", "K07"])
        self.assertEqual(warm_body["pending"], 9)
        self.assertEqual(warm_body["skipped_pending"], 2)
        self.assertEqual(warm_body["scope_progress"],
                         {"total": 10, "completed": 1, "pending": 9})


if __name__ == "__main__":
    unittest.main(verbosity=2)
