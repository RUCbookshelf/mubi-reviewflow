# -*- coding: utf-8 -*-
"""2.1.5 Wave G4/W9 · 撤稿/更正关注后端钉子（专属新文件，撤实现必红）。

覆盖设计 ``design_2.1.5_retraction_watch.md`` §四 B4 全清单：

1. **出站仅 api.crossref.org**：mock 通道记录每一次 ``open()``，断言主机恒为
   配置的 Crossref base。自托管改配后同一断言仍成立（主机=改配后的 base），且
   ``file://`` / 带 userinfo / 带查询串的 base 一律拒绝；DOI 无法改变出站主机
   （``quote(safe='')`` + 构造后主机断言）。
2. **无任何自动触发**：静态扫描本模块源码，禁止 threading / scheduler / Timer /
   asyncio / BackgroundTasks / atexit / ``__del__`` 等后台形态；端点的全部出站
   调用次数必须等于「HTTP 请求驱动的 DOI 数」（多一次即为偷偷预热）。
3. **append-only**：第二个 run 建立并跑完后，第一个 run 的**行**与它的发现
   **逐字段零改写**。
4. **决定表零变化审计**：整个流程（两个 run、含失败降级）前后，筛选员库
   ``decisions`` / ``stage2_decisions`` 逐行相等，库文件 sha256 不变。
5. **单条超时/坏响应 → unknown，绝不 500**（整批仍是 200）。
6. **分批游标幂等**：50 条/批；同游标重放不重复出站、不重复计数；断点续查。
7. **300 DOI 上限截断 + 披露**（``truncated`` / ``remaining_dois``）。
8. **去重键幂等**：``(doi, update_type, source_doi)``——同一发现从「通告侧」与
   「受影响侧」两个方向解析到时只落一行。
9. **scope 口径**：默认复筛纳入；可选初筛纳入；无 DOI 条目计 unknown 且零出站。
10. **权限**：触发仅属主/can_manage（成员 403）；runs/详情/徽标全员可读。
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import urllib.error
import warnings
from contextlib import closing
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# 短前缀 + 短任务名：Windows MAX_PATH 对临时目录很敏感。
fixture = tempfile.TemporaryDirectory(prefix="rf_w9_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient  # noqa: E402

from coscreen import db as db_mod  # noqa: E402
from coscreen import fulltext as fulltext_mod  # noqa: E402
from coscreen import review_workflow as workflow  # noqa: E402
from coscreen import tasks as tasks_mod  # noqa: E402
from coscreen.models import Article  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend import pdf_fetch_api  # noqa: E402
from custom_backend import retraction_watch_api as w9  # noqa: E402
from custom_backend.main import DATA_DIR, app  # noqa: E402

CROSSREF = "https://api.crossref.org"
SELF_HOSTED = "https://crossref.mirror.example"

#: 逐表固定 SQL（表名为模块常量；查询串保持字面量，不拼接不格式化）。
_ROW_SQL = {
    "decisions": "SELECT * FROM decisions",
    "stage2_decisions": "SELECT * FROM stage2_decisions",
}


# ---------------------------------------------------------------------------
# 出站桩：只允许配置端点，记录每次调用
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self, limit=-1):
        if limit is None or limit < 0:
            return self._payload
        return self._payload[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class CrossrefStub:
    """按 DOI 片段回放；记录每次 (url, timeout)。未声明片段回放空 message。"""

    def __init__(self):
        self.calls: list[tuple[str, float | None]] = []
        self.routes: dict[str, object] = {}

    def route(self, fragment: str, payload) -> "CrossrefStub":
        self.routes[fragment] = payload
        return self

    def open(self, request, timeout=None):
        url = request.full_url
        self.calls.append((url, timeout))
        for fragment, payload in self.routes.items():
            if fragment in url:
                if isinstance(payload, BaseException):
                    raise payload
                if isinstance(payload, bytes):
                    return FakeResponse(payload)
                return FakeResponse(json.dumps(payload).encode("utf-8"))
        return FakeResponse(json.dumps({"message": {"DOI": "unrouted"}}).encode())

    def hosts(self) -> set[str]:
        return {urlsplit(url).hostname for url, _ in self.calls}


def works(doi: str, **kwargs) -> dict:
    message = {"DOI": doi}
    message.update(kwargs)
    return {"message": message}


def updated_by(doi: str, source: str, kind: str, date=None, label="") -> dict:
    entry = {"DOI": source, "type": kind, "label": label}
    if date:
        entry["updated"] = {"date-parts": [date]}
    return works(doi, **{"updated-by": [entry]})


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

def _make_user(prefix: str):
    user = auth_mod.create_user(f"{prefix}@example.test", f"{prefix}-Pass-2026!")
    token = auth_mod.create_access_token(user["id"], user["username"])
    return user, {"Authorization": "Bearer " + token}


def _table_rows(path: Path, table: str) -> list[tuple]:
    sql = _ROW_SQL.get(table)
    if sql is None:
        raise KeyError(table)
    with closing(sqlite3.connect(str(path))) as conn:
        try:
            return sorted(conn.execute(sql).fetchall())
        except sqlite3.OperationalError:
            return []


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _collaboration_snapshot(info) -> dict:
    """协作库**既有**四表快照（retraction_* 属本波新增，不参与比对）。"""
    path = info.dir / "collaboration.db"
    out: dict[str, object] = {}
    with closing(sqlite3.connect(str(path))) as conn:
        try:
            out["task"] = sorted(conn.execute(
                "SELECT * FROM collaboration_task").fetchall())
        except sqlite3.OperationalError:
            out["task"] = None
        try:
            out["grants"] = sorted(conn.execute(
                "SELECT * FROM collaboration_role_grants ORDER BY user_id").fetchall())
        except sqlite3.OperationalError:
            out["grants"] = None
        try:
            out["settings"] = sorted(conn.execute(
                "SELECT * FROM collaboration_settings ORDER BY key").fetchall())
        except sqlite3.OperationalError:
            out["settings"] = None
        try:
            out["member_settings"] = sorted(conn.execute(
                "SELECT * FROM collaboration_member_settings "
                "ORDER BY user_id").fetchall())
        except sqlite3.OperationalError:
            out["member_settings"] = None
    return out


class W9Base(unittest.TestCase):
    _seq = 0

    #: 覆盖：子类可放大（300 上限用例）。
    ARTICLE_COUNT = 4

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        # 用户名按测试类隔离：同一 session 里各类各自建号，重名会被 auth 的
        # 「筛选员数据路径冲突」守卫拒掉（slug 相同）。
        tag = cls.__name__[:10].lower()
        cls.owner, cls.oauth = _make_user(f"{tag}own")
        cls.member, cls.mauth = _make_user(f"{tag}mem")
        cls.outsider, cls.xauth = _make_user(f"{tag}out")
        # 钉子自身证明「礼貌节奏」存在（随后按用例置 0 避免拖慢）。
        assert w9.POLITE_INTERVAL_SECONDS > 0

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        type(self)._seq += 1
        mock.patch.object(w9, "POLITE_INTERVAL_SECONDS", 0).start()
        self.addCleanup(mock.patch.stopall)
        name = f"{type(self).__name__[:12].lower()}{type(self)._seq}"
        response = self.client.post(
            "/api/tasks", json={"name": name}, headers=self.oauth)
        self.assertIn(response.status_code, (200, 201), response.text)
        self.task = response.json()["task_id"]
        self.info = tasks_mod.find_task(self.task, DATA_DIR)
        self.owner_db = self.info.dir / (
            tasks_mod.slugify_screener(self.owner["username"]) + ".db")
        db_mod.init_db(self.owner_db)
        workflow.migrate(self.owner_db)
        invite = self.client.post(
            f"/api/tasks/{self.task}/collaboration/invite",
            json={"username": self.member["username"], "role": "coder"},
            headers=self.oauth)
        self.assertEqual(invite.status_code, 201, invite.text)

    # ---- 数据播种 -------------------------------------------------------
    @staticmethod
    def _decision_at(template, index: int):
        """单元素模板 = 全员同一决定；多元素模板 = 逐条模板（超长取最后一条）。"""
        if not template:
            return None
        if len(template) == 1:
            return template[0]
        return template[min(index - 1, len(template) - 1)]

    def seed(self, count: int | None = None, *, with_doi=True,
             stage2=("include",), stage1=None):
        """写入题录 + 决定。``stage2``/``stage1`` 为决定模板（见 _decision_at）。"""
        total = count if count is not None else self.ARTICLE_COUNT
        articles = []
        for index in range(1, total + 1):
            doi = f"10.1000/w9k{index:03d}" if with_doi else None
            articles.append(Article(zotero_key=f"K{index:03d}",
                                    title=f"Study {index}", authors="Wang, A",
                                    journal="J Test", year=2021, doi=doi))
        articles.append(Article(zotero_key="K900", title="No DOI record",
                                authors="", journal="", year=2021, doi=None))
        db_mod.upsert_articles(self.owner_db, articles, [])
        if stage1:
            for index in range(1, total + 1):
                decision = self._decision_at(stage1, index)
                if decision:
                    db_mod.save_decision(self.owner_db, f"K{index:03d}", decision)
        if stage2:
            for index in range(1, total + 1):
                decision = self._decision_at(stage2, index)
                if decision:
                    fulltext_mod.save_stage2(
                        self.owner_db, f"K{index:03d}", decision)
        return articles

    def check(self, body: dict, *, headers=None):
        return self.client.post(f"/api/tasks/{self.task}/retraction-check",
                                json=body, headers=headers or self.oauth)

    def run_to_end(self, scope="stage2_include", *, headers=None, offset=0):
        """连续调用直到 done（最多 20 批，防止实现不推进时死循环）。

        若已有在途 run（409 run_in_progress）则按返回的 run_id 续跑——这正是
        契约给前端的恢复路径（`GET runs` → `run_id`）。
        """
        headers = headers or self.oauth
        response = self.check({"scope": scope, "offset": offset}, headers=headers)
        payload = response.json()
        if (response.status_code == 409
                and payload.get("detail", {}).get("error_code")
                == "run_in_progress"):
            params = payload["detail"]["params"]
            payload = self.check({"scope": params["scope"],
                                  "run_id": params["run_id"]},
                                 headers=headers).json()
        batches = [payload]
        while not payload.get("done") and len(batches) < 20:
            payload = self.check(
                {"scope": payload["scope"], "cursor": payload["cursor"]},
                headers=headers).json()
            batches.append(payload)
        return payload, batches

    def settings_path(self) -> Path:
        return self.info.dir / "pdf_fetch_settings.json"


# ---------------------------------------------------------------------------
# 1. 出站白名单
# ---------------------------------------------------------------------------

class OutboundWhitelistPins(W9Base):
    def test_only_configured_crossref_host_is_contacted(self):
        self.seed(2, stage2=("include",))
        stub = CrossrefStub().route("w9k001", updated_by(
            "10.1000/w9k001", "10.1000/retract01", "retraction",
            [2024, 3, 2], "Retraction Note"))
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub):
            response = self.check({"scope": "stage2_include"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(stub.calls, "应至少发起一次 Crossref 查询")
        self.assertEqual(stub.hosts(), {"api.crossref.org"})
        for url, timeout in stub.calls:
            self.assertTrue(url.startswith(CROSSREF + "/works/"), url)
            self.assertEqual(timeout, 15.0)   # 复用 pdf_fetch 的短超时
        # 默认未配置 email → 不带 mailto（URL 仍由白名单构造）
        self.assertNotIn("mailto=", stub.calls[0][0])

    def test_self_hosted_base_keeps_whitelist_consistency(self):
        self.seed(1, stage2=("include",))
        self.settings_path().write_text(json.dumps({
            "enabled": False, "email": "team@example.test",
            "crossref_base_url": SELF_HOSTED,
        }), encoding="utf-8")
        stub = CrossrefStub().route("w9k001", updated_by(
            "10.1000/w9k001", "10.1000/eoc", "expression_of_concern"))
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub):
            response = self.check({"scope": "stage2_include"})
        self.assertEqual(response.status_code, 200, response.text)
        # 改配后：只打自托管 base，绝不再触达官方域
        self.assertEqual(stub.hosts(), {"crossref.mirror.example"})
        self.assertTrue(stub.calls[0][0].startswith(SELF_HOSTED + "/works/"))
        # 配了 email 也**不随请求发出**：出站载荷只有 DOI（r2 复审 HIGH）
        self.assertNotIn("mailto", stub.calls[0][0])
        self.assertNotIn("@", stub.calls[0][0])
        self.assertNotIn("?", stub.calls[0][0])

    def test_base_url_shape_is_validated(self):
        for bad in ("file:///etc/passwd", "ftp://api.crossref.org",
                    "https://user:pw@api.crossref.org",
                    "https://api.crossref.org/works?x=1"):
            with self.assertRaises(pdf_fetch_api.OutboundError):
                pdf_fetch_api.crossref_works_url(bad, "10.1000/x")

    def test_doi_cannot_steer_the_outbound_host(self):
        url = pdf_fetch_api.crossref_works_url(
            CROSSREF, "10.1000/../../evil.example/x")
        self.assertEqual(urlsplit(url).hostname, "api.crossref.org")
        self.assertNotIn("../", url)
        with self.assertRaises(pdf_fetch_api.OutboundError):
            pdf_fetch_api.crossref_works_url(CROSSREF, "not-a-doi")

    def test_outbound_carries_only_doi(self):
        """出站只发 DOI：URL 里不得出现标题/摘要等题录文本。"""
        self.seed(1, stage2=("include",))
        stub = CrossrefStub()
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub):
            self.check({"scope": "stage2_include"})
        for url, _ in stub.calls:
            self.assertNotIn("Study%201", url)
            self.assertNotIn("Study 1", url)
            self.assertIn("10.1000%2Fw9k001", url)


# ---------------------------------------------------------------------------
# 2. 无任何自动触发
# ---------------------------------------------------------------------------

class NoBackgroundTriggerPins(W9Base):
    #: 禁止在**代码**里出现的模块/名字（docstring 里披露「没有 scheduler」不算）。
    FORBIDDEN_MODULES = {"threading", "asyncio", "sched", "atexit",
                         "concurrent", "multiprocessing", "apscheduler",
                         "schedule", "signal"}
    FORBIDDEN_NAMES = {"BackgroundTasks", "Timer", "Thread", "Process",
                       "threading", "asyncio"}

    def _identifiers(self) -> tuple[set[str], set[str]]:
        """AST 级扫描：docstring/注释不参与判定（只认真实导入与标识符引用）。"""
        tree = ast.parse(Path(w9.__file__).read_text(encoding="utf-8"))
        modules: set[str] = set()
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module.split(".")[0])
            elif isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
        return modules, names

    def test_module_has_no_background_machinery(self):
        modules, names = self._identifiers()
        self.assertEqual(modules & self.FORBIDDEN_MODULES, set(),
                         "W9 不得引入线程/定时/进程模块")
        self.assertEqual(names & self.FORBIDDEN_NAMES, set(),
                         "W9 不得引用后台执行形态")

    def test_outbound_count_equals_http_driven_dois(self):
        """一次请求只查本批 DOI——多出来的就是偷偷预热/后台。"""
        self.seed(3, stage2=("include",))
        stub = CrossrefStub()
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub):
            self.check({"scope": "stage2_include"})
        # 3 条带 DOI（K900 无 DOI 且未纳入 stage2 → 不在范围）
        self.assertEqual(len(stub.calls), 3)


class RepoWideNoBackgroundPins(unittest.TestCase):
    """全库 grep 钉（r2 复审 LOW a）：不只扫 W9 模块，扫整个后端与领域包。"""

    ROOT = Path(__file__).resolve().parents[2]
    TOKENS = ("BackgroundTasks", "apscheduler", "sched.scheduler",
              "threading.Timer", "asyncio.create_task", "loop.create_task",
              "ensure_future", "schedule.every")
    #: 唯一的既有例外：桌面模式「重启后回跳」的 1 秒延时回调，与 W9 无关。
    ALLOWED = {("custom_backend/main.py", "threading.Timer")}

    def test_no_scheduler_or_background_task_residue_repo_wide(self):
        """**递归**扫 custom_backend/ 与 coscreen/ 全部 .py（含 al/stats/parsers 子包）。"""
        hits: set[tuple[str, str]] = set()
        for folder in ("custom_backend", "coscreen"):
            for path in sorted((self.ROOT / folder).rglob("*.py")):
                if "__pycache__" in path.parts:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                for token in self.TOKENS:
                    if token in text:
                        hits.add((str(path.relative_to(self.ROOT)).replace("\\", "/"),
                                  token))
        self.assertEqual(hits - self.ALLOWED, set(),
                         f"新增了后台/定时形态：{sorted(hits - self.ALLOWED)}")

    def test_w9_is_imported_only_by_main(self):
        """只有一个模块 import 了 W9（main.py 的注册行）——没有后台调用方。

        用 AST 判导入，而不是子串匹配：文档/注释里提到模块名不算调用方。
        """
        callers: list[str] = []
        for path in sorted(self.ROOT.rglob("*.py")):
            if "build" in path.parts or path.name == "retraction_watch_api.py":
                continue
            try:
                with warnings.catch_warnings():
                    # 其它模块源码里的旧式转义会触发 DeprecationWarning，与判定无关。
                    warnings.simplefilter("ignore")
                    tree = ast.parse(
                        path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            found = False
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    found = found or node.module.endswith("retraction_watch_api")
                elif isinstance(node, ast.Import):
                    found = found or any(
                        alias.name.endswith("retraction_watch_api")
                        for alias in node.names)
            if found:
                callers.append(str(path.relative_to(self.ROOT)).replace("\\", "/"))
        self.assertEqual(callers, ["custom_backend/main.py"])


# ---------------------------------------------------------------------------
# 3. append-only + 4. 决定表零变化
# ---------------------------------------------------------------------------

class AppendOnlyPins(W9Base):
    def _run_snapshot(self) -> dict:
        collab = self.info.dir / "collaboration.db"
        with closing(sqlite3.connect(str(collab))) as conn:
            conn.row_factory = sqlite3.Row
            runs = [dict(row) for row in conn.execute(
                "SELECT * FROM retraction_runs ORDER BY run_id")]
            findings = [dict(row) for row in conn.execute(
                "SELECT * FROM retraction_findings "
                "ORDER BY run_id, zotero_key, update_type, source_doi")]
        return {"runs": runs, "findings": findings}

    def test_second_run_never_rewrites_the_first(self):
        self.seed(2, stage2=("include",))
        stub = CrossrefStub().route("w9k001", updated_by(
            "10.1000/w9k001", "10.1000/retract01", "retraction"))
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub):
            first, _ = self.run_to_end()
        self.assertTrue(first["done"])
        before = self._run_snapshot()
        self.assertEqual(len(before["runs"]), 1)
        first_run = dict(before["runs"][0])
        first_findings = [row for row in before["findings"]
                          if row["run_id"] == first_run["run_id"]]
        self.assertTrue(first_findings, "第一个 run 必须有发现")

        # 第二个 run：不同发现（不同来源 DOI）
        stub2 = CrossrefStub().route("w9k001", updated_by(
            "10.1000/w9k001", "10.1000/correction02", "correction"))
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub2):
            second, _ = self.run_to_end()
        self.assertTrue(second["done"])
        self.assertNotEqual(second["run_id"], first_run["run_id"])

        after = self._run_snapshot()
        self.assertEqual(len(after["runs"]), 2)
        self.assertEqual(after["runs"][0], first_run, "旧 run 行被改写了")
        self.assertEqual([row for row in after["findings"]
                          if row["run_id"] == first_run["run_id"]],
                         first_findings, "旧 run 的发现被改写了")

        # 徽标跨 run 去重后同时保留两类
        badge = self.client.get(f"/api/tasks/{self.task}/retraction-badge",
                                headers=self.mauth).json()
        self.assertEqual(sorted(badge["badges"]["K001"]["types"]),
                         ["correction", "retraction"])
        # 严重度优先：撤稿不被后来的更正降级
        self.assertEqual(badge["badges"]["K001"]["type"], "retraction")

    def test_decisions_are_never_touched(self):
        """属主库 + 成员库 + 协作库既有表，全流程前后零变化（r2 复审 LOW b）。"""
        self.seed(3, stage2=("include", "include", "exclude"),
                  stage1=("include", "include", "include", "include"))
        member_db = self.info.dir / (
            tasks_mod.slugify_screener(self.member["username"]) + ".db")
        db_mod.init_db(member_db)
        workflow.migrate(member_db)
        db_mod.upsert_articles(member_db, [
            Article(zotero_key="K001", title="Study 1", authors="Wang, A",
                    journal="J Test", year=2021, doi="10.1000/w9k001"),
            Article(zotero_key="K002", title="Study 2", authors="Wang, A",
                    journal="J Test", year=2021, doi="10.1000/w9k002"),
        ], [])
        db_mod.save_decision(member_db, "K001", "include")
        fulltext_mod.save_stage2(member_db, "K001", "include")

        decisions_before = _table_rows(self.owner_db, "decisions")
        stage2_before = _table_rows(self.owner_db, "stage2_decisions")
        member_before = _table_rows(member_db, "decisions")
        member_stage2_before = _table_rows(member_db, "stage2_decisions")
        sha_before = (_sha256(self.owner_db), _sha256(member_db))
        collab_before = _collaboration_snapshot(self.info)

        stub = CrossrefStub().route("w9k001", updated_by(
            "10.1000/w9k001", "10.1000/retract01", "retraction"))
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub):
            payload, _ = self.run_to_end()
            self.run_to_end("stage1_include")
        self.assertEqual(payload["status"], "done")

        self.assertEqual(_table_rows(self.owner_db, "decisions"), decisions_before)
        self.assertEqual(_table_rows(self.owner_db, "stage2_decisions"),
                         stage2_before)
        self.assertEqual(_table_rows(member_db, "decisions"), member_before)
        self.assertEqual(_table_rows(member_db, "stage2_decisions"),
                         member_stage2_before)
        self.assertEqual((_sha256(self.owner_db), _sha256(member_db)), sha_before,
                         "撤稿检查不得写任何筛选员库（连字节都不许变）")
        self.assertEqual(_collaboration_snapshot(self.info), collab_before,
                         "协作库既有表（非 retraction_*）被改写了")


# ---------------------------------------------------------------------------
# 5. 失败降级 + 6. 游标幂等 + 7. 上限截断
# ---------------------------------------------------------------------------

class DegradeAndCursorPins(W9Base):
    def test_single_timeout_degrades_to_unknown_not_500(self):
        self.seed(3, stage2=("include",))
        stub = CrossrefStub()
        stub.route("w9k001", TimeoutError("upstream too slow"))
        stub.route("w9k002", b"{not json")
        stub.route("w9k003", updated_by("10.1000/w9k003", "10.1000/c03",
                                        "correction"))
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub):
            response = self.check({"scope": "stage2_include"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["done"])
        self.assertEqual(body["unknown_count"], 2)   # 超时 + 坏 JSON
        self.assertEqual(body["found_count"], 1)     # 另一条照常出结果
        self.assertEqual(body["processed"], 3)

    def test_storage_fault_is_503_not_500(self):
        """筛选员库被锁/IO 失败 → 503 结构化错误（r2 复审 HIGH：曾有 NameError→500）。"""
        self.seed(2, stage2=("include",))

        def locked(path):
            raise sqlite3.OperationalError("database is locked")

        with mock.patch.object(w9, "_connect_ro", locked):
            response = self.check({"scope": "stage2_include"})
        self.assertEqual(response.status_code, 503, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["error_code"], "storage_unavailable")
        self.assertEqual(detail["message_key"], "retraction.storage_unavailable")

    def test_storage_oserror_is_503_not_500(self):
        self.seed(1, stage2=("include",))

        def broken(path):
            raise OSError("disk gone")

        with mock.patch.object(w9, "_connect_ro", broken):
            response = self.check({"scope": "stage2_include"})
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["detail"]["error_code"],
                         "storage_unavailable")

    def test_upstream_http_error_is_unknown_not_500(self):
        self.seed(1, stage2=("include",))
        stub = CrossrefStub().route("w9k001", urllib.error.HTTPError(
            CROSSREF, 503, "boom", {}, None))
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub):
            response = self.check({"scope": "stage2_include"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["unknown_count"], 1)

    def test_batch_size_is_50_and_cursor_resumes(self):
        self.seed(120, stage2=("include",))
        stub = CrossrefStub()
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub):
            first = self.check({"scope": "stage2_include"}).json()
            self.assertFalse(first["done"])
            self.assertEqual(first["processed"], 50)
            self.assertEqual(first["processed_delta"], 50)
            self.assertEqual(first["total"], 120)
            self.assertRegex(first["cursor"], r"^\d+:\d+$")
            self.assertEqual(len(stub.calls), 50)

            # 正常续跑：游标 = 当前进度 → 处理下一批
            second = self.check({"scope": "stage2_include",
                                 "cursor": first["cursor"]}).json()
            self.assertEqual(second["processed"], 100)
            self.assertEqual(len(stub.calls), 100)

            # 断网重试：上一批响应丢失后重放**旧**游标 → 不重复出站、不重复计数
            replay = self.check({"scope": "stage2_include",
                                 "cursor": first["cursor"]}).json()
            self.assertEqual(len(stub.calls), 100, "重放旧游标不得再次出站")
            self.assertEqual(replay["processed"], 100)
            self.assertEqual(replay["processed_delta"], 0)
            self.assertEqual(replay["findings_delta"], 0)

            third = self.check({"scope": "stage2_include",
                                "cursor": second["cursor"]}).json()
            self.assertTrue(third["done"])
            self.assertIsNone(third["cursor"])
            self.assertEqual(third["processed"], 120)
            self.assertEqual(len(stub.calls), 120)

            # 已完成后再重放 → 幂等空操作
            again = self.check({"scope": "stage2_include",
                                "cursor": second["cursor"]}).json()
            self.assertTrue(again["done"])
            self.assertEqual(len(stub.calls), 120)

    def test_resume_by_run_id_after_page_reload(self):
        self.seed(120, stage2=("include",))
        stub = CrossrefStub()
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub):
            first = self.check({"scope": "stage2_include"}).json()
            resumed = self.check({"scope": "stage2_include",
                                  "run_id": first["run_id"]}).json()
            self.assertEqual(resumed["processed"], 100)
            # 刷新后不带游标再启动 → 409 指向在途 run（不并行重复出站）
            conflict = self.check({"scope": "stage2_include"})
            self.assertEqual(conflict.status_code, 409, conflict.text)
            detail = conflict.json()["detail"]
            self.assertEqual(detail["error_code"], "run_in_progress")
            self.assertEqual(detail["params"]["run_id"], first["run_id"])

    def test_stale_and_malformed_cursor_are_rejected(self):
        self.seed(60, stage2=("include",))
        stub = CrossrefStub()
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub):
            first = self.check({"scope": "stage2_include"}).json()
            run_id = first["run_id"]
            stale = self.check({"scope": "stage2_include",
                                "cursor": f"{run_id}:999"})
            self.assertEqual(stale.status_code, 409, stale.text)
            self.assertEqual(stale.json()["detail"]["error_code"], "cursor_stale")
            for bad in ("", "abc", "0:5", "-1:5", f"{run_id}:x"):
                response = self.check({"scope": "stage2_include", "cursor": bad})
                self.assertEqual(response.status_code, 400, (bad, response.text))
                self.assertEqual(response.json()["detail"]["error_code"],
                                 "cursor_invalid")
            both = self.check({"scope": "stage2_include", "cursor": first["cursor"],
                               "run_id": run_id})
            self.assertEqual(both.status_code, 400, both.text)
            self.assertEqual(both.json()["detail"]["error_code"], "cursor_conflict")

    def test_double_start_is_blocked_by_the_database(self):
        """并发双击：唯一部分索引保证同一任务只有一个在途 run（不靠进程内锁）。"""
        self.seed(120, stage2=("include",))
        stub = CrossrefStub()
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub):
            first = self.check({"scope": "stage2_include"}).json()
        self.assertEqual(first["status"], "running")
        collab = self.info.dir / "collaboration.db"
        with closing(sqlite3.connect(str(collab))) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO retraction_runs "
                    "(task_id, scope, status, started_by, started_at) "
                    "VALUES (?, 'stage2_include', 'running', 'x', 'y')",
                    (self.task,))
        # 跑完后同一个任务可以再开新 run
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub):
            payload, _ = self.run_to_end()
        self.assertTrue(payload["done"])
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub):
            again = self.check({"scope": "stage2_include"}).json()
        self.assertNotEqual(again["run_id"], payload["run_id"])

    def test_300_doi_cap_truncates_and_discloses(self):
        self.seed(310, stage2=("include",))
        stub = CrossrefStub()
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub):
            first = self.check({"scope": "stage2_include"}).json()
            self.assertEqual(first["total"], 300)
            self.assertTrue(first["truncated"])
            self.assertEqual(first["remaining_dois"], 10)
            self.assertEqual(first["offset"], 0)
            self.assertEqual(first["next_offset"], 300)
            tail, _ = self.run_to_end()
        self.assertTrue(tail["done"])
        detail = self.client.get(
            f"/api/tasks/{self.task}/retraction-runs/{first['run_id']}",
            headers=self.oauth).json()
        self.assertTrue(detail["run"]["truncated"])
        self.assertEqual(detail["run"]["remaining_dois"], 10)
        self.assertEqual(detail["run"]["next_offset"], 300)
        # K900 无 DOI 且未纳入 stage2 → eligible 恰为 310 条决定
        self.assertEqual(detail["run"]["eligible_count"], 310)

        # 「剩下的 10 条」确实可以继续查（r2 复审 MEDIUM）：用 next_offset 开新窗
        stub2 = CrossrefStub()
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub2):
            second, _ = self.run_to_end(offset=300)
        self.assertEqual(second["total"], 10)
        self.assertFalse(second["truncated"])
        self.assertEqual(second["next_offset"], None)
        queried = {url.rsplit("/", 1)[-1] for url, _ in stub2.calls}
        self.assertIn("10.1000%2Fw9k310", queried)
        self.assertIn("10.1000%2Fw9k301", queried)
        self.assertNotIn("10.1000%2Fw9k001", queried)


# ---------------------------------------------------------------------------
# 8. 去重键幂等
# ---------------------------------------------------------------------------

class DedupePins(W9Base):
    def test_same_finding_from_both_directions_is_one_row(self):
        """通告侧（update-to）与受影响侧（updated-by）解析到同一发现 → 一行。"""
        articles = [
            Article(zotero_key="K001", title="Retracted paper", authors="A",
                    journal="J", year=2020, doi="10.1000/target"),
            Article(zotero_key="K002", title="Retraction notice", authors="B",
                    journal="J", year=2024, doi="10.1000/notice"),
        ]
        db_mod.upsert_articles(self.owner_db, articles, [])
        for key in ("K001", "K002"):
            fulltext_mod.save_stage2(self.owner_db, key, "include")
        stub = CrossrefStub()
        # 目标论文：被通告更新
        stub.route("target", works("10.1000/target", **{"updated-by": [
            {"DOI": "10.1000/notice", "type": "retraction",
             "updated": {"date-parts": [[2024, 1, 1]]}}]}))
        # 通告论文：声明自己更新了目标
        stub.route("notice", works("10.1000/notice", **{"update-to": [
            {"DOI": "10.1000/target", "type": "retraction",
             "updated": {"date-parts": [[2024, 1, 1]]}}]}))
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub):
            payload, _ = self.run_to_end()
        self.assertEqual(payload["found_count"], 1, payload)
        detail = self.client.get(
            f"/api/tasks/{self.task}/retraction-runs/{payload['run_id']}",
            headers=self.oauth).json()
        self.assertEqual(len(detail["findings"]), 1)
        finding = detail["findings"][0]
        self.assertEqual(finding["zotero_key"], "K001")
        self.assertEqual(finding["doi"], "10.1000/target")
        self.assertEqual(finding["update_type"], "retraction")
        self.assertEqual(finding["source_doi"], "10.1000/notice")
        self.assertEqual(finding["url"], "https://doi.org/10.1000/notice")
        # 通告本身不被误标
        badge = self.client.get(f"/api/tasks/{self.task}/retraction-badge",
                                headers=self.oauth).json()
        self.assertNotIn("K002", badge["badges"])

    def test_duplicate_records_sharing_a_doi_all_get_badges(self):
        """重复题录共享同一 DOI：每一条都要拿到徽标，且只出站一次（r2 复审 MEDIUM）。"""
        articles = [
            Article(zotero_key="KA", title="Dup A", authors="A", journal="J",
                    year=2020, doi="10.1000/dup"),
            Article(zotero_key="KB", title="Dup B", authors="A", journal="J",
                    year=2020, doi="10.1000/dup"),
        ]
        db_mod.upsert_articles(self.owner_db, articles, [])
        for key in ("KA", "KB"):
            fulltext_mod.save_stage2(self.owner_db, key, "include")
        stub = CrossrefStub().route("dup", updated_by(
            "10.1000/dup", "10.1000/notice", "retraction"))
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub):
            payload, _ = self.run_to_end()
        self.assertEqual(len(stub.calls), 1, "同一 DOI 只出站一次")
        badge = self.client.get(f"/api/tasks/{self.task}/retraction-badge",
                                headers=self.oauth).json()
        self.assertEqual(sorted(badge["badges"]), ["KA", "KB"])
        self.assertEqual(payload["found_count"], 2)   # 两条题录各一行发现
        detail = self.client.get(
            f"/api/tasks/{self.task}/retraction-runs/{payload['run_id']}",
            headers=self.oauth).json()
        self.assertEqual(sorted(item["zotero_key"] for item in detail["findings"]),
                         ["KA", "KB"])

    def test_notice_in_early_batch_marks_target_from_later_batch(self):
        """第 1 批查到的通告指向第 2 批才检查的文献——发现必须现在就能归属。"""
        self.seed(60, stage2=("include",))
        stub = CrossrefStub().route("w9k001", works("10.1000/w9k001", **{
            "update-to": [{"DOI": "10.1000/w9k060", "type": "retraction",
                           "updated": {"date-parts": [[2025, 5, 1]]}}]}))
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub):
            first = self.check({"scope": "stage2_include"}).json()
            self.assertEqual(first["processed"], 50)
            self.assertEqual(first["found_count"], 1, first)
            detail = self.client.get(
                f"/api/tasks/{self.task}/retraction-runs/{first['run_id']}",
                headers=self.oauth).json()
            self.assertEqual(detail["findings"][0]["zotero_key"], "K060")
            self.assertEqual(detail["findings"][0]["source_doi"], "10.1000/w9k001")
            rest, _ = self.run_to_end()
        self.assertTrue(rest["done"])
        self.assertEqual(rest["found_count"], 1)   # 不重复

    def test_unknown_update_types_are_kept_at_lowest_severity(self):
        """未知类型**不得丢弃**（负责人裁定）：照常入库、最严重度=1（灰）。"""
        self.seed(1, stage2=("include",))
        stub = CrossrefStub().route("w9k001", updated_by(
            "10.1000/w9k001", "10.1000/addendum", "addendum"))
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub):
            payload, _ = self.run_to_end()
        self.assertEqual(payload["found_count"], 1, "未知类型不得被丢弃")
        self.assertEqual(payload["unknown_count"], 0)
        badge = self.client.get(f"/api/tasks/{self.task}/retraction-badge",
                                headers=self.oauth).json()
        item = badge["badges"]["K001"]
        self.assertEqual(item["type"], "addendum")
        self.assertEqual(item["severity"], 1)      # 与 correction 同级
        self.assertEqual(item["types"], ["addendum"])

    def test_retraction_family_types_are_red(self):
        """partial_retraction / withdrawal / removal 属撤稿类 → 严重度 3（负责人裁定）。"""
        self.seed(3, stage2=("include",))
        stub = CrossrefStub()
        stub.route("w9k001", updated_by("10.1000/w9k001", "10.1000/n1",
                                        "partial_retraction"))
        stub.route("w9k002", updated_by("10.1000/w9k002", "10.1000/n2",
                                        "withdrawal"))
        stub.route("w9k003", updated_by("10.1000/w9k003", "10.1000/n3",
                                        "removal"))
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub):
            payload, _ = self.run_to_end()
        self.assertEqual(payload["found_count"], 3)
        badge = self.client.get(f"/api/tasks/{self.task}/retraction-badge",
                                headers=self.oauth).json()
        self.assertEqual(badge["badges"]["K001"]["type"], "partial_retraction")
        self.assertEqual(badge["badges"]["K002"]["type"], "withdrawal")
        self.assertEqual(badge["badges"]["K003"]["type"], "removal")
        for key in ("K001", "K002", "K003"):
            self.assertEqual(badge["badges"][key]["severity"], 3, key)

    def test_withdrawal_beats_a_later_correction(self):
        """撤稿类不被后来的一条更正降级（负责人条件 3 的新类型版）。"""
        self.seed(1, stage2=("include",))
        stub = CrossrefStub().route("w9k001", updated_by(
            "10.1000/w9k001", "10.1000/withdraw", "withdrawal"))
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub):
            self.run_to_end()
        stub2 = CrossrefStub().route("w9k001", updated_by(
            "10.1000/w9k001", "10.1000/corr", "correction"))
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub2):
            self.run_to_end()
        badge = self.client.get(f"/api/tasks/{self.task}/retraction-badge",
                                headers=self.oauth).json()
        item = badge["badges"]["K001"]
        self.assertEqual(sorted(item["types"]), ["correction", "withdrawal"])
        self.assertEqual(item["type"], "withdrawal")
        self.assertEqual(item["severity"], 3)

    def test_unknown_type_never_outranks_a_retraction(self):
        """未知类型即使出现在更晚的 run，也不能盖过撤稿类。"""
        self.seed(1, stage2=("include",))
        stub = CrossrefStub().route("w9k001", updated_by(
            "10.1000/w9k001", "10.1000/retract", "retraction"))
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub):
            self.run_to_end()
        stub2 = CrossrefStub().route("w9k001", updated_by(
            "10.1000/w9k001", "10.1000/odd", "clarification"))
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub2):
            self.run_to_end()
        badge = self.client.get(f"/api/tasks/{self.task}/retraction-badge",
                                headers=self.oauth).json()
        item = badge["badges"]["K001"]
        self.assertEqual(item["type"], "retraction")
        self.assertEqual(item["severity"], 3)
        self.assertIn("clarification", item["types"])


# ---------------------------------------------------------------------------
# 9. scope 口径
# ---------------------------------------------------------------------------

class ScopePins(W9Base):
    def test_scope_follows_current_effective_decisions(self):
        self.seed(4, stage1=("include", "include", "exclude", "include"),
                  stage2=("include", "exclude", "include", None))
        stub = CrossrefStub()
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub):
            stage2, _ = self.run_to_end("stage2_include")
            calls_stage2 = len(stub.calls)
            stage1, _ = self.run_to_end("stage1_include")
        # stage2: K001 include, K002 exclude, K003 include → 2 条
        self.assertEqual(stage2["total"], 2)
        self.assertEqual(calls_stage2, 2)
        # stage1: K001,K002,K004 include（K003 exclude）→ 3 条
        self.assertEqual(stage1["total"], 3)

    def test_no_doi_entries_count_unknown_without_outbound(self):
        self.seed(2, stage2=("include",))
        fulltext_mod.save_stage2(self.owner_db, "K900", "include")
        stub = CrossrefStub()
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub):
            payload, _ = self.run_to_end()
        self.assertEqual(payload["total"], 3)
        self.assertEqual(payload["unknown_count"], 1)   # 无 DOI
        self.assertEqual(len(stub.calls), 2, "无 DOI 条目绝不出站")
        detail = self.client.get(
            f"/api/tasks/{self.task}/retraction-runs/{payload['run_id']}",
            headers=self.oauth).json()
        self.assertEqual(detail["run"]["checked_count"], 3)

    def test_empty_scope_is_a_valid_empty_run(self):
        payload = self.check({"scope": "stage2_include"}).json()
        self.assertEqual(payload["total"], 0)
        self.assertTrue(payload["done"])
        self.assertEqual(payload["status"], "done")

    def test_invalid_scope_rejected(self):
        response = self.check({"scope": "everything"})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["error_code"], "scope_invalid")
        extra = self.check({"scope": "stage2_include", "bogus": 1})
        self.assertEqual(extra.status_code, 422, extra.text)


# ---------------------------------------------------------------------------
# 10. 权限
# ---------------------------------------------------------------------------

class PermissionPins(W9Base):
    def test_member_cannot_trigger_but_can_read(self):
        self.seed(1, stage2=("include",))
        stub = CrossrefStub().route("w9k001", updated_by(
            "10.1000/w9k001", "10.1000/retract01", "retraction"))
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub):
            owned = self.check({"scope": "stage2_include"}).json()
            denied = self.check({"scope": "stage2_include"}, headers=self.mauth)
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(denied.json()["detail"]["error_code"], "owner_required")
        # 成员可读三件套
        runs = self.client.get(f"/api/tasks/{self.task}/retraction-runs",
                               headers=self.mauth)
        self.assertEqual(runs.status_code, 200, runs.text)
        self.assertEqual(runs.json()["count"], 1)
        detail = self.client.get(
            f"/api/tasks/{self.task}/retraction-runs/{owned['run_id']}",
            headers=self.mauth)
        self.assertEqual(detail.status_code, 200, detail.text)
        badge = self.client.get(f"/api/tasks/{self.task}/retraction-badge",
                                headers=self.mauth)
        self.assertEqual(badge.status_code, 200, badge.text)
        self.assertTrue(badge.json()["has_any"])

    def test_outsider_and_anonymous_are_rejected(self):
        self.seed(1, stage2=("include",))
        for headers in (self.xauth, {}):
            response = self.client.get(
                f"/api/tasks/{self.task}/retraction-runs", headers=headers)
            self.assertIn(response.status_code, (401, 403), response.text)
        denied = self.check({"scope": "stage2_include"}, headers=self.xauth)
        self.assertIn(denied.status_code, (401, 403), denied.text)

    def test_legacy_task_is_blocked_in_server_mode(self):
        """server 模式 legacy（无属主）任务：**既有全局闸门**先行拦截。

        实测返回 409 ``collaboration_owner_unset``（既有凭据/协作闸门的错误码，
        不是本模块的 ``retraction.legacy_owner_unset``）——本模块内的
        ``legacy_owner_unset`` 是纵深防御分支，在当前闸门排序下不可达（与 W8
        ``task_forbidden`` 同一处境）。本用例钉住真实可观测行为，避免文档声称
        一条走不到的路径。
        """
        self.seed(1, stage2=("include",))
        collab = self.info.dir / "collaboration.db"
        with closing(sqlite3.connect(str(collab))) as conn:
            try:
                conn.execute("DELETE FROM collaboration_task")
                conn.commit()
            except sqlite3.OperationalError:
                self.skipTest("协作库无 collaboration_task 表，跳过")
        for headers in (self.oauth, self.mauth):
            response = self.check({"scope": "stage2_include"}, headers=headers)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["detail"]["error_code"],
                             "collaboration_owner_unset")

    def test_unknown_task_and_run_are_404(self):
        self.assertEqual(self.client.get(
            "/api/tasks/nope-no-such-task/retraction-runs",
            headers=self.oauth).status_code, 404)
        self.seed(1, stage2=("include",))
        missing = self.client.get(
            f"/api/tasks/{self.task}/retraction-runs/9999", headers=self.oauth)
        self.assertEqual(missing.status_code, 404, missing.text)
        self.assertEqual(missing.json()["detail"]["error_code"], "run_not_found")
        malformed = self.client.get(
            f"/api/tasks/{self.task}/retraction-runs/abc", headers=self.oauth)
        self.assertEqual(malformed.status_code, 404, malformed.text)


# ---------------------------------------------------------------------------
# 11. 响应形状冻结（契约 §二）
# ---------------------------------------------------------------------------

class ResponseShapePins(W9Base):
    PROGRESS_KEYS = {
        "run_id", "scope", "status", "processed", "processed_delta", "total",
        "cursor", "done", "findings_delta", "unknown_delta", "found_count",
        "unknown_count", "truncated", "remaining_dois", "offset", "next_offset",
        "disclosure_keys", "message_key",
    }

    def test_progress_payload_shape(self):
        self.seed(2, stage2=("include",))
        stub = CrossrefStub()
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub):
            body = self.check({"scope": "stage2_include"}).json()
        self.assertEqual(set(body), self.PROGRESS_KEYS)
        self.assertEqual(body["disclosure_keys"], list(w9.DISCLOSURE_KEYS))
        self.assertEqual(body["message_key"], "retraction.check_done")

    def test_badge_and_runs_shape(self):
        self.seed(1, stage2=("include",))
        stub = CrossrefStub().route("w9k001", updated_by(
            "10.1000/w9k001", "10.1000/retract01", "retraction"))
        with mock.patch.object(pdf_fetch_api, "_OPENER", stub):
            payload = self.check({"scope": "stage2_include"}).json()
        badge = self.client.get(f"/api/tasks/{self.task}/retraction-badge",
                                headers=self.oauth).json()
        self.assertEqual(set(badge),
                         {"badges", "count", "has_any", "severity",
                          "disclosure_keys"})
        self.assertEqual(badge["severity"],
                         {"retraction": 3, "expression_of_concern": 2,
                          "correction": 1, "other": 1})
        item = badge["badges"]["K001"]
        self.assertEqual(set(item), {
            "zotero_key", "type", "severity", "doi", "latest_run_id",
            "latest_found_at", "source_doi", "source_date", "source_title",
            "url", "types"})
        runs = self.client.get(f"/api/tasks/{self.task}/retraction-runs",
                               headers=self.oauth).json()
        self.assertEqual(set(runs),
                         {"runs", "count", "active_run_id", "disclosure_keys"})
        detail = self.client.get(
            f"/api/tasks/{self.task}/retraction-runs/{payload['run_id']}",
            headers=self.oauth).json()
        self.assertEqual(set(detail), {"run", "findings", "finding_count",
                                       "disclosure_keys"})
        self.assertEqual(detail["finding_count"], 1)

    def test_badge_is_empty_before_any_run(self):
        self.seed(1, stage2=("include",))
        badge = self.client.get(f"/api/tasks/{self.task}/retraction-badge",
                                headers=self.oauth).json()
        self.assertEqual(badge["badges"], {})
        self.assertFalse(badge["has_any"])
        runs = self.client.get(f"/api/tasks/{self.task}/retraction-runs",
                               headers=self.oauth).json()
        self.assertEqual(runs["runs"], [])
        self.assertIsNone(runs["active_run_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
