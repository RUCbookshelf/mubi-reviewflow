# -*- coding: utf-8 -*-
"""2.1.5-sof · SoF 判定写回（采纳为判定）红钉（专属新文件，撤实现必红）。

用户 2026-11-10 裁定：SoF 采纳为判定**提前到 2.1.5**（设计 tables v1.0 原留
v2：判定由研究者填、建议自动、采纳交互另立端点）。本文件先于实现写成，
端点缺席时全红。

按任务书钉清单成文：

1. **权限**：普通成员 PUT → 结构化 403 ``tables.judgment_requires_consensus``
   且**零写入**（协作库不得出现 sof_judgments 表/行）；属主/带
   ``consensus:write`` 的裁决员 manual 写入 → ``?detail=1`` 出现 judgment 块
   且 SoF 行以判定为准展示（总体置信度列 = 判定值 + 「已判定」标记）；
2. **采纳**：``adopt_from_suggestion=true`` → 判定 = 当前 grade_assessor 建议、
   快照完整（四构件建议+理由 signal+当时 n_studies）、手工 components/overall
   被服务端忽略；无合成结果（单项研究/无效应数据）不可采纳 → 400 结构化；
3. **不可变性**：建议事后变化（新增研究/评价）→ 已采纳判定与快照不变；
4. **覆盖**：同一结局四元组再写 = 新版本，``prev`` 保留上一版（单级回看），
   ``decided_by``/``decided_at`` 更新；
5. **校验**：结局四元组不存在于当前筛选员库 → 400 ``tables.judgment_outcome_unknown``；
   manual 缺 components/overall → 400；构件/overall 枚举非法 → 422（Pydantic）；
6. **表幂等**：``CREATE TABLE IF NOT EXISTS``，重复建表/重复写入不炸；
7. **无逐人评分**：judgment 块只含定稿判定（负钉：块内无 rater 级字段，
   响应不含 raters/ratings/votes/per_member 等键）；
   并钉裁定：判定是「定稿结论」而非「他人评分」，普通成员（不带 rob_tool）
   读 ``?detail=1`` 也能看到 judgment 块含档位（rob 列权限口径不变）。

运行：
    python -m pytest build/tests/test_2_1_5_sof_judgment.py -q
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

fixture = tempfile.TemporaryDirectory(prefix="rf_sofj_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient  # noqa: E402

from coscreen import appraisal as appraisal_core  # noqa: E402
from coscreen import db as db_mod  # noqa: E402
from coscreen import review_analysis as analysis_mod  # noqa: E402
from coscreen import sof_judgments as sof_judgments_core  # noqa: E402
from coscreen import tasks as tasks_mod  # noqa: E402
from coscreen.models import Article  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend.main import DATA_DIR, app  # noqa: E402

TOOL = "rob2"
#: 判定块的固定键（契约冻结；多一个少一个都红）。
JUDGMENT_KEYS = {"components", "overall", "note", "source", "adopted_snapshot",
                 "decided_by", "decided_at", "prev"}


def _make_user(prefix: str):
    username = f"{prefix}@example.test"
    user = auth_mod.create_user(username, f"{prefix}-Pass-2026!")
    return user, {"Authorization": "Bearer " +
                  auth_mod.create_access_token(user["id"], user["username"])}


class SofJudgmentBase(unittest.TestCase):
    _seq = 0
    #: 每类独立账号前缀（混跑不撞用户名/任务名）。
    PREFIX = "sofjbase"

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        cls.owner, cls.auth = _make_user(f"{cls.PREFIX}owner")
        cls.member, cls.member_auth = _make_user(f"{cls.PREFIX}member")

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        type(self)._seq += 1
        response = self.client.post(
            "/api/tasks", json={"name": f"{self.PREFIX}-{type(self)._seq}"},
            headers=self.auth)
        self.assertIn(response.status_code, (200, 201), response.text)
        self.task = response.json()["task_id"]
        invite = self.client.post(
            f"/api/tasks/{self.task}/collaboration/invite",
            json={"username": self.member["username"], "role": "coder"},
            headers=self.auth)
        self.assertEqual(invite.status_code, 201, invite.text)
        self.info = tasks_mod.find_task(self.task, DATA_DIR)
        self.db = self.info.dir / (tasks_mod.slugify_screener(self.owner["username"]) + ".db")
        self.store = self.info.dir / "collaboration.db"
        self.sof = f"/api/tasks/{self.task}/tables/sof"
        self.judgment = f"/api/tasks/{self.task}/tables/sof/judgment"

    # ---- 夹具 ---------------------------------------------------------
    def _seed_effects(self, *, comparison="干预 vs 对照", outcome="疼痛",
                      timepoint="12 周"):
        """两研究 MD 0.5/0.3 —— 与契约 §4.2 实测样例同数据（有合成结果）。"""
        db_mod.init_db(self.db)
        db_mod.upsert_articles(self.db, [
            Article(zotero_key="K1", title="Alpha RCT", authors="", year=2019),
            Article(zotero_key="K2", title="Beta cohort", authors="", year=2021),
        ], [])
        analysis_mod.save_study(self.db, "S1", "Study 1", ["K1"])
        analysis_mod.save_study(self.db, "S2", "Study 2", ["K2"])
        analysis_mod.save_effect(self.db, "S1", comparison, outcome, timepoint,
                                 "MD", 0.5, 0.2, "K1")
        analysis_mod.save_effect(self.db, "S2", comparison, outcome, timepoint,
                                 "MD", 0.3, 0.25, "K2")

    def _put_appraisal(self, article: str, judgement: str) -> None:
        conn = db_mod._connect(self.db)
        try:
            appraisal_core.ensure_appraisal_storage(conn)
            appraisal_core.save_record(
                conn, article, TOOL, responses={}, comments={}, variant=None,
                overall={"mode": "rob2_domains", "judgement": judgement})
            conn.commit()
        finally:
            conn.close()

    def _member_library(self):
        """成员自己的分析库（同一结局四元组）：裁决员采纳/成员自查用。"""
        path = self.info.dir / (tasks_mod.slugify_screener(self.member["username"]) + ".db")
        db_mod.init_db(path)
        db_mod.upsert_articles(path, [
            Article(zotero_key="K1", title="Member study", authors="", year=2020)], [])
        analysis_mod.save_study(path, "S1", "Study 1", ["K1"])
        analysis_mod.save_effect(path, "S1", "干预 vs 对照", "疼痛", "12 周",
                                 "MD", 0.5, 0.2, "K1")
        return path

    def _grant_reconciler(self):
        response = self.client.post(
            f"/api/tasks/{self.task}/collaboration/roles",
            json={"user_id": self.member["id"], "role": "reconciler"},
            headers=self.auth)
        self.assertEqual(response.status_code, 201, response.text)

    # ---- 请求小工具 ---------------------------------------------------
    def _put(self, headers=None, **body):
        return self.client.put(self.judgment, json=body, headers=headers or self.auth)

    def _detail(self, headers=None, **body):
        payload = {"format": "csv"}
        payload.update(body)
        return self.client.post(self.sof, json=payload, params={"detail": 1},
                                headers=headers or self.auth)

    def _store_rows(self) -> list[tuple]:
        with closing(sqlite3.connect(self.store)) as conn:
            return conn.execute(
                "SELECT comparison, outcome, timepoint, measure, rob, overall, "
                "decided_by, decided_at, prev_json FROM sof_judgments").fetchall()

    def _table_exists(self) -> bool:
        with closing(sqlite3.connect(self.store)) as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='sof_judgments'"
            ).fetchall()
        return bool(rows)


# ---------------------------------------------------------------------------
# 1. 权限：成员 403 零写入；属主/裁决员 manual 写入 → judgment 块 + 行展示
# ---------------------------------------------------------------------------

class JudgmentPermissionPins(SofJudgmentBase):
    PREFIX = "sofjperm"

    def test_member_put_is_403_zero_writes(self):
        """钉 ①a：普通成员 PUT → 结构化 403，且协作库零写入（表都不建）。"""
        self._seed_effects()
        response = self._put(
            headers=self.member_auth,
            comparison="干预 vs 对照", outcome="疼痛", timepoint="12 周", measure="MD",
            components={"rob": "serious", "inconsistency": "none",
                        "imprecision": "none", "publication_bias": "none"},
            overall="low", note="成员越权尝试")
        self.assertEqual(response.status_code, 403, response.text)
        body = response.json()["detail"]
        self.assertEqual(body["error_code"], "tables_judgment_requires_consensus")
        self.assertEqual(body["message_key"], "tables.judgment_requires_consensus")
        # 零写入：sof_judgments 表不得被创建（权限闸在建表之前）
        self.assertFalse(self._table_exists(), "成员 403 不得建 sof_judgments 表")

    def test_owner_manual_write_then_member_still_403_and_zero_new_writes(self):
        """钉 ①b：属主写入 1 行后成员再 PUT 仍 403，且行内容零变化。"""
        self._seed_effects()
        ok = self._put(
            comparison="干预 vs 对照", outcome="疼痛", timepoint="12 周", measure="MD",
            components={"rob": "serious", "inconsistency": "none",
                        "imprecision": "none", "publication_bias": "none"},
            overall="low", note="属主判定")
        self.assertEqual(ok.status_code, 200, ok.text)
        rows = self._store_rows()
        self.assertEqual(len(rows), 1)
        denied = self._put(
            headers=self.member_auth,
            comparison="干预 vs 对照", outcome="疼痛", timepoint="12 周", measure="MD",
            components={"rob": "very_very_serious", "inconsistency": "serious",
                        "imprecision": "serious", "publication_bias": "serious"},
            overall="very_low", note="覆盖尝试")
        self.assertEqual(denied.status_code, 403, denied.text)
        after = self._store_rows()
        self.assertEqual(after, rows, "成员 403 后不得有任何行变化")

    def test_owner_manual_write_appears_in_detail_and_cells(self):
        """钉 ①c：属主 manual 写入 → detail=1 出现 judgment 块，行以判定展示。"""
        self._seed_effects()
        response = self._put(
            comparison="干预 vs 对照", outcome="疼痛", timepoint="12 周", measure="MD",
            components={"rob": "serious", "inconsistency": "none",
                        "imprecision": "none", "publication_bias": "none"},
            overall="moderate", note="证据大体可信")
        self.assertEqual(response.status_code, 200, response.text)
        block = response.json()["judgment"]
        self.assertEqual(set(block), JUDGMENT_KEYS)
        self.assertEqual(block["source"], "manual")
        self.assertIsNone(block["adopted_snapshot"])
        self.assertEqual(block["components"]["rob"], "serious")
        self.assertEqual(block["decided_by"], self.owner["id"])

        detail = self._detail().json()
        self.assertEqual(len(detail["rows"]), 1)
        row = detail["rows"][0]
        self.assertEqual(row["judgment"]["overall"], "moderate")
        self.assertEqual(row["judgment"]["note"], "证据大体可信")
        # 行以判定为准：总体置信度 = 判定值 + 已判定标记；四构件 = 判定档位
        self.assertEqual(row["cells"]["certainty"], "中 ⊕⊕⊕○（已判定）")
        self.assertEqual(row["cells"]["rob"], "严重(-1)（已判定）")
        self.assertEqual(row["cells"]["inconsistency"], "无降级（已判定）")
        # 建议仍在 detail 里并列（不因判定消失）
        self.assertIn("suggestions", row["detail"]["grade"])
        self.assertEqual(set(row["detail"]["grade"]["suggestions"]),
                         {"rob", "inconsistency", "imprecision", "publication_bias"})

    def test_judged_row_csv_also_shows_judgment(self):
        """钉 ①d：装配口径不分出口——CSV 下载同样以判定展示。"""
        self._seed_effects()
        ok = self._put(
            comparison="干预 vs 对照", outcome="疼痛", timepoint="12 周", measure="MD",
            components={"rob": "none", "inconsistency": "none",
                        "imprecision": "none", "publication_bias": "none"},
            overall="high", note="")
        self.assertEqual(ok.status_code, 200, ok.text)
        csv_resp = self.client.post(self.sof, json={"format": "csv"},
                                    headers=self.auth)
        self.assertEqual(csv_resp.status_code, 200, csv_resp.text)
        self.assertIn("已判定", csv_resp.content.decode("utf-8-sig"))
        self.assertIn("高 ⊕⊕⊕⊕（已判定）", csv_resp.content.decode("utf-8-sig"))

    def test_adjudicator_manual_write(self):
        """钉 ①e：裁决员（consensus:write）与属主同权可写（自己的库上）。"""
        self._member_library()
        self._grant_reconciler()
        response = self._put(
            headers=self.member_auth,
            comparison="干预 vs 对照", outcome="疼痛", timepoint="12 周", measure="MD",
            components={"rob": "none", "inconsistency": "serious",
                        "imprecision": "none", "publication_bias": "none"},
            overall="low", note="裁决员判定")
        self.assertEqual(response.status_code, 200, response.text)
        block = response.json()["judgment"]
        self.assertEqual(block["decided_by"], self.member["id"])
        # 成员自己的 detail=1 现在带 judgment 块
        detail = self._detail(headers=self.member_auth).json()
        self.assertEqual(detail["rows"][0]["judgment"]["overall"], "low")

    def test_unjudged_row_judgment_is_null(self):
        """无判定的行 judgment 块 = null（契约：无判定=null，不是缺键）。"""
        self._seed_effects()
        detail = self._detail().json()
        self.assertEqual(len(detail["rows"]), 1)
        self.assertIn("judgment", detail["rows"][0])
        self.assertIsNone(detail["rows"][0]["judgment"])


# ---------------------------------------------------------------------------
# 2. 采纳为判定：判定=当前建议、快照完整、手工字段被忽略；无合成不可采纳
# ---------------------------------------------------------------------------

class JudgmentAdoptPins(SofJudgmentBase):
    PREFIX = "sofjadopt"

    def test_adopt_takes_current_suggestion_and_ignores_manual(self):
        """钉 ②：adopt_from_suggestion → 判定=当前建议；快照含四构件建议/理由/
        n_studies；手工 components/overall 被忽略（note 保留）。"""
        self._seed_effects()
        self._put_appraisal("K1", "high")  # → GRADE serious 档 → rob 建议 very_serious
        response = self._put(
            comparison="干预 vs 对照", outcome="疼痛", timepoint="12 周", measure="MD",
            adopt_from_suggestion=True, rob_tool=TOOL,
            # 手工字段故意全错位：采纳时必须被忽略
            components={"rob": "none", "inconsistency": "very_very_serious",
                        "imprecision": "very_very_serious",
                        "publication_bias": "very_very_serious"},
            overall="high", note="采纳自动建议")
        self.assertEqual(response.status_code, 200, response.text)
        block = response.json()["judgment"]
        self.assertEqual(block["source"], "adopted")
        # 判定 = 当前建议：rob=very_serious（1/1 研究高风险带），其余 none
        self.assertEqual(block["components"],
                         {"rob": "very_serious", "inconsistency": "none",
                          "imprecision": "none", "publication_bias": "none"})
        # 总体 = 建议口径算出的 final_quality（高 -2 = 低）
        self.assertEqual(block["overall"], "low")
        self.assertEqual(block["note"], "采纳自动建议")
        # 快照完整：四构件建议（level+signal 理由）+ n_studies + 参数回显
        snapshot = block["adopted_snapshot"]
        self.assertEqual(set(snapshot["components"]),
                         {"rob", "inconsistency", "imprecision", "publication_bias"})
        for name, piece in snapshot["components"].items():
            self.assertEqual(set(piece), {"level", "signal"}, name)
        self.assertEqual(snapshot["components"]["rob"]["level"], "very_serious")
        # signal 原样（grade_assessor.py:200 的 rule 码，不改写不四舍五入）
        self.assertEqual(snapshot["components"]["rob"]["signal"]["rule"],
                         "majority_high_risk")
        self.assertEqual(snapshot["n_studies"], 2)
        self.assertEqual(snapshot["initial_design"], "rct")

    def test_adopt_without_manual_fields_is_accepted(self):
        """采纳请求可以不带 components/overall（服务端取建议，缺字段不 400）。"""
        self._seed_effects()
        response = self._put(
            comparison="干预 vs 对照", outcome="疼痛", timepoint="12 周", measure="MD",
            adopt_from_suggestion=True)
        self.assertEqual(response.status_code, 200, response.text)
        block = response.json()["judgment"]
        self.assertEqual(block["source"], "adopted")
        self.assertEqual(block["components"],
                         {"rob": "none", "inconsistency": "none",
                          "imprecision": "none", "publication_bias": "none"})
        self.assertEqual(block["overall"], "high")

    def test_adopt_without_synthesis_is_structured_400(self):
        """钉 ②b：单项研究行没有合成 → 无「当前总体置信度建议」可采纳 →
        400 结构化（绝不按全构件无降级凑出「高」）；manual 在同一行仍可写。"""
        self._seed_effects()
        # 单独一条只有单项研究的分层（S1 不良反应）
        analysis_mod.save_effect(self.db, "S1", "干预 vs 对照", "不良反应", "12 周",
                                 "MD", 0.4, 0.2, "K1")
        response = self._put(
            comparison="干预 vs 对照", outcome="不良反应", timepoint="12 周",
            measure="MD", adopt_from_suggestion=True)
        self.assertEqual(response.status_code, 400, response.text)
        body = response.json()["detail"]
        self.assertEqual(body["error_code"], "tables_judgment_adopt_unavailable")
        self.assertEqual(body["message_key"], "tables.judgment_adopt_unavailable")
        # 零写入：不可采纳的行不得落任何判定
        if self._table_exists():
            self.assertEqual(self._store_rows(), [])
        # 人工判定不受合成存在与否限制
        manual = self._put(
            comparison="干预 vs 对照", outcome="不良反应", timepoint="12 周",
            measure="MD",
            components={"rob": "serious", "inconsistency": "none",
                        "imprecision": "serious", "publication_bias": "none"},
            overall="low", note="单项研究人工判定")
        self.assertEqual(manual.status_code, 200, manual.text)

    def test_suggestion_change_later_does_not_mutate_judgment(self):
        """钉 ③：采纳后新增第三项研究（建议变化）→ 判定与快照仍是采纳时值。"""
        self._seed_effects()
        response = self._put(
            comparison="干预 vs 对照", outcome="疼痛", timepoint="12 周", measure="MD",
            adopt_from_suggestion=True)
        self.assertEqual(response.status_code, 200, response.text)
        adopted = response.json()["judgment"]
        self.assertEqual(adopted["components"]["inconsistency"], "none")

        # 事后变化：加第三项研究（效应差异巨大 → I² 飙高 → 不一致性建议 serious）
        db_mod.upsert_articles(self.db, [
            Article(zotero_key="K3", title="Gamma outlier", authors="", year=2022)], [])
        analysis_mod.save_study(self.db, "S3", "Study 3", ["K3"])
        analysis_mod.save_effect(self.db, "S3", "干预 vs 对照", "疼痛", "12 周",
                                 "MD", 5.0, 0.3, "K3")

        detail = self._detail().json()
        row = detail["rows"][0]
        # 建议确实变了（证明变化真的发生了，钉不是空转）
        self.assertEqual(row["detail"]["grade"]["suggestions"]["inconsistency"]["level"],
                         "serious")
        self.assertEqual(row["n_studies"], 3)
        # 判定不变、快照不变（仍是采纳时：inconsistency=none、n_studies=2）
        judgment = row["judgment"]
        self.assertEqual(judgment["components"], adopted["components"])
        self.assertEqual(judgment["overall"], adopted["overall"])
        self.assertEqual(judgment["adopted_snapshot"]["n_studies"], 2)
        self.assertEqual(
            judgment["adopted_snapshot"]["components"]["inconsistency"]["level"], "none")
        # 行展示仍以判定为准（不被新建议翻动）
        self.assertEqual(row["cells"]["inconsistency"], "无降级（已判定）")


# ---------------------------------------------------------------------------
# 3. 覆盖：prev 单级回看 + decided_by/at 更新
# ---------------------------------------------------------------------------

class JudgmentOverwritePins(SofJudgmentBase):
    PREFIX = "sofjover"

    def test_overwrite_keeps_prev_and_updates_decided_by_at(self):
        """钉 ④：同一四元组再写 = 新版本；prev 保留上一版（单级）；
        decided_at 单调前进；换人写 → decided_by 更新。"""
        self._seed_effects()
        v1 = self._put(
            comparison="干预 vs 对照", outcome="疼痛", timepoint="12 周", measure="MD",
            components={"rob": "serious", "inconsistency": "none",
                        "imprecision": "none", "publication_bias": "none"},
            overall="moderate", note="v1")
        self.assertEqual(v1.status_code, 200, v1.text)
        block1 = v1.json()["judgment"]
        self.assertFalse(v1.json()["replaced"])
        self.assertIsNone(block1["prev"])

        time.sleep(0.01)
        v2 = self._put(
            comparison="干预 vs 对照", outcome="疼痛", timepoint="12 周", measure="MD",
            components={"rob": "very_serious", "inconsistency": "serious",
                        "imprecision": "none", "publication_bias": "none"},
            overall="low", note="v2")
        self.assertEqual(v2.status_code, 200, v2.text)
        self.assertTrue(v2.json()["replaced"])
        block2 = v2.json()["judgment"]
        # 新版生效
        self.assertEqual(block2["overall"], "low")
        self.assertEqual(block2["note"], "v2")
        # prev = 上一版（不带它自己的 prev —— 单级回看，不做全历史）
        self.assertEqual(block2["prev"]["overall"], "moderate")
        self.assertEqual(block2["prev"]["note"], "v1")
        self.assertEqual(block2["prev"]["components"], block1["components"])
        self.assertIsNone(block2["prev"]["prev"])
        # decided_by/at 更新
        self.assertEqual(block2["decided_by"], self.owner["id"])
        self.assertGreater(block2["decided_at"], block1["decided_at"])

        # 裁决员覆盖 → decided_by 换人（成员库同四元组）
        self._member_library()
        self._grant_reconciler()
        time.sleep(0.01)
        v3 = self._put(
            headers=self.member_auth,
            comparison="干预 vs 对照", outcome="疼痛", timepoint="12 周", measure="MD",
            components={"rob": "none", "inconsistency": "none",
                        "imprecision": "none", "publication_bias": "none"},
            overall="high", note="v3")
        self.assertEqual(v3.status_code, 200, v3.text)
        block3 = v3.json()["judgment"]
        self.assertEqual(block3["decided_by"], self.member["id"])
        self.assertEqual(block3["prev"]["overall"], "low")  # prev 只留上一版
        detail = self._detail().json()
        self.assertEqual(detail["rows"][0]["judgment"]["prev"]["note"], "v2")


# ---------------------------------------------------------------------------
# 4. 校验：四元组未知 400 / manual 缺字段 400 / 枚举非法 422
# ---------------------------------------------------------------------------

class JudgmentValidationPins(SofJudgmentBase):
    PREFIX = "sofjvalid"

    def test_unknown_outcome_tuple_is_structured_400(self):
        """钉 ⑤a：结局四元组不存在于当前筛选员库 → 400 结构化（params 回显）。"""
        self._seed_effects()
        for payload in (
            {"comparison": "干预 vs 对照", "outcome": "不存在的结局",
             "timepoint": "12 周", "measure": "MD"},
            {"comparison": "别的比较", "outcome": "疼痛",
             "timepoint": "12 周", "measure": "MD"},
            {"comparison": "干预 vs 对照", "outcome": "疼痛",
             "timepoint": "24 周", "measure": "MD"},
            {"comparison": "干预 vs 对照", "outcome": "疼痛",
             "timepoint": "12 周", "measure": "OR"},
        ):
            with self.subTest(tuple=payload):
                body = dict(payload)
                body.update(components={"rob": "none", "inconsistency": "none",
                                        "imprecision": "none",
                                        "publication_bias": "none"},
                            overall="high")
                response = self._put(**body)
                self.assertEqual(response.status_code, 400, response.text)
                detail = response.json()["detail"]
                self.assertEqual(detail["error_code"], "tables_judgment_outcome_unknown")
                self.assertEqual(detail["message_key"],
                                 "tables.judgment_outcome_unknown")
                self.assertEqual(detail["params"]["outcome"], payload["outcome"])

    def test_manual_missing_fields_is_structured_400(self):
        """钉 ⑤b：manual 写入缺 components/overall → 400 结构化。"""
        self._seed_effects()
        response = self._put(
            comparison="干预 vs 对照", outcome="疼痛", timepoint="12 周", measure="MD",
            overall="high", note="缺 components")
        self.assertEqual(response.status_code, 400, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["error_code"], "tables_judgment_missing_fields")
        self.assertEqual(detail["message_key"], "tables.judgment_missing_fields")

        response = self._put(
            comparison="干预 vs 对照", outcome="疼痛", timepoint="12 周", measure="MD",
            components={"rob": "none", "inconsistency": "none",
                        "imprecision": "none", "publication_bias": "none"},
            note="缺 overall")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["error_code"],
                         "tables_judgment_missing_fields")

    def test_invalid_enums_are_422(self):
        """钉 ⑤c：构件/overall 枚举非法 → Pydantic 422（与本向导既有约定一致）。"""
        self._seed_effects()
        response = self._put(
            comparison="干预 vs 对照", outcome="疼痛", timepoint="12 周", measure="MD",
            components={"rob": "catastrophic", "inconsistency": "none",
                        "imprecision": "none", "publication_bias": "none"},
            overall="high")
        self.assertEqual(response.status_code, 422, response.text)
        response = self._put(
            comparison="干预 vs 对照", outcome="疼痛", timepoint="12 周", measure="MD",
            components={"rob": "none", "inconsistency": "none",
                        "imprecision": "none", "publication_bias": "none"},
            overall="supreme")
        self.assertEqual(response.status_code, 422, response.text)

    def test_adopt_unknown_tuple_still_400_before_any_write(self):
        """采纳路径同样先验四元组：未知四元组 400 且零写入。"""
        response = self._put(
            comparison="干预 vs 对照", outcome="不存在", timepoint="12 周",
            measure="MD", adopt_from_suggestion=True)
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["error_code"],
                         "tables_judgment_outcome_unknown")
        self.assertFalse(self._table_exists())


# ---------------------------------------------------------------------------
# 5. 幂等 + 无逐人评分 + 成员可见裁定
# ---------------------------------------------------------------------------

class JudgmentStoragePins(SofJudgmentBase):
    PREFIX = "sofjstor"

    def test_schema_ensure_is_idempotent(self):
        """钉 ⑥：建表幂等——ensure 两遍只产生一张表；不同四元组重复写入不炸。"""
        self._seed_effects()
        with closing(sqlite3.connect(self.store)) as conn:
            sof_judgments_core.ensure_schema(conn)
            sof_judgments_core.ensure_schema(conn)
            count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='table' AND name='sof_judgments'").fetchone()[0]
        self.assertEqual(count, 1)
        # 端点侧再写两个不同四元组（端点自身 ensure + upsert 幂等）
        analysis_mod.save_effect(self.db, "S1", "干预 vs 对照", "疼痛", "24 周",
                                 "MD", 0.6, 0.2, "K1")
        analysis_mod.save_effect(self.db, "S2", "干预 vs 对照", "疼痛", "24 周",
                                 "MD", 0.2, 0.2, "K2")
        first = self._put(
            comparison="干预 vs 对照", outcome="疼痛", timepoint="12 周", measure="MD",
            components={"rob": "none", "inconsistency": "none",
                        "imprecision": "none", "publication_bias": "none"},
            overall="high", note="")
        self.assertEqual(first.status_code, 200, first.text)
        second = self._put(
            comparison="干预 vs 对照", outcome="疼痛", timepoint="24 周", measure="MD",
            components={"rob": "none", "inconsistency": "none",
                        "imprecision": "none", "publication_bias": "none"},
            overall="high", note="")
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(len(self._store_rows()), 2)

    def test_judgment_block_has_no_rater_level_fields(self):
        """钉 ⑦a：judgment 块只含定稿判定——无 rater 级字段（负钉）。"""
        self._seed_effects()
        self._put_appraisal("K1", "high")
        response = self._put(
            comparison="干预 vs 对照", outcome="疼痛", timepoint="12 周", measure="MD",
            adopt_from_suggestion=True, rob_tool=TOOL, note="x")
        self.assertEqual(response.status_code, 200, response.text)
        block = response.json()["judgment"]
        self.assertEqual(set(block), JUDGMENT_KEYS)
        self.assertEqual(set(block["components"]),
                         {"rob", "inconsistency", "imprecision", "publication_bias"})
        # 负钉：PUT 响应与 detail 行都不含逐人评分键
        for forbidden in ("raters", "ratings", "votes", "per_member", "by_member",
                          "member_ratings", "rater"):
            self.assertNotIn(forbidden, response.text, forbidden)
        detail = self._detail(rob_tool=TOOL)
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertNotIn("raters", detail.text)
        self.assertNotIn("votes", detail.text)

    def test_member_sees_judgment_block_with_levels(self):
        """钉 ⑦b（裁定）：判定是定稿结论而非他人评分——普通成员（不带
        rob_tool、无 consensus 权）读 detail=1 也看到 judgment 块含档位；
        rob 列权限口径不变（detail.rob 无任何档位/levels）。"""
        self._seed_effects()
        ok = self._put(
            comparison="干预 vs 对照", outcome="疼痛", timepoint="12 周", measure="MD",
            components={"rob": "very_serious", "inconsistency": "none",
                        "imprecision": "serious", "publication_bias": "none"},
            overall="low", note="定稿")
        self.assertEqual(ok.status_code, 200, ok.text)
        self._member_library()
        detail = self._detail(headers=self.member_auth)
        self.assertEqual(detail.status_code, 200, detail.text)
        row = detail.json()["rows"][0]
        self.assertIsNotNone(row["judgment"])
        self.assertEqual(row["judgment"]["components"]["rob"], "very_serious")
        self.assertEqual(row["cells"]["certainty"], "低 ⊕⊕○○（已判定）")
        # rob 列权限不变：无 rob_tool 时聚合为空、不含档位
        self.assertEqual(row["detail"]["rob"]["counts"], {})
        self.assertNotIn("levels", repr(row["detail"]["rob"]))


# ---------------------------------------------------------------------------
# 6. 第 2 轮评审补钉
# ---------------------------------------------------------------------------

class JudgmentLibraryDegradationPins(SofJudgmentBase):
    """评审阻断项：PUT 判定写回在本人分析库损坏/被占时必须 503（不落全局 500）。

    与 Table 1 / SoF 读端点同一口径（w7 LibraryLockDegradationPins 对等；
    tables_api 自述「个人库被外部进程持锁/暂时不可读时**各端点** 503」）。
    """

    PREFIX = "sofjlib"

    def test_corrupt_screener_db_put_is_503_both_sources(self):
        """真实损坏（非 SQLite 字节）：manual 与 adopt 两种 PUT 都 503 且零写入。"""
        from custom_backend import main as main_mod
        self._seed_effects()
        self.db.write_bytes(b"this is not a sqlite database at all........")
        message = main_mod.SCREENER_LIBRARY_UNAVAILABLE_MESSAGE
        manual = self._put(
            comparison="干预 vs 对照", outcome="疼痛", timepoint="12 周", measure="MD",
            components={"rob": "none", "inconsistency": "none",
                        "imprecision": "none", "publication_bias": "none"},
            overall="high", note="")
        self.assertEqual(manual.status_code, 503, manual.text)
        self.assertEqual(manual.json()["detail"], message)
        adopt = self._put(
            comparison="干预 vs 对照", outcome="疼痛", timepoint="12 周", measure="MD",
            adopt_from_suggestion=True)
        self.assertEqual(adopt.status_code, 503, adopt.text)
        self.assertEqual(adopt.json()["detail"], message)
        # 零写入
        self.assertFalse(self._table_exists())

    def test_locked_screener_db_put_is_503(self):
        """持锁（OperationalError）同样 503；同请求在锁释放后恢复 200。"""
        from custom_backend import main as main_mod
        from custom_backend import tables_api
        self._seed_effects()
        message = main_mod.SCREENER_LIBRARY_UNAVAILABLE_MESSAGE
        with mock.patch.object(
                tables_api, "_effect_strata",
                side_effect=sqlite3.OperationalError("database is locked")):
            response = self._put(
                comparison="干预 vs 对照", outcome="疼痛", timepoint="12 周",
                measure="MD",
                components={"rob": "none", "inconsistency": "none",
                            "imprecision": "none", "publication_bias": "none"},
                overall="high", note="")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["detail"], message)
        # 故障源移除即恢复（无残留状态）
        ok = self._put(
            comparison="干预 vs 对照", outcome="疼痛", timepoint="12 周", measure="MD",
            components={"rob": "none", "inconsistency": "none",
                        "imprecision": "none", "publication_bias": "none"},
            overall="high", note="")
        self.assertEqual(ok.status_code, 200, ok.text)


class JudgmentSnapshotRedactionPins(SofJudgmentBase):
    """评审 I-19 项：采纳快照里的聚合证据（signal 计数/理由、n_studies）对无
    ``consensus:write`` 的读者必须裁剪——judgment 块（定稿判定）仍全员可见，
    但快照的推导证据与 rob 聚合计数同源（grade-input 闸内数据），不得外泄。"""

    PREFIX = "sofjredact"

    def _adopt_with_counts(self):
        """属主带 rob_tool 采纳：rob signal 含 high_risk_studies/n_studies 计数。"""
        self._seed_effects()
        self._put_appraisal("K1", "high")
        response = self._put(
            comparison="干预 vs 对照", outcome="疼痛", timepoint="12 周", measure="MD",
            adopt_from_suggestion=True, rob_tool=TOOL)
        self.assertEqual(response.status_code, 200, response.text)
        signal = response.json()["judgment"]["adopted_snapshot"]["components"]["rob"]["signal"]
        self.assertTrue(signal.get("available"), signal)
        self.assertIn("high_risk_studies", signal)
        return response.json()["judgment"]

    def test_member_gets_redacted_snapshot_owner_gets_full(self):
        """成员（不带 rob_tool）：judgment 块在、档位在；快照只剩 level（无
        signal/计数/理由/n_studies，带 evidence_redacted 标记）；属主全量。"""
        self._adopt_with_counts()
        self._member_library()
        member_detail = self._detail(headers=self.member_auth)
        self.assertEqual(member_detail.status_code, 200, member_detail.text)
        block = member_detail.json()["rows"][0]["judgment"]
        self.assertIsNotNone(block)
        # 定稿判定（档位）照旧可见——裁定不变
        self.assertEqual(block["components"]["rob"], "very_serious")
        snapshot = block["adopted_snapshot"]
        self.assertTrue(snapshot["evidence_redacted"])
        self.assertEqual(snapshot["components"]["rob"], {"level": "very_serious"})
        for name in ("rob", "inconsistency", "imprecision", "publication_bias"):
            self.assertEqual(set(snapshot["components"][name]), {"level"}, name)
        self.assertNotIn("n_studies", snapshot)
        # 整个成员响应不含聚合计数/理由码（含嵌套）
        for forbidden in ("high_risk_studies", "majority_high_risk", "n_studies"):
            self.assertNotIn(forbidden, json.dumps(block), forbidden)
        # 属主（带 rob_tool，过同一闸）拿到全量快照
        owner_detail = self._detail(rob_tool=TOOL)
        self.assertEqual(owner_detail.status_code, 200, owner_detail.text)
        owner_block = owner_detail.json()["rows"][0]["judgment"]
        self.assertFalse(owner_block["adopted_snapshot"].get("evidence_redacted", False))
        self.assertEqual(
            owner_block["adopted_snapshot"]["components"]["rob"]["signal"]["rule"],
            "majority_high_risk")
        self.assertEqual(owner_block["adopted_snapshot"]["n_studies"], 2)

    def test_prev_snapshot_is_redacted_for_member_too(self):
        """覆盖产生的 prev 块同样可能携带采纳快照——成员视角一并裁剪。
        （v1 = 采纳（快照含计数），v2 = 手工改判 → prev 携带 v1 的采纳快照。）"""
        self._adopt_with_counts()
        overwrite = self._put(
            comparison="干预 vs 对照", outcome="疼痛", timepoint="12 周", measure="MD",
            components={"rob": "serious", "inconsistency": "none",
                        "imprecision": "none", "publication_bias": "none"},
            overall="moderate", note="改判")
        self.assertEqual(overwrite.status_code, 200, overwrite.text)
        self._member_library()
        member_detail = self._detail(headers=self.member_auth)
        prev = member_detail.json()["rows"][0]["judgment"]["prev"]
        self.assertIsNotNone(prev["adopted_snapshot"])
        self.assertTrue(prev["adopted_snapshot"]["evidence_redacted"])
        self.assertNotIn("signal", json.dumps(prev["adopted_snapshot"]))
        self.assertNotIn("high_risk_studies", json.dumps(prev))
        # PUT 响应（写入者本人已过闸）不裁剪
        self.assertFalse(
            overwrite.json()["judgment"]["prev"]["adopted_snapshot"].get(
                "evidence_redacted", False))


class JudgmentRobToolManualPins(SofJudgmentBase):
    """评审低项：rob_tool 只服务采纳路径的「当前建议」现算——手工判定不读
    评价聚合，携带未知/link_only 工具应被忽略而不是 400（契约 §8.2）。"""

    PREFIX = "sofjtool"

    def test_manual_put_ignores_rob_tool(self):
        self._seed_effects()
        link_only = appraisal_core.LINK_ONLY[0]["id"]
        for tool in (link_only, "no-such-tool"):
            with self.subTest(tool=tool):
                response = self._put(
                    comparison="干预 vs 对照", outcome="疼痛", timepoint="12 周",
                    measure="MD", rob_tool=tool,
                    components={"rob": "none", "inconsistency": "none",
                                "imprecision": "none", "publication_bias": "none"},
                    overall="high", note="")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["judgment"]["source"], "manual")
                self.assertIsNone(response.json()["judgment"]["adopted_snapshot"])

    def test_adopt_still_validates_rob_tool(self):
        """采纳路径仍要现算建议：未知工具 → 400 tables_rob_tool_unknown（不变）。"""
        self._seed_effects()
        response = self._put(
            comparison="干预 vs 对照", outcome="疼痛", timepoint="12 周", measure="MD",
            adopt_from_suggestion=True, rob_tool="no-such-tool")
        self.assertEqual(response.status_code, 400, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["error_code"], "tables_rob_tool_unknown")
        self.assertEqual(detail["message_key"], "tables.rob_tool_unknown")


class PackageRebindPins(SofJudgmentBase):
    """评审跨特性项：整包导入把协作库改绑给导入者时，sof_judgments 的
    decided_by（含 prev 里的 decided_by）不得继续指向源部署用户 id——与
    role grants「引用源部署 ID 无意义且可能误授权」同因同修。"""

    PREFIX = "sofjrebind"

    def test_rewrite_collaboration_store_rebinds_decided_by(self):
        import json as json_mod

        from custom_backend import task_package_api
        self._seed_effects()
        v1 = self._put(
            comparison="干预 vs 对照", outcome="疼痛", timepoint="12 周", measure="MD",
            components={"rob": "serious", "inconsistency": "none",
                        "imprecision": "none", "publication_bias": "none"},
            overall="low", note="源部署判定")
        self.assertEqual(v1.status_code, 200, v1.text)
        v2 = self._put(
            comparison="干预 vs 对照", outcome="疼痛", timepoint="12 周", measure="MD",
            adopt_from_suggestion=True)   # v2 带 prev（v1 manual）
        self.assertEqual(v2.status_code, 200, v2.text)
        self._grant_reconciler()  # 有一项 role grant 供验证清空先例不变

        ok = task_package_api._rewrite_collaboration_store(
            self.info.dir, "collaboration.db", task_id=self.task,
            owner_id=self.member["id"], owner_name=self.member["username"])
        self.assertTrue(ok)
        with closing(sqlite3.connect(self.store)) as conn:
            conn.row_factory = sqlite3.Row
            owner = conn.execute(
                "SELECT owner_user_id FROM collaboration_task WHERE singleton=1"
            ).fetchone()
            grants = conn.execute(
                "SELECT COUNT(*) FROM collaboration_role_grants").fetchone()[0]
            rows = conn.execute(
                "SELECT decided_by, prev_json FROM sof_judgments").fetchall()
        self.assertEqual(owner["owner_user_id"], self.member["id"])
        self.assertEqual(grants, 0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["decided_by"], self.member["id"])
        prev = json_mod.loads(rows[0]["prev_json"])
        self.assertEqual(prev["decided_by"], self.member["id"])


def tearDownModule():
    fixture.cleanup()


if __name__ == "__main__":
    unittest.main()
