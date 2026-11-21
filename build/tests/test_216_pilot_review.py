# -*- coding: utf-8 -*-
"""规格九「训练复盘」后端红钉（B1 载荷 / B2 导出 / B3 词条零新增）。

规格：``out/maintenance216/TRAINING_REVIEW_SPEC_20261121.md``（命名与方向
裁定已定死：误排除=本人 exclude×金标准 include；误纳入=本人 include×
金标准 exclude）。本文件钉：

1. 契约：金标准+作答+交卷 → my-review 载荷形状/聚合自洽（总数=两组并集
   +flags 去重）/tier 规则/题录字段齐（_catalog 同 my-attempt 白名单）；
2. 门槛：未交卷 409 ``attempt_not_submitted``（含 export）；练习尝试不改
   复盘（只认首次 #19）；参与者未交卷时 409 而非他人数据；任务外成员
   403 ``task_forbidden``；成员间数据互不可见；
3. 快照 vs 重算双路径一致性（删 ``pilot_results`` 行 → source=recomputed，
   逐条对照/分组/聚类/flags 完全一致）；
4. 导出：字节为合法 docx（PK zip 签名 + python-docx 重开）+关键标题存在；
5. ``organize_review`` 纯函数钉（方向定义/理由规范化聚类/flag 定界/
   tier 优先序/并集不变量）。

运行：
    python -m pytest build/tests/test_216_pilot_review.py -q
"""
from __future__ import annotations

import io
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# 与既有服务端夹具同法：先设环境再导入 main。
fixture = tempfile.TemporaryDirectory(prefix="rf_spec9_pilot_review_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient  # noqa: E402

from coscreen import db as db_mod  # noqa: E402
from coscreen import pilot_screening as pilot  # noqa: E402
from coscreen import review_workflow as store  # noqa: E402
from coscreen import tasks as tasks_mod  # noqa: E402
from coscreen.models import Article  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend.main import DATA_DIR, app  # noqa: E402

USERS = ("spec9owner@example.test", "spec9member@example.test",
         "spec9member2@example.test", "spec9outsider@example.test")

KEYS = [f"K{i:02d}" for i in range(1, 13)]

#: 契约钉数据：金标准 decision →（成员作答 decision, 理由）。
#: K01–K03 = 同一理由三种书写（规范化后同串）→ 误排除聚类≥3（pattern）；
#: K04–K05 = 误纳入聚类 2 条（isolated）；K06/K07 = 判定一致但理由命中
#: 规范性（不确定措辞/空理由 → tier=flag）；K08 = 误排除+不确定措辞
#: （方向优先 → isolated，同时进 flags）；K09–K12 = 普通一致（不在复盘）。
GOLD = {
    "K01": "include", "K02": "include", "K03": "include",
    "K04": "exclude", "K05": "exclude", "K06": "exclude",
    "K07": "exclude", "K08": "include", "K09": "include",
    "K10": "exclude", "K11": "include", "K12": "exclude",
}
ANSWERS = {
    "K01": ("exclude", " Not  In Scope "),
    "K02": ("exclude", "not in scope"),
    "K03": ("exclude", "NOT IN SCOPE"),
    "K04": ("include", "主题相关"),
    "K05": ("include", "主题相关"),
    "K06": ("exclude", "不确定"),
    "K07": ("exclude", ""),
    "K08": ("exclude", "Unclear 设计"),
    "K09": ("include", "ok"),
    "K10": ("exclude", "样本不符"),
    "K11": ("include", "ok"),
    "K12": ("exclude", "设计不符"),
}


class ReviewOrganizePins(unittest.TestCase):
    """organize_review 纯函数钉（零 IO）。"""

    @staticmethod
    def _item(key, gold, trainee, reason="r", gold_reason="g"):
        return {"zotero_key": key, "gold_decision": gold,
                "gold_reason": gold_reason, "trainee_decision": trainee,
                "trainee_reason": reason}

    def test_direction_definitions_are_fixed(self):
        review = pilot.organize_review([
            self._item("A", "include", "exclude"),   # 误排除
            self._item("B", "exclude", "include"),   # 误纳入
            self._item("C", "include", "include"),   # 一致 → 不在范围
            self._item("D", "exclude", "exclude"),   # 一致 → 不在范围
            self._item("E", "maybe", "include"),     # maybe 相关：无方向 → 不在范围
        ])
        self.assertEqual(review["groups"]["false_exclude"], ["A"])
        self.assertEqual(review["groups"]["false_include"], ["B"])
        self.assertEqual(review["counts"]["total"], 2)
        self.assertEqual([item["zotero_key"] for item in review["items"]],
                         ["A", "B"])

    def test_reason_clustering_normalization_and_pattern_tier(self):
        review = pilot.organize_review([
            self._item("A", "include", "exclude", " Not  In Scope "),
            self._item("B", "include", "exclude", "not in scope"),
            self._item("C", "include", "exclude", "NOT IN SCOPE"),
            self._item("D", "include", "exclude", "别的理由"),
        ])
        self.assertEqual(len(review["patterns"]), 2)
        top = review["patterns"][0]
        self.assertEqual((top["reason"], top["direction"], top["count"]),
                         ("not in scope", "false_exclude", 3))
        self.assertEqual(top["keys"], ["A", "B", "C"])
        tiers = {item["zotero_key"]: item["tier"] for item in review["items"]}
        self.assertEqual(tiers,
                         {"A": "pattern", "B": "pattern", "C": "pattern",
                          "D": "isolated"})

    def test_flag_boundary_and_precedence(self):
        review = pilot.organize_review([
            self._item("A", "exclude", "exclude", "不确定"),      # 命中：不确定
            self._item("B", "exclude", "exclude", ""),            # 命中：空理由
            self._item("C", "exclude", "exclude", "UNCLEAR 情形"),  # 命中：大小写不敏感
            self._item("D", "exclude", "exclude", "样本量不足"),   # 不命中
            self._item("E", "include", "include", "不确定"),      # 非 exclude → 不命中
            self._item("F", "include", "exclude", "不知道"),      # 误排除+命中 → 方向优先
        ])
        flagged = {flag["zotero_key"]: flag for flag in review["flags"]}
        self.assertEqual(sorted(flagged), ["A", "B", "C", "F"])
        self.assertEqual(flagged["B"]["kind"], "empty_reason")
        self.assertEqual(flagged["A"]["kind"], "uncertain_reason")
        self.assertIsNone(flagged["A"]["direction"])
        self.assertEqual(flagged["F"]["direction"], "false_exclude")
        tiers = {item["zotero_key"]: item["tier"] for item in review["items"]}
        # tier 优先序：方向分组内（pattern/isolated）> 仅规范性（flag）。
        self.assertEqual(tiers["A"], "flag")
        self.assertEqual(tiers["B"], "flag")
        self.assertEqual(tiers["F"], "isolated")

    def test_union_invariant(self):
        review = pilot.organize_review([
            self._item("A", "include", "exclude", "x"),
            self._item("B", "exclude", "include", "x"),
            self._item("C", "exclude", "exclude", "不知道"),  # 与差异无交集
            self._item("D", "include", "exclude", "不知道"),  # 差异∩规范性 → 去重
        ])
        union = set(review["groups"]["false_exclude"])
        union |= set(review["groups"]["false_include"])
        union |= {flag["zotero_key"] for flag in review["flags"]}
        self.assertEqual(review["counts"]["total"], len(union))
        self.assertEqual(review["counts"]["total"], 4)  # A/B/C/D 各一次
        # 聚类 count 之和 == 两方向分组条数之和。
        self.assertEqual(sum(p["count"] for p in review["patterns"]),
                         review["counts"]["false_exclude"]
                         + review["counts"]["false_include"])


class ReviewApiBase(unittest.TestCase):
    """HTTP 夹具（server 模式 + 真实协作库；pilot 端点不触团队写通道）。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.users = []
        for name in USERS:
            try:
                user = auth_mod.create_user(name, "Spec9-Review-Test-2026")
            except auth_mod.AuthError:
                user = auth_mod.get_user(name)
            cls.users.append({
                "user_id": user["id"],
                "username": user["username"],
                "token": auth_mod.create_access_token(user["id"],
                                                      user["username"]),
            })

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    def auth(self, index: int = 0) -> dict:
        return {"Authorization": "Bearer " + self.users[index]["token"]}

    def new_task(self, invite: int = 1) -> None:
        response = self.client.post(
            "/api/tasks", json={"name": self._testMethodName},
            headers=self.auth())
        self.assertEqual(response.status_code, 201, response.text)
        self.task = response.json()["task_id"]
        self.info = tasks_mod.find_task(self.task, DATA_DIR)
        self.owner_db = self.info.dir / (
            tasks_mod.slugify_screener(self.users[0]["username"]) + ".db")
        self.collab_db = self.info.dir / "collaboration.db"
        db_mod.init_db(self.owner_db)
        store.migrate(self.owner_db)
        db_mod.upsert_articles(self.owner_db, [
            Article(zotero_key=key, title=f"Study {key}",
                    abstract=f"Abstract {key}", journal="J Spec9",
                    year=2021, doi=f"10.1000/{key}",
                    authors="Author, A.; Coauthor, B.")
            for key in KEYS], [])
        for index in range(1, max(invite, 0) + 1):
            invited = self.client.post(
                f"/api/tasks/{self.task}/collaboration/invite",
                json={"username": self.users[index]["username"],
                      "role": "coder"},
                headers=self.auth())
            self.assertEqual(invited.status_code, 201, invited.text)

    def post(self, path: str, body: dict | None = None, *, index: int = 0):
        return self.client.post(f"/api/tasks/{self.task}{path}",
                                json=body or {}, headers=self.auth(index))

    def get(self, path: str, *, index: int = 0):
        return self.client.get(f"/api/tasks/{self.task}{path}",
                               headers=self.auth(index))

    def ready_set(self) -> int:
        response = self.post("/pilot/sets", {
            "sampling": "curated", "curated_keys": KEYS, "size": len(KEYS),
            "note": "spec9 review"})
        self.assertEqual(response.status_code, 201, response.text)
        set_id = response.json()["set"]["id"]
        for key, decision in GOLD.items():
            saved = self.post(f"/pilot/sets/{set_id}/gold", {
                "zotero_key": key, "decision": decision,
                "reason": f"gold {decision}"})
            self.assertEqual(saved.status_code, 200, saved.text)
        published = self.post(f"/pilot/sets/{set_id}/publish")
        self.assertEqual(published.status_code, 200, published.text)
        return set_id

    def submit_answers(self, set_id: int, answers: dict, *, index: int = 1):
        started = self.post(f"/pilot/sets/{set_id}/attempts", {}, index=index)
        self.assertEqual(started.status_code, 200, started.text)
        for key, (decision, reason) in answers.items():
            saved = self.post(f"/pilot/sets/{set_id}/my-attempt/item", {
                "zotero_key": key, "decision": decision, "reason": reason},
                index=index)
            self.assertEqual(saved.status_code, 200, saved.text)
        submitted = self.post(f"/pilot/sets/{set_id}/my-attempt/submit", {},
                              index=index)
        self.assertEqual(submitted.status_code, 200, submitted.text)
        return submitted.json()["result"]

    def my_review(self, set_id: int, *, index: int = 1):
        response = self.get(f"/pilot/sets/{set_id}/my-review", index=index)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["review"]


class ReviewContractPins(ReviewApiBase):
    """B1 契约：载荷形状 + 聚合自洽 + tier 规则 + 题录字段。"""

    def test_payload_shape_aggregation_and_catalog(self):
        self.new_task()
        set_id = self.ready_set()
        result = self.submit_answers(set_id, ANSWERS)
        self.assertTrue(result["counts_for_clearance"])
        review = self.my_review(set_id)

        # 顶层形状。
        self.assertEqual(sorted(review),
                         ["attempt", "counts", "flags", "groups", "items",
                          "patterns", "set", "source", "verdict"])
        self.assertEqual(review["source"], "snapshot")
        self.assertEqual(review["attempt"]["id"], result["attempt_id"])
        self.assertEqual(review["set"]["id"], set_id)
        self.assertIn(review["set"]["status"], ("published", "archived"))

        # 分组（方向定义以此为准）。
        counts = review["counts"]
        self.assertEqual(review["groups"]["false_exclude"],
                         ["K01", "K02", "K03", "K08"])
        self.assertEqual(review["groups"]["false_include"],
                         ["K04", "K05"])
        self.assertEqual(counts["false_exclude"], 4)
        self.assertEqual(counts["false_include"], 2)
        self.assertEqual(counts["flags"], 3)

        # 聚合自洽：总数 = 两组并集 + flags 去重。
        union = set(review["groups"]["false_exclude"])
        union |= set(review["groups"]["false_include"])
        union |= {flag["zotero_key"] for flag in review["flags"]}
        self.assertEqual(counts["total"], len(union))
        self.assertEqual(counts["total"], 8)  # K01..K08 去重（K08 双命中）

        # 偏差模式汇总：规范化聚类（三种书写同串）+ count 降序。
        self.assertEqual(counts["patterns"], 3)
        self.assertEqual(review["patterns"][0],
                         {"reason": "not in scope", "direction":
                          "false_exclude", "count": 3,
                          "keys": ["K01", "K02", "K03"]})
        self.assertEqual([p["count"] for p in review["patterns"]],
                         [3, 2, 1])

        # 标记规范性：K06 不确定措辞（一致→无方向）；K07 空理由；
        # K08 误排除+不确定措辞（方向保留）。
        flags = {flag["zotero_key"]: flag for flag in review["flags"]}
        self.assertEqual(sorted(flags), ["K06", "K07", "K08"])
        self.assertEqual(flags["K06"]["kind"], "uncertain_reason")
        self.assertIsNone(flags["K06"]["direction"])
        self.assertEqual(flags["K07"]["kind"], "empty_reason")
        self.assertEqual(flags["K08"]["kind"], "uncertain_reason")
        self.assertEqual(flags["K08"]["direction"], "false_exclude")

        # 逐条：tier 规则 + 双方判定/理由 + 题录字段（my-attempt 白名单）。
        items = {item["zotero_key"]: item for item in review["items"]}
        self.assertEqual(sorted(items),
                         ["K01", "K02", "K03", "K04", "K05", "K06",
                          "K07", "K08"])
        expected_tier = {"K01": "pattern", "K02": "pattern",
                         "K03": "pattern", "K04": "isolated",
                         "K05": "isolated", "K06": "flag",
                         "K07": "flag", "K08": "isolated"}
        for key, item in items.items():
            self.assertEqual(item["tier"], expected_tier[key], key)
            self.assertIn("title", item)
            self.assertEqual(item["title"], f"Study {key}")
            self.assertEqual(item["journal"], "J Spec9")
            self.assertEqual(item["year"], 2021)
            self.assertEqual(item["doi"], f"10.1000/{key}")
            self.assertEqual(item["authors"], "Author, A.; Coauthor, B.")
            self.assertEqual(item["abstract"], f"Abstract {key}")
            self.assertEqual(item["item_type"], "")
        self.assertEqual(items["K01"]["trainee_reason"], " Not  In Scope ")
        self.assertEqual(items["K01"]["gold_reason"], "gold include")
        self.assertEqual(items["K01"]["trainee_decision"], "exclude")
        self.assertEqual(items["K01"]["gold_decision"], "include")
        self.assertEqual(items["K08"]["pattern_count"], 1)
        self.assertEqual(items["K01"]["pattern_count"], 3)

    def test_two_path_snapshot_vs_recomputed_consistency(self):
        self.new_task()
        set_id = self.ready_set()
        self.submit_answers(set_id, ANSWERS)
        snapshot_review = self.my_review(set_id)
        self.assertEqual(snapshot_review["source"], "snapshot")

        # 制造异常：抹掉快照行（外部直写库口径）→ 端点必须走 evaluate 同源
        # 重算路径且与快照结果完全一致。
        attempt_id = snapshot_review["attempt"]["id"]
        with closing(sqlite3.connect(self.collab_db)) as conn, conn:
            deleted = conn.execute(
                "DELETE FROM pilot_results WHERE attempt_id = ?",
                (attempt_id,)).rowcount
        self.assertEqual(deleted, 1)

        recomputed = self.my_review(set_id)
        self.assertEqual(recomputed["source"], "recomputed")
        compare_fields = ("trainee_decision", "trainee_reason",
                          "gold_decision", "gold_reason", "direction",
                          "flag", "tier", "pattern_count")
        before = {item["zotero_key"]: item
                  for item in snapshot_review["items"]}
        after = {item["zotero_key"]: item for item in recomputed["items"]}
        self.assertEqual(sorted(before), sorted(after))
        for key, item in before.items():
            for field in compare_fields:
                self.assertEqual(item[field], after[key][field], key)
        self.assertEqual(recomputed["groups"], snapshot_review["groups"])
        self.assertEqual(recomputed["patterns"], snapshot_review["patterns"])
        self.assertEqual(recomputed["flags"], snapshot_review["flags"])
        self.assertEqual(recomputed["counts"], snapshot_review["counts"])


class ReviewGatePins(ReviewApiBase):
    """B1 门槛与可见性（沿用 my-result / 既有权限族口径）。"""

    def test_before_submit_is_409_for_payload_and_export(self):
        self.new_task()
        set_id = self.ready_set()
        # 完全没开过尝试。
        for path in ("my-review", "my-review/export"):
            response = self.get(f"/pilot/sets/{set_id}/{path}", index=1)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["detail"]["error_code"],
                             "attempt_not_submitted")
            self.assertEqual(response.json()["detail"]["message_key"],
                             "pilot.attempt_not_submitted")
        # 在途未交卷同样 409。
        started = self.post(f"/pilot/sets/{set_id}/attempts", {}, index=1)
        self.assertEqual(started.status_code, 200, started.text)
        response = self.get(f"/pilot/sets/{set_id}/my-review", index=1)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["error_code"],
                         "attempt_not_submitted")

    def test_practice_attempt_does_not_change_review(self):
        self.new_task()
        set_id = self.ready_set()
        first = self.submit_answers(set_id, ANSWERS)
        self.assertTrue(first["counts_for_clearance"])
        review_before = self.my_review(set_id)

        # 练习重答（全 include，与首考完全不同）——复盘只认首次。
        practice = self.submit_answers(
            set_id, {key: ("include", "practice retake") for key in KEYS})
        self.assertFalse(practice["counts_for_clearance"])
        self.assertTrue(practice["practice"])

        review_after = self.my_review(set_id)
        self.assertEqual(review_after["items"], review_before["items"])
        self.assertEqual(review_after["groups"], review_before["groups"])
        self.assertEqual(review_after["patterns"],
                         review_before["patterns"])
        self.assertEqual(review_after["flags"], review_before["flags"])
        self.assertEqual(review_after["attempt"]["id"],
                         review_before["attempt"]["id"])

    def test_member_isolation_and_outsider_forbidden(self):
        self.new_task(invite=2)
        set_id = self.ready_set()
        self.submit_answers(set_id, ANSWERS, index=1)
        owner_review = self.my_review(set_id, index=1)
        self.assertEqual(owner_review["counts"]["total"], 8)

        # 任务外成员：403 task_forbidden（沿用既有族）。
        outsider = self.get(f"/pilot/sets/{set_id}/my-review", index=3)
        self.assertEqual(outsider.status_code, 403, outsider.text)
        self.assertEqual(outsider.json()["detail"]["error_code"], "task_forbidden")

        # 另一参与者未交卷：409（拿不到也绝不包含首个成员的数据）。
        member2 = self.get(f"/pilot/sets/{set_id}/my-review", index=2)
        self.assertEqual(member2.status_code, 409, member2.text)
        self.assertEqual(member2.json()["detail"]["error_code"],
                         "attempt_not_submitted")

        # member2 交卷后只见本人复盘（照抄金标准 → 空复盘）。
        self.submit_answers(
            set_id, {key: (GOLD[key], "agree") for key in KEYS}, index=2)
        member2_review = self.my_review(set_id, index=2)
        self.assertEqual(member2_review["counts"]["total"], 0)
        self.assertEqual(member2_review["patterns"], [])
        self.assertNotEqual(member2_review["attempt"]["id"],
                            owner_review["attempt"]["id"])


class ReviewExportPins(ReviewApiBase):
    """B2 导出：合法 docx + 关键标题。"""

    def test_export_is_valid_docx_with_expected_headings(self):
        import io as io_mod

        from docx import Document
        self.new_task()
        set_id = self.ready_set()
        self.submit_answers(set_id, ANSWERS)
        response = self.get(f"/pilot/sets/{set_id}/my-review/export",
                            index=1)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument"
            ".wordprocessingml.document"))
        self.assertIn("pilot-review-", response.headers.get(
            "content-disposition", ""))
        body = response.content
        # zip 签名 + python-docx 能重开。
        self.assertEqual(body[:2], b"PK")
        document = Document(io_mod.BytesIO(body))
        headings = {paragraph.text for paragraph in document.paragraphs
                    if paragraph.style.name.startswith(("Heading", "Title"))}
        self.assertIn("筛选训练复盘报告", headings)
        for expected in ("使用说明", "偏差模式汇总", "差异总表",
                         "误排除（过严）（4 条）", "误纳入（过松）（2 条）",
                         "标记规范性提示"):
            self.assertIn(expected, headings)
        # 全文不出现他人标识（成员互不可见的导出面）。
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        for table in document.tables:
            for row in table.rows:
                for cell in row.cells:
                    text += "\n" + cell.text
        self.assertNotIn("spec9member2", text)
        self.assertNotIn("spec9outsider", text)

    def test_export_before_submit_is_409(self):
        self.new_task()
        set_id = self.ready_set()
        response = self.get(f"/pilot/sets/{set_id}/my-review/export",
                            index=1)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["error_code"],
                         "attempt_not_submitted")


if __name__ == "__main__":
    unittest.main()
