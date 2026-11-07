"""Render A4_comparison.md from fixtures/results/*.json (deterministic, no rounding tricks).

Usage:
  "C:/Users/16073/.codex/preview-data/reviewflow-2-1-1-venv/Scripts/python.exe" \
      E:/mubi-reviewflow/out/benchmark/fixtures/make_report.py
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
REPORT = HERE.parent / "A4_comparison.md"

ORDER = [
    "Normand1999", "DK2007", "Linde2005", "Raudenbush1985", "BCG",
    "Rothman2008", "Yusuf1985", "Egger2001", "CohenKappa", "Senn2013",
    "FleissLightKappa",
]

VERDICT_ORDER = {"PASS": 0, "FAIL": 1, "INFO": 2, "NOT-RUN": 3}


def fmt(value, digits: int = 8) -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        return value
    if isinstance(value, bool) or isinstance(value, int):
        return str(value)
    if value != value:  # NaN
        return "NaN"
    if value in (float("inf"), float("-inf")):
        return "inf"
    if value == int(value) and abs(value) < 1e15:
        return f"{value:.1f}"
    text = f"{value:.{digits}f}"
    if abs(value) < 1e-4 and value != 0.0:
        return f"{value:.3e}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def tol_str(check: dict) -> str:
    if check["verdict"] == "NOT-RUN":
        return "—"
    if check["verdict"] == "INFO":
        return "—"
    if check.get("eff_abs_tol") is not None:
        return f"abs<= {check['eff_abs_tol']:g}"
    eff = check.get("eff_rel_tol")
    if eff is None:
        return "—"
    return f"rel<= {eff:.3g} (A1 {check['rel_tol_a1']:g})"


def diff_cols(check: dict) -> tuple[str, str]:
    if check["verdict"] in ("NOT-RUN", "INFO"):
        return "—", "—"
    if check.get("rel_diff") is None:
        return fmt(check["abs_diff"]), "abs"
    return fmt(check["abs_diff"]), fmt(check["rel_diff"])


def main() -> None:
    payloads = {}
    for path in sorted(RESULTS.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["_file"] = path.name
        payloads[payload["dataset"]] = payload
    datasets = [d for d in ORDER if d in payloads]
    for d in sorted(payloads):
        if d not in datasets:
            datasets.append(d)

    total = {"PASS": 0, "FAIL": 0, "NOT-RUN": 0, "INFO": 0}
    for payload in payloads.values():
        for key in total:
            total[key] += payload["counts"].get(key, 0)

    lines: list[str] = []
    lines.append("# A4 全量运行与数值比对报告 — 木笔 ReviewFlow 统计模块第三方体检")
    lines.append("")
    lines.append("- 生成方式：`fixtures/make_report.py` 由 `fixtures/results/*.json` 逐位转录，无手工抄写。")
    lines.append("- 被测代码：`E:/rf214-ai`（fix/2.1.4 @ eb11741 静态副本；coscreen 统计内核，直调纯函数/DB 内核，"
                 "未走 HTTP 层，依据 A2 §0）。")
    lines.append("- 解释器：`C:/Users/16073/.codex/preview-data/reviewflow-2-1-1-venv/Scripts/python.exe`"
                 "（Python 3.11.15；numpy 2.4.6 / scipy 1.17.1 / statsmodels 0.14.5 / pandas 2.3.3）。")
    lines.append("- 输入数据：全部硬编码自 A1 §1–§8 字面量表；未访问网络。")
    lines.append("- 容差：rel 列 = 生效容差 = max(A1 §9 建议值, 发表值末位半单位换算的相对精度)——"
                 "发表精度的舍入半单位是可核验精度的下限，比 A1 建议更松时以舍入地板为准（如 2 位小数的发表量"
                 "无法支撑 1e-4 相对比对）；整型输出要求全等；发表值为 0 的量（如边界 τ²=0.000）用绝对容差 "
                 "0.5×10^-d。")
    lines.append("- 判定口径：统计量严格、p 宽松、整型全等（A1 §9）。“derived” = 由被测代码输出经代数恒等式"
                 "推导后比对（如 SE=CI 宽/2z、H²=Q/df、log(OR)），推导式在各夹具文件头注明。")
    lines.append("- 复跑（幂等，覆盖 results/）：`\"C:/Users/16073/.codex/preview-data/reviewflow-2-1-1-venv/Scripts/python.exe\" E:/mubi-reviewflow/out/benchmark/fixtures/run_all.py`")
    lines.append("")
    lines.append("## 汇总")
    lines.append("")
    lines.append(f"- **PASS：{total['PASS']}　FAIL：{total['FAIL']}　NOT-RUN：{total['NOT-RUN']}　"
                 f"INFO（已执行但无匹配发表参照）：{total['INFO']}**（按检查行计数）")
    lines.append("")
    lines.append("| 数据集 | PASS | FAIL | NOT-RUN | INFO |")
    lines.append("|---|---|---|---|---|")
    for d in datasets:
        c = payloads[d]["counts"]
        lines.append(f"| {d} | {c.get('PASS', 0)} | {c.get('FAIL', 0)} | {c.get('NOT-RUN', 0)} | {c.get('INFO', 0)} |")
    lines.append("")

    for d in datasets:
        payload = payloads[d]
        lines.append(f"## {d}")
        lines.append("")
        lines.append(f"参照：{payload['checks'][0]['source'] if payload['checks'] else ''}")
        lines.append("")
        lines.append("| 检查项 | 方法 | 实测 | 发表 | 绝对偏差 | 相对偏差 | 容差 | 判定 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for c in payload["checks"]:
            abs_d, rel_d = diff_cols(c)
            metric = c["metric"] + (" *(derived)*" if c.get("derived") else "")
            if c.get("note"):
                metric += f" 〔{c['note']}〕"
            pub = fmt(c["published"]) if not isinstance(c["published"], str) else c["published"]
            obs = fmt(c["observed"]) if not isinstance(c["observed"], str) else c["observed"]
            lines.append(f"| {c['id']} | {c['method']} | {obs} | {pub} | {abs_d} | {rel_d} | "
                         f"{tol_str(c)} | **{c['verdict']}** |")
        lines.append("")

    fails = [c for payload in payloads.values() for c in payload["checks"] if c["verdict"] == "FAIL"]
    lines.append("## FAIL 明细（供归因，全部关键数字 8 位）")
    lines.append("")
    if not fails:
        lines.append("无 FAIL。")
    for c in fails:
        lines.append(f"### {c['id']} — {c['dataset']} / {c['method']} / {c['metric']}")
        lines.append("")
        lines.append(f"- 实测 = `{fmt(c['observed'])}`；发表 = `{fmt(c['published'])}`"
                     f"（{c['source']}）")
        if c.get("rel_diff") is None:
            lines.append(f"- 绝对偏差 = `{fmt(c['abs_diff'])}`（绝对容差 {c.get('eff_abs_tol')}）")
        else:
            lines.append(f"- 绝对偏差 = `{fmt(c['abs_diff'])}`；相对偏差 = `{fmt(c['rel_diff'])}`；"
                         f"生效相对容差 = `{c['eff_rel_tol']:.6g}`（A1 建议 {c['rel_tol_a1']:g}）")
        if c.get("note"):
            lines.append(f"- 备注：{c['note']}")
        lines.append("")

    lines.append("## NOT-RUN / INFO 清单（原因）")
    lines.append("")
    for d in datasets:
        rows = [c for c in payloads[d]["checks"] if c["verdict"] in ("NOT-RUN", "INFO")]
        if not rows:
            continue
        lines.append(f"### {d}")
        lines.append("")
        for c in rows:
            published = c["published"] if isinstance(c["published"], str) else fmt(c["published"])
            lines.append(f"- **{c['verdict']}** {c['method']}"
                         + (f"（应比对象：{published}）" if published else "")
                         + f"：{c['note']}")
        lines.append("")

    lines.append("## 结果文件")
    lines.append("")
    for d in datasets:
        lines.append(f"- `fixtures/results/{payloads[d]['_file']}`（{d}：含被测代码全部原始输出 raw 块，"
                     "8 位以上精度，供逐位对拍）")
    lines.append("")

    lines.append("## 执行中发现的 A1/A2 文档与实际不符 / 需交接事项")
    lines.append("")
    lines.append("以下为执行期核实的事实（不做算法对错结论，归因留给下一阶段）：")
    lines.append("")
    lines.append("1. **A1 §6.1 Cohen kappa ⚠ 已定向**：A1 手算用 pe=208/900，但打印表的行边际为 "
                 "(13,10,2,1,4)、列边际为 (7,9,5,5,4)，Σ行×列=13·7+10·9+2·5+1·5+4·4=**212**，"
                 "正确 pe=212/900=0.235556；由此 κ=(22/30−212/900)/(1−212/900)=**0.651163**，与页面打印 "
                 "0.6512 一致。A1 的 208 属手算笔误，页面值正确，被测 cohen_kappa 与页面一致（PASS）。")
    lines.append("2. **A1 §1.2/§2.2 REML 行的 I²/H² 与本页 Q 不可互推**：Normand 页 REML 行印 I²=98.92%/"
                 "H²=92.26，但同页 Q(8)=241.06 ⇒ (Q−df)/Q=96.68%、Q/df=30.13；R&B 页印 41.86%/1.72，"
                 "同页 Q(18)=35.830 ⇒ 49.76%/1.99。两处页面印值彼此自洽（1−1/H²=I²）但都不能由本页 Q 导出，"
                 "与 A1 §1.1 对 BCG I² 的 ⚠（92.22 vs 92.12）同类。对照：Yusuf 页 H²=0.64 恰等于 Q/df，说明"
                 "各页 I²/H² 口径不一致（或 A1 转录自不同输出段）。按 A1 重算政策，主判据用 Q 导出值（PASS），"
                 "页面印值保留为 secondary 行（FAIL，供归因）。被测内核 i2_percent 恒为 (Q−df)/Q。")
    lines.append("3. **A1 §7.1 Senn2013 的 escalc 口径已实证**：metadat 示例页的对比方差为 metafor "
                 "escalc(measure='MD') 默认的**非合并**方差 sd1²/n1+sd2²/n2（用该口径本夹具精确复现 "
                 "τ²=0.099915、九个 vs 安慰剂估计至 4 位、QE=96.9841）；合并 SD（HO）口径则偏离（原始块"
                 " `variant_pooled_sd_ho` 留档）。A1 §1.2 对 Normand 标注 vtype='HO' 同样被实证（HO 得 "
                 "Q=241.059≈发表 241.06，非合并得 238.916）。两数据集口径不同并非矛盾——是两页各自的分析选择。")
    lines.append("4. **A1 §3.2 Yusuf1985 悬案已破（A5 阶段，2026-10-27）**：原 8 项 FAIL 根因为 A1 将 Wilcox 1 "
                 "治疗组事件数转录为 1，实际为 8（源页 dat.yusuf1985 行 \"Wilcox 1 8 259 7 129\"，"
                 "metadat 文档交叉确认）。修正后 Σ(O−E)=−4.855、ΣV=70.13，与发表值反推的 −4.85/70.13 吻合，"
                 "10 项检查全部 PASS（含 logOR=−0.0692/SE=0.1194/Q(17)=10.8275）。被测 pool_peto 全程正确"
                 "——修正前后独立教科书重算均与内核逐位一致。")
    lines.append("5. **A2 §7 Egger 口径补充**：被测 small_study_effects 为 y/se 对 1/se 的**无权重 OLS**"
                 "（经典 Egger 1997 缩放式）。其 p（0.007264）与 metafor regtest 的 classic lm 版 "
                 "（t=−3.1783, p=0.0073）吻合，与默认 rma 版（z=−2.8062, p=0.0050）有 45% 相对差 —— 两行"
                 "参照均保留在报告中。")
    lines.append("6. **产品效应量构造器的口径备注（非错误）**：coscreen.review_analysis.continuous_effect"
                 "(measure='MD') 的抽样方差为非合并式 sd_t²/n_t+sd_c²/n_c（与 metafor escalc MD 默认一致）；"
                 "Normand1999 发表分析用的是 vtype='HO' 合并式，故若端到端走产品构造器会得到与发表分析略不同"
                 "的 vi（本夹具按 A1 指定的 HO 由夹具侧构造 vi，只测合并内核）。")
    lines.append("7. **DB 夹具配方补充**：A2 §1.4 样例的 `with _connect(...)` 只提交事务不关闭连接，Windows "
                 "下会锁住 db 文件；夹具改用 `contextlib.closing`+事务并已回写 fixtures/common 说明。")
    lines.append("8. **Fleiss/Light 数据缺口确认**：本机无 R（Rscript 不存在）、且夹具禁网，irr::diagnoses "
                 "30×6 矩阵无法导出（A1 §6.2 仅公开前 4 行）→ Fleiss/Light 全部 NOT-RUN；仓库亦无 Fleiss 实现"
                 "（A2 §5 已标注，grep 复核为 0 命中）。")
    lines.append("")
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"report -> {REPORT} ({len(fails)} FAIL rows)")


if __name__ == "__main__":
    main()
