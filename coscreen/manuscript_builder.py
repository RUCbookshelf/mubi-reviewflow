"""综述撰写器（Manuscript Builder）：从 ReviewFlow 数据生成 PRISMA 2020 合规
的综述稿件框架（衍生产品规格 §4）。

纯计算模块，零副作用：不连数据库、不起服务、不做任何网络请求、不读写文件
（产品哲学 §0.1 / §0.4）。python-docx 与 cairosvg 在渲染函数内延迟导入，
import 本模块只加载标准库。

生成方式是**规则驱动的模板句填充（非 LLM）**：每个章节声明其数据源
（``MANUSCRIPT_SECTIONS`` 的 content_source），由 API 层收集的纯 dict 数据
逐节渲染成 docx / latex / markdown 三种格式之一的稿件框架。
Discussion / Limitations / Conclusions 三节只留占位（MANUALLY_WRITTEN）。

方法学锚：
- PRISMA 2020 声明（Page et al. 2021, BMJ 372:n71, doi:10.1136/bmj.n71）；
- Cochrane Handbook ch.4（检索）与 ch.9–10（合成、发表偏倚）；
- I² 解读阈值 50%（Cochrane Handbook ch.10, section 10.10.1）；
- Egger 回归（Egger et al. 1997, BMJ 315:629–634）；
- 参考文献样式：AMA Manual of Style 11th ed.、ICMJE（Vancouver）、APA 7th。

数据契约（API 层负责收集，本模块只格式化）::

    data = {
      "task": {"task_id": str, "display_name": str},
      "protocol": dict | None,          # W5 protocol_designer 存 task.json 的方案
      "prisma": {"identified": int, "duplicates_removed": int,
                 "records_screened": int, "records_excluded": int,
                 "records_included": int, "stage2_assessed": int|None,
                 "stage2_included": int|None} | None,
      "articles": [article dict（title/authors/journal/year/doi，
                   可选 volume/issue/pages）],   # 非重复条目（参考文献 + 文献计量）
      "extraction": {"n_studies": int, "n_effects": int,
                     "measures": [str], "entry_methods": [str]} | None,
      "rob": {"n_assessments": int, "frameworks": {name: count},
              "overall_counts": {judgement: count}} | None,
      "syntheses": [ {"comparison", "outcome", "timepoint", "measure",
                      "model", "ci_method", "result": 合成结果 dict | None,
                      "error": str | None, "egger": dict | None,
                      "egger_note": str | None} ],
      "grade": {"n_assessments": int, "sof": SoF 表 dict | None} | None,
      "software": {"name": str, "version": str},
      "generated_at": str,
    }
"""

from __future__ import annotations

import base64
import json
import math
from typing import Callable

__all__ = [
    "MANUSCRIPT_SECTIONS",
    "SECTION_INDEX",
    "PLACEHOLDER_SECTIONS",
    "MANUSCRIPT_OUTPUT_KINDS",
    "REFERENCE_STYLES",
    "PLACEHOLDER_NOTE",
    "ManuscriptError",
    "generate_methods_paragraph",
    "format_reference",
    "build_manuscript",
    "render_markdown",
    "render_latex",
    "render_docx",
    "forest_plot_svg",
    "prisma_flow_svg",
    "svg_to_png",
]

# ---------------------------------------------------------------------------
# 章节模板（规格 §4.2 逐字）
# ---------------------------------------------------------------------------

#: (section_key, section_title, content_source) —— 顺序即稿件顺序
MANUSCRIPT_SECTIONS = [
    ("title", "Title", "from_protocol"),
    ("abstract", "Abstract", "from_synthesis_summary"),
    ("introduction", "Introduction", "from_protocol_background"),
    ("methods_search", "Methods: Search Strategy", "from_protocol_search"),
    ("methods_screening", "Methods: Study Selection", "from_prisma_flow"),
    ("methods_data", "Methods: Data Extraction", "from_extraction_summary"),
    ("methods_risk", "Methods: Risk of Bias", "from_rob_summary"),
    ("methods_synthesis", "Methods: Synthesis", "from_analysis_parameters"),
    ("results_flow", "Results: Study Flow", "from_prisma_counts"),
    ("results_characteristics", "Results: Study Characteristics", "from_bibliometrics"),
    ("results_rob", "Results: Risk of Bias", "from_rob_results"),
    ("results_synthesis", "Results: Synthesis", "from_synthesis_results"),
    ("results_grade", "Results: GRADE", "from_grade_assessments"),
    ("results_heterogeneity", "Results: Heterogeneity", "from_i2_tau"),
    ("results_publication_bias", "Results: Publication Bias", "from_funnel_tests"),
    ("discussion", "Discussion", "MANUALLY_WRITTEN"),  # 不自动生成
    ("limitations", "Limitations", "MANUALLY_WRITTEN"),
    ("conclusions", "Conclusions", "MANUALLY_WRITTEN"),
    ("references", "References", "from_article_list"),
]

SECTION_INDEX: dict[str, tuple[str, str]] = {
    key: (title, source) for key, title, source in MANUSCRIPT_SECTIONS}

#: 仅留占位、不自动生成的章节（content_source == "MANUALLY_WRITTEN"）
PLACEHOLDER_SECTIONS = tuple(
    key for key, _, source in MANUSCRIPT_SECTIONS if source == "MANUALLY_WRITTEN")

#: 稿件输出文件种类。命名不用 ``*_FORMATS`` 后缀：tests/test_measure_registry.py 的
#: 反射扫描会把 coscreen/ 内含 ``_FORMATS`` 的常量行上的引号大写词元当作 measure 代码
#: （与 W6 的 DTA_ROUTING_CODE 同一约束）；本常量枚举导出文件类型，与 measure 注册表无关。
MANUSCRIPT_OUTPUT_KINDS = ("docx", "latex", "markdown")
REFERENCE_STYLES = ("ama", "vancouver", "apa")

PLACEHOLDER_NOTE = ("[This section is intentionally left blank and must be written "
                    "manually by the review authors. — 此节需作者手动撰写，不自动生成。]")

_MISSING_NOTE = "[{source}: data source not available in this task yet — fill in manually. — 数据源暂缺，请手动补写。]"


class ManuscriptError(ValueError):
    """稿件构建参数错误（未知章节键 / 未知样式等）。"""


# ---------------------------------------------------------------------------
# 数字与文本工具
# ---------------------------------------------------------------------------


def _num(value: float | int | None) -> str:
    """与 custom_backend.synthesis_report._number 同口径：4 位有效数字。"""
    return "—" if value is None else f"{float(value):.4g}"


def _esc(text: object) -> str:
    """XML/SVG 文本转义（& < >）。"""
    return (str(text)
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


# ---------------------------------------------------------------------------
# 方法学段落（规格 §4.2 generate_methods_paragraph，规则驱动非 LLM）
# ---------------------------------------------------------------------------

_MODEL_SENTENCES = {
    "fixed": "a fixed-effect (common-effect) model",
    "random": "a random-effects model",
    "random_pm": "a random-effects model",
    "random_reml": "a random-effects model",
}
#: 各随机效应模型默认的 τ² 估计法（与 coscreen.review_analysis.synthesize 一致）
_DEFAULT_TAU2 = {
    "random": "DerSimonian-Laird",
    "random_pm": "Paule-Mandel",
    "random_reml": "restricted maximum likelihood (REML)",
}
#: tau2_method 显式取值 → 全称
_TAU2_ALIASES = {
    "dl": "DerSimonian-Laird", "dersimonian-laird": "DerSimonian-Laird",
    "pm": "Paule-Mandel", "paule-mandel": "Paule-Mandel",
    "reml": "restricted maximum likelihood (REML)",
}
_PUBLICATION_BIAS_TESTS = {
    "egger": "Egger's regression test",
}


def generate_methods_paragraph(synthesis_params: dict) -> str:
    """从实际运行参数生成 Statistical analysis 方法段（模板句，非 LLM）。

    ``synthesis_params`` 读取的键（均带缺省）：

    - ``model``：``"fixed" | "random" | "random_pm" | "random_reml"``（必填）；
    - ``tau2_method``：可选显式 τ² 估计法（``"DL"|"PM"|"REML"``，大小写不敏感），
      缺省按模型推导（random=DL / random_pm=PM / random_reml=REML）；
    - ``ci_method``：``"normal" | "hksj"``（缺省 ``"normal"``）；
    - ``i2``（或 ``i2_percent``）：I² 百分比数值或 None（None 时省略 I² 句）；
    - ``heterogeneity_threshold``：实质性异质性阈值（百分比，缺省 50；
      None 时不生成阈值句）；
    - ``publication_bias_test``：``"egger"`` 或空（空 → 明确写"未做正式检验"）；
    - ``software_name`` / ``software_version``：缺省 ReviewFlow / 1.0。

    未知 model / ci_method / publication_bias_test 抛
    :class:`ManuscriptError`（收紧而非静默丢弃，与项目哲学一致）。

    示例输出（规格 §4.2）::

        Statistical analysis was performed using a random-effects model with
        DerSimonian-Laird tau-squared estimation. The pooled effect estimate was
        calculated as the inverse-variance weighted mean. HKSJ (Hartung-Knapp-
        Sidik-Jonkman) confidence intervals were used. Heterogeneity was
        assessed using the I-squared statistic (I² = 72.3%). Statistical
        heterogeneity was considered substantial when I² exceeded 50%.
        Publication bias was assessed using Egger's regression test. All
        analyses were conducted in ReviewFlow (version 1.0).
    """
    if not isinstance(synthesis_params, dict):
        raise ManuscriptError("synthesis_params 必须是 dict。")
    model = synthesis_params.get("model")
    if model not in _MODEL_SENTENCES:
        raise ManuscriptError(
            f"未知合成模型 {model!r}；合法取值：{sorted(_MODEL_SENTENCES)}。")
    ci_method = str(synthesis_params.get("ci_method") or "normal").lower()
    if ci_method not in {"normal", "hksj"}:
        raise ManuscriptError(f"未知置信区间方法 {ci_method!r}；合法取值：normal / hksj。")

    sentences: list[str] = []
    # 句 1：模型 + τ² 估计
    head = f"Statistical analysis was performed using {_MODEL_SENTENCES[model]}"
    if model != "fixed":
        tau2_method = synthesis_params.get("tau2_method")
        if tau2_method:
            full = _TAU2_ALIASES.get(str(tau2_method).lower())
            if full is None:
                raise ManuscriptError(
                    f"未知 tau2_method {tau2_method!r}；合法取值：DL / PM / REML。")
        else:
            full = _DEFAULT_TAU2[model]
        head += f" with {full} tau-squared estimation"
    sentences.append(head + ".")
    # 句 2：合并方式（逆方差加权均数——与 review_analysis.synthesize 一致）
    sentences.append("The pooled effect estimate was calculated as the "
                     "inverse-variance weighted mean.")
    # 句 3：置信区间
    sentences.append("HKSJ (Hartung-Knapp-Sidik-Jonkman) confidence intervals were used."
                     if ci_method == "hksj" else
                     "Normal approximation 95% confidence intervals were used.")
    # 句 4/5：异质性
    i2 = synthesis_params.get("i2", synthesis_params.get("i2_percent"))
    if i2 is not None:
        sentences.append("Heterogeneity was assessed using the I-squared statistic "
                         f"(I² = {float(i2):.1f}%).")
        threshold = synthesis_params.get("heterogeneity_threshold", 50.0)
        if threshold is not None:
            sentences.append("Statistical heterogeneity was considered substantial "
                             f"when I² exceeded {float(threshold):.0f}%.")
    # 句 6：发表偏倚
    test = synthesis_params.get("publication_bias_test")
    if test:
        name = _PUBLICATION_BIAS_TESTS.get(str(test).lower())
        if name is None:
            raise ManuscriptError(
                f"未知发表偏倚检验 {test!r}；合法取值：{sorted(_PUBLICATION_BIAS_TESTS)}。")
        sentences.append(f"Publication bias was assessed using {name}.")
    else:
        sentences.append("Publication bias was not assessed with a formal statistical test.")
    # 句 7：软件
    software_name = str(synthesis_params.get("software_name") or "ReviewFlow")
    software_version = str(synthesis_params.get("software_version") or "1.0")
    sentences.append(f"All analyses were conducted in {software_name} "
                     f"(version {software_version}).")
    return " ".join(sentences)


# ---------------------------------------------------------------------------
# 参考文献格式化（AMA / Vancouver(ICMJE) / APA 7）
# ---------------------------------------------------------------------------


def _author_list(article: dict) -> list[str]:
    """authors 字符串 → 作者列表（存储约定："; " 分隔，见 normalize_authors）。"""
    raw = str(article.get("authors") or "")
    return [part.strip() for part in raw.split(";") if part.strip()]


def _clean_doi(article: dict) -> str:
    """剥掉存储 DOI 可能带的 doi:/https://doi.org/ 前缀，返回裸 DOI。"""
    doi = str(article.get("doi") or "").strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.lower().startswith(prefix):
            doi = doi[len(prefix):].strip()
    return doi


def _volume_segment(article: dict) -> str:
    """卷(期):页 片段；任一缺失则只拼存在的部分（数据里没有就不编造）。"""
    volume = str(article.get("volume") or "").strip()
    issue = str(article.get("issue") or "").strip()
    pages = str(article.get("pages") or "").strip()
    head = f"{volume}({issue})" if volume and issue else volume or issue
    segment = head
    if head and pages:
        segment = f"{head}:{pages}"
    elif pages:
        segment = pages
    return segment


def format_reference(article: dict, style: str = "ama") -> str:
    """单篇文献 → 目标样式参考文献条目（作者串缺字段时诚实降级，不编造）。

    - ama：AMA 11th——作者（>6 人取前 3 + et al）。标题. 期刊. 年;卷(期):页. doi:x
    - vancouver：ICMJE——作者（>6 人取前 6 + et al）。标题. 期刊. 年;卷(期):页. doi:x
    - apa：APA 7——作者（≤20 全列，>20 前 19 + … + 末位；末位前用 &）. (年).
      标题. 期刊, 卷(期), 页. https://doi.org/x
    """
    style = str(style or "").lower()
    if style not in REFERENCE_STYLES:
        raise ManuscriptError(
            f"未知参考文献样式 {style!r}；合法取值：{list(REFERENCE_STYLES)}。")
    if not isinstance(article, dict):
        raise ManuscriptError("article 必须是 dict。")
    authors = _author_list(article)
    title = str(article.get("title") or "").strip() or "[Title not recorded]"
    journal = str(article.get("journal") or "").strip()
    year = article.get("year")
    doi = _clean_doi(article)
    volume_segment = _volume_segment(article)

    if style in {"ama", "vancouver"}:
        if not authors:
            author_text = "[Authors not recorded]"
        elif len(authors) > 6:
            keep = 3 if style == "ama" else 6  # AMA 11th：>6 取前 3；ICMJE：取前 6
            author_text = ", ".join(authors[:keep]) + ", et al"
        else:
            author_text = ", ".join(authors)
        parts = [author_text + ".", title + "."]
        if journal:
            if year:
                segment = (f"{journal}. {year};{volume_segment}." if volume_segment
                           else f"{journal}. {year}.")
            else:
                segment = f"{journal}.{(' ' + volume_segment + '.') if volume_segment else ''}"
            parts.append(segment)
        elif year:
            parts.append(f"{year}.")
        if doi:
            parts.append(f"doi:{doi}.")
        return " ".join(part for part in parts if part)
    # APA 7
    if not authors:
        author_text = "[Authors not recorded]"
    elif len(authors) > 20:
        author_text = ", ".join(authors[:19]) + ", ... " + authors[-1]
    else:
        author_text = ", ".join(authors[:-1]) + ", & " + authors[-1] if len(authors) > 1 \
            else authors[0]
    parts = [author_text + ("." if not author_text.endswith(".") else "")]
    if year:
        parts.append(f"({year}).")
    parts.append(title + ".")
    if journal:
        volume = str(article.get("volume") or "").strip()
        issue = str(article.get("issue") or "").strip()
        pages = str(article.get("pages") or "").strip()
        tail_parts = []
        if volume and issue:
            tail_parts.append(f"{volume}({issue})")
        elif volume or issue:
            tail_parts.append(volume or issue)
        if pages:
            tail_parts.append(pages)
        segment = journal + (", " + ", ".join(tail_parts) if tail_parts else "")
        parts.append(segment + ".")
    if doi:
        parts.append(f"https://doi.org/{doi}")
    return " ".join(part for part in parts if part)


# ---------------------------------------------------------------------------
# 图（纯字符串 SVG；PNG 转换延迟导入 cairosvg）
# ---------------------------------------------------------------------------

# 与 custom_backend.synthesis_report._LOG_SCALE_MEASURES / _POINT_ESTIMATES 同口径
_LOG_SCALE_MEASURES = {"RR", "OR", "HR", "RATE_RATIO"}
_POINT_ESTIMATES = {"MEAN", "LOGIT_PROP", "LOG_RATE"}


class _Svg:
    """极小 SVG 生成器（纯字符串拼接，确定性输出）。"""

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
                      f'height="{height}" viewBox="0 0 {width} {height}">']

    def text(self, x: float, y: float, content: str, size: int = 12,
             anchor: str = "start", bold: bool = False, fill: str = "#1f364c") -> None:
        weight = ' font-weight="bold"' if bold else ""
        self.parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" font-family="DejaVu Sans, Arial, sans-serif" '
            f'font-size="{size}" text-anchor="{anchor}" fill="{fill}"{weight}>'
            f'{_esc(content)}</text>')

    def line(self, x1: float, y1: float, x2: float, y2: float,
             stroke: str = "#2a6f97", width: float = 2.0, dash: str = "") -> None:
        extra = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                          f'stroke="{stroke}" stroke-width="{width}"{extra}/>')

    def rect(self, x: float, y: float, w: float, h: float,
             stroke: str = "#1f364c", fill: str = "#eef3f7") -> None:
        self.parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
                          f'fill="{fill}" stroke="{stroke}" stroke-width="1.5" rx="4"/>')

    def circle(self, cx: float, cy: float, r: float, fill: str = "#2a6f97") -> None:
        self.parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{fill}"/>')

    def polygon(self, points: list[tuple[float, float]], fill: str = "#1f364c") -> None:
        coords = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        self.parts.append(f'<polygon points="{coords}" fill="{fill}"/>')

    def render(self) -> str:
        return "\n".join([*self.parts, "</svg>"])


def _display_value(value: float, measure: str) -> float:
    """分析尺度 → 展示尺度（与 synthesis_report._display_value 同口径）。"""
    if measure in _LOG_SCALE_MEASURES or measure in {"LOG_RATE", "LOG_ROM", "PAIRED_OR"}:
        return math.exp(value)
    if measure == "FISHER_Z":
        return math.tanh(value)
    if measure == "LOGIT_PROP":
        return 1 / (1 + math.exp(-value)) if value >= 0 else \
            math.exp(value) / (1 + math.exp(value))
    return value


def forest_plot_svg(result: dict, title: str = "") -> str:
    """合成结果 → 森林图 SVG（纯字符串；分析尺度画线，右侧标注展示尺度值）。

    ``result`` 是 ``review_analysis.synthesize`` 的返回值（含 effects /
    pooled_analysis / ci_analysis_low / ci_analysis_high / measure）。
    """
    measure = result.get("measure", "")
    effects = [e for e in result.get("effects", [])
               if isinstance(e.get("estimate"), (int, float))
               and isinstance(e.get("se"), (int, float))]

    def center(effect: dict) -> float:
        return math.log(effect["estimate"]) if effect["measure"] in _LOG_SCALE_MEASURES \
            else effect["estimate"]

    values = []
    if result.get("measure") not in _POINT_ESTIMATES:
        values.append(0.0)
    for effect in effects:
        mid = center(effect)
        values.extend((mid - 1.96 * effect["se"], mid + 1.96 * effect["se"]))
    for key in ("pooled_analysis", "ci_analysis_low", "ci_analysis_high"):
        if isinstance(result.get(key), (int, float)):
            values.append(float(result[key]))
    if not values:
        low, high = -1.0, 1.0
    else:
        low, high = min(values), max(values)
        padding = (high - low) * 0.08 or max(abs(low), 1.0) * 0.08
        low, high = low - padding, high + padding

    row_h = 26.0
    top = 46.0
    width = 780
    plot_x0, plot_x1 = 240.0, 580.0
    height = int(top + row_h * (len(effects) + 1) + 42)
    svg = _Svg(width, height)
    svg.text(8, 24, title or f"Forest plot ({measure})", size=14, bold=True)

    def x_pos(value: float) -> float:
        return plot_x0 + (value - low) / (high - low) * (plot_x1 - plot_x0)

    header_y = top - 10
    svg.text(8, header_y, "Study", size=11, bold=True, fill="#6a7782")
    svg.text(plot_x1 + 8, header_y, "Estimate (95% CI)", size=11, bold=True,
             fill="#6a7782")
    if measure not in _POINT_ESTIMATES:
        ref = x_pos(0.0)
        svg.line(ref, top - 2, ref, height - 34, stroke="#9aa7b1", width=1.0, dash="4 3")
    for index, effect in enumerate(effects):
        y = top + row_h * index + row_h / 2
        mid = center(effect)
        lo = mid - 1.96 * effect["se"]
        hi = mid + 1.96 * effect["se"]
        label = str(effect.get("study_label") or effect.get("study_id") or "")
        svg.text(8, y + 4, label[:30], size=11)
        svg.line(x_pos(lo), y, x_pos(hi), y)
        svg.circle(x_pos(mid), y, 4)
        svg.text(plot_x1 + 8, y + 4,
                 f"{_num(_display_value(mid, effect['measure']))} "
                 f"({_num(_display_value(lo, effect['measure']))} to "
                 f"{_num(_display_value(hi, effect['measure']))})", size=10)
    pooled_y = top + row_h * len(effects) + row_h / 2
    if all(isinstance(result.get(key), (int, float))
           for key in ("pooled_analysis", "ci_analysis_low", "ci_analysis_high")):
        svg.text(8, pooled_y + 4, "Pooled", size=11, bold=True)
        svg.line(x_pos(result["ci_analysis_low"]), pooled_y,
                 x_pos(result["ci_analysis_high"]), pooled_y, stroke="#1f364c", width=3)
        svg.polygon([(x_pos(result["pooled_analysis"]), pooled_y - 7),
                     (x_pos(result["pooled_analysis"]) + 7, pooled_y),
                     (x_pos(result["pooled_analysis"]), pooled_y + 7),
                     (x_pos(result["pooled_analysis"]) - 7, pooled_y)])
        svg.text(plot_x1 + 8, pooled_y + 4,
                 f"{_num(result.get('pooled'))} "
                 f"({_num(result.get('ci_low'))} to {_num(result.get('ci_high'))})",
                 size=10, bold=True)
    svg.line(plot_x0, height - 26, plot_x1, height - 26, stroke="#6a7782", width=1)
    svg.text(plot_x0, height - 12, _num(low), size=9, fill="#6a7782")
    svg.text(plot_x1, height - 12, _num(high), size=9, anchor="end", fill="#6a7782")
    svg.text((plot_x0 + plot_x1) / 2, height - 12,
             "analysis scale" + ("" if measure in _POINT_ESTIMATES else "; dashed line = no effect"),
             size=9, anchor="middle", fill="#6a7782")
    return svg.render()


def prisma_flow_svg(counts: dict) -> str:
    """PRISMA 2020 研究筛选流程图 SVG（主列 4 框 + 右侧排除框，纯字符串）。

    ``counts``：identified / duplicates_removed / records_screened /
    records_excluded / records_included（缺项按 0 并标注 —）。
    """
    def n(key: str) -> str:
        value = counts.get(key)
        return "—" if value is None else str(int(value))

    main_rows = [
        ("Records identified", n("identified")),
        ("Records after duplicates removed",
         str(int(counts.get("identified") or 0) - int(counts.get("duplicates_removed") or 0))
         if counts.get("identified") is not None else "—"),
        ("Records screened", n("records_screened")),
        ("Reports assessed for eligibility / included", n("records_included")),
    ]
    side_rows = [
        ("Duplicate records removed", n("duplicates_removed")),
        ("Records excluded at screening", n("records_excluded")),
    ]
    box_w, box_h, gap = 340.0, 52.0, 46.0
    side_x, side_w = 470.0, 300.0
    width = 800
    height = int(box_h * len(main_rows) + gap * (len(main_rows) - 1) + 40)
    svg = _Svg(width, height)
    svg.text(8, 24, "PRISMA 2020 study flow", size=14, bold=True)
    for index, (label, value) in enumerate(main_rows):
        y = 34 + (box_h + gap) * index
        svg.rect(80, y, box_w, box_h)
        svg.text(80 + box_w / 2, y + 21, label, size=11, anchor="middle")
        svg.text(80 + box_w / 2, y + 39, f"n = {value}", size=12, anchor="middle", bold=True)
        if index:
            svg.line(80 + box_w / 2, y - gap, 80 + box_w / 2, y, stroke="#1f364c")
            svg.polygon([(80 + box_w / 2 - 5, y - 8), (80 + box_w / 2 + 5, y - 8),
                         (80 + box_w / 2, y)])
    # 右侧排除框：分别指向第 1→2 行（去重）与第 3→4 行（初筛排除）
    for (label, value), source_row in zip(side_rows, (0, 2)):
        y = 34 + (box_h + gap) * source_row + box_h / 2
        svg.rect(side_x, y - box_h / 2, side_w, box_h, fill="#f7f7f7")
        svg.text(side_x + side_w / 2, y - 4, label, size=11, anchor="middle")
        svg.text(side_x + side_w / 2, y + 15, f"n = {value}", size=12, anchor="middle", bold=True)
        svg.line(80 + box_w, y, side_x, y, stroke="#1f364c")
        svg.polygon([(side_x - 8, y - 5), (side_x - 8, y + 5), (side_x, y)])
    svg.text(8, height - 8,
             "Counts derived from this task's screening library; PRISMA 2020 (Page et al. 2021, BMJ 372:n71).",
             size=9, fill="#6a7782")
    return svg.render()


def svg_to_png(svg: str, output_width: int = 1560) -> bytes:
    """SVG → PNG（cairosvg 延迟导入；不可用/失败抛 ManuscriptError 由调用方降级）。"""
    try:
        from coscreen.svg_render import svg_to_png as render
        return render(svg.encode("utf-8"), output_width)
    except Exception as exc:
        raise ManuscriptError(f"SVG→PNG 转换失败：{exc}") from exc


# ---------------------------------------------------------------------------
# 章节内容生成（content_source → 构建器）
# ---------------------------------------------------------------------------


def _missing(source: str) -> dict:
    return {"paragraphs": [_MISSING_NOTE.format(source=source)], "preformatted": [],
            "table": None, "figure": None, "placeholder": False}


def _ok(paragraphs: list[str], preformatted: list[str] | None = None,
        table: dict | None = None, figure: dict | None = None) -> dict:
    return {"paragraphs": [p for p in paragraphs if p],
            "preformatted": preformatted or [], "table": table,
            "figure": figure, "placeholder": False}


def _placeholder() -> dict:
    return {"paragraphs": [PLACEHOLDER_NOTE], "preformatted": [], "table": None,
            "figure": None, "placeholder": True}


def _protocol(data: dict) -> dict | None:
    protocol = data.get("protocol")
    return protocol if isinstance(protocol, dict) else None


def _section_title(data: dict) -> dict:
    protocol = _protocol(data)
    task = data.get("task") or {}
    text = (protocol or {}).get("title") or task.get("display_name") or "[Untitled review]"
    return _ok([str(text)])


def _section_abstract(data: dict) -> dict:
    syntheses = [s for s in data.get("syntheses") or [] if isinstance(s, dict)]
    done = [s for s in syntheses if s.get("result")]
    if not done:
        return _missing("from_synthesis_summary")
    lines = []
    for item in done:
        result = item["result"]
        lines.append(
            f"{item['outcome']} ({item['timepoint'] or 'no time point'}, "
            f"{item['measure']}, k = {result.get('n_studies')}): "
            f"pooled {_num(result.get('pooled'))} "
            f"(95% CI {_num(result.get('ci_low'))} to {_num(result.get('ci_high'))}).")
    intro = (f"Background and objectives: see the protocol and introduction. "
             f"Methods: {len(done)} synthesis stratum/strata were pooled by "
             f"inverse-variance meta-analysis in "
             f"{(data.get('software') or {}).get('name', 'ReviewFlow')}. Results: ")
    return _ok([intro + " ".join(lines)])


def _section_introduction(data: dict) -> dict:
    protocol = _protocol(data)
    if not protocol:
        return _missing("from_protocol_background")
    paragraphs = []
    question = str(protocol.get("research_question") or "").strip()
    if question:
        paragraphs.append(f"The research question was: {question}")
    pico = protocol.get("pico")
    if isinstance(pico, list) and pico:
        parts = []
        for element in pico:
            if not isinstance(element, dict):
                continue
            value = str(element.get("value") or "").strip()
            if not value and not (element.get("synonyms") or element.get("mesh_terms")):
                continue
            label = str(element.get("label") or element.get("key") or "").strip()
            parts.append(f"{label or 'element'}: {value or '(terms only)'}")
        if parts:
            paragraphs.append("The review question was structured by PICO — " +
                              "; ".join(parts) + ".")
    if not paragraphs:
        return _missing("from_protocol_background")
    paragraphs.append(
        "[Expand with background and rationale from the protocol — fill in manually.]")
    return _ok(paragraphs)


def _section_methods_search(data: dict) -> dict:
    protocol = _protocol(data)
    strategies = (protocol or {}).get("search_strategies")
    if not (isinstance(strategies, list) and strategies):
        return _missing("from_protocol_search")
    paragraphs = [
        "Electronic searches were designed for the PICO elements of the protocol "
        "(concept groups joined with AND; synonyms and subject headings joined with "
        "OR within each group). The following search strategies were generated and "
        "executed by the review authors:"]
    preformatted = []
    for strategy in strategies:
        if not isinstance(strategy, dict):
            continue
        database = str(strategy.get("database") or "unknown").capitalize()
        query = str(strategy.get("query_string") or "").strip()
        lines = query.count("\n") + 1 if query else 0
        preformatted.append(f"-- {database} ({lines} line(s)) --\n{query or '(empty)'}")
    if not preformatted:
        return _missing("from_protocol_search")
    return _ok(paragraphs, preformatted=preformatted)


def _prisma_counts(data: dict) -> dict | None:
    counts = data.get("prisma")
    return counts if isinstance(counts, dict) else None


def _section_methods_screening(data: dict) -> dict:
    counts = _prisma_counts(data)
    if not counts:
        return _missing("from_prisma_flow")
    sentences = [
        f"Database searching identified {int(counts.get('identified') or 0)} records.",
        f"{int(counts.get('duplicates_removed') or 0)} duplicate records were removed "
        "before screening.",
        f"{int(counts.get('records_screened') or 0)} records were screened on title and "
        f"abstract, of which {int(counts.get('records_excluded') or 0)} were excluded.",
        f"{int(counts.get('records_included') or 0)} records were included after "
        "full-text assessment."]
    stage2_assessed = counts.get("stage2_assessed")
    if stage2_assessed is not None:
        sentences.append(
            f"{int(stage2_assessed)} reports were assessed at the second screening stage; "
            f"{int(counts.get('stage2_included') or 0)} were included.")
    return _ok([" ".join(sentences),
                "[Describe the screening process, dual screening and arbitration as "
                "actually performed — fill in manually.]"])


def _section_methods_data(data: dict) -> dict:
    extraction = data.get("extraction")
    if not (isinstance(extraction, dict) and extraction.get("n_effects")):
        return _missing("from_extraction_summary")
    measures = sorted({str(m) for m in extraction.get("measures") or [] if m})
    entry_methods = sorted({str(m) for m in extraction.get("entry_methods") or [] if m})
    sentences = [
        f"Data extraction was performed with ReviewFlow structured extraction templates; "
        f"{int(extraction.get('n_effects') or 0)} effect estimates were extracted across "
        f"{int(extraction.get('n_studies') or 0)} studies."]
    if measures:
        sentences.append(f"Effect measures used: {', '.join(measures)}.")
    if entry_methods:
        sentences.append(f"Entry methods used: {', '.join(entry_methods)}.")
    return _ok([" ".join(sentences),
                "[Describe extracted variables, pilot testing and disagreement "
                "resolution — fill in manually.]"])


_ROB_FRAMEWORK_NAMES = {
    "RoB 2": "the Cochrane Risk of Bias 2 (RoB 2) tool",
    "ROBINS-I": "the Risk Of Bias In Non-randomized Studies - of Interventions "
                "(ROBINS-I) tool",
    "QUADAS-2": "the Quality Assessment of Diagnostic Accuracy Studies 2 (QUADAS-2) tool",
}


def _rob_summary(data: dict) -> dict | None:
    rob = data.get("rob")
    return rob if isinstance(rob, dict) else None


def _section_methods_rob(data: dict) -> dict:
    rob = _rob_summary(data)
    if not rob or not rob.get("n_assessments"):
        return _missing("from_rob_summary")
    frameworks = rob.get("frameworks") or {}
    if frameworks:
        described = ", ".join(
            f"{_ROB_FRAMEWORK_NAMES.get(name, name)} ({count} assessment(s))"
            for name, count in sorted(frameworks.items()))
        sentence = f"Risk of bias was assessed using {described}."
    else:
        sentence = "Risk of bias was assessed within ReviewFlow."
    return _ok([sentence,
                f"In total {int(rob.get('n_assessments') or 0)} risk-of-bias assessments "
                "were recorded.",
                "[Describe domain-level judgements and the overall judgement rule — "
                "fill in manually.]"])


def _section_methods_synthesis(data: dict) -> dict:
    syntheses = [s for s in data.get("syntheses") or [] if isinstance(s, dict)]
    if not syntheses:
        return _missing("from_analysis_parameters")
    software = data.get("software") or {}
    paragraphs = []
    distinct = {(s.get("model"), s.get("ci_method")) for s in syntheses}
    for item in syntheses:
        result = item.get("result") or {}
        params = {
            "model": item.get("model"),
            "ci_method": item.get("ci_method"),
            "i2": result.get("i2_percent"),
            "publication_bias_test": "egger" if item.get("egger") else None,
            "software_name": software.get("name", "ReviewFlow"),
            "software_version": software.get("version", "1.0"),
        }
        paragraph = generate_methods_paragraph(params)
        if len(distinct) > 1 or len(syntheses) > 1:
            label = (f"{item.get('comparison')} / {item.get('outcome')}"
                     f"{' (' + item['timepoint'] + ')' if item.get('timepoint') else ''}")
            paragraph = f"For {label}: {paragraph}"
        paragraphs.append(paragraph)
    paragraphs.append(
        "[Report sensitivity analyses (leave-one-out, HKSJ), subgroups and "
        "meta-regression as actually performed — fill in manually.]")
    return _ok(paragraphs)


def _section_results_flow(data: dict) -> dict:
    counts = _prisma_counts(data)
    if not counts:
        return _missing("from_prisma_counts")
    rows = [["Records identified", int(counts.get("identified") or 0)],
            ["Duplicate records removed", int(counts.get("duplicates_removed") or 0)],
            ["Records screened", int(counts.get("records_screened") or 0)],
            ["Records excluded at screening", int(counts.get("records_excluded") or 0)],
            ["Records included", int(counts.get("records_included") or 0)]]
    if counts.get("stage2_assessed") is not None:
        rows.append(["Reports assessed at second stage", int(counts["stage2_assessed"])])
        rows.append(["Reports included at second stage",
                     int(counts.get("stage2_included") or 0)])
    table = {"columns": ["Stage", "Records"], "rows": rows}
    figure = None
    if counts.get("identified") is not None:
        svg = prisma_flow_svg(counts)
        figure = {"name": "prisma-flow", "alt": "PRISMA 2020 study flow diagram",
                  "svg": svg}
    return _ok(["The study flow is summarised below."], table=table, figure=figure)


def _section_results_characteristics(data: dict) -> dict:
    articles = [a for a in data.get("articles") or [] if isinstance(a, dict)]
    if not articles:
        return _missing("from_bibliometrics")
    years = sorted(int(a["year"]) for a in articles
                   if isinstance(a.get("year"), int) or
                   (isinstance(a.get("year"), str) and str(a["year"]).isdigit()))
    journals: dict[str, int] = {}
    for article in articles:
        journal = str(article.get("journal") or "").strip()
        if journal:
            journals[journal] = journals.get(journal, 0) + 1
    sentences = [f"{len(articles)} unique records were included in the review library."]
    if years:
        sentences.append(f"Publication years ranged from {years[0]} to {years[-1]} "
                         f"(median {years[len(years) // 2]}).")
    rows = [[journal, count] for journal, count in
            sorted(journals.items(), key=lambda kv: (-kv[1], kv[0]))[:10]]
    table = {"columns": ["Journal", "Records"], "rows": rows} if rows else None
    if journals:
        top = ", ".join(f"{name} ({count})" for name, count in rows[:5])
        sentences.append(f"Most frequent journals: {top}.")
    paragraphs = [" ".join(sentences),
                  "[Add the study characteristics table of included studies — "
                  "fill in manually.]"]
    return _ok(paragraphs, table=table)


def _rob_overall_counts(rob: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for judgement, count in (rob.get("overall_counts") or {}).items():
        try:
            counts[str(judgement)] = int(count)
        except (TypeError, ValueError):
            continue
    return counts


def _section_results_rob(data: dict) -> dict:
    rob = _rob_summary(data)
    if not rob or not rob.get("n_assessments"):
        return _missing("from_rob_results")
    counts = _rob_overall_counts(rob)
    if not counts:
        return _ok(["Risk-of-bias assessments were recorded but no overall judgements "
                    "could be aggregated."])
    rows = [[judgement, count] for judgement, count in
            sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    table = {"columns": ["Overall risk-of-bias judgement", "Assessments"], "rows": rows}
    summary = ", ".join(f"{judgement}: {count}" for judgement, count in rows)
    return _ok([f"Overall risk-of-bias judgements across the latest assessment per "
                f"study — {summary}."], table=table)


def _section_results_synthesis(data: dict) -> dict:
    syntheses = [s for s in data.get("syntheses") or [] if isinstance(s, dict)]
    if not syntheses:
        return _missing("from_synthesis_results")
    paragraphs = []
    table_rows = []
    figures = []
    for item in syntheses:
        label = (f"{item.get('comparison')} / {item.get('outcome')}"
                 f"{' (' + item['timepoint'] + ')' if item.get('timepoint') else ''}")
        result = item.get("result")
        if not result:
            error = item.get("error") or "result unavailable"
            paragraphs.append(f"{label}: synthesis not performed ({error}).")
            table_rows.append([label, item.get("measure", ""), "—", "—", "—"])
            continue
        paragraphs.append(
            f"{label}: pooled {item.get('measure')} = {_num(result.get('pooled'))} "
            f"(95% CI {_num(result.get('ci_low'))} to {_num(result.get('ci_high'))}; "
            f"k = {result.get('n_studies')}; {result.get('model')} model, "
            f"{result.get('method')}).")
        table_rows.append([label, item.get("measure", ""),
                           _num(result.get("pooled")),
                           f"{_num(result.get('ci_low'))} to {_num(result.get('ci_high'))}",
                           result.get("n_studies")])
        figures.append({"name": f"forest-{len(figures) + 1}",
                        "alt": f"Forest plot for {label}",
                        "svg": forest_plot_svg(result, title=label)})
    table = {"columns": ["Comparison / outcome", "Measure", "Pooled estimate",
                         "95% CI", "Studies"],
             "rows": table_rows}
    # 多个森林图：取第一幅作为本节图（其余作为附加段落注记，渲染层逐幅输出）
    section = _ok(paragraphs, table=table, figure=figures[0] if figures else None)
    section["extra_figures"] = figures[1:]  # type: ignore[typeddict-unknown-key]
    return section


def _section_results_grade(data: dict) -> dict:
    grade = data.get("grade")
    if not (isinstance(grade, dict) and grade.get("n_assessments")):
        return _missing("from_grade_assessments")
    sof = grade.get("sof")
    if not (isinstance(sof, dict) and sof.get("rows")):
        return _ok([f"{int(grade['n_assessments'])} GRADE assessments were saved; "
                    "the Summary of Findings table could not be rebuilt from them."])
    table = {"columns": [str(c) for c in sof.get("columns") or []],
             "rows": [[str(cell) for cell in row] for row in sof["rows"]]}
    return _ok(["The certainty of the evidence was rated with GRADE; the Summary of "
                "Findings table follows."], table=table)


def _section_results_heterogeneity(data: dict) -> dict:
    syntheses = [s for s in data.get("syntheses") or []
                 if isinstance(s, dict) and s.get("result")]
    if not syntheses:
        return _missing("from_i2_tau")
    rows = []
    paragraphs = []
    for item in syntheses:
        result = item["result"]
        label = f"{item.get('comparison')} / {item.get('outcome')}"
        if item.get("timepoint"):
            label += f" ({item['timepoint']})"
        tau2 = result.get("tau2")
        rows.append([label, f"{_num(result.get('i2_percent'))}%", _num(tau2),
                     _num(result.get("q")), result.get("q_df"),
                     _num(result.get("q_p"))])
        sentence = (f"{label}: I² = {_num(result.get('i2_percent'))}%")
        if tau2 is not None:
            sentence += f", τ² = {_num(tau2)}"
        sentence += f", Q({result.get('q_df')}) = {_num(result.get('q'))}, " \
                    f"p = {_num(result.get('q_p'))}."
        paragraphs.append(sentence)
    table = {"columns": ["Comparison / outcome", "I²", "τ²", "Q", "df", "p"],
             "rows": rows}
    return _ok(paragraphs, table=table)


def _section_results_publication_bias(data: dict) -> dict:
    syntheses = [s for s in data.get("syntheses") or [] if isinstance(s, dict)]
    if not syntheses:
        return _missing("from_funnel_tests")
    paragraphs = []
    for item in syntheses:
        label = f"{item.get('comparison')} / {item.get('outcome')}"
        if item.get("timepoint"):
            label += f" ({item['timepoint']})"
        egger = item.get("egger")
        if isinstance(egger, dict) and egger.get("egger_p") is not None:
            paragraphs.append(
                f"{label}: Egger's regression intercept = "
                f"{_num(egger.get('egger_intercept'))} "
                f"(p = {_num(egger.get('egger_p'))}, k = {egger.get('n_studies')}). "
                "Asymmetry indicates a small-study effect, not proof of publication bias.")
        else:
            note = item.get("egger_note") or \
                "Egger's test requires at least ten independent studies"
            paragraphs.append(f"{label}: not assessed ({note}).")
    return _ok(paragraphs)


def _section_references(data: dict) -> dict:
    articles = [a for a in data.get("articles") or [] if isinstance(a, dict)]
    if not articles:
        return _missing("from_article_list")
    entries = []
    for index, article in enumerate(articles, start=1):
        entries.append(f"{index}. {format_reference(article, data.get('__reference_style__', 'ama'))}")
    note = (f"[{len(articles)} reference(s) generated from the task's unique records "
            "using the selected citation style; verify every entry against the source "
            "before submission.]")
    return _ok([*entries, note])


_SECTION_BUILDERS: dict[str, Callable[[dict], dict]] = {
    "from_protocol": _section_title,
    "from_synthesis_summary": _section_abstract,
    "from_protocol_background": _section_introduction,
    "from_protocol_search": _section_methods_search,
    "from_prisma_flow": _section_methods_screening,
    "from_extraction_summary": _section_methods_data,
    "from_rob_summary": _section_methods_rob,
    "from_analysis_parameters": _section_methods_synthesis,
    "from_prisma_counts": _section_results_flow,
    "from_bibliometrics": _section_results_characteristics,
    "from_rob_results": _section_results_rob,
    "from_synthesis_results": _section_results_synthesis,
    "from_grade_assessments": _section_results_grade,
    "from_i2_tau": _section_results_heterogeneity,
    "from_funnel_tests": _section_results_publication_bias,
    "from_article_list": _section_references,
}


def build_manuscript(data: dict, sections: list[str] | tuple[str, ...] = ("all",),
                     include_figures: bool = True,
                     reference_style: str = "ama") -> dict:
    """按章节模板把收集好的数据渲染为结构化稿件（纯 dict，供三种渲染器使用）。

    - ``sections``：``["all"]`` 或 section_key 列表（顺序仍按 MANUSCRIPT_SECTIONS，
      未知键抛 :class:`ManuscriptError`）；
    - ``include_figures``：False 时剔除所有 figure（段落与表格保留）；
    - ``reference_style``：ama / vancouver / apa（非法抛 ManuscriptError）。

    返回 ``{"meta": {...}, "sections": [{"key","title","source","paragraphs",
    "preformatted","table","figure","extra_figures","placeholder"}, ...]}``。
    """
    if not isinstance(data, dict):
        raise ManuscriptError("data 必须是 dict。")
    reference_style = str(reference_style or "").lower()
    if reference_style not in REFERENCE_STYLES:
        raise ManuscriptError(
            f"未知参考文献样式 {reference_style!r}；合法取值：{list(REFERENCE_STYLES)}。")
    if not isinstance(sections, (list, tuple)) or not sections:
        raise ManuscriptError("sections 必须是非空列表（['all'] 或 section_key 列表）。")
    keys = list(sections)
    if keys == ["all"]:
        selected = [key for key, _, _ in MANUSCRIPT_SECTIONS]
    else:
        known = set(SECTION_INDEX)
        unknown = [key for key in keys if key not in known]
        if unknown:
            raise ManuscriptError(
                f"未知章节键 {unknown}；合法取值：{sorted(known)}（或 ['all']）。")
        selected = [key for key, _, _ in MANUSCRIPT_SECTIONS if key in set(keys)]

    payload = dict(data)
    payload["__reference_style__"] = reference_style
    built = []
    for key in selected:
        title, source = SECTION_INDEX[key]
        if source == "MANUALLY_WRITTEN":
            content = _placeholder()
        else:
            content = _SECTION_BUILDERS[source](payload)
        if not include_figures:
            content["figure"] = None
            content["extra_figures"] = []
        content.setdefault("extra_figures", [])
        built.append({"key": key, "title": title, "source": source, **content})
    software = data.get("software") or {}
    meta = {
        "reference_style": reference_style,
        "include_figures": bool(include_figures),
        "software": f"{software.get('name', 'ReviewFlow')} "
                    f"{software.get('version', '1.0')}",
        "generated_at": str(data.get("generated_at") or ""),
        "n_sections": len(built),
        "models": sorted({str(s.get("model")) for s in data.get("syntheses") or []
                          if isinstance(s, dict)} - {""}),
    }
    return {"meta": meta, "sections": built}


# ---------------------------------------------------------------------------
# 渲染器：markdown / latex / docx
# ---------------------------------------------------------------------------


def _figure_markdown(figure: dict) -> str:
    encoded = base64.b64encode(figure["svg"].encode("utf-8")).decode("ascii")
    return (f'\n<img alt="{_esc(figure.get("alt", figure.get("name", "figure")))}" '
            f'src="data:image/svg+xml;base64,{encoded}" width="780"/>\n')


def _table_markdown(table: dict) -> str:
    columns = [str(c).replace("|", "\\|") for c in table.get("columns") or []]
    if not columns:
        return ""
    lines = ["| " + " | ".join(columns) + " |",
             "| " + " | ".join("---" for _ in columns) + " |"]
    for row in table.get("rows") or []:
        lines.append("| " + " | ".join(
            str(cell).replace("|", "\\|").replace("\n", " ") for cell in row) + " |")
    return "\n".join(lines)


def render_markdown(manuscript: dict) -> str:
    """结构化稿件 → Markdown（预览端点与 markdown 导出共用）。"""
    sections = manuscript.get("sections") or []
    lines: list[str] = []
    for index, section in enumerate(sections):
        title = section["title"]
        if section["key"] == "title":
            lines.append(f"# {section['paragraphs'][0] if section['paragraphs'] else 'Untitled'}")
            lines.append("")
            continue
        lines.append(f"## {title}")
        lines.append("")
        for paragraph in section.get("paragraphs") or []:
            lines.append(paragraph)
            lines.append("")
        for block in section.get("preformatted") or []:
            lines.append("```text")
            lines.extend(block.splitlines())
            lines.append("```")
            lines.append("")
        if section.get("table"):
            rendered = _table_markdown(section["table"])
            if rendered:
                lines.append(rendered)
                lines.append("")
        for figure in [section.get("figure"), *(section.get("extra_figures") or [])]:
            if figure:
                lines.append(_figure_markdown(figure))
    lines.append("---")
    meta = manuscript.get("meta") or {}
    lines.append(f"*Generated by {meta.get('software', 'ReviewFlow')} on "
                 f"{meta.get('generated_at') or 'n/a'}; reference style: "
                 f"{meta.get('reference_style', 'ama')}.*")
    return "\n".join(lines).strip() + "\n"


_LATEX_ESCAPES = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
                  "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
                  "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def _latex_escape(text: str) -> str:
    return "".join(_LATEX_ESCAPES.get(ch, ch) for ch in str(text))


def _table_latex(table: dict) -> str:
    columns = table.get("columns") or []
    if not columns:
        return ""
    spec = "l" + "r" * (len(columns) - 1)
    lines = [r"\begin{tabular}{" + spec + "}", r"\toprule",
             " & ".join(_latex_escape(c) for c in columns) + r" \\",
             r"\midrule"]
    for row in table.get("rows") or []:
        lines.append(" & ".join(_latex_escape(cell) for cell in row) + r" \\")
    lines.extend([r"\bottomrule", r"\end{tabular}"])
    return "\n".join(lines)


def render_latex(manuscript: dict) -> str:
    """结构化稿件 → 单文件 LaTeX（图表以注释占位：LaTeX 无法内联二进制图像）。"""
    sections = manuscript.get("sections") or []
    body: list[str] = []
    title_block: list[str] = []
    for section in sections:
        if section["key"] == "title":
            title = section["paragraphs"][0] if section["paragraphs"] else "Untitled"
            title_block.append(r"\title{" + _latex_escape(title) + "}")
            title_block.append(r"\author{ReviewFlow manuscript framework}")
            title_block.append(r"\date{" + _latex_escape((manuscript.get("meta") or {})
                                                  .get("generated_at") or "") + "}")
            continue
        body.append("")
        body.append(r"\section{" + _latex_escape(section["title"]) + "}")
        for paragraph in section.get("paragraphs") or []:
            body.append("")
            body.append(_latex_escape(paragraph) if not section.get("placeholder")
                        else r"\textit{" + _latex_escape(paragraph) + "}")
        for block in section.get("preformatted") or []:
            body.extend(["", r"\begin{verbatim}", block, r"\end{verbatim}"])
        if section.get("table"):
            rendered = _table_latex(section["table"])
            if rendered:
                body.extend(["", rendered])
        for figure in [section.get("figure"), *(section.get("extra_figures") or [])]:
            if figure:
                body.extend(["",
                             f"% figure: {figure.get('name')} ({figure.get('alt')}) — "
                             "export the SVG/PNG and include with \\includegraphics"])
    preamble = [
        r"% Generated by ReviewFlow manuscript builder (offline, rule-based).",
        r"% Compile with xelatex/lualatex for full Unicode (I², τ², CJK).",
        r"\documentclass[11pt]{article}",
        r"\usepackage{booktabs}",
        r"\usepackage{graphicx}",
        r"\usepackage{longtable}",
        r"\usepackage[hmargin=1in]{geometry}",
    ]
    return "\n".join([*preamble, *title_block, r"\begin{document}", r"\maketitle",
                      *body, r"\end{document}", ""])


def render_docx(manuscript: dict) -> bytes:
    """结构化稿件 → Word 文档字节（python-docx；复用 synthesis_report 的导出形态）。

    图表嵌入：SVG 经 cairosvg 转 PNG 后 add_picture；cairosvg 不可用或转换失败时
    降级为斜体文字注记（单点故障不阻断稿件生成）。
    """
    from docx import Document
    from docx.shared import Inches, Pt
    from io import BytesIO

    document = Document()
    document.sections[0].top_margin = Inches(0.8)
    document.sections[0].bottom_margin = Inches(0.8)
    for section in manuscript.get("sections") or []:
        if section["key"] == "title":
            title = section["paragraphs"][0] if section["paragraphs"] else "Untitled"
            document.add_heading(title, 0)
            continue
        document.add_heading(section["title"], level=1)
        for paragraph in section.get("paragraphs") or []:
            added = document.add_paragraph(paragraph)
            if section.get("placeholder"):
                for run in added.runs:
                    run.italic = True
        for block in section.get("preformatted") or []:
            added = document.add_paragraph(block)
            for run in added.runs:
                run.font.name = "Consolas"
                run.font.size = Pt(8)
        table = section.get("table")
        if table and table.get("columns"):
            docx_table = document.add_table(rows=1, cols=len(table["columns"]))
            docx_table.style = "Table Grid"
            for index, column in enumerate(table["columns"]):
                docx_table.rows[0].cells[index].text = str(column)
            for row in table.get("rows") or []:
                cells = docx_table.add_row().cells
                for index, cell in enumerate(row):
                    cells[index].text = str(cell)
        for figure in [section.get("figure"), *(section.get("extra_figures") or [])]:
            if not figure:
                continue
            try:
                png = svg_to_png(figure["svg"])
                document.add_picture(BytesIO(png), width=Inches(6.3))
            except ManuscriptError as exc:
                note = document.add_paragraph(
                    f"[Figure '{figure.get('name')}' could not be embedded: {exc}. "
                    "The data table above carries the same information.]")
                for run in note.runs:
                    run.italic = True
    meta = manuscript.get("meta") or {}
    footer = document.add_paragraph(
        f"Generated by {meta.get('software', 'ReviewFlow')} on "
        f"{meta.get('generated_at') or 'n/a'}; reference style: "
        f"{meta.get('reference_style', 'ama')}.")
    for run in footer.runs:
        run.font.size = Pt(8)
    out = BytesIO()
    document.save(out)
    return out.getvalue()


def manuscript_to_json(manuscript: dict) -> str:
    """结构化稿件 → JSON 字符串（调试/测试辅助；SVG 内联其中）。"""
    return json.dumps(manuscript, ensure_ascii=False, indent=2)
