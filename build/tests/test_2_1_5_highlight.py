# -*- coding: utf-8 -*-
"""2.1.5 Wave D2 · 摘要关键词高亮后端红钉（专属新文件，撤实现必红）。

覆盖（对应泳道 D2 验收条款）：

1. 纯计算 ``coscreen.highlight.compute_highlights(text, terms)``：
   大小写不敏感、英文词边界（``cat`` 不命中 ``category``）、中文直接子串、
   短语优先于子词（``meta-analysis`` 命中时不重复标 ``analysis``）、
   重叠取最长、空组/空文本、范围升序且互不重叠。
2. 偏移口径钉：``text[start:end].lower() == term.lower()``（字符/码点偏移，
   Python ``str`` 切片口径）；同时返回的 ``byte_start/byte_end`` 必须能对
   UTF-8 字节串切片复现同一段文本（中文时字节区间宽于字符区间）。
3. 设置存储往返：每成员独立、可加表、PUT 幂等（同一请求体重复提交得到
   完全相同的 set_id 与内容）、激活组切换。
4. 端点权限：非任务成员 GET/PUT/compute 一律 403。
5. compute 两种输入形态：``{text}`` 与 ``{article_key, field}``（服务端从
   题录权威库取 title/abstract 文本后计算），并校验错误降级（未知字段/
   未知文献/缺输入/超长文本都是结构化 4xx，绝不 500）。

注意：本文件在实现落地前应当整体红（模块不存在 → 导入/收集失败），
这正是「红钉先行」的证据。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

fixture = tempfile.TemporaryDirectory(prefix="rf_215d_highlight_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient  # noqa: E402

from coscreen import db as db_mod  # noqa: E402
from coscreen import highlight as highlight_core  # noqa: E402
from coscreen import tasks as tasks_mod  # noqa: E402
from coscreen.models import Article  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend.main import app, DATA_DIR  # noqa: E402

TITLE = "A Meta-Analysis of Randomized Trials 随机对照试验"
ABSTRACT = ("This randomized study covers meta-analysis methods. "
            "Category-level results and concatenate checks. 随机分组。")


def _slice(text: str, item: dict) -> str:
    return text[item["start"]:item["end"]]


# ---------------------------------------------------------------------------
# 1. 纯计算
# ---------------------------------------------------------------------------

class HighlightComputeTests(unittest.TestCase):
    def test_case_insensitive_ascii(self):
        text = "RANDOMIZED trial; Randomized trial; randomized."
        ranges = highlight_core.compute_highlights(text, ["randomized"])
        assert len(ranges) == 3, ranges
        for item in ranges:
            assert _slice(text, item).lower() == "randomized"

    def test_english_word_boundary(self):
        """英文按词边界：cat 不命中 category / concatenate。"""
        text = "category concatenate cat."
        ranges = highlight_core.compute_highlights(text, ["cat"])
        assert len(ranges) == 1, ranges
        assert _slice(text, ranges[0]) == "cat"

    def test_chinese_direct_substring(self):
        """中文直接子串：随机 命中 随机对照 / 随机分组。"""
        text = "这是一项随机对照试验，采用随机分组。"
        ranges = highlight_core.compute_highlights(text, ["随机"])
        assert len(ranges) == 2, ranges
        assert all(_slice(text, item) == "随机" for item in ranges)

    def test_phrase_beats_subterm(self):
        """短语优先：meta-analysis 命中时不再标 analysis。"""
        text = "We ran a meta-analysis of trials."
        ranges = highlight_core.compute_highlights(
            text, ["meta-analysis", "analysis"])
        assert len(ranges) == 1, ranges
        assert _slice(text, ranges[0]) == "meta-analysis"
        assert ranges[0]["term_id"] == 0

    def test_overlap_keeps_longest(self):
        """重叠取最长：研究设计 [0,4) 覆盖 究设 [1,3)。"""
        text = "研究设计"
        ranges = highlight_core.compute_highlights(text, ["研究设计", "究设"])
        assert len(ranges) == 1, ranges
        assert (ranges[0]["start"], ranges[0]["end"]) == (0, 4)
        assert ranges[0]["term_id"] == 0

    def test_equal_length_overlap_prefers_earlier_start(self):
        """等长重叠：研究设 [0,3) 与 究设计 [1,4) 取起点更早者。"""
        text = "研究设计"
        ranges = highlight_core.compute_highlights(text, ["研究设", "究设计"])
        assert len(ranges) == 1, ranges
        assert (ranges[0]["start"], ranges[0]["end"]) == (0, 3)
        assert ranges[0]["term_id"] == 0

    def test_empty_terms_and_text(self):
        assert highlight_core.compute_highlights("abc", []) == []
        assert highlight_core.compute_highlights("abc", ["", "   "]) == []
        assert highlight_core.compute_highlights("", ["abc"]) == []

    def test_ranges_sorted_and_non_overlapping(self):
        text = "cat dog catdog cat"
        ranges = highlight_core.compute_highlights(text, ["cat", "dog"])
        starts = [item["start"] for item in ranges]
        assert starts == sorted(starts)
        for prev, nxt in zip(ranges, ranges[1:]):
            assert prev["end"] <= nxt["start"], ranges

    def test_offsets_slice_and_byte_offsets(self):
        """字符偏移切片 == 词；UTF-8 字节偏移切片同样 == 词（中文更宽）。"""
        text = "前言随机对照试验 Randomized 结论"
        ranges = highlight_core.compute_highlights(text, ["随机", "randomized"])
        assert len(ranges) == 2, ranges
        expected = {0: "随机", 1: "randomized"}
        encoded = text.encode("utf-8")
        for item in ranges:
            term = expected[item["term_id"]]
            assert _slice(text, item).lower() == term.lower()
            assert encoded[item["byte_start"]:item["byte_end"]].decode(
                "utf-8").lower() == term.lower()
        chinese = next(item for item in ranges if item["term_id"] == 0)
        # 3 个 UTF-8 字节 vs 1 个字符：字节区间必须更宽
        assert chinese["byte_end"] - chinese["byte_start"] > (
            chinese["end"] - chinese["start"])

    def test_english_adjacent_to_cjk_still_matches(self):
        text = "随机randomized对照"
        ranges = highlight_core.compute_highlights(text, ["randomized"])
        assert len(ranges) == 1, ranges
        assert _slice(text, ranges[0]) == "randomized"

    def test_duplicate_terms_do_not_duplicate_ranges(self):
        text = "cat cat"
        ranges = highlight_core.compute_highlights(text, ["cat", "CAT"])
        assert len(ranges) == 2, ranges
        assert {item["term_id"] for item in ranges} == {0}

    def test_astral_codepoint_offsets(self):
        """增补平面字符：start/end 是码点偏移（≠ JS UTF-16 下标），byte_* 是 UTF-8。"""
        text = "😀cat😀cat"
        ranges = highlight_core.compute_highlights(text, ["cat"])
        assert len(ranges) == 2, ranges
        for item in ranges:
            assert text[item["start"]:item["end"]] == "cat"
        # 第一个 emoji 占 1 个码点 / 2 个 UTF-16 码元 / 4 个 UTF-8 字节
        assert ranges[0]["start"] == 1
        assert ranges[0]["byte_start"] == 4
        assert ranges[1]["start"] == 5
        assert ranges[1]["byte_start"] == 11


# ---------------------------------------------------------------------------
# 2. 设置存储 + 端点
# ---------------------------------------------------------------------------

class HighlightApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.users = []
        for i in range(3):
            # 用户名与同波 import_ai_assist 测试隔离（W2/W4 卫生同型问题）：
            # 两文件在导入期各自设 COBOOKSHELF_DATA_DIR，但 custom_backend.main
            # 的 DATA_DIR 在首次导入即固化，同进程组合跑时共用首个临时目录，
            # 同名用户会触发 auth 的筛选员路径冲突保护。前缀区分即可。
            user = auth_mod.create_user(f"r215dhl{i}@example.test",
                                        "R215D-Highlight-Test-2026")
            cls.users.append({
                "user_id": user["id"],
                "username": user["username"],
                "token": auth_mod.create_access_token(user["id"], user["username"]),
            })

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    def auth(self, index=0):
        return {"Authorization": "Bearer " + self.users[index]["token"]}

    def setUp(self):
        self.task = self.client.post(
            "/api/tasks", json={"name": self._testMethodName},
            headers=self.auth()).json()["task_id"]
        self.info = tasks_mod.find_task(self.task, DATA_DIR)
        response = self.client.post(
            f"/api/tasks/{self.task}/collaboration/invite",
            json={"username": self.users[1]["username"], "role": "coder"},
            headers=self.auth())
        self.assertEqual(response.status_code, 201, response.text)
        # 题录写入属主（member 0）个人库——compute 的 article_key 路径必须
        # 能从属主库取到正文，协作者（member 1）也算得出高亮。
        db = self._member_db(0)
        db_mod.init_db(db)
        db_mod.upsert_articles(db, [
            Article(zotero_key="HK1", title=TITLE, abstract=ABSTRACT),
        ], [])

    def _member_db(self, index: int) -> Path:
        slug = tasks_mod.slugify_screener(self.users[index]["username"])
        return self.info.dir / f"{slug}.db"

    def _sets_url(self):
        return f"/api/tasks/{self.task}/highlight/sets"

    def _compute_url(self):
        return f"/api/tasks/{self.task}/highlight/compute"

    def _put_sets(self, index, sets, active=None):
        return self.client.put(
            self._sets_url(), json={"sets": sets, "active_set_id": active},
            headers=self.auth(index))

    def _body(self):
        return {
            "sets": [
                {"name": "RCT 关键词",
                 "terms": ["randomized", "Randomized", "随机"]},
                {"name": "方法学", "terms": ["meta-analysis", "analysis"]},
            ],
            "active_set_id": None,
        }

    # ---- 设置往返 / 幂等 / 成员隔离 ------------------------------------

    def test_default_empty_sets(self):
        response = self.client.get(self._sets_url(), headers=self.auth(0))
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        assert payload["available"] is True
        assert payload["sets"] == []
        assert payload["active_set_id"] is None

    def test_sets_roundtrip_and_activation(self):
        body = self._body()
        response = self._put_sets(0, body["sets"], body["active_set_id"])
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()
        assert len(saved["sets"]) == 2, saved
        assert saved["active_set_id"] is None
        ids = [item["set_id"] for item in saved["sets"]]
        assert all(ids)
        assert saved["sets"][0]["terms"][0] == "randomized"

        active = ids[1]
        response = self._put_sets(0, saved["sets"], active)
        self.assertEqual(response.status_code, 200, response.text)

        fetched = self.client.get(self._sets_url(), headers=self.auth(0)).json()
        assert fetched["active_set_id"] == active, fetched
        assert [item["set_id"] for item in fetched["sets"]] == ids
        assert fetched["sets"][0]["terms"] == ["randomized", "Randomized", "随机"]

    def test_put_is_idempotent(self):
        body = self._body()
        first = self._put_sets(0, body["sets"], body["active_set_id"]).json()
        second = self._put_sets(0, body["sets"], body["active_set_id"]).json()
        assert first["sets"] == second["sets"], (first, second)
        assert first["active_set_id"] == second["active_set_id"]

    def test_sets_are_per_member(self):
        self._put_sets(0, self._body()["sets"], None)
        member_one = self.client.get(self._sets_url(), headers=self.auth(1)).json()
        assert member_one["sets"] == [], member_one
        assert member_one["active_set_id"] is None

    # ---- 校验 -----------------------------------------------------------

    def test_unknown_active_set_rejected(self):
        response = self._put_sets(0, self._body()["sets"], "hs_does_not_exist")
        self.assertEqual(response.status_code, 400, response.text)
        assert response.json()["detail"]["error_code"] == "highlight_active_set_unknown"

    def test_empty_group_allowed_but_invalid_shapes_rejected(self):
        response = self._put_sets(0, [{"name": "空组", "terms": []}], None)
        self.assertEqual(response.status_code, 200, response.text)

        too_many = [{"name": f"组{i}", "terms": ["x"]}
                    for i in range(highlight_core.MAX_SETS + 1)]
        response = self._put_sets(0, too_many, None)
        self.assertEqual(response.status_code, 400, response.text)
        assert response.json()["detail"]["error_code"] == "highlight_sets_too_many"

        response = self._put_sets(0, [{"name": "", "terms": ["x"]}], None)
        self.assertEqual(response.status_code, 400, response.text)

        response = self._put_sets(0, [{"name": "组", "terms": [""]}], None)
        self.assertEqual(response.status_code, 400, response.text)

        long_term = "x" * (highlight_core.MAX_TERM_LENGTH + 1)
        response = self._put_sets(0, [{"name": "组", "terms": [long_term]}], None)
        self.assertEqual(response.status_code, 400, response.text)

        # 孤立代理项会让 sqlite 绑定/UTF-8 回显失败：必须结构化 400 而非 500。
        # 走原始 JSON 字节，避免测试客户端自身拒绝编码代理项。
        raw = json.dumps(
            {"sets": [{"name": "组", "terms": ["\ud800"]}],
             "active_set_id": None}).encode("utf-8")
        response = self.client.put(
            self._sets_url(), content=raw,
            headers={**self.auth(0), "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 400, response.text)

        response = self._put_sets(0, [
            {"set_id": "dup", "name": "A", "terms": []},
            {"set_id": "dup", "name": "B", "terms": []},
        ], None)
        self.assertEqual(response.status_code, 400, response.text)

    # ---- compute --------------------------------------------------------

    def _activate_first_set(self, index, terms):
        response = self._put_sets(index, [{"name": "组", "terms": terms}], None)
        self.assertEqual(response.status_code, 200, response.text)
        set_id = response.json()["sets"][0]["set_id"]
        response = self._put_sets(
            index, response.json()["sets"], set_id)
        self.assertEqual(response.status_code, 200, response.text)
        return set_id

    def test_compute_text_uses_active_set(self):
        set_id = self._activate_first_set(0, ["Meta-Analysis", "analysis", "随机"])
        text = "A Meta-Analysis of randomized 随机对照"
        response = self.client.post(
            self._compute_url(), json={"text": text}, headers=self.auth(0))
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        assert payload["set_id"] == set_id
        assert payload["source"] == "text"
        assert payload["text"] == text
        assert [item["term"] for item in payload["terms"]] == [
            "Meta-Analysis", "analysis", "随机"]
        assert payload["terms"][0]["term_id"] == 0
        ranges = payload["ranges"]
        assert len(ranges) == 2, ranges  # meta-analysis + 随机，analysis 被短语覆盖
        for item in ranges:
            assert _slice(text, item).lower() == payload["terms"][
                item["term_id"]]["term"].lower()
            assert text.encode("utf-8")[item["byte_start"]:item["byte_end"]].decode(
                "utf-8").lower() == payload["terms"][item["term_id"]]["term"].lower()

    def test_compute_without_active_set_is_empty(self):
        response = self._put_sets(
            0, [{"name": "组", "terms": ["randomized"]}], None)
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.post(
            self._compute_url(), json={"text": "randomized trial"},
            headers=self.auth(0))
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        assert payload["set_id"] is None
        assert payload["terms"] == []
        assert payload["ranges"] == []

    def test_compute_empty_group_is_empty(self):
        self._activate_first_set(0, [])
        response = self.client.post(
            self._compute_url(), json={"text": "randomized trial"},
            headers=self.auth(0))
        self.assertEqual(response.status_code, 200, response.text)
        assert response.json()["ranges"] == []

    def test_compute_from_article_title_and_abstract(self):
        self._activate_first_set(1, ["meta-analysis", "randomized", "随机"])
        for field, expected_text in (("title", TITLE), ("abstract", ABSTRACT)):
            response = self.client.post(
                self._compute_url(),
                json={"article_key": "HK1", "field": field},
                headers=self.auth(1))  # 协作者：题录取自属主库
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            assert payload["source"] == "article"
            assert payload["article_key"] == "HK1"
            assert payload["field"] == field
            assert payload["text"] == expected_text
            assert payload["ranges"], payload
            for item in payload["ranges"]:
                assert _slice(expected_text, item).lower() == payload["terms"][
                    item["term_id"]]["term"].lower()

        title_payload = self.client.post(
            self._compute_url(),
            json={"article_key": "HK1", "field": "title"},
            headers=self.auth(1)).json()
        assert any(_slice(TITLE, item) == "Meta-Analysis"
                   for item in title_payload["ranges"]), title_payload

    def test_compute_input_validation(self):
        self._activate_first_set(0, ["randomized"])
        response = self.client.post(
            self._compute_url(), json={}, headers=self.auth(0))
        self.assertEqual(response.status_code, 400, response.text)
        assert response.json()["detail"]["error_code"] == "highlight_text_required"

        response = self.client.post(
            self._compute_url(),
            json={"text": "x", "article_key": "HK1", "field": "title"},
            headers=self.auth(0))
        self.assertEqual(response.status_code, 400, response.text)

        response = self.client.post(
            self._compute_url(),
            json={"article_key": "HK1", "field": "notes"},
            headers=self.auth(0))
        self.assertEqual(response.status_code, 400, response.text)
        assert response.json()["detail"]["error_code"] == "highlight_field_unknown"

        response = self.client.post(
            self._compute_url(),
            json={"article_key": "NOPE", "field": "title"},
            headers=self.auth(0))
        self.assertEqual(response.status_code, 404, response.text)
        assert response.json()["detail"]["error_code"] == "highlight_article_unknown"

        oversized = "x" * (highlight_core.MAX_TEXT_LENGTH + 1)
        response = self.client.post(
            self._compute_url(), json={"text": oversized}, headers=self.auth(0))
        self.assertEqual(response.status_code, 400, response.text)
        assert response.json()["detail"]["error_code"] == "highlight_text_too_large"

        raw = json.dumps({"text": "bad \ud800 text"}).encode("utf-8")
        response = self.client.post(
            self._compute_url(), content=raw,
            headers={**self.auth(0), "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 400, response.text)
        assert response.json()["detail"]["error_code"] == "highlight_text_invalid"

    # ---- 权限 -----------------------------------------------------------

    def test_non_member_forbidden(self):
        response = self.client.get(self._sets_url(), headers=self.auth(2))
        self.assertEqual(response.status_code, 403, response.text)

        response = self._put_sets(2, self._body()["sets"], None)
        self.assertEqual(response.status_code, 403, response.text)

        response = self.client.post(
            self._compute_url(), json={"text": "randomized"},
            headers=self.auth(2))
        self.assertEqual(response.status_code, 403, response.text)


if __name__ == "__main__":
    unittest.main()
