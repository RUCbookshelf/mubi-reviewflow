/* Analysis display vocabulary. Machine enum values and source text are not rewritten. */
(function () {
  'use strict';
  const pairs = [
  [
    "请核对 Bonett（2021）第 3 卷第 3.4 节、公式 3.11–3.12 及例 3.6/3.8；四分相关近似见 Bonett 与 Price（2005）。该近似使用校正后的优势比及依赖边际分布的指数，并非最大似然估计。请引用实际方法和研究报告；不要使用连续 Pearson 相关的标准误 1/√(n−3) 替代。",
    "Verify Bonett (2021), Volume 3, §3.4, equations 3.11–3.12 and examples 3.6/3.8; Bonett & Price (2005) for the tetrachoric approximation. The approximation uses a corrected odds ratio and a marginal-adaptive exponent, not maximum likelihood. Cite the chosen method and source report; review the original book text. Do not substitute the continuous-Pearson SE 1/√(n−3)."
  ],
  [
    "请核对 Harrer 等（2022）《Doing Meta-Analysis with R》第 4.2.2 节，第 112–114 页（Glass 段落在第 113 页），以及 Hedges（1982）。计算对应 metafor 的 SMD1、correct=FALSE、vtype=LS。请核对原文，并引用标准化方式、方差方法和研究报告。",
    "Check Harrer et al. (2022), Doing Meta-Analysis with R, §4.2.2, pp. 112–114 (Glass passage p. 113), and Hedges (1982). The calculation matches metafor SMD1, correct=FALSE, vtype=LS. Verify the original methods and cite the standardizer, variance method and study reports."
  ],
  [
    "请核对 Borenstein 等（2021）《Introduction to Meta-Analysis》第 2 版第 4 章，第 30–31 页，以及 Hedges、Gurevitch 与 Curtis（1999）公式 1。这里使用未校正的对数均值比及分组 delta 方差（metafor ROM、correct=FALSE、vtype=LS）。请引用方法和研究报告；原书印出的标准误与所印方差的平方根不一致。",
    "Verify Borenstein et al. (2021), Introduction to Meta-Analysis, 2nd ed., chapter 4, pp. 30–31, and Hedges, Gurevitch & Curtis (1999), equation 1. This uses uncorrected log ROM with separate-arm delta variances (metafor ROM, correct=FALSE, vtype=LS). Cite the method and source reports; the book's printed SE differs from the square root of its printed variance."
  ],
  [
    "原文报告的效应及不确定性，请核对",
    "For reported effects and uncertainty, check the"
  ],
  [
    "Cochrane Handbook 第 6 章，第 6.3.1–6.3.2 节",
    "Cochrane Handbook ch. 6, §§6.3.1–6.3.2"
  ],
  [
    "。比值效应在对数尺度上分析。请引用实际计算方法和研究报告；发表前核对检验及区间假设。",
    ". Ratio effects are analysed on the log scale. Cite the actual calculation and study report; verify the test and interval assumptions against the original methods before publication."
  ],
  [
    "将 log-rank 的 O−E 与 V 换算为效应时，请核对",
    "For log-rank O−E and V conversion, check"
  ],
  [
    "Tierney 等（2007）公式 7",
    "Tierney et al. (2007), equation 7"
  ],
  [
    "Cochrane Handbook 第 6 章，第 6.8.2 节",
    "Cochrane Handbook ch. 6, §6.8.2"
  ],
  [
    "。请核对治疗组符号、事件定义和原始方法，并引用换算方法和研究报告。",
    ". Verify the treatment-arm sign, event definition and original methods before publication, and cite the conversion method and study report."
  ],
  [
    "配对与变化值数据格式，请核对 Borenstein 等（2021）",
    "For paired and change-score formats, check Borenstein et al. (2021),"
  ],
  [
    "，第 2 版第 4 章，第 23–29 页；Cooper、Hedges 与 Valentine 主编（2019）",
    ", 2nd ed., ch. 4, pp. 23–29; Cooper, Hedges & Valentine (eds., 2019),"
  ],
  [
    "，第 11 章，第 211–216 页；以及 Cochrane Handbook 第 6 章和第 23 章。请引用实际使用的方法、假定相关系数的来源及研究报告；撰写前核对原书和设计假设。",
    ", ch. 11, pp. 211–216; and Cochrane Handbook ch. 6 and 23. Cite the method actually used, the source of any assumed correlation, and the study report. Verify the book text and design assumptions before writing."
  ],
  [
    "独立两组 t、精确 p 值、两组 F 或 Cohen d 换算，请核对 Borenstein 等（2021）",
    "For independent-group t, exact p, two-group F or Cohen d conversion, check Borenstein et al. (2021),"
  ],
  [
    "，第 2 版第 4 章，第 24、26–28 页，以及",
    ", 2nd ed., ch. 4, pp. 24 and 26–28, and the"
  ],
  [
    "metafor escalc 的 SMD 文档", "metafor escalc SMD documentation", "Documentation de metafor::escalc pour le SMD", "Документация metafor::escalc для SMD", "Documentación de metafor::escalc para SMD", "metafor escalc SMD documentation", "Documentação de metafor::escalc para SMD", "SMD-Dokumentation zu metafor::escalc", "Документација metafor::escalc за SMD", "metafor escalc SMD documentation"
  ],
  [
    "。请引用换算方法和原始研究报告；发表前核对原书、检验设计和效应方向。",
    ". Cite the conversion method and original study report; verify the book text, test design and effect direction before publication."
  ],
  [
    "单组均值与比例输入，请核对 Harrer 等（2022）",
    "For one-group mean and proportion inputs, check Harrer et al. (2022),"
  ],
  [
    "，第 3.2.1–3.2.2 节（59–61 页）及第 4.2.6 节（132–134 页）。由原文区间换算标准误，请核对",
    ", §§3.2.1–3.2.2 (pp. 59–61) and §4.2.6 (pp. 132–134). For reported CI to SE conversion, check"
  ],
  [
    "。单组发生率，请核对",
    ". For one-group rates, check"
  ],
  [
    "metafor escalc 文档", "metafor escalc documentation", "Documentation de metafor::escalc", "Документация metafor::escalc", "Documentación de metafor::escalc", "metafor escalc documentation", "Documentação de metafor::escalc", "Dokumentation zu metafor::escalc", "Документација metafor::escalc", "metafor escalc documentation"
  ],
  [
    "；计数似然模型，请核对",
    "; for count likelihood models, check"
  ],
  [
    "metafor rma.glmm 文档", "metafor rma.glmm documentation", "Documentation de metafor::rma.glmm", "Документация metafor::rma.glmm", "Documentación de metafor::rma.glmm", "metafor rma.glmm documentation", "Documentação de metafor::rma.glmm", "Dokumentation zu metafor::rma.glmm", "Документација metafor::rma.glmm", "metafor rma.glmm documentation"
  ],
  [
    "。这些格式使用正态逆方差近似；区间及原始尺度精度换算为近似方法，变换后的比例与发生率输入不接受边界值。请引用实际计算方法、研究报告及模型，并核对原文。",
    ". These formats use normal inverse-variance approximations; direct CI and raw-scale precision conversions are approximate, and transformed proportion/rate inputs reject boundary values. Cite the actual calculation, study reports and any model used, and verify the original book or method text before publication."
  ],
  [
    "Cochrane Handbook 第 23 章，第 23.1.4–23.1.5 节",
    "Cochrane Handbook ch. 23, §§23.1.4–23.1.5"
  ],
  [
    "Cochrane Handbook 第 23.3.4 节",
    "Cochrane Handbook §23.3.4"
  ],
  [
    "Cochrane Handbook 第 10.14 节；Harrer 等（2022）",
    "Cochrane Handbook §10.14; Harrer et al. (2022),"
  ],
  [
    "，第 11–12 章及第 17 章。",
    ", chs. 11–12 and 17."
  ],
  [
    "RR、OR、HR 和发生率比要求正的估计值及对数尺度标准误。FISHER_Z 保存 Fisher z，合并后显示相关系数 r。MEAN 使用结局原始单位；LOGIT_PROP 和 LOG_RATE 保存 logit 或对数估计及标准误，再显示比例或发生率。单组分析请在比较组中明确标注，并核对单位、人群与时间点的可比性。",
    "RR/OR/HR/rate ratio require a positive estimate and log-scale SE. FISHER_Z stores Fisher's z and reports pooled correlation r. MEAN uses the outcome unit; LOGIT_PROP and LOG_RATE store logit/log estimates and their SE, then display pooled proportions/rates. Use a one-group label in Comparison and keep units, populations and time points comparable."
  ],
  [
    "研究影响诊断逐研究删除并重新拟合所选模型。请核对 Viechtbauer 与 Cheung（2010），DOI 10.1002/jrsm.11，以及 Harrer 等（2022）",
    "Study influence uses whole-study deletion and refits the selected model. Check Viechtbauer & Cheung (2010), DOI 10.1002/jrsm.11, and Harrer et al. (2022),"
  ],
  [
    "，第 5.4.2 节，第 156–162 页。诊断值较大并不自动构成排除研究的理由；发表前请核对方法和原始报告。",
    ", §5.4.2, pp. 156–162. A large diagnostic does not automatically justify excluding a study; verify method and source reports before publication."
  ],
  [
    "，第 4.2.1/4.4.5、12.2.2、3.7.6 及 4.8.5 节。尚未核实该书是否明确给出了 μ 的 Student-t 先验。",
    ", §§4.2.1/4.4.5, 12.2.2, 3.7.6 and 4.8.5. The book's explicit Student-t prior on μ has not been verified."
  ],
  [
    "，第 4.1 章及第 4.2.1、4.2.3、4.4.4 节（本地 PDF 第 214–215、224–225、228–232、251–253 页）。",
    ", ch. 4.1 and §§4.2.1, 4.2.3, 4.4.4 (local PDF pp. 214–215, 224–225, 228–232, 251–253)."
  ],
  [
    "基础效应量与逆方差合并：Harrer 等（2022）",
    "Core effect sizes and inverse-variance synthesis: Harrer et al. (2022),"
  ],
  [
    "，第 2 版第 11–14 章及第 17 章。SMD 须核对已保存的标准化方式：Hedges g 使用合并标准差及小样本校正（Hedges，1981，DOI 10.3102/10769986006002107）；Glass Δ 使用对照组标准差（Harrer 等，第 4.2.2 节，112–114 页）。随机效应方差估计请引用实际采用的方法：DerSimonian 与 Laird（1986），DOI 10.1016/0197-2456(86)90046-2；Paule 与 Mandel（1982）",
    ", 2nd ed., ch. 11–14, 17. For SMD, check the saved standardizer: Hedges g uses pooled SD and a small-sample correction (Hedges 1981, DOI 10.3102/10769986006002107); Glass Δ uses the control-arm SD (Harrer et al., §4.2.2, pp. 112–114). Cite the selected random-effects estimator: DerSimonian & Laird (1986), DOI 10.1016/0197-2456(86)90046-2; Paule & Mandel (1982),"
  ],
  [
    "87:377–385；或 REML，见 Harrer 等第 4.1.2.1 节，102–103 页、",
    "87:377–385; or REML as described by Harrer et al., §4.1.2.1, pp. 102–103,"
  ],
  [
    "以及 Cochrane Handbook 第 10 章。修正 HKSJ 见 Harrer 第 4 章及 Knapp 与 Hartung（2003），DOI 10.1002/sim.1482。预测区间采用 Borenstein 第 17 章的 k−2 自由度约定。请核对原文，并仅引用实际使用的方法。",
    ", and the Cochrane Handbook, ch. 10. For modified HKSJ, see Harrer ch. 4 and Knapp & Hartung (2003), DOI 10.1002/sim.1482. The prediction interval uses Borenstein ch. 17's k−2 degrees of freedom convention. Check the original texts and cite the methods actually used."
  ],
  [
    "MD 使用各组自身的方差。将已报告区间换算为标准误，假定其在分析尺度上为对称正态区间。请核对参考书和适用条件；引用来源不代表数值与其他软件逐项一致。",
    "MD uses arm-specific variances. Converting a reported CI to SE assumes a symmetric normal interval on the analysis scale. Check the cited books and applicability; a citation does not establish numerical parity with other software."
  ],
  [
    "固定效应 GLS 不估计研究间异质性。2–8 个结局的 REML 估计研究间方差及相关；三个及以上结局要求每对结局至少有三项重叠研究，并拒绝奇异边界拟合。5–8 个结局需要更多计算及足够研究来稳定估计协方差。协方差与 SE² 必须使用相同的分析尺度。",
    "Fixed-effect GLS does not estimate between-study heterogeneity. Two- to eight-outcome REML estimates between-study variances and correlations; three or more outcomes need at least three overlapping studies per outcome pair and reject singular boundary fits. Five to eight outcomes require more computation and sufficient studies for stable covariance estimates. Covariance and SE² use the same analysis scale."
  ],
  [
    "此操作同时运行两个模型。共同概率或发生率模型假定各研究共享同一参数，并使用精确区间；随机截距模型允许 logit 或对数尺度上的研究间差异，使用近似 Wald 区间。运行前请确定报告哪个模型。",
    "This runs both models. The common probability or rate model assumes one value across studies and uses an exact interval. The random-intercept model allows between-study variation on the logit or log scale and uses an approximate Wald interval. Decide which result to report before running."
  ],
  [
    "查看单研究区间：比例、发生率、风险差或发生率比",
    "View study intervals: proportion, rate, risk difference or rate ratio"
  ],
  [
    "使用上方比较组、结局和时间点下已选的原始比例计数。至少需要三项非边界研究，少于十项时应谨慎。此单组比例变体不支持双臂 OR 或发生率。零事件及全事件研究的回归权重为零，并列为排除；不作连续性校正。",
    "Uses selected raw proportion counts from the comparison, outcome and time point above. At least three non-boundary studies are required; fewer than ten warrants caution. This single-proportion variant does not support two-arm OR or incidence rates. Zero-event and all-event studies have zero regression weight and are listed as excluded; no continuity correction is applied."
  ],
  [
    "运行 Peters 单组比例检验",
    "Run Peters single-proportion test"
  ],
  [
    "方法来源：Peters 等（2006），JAMA 295(6):676–680，DOI 10.1001/jama.295.6.676。单组比例扩展遵循 R meta 8.5-0 对 metaprop 对象的 metabias 实现。请核对原文与软件文档，引用实际使用的方法和变体；不对称不能确证发表偏倚。",
    "Method source: Peters et al. (2006), JAMA 295(6):676–680, DOI 10.1001/jama.295.6.676. The single-proportion extension follows R meta 8.5-0 metabias for metaprop objects. Verify the original paper and software documentation, and cite the method and variant used. Asymmetry cannot establish publication bias."
  ],
  [
    "，第 3.2.2、4.2.6 节，第 60–61、132–134 页（logit 比例与二项 logit 广义线性混合模型）；Borenstein 等（2021）",
    ", §§3.2.2, 4.2.6, pp. 60–61, 132–134 (logit proportions and binomial-logit GLMM); Borenstein et al. (2021),"
  ],
  [
    "，第 2 版第 50 章，第 465–468 页（患病率示例）；",
    ", 2nd ed., ch. 50, pp. 465–468 (prevalence example);"
  ],
  [
    "metafor rma.glmm 方法文档", "metafor rma.glmm methods", "Documentation méthodologique de metafor::rma.glmm", "Описание метода metafor::rma.glmm", "Documentación metodológica de metafor::rma.glmm", "metafor rma.glmm methods", "Documentação metodológica de metafor::rma.glmm", "Methodendokumentation zu metafor::rma.glmm", "Опис методе metafor::rma.glmm", "metafor rma.glmm methods"
  ],
  [
    "提供二项与泊松计数似然方法。请引用实际模型及各研究报告；核对原始方法、人群、事件定义和人时单位是否可比。",
    "for binomial and Poisson count likelihoods. Cite the actual model and every source report; check the original method text and whether study populations, event definitions, and person-time units are comparable."
  ],
  [
    "共同概率／发生率分支使用精确区间：Clopper 与 Pearson（1934），DOI 10.1093/biomet/26.4.404；Garwood（1936），DOI 10.1093/biomet/28.3-4.437。随机截距分支在可识别时报告条件性参考组估计及基于观察信息的 Wald 区间；边际均值另行标注。请结合异质性、研究数较少及边界拟合解释。",
    "The common-probability/rate branch uses exact intervals: Clopper & Pearson (1934), DOI 10.1093/biomet/26.4.404; Garwood (1936), DOI 10.1093/biomet/28.3-4.437. The random-intercept branch reports a conditional reference-group estimate and an observed-information Wald interval when identifiable; its marginal mean is separately labeled. Heterogeneity, small study counts and boundary fits require interpretation."
  ],
  [
    "，第 2 版第 42 章，第 369–376 页（MH OR、Peto）；Harrer 等（2022）",
    ", 2nd ed., ch. 42, pp. 369–376 (MH OR, Peto); Harrer et al. (2022),"
  ],
  [
    "，第 4 章，第 115–119 页。Mantel 与 Haenszel（1959），DOI 10.1093/jnci/22.4.719。RR/RD 公式见 Cochrane RevMan 5 统计算法。",
    ", ch. 4, pp. 115–119. Mantel & Haenszel (1959), DOI 10.1093/jnci/22.4.719. RR/RD formulas: Cochrane RevMan 5 statistical algorithms."
  ],
  [
    "配对观测请使用",
    "For matched observations, use the"
  ],
  [
    "配对表区间查看器",
    "paired table interval viewer"
  ],
  [
    "输入一项研究的计数。自然尺度区间用于描述该研究，并非合并治疗效应；不能反推标准误后用于逆方差合并。",
    "Enter counts from one study. These natural-scale intervals describe that study; they are not pooled treatment effects and must not be converted into an SE for inverse-variance pooling."
  ],
  [
    "比例（事件数／人数）",
    "Proportion (events / people)"
  ],
  [
    "发生率（事件数／人时）",
    "Rate (events / person-time)"
  ],
  [
    "风险差（独立组）",
    "Risk difference (independent groups)"
  ],
  [
    "发生率比（独立泊松计数）",
    "Rate ratio (independent Poisson counts)"
  ],
  [
    "请先选择统计量",
    "Choose quantity first"
  ],
  [
    "人数或人时",
    "People or person-time"
  ],
  [
    "对照组总人数",
    "Control total people"
  ],
  [
    "仅用于独立组。方向为治疗组风险减去对照组风险；正值表示治疗组事件更多，不一定有益。Newcombe 方法 10 使用未作连续性校正的 Wilson 界限，属于近似而非精确区间。接近零事件或全事件时 Wald 可能低估不确定性；两组方差均为零时拒绝计算。",
    "Independent groups only. Direction is treatment risk minus control risk; a positive value means more events under treatment, not necessarily benefit. Newcombe method 10 uses Wilson limits without continuity correction; it is an approximation, not an exact interval. Wald can understate uncertainty near zero or all events and is rejected when both arm variances are zero."
  ],
  [
    "用于独立泊松计数，两组须使用相同人时单位。发生率比 =（治疗组事件数／治疗组人时）÷（对照组事件数／对照组人时）。精确区间偏保守；中 P 区间覆盖率可能低于标称水平。单组零事件可产生 0 或 ∞；两组均零事件不提供发生率比信息，拒绝计算。JSON 中无限界限记录为 \"Infinity\" 或 \"-Infinity\"，而不是缺失值。",
    "Independent Poisson counts; use the same person-time unit for both arms. IRR = (treatment events / treatment person-time) ÷ (control events / control person-time). Exact intervals are conservative; mid-P intervals can have coverage below the nominal level. A single zero-event arm can produce 0 or ∞; two zero-event arms provide no rate-ratio information and are rejected. JSON records encode infinite bounds as \"Infinity\" or \"-Infinity\", never as missing values."
  ],
  [
    "研究报告及页码／表格（写入导出记录）",
    "Study report and page / table (for the exported record)"
  ],
  [
    "需要保守覆盖率时，可考虑 Clopper–Pearson（比例）或 Garwood（发生率）。Wilson、中 P 和 Byar 的覆盖率可能低于标称水平。Jeffreys 报告未修正的贝叶斯等尾可信区间，其边界不必包含 0 或 1。",
    "For conservative coverage, consider Clopper–Pearson (proportions) or Garwood (rates). Wilson, mid-P and Byar can have coverage below the nominal level. Jeffreys reports an unmodified Bayesian equal-tailed credible interval; its boundary limits need not contain 0 or 1."
  ],
  [
    "计算单研究区间",
    "Calculate study interval"
  ],
  [
    "导出区间记录（JSON）",
    "Export interval record (JSON)"
  ],
  [
    "方法来源随结果及导出记录提供。发表时请核对原始论文或书籍，并引用实际使用的方法和研究报告。此查看器不会向合并分析数据集保存效应量。",
    "Method sources appear with the result and export. Check the original paper or book, and cite the method actually used plus the study report when publishing. This viewer does not save an effect to the synthesis dataset."
  ],
  [
    "漏斗图与 Egger 选项请参见 Harrer 等（2022）",
    "For funnel-plot and Egger options: Harrer et al. (2022),"
  ],
  [
    "，第 9 章，第 242–244 页；Egger 等（1997），DOI 10.1136/bmj.315.7109.629。请引用实际使用的方法。",
    ", ch. 9, pp. 242–244; Egger et al. (1997), DOI 10.1136/bmj.315.7109.629. Cite the method actually used."
  ],
  [
    "，第 2 版第 22 章。Knapp 与 Hartung（2003），DOI 10.1002/sim.1482。",
    ", 2nd ed., ch. 22. Knapp & Hartung (2003), DOI 10.1002/sim.1482."
  ],
  [
    "，第 9 章，第 242–244 页。原始方法：Begg 与 Mazumdar（1994），DOI 10.2307/2533446；Rosenthal（1979），DOI 10.1037/0033-2909.86.3.638；Orwin（1983），DOI 10.3102/10769986008002157；Duval 与 Tweedie（2000），DOI 10.1111/j.0006-341X.2000.00455.x。仅引用实际使用的方法。剪补法属于敏感性分析。",
    ", ch. 9, pp. 242–244. Original methods: Begg & Mazumdar (1994), DOI 10.2307/2533446; Rosenthal (1979), DOI 10.1037/0033-2909.86.3.638; Orwin (1983), DOI 10.3102/10769986008002157; Duval & Tweedie (2000), DOI 10.1111/j.0006-341X.2000.00455.x. Cite only methods actually used. Trim-and-fill is a sensitivity analysis."
  ],
  [
    "Cochrane Handbook 第 11 章，第 11.4.3 节；Harrer 等（2022）",
    "Cochrane Handbook, Chapter 11, §11.4.3; Harrer et al. (2022),"
  ],
  [
    "，第 12 章，第 12.2.1 节，第 336–338 页（对比网络模型及随机效应扩展），342–344 页（设计间 Q）；Hedges（2019），载于",
    ", ch. 12, §12.2.1, pp. 336–338 (contrast network model and random-effects extension), pp. 342–344 (between-design Q); Hedges (2019), in"
  ],
  [
    "，第 13 章，第 13.1.1 节，第 282–283 页（相依比较）。协方差感知路径需要提供研究内协方差。REML 假定各组偏差独立、方差相同，且共享一个 τ²。独立的设计间 Q 检验仅采用固定效应；两种分析均不检验可传递性。请核对原书、模型假设和研究报告，并引用实际方法。",
    ", ch. 13, §13.1.1, pp. 282–283 (dependent comparisons). The covariance-aware path requires supplied within-study covariance. REML assumes independent equal-variance arm deviations and one common τ². The separate between-design Q test is fixed-effect only; neither analysis checks transitivity. Verify the book text, model assumptions and source reports; cite the methods actually used."
  ],
  [
    "直接／间接对比分解会从间接网络中排除整项直接研究的数据块。随机效应为两个部分分别估计 REML τ²，不同于 netmeta 的共同网络 τ² 约定。请核对 Harrer 等（2022）第 12 章，352–353 页，以及 Dias 等（2010），DOI 10.1002/sim.3767，并引用方法和研究报告。不一致性 p 值不能评估可传递性；请检查效应修饰变量。",
    "The direct/indirect contrast split excludes entire direct-study blocks from the indirect network. Random effects estimates separate REML τ² values for each partition, unlike netmeta's common network τ² convention. Check Harrer et al. (2022), ch. 12, pp. 352–353, and Dias et al. (2010), DOI 10.1002/sim.3767. Verify the original sources and cite the method and study reports. A disagreement p-value does not assess transitivity; check effect modifiers."
  ],
  [
    "，第 4.1.2.1–2 节，第 102–104 页（方差估计与 HKSJ 区间）。",
    ", §§4.1.2.1–2, pp. 102–104 (variance estimators and HKSJ intervals)."
  ],
  [
    "相关线性剂量趋势的 GLS 见 Greenland 与 Longnecker（1992），DOI 10.1093/oxfordjournals.aje.a116237。Cooper 等（2019）",
    "For GLS of correlated linear dose trends, see Greenland & Longnecker (1992), DOI 10.1093/oxfordjournals.aje.a116237. Cooper et al. (2019),"
  ],
  [
    "，第 3 版第 13 章，第 282–284 页讨论研究内相依效应；Grant 与 Di Tanna（2025）",
    ", 3rd ed., ch. 13, pp. 282–284 discusses dependent effects within studies; Grant & Di Tanna (2025),"
  ],
  [
    "，第 17.1 节仅提供剂量分析背景。本模块需要正确尺度上的协方差矩阵，并拟合两阶段线性斜率；这些书籍并未校准这一确切组合。",
    ", §17.1 provides dose-analysis background only. This module requires a covariance matrix on the correct scale and fits a two-stage linear slope; the books do not calibrate this exact combination."
  ],
  [
    "Borenstein 等（2021）《Introduction to Meta-Analysis》第 2 版第 39 章，第 351–355 页提供个体参与者数据的概念指导。请核对原文，并引用实际计算方法和研究报告。",
    "Borenstein et al. (2021), Introduction to Meta-Analysis, 2nd ed., Chapter 39, pp. 351–355 provides conceptual IPD guidance. Check the original text; cite the calculation methods and study reports used."
  ],
  [
    "survival::coxph 方法文档", "survival::coxph method documentation", "Documentation de la méthode survival::coxph", "Документация метода survival::coxph", "Documentación del método survival::coxph", "survival::coxph method documentation", "Documentação do método survival::coxph", "Methodendokumentation zu survival::coxph", "Документација методе survival::coxph", "survival::coxph method documentation"
  ],
  [
    "。该书提供概念指导，并未验证这一具体个体参与者数据模型。",
    ". The book provides conceptual guidance but does not validate this exact IPD fit."
  ],
  [
    "，第 2 版第 39 章，第 354–355 页；Hua 等（2017），DOI 10.1002/sim.7171，说明了为何须区分研究内与跨研究的治疗交互。请核对原始方法和研究报告。",
    ", 2nd ed., ch. 39, pp. 354–355; Hua et al. (2017), DOI 10.1002/sim.7171, explains why within- and across-study treatment interactions must be separated. Verify the original methods and study reports."
  ],
  [
    "领域及整体判断由评价者录入。本表单不执行官方信号问题算法。",
    "Domain and overall judgements are entered by the reviewer. This form does not implement the official signalling-question algorithm."
  ],
  [
    "使用当前分析的比较组、结局、时间点和效应指标，以及当前评价者保存的整体判断。选择要排除的标签；完整与限制样本使用相同模型。每项研究存在多个已保存结果变体时，尚无法明确对应偏倚风险评价，故拒绝分析。",
    "Uses the current analysis comparison, outcome, time point and measure, plus this reviewer's recorded overall judgments. Choose labels to exclude; the original and restricted analyses use the same model. Results with multiple saved variants per study cannot yet be linked unambiguously to a RoB assessment and are rejected."
  ],
  [
    "选择合并模型",
    "Choose synthesis model"
  ],
  [
    "随机效应（DL）",
    "Random effects (DL)"
  ],
  [
    "随机效应（PM）",
    "Random effects (PM)"
  ],
  [
    "随机效应（REML）",
    "Random effects (REML)"
  ],
  [
    "选择区间方法",
    "Choose interval method"
  ],
  [
    "比较完整样本与排除后样本",
    "Compare all vs excluded judgments"
  ],
  [
    "偏倚风险判断不是数值化质量权重。偏倚评价与敏感性分析请核对 Cochrane Handbook 第 8、10 章；引用评价框架、合并方法和研究报告。排除研究会改变分析样本，不能据此确证偏倚导致了结果。",
    "Risk-of-bias judgments are not numeric quality weights. See Cochrane Handbook ch. 8 and ch. 10 for risk-of-bias assessment and sensitivity analysis; verify the original guidance and cite the assessment framework, synthesis method and study reports. Exclusion changes the analysis set and cannot establish that bias caused a result."
  ],
  [
    "双变量合并",
    "Bivariate synthesis"
  ],
  [
    "导出诊断图 SVG",
    "Export diagnostic plot SVG"
  ],
  [
    "阳性和阴性似然比由同一拟合的敏感度与特异度推导。近似 95% 区间使用拟合均值参数的协方差，包括其相关。研究间异质性不能替代估计协方差。",
    "LR+ and LR− are derived from the same fitted sensitivity and specificity. The approximate 95% intervals use the covariance of the fitted mean parameters, including their correlation. Between-study heterogeneity is not substituted for estimation covariance."
  ],
  [
    "导出似然比 JSON",
    "Export likelihood-ratio JSON"
  ],
  [
    "似然比须结合具体场景的检验前概率，才能计算检验后概率（Fagan 列线图／贝叶斯公式）。本面板仅报告比值。请核对 Cochrane DTA Handbook 1.0 版（2010）第 10 章，第 10.2.3.3、10.4.2、10.5.2 节，并在发表时引用方法。",
    "Likelihood ratios require a setting-specific pre-test probability to calculate post-test probability (Fagan nomogram / Bayes). This panel reports ratios only. Check Cochrane DTA Handbook v1.0 (2010), ch. 10, §§10.2.3.3, 10.4.2 and 10.5.2; verify the original methods and cite them when publishing."
  ],
  [
    "仅供探索性比较：分别合并两种似然比会忽略相关，可能产生不相容的汇总。优先采用上方双变量汇总点推导的似然比。请明确指定当前检验、目标疾病、阈值及参照标准。",
    "Exploratory comparison only: separate LR pooling ignores their correlation and can produce incompatible summaries. Prefer LR derived at the bivariate summary point above. Uses the selected index test, target condition, threshold and reference standard; specify all four."
  ],
  [
    "固定效应逆方差",
    "Fixed inverse variance"
  ],
  [
    "随机效应（DerSimonian–Laird）",
    "Random effects (DerSimonian–Laird)"
  ],
  [
    "选择零单元格处理",
    "Choose zero-cell policy"
  ],
  [
    "排除含零单元格的表",
    "Exclude tables containing a zero"
  ],
  [
    "仅对含零单元格的表，四格均加 0.5",
    "Add 0.5 to all four cells of zero-containing tables only"
  ],
  [
    "置信水平（%）",
    "Confidence level (%)"
  ],
  [
    "比较研究似然比",
    "Compare study likelihood ratios"
  ],
  [
    "导出探索性似然比审计（JSON）",
    "Export exploratory LR audit (JSON)"
  ],
  [
    "这是拟合双变量模型的等价参数化：每项研究一个阈值，不含协变量。不重新拟合，也不额外估计不确定性。形状参数 β = 0 对应对称的 SROC。",
    "Equivalent parameterization of the fitted bivariate model: one threshold per study, without covariates. No refitting or additional uncertainty estimation. Shape β = 0 corresponds to a symmetric SROC."
  ],
  [
    "请核对 Cochrane Handbook for Systematic Reviews of Diagnostic Test Accuracy 1.0 版（2010）第 10 章，第 10.5.2.3 节，26–27 页。解释前核对符号及版本；发表时引用模型、变换和研究报告。",
    "Check the original Cochrane Handbook for Systematic Reviews of Diagnostic Test Accuracy, version 1.0 (2010), ch. 10, §10.5.2.3, pp. 26–27. Verify notation and edition before interpretation; cite the model, transformation and study reports when publishing."
  ],
  [
    "至少使用十项独立研究，每项一个可比数值阈值，且至少三个不同阈值。上方检验、目标疾病及参照标准定义分析范围。某研究有多个已存阈值时，可在下方映射中指定唯一阈值。阈值单位和方向须一致。",
    "Use at least ten independent studies with one comparable numeric threshold each and at least three distinct values. The index test, target condition and reference standard above define the analysis. If a study has multiple saved thresholds, choose exactly one in the optional mapping below. Threshold units and direction must match."
  ],
  [
    "可选：以研究编号为键的阈值选择 JSON",
    "Optional threshold choice JSON keyed by study ID"
  ],
  [
    "运行探索性阈值元回归",
    "Run exploratory threshold meta-regression"
  ],
  [
    "此研究层面的双变量模型估计 logit 敏感度和特异度尺度上的关联；不能证明仅改变阈值就造成观察到的差异。请核对 Cochrane",
    "This bivariate study-level model estimates associations on logit sensitivity and specificity scales; it does not show that changing threshold alone causes the observed difference. Check the Cochrane"
  ],
  [
    "，第 10 章，第 10.1.4.2 与 10.5.3.1 节，并核对原始研究报告。Borenstein 等（2021）第 49 章及 Cooper 等（2019）第 21 章仅提供诊断准确性范围或阅读线索，不是本模型的校准依据。发表时引用诊断模型和数据来源。",
    ", ch. 10, §§10.1.4.2 and 10.5.3.1, and verify the original source reports. Borenstein et al. (2021), ch. 49 and Cooper et al. (2019), ch. 21 provide DTA scope or reading leads, not calibration for this model. Cite the DTA model and data sources when publishing."
  ],
  [
    "双变量二项 logit 正态随机效应模型见 Chu 与 Cole（2006），DOI 10.1016/j.jclinepi.2006.06.011，以及 Cochrane",
    "For the bivariate binomial-logit normal random-effects model, see Chu & Cole (2006), DOI 10.1016/j.jclinepi.2006.06.011, and the Cochrane"
  ],
  [
    "，第 10 章。Borenstein（2021）第 49 章及 Cooper 等（2019）第 21 章仅提供范围或延伸阅读，未校准本模块的 11 点求积或 Wald 区间。",
    ", ch. 10. Borenstein (2021), ch. 49 and Cooper et al. (2019), ch. 21 provide scope or further-reading leads only; they do not calibrate this module's 11-point quadrature or Wald intervals."
  ],
  [
    "记录研究发现、原文引语及来源定位，再将有依据的发现归类并形成综合发现。",
    "Record findings with their source quotation and locator, then group supported findings into categories and synthesized findings."
  ],
  [
    "综合类别",
    "Synthesize categories"
  ],
  [
    "分析工作区",
    "Analysis workspace"
  ],
  [
    "分析目录",
    "Analysis directory"
  ],
  [
    "查找分析方法", "Find an analysis", "Rechercher une analyse", "Поиск метода анализа", "Buscar un análisis", "解析方法を検索", "Buscar uma análise", "Analyseverfahren suchen", "Потражи метод анализе", "분석 방법 찾기"
  ],
  [
    "未找到匹配的方法",
    "No matching methods"
  ],
  [
    "合并分析",
    "Synthesis"
  ],
  [
    "合并结果",
    "Pooled results"
  ],
  [
    "效应量与合并分析",
    "Effect sizes and synthesis"
  ],
  [
    "结果",
    "Results"
  ],
  [
    "模型与区间比较",
    "Model & interval comparison"
  ],
  [
    "研究诊断",
    "Study diagnostics"
  ],
  [
    "方法与引用",
    "Methods & citations"
  ],
  [
    "当前结果", "Current result", "Résultat actuel", "Текущий результат", "Resultado actual", "現在の結果", "Resultado atual", "Aktuelles Ergebnis", "Тренутни резултат", "현재 결과"
  ],
  [
    "上次运行结果", "Previous result", "Résultat de l’exécution précédente", "Результат предыдущего запуска", "Resultado de la ejecución anterior", "前回の実行結果", "Resultado da execução anterior", "Ergebnis des vorherigen Laufs", "Резултат претходног покретања", "이전 실행 결과"
  ],
  [
    "运行记录", "Run record", "Historique des exécutions", "Запись о запуске", "Historial de ejecuciones", "実行記録", "Histórico de execuções", "Laufprotokoll", "Запис о покретању", "실행 기록"
  ],
  [
    "未运行",
    "Not run"
  ],
  [
    "等待运行",
    "Ready to analyse"
  ],
  [
    "先确认分析范围和模型，再运行合并分析。",
    "Confirm the analysis scope and model, then run the synthesis."
  ],
  [
    "结果将显示合并效应、区间和森林图。此处不会生成示例数据。",
    "The result will show the pooled effect, interval and forest plot. No sample data are generated here."
  ],
  [
    "模型配置",
    "Model configuration"
  ],
  [
    "已应用的模型", "Applied model", "Modèle appliqué", "Применённая модель", "Modelo aplicado", "適用済みモデル", "Modelo aplicado", "Angewandtes Modell", "Примењени модел", "적용된 모형"
  ],
  [
    "编辑模型",
    "Edit model"
  ],
  [
    "应用设置",
    "Apply settings"
  ],
  [
    "取消",
    "Cancel"
  ],
  [
    "取消修改",
    "Discard changes"
  ],
  [
    "返回结果", "Back to result", "Retour au résultat", "Вернуться к результату", "Volver al resultado", "結果に戻る", "Voltar ao resultado", "Zurück zum Ergebnis", "Назад на резултат", "결과로 돌아가기"
  ],
  [
    "未应用的修改", "Unapplied changes", "Modifications non appliquées", "Не применённые изменения", "Cambios sin aplicar", "未適用の変更", "Alterações não aplicadas", "Nicht angewandte Änderungen", "Непримењене измене", "적용되지 않은 변경사항"
  ],
  [
    "配置草稿", "Configuration draft", "Brouillon de configuration", "Черновик конфигурации", "Borrador de configuración", "設定の下書き", "Rascunho da configuração", "Konfigurationsentwurf", "Нацрт конфигурације", "설정 초안"
  ],
  [
    "修改仅在点击“应用设置”后生效。",
    "Changes take effect only after you choose Apply settings."
  ],
  [
    "应用设置不会自动运行分析。", "Applying settings does not run the analysis.", "L’application des paramètres ne lance pas l’analyse.", "Применение настроек не запускает анализ.", "Aplicar la configuración no ejecuta el análisis.", "設定を適用しても解析は実行されません。", "Aplicar as configurações não executa a análise.", "Einstellungen werden angewendet, ohne die Analyse auszuführen.", "Примена подешавања не покреће анализу.", "설정을 적용해도 분석은 실행되지 않습니다."
  ],
  [
    "未应用的模型修改已保留。", "Your unapplied model changes have been retained.", "Les modifications non appliquées au modèle ont été conservées.", "Неприменённые изменения модели сохранены.", "Se conservaron los cambios del modelo que aún no se habían aplicado.", "未適用のモデル変更は保持されています。", "As alterações do modelo que não foram aplicadas foram mantidas.", "Ihre noch nicht angewendeten Modelländerungen wurden beibehalten.", "Непримењене измене модела су сачуване.", "적용하지 않은 모델 변경사항이 유지되었습니다."
  ],
  [
    "设置已更新，以下仍为上次运行结果。",
    "Settings have changed. The result below is from the previous run."
  ],
  [
    "重新运行",
    "Run again"
  ],
  [
    "运行合并分析",
    "Run synthesis"
  ],
  [
    "运行分析",
    "Run analysis"
  ],
  [
    "正在计算", "Running", "Calcul en cours", "Выполняется", "En ejecución", "実行中", "Em execução", "Wird ausgeführt", "Израчунавање", "실행 중"
  ],
  [
    "运行失败", "Run failed", "Échec de l’exécution", "Сбой запуска", "Error en la ejecución", "実行に失敗しました", "Falha na execução", "Ausführung fehlgeschlagen", "Покретање није успело", "실행 실패"
  ],
  [
    "导出", "Export", "Exporter", "Экспорт", "Exportar", "エクスポート", "Exportar", "Export", "Извоз", "내보내기"
  ],
  [
    "图表与报告",
    "Figure & reports"
  ],
  [
    "数据与审计记录",
    "Data & audit records"
  ],
  [
    "导出当前森林图",
    "Export current forest plot"
  ],
  [
    "报告会按已应用的设置重新计算。",
    "Reports are recomputed using the applied settings."
  ],
  [
    "重新计算并导出 Word",
    "Recompute & export Word"
  ],
  [
    "重新计算并导出 PowerPoint",
    "Recompute & export PowerPoint"
  ],
  [
    "重新计算并导出 LaTeX",
    "Recompute & export LaTeX"
  ],
  [
    "导出合并审计 CSV",
    "Export synthesis audit CSV"
  ],
  [
    "森林图",
    "Forest plot"
  ],
  [
    "显示设置",
    "Display settings"
  ],
  [
    "图形样式", "Figure appearance", "Apparence de la figure", "Внешний вид рисунка", "Aspecto de la figura", "図の表示", "Aparência da figura", "Darstellung der Abbildung", "Изглед слике", "그림 모양"
  ],
  [
    "完成",
    "Done"
  ],
  [
    "显示设置立即应用，不改变统计模型。", "Display changes apply immediately and do not change the statistical model.", "Les paramètres d’affichage s’appliquent immédiatement et ne modifient pas le modèle statistique.", "Изменения отображения применяются сразу и не меняют статистическую модель.", "Los cambios de visualización se aplican de inmediato y no modifican el modelo estadístico.", "表示設定はすぐに反映され、統計モデルは変更されません。", "As alterações de exibição são aplicadas imediatamente e não alteram o modelo estatístico.", "Änderungen an der Anzeige werden sofort übernommen und ändern das statistische Modell nicht.", "Измене приказа се примењују одмах и не мењају статистички модел.", "표시 설정은 즉시 적용되며 통계 모형은 변경되지 않습니다."
  ],
  [
    "纳入研究", "Included studies", "Études incluses", "Включённые исследования", "Estudios incluidos", "採用研究", "Estudos incluídos", "Eingeschlossene Studien", "Укључене студије", "포함된 연구"
  ],
  [
    "所选数据", "Selected data", "Données sélectionnées", "Выбранные данные", "Datos seleccionados", "選択したデータ", "Dados selecionados", "Ausgewählte Daten", "Изабрани подаци", "선택한 자료"
  ],
  [
    "分析范围",
    "Analysis scope"
  ],
  [
    "比较组",
    "Comparison"
  ],
  [
    "结局",
    "Outcome"
  ],
  [
    "时间点",
    "Time point"
  ],
  [
    "效应指标",
    "Effect measure"
  ],
  [
    "研究（仅用于录入）",
    "Study (data entry only)"
  ],
  [
    "当前范围按比较组、结局、时间点和效应指标筛选，不按单个研究筛选。",
    "The analysis is filtered by comparison, outcome, time point and effect measure, not by the entry study selector."
  ],
  [
    "合并效应",
    "Pooled effect"
  ],
  [
    "95% 置信区间",
    "95% confidence interval"
  ],
  [
    "研究间方差", "Between-study variance", "Variance inter-études", "Межисследовательская дисперсия", "Varianza entre estudios", "研究間分散", "Variância entre estudos", "Zwischenstudienvarianz", "Варијанса између студија", "연구 간 분산"
  ],
  [
    "预测区间",
    "Prediction interval"
  ],
  [
    "异质性说明",
    "Heterogeneity details"
  ],
  [
    "方法依据", "Method basis", "Fondement méthodologique", "Методологическая основа", "Base metodológica", "方法の根拠", "Base metodológica", "Methodische Grundlage", "Методолошка основа", "방법 근거"
  ],
  [
    "本次实际采用的方法",
    "Methods used in this run"
  ],
  [
    "尚未运行；以下为候选方法说明。",
    "Not run yet; the following describes the available methods."
  ],
  [
    "参考方法说明",
    "Reference methods"
  ],
  [
    "查看原始计算记录",
    "View original computation record"
  ],
  [
    "技术记录（原始字段）", "Technical record (original fields)", "Enregistrement technique (champs d’origine)", "Техническая запись (исходные поля)", "Registro técnico (campos originales)", "技術記録（元のフィールド）", "Registro técnico (campos originais)", "Technischer Datensatz (Originalfelder)", "Технички запис (изворна поља)", "기술 기록(원래 필드)"
  ],
  [
    "原始字段保留用于核查，不作为界面文案翻译。",
    "Original fields are preserved for auditing, not translated as interface text."
  ],
  [
    "所选研究及来源：", "Selected studies and sources:", "Études et sources sélectionnées :", "Выбранные исследования и источники:", "Estudios y fuentes seleccionados:", "選択した研究と出典：", "Estudos e fontes selecionados:", "Ausgewählte Studien und Quellen:", "Изабране студије и извори:", "선택한 연구 및 출처:"
  ],
  [
    "所选研究与来源",
    "Selected studies & sources"
  ],
  [
    "方法说明与限制", "Methods and limitations", "Méthodes et limites", "Методы и ограничения", "Métodos y limitaciones", "方法と限界", "Métodos e limitações", "Methoden und Einschränkungen", "Методе и ограничења", "방법 및 한계"
  ],
  [
    "复制方法说明",
    "Copy method notes"
  ],
  [
    "已复制", "Copied", "Copié", "Скопировано", "Copiado", "コピーしました", "Copiado", "Kopiert", "Копирано", "복사됨"
  ],
  [
    "复制失败，请手动选择文本。", "Copy failed. Select the text to copy it manually.", "Échec de la copie. Sélectionnez le texte manuellement.", "Не удалось скопировать. Выделите текст и скопируйте его вручную.", "No se pudo copiar. Seleccione el texto manualmente.", "コピーできませんでした。手動でテキストを選択してください。", "Não foi possível copiar. Selecione o texto manualmente.", "Kopieren fehlgeschlagen. Markieren Sie den Text, um ihn manuell zu kopieren.", "Копирање није успело. Изаберите текст да бисте га копирали ручно.", "복사하지 못했습니다. 텍스트를 직접 선택하세요."
  ],
  [
    "候选方法参考", "Candidate method references", "Références des méthodes envisagées", "Возможные ссылки на методы", "Referencias de métodos candidatos", "候補となる方法の参考文献", "Referências de métodos candidatos", "Kandidaten für Methodenreferenzen", "Кандидатске референце метода", "후보 방법 참고문헌"
  ],
  [
    "查看完整来源", "View full sources", "Afficher toutes les informations sur la source", "Показать полные сведения об источнике", "Ver toda la información de la fuente", "出典情報をすべて表示", "Ver todas as informações da fonte", "Vollständige Quellenangaben anzeigen", "Прикажи све податке о извору", "전체 출처 정보 보기"
  ],
  [
    "研究间方差估计比较",
    "Compare heterogeneity estimators"
  ],
  [
    "比较 DL、PM 和 REML",
    "Compare DL, PM and REML"
  ],
  [
    "τ² 区间",
    "τ² intervals"
  ],
  [
    "计算 τ² 的 95% REML 轮廓区间",
    "Calculate the 95% REML profile interval for τ²"
  ],
  [
    "效应与区间方法",
    "Effect & interval methods"
  ],
  [
    "比较模型与区间方法的敏感性",
    "Compare model and interval sensitivity"
  ],
  [
    "运行方法比较", "Run method comparison", "Lancer la comparaison des méthodes", "Сравнить методы", "Ejecutar la comparación de métodos", "方法の比較を実行", "Executar a comparação de métodos", "Methodenvergleich ausführen", "Покрени поређење метода", "방법 비교 실행"
  ],
  [
    "敏感性分析应依据预先规定的比较，不根据显著性挑选结果。",
    "Use prespecified sensitivity comparisons; do not select a result based on significance."
  ],
  [
    "留一法分析",
    "Leave-one-study-out"
  ],
  [
    "研究影响诊断",
    "Study influence diagnostics"
  ],
  [
    "逐研究删除并重新拟合，检查单项研究对结果的影响。",
    "Delete one study at a time and refit to examine its influence."
  ],
  [
    "诊断值较大不是自动排除研究的理由。",
    "A large diagnostic value is not an automatic reason to exclude a study."
  ],
  [
    "贝叶斯随机效应合并",
    "Bayesian random-effects synthesis"
  ],
  [
    "贝叶斯先验敏感性",
    "Bayesian prior sensitivity"
  ],
  [
    "查看", "View", "Afficher", "Показать", "Ver", "表示", "Ver", "Anzeigen", "Прикажи", "보기"
  ],
  [
    "收起", "Hide", "Masquer", "Скрыть", "Ocultar", "非表示", "Ocultar", "Ausblenden", "Сакриј", "숨기기"
  ],
  ["查看原始信息", "View original message", "Afficher le message original", "Показать исходное сообщение", "Ver mensaje original", "元のメッセージを表示", "Ver mensagem original", "Originalnachricht anzeigen", "Прикажи изворну поруку", "원본 메시지 보기"],
  [
    "方法返回了补充说明；请展开核对原文。", "The method returned additional information. Expand to review the original text.", "La méthode a renvoyé des informations complémentaires ; développez la section pour consulter le texte original.", "Метод вернул дополнительную информацию. Разверните подробности, чтобы проверить исходный текст.", "El método devolvió información adicional; despliegue la sección para consultar el texto original.", "方法から補足情報が返されました。展開して原文を確認してください。", "O método retornou informações adicionais; expanda a seção para consultar o texto original.", "Die Methode hat zusätzliche Informationen zurückgegeben. Öffnen Sie die Details, um den Originaltext zu prüfen.", "Метода је вратила додатне информације. Проширите детаље да бисте проверили изворни текст.", "방법에서 추가 설명을 반환했습니다. 펼쳐서 원문을 확인하세요."
  ],
  ["请求未完成，请检查输入或展开错误详情。", "The request could not be completed. Check your inputs or expand the error details.", "La demande n’a pas abouti. Vérifiez les données saisies ou développez les détails de l’erreur.", "Не удалось выполнить запрос. Проверьте введённые данные или откройте подробности ошибки.", "No se pudo completar la solicitud. Revise los datos o despliegue los detalles del error.", "リクエストを完了できませんでした。入力内容を確認するか、エラーの詳細を開いてください。", "Não foi possível concluir a solicitação. Confira os dados ou expanda os detalhes do erro.", "Die Anfrage konnte nicht abgeschlossen werden. Prüfen Sie Ihre Eingaben oder öffnen Sie die Fehlerdetails.", "Захтев није довршен. Проверите унос или отворите детаље грешке.", "요청을 완료하지 못했습니다. 입력을 확인하거나 오류 세부 정보를 펼치세요."],
  [
    "固定效应 · 共同效应",
    "Fixed effect · common effect"
  ],
  [
    "随机效应 · DL",
    "Random effects · DL"
  ],
  [
    "随机效应 · PM",
    "Random effects · PM"
  ],
  [
    "随机效应 · REML",
    "Random effects · REML"
  ],
  [
    "正态 / Wald · 95%",
    "Normal / Wald · 95%"
  ],
  [
    "修正 HKSJ · 95%（随机效应）",
    "Modified HKSJ · 95% (random effects)"
  ],
  [
    "请先选择合并模型。", "Choose a synthesis model before running this analysis.", "Sélectionnez un modèle de synthèse avant de lancer cette analyse.", "Перед запуском анализа выберите модель синтеза.", "Seleccione un modelo de síntesis antes de ejecutar este análisis.", "解析を実行する前に統合モデルを選択してください。", "Selecione um modelo de síntese antes de executar esta análise.", "Wählen Sie vor der Analyse ein Synthesemodell aus.", "Изаберите модел синтезе пре покретања анализе.", "분석을 실행하기 전에 통합 모형을 선택하세요."
  ],
  [
    "请先选择置信区间方法。", "Choose a confidence interval method before running this analysis.", "Sélectionnez une méthode d’intervalle de confiance avant de lancer cette analyse.", "Перед запуском анализа выберите метод построения доверительного интервала.", "Seleccione un método de intervalo de confianza antes de ejecutar este análisis.", "先に信頼区間の方法を選択してください。", "Selecione um método de intervalo de confiança antes de executar esta análise.", "Wählen Sie vor der Analyse ein Konfidenzintervallverfahren aus.", "Изаберите методу за интервал поверења пре покретања анализе.", "먼저 신뢰구간 방법을 선택하세요."
  ],
  [
    "请先选择效应指标。", "Choose an effect measure before running this analysis.", "Sélectionnez une mesure d’effet avant de lancer cette analyse.", "Перед запуском анализа выберите показатель эффекта.", "Seleccione una medida del efecto antes de ejecutar este análisis.", "先に効果指標を選択してください。", "Selecione uma medida de efeito antes de executar esta análise.", "Wählen Sie vor der Analyse ein Effektmaß aus.", "Изаберите меру ефекта пре покретања анализе.", "먼저 효과 지표를 선택하세요."
  ],
  [
    "修正 HKSJ 仅用于随机效应模型。", "Modified HKSJ requires a random-effects model.", "La méthode HKSJ modifiée nécessite un modèle à effets aléatoires.", "Модифицированный HKSJ применим только к модели случайных эффектов.", "El método HKSJ modificado requiere un modelo de efectos aleatorios.", "修正 HKSJ はランダム効果モデルでのみ使用できます。", "O método HKSJ modificado requer um modelo de efeitos aleatórios.", "Modifiziertes HKSJ ist nur für ein Random-Effects-Modell verfügbar.", "Измењени HKSJ примењује се само у моделу случајних ефеката.", "수정 HKSJ는 랜덤효과 모형에서만 사용할 수 있습니다."
  ],
  [
    "当前没有可用于合并的效应指标。", "No synthesis-ready effect measures are available.", "Aucune mesure d’effet ne peut être incluse dans une synthèse pour le moment.", "Нет показателей эффекта, доступных для синтеза.", "No hay medidas del efecto disponibles para la síntesis.", "統合解析に使用できる効果指標がありません。", "Não há medidas de efeito disponíveis para a síntese.", "Es sind keine Effektmaße für die Synthese verfügbar.", "Нема мера ефекта доступних за синтезу.", "통합 분석에 사용할 수 있는 효과 지표가 없습니다."
  ],
  [
    "比较期间所选结果发生变化，请重新运行。", "Selected results changed during comparison; run it again.", "Les résultats sélectionnés ont changé pendant la comparaison ; relancez l’analyse.", "Во время сравнения выбранные результаты изменились. Запустите сравнение ещё раз.", "Los resultados seleccionados cambiaron durante la comparación; vuelva a ejecutar el análisis.", "比較中に選択した結果が変更されました。もう一度実行してください。", "Os resultados selecionados mudaram durante a comparação; execute a análise novamente.", "Die ausgewählten Ergebnisse haben sich während des Vergleichs geändert. Führen Sie den Vergleich erneut aus.", "Изабрани резултати су се променили током поређења. Поново покрените поређење.", "비교 중 선택한 결과가 변경되었습니다. 다시 실행하세요."
  ],
  [
    "估计方法", "Estimator", "Estimateur", "Оцениватель", "Estimador", "推定方法", "Estimador", "Schätzer", "Процењивач", "추정 방법"
  ],
  [
    "合并效应及 95% 置信区间", "Pooled effect (95% CI)", "Effet combiné (IC à 95 %)", "Объединённый эффект и 95%-й доверительный интервал", "Efecto combinado (IC del 95 %)", "統合効果と95%信頼区間", "Efeito combinado (IC de 95%)", "Gepoolter Effekt und 95%-Konfidenzintervall", "Обједињени ефекат и 95% интервал поверења", "통합 효과 및 95% 신뢰구간"
  ],
  [
    "正在比较三种研究间方差估计方法…已等待 {0} 秒。", "Comparing three heterogeneity estimators… {0} s elapsed.", "Comparaison de trois estimateurs de la variance interétudes… {0} s écoulées.", "Сравнение трёх оценок межисследовательской дисперсии… прошло {0} с.", "Comparando tres estimadores de la varianza entre estudios… han transcurrido {0} s.", "3種類の研究間分散推定量を比較中… {0} 秒経過。", "Comparando três estimadores da variância entre estudos… {0} s decorridos.", "Drei Schätzer der Zwischenstudienvarianz werden verglichen… {0} s vergangen.", "Упоређују се три процењивача варијансе између студија… прошло је {0} s.", "세 가지 연구 간 분산 추정량을 비교 중… {0}초 경과."
  ],
  [
    "正在合并分析…已等待 {0} 秒。", "Synthesizing… {0} s elapsed.", "Méta-analyse en cours… {0} s écoulées.", "Выполняется синтез… прошло {0} с.", "Combinando resultados… {0} s transcurridos.", "統合中… {0} 秒経過。", "Combinando resultados… {0} s decorridos.", "Synthese läuft… {0} s vergangen.", "Синтеза је у току… прошло је {0} с.", "통합 중… {0}초 경과."
  ],
  [
    "正在载入研究与报告…已等待 {0} 秒。", "Loading studies and reports… {0} s elapsed.", "Chargement des études et des rapports… {0} s écoulées.", "Загрузка исследований и отчётов… прошло {0} с.", "Cargando estudios e informes… han transcurrido {0} s.", "研究と報告を読み込み中… {0} 秒経過。", "Carregando estudos e relatórios… {0} s decorridos.", "Studien und Berichte werden geladen… {0} s vergangen.", "Учитавање студија и извештаја… прошло је {0} с.", "연구와 보고서를 불러오는 중… {0}초 경과."
  ],
  [
    "{0} 项独立研究 · {1} · {2}", "{0} independent studies · {1} · {2}", "{0} études indépendantes · {1} · {2}", "{0} независимых исследований · {1} · {2}", "{0} estudios independientes · {1} · {2}", "独立した研究 {0} 件 · {1} · {2}", "{0} estudos independentes · {1} · {2}", "{0} unabhängige Studien · {1} · {2}", "{0} независних студија · {1} · {2}", "독립 연구 {0}건 · {1} · {2}"
  ],
  [
    "结果相近并不能验证模型假设。请核对原始报告，并引用实际使用的估计方法。", "Similar estimates do not validate model assumptions. Verify source reports and cite the estimator used.", "Des estimations similaires ne valident pas les hypothèses du modèle. Vérifiez les rapports sources et citez l’estimateur utilisé.", "Близкие оценки не подтверждают допущения модели. Проверьте исходные отчёты и укажите использованный метод оценивания.", "Que las estimaciones sean similares no valida los supuestos del modelo. Verifique los informes fuente y cite el estimador utilizado.", "推定値が近くても、モデルの仮定が妥当だとは確認できません。原資料の報告を確認し、実際に使用した推定方法を引用してください。", "Estimativas semelhantes não validam as suposições do modelo. Confira os relatórios de origem e cite o estimador utilizado.", "Ähnliche Schätzungen bestätigen die Modellannahmen nicht. Prüfen Sie die Quellenberichte und zitieren Sie den verwendeten Schätzer.", "Сличне процене не потврђују претпоставке модела. Проверите изворне извештаје и наведите коришћени процењивач.", "추정치가 비슷하다고 해서 모형 가정이 타당한 것은 아닙니다. 원자료 보고서를 확인하고 실제 사용한 추정 방법을 인용하세요."
  ],
  [
    "τ² 表示 {0} 分析尺度上的研究间方差。三次拟合均使用固定逆方差 Q={1}（自由度 {2}），但 τ² 估计方法不同。", "τ² is the between-study variance on the {0} analysis scale. All three fits use the same inverse-variance weighted fixed-effect Q={1} (df {2}); the τ² estimators differ.", "τ² est la variance inter-études sur l’échelle d’analyse {0}. Les trois ajustements utilisent le Q du modèle à effet fixe, pondéré par l’inverse de la variance d’échantillonnage, Q={1} (df {2}) ; les estimateurs de τ² diffèrent.", "τ² — межисследовательская дисперсия на аналитической шкале {0}. Во всех трёх моделях используется одно и то же Q={1} модели с фиксированным эффектом и весами, обратными выборочным дисперсиям (df {2}); методы оценки τ² различаются.", "τ² es la varianza entre estudios en la escala de análisis {0}. Los tres ajustes usan el Q del modelo de efectos fijos, ponderado por el inverso de la varianza de muestreo, Q={1} (gl {2}); los estimadores de τ² difieren.", "τ² は {0} 解析尺度上の研究間分散です。3つの適合はいずれも固定効果の逆分散重み付き Q={1}（自由度 {2}）を用いますが、τ² の推定方法は異なります。", "τ² é a variância entre estudos na escala de análise {0}. Os três ajustes usam o Q do modelo de efeitos fixos, ponderado pelo inverso da variância amostral, Q={1} (gl {2}); os estimadores de τ² diferem.", "τ² ist die Varianz zwischen Studien auf der Analyseskala {0}. Alle drei Anpassungen verwenden dasselbe Q={1} aus einem Fixed-Effect-Modell mit inverser Varianzgewichtung (df {2}); die τ²-Schätzer unterscheiden sich.", "τ² је варијанса између студија на аналитичкој скали {0}. Сва три прилагођавања користе исто Q={1} из модела са фиксним ефектом и тежинама обрнуто пропорционалним варијансама узорковања (df {2}); процењивачи τ² се разликују.", "τ²는 {0} 분석 척도에서의 연구 간 분산입니다. 세 적합 모두 고정효과 역분산 가중 Q={1}(자유도 {2})를 사용하지만 τ² 추정 방법은 서로 다릅니다."
  ],
  [
    "自然尺度",
    "natural"
  ],
  [
    "对数尺度",
    "log"
  ],
  [
    "Fisher z 尺度",
    "fisher_z"
  ],
  [
    "logit 尺度", "logit", "logit", "Шкала logit", "logit", "ロジット尺度", "logit", "Logit-Skala", "Логит скала", "로짓 척도"
  ],
  [
    "正态 95% 区间（分析尺度上估计值 ± 1.96 × SE）", "normal 95% (estimate ± 1.96 × SE on analysis scale)", "Intervalle normal à 95 % (estimation ± 1,96 × ET sur l’échelle d’analyse)", "Нормальный 95%-й интервал (оценка ± 1,96 × стандартная ошибка на аналитической шкале)", "Intervalo normal del 95 % (estimación ± 1,96 × EE en la escala de análisis)", "正規近似による95%区間（解析尺度での推定値 ± 1.96 × SE）", "Intervalo normal de 95% (estimativa ± 1,96 × EP na escala de análise)", "Normales 95%-Intervall (Schätzwert ± 1,96 × SE auf der Analyseskala)", "Нормални интервал од 95% (процена ± 1,96 × стандардна грешка на аналитичкој скали)", "정규근사 95% 구간(분석 척도의 추정치 ± 1.96 × SE)"
  ],
  [
    "固定效应逆方差模型",
    "fixed-effect inverse variance"
  ],
  [
    "DerSimonian–Laird 随机效应",
    "DerSimonian-Laird random effects"
  ],
  [
    "REML 随机效应",
    "restricted maximum likelihood random effects"
  ],
  [
    "Wald 正态区间",
    "Wald normal interval"
  ],
  [
    "修正 Hartung–Knapp–Sidik–Jonkman t 区间",
    "modified Hartung-Knapp-Sidik-Jonkman t interval"
  ],
  [
    "Higgins 型预测区间",
    "Higgins-type prediction interval"
  ],
  [
    "最大似然随机效应",
    "Maximum-likelihood random-effects model"
  ],
  [
    "合并效应 ML 轮廓似然区间",
    "pooled-effect ML profile likelihood interval"
  ],
  [
    "可用",
    "Available"
  ],
  [
    "未收敛。",
    "Did not converge."
  ],
  [
    "没有返回结果", "no result returned", "Aucun résultat renvoyé", "Результат не получен", "No se devolvieron resultados", "結果が返されませんでした", "Nenhum resultado retornado", "Kein Ergebnis zurückgegeben", "Није добијен резултат", "결과가 반환되지 않았습니다"
  ],
  [
    "至少需要三项研究。",
    "needs at least three studies"
  ],
  [
    "上限不可用；该区间不是有限区间。",
    "Upper bound unavailable; not a finite interval."
  ],
  [
    "第一层参数自助法基本区间",
    "first-level parametric bootstrap basic interval"
  ],
  [
    "基本法", "basic", "de base", "Метод basic", "básico", "基本法", "básico", "Basic-Verfahren", "Метода basic", "기본법"
  ],
  [
    "基本法（反向百分位）",
    "Basic (reverse percentile)"
  ],
  [
    "请核对 Higgins 与 Thompson（2002）原文，并引用实际使用的估计量和区间方法。", "Check Higgins & Thompson (2002) and cite the estimator and interval method used.", "Consultez Higgins et Thompson (2002) et citez l’estimateur et la méthode d’intervalle utilisés.", "Сверьтесь с оригинальной работой Higgins & Thompson (2002) и укажите использованный метод оценивания и построения интервала.", "Consulte Higgins y Thompson (2002) y cite el estimador y el método de intervalo utilizados.", "Higgins と Thompson（2002）の原文を確認し、実際に使用した推定量と区間法を引用してください。", "Consulte Higgins e Thompson (2002) e cite o estimador e o método de intervalo utilizados.", "Prüfen Sie Higgins & Thompson (2002) im Original und zitieren Sie den verwendeten Schätzer und das Intervallverfahren.", "Проверите изворни рад Higgins & Thompson (2002) и наведите коришћени процењивач и методу за интервал.", "Higgins와 Thompson(2002) 원문을 확인하고 실제 사용한 추정량과 구간 방법을 인용하세요."
  ],
  [
    " 至 ", " to ", " à ", " по ", " a ", " から ", " a ", " bis ", " до ", " 부터 "
  ],
  [
    "{0}：{1}",
    "{0}: {1}"
  ],
  [
    "当前没有可复制的方法说明。", "There are no method notes to copy yet.", "Aucune note méthodologique à copier pour le moment.", "Пока нет примечаний о методах для копирования.", "Todavía no hay notas metodológicas que copiar.", "コピーできる方法メモはまだありません。", "Ainda não há notas metodológicas para copiar.", "Es gibt noch keine Methodennotizen zum Kopieren.", "Још нема белешки о методама за копирање.", "복사할 방법 메모가 아직 없습니다."
  ],
  [
    "设置已应用；运行分析以更新结果。", "Settings applied. Run the analysis to update the result.", "Paramètres appliqués. Lancez l’analyse pour mettre le résultat à jour.", "Настройки применены. Запустите анализ, чтобы обновить результат.", "Configuración aplicada. Ejecute el análisis para actualizar el resultado.", "設定を適用しました。結果を更新するには解析を実行してください。", "Configurações aplicadas. Execute a análise para atualizar o resultado.", "Einstellungen angewendet. Führen Sie die Analyse aus, um das Ergebnis zu aktualisieren.", "Подешавања су примењена. Покрените анализу да бисте ажурирали резултат.", "설정을 적용했습니다. 결과를 업데이트하려면 분석을 실행하세요."
  ],
  [
    "返回分析目录", "Back to analysis directory", "Retour au répertoire d’analyses", "Вернуться в каталог анализов", "Volver al directorio de análisis", "解析一覧に戻る", "Voltar ao diretório de análises", "Zurück zum Analyseverzeichnis", "Назад на каталог анализа", "분석 목록으로 돌아가기"
  ],
  [
    "打开分析目录",
    "Open analysis directory"
  ],
  [
    "关闭分析目录", "Close analysis directory", "Fermer le répertoire d’analyses", "Закрыть каталог анализов", "Cerrar el directorio de análisis", "解析一覧を閉じる", "Fechar o diretório de análises", "Analyseverzeichnis schließen", "Затвори каталог анализа", "분석 목록 닫기"
  ],
  [
    "区间方法比较", "Interval method comparison", "Comparaison des méthodes d’intervalle", "Сравнение методов построения интервалов", "Comparación de métodos de intervalos", "区間法の比較", "Comparação de métodos de intervalo", "Vergleich der Intervallmethoden", "Поређење метода за интервал", "구간 방법 비교"
  ],
  [
    "参考行", "Reference row", "Ligne de référence", "Референсная строка", "Fila de referencia", "参照行", "Linha de referência", "Referenzzeile", "Ред референце", "참조 행"
  ],
  [
    "未指定", "Not specified", "Non spécifié", "Не указано", "No especificado", "指定なし", "Não especificado", "Nicht angegeben", "Није наведено", "지정되지 않음"
  ],
  [
    "配置错误", "Configuration error", "Erreur de configuration", "Ошибка конфигурации", "Error de configuración", "設定エラー", "Erro de configuração", "Konfigurationsfehler", "Грешка у конфигурацији", "설정 오류"
  ],
  [
    "图形", "Figure", "Figure", "Рисунок", "Figura", "図", "Figura", "Abbildung", "Слика", "그림"
  ],
  [
    "数值表", "Values", "Valeurs", "Значения", "Valores", "数値", "Valores", "Werte", "Вредности", "값"
  ],
  [
    "研究",
    "Study"
  ],
  [
    "估计值",
    "Estimate"
  ],
  [
    "区间下限", "Lower bound", "Borne inférieure", "Нижняя граница интервала", "Límite inferior", "下限", "Limite inferior", "Untere Intervallgrenze", "Доња граница интервала", "하한"
  ],
  [
    "区间上限", "Upper bound", "Borne supérieure", "Верхняя граница интервала", "Límite superior", "上限", "Limite superior", "Obere Intervallgrenze", "Горња граница интервала", "상한"
  ],
  [
    "暂无森林图", "No forest plot yet", "Pas encore de diagramme en forêt", "Лесной график пока не построен", "Aún no hay gráfico de bosque", "フォレストプロットはまだありません", "Ainda não há gráfico de floresta", "Noch kein Forest-Plot", "Шумски график још није доступан", "포리스트 플롯이 아직 없습니다"
  ],
  [
    "显示全部分析", "Show all analyses", "Afficher toutes les analyses", "Показать все анализы", "Mostrar todos los análisis", "すべての解析を表示", "Mostrar todas as análises", "Alle Analysen anzeigen", "Прикажи све анализе", "모든 분석 표시"
  ],
  [
    "已应用", "Applied", "Appliqué", "Применено", "Aplicado", "適用済み", "Aplicado", "Angewandt", "Примењено", "적용됨"
  ],
  [
    "所选模型与区间不兼容。", "The selected model and interval method are incompatible.", "Le modèle sélectionné et la méthode d’intervalle sont incompatibles.", "Выбранная модель несовместима с методом построения интервала.", "El modelo seleccionado y el método de intervalo son incompatibles.", "選択したモデルと区間法は併用できません。", "O modelo selecionado e o método de intervalo são incompatíveis.", "Das ausgewählte Modell und das Intervallverfahren sind nicht miteinander vereinbar.", "Изабрани модел није компатибилан са методом за интервал.", "선택한 모형과 구간 방법은 함께 사용할 수 없습니다."
  ],
  [
    "方法学参考与引用提示（请查阅原书核对）",
    "Method references and citation guidance (check the original sources)"
  ],
  [
    "异质性：I²={0}%，固定逆方差 Q={1}（自由度 {2}）。τ²={3}；分析尺度：{4}。", "Heterogeneity: I²={0}%, fixed-effect Q={1} with inverse sampling-variance weights (df {2}). τ²={3}; analysis scale: {4}.", "Hétérogénéité : I²={0} %, Q du modèle à effet fixe, pondéré par l’inverse de la variance d’échantillonnage, Q={1} (df {2}). τ²={3} ; échelle d’analyse : {4}.", "Гетерогенность: I²={0}%, Q={1} модели с фиксированным эффектом и весами, обратными выборочным дисперсиям (df {2}). τ²={3}; аналитическая шкала: {4}.", "Heterogeneidad: I²={0} %, Q del modelo de efectos fijos, ponderado por el inverso de la varianza de muestreo, Q={1} (gl {2}). τ²={3}; escala de análisis: {4}.", "異質性：I²={0}%、固定効果の逆分散重み付き Q={1}（自由度 {2}）。τ²={3}；解析尺度：{4}。", "Heterogeneidade: I²={0}%, Q do modelo de efeitos fixos, ponderado pelo inverso da variância amostral, Q={1} (gl {2}). τ²={3}; escala de análise: {4}.", "Heterogenität: I²={0} %, Q={1} aus dem Fixed-Effect-Modell mit inverser Varianzgewichtung (df {2}). τ²={3}; Analyseskala: {4}.", "Хетерогеност: I²={0}%, Q={1} из модела са фиксним ефектом и тежинама обрнуто пропорционалним варијансама узорковања (df {2}). τ²={3}; аналитичка скала: {4}.", "이질성: I²={0}%, 고정효과 역분산 가중 Q={1}(자유도 {2}). τ²={3}; 분석 척도: {4}."
  ],
  [
    "I² 按 max(0, (Q−df)/Q) 计算；Q=0 时记为 0。请结合所用模型解释，并引用实际估计方法。", "I² = max(0, (Q−df)/Q), set to 0 when Q=0. Interpret in the model context and cite the estimator used.", "I² = max(0, (Q−df)/Q) et vaut 0 lorsque Q=0. Interprétez-le selon le modèle utilisé et citez l’estimateur employé.", "I² = max(0, (Q−df)/Q); при Q=0 значение принимается равным 0. Интерпретируйте его с учётом модели и укажите использованный метод оценивания.", "I² = max(0, (Q−df)/Q) y se fija en 0 cuando Q=0. Interprételo según el modelo utilizado y cite el estimador empleado.", "I² は max(0, (Q−df)/Q) で計算し、Q=0 の場合は 0 とします。使用したモデルの文脈で解釈し、実際の推定方法を引用してください。", "I² = max(0, (Q−df)/Q), definido como 0 quando Q=0. Interprete-o no contexto do modelo e cite o estimador utilizado.", "I² = max(0, (Q−df)/Q); bei Q=0 wird I² auf 0 gesetzt. Interpretieren Sie den Wert im Modellkontext und zitieren Sie den verwendeten Schätzer.", "I² = max(0, (Q−df)/Q); ако је Q=0, вредност је 0. Тумачите је у контексту модела и наведите коришћени процењивач.", "I²는 max(0, (Q−df)/Q)로 계산하며 Q=0이면 0으로 둡니다. 사용한 모형의 맥락에서 해석하고 실제 추정 방법을 인용하세요."
  ],
  [
    "统计结果与研究报告保留原始数值；方法引用须自行核对原文。", "Results and study reports retain their original values; verify method citations against the original sources.", "Les résultats statistiques et les rapports d’étude conservent leurs valeurs originales ; vérifiez les références méthodologiques dans les sources originales.", "Статистические результаты и отчёты исследований сохраняют исходные значения; сверяйте описания методов с первоисточниками.", "Los resultados estadísticos y los informes de los estudios conservan sus valores originales; verifique las citas metodológicas en las fuentes originales.", "統計結果と研究報告の数値は原値のまま保持されます。方法の引用は原典で確認してください。", "Os resultados estatísticos e os relatórios dos estudos mantêm os valores originais; confira as citações metodológicas nas fontes originais.", "Statistische Ergebnisse und Studienberichte behalten ihre Originalwerte; prüfen Sie die Methodenangaben anhand der Originalquellen.", "Статистички резултати и извештаји студија задржавају изворне вредности; проверите наводе о методама према изворним радовима.", "통계 결과와 연구 보고서의 값은 원래 수치 그대로 유지됩니다. 방법 인용은 원문에서 확인하세요."
  ],
  [
    "导出 Peters JSON",
    "Export Peters JSON"
  ],
  [
    "随机种子",
    "Random seed"
  ],
  [
    "参照模型", "Reference model", "Modèle de référence", "Референсная модель", "Modelo de referencia", "参照モデル", "Modelo de referência", "Referenzmodell", "Референтни модел", "기준 모형"
  ],
  [
    "固定效应逆方差模型",
    "fixed-effect inverse variance"
  ],
  [
    "DerSimonian–Laird 随机效应",
    "DerSimonian-Laird random effects"
  ],
  [
    "REML 随机效应",
    "restricted maximum likelihood random effects"
  ],
  [
    "Wald 正态区间",
    "Wald normal interval"
  ],
  [
    "修正 Hartung–Knapp–Sidik–Jonkman t 区间",
    "modified Hartung-Knapp-Sidik-Jonkman t interval"
  ],
  [
    "Higgins 型预测区间",
    "Higgins-type prediction interval"
  ],
  [
    "最大似然随机效应",
    "Maximum-likelihood random-effects model"
  ],
  [
    "合并效应 ML 轮廓似然区间",
    "pooled-effect ML profile likelihood interval"
  ],
  [
    "研究数较少或 τ² 处于边界时，渐近似然比界限可能不准确。",
    "Asymptotic likelihood-ratio limits can be inaccurate with few studies or a boundary tau-squared estimate."
  ],
  [
    "低于无效值", "Below the null value", "inférieur à la valeur nulle", "Ниже значения, соответствующего отсутствию эффекта", "por debajo del valor nulo", "無効果の基準値より低い", "abaixo do valor nulo", "Unterhalb des Werts ohne Effekt", "Испод вредности која означава одсуство ефекта", "효과 없음 기준값보다 낮음"
  ],
  [
    "高于无效值", "Above the null value", "supérieur à la valeur nulle", "Выше значения, соответствующего отсутствию эффекта", "por encima del valor nulo", "無効果の基準値より高い", "acima do valor nulo", "Oberhalb des Werts ohne Effekt", "Изнад вредности која означава одсуство ефекта", "효과 없음 기준값보다 높음"
  ],
  [
    "跨越无效值", "Crosses the null value", "recoupe la valeur nulle", "Интервал охватывает значение, соответствующее отсутствию эффекта", "incluye el valor nulo", "無効果の基準値をまたぐ", "inclui o valor nulo", "Das Intervall umfasst den Wert ohne Effekt", "Интервал обухвата вредност која означава одсуство ефекта", "효과 없음 기준값을 포함함"
  ],
  [
    "修正 HKSJ 区间用于随机效应模型；主合并分析不接受固定效应 + HKSJ。此处仅供并列敏感性比较。",
    "The modified HKSJ interval is designed for random-effects models; coscreen.review_analysis.synthesize rejects fixed + HKSJ for primary synthesis. Shown here for side-by-side sensitivity comparison only."
  ],
  [
    "固定效应模型将 τ² 设为 0，因此该预测区间只反映同质性假设下新研究的抽样误差；报告时应使用随机效应预测区间。",
    "The fixed-effect model sets tau-squared to 0, so this prediction interval covers only the sampling variation of a new study under homogeneity; random-effect prediction intervals are the reportable ones."
  ],
  [
    "预测区间至少需要三项研究（t 分位数自由度为 k−2）。",
    "prediction intervals need at least three studies (t quantile with k - 2 df)"
  ],
  [
    "至少需要三项研究。",
    "needs at least three studies"
  ],
  [
    "REML τ² 轮廓区间目前仅支持 95% 水平。",
    "profile REML tau-squared interval is implemented at level 0.95 only"
  ],
  [
    "上限不可用；该区间不是有限区间。",
    "Upper bound unavailable; not a finite interval."
  ],
  [
    "未收敛。",
    "Did not converge."
  ],
  [
    "可用",
    "Available"
  ],
  [
    "H 与 I² 转换区间至少需要三项研究。",
    "H and I-squared transformed intervals need at least three studies"
  ],
  [
    "不同区间对是否跨越无效值的结论不一致。",
    "Intervals disagree on whether they cross the null. "
  ],
  [
    "第一层参数自助法百分位区间",
    "first-level parametric bootstrap percentile interval"
  ],
  [
    "第一层参数自助法基本区间",
    "first-level parametric bootstrap basic interval"
  ],
  [
    "REML τ² 轮廓似然区间",
    "REML profile likelihood interval for tau-squared"
  ],
  [
    "固定逆方差 Q 的非中心卡方反演（近似；DL 点估计）",
    "Noncentral chi-square inversion of fixed-effect Q using inverse sampling-variance weights (approximate; DL point estimate)"
  ],
  [
    "Viechtbauer（2007）τ² Q-profile 区间",
    "Viechtbauer (2007) Q-profile interval for tau-squared"
  ],
  [
    "Higgins 与 Thompson（2002）τ² 转换区间（H 界限经典型方差 (k−1)/C 映射）",
    "Higgins & Thompson (2002) transformed interval for tau-squared (H limits mapped through the typical variance (k-1)/C)"
  ],
  [
    "Higgins 与 Thompson（2002）τ² 转换区间",
    "Higgins & Thompson (2002) transformed interval for tau-squared"
  ],
  [
    "Higgins 与 Thompson（2002）转换区间",
    "Higgins & Thompson (2002) transformed interval"
  ],
  [
    "第一层参数自助法是条件性敏感性分析，不会重新生成研究间异质性。basic 区间请核对 Davison 与 Hinkley（1997）《Bootstrap Methods and their Application》第 5.6 式；报告时引用方法，并报告重复次数和随机种子。",
    "First-level parametric bootstrap is a conditional sensitivity analysis; it does not regenerate between-study heterogeneity. Check Davison & Hinkley (1997), Bootstrap Methods and their Application, eq. 5.6 for basic intervals; cite the method and report replicate count and seed."
  ],
  [
    "请先应用或取消模型修改。", "Apply or cancel the model changes first.", "Appliquez ou annulez d’abord les modifications du modèle.", "Сначала примените или отмените изменения модели.", "Aplique primero los cambios del modelo o descártelos.", "先にモデル変更を適用するか、取り消してください。", "Aplique ou cancele primeiro as alterações do modelo.", "Wenden Sie die Modelländerungen zuerst an oder verwerfen Sie sie.", "Најпре примените или откажите измене модела.", "먼저 모형 변경사항을 적용하거나 취소하세요."
  ],
  [
    "完整方法、限制和研究来源见“方法与引用”；计算输入保留于 SVG 元数据。", "See Methods & citations for full methods, limitations and study sources; calculation inputs are retained in the SVG metadata.", "Pour les méthodes complètes, les limites et les sources des études, consultez « Méthodes et références » ; les données de calcul sont conservées dans les métadonnées SVG.", "Полное описание методов, ограничений и источников исследований см. в разделе «Методы и цитирования»; входные данные расчёта сохранены в метаданных SVG.", "Consulte «Métodos y referencias» para ver los métodos completos, las limitaciones y las fuentes de los estudios; los datos de cálculo se conservan en los metadatos SVG.", "方法、限界、研究の出典の詳細は「方法と引用」を参照してください。計算への入力値は SVG メタデータに保持されます。", "Consulte “Métodos e referências” para ver os métodos completos, as limitações e as fontes dos estudos; os dados do cálculo são mantidos nos metadados SVG.", "Vollständige Methoden, Einschränkungen und Studienquellen finden Sie unter „Methoden & Zitate“; die Berechnungseingaben sind in den SVG-Metadaten gespeichert.", "Комплетан опис метода, ограничења и извора студија налази се у одељку „Методе и цитати“; улазни подаци за израчун сачувани су у метаподацима SVG-а.", "방법, 한계 및 연구 출처의 자세한 내용은 ‘방법 및 인용’을 확인하세요. 계산 입력값은 SVG 메타데이터에 보존됩니다."
  ],
  [
    "效应及 95% 置信区间", "Effect and 95% confidence interval", "Effet et intervalle de confiance à 95 %", "Эффект и 95%-й доверительный интервал", "Efecto e intervalo de confianza del 95 %", "効果と95%信頼区間", "Efeito e intervalo de confiança de 95%", "Effekt und 95%-Konfidenzintervall", "Ефекат и интервал поверења од 95%", "효과 및 95% 신뢰구간"
  ],
  [
    "Higgins–Thompson 95% 区间：I² {0}%；H={1}（{2}）。", "Higgins–Thompson 95% interval: I² {0}%; H={1} ({2}).", "Intervalle de Higgins–Thompson à 95 % : I² {0} % ; H={1} ({2}).", "95%-й интервал Higgins–Thompson: I² {0}%; H={1} ({2}).", "Intervalo de Higgins–Thompson del 95 %: I² {0} %; H={1} ({2}).", "Higgins–Thompson 95%区間：I² {0}%；H={1}（{2}）。", "Intervalo de Higgins–Thompson de 95%: I² {0}%; H={1} ({2}).", "Higgins–Thompson-95%-Intervall: I² {0} %; H={1} ({2}).", "Higgins–Thompson интервал од 95%: I² {0}%; H={1} ({2}).", "Higgins–Thompson 95% 구간: I² {0}%; H={1} ({2})."
  ],
  [
    "区间对照（显示尺度）", "Intervals (display scale)", "Comparaison des intervalles (échelle d’affichage)", "Интервалы (шкала отображения)", "Comparación de intervalos (escala mostrada)", "区間の比較（表示尺度）", "Comparação de intervalos (escala exibida)", "Intervalle (Darstellungsskala)", "Интервали (скала приказа)", "구간 비교(표시 척도)"
  ],
  [
    "区间：{0} 至 {1}", "Interval: {0} to {1}", "Intervalle : {0} à {1}", "Интервал: от {0} до {1}", "Intervalo: {0} a {1}", "区間：{0} ～ {1}", "Intervalo: {0} a {1}", "Intervall: {0} bis {1}", "Интервал: {0} до {1}", "구간: {0}~{1}"
  ],
  [
    "设为参考", "Set as reference", "Définir comme référence", "Задать как референс", "Establecer como referencia", "参照に設定", "Definir como referência", "Als Referenz festlegen", "Постави као референцу", "기준으로 설정"
  ],
  [
    "完整模型与区间结果", "Full model and interval results", "Résultats complets du modèle et des intervalles", "Полные результаты модели и интервалы", "Resultados completos del modelo y los intervalos", "モデルと区間の全結果", "Resultados completos do modelo e dos intervalos", "Vollständige Modell- und Intervallergebnisse", "Комплетни резултати модела и интервала", "모형 및 구간 결과 전체"
  ],
  [
    "原分析范围", "Original analysis scope", "Périmètre initial de l’analyse", "Исходный охват анализа", "Ámbito original del análisis", "元の解析範囲", "Escopo original da análise", "Ursprünglicher Analyseumfang", "Првобитни обим анализе", "원래 분석 범위"
  ],
  [
    "当前计算没有返回可用结果。", "The calculation did not return an available result.", "Le calcul n’a pas renvoyé de résultat exploitable.", "Расчёт не вернул доступного результата.", "El cálculo no devolvió un resultado disponible.", "計算から利用可能な結果が返されませんでした。", "O cálculo não retornou um resultado utilizável.", "Die Berechnung hat kein verfügbares Ergebnis geliefert.", "Израчун није дао доступан резултат.", "계산에서 사용 가능한 결과를 반환하지 않았습니다."
  ],
  [
    "保留修改并继续编辑",
    "Keep changes and continue editing"
  ],
  [
    "放弃修改并切换",
    "Discard changes and switch"
  ],
  [
    "尚有未应用的模型修改",
    "Unapplied model changes"
  ],
  [
    "切换任务将放弃这些修改。已经完成的运行不会因此被重算。",
    "Switching tasks discards these changes. Completed runs will not be recomputed."
  ],
  [
    "随机效应 · PM",
    "Random effects · PM"
  ],
  [
    "自然尺度",
    "Natural scale"
  ],
  [
    "至", "to", "à", "до", "a", "～", "a", "bis", "до", "~"
  ],
  [
    "区间方法",
    "Interval method"
  ],
  [
    "REML τ² 轮廓似然区间",
    "REML profile likelihood interval for tau-squared"
  ],
  [
    "Harrer 等（2022）第 4 章；Borenstein 等（2021）第 11–12 章。", "Harrer et al. (2022), ch. 4; Borenstein et al. (2021), ch. 11–12", "Harrer et al. (2022), ch. 4; Borenstein et al. (2021), ch. 11–12", "Harrer и соавт. (2022), гл. 4; Borenstein и соавт. (2021), гл. 11–12.", "Harrer et al. (2022), ch. 4; Borenstein et al. (2021), ch. 11–12", "Harrer et al. (2022), ch. 4; Borenstein et al. (2021), ch. 11–12", "Harrer et al. (2022), ch. 4; Borenstein et al. (2021), ch. 11–12", "Harrer et al. (2022), Kap. 4; Borenstein et al. (2021), Kap. 11–12.", "Harrer и сар. (2022), погл. 4; Borenstein и сар. (2021), погл. 11–12.", "Harrer et al. (2022), ch. 4; Borenstein et al. (2021), ch. 11–12"
  ],
  [
    "I² 根据固定逆方差 Q 计算：Higgins 与 Thompson（2002），Statistics in Medicine，21：1539–1558。", "I² from fixed-effect Q with inverse sampling-variance weights: Higgins & Thompson (2002), Statistics in Medicine 21:1539–1558", "I² calculé à partir du Q du modèle à effet fixe, pondéré par l’inverse de la variance d’échantillonnage : Higgins et Thompson (2002), Statistics in Medicine 21:1539–1558.", "I² рассчитывается по Q модели с фиксированным эффектом и весами, обратными выборочным дисперсиям: Higgins и Thompson (2002), Statistics in Medicine 21:1539–1558.", "I² calculado a partir del Q del modelo de efectos fijos, ponderado por el inverso de la varianza de muestreo: Higgins y Thompson (2002), Statistics in Medicine 21:1539–1558.", "I² from fixed inverse-variance Q: Higgins & Thompson (2002), Statistics in Medicine 21:1539–1558", "I² calculado a partir do Q do modelo de efeitos fixos, ponderado pelo inverso da variância amostral: Higgins e Thompson (2002), Statistics in Medicine 21:1539–1558.", "I² wird aus Q eines Fixed-Effect-Modells mit inverser Varianzgewichtung berechnet: Higgins & Thompson (2002), Statistics in Medicine 21:1539–1558.", "I² се израчунава на основу Q модела са фиксним ефектом и тежинама обрнуто пропорционалним варијансама узорковања: Higgins & Thompson (2002), Statistics in Medicine 21:1539–1558.", "I² from fixed inverse-variance Q: Higgins & Thompson (2002), Statistics in Medicine 21:1539–1558"
  ],
  [
    "单组均值：Harrer 等（2022）第 3.2.1 节，第 59 页。", "one-group mean: Harrer et al. (2022), §3.2.1, p. 59", "Moyenne à un groupe : Harrer et al. (2022), §3.2.1, p. 59.", "Среднее для одной группы: Harrer и соавт. (2022), § 3.2.1, с. 59.", "Media de un grupo: Harrer et al. (2022), §3.2.1, p. 59.", "単群平均：Harrer ら（2022）、§3.2.1、p. 59。", "Média de um grupo: Harrer et al. (2022), §3.2.1, p. 59.", "Mittelwert einer Gruppe: Harrer et al. (2022), § 3.2.1, S. 59.", "Средња вредност једне групе: Harrer и сар. (2022), § 3.2.1, стр. 59.", "단일군 평균: Harrer 등(2022), §3.2.1, p. 59."
  ],
  [
    "单组 logit 比例（逆方差近似）：Harrer 等（2022）第 3.2.2、4.2.6 节，第 60–61、132–134 页。", "one-group logit proportion (inverse-variance approximation): Harrer et al. (2022), §§3.2.2 and 4.2.6, pp. 60–61, 132–134", "Proportion logit à un groupe (approximation par variance inverse) : Harrer et al. (2022), §§3.2.2 et 4.2.6, pp. 60–61, 132–134.", "Доля для одной группы на шкале logit (приближение обратной дисперсии): Harrer и соавт. (2022), §§ 3.2.2 и 4.2.6, с. 60–61 и 132–134.", "Proporción logit de un grupo (aproximación por varianza inversa): Harrer et al. (2022), §§3.2.2 y 4.2.6, pp. 60–61, 132–134.", "単群ロジット割合（逆分散近似）：Harrer ら（2022）、§§3.2.2、4.2.6、pp. 60–61、132–134。", "Proporção logit de um grupo (aproximação por variância inversa): Harrer et al. (2022), §§3.2.2 e 4.2.6, pp. 60–61, 132–134.", "Logit-Anteil einer Gruppe (Invers-Varianz-Näherung): Harrer et al. (2022), §§ 3.2.2 und 4.2.6, S. 60–61 und 132–134.", "Логит удео једне групе (апроксимација обрнуте варијансе): Harrer и сар. (2022), §§ 3.2.2 и 4.2.6, стр. 60–61 и 132–134.", "단일군 로짓 비율(역분산 근사): Harrer 등(2022), §§3.2.2, 4.2.6, pp. 60–61, 132–134."
  ],
  [
    "单组对数发生率：metafor escalc 文档，IRLN 指标；请核对人时单位。", "one-group log incidence rate: metafor escalc documentation, measure IRLN; verify person-time unit", "Taux d’incidence logarithmique à un groupe : documentation metafor escalc, mesure IRLN ; vérifiez l’unité de temps-personne.", "Логарифм частоты заболеваемости для одной группы: документация metafor escalc, показатель IRLN; проверьте единицу человеко-времени.", "Tasa de incidencia logarítmica de un grupo: documentación de metafor escalc, medida IRLN; verifique la unidad de persona-tiempo.", "単群の対数発生率：metafor escalc のドキュメント、指標 IRLN；人時間の単位を確認してください。", "Taxa de incidência logarítmica de um grupo: documentação do metafor escalc, medida IRLN; confira a unidade de pessoa-tempo.", "Logarithmische Inzidenzrate einer Gruppe: Dokumentation zu metafor escalc, Maß IRLN; prüfen Sie die Personenzeiteinheit.", "Логаритам стопе инциденције у једној групи: документација metafor escalc, мера IRLN; проверите јединицу особа-време.", "단일군 로그 발생률: metafor escalc 문서, IRLN 측정치; 인시 단위를 확인하세요."
  ],
  [
    "已报告的单组正态／Wald 区间换算为标准误：先将界限变换至保存的分析尺度，再将区间宽度除以对应置信水平下正态临界值的两倍。请核对 Cochrane Handbook 第 6 章第 6.3.1–6.3.2 节、原区间方法，并引用研究报告。", "Reported one-group normal/Wald CI to SE: transform limits to the saved analysis scale, then divide interval width by twice the normal critical value at the saved confidence level. Check Cochrane Handbook ch. 6, §§6.3.1–6.3.2; verify the source interval method and cite the report.", "Conversion d’un IC normal/Wald publié en erreur-type pour un groupe : transformez d’abord les bornes vers l’échelle d’analyse enregistrée, puis divisez la largeur de l’intervalle par deux fois la valeur critique normale correspondant au niveau de confiance enregistré. Consultez le Cochrane Handbook, ch. 6, §§6.3.1–6.3.2 ; vérifiez la méthode de l’intervalle source et citez le rapport.", "Для перевода опубликованного нормального/Wald-доверительного интервала для одной группы в стандартную ошибку сначала преобразуйте границы на сохранённую аналитическую шкалу, затем разделите ширину интервала на удвоенное нормальное критическое значение для сохранённого уровня доверия. Сверьтесь с Cochrane Handbook, гл. 6, §§ 6.3.1–6.3.2, исходным методом построения интервала и отчётом исследования.", "Conversión de un IC normal/Wald publicado a EE para un grupo: transforme primero los límites a la escala de análisis guardada y luego divida la amplitud del intervalo por dos veces el valor crítico normal correspondiente al nivel de confianza guardado. Consulte el Cochrane Handbook, cap. 6, §§6.3.1–6.3.2; verifique el método del intervalo fuente y cite el informe.", "報告された単群の正規／Wald 信頼区間から標準誤差への換算：限界値を保存された解析尺度に変換し、区間幅を保存された信頼水準に対応する正規臨界値の2倍で割ります。Cochrane Handbook 第6章 §§6.3.1–6.3.2 と原区間法を確認し、研究報告を引用してください。", "Conversão de um IC normal/Wald publicado em EP para um grupo: primeiro transforme os limites para a escala de análise armazenada; depois, divida a amplitude do intervalo por duas vezes o valor crítico normal correspondente ao nível de confiança armazenado. Consulte o Cochrane Handbook, cap. 6, §§6.3.1–6.3.2; confira o método do intervalo de origem e cite o relatório.", "Zur Umrechnung eines berichteten Normal-/Wald-Konfidenzintervalls für eine einzelne Gruppe in einen Standardfehler transformieren Sie zunächst die Intervallgrenzen auf die gespeicherte Analyseskala. Teilen Sie dann die Intervallbreite durch das Doppelte des Normalquantils für das gespeicherte Konfidenzniveau. Prüfen Sie Cochrane Handbook, Kap. 6, §§ 6.3.1–6.3.2, die ursprüngliche Methode zur Berechnung des Intervalls und den Studienbericht.", "Да бисте пријављени нормални/Wald интервал поверења за једну групу претворили у стандардну грешку, најпре трансформишите границе интервала на сачувану аналитичку скалу, а затим поделите ширину интервала са двоструком критичном нормалном вредношћу за сачувани ниво поверења. Проверите Cochrane Handbook, погл. 6, §§ 6.3.1–6.3.2, изворну методу за рачунање интервала и извештај студије.", "보고된 단일군 정규/Wald 신뢰구간을 표준오차로 환산: 경계값을 저장된 분석 척도로 변환한 뒤 구간 폭을 저장된 신뢰수준의 정규 임계값 2배로 나눕니다. Cochrane Handbook 제6장 §§6.3.1–6.3.2와 원래 구간 방법을 확인하고 연구 보고서를 인용하세요."
  ],
  [
    "单组原始尺度不确定性换算为 logit／log 标准误时采用一阶 delta 近似；请核对 Harrer 等（2022）第 3.2.2、3.3.3 节，并引用报告的尺度与来源。", "One-group raw uncertainty to logit/log SE uses a first-order delta approximation; check Harrer et al. (2022), §§3.2.2, 3.3.3, and cite the reported scale and source.", "La conversion de l’incertitude sur l’échelle brute d’un groupe en erreur-type logit/log utilise une approximation delta du premier ordre ; consultez Harrer et al. (2022), §§3.2.2 et 3.3.3, et citez l’échelle et la source rapportées.", "При переводе исходной неопределённости для одной группы в SE на шкале logit/log используется дельта-аппроксимация первого порядка. Сверьтесь с Harrer и соавт. (2022), §§ 3.2.2 и 3.3.3, и укажите в ссылках шкалу и источник, приведённые в отчёте.", "La conversión de la incertidumbre en escala bruta de un grupo a un EE logit/log usa una aproximación delta de primer orden; consulte Harrer et al. (2022), §§3.2.2 y 3.3.3, y cite la escala y la fuente informadas.", "単群の生尺度の不確実性をロジット／対数尺度の標準誤差に換算する際は、1次の delta 近似を用います。Harrer ら（2022）§§3.2.2、3.3.3 を確認し、報告された尺度と出典を引用してください。", "A conversão da incerteza na escala bruta de um grupo para EP logit/log usa uma aproximação delta de primeira ordem; consulte Harrer et al. (2022), §§3.2.2 e 3.3.3, e cite a escala e a fonte relatadas.", "Bei der Umrechnung der Rohunsicherheit einer einzelnen Gruppe in einen Logit-/Log-SE wird eine Delta-Näherung erster Ordnung verwendet. Prüfen Sie Harrer et al. (2022), §§ 3.2.2 und 3.3.3, und zitieren Sie die berichtete Skala und Quelle.", "При претварању изворне неизвесности за једну групу у SE на logit/лог скали користи се делта апроксимација првог реда. Проверите Harrer и сар. (2022), §§ 3.2.2 и 3.3.3, и наведите скалу и извор из извештаја.", "단일군 원척도 불확실성을 로짓/로그 척도 표준오차로 환산할 때 1차 델타 근사를 사용합니다. Harrer 등(2022) §§3.2.2, 3.3.3을 확인하고 보고된 척도와 출처를 인용하세요."
  ],
  [
    "Glass Δ：以对照组标准差为分母，不作校正；metafor 的 SMD1、correct=FALSE、vtype=LS。请核对 Harrer 等（2022）第 4.2.2 节，第 112–114 页，并引用方法及研究报告。", "Glass delta: control-arm SD, uncorrected; metafor SMD1, correct=FALSE, vtype=LS. Check Harrer et al. (2022), §4.2.2, pp. 112–114, and cite the method and study reports.", "Glass Δ : écart-type du groupe témoin comme dénominateur, sans correction ; metafor SMD1, correct=FALSE, vtype=LS. Consultez Harrer et al. (2022), §4.2.2, pp. 112–114, et citez la méthode et les rapports d’étude.", "Glass Δ: в знаменателе используется SD контрольной группы без поправки; metafor SMD1, correct=FALSE, vtype=LS. Сверьтесь с Harrer и соавт. (2022), § 4.2.2, с. 112–114, и укажите в ссылках метод и отчёты исследований.", "Glass Δ: desviación estándar del grupo de control como denominador, sin corrección; metafor SMD1, correct=FALSE, vtype=LS. Consulte Harrer et al. (2022), §4.2.2, pp. 112–114, y cite el método y los informes de los estudios.", "Glass Δ：対照群の標準偏差を使用し、補正なし。metafor の SMD1、correct=FALSE、vtype=LS。Harrer ら（2022）§4.2.2、pp. 112–114 を確認し、方法と研究報告を引用してください。", "Glass Δ: desvio padrão do grupo controle como denominador, sem correção; metafor SMD1, correct=FALSE, vtype=LS. Consulte Harrer et al. (2022), §4.2.2, pp. 112–114, e cite o método e os relatórios dos estudos.", "Glass Δ: Die SD der Kontrollgruppe wird als Nenner verwendet, ohne Korrektur; metafor SMD1, correct=FALSE, vtype=LS. Prüfen Sie Harrer et al. (2022), § 4.2.2, S. 112–114, und zitieren Sie Methode und Studienberichte.", "Glass Δ: у имениоцу се користи SD контролне групе, без корекције; metafor SMD1, correct=FALSE, vtype=LS. Проверите Harrer и сар. (2022), § 4.2.2, стр. 112–114, и цитирајте методу и извештаје студија.", "Glass Δ: 대조군 표준편차 사용, 보정 없음. metafor SMD1, correct=FALSE, vtype=LS. Harrer 등(2022) §4.2.2, pp. 112–114를 확인하고 방법 및 연구 보고서를 인용하세요."
  ],
  [
    "已报告的 Glass Δ：使用对照组标准差及来源报告的标准误。请核对原报告的校正与方差方法、Harrer 等（2022）第 4.2.2 节，第 112–114 页，并引用来源。", "Reported Glass delta: control-arm SD with source-reported SE. Verify the report’s correction and variance method; check Harrer et al. (2022), §4.2.2, pp. 112–114, and cite the source.", "Glass Δ rapporté : écart-type du groupe témoin et erreur-type indiquée dans le rapport source. Vérifiez la correction et la méthode de variance du rapport ; consultez Harrer et al. (2022), §4.2.2, pp. 112–114, et citez la source.", "Опубликованное значение Glass Δ: SD контрольной группы и SE из исходного отчёта. Проверьте, какие поправка и метод расчёта дисперсии использованы в отчёте, а также Harrer и соавт. (2022), § 4.2.2, с. 112–114; укажите источник в ссылках.", "Glass Δ informado: desviación estándar del grupo de control y EE indicado en el informe fuente. Verifique la corrección y el método de varianza del informe; consulte Harrer et al. (2022), §4.2.2, pp. 112–114, y cite la fuente.", "報告済みの Glass Δ：対照群の標準偏差と、出典で報告された標準誤差を使用します。原報告の補正法と分散法を確認し、Harrer ら（2022）§4.2.2、pp. 112–114 を参照して出典を引用してください。", "Glass Δ relatado: desvio padrão do grupo controle e EP informado no relatório de origem. Confira a correção e o método de variância do relatório; consulte Harrer et al. (2022), §4.2.2, pp. 112–114, e cite a fonte.", "Berichtetes Glass Δ: SD der Kontrollgruppe und im Quellenbericht angegebener SE. Prüfen Sie die Korrektur- und Varianzmethode im Bericht sowie Harrer et al. (2022), § 4.2.2, S. 112–114, und zitieren Sie die Quelle.", "Пријављени Glass Δ: SD контролне групе и SE наведена у изворном извештају. Проверите корекцију и методу варијансе из извештаја, као и Harrer и сар. (2022), § 4.2.2, стр. 112–114, и наведите извор.", "보고된 Glass Δ: 대조군 표준편차와 출처에 보고된 표준오차를 사용합니다. 원 보고서의 보정 및 분산 방법을 확인하고 Harrer 등(2022) §4.2.2, pp. 112–114를 참고하여 출처를 인용하세요."
  ],
  [
    "Hedges g：合并标准差及小样本校正。人工录入的效应与标准误仍须核对；确认适用后引用 Hedges（1981），DOI 10.3102/10769986006002107。", "Hedges g: pooled SD with small-sample correction. Verify manually reported effects and SEs; cite Hedges (1981), DOI 10.3102/10769986006002107, when confirmed.", "Hedges g : écart-type combiné et correction pour petit échantillon. Vérifiez les effets et erreurs-types saisis manuellement ; si la méthode est applicable, citez Hedges (1981), DOI 10.3102/10769986006002107.", "Hedges g: объединённое SD с поправкой на малую выборку. Введённые вручную эффекты и SE всё равно необходимо проверить; после подтверждения укажите Hedges (1981), DOI 10.3102/10769986006002107.", "Hedges g: desviación estándar combinada y corrección para muestras pequeñas. Verifique los efectos y EE introducidos manualmente; si corresponde, cite Hedges (1981), DOI 10.3102/10769986006002107.", "Hedges g：プール標準偏差と小標本補正を使用します。手入力した効果量と標準誤差を確認してください。適用を確認できた場合は Hedges（1981）、DOI 10.3102/10769986006002107 を引用してください。", "Hedges g: desvio padrão combinado e correção para amostras pequenas. Confira os efeitos e EP inseridos manualmente; se for aplicável, cite Hedges (1981), DOI 10.3102/10769986006002107.", "Hedges g: gepoolte SD mit Korrektur für kleine Stichproben. Manuell eingetragene Effekte und SE müssen weiterhin geprüft werden; zitieren Sie nach Bestätigung Hedges (1981), DOI 10.3102/10769986006002107.", "Hedges g: обједињени SD са корекцијом за мале узорке. Ручно унети ефекти и SE и даље морају да се провере; након потврде цитирајте Hedges (1981), DOI 10.3102/10769986006002107.", "Hedges g: 통합 표준편차와 소표본 보정을 사용합니다. 직접 입력한 효과와 표준오차를 확인하세요. 적용이 확인되면 Hedges(1981), DOI 10.3102/10769986006002107을 인용하세요."
  ],
  [
    "单组后测减前测变化：Borenstein 等（2021）第 4 章，第 28–29 页；metafor escalc 的 SMCR／SMCRH／SMCC。请按保存的 variance_method 引用 Becker（1988）、Bonett（2008）或 Gibbons 等（1993）；SMCR 方差遵循 metafor 而非书中公式 4.28。不得解释为有对照的处理效应。", "One-group post−pre change: Borenstein et al. (2021), ch. 4, pp. 28–29; metafor escalc SMCR/SMCRH/SMCC. Cite the saved variance_method (Becker 1988, Bonett 2008 or Gibbons et al. 1993); the SMCR variance follows metafor rather than book equation 4.28. No controlled treatment-effect interpretation.", "Variation pré-post à un groupe : Borenstein et al. (2021), ch. 4, pp. 28–29 ; metafor escalc SMCR/SMCRH/SMCC. Citez la variance_method enregistrée (Becker 1988, Bonett 2008 ou Gibbons et al. 1993) ; la variance SMCR suit metafor, et non l’équation 4.28 du livre. Ne l’interprétez pas comme un effet de traitement contrôlé.", "Изменение после/до в одной группе: Borenstein и соавт. (2021), гл. 4, с. 28–29; metafor escalc SMCR/SMCRH/SMCC. В зависимости от сохранённого variance_method укажите Becker (1988), Bonett (2008) или Gibbons и соавт. (1993); дисперсия SMCR соответствует реализации metafor, а не формуле 4.28 из книги. Не интерпретируйте результат как контролируемый эффект лечения.", "Cambio post−pre de un grupo: Borenstein et al. (2021), cap. 4, pp. 28–29; metafor escalc SMCR/SMCRH/SMCC. Cite la variance_method guardada (Becker 1988, Bonett 2008 o Gibbons et al. 1993); la varianza SMCR sigue metafor, no la ecuación 4.28 del libro. No lo interprete como un efecto de tratamiento controlado.", "単群の事後−事前変化：Borenstein ら（2021）第4章、pp. 28–29；metafor escalc の SMCR／SMCRH／SMCC。保存された variance_method に応じて Becker（1988）、Bonett（2008）、または Gibbons ら（1993）を引用してください。SMCR の分散は書籍の式4.28ではなく metafor に従います。対照群のある治療効果として解釈しないでください。", "Mudança pós−pré de um grupo: Borenstein et al. (2021), cap. 4, pp. 28–29; metafor escalc SMCR/SMCRH/SMCC. Cite a variance_method armazenada (Becker 1988, Bonett 2008 ou Gibbons et al. 1993); a variância SMCR segue o metafor, não a equação 4.28 do livro. Não interprete como efeito de tratamento controlado.", "Ein-Gruppen-Veränderung als Post-Messung minus Prä-Messung: Borenstein et al. (2021), Kap. 4, S. 28–29; metafor escalc SMCR/SMCRH/SMCC. Zitieren Sie entsprechend dem gespeicherten variance_method Becker (1988), Bonett (2008) oder Gibbons et al. (1993); die SMCR-Varianz folgt metafor und nicht Gl. 4.28 des Buches. Nicht als kontrollierter Behandlungseffekt interpretieren.", "Промена од мерења пре до мерења после у једној групи: Borenstein и сар. (2021), погл. 4, стр. 28–29; metafor escalc SMCR/SMCRH/SMCC. Према сачуваном variance_method, цитирајте Becker (1988), Bonett (2008) или Gibbons и сар. (1993); варијанса SMCR прати metafor, а не формулу 4.28 из књиге. Не тумачите резултат као контролисани ефекат лечења.", "단일군 사후−사전 변화: Borenstein 등(2021) 제4장, pp. 28–29; metafor escalc의 SMCR/SMCRH/SMCC. 저장된 variance_method에 따라 Becker(1988), Bonett(2008) 또는 Gibbons 등(1993)을 인용하세요. SMCR 분산은 책의 식 4.28이 아니라 metafor를 따릅니다. 대조군이 있는 치료 효과로 해석하지 마세요."
  ],
  [
    "未校正的对数均值比：Hedges 等（1999），DOI 10.1890/0012-9658(1999)080[1150:TMAORR]2.0.CO;2；Borenstein 等（2021）第 4 章，第 30–31 页。请核对原文并引用实际方法。", "Uncorrected log ratio of means: Hedges et al. (1999), DOI 10.1890/0012-9658(1999)080[1150:TMAORR]2.0.CO;2; Borenstein et al. (2021), ch. 4, pp. 30–31. Verify the original sources and cite the method used.", "Rapport logarithmique des moyennes non corrigé : Hedges et al. (1999), DOI 10.1890/0012-9658(1999)080[1150:TMAORR]2.0.CO;2 ; Borenstein et al. (2021), ch. 4, pp. 30–31. Vérifiez les sources originales et citez la méthode utilisée.", "Некорректированное логарифмическое отношение средних: Hedges и соавт. (1999), DOI 10.1890/0012-9658(1999)080[1150:TMAORR]2.0.CO;2; Borenstein и соавт. (2021), гл. 4, с. 30–31. Сверьтесь с первоисточниками и укажите в ссылках фактически использованный метод.", "Razón logarítmica de medias sin corregir: Hedges et al. (1999), DOI 10.1890/0012-9658(1999)080[1150:TMAORR]2.0.CO;2; Borenstein et al. (2021), cap. 4, pp. 30–31. Verifique las fuentes originales y cite el método utilizado.", "未補正の対数平均比：Hedges ら（1999）、DOI 10.1890/0012-9658(1999)080[1150:TMAORR]2.0.CO;2；Borenstein ら（2021）第4章、pp. 30–31。原典を確認し、実際に使用した方法を引用してください。", "Razão logarítmica de médias não corrigida: Hedges et al. (1999), DOI 10.1890/0012-9658(1999)080[1150:TMAORR]2.0.CO;2; Borenstein et al. (2021), cap. 4, pp. 30–31. Confira as fontes originais e cite o método utilizado.", "Unkorrigiertes logarithmisches Mittelwertverhältnis: Hedges et al. (1999), DOI 10.1890/0012-9658(1999)080[1150:TMAORR]2.0.CO;2; Borenstein et al. (2021), Kap. 4, S. 30–31. Prüfen Sie die Originalquellen und zitieren Sie die tatsächlich verwendete Methode.", "Некориговани логаритам односа средина: Hedges и сар. (1999), DOI 10.1890/0012-9658(1999)080[1150:TMAORR]2.0.CO;2; Borenstein и сар. (2021), погл. 4, стр. 30–31. Проверите изворне радове и цитирајте стварно коришћену методу.", "보정하지 않은 로그 평균비: Hedges 등(1999), DOI 10.1890/0012-9658(1999)080[1150:TMAORR]2.0.CO;2; Borenstein 등(2021) 제4장, pp. 30–31. 원문을 확인하고 실제 사용한 방법을 인용하세요."
  ],
  [
    "配对二分类结局：Curtin 等（2002），DOI 10.1002/sim.1206。请将配对估计与平行组估计分开，引用前核对原文。", "Paired binary outcomes: Curtin et al. (2002), DOI 10.1002/sim.1206. Keep paired and parallel estimates separate; verify the original source before citing.", "Critères binaires appariés : Curtin et al. (2002), DOI 10.1002/sim.1206. Distinguez les estimations appariées de celles des groupes parallèles et vérifiez la source originale avant de la citer.", "Парные бинарные исходы: Curtin и соавт. (2002), DOI 10.1002/sim.1206. Не объединяйте парные оценки с оценками для параллельных групп; перед цитированием сверьтесь с первоисточником.", "Desenlaces binarios pareados: Curtin et al. (2002), DOI 10.1002/sim.1206. Mantenga separadas las estimaciones pareadas y las de grupos paralelos, y verifique la fuente original antes de citarla.", "対応のある二値アウトカム：Curtin ら（2002）、DOI 10.1002/sim.1206。対応デザインの推定値は並行群の推定値と分け、引用前に原典を確認してください。", "Desfechos binários pareados: Curtin et al. (2002), DOI 10.1002/sim.1206. Mantenha as estimativas pareadas separadas das de grupos paralelos e confira a fonte original antes de citá-la.", "Gepaarte binäre Endpunkte: Curtin et al. (2002), DOI 10.1002/sim.1206. Gepaarte und Parallelgruppen-Schätzungen getrennt halten und vor dem Zitieren die Originalquelle prüfen.", "Упарени бинарни исходи: Curtin и сар. (2002), DOI 10.1002/sim.1206. Одвојте упарене процене од процена за паралелне групе и проверите изворни рад пре цитирања.", "대응 이분형 결과: Curtin 등(2002), DOI 10.1002/sim.1206. 대응 설계 추정치와 평행군 추정치를 분리하고 인용 전에 원문을 확인하세요."
  ],
  [
    "配对风险差的区间限制：Fagerland 等（2014），DOI 10.1002/sim.6148；该来源建议采用得分区间而非 Wald 区间。", "Paired risk difference interval caveat: Fagerland et al. (2014), DOI 10.1002/sim.6148; score-based intervals are recommended over Wald intervals.", "Limite pour l’intervalle de différence de risques appariée : Fagerland et al. (2014), DOI 10.1002/sim.6148 ; cette source recommande un intervalle score plutôt qu’un intervalle de Wald.", "Оговорка об интервале для парной разности рисков: Fagerland и соавт. (2014), DOI 10.1002/sim.6148; в этой работе вместо интервалов Wald рекомендуется использовать score-интервалы.", "Precaución sobre el intervalo de la diferencia de riesgos pareada: Fagerland et al. (2014), DOI 10.1002/sim.6148; la fuente recomienda un intervalo de puntuación en lugar de uno de Wald.", "対応のあるリスク差の区間に関する注意：Fagerland ら（2014）、DOI 10.1002/sim.6148。この出典は Wald 区間ではなくスコア区間を推奨しています。", "Ressalva sobre o intervalo da diferença de riscos pareada: Fagerland et al. (2014), DOI 10.1002/sim.6148; a fonte recomenda um intervalo baseado em escore em vez de um intervalo de Wald.", "Hinweis zum Intervall für gepaarte Risikodifferenzen: Fagerland et al. (2014), DOI 10.1002/sim.6148; diese Quelle empfiehlt Score-Intervalle statt Wald-Intervallen.", "Напомена о интервалу за упарену разлику ризика: Fagerland и сар. (2014), DOI 10.1002/sim.6148; овај извор препоручује score интервале уместо Wald интервала.", "대응 위험차 구간에 관한 주의사항: Fagerland 등(2014), DOI 10.1002/sim.6148. 이 출처는 Wald 구간 대신 점수 구간을 권장합니다."
  ],
  [
    "二分类 phi 系数及依赖边际分布的标准误：Bonett（2021），Statistical Methods for Psychologists，第 3 卷，第 3.4 节，第 79–82 页。请核对并引用原文。", "Binary phi coefficient and marginal-dependent SE: Bonett (2021), Statistical Methods for Psychologists, vol. 3, §3.4, pp. 79–82. Verify and cite the original text.", "Coefficient phi binaire et erreur-type dépendant des marges : Bonett (2021), Statistical Methods for Psychologists, vol. 3, §3.4, pp. 79–82. Vérifiez et citez le texte original.", "Бинарный коэффициент φ и SE, зависящая от маргинальных распределений: Bonett (2021), Statistical Methods for Psychologists, т. 3, § 3.4, с. 79–82. Сверьтесь с первоисточником и укажите ссылку.", "Coeficiente phi binario y EE dependiente de los marginales: Bonett (2021), Statistical Methods for Psychologists, vol. 3, §3.4, pp. 79–82. Verifique y cite el texto original.", "二値 phi 係数と周辺割合に依存する標準誤差：Bonett（2021）, Statistical Methods for Psychologists, vol. 3, §3.4, pp. 79–82。原文を確認し、引用してください。", "Coeficiente phi binário e EP dependente das margens: Bonett (2021), Statistical Methods for Psychologists, vol. 3, §3.4, pp. 79–82. Confira e cite o texto original.", "Binärer Phi-Koeffizient und von den Randverteilungen abhängiger SE: Bonett (2021), Statistical Methods for Psychologists, Bd. 3, § 3.4, S. 79–82. Prüfen und zitieren Sie die Originalquelle.", "Бинарни пхи коефицијент и SE која зависи од маргиналних расподела: Bonett (2021), Statistical Methods for Psychologists, том 3, § 3.4, стр. 79–82. Проверите и цитирајте изворни текст.", "이분형 phi 계수와 주변 비율에 의존하는 표준오차: Bonett(2021), Statistical Methods for Psychologists, vol. 3, §3.4, pp. 79–82. 원문을 확인하고 인용하세요."
  ],
  [
    "近似四分相关：Bonett 与 Price（2005），DOI 10.3102/10769986030002213。请核对并引用原方法。", "Approximate tetrachoric correlation: Bonett & Price (2005), DOI 10.3102/10769986030002213. Verify and cite the original method.", "Corrélation tétrachorique approximative : Bonett et Price (2005), DOI 10.3102/10769986030002213. Vérifiez et citez la méthode originale.", "Приближенная тетрахорическая корреляция: Bonett и Price (2005), DOI 10.3102/10769986030002213. Сверьтесь с оригинальным описанием метода и укажите ссылку.", "Correlación tetracórica aproximada: Bonett y Price (2005), DOI 10.3102/10769986030002213. Verifique y cite el método original.", "近似四分相関：Bonett と Price（2005）、DOI 10.3102/10769986030002213。原法を確認し、引用してください。", "Correlação tetracórica aproximada: Bonett e Price (2005), DOI 10.3102/10769986030002213. Confira e cite o método original.", "Näherungsweise tetrachorische Korrelation: Bonett & Price (2005), DOI 10.3102/10769986030002213. Prüfen und zitieren Sie die ursprüngliche Methode.", "Приближна тетрахорична корелација: Bonett & Price (2005), DOI 10.3102/10769986030002213. Проверите и цитирајте изворну методу.", "근사 사분상관: Bonett와 Price(2005), DOI 10.3102/10769986030002213. 원래 방법을 확인하고 인용하세요."
  ],
  [
    "DerSimonian 与 Laird（1986），DOI 10.1016/0197-2456(86)90046-2。",
    "DerSimonian & Laird (1986), DOI 10.1016/0197-2456(86)90046-2"
  ],
  [
    "Paule 与 Mandel（1982），J. Res. Natl. Bur. Stand.，87：377–385。",
    "Paule & Mandel (1982), J. Res. Natl. Bur. Stand. 87:377–385"
  ],
  [
    "REML 异质性估计：Harrer 等（2022）第 4.1.2.1 节，第 102–103 页；metafor rma.uni；Cochrane Handbook 第 10 章。请核对原始方法。", "REML heterogeneity estimator: Harrer et al. (2022), §4.1.2.1, pp. 102–103; metafor rma.uni; Cochrane Handbook ch. 10; verify the original method text", "Estimateur REML de l’hétérogénéité : Harrer et al. (2022), §4.1.2.1, pp. 102–103 ; metafor rma.uni ; Cochrane Handbook, ch. 10. Vérifiez le texte méthodologique original.", "Оценка гетерогенности REML: Harrer и соавт. (2022), § 4.1.2.1, с. 102–103; metafor rma.uni; Cochrane Handbook, гл. 10. Сверьтесь с исходным описанием метода.", "Estimador REML de heterogeneidad: Harrer et al. (2022), §4.1.2.1, pp. 102–103; metafor rma.uni; Cochrane Handbook, cap. 10. Verifique el texto metodológico original.", "REML 異質性推定量：Harrer ら（2022）§4.1.2.1、pp. 102–103；metafor rma.uni；Cochrane Handbook 第10章。原法を確認してください。", "Estimador REML da heterogeneidade: Harrer et al. (2022), §4.1.2.1, pp. 102–103; metafor rma.uni; Cochrane Handbook, cap. 10. Confira o texto metodológico original.", "REML-Schätzer der Heterogenität: Harrer et al. (2022), § 4.1.2.1, S. 102–103; metafor rma.uni; Cochrane Handbook, Kap. 10. Prüfen Sie die ursprüngliche Methodenbeschreibung.", "REML процена хетерогености: Harrer и сар. (2022), § 4.1.2.1, стр. 102–103; metafor rma.uni; Cochrane Handbook, погл. 10. Проверите изворни опис методе.", "REML 이질성 추정량: Harrer 등(2022) §4.1.2.1, pp. 102–103; metafor rma.uni; Cochrane Handbook 제10장. 원래 방법을 확인하세요."
  ],
  [
    "修正 HKSJ：Harrer 等（2022）第 4 章；Knapp 与 Hartung（2003），DOI 10.1002/sim.1482。", "modified HKSJ: Harrer et al. (2022), ch. 4; Knapp & Hartung (2003), DOI 10.1002/sim.1482", "HKSJ modifié : Harrer et al. (2022), ch. 4 ; Knapp et Hartung (2003), DOI 10.1002/sim.1482.", "Модифицированный HKSJ: Harrer и соавт. (2022), гл. 4; Knapp и Hartung (2003), DOI 10.1002/sim.1482.", "HKSJ modificado: Harrer et al. (2022), cap. 4; Knapp y Hartung (2003), DOI 10.1002/sim.1482.", "修正 HKSJ：Harrer ら（2022）第4章；Knapp と Hartung（2003）、DOI 10.1002/sim.1482。", "HKSJ modificado: Harrer et al. (2022), cap. 4; Knapp e Hartung (2003), DOI 10.1002/sim.1482.", "Modifiziertes HKSJ: Harrer et al. (2022), Kap. 4; Knapp & Hartung (2003), DOI 10.1002/sim.1482.", "Модификовани HKSJ: Harrer и сар. (2022), погл. 4; Кнапп & Хартунг (2003), DOI 10.1002/sim.1482.", "수정 HKSJ: Harrer 등(2022) 제4장; Knapp과 Hartung(2003), DOI 10.1002/sim.1482."
  ],
  [
    "预测区间：Borenstein 等（2021）第 17 章。", "prediction interval: Borenstein et al. (2021), ch. 17", "Intervalle de prédiction : Borenstein et al. (2021), ch. 17.", "Предиктивный интервал: Borenstein и соавт. (2021), гл. 17.", "Intervalo de predicción: Borenstein et al. (2021), cap. 17.", "予測区間：Borenstein ら（2021）第17章。", "Intervalo de predição: Borenstein et al. (2021), cap. 17.", "Prädiktionsintervall: Borenstein et al. (2021), Kap. 17.", "Предикциони интервал: Borenstein и сар. (2021), погл. 17.", "예측 구간: Borenstein 등(2021) 제17장."
  ],
  [
    "上限不可用", "Upper bound unavailable", "Borne supérieure indisponible", "Верхняя граница недоступна", "Límite superior no disponible", "上限を取得できません", "Limite superior indisponível", "Obere Grenze nicht verfügbar", "Горња граница није доступна", "상한을 구할 수 없음"
  ],
  [
    "REML τ²：{0}；95% 轮廓区间：[{1}, {2}]；方差尺度：{3}。", "REML τ²: {0}; 95% profile interval: [{1}, {2}]; variance scale: {3}.", "REML τ² : {0} ; intervalle de profil à 95 % : [{1}, {2}] ; échelle de variance : {3}.", "REML τ²: {0}; 95%-й профильный интервал: [{1}, {2}]; шкала дисперсии: {3}.", "REML τ²: {0}; intervalo de perfil del 95 %: [{1}, {2}]; escala de varianza: {3}.", "REML τ²：{0}；95%プロファイル区間：[{1}, {2}]；分散尺度：{3}。", "REML τ²: {0}; intervalo de perfil de 95%: [{1}, {2}]; escala de variância: {3}.", "REML τ²: {0}; 95%-Profilintervall: [{1}, {2}]; Varianzskala: {3}.", "REML τ²: {0}; 95% профилни интервал: [{1}, {2}]; скала варијансе: {3}.", "REML τ²: {0}; 95% 프로파일 구간: [{1}, {2}]; 분산 척도: {3}."
  ],
  [
    "{0} 项独立研究。", "{0} independent studies.", "{0} études indépendantes.", "{0} независимых исследований.", "{0} estudios independientes.", "独立した研究 {0} 件。", "{0} estudos independentes.", "{0} unabhängige Studien.", "{0} независних студија.", "독립 연구 {0}건."
  ],
  [
    "请引用原始轮廓似然方法及研究报告。", "Cite the original profile likelihood method and study reports.", "Citez la méthode originale de vraisemblance profilée et les rapports d’étude.", "Укажите в ссылках исходный метод профильного правдоподобия и отчёты исследований.", "Cite el método original de verosimilitud perfilada y los informes de los estudios.", "プロファイル尤度法の原典と研究報告を引用してください。", "Cite o método original de verossimilhança perfilada e os relatórios dos estudos.", "Zitieren Sie die ursprüngliche Profil-Likelihood-Methode und die Studienberichte.", "Цитирајте изворну методу профилне веродостојности и извештаје студија.", "프로파일 우도 방법의 원문과 연구 보고서를 인용하세요."
  ],
  [
    "区间上限未收敛，不得将其报告为有限界限。", "The upper interval limit did not converge; do not report it as finite.", "La borne supérieure de l’intervalle n’a pas convergé ; ne la présentez pas comme une limite finie.", "Верхняя граница интервала не сошлась; не указывайте её как конечную.", "El límite superior del intervalo no convergió; no lo informe como un límite finito.", "区間上限が収束しませんでした。有限の上限として報告しないでください。", "O limite superior do intervalo não convergiu; não o relate como limite finito.", "Die obere Intervallgrenze ist nicht konvergiert; sie darf nicht als endlich angegeben werden.", "Горња граница интервала није конвергирала; не приказујте је као коначну.", "구간 상한이 수렴하지 않았습니다. 유한한 상한으로 보고하지 마세요."
  ],
  [
    "正在计算 τ² 轮廓区间…已等待 {0} 秒。", "Calculating the τ² profile interval… {0} seconds elapsed.", "Calcul de l’intervalle profilé de τ²… {0} secondes écoulées.", "Расчёт профильного интервала для τ²… прошло {0} с.", "Calculando el intervalo de perfil de τ²… han transcurrido {0} s.", "τ² プロファイル区間を計算中… {0} 秒経過。", "Calculando o intervalo de perfil de τ²… {0} s decorridos.", "τ²-Profilintervall wird berechnet… {0} Sekunden vergangen.", "Израчунавање профилног интервала за τ²… прошло је {0} с.", "τ² 프로파일 구간 계산 중… {0}초 경과."
  ],
  [
    "分析未完成，请查看错误详情。", "The analysis did not complete. Review the error details.", "L’analyse n’a pas abouti. Consultez les détails de l’erreur.", "Анализ не завершён. Просмотрите подробности ошибки.", "El análisis no se completó. Consulte los detalles del error.", "解析が完了しませんでした。エラーの詳細を確認してください。", "A análise não foi concluída. Consulte os detalhes do erro.", "Die Analyse wurde nicht abgeschlossen. Prüfen Sie die Fehlerdetails.", "Анализа није завршена. Погледајте детаље грешке.", "분석이 완료되지 않았습니다. 오류 세부정보를 확인하세요."
  ],
  [
    "固定模型；τ² 设为 0", "Fixed model; τ² set to 0", "Modèle fixe ; τ² fixé à 0", "Модель с фиксированным эффектом; τ² задано равным 0", "Modelo fijo; τ² fijada en 0", "固定効果モデル；τ² は 0 に設定", "Modelo fixo; τ² definido como 0", "Fixed-Effect-Modell; τ² auf 0 gesetzt", "Модел фиксног ефекта; τ² је постављено на 0", "고정효과 모형; τ²를 0으로 설정"
  ],
  [
    "{0} 项研究 · {1} · {2} · {3} 分析尺度", "{0} studies · {1} · {2} · {3} analysis scale", "{0} études · {1} · {2} · échelle d’analyse {3}", "{0} исследований · {1} · {2} · аналитическая шкала {3}", "{0} estudios · {1} · {2} · escala de análisis {3}", "研究 {0} 件 · {1} · {2} · {3} 解析尺度", "{0} estudos · {1} · {2} · escala de análise {3}", "{0} Studien · {1} · {2} · Analyseskala {3}", "{0} студија · {1} · {2} · аналитичка скала {3}", "연구 {0}건 · {1} · {2} · {3} 분석 척도"
  ],
  [
    "删除后合并效应", "Leave-one-out pooled effect", "Effet combiné en retirant une étude à la fois", "Объединённый эффект с исключением по одному исследованию", "Efecto combinado tras excluir un estudio cada vez", "1件除外法による統合効果", "Efeito combinado após excluir um estudo por vez", "Gepoolter Effekt nach Ausschluss jeweils einer Studie", "Обједињени ефекат након изостављања по једне студије", "1건씩 제외했을 때 통합 효과"
  ],
  [
    "删除后 τ²（{0}；{1} 方差尺度）", "Leave-one-out τ² ({0}; {1} variance scale)", "τ² après exclusion d’une étude ({0} ; échelle de variance {1})", "τ² после исключения исследования ({0}; шкала дисперсии {1})", "τ² al excluir un estudio ({0}; escala de varianza {1})", "1件除外法による τ²（{0}；{1} 分散尺度）", "τ² após excluir um estudo ({0}; escala de variância {1})", "τ² nach Ausschluss der Studie ({0}; Varianzskala {1})", "τ² након изостављања студије ({0}; скала варијансе {1})", "1건씩 제외했을 때의 τ²({0}; {1} 분산 척도)"
  ],
  [
    "标准化残差", "Standardized residual", "Résidu standardisé", "Стандартизованный остаток", "Residuo estandarizado", "標準化残差", "Resíduo padronizado", "Standardisiertes Residuum", "Стандардизовани остатак", "표준화 잔차"
  ],
  [
    "这些是模型诊断值，不是自动排除规则。解释较大数值前，请核对研究设计、所选结果和来源报告。", "These are model diagnostics, not automatic exclusion rules. Confirm the study design, selected result and source report before interpreting a large value.", "Il s’agit de diagnostics du modèle, pas de règles d’exclusion automatiques. Avant d’interpréter une valeur élevée, vérifiez le plan d’étude, le résultat sélectionné et le rapport source.", "Это диагностические показатели модели, а не автоматические правила исключения. Прежде чем интерпретировать большое значение, проверьте дизайн исследования, выбранный результат и исходный отчёт.", "Son diagnósticos del modelo, no reglas automáticas de exclusión. Antes de interpretar un valor alto, confirme el diseño del estudio, el resultado seleccionado y el informe fuente.", "これはモデル診断値であり、自動除外の基準ではありません。大きな値を解釈する前に、研究デザイン、選択した結果、原資料の報告を確認してください。", "São diagnósticos do modelo, não regras automáticas de exclusão. Antes de interpretar um valor alto, confira o desenho do estudo, o resultado selecionado e o relatório de origem.", "Dies sind Modelldiagnostiken und keine automatischen Ausschlussregeln. Prüfen Sie Studiendesign, ausgewähltes Ergebnis und Quellenbericht, bevor Sie einen großen Wert interpretieren.", "Ово су дијагностички показатељи модела, а не правила за аутоматско искључивање. Пре тумачења велике вредности проверите дизајн студије, изабрани резултат и изворни извештај.", "이 값은 모형 진단값이며 자동 제외 기준이 아닙니다. 큰 값을 해석하기 전에 연구 설계, 선택한 결과 및 원자료 보고서를 확인하세요."
  ],
  [
    "正在计算研究影响诊断…已等待 {0} 秒。", "Calculating study influence diagnostics… {0} seconds elapsed.", "Calcul des diagnostics d’influence des études… {0} secondes écoulées.", "Расчёт диагностик влияния отдельных исследований… прошло {0} с.", "Calculando los diagnósticos de influencia de los estudios… han transcurrido {0} s.", "研究の影響度診断を計算中… {0} 秒経過。", "Calculando os diagnósticos de influência dos estudos… {0} s decorridos.", "Einflussdiagnostiken für Studien werden berechnet… {0} Sekunden vergangen.", "Израчунавање дијагностике утицаја студија… прошло је {0} с.", "연구 영향 진단 계산 중… {0}초 경과."
  ],
  [
    "所选结果在比较过程中发生变化，请重新运行。", "Selected results changed during comparison; run it again.", "Les résultats sélectionnés ont changé pendant la comparaison ; relancez l’analyse.", "Во время сравнения выбранные результаты изменились. Запустите сравнение ещё раз.", "Los resultados seleccionados cambiaron durante la comparación; vuelva a ejecutar el análisis.", "比較中に選択した結果が変更されました。もう一度実行してください。", "Os resultados selecionados mudaram durante a comparação; execute a análise novamente.", "Die ausgewählten Ergebnisse haben sich während des Vergleichs geändert. Führen Sie den Vergleich erneut aus.", "Изабрани резултати су се променили током поређења. Поново покрените поређење.", "비교 중 선택한 결과가 변경되었습니다. 다시 실행하세요."
  ],
  [
    "分析",
    "analysis"
  ],
  [
    "来源", "Source", "Source", "Источник", "Fuente", "出典", "Fonte", "Quelle", "Извор", "출처"
  ],
  /* W17 批次追加：W15 GRADE + W16 撰稿/活体综述/证据地图词条（十语言；只追加） */
  [
    "GRADE 评估",
    "GRADE assessment",
    "Évaluation GRADE",
    "Оценка GRADE",
    "Evaluación GRADE",
    "GRADE 評価",
    "Avaliação GRADE",
    "GRADE-Bewertung",
    "GRADE оцена",
    "GRADE 평가"
  ],
  [
    "按 GRADE 框架评估每个结局的证据质量（高→中→低→极低）。[自动建议] 从已保存的合成/RoB/发表偏倚结果推导降级因子；间接性无法自动推导，须人工判断。修改预填值会标记「已覆盖」并记入备注。",
    "Assess the certainty of the evidence per outcome with the GRADE framework (high→moderate→low→very low). [Auto-suggest] infers downgrade factors from saved synthesis/RoB/publication-bias results; indirectness cannot be inferred automatically and needs human judgement. Editing a prefilled value marks it “overridden” and is recorded in the notes.",
    "Évaluez la certitude des preuves pour chaque critère selon GRADE (élevée → modérée → faible → très faible). [Suggestion automatique] déduit les facteurs de déclassement des résultats enregistrés (synthèse/RoB/biais de publication) ; le caractère indirect ne peut pas être déduit automatiquement et exige un jugement humain. Modifier une valeur préremplie la marque « remplacée » et l’inscrit dans les notes.",
    "Оценивайте уверенность в доказательствах по каждому исходу по системе GRADE (высокая → средняя → низкая → очень низкая). [Автоподсказка] выводит факторы снижения из сохранённых результатов синтеза/RoB/публикационного смещения; косвенность автоматически не выводится и требует экспертной оценки. Изменение предзаполненного значения помечается как «переопределено» и фиксируется в примечании.",
    "Evalúe con GRADE la certeza de la evidencia para cada desenlace (alta → moderada → baja → muy baja). [Sugerencia automática] infiere los factores de degradación a partir de los resultados guardados de síntesis/riesgo de sesgo/sesgo de publicación; el carácter indirecto de la evidencia no puede inferirse automáticamente y requiere juicio humano. Al modificar un valor prerrellenado, se marca como «Modificado manualmente» y se registra en las notas.",
    "GRADE フレームワークで各アウトカムのエビデンスの確実性を評価します（高→中→低→極低）。[自動提案] は保存済みの合成/RoB/出版バイアス結果から引き下げ因子を推定します。間接性は自動推定できず、人的判断が必要です。初期値を変更すると「上書き済み」と表示され、備考に記録されます。",
    "Avalie com o GRADE a certeza da evidência por desfecho (alta → moderada → baixa → muito baixa). [Sugestão automática] infere os fatores de rebaixamento dos resultados salvos de síntese/risco de viés/viés de publicação; a indireção não pode ser inferida automaticamente e exige julgamento humano. Alterar um valor pré-preenchido o marca como “substituído” e registra em notas.",
    "Bewerten Sie die Sicherheit der Evidenz je Endpunkt nach GRADE (hoch → moderat → niedrig → sehr niedrig). [Automatisch vorschlagen] leitet Abwertungsfaktoren aus gespeicherten Ergebnissen von Synthese/Biasrisiko/Publication-Bias ab; Indirektheit kann nicht automatisch abgeleitet werden und erfordert menschliches Urteil. Eine geänderte Voreinstellung wird als „überschrieben“ markiert und in den Notizen vermerkt.",
    "Оцените поузданост доказа за сваки исход кроз GRADE оквир (висока → умерена → ниска → врло ниска). [Аутоматски предлог] изводи факторе снижавања из сачуваних резултата синтезе/ризика пристрасности/пристрасности објављивања; посредност се не може аутоматски извести и захтева људску оцену. Измена унапред унете вредности означава се као „ручно измењена“ и бележи у напоменама.",
    "GRADE 체계로 각 결과의 근거 확실성을 평가합니다(높음→중간→낮음→매우 낮음). [자동 제안]은 저장된 합성/비뚤림 위험/출판 편향 결과에서 강등 요인을 추론합니다. 간접성은 자동 추론이 불가하여 사람의 판단이 필요합니다. 미리 채워진 값을 수정하면 “재정의됨”으로 표시되고 메모에 기록됩니다."
  ],
  [
    "导出 SoF 表",
    "Export SoF table",
    "Exporter le tableau SoF",
    "Экспортировать таблицу SoF",
    "Exportar tabla SoF",
    "SoF 表を書き出す",
    "Exportar tabela SoF",
    "SoF-Tabelle exportieren",
    "Извези SoF табелу",
    "SoF 표 내보내기"
  ],
  [
    "尚无已保存的效应量——先在「效应量录入」保存结果，再回到这里做 GRADE 评估。",
    "No saved effect sizes yet — save results under “Effect entry” first, then come back for the GRADE assessment.",
    "Aucune taille d’effet enregistrée pour l’instant — enregistrez d’abord des résultats dans « Saisie des effets », puis revenez pour l’évaluation GRADE.",
    "Сохранённых величин эффекта пока нет — сначала сохраните результаты в разделе «Ввод эффектов», затем вернитесь для оценки GRADE.",
    "Aún no hay tamaños del efecto guardados: guarde primero resultados en «Entrada de efectos» y vuelva aquí para la evaluación GRADE.",
    "保存済みの効果量がありません。先に「効果量の入力」で結果を保存してから、ここで GRADE 評価を行ってください。",
    "Ainda não há tamanhos de efeito salvos — salve resultados em “Entrada de efeitos” e volte aqui para a avaliação GRADE.",
    "Noch keine gespeicherten Effektgrößen — speichern Sie zuerst Ergebnisse unter „Effekteingabe“ und kehren Sie dann für die GRADE-Bewertung zurück.",
    "Још нема сачуваних величина ефекта — прво сачувајте резултате у „Унос ефеката“, па се вратите за GRADE оцену.",
    "아직 저장된 효과 크기가 없습니다. 먼저 “효과 입력”에서 결과를 저장한 뒤 여기로 돌아와 GRADE 평가를 하세요."
  ],
  [
    "已覆盖",
    "Overridden",
    "Remplacé",
    "Переопределено",
    "Modificado manualmente",
    "上書き済み",
    "Substituído",
    "Überschrieben",
    "Ручно измењено",
    "재정의됨"
  ],
  [
    "（未命名结局）",
    "(unnamed outcome)",
    "(critère de jugement sans nom)",
    "(исход без названия)",
    "(desenlace sin nombre)",
    "（無名アウトカム）",
    "(desfecho sem nome)",
    "(unbenannter Endpunkt)",
    "(неименовани исход)",
    "(이름 없는 결과)"
  ],
  [
    "时间点：{0}",
    "Time point: {0}",
    "Moment de mesure : {0}",
    "Момент измерения: {0}",
    "Momento de medición: {0}",
    "測定時点：{0}",
    "Momento da medição: {0}",
    "Messzeitpunkt: {0}",
    "Време мерења: {0}",
    "측정 시점: {0}"
  ],
  [
    "研究设计与初始质量",
    "Study design and starting certainty",
    "Design d’étude et certitude initiale",
    "Дизайн исследования и исходная уверенность",
    "Diseño del estudio y certeza inicial",
    "研究デザインと初期の確実性",
    "Desenho do estudo e certeza inicial",
    "Studiendesign und Ausgangssicherheit",
    "Дизајн студије и почетна поузданост",
    "연구 설계와 초기 확실성"
  ],
  [
    "结局重要性",
    "Outcome importance",
    "Importance du critère de jugement",
    "Важность исхода",
    "Importancia del desenlace",
    "アウトカムの重要性",
    "Importância do desfecho",
    "Bedeutung des Endpunkts",
    "Значај исхода",
    "결과의 중요도"
  ],
  [
    "观察性升级因子",
    "Upgrade factors for observational evidence",
    "Facteurs de rehaussement (études observationnelles)",
    "Факторы повышения для наблюдательных исследований",
    "Factores de mejora (estudios observacionales)",
    "観察研究の引き上げ因子",
    "Fatores de melhoria (estudos observacionais)",
    "Aufwertungsfaktoren für Beobachtungsstudien",
    "Фактори подизања за опсервационе студије",
    "관찰 연구 상향 요인"
  ],
  [
    "最终质量",
    "Final certainty",
    "Certitude finale",
    "Итоговая уверенность",
    "Certeza final",
    "最終的な確実性",
    "Certeza final",
    "Finale Sicherheit",
    "Финална поузданост",
    "최종 확실성"
  ],
  [
    "覆盖备注",
    "Override notes",
    "Notes de remplacement",
    "Примечания о переопределении",
    "Notas de modificación manual",
    "上書き備考",
    "Notas de substituição",
    "Überschreibungsnotizen",
    "Напомене о прегажењу",
    "재정의 메모"
  ],
  ["覆盖备注（可选）", "Override notes (optional)", "Remarque sur la modification de l’évaluation (facultatif)", "Примечание к изменённой оценке (необязательно)", "Nota sobre la modificación manual (opcional)", "評価の変更に関する備考（任意）", "Nota sobre a avaliação alterada (opcional)", "Anmerkung zur geänderten Bewertung (optional)", "Напомена о измењеној процени (опционо)", "평가 변경 메모(선택 사항)"],
  [
    "自动建议",
    "Auto-suggest",
    "Suggestion automatique",
    "Автоподсказка",
    "Sugerir automáticamente",
    "自動提案",
    "Sugestão automática",
    "Automatisch vorschlagen",
    "Аутоматски предлог",
    "자동 제안"
  ],
  [
    "保存评估",
    "Save assessment",
    "Enregistrer l’évaluation",
    "Сохранить оценку",
    "Guardar evaluación",
    "評価を保存",
    "Salvar avaliação",
    "Bewertung speichern",
    "Сачувај оцену",
    "평가 저장"
  ],
  [
    "初始 {0} − 降级 {1} + 升级 {2} → 最终 {3}",
    "Start {0} − downgrade {1} + upgrade {2} → final {3}",
    "Départ {0} − déclassement {1} + rehaussement {2} → final {3}",
    "Старт {0} − снижение {1} + повышение {2} → итог {3}",
    "Inicio {0} − degradación {1} + mejora {2} → final {3}",
    "初期 {0} − 引き下げ {1} + 引き上げ {2} → 最終 {3}",
    "Início {0} − rebaixamento {1} + melhoria {2} → final {3}",
    "Start {0} − Abwertung {1} + Aufwertung {2} → Ende {3}",
    "Почетак {0} − снижавање {1} + подизање {2} → финално {3}",
    "시작 {0} − 강등 {1} + 상향 {2} → 최종 {3}"
  ],
  [
    "不一致性",
    "Inconsistency",
    "Incohérence",
    "Несогласованность",
    "Inconsistencia",
    "不整合性",
    "Inconsistência",
    "Inkonsistenz",
    "Недоследност",
    "불일치성"
  ],
  [
    "间接性",
    "Indirectness",
    "Caractère indirect",
    "Косвенность",
    "Evidencia indirecta",
    "間接性",
    "Indireção",
    "Indirektheit",
    "Индиректност",
    "간접성"
  ],
  [
    "不精确性", "Imprecision", "Imprécision", "Неточность", "Imprecisión", "不精確性", "Imprecisão", "Unpräzision", "Непрецизност", "불정확성"
  ],
  [
    "发表偏倚",
    "Publication bias",
    "Biais de publication",
    "Публикационное смещение",
    "Sesgo de publicación",
    "出版バイアス",
    "Viés de publicação",
    "Publication-Bias",
    "Пристрасност објављивања",
    "출판 편향"
  ],
  [
    "大效应",
    "Large effect",
    "Grand effet",
    "Большой эффект",
    "Gran efecto",
    "大きな効果",
    "Grande efeito",
    "Großer Effekt",
    "Велик ефекат",
    "큰 효과"
  ],
  [
    "剂量反应",
    "Dose response",
    "Relation dose-réponse",
    "Доза-эффект",
    "Relación dosis-respuesta",
    "用量反応",
    "Relação dose-resposta",
    "Dosis-Wirkungs-Beziehung",
    "Однос доза-одговор",
    "용량-반응"
  ],
  [
    "合理混杂",
    "Plausible confounding",
    "Facteurs de confusion plausibles",
    "Правдоподобное смешение",
    "Confusión plausible",
    "もっともらしい交絡",
    "Confusão plausível",
    "Plausible Störgrößen",
    "Веродостојни збуњујући фактори",
    "개연성 있는 교란"
  ],
  [
    "无降级",
    "No downgrade",
    "Aucun déclassement",
    "Без снижения",
    "Sin degradación",
    "引き下げなし",
    "Sem rebaixamento",
    "Keine Abwertung",
    "Без снижавања",
    "강등 없음"
  ],
  [
    "严重(-1)",
    "Serious (-1)",
    "Sérieux (-1)",
    "Серьёзно (-1)",
    "Serio (-1)",
    "重大（-1）",
    "Sério (-1)",
    "Ernst (-1)",
    "Озбиљно (-1)",
    "심각함(-1)"
  ],
  [
    "非常严重(-2)",
    "Very serious (-2)",
    "Très sérieux (-2)",
    "Очень серьёзно (-2)",
    "Muy serio (-2)",
    "非常に重大（-2）",
    "Muito sério (-2)",
    "Sehr ernst (-2)",
    "Врло озбиљно (-2)",
    "매우 심각함(-2)"
  ],
  [
    "极其严重(-3)",
    "Extremely serious (-3)",
    "Extrêmement sérieux (-3)",
    "Крайне серьёзно (-3)",
    "Extremadamente serio (-3)",
    "極めて重大（-3）",
    "Extremamente sério (-3)",
    "Äußerst ernst (-3)",
    "Изузетно озбиљно (-3)",
    "극도로 심각함(-3)"
  ],
  [
    "无升级",
    "No upgrade",
    "Aucun rehaussement",
    "Без повышения",
    "Sin mejora",
    "引き上げなし",
    "Sem melhoria",
    "Keine Aufwertung",
    "Без подизања",
    "상향 없음"
  ],
  [
    "升级(+1)",
    "Upgrade (+1)",
    "Rehaussement (+1)",
    "Повышение (+1)",
    "Mejora (+1)",
    "引き上げ（+1）",
    "Melhoria (+1)",
    "Aufwertung (+1)",
    "Подизање (+1)",
    "상향(+1)"
  ],
  [
    "升级(+2)",
    "Upgrade (+2)",
    "Rehaussement (+2)",
    "Повышение (+2)",
    "Mejora (+2)",
    "引き上げ（+2）",
    "Melhoria (+2)",
    "Aufwertung (+2)",
    "Подизање (+2)",
    "상향(+2)"
  ],
  [
    "RCT（初始质量：高）",
    "RCT (starting certainty: high)",
    "ECR (certitude initiale : élevée)",
    "РКИ (исходная уверенность: высокая)",
    "ECA (certeza inicial: alta)",
    "RCT（初期の確実性：高）",
    "ECR (certeza inicial: alta)",
    "RCT (Ausgangssicherheit: hoch)",
    "РЦТ (почетна поузданост: висока)",
    "RCT(초기 확실성: 높음)"
  ],
  [
    "观察性研究（初始质量：低）",
    "Observational (starting certainty: low)",
    "Observationnel (certitude initiale : faible)",
    "Наблюдательное (исходная уверенность: низкая)",
    "Observacional (certeza inicial: baja)",
    "観察研究（初期の確実性：低）",
    "Observacional (certeza inicial: baixa)",
    "Beobachtungsstudie (Ausgangssicherheit: niedrig)",
    "Опсервациона (почетна поузданост: ниска)",
    "관찰 연구(초기 확실성: 낮음)"
  ],
  [
    "诊断准确性（初始质量：高）",
    "Diagnostic accuracy (starting certainty: high)",
    "Précision diagnostique (certitude initiale : élevée)",
    "Диагностическая точность (исходная уверенность: высокая)",
    "Exactitud diagnóstica (certeza inicial: alta)",
    "診断精度（初期の確実性：高）",
    "Acurácia diagnóstica (certeza inicial: alta)",
    "Diagnostische Genauigkeit (Ausgangssicherheit: hoch)",
    "Дијагностичка тачност (почетна поузданост: висока)",
    "진단 정확도(초기 확실성: 높음)"
  ],
  [
    "中",
    "Moderate",
    "Modérée",
    "Средняя",
    "Moderada",
    "中",
    "Moderada",
    "Moderat",
    "Умерена",
    "중간"
  ],
  [
    "极低",
    "Very low",
    "Très faible",
    "Очень низкая",
    "Muy baja",
    "極めて低い",
    "Muito baixa",
    "Sehr niedrig",
    "Врло ниска",
    "매우 낮음"
  ],
  [
    "关键结局",
    "Critical outcome",
    "Critère de jugement critique",
    "Критический исход",
    "Desenlace crítico",
    "クリティカルなアウトカム",
    "Desfecho crítico",
    "Kritischer Endpunkt",
    "Критични исход",
    "핵심 결과"
  ],
  [
    "重要结局",
    "Important outcome",
    "Critère de jugement important",
    "Важный исход",
    "Desenlace importante",
    "重要なアウトカム",
    "Desfecho importante",
    "Wichtiger Endpunkt",
    "Важан исход",
    "중요 결과"
  ],
  [
    "不重要结局",
    "Outcome of limited importance",
    "Critère de jugement peu important",
    "Исход ограниченной важности",
    "Desenlace de poca importancia",
    "重要でないアウトカム",
    "Desfecho de pouca importância",
    "Endpunkt von begrenzter Bedeutung",
    "Малозначајан исход",
    "덜 중요한 결과"
  ],
  [
    "GRADE 证据概要（Summary of Findings）",
    "GRADE Summary of Findings",
    "Résumé GRADE des preuves (Summary of Findings)",
    "Краткое изложение доказательств GRADE (Summary of Findings)",
    "Resumen GRADE de la evidencia (Summary of Findings)",
    "GRADE エビデンス概要（Summary of Findings）",
    "Resumo GRADE da evidência (Summary of Findings)",
    "GRADE-Zusammenfassung der Evidenz (Summary of Findings)",
    "GRADE преглед доказа (Summary of Findings)",
    "GRADE 근거 요약(Summary of Findings)"
  ],
  [
    "下载 SoF CSV",
    "Download SoF CSV",
    "Télécharger le CSV SoF",
    "Скачать SoF CSV",
    "Descargar CSV SoF",
    "SoF CSV をダウンロード",
    "Baixar CSV SoF",
    "SoF-CSV herunterladen",
    "Преузми SoF CSV",
    "SoF CSV 내려받기"
  ],
  [
    "{0}：无信号。",
    "{0}: no signal.",
    "{0} : aucun signal.",
    "{0}: нет сигнала.",
    "{0}: sin señal.",
    "{0}：シグナルなし。",
    "{0}: sem sinal.",
    "{0}: kein Signal.",
    "{0}: нема сигнала.",
    "{0}: 신호 없음."
  ],
  [
    "无数据，需人工判断",
    "No data; needs human judgement",
    "Pas de données ; jugement humain requis",
    "Нет данных; требуется экспертная оценка",
    "Sin datos; requiere juicio humano",
    "データなし。人的判断が必要です",
    "Sem dados; exige julgamento humano",
    "Keine Daten; menschliches Urteil erforderlich",
    "Нема података; потребна људска процена",
    "데이터 없음, 사람의 판단 필요"
  ],
  [
    "规则 {0}",
    "Rule {0}",
    "Règle {0}",
    "Правило {0}",
    "Regla {0}",
    "ルール {0}",
    "Regra {0}",
    "Regel {0}",
    "Правило {0}",
    "규칙 {0}"
  ],
  [
    "{0}: {1} → {2}",
    "{0}: {1} → {2}",
    "{0} : {1} → {2}",
    "{0}: {1} → {2}",
    "{0}: {1} → {2}",
    "{0}: {1} → {2}",
    "{0}: {1} → {2}",
    "{0}: {1} → {2}",
    "{0}: {1} → {2}",
    "{0}: {1} → {2}"
  ],
  [
    "{0}: {1}→{2}",
    "{0}: {1}→{2}",
    "{0} : {1}→{2}",
    "{0}: {1}→{2}",
    "{0}: {1}→{2}",
    "{0}: {1}→{2}",
    "{0}: {1}→{2}",
    "{0}: {1}→{2}",
    "{0}: {1}→{2}",
    "{0}: {1}→{2}"
  ],
  ["该行缺少比较方向、结局或时间点，无法生成建议。", "This row is missing a comparison, outcome or time point, so a suggestion cannot be generated.", "Il manque à cette ligne une comparaison, un critère de jugement ou un moment de mesure ; aucune suggestion ne peut être générée.", "В строке не указаны сравнение, исход или момент измерения, поэтому предложение сформировать нельзя.", "Falta la comparación, el resultado o el momento de medición, por lo que no se puede generar una sugerencia.", "比較、アウトカム、測定時点のいずれかが入力されていないため、提案を作成できません。", "Falta a comparação, o desfecho ou o momento da medição; não é possível gerar uma sugestão.", "In dieser Zeile fehlen Vergleich, Endpunkt oder Messzeitpunkt; es kann kein Vorschlag erstellt werden.", "У реду недостају поређење, исход или време мерења, па предлог није могуће направити.", "이 행에 비교, 결과 또는 측정 시점이 없어 제안할 수 없습니다."],
  [
    "正在推导自动建议…",
    "Deriving auto-suggestions…",
    "Calcul des suggestions automatiques…",
    "Формирование автоподсказок…",
    "Calculando sugerencias automáticas…",
    "自動提案を生成中…",
    "Derivando sugestões automáticas…",
    "Automatische Vorschläge werden abgeleitet…",
    "Извођење аутоматских предлога…",
    "자동 제안을 도출하는 중…"
  ],
  [
    "合成口径：{0} 模型 · {1} 项研究 · 合并效应 {2}（95% CI {3} 至 {4}）· I²={5}%",
    "Synthesis basis: {0} model · {1} studies · pooled effect {2} (95% CI {3} to {4}) · I²={5}%",
    "Base de la synthèse : modèle {0} · {1} études · effet combiné {2} (IC à 95 % {3} à {4}) · I²={5} %",
    "Основа синтеза: модель {0} · исследований {1} · объединённый эффект {2} (95% ДИ {3}–{4}) · I²={5}%",
    "Base de síntesis: modelo {0} · {1} estudios · efecto combinado {2} (IC del 95 % {3} a {4}) · I²={5} %",
    "合成の前提：{0} モデル・{1} 研究・統合効果 {2}（95% CI {3}〜{4}）・I²={5}%",
    "Base da síntese: modelo {0} · {1} estudos · efeito combinado {2} (IC 95% {3} a {4}) · I²={5}%",
    "Synthesebasis: {0}-Modell · {1} Studien · Gesamteffekt {2} (95-%-KI {3} bis {4}) · I²={5} %",
    "Основа синтезе: модел {0} · {1} студија · обједињени ефекат {2} (95% ИП {3} до {4}) · I²={5}%",
    "합성 기준: {0} 모형 · 연구 {1}건 · 통합 효과 {2}(95% CI {3}~{4}) · I²={5}%"
  ],
  [
    "发表偏倚检验不可用（研究数不足或单组指标），该因子需人工评估。",
    "The publication-bias test is unavailable (too few studies or a single-arm measure); assess this factor manually.",
    "Le test de biais de publication est indisponible (études insuffisantes ou mesure à un seul groupe) ; évaluez ce facteur manuellement.",
    "Тест публикационного смещения недоступен (слишком мало исследований или одногрупповый показатель); оцените этот фактор вручную.",
    "La prueba de sesgo de publicación no está disponible (demasiados pocos estudios o una medida de un solo grupo); evalúe este factor manualmente.",
    "出版バイアス検定は利用できません（研究数不足または単群指標）。この因子は手動で評価してください。",
    "O teste de viés de publicação não está disponível (poucos estudos ou medida de grupo único); avalie este fator manualmente.",
    "Der Publication-Bias-Test ist nicht verfügbar (zu wenige Studien oder Ein-Gruppen-Maß); bewerten Sie diesen Faktor manuell.",
    "Тест пристрасности објављивања није доступан (премало студија или једногрупна мера); оцените овај фактор ручно.",
    "출판 편향 검정을 사용할 수 없습니다(연구 수 부족 또는 단일군 지표). 이 요인은 직접 평가하세요."
  ],
  [
    "以上为自动建议；间接性完全依赖人工判断。请逐项确认或覆盖。",
    "These are auto-suggestions; indirectness relies entirely on human judgement. Confirm or override each item.",
    "Ce sont des suggestions automatiques ; le caractère indirect repose entièrement sur le jugement humain. Confirmez ou remplacez chaque item.",
    "Это автоподсказки; косвенность полностью зависит от экспертной оценки. Подтвердите или переопределите каждый пункт.",
    "Estas son sugerencias automáticas; la evaluación de la evidencia indirecta depende por completo del juicio humano. Confirme o modifique cada elemento.",
    "これらは自動提案です。間接性は完全に人的判断に依存します。各項目を確認または上書きしてください。",
    "Estas são sugestões automáticas; a indireção depende inteiramente de julgamento humano. Confirme ou substitua cada item.",
    "Dies sind automatische Vorschläge; Indirektheit hängt ganz vom menschlichen Urteil ab. Bestätigen oder überschreiben Sie jeden Punkt.",
    "Ово су аутоматски предлози; посредност у потпуности зависи од људске оцене. Потврдите или ручно измените сваку ставку.",
    "자동 제안입니다. 간접성은 전적으로 사람의 판단에 의존합니다. 각 항목을 확인하거나 재정의하세요."
  ],
  [
    "已按自动建议预填（初始质量：{0}，建议最终质量：{1}）。",
    "Prefilled from auto-suggestions (starting certainty: {0}; suggested final certainty: {1}).",
    "Prérempli d’après les suggestions automatiques (certitude initiale : {0} ; certitude finale suggérée : {1}).",
    "Предзаполнено по автоподсказкам (исходная уверенность: {0}; предложенная итоговая: {1}).",
    "Prerrellenado con las sugerencias automáticas (certeza inicial: {0}; certeza final sugerida: {1}).",
    "自動提案に基づいて初期値を設定しました（初期の確実性：{0}、提案される最終確実性：{1}）。",
    "Pré-preenchido com as sugestões automáticas (certeza inicial: {0}; certeza final sugerida: {1}).",
    "Aus automatischen Vorschlägen vorbelegt (Ausgangssicherheit: {0}; vorgeschlagene finale Sicherheit: {1}).",
    "Унапред унето по аутоматским предлозима (почетна поузданост: {0}; предложена финална: {1}).",
    "자동 제안으로 미리 채웠습니다(초기 확실성: {0}, 제안된 최종 확실성: {1})."
  ],
  [
    "自动建议失败：",
    "Auto-suggest failed: ",
    "Échec de la suggestion automatique : ",
    "Ошибка автоподсказки: ",
    "Error de la sugerencia automática: ",
    "自動提案に失敗：",
    "Falha na sugestão automática: ",
    "Automatischer Vorschlag fehlgeschlagen: ",
    "Аутоматски предлог није успео: ",
    "자동 제안 실패: "
  ],
  ["该行缺少比较方向、结局或时间点，无法保存。请补全后重试。", "This row is missing a comparison, outcome or time point and cannot be saved. Complete the missing fields and try again.", "Il manque à cette ligne une comparaison, un critère de jugement ou un moment de mesure ; elle ne peut pas être enregistrée. Complétez les champs manquants et réessayez.", "В строке не указаны сравнение, исход или момент измерения, поэтому сохранить её нельзя. Заполните недостающие поля и повторите попытку.", "Falta la comparación, el resultado o el momento de medición, por lo que no se puede guardar. Complete los campos y vuelva a intentarlo.", "比較、アウトカム、測定時点のいずれかが入力されていないため保存できません。不足項目を入力して再試行してください。", "Falta a comparação, o desfecho ou o momento da medição, então não é possível salvar. Preencha os campos e tente novamente.", "In dieser Zeile fehlen Vergleich, Endpunkt oder Messzeitpunkt. Ergänzen Sie die fehlenden Angaben und versuchen Sie es erneut.", "У реду недостају поређење, исход или време мерења, па чување није могуће. Попуните поља и покушајте поново.", "이 행에 비교, 결과 또는 측정 시점이 없어 저장할 수 없습니다. 누락된 항목을 입력한 뒤 다시 시도하세요."],
  [
    "正在保存 GRADE 评估…",
    "Saving the GRADE assessment…",
    "Enregistrement de l’évaluation GRADE…",
    "Сохранение оценки GRADE…",
    "Guardando la evaluación GRADE…",
    "GRADE 評価を保存中…",
    "Salvando a avaliação GRADE…",
    "GRADE-Bewertung wird gespeichert…",
    "Чување GRADE оцене…",
    "GRADE 평가 저장 중…"
  ],
  ["✓ 已保存（最终质量已重新计算：{0}）。", "✓ Saved (final certainty recalculated: {0}).", "✓ Enregistré (certitude finale recalculée : {0}).", "✓ Сохранено (итоговая достоверность доказательств пересчитана: {0}).", "✓ Guardado (certeza final recalculada: {0}).", "✓ 保存しました（最終的なエビデンスの確実性を再計算しました：{0}）。", "✓ Salvo (certeza final recalculada: {0}).", "✓ Gespeichert (endgültige Evidenzsicherheit neu berechnet: {0}).", "✓ Сачувано (коначна поузданост доказа је поново израчуната: {0}).", "✓ 저장됨(최종 근거 확실성을 다시 계산함: {0})."],
  [
    "✓ 已保存「{0}」的 GRADE 评估",
    "✓ GRADE assessment saved for “{0}”",
    "✓ Évaluation GRADE enregistrée pour « {0} »",
    "✓ Оценка GRADE сохранена для «{0}»",
    "✓ Evaluación GRADE guardada para « {0} »",
    "✓「{0}」の GRADE 評価を保存しました",
    "✓ Avaliação GRADE salva para “{0}”",
    "✓ GRADE-Bewertung für „{0}“ gespeichert",
    "✓ GRADE оцена сачувана за „{0}“",
    "✓ “{0}”의 GRADE 평가를 저장했습니다"
  ],
  [
    "✓ 已生成 SoF 表（{0} 行）",
    "✓ SoF table generated ({0} rows)",
    "✓ Tableau SoF généré ({0} lignes)",
    "✓ Таблица SoF сформирована ({0} строк)",
    "✓ Tabla SoF generada ({0} filas)",
    "✓ SoF 表を生成しました（{0} 行）",
    "✓ Tabela SoF gerada ({0} linhas)",
    "✓ SoF-Tabelle erzeugt ({0} Zeilen)",
    "✓ SoF табела генерисана ({0} редова)",
    "✓ SoF 표를 생성했습니다({0}행)"
  ],
  [
    "SoF 表数据格式异常。",
    "The SoF table data has an unexpected format.",
    "Le format des données du tableau SoF est inattendu.",
    "Неожиданный формат данных таблицы SoF.",
    "El formato de los datos de la tabla SoF es inesperado.",
    "SoF 表のデータ形式が不正です。",
    "O formato dos dados da tabela SoF é inesperado.",
    "Das Datenformat der SoF-Tabelle ist unerwartet.",
    "Формат података SoF табеле је неочекиван.",
    "SoF 표 데이터 형식이 올바르지 않습니다."
  ],
  [
    "撰写稿件",
    "Write manuscript",
    "Rédiger le manuscrit",
    "Написать рукопись",
    "Redactar manuscrito",
    "原稿を執筆",
    "Redigir manuscrito",
    "Manuskript verfassen",
    "Пиши рукопис",
    "원고 작성"
  ],
  ["根据本任务中的方案、效应量、偏倚评估、GRADE 与筛选计数，按 PRISMA 2020 结构生成综述稿件框架（规则生成，非 AI 撰写）。Discussion、Limitations 和 Conclusions 三节需由作者撰写。", "Generate a PRISMA 2020-structured review manuscript outline from this task’s protocol, effect sizes, risk-of-bias assessments, GRADE ratings and screening counts. It is rule-generated, not AI-written. Authors must write the Discussion, Limitations and Conclusions sections.", "Générez un plan de manuscrit de revue suivant la structure PRISMA 2020 à partir du protocole de cette tâche, des tailles d’effet, des évaluations du risque de biais, des évaluations GRADE et des décomptes de sélection. Ce plan est généré par des règles et non rédigé par une IA. Les auteurs doivent rédiger les sections « Discussion », « Limites » et « Conclusions ».", "Сформируйте план рукописи обзора по структуре PRISMA 2020 на основе протокола, размеров эффекта, оценок риска систематической ошибки, оценок GRADE и подсчётов отбора из этой задачи. Текст создаётся по правилам, а не ИИ. Разделы Discussion, Limitations и Conclusions должны написать авторы.", "Genere un esquema de manuscrito de revisión conforme a la estructura PRISMA 2020 a partir del protocolo de esta tarea, los tamaños del efecto, las evaluaciones del riesgo de sesgo, las valoraciones GRADE y los recuentos del cribado. Se genera mediante reglas; no está redactado por IA. Los autores deben redactar las secciones «Discusión», «Limitaciones» y «Conclusiones».", "このタスクのプロトコル、効果量、バイアスリスク評価、GRADE 評価、選別件数から、PRISMA 2020 構造のレビュー原稿案を生成します（ルール生成であり、AI による執筆ではありません）。Discussion、Limitations、Conclusions の各節は著者が執筆してください。", "Gere um esboço de manuscrito de revisão de acordo com a estrutura PRISMA 2020, com base no protocolo desta tarefa, nos tamanhos de efeito, nas avaliações do risco de viés, nas avaliações GRADE e nas contagens da triagem. A geração segue regras, sem redação por IA. Os autores devem redigir as seções “Discussão”, “Limitações” e “Conclusões”.", "Erzeugen Sie anhand von Protokoll, Effektgrößen, Biasrisikobewertungen, GRADE-Einstufungen und Screening-Zahlen dieser Aufgabe einen nach PRISMA 2020 strukturierten Entwurf für ein Review-Manuskript. Der Text wird regelbasiert und nicht durch KI erstellt. Die Abschnitte Discussion, Limitations und Conclusions müssen die Autoren selbst verfassen.", "Направите нацрт рукописа прегледа у структури PRISMA 2020 на основу протокола, величина ефекта, процена ризика пристрасности, GRADE оцена и бројања селекције у овом задатку. Текст се генерише по правилима, а не помоћу вештачке интелигенције. Одељке Discussion, Limitations и Conclusions треба да напишу аутори.", "이 작업의 프로토콜, 효과 크기, 비뚤림 위험 평가, GRADE 평가 및 선별 건수를 바탕으로 PRISMA 2020 구조의 리뷰 원고 개요를 생성합니다. 규칙에 따라 생성되며 AI가 작성하지 않습니다. Discussion, Limitations, Conclusions는 저자가 작성해야 합니다."],
  [
    "稿件章节",
    "Manuscript sections",
    "Sections du manuscrit",
    "Разделы рукописи",
    "Secciones del manuscrito",
    "原稿のセクション",
    "Seções do manuscrito",
    "Manuskriptabschnitte",
    "Одељци рукописа",
    "원고 섹션"
  ],
  [
    "需手动撰写",
    "Manual writing required",
    "Rédaction manuelle requise",
    "Требуется ручное написание",
    "Requiere redacción manual",
    "手動での執筆が必要",
    "Requer redação manual",
    "Manuelle Bearbeitung nötig",
    "Потребно ручно писање",
    "수동 작성 필요"
  ],
  [
    "需手动撰写：{0}",
    "Manual writing required: {0}",
    "Rédaction manuelle requise : {0}",
    "Требуется ручное написание: {0}",
    "Requiere redacción manual: {0}",
    "手動での執筆が必要：{0}",
    "Requer redação manual: {0}",
    "Manuelle Bearbeitung nötig: {0}",
    "Потребно ручно писање: {0}",
    "수동 작성 필요: {0}"
  ],
  [
    "全不选",
    "Deselect all",
    "Tout désélectionner",
    "Снять все",
    "Deseleccionar todo",
    "すべて選択解除",
    "Desmarcar tudo",
    "Alle abwählen",
    "Поништи све",
    "모두 선택 해제"
  ],
  [
    "全选",
    "Select all",
    "Tout sélectionner",
    "Выбрать все",
    "Seleccionar todo",
    "すべて選択",
    "Marcar tudo",
    "Alle auswählen",
    "Изабери све",
    "모두 선택"
  ],
  [
    "稿件格式",
    "Manuscript format",
    "Format du manuscrit",
    "Формат рукописи",
    "Formato del manuscrito",
    "原稿の形式",
    "Formato do manuscrito",
    "Manuskriptformat",
    "Формат рукописа",
    "원고 형식"
  ],
  [
    "参考文献格式",
    "Reference style",
    "Style bibliographique",
    "Стиль ссылок",
    "Estilo de referencias",
    "参考文献の形式",
    "Estilo de referências",
    "Zitierstil",
    "Стил референци",
    "참고문헌 형식"
  ],
  [
    "合并模型（稿件重算合成用）",
    "Pooling model (for the manuscript re-synthesis)",
    "Modèle de synthèse (pour la resynthèse du manuscrit)",
    "Модель объединения (для пересчёта синтеза в рукописи)",
    "Modelo de combinación (para la resíntesis del manuscrito)",
    "合併モデル（原稿の再合成用）",
    "Modelo de combinação (para a ressíntese do manuscrito)",
    "Poolmodell (für die Neusynthese des Manuskripts)",
    "Модел обједињавања (за пресинтезу рукописа)",
    "통합 모형(원고 재합성용)"
  ],
  [
    "置信区间方法（稿件重算合成用）",
    "Confidence-interval method (for the manuscript re-synthesis)",
    "Méthode d’intervalle de confiance (pour la resynthèse du manuscrit)",
    "Метод доверительного интервала (для пересчёта синтеза в рукописи)",
    "Método del intervalo de confianza (para la resíntesis del manuscrito)",
    "信頼区間の方法（原稿の再合成用）",
    "Método do intervalo de confiança (para a ressíntese do manuscrito)",
    "Konfidenzintervallmethode (für die Neusynthese des Manuskripts)",
    "Метод интервала поверења (за пресинтезу рукописа)",
    "신뢰구간 방법(원고 재합성용)"
  ],
  [
    "格式", "Format", "Format", "Формат", "Formato", "形式", "Formato", "Format", "Формат", "형식"
  ],
  [
    "嵌入图表（森林图 / PRISMA 流程图）",
    "Embed figures (forest plot / PRISMA flow diagram)",
    "Intégrer les figures (forest plot / diagramme PRISMA)",
    "Встроить рисунки (лесной график / блок-схема PRISMA)",
    "Incluir figuras (diagrama de bosque / flujo PRISMA)",
    "図表を埋め込む（フォレストプロット／PRISMA フローチャート）",
    "Embutir figuras (gráfico de floresta / fluxograma PRISMA)",
    "Abbildungen einbetten (Forest-Plot / PRISMA-Flussdiagramm)",
    "Угради слике (форест дијаграм / PRISMA дијаграм тока)",
    "그림 포함(포레스트 플롯 / PRISMA 흐름도)"
  ],
  [
    "生成并下载",
    "Generate and download",
    "Générer et télécharger",
    "Сгенерировать и скачать",
    "Generar y descargar",
    "生成してダウンロード",
    "Gerar e baixar",
    "Erzeugen und herunterladen",
    "Генериши и преузми",
    "생성 후 내려받기"
  ],
  [
    "Markdown 预览",
    "Markdown preview",
    "Aperçu Markdown",
    "Предпросмотр Markdown",
    "Vista previa Markdown",
    "Markdown プレビュー",
    "Pré-visualização Markdown",
    "Markdown-Vorschau",
    "Markdown преглед",
    "Markdown 미리보기"
  ],
  [
    "请至少勾选一个章节。",
    "Select at least one section.",
    "Sélectionnez au moins une section.",
    "Выберите хотя бы один раздел.",
    "Seleccione al menos una sección.",
    "少なくとも 1 つのセクションを選択してください。",
    "Selecione ao menos uma seção.",
    "Wählen Sie mindestens einen Abschnitt.",
    "Изаберите барем један одељак.",
    "최소 한 개의 섹션을 선택하세요."
  ],
  [
    "正在生成稿件…",
    "Generating the manuscript…",
    "Génération du manuscrit…",
    "Формирование рукописи…",
    "Generando el manuscrito…",
    "原稿を生成中…",
    "Gerando o manuscrito…",
    "Manuskript wird erzeugt…",
    "Генерисање рукописа…",
    "원고 생성 중…"
  ],
  [
    "✓ 已生成并下载：",
    "✓ Generated and downloaded: ",
    "✓ Généré et téléchargé : ",
    "✓ Сгенерировано и скачано: ",
    "✓ Generado y descargado: ",
    "✓ 生成してダウンロードしました：",
    "✓ Gerado e baixado: ",
    "✓ Erzeugt und heruntergeladen: ",
    "✓ Генерисано и преузето: ",
    "✓ 생성 후 내려받음: "
  ],
  [
    "正在生成预览…",
    "Generating the preview…",
    "Génération de l’aperçu…",
    "Формирование предпросмотра…",
    "Generando la vista previa…",
    "プレビューを生成中…",
    "Gerando a pré-visualização…",
    "Vorschau wird erzeugt…",
    "Генерисање прегледа…",
    "미리보기 생성 중…"
  ],
  [
    "✓ 预览已生成（全部章节；含图内联为 SVG）。",
    "✓ Preview generated (all sections; figures inlined as SVG).",
    "✓ Aperçu généré (toutes les sections ; figures insérées en SVG).",
    "✓ Предпросмотр сформирован (все разделы; рисунки встроены как SVG).",
    "✓ Vista previa generada (todas las secciones; figuras incrustadas como SVG).",
    "✓ プレビューを生成しました（全セクション。図は SVG として埋め込み）。",
    "✓ Pré-visualização gerada (todas as seções; figuras embutidas como SVG).",
    "✓ Vorschau erzeugt (alle Abschnitte; Abbildungen als SVG eingebettet).",
    "✓ Преглед генерисан (сви одељци; слике уграђене као SVG).",
    "✓ 미리보기를 생성했습니다(전체 섹션, 그림은 SVG로 포함)."
  ],
  [
    "预览失败：",
    "Preview failed: ",
    "Échec de l’aperçu : ",
    "Ошибка предпросмотра: ",
    "Error de la vista previa: ",
    "プレビューに失敗：",
    "Falha na pré-visualização: ",
    "Vorschau fehlgeschlagen: ",
    "Преглед није успео: ",
    "미리보기 실패: "
  ],
  [
    "预览为全部章节的 Markdown；下载文件按上方勾选与格式生成。",
    "The preview shows Markdown for all sections; the downloaded file follows the selections and format above.",
    "L’aperçu montre le Markdown de toutes les sections ; le fichier téléchargé suit les sélections et le format ci-dessus.",
    "Предпросмотр показывает Markdown всех разделов; загружаемый файл формируется по отметкам и формату выше.",
    "La vista previa muestra el Markdown de todas las secciones; el archivo descargado sigue la selección y el formato de arriba.",
    "プレビューは全セクションの Markdown です。ダウンロードファイルは上の選択と形式で生成されます。",
    "A pré-visualização mostra o Markdown de todas as seções; o arquivo baixado segue as seleções e o formato acima.",
    "Die Vorschau zeigt das Markdown aller Abschnitte; die heruntergeladene Datei folgt den obigen Auswahlen und dem Format.",
    "Преглед приказује Markdown свих одељака; преузета датотека прати изборе и формат изнад.",
    "미리보기는 전체 섹션의 Markdown입니다. 내려받는 파일은 위의 선택과 형식을 따릅니다."
  ],
  [
    "生成器：{0}", "Generator: {0}", "Générateur : {0}", "Генератор: {0}", "Generador: {0}", "ジェネレーター：{0}", "Gerador: {0}", "Generator: {0}", "Генератор: {0}", "생성기: {0}"
  ],
  [
    "生成时间：{0}",
    "Generated at: {0}",
    "Généré le : {0}",
    "Время генерации: {0}",
    "Generado el: {0}",
    "生成日時：{0}",
    "Gerado em: {0}",
    "Erzeugt am: {0}",
    "Генерисано: {0}",
    "생성 시각: {0}"
  ],
  ["（未选择，将采用默认：随机效应 · DL）", "(not selected; default: random effects · DL)", "(non sélectionné ; méthode par défaut : effets aléatoires · DL)", "(не выбрано; метод по умолчанию: случайные эффекты · DL)", "(sin seleccionar; opción predeterminada: efectos aleatorios · DL)", "（未選択。既定の方法：ランダム効果・DL）", "(não selecionado; padrão: efeitos aleatórios · DL)", "(nicht gewählt; Standardeinstellung: Zufallseffekte · DL)", "(није изабрано; подразумевано: случајни ефекти · DL)", "(선택 안 함, 기본값: 무작위 효과 · DL)"],
  ["（未选择，将采用默认：正态 / Wald）", "(not selected; default: normal / Wald)", "(non sélectionné ; méthode par défaut : normale / Wald)", "(не выбрано; метод по умолчанию: нормальное распределение / Wald)", "(sin seleccionar; opción predeterminada: normal / Wald)", "（未選択。既定の方法：正規 / Wald）", "(não selecionado; padrão: normal / Wald)", "(nicht gewählt; Standardeinstellung: Normalverteilung / Wald)", "(није изабрано; подразумевано: нормална расподела / Wald)", "(선택 안 함, 기본값: 정규분포 / Wald)"],
  [
    "活体综述",
    "Living review",
    "Revue vivante",
    "Живой обзор",
    "Revisión viva",
    "リビングレビュー",
    "Revisão viva",
    "Living Review",
    "Живи преглед",
    "리빙 리뷰"
  ],
  [
    "保存检索式，导入新检索到的文献，再查看去重与结果变化。PubMed 检查需单独开启；手动导入仍可使用。",
    "Save a search strategy, import newly retrieved records, then review deduplication and result changes. PubMed checks must be enabled separately; manual import remains available.",
    "Enregistrez la stratégie de recherche, importez les nouvelles références, puis consultez le dédoublonnage et l’évolution des résultats. Le contrôle PubMed doit être activé séparément ; l’importation manuelle reste disponible.",
    "Сохраните поисковую стратегию, импортируйте найденные новые публикации, затем просмотрите дедупликацию и изменения результатов. Проверку PubMed нужно включить отдельно; ручной импорт доступен.",
    "Guarde la estrategia de búsqueda, importe los nuevos registros recuperados y revise la deduplicación y los cambios en los resultados. La comprobación de PubMed debe activarse por separado; la importación manual sigue disponible.",
    "検索式を保存し、新たに検索された文献をインポートして、重複排除と結果の変化を確認します。PubMed のチェックは別途有効化が必要ですが、手動インポートは引き続き利用できます。",
    "Salve a estratégia de busca, importe os novos registros encontrados e confira a deduplicação e as mudanças nos resultados. A verificação do PubMed precisa ser ativada separadamente; a importação manual continua disponível.",
    "Speichern Sie die Suchstrategie, importieren Sie neu gefundene Literatur und prüfen Sie anschließend Dublettenbereinigung und Ergebnisänderungen. Die PubMed-Prüfung muss separat aktiviert werden; der manuelle Import bleibt verfügbar.",
    "Сачувајте стратегију претраге, увезите новопронађене публикације, па прегледајте уклањање дупликата и промене резултата. Проверу преко PubMed-а треба посебно укључити; ручни увоз је и даље доступан.",
    "검색식을 저장하고 새로 검색된 문헌을 가져온 뒤 중복 제거와 결과 변화를 확인하세요. PubMed 검사는 별도로 켜야 하며, 수동 가져오기는 계속 사용할 수 있습니다."
  ],
  [
    "检索式管理",
    "Search strategy management",
    "Gestion des stratégies de recherche",
    "Управление поисковыми стратегиями",
    "Gestión de estrategias de búsqueda",
    "検索式の管理",
    "Gerenciamento de estratégias de busca",
    "Verwaltung der Suchstrategien",
    "Управљање стратегијама претраге",
    "검색식 관리"
  ],
  [
    "尚未保存检索式。",
    "No search strategy saved yet.",
    "Aucune stratégie de recherche enregistrée.",
    "Поисковые стратегии ещё не сохранены.",
    "Aún no hay estrategia de búsqueda guardada.",
    "検索式が保存されていません。",
    "Ainda não há estratégia de busca salva.",
    "Noch keine Suchstrategie gespeichert.",
    "Још нема сачуване стратегије претраге.",
    "아직 저장된 검색식이 없습니다."
  ],
  [
    "检索式",
    "Search strategy",
    "Stratégie de recherche",
    "Поисковая стратегия",
    "Estrategia de búsqueda",
    "検索式",
    "Estratégia de busca",
    "Suchstrategie",
    "Стратегија претраге",
    "검색식"
  ],
  [
    "粘贴数据库检索式（如 PubMed 检索框中的完整检索式）",
    "Paste the database search strategy (e.g. the full query from the PubMed search box)",
    "Collez la stratégie de recherche (p. ex. la requête complète de la boîte PubMed)",
    "Вставьте поисковую стратегию базы (напр. полный запрос из строки поиска PubMed)",
    "Pegue la estrategia de búsqueda (p. ej. la consulta completa del cuadro de PubMed)",
    "データベースの検索式を貼り付けてください（例：PubMed 検索ボックスの完全な検索式）",
    "Cole a estratégia de busca do banco (ex. a consulta completa da caixa do PubMed)",
    "Fügen Sie die Suchstrategie der Datenbank ein (z. B. die vollständige Abfrage aus dem PubMed-Suchfeld)",
    "Налепите стратегију претраге базе (нпр. комплетан упит из PubMed поља за претрагу)",
    "데이터베이스 검색식을 붙여넣으세요(예: PubMed 검색창의 전체 검색식)"
  ],
  [
    "上次检索日期（可选）",
    "Last search date (optional)",
    "Date de la dernière recherche (facultatif)",
    "Дата последнего поиска (необязательно)",
    "Fecha de la última búsqueda (opcional)",
    "前回の検索日（任意）",
    "Data da última busca (opcional)",
    "Datum der letzten Suche (optional)",
    "Датум последње претраге (опционо)",
    "마지막 검색 날짜(선택)"
  ],
  [
    "上次命中数（可选）",
    "Last hit count (optional)",
    "Résultats de la dernière recherche (facultatif)",
    "Число результатов последнего поиска (необязательно)",
    "Número de resultados de la última búsqueda (opcional)",
    "前回のヒット数（任意）",
    "Número de acertos da última busca (opcional)",
    "Trefferzahl der letzten Suche (optional)",
    "Број погодака последње претраге (опционо)",
    "마지막 검색 적중 수(선택)"
  ],
  [
    "保存检索式",
    "Save search strategy",
    "Enregistrer la stratégie",
    "Сохранить стратегию",
    "Guardar estrategia",
    "検索式を保存",
    "Salvar estratégia",
    "Suchstrategie speichern",
    "Сачувај стратегију",
    "검색식 저장"
  ],
  [
    "保存 / 更新检索式",
    "Save / update search strategy",
    "Enregistrer / mettre à jour la stratégie",
    "Сохранить / обновить стратегию",
    "Guardar / actualizar estrategia",
    "検索式の保存／更新",
    "Salvar / atualizar estratégia",
    "Suchstrategie speichern / aktualisieren",
    "Сачувај / ажурирај стратегију",
    "검색식 저장 / 갱신"
  ],
  [
    "检索式（必填）",
    "Search strategy (required)",
    "Stratégie de recherche (obligatoire)",
    "Поисковая стратегия (обязательно)",
    "Estrategia de búsqueda (obligatoria)",
    "検索式（必須）",
    "Estratégia de busca (obrigatória)",
    "Suchstrategie (erforderlich)",
    "Стратегија претраге (обавезно)",
    "검색식(필수)"
  ],
  [
    "上次检索日期",
    "Last search date",
    "Date de la dernière recherche",
    "Дата последнего поиска",
    "Fecha de la última búsqueda",
    "前回の検索日",
    "Data da última busca",
    "Datum der letzten Suche",
    "Датум последње претраге",
    "마지막 검색 날짜"
  ],
  [
    "上次命中数",
    "Last hit count",
    "Nombre de résultats de la dernière recherche",
    "Число результатов последнего поиска",
    "Número de resultados de la última búsqueda",
    "前回のヒット数",
    "Número de acertos da última busca",
    "Trefferzahl der letzten Suche",
    "Број погодака последње претраге",
    "마지막 검색 적중 수"
  ],
  [
    "手动导入更新（离线）",
    "Manual import update (offline)",
    "Mise à jour par import manuel (hors ligne)",
    "Обновление ручным импортом (офлайн)",
    "Actualización por importación manual (sin conexión)",
    "手動インポートで更新（オフライン）",
    "Atualização por importação manual (offline)",
    "Update durch manuellen Import (offline)",
    "Ажурирање ручним увозом (офлајн)",
    "수동 가져오기 갱신(오프라인)"
  ],
  [
    "增量文献文件（RIS/CSV）",
    "Incremental references file (RIS/CSV)",
    "Fichier incrémental de références (RIS/CSV)",
    "Файл с дополнительными публикациями (RIS/CSV)",
    "Archivo incremental de referencias (RIS/CSV)",
    "増分文献ファイル（RIS/CSV）",
    "Arquivo incremental de referências (RIS/CSV)",
    "Inkrementelle Referenzdatei (RIS/CSV)",
    "Датотека прираштаја референци (RIS/CSV)",
    "증분 문헌 파일(RIS/CSV)"
  ],
  [
    "导入并评估影响",
    "Import and assess impact",
    "Importer et évaluer l’impact",
    "Импортировать и оценить влияние",
    "Importar y evaluar el impacto",
    "インポートして影響を評価",
    "Importar e avaliar o impacto",
    "Importieren und Auswirkung bewerten",
    "Увези и процени утицај",
    "가져와 영향 평가"
  ],
  [
    "结局层四要素用于重跑合并分析并对比前后结论；文件先经安全校验，与库内文献自动去重。",
    "The four outcome fields re-run the pooled analysis and compare conclusions before/after; the file is safety-checked first and deduplicated against the library automatically.",
    "Les quatre champs de critère relancent l’analyse poolée et comparent les conclusions avant/après ; le fichier est d’abord vérifié puis dédoublonné automatiquement avec la bibliothèque.",
    "Четыре поля исхода перезапускают объединённый анализ и сравнивают выводы до/после; файл сначала проходит проверку безопасности и автоматически дедуплицируется с библиотекой.",
    "Los cuatro campos del resultado reejecutan el análisis combinado y comparan las conclusiones antes/después; el archivo pasa primero una comprobación de seguridad y se deduplica automáticamente con la biblioteca.",
    "結局層の 4 要素で合併分析を再実行し、結論の前後を比較します。ファイルは先に安全検証を受け、ライブラリ内の文献と自動的に重複排除されます。",
    "Os quatro campos do desfecho reexecutam a análise combinada e comparam as conclusões antes/depois; o arquivo passa primeiro por verificação de segurança e é deduplicado automaticamente com a biblioteca.",
    "Die vier Endpunktfelder führen die Poolanalyse erneut aus und vergleichen die Schlussfolgerungen vorher/nachher; die Datei wird zuerst sicherheitsgeprüft und automatisch mit der Bibliothek dedupliziert.",
    "Четири поља исхода поново покрећу обједињену анализу и пореде закључке пре/после; датотека прво пролази безбедносну проверу и аутоматски се дедуплицира са библиотеком.",
    "결과층 4개 요소로 통합 분석을 재실행하고 결론의 전후를 비교합니다. 파일은 먼저 안전 검사를 받은 뒤 라이브러리의 문헌과 자동으로 중복 제거됩니다."
  ],
  [
    "增量文献文件",
    "Incremental references file",
    "Fichier incrémental de références",
    "Файл с дополнительными публикациями",
    "Archivo incremental de referencias",
    "増分文献ファイル",
    "Arquivo incremental de referências",
    "Inkrementelle Referenzdatei",
    "Датотека прираштаја референци",
    "증분 문헌 파일"
  ],
  [
    "变更报告",
    "Change report",
    "Rapport de changements",
    "Отчёт об изменениях",
    "Informe de cambios",
    "変更レポート",
    "Relatório de mudanças",
    "Änderungsbericht",
    "Извештај о изменама",
    "변경 보고서"
  ],
  [
    "尚无更新记录——导入一次增量文献或运行一次检查后，这里展示前后对比与影响评估。",
    "No update records yet — after one incremental import or one check, the before/after comparison and impact assessment appear here.",
    "Aucun historique de mise à jour — après un import incrémental ou une vérification, la comparaison avant/après et l’évaluation d’impact s’affichent ici.",
    "Записей обновлений пока нет — после одного приращения или одной проверки здесь появятся сравнение «до/после» и оценка влияния.",
    "Aún no hay registros de actualización — tras una importación incremental o una comprobación, aquí aparecen la comparación antes/después y la evaluación del impacto.",
    "更新記録がまだありません。増分インポートまたはチェックを 1 回実行すると、ここに前後比較と影響評価が表示されます。",
    "Ainda não há registros de atualização — após uma importação incremental ou uma verificação, aparecem aqui a comparação antes/depois e a avaliação de impacto.",
    "Noch keine Update-Einträge — nach einem inkrementellen Import oder einer Prüfung erscheinen hier der Vorher-nachher-Vergleich und die Bewertung der Auswirkungen.",
    "Још нема записа ажурирања — након једног прираштајног увоза или провере, овде се приказују поређење пре/после и процена утицаја.",
    "아직 갱신 기록이 없습니다. 증분 가져오기나 검증을 한 번 실행하면 전후 비교와 영향 평가가 여기에 나타납니다."
  ],
  [
    "历次更新",
    "Update history",
    "Historique des mises à jour",
    "История обновлений",
    "Historial de actualizaciones",
    "更新履歴",
    "Histórico de atualizações",
    "Update-Verlauf",
    "Историја ажурирања",
    "갱신 기록"
  ],
  [
    "开启自动监控（PubMed 插件，显式开启）",
    "Enable auto-monitoring (PubMed plugin; explicit opt-in)",
    "Activer le suivi automatique (greffon PubMed ; activation explicite)",
    "Включить автомониторинг (плагин PubMed; явное включение)",
    "Activar seguimiento automático (complemento PubMed; activación explícita)",
    "自動監視を有効化（PubMed プラグイン、明示的なオン）",
    "Ativar monitoramento automático (plugin PubMed; ativação explícita)",
    "Automatisches Monitoring aktivieren (PubMed-Plugin; ausdrücklich eingeschaltet)",
    "Укључи аутоматско праћење (PubMed додатак; изричито укључење)",
    "자동 모니터링 켜기(PubMed 플러그인, 명시적 활성화)"
  ],
  [
    "检查间隔（天）",
    "Check interval (days)",
    "Intervalle de vérification (jours)",
    "Интервал проверки (дней)",
    "Intervalo de comprobación (días)",
    "チェック間隔（日）",
    "Intervalo de verificação (dias)",
    "Prüfintervall (Tage)",
    "Интервал провере (дана)",
    "검증 간격(일)"
  ],
  [
    "立即检查",
    "Check now",
    "Vérifier maintenant",
    "Проверить сейчас",
    "Comprobar ahora",
    "今すぐチェック",
    "Verificar agora",
    "Jetzt prüfen",
    "Провери одмах",
    "지금 검증"
  ],
  [
    "开关只写入任务设置，不启动后台线程；「立即检查」为手动触发的 PubMed 增量拉取（本应用唯一联网入口），失败时自动降级为手动导入模式。",
    "The switch only writes the task setting and starts no background thread; “Check now” is a manually triggered incremental PubMed fetch (the app’s only networked entry point) and falls back to manual-import mode on failure.",
    "L’interrupteur ne fait qu’écrire le réglage de la tâche, sans thread en arrière-plan ; « Vérifier maintenant » déclenche manuellement une récupération PubMed incrémentale (seul point d’accès réseau de l’application) et bascule en mode d’import manuel en cas d’échec.",
    "Переключатель только записывает настройку задачи и не запускает фоновый поток; «Проверить сейчас» — вручную запускаемое приращение из PubMed (единственная сетевая точка приложения); при сбое автоматически включается режим ручного импорта.",
    "El interruptor solo escribe el ajuste de la tarea y no inicia ningún hilo en segundo plano; «Comprobar ahora» dispara manualmente una recuperación incremental de PubMed (único punto de acceso a la red de la aplicación) y ante fallos cambia automáticamente al modo de importación manual.",
    "スイッチはタスク設定への書き込みのみで、バックグラウンドスレッドは起動しません。「今すぐチェック」は手動で発火する PubMed 増分取得（本アプリ唯一のネットワーク入口）で、失敗時は自動的に手動インポートモードに切り替わります。",
    "A chave apenas grava a configuração da tarefa e não inicia threads em segundo plano; “Verificar agora” dispara manualmente uma coleta incremental do PubMed (único ponto de rede do aplicativo) e, em caso de falha, muda automaticamente para o modo de importação manual.",
    "Der Schalter schreibt nur die Aufgabeneinstellung und startet keinen Hintergrund-Thread; „Jetzt prüfen“ löst manuell einen inkrementellen PubMed-Abruf aus (der einzige Netzwerkzugangspunkt der App) und fällt bei einem Fehler auf den manuellen Importmodus zurück.",
    "Прекидач само уписује подешавање задатка и не покреће позадинску нит; „Провери одмах“ је ручно покренуто PubMed добављање прираштаја (једина мрежна тачка апликације) и при неуспеху аутоматски прелази на режим ручног увоза.",
    "스위치는 작업 설정만 기록하며 백그라운드 스레드를 시작하지 않습니다. “지금 검증”은 수동으로 실행하는 PubMed 증분 수집(이 앱의 유일한 네트워크 진입점)이며 실패 시 자동으로 수동 가져오기 모드로 전환됩니다."
  ],
  [
    "自动监控（可选，默认关闭）",
    "Auto-monitoring (optional, off by default)",
    "Suivi automatique (optionnel, désactivé par défaut)",
    "Автомониторинг (необязательно, по умолчанию выключен)",
    "Seguimiento automático (opcional, desactivado por defecto)",
    "自動監視（任意、既定で無効）",
    "Monitoramento automático (opcional, desativado por padrão)",
    "Automatisches Monitoring (optional, standardmäßig aus)",
    "Аутоматско праћење (опционо, подразумевано искључено)",
    "자동 모니터링(선택, 기본 꺼짐)"
  ],
  [
    "上次检索：{0}",
    "Last search: {0}",
    "Dernière recherche : {0}",
    "Последний поиск: {0}",
    "Última búsqueda: {0}",
    "前回の検索：{0}",
    "Última busca: {0}",
    "Letzte Suche: {0}",
    "Последња претрага: {0}",
    "마지막 검색: {0}"
  ],
  [
    "命中 {0}",
    "{0} hits",
    "{0} résultats",
    "{0} результатов",
    "{0} resultados",
    "ヒット {0} 件",
    "{0} resultados",
    "{0} Treffer",
    "{0} погодака",
    "적중 {0}건"
  ],
  [
    "一键打开 PubMed ↗",
    "Open PubMed ↗",
    "Ouvrir PubMed ↗",
    "Открыть PubMed ↗",
    "Abrir PubMed ↗",
    "PubMed を開く ↗",
    "Abrir PubMed ↗",
    "PubMed öffnen ↗",
    "Отвори PubMed ↗",
    "PubMed 열기 ↗"
  ],
  [
    "请先填写检索式。",
    "Enter the search strategy first.",
    "Saisissez d’abord la stratégie de recherche.",
    "Сначала введите поисковую стратегию.",
    "Introduzca primero la estrategia de búsqueda.",
    "先に検索式を入力してください。",
    "Informe primeiro a estratégia de busca.",
    "Geben Sie zuerst die Suchstrategie ein.",
    "Прво унесите стратегију претраге.",
    "먼저 검색식을 입력하세요."
  ],
  [
    "正在保存检索式…",
    "Saving the search strategy…",
    "Enregistrement de la stratégie…",
    "Сохранение поисковой стратегии…",
    "Guardando la estrategia de búsqueda…",
    "検索式を保存中…",
    "Salvando a estratégia de busca…",
    "Suchstrategie wird gespeichert…",
    "Чување стратегије претраге…",
    "검색식 저장 중…"
  ],
  [
    "✓ 检索式已保存（PubMed 链接由本地规则生成）。",
    "✓ Search strategy saved (the PubMed link is generated by local rules).",
    "✓ Stratégie enregistrée (le lien PubMed est généré par des règles locales).",
    "✓ Стратегия сохранена (ссылка PubMed формируется локальными правилами).",
    "✓ Estrategia guardada (el enlace de PubMed se genera con reglas locales).",
    "✓ 検索式を保存しました（PubMed リンクはローカルルールで生成）。",
    "✓ Estratégia salva (o link do PubMed é gerado por regras locais).",
    "✓ Suchstrategie gespeichert (der PubMed-Link wird durch lokale Regeln erzeugt).",
    "✓ Стратегија сачувана (PubMed линк генеришу локална правила).",
    "✓ 검색식을 저장했습니다(PubMed 링크는 로컬 규칙으로 생성)."
  ],
  [
    "✓ 检索式已保存",
    "✓ Search strategy saved",
    "✓ Stratégie enregistrée",
    "✓ Стратегия сохранена",
    "✓ Estrategia guardada",
    "✓ 検索式を保存しました",
    "✓ Estratégia salva",
    "✓ Suchstrategie gespeichert",
    "✓ Стратегија сачувана",
    "✓ 검색식을 저장했습니다"
  ],
  [
    "请先选择增量文献文件（RIS/CSV）。",
    "Choose the incremental references file (RIS/CSV) first.",
    "Choisissez d’abord le fichier incrémental de références (RIS/CSV).",
    "Сначала выберите файл с дополнительными публикациями (RIS/CSV).",
    "Elija primero el archivo incremental de referencias (RIS/CSV).",
    "先に増分文献ファイル（RIS/CSV）を選択してください。",
    "Escolha primeiro o arquivo incremental de referências (RIS/CSV).",
    "Wählen Sie zuerst die inkrementelle Referenzdatei (RIS/CSV).",
    "Прво изаберите датотеку прираштаја референци (RIS/CSV).",
    "먼저 증분 문헌 파일(RIS/CSV)을 선택하세요."
  ],
  [
    "请填写结局层的「{0}」（重跑合并分析需要）。",
    "Fill in the outcome field “{0}” (needed to re-run the pooled analysis).",
    "Renseignez le champ de critère « {0} » (nécessaire pour relancer l’analyse poolée).",
    "Заполните поле исхода «{0}» (нужно для перезапуска объединённого анализа).",
    "Complete el campo del desenlace «{0}» (necesario para volver a ejecutar el metanálisis).",
    "結局層の「{0}」を入力してください（合併分析の再実行に必要）。",
    "Preencha o campo do desfecho “{0}” (necessário para reexecutar a análise combinada).",
    "Füllen Sie das Endpunktfeld „{0}“ aus (für die erneute Poolanalyse nötig).",
    "Попуните поље исхода „{0}“ (потребно за поновно покретање обједињене анализе).",
    "결과층의 “{0}”을(를) 입력하세요(통합 분석 재실행에 필요)."
  ],
  [
    "正在导入并重算…（去重、AI 热启动、重跑合并分析）",
    "Importing and recomputing… (dedup, AI warm start, re-running the pooled analysis)",
    "Import et recalcul… (déduplication, démarrage à chaud de l’IA, nouvelle analyse poolée)",
    "Импорт и пересчёт… (дедупликация, тёплый старт ИИ, повторный объединённый анализ)",
    "Importando y recalculando… (deduplicación, arranque en caliente de la IA, reejecución del análisis combinado)",
    "インポートして再計算中…（重複排除、AI ウォームスタート、合併分析の再実行）",
    "Importando e recalculando… (deduplicação, warm start de IA, reexecução da análise combinada)",
    "Import und Neuberechnung… (Deduplizierung, KI-Warmstart, erneute Poolanalyse)",
    "Увоз и поновни рачун… (дедупликација, ИА топли старт, поновна обједињена анализа)",
    "가져오고 재계산하는 중…(중복 제거, AI 웜스타트, 통합 분석 재실행)"
  ],
  [
    "✓ 增量导入完成。",
    "✓ Incremental import complete.",
    "✓ Import incrémental terminé.",
    "✓ Приращение импортировано.",
    "✓ Importación incremental completada.",
    "✓ 増分インポートが完了しました。",
    "✓ Importação incremental concluída.",
    "✓ Inkrementeller Import abgeschlossen.",
    "✓ Прираштајни увоз завршен.",
    "✓ 증분 가져오기 완료."
  ],
  [
    "✓ 增量导入完成：新增 {0} 篇，去重移除 {1} 篇",
    "✓ Incremental import complete: {0} new, {1} removed as duplicates",
    "✓ Import incrémental terminé : {0} nouveautés, {1} retirées comme doublons",
    "✓ Приращение импортировано: новых {0}, удалено дубликатов {1}",
    "✓ Importación incremental completada: {0} nuevas, {1} eliminadas por duplicado",
    "✓ 増分インポート完了：新規 {0} 件、重複除去 {1} 件",
    "✓ Importação incremental concluída: {0} novas, {1} removidas como duplicadas",
    "✓ Inkrementeller Import abgeschlossen: {0} neu, {1} als Duplikate entfernt",
    "✓ Прираштајни увоз завршен: нових {0}, уклоњено дупликата {1}",
    "✓ 증분 가져오기 완료: 신규 {0}건, 중복 제거 {1}건"
  ],
  [
    "更新时间：{0}",
    "Updated: {0}",
    "Mis à jour : {0}",
    "Обновлено: {0}",
    "Actualizado: {0}",
    "更新日時：{0}",
    "Atualizado: {0}",
    "Aktualisiert: {0}",
    "Ажурирано: {0}",
    "갱신 시각: {0}"
  ],
  [
    "本次更新",
    "This update",
    "Cette mise à jour",
    "Это обновление",
    "Esta actualización",
    "今回の更新",
    "Esta atualização",
    "Dieses Update",
    "Ово ажурирање",
    "이번 갱신"
  ],
  [
    "新增 {0} 篇",
    "{0} new records",
    "{0} nouvelles références",
    "Новых записей: {0}",
    "{0} referencias nuevas",
    "新規 {0} 件",
    "{0} novas referências",
    "{0} neue Referenzen",
    "{0} нових записа",
    "신규 {0}건"
  ],
  [
    "去重移除 {0} 篇",
    "{0} removed as duplicates",
    "{0} retirées comme doublons",
    "Удалено дубликатов: {0}",
    "{0} eliminadas por duplicado",
    "重複として除外 {0} 件",
    "{0} removidas como duplicadas",
    "{0} als Duplikate entfernt",
    "{0} уклоњено као дупликати",
    "중복으로 제거 {0}건"
  ],
  [
    "待新筛 {0} 篇（AI 热启动排序）",
    "{0} awaiting screening (AI warm-start ranking)",
    "{0} en attente de sélection (classement par IA avec initialisation à chaud)",
    "{0} ожидают отбора (ИИ-ранжирование с тёплым стартом)",
    "{0} pendientes de cribado (orden por IA con arranque en caliente)",
    "未スクリーニング {0} 件（AI ウォームスタート順）",
    "{0} aguardando triagem (ordenação por IA com warm start)",
    "{0} warten auf das Screening (KI-Warmstart-Rangfolge)",
    "{0} чека скрининг (ИА рангирање са топлим стартом)",
    "선별 대기 {0}건(AI 웜스타트 정렬)"
  ],
  [
    "AI 排序不可用",
    "AI ranking unavailable",
    "Classement IA indisponible",
    "ИИ-ранжирование недоступно",
    "Orden por IA no disponible",
    "AI ランキング利用不可",
    "Ordenação por IA indisponível",
    "KI-Rangfolge nicht verfügbar",
    "ИА рангирање недоступно",
    "AI 정렬 사용 불가"
  ],
  [
    "AI 排序不可用：",
    "AI ranking unavailable: ",
    "Classement IA indisponible : ",
    "ИИ-ранжирование недоступно: ",
    "Orden por IA no disponible: ",
    "AI ランキング利用不可：",
    "Ordenação por IA indisponível: ",
    "KI-Rangfolge nicht verfügbar: ",
    "ИА рангирање недоступно: ",
    "AI 정렬 사용 불가: "
  ],
  [
    "结局层",
    "Outcome layer",
    "Niveau des critères de jugement",
    "Слой исхода",
    "Nivel de desenlaces",
    "結局層",
    "Camada de desfecho",
    "Endpunktebene",
    "Слој исхода",
    "결과층"
  ],
  [
    "合并结果对比",
    "Pooled results comparison",
    "Comparaison des résultats combinés",
    "Сравнение объединённых результатов",
    "Comparación de resultados combinados",
    "統合結果の比較",
    "Comparação de resultados combinados",
    "Vergleich der Poolergebnisse",
    "Поређење обједињених резултата",
    "통합 결과 비교"
  ],
  [
    "更新前",
    "Before update",
    "Avant mise à jour",
    "До обновления",
    "Antes de actualizar",
    "更新前",
    "Antes da atualização",
    "Vor dem Update",
    "Пре ажурирања",
    "갱신 전"
  ],
  [
    "更新后",
    "After update",
    "Après mise à jour",
    "После обновления",
    "Después de actualizar",
    "更新後",
    "Depois da atualização",
    "Nach dem Update",
    "Након ажурирања",
    "갱신 후"
  ],
  [
    "I²（%）",
    "I² (%)",
    "I² (%)",
    "I² (%)",
    "I² (%)",
    "I²（%）",
    "I² (%)",
    "I² (%)",
    "I² (%)",
    "I²(%)"
  ],
  [
    "本次更新未重算合并结果（未提供完整结局层或研究数不足）。",
    "The pooled result was not recomputed this update (incomplete outcome layer or too few studies).",
    "Le résultat combiné n’a pas été recalculé lors de cette mise à jour (données sur les critères de jugement incomplètes ou nombre d’études insuffisant).",
    "Объединённый результат не пересчитан в этом обновлении (неполный слой исхода или слишком мало исследований).",
    "El resultado combinado no se recalculó en esta actualización (datos de desenlaces incompletos o muy pocos estudios).",
    "今回の更新では統合結果を再計算しませんでした（結局層が不完全か研究数不足）。",
    "O resultado combinado não foi recalculado nesta atualização (camada de desfecho incompleta ou estudos insuficientes).",
    "Das Poolergebnis wurde bei diesem Update nicht neu berechnet (Endpunktebene unvollständig oder zu wenige Studien).",
    "Обједињени резултат није поново израчунат у овом ажурирању (непотпун слој исхода или премало студија).",
    "이번 갱신에서는 통합 결과를 재계산하지 않았습니다(결과층 불완전 또는 연구 수 부족)."
  ],
  [
    "⚠ 结论已翻转（CI 跨 null 状态变化）",
    "⚠ Conclusion flipped (CI-versus-null changed)",
    "⚠ Conclusion inversée (la position de l’IC par rapport à la valeur nulle a changé)",
    "⚠ Вывод изменился на противоположный (отношение ДИ к нулю изменилось)",
    "⚠ Conclusión invertida (cambió la posición del IC respecto al valor nulo)",
    "⚠ 結論が反転しました（CI の null に対する状態が変化）",
    "⚠ Conclusão invertida (mudou a posição do IC em relação ao valor nulo)",
    "⚠ Schlussfolgerung gekippt (KI-Bezug zur Null geändert)",
    "⚠ Закључак преокренут (промена односа интервала поверења према нули)",
    "⚠ 결론이 반전되었습니다(신뢰구간의 null 관계 변화)"
  ],
  [
    "结论未翻转",
    "Conclusion not flipped",
    "Conclusion non inversée",
    "Вывод не изменился",
    "Conclusión no invertida",
    "結論は反転していません",
    "Conclusão não invertida",
    "Schlussfolgerung nicht gekippt",
    "Закључак није преокренут",
    "결론 반전 없음"
  ],
  [
    "结论已翻转",
    "Conclusion flipped",
    "Conclusion inversée",
    "Вывод изменился на противоположный",
    "Conclusión invertida",
    "結論が反転しました",
    "Conclusão invertida",
    "Schlussfolgerung gekippt",
    "Закључак преокренут",
    "결론 반전됨"
  ],
  [
    "影响评估",
    "Impact assessment",
    "Évaluation d’impact",
    "Оценка влияния",
    "Evaluación del impacto",
    "影響評価",
    "Avaliação de impacto",
    "Auswirkungsbewertung",
    "Процена утицаја",
    "영향 평가"
  ],
  [
    "新增 {0}",
    "New: {0}",
    "Nouveau : {0}",
    "Новые: {0}",
    "Nuevo: {0}",
    "新規：{0}",
    "Novo: {0}",
    "Neu: {0}",
    "Новo: {0}",
    "신규: {0}"
  ],
  [
    "✓ 自动监控已开启（无后台线程，需手动「立即检查」）",
    "✓ Auto-monitoring enabled (no background thread; use “Check now” manually)",
    "✓ Suivi automatique activé (aucun thread en arrière-plan ; utilisez « Vérifier maintenant » manuellement)",
    "✓ Автомониторинг включён (без фонового потока; запускайте «Проверить сейчас» вручную)",
    "✓ Seguimiento automático activado (sin hilo en segundo plano; use «Comprobar ahora» manualmente)",
    "✓ 自動監視を有効にしました（バックグラウンドスレッドなし。「今すぐチェック」は手動実行）",
    "✓ Monitoramento automático ativado (sem thread em segundo plano; use “Verificar agora” manualmente)",
    "✓ Automatisches Monitoring aktiviert (kein Hintergrund-Thread; „Jetzt prüfen“ manuell auslösen)",
    "✓ Аутоматско праћење укључено (без позадинске нити; „Провери одмах“ покреће се ручно)",
    "✓ 자동 모니터링을 켰습니다(백그라운드 스레드 없음, “지금 검증”은 수동 실행)."
  ],
  [
    "自动监控已关闭",
    "Auto-monitoring disabled",
    "Suivi automatique désactivé",
    "Автомониторинг выключен",
    "Seguimiento automático desactivado",
    "自動監視は無効です",
    "Monitoramento automático desativado",
    "Automatisches Monitoring deaktiviert",
    "Аутоматско праћење искључено",
    "자동 모니터링 꺼짐"
  ],
  [
    "设置自动监控失败：",
    "Failed to set auto-monitoring: ",
    "Échec du réglage du suivi automatique : ",
    "Не удалось настроить автомониторинг: ",
    "Error al configurar el seguimiento automático: ",
    "自動監視の設定に失敗：",
    "Falha ao configurar o monitoramento automático: ",
    "Automatisches Monitoring konnte nicht gesetzt werden: ",
    "Подешавање аутоматског праћења није успело: ",
    "자동 모니터링 설정 실패: "
  ],
  [
    "正在通过 PubMed 插件拉取增量…",
    "Fetching updates via the PubMed plugin…",
    "Récupération incrémentale via le greffon PubMed…",
    "Получение приращения через плагин PubMed…",
    "Obteniendo novedades mediante el complemento PubMed…",
    "PubMed プラグインで増分を取得中…",
    "Buscando atualizações via plugin PubMed…",
    "Inkremente werden über das PubMed-Plugin abgerufen…",
    "Добављање прираштаја кроз PubMed додатак…",
    "PubMed 플러그인으로 증분을 가져오는 중…"
  ],
  [
    "网络不可达，请手动导出文件后导入。",
    "Network unreachable; export the file manually and import it.",
    "Réseau injoignable ; exportez le fichier manuellement puis importez-le.",
    "Сеть недоступна; экспортируйте файл вручную и импортируйте его.",
    "Red inaccesible; exporte el archivo manualmente e impórtelo.",
    "ネットワークに到達できません。手動でファイルをエクスポートしてインポートしてください。",
    "Rede inacessível; exporte o arquivo manualmente e importe-o.",
    "Netzwerk nicht erreichbar; exportieren Sie die Datei manuell und importieren Sie sie.",
    "Мрежа недоступна; извезите датотеку ручно и увезите је.",
    "네트워크에 접근할 수 없습니다. 파일을 수동으로 내보낸 뒤 가져오세요."
  ],
  [
    "✓ 检查完成：PubMed 拉回 {0} 篇。",
    "✓ Check complete: {0} references retrieved from PubMed.",
    "✓ Vérification terminée : {0} références récupérées de PubMed.",
    "✓ Проверка завершена: из PubMed получено публикаций: {0}.",
    "✓ Comprobación completada: {0} referencias recuperadas de PubMed.",
    "✓ チェック完了：PubMed から {0} 件を取得しました。",
    "✓ Verificação concluída: {0} referências obtidas do PubMed.",
    "✓ Prüfung abgeschlossen: {0} Referenzen aus PubMed abgerufen.",
    "✓ Провера завршена: преузето {0} референци са PubMed-а.",
    "✓ 검증 완료: PubMed에서 {0}건을 가져왔습니다."
  ],
  [
    "✓ 检查完成",
    "✓ Check complete",
    "✓ Vérification terminée",
    "✓ Проверка завершена",
    "✓ Comprobación completada",
    "✓ チェック完了",
    "✓ Verificação concluída",
    "✓ Prüfung abgeschlossen",
    "✓ Провера завршена",
    "✓ 검증 완료"
  ],
  [
    "检查失败：",
    "Check failed: ",
    "Échec de la vérification : ",
    "Ошибка проверки: ",
    "Error de comprobación: ",
    "チェックに失敗：",
    "Falha na verificação: ",
    "Prüfung fehlgeschlagen: ",
    "Провера није успела: ",
    "검증 실패: "
  ],
  [
    "手动 / 其他来源",
    "Manual / other source",
    "Manuel / autre source",
    "Вручную / другой источник",
    "Manual / otra fuente",
    "手動／その他の出典",
    "Manual / outra fonte",
    "Manuell / andere Quelle",
    "Ручно / други извор",
    "수동 / 기타 출처"
  ],
  [
    "手动导入",
    "Manual import",
    "Import manuel",
    "Ручной импорт",
    "Importación manual",
    "手動インポート",
    "Importação manual",
    "Manueller Import",
    "Ручни увоз",
    "수동 가져오기"
  ],
  [
    "PubMed 插件",
    "PubMed plugin",
    "Greffon PubMed",
    "Плагин PubMed",
    "Complemento PubMed",
    "PubMed プラグイン",
    "Plugin PubMed",
    "PubMed-Plugin",
    "PubMed додатак",
    "PubMed 플러그인"
  ],
  [
    "指标代码",
    "Measure code",
    "Code de mesure",
    "Код показателя",
    "Código de medida",
    "指標コード",
    "Código de medida",
    "Kennzahlcode",
    "Код мере",
    "지표 코드"
  ],
  [
    "指标代码（如 MD、OR）",
    "Measure code (e.g. MD, OR)",
    "Code de mesure (p. ex. MD, OR)",
    "Код показателя (напр. MD, OR)",
    "Código de medida (p. ej. MD, OR)",
    "指標コード（例：MD、OR）",
    "Código de medida (ex. MD, OR)",
    "Kennzahlcode (z. B. MD, OR)",
    "Код мере (нпр. MD, OR)",
    "지표 코드(예: MD, OR)"
  ],
  [
    "证据地图",
    "Evidence map",
    "Carte des preuves",
    "Карта доказательств",
    "Mapa de evidencia",
    "エビデンスマップ",
    "Mapa de evidências",
    "Evidenzkarte",
    "Мапа доказа",
    "근거 지도"
  ],
  [
    "按已导入文献生成分布地图，点击单元格查看文献。空白格只表示当前文献库没有记录，不代表该领域没有研究。主题聚类在本地计算。",
    "Create a distribution map from imported references and click a cell to view its records. An empty cell means only that the current library has no records there; it does not mean the field has no research. Topic clustering runs locally.",
    "Créez une carte de répartition à partir des références importées et cliquez sur une cellule pour consulter les références correspondantes. Une cellule vide indique seulement qu’aucune référence n’est présente dans la bibliothèque actuelle ; cela ne signifie pas que le domaine manque de recherches. Le regroupement thématique est calculé localement.",
    "Постройте карту распределения по импортированным публикациям и нажмите на ячейку, чтобы просмотреть публикации. Пустая ячейка означает лишь, что в текущей библиотеке нет записей; это не означает отсутствия исследований в данной области. Тематическая кластеризация выполняется локально.",
    "Genere un mapa de distribución a partir de las referencias importadas y haga clic en una celda para ver sus registros. Una celda vacía solo indica que la biblioteca actual no contiene registros allí; no significa que no haya investigaciones en ese campo. La agrupación temática se calcula localmente.",
    "インポート済み文献から分布マップを作成し、セルをクリックして文献を確認します。空のセルは現在の文献ライブラリに記録がないことだけを示し、その分野に研究がないことを意味しません。トピックのクラスタリングはローカルで計算します。",
    "Gere um mapa de distribuição com as referências importadas e clique em uma célula para ver os registros. Uma célula vazia indica apenas que não há registros na biblioteca atual; não significa que não existam pesquisas na área. O agrupamento por tema é calculado localmente.",
    "Erstellen Sie aus den importierten Referenzen eine Verteilungskarte und klicken Sie auf ein Feld, um die zugehörigen Einträge anzuzeigen. Ein leeres Feld bedeutet nur, dass die aktuelle Bibliothek dort keine Einträge enthält; es bedeutet nicht, dass es in diesem Bereich keine Forschung gibt. Die Themencluster werden lokal berechnet.",
    "Направите мапу расподеле на основу увезених референци и кликните на поље да бисте видели публикације. Празно поље само значи да у тренутној библиотеци нема записа; не значи да у тој области нема истраживања. Тематско груписање се израчунава локално.",
    "가져온 문헌으로 분포 지도를 만들고 셀을 클릭해 문헌을 확인하세요. 빈 셀은 현재 문헌 라이브러리에 기록이 없다는 뜻일 뿐, 해당 분야에 연구가 없다는 뜻은 아닙니다. 주제 군집화는 로컬에서 계산됩니다."
  ],
  [
    "X 轴维度",
    "X-axis dimension",
    "Dimension de l’axe X",
    "Измерение оси X",
    "Dimensión del eje X",
    "X 軸の次元",
    "Dimensão do eixo X",
    "X-Achsen-Dimension",
    "Димензија X осе",
    "X축 차원"
  ],
  [
    "Y 轴维度",
    "Y-axis dimension",
    "Dimension de l’axe Y",
    "Измерение оси Y",
    "Dimensión del eje Y",
    "Y 軸の次元",
    "Dimensão do eixo Y",
    "Y-Achsen-Dimension",
    "Димензија Y осе",
    "Y축 차원"
  ],
  [
    "主题聚类数",
    "Number of topic clusters",
    "Nombre de groupes thématiques",
    "Число тематических кластеров",
    "Número de grupos temáticos",
    "トピッククラスター数",
    "Número de agrupamentos temáticos",
    "Anzahl der Themencluster",
    "Број тематских кластера",
    "주제 군집 수"
  ],
  [
    "生成地图",
    "Generate map",
    "Générer la carte",
    "Сформировать карту",
    "Generar mapa",
    "マップを生成",
    "Gerar mapa",
    "Karte erzeugen",
    "Генериши мапу",
    "지도 생성"
  ],
  [
    "主题簇",
    "Topic clusters",
    "Groupes thématiques",
    "Тематические кластеры",
    "Grupos temáticos",
    "トピッククラスター",
    "Agrupamentos temáticos",
    "Themencluster",
    "Тематски кластери",
    "주제 군집"
  ],
  [
    "年份",
    "Year",
    "Année",
    "Год",
    "Año",
    "年",
    "Ano",
    "Jahr",
    "Година",
    "연도"
  ],
  [
    "期刊",
    "Journal",
    "Revue",
    "Журнал",
    "Revista",
    "学術誌",
    "Periódico",
    "Zeitschrift",
    "Часопис",
    "학술지"
  ],
  [
    "人群", "Population", "Population", "Популяция", "Población", "対象集団", "População", "Population", "Популација", "대상 집단"
  ],
  [
    "Meta 分析",
    "Meta-analysis",
    "Méta-analyse",
    "Мета-анализ",
    "Metanálisis",
    "メタ分析",
    "Metanálise",
    "Metaanalyse",
    "Мета-анализа",
    "메타분석"
  ],
  [
    "系统综述",
    "Systematic review",
    "Revue systématique",
    "Систематический обзор",
    "Revisión sistemática",
    "システマティックレビュー",
    "Revisão sistemática",
    "Systematische Übersichtsarbeit",
    "Систематски преглед",
    "체계적 문헌고찰"
  ],
  [
    "随机对照试验",
    "Randomized controlled trial",
    "Essai contrôlé randomisé",
    "Рандомизированное контролируемое исследование",
    "Ensayo clínico aleatorizado",
    "無作為化比較試験",
    "Ensaio clínico randomizado",
    "Randomisierte kontrollierte Studie",
    "Рандомизирана контролисана студија",
    "무작위 대조시험"
  ],
  [
    "队列研究",
    "Cohort study",
    "Étude de cohorte",
    "Когортное исследование",
    "Estudio de cohortes",
    "コホート研究",
    "Estudo de coorte",
    "Kohortenstudie",
    "Кохортна студија",
    "코호트 연구"
  ],
  [
    "病例对照",
    "Case-control",
    "Cas-témoins",
    "Случай-контроль",
    "Casos y controles",
    "症例対照研究",
    "Caso-controle",
    "Fall-Kontroll-Studie",
    "Студија случај-контрола",
    "사례-대조 연구"
  ],
  [
    "横断面研究",
    "Cross-sectional study",
    "Étude transversale",
    "Поперечное исследование",
    "Estudio transversal",
    "横断研究",
    "Estudo transversal",
    "Querschnittsstudie",
    "Пресечна студија",
    "단면 연구"
  ],
  [
    "儿童",
    "Children",
    "Enfants",
    "Дети",
    "Niños",
    "小児",
    "Crianças",
    "Kinder",
    "Деца",
    "소아"
  ],
  [
    "老年人群",
    "Older adults",
    "Personnes âgées",
    "Пожилые",
    "Personas mayores",
    "高齢者",
    "Idosos",
    "Ältere Menschen",
    "Старије особе",
    "노인"
  ],
  [
    "动物",
    "Animals",
    "Animaux",
    "Животные",
    "Animales",
    "動物",
    "Animais",
    "Tiere",
    "Животиње",
    "동물"
  ],
  [
    "患者",
    "Patients",
    "Patients",
    "Пациенты",
    "Pacientes",
    "患者",
    "Pacientes",
    "Patienten",
    "Пацијенти",
    "환자"
  ],
  [
    "成人",
    "Adults",
    "Adultes",
    "Взрослые",
    "Adultos",
    "成人",
    "Adultos",
    "Erwachsene",
    "Одрасли",
    "성인"
  ],
  [
    "未知年份",
    "Unknown year",
    "Année inconnue",
    "Год неизвестен",
    "Año desconocido",
    "不明な年",
    "Ano desconhecido",
    "Unbekanntes Jahr",
    "Непозната година",
    "알 수 없는 연도"
  ],
  [
    "未知期刊",
    "Unknown journal",
    "Revue inconnue",
    "Журнал неизвестен",
    "Revista desconocida",
    "不明な学術誌",
    "Periódico desconhecido",
    "Unbekannte Zeitschrift",
    "Непознат часопис",
    "알 수 없는 학술지"
  ],
  [
    "其他期刊",
    "Other journals",
    "Autres revues",
    "Другие журналы",
    "Otras revistas",
    "その他の学術誌",
    "Outros periódicos",
    "Weitere Zeitschriften",
    "Остали часописи",
    "기타 학술지"
  ],
  [
    "X 轴与 Y 轴维度不能相同。",
    "The X-axis and Y-axis dimensions must differ.",
    "Les dimensions des axes X et Y doivent être différentes.",
    "Измерения осей X и Y должны различаться.",
    "Las dimensiones de los ejes X e Y deben ser distintas.",
    "X 軸と Y 軸の次元は同じにできません。",
    "As dimensões dos eixos X e Y devem ser diferentes.",
    "Die Dimensionen der X- und Y-Achse müssen unterschiedlich sein.",
    "Димензије X и Y осе морају бити различите.",
    "X축과 Y축 차원은 같을 수 없습니다."
  ],
  [
    "正在生成证据地图…（TF-IDF 聚类 + 交叉计数）",
    "Generating the evidence map… (TF-IDF clustering + cross-counting)",
    "Génération de la carte des preuves… (regroupement TF-IDF + comptage croisé)",
    "Формирование карты доказательств… (TF-IDF кластеризация + перекрёстный подсчёт)",
    "Generando el mapa de evidencia… (agrupamiento TF-IDF + conteo cruzado)",
    "エビデンスマップを生成中…（TF-IDF クラスタリング＋交差カウント）",
    "Gerando o mapa de evidências… (agrupamento TF-IDF + contagem cruzada)",
    "Evidenzkarte wird erzeugt… (TF-IDF-Clustering + Kreuzzählung)",
    "Генерисање мапе доказа… (TF-IDF кластерисање + укрштено бројање)",
    "근거 지도 생성 중…(TF-IDF 군집화 + 교차 집계)"
  ],
  [
    "✓ 已生成：{0} 篇文献 · {1} × {2}。点击单元格查看该组合下的文献。",
    "✓ Generated: {0} references · {1} × {2}. Click a cell to list the references in that combination.",
    "✓ Généré : {0} références · {1} × {2}. Cliquez sur une cellule pour lister les références de cette combinaison.",
    "✓ Сформировано: публикаций {0} · {1} × {2}. Нажмите на ячейку, чтобы увидеть публикации этого сочетания.",
    "✓ Generado: {0} referencias · {1} × {2}. Haga clic en una celda para ver las referencias de esa combinación.",
    "✓ 生成しました：{0} 件の文献・{1} × {2}。セルをクリックするとその組み合わせの文献を表示します。",
    "✓ Gerado: {0} referências · {1} × {2}. Clique numa célula para listar as referências dessa combinação.",
    "✓ Erzeugt: {0} Referenzen · {1} × {2}. Klicken Sie auf eine Zelle, um die Referenzen dieser Kombination aufzulisten.",
    "✓ Генерисано: {0} референци · {1} × {2}. Кликните на ћелију да видите референце те комбинације.",
    "✓ 생성 완료: 문헌 {0}건 · {1} × {2}. 칸을 클릭하면 해당 조합의 문헌을 봅니다."
  ],
  [
    "当前库没有可聚类的文献（或全部文本为空）。",
    "No references can be clustered in this library (or all texts are empty).",
    "Aucune référence ne peut être regroupée dans cette bibliothèque (ou tous les textes sont vides).",
    "В этой библиотеке нет публикаций для кластеризации (или все тексты пусты).",
    "No hay referencias que agrupar en esta biblioteca (o todos los textos están vacíos).",
    "このライブラリにはクラスタリングできる文献がありません（または全テキストが空）。",
    "Não há referências a agrupar nesta biblioteca (ou todos os textos estão vazios).",
    "In dieser Bibliothek lassen sich keine Referenzen clustern (oder alle Texte sind leer).",
    "У овој библиотеци нема референци за кластерисање (или су сви текстови празни).",
    "이 라이브러리에는 군집화할 문헌이 없습니다(또는 모든 텍스트가 비어 있음)."
  ],
  [
    "证据地图热力图：{0} × {1}",
    "Evidence-map heatmap: {0} × {1}",
    "Carte thermique des preuves : {0} × {1}",
    "Тепловая карта доказательств: {0} × {1}",
    "Mapa de calor de evidencia: {0} × {1}",
    "エビデンスマップ ヒートマップ：{0} × {1}",
    "Mapa de calor de evidências: {0} × {1}",
    "Evidenz-Heatmap: {0} × {1}",
    "Топлотна мапа доказа: {0} × {1}",
    "근거 지도 히트맵: {0} × {1}"
  ],
  [
    "{0} 篇文献",
    "{0} references",
    "{0} références",
    "{0} публикаций",
    "{0} referencias",
    "文献 {0} 件",
    "{0} referências",
    "{0} Referenzen",
    "{0} референци",
    "문헌 {0}건"
  ],
  [
    "研究缺口：无研究覆盖",
    "Research gap: no studies cover this",
    "Lacune de recherche : aucune étude",
    "Пробел: нет исследований",
    "Vacío de investigación: sin estudios",
    "研究ギャップ：該当研究なし",
    "Lacuna de pesquisa: sem estudos",
    "Forschungslücke: keine Studien",
    "Празнина у истраживању: нема студија",
    "연구 공백: 해당 연구 없음"
  ],
  [
    "少",
    "Fewer",
    "Moins",
    "Меньше",
    "Menos",
    "少ない",
    "Menos",
    "Weniger",
    "Мање",
    "적음"
  ],
  [
    "多",
    "More",
    "Plus",
    "Больше",
    "Más",
    "多い",
    "Mais",
    "Mehr",
    "Више",
    "많음"
  ],
  [
    "研究缺口",
    "Research gaps",
    "Lacunes de recherche",
    "Пробелы в исследованиях",
    "Brechas de investigación",
    "研究ギャップ",
    "Lacunas de pesquisa",
    "Forschungslücken",
    "Празнине у истраживању",
    "연구 공백"
  ],
  [
    "主题簇（每簇前 {0} 个代表词）",
    "Topic clusters (top {0} terms per cluster)",
    "Groupes thématiques ({0} termes représentatifs par groupe)",
    "Тематические кластеры (по {0} характерных терминов)",
    "Grupos temáticos ({0} términos representativos por grupo)",
    "トピッククラスター（各クラスターの代表語 {0} 語）",
    "Agrupamentos temáticos ({0} termos representativos por agrupamento)",
    "Themencluster ({0} Leitbegriffe je Cluster)",
    "Тематски кластери ({0} репрезентативних појмова по кластеру)",
    "주제 군집(군집별 대표어 {0}개)"
  ],
  [
    "主题簇 {0}",
    "Topic cluster {0}",
    "Groupe thématique {0}",
    "Тематический кластер {0}",
    "Grupo temático {0}",
    "トピッククラスター {0}",
    "Agrupamento temático {0}",
    "Themencluster {0}",
    "Тематски кластер {0}",
    "주제 군집 {0}"
  ],
  [
    "{0} 篇",
    "{0} records",
    "{0} références",
    "{0} записей",
    "{0} referencias",
    "{0} 件",
    "{0} registros",
    "{0} Einträge",
    "{0} записа",
    "{0}건"
  ],
  [
    "（文本为空，无代表词）",
    "(empty text; no representative terms)",
    "(texte vide ; aucun terme représentatif)",
    "(пустой текст; характерных терминов нет)",
    "(texto vacío; sin términos representativos)",
    "（テキストが空のため代表語なし）",
    "(texto vazio; sem termos representativos)",
    "(leerer Text; keine Leitbegriffe)",
    "(празан текст; нема репрезентативних појмова)",
    "(텍스트가 비어 대표어 없음)"
  ],
  [
    "{0} 个空白单元格（图中虚线框）",
    "{0} empty cells (dashed frames in the figure)",
    "{0} cellules vides (cadres pointillés dans la figure)",
    "{0} пустых ячеек (пунктирные рамки на рисунке)",
    "{0} celdas vacías (marcos discontinuos en la figura)",
    "空白セル {0} 個（図内の破線枠）",
    "{0} células vazias (molduras tracejadas na figura)",
    "{0} leere Zellen (gestrichelte Rahmen in der Abbildung)",
    "{0} празних ћелија (испрекидани оквири на слици)",
    "빈 칸 {0}개(그림의 점선 테두리)"
  ],
  [
    "仅列出前 12 个；完整清单见图内虚线单元格。",
    "Only the first 12 are listed; the full list is in the dashed cells of the figure.",
    "Seuls les 12 premiers sont listés ; la liste complète est dans les cellules pointillées de la figure.",
    "Показаны только первые 12; полный список — в пунктирных ячейках рисунка.",
    "Solo se listan los 12 primeros; la lista completa está en las celdas discontinuas de la figura.",
    "先頭 12 件のみ表示。完全な一覧は図内の破線セルにあります。",
    "Apenas os 12 primeiros são listados; a lista completa está nas células tracejadas da figura.",
    "Nur die ersten 12 sind aufgelistet; die vollständige Liste steht in den gestrichelten Zellen der Abbildung.",
    "Наведено је само првих 12; потпун списак је у испрекиданим ћелијама на слици.",
    "처음 12개만 표시됩니다. 전체 목록은 그림의 점선 칸에 있습니다."
  ],
  [
    "仅显示前 50 篇，共 {0} 篇。",
    "Showing the first 50 of {0} references.",
    "Affichage des 50 premières références sur {0}.",
    "Показаны первые 50 из {0} публикаций.",
    "Se muestran las primeras 50 de {0} referencias.",
    "最初の 50 件のみ表示（全 {0} 件）。",
    "Mostrando as primeiras 50 de {0} referências.",
    "Die ersten 50 von {0} Referenzen werden angezeigt.",
    "Приказује се првих 50 од {0} референци.",
    "전체 {0}건 중 처음 50건만 표시합니다."
  ],
  [
    "该组合下没有匹配的文献。",
    "No references match this combination.",
    "Aucune référence ne correspond à cette combinaison.",
    "Под это сочетание нет публикаций.",
    "Ninguna referencia coincide con esta combinación.",
    "この組み合わせに一致する文献はありません。",
    "Nenhuma referência corresponde a esta combinação.",
    "Keine Referenzen passen zu dieser Kombination.",
    "Ниједна референца не одговара овој комбинацији.",
    "이 조합에 해당하는 문헌이 없습니다."
  ],
  [
    "文献列表不可用：分析页文献尚未加载完成，请稍后重试。",
    "Reference list unavailable: the analysis-page references have not finished loading; retry shortly.",
    "Liste de références indisponible : les références de la page d’analyse ne sont pas encore chargées ; réessayez sous peu.",
    "Список публикаций недоступен: публикации страницы анализа ещё не загрузились; повторите чуть позже.",
    "Lista de referencias no disponible: las referencias de la página de análisis aún no terminan de cargar; reintente en breve.",
    "文献一覧を利用できません：分析ページの文献がまだ読み込み完了していません。しばらくしてから再試行してください。",
    "Lista de referências indisponível: as referências da página de análise ainda não terminaram de carregar; tente novamente em breve.",
    "Referenzliste nicht verfügbar: die Referenzen der Analyseseite sind noch nicht geladen; versuchen Sie es gleich erneut.",
    "Списак референци недоступан: референце стране анализе још нису учитане; покушајте ускоро поново.",
    "문헌 목록을 사용할 수 없습니다: 분석 페이지 문헌이 아직 로드되지 않았습니다. 잠시 후 다시 시도하세요."
  ],
  ["主题簇根据当前文献库使用 TF-IDF+KMeans 自动分配。此处按另一维度（{0}）筛选文献；单篇归属不单独重算。", "Topic clusters are assigned across the current reference library using TF-IDF + K-means. This list filters by the other dimension ({0}); individual assignments are not recalculated here.", "Les groupes thématiques sont attribués à l’ensemble de la bibliothèque actuelle avec TF-IDF + K-means. Cette liste filtre selon l’autre dimension ({0}) ; les attributions individuelles ne sont pas recalculées ici.", "Тематические кластеры распределяются по текущей библиотеке публикаций методом TF-IDF + K-means. В этом списке действует фильтр по другому измерению ({0}); принадлежность отдельной публикации здесь не пересчитывается.", "Los grupos temáticos se asignan en toda la biblioteca actual mediante TF-IDF + K-means. Esta lista filtra por la otra dimensión ({0}); aquí no se recalcula la asignación de cada referencia.", "現在の文献ライブラリ全体を対象に TF-IDF + K-means でトピッククラスターを割り当てます。この一覧はもう一方の次元（{0}）で絞り込み、各文献の割り当てはここでは再計算しません。", "Os grupos temáticos são atribuídos em toda a biblioteca atual usando TF-IDF + K-means. Esta lista filtra pela outra dimensão ({0}); as atribuições individuais não são recalculadas aqui.", "Themencluster werden anhand der aktuellen Referenzbibliothek mit TF-IDF + K-means zugewiesen. Diese Liste filtert nach der anderen Dimension ({0}); einzelne Zuordnungen werden hier nicht neu berechnet.", "Тематски кластери се додељују у целој тренутној библиотеци референци методом TF-IDF + K-means. Овај списак филтрира по другој димензији ({0}); појединачне доделе се овде не прерачунавају.", "현재 문헌 라이브러리 전체를 대상으로 TF-IDF + K-means를 사용해 주제 군집을 할당합니다. 이 목록은 다른 차원({0})으로 필터링하며 개별 문헌의 할당을 여기서 다시 계산하지 않습니다."],
  ["按相同规则从当前文献库筛出 {0} 篇，与地图计数 {1} 不一致（生成地图后，文献库可能已变化）。", "Applying the same rules to the current reference library gives {0} references, compared with {1} on the map. The library may have changed since the map was generated.", "L’application des mêmes règles à la bibliothèque actuelle donne {0} références, contre {1} sur la carte. La bibliothèque a peut-être changé depuis la création de la carte.", "Применение тех же правил к текущей библиотеке публикаций даёт {0} ссылок, тогда как на карте указано {1}. После построения карты библиотека могла измениться.", "Al aplicar las mismas reglas a la biblioteca actual se obtienen {0} referencias, frente a {1} en el mapa. La biblioteca pudo cambiar desde que se generó el mapa.", "現在の文献ライブラリに同じ規則を適用すると {0} 件となり、地図の件数 {1} と一致しません。地図の作成後にライブラリが変更された可能性があります。", "A aplicação das mesmas regras à biblioteca atual resulta em {0} referências, enquanto o mapa mostra {1}. A biblioteca pode ter mudado desde a geração do mapa.", "Nach denselben Regeln ergeben sich in der aktuellen Referenzbibliothek {0} Einträge, während die Karte {1} zeigt. Die Bibliothek kann sich seit der Kartenerstellung geändert haben.", "Применом истих правила на тренутну библиотеку добија се {0} референци, док мапа приказује {1}. Библиотека се можда променила од прављења мапе.", "현재 문헌 라이브러리에 같은 규칙을 적용하면 {0}건이며 지도에는 {1}건이 표시됩니다. 지도를 만든 뒤 라이브러리가 변경되었을 수 있습니다."]
];
  RFLang.register(pairs);
  const bySource = new Map(pairs.flatMap(([zh,en]) => [[zh,zh],[en,zh]]));
  const term = value => {
    const raw = value == null ? '—' : String(value);
    return RFLang.t(bySource.get(raw) || raw);
  };
  function message(value) {
    const raw = String(value ?? '');
    const translated = term(raw);
    const host = document.createElement('span');
    if (RFLang.get() === 'en' || translated !== raw || /[\u3400-\u9fff]/.test(raw) || !/[A-Za-z]{3}.*\s+[A-Za-z]{3}/.test(raw)) {
      host.textContent = translated; return host;
    }
    host.className = 'an-technical-message';
    const summary = document.createElement('span');
    summary.textContent = RFLang.t('此信息暂未提供当前语言译文；展开可查看原文。');
    const details = document.createElement('details');
    const label = document.createElement('summary'); label.textContent = RFLang.t('查看原始信息');
    const content = document.createElement('div'); content.textContent = raw; content.lang = 'en'; content.setAttribute('translate','no');
    details.append(label,content); host.append(summary,details); return host;
  }
  window.RFAnalysisLocale = Object.freeze({term,message});
})();
