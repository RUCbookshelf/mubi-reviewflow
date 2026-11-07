"""r7 MM9 后端请求钉子：422 校验回显里的非有限浮点不得炸掉序列化。

覆盖（把对应修复删掉，断言必须变红）：
- MM9-04：请求体携带裸 ``NaN``/``Infinity``/``-Infinity`` 字面量时，
  pydantic 以 ``finite_number`` 拒绝（应为 422），但错误 ``input`` 是原
  float——修复前 ``_clip_validation_echo`` 只按 repr 长度裁剪放行原值，
  ``JSONResponse(allow_nan=False)`` 在 422 处理器内渲染即抛
  ``ValueError: Out of range float values are not JSON compliant`` → 500。
  修复后非有限浮点（含容器内嵌套）先替换为 ``<non-finite …>`` 占位字符串
  再做长度裁剪 → 422 正常返回。
- MM9-04 R2（复审二轮）：清洗的深度上限处曾"原样放行"容器子树——
  9 层列表 [[…[NaN]…]] 的 repr 仅 21 字符，长度裁剪不设防，嵌套非有限
  浮点逃逸 → 同一 500（NNT 端点 estimate 同样可达）。修复后超深容器
  子树整体替换为 ``<deep nesting omitted>`` 占位字符串，任意深度的
  非有限浮点都不再进入序列化。

破坏即红（R4）：
- 复现步骤见 ``Mm9NonfiniteEchoRedTests``：把 ``backend_main
  ._clip_validation_echo`` 运行时 patch 回修复前实现（仅按 repr 长度
  裁剪、非有限浮点原样返回），同一请求立即回到 500——即上面全部 422
  断言在修复被撤掉时必然失败。deepseek 复审（out/external_reviews/
  mm9_verify_g2.md §1.2(b)(d)）已用同一因果对照独立确认：真实 app +
  TestClient 裸字面量三值均 500，patch 占位实现后立即 422。

测试口径（与复审探针一致）：``dependency_overrides[require_user]`` 注入
已登录用户——绕过 ``require_user`` 内部的任务存在性预检（main.py 的
``_task_or_404``），否则不存在的任务 T1 会在**进入请求体校验之前**就
404，测不到 422/500 分叉。对照 0.95（合法体）经端点内 ``task_or_404``
落到 404「任务不存在。」。
"""
import json as json_mod
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
fixture = tempfile.TemporaryDirectory(prefix="rf_r7_mm9_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient

from custom_backend import main as backend_main
from custom_backend.main import app

#: 与被测端点契约一致的合法基线体（dta_exploratory_lr.ExploratoryLrIn），
#: 仅替换 confidence 的字面量。裸 NaN/Infinity/-Infinity 不是合法 JSON，
#: 但 python ``json.loads``（FastAPI 默认体解析）接受这三个扩展字面量。
_LR_BODY = ('{"index_test":"IT","target_condition":"TC","threshold":"TH",'
            '"reference_standard":"RS","model":"fixed","correction":"none",'
            '"confidence": %s}')
_LR_URL = "/api/tasks/T1/analysis/dta/exploratory-likelihood-ratios"
_NNT_URL = "/api/tasks/T1/analysis/nnt-conversion"


def _nested(value, wraps: int):
    """value 外面包 wraps 层列表（json.dumps(allow_nan=True) 可序列化 NaN）。"""
    for _ in range(wraps):
        value = [value]
    return value


class _ClientMixin(unittest.TestCase):
    """真实 app + TestClient（raise_server_exceptions=False 才能观测 500）。"""

    @classmethod
    def setUpClass(cls):
        # 注入已登录用户：不覆盖则 require_user 先按 task_id 做 404 预检，
        # 请求体校验根本不会执行（deepseek 复审 §1.2(b) 同口径）。
        app.dependency_overrides[backend_main.require_user] = lambda: {
            "user_id": 1, "username": "mm9-probe"}
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        app.dependency_overrides.pop(backend_main.require_user, None)

    def post_confidence(self, literal: str):
        return self.client.post(
            _LR_URL, content=(_LR_BODY % literal).encode("utf-8"),
            headers={"Content-Type": "application/json"})


class Mm9NonfiniteEchoReturns422Tests(_ClientMixin):
    """MM9-04 主钉子：裸 NaN/Infinity/-Infinity 必须 422（不是 500）。"""

    def test_mm9_04_nonfinite_confidence_is_422_not_500(self):
        for literal in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(literal=literal):
                r = self.post_confidence(literal)
                self.assertNotEqual(r.status_code, 500,
                                    f"{literal} 不得再炸成 500：{r.text[:200]}")
                self.assertEqual(r.status_code, 422, r.text[:300])
                # 响应体必须是可解析 JSON（修复前正是在序列化阶段崩溃）
                detail = r.json()["detail"]
                self.assertTrue(any(e.get("type") == "finite_number"
                                    for e in detail), detail)
                # 非有限浮点以占位字符串回显，不再原样携带 float('nan')
                echoed = [repr(e.get("input")) for e in detail]
                self.assertTrue(
                    any("non-finite" in echo for echo in echoed), echoed)
                self.assertTrue(
                    all("nan" != echo for echo in echoed), echoed)

    def test_mm9_04_nonfinite_in_containers_also_422(self):
        """错误 input 是整个畸形体（list/dict 容器内嵌 NaN）同样不得 500。

        修复的递归清洗分支：容器内非有限浮点也要换成占位字符串。
        """
        for name, raw in (
            ("list body", "[NaN]"),
            ("nested dict", '{"confidence": {"x": NaN}}'),
        ):
            with self.subTest(shape=name):
                r = self.client.post(
                    _LR_URL, content=raw.encode("utf-8"),
                    headers={"Content-Type": "application/json"})
                self.assertNotEqual(r.status_code, 500, r.text[:200])
                self.assertEqual(r.status_code, 422, r.text[:300])
                self.assertIn("non-finite", r.text)

    def test_mm9_04_nonfinite_beyond_sanitize_depth_is_422_not_500(self):
        """R2 钉子：深度超过清洗上限的容器内 NaN 也不得 500。

        复审二轮实测：深度上限处原样放行时，9 层列表内的 NaN repr 仅
        21 字符（< 1024），长度裁剪不设防 → 逃逸 → 500。修复后超深
        容器子树整体替换为 ``<deep nesting omitted>`` → 422。
        """
        for depth in (9, 20, 64):
            with self.subTest(depth=depth):
                body = {"index_test": _nested(float("nan"), depth),
                        "target_condition": "TC", "threshold": "TH",
                        "reference_standard": "RS", "model": "fixed",
                        "correction": "none", "confidence": 0.5}
                r = self.client.post(
                    _LR_URL,
                    content=json_mod.dumps(body, allow_nan=True).encode("utf-8"),
                    headers={"Content-Type": "application/json"})
                self.assertNotEqual(
                    r.status_code, 500,
                    f"{depth} 层列表内 NaN 不得再炸成 500：{r.text[:200]}")
                self.assertEqual(r.status_code, 422, r.text[:300])
                r.json()  # 响应体必须可解析（修复前正是在序列化阶段崩溃）
                # 裸 NaN 字面量不再出现（占位是小写 nan / deep nesting）
                self.assertNotIn("NaN", r.text)
                self.assertNotIn("Infinity", r.text)

    def test_mm9_04_deep_nonfinite_on_nnt_endpoint_is_422_not_500(self):
        """R2 钉子（复审二轮第二个可达端点）：NNT estimate 深嵌 NaN → 422。"""
        body = {"measure": "rd", "estimate": _nested(float("inf"), 9),
                "lower": 0.1, "upper": 0.9, "level": 0.5,
                "adverse_event_confirmed": True, "time_horizon": "12 months"}
        r = self.client.post(
            _NNT_URL,
            content=json_mod.dumps(body, allow_nan=True).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        self.assertNotEqual(r.status_code, 500, r.text[:200])
        self.assertEqual(r.status_code, 422, r.text[:300])
        self.assertNotIn("Infinity", r.text)
        # 对照：合法体通过校验 → 端点内 task_or_404("T1") → 404
        ok = dict(body, estimate=0.2)
        r_ok = self.client.post(
            _NNT_URL,
            content=json_mod.dumps(ok).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        self.assertEqual(r_ok.status_code, 404, r_ok.text[:200])


class Mm9NonfiniteEchoContrastTests(_ClientMixin):
    """对照口径（复审 §1.2(b)）：0.95 → 404；"abc" → 422；字符串 "NaN" → 422。"""

    def test_mm9_04_valid_confidence_falls_through_to_task_404(self):
        r = self.post_confidence("0.95")
        # 合法体通过校验 → 端点内 task_or_404("T1") → 任务不存在 404
        self.assertEqual(r.status_code, 404, r.text[:200])
        self.assertEqual(r.json()["detail"], "任务不存在。")

    def test_mm9_04_unparsable_string_is_422_with_untouched_echo(self):
        r = self.post_confidence('"abc"')
        self.assertEqual(r.status_code, 422, r.text[:200])
        detail = r.json()["detail"]
        entry = next(e for e in detail if e.get("type") == "float_parsing")
        # 字符串回显原样保留——修复只处理非有限浮点，不影响普通输入
        self.assertEqual(entry["input"], "abc")

    def test_mm9_04_nonfinite_string_literal_is_422(self):
        """字符串 "NaN"（strict=False 会先被转成 nan 再被拒）回显保留原串。"""
        r = self.post_confidence('"NaN"')
        self.assertEqual(r.status_code, 422, r.text[:200])
        detail = r.json()["detail"]
        entry = next(e for e in detail if e.get("type") == "finite_number")
        self.assertEqual(entry["input"], "NaN")


class Mm9NonfiniteEchoRedTests(_ClientMixin):
    """破坏即红：把修复撤掉（patch 回修复前实现），同一请求必须回到 500。

    复现步骤（等价于 revert main.py 的 MM9-04 修复）：
    1. 用修复前实现覆盖 ``backend_main._clip_validation_echo``（仅按
       repr 长度裁剪、非有限浮点与超深容器原样返回）；
    2. POST 裸 NaN 体 / 9 层列表内 NaN 体 → 500（全局 Exception 处理器
       兜底文案；R2：深嵌 NaN 的 repr 仅 21 字符，长度裁剪同样不设防）；
    3. 恢复原实现 → 同一请求回到 422。
    本用例执行第 2 步断言 500，即证明上面两组 422 钉子在修复被删时必红。
    """

    def test_mm9_04_without_fix_request_fails_with_500(self):
        def vulnerable_clip(value):  # noqa: ANN001 修复前实现（main.py MM9-04 前）
            try:
                size = len(repr(value))
            except Exception:  # pragma: no cover
                return "<omitted>"
            if size > backend_main._MAX_VALIDATION_ECHO_CHARS:
                return f"<{size} chars omitted>"
            return value  # 非有限浮点/超深容器原样放行 → 序列化崩溃

        original = backend_main._clip_validation_echo
        backend_main._clip_validation_echo = vulnerable_clip
        try:
            r = self.post_confidence("NaN")
            self.assertEqual(
                r.status_code, 500,
                "修复前实现下应观察到 500；若此处为 422，说明修复未真正"
                "绑定该路径，破坏即红失效")
            # R2：深嵌 NaN（长度裁剪不设防的第二条逃逸路径）同样回到 500
            deep = {"index_test": _nested(float("nan"), 9),
                    "target_condition": "TC", "threshold": "TH",
                    "reference_standard": "RS", "model": "fixed",
                    "correction": "none", "confidence": 0.5}
            r_deep = self.client.post(
                _LR_URL,
                content=json_mod.dumps(deep, allow_nan=True).encode("utf-8"),
                headers={"Content-Type": "application/json"})
            self.assertEqual(
                r_deep.status_code, 500,
                "修复前实现下深嵌 NaN 应观察到 500（R2 逃逸路径）")
        finally:
            backend_main._clip_validation_echo = original
        # 恢复修复后同一请求回到 422（因果闭环）
        r = self.post_confidence("NaN")
        self.assertEqual(r.status_code, 422, r.text[:200])


class Mm9ClipEchoHelperTests(unittest.TestCase):
    """_clip_validation_echo 单元口径：正常值原样、超大裁剪、非有限占位。"""

    def test_clip_normal_and_oversize_unchanged_behavior(self):
        clip = backend_main._clip_validation_echo
        # MM2-02 既有行为不得回归：正常小值原样、超大值占位
        self.assertEqual(clip(0.95), 0.95)
        self.assertEqual(clip("abc"), "abc")
        oversized = clip("x" * 3000)
        self.assertTrue(str(oversized).startswith("<"), oversized)
        self.assertIn("chars omitted", str(oversized))

    def test_clip_nonfinite_floats_become_strings(self):
        clip = backend_main._clip_validation_echo
        self.assertEqual(clip(float("nan")), "<non-finite nan>")
        self.assertEqual(clip(float("inf")), "<non-finite inf>")
        self.assertEqual(clip(float("-inf")), "<non-finite -inf>")
        # 容器内嵌套同样清洗；有限浮点与其它类型不受影响
        self.assertEqual(clip({"a": float("nan"), "b": 1.5}),
                         {"a": "<non-finite nan>", "b": 1.5})
        self.assertEqual(clip([float("inf"), "ok"]),
                         ["<non-finite inf>", "ok"])

    def test_clip_deep_containers_substituted_not_passed_through(self):
        """R2：深度上限处必须整体替换子树，不得原样放行。

        边界口径：``_nested(v, N)`` 是 N 层列表，最内层值在清洗深度 N。
        上限内（N=8）逐层清洗到标量；超限（N=9）子树整体占位；干净的超深
        容器同样占位（展示层折衷：该形状不在本产品任何合法请求体中）。
        """
        clip = backend_main._clip_validation_echo
        # 上限内：8 层列表包 NaN → 逐层递归到标量占位
        cleaned = clip(_nested(float("nan"), 8))
        self.assertEqual(cleaned, _nested("<non-finite nan>", 8))
        # 超限：9 层列表包 NaN → 第 9 层容器整体替换为占位字符串
        deep_dirty = clip(_nested(float("nan"), 9))
        self.assertEqual(deep_dirty, _nested("<deep nesting omitted>", 8))
        # 超限但"干净"的容器同样整体占位——深度上限绝不原样放行容器
        deep_clean = clip(_nested("ok", 9))
        self.assertEqual(deep_clean, _nested("<deep nesting omitted>", 8))
        # 顶层标量不受深度上限影响（浮点检查在最前）
        self.assertEqual(clip(float("nan")), "<non-finite nan>")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
