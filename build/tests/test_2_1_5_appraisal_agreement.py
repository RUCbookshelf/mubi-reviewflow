# -*- coding: utf-8 -*-
"""2.1.5 Wave C1 红钉：双评一致性 / 评价导出 / GRADE 衔接。

覆盖：

1. 双评 κ 数值钉：两名评价人 × 两篇文献 × 4 个条目，构造已知应答矩阵
   （一致 6/8、边际各半）→ Cohen's κ 必须精确为 0.5；逐条目一致率与
   条目级 κ 同步对账。
2. 分歧清单负钉：只含 ``article_key + item_id + 双方应答``；PUT 时写入的
   评论与说明（自由文本理由）绝不跨人出现在一致率载荷里。
3. 权限：任务成员（无 ``consensus:write``）读一致率 → 403；属主 → 200；
   裁决员（reconciler）→ 200。
4. 成员库锁忙 → 503（不输出缺员 κ），且响应不泄露任何文献键/应答。
5. 导出 CSV 列与行形状：两人 × 两文献 = 4 条数据行，每人一行；列名是
   条目编号；CASP 的 overall 是逐条报告文本。
6. GRADE 映射表钉：``GRADE_ROB_MAP`` 每工具至少 3 档（输入档与输出档都
   ≥3），并逐条钉住 RoB2 High→serious、Critical→very_serious 等映射；
   ``GET …/appraisal/grade-input`` 逐文献返回档位且不写 GRADE 草稿。

运行：pytest build/tests/test_2_1_5_appraisal_agreement.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# 与既有 2.1.5 服务端测试同法：先设环境再导入 main。
fixture = tempfile.TemporaryDirectory(prefix="rf_215c_agreement_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient  # noqa: E402

from coscreen import appraisal as core  # noqa: E402
from coscreen import db as db_mod  # noqa: E402
from coscreen import tasks as tasks_mod  # noqa: E402
from coscreen.models import Article  # noqa: E402
from custom_backend import appraisal_agreement_api as agreement_api  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend.main import app, SCREENER_LIBRARY_UNAVAILABLE_MESSAGE  # noqa: E402

TOOL = "casp-qualitative"

#: 两篇文献 × 4 条目的已知应答矩阵（条目 1–4）：
#:         K1                    K2
#: owner   Y Y N Y               N N N Y
#: peer    Y Y N N               N N Y Y
#: 配对 8 格：YY 3 + NN 3 = 一致 6，YN 1 + NY 1 = 不一致 2；双方边际均为
#: Y=4/N=4，期望一致率 0.5 → Cohen's κ = (0.75 - 0.5) / (1 - 0.5) = 0.5。
OWNER_RESPONSES = {
    "K1": {"1": "Yes", "2": "Yes", "3": "No", "4": "Yes"},
    "K2": {"1": "No", "2": "No", "3": "No", "4": "Yes"},
}
PEER_RESPONSES = {
    "K1": {"1": "Yes", "2": "Yes", "3": "No", "4": "No"},
    "K2": {"1": "No", "2": "No", "3": "Yes", "4": "Yes"},
}

ARTICLES = [
    Article(zotero_key="K1", title="Alpha qualitative study"),
    Article(zotero_key="K2", title="Beta qualitative study"),
]


def _slug(username: str) -> str:
    return tasks_mod.slugify_screener(username)


class AppraisalAgreementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        cls.owner = auth_mod.create_user("r215c-owner@example.test",
                                         "R215C-Owner-2026!")
        cls.peer = auth_mod.create_user("r215c-peer@example.test",
                                        "R215C-Peer-2026!")
        cls.reconciler = auth_mod.create_user("r215c-arbiter@example.test",
                                              "R215C-Arbiter-2026!")
        cls.owner_auth = cls._auth(cls.owner)
        cls.peer_auth = cls._auth(cls.peer)
        cls.reconciler_auth = cls._auth(cls.reconciler)

    @classmethod
    def _auth(cls, user: dict) -> dict:
        token = auth_mod.create_access_token(user["id"], user["username"])
        return {"Authorization": "Bearer " + token}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    # ---- 夹具 ---------------------------------------------------------
    def setUp(self):
        response = self.client.post(
            "/api/tasks", json={"name": f"agreement-{self._testMethodName}"},
            headers=self.owner_auth)
        assert response.status_code in (200, 201), response.text
        self.task = response.json()["task_id"]
        self.base = f"/api/tasks/{self.task}/appraisal"
        for user, role in ((self.peer, "coder"), (self.reconciler, "reconciler")):
            invite = self.client.post(
                f"/api/tasks/{self.task}/collaboration/invite",
                json={"username": user["username"], "role": role},
                headers=self.owner_auth)
            assert invite.status_code == 201, invite.text
        self.ctx = app.state.review_workflow.context(
            self.task, {"user_id": self.owner["id"],
                        "username": self.owner["username"]})
        db_mod.upsert_articles(Path(self.ctx.owner_db), ARTICLES, [])

    def _put(self, auth: dict, article: str, responses: dict,
             *, comments: dict | None = None, note: str = "") -> None:
        body = {"tool": TOOL, "responses": responses, "note": note}
        if comments is not None:
            body["comments"] = comments
        response = self.client.put(f"{self.base}/{article}", json=body,
                                   headers=auth)
        assert response.status_code == 200, response.text

    def _seed_two_raters(self, *, comments: bool = False) -> None:
        owner_comments = {"4": "owner private reason"} if comments else {}
        peer_comments = {"4": "peer private reason"} if comments else {}
        for article, responses in OWNER_RESPONSES.items():
            self._put(self.owner_auth, article, responses,
                      comments=owner_comments,
                      note="owner private note" if comments else "")
        for article, responses in PEER_RESPONSES.items():
            self._put(self.peer_auth, article, responses,
                      comments=peer_comments,
                      note="peer private note" if comments else "")

    def _agreement(self, auth: dict | None = None):
        return self.client.get(f"{self.base}/agreement", params={"tool": TOOL},
                               headers=auth or self.owner_auth)

    # ---- 1. κ 数值钉 ---------------------------------------------------
    def test_pairwise_kappa_is_exactly_half(self):
        self._seed_two_raters()
        response = self._agreement()
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["available"] is True
        assert payload["tool_id"] == TOOL
        assert payload["rater_count"] == 2
        assert payload["n_pairs"] == 1

        pair = payload["pairwise"][0]
        assert pair["n_paired_cells"] == 8
        assert pair["agreement_rate"] == 0.75
        assert pair["cohen_kappa"] == 0.5
        assert pair["kappa_status"] == "available"
        assert payload["mean_cohen_kappa"] == 0.5

        raters = {row["rater_id"]: row for row in payload["raters"]}
        for rater_id in (_slug(self.owner["username"]),
                         _slug(self.peer["username"])):
            assert raters[rater_id]["articles_rated"] == 2
            assert raters[rater_id]["items_answered"] == 8

    def test_item_level_agreement_and_kappa(self):
        self._seed_two_raters()
        payload = self._agreement().json()
        items = {row["item_id"]: row for row in payload["items"]}
        # 条目 1/2 在两篇文献上都一致（YY、NN）→ 一致率 1、κ 1。
        assert items["1"]["n_paired_cells"] == 2
        assert items["1"]["agreement_rate"] == 1.0
        assert items["1"]["cohen_kappa"] == 1.0
        assert items["2"]["cohen_kappa"] == 1.0
        # 条目 3/4 各有一格分歧：一致率 0.5，κ = 0（边际仍各半）。
        assert items["3"]["n_disagreements"] == 1
        assert items["3"]["agreement_rate"] == 0.5
        assert items["3"]["cohen_kappa"] == 0.0
        assert items["4"]["n_disagreements"] == 1
        assert items["4"]["cohen_kappa"] == 0.0

    def test_disagreements_carry_no_free_text_reasons(self):
        """负钉：评论/说明（理由）不得出现在分歧清单或载荷任何位置。"""
        self._seed_two_raters(comments=True)
        response = self._agreement()
        assert response.status_code == 200, response.text
        payload = response.json()
        disputes = payload["disagreements"]
        assert len(disputes) == 2
        assert {(row["article_key"], row["item_id"]) for row in disputes} == {
            ("K1", "4"), ("K2", "3")}
        for row in disputes:
            assert set(row) == {"article_key", "item_id", "number", "responses"}
            assert "reason" not in row and "comment" not in row and "note" not in row
        text = json.dumps(payload)
        for forbidden in ("private reason", "private note"):
            assert forbidden not in text, f"一致率载荷泄露自由文本：{forbidden}"

    def test_missing_raters_is_reported_not_guessed(self):
        self._put(self.owner_auth, "K1", OWNER_RESPONSES["K1"])
        payload = self._agreement().json()
        assert payload["rater_count"] == 1
        assert payload["insufficient_raters"] is True
        assert payload["pairwise"] == []
        assert payload["mean_cohen_kappa"] is None

    def test_missing_or_unknown_tool_is_structured_400(self):
        cases = [
            (f"{self.base}/agreement", None, "appraisal.tool_required"),
            (f"{self.base}/export", None, "appraisal.tool_required"),
            (f"{self.base}/grade-input", None, "appraisal.tool_required"),
            (f"{self.base}/agreement", "not-a-tool", "appraisal.tool_unknown"),
            (f"{self.base}/export", "quadas-2", "appraisal.tool_link_only"),
        ]
        for url, tool, message_key in cases:
            params = {} if tool is None else {"tool": tool}
            response = self.client.get(url, params=params, headers=self.owner_auth)
            assert response.status_code == 400, (url, tool, response.text)
            assert response.json()["detail"]["message_key"] == message_key

    # ---- 2. 权限 -------------------------------------------------------
    def test_member_without_consensus_write_is_forbidden(self):
        self._seed_two_raters()
        response = self._agreement(self.peer_auth)
        assert response.status_code == 403, response.text

    def test_owner_and_adjudicator_can_read(self):
        self._seed_two_raters()
        assert self._agreement(self.owner_auth).status_code == 200
        assert self._agreement(self.reconciler_auth).status_code == 200

    # ---- 3. 锁忙 503 ---------------------------------------------------
    @staticmethod
    @contextmanager
    def _exclusive_lock(path: Path):
        """外部进程独占个人库：先切 rollback journal 再 BEGIN EXCLUSIVE。"""
        with closing(sqlite3.connect(path)) as setup:
            setup.execute("PRAGMA journal_mode=DELETE")
        holder = sqlite3.connect(path, timeout=1)
        holder.execute("BEGIN EXCLUSIVE")
        try:
            yield
        finally:
            holder.rollback()
            holder.close()

    def test_locked_member_library_returns_503_without_payload(self):
        self._seed_two_raters()
        locked = Path(self.ctx.owner_db).parent / "zzz-locked.db"
        with closing(sqlite3.connect(locked)) as conn, conn:
            core.ensure_appraisal_storage(conn)
        with mock.patch.object(agreement_api, "SCREENER_READ_TIMEOUT", 0.2), \
                self._exclusive_lock(locked):
            response = self._agreement()
        assert response.status_code == 503, response.text
        body = response.json()["detail"]
        assert body == SCREENER_LIBRARY_UNAVAILABLE_MESSAGE, body
        assert "K1" not in response.text and "Yes" not in response.text

    # ---- 4. 导出 CSV ---------------------------------------------------
    def test_export_csv_columns_and_one_row_per_evaluation(self):
        self._seed_two_raters()
        response = self.client.get(f"{self.base}/export", params={"tool": TOOL},
                                   headers=self.owner_auth)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/csv")
        text = response.content.decode("utf-8-sig")
        lines = [line for line in text.splitlines() if line]
        tool = core.REGISTRY[TOOL]
        item_numbers = [str(item["number"]) for item in core.iter_items(tool)]
        expected_header = ["zotero_key", "title", "tool", *item_numbers,
                           "overall", "rater", "updated_at"]
        assert lines[0] == ",".join(expected_header), lines[0]
        # 两人 × 两文献 = 4 条数据行（一行一评价人）。
        assert len(lines) == 1 + 4, len(lines)
        owner_slug = _slug(self.owner["username"])
        peer_slug = _slug(self.peer["username"])
        rows = [line.split(",") for line in lines[1:]]
        assert {row[0] for row in rows} == {"K1", "K2"}
        assert {row[-2] for row in rows} == {owner_slug, peer_slug}
        assert {row[2] for row in rows} == {TOOL}
        assert {row[1] for row in rows} == {"Alpha qualitative study",
                                            "Beta qualitative study"}
        owner_k1 = next(row for row in rows
                        if row[0] == "K1" and row[-2] == owner_slug)
        item_offset = 3
        assert owner_k1[item_offset:item_offset + 4] == ["Yes", "Yes", "No", "Yes"]
        assert owner_k1[item_offset + len(item_numbers)] == \
            "1=Yes; 2=Yes; 3=No; 4=Yes"

    # ---- 5. GRADE 映射表 ----------------------------------------------
    def test_grade_mapping_tables_expose_three_tiers_per_tool(self):
        for tool_id, table in core.GRADE_ROB_MAP.items():
            assert len(table) >= 3, tool_id
            levels = set(table.values())
            assert levels <= set(core.GRADE_RISK_LEVELS), tool_id
            assert len(levels) == 3, (tool_id, levels)

    def test_grade_mapping_known_values(self):
        cases = [
            ({ "mode": "domains", "judgement": "low" }, "low"),
            ({ "mode": "domains", "judgement": "some_concerns" }, "low"),
            ({ "mode": "domains", "judgement": "high" }, "serious"),
            ({ "mode": "domains", "judgement": "critical" }, "very_serious"),
        ]
        for aggregate, level in cases:
            assert core.study_rating_for_grade("rob2", aggregate)["risk_of_bias"] \
                == level, aggregate
        assert core.study_rating_for_grade(
            "amstar2", {"confidence": "high"})["risk_of_bias"] == "low"
        assert core.study_rating_for_grade(
            "amstar2", {"confidence": "moderate"})["risk_of_bias"] == "serious"
        assert core.study_rating_for_grade(
            "amstar2", {"confidence": "critically_low"})["risk_of_bias"] \
            == "very_serious"
        assert core.study_rating_for_grade(
            "casp-qualitative", {"overall_judgement": "moderate"})["risk_of_bias"] \
            == "serious"
        # MMAT 无官方总体评级：按逐条 Yes 占比分三档。
        assert core.study_rating_for_grade(
            "mmat", {"answer_distribution": {"Yes": 9, "No": 1}})["risk_of_bias"] \
            == "low"
        assert core.study_rating_for_grade(
            "mmat", {"answer_distribution": {"Yes": 5, "No": 5}})["risk_of_bias"] \
            == "serious"
        assert core.study_rating_for_grade(
            "mmat", {"answer_distribution": {"Yes": 1, "No": 9}})["risk_of_bias"] \
            == "very_serious"

    def test_grade_input_endpoint_lists_per_study_levels_read_only(self):
        self._seed_two_raters()
        response = self.client.get(f"{self.base}/grade-input",
                                   params={"tool": TOOL},
                                   headers=self.owner_auth)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["available"] is True
        assert payload["writes_grade_draft"] is False
        studies = {row["article_key"]: row for row in payload["studies"]}
        assert set(studies) == {"K1", "K2"}
        # K1：owner 3/4 Yes、peer 2/4 Yes → 两人都是 moderate → serious；
        # K2：owner 1/4 Yes（low→very_serious）、peer 2/4（serious）→ 取最严重。
        assert studies["K1"]["risk_of_bias"] == "serious"
        assert studies["K1"]["basis"] == "worst_of_raters"
        assert studies["K2"]["risk_of_bias"] == "very_serious"
        assert {row["article_key"] for row in payload["grade_ready"]} == {"K1", "K2"}
        # 只读：再次读取结果不变，且记录表未被 GRADE 端点改写。
        again = self.client.get(f"{self.base}/grade-input", params={"tool": TOOL},
                                headers=self.owner_auth).json()
        assert again["studies"] == payload["studies"]


class GradeMappingPureTests(unittest.TestCase):
    """不依赖 HTTP 的纯函数钉（GRADE 映射表每工具至少三档）。"""

    def test_every_registry_tool_has_a_mapping_branch(self):
        for tool_id, tool in core.REGISTRY.items():
            family = core._grade_tool_key(tool)
            assert family in core.GRADE_ROB_MAP, (tool_id, family)

    def test_unknown_tool_rejected(self):
        with self.assertRaises(ValueError):
            core.study_rating_for_grade("not-a-tool", {})

    def test_plain_overall_string_is_accepted(self):
        assert core.study_rating_for_grade("rob2", "high")["risk_of_bias"] \
            == "serious"
        assert core.study_rating_for_grade("amstar2", "high")["risk_of_bias"] \
            == "low"

    def test_missing_overall_is_unavailable_not_guessed(self):
        result = core.study_rating_for_grade("rob2", {})
        assert result["available"] is False
        assert result["risk_of_bias"] is None
        assert result["rule"] == "no_overall"


if __name__ == "__main__":
    unittest.main()
