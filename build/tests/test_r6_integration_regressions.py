"""复审 r6 新确认问题的回归钉（集成修复轮）。

覆盖：
- U1-P5 残余：normalize_doi 剥离 CJK 尾标点（。，、”）】）；
- U2 新发现：parse_csv GBK 解码回退与不可解码指引、仅表头 CSV 语义、
  CLI import 对 0 条记录的 stderr 警告（与 RIS ParseError 口径对称）；
- U6-P8：prisma_form._esc 转义 CR/LF，DOT 引号串不再裸断行；
- PRISMA 契约：/api/prisma/generate 对非法表单（ok=False）不产出 dot/json；
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
    """U2-P8 + 复审新发现：缺 ER 的记录被 rispy 静默丢弃/吞并后续记录。"""

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
        self.assertEqual([a.title for a in articles], ["Alpha", "Beta"])
        self.assertIn("嵌合", err.getvalue())

    def test_last_record_missing_er_rejected(self):
        from coscreen.parsers.ris_parser import parse_ris

        body = "TY  - JOUR\nTI  - Alpha\nER  -\nTY  - JOUR\nTI  - Beta\n"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ParseError) as ctx:
                parse_ris(self._write(tmp, body))
        self.assertIn("缺少结束标签 ER", str(ctx.exception))

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
            # 引号串内的裸换行已被转义：每行引号数应为偶数（复审同法扫描）。
            self.assertEqual(
                line.count('"') % 2, 0, f"引号未闭合的行: {line!r}"
            )


class PrismaGenerateContractTests(unittest.TestCase):
    """PRISMA 契约缺口：validate docstring 承诺 error 阻止导出，API 却仍返回 dot/json。

    直调端点函数（不启动 TestClient、不改全局 DATA_DIR），避免与套件中
    其他依赖环境变量的集成测试产生目录生命周期干扰。
    """

    def test_invalid_form_returns_no_export_artifacts(self):
        from custom_backend.main import PrismaFormIn, PrismaGenerateIn, prisma_generate

        # 空表单缺识别来源行 → R1 error → ok=False，不得产出 dot/json。
        payload = prisma_generate(
            PrismaGenerateIn(form=PrismaFormIn()),
            user={"user_id": "probe", "username": "probe"},
        )
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["errors"])
        self.assertIsNone(payload["dot"])
        self.assertIsNone(payload["json"])
        self.assertIsNotNone(payload["form"])


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
