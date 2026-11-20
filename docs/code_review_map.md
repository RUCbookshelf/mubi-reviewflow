# 代码评审快速定位图谱（Code Review Map）

> 2026-11-12 由 GitNexus（v1.6.9）索引生成，索引基线 commit `2226cad`（main）。
> 刷新：仓库根目录跑 `gitnexus analyze`（大改动/合并后跑一次）。语义/FTS 检索未启用（缺扩展，`gitnexus analyze --repair-fts` 可补），图查询不受影响。
> 本文档=静态骨架 + 评审热点；逐符号查询用文末速查卡的命令，不要靠 grep 猜。

## 一、三层架构与依赖铁律

```
┌────────────────────────────────────────────────────────────┐
│ custom_frontend/（无框架原生 JS，~30k 行）                  │
│   index.html 单页 + 追加式模块挂载（IIFE + RFLang 十语言）  │
│   与后端仅经 HTTP api() 通信，无直接调用                    │
└──────────────────────────┬─────────────────────────────────┘
                           │ HTTP（368 条路由）
┌──────────────────────────▼─────────────────────────────────┐
│ custom_backend/（FastAPI 编排层，225 个 py）                │
│   main.py 单体 176 条路由（任务/决定/分析/团队基础域）      │
│   + 40 个 feature_*_api.py（2.1.5 起按波次分文件）          │
└──────────────────────────┬─────────────────────────────────┘
                           │ 218 条调用边，方向单向 ▼（铁律：core 不反向 import backend/frontend）
┌──────────────────────────▼─────────────────────────────────┐
│ coscreen/（纯计算/纯 SQL 核心库）                           │
│   决定/导入/去重/全文/轨迹/统计/AL/评价/检索式/通知         │
│   零副作用纪律：多数模块不连库不起服务不做网络              │
└────────────────────────────────────────────────────────────┘
```

规模：494 索引文件 / 4541 函数 / 1998 方法 / 469 类 / 300 执行流 / 368 路由 / 205 个测试文件（build/tests/）。

## 二、后端 API 面（改接口前先看这张表）

| 域 | 文件 | 路由数 |
|---|---|---|
| **单体基础域** | `custom_backend/main.py`（8598 行） | **176** |
| AI 建议/钥匙柜 | ai_suggest_api.py | 17 |
| 筛选训练 | pilot_screening_api.py | 16 |
| 检索式管理 | search_strategy_api.py | 16 |
| 工作流筛选 | review_workflow_api.py | 12 |
| 导入复核 | review_imports_api.py | 10 |
| 撤销工作区 | workspace_undo_api.py | 10 |
| 研究轨迹 | research_trace_api.py | 10 |
| 打卡提醒 | streak_api.py | 10 |
| 偏倚共享 | rob_shared_api.py | 9 |
| 编码共识 | coding_consensus_api.py | 8 |
| 其余（每个 1–6 条） | report_evidence / local_storage / task_package / pdf_fetch / import_ai_assist / appraisal / study_intervals / retraction_watch / appraisal_agreement / zotero_local / highlight / tables / eric_search / 各统计 view_* / criteria_panel / binary_reset / eric_incremental / study_design / arbitration_session 等 30+ 文件 | ~90 |

评审提示：**main.py 是改动最频繁、扇出最大的单点**（近一周 17 次改动居首）；新功能一律落独立 `*_api.py` + main.py 一行注册，评审时看到往 main.py 塞大段新逻辑应质疑。

## 三、coscreen 计算核心分区

| 子域 | 模块 |
|---|---|
| 筛选与决定 | review_workflow、screening_arbitration、fulltext、binary_reset 相关 |
| 导入与去重 | review_imports、dedup、normalize、research_imports 系列 |
| 主动学习排序 | al/ 包（features/nb/svm/profiles/ranker/strategies/stop_*） |
| 统计合成 | review_analysis、network_gls/random、meta_regression、cluster_*、dta_*、survival_*、stats/（kappa、prisma_form） |
| 报告导出 | export、manuscript_builder、synthesis_report、analysis_audit_report |
| 轨迹 | research_trace、research_trace_export |
| 偏倚评价 | rob_storage、risk_of_bias_contract、rob_v2_sensitivity、appraisal |
| 2.1.5 新增 | pilot_screening、search_strategy、eric_incremental、notify、topic_order 等 |

## 四、前端结构（评审前端 PR 时）

- **挂载模式**：模块 = IIFE 追加挂到既有 DOM 锚点（参照 living_review.js 头注释的接入范式），锚点缺失时安静退出——评审新前端件先看锚点兜底。
- **十语言**：RFLang.register 词典（interface_locale.js 6096 行、analysis_locale.js 3834 行是两大词典）；改文案=只动词典行，十列对齐由 build/tests 校验器钉住。
- **共享件（扇入最高，改前必查调用方）**：pdf_fetch.js（68 个文件调用）、report_evidence.js（53）、analysis_audit_download.js（23）、unified_import_workspace.js。
- 渲染安全铁律：一切 textContent，禁 innerHTML。

## 五、三条数据主干（评审时先定位主干再看分支）

1. **导入→复核→入库**：上传 → review_imports（staged）→ dedup 候选对 → 导入复核（人工/AI 建议）→ activate 落 articles → 任务库。涉及 import_review_* / dedup / normalize；重复判定规则改动必查 test_r10_u2_bulk_identity 跨语言对照钉。
2. **筛选→决定→分析→报告**：队列（排序策略 max/mixed/uncertainty/cluster）→ 决定写入（per-screener {slug}.db）→ 仲裁 → analysis_storage → 导出/报告/PRISMA。涉及 review_workflow_api / screening_arbitration / al/ / export。
3. **增量订阅**：已保存检索式（检索式库）→ living_review check-now（PubMed/ERIC 分派）→ 虚拟 RIS → 受保护导入待审（回到主干 1）。

## 六、评审热点区（近期 churn × 高扇入交集优先看）

| 信号 | 文件 |
|---|---|
| 改动最频（近一周） | custom_backend/main.py（17）、docs/openapi.json、API_REFERENCE.md、task_package_api、tables_api、review_workflow_api、streak_api、pdf_fetch_api、import_ai_assist_api |
| 高扇入核心 | coscreen/review_analysis.py（21 个调用方）、db.py、models.py、dedup.py、normalize.py、source_provenance.py、analysis_audit_report.py |
| 大文件（评审拆分意识） | main.py 8598 行；前端 interface_locale.js 6096、analysis_locale.js 3834 |
| 已知循环依赖（3 处，非新问题但评审改动时留意） | ① coding.py ↔ workspace_undo.py；② import_review_actions.py ↔ import_review_presentation.py；③ appraisal_agreement_api → tables_api → main.py → appraisal_agreement_api |

文档同步钉：改路由/契约的 PR 必须同步 openapi.json + API_REFERENCE.md + RELEASE_NOTES（有计数等值钉把关，合入前实算刷新）。

## 七、测试与门禁速查

- 后端测试：`build/tests/`（205 文件，命名 `test_<波次/域>_<主题>.py`；2.1.5 系 `test_2_1_5_*.py`）。跑法见仓库 README/CLAUDE.md。
- 门禁：全量 pytest + node --test + 四校验器 + openapi fresh + SQL 参数扫描（`build/scripts/scan_dynamic_sql.py`，Mimosa 治理）。
- 浏览器套件：build/tests/*.cjs（Playwright；import_review 套件会覆写跟踪截图，发布前 git restore）。

## 八、GitNexus 速查卡（评审时逐符号下钻）

```bash
gitnexus query "导入复核 批量确认"     # 概念→执行流
gitnexus context decide_candidate      # 符号 360°：调用方/被调/所在流
gitnexus impact decide_candidate       # 改动爆炸半径（depth 1-3 + 置信）
gitnexus trace decide_candidate activate  # 两符号最短调用路径
gitnexus detect-changes                # 当前 git diff → 受影响符号与执行流
gitnexus check --cycles                # 循环依赖
gitnexus cypher "MATCH (a)-[:CodeRelation {type:'CALLS'}]->(b) WHERE a.filePath='custom_backend/main.py' RETURN b.filePath, count(*) ORDER BY 2 DESC LIMIT 20"
gitnexus status                        # 索引是否落后于当前 commit（落后就 analyze）
```

典型评审动线：`gitnexus detect-changes` 看本次改动落在哪些执行流 → 对核心被改符号跑 `impact` → 跨层改动跑 `trace` 确认前后端会合点 → 对照本文档第五节判断属于哪条主干。
