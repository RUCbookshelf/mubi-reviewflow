"""r9 L2a（编码·共识）钉子测试 —— 清单 §3.2 的 14 条修复。

每条用例对应一个 confirmed 缺陷，**在基线 bfd07ae 上必须红**（把对应修复回退
即变红）；对照组断言"合法的既有行为逐位不变"（R1/R2/R3）。

覆盖与探针口径（清单 §3.2 原文关键短语）：
  F1-110  VERSION 带 BOM -> ``import pkg`` 不崩、值仍为版本串
  F2-89   跨进程竞态快照 -> ``UNIQUE constraint failed: coding_dimensions.name``
          曾裸抛 IntegrityError（``issubclass(ValueError) == False``）
  F2-90   choice ``A|B`` -> numeric 后存量值仍计完成；不可转换必须拒绝改型
  F2-91   replace 导入删必需字段 -> ``required_field_missing`` / ``complete_keys=set()``
  F2-95   stage2 两个 exclude、0 include -> ``coding_queue() -> ['K1','K2','K3']``
  F3-12   2×500KB source -> ``ValueError: value and source must be at most 1 MB``
  F3-13   ``rater_ids ('a ','a')`` 通过守卫后 SQL 坍缩、``missing_by_rater`` 静默
  F3-9    缺失路径 ``list_freezes -> []`` 且库被创建 57344B
  F2-92   录制 off 时 import_scheme 仍写 ``trace_field_lifecycles``
  F2-93   ``normalize '1_000' -> '1_000'``（导出后 pandas 读成 str）
  F2-98   ``ensure_fields`` 前 8 键、后 9 键，``key-set equal: False``
  F3-10   no-trace/off -> ``versions 1 2 3 4``
  F3-11   ``'{not valid json'`` -> ``GET freezes/1 500`` vs 其余端点 400
  F3-14   ``append_consensus_record([1,2]) -> OK``、同一 rater v1+v2 作独立输入
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
import uuid
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_fixture = tempfile.TemporaryDirectory(prefix="rf_r9_l2a_")
os.environ.setdefault("COBOOKSHELF_DATA_DIR", _fixture.name)
os.environ.setdefault("REVIEWFLOW_MODE", "desktop")

from coscreen import coding, coding_consensus, coding_evidence, db as articles_db
from coscreen import fulltext, review_workflow
from coscreen.models import Article
from coscreen.research_trace import TraceContext, ensure_schema, update_settings

REPO = Path(__file__).resolve().parents[2]


def _fresh_db(root: Path, name: str) -> Path:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "screener.db"


def _seed(db: Path, keys: tuple[str, ...] = ("K1", "K2", "K3")) -> None:
    articles_db.init_db(db)
    articles_db.upsert_articles(
        db, [Article(zotero_key=key, title=f"T{key}") for key in keys], [])


def _off_context(task: str = "r9-l2a") -> TraceContext:
    """trace 录制**关闭**的上下文（探针口径：off 状态）。"""
    return TraceContext(task_id=task, actor_id="901", request_id=str(uuid.uuid4()))


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="rf_r9_l2a_case_")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()


# ---------------------------------------------------------------------------
# b01·F1-110：VERSION 带 BOM 时包导入不再崩
# ---------------------------------------------------------------------------
class VersionFileBomTests(_Base):
    def _probe(self, version_bytes: bytes) -> subprocess.CompletedProcess:
        pkg_root = self.root / "pkgroot"
        (pkg_root / "pkgprobe").mkdir(parents=True, exist_ok=True)
        (pkg_root / "pkgprobe" / "__init__.py").write_bytes(
            (REPO / "coscreen" / "__init__.py").read_bytes())
        (pkg_root / "VERSION").write_bytes(version_bytes)
        code = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(pkg_root)!r})
            import pkgprobe
            print(pkgprobe.__version__)
        """)
        # -I：隔离模式，避免把当前工作树的 coscreen 抢先解析进来
        return subprocess.run([sys.executable, "-I", "-c", code],
                              capture_output=True, text=True, encoding="utf-8")

    def test_bom_version_still_imports(self):
        """修复前：UnicodeDecodeError 冲出 except OSError，包整体不可导入。"""
        out = self._probe(b"\xef\xbb\xbf2.1.4")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "2.1.4")

    def test_plain_ascii_version_unchanged(self):
        """对照组：仓库现状 VERSION（纯 ASCII）读取结果逐字节不变。"""
        out = self._probe(b"2.1.4\n")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "2.1.4")
        # 2026-11-13 随正名：仓库 VERSION 随发布推进（2.1.4→2.1.5→…），
        # 钉硬编码版本号必随每次发版红——改为钉「纯 ASCII 语义版本」本身
        #（受保护可观察面=无 BOM、纯 ASCII、可被 ascii 读出，非具体数字）。
        import re as _re
        self.assertRegex(
            (REPO / "VERSION").read_text(encoding="ascii").strip(),
            _re.compile(r"^\d+(\.\d+)+$"))

    def test_non_utf8_bytes_fall_back_to_default(self):
        """非 UTF-8 残留字节不再致命：退回 '0.0.0'（与"无 VERSION"同口径）。"""
        out = self._probe(b"\xff\xfe2\x00.\x001\x00")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "0.0.0")


# ---------------------------------------------------------------------------
# b04·F2-89：import_scheme 快照与落库同事务 + UNIQUE 翻译
# ---------------------------------------------------------------------------
class ImportSchemeRaceTests(_Base):
    def test_stale_snapshot_unique_is_value_error(self):
        """修复前：UNIQUE constraint failed 裸 IntegrityError（非 ValueError）。"""
        db = _fresh_db(self.root, "f289")
        _seed(db)
        coding.add_dimension(db, "B", "text", [])

        real = coding._scheme_snapshot
        coding._scheme_snapshot = lambda conn: []  # 模拟跨进程陈旧的空快照
        try:
            with self.assertRaises(ValueError) as caught:
                coding.import_scheme(db, {"version": 1, "dimensions": [
                    {"name": "B", "dtype": "text", "options": []}]})
        finally:
            coding._scheme_snapshot = real
        self.assertIn("维度名称已存在", str(caught.exception))
        self.assertNotIn("UNIQUE", str(caught.exception))

    def test_snapshot_sees_fields_committed_before_import(self):
        """对照组：事务内快照能看到已提交的同字段，按"已存在"路径精确匹配。"""
        db = _fresh_db(self.root, "f289ok")
        _seed(db)
        dim_id = coding.add_dimension(db, "A", "text", [], description="x")
        coding_evidence.ensure_fields(db)
        field_id = coding.export_scheme(db)["dimensions"][0]["field_id"]
        entry = coding.export_scheme(db)["dimensions"][0]
        self.assertEqual(coding.import_scheme(db, {"version": 1, "dimensions": [entry]}), 1)
        rows = coding.list_dimensions(db)
        self.assertEqual([(d["id"], d["name"]) for d in rows], [(dim_id, "A")])
        self.assertEqual(coding.export_scheme(db)["dimensions"][0]["field_id"], field_id)


# ---------------------------------------------------------------------------
# b04·F2-90：改 dtype/选项时迁移或拒绝存量值
# ---------------------------------------------------------------------------
class DimensionDtypeChangeTests(_Base):
    def test_incompatible_values_block_dtype_change(self):
        """修复前：choice 'A|B' 改 numeric 后值保留、complete_keys 仍含 K1。"""
        db = _fresh_db(self.root, "f290")
        _seed(db, ("K1",))
        dim_id = coding.add_dimension(db, "Choice", "choice", ["A", "B"], multi_select=True)
        coding.save_coding_value(db, dim_id, "K1", "A|B")

        with self.assertRaises(ValueError) as caught:
            coding.update_dimension(db, dim_id, dtype="numeric")
        self.assertIn("不符合新定义", str(caught.exception))
        self.assertIn("K1", str(caught.exception))
        # 失败必须整体回滚：定义与值都不动
        self.assertEqual(coding.list_dimensions(db)[0]["dtype"], "choice")
        self.assertEqual(coding.get_coding_values(db, "K1"), {dim_id: "A|B"})

    def test_compatible_values_are_kept_and_complete(self):
        """对照组：数值形态的 choice 值改 numeric 后逐字保留、仍计完成。"""
        db = _fresh_db(self.root, "f290ok")
        _seed(db, ("K1",))
        dim_id = coding.add_dimension(db, "Nums", "choice", ["1", "2"])
        coding.save_coding_value(db, dim_id, "K1", "2")
        coding.update_dimension(db, dim_id, dtype="numeric")
        self.assertEqual(coding.list_dimensions(db)[0]["dtype"], "numeric")
        self.assertEqual(coding.get_coding_values(db, "K1"), {dim_id: "2"})
        self.assertEqual(coding_evidence.complete_keys(db, db, ["K1"]), {"K1"})

    def test_narrowing_options_rewrites_choice_order(self):
        """选项收窄 + 重排：仍合法的多选值按新选项顺序规范化落库。"""
        db = _fresh_db(self.root, "f290order")
        _seed(db, ("K1",))
        dim_id = coding.add_dimension(db, "Pick", "choice", ["B", "A"], multi_select=True)
        coding.save_coding_value(db, dim_id, "K1", "A|B")
        self.assertEqual(coding.get_coding_values(db, "K1")[dim_id], "B|A")
        coding.update_dimension(db, dim_id, options=["A", "B"])
        self.assertEqual(coding.get_coding_values(db, "K1")[dim_id], "A|B")

    def test_text_only_change_leaves_values_untouched(self):
        """对照组：只改名称/说明（定义语义未变）不触碰任何存量值。"""
        db = _fresh_db(self.root, "f290text")
        _seed(db, ("K1",))
        dim_id = coding.add_dimension(db, "Free", "numeric", [])
        coding.save_coding_value(db, dim_id, "K1", "1e3")
        coding.update_dimension(db, dim_id, description="renamed only")
        self.assertEqual(coding.get_coding_values(db, "K1"), {dim_id: "1e3"})


# ---------------------------------------------------------------------------
# b04·F2-91：replace 导入不得静默删除必需字段
# ---------------------------------------------------------------------------
class RequiredFieldRemovalTests(_Base):
    def _required_db(self, name: str):
        db = _fresh_db(self.root, name)
        _seed(db, ("K1",))
        coding.add_dimension(db, "Req", "text", [])
        coding_evidence.ensure_fields(db)
        field_id = coding.export_scheme(db)["dimensions"][0]["field_id"]
        with closing(review_workflow.connect(db)) as conn, conn:
            conn.execute("UPDATE workflow_state SET evidence_json=?", (json.dumps([field_id]),))
        return db, field_id

    def test_replace_import_rejects_required_removal(self):
        """修复前：导入缺字段的方案后 mapping 报 required_field_missing、complete=∅。"""
        db, _ = self._required_db("f291")
        with self.assertRaises(ValueError) as caught:
            coding.import_scheme(db, {"version": 1, "dimensions": []}, replace=True)
        self.assertIn("required evidence field", str(caught.exception))
        # 失败即回滚：字段还在，也没有遗留 required 幽灵项
        self.assertEqual([d["name"] for d in coding.list_dimensions(db)], ["Req"])
        self.assertEqual(coding_evidence.mapping(db, db)[1], [])

    def test_non_required_field_still_removable(self):
        """对照组：非必需字段仍可被 replace 移除（未反向限制合法导入）。"""
        db, field_id = self._required_db("f291ok")
        coding.add_dimension(db, "Free", "text", [])
        self.assertEqual(len(coding.list_dimensions(db)), 2)
        entry = coding._scheme_entry(coding.list_dimensions(db)[0])
        entry["field_id"] = field_id
        coding.import_scheme(db, {"version": 1, "dimensions": [entry]}, replace=True)
        self.assertEqual([d["name"] for d in coding.list_dimensions(db)], ["Req"])

    def test_append_import_still_keeps_required_field(self):
        """对照组：replace=False 的追加导入不受新守卫影响。"""
        db, _ = self._required_db("f291append")
        coding.import_scheme(db, {"version": 1, "dimensions": [
            {"name": "New", "dtype": "text", "options": []}]}, replace=False)
        self.assertEqual([d["name"] for d in coding.list_dimensions(db)], ["Req", "New"])


# ---------------------------------------------------------------------------
# b04·F2-95：stage2→stage1 回退排除 exclude
# ---------------------------------------------------------------------------
class CodingQueueFallbackTests(_Base):
    def _db_with_stage2(self, name: str, stage2: dict[str, str]) -> Path:
        db = _fresh_db(self.root, name)
        _seed(db)
        for key in ("K1", "K2", "K3"):
            articles_db.save_decision(db, key, "include")
        for key, decision in stage2.items():
            fulltext.save_stage2(db, key, decision, exclusion_reason="probe")
        return db

    def test_stage2_excludes_do_not_leak_into_fallback(self):
        """修复前：两个 exclude、0 include -> ['K1','K2','K3']（K1/K2 泄漏）。"""
        db = self._db_with_stage2("f295", {"K1": "exclude", "K2": "exclude"})
        self.assertEqual(coding.coding_queue(db, "stage2"), ["K3"])
        self.assertEqual(coding.coding_queue(db), ["K3"])

    def test_stage2_include_takes_priority(self):
        """对照组：有 include 时只返回 include（主路径不变）。"""
        db = self._db_with_stage2("f295inc", {"K1": "include", "K2": "exclude"})
        self.assertEqual(coding.coding_queue(db, "stage2"), ["K1"])

    def test_maybe_still_excluded_from_fallback(self):
        """对照组：maybe 的既有排除行为不变。"""
        db = self._db_with_stage2("f295maybe", {"K2": "maybe"})
        self.assertEqual(coding.coding_queue(db, "stage2"), ["K1", "K3"])

    def test_stage1_mode_is_unaffected(self):
        """对照组：显式 stage1 仍是初筛纳入全集。"""
        db = self._db_with_stage2("f295s1", {"K1": "exclude"})
        self.assertEqual(coding.coding_queue(db, "stage1"), ["K1", "K2", "K3"])


# ---------------------------------------------------------------------------
# b04·F3-12：freeze 快照不再套用 1 MB 入参上限
# ---------------------------------------------------------------------------
class FreezeSnapshotSizeTests(_Base):
    BIG = "x" * 500_000

    def _two_big_consensus(self, db: Path) -> list[int]:
        ids = []
        for index in range(2):
            rater = coding_consensus.append_rater_record(
                db, item_key=f"K{index}", field_key="f", rater_id="r",
                value="v", source={"blob": self.BIG})
            record = coding_consensus.append_consensus_record(
                db, item_key=f"K{index}", field_key="f", reconciler_id="z",
                value="v", source={"blob": self.BIG}, rationale="r",
                rater_record_ids=[rater["id"]])
            ids.append(record["id"])
        return ids

    def test_two_large_records_can_be_frozen(self):
        """修复前：ValueError: value and source must be at most 1 MB。"""
        db = _fresh_db(self.root, "f312")
        ids = self._two_big_consensus(db)
        frozen = coding_consensus.freeze_consensus(db, name="two", frozen_by="z", consensus_ids=ids)
        self.assertEqual(frozen["version"], 1)
        loaded = coding_consensus.get_freeze(db, frozen["id"])
        self.assertEqual([r["id"] for r in loaded["records"]], ids)
        # 快照摘要按落库字节自证
        import hashlib
        with closing(sqlite3.connect(db)) as conn:
            raw = conn.execute("SELECT records_json FROM coding_freezes WHERE id=?",
                               (frozen["id"],)).fetchone()[0]
        self.assertEqual(hashlib.sha256(raw.encode("utf-8")).hexdigest(), frozen["content_sha256"])

    def test_single_input_still_limited_to_1mb(self):
        """对照组：单条 value/source 的 1 MB 入参上限不变（消息也不变）。"""
        db = _fresh_db(self.root, "f312lim")
        with self.assertRaises(ValueError) as caught:
            coding_consensus.append_rater_record(
                db, item_key="K1", field_key="f", rater_id="r",
                value="v", source={"blob": "x" * 1_100_000})
        self.assertEqual(str(caught.exception), "value and source must be at most 1 MB")

    def test_small_freeze_payload_unchanged(self):
        """对照组：常规小冻结的版本链与摘要口径逐位不变。"""
        db = _fresh_db(self.root, "f312small")
        rater = coding_consensus.append_rater_record(
            db, item_key="K1", field_key="f", rater_id="r", value="A", source={"s": 1})
        record = coding_consensus.append_consensus_record(
            db, item_key="K1", field_key="f", reconciler_id="z", value="A",
            source={"s": 1}, rationale="r", rater_record_ids=[rater["id"]])
        first = coding_consensus.freeze_consensus(db, name="v", frozen_by="z",
                                                 consensus_ids=[record["id"]])
        second = coding_consensus.freeze_consensus(db, name="v", frozen_by="z",
                                                  consensus_ids=[record["id"]])
        self.assertEqual((first["version"], second["version"]), (1, 2))
        self.assertEqual(second["previous_freeze_id"], first["id"])
        self.assertEqual(first["content_sha256"], second["content_sha256"])


# ---------------------------------------------------------------------------
# b04·F3-13：评分员互异守卫必须在归一化之后比较
# ---------------------------------------------------------------------------
class RaterIdentityGuardTests(_Base):
    def _db(self, name: str) -> Path:
        db = _fresh_db(self.root, name)
        for key in ("K1", "K2"):
            coding_consensus.append_rater_record(
                db, item_key=key, field_key="f", rater_id="a", value="v", source={"s": 1})
        return db

    def test_whitespace_variant_is_rejected(self):
        """修复前：('a ','a') 通过守卫，SQL 坍缩为同一评分员、n_paired=0 静默。"""
        db = self._db("f313")
        with self.assertRaises(ValueError) as caught:
            coding_consensus.latest_rater_records_for_field(
                db, field_key="f", rater_ids=("a ", "a"))
        self.assertEqual(str(caught.exception), "select two different coders")

    def test_identical_ids_still_rejected(self):
        """对照组：完全相同的两个 ID 照旧拒绝（消息不变）。"""
        db = self._db("f313same")
        with self.assertRaises(ValueError) as caught:
            coding_consensus.latest_rater_records_for_field(
                db, field_key="f", rater_ids=("a", "a"))
        self.assertEqual(str(caught.exception), "select two different coders")

    def test_different_coders_still_allowed(self):
        """对照组：真正不同的评分员（含带空白写法）照旧可选，结果按归一 ID 返回。"""
        db = self._db("f313ok")
        rows = coding_consensus.latest_rater_records_for_field(
            db, field_key="f", rater_ids=("a ", "b"))
        self.assertEqual([(r["item_key"], r["rater_id"]) for r in rows],
                         [("K1", "a"), ("K2", "a")])

    def test_missing_side_stays_visible(self):
        """对照组：一方确实无记录时仍返回有记录一侧的条目（不静默清空）。"""
        db = self._db("f313empty")
        rows = coding_consensus.latest_rater_records_for_field(
            db, field_key="f", rater_ids=("a", "b"))
        self.assertEqual(sorted(r["item_key"] for r in rows), ["K1", "K2"])
        self.assertEqual({r["rater_id"] for r in rows}, {"a"})


# ---------------------------------------------------------------------------
# b05·F3-9：只读函数不建库
# ---------------------------------------------------------------------------
class ReadOnlyConsensusTests(_Base):
    def test_missing_path_raises_instead_of_empty_success(self):
        """修复前：list_freezes -> [] 且库被创建 57344B。"""
        missing = self.root / "f39" / "does-not-exist.db"
        missing.parent.mkdir(parents=True, exist_ok=True)
        calls = {
            "list_freezes": lambda: coding_consensus.list_freezes(missing),
            "get_freeze": lambda: coding_consensus.get_freeze(missing, 1),
            "list_rater_records": lambda: coding_consensus.list_rater_records(
                missing, item_key="K", field_key="f"),
            "latest_rater_records_for_field": lambda: (
                coding_consensus.latest_rater_records_for_field(
                    missing, field_key="f", rater_ids=("a", "b"))),
            "list_consensus_records": lambda: coding_consensus.list_consensus_records(
                missing, item_key="K", field_key="f"),
        }
        for name, call in calls.items():
            with self.subTest(function=name):
                with self.assertRaises(FileNotFoundError):
                    call()
        self.assertFalse(missing.exists(), "只读函数不得创建数据库文件")

    def test_existing_db_without_tables_returns_empty(self):
        """对照组：库存在但从未写共识数据 -> 空结果（不抛、不建表）。"""
        db = _fresh_db(self.root, "f39ok")
        sqlite3.connect(db).close()
        self.assertEqual(coding_consensus.list_freezes(db), [])
        self.assertIsNone(coding_consensus.get_freeze(db, 1))
        self.assertEqual(coding_consensus.list_rater_records(db, item_key="K", field_key="f"), [])
        self.assertEqual(coding_consensus.list_consensus_records(db, item_key="K", field_key="f"), [])
        with closing(sqlite3.connect(db)) as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(tables, set(), "只读函数不得执行 DDL")

    def test_read_functions_see_committed_data(self):
        """对照组：正常库的读结果与写入逐字段一致。"""
        db = _fresh_db(self.root, "f39data")
        rater = coding_consensus.append_rater_record(
            db, item_key="K1", field_key="f", rater_id="r", value="A", source={"s": 1})
        self.assertEqual([r["id"] for r in coding_consensus.list_rater_records(
            db, item_key="K1", field_key="f")], [rater["id"]])
        record = coding_consensus.append_consensus_record(
            db, item_key="K1", field_key="f", reconciler_id="z", value="A",
            source={"s": 1}, rationale="r", rater_record_ids=[rater["id"]])
        frozen = coding_consensus.freeze_consensus(db, name="v", frozen_by="z",
                                                  consensus_ids=[record["id"]])
        self.assertEqual(coding_consensus.get_freeze(db, frozen["id"])["id"], frozen["id"])
        self.assertEqual([f["id"] for f in coding_consensus.list_freezes(db)], [frozen["id"]])


# ---------------------------------------------------------------------------
# b11·F2-92：录制关闭时 import_scheme 不写 field lifecycles
# ---------------------------------------------------------------------------
class TraceOffLifecycleTests(_Base):
    def test_off_recording_writes_no_lifecycle(self):
        """修复前：off 状态 lifecycles 1 写入、trace_events 0（仅边界）。"""
        db = _fresh_db(self.root, "f292")
        _seed(db, ("K1",))
        context = _off_context()
        with closing(coding._connect(db)) as conn, conn:
            ensure_schema(conn)
            update_settings(conn, context, stages=[], status="off",
                            operation_id="probe-off", expected_version=0)
        coding.import_scheme(db, {"version": 1, "dimensions": [
            {"name": "X", "dtype": "text", "options": []}]}, replace=False,
            trace_context=context)
        with closing(sqlite3.connect(db)) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM trace_field_lifecycles").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM trace_events").fetchone()[0], 0)

    def test_recording_on_still_writes_lifecycle_and_event(self):
        """对照组：录制开启时生命周记与事件照旧各写一条（行为逐位不变）。"""
        db = _fresh_db(self.root, "f292on")
        _seed(db, ("K1",))
        context = _off_context("r9-l2a-on")
        with closing(coding._connect(db)) as conn, conn:
            ensure_schema(conn)
            update_settings(conn, context, stages=["coding"], status="recording",
                            operation_id="probe-on", expected_version=0)
        coding.import_scheme(db, {"version": 1, "dimensions": [
            {"name": "X", "dtype": "text", "options": []}]}, replace=False,
            trace_context=context)
        with closing(sqlite3.connect(db)) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM trace_field_lifecycles").fetchone()[0], 1)
            actions = [r[0] for r in conn.execute("SELECT action FROM trace_events")]
        self.assertEqual(actions[-1], "field_definition_created")
        self.assertEqual(actions.count("field_definition_created"), 1)


# ---------------------------------------------------------------------------
# b11·F2-93：numeric 值以可交换的 ASCII 表示入库
# ---------------------------------------------------------------------------
class NumericCanonicalTests(_Base):
    def _save(self, name: str, text: str) -> str:
        db = _fresh_db(self.root, name)
        _seed(db, ("K1",))
        dim_id = coding.add_dimension(db, "N", "numeric", [])
        coding.save_coding_value(db, dim_id, "K1", text)
        return coding.get_coding_values(db, "K1")[dim_id]

    def test_underscore_grouping_is_canonicalized(self):
        """修复前：normalize '1_000' -> '1_000'（导出 pandas dtype: str）。"""
        self.assertEqual(self._save("f293a", "1_000"), "1000")
        self.assertEqual(self._save("f293b", "1_000.50"), "1000.50")

    def test_non_ascii_digits_are_canonicalized(self):
        """阿拉伯-印度数字与全角数字折成 ASCII（值不变）。"""
        self.assertEqual(self._save("f293c", "\u0661\u0662\u0663"), "123")
        self.assertEqual(self._save("f293d", "\uff11\uff12\uff13"), "123")

    def test_plain_ascii_forms_are_byte_identical(self):
        """对照组：常规 ASCII 写法逐字保留（含大小写指数、前导点、尾点）。"""
        for index, text in enumerate(("1000", "1e3", "1E5", ".5", "5.", "-0.5",
                                      "2.5e-3", "12345678901234567890", "-0")):
            with self.subTest(text=text):
                self.assertEqual(self._save(f"f293ok{index}", text), text)

    def test_non_finite_and_garbage_still_rejected(self):
        """对照组：非有限/非数值仍按原文案拒绝（未放宽合法域之外）。"""
        db = _fresh_db(self.root, "f293bad")
        _seed(db, ("K1",))
        dim_id = coding.add_dimension(db, "N", "numeric", [])
        for text in ("inf", "-inf", "nan", "abc", "1__0", "_1"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    coding.save_coding_value(db, dim_id, "K1", text)


# ---------------------------------------------------------------------------
# b11·F2-98：export_scheme 键集固定
# ---------------------------------------------------------------------------
class SchemeExportKeySetTests(_Base):
    KEYS = {"name", "dtype", "options", "multi_select", "unit",
            "description", "section", "position", "field_id"}

    def test_key_set_is_stable_across_ensure_fields(self):
        """修复前：ensure_fields 前 8 键、后 9 键，key-set equal: False。"""
        db = _fresh_db(self.root, "f298")
        _seed(db, ("K1",))
        coding.add_dimension(db, "A", "text", [])
        before = set(coding.export_scheme(db)["dimensions"][0])
        coding_evidence.ensure_fields(db)
        after = set(coding.export_scheme(db)["dimensions"][0])
        self.assertEqual(before, after)
        self.assertEqual(before, self.KEYS)

    def test_two_exports_of_same_state_are_byte_equal(self):
        """同一入库状态两次导出逐字节相等，且 field_id 稳定。"""
        db = _fresh_db(self.root, "f298twice")
        _seed(db, ("K1",))
        coding.add_dimension(db, "A", "text", [])
        coding_evidence.ensure_fields(db)
        first = json.dumps(coding.export_scheme(db), ensure_ascii=False, sort_keys=True)
        second = json.dumps(coding.export_scheme(db), ensure_ascii=False, sort_keys=True)
        self.assertEqual(first, second)
        payload = json.loads(first)["dimensions"][0]
        self.assertIsNotNone(payload["field_id"])
        # 导出即为可导入载荷（round-trip 不改字段身份）
        coding.import_scheme(db, json.loads(first), replace=True)
        self.assertEqual(coding.export_scheme(db)["dimensions"][0]["field_id"],
                         payload["field_id"])

    def test_empty_scheme_has_no_dimensions(self):
        """对照组：空方案导出形态不变。"""
        db = _fresh_db(self.root, "f298empty")
        _seed(db, ("K1",))
        self.assertEqual(coding.export_scheme(db), {"version": 1, "dimensions": []})


# ---------------------------------------------------------------------------
# b11·F3-10：trace 关闭时同内容提交仍幂等
# ---------------------------------------------------------------------------
class RaterIdempotencyTests(_Base):
    def test_repeat_submission_is_idempotent_without_trace(self):
        """修复前：no-trace/off -> versions 1 2 3 4（每次 +1 的伪修订）。"""
        db = _fresh_db(self.root, "f310")
        ids = [
            coding_consensus.append_rater_record(
                db, item_key="K1", field_key="f", rater_id="a", value="same",
                source={"s": 1}, rationale="r")["id"]
            for _ in range(4)
        ]
        self.assertEqual(len(set(ids)), 1)
        with closing(sqlite3.connect(db)) as conn:
            self.assertEqual([r[0] for r in conn.execute(
                "SELECT version FROM coding_rater_records ORDER BY version")], [1])

    def test_changed_content_still_creates_new_version(self):
        """对照组：内容真的变了仍追加新版本并接上 supersedes 链。"""
        db = _fresh_db(self.root, "f310v")
        first = coding_consensus.append_rater_record(
            db, item_key="K1", field_key="f", rater_id="a", value="A",
            source={"s": 1}, rationale="r")
        second = coding_consensus.append_rater_record(
            db, item_key="K1", field_key="f", rater_id="a", value="B",
            source={"s": 1}, rationale="r")
        self.assertEqual((first["version"], second["version"]), (1, 2))
        self.assertEqual(second["supersedes_id"], first["id"])

    def test_trace_on_keeps_idempotency_and_replay(self):
        """对照组：录制开启时同内容幂等（既有行为）依旧成立。"""
        db = _fresh_db(self.root, "f310on")
        context = _off_context("r9-l2a-rater")
        with closing(coding_consensus._connect(db)) as conn, conn:
            ensure_schema(conn)
            update_settings(conn, context, stages=["coding"], status="recording",
                            operation_id="probe-rater", expected_version=0)
        first = coding_consensus.append_rater_record(
            db, item_key="K1", field_key="f", rater_id="901", value="same",
            source={"s": 1}, rationale="r", trace_context=context)
        second = coding_consensus.append_rater_record(
            db, item_key="K1", field_key="f", rater_id="901", value="same",
            source={"s": 1}, rationale="r", trace_context=context)
        self.assertEqual(first["id"], second["id"])


# ---------------------------------------------------------------------------
# b11·F3-11：损坏库行的 JSON 解析受控
# ---------------------------------------------------------------------------
class CorruptStoreJsonTests(_Base):
    def _corrupt_freeze(self, db: Path) -> int:
        rater = coding_consensus.append_rater_record(
            db, item_key="K1", field_key="f", rater_id="r", value="v", source={"s": 1})
        record = coding_consensus.append_consensus_record(
            db, item_key="K1", field_key="f", reconciler_id="z", value="v",
            source={"s": 1}, rationale="r", rater_record_ids=[rater["id"]])
        frozen = coding_consensus.freeze_consensus(db, name="v1", frozen_by="z",
                                                  consensus_ids=[record["id"]])
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute("DROP TRIGGER coding_freezes_no_update")
            conn.execute("UPDATE coding_freezes SET records_json='{not valid json'")
        return frozen["id"]

    def test_corrupt_freeze_raises_value_error(self):
        """修复前：裸 JSONDecodeError（端点 500）而非业务 ValueError（400）。"""
        db = _fresh_db(self.root, "f311")
        freeze_id = self._corrupt_freeze(db)
        with self.assertRaises(ValueError) as caught:
            coding_consensus.get_freeze(db, freeze_id)
        self.assertIn("not valid JSON", str(caught.exception))
        with self.assertRaises(ValueError):
            coding_consensus.list_freezes(db)

    def test_corrupt_rater_value_raises_value_error(self):
        """同族：评分员记录的坏 value_json 也译成 ValueError（不是裸 JSON 错）。"""
        db = _fresh_db(self.root, "f311r")
        coding_consensus.append_rater_record(
            db, item_key="K1", field_key="f", rater_id="r", value="v", source={"s": 1})
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute("DROP TRIGGER coding_rater_records_no_update")
            conn.execute("UPDATE coding_rater_records SET value_json='{bad'")
        with self.assertRaises(ValueError) as caught:
            coding_consensus.list_rater_records(db, item_key="K1", field_key="f")
        self.assertIn("not valid JSON", str(caught.exception))

    def test_intact_rows_are_unchanged(self):
        """对照组：完好库的读结果逐字段不变（含 freeze 摘要）。"""
        db = _fresh_db(self.root, "f311ok")
        rater = coding_consensus.append_rater_record(
            db, item_key="K1", field_key="f", rater_id="r", value={"a": [1, 2]},
            source={"s": "文本"}, rationale="why")
        record = coding_consensus.append_consensus_record(
            db, item_key="K1", field_key="f", reconciler_id="z", value="v",
            source={"s": 1}, rationale="r", rater_record_ids=[rater["id"]])
        frozen = coding_consensus.freeze_consensus(db, name="v1", frozen_by="z",
                                                  consensus_ids=[record["id"]])
        loaded_rater = coding_consensus.list_rater_records(
            db, item_key="K1", field_key="f")[0]
        self.assertEqual(loaded_rater["value"], {"a": [1, 2]})
        self.assertEqual(loaded_rater["source"], {"s": "文本"})
        self.assertEqual(coding_consensus.get_freeze(db, frozen["id"])["records"][0]["id"],
                         record["id"])


class F311EndpointFreezeTranslationTests(_Base):
    """F3-11 端点侧（r9 L2a 登记补齐）：get_freeze 是 8 个 consensus 端点中
    唯一不接 ValueError 的——同一个损坏行，``GET freezes`` 400 而
    ``GET freezes/{id}`` 500。修复：端点按同文件其余 7 处口径把 ValueError
    译成 ``bad()``（400）。"""

    def _app_client(self, db: Path):
        from fastapi import FastAPI, HTTPException
        from fastapi.testclient import TestClient

        from custom_backend import coding_consensus_api

        class _Info:
            task_id = "T"

        app = FastAPI()
        coding_consensus_api.register_coding_consensus_routes(
            app,
            lambda: {"user_id": "u1"},
            lambda task_id: _Info(),
            lambda info: str(db),
            lambda info, user, action: True,
            lambda info, item_key, field_key: True,
            lambda message: HTTPException(400, str(message)),
        )
        return TestClient(app, raise_server_exceptions=False)

    def _seeded_freeze(self, db: Path) -> int:
        rater = coding_consensus.append_rater_record(
            db, item_key="K1", field_key="f", rater_id="r", value="v", source={"s": 1})
        record = coding_consensus.append_consensus_record(
            db, item_key="K1", field_key="f", reconciler_id="z", value="v",
            source={"s": 1}, rationale="r", rater_record_ids=[rater["id"]])
        frozen = coding_consensus.freeze_consensus(
            db, name="v1", frozen_by="z", consensus_ids=[record["id"]])
        return frozen["id"]

    def test_corrupt_freeze_endpoint_is_400_not_500(self):
        """修前：``GET freezes/{id}`` -> 500 Internal Server Error（未接 ValueError）。"""
        db = _fresh_db(self.root, "f311ep")
        freeze_id = self._seeded_freeze(db)
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute("DROP TRIGGER coding_freezes_no_update")
            conn.execute("UPDATE coding_freezes SET records_json='{not valid json'")
        client = self._app_client(db)
        one = client.get(f"/api/tasks/T/coding/freezes/{freeze_id}")
        self.assertEqual(400, one.status_code, one.text[:300])
        self.assertIn("not valid JSON", one.text)
        listed = client.get("/api/tasks/T/coding/freezes")
        self.assertEqual(400, listed.status_code, listed.text[:300])

    def test_healthy_freeze_endpoints_unchanged(self):
        """对照组：完好库上单条/列表/未知 id 的既有响应逐位不变。"""
        db = _fresh_db(self.root, "f311epok")
        freeze_id = self._seeded_freeze(db)
        client = self._app_client(db)
        one = client.get(f"/api/tasks/T/coding/freezes/{freeze_id}")
        self.assertEqual(200, one.status_code, one.text[:300])
        body = one.json()
        self.assertEqual(freeze_id, body["id"])
        self.assertEqual("v1", body["name"])
        self.assertEqual(1, len(body["records"]))
        listed = client.get("/api/tasks/T/coding/freezes")
        self.assertEqual(200, listed.status_code, listed.text[:300])
        self.assertEqual([freeze_id], [row["id"] for row in listed.json()["freezes"]])
        self.assertNotIn("records", listed.json()["freezes"][0])
        missing = client.get("/api/tasks/T/coding/freezes/987654")
        self.assertEqual(404, missing.status_code, missing.text[:300])


# ---------------------------------------------------------------------------
# b11·F3-14：共识输入必须来自互异评分员的最新版
# ---------------------------------------------------------------------------
class ConsensusInputIndependenceTests(_Base):
    def _two_coders(self, db: Path) -> tuple[dict, dict]:
        return (
            coding_consensus.append_rater_record(
                db, item_key="K1", field_key="f", rater_id="alice", value="A",
                source={"s": 1}),
            coding_consensus.append_rater_record(
                db, item_key="K1", field_key="f", rater_id="bob", value="B",
                source={"s": 1}),
        )

    def test_same_rater_two_versions_rejected(self):
        """修复前：append_consensus_record([v1,v2]) -> OK，sources [(1,1),(1,2)]。"""
        db = _fresh_db(self.root, "f314")
        v1 = coding_consensus.append_rater_record(
            db, item_key="K1", field_key="f", rater_id="solo", value="A", source={"s": 1})
        v2 = coding_consensus.append_rater_record(
            db, item_key="K1", field_key="f", rater_id="solo", value="B", source={"s": 1})
        with self.assertRaises(ValueError) as caught:
            coding_consensus.append_consensus_record(
                db, item_key="K1", field_key="f", reconciler_id="z", value="A",
                source={"s": 1}, rationale="r",
                rater_record_ids=[v1["id"], v2["id"]])
        self.assertEqual(str(caught.exception),
                         "consensus inputs must come from different coders")

    def test_stale_revision_rejected(self):
        """同一评分员的旧版也不再被当作"最新输入"接受。"""
        db = _fresh_db(self.root, "f314stale")
        v1 = coding_consensus.append_rater_record(
            db, item_key="K1", field_key="f", rater_id="alice", value="A", source={"s": 1})
        v2 = coding_consensus.append_rater_record(
            db, item_key="K1", field_key="f", rater_id="alice", value="B", source={"s": 1})
        bob = coding_consensus.append_rater_record(
            db, item_key="K1", field_key="f", rater_id="bob", value="C", source={"s": 1})
        with self.assertRaises(ValueError) as caught:
            coding_consensus.append_consensus_record(
                db, item_key="K1", field_key="f", reconciler_id="z", value="A",
                source={"s": 1}, rationale="r",
                rater_record_ids=[v1["id"], bob["id"]])
        self.assertEqual(str(caught.exception),
                         "consensus inputs must be each coder's latest revision")

    def test_two_distinct_latest_records_still_accepted(self):
        """对照组：真实双人最新版照旧可建共识，sources 顺序确定。"""
        db = _fresh_db(self.root, "f314ok")
        alice, bob = self._two_coders(db)
        record = coding_consensus.append_consensus_record(
            db, item_key="K1", field_key="f", reconciler_id="z", value="A|B",
            source={"s": 1}, rationale="r",
            rater_record_ids=[bob["id"], alice["id"]])
        self.assertEqual(record["rater_record_ids"], sorted([alice["id"], bob["id"]]))

    def test_single_input_still_accepted(self):
        """对照组：单条来源（reconciler 自行决定）照旧接受。"""
        db = _fresh_db(self.root, "f314one")
        alice, _ = self._two_coders(db)
        record = coding_consensus.append_consensus_record(
            db, item_key="K1", field_key="f", reconciler_id="z", value="A",
            source={"s": 1}, rationale="r", rater_record_ids=[alice["id"]])
        self.assertEqual(record["rater_record_ids"], [alice["id"]])

    def test_empty_sources_still_accepted(self):
        """对照组：不给出源记录（空列表）照旧接受。"""
        db = _fresh_db(self.root, "f314none")
        record = coding_consensus.append_consensus_record(
            db, item_key="K1", field_key="f", reconciler_id="z", value="A",
            source={"s": 1}, rationale="r")
        self.assertEqual(record["rater_record_ids"], [])


if __name__ == "__main__":
    unittest.main()
