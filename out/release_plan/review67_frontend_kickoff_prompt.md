# 2.1.5 review67 波 · 前端交接 prompt（M-13 删镜像读载荷 ‖ fe-pdf 文案/明细桶/防弹窗/防抖 ‖ fe-report 两清 ‖ fe-import 三对齐 ‖ analysis.rob.* 55 键×10 ‖ GRADE 推导依据恢复）

> 你是木笔 ReviewFlow 的前端智能体。本文档是 review67 前端批（源自 2026-11-12 全仓 22 泳道评审的 15 条前端/前端侧发现，逐条已在 main@2226cad 读码核实）的唯一交接入口，把本文件全文作为任务书执行。行号口径：**本文所有行号基于 main@2226cad**；你的基分支（aiux 前端波）会移动 `unified_import_workspace.js` 等文件的行号——一律按引用的**代码锚文本**重新定位，不信行号。

## 0. 铁律（防撞，先读三遍）

1. **串行依赖**：本批与 aiux 前端波同文件域（`unified_import_workspace.js`/`import_review_updates.js`/bulkDialog 等），**必须基于本地分支 `feat/2.1.5-aiux-frontend` 的顶端提交**新建分支 **`fix/2.1.5-review67-frontend`**。开工前先 `git rev-parse --verify feat/2.1.5-aiux-frontend`——**该分支截至本文撰写时点（2026-11-12）尚未落库**；不存在即停下申报等待，**禁止改基 main 或 feat/2.1.5-aiux-backend 开工**。工作树用 **E:/rf215-authfe**（前端专用树；确认 aiux 波已在该树完成并离开后再动工）。
2. **只改 `custom_frontend/`**（产品代码）；测试仅限 `build/tests/*.test.cjs`（新建＋受影响修订）与 `.github/workflows/release.yml` 的测试清单行（沿 aiux 波先例）。禁改 `custom_backend/`、`coscreen/`、`build/` 其他脚本、docs。**唯一申报例外**：M-13 需退役 `build/tests/test_r10_u2_bulk_identity.py`（.py）——该文件头 docstring 已自declare「前端按 F2 删镜像、改读 bulk_eligible 后，本钉退役」；若主代理否决此例外，勿自行绕过，在偏差申报中留待主代理处理。
3. **禁入其他工作树**：E:/rf215-r67（评审修复批在用）、E:/rf215-aiux、E:/rf215-auth、E:/rf215-auto、E:/mubi-reviewflow。
4. 词典新增一律 10 语种（zh/en/fr/ru/es/ja/pt/de/sr/ko）`RFLang.register` 行：十槽全非空、`{0}{1}…` 占位跨槽一致（十槽皆可作 T() 键，代码里的英文串命中的是 en 槽）。凡改动的既有 script 一律递增 `?v=` 令牌（本批统一用 `?v=20261112-r67`）：当前令牌见 `custom_frontend/index.html:9848-9850`（import_review_refined_locale/import_review_updates `?v=20261101-r10u2`、unified_import_workspace `?v=20261107-r14`）、`:9854`（analysis_grade `?v=20261030-r8fl5`）、`:9868`（report_evidence `?v=2.1.4-r5`）、`:9876`（pdf_fetch `?v=20261030-r8-w2pdf1`）。新建 locale 文件按先例挂 index.html 新 script 标签（自带令牌）。
5. 仓规：注释一律中文；错误必须结构化（code/message_key），绝不裸 500/裸英文技术串直达用户；钥匙/敏感值绝不进日志或响应；**每处改动旁注明 review67 编号**（如 `/* review67 M-13 */`）。
6. 完成后在你的分支本地提交（不 push），回报：提交哈希、文件清单、node 测试数（先跑基分支基线再跑你的改动后，两组数）、真实后端走查结论、偏差申报（任何与本 prompt 不符处如实列出，勿自行改后端）。

## 1. 载荷契约（已实现并钉测，按此对接，勿猜）

### 导入复核候选载荷（单一事实源在后端）
- `present_candidates`（custom_backend/import_review_presentation.py:150-153）逐候选下发：**`bulk_eligible`:bool**、**`bulk_ineligible_reason`**:str|null、**`suggested_keeper`**:`{keeper_key, keeper_record_id, reasons:[...]}`。三字段已由 `build/validate_import_review.py:102-112` 与后端判据/建议函数**逐值同源钉死**（含 suggested_keeper；伪造会在校验器变红）。
- 不可批量原因键全集（import_review_actions.py:71-102）：`identity_conflict` / `status` / `insufficient_members` / `active_conflict` / `doi_conflict` / `missing_identity` / `generic_title` / `short_title`（eligible 时为 null）。判据三段：a 全员身份 DOI 一致（**期刊样式 DOI 视同缺 DOI**）；b 全员无身份 DOI＋规范化题名/第一作者姓氏/年份三项一致非空；c 部分成员有身份 DOI 且唯一取值＋三项一致；b/c 排除通用/过短标题。
- `suggested_keeper.reasons` 键集（coscreen/review_imports.py:100-101 + presentation.py:107-128）：`has_doi`/`has_abstract`/`has_journal`/`has_year`/`has_authors`/`has_title`/`active_member`，由前端渲染文案。确认接口仍要求显式 keeper——建议只是默认值。
- 批量端点不变：`POST /api/tasks/{id}/import-batches/{batch_id}/bulk-review`（≤100/次、全有或全无；ineligible → 409 `import_merge.bulk_ineligible`）。

### pdf-fetch 错误与批量结果
- 错误载荷 `{code,...}`。后端 code 全集（本批 grep custom_backend/pdf_fetch_api.py 实测）：`pdf_fetch_disabled`/`base_url_invalid`/`base_url_insecure`/`base_url_blocked`/`email_missing`/`email_required`/`email_invalid`/`settings_version_conflict`/`article_missing`/`doi_missing`/`invalid_pdf`/`unsafe_url`/`upstream`/`storage`/`would_replace`/`trace_conflict`。
- fetch-batch 结果 `outcome∈{saved,no_oa,failed,skipped,missing,invalid,blocked,unresolvable}`；`skipped` 的 `reason∈{already_has_pdf, invalid_key, doi_missing, aborted_upstream_unreachable, stage_paused 等工作流族}`（doi_missing 见 pdf_fetch_api.py:978；前端预过滤只查 `a.doi` 非空，后端要求 `^10.` 前缀 → abc/含空格 DOI 前端放行、后端跳过，这是 L-15 明细缺桶的成因）。

### RoB 契约错误（L-38 词条的对接面）
- 后端 `_rob_contract_error`（custom_backend/main.py:1543-1547）：HTTP 载荷 `{error_code:<code>, message_key:"analysis.rob.<code>", params:exc.details, details:str(exc), message:str(exc)}`；status：`revision_conflict`→409、`reviewer_forbidden`→403、其余 400。
- 前端渲染链已存在：`api()`→errOf（index.html:5367-5370）`payload.message_key`→`window.RFServerMessage(key)`→`T(中文源串)`；`SERVER_MESSAGE_SOURCES`（index.html:5202-5204）目前仅 1 键。**注意两处现状缺口**：RFServerMessage 现签名不收 params；rob 载荷的 `params` 是 dict（exc.details，键如 field/domain/framework）。

## 2. 工作项（编号=总报告 E:/mubi-reviewflow/out/code_review_2026-11-12/REPORT.md 条目号；每条附核实锚点）

### M-13（medium·必修）删前端镜像判据，改读载荷字段
- 删 `custom_frontend/import_review_updates.js` 的镜像三件：`eligible()`（:31-36）＋其私有助手 `normDoi()`/`identityKey()`（:14-30，仅被 eligible 引用）＋头注释镜像说明块（:5-13）；模块其余导出（`merge`/`create`/`needsReload`/`canActivate`）不动。
- 两处消费点改读字段（unified_import_workspace.js，锚文本定位）：①toolbar 批量按钮可用性 `!groups.some(ImportReviewUpdates.eligible)`（:153 锚 `const bulk=write('Confirm eligible duplicates'`）→ `!groups.some(g => !g.legacy && g.bulk_eligible === true)`；②bulkDialog 入选过滤 `groups.filter(ImportReviewUpdates.eligible)`（:195 锚 `function bulkDialog(kind)`，chosen 与 total 两处同表达式）→ 同判。**必须保留 `!g.legacy` 显式排除**：legacy 组来自另一 feed（`prior.result.all.map(g=>({...g,legacy:true}))`），其载荷无 bulk_eligible 字段，`=== true` 天然排掉 undefined，但语义上要写明不靠隐式。
- 退役 `build/tests/test_r10_u2_bulk_identity.py`（铁律 2 的申报例外；其钉 1 就是已删除镜像的跨语言对照矩阵，文件头已写明退役条件与接替钉：后端矩阵钉 test_2_1_5_g5_merge_rules.py＋validate_import_review.py 载荷一致钉）。修订 `build/tests/import_review_updates.test.cjs`：删 eligible 镜像钉（锚 `test('bulk requires matching nonempty DOI`），补一条「unified_import_workspace 的入选判定只认 `bulk_eligible===true` 且排除 legacy」的纯函数/源码断言钉。
- 背景（为何必修）：镜像与后端 G5 判据已实证分叉——journal_same_generic 形态前端误判可批量，用户确认后整批 409 回滚；journal_two_diff、sharp_s 形态前端误判不可批量（少列）。

### L-33 批量对话框说明文案与范围一致（低危已核实）
- `unified_import_workspace.js` bulkDialog 说明句锚 `n('p',kind==='candidate?'Only loaded candidates with matching DOI values and no identity conflict are included.':…`（:195）——只描述旧判据 a，但同框按判据 b/c 也列「无 DOI 强身份一致」组。改为如实描述三段判据（如 zh：「仅纳入后端判定可批量确认的已加载候选：身份 DOI 一致，或题名/第一作者/年份一致且无身份冲突；通用或过短标题除外。」），`import_review_refined_locale.js` 对应两行十槽同步改写（en 槽即代码里的键）。
- 可选加分：入选行内小字展示 `bulk_ineligible_reason`（禁用按钮 title 亦可），需补 §3 的 8 个原因键；不做不算缺口。

### L-34 默认保留者采纳后端 suggested_keeper（低危已核实）
- candidateReader 的 keeper 默认链锚 `let keeper=keepers.get(W.scope()+':'+key)||item.keeper_key||item.keeper_record_id||activeMembers[0]?.ref_id||members[0]?.ref_id||members[0]?.zotero_key`（:177）与 bulkDialog 行内 keeper 锚 `const keeper=keepers.get(W.scope()+':'+id(item))||item.members.find(m=>m.ref_type==='active')?.ref_id||item.members[0]?.ref_id`（:195）：在「用户已选（keepers 缓存）/已定 keeper」之后、`activeMembers[0]` 兜底**之前**插入 `||item.suggested_keeper?.keeper_key||item.suggested_keeper?.keeper_record_id`（载荷字段，前端此前 0 处读取，grep 已证）。确认接口仍要求显式 keeper、后端照常校验——只改默认值，不改提交语义。reasons 文案渲染见 §3。

### L-35 语言切换陈旧 batch 守卫（低危已核实）
- 锚 `document.addEventListener('reviewflow:language-changed',()=>{if(listView&&listBatches){…renderList()…return;}if(batch){…render()…}})`（:210）：列表请求失败时 `listBatches=null`，守卫漏掉该分支落入 `if(batch)` → 用上一次批次的陈旧数据渲染复核详情。修法：`listView` 为真时不得落入 batch 详情分支——`listBatches` 在则 renderList()，为 null（失败态）则安全 no-op 或仅重渲染当前失败态视图；补一条 node 钉（vm 驱动该 handler，断言 listBatches=null 时不调用详情 render）。

### L-14～L-20 pdf_fetch.js 七连修（低危均已核实；messageFor 锚 `function messageFor(error)` :139-158）
- **L-14 透传 detail**：未映射 code/状态不再一律「出现未知错误」——兜底改为优先 `error.message`（errOf 已把结构化 payload 渲染/本地化进 message；纯网络层异常才落通用句）。同时补齐后端已有但前端未映射的 code：`pdf_fetch_storage`（503，新句「后端服务暂不可用，请稍后重试。」）、`pdf_fetch_base_url_blocked`（400，新句「自托管端点被安全策略拒绝，请检查配置。」）；`ApiError(0)`（网络断开）显式映射离线句（新键「无法连接服务器，请检查网络。」）。已映射键不动。
- **L-15 doi_missing 桶**：批量「跳过明细」reasons 分解锚 `if(reasons.already_has_pdf)…if(reasons.invalid_key)…`（:405-420 一带）补 `doi_missing` 桶（新键「缺少有效 DOI {0}」），消除「跳过 3·明细只有 2」。
- **L-16 403 错位文案**：锚 `if (error && error.status === 403) return T('只有任务负责人可以修改该设置。');`（:156）——fetch/fetch-batch/metadata-suggestions 端点无 owner 门，403 只来自非成员校验。改为：无 code 的 403 显示通用权限句（新键「你没有执行该操作的权限。」）。注意后端事实（已核实）：PUT settings 的 owner 403 是**纯字符串无 code**（pdf_fetch_api.py:1124-1127 `"Only the task owner can manage PDF fetch settings."`），fetch 类成员 403 亦无 code——两语境只能按**调用面**分案（settings 保存表单沿用 owner 句、获取类动作用通用句；messageFor 可带场景参数或由调用方传语境），节点测试钉两种 403 各得其所。改后端补 code 超出本批范围，勿做。
- **L-17 打开 PDF 弹窗拦截反馈**：锚 `async function openPdfBlob(articleKey)`（:164-183，window.open 成功断 opener / 失败落 anchor 兜底）。两路皆可能被弹窗拦截且零反馈：anchor 兜底后仍无法确认打开时（或统一地：两路尝试后）以 `toast()` 给出新键「浏览器拦截了弹出窗口，请允许本站弹出窗口后重试。」；外层调用点照 `safe()` 语义不外溢异常。
- **L-18 获取全文按钮 in-flight 防抖**：锚 `const fetchButton = btn('获取全文', () => fetchOne(status));`（:279）——对照同文件 save（:337/:357 disabled=true/finally=false）与 batchButton（:376/:430）模式，fetchOne 期间禁用按钮（fetchOne 签名可加按钮引用或包一层），杜绝连点并发 POST 与确认框堆叠。
- **L-19 删死词条**：`pdf_fetch.js:66` 注册行「开放获取链接的域名无法解析，请稍后重试。」全文仅此一处出现（grep 实测 1 命中=注册本身），删除该十槽行（后端 DNS 失败折成 502 `pdf_fetch_upstream`，结构上无法区分，保留只会误导维护者）。
- **L-20 extra 拆桶**：锚 `const extra = (summary.invalid||0)+(summary.blocked||0)+(summary.unresolvable||0)+(summary.trace_conflict||0)`（:396-397）——四类语义不同（invalid=内容无效、blocked=SSRF 安全拒绝、unresolvable=DNS 失败、trace_conflict=幂等键冲突），不得混进一句「另有 {0} 条内容无效」。拆为逐桶短句（新键见 §3；安全拒绝被误读为文件损坏是本条病根）。零值桶不显示。

### L-21/L-22 report_evidence.js 两清（低危均已核实）
- **L-21 关闭按钮 aria-label 翻译**：锚 `close.setAttribute('aria-label','关闭');`（dialog() 工厂内，:56）。该 setAttribute 绕过本文件 `n()` 的 attrs 翻译分支（`k==='aria-label'&&ReportEvidenceLocale.keys.has(v)?T(v):v`，n 定义 :5 可证）。「关闭」已是注册键（report_evidence_locale.js:86 十语言全有）——改为经翻译路径取值（T('关闭') 或走 n() attrs 分支）；对已打开对话框在 language-changed 处理器（:131，现只重译 `[data-mp-text]`）同步刷新打开中 mp-dialog 的该 aria-label。
- **L-22 失败对话框清理**：锚 `async function quantChoices(){const m=dialog('样本与结果选择'),data=await api(taskApi()+'/analysis/effects');…}`（:98）——dialog() 工厂 append 即入 body（:56），await 抛错则永不 show/close，每次失败累积一个不可见空 dialog。修法任选其一并钉测试：把 dialog 创建挪到 await 成功之后；或 try/catch/finally 中出错即 `m.d.remove()`。

### L-38 analysis.rob.* 55 键×10 语种词条（低危已核实；潜伏项——v2 前端尚未接入，先备词条）
- 55 个 message_key 后缀（=RobContractError code 全集，本批从 coscreen/risk_of_bias_contract.py + coscreen/rob_storage.py grep 去重实测恰 55）：`ambiguous_legacy_scope ambiguous_study_unit consensus_assessors_not_distinct consensus_judgement_required consensus_mapping_mismatch consensus_record_required consensus_result_alignment_mismatch consensus_scope_mismatch consensus_source_id_mismatch consensus_source_not_independent consensus_source_owner_mismatch consensus_source_revision_conflict consensus_source_revision_stale consensus_source_set_immutable consensus_sources_required consensus_validation_required duplicate_consensus_source duplicate_effect_version duplicate_record_revision duplicate_result_link host_fields_forbidden immutable_mapping immutable_record_identity invalid_assessment invalid_consensus_source invalid_domain invalid_domain_status invalid_domains invalid_field invalid_json_value invalid_judgement invalid_overall invalid_record_kind invalid_result_link invalid_result_reference invalid_revision invalid_source invalid_statistical_unit mixed_result_strata record_not_found result_link_required result_link_study_mismatch result_reference_missing result_scope_mismatch result_selection_required result_version_missing reviewer_forbidden revision_conflict revision_required run_link_mismatch selector_required source_unlinked unsupported_framework unsupported_mapping unsupported_not_applicable`
- 新建 `custom_frontend/analysis_rob_locale.js`（register 模式，55 行十槽；zh 为规范源串，语义照后端 raise 处英文消息与 details 键意译；带 params 的码如 `invalid_field`(field)/`invalid_domain`(domain)/`unsupported_framework`(framework) 源串用 `{0}…` 占位），挂 index.html script 标签（带 `?v=20261112-r67`）。
- `SERVER_MESSAGE_SOURCES`（index.html:5202-5204）补 55 条 `'analysis.rob.<code>': '<zh 源串>'` 映射；**同步小改两处接线**（向后兼容）：`RFServerMessage(key, params)` 增可选参数并 `T(source, ...)` 填充占位，errOf 调用点把 `payload.params`（dict）按 `Object.values` 序传入；`team.screening_not_enabled` 无参键行为不变。409 `revision_conflict` 与 403 `reviewer_forbidden` 语义在源串中体现（「已被他人修改，请刷新后重试」/「该筛选员无权操作此评估」）。
- 验收以 node 钉为主（词条命中+占位填充+十槽一致）；浏览器走查可选（v2 UI 未接入无用户可达路径——这是潜伏项，如实申报即可，勿为走查私接 UI）。

### L-44 GRADE 推导依据 syncFromState 恢复（低危已核实）
- 病灶：ⓘ tooltip 与推导依据框只在 runAutoSuggest 内填充（锚 :375-392 `for(const f of DOWNGRADE_FACTORS){…info.title=text;info.hidden=…}`＋`ui.rationaleBox.replaceChildren(…)`），`syncFromState`（锚 :236-242）只回填下拉/徽标/覆盖标记，不触碰 factorInfo/rationaleBox——renderRows 重建行（保存后、analysis-loaded/core-loaded 事件、切语言 refreshLang）后推导依据从界面消失，需重点「自动建议」。
- 修法：把 :375-392 的填充逻辑抽成可复用函数（如 `renderRationale(ui, st)`），runAutoSuggest 与 syncFromState（或 renderRows 行装配末尾）都调用，从 `st.signals`/`st.synthesis` 重建。数据面已核实：`st.signals` 持久化于保存载荷 `signal_sources` 并在 restoreAssessments 恢复（:436/:542）；**`st.synthesis` 不进保存载荷、仅本会话内存**（save body :425-437 无此字段）——恢复时机无「合成口径」一行属预期，缺省隐藏该行即可，**不得伪造合成数据**。

## 3. 词典（新键 ×10 语种；改既有句=同步改写对应十槽）
至少新增：后端服务暂不可用/自托管端点被安全策略拒绝/无法连接服务器请检查网络/你没有执行该操作的权限/浏览器拦截了弹出窗口/缺少有效 DOI {0}/安全拒绝 {0}/域名无法解析 {0}/幂等冲突 {0}/内容无效 {0}（L-20 拆桶句式可合并为一组带标签短句）/L-33 新说明句（含 metadata 分支句改写）/suggested_keeper reasons 7 键（has_doi 等＋active_member）/analysis.rob.* 55 键。可选：bulk_ineligible_reason 8 键。删除：pdf_fetch.js:66 死词条整行。全部十槽非空、占位跨槽一致；`build/validate_evidence_locale_static.cjs`（`node build/validate_evidence_locale_static.cjs`）必须 exit 0——report_evidence.js 在其 MODULES 扫描面内，L-21 改动后勿引入未注册 CJK 字面量。

## 4. 测试与验收
- 修订/新建（全部 node --test）：`import_review_updates.test.cjs`（M-13：镜像钉删、字段读取钉）；`pdf_fetch_ui.test.cjs`（L-14～L-20：未映射透传、403 双语境、storage/blocked/离线映射、doi_missing 桶、extra 拆桶、防抖连点仅一发、死词条删除后十槽门仍绿）；新建 `report_evidence_ui.test.cjs`（L-21 aria-label 经 T＋切语言刷新、L-22 失败即移除不残留）与 GRADE/导入复核各一钉文件（L-44 renderRationale 纯函数级行集；M-13/L-33/L-34/L-35 的 bulk 入选判定、keeper 默认链、language-changed 守卫——vm 手法照 pdf_fetch_ui.test.cjs 先例）。新文件全部加进 `.github/workflows/release.yml` 测试清单（现清单 :131-136）。
- `node --test build/tests/*.test.cjs` 全绿零回归。基线口径：本批撰写时点在 main@2226cad 实测 **82 pass / 0 fail**；你的实际基线=你的基分支（含 aiux 前端波新增测试）顶端实跑数，报告里写你自己跑出的两组数。
- 真实后端走查（你的树自带后端）：起服务→导入→复核界面（M-13：批量按钮可用性与后端判据一致，journal 样式 DOI 组不再误列；L-34：默认保留者=后端建议；L-33：文案与实际列出的组一致；L-35：列表失败态切语言不串台）→pdf-fetch 面板（L-14/16/18/20 文案与防抖；批量跳过明细含 DOI 缺失桶）→切语言抽查三模块（L-21 关闭按钮读音、L-44 推导依据仍在、L-33 文案）→GRADE：自动建议→保存→触发重渲染（保存后/切语言）推导依据不消失。RoB 词条以 node 钉验收（见 L-38）。截图放你树内 `out/`。
- 回报格式：提交哈希/文件清单/node 两组数/走查结论/偏差申报（任何与本 prompt 不符处如实列出，勿自行改后端）。
