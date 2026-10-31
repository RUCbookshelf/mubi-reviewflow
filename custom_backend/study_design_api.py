"""W4 预置分类器 API（B3 空白格泳道；2.1.4-r8）。

单一只读端点：``GET /api/tasks/{task_id}/study-designs`` —— 对当前任务
**筛选目录**（与初筛队列同一权威来源：工作流 runtime 的
``eligible(ctx, 'screen')`` 键集 × 属主库完整题录，含 ``raw_json``）做研究
设计分类，返回 ``{"rule_version", "count", "items": {zotero_key: 分类结果}}``。

治理红线落实：

- **纯本地零出站**：分类是 ``coscreen/al/study_design.py`` 的正则启发式，
  不联网、无任何定时/后台触发；无开关即无「默认开」的联网面——本端点只在
  初筛页前端模块请求时计算一次。
- **失败优雅降级绝不 500**：端点把 **全部**导入（含 ``coscreen.al.study_design``
  与 ``coscreen.models``）、上下文解析、读库、分类都包在同一个 try 内；任何
  异常降级为 ``200 + {"available": false, "items": {}, ...}``。钉子分别注入
  读库异常与模块导入异常断言两条路径都不产生 5xx。
- **零写副作用**：不调用 ``_screener_db``（该助手会 ``init_db`` 新建成员库），
  改为自行解析属主库路径 + runtime 的只读读取（``article_rows`` 对不存在的
  库直接返回 ``[]``）。成员库尚未同步时返回**属主目录全量**，而非误导性的
  ``count=0``。
- **隐私披露**：只访问本应用自身的任务目录，无任何回环/外部端点访问。

判定结果与 ``docs/methodology/study_design_classifier.md`` 披露的
precision/recall 走同一条代码路径（``study_design.classify_corpus``）。
"""

from __future__ import annotations

from fastapi import Depends, HTTPException

from coscreen.tasks import slugify_screener

#: 端点单次响应的文献数上限（防御性上限）。超限时 ``truncated=true``，
#: 前端把 truncated 视作整体不可用（不展示部分徽标）——见
#: ``custom_frontend/study_design_badge.js`` 的 truncated 分支与
#: 方法学文档 §6：绝不静默丢弃，也绝不让部分结果冒充「未识别」。
MAX_ITEMS_PER_RESPONSE = 20_000

#: 导入失败时降级响应里的规则版本兜底（导入不可用时无从读取真实常量；
#: 响应同时带 available=false，前端不使用任何判定）。
_RULE_VERSION_FALLBACK = 1


def _degraded(error: str = "study design classification unavailable") -> dict:
    return {"rule_version": _RULE_VERSION_FALLBACK, "count": 0, "items": {},
            "available": False, "truncated": False, "error": error}


def register_study_design_routes(app, require_user, task_or_404, task_visible) -> None:
    """在 ``app`` 上注册 W4 分类器端点（只读；main.py 追加式注册行调用）。"""

    @app.get("/api/tasks/{task_id}/study-designs")
    def study_designs(task_id: str, user: dict = Depends(require_user)) -> dict:
        """任务筛选目录全量文献的研究设计分类（纯本地，见模块 docstring）。

        降级契约：任何异常 → 200 + available=false（绝不 500）。
        """
        # 门禁：任务不存在 404 / 不可见 403（与其余端点同口径）。
        info = task_or_404(task_id)
        if not task_visible(info, user):
            raise HTTPException(403, "Task access denied.")

        try:
            import json as _json

            from coscreen import task_collaboration as collaboration_mod
            from coscreen.al.study_design import RULE_VERSION, classify_corpus
            from coscreen.models import Article

            runtime = app.state.review_workflow
            collab = collaboration_mod.shared_store_path(info.dir)
            owner = collaboration_mod.get_owner(collab, task_id=info.task_id)
            # 属主库路径按 slug 自行解析（**不**经 runtime.screener_db / _screener_db，
            # 二者都会 init_db 新建库）：legacy 任务归当前用户，团队任务归属主。
            owner_name = str(owner["owner_username"]) if owner is not None else user["username"]
            owner_db = info.dir / (slugify_screener(owner_name) + ".db")
            # 权威键集：属主库 + is_duplicate_of/待审标题重复组过滤（与初筛队列
            # 同一逻辑）；只读路径——不 init 任何库、不写评审表。
            ctx = _ContextLike(info, owner_db)
            rows, _saved = runtime.eligible(ctx, "screen")
            keys = [row["zotero_key"] for row in rows]
            truncated = len(keys) > MAX_ITEMS_PER_RESPONSE
            keys = keys[:MAX_ITEMS_PER_RESPONSE]
            # eligible 只给紧凑列（无 abstract/raw_json）——按完整题录回读同一库，
            # 保持键序与条目集不变（article_rows 对不存在的库返回 []）。
            full = {row["zotero_key"]: row for row in runtime.article_rows(owner_db)}
            articles = []
            for key in keys:
                row = full.get(key)
                if row is None:
                    continue
                raw = {}
                raw_json = row.get("raw_json")
                if isinstance(raw_json, str) and raw_json.strip():
                    try:
                        parsed = _json.loads(raw_json)
                        raw = parsed if isinstance(parsed, dict) else {}
                    except ValueError:
                        raw = {}
                articles.append(Article.from_dict({**row, "raw": raw}).to_dict())
            items = classify_corpus(articles)
        except Exception:  # noqa: BLE001 —— 只读端点整体降级：徽标消失，绝不 500
            return _degraded()

        return {"rule_version": RULE_VERSION, "count": len(items), "items": items,
                "available": True, "truncated": truncated}


class _ContextLike:
    """``runtime.eligible`` 只用到 ``owner_db``／``actor_db``（两者此处同路径：
    键集来自属主目录；本人决策在只读判断里不需要，``saved_rows`` 对不存在的表
    返回 ``{}``）。因此不必构造 ``WorkflowContext``——后者会经 screener_db 建库。"""

    __slots__ = ("info", "owner_db", "actor_db")

    def __init__(self, info, owner_db) -> None:
        self.info = info
        self.owner_db = owner_db
        self.actor_db = owner_db
