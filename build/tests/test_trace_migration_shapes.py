"""2.1.4 迁移形态补充回归：混合形态的反向（before=旧摘要 / after=全量快照）。

正向形态（before=全量 / after=旧摘要）已由后端泳道
test_report_evidence.py::test_legacy_mixed_digest_side_preserved 钉住；本文件补齐
反向可达形态——2.1.3 中某次保存超预算在 after 侧产生摘要后，成员删减内容使
下一次保存回到小工作区，会留下 before=摘要 / after=全量 的混合事件。断言口径
与正向用例一致：摘要侧原样保留其 workspace_sha256 与 links/findings 计数
（不得重算为空摘要），全量侧降级为计算摘要。

只加测试、不改产品代码：按保存批次折叠实现（FOLD_BACKUP_SUFFIX 等标记）不在
本树时自动跳过；集成后端泳道后自动启用。
"""
import json
import os
import sys
import tempfile
import unittest
import uuid
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
fixture = tempfile.TemporaryDirectory(prefix="rf_fold_shape_test_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient

from custom_backend import report_evidence_api
from custom_backend.main import app
from coscreen import db, research_trace
from coscreen.models import Article


@unittest.skipUnless(
    hasattr(report_evidence_api, "FOLD_BACKUP_SUFFIX"),
    "按保存批次折叠实现不在本树（由后端泳道 fix/2.1.4-r2-backend 提供）；集成后自动启用",
)
class ReverseMixedDigestFoldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        r = cls.client.post(
            "/api/auth/register",
            json={"username": "foldshape@example.test", "password": "Fold-Shape-2026"},
        ).json()
        cls.user = {"user_id": r["user"]["id"], "token": r["token"]}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    def setUp(self):
        self.task = self.client.post(
            "/api/tasks",
            json={"name": self._testMethodName},
            headers={"Authorization": "Bearer " + self.user["token"]},
        ).json()["task_id"]
        self.ctx = app.state.review_workflow.context(self.task, self.user)
        db.upsert_articles(
            self.ctx.owner_db,
            [Article(zotero_key="R1", title="First", doi="10.test/1")],
            [],
        )
        self.actor = str(self.user["user_id"])

    def test_reverse_mixed_digest_side_preserved(self):
        # before=旧摘要（2.1.3 超预算产物）/ after=全量小快照（删减内容后的保存）。
        before = {
            "value": "Save evidence",
            "workspace_sha256": "e" * 64,
            "links": 3,
            "findings": 1,
            "note": "Large workspace: export JSON to inspect current details.",
        }
        after = {
            "value": "Save evidence",
            "workspace": {"links": [], "drafts": [{"id": "F1", "key": "R1", "finding": "Meaning"}]},
        }
        with closing(db._connect(self.ctx.owner_db)) as c:
            research_trace.ensure_schema(c)
            store_id = c.execute("SELECT store_id FROM trace_store WHERE singleton=1").fetchone()[0]
            c.execute(
                "INSERT INTO trace_events (event_id,store_id,task_id,actor_id,stage,action,subject_id,"
                "before_json,after_json,source,recorded_at,schema_version,app_version) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()), store_id, self.task, self.actor,
                    "coding", "report_evidence_changed", "R1",
                    json.dumps(before, ensure_ascii=False),
                    json.dumps(after, ensure_ascii=False),
                    "report_evidence", "2026-01-02T00:00:00Z",
                    research_trace.SCHEMA_VERSION, research_trace.__version__,
                ),
            )
            c.commit()
        with closing(report_evidence_api.connect(self.ctx.owner_db)):
            pass  # 触发按保存批次折叠（幂等、带完成标记）
        with closing(db._connect(self.ctx.owner_db)) as c:
            row = c.execute(
                "SELECT before_json,after_json FROM trace_events "
                "WHERE action='report_evidence_changed'"
            ).fetchone()
        self.assertIsNotNone(row)
        folded_before = json.loads(row[0])
        folded_after = json.loads(row[1])
        # 摘要侧（before）原样保留：不得重算为空工作区摘要。
        self.assertEqual(folded_before["workspace_sha256"], "e" * 64)
        self.assertEqual(folded_before["links"], 3)
        self.assertEqual(folded_before["findings"], 1)
        # 全量侧（after）脱敏后降级为计算摘要。
        self.assertIn("workspace_sha256", folded_after)
        self.assertNotEqual(folded_after["workspace_sha256"], "e" * 64)
        self.assertEqual(folded_after["findings"], 1)
        self.assertIn("Legacy full workspace snapshot compacted", folded_after["note"])


if __name__ == "__main__":
    unittest.main()
