# -*- coding: utf-8 -*-
"""2.1.5 Wave C1 红钉：双评一致性 / 评价导出 / GRADE 衔接。

覆盖：

1. 双评 κ 数值钉：两名评价人 × 两篇文献 × 4 个条目，构造已知应答矩阵
   （一致 6/8、边际各半）→ Cohen's κ 必须精确为 0.5；逐条目一致率与
   条目级 κ 同步对账。
2. 分歧清单负钉：只含 ``article_key + item_id + 双方应答``；PUT 时写入的
   评论与说明（自由文本理由）绝不跨人出现在一致率载荷里。
3. 权限：任务成员（无 ``consensus:write``）读一致率 → 403；属主 → 200；
   裁决员（reconciler）→ 200。
4. 成员库锁忙 → 503（不输出缺员 κ），且响应不泄露任何文献键/应答。
5. 导出 CSV 列与行形状：两人 × 两文献 = 4 条数据行，每人一行；列名是
   条目编号；CASP 的 overall 是逐条报告文本。
6. GRADE 映射表钉：``GRADE_ROB_MAP`` 每工具至少 3 档（输入档与输出档都
   ≥3），并逐条钉住 RoB2 High→serious、Critical→very_serious 等映射；
   ``GET …/appraisal/grade-input`` 逐文献返回档位且不写 GRADE 草稿。

运行：pytest build/tests/test_2_1_5_appraisal_agreement.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# 与既有 2.1.5 服务端测试同法：先设环境再导入 main。
#
# 包 J（F 线移交族）根修正：本模块原先在导入期自建私有临时目录并把它写进
# ``COBOOKSHELF_DATA_DIR``。pytest 下 main 在 ``pytest_configure`` 已导入并
# 把 ``DATA_DIR`` **冻结**在会话根上；模块级改写只影响运行期 ``auth.data_dir()``
# 而不改冻结值——团队写窗按 ``runtime.data_dir``（=会话根）找 ``users.db``，
# 账号却建在私有根 → 全部邀请/写入 503 ``[accounts_unreadable] accounts DB
# missing``（本包 26F 实测签名）。正确做法与 2.1.5 其余套件一致：**借用**
# test-only 所有者根（pytest 会话代 / 裸跑进程 owner），绝不自建分叉根。
# r3 的模块边界语义保留：结束时保证 server + 同一根，**不还原成导入前的值**；
# 目录生命周期交给所有者模型（``_bare_env``），本模块不再持有私有目录。
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bare_env  # noqa: E402

_BARE = _bare_env.acquire(mode="server")
#: 本模块使用的数据根（所有者根）。main.DATA_DIR 导入期冻结到同一根。
MODULE_ROOT = os.environ["COBOOKSHELF_DATA_DIR"]


def setUpModule():
    _bare_env.ensure_safe_for_execution()


def _guarantee_module_env() -> None:
    """模块边界的环境保证（r3 语义，包 J 根修正）：server + **所有者根**，
    **不还原成导入前的值**。pytest 的模块级 env 赋值发生在收集期、
    tearDownModule 在测试期，"还原 unset"会抹掉后续套件（w5/w7 等不重钉
    env 的）收集期设的 server 状态（实测 `pytest 本文件 w7` → 44 errors，
    评审 r3 阻断项）。"""
    os.environ["REVIEWFLOW_MODE"] = "server"
    os.environ["COBOOKSHELF_DATA_DIR"] = MODULE_ROOT


def tearDownModule():
    """模块结束：先保证环境（见 :func:`_guarantee_module_env`），再归还借用。

    根目录的删除/两键恢复只属于所有者收尾（``_bare_env`` 会话/进程合同）；
    模块只归还自己的 borrow token，绝不自删共享根（F 线同法）。"""
    _guarantee_module_env()
    _bare_env.release(_BARE)

from fastapi.testclient import TestClient  # noqa: E402

from coscreen import appraisal as core  # noqa: E402
from coscreen import db as db_mod  # noqa: E402
from coscreen import review_workflow as store  # noqa: E402
from coscreen import task_collaboration as collaboration_mod  # noqa: E402
from coscreen import tasks as tasks_mod  # noqa: E402
from coscreen import team_terminal_registry as terminal_registry  # noqa: E402
from coscreen import team_vote_ledger as vote_ledger  # noqa: E402
from coscreen.models import Article  # noqa: E402
from custom_backend import appraisal_agreement_api as agreement_api  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend import team_source_lifecycle as team_source_lifecycle  # noqa: E402
from custom_backend.appraisal_api import prepare_appraisal_storage_for  # noqa: E402
from custom_backend.main import (  # noqa: E402
    DATA_DIR, app, SCREENER_LIBRARY_UNAVAILABLE_MESSAGE,
)

TOOL = "casp-qualitative"

#: 两篇文献 × 4 条目的已知应答矩阵（条目 1–4）：
#:         K1                    K2
#: owner   Y Y N Y               N N N Y
#: peer    Y Y N N               N N Y Y
#: 配对 8 格：YY 3 + NN 3 = 一致 6，YN 1 + NY 1 = 不一致 2；双方边际均为
#: Y=4/N=4，期望一致率 0.5 → Cohen's κ = (0.75 - 0.5) / (1 - 0.5) = 0.5。
OWNER_RESPONSES = {
    "K1": {"1": "Yes", "2": "Yes", "3": "No", "4": "Yes"},
    "K2": {"1": "No", "2": "No", "3": "No", "4": "Yes"},
}
PEER_RESPONSES = {
    "K1": {"1": "Yes", "2": "Yes", "3": "No", "4": "No"},
    "K2": {"1": "No", "2": "No", "3": "Yes", "4": "Yes"},
}

ARTICLES = [
    Article(zotero_key="K1", title="Alpha qualitative study"),
    Article(zotero_key="K2", title="Beta qualitative study"),
]


def _slug(username: str) -> str:
    return tasks_mod.slugify_screener(username)


def _auth_headers(user: dict) -> dict:
    token = auth_mod.create_access_token(user["id"], user["username"])
    return {"Authorization": "Bearer " + token}


def _configure_team_write_fixture(client, task_id: str, owner: dict,
                                  members: list[tuple[dict, str]], *,
                                  articles: list[Article],
                                  vote_keys: list[str]) -> None:
    """把已受邀任务补齐到「团队评价写可用」的真实态（F 线同法配方，零 mock）。

    2.1.6 起受邀任务即 team：评价 PUT 走 P15 ``runtime.mutate`` 真实写窗
    （``member_appraisal``，actor 目标）。写窗要求且本函数逐一补齐：

    1. **业务角色单选**（先于任何团队写）：邀请只授访问角色；未配置即
       409 ``team.role.unconfigured``。评价是编码者业务（``coding:submit``），
       故属主与被测成员均须 coder；reconciler 成员按其角色单选（读门用）。
    2. **存储显式准备**：P17 ``prepare_appraisal_storage_for``（
       appraisal_records）+ P14 ``prepare_team_protocol_storage``（F1
       facts/receipts）——写窗只做纯读检查、绝不建表；缺表即
       409 ``TeamFlowReceiptUnavailable``。
    3. **P06 真实 source 登记**：``make_source_identity`` 真 token +
       ``register_source_on`` 真登记（成员库真实 P04 账本身份，非占位）。
    4. **上游链完成**：``member_appraisal`` 链目标=appraisal，需 stage1+
       stage2 全员终局 include；coder 恒为必需筛选员（owner 无旁路）。
       阶段顺序固定（全员 stage1 终局才写 stage2）。

    本函数只补夹具，不改任何断言：隔离/权限/κ 数值语义由原钉原样承担。
    """
    info = tasks_mod.find_task(task_id, DATA_DIR)
    assert info is not None
    task_dir = info.dir
    owner_db = task_dir / f"{_slug(owner['username'])}.db"
    member_dbs = {
        user["id"]: task_dir / f"{_slug(user['username'])}.db"
        for user, _role in members
    }
    collab_path = collaboration_mod.shared_store_path(task_dir)
    # 1. 业务角色单选（属主=coder；成员按传入角色）
    collaboration_mod.set_business_role(
        collab_path, actor_user_id=owner["id"], user_id=owner["id"],
        business_role="coder", task_id=task_id)
    for user, role in members:
        collaboration_mod.set_business_role(
            collab_path, actor_user_id=owner["id"], user_id=user["id"],
            business_role=role, task_id=task_id)
    # 2. 存储显式准备（写窗只纯读检查）。宿主注入的产品解析器
    #    ``_screener_db(ensure=True)``：缺库时按标准原语建库（含 P04 账本），
    #    appraisal ensure 幂等；随后补 workflow 迁移（stage 表/team_latched）。
    for user in (owner, *(user for user, _role in members)):
        prepare_appraisal_storage_for(
            info, user["username"], app.state.review_workflow.screener_db)
    for path in (owner_db, *member_dbs.values()):
        store.migrate(path)
    team_source_lifecycle.prepare_team_protocol_storage(
        collab_path=Path(collab_path),
        source_paths=[owner_db, *member_dbs.values()])
    with closing(sqlite3.connect(str(owner_db))) as conn, conn:
        conn.execute("UPDATE workflow_state SET team_latched=1 WHERE singleton=1")
    # 3. P06 真实 source 登记（成员真实账本身份）
    with closing(sqlite3.connect(str(collab_path))) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT task_instance_id FROM main.team_task_protocol"
            " LIMIT 1").fetchone()
    assert row is not None, (
        "create_task must have registered the team protocol row (P18)")
    instance = row["task_instance_id"]
    for user, _role in members:
        member_db = member_dbs[user["id"]]
        with closing(sqlite3.connect(str(member_db))) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN")
            library = vote_ledger.library_identity_header_on(conn).library_id
            conn.rollback()
        token = team_source_lifecycle.make_source_identity(
            task_dir=task_dir, source_path=member_db,
            source_user_id=user["id"], source_library_id=library)
        writer = sqlite3.connect(str(collab_path), timeout=15)
        writer.row_factory = sqlite3.Row
        writer.execute("BEGIN IMMEDIATE")
        try:
            terminal_registry.register_source_on(
                writer, task_instance_id=instance,
                source_user_id=user["id"], source_library_id=library,
                source_path_identity=token,
                registration_id=f"reg-agreement-{task_id}-{user['id']}")
            writer.commit()
        finally:
            writer.close()
    # 成员库题录（各库自读；评价隔离语义不变）
    for path in (owner_db, *member_dbs.values()):
        db_mod.upsert_articles(path, articles, [])
    # 4. 上游链完成：必须筛选员=属主 + coder 成员（reconciler 默认不参与）
    required_headers = [_auth_headers(owner)]
    required_headers.extend(_auth_headers(user) for user, role in members
                           if role == "coder")
    for path in ("/decisions", "/stage2/decisions"):
        for headers in required_headers:
            for key in vote_keys:
                voted = client.post(
                    f"/api/tasks/{task_id}{path}",
                    json={"zotero_key": key, "decision": "include",
                          "reason": "", "notes": "", "tags": None},
                    headers=headers)
                assert voted.status_code == 200, (path, key, voted.text)


class AppraisalAgreementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        # C2 顺序泄漏修复：类装配（建用户）也在请求期读 os.environ，先跑模块
        # 的 tearDownModule 可能已把收集期的模块级赋值抹掉——装配前重申。
        _guarantee_module_env()
        cls.owner = auth_mod.create_user("r215c-owner@example.test",
                                         "R215C-Owner-2026!")
        cls.peer = auth_mod.create_user("r215c-peer@example.test",
                                        "R215C-Peer-2026!")
        cls.reconciler = auth_mod.create_user("r215c-arbiter@example.test",
                                              "R215C-Arbiter-2026!")
        cls.owner_auth = cls._auth(cls.owner)
        cls.peer_auth = cls._auth(cls.peer)
        cls.reconciler_auth = cls._auth(cls.reconciler)

    @classmethod
    def _auth(cls, user: dict) -> dict:
        token = auth_mod.create_access_token(user["id"], user["username"])
        return {"Authorization": "Bearer " + token}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        # r3：数据根由模块统一保证（tearDownModule）；类收尾只关自己的 Client。

    # ---- 夹具 ---------------------------------------------------------
    def setUp(self):
        # C2 顺序泄漏修复（用例内还原）：本模块对 REVIEWFLOW_MODE /
        # COBOOKSHELF_DATA_DIR 的模块级赋值发生在 pytest 收集期；先跑模块
        # （如 test_pdf_fetch_api）的 tearDownModule 会在其用例结束后把这
        # 两个变量还原/弹回，收集期赋值救不了测试期。server-only 端点与
        # auth 用户库都在请求期读 os.environ，故每个用例装配时重申一次
        # （见本类末尾 test_order_leak_step1/step2 复现钉）。
        os.environ["REVIEWFLOW_MODE"] = "server"
        os.environ["COBOOKSHELF_DATA_DIR"] = MODULE_ROOT
        response = self.client.post(
            "/api/tasks", json={"name": f"agreement-{self._testMethodName}"},
            headers=self.owner_auth)
        assert response.status_code in (200, 201), response.text
        self.task = response.json()["task_id"]
        self.base = f"/api/tasks/{self.task}/appraisal"
        for user, role in ((self.peer, "coder"), (self.reconciler, "reconciler")):
            invite = self.client.post(
                f"/api/tasks/{self.task}/collaboration/invite",
                json={"username": user["username"], "role": role},
                headers=self.owner_auth)
            assert invite.status_code == 201, invite.text
        self.ctx = app.state.review_workflow.context(
            self.task, {"user_id": self.owner["id"],
                        "username": self.owner["username"]})
        db_mod.upsert_articles(Path(self.ctx.owner_db), ARTICLES, [])
        # F 线移交族（2.1.6 语义，用户裁定照升级补夹具）：受邀后任务即 team，
        # 评价 PUT 经 P15 团队写窗（member_appraisal）——业务角色单选 + 存储
        # 显式准备 + P06 source 登记 + 上游链完成，缺一即 409。补齐后本套件
        # 每条断言原样承担：κ 数值/分歧自由文本负钉/权限 403/锁忙 503/导出
        # 列形状/GRADE 只读语义均未改。
        _configure_team_write_fixture(
            self.client, self.task, self.owner,
            [(self.peer, "coder"), (self.reconciler, "reconciler")],
            articles=ARTICLES, vote_keys=["K1", "K2"])

    def _put(self, auth: dict, article: str, responses: dict,
             *, comments: dict | None = None, note: str = "") -> None:
        body = {"tool": TOOL, "responses": responses, "note": note}
        if comments is not None:
            body["comments"] = comments
        response = self.client.put(f"{self.base}/{article}", json=body,
                                   headers=auth)
        assert response.status_code == 200, response.text
        # 夹具自检（F 线同法）：P15 写窗只在 team 分支 committed 后透传
        # X-Receipt-Id/X-Request-Id；solo/legacy 恒空。头缺失即夹具退化为
        # 非团队路径（此时 κ/权限钉测的就不是团队语义），立即红。
        names = {name.lower() for name in response.headers}
        assert "x-receipt-id" in names, (
            "appraisal PUT 未走 P15 团队写窗（缺 X-Receipt-Id）",
            article, response.status_code, response.text[:300])
        assert "x-request-id" in names, (
            "appraisal PUT 未带 P15 请求编号头", article)

    def _seed_two_raters(self, *, comments: bool = False) -> None:
        owner_comments = {"4": "owner private reason"} if comments else {}
        peer_comments = {"4": "peer private reason"} if comments else {}
        for article, responses in OWNER_RESPONSES.items():
            self._put(self.owner_auth, article, responses,
                      comments=owner_comments,
                      note="owner private note" if comments else "")
        for article, responses in PEER_RESPONSES.items():
            self._put(self.peer_auth, article, responses,
                      comments=peer_comments,
                      note="peer private note" if comments else "")

    def _agreement(self, auth: dict | None = None):
        return self.client.get(f"{self.base}/agreement", params={"tool": TOOL},
                               headers=auth or self.owner_auth)

    # ---- 1. κ 数值钉 ---------------------------------------------------
    def test_pairwise_kappa_is_exactly_half(self):
        self._seed_two_raters()
        response = self._agreement()
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["available"] is True
        assert payload["tool_id"] == TOOL
        assert payload["rater_count"] == 2
        assert payload["n_pairs"] == 1

        pair = payload["pairwise"][0]
        assert pair["n_paired_cells"] == 8
        assert pair["agreement_rate"] == 0.75
        assert pair["cohen_kappa"] == 0.5
        assert pair["kappa_status"] == "available"
        assert payload["mean_cohen_kappa"] == 0.5

        raters = {row["rater_id"]: row for row in payload["raters"]}
        for rater_id in (_slug(self.owner["username"]),
                         _slug(self.peer["username"])):
            assert raters[rater_id]["articles_rated"] == 2
            assert raters[rater_id]["items_answered"] == 8

    def test_item_level_agreement_and_kappa(self):
        self._seed_two_raters()
        payload = self._agreement().json()
        items = {row["item_id"]: row for row in payload["items"]}
        # 条目 1/2 在两篇文献上都一致（YY、NN）→ 一致率 1、κ 1。
        assert items["1"]["n_paired_cells"] == 2
        assert items["1"]["agreement_rate"] == 1.0
        assert items["1"]["cohen_kappa"] == 1.0
        assert items["2"]["cohen_kappa"] == 1.0
        # 条目 3/4 各有一格分歧：一致率 0.5，κ = 0（边际仍各半）。
        assert items["3"]["n_disagreements"] == 1
        assert items["3"]["agreement_rate"] == 0.5
        assert items["3"]["cohen_kappa"] == 0.0
        assert items["4"]["n_disagreements"] == 1
        assert items["4"]["cohen_kappa"] == 0.0

    def test_disagreements_carry_no_free_text_reasons(self):
        """负钉：评论/说明（理由）不得出现在分歧清单或载荷任何位置。"""
        self._seed_two_raters(comments=True)
        response = self._agreement()
        assert response.status_code == 200, response.text
        payload = response.json()
        disputes = payload["disagreements"]
        assert len(disputes) == 2
        assert {(row["article_key"], row["item_id"]) for row in disputes} == {
            ("K1", "4"), ("K2", "3")}
        for row in disputes:
            assert set(row) == {"article_key", "item_id", "number", "responses"}
            assert "reason" not in row and "comment" not in row and "note" not in row
        text = json.dumps(payload)
        for forbidden in ("private reason", "private note"):
            assert forbidden not in text, f"一致率载荷泄露自由文本：{forbidden}"

    def test_missing_raters_is_reported_not_guessed(self):
        self._put(self.owner_auth, "K1", OWNER_RESPONSES["K1"])
        payload = self._agreement().json()
        assert payload["rater_count"] == 1
        assert payload["insufficient_raters"] is True
        assert payload["pairwise"] == []
        assert payload["mean_cohen_kappa"] is None

    def test_missing_or_unknown_tool_is_structured_400(self):
        cases = [
            (f"{self.base}/agreement", None, "appraisal.tool_required"),
            (f"{self.base}/export", None, "appraisal.tool_required"),
            (f"{self.base}/grade-input", None, "appraisal.tool_required"),
            (f"{self.base}/agreement", "not-a-tool", "appraisal.tool_unknown"),
            (f"{self.base}/export", "quadas-2", "appraisal.tool_link_only"),
        ]
        for url, tool, message_key in cases:
            params = {} if tool is None else {"tool": tool}
            response = self.client.get(url, params=params, headers=self.owner_auth)
            assert response.status_code == 400, (url, tool, response.text)
            assert response.json()["detail"]["message_key"] == message_key

    # ---- 2. 权限 -------------------------------------------------------
    def test_member_without_consensus_write_is_forbidden(self):
        self._seed_two_raters()
        response = self._agreement(self.peer_auth)
        assert response.status_code == 403, response.text

    def test_owner_and_adjudicator_can_read(self):
        self._seed_two_raters()
        assert self._agreement(self.owner_auth).status_code == 200
        assert self._agreement(self.reconciler_auth).status_code == 200

    # ---- 3. 锁忙 503 ---------------------------------------------------
    @staticmethod
    @contextmanager
    def _exclusive_lock(path: Path):
        """外部进程独占个人库：先切 rollback journal 再 BEGIN EXCLUSIVE。"""
        with closing(sqlite3.connect(path)) as setup:
            setup.execute("PRAGMA journal_mode=DELETE")
        holder = sqlite3.connect(path, timeout=1)
        holder.execute("BEGIN EXCLUSIVE")
        try:
            yield
        finally:
            holder.rollback()
            holder.close()

    def test_locked_member_library_returns_503_without_payload(self):
        self._seed_two_raters()
        locked = Path(self.ctx.owner_db).parent / "zzz-locked.db"
        with closing(sqlite3.connect(locked)) as conn, conn:
            core.ensure_appraisal_storage(conn)
        with mock.patch.object(agreement_api, "SCREENER_READ_TIMEOUT", 0.2), \
                self._exclusive_lock(locked):
            response = self._agreement()
        assert response.status_code == 503, response.text
        body = response.json()["detail"]
        assert body == SCREENER_LIBRARY_UNAVAILABLE_MESSAGE, body
        assert "K1" not in response.text and "Yes" not in response.text

    # ---- 4. 导出 CSV ---------------------------------------------------
    def test_export_csv_columns_and_one_row_per_evaluation(self):
        self._seed_two_raters()
        response = self.client.get(f"{self.base}/export", params={"tool": TOOL},
                                   headers=self.owner_auth)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/csv")
        text = response.content.decode("utf-8-sig")
        lines = [line for line in text.splitlines() if line]
        tool = core.REGISTRY[TOOL]
        item_numbers = [str(item["number"]) for item in core.iter_items(tool)]
        expected_header = ["zotero_key", "title", "tool", *item_numbers,
                           "overall", "rater", "updated_at"]
        assert lines[0] == ",".join(expected_header), lines[0]
        # 两人 × 两文献 = 4 条数据行（一行一评价人）。
        assert len(lines) == 1 + 4, len(lines)
        owner_slug = _slug(self.owner["username"])
        peer_slug = _slug(self.peer["username"])
        rows = [line.split(",") for line in lines[1:]]
        assert {row[0] for row in rows} == {"K1", "K2"}
        assert {row[-2] for row in rows} == {owner_slug, peer_slug}
        assert {row[2] for row in rows} == {TOOL}
        assert {row[1] for row in rows} == {"Alpha qualitative study",
                                            "Beta qualitative study"}
        owner_k1 = next(row for row in rows
                        if row[0] == "K1" and row[-2] == owner_slug)
        item_offset = 3
        assert owner_k1[item_offset:item_offset + 4] == ["Yes", "Yes", "No", "Yes"]
        assert owner_k1[item_offset + len(item_numbers)] == \
            "1=Yes; 2=Yes; 3=No; 4=Yes"

    # ---- 5. GRADE 映射表 ----------------------------------------------
    def test_grade_mapping_tables_expose_three_tiers_per_tool(self):
        for tool_id, table in core.GRADE_ROB_MAP.items():
            assert len(table) >= 3, tool_id
            levels = set(table.values())
            assert levels <= set(core.GRADE_RISK_LEVELS), tool_id
            assert len(levels) == 3, (tool_id, levels)

    def test_grade_mapping_known_values(self):
        cases = [
            ({ "mode": "domains", "judgement": "low" }, "low"),
            ({ "mode": "domains", "judgement": "some_concerns" }, "low"),
            ({ "mode": "domains", "judgement": "high" }, "serious"),
            ({ "mode": "domains", "judgement": "critical" }, "very_serious"),
        ]
        for aggregate, level in cases:
            assert core.study_rating_for_grade("rob2", aggregate)["risk_of_bias"] \
                == level, aggregate
        assert core.study_rating_for_grade(
            "amstar2", {"confidence": "high"})["risk_of_bias"] == "low"
        assert core.study_rating_for_grade(
            "amstar2", {"confidence": "moderate"})["risk_of_bias"] == "serious"
        assert core.study_rating_for_grade(
            "amstar2", {"confidence": "critically_low"})["risk_of_bias"] \
            == "very_serious"
        assert core.study_rating_for_grade(
            "casp-qualitative", {"overall_judgement": "moderate"})["risk_of_bias"] \
            == "serious"
        # MMAT 无官方总体评级：按逐条 Yes 占比分三档。
        assert core.study_rating_for_grade(
            "mmat", {"answer_distribution": {"Yes": 9, "No": 1}})["risk_of_bias"] \
            == "low"
        assert core.study_rating_for_grade(
            "mmat", {"answer_distribution": {"Yes": 5, "No": 5}})["risk_of_bias"] \
            == "serious"
        assert core.study_rating_for_grade(
            "mmat", {"answer_distribution": {"Yes": 1, "No": 9}})["risk_of_bias"] \
            == "very_serious"

    def test_grade_input_endpoint_lists_per_study_levels_read_only(self):
        self._seed_two_raters()
        response = self.client.get(f"{self.base}/grade-input",
                                   params={"tool": TOOL},
                                   headers=self.owner_auth)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["available"] is True
        assert payload["writes_grade_draft"] is False
        studies = {row["article_key"]: row for row in payload["studies"]}
        assert set(studies) == {"K1", "K2"}
        # K1：owner 3/4 Yes、peer 2/4 Yes → 两人都是 moderate → serious；
        # K2：owner 1/4 Yes（low→very_serious）、peer 2/4（serious）→ 取最严重。
        assert studies["K1"]["risk_of_bias"] == "serious"
        assert studies["K1"]["basis"] == "worst_of_raters"
        assert studies["K2"]["risk_of_bias"] == "very_serious"
        assert {row["article_key"] for row in payload["grade_ready"]} == {"K1", "K2"}
        # 只读：再次读取结果不变，且记录表未被 GRADE 端点改写。
        again = self.client.get(f"{self.base}/grade-input", params={"tool": TOOL},
                                headers=self.owner_auth).json()
        assert again["studies"] == payload["studies"]

    # ---- 6. 终审 #03：三端点权限闸逐端点红钉 + agreement 桌面 404 ------

    def test_export_forbidden_to_plain_member_without_payload(self):
        """export 逐端点权限钉：普通成员（无 consensus:write）403 且零内容。"""
        self._seed_two_raters()
        response = self.client.get(f"{self.base}/export", params={"tool": TOOL},
                                   headers=self.peer_auth)
        assert response.status_code == 403, response.text
        # 拒绝体绝不携带跨人评价内容（文献键/逐人应答）。
        assert "K1" not in response.text and "K2" not in response.text, response.text
        assert "Yes" not in response.text and "No" not in response.text, response.text

    def test_grade_input_forbidden_to_plain_member_without_levels(self):
        """grade-input 逐端点权限钉：普通成员 403 且档位/逐人明细不出现在响应。"""
        self._seed_two_raters()
        response = self.client.get(f"{self.base}/grade-input",
                                   params={"tool": TOOL},
                                   headers=self.peer_auth)
        assert response.status_code == 403, response.text
        for leak in ("risk_of_bias", "serious", "very_serious", "grade_ready",
                     "studies", "K1", "K2"):
            assert leak not in response.text, (leak, response.text)

    def test_owner_and_reconciler_reach_export_and_grade_input(self):
        """对照腿：属主与 reconciler（consensus:write）三端点均 200。"""
        self._seed_two_raters()
        for auth in (self.owner_auth, self.reconciler_auth):
            assert self._agreement(auth).status_code == 200
            exported = self.client.get(f"{self.base}/export",
                                       params={"tool": TOOL}, headers=auth)
            assert exported.status_code == 200, exported.text
            graded = self.client.get(f"{self.base}/grade-input",
                                     params={"tool": TOOL}, headers=auth)
            assert graded.status_code == 200, graded.text
            assert graded.json()["available"] is True

    def test_agreement_desktop_mode_is_structured_404(self):
        """agreement 桌面模式 404 闸：非 server 模式结构化 404（先于权限判定）。"""
        self._seed_two_raters()
        with mock.patch.dict(os.environ, {"REVIEWFLOW_MODE": "desktop"}):
            response = self._agreement()
        assert response.status_code == 404, response.text
        detail = response.json()["detail"]
        assert detail["error_code"] == "appraisal_agreement_desktop_mode", detail
        assert detail["message_key"] == "appraisal.agreement.desktop_mode", detail
        assert "K1" not in response.text

    # ---- C2 顺序泄漏复现钉（sec2 登记：先跑 pdf_fetch 时本套件撞环境泄漏） --
    #
    # 泄漏机理：pytest 先**收集**（此时执行两个模块的模块级代码——本文件在
    # 收集期设 REVIEWFLOW_MODE=server / COBOOKSHELF_DATA_DIR=本模块临时目录），
    # 再按文件顺序跑用例。test_pdf_fetch_api 的 tearDownModule（r13 测试卫生）
    # 在它的用例全部结束后、本套件用例开始前，把这两个变量还原/弹回收集前的
    # 值（未设置）。本文件的模块级赋值早已发生、救不了测试期，而 server-only
    # 端点（collaboration invite / appraisal 三端点）与 auth 用户库都在**请求
    # 期**读 os.environ → setUp 内 invite 404「团队协作仅在服务器模式下开放」
    # （2026-11 合跑实测 19 failed 即此签名）。
    #
    # step1 在本类内复刻该中间状态（弹掉两个变量且**不恢复**——这正是先跑
    # 模块收尾后进程的真实状态）；step2 紧随其后装配任务并打 server-only
    # 端点：修复（每用例 setUp 内重申环境，见 setUp）后必须全绿；退回旧的
    # 「只在模块级设一次」行为，step2 以同一 404 签名红。

    def test_order_leak_step1_simulate_prior_module_env_restore(self):
        """step1：模拟先跑模块 tearDownModule 的环境还原（不恢复，留状态）。"""
        os.environ.pop("REVIEWFLOW_MODE", None)
        os.environ.pop("COBOOKSHELF_DATA_DIR", None)

    def test_order_leak_step2_server_endpoints_still_usable(self):
        """step2：紧随其后，server-only 装配与端点必须仍然可用。"""
        self._seed_two_raters()
        response = self._agreement()
        assert response.status_code == 200, response.text
        exported = self.client.get(f"{self.base}/export",
                                   params={"tool": TOOL}, headers=self.owner_auth)
        assert exported.status_code == 200, exported.text

    def test_module_teardown_restores_env_hygiene(self):
        """r3 卫生钉（包 J 根修正后的语义）：模块边界**保证 server + 所有者
        根**，绝不还原成导入前的值（unset 抹除），也绝不自删共享根。

        r2 版把 tearDownModule 做成"还原 _ORIGINAL_ENV"——pytest 模块级 env
        赋值在收集期、tearDownModule 在测试期，两文件直跑时后续套件（w5/w7
        这类不重钉 env 的）收集期赋的 server 被抹掉，实测 `pytest 本文件
        w7` → 44 errors（评审 r3 阻断项）。正确语义是保证而非抹除。

        包 J（F 线移交族）：本模块原先自建私有临时根并在模块末 cleanup——
        pytest 下 main.DATA_DIR 已冻结在会话根，私有根令团队写窗找不到
        accounts（26F 签名）。现改为**借用** ``_bare_env`` 所有者根，模块末
        只归还 borrow token；根的删除/两键恢复属于所有者收尾。本钉按新
        语义验证接线（保证 + 归还，顺序固定；以桩验证不真改环境）。
        """
        os.environ.pop("REVIEWFLOW_MODE", None)
        os.environ.pop("COBOOKSHELF_DATA_DIR", None)
        _guarantee_module_env()
        assert os.environ.get("REVIEWFLOW_MODE") == "server", \
            "模块边界必须保证 server 模式（不抹除）"
        assert os.environ.get("COBOOKSHELF_DATA_DIR") == MODULE_ROOT, \
            "模块边界必须保证所有者数据根（不抹除）"
        # 接线：tearDownModule = 环境保证 + 归还所有者 borrow（不删根）。
        calls: list[str] = []
        module = sys.modules[__name__]
        real_guarantee = module._guarantee_module_env
        real_release = module._bare_env.release

        def _release(token):
            calls.append("release")
            real_release(token)

        with mock.patch.object(
                module, "_guarantee_module_env",
                side_effect=lambda: (calls.append("guarantee"),
                                     real_guarantee())), \
                mock.patch.object(module._bare_env, "release",
                                  side_effect=_release):
            tearDownModule()
        assert calls == ["guarantee", "release"], \
            f"tearDownModule 接线=先保证环境后归还借用：实际 {calls}"


class GradeMappingPureTests(unittest.TestCase):
    """不依赖 HTTP 的纯函数钉（GRADE 映射表每工具至少三档）。"""

    def test_every_registry_tool_has_a_mapping_branch(self):
        for tool_id, tool in core.REGISTRY.items():
            family = core._grade_tool_key(tool)
            assert family in core.GRADE_ROB_MAP, (tool_id, family)

    def test_unknown_tool_rejected(self):
        with self.assertRaises(ValueError):
            core.study_rating_for_grade("not-a-tool", {})

    def test_plain_overall_string_is_accepted(self):
        assert core.study_rating_for_grade("rob2", "high")["risk_of_bias"] \
            == "serious"
        assert core.study_rating_for_grade("amstar2", "high")["risk_of_bias"] \
            == "low"

    def test_missing_overall_is_unavailable_not_guessed(self):
        result = core.study_rating_for_grade("rob2", {})
        assert result["available"] is False
        assert result["risk_of_bias"] is None
        assert result["rule"] == "no_overall"


class ExportFormulaInjectionPins(unittest.TestCase):
    """终审 #02：评价导出 CSV 公式注入防护（成员可控 article_key 落格）。"""

    FORMULA_KEYS = [
        '=HYPERLINK("http: spoofed.evilexample.net","click")',  # 无斜杠形态
        "@SUM(A1:A9)",
        "-2+3",
        "=1+1",
    ]

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        # r2 测试卫生：用户名按次随机（先例模块的环境泄漏会让 data/ 落 cwd、
        # 固定用户名跨根复跑撞「筛选员数据路径冲突」——随机后任何文件顺序、
        # 任何残留状态下都成立）。
        import uuid as _uuid

        # C2 顺序泄漏修复：与 AppraisalAgreementTests 同因——类装配建用户
        # 前重申收集期环境（先跑模块的 tearDownModule 可能已抹掉）。
        _guarantee_module_env()
        suffix = _uuid.uuid4().hex[:6]
        cls.owner = auth_mod.create_user(
            f"r215c-csv-owner-{suffix}@example.test", "R215C-CSV-2026!")
        cls.peer = auth_mod.create_user(
            f"r215c-csv-peer-{suffix}@example.test", "R215C-CSV-Peer-2026!")
        cls.owner_auth = {"Authorization": "Bearer " + auth_mod.create_access_token(
            cls.owner["id"], cls.owner["username"])}
        cls.peer_auth = {"Authorization": "Bearer " + auth_mod.create_access_token(
            cls.peer["id"], cls.peer["username"])}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        # C2 顺序泄漏修复（用例内还原）：与 AppraisalAgreementTests.setUp 同因。
        os.environ["REVIEWFLOW_MODE"] = "server"
        os.environ["COBOOKSHELF_DATA_DIR"] = MODULE_ROOT
        response = self.client.post(
            "/api/tasks",
            # 包 J：任务名按方法名截短（原 `csv-formula-{全名}` 令任务目录 +
            # 成员库名 + trace 首次迁移备份边车名超过 Windows MAX_PATH 260，
            # 团队初筛写触发 member_prepare_trace 建 trace schema 时
            # `backup` 打不开临时文件 → 500；纯环境路径长度伪影，非产品缺陷。
            # 截短后仍按方法名唯一，测试语义不变。
            json={"name": f"csv-{self._testMethodName[5:][:20]}"},
            headers=self.owner_auth)
        assert response.status_code in (200, 201), response.text
        self.task = response.json()["task_id"]
        self.base = f"/api/tasks/{self.task}/appraisal"
        invite = self.client.post(
            f"/api/tasks/{self.task}/collaboration/invite",
            json={"username": self.peer["username"], "role": "coder"},
            headers=self.owner_auth)
        assert invite.status_code == 201, invite.text
        ctx = app.state.review_workflow.context(
            self.task, {"user_id": self.owner["id"],
                        "username": self.owner["username"]})
        db_mod.upsert_articles(Path(ctx.owner_db), ARTICLES, [])
        # F 线移交族：同 AppraisalAgreementTests——受邀后即 team，公式形状
        # 键须先过团队链与存储准备才能落 appraisal_records（否则 409 上游
        # 未清/收据未准备）。导出防护断言（_csv_cell ' 前缀）原样不变。
        # 题录须含公式形状键本身（canonical 写域 = 属主库 articles 行）。
        formula_articles = [*ARTICLES, *(
            Article(zotero_key=key, title="Formula-shaped article")
            for key in self.FORMULA_KEYS)]
        _configure_team_write_fixture(
            self.client, self.task, self.owner, [(self.peer, "coder")],
            articles=formula_articles,
            vote_keys=["K1", *self.FORMULA_KEYS])

    def _export_rows(self):
        response = self.client.get(f"{self.base}/export", params={"tool": TOOL},
                                   headers=self.owner_auth)
        assert response.status_code == 200, response.text
        text = response.content.decode("utf-8-sig")
        import csv as _csv
        import io as _io
        return list(_csv.reader(_io.StringIO(text)))

    def test_formula_shaped_article_keys_are_prefixed(self):
        for key in self.FORMULA_KEYS:
            with self.subTest(key=key):
                put = self.client.put(f"{self.base}/{key}",
                                      json={"tool": TOOL,
                                            "responses": OWNER_RESPONSES["K1"],
                                            "note": ""},
                                      headers=self.peer_auth)
                assert put.status_code == 200, put.text
        rows = self._export_rows()
        by_key = {row[0]: row for row in rows[1:]}
        for key in self.FORMULA_KEYS:
            cell = by_key.get(key, by_key.get("'" + key, [None]))[0]
            self.assertIsNotNone(cell, f"{key!r} 未出现在导出")
            self.assertTrue(cell.startswith("'"),
                            f"公式形状单元格必须以 ' 开头：{cell!r}")
        # 表头同样过防护（无公式形状则原样）。
        self.assertEqual(rows[0][0], "zotero_key")

    def test_plain_cells_not_prefixed(self):
        put = self.client.put(f"{self.base}/K1",
                              json={"tool": TOOL, "responses": OWNER_RESPONSES["K1"],
                                    "note": ""},
                              headers=self.owner_auth)
        assert put.status_code == 200, put.text
        rows = self._export_rows()
        by_key = {row[0]: row for row in rows[1:]}
        self.assertEqual(by_key["K1"][0], "K1", "普通文本不加前缀")
        self.assertEqual(by_key["K1"][1], "Alpha qualitative study")

    def test_mutation_csv_guard_removed_pin_goes_red(self):
        """破坏性变异：_csv_cell 换成恒等函数 → 公式键无前缀落格——上面的钉必红。"""
        from custom_backend import appraisal_agreement_api as api_mod
        key = "=1+1"
        put = self.client.put(f"{self.base}/{key}",
                              json={"tool": TOOL, "responses": OWNER_RESPONSES["K1"],
                                    "note": ""},
                              headers=self.peer_auth)
        assert put.status_code == 200, put.text
        with mock.patch.object(api_mod, "_csv_cell", lambda value: str(value)):
            rows = self._export_rows()
        by_key = {row[0]: row for row in rows[1:]}
        self.assertEqual(by_key["=1+1"][0], "=1+1",
                         "无防护时公式形状原样落格（钉必红）")


if __name__ == "__main__":
    unittest.main()
