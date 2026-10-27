"""复审 r6 新确认问题的回归钉（集成修复轮）。

覆盖：
- U1-P5 残余：normalize_doi 剥离 CJK 尾标点（。，、”）】）；
- U2 新发现：parse_csv GBK 解码回退与不可解码指引、仅表头 CSV 语义、
  CLI import 对 0 条记录的 stderr 警告（与 RIS ParseError 口径对称）；
- U6-P8：prisma_form._esc 转义 CR/LF，DOT 引号串不再裸断行（引号计数
  改为转义感知，r5 复审 #54）；
- PRISMA 契约（r5 复审 #51 修正）：/api/prisma/generate 对 ok=False 的
  表单仍返回降级 dot/json（前端仅在 ok 分支消费）；
- living_review：混合候选组（模糊 + 键冲突）标注为 mixed，键冲突来源
  不再被 fuzzy 标签吞掉。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen.normalize import normalize_doi
from coscreen.parsers.csv_parser import parse_csv
from coscreen.parsers.ris_parser import ParseError


class NormalizeDoiCjkTailPunctTests(unittest.TestCase):
    """U1-P5 残余：中文句读尾标点使同文 DOI 不相等、重复文献失去标记。"""

    def test_cjk_tail_punctuation_stripped(self):
        for ch in "。，、；：！？”》）】":
            with self.subTest(ch=ch):
                self.assertEqual(normalize_doi(f"10.1234/abc{ch}"), "10.1234/abc")

    def test_ascii_behavior_unchanged(self):
        # 既有钉住口径（test_dedup_u1_regressions）不回归：
        self.assertEqual(normalize_doi("10.1000/abc."), "10.1000/abc")
        self.assertEqual(normalize_doi("10.1000/(abc).def)"), "10.1000/(abc).def")
        self.assertEqual(normalize_doi("10.1000/abc]"), "10.1000/abc")

    def test_paired_cjk_brackets_kept(self):
        # 全角括号先被 NFKC 折叠为 ASCII；成对括号是 DOI 合法内容，不应误剥。
        self.assertEqual(normalize_doi("10.1000/（abc）"), "10.1000/(abc)")


class CsvEncodingTests(unittest.TestCase):
    """U2 新发现：GBK CSV 无法导入且报错不可操作（ris_parser 有回退、CSV 没有）。"""

    def test_gbk_csv_parses(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gbk.csv"
            content = "Title,Creator,DOI,Publication Year\r\n中文标题测试,张三,10.1000/gbk,2024\r\n"
            path.write_bytes(content.encode("gbk"))
            articles = parse_csv(path)
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].title, "中文标题测试")
        self.assertEqual(articles[0].authors, "张三")
        self.assertEqual(articles[0].doi, "10.1000/gbk")
        self.assertEqual(articles[0].year, 2024)

    def test_utf8_sig_csv_still_parses(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "utf8.csv"
            content = "Title,Creator,DOI\r\nUTF8 Title,Smith J,10.1000/u8\r\n"
            path.write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))
            articles = parse_csv(path)
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].title, "UTF8 Title")

    def test_undecodable_csv_gets_actionable_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.csv"
            # 既非 UTF-8 也非 GBK 的字节（ latin-1 高位序列对两者均非法）。
            path.write_bytes(b"Title,Creator\r\n\xff\xfe\x81\x40\x9d,\xff\r\n")
            with self.assertRaises(ParseError) as ctx:
                parse_csv(path)
        self.assertIn("另存为 UTF-8", str(ctx.exception))


class RisTerminatorTests(unittest.TestCase):
    """U2-P8 + 复审新发现：缺 ER 的记录被 rispy 静默丢弃/吞并后续记录。

    r2 评审 R3 口径：不整文件拒绝——完整记录照常导入，被丢弃/嵌合的
    记录逐条显式告警。
    """

    @staticmethod
    def _write(tmp: str, body: str) -> Path:
        path = Path(tmp) / "in.ris"
        path.write_text(body, encoding="utf-8")
        return path

    def test_mid_record_missing_er_warns_chimera(self):
        # 中部缺 ER：rispy 不丢弃记录（返回数与 ER 数一致）而是生成嵌合记录
        # （Beta 吸收 Gamma：作者污染、Gamma 标题静默消失）。r6 解析器泳道
        # 按「预校验与 rispy 文法逐字对齐、只对 rispy 实际丢弃记录的场景
        # 抛错」的方向改口径：不整文件拒绝，改为可定位的 stderr 告警。
        body = (
            "TY  - JOUR\nTI  - Alpha\nAU  - Alice\nDO  - 10.1/alpha\nER  -\n"
            "TY  - JOUR\nTI  - Beta\nAU  - Bob\nDO  - 10.1/beta\n"
            "TY  - JOUR\nTI  - Gamma\nAU  - Carol\nDO  - 10.1/gamma\nER  -\n"
        )
        from coscreen.parsers.ris_parser import parse_ris
        from contextlib import redirect_stderr
        from io import StringIO as _StringIO

        with tempfile.TemporaryDirectory() as tmp:
            err = _StringIO()
            with redirect_stderr(err):
                articles = parse_ris(self._write(tmp, body))
        # 集成轮复审（B3）落地后转正：嵌合点拆分，三条各自导入无字段污染
        self.assertEqual([a.title for a in articles], ["Alpha", "Beta", "Gamma"])
        self.assertIn("拆分", err.getvalue())

    def test_last_record_missing_er_imports_complete_with_warning(self):
        # r2 评审 R3「绝不整文件拒绝」：末条缺 ER 不再 ParseError（此前实测
        # CLI rc=2、0 行入库、Alpha 也被丢弃）——完整记录照常导入，被
        # rispy 丢弃的记录逐条显式告警
        from contextlib import redirect_stderr
        from io import StringIO as _StringIO

        from coscreen.parsers.ris_parser import parse_ris

        body = "TY  - JOUR\nTI  - Alpha\nER  -\nTY  - JOUR\nTI  - Beta\n"
        with tempfile.TemporaryDirectory() as tmp:
            err = _StringIO()
            with redirect_stderr(err):
                articles = parse_ris(self._write(tmp, body))
        self.assertEqual([a.title for a in articles], ["Alpha"])
        self.assertIn("第 2 条记录", err.getvalue())
        self.assertIn("未被导入", err.getvalue())

    def test_complete_records_still_parse(self):
        from coscreen.parsers.ris_parser import parse_ris

        body = (
            "TY  - JOUR\nTI  - Alpha\nAU  - Alice\nER  -\n"
            "TY  - JOUR\nTI  - Beta\nAU  - Bob\nER  -\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            articles = parse_ris(self._write(tmp, body))
        self.assertEqual([a.title for a in articles], ["Alpha", "Beta"])


class CsvAuthorAliasTests(unittest.TestCase):
    """U2-P1：真实 Zotero 表头用 Author 列，作者值被静默丢弃。"""

    def test_author_column_mapped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "zotero.csv"
            content = (
                "Key,Item Type,Author,Title,Publication Year,DOI\n"
                'ABC123,Journal Article,"Smith John;Doe Jane",Trial,2020,10.1/t\n'
            )
            path.write_text(content, encoding="utf-8")
            articles = parse_csv(path)
        self.assertEqual(len(articles), 1)
        self.assertIn("Smith John", articles[0].authors)
        self.assertIn("Doe Jane", articles[0].authors)


class HeaderOnlyCsvTests(unittest.TestCase):
    """U2 新发现：仅表头 CSV（截断导出）静默成功。"""

    def test_header_only_csv_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "header.csv"
            path.write_text("Title,Creator,DOI\r\n", encoding="utf-8")
            self.assertEqual(parse_csv(path), [])

    def test_cli_import_warns_on_zero_records(self):
        import coscreen.cli as cli

        original = cli.parse_file
        cli.parse_file = lambda _path: []
        try:
            with tempfile.TemporaryDirectory() as tmp:
                db_path = str(Path(tmp) / "warn.db")
                args = Namespace(
                    file=str(Path(tmp) / "header.csv"),
                    db=db_path,
                    screener=None,
                    task=None,
                    auto_merge_fuzzy=False,
                )
                import io
                from contextlib import redirect_stderr, redirect_stdout

                err_buf, out_buf = io.StringIO(), io.StringIO()
                with redirect_stderr(err_buf), redirect_stdout(out_buf):
                    code = cli.cmd_import(args)
        finally:
            cli.parse_file = original
        self.assertEqual(code, 0)
        self.assertIn("未解析到任何数据记录", err_buf.getvalue())
        self.assertIn("截断", err_buf.getvalue())


class PrismaEscTests(unittest.TestCase):
    """U6-P8：_esc 只转义反斜杠与双引号，裸 CR/LF 在 DOT 引号串内断行。"""

    def test_esc_escapes_crlf(self):
        from coscreen.stats.prisma_form import _esc

        self.assertEqual(_esc("a\nb"), "a\\nb")
        self.assertEqual(_esc("a\r\nb"), "a\\nb")
        self.assertEqual(_esc("a\rb"), "a\\nb")
        self.assertEqual(_esc('say "hi"\\x'), 'say \\"hi\\"\\\\x')

    @staticmethod
    def _count_unescaped_quotes(line: str) -> int:
        # r5 复审 #54：naive line.count('"') 把合法转义 \" 也计入——合法表单
        # 标签含奇数个双引号（如来源名 'He said "hi'）时旧断言会误报 FAIL。
        # 改为跳过反斜杠转义后再计数。
        count = 0
        escaped = False
        for ch in line:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                count += 1
        return count

    def test_prisma_form_dot_no_bare_newline_in_labels(self):
        from coscreen.stats.prisma_form import (
            IdentifiedRow,
            PrismaFormData,
            prisma_form_dot,
        )

        form = PrismaFormData(
            review_mode="updated",
            identified_rows_db=[IdentifiedRow("database", "Multi\nLine 来源", 10)],
            removed_other_note="说明一行\n说明二行",
        )
        dot = prisma_form_dot(form, merge_note="合并说明一\n合并说明二")
        for line in dot.splitlines():
            # 引号串内的裸换行已被转义：每行「未转义引号」数应为偶数。
            self.assertEqual(
                self._count_unescaped_quotes(line) % 2, 0, f"引号未闭合的行: {line!r}"
            )

    def test_escaped_quote_in_label_keeps_lines_closed(self):
        # r5 复审 #54 回放：来源名含单个双引号（DOT 内转义为 \"），整行合法；
        # naive 计数为奇会把这条合法表单误判为引号未闭合。
        from coscreen.stats.prisma_form import (
            IdentifiedRow,
            PrismaFormData,
            prisma_form_dot,
        )

        form = PrismaFormData(
            identified_rows_db=[IdentifiedRow("database", 'He said "hi', 10)],
        )
        dot = prisma_form_dot(form)
        naive = [line.count('"') for line in dot.splitlines()]
        self.assertTrue(any(c % 2 == 1 for c in naive))  # 奇数裸计数存在（复现前提）
        for line, unescaped in zip(dot.splitlines(), map(self._count_unescaped_quotes, dot.splitlines())):
            self.assertEqual(unescaped % 2, 0, f"引号未闭合的行: {line!r}")


class PrismaGenerateContractTests(unittest.TestCase):
    """PRISMA API 契约（r5 复审 #51 修正）：ok=False 时仍返回降级导出物。

    fa12f16 曾改为 ok=False 一律 dot/json=None；叠加上游校验 error（如
    R13 对官方模板「仅报总数」的误判）后，合规表单在 API 层也拿不到导出
    物且无降级路径。现改为恒返回 dot/json：前端 prRenderResult 仅在 d.ok
    分支渲染与启用下载（行为不变），直连 API 客户端可自行取舍。
    直调端点函数（不启动 TestClient、不改全局 DATA_DIR），避免与套件中
    其他依赖环境变量的集成测试产生目录生命周期干扰。
    """

    def test_invalid_form_blocks_exports(self):
        from custom_backend.main import PrismaFormIn, PrismaGenerateIn, prisma_generate

        # 集成轮复审（R6-F8 契约恢复）：空表单缺识别来源行 → R1 error →
        # ok=False 时 dot/json 一律 None（error 阻止导出；r6 曾反转成
        # 「降级导出仍返回」——r5 #51 的官方模板动机已由 R13W 闭环）。
        payload = prisma_generate(
            PrismaGenerateIn(form=PrismaFormIn()),
            user={"user_id": "probe", "username": "probe"},
        )
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["errors"])
        self.assertIsNone(payload["dot"])
        self.assertIsNone(payload["json"])
        self.assertIsNotNone(payload["form"])

    def test_official_template_form_gets_ok_and_exports(self):
        # r5 复审 #51 同法：官方模板式表单（题摘排除仅报总数）修复前
        # ok=False、唯一 error R13、dot=None、json=None；现 ok=True 直接
        # 拿到导出物（R13 降级为 warning，U6-P9）。
        from custom_backend.main import PrismaFormIn, PrismaGenerateIn, prisma_generate

        payload = prisma_generate(
            PrismaGenerateIn(form=PrismaFormIn(
                identified_rows_db=[
                    {"source_type": "database", "name": "Medline", "n": 120},
                    {"source_type": "database", "name": "Embase", "n": 30},
                ],
                duplicates_removed=20,
                removed_other=10,
                records_screened=120,
                records_excluded_screening=30,
                reports_sought_db=90,
                reports_not_retrieved_db=10,
                reports_assessed_db=80,
                exclusion_reasons_db=[{"reason": "design", "n": 30}],
                studies_included=40,
                reports_of_included_studies=50,
            )),
            user={"user_id": "probe", "username": "probe"},
        )
        self.assertTrue(payload["ok"], payload["errors"])
        self.assertIn("digraph PRISMA2020", payload["dot"])
        self.assertIsNotNone(payload["json"])


class CandidateReviewGroupLabelTests(unittest.TestCase):
    """living_review 复审：混合候选组（模糊 + 键冲突）被整体标 fuzzy，键冲突来源不可见。"""

    @staticmethod
    def _run(pairs):
        from coscreen.living_review import _candidate_review_groups
        from coscreen.models import Article

        keys = sorted({k for p in pairs for k in (p.kept_key, p.dup_key)})
        articles = [Article(zotero_key=k, title=f"Title {k}") for k in keys]
        return _candidate_review_groups(pairs, articles, set(keys))

    def test_mixed_group_labeled_mixed(self):
        from coscreen.dedup import DuplicatePair

        pairs = [
            DuplicatePair("A", "B", "fuzzy"),
            DuplicatePair("B", "C", "exact_key_conflict"),
        ]
        groups = self._run(pairs)
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertTrue(group["normalized_title"].startswith("mixed:"))
        kinds = {m["match_kind"] for m in group["members"]}
        self.assertEqual(kinds, {"fuzzy", "key_conflict"})

    def test_pure_fuzzy_group_labeled_fuzzy(self):
        from coscreen.dedup import DuplicatePair

        groups = self._run([DuplicatePair("A", "B", "fuzzy")])
        self.assertEqual(len(groups), 1)
        self.assertTrue(groups[0]["normalized_title"].startswith("fuzzy:"))
        self.assertEqual(
            {m["match_kind"] for m in groups[0]["members"]}, {"fuzzy"}
        )

    def test_pure_conflict_group_labeled_key_conflict(self):
        from coscreen.dedup import DuplicatePair

        groups = self._run([DuplicatePair("A", "B", "exact_key_conflict")])
        self.assertEqual(len(groups), 1)
        self.assertTrue(groups[0]["normalized_title"].startswith("key-conflict:"))
        self.assertEqual(
            {m["match_kind"] for m in groups[0]["members"]}, {"key_conflict"}
        )


if __name__ == "__main__":
    unittest.main()
