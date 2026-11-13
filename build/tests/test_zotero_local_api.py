"""W1 · Zotero 本地 API 直连回归钉子（空白格计划 W1 / B5）。

覆盖（钉子=破坏即红）：
- 回环限定：BASE_URL 常量恒为 http://127.0.0.1:23119；带 scheme/非 /api 前缀的
  路径在发起任何请求前即被拒绝（伪造 opener 记录零调用）——用户输入无法把
  出站引向回环之外（隐私披露的机器可验证形态）；
- 失败优雅降级（真实 TestClient，仅替换 _OPENER 网络层）：不可达/超时/拒绝
  重定向/坏响应 → 三端点一律 4xx（409 引导）+ code，绝不 500；
- 条目转换：Zotero 条目 → 合成 RIS → 真实 parse_file 解析回 Article，
  键/题录字段逐项断言（ID 标签携带 Zotero 条目键，稳定重导语义）；
- 管线注入：POST /zotero-local/import 经 app.state.review_imports.upload()
  走受保护导入——条目落库、来源文件名溯源、重导幂等（精确去重不新增）、
  跨集合同条目去重、空集合/未知集合/超量集合/未登录/未知任务的 4xx 边界；
- 默认关：模块源不包含定时器/线程/后台出站调用（零自动触发面）。

伪造 Zotero：mock.patch.object(zotero_local_api, "_fetch_json")（成功路径）或
替换 _OPENER（失败路径，保留 _fetch_json/_http_get 全部生产逻辑），零真实网络。
"""
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
fixture = tempfile.TemporaryDirectory(prefix="rf_zotero_local_test_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient  # noqa: E402

from custom_backend import main as main_mod  # noqa: E402
from custom_backend.main import app  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend import zotero_local_api as zotero  # noqa: E402
from coscreen import db as db_mod  # noqa: E402
from coscreen.parsers import parse_file  # noqa: E402

DATA_ROOT = Path(main_mod.DATA_DIR)

PAGE_LIMIT = zotero.PAGE_LIMIT


# ---------------------------------------------------------------------------
# 伪造的本机 Zotero 库（真实 API JSON 形状）
# ---------------------------------------------------------------------------

def _collection(key, name, parent=False, num_items=0):
    return {"key": key, "version": 42, "library": {"type": "user"},
            "data": {"key": key, "name": name, "parentCollection": parent},
            "meta": {"numItems": num_items}}


def _item(key, item_type, *, title="", creators=(), **fields):
    data = {"itemType": item_type, "title": title,
            "creators": [dict(c) for c in creators]}
    data.update(fields)
    return {"key": key, "version": 7, "data": data}


CREATORS_1 = ({"creatorType": "author", "firstName": "Ada", "lastName": "Lovelace"},
              {"creatorType": "author", "name": "National Institute"})

FAKE_COLLECTIONS = [
    _collection("COLL001", "Trials", num_items=3),
    _collection("COLL002", "Nested", parent="COLL001", num_items=2),
    _collection("EMPTY01", "Empty"),
    _collection("PAGED000", "Paged", num_items=PAGE_LIMIT + 50),
]

FAKE_ITEMS = {
    "COLL001": [
        _item("ITEM0001", "journalArticle", title="Trial of Example Therapy",
              creators=CREATORS_1, publicationTitle="J Example",
              date="2024-03-01", DOI="10.1000/example",
              abstractNote="Background: example.", url="https://example.org/1"),
        _item("ITEM0002", "thesis", title="Thesis work",
              creators=({"creatorType": "author", "firstName": "Grace", "lastName": "Hopper"},),
              date="1999", abstractNote="Abstract.", url=""),
        _item("ITEM0003", "journalArticle", title="No DOI article",
              creators=(), publicationTitle="J Other", date="2025-01"),
    ],
    "COLL002": [
        # 与 COLL001 重叠的同一文献（跨集合去重钉子）
        _item("ITEM0001", "journalArticle", title="Trial of Example Therapy",
              creators=CREATORS_1, publicationTitle="J Example",
              date="2024-03-01", DOI="10.1000/example"),
        _item("ITEM0004", "book", title="A Book", date="2020",
              creators=({"creatorType": "author", "name": "Org Author"},)),
        # note 类型必须被跳过（/items/top 防御性双保险）
        _item("NOTE0001", "note", title="a note", note="x"),
    ],
    "EMPTY01": [],
    # 分页钉子：150 条 > 单页 100
    "PAGED000": [
        _item(f"PGD{i:04d}", "journalArticle", title=f"Paged article {i}")
        for i in range(PAGE_LIMIT + 50)
    ],
}


def fake_fetch_json(path: str):
    """按真实本地 API 的路径/查询串形状分发伪造响应（含分页）。"""
    parsed = urllib.parse.urlsplit(path)
    query = urllib.parse.parse_qs(parsed.query)
    if parsed.path == "/api/users/0/collections":
        if "start" not in query:          # 连接测试端点（limit=1，不带 start）
            return [FAKE_COLLECTIONS[0]]
        return FAKE_COLLECTIONS
    if parsed.path == "/api/users/0/collections/PAGED000/items/top":
        start = int(query["start"][0])
        return FAKE_ITEMS["PAGED000"][start:start + int(query["limit"][0])]
    for key, items in FAKE_ITEMS.items():
        if parsed.path == f"/api/users/0/collections/{key}/items/top":
            start = int(query.get("start", ["0"])[0])
            return items[start:start + int(query.get("limit", ["100"])[0])]
    raise AssertionError(f"伪造 Zotero 未覆盖路径: {path}")



# ---------------------------------------------------------------------------
# 失败矩阵 opener（保留 _fetch_json/_http_get 生产逻辑，只换网络层）
# ---------------------------------------------------------------------------

class _RecordingOpener:
    """记录 open() 调用；用于证明非法路径在任何出站前被拒绝。"""

    def __init__(self):
        self.calls = []

    def open(self, request, timeout=None):
        self.calls.append((request.full_url, timeout))
        raise AssertionError("RecordingOpener 不应被调用")


class _RefusedOpener:
    def open(self, request, timeout=None):
        raise urllib.error.URLError(OSError(10061, "Connection refused"))


class _TimeoutOpener:
    def open(self, request, timeout=None):
        raise TimeoutError("timed out")


class _RedirectOpener:
    def open(self, request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 302, "Found", {"Location": "http://127.0.0.1:23119/api/elsewhere"},
            io.BytesIO(b""))


class _BadJsonResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit=-1):
        return b"<html>not json</html>"


class _BadJsonOpener:
    def open(self, request, timeout=None):
        return _BadJsonResponse()


class _PayloadResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit=-1):
        return self.payload


class _PayloadOpener:
    """返回任意原始字节（合法 JSON / 非法嵌套 / 代理字符），生产解析链全走真实代码。"""

    def __init__(self, payload):
        self.payload = payload

    def open(self, request, timeout=None):
        return _PayloadResponse(self.payload)


class _RoutingPayloadOpener:
    """按请求 URL 返回不同原始字节；用于「真实 _fetch_json 解析 + 清洗」的多页场景。

    ``routes``：``[(子串, 原始字节), ...]``，按顺序第一个命中的子串胜出。"""

    def __init__(self, routes, default=b"[]"):
        self.routes = list(routes)
        self.default = default

    def open(self, request, timeout=None):
        url = request.full_url
        for needle, payload in self.routes:
            if needle in url:
                return _PayloadResponse(payload)
        return _PayloadResponse(self.default)


class _ProtocolErrorOpener:
    """R4#1：协议层异常（HTTPException 家族，**非 OSError 子类**）——127.0.0.1:23119
    是本机任意进程可抢占的固定端口，非 Zotero 服务时最常命中这族错误。"""

    def __init__(self, exc_type):
        self.exc_type = exc_type

    def open(self, request, timeout=None):
        raise self.exc_type("garbage from a non-Zotero process on 23119")


class ZoteroLocalApiNails(unittest.TestCase):
    """真实 TestClient；成功路径 mock _fetch_json，失败路径替换 _OPENER。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        user = auth_mod.create_user(
            f"zotero-owner-{id(cls):x}@example.test", "Zotero-Local-2026")
        cls.token = auth_mod.create_access_token(user["id"], user["username"])
        # workflow.context 消费 {user_id, username}（与 test_ai_suggest 口径一致）
        cls.user = {"user_id": user["id"], "username": user["username"]}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    def auth(self):
        return {"Authorization": "Bearer " + self.token}

    def _task(self) -> str:
        # 任务名按用例唯一（同名 409：已有同名任务）
        r = self.client.post("/api/tasks",
                             json={"name": f"zotero-local-{self._testMethodName}"},
                             headers=self.auth())
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["task_id"]

    def _articles(self, task_id: str) -> dict:
        ctx = app.state.review_workflow.context(task_id, self.user)
        return {a.zotero_key: a for a in db_mod.list_articles(ctx.owner_db)}

    # -- 红线：回环限定 ------------------------------------------------------

    def test_loopback_constants_and_no_escaping_paths(self):
        """出站地址恒为声明的回环常量；带 scheme/非 /api 路径在出站前被拒。"""
        self.assertEqual(zotero.BASE_URL, "http://127.0.0.1:23119")
        self.assertEqual((zotero.LOOPBACK_HOST, zotero.LOOPBACK_PORT),
                         ("127.0.0.1", 23119))
        recorder = _RecordingOpener()
        with mock.patch.object(zotero, "_OPENER", recorder):
            for bad in ("http://evil.example/api/x", "//evil.example/api/x",
                        "https://127.0.0.1:23119/api/x",
                        "/api/../../etc/passwd", "not-a-path"):
                with self.subTest(path=bad):
                    with self.assertRaises(zotero.ZoteroLocalError) as ctx:
                        zotero._http_get(bad)
                    self.assertEqual(ctx.exception.code, "zotero_bad_path")
        self.assertEqual(recorder.calls, [], "非法路径必须在任何出站前被拒绝")

    def test_bad_collection_key_rejected_before_fetch(self):
        """集合键形状校验先行——怪字符键不会拼进 URL。"""
        with mock.patch.object(zotero, "_fetch_json", side_effect=AssertionError("不得出站")):
            for bad in ("../escape", "A B/C", "x" * 33, ""):
                with self.subTest(key=bad):
                    with self.assertRaises(zotero.ZoteroLocalError) as ctx:
                        zotero.fetch_collection_items(bad)
                    self.assertEqual(ctx.exception.code, "zotero_bad_collection_key")

    def test_no_background_triggers_in_module_source(self):
        """默认关：模块源不包含定时器/线程/后台出站（零自动触发面）。"""
        source = Path(zotero.__file__).read_text(encoding="utf-8")
        for banned in ("threading", "Timer(", "schedule", "urlopen("):
            self.assertNotIn(banned, source, f"模块源不得出现 {banned!r}")

    # -- 红线：失败优雅降级（绝不 500） --------------------------------------

    def _assert_guide(self, response, code):
        self.assertEqual(response.status_code, 409, response.text)
        # FastAPI 包一层 detail；前端 api() 读 detail.code/detail.message 分流
        body = response.json()["detail"]
        self.assertEqual(body["code"], code)
        self.assertIn("message", body)
        self.assertEqual(body["endpoint"], "http://127.0.0.1:23119")

    def test_failure_matrix_is_graceful_4xx_never_500(self):
        """不可达/超时/重定向/坏响应 × 三端点 = 409 + code，绝不 500。"""
        task = self._task()
        matrix = [
            (_RefusedOpener(), "zotero_unreachable"),
            (_TimeoutOpener(), "zotero_timeout"),
            (_RedirectOpener(), "zotero_redirect"),
            (_BadJsonOpener(), "zotero_bad_response"),
        ]
        for opener, code in matrix:
            with mock.patch.object(zotero, "_OPENER", opener):
                with self.subTest(code=code):
                    self._assert_guide(
                        self.client.post(
                            f"/api/tasks/{task}/zotero-local/test",
                            headers=self.auth()), code)
                    self._assert_guide(
                        self.client.get(
                            f"/api/tasks/{task}/zotero-local/collections",
                            headers=self.auth()), code)
                    self._assert_guide(
                        self.client.post(
                            f"/api/tasks/{task}/zotero-local/import",
                            json={"collection_keys": ["COLL001"]},
                            headers=self.auth()), code)

    def test_read_error_body_uses_message_shape_for_frontend(self):
        """错误体 message/code 口径与前端 api() 结构化错误解析对齐。"""
        task = self._task()
        with mock.patch.object(zotero, "_OPENER", _RefusedOpener()):
            r = self.client.post(f"/api/tasks/{task}/zotero-local/test",
                                 headers=self.auth())
        self.assertEqual(r.status_code, 409, r.text)
        body = r.json()["detail"]
        self.assertIn("Zotero", body["message"])
        self.assertNotIsInstance(body["message"], dict)

    # -- 红线：条目转换（Zotero → RIS → Article） ----------------------------

    def test_item_conversion_roundtrip_through_real_parser(self):
        """合成 RIS 经真实 parse_file 解析回 Article：键与题录逐项断言。"""
        payload = zotero.items_to_ris_bytes([FAKE_ITEMS["COLL001"][0]])
        temp = Path(tempfile.mkdtemp(prefix="rf_zotero_ris_")) / "synthetic.ris"
        temp.write_bytes(payload)
        articles = parse_file(temp)
        self.assertEqual(len(articles), 1)
        article = articles[0]
        self.assertEqual(article.zotero_key, "ITEM0001", "ID 标签携带 Zotero 条目键")
        self.assertEqual(article.title, "Trial of Example Therapy")
        self.assertEqual(article.authors, "Lovelace, Ada; National Institute")
        self.assertEqual(article.journal, "J Example")
        self.assertEqual(article.year, 2024)
        self.assertEqual(article.doi, "10.1000/example")
        self.assertEqual(article.abstract, "Background: example.")
        self.assertEqual(article.url, "https://example.org/1")
        self.assertEqual(article.item_type, "JOUR")
        # 稳定来源键不得落成派生键（重导沿用同一键 → 去重语义与手工导出一致）
        self.assertNotIn("_generated_key", article.raw)
        self.assertNotIn("_parse_warnings", article.raw)

    def test_synthesized_ris_is_single_line_and_utf8(self):
        """多行字段压单行（防 rispy 续行误读）、UTF-8 可解码、无 NUL。"""
        payload = zotero.items_to_ris_bytes([
            FAKE_ITEMS["COLL001"][0],
            _item("ITEM0009", "journalArticle", title="CRLF\ntitle",
                  abstractNote="a\r\nb", creators=()),
        ])
        text = payload.decode("utf-8")
        self.assertNotIn("\r", text)
        for line in text.splitlines():
            self.assertNotIn("\t", line)
        self.assertEqual(text.count("TY  - "), 2)
        self.assertEqual(text.count("ER  -"), 2)

    def test_note_and_attachment_items_skipped(self):
        with mock.patch.object(zotero, "_fetch_json", fake_fetch_json):
            items = zotero.fetch_collection_items("COLL002")
        self.assertEqual([i["key"] for i in items], ["ITEM0001", "ITEM0004"],
                         "note/attachment 类型必须被跳过")

    def test_pagination_aggregates_beyond_one_page(self):
        with mock.patch.object(zotero, "_fetch_json", fake_fetch_json):
            items = zotero.fetch_collection_items("PAGED000")
        self.assertEqual(len(items), PAGE_LIMIT + 50)
        self.assertEqual(len({i["key"] for i in items}), PAGE_LIMIT + 50,
                         "分页不得引入重复条目")

    def test_pagination_cap_raises_instead_of_silently_truncating(self):
        """R4#2：确实超出分页上限 → 必须报错，绝不 200 + 静默丢尾部。

        Zotero 单库几千条是常态；旧实现第 50 页后直接 return，6000 条的库会
        静默只导入 5000 条且无任何提示。这里用**有限长度**的假库（上限+1），
        而不是「永远满页」——后者结构上无法区分「恰好到上限」与「还有更多」，
        正是 R5#1 指出的误拒未被覆盖的原因。"""
        capped = PAGE_LIMIT * zotero.MAX_PAGES_PER_REQUEST
        total = capped + 1

        def finite(path):
            start = int(urllib.parse.parse_qs(
                urllib.parse.urlsplit(path).query)["start"][0])
            limit = int(urllib.parse.parse_qs(
                urllib.parse.urlsplit(path).query)["limit"][0])
            return [{"key": f"K{i:06d}", "data": {"itemType": "journalArticle",
                                                  "title": f"t{i}"}}
                    for i in range(start, min(start + limit, total))]

        with mock.patch.object(zotero, "_fetch_json", finite):
            with self.assertRaises(zotero.ZoteroLocalError) as ctx:
                zotero.fetch_collection_items("COLL001")
        self.assertEqual(ctx.exception.code, "zotero_too_many_items")
        self.assertIn("分批", str(ctx.exception))
        # 端到端：端点必须给 409 引导而非 200
        task = self._task()
        with mock.patch.object(zotero, "_fetch_json", finite):
            r = self.client.post(f"/api/tasks/{task}/zotero-local/import",
                                 json={"collection_keys": ["COLL001"]},
                                 headers=self.auth())
        self._assert_guide(r, "zotero_too_many_items")

    def test_pagination_exactly_at_cap_is_accepted(self):
        """R5#1：恰好等于上限（50×100=5000）条必须正常导入，不得误拒。

        上一版在跑满 50 页后无条件抛错，而端点侧 ``total > MAX_ITEMS_PER_IMPORT``
        是严格大于（5000 合法）——两处冲突造成 off-by-one 误拒。修法是跑满后
        取 1 条探针：探针空 = 恰好到边界。钉子用「恰好 5000 条」的假库，
        旧实现必红（R4#2 引入的回归），删除探针分支同样必红。"""
        capped = PAGE_LIMIT * zotero.MAX_PAGES_PER_REQUEST

        def exactly_capped(path):
            parsed = urllib.parse.urlsplit(path)
            query = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/api/users/0/collections":
                return [{"key": "COLL001", "data": {"key": "COLL001", "name": "Capped"},
                         "meta": {"numItems": capped}}]
            start = int(query["start"][0])
            limit = int(query["limit"][0])
            return [{"key": f"K{i:06d}", "data": {"itemType": "journalArticle",
                                                  "title": f"t{i}"}}
                    for i in range(start, min(start + limit, capped))]

        with mock.patch.object(zotero, "_fetch_json", exactly_capped):
            items = zotero.fetch_collection_items("COLL001")
        self.assertEqual(len(items), capped, "恰好到上限必须完整返回，不得误拒")
        self.assertEqual(len({i["key"] for i in items}), capped)

        # 端点层：恰好 5000 条必须 200（MAX_ITEMS_PER_IMPORT 是严格大于判定）
        task = self._task()
        with mock.patch.object(zotero, "_fetch_json", exactly_capped):
            r = self.client.post(f"/api/tasks/{task}/zotero-local/import",
                                 json={"collection_keys": ["COLL001"]},
                                 headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["zotero"]["total_items"], capped)

    def test_provenance_name_survives_slashes_in_collection_name(self):
        """R5#2：集合名含 '/' 时，溯源文件名不得被 clean_upload_name 截断。

        真实库实测：``99（过往版本）-经典文献/理论基础`` 曾退化成
        ``理论基础 [6D8AAIUS].ris``——``Zotero`` 前缀与集合全名一起丢失，
        且 ``A/x`` 与 ``B/x`` 会撞成同名文件。开发机 253 个集合里有 8 个含 '/'。
        """
        cases = [
            ("Plain Name", "K0000001", "Zotero Plain Name [K0000001].ris"),
            ("Synthetic: A / B", "K0000002", "Zotero Synthetic- A - B [K0000002].ris"),
            ("RCT / observational", "K0000003", "Zotero RCT - observational [K0000003].ris"),
            ("2024/2025 search", "K0000004", "Zotero 2024-2025 search [K0000004].ris"),
            ("99（过往版本）-经典文献/理论基础", "K0000005",
             "Zotero 99（过往版本）-经典文献-理论基础 [K0000005].ris"),
        ]
        for name, key, expected in cases:
            with self.subTest(name=name):
                got = zotero._provenance_name(name, key, set())
                self.assertEqual(got, expected)
                # 关键判据：再经管线的 clean_upload_name 必须**原样**保留
                from custom_backend.security import clean_upload_name
                self.assertEqual(clean_upload_name(got), got,
                                 "清洗后名称不得再被 clean_upload_name 改写")

    def test_provenance_name_keeps_same_suffix_collections_distinct(self):
        """不同集合清洗后同名（A/x 与 B/x）必须仍可区分，绝不共用一个文件名。"""
        used: set[str] = set()
        first = zotero._provenance_name("A/x", "K0000001", used)
        second = zotero._provenance_name("B/x", "K0000002", used)
        self.assertNotEqual(first, second)
        for name in (first, second):
            self.assertTrue(name.startswith("Zotero "), name)
            self.assertIn("x", name)

    def test_import_provenance_round_trip_with_slash_collection(self):
        """端到端：含 '/' 的集合名导入后 raw.source_file 仍以 'Zotero ' 开头且
        含集合全名（清洗为 '-'），不被管线二次截断。"""
        import copy
        collections = [_collection("SLSH0001", "RCT / observational", num_items=2)]
        items = {"SLSH0001": [
            _item("SLSHITEM1", "journalArticle", title="Slash collection paper",
                  creators=CREATORS_1, publicationDate=None, date="2024",
                  DOI="10.1000/slash1"),
            _item("SLSHITEM2", "journalArticle", title="Second paper", date="2023",
                  DOI="10.1000/slash2"),
        ]}

        def fetch(path):
            parsed = urllib.parse.urlsplit(path)
            query = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/api/users/0/collections":
                return collections
            if parsed.path == "/api/users/0/collections/SLSH0001/items/top":
                start = int(query["start"][0])
                return items["SLSH0001"][start:start + int(query["limit"][0])]
            raise AssertionError(f"未覆盖路径: {path}")

        task = self._task()
        with mock.patch.object(zotero, "_fetch_json", fetch):
            r = self.client.post(f"/api/tasks/{task}/zotero-local/import",
                                 json={"collection_keys": ["SLSH0001"]},
                                 headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        expected = "Zotero RCT - observational [SLSH0001].ris"
        self.assertEqual(r.json()["files"][0]["name"], expected)
        for key, article in self._articles(task).items():
            source_file = json.loads(article.to_dict()["raw_json"])["source_file"]
            self.assertEqual(source_file, expected,
                             f"{key} 的溯源文件名被改写：{source_file}")
        self.assertGreater(len(collections), 0)
        self.assertTrue(copy.deepcopy(items))

    # -- R4#4：代理环境不得把本机库外发 --------------------------------

    def test_proxy_environment_does_not_hijack_loopback_traffic(self):
        """R4#4（R5#3 重修）：设 HTTP_PROXY 后，出站仍必须直连 127.0.0.1，代理零命中。

        urllib 默认读 http_proxy/HTTP_PROXY；企业/校园网普遍预设。若跟随代理，
        「不向任何外部地址发送数据」的常驻披露（zotero_import.js）即为假。
        先例 build/launcher.py:127 对同一回环探测用 ProxyHandler({})。

        **必须用子进程**：``_OPENER`` 是模块 import 期构造的，构造时按当时的
        代理环境装配 handler；在已导入的进程里 mock.patch.dict(os.environ) 对
        它毫无影响，那样的断言无论修复是否在场都恒绿（R5#3 实测）。真实部署中
        代理环境先于进程启动存在，因此这里把 HTTP_PROXY 放进子进程的 env 再
        import——撤掉 ProxyHandler({}) 时子进程会真的把请求交给代理，钉子即红。
        """
        hits = []

        class _Recorder(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                hits.append(self.path)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"[]")

            def log_message(self, *args):
                pass

        proxy = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
        thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        thread.start()
        proxy_url = f"http://127.0.0.1:{proxy.server_address[1]}"
        repo = Path(__file__).resolve().parents[2]
        # 子进程脚本：在「带代理环境」的进程里 import 生产模块再发一次请求。
        script = (
            "import sys\n"
            f"sys.path.insert(0, {str(repo)!r})\n"
            "from custom_backend import zotero_local_api as z\n"
            "try:\n"
            "    z._http_get('/api/users/0/collections')\n"
            "except z.ZoteroLocalError:\n"
            "    pass\n"
            "print('OK')\n"
        )
        env = {k: v for k, v in os.environ.items()
               if k.lower() not in ("http_proxy", "https_proxy", "no_proxy",
                                    "all_proxy")}
        env.update({"HTTP_PROXY": proxy_url, "HTTPS_PROXY": proxy_url,
                    "http_proxy": proxy_url, "https_proxy": proxy_url})
        try:
            proc = subprocess.run([sys.executable, "-I", "-c", script], cwd=str(repo),
                                  env=env, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=120)
        finally:
            proxy.shutdown()
            proxy.server_close()
            thread.join(timeout=5)
        self.assertIn("OK", proc.stdout or "", (proc.stdout or "") + (proc.stderr or ""))
        self.assertEqual(hits, [], "回环请求被代理接管（隐私披露将不成立）")

    # -- R4#1：协议层异常必须优雅降级 ----------------------------------

    def test_http_exception_family_degrades_to_409_never_500(self):
        """R4#1：BadStatusLine/InvalidURL/LineTooLong/UnknownProtocol/IncompleteRead
        **不是 OSError 子类**，旧 except 链漏捕 → 三端点全 500。

        旁证 ai_suggest_api.py:353（MM-09）同一缺陷已按 except HTTPException 修过。"""
        import http.client as http_client

        task = self._task()
        matrix = [http_client.BadStatusLine,
                  http_client.InvalidURL,
                  http_client.LineTooLong,
                  http_client.UnknownProtocol,
                  http_client.IncompleteRead]
        for exc_type in matrix:
            with self.subTest(exc=exc_type.__name__), \
                    mock.patch.object(zotero, "_OPENER", _ProtocolErrorOpener(exc_type)):
                self._assert_guide(
                    self.client.post(f"/api/tasks/{task}/zotero-local/test",
                                     headers=self.auth()), "zotero_bad_response")
                self._assert_guide(
                    self.client.get(f"/api/tasks/{task}/zotero-local/collections",
                                    headers=self.auth()), "zotero_bad_response")
                self._assert_guide(
                    self.client.post(f"/api/tasks/{task}/zotero-local/import",
                                     json={"collection_keys": ["COLL001"]},
                                     headers=self.auth()), "zotero_bad_response")
        # 断言这族异常确实不是 OSError（否则旧 except 已能兜住，钉子无意义）
        self.assertFalse(issubclass(http_client.BadStatusLine, OSError))
        self.assertFalse(issubclass(http_client.InvalidURL, OSError))

    def test_real_garbage_server_degrades_to_409_not_500(self):
        """R4#1 端到端：真在本机端口上跑一个只会吐垃圾行的服务器（非 Zotero），
        把 BASE_URL 指过去，其余全走生产代码——三端点必须 4xx，绝不 500。"""

        class _Garbage(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                self.wfile.write(b"NOT-HTTP/9.9 garbage\r\n\r\n")

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), _Garbage)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = server.server_address[1]
        task = self._task()
        try:
            with mock.patch.object(zotero, "BASE_URL", f"http://127.0.0.1:{port}"):
                for method, path, body in (
                        ("post", f"/api/tasks/{task}/zotero-local/test", None),
                        ("get", f"/api/tasks/{task}/zotero-local/collections", None),
                        ("post", f"/api/tasks/{task}/zotero-local/import",
                         {"collection_keys": ["COLL001"]})):
                    with self.subTest(path=path):
                        kwargs = {"headers": self.auth()}
                        if body is not None:
                            kwargs["json"] = body
                        r = getattr(self.client, method)(path, **kwargs)
                        self.assertEqual(r.status_code, 409, r.text)
                        self.assertNotEqual(r.status_code, 500)
                        self.assertIn("code", r.json()["detail"])
        finally:
            server.shutdown()
            server.server_close()

    # -- R8#W1-1/2：深嵌套与代理字符（RecursionError / UnicodeEncodeError → 500）--

    def test_real_server_with_deep_json_degrades_to_409_not_500(self):
        """真实回环响应：截断的深嵌套 JSON 必须在三个端点返回 409。

        解码器可能先触及递归上限，也可能读到末尾才发现 JSON 截断。
        两种解析错误都应映射为 zotero_bad_response。
        """

        class _DeepJson(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                body = b"[" * 1500 + b"]" * 1499
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), _DeepJson)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = server.server_address[1]
        base = f"http://127.0.0.1:{port}"
        task = self._task()
        try:
            with mock.patch.object(zotero, "BASE_URL", base):
                for method, path, body in (
                        ("post", f"/api/tasks/{task}/zotero-local/test", None),
                        ("get", f"/api/tasks/{task}/zotero-local/collections", None),
                        ("post", f"/api/tasks/{task}/zotero-local/import",
                         {"collection_keys": ["COLL001"]})):
                    with self.subTest(path=path):
                        kwargs = {"headers": self.auth()}
                        if body is not None:
                            kwargs["json"] = body
                        r = getattr(self.client, method)(path, **kwargs)
                        self.assertEqual(r.status_code, 409, r.text[:300])
                        detail = r.json()["detail"]
                        self.assertEqual(detail["code"], "zotero_bad_response")
                        self.assertEqual(detail["endpoint"], base)
        finally:
            server.shutdown()
            server.server_close()

    def test_json_recursion_error_degrades_to_409_never_500(self):
        """解析层必须把解码器抛出的 RecursionError 转成 ZoteroLocalError。

        仅模拟 Zotero 模块的解码器与网络，避免依赖解释器的递归上限。直调
        _fetch_json 能确认异常在解析层被处理，三条路由再验证 409 响应。
        """
        task = self._task()
        decoder = mock.Mock(wraps=json)
        decoder.loads.side_effect = RecursionError("JSON nesting limit exceeded")
        with mock.patch.object(zotero, "_OPENER", _PayloadOpener(b"[]")), \
                mock.patch.object(zotero, "json", decoder):
            with self.assertRaises(zotero.ZoteroLocalError) as ctx:
                zotero._fetch_json("/api/users/0/collections")
            self.assertEqual(ctx.exception.code, "zotero_bad_response")
            self._assert_guide(
                self.client.post(f"/api/tasks/{task}/zotero-local/test",
                                 headers=self.auth()), "zotero_bad_response")
            self._assert_guide(
                self.client.get(f"/api/tasks/{task}/zotero-local/collections",
                                headers=self.auth()), "zotero_bad_response")
            self._assert_guide(
                self.client.post(f"/api/tasks/{task}/zotero-local/import",
                                 json={"collection_keys": ["COLL001"]},
                                 headers=self.auth()), "zotero_bad_response")

    def test_route_level_backstop_catches_any_exception(self):
        """R8#W1-2 兜底层：三条路由各自在 ``except ZoteroLocalError`` 之后还有
        一道 ``except Exception`` → 409。它的价值是「下一个没想到的异常类型」
        也打不出 500（never-500 是书面契约）。这里用 ``_fetch_json`` 抛一个
        与 Zotero 完全无关的 ``RuntimeError`` 来验证：三端点仍是 409，不是 500。"""
        task = self._task()
        with mock.patch.object(zotero, "_fetch_json",
                               side_effect=RuntimeError("unexpected internal boom")):
            self._assert_guide(
                self.client.post(f"/api/tasks/{task}/zotero-local/test",
                                 headers=self.auth()), "zotero_bad_response")
            self._assert_guide(
                self.client.get(f"/api/tasks/{task}/zotero-local/collections",
                                headers=self.auth()), "zotero_bad_response")
            self._assert_guide(
                self.client.post(f"/api/tasks/{task}/zotero-local/import",
                                 json={"collection_keys": ["COLL001"]},
                                 headers=self.auth()), "zotero_bad_response")

    def test_deep_but_parseable_payload_is_depth_guarded(self):
        """R8#W1-1 的纵深防线：**能解析**的近阈值嵌套（实测 993 层可解析）不该被
        原样放行——解析成功只说明 json 模块吃得下，不代表下游（序列化 / 字符串
        拼接 / 未来的响应形态）吃得住。``_sanitize_payload`` 在
        ``MAX_PAYLOAD_DEPTH`` 处整体截断。

        诚实标注：**当前调用图里没有**一条已证实的「解析成功后再爆栈」500 路径
        （本组复现的 500 只有 RecursionError 与代理字符两条）；本用例钉的是深度
        守卫这一层防线本身，不是一条已观测的 500。"""
        # ① 单元层：超深子树被截断、浅层原样保留
        deep = "leaf"
        for _ in range(zotero.MAX_PAYLOAD_DEPTH + 5):
            deep = {"child": deep}
        washed = zotero._sanitize_payload({"a": deep, "b": "keep"})
        self.assertEqual(washed["b"], "keep")
        cursor, level = washed["a"], 0
        while isinstance(cursor, dict):
            cursor, level = cursor.get("child"), level + 1
        # 外层 dict 位于 depth 0，其值自 depth 1 起——保留到 MAX-1 层，再深即截断。
        self.assertEqual(level, zotero.MAX_PAYLOAD_DEPTH - 1)
        self.assertIsNone(cursor, "超过深度上限的子树必须整体替换为 None")

        # ② 接线层：``_fetch_json`` 生产链的返回值**已经洗过**（守卫真的接上了，
        #    而不是只存在于一个没人调用的函数里）。去掉 ``return _sanitize_payload(...)``
        #    这一行，本断言立刻红（未洗时数据字段仍是 200 层嵌套）。
        nested_raw = ('[{"data":' + ("[" * 200) + ("]" * 200) + '}]').encode()
        with mock.patch.object(zotero, "_OPENER", _PayloadOpener(nested_raw)):
            fetched = zotero._fetch_json("/api/users/0/collections")
        cursor, levels = fetched[0]["data"], 0
        while isinstance(cursor, list):
            cursor, levels = (cursor[0] if cursor else None), levels + 1
        self.assertLess(levels, zotero.MAX_PAYLOAD_DEPTH,
                        "超深子树必须在 _fetch_json 出口已截断")

        # ③ 端点层：近阈值但**可解析**的负载不得 500（200）
        nested = "[]"
        for _ in range(400):
            nested = "[" + nested + "]"
        payload = ('[{"key":"COLL001","data":{"key":"COLL001","name":"Deep",'
                   '"parentCollection":' + nested + '},"meta":{"numItems":1}}]')
        task = self._task()
        with mock.patch.object(zotero, "_OPENER",
                               _PayloadOpener(payload.encode("utf-8"))):
            r = self.client.get(f"/api/tasks/{task}/zotero-local/collections",
                                headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertEqual(r.json()["collections"][0]["name"], "Deep")

    def test_sanitizer_keeps_shallow_payloads_intact(self):
        """不误伤：浅结构（真实 Zotero 形状）经 ``_sanitize_payload`` 后完全不变。"""
        sample = {"key": "K", "data": {"itemType": "journalArticle",
                                       "title": "T", "creators": [{"name": "A"}],
                                       "tags": [{"tag": "x"}]},
                  "meta": {"numItems": 3}}
        self.assertEqual(zotero._sanitize_payload(sample), sample)
        self.assertEqual(zotero._sanitize_payload(None), None)
        self.assertEqual(zotero._sanitize_payload(7), 7)

    def test_lone_surrogates_degrade_to_readable_4xx_or_200_never_500(self):
        """R8#W1-2：JSON 里的 ``"\\ud800"`` 是合法转义，但 Python 得到的 str
        无法 UTF-8 编码——落进响应序列化 / RIS 合成就是 ``UnicodeEncodeError``
        （**不是 ValueError**）→ 500（本树实测三端点皆然）。

        修复后：集合名里的代理字符洗成 U+FFFD（200，仍可显示）；
        条目字段里的同理（导入成功 200）；**用户自己提交的**集合键含代理字符
        时给可读 409（不是 500）。"""
        task = self._task()
        # ① Zotero 侧的集合名含代理字符 → 200，代理字符被替换
        colls = (b'[{"key":"COLL001","data":{"key":"COLL001",'
                 b'"name":"bad \\ud800 name"},"meta":{"numItems":1}}]')
        with mock.patch.object(zotero, "_OPENER", _PayloadOpener(colls)):
            r = self.client.get(f"/api/tasks/{task}/zotero-local/collections",
                                headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertIn("\ufffd", r.json()["collections"][0]["name"])
        r.text.encode("utf-8")  # 响应体必须可 UTF-8 编码（旧代码在此抛）

        # ② 条目字段（title/abstract/creator）含代理字符 → 仍可导入（200）
        #    这里**不 mock _fetch_json**：让真实 _fetch_json → _sanitize_payload
        #    处理原始字节，钉子才能证明「清洗确实发生在生产解析链里」。
        items_raw = (b'[{"key":"ITEM0001","data":{"itemType":"journalArticle",'
                     b'"title":"bad \\ud800 title","abstractNote":"abs \\ud800",'
                     b'"creators":[{"name":"org \\ud800"}]}}]')
        colls_raw = (b'[{"key":"COLL001","data":{"key":"COLL001",'
                     b'"name":"Trials"},"meta":{"numItems":1}}]')
        opener = _RoutingPayloadOpener([
            ("/collections/COLL001/items/top", items_raw),
            ("/collections", colls_raw),
        ])
        with mock.patch.object(zotero, "_OPENER", opener):
            r = self.client.post(f"/api/tasks/{task}/zotero-local/import",
                                 json={"collection_keys": ["COLL001"]},
                                 headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text[:300])
        r.text.encode("utf-8")
        # 条目确实落库（不是被静默丢弃）
        self.assertEqual(r.json()["zotero"]["total_items"], 1)

    def test_client_supplied_surrogate_collection_key_never_500(self):
        """R8#W1-2 的另一半：**用户可控**输入同样不得打出 500。

        请求体 ``{"collection_keys": ["\\ud800"]}`` 是合法 JSON；旧代码把它拼进
        409 message → ``str(exc)`` 序列化抛 ``UnicodeEncodeError`` → 500
        （即使 Zotero 侧完全正常，本树实测复现）。修复后在入口就洗掉，
        得到可读 409 ``zotero_collection_not_found``。

        注：修复是**双保险**（入口 ``keys = [_sanitize_payload(...)]`` +
        ``_guide`` 里的 message 清洗），实测只删其中任一处本用例仍绿、两处
        全删才红——冗余是有意的，因为 message 也可能被未来的新 code 路径拼上
        用户输入。"""
        task = self._task()
        body = b'{"collection_keys":["\\ud800"]}'
        with mock.patch.object(zotero, "_fetch_json", return_value=[]):
            r = self.client.post(f"/api/tasks/{task}/zotero-local/import",
                                 content=body,
                                 headers={**self.auth(),
                                          "Content-Type": "application/json"})
        self.assertEqual(r.status_code, 409, r.text[:300])
        self.assertEqual(r.json()["detail"]["code"], "zotero_collection_not_found")
        r.text.encode("utf-8")

    # -- 管线注入（受保护导入全链路） ----------------------------------------

    def test_import_injects_through_protected_pipeline(self):
        """导入经 upload() 接缝：落库、溯源文件名、响应携带批次与 zotero 摘要。"""
        task = self._task()
        with mock.patch.object(zotero, "_fetch_json", fake_fetch_json):
            r = self.client.post(f"/api/tasks/{task}/zotero-local/import",
                                 json={"collection_keys": ["COLL001"]},
                                 headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        # 受保护导入响应（与 /import-batch 同形）：批次、解析计数、逐文件溯源
        self.assertIn("batch_id", body)
        self.assertEqual(body["total_parsed"], 3)
        self.assertEqual(body["imported"], 3)
        self.assertEqual(body["files"][0]["name"], "Zotero Trials [COLL001].ris")
        # 本模块附加的 Zotero 摘要
        self.assertEqual(body["zotero"]["total_items"], 3)
        self.assertEqual(body["zotero"]["collections"][0]["key"], "COLL001")
        articles = self._articles(task)
        self.assertEqual(set(articles), {"ITEM0001", "ITEM0002", "ITEM0003"})
        self.assertEqual(articles["ITEM0001"].doi, "10.1000/example")
        self.assertEqual(articles["ITEM0001"].year, 2024)
        self.assertIn("source_file",
                      json.loads(articles["ITEM0001"].to_dict()["raw_json"]),
                      "受保护管线的来源文件溯源必须生效")

    def test_reimport_is_idempotent_exact_dedup(self):
        """重导同一集合走精确去重：零新增行（去重管线白得）。"""
        task = self._task()
        with mock.patch.object(zotero, "_fetch_json", fake_fetch_json):
            first = self.client.post(f"/api/tasks/{task}/zotero-local/import",
                                     json={"collection_keys": ["COLL001"]},
                                     headers=self.auth())
            self.assertEqual(first.status_code, 200, first.text)
            second = self.client.post(f"/api/tasks/{task}/zotero-local/import",
                                      json={"collection_keys": ["COLL001"]},
                                      headers=self.auth())
        self.assertEqual(second.status_code, 200, second.text)
        body = second.json()
        self.assertEqual(body["total_parsed"], 3)
        # 去重管线的真实口径：重导零新行（new/activated_new 为 0，全部并入既有
        # existing=3），库内仍为 3 条（imported 语义=入库记录数，含重复行并入）。
        self.assertEqual(body["new"], 0, "重导不得产生新记录")
        self.assertEqual(body["activated_new"], 0)
        self.assertEqual(body["existing"], 3)
        self.assertEqual(body["current_screening_total"], 3)
        self.assertEqual(len(self._articles(task)), 3, "库内行数必须保持不变")

    def test_cross_source_dedup_manual_ris_then_zotero_direct(self):
        """R4#9：最常见的真实路径——先手工上传 Zotero 导出的 RIS（无 ID 标签，
        键为 auto-*），再用直连导入同一篇（带 ID）。库内行数必须不变。

        此前钉子只用 Zotero 键作身份，钉的是「ID 标签生效」而非「去重管线生效」。"""
        task = self._task()
        # 1) 手工 RIS：与 ITEM0001 同 DOI/标题，但**不含 ID 标签**
        manual = (
            "TY  - JOUR\n"
            "T1  - Trial of Example Therapy\n"
            "AU  - Lovelace, Ada\n"
            "JO  - J Example\n"
            "PY  - 2024\n"
            "DO  - 10.1000/example\n"
            "ER  - \n"
        ).encode("utf-8")
        r = self.client.post(f"/api/tasks/{task}/import",
                             files={"file": ("manual.ris", manual, "application/x-research-info-systems")},
                             headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        before = self._articles(task)
        self.assertEqual(len(before), 1)
        manual_key = next(iter(before))
        self.assertTrue(manual_key.startswith("auto-"), f"手工 RIS 应为派生键，实际 {manual_key}")

        # 2) 直连导入同一篇（带 ID=ITEM0001）
        with mock.patch.object(zotero, "_fetch_json", fake_fetch_json):
            r = self.client.post(f"/api/tasks/{task}/zotero-local/import",
                                 json={"collection_keys": ["COLL001"]},
                                 headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        after = self._articles(task)
        self.assertEqual(len(after), 3, "COLL001 共 3 条，其中 1 条与手工 RIS 同文献")
        self.assertIn(manual_key, after, "既有手工导入行必须保留（不得被覆盖成 Zotero 键）")
        # 去重管线的真实口径：new 计来源记录数，activated_new 才是新增规范行；
        # duplicates_against_db 证明重叠条目确实被识别为与库内既有行重复。
        self.assertEqual(body["activated_new"], 2, "只有 2 条是真正新增的规范行")
        self.assertEqual(body["duplicates_against_db"], 1,
                         "与手工 RIS 重叠的那条必须被去重管线判为库内重复")
        self.assertEqual(after[manual_key].doi, "10.1000/example")

    def test_cross_collection_overlap_deduped(self):
        """两集合含同一文献：单批导入只落一条（跨集合键去重先行）。"""
        task = self._task()
        with mock.patch.object(zotero, "_fetch_json", fake_fetch_json):
            r = self.client.post(f"/api/tasks/{task}/zotero-local/import",
                                 json={"collection_keys": ["COLL001", "COLL002"]},
                                 headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        # COLL001 3 条 + COLL002 新增 1 条（ITEM0001 重叠、note 跳过）
        self.assertEqual(body["zotero"]["total_items"], 4)
        self.assertEqual(len(self._articles(task)), 4)

    def test_import_boundaries_are_graceful_4xx(self):
        """空集合/未知集合/超量集合/未登录/未知任务 → 4xx，绝不 500。"""
        task = self._task()
        with mock.patch.object(zotero, "_fetch_json", fake_fetch_json):
            empty = self.client.post(f"/api/tasks/{task}/zotero-local/import",
                                     json={"collection_keys": ["EMPTY01"]},
                                     headers=self.auth())
            self.assertEqual(empty.status_code, 422, empty.text)
            self.assertEqual(empty.json()["detail"]["code"], "zotero_no_items")
            self.assertIn("message", empty.json()["detail"])
            missing = self.client.post(f"/api/tasks/{task}/zotero-local/import",
                                       json={"collection_keys": ["NOPE9999"]},
                                       headers=self.auth())
            self._assert_guide(missing, "zotero_collection_not_found")
            too_many = self.client.post(
                f"/api/tasks/{task}/zotero-local/import",
                json={"collection_keys": [f"C{i:07d}" for i in range(21)]},
                headers=self.auth())
            self.assertEqual(too_many.status_code, 422, too_many.text)
        unauth = self.client.post(f"/api/tasks/{task}/zotero-local/import",
                                  json={"collection_keys": ["COLL001"]})
        self.assertEqual(unauth.status_code, 401, unauth.text)
        ghost = self.client.post("/api/tasks/does-not-exist/zotero-local/test",
                                 headers=self.auth())
        self.assertEqual(ghost.status_code, 404, ghost.text)

    # -- R4#5：可见性门禁与 W4 对齐 ------------------------------------

    def test_endpoints_gate_on_task_visibility_like_w4(self):
        """R4#5：非参与者必须被端点自身拒绝（403），而不是全靠全局 require_user。

        与 main.py:5609（W4 study_design）同一契约：注入 task_visible 并在端点
        先判。本钉子直接调用注册函数、只注入一个恒 False 的 task_visible，
        证明门禁在模块内真实生效（撤掉该判定即红）。"""
        from fastapi import FastAPI

        probe = FastAPI()
        zotero.register_zotero_local_routes(
            probe, self._fake_require_user, lambda task_id: self._stub_info,
            lambda info, user: False)
        client = TestClient(probe)
        for method, path, body in (
                ("post", "/api/tasks/T1/zotero-local/test", None),
                ("get", "/api/tasks/T1/zotero-local/collections", None),
                ("post", "/api/tasks/T1/zotero-local/import",
                 {"collection_keys": ["COLL001"]})):
            with self.subTest(path=path):
                kwargs = {}
                if body is not None:
                    kwargs["json"] = body
                r = getattr(client, method)(path, **kwargs)
                self.assertEqual(r.status_code, 403, r.text)

    def test_production_registration_passes_visibility_gate(self):
        """生产注册行必须注入 _task_visible_to（不传 = 门禁静默失效）。"""
        source = (Path(__file__).resolve().parents[2]
                  / "custom_backend" / "main.py").read_text(encoding="utf-8")
        self.assertRegex(
            source,
            r"register_zotero_local_routes\(app, require_user, _task_or_404, _task_visible_to\)",
            "生产注册行必须显式注入 _task_visible_to（与 W4 study_design 同契约）")

    _stub_info = object()

    @staticmethod
    def _fake_require_user():
        return {"user_id": 1, "username": "stub"}

    def test_item_cap_is_graceful(self):
        """超单次导入条目上限 → 409 引导（分批提示），绝不 500。"""
        with mock.patch.object(zotero, "MAX_ITEMS_PER_IMPORT", PAGE_LIMIT + 50 - 1), \
                mock.patch.object(zotero, "_fetch_json", fake_fetch_json):
            task = self._task()
            r = self.client.post(f"/api/tasks/{task}/zotero-local/import",
                                 json={"collection_keys": ["PAGED000"]},
                                 headers=self.auth())
        self._assert_guide(r, "zotero_too_many_items")

    # -- 成功路径：三端点形状 ------------------------------------------------

    def test_connection_test_and_collections_endpoints(self):
        task = self._task()
        with mock.patch.object(zotero, "_fetch_json", fake_fetch_json):
            test = self.client.post(f"/api/tasks/{task}/zotero-local/test",
                                    headers=self.auth())
            self.assertEqual(test.status_code, 200, test.text)
            self.assertTrue(test.json()["ok"])
            self.assertEqual(test.json()["endpoint"], "http://127.0.0.1:23119")
            self.assertIsInstance(test.json()["latency_ms"], int)
            collections = self.client.get(
                f"/api/tasks/{task}/zotero-local/collections", headers=self.auth())
        self.assertEqual(collections.status_code, 200, collections.text)
        body = collections.json()
        self.assertEqual(body["count"], 4)
        names = {row["name"]: row for row in body["collections"]}
        self.assertEqual(names["Trials"]["num_items"], 3)
        self.assertEqual(names["Nested"]["parent"], "COLL001",
                         "父子关系保留，供前端层级缩进")


if __name__ == "__main__":
    unittest.main()
