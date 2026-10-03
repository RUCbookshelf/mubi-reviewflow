"""活体综述监控器 —— 检索式记忆 + 增量导入管线（衍生产品规格 §5.2）。

纯计算模块：不含任何网络请求（PubMed 自动监控是 custom_backend/living_review_plugin.py
里的可选插件，本模块永不知晓它的存在），导入零副作用（不连库不起服务——只有显式
调用管线函数才写库）。

增量更新管线（:func:`process_incremental_update`，规格 §5.2 七步）：

1. 解析新文件（coscreen.parsers）；
2. 与现有库合并去重（coscreen.dedup.find_duplicates，与 /api/tasks/{id}/import
   同一算法——新条目既与库内条目去重，也在批内去重）；
3. 导入新增文献（coscreen.db.upsert_articles，重复条目同样入库并打
   is_duplicate_of 标记，与主导入端点语义一致）；
4. 对新增文献运行 AL 排序（热启动——用库内已有标签训练，新增文献全部作为
   未筛候选进入队列；特征/模型完全复用 coscreen.al，不引入新特征参数，
   不触碰 CJK bigram 行为）；
5. 重跑 Meta 分析（新旧全部研究：coscreen.review_analysis.synthesize 按当前
   库内全部已选效应量合并）；
6. 对比前后合并结果（结论翻转 = CI 跨 null 状态变化，在分析尺度上判定）；
7. 生成人可读变更报告。

结论翻转判定（规格 §5.2 ``conclusion_changed`` 注释「CI 跨 null 状态变化」）：
对 RR/OR/HR 等比值类指标，synthesize 返回的是对数尺度 ``ci_analysis_low/high``，
null = 0（即展示尺度的 1）；MD/SMD/RD 等差值类指标本身在自然尺度，null 同为 0。
故统一用分析尺度 CI 是否包含 0 判定，两侧任一不可得时翻转记 False（无法比较）
并在报告中说明原因——不做任何静默的方向假设。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence

from coscreen import db as db_mod
from coscreen.al.ranker import ActiveLearningRanker
from coscreen.dedup import find_duplicates
from coscreen.models import Article
from coscreen.parsers import parse_file
from coscreen.review_analysis import save_effect, save_study, synthesize

__all__ = [
    "SUPPORTED_DATABASES",
    "SearchRecord",
    "IncrementalResult",
    "ci_crosses_null",
    "process_incremental_update",
    "process_incremental_articles",
    "record_to_dict",
    "record_from_dict",
]

#: SearchRecord.database 的合法取值（规格 §5.2）
SUPPORTED_DATABASES = ("pubmed", "embase", "cochrane", "manual")

#: synthesize 结果中进入 IncrementalResult.meta_before/meta_after 的摘要键
_META_SUMMARY_KEYS = (
    "n_studies", "study_ids", "measure", "model", "method", "ci_method",
    "pooled_analysis", "ci_analysis_low", "ci_analysis_high",
    "pooled", "ci_low", "ci_high", "i2_percent",
)


# ---------------------------------------------------------------------------
# 数据模型（规格 §5.2）
# ---------------------------------------------------------------------------

@dataclass
class SearchRecord:
    """已保存的检索式（检索式记忆）。"""

    database: str            # "pubmed" | "embase" | "cochrane" | "manual"
    query_string: str
    last_run_date: str = ""  # 上次检索日期（YYYY-MM-DD；未执行过为空串）
    url: str = ""            # 可点击链接（仅 PubMed 有）
    total_hits_last: int = 0  # 上次检索的总命中数
    saved_at: str = ""       # 保存时间（ISO8601；序列化层扩展字段）

    def __post_init__(self) -> None:
        if self.database not in SUPPORTED_DATABASES:
            raise ValueError(
                f"非法 database: {self.database!r}，必须为 {SUPPORTED_DATABASES} 之一"
            )
        if not self.query_string.strip():
            raise ValueError("query_string 不能为空")


@dataclass
class IncrementalResult:
    """增量导入的处理结果（规格 §5.2）。"""

    new_articles: list = field(default_factory=list)   # 新增文献 dict（去重后）
    duplicates_removed: int = 0
    al_prioritized: list = field(default_factory=list)  # [(zotero_key, score)]
    meta_before: dict = field(default_factory=dict)     # 之前的合并结果摘要
    meta_after: dict = field(default_factory=dict)      # 加入新文献后的合并结果摘要
    conclusion_changed: bool = False                    # CI 跨 null 状态变化
    change_report: str = ""                             # 人可读的变更摘要
    al_available: bool = True                           # 扩展：AL 是否可用（标签不足为 False）
    al_note: str = ""                                   # 扩展：AL 不可用原因 / 模型说明

    def to_dict(self) -> dict:
        return {
            "new_articles": self.new_articles,
            "duplicates_removed": self.duplicates_removed,
            "al_prioritized": [
                {"zotero_key": key, "score": float(score)}
                for key, score in self.al_prioritized
            ],
            "meta_before": self.meta_before,
            "meta_after": self.meta_after,
            "conclusion_changed": self.conclusion_changed,
            "change_report": self.change_report,
            "al_available": self.al_available,
            "al_note": self.al_note,
        }


def record_to_dict(record: SearchRecord) -> dict:
    return {
        "database": record.database,
        "query_string": record.query_string,
        "last_run_date": record.last_run_date,
        "url": record.url,
        "total_hits_last": int(record.total_hits_last),
        "saved_at": record.saved_at,
    }


def record_from_dict(d: Mapping) -> SearchRecord:
    return SearchRecord(
        database=str(d.get("database") or ""),
        query_string=str(d.get("query_string") or ""),
        last_run_date=str(d.get("last_run_date") or ""),
        url=str(d.get("url") or ""),
        total_hits_last=int(d.get("total_hits_last") or 0),
        saved_at=str(d.get("saved_at") or ""),
    )


# ---------------------------------------------------------------------------
# Meta 对比（结论翻转 = CI 跨 null 状态变化）
# ---------------------------------------------------------------------------

def _meta_summary(result: dict) -> dict:
    """synthesize 完整结果 -> 增量结果携带的摘要（剔除逐研究效应量行）。"""
    return {key: result[key] for key in _META_SUMMARY_KEYS if key in result}


def _meta_unavailable(reason: str) -> dict:
    """合并结果不可得时的占位（始终是 dict，便于 JSON 序列化）。"""
    return {"available": False, "reason": reason}


def _meta_available(summary: dict) -> bool:
    return bool(summary.get("available", True)) and summary.get("pooled_analysis") is not None


def ci_crosses_null(summary: Mapping) -> bool | None:
    """分析尺度 CI 是否包含 null（0）。

    返回 ``None`` 表示无法判定（合并结果不可得，或 CI 边界缺失）。
    """
    if not _meta_available(dict(summary)):
        return None
    low, high = summary.get("ci_analysis_low"), summary.get("ci_analysis_high")
    if low is None or high is None:
        return None
    return float(low) <= 0.0 <= float(high)


def _conclusion_changed(before: Mapping, after: Mapping) -> bool:
    """CI 跨 null 状态变化：任一侧无法判定时不称翻转（False），由报告说明。"""
    crosses_before = ci_crosses_null(before)
    crosses_after = ci_crosses_null(after)
    if crosses_before is None or crosses_after is None:
        return False
    return crosses_before != crosses_after


# ---------------------------------------------------------------------------
# 变更报告（人可读，中文；与后端其余用户可见文案口径一致）
# ---------------------------------------------------------------------------

def _fmt(value: Any) -> str:
    """数值展示：float 保留 3 位有效小数，None 显示为 N/A。"""
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _meta_line(label: str, summary: Mapping, measure: str) -> str:
    if not _meta_available(dict(summary)):
        return f"- {label}：无法计算（{summary.get('reason', '未知原因')}）"
    return (
        f"- {label}：{measure} = {_fmt(summary.get('pooled'))}"
        f"（95%CI {_fmt(summary.get('ci_low'))} ~ {_fmt(summary.get('ci_high'))}，"
        f"k = {_fmt(summary.get('n_studies'))}，I² = {_fmt(summary.get('i2_percent'))}%）"
    )


def _build_change_report(
    *,
    when: str,
    comparison: str,
    outcome: str,
    timepoint: str,
    measure: str,
    n_new: int,
    duplicates_removed: int,
    duplicates_against_db: int,
    al_available: bool,
    al_note: str,
    al_prioritized: Sequence[tuple[str, float]],
    meta_before: Mapping,
    meta_after: Mapping,
    conclusion_changed: bool,
    n_new_effects: int,
) -> str:
    lines = [
        f"活体综述增量更新报告（{when}）",
        f"结局层：{comparison} / {outcome} / {timepoint} / {measure}",
        f"- 新增文献：{n_new} 篇（去重移除 {duplicates_removed} 篇"
        + (f"，其中与库内既有文献重复 {duplicates_against_db} 篇"
           if duplicates_against_db else "")
        + "）",
    ]
    if al_available:
        head = "、".join(f"{key}（{score:.2f}）" for key, score in al_prioritized[:5])
        lines.append(f"- AL 热启动排序（{al_note}）：新增文献优先队列前 {min(5, len(al_prioritized))} 项：{head or '（无新增文献）'}")
    else:
        lines.append(f"- AL 排序不可用（{al_note}），新增文献按导入顺序排列")

    lines.append(_meta_line("更新前合并结果", meta_before, measure))
    lines.append(_meta_line("更新后合并结果", meta_after, measure))

    crosses_before, crosses_after = ci_crosses_null(meta_before), ci_crosses_null(meta_after)
    if conclusion_changed:
        # 状态一定两侧均可判定（否则 _conclusion_changed 为 False）
        before_text = "包含 0" if crosses_before else "不含 0"
        after_text = "包含 0" if crosses_after else "不含 0"
        lines.append(f"- ⚠ 结论翻转：更新前 CI {before_text}，更新后 CI {after_text}")
    elif crosses_before is None or crosses_after is None:
        lines.append("- 结论未翻转（合并结果至少一侧不可得，无法比较 CI 跨 null 状态）")
    elif crosses_before != crosses_after:  # pragma: no cover - _conclusion_changed 兜底
        lines.append("- ⚠ 结论翻转：CI 跨 null 状态变化")
    else:
        state = "仍不含 0" if not crosses_after else "仍包含 0"
        lines.append(f"- 结论未翻转：CI {state}")
    if n_new and not n_new_effects:
        lines.append("- 新增文献尚无效应量数据：请先筛选并完成数据提取，再评估合并结果影响")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 增量管线
# ---------------------------------------------------------------------------

def _normalize_new_effects(
    new_effects: Sequence[Mapping] | None,
    usable_keys: set[str],
) -> list[dict]:
    """校验可选的新研究效应量行；引用不可用文献（不存在/重复条目）即 ValueError。

    每行：``{"zotero_key", "study_id", "estimate", "se"}``，可选
    ``"label"`` / ``"source_locator"``。measure 由管线统一使用调用方指定的
    指标（与既有结局层一致，不允许在增量里静默混入别的指标）。
    """
    if not new_effects:
        return []
    prepared: list[dict] = []
    for i, row in enumerate(new_effects, start=1):
        try:
            key = str(row["zotero_key"]).strip()
            study_id = str(row["study_id"]).strip()
            estimate = float(row["estimate"])
            se = float(row["se"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"new_effects 第 {i} 行缺少/非法字段（zotero_key/study_id/estimate/se）：{exc}") from exc
        if not key or not study_id:
            raise ValueError(f"new_effects 第 {i} 行 zotero_key 与 study_id 不能为空")
        if key not in usable_keys:
            raise ValueError(
                f"new_effects 第 {i} 行引用的文献 {key!r} 不在库中或为重复条目"
            )
        prepared.append({
            "zotero_key": key,
            "study_id": study_id,
            "label": str(row.get("label") or study_id),
            "estimate": estimate,
            "se": se,
            "source_locator": str(row.get("source_locator") or ""),
        })
    return prepared


def process_incremental_update(
    existing_db_path: str,
    new_articles_file: str,  # RIS/CSV 文件路径
    comparison: str, outcome: str, timepoint: str, measure: str,
    *,
    new_effects: Sequence[Mapping] | None = None,
    model: str = "fixed",
    ci_method: str = "normal",
    al_ranker: ActiveLearningRanker | None = None,
) -> IncrementalResult:
    """增量更新的完整处理管线（100% 本地计算；规格 §5.2 七步，见模块 docstring）。

    ``new_effects``（可选扩展）：随本次导入一并登记的新研究效应量
    （``zotero_key`` 须指向本次新增或库内既有非重复文献）。增量导入的题录
    本身不含效应量——若不提供，第 5/6 步的前后合并结果一致，报告会说明
    「新增文献尚无效应量数据」。
    ``model``/``ci_method`` 透传 :func:`coscreen.review_analysis.synthesize`
    （缺省与其一致）；``al_ranker`` 缺省用 :class:`ActiveLearningRanker`
    默认构造（调用方可注入任务级策略/种子/预设——热启动仍用库内已有标签）。
    """
    articles = parse_file(new_articles_file)
    return process_incremental_articles(
        existing_db_path, articles, comparison, outcome, timepoint, measure,
        new_effects=new_effects, model=model, ci_method=ci_method,
        al_ranker=al_ranker,
    )


def process_incremental_articles(
    existing_db_path: str,
    new_articles: Sequence[Article],
    comparison: str, outcome: str, timepoint: str, measure: str,
    *,
    new_effects: Sequence[Mapping] | None = None,
    model: str = "fixed",
    ci_method: str = "normal",
    al_ranker: ActiveLearningRanker | None = None,
) -> IncrementalResult:
    """:func:`process_incremental_update` 的核心实现，输入为已解析的 Article 列表。

    自动监控插件（check-now）拉回的文献没有中间文件，直接走本入口复用同一条
    管线；文件入口只是多一步解析。
    """
    db_path = existing_db_path

    # ---- 1/2. 与现有库合并去重（与主导入端点同一算法）----
    # existing 含重复标记行（用于重导判定）；usable 是库内非重复行
    # （save_study 拒绝重复标记行，新效应量只能挂在非重复文献上）。
    existing = db_mod.list_articles(db_path, include_duplicates=True)
    existing_keys = {a.zotero_key for a in existing}
    existing_usable = {a.zotero_key for a in db_mod.list_articles(db_path)}
    incoming = list(new_articles)
    if not incoming:
        raise ValueError("增量文件中没有任何文献记录")

    _, report = find_duplicates(existing + incoming)
    pairs = report.exact_pairs + report.fuzzy_pairs
    # 与库重复（两类，均不算新增）：
    #   a) 同键重导——zotero_key 已在库内（find_duplicates 产生 kept==dup 的
    #      自指配对，dup_key 在 existing_keys 中，靠键判定而不是靠配对）；
    #   b) 异键同 DOI/模糊——配对的保留条目在库内、重复条目来自新文件。
    # 批内重复（配对双方都来自新文件）与库无关，单独计。
    dup_against_db: set[str] = {
        a.zotero_key for a in incoming if a.zotero_key in existing_keys
    }
    batch_dup_keys: set[str] = set()
    for p in pairs:
        if p.dup_key in existing_keys or p.dup_key in dup_against_db:
            continue  # a) 已按键计入
        if p.kept_key in existing_keys:
            dup_against_db.add(p.dup_key)  # b)
        else:
            batch_dup_keys.add(p.dup_key)  # 批内
    dup_keys = dup_against_db | batch_dup_keys
    kept_new = [a for a in incoming if a.zotero_key not in dup_keys]
    duplicates_removed = len(incoming) - len(kept_new)
    duplicates_against_db = len(dup_against_db)

    # ---- 5(预). 校验随行的新研究效应量（在写库之前失败：库零变更）----
    prepared_effects = _normalize_new_effects(
        new_effects, existing_usable | {a.zotero_key for a in kept_new})

    # ---- 6(前). 合并结果快照（必须在导入/登记效应量之前计算）----
    meta_before = _run_meta(db_path, comparison, outcome, timepoint, measure,
                            model=model, ci_method=ci_method)

    # ---- 3. 导入新增文献（重复条目同样入库并打 is_duplicate_of 标记）----
    db_mod.upsert_articles(db_path, incoming, pairs)

    # ---- 5(前半). 登记随行的新研究效应量（先于 meta_after，后于 meta_before）----
    for row in prepared_effects:
        try:
            save_study(db_path, row["study_id"], row["label"], [row["zotero_key"]])
            save_effect(db_path, row["study_id"], comparison, outcome, timepoint,
                        measure, row["estimate"], row["se"], row["zotero_key"],
                        {"source_locator": row["source_locator"]},
                        entry_method="living_review",
                        source_locator=row["source_locator"])
        except ValueError as exc:
            raise ValueError(f"新研究效应量登记失败（{row['study_id']}）：{exc}") from exc

    # ---- 4. AL 热启动排序：库内已有标签训练，新增文献作为候选 ----
    decisions = {
        r["zotero_key"]: r["decision"] for r in db_mod.list_decisions(db_path)
    }
    ranker = al_ranker if al_ranker is not None else ActiveLearningRanker()
    ranking = ranker.rank(db_mod.list_articles(db_path, include_duplicates=False),
                          decisions)
    new_keys = [a.zotero_key for a in kept_new]
    if ranking is None:
        n_labeled = sum(1 for d in decisions.values() if d in ("include", "exclude"))
        al_prioritized = [(key, 0.0) for key in new_keys]
        al_available = False
        al_note = f"已有标签 {n_labeled} 个，少于 min_labeled={ranker.min_labeled}"
    else:
        rank_of = {key: pos for pos, key in enumerate(ranking.order)}
        new_set = set(new_keys)
        al_prioritized = [
            (key, float(ranking.scores.get(key, 0.0)))
            for key in ranking.order if key in new_set
        ]
        # 排序器候选队列理论覆盖全部未筛文献；防御性兜底保序追加（不丢条目）
        ranked_keys = {key for key, _ in al_prioritized}
        al_prioritized.extend(
            (key, 0.0) for key in new_keys if key not in ranked_keys
        )
        al_available = True
        al_note = (f"策略 {ranking.strategy}，热启动标签 {ranking.n_labeled} 个，"
                   f"种子 {ranking.seed}")

    # ---- 5(后半)/6(后). 重跑 Meta（新旧全部研究）并对比 ----
    meta_after = _run_meta(db_path, comparison, outcome, timepoint, measure,
                           model=model, ci_method=ci_method)
    conclusion_changed = _conclusion_changed(meta_before, meta_after)

    when = datetime.now().isoformat(timespec="seconds")
    change_report = _build_change_report(
        when=when, comparison=comparison, outcome=outcome, timepoint=timepoint,
        measure=measure, n_new=len(kept_new), duplicates_removed=duplicates_removed,
        duplicates_against_db=duplicates_against_db,
        al_available=al_available, al_note=al_note,
        al_prioritized=al_prioritized, meta_before=meta_before, meta_after=meta_after,
        conclusion_changed=conclusion_changed, n_new_effects=len(prepared_effects),
    )

    def _article_dict(art: Article) -> dict:
        d = art.to_dict()
        try:
            d["raw"] = json.loads(d.get("raw_json") or "{}")
        except (ValueError, TypeError):
            d["raw"] = {}
        d.pop("raw_json", None)
        return d

    return IncrementalResult(
        new_articles=[_article_dict(a) for a in kept_new],
        duplicates_removed=duplicates_removed,
        al_prioritized=al_prioritized,
        meta_before=meta_before,
        meta_after=meta_after,
        conclusion_changed=conclusion_changed,
        change_report=change_report,
        al_available=al_available,
        al_note=al_note,
    )


def _run_meta(
    db_path: str,
    comparison: str, outcome: str, timepoint: str, measure: str,
    *, model: str, ci_method: str,
) -> dict:
    """跑一次合并；任何 ValueError（层内无研究/研究数不足/指标不兼容等）
    都转成「不可得」占位——增量管线的职责是把原因讲清楚，而不是中断导入。
    """
    try:
        return _meta_summary(
            synthesize(db_path, comparison, outcome, timepoint, measure,
                       model=model, ci_method=ci_method))
    except ValueError as exc:
        return _meta_unavailable(str(exc))


# 便利重导出（调用方拼保存检索式的 PubMed 可点击 URL 时使用；仅拼字符串不联网）
def pubmed_search_url(query_string: str) -> str:  # pragma: no cover - 薄委托
    from coscreen.protocol_designer import generate_pubmed_url

    return generate_pubmed_url(query_string)
