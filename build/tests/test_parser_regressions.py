"""首审 U2 解析器条目 + r5/r6 复审新确认项的回归钉（R6-P1 全矩阵）。

覆盖 parse_csv/parse_ris → find_duplicates → 键消歧 → db.upsert_articles
全链路；每条用例按首审 core_audit_findings.md / r5 复审 ④ 节的复现手法
原样回放修复前的失败输入。修复归属其他泳道的条目按 handoff 要求钉住
现状并注明原因（skip/注释），不冒充已修复。

矩阵对应：
- U2-P1 Author 列（集成轮已修，此处保持钉住）；
- U2-P2/R6-P3 AN/ID 非法键与同文件键冲突（来源键降级 + 直调 upsert 消歧）；
- U2-P3 首行字段数校验；U2-P12 物理行号；
- U2-P5/R6-D5 _fallback_id 改记录内容稳定标识（重导幂等验收）；
- U2-P6/P9/P10 J2/JA/Y1/SO/DI/A1 标签映射补齐；
- U2-P8 + 复审 #12/#13/#17 TY/ER 预校验与 rispy 文法逐字对齐；
- U2-P11 + 复审 #11/#16 编码回退置信度与告警（cp1252 不再静默乱码）；
- R6-P2 用户保留列注入剔除；
- R1 重导幂等验收（byte 级与内容级；2.1.3 旧 auto 键公式归 R6-D2 泳道）。
"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen.parsers.csv_parser import parse_csv, ParseError
from coscreen.parsers.ris_parser import parse_ris

# 与 custom_backend/main.py 的 _KEY_RE 逐字一致：含 "/"、"\"、NUL 或超长
# 的键在 coding/fulltext 等端点不可达（U2-P2/R6-P3 的后端口径）
KEY_RE = re.compile(r"^[^/\\\x00]{1,300}$")


def _write(tmp: str, name: str, body) -> Path:
    path = Path(tmp) / name
    path.write_bytes(body if isinstance(body, bytes) else body.encode("utf-8"))
    return path


class CsvStandardMatrixTests(unittest.TestCase):
    """标准 Zotero CSV 矩阵：Creator/Author 表头、42 列宽表头、GBK、BOM、仅表头。"""

    def test_creator_header_full_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(
                tmp,
                "creator.csv",
                "Key,Item Type,Publication Year,Creator,Title,Publication Title,DOI,Abstract Note,Url\n"
                'ABC123,Journal Article,2020,"Smith John;Doe Jane",Trial on X,BMJ,10.1/t,Long abstract.,https://ex.com/1\n',
            )
            articles = parse_csv(path)
        self.assertEqual(len(articles), 1)
        a = articles[0]
        self.assertEqual(a.zotero_key, "ABC123")
        self.assertEqual(a.title, "Trial on X")
        self.assertIn("Smith John", a.authors)
        self.assertEqual(a.journal, "BMJ")
        self.assertEqual(a.year, 2020)
        self.assertEqual(a.doi, "10.1/t")
        self.assertEqual(a.url, "https://ex.com/1")

    def test_author_header_matrix(self):
        # U2-P1（集成轮已修）：真实 Zotero 导出列名是 Author，保持钉住。
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(
                tmp,
                "author.csv",
                "Key,Item Type,Author,Title,Publication Year,DOI\n"
                'ABC124,Journal Article,"Smith John;Doe Jane",Trial,2020,10.1/t\n',
            )
            articles = parse_csv(path)
        self.assertIn("Smith John", articles[0].authors)
        self.assertIn("Doe Jane", articles[0].authors)

    def test_wide_zotero_header_42_columns(self):
        # 首审 U2-P1 证据用的 42 列真实 Zotero 表头（节选核心列 + 长尾列）
        header = (
            "Key,Item Type,Publication Year,Author,Title,Publication Title,"
            "ISBN,ISSN,Date,DOI,Url,Abstract Note,Date Added,Date Modified,"
            "Access Date,Library Catalog,Call Number,Rights,Extra,Manual Tags,"
            "Automatic Tags,Editor,Series Title,Short Title,Volume,Issue,Pages,"
            "Num Pages,Place,Publisher,Language,Archive,Archive Location,"
            "Library Catalog,Call Number,Journal Abbreviation,DOI,Link,Date,"
            "Running Time,Type,Extra"
        )
        n_cols = len(header.split(","))
        self.assertEqual(n_cols, 42)
        values = ["K42"] + [""] * 3 + ["Wide Title"] + [""] * (n_cols - 5)
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "wide.csv", header + "\n" + ",".join(values) + "\n")
            articles = parse_csv(path)
        self.assertEqual(articles[0].zotero_key, "K42")
        self.assertEqual(articles[0].title, "Wide Title")

    def test_gbk_csv_parses_with_warning(self):
        # 真实 GBK 中文文件必须继续可导入（R2：不得因新增置信度而误拒），
        # 且编码回退带告警（R3：回退不得静默）。
        content = "Title,Creator,DOI,Publication Year\r\n中文标题测试,张三,10.1000/gbk,2024\r\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "gbk.csv", content.encode("gbk"))
            err = StringIO()
            with redirect_stderr(err):
                articles = parse_csv(path)
        self.assertEqual(articles[0].title, "中文标题测试")
        self.assertEqual(articles[0].authors, "张三")
        self.assertIn("GBK", " ".join(articles[0].raw.get("_parse_warnings", [])))
        self.assertIn("GBK", err.getvalue())

    def test_utf8_sig_bom_still_parses(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "bom.csv", b"\xef\xbb\xbfTitle,Creator,DOI\r\nUTF8 Title,Smith J,10.1000/u8\r\n")
            articles = parse_csv(path)
        self.assertEqual(articles[0].title, "UTF8 Title")

    def test_header_only_csv_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "header.csv", "Title,Creator,DOI\r\n")
            self.assertEqual(parse_csv(path), [])

    def test_fewer_fields_row_still_parses(self):
        # R2：字段数少于表头是 pandas 接受的合法形态（缺失列补空），
        # 预校验不得比 pandas 更严
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "short.csv", "Key,Title,DOI,Url\nK1,Short Title\n")
            articles = parse_csv(path)
        self.assertEqual(articles[0].zotero_key, "K1")
        self.assertEqual(articles[0].title, "Short Title")
        self.assertEqual(articles[0].doi, "")

    def test_quoted_comma_title_parses(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(
                tmp, "quoted.csv", 'Key,Title,DOI\nK1,"A study, of X",10.1/x\n'
            )
            articles = parse_csv(path)
        self.assertEqual(articles[0].title, "A study, of X")
        self.assertEqual(articles[0].doi, "10.1/x")


class CsvFieldCountTests(unittest.TestCase):
    """U2-P3：首条数据行字段多于表头曾被 pandas 静默左移错配。"""

    def test_first_row_extra_fields_rejected_with_line(self):
        # 首审复现原样回放：key='A study'、title='of X'、doi='10.1/x' 的静默错位
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "p3.csv", "Key,Title,DOI\nK1,A study, of X,10.1/x\n")
            with self.assertRaises(ParseError) as ctx:
                parse_csv(path)
        message = str(ctx.exception)
        self.assertIn("第 2 行", message)
        self.assertIn("4 个字段", message)
        self.assertIn("3 个", message)

    def test_later_row_extra_fields_rejected_with_line(self):
        # 第二行起 pandas 本就抛 ParserError；统一为带行号的可操作消息
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(
                tmp, "p3b.csv", "Key,Title,DOI\nK1,T1,10.1/a\nK2,T2,10.1/b,extra\n"
            )
            with self.assertRaises(ParseError) as ctx:
                parse_csv(path)
        self.assertIn("第 3 行", str(ctx.exception))

    def test_quoted_newline_field_count_uses_physical_line(self):
        # U2-P12：引号内换行时，错误行号必须按物理行计（原 idx+2 报 3，实际第 4 行）
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(
                tmp, "p12.csv", 'Key,Title,Abstract Note\nK1,Alpha,"line1\nline2"\nK2,OK,10.1/k\n'
            )
            articles = parse_csv(path)
        self.assertEqual([a.zotero_key for a in articles], ["K1", "K2"])
        # 无 Key/Title/DOI 的损坏行（含引号内换行的前置行）报物理行号
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(
                tmp, "p12b.csv", 'Key,Title,Abstract Note\nK1,Alpha,"line1\nline2"\n,,\n'
            )
            with self.assertRaises(ParseError) as ctx:
                parse_csv(path)
        self.assertIn("第 4 行", str(ctx.exception))


class CsvReservedColumnTests(unittest.TestCase):
    """R6-P2：用户 CSV 的 "_" 前缀保留列不得透传 raw（防键注入/重定向）。"""

    def test_reserved_columns_stripped_and_keys_preserved(self):
        # 复审 repro 原样回放：两行 Key K2/K3 + 同一 _fallback_id 曾把两行键坍缩
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(
                tmp,
                "inject.csv",
                "Key,Title,DOI,_generated_key,_fallback_id\n"
                "K2,Title Two,,1,same-id\n"
                "K3,Title Three,,1,same-id\n",
            )
            err = StringIO()
            with redirect_stderr(err):
                articles = parse_csv(path)
        self.assertEqual([a.zotero_key for a in articles], ["K2", "K3"])
        for article in articles:
            self.assertNotIn("_fallback_id", article.raw)
            self.assertNotIn("_generated_key", article.raw)
            self.assertNotIn("_parse_warnings", article.raw)  # 键正常，无降级告警
        self.assertIn("保留列", err.getvalue())
        self.assertIn("_fallback_id", err.getvalue())

    def test_fallback_key_injection_not_adopted(self):
        # 复审 repro：_fallback_key=VICTIM-KEY 曾可把行键重定向到库内任意键
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(
                tmp,
                "victim.csv",
                "Key,Title,DOI,_generated_key,_fallback_key\n"
                "K9,Victim Title,,1,VICTIM-KEY\n",
            )
            with redirect_stderr(StringIO()):
                articles = parse_csv(path)
        self.assertEqual(articles[0].zotero_key, "K9")
        self.assertNotIn("VICTIM-KEY", str(articles[0].raw))

    def test_stabilize_ignores_injected_reserved_columns(self):
        # 下游真实 _stabilize_generated_import_keys：注入值被剔除后不得坍缩键
        from custom_backend.main import _stabilize_generated_import_keys

        with tempfile.TemporaryDirectory() as tmp:
            path = _write(
                tmp,
                "inject2.csv",
                "Key,Title,DOI,_generated_key,_fallback_id\n"
                "K2,Title Two,,1,same-id\n"
                "K3,Title Three,,1,same-id\n",
            )
            with redirect_stderr(StringIO()):
                articles = parse_csv(path)
        _stabilize_generated_import_keys([], articles)
        self.assertEqual([a.zotero_key for a in articles], ["K2", "K3"])


class RisTerminatorGrammarTests(unittest.TestCase):
    """U2-P8 + 复审 #12/#13/#17：TY/ER 预校验与 rispy 文法逐字对齐。"""

    def test_last_record_single_space_er_rejected(self):
        # 复审 #13 原样回放：'ER -'（单空格）曾通过预校验后让 Beta 静默丢失
        body = "TY  - JOUR\nTI  - Alpha\nER  -\nTY  - JOUR\nTI  - Beta\nER -\n"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ParseError) as ctx:
                parse_ris(_write(tmp, "p8.ris", body))
        message = str(ctx.exception)
        self.assertIn("缺少结束标签 ER", message)
        self.assertIn("第 2 条记录", message)
        self.assertIn("第 4 行", message)

    def test_mid_record_missing_er_warns_chimera(self):
        # 中部缺 ER：rispy 不丢弃记录（返回数与 ER 数一致）而是生成嵌合记录
        # （Beta 吸收 Gamma：作者污染、Gamma 标题静默消失）。按 r6 方向
        # （只对「rispy 实际丢弃记录」的场景抛错），此形态不整文件拒绝，
        # 改为可定位的 stderr 告警（R3：能警告不改变行为）
        body = (
            "TY  - JOUR\nTI  - Alpha\nAU  - Alice\nDO  - 10.1/alpha\nER  -\n"
            "TY  - JOUR\nTI  - Beta\nAU  - Bob\nDO  - 10.1/beta\n"
            "TY  - JOUR\nTI  - Gamma\nAU  - Carol\nDO  - 10.1/gamma\nER  -\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            err = StringIO()
            with redirect_stderr(err):
                articles = parse_ris(_write(tmp, "mid.ris", body))
        self.assertEqual([a.title for a in articles], ["Alpha", "Beta"])
        self.assertIn("嵌合", err.getvalue())
        self.assertIn("第 6 行", err.getvalue())

    def test_er_positive_continuation_not_rejected(self):
        # 复审 #12 原样回放：合法续行 'ER-positive ...' 曾被计为 ER 整文件误拒
        body = (
            "TY  - JOUR\nTI  - Breast cancer study\nAB  - Abstract line.\n"
            "ER-positive tumors were treated with care.\nER  -\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            articles = parse_ris(_write(tmp, "erpos.ris", body))
        self.assertEqual(len(articles), 1)
        self.assertIn("ER-positive tumors were treated with care.", articles[0].abstract)

    def test_indented_ty_continuation_not_rejected(self):
        # 复审 #17 原样回放：缩进续行 '    TY - 2019 protocols ...' 曾被计为 TY
        body = (
            "TY  - JOUR\nTI  - Trial\nAB  - Methods text.\n"
            "    TY - 2019 protocols were used here\nER  -\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            articles = parse_ris(_write(tmp, "indty.ris", body))
        self.assertEqual(len(articles), 1)
        self.assertIn("TY - 2019 protocols were used here", articles[0].abstract)

    def test_trailing_ty_prefixed_junk_line_not_rejected(self):
        # rispy 对 ER 后以 "TY" 开头的纯文本行只会静默丢弃幻影记录，
        # 预校验不得比 rispy 更严（R2）：该文件应与 rispy 口径一致地解析
        body = (
            "TY  - JOUR\nTI  - Alpha\nER  -\n"
            "Type 2 diabetes patients were seen in the annex.\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            articles = parse_ris(_write(tmp, "junk.ris", body))
        self.assertEqual([a.title for a in articles], ["Alpha"])

    def test_fn_preamble_and_ef_footer_parse(self):
        # 矩阵项：FN 前言/EF 结尾的多记录文件
        body = (
            "FN  - Export File\nTY  - JOUR\nTI  - Alpha\nER  -\n"
            "TY  - JOUR\nTI  - Beta\nER  -\nEF  - done\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            articles = parse_ris(_write(tmp, "fn.ris", body))
        self.assertEqual([a.title for a in articles], ["Alpha", "Beta"])

    def test_complete_records_still_parse(self):
        body = (
            "TY  - JOUR\nTI  - Alpha\nAU  - Alice\nER  -\n"
            "TY  - JOUR\nTI  - Beta\nAU  - Bob\nER  -\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            articles = parse_ris(_write(tmp, "ok.ris", body))
        self.assertEqual([a.title for a in articles], ["Alpha", "Beta"])

    def test_crlf_file_parses(self):
        # CRLF 行尾：与 rispy 同按 \n 切分、\r 不影响标签判定
        body = "TY  - JOUR\r\nTI  - Alpha\r\nER  -\r\nTY  - JOUR\r\nTI  - Beta\r\nER  -\r\n"
        with tempfile.TemporaryDirectory() as tmp:
            articles = parse_ris(_write(tmp, "crlf.ris", body))
        self.assertEqual([a.title for a in articles], ["Alpha", "Beta"])


class RisTagMappingTests(unittest.TestCase):
    """U2-P6/P9/P10：J2/JA/Y1/SO/DI/A1 按 rispy 映射名补齐。"""

    @staticmethod
    def _one(tmp: str, body: str):
        return parse_ris(_write(tmp, "tag.ris", body))[0]

    def test_y1_year(self):
        with tempfile.TemporaryDirectory() as tmp:
            article = self._one(tmp, "TY  - JOUR\nTI  - T\nY1  - 1998\nER  -\n")
        self.assertEqual(article.year, 1998)

    def test_y1_partial_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            article = self._one(tmp, "TY  - JOUR\nTI  - T\nY1  - 2019/05\nER  -\n")
        self.assertEqual(article.year, 2019)

    def test_j2_ja_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            article = self._one(
                tmp, "TY  - JOUR\nTI  - T\nJ2  - J Abbrev2\nJA  - J AbbrevA\nER  -\n"
            )
        self.assertEqual(article.journal, "J Abbrev2")

    def test_wos_so_di_journal_and_doi(self):
        # SO/DI 不在 rispy 默认映射（落 unknown_tag），此前期刊与 DOI 双丢
        with tempfile.TemporaryDirectory() as tmp:
            article = self._one(
                tmp, "TY  - JOUR\nTI  - T\nSO  - WoS Journal\nDI  - 10.1000/xyz\nER  -\n"
            )
        self.assertEqual(article.journal, "WoS Journal")
        self.assertEqual(article.doi, "10.1000/xyz")

    def test_di_only_doi(self):
        with tempfile.TemporaryDirectory() as tmp:
            article = self._one(tmp, "TY  - JOUR\nTI  - Test\nDI  - 10.1000/xyz\nER  -\n")
        self.assertEqual(article.doi, "10.1000/xyz")

    def test_a1_first_authors(self):
        with tempfile.TemporaryDirectory() as tmp:
            article = self._one(tmp, "TY  - JOUR\nTI  - T\nA1  - Smith, John\nER  -\n")
        self.assertEqual(article.authors, "Smith, John")

    def test_au_preferred_over_a1(self):
        with tempfile.TemporaryDirectory() as tmp:
            article = self._one(
                tmp, "TY  - JOUR\nTI  - T\nAU  - Doe, Jane\nA1  - Smith, John\nER  -\n"
            )
        self.assertEqual(article.authors, "Doe, Jane")

    def test_existing_mappings_unchanged(self):
        # 既有 TI/T1/DO/PY/DA/JF/JO/T2/AU/AB 行为不回归（钉现状：
        # T1（primary_title）在候选序中先于 TI——同现时 primary_title 胜出）
        body = (
            "TY  - JOUR\nTI  - Wos Title\nT1  - Zotero Title\nAU  - Alice\n"
            "PY  - 2001\nDA  - 2002-03-04\nJF  - JF Name\nJO  - JO Name\n"
            "T2  - T2 Name\nDO  - 10.1/do\nAB  - Abs.\nER  -\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            article = self._one(tmp, body)
        self.assertEqual(article.title, "Zotero Title")
        self.assertEqual(article.authors, "Alice")
        self.assertEqual(article.year, 2001)
        self.assertEqual(article.journal, "JO Name")
        self.assertEqual(article.doi, "10.1/do")
        self.assertEqual(article.abstract, "Abs.")


class EncodingConfidenceTests(unittest.TestCase):
    """U2-P11 + 复审 #11/#16：cp1252 不再静默 GBK 乱码；无法判定时响亮报错。"""

    def test_ris_cp1252_curly_quote_decoded_correctly(self):
        # 首审 T4 原样回放：b'The author\x92s view' 曾 GBK 静默解为 'The author抯 view'
        data = b"TY  - JOUR\nTI  - The author\x92s view\nER  - \n"
        with tempfile.TemporaryDirectory() as tmp:
            err = StringIO()
            with redirect_stderr(err):
                article = parse_ris(_write(tmp, "cp1252.ris", data))[0]
        self.assertEqual(article.title, "The author’s view")
        warnings = " ".join(article.raw.get("_parse_warnings", []))
        self.assertIn("CP1252", warnings)
        self.assertIn("CP1252", err.getvalue())

    def test_csv_cp1252_umlaut_decoded_correctly(self):
        # 复审 #11 原样回放：'Müller study' 曾静默解为 'M黮ler study'
        data = b"Title,Creator,DOI\r\nM\xfcller study,Smith J,10.1/m\r\n"
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stderr(StringIO()):
                article = parse_csv(_write(tmp, "cp1252.csv", data))[0]
        self.assertEqual(article.title, "Müller study")
        self.assertIn("CP1252", " ".join(article.raw.get("_parse_warnings", [])))

    def test_ris_real_gbk_still_parses(self):
        # R2：真实 GBK 中文文件不得因置信度门槛被误拒
        body = "TY  - JOUR\nTI  - 中文标题测试\nAU  - 张三\nER  - \n"
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stderr(StringIO()):
                article = parse_ris(_write(tmp, "gbk.ris", body.encode("gbk")))[0]
        self.assertEqual(article.title, "中文标题测试")
        self.assertEqual(article.authors, "张三")

    def test_undecodable_raises_actionable_error(self):
        # 既非 UTF-8 也非 GBK/cp1252 可安全判定的字节：列出候选并给转存指引
        data = b"Title,Creator\r\n\xff\xfe\x81\x40\x9d,\xff\r\n"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ParseError) as ctx:
                parse_csv(_write(tmp, "bad.csv", data))
        self.assertIn("另存为 UTF-8", str(ctx.exception))

    def test_utf8_text_with_cjk_marker_chars_unaffected(self):
        # UTF-8 优先：正文含 GBK mojibake 标记字符的合法 UTF-8 文件不经回退链
        body = "TY  - JOUR\nTI  - 含抯黮等生僻字的正常UTF-8标题\nER  - \n"
        with tempfile.TemporaryDirectory() as tmp:
            article = parse_ris(_write(tmp, "utf8mark.ris", body))[0]
        self.assertEqual(article.title, "含抯黮等生僻字的正常UTF-8标题")
        self.assertNotIn("_parse_warnings", article.raw)


class SourceKeyDemotionTests(unittest.TestCase):
    """U2-P2/R6-P3：非法键与同文件键冲突降级为派生键，直调 upsert 不再覆盖。"""

    def test_same_an_different_titles_not_collapsed(self):
        # 首审 U2-P2 原样回放：同 'AN  - Box 3' 的 Women/Men 曾 exact_key 合并剩 1 行
        body = (
            "TY  - JOUR\nTI  - Women in science\nAU  - Womon, Wendy\nAN  - Box 3\nER  -\n"
            "TY  - JOUR\nTI  - Men in science\nAU  - Mandel, Mark\nAN  - Box 3\nER  -\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            err = StringIO()
            with redirect_stderr(err):
                articles = parse_ris(_write(tmp, "box3.ris", body))
        keys = [a.zotero_key for a in articles]
        self.assertEqual(keys[0], "Box 3")  # 首条保留原键（最小改动）
        self.assertNotEqual(keys[0], keys[1])
        self.assertTrue(keys[1].startswith("auto-"))
        self.assertEqual(articles[1].raw.get("_demoted_source_key"), "Box 3")
        self.assertIn("不是行级唯一标识", " ".join(articles[1].raw.get("_parse_warnings", [])))
        self.assertIn("Box 3", err.getvalue())

    def test_same_an_direct_upsert_keeps_two_rows(self):
        # "直调 upsert 也需消歧"：不经 find_duplicates/stabilize，两行都落库
        import coscreen.db as db_mod

        body = (
            "TY  - JOUR\nTI  - Women in science\nAU  - Womon, Wendy\nAN  - Box 3\nER  -\n"
            "TY  - JOUR\nTI  - Men in science\nAU  - Mandel, Mark\nAN  - Box 3\nER  -\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            articles = parse_ris(_write(tmp, "box3b.ris", body))
            db_path = str(Path(tmp) / "t.db")
            db_mod.init_db(db_path)
            with redirect_stderr(StringIO()):
                db_mod.upsert_articles(db_path, articles, [])
            rows = db_mod.list_articles(db_path, include_duplicates=True)
        self.assertEqual(len(rows), 2)
        self.assertEqual({r.title for r in rows}, {"Women in science", "Men in science"})

    def test_find_duplicates_no_exact_merge_for_same_an(self):
        from coscreen.dedup import find_duplicates

        body = (
            "TY  - JOUR\nTI  - Women in science\nAU  - Womon, Wendy\nAN  - Box 3\nER  -\n"
            "TY  - JOUR\nTI  - Men in science\nAU  - Mandel, Mark\nAN  - Box 3\nER  -\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stderr(StringIO()):
                articles = parse_ris(_write(tmp, "box3c.ris", body))
        kept, report = find_duplicates(articles)
        self.assertEqual(len(kept), 2)
        self.assertEqual(report.exact_pairs, [])

    def test_slash_key_demoted_to_backend_legal_key(self):
        # R6-P3：'AN  - Box 3/File 2' 曾解析出含斜杠键，后端 _KEY_RE 不可达
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stderr(StringIO()):
                article = parse_ris(
                    _write(
                        tmp,
                        "slash.ris",
                        "TY  - JOUR\nTI  - Slash Key Paper\nAN  - Box 3/File 2\nER  -\n",
                    )
                )[0]
        self.assertTrue(KEY_RE.match(article.zotero_key))
        self.assertEqual(article.raw.get("_demoted_source_key"), "Box 3/File 2")

    def test_overlong_key_demoted(self):
        long_key = "K" * 400
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stderr(StringIO()):
                article = parse_ris(
                    _write(
                        tmp,
                        "long.ris",
                        f"TY  - JOUR\nTI  - Long Key Paper\nAN  - {long_key}\nER  -\n",
                    )
                )[0]
        self.assertTrue(KEY_RE.match(article.zotero_key))

    def test_identical_same_key_rows_not_demoted(self):
        # 同键同内容（真实重复行）：保留同键，由既有去重/幂等路径自然合并
        body = (
            "TY  - JOUR\nTI  - Same Paper\nAN  - K1\nER  -\n"
            "TY  - JOUR\nTI  - Same Paper\nAN  - K1\nER  -\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stderr(StringIO()):
                articles = parse_ris(_write(tmp, "dupk.ris", body))
        self.assertEqual([a.zotero_key for a in articles], ["K1", "K1"])

    def test_legal_unique_key_untouched(self):
        # 正常唯一键（含纯数字 PMID 形态）不被降级——不引入"自由文本"启发式
        for key in ("WOS:000123456789", "12345678", "PMC1234567"):
            with tempfile.TemporaryDirectory() as tmp:
                article = parse_ris(
                    _write(
                        tmp,
                        f"k{key[-4:]}.ris",
                        f"TY  - JOUR\nTI  - Paper {key}\nAN  - {key}\nER  -\n",
                    )
                )[0]
            self.assertEqual(article.zotero_key, key)

    def test_csv_duplicate_key_demotion(self):
        with tempfile.TemporaryDirectory() as tmp:
            err = StringIO()
            with redirect_stderr(err):
                articles = parse_csv(
                    _write(
                        tmp,
                        "dupk.csv",
                        "Key,Title,DOI\nSHARED,Alpha Paper,\nSHARED,Beta Paper,\n",
                    )
                )
        self.assertEqual(articles[0].zotero_key, "SHARED")
        self.assertNotEqual(articles[1].zotero_key, "SHARED")
        self.assertIn("不是行级唯一标识", err.getvalue())


class FallbackIdStabilityTests(unittest.TestCase):
    """U2-P5/复审 #18：_fallback_id 改记录内容稳定标识，重导不再漂移。"""

    @staticmethod
    def _smith_file(tmp: str, name: str, extra: str = "") -> Path:
        # 同题同姓同年（John vs Jane Smith 2020）：基础键相同，须靠后缀消歧
        body = (
            "TY  - JOUR\nTI  - Same Topic\nAU  - Smith, John\nPY  - 2020\nER  -\n"
            "TY  - JOUR\nTI  - Same Topic\nAU  - Smith, Jane\nPY  - 2020\nER  -\n"
        ) + extra
        return _write(tmp, name, body)

    def test_fallback_id_stable_across_file_change(self):
        # 复审 #18 原样回放：仅追加 '; exported again' 后标识/后缀曾漂移
        with tempfile.TemporaryDirectory() as tmp:
            base = parse_ris(self._smith_file(tmp, "a.ris"))
            reexport = parse_ris(self._smith_file(tmp, "b.ris", "; exported again\n"))
        self.assertEqual(
            [a.raw["_fallback_id"] for a in base],
            [a.raw["_fallback_id"] for a in reexport],
        )

    def test_reimport_after_file_change_adds_zero_rows(self):
        # 端到端（CLI 同序管道：find_duplicates → stabilize → upsert）：
        # 首导 2 行；换字节重导（文件加注释行）不得新增第 3 行
        import coscreen.db as db_mod
        from coscreen.dedup import find_duplicates, stabilize_conflicting_keys

        def import_once(db_path: str, path: Path):
            articles = parse_ris(path)
            _, report = find_duplicates(articles)
            known = db_mod.list_articles(db_path, include_duplicates=True)
            with redirect_stderr(StringIO()):
                stabilize_conflicting_keys(
                    articles,
                    report.exact_pairs + report.fuzzy_pairs + report.key_conflict_pairs,
                    known=known,
                )
                return db_mod.upsert_articles(db_path, articles, report.exact_pairs), articles

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "t.db")
            db_mod.init_db(db_path)
            summary1, arts1 = import_once(db_path, self._smith_file(tmp, "a.ris"))
            rows1 = db_mod.list_articles(db_path, include_duplicates=True)
            self.assertEqual(len(rows1), 2)
            self.assertEqual(summary1.n_new, 2)
            self.assertNotEqual(arts1[0].zotero_key, arts1[1].zotero_key)
            # 字节级幂等：同一文件重导零新增
            summary2, _ = import_once(db_path, self._smith_file(tmp, "a2.ris"))
            self.assertEqual(summary2.n_new, 0)
            self.assertEqual(len(db_mod.list_articles(db_path, include_duplicates=True)), 2)
            # 内容级幂等：仅加一行注释的重导出（U2-P5 验收）零新增
            summary3, _ = import_once(
                db_path, self._smith_file(tmp, "b.ris", "; exported again\n")
            )
            self.assertEqual(summary3.n_new, 0)
            self.assertEqual(len(db_mod.list_articles(db_path, include_duplicates=True)), 2)

    def test_web_stabilize_path_stable_across_file_change(self):
        # Web 导入路径（真实 _stabilize_generated_import_keys）同样稳定
        from custom_backend.main import _stabilize_generated_import_keys

        with tempfile.TemporaryDirectory() as tmp:
            first = parse_ris(self._smith_file(tmp, "a.ris"))
            _stabilize_generated_import_keys([], first)
            reexport = parse_ris(self._smith_file(tmp, "b.ris", "; exported again\n"))
            _stabilize_generated_import_keys(first, reexport)
        self.assertEqual(
            [a.zotero_key for a in first], [a.zotero_key for a in reexport]
        )

    def test_identical_records_share_fallback_id(self):
        # 完全相同的记录共享标识：自然并入同一行（真实重复的正确语义）
        body = (
            "TY  - JOUR\nTI  - Intro\nAU  - Carol\nER  -\n"
            "TY  - JOUR\nTI  - Intro\nAU  - Carol\nER  -\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            articles = parse_ris(_write(tmp, "intro.ris", body))
        self.assertEqual(articles[0].raw["_fallback_id"], articles[1].raw["_fallback_id"])


class ReImportIdempotencyTests(unittest.TestCase):
    """R1 验收：键/标识改动后，重导同一文件零新增行；既有行主键不重写。"""

    @staticmethod
    def _file(tmp: str, name: str, extra: str = "") -> Path:
        # 混合形态：来源键行 + 无键行（含 A1/J2/DI 等补齐标签）
        body = (
            "TY  - JOUR\nTI  - Keyed Paper\nAU  - Doe, Jane\nAN  - WOS:0001\nER  -\n"
            "TY  - JOUR\nTI  - Unkeyed Paper\nA1  - Roe, Richard\nY1  - 2019\n"
            "J2  - J Abbrev\nDI  - 10.1/unkeyed\nER  -\n"
        ) + extra
        return _write(tmp, name, body)

    def test_reimport_same_and_modified_file_zero_new_rows(self):
        import coscreen.db as db_mod
        from coscreen.dedup import find_duplicates, stabilize_conflicting_keys

        def import_once(db_path: str, path: Path):
            articles = parse_ris(path)
            _, report = find_duplicates(articles)
            known = db_mod.list_articles(db_path, include_duplicates=True)
            with redirect_stderr(StringIO()):
                stabilize_conflicting_keys(
                    articles,
                    report.exact_pairs + report.fuzzy_pairs + report.key_conflict_pairs,
                    known=known,
                )
                return db_mod.upsert_articles(db_path, articles, report.exact_pairs)

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "t.db")
            db_mod.init_db(db_path)
            summary = import_once(db_path, self._file(tmp, "a.ris"))
            self.assertEqual(summary.n_new, 2)
            keys_after_first = [
                a.zotero_key
                for a in db_mod.list_articles(db_path, include_duplicates=True)
            ]
            # 字节级重导：零新增、主键不变
            summary = import_once(db_path, self._file(tmp, "a2.ris"))
            self.assertEqual(summary.n_new, 0)
            # 内容级重导（文件尾部加注释行）：零新增、主键不变
            summary = import_once(db_path, self._file(tmp, "b.ris", "; exported again\n"))
            self.assertEqual(summary.n_new, 0)
            rows = db_mod.list_articles(db_path, include_duplicates=True)
            self.assertEqual(
                sorted(a.zotero_key for a in rows), sorted(keys_after_first)
            )
            self.assertEqual(len(rows), 2)

    @unittest.skip(
        "R6-D2（去重引擎泳道）：2.1.3 旧 auto-* 键公式（sha1(doi|title)）与新公式"
        "（sha1(content_hash|姓氏|年份)，normalize.py）不兼容导致重导新增行；"
        "键公式不在本泳道（normalize.py 归回归泳道），兼容映射由 dedup-engine 落实"
    )
    def test_legacy_2_1_3_auto_key_reimport_idempotent(self):
        # 验收（R6-D2 落地后启用）：按 2.1.3 公式预置 auto-<sha1(doi|title)[:12]>
        # 行后重导同一 RIS，断言零新增
        raise NotImplementedError


if __name__ == "__main__":
    unittest.main()
