"""r8b FL2 钉子 —— #14/#20：RIS/CSV 首见身份补全与缺 ER 告警归属。

复审复现口径（out/external_reviews/r6_verify_dedup.md #14/#20，探针
`14_*`/`20`，真实 parse_ris/parse_csv）：

- #14：三条同 ID K1 同题、作者/年依次 空/空、Smith/2020、Jones/2021 →
  修复前 parse 三条全 K1、stderr 空；CLI『导入 3 | 精确重复 2 | 进入筛选
  1 | 新增 1 更新 2』；DB 只剩 Jones, Jane 一行、0 警告。对照首条有作者
  （Brown）→ 3 行 + 两条降级警告。
- #20：TY Alpha…→TY Beta…（文件尾、全文件无 ER）→ 修复前告警②称
  「第 1 条记录（标题：Alpha real paper）…未被导入」，实际 Alpha 已被
  合成 ER 拆分导入，真正被静默丢弃的 Beta 无任何告警点名。

修复：兼容（含单侧缺元数据补全口径）保留首见键时，用后续同键记录的非空
元数据**补全**首见身份并显式警告，第 3 条起与补全身份比较、可证不同即
降级为自动键；截断告警的在途状态在嵌合 TY 处同步推进，点名真正未导入
的记录。撤修复必红。
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen.parsers.csv_parser import parse_csv
from coscreen.parsers.ris_parser import parse_ris


class _ErrIO(StringIO):
    pass


def _parse_quietly(parser, path: Path):
    buf = StringIO()
    import contextlib

    with contextlib.redirect_stderr(buf):
        arts = parser(path)
    return arts, buf.getvalue()


def _run_cli(args: list[str]):
    """真实子进程 `python -B -m coscreen ...`（复审同款探针形态，评审 R2）。"""
    import os

    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, "-B", "-m", "coscreen", *[str(a) for a in args]],
        cwd=str(Path(__file__).resolve().parents[2]), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


class FirstSeenIdentityCompletionTests(unittest.TestCase):
    """#14：首见身份用后续非空元数据补全，可证不同即降级。撤修复必红。"""

    RIS_BODY = (
        "TY  - JOUR\nID  - K1\nTI  - Same Trial\nER  -\n\n"
        "TY  - JOUR\nID  - K1\nTI  - Same Trial\nAU  - Smith, John\n"
        "PY  - 2020\nER  -\n\n"
        "TY  - JOUR\nID  - K1\nTI  - Same Trial\nAU  - Jones, Jane\n"
        "PY  - 2021\nER  -\n\n")

    CSV_BODY = ("Key,Title,Creator,Publication Year\n"
                "K1,Same Trial,,\n"
                'K1,Same Trial,"Smith, John",2020\n'
                'K1,Same Trial,"Jones, Jane",2021\n')

    def test_ris_third_record_with_different_author_demoted(self):
        with tempfile.TemporaryDirectory(prefix="rf_r8b14r_") as tmp:
            ris = Path(tmp) / "k.ris"
            ris.write_text(self.RIS_BODY, encoding="ascii")
            arts, stderr = _parse_quietly(parse_ris, ris)
            keys = [a.zotero_key for a in arts]
            # 修复前三条全 K1 且 stderr 空 → Jones 静默覆盖 Smith
            self.assertEqual(keys[0], "K1")
            self.assertEqual(keys[1], "K1")
            self.assertNotEqual(keys[2], "K1",
                                 f"第三条与补全身份（Smith/2020）可证不同，"
                                 f"必须降级为自动键: {keys}")
            self.assertIn("补全", stderr)
            self.assertIn("回退为自动生成键", stderr)

    def test_csv_third_record_with_different_author_demoted(self):
        with tempfile.TemporaryDirectory(prefix="rf_r8b14c_") as tmp:
            csv = Path(tmp) / "k.csv"
            csv.write_text(self.CSV_BODY, encoding="utf-8")
            arts, stderr = _parse_quietly(parse_csv, csv)
            keys = [a.zotero_key for a in arts]
            self.assertEqual(keys[0], "K1")
            self.assertEqual(keys[1], "K1")
            self.assertNotEqual(keys[2], "K1", f"CSV 同款: {keys}")
            self.assertIn("补全", stderr)

    def test_cli_import_preserves_both_works(self):
        # 端到端：DB 不得只剩最后一条（Jones 覆盖 Smith）。
        with tempfile.TemporaryDirectory(prefix="rf_r8b14e_") as tmp:
            tmp = Path(tmp)
            ris = tmp / "k.ris"
            ris.write_text(self.RIS_BODY, encoding="ascii")
            db = str(tmp / "t.db")
            proc = _run_cli(["import", "--file", str(ris), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            import sqlite3
            from contextlib import closing

            with closing(sqlite3.connect(db)) as conn:
                rows = conn.execute(
                    "SELECT authors FROM articles ORDER BY import_order").fetchall()
            authors = [r[0] for r in rows]
            self.assertIn("Smith, John", authors,
                          f"Smith 行不得被 Jones 静默覆盖: {authors}")
            self.assertIn("Jones, Jane", authors)
            self.assertEqual(len(rows), 2)

    def test_control_first_row_with_author_yields_three_rows(self):
        # 对照（复审 control）：首条有作者时三条互证不同 → 3 行 + 降级警告。
        with tempfile.TemporaryDirectory(prefix="rf_r8b14k_") as tmp:
            ris = Path(tmp) / "k.ris"
            ris.write_text(
                "TY  - JOUR\nID  - K1\nTI  - Same Trial\nAU  - Brown, Bob\n"
                "PY  - 2019\nER  -\n\n"
                "TY  - JOUR\nID  - K1\nTI  - Same Trial\nAU  - Smith, John\n"
                "PY  - 2020\nER  -\n\n"
                "TY  - JOUR\nID  - K1\nTI  - Same Trial\nAU  - Jones, Jane\n"
                "PY  - 2021\nER  -\n\n", encoding="ascii")
            arts, stderr = _parse_quietly(parse_ris, ris)
            self.assertEqual(len({a.zotero_key for a in arts}), 3)
            self.assertEqual(stderr.count("回退为自动生成键"), 2)


class MissingErAttributionTests(unittest.TestCase):
    """#20：截断告警点名真正未导入的记录。撤修复必红。"""

    def test_chimera_plus_truncation_warning_names_dropped_record(self):
        with tempfile.TemporaryDirectory(prefix="rf_r8b20_") as tmp:
            ris = Path(tmp) / "k.ris"
            ris.write_text(
                "TY  - JOUR\nTI  - Alpha real paper\nAU  - Smith, John\n"
                "PY  - 2020\n"
                "TY  - JOUR\nTI  - Beta lost paper\nAU  - Jones, Jane\n"
                "PY  - 2021\n", encoding="ascii")
            arts, stderr = _parse_quietly(parse_ris, ris)
            titles = [a.title for a in arts]
            self.assertEqual(titles, ["Alpha real paper"])  # Beta 被 rispy 丢弃
            # 告警必须点名 Beta（真正未导入），不得再误报 Alpha
            self.assertIn("Beta lost paper", stderr)
            drop_warnings = [line for line in stderr.splitlines()
                             if "未被导入" in line]
            self.assertEqual(len(drop_warnings), 1,
                             f"缺 ER 告警应恰一条: {drop_warnings}")
            self.assertIn("Beta lost paper", drop_warnings[0])
            self.assertNotIn("Alpha real paper", drop_warnings[0],
                             "已导入的 Alpha 不得再被报「未被导入」")


if __name__ == "__main__":
    unittest.main()
