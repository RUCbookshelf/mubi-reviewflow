# 研究轨迹包 DOI 关联口径（wave D4 契约）

> 适用范围：`POST /api/tasks/{task_id}/trace/imports/preview` 与 `…/imports/commit`
> 返回的 `subjects[].candidates`（`match_basis` 为 `doi` 的条目）。
> 实现：`custom_backend/research_trace_api._import_metadata`。

## 1. 结论

**期刊样式 DOI 不参与轨迹关联。** 形如 `10.1002/(ISSN)1545-7249` 的出版社/
期刊级标识在 DOI 关联中等同于「无 DOI」：来源侧（导入包的
`baseline_item.after.doi`）与目标侧（库内 `articles.doi`）同口径剔除后再做
等值匹配。真实文章 DOI 的关联行为不变；`zotero_key` 等值（`match_basis='key'`）
不受影响。

判定复用 `coscreen.dedup.is_journal_pattern_doi`（r17 引入的公开纯函数）：
`normalize_doi` 之后含 `"(issn)"` 片段即为样式 DOI，大小写与全角括号变体
（`(ISSN)`、`（ISSN）`）同判。**不新写规则**，与 dedup/导入复核共用同一口径。

## 2. 为什么必须收口（输入来源与匹配语义）

- **来源侧不是受控输入**：正常导出时 `baseline_item` 的 `after.doi` 直接取自
  `articles.doi`（`coscreen/research_trace.py:_snapshot_articles_and_protocol`，
  原样写入、不过滤）；导入时该字段又原样来自用户上传的包——
  `coscreen/research_trace_export._validate_event` 只校验字段类型/长度，
  不校验 DOI 语义。因此样式 DOI 既可能随「导出→再导入」自然流入，也可能是
  上传包里手写的值。
- **目标侧同样保留样式 DOI**：r17 只把样式 DOI 摘出**身份键**，记录、导出与
  展示仍原样保留（`coscreen/dedup.py:_identity_doi` 的注释口径），所以库里
  同刊不同文献共享同一个样式 DOI 是常态。
- **匹配语义**：两侧各自 `normalize_doi` 后等值连接。样式 DOI 相等但对应
  **不同文献**，此前会让一个导入主体同时挂到该刊全部文献上——这正是 r17 在
  dedup 侧修掉、而轨迹侧遗留的误关联（wave D4 回填）。

修复前实测（两条不同文献 `ART-A`/`ART-B` 同带样式 DOI）：
`SRC-PATTERN -> [('ART-A','doi'), ('ART-B','doi')]`。

## 3. 落地与钉子

- 实现：`_import_metadata` 在构建来源 DOI 集合时跳过样式 DOI；库侧临时表
  （`trace_import_doi_targets`）在 `ROW_NUMBER()` 截断前用同一判定过滤，
  与 dedup 的「双侧同口径」保持一致。
- 钉子：`build/tests/test_2_1_5_trace_doi.py`
  - 同一主体仅带样式 DOI → 无任何 `doi` 候选（撤修复必红）；
  - `(ISSN)`/`（ISSN）` 变体同判；
  - 真实 DOI 仍关联到唯一目标（对照，修复前后同为绿）；
  - 主体键命中且带样式 DOI → 仅保留 `key` 候选，不额外挂同刊其他文献；
  - `_import_metadata` docstring 自述该口径（文档钉子）。
- SQL 安全：新增分支只调用 SQLite UDF 与固定文本，无拼接值；动态 SQL 扫描器
  退出码 0，白名单未新增条目（`research_trace_api.py` 三条既有条目仅按行号
  平移重钉）。
