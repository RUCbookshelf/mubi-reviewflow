# -*- coding: utf-8 -*-
"""2.1.5 Wave G3 · W5 全文规范归档 + 任务整包导出/导入红钉（专属新文件）。

权威设计：``out/release_plan/drafts/design_2.1.5_fulltext_archive_package.md``
v1.0（融合并取代 design_2.1.5_task_package.md v1.0）。

按任务书 W5 红钉清单成文，先于实现写成（撤实现必红）：

1. 命名函数全套钉：非法字符/中文姓/缺年缺作者/超长截断/碰撞 sha256 防碰，
   且同一输入 + 同一 taken 恒返回同一结果（纯函数确定性）。
2. 两个出口条目名一致：archive-export 的 zip 条目 === package/export 的
   ``fulltext/**`` 条目（单一事实源）。
3. scope 口径 = 当前生效决定：单筛员直取；多筛员仅全一致或有效仲裁快照
   生效；冲突未解决不进任何 include 范围；过期仲裁快照被忽略。
4. 无 PDF 条目跳过并计数；零全文 = 合法空 zip（摘要 0/0，不报错）。
5. 导出→导入往返 fulltexts 元数据一致（含/不含 PDF 两形态）；导入者为新
   属主；协作库改绑；历史库 task/screener 改写；内部回到安全命名存储。
6. 凭据扫描钉：task.json 脱敏（凭据/环境/JWT），包内零凭据/JWT 字面量；
   manifest 只记录被删字段名、不记录值。
7. 原子性 + 幂等：坏包/篡改包零半成品任务目录；同包二次导入 = 两个独立
   任务；重复导出对源任务零副作用。
8. 413 降级：整包上限超限返回结构化 413（含建议排除 PDF 的语义字段）。
9. 权限：属主可导出；普通成员/非参与者 403；导入对任何登录账号开放。

运行：
    python -m pytest build/tests/test_2_1_5_w5_archive_package.py -q
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

fixture = tempfile.TemporaryDirectory(prefix="rf_2_1_5_w5_archive_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient  # noqa: E402

from coscreen import db as db_mod  # noqa: E402
from coscreen import fulltext as fulltext_mod  # noqa: E402
from coscreen import import_history  # noqa: E402
from coscreen import screening_arbitration  # noqa: E402
from coscreen import task_collaboration  # noqa: E402
from coscreen import tasks as tasks_mod  # noqa: E402
from coscreen import review_workflow as workflow  # noqa: E402
from coscreen.fulltext_archive import archive_filename  # noqa: E402
from coscreen.models import Article  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend import task_package_api  # noqa: E402
from custom_backend.main import DATA_DIR, app  # noqa: E402

USERS = ("w5owner@example.test", "w5member@example.test", "w5outsider@example.test")
_JWT_SAMPLE = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.c2lnbmF0dXJlLXNhbXBsZQ"
_CREDENTIAL_SAMPLES = (b"secret-token-value", b"hunter2", _JWT_SAMPLE.encode())


def make_pdf(marker: str) -> bytes:
    """pypdf 生成真实可解析的一页 PDF（save_pdf 要求 ≥1 页）。"""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_metadata({"/Title": marker})
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _zip_names(payload: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return sorted(archive.namelist())


def _zip_read(payload: bytes, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return archive.read(name)


def _task_dir_count() -> int:
    root = tasks_mod.tasks_root(DATA_DIR)
    return len([p for p in root.iterdir() if p.is_dir()]) if root.is_dir() else 0


class ArchiveNamingPins(unittest.TestCase):
    """设计 §二.C：规范命名是纯函数、单一事实源。"""

    def test_illegal_chars_and_control_chars_are_replaced(self):
        name = archive_filename({
            "key": 'A/B\\C:D*E?F"G<H>I|J\x00K',
            "authors": "Wang, Alice",
            "year": 2020,
        })
        self.assertEqual(name, "A_B_C_D_E_F_G_H_I_J_K-Wang-2020.pdf")
        self.assertTrue(name.endswith(".pdf"))
        self.assertFalse(any(ch in name for ch in '<>:"/\\|?*\x00'))

    def test_chinese_surname_kept_as_is(self):
        self.assertEqual(
            archive_filename({"key": "AB12CD34", "authors": "王, 小明", "year": 2022}),
            "AB12CD34-王-2022.pdf")
        # 无逗号的单段中文姓名：复姓取两字，其余首字。
        self.assertEqual(
            archive_filename({"key": "AB12CD34", "authors": "欧阳修", "year": 2021}),
            "AB12CD34-欧阳-2021.pdf")
        self.assertEqual(
            archive_filename({"key": "AB12CD34", "authors": "王小明", "year": 2021}),
            "AB12CD34-王-2021.pdf")

    def test_missing_author_and_year_become_unknown_not_skipped(self):
        self.assertEqual(
            archive_filename({"key": "K01", "authors": "", "year": None}),
            "K01-unknown-unknown.pdf")
        self.assertEqual(
            archive_filename({"key": "K01", "authors": None, "year": 0}),
            "K01-unknown-unknown.pdf")
        # 自由文本里能识别出四位年份。
        self.assertEqual(
            archive_filename({"key": "K01", "authors": "Smith, J.",
                              "year": "Published 2019"}),
            "K01-Smith-2019.pdf")

    def test_overlong_stem_truncated_to_120_chars(self):
        name = archive_filename({"key": "K" * 300, "authors": "Wang, A",
                                 "year": 2020})
        self.assertLessEqual(len(name[:-4]), 120)
        self.assertTrue(name.endswith(".pdf"))

    def test_collision_gets_sha256_suffix_deterministically(self):
        first = archive_filename({"key": "A/B", "authors": "Wang, A", "year": 2020})
        second = archive_filename({"key": "A?B", "authors": "Wang, A", "year": 2020},
                                  taken={first})
        self.assertEqual(first, "A_B-Wang-2020.pdf")
        self.assertNotEqual(first, second)
        self.assertRegex(second, r"^A_B-Wang-2020-[0-9a-f]{8}\.pdf$")
        again = archive_filename({"key": "A?B", "authors": "Wang, A", "year": 2020},
                                 taken={first})
        self.assertEqual(second, again)

    def test_same_author_same_year_different_keys_do_not_collide(self):
        one = archive_filename({"key": "KEY1", "authors": "Wang, A", "year": 2020})
        two = archive_filename({"key": "KEY2", "authors": "Wang, A", "year": 2020})
        self.assertNotEqual(one, two)


class _ApiBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.users = []
        for name in USERS:
            try:
                user = auth_mod.create_user(name, "W5-Archive-Test-2026")
            except auth_mod.AuthError:
                user = auth_mod.get_user(name)
            cls.users.append({
                "user_id": user["id"],
                "username": user["username"],
                "token": auth_mod.create_access_token(user["id"], user["username"]),
            })

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def auth(self, index: int = 0) -> dict:
        return {"Authorization": "Bearer " + self.users[index]["token"]}

    def new_task(self, *, invite_member: bool = True) -> None:
        response = self.client.post(
            "/api/tasks", json={"name": self._testMethodName}, headers=self.auth())
        self.assertEqual(response.status_code, 201, response.text)
        self.task = response.json()["task_id"]
        self.info = tasks_mod.find_task(self.task, DATA_DIR)
        self.owner_db = self.info.dir / (
            tasks_mod.slugify_screener(self.users[0]["username"]) + ".db")
        db_mod.init_db(self.owner_db)
        workflow.migrate(self.owner_db)
        self.articles = [
            Article(zotero_key=f"K{i:02d}", title=f"Study {i:02d}",
                    authors="Wang, Alice" if i == 1 else "王, 小明" if i == 2 else "",
                    journal="J Test", year=2020 + i if i <= 4 else None,
                    doi=f"10.1000/w5k{i:02d}")
            for i in range(1, 7)
        ]
        db_mod.upsert_articles(self.owner_db, self.articles, [])
        if invite_member:
            invited = self.client.post(
                f"/api/tasks/{self.task}/collaboration/invite",
                json={"username": self.users[1]["username"], "role": "coder"},
                headers=self.auth())
            self.assertEqual(invited.status_code, 201, invited.text)
        self.member_db = self.info.dir / (
            tasks_mod.slugify_screener(self.users[1]["username"]) + ".db")

    def seed_member_db(self) -> None:
        db_mod.init_db(self.member_db)
        workflow.migrate(self.member_db)
        db_mod.upsert_articles(self.member_db, self.articles, [])

    def add_pdf(self, key: str, marker: str | None = None) -> bytes:
        data = make_pdf(marker or key)
        fulltext_mod.save_pdf(self.owner_db, key, data,
                              pdfs_dir=self.info.dir / "pdfs")
        return data

    def decide(self, mapping: dict[str, str], *, db: Path | None = None) -> None:
        for key, decision in mapping.items():
            db_mod.save_decision(db or self.owner_db, key, decision,
                                 exclusion_reason="reason" if decision == "exclude" else "")

    def archive(self, scope: str = "all", *, index: int = 0,
                task: str | None = None):
        return self.client.post(
            f"/api/tasks/{task or self.task}/fulltext/archive-export",
            json={"scope": scope}, headers=self.auth(index))

    def package(self, *, include_pdfs: bool = True, index: int = 0,
                task: str | None = None):
        return self.client.post(
            f"/api/tasks/{task or self.task}/package/export",
            json={"include_pdfs": include_pdfs}, headers=self.auth(index))


class ArchiveExportPins(_ApiBase):
    def test_two_outlets_share_names_and_summary_headers(self):
        self.new_task()
        self.add_pdf("K01")
        self.add_pdf("K02")
        self.add_pdf("K03")
        self.decide({"K01": "include", "K02": "include", "K03": "exclude"})

        archive = self.archive("all")
        self.assertEqual(archive.status_code, 200, archive.text)
        self.assertEqual(archive.headers["x-archive-exported"], "3")
        self.assertEqual(archive.headers["x-archive-skipped"], "0")
        self.assertEqual(archive.headers["x-archive-summary"], "3/0")
        names = _zip_names(archive.content)
        self.assertEqual(names, ["K01-Wang-2021.pdf", "K02-王-2022.pdf",
                                 "K03-unknown-2023.pdf"])

        package = self.package()
        self.assertEqual(package.status_code, 200, package.text)
        package_pdfs = sorted(
            name[len("fulltext/"):] for name in _zip_names(package.content)
            if name.startswith("fulltext/"))
        self.assertEqual(package_pdfs, names)  # 单一事实源：两出口条目名一致

    def test_scope_follows_current_effective_decisions(self):
        self.new_task()
        for key in ("K01", "K02", "K03", "K04"):
            self.add_pdf(key)
        self.decide({"K01": "include", "K02": "include",
                     "K03": "exclude", "K04": "maybe"})
        fulltext_mod.save_stage2(self.owner_db, "K01", "include")
        fulltext_mod.save_stage2(self.owner_db, "K02", "exclude")

        stage1 = self.archive("stage1_include")
        self.assertEqual(stage1.status_code, 200, stage1.text)
        self.assertEqual(sorted(_zip_names(stage1.content)),
                         ["K01-Wang-2021.pdf", "K02-王-2022.pdf"])
        stage2 = self.archive("stage2_include")
        self.assertEqual(stage2.status_code, 200, stage2.text)
        self.assertEqual(_zip_names(stage2.content), ["K01-Wang-2021.pdf"])

    def test_zero_fulltext_is_empty_zip_not_error(self):
        self.new_task()
        response = self.archive("all")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(_zip_names(response.content), [])
        self.assertEqual(response.headers["x-archive-summary"], "0/0")

    def test_missing_pdf_entries_skipped_and_counted(self):
        self.new_task()
        self.add_pdf("K01")
        with closing(sqlite3.connect(self.owner_db)) as conn, conn:
            conn.execute(
                "INSERT INTO fulltexts (zotero_key, filename, path, sha256, n_pages, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("K02", "K02.pdf", "pdfs/missing.pdf", "0" * 64, 1, "2026-01-01"),
            )
        response = self.archive("all")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["x-archive-exported"], "1")
        self.assertEqual(response.headers["x-archive-skipped"], "1")
        self.assertEqual(response.headers["x-archive-summary"], "1/1")
        self.assertIn("missing_file=1", response.headers["x-archive-skipped-reasons"])
        self.assertEqual(_zip_names(response.content), ["K01-Wang-2021.pdf"])

    def test_multi_screener_conflict_arbitration_and_stale_snapshot(self):
        self.new_task()
        self.seed_member_db()
        for key in ("K01", "K02", "K03", "K04"):
            self.add_pdf(key)
        self.decide({"K01": "include", "K02": "include",
                     "K03": "include", "K04": "include"})
        self.decide({"K01": "include", "K02": "exclude",
                     "K03": "include", "K04": "exclude"}, db=self.member_db)

        before = self.archive("stage1_include")
        self.assertEqual(sorted(_zip_names(before.content)),
                         ["K01-Wang-2021.pdf", "K03-unknown-2023.pdf"])

        named = screening_arbitration.load_screener_decision_packages(
            self.info.db_files, stage="stage1")
        merged = screening_arbitration.merge_decision_packages(named)
        screening_arbitration.append_decision_arbitration(
            task_collaboration.shared_store_path(self.info.dir), stage="stage1",
            merged=merged,
            resolutions=[
                {"zotero_key": "K02", "decision": "include", "rationale": "adjudicated"},
                {"zotero_key": "K04", "decision": "include", "rationale": "adjudicated"},
            ],
            arbitrator_id=self.users[0]["user_id"],
        )
        after = self.archive("stage1_include")
        self.assertEqual(sorted(_zip_names(after.content)),
                         ["K01-Wang-2021.pdf", "K02-王-2022.pdf",
                          "K03-unknown-2023.pdf", "K04-unknown-2024.pdf"])

        # 改票后旧仲裁失效：K04 双方互换，快照与当前投票不再逐项相等。
        db_mod.save_decision(self.owner_db, "K04", "exclude", exclusion_reason="changed")
        db_mod.save_decision(self.member_db, "K04", "include")
        stale = self.archive("stage1_include")
        self.assertNotIn("K04-unknown-2024.pdf", _zip_names(stale.content))

    def test_owner_only_permission(self):
        self.new_task()
        self.add_pdf("K01")
        self.assertEqual(self.archive("all", index=1).status_code, 403)
        self.assertEqual(self.archive("all", index=2).status_code, 403)
        self.assertEqual(self.package(index=1).status_code, 403)
        preflight = self.client.get(
            f"/api/tasks/{self.task}/package/preflight", headers=self.auth(1))
        self.assertEqual(preflight.status_code, 403)
        self.assertEqual(preflight.json()["detail"]["error_code"], "write_forbidden")
        outsider = self.package(index=2)
        # 非参与者在全局任务可见性中间件处即被 403 拒绝（既有口径），
        # 不会到达本模块端点。
        self.assertEqual(outsider.status_code, 403)


class PackagePins(_ApiBase):
    def _export(self, **kwargs) -> tuple[bytes, dict]:
        response = self.package(**kwargs)
        self.assertEqual(response.status_code, 200, response.text)
        return response.content, dict(response.headers)

    def test_export_preflight_reports_members_and_sizes(self):
        self.new_task()
        self.add_pdf("K01")
        response = self.client.get(
            f"/api/tasks/{self.task}/package/preflight?include_pdfs=true",
            headers=self.auth())
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        paths = [member["path"] for member in body["members"]]
        owner_slug = tasks_mod.slugify_screener(self.users[0]["username"])
        self.assertIn("task.sanitized.json", paths)
        self.assertIn(f"db/{owner_slug}.db", paths)
        self.assertIn("collaboration.db", paths)
        self.assertIn("fulltext/K01-Wang-2021.pdf", paths)
        self.assertEqual(body["fulltexts"]["count"], 1)
        self.assertFalse(body["over_limit"])

    def test_task_json_redaction_and_zero_credentials_in_package(self):
        self.new_task()
        self.add_pdf("K01")
        task_json = self.info.dir / "task.json"
        payload = json.loads(task_json.read_text(encoding="utf-8"))
        payload.update({
            "api_token": "secret-token-value",
            "password": "hunter2",
            "cwd": "C:\\Users\\someone\\reviewflow",
            "nested": {"jwt": _JWT_SAMPLE, "keep": "ok"},
            "protocol": {"title": "P"},
        })
        task_json.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        data, _headers = self._export()
        sanitized = json.loads(_zip_read(data, "task.sanitized.json").decode("utf-8"))
        self.assertNotIn("api_token", sanitized)
        self.assertNotIn("password", sanitized)
        self.assertNotIn("cwd", sanitized)
        self.assertNotIn("jwt", sanitized.get("nested", {}))
        self.assertEqual(sanitized["nested"]["keep"], "ok")
        self.assertEqual(sanitized["protocol"]["title"], "P")

        manifest = json.loads(_zip_read(data, "manifest.json").decode("utf-8"))
        redacted = set(manifest["redacted_fields"])
        self.assertTrue({"api_token", "password", "cwd", "nested.jwt"} <= redacted)
        self.assertNotIn("secret-token-value", json.dumps(manifest))

        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for name in archive.namelist():
                blob = archive.read(name)
                for sample in _CREDENTIAL_SAMPLES:
                    self.assertNotIn(sample, blob, f"credential leaked in {name}")

    def test_roundtrip_with_pdfs_preserves_fulltexts_and_owner(self):
        self.new_task()
        originals = {key: self.add_pdf(key) for key in ("K01", "K02", "K03")}
        # 旧库可能保留自定义展示名；导入应按 manifest 原名还原登记。
        with closing(sqlite3.connect(self.owner_db)) as conn, conn:
            conn.execute("UPDATE fulltexts SET filename=? WHERE zotero_key=?",
                         ("Original K02.pdf", "K02"))
        self.decide({"K01": "include", "K02": "exclude", "K03": "include"})
        import_history.save_import(
            self.info.dir / "import_history.sqlite3", task_id=self.task,
            screener=self.users[0]["username"], import_type="single",
            stats={"imported": 3}, files=[("sample.ris", b"TY  - JOUR\n")])

        data, headers = self._export()
        self.assertRegex(headers.get("x-package-sha256", ""), r"^[0-9a-f]{64}$")

        imported = self.client.post(
            "/api/tasks/import-package",
            files={"package": ("package.zip", data, "application/zip")},
            data={"preview_sha256": hashlib.sha256(data).hexdigest()},
            headers=self.auth(2))
        self.assertEqual(imported.status_code, 200, imported.text)
        result = imported.json()
        self.assertEqual(result["fulltexts_restored"], 3)

        new_info = tasks_mod.find_task(result["task_id"], DATA_DIR)
        self.assertIsNotNone(new_info)
        new_name = tasks_mod.slugify_screener(self.users[0]["username"])
        self.assertEqual(result["member_slug_map"], {new_name: new_name})
        new_db = new_info.dir / f"{new_name}.db"

        from coscreen.db import list_decisions
        self.assertEqual(
            [(row["zotero_key"], row["decision"]) for row in list_decisions(new_db)],
            [("K01", "include"), ("K02", "exclude"), ("K03", "include")])

        with closing(sqlite3.connect(str(new_db))) as conn:
            rows = conn.execute(
                "SELECT zotero_key, filename, path, sha256, n_pages FROM fulltexts").fetchall()
        self.assertEqual(len(rows), 3)
        by_key = {row[0]: row for row in rows}
        self.assertEqual(by_key["K02"][1], "Original K02.pdf")
        for key, filename, path, sha256, n_pages in rows:
            resolved = tasks_mod.resolve_fulltext_raw(path, new_db, data_dir=DATA_DIR)
            self.assertIsNotNone(resolved, f"{key} 的 PDF 未在导入后落盘")
            self.assertEqual(Path(resolved).read_bytes(), originals[key])
            self.assertEqual(len(sha256), 64)
            self.assertGreaterEqual(n_pages, 1)
            # 内部仍是 safe_pdf 单射命名（规范名只存在于导出副本）。
            self.assertNotIn("-Wang-", Path(path).name)

        owner = task_collaboration.get_owner(
            task_collaboration.shared_store_path(new_info.dir),
            task_id=result["task_id"])
        self.assertEqual(owner["owner_user_id"], self.users[2]["user_id"])
        self.assertEqual(owner["owner_username"], self.users[2]["username"])

        with closing(sqlite3.connect(str(new_info.dir / "import_history.sqlite3"))) as conn:
            history = conn.execute("SELECT task_id, screener FROM imports").fetchall()
        self.assertEqual(history, [(result["task_id"], new_name)])

    def test_roundtrip_without_pdfs_keeps_metadata(self):
        self.new_task()
        for key in ("K01", "K02"):
            self.add_pdf(key)
        self.decide({"K01": "include", "K02": "include"})

        data, _headers = self._export(include_pdfs=False)
        self.assertFalse(any(name.startswith("fulltext/")
                             for name in _zip_names(data)))
        imported = self.client.post(
            "/api/tasks/import-package",
            files={"package": ("package.zip", data, "application/zip")},
            headers=self.auth(2))
        self.assertEqual(imported.status_code, 200, imported.text)
        result = imported.json()
        self.assertEqual(result["fulltexts_restored"], 0)

        new_info = tasks_mod.find_task(result["task_id"], DATA_DIR)
        new_db = new_info.dir / f"{tasks_mod.slugify_screener(self.users[0]['username'])}.db"
        with closing(sqlite3.connect(str(new_db))) as conn:
            rows = conn.execute(
                "SELECT zotero_key, filename, sha256, n_pages FROM fulltexts "
                "ORDER BY zotero_key").fetchall()
        self.assertEqual([row[0] for row in rows], ["K01", "K02"])
        self.assertFalse((new_info.dir / "pdfs").exists())

    def test_import_preflight_is_read_only(self):
        self.new_task()
        self.add_pdf("K01")
        data, _headers = self._export()
        before = _task_dir_count()
        response = self.client.post(
            "/api/tasks/import-package/preflight",
            files={"package": ("package.zip", data, "application/zip")},
            headers=self.auth(2))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["package_sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(body["source_task"]["task_id"], self.task)
        self.assertEqual(_task_dir_count(), before)

    def test_tampered_package_rejected_atomically(self):
        self.new_task()
        self.add_pdf("K01")
        data, _headers = self._export()
        before = _task_dir_count()
        with zipfile.ZipFile(io.BytesIO(data)) as source:
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as target:
                for info in source.infolist():
                    blob = source.read(info.filename)
                    if info.filename.startswith("db/"):
                        blob = blob + b"tampered"
                    target.writestr(info, blob)
        response = self.client.post(
            "/api/tasks/import-package",
            files={"package": ("bad.zip", buffer.getvalue(), "application/zip")},
            headers=self.auth(2))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["error_code"],
                         "package_checksum_mismatch")
        self.assertEqual(_task_dir_count(), before)
        leftovers = [p for p in tasks_mod.tasks_root(DATA_DIR).iterdir()
                     if p.name.startswith(".import-")]
        self.assertEqual(leftovers, [])

    def test_truncated_zip_is_structured_400(self):
        self.new_task()
        response = self.client.post(
            "/api/tasks/import-package",
            files={"package": ("bad.zip", b"PK\x03\x04broken", "application/zip")},
            headers=self.auth(2))
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["error_code"], "package_invalid")

    def test_empty_upload_is_structured_400(self):
        before = _task_dir_count()
        response = self.client.post(
            "/api/tasks/import-package",
            files={"package": ("empty.zip", b"", "application/zip")},
            headers=self.auth(2))
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["error_code"], "package_invalid")
        self.assertEqual(_task_dir_count(), before)

    def test_preview_sha256_mismatch_rejected_before_write(self):
        self.new_task()
        self.add_pdf("K01")
        data, _headers = self._export()
        before = _task_dir_count()
        response = self.client.post(
            "/api/tasks/import-package",
            files={"package": ("package.zip", data, "application/zip")},
            data={"preview_sha256": "0" * 64},
            headers=self.auth(2))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["error_code"],
                         "package_preview_mismatch")
        self.assertEqual(_task_dir_count(), before)

    def test_same_package_imported_twice_is_two_independent_tasks(self):
        self.new_task()
        self.add_pdf("K01")
        data, _headers = self._export()
        before = _task_dir_count()
        ids = []
        for _ in range(2):
            response = self.client.post(
                "/api/tasks/import-package",
                files={"package": ("package.zip", data, "application/zip")},
                headers=self.auth(2))
            self.assertEqual(response.status_code, 200, response.text)
            ids.append(response.json()["task_id"])
        self.assertEqual(len(set(ids)), 2)
        self.assertEqual(_task_dir_count(), before + 2)
        for task_id in ids:
            info = tasks_mod.find_task(task_id, DATA_DIR)
            self.assertTrue((info.dir / "task.json").is_file())
            self.assertTrue((info.dir / "collaboration.db").is_file())

    def test_repeated_export_has_zero_side_effects_on_source(self):
        self.new_task()
        self.add_pdf("K01")
        self.decide({"K01": "include"})

        def logical_files() -> list[str]:
            # 只读快照可能让 SQLite 生成/保留 -wal/-shm 附属文件（引擎行为，
            # 不承载数据）；数据文件集合与内容必须逐次一致。
            return sorted(p.name for p in self.info.dir.iterdir()
                          if not p.name.endswith(("-wal", "-shm")))

        before = logical_files()
        first, _ = self._export()
        second, _ = self._export()
        self.assertEqual(_zip_names(first), _zip_names(second))
        self.assertEqual(logical_files(), before)
        self.assertEqual(
            [(row["zotero_key"], row["decision"]) for row in db_mod.list_decisions(self.owner_db)],
            [("K01", "include")])
        self.assertEqual(
            [row["zotero_key"] for row in tasks_mod.iter_fulltexts(self.owner_db)],
            ["K01"])

    def test_over_limit_returns_structured_413(self):
        self.new_task()
        self.add_pdf("K01")
        original = task_package_api.MAX_PACKAGE_BYTES
        try:
            task_package_api.MAX_PACKAGE_BYTES = 64
            response = self.package()
            self.assertEqual(response.status_code, 413, response.text)
            detail = response.json()["detail"]
            self.assertEqual(detail["error_code"], "package_too_large")
            self.assertEqual(detail["params"]["limit_bytes"], 64)
            self.assertEqual(detail["params"]["pdf_count"], 1)
            self.assertGreater(detail["params"]["pdf_bytes"], 0)
        finally:
            task_package_api.MAX_PACKAGE_BYTES = original
        # 恢复上限后同一任务仍可正常导出（降级不破坏状态）。
        self.assertEqual(self.package().status_code, 200)


# ---------------------------------------------------------------------------
# 终审 #41 / #39 安全钉（流式解压三限额 · 60 分钟 TTL 清扫 · 全局整包互斥 ·
# 未覆盖 fulltexts 行处置）。变异钉沿 test_2_1_5_notify_sec_pins.py 既有型：
# 运行期 monkeypatch 去掉防护 → 对应绿钉描述的行为必然翻红（危险真会发生）。
# ---------------------------------------------------------------------------

_MIB = 1024 * 1024
_SQLITE_MAGIC = b"SQLite format 3\x00"


def _build_raw_package(members: dict, *, fulltexts=None, compress=None) -> bytes:
    """直接构造整包 zip（manifest 可选谎报 fulltexts 尺寸——攻击者可控面）。"""
    manifest = {
        "schema": task_package_api.PACKAGE_SCHEMA,
        "source_task": {"task_id": "src", "display_name": "s",
                        "screeners": ["member"]},
        "members": [{"path": name, "sha256": hashlib.sha256(blob).hexdigest(),
                     "size": len(blob)} for name, blob in members.items()],
        "fulltexts": fulltexts or [],
        "skipped_fulltexts": [],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, blob in members.items():
            archive.writestr(name, blob, compress_type=(
                compress or {}).get(name, zipfile.ZIP_DEFLATED))
        archive.writestr("manifest.json", json.dumps(manifest).encode())
    return buffer.getvalue()


def _bomb_members(member_mib: int = 16, pad: int = 48 * 1024) -> dict:
    """高压缩比 db 成员 + stored 垫片（垫高头部总压缩量，掩护单成员炸弹）。"""
    return {
        "task.sanitized.json": b'{"task_id":"t","display_name":"T"}',
        "db/x.db": _SQLITE_MAGIC + b"\x00" * (member_mib * _MIB - 16),
        "fulltext/pad.pdf": os.urandom(pad),
    }


_BOMB_COMPRESS = {"db/x.db": zipfile.ZIP_BZIP2,
                  "fulltext/pad.pdf": zipfile.ZIP_STORED}


def _rename_members(data: bytes, rename: dict, manifest_transform=None) -> bytes:
    """重打包：按 ``rename`` 改成员名；``manifest_transform`` 就地改 manifest。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(
            buffer, "w", compression=zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            payload = src.read(info.filename)
            name = rename.get(info.filename, info.filename)
            if info.filename == "manifest.json" and manifest_transform is not None:
                manifest = json.loads(payload.decode("utf-8"))
                manifest_transform(manifest)
                payload = (json.dumps(manifest, ensure_ascii=False, indent=2,
                                      sort_keys=True) + "\n").encode("utf-8")
                name = "manifest.json"
            new_info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            new_info.compress_type = zipfile.ZIP_DEFLATED
            new_info.external_attr = 0o600 << 16
            dst.writestr(new_info, payload)
    return buffer.getvalue()


class SecurityLimitsPins(_ApiBase):
    """三限额各一钉 + 谎报 PDF 尺寸 + 流式内存上界 + 变异钉。"""

    def _import_raw(self, payload: bytes):
        return self.client.post(
            "/api/tasks/import-package",
            files={"package": ("pkg.zip", payload, "application/zip")},
            headers=self.auth(2))

    def test_bomb_package_rejected_structured_and_cleaned(self):
        self.new_task()
        before = _task_dir_count()
        bomb = _build_raw_package(_bomb_members(16), compress=_BOMB_COMPRESS)
        response = self._import_raw(bomb)
        self.assertEqual(response.status_code, 400, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["error_code"], "package_invalid")
        self.assertEqual(detail["message_key"], "task_package.package_invalid")
        self.assertEqual(detail["params"]["reason"], "compression_ratio")
        self.assertEqual(_task_dir_count(), before)
        root = tasks_mod.tasks_root(DATA_DIR)
        self.assertEqual([p for p in root.iterdir() if p.name.startswith(".import-")],
                         [], "恶意包拒绝后必须清场（零 .import- 残留）")

    def test_member_cap_limit_pinned(self):
        self.new_task()
        members = {
            "task.sanitized.json": b'{"task_id":"t","display_name":"T"}',
            "db/a.db": _SQLITE_MAGIC + os.urandom(32 * 1024),
            "fulltext/big.pdf": os.urandom(8 * _MIB + 512 * 1024),
        }
        payload = _build_raw_package(members)
        original = task_package_api.MAX_MEMBER_BYTES
        task_package_api.MAX_MEMBER_BYTES = 8 * _MIB
        try:
            response = self._import_raw(payload)
        finally:
            task_package_api.MAX_MEMBER_BYTES = original
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["params"]["reason"],
                         "member_too_large")
        self.assertEqual(response.json()["detail"]["params"]["member"],
                         "fulltext/big.pdf")

    def test_total_uncompressed_limit_pinned(self):
        self.new_task()
        members = {
            "task.sanitized.json": b'{"task_id":"t","display_name":"T"}',
            "db/a.db": _SQLITE_MAGIC + os.urandom(2 * _MIB),
            "fulltext/p1.pdf": os.urandom(2 * _MIB),
            "fulltext/p2.pdf": os.urandom(2 * _MIB),
        }
        payload = _build_raw_package(members)
        original = task_package_api.MAX_UNCOMPRESSED_BYTES
        task_package_api.MAX_UNCOMPRESSED_BYTES = 5 * _MIB
        try:
            response = self._import_raw(payload)
        finally:
            task_package_api.MAX_UNCOMPRESSED_BYTES = original
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["params"]["reason"],
                         "uncompressed_too_large")

    def test_ratio_limit_pinned(self):
        self.new_task()
        payload = _build_raw_package(_bomb_members(8), compress=_BOMB_COMPRESS)
        original = task_package_api.MAX_COMPRESSION_RATIO
        task_package_api.MAX_COMPRESSION_RATIO = 500
        try:
            response = self._import_raw(payload)
        finally:
            task_package_api.MAX_COMPRESSION_RATIO = original
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["params"]["reason"],
                         "compression_ratio")

    def test_actual_byte_limit_enforced_beyond_declared(self):
        """实际字节这道闸独立成立：声明值预检被绕过（猴补为 no-op）时，
        流式解压仍按实际字节拒绝——去掉实际闸这颗钉必红。"""
        self.new_task()
        members = {
            "task.sanitized.json": b'{"task_id":"t","display_name":"T"}',
            "db/a.db": _SQLITE_MAGIC + os.urandom(6 * _MIB),
        }
        payload = _build_raw_package(members)
        original_cap = task_package_api.MAX_MEMBER_BYTES
        task_package_api.MAX_MEMBER_BYTES = 3 * _MIB
        try:
            with mock.patch.object(task_package_api, "_check_member_declared",
                                   lambda info: None):
                response = self._import_raw(payload)
        finally:
            task_package_api.MAX_MEMBER_BYTES = original_cap
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["params"]["reason"],
                         "member_too_large")

    def test_pdf_size_lie_rejected(self):
        """fulltexts[].size 谎报（1 vs 实际成员 512KiB）→ pdf_size_mismatch。"""
        self.new_task()
        real = os.urandom(512 * 1024)
        members = {
            "task.sanitized.json": b'{"task_id":"t","display_name":"T"}',
            "db/a.db": _SQLITE_MAGIC + os.urandom(32 * 1024),
            "fulltext/big.pdf": real,
        }
        payload = _build_raw_package(members, fulltexts=[{
            "zotero_key": "K1", "archive_path": "fulltext/big.pdf",
            "sha256": hashlib.sha256(real).hexdigest(), "size": 1,
            "original_filename": "big.pdf"}])
        response = self._import_raw(payload)
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["params"]["reason"],
                         "pdf_size_mismatch")

    def test_streaming_import_memory_bounded(self):
        """诚实 8MiB 成员导入：流式解压内存峰值不得随成员尺寸线性放大。"""
        self.new_task()
        members = {
            "task.sanitized.json": b'{"task_id":"t","display_name":"T"}',
            "db/alice.db": _SQLITE_MAGIC + b"\x00" * (8 * _MIB - 16),
        }
        payload = _build_raw_package(members)
        import tracemalloc
        tracemalloc.start()
        try:
            response = self._import_raw(payload)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertLess(peak, 6 * _MIB,
                        f"流式解压峰值应保持块级（实测 {peak / _MIB:.1f}MiB；"
                        f"整块读 8MiB 成员至少翻倍到 ≥8MiB）")

    def test_mutation_limits_removed_pin_goes_red(self):
        """破坏性变异：三限额两道闸全删（声明/实际检查 no-op）→ 炸弹包导入
        200 且 16MiB 落盘——test_bomb_package_rejected… 必红（防护是承重墙）。"""
        self.new_task()
        payload = _build_raw_package(_bomb_members(16), compress=_BOMB_COMPRESS)
        before = _task_dir_count()
        with mock.patch.object(task_package_api, "_check_member_declared",
                               lambda info: None), \
             mock.patch.object(task_package_api, "_check_member_actual",
                               lambda info, size, total: None):
            response = self._import_raw(payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(_task_dir_count(), before + 1, "无防护时炸弹真实落盘")


class TempTTLAndMutexPins(_ApiBase):
    """60 分钟临时保留上限（惰性清扫）+ 全局整包操作互斥 + 变异钉。"""

    def _stale_mtime(self):
        return time.time() - (task_package_api.PACKAGE_TEMP_TTL_SECONDS + 60)

    def test_ttl_sweep_removes_stale_keeps_fresh(self):
        self.new_task()
        temp_root = Path(tempfile.gettempdir())
        stale = []
        for prefix in task_package_api._PACKAGE_TEMP_DIR_PREFIXES:
            path = temp_root / (prefix + "sec2-w5-stale")
            path.mkdir(exist_ok=True)
            (path / "package.zip").write_bytes(b"stale")
            os.utime(path, (self._stale_mtime(), self._stale_mtime()))
            stale.append(path)
        tasks_root = Path(tasks_mod.tasks_root(DATA_DIR))
        stale_import = tasks_root / ".import-sec2-w5-stale"
        stale_import.mkdir(parents=True, exist_ok=True)
        (stale_import / "x.db").write_bytes(b"stale")
        os.utime(stale_import, (self._stale_mtime(), self._stale_mtime()))
        stale.append(stale_import)
        fresh = temp_root / "rf-package-export-sec2-w5-fresh"
        fresh.mkdir(exist_ok=True)
        (fresh / "in-flight.zip").write_bytes(b"fresh")
        fresh_time = time.time() - (
            task_package_api.PACKAGE_TEMP_TTL_SECONDS - 60)
        os.utime(fresh, (fresh_time, fresh_time))
        try:
            response = self.client.get(f"/api/tasks/{self.task}/package/preflight",
                                       headers=self.auth())
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual([p for p in stale if p.exists()], [],
                             "61 分钟孤儿必须被任一整包端点触达后的清扫删除")
            self.assertTrue(fresh.exists(), "60 分钟内在途文件不得误删")
        finally:
            for path in [*stale, fresh]:
                shutil.rmtree(path, ignore_errors=True)

    def test_mutation_sweep_removed_pin_goes_red(self):
        """破坏性变异：清扫删掉（no-op）→ 孤儿全在——上一颗钉必红。"""
        self.new_task()
        temp_root = Path(tempfile.gettempdir())
        stale = temp_root / "rf-package-export-sec2-w5-mut"
        stale.mkdir(exist_ok=True)
        (stale / "package.zip").write_bytes(b"stale")
        os.utime(stale, (self._stale_mtime(), self._stale_mtime()))
        try:
            with mock.patch.object(task_package_api, "_sweep_stale_package_temp",
                                   lambda *args, **kwargs: 0):
                response = self.client.get(
                    f"/api/tasks/{self.task}/package/preflight", headers=self.auth())
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(stale.exists(), "无清扫时 61 分钟孤儿真实残留")
        finally:
            shutil.rmtree(stale, ignore_errors=True)

    def test_import_mutex_second_operation_409_shared_with_exports(self):
        self.new_task()
        lock = task_package_api._PACKAGE_OP_LOCK
        self.assertTrue(lock.acquire(blocking=False))
        try:
            import_response = self.client.post(
                "/api/tasks/import-package",
                files={"package": ("p.zip", b"anything", "application/zip")},
                headers=self.auth(2))
            self.assertEqual(import_response.status_code, 409, import_response.text)
            self.assertEqual(
                import_response.json()["detail"]["message_key"],
                "task_package.operation_busy")
            export = self.package()
            archive = self.archive("all")
            self.assertEqual(export.status_code, 409, export.text)
            self.assertEqual(archive.status_code, 409, archive.text)
        finally:
            lock.release()
        # 锁释放后操作恢复正常（互斥不留下永久占用）。
        self.assertEqual(self.package().status_code, 200)

    def test_concurrent_second_import_409_first_ok(self):
        self.new_task()
        self.add_pdf("K01")
        data, _headers = self.package(), None
        payload = data.content
        entered = threading.Event()
        release = threading.Event()
        original = task_package_api._apply_import
        results = {}

        def slow_apply(*args, **kwargs):
            entered.set()
            release.wait(timeout=30)
            return original(*args, **kwargs)

        with mock.patch.object(task_package_api, "_apply_import", slow_apply):
            thread = threading.Thread(target=lambda: results.__setitem__(
                "first", self.client.post(
                    "/api/tasks/import-package",
                    files={"package": ("pkg.zip", payload, "application/zip")},
                    headers=self.auth(2))))
            thread.start()
            try:
                self.assertTrue(entered.wait(timeout=15), "第一个导入未进入落盘阶段")
                results["second"] = self.client.post(
                    "/api/tasks/import-package",
                    files={"package": ("pkg2.zip", payload, "application/zip")},
                    headers=self.auth(2))
            finally:
                release.set()
                thread.join(timeout=60)
        self.assertEqual(results["second"].status_code, 409, results["second"].text)
        self.assertEqual(results["first"].status_code, 200, results["first"].text)

    def test_mutation_mutex_removed_pin_goes_red(self):
        """破坏性变异：互斥锁换成恒成功假锁（acquire 恒 True / release 空转）→
        第二导入并发放行（200）——test_concurrent_second_import_409_first_ok 必红。"""
        self.new_task()
        self.add_pdf("K01")
        payload = self.package().content
        entered = threading.Event()
        release = threading.Event()
        original = task_package_api._apply_import
        results = {}

        class _BrokenLock:
            def acquire(self, blocking=True):
                return True

            def release(self):
                pass

        def slow_apply(*args, **kwargs):
            entered.set()
            release.wait(timeout=30)
            return original(*args, **kwargs)

        with mock.patch.object(task_package_api, "_PACKAGE_OP_LOCK",
                               _BrokenLock()), \
             mock.patch.object(task_package_api, "_apply_import", slow_apply):
            thread = threading.Thread(target=lambda: results.__setitem__(
                "first", self.client.post(
                    "/api/tasks/import-package",
                    files={"package": ("pkg.zip", payload, "application/zip")},
                    headers=self.auth(2))))
            thread.start()
            try:
                self.assertTrue(entered.wait(timeout=15))
                results["second"] = self.client.post(
                    "/api/tasks/import-package",
                    files={"package": ("pkg2.zip", payload, "application/zip")},
                    headers=self.auth(2))
            finally:
                release.set()
                thread.join(timeout=60)
        # 无互斥时第二个导入被放行（危险行为真实发生——并发放大 + 6GB 叠加）。
        self.assertEqual(results["second"].status_code, 200, results["second"].text)

    def test_client_interrupt_cleans_workdir_and_releases_lock(self):
        """客户端中断下载：zip/工作目录即刻删除、互斥锁即刻释放。"""
        self.new_task()
        self.add_pdf("K01")
        lock = task_package_api._PACKAGE_OP_LOCK
        with self.client.stream("POST",
                                f"/api/tasks/{self.task}/package/export",
                                json={"include_pdfs": True},
                                headers=self.auth()) as response:
            self.assertEqual(response.status_code, 200)
            for _chunk in response.iter_bytes(chunk_size=1024):
                break  # 拿到首块即中断（模拟客户端断开）
        deadline = time.time() + 10
        while time.time() < deadline:
            if lock.acquire(blocking=False):
                lock.release()
                break
            time.sleep(0.05)
        else:
            self.fail("客户端中断后整包操作锁未释放（10s 内）")
        leftovers = [p.name for p in Path(tempfile.gettempdir()).iterdir()
                     if p.name.startswith("rf-package-export-")
                     and p.stat().st_mtime > time.time() - 60]
        self.assertEqual(leftovers, [], "中断后工作目录必须即刻删除")
        # 锁已回来：下一个整包操作立即可用。
        self.assertEqual(self.package().status_code, 200)


class UncoveredFulltextsPins(_ApiBase):
    """终审 #39：manifest 未覆盖的 fulltexts 行不得指向共享池读他人文件。

    攻击面 = 攻击者**自造**整包（db 里塞指向 {data_dir}/pdfs 共享平铺池的
    fulltexts 行，manifest 只诚实覆盖一个无害 key）——导入后两个导出出口都
    会把池内他人/遗留文件当作本任务全文导出。
    """

    def _attacker_package(self, victim_name: str) -> tuple[bytes, bytes]:
        victim = Path(DATA_DIR) / "pdfs" / victim_name
        victim.parent.mkdir(parents=True, exist_ok=True)
        victim_bytes = ("%PDF-1.4\nLEGACY-SECRET-"
                        + victim_name + "\n%%EOF\n").encode()
        victim.write_bytes(victim_bytes)
        self.addCleanup(lambda: victim.unlink(missing_ok=True))

        db_path = Path(self.info.dir) / "sec2-attacker.db"
        db_mod.init_db(db_path)
        with closing(sqlite3.connect(db_path)) as conn, conn:
            for key, authors, year in (("A1", "Legit, L", 2021),
                                       ("VICTIM", "Victim, V", 2019),
                                       ("GHOST", "Ghost, G", 2018)):
                conn.execute(
                    "INSERT INTO articles (zotero_key, title, authors, year) "
                    "VALUES (?, 't', ?, ?)", (key, authors, year))

            def ft(key, path):
                conn.execute(
                    "INSERT INTO fulltexts (zotero_key, filename, path, sha256, "
                    "n_pages, added_at) VALUES (?, ?, ?, ?, 1, '2026-01-01')",
                    (key, key + ".pdf", path, "0" * 64))
            ft("A1", "pdfs/placeholder.pdf")
            # VICTIM 用绝对路径（存储层 fulltexts.path 的真实形态，#43 证实），
            # GHOST 用 data/pdfs 相对写法——两种历史形态都要被中和。
            ft("VICTIM", str(victim))
            ft("GHOST", f"data/pdfs/missing_{victim_name}")
        legit = make_pdf("A1")
        members = {
            "task.sanitized.json": json.dumps(
                {"task_id": "src", "display_name": "s"}).encode(),
            "db/sec2-attacker.db": db_path.read_bytes(),
            "fulltext/A1-Legit-2021.pdf": legit,
        }
        payload = _build_raw_package(members, fulltexts=[{
            "zotero_key": "A1", "archive_path": "fulltext/A1-Legit-2021.pdf",
            "sha256": hashlib.sha256(legit).hexdigest(), "size": len(legit),
            "original_filename": "A1.pdf"}])
        return payload, victim_bytes

    def test_uncovered_fulltexts_rows_neutralized(self):
        self.new_task()
        payload, victim_bytes = self._attacker_package("legacy_sec2_K9.pdf")
        imported = self.client.post(
            "/api/tasks/import-package",
            files={"package": ("pkg.zip", payload, "application/zip")},
            headers=self.auth(2))
        self.assertEqual(imported.status_code, 200, imported.text)
        result = imported.json()
        self.assertEqual(result["fulltexts_restored"], 1)
        self.assertGreaterEqual(result["fulltexts_neutralized"], 2,
                                "VICTIM 与 GHOST 两行未覆盖路径必须被中和")

        new_task = result["task_id"]
        new_info = tasks_mod.find_task(new_task, DATA_DIR)
        new_slug = result["member_slug_map"]["sec2-attacker"]
        with closing(sqlite3.connect(str(new_info.dir / f"{new_slug}.db"))) as conn:
            rows = dict(conn.execute(
                "SELECT zotero_key, path FROM fulltexts").fetchall())
        self.assertEqual(rows.get("VICTIM"), "", "共享池相对路径必须清空")
        self.assertEqual(rows.get("GHOST"), "")
        self.assertTrue(rows.get("A1"), "manifest 覆盖的 key 不受影响")

        for export_path, kwargs in (
                (f"/api/tasks/{new_task}/package/export", {"json": {"include_pdfs": True}}),
                (f"/api/tasks/{new_task}/fulltext/archive-export", {"json": {"scope": "all"}})):
            exported = self.client.post(export_path, headers=self.auth(2), **kwargs)
            self.assertEqual(exported.status_code, 200, exported.text)
            with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
                blob = b"".join(archive.read(name) for name in archive.namelist())
            self.assertNotIn(victim_bytes, blob,
                             f"共享池他人文件绝不被导出：{export_path}")

    def test_mutation_neutralize_removed_pin_goes_red(self):
        """破坏性变异：中和删掉（no-op）→ 未覆盖行原样保留（fulltexts_neutralized=0）——
        test_uncovered_fulltexts_rows_neutralized 的行清空/计数断言必红。导出侧的
        纵深（仅任务目录根）由 HostileSchemaAndExportRootPins 单独钉住。"""
        self.new_task()
        payload, victim_bytes = self._attacker_package("legacy_sec2_mut.pdf")
        with mock.patch.object(task_package_api,
                               "_neutralize_unrestored_fulltexts",
                               lambda base, names, keys: 0):
            imported = self.client.post(
                "/api/tasks/import-package",
                files={"package": ("pkg.zip", payload, "application/zip")},
                headers=self.auth(2))
        self.assertEqual(imported.status_code, 200, imported.text)
        result = imported.json()
        self.assertEqual(result["fulltexts_neutralized"], 0)
        new_info = tasks_mod.find_task(result["task_id"], DATA_DIR)
        new_slug = result["member_slug_map"]["sec2-attacker"]
        with closing(sqlite3.connect(str(new_info.dir / f"{new_slug}.db"))) as conn:
            rows = dict(conn.execute(
                "SELECT zotero_key, path FROM fulltexts").fetchall())
        self.assertTrue(rows.get("VICTIM"), "无中和时共享池路径真实保留（钉必红）")


def _patch_central(raw: bytes, member: str, *, file_size=None) -> bytes:
    """伪造 central directory 的声明尺寸（评审 rev_41_zipcorrupt.py 同技法）。"""
    import struct

    data = bytearray(raw)
    idx = 0
    while True:
        sig = data.find(b"PK\x01\x02", idx)
        assert sig >= 0, f"central entry {member} not found"
        name_len = struct.unpack_from("<H", data, sig + 28)[0]
        name = bytes(data[sig + 46:sig + 46 + name_len]).decode("utf-8", "replace")
        if name == member:
            struct.pack_into("<I", data, sig + 24, file_size)
            break
        idx = sig + 4
    return bytes(data)


class HostileSchemaAndExportRootPins(_ApiBase):
    """终审 #39 r2（对抗复核）：自造库 trigger/view/BLOB 对抗 + 导出根收窄。"""

    def _hostile_package(self, mode, *, cover=False):
        db_path = Path(self.info.dir) / f"sec2-{mode}-{int(cover)}.db"
        db_mod.init_db(db_path)
        with closing(sqlite3.connect(db_path)) as conn, conn:
            conn.execute(
                "INSERT INTO articles (zotero_key, title, authors, year) "
                "VALUES (?, 't', 'Victim, V', 2019)", ("VICTIM",))
            row = ("VICTIM", "V.pdf", "data/pdfs/legacy_sec2_hostile.pdf",
                   "0" * 64, 1, "2026-01-01")
            if mode in ("plain", "trigger"):
                conn.execute(
                    "INSERT INTO fulltexts (zotero_key, filename, path, sha256, "
                    "n_pages, added_at) VALUES (?,?,?,?,?,?)", row)
            if mode == "trigger":
                conn.execute(
                    "CREATE TRIGGER blk BEFORE UPDATE ON fulltexts "
                    "BEGIN SELECT RAISE(IGNORE); END")
            elif mode == "view":
                conn.execute(
                    "CREATE TABLE evil_ft (zotero_key TEXT PRIMARY KEY, "
                    "filename TEXT, path TEXT, sha256 TEXT, n_pages INTEGER, "
                    "added_at TEXT)")
                conn.execute("INSERT INTO evil_ft VALUES (?,?,?,?,?,?)", row)
                conn.execute("DROP TABLE fulltexts")
                conn.execute(
                    "CREATE VIEW fulltexts AS SELECT zotero_key, filename, path, "
                    "sha256, n_pages, added_at FROM evil_ft")
        members = {
            "task.sanitized.json": json.dumps(
                {"task_id": "src", "display_name": "s"}).encode(),
            "db/sec2-attacker.db": db_path.read_bytes(),
        }
        fulltexts = []
        if cover:
            legit = make_pdf("A1")
            members["fulltext/A1.pdf"] = legit
            fulltexts = [{"zotero_key": "VICTIM",
                          "archive_path": "fulltext/A1.pdf",
                          "sha256": hashlib.sha256(legit).hexdigest(),
                          "size": len(legit), "original_filename": "A1.pdf"}]
        return _build_raw_package(members, fulltexts=fulltexts)

    def _import_leftovers(self):
        root = tasks_mod.tasks_root(DATA_DIR)
        if not root.is_dir():
            return []
        return [p.name for p in root.iterdir() if p.name.startswith(".import-")]

    def test_trigger_and_view_schemas_rejected_not_silently_bypassed(self):
        self.new_task()
        for mode in ("trigger", "view"):
            with self.subTest(mode=mode):
                response = self.client.post(
                    "/api/tasks/import-package",
                    files={"package": ("pkg.zip", self._hostile_package(mode),
                                       "application/zip")},
                    headers=self.auth(2))
                self.assertEqual(response.status_code, 400, response.text)
                detail = response.json()["detail"]
                self.assertEqual(detail["error_code"], "package_invalid")
                self.assertEqual(detail["message_key"],
                                 "task_package.package_invalid")
                self.assertEqual(detail["params"]["reason"],
                                 "fulltext_neutralize_failed")
                self.assertEqual(self._import_leftovers(), [],
                                 "拒绝后必须清场")

    def test_covered_key_rewrite_resists_trigger(self):
        """manifest 覆盖的 key 改写被触发器挡住 → 拒绝（rowcount 校验）。"""
        self.new_task()
        response = self.client.post(
            "/api/tasks/import-package",
            files={"package": ("pkg.zip",
                               self._hostile_package("trigger", cover=True),
                               "application/zip")},
            headers=self.auth(2))
        self.assertEqual(response.status_code, 400, response.text)
        detail = response.json()["detail"]
        self.assertIn(detail["params"]["reason"],
                      ("fulltext_rewrite", "fulltext_neutralize_failed"))
        self.assertEqual(self._import_leftovers(), [])

    def test_blob_key_rows_neutralized_with_honest_count(self):
        """BLOB 形态 zotero_key：原始对象做参数 → 行真实清空、计数如实（r2）。"""
        self.new_task()
        db_path = Path(self.info.dir) / "sec2-blob.db"
        db_mod.init_db(db_path)
        with closing(sqlite3.connect(db_path)) as conn, conn:
            conn.execute(
                "INSERT INTO articles (zotero_key, title, authors, year) "
                "VALUES (?, 't', 'B, B', 2020)", (b"AB",))
            conn.execute(
                "INSERT INTO fulltexts (zotero_key, filename, path, sha256, "
                "n_pages, added_at) VALUES (?,?,?,?,?,?)",
                (b"AB", "B.pdf", "data/pdfs/legacy_sec2_blob.pdf", "0" * 64,
                 1, "2026-01-01"))
        members = {
            "task.sanitized.json": json.dumps(
                {"task_id": "src", "display_name": "s"}).encode(),
            "db/sec2-attacker.db": db_path.read_bytes(),
        }
        response = self.client.post(
            "/api/tasks/import-package",
            files={"package": ("pkg.zip", _build_raw_package(members),
                               "application/zip")},
            headers=self.auth(2))
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["fulltexts_neutralized"], 1,
                         "计数必须等于实际清空的行数（r2 计数 bug 修复）")
        new_info = tasks_mod.find_task(result["task_id"], DATA_DIR)
        new_slug = result["member_slug_map"]["sec2-attacker"]
        with closing(sqlite3.connect(str(new_info.dir / f"{new_slug}.db"))) as conn:
            rows = conn.execute(
                "SELECT typeof(zotero_key), path FROM fulltexts").fetchall()
        self.assertEqual(rows, [("blob", "")], "BLOB 键行 path 必须真实清空")

    def test_export_skips_rows_pointing_outside_task_dir(self):
        """导出纵深：fulltexts.path 指向共享平铺池的真实文件 → 两出口一律
        missing_file 跳过、绝不带出字节（合法根=仅任务目录，r2 收窄）。"""
        self.new_task()
        self.add_pdf("K01")
        pool = Path(DATA_DIR) / "pdfs"
        pool.mkdir(parents=True, exist_ok=True)
        secret = b"%PDF-1.4\nR2-POOL-SECRET\n%%EOF\n"
        victim = pool / "legacy_sec2_pool.pdf"
        victim.write_bytes(secret)
        self.addCleanup(lambda: victim.unlink(missing_ok=True))
        with closing(sqlite3.connect(self.owner_db)) as conn, conn:
            conn.execute(
                "INSERT INTO articles (zotero_key, title, authors, year) "
                "VALUES ('POOLX', 'T2', 'Wang, A', 2021)")
            conn.execute(
                "INSERT INTO fulltexts (zotero_key, filename, path, sha256, "
                "n_pages, added_at) VALUES ('POOLX', 'POOLX.pdf', ?, ?, 1, "
                "'2026-01-01')", (str(victim), "0" * 64))
        for path, kwargs in (
                (f"/api/tasks/{self.task}/fulltext/archive-export",
                 {"json": {"scope": "all"}}),
                (f"/api/tasks/{self.task}/package/export",
                 {"json": {"include_pdfs": True}})):
            with self.subTest(outlet=path):
                response = self.client.post(path, headers=self.auth(), **kwargs)
                self.assertEqual(response.status_code, 200, response.text)
                with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                    blob = b"".join(archive.read(n) for n in archive.namelist())
                self.assertNotIn(secret, blob)
        archive = self.client.post(
            f"/api/tasks/{self.task}/fulltext/archive-export",
            json={"scope": "all"}, headers=self.auth())
        self.assertEqual(archive.headers["x-archive-summary"], "1/1",
                         "池内行必须计 missing_file 跳过")

    def test_mutation_rowcheck_removed_pin_goes_red(self):
        """破坏性变异：中和换成无 rowcount 校验版（str 参数 + 不查行数）→
        trigger 包导入 200 且行保留——test_trigger_and_view… 必红。"""
        self.new_task()

        def _no_check(base, names, restored_keys):
            keep = {str(key) for key in restored_keys}
            for name in names:
                try:
                    target = task_package_api._contained(Path(base), name)
                    with closing(sqlite3.connect(str(target))) as conn:
                        rows = conn.execute(
                            "SELECT zotero_key, path FROM fulltexts").fetchall()
                        for row in rows:
                            if (row and row[0] is not None
                                    and str(row[0]) not in keep and row[1]):
                                conn.execute(
                                    "UPDATE fulltexts SET path='' "
                                    "WHERE zotero_key=? AND path<>''",
                                    (str(row[0]),))
                        conn.commit()
                except sqlite3.Error:
                    pass
            return 0

        with mock.patch.object(task_package_api,
                               "_neutralize_unrestored_fulltexts", _no_check):
            response = self.client.post(
                "/api/tasks/import-package",
                files={"package": ("pkg.zip", self._hostile_package("trigger"),
                                   "application/zip")},
                headers=self.auth(2))
        self.assertEqual(response.status_code, 200, response.text)
        new_task = response.json()["task_id"]
        new_info = tasks_mod.find_task(new_task, DATA_DIR)
        with closing(sqlite3.connect(str(new_info.dir / "sec2-attacker.db"))) as conn:
            rows = dict(conn.execute(
                "SELECT zotero_key, path FROM fulltexts").fetchall())
        self.assertTrue(rows.get("VICTIM"),
                        "无 rowcount 校验时触发器包真实带行落库（钉必红）")

    def test_mutation_export_roots_widened_pin_goes_red(self):
        """破坏性变异：合法根放宽回（任务目录 + 共享平铺池）→ 池内文件真实
        进归档导出——test_export_skips_rows… 必红。"""
        self.new_task()
        self.add_pdf("K01")
        pool = Path(DATA_DIR) / "pdfs"
        pool.mkdir(parents=True, exist_ok=True)
        secret = b"%PDF-1.4\nR2-POOL-MUT\n%%EOF\n"
        victim = pool / "legacy_sec2_poolmut.pdf"
        victim.write_bytes(secret)
        self.addCleanup(lambda: victim.unlink(missing_ok=True))
        with closing(sqlite3.connect(self.owner_db)) as conn, conn:
            conn.execute(
                "INSERT INTO articles (zotero_key, title, authors, year) "
                "VALUES ('POOLX', 'T2', 'Wang, A', 2021)")
            conn.execute(
                "INSERT INTO fulltexts (zotero_key, filename, path, sha256, "
                "n_pages, added_at) VALUES ('POOLX', 'POOLX.pdf', ?, ?, 1, "
                "'2026-01-01')", (str(victim), "0" * 64))

        def widened(info):
            return [Path(info.dir).resolve(), pool.resolve()]

        with mock.patch.object(task_package_api, "_fulltext_allowed_roots",
                               widened):
            response = self.client.post(
                f"/api/tasks/{self.task}/fulltext/archive-export",
                json={"scope": "all"}, headers=self.auth())
        self.assertEqual(response.status_code, 200, response.text)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            blob = b"".join(archive.read(n) for n in archive.namelist())
        self.assertIn(secret, blob, "根放宽时共享池文件真实泄露（钉必红）")


class AfterTriggerAndGetLeakPins(_ApiBase):
    """终审 #39 r3：AFTER UPDATE 写回触发器 + GET /fulltext 读侧泄露。"""

    def _after_trigger_package(self):
        db_path = Path(self.info.dir) / "sec2-after.db"
        db_mod.init_db(db_path)
        with closing(sqlite3.connect(db_path)) as conn, conn:
            conn.execute(
                "INSERT INTO articles (zotero_key, title, authors, year) "
                "VALUES ('K', 't', 'V, V', 2019)")
            conn.execute(
                "INSERT INTO fulltexts (zotero_key, filename, path, sha256, "
                "n_pages, added_at) VALUES ('K', 'K.pdf', "
                "'data/pdfs/legacy_sec2_after.pdf', ?, 1, '2026-01-01')",
                ("0" * 64,))
            # r3 对抗形态：外层 UPDATE rowcount=1，触发器把 path 写回旧值。
            conn.execute(
                "CREATE TRIGGER revert_path AFTER UPDATE OF path ON fulltexts "
                "BEGIN UPDATE fulltexts SET path=OLD.path "
                "WHERE zotero_key=NEW.zotero_key; END")
        members = {
            "task.sanitized.json": json.dumps(
                {"task_id": "src", "display_name": "s"}).encode(),
            "db/sec2-attacker.db": db_path.read_bytes(),
        }
        return _build_raw_package(members)

    def test_after_update_trigger_rejected(self):
        """AFTER UPDATE 写回触发器：schema 层拒绝（fulltexts 不得挂任何
        触发器），commit 后重 SELECT 断言兜底——绝不给假 200 + 行保留。"""
        self.new_task()
        before = _task_dir_count()
        response = self.client.post(
            "/api/tasks/import-package",
            files={"package": ("pkg.zip", self._after_trigger_package(),
                               "application/zip")},
            headers=self.auth(2))
        self.assertEqual(response.status_code, 400, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["error_code"], "package_invalid")
        self.assertEqual(detail["params"]["reason"], "fulltext_neutralize_failed")
        self.assertEqual(_task_dir_count(), before)
        root = tasks_mod.tasks_root(DATA_DIR)
        leftovers = [] if not root.is_dir() else [
            p.name for p in root.iterdir() if p.name.startswith(".import-")]
        self.assertEqual(leftovers, [])

    def test_schema_guard_detects_trigger_and_view(self):
        """schema 守卫直测：普通 fulltexts 表 → False；触发器/视图 → True。"""
        base = Path(tempfile.mkdtemp(prefix="sec2-schema-guard-"))
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        plain = base / "plain.db"
        db_mod.init_db(plain)
        with closing(sqlite3.connect(plain)) as conn:
            self.assertFalse(task_package_api._fulltexts_schema_hostile(conn))
        tricky = base / "tricky.db"
        db_mod.init_db(tricky)
        with closing(sqlite3.connect(tricky)) as conn, conn:
            conn.execute(
                "CREATE TRIGGER t1 AFTER UPDATE ON fulltexts "
                "BEGIN SELECT 1; END")
        with closing(sqlite3.connect(tricky)) as conn:
            self.assertTrue(task_package_api._fulltexts_schema_hostile(conn))
        viewed = base / "viewed.db"
        db_mod.init_db(viewed)
        with closing(sqlite3.connect(viewed)) as conn, conn:
            conn.execute("ALTER TABLE fulltexts RENAME TO real_ft")
            conn.execute("CREATE VIEW fulltexts AS SELECT * FROM real_ft")
        with closing(sqlite3.connect(viewed)) as conn:
            self.assertTrue(task_package_api._fulltexts_schema_hostile(conn))

    def test_mutation_neutralize_naive_pin_goes_red(self):
        """破坏性变异：中和换成朴素版（无 schema/rowcount/事后核验）→
        AFTER 触发器包导入 200 且行保留——test_after_update_trigger_rejected 必红。"""
        self.new_task()

        def _naive(base, names, covered_paths):
            for name in names:
                try:
                    target = task_package_api._contained(Path(base), name)
                    with closing(sqlite3.connect(str(target))) as conn:
                        rows = conn.execute(
                            "SELECT zotero_key, path FROM fulltexts").fetchall()
                        for row in rows:
                            if row and row[0] is not None and row[1]:
                                conn.execute(
                                    "UPDATE fulltexts SET path='' "
                                    "WHERE zotero_key=?", (str(row[0]),))
                        conn.commit()
                except sqlite3.Error:
                    pass
            return 1

        with mock.patch.object(task_package_api,
                               "_neutralize_unrestored_fulltexts", _naive):
            response = self.client.post(
                "/api/tasks/import-package",
                files={"package": ("pkg.zip", self._after_trigger_package(),
                                   "application/zip")},
                headers=self.auth(2))
        self.assertEqual(response.status_code, 200, response.text)
        new_info = tasks_mod.find_task(response.json()["task_id"], DATA_DIR)
        with closing(sqlite3.connect(str(new_info.dir / "sec2-attacker.db"))) as conn:
            rows = dict(conn.execute(
                "SELECT zotero_key, path FROM fulltexts").fetchall())
        self.assertTrue(rows.get("K"),
                        "无防护时 AFTER 触发器包真实带池路径落库（钉必红）")

    def test_get_fulltext_rejects_pool_paths(self):
        """读侧纵深：fulltexts 行指向共享平铺池 → GET /fulltext/{key} 404，
        任务目录内正常文献照常 200（终审 #39 r3）。"""
        self.new_task()
        legit = self.add_pdf("K01")
        pool = Path(DATA_DIR) / "pdfs"
        pool.mkdir(parents=True, exist_ok=True)
        secret = b"%PDF-1.4\nR3-POOL-GET\n%%EOF\n"
        victim = pool / "legacy_sec2_get.pdf"
        victim.write_bytes(secret)
        self.addCleanup(lambda: victim.unlink(missing_ok=True))
        with closing(sqlite3.connect(self.owner_db)) as conn, conn:
            conn.execute(
                "INSERT INTO articles (zotero_key, title, authors, year) "
                "VALUES ('POOLK', 'T', 'Wang, A', 2021)")
            conn.execute(
                "INSERT INTO fulltexts (zotero_key, filename, path, sha256, "
                "n_pages, added_at) VALUES ('POOLK', 'P.pdf', ?, ?, 1, "
                "'2026-01-01')", (str(victim), "0" * 64))
        absolute = self.client.get(f"/api/tasks/{self.task}/fulltext/POOLK",
                                   headers=self.auth())
        self.assertEqual(absolute.status_code, 404, absolute.text)
        self.assertNotEqual(absolute.content, secret)
        with closing(sqlite3.connect(self.owner_db)) as conn, conn:
            conn.execute(
                "UPDATE fulltexts SET path='data/pdfs/legacy_sec2_get.pdf' "
                "WHERE zotero_key='POOLK'")
        relative = self.client.get(f"/api/tasks/{self.task}/fulltext/POOLK",
                                   headers=self.auth())
        self.assertEqual(relative.status_code, 404, relative.text)
        ok = self.client.get(f"/api/tasks/{self.task}/fulltext/K01",
                             headers=self.auth())
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(ok.content, legit, "任务目录内正常文献不受影响")

    def test_mutation_fulltext_pdf_guard_widened_pin_goes_red(self):
        """破坏性变异：main.is_within 恒 True（读侧包含性删掉）→ 池内文件
        被 GET 逐字节读出——test_get_fulltext_rejects_pool_paths 必红。"""
        from custom_backend import main as main_mod
        self.new_task()
        pool = Path(DATA_DIR) / "pdfs"
        pool.mkdir(parents=True, exist_ok=True)
        secret = b"%PDF-1.4\nR3-POOL-MUTGET\n%%EOF\n"
        victim = pool / "legacy_sec2_mutget.pdf"
        victim.write_bytes(secret)
        self.addCleanup(lambda: victim.unlink(missing_ok=True))
        with closing(sqlite3.connect(self.owner_db)) as conn, conn:
            conn.execute(
                "INSERT INTO articles (zotero_key, title, authors, year) "
                "VALUES ('POOLK', 'T', 'Wang, A', 2021)")
            conn.execute(
                "INSERT INTO fulltexts (zotero_key, filename, path, sha256, "
                "n_pages, added_at) VALUES ('POOLK', 'P.pdf', ?, ?, 1, "
                "'2026-01-01')", (str(victim), "0" * 64))
        with mock.patch.object(main_mod, "is_within", lambda child, root: True):
            response = self.client.get(f"/api/tasks/{self.task}/fulltext/POOLK",
                                       headers=self.auth())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, secret, "读侧防护删掉后真实泄露（钉必红）")


class ForgedCentralDirectoryPins(_ApiBase):
    """终审 #41 r2：central directory 谎报尺寸（zip64/CRC 伪造）→ 结构化 400。"""

    def test_declared_smaller_than_actual_is_zip_corrupt_400(self):
        self.new_task()
        members = {
            "task.sanitized.json": b'{"task_id":"t","display_name":"T"}',
            "db/a.db": _SQLITE_MAGIC + os.urandom(64 * 1024),
        }
        payload = _patch_central(_build_raw_package(members), "db/a.db",
                                 file_size=10)
        before = _task_dir_count()
        response = self.client.post(
            "/api/tasks/import-package",
            files={"package": ("pkg.zip", payload, "application/zip")},
            headers=self.auth(2))
        self.assertEqual(response.status_code, 400, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["error_code"], "package_invalid")
        self.assertEqual(detail["params"]["reason"], "zip_corrupt")
        self.assertEqual(_task_dir_count(), before)
        root = tasks_mod.tasks_root(DATA_DIR)
        leftovers = [] if not root.is_dir() else [
            p.name for p in root.iterdir() if p.name.startswith(".import-")]
        self.assertEqual(leftovers, [], "拒绝后必须清场")


class RelativePackagePathPins(_ApiBase):
    """终审 #43（用户 2026-11-09 裁定「相对化」）：导出包内零绝对路径。

    修复前 ``_snapshot_sqlite`` 纯 backup 后 db 成员原样入包——fulltexts.path
    携带源机器绝对路径（盘符/用户名/目录布局）。修复后 db 成员内一律任务目录
    相对（``pdfs/<名>.pdf``）；旧格式包（绝对路径）导入保持兼容。
    """

    _ABS_DRIVE_RE = re.compile(rb"[A-Za-z]:[\\/]")

    def test_exported_db_members_carry_task_relative_paths_only(self):
        self.new_task()
        for key in ("K01", "K02"):
            self.add_pdf(key)
        response = self.package()
        self.assertEqual(response.status_code, 200, response.text)
        data = response.content
        source_dir = str(Path(self.info.dir).resolve())

        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for name in archive.namelist():
                payload = archive.read(name)
                if name in ("manifest.json", "task.sanitized.json"):
                    text = payload.decode("utf-8")
                    self.assertNotIn(source_dir, text, f"{name} 含源任务目录")
                    self.assertNotIn(source_dir.replace("\\", "/"), text,
                                     f"{name} 含源任务目录（POSIX 形式）")
                    self.assertIsNone(self._ABS_DRIVE_RE.search(payload),
                                      f"{name} 含盘符绝对路径字面量")
                if not (name.startswith("db/") and name.endswith(".db")):
                    continue
                handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
                try:
                    handle.write(payload)
                    handle.close()
                    with closing(sqlite3.connect(handle.name)) as conn:
                        rows = conn.execute(
                            "SELECT zotero_key, path FROM fulltexts").fetchall()
                finally:
                    os.unlink(handle.name)
                self.assertTrue(rows, "导出 db 成员应保留 fulltexts 登记")
                for key, path in rows:
                    if not path:
                        continue
                    self.assertFalse(
                        Path(path).is_absolute(),
                        f"{name}:{key} 不得携带源机器绝对路径（终审 #43）: {path}")
                    self.assertTrue(
                        path.replace("\\", "/").startswith("pdfs/"),
                        f"{name}:{key} 应为任务目录相对形式: {path}")

    def test_legacy_absolute_path_package_imports_fine(self):
        """兼容钉：旧格式包（db 内 fulltexts.path 为源机器绝对路径）导入仍成功。"""
        self.new_task()
        originals = {key: self.add_pdf(key) for key in ("K01", "K02")}
        data = self.package().content
        source_dir = Path(self.info.dir).resolve()

        renamed = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as src:
            db_name = next(n for n in src.namelist()
                           if n.startswith("db/") and n.endswith(".db"))
            handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
            handle.write(src.read(db_name))
            handle.close()
            try:
                with closing(sqlite3.connect(handle.name)) as conn, conn:
                    for key, path in conn.execute(
                            "SELECT zotero_key, path FROM fulltexts").fetchall():
                        if path:
                            conn.execute(
                                "UPDATE fulltexts SET path=? WHERE zotero_key=?",
                                (str(source_dir / "pdfs" / Path(path).name), key))
                member_bytes = Path(handle.name).read_bytes()
            finally:
                os.unlink(handle.name)
            with zipfile.ZipFile(renamed, "w", compression=zipfile.ZIP_DEFLATED) as dst:
                for info in src.infolist():
                    payload = member_bytes if info.filename == db_name \
                        else src.read(info.filename)
                    if info.filename == "manifest.json":
                        manifest = json.loads(payload.decode("utf-8"))
                        for member in manifest["members"]:
                            if member["path"] == db_name:
                                member["sha256"] = hashlib.sha256(member_bytes).hexdigest()
                                member["size"] = len(member_bytes)
                        payload = (json.dumps(manifest, ensure_ascii=False, indent=2,
                                              sort_keys=True) + "\n").encode("utf-8")
                    new_info = zipfile.ZipInfo(info.filename,
                                               date_time=(1980, 1, 1, 0, 0, 0))
                    new_info.compress_type = zipfile.ZIP_DEFLATED
                    new_info.external_attr = 0o600 << 16
                    dst.writestr(new_info, payload)

        imported = self.client.post(
            "/api/tasks/import-package",
            files={"package": ("legacy.zip", renamed.getvalue(), "application/zip")},
            headers=self.auth(2))
        self.assertEqual(imported.status_code, 200, imported.text)
        result = imported.json()
        self.assertEqual(result["fulltexts_restored"], 2)
        new_info = tasks_mod.find_task(result["task_id"], DATA_DIR)
        new_db = new_info.dir / f"{tasks_mod.slugify_screener(self.users[0]['username'])}.db"
        with closing(sqlite3.connect(str(new_db))) as conn:
            for key, path in conn.execute(
                    "SELECT zotero_key, path FROM fulltexts").fetchall():
                resolved = tasks_mod.resolve_fulltext_raw(path, new_db, data_dir=DATA_DIR)
                self.assertIsNotNone(resolved, f"{key} 旧格式包导入后必须可解析")
                self.assertEqual(Path(resolved).read_bytes(), originals[key])

    def test_relativize_hostile_schema_fails_as_package_member_invalid(self):
        """终审 #43 失败路径钉（r2 复核统一口径）：源库 fulltexts 被挂触发器
        （对抗改写）→ 导出结构化 400 ``package_member_invalid``
        ``{member, reason=fulltext_relativize_failed}``——与契约错误表一致
        （`_snapshot_sqlite` 的 ``sqlite_unreadable`` 同族）；绝不 500、绝不
        静默带出绝对路径（错误体是 JSON，不是 zip 流）。"""
        self.new_task()
        self.add_pdf("K01")
        with closing(sqlite3.connect(self.owner_db)) as conn, conn:
            conn.execute(
                "CREATE TRIGGER evil_fulltexts_rewrite AFTER UPDATE ON fulltexts "
                "BEGIN UPDATE fulltexts SET path='C:\\\\evil\\\\abs.pdf'; END")
        response = self.package()
        self.assertEqual(response.status_code, 400, response.text)
        self.assertNotIn("zip", response.headers.get("content-type", ""))
        detail = response.json()["detail"]
        self.assertEqual(detail["error_code"], "package_member_invalid")
        self.assertEqual(detail["message_key"],
                         "task_package.package_member_invalid")
        self.assertEqual(detail["params"]["reason"], "fulltext_relativize_failed")
        self.assertEqual(detail["params"]["member"], self.owner_db.name)
        # 拒绝后零临时残留（rf-package-export-* 工作目录即刻清理）。
        leftovers = [p.name for p in Path(tempfile.gettempdir()).iterdir()
                     if p.name.startswith("rf-package-export-")]
        self.assertEqual(leftovers, [], "相对化失败后必须清场")

    def test_mutation_relativize_removed_pin_goes_red(self):
        """破坏性变异：跳过相对化改写 → 包内出现绝对路径，主钉必红。"""
        self.new_task()
        self.add_pdf("K01")
        with mock.patch.object(task_package_api, "_relativize_snapshot_fulltexts",
                               lambda *args, **kwargs: 0):
            response = self.package()
        self.assertEqual(response.status_code, 200, response.text)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            db_name = next(n for n in archive.namelist()
                           if n.startswith("db/") and n.endswith(".db"))
            handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
            handle.write(archive.read(db_name))
            handle.close()
            try:
                with closing(sqlite3.connect(handle.name)) as conn:
                    paths = [row[0] for row in conn.execute(
                        "SELECT path FROM fulltexts").fetchall()]
            finally:
                os.unlink(handle.name)
        self.assertTrue(paths and all(Path(p).is_absolute() for p in paths),
                        "去掉相对化后导出包必须携带绝对路径（否则主钉是假钉）")


class ArchiveExportLimitPins(_ApiBase):
    """终审 #44（用户 2026-11-09 裁定「加上限」）：归档导出总量上限与整包导出
    对齐（同一常量口径 MAX_PACKAGE_BYTES=2GB）——超限结构化 413
    ``task_package.archive_too_large`` 且零文件流开始；体检处提前提示。"""

    def test_over_limit_returns_structured_413_before_any_stream(self):
        self.new_task()
        self.add_pdf("K01")
        before = sorted(p.name for p in Path(tempfile.gettempdir()).iterdir()
                        if p.name.startswith("rf-archive-export-"))
        with mock.patch.object(task_package_api, "MAX_ARCHIVE_EXPORT_BYTES", 1):
            response = self.archive()
        self.assertEqual(response.status_code, 413, response.text)
        self.assertIn("application/json", response.headers["content-type"],
                      "超限时绝不能开始流 zip")
        detail = response.json()["detail"]
        self.assertEqual(detail["error_code"], "archive_too_large")
        self.assertEqual(detail["message_key"], "task_package.archive_too_large")
        self.assertEqual(detail["params"]["limit_bytes"], 1)
        self.assertIn("total_bytes", detail["params"])
        after = sorted(p.name for p in Path(tempfile.gettempdir()).iterdir()
                       if p.name.startswith("rf-archive-export-"))
        self.assertEqual(after, before, "超限拒绝后不得残留归档工作目录")

    def test_preflight_reports_archive_export_limit_hint(self):
        self.new_task()
        originals = {key: self.add_pdf(key) for key in ("K01", "K02")}
        response = self.client.get(
            f"/api/tasks/{self.task}/package/preflight", headers=self.auth())
        self.assertEqual(response.status_code, 200, response.text)
        hint = response.json()["archive_export"]
        self.assertEqual(hint["limit_bytes"],
                         task_package_api.MAX_ARCHIVE_EXPORT_BYTES)
        self.assertEqual(hint["limit_bytes"], task_package_api.MAX_PACKAGE_BYTES,
                         "归档上限必须与整包导出同一常量口径")
        self.assertEqual(hint["bytes"], sum(len(blob) for blob in originals.values()))
        self.assertFalse(hint["over_limit"])

    def test_within_limit_export_is_normal_zip(self):
        self.new_task()
        self.add_pdf("K01")
        response = self.archive()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["x-archive-summary"], "1/0")


class MemberNameParityPins(_ApiBase):
    """终审 #45（用户 2026-11-09 裁定「统一按预检」）：导入落地层与预检共用
    同一成员名判定函数——合法带点名（``db/u5..c9.db``、带点 zotero_key 的规范
    PDF 名）预检放行 → 导入必成功（应用自导出的整包可往返）；非法名
    （``../`` 上跳、绝对、反斜杠、NUL）两处一致拒绝。"""

    def test_dotted_member_names_preflight_and_import_agree_and_roundtrip(self):
        self.new_task()
        original = self.add_pdf("K01")
        data = self.package().content
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            db_name = next(n for n in archive.namelist()
                           if n.startswith("db/") and n.endswith(".db"))
            pdf_name = next(n for n in archive.namelist() if n.startswith("fulltext/"))
        rename = {db_name: "db/u5..c9.db", pdf_name: "fulltext/A..B-Wang-2020.pdf"}

        def transform(manifest):
            for member in manifest["members"]:
                member["path"] = rename.get(member["path"], member["path"])
            for entry in manifest["fulltexts"]:
                entry["archive_path"] = rename.get(entry["archive_path"],
                                                   entry["archive_path"])
                entry["archive_name"] = entry["archive_path"][len("fulltext/"):]

        dotted = _rename_members(data, rename, transform)
        preview = self.client.post(
            "/api/tasks/import-package/preflight",
            files={"package": ("pkg.zip", dotted, "application/zip")},
            headers=self.auth(2))
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()["slug_map"], {"u5..c9": "u5..c9"})

        imported = self.client.post(
            "/api/tasks/import-package",
            files={"package": ("pkg.zip", dotted, "application/zip")},
            headers=self.auth(2))
        self.assertEqual(imported.status_code, 200,
                         f"预检放行的合法带点成员名导入必须成功（口径一致）: {imported.text}")
        result = imported.json()
        self.assertEqual(result["fulltexts_restored"], 1)
        new_info = tasks_mod.find_task(result["task_id"], DATA_DIR)
        new_db = new_info.dir / "u5..c9.db"
        self.assertTrue(new_db.is_file(), "带点 slug 的库文件必须落地")
        with closing(sqlite3.connect(str(new_db))) as conn:
            path = conn.execute(
                "SELECT path FROM fulltexts WHERE zotero_key='K01'").fetchone()[0]
        resolved = tasks_mod.resolve_fulltext_raw(path, new_db, data_dir=DATA_DIR)
        self.assertIsNotNone(resolved)
        self.assertEqual(Path(resolved).read_bytes(), original)

    def test_illegal_member_names_rejected_at_both_layers(self):
        for bad in ("db/../evil.db", "/abs.db", "db\\x.db", "db/bad\x00name.db"):
            with self.subTest(member=bad):
                payload = _build_raw_package({
                    "task.sanitized.json": b'{"task_id":"t","display_name":"T"}',
                    bad: _SQLITE_MAGIC + os.urandom(64),
                })
                for endpoint in ("import-package/preflight", "import-package"):
                    response = self.client.post(
                        f"/api/tasks/{endpoint}",
                        files={"package": ("pkg.zip", payload, "application/zip")},
                        headers=self.auth(2))
                    self.assertEqual(response.status_code, 400,
                                     f"{endpoint} 必须拒绝 {bad!r}")
                    detail = response.json()["detail"]
                    self.assertEqual(detail["error_code"], "package_invalid")
                    self.assertEqual(detail["params"]["reason"],
                                     "member_name_invalid")

    def test_extract_member_shares_preflight_predicate(self):
        """函数级单一事实源钉：_extract_member 与 _validate_member_name 对同一
        名字集合给出同一判定（带点放行、非法拒绝）。"""
        import inspect

        source = inspect.getsource(task_package_api._extract_member)
        self.assertIn("_validate_member_name", source,
                      "落地层必须直接调用预检判定函数（不得复制口径）")
        names_ok = ["db/u5..c9.db", "db/plain.db"]
        names_bad = ["db/../evil.db", "/abs.db", "db\\x.db", "db/bad\x00name.db"]
        with tempfile.TemporaryDirectory(prefix="rf_215w5_parity_") as tmp:
            zip_path = Path(tmp) / "pkg.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                for name in names_ok + names_bad:
                    archive.writestr(name, b"x")
            base = Path(tmp) / "out"
            base.mkdir()
            with zipfile.ZipFile(zip_path, "r") as archive:
                for name in names_ok:
                    target = task_package_api._extract_member(
                        archive, name, base, "plain-land.db"
                        if name != names_ok[0] else "u5..c9.db")
                    self.assertTrue(target.is_file())
                for name in names_bad:
                    with self.assertRaises(task_package_api.TaskPackageError) as ctx:
                        task_package_api._extract_member(archive, name, base, "x.db")
                    self.assertEqual(ctx.exception.params.get("reason"),
                                     "member_name_invalid")


def tearDownModule():
    """所有测试类共享同一临时数据目录；模块结束时统一清理。"""
    fixture.cleanup()


# ---------------------------------------------------------------------------
# 终审 #40 剩余分支（sec8）：缺 task.sanitized.json / 深嵌套 JSON / 旧 schema
# 三类「结构自洽但畸形」的包不再触发未捕获异常（KeyError / RecursionError /
# sqlite3.OperationalError）→ 500；统一 400 package_invalid（reason 区分），
# 原子回滚清场（对照：模块 docstring「绝不 500」承诺）。
# ---------------------------------------------------------------------------

class MalformedPackagePins(_ApiBase):
    """三种畸形包各一：preflight/import 的形状与清场。"""

    def _post_package(self, endpoint: str, package: bytes):
        return self.client.post(
            endpoint,
            files={"package": ("pkg.zip", package, "application/zip")},
            headers=self.auth(0))

    def _assert_invalid(self, response, reason: str):
        self.assertEqual(response.status_code, 400, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["error_code"], "package_invalid")
        self.assertEqual(detail["message_key"], "task_package.package_invalid")
        self.assertEqual(detail["params"]["reason"], reason)

    def _import_leftovers(self):
        root = tasks_mod.tasks_root(DATA_DIR)
        if not root.is_dir():
            return []
        return [p.name for p in root.iterdir() if p.name.startswith(".import-")]

    def test_manifest_consistent_package_without_task_json_is_400(self):
        # manifest 只声明 db/x.db，zip 成员也恰只有 db/x.db（集合自洽）——
        # 修复前 archive.getinfo("task.sanitized.json") KeyError → 500。
        self.new_task()
        package = _build_raw_package({"db/x.db": _SQLITE_MAGIC + b"\x00" * 64})
        for endpoint in ("/api/tasks/import-package/preflight",
                         "/api/tasks/import-package"):
            with self.subTest(endpoint=endpoint):
                response = self._post_package(endpoint, package)
                self._assert_invalid(response, "task_json_missing")
        self.assertEqual(self._import_leftovers(), [], "拒绝后必须清场")

    def test_deeply_nested_task_json_is_400_not_recursion_crash(self):
        # 约 80KB 的深嵌套 JSON（远小于 8MB 成员上限）：json.loads 抛
        # RecursionError（非 ValueError 子类）——修复前穿透 → 500。
        self.new_task()
        deep = b"[" * 40000 + b"]" * 40000
        package = _build_raw_package({
            "task.sanitized.json": deep,
            "db/x.db": _SQLITE_MAGIC + b"\x00" * 64,
        })
        for endpoint in ("/api/tasks/import-package/preflight",
                         "/api/tasks/import-package"):
            with self.subTest(endpoint=endpoint):
                response = self._post_package(endpoint, package)
                self._assert_invalid(response, "task_json_unreadable")
        self.assertEqual(self._import_leftovers(), [])

    def test_legacy_fulltexts_schema_is_400_on_import(self):
        # 包内 db 的 fulltexts 缺 n_pages/added_at 列（旧 schema）：save_pdf
        # 的 SELECT/INSERT 抛 OperationalError "no such column"——修复前 500
        # （导入日志可见该 SQL 错误）；现按坏包拒绝，且清场原子。
        self.new_task()
        with tempfile.TemporaryDirectory(prefix="rf_sec8_old_schema_") as tmp:
            old_db = Path(tmp) / "old.db"
            db_mod.init_db(old_db)
            with closing(sqlite3.connect(old_db)) as conn, conn:
                conn.execute(
                    "INSERT INTO articles (zotero_key, title, authors, year) "
                    "VALUES ('OLD1', 'Old schema', 'A, B', 2019)")
                # 先按现 schema 插全列，再 DROP 成旧形态（缺 n_pages/added_at）
                conn.execute(
                    "INSERT INTO fulltexts (zotero_key, filename, path, sha256, "
                    "n_pages, added_at) VALUES ('OLD1', 'A.pdf', "
                    "'data/pdfs/legacy.pdf', '" + "0" * 64 + "', 1, '2026-01-01')")
                conn.execute("ALTER TABLE fulltexts DROP COLUMN n_pages")
                conn.execute("ALTER TABLE fulltexts DROP COLUMN added_at")
            legit = make_pdf("SEC8")
            package = _build_raw_package(
                {
                    "task.sanitized.json": json.dumps(
                        {"task_id": "src", "display_name": "s"}).encode(),
                    "db/old.db": old_db.read_bytes(),
                    "fulltext/A.pdf": legit,
                },
                fulltexts=[{"zotero_key": "OLD1",
                            "archive_path": "fulltext/A.pdf",
                            "sha256": hashlib.sha256(legit).hexdigest(),
                            "size": len(legit),
                            "original_filename": "A.pdf"}])
        # preflight 只做静态校验（schema 问题不可见）→ 通过；
        response = self._post_package("/api/tasks/import-package/preflight", package)
        self.assertEqual(response.status_code, 200, response.text)
        # 落地阶段才触碰包内库 → 结构化 400（reason 区分）+ 零残留。
        response = self._post_package("/api/tasks/import-package", package)
        self._assert_invalid(response, "fulltext_schema_unsupported")
        self.assertEqual(response.json()["detail"]["params"]["zotero_key"], "OLD1")
        self.assertEqual(self._import_leftovers(), [], "失败导入必须原子清场")


# ---------------------------------------------------------------------------
# 终审 #42 剩余缺口：加密成员/重复成员闸、_read_upload_bounded 有界上传、
# 协作库 grants 清空、-imported slug 映射（成员名/三限额/清场已有上方钉组）
# ---------------------------------------------------------------------------

class EncryptedDuplicateMemberPins(_ApiBase):
    """zip 结构闸里此前无钉的两条：encrypted_member / duplicate_member。

    去掉对应检查后，加密/重复成员包会在更深的校验层以别的 reason 被拒
    （或被接受）——本组钉死 preflight 与 import 两层都给出准确 reason；
    变异钉证明检查是承重的（被绕过后 reason 变 manifest_missing 即红）。"""

    def _post(self, endpoint: str, payload: bytes):
        return self.client.post(
            f"/api/tasks/{endpoint}",
            files={"package": ("pkg.zip", payload, "application/zip")},
            headers=self.auth(2))

    def _assert_reason(self, response, reason: str):
        self.assertEqual(response.status_code, 400, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["error_code"], "package_invalid")
        self.assertEqual(detail["params"]["reason"], reason)

    @staticmethod
    def _encrypted_package() -> bytes:
        """zipfile 写盘时会清洗 flag_bits——加密位用中央目录原始字节手术置上
        （校验路径读的正是中央目录的 flag_bits）。"""
        import struct
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("db/a.db", _SQLITE_MAGIC + os.urandom(64))
        raw = bytearray(buffer.getvalue())
        end = raw.rfind(b"PK\x05\x06")
        pos = 0
        while True:
            pos = raw.find(b"PK\x01\x02", pos)
            if pos < 0 or pos > end:
                break
            flag = struct.unpack_from("<H", raw, pos + 8)[0]
            struct.pack_into("<H", raw, pos + 8, flag | 0x1)
            pos += 4
        return bytes(raw)

    @staticmethod
    def _duplicate_package() -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            blob = _SQLITE_MAGIC + os.urandom(64)
            archive.writestr("db/a.db", blob)
            archive.writestr("db/a.db", blob)  # 同名重复成员
        return buffer.getvalue()

    def test_encrypted_member_rejected_at_both_layers(self):
        payload = self._encrypted_package()
        for endpoint in ("import-package/preflight", "import-package"):
            with self.subTest(endpoint=endpoint):
                self._assert_reason(self._post(endpoint, payload),
                                    "encrypted_member")

    def test_duplicate_member_rejected_at_both_layers(self):
        payload = self._duplicate_package()
        for endpoint in ("import-package/preflight", "import-package"):
            with self.subTest(endpoint=endpoint):
                self._assert_reason(self._post(endpoint, payload),
                                    "duplicate_member")

    def test_mutation_encrypted_check_bypassed_pin_goes_red(self):
        """破坏性变异：infolist 层把加密位清零（等效删除该检查）→ 包走到
        manifest_missing 而非 encrypted_member——上一颗钉必红。"""
        real_infolist = zipfile.ZipFile.infolist

        def _no_flag(self):
            infos = real_infolist(self)
            for info in infos:
                info.flag_bits = 0
            return infos

        with mock.patch.object(zipfile.ZipFile, "infolist", _no_flag):
            response = self._post("import-package/preflight",
                                  self._encrypted_package())
        self._assert_reason(response, "manifest_missing")

    def test_mutation_duplicate_check_neutered_pin_goes_red(self):
        """破坏性变异：``len(names) != len(set(names))`` 的比较恒假（值保真
        的恒等 int）→ 包走到 manifest_missing 而非 duplicate_member——上一
        颗钉必红。"""
        real_len = len

        class _EqAnyInt(int):
            def __eq__(self, other):
                return True

            def __ne__(self, other):
                return False

        with mock.patch.object(task_package_api, "len",
                               lambda seq: _EqAnyInt(real_len(seq)),
                               create=True):
            response = self._post("import-package/preflight",
                                  self._duplicate_package())
        self._assert_reason(response, "manifest_missing")


class ReadUploadBoundedPins(unittest.TestCase):
    """``_read_upload_bounded`` 函数级红钉（终审 #42：有界上传此前只有
    端点级 413 一处，数值漂移无人发现——终审 runner.py drift_rule 探针）。"""

    class _FakeUpload:
        def __init__(self, chunks):
            self._chunks = list(chunks)

        async def read(self, limit=-1):
            return self._chunks.pop(0) if self._chunks else b""

    def _run(self, chunks, limit):
        import asyncio
        return asyncio.run(
            task_package_api._read_upload_bounded(self._FakeUpload(chunks),
                                                  limit))

    def _temp_files(self):
        import glob
        return set(glob.glob(os.path.join(tempfile.gettempdir(),
                                          "rf-package-upload-*")))

    def test_over_limit_is_structured_413_and_cleans_temp_file(self):
        before = self._temp_files()
        with self.assertRaises(task_package_api.TaskPackageError) as ctx:
            self._run([b"x" * 1024] * 2, limit=1024)
        error = ctx.exception
        self.assertEqual(error.status, 413)
        self.assertEqual(error.params["limit_bytes"],
                         1024 - task_package_api._UPLOAD_SLACK_BYTES,
                         "413 载荷必须按 limit-slack 报真实端点上限")
        self.assertEqual(error.params["total_bytes"], 2048)
        self.assertEqual(self._temp_files(), before, "超限后的临时文件必须被清理")

    def test_empty_body_is_structured_400(self):
        with self.assertRaises(task_package_api.TaskPackageError) as ctx:
            self._run([], limit=1024)
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.params["reason"], "empty_upload")

    def test_within_limit_spools_and_returns_path(self):
        path = None
        try:
            path = self._run([b"abc", b"def"], limit=1024)
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes(), b"abcdef")
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

    def test_mutation_bound_removed_equivalent_pin_goes_red(self):
        """破坏性变异的等价对照：limit 被放到天文数字（=「total>limit 检查
        形同虚设」）时，同样的超限内容被完整落盘并成功返回——证明真实
        limit 下的 413 完全由该检查承重（去掉检查即如此行为）。"""
        path = None
        try:
            path = self._run([b"x" * 1024] * 2, limit=1 << 62)
            self.assertTrue(path.is_file())
            self.assertEqual(path.stat().st_size, 2048,
                             "无界时 2KiB 全部落盘（= 有界钉必红）")
        finally:
            if path is not None:
                path.unlink(missing_ok=True)


class CollabRewritePins(unittest.TestCase):
    """``_rewrite_collaboration_store``：导入协作库时旧部署的 role grants /
    member settings 必须清空 + 任务改绑导入者（终审 #42 ⑤）。"""

    def _make_store(self, base):
        path = base / "collaboration.db"
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("CREATE TABLE collaboration_task(singleton INTEGER"
                         " PRIMARY KEY, task_id TEXT, owner_user_id INTEGER,"
                         " owner_username TEXT)")
            conn.execute("CREATE TABLE collaboration_role_grants("
                         "user_id INTEGER, role TEXT)")
            conn.execute("CREATE TABLE collaboration_member_settings("
                         "user_id INTEGER, json TEXT)")
            conn.execute("INSERT INTO collaboration_task VALUES(1,'src-task',"
                         "42,'old-owner')")
            conn.execute("INSERT INTO collaboration_role_grants VALUES(42,'coder')")
            conn.execute("INSERT INTO collaboration_role_grants VALUES(43,'reconciler')")
            conn.execute("INSERT INTO collaboration_member_settings VALUES(42,'{}')")
        return path

    def test_grants_and_settings_cleared_and_task_rebound(self):
        with tempfile.TemporaryDirectory(prefix="rf_w5_collab_rw_") as tmp:
            base = Path(tmp)
            path = self._make_store(base)
            ok = task_package_api._rewrite_collaboration_store(
                base, "collaboration.db", task_id="new-task", owner_id=7,
                owner_name="importer")
            self.assertTrue(ok)
            with closing(sqlite3.connect(path)) as conn:
                task = conn.execute(
                    "SELECT task_id, owner_user_id, owner_username FROM"
                    " collaboration_task").fetchone()
                self.assertEqual(task, ("new-task", 7, "importer"))
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM collaboration_role_grants"
                ).fetchone()[0], 0, "旧部署的 grants 必须清空")
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM collaboration_member_settings"
                ).fetchone()[0], 0, "旧部署的 member settings 必须清空")

    def test_mutation_deletes_swallowed_pin_goes_red(self):
        """破坏性变异：清空语句被转发代理拦掉（经 ``__getattr__`` 动态绑定，
        不在测试里写出「变量 SQL 进 execute」形态——同 W1 变异钉惯例）→
        grants 原样残留——上一颗钉必红。"""
        real_open_rw = task_package_api._open_rw
        swallow = "".join(["DEL", "ETE"])

        class _Proxy:
            def __init__(self, real):
                self._real = real

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self._real.__exit__(*exc)
                return False

            def __getattr__(self, name):
                attr = getattr(self._real, name)
                if name in ("commit", "rollback", "close") or not callable(attr):
                    return attr

                def _forward(*args, **kwargs):
                    first = str(args[0]) if args else ""
                    if first.lstrip().upper().startswith(swallow):

                        class _Noop:
                            rowcount = 0

                        return _Noop()
                    return attr(*args, **kwargs)

                return _forward

        with tempfile.TemporaryDirectory(prefix="rf_w5_collab_mut_") as tmp:
            base = Path(tmp)
            path = self._make_store(base)
            with mock.patch.object(task_package_api, "_open_rw",
                                   lambda *a, **k: _Proxy(real_open_rw(*a, **k))):
                ok = task_package_api._rewrite_collaboration_store(
                    base, "collaboration.db", task_id="new-task", owner_id=7,
                    owner_name="importer")
            self.assertTrue(ok)
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM collaboration_role_grants"
                ).fetchone()[0], 2, "变异下 grants 残留（= 清空钉必红）")


class SlugMapPins(unittest.TestCase):
    """``_slug_map_for``：保留名/大小写冲突加 ``-imported`` 后缀且映射
    唯一（终审 #42 ⑤；纯函数输出钉，同 TopicOrderUnitNails 先例）。"""

    def test_reserved_and_case_collisions_get_imported_suffix(self):
        mapping = task_package_api._slug_map_for([
            "db/collaboration.db", "db/Collaboration.db", "db/plain.db"])
        # 排序先处理大写 Collaboration（casefold 命中保留名）→ -imported；
        # 小写 collaboration 再撞保留名 + 已用后缀 → -imported-2。
        self.assertEqual(mapping["Collaboration"], "Collaboration-imported")
        self.assertEqual(mapping["collaboration"], "collaboration-imported-2")
        self.assertEqual(mapping["plain"], "plain")
        # 映射目标整体：无保留名、大小写唯一（同名库绝不互相覆盖）
        targets = [target.casefold() for target in mapping.values()]
        self.assertEqual(len(targets), len(set(targets)))
        self.assertNotIn("collaboration", targets)

    def test_plain_names_keep_identity(self):
        mapping = task_package_api._slug_map_for(
            [f"db/s{i}.db" for i in range(3)])
        self.assertEqual(sorted(mapping.values()), ["s0", "s1", "s2"])


if __name__ == "__main__":
    unittest.main()
