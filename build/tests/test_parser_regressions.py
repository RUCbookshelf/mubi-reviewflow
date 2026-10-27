"""首审 U2 解析器条目 + r5/r6 复审新确认项的回归钉（R6-P1 全矩阵）。

覆盖 parse_csv/parse_ris → find_duplicates → 键消歧 → db.upsert_articles
全链路；每条用例按首审 core_audit_findings.md / r5 复审 ④ 节的复现手法
原样回放修复前的失败输入。修复归属其他泳道的条目按 handoff 要求钉住
现状并注明原因（skip/注释），不冒充已修复。

矩阵对应：
- U2-P1 Author 列（集成轮已修，此处保持钉住）；
- U2-P2/R6-P3 AN/ID 非法键、非全局稳定标识（自由文本/短序号）与同文件
  键冲突的降级；跨文件同键两文件 CLI 复现（r2 评审）；
- U2-P3 首行字段数校验；U2-P12 物理行号；
- U2-P5/复审#18：_fallback_id 保持 v2.1.3 形态（R1），内容稳定标识放
  raw["_record_id"]（含同文件 occurrence 区分，不折叠真实重复行），
  下游消费归 R6-D5（漂移族以 skip 钉住）；
- U2-P6/P9/P10 J2/JA/Y1/SO/DI/A1 标签映射补齐；
- U2-P8 + 复审 #12/#13/#17：单空格 'ER -' 规范化后真正导入；末条缺 ER
  不整文件拒绝（完整记录照常入库+逐条告警）；合法续行不误拒（r2 评审 R3）；
- U2-P11 + 复审 #11/#16 编码回退置信度与告警（cp1252 不再静默乱码）；
- U2-P7/R5#14 以 skip 钉住并归因 R6-D4/R6-D2；
- R6-P2 用户保留列注入剔除；
- R1 验收：字节级重导零新增 + 2.1.3 形态预置库（旧 id+后缀行）CLI 与
  _stabilize 两路重导零新增（V213PresetReimportTests）；2.1.3 旧 auto
  键公式归 R6-D2（skip 注明）。
"""

from __future__ import annotations

import os
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


def _backend_main():
    """按需导入 custom_backend.main（模块级 DATA_DIR 在首次导入时冻结）。

    若本文件先于其他 Web 测试类运行且环境未设 COBOOKSHELF_DATA_DIR，
    先指到临时目录——否则 DATA_DIR 冻结为仓库相对路径 ./data，后续
    类的任务/用户库会写进仓库（测试顺序敏感，r2 会话实测 409 污染）。
    """
    if "COBOOKSHELF_DATA_DIR" not in os.environ:
        os.environ["COBOOKSHELF_DATA_DIR"] = tempfile.mkdtemp(prefix="rf_parser_main_")
    import custom_backend.main as main_mod

    return main_mod

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
        _stabilize_generated_import_keys = _backend_main()._stabilize_generated_import_keys

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

    def test_last_record_single_space_er_normalized_imports_both(self):
        # r2 评审 R3 裁定：单空格 'ER -' 是终止行笔误（Zotero 导入器自身
        # 允许 1-2 空格），不得整文件拒绝——规范化为 'ER  -' 后 Beta 真正
        # 导入（修复前：rc=2、0 行入库、Alpha 也被丢弃）
        body = "TY  - JOUR\nTI  - Alpha\nER  -\nTY  - JOUR\nTI  - Beta\nER -\n"
        with tempfile.TemporaryDirectory() as tmp:
            err = StringIO()
            with redirect_stderr(err):
                articles = parse_ris(_write(tmp, "p8.ris", body))
        self.assertEqual([a.title for a in articles], ["Alpha", "Beta"])
        self.assertNotIn("缺少结束标签 ER", err.getvalue())

    def test_last_record_missing_er_imports_complete_with_warning(self):
        # r2 评审 R3「绝不整文件拒绝」：末条完全缺 ER 时完整记录（Alpha）
        # 照常导入，被 rispy 丢弃的记录（Beta）逐条显式告警，不再 ParseError
        body = "TY  - JOUR\nTI  - Alpha\nER  -\nTY  - JOUR\nTI  - Beta\n"
        with tempfile.TemporaryDirectory() as tmp:
            err = StringIO()
            with redirect_stderr(err):
                articles = parse_ris(_write(tmp, "trunc.ris", body))
        self.assertEqual([a.title for a in articles], ["Alpha"])
        message = err.getvalue()
        self.assertIn("第 2 条记录", message)
        self.assertIn("第 4 行", message)
        self.assertIn("Beta", message)  # 标题定位
        self.assertIn("未被导入", message)

    def test_cli_import_survives_damaged_last_record(self):
        # r2 评审要求：CLI 不得因末条损坏而 0 行退出 2；Alpha 必须已入库
        import coscreen.cli as cli
        from argparse import Namespace
        from contextlib import redirect_stdout

        body = "TY  - JOUR\nTI  - Alpha\nAU  - Alice\nDO  - 10.1/a\nER  -\nTY  - JOUR\nTI  - Beta\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "dmg.ris", body)
            db_path = str(Path(tmp) / "dmg.db")
            err = StringIO()
            out = StringIO()
            with redirect_stderr(err), redirect_stdout(out):
                code = cli.cmd_import(
                    Namespace(file=str(path), db=db_path, screener=None,
                              task=None, auto_merge_fuzzy=False)
                )
            self.assertEqual(code, 0)
            import sqlite3
            from contextlib import closing

            with closing(sqlite3.connect(db_path)) as conn:
                rows = conn.execute("SELECT title FROM articles").fetchall()
        self.assertEqual(rows, [("Alpha",)])

    def test_mid_record_missing_er_warns_chimera(self):
        # 中部缺 ER（复审 B3 落地后转正）：rispy 原会把 Beta+Gamma 合并为
        # 嵌合记录（作者污染、Gamma 标题静默消失）——现 _split_chimera_records
        # 在嵌合点补全合成 ER：Beta 按截至字段导入、Gamma 独立导入，两条
        # 都不丢；文件级告警回流 raw['_parse_warnings']（Web/API 可见）。
        body = (
            "TY  - JOUR\nTI  - Alpha\nAU  - Alice\nDO  - 10.1/alpha\nER  -\n"
            "TY  - JOUR\nTI  - Beta\nAU  - Bob\nDO  - 10.1/beta\n"
            "TY  - JOUR\nTI  - Gamma\nAU  - Carol\nDO  - 10.1/gamma\nER  -\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            err = StringIO()
            with redirect_stderr(err):
                articles = parse_ris(_write(tmp, "mid.ris", body))
        self.assertEqual([a.title for a in articles], ["Alpha", "Beta", "Gamma"])
        self.assertEqual([a.doi for a in articles],
                         ["10.1/alpha", "10.1/beta", "10.1/gamma"])  # 无字段污染
        self.assertIn("拆分", err.getvalue())
        self.assertIn("第 6 行", err.getvalue())
        # 文件级告警回流每条记录（B3：API/Web 路径可见）
        for article in articles:
            self.assertTrue(any("拆分" in w for w in article.raw.get("_parse_warnings", [])))

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

    def test_gbk_traditional_chinese_file_not_rejected(self):
        # 复审 B4 原样回放（R2 误拒）：GBK 编码繁体中文 RIS 曾因 mojibake 标记
        # 误判整文件 ParseError（常用繁体 160 字采样 40 字命中标记集）。
        body = "TY  - JOUR\nTI  - 臨床試驗的系統性回顧與薈萃分析\nAU  - 王大明\nER  - \n"
        with tempfile.TemporaryDirectory() as tmp:
            err = StringIO()
            with redirect_stderr(err):
                article = parse_ris(_write(tmp, "trad.ris", body.encode("gbk")))[0]
        self.assertEqual(article.title, "臨床試驗的系統性回顧與薈萃分析")
        self.assertEqual(article.authors, "王大明")

    def test_gbk_traditional_csv_not_rejected(self):
        # 同上（CSV 路）：「針灸傾斜角度對溫針療效之影響」曾整文件拒绝
        data = "Title,Creator\r\n針灸傾斜角度對溫針療效之影響,Chen\r\n".encode("gbk")
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stderr(StringIO()):
                article = parse_csv(_write(tmp, "trad.csv", data))[0]
        self.assertEqual(article.title, "針灸傾斜角度對溫針療效之影響")

    def test_cp1252_consecutive_accents_not_decoded_as_gbk(self):
        # 复审 B5 原样回放：'Größe der Stichprobe' 的 ö+ß（相邻高位字母对）
        # 曾被 GBK 静默解成 'Gr鲞e der Stichprobe'（标题/content_hash 双污染）。
        data = b"Title,Creator,DOI\r\nGr\xf6\xdfe der Stichprobe,Smith J,10.1/g\r\n"
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stderr(StringIO()):
                article = parse_csv(_write(tmp, "grosse.csv", data))[0]
        self.assertEqual(article.title, "Größe der Stichprobe")
        warnings = " ".join(article.raw.get("_parse_warnings", []))
        self.assertIn("CP1252", warnings)

    def test_gbk_rare_char_not_misjudged_cp1252(self):
        # 复审 B6 原样回放：合法 GBK 含生僻字「狝」（AA41）曾被单个标记
        # 否决 GBK、按 cp1252 静默解成 'ªA'。
        data = "Title,Creator\r\nA long ASCII title with rare char 狝 inside it,Smith\r\n".encode("gbk")
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stderr(StringIO()):
                article = parse_csv(_write(tmp, "rare.csv", data))[0]
        self.assertEqual(article.title, "A long ASCII title with rare char 狝 inside it")

    def test_corrupted_gbk_rejected_loudly(self):
        # 复审 B10（低危侧收口）：含非法字节（0xFF）的损坏 GBK 文件不得带
        # 替换字符静默导入——响亮拒绝（2.1.3 基线行为）。
        good = "Title,Creator\r\n" + "".join(f"中医临床对照试验第{i}行,Wang\r\n" for i in range(50))
        raw = good.encode("gbk")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ParseError) as ctx:
                parse_csv(_write(tmp, "bad.csv", raw[:60] + b"\xff" + raw[61:]))
        self.assertIn("另存为 UTF-8", str(ctx.exception))

    def test_utf16le_file_decoded_not_misled_downstream(self):
        # 复审 B9 原样回放：UTF-16LE CSV 曾被当作单字节文本「成功」解码
        # （NUL 算 ASCII），把编码错误误报成缺列/无记录——现按 UTF-16 解码。
        data = "Title,Creator\r\nTrial of drug X,Smith\r\n".encode("utf-16-le")
        with tempfile.TemporaryDirectory() as tmp:
            article = parse_csv(_write(tmp, "u16.csv", data))[0]
        self.assertEqual(article.title, "Trial of drug X")
        self.assertIn("UTF-16", " ".join(article.raw.get("_parse_warnings", [])))

    def test_trailing_record_missing_er_warning_flows_into_raw(self):
        # 复审 B3：末条缺 ER 的丢弃告警回流 raw['_parse_warnings']（此前只
        # print(stderr)，Web/API 路径用户只看到「少一条」）。
        body = (
            "TY  - JOUR\nTI  - Alpha\nAU  - Alice\nER  -\n"
            "TY  - JOUR\nTI  - Truncated\nAU  - Bob\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stderr(StringIO()):
                articles = parse_ris(_write(tmp, "tail.ris", body))
        self.assertEqual([a.title for a in articles], ["Alpha"])
        self.assertTrue(any("缺少结束标签 ER" in w for w in articles[0].raw.get("_parse_warnings", [])))


class SourceKeyDemotionTests(unittest.TestCase):
    """U2-P2/R6-P3：非法键与同文件键冲突降级为派生键，直调 upsert 不再覆盖。"""

    def test_same_an_different_titles_not_collapsed(self):
        # 首审 U2-P2 原样回放：同 'AN  - Box 3' 的 Women/Men 曾 exact_key 合并剩 1 行。
        # r2 评审后口径：'Box 3' 含空白属 archiveLocation 自由文本，两条一致
        # 降级为内容派生键（跨文件也不再撞键覆盖）
        body = (
            "TY  - JOUR\nTI  - Women in science\nAU  - Womon, Wendy\nAN  - Box 3\nER  -\n"
            "TY  - JOUR\nTI  - Men in science\nAU  - Mandel, Mark\nAN  - Box 3\nER  -\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            err = StringIO()
            with redirect_stderr(err):
                articles = parse_ris(_write(tmp, "box3.ris", body))
        keys = [a.zotero_key for a in articles]
        self.assertNotEqual(keys[0], keys[1])
        self.assertTrue(all(k.startswith("auto-") for k in keys))
        self.assertEqual(
            [a.raw.get("_demoted_source_key") for a in articles], ["Box 3", "Box 3"]
        )
        self.assertIn("不是全局稳定标识", " ".join(articles[0].raw.get("_parse_warnings", [])))
        self.assertIn("Box 3", err.getvalue())

    def test_csv_id_column_free_text_demoted_like_ris(self):
        # 复审（口径分叉收口）：同一自由文本键 'Box 3' 在 RIS 的 AN 列被降级
        # 告警，CSV 的 ID 列曾绕过全部降级门静默用作字面键（直调 upsert 即
        # 静默覆盖）——现 ID/AN/Accession_Number 列过同一三道闸。
        import coscreen.db as db_mod

        data = ("Title,Creator,ID\r\n"
                "Women Study,Womon Wendy,Box 3\r\n"
                "Men Study,Mandel Mark,Box 3\r\n")
        with tempfile.TemporaryDirectory() as tmp:
            err = StringIO()
            with redirect_stderr(err):
                articles = parse_csv(_write(tmp, "idcol.csv", data))
        keys = [a.zotero_key for a in articles]
        self.assertNotEqual(keys[0], keys[1])
        self.assertTrue(all(k.startswith("auto-") for k in keys), keys)
        self.assertIn("不是全局稳定标识",
                      " ".join(articles[0].raw.get("_parse_warnings", [])))
        # 直调 upsert（公开 API）不再静默覆盖
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "t.db")
            db_mod.init_db(db_path)
            db_mod.upsert_articles(db_path, articles, [])
            self.assertEqual(len(db_mod.list_articles(db_path, include_duplicates=True)), 2)

    def test_csv_sequence_number_id_column_demoted(self):
        # 复审：逐行序号 1/2（无 Key 列的 CSV ID 列）曾静默用作主键
        data = ("Title,Creator,ID\r\n"
                "First Study,Alice,1\r\n"
                "Second Study,Bob,2\r\n")
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stderr(StringIO()):
                articles = parse_csv(_write(tmp, "seq.csv", data))
        self.assertTrue(all(a.zotero_key.startswith("auto-") for a in articles), articles)

    def test_same_key_same_title_different_author_demoted(self):
        # 复审 B1/B7：同键同题（content_hash 相同）但作者不同（Smith John vs
        # Jane）曾不触发降级、CLI 全链路静默丢 John 行——现解析层按
        # (哈希,姓,名,年份) 身份降级第二条，去重层另报候选。
        body = (
            "TY  - JOUR\nTI  - Same Topic\nAU  - Smith, John\nPY  - 2020\nAN  - SMITH2020\nER  -\n"
            "TY  - JOUR\nTI  - Same Topic\nAU  - Smith, Jane\nPY  - 2020\nAN  - SMITH2020\nER  -\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            err = StringIO()
            with redirect_stderr(err):
                articles = parse_ris(_write(tmp, "smith.ris", body))
        keys = [a.zotero_key for a in articles]
        self.assertNotEqual(keys[0], keys[1])
        self.assertEqual(articles[1].raw.get("_demoted_source_key"), "SMITH2020")
        self.assertIn("作者/年份不同",
                      " ".join(articles[1].raw.get("_parse_warnings", [])))


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

    def test_free_text_and_sequence_keys_demoted(self):
        # r2 评审 U2-P2：单记录 'AN - Box 3'（含空白自由文本）与 'ID - 1'
        # （短纯数字序号）不是全局稳定标识，跨文件会撞键覆盖——一致降级 auto。
        # r3 评审裁定纯数字阈值收窄到 <=2（5 位 '12345' 是合法 PMID 形态）
        for tag, key in (("AN", "Box 3"), ("ID", "1"), ("ID", "12")):
            with tempfile.TemporaryDirectory() as tmp:
                with redirect_stderr(StringIO()):
                    article = parse_ris(
                        _write(
                            tmp,
                            f"seq{key[-2:]}.ris",
                            f"TY  - JOUR\nTI  - Paper {key}\n{tag}  - {key}\nER  -\n",
                        )
                    )[0]
            self.assertTrue(
                article.zotero_key.startswith("auto-"),
                f"{tag}={key!r} 应降级，实际 {article.zotero_key!r}",
            )
            self.assertEqual(article.raw.get("_demoted_source_key"), key)

    def test_pmid_form_numeric_keys_kept(self):
        # r3 评审：'123'/'12345'/'12345678' 等 3 位及以上纯数字是 PMID 形态
        # 的全局稳定标识，不得降级（修复前 <=5 阈值曾把 '12345' 降级）
        for key in ("123", "12345", "12345678"):
            with tempfile.TemporaryDirectory() as tmp:
                article = parse_ris(
                    _write(
                        tmp,
                        f"pmid{key}.ris",
                        f"TY  - JOUR\nTI  - Old PMID paper {key}\nAN  - {key}\nER  -\n",
                    )
                )[0]
            self.assertEqual(article.zotero_key, key)

    def test_pmid_form_key_v2_1_3_preset_reimport_zero_new(self):
        # r3 评审验收：2.1.3 形态 source-key 库（键直用 '12345'，raw 无
        # 生成键三元组）重导同一文件必须零新增（修复前 '12345' 被误降级
        # 为 auto-*，「新增 1 条」出现重复行）
        import sqlite3
        from argparse import Namespace
        from contextlib import closing, redirect_stdout

        import coscreen.cli as cli
        import coscreen.db as db_mod
        from coscreen.models import Article

        with tempfile.TemporaryDirectory() as tmp:
            path = _write(
                tmp, "pmid.ris", "TY  - JOUR\nTI  - Old PMID paper\nAU  - Womon, Wendy\nAN  - 12345\nER  -\n"
            )
            db_path = str(Path(tmp) / "t.db")
            db_mod.init_db(db_path)
            db_mod.upsert_articles(
                db_path, [Article(zotero_key="12345", title="Old PMID paper", raw={})], []
            )
            with redirect_stderr(StringIO()), redirect_stdout(StringIO()):
                code = cli.cmd_import(
                    Namespace(file=str(path), db=db_path, screener=None,
                              task=None, auto_merge_fuzzy=False)
                )
            self.assertEqual(code, 0)
            with closing(sqlite3.connect(db_path)) as conn:
                rows = conn.execute("SELECT zotero_key, title FROM articles").fetchall()
        self.assertEqual(rows, [("12345", "Old PMID paper")])  # 0 新增：仍是 1 行

    def test_whitespace_key_v2_1_3_preset_reimport_adopts_legacy_key(self):
        # R6-D2 落地后的 R1 验收（原「已知例外」钉转正）：含空白自由文本键
        # （AN 映射 archiveLocation，如 'Box 3'）在导入时降级 auto，但
        # stabilize 的身份匹配层（content_hash＋首作者门）命中 2.1.3 既有
        # 'Box 3' 行时沿用其键走更新路径——重导零新增、旧主键不重写。
        # 修复前现状：新增 1 行 auto-* 重复（r3 评审实测）。
        import sqlite3
        from argparse import Namespace
        from contextlib import closing, redirect_stdout

        import coscreen.cli as cli
        import coscreen.db as db_mod
        from coscreen.models import Article

        with tempfile.TemporaryDirectory() as tmp:
            path = _write(
                tmp, "box3.ris", "TY  - JOUR\nTI  - Women in science\nAU  - Womon, Wendy\nAN  - Box 3\nER  -\n"
            )
            db_path = str(Path(tmp) / "t.db")
            db_mod.init_db(db_path)
            db_mod.upsert_articles(
                db_path,
                [Article(zotero_key="Box 3", title="Women in science", raw={})],
                [],
            )
            with redirect_stderr(StringIO()), redirect_stdout(StringIO()):
                code = cli.cmd_import(
                    Namespace(file=str(path), db=db_path, screener=None,
                              task=None, auto_merge_fuzzy=False)
                )
            self.assertEqual(code, 0)
            with closing(sqlite3.connect(db_path)) as conn:
                rows = conn.execute("SELECT zotero_key, title FROM articles ORDER BY zotero_key").fetchall()
        # R1 验收：沿用库内 'Box 3' 键零新增（旧行更新，主键不变）
        self.assertEqual(rows, [("Box 3", "Women in science")])


class CrossFileKeyClashTests(unittest.TestCase):
    """r2 评审 U2-P2：跨文件同键不同文献曾「更新 1 条」覆盖（DB 只剩 Men）。"""

    @staticmethod
    def _import(db_path: str, path: Path) -> None:
        from argparse import Namespace
        from contextlib import redirect_stdout
        import coscreen.cli as cli

        with redirect_stderr(StringIO()), redirect_stdout(StringIO()):
            code = cli.cmd_import(
                Namespace(file=str(path), db=db_path, screener=None,
                          task=None, auto_merge_fuzzy=False)
            )
        assert code == 0

    def test_two_files_same_an_both_survive(self):
        # 评审复现原样回放：两个单记录文件同 AN 'Box 3'，第二次导入
        # 曾「更新 1 条」把 Women 覆盖成 Men；降级 auto 后两行都在
        import sqlite3
        from contextlib import closing

        women = "TY  - JOUR\nTI  - Women in science\nAU  - Womon, Wendy\nAN  - Box 3\nER  -\n"
        men = "TY  - JOUR\nTI  - Men in science\nAU  - Mandel, Mark\nAN  - Box 3\nER  -\n"
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "t.db")
            for i, body in enumerate((women, men)):
                self._import(db_path, _write(tmp, f"f{i}.ris", body))
            with closing(sqlite3.connect(db_path)) as conn:
                rows = conn.execute("SELECT title FROM articles ORDER BY title").fetchall()
        self.assertEqual(rows, [("Men in science",), ("Women in science",)])

    def test_two_files_same_numeric_id_both_survive(self):
        import sqlite3
        from contextlib import closing

        women = "TY  - JOUR\nTI  - Women in science\nAU  - Womon, Wendy\nID  - 1\nER  -\n"
        men = "TY  - JOUR\nTI  - Men in science\nAU  - Mandel, Mark\nID  - 1\nER  -\n"
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "t.db")
            for i, body in enumerate((women, men)):
                self._import(db_path, _write(tmp, f"g{i}.ris", body))
            with closing(sqlite3.connect(db_path)) as conn:
                rows = conn.execute("SELECT title FROM articles ORDER BY title").fetchall()
        self.assertEqual(rows, [("Men in science",), ("Women in science",)])


class CsvInFileKeyClashTests(unittest.TestCase):
    """CSV 同文件同键冲突降级（U2-P2 的 CSV 侧）。"""

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
    """U2-P5/复审 #18 + r2 评审 R1/U2-P5 定案：

    - ``_fallback_id`` 保持 v2.1.3 出货形态（sha256(整文件字节):行号）——
      2.1.3 库重导幂等依赖它，改形态即 R1 断裂（r2 评审实测 n_new 0→2）；
    - 内容稳定标识放独立字段 ``raw["_record_id"]``（同文件相同内容加
      "#N" occurrence，不折叠真实重复行），消费与切换归去重引擎 R6-D5；
    - 「换字节重导零新增」在 _fallback_id 旧形态下不成立（后缀随文件
      摘要漂移），整族以 skip 钉住并注明 R6-D5 归属。
    """

    @staticmethod
    def _smith_file(tmp: str, name: str, extra: str = "") -> Path:
        # 同题同姓同年（John vs Jane Smith 2020）：基础键相同，须靠后缀消歧
        body = (
            "TY  - JOUR\nTI  - Same Topic\nAU  - Smith, John\nPY  - 2020\nER  -\n"
            "TY  - JOUR\nTI  - Same Topic\nAU  - Smith, Jane\nPY  - 2020\nER  -\n"
        ) + extra
        return _write(tmp, name, body)

    def test_fallback_id_matches_v2_1_3_format(self):
        # R1 钉：_fallback_id 与 git show v2.1.3:...ris_parser.py:96-97 的
        # f"{sha256(文件字节).hexdigest()}:{record_no}" 逐字一致
        import hashlib

        with tempfile.TemporaryDirectory() as tmp:
            path = self._smith_file(tmp, "a.ris")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            articles = parse_ris(path)
        self.assertEqual(
            [a.raw["_fallback_id"] for a in articles],
            [f"{digest}:1", f"{digest}:2"],
        )

    def test_record_id_stable_across_file_change(self):
        # 内容稳定标识（独立字段）：换字节重导出后逐条相同（U2-P5 的
        # 解析侧交付物；下游消费归 R6-D5）
        with tempfile.TemporaryDirectory() as tmp:
            base = parse_ris(self._smith_file(tmp, "a.ris"))
            reexport = parse_ris(self._smith_file(tmp, "b.ris", "; exported again\n"))
        self.assertEqual(
            [a.raw["_record_id"] for a in base],
            [a.raw["_record_id"] for a in reexport],
        )
        # 同文件内两条不同内容记录的 _record_id 互不相同
        self.assertNotEqual(base[0].raw["_record_id"], base[1].raw["_record_id"])

    def test_identical_records_get_occurrence_suffixed_record_ids(self):
        # r2 评审 U2-P5 定案：同文件内完全相同的记录不折叠——第 2 条的
        # _record_id 加 "#2" occurrence 区分（供 R6-D5 保持 2 行基线）
        body = (
            "TY  - JOUR\nTI  - Intro\nAU  - Carol\nER  -\n"
            "TY  - JOUR\nTI  - Intro\nAU  - Carol\nER  -\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            articles = parse_ris(_write(tmp, "intro.ris", body))
        ids = [a.raw["_record_id"] for a in articles]
        self.assertNotEqual(ids[0], ids[1])
        self.assertTrue(ids[1].startswith(ids[0]) and "#2" in ids[1])

    def test_identical_records_keep_two_rows_web_and_direct(self):
        # r2 评审 U2-P5 复现原样回放：两条 'Intro'/Carol 曾被折叠成 1 行
        # （Web stabilize 后同键、直调 upsert n_new=1）。恢复 2.1.3 形态
        # _fallback_id 后两路均回到 2 行基线
        import coscreen.db as db_mod

        _stabilize_generated_import_keys = _backend_main()._stabilize_generated_import_keys

        body = (
            "TY  - JOUR\nTI  - Intro\nAU  - Carol\nER  -\n"
            "TY  - JOUR\nTI  - Intro\nAU  - Carol\nER  -\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            articles = parse_ris(_write(tmp, "intro.ris", body))
            _stabilize_generated_import_keys([], articles)
            keys = [a.zotero_key for a in articles]
            self.assertEqual(len(set(keys)), 2)
            db_path = str(Path(tmp) / "t.db")
            db_mod.init_db(db_path)
            summary = db_mod.upsert_articles(db_path, articles, [])
            rows = db_mod.list_articles(db_path, include_duplicates=True)
        self.assertEqual(summary.n_new, 2)
        self.assertEqual(len(rows), 2)

    def test_reimport_byte_identical_zero_new_rows(self):
        # 字节级幂等（同一文件重导）：CLI 同序管道下零新增
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
            summary1 = import_once(db_path, self._smith_file(tmp, "a.ris"))
            self.assertEqual(summary1.n_new, 2)
            summary2 = import_once(db_path, self._smith_file(tmp, "a2.ris"))
            self.assertEqual(summary2.n_new, 0)
            self.assertEqual(
                len(db_mod.list_articles(db_path, include_duplicates=True)), 2
            )

    def test_exact_18_reexport_zero_new_rows(self):
        # U2-P5/#18 落地后的 R1 验收（原现状钉转正）：两条完全相同的
        # 'Intro'/Carol + 仅追加 '; exported again' 重导——CLI 与 Web 两路
        # 均零新增（dedup/_stable_content_id 与 main._stabilize 优先消费
        # 内容稳定的 raw['_record_id']（含 #N occurrence），旧 2.1.3 行
        # 回退 _fallback_id）。修复前：CLI n_new=1 共 3 行、Web n_new=2
        # 共 4 行（_fallback_id 随整文件摘要漂移）。
        import coscreen.db as db_mod
        from coscreen.dedup import find_duplicates, stabilize_conflicting_keys

        body = (
            "TY  - JOUR\nTI  - Intro\nAU  - Carol\nER  -\n"
            "TY  - JOUR\nTI  - Intro\nAU  - Carol\nER  -\n"
        )

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
            first = _write(tmp, "a.ris", body)
            second = _write(tmp, "b.ris", body + "; exported again\n")
            db_path = str(Path(tmp) / "cli.db")
            db_mod.init_db(db_path)
            self.assertEqual(import_once(db_path, first).n_new, 2)
            self.assertEqual(import_once(db_path, second).n_new, 0)  # 零新增
            self.assertEqual(
                len(db_mod.list_articles(db_path, include_duplicates=True)), 2
            )

        _stabilize_generated_import_keys = _backend_main()._stabilize_generated_import_keys
        with tempfile.TemporaryDirectory() as tmp:
            first = _write(tmp, "a.ris", body)
            second = _write(tmp, "b.ris", body + "; exported again\n")
            db_path = str(Path(tmp) / "web.db")
            db_mod.init_db(db_path)
            batch1 = parse_ris(first)
            _stabilize_generated_import_keys([], batch1)
            db_mod.upsert_articles(db_path, batch1, [])
            self.assertEqual(
                len(db_mod.list_articles(db_path, include_duplicates=True)), 2
            )
            batch2 = parse_ris(second)
            _stabilize_generated_import_keys(
                db_mod.list_articles(db_path, include_duplicates=True), batch2
            )
            summary2 = db_mod.upsert_articles(db_path, batch2, [])
            self.assertEqual(summary2.n_new, 0)  # 零新增（_record_id 匹配）
            self.assertEqual(
                len(db_mod.list_articles(db_path, include_duplicates=True)), 2
            )

    def test_reimport_after_file_change_adds_zero_rows(self):
        # R6-D5 落地后的启用（原 skip 转正）：文件尾部追加注释行重导，
        # CLI 管道零新增——_record_id 内容稳定，不随文件字节漂移。
        import coscreen.db as db_mod
        from coscreen.dedup import find_duplicates, stabilize_conflicting_keys

        body = (
            "TY  - JOUR\nTI  - Intro\nAU  - Carol\nER  -\n"
            "TY  - JOUR\nTI  - Intro\nAU  - Carol\nER  -\n"
        )

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
            first = _write(tmp, "a.ris", body)
            second = _write(tmp, "b.ris", body + "; exported again\n")
            db_path = str(Path(tmp) / "t.db")
            db_mod.init_db(db_path)
            self.assertEqual(import_once(db_path, first).n_new, 2)
            self.assertEqual(import_once(db_path, second).n_new, 0)
            self.assertEqual(
                len(db_mod.list_articles(db_path, include_duplicates=True)), 2
            )


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
            rows = db_mod.list_articles(db_path, include_duplicates=True)
            self.assertEqual(
                sorted(a.zotero_key for a in rows), sorted(keys_after_first)
            )
            self.assertEqual(len(rows), 2)
            # 内容级重导（文件尾部加注释行零新增）已由 R6-D5 落地，
            # 见 test_reimport_modified_file_zero_new_rows

    def test_reimport_modified_file_zero_new_rows(self):
        # R6-D5 落地后的启用（原 skip 转正）：文件尾部加注释行重导零新增
        # （_record_id 内容稳定；_fallback_id 形态保持 v2.1.3 不变）。
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
            self.assertEqual(import_once(db_path, self._file(tmp, "a.ris")).n_new, 2)
            self.assertEqual(
                import_once(db_path, self._file(tmp, "a2.ris", "; note\n")).n_new, 0
            )
            self.assertEqual(
                len(db_mod.list_articles(db_path, include_duplicates=True)), 2
            )

    def test_smith_john_jane_same_key_direct_upsert_no_collapse(self):
        # R6-D4 落地后的验收（原现状钉转正）：auto 键公式掺入名
        # （content_hash|姓氏|名|年份）——同姓同年同题但名不同
        # （Smith John/Jane 2020）两记录键不同，直调 db.upsert_articles
        # 各自成行，不再坍缩。修复前：两记录同键，主键 upsert 坍缩 1 行
        # （U2-P7/R5-④#14 原样回放）。完全同内容（无作者/无年份同题）仍
        # 视为真重复坍缩——内容无任何区分度，属精确重复语义。
        import coscreen.db as db_mod

        smith = (
            "TY  - JOUR\nTI  - Same Topic\nAU  - Smith, John\nPY  - 2020\nER  -\n"
            "TY  - JOUR\nTI  - Same Topic\nAU  - Smith, Jane\nPY  - 2020\nER  -\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            articles = parse_ris(_write(tmp, "smith.ris", smith))
            self.assertNotEqual(articles[0].zotero_key, articles[1].zotero_key)
            db_path = str(Path(tmp) / "t.db")
            db_mod.init_db(db_path)
            summary = db_mod.upsert_articles(db_path, articles, [])
            rows = db_mod.list_articles(db_path, include_duplicates=True)
        self.assertEqual((summary.n_imported, summary.n_new), (2, 2))
        self.assertEqual(len(rows), 2)  # 两行共存，无覆盖

        # 完全同内容（无作者/无年份同题）：真重复，仍按精确重复坍缩
        bare = "TY  - JOUR\nTI  - Bare Same Title\nER  -\nTY  - JOUR\nTI  - Bare Same Title\nER  -\n"
        with tempfile.TemporaryDirectory() as tmp:
            articles = parse_ris(_write(tmp, "bare.ris", bare))
            db_path = str(Path(tmp) / "t2.db")
            db_mod.init_db(db_path)
            summary = db_mod.upsert_articles(db_path, articles, [])
            rows = db_mod.list_articles(db_path, include_duplicates=True)
        self.assertEqual((summary.n_new, summary.n_existing), (1, 1))
        self.assertEqual(len(rows), 1)

    def test_legacy_2_1_3_auto_key_reimport_idempotent(self):
        # R6-D2 落地后的 R1 验收（原 skip 转正）：按 2.1.3 公式预置
        # auto-<sha1(doi|title)[:12]> 行（无 ID/无 DOI，Metformin/Wang, Wei/
        # 2005 复审场景）后重导同一 RIS——stabilize 身份匹配（content_hash＋
        # 首作者门）沿用库内旧键，零新增、主键不重写。修复前：「新增 1 条」
        # （DB 2 行同标题、is_duplicate_of 均 NULL，复审实测）。
        import hashlib
        import sqlite3
        from argparse import Namespace
        from contextlib import closing, redirect_stdout
        from coscreen.models import Article

        import coscreen.cli as cli
        import coscreen.db as db_mod

        body = "TY  - JOUR\nTI  - Metformin for prevention of diabetes\nAU  - Wang, Wei\nPY  - 2005\nER  -\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "metformin.ris", body)
            db_path = str(Path(tmp) / "legacy.db")
            db_mod.init_db(db_path)
            base = "auto-" + hashlib.sha1("|Metformin for prevention of diabetes".encode()).hexdigest()[:12]
            db_mod.upsert_articles(
                db_path,
                [Article(zotero_key=base, title="Metformin for prevention of diabetes",
                         authors="Wang, Wei", year=2005, raw={})],
                [],
            )
            out_buf = StringIO()
            with redirect_stderr(StringIO()), redirect_stdout(out_buf):
                code = cli.cmd_import(
                    Namespace(file=str(path), db=db_path, screener=None,
                              task=None, auto_merge_fuzzy=False)
                )
            self.assertEqual(code, 0)
            self.assertIn("新增 0 条", out_buf.getvalue())
            with closing(sqlite3.connect(db_path)) as conn:
                rows = conn.execute(
                    "SELECT zotero_key, is_duplicate_of FROM articles").fetchall()
        self.assertEqual(rows, [(base, None)])  # 沿用旧键，仍 1 行


class V213PresetReimportTests(unittest.TestCase):
    """r2/r3 评审 R1 验收：2.1.3 形态预置库（旧 base + 旧 id + 后缀行）重导。

    预置行按 v2.1.3 出货代码手工构造：
    - base 键：v2.1.3 normalize.py build_zotero_key 的
      "auto-" + sha1(doi|title)[:12]（r3 评审：不得用当前公式 base 冒充）；
    - id：git show v2.1.3:coscreen/parsers/ris_parser.py:96-97 的
      f"{sha256(文件字节).hexdigest()}:{record_no}"；
    - Web 后缀：v2.1.3 custom_backend/main.py:1425 的
      sha256(fallback_id)[:12]；
    - CLI 后缀：coscreen/dedup.py _conflict_suffix_key 的
      sha256(f"{key}|{fallback_id}")[:12]。

    R6-D2 落地（集成轮）：真实 2.1.3 base 下 CLI 与 Web 两路均零新增
    ——稳定层身份匹配（_record_id/_fallback_id → content_hash＋首作者门
    → 标题+姓氏+名+年份）沿用库内既有键，主键不重写（R1 铁律验收）。
    """

    BODY = (
        "TY  - JOUR\nTI  - Same Topic\nAU  - Smith, John\nPY  - 2020\nER  -\n"
        "TY  - JOUR\nTI  - Same Topic\nAU  - Smith, Jane\nPY  - 2020\nER  -\n"
    )

    @staticmethod
    def _v2_1_3_base(doi: str, title: str) -> str:
        """v2.1.3 的 auto 基键公式："auto-" + sha1(doi|title)[:12]。"""
        import hashlib

        return "auto-" + hashlib.sha1(f"{doi}|{title}".encode("utf-8")).hexdigest()[:12]

    @classmethod
    def _preset(cls, articles, digest: str, suffix_fn, base: str) -> list:
        preset = []
        for art, fbid in zip(articles, (f"{digest}:1", f"{digest}:2")):
            row = type(art)(**art.__dict__)
            row.raw = dict(art.raw)
            row.raw["_fallback_id"] = fbid
            row.raw["_fallback_key"] = base
            row.zotero_key = base if fbid.endswith(":1") else f"{base}-{suffix_fn(fbid)}"
            preset.append(row)
        return preset

    def test_web_stabilize_reuses_true_v2_1_3_preset_keys(self):
        # r3 评审：预置用真实 2.1.3 base（auto-sha1(doi|title)[:12]）+ 旧
        # id + main.py 公式后缀；Web 路径按 _fallback_id 匹配复用预置键，
        # 零新增（与当前 base 公式无关）
        import hashlib

        import coscreen.db as db_mod

        _stabilize_generated_import_keys = _backend_main()._stabilize_generated_import_keys

        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "smith.ris", self.BODY)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            articles = parse_ris(path)
            base = self._v2_1_3_base(articles[0].doi, articles[0].title)
            web_suffix = (
                lambda fbid: hashlib.sha256(fbid.encode("utf-8")).hexdigest()[:12]
            )
            preset = self._preset(articles, digest, web_suffix, base)
            db_path = str(Path(tmp) / "web.db")
            db_mod.init_db(db_path)
            db_mod.upsert_articles(db_path, preset, [])
            incoming = parse_ris(path)
            _stabilize_generated_import_keys(
                db_mod.list_articles(db_path, include_duplicates=True), incoming
            )
            self.assertEqual(
                [a.zotero_key for a in incoming], [a.zotero_key for a in preset]
            )
            summary = db_mod.upsert_articles(db_path, incoming, [])
            rows = db_mod.list_articles(db_path, include_duplicates=True)
        self.assertEqual(summary.n_new, 0)
        self.assertEqual(len(rows), 2)

    def test_cli_reimport_current_formula_preset_zero_new_rows(self):
        # 基线钉（当前 r5 后 auto 公式派生的 base 键 + 旧形态 id + dedup
        # 公式后缀）：CLI 同序管道重导零新增。注意这不是 2.1.3 base——
        # 真实 2.1.3 base 的表现见下一条（R6-D2 现状钉）
        import hashlib
        import sqlite3
        from argparse import Namespace
        from contextlib import closing, redirect_stdout

        import coscreen.cli as cli
        import coscreen.db as db_mod

        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "smith.ris", self.BODY)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            articles = parse_ris(path)
            base = articles[0].zotero_key  # 当前公式 base（非 2.1.3）
            dedup_suffix = (
                lambda fbid: hashlib.sha256(f"{base}|{fbid}".encode("utf-8")).hexdigest()[:12]
            )
            preset = self._preset(articles, digest, dedup_suffix, base)
            db_path = str(Path(tmp) / "cli.db")
            db_mod.init_db(db_path)
            db_mod.upsert_articles(db_path, preset, [])
            with redirect_stderr(StringIO()), redirect_stdout(StringIO()):
                code = cli.cmd_import(
                    Namespace(file=str(path), db=db_path, screener=None,
                              task=None, auto_merge_fuzzy=False)
                )
            self.assertEqual(code, 0)
            with closing(sqlite3.connect(db_path)) as conn:
                count = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        self.assertEqual(count, 2)  # 零新增：仍是预置的 2 行

    def test_cli_reimport_true_v2_1_3_base_zero_new_rows(self):
        # R6-D2 落地后的 R1 验收（原现状钉转正）：预置真实 2.1.3 base
        # （auto-sha1("|Same Topic")[:12] = auto-ad1e0e62dc47）+ 旧 id +
        # dedup 公式后缀后重导——CLI 稳定层身份匹配（_record_id/_fallback_id
        # → content_hash＋首作者门）命中旧行沿用库内键，零新增、旧主键
        # 不重写。修复前：「新增 2 条」（旧 2 行 + 新 base 2 行，r3 评审
        # 实测现状）
        import hashlib
        import sqlite3
        from argparse import Namespace
        from contextlib import closing, redirect_stdout

        import coscreen.cli as cli
        import coscreen.db as db_mod

        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "smith.ris", self.BODY)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            articles = parse_ris(path)
            base = self._v2_1_3_base(articles[0].doi, articles[0].title)
            dedup_suffix = (
                lambda fbid: hashlib.sha256(f"{base}|{fbid}".encode("utf-8")).hexdigest()[:12]
            )
            preset = self._preset(articles, digest, dedup_suffix, base)
            db_path = str(Path(tmp) / "cli213.db")
            db_mod.init_db(db_path)
            db_mod.upsert_articles(db_path, preset, [])
            out_buf = StringIO()
            with redirect_stderr(StringIO()), redirect_stdout(out_buf):
                code = cli.cmd_import(
                    Namespace(file=str(path), db=db_path, screener=None,
                              task=None, auto_merge_fuzzy=False)
                )
            self.assertEqual(code, 0)
            self.assertIn("新增 0 条", out_buf.getvalue())
            with closing(sqlite3.connect(db_path)) as conn:
                keys = [r[0] for r in conn.execute("SELECT zotero_key FROM articles").fetchall()]
        # R1 验收：仍是预置的 2 行、键即 2.1.3 base 及其后缀行（主键未重写）
        self.assertEqual(len(keys), 2)
        self.assertTrue(all(k.startswith("auto-ad1e") for k in keys), keys)


if __name__ == "__main__":
    unittest.main()
