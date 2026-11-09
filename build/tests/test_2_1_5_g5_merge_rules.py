# -*- coding: utf-8 -*-
"""2.1.5 G5「重复合并质量」· 合并规则与撤销 红钉（专属新文件，撤实现必红）。

权威设计：``out/release_plan/drafts/design_2.1.5_dedup_merge_quality.md``
（§七「执行决议」优先）。本钉覆盖 B1–B5 与 §七五项要求，逐条在实现前为红：

1. B1 三段判据：DOI 全一致 (a) / 全员无身份 DOI + 三项强身份一致 (b) / 部分
   成员有身份 DOI 且规范化后只有一个取值 + 三项强身份一致 (c)；期刊样式 DOI
   照旧视同缺 DOI（_identity_doi）。
   **通用/过短标题从严排除**：通用标题（中英清单 + 三个前缀）与规范化后过短
   （非 CJK < 4 词、CJK < 6 字）的组不适用身份推断判据 (b)(c)，仍可逐条确认。
   DOI 完全一致的 (a) 不受此限——DOI 已是单篇身份，与题名通用与否无关；这与
   §七「不论有无 DOI」的字面读法不同，按本波指令 B1 的显式三段范围执行，契约
   与报告均写明。既有 DOI 批量回归（test_import_review_actions 的短题名夹具）
   也据此保持可用。
2. 载荷单一事实源：present_candidates 与 review_updates 两组载荷都带
   ``bulk_eligible`` / ``bulk_ineligible_reason``，且与 ``eligible_duplicate()``
   逐组一致；apply_bulk 调同一函数，不可批量时返回结构化 message_key。
3. B2 suggested_keeper：规则①组内已入任务成员必为保留者（硬约束）；否则按
   完整度字典序（身份 DOI > 摘要 > 期刊 > 年份 > 作者 > 标题）取最高，平分取
   原顺序（导入顺序）；reasons 为结构化键。
4. B3 只补空：保留条目空字段（NULL/空串/仅空白/占位串）才补；两边有值不同 →
   保留条目值不动（B4）；多被合并者不同值 → 完整度降序、原顺序取第一份，其余
   记入留痕；DOI 撞号（期刊样式/被其他已入任务文献占用）不补并报告；写入前
   复核字段仍为空/仍等于操作前值，否则跳过并报告；留痕逐字段落库。
5. B3 撤销：单条与批量补空完整恢复（NULL 与空串保真），保留条目的已入任务
   文献行与待审记录载荷都在快照内；被他人改动后不覆盖。留痕表随撤销标记。
6. B5 逐字段采用：action=field_picks + picks，缺省=keep；并发校验同
   accept_incoming；incoming DOI 与 accept_incoming 同一 DOI 守卫；撤销沿用
   metadata 快照。非法字段名 422/结构化 400。
7. B4 明文钉：冲突缺省=保留条目值不变（单条与批量各一条）。
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import unittest
import uuid
from contextlib import closing

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from coscreen import db as db_mod  # noqa: E402
from coscreen import review_imports as store  # noqa: E402
from coscreen import review_workflow as wf  # noqa: E402

#: 一次性临时账号口令（运行时随机生成，不落任何可复用凭据）。
ACCOUNT_SECRET = uuid.uuid4().hex

#: 非通用、非过短的长题名（英文 ≥ 4 词），供身份推断判据用例使用。
LONG_TITLE = "Teachers Professional Identity Study"


def _article(**kw):
    base = {"title": "", "authors": "", "year": "", "doi": "",
            "journal": "", "abstract": ""}
    base.update(kw)
    return base


def _member(title, authors, year, doi, ref_id=None, ref_type="record", **extra):
    ref_id = ref_id or ("m-" + title + "-" + str(authors) + "-" + str(year))
    if ref_type == "active":
        return {"ref_type": "active", "ref_id": ref_id,
                "article": _article(title=title, authors=authors, year=year, doi=doi, **extra)}
    return {"ref_type": "record", "ref_id": ref_id,
            "record": {"record_id": ref_id, "state": "staged",
                       "article": _article(title=title, authors=authors, year=year, doi=doi, **extra)}}


def _group(*members, kind="title", status="pending"):
    return {"kind": kind, "status": status, "members": list(members)}


# ---------------------------------------------------------------------------
# B1 判据矩阵（纯判定，不依赖数据库）
# ---------------------------------------------------------------------------

def _eligibility_cases():
    return {
        # (a) 全员身份 DOI 一致
        "doi_equal": (_group(_member("A study one", "Smith, J", "2020", "10.X/ABC "),
                             _member("A completely different title", "Doe, A", "1999", "10.x/abc")),
                      True, None),
        "doi_conflict": (_group(_member(LONG_TITLE, "Rivera, Ana", "2023", "10.x/a"),
                                _member(LONG_TITLE, "Rivera, Ana", "2023", "10.x/b")),
                         False, "doi_conflict"),
        # (b) 全员无身份 DOI + 三项强身份一致
        "identity_triple_match": (_group(_member(LONG_TITLE, "Rivera, Ana", "2023", ""),
                                         _member("teachers professional identity study!", "Rivera, Ana", "2023", "")),
                                  True, None),
        "identity_year_differs": (_group(_member(LONG_TITLE, "Rivera, Ana", "2023", ""),
                                         _member(LONG_TITLE, "Rivera, Ana", "2024", "")),
                                  False, "missing_identity"),
        "identity_author_differs": (_group(_member(LONG_TITLE, "Rivera, Ana", "2023", ""),
                                           _member(LONG_TITLE, "Chen, Mei", "2023", "")),
                                    False, "missing_identity"),
        "identity_missing_year": (_group(_member(LONG_TITLE, "Rivera, Ana", "", ""),
                                         _member(LONG_TITLE, "Rivera, Ana", "", "")),
                                  False, "missing_identity"),
        "identity_van_particle": (_group(_member(LONG_TITLE, "van der Berg, J.", "2021", ""),
                                         _member(LONG_TITLE, "Berg, J", "2021", "")),
                                  True, None),
        # (c) 部分成员有身份 DOI、规范化后只有一个取值 + 三项强身份一致
        "identity_partial_doi_single": (_group(_member(LONG_TITLE, "Rivera, Ana", "2023", "10.x/a"),
                                               _member(LONG_TITLE, "Rivera, Ana", "2023", "")),
                                        True, None),
        "identity_partial_doi_two_values": (_group(_member(LONG_TITLE, "Rivera, Ana", "2023", "10.x/a"),
                                                   _member(LONG_TITLE, "Rivera, Ana", "2023", "10.x/b")),
                                            False, "doi_conflict"),
        "identity_partial_doi_mismatch": (_group(_member(LONG_TITLE, "Rivera, Ana", "2023", "10.x/a"),
                                                 _member(LONG_TITLE, "Chen, Mei", "2023", "")),
                                          False, "missing_identity"),
        # 期刊样式 DOI 视同缺 DOI
        "journal_pattern_plus_real": (_group(_member(LONG_TITLE, "Rivera, Ana", "2023", "10.1002/(issn)1545-7249"),
                                             _member(LONG_TITLE, "Rivera, Ana", "2023", "10.x/a")),
                                      True, None),
        "journal_pattern_only_pair": (_group(_member(LONG_TITLE, "Rivera, Ana", "2023", "10.1002/(issn)1545-7249"),
                                             _member(LONG_TITLE, "Rivera, Ana", "2023", "10.1002/(ISSN)1545-7249")),
                                      True, None),
        "journal_pattern_plus_empty": (_group(_member(LONG_TITLE, "Rivera, Ana", "2023", "10.1002/(issn)1545-7249"),
                                              _member(LONG_TITLE, "Rivera, Ana", "2023", "")),
                                       True, None),
        # 通用标题（英文）：至少 3 例 + 前缀
        "generic_en_editorial": (_group(_member("Editorial", "Rivera, Ana", "2023", ""),
                                        _member("Editorial", "Rivera, Ana", "2023", "")),
                                 False, "generic_title"),
        "generic_en_book_review": (_group(_member("Book review", "Rivera, Ana", "2023", ""),
                                          _member("Book review", "Rivera, Ana", "2023", "")),
                                   False, "generic_title"),
        "generic_en_erratum": (_group(_member("Erratum", "Rivera, Ana", "2023", ""),
                                      _member("Erratum", "Rivera, Ana", "2023", "")),
                               False, "generic_title"),
        "generic_en_prefix_special_issue": (
            _group(_member("Introduction to the special issue on teacher identity", "Rivera, Ana", "2023", ""),
                   _member("Introduction to the special issue on teacher identity", "Rivera, Ana", "2023", "")),
            False, "generic_title"),
        "generic_en_prefix_guest_editorial": (
            _group(_member("Guest editorial: the future of reviews", "Rivera, Ana", "2023", ""),
                   _member("Guest editorial: the future of reviews", "Rivera, Ana", "2023", "")),
            False, "generic_title"),
        # 通用标题（中文）：至少 3 例
        "generic_zh_editor_note": (_group(_member("编者按", "张三", "2023", ""),
                                          _member("编者按", "张三", "2023", "")),
                                   False, "generic_title"),
        "generic_zh_book_review": (_group(_member("书评", "张三", "2023", ""),
                                          _member("书评", "张三", "2023", "")),
                                   False, "generic_title"),
        "generic_zh_preface": (_group(_member("卷首语", "张三", "2023", ""),
                                      _member("卷首语", "张三", "2023", "")),
                               False, "generic_title"),
        # (a) 不受通用/过短标题限制：DOI 已是单篇身份
        "doi_equal_generic_title": (_group(_member("Editorial", "Rivera, Ana", "2023", "10.x/ed"),
                                           _member("Editorial", "Rivera, Ana", "2023", "10.x/ed")),
                                    True, None),
        "doi_equal_short_title": (_group(_member("A study", "Rivera, Ana", "2023", "10.x/one"),
                                         _member("A study", "Rivera, Ana", "2023", "10.x/one")),
                                  True, None),
        # 过短标题（非 CJK < 4 词）
        "short_en_three_words": (_group(_member("Teacher identity study", "Rivera, Ana", "2023", ""),
                                        _member("Teacher identity study", "Rivera, Ana", "2023", "")),
                                 False, "short_title"),
        "short_en_two_words": (_group(_member("A study", "Rivera, Ana", "2023", ""),
                                      _member("A study", "Rivera, Ana", "2023", "")),
                               False, "short_title"),
        # 过短标题（CJK < 6 字）
        "short_zh_four_chars": (_group(_member("教育研究", "张三", "2023", ""),
                                       _member("教育研究", "张三", "2023", "")),
                                False, "short_title"),
        "short_zh_five_chars": (_group(_member("教育研究法", "张三", "2023", ""),
                                       _member("教育研究法", "张三", "2023", "")),
                                False, "short_title"),
        # 非过短的 CJK 长题名可走身份推断判据
        "long_zh_identity_match": (_group(_member("教师专业身份研究综述", "张三", "2023", ""),
                                          _member("教师专业身份研究综述", "张三", "2023", "")),
                                   True, None),
        # 既有对照：状态/种类/成员数
        "identity_conflict_kind": (_group(_member(LONG_TITLE, "Rivera, Ana", "2023", ""),
                                          _member(LONG_TITLE, "Rivera, Ana", "2023", ""),
                                          kind="identity_conflict"),
                                   False, "identity_conflict"),
        "already_decided": (_group(_member(LONG_TITLE, "Rivera, Ana", "2023", ""),
                                   _member(LONG_TITLE, "Rivera, Ana", "2023", ""),
                                   status="duplicate"),
                            False, "status"),
        "two_active_members": (_group(_member(LONG_TITLE, "Rivera, Ana", "2023", "", ref_id="a", ref_type="active"),
                                      _member(LONG_TITLE, "Rivera, Ana", "2023", "", ref_id="b", ref_type="active")),
                               False, "active_conflict"),
        "single_member": (_group(_member(LONG_TITLE, "Rivera, Ana", "2023", "")),
                          False, "insufficient_members"),
        "mixed_member_types": (_group(_member(LONG_TITLE, "Rivera, Ana", "2023", "", ref_id="a", ref_type="active"),
                                      _member(LONG_TITLE, "Rivera, Ana", "2023", "", ref_id="b")),
                               True, None),
    }


class BulkEligibilityCriteriaTests(unittest.TestCase):
    def test_three_leg_matrix(self):
        """判据三段 + 通用/过短标题 + 期刊样式 DOI 的逐例结论。"""
        from custom_backend import import_review_actions as actions
        self.assertTrue(hasattr(actions, "bulk_eligibility"),
                        "B1 必须暴露 bulk_eligibility()（载荷与校验共用的单一事实源）")
        for name, (group, expected, reason) in _eligibility_cases().items():
            with self.subTest(case=name):
                ok, why = actions.bulk_eligibility(group)
                self.assertEqual(ok, expected)
                self.assertEqual(why, reason)
                self.assertEqual(actions.eligible_duplicate(group), expected,
                                 "eligible_duplicate 必须与 bulk_eligibility 同源")


# ---------------------------------------------------------------------------
# 数据库夹具（真实任务 + 复制模板库，沿用既有鉴定套件手法）
# ---------------------------------------------------------------------------

#: 模块级共享夹具：main.py 的 DATA_DIR 在 import 时绑定，跨类重复注册/换目录
#: 会把任务路由指向首个夹具（既有鉴定套件已记录同一坑），故只建一次。
_ENV: dict = {}


def setUpModule():
    fixture = tempfile.TemporaryDirectory(prefix="rf_215g5_")
    os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
    from fastapi.testclient import TestClient
    from custom_backend.main import app
    client = TestClient(app)
    client.__enter__()
    result = client.post("/api/auth/register",
                         json={"username": "g5merge@example.test",
                               "password": ACCOUNT_SECRET}).json()
    user = {"user_id": result["user"]["id"], "username": result["user"]["username"]}
    client.headers["Authorization"] = "Bearer " + result["token"]
    from coscreen import tasks as _tasks
    template = client.post("/api/tasks", json={"name": "g5-merge-template"}).json()["task_id"]
    ctx = app.state.review_workflow.context(template, user)
    db_mod.init_db(ctx.owner_db)
    store.migrate(ctx.owner_db)
    with closing(wf.connect(ctx.owner_db)) as conn, conn:
        conn.execute("INSERT INTO workflow_sources VALUES(?,?,?,?,?)",
                     ("s", "01-Wiley.ris", "01-wiley.ris", 1, "now"))
        conn.execute("INSERT INTO workflow_import_batches VALUES(?,?,?,?,?,?,?)",
                     ("b", str(user["user_id"]), "h", "batch", 1, "staged", "now"))
        conn.execute("INSERT INTO workflow_import_files VALUES(?,?,?,?,?)",
                     ("b", 0, "01-Wiley.ris", "d", "s"))
    _ENV.update(fixture=fixture, client=client, user=user,
                slug=_tasks.slugify_screener(user["username"]),
                template_bytes=pathlib.Path(ctx.owner_db).read_bytes())


def tearDownModule():
    _ENV["client"].__exit__(None, None, None)
    _ENV["fixture"].cleanup()


class _ApiCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = _ENV["client"]
        cls.user = _ENV["user"]
        cls._slug = _ENV["slug"]
        cls._template_bytes = _ENV["template_bytes"]

    def setUp(self):
        import gc
        gc.collect()
        self.task = self.client.post("/api/tasks", json={"name": self._testMethodName}).json()["task_id"]
        from coscreen import tasks as _tasks
        from custom_backend.main import DATA_DIR
        info = _tasks.find_task(self.task, DATA_DIR)
        self.db = info.dir / (self._slug + ".db")
        pathlib.Path(self.db).write_bytes(self._template_bytes)
        self.base = "/api/tasks/%s/import-batches/b" % self.task

    # ---- 数据夹具 ----
    def add_active(self, key, *, title="Keeper Title", authors="Rivera, Ana", year=2023,
                   doi="", abstract=None, journal=None, item_type=""):
        with closing(wf.connect(self.db)) as conn, conn:
            conn.execute(
                "INSERT INTO articles(zotero_key,item_type,title,authors,journal,year,doi,abstract,url,import_order) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (key, item_type, title, authors, journal, year, doi, abstract, "", 1))

    def add_record(self, rid, *, title=LONG_TITLE, authors="Rivera, Ana", year=2023, doi="",
                   abstract=None, journal=None, row_index=1, created_at="2026-01-01T00:00:00+00:00",
                   canonical=None):
        article = {"zotero_key": rid, "item_type": "", "title": title, "authors": authors,
                   "journal": journal, "year": year, "doi": doi, "abstract": abstract,
                   "url": "", "source_format": "ris", "raw_json": "{}", "content_hash": ""}
        with closing(wf.connect(self.db)) as conn, conn:
            conn.execute("INSERT INTO workflow_source_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (rid, "s", rid, row_index, "01-Wiley.ris", json.dumps(article),
                          json.dumps(article), rid, rid, canonical, None, "new", "staged", created_at))
            conn.execute("INSERT INTO workflow_import_members VALUES(?,?,?)", ("b", rid, 0))

    def add_group(self, cid, refs, kind="title", status="pending"):
        with closing(wf.connect(self.db)) as conn, conn:
            conn.execute("INSERT INTO workflow_import_candidates(id,signature,kind,status,created_at,updated_at) "
                         "VALUES(?,?,?,?,?,?)", (cid, cid, kind, status, "now", "now"))
            for ref_type, ref_id in refs:
                conn.execute("INSERT INTO workflow_import_candidate_members VALUES(?,?,?)",
                             (cid, ref_type, ref_id))

    def article(self, key):
        with closing(wf.connect(self.db)) as conn:
            row = conn.execute("SELECT * FROM articles WHERE zotero_key=?", (key,)).fetchone()
        return dict(row) if row else None

    def version(self):
        with closing(wf.connect(self.db)) as conn:
            return conn.execute("SELECT version FROM workflow_state WHERE singleton=1").fetchone()[0]

    def post(self, path, body):
        return self.client.post(self.base + path, json={"expected_policy_version": self.version(), **body})

    def decide(self, cid, keeper_key=None, keeper_record_id=None, **extra):
        body = {"status": "duplicate", "keeper_key": keeper_key,
                "keeper_record_id": keeper_record_id, **extra}
        return self.post("/candidates/" + cid, body)

    def candidates(self):
        response = self.client.get(self.base + "/candidates")
        self.assertEqual(response.status_code, 200, response.text)
        return {row["id"]: row for row in response.json()["candidates"]}


# ---------------------------------------------------------------------------
# 存储：新增留痕表在既有库上升级幂等
# ---------------------------------------------------------------------------

class MigrationIdempotencyTests(unittest.TestCase):
    def test_migration_versions_are_globally_unique(self):
        """所有写 workflow_migrations 的模块共用一张表：版本号必须全局唯一。

        曾经用 3（与 import_review_undo 撞号）→ 遗留库因 version=3 已存在而
        migrate 早退、新表永不创建，首次确认重复抛 OperationalError→500。
        """
        from coscreen import binary_reset, coding_evidence, import_review_undo
        from coscreen import import_sources, review_workflow, screening_rounds
        owners = {
            'workflow': review_workflow.SCHEMA_VERSION,
            'review_imports': store.MIGRATION_VERSION,
            'import_review_undo': import_review_undo.MIGRATION_VERSION,
            'import_sources_v4': import_sources.VERSION,
            'import_sources_v5': import_sources.FILENAME_VERSION,
            'screening_rounds': screening_rounds.ROUNDS_VERSION,
            'coding_evidence': coding_evidence.MIGRATION_VERSION,
            'binary_reset': binary_reset.RESET_VERSION,
        }
        self.assertEqual(len(owners.values()), len(set(owners.values())),
                         "workflow_migrations 版本号撞号：%r" % (owners,))

    def test_field_merge_table_added_idempotently_on_legacy_db(self):
        with tempfile.TemporaryDirectory(prefix="rf_215g5_mig_") as tmp:
            path = pathlib.Path(tmp) / "owner.db"
            db_mod.init_db(path)
            store.migrate(path)
            store.migrate(path)  # 重复迁移不报错
            with closing(wf.connect(path)) as conn, conn:
                # 模拟 G5 之前的真实遗留库：已含 import_review_undo 的 version=3，
                # 但没有 G5 的版本行与新表 → 再次迁移必须补齐（不得因 3 早退）。
                conn.execute("DELETE FROM workflow_migrations WHERE version=?",
                             (store.MIGRATION_VERSION,))
                conn.execute("INSERT OR IGNORE INTO workflow_migrations VALUES(?,?)",
                             (2, "legacy"))
                conn.execute("INSERT OR IGNORE INTO workflow_migrations VALUES(?,?)",
                             (3, "legacy"))
                conn.execute("DROP TABLE workflow_import_field_merges")
            store.migrate(path)
            with closing(wf.connect(path)) as conn:
                tables = {row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                versions = {row[0] for row in conn.execute(
                    "SELECT version FROM workflow_migrations")}
        self.assertIn("workflow_import_field_merges", tables,
                      "遗留库升级必须补建留痕表（版本行不得与其他模块撞号）")
        self.assertIn(store.MIGRATION_VERSION, versions)
        self.assertIn(3, versions, "其他模块的既有版本行必须保留")


# ---------------------------------------------------------------------------
# B1/B2 载荷单一事实源 + suggested_keeper
# ---------------------------------------------------------------------------

class CandidatePayloadTests(_ApiCase):
    def _seed(self):
        self.add_active("K1", title=LONG_TITLE, doi="", abstract=None)
        self.add_record("R1", title=LONG_TITLE, doi="")
        self.add_group("g1", [("active", "K1"), ("record", "R1")])
        self.add_active("K2", title="Keeper Two", doi="10.x/keep2")
        self.add_record("R2", title=LONG_TITLE, doi="10.x/keep2")
        self.add_record("R3", title=LONG_TITLE, doi="10.x/other")
        self.add_group("g2", [("active", "K2"), ("record", "R2"), ("record", "R3")])

    def test_payload_carries_bulk_fields_and_matches_backend(self):
        from custom_backend.import_review_actions import eligible_duplicate
        self._seed()
        rows = self.candidates()
        self.assertIn("bulk_eligible", rows["g1"])
        self.assertIn("bulk_ineligible_reason", rows["g1"])
        self.assertIs(rows["g1"]["bulk_eligible"], True)
        self.assertIsNone(rows["g1"]["bulk_ineligible_reason"])
        self.assertIs(rows["g2"]["bulk_eligible"], False)
        self.assertEqual(rows["g2"]["bulk_ineligible_reason"], "doi_conflict")
        for cid, row in rows.items():
            self.assertEqual(row["bulk_eligible"], eligible_duplicate(row),
                             "载荷结论必须与 eligible_duplicate() 逐组一致：%s" % cid)

    def test_review_updates_payload_matches(self):
        from custom_backend.import_review_actions import eligible_duplicate
        self._seed()
        response = self.post("/candidates/g2", {"status": "not_duplicate"})
        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()["review_updates"]["candidates"][0]
        self.assertIs(row["bulk_eligible"], False)
        # 已决候选的载荷同样带原因键（优先级：状态先于 DOI）。
        self.assertEqual(row["bulk_ineligible_reason"], "status")
        self.assertEqual(row["bulk_eligible"], eligible_duplicate(row))
        self.assertIn("suggested_keeper", row)

    def test_bulk_eligibility_error_is_structured(self):
        self._seed()
        response = self.post("/bulk-review", {"actions": [
            {"kind": "candidate", "id": "g1", "keeper_key": "K1"},
            {"kind": "candidate", "id": "g2", "keeper_key": "K2"}]})
        self.assertEqual(response.status_code, 409, response.text)
        detail = response.json()["detail"]
        self.assertIsInstance(detail, dict, detail)
        self.assertEqual(detail.get("message_key"), "import_merge.bulk_ineligible")
        self.assertEqual(detail.get("params", {}).get("reason"), "doi_conflict")

    def test_suggested_keeper_prefers_active_member(self):
        self._seed()
        rows = self.candidates()
        self.assertEqual(rows["g1"]["suggested_keeper"],
                         {"keeper_key": "K1", "keeper_record_id": None,
                          "reasons": ["active_member"]})

    def test_suggested_keeper_dict_order(self):
        # 组内无已入任务成员：DOI > 摘要；D1 有 DOI，D2 只有摘要 → D1。
        self.add_record("D1", title=LONG_TITLE, doi="10.x/d1", row_index=2)
        self.add_record("D2", title=LONG_TITLE, doi="", abstract="Has abstract", row_index=1)
        self.add_group("d", [("record", "D1"), ("record", "D2")])
        keeper = self.candidates()["d"]["suggested_keeper"]
        self.assertEqual(keeper["keeper_record_id"], "D1")
        self.assertIn("has_doi", keeper["reasons"])
        self.assertNotIn("has_abstract", keeper["reasons"])

    def test_suggested_keeper_tie_uses_import_order(self):
        # 完整度相同 → 原顺序（导入顺序，这里按 row_index；与 ref id 字典序相反）。
        self.add_record("zz-first", title=LONG_TITLE, doi="", row_index=1)
        self.add_record("aa-second", title=LONG_TITLE, doi="", row_index=2)
        self.add_group("t", [("record", "aa-second"), ("record", "zz-first")])
        keeper = self.candidates()["t"]["suggested_keeper"]
        self.assertEqual(keeper["keeper_record_id"], "zz-first")

    def test_suggested_keeper_reasons_are_structural_keys(self):
        self.add_record("F1", title=LONG_TITLE, authors="Rivera, Ana", year=2023,
                        doi="10.x/f1", abstract="A", journal="J", row_index=1)
        self.add_record("F2", title=LONG_TITLE, doi="10.x/f2", row_index=2)
        self.add_group("f", [("record", "F1"), ("record", "F2")])
        keeper = self.candidates()["f"]["suggested_keeper"]
        self.assertEqual(keeper["keeper_record_id"], "F1")
        self.assertEqual(keeper["reasons"],
                         ["has_doi", "has_abstract", "has_journal", "has_year",
                          "has_authors", "has_title"])

    def test_suggested_keeper_is_never_an_implicit_default(self):
        """suggested_keeper 只做建议：确认接口仍必须显式给 keeper，否则拒绝。"""
        self._seed()
        response = self.post("/candidates/g1", {"status": "duplicate"})
        self.assertEqual(response.status_code, 409, response.text)


# ---------------------------------------------------------------------------
# B3/B4 只补空 + 冲突缺省 + 留痕 + DOI 守卫
# ---------------------------------------------------------------------------

class MergeFillTests(_ApiCase):
    def test_fill_blank_only_and_never_overwrite(self):
        self.add_active("K1", title="Keeper Title", doi="10.keep/1", abstract=None,
                        journal=None, year=2020)
        self.add_record("R1", title="Different Merged Title", doi="10.keep/1",
                        abstract="Merged abstract", journal="Journal X", year=2021)
        self.add_group("g", [("active", "K1"), ("record", "R1")])
        response = self.decide("g", keeper_key="K1")
        self.assertEqual(response.status_code, 200, response.text)
        row = self.article("K1")
        self.assertEqual(row["abstract"], "Merged abstract")
        self.assertEqual(row["journal"], "Journal X")
        # B4：两边都有值但不同 → 保留条目值不动
        self.assertEqual(row["title"], "Keeper Title")
        self.assertEqual(row["year"], 2020)
        summary = response.json()["merge_fill"]
        self.assertEqual(summary["articles"], 1)
        self.assertEqual(summary["fields"], {"abstract": 1, "journal": 1})

    def test_no_fill_omits_merge_fill_key(self):
        """无补空/撞号/跳过时单条确认不返回 merge_fill（与批量路径同形）。"""
        self.add_active("K1", title=LONG_TITLE, doi="10.nf/1", abstract="Keeper abstract")
        self.add_record("R1", title=LONG_TITLE, doi="10.nf/1", abstract="Keeper abstract")
        self.add_group("g", [("active", "K1"), ("record", "R1")])
        response = self.decide("g", keeper_key="K1")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("merge_fill", response.json())

    def test_multiple_merged_members_pick_most_complete_then_order(self):
        self.add_active("K1", doi="10.keep/1", abstract=None)
        self.add_record("R1", title=LONG_TITLE, doi="10.keep/1", abstract="First abstract",
                        row_index=1)
        self.add_record("R2", title=LONG_TITLE, doi="10.keep/1", abstract="Second abstract",
                        journal="Journal Two", row_index=2)
        self.add_group("g", [("active", "K1"), ("record", "R1"), ("record", "R2")])
        response = self.decide("g", keeper_key="K1")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.article("K1")["abstract"], "Second abstract",
                         "多被合并者给出不同值 → 完整度降序取第一份")
        with closing(wf.connect(self.db)) as conn:
            trails = [dict(row) for row in conn.execute(
                "SELECT field,applied,source_record_id,decision_source,conflict "
                "FROM workflow_import_field_merges WHERE candidate_id='g' AND field='abstract' "
                "ORDER BY id")]
        self.assertEqual([(r["source_record_id"], r["applied"]) for r in trails],
                         [("R2", 1), ("R1", 0)],
                         "未采用的冲突值必须留在留痕里（applied=0）")

    def test_doi_guard_journal_pattern_is_not_filled(self):
        self.add_active("K1", title=LONG_TITLE, doi=None)
        self.add_record("R1", title=LONG_TITLE, doi="10.1002/(issn)1545-7249")
        self.add_group("g", [("active", "K1"), ("record", "R1")])
        response = self.decide("g", keeper_key="K1")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(self.article("K1")["doi"], "期刊样式 DOI 不得补入保留条目")
        collisions = response.json()["merge_fill"]["doi_collision"]
        self.assertEqual([c["reason"] for c in collisions], ["journal_pattern"])

    def test_doi_guard_occupied_by_other_article(self):
        self.add_active("OTHER", title="Other article", doi="10.other/1")
        self.add_active("K1", title=LONG_TITLE, doi=None)
        self.add_record("R1", title=LONG_TITLE, doi="10.other/1")
        self.add_group("g", [("active", "K1"), ("record", "R1")])
        response = self.decide("g", keeper_key="K1")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(self.article("K1")["doi"], "已被其他已入任务文献占用的 DOI 不得补入")
        collisions = response.json()["merge_fill"]["doi_collision"]
        self.assertEqual(collisions[0]["reason"], "occupied")
        self.assertEqual(collisions[0]["occupied_by"], "OTHER")

    def test_bulk_never_overwrites_conflicts(self):
        for cid, keeper in (("b1", "B1K"), ("b2", "B2K")):
            self.add_active(keeper, title="Keeper Title " + cid, doi="10.bulk/" + cid,
                            abstract=None)
            self.add_record(cid + "R", title="Incoming Different Title " + cid,
                            doi="10.bulk/" + cid, abstract="Filled " + cid)
            self.add_group(cid, [("active", keeper), ("record", cid + "R")])
        response = self.post("/bulk-review", {"actions": [
            {"kind": "candidate", "id": "b1", "keeper_key": "B1K"},
            {"kind": "candidate", "id": "b2", "keeper_key": "B2K"}]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.article("B1K")["title"], "Keeper Title b1")
        self.assertEqual(self.article("B2K")["title"], "Keeper Title b2")
        self.assertEqual(self.article("B1K")["abstract"], "Filled b1")
        self.assertEqual(response.json()["merge_fill"]["articles"], 2)

    def test_write_guard_skips_field_changed_by_others(self):
        """写入条件复核：规划时字段为空、落笔时已被他人改动 → 跳过并报告。

        API 的单事务（BEGIN IMMEDIATE）内不可能真实交错，故在共享写入助手上
        构造「规划快照 vs 当前行」不一致，钉住逐字段条件写本身。
        """
        self.add_active("K1", title=LONG_TITLE, doi="10.keep/1", abstract="Someone else wrote")
        # 终审 #22 起补空成功会发布 acceptance（record_id 外键指向来源记录），
        # 夹具按真实形态补一条来源记录（生产路径由 _candidate_merged_members
        # 从 workflow_source_records 读出，天然满足外键）。
        self.add_record("R1", title=LONG_TITLE, doi="10.keep/1",
                        abstract="Merged abstract", journal="J", row_index=2)
        keeper_meta = {"title": LONG_TITLE, "doi": "10.keep/1", "abstract": None,
                       "journal": None, "authors": "Rivera, Ana", "year": 2023,
                       "item_type": "", "url": ""}
        merged = [{"record_id": "R1",
                   "article": {"title": LONG_TITLE, "doi": "10.keep/1",
                               "abstract": "Merged abstract", "journal": "J"}}]
        with closing(wf.connect(self.db)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            summary = store.apply_keeper_fill(
                conn, batch_id="b", candidate_id="guard", actor="u1",
                keeper_kind="active", keeper_ref="K1", keeper_meta=keeper_meta,
                merged=merged)
        self.assertEqual(self.article("K1")["abstract"], "Someone else wrote")
        self.assertIn({"field": "abstract", "reason": "changed_by_other"}, summary["skipped"])
        self.assertEqual(summary["fields"].get("journal"), 1)


# ---------------------------------------------------------------------------
# 冲突选择 conflict_picks（合法/非法/ai_suggestion_id）
# ---------------------------------------------------------------------------

class ConflictPickTests(_ApiCase):
    def _seed_conflict(self, cid="g", keeper="K1", row="R1"):
        self.add_active(keeper, title="Keeper Conservative Title", doi="10.cp/1")
        self.add_record(row, title="Merged Preferred Title", doi="10.cp/1")
        self.add_group(cid, [("active", keeper), ("record", row)])

    def test_conflict_pick_by_member_id(self):
        self._seed_conflict()
        response = self.decide("g", keeper_key="K1", conflict_picks={"title": "R1"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.article("K1")["title"], "Merged Preferred Title")
        with closing(wf.connect(self.db)) as conn:
            trail = dict(conn.execute(
                "SELECT decision_source,source_record_id FROM workflow_import_field_merges "
                "WHERE candidate_id='g' AND field='title'").fetchone())
        self.assertEqual(trail["decision_source"], "human")
        self.assertEqual(trail["source_record_id"], "R1")

    def test_conflict_pick_keeper_keeps_value(self):
        self._seed_conflict()
        response = self.decide("g", keeper_key="K1", conflict_picks={"title": "keeper"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.article("K1")["title"], "Keeper Conservative Title")

    def test_conflict_pick_with_ai_suggestion_marks_prefill(self):
        self._seed_conflict()
        response = self.decide("g", keeper_key="K1",
                               conflict_picks={"title": "R1"},
                               ai_suggestion_id="audit-123")
        self.assertEqual(response.status_code, 200, response.text)
        with closing(wf.connect(self.db)) as conn:
            trail = dict(conn.execute(
                "SELECT decision_source,ai_suggestion_id FROM workflow_import_field_merges "
                "WHERE candidate_id='g' AND field='title'").fetchone())
        self.assertEqual(trail["decision_source"], "ai_prefill")
        self.assertEqual(trail["ai_suggestion_id"], "audit-123")

    def test_conflict_pick_invalid_field_is_structured_400(self):
        self._seed_conflict()
        response = self.decide("g", keeper_key="K1", conflict_picks={"bogus": "R1"})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["message_key"],
                         "import_merge.conflict_pick_invalid_field")

    def test_conflict_pick_invalid_member_is_structured_400(self):
        self._seed_conflict()
        response = self.decide("g", keeper_key="K1", conflict_picks={"title": "nope"})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["message_key"],
                         "import_merge.conflict_pick_invalid_member")

    def test_conflict_pick_on_blank_side_is_rule_fill(self):
        # 保留条目该字段为空 → 自动补全（规则），不是冲突选择。
        self.add_active("K1", title=LONG_TITLE, doi="10.cp/2", abstract=None)
        self.add_record("R1", title=LONG_TITLE, doi="10.cp/2", abstract="From record")
        self.add_group("g", [("active", "K1"), ("record", "R1")])
        response = self.decide("g", keeper_key="K1", conflict_picks={"abstract": "R1"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.article("K1")["abstract"], "From record")
        with closing(wf.connect(self.db)) as conn:
            source = conn.execute(
                "SELECT decision_source FROM workflow_import_field_merges "
                "WHERE candidate_id='g' AND field='abstract'").fetchone()[0]
        self.assertEqual(source, "rule")

    def test_bulk_conflict_pick_applies_and_undoes(self):
        self._seed_conflict()
        response = self.post("/bulk-review", {"actions": [
            {"kind": "candidate", "id": "g", "keeper_key": "K1",
             "conflict_picks": {"title": "R1"}}]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.article("K1")["title"], "Merged Preferred Title")
        undo = self.post("/undo", {})
        self.assertEqual(undo.status_code, 200, undo.text)
        self.assertEqual(self.article("K1")["title"], "Keeper Conservative Title")


# ---------------------------------------------------------------------------
# B3 撤销（单条/批量、NULL 与空串保真、他人改动不覆盖、留痕标记）
# ---------------------------------------------------------------------------

class MergeFillUndoTests(_ApiCase):
    def test_single_undo_restores_fill_and_empty_string_fidelity(self):
        self.add_active("K1", title="Keeper Title", doi="10.u/1", abstract=None, journal="")
        self.add_record("R1", title="Different Merged Title", doi="10.u/1",
                        abstract="Merged abstract", journal="Journal X")
        self.add_group("g", [("active", "K1"), ("record", "R1")])
        self.assertEqual(self.decide("g", keeper_key="K1").status_code, 200)
        self.assertEqual(self.article("K1")["abstract"], "Merged abstract")
        undo = self.post("/undo", {})
        self.assertEqual(undo.status_code, 200, undo.text)
        row = self.article("K1")
        self.assertIsNone(row["abstract"], "NULL 必须保真恢复为 NULL")
        self.assertEqual(row["journal"], "", "空串必须保真恢复为空串")

    def test_bulk_undo_restores_every_keeper(self):
        for cid, keeper in (("u1", "K1"), ("u2", "K2")):
            self.add_active(keeper, title="Keeper Title " + cid, doi="10.undo/" + cid,
                            abstract=None)
            self.add_record(cid + "R", title="Incoming Title " + cid, doi="10.undo/" + cid,
                            abstract="Filled " + cid)
            self.add_group(cid, [("active", keeper), ("record", cid + "R")])
        response = self.post("/bulk-review", {"actions": [
            {"kind": "candidate", "id": "u1", "keeper_key": "K1"},
            {"kind": "candidate", "id": "u2", "keeper_key": "K2"}]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.article("K1")["abstract"], "Filled u1")
        undo = self.post("/undo", {})
        self.assertEqual(undo.status_code, 200, undo.text)
        self.assertIsNone(self.article("K1")["abstract"])
        self.assertIsNone(self.article("K2")["abstract"])

    def test_undo_marks_field_merge_trail(self):
        self.add_active("K1", title=LONG_TITLE, doi="10.u/3", abstract=None)
        self.add_record("R1", title=LONG_TITLE, doi="10.u/3", abstract="Merged")
        self.add_group("g", [("active", "K1"), ("record", "R1")])
        self.assertEqual(self.decide("g", keeper_key="K1").status_code, 200)
        self.assertEqual(self.post("/undo", {}).status_code, 200)
        with closing(wf.connect(self.db)) as conn:
            undone = conn.execute(
                "SELECT undone FROM workflow_import_field_merges WHERE candidate_id='g'").fetchone()[0]
        self.assertEqual(undone, 1, "撤销必须标记留痕行（契约口径：标记而非删除）")

    def test_external_change_blocks_undo(self):
        self.add_active("K1", title=LONG_TITLE, doi="10.u/4", abstract=None)
        self.add_record("R1", title=LONG_TITLE, doi="10.u/4", abstract="Merged")
        self.add_group("g", [("active", "K1"), ("record", "R1")])
        self.assertEqual(self.decide("g", keeper_key="K1").status_code, 200)
        with closing(wf.connect(self.db)) as conn, conn:
            conn.execute("UPDATE articles SET abstract='Written by someone else' WHERE zotero_key='K1'")
        response = self.post("/undo", {})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.article("K1")["abstract"], "Written by someone else")

    def test_staged_keeper_fill_and_undo_restores_payload(self):
        self.add_record("KEEP", title=LONG_TITLE, doi="10.u/5", abstract=None)
        self.add_record("OTHER", title=LONG_TITLE, doi="10.u/5", abstract="From other")
        self.add_group("g", [("record", "KEEP"), ("record", "OTHER")])
        response = self.decide("g", keeper_record_id="KEEP")
        self.assertEqual(response.status_code, 200, response.text)
        with closing(wf.connect(self.db)) as conn:
            payload = json.loads(conn.execute(
                "SELECT article_json FROM workflow_source_records WHERE id='KEEP'").fetchone()[0])
        self.assertEqual(payload["abstract"], "From other")
        self.assertEqual(self.post("/undo", {}).status_code, 200)
        with closing(wf.connect(self.db)) as conn:
            payload = json.loads(conn.execute(
                "SELECT article_json FROM workflow_source_records WHERE id='KEEP'").fetchone()[0])
        self.assertIsNone(payload["abstract"], "待审保留记录的载荷必须完整恢复")


class MergeFillAcceptancePins(_ApiCase):
    """终审 #22（用户 2026-11-09 裁定）：补空写库后按当前 catalog_revision 发布
    ``workflow_metadata_acceptances``（与 decide_metadata 两条写路径同口径）——
    补空之后才首次同步目录的成员（新受邀/成员库重建）必须一次就拿到补全字段，
    不被补空前的旧 acceptance 冲回 NULL。"""

    def test_fill_publishes_acceptance_new_member_first_sync_sees_fill(self):
        import sqlite3

        from coscreen import tasks as tasks_mod
        from custom_backend import shared_corpus
        from custom_backend.main import DATA_DIR

        self.add_active("K", title="Old Title", journal=None, abstract=None,
                        year=2023, doi="10.k/1")
        self.add_record("m0", title="New Title", journal=None, abstract=None,
                        doi="10.k/1", canonical="K")
        with closing(wf.connect(self.db)) as conn, conn:
            store.proposal(conn, "m0", "K")
        first = self.post("/metadata/m0", {"action": "field_picks",
                                           "picks": {"title": "incoming"}})
        self.assertEqual(first.status_code, 200, first.text)
        self.add_record("R1", title=LONG_TITLE, journal="Journal Filled",
                        abstract="Abstract Filled", doi="10.k/1", row_index=2)
        self.add_group("g", [("active", "K"), ("record", "R1")])
        decided = self.decide("g", keeper_key="K")
        self.assertEqual(decided.status_code, 200, decided.text)
        self.assertEqual(self.article("K")["journal"], "Journal Filled")

        with closing(wf.connect(self.db)) as conn:
            revision, metadata_json = conn.execute(
                "SELECT revision, metadata_json FROM workflow_metadata_acceptances "
                "WHERE zotero_key='K'").fetchone()
            catalog = conn.execute(
                "SELECT catalog_revision FROM workflow_state WHERE singleton=1"
            ).fetchone()[0]
        self.assertEqual(revision, catalog,
                         "acceptance 必须按补空后的当前 catalog_revision 发布")
        self.assertEqual(json.loads(metadata_json)["journal"], "Journal Filled")
        self.assertEqual(json.loads(metadata_json)["abstract"], "Abstract Filled")

        info = tasks_mod.find_task(self.task, DATA_DIR)
        shared_corpus.sync_shared_articles(
            info.dir, owner_username=self.user["username"],
            member_username="sec7b-newbie", catalog_revision=catalog)
        member_db = info.dir / (tasks_mod.slugify_screener("sec7b-newbie") + ".db")
        with closing(sqlite3.connect(str(member_db))) as conn:
            row = conn.execute(
                "SELECT journal, abstract FROM articles WHERE zotero_key='K'").fetchone()
        self.assertEqual(row, ("Journal Filled", "Abstract Filled"),
                         "新成员首次同步必须拿到补全字段（旧 acceptance 不得冲回 NULL）")


# ---------------------------------------------------------------------------
# B5 逐字段采用 field_picks（含 DOI 守卫与撤销）
# ---------------------------------------------------------------------------

class FieldPicksTests(_ApiCase):
    def _proposal(self, rid, key, *, incoming_title="New Title", incoming_journal="New Journal",
                  incoming_doi="10.k/9", incoming_year=2011):
        self.add_record(rid, title=incoming_title, doi=incoming_doi, journal=incoming_journal,
                        year=incoming_year, canonical=key)
        with closing(wf.connect(self.db)) as conn, conn:
            store.proposal(conn, rid, key)

    def _seed(self):
        self.add_active("K", title="Old Title", journal="Old Journal", year=2010, doi="10.k/9")
        self._proposal("m0", "K")

    def test_field_picks_applies_only_incoming(self):
        self._seed()
        response = self.post("/metadata/m0", {"action": "field_picks",
                                              "picks": {"title": "incoming"}})
        self.assertEqual(response.status_code, 200, response.text)
        row = self.article("K")
        self.assertEqual(row["title"], "New Title")
        self.assertEqual(row["journal"], "Old Journal", "未选 incoming 的字段保持现有值")
        self.assertEqual(row["year"], 2010)
        with closing(wf.connect(self.db)) as conn:
            accepted = json.loads(conn.execute(
                "SELECT accepted_json FROM workflow_import_metadata WHERE record_id='m0'").fetchone()[0])
            acceptance = conn.execute(
                "SELECT metadata_json FROM workflow_metadata_acceptances WHERE zotero_key='K'").fetchone()[0]
        self.assertEqual(accepted["title"], "New Title")
        self.assertEqual(accepted["journal"], "Old Journal")
        self.assertEqual(json.loads(acceptance)["title"], "New Title")

    def test_field_picks_defaults_to_keep(self):
        self._seed()
        response = self.post("/metadata/m0", {"action": "field_picks",
                                              "picks": {"journal": "keep"}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.article("K")["journal"], "Old Journal")
        self.assertEqual(self.article("K")["title"], "Old Title")

    def test_same_action_resubmit_is_structured_noop(self):
        """终审 #26（用户 2026-11-09 裁定）：同 action 重复提交（不同 picks）→
        200 + ``metadata_noop=true`` 标记；不写库（题录/accepted_json/acceptance
        不变、policy_version 不增）；不同 action 仍 409。"""
        self._seed()
        first = self.post("/metadata/m0", {"action": "field_picks",
                                           "picks": {"title": "incoming"}})
        self.assertEqual(first.status_code, 200, first.text)
        version_after_first = first.json()["policy_version"]
        with closing(wf.connect(self.db)) as conn:
            accepted_1 = conn.execute(
                "SELECT accepted_json FROM workflow_import_metadata "
                "WHERE record_id='m0'").fetchone()[0]
            acceptance_1 = conn.execute(
                "SELECT revision, record_id, metadata_json FROM "
                "workflow_metadata_acceptances WHERE zotero_key='K'").fetchone()

        second = self.post("/metadata/m0", {"action": "field_picks",
                                            "picks": {"journal": "incoming"}})
        self.assertEqual(second.status_code, 200, second.text)
        self.assertIs(second.json().get("metadata_noop"), True,
                      "重复提交必须携带结构化 noop 标记（前端据此提示未生效）")
        self.assertEqual(second.json()["policy_version"], version_after_first,
                         "幂等重放不得抬 policy_version")
        with closing(wf.connect(self.db)) as conn:
            accepted_2 = conn.execute(
                "SELECT accepted_json FROM workflow_import_metadata "
                "WHERE record_id='m0'").fetchone()[0]
            acceptance_2 = conn.execute(
                "SELECT revision, record_id, metadata_json FROM "
                "workflow_metadata_acceptances WHERE zotero_key='K'").fetchone()
        self.assertEqual(accepted_2, accepted_1, "accepted_json 不得变")
        self.assertEqual(acceptance_2, acceptance_1, "acceptance 不得变")
        self.assertEqual(self.article("K")["journal"], "Old Journal",
                         "第二次的 picks 不得生效")

        different = self.post("/metadata/m0", {"action": "accept_incoming"})
        self.assertEqual(different.status_code, 409, different.text)

    def test_field_picks_concurrency_check(self):
        self._seed()
        with closing(wf.connect(self.db)) as conn, conn:
            conn.execute("UPDATE articles SET title='Changed meanwhile' WHERE zotero_key='K'")
        response = self.post("/metadata/m0", {"action": "field_picks",
                                              "picks": {"title": "incoming"}})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.article("K")["title"], "Changed meanwhile")

    def test_field_picks_invalid_field_name_is_422_or_structured_400(self):
        self._seed()
        response = self.post("/metadata/m0", {"action": "field_picks",
                                              "picks": {"bogus": "incoming"}})
        self.assertIn(response.status_code, (400, 422), response.text)

    def test_field_picks_undo_restores_metadata(self):
        self._seed()
        self.assertEqual(self.post("/metadata/m0", {"action": "field_picks",
                                                    "picks": {"title": "incoming"}}).status_code, 200)
        undo = self.post("/undo", {})
        self.assertEqual(undo.status_code, 200, undo.text)
        row = self.article("K")
        self.assertEqual(row["title"], "Old Title")
        self.assertEqual(row["journal"], "Old Journal")
        with closing(wf.connect(self.db)) as conn:
            action = conn.execute(
                "SELECT action FROM workflow_import_metadata WHERE record_id='m0'").fetchone()[0]
        self.assertIsNone(action)

    def test_field_picks_doi_guard_journal_pattern(self):
        self.add_active("K", title="Old Title", doi="10.k/9")
        self._proposal("m0", "K", incoming_doi="10.1002/(issn)1111-2222")
        response = self.post("/metadata/m0", {"action": "field_picks",
                                              "picks": {"doi": "incoming"}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.article("K")["doi"], "10.k/9", "incoming 期刊样式 DOI 不得写入")
        self.assertEqual(response.json()["doi_collision"][0]["reason"], "journal_pattern")

    def test_accept_incoming_also_guards_journal_pattern_doi(self):
        self.add_active("K", title="Old Title", doi="10.k/9")
        self._proposal("m0", "K", incoming_doi="10.1002/(issn)3333-4444")
        response = self.post("/metadata/m0", {"action": "accept_incoming"})
        self.assertEqual(response.status_code, 200, response.text)
        row = self.article("K")
        self.assertEqual(row["doi"], "10.k/9")
        self.assertEqual(row["title"], "New Title", "其余字段照常采纳")
        self.assertEqual(response.json()["doi_collision"][0]["reason"], "journal_pattern")

    def test_accept_incoming_guards_occupied_doi(self):
        self.add_active("K", title="Old Title", doi="10.k/9")
        self.add_active("OTHER", title="Occupied", doi="10.occupied/1")
        self._proposal("m0", "K", incoming_doi="10.occupied/1")
        response = self.post("/metadata/m0", {"action": "accept_incoming"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.article("K")["doi"], "10.k/9")
        self.assertEqual(response.json()["doi_collision"][0]["reason"], "occupied")

    def test_keep_existing_unchanged(self):
        self._seed()
        response = self.post("/metadata/m0", {"action": "keep_existing"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.article("K")["title"], "Old Title")


if __name__ == "__main__":
    unittest.main(verbosity=2)
