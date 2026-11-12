"""W2 · PDF 自动获取（空白格 B1）后端钉子：mock opener 注入 + 治理红线 + r2 复审各项。

覆盖（plan.md AW2 / pdf_fetch_api.py 模块注释逐条对应）：
- 默认关：未启用时 fetch / fetch-batch / metadata-suggestions 一律 403
  ``pdf_fetch_disabled`` 且**零出站**（受控桩任何 open() 都算破坏即红）；
  **严格真值**：存量 JSON 里 enabled="false"/"0"/1/{"a":1} 一律按关（r2 复审④）；
- 成功路径：Unpaywall OA → 开放 PDF → check_pdf_bytes → save_pdf 落盘
  （fulltexts 行 + 磁盘单射文件）+ ``fulltext_added`` 轨迹事件（source=pdf_fetch）；
- 无 OA 路径：is_oa=false → 200 ``no_oa``，不落盘；
- 网络失败路径：URLError / TimeoutError / 30x（_NoRedirectHandler 语义）→
  502 ``pdf_fetch_upstream``（reason 结构化），绝不 500；
- **http.client 族**（r2 复审①）：BadStatusLine / IncompleteRead / HTTPException
  → 502 ``bad_response``，绝不 500（半开连接与反代脏响应的典型形态）；
- 坏响应路径：下载内容非 PDF → 422 ``pdf_fetch_invalid_pdf``；
- **请求级幂等键**（r2 复审①）：同一 X-Request-ID 下批量多篇各自派生 request_id
  → 全部 saved（修前第二篇起 TraceConflict→invalid）；同一 id 跨篇单篇请求亦然；
- 端点白名单（隐私红线）：PDF URL 只取 http/https 且拒绝 userinfo——
  file:// / ftp:// / 带凭据 URL 一律丢弃且不发起请求；
- **回环/私网拒绝**（r2 复审②）：PDF URL 指向 127.0.0.1/localhost/内网/解析到
  私网地址 → 422 ``pdf_fetch_unsafe_url``，服务端零请求该地址；
- **官方端点不可绕过**（r2 复审③）：https://API.unpaywall.org、:443、尾点、/. 变体
  判为官方→email 必填 422；http:// 明文官方→422 ``pdf_fetch_base_url_insecure``；
- **不静默覆盖**（r2 复审⑤）：已有全文时 409 ``pdf_fetch_would_replace``，带
  overwrite=1 才覆盖且 outcome.replaced=true；批量默认跳过 already_has_pdf；
- 设置：owner-only 写、email 校验、base_url 形状校验、expected_version 乐观锁
  （**省略时不 409**，r2 复审⑩）、settings 落盘、**落盘 OSError → 503**（r2 复审⑨）；
- Crossref 建议只读：返回缺失字段（DOI/年份/摘要）建议后 articles 行原样；
- 批量：混合 outcome 汇总（含 invalid/missing/blocked 全部计数，r2 复审⑥）+
  连续出站失败熔断（含 bad_response 形态，r2 复审⑥）。

出站以 mock opener 替换 ``pdf_fetch_api._OPENER``、DNS 以 ``_resolve_host`` 桩
替换（零联网；未声明端点即 AssertionError 破坏即红）。
"""
import http.client
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import urllib.error
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# r13 测试卫生：导入期需要临时数据目录来绑定导入期冻结的 main.DATA_DIR，但
# **必须保存原值**并在模块结束时恢复（tearDownModule）——不得把
# COBOOKSHELF_DATA_DIR / REVIEWFLOW_MODE 泄漏给同进程后续测试模块。
_ENV_KEYS = ("REVIEWFLOW_MODE", "COBOOKSHELF_DATA_DIR")
_ORIGINAL_ENV = {key: os.environ.get(key) for key in _ENV_KEYS}
fixture = tempfile.TemporaryDirectory(prefix="rf_pdf_fetch_test_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient

from custom_backend import main as main_mod
from custom_backend.main import app
from custom_backend import auth as auth_mod, pdf_fetch_api
from coscreen import db, fulltext as fulltext_mod, tasks as tasks_mod
from coscreen.models import Article

#: 与 main.DATA_DIR 同一绑定（全套 pytest 下其它模块会在收集期改写环境变量）。
DATA_ROOT = Path(main_mod.DATA_DIR)


def tearDownModule():
    """恢复导入期环境（r13 测试卫生）：不把临时目录/运行模式泄漏出去。"""
    for key, value in _ORIGINAL_ENV.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


UNPAYWALL = "https://api.unpaywall.org"
CROSSREF = "https://api.crossref.org"
#: 固定幂等键：前端 api() 对所有写请求恒带 X-Request-ID（index.html:5158）。
REQUEST_ID = "0f6f6c1e-9f2a-4a5a-8a6e-1c2b3d4e5f60"


def make_pdf(marker: str = "W2") -> bytes:
    """pypdf 生成真实可解析的一页 PDF（save_pdf 要求 ≥1 页）。"""
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_metadata({"/Title": marker})
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def fake_resolve(host, port=443):
    """DNS 桩：示例域解析为公网地址；回环/内网字面量保持原样（红线不靠真解析）。"""
    mapping = {
        "oa.example": ["93.184.216.34"],
        "api.unpaywall.org": ["104.18.0.1"],
        "api.crossref.org": ["104.16.0.1"],
        "example.test": ["93.184.216.35"],
        # 域名解析到回环：模拟 DNS 层面的内网指向（rebinding 常见形态）
        "sneaky.example": ["127.0.0.1"],
        # 终审 #30：域名解析回链路本地（云元数据常见形态）
        "metadata-ish.example": ["169.254.169.254"],
        # r2：域名解析回 IPv6 ULA（AWS IMDSv6 形态）
        "ula-ish.example": ["fd00:ec2::254"],
        # 自托管正钉：RFC1918 域名
        "selfhost.example": ["192.168.1.10"],
    }
    if host in mapping:
        return list(mapping[host])
    raise OSError("dns stub: unknown host " + host)


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


class RecordingOpener:
    """受控出站桩：按路由表回放并记录 (url, timeout, headers)。"""

    def __init__(self, routes):
        self.routes = routes          # list[(predicate, payload | Exception)]
        self.calls = []               # (url, timeout, header dict)

    def open(self, request, timeout=None):
        url = request.full_url
        self.calls.append((url, timeout, dict(request.header_items())))
        for predicate, payload in self.routes:
            if predicate(url):
                if isinstance(payload, Exception):
                    raise payload
                return FakeResponse(payload)
        raise AssertionError("出站触达未声明端点（隐私红线破坏）: " + url)


def unpaywall_json(doi_fragment: str, payload: dict):
    return (lambda url: url.startswith(UNPAYWALL + "/v2/") and doi_fragment in url,
            json.dumps(payload).encode("utf-8"))


def crossref_json(payload: dict):
    return (lambda url: url.startswith(CROSSREF + "/works"),
            json.dumps(payload).encode("utf-8"))


def pdf_route(payload: bytes):
    return (lambda url: url.startswith("https://oa.example/"), payload)


OA_SUCCESS = {"is_oa": True,
              "best_oa_location": {"url_for_pdf": "https://oa.example/success.pdf"},
              "oa_locations": []}
OA_NO = {"is_oa": False, "best_oa_location": None, "oa_locations": []}


class PdfFetchFixture:
    """共享夹具（非 TestCase：不被 pytest 单独收集，供两轮用例类复用）。"""

    #: 短标签：用户名与任务名都要拼进临时目录路径，Windows MAX_PATH 很敏感
    #: （trace 初次迁移还会在库名后再追加一长串 .bak.<随机>.tmp）。
    TAG = "w2"

    @classmethod
    def setUpClass(cls):
        # r13 测试卫生：类运行期保存环境并在收尾原样恢复（与 W4 同款）。别的
        # 模块可能在运行期改写 REVIEWFLOW_MODE（launcher 会改成 desktop），
        # 本类必须自己钉住 server 语义，且不把自己的覆盖泄漏给下一个类。
        cls._saved_env = {key: os.environ.get(key) for key in _ENV_KEYS}
        # 运行期把环境钉到**导入期冻结的共享根**（而不是本模块的临时目录）：
        # auth.create_user 每次现读 COBOOKSHELF_DATA_DIR，必须与 main.DATA_DIR
        # 指向同一处，否则用户库与任务库分叉。
        os.environ["COBOOKSHELF_DATA_DIR"] = str(main_mod.DATA_DIR)
        os.environ["REVIEWFLOW_MODE"] = "server"
        # main.DATA_DIR 导入期冻结，可能指向别人已删的临时目录：用前保证存在。
        Path(main_mod.DATA_DIR).mkdir(parents=True, exist_ok=True)
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.users = []
        # 用户名按测试类隔离：两个用例类在同一 session 里各自建号，重名会被
        # auth 的「筛选员数据路径冲突」守卫拒掉（slug 相同）。
        suffix = cls.TAG
        for i, name in enumerate(("owner", "peer", "outsider")):
            # 直接建用户签发令牌（不占 /api/auth/register 限流桶；先例 test_ai_suggest）。
            user = auth_mod.create_user(f"{name}-{suffix}-{i}@example.test", "Pdf-Fetch-2026")
            token = auth_mod.create_access_token(user["id"], user["username"])
            cls.users.append({"user_id": user["id"], "username": user["username"],
                              "token": token})

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        # 不 cleanup 数据目录（r13 测试卫生，与 W4 统一口径）：main.DATA_DIR 在
        # 导入期冻结且可能是**共享**目录，删掉会让同进程后续模块建任务/建用户
        # 直接 401/500。临时目录交给进程退出/GC 回收，同进程内保持可用。
        for key, value in cls._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def auth(self, i=0):
        return {"Authorization": "Bearer " + self.users[i]["token"]}

    def setUp(self):
        # 任务名带类前缀：两个用例类有同名方法，裸 _testMethodName 会撞任务名
        # （任务创建按显示名去重，第二个类整批 409 → KeyError: 'task_id'）。
        created = self.client.post(
            "/api/tasks",
            json={"name": f"{self.TAG}-{self._testMethodName}"},
            headers=self.auth(0)).json()
        self.assertIn("task_id", created, f"创建任务失败：{created}")
        self.task = created["task_id"]
        ctx = app.state.review_workflow.context(self.task, self.users[0])
        self.owner_db = Path(ctx.owner_db)
        db.upsert_articles(self.owner_db, [
            Article(zotero_key="A1", title="Open access success trial",
                    authors="Doe J", journal="J Open", doi="10.9999/success"),
            Article(zotero_key="A2", title="No OA trial", doi="10.9999/nooa"),
            Article(zotero_key="A3", title="Unreachable trial", doi="10.9999/dead"),
            Article(zotero_key="A4", title="Unreachable too", doi="10.9999/dead2"),
            Article(zotero_key="NODOI", title="No DOI article"),
        ], [])
        self.info = tasks_mod.find_task(self.task, str(DATA_ROOT))
        # 零路由桩：未启用/白名单外任何出站都直接 AssertionError（破坏即红）。
        self.opener = RecordingOpener([])
        for target, value in ((pdf_fetch_api, self.opener),):
            guard = mock.patch.object(target, "_OPENER", value)
            guard.start()
            self.addCleanup(guard.stop)
        dns_guard = mock.patch.object(pdf_fetch_api, "_resolve_host", fake_resolve)
        dns_guard.start()
        self.addCleanup(dns_guard.stop)

    def set_routes(self, *routes):
        self.opener.routes = list(routes)

    def enable(self, **overrides):
        body = {"enabled": True, "email": "team@example.test",
                "expected_version": 0, **overrides}
        return self.client.put(f"/api/tasks/{self.task}/pdf-fetch/settings",
                               json=body, headers=self.auth(0))

    def enable_now(self, **values):
        """让任务处于「已启用」且不推进 version（循环里避免反复 PUT 撞乐观锁）。"""
        self.write_settings_file(enabled=True, email="team@example.test",
                                 unpaywall_base_url=UNPAYWALL,
                                 crossref_base_url=CROSSREF, **values)

    def write_settings_file(self, **values):
        """直接改任务 JSON（模拟存量文件/手工改库/旧版本遗留）。"""
        path = self.info.dir / "pdf_fetch_settings.json"
        payload = {**pdf_fetch_api._DEFAULT_SETTINGS,
                   "enabled": True, "email": "team@example.test", "version": 5,
                   **values}
        path.write_text(json.dumps(payload), encoding="utf-8")
        return payload

    # -- 默认关（治理红线） --------------------------------------------------

    def test_default_off_blocks_all_and_never_opens(self):
        settings = self.client.get(f"/api/tasks/{self.task}/pdf-fetch/settings",
                                   headers=self.auth(0)).json()
        self.assertFalse(settings["enabled"], "新任务必须默认关")
        self.assertEqual(settings["outbound_endpoints"], [UNPAYWALL, CROSSREF])
        for path, kwargs in (
            (f"/api/tasks/{self.task}/fulltext/A1/fetch", {}),
            (f"/api/tasks/{self.task}/fulltext/fetch-batch",
             {"json": {"keys": ["A1"]}}),
        ):
            resp = self.client.post(path, headers=self.auth(0), **kwargs)
            self.assertEqual(resp.status_code, 403, path)
            self.assertEqual(resp.json()["detail"]["code"], "pdf_fetch_disabled")
        resp = self.client.get(f"/api/tasks/{self.task}/fulltext/A1/metadata-suggestions",
                               headers=self.auth(0))
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self.opener.calls, [], "未启用时绝不允许任何出站")

    def test_stored_truthy_noise_does_not_enable(self):
        """r2 复审④：存量/手改 JSON 的任意非空值不得把功能打开。"""
        for raw in ("false", "0", {"a": 1}, [], 2, "yes", None):
            self.write_settings_file(enabled=raw)
            resp = self.client.post(f"/api/tasks/{self.task}/fulltext/A1/fetch",
                                    headers=self.auth(0))
            self.assertEqual(resp.status_code, 403, f"enabled={raw!r} 必须按关处理")
            self.assertEqual(resp.json()["detail"]["code"], "pdf_fetch_disabled")
        self.assertEqual(self.opener.calls, [], "按关处理时绝不出站")
        for raw in (True, "true", "TRUE", 1):  # 复审白名单：True / "true" / 1
            self.write_settings_file(enabled=raw)
            settings = self.client.get(f"/api/tasks/{self.task}/pdf-fetch/settings",
                                       headers=self.auth(0)).json()
            self.assertTrue(settings["enabled"], f"enabled={raw!r} 应判为开")

    # -- 设置：owner-only / 校验 / 乐观锁 / 落盘 ------------------------------

    def test_settings_owner_only_and_validation(self):
        for i in (1, 2):  # 非参与者/外人：任务上下文 403
            resp = self.client.put(f"/api/tasks/{self.task}/pdf-fetch/settings",
                                   json={"enabled": True}, headers=self.auth(i))
            self.assertEqual(resp.status_code, 403)
        resp = self.enable(email="", enabled=True)
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["detail"]["code"], "pdf_fetch_email_required")
        resp = self.enable(email="not-an-email")
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["detail"]["code"], "pdf_fetch_email_invalid")
        resp = self.enable(unpaywall_base_url="ftp://127.0.0.1:8787")
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["detail"]["code"], "pdf_fetch_base_url_invalid")
        resp = self.enable()
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["enabled"])
        self.assertEqual(body["version"], 1)
        stored = json.loads((self.info.dir / "pdf_fetch_settings.json")
                            .read_text(encoding="utf-8"))
        self.assertTrue(stored["enabled"], "设置必须落盘任务 JSON")
        self.assertEqual(stored["email"], "team@example.test")
        # 乐观锁：重放同 version 必须 409。
        resp = self.enable(expected_version=0)
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["detail"]["code"],
                         "pdf_fetch_settings_version_conflict")

    def test_expected_version_omitted_skips_optimistic_lock(self):
        """r2 复审⑩：省略 expected_version 是新任务首次保存的合法调用，不得恒 409。"""
        resp = self.client.put(
            f"/api/tasks/{self.task}/pdf-fetch/settings",
            json={"enabled": True, "email": "team@example.test"},
            headers=self.auth(0))
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["version"], 1)

    def test_settings_write_oserror_is_503_not_500(self):
        """r2 复审⑨：设置落盘失败（磁盘满/只读）必须 503 而非 500。"""
        with mock.patch.object(pdf_fetch_api, "_atomic_write_json",
                               side_effect=OSError(28, "No space left on device")):
            resp = self.enable()
        self.assertEqual(resp.status_code, 503, resp.text)
        self.assertEqual(resp.json()["detail"]["code"], "pdf_fetch_storage")

    def test_official_endpoint_variants_cannot_bypass_email(self):
        """r2 复审③：官方端点的规范化变体不得绕过 email 必填与 https 约束。"""
        variants = ("https://API.unpaywall.org", "https://api.unpaywall.org:443",
                    "https://api.unpaywall.org.", "https://api.unpaywall.org/.")
        for base in variants:
            resp = self.enable(unpaywall_base_url=base, email="")
            self.assertEqual(resp.status_code, 422, f"{base} 必须要求 email")
            self.assertEqual(resp.json()["detail"]["code"], "pdf_fetch_email_required")
        # 明文打官方：拒绝（会泄露 DOI/email 且违反条款）
        resp = self.enable(unpaywall_base_url="http://api.unpaywall.org")
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["detail"]["code"], "pdf_fetch_base_url_insecure")

    def test_runtime_email_guard_covers_tampered_settings(self):
        # 存量文件被改成「官方端点 + 空 email」：运行时防线必须补位 422 零出站。
        self.write_settings_file(unpaywall_base_url=UNPAYWALL, email="")
        resp = self.client.post(f"/api/tasks/{self.task}/fulltext/A1/fetch",
                                headers=self.auth(0))
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["detail"]["code"], "pdf_fetch_email_missing")
        self.assertEqual(self.opener.calls, [])

    # -- 成功路径：落盘 + fulltexts 行 + 轨迹事件 ------------------------------

    def test_fetch_success_saves_pdf_and_trace_event(self):
        from contextlib import closing
        from coscreen.research_trace import TraceContext, ensure_default_recording
        # closing()：sqlite3 的 with 只管事务不关连接——Windows 上残留连接会锁住
        # WAL/SHM 文件，把类级 TemporaryDirectory.cleanup() 炸成 teardown ERROR。
        with closing(sqlite3.connect(self.owner_db)) as conn:
            ensure_default_recording(conn, TraceContext(
                task_id=self.task, actor_id=self.users[0]["user_id"], source="pdf_fetch"))
        self.assertEqual(self.enable().status_code, 200)
        self.set_routes(unpaywall_json("10.9999%2Fsuccess", OA_SUCCESS),
                        pdf_route(make_pdf("W2-success")))
        resp = self.client.post(f"/api/tasks/{self.task}/fulltext/A1/fetch",
                                headers=self.auth(0))
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["outcome"], "saved")
        self.assertEqual(body["n_pages"], 1)
        self.assertFalse(body["replaced"], "首次获取不应标记 replaced")
        self.assertEqual(body["oa_url"], "https://oa.example/success.pdf")
        meta = fulltext_mod.get_fulltext(self.owner_db, "A1")
        self.assertIsNotNone(meta, "fulltexts 行必须落库")
        self.assertTrue(Path(meta.path).exists(), "PDF 必须落盘")
        self.assertTrue(Path(meta.path).read_bytes().startswith(b"%PDF-"))
        # 出站形状钉子：显式 UA、短超时、Unpaywall email 参数、顺序正确。
        self.assertEqual(len(self.opener.calls), 2)
        api_url, api_timeout, api_headers = self.opener.calls[0]
        pdf_url, pdf_timeout, pdf_headers = self.opener.calls[1]
        self.assertEqual(api_url,
                         f"{UNPAYWALL}/v2/10.9999%2Fsuccess?email=team%40example.test")
        self.assertEqual(api_headers.get("User-agent"), pdf_fetch_api.USER_AGENT)
        self.assertEqual(pdf_headers.get("User-agent"), pdf_fetch_api.USER_AGENT)
        self.assertEqual(api_timeout, pdf_fetch_api.API_TIMEOUT_SECONDS)
        self.assertEqual(pdf_timeout, pdf_fetch_api.PDF_TIMEOUT_SECONDS)
        self.assertTrue(pdf_url.startswith("https://oa.example/"))
        with closing(sqlite3.connect(self.owner_db)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT action, subject_id, source FROM trace_events "
                "WHERE subject_id='A1' AND stage='fulltext'").fetchone()
        self.assertIsNotNone(row, "save_pdf 必须留 fulltext_added 轨迹事件")
        self.assertEqual(row["action"], "fulltext_added")
        self.assertEqual(row["source"], "pdf_fetch")

    def test_fetch_with_request_id_still_saves(self):
        """前端写请求恒带 X-Request-ID：单篇路径必须照常成功。"""
        self.assertEqual(self.enable().status_code, 200)
        self.set_routes(unpaywall_json("10.9999%2Fsuccess", OA_SUCCESS),
                        pdf_route(make_pdf("W2-rid-single")))
        resp = self.client.post(f"/api/tasks/{self.task}/fulltext/A1/fetch",
                                headers={**self.auth(0), "X-Request-ID": REQUEST_ID})
        self.assertEqual(resp.status_code, 200, resp.text)

    # -- 无 OA / 网络失败 / 30x / http.client 族 / 坏 PDF ----------------------

    def test_fetch_no_oa(self):
        self.assertEqual(self.enable().status_code, 200)
        self.set_routes(unpaywall_json("10.9999%2Fnooa", OA_NO))
        resp = self.client.post(f"/api/tasks/{self.task}/fulltext/A2/fetch",
                                headers=self.auth(0))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["outcome"], "no_oa")
        self.assertIsNone(fulltext_mod.get_fulltext(self.owner_db, "A2"))
        self.assertEqual(len(self.opener.calls), 1)

    def test_fetch_unreachable_is_502_not_500(self):
        self.assertEqual(self.enable().status_code, 200)
        self.set_routes((lambda url: url.startswith(UNPAYWALL),
                         urllib.error.URLError("name resolution failed")))
        resp = self.client.post(f"/api/tasks/{self.task}/fulltext/A3/fetch",
                                headers=self.auth(0))
        self.assertEqual(resp.status_code, 502, resp.text)
        detail = resp.json()["detail"]
        self.assertEqual(detail["code"], "pdf_fetch_upstream")
        self.assertEqual(detail["reason"], "unreachable")

    def test_fetch_timeout_is_502(self):
        self.assertEqual(self.enable().status_code, 200)
        self.set_routes((lambda url: url.startswith(UNPAYWALL), TimeoutError()))
        resp = self.client.post(f"/api/tasks/{self.task}/fulltext/A3/fetch",
                                headers=self.auth(0))
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(resp.json()["detail"]["reason"], "timeout")

    def test_redirect_is_rejected(self):
        self.assertEqual(self.enable().status_code, 200)
        redirect = urllib.error.HTTPError(
            "https://unpaywall.example/elsewhere", 302, "Found", None, None)
        self.set_routes((lambda url: url.startswith(UNPAYWALL), redirect))
        resp = self.client.post(f"/api/tasks/{self.task}/fulltext/A1/fetch",
                                headers=self.auth(0))
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(resp.json()["detail"]["reason"], "upstream_status")
        # 先例钉子（ai_suggest_api.py:301 同款）：handler 显式拒绝跟随重定向。
        self.assertIsNone(pdf_fetch_api._NoRedirectHandler().redirect_request(
            None, None, 302, "Found", None, "https://elsewhere.example/"))

    def test_http_client_exceptions_are_502_not_500(self):
        """r2 复审①：半开连接/反代脏响应（http.client 族）不得 500。"""
        raisers = {
            "badstatus": http.client.BadStatusLine("NOT-HTTP"),
            "incomplete": http.client.IncompleteRead(b"partial", 100),
            "httpexception": http.client.HTTPException("chunked decode failed"),
            "remote_disconnected": http.client.RemoteDisconnected("remote closed"),
        }
        for name, exc in raisers.items():
            with self.subTest(name=name):
                self.enable_now()
                self.set_routes((lambda url: url.startswith(UNPAYWALL), exc))
                resp = self.client.post(f"/api/tasks/{self.task}/fulltext/A3/fetch",
                                        headers=self.auth(0))
                self.assertEqual(resp.status_code, 502,
                                 f"{name} 必须降级 502，实际 {resp.status_code}: {resp.text[:200]}")
                detail = resp.json()["detail"]
                self.assertEqual(detail["code"], "pdf_fetch_upstream")
                self.assertIn(detail["reason"], ("unreachable", "bad_response"))

    def test_fetch_invalid_pdf_is_422(self):
        self.assertEqual(self.enable().status_code, 200)
        self.set_routes(unpaywall_json("10.9999%2Fsuccess", OA_SUCCESS),
                        (lambda url: url.startswith("https://oa.example/"),
                         b"<html>This is not a PDF</html>"))
        resp = self.client.post(f"/api/tasks/{self.task}/fulltext/A1/fetch",
                                headers=self.auth(0))
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertEqual(resp.json()["detail"]["code"], "pdf_fetch_invalid_pdf")
        self.assertIsNone(fulltext_mod.get_fulltext(self.owner_db, "A1"))

    # -- 端点白名单（隐私红线）：只访问声明端点 + 公网 PDF 链接 ----------------

    def test_pdf_url_whitelist_drops_dangerous_schemes(self):
        self.assertEqual(self.enable().status_code, 200)
        self.set_routes(unpaywall_json("10.9999%2Fsuccess", {
            "is_oa": True,
            "best_oa_location": None,
            "oa_locations": [
                {"url_for_pdf": "file:///etc/passwd"},
                {"url_for_pdf": "ftp://oa.example/paper.pdf"},
                {"url_for_pdf": "https://user:secret@oa.example/paper.pdf"},
            ],
        }))
        resp = self.client.post(f"/api/tasks/{self.task}/fulltext/A1/fetch",
                                headers=self.auth(0))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["outcome"], "no_oa")
        self.assertEqual(len(self.opener.calls), 1,
                         "危险 scheme/userinfo 的 PDF URL 必须被丢弃，不得出站")

    def test_loopback_and_private_pdf_urls_are_refused(self):
        """r2 复审②：上游响应指向回环/私网时不得把服务端当内网探针。"""
        for target in ("http://127.0.0.1:9/internal-secret",
                       "http://localhost:9/internal-secret",
                       "http://10.0.0.5/internal-secret",
                       "http://[::1]:9/internal-secret",
                       "http://169.254.169.254/latest/meta-data",
                       "https://sneaky.example/internal-secret"):
            with self.subTest(target=target):
                self.enable_now()
                self.opener.calls.clear()
                self.set_routes(unpaywall_json("10.9999%2Fsuccess", {
                    "is_oa": True,
                    "best_oa_location": {"url_for_pdf": target},
                    "oa_locations": [],
                }))
                resp = self.client.post(f"/api/tasks/{self.task}/fulltext/A1/fetch",
                                        headers=self.auth(0))
                self.assertEqual(resp.status_code, 422, resp.text)
                self.assertEqual(resp.json()["detail"]["code"], "pdf_fetch_unsafe_url")
                self.assertEqual(len(self.opener.calls), 1,
                                 "只允许 Unpaywall 那一次出站，内部地址绝不被请求")
                self.assertFalse(any("internal-secret" in url or "meta-data" in url
                                     for url, _t, _h in self.opener.calls))

    def test_self_hosted_loopback_base_url_is_used(self):
        resp = self.enable(unpaywall_base_url="http://127.0.0.1:8787", email="")
        self.assertEqual(resp.status_code, 200, resp.text)  # 自托管可免 email
        self.assertFalse(resp.json()["official_endpoint"])
        self.set_routes(
            (lambda url: url.startswith("http://127.0.0.1:8787/v2/"),
             json.dumps(OA_SUCCESS).encode("utf-8")),
            pdf_route(make_pdf("W2-selfhost")))
        resp = self.client.post(f"/api/tasks/{self.task}/fulltext/A1/fetch",
                                headers=self.auth(0))
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["outcome"], "saved")
        self.assertTrue(self.opener.calls[0][0].startswith("http://127.0.0.1:8787/v2/"),
                        "自托管 base_url 必须生效（回环示范）")

    # -- 不静默覆盖 -----------------------------------------------------------

    def test_existing_pdf_requires_explicit_overwrite(self):
        """r2 复审⑤：已有全文时先 409，确认后 overwrite=1 才覆盖。"""
        from contextlib import closing
        with closing(sqlite3.connect(self.owner_db)) as conn, conn:
            pass
        original = make_pdf("original-upload")
        pdfs = self.info.dir / "pdfs"
        pdfs.mkdir(parents=True, exist_ok=True)
        first = fulltext_mod.save_pdf(self.owner_db, "A1", original, pdfs_dir=pdfs)
        self.assertEqual(self.enable().status_code, 200)
        self.set_routes(unpaywall_json("10.9999%2Fsuccess", OA_SUCCESS),
                        pdf_route(make_pdf("fetched-replacement")))
        resp = self.client.post(f"/api/tasks/{self.task}/fulltext/A1/fetch",
                                headers=self.auth(0))
        self.assertEqual(resp.status_code, 409, resp.text)
        detail = resp.json()["detail"]
        self.assertEqual(detail["code"], "pdf_fetch_would_replace")
        self.assertEqual(detail["existing"]["sha256"], first.sha256)
        self.assertEqual(fulltext_mod.get_fulltext(self.owner_db, "A1").sha256, first.sha256,
                         "未确认时既有全文必须原样保留")
        self.assertEqual(len(self.opener.calls), 0, "409 前不得出站")
        # 显式确认后才覆盖
        resp = self.client.post(f"/api/tasks/{self.task}/fulltext/A1/fetch?overwrite=1",
                                headers=self.auth(0))
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["replaced"])
        self.assertNotEqual(fulltext_mod.get_fulltext(self.owner_db, "A1").sha256,
                            first.sha256)

    def test_batch_skips_existing_by_default(self):
        from contextlib import closing
        with closing(sqlite3.connect(self.owner_db)) as conn, conn:
            pass
        pdfs = self.info.dir / "pdfs"
        pdfs.mkdir(parents=True, exist_ok=True)
        fulltext_mod.save_pdf(self.owner_db, "A1", make_pdf("already"), pdfs_dir=pdfs)
        self.assertEqual(self.enable().status_code, 200)
        self.set_routes(unpaywall_json("10.9999%2Fnooa", OA_NO))
        resp = self.client.post(f"/api/tasks/{self.task}/fulltext/fetch-batch",
                                json={"keys": ["A1", "A2"]}, headers=self.auth(0))
        self.assertEqual(resp.status_code, 200)
        results = {r["zotero_key"]: r for r in resp.json()["results"]}
        self.assertEqual(results["A1"]["outcome"], "skipped")
        self.assertEqual(results["A1"]["reason"], "already_has_pdf")
        self.assertEqual(results["A2"]["outcome"], "no_oa")

    # -- 单篇入口校验 ---------------------------------------------------------

    def test_doi_missing_and_article_missing(self):
        self.assertEqual(self.enable().status_code, 200)
        resp = self.client.post(f"/api/tasks/{self.task}/fulltext/NODOI/fetch",
                                headers=self.auth(0))
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["detail"]["code"], "pdf_fetch_doi_missing")
        resp = self.client.post(f"/api/tasks/{self.task}/fulltext/GHOST/fetch",
                                headers=self.auth(0))
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["detail"]["code"], "pdf_fetch_article_missing")
        self.assertEqual(self.opener.calls, [], "校验失败不得出站")

    def test_non_participant_denied(self):
        self.assertEqual(self.enable().status_code, 200)
        for i in (1, 2):
            resp = self.client.post(f"/api/tasks/{self.task}/fulltext/A1/fetch",
                                    headers=self.auth(i))
            self.assertEqual(resp.status_code, 403)
        resp = self.client.get(f"/api/tasks/{self.task}/pdf-fetch/settings",
                               headers=self.auth(2))
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self.opener.calls, [])

    def test_paused_stage_blocks_write(self):
        """r2 复审⑪：阶段暂停 = 只读，补全文不得例外写入。"""
        self.assertEqual(self.enable().status_code, 200)
        state = self.client.get(f"/api/tasks/{self.task}/workflow-settings",
                                headers=self.auth(0)).json()
        stages = {}
        for stage, value in state["stages"].items():
            stages[stage] = {"mode": "paused" if stage == "fulltext" else value["mode"],
                             "source_ids": value.get("source_ids", []),
                             "show_maybe": value.get("show_maybe", True)}
            if stage == "coding":
                stages[stage].pop("show_maybe", None)
        put = self.client.put(f"/api/tasks/{self.task}/workflow-settings",
                              json={"stages": stages,
                                    "evidence_required_field_ids":
                                        state.get("evidence_required_field_ids") or [],
                                    "expected_version": state["version"]},
                              headers=self.auth(0))
        self.assertEqual(put.status_code, 200, put.text)
        resp = self.client.post(f"/api/tasks/{self.task}/fulltext/A1/fetch",
                                headers=self.auth(0))
        self.assertEqual(resp.status_code, 409, resp.text)
        self.assertEqual(resp.json()["detail"]["code"], "stage_paused")
        self.assertEqual(self.opener.calls, [], "门禁拒绝时不得出站")

    # -- Crossref 只读建议（绝不写库） ----------------------------------------

    def test_metadata_suggestions_read_only(self):
        self.assertEqual(self.enable().status_code, 200)
        self.set_routes(crossref_json({
            "message": {"DOI": "10.9999/success",
                        "abstract": "<jats:p>Structured abstract text.</jats:p>",
                        "issued": {"date-parts": [[2020]]}}}))
        resp = self.client.get(
            f"/api/tasks/{self.task}/fulltext/A1/metadata-suggestions",
            headers=self.auth(0))
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["read_only"])
        self.assertEqual(body["suggestions"],
                         {"abstract": "Structured abstract text.", "year": 2020})
        self.assertNotIn("doi", body["suggestions"], "已有 DOI 不重复建议")
        # 只读钉子：articles 行必须原样（缺少年份/摘要保持缺失）。
        after = db.get_article(self.owner_db, "A1")
        self.assertEqual(after.year, None)
        self.assertEqual(after.abstract, "")

    def test_metadata_suggestions_title_search_for_doi_backfill(self):
        self.assertEqual(self.enable().status_code, 200)
        self.set_routes(crossref_json({
            "message": {"items": [
                {"DOI": "10.9999/found", "title": ["No DOI Article"]},
                {"DOI": "10.9999/wrong", "title": ["Completely Different Paper"]},
            ]}}))
        resp = self.client.get(
            f"/api/tasks/{self.task}/fulltext/NODOI/metadata-suggestions",
            headers=self.auth(0))
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["matched_by"], "title")
        self.assertEqual(body["suggestions"].get("doi"), "10.9999/found")
        self.assertEqual(db.get_article(self.owner_db, "NODOI").doi, "",
                         "建议绝不回写 articles.doi")

    def test_metadata_suggestions_null_or_list_message_is_200(self):
        """r13 终扫低危 be-outbound-1：Crossref ``message`` 为 null/list 时不得 500。

        DOI 路径早有 isinstance 守卫，标题检索路径旧写法 ``payload.get("message", {})``
        在键存在但值为 None/list 时拿到 None/list 再 ``.get`` → AttributeError →
        路由只捕 OutboundError → 500。这里要求 200 + 空建议（优雅降级）。
        """
        self.enable_now()
        for payload in ({"message": None}, {"message": []}):
            with self.subTest(payload=payload):
                self.set_routes(crossref_json(payload))
                resp = self.client.get(
                    f"/api/tasks/{self.task}/fulltext/NODOI/metadata-suggestions",
                    headers=self.auth(0))
                self.assertEqual(resp.status_code, 200, resp.text)
                body = resp.json()
                self.assertEqual(body["suggestions"], {})
                self.assertEqual(body["matched_by"], "none")
                self.assertTrue(body["read_only"])

    # -- 批量：混合 outcome + 熔断 + 幂等键 -----------------------------------

    def test_batch_mixed_outcomes(self):
        self.assertEqual(self.enable().status_code, 200)
        self.set_routes(
            unpaywall_json("10.9999%2Fsuccess", OA_SUCCESS),
            unpaywall_json("10.9999%2Fnooa", OA_NO),
            (lambda url: "10.9999%2Fdead" in url,
             urllib.error.URLError("down")),
            pdf_route(make_pdf("W2-batch")))
        resp = self.client.post(f"/api/tasks/{self.task}/fulltext/fetch-batch",
                                json={"keys": ["A1", "A2", "A3", "NODOI", "GHOST"]},
                                headers=self.auth(0))
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertFalse(body["aborted"])
        self.assertEqual(body["summary"],
                         {"saved": 1, "no_oa": 1, "failed": 1, "skipped": 1,
                          "missing": 1, "invalid": 0, "blocked": 0,
                          "unresolvable": 0, "trace_conflict": 0})
        outcomes = {r["zotero_key"]: r["outcome"] for r in body["results"]}
        self.assertEqual(outcomes["A1"], "saved")
        self.assertEqual(outcomes["A2"], "no_oa")
        self.assertEqual(outcomes["A3"], "failed")
        self.assertEqual(outcomes["NODOI"], "skipped")
        self.assertEqual(outcomes["GHOST"], "missing")

    def test_batch_with_request_id_saves_every_key(self):
        """r2 复审①（高·阻断）：真实前端形态（同请求多篇）必须全部落盘。

        修前：所有 key 共用同一 request_id → save_pdf 的 begin_write_request
        在第二篇抛 TraceConflict → 被当成坏 PDF → summary {saved:1, invalid:3}。
        """
        from contextlib import closing
        from coscreen.research_trace import TraceContext, ensure_default_recording
        with closing(sqlite3.connect(self.owner_db)) as conn:
            ensure_default_recording(conn, TraceContext(
                task_id=self.task, actor_id=self.users[0]["user_id"], source="pdf_fetch"))
        self.assertEqual(self.enable().status_code, 200)
        keys = ["A1", "A2", "A3", "A4"]
        routes = []
        for suffix, key in enumerate(keys):
            routes.append(unpaywall_json(
                f"10.9999%2F{('success', 'nooa', 'dead', 'dead2')[suffix]}",
                OA_SUCCESS))
        # 全部 DOI 都命中同一个 OA PDF（各自内容不同，便于核对落盘）
        self.set_routes(*routes,
                        (lambda url: url.startswith("https://oa.example/"),
                         make_pdf("W2-batch-rid")))
        resp = self.client.post(f"/api/tasks/{self.task}/fulltext/fetch-batch",
                                json={"keys": keys},
                                headers={**self.auth(0), "X-Request-ID": REQUEST_ID})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        outcomes = {r["zotero_key"]: r for r in body["results"]}
        self.assertEqual(outcomes["A1"]["outcome"], "saved", outcomes["A1"])
        for key in ("A2", "A3", "A4"):
            self.assertEqual(outcomes[key]["outcome"], "saved", outcomes[key])
        self.assertEqual(body["summary"]["invalid"], 0,
                         "TraceConflict 不得再被当成坏 PDF")
        # 轨迹必须为每篇各留一条 fulltext_added（不同 subject）
        with closing(sqlite3.connect(self.owner_db)) as conn:
            rows = conn.execute(
                "SELECT subject_id, request_id FROM trace_events "
                "WHERE stage='fulltext' AND action='fulltext_added'").fetchall()
        subjects = {row[0] for row in rows}
        self.assertEqual(subjects, set(keys), "四篇都必须落轨迹")
        request_ids = {row[1] for row in rows}
        self.assertEqual(len(request_ids), len(keys),
                         "每个 key 派生独立 request_id（{rid}:{key}）")

    def test_batch_circuit_breaker_on_consecutive_unreachable(self):
        self.assertEqual(self.enable().status_code, 200)
        self.set_routes(
            (lambda url: "10.9999%2Fdead" in url, urllib.error.URLError("down")),
            unpaywall_json("10.9999%2Fsuccess", OA_SUCCESS),
            pdf_route(make_pdf("W2-breaker")))
        resp = self.client.post(f"/api/tasks/{self.task}/fulltext/fetch-batch",
                                json={"keys": ["A3", "A4", "A1"]},
                                headers=self.auth(0))
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["aborted"], "连续不可达必须熔断")
        results = {r["zotero_key"]: r for r in body["results"]}
        self.assertEqual(results["A3"]["outcome"], "failed")
        self.assertEqual(results["A4"]["outcome"], "failed")
        self.assertEqual(results["A1"]["outcome"], "skipped")
        self.assertEqual(results["A1"]["reason"], "aborted_upstream_unreachable")

    def test_batch_single_unreachable_does_not_abort(self):
        self.assertEqual(self.enable().status_code, 200)
        self.set_routes(
            (lambda url: "10.9999%2Fdead" in url, urllib.error.URLError("down")),
            unpaywall_json("10.9999%2Fsuccess", OA_SUCCESS),
            pdf_route(make_pdf("W2-breaker2")))
        resp = self.client.post(f"/api/tasks/{self.task}/fulltext/fetch-batch",
                                json={"keys": ["A3", "A1"]},
                                headers=self.auth(0))
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["aborted"], "单次不可达不得熔断剩余条目")
        outcomes = {r["zotero_key"]: r["outcome"] for r in body["results"]}
        self.assertEqual(outcomes, {"A3": "failed", "A1": "saved"})

    def test_batch_breaker_also_trips_on_bad_response(self):
        """r2 复审⑥：集体坏响应（反代拦截）同样说明上游不可用，应计入熔断。"""
        self.assertEqual(self.enable().status_code, 200)
        self.set_routes((lambda url: url.startswith(UNPAYWALL),
                         http.client.BadStatusLine("NOT-HTTP")))
        resp = self.client.post(f"/api/tasks/{self.task}/fulltext/fetch-batch",
                                json={"keys": ["A1", "A2", "A3", "A4"]},
                                headers=self.auth(0))
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["aborted"])
        self.assertEqual(body["summary"]["failed"], 2)
        self.assertEqual(body["summary"]["skipped"], 2)


# ---------------------------------------------------------------------------
# r3 复审钉子（第 3 轮）
# ---------------------------------------------------------------------------

class PdfFetchTests(PdfFetchFixture, unittest.TestCase):
    """第 1–2 轮后端钉子。"""

    TAG = "w2a"


class PdfFetchRound3Tests(PdfFetchFixture, unittest.TestCase):

    TAG = "w2b"
    """r3 复审十项：CGNAT 元数据面、代理绕过、官方端点端口、幂等键长度、
    读库/门禁 500、成员邮箱掩码、unresolvable 分流、AssertionError 不吞。"""

    def test_cgnat_metadata_address_is_refused(self):
        """r3 复审①（高）：100.64.0.0/10 既非 private 也非 reserved，旧判据放行。

        阿里云 ECS 元数据面 100.100.100.200 必须 422 且**零请求该地址**。
        """
        import ipaddress
        self.assertFalse(ipaddress.ip_address("100.100.100.200").is_private,
                         "前提：Python 认为 CGNAT 非 private（旧判据的洞）")
        self.enable_now()
        for target in ("http://100.100.100.200/latest/meta-data",
                       "http://100.64.0.1/latest/meta-data",
                       "http://100.127.255.254/latest/meta-data"):
            with self.subTest(target=target):
                self.opener.calls.clear()
                self.set_routes(unpaywall_json("10.9999%2Fsuccess", {
                    "is_oa": True, "best_oa_location": {"url_for_pdf": target},
                    "oa_locations": []}))
                resp = self.client.post(f"/api/tasks/{self.task}/fulltext/A1/fetch",
                                        headers=self.auth(0))
                self.assertEqual(resp.status_code, 422, resp.text)
                self.assertEqual(resp.json()["detail"]["code"], "pdf_fetch_unsafe_url")
                self.assertEqual(len(self.opener.calls), 1,
                                 "只允许 Unpaywall 那一次出站")
                self.assertFalse(any("meta-data" in url for url, _t, _h in self.opener.calls))

    def test_public_documentation_ranges_are_still_refused(self):
        """is_global 白名单口径同时覆盖文档段/保留段（旧判据靠 is_private 兜住）。"""
        self.enable_now()
        for target in ("http://192.0.2.1/x", "http://198.51.100.1/x", "http://203.0.113.1/x",
                       "http://0.0.0.0/x", "http://240.0.0.1/x"):
            with self.subTest(target=target):
                self.opener.calls.clear()
                self.set_routes(unpaywall_json("10.9999%2Fsuccess", {
                    "is_oa": True, "best_oa_location": {"url_for_pdf": target},
                    "oa_locations": []}))
                resp = self.client.post(f"/api/tasks/{self.task}/fulltext/A1/fetch",
                                        headers=self.auth(0))
                self.assertEqual(resp.status_code, 422, f"{target}: {resp.text[:120]}")

    def test_opener_ignores_proxy_environment(self):
        """r3 复审②（中）：出站必须直连——不得因 http(s)_proxy 把 DOI/email 发给代理。"""
        import urllib.request as _urllib

        def proxy_routing(opener):
            # ProxyHandler 的 http_open/https_open 只有在 proxies 非空时才注册进
            # opener（空 dict 时不加任何方法），故用「有没有代理路由方法」判定，
            # 而不是看 handlers 里有没有 ProxyHandler 这个类型。
            return sorted(name for name, _func in opener.handle_open.items()
                          for _h in opener.handle_open[name]
                          if type(_h).__name__ == "ProxyHandler")

        with mock.patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:9",
                                          "HTTPS_PROXY": "http://127.0.0.1:9",
                                          "http_proxy": "http://127.0.0.1:9"}):
            default_opener = _urllib.build_opener()
            our_opener = pdf_fetch_api._build_opener()
            self.assertTrue(proxy_routing(default_opener),
                            "前提：默认 opener 在设置代理环境变量后会走代理")
            self.assertEqual(proxy_routing(our_opener), [],
                             "本模块 opener 必须忽略 HTTP(S)_PROXY（空 ProxyHandler）")
        self.assertTrue(any(type(h).__name__ == "_NoRedirectHandler"
                            for h in our_opener.handlers), "拒绝重定向仍需保留")
        # 行为钉：设置 HTTP_PROXY 后受控桩仍被直接调用（不经过任何代理层）
        self.enable_now()
        self.set_routes(unpaywall_json("10.9999%2Fsuccess", OA_SUCCESS),
                        pdf_route(make_pdf("W2-proxy")))
        with mock.patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:9",
                                          "HTTPS_PROXY": "http://127.0.0.1:9"}):
            resp = self.client.post(f"/api/tasks/{self.task}/fulltext/A1/fetch",
                                    headers=self.auth(0))
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["outcome"], "saved")
        self.assertEqual(len(self.opener.calls), 2, "两次出站都直达受控桩")

    def test_official_endpoint_ignores_port_in_identity(self):
        """r3 复审③（中）：:80 / :8443 变体不得绕过 email 与 https 双约束。"""
        for base in ("http://api.unpaywall.org:80", "http://API.Unpaywall.org:80",
                     "https://api.unpaywall.org:8443", "https://api.unpaywall.org:80"):
            with self.subTest(base=base):
                resp = self.enable(unpaywall_base_url=base, email="")
                self.assertEqual(resp.status_code, 422, f"{base} 必须被拒")
                self.assertEqual(resp.json()["detail"]["code"],
                                 "pdf_fetch_base_url_insecure")
                self.opener.calls.clear()
        # https + 443 形态仍可启用（且判为官方、要求 email）
        self.assertEqual(self.enable(unpaywall_base_url="https://api.unpaywall.org:443",
                                     email="").json()["detail"]["code"],
                         "pdf_fetch_email_required")

    def test_long_article_key_still_saves_with_request_id(self):
        """r3 复审④（中）：key >=164 时 request_id 超 200 上限会永久 422。

        导入侧 build_zotero_key 原样保留来源 ID，长键可达；派生必须散列有界。
        """
        from coscreen.research_trace import TraceContext
        long_key = "K" * 300
        db.upsert_articles(self.owner_db, [Article(
            zotero_key=long_key, title="Long key article", doi="10.9999/longkey")], [])
        self.enable_now()
        self.set_routes(unpaywall_json("10.9999%2Flongkey", OA_SUCCESS),
                        unpaywall_json("10.9999%2Fboundary", OA_SUCCESS),
                        pdf_route(make_pdf("W2-longkey")))
        resp = self.client.post(
            f"/api/tasks/{self.task}/fulltext/{long_key}/fetch",
            headers={**self.auth(0), "X-Request-ID": REQUEST_ID})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["outcome"], "saved")
        self.assertIsNotNone(fulltext_mod.get_fulltext(self.owner_db, long_key))
        # 派生的 request_id 必须落在既有 200 字符预算内
        context = pdf_fetch_api._key_trace_context(
            TraceContext(task_id=self.task, actor_id=self.users[0]["user_id"],
                         request_id=REQUEST_ID), long_key)
        self.assertLessEqual(len(context.request_id), 200)
        self.assertNotIn(long_key, context.request_id, "不得原样拼接长 key")

        # 对照：边界 163/164 两档都必须成功（旧实现 164 起红）。
        # 每档用**独立请求 id**：同一 request_id 下同库多键写不同内容会被幂等表
        # 判为重放冲突（这是既有语义，不是本组缺陷）。
        for length in (163, 164):
            key = "B" * length
            db.upsert_articles(self.owner_db, [Article(
                zotero_key=key, title="Boundary key", doi="10.9999/boundary")], [])
            resp = self.client.post(
                f"/api/tasks/{self.task}/fulltext/{key}/fetch",
                headers={**self.auth(0),
                         "X-Request-ID": uuid.uuid4().hex[:8] + "-0000-4000-8000-000000000000"})
            self.assertEqual(resp.status_code, 200, f"len={length}: {resp.text[:160]}")

    def test_storage_failures_in_read_and_gate_paths_are_503(self):
        """r3 复审⑤（中）：读库/门禁在 try 之外 -> 旧实现 500。"""
        self.enable_now()
        cases = [
            ("get_article", mock.patch.object(pdf_fetch_api.db_mod, "get_article",
                                              side_effect=sqlite3.OperationalError("database is locked"))),
            ("get_fulltext", mock.patch.object(pdf_fetch_api.fulltext_mod, "get_fulltext",
                                               side_effect=sqlite3.DatabaseError("malformed"))),
            ("writable_reason", mock.patch.object(
                type(app.state.review_workflow), "writable_reason",
                side_effect=sqlite3.OperationalError("database is locked"))),
        ]
        for name, patcher in cases:
            with self.subTest(name=name):
                with patcher:
                    resp = self.client.post(f"/api/tasks/{self.task}/fulltext/A1/fetch",
                                            headers=self.auth(0))
                self.assertEqual(resp.status_code, 503,
                                 f"{name} 必须降级 503，实际 {resp.status_code}: {resp.text[:200]}")
                self.assertEqual(resp.json()["detail"]["code"], "pdf_fetch_storage")
        # 批量：逐条 failed/storage，整体仍 200
        with mock.patch.object(pdf_fetch_api.db_mod, "get_article",
                               side_effect=sqlite3.OperationalError("database is locked")):
            resp = self.client.post(f"/api/tasks/{self.task}/fulltext/fetch-batch",
                                    json={"keys": ["A1", "A2"]}, headers=self.auth(0))
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["summary"]["failed"], 2)
        self.assertTrue(all(r["reason"] == "storage" for r in body["results"]))

    def test_member_sees_masked_email(self):
        """r3 复审⑪（低）：联系 email 是 owner 的 PII，成员回包必须掩码。"""
        from coscreen import task_collaboration
        self.assertEqual(self.enable(email="owner-secret@team.test").status_code, 200)
        collab = task_collaboration.shared_store_path(self.info.dir)
        task_collaboration.grant_role(collab, task_id=self.task,
                                      user_id=self.users[1]["user_id"],
                                      role="coder",
                                      actor_user_id=self.users[0]["user_id"])
        owner_view = self.client.get(f"/api/tasks/{self.task}/pdf-fetch/settings",
                                     headers=self.auth(0)).json()
        self.assertEqual(owner_view["email"], "owner-secret@team.test",
                         "owner 本人必须看到完整值（否则无法核对）")
        member_view = self.client.get(f"/api/tasks/{self.task}/pdf-fetch/settings",
                                      headers=self.auth(1))
        self.assertEqual(member_view.status_code, 200, member_view.text)
        body = member_view.json()
        self.assertNotIn("owner-secret@team.test", json.dumps(body),
                         "成员不得拿到完整联系邮箱")
        self.assertEqual(body["email"], "o***@team.test")
        self.assertTrue(body.get("email_masked"))

    def test_unresolvable_link_reports_upstream_not_unsafe(self):
        """r3 复审⑨（低）：DNS 解析不出 != 指向内网；不得归因成安全拒绝。"""
        self.enable_now()
        self.set_routes(unpaywall_json("10.9999%2Fsuccess", {
            "is_oa": True,
            "best_oa_location": {"url_for_pdf": "https://no-such-host.invalid/x.pdf"},
            "oa_locations": []}))
        resp = self.client.post(f"/api/tasks/{self.task}/fulltext/A1/fetch",
                                headers=self.auth(0))
        self.assertEqual(resp.status_code, 502, resp.text)
        detail = resp.json()["detail"]
        self.assertEqual(detail["code"], "pdf_fetch_upstream")
        self.assertEqual(detail["reason"], "unresolvable_host")
        # 批量里单列 unresolvable，不再并进 blocked
        resp = self.client.post(f"/api/tasks/{self.task}/fulltext/fetch-batch",
                                json={"keys": ["A1"]}, headers=self.auth(0))
        self.assertEqual(resp.json()["results"][0]["outcome"], "unresolvable")

    def test_assertion_errors_are_not_swallowed_as_upstream_failure(self):
        """r3 复审⑭（低）：AssertionError 是程序/测试缺陷，不得被兜底吞成 502。"""
        self.enable_now()
        self.set_routes((lambda url: url.startswith(UNPAYWALL),
                         AssertionError("undeclared outbound in test stub")))
        with self.assertRaises(AssertionError):
            self.client.post(f"/api/tasks/{self.task}/fulltext/A1/fetch",
                             headers=self.auth(0))
        # 对照：真正的上游形态仍降级
        self.set_routes((lambda url: url.startswith(UNPAYWALL),
                         http.client.BadStatusLine("NOT-HTTP")))
        resp = self.client.post(f"/api/tasks/{self.task}/fulltext/A1/fetch",
                                headers=self.auth(0))
        self.assertEqual(resp.status_code, 502)


# ---------------------------------------------------------------------------
# r4 复审钉子：批量入口的 try 之前可抛点（库锁 / 目录 / 逐条轨迹失败）
# ---------------------------------------------------------------------------

class PdfFetchRound4Tests(PdfFetchFixture, unittest.TestCase):
    """r4 复审（高）：fetch-batch 前置准备在 try 之外 → 库锁 5xx。

    复现形态（评审原话①）：``init_db`` 第二次起抛
    ``sqlite3.OperationalError("database is locked")``（标准 SQLite 争用）→
    旧实现未捕获 → 500。补全形态②：``pdfs_dir_for`` 的 mkdir 抛 OSError
    （磁盘只读/写满）同样是前置可抛点。
    """

    TAG = "w2c"

    def error_client(self):
        """不把服务端异常重新抛出，才能断言「确实是 500 而不是别的」。"""
        return TestClient(app, raise_server_exceptions=False)

    def batch(self, keys, *, client=None):
        client = client or self.client
        return client.post(f"/api/tasks/{self.task}/fulltext/fetch-batch",
                           json={"keys": list(keys)}, headers=self.auth(0))

    def assert_prepare_503(self, resp):
        self.assertEqual(resp.status_code, 503,
                         f"批量前置失败必须降级 503，实际 {resp.status_code}: {resp.text[:300]}")
        detail = resp.json()["detail"]
        self.assertEqual(detail["code"], "pdf_fetch_storage")
        self.assertEqual(detail["scope"], "batch")
        self.assertEqual(detail["stage"], "prepare")
        self.assertEqual(detail["executed"], 0)
        self.assertTrue(detail["retryable"])
        # 可读指引：必须说明「本次没有执行任何获取」，不能只说「失败」
        self.assertIn("没有执行任何获取", detail["detail"])
        self.assertEqual(resp.headers.get("retry-after"), "5")

    def test_batch_prepare_db_lock_is_503_not_500(self):
        """评审原话①：init_db 抛 database is locked → 503 结构化指引，绝不 500。"""
        self.enable_now()
        client = self.error_client()
        with mock.patch.object(db, "init_db",
                               side_effect=sqlite3.OperationalError("database is locked")):
            # 同一请求重试两次：真实争用是「第二次起」才锁，契约必须每次都稳。
            for attempt in range(2):
                with self.subTest(attempt=attempt):
                    resp = self.batch(["A1", "A2"], client=client)
                    self.assert_prepare_503(resp)
        self.assertEqual(self.opener.calls, [], "准备阶段失败绝不得到出站")

    def test_batch_prepare_db_corruption_is_503_not_500(self):
        """库损坏（DatabaseError 族）同样只能降级，不能变成 500。"""
        self.enable_now()
        client = self.error_client()
        with mock.patch.object(db, "init_db",
                               side_effect=sqlite3.DatabaseError("database disk image is malformed")):
            self.assert_prepare_503(self.batch(["A1"], client=client))

    def test_batch_prepare_pdfs_dir_failure_is_503_not_500(self):
        """评审补齐②：pdfs_dir_for 的 mkdir 抛 OSError（只读/写满）也不得 500。"""
        self.enable_now()
        client = self.error_client()
        original_mkdir = Path.mkdir

        def deny_pdfs_dir(path_self, *args, **kwargs):
            if path_self.name == "pdfs":
                raise OSError(28, "No space left on device")
            return original_mkdir(path_self, *args, **kwargs)

        with mock.patch.object(Path, "mkdir", deny_pdfs_dir):
            self.assert_prepare_503(self.batch(["A1"], client=client))
        self.assertEqual(self.opener.calls, [])

    def oa_none_route(self):
        """任何 Unpaywall 查询都回「无 OA」——本组只关心降级形状，不关心 DOI 分流。"""
        return (lambda url: url.startswith(UNPAYWALL),
                json.dumps(OA_NO).encode("utf-8"))

    def test_batch_recovers_after_lock_is_released(self):
        """锁是暂时的：解除争用后同一批必须照常跑完（不是把任务判死）。"""
        self.enable_now()
        self.set_routes(self.oa_none_route())
        client = self.error_client()
        with mock.patch.object(db, "init_db",
                               side_effect=sqlite3.OperationalError("database is locked")):
            self.assert_prepare_503(self.batch(["A1", "A2"], client=client))
        resp = self.batch(["A1", "A2"], client=client)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["summary"]["no_oa"], 2)

    def test_batch_single_key_trace_failure_stays_per_key(self):
        """逐条链路里抛出的 HTTPException（轨迹库 503）只折当前条目。

        ``trace_context_for`` 在轨迹库不可用时抛 503「本次修改尚未保存」——
        旧实现未捕获，一旦命中，整批（含已保存的条目）全部作废。
        """
        self.enable_now()
        self.set_routes(self.oa_none_route())
        real_fetch_one = pdf_fetch_api._fetch_one

        def flaky(db_path, key, article, **kwargs):
            if key == "A3":
                raise pdf_fetch_api.HTTPException(
                    503, "本次修改尚未保存。轨迹记录不可用，请重试或暂停记录后重新保存。")
            return real_fetch_one(db_path, key, article, **kwargs)

        client = self.error_client()
        with mock.patch.object(pdf_fetch_api, "_fetch_one", flaky):
            resp = self.batch(["A3", "A1"], client=client)
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        results = {r["zotero_key"]: r for r in body["results"]}
        self.assertEqual(results["A3"]["outcome"], "failed")
        self.assertEqual(results["A3"]["reason"], "storage")
        self.assertEqual(results["A1"]["outcome"], "no_oa",
                         "前一条的轨迹失败不得取消后续条目")
        self.assertEqual(body["summary"]["failed"], 1)
        self.assertEqual(body["summary"]["no_oa"], 1)
        # 没被误判成熔断
        self.assertFalse(body["aborted"])

    def test_batch_prepare_keeps_auth_and_disabled_semantics(self):
        """降级不得吞掉 403/404：非成员仍 403，未启用仍 403（治理红线）。"""
        resp = self.batch(["A1"])
        self.assertEqual(resp.status_code, 403, "未启用时必须 403")
        self.assertEqual(resp.json()["detail"]["code"], "pdf_fetch_disabled")
        self.enable_now()
        resp = self.client.post(f"/api/tasks/{self.task}/fulltext/fetch-batch",
                                json={"keys": ["A1"]}, headers=self.auth(2))
        self.assertEqual(resp.status_code, 403)
        resp = self.client.post("/api/tasks/no-such-task/fulltext/fetch-batch",
                                json={"keys": ["A1"]}, headers=self.auth(0))
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(self.opener.calls, [], "被拒请求绝不得到出站")

    # -- 同族：其它端点也在 try 之前碰工作流上下文 --------------------------

    def test_context_failure_degrades_on_every_entry_point(self):
        """r4 复审（高·同族）：``_context`` 在 try 之外 → 单篇/建议/设置同样 500。

        实测（跨进程 BEGIN EXCLUSIVE）：单篇端点复现 500。上下文要开协作库、
        属主库并跑迁移，库锁与损坏都是标准运行形态，四个入口口径必须一致。
        """
        self.enable_now()
        client = self.error_client()
        workflow = type(app.state.review_workflow)
        for name, method, path, kwargs in (
            ("fetch_one", "post", f"/api/tasks/{self.task}/fulltext/A1/fetch", {}),
            ("fetch_batch", "post", f"/api/tasks/{self.task}/fulltext/fetch-batch",
             {"json": {"keys": ["A1"]}}),
            ("metadata", "get",
             f"/api/tasks/{self.task}/fulltext/A1/metadata-suggestions", {}),
            ("get_settings", "get", f"/api/tasks/{self.task}/pdf-fetch/settings", {}),
            ("put_settings", "put", f"/api/tasks/{self.task}/pdf-fetch/settings",
             {"json": {"enabled": True, "email": "team@example.test"}}),
        ):
            with self.subTest(endpoint=name):
                with mock.patch.object(workflow, "context",
                                       side_effect=sqlite3.OperationalError("database is locked")):
                    resp = getattr(client, method)(path, headers=self.auth(0), **kwargs)
                self.assertEqual(resp.status_code, 503,
                                 f"{name} 必须降级 503，实际 {resp.status_code}: {resp.text[:200]}")
                self.assertEqual(resp.json()["detail"]["code"], "pdf_fetch_storage")
        self.assertEqual(self.opener.calls, [], "降级路径绝不得到出站")

    def test_collaboration_lock_still_maps_to_its_own_503(self):
        """协作库锁是既有 503 语义（OwnerUnavailableError），不得被本组改写。"""
        from coscreen import task_collaboration
        self.enable_now()
        client = self.error_client()
        with mock.patch.object(task_collaboration, "get_owner",
                               side_effect=task_collaboration.OwnerUnavailableError("busy")):
            resp = client.post(f"/api/tasks/{self.task}/fulltext/fetch-batch",
                               json={"keys": ["A1"]}, headers=self.auth(0))
        self.assertEqual(resp.status_code, 503, resp.text)
        self.assertIn("稍后重试", json.dumps(resp.json(), ensure_ascii=False))

    def test_corrupt_workflow_state_degrades_without_retry_hint(self):
        """损坏的 workflow_state.stages_json（JSONDecodeError ⊂ ValueError）。

        实测：上下文在准备阶段 json.loads 抛错 → 旧实现 500。这是**永久**损坏，
        与库锁不同：必须给修复指引且 ``retryable`` 为假（诱使用户反复重试
        只会重复同一个错误）。批量与单篇 / 设置读取口径一致。
        """
        from contextlib import closing
        self.enable_now()
        client = self.error_client()
        with closing(sqlite3.connect(self.owner_db)) as conn, conn:
            conn.execute("UPDATE workflow_state SET stages_json='{not json' WHERE singleton=1")
        resp = client.post(f"/api/tasks/{self.task}/fulltext/fetch-batch",
                           json={"keys": ["A1", "A2"]}, headers=self.auth(0))
        self.assertEqual(resp.status_code, 503,
                         f"损坏状态必须降级 503，实际 {resp.status_code}: {resp.text[:200]}")
        detail = resp.json()["detail"]
        self.assertEqual(detail["code"], "pdf_fetch_storage")
        self.assertFalse(detail["retryable"], "永久损坏不得标成可重试")
        self.assertIn("已损坏", detail["detail"])
        self.assertEqual(detail["executed"], 0)
        self.assertIsNone(resp.headers.get("retry-after"))
        for path in (f"/api/tasks/{self.task}/fulltext/A1/fetch",
                     f"/api/tasks/{self.task}/fulltext/A1/metadata-suggestions",
                     f"/api/tasks/{self.task}/pdf-fetch/settings"):
            with self.subTest(path=path):
                entry = client.get if path.endswith("settings") or path.endswith(
                    "metadata-suggestions") else client.post
                r = entry(path, headers=self.auth(0))
                self.assertEqual(r.status_code, 503, f"{path}: {r.text[:200]}")
                # 单篇/建议/设置同口径：都不得把永久损坏说成「稍后重试」。
                self.assertFalse(r.json()["detail"]["retryable"],
                                 f"{path} 不得把永久损坏标成可重试")
                self.assertIn("已损坏", json.dumps(r.json(), ensure_ascii=False))
        self.assertEqual(self.opener.calls, [], "损坏状态绝不得到出站")


class PdfFetchBaseUrlGovernanceTests(PdfFetchFixture, unittest.TestCase):
    """终审 #30：可配置 base_url 拦链路本地/CGNAT/云元数据（保存+出站两处）。

    回环与 RFC1918 放行（自托管正钉既有，见 test_self_hosted_loopback_base_url_is_used
    与本类正钉）——被拦集合与 ai_keys._clean_base_url 同口径。
    """

    TAG = "w2b30"

    def test_blocked_base_urls_rejected_at_save(self):
        for base in ("http://169.254.169.254", "http://100.100.100.200",
                     "http://metadata.google.internal", "http://metadata.goog",
                     "http://[fe80::1]", "http://metadata-ish.example",
                     "http://[fd00:ec2::254]",    # r2：IPv6 ULA（AWS IMDSv6）
                     "http://ula-ish.example"):    # r2：DNS 解析回 ULA
            with self.subTest(base=base):
                resp = self.enable(unpaywall_base_url=base, email="")
                self.assertEqual(resp.status_code, 400, f"{base}: {resp.text[:160]}")
                detail = resp.json()["detail"]
                self.assertEqual(detail["code"], "pdf_fetch_base_url_blocked")
        # Crossref base 同样受拦（两个字段共用同一判定）。
        resp = self.enable(crossref_base_url="http://169.254.169.254")
        self.assertEqual(resp.status_code, 400, resp.text[:160])
        self.assertEqual(resp.json()["detail"]["code"], "pdf_fetch_base_url_blocked")

    def test_loopback_and_rfc1918_self_hosted_still_allowed(self):
        for index, base in enumerate(("http://127.0.0.1:8787",
                                      "http://192.168.1.10:8787",
                                      "http://10.1.2.3:8787",
                                      "http://selfhost.example")):
            with self.subTest(base=base):
                resp = self.enable(unpaywall_base_url=base, email="",
                                   expected_version=index)
                self.assertEqual(resp.status_code, 200, f"{base}: {resp.text[:160]}")

    def test_stored_blocked_base_never_opens_outbound(self):
        """存量文件指向链路本地（绕过保存闸）→ 出站零次 + 结构化降级。"""
        self.write_settings_file(unpaywall_base_url="http://169.254.169.254")
        self.set_routes(crossref_json({"message": {}}))
        self.opener.calls.clear()
        resp = self.client.post(f"/api/tasks/{self.task}/fulltext/A1/fetch",
                                headers=self.auth(0))
        self.assertEqual(resp.status_code, 502, resp.text[:200])
        detail = resp.json()["detail"]
        self.assertEqual(detail["reason"], "unsafe_url")
        self.assertEqual(self.opener.calls, [],
                         "被拦 base 绝不出站（含 Crossref 查询）")

    def test_w9_crossref_seam_rejects_blocked_base(self):
        """W9 撤稿检查复用的 crossref_works_url 同一出口闸（fetch_crossref_work）。"""
        with self.assertRaises(pdf_fetch_api.OutboundError) as raised:
            pdf_fetch_api.fetch_crossref_work("http://100.100.100.200", "10.9999/x")
        self.assertEqual(raised.exception.reason, "unsafe_url")
        self.assertEqual(self.opener.calls, [])

    def test_dns_resolving_to_linklocal_rejected(self):
        """域名解析回链路本地（metadata-ish.example → 169.254.169.254）同样拒。"""
        self.assertEqual(
            pdf_fetch_api._base_url_block_reason("http://metadata-ish.example"),
            "link_local")

    def test_ula_addresses_blocked_like_ai_keys_precedent(self):
        """r2：fc00::/7（含 fd00:: 云元数据端点）按 ai_keys._ULA_V6 同口径拦；
        回环 ::1 与 fe80 的区分不受影响。"""
        self.assertEqual(pdf_fetch_api._base_url_block_reason("http://[fd00:ec2::254]"),
                         "ula")
        self.assertEqual(pdf_fetch_api._base_url_block_reason("http://[fc00::1]"),
                         "ula")
        self.assertIsNone(pdf_fetch_api._base_url_block_reason("http://[::1]"),
                          "回环必须放行（自托管正钉）")
        self.assertIsNone(pdf_fetch_api._base_url_block_reason("http://127.0.0.1:8787"))

    def test_mutation_save_guard_removed_pin_goes_red(self):
        """破坏性变异：保存侧判定删掉（恒 None）→ 坏地址保存 200——第一颗钉必红。"""
        with mock.patch.object(pdf_fetch_api, "_base_url_block_reason",
                               lambda base: None):
            resp = self.enable(unpaywall_base_url="http://169.254.169.254",
                               email="")
        self.assertEqual(resp.status_code, 200, resp.text[:160])

    def test_mutation_outbound_guard_removed_pin_goes_red(self):
        """破坏性变异：出站闸删掉（no-op）→ 出站真的发往 169.254.169.254。

        （review67 M-15 集成修订：出站闸自 _guard_base_url_outbound 迁至
        _pinned_base_addresses——同一被拦集合+连接期钉扎；变异靶随之更新，
        证明语义不变：闸被拆掉时被拦地址真的可达。）"""
        self.write_settings_file(unpaywall_base_url="http://169.254.169.254")
        self.set_routes(
            (lambda url: url.startswith("http://169.254.169.254/v2/"),
             json.dumps(OA_NO).encode("utf-8")),
            crossref_json({"message": {}}))
        self.opener.calls.clear()
        with mock.patch.object(pdf_fetch_api, "_pinned_base_addresses",
                               lambda base: None):
            resp = self.client.post(f"/api/tasks/{self.task}/fulltext/A1/fetch",
                                    headers=self.auth(0))
        self.assertEqual(resp.status_code, 200, resp.text[:200])
        self.assertTrue(any(url.startswith("http://169.254.169.254/")
                            for url, _t, _h in self.opener.calls),
                        "无出站闸时真的把 DOI+email 发往链路本地地址（钉必红）")


class GetBytesTooLargePins(unittest.TestCase):
    """终审 #35：``_get_bytes`` 出站响应体积上限（JSON 2MiB / PDF 100MiB）
    的 too_large 红钉（纯函数级，零真实出站）。

    既有测试的 FakeResponse 只按调用方给的 limit 裁切，从未构造超限响应；
    删掉超限判定（pdf_fetch_api ``len(data) > max_bytes`` → too_large）或把
    ``read(max_bytes+1)`` 改成不限量读取时全部测试仍绿。本类构造「无视
    limit 的超限响应」：
    - 超限 → ``OutboundError(reason='too_large')``；
    - ``read`` 收到的 limit 恒等于 ``max_bytes+1``（内存上界；上限被改即红）；
    - JSON 路径超限 → too_large 而非 bad_response（未进入 JSON 解析）。"""

    URL = "https://api.example.test/endpoint"

    def test_over_limit_raises_too_large_and_reads_bounded(self):
        recorded = {}

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, limit):
                recorded["read"] = limit
                return b"x" * 17  # 无视 limit：模拟真实超大响应

        def _open(request, timeout=None):
            return _Resp()

        with mock.patch.object(pdf_fetch_api, "_open", _open):
            with self.assertRaises(pdf_fetch_api.OutboundError) as ctx:
                pdf_fetch_api._get_bytes(self.URL, timeout=3.0,
                                         max_bytes=16, accept="*/*")
        self.assertEqual(ctx.exception.reason, "too_large",
                         str(ctx.exception))
        self.assertEqual(recorded["read"], 17,
                         "read 必须带 max_bytes+1 的 limit（内存上界）")

    def test_json_path_over_cap_is_too_large_not_bad_response(self):
        """``_get_json``（MAX_JSON_BYTES）超限 → too_large，绝不进入解析。"""
        cap = pdf_fetch_api.MAX_JSON_BYTES
        recorded = {}

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, limit):
                recorded["read"] = limit
                return b"x" * (cap + 1)

        def _open(request, timeout=None):
            return _Resp()

        with mock.patch.object(pdf_fetch_api, "_open", _open):
            with self.assertRaises(pdf_fetch_api.OutboundError) as ctx:
                pdf_fetch_api._get_json(self.URL)
        self.assertEqual(ctx.exception.reason, "too_large",
                         str(ctx.exception))
        self.assertEqual(recorded["read"], cap + 1)

    def test_mutation_cap_widened_pin_goes_red(self):
        """破坏性变异：上限放宽 → 同样的超限响应落到 bad_response（进入
        解析）而不是 too_large——上一组钉必红。"""
        real_cap = pdf_fetch_api.MAX_JSON_BYTES

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, limit):
                return b"x" * (real_cap + 1)

        def _open(request, timeout=None):
            return _Resp()

        with mock.patch.object(pdf_fetch_api, "_open", _open), \
                mock.patch.object(pdf_fetch_api, "MAX_JSON_BYTES", 1 << 30):
            with self.assertRaises(pdf_fetch_api.OutboundError) as ctx:
                pdf_fetch_api._get_json(self.URL)
        self.assertEqual(ctx.exception.reason, "bad_response",
                         str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
