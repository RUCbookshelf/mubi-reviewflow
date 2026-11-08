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
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path

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


def tearDownModule():
    """所有测试类共享同一临时数据目录；模块结束时统一清理。"""
    fixture.cleanup()


if __name__ == "__main__":
    unittest.main()
