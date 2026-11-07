"""r8b FL2 钉子 —— #17/#18/#19：编码双候选择优与重复表头列。

复审复现口径（out/external_reviews/r6_verify_dedup.md #17/#18/#19，探针
`17`/`18`/`19`，真实 decode_bytes_with_confidence/parse_csv/parse_ris）：

- #17：cp1252 字节 '©The Author(s) 2020'→'㏕he Author(s) 2020'、
  '©Springer-Verlag Berlin'→'㏒pringer…'、'©Oxford…'→'㎡xford…'，仅
  「已按 GBK 解码」警告；'© Springer'（带空格）才正确选 cp1252。
- #18：表头 Key,Title,DOI,DOI + 值在第二个 DOI 列 → doi=''、0 警告；
  值在第一列正常。
- #19：20 条英文 + 1 条中文（臟/王伟/中华医学杂志）的 GBK RIS 因单个
  common 标记（臟=GBK C54B）被整文翻成 cp1252，标题解成 'ÅK' 等乱码。

修复：©/®/°+ASCII 字节对计入 cp1252 判定证据；common 标记翻转需占 CJK
主体且无 GB2312 一级常用字（守卫）；重复表头列按出现序取首个非空值并
显式警告。撤修复必红。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from io import StringIO
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen.parsers.csv_parser import decode_bytes_with_confidence, parse_csv
from coscreen.parsers.ris_parser import parse_ris


_REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_cli(args: list[str]):
    """真实子进程 `python -B -m coscreen ...`（复审同款探针形态）。

    评审 R2：钉子此前是进程内 ``cli_module.main`` + redirect_stdout，与
    r6 复审的 ``python -B -m coscreen`` 子进程形态不同（子进程才能覆盖
    stdout/stderr 编码、退出码与 __main__ 入口）。cwd=仓库根、UTF-8 输出。
    """
    import os
    import subprocess

    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-B", "-m", "coscreen", *[str(a) for a in args]],
        cwd=str(_REPO_ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return proc


def _decode(text: str, encoding: str):
    return decode_bytes_with_confidence(text.encode(encoding), "probe.txt")


def _decode_in_file(title: str, encoding: str = "cp1252"):
    """把题录放进真实形态的 CSV 正文再解码。

    评审的 #17 用例是**整份导出文件**里的一个标题；裸标题串（如 '©Über'）
    的非 ASCII 占比 40% 会先被既有的 ``_MAX_CP1252_NON_ASCII_RATE=0.2``
    可行性门拒掉——那是另一个既有守卫，不是 #17 的判据。用真实文件正文
    （ASCII 行占主体）才能单独检验词首证据本身。
    """
    body = (
        "Key,Title,DOI,Publication Year,Author\n"
        "A1,First English study,10.1/a,2019,Doe\n"
        "A2,Second English study,10.1/b,2020,Roe\n"
        f'M1,"{title}",10.1/muller,2021,Mueller\n'
        "A3,Third English study,10.1/c,2022,Poe\n"
    )
    return decode_bytes_with_confidence(body.encode(encoding), "probe.csv")


class Cp1252SymbolPrefixTests(unittest.TestCase):
    """#17：©/®/° 前缀标题不再被 GBK 误解。撤修复必红。"""

    def test_copyright_prefix_titles_decode_as_cp1252(self):
        for text in ("©The Author(s) 2020", "©Springer-Verlag Berlin",
                     "©Oxford University Press"):
            with self.subTest(text=text):
                decoded, warnings = _decode(text, "cp1252")
                self.assertEqual(decoded, text,
                                 f"cp1252 标题被 GBK 误解: {decoded!r}")
                self.assertTrue(warnings, "非 UTF-8 解码必须有告警")

    def test_space_after_symbol_still_cp1252(self):
        decoded, _ = _decode("© Springer", "cp1252")
        self.assertEqual(decoded, "© Springer")

    def test_accented_word_after_copyright_symbol_decodes_cp1252(self):
        # 评审第 3 轮残留 high 原样回放：第 3 轮判据要求 © 后**两个字节都是
        # ASCII 字母**，于是「© + 重音/变音符开头的词」整类落回 GBK
        # （©Müller -> '㎝黮ler'、©Éditions -> '┥ditions'），跨版本重导
        # 新增重复行。修法：符号后「字母+高位重音字母」或「高位重音字母+
        # 字母」同样计 cp1252 词首证据。撤修复（回到两字节都须 ASCII 字母）
        # 此用例必红。
        for text in ("©Müller, Hans", "©Éditions du Seuil",
                     "©Ökologie der Stadt", "©Åke Svensson",
                     "©Øresund", "©Ça va", "®Müller GmbH",
                     "©Größe der Stichprobe", "©Über", "©Émile"):
            with self.subTest(text=text):
                decoded, warnings = _decode_in_file(text)
                self.assertIn(f'"{text}"', decoded,
                              f"© + 重音词被整类翻成 GBK: "
                              f"{decoded.splitlines()[3]!r}")
                self.assertTrue(warnings, "非 UTF-8 解码必须有告警")

    def test_symbol_original_ascii_pair_rule_not_regressed(self):
        # 第 3 轮原判据（ASCII 字母 + ASCII 字母）不得回归——评审原话
        # 「同时保住 ©Springer 类原判据不回归」。
        for text in ("©The Author(s) 2020", "©Springer-Verlag Berlin",
                     "©Oxford University Press", "©MILLER", "©the author"):
            with self.subTest(text=text):
                decoded, _ = _decode_in_file(text)
                self.assertIn(f'"{text}"', decoded)

    def test_accented_copyright_title_reimport_has_no_duplicate_row(self):
        # 跨版本（评审实测口径）：2.1.3 已按 cp1252 正确入库的 ©Müller 题录，
        # 用真实 CLI 子进程重导同一文件必须原地更新（新增 0 条），不得新增
        # 第二行（修复前 "新增 1 条" + 库内出现 '㎝黮ler, Hans'）。
        import sqlite3
        from contextlib import closing

        from coscreen import db as db_mod
        from coscreen.models import Article, compute_content_hash

        with tempfile.TemporaryDirectory(prefix="rf_r8b17ac_") as tmp:
            tmp = Path(tmp)
            csv = tmp / "accented.csv"
            csv.write_bytes(
                'Key,Title,DOI\nM1,"©Müller, Hans",10.1/m\n'.encode("cp1252"))
            db = str(tmp / "t.db")
            db_mod.init_db(db)
            title = "©Müller, Hans"
            db_mod.upsert_articles(db, [Article(
                zotero_key="M1", title=title, doi="10.1/m",
                content_hash=compute_content_hash("10.1/m", title))], [])
            proc = _run_cli(["import", "--file", str(csv), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("新增 0 条", proc.stdout,
                          "© + 重音词文件重导必须原地更新（修复前新增 1 行）")
            with closing(sqlite3.connect(db)) as conn:
                rows = conn.execute(
                    "SELECT zotero_key, title FROM articles").fetchall()
            self.assertEqual([r[0] for r in rows], ["M1"])
            self.assertEqual(rows[0][1], title, "标题不得为 GBK 乱码")

    def test_gbk_unit_symbol_before_ascii_letter_stays_gbk(self):
        # 同行判据的另一侧：GBK 的 ㎡/㎏/㎜ 后紧接 ASCII 字母（'5㎡pH'、
        # '25㎜Hg'）在字节形态上与 '@ + 两个字母' 无法区分，靠「单位符号
        # 前是数字」排除左侧边界。旧判据曾把 '5㎡pH' 翻成 '5©OpH'。
        # 撤左侧边界（改回只看右侧两字节）此用例必红。
        for text in ("5㎡pH", "20㎡pH", "5㎡AB", "5㎝AB", "25㎜Hg",
                     "40㎎kg", "5㎝a"):
            with self.subTest(text=text):
                decoded, _ = decode_bytes_with_confidence(
                    text.encode("gbk"), "probe.csv")
                self.assertEqual(decoded, text,
                                 f"GBK 单位符号被误翻成 cp1252: {decoded!r}")

    def test_real_gbk_symbol_char_not_flipped(self):
        # 防线：真实 GBK 文本里的全角/兼容符号（如 ㎡ 经自身 GBK 编码出现，
        # 第二字节非 ASCII）不得因 #17 证据被翻成 cp1252。
        decoded, _ = decode_bytes_with_confidence(
            "面积 20㎡".encode("gbk"), "probe.txt")
        self.assertEqual(decoded, "面积 20㎡")

    def test_gbk_western_records_keep_greek_and_math_symbols(self):
        # 评审 R2·F2（high）：r8b 第 1 轮曾用「GBK 候选 cjk==0 即选 cp1252」
        # 的捷径，把 GBK 编码的**纯西文**题录整篇翻成 cp1252（± → ¡À、
        # ° → ¡ã、α → ¦Á、· → ¡¤、㎡ → ©O），并让 2.1.3 库重导读成新行。
        # 该捷径已删除；撤删除（恢复 cjk==0 分支）此用例必红。
        for text in ("blood pressure (mean ± SD)", "stored at 25°C",
                     "α-glucosidase activity", "dose 5·10 mg",
                     "β-blocker trial", "area 20㎡"):
            with self.subTest(text=text):
                decoded, _ = decode_bytes_with_confidence(
                    text.encode("gbk"), "probe.csv")
                self.assertEqual(decoded, text,
                                 f"GBK 西文题录被误翻成 cp1252: {decoded!r}")

    def test_gbk_unit_symbols_after_space_stay_gbk(self):
        # 评审 R3·F2（high）：GBK 的 ㎡/㎎/㎜/㎝ 双字节恰为 0xA9+字母，第 2 轮
        # 的「词首/前导数字」启发式让「空格 + ㎡」整类翻成 cp1252
        # （area ㎡→area ©O、dose ㎎→dose ©J）。现按「A9 + 两个 ASCII 字母」
        # 判别：这些单位符号后跟空格/括号/结尾，不构成证据。
        # 撤判据（回到位置启发式）此用例必红。
        for text in ("area ㎡ used", "body surface area ㎡ per patient",
                     "area (㎡) of 1.7", "dose ㎎ used", "length ㎜",
                     "volume ㎝", "area 20㎡", "log ㏒ value"):
            with self.subTest(text=text):
                decoded, _ = decode_bytes_with_confidence(
                    text.encode("gbk"), "probe.csv")
                self.assertEqual(decoded, text,
                                 f"GBK 单位符号被误翻成 cp1252: {decoded!r}")

    def test_unit_symbol_csv_reimport_idempotent(self):
        # 跨版本钉（评审要求的形式，样本改用「空格 ㎡」）：按正确 GBK 解码
        # 预置库（模拟 2.1.3 已正确入库）后重导同一文件 → 0 新增、单行。
        import sqlite3
        from contextlib import closing

        from coscreen import db as db_mod
        from coscreen.models import Article, compute_content_hash

        with tempfile.TemporaryDirectory(prefix="rf_r8b17u_") as tmp:
            tmp = Path(tmp)
            csv = tmp / "unit.csv"
            csv.write_text(
                'Key,Title,DOI\nS1,"body surface area ㎡ per patient",10.1/s\n',
                encoding="gbk")
            db = str(tmp / "t.db")
            db_mod.init_db(db)
            decoded, _ = decode_bytes_with_confidence(csv.read_bytes(), csv.name)
            import csv as _csv
            import io as _io
            title = list(_csv.reader(_io.StringIO(decoded)))[1][1]
            self.assertIn("㎡", title)  # 正确 GBK 解码
            db_mod.upsert_articles(db, [Article(
                zotero_key="S1", title=title, doi="10.1/s",
                content_hash=compute_content_hash("10.1/s", title))], [])
            proc = _run_cli(["import", "--file", str(csv), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("新增 0 条", proc.stdout,
                          "GBK 单位符号文件重导必须原地更新（第 2 轮曾新增 1 行）")
            with closing(sqlite3.connect(db)) as conn:
                rows = conn.execute("SELECT zotero_key FROM articles").fetchall()
            self.assertEqual(rows, [("S1",)])

    def test_gbk_western_csv_reimport_is_idempotent_across_versions(self):
        # 跨版本（评审 R2 实测口径）：先用「正确 GBK 解码」的库重导同一 GBK
        # 文件，必须原地更新（0 新增），而不是新增第二行 + 键冲突候选。
        import sqlite3
        from contextlib import closing

        from coscreen.models import Article, compute_content_hash

        with tempfile.TemporaryDirectory(prefix="rf_r8b17r_") as tmp:
            tmp = Path(tmp)
            csv = tmp / "western.csv"
            csv.write_text(
                'Key,Title,DOI\nG1,"blood pressure (mean ± SD)",10.1/g\n',
                encoding="gbk")
            db = str(tmp / "t.db")
            from coscreen import db as db_mod

            db_mod.init_db(db)
            decoded, _ = decode_bytes_with_confidence(csv.read_bytes(), csv.name)
            import csv as _csv, io as _io
            title = list(_csv.reader(_io.StringIO(decoded)))[1][1]
            self.assertEqual(title, "blood pressure (mean ± SD)")  # 基线口径
            db_mod.upsert_articles(db, [Article(
                zotero_key="G1", title=title, doi="10.1/g",
                content_hash=compute_content_hash("10.1/g", title))], [])
            proc = _run_cli(["import", "--file", str(csv), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("新增 0 条", proc.stdout,
                          "同一 GBK 文件重导必须原地更新（修复前新增 1 条）")
            with closing(sqlite3.connect(db)) as conn:
                rows = conn.execute("SELECT zotero_key, title FROM articles").fetchall()
            # 标题尾部 ")" 由既有的 normalize_title 尾标点规则剥离（基线同款），
            # 此处只断言无重复行、无 cp1252 乱码。
            self.assertEqual([r[0] for r in rows], ["G1"])
            self.assertIn("±", rows[0][1])
            self.assertNotIn("¡", rows[0][1])


class DilutedGbkGuardTests(unittest.TestCase):
    """#19：单个 common 标记不把稀释 GBK 真文件翻成 cp1252。撤修复必红。"""

    def _diluted_gbk_file(self, tmp: Path) -> Path:
        records = []
        for i in range(20):
            records.append(
                f"TY  - JOUR\nID  - E{i}\nTI  - English study number {i}\n"
                "AU  - Doe, Jane\nPY  - 2019\nER  -\n\n")
        records.append(
            "TY  - JOUR\nID  - C1\nTI  - 臟\nAU  - 王伟\nPY  - 2019\n"
            "JO  - 中华医学杂志\nER  -\n\n")
        ris = tmp / "diluted.ris"
        ris.write_bytes("".join(records).encode("gbk"))
        return ris

    def test_diluted_gbk_ris_keeps_chinese_metadata(self):
        with tempfile.TemporaryDirectory(prefix="rf_r8b19_") as tmp:
            ris = self._diluted_gbk_file(Path(tmp))
            buf = StringIO()
            with redirect_stderr(buf):
                arts = parse_ris(ris)
            self.assertEqual(len(arts), 21)
            chinese = [a for a in arts if a.zotero_key == "C1"][0]
            self.assertEqual(chinese.title, "臟",
                             f"修复前被翻成 cp1252 乱码: {chinese.title!r}")
            self.assertEqual(chinese.authors, "王伟")
            self.assertEqual(chinese.journal, "中华医学杂志")

    def test_level1_guard_blocks_flip_with_multiple_markers(self):
        # 评审 R2·F5（medium）：5928 个 mojibake 标记中 `_is_gb2312_level1`
        # 命中为 0，守卫只在「标记 + 非标记 CJK 混排且标记占多数」时可判别。
        # 夹具（侫侭×3 + 病：markers=6 / cjk=7，满足 evidence*2>cjk）在
        # **删除守卫**时整篇翻成 cp1252（'‚A‚B…'），保留守卫时为 GBK；
        # 已用副本树定点删除验证过红/绿。
        text = ("TY  - JOUR\nTI  - 侫侭侫侭侫侭病\nAU  - 王伟\n"
                "PY  - 2019\nER  -\n\n")
        decoded, warnings = decode_bytes_with_confidence(
            text.encode("gbk"), "probe.ris")
        self.assertEqual(decoded, text,
                         "含 GB2312 一级常用字的 GBK 文本不得翻成 cp1252: "
                         f"{decoded[:40]!r}")
        self.assertIn("GBK", warnings[0])


class DuplicateHeaderColumnTests(unittest.TestCase):
    """#18：重复表头后列的值不再被静默丢弃。撤修复必红。"""

    def test_value_in_second_doi_column_recovered(self):
        with tempfile.TemporaryDirectory(prefix="rf_r8b18_") as tmp:
            csv = Path(tmp) / "dup.csv"
            csv.write_text("Key,Title,DOI,DOI\nK1,Title,,10.1/x\n",
                           encoding="utf-8")
            buf = StringIO()
            with redirect_stderr(buf):
                arts = parse_csv(csv)
            self.assertEqual(arts[0].doi, "10.1/x",
                             "值在后出现的重复 DOI 列不得被静默丢弃")
            self.assertIn("重复的 doi 列", buf.getvalue(), "必须显式警告")

    def test_value_in_first_doi_column_unchanged(self):
        with tempfile.TemporaryDirectory(prefix="rf_r8b18b_") as tmp:
            csv = Path(tmp) / "dup.csv"
            csv.write_text("Key,Title,DOI,DOI\nK1,Title,10.1/x,\n",
                           encoding="utf-8")
            buf = StringIO()
            with redirect_stderr(buf):
                arts = parse_csv(csv)
            self.assertEqual(arts[0].doi, "10.1/x")

    def test_first_non_empty_wins_when_both_columns_have_values(self):
        # 出现序优先：两列都有值时取第一列（确定性，不被后列改写）。
        with tempfile.TemporaryDirectory(prefix="rf_r8b18c_") as tmp:
            csv = Path(tmp) / "dup.csv"
            csv.write_text("Key,Title,DOI,DOI\nK1,Title,10.1/first,10.1/second\n",
                           encoding="utf-8")
            buf = StringIO()
            with redirect_stderr(buf):
                arts = parse_csv(csv)
            self.assertEqual(arts[0].doi, "10.1/first")


if __name__ == "__main__":
    unittest.main()
