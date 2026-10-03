"""PRISMA 2020 流程图填写页的纯逻辑层。

供 PathB 前端通过 API 调用，并在 pytest 中独立单测：

1. 表单数据结构（``PrismaFormData`` 及来源行/理由行），字段名与
   FORM_CONTRACT 逐字一致；
2. 数字勾稽校验引擎 ``validate`` —— 按 R1→R13 顺序执行，**全部**违规一次性
   返回（不因首错中断），错误消息中英双语并列规则号
   （如 ``R2: Σ识别(120) − 筛前移除(30) ≠ 进入筛选的记录(90) / ...``）；
   另有向导分组接口 ``validate_step(form, step)``（单步字段级 error +
   全表计数）与 ``expected_auto`` / ``apply_autoderive``（下游默认值自动
   带出），供前端向导做常驻校验；
3. 确定性导出：``prisma_form_dot``（官方结构流程图 DOT，中文标签，玉青配色
   与 ``form_to_dict`` / ``form_json``（键名与契约字段一致）。

依据与口径（规则号 = PRISMA_REFERENCE §8 总表）：
- R1–R5、R7：[E&E 序列] + [模板结构]（官方明文/官方模板箭头蕴含）；
- R6/R9/R12：（推断）或图注明文，按手册标注落地；
- R10：模板脚注 **（自动化工具拆分）；
- R11：[DTA清单] item 17（"included in meta-analysis, if applicable"）；
- R13（2026-09-21 契约演进）：初筛排除理由拆分 Σ理由 = 初筛排除数。官方 S2 侧框
  （`Records excluded**`）为单一总数、**不拆理由**（PRISMA_REFERENCE §7 S2 行），
  逐理由拆分属**本项目推断口径**（工程扩展），消息中已注明；S3 全文理由仍走 R8。
- DTA 模式 = 新综述全部字段 + 可选 meta-analysis 计数（官方无 DTA 专属流程图，
  该计数**不渲染为图框**，仅出现在 JSON 与页面提示中——PRISMA_REFERENCE §6/§9）。
- 更新综述的末框一致性（studies_included == total_studies 等）为（推断）工程口径：
  页面在 updated 模式下自动派生这两个字段，API 直填时由 R7 兜底校验。

灰框规则（R12，官方图注明文）：`Records removed before screening` 三行全 0 时、
`Reports not retrieved` 为 0 时，DOT **自动省略**对应侧框；validate 同步给出提示。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace

__all__ = [
    "ATTRIBUTION",
    "AUTO_CHAIN",
    "DB_SOURCE_TYPES",
    "DTA_REASON_VOCABULARY",
    "MODE_LABELS",
    "OTHER_SOURCE_TYPES",
    "REVIEW_MODES",
    "SCOPE_LABELS",
    "SOURCE_TYPE_LABELS",
    "SOURCES_SCOPES",
    "STEP_FIELDS",
    "IdentifiedRow",
    "Issue",
    "PrismaFormData",
    "ReasonRow",
    "StepValidation",
    "ValidationResult",
    "active_identified_rows",
    "active_reason_rows",
    "apply_autoderive",
    "expected_auto",
    "expected_included_reports",
    "form_json",
    "form_to_dict",
    "prisma_form_dot",
    "sum_identified",
    "sum_reasons",
    "validate",
    "validate_step",
]

#: 综述模式（FORM_CONTRACT review_mode 枚举）
REVIEW_MODES = ("new", "updated", "dta")

#: 检索范围（FORM_CONTRACT sources_scope 枚举）：v1 仅库 / v2 含其他方法
SOURCES_SCOPES = ("db_reg", "db_reg_other")

#: 数据库列来源行类型枚举（`Databases` / `Registers`）
DB_SOURCE_TYPES = ("database", "register")

#: 其他方法列来源行类型枚举（`Websites` / `Organisations` / `Citation searching` / `etc.`）
OTHER_SOURCE_TYPES = ("website", "organisation", "citation_searching", "other")

#: 来源类型 -> 中文标签（DOT 与页面共用；参考译文，非官方译名）
SOURCE_TYPE_LABELS = {
    "database": "数据库",
    "register": "注册库",
    "website": "网站",
    "organisation": "组织",
    "citation_searching": "引文检索",
    "other": "其他",
}

#: 综述模式 -> 中文标签（页面下拉用）
MODE_LABELS = {"new": "新综述", "updated": "更新综述", "dta": "诊断准确性综述（DTA）"}

#: 检索范围 -> 中文标签（页面下拉用）
SCOPE_LABELS = {
    "db_reg": "v1：仅数据库 + 注册库",
    "db_reg_other": "v2：数据库 + 注册库 + 其他方法",
}

#: （推断）DTA 排除理由建议词表（PRISMA_REFERENCE §6.4，来自 DTA 清单 item 6/20
#: 的资格域，非官方流程图内容，仅作输入建议）
DTA_REASON_VOCABULARY = [
    "人群/疾病谱不符",
    "未采用目标指数试验",
    "参考标准不符",
    "目标疾病定义不符",
    "研究设计不符",
    "无法提取 2×2 数据（TP/FP/FN/TN）",
]

#: 模板页脚署名（CC BY 4.0 要求，PRISMA_REFERENCE §0.1/§5 导出建议）
ATTRIBUTION = "Source: Page MJ, et al. BMJ 2021;372:n71. doi: 10.1136/bmj.n71."


# ---------------------------------------------------------------------------
# 数据结构（字段名 = FORM_CONTRACT 键名）
# ---------------------------------------------------------------------------


@dataclass
class IdentifiedRow:
    """识别来源行：{source_type, name, n}。"""

    source_type: str
    name: str
    n: int


@dataclass
class ReasonRow:
    """全文排除理由行：{reason, n}。"""

    reason: str
    n: int


@dataclass
class PrismaFormData:
    """PRISMA 流程图全部填写值（字段名与 FORM_CONTRACT 一致）。

    可选计数（``human_excluded_n`` / ``automation_excluded_n`` /
    ``studies_included_meta_analysis``）未启用时为 ``None``；
    其余计数均非负整数，未填写按 0 处理。
    """

    review_mode: str = "new"
    sources_scope: str = "db_reg"
    # 通用：数据库/注册库列
    identified_rows_db: list[IdentifiedRow] = field(default_factory=list)
    duplicates_removed: int = 0
    automation_ineligible: int = 0
    removed_other: int = 0
    removed_other_note: str = ""
    records_screened: int = 0
    records_excluded_screening: int = 0
    # 初筛（题目/摘要）排除理由行：官方 S2 侧框为单一总数，逐理由拆分为本项目
    # 推断口径（R13 校验 Σ = records_excluded_screening；旧导出 JSON 无此键仍可加载）
    screening_exclusion_reasons: list[ReasonRow] = field(default_factory=list)
    human_excluded_n: int | None = None
    automation_excluded_n: int | None = None
    reports_sought_db: int = 0
    reports_not_retrieved_db: int = 0
    reports_assessed_db: int = 0
    exclusion_reasons_db: list[ReasonRow] = field(default_factory=list)
    studies_included: int = 0
    reports_of_included_studies: int = 0
    # 仅 sources_scope = "db_reg_other"
    identified_rows_other: list[IdentifiedRow] = field(default_factory=list)
    reports_sought_other: int = 0
    reports_not_retrieved_other: int = 0
    reports_assessed_other: int = 0
    exclusion_reasons_other: list[ReasonRow] = field(default_factory=list)
    # 仅 review_mode = "updated"
    previous_studies: int = 0
    previous_reports: int = 0
    new_studies: int = 0
    new_reports: int = 0
    total_studies: int = 0
    total_reports: int = 0
    # 仅 review_mode = "dta"
    studies_included_meta_analysis: int | None = None


@dataclass(frozen=True)
class Issue:
    """一条校验结果：规则号 + 中英双语文案。

    向导字段定位（ui/7 常驻校验用，validate() 逐步注；全局性 issue 为空串）：
    - ``field``：该违规归属的契约字段名（如 ``records_screened``）；
    - ``expected`` / ``actual``：等式规则的「应为 / 当前」数值
      （区间/行级规则无单一期望值时为 ``None``，页面回退展示整句消息）。
    """

    rule: str
    zh: str
    en: str
    field: str = ""
    expected: int | None = None
    actual: int | None = None

    @property
    def text(self) -> str:
        """展示文案：`R2: 中文 / English`（中英并列规则号）。"""
        return f"{self.rule}: {self.zh} / {self.en}"


@dataclass
class ValidationResult:
    """validate 的输出：errors 阻止渲染导出；warnings/hints 只提示。"""

    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    hints: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """无 error 即通过（warnings/hints 不阻断）。"""
        return not self.errors


@dataclass(frozen=True)
class StepValidation:
    """validate_step 的输出：向导单步的分字段 error + 全表 error 总数。

    - ``issues``：该步字段相关的全部 error（气泡/字段红字逐条对应）；
    - ``n_global``：整表 error 总数（顶部「待对齐 N 处」徽标用，同输入同输出）。
    """

    step: str
    issues: list[Issue] = field(default_factory=list)
    n_global: int = 0

    @property
    def ok(self) -> bool:
        return not self.issues


# ---------------------------------------------------------------------------
# 求和与行过滤（占位行不计入：名称与计数均为空/0 的行视为未启用）
# ---------------------------------------------------------------------------


def active_identified_rows(rows: list[IdentifiedRow]) -> list[IdentifiedRow]:
    """过滤占位来源行：name 去空白后为空且 n == 0 的行不参与求和/校验/导出。"""
    return [r for r in rows if (r.name or "").strip() or r.n != 0]


def active_reason_rows(rows: list[ReasonRow]) -> list[ReasonRow]:
    """过滤占位理由行：reason 去空白后为空且 n == 0 的行不参与求和/校验/导出。"""
    return [r for r in rows if (r.reason or "").strip() or r.n != 0]


def sum_identified(rows: list[IdentifiedRow]) -> int:
    """来源行 n 之和（占位行已排除）。"""
    return sum(r.n for r in active_identified_rows(rows))


def sum_reasons(rows: list[ReasonRow]) -> int:
    """理由行 n 之和（占位行已排除）。"""
    return sum(r.n for r in active_reason_rows(rows))


def expected_included_reports(form: PrismaFormData) -> int:
    """按 R5/R5' 推导的「应纳入报告数」= 各列（资格评估 − Σ理由）之和。

    updated 模式下该值应对 ``new_reports``（新纳入研究的报告），
    new/dta 模式下对 ``reports_of_included_studies``（纳入研究的报告）。
    """
    remainder_db = form.reports_assessed_db - sum_reasons(form.exclusion_reasons_db)
    if form.sources_scope == "db_reg_other":
        remainder_other = (
            form.reports_assessed_other - sum_reasons(form.exclusion_reasons_other)
        )
        return remainder_db + remainder_other
    return remainder_db


# ---------------------------------------------------------------------------
# 向导自动推导（ui/7 自动带出下游默认值；纯函数，Streamlit 无关）
# ---------------------------------------------------------------------------

#: 自动推导链（依赖序）：expected_auto 沿此顺序级联计算——上游刚推导出的值
#: 立即参与下游期望值。updated 专属字段在 new/dta 模式下返回 None（跳过）。
AUTO_CHAIN: tuple[str, ...] = (
    "records_screened",
    "reports_sought_db",
    "reports_assessed_db",
    "new_studies",
    "new_reports",
    "total_studies",
    "total_reports",
    "reports_of_included_studies",
    "studies_included",
)

#: 向导步骤 -> 契约字段分组（validate_step 按步过滤 error；同一字段可归属
#: 多步，如 records_screened 既是 S1 的「去重后数」又是 S2 的「进入初筛数」）
STEP_FIELDS: dict[str, tuple[str, ...]] = {
    "s1": (
        "identified_rows_db",
        "duplicates_removed",
        "automation_ineligible",
        "removed_other",
        "records_screened",
    ),
    "s2": (
        "records_screened",
        "records_excluded_screening",
        "screening_exclusion_reasons",
        "reports_sought_db",
    ),
    "s3": (
        "reports_sought_db",
        "reports_not_retrieved_db",
        "reports_assessed_db",
        "exclusion_reasons_db",
    ),
    "s4": (
        "studies_included",
        "reports_of_included_studies",
        "previous_studies",
        "previous_reports",
        "new_studies",
        "new_reports",
        "total_studies",
        "total_reports",
        "studies_included_meta_analysis",
    ),
}


def expected_auto(form: PrismaFormData, field: str) -> int | None:
    """按官方勾稽规则推导某字段的「应为」值（自动带出）；非自动字段返回 None。

    - records_screened ← Σ识别 − 筛前移除（R2）
    - reports_sought_db ← 进入筛选 − 题摘排除（R3）
    - reports_assessed_db ← 寻求获取 − 未获取（R4）
    - new/dta：reports_of_included_studies = studies_included ← 评估 − Σ理由（R5）
    - updated：new_studies = new_reports ← 评估 − Σ理由（R5，向导不给新纳入输入框，
      两行同源自动带出）；total_studies/total_reports ← previous + new（R7）；
      studies_included/reports_of_included_studies ← Total（末框一致性口径）。

    纯数学口径：不做非负截断（负期望交由 R9/R2 等规则暴露）。
    """
    is_updated = form.review_mode == "updated"
    if field == "records_screened":
        removed = form.duplicates_removed + form.automation_ineligible + form.removed_other
        return sum_identified(form.identified_rows_db) - removed
    if field == "reports_sought_db":
        return form.records_screened - form.records_excluded_screening
    if field == "reports_assessed_db":
        return form.reports_sought_db - form.reports_not_retrieved_db
    if field == "new_studies":
        return expected_included_reports(form) if is_updated else None
    if field == "new_reports":
        return expected_included_reports(form) if is_updated else None
    if field == "total_studies":
        return form.previous_studies + form.new_studies if is_updated else None
    if field == "total_reports":
        return form.previous_reports + form.new_reports if is_updated else None
    if field == "reports_of_included_studies":
        return form.total_reports if is_updated else expected_included_reports(form)
    if field == "studies_included":
        return form.total_studies if is_updated else expected_included_reports(form)
    return None


def apply_autoderive(form: PrismaFormData, overrides: frozenset[str] | set[str]) -> PrismaFormData:
    """返回自动推导后的表单副本：非 override 字段沿 AUTO_CHAIN 重算。

    ``overrides`` 内的字段保留用户值（若违反规则由 validate 暴露为 error，
    页面据此弹「应为/当前」气泡）。不修改入参；列表字段与原表单共享引用
    （本函数只写整数字段）。
    """
    out = replace(form)
    for field_name in AUTO_CHAIN:
        if field_name in overrides:
            continue
        value = expected_auto(out, field_name)
        if value is not None:
            setattr(out, field_name, int(value))
    return out


#: 参与 R9 非负校验的整数字段（None 项跳过）
_INT_FIELDS = (
    "duplicates_removed",
    "automation_ineligible",
    "removed_other",
    "records_screened",
    "records_excluded_screening",
    "reports_sought_db",
    "reports_not_retrieved_db",
    "reports_assessed_db",
    "studies_included",
    "reports_of_included_studies",
    "reports_sought_other",
    "reports_not_retrieved_other",
    "reports_assessed_other",
    "previous_studies",
    "previous_reports",
    "new_studies",
    "new_reports",
    "total_studies",
    "total_reports",
    "human_excluded_n",
    "automation_excluded_n",
    "studies_included_meta_analysis",
)


# ---------------------------------------------------------------------------
# 校验引擎（R1→R12，全部违规一次性返回）
# ---------------------------------------------------------------------------


def validate(form: PrismaFormData) -> ValidationResult:
    """执行 R1→R12 勾稽校验，返回全部 error / warning / hint（不因首错中断）。

    error 阻止导出与流程图渲染；warning（R6 提示、R10 缺拆分建议）与
    hint（R12 灰框建议）只提示。同输入同输出（确定性）。
    除规则号与双语文案外，每条 error 逐步注 ``field``（契约字段定位）与
    ``expected`` / ``actual``（等式规则的应为/当前值），供向导按字段弹泡。
    """
    errors: list[Issue] = []
    warnings: list[Issue] = []
    hints: list[Issue] = []

    def err(
        rule: str,
        zh: str,
        en: str,
        field: str = "",
        expected: int | None = None,
        actual: int | None = None,
    ) -> None:
        errors.append(Issue(rule, zh, en, field=field, expected=expected, actual=actual))

    # ---- FORM：枚举合法性（页面不会产生，直连 API 的兜底）----
    if form.review_mode not in REVIEW_MODES:
        err(
            "FORM",
            f"综述类型 review_mode={form.review_mode!r} 不合法（应为 {' / '.join(REVIEW_MODES)}）",
            f"invalid review_mode={form.review_mode!r} (expected {' / '.join(REVIEW_MODES)})",
        )
    if form.sources_scope not in SOURCES_SCOPES:
        err(
            "FORM",
            f"检索范围 sources_scope={form.sources_scope!r} 不合法（应为 {' / '.join(SOURCES_SCOPES)}）",
            f"invalid sources_scope={form.sources_scope!r} (expected {' / '.join(SOURCES_SCOPES)})",
        )

    scope_other = form.sources_scope == "db_reg_other"
    is_updated = form.review_mode == "updated"
    is_dta = form.review_mode == "dta"

    rows_db = active_identified_rows(form.identified_rows_db)
    rows_other = (
        active_identified_rows(form.identified_rows_other) if scope_other else []
    )
    sum_db = sum(r.n for r in rows_db)
    sum_other = sum(r.n for r in rows_other)
    removed_total = form.duplicates_removed + form.automation_ineligible + form.removed_other

    # ---- R1：来源行（≥1 行；类型合法；名称非空；n ≥ 0）----
    _check_identified_rows(err, "数据库列", rows_db, DB_SOURCE_TYPES, field="identified_rows_db")
    if scope_other:
        _check_identified_rows(
            err, "其他方法列", rows_other, OTHER_SOURCE_TYPES, field="identified_rows_other"
        )
    if not rows_db:
        err(
            "R1",
            "数据库列至少需要 1 行识别来源（数据库/注册库，脚注 * 鼓励逐库报告）",
            "db column needs >=1 identification source row (footnote * encourages per-database reporting)",
            field="identified_rows_db",
        )
    if scope_other and not rows_other:
        err(
            "R1",
            "其他方法列至少需要 1 行识别来源（网站/组织/引文检索/其他）",
            "other-methods column needs >=1 identification source row (websites/organisations/citation searching)",
            field="identified_rows_other",
        )
    if sum_db == 0 and sum_other == 0:
        warnings.append(Issue(
            "R1",
            "尚未填写任何识别来源数字（合计 0）——请从「识别的记录来自*」开始填写",
            "no identification counts entered yet (total 0) — start from 'Records identified from*'",
        ))

    # ---- R2：Σ识别 − 筛前移除 = 进入筛选的记录 ----
    if form.records_screened != sum_db - removed_total:
        err(
            "R2",
            f"数据库列：Σ识别({sum_db}) − 筛前移除({removed_total}) ≠ 进入筛选的记录({form.records_screened})",
            f"db column: identified({sum_db}) − removed({removed_total}) ≠ records screened({form.records_screened})",
            field="records_screened",
            expected=sum_db - removed_total,
            actual=form.records_screened,
        )

    # ---- R3：进入筛选的记录 − 排除的记录 = 寻求获取的报告 ----
    if form.reports_sought_db != form.records_screened - form.records_excluded_screening:
        err(
            "R3",
            f"数据库列：进入筛选的记录({form.records_screened}) − 排除的记录({form.records_excluded_screening})"
            f" ≠ 寻求获取的报告({form.reports_sought_db})",
            f"records screened({form.records_screened}) − records excluded({form.records_excluded_screening})"
            f" ≠ reports sought({form.reports_sought_db})",
            field="reports_sought_db",
            expected=form.records_screened - form.records_excluded_screening,
            actual=form.reports_sought_db,
        )

    # ---- R4：寻求获取的报告 − 未获取到的报告 = 进行资格评估的报告 ----
    if form.reports_assessed_db != form.reports_sought_db - form.reports_not_retrieved_db:
        err(
            "R4",
            f"数据库列：寻求获取的报告({form.reports_sought_db}) − 未获取到的报告({form.reports_not_retrieved_db})"
            f" ≠ 进行资格评估的报告({form.reports_assessed_db})",
            f"reports sought({form.reports_sought_db}) − not retrieved({form.reports_not_retrieved_db})"
            f" ≠ assessed({form.reports_assessed_db})",
            field="reports_assessed_db",
            expected=form.reports_sought_db - form.reports_not_retrieved_db,
            actual=form.reports_assessed_db,
        )

    # ---- R5：资格评估 − Σ理由 = 纳入报告数（报告层；studies 不做等式）----
    reasons_db = active_reason_rows(form.exclusion_reasons_db)
    reasons_other = active_reason_rows(form.exclusion_reasons_other) if scope_other else []
    sum_rs = sum_reasons(form.exclusion_reasons_db) + (
        sum_reasons(form.exclusion_reasons_other) if scope_other else 0
    )
    expected = expected_included_reports(form)
    assessed_total = form.reports_assessed_db + (
        form.reports_assessed_other if scope_other else 0
    )
    if is_updated:
        target_name = "new_reports"
        target, target_label = form.new_reports, "新纳入研究的报告"
        target_label_en = "reports of new included studies"
    else:
        target_name = "reports_of_included_studies"
        target, target_label = form.reports_of_included_studies, "纳入研究的报告"
        target_label_en = "reports of included studies"
    if target != expected:
        err(
            "R5",
            f"{target_label}({target}) ≠ 资格评估({assessed_total}) − Σ排除理由({sum_rs}) = {expected}",
            f"{target_label_en}({target}) ≠ assessed({assessed_total}) − Σreasons({sum_rs}) = {expected}",
            field=target_name,
            expected=expected,
            actual=target,
        )

    # ---- R6：其他方法列无识别→获取勾稽（提示即可）----
    if scope_other:
        warnings.append(Issue(
            "R6",
            "其他方法列：识别数与「寻求获取的报告」之间无官方勾稽（该列无筛选/移除框），数字自担",
            "other-methods column: no official reconciliation between identified and sought counts (no screening/removal boxes in that column)",
        ))

    # ---- R7：更新综述 total = previous + new ----
    if is_updated:
        if form.total_studies != form.previous_studies + form.new_studies:
            err(
                "R7",
                f"综述纳入研究总数({form.total_studies}) ≠ 上一版纳入({form.previous_studies}) + 新纳入({form.new_studies})",
                f"total studies({form.total_studies}) ≠ previous({form.previous_studies}) + new({form.new_studies})",
                field="total_studies",
                expected=form.previous_studies + form.new_studies,
                actual=form.total_studies,
            )
        if form.total_reports != form.previous_reports + form.new_reports:
            err(
                "R7",
                f"纳入研究报告总数({form.total_reports}) ≠ 上一版报告({form.previous_reports}) + 新纳入报告({form.new_reports})",
                f"total reports({form.total_reports}) ≠ previous({form.previous_reports}) + new({form.new_reports})",
                field="total_reports",
                expected=form.previous_reports + form.new_reports,
                actual=form.total_reports,
            )
        # （推断）工程口径：末框两行与 Total 框一致（页面在 updated 模式自动派生）
        if form.studies_included != form.total_studies:
            err(
                "R7",
                f"纳入综述的研究({form.studies_included}) 应与综述纳入研究总数({form.total_studies}) 一致（更新综述末框 = Total）",
                f"studies included({form.studies_included}) should equal total studies({form.total_studies}) in an updated review",
                field="studies_included",
                expected=form.total_studies,
                actual=form.studies_included,
            )
        if form.reports_of_included_studies != form.total_reports:
            err(
                "R7",
                f"纳入研究的报告({form.reports_of_included_studies}) 应与纳入研究报告总数({form.total_reports}) 一致（更新综述末框 = Total）",
                f"reports of included studies({form.reports_of_included_studies}) should equal"
                f" total reports({form.total_reports}) in an updated review",
                field="reports_of_included_studies",
                expected=form.total_reports,
                actual=form.reports_of_included_studies,
            )

    # ---- R8：理由行（非空/不重复/n ≥ 1；有排除时至少 1 行）----
    _check_reason_rows(err, "数据库列", reasons_db, field="exclusion_reasons_db")
    if scope_other:
        _check_reason_rows(err, "其他方法列", reasons_other, field="exclusion_reasons_other")
    n_excluded_total = assessed_total - target
    if n_excluded_total > 0 and sum_rs == 0:
        err(
            "R8",
            f"资格评估共排除 {n_excluded_total} 条报告但未填写任何排除理由（至少 1 行）",
            f"{n_excluded_total} report(s) excluded at eligibility but no exclusion reason row given (need >=1)",
            field="exclusion_reasons_db",
        )

    # ---- R9：非负整数；移除三行各自 ≤ Σ识别 ----
    for name in _INT_FIELDS:
        value = getattr(form, name)
        if value is not None and value < 0:
            err(
                "R9",
                f"字段 {name} 为负数（{value}）：所有计数需为 ≥ 0 的整数",
                f"field {name} is negative ({value}): all counts must be integers >= 0",
                field=name,
                expected=0,
                actual=value,
            )
    for row_field, row_label, value in (
        ("duplicates_removed", "重复记录移除", form.duplicates_removed),
        ("automation_ineligible", "自动化工具标记不合格", form.automation_ineligible),
        ("removed_other", "其他原因移除", form.removed_other),
    ):
        if value > sum_db:
            err(
                "R9",
                f"筛前移除行「{row_label}」({value}) 超过数据库列 Σ识别({sum_db})",
                f"pre-screening removal row '{row_label}' ({value}) exceeds db identified total ({sum_db})",
                field=row_field,
            )

    # ---- R10：脚注** 自动化拆分（人工 + 自动化 = 排除总数）----
    human, auto = form.human_excluded_n, form.automation_excluded_n
    if (human is None) != (auto is None):
        err(
            "R10",
            "脚注**拆分：人工与自动化两个计数需同时填写或同时留空",
            "footnote** split: human and automation counts must be both filled or both empty",
        )
    elif human is not None and auto is not None and human + auto != form.records_excluded_screening:
        err(
            "R10",
            f"脚注**拆分：人工({human}) + 自动化({auto}) ≠ 排除的记录({form.records_excluded_screening})",
            f"footnote** split: human({human}) + automation({auto}) ≠ records excluded({form.records_excluded_screening})",
        )
    if human is None and auto is None and form.automation_ineligible > 0:
        warnings.append(Issue(
            "R10",
            f"使用了自动化工具（筛前移除 {form.automation_ineligible} 条）但未提供脚注**拆分——建议补填人工/自动化各排除多少",
            f"automation tools used ({form.automation_ineligible} records removed pre-screening) but no footnote**"
            f" split given — consider reporting human vs automation exclusions",
        ))

    # ---- R11：DTA meta-analysis 计数 ≤ 纳入研究数 ----
    if is_dta and form.studies_included_meta_analysis is not None:
        meta = form.studies_included_meta_analysis
        if not 0 <= meta <= form.studies_included:
            err(
                "R11",
                f"进入 meta-analysis 的研究数({meta}) 需在 0 ≤ n ≤ 纳入综述的研究({form.studies_included}) 范围内",
                f"studies in meta-analysis({meta}) must satisfy 0 <= n <= studies included({form.studies_included})",
                field="studies_included_meta_analysis",
                actual=meta,
            )

    # ---- R12：灰框条件提示（非数据校验；DOT 已按此自动隐藏对应框）----
    if form.duplicates_removed == 0 and form.automation_ineligible == 0 and form.removed_other == 0:
        hints.append(Issue(
            "R12",
            "筛前移除三行均为 0：按官方灰框规则已从图中隐藏「筛前移除的记录」框",
            "all pre-screening removals are 0: 'Records removed before screening' box hidden per official grey-box rule",
        ))
    if form.reports_not_retrieved_db == 0:
        hints.append(Issue(
            "R12",
            "数据库列「未获取到的报告」为 0：已从图中隐藏该框",
            "db column 'Reports not retrieved' is 0: box hidden from the diagram",
        ))
    if scope_other and form.reports_not_retrieved_other == 0:
        hints.append(Issue(
            "R12",
            "其他方法列「未获取到的报告」为 0：已从图中隐藏该框",
            "other-methods 'Reports not retrieved' is 0: box hidden from the diagram",
        ))

    # ---- R13：初筛排除理由拆分 Σ理由 = 初筛排除数（推断口径，见模块 docstring）----
    # 官方 S2 侧框（Records excluded**）只报单一总数；逐理由拆分为本项目扩展，
    # 消息中显式注明「推断」，不得声称为官方规定（PRISMA_REFERENCE §0.3/§7）。
    scr_reasons = active_reason_rows(form.screening_exclusion_reasons)
    sum_scr = sum_reasons(form.screening_exclusion_reasons)
    if sum_scr != form.records_excluded_screening:
        err(
            "R13",
            f"初筛排除理由合计({sum_scr}) ≠ 初筛排除数({form.records_excluded_screening})"
            "（官方侧框为单一总数，逐理由拆分为本项目推断口径）",
            f"first-screening exclusion reasons total({sum_scr}) ≠ records excluded"
            f"({form.records_excluded_screening})"
            " (official box reports a single total; the itemized split is a project-side convention)",
            field="screening_exclusion_reasons",
            expected=form.records_excluded_screening,
            actual=sum_scr,
        )
    _check_reason_rows(
        err, "初筛", scr_reasons, field="screening_exclusion_reasons", rule="R13"
    )

    return ValidationResult(errors=errors, warnings=warnings, hints=hints)


def validate_step(form: PrismaFormData, step: str) -> StepValidation:
    """向导单步校验：只保留归属该步字段的 error，并附带全表 error 总数。

    ``step`` 取 ``STEP_FIELDS`` 的键（"s1"–"s4"，对应检索识别/初筛/全文获取
    与评估/纳入）；未知步骤抛 ``ValueError``。全局性 error（FORM 枚举等，
    ``field == ""``）不计入任何单步，仅计入 ``n_global``。同输入同输出。
    """
    if step not in STEP_FIELDS:
        raise ValueError(f"unknown wizard step: {step!r} (expected {sorted(STEP_FIELDS)})")
    result = validate(form)
    fields = STEP_FIELDS[step]
    issues = [issue for issue in result.errors if issue.field in fields]
    return StepValidation(step=step, issues=issues, n_global=len(result.errors))


def _check_identified_rows(
    err, col_label: str, rows: list[IdentifiedRow], allowed, field: str = ""
) -> None:
    """R1 行级校验：类型枚举、名称非空、n ≥ 0（占位行已被过滤）。"""
    for i, row in enumerate(rows, 1):
        if row.source_type not in allowed:
            err(
                "R1",
                f"{col_label}第 {i} 行来源类型 {row.source_type!r} 不合法（应为 {' / '.join(allowed)}）",
                f"{col_label} row {i}: invalid source_type {row.source_type!r} (expected {' / '.join(allowed)})",
                field=field,
            )
        if not (row.name or "").strip():
            err(
                "R1",
                f"{col_label}第 {i} 行来源名称不能为空",
                f"{col_label} row {i}: source name must not be empty",
                field=field,
            )
        if row.n < 0:
            err(
                "R1",
                f"{col_label}第 {i} 行 n 为负数（{row.n}）：需 ≥ 0",
                f"{col_label} row {i}: n is negative ({row.n}); must be >= 0",
                field=field,
            )


def _check_reason_rows(
    err, col_label: str, rows: list[ReasonRow], field: str = "", rule: str = "R8"
) -> None:
    """R8（全文）/R13（初筛）行级校验：理由非空、不重复、n ≥ 1（占位行已被过滤）。"""
    seen: set[str] = set()
    for i, row in enumerate(rows, 1):
        reason = (row.reason or "").strip()
        if not reason:
            err(
                rule,
                f"{col_label}排除理由第 {i} 行：理由不能为空",
                f"{col_label} exclusion reason row {i}: reason must not be empty",
                field=field,
            )
        if reason in seen:
            err(
                rule,
                f"{col_label}排除理由重复：{reason}",
                f"{col_label} duplicate exclusion reason: {reason}",
                field=field,
            )
        seen.add(reason)
        if row.n < 1:
            err(
                rule,
                f"{col_label}排除理由第 {i} 行「{reason or '（空）'}」n 需 ≥ 1（当前 {row.n}）",
                f"{col_label} exclusion reason row {i} '{reason or '(empty)'}': n must be >= 1 (got {row.n})",
                field=field,
            )


# ---------------------------------------------------------------------------
# 确定性导出：数字 JSON（键名 = 契约字段）
# ---------------------------------------------------------------------------


def form_to_dict(form: PrismaFormData) -> dict:
    """导出全部填写值（键名与 FORM_CONTRACT 字段一致，键序固定）。

    确定性口径（契约第 4 条）：来源行按录入顺序、理由行按频次降序 +
    同频按名称序；占位行（名称/理由与计数均为空/0）不导出；
    文本字段输出 trim 后的值。同一输入多次调用结果完全一致。
    """
    return {
        "review_mode": form.review_mode,
        "sources_scope": form.sources_scope,
        "identified_rows_db": [
            {
                "source_type": r.source_type,
                "name": (r.name or "").strip(),
                "n": int(r.n),
            }
            for r in active_identified_rows(form.identified_rows_db)
        ],
        "duplicates_removed": int(form.duplicates_removed),
        "automation_ineligible": int(form.automation_ineligible),
        "removed_other": int(form.removed_other),
        "removed_other_note": (form.removed_other_note or "").strip()[:200],
        "records_screened": int(form.records_screened),
        "records_excluded_screening": int(form.records_excluded_screening),
        "screening_exclusion_reasons": _reason_rows_json(form.screening_exclusion_reasons),
        "human_excluded_n": None if form.human_excluded_n is None else int(form.human_excluded_n),
        "automation_excluded_n": None if form.automation_excluded_n is None else int(form.automation_excluded_n),
        "reports_sought_db": int(form.reports_sought_db),
        "reports_not_retrieved_db": int(form.reports_not_retrieved_db),
        "reports_assessed_db": int(form.reports_assessed_db),
        "exclusion_reasons_db": _reason_rows_json(form.exclusion_reasons_db),
        "studies_included": int(form.studies_included),
        "reports_of_included_studies": int(form.reports_of_included_studies),
        "identified_rows_other": [
            {
                "source_type": r.source_type,
                "name": (r.name or "").strip(),
                "n": int(r.n),
            }
            for r in active_identified_rows(form.identified_rows_other)
        ],
        "reports_sought_other": int(form.reports_sought_other),
        "reports_not_retrieved_other": int(form.reports_not_retrieved_other),
        "reports_assessed_other": int(form.reports_assessed_other),
        "exclusion_reasons_other": _reason_rows_json(form.exclusion_reasons_other),
        "previous_studies": int(form.previous_studies),
        "previous_reports": int(form.previous_reports),
        "new_studies": int(form.new_studies),
        "new_reports": int(form.new_reports),
        "total_studies": int(form.total_studies),
        "total_reports": int(form.total_reports),
        "studies_included_meta_analysis": (
            None if form.studies_included_meta_analysis is None
            else int(form.studies_included_meta_analysis)
        ),
    }


def _reason_rows_json(rows: list[ReasonRow]) -> list[dict]:
    """理由行导出：频次降序 + 同频按名称序（确定性排序）。"""
    active = [
        {"reason": (r.reason or "").strip(), "n": int(r.n)}
        for r in active_reason_rows(rows)
    ]
    return sorted(active, key=lambda d: (-d["n"], d["reason"]))


def form_json(form: PrismaFormData) -> str:
    """数字 JSON 字符串（utf-8、缩进 2、中文不转义；确定性）。"""
    return json.dumps(form_to_dict(form), ensure_ascii=False, indent=2) + "\n"


# ---------------------------------------------------------------------------
# 官方结构流程图 DOT（中文标签，玉青配色沿用 ui/_theme）
# ---------------------------------------------------------------------------

# 配色沿用 ui/_theme.py：JADE / JADE_DARK / TERRACOTTA（玉青主链 + 陶红排除侧框）
_JADE = "#2F6F6A"
_JADE_DARK = "#234F4B"
_TERRA = "#B0563F"
_MAIN_FILL = "#E3F2F0"      # 主链框底（浅玉青 primary-100）
_SIDE_FILL = "#F9EFEA"      # 排除侧框底（陶红浅底）
_GREY_FILL = "#EAEDF1"      # 灰框（条件填写）底（次色 50）
_GREY_LINE = "#85909D"


def _esc(text: str) -> str:
    """DOT 标签转义：反斜杠与双引号（换行由调用方以 \\l 追加）。"""
    return (text or "").replace("\\", "\\\\").replace('"', '\\"')


def _lines_label(lines: list[str]) -> str:
    """多行左对齐标签：每行结尾 \\l（graphviz 左对齐换行）。"""
    return "".join(_esc(line) + "\\l" for line in lines)


def _simple_label(line: str) -> str:
    """单行标签（居中）。"""
    return _esc(line)


def prisma_form_dot(form: PrismaFormData, merge_note: str = "") -> str:
    """生成官方结构的 PRISMA 2020 流程图 DOT（中文标签，含计数）。

    结构口径（PRISMA_REFERENCE §2–§6，[模板] 逐字标签的参考译文）：
    - 三条阶段侧栏：识别 / 筛选 / 纳入；
    - 数据库列主链：识别 → 进入筛选 → 寻求获取 → 资格评估 → 末框，
      侧框：筛前移除（灰框）、题摘排除（含本项目推断口径的逐理由拆分行，有行才列）、
      未获取（灰框）、全文排除理由；
    - v2 追加其他方法列（识别 → 寻求获取 → 资格评估，无筛选/移除框）；
    - 更新综述追加「既往研究」列、新纳入框与 Total 末框；
    - DTA 与新综述同构（meta-analysis 计数不渲染为图框，见模块 docstring）；
    - 灰框规则（R12）：三行移除全 0 / 未获取为 0 时自动省略对应侧框；
    - 页脚署名 ATTRIBUTION（CC BY 4.0）；``merge_note`` 非空时（更新综述的
      「一句话合并说明」）追加在署名之后，向导页向导字段用，缺省不改变输出。

    确定性：同输入同输出（纯字符串拼接，节点/边按固定顺序生成）。
    """
    scope_other = form.sources_scope == "db_reg_other"
    is_updated = form.review_mode == "updated"
    rows_db = active_identified_rows(form.identified_rows_db)
    rows_other = active_identified_rows(form.identified_rows_other)
    sum_db = sum(r.n for r in rows_db)
    sum_other = sum(r.n for r in rows_other)
    removed_total = form.duplicates_removed + form.automation_ineligible + form.removed_other

    nodes: list[str] = []
    edges: list[str] = []
    ranks: list[str] = []

    def node(name: str, label: str, **attrs: str) -> None:
        parts = [f'{k}="{v}"' for k, v in attrs.items()]
        nodes.append(f'    {name} [label="{label}"{"" if not parts else ", " + ", ".join(parts)}];')

    def edge(src: str, dst: str, style: str | None = None) -> None:
        attr = f' [style={style}]' if style else ""
        edges.append(f"    {src} -> {dst}{attr};")

    def rank(*names: str) -> None:
        ranks.append("    { rank=same; " + "; ".join(names) + "; }")

    # ---- 阶段侧栏（竖排标签，占位节点；\\n = DOT 居中换行）----
    node("stage_ident", "识别\\nIdentification", **{"shape": "plaintext", "fontcolor": _JADE_DARK})
    node("stage_screen", "筛选\\nScreening", **{"shape": "plaintext", "fontcolor": _JADE_DARK})
    node("stage_included", "纳入\\nIncluded", **{"shape": "plaintext", "fontcolor": _JADE_DARK})

    # ---- 列标题（官方逐字标题的参考译文）----
    new_word = "新" if is_updated else ""
    header_db_zh = f"通过数据库和注册库识别{new_word}研究"
    header_other_zh = f"通过其他方法识别{new_word}研究"
    header_attrs = {"shape": "plaintext", "fontcolor": _JADE_DARK}
    header_rank = ["stage_ident"]
    if is_updated:
        node("header_prev", "既往研究\\nPrevious studies", **header_attrs)
        header_rank.append("header_prev")
    header_en = f"Identification of {'new ' if is_updated else ''}studies"
    node("header_db", f"{header_db_zh}\\n{header_en} via databases and registers", **header_attrs)
    header_rank.append("header_db")
    if scope_other:
        node("header_other", f"{header_other_zh}\\n{header_en} via other methods", **header_attrs)
        header_rank.append("header_other")
    rank(*header_rank)
    # 同层左→右次序（不可见边）
    order_chain = ["stage_ident"] + (["header_prev"] if is_updated else []) + ["header_db"] + (["header_other"] if scope_other else [])
    for a, b in zip(order_chain, order_chain[1:]):
        edge(a, b, style="invis")
    edge("header_db", "ident_db", style="invis")
    if scope_other:
        edge("header_other", "ident_other", style="invis")
    if is_updated:
        edge("header_prev", "prev_box", style="invis")
    edge("stage_ident", "stage_screen", style="invis")
    edge("stage_screen", "stage_included", style="invis")

    # ---- 数据库/注册库列 ----
    ident_lines = ["识别的记录来自*："]
    ident_lines += [
        f"· {(r.name or '').strip()}（{SOURCE_TYPE_LABELS.get(r.source_type, r.source_type)}，n = {r.n}）"
        for r in rows_db
    ]
    ident_lines.append(f"合计 n = {sum_db}")
    node("ident_db", _lines_label(ident_lines))
    node(
        "screened_db",
        _simple_label(f"进入筛选的记录（n = {form.records_screened}）"),
    )
    edge("ident_db", "screened_db")

    removed_rank = ["ident_db"]
    if removed_total > 0:
        removed_lines = ["筛前移除的记录："]
        if form.duplicates_removed > 0:
            removed_lines.append(f"重复记录移除（n = {form.duplicates_removed}）")
        if form.automation_ineligible > 0:
            removed_lines.append(f"被自动化工具标记为不合格的记录（n = {form.automation_ineligible}）")
        if form.removed_other > 0:
            note = (form.removed_other_note or "").strip()
            suffix = f"：{note}" if note else ""
            removed_lines.append(f"其他原因移除（n = {form.removed_other}）{suffix}")
        node(
            "removed_db",
            _lines_label(removed_lines),
            fillcolor=_GREY_FILL,
            color=_GREY_LINE,
            style="rounded,filled,dashed",
        )
        edge("ident_db", "removed_db")
        removed_rank.append("removed_db")
    rank(*removed_rank)

    excl_scr_lines = [f"排除的记录**（n = {form.records_excluded_screening}）"]
    # 初筛排除理由拆分（本项目推断口径，R13）：有行才逐条列出（官方侧框为单一总数）
    scr_reasons = active_reason_rows(form.screening_exclusion_reasons)
    excl_scr_lines += [f"· {(r.reason or '').strip()}（n = {r.n}）" for r in scr_reasons]
    if form.human_excluded_n is not None and form.automation_excluded_n is not None:
        excl_scr_lines.append(
            f"其中：人工 {form.human_excluded_n} · 自动化 {form.automation_excluded_n}"
        )
    node("excl_screen_db", _lines_label(excl_scr_lines), fillcolor=_SIDE_FILL, color=_TERRA)
    edge("screened_db", "excl_screen_db")
    rank("stage_screen", "screened_db", "excl_screen_db")

    node("sought_db", _simple_label(f"寻求获取的报告（n = {form.reports_sought_db}）"))
    edge("screened_db", "sought_db")
    sought_rank = ["sought_db"]
    if form.reports_not_retrieved_db > 0:
        node(
            "notret_db",
            _simple_label(f"未获取到的报告（n = {form.reports_not_retrieved_db}）"),
            fillcolor=_GREY_FILL,
            color=_GREY_LINE,
            style="rounded,filled,dashed",
        )
        edge("sought_db", "notret_db")
        sought_rank.append("notret_db")
    rank(*sought_rank)

    node("assessed_db", _simple_label(f"进行资格评估的报告（n = {form.reports_assessed_db}）"))
    edge("sought_db", "assessed_db")
    reasons_db = active_reason_rows(form.exclusion_reasons_db)
    reason_lines = ["排除的报告："]
    reason_lines += [f"{(r.reason or '').strip()}（n = {r.n}）" for r in reasons_db]
    if len(reason_lines) == 1:
        reason_lines.append("（无排除）")
    node("excl_ft_db", _lines_label(reason_lines), fillcolor=_SIDE_FILL, color=_TERRA)
    edge("assessed_db", "excl_ft_db")
    rank("assessed_db", "excl_ft_db")

    # ---- 其他方法列（仅 v2；无筛选/移除框，官方结构）----
    if scope_other:
        ident_o_lines = ["识别的记录来自："]
        ident_o_lines += [
            f"· {(r.name or '').strip()}（{SOURCE_TYPE_LABELS.get(r.source_type, r.source_type)}，n = {r.n}）"
            for r in rows_other
        ]
        ident_o_lines.append(f"合计 n = {sum_other}")
        node("ident_other", _lines_label(ident_o_lines))
        node("sought_other", _simple_label(f"寻求获取的报告（n = {form.reports_sought_other}）"))
        edge("ident_other", "sought_other")
        if removed_total > 0:
            rank("ident_db", "removed_db", "ident_other")
        else:
            rank("ident_db", "ident_other")
        node("assessed_other", _simple_label(f"进行资格评估的报告（n = {form.reports_assessed_other}）"))
        edge("sought_other", "assessed_other")
        reasons_other = active_reason_rows(form.exclusion_reasons_other)
        reason_o_lines = ["排除的报告："]
        reason_o_lines += [f"{(r.reason or '').strip()}（n = {r.n}）" for r in reasons_other]
        if len(reason_o_lines) == 1:
            reason_o_lines.append("（无排除）")
        node("excl_ft_other", _lines_label(reason_o_lines), fillcolor=_SIDE_FILL, color=_TERRA)
        edge("assessed_other", "excl_ft_other")
        rank("assessed_db", "excl_ft_db", "assessed_other", "excl_ft_other")
        if form.reports_not_retrieved_other > 0:
            node(
                "notret_other",
                _simple_label(f"未获取到的报告（n = {form.reports_not_retrieved_other}）"),
                fillcolor=_GREY_FILL,
                color=_GREY_LINE,
                style="rounded,filled,dashed",
            )
            edge("sought_other", "notret_other")
            rank("sought_db", "notret_other", "sought_other")

    # ---- 末框（Included 段）----
    if is_updated:
        node(
            "new_box",
            _lines_label([
                f"新纳入的研究（n = {form.new_studies}）",
                f"新纳入研究的报告（n = {form.new_reports}）",
            ]),
        )
        edge("assessed_db", "new_box")
        if scope_other:
            edge("assessed_other", "new_box")
        node(
            "prev_box",
            _lines_label([
                f"上一版综述纳入的研究（n = {form.previous_studies}）",
                f"上一版综述纳入研究的报告（n = {form.previous_reports}）",
            ]),
        )
        edge("prev_box", "final_box")
        edge("new_box", "final_box")
        rank("prev_box", "new_box")
        final_lines = [
            f"综述纳入研究总数（n = {form.total_studies}）",
            f"纳入研究报告总数（n = {form.total_reports}）",
        ]
    else:
        edge("assessed_db", "final_box")
        if scope_other:
            edge("assessed_other", "final_box")
        final_lines = [
            f"纳入综述的研究（n = {form.studies_included}）",
            f"纳入研究的报告（n = {form.reports_of_included_studies}）",
        ]
    node(
        "final_box",
        _lines_label(final_lines),
        fillcolor=_JADE,
        fontcolor="#FFFFFF",
        color=_JADE_DARK,
    )
    rank("stage_included", "final_box")

    header = [
        "digraph PRISMA2020 {",
        "    graph [rankdir=TB, splines=ortho, nodesep=0.4, ranksep=0.55,",
        f'        label="{_esc(_footer_label(merge_note))}", labelloc=b, fontsize=10, fontcolor="#5B6B78"];',
        "    node [shape=box, style=\"rounded,filled\", fillcolor=" + f'"{_MAIN_FILL}", color="{_JADE}", fontcolor="#1F2A37", margin="0.16,0.10"];',
        f'    edge [color="{_JADE_DARK}", arrowsize=0.7];',
    ]
    body = nodes + ranks + edges
    return "\n".join(header + body + ["}"]) + "\n"


def _footer_label(merge_note: str) -> str:
    """DOT 页脚：署名行（缺省）；merge_note 非空时以「 | 」追加其后的说明。"""
    note = (merge_note or "").strip()
    return f"{ATTRIBUTION} | {note}" if note else ATTRIBUTION
