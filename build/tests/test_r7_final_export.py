"""r7 修复泳道「导出溢出」钉子测试（MM10-03）。

每条用例对应一个已确认缺陷的修复；把对应修复回退，该用例必须变红：
- MM10-03：se=362（RR 保存估计 2.0，分析尺度上界 ln2+1.96×362=710.2131
  超出 exp 的 double 可表示范围 709.786）时，
  ``custom_backend/synthesis_report.py`` 的 ``_display_value`` 直接
  ``math.exp`` 抛 OverflowError → GET /api/tasks/{id}/analysis/report
  ?format=docx|pptx 500；``coscreen/manuscript_builder.py`` 同型 →
  POST /api/tasks/{id}/analysis/manuscript/generate（与 GET …/preview）
  500。修复复用同库 ``coscreen/review_analysis.py`` display() 的既有降级
  契约：溢出/非有限展示值降级为 None（经 _number/_num 渲染为既有的 "—"
  占位，与池效应行同一呈现），导出照常 200。
- 正常对照（se=361 / 361.7，上界 exp 仍可表示：3.894e+307 / 1.536e+308）
  逐位不变：模块级扫频断言两条 ``_display_value`` 对全部有限输入与原公式
  逐位相同；HTTP 级断言三路径产物中的数值文本与测试内独立复算的原公式
  ``f"{...:.4g}"`` 完全一致（复审 final_verify_v1.md §3.3 的阈值口径）。
"""
import base64
import io
import math
import os
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
fixture = tempfile.TemporaryDirectory(prefix="rf_r7_final_export_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient

from coscreen import db as db_mod
from coscreen import manuscript_builder
from coscreen import tasks as tasks_mod
from coscreen.models import Article
from coscreen.review_analysis import save_effect, save_study
from custom_backend import auth as auth_mod
from custom_backend import synthesis_report
from custom_backend.main import app, DATA_DIR

# 复审实测阈值（final_verify_v1.md §3.3）：estimate=2.0 的 RR 逐研究上界
# ln2+1.96×se —— se=361.0 → 708.2531（exp=3.89434e+307 可表示）、
# se=361.7 → 709.6251（exp=1.53563e+308 可表示）、se=362.0 → 710.2131 溢出。
RR_ESTIMATE = 2.0
SE_OVERFLOW = 362.0
SE_NORMAL_361_7 = 361.7
SE_NORMAL_361_0 = 361.0
# 与两个导出器完全相同的展示公式（有限路径的期望值独立来源）。
LN2 = math.log(RR_ESTIMATE)
_LOG_MEASURES = ("RR", "OR", "HR", "RATE_RATIO", "LOG_RATE", "LOG_ROM", "PAIRED_OR")
_DEGRADED = "—"  # _number(None) / _num(None) 的既有占位，与池效应行同口径


def _expected_row(estimate: float, se: float) -> str:
    """按修前展示公式独立复算单研究行文本；溢出上界按降级契约期望为 "—"。

    有限值的期望来自与导出器完全相同的表达式 ``f"{math.exp(v):.4g}"``（测试
    内独立执行），因此对有限路径的断言即逐位不变断言；溢出值期望 None 的
    既有占位渲染 "—"（同 review_analysis.display 契约）。"""

    def show(value: float) -> str:
        try:
            out = math.exp(value)
        except OverflowError:
            return _DEGRADED
        return f"{out:.4g}"

    center = math.log(estimate)
    low, high = center - 1.96 * se, center + 1.96 * se
    return f"{show(center)} ({show(low)} to {show(high)})"


def _ooxml_text(payload: bytes) -> str:
    """OOXML（docx/pptx）zip 内全部 xml 的拼接文本。"""
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return "".join(archive.read(name).decode("utf-8", "replace")
                       for name in archive.namelist() if name.endswith(".xml"))


def _figure_svgs(markdown_text: str) -> str:
    """markdown 内联 data-URI SVG 图形解码后的拼接文本。"""
    encoded = re.findall(r"data:image/svg\+xml;base64,([A-Za-z0-9+/=]+)", markdown_text)
    return "".join(base64.b64decode(part).decode("utf-8") for part in encoded)


class Mm10DisplayValueContractTests(unittest.TestCase):
    """模块级钉子：有限路径逐位不变 + 溢出/非有限降级契约（回退修复必红）。"""

    MODULES = (synthesis_report, manuscript_builder)

    def test_finite_inputs_bit_identical_to_original_formula(self):
        """全部有限输入下两条 _display_value 必须与原公式逐位相同。"""
        finite = [-745.0, -708.0, -708.8269, -1.5, 0.0, LN2, 1.0, 708.0, 708.2531, 709.0, 709.6251]
        for module in self.MODULES:
            for value in finite:
                for measure in _LOG_MEASURES:  # math.exp 分支（含次正规下溢 0.0）
                    self.assertEqual(module._display_value(value, measure), math.exp(value),
                                     f"{module.__name__}._display_value({value}, {measure})")
                self.assertEqual(module._display_value(value, "FISHER_Z"), math.tanh(value))
                self.assertEqual(module._display_value(value, "MEAN"), value)  # 恒等分支
            for value in (0.0, 0.5, 1.0, 37.0):  # LOGIT_PROP v>=0 分支
                self.assertEqual(module._display_value(value, "LOGIT_PROP"),
                                 1 / (1 + math.exp(-value)))
            for value in (-37.0, -1.0, -0.5):  # LOGIT_PROP v<0 分支
                self.assertEqual(module._display_value(value, "LOGIT_PROP"),
                                 math.exp(value) / (1 + math.exp(value)))

    def test_overflow_and_non_finite_degrade_to_none(self):
        """溢出/非有限展示值降级为 None（review_analysis.display 同契约）。

        修前：math.exp 对 >709.786 的值抛 OverflowError（本用例红）。"""
        for module in self.MODULES:
            for value in (710.0, 710.2131, 800.0, 1000.0):
                for measure in _LOG_MEASURES:
                    self.assertIsNone(module._display_value(value, measure),
                                      f"{module.__name__}._display_value({value}, {measure})")
            self.assertIsNone(module._display_value(float("inf"), "MEAN"))
            self.assertIsNone(module._display_value(float("nan"), "MEAN"))
        # None 占位渲染走两个格式化器的既有分支（与池效应行同款 "—"）。
        self.assertEqual(synthesis_report._number(None), _DEGRADED)
        self.assertEqual(manuscript_builder._num(None), _DEGRADED)


class Mm10ExportOverflowEndpointTests(unittest.TestCase):
    """端点级钉子（真实 TestClient）：MM10-03 三条路径 + 正常对照。"""

    @classmethod
    def setUpClass(cls):
        # raise_server_exceptions=False：修前 500 以状态码呈现，钉子断言 200 契约本身
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        # 不走 HTTP 注册（多模块共享限流桶会撞 429，同 test_r7_mm5be_backend_caliber 手法）
        cls.user = auth_mod.create_user("r7final@example.test", "R7Final-Export-2026")
        cls.token = auth_mod.create_access_token(cls.user["id"], cls.user["username"])
        cls.task = cls.client.post(
            "/api/tasks", json={"name": "r7-final-export"},
            headers=cls.auth_header()).json()["task_id"]
        info = tasks_mod.find_task(cls.task, DATA_DIR)
        cls.db = info.dir / (tasks_mod.slugify_screener(cls.user["username"]) + ".db")
        db_mod.init_db(cls.db)
        db_mod.upsert_articles(
            cls.db, [Article(zotero_key=f"K{i}", title=f"T{i}") for i in range(1, 7)], [])
        for i in range(1, 7):
            save_study(cls.db, f"S{i}", f"Study {i}", [f"K{i}"])
        # 三个互不混池的 stratum：溢出组 se=362；正常对照组 se=361.7 / 361.0
        strata = (("C-overflow", "S1", "S2", SE_OVERFLOW),
                  ("C-se361-7", "S3", "S4", SE_NORMAL_361_7),
                  ("C-se361-0", "S5", "S6", SE_NORMAL_361_0))
        for comparison, first, second, se in strata:
            for study in (first, second):
                save_effect(cls.db, study, comparison, "O", "T", "RR", RR_ESTIMATE, se,
                            f"K{study[1:]}")

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    @classmethod
    def auth_header(cls):
        return {"Authorization": "Bearer " + cls.token}

    def _report(self, comparison: str, fmt: str):
        return self.client.get(
            f"/api/tasks/{self.task}/analysis/report",
            params={"comparison": comparison, "outcome": "O", "timepoint": "T",
                    "measure": "RR", "model": "fixed", "ci_method": "normal", "format": fmt},
            headers=self.auth_header())

    def _generate_markdown(self) -> str:
        response = self.client.post(
            f"/api/tasks/{self.task}/analysis/manuscript/generate",
            json={"format": "markdown", "sections": ["all"], "include_figures": True,
                  "reference_style": "ama", "model": "fixed", "ci_method": "normal"},
            headers=self.auth_header())
        self.assertEqual(response.status_code, 200, response.text[:800])
        return response.text

    def test_mm10_03_docx_overflow_degrades_and_stays_200(self):
        """se=362 的 DOCX 导出必须 200、产物可解析、降级占位可见（修前 500）。"""
        response = self._report("C-overflow", "docx")
        self.assertEqual(response.status_code, 200, response.text[:800])
        self.assertIn("wordprocessingml", response.headers.get("content-type", ""))
        payload = response.content
        self.assertTrue(payload.startswith(b"PK"), "docx 必须是可解析的 OOXML zip（PK 头）")
        self.assertIn(_expected_row(RR_ESTIMATE, SE_OVERFLOW), _ooxml_text(payload),
                      "溢出上界必须降级为既有 — 占位且在逐研究行可见")

    def test_mm10_03_pptx_overflow_degrades_and_stays_200(self):
        """se=362 的 PPTX 导出必须 200、产物可解析、降级占位可见（修前 500）。"""
        response = self._report("C-overflow", "pptx")
        self.assertEqual(response.status_code, 200, response.text[:800])
        self.assertIn("presentationml", response.headers.get("content-type", ""))
        payload = response.content
        self.assertTrue(payload.startswith(b"PK"), "pptx 必须是可解析的 OOXML zip（PK 头）")
        self.assertIn(_expected_row(RR_ESTIMATE, SE_OVERFLOW), _ooxml_text(payload))

    def test_mm10_03_manuscript_generate_and_preview_overflow_degrade(self):
        """se=362 的稿件生成/预览必须 200、JSON 可解析、降级占位可见（修前 500）。"""
        markdown_text = self._generate_markdown()  # POST generate（修前在此 500）
        svgs = _figure_svgs(markdown_text)
        self.assertTrue(svgs, "markdown 必须内联森林图 SVG")
        self.assertIn(_expected_row(RR_ESTIMATE, SE_OVERFLOW), svgs)
        preview = self.client.get(
            f"/api/tasks/{self.task}/analysis/manuscript/preview",
            params={"reference_style": "ama", "model": "fixed", "ci_method": "normal"},
            headers=self.auth_header())
        self.assertEqual(preview.status_code, 200, preview.text[:800])
        body = preview.json()  # manuscript JSON 必须可解析（修前 500）
        self.assertIn(_expected_row(RR_ESTIMATE, SE_OVERFLOW), _figure_svgs(body["markdown"]))

    def test_mm10_03_normal_control_se_361_and_361_7_bitpath_unchanged(self):
        """正常对照：se=361 / 361.7 三路径 200 且逐研究数值与原公式逐位一致。"""
        for comparison, se in (("C-se361-7", SE_NORMAL_361_7), ("C-se361-0", SE_NORMAL_361_0)):
            row = _expected_row(RR_ESTIMATE, se)
            for fmt in ("docx", "pptx"):
                response = self._report(comparison, fmt)
                self.assertEqual(response.status_code, 200,
                                 f"{comparison} {fmt}: {response.status_code} {response.text[:400]}")
                self.assertTrue(response.content.startswith(b"PK"))
                self.assertIn(row, _ooxml_text(response.content),
                              f"{comparison} {fmt} 的有限上界渲染必须与原公式逐位相同")
            self.assertIn(row, _figure_svgs(self._generate_markdown()))
