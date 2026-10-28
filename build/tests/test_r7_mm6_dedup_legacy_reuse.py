"""r7-MM6 修复泳道回归钉：MM6-04 截断不得打断「重导沿用既往消歧行」链。

外部复审（minimax_findings_verification_6.md MM6-04，deepseek 复现 confirmed）：
修复前（G-02 之前）的消歧键公式是 ``base + "-" + sha256(base|content)[:12]``
（不截断）；base 长 300 时得到 313 字符键。G-02 截断后候选键变为
``base[:287] + "-" + suffix``（300 字符）≠ 库内 313 旧键，
``known_by_key.get(候选)`` 命中失败 → 重导同一文件走**插入**路径：
真实 CLI ``python -m coscreen import`` 重导同一 CSV「新增 1 条」，
同一文献出现 313 死锁行 + 300 新行两行（probe_import_cli_legacy 实测）。

钉子（破坏即红）：
- 单元：``_disambiguation_key`` 先按**历史公式（完整 base 拼接）**生成候选
  探测库内既往消歧行，命中且同一文献则沿用 313 旧键（修前返回截断 300 键
  → 红）；未命中时仍返回截断键 ≤300（G-02 行为不变，恒绿）；
- probe_dedup 场景：库内预置 A（300 基键）+ B（313 历史消歧键），真实
  find_duplicates + stabilize_conflicting_keys + upsert_articles 重导同一批 →
  B 沿用 313 键走更新路径，n_new=0、行数仍 2（修前 n_new=1、行数 3 → 红）；
- probe_import_cli_legacy 场景：预置 313 旧键后真实 CLI 重导同一 CSV
  「新增 0 条」、行数不变（修前「新增 1 条」→ 红）；
- 恒绿守护：短键（≤287）管线形态与旧公式逐字节一致（沿用 G-02 的守护
  口径），base=287 边界两侧行为分界不漂移。
"""

from __future__ import annotations

import hashlib
import io
import re
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout, redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen import dedup as dedup_mod
from coscreen.dedup import (
    _disambiguation_key,
    _stable_content_id,
    find_duplicates,
    stabilize_conflicting_keys,
)
from coscreen.models import Article

KEY_MAX = 300


def _article(key: str, title: str, authors: str = "", doi: str = "",
             year: int | None = None) -> Article:
    return Article(zotero_key=key, title=title, authors=authors, doi=doi,
                   year=year, raw={})


def _legacy_suffix(key: str, art: Article) -> str:
    """历史公式后缀（raw={} 时内容标识为文档化回退形态，见 G-02 测试同法）。"""
    return hashlib.sha256(f"{key}|{_stable_content_id(art)}".encode("utf-8")).hexdigest()[:12]


def _run_cli(args: list[str]):
    """同进程直调 CLI 主入口（与 test_dedup_key_len_g02 同一手法）。"""
    from coscreen import cli as cli_module

    buf_out, buf_err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf_out), redirect_stderr(buf_err):
        try:
            code = cli_module.main(list(args))
        except SystemExit as exc:
            code = int(exc.code or 0)
    import subprocess

    return subprocess.CompletedProcess(args, int(code), buf_out.getvalue(), buf_err.getvalue())


def _write_csv(path: Path, key: str, title: str, doi: str) -> None:
    path.write_text(
        "Key,Title,Author,DOI\n"
        f'{key},"{title}","Kim, Lee",{doi}\n',
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 单元钉子：历史公式候选优先，命中库内既往行则沿用
# ---------------------------------------------------------------------------
class LegacyCandidateFirstTests(unittest.TestCase):
    def test_legacy_full_base_candidate_hits_known_row(self):
        # 库内持修复前 313 键（同一文献）：截断前先按历史公式 base+suffix
        # 探测，命中即沿用（修前返回截断 300 键 → 红）。
        base = "K" * 300
        art = _article(base, "Shared Key Article B", authors="Author, B",
                       doi="10.1/b", year=2025)
        legacy = f"{base}-{_legacy_suffix(base, art)}"
        self.assertEqual(len(legacy), 313)
        got = _disambiguation_key(base, art, {base}, {legacy: art})
        self.assertEqual(got, legacy)

    def test_legacy_miss_still_returns_truncated_key(self):
        # 未命中库内既往行：仍用截断键 ≤300（G-02 行为不变，恒绿守护）。
        base = "K" * 300
        art = _article(base, "Shared Key Article B", authors="Author, B",
                       doi="10.1/b", year=2025)
        legacy = f"{base}-{_legacy_suffix(base, art)}"
        got = _disambiguation_key(base, art, {base}, {})
        self.assertNotEqual(got, legacy)
        self.assertLessEqual(len(got), KEY_MAX)
        self.assertRegex(got, r"-[0-9a-f]{12}$")

    def test_short_key_pipeline_byte_identical(self):
        # 恒绿守护：≤287 的键历史公式与现行公式逐字节一致，沿用路径不变。
        for base in ("MM6S", "K" * 287):
            art = _article(base, "Short key study", authors="Wang, Wei",
                           doi="10.9/x", year=2005)
            legacy = f"{base}-{_legacy_suffix(base, art)}"
            self.assertLessEqual(len(legacy), KEY_MAX)
            got = _disambiguation_key(base, art, {base}, {legacy: art})
            self.assertEqual(got, legacy)


# ---------------------------------------------------------------------------
# probe_dedup 场景：预置 313 旧键，重导同一批走更新路径（零新增行）
# ---------------------------------------------------------------------------
class LegacyKeyReimportDedupTests(unittest.TestCase):
    def test_reimport_legacy_313_key_reuses_row(self):
        from coscreen import db as db_mod

        base = "K" * 300
        art_a = _article(base, "Shared Key Article A", authors="Author, A",
                         doi="10.1/a", year=2024)
        art_b = _article(base, "Shared Key Article B", authors="Author, B",
                         doi="10.1/b", year=2025)
        legacy_b = f"{base}-{_legacy_suffix(base, art_b)}"
        self.assertEqual(len(legacy_b), 313)
        with tempfile.TemporaryDirectory(prefix="rf_mm6_dedup_") as tmp:
            db = str(Path(tmp) / "library_legacy.db")
            db_mod.init_db(db)
            # 预置「修复前已导入」形态：A 持基键、B 持历史公式 313 消歧键
            db_mod.upsert_articles(
                db,
                [_article(base, art_a.title, art_a.authors, art_a.doi, art_a.year),
                 _article(legacy_b, art_b.title, art_b.authors, art_b.doi, art_b.year)],
                [],
            )
            self.assertEqual(len(db_mod.list_articles(db, include_duplicates=True)), 2)

            # 重导同一批 A/B（基键形态），真实去重 + 键消歧 + 入库管线
            known = db_mod.list_articles(db, include_duplicates=True)
            combined = known + [art_a, art_b]
            _, report = find_duplicates(combined, include_fuzzy=False)
            warns: list[str] = []
            stabilize_conflicting_keys(
                combined,
                report.exact_pairs + report.fuzzy_pairs + report.key_conflict_pairs,
                known=known, warnings=warns,
            )
            # 与 cmd_import 同口径：只入库本批（combined 仅参与去重/消歧视图）
            summary = db_mod.upsert_articles(
                db, [art_a, art_b],
                [p for p in report.exact_pairs + report.fuzzy_pairs
                 + report.key_conflict_pairs if p.kept_key != p.dup_key],
            )
            # 修前：截断候选键 ≠ 313 旧键 → 插入新行 n_new=1（红）
            self.assertEqual(summary.n_new, 0,
                             f"重导不得新增行: n_new={summary.n_new}, warns={warns}")
            self.assertEqual(summary.n_existing, 2)
            arts = db_mod.list_articles(db, include_duplicates=True)
            self.assertEqual(len(arts), 2)  # 修前 = 3（红）
            self.assertTrue(any(a.zotero_key == legacy_b for a in arts),
                            "B 应沿用 313 历史消歧键（更新路径）")


# ---------------------------------------------------------------------------
# probe_import_cli_legacy 场景：真实 CLI 重导同一 CSV「新增 0 条」
# ---------------------------------------------------------------------------
class LegacyKeyReimportCliTests(unittest.TestCase):
    def test_cli_reimport_after_legacy_prewrite_adds_zero(self):
        from coscreen import db as db_mod

        base = "K" * 300
        with tempfile.TemporaryDirectory(prefix="rf_mm6_cli_") as tmp:
            tmp = Path(tmp)
            db = str(tmp / "library_legacy.db")
            csv_a, csv_b = tmp / "file_a.csv", tmp / "file_b.csv"
            _write_csv(csv_a, base, "Shared Key Article A", "10.1/a")
            _write_csv(csv_b, base, "Shared Key Article B", "10.1/b")
            # 现行管线先导入，得到「修复后形态」（B 持截断 300 键）
            for csv in (csv_a, csv_b):
                proc = _run_cli(["import", "--file", str(csv), "--db", db])
                self.assertEqual(proc.returncode, 0, proc.stderr)
            # 把 B 的键改写回修复前的 313 历史消歧键（模拟修复前已导入的库）。
            # 后缀必须按真实 CSV 管线产出的 Article 计算（parsers 会写入
            # raw["_record_id"]，内容标识与 raw={} 回退形态不同）。
            from coscreen.parsers import parse_file
            parsed_b = [a for a in parse_file(str(csv_b))
                        if "Article B" in (a.title or "")]
            self.assertEqual(len(parsed_b), 1)
            legacy = f"{base}-{_legacy_suffix(base, parsed_b[0])}"
            self.assertEqual(len(legacy), 313)
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.execute("PRAGMA foreign_keys=OFF")
                arts = conn.execute(
                    "SELECT zotero_key, title FROM articles ORDER BY title"
                ).fetchall()
                self.assertEqual(len(arts), 2)
                ghost_row = next(k for k, t in arts if "Article B" in t)
                conn.execute("UPDATE articles SET zotero_key=? WHERE zotero_key=?",
                             (legacy, ghost_row))
            keys = [a.zotero_key for a in db_mod.list_articles(db, include_duplicates=True)]
            self.assertTrue(any(len(k) == 313 for k in keys), f"预置失败: {keys}")

            # 重导同一 CSV：修前「新增 1 条」（截断候选未命中 313 旧键）→ 红
            proc = _run_cli(["import", "--file", str(csv_b), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("新增 0 条", proc.stdout, proc.stdout)
            arts = db_mod.list_articles(db, include_duplicates=True)
            self.assertEqual(len(arts), 2, f"重导不得增加行数: {arts}")
            b_rows = [a for a in arts if "Article B" in (a.title or "")]
            self.assertEqual(len(b_rows), 1, f"同一文献不得出现两行: {arts}")


if __name__ == "__main__":
    unittest.main()
