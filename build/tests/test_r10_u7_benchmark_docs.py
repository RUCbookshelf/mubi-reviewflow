# -*- coding: utf-8 -*-
"""r10-U7 / A7：统计方法验证材料入库（docs/benchmark/）的文档钉子（破坏即红）。

任务（A7 落地）：把 ``out/benchmark/`` 下的统计验证材料（验证报告 v1、A1–A5
交付文档、fixtures 复现包）整理入 ``docs/benchmark/``，与 ``docs/methodology/``
的召回证书并列；README 加「统计方法验证」小节链接；报告头部加版本锚点与复现
说明；原始语料按体积门槛（>50 MB 不入库，实为 31.9 MB——按「只入文档＋说明、
不入库原始数据」口径处理）只留清单与 sha256。

三条钉面（对应任务书「钉子=文档存在性＋README 命中＋fixtures 清单一致性」）：

1. **文档存在性**：``docs/benchmark/`` 的报告／A1／A2／A4／A5／README 与
   ``fixtures/`` 全量文件在位；``conftest.py`` 收集守卫在位（仓库根裸跑 pytest
   不会把研究态夹具当测试跑并在仓内落临时 sqlite）。
2. **README 命中**：十个 ``## <语言>`` 节**每一节**都要有**可见的** markdown
   链接指向 ``docs/benchmark/README.md``（HTML 注释内不算）；中文节标题必须是
   「统计方法验证」；链接目标在磁盘上真实存在（MM5-15/16 同款：README 不得引用
   不存在的文件）。README 的统计口径（204 / 159 / 11 / 31 / 3）与结果 JSON 一致。
3. **fixtures 清单一致性**：``docs/benchmark/README.md`` 的
   ``fixtures-manifest-v1`` 清单块逐文件 sha256 核对——文件集合与内容任一变动
   而清单未同步即红；11 个结果 JSON 的 ``counts`` 汇总必须等于 204 项检查
   （159 PASS / 11 FAIL / 31 NOT-RUN / 3 INFO），与报告 §3 的结论行一致。

另两条如实性钉子：
4. **报告正文未被改写**：报告头部新增的「入库」段用
   ``A7-benchmark-ingest`` 标记包裹，标记外正文（§1–§9）的 sha256 被钉死——
   谁改了正文（例如把 11 项 FAIL 改成 0 项、或删掉参考侧差异披露）即红。
5. **原始语料不入库**：``docs/benchmark/`` 下不得出现 ``datasets/`` 目录或任何
   CSV 原始语料；说明里必须给出 20 个文件的清单（每个 64 位 sha256）与总字节数。
"""
from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BENCH = REPO / "docs" / "benchmark"
FIXTURES = BENCH / "fixtures"
RESULTS = FIXTURES / "results"
README = REPO / "README.md"
DOC_README = BENCH / "README.md"
REPORT = BENCH / "验证报告_v1.md"

#: 入库的交付文档（除 fixtures 外的四份 A 系列 ＋ 报告 ＋ 本目录说明）。
DOC_FILES = [
    "验证报告_v1.md",
    "A1_datasets.md",
    "A2_inventory.md",
    "A4_comparison.md",
    "A5_attribution.md",
    "README.md",
    "conftest.py",
]

#: 十个语言节标题（## 级）——每一节都必须命中入库材料链接（W7 同款做法）。
TEN_LANGUAGES = [
    "中文", "English", "Español", "日本語", "Português",
    "Deutsch", "Српски", "한국어", "Русский", "Français",
]

#: 报告正文（§1–§9，去掉 A7 入库段后）的 sha256。LF 归一后计算：Windows 检出
#: 会把工作树写成 CRLF，按原始字节取摘要会让钉子随检出配置红绿（W7 已踩过）。
REPORT_BODY_SHA256 = (
    "a53acd3334a58b38d00ea622303fda280031594059d79e3354aaad832d291139"
)

#: 入库基线与被测内核提交（报告头部必须都出现，且必须区分开）。
BASELINE = "7535a44"
KERNEL_COMMIT = "eb11741"

#: 原始语料清单钉：20 个文件、总字节数。
CORPUS_FILES = 20
CORPUS_BYTES = 31895238

BEGIN = "<!-- A7-benchmark-ingest:begin -->"
END = "<!-- A7-benchmark-ingest:end -->"

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def _lf(text: str) -> str:
    return text.replace("\r\n", "\n")


def _doc(text: str) -> str:
    return _lf((BENCH / text).read_text(encoding="utf-8"))


def _manifest_hashes() -> dict:
    """解析 docs/benchmark/README.md 的 fixtures-manifest-v1 清单块。"""
    body = _doc("README.md")
    start = body.find("fixtures-manifest-v1")
    assert start >= 0, "docs/benchmark/README.md 缺少 fixtures-manifest-v1 清单块"
    # 标记行之后到本块收尾围栏之间的就是清单本体（围栏前的内容）。
    block = body[start:].split("```", 1)[0]
    pairs = dict(re.findall(r'"([^"]+)":\s*"([0-9a-f]{64})"', block))
    assert pairs, "fixtures-manifest-v1 清单块解析为空"
    return pairs


def _language_sections() -> dict:
    sections, current = {}, None
    for line in _lf(README.read_text(encoding="utf-8")).split("\n"):
        match = re.match(r"^## (.+?)\s*$", line)
        if match:
            current = match.group(1)
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return {name: "\n".join(lines) for name, lines in sections.items()}


class BenchmarkDocsExist(unittest.TestCase):
    """1. 文档存在性（撤掉入库即红）。"""

    def test_all_delivery_documents_exist(self):
        self.assertTrue(BENCH.is_dir(), f"缺少入库目录 {BENCH}")
        for name in DOC_FILES:
            self.assertTrue((BENCH / name).is_file(), f"missing docs/benchmark/{name}")

    def test_report_is_the_v1_validation_report(self):
        text = _doc("验证报告_v1.md")
        self.assertIn("统计内核数值验证报告（v1 草稿）", text)
        for section in ("## 1. 验证方法", "## 3. 结果总览", "## 4. 参考侧差异说明",
                        "## 7. 局限", "## 8. 复现", "## 9. 结论"):
            self.assertIn(section, text, f"报告章节缺失: {section}")

    def test_fixture_package_complete(self):
        modules = sorted(p.name for p in FIXTURES.glob("*.py"))
        results = sorted(p.name for p in RESULTS.glob("*.json"))
        self.assertEqual(len(modules), 14, f"夹具模块数应为 14，实际 {modules}")
        self.assertIn("run_all.py", modules)
        self.assertIn("common.py", modules)
        self.assertEqual(len(results), 11, f"结果 JSON 应为 11 个，实际 {results}")
        self.assertIn("run_all.py", _doc("README.md"))

    def test_conftest_guard_and_collection_surface(self):
        """研究态夹具不得被 pytest 当测试收集（并写临时 sqlite）。

        实测：夹具文件名不匹配 pytest 默认 pattern，``pytest docs/benchmark
        --collect-only`` 在有／无 conftest 时都输出 "no tests collected"——真正
        让它不跑的是命名约定，不是 conftest。因此两层都钉：① conftest 显式忽略
        ``fixtures/``（将来有人加 ``test_*.py`` 时的护栏）；② ``docs/benchmark/``
        下不得存在任何默认 pattern 可收集的测试文件（一旦有人加了就红，而不是
        让研究脚本在仓库根裸跑 ``pytest`` 时执行）。
        """
        guard = BENCH / "conftest.py"
        self.assertTrue(guard.is_file(), "docs/benchmark/conftest.py 收集守卫缺失")
        self.assertIn('collect_ignore = ["fixtures"]',
                      guard.read_text(encoding="utf-8"),
                      "收集守卫必须忽略 fixtures/（研究态夹具随库分发）")
        collectable = sorted(
            p.relative_to(BENCH).as_posix()
            for p in BENCH.rglob("*.py")
            if p.name.startswith("test_") or p.name.endswith("_test.py")
        )
        self.assertFalse(
            collectable,
            f"docs/benchmark/ 下出现可被默认收集的测试文件（研究脚本不是产品测试）: {collectable}",
        )


class ReadmeHitsAllLanguages(unittest.TestCase):
    """2. README 命中（十语言逐节、可见链接、目标真实存在）。"""

    def test_chinese_section_has_validation_heading(self):
        text = _lf(README.read_text(encoding="utf-8"))
        self.assertIn("### 统计方法验证", text,
                      "中文节缺少「统计方法验证」小节（A7 要求与召回证书节并列）")
        # 小节必须在「召回证书与停止规则」之后、「快速开始」之前——与 W7 相邻定位。
        zh = _language_sections()["中文"]
        recall = zh.index("### 召回证书与停止规则")
        stats = zh.index("### 统计方法验证")
        quick = zh.index("### 快速开始")
        self.assertLess(recall, stats, "统计方法验证节必须排在召回证书节之后")
        self.assertLess(stats, quick, "统计方法验证节必须排在快速开始之前")

    def test_every_language_section_links_the_dossier(self):
        """每节必须含**可见的** markdown 链接（HTML 注释内链接不算）。"""
        sections = _language_sections()
        link = "](" + "docs/benchmark/README.md" + ")"
        for lang in TEN_LANGUAGES:
            self.assertIn(lang, sections, f"README 缺少语言节: ## {lang}")
            visible = [
                line for line in sections[lang].split("\n")
                if link in line and "<!--" not in line
            ]
            self.assertTrue(
                visible,
                f"语言节 [{lang}] 无可见的统计验证材料链接（十语言注册回退）",
            )

    def test_links_resolve_on_disk(self):
        """README 引用的入库材料必须真实存在（MM5-15/16 同款静态守护）。"""
        text = _lf(README.read_text(encoding="utf-8"))
        targets = set(re.findall(r"\]\((docs/benchmark/[^)\s#]+)", text))
        self.assertTrue(targets, "README 未引用任何 docs/benchmark/ 下的文件")
        for rel in targets:
            self.assertTrue((REPO / rel).is_file(), f"README 引用了不存在的文件: {rel}")

    def test_readme_numbers_match_result_json(self):
        """README 的统计口径不得与结果 JSON 漂移（159/11/31/3、204 项）。"""
        counts = {"PASS": 0, "FAIL": 0, "NOT-RUN": 0, "INFO": 0}
        total = 0
        for path in sorted(RESULTS.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            total += payload["n_checks"]
            for key, value in payload["counts"].items():
                counts[key] += value
        self.assertEqual(total, 204, "204 项检查的口径被改动")
        self.assertEqual(counts, {"PASS": 159, "FAIL": 11, "NOT-RUN": 31, "INFO": 3},
                         "结果 JSON 的汇总与报告结论（159/11/31/3）不一致")
        zh = _language_sections()["中文"]
        for needle in ("204 项检查", "159 项直接通过", "11 项差异", "31 项", "3 项"):
            self.assertIn(needle, zh, f"中文统计方法验证节缺少口径 {needle!r}")
        self.assertIn("10 classic datasets and 204 checks", _language_sections()["English"])


class FixtureManifestConsistency(unittest.TestCase):
    """3. fixtures 清单一致性（文件集合与内容双钉）。"""

    def test_manifest_matches_disk_exactly(self):
        manifest = _manifest_hashes()
        on_disk = {
            p.relative_to(BENCH).as_posix(): p
            for p in list(FIXTURES.glob("*.py")) + list(RESULTS.glob("*.json"))
        }
        missing = sorted(set(manifest) - set(on_disk))
        extra = sorted(set(on_disk) - set(manifest))
        self.assertFalse(missing, f"清单列了磁盘上没有的夹具: {missing}")
        self.assertFalse(extra, f"磁盘上有清单未登记的夹具（改了夹具未同步清单）: {extra}")
        for rel, digest in manifest.items():
            self.assertTrue(_SHA_RE.match(digest), f"{rel}: sha256 格式异常 {digest!r}")
            actual = hashlib.sha256(on_disk[rel].read_bytes()).hexdigest()
            self.assertEqual(actual, digest,
                             f"{rel} 内容与清单不符（夹具被改动而清单未同步）")

    def test_manifest_covers_every_dataset_of_the_report(self):
        """报告 §2 列的十套数据集，夹具里都要有对应模块。"""
        manifest = _manifest_hashes()
        for module in ("bcg", "normand1999", "dk2007", "linde2005", "raudenbush1985",
                       "rothman2008", "yusuf1985", "egger2001", "kappa_cohen",
                       "senn2013"):
            self.assertIn(f"fixtures/{module}.py", manifest)
            self.assertIn(f"fixtures/results/{module}.json", manifest)

    def test_results_are_timestamp_free(self):
        """结果可逐字节复现的前提：JSON 里不得有时间戳等浮动字段。"""
        for path in sorted(RESULTS.glob("*.json")):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(
                re.search(r"20\d\d-\d\d-\d\d[T ][\d:]{5}", text),
                f"{path.name} 含时间戳，重跑会逐字节变化（会打红上面的 sha256 清单）",
            )


class ReportHeaderAnchorsAndBodyFrozen(unittest.TestCase):
    """4. 报告头部版本锚点＋复现说明；标记外正文逐字未改。"""

    def test_header_block_present_with_version_anchors(self):
        text = _lf(REPORT.read_text(encoding="utf-8"))
        self.assertEqual(text.count(BEGIN), 1, "报告缺少唯一的入库段起始标记")
        self.assertEqual(text.count(END), 1, "报告缺少唯一的入库段结束标记")
        begin, end = text.index(BEGIN), text.index(END)
        self.assertLess(begin, end)
        block = text[begin:end]
        # 版本锚点：入库基线与被测内核提交都要写明，且必须点明两者不是同一提交。
        self.assertIn(BASELINE, block, "入库段缺少入库版本锚点 7535a44")
        self.assertIn(KERNEL_COMMIT, block, "入库段缺少被测内核提交 eb11741")
        self.assertIn("不是同一提交", block, "入库段必须写明基线与被测提交不同")
        self.assertIn(f"fix/2.1.4-r10-ux", block)
        # 复现说明：入口必须是 fixtures/run_all.py。
        self.assertIn("docs/benchmark/fixtures/run_all.py", block,
                      "入库段缺少复现入口 docs/benchmark/fixtures/run_all.py")
        # 与并列的方法学报告互链。
        self.assertIn("../methodology/recall_certificate.md", block,
                      "入库段必须指明与召回证书方法学报告并列")

    def test_body_outside_header_is_untouched(self):
        """标记外正文（§1–§9）的 sha256 钉死：报告内容只入库存档，不得改写。"""
        text = _lf(REPORT.read_text(encoding="utf-8"))
        lines = text.split("\n")
        begin, end = lines.index(BEGIN), lines.index(END)
        self.assertEqual(lines[begin - 1], "",
                         "入库段前必须恰好隔一个空行（去掉标记后可无痕还原）")
        body = "\n".join(lines[: begin - 1] + lines[end + 1:])
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        self.assertEqual(
            digest, REPORT_BODY_SHA256,
            "报告正文被改写（§1–§9 必须与入库前逐字一致；新增说明只能写进入库段）",
        )

    def test_contested_claims_keep_their_disclosure(self):
        """「对外引用竞品事实」与已知不一致处的存疑标注必须保留（不改写）。"""
        a1 = _doc("A1_datasets.md")
        self.assertIn("⚠", a1, "A1 的（⚠）不一致标注被删除")
        self.assertIn("92.22", a1)
        self.assertIn("0.6532", a1)
        a4 = _doc("A4_comparison.md")
        self.assertIn("212/900", a4, "A4 勘误 #1（κ 手算笔误）被删除")
        a2 = _doc("A2_inventory.md")
        self.assertIn("RevMan", a2, "A2 §0 的对标取向句被删除")
        # 入库段落不得把该取向句升级成竞品实测结论。
        for text, name in ((a2, "A2"), (a1, "A1"), (_doc("A5_attribution.md"), "A5")):
            self.assertNotIn("优于 RevMan", text, f"{name} 出现未经证实的竞品对比结论")


class RawCorpusIsNotIngested(unittest.TestCase):
    """5. 原始语料不入库（只留清单与 sha256）。"""

    def test_no_dataset_payload_under_docs_benchmark(self):
        self.assertFalse((BENCH / "datasets").exists(),
                         "docs/benchmark/datasets/ 不得入库（原始语料只留清单）")
        payloads = [p for p in BENCH.rglob("*")
                    if p.is_file() and p.suffix.lower() in {".csv", ".toml", ".db"}]
        self.assertFalse(payloads, f"docs/benchmark/ 出现原始语料载荷: {payloads}")

    def test_dossier_documents_the_excluded_corpus(self):
        """说明必须给出可复核的替代：20 个文件的清单＋总字节数＋来源 URL。"""
        text = _doc("README.md")
        self.assertIn(f"{CORPUS_FILES} 个文件", text,
                      "说明未给出被排除的原始语料文件数")
        self.assertIn(f"{CORPUS_BYTES:,}", text,
                      "说明未给出原始语料总字节数（可核查体积门槛）")
        rows = [ln for ln in text.split("\n") if ln.startswith("| `datasets/")]
        self.assertEqual(len(rows), CORPUS_FILES,
                         f"清单行数应为 {CORPUS_FILES}，实际 {len(rows)}")
        for row in rows:
            self.assertRegex(row, r"\|\s*\d+\s*\|", f"清单行缺字节数: {row[:60]}")
            self.assertTrue(any(_SHA_RE.match(tok) for tok in re.findall(r"`([^`]+)`", row)),
                            f"清单行缺完整 sha256: {row[:60]}")
        self.assertIn("dmice.ohsu.edu", text, "说明缺少 Cohen 2006 的原始出处 URL")
        self.assertIn("10.7910/DVN/WMGPGZ/HY6N2S", text, "说明缺少 Nagtegaal 2019 的 DOI")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
