# 预置研究设计分类器方法学与评估披露（W4 · v1）

- 版本：v1 · 2026-10-30 · 随 2.1.4 发布（W4 空白格泳道 · B3 竞品空白格核销）
- 被描述实现：`coscreen/al/study_design.py`（纯本地分类函数）、
  `custom_backend/study_design_api.py`（只读端点 `GET /api/tasks/{id}/study-designs`）、
  `custom_frontend/study_design_badge.js`（初筛卡徽标 + 排除理由预填不代存）
- 规则版本：`study_design.RULE_VERSION = 1`（判定因子变化时 bump，本文件 §5 的
  评估表同步重测）

---

## 1. 这是什么、不是什么

预置分类器对**题名 + 摘要 + raw 出版类型字段**跑一组**精确优先的有序正则**
（meta→系统综述→RCT→队列→病例对照→横断面→诊断准确性，先命中先归类，
都不中归「未识别」），把结果显示为初筛卡上的分类徽标，并在判为
**非 RCT 的已识别设计**时**预选**「研究设计不符」排除理由（只填控件，
绝不代存——沿用 AI 建议「采纳＝填控件」先例，用户改过则不打扰）。

**它不是**：语义模型、机器学习分类器、或设计判定的权威来源。它是
`evidence_mapper` 的 `study_design` 维度**同一套规则**（`_STUDY_DESIGN_RULES`，
`coscreen/evidence_mapper.py:80-93`；规则优先级与方法学讨论见该模块
docstring §78-94「精确优先，宁缺毋滥」）在初筛场景的复用——两处判定
永不漂移。

## 2. 两路信号与加权

| 信号 | 来源 | 权重 |
|---|---|---|
| 主信号 | 题名 + 摘要 | **永远优先**：命中即定类 |
| 辅信号 | raw_json 的出版类型类字段（RIS `unknown_tag['PT'/'TY']`、rispy `type_of_reference`、CSV `publication_type(s)`/`pubtype(s)` 列） | 仅当主信号未命中时兜底（`source: "raw_json"`） |

主信号压轴的动机：PubMed 会给「纳入 RCT 的 Meta 分析」同时挂
`Meta-Analysis` 与 `Randomized Controlled Trial` 两个出版类型——题名里写了
meta-analysis 的记录不能被 PT 里的 RCT 抢走。反之，题名/摘要不提设计的
RCT（本评估 6 条中有 3 条）靠 PT 兜底找回。

**未识别 ≠ 研究设计不符**：`other` 不产生任何排除建议。预填只发生在
「识别为某个具体的非 RCT 设计」时。

## 3. 评估夹具（公开样例，来源与许可）

- 来源：PubMed E-utilities（`https://eutils.ncbi.nlm.nih.gov`，公开端点），
  esearch+efetch，取数日 **2026-10-30**；每类 6 条共 **48 篇独立文献**
  （48 个互不相同的 PMID；同一 PMID 跨类/类内重复按先到先得剔除，本次剔除
  2 条候选——两篇 cohort 与 cross_sectional 双重命中的记录）。
  取数脚本随库分发：`build/scripts/fetch_study_design_fixture.py`；
  夹具：`build/tests/study_design_fixture.json`（内嵌检索式、去重规则与许可以下同）。
- 指标口径：**逐记录 = 逐 PMID**。夹具无重复键，§4 表格与 §5 机器块均按
  48 条记录计算，不存在「键数 / 记录数」分歧。
- **摘要截断至 400 字符**入库（降低全文再分发面）。**这不是无害的取样选择**：
  本夹具 42 条设计类金标的摘要**全部**超过 400 字符（实测最短者也超线），
  因此 §4 的 cohort/diagnostic 低召回主要是截断造成的度量缺口，而非规则在
  全文上的真实召回（量化见偏差 3）。题录与摘要片段版权归原作者与 PubMed，
  仅作测试夹具随库分发，不作商业用途。
- 金标 = 取数检索式所属类别（rct/systematic_review/meta_analysis 用
  `[Publication Type]` 检索；cohort/case_control/cross_sectional/diagnostic
  用题名摘要词检索；负类 `other` 取 `case reports[Publication Type]`）。

### 必须直视的偏差（诚实披露）

1. **选择偏差**：多数类别的取样词与分类关键词**同源**（按 PT 取 RCT、按
   题名摘要词取队列研究）。本表度量的是「规则对典型文献表述的覆盖面」，
   **不是**真实筛选流上的判别力——真实流里 RCT 摘要不写 "randomized
   trial" 的比例可能不同（本夹具 title/abstract-only 消融显示 RCT 召回
   仅 0.50，即一半 RCT 题名摘要不含规则词，全靠 PT 兜底）。
2. **小样本**：每类 6 条；任何一个 TP/FP 的变动都会 ±0.17 量级地移动
   该类 precision/recall。不要把数值当作性能承诺。
3. **截断是低召回的主因，且已量化**：金标由「规则词出现在题名/摘要」的
   检索式定义，所以规则词**必然**存在于全文摘要中。把 17 条设计类 FN 的
   完整摘要（raw XML 重取，未截断）重新喂进同一分类函数后，**11 条立即
   归位**（cohort 5/5、diagnostic 4/5、cross_sectional 1/1 等），仅 6 条仍
   错（见偏差 4/5）。逐条位置实测：10 条 cohort+diagnostic FN 中，8 条的
   规则词位于第 **400 字符之后**（如 cohort@1656、diagnostic@1491），1 条
   正好在线前 398 字（另一条被判成 cohort 抢先），1 条全文摘要确实不含规则
   词。结论：**§4 的 cohort/diagnostic 召回度量的是「400 字前缀内的规则词
   覆盖」，不是规则在全文上的召回**——产品实际入库存的是完整摘要，故线上
   表现应优于该列。全文摘要下的上界（同规则、不截断）：cohort 0.60/1.00、
   diagnostic 1.00/0.83、42 条设计金标精确命中 35 条（截断下 25 条）。
4. **多设计文献**：规则取**首个命中**（特异性优先），不是文献的主导设计。
   本夹具中 meta_analysis 的 2 个 FP 均为 SR/MA 混合出版物（题名同时出现
   "systematic review" 与 "meta-analysis"，按优先级判为 Meta 分析），
   correspondingly systematic_review 有 2 个 FN。
5. **全文摘要下仍错的 6 条**（与截断无关，是规则本身的已知行为）：
   2 条 SR/MA 混合出版物按优先级判为 Meta 分析；3 条病例对照的完整摘要里
   "cohort" 出现得比 "case-control" 更早，按「首个命中」被判为队列研究；
   1 条诊断类全文摘要确实不含 `diagnostic accuracy` /
   `sensitivity and specificity` 字样。前两类是「特异性优先 + 首个命中」
   的既定取舍（宁可少判，不臆测）；第三类是**规则覆盖缺口**，若未来要提升
   recall，应扩规则词表而非放宽匹配。
6. **`other` 行不是「负类准确率」**：§4 的 other 行按类级口径统计，
   tp=5/fp=15 表示 15 条**其他金标类别**的记录落入未识别（即各类 FN 的
   一部分），not 15 条病例报告被错标成设计类。

## 4. 实测 precision / recall（2026-10-30，规则版本 1）

全管线（题名+摘要主信号 + raw 出版类型兜底）与消融（仅题名+摘要）：

| 类别 | 全 P | 全 R | 仅题名摘要 P | 仅题名摘要 R | 全 tp/fp/fn |
|---|---|---|---|---|---|
| Meta 分析 | 0.75 | 1.00 | 0.75 | 1.00 | 6/2/0 |
| 系统综述 | 0.80 | 0.67 | 0.80 | 0.67 | 4/1/2 |
| 随机对照试验 | **1.00** | **1.00** | 1.00 | 0.50 | 6/0/0 |
| 队列研究 | 1.00 | 0.17 | 1.00 | 0.17 | 1/0/5 |
| 病例对照 | 1.00 | 0.33 | 1.00 | 0.33 | 2/0/4 |
| 横断面研究 | 1.00 | 0.83 | 1.00 | 0.83 | 5/0/1 |
| 诊断准确性 | 1.00 | 0.17 | 1.00 | 0.17 | 1/0/5 |
| 未识别（类级口径，见偏差 6） | 0.25 | 0.83 | 0.22 | 0.83 | 5/15/1 |

读法提示（与 §3 偏差对照）：

- **RCT 类 1.00/1.00** 主要由 PT 兜底支撑（消融召回 0.50）——真实收益
  就是「PubMed RIS 导入（带 PT 标签）场景下 RCT 徽标可靠」；纯 CSV
  导入（无 PT 列）时按消融列理解。
- 各识别类 precision 最低 0.75（Meta 分析）、其中 5/7 类 = 1.00：**宁缺毋滥**
  取向的直接体现——代价是队列/诊断准确性两类的召回（各 0.17）。**但这两列
  是截断度量的下限，不是产品表现**：完整摘要下 cohort 召回 1.00、diagnostic
  0.83（偏差 3 的实测上界），产品存的是完整摘要。
- 「未识别」行按类级口径读（tp/fp/fn=5/15/1，共判为未识别 20 条）：
  tp=5 = 真病例报告未被误识别；fp=15 = **其他金标类别**的记录因关键词缺失
  落入未识别（它们同时计入各自类别的 FN）；fn=1 = 1 条病例报告因摘要含设计
  词被识别成某个具体设计。因此 P=0.25 绝不是「15 条病例报告被错标成设计类」
  ——反方向的错误（设计类被判成 other）体现在 fp 列与前七行的 FN。
- **识别类 FN 合计 17 条**（meta 0 + 系统综述 2 + rct 0 + 队列 5 + 病例对照
  4 + 横断面 1 + 诊断 5）：其中 **15 条落入 other**（与 other 的 fp 同源），
  另 **2 条**是 SR/MA 混合出版物被判为 Meta 分析（PMID-42959033、
  PMID-42959031）。17 条中有 11 条在完整摘要下即归位（见偏差 3）。

## 5. 机器可读评估块（与 `build/tests/test_w4_study_design.py` 钉子互锁）

测试实时重算下表并与本块逐值比对：规则或夹具任一变化导致数值漂移时，
**文档与代码的这把锁立即变红**——更新本块必须伴随规则版本 bump 或
夹具重取的说明。

```json
{
  "rule_version": 1,
  "n": 48,
  "per_class": {
    "full": {
      "meta_analysis": {
        "tp": 6,
        "fp": 2,
        "fn": 0,
        "precision": 0.75,
        "recall": 1.0
      },
      "systematic_review": {
        "tp": 4,
        "fp": 1,
        "fn": 2,
        "precision": 0.8,
        "recall": 0.67
      },
      "rct": {
        "tp": 6,
        "fp": 0,
        "fn": 0,
        "precision": 1.0,
        "recall": 1.0
      },
      "cohort": {
        "tp": 1,
        "fp": 0,
        "fn": 5,
        "precision": 1.0,
        "recall": 0.17
      },
      "case_control": {
        "tp": 2,
        "fp": 0,
        "fn": 4,
        "precision": 1.0,
        "recall": 0.33
      },
      "cross_sectional": {
        "tp": 5,
        "fp": 0,
        "fn": 1,
        "precision": 1.0,
        "recall": 0.83
      },
      "diagnostic": {
        "tp": 1,
        "fp": 0,
        "fn": 5,
        "precision": 1.0,
        "recall": 0.17
      },
      "other": {
        "tp": 5,
        "fp": 15,
        "fn": 1,
        "precision": 0.25,
        "recall": 0.83
      }
    },
    "title_abstract_only": {
      "meta_analysis": {
        "tp": 6,
        "fp": 2,
        "fn": 0,
        "precision": 0.75,
        "recall": 1.0
      },
      "systematic_review": {
        "tp": 4,
        "fp": 1,
        "fn": 2,
        "precision": 0.8,
        "recall": 0.67
      },
      "rct": {
        "tp": 3,
        "fp": 0,
        "fn": 3,
        "precision": 1.0,
        "recall": 0.5
      },
      "cohort": {
        "tp": 1,
        "fp": 0,
        "fn": 5,
        "precision": 1.0,
        "recall": 0.17
      },
      "case_control": {
        "tp": 2,
        "fp": 0,
        "fn": 4,
        "precision": 1.0,
        "recall": 0.33
      },
      "cross_sectional": {
        "tp": 5,
        "fp": 0,
        "fn": 1,
        "precision": 1.0,
        "recall": 0.83
      },
      "diagnostic": {
        "tp": 1,
        "fp": 0,
        "fn": 5,
        "precision": 1.0,
        "recall": 0.17
      },
      "other": {
        "tp": 5,
        "fp": 18,
        "fn": 1,
        "precision": 0.22,
        "recall": 0.83
      }
    }
  }
}
```

## 6. 产品行为与治理红线对照

- **纯本地**：端点与分类函数零联网、零定时任务、**零写副作用**（不建
  成员筛选员库、不写任何评审表——取数自行解析属主库路径 + 只读读取，特
  意不调 `_screener_db`）；无开关即无「默认开」的联网面（与 W1/W2/W3 的
  联网功能不同，本功能没有出站）。
- **降级**：`全部`导入（含分类模块本身）/上下文/读库/分类异常 →
  `200 + available=false`（绝不 500；导入失败也在同一 try 内，钉子实测
  注入 ImportError 仍 200）。前端拉取失败 → 徽标移除、预填跳过，初筛流程
  不受影响，且同一作用域内不重试（避免重试风暴）。
- **部分结果整体降级**：目录超过 20000 条时后端回 `truncated=true`，前端
  把「部分结果」整体视作不可用（不展示部分徽标）——宁可没有徽标，也不
  让未列出的文献看起来像「未识别」。
- **占位不留尸**：数据就绪（成功或失败）后新渲染的卡片若无分类，`badge()`
  直接返回 `null`（调用方 filter 剔除），绝不留下永久「…」占位；数据在途
  时的占位由数据到达帧的补绘决定去留。
- **缓存随目录变更失效**：`reviewflow:import-completed`（导入激活后派发）与
  `reviewflow:workflow-changed`（目录/策略变更）到达即丢弃缓存，下一次卡片
  渲染重新取数——因此新导入并激活的文献无需切任务/重开应用就能拿到徽标；
  仍保持「每作用域一次拉取」，**不引入轮询或后台请求**。若当时正停在初筛页
  且理由草稿干净，顺手重绘一次；草稿未保存（decisionDirty）时不重绘，避免
  冲掉用户输入。
- **预填不代存**：仅设置 `#excReason` 的值并派发 `change`；保存仍由用户
  点击「3 排除」触发。用户手动改过理由后，本卡片不再自动改值。
- **隐私披露**：只访问本应用自身 API（`/api/tasks/{id}/study-designs`），
  无任何回环/外部地址；评估取数（开发期一次性脚本，**不属产品出站面**）
  访问 PubMed 公开端点，出站同样走「显式 UA + 局部超时 + 读取上限 +
  拒绝 3xx 重定向」同款约束，并由零网络钉子
  （`FetchScriptEgressNails`：注入假 opener 断言 UA/超时入参/读取上限、
  断言 3xx 被拒）守护。**复现口径如实声明**：重跑脚本生成的是**同构**清单
  而非同一批 PMID（PubMed 结果集随时间漂移）；数值结论锁定在本仓库已入库
  的这份夹具上。
