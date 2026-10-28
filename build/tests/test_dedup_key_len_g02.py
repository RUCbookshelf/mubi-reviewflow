"""r7 补充泳道 G-02 回归：消歧键长度必须受全局 300 字符上限约束。

外部复审（gemini_findings_verification.md G-02，confirmed）：
``_disambiguation_key`` 生成 ``base_key + "-" + sha256(...)[:12]``，当来源键
长 288-300（``csv_parser.is_legal_source_key`` 承认的合法输入）时消歧键达
301-313 字符；serial 重试再追加 ``-{serial}`` 只会更长。SQLite 无长度约束
照常入库、Web 文章列表可见，但 ``custom_backend/main.py`` 的 ``_KEY_RE``
（``^[^/\\x00]{1,300}$``）与 ``DecisionIn.zotero_key``（max_length=300）会
拒绝决策（422）/全文（400）/编码（400）请求——文献成为不可推进的死锁行。

钉子（破坏即红）：
- 单元：真实 Article、base_key=295/288/300 的同键异文，生成键必须 ≤300 且
  互异；serial 重试形态同样 ≤300（修前 308/310 直接越界变红）；
- 端到端：CLI 两步导入（Key=K*295 同键异文）→ DB 全部键 ≤300，对消歧键
  POST 决策 200（修前 422 变红）；对照组短键决策 200 两态恒绿；
- R2 铁律守护：base ≤287（修前修后都合法的键）生成键与旧公式
  ``f"{key}-{suffix}"``（含 serial 形态）逐字节一致，合法输入行为不变。
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# —— 服务端端到端段环境（与 test_upload_security_regression.py 同一手法）——
# 必须在首次导入 custom_backend.main 之前设置（全套 pytest 单进程共享模块缓存）。
_fixture = tempfile.TemporaryDirectory(prefix="rf_g02_dedup_")
os.environ["COBOOKSHELF_DATA_DIR"] = _fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from coscreen import dedup as dedup_mod
from coscreen.dedup import _disambiguation_key
from coscreen.models import Article

# 全局键上限（custom_backend._KEY_RE / DecisionIn.zotero_key / is_legal_source_key 三处同口径）
KEY_MAX = 300


def _article(key: str, title: str, authors: str = "", doi: str = "",
             year: int | None = None) -> Article:
    return Article(zotero_key=key, title=title, authors=authors, doi=doi,
                   year=year, raw={})


def _legacy_suffix(key: str, art: Article) -> str:
    """旧公式后缀的独立重算（raw={} 时内容标识即文档化的
    v2.1.3 回退形态 ``doi|title|authors|year``，见 dedup._stable_content_id）。"""
    content_id = f"{art.doi}|{art.title}|{art.authors}|{art.year}"
    return hashlib.sha256(f"{key}|{content_id}".encode("utf-8")).hexdigest()[:12]


def _run_cli(args: list[str]):
    """同进程直调 CLI 主入口（与 test_dedup_u1_regressions 同一手法）。"""
    import subprocess

    from coscreen import cli as cli_module

    buf_out, buf_err = StringIO(), StringIO()
    with redirect_stdout(buf_out), redirect_stderr(buf_err):
        try:
            code = cli_module.main(list(args))
        except SystemExit as exc:
            code = int(exc.code or 0)
    return subprocess.CompletedProcess(args, int(code), buf_out.getvalue(), buf_err.getvalue())


# ---------------------------------------------------------------------------
# 单元钉子：_disambiguation_key 输出长度受 300 约束（G-02 核心）
# ---------------------------------------------------------------------------
class DisambiguationKeyLengthTests(unittest.TestCase):
    def test_long_base_key_stays_within_300(self):
        # base_key 288-300 是 is_legal_source_key 承认的合法来源键；消歧后
        # 总长必须仍 ≤300（修前 295→308 直接越界，本断言变红）。
        for length in (288, 295, 300):
            art = _article("K" * length, f"Study {length}", doi="10.1000/x")
            got = _disambiguation_key("K" * length, art, {art.zotero_key}, {})
            self.assertLessEqual(len(got), KEY_MAX,
                                 f"base={length} -> disambig key len={len(got)}")
            self.assertTrue(got.startswith("K"), got[:5])
            self.assertRegex(got, r"-[0-9a-f]{12}$")  # 前缀 + 完整 12hex 后缀

    def test_same_long_key_different_content_keys_distinct_and_bounded(self):
        # 同键异文（G-02 场景）：两条生成键互异且都 ≤300。
        key = "K" * 295
        art_a = _article(key, "Alpha study on topic one", authors="Alpha, A.",
                         doi="10.1000/alpha", year=2024)
        art_b = _article(key, "Beta study on a totally different subject",
                         authors="Beta, B.", doi="10.1000/beta", year=2025)
        k_a = _disambiguation_key(key, art_a, {key}, {})
        k_b = _disambiguation_key(key, art_b, {key, k_a}, {})
        self.assertNotEqual(k_a, k_b)
        self.assertLessEqual(len(k_a), KEY_MAX)
        self.assertLessEqual(len(k_b), KEY_MAX)

    def test_serial_retry_form_also_within_300(self):
        # serial 重试形态 ``base-suffix-{serial}`` 是最长形态，同样 ≤300
        # （修前 295 基键首候选 308、重试 310，双双越界变红）。
        key = "K" * 295
        art = _article(key, "Gamma study", doi="10.1000/gamma")
        first = _disambiguation_key(key, art, set(), {})
        retried = _disambiguation_key(key, art, {first}, {})
        self.assertLessEqual(len(first), KEY_MAX, f"first len={len(first)}")
        self.assertLessEqual(len(retried), KEY_MAX, f"retry len={len(retried)}")
        self.assertNotEqual(first, retried)
        # 重试键仍是"基键前缀 + 确定性后缀 + serial"形态（身份可追溯）
        self.assertTrue(retried.startswith("K"), retried[:5])
        self.assertRegex(retried, r"-[0-9a-f]{12}-2$")

    def test_distinct_long_keys_distinct_after_truncation(self):
        # 截断只作用于前缀：不同基键/不同内容经完整键参与的后缀哈希区分，
        # 截断后仍互异（修前亦互异——恒绿守护，防修复引入前缀坍缩）。
        art = _article("", "Delta study", doi="10.1000/delta")
        candidates = {
            _disambiguation_key("K" * 288, art, set(), {}),
            _disambiguation_key("K" * 289, art, set(), {}),
            _disambiguation_key("K" * 300, art, set(), {}),
        }
        self.assertEqual(len(candidates), 3)
        for c in candidates:
            self.assertLessEqual(len(c), KEY_MAX)

    # ------------------------------------------------------------------
    # R2 铁律守护：≤287 的合法键（修前修后生成键都 ≤300）行为逐字节不变
    # ------------------------------------------------------------------
    def test_short_key_formula_byte_identical_to_legacy(self):
        for base in ("WM1", "KEY_" + "a" * 100, "K" * 287):
            art = _article(base, "Short key study", authors="Wang, Wei",
                           doi="10.9/x", year=2005)
            # 前提：raw={} 的内容标识即文档化回退形态（后缀哈希的输入）
            self.assertEqual(
                dedup_mod._stable_content_id(art),
                f"{art.doi}|{art.title}|{art.authors}|{art.year}")
            got = _disambiguation_key(base, art, {base}, {})
            expected = f"{base}-{_legacy_suffix(base, art)}"
            self.assertEqual(got, expected)
            self.assertEqual(len(got), len(base) + 13)  # "-" + 12hex
        # 恰好 287：不截断，总长恰为 300（边界两侧行为分界点）

    def test_short_key_serial_retry_formula_unchanged(self):
        base = "WM1"
        art = _article(base, "Short key study", authors="Wang, Wei",
                       doi="10.9/x", year=2005)
        suffix = _legacy_suffix(base, art)
        first = _disambiguation_key(base, art, set(), {})
        retried = _disambiguation_key(base, art, {first}, {})
        self.assertEqual(first, f"{base}-{suffix}")
        self.assertEqual(retried, f"{base}-{suffix}-2")  # 旧公式 serial 形态

    def test_short_key_known_row_reuse_path_unchanged(self):
        # 同一文献重导：库内已有同候选键行（known_by_key 命中）→ 沿用候选键
        # 走更新路径（重导幂等），公式不受本修复影响。
        base = "WM1"
        art = _article(base, "Short key study", authors="Wang, Wei",
                       doi="10.9/x", year=2005)
        candidate = f"{base}-{_legacy_suffix(base, art)}"
        got = _disambiguation_key(base, art, {base}, {candidate: art})
        self.assertEqual(got, candidate)


# ---------------------------------------------------------------------------
# 端到端钉子：CLI 导入同键异文（base=295）→ DB 键 ≤300 → 决策接口可用
# ---------------------------------------------------------------------------
class LongKeyImportDecisionE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from custom_backend import main as backend_main
        from custom_backend import auth as auth_mod

        cls.backend_main = backend_main
        cls.client = TestClient(backend_main.app)
        cls.client.__enter__()
        # 不走 HTTP /api/auth/register：TestClient 的 client.host 共享同一
        # 注册限流桶，直接建用户+签发令牌（与 upload 安全测试同一手法）。
        user = auth_mod.create_user("g02dedup@example.test", "G02-Dedup-2026")
        cls.user = {"user_id": user["id"], "username": user["username"],
                    "token": auth_mod.create_access_token(user["id"], user["username"])}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        _fixture.cleanup()

    def auth(self):
        return {"Authorization": "Bearer " + self.user["token"]}

    def setUp(self):
        from custom_backend.main import TaskLockMiddleware
        from coscreen import tasks as tasks_mod

        TaskLockMiddleware._held.clear()
        self.task = self.client.post(
            "/api/tasks", json={"name": self._testMethodName},
            headers=self.auth()).json()["task_id"]
        self.info = tasks_mod.find_task(self.task, self.backend_main.DATA_DIR)
        self.assertIsNotNone(self.info)
        slug = tasks_mod.slugify_screener(self.user["username"])
        self.db = str(self.info.dir / f"{slug}.db")

    def tearDown(self):
        from custom_backend.main import TaskLockMiddleware
        from coscreen import tasks as tasks_mod

        TaskLockMiddleware._held.clear()
        TaskLockMiddleware._deleting.clear()
        TaskLockMiddleware._list_readers = 0
        for path in list(tasks_mod._HELD_LOCKS):
            tasks_mod.release_task_lock(path, force=True)

    @staticmethod
    def _write_csv(path: Path, key: str, title: str, doi: str) -> None:
        path.write_text(
            "Key,Title,Author,DOI\n"
            f'{key},"{title}","Kim, Lee",{doi}\n',
            encoding="utf-8",
        )

    def test_long_key_conflict_import_decision_reachable(self):
        # G-02 复现形态：两条 CSV 同 Key（295 字符，is_legal_source_key 合法）
        # 不同标题/DOI → 第二条加确定性后缀。修前消歧键 308 字符入库，
        # 决策 422/全文 400 死锁；修后 ≤300、决策 200。
        from coscreen import db as db_mod

        long_key = "K" * 295
        with tempfile.TemporaryDirectory(prefix="rf_g02_e2e_") as tmp:
            csv1 = Path(tmp) / "alpha.csv"
            csv2 = Path(tmp) / "beta.csv"
            self._write_csv(csv1, long_key, "Alpha study on topic one", "10.1000/alpha")
            self._write_csv(csv2, long_key, "Beta study on a totally different subject",
                            "10.1000/beta")
            for csv in (csv1, csv2):
                proc = _run_cli(["import", "--file", str(csv), "--db", self.db])
                self.assertEqual(proc.returncode, 0, proc.stderr)

        arts = db_mod.list_articles(self.db, include_duplicates=True)
        self.assertEqual(len(arts), 2)
        # (a) DB 内全部键 ≤300（修前 ghost 行 308，变红）
        for a in arts:
            self.assertLessEqual(len(a.zotero_key), KEY_MAX,
                                 f"db key len={len(a.zotero_key)}")
        ghost = next(a.zotero_key for a in arts if a.zotero_key != long_key)
        self.assertLessEqual(len(ghost), KEY_MAX)
        # 列表可见（死锁形态的"可见"半边，两态恒绿）
        r_list = self.client.get(f"/api/tasks/{self.task}/articles",
                                 headers=self.auth())
        self.assertEqual(r_list.status_code, 200, r_list.text)
        listed_keys = {row["zotero_key"] for row in r_list.json()["articles"]}
        self.assertIn(long_key, listed_keys)
        self.assertIn(ghost, listed_keys)
        # (b) 决策接口对消歧键可用：修前 Pydantic max_length=300 → 422（变红）
        r_ghost = self.client.post(
            f"/api/tasks/{self.task}/decisions",
            json={"zotero_key": ghost, "decision": "include"},
            headers=self.auth())
        self.assertEqual(r_ghost.status_code, 200, r_ghost.text)
        self.assertTrue(r_ghost.json().get("saved"))
        # 对照：保留原键的 295 字符行决策可用（复审 C 段对照，两态恒绿）
        r_keep = self.client.post(
            f"/api/tasks/{self.task}/decisions",
            json={"zotero_key": long_key, "decision": "maybe"},
            headers=self.auth())
        self.assertEqual(r_keep.status_code, 200, r_keep.text)

    def test_short_key_conflict_import_shape_unchanged(self):
        # 对照组（R2）：短键同键异文经同一 CLI 管线，消歧键仍是
        # "基键-12hex"（21 字符）且决策 200——短键行为不因本修复改变。
        from coscreen import db as db_mod

        short_key = "G02CTL1"
        with tempfile.TemporaryDirectory(prefix="rf_g02_ctl_") as tmp:
            csv1 = Path(tmp) / "ctl1.csv"
            csv2 = Path(tmp) / "ctl2.csv"
            self._write_csv(csv1, short_key, "Control study one", "10.1000/ctl-a")
            self._write_csv(csv2, short_key, "Control study two", "10.1000/ctl-b")
            for csv in (csv1, csv2):
                proc = _run_cli(["import", "--file", str(csv), "--db", self.db])
                self.assertEqual(proc.returncode, 0, proc.stderr)

        arts = db_mod.list_articles(self.db, include_duplicates=True)
        self.assertEqual(len(arts), 2)
        ghost = next(a.zotero_key for a in arts if a.zotero_key != short_key)
        self.assertRegex(ghost, rf"^{re.escape(short_key)}-[0-9a-f]{{12}}$")
        self.assertEqual(len(ghost), len(short_key) + 13)  # 旧公式形态（无截断）
        r_ghost = self.client.post(
            f"/api/tasks/{self.task}/decisions",
            json={"zotero_key": ghost, "decision": "include"},
            headers=self.auth())
        self.assertEqual(r_ghost.status_code, 200, r_ghost.text)


if __name__ == "__main__":
    unittest.main()
