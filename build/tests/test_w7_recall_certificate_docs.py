"""W7 召回证书方法学公开报告的文档+行为钉子（2.1.4 r8，第 2 轮加固，破坏即红）。

钉面（对应 W7b 验收与 r1/r2 评审意见）：
1. **文档存在性**：recall_certificate.md 九节结构 + 26 个研究原始数据文件与 4 个
   复现夹具逐文件存在；verification_results.csv 仍是 90 场（3 配置 × 6 数据集 ×
   5 种子）；stoprule 回归覆盖 streak 10/25/50。
2. **README 逐语言命中**：十个 `## <语言>` 节**每一节**都必须含**可见的**
   markdown 链接（HTML 注释内链接不算，r3 加固）；并正向断言"暂不显示具体
   数值 / does not yet show the numeric value"，交叉核对面板文案不含 confidence
   （r3：反向黑名单易被换说法绕过）。
3. **如实披露不回退**：F-04-01 数值表与产品内核实算逐位一致；Pocock 边界披露
   （序贯校正仅在理论层、产品判定=单次抽样零相关）；r2 新增钉——界面/API 不输出
   置信度数值的披露、停止规则注册表旧口径披露、抽样由前端代选/判读人工的准确
   流程描述、检查端点懒建筛员库的措辞、损坏 DB 500 的已知边界记录。
4. **复现命令钉**：报告 §8 必须用正确入口 `python -m coscreen simulate`
   （`coscreen.cli simulate` 直呼是静默 no-op），给出 PYTHONPATH 前置与"每次
   全新数据库"提示；D4 需经 `fixtures/run_d_check.py`（其研究树 sys.path 在
   分发布局下不成立，r3）。
5. **行为钉子**：
   a. §8(b3) 合成夹具端到端真实重放（产品 CLI import+simulate，与期望 JSON 逐位一致）；
   b. §8(b2) **公开 SYNERGY 语料现跑**（r3）：与 verification_results.csv 的
      legacy_nb/Nelson_2002/seed=1 行 N/K/n_to_95/wss95/r95/ttd_mean 六项逐位一致，
      且语料 sha256 对得上 summary.csv；
   c. 检查端点真实 TestClient：未登录 401、缺参 422、未知任务 404、空语料 200、
      **到达 look 点主分支**（205 条/120 已筛 → k_t=1、m=26、只读不写决策，r3），
      响应均无 confidence 字段；stop-rules 注册表形状；
   d. §8(c) D1–D4 运行器 ALL PASS（r3）。
6. **交付完整性（r8 新增）**：`docs/methodology/data/` 三个公开语料必须可入库。仓库
   `.gitignore` 的 `data/` 规则曾把整个目录吞掉（`git check-ignore` 实测命中），干净
   检出后本文件的存在性钉子必红、CI 必红。钉子分两层：解析 `.gitignore` 的静态判定
    （不依赖 git 二进制，git 不可用的环境同样生效），以及 `git check-ignore` +
   `git add --dry-run` 实测交叉核对（有 git 时）。
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

REPO = Path(__file__).resolve().parents[2]
METH = REPO / "docs" / "methodology"
README = REPO / "README.md"
GITIGNORE = REPO / ".gitignore"
GIT = shutil.which("git")

#: 仿验证报告_v1 的九节标题（## N. 前缀）——删节/改名即红。
NINE_SECTIONS = [
    "## 1. 统计原理（超几何上界）",
    "## 2. 参数含义",
    "## 3. 使用指南",
    "## 4. 数据来源与分发清单",
    "## 5. 结果总览",
    "## 6. 边界与如实披露",
    "## 7. 局限",
    "## 8. 复现",
    "## 9. 结论",
]

#: W7a 分发的研究原始数据逐文件清单（research/ → docs/methodology/ 镜像，
#: 与源逐文件 sha256 核验）。r2 修复：含此前遗漏的 sim/README.md、test_replay.py
#: 与 sim/results/ 3 文件，以及新增的 notes/check_d1..d4 自检脚本。
RAW_FILES = [
    "notes/stopping_rules.md",
    "notes/check_d1_streak.py",
    "notes/check_d2_subsample.py",
    "notes/check_d3_estimators.py",
    "notes/check_d4_combined.py",
    "results/al_upgrade_verification/REPORT.md",
    "results/al_upgrade_verification/run_verification.py",
    "results/al_upgrade_verification/verification_results.csv",
    "results/al_upgrade_verification/verification_summary.csv",
    "results/al_upgrade_verification/stoprule_runs.csv",
    "results/al_upgrade_verification/stoprule_summary.csv",
    "results/al_upgrade_verification/verification_meta.json",
    "sim/README.md",
    "sim/bilingual.py",
    "sim/conftest.py",
    "sim/dual_oracle.py",
    "sim/equivalence_check.py",
    "sim/replay.py",
    "sim/stopping_rules.py",
    "sim/test_bilingual.py",
    "sim/test_dual_oracle.py",
    "sim/test_replay.py",
    "sim/test_stopping_rules.py",
    "sim/results/equivalence_results.json",
    "sim/results/example-trajectory-mixed-s42.summary.json",
    "sim/results/example-trajectory-mixed-s42.trajectory.jsonl",
]

#: 公开语料（SYNERGY v1，sha256 与 summary.csv 记录一致）。
PUBLIC_DATA_FILES = [
    "data/synergy/Nelson_2002.csv",
    "data/synergy/summary.csv",
    "data/provenance.md",
]

#: §8 复现夹具与运行器（本仓生成并实跑验证）。
FIXTURE_FILES = [
    "fixtures/gen_simulate_fixture.py",
    "fixtures/corpus.csv",
    "fixtures/gold.csv",
    "fixtures/expected_simulate_mixed_s42.json",
    "fixtures/prepare_synergy_nelson.py",
    "fixtures/nelson_corpus.csv",
    "fixtures/nelson_gold.csv",
    "fixtures/expected_synergy_nelson_legacy_s1.json",
    "fixtures/run_d_check.py",
]

#: README 的十个语言节标题（## 级）。每节都必须命中报告链接。
TEN_LANGUAGES = [
    "中文", "English", "Español", "日本語", "Português",
    "Deutsch", "Српски", "한국어", "Русский", "Français",
]


def _report_text() -> str:
    return (METH / "recall_certificate.md").read_text(encoding="utf-8")


def _gitignore_rules() -> list[tuple[bool, str]]:
    """把仓库根 .gitignore 解析成 (是否否定, 去掉首尾斜杠的模式) 列表。

    只覆盖本仓实际用到的语法（注释、`!` 否定、尾部 `/` 目录标记）；不使用 git
    二进制，因此 git 不可用的环境里"语料可入库"的静态判定仍然生效。
    """
    rules: list[tuple[bool, str]] = []
    for raw in GITIGNORE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        pattern = (line[1:] if negated else line).strip().rstrip("/")
        if pattern:
            rules.append((negated, pattern))
    return rules


def _ignored_by_gitignore(rel_path: str, rules: list[tuple[bool, str]]) -> bool:
    """模拟 git 的"后匹配规则胜出"判定（含祖先目录被忽略的情形）。"""
    import fnmatch

    parts = rel_path.strip("/").split("/")
    ignored = False
    for negated, pattern in rules:
        anchored = pattern.startswith("/")
        pat = pattern.lstrip("/")
        candidates = ["/".join(parts[: i + 1]) for i in range(len(parts))]
        matched = any(
            fnmatch.fnmatchcase(cand, pat)
            or (not anchored and fnmatch.fnmatchcase(cand.rsplit("/", 1)[-1], pat))
            for cand in candidates
        )
        if matched:
            ignored = not negated
    return ignored


class RecallCertificateDocExists(unittest.TestCase):
    """文档与原始数据存在性 + 九节结构。"""

    def test_report_and_nine_sections_exist(self) -> None:
        path = METH / "recall_certificate.md"
        self.assertTrue(path.is_file(), f"missing {path}")
        text = path.read_text(encoding="utf-8")
        for header in NINE_SECTIONS:
            self.assertIn(header, text, f"recall_certificate.md 缺少章节: {header}")

    def test_raw_research_data_distributed(self) -> None:
        for rel in RAW_FILES + PUBLIC_DATA_FILES + FIXTURE_FILES:
            path = METH / rel
            self.assertTrue(path.is_file(), f"W7a/W7b 数据缺失: {path}")

    def test_verification_batch_is_90_runs(self) -> None:
        """主批 CSV 必须 90 数据行（3 配置 × 6 数据集 × 5 种子）。"""
        with (METH / "results/al_upgrade_verification/verification_results.csv").open(
            encoding="utf-8"
        ) as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 90, "verification_results.csv 不再是 90 场")
        self.assertEqual(
            len({(r["config"], r["dataset"]) for r in rows}),
            18,
            "应为 3 配置 × 6 数据集",
        )
        self.assertEqual(
            {r["seed"] for r in rows}, {"1", "2", "3", "4", "5"}, "种子应为 1..5"
        )
        self.assertTrue(
            all(float(r["r95"]) >= 0.95 for r in rows), "全部场次 r95 应 ≥0.95"
        )

    def test_stoprule_regression_covers_streak_10_25_50(self) -> None:
        with (METH / "results/al_upgrade_verification/stoprule_summary.csv").open(
            encoding="utf-8"
        ) as fh:
            streaks = {row["streak_n"] for row in csv.DictReader(fh)}
        self.assertLessEqual({"10", "25", "50"}, streaks)


class MethodologyCorpusIsCommittable(unittest.TestCase):
    """r8 交付完整性钉子：公开语料必须能被 git 带入，干净检出后本文件才不会红。

    回归场景（r8 评审实测）：`.gitignore:11` 的 `data/` 规则命中
    `docs/methodology/data/**`，`git add` 静默跳过三个语料 → 干净检出缺文件 →
    `test_raw_research_data_distributed` 必红 → CI 必红。
    """

    #: 必须是可入库的三份公开语料（与 PUBLIC_DATA_FILES 一致）。
    CORPUS = PUBLIC_DATA_FILES

    def test_gitignore_does_not_swallow_published_corpus(self) -> None:
        """静态判定：按 .gitignore 规则集逐行重放，语料不得被判为忽略。"""
        rules = _gitignore_rules()
        self.assertTrue(rules, ".gitignore 解析为空——规则文件可能被删或格式异常")
        for rel in self.CORPUS:
            repo_rel = f"docs/methodology/{rel}"
            self.assertFalse(
                _ignored_by_gitignore(repo_rel, rules),
                f"{repo_rel} 被 .gitignore 忽略——需要否定规则 !docs/methodology/data/；"
                "否则 git add 不会带上语料，干净检出后 W7 钉子必红",
            )
            self.assertTrue((REPO / repo_rel).is_file(), f"语料文件本体缺失: {repo_rel}")

    def test_data_rule_still_ignores_real_user_data(self) -> None:
        """否定规则不得反噬：真实的用户数据目录仍必须被忽略。"""
        rules = _gitignore_rules()
        for rel in ("data/review.sqlite", "custom_backend/data/x.json", "instance/app.cfg"):
            self.assertTrue(
                _ignored_by_gitignore(rel, rules),
                f"{rel} 不再被忽略——否定规则写宽了（写成 !data/ 之类会害用户数据入库）",
            )

    @unittest.skipIf(GIT is None, "git not found")
    def test_git_check_ignore_does_not_match_corpus(self) -> None:
        """实测交叉核对：git check-ignore 对三份语料必须**不命中**（退出码 1）。"""
        for rel in self.CORPUS:
            proc = subprocess.run(
                [GIT, "-C", str(REPO), "check-ignore", "-v",
                 f"docs/methodology/{rel}"],
                capture_output=True, text=True,
            )
            self.assertEqual(
                proc.returncode, 1,
                f"git check-ignore 命中 docs/methodology/{rel} —— git add 不会带上"
                f"该文件，干净检出后 W7 钉子必红。命中规则:\n{proc.stdout}",
            )

    @unittest.skipIf(GIT is None, "git not found")
    def test_git_add_dry_run_includes_corpus(self) -> None:
        """实测交叉核对：语料必须「git add 不会漏掉」——两条互斥路径都要绿。

        原始写法只认 `git add --dry-run` 的输出，但那命令**只列未跟踪/已改动**
        文件：语料一旦随提交入库且工作树无改动，输出就是空串，钉子必假红
        （本批合入实测：13be7c5 单独检出即红）。语料的可入库性有两种成立形态：
        ① 尚未跟踪 → `git add --dry-run` 会列出它；
        ② 已跟踪且无改动 → `git ls-files --error-unmatch` 命中。
        被 .gitignore 吞掉且未跟踪时两条都不成立，钉子照旧变红（保护力不减）。
        """
        proc = subprocess.run(
            [GIT, "-C", str(REPO), "add", "--dry-run", "--", "docs/methodology"],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, f"git add --dry-run 失败:\n{proc.stderr}")
        listed = proc.stdout
        for rel in self.CORPUS:
            repo_rel = f"docs/methodology/{rel}"
            if repo_rel in listed:
                continue  # ① 未跟踪/已改动，dry-run 确实会带上它
            tracked = subprocess.run(
                [GIT, "-C", str(REPO), "ls-files", "--error-unmatch", "--", repo_rel],
                capture_output=True, text=True,
            )
            self.assertEqual(
                tracked.returncode, 0,
                f"{repo_rel} 既不在 `git add --dry-run` 输出里，也未被 git 跟踪"
                f"——该语料会被静默漏掉。dry-run 输出:\n{listed}",
            )


class RecallCertificateReadmeHits(unittest.TestCase):
    """README 十语言逐节命中检查（解决"零宣传"；r2 修复：逐语言、非总数）。"""

    def _language_sections(self) -> dict[str, str]:
        text = README.read_text(encoding="utf-8")
        sections: dict[str, list[str]] = {}
        current = None
        for line in text.splitlines():
            match = re.match(r"^## (.+?)\s*$", line)
            if match:
                current = match.group(1)
                sections[current] = []
            elif current is not None:
                sections[current].append(line)
        return {name: "\n".join(lines) for name, lines in sections.items()}

    def test_every_language_section_links_the_report(self) -> None:
        """每节必须含**可见的** markdown 链接（排除 HTML 注释内链接）。"""
        sections = self._language_sections()
        link = "](" + "docs/methodology/recall_certificate.md" + ")"
        for lang in TEN_LANGUAGES:
            self.assertIn(lang, sections, f"README 缺少语言节: ## {lang}")
            body = sections[lang]
            visible = [
                line for line in body.splitlines()
                if link in line and "<!--" not in line
            ]
            self.assertTrue(
                visible,
                f"语言节 [{lang}] 无可见的报告 markdown 链接"
                "（注释内链接不算，十语言注册回退）",
            )

    def test_readme_keywords_and_section_headings(self) -> None:
        text = README.read_text(encoding="utf-8")
        for needle in ("召回证书", "停止规则", "Recall certificate", "stopping rules"):
            self.assertIn(needle, text, f"README 未命中关键词: {needle}")
        self.assertIn("### 召回证书与停止规则", text)
        self.assertIn("### Recall certificate and stopping rules", text)

    def test_readme_does_not_claim_ui_shows_confidence_number(self) -> None:
        """r3 加固：正向断言"不显示"限定词，并交叉核对产品事实。

        反向黑名单（r2）易被换一种说法绕过，故改为：
        1) 中文节必须同时出现"暂不显示具体数值"；英文节必须出现 "does not yet
           show the numeric value"；
        2) 交叉钉子：界面与端点确实不输出置信度——al_stop_panel.js 的通过文案
           不含 confidence 数值，检查端点响应键无 confidence（见行为钉子）。
        """
        text = README.read_text(encoding="utf-8")
        self.assertIn("暂不显示具体数值", text, "README 中文节缺少'不显示数值'限定")
        self.assertIn("does not yet show the numeric value", text,
                      "README 英文节缺少'不显示数值'限定")
        for good in ("并非固定 95%", "not a fixed 95%"):
            self.assertIn(good, text)
        # 交叉钉子：产品面板的通过文案确实不打印置信度数值（无 {confidence} 占位）。
        panel = (REPO / "custom_frontend" / "al_stop_panel.js").read_text(encoding="utf-8")
        verdict_lines = [
            ln for ln in panel.splitlines() if "证书通过" in ln
        ]
        self.assertTrue(verdict_lines, "未找到面板通过文案")
        for line in verdict_lines:
            self.assertNotIn("confidence", line.lower(),
                             "面板通过文案出现 confidence——README 需同步改口径")


class RecallCertificateDisclosureNails(unittest.TestCase):
    """如实披露与实现一致性（破坏即红；含 r2 新增披露钉）。"""

    def test_hypergeometric_bounds_match_product_kernel(self) -> None:
        """报告中 F-04-01 数值表必须与产品内核实算逐位一致。"""
        from coscreen.al.stop_certificate import RecallCertificate

        cert = RecallCertificate()
        bounds = [round(1 - cert.certified_confidence(100, k, 30), 4) for k in range(9)]
        text = _report_text()
        for value in bounds:
            self.assertIn(f"{value:.4f}".rstrip("0").rstrip(".") or "0", text)
        # k_t≥8（已发现 ≥152 条相关）置信度才 ≥95% 的诚实结论。
        self.assertEqual(cert.threshold(152), 8)
        self.assertIn("k_t≥8", text)

    def test_pocock_boundary_disclosure_present(self) -> None:
        """Pocock 边界披露不得回退：序贯校正仅在理论层，产品判定=单次抽样零相关。"""
        text = _report_text()
        self.assertIn("Pocock", text)
        self.assertIn("零相关", text)
        self.assertIn("未按 look 数切分 α", text)
        self.assertIn("alpha", text)  # 参数表含 α 的名义性说明

    def test_r2_presentation_layer_disclosures_present(self) -> None:
        """r2 修复钉：界面/API 不输出置信度数值、注册表旧口径、代选样本、懒建库。"""
        text = _report_text()
        # 界面与检查端点均不输出置信度数值（API 无 confidence 字段）。
        self.assertIn("均不输出置信度数值", text)
        # 停止规则注册表仍带旧"≥95% 置信度"口径的披露。
        self.assertIn("以 ≥95% 置信度保证总召回 ≥95%", text)
        self.assertIn("stop_registry.py", text)
        # 抽样由前端按钮代选、判读人工的准确流程描述。
        self.assertIn("开始抽验", text)
        self.assertIn("判读始终由人工完成", text)
        # 检查端点会懒建筛员空库（"不写任何数据"的过强措辞不得回潮）。
        self.assertIn("懒初始化", text)
        self.assertNotIn("不写任何数据", text)
        # 损坏 DB 返回 500 的已知边界记录（a080fde 既有、非 W7 引入）。
        self.assertIn("500", text)

    def test_reproduction_entry_points_correct(self) -> None:
        """r2 修复钉：§8(b) 用 python -m coscreen simulate；禁把 cli 直呼写成命令行。"""
        text = _report_text()
        self.assertIn("python -m coscreen simulate", text)
        self.assertIn("静默无操作", text)  # 对 cli 直呼 no-op 的警示必须在
        # 代码围栏里不得再出现以 cli 直呼开头的命令行（r1 的失效复现命令）。
        self.assertNotIn("\npython -m coscreen.cli simulate", text)
        self.assertNotIn("python -m coscreen.cli simulate --db", text)
        # §8(c) PYTHONPATH 前置说明。
        self.assertIn("PYTHONPATH", text)
        # §8(b) 夹具实测数值已记录（与 expected JSON 由行为钉子逐位互证）。
        self.assertIn("0.5207", text)

    def test_sim_collection_guard_in_place(self) -> None:
        """docs/methodology/conftest.py 收集守卫必须在位（防裸 pytest 误收研究测试）。"""
        guard = METH / "conftest.py"
        self.assertTrue(guard.is_file(), f"missing {guard}")
        self.assertIn('collect_ignore = ["sim"]', guard.read_text(encoding="utf-8"))


class SimulateFixtureBehaviorNail(unittest.TestCase):
    """§8(b) 夹具端到端行为钉子：真实产品 CLI 重放，输出与期望逐位一致。"""

    def test_fixture_replays_via_product_cli_bit_exact(self) -> None:
        fixtures = METH / "fixtures"
        with tempfile.TemporaryDirectory(prefix="rf_w7_sim_") as tmp:
            db = str(Path(tmp) / "fixture.sqlite")
            out = str(Path(tmp) / "result.json")
            imp = subprocess.run(
                [sys.executable, "-m", "coscreen", "import",
                 "--db", db, "--file", str(fixtures / "corpus.csv")],
                cwd=str(REPO), capture_output=True, encoding="utf-8", timeout=300,
                env=dict(os.environ, PYTHONIOENCODING="utf-8"),
            )
            self.assertEqual(
                imp.returncode, 0, f"import 失败:\n{imp.stdout}\n{imp.stderr}"
            )
            sim = subprocess.run(
                [sys.executable, "-m", "coscreen", "simulate",
                 "--db", db, "--gold", str(fixtures / "gold.csv"),
                 "--strategy", "mixed", "--seed", "42", "--out", out],
                cwd=str(REPO), capture_output=True, encoding="utf-8", timeout=600,
                env=dict(os.environ, PYTHONIOENCODING="utf-8"),
            )
            self.assertEqual(
                sim.returncode, 0, f"simulate 失败:\n{sim.stdout}\n{sim.stderr}"
            )
            actual = json.loads(Path(out).read_text(encoding="utf-8"))
            expected = json.loads(
                (fixtures / "expected_simulate_mixed_s42.json").read_text("utf-8")
            )
            self.assertEqual(
                actual, expected, "simulate 夹具输出与期望 JSON 不一致（数值回归）"
            )
            self.assertGreaterEqual(expected["target"]["r95"], 0.95)


class CheckEndpointBehaviorNail(unittest.TestCase):
    """检查端点真实 TestClient 行为钉子（优雅路径 + 响应形状与报告 §3 一致）。"""

    @classmethod
    def setUpClass(cls) -> None:
        # main.DATA_DIR 是 import 时冻结的模块常量（其他测试模块可能已设临时目录
        # 并在会话中清理）——本类 patch 到自己的新目录；auth 等按调用读环境变量。
        cls._tmp = tempfile.TemporaryDirectory(prefix="rf_w7_cert_")
        cls._saved_env = os.environ.get("COBOOKSHELF_DATA_DIR")
        os.environ["COBOOKSHELF_DATA_DIR"] = cls._tmp.name
        from custom_backend import main as main_mod
        from custom_backend import auth as auth_mod

        cls._patch = mock.patch.object(main_mod, "DATA_DIR", Path(cls._tmp.name))
        cls._patch.start()
        cls.client = __import__(
            "fastapi.testclient", fromlist=["TestClient"]
        ).TestClient(main_mod.app, raise_server_exceptions=False)
        cls.client.__enter__()
        user = auth_mod.create_user("w7cert@example.test", "W7-Cert-2026!")
        cls.auth = {"Authorization": "Bearer " +
                    auth_mod.create_access_token(user["id"], user["username"])}

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.__exit__(None, None, None)
        cls._patch.stop()
        cls._tmp.cleanup()
        if cls._saved_env is None:
            os.environ.pop("COBOOKSHELF_DATA_DIR", None)
        else:
            os.environ["COBOOKSHELF_DATA_DIR"] = cls._saved_env

    def test_unauthenticated_is_401(self) -> None:
        resp = self.client.get(
            "/api/tasks/w7-noauth/al/stop-certificate/check"
            "?comparison=&outcome=&timepoint="
        )
        self.assertEqual(resp.status_code, 401, resp.text)

    def test_missing_required_params_is_422(self) -> None:
        # 任务存在而必填查询参数缺失 → 422（未知任务时任务 404 先行，另测）。
        task_id = self.client.post(
            "/api/tasks", json={"name": "w7-noparams"}, headers=self.auth
        ).json()["task_id"]
        resp = self.client.get(
            f"/api/tasks/{task_id}/al/stop-certificate/check", headers=self.auth
        )
        self.assertEqual(resp.status_code, 422, resp.text)

    def test_unknown_task_is_404(self) -> None:
        resp = self.client.get(
            "/api/tasks/w7-does-not-exist/al/stop-certificate/check"
            "?comparison=&outcome=&timepoint=",
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 404, resp.text)

    def test_empty_corpus_is_200_without_confidence_field(self) -> None:
        task_id = self.client.post(
            "/api/tasks", json={"name": "w7-empty-corpus"}, headers=self.auth
        ).json()["task_id"]
        resp = self.client.get(
            f"/api/tasks/{task_id}/al/stop-certificate/check"
            "?comparison=&outcome=&timepoint=",
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["total_records"], 0)
        self.assertIn("语料为空", body["message"])
        # 报告 §3 披露：检查端点不输出置信度数值（无 confidence 字段）。
        self.assertNotIn("confidence", body)
        self.assertNotIn("confidence", body.get("certificate", {}))
        cert = body["certificate"]
        self.assertEqual(cert["recall_target"], 0.95)
        self.assertEqual(cert["sampling_fraction"], 0.30)
        self.assertEqual(cert["look_fractions"], [0.5, 0.7, 0.85, 0.95])

    def test_at_look_point_branch_is_read_only_and_correct(self) -> None:
        """r3 加固：到达检查点的主分支（蓝图"只读不代抽"核心行为）。

        导入 205 条语料、把 120 条写成已筛（30 include / 90 exclude）后，
        端点应给出 look 点位、抽验量与 k_t，且**不新增任何决策**（只读）。
        """
        import sqlite3

        from custom_backend import main as main_mod
        from custom_backend import auth as auth_mod

        sid = "w7look"
        user = auth_mod.create_user("w7look@example.test", "W7-Look-2026!")
        # screener 须为本人（端点对他人筛选员库 403——保持该保护不改）。
        screener = user["username"]
        headers = {"Authorization": "Bearer " +
                   auth_mod.create_access_token(user["id"], user["username"])}
        task_id = self.client.post(
            "/api/tasks", json={"name": "w7-look"}, headers=headers
        ).json()["task_id"]
        # 用与前端同口径的筛员库路径写入进度（只写测试用的已筛决定）。
        from coscreen import db as db_mod
        db_path = main_mod._screener_db(
            main_mod._task_or_404(task_id), screener
        )
        db_mod.init_db(db_path)
        from contextlib import closing

        keys = [f"LOOK{i:04d}" for i in range(205)]
        with closing(sqlite3.connect(db_path)) as conn, conn:
            conn.executemany(
                "INSERT INTO articles (zotero_key, title, abstract) VALUES (?, ?, '')",
                [(key, f"t{i}") for i, key in enumerate(keys)],
            )
        # 120 条已判读：前 30 include，接着 90 exclude。
        with closing(sqlite3.connect(db_path)) as conn, conn:
            conn.executemany(
                "INSERT INTO decisions (zotero_key, decision, screened_at, updated_at)"
                " VALUES (?, ?, '2026-10-30T00:00:00', '2026-10-30T00:00:00')",
                [(key, "include" if i < 30 else "exclude")
                 for i, key in enumerate(keys[:120])],
            )
        before = self._decision_count(db_path)
        resp = self.client.get(
            f"/api/tasks/{task_id}/al/stop-certificate/check"
            f"?comparison=&outcome=&timepoint=&screener={screener}",
            headers=headers,
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["total_records"], 205)
        self.assertEqual(body["screened_count"], 120)
        self.assertEqual(body["found_relevant"], 30)
        self.assertEqual(body["pool_size"], 85)
        self.assertTrue(body["at_look_point"])
        self.assertEqual(body["look_fraction"], 0.5)
        self.assertEqual(body["k_t"], 1)          # floor(30*0.05/0.95)
        self.assertEqual(body["required_sample_size"], 26)  # ceil(85*0.30)
        self.assertIn("并非固定 95%", body["message"])
        self.assertNotIn("confidence", body)      # 端点不输出置信度数值
        # 只读：端点不写筛选决策。
        self.assertEqual(self._decision_count(db_path), before,
                         "检查端点写入了决策——违反只读契约")

    @staticmethod
    def _decision_count(db_path) -> int:
        import sqlite3
        from contextlib import closing

        with closing(sqlite3.connect(db_path)) as conn:
            return conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]

    def test_stop_rules_registry_shape(self) -> None:
        task_id = self.client.post(
            "/api/tasks", json={"name": "w7-stop-rules"}, headers=self.auth
        ).json()["task_id"]
        resp = self.client.get(
            f"/api/tasks/{task_id}/al/stop-rules", headers=self.auth
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        rules = resp.json()["rules"]
        self.assertLessEqual({"manual", "streak", "certificate"}, set(rules))
        self.assertTrue(rules["streak"].get("deprecated"))
        self.assertTrue(rules["certificate"].get("recommended"))


class PublicCorpusReproductionNail(unittest.TestCase):
    """§8(b2)：公开 SYNERGY 语料现跑，必须与 90 场批次对应行逐位一致。"""

    def test_nelson_reproduces_verification_batch_row(self) -> None:
        fixtures = METH / "fixtures"
        with tempfile.TemporaryDirectory(prefix="rf_w7_nelson_") as tmp:
            db = str(Path(tmp) / "nelson.sqlite")   # 每次全新库（同键更新会污染）
            out = str(Path(tmp) / "nelson.json")
            for args in (
                [sys.executable, "-m", "coscreen", "import", "--db", db,
                 "--file", str(fixtures / "nelson_corpus.csv")],
                [sys.executable, "-m", "coscreen", "simulate", "--db", db,
                 "--gold", str(fixtures / "nelson_gold.csv"),
                 "--strategy", "max", "--seed", "1", "--out", out],
            ):
                proc = subprocess.run(
                    args, cwd=str(REPO), capture_output=True, encoding="utf-8", timeout=900,
                    env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                )
                self.assertEqual(
                    proc.returncode, 0, f"{args[3]} 失败:\n{proc.stdout}\n{proc.stderr}"
                )
            sim = json.loads(Path(out).read_text(encoding="utf-8"))["target"]
            with (METH / "results/al_upgrade_verification/verification_results.csv").open(
                encoding="utf-8"
            ) as fh:
                row = next(
                    r for r in csv.DictReader(fh)
                    if r["config"] == "legacy_nb"
                    and r["dataset"] == "Nelson_2002"
                    and r["seed"] == "1"
                )
            self.assertEqual(int(row["N"]), sim["n_records"])
            self.assertEqual(int(row["K"]), sim["n_relevant"])
            self.assertEqual(int(row["n_to_95"]), sim["n_to_95"])
            self.assertEqual(float(row["wss95"]), sim["wss95"])
            self.assertEqual(float(row["r95"]), sim["r95"])
            self.assertEqual(float(row["ttd_mean"]), sim["ttd_mean"])
            expected = json.loads(
                (fixtures / "expected_synergy_nelson_legacy_s1.json").read_text("utf-8")
            )
            self.assertEqual(expected, json.loads(Path(out).read_text(encoding="utf-8")))

    def test_public_corpus_is_the_documented_synergy_slice(self) -> None:
        """公开语料与 SYNERGY summary.csv 记录的 sha256 一致（来源可核）。

        比对的是**入库形态**（LF）的摘要：summary.csv 在 LF 检出上生成，而
        Windows + core.autocrlf=true 的检出把工作树写成 CRLF（本批合入实测：
        13be7c5 在 Windows 检出即红，同一提交在 LF 检出为绿）。直接对工作树
        原始字节取摘要，会让钉子随检出配置红绿——不是语料变了，是换行符被
        本地规范化了。git 的 blob 一律是 LF，故按 LF 归一后取摘要才与 summary
        的记录同口径；真正的内容改动（多/少一行、改一个字符）仍然变红。
        """
        import hashlib

        raw = (METH / "data/synergy/Nelson_2002.csv").read_bytes()
        canonical = raw.replace(b"\r\n", b"\n")
        digest = hashlib.sha256(canonical).hexdigest()
        with (METH / "data/synergy/summary.csv").open(encoding="utf-8") as fh:
            row = next(r for r in csv.DictReader(fh) if r["dataset"] == "Nelson_2002")
        self.assertEqual(digest, row["sha256"])
        self.assertEqual(int(row["n_records"]), 366)
        self.assertEqual(int(row["n_included"]), 80)


class DCheckRunnerNail(unittest.TestCase):
    """§8(c)：D1–D4 随库运行器必须 ALL PASS（D4 有研究树路径依赖）。"""

    def test_d4_passes_via_shipped_runner(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(METH / "fixtures" / "run_d_check.py"), "d4"],
            cwd=str(REPO), capture_output=True, encoding="utf-8", timeout=900,
            env=dict(os.environ, PYTHONIOENCODING="utf-8"),
        )
        self.assertEqual(proc.returncode, 0, f"{proc.stdout}\n{proc.stderr}")
        self.assertIn("RESULT: ALL PASS", proc.stdout)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
