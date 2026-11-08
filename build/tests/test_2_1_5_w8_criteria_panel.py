# -*- coding: utf-8 -*-
"""2.1.5 Wave G3/W8 · 标准对照常驻卡后端钉子（专属新文件，撤实现必红）。

覆盖方案书 W8 红钉清单（design_2.1.5_criteria_panel.md §三 B3）：

1. **聚合端点零写入**：调用前后任务目录全部文件 sha256 **与 mtime_ns** 不变
   （task.json 与筛选员库——含轨迹表——零变化；不新建任何文件/库/临时文件；
   等值无锁重写也会被 mtime 检出）；
2. **旧 task.json 无 key_terms 兼容读**：legacy 协议行（无该字段）不炸，
   面板返回 has_protocol=true + key_terms=[]，导出无「关键术语」节；
3. **权限**：任务成员（coder/reconciler）可读；非任务成员 403；未认证 401；
   未知任务 404；**写路径只归属主**（coder/reconciler 覆盖或清空 403 且数据零变化，
   评审 r2 阻断项）；
4. **markdown 导出含新节**：有术语时出现「## 关键术语」与术语/释义；无术语
   时整节省略（不产生空表）；`|`、换行与**反斜杠**转义；
5. **key_terms 往返序列化**：ProtocolDocument → dict → ProtocolDocument 保序
   保内容；畸形条目宽松跳过（不抛错）；HTTP 保存/读取/导出全链路一致。

另钉响应形状冻结（恰好四个键，见 frontend_contract_2.1.5-w8-criteria-panel.md）。
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# 短前缀 + 短任务名：research_trace 的迁移备份文件名较长，Windows MAX_PATH
# （260）在长临时目录下会被挤爆（"unable to open database file"）。只影响测试
# 夹具命名，不涉及产品路径。
fixture = tempfile.TemporaryDirectory(prefix="rf_w8cp_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient  # noqa: E402

from coscreen import db as db_mod  # noqa: E402
from coscreen import protocol_designer as pd  # noqa: E402
from coscreen import research_trace as trace_mod  # noqa: E402
from coscreen import tasks as tasks_mod  # noqa: E402
from coscreen.task_collaboration import DB_FILENAME as COLLAB_DB  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend.main import DATA_DIR, app  # noqa: E402


# ---------------------------------------------------------------------------
# 1. 纯函数钉：key_terms 序列化 / 反序列化 / 导出
# ---------------------------------------------------------------------------

class KeyTermsSerializationPins(unittest.TestCase):
    def _doc(self, **overrides):
        payload = {
            "title": "方案甲",
            "research_question": "同伴支持对糖尿病自我管理的影响？",
            "pico": [{"key": "population", "value": "adults"}],
            "inclusion_criteria": ["成人 2 型糖尿病", "干预 ≥ 8 周"],
            "exclusion_criteria": ["妊娠期糖尿病"],
            "key_terms": [
                {"term": "同伴支持", "definition": "由受过培训的非专业人员提供的结构化支持"},
                {"term": "干预 ≥ 8 周", "definition": "干预时长不少于 8 周，含 8 周"},
            ],
            "search_strategies": [],
            "created_at": "2026-11-08T10:00:00",
        }
        payload.update(overrides)
        return payload

    def test_round_trip_preserves_order_and_text(self):
        doc = pd.protocol_from_dict(self._doc())
        self.assertEqual([kt.term for kt in doc.key_terms], ["同伴支持", "干预 ≥ 8 周"])
        self.assertEqual(doc.key_terms[0].definition,
                         "由受过培训的非专业人员提供的结构化支持")
        stored = pd.protocol_to_dict(doc)
        self.assertEqual(stored["key_terms"], self._doc()["key_terms"])
        again = pd.protocol_from_dict(stored)
        self.assertEqual(pd.protocol_to_dict(again), stored)

    def test_legacy_row_without_key_terms_is_empty_not_error(self):
        legacy = self._doc()
        legacy.pop("key_terms")
        doc = pd.protocol_from_dict(legacy)
        self.assertEqual(doc.key_terms, [])
        # 旧 dict 经导出不得多出空节
        self.assertNotIn("关键术语", pd.protocol_to_markdown(legacy))
        # None / 非 list 同样降级为空（旧数据与手写 dict 都可能出现）
        self.assertEqual(pd.protocol_from_dict(self._doc(key_terms=None)).key_terms, [])
        self.assertEqual(pd.protocol_from_dict(self._doc(key_terms={"term": "x"})).key_terms, [])

    def test_malformed_entries_are_skipped_not_fatal(self):
        doc = pd.protocol_from_dict(self._doc(key_terms=[
            {"term": "  有效术语  ", "definition": "  释义  "},
            {"term": "   ", "definition": "空术语名"},
            {"definition": "缺 term"},
            "not-a-dict",
            {"term": 123, "definition": None},
        ]))
        self.assertEqual([kt.term for kt in doc.key_terms], ["有效术语", "123"])
        self.assertEqual(doc.key_terms[0].definition, "释义")
        self.assertEqual(doc.key_terms[1].definition, "")

    def test_markdown_has_key_terms_section_with_escaping(self):
        md = pd.protocol_to_markdown(self._doc(key_terms=[
            {"term": "剂量|单位", "definition": "每公斤体重第一行\n第二行"},
        ]))
        self.assertIn("## 关键术语", md)
        self.assertIn("| 术语 | 定义 |", md)
        self.assertIn("剂量\\|单位", md, "术语中的 | 必须转义，否则撕裂表格")
        self.assertIn("每公斤体重第一行<br>第二行", md, "释义换行必须折叠为 <br>")
        # 节序：关键术语在排除标准之后、创建时间之前
        self.assertLess(md.index("## 排除标准"), md.index("## 关键术语"))
        self.assertLess(md.index("## 关键术语"), md.index("创建时间："))

    def test_markdown_omits_section_when_empty(self):
        md = pd.protocol_to_markdown(self._doc(key_terms=[]))
        self.assertNotIn("## 关键术语", md)

    def test_backslash_is_escaped_before_pipe(self):
        """评审 r2 笔误项：反斜杠必须先转义，否则 ``a\\|b`` 的 ``|`` 转义失效。"""
        md = pd.protocol_to_markdown(self._doc(key_terms=[
            {"term": "a\\|b", "definition": "x"},
        ]))
        row = next(line for line in md.splitlines() if "a" in line and "| x |" in line)
        self.assertIn("a" + "\\\\" + "\\|" + "b", row,
                      "反斜杠须先转义为 \\\\，再接 \\| —— 否则输出 a\\|b 被还原成裸 |")
        # 表格结构未被撕裂：该行只有 3 个未转义的 |（表格分隔符）
        unescaped = 0
        index = 0
        while index < len(row):
            if row[index] == "\\":
                index += 2
                continue
            if row[index] == "|":
                unescaped += 1
            index += 1
        self.assertEqual(unescaped, 3, f"表格行被撕裂：{row!r}")


# ---------------------------------------------------------------------------
# 2. HTTP 端点钉（真实 TestClient）
# ---------------------------------------------------------------------------

def _make_user(prefix):
    username = f"{prefix}@example.test"
    user = auth_mod.create_user(username, f"{prefix}-Pass-2026!")
    return user, {"Authorization": "Bearer " +
                  auth_mod.create_access_token(user["id"], user["username"])}


def _snapshot_files(root: Path) -> dict[str, tuple[str, int]]:
    """任务目录下全部文件 -> (sha256, st_mtime_ns)。

    只比内容哈希会漏掉「用相同内容无锁重写 task.json」这一丢更新风险
    （评审 r2 变异 w_rewrite_same 存活）；加入 mtime_ns 后等值重写也会变红。
    """
    digests: dict[str, tuple[str, int]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            stat = path.stat()
            digests[str(path.relative_to(root)).replace("\\", "/")] = (
                hashlib.sha256(path.read_bytes()).hexdigest(), stat.st_mtime_ns)
    return digests


class CriteriaPanelEndpointPins(unittest.TestCase):
    _seq = 0

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        cls.owner, cls.auth = _make_user("w8powner")
        cls.member, cls.member_auth = _make_user("w8pmember")
        cls.reconciler, cls.reconciler_auth = _make_user("w8preconciler")
        cls.outsider, cls.outsider_auth = _make_user("w8poutsider")

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        type(self)._seq += 1
        response = self.client.post(
            "/api/tasks", json={"name": f"w8cp{type(self)._seq}"},
            headers=self.auth)
        self.assertIn(response.status_code, (200, 201), response.text)
        self.task = response.json()["task_id"]
        self.base = f"/api/tasks/{self.task}/criteria-panel"
        for target, role in ((self.member, "coder"), (self.reconciler, "reconciler")):
            invite = self.client.post(
                f"/api/tasks/{self.task}/collaboration/invite",
                json={"username": target["username"], "role": role},
                headers=self.auth)
            self.assertEqual(invite.status_code, 201, invite.text)
        self.info = tasks_mod.find_task(self.task, DATA_DIR)
        self.assertIsNotNone(self.info)

    def _save_protocol(self, **overrides):
        payload = {
            "title": "方案甲",
            "research_question": "同伴支持对糖尿病自我管理的影响？",
            "pico": [{"key": "population", "label": "人群", "value": "成年人"}],
            "inclusion_criteria": ["成人 2 型糖尿病", "干预 ≥ 8 周"],
            "exclusion_criteria": ["妊娠期糖尿病"],
            "key_terms": [
                {"term": "同伴支持", "definition": "结构化支持"},
            ],
        }
        payload.update(overrides)
        response = self.client.post(f"/api/tasks/{self.task}/protocol",
                                    json=payload, headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["protocol"]

    # ---- 形状 / 无方案 ------------------------------------------------
    def test_shape_frozen_and_no_protocol_is_empty(self):
        response = self.client.get(self.base, headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(set(body), {"inclusion", "exclusion", "key_terms", "has_protocol"},
                         "响应形状冻结：恰好四个键")
        self.assertFalse(body["has_protocol"])
        self.assertEqual((body["inclusion"], body["exclusion"], body["key_terms"]),
                         ([], [], []))

    def test_aggregates_saved_protocol(self):
        self._save_protocol()
        body = self.client.get(self.base, headers=self.auth).json()
        self.assertTrue(body["has_protocol"])
        self.assertEqual(body["inclusion"], ["成人 2 型糖尿病", "干预 ≥ 8 周"])
        self.assertEqual(body["exclusion"], ["妊娠期糖尿病"])
        self.assertEqual(body["key_terms"],
                         [{"term": "同伴支持", "definition": "结构化支持"}])

    # ---- 旧 task.json 兼容读（无 key_terms 字段） ----------------------
    def test_legacy_task_json_without_key_terms(self):
        legacy_protocol = {
            "title": "旧方案",
            "research_question": "",
            "pico": [{"key": "population", "label": "人群", "value": "成人",
                      "synonyms": [], "mesh_terms": []}],
            "inclusion_criteria": ["旧纳入标准"],
            "exclusion_criteria": ["旧排除标准"],
            "search_strategies": [],
            "created_at": "2026-01-01T00:00:00",
        }
        raw = tasks_mod._read_task_json(self.info.dir) or {}  # noqa: SLF001
        raw["protocol"] = legacy_protocol
        tasks_mod._write_task_json(self.info.dir, raw)  # noqa: SLF001

        response = self.client.get(self.base, headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["has_protocol"])
        self.assertEqual(body["inclusion"], ["旧纳入标准"])
        self.assertEqual(body["exclusion"], ["旧排除标准"])
        self.assertEqual(body["key_terms"], [], "旧数据无 key_terms 必须是空清单而非 500")

        export = self.client.get(f"/api/tasks/{self.task}/protocol/export",
                                 headers=self.auth)
        self.assertEqual(export.status_code, 200, export.text)
        self.assertNotIn("## 关键术语", export.text)

        # 旧行仍可经 GET /protocol 原样读出（不因新增字段被改写）
        fetched = self.client.get(f"/api/tasks/{self.task}/protocol", headers=self.auth)
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertEqual(fetched.json()["protocol"]["inclusion_criteria"], ["旧纳入标准"])

    # ---- 权限 ---------------------------------------------------------
    def test_member_readable_outsider_and_anonymous_denied(self):
        self._save_protocol()
        member = self.client.get(self.base, headers=self.member_auth)
        self.assertEqual(member.status_code, 200, member.text)
        self.assertEqual(member.json()["key_terms"][0]["term"], "同伴支持")

        outsider = self.client.get(self.base, headers=self.outsider_auth)
        self.assertEqual(outsider.status_code, 403, outsider.text)
        self.assertNotIn("同伴支持", outsider.text, "403 响应不得泄露标准/术语内容")
        # 403 体由 `require_user` 的任务参与权闸门统一产生（历史裸文案）；
        # 端点内另有一道 `task_visible` 结构化兜底（criteria_panel.task_forbidden），
        # 两者都接受，但状态码与零泄露是钉住的。
        detail = outsider.json()["detail"]
        if isinstance(detail, dict):
            self.assertEqual(detail["error_code"], "task_forbidden")
        else:
            self.assertIn("参与者", detail)

        anonymous = self.client.get(self.base)
        self.assertEqual(anonymous.status_code, 401, anonymous.text)

        missing = self.client.get("/api/tasks/no-such-task/criteria-panel",
                                  headers=self.auth)
        self.assertEqual(missing.status_code, 404, missing.text)

    def test_inner_visibility_guard_is_structured_403(self):
        """端点内 `task_visible` 兜底分支（HTTP 层被 `require_user` 闸门先行拦截，
        正常请求不可达）直接调用端点函数钉住：结构化 task_forbidden，非裸文案。"""
        from fastapi import HTTPException

        from custom_backend.main import app as _app

        route = next(r for r in _app.routes
                     if getattr(r, "path", None) == "/api/tasks/{task_id}/criteria-panel")
        with self.assertRaises(HTTPException) as caught:
            route.endpoint(self.task, {"user_id": self.outsider["id"],
                                       "username": self.outsider["username"]})
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.detail["error_code"], "task_forbidden")
        self.assertEqual(caught.exception.detail["message_key"],
                         "criteria_panel.task_forbidden")

    def test_write_gate_owner_only_coder_cannot_overwrite_criteria(self):
        """评审阻断项：方案保存（含 key_terms）只归属主；coder/reconciler 覆盖或
        清空必须 403，且既有数据零变化（`_require_task_owner` 同一口径）。"""
        self._save_protocol()
        baseline = self.client.get(self.base, headers=self.auth).json()

        hijack = {
            "title": "coder 覆盖",
            "pico": [{"key": "population", "value": "x"}],
            "inclusion_criteria": ["coder-inc"],
            "exclusion_criteria": [],
            "key_terms": [{"term": "coder-overwrote", "definition": ""}],
        }
        for headers, who in ((self.member_auth, "coder"),
                             (self.reconciler_auth, "reconciler")):
            with self.subTest(role=who):
                response = self.client.post(f"/api/tasks/{self.task}/protocol",
                                            json=hijack, headers=headers)
                self.assertEqual(response.status_code, 403, response.text)
                self.assertEqual(self.client.get(self.base, headers=self.auth).json(),
                                 baseline, "非属主写入被拒后标准与术语必须原样")

        clear = self.client.post(f"/api/tasks/{self.task}/protocol", json={
            "title": "清空", "pico": [{"key": "population", "value": "x"}],
            "key_terms": [],
        }, headers=self.member_auth)
        self.assertEqual(clear.status_code, 403, "显式清空术语同样需要属主权限")
        self.assertEqual(self.client.get(self.base, headers=self.auth).json()["key_terms"],
                         baseline["key_terms"])

        owner = self.client.get(f"/api/tasks/{self.task}/protocol", headers=self.auth)
        self.assertEqual(owner.status_code, 200, owner.text)

    # ---- 只读钉：调用前后任务库 / 轨迹零变化 ---------------------------
    def test_endpoint_is_read_only_zero_rows_and_zero_files(self):
        """调用前后任务目录（task.json + 筛选员库含轨迹表）字节级不变。"""
        self._save_protocol()
        db_path = self.info.dir / (tasks_mod.slugify_screener(
            self.owner["username"]) + ".db")
        db_mod.init_db(db_path)  # 模拟「任务已在使用」：库已存在
        with closing(sqlite3.connect(db_path)) as conn, conn:
            trace_mod.ensure_schema(conn)  # 轨迹表（trace_*）确实落库
        with closing(sqlite3.connect(db_path)) as conn:
            trace_tables = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND name LIKE 'trace_%'").fetchall()]
        self.assertIn("trace_events", trace_tables,
                      "前提核对：筛选员库必须含轨迹表，只读钉才有意义")

        before = _snapshot_files(self.info.dir)
        first = self.client.get(self.base, headers=self.member_auth)
        second = self.client.get(self.base, headers=self.auth)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        after = _snapshot_files(self.info.dir)

        self.assertEqual(sorted(after), sorted(before),
                         "只读端点不得新建任何文件（库/临时文件/WAL）")
        changed = {name for name in before if before[name] != after[name]}
        self.assertEqual(changed, set(), "只读端点改动了任务目录文件（task.json 或筛选员库）")

    # ---- 只读钉的姐妹钉：无方案时同样不建库 ---------------------------
    def test_no_protocol_read_does_not_create_database(self):
        before = _snapshot_files(self.info.dir)
        response = self.client.get(self.base, headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        after = _snapshot_files(self.info.dir)
        self.assertEqual(sorted(after), sorted(before))
        self.assertEqual([n for n in before if before[n] != after[n]], [])

    # ---- key_terms 往返经 HTTP ----------------------------------------
    def test_key_terms_round_trip_and_export_via_http(self):
        saved = self._save_protocol(key_terms=[
            {"term": "同伴支持", "definition": "结构化支持"},
            {"term": "  ", "definition": "空术语名应被丢弃"},
            {"term": "干预 ≥ 8 周", "definition": "含 8 周"},
        ])
        self.assertEqual(saved["key_terms"], [
            {"term": "同伴支持", "definition": "结构化支持"},
            {"term": "干预 ≥ 8 周", "definition": "含 8 周"},
        ], "写路径清洗空白术语名，保持顺序")

        fetched = self.client.get(f"/api/tasks/{self.task}/protocol",
                                  headers=self.auth).json()["protocol"]
        self.assertEqual(fetched["key_terms"], saved["key_terms"])

        panel = self.client.get(self.base, headers=self.member_auth).json()
        self.assertEqual(panel["key_terms"], saved["key_terms"])

        markdown = self.client.get(f"/api/tasks/{self.task}/protocol/export",
                                   headers=self.auth).text
        self.assertIn("## 关键术语", markdown)
        self.assertIn("| 同伴支持 | 结构化支持 |", markdown)
        self.assertIn("| 干预 ≥ 8 周 | 含 8 周 |", markdown)

        # 整组覆盖语义 + 幂等：同一请求体二次提交结果一致
        again = self._save_protocol(key_terms=[
            {"term": "同伴支持", "definition": "结构化支持"},
            {"term": "干预 ≥ 8 周", "definition": "含 8 周"},
        ])
        self.assertEqual(again["key_terms"], saved["key_terms"])
        self.assertEqual(self.client.get(self.base, headers=self.auth).json()["key_terms"],
                         saved["key_terms"])

    def test_key_terms_omitted_by_old_client_is_backward_compatible(self):
        """旧客户端（不传 key_terms）保存：合法、且**继承既有术语不清空**；显式 [] 才清空。"""
        self._save_protocol(key_terms=[{"term": "同伴支持", "definition": "结构化支持"}])
        response = self.client.post(f"/api/tasks/{self.task}/protocol", json={
            "title": "旧客户端方案",
            "pico": [{"key": "population", "value": "adults"}],
            "inclusion_criteria": ["a"],
        }, headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["protocol"]["key_terms"],
                         [{"term": "同伴支持", "definition": "结构化支持"}],
                         "未传字段（2.1.5 W8 之前的表单）不得静默清空已保存的术语")
        self.assertEqual(self.client.get(self.base, headers=self.auth).json()["key_terms"],
                         [{"term": "同伴支持", "definition": "结构化支持"}])

        cleared = self._save_protocol(key_terms=[])
        self.assertEqual(cleared["key_terms"], [], "显式 [] 仍是清空（整组覆盖）")
        self.assertEqual(self.client.get(self.base, headers=self.auth).json()["key_terms"], [])

    def test_key_terms_limits_are_422_not_500(self):
        """契约 §3 校验口径：条目数 > 200 或 term > 200 字符 → 422（Pydantic）。"""
        too_many = self.client.post(f"/api/tasks/{self.task}/protocol", json={
            "title": "超量",
            "pico": [{"key": "population", "value": "adults"}],
            "key_terms": [{"term": f"t{i}", "definition": ""} for i in range(201)],
        }, headers=self.auth)
        self.assertEqual(too_many.status_code, 422, too_many.text)
        too_long = self.client.post(f"/api/tasks/{self.task}/protocol", json={
            "title": "超长",
            "pico": [{"key": "population", "value": "adults"}],
            "key_terms": [{"term": "x" * 201, "definition": ""}],
        }, headers=self.auth)
        self.assertEqual(too_long.status_code, 422, too_long.text)


class PanelPayloadPins(unittest.TestCase):
    """聚合载荷的防御性口径（畸形按空、超长截断；失败绝不 500）。"""

    def test_malformed_fields_degrade_and_huge_lists_truncate(self):
        from custom_backend import criteria_panel_api as api

        payload = api._panel_payload({
            "inclusion_criteria": [f"c{i}" for i in range(700)],
            "exclusion_criteria": "not-a-list",
            "key_terms": [{"term": f"t{i}"} for i in range(700)],
        })
        self.assertEqual(len(payload["inclusion"]), api._MAX_ITEMS, "超长清单必须截断不是报错")
        self.assertEqual(len(payload["key_terms"]), api._MAX_ITEMS)
        self.assertEqual(payload["exclusion"], [], "非 list 字段按空处理")
        self.assertTrue(payload["has_protocol"])
        # 非对象协议一律「无方案」（缺失 / 损坏 / 手写脏值）
        for junk in (None, {}, "junk", [], 42):
            with self.subTest(junk=junk):
                self.assertEqual(api._panel_payload(junk), {
                    "inclusion": [], "exclusion": [], "key_terms": [],
                    "has_protocol": False})


class CorruptTaskJsonPins(unittest.TestCase):
    """task.json 边界口径（评审 r2 核正，勿再说「缺失也返回 has_protocol=false」）。

    - **内容损坏**（非法 JSON）→ 200 + has_protocol=false（本端点降级）；
    - **文件不存在** → 任务在 `find_task` 层面就不存在，整个请求 404
      （`require_user` 闸门先行），到不了本端点——这是既有任务路由口径。
    """

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        cls.owner, cls.auth = _make_user("w8panel-corrupt")

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def _new_task(self, name):
        response = self.client.post("/api/tasks", json={"name": name},
                                    headers=self.auth)
        self.assertIn(response.status_code, (200, 201), response.text)
        task = response.json()["task_id"]
        return task, tasks_mod.find_task(task, DATA_DIR)

    def test_corrupt_task_json_degrades_to_no_protocol(self):
        task, info = self._new_task("w8-corrupt")
        path = info.dir / "task.json"
        self.assertTrue(path.is_file())
        path.write_text("{not json", encoding="utf-8")
        body = self.client.get(f"/api/tasks/{task}/criteria-panel",
                               headers=self.auth)
        self.assertEqual(body.status_code, 200, body.text)
        self.assertEqual(body.json(), {"inclusion": [], "exclusion": [],
                                       "key_terms": [], "has_protocol": False})

    def test_missing_task_json_is_404_not_empty_panel(self):
        task, info = self._new_task("w8-nofile")
        (info.dir / "task.json").unlink()
        body = self.client.get(f"/api/tasks/{task}/criteria-panel",
                               headers=self.auth)
        self.assertEqual(body.status_code, 404, body.text)

    def test_server_legacy_task_read_is_409_with_migration_key(self):
        """评审 r3 阻断项：server 模式 legacy（无属主）任务的**只读 GET** 也是 409
        （`require_user` 对所有方法生效），前端必须按 message_key 分流出「属主迁移
        指引、重试无效」，不能一律提示「稍后重试」。"""
        task, info = self._new_task("w8-legacy")
        (info.dir / COLLAB_DB).unlink()  # 删除属主记录 = server 模式 legacy
        body = self.client.get(f"/api/tasks/{task}/criteria-panel",
                               headers=self.auth)
        self.assertEqual(body.status_code, 409, body.text)
        self.assertEqual(body.json()["detail"]["message_key"],
                         "coding.collaboration_owner_unset")
        self.assertEqual(body.json()["detail"]["error_code"],
                         "collaboration_owner_unset")


if __name__ == "__main__":
    unittest.main()
