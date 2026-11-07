"""W4 预置分类器（B3）钉子——破坏即红。

覆盖四层：

1. **分类函数边界**（``coscreen/al/study_design.py``）：空值/缺键/形态异常
   降级、规则优先级（meta>sysrev>rct）、七类设计的正边界与词边界、
   raw_json 出版类型兜底（rispy unknown_tag['PT'] / 具名列 / type_of_reference
   / 纯 'JOUR' 不命中）、主信号压轴、非 dict 输入 ValueError、
   预填建议只在「识别为非 RCT 的具体设计」时给出。
2. **评估夹具 × 文档互锁**：实时重算 PubMed 公开样例夹具（48 条）的
   precision/recall，与 ``docs/methodology/study_design_classifier.md`` §5
   机器可读块逐值比对；文档的头条主张（RCT 全管线 1.00/1.00、识别类
   precision ≥ 0.50、5/7 类 = 1.00）同样钉死——规则或夹具漂移即红。
3. **真实 TestClient**（端点 ``GET /api/tasks/{id}/study-designs``）：
   401/404/403 门禁、正常路径标签正确、**失败降级**（读库抛错 → 200 +
   available=false，绝不 500）。
4. **静态守卫**：index.html 三处锚点（徽标插在 AL 分数条与摘要之间、
   预填在 restoreReason 之后 markDecisionClean 之前、script 令牌
   ``20261030-r8-w4sd1`` 且不挤占 W5 末尾 SW 锚点）；前端模块红线
   （IIFE/RFLang.register/textContent-only、无 innerHTML/eval/fetch/外链）；
   后端模块纯本地（无 urllib/http/socket/sklearn 导入）且规则**复用**
   ``evidence_mapper._STUDY_DESIGN_RULES``（import 断言，镜像实现即红）；
   release.yml 把本组 node 行为套件接入 node --test 门禁（MM11-01 同型）。
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

_ENV_KEYS = ("REVIEWFLOW_MODE", "COBOOKSHELF_DATA_DIR")
_ORIGINAL_ENV = {key: os.environ.get(key) for key in _ENV_KEYS}
_fixture = tempfile.TemporaryDirectory(prefix="rf_w4_study_design_")
os.environ["COBOOKSHELF_DATA_DIR"] = _fixture.name
os.environ.setdefault("REVIEWFLOW_MODE", "server")


def tearDownModule():
    """恢复导入期环境（r13 测试卫生，与 W2 同款）：不泄漏临时目录/运行模式。"""
    for key, value in _ORIGINAL_ENV.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

from fastapi.testclient import TestClient  # noqa: E402

from custom_backend.main import app  # noqa: E402
from custom_backend import main as main_mod  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from coscreen import db  # noqa: E402
from coscreen.al import study_design as sd  # noqa: E402
from coscreen.models import Article  # noqa: E402

FIXTURE = REPO / "build" / "tests" / "study_design_fixture.json"
DOC = REPO / "docs" / "methodology" / "study_design_classifier.md"
FRONTEND = REPO / "custom_frontend" / "study_design_badge.js"
INDEX_HTML = REPO / "custom_frontend" / "index.html"
MAIN_PY = REPO / "custom_backend" / "main.py"
API_PY = REPO / "custom_backend" / "study_design_api.py"
BADGE_JS = "study_design_badge.js"
TOKEN = "20261030-r8-w4sd1"

DESIGN_LABELS = ("meta_analysis", "systematic_review", "rct", "cohort",
                 "case_control", "cross_sectional", "diagnostic")


def _classify(article):
    return sd.classify_article(article)


class StudyDesignBoundaryNails(unittest.TestCase):
    """分类函数边界（破坏即红：任何规则/加权回退都会命中下面某条）。"""

    def test_non_dict_input_raises_value_error(self):
        for bad in (None, "rct", 42, [("title", "x")]):
            with self.assertRaises(ValueError):
                sd.classify_article(bad)

    def test_empty_and_missing_fields_degrade_to_other(self):
        for article in ({}, {"title": None, "abstract": None},
                        {"title": "", "abstract": ""}, {"raw": "not-a-dict"}):
            result = _classify(article)
            self.assertEqual(result["label"], "other")
            self.assertFalse(result["recognized"])
            self.assertFalse(result["is_rct"])
            self.assertIsNone(result["source"])
            self.assertIsNone(result["matched_keyword"])
            self.assertIsNone(result["suggest_exclusion_reason"])

    def test_rule_precedence_meta_beats_sysrev_beats_rct(self):
        text = "A systematic review and meta-analysis of randomized controlled trials"
        self.assertEqual(_classify({"title": text, "abstract": ""})["label"], "meta_analysis")
        self.assertEqual(
            _classify({"title": "", "abstract": "systematic review of randomized controlled trials"})["label"],
            "systematic_review")

    def test_rct_patterns_cover_spelling_and_allocation_phrasings(self):
        for text in ("A randomized controlled trial of drug X",
                     "A randomised trial of drug X",
                     "Patients were randomly allocated to two groups",
                     "randomly assigned 1:1 to surgery"):
            self.assertEqual(_classify({"title": text, "abstract": ""})["label"], "rct", text)
            self.assertEqual(_classify({"title": "", "abstract": text})["source"], "title_abstract")

    def test_observational_and_diagnostic_boundaries(self):
        cases = {
            "cohort": ("A prospective cohort study", "cohorts of workers"),
            "case_control": ("A case-control study", "case control study"),
            "cross_sectional": ("A cross-sectional survey", "cross sectional survey"),
            "diagnostic": ("Diagnostic accuracy of index test",
                           "we report sensitivity and specificity of the assay"),
        }
        for label, (title, abstract) in cases.items():
            self.assertEqual(_classify({"title": title, "abstract": ""})["label"], label, title)
            self.assertEqual(_classify({"title": "", "abstract": abstract})["label"], label, abstract)
        # 词边界：cohortectomy 不是队列研究；plain review 不是系统综述
        self.assertEqual(_classify({"title": "cohortectomy outcomes", "abstract": ""})["label"], "other")
        self.assertEqual(_classify({"title": "A narrative review", "abstract": ""})["label"], "other")

    def test_raw_publication_type_fallback_and_shapes(self):
        base = {"title": "No design words here", "abstract": "Plain abstract."}
        # rispy 未知标签形态（PubMed RIS 导入：unknown_tag['PT'] 列表）
        raw_ris = {"unknown_tag": {"PT": ["Journal Article", "Randomized Controlled Trial"]}}
        result = _classify({**base, "raw": raw_ris})
        self.assertEqual((result["label"], result["source"]), ("rct", "raw_json"))
        # CSV 列名形态 / rispy type_of_reference（'JOUR' 不命中任何规则）
        self.assertEqual(_classify({**base, "raw": {"publication_type": ["Systematic Review"]}})["label"],
                         "systematic_review")
        self.assertEqual(_classify({**base, "raw": {"type_of_reference": "JOUR"}})["label"], "other")
        # Article.to_dict() 形态：raw 以 raw_json 字符串携带
        article = Article(zotero_key="K1", title=base["title"], abstract=base["abstract"], raw=raw_ris)
        self.assertEqual(_classify(article.to_dict())["label"], "rct")
        # raw_json 损坏 → 静默降级为 other
        self.assertEqual(_classify({**base, "raw_json": "{broken"})["label"], "other")

    def test_primary_signal_beats_raw_fallback(self):
        # PubMed 会给「纳入 RCT 的 Meta 分析」同时挂 Meta-Analysis 与 RCT 两个
        # 出版类型——题名的主信号必须压轴
        raw = {"unknown_tag": {"PT": ["Meta-Analysis", "Randomized Controlled Trial"]}}
        self.assertEqual(_classify({"title": "A meta-analysis of trials", "abstract": "", "raw": raw})["label"],
                         "meta_analysis")

    def test_exclusion_suggestion_only_for_recognized_non_rct(self):
        expectations = {
            "rct": None, "other": None, "meta_analysis": "研究设计不符",
            "systematic_review": "研究设计不符", "cohort": "研究设计不符",
            "case_control": "研究设计不符", "cross_sectional": "研究设计不符",
            "diagnostic": "研究设计不符",
        }
        for label, expected in expectations.items():
            self.assertEqual(sd.suggest_exclusion_reason(label), expected, label)
        self.assertEqual(_classify({"title": "A randomized controlled trial", "abstract": ""})
                         ["suggest_exclusion_reason"], None)
        self.assertEqual(_classify({"title": "A cohort study", "abstract": ""})
                         ["suggest_exclusion_reason"], "研究设计不符")

    def test_result_shape_is_json_serializable_and_stable(self):
        result = _classify({"zotero_key": "K9", "title": "Randomized trial", "abstract": ""})
        self.assertEqual(result["zotero_key"], "K9")
        self.assertEqual(result["rule_version"], sd.RULE_VERSION)
        self.assertTrue(result["is_rct"])
        self.assertTrue(result["matched_keyword"])
        json.dumps(result)  # 不抛错
        # classify_corpus：空键跳过、重复键后写者胜
        corpus = sd.classify_corpus([
            {"zotero_key": "", "title": "cohort"},
            {"zotero_key": "A", "title": "cohort"},
            {"zotero_key": "A", "title": "randomized trial"},
        ])
        self.assertEqual(list(corpus), ["A"])
        self.assertEqual(corpus["A"]["label"], "rct")


class FixtureDocLockNails(unittest.TestCase):
    """夹具 × 文档互锁：实测 precision/recall 与披露逐值一致（漂移即红）。"""

    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.golds = {item["zotero_key"]: item["gold"] for item in cls.data["items"]}
        cls.full = {item["zotero_key"]: _classify(item) for item in cls.data["items"]}
        cls.title_abstract = {item["zotero_key"]: _classify({**item, "raw": {}})
                              for item in cls.data["items"]}

    def _metrics(self, results, label):
        tp = sum(1 for k in self.golds if results[k]["label"] == label and self.golds[k] == label)
        fp = sum(1 for k in self.golds if results[k]["label"] == label and self.golds[k] != label)
        fn = sum(1 for k in self.golds if results[k]["label"] != label and self.golds[k] == label)
        return {"tp": tp, "fp": fp, "fn": fn,
                "precision": round(tp / (tp + fp), 2) if tp + fp else None,
                "recall": round(tp / (tp + fn), 2) if tp + fn else None}

    def test_fixture_shape(self):
        self.assertEqual(len(self.data["items"]), 48)
        keys = [item["zotero_key"] for item in self.data["items"]]
        self.assertEqual(len(set(keys)), 48,
                         "复审 issue 3：夹具必须是 48 篇独立文献（跨类重复已剔除）——"
                         "重复键会让「键数 / 记录数」口径分歧，文档指标随之失真")
        per_class = {}
        for item in self.data["items"]:
            per_class[item["gold"]] = per_class.get(item["gold"], 0) + 1
        self.assertEqual(per_class, {label: 6 for label in
                                     DESIGN_LABELS + ("other",)})
        self.assertEqual(self.data["meta"]["per_class"], 6)
        self.assertIn("duplicates_skipped", self.data["meta"], "取数脚本必须记录被剔除的跨类重复数")

    def test_doc_machine_block_matches_live_recompute(self):
        match = re.search(r"```json\n(.*?)\n```", DOC.read_text(encoding="utf-8"), re.S)
        self.assertIsNotNone(match, "方法学文档 §5 必须携带机器可读评估块")
        documented = json.loads(match.group(1))
        self.assertEqual(documented["rule_version"], sd.RULE_VERSION)
        self.assertEqual(documented["n"], len(self.data["items"]))
        for label in DESIGN_LABELS + ("other",):
            self.assertEqual(documented["per_class"]["full"][label],
                             self._metrics(self.full, label), f"full/{label}")
            self.assertEqual(documented["per_class"]["title_abstract_only"][label],
                             self._metrics(self.title_abstract, label), f"ablation/{label}")

    def test_doc_headline_claims_hold(self):
        """文档的头条主张（复审后按 48 篇独立文献重算）：RCT 全管线
        1.00/1.00（靠 raw 兜底）、识别类 precision ≥ 0.75、5/7 类 = 1.00、
        消融 RCT 召回 0.50。任一失守（规则改动/夹具漂移）即红。"""
        full = {label: self._metrics(self.full, label) for label in DESIGN_LABELS}
        self.assertEqual((full["rct"]["precision"], full["rct"]["recall"]), (1.0, 1.0))
        ablation = self._metrics(self.title_abstract, "rct")
        self.assertEqual(ablation["recall"], 0.5,
                         "消融列的 RCT 召回 0.50 是 raw 兜底价值的直接证据，失守即红")
        precisions = {label: values["precision"] for label, values in full.items()}
        self.assertGreaterEqual(min(precisions.values()), 0.75)
        self.assertEqual(sum(1 for value in precisions.values() if value == 1.0), 5)
        # 「未识别」行按类级口径：tp 为真病例报告、fp 为落入未识别的其他类别，
        # 两者之和 = 被判为 other 的记录数（本夹具 20 条），而 FN = other 金标里
        # 被识别成某个设计类的记录（本夹具 1 条）。
        other = self._metrics(self.full, "other")
        predicted_other = sum(1 for key, result in self.full.items() if result["label"] == "other")
        self.assertEqual(other["tp"] + other["fp"], predicted_other,
                         "other 行按类级口径统计：tp+fp = 被判为未识别的记录数")
        self.assertEqual(other["fn"], sum(1 for key, gold in self.golds.items() if gold == "other") - other["tp"],
                         "other 的 FN = 病例报告里被识别成某个具体设计的记录")

    def test_fn_breakdown_matches_documented_claim(self):
        """复审第 3 轮 issue 1：文档的 FN 汇总句必须与重算逐值一致。

        实测口径（读夹具逐条 classify_article）：识别类 FN 合计 17，其中 15
        条落入 other、2 条为 SR/MA 混合出版物被判成 Meta 分析（PMID 逐字钉）。
        旧文档写「合计 15 条（6+2+3+5+4+1+5 的中除 rct 外）」——数值与文字
        都错，本钉子防止回退。"""
        fn_by_gold = {label: 0 for label in DESIGN_LABELS}
        fn_destination = {}
        for key, gold in self.golds.items():
            if gold == "other":
                continue
            predicted = self.full[key]["label"]
            if predicted != gold:
                fn_by_gold[gold] += 1
                fn_destination[predicted] = fn_destination.get(predicted, 0) + 1
        self.assertEqual(fn_by_gold, {
            "meta_analysis": 0, "systematic_review": 2, "rct": 0, "cohort": 5,
            "case_control": 4, "cross_sectional": 1, "diagnostic": 5,
        })
        self.assertEqual(sum(fn_by_gold.values()), 17, "识别类 FN 合计")
        self.assertEqual(fn_destination.get("other", 0), 15, "其中落入 other 的条数")
        self.assertEqual(fn_destination.get("meta_analysis", 0), 2,
                         "另 2 条 SR/MA 混合出版物被判为 Meta 分析")
        doc = DOC.read_text(encoding="utf-8")
        self.assertIn("识别类 FN 合计 17 条", doc)
        self.assertIn("PMID-42959033", doc)
        self.assertIn("PMID-42959031", doc)
        self.assertNotIn("6+2+3+5+4+1+5", doc, "旧的错算括号必须清除")
        self.assertNotIn("识别类 FN 合计 15 条", doc)

    def test_truncation_attribution_is_measured_not_asserted(self):
        """复审第 3 轮 issue 2：文档把低召回归因于截断——该归因必须可从夹具
        文本复现，而不是一句无法核验的断言。

        可复现的硬事实（不联网，只用夹具 + 摘要截断常量）：
        - 42 条设计类金标的摘要**每一条**都恰好触及 400 字符截断上限；
        - 金标检索式要求规则词出现在题名/摘要，故「摘要前 400 字无规则词」
          必然意味着「规则词落在截断线之后」——文档据此把 11/17 的 FN 归因
          于截断，把 6/17 归因于规则本身（含 3 条 cohort 抢先、2 条 SR/MA、
          1 条规则覆盖缺口），并明确写出患者可见的结论「§4 的两列是截断
          度量的下限」。"""
        from build.scripts.fetch_study_design_fixture import ABSTRACT_LIMIT  # noqa: WPS433
        self.assertEqual(ABSTRACT_LIMIT, 400)
        design = [item for item in self.data["items"] if item["gold"] != "other"]
        self.assertEqual(len(design), 42)
        at_limit = [item for item in design if len(item["abstract"]) == ABSTRACT_LIMIT]
        self.assertEqual(len(at_limit), 42,
                         "全部 42 条设计金标的摘要都被截断到上限（截断归因的前提）")
        # 截断线前的规则词命中统计：cohort/diagnostic 各只有 1 条在前 400 字
        for label in ("cohort", "diagnostic"):
            rows = [item for item in design if item["gold"] == label]
            self.assertEqual(len(rows), 6)
            predicted = sum(1 for item in rows if self.full[item["zotero_key"]]["label"] == label)
            self.assertEqual(predicted, 1, f"{label}: 截断下仅 1/6 命中（文档口径）")
        doc = DOC.read_text(encoding="utf-8")
        for needle in ("400 字符之内" if False else "400 字符之后",
                       "11 条立即", "截断度量的下限", "cohort 0.60/1.00", "diagnostic 1.00/0.83"):
            self.assertIn(needle, doc, f"截断归因披露缺失：{needle}")
        self.assertNotIn("常常通篇不写规则词", doc,
                         "旧的错误归因（称记录不写设计词）必须清除——金标检索式已要求该词存在")

    def test_doc_text_matches_recomputed_numbers(self):
        """复审 issue 3：文档正文的行内数字必须与逐记录重算一致（防止只同步
        §5 机器块而正文表格/读法提示留在旧数）。"""
        doc = DOC.read_text(encoding="utf-8")
        boundary = self._metrics(self.full, "meta_analysis")
        self.assertIn(f"| Meta 分析 | {boundary['precision']:.2f} |", doc)
        rct_ablation = self._metrics(self.title_abstract, "rct")
        self.assertIn(f"{rct_ablation['recall']:.2f}", doc)
        self.assertIn("48 篇独立文献", doc, "去重口径必须在 §3 如实披露")
        self.assertNotIn("P=0.43", doc, "旧口径（46 键）的行内数字必须清除")
        self.assertNotIn("6/1/0", doc, "旧 Meta 分析 tp/fp/fn 必须清除")


class FetchScriptEgressNails(unittest.TestCase):
    """取数脚本的出站红线（复审第 3 轮 issue 4）——零网络回归钉子。

    复审指出：``_NoRedirectHandler`` / 局部 ``timeout`` / ``READ_LIMIT`` 只靠读码
    确认，没有回归钉子（W2/W3 各自的出站钉子则都有），且脚本 docstring 的
    「重跑可复现」不成立。本类用注入的假 opener 钉三件事：

    1. 3xx 响应在 opener 层被拒（``_NoRedirectHandler.redirect_request`` 返回
       None），而不是静默跟随到第二个主机；
    2. 每次请求都带显式 UA、局部 timeout ≥1s（不是进程级 setdefaulttimeout）
       且读取受 ``READ_LIMIT`` 截断；
    3. 端点固定为 https 的 NCBI 前缀（无 http://、无用户可控 host）。
    """

    @classmethod
    def setUpClass(cls):
        import importlib
        cls.mod = importlib.import_module("build.scripts.fetch_study_design_fixture")

    def test_no_redirect_handler_rejects_3xx(self):
        import urllib.request
        handler = self.mod._NoRedirectHandler()
        for code in (301, 302, 303, 307, 308):
            self.assertIsNone(
                handler.redirect_request(
                    urllib.request.Request("https://eutils.ncbi.nlm.nih.gov/x"),
                    None, code, "Moved", {"Location": "https://evil.example/x"},
                    "https://evil.example/x"),
                f"3xx({code}) 必须被拒绝（返回 None 让 urllib 抛 HTTPError）")

    def test_request_carries_ua_local_timeout_and_read_limit(self):
        """注入假 opener：断言 UA/超时入参/读取上限，全程零网络。"""
        seen = {}
        payload = b'{"esearchresult": {"idlist": ["1"]}}'

        class _Resp:
            def __init__(self, body):
                self._body = body

            def read(self, amount=None):
                seen["read_amount"] = amount
                return self._body[:amount] if amount else self._body

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class _Opener:
            def open(self, request, timeout=None):
                seen["url"] = request.full_url
                seen["ua"] = request.get_header("User-agent")
                seen["timeout"] = timeout
                return _Resp(payload)

        original = self.mod._OPENER
        self.mod._OPENER = _Opener()
        try:
            body = self.mod._get(f"{self.mod.BASE}/esearch.fcgi?db=pubmed&term=x")
        finally:
            self.mod._OPENER = original

        self.assertEqual(body, payload)
        self.assertTrue(seen["url"].startswith("https://eutils.ncbi.nlm.nih.gov/"),
                        "端点必须固定为 https 的 NCBI 前缀")
        self.assertIn("ReviewFlow", seen["ua"] or "", "必须携带显式 UA")
        self.assertIsNotNone(seen["timeout"], "超时必须显式传入（局部，而非进程级）")
        self.assertGreaterEqual(seen["timeout"], 1.0)
        self.assertLessEqual(seen["timeout"], 60.0, "短超时：不得超过 60s")
        self.assertEqual(seen["read_amount"], self.mod.READ_LIMIT,
                         "读取必须受上限约束")
        self.assertLessEqual(self.mod.READ_LIMIT, 5_000_000)

    def test_script_never_uses_process_wide_socket_timeout(self):
        """查代码不查注释：脚本注释里正因为写着『不用 socket.setdefaulttimeout』
        才必须剥离字符串/注释后再断言（同前端静态守卫口径）。"""
        raw_source = (REPO / "build" / "scripts" / "fetch_study_design_fixture.py").read_text(encoding="utf-8")
        source = re.sub(r'"""[\s\S]*?"""', "", raw_source)      # 模块/函数 docstring
        source = re.sub(r"#[^\n]*", "", source)   # strip line comments
        self.assertNotIn("socket.setdefaulttimeout", source,
                         "不得改进程级全局 socket 超时（复审 issue 4）")
        self.assertNotIn("import socket", source)
        self.assertNotIn("http://", source, "端点必须全 https")
        self.assertIn("_NoRedirectHandler", source)
        self.assertIn("timeout=TIMEOUT_SECONDS", source, "超时必须作为局部入参传入 opener")

    def test_docstring_states_reproducibility_honestly(self):
        source = (REPO / "build" / "scripts" / "fetch_study_design_fixture.py").read_text(encoding="utf-8")
        self.assertNotIn("重跑可复现清单", source, "旧的不实表述必须清除")
        self.assertIn("同构", source)
        self.assertIn("不能", source, "必须明说重跑不能逐字重现同一批 PMID")


class StudyDesignEndpointNails(unittest.TestCase):
    """真实 TestClient：门禁、正常路径、失败降级（绝不 500）。"""

    @classmethod
    def setUpClass(cls):
        # 顺序无关（r9 收尾）：模块级 setdefault("REVIEWFLOW_MODE", "server")
        # 只在导入期生效，挡不住**运行期**被别的模块改写。实测 test_r9_l7a_launcher
        # 在进程内走 build/launcher.py 的启动路径（launcher.py:461-463 把
        # REVIEWFLOW_MODE 改成 desktop 并 pop 掉 COBOOKSHELF_DATA_DIR），此后
        # main._server_mode()（custom_backend/main.py:956 每次现读环境）恒为
        # False，本类协作端点邀请返回 404「团队协作仅在服务器模式下开放」。
        # 故在本类运行期自己钉住 server 语义，收尾原样还原（不把自己的覆盖泄漏出去）。
        cls._saved_env = {key: os.environ.get(key)
                          for key in ("REVIEWFLOW_MODE", "COBOOKSHELF_DATA_DIR")}
        os.environ["REVIEWFLOW_MODE"] = "server"
        # auth.create_user 运行期现读 COBOOKSHELF_DATA_DIR，必须与导入期冻结的
        # main.DATA_DIR 同根（r13 测试卫生：模块级环境会被 W2 的 tearDownModule
        # 还原，若这里不重钉，用户库会落到仓库 data/ 与历史账号撞名）。
        os.environ["COBOOKSHELF_DATA_DIR"] = str(main_mod.DATA_DIR)
        # main.DATA_DIR 在导入期冻结（custom_backend/main.py:168），可能指向先前
        # 模块的临时目录且已被其 cleanup 删除（test_ai_suggest 的 tearDownClass
        # 就会删掉自己那枚）。本类不依赖「别人还没删」这一前提：用之前保证它存在。
        main_mod.DATA_DIR.mkdir(parents=True, exist_ok=True)
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.user = auth_mod.create_user("w4owner@example.test", "W4-Study-2026")
        cls.token = auth_mod.create_access_token(cls.user["id"], cls.user["username"])
        cls.outsider = auth_mod.create_user("w4outsider@example.test", "W4-Study-2026")
        cls.outsider_token = auth_mod.create_access_token(cls.outsider["id"], cls.outsider["username"])
        cls.task_id = cls.client.post(
            "/api/tasks", json={"name": "w4-study-design"},
            headers={"Authorization": "Bearer " + cls.token}).json()["task_id"]
        ctx = app.state.review_workflow.context(
            cls.task_id, {"user_id": cls.user["id"], "username": cls.user["username"]})
        db.upsert_articles(ctx.owner_db, [
            Article(zotero_key="A1", title="A randomised controlled trial of therapy X",
                    abstract="Multicentre study."),
            Article(zotero_key="A2", title="Effects of intervention",
                    abstract="This systematic review included 30 studies."),
            Article(zotero_key="A3", title="Plain title", abstract="Plain abstract.",
                    raw={"unknown_tag": {"PT": ["Journal Article", "Randomized Controlled Trial"]}}),
            Article(zotero_key="A4", title="A prospective cohort study", abstract=""),
            Article(zotero_key="A5", title="Plain", abstract="Nothing here."),
        ], [])

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        # 不 cleanup 数据目录（W2/W4 已统一口径，r13 测试卫生）：main.DATA_DIR
        # 在导入期冻结，指向的是**共享**目录；删掉它会让同进程后续模块建任务/
        # 建用户直接 401/500。临时目录交给进程退出/GC 回收，同一进程内保持可用。
        for key, value in cls._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def auth(self):
        return {"Authorization": "Bearer " + self.token}

    def url(self):
        return f"/api/tasks/{self.task_id}/study-designs"

    def test_requires_authentication(self):
        self.assertEqual(self.client.get(self.url()).status_code, 401)

    def test_unknown_task_404(self):
        r = self.client.get("/api/tasks/does-not-exist/study-designs", headers=self.auth())
        self.assertEqual(r.status_code, 404)

    def test_outsider_403(self):
        r = self.client.get(self.url(), headers={"Authorization": "Bearer " + self.outsider_token})
        self.assertEqual(r.status_code, 403)

    def test_happy_path_labels_and_suggestions(self):
        r = self.client.get(self.url(), headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["available"])
        self.assertFalse(body["truncated"])
        self.assertEqual(body["rule_version"], sd.RULE_VERSION)
        self.assertEqual(body["count"], 5)
        items = body["items"]
        self.assertEqual(items["A1"]["label"], "rct")
        self.assertIsNone(items["A1"]["suggest_exclusion_reason"])
        self.assertEqual(items["A2"]["label"], "systematic_review")
        self.assertEqual(items["A2"]["suggest_exclusion_reason"], "研究设计不符")
        self.assertEqual((items["A3"]["label"], items["A3"]["source"]), ("rct", "raw_json"))
        self.assertEqual(items["A4"]["label"], "cohort")
        self.assertEqual(items["A4"]["suggest_exclusion_reason"], "研究设计不符")
        self.assertEqual(items["A5"]["label"], "other")
        self.assertIsNone(items["A5"]["suggest_exclusion_reason"])

    def test_read_failure_degrades_never_500(self):
        """失败降级：目录读取抛错 → 200 + available=false + items={}（绝不 500）。"""
        with mock.patch.object(type(app.state.review_workflow), "eligible",
                               side_effect=RuntimeError("catalog exploded")):
            r = self.client.get(self.url(), headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertFalse(body["available"])
        self.assertEqual(body["items"], {})
        self.assertEqual(body["count"], 0)

    def test_import_failure_degrades_never_500(self):
        """失败降级（复审 issue 2）：分类模块**导入失败**也必须 200+available=false。

        复审实测：两个 import 原在 try 之外 → 注入 ImportError 时 HTTP 500。
        本钉子把 import 注入到 sys.modules 里抛 ImportError 的桩模块上，
        断言绝不 5xx。"""
        import builtins
        real_import = builtins.__import__

        def flaky_import(name, *args, **kwargs):
            if name == "coscreen.al.study_design":
                raise ImportError("simulated module failure")
            return real_import(name, *args, **kwargs)

        with mock.patch.object(builtins, "__import__", side_effect=flaky_import):
            r = self.client.get(self.url(), headers=self.auth())
        self.assertLess(r.status_code, 500, r.text)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertFalse(body["available"])
        self.assertEqual(body["items"], {})

    def test_endpoint_does_not_create_or_write_review_databases(self):
        """只读契约（复审 issue 5）：成员首次 GET 不得新建成员筛选员库。

        复审实测：原实现经 ``_screener_db`` 取数会 ``init_db`` 建出
        ``{member}.db``（36 张表）。改走工作流 runtime 的只读目录后必须零写入。
        """
        from coscreen import tasks as tasks_mod
        member = auth_mod.create_user("w4member-write@example.test", "W4-Study-2026")
        member_token = auth_mod.create_access_token(member["id"], member["username"])
        r = self.client.post(f"/api/tasks/{self.task_id}/collaboration/invite",
                             json={"username": member["username"], "role": "coder"},
                             headers=self.auth())
        self.assertEqual(r.status_code, 201, r.text)
        info = tasks_mod.find_task(self.task_id, str(main_mod.DATA_DIR))
        member_db = info.dir / (tasks_mod.slugify_screener(member["username"]) + ".db")
        self.assertFalse(member_db.exists(), "前置：成员库尚不存在")
        resp = self.client.get(self.url(),
                               headers={"Authorization": "Bearer " + member_token})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertFalse(member_db.exists(),
                         "只读端点不得新建成员筛选员库（复审 issue 5）")
        # 目录语义：成员库未同步时仍返回属主目录全量（而非误导性的 count=0）
        body = resp.json()
        self.assertTrue(body["available"])
        self.assertEqual(body["count"], 5)
        self.assertEqual(set(body["items"]), {"A1", "A2", "A3", "A4", "A5"})


class WiringStaticGuardNails(unittest.TestCase):
    """接线静态守卫：index.html 锚点、前端红线、后端纯本地、CI 挂载。"""

    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")
        cls.js = FRONTEND.read_text(encoding="utf-8")
        cls.main_py = MAIN_PY.read_text(encoding="utf-8")
        cls.api_py = API_PY.read_text(encoding="utf-8")
        cls.doc = DOC.read_text(encoding="utf-8")

    def test_index_badge_anchor_sits_between_al_score_and_abstract(self):
        badge_call = "window.StudyDesignBadge ? StudyDesignBadge.badge(r) : null"
        self.assertEqual(self.html.count(badge_call), 1)
        render = self.html[self.html.index("async function renderScreen()"):self.html.index("let screenRequestVersion")]
        al_score = render.index("class: 'al-score'")
        badge = render.index(badge_call)
        abstract = render.index("class: 'absbox'")
        self.assertLess(al_score, badge, "徽标必须插在 AL 分数条之后（旁）")
        self.assertLess(badge, abstract, "徽标必须在摘要块之前")

    def test_index_prefill_anchor_before_mark_decision_clean(self):
        prefill_call = "window.StudyDesignBadge?.prefill(reason, r);"
        self.assertEqual(self.html.count(prefill_call), 1)
        render = self.html[self.html.index("async function renderScreen()"):self.html.index("let screenRequestVersion")]
        restore = render.index("WorkspaceUndo?.restoreReason")
        prefill = render.index(prefill_call)
        clean = render.index("WorkflowControls.markDecisionClean('screen');", prefill)
        self.assertLess(restore, prefill, "预填必须在已存理由恢复之后（不覆盖已存值）")
        self.assertLess(prefill, clean, "预填必须在 markDecisionClean 之前（预填值进入干净基线）")

    def test_index_script_token_and_w5_tail_untouched(self):
        tag = f'<script src="./{BADGE_JS}?v={TOKEN}"></script>'
        self.assertEqual(self.html.count(tag), 1, "script 令牌必须恰一次")
        sw = self.html.index("navigator.serviceWorker.register")
        self.assertLess(self.html.index(tag), sw, "W5 的 SW 注册行必须保持在文档最末")

    def test_index_exclusion_option_value_exists(self):
        self.assertIn("'研究设计不符'", self.html, "预填目标选项值必须存在于初筛排除理由下拉")

    def test_frontend_module_red_lines(self):
        self.assertRegex(self.js, r"\(function \(\) \{", "必须 IIFE 自包含（文件头注释之后整体包裹）")
        self.assertIn("'use strict'", self.js)
        self.assertIn("RFLang.register", self.js)
        self.assertIn("window.StudyDesignBadge", self.js)
        # 红线查代码不查注释：先剥 /* */ 与 // 注释再断言
        code_only = re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", self.js, flags=re.S))
        for forbidden in ("innerHTML", "insertAdjacentHTML", "document.write",
                          "eval(", "new Function", "fetch(", "XMLHttpRequest",
                          "WebSocket", "http://", "https://", "ws://"):
            self.assertNotIn(forbidden, code_only, f"前端模块红线：代码中不得出现 {forbidden}")

    def test_backend_modules_stay_pure_local(self):
        for source in (REPO / "coscreen" / "al" / "study_design.py").read_text(encoding="utf-8"), self.api_py:
            for forbidden in ("urllib", "requests", "httpx", "socket", "sklearn", "numpy"):
                self.assertNotIn(forbidden, source, f"后端纯本地红线：不得出现 {forbidden}")
        # 规则必须复用 evidence_mapper（镜像实现即红）
        self.assertIn("from coscreen.evidence_mapper import OTHER_LABEL, _STUDY_DESIGN_RULES",
                      (REPO / "coscreen" / "al" / "study_design.py").read_text(encoding="utf-8"))

    def test_main_registers_the_route_once(self):
        call = "register_study_design_routes(app, require_user, _task_or_404, _task_visible_to)"
        self.assertEqual(self.main_py.count(call), 1)
        self.assertEqual(self.main_py.count("from custom_backend.study_design_api import"), 1)

    def test_release_yml_wires_node_behavior_suite(self):
        """MM11-01 同型：本组 node 行为套件必须挂在 release 门禁的
        node --test 接线里（撤线即红——不留文档型测试）。"""
        yml = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        step = yml[yml.index("Run static validators"):yml.index("actions/upload-artifact")]
        self.assertIn("node --test", step)
        self.assertIn("build/tests/study_design_badge.test.cjs", step)

    def test_doc_declares_freshness_and_biases(self):
        for needle in ("2026-10-30", "选择偏差", "小样本", "截断", "多设计文献",
                       "预填不代存", "available=false"):
            self.assertIn(needle, self.doc, f"诚实披露缺失：{needle}")


if __name__ == "__main__":
    unittest.main()
