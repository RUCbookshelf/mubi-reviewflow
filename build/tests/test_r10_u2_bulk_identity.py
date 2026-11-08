# -*- coding: utf-8 -*-
"""r10-U2 批量确认资格判据的双端钉子（2.1.5 G5 换钉版）。

**钉头变更（2.1.5 G5·B1）**：后端已改为单一事实源（``bulk_eligibility`` 三段判据
+ 通用/过短标题从严排除），候选载荷直接下发 ``bulk_eligible`` /
``bulk_ineligible_reason``。本文件里的前端镜像 ``ImportReviewUpdates.eligible()``
是**旧镜像**——前端按 F2 删除 eligible/identityKey 镜像、改读 ``bulk_eligible``
后，本钉退役，由后端矩阵钉（build/tests/test_2_1_5_g5_merge_rules.py）+
载荷一致钉接替。

后端判据（三段任一成立，仍需人工点一下确认）：
  a. 全员身份 DOI 一致（期刊样式 DOI 视同缺 DOI）；
  b. 全员无身份 DOI + 规范化标题/第一作者姓氏/年份三项全一致非空；
  c. 部分成员有身份 DOI、出现的身份 DOI 规范化后只有一个取值 + 三项一致非空。
身份推断判据 b/c 不适用于通用/过短标题（中英清单 + 三个前缀；非 CJK < 4 词、
CJK < 6 字）。

过渡期只允许两类已披露差异（**逐例列举**，见 ``EXPECTED_DIFFS``）：
  ① 新判据 (c) 用例（含「期刊样式 DOI 视同缺 DOI」的同形用例）：后端=可批量、
     前端旧镜像=不可（前端少列，安全方向）；
  ② 通用/过短标题用例：后端=不可、前端旧镜像可能=可——批量提交时后端结构化
     拒绝（全有或全无语义不变）。
其余用例前后端结论必须逐例一致。

三条钉子（钉子 2/3 不受过渡差异影响）：
1. 跨语言对照矩阵（按上表允许两类差异）；
2. 对照组逐位不变：近似但差一项、identity_conflict、已决、多 active、单成员
   等仍不可批量；
3. 真实批量路径（TestClient）：三项强身份一致且题名非通用/非过短的组可整批
   确认（200 且落库）；近似组仍 409 且整批回滚。
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
import uuid
from contextlib import closing

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

#: 一次性临时账号口令（运行时随机生成，不落任何可复用凭据）。
ACCOUNT_SECRET = uuid.uuid4().hex

NODE_CANDIDATES = (r"C:\Users\16073\AppData\Local\hermes\node\node.exe",)


def _node_exe():
    for exe in NODE_CANDIDATES:
        if pathlib.Path(exe).is_file():
            return exe
    import shutil
    return shutil.which("node")


def _member(title, authors, year, doi):
    return {"ref_type": "record", "ref_id": title + "|" + str(hash((title, authors, year, doi))),
            "record": {"record_id": "r", "state": "staged",
                       "article": {"title": title, "authors": authors, "year": year, "doi": doi}}}


def _group(members, status="pending", kind="title"):
    return {"review_id": "candidate", "status": status, "kind": kind, "members": members}


#: 同题名（≥ 4 词、非通用）用于身份推断判据。
_LONG = "Teachers Professional Identity Study"

# 跨语言对照矩阵：每条都不依赖数据库，纯判定。
CASES = {
    # ---- 判据 a：DOI 一致 ----
    "doi_equal": _group([_member("A study", "Smith, J", "2020", "10.X/ABC "),
                         _member("A completely different title", "Doe, A", "1999", "10.x/abc")]),
    "doi_conflict": _group([_member(_LONG, "Smith, J", "2020", "10.x/a"),
                            _member(_LONG, "Smith, J", "2020", "10.x/b")]),
    # 一侧有 DOI、一侧缺，但三项强身份一致 → 新判据 (c)（差异类①）。
    "doi_mixed": _group([_member(_LONG, "Smith, J", "2020", "10.x/a"),
                         _member(_LONG, "Smith, J", "2020", "")]),
    # DOI 已是单篇身份：通用题名不受排除（新旧同结论）。
    "doi_equal_generic_title": _group([_member("Editorial", "Smith, J", "2020", "10.x/ed"),
                                       _member("Editorial", "Smith, J", "2020", "10.x/ed")]),
    # ---- 判据 b：全员无身份 DOI + 三项强身份一致 ----
    "identity_triple_match": _group([_member(_LONG, "Rivera, Ana", "2023", ""),
                                     _member("teachers professional identity study!", "Rivera, Ana", "2023", "")]),
    "identity_year_differs": _group([_member(_LONG, "Rivera, Ana", "2023", ""),
                                     _member(_LONG, "Rivera, Ana", "2024", "")]),
    "identity_author_differs": _group([_member(_LONG, "Rivera, Ana", "2023", ""),
                                       _member(_LONG, "Chen, Mei", "2023", "")]),
    "identity_title_differs": _group([_member(_LONG, "Rivera, Ana", "2023", ""),
                                      _member(_LONG + " Revisited", "Rivera, Ana", "2023", "")]),
    "identity_missing_year": _group([_member(_LONG, "Rivera, Ana", "", ""),
                                     _member(_LONG, "Rivera, Ana", "", "")]),
    "identity_van_particle": _group([_member(_LONG, "van der Berg, J.", "2021", ""),
                                     _member(_LONG, "Berg, J", "2021", "")]),
    "identity_initial_surname": _group([_member(_LONG, "John Smith", "2021", ""),
                                        _member(_LONG, "Smith J", "2021", "")]),
    # ---- 判据 c：部分有 DOI、且只有一个取值（差异类①） ----
    "identity_partial_doi_single": _group([_member(_LONG, "Rivera, Ana", "2023", "10.x/a"),
                                           _member(_LONG, "Rivera, Ana", "2023", "")]),
    "identity_partial_doi_two_values": _group([_member(_LONG, "Rivera, Ana", "2023", "10.x/a"),
                                               _member(_LONG, "Rivera, Ana", "2023", "10.x/b")]),
    "identity_partial_doi_mismatch": _group([_member(_LONG, "Rivera, Ana", "2023", "10.x/a"),
                                             _member(_LONG, "Chen, Mei", "2023", "")]),
    # 期刊样式 DOI 视同缺 DOI（差异类①：后端可、前端旧镜像不可）。
    "journal_pattern_plus_real": _group([_member(_LONG, "Rivera, Ana", "2023", "10.1002/(issn)1545-7249"),
                                         _member(_LONG, "Rivera, Ana", "2023", "10.x/a")]),
    "journal_pattern_plus_empty": _group([_member(_LONG, "Rivera, Ana", "2023", "10.1002/(issn)1545-7249"),
                                          _member(_LONG, "Rivera, Ana", "2023", "")]),
    # ---- 通用/过短标题（差异类②：后端不可、前端旧镜像可能可） ----
    "generic_en_editorial": _group([_member("Editorial", "Rivera, Ana", "2023", ""),
                                    _member("Editorial", "Rivera, Ana", "2023", "")]),
    "generic_en_book_review": _group([_member("Book review", "Rivera, Ana", "2023", ""),
                                      _member("Book review", "Rivera, Ana", "2023", "")]),
    "generic_en_prefix_special_issue": _group(
        [_member("Introduction to the special issue on teacher identity", "Rivera, Ana", "2023", ""),
         _member("Introduction to the special issue on teacher identity", "Rivera, Ana", "2023", "")]),
    "generic_zh_editor_note": _group([_member("编者按", "张三", "2023", ""),
                                      _member("编者按", "张三", "2023", "")]),
    "generic_zh_book_review": _group([_member("书评", "张三", "2023", ""),
                                      _member("书评", "张三", "2023", "")]),
    "short_en_two_words": _group([_member("A study", "Rivera, Ana", "2023", ""),
                                  _member("A study", "Rivera, Ana", "2023", "")]),
    "short_en_three_words": _group([_member("Teacher identity study", "Rivera, Ana", "2023", ""),
                                    _member("Teacher identity study", "Rivera, Ana", "2023", "")]),
    "short_zh_four_chars": _group([_member("教育研究", "张三", "2023", ""),
                                   _member("教育研究", "张三", "2023", "")]),
    # ---- 对照：状态/种类/成员数逐位不变 ----
    "identity_conflict_kind": _group([_member(_LONG, "Smith, J", "2021", ""),
                                      _member(_LONG, "Smith, J", "2021", "")], kind="identity_conflict"),
    "already_decided": _group([_member(_LONG, "Smith, J", "2021", ""),
                               _member(_LONG, "Smith, J", "2021", "")], status="duplicate"),
    "two_active_members": _group([{"ref_type": "active", "ref_id": "a", "article": {"title": _LONG, "authors": "S", "year": "2020", "doi": ""}},
                                  {"ref_type": "active", "ref_id": "b", "article": {"title": _LONG, "authors": "S", "year": "2020", "doi": ""}}]),
    "single_member": _group([_member(_LONG, "Smith, J", "2021", "")]),
}

#: 过渡期允许的前后端差异，**逐例列举**（后端结论, 前端旧镜像结论）。
#: 类①：新判据 (c)（含期刊样式 DOI 视同缺 DOI 的同形用例）——后端可、前端不可。
#: 类②：通用/过短标题——后端不可、前端旧镜像可能可（后端提交时结构化拒绝）。
EXPECTED_DIFFS = {
    "doi_mixed": (True, False),
    "identity_partial_doi_single": (True, False),
    "journal_pattern_plus_real": (True, False),
    "journal_pattern_plus_empty": (True, False),
    "generic_en_editorial": (False, True),
    "generic_en_book_review": (False, True),
    "generic_en_prefix_special_issue": (False, True),
    "generic_zh_editor_note": (False, True),
    "generic_zh_book_review": (False, True),
    "short_en_two_words": (False, True),
    "short_en_three_words": (False, True),
    "short_zh_four_chars": (False, True),
}

_NODE = r"""
const U = require(process.argv[1] + '/custom_frontend/import_review_updates.js');
const cases = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));
const out = {};
for (const [name, group] of Object.entries(cases)) out[name] = U.eligible(group);
process.stdout.write(JSON.stringify(out));
"""


class BulkIdentityParityTests(unittest.TestCase):
    def test_backend_and_frontend_agree_except_declared_transition_diffs(self):
        from custom_backend.import_review_actions import eligible_duplicate
        self.assertTrue(set(EXPECTED_DIFFS) <= set(CASES),
                        "已披露差异必须逐例出现在矩阵中")
        exe = _node_exe()
        if exe is None:
            self.skipTest("node 不可用")
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
            handle.write(json.dumps(CASES, ensure_ascii=False))
            path = handle.name
        try:
            proc = subprocess.run([exe, "-e", _NODE, str(REPO), path], capture_output=True,
                                  text=True, encoding="utf-8", timeout=120)
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        self.assertEqual(proc.returncode, 0, proc.stderr[-1500:])
        frontend = json.loads(proc.stdout.strip().splitlines()[-1])
        backend = {name: eligible_duplicate(case) for name, case in CASES.items()}
        unexpected = {}
        for name in CASES:
            actual = (backend[name], frontend.get(name))
            if name in EXPECTED_DIFFS:
                if actual != EXPECTED_DIFFS[name]:
                    unexpected[name] = actual
            elif backend[name] != frontend.get(name):
                unexpected[name] = actual
        self.assertEqual(unexpected, {}, "未披露的前后端判据漂移：%r" % (unexpected,))

    def test_expected_verdicts(self):
        """后端矩阵：判据三段 + 通用/过短标题排除 + 对照逐位不变。"""
        from custom_backend.import_review_actions import eligible_duplicate
        expected = {
            "doi_equal": True, "doi_conflict": False, "doi_mixed": True,
            "doi_equal_generic_title": True,
            "identity_triple_match": True, "identity_year_differs": False,
            "identity_author_differs": False, "identity_title_differs": False,
            "identity_missing_year": False, "identity_van_particle": True,
            "identity_initial_surname": True,
            "identity_partial_doi_single": True, "identity_partial_doi_two_values": False,
            "identity_partial_doi_mismatch": False,
            "journal_pattern_plus_real": True, "journal_pattern_plus_empty": True,
            "generic_en_editorial": False, "generic_en_book_review": False,
            "generic_en_prefix_special_issue": False,
            "generic_zh_editor_note": False, "generic_zh_book_review": False,
            "short_en_two_words": False, "short_en_three_words": False,
            "short_zh_four_chars": False,
            "identity_conflict_kind": False, "already_decided": False,
            "two_active_members": False, "single_member": False,
        }
        actual = {name: eligible_duplicate(case) for name, case in CASES.items()}
        self.assertEqual(actual, expected)


class BulkConfirmEndToEndTests(unittest.TestCase):
    """真实批量确认路径：三项强身份一致（题名非通用/非过短）的组可整批确认；
    差一项的整批回滚。"""

    #: ≥ 4 词、非通用——满足身份推断判据的题名下限。
    TITLE = "Shared Identity Title Study"

    @classmethod
    def setUpClass(cls):
        cls._fixture = tempfile.TemporaryDirectory(prefix="rf_r10_bulk_identity_")
        os.environ["COBOOKSHELF_DATA_DIR"] = cls._fixture.name
        from fastapi.testclient import TestClient
        from custom_backend.main import app
        cls.client = TestClient(app)
        cls.client.__enter__()
        result = cls.client.post("/api/auth/register",
                                 json={"username": "r10bulk@example.test",
                                       "password": ACCOUNT_SECRET}).json()
        cls.user = {"user_id": result["user"]["id"], "username": result["user"]["username"]}
        cls.client.headers["Authorization"] = "Bearer " + result["token"]
        from coscreen import db, review_imports as store, review_workflow as wf
        from coscreen import tasks as _tasks
        template = cls.client.post("/api/tasks", json={"name": "bulk-identity-template"}).json()["task_id"]
        ctx = app.state.review_workflow.context(template, cls.user)
        db.init_db(ctx.owner_db)
        store.migrate(ctx.owner_db)
        with closing(wf.connect(ctx.owner_db)) as conn, conn:
            conn.execute("INSERT INTO workflow_sources VALUES(?,?,?,?,?)", ("s", "01-Wiley.ris", "01-wiley.ris", 1, "now"))
            conn.execute("INSERT INTO workflow_import_batches VALUES(?,?,?,?,?,?,?)",
                         ("b", str(cls.user["user_id"]), "h", "batch", 1, "staged", "now"))
            conn.execute("INSERT INTO workflow_import_files VALUES(?,?,?,?,?)", ("b", 0, "01-Wiley.ris", "d", "s"))
        cls._template_bytes = pathlib.Path(ctx.owner_db).read_bytes()
        cls._slug = _tasks.slugify_screener(cls.user["username"])

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls._fixture.cleanup()

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

    def _seed_group(self, cid, *, title, authors, year, doi="", second_year=None):
        """两成员候选组；second_year 用于构造「组内年份不一致」的近似组。"""
        from coscreen import review_workflow as wf
        from coscreen.models import Article
        import json as _json
        with closing(wf.connect(self.db)) as conn, conn:
            for suffix in ("a", "b"):
                key = cid + suffix
                member_year = second_year if (suffix == "b" and second_year is not None) else year
                article = Article(zotero_key=key, title=title, authors=authors, year=member_year, doi=doi).to_dict()
                conn.execute("INSERT INTO workflow_source_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (key, "s", key, 1, "01-Wiley.ris", _json.dumps(article), _json.dumps(article),
                              key, key, None, None, "new", "staged", "now"))
                conn.execute("INSERT INTO workflow_import_members VALUES(?,?,?)", ("b", key, 0))
            conn.execute("INSERT INTO workflow_import_candidates(id,signature,kind,status,created_at,updated_at) "
                         "VALUES(?,?,?,?,?,?)", (cid, cid, "title", "pending", "now", "now"))
            for suffix in ("a", "b"):
                conn.execute("INSERT INTO workflow_import_candidate_members VALUES(?,?,?)",
                             (cid, "record", cid + suffix))

    def _version(self):
        from coscreen import review_workflow as wf
        with closing(wf.connect(self.db)) as conn:
            return conn.execute("SELECT version FROM workflow_state").fetchone()[0]

    def test_title_author_year_group_can_be_bulk_confirmed(self):
        self._seed_group("same", title=self.TITLE, authors="Rivera, Ana", year="2023")
        response = self.client.post(self.base + "/bulk-review", json={
            "expected_policy_version": self._version(),
            "actions": [{"kind": "candidate", "id": "same", "keeper_record_id": "samea"}]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["review_updates"]["candidates"][0]["status"], "duplicate")
        self.assertTrue(response.json()["undo"]["available"], "整批必须可作为一步撤销")

    def test_near_miss_group_rolls_back_whole_batch(self):
        self._seed_group("same", title=self.TITLE, authors="Rivera, Ana", year="2023")
        # 近似组：组内两成员年份不同 → 不满足强身份一致（不是「题名相似就算重复」）。
        self._seed_group("near", title=self.TITLE, authors="Rivera, Ana", year="2024", second_year="2025")
        version = self._version()
        response = self.client.post(self.base + "/bulk-review", json={
            "expected_policy_version": version,
            "actions": [{"kind": "candidate", "id": "same", "keeper_record_id": "samea"},
                        {"kind": "candidate", "id": "near", "keeper_record_id": "neara"}]})
        self.assertEqual(response.status_code, 409, response.text)
        from coscreen import review_workflow as wf
        with closing(wf.connect(self.db)) as conn:
            statuses = [row[0] for row in conn.execute("SELECT status FROM workflow_import_candidates ORDER BY id")]
            self.assertEqual(statuses, ["pending", "pending"], "任一失败必须整批回滚")
            self.assertEqual(conn.execute("SELECT version FROM workflow_state").fetchone()[0], version)


if __name__ == "__main__":
    unittest.main()
