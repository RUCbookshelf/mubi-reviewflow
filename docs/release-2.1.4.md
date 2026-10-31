# 2.1.4 release scope and validation

本版为 2.1.3 证据工作区的复核修复版：合并丢失的前端泳道、收敛存量轨迹残留，并补齐双模型评审确认的中/低危缺口。

## Fixed（前端证据工作区，恢复并续修 F06–F10 泳道）

- 恢复从 `fix/2.1.4-frontend` 丢失的三个提交（草稿 sessionStorage 暂存、切任务确认关闭、409 冲突自动刷新、对话框 scope 冻结、36 条十语言词条：28 条补齐既有界面文案、6 条为新增界面文案、2 条随切任务确认对话框新增）。
- 撤销栈耗尽等状态性禁用不再被点击收尾逻辑重新启用（`mpLock` 并入 `lockState`）。
- `undo()/adjudicate()` 与保存同级：`saving` 互斥并使在途旧载入响应失效，旧快照不再覆盖撤销/裁决后的状态。
- 共享裁决解除请求携带关系对 `(a, b)`：2.1.3 已落库的同成员同 id 重复关系可从界面唯一定位解除，不再困于后端歧义 409；裁决阶段随页面阶段（screen/fulltext/coding）记录，阶段收窄后共享裁决与撤销均留轨迹。
- 超限截断留痕不新增操作名：超出主体数上限的保存以既有 `report_evidence_changed` 动作记录，截断信息（如 `truncated_subjects`）写入事件载荷，轨迹页筛选与「报告关联与证据变更」条目保持既有形态。
- 删除首页词典中零引用的「训练所需最少纳入或排除决定数」十语言死词条。

## Fixed（后端证据 API）

- 2MB 请求守卫按 `Content-Length` 头在依赖阶段拒绝明显超限的请求。如实说明：FastAPI 在路由依赖执行前可能已读入（并解析）请求体，该预检不构成读取前的拦截，只是缩短超限请求继续被处理的窗口（回归测试钉住的是 413 契约，而非「未读体」机制）。
- 幂等去重日志按成员保留最近 200 条操作，表不再随保存次数无限增长。
- 确认关系前复核提案人当前仍具编码提交权；被撤销角色成员的遗留提案不能经确认进入共享库（409 明示）。
- 他人提案脱敏增强：locator 这类低熵文本（如 "p.4"）不再保留可枚举比对的 sha256，只留长度；evidence 仍保留哈希与长度用于一致性比对。

## 存量轨迹（2.1.3 数据）迁移与保留策略

- 旧版（2.1.3，本功能首个版本；2.1.2 及更早没有报告关联与证据工作区）每次保存给每个受影响主体写一条轨迹事件，单侧序列化不足约 850KB 时内嵌整份工作区快照（达到约 850KB 时该侧已是 `workspace_sha256` 摘要），快照累积后导出会永久 413（32MB 上限），共享裁决轨迹中还留存其他成员的依据/位置原文。自本版起，打开证据工作区或导出轨迹时自动执行一次性迁移（幂等、带完成标记；迁移前对涉及的数据库文件创建 `.pre-fold-v1.bak` 备份，已存在则跳过不覆盖，备份失败则本次不折叠、下次连接重试）：2.1.3 全量快照事件按保存批次改写为按报告增量（含导入的 imported_report_evidence_changed 行；单条或该成员累计超预算时降级为 `workspace_sha256` 摘要与 links/findings 计数），非本人提案文本按上述规则脱敏，畸形载荷跳过并留标记事件。事件条数如实口径：同一保存批次的多主体事件逐行保留，每行改写为该报告主体的增量（不合并为单主体事件、不删除行——事件条数、唯一主体计数、修改主体计数与每日活动圆点不因迁移变化，仅技术视图 JSON 载荷由全量快照变为增量/摘要）；单主体保存链每批各一条、条数不变（90 次保存量级实测折叠后仍为 90 条、仅载荷压缩，导出恢复可用）；导入事件同样逐行改写、不合并不删除，origin 行与 origin_* 列不动（旧导出包重导入保持 duplicate 语义），before/after 载荷与自有事件一样逐行改写为增量/摘要。更大的存量轨迹由本版的导出包分卷与导出/请求体积上限上调承接；分卷包仅 2.1.4 及以上版本可导入，旧版本导出的单文件包继续可导入。公开导出默认不含他人私有文本（关系依据/位置与证据草稿文本在导出侧兜底脱敏；界面显式勾选「包含私有备注/包含来源引句」时按选择保留对应原文，其余字段仍兜底脱敏）。
- 迁移不缩短记录时段：事件条数、唯一主体计数与每日活动圆点均不因迁移变化，每个保存批次的每一行都保留（仅载荷压缩）。如仍需缩减轨迹（长期任务接近导出上限），可在「轨迹」页清除个人历史；清除只删除轨迹事件并重建基线，不影响已保存的关系、证据草稿或任何筛选/编码数据。详见 `REPORT_LINKS_AND_EVIDENCE.zh-CN.md` 第 7 节。

## Validation record

- `python -m pytest build/tests -q`：本 docs 泳道树（46b22d6 基线＋文档改动）36 项通过（含新增回归：超限请求体 413 拒绝、去重日志限留、撤销提案人角色后确认 409、存量全量快照迁移后导出 200/脱敏/幂等、locator 哈希不外带）。36 为泳道基线计数：后端泳道分支（`fix/2.1.4-r2-backend`，含分卷、origin/草稿导出脱敏等）最新提交 c26e259 自述全量 53 项通过（评审实跑同为 53 passed），最终 2.1.4 集成树须以合流后实跑数为准，不沿用上述任一计数。
- 终审修复轮（集成树 `fix/2.1.4`）实跑：`C:/Users/16073/.codex/preview-data/reviewflow-2-1-1-venv/Scripts/python.exe -m pytest build/tests -q` → 59 passed（含新增：共享裁决 stage 与共享撤销 previous_stage 回归、多主体批次逐行保留与 summary 计数一致回归；`test_trace_static_guards` 豁免清空后 4 项通过）。
- 前端无 Node 环境（`node --version` 为 command not found，`node_modules/playwright` 不存在），浏览器套件 15 项（含 `min_labeled 不在提交体`断言）本轮未运行；`workflow_draft_guard_browser.cjs` 的 seed 定位器已在 `760ae2f` 更正为 `max="2147483647"`。（终局集成轮更新：上述「未运行」只是当时的泳道环境状态，不是遗留缺口——后续轮次已具备 Node+Playwright 并在隔离实例补跑闭环，workflow guard 15/15、ux_high 9/9、report_evidence 6/6 全部实跑通过，见「终局集成轮」小节。）
- 前端改动经逐行 diff 自查与词条静态核验：`report_evidence.js` 全部含 CJK 字面量均已注册十语言词条（含 9 个 change() 轨迹 label，静态守护测试豁免清单已清空）；唯一不进入检查范围的是数据分隔符「；」（纯标点、不含表意字符）。

## 终审修复（集成树第二轮）

- 契约④（high）：存量迁移不再把同批多主体事件合并为单主体事件——批次内逐行改写为各自主体的增量、不删除任何行，事件条数、唯一主体计数、修改主体计数与每日活动圆点不因迁移变化（新增回归测试钉住迁移前后 summary 计数一致）；`affected_subjects` 合并载荷与 `fold_to_one` 删除路径随之移除。
- G2（high）：评价对话框（appraise）暂存只保存当前 tool|source 条目（旧的全量映射暂存格式不再恢复），恢复只作用于当前条目，保存基线也只比较该条目——其他报告的评价不再被本地旧暂存回写/删除。
- G10（medium）：「新增发现」打开定性证据草稿时忽略并清空 `extract|…|new` 暂存槽，不再恢复可能已落库的旧内容/旧 id（r3 收窄：清空仅在「新增发现」按钮路径 `old===null` 时执行；工作菜单入口 `existingId===undefined` 不再被误清空，F08 的「重新打开同一对话框恢复未保存草稿」在该路径恢复生效）。
- G11（medium）：`index.html` 对 `research_trace.js`/`report_evidence_locale.js`/`report_evidence.js` 的引用上戳 `?v=2.1.4`（launcher 对非 HTML 静态资源下发 max-age=86400，旧戳会让升级用户最长 24h 继续跑旧 JS）；`WORKFLOW_CONTROLS.md` 的「所有改动脚本统一 20261012-workflow6」失实句已改为按区分戳如实表述。（历史记录：该上戳为第二轮动作；第三轮 r3 内容变更已再度上戳至 `?v=2.1.4-r3`，见下文 r3 小节。）
- G19/G20（low）：关系管理「解除」在版本冲突 409 自动刷新后仍重绘列表；「新增评估」改用固定 `confidence|new` 暂存槽，未保存的新建评估草稿不再因一次性 uuid 槽位成为不可达孤儿。（终局修复轮更正：该共用槽未按方法隔离——切换综合方法后其他方法的置信度对话框会在同一槽上 write/drop，把原方法的新建草稿清除，终局评审在 18777 隔离实例实测「切主题综合再切回 JBI 后草稿丢失、ConQual 重开为空」；现已升级为按方法固定分槽 `confidence|<method>|new`，仍保留 G20「槽名不含一次性 uuid」的固定槽语义，见「终局修复轮」小节。）
- G22–G25/G28（low）：词条修正——韩语「결말」→「결과」（结局/结果，两处）、法语 outcomes「critères」→「critères de jugement」（两处）、日语「可信性」→「信頼性」（r3 按术语表定稿：`report_evidence_locale.js` 的「JBI 发现可信度」「可信度（Credibility）」两处旧词条与「沿用当前产品…」行一并统一为「信頼性」，与本文件「可靠性（Dependability）」行及 `interface_locale.js` 全应用用词一致）、德语「dient nur Ansicht/Kopie」→「dient nur der Ansicht und dem Kopieren」；「评估草稿已保存」（置信度）与「评价草稿已保存」（方法学评价）八种译文逐字相同的问题已按语义区分。
- G26（low）：9 个 change() 轨迹 label 注册十语言词条（+9 行），静态守护豁免清单清空。
- G31（low）：`REPORT_LINKS_AND_EVIDENCE.zh-CN.md` 与本文档「旧版每次保存把整份工作区快照写入轨迹」改为如实口径：2.1.3 为本功能首个版本（2.1.2 及更早无该文件），单侧序列化 ≥850KB 时该侧已是 workspace_sha256 摘要（两侧独立判定）。
- G17（测试缺口）：新增共享裁决 `/relations` 携带 stage 与共享撤销 `/relations/undo` 复用 previous_stage 的回归测试（阶段收窄 screen 时裁决与撤销事件均留痕且阶段一致）。

## 前端收尾（第三轮 r3，复核确认四缺口）

- 日语术语统一（复核缺口 1）：`report_evidence_locale.js` 三处日语列统一为「信頼性」——「JBI 发现可信度」（`JBI 知見の信頼性`）、「可信度（Credibility）」（`信頼性（Credibility）`）、「沿用当前产品…」行（原「確信性」改「信頼性」），与全应用术语表一致；上文 G22–G25/G28 行已同步改写。
- 轨迹页报告关联载荷中文化（复核缺口 2；按评审反馈迭代）：`research_trace.js` 新增 `report_evidence_changed` 载荷渲染——label 判定用**显式 13 项集合**（9 个前端 change() label + 4 个后端裁决/撤销 label：「确认报告关系」「解除报告关系」「撤销报告关系裁决」「撤销报告关联与证据变更」，均已注册十语言）经 `T()` 本地化；纯 `{value:存储值}` 编码载荷（`coscreen/coding.py` 的 coding 事件）不在此列、原样输出，不会因撞上界面词条（如「可信」）被误译；`changes.links/drafts` 增量条目、映射字段（综述路径/综合方法/置信度/appraisals 的 items 等）与摘要/截断/跳过标记键名按映射表输出译文，枚举取值（关系类型/状态、JBI 可信度、综述路径、综合方法、重复对结论）走已注册词条；他成员提案在存储侧脱敏后的 locator 对象（`{redacted,length}`）渲染本地化占位「他人私有内容（已脱敏）」而非 `[object Object]`。上述 13 个 label 与已映射键名在非中文界面出译文；未注册的 label/深层未知数据键按数据原样（英文折叠标记事件即此类），`note` 等英文附注仍仅见于技术视图。新增静态守护 `BackendTraceLabelCouplingTests`：后端 `trace()` 中文 label 集合精确钉住且必须已注册、并出现在前端 `RE_LABELS` 集合。
- 字段类型词条补注册（复核缺口 3）：「填空/单选/多选/选项」4 个词条注册十语言（`research_trace_locale.js`），并连同「报告关联/证据草稿/重复证据对/工作区校验和/受影响主体/已截断主体/跳过事件/评价条目/他人私有内容（已脱敏）」（+13 行合计）从 `test_trace_static_guards.py` 的 `RESEARCH_TRACE_EXEMPT` 豁免清单移除，清单只剩语言自称名「日本語/简体中文」；4 个后端裁决/撤销 label 词条注册于 `report_evidence_locale.js`（紧随 9 个 change label）。
- 「新增发现」清空范围收窄（复核缺口 4，本轮回归修复）：`report_evidence.js` 的 G10 清空条件由 `if(old)` 收窄为 `old===null`（仅「新增发现」按钮路径）；工作菜单入口 `extract(key)`（`existingId===undefined`、无已存草稿时 `old===undefined`）不再误清空 `extract|…|new` 暂存槽，F08 的「重新打开同一对话框恢复未保存草稿」在工作菜单路径恢复生效。
- 缓存戳：四个改动脚本 `research_trace.js`/`research_trace_locale.js`/`report_evidence.js`/`report_evidence_locale.js` 的 `index.html` 引用统一上戳 `?v=2.1.4-r3`（G11 交付规则：内容变更须同变更上戳）。
- r3 验证（含评审反馈迭代轮）：工作树 `E:/rf214-r3-frontend` 实跑 `C:/Users/16073/.codex/preview-data/reviewflow-2-1-1-venv/Scripts/python.exe -m pytest build/tests -q` → 60 passed（新增 `BackendTraceLabelCouplingTests` 1 项）；PATH 无全局 Node（`node --version` 仍 command not found），使用本机 Hermes Node 二进制（`C:/Users/16073/AppData/Local/hermes/node/node.exe`）对改动 JS 做 `--check` 语法校验，并以 vm 沙箱加载真实 locale 字典跑功能 harness：轨迹载荷渲染断言全过（英/日/韩界面、增量/摘要/截断/跳过标记、纯编码值 `{value:'可信'}` 原样不译、脱敏 locator 对象出本地化占位而非 `[object Object]`、`确认报告关系`/`解除报告关系` 等后端 label 出译文、appraisals `items` 键映射、英文摘要对已注册 label 零残留中文）；`extract()` 暂存 4 项断言全过（工作菜单恢复/新增发现清空/清空后不恢复/重暂存可恢复）。Playwright 浏览器套件在 r3 未运行（r4 已新增 report_evidence 浏览器套件并实跑通过，见下节）。

## 前端收尾（第四轮 r4，评审反馈：G2 残余收窄、G21 浏览器回归与缓存戳）

- G2 残余收窄：`report_evidence.js` 的 appraise 暂存（`trackDraft(m,'appraise',…)`）脏判定与写回调均收窄为当前 `tool|source` 条目（`entryDiff(tool+'|'+source)`、`drafts[tool+'|'+source]`），删除 `diffKeys()` 与首键回退挑选——多差异键时不再暂存非当前条目；`appraise` 打开时的单条恢复与保存路径（原 `:100-118` 已验收语义）不变。新增静态守护 `test_trace_static_guards.py` 的 `AppraiseDraftScopeGuardTests` 钉住该口径（脏判定/写回调=当前键，全文件禁 `keys[0]`/`diffKeys` 回流）。
- G21（report_evidence 前端行为浏览器回归）：新增 `build/tests/report_evidence_browser.cjs`，以 Playwright 直连隔离实例（独立数据目录 `E:/uxt-r4-frontend`、端口 18772 避开其他泳道、代码取自本工作树 `build.launcher`；`prep.py` 注册账号/建任务/种入 3 篇文献并复位证据工作区，可重复跑）实测 F06–F09/F20 共 6 项，全部断言用户可见结果（按钮 `disabled` 态、对话框 `.mp-error` 文案、`.toast` 文案、textarea 字段值、恢复提示）：F06 撤销按钮栈空禁用且点击其他按钮/重开不复活；F07 保存 PUT 人为延迟 900ms 期间个人撤销与共享裁决（确认关系）并发点击被 `saving` 互斥拒绝（提示「正在保存，请稍候」），延迟落库后撤销可完整恢复关系、撤销消费完栈后按钮保持禁用；F08 关联对话框编辑中途关闭→sessionStorage 暂存→重开恢复（含恢复提示与字段回填）；F09 Node 侧持 token 直接 PUT 推进 revision 造 409→界面呈现「已自动刷新为最新内容」→同一对话框重试成功且落库可见；F20 置信度草稿按方法分槽（JBI 草稿暂存后，综述路径切到主题综合，CERQual 表单为空且无恢复提示）。实跑 6/6 PASS、exit 0（含 prep 复位后的多轮重跑与上戳后的回归重跑），跑完停实例并确认 `/api/health` 为 000。（终局修复轮更正：本轮 F20 断言只覆盖「不串入」，未验证切回 JBI 后草稿保留——终局评审实测共用 `confidence|new` 槽导致切回后草稿丢失；F20 已升级为按方法固定分槽并补「切回 JBI 恢复」断言，见「终局修复轮」小节。）
- 缓存戳：r4 评审修复泳道（`E:/rf214-r4-frontend`）内唯一内容变更脚本 `report_evidence.js` 的 `index.html` 引用由 `?v=2.1.4-r3` 上戳 `?v=2.1.4-r4`（G11 交付规则：内容变更须同变更上戳；launcher 对非 HTML 静态资源 `Cache-Control: public, max-age=86400`，不上戳则缓存用户最长 24h 仍跑旧 JS，G2 残余修复对其不生效）；`research_trace.js`/`research_trace_locale.js`/`report_evidence_locale.js` 该泳道无内容变更，维持 `?v=2.1.4-r3`。（终局集成轮更正：本句的「本轮」仅指 r4 评审修复泳道；r4 阶段同期还有 UX 回归泳道改动 `unified_import_workspace.js` 等 11 个脚本——`8a72314`/`8b560d9`，随 `eac1928` 合入集成树——当时未随之分面积上戳，其戳位已在终局集成轮统一上戳 `?v=2.1.4-r4`，见「终局集成轮」小节。）
- r4 验证：工作树 `E:/rf214-r4-frontend` 实跑 `C:/Users/16073/.codex/preview-data/reviewflow-2-1-1-venv/Scripts/python.exe -m pytest build/tests -q` → 61 passed（新增 `AppraiseDraftScopeGuardTests` 1 项，既有锚点全过）；`node --check custom_frontend/report_evidence.js` 通过；上戳后重跑 `report_evidence_browser.cjs`（`REVIEWFLOW_PLAYWRIGHT=E:/rf214-playwright/node_modules/playwright`、`REVIEWFLOW_GUARD_SESSION=E:/uxt-r4-frontend/session.json`）→ 6/6 PASS、exit 0、无页面脚本错误，实例已停（health 000）。

## 终局集成轮（r5：缓存戳收口、VERSION 2.1.4、发布记录如实化）

- 缓存戳收口（G11 残余，终局意见逐条处理）：r4 阶段实际改动过内容的 11 个脚本此前仍沿用旧戳，升级用户因 launcher 对非 HTML 静态资源下发 `Cache-Control: public, max-age=86400` 会在最长 24h 内继续跑旧 JS（终局评审实测缓存旧 JS 时高4 的「已启用／已核验」徽标缺失、高5 的完成胶囊保持 44px 不折叠）。本轮统一上戳 `?v=2.1.4-r4`：`unified_import_workspace.js`、`import_review_loading.js`、`import_review_locale.js`、`import_export_locale.js`、`analysis_locale.js`、`analysis_navigation.js`、`analysis_dependent_synthesis.js`、`analysis_estimator_sensitivity.js`、`analysis_influence.js`、`analysis_sensitivity_view.js`、`analysis_tau_profile.js`（`index.html` 共 11 处引用，逐一核对与 `main...HEAD` 的前端改动清单一致）；`docs/WORKFLOW_CONTROLS.md` 的戳描述同步改写为如实口径，并纠正其中「workflow 样式表 20261012-workflow6」的失实句（实际为 `?v=2.1.3`）。`research_trace.js`/`research_trace_locale.js`/`report_evidence_locale.js` 无新内容变更，维持 `?v=2.1.4-r3`。
- VERSION 与版本一致性：根目录 `VERSION` 第 1 行由 `2.1.3` 升为 `2.1.4`。`/api/runtime`（`custom_backend/update_check.py` 的 `current_version()` 读根 `VERSION`）与轨迹事件 `app_version`（`coscreen/__init__.py` 的 `_app_version()` 同读根 `VERSION`）同一来源；本轮在隔离实例（端口 18777）实测 `GET /api/runtime` 返回 `{"version":"2.1.4"}`，且该任务轨迹事件的 `app_version` 全量为 `2.1.4`（16 条中含浏览器套件保存产生的 11 条 `report_evidence_changed`，其余为任务建立的 baseline 事件），两处一致。
- 高危 UX 五项的如实状态（原 Deferred 中的「未实现／本分支未改动／部分交付」声明已过时，全部移出 Deferred）：高1（401 后返回原位）已交付完整链路——401 统一登出前先弹十语言登录过期 toast、`logout()` 不清除 URL 片段、重新登录 `enterApp()` 按 `location.hash` 复原原页面（`test_ux_frontend_static_guards.py` 钉住三处接线），不再是「部分交付」；高2（分析错误翻译与「查看原始信息」展开）已实现——localized 标记经 CustomEvent detail 传递、已翻译错误在分析页错误条直读译文、原始信息由显式点击展开、分析页 inert 恢复不再绑定 `current()`（`test_ux_frontend_review_fixes.py` 以 Node vm 加载真实字典断言 fr/de 译文直读与旧路径包壳，终局评审浏览器复核确认）；高4（「待启用」三口径计数一致／状态徽标／批次名／计数即时刷新）与高7（导入遮罩取消与已用时长）已实现并由 `ux_high_browser.cjs` 在真实导入 API fixture 上浏览器实测（高4 含 hc/gc/legacy/渐进加载竞争 8 项，高7 挂起遮罩取消 1 项）；高5（loading 静默合并）已实现——complete 起始态压住转圈重放、完成胶囊 4 秒后折叠（`test_ux_frontend_review_fixes.py` 以 msedge headless 加载真实 CSS 实测折叠前 ≥44px、移动端 ≥22px、折叠后 0 且 display:none）。
- G21 配套自包含（终局意见建议项）：r4 泳道仓库外的 `prep.py`/`run_instance.py` 移入仓库并参数化——新增 `build/tests/report_evidence_instance.py`（隔离实例启动器）与 `build/tests/report_evidence_prep.py`（注册账号/建任务/种入 3 篇文献并复位证据工作区/写出 session.json，可重复跑），`report_evidence_browser.cjs` 头注同步改指仓库内脚本；F06 断言补强「点击其他按钮」路径——原断言只点关闭/重开，现增加点击页脚「查看轨迹」按钮跳页返回后重开，撤销按钮仍必须禁用。
- 本轮验证（集成树 `E:/rf214-integration`，全部实跑）：`C:/Users/16073/.codex/preview-data/reviewflow-2-1-1-venv/Scripts/python.exe -m pytest build/tests -q` → **102 passed**；隔离实例（`build/tests/report_evidence_instance.py`，数据目录 `E:/uxt-r5-integration`、端口 18777、Hermes Node `C:/Users/16073/AppData/Local/hermes/node/node.exe` ＋ `E:/rf214-playwright` 的 Playwright）上 `report_evidence_browser.cjs` → **6/6 PASS**（含补强后的 F06）、`ux_high_browser.cjs` → **9/9 PASS**、`workflow_draft_guard_browser.cjs` → **15/15 PASS**，三者均 exit 0、无页面脚本错误；实例在线时确认 served `index.html` 已带全部 11 个 `?v=2.1.4-r4` 新戳；跑完停实例并确认 `/api/health` 为 000。

## 终局修复轮（r6：F20 置信度草稿按方法固定分槽、高7 挂起闸门确定性化）

- F20（终局意见，实修而非改口径）：终局评审在隔离实例（端口 18777）实测证明原实现并未真正按方法分槽——`confidence()` 对「新建评估」所有方法共用 sessionStorage 槽 `confidence|new`（方法只写在 stash.key 里），JBI 草稿暂存后切主题综合打开/取消，CERQual 对话框虽不恢复 JBI 内容（不串入），但其 `trackDraft` 挂在同一共用槽上，关闭即把该槽 drop/write，切回 JBI 后 ConQual 重开为空且无恢复提示（本轮修复前先在 18779 隔离实例复现该丢失现场，探针输出 `switchback: note=0 field=""`）。修复：新建评估槽改为按方法固定命名 `confidence|<method>|new`（`report_evidence.js` `confidence()`），保留 G20「槽名不含一次性 uuid」的固定槽语义；编辑既有评估仍用 `confidence|<id>` 槽（id 自带方法前缀，天然按方法隔离）。分槽后各方法互不清空/覆盖，切回原方法重开即恢复。旧共用槽 `confidence|new` 在升级瞬间可能残留一条孤儿暂存（仅跨热升级且恰有未保存草稿的会话可见，随标签页关闭消失），不做迁移，与 r4 G2 旧格式暂存「自然淘汰」同口径。
- F20 浏览器断言补强：`build/tests/report_evidence_browser.cjs` 的 F20 用例在原「不串入」断言之外新增——①槽位机制钉子：JBI 新建草稿必须落在 `confidence|jbi|new` 且 `confidence|new` 必须为 null；主题综合未输入即取消不得写自己的槽、且 JBI 槽原样保留；②终局意见要求的「切回 JBI」路径：综述路径保存回 jbi 后重开 ConQual，必须出现恢复提示并回填草稿字段。方法切换封装为可复用 `setMethod()`（含 `#mp-method-settings` 被首页重渲染替换时的稳定等待与重试），F20 用例起手先显式保存 `qual+jbi` 保证前置确定。
- 高7（终局意见，测试确定性修复）：`ux_high_browser.cjs` 原只以 glob `**/import-batch` 拦截挂起请求，但单文件导入实际 POST `/import`（`index.html` `realImport()`：`files.length>1` 才走 `/import-batch`），且两端的 URL 都带 `?screener=` 查询串——Playwright glob 生成的正则以 `$` 锚定整条 URL，带查询串即不匹配，两条路由实际都拦不住，此前 PASS 依赖真实请求恰好未完成的竞态。修复：改用正则路由 `/\/import(-batch)?(\?.*)?$/` 同时钉住单文件与批量端点（含查询串），主请求在 `release()` 前确定性挂起；取消后浏览器中止 fetch，`route.fulfill` 对已中止路由可能拒绝，已用 `.catch` 吞掉避免未处理 Promise 拒绝。另以一次性探针实测闸门确定性：拦截下点击导入后保持 5 秒遮罩仍在、取消可用，随后取消恢复交互（遮罩只由真实请求完成或取消收起，挂起 5 秒不收起即证明请求被拦截而非竞态）。
- 缓存戳（G11 交付规则）：`report_evidence.js` 内容变更，`index.html` 引用由 `?v=2.1.4-r4` 上戳 `?v=2.1.4-r5`；`docs/WORKFLOW_CONTROLS.md` 的戳清单同步改写（r5 轮统一上戳的 11 个脚本不变，仍为 `?v=2.1.4-r4`）。实例在线时实测 served `index.html` 引用为 `report_evidence.js?v=2.1.4-r5`。
- 本轮验证（集成树 `E:/rf214-integration`，全部实跑）：`node --check` 对 `custom_frontend/report_evidence.js`、`build/tests/report_evidence_browser.cjs`、`build/tests/ux_high_browser.cjs` 全部通过；`C:/Users/16073/.codex/preview-data/reviewflow-2-1-1-venv/Scripts/python.exe -m pytest build/tests -q` → **102 passed**；隔离实例（`report_evidence_instance.py`，数据目录 `E:/uxt-r6-integration`、端口 18779）上 prep 复位后 `report_evidence_browser.cjs` → **6/6 PASS**（含切回恢复断言的 F20），prep 再复位后重跑再获 6/6 PASS、均 exit 0、无页面脚本错误；第二隔离实例（同脚本，数据目录 `E:/uxt-r6-ux`、端口 18775）上 `ux_high_browser.cjs` → **9/9 PASS**、exit 0、无页面脚本错误。两实例跑完已停并确认 `/api/health` 为 000。`workflow_draft_guard_browser.cjs` 本轮未运行（本轮改动不涉及工作流草稿守卫面积；r5 实跑记录为 15/15 PASS）。

## 空白格补齐（r8 · W2：PDF 自动获取，默认关闭的联网功能）

- 功能与默认值：新增任务级「PDF 自动获取」（`custom_backend/pdf_fetch_api.py`）：按文献 DOI 查询 Unpaywall 的开放获取版本，下载 PDF 后经既有的 `security.check_pdf_bytes` 校验与 `fulltext.save_pdf` 落盘，并写既有 `fulltext_added`/`fulltext_replaced` 轨迹事件。**默认关闭**：任务设置文件缺省 `enabled=false`，只有该值为真（`true`/`1`）才视为开启；未开启时三个端点一律 403 且**零出站**。全部出站只由用户点击「获取全文」/「批量获取」触发，无定时器、无后台任务。
- 隐私披露（本版新增联网出口，须如实告知）：开启后，应用会把**文献 DOI** 与负责人填写的**联系 email** 发送到 Unpaywall（`https://api.unpaywall.org`，其服务条款要求带 email），并可能向 Crossref（`https://api.crossref.org`）查询元数据建议；随后向 Unpaywall 返回的开放获取 PDF 链接发起下载。设置页常显「出站访问范围」两行（列出生效的两个端点），并声明只访问这两处端点与上述 PDF 链接。**不接受回环/内网目标**：PDF 链接若指向本机、私网、链路本地地址（含解析到这些地址的域名），一律拒绝并报 `pdf_fetch_unsafe_url`；官方 Unpaywall 端点强制 https 且必须填 email（对 `API.unpaywall.org`、`:443`、尾点等写法同样判定为官方，无法靠改写 base_url 绕过）。
- 自托管：两个端点地址均可配置并存于任务目录的 `pdf_fetch_settings.json`（指向自建代理或本机回环代理时不必填 email）。该配置只影响本任务的请求目标，不改变上述「默认关闭」与「显式触发」两条。
- 失败处理：上游不可达、超时、重定向、非 HTTP 响应、超限、坏 JSON 一律 502 `pdf_fetch_upstream`（带结构化 reason）；下载内容不是可解析 PDF 为 422；磁盘或数据库写入失败为 503。批量端点逐条返回结果、整体不返回 5xx，并在连续两次出站失败后中止剩余条目。**不静默覆盖**：该文献已有全文时单篇端点返回 409 并要求显式确认（`overwrite=1`），批量默认跳过已有全文。
- Crossref 元数据建议（缺 DOI/年份/摘要时）**只读**：仅返回建议值供复制，绝不写库；采纳仍走既有的文献编辑路径并留痕。
- 阶段门禁：任务只读、阶段暂停、文献不在当前来源范围时拒绝写入全文（沿用既有 `stage_paused` / `article_outside_scope` 等代码）。
- 前端：初筛卡「获取全文 / 元数据建议」按钮与设置页开关/批量入口，自带十语言词条、`textContent` 渲染；「打开 PDF」经带鉴权的 blob 请求打开（全站鉴权仅 Bearer 头，直接链接会 401）。
- 第三轮收紧（r3 复审）：① 非公网地址判据改为 `is_global` 白名单——旧判据漏掉 100.64.0.0/10（RFC 6598 CGNAT），阿里云 ECS 元数据面 `100.100.100.200` 会被当成可访问地址真实抓取；② 出站 opener 显式挂空 `ProxyHandler({})`——否则 `HTTP(S)_PROXY` 会把 DOI 与联系 email 整包发给代理，配置到回环的自托管端点也收不到请求；③ 官方 Unpaywall 身份判定不再看端口（`:80` / `:8443` 变体曾同时绕过 email 必填与 https 强制，现按 hostname 判官方，非 443 端口一律拒绝）；④ 批量/单篇的幂等 request_id 改为对文献键散列（旧写法拼原键，键长 ≥164 字符时超 200 上限导致合法文献永久失败）；⑤ 读库与工作流门禁纳入 503 兜底（SQLite 争用不再 500），批量逐条折成 `failed/storage`；⑥ 前端补三条回归钉子（覆盖确认、批量补充计数、鉴权 blob 打开），此前这三条修复在 CI 里退回也全绿；⑦「打开 PDF」不再双开标签页（`window.open(..., 'noopener')` 恒返回 null 会误入兜底分支）；⑧ 界面披露句与后端 `pdf_hosts_note` 同口径（含「本机与内网地址一律拒绝」）；⑨ 域名解析失败单列 `unresolvable`（502），不再误报成「指向内网」；⑩ 批量跳过按原因拆分（已有全文 / 键非法 / 工作流拦截 / 上游中止）；⑪ 成员读取设置时联系 email 掩码（`o***@team.test`），仅负责人可见完整值；⑫ 出站兜底不再吞 `AssertionError`（测试桩的「未声明端点」断言不会被降级成 502）。
- 验证（本泳道工作树 `E:/rf214-r8-features` 实跑）：`python -m pytest build/tests/test_pdf_fetch_api.py -q` → 69 passed / 35 subtests（含 r3 新增 9 项）；`node --test build/tests/pdf_fetch_ui.test.cjs` → 11 passed；CI 同款合并命令 `node --test build/tests/{import_review_model,import_review_updates,ml_batch,study_design_badge,pdf_fetch_ui}.test.cjs` → 38 passed（本版已把 `pdf_fetch_ui.test.cjs` 接入 `release.yml`）。破坏即红：后端 5 个变异（is_global 判据、空 ProxyHandler、官方端口判定、幂等键散列、存储兜底）与前端 3 个变异（补充计数、覆盖确认、blob 打开）全部使对应钉子变红（副本上做，目标树未改）。门禁实跑：`python -m pytest build/tests -q` → 958 passed, 4 skipped, 220 subtests passed。

## Deferred（按终审要求逐项声明；终局轮后仅余下列各项）

- 高1/高2/高4/高5/高7 与 G21：原「未实现／部分交付／零浏览器覆盖」的声明均已过时并移出本节——五项高危 UX 修复已交付并经机器与浏览器验证，report_evidence 前端行为已有隔离实例浏览器回归，见「终局集成轮」小节。
- G14（一次性 fetchall 内存／锁峰值上限实测）：**risk-accept（未闭合，不计入已闭合债务）**。现状如实声明：`custom_backend/report_evidence_api.py:355` 的存量迁移扫描仍 `fetchall()` 全量装载（先物化再折叠，内存约为载荷的 2–3 倍），`:468` 起折叠段仍整段持有 `BEGIN IMMEDIATE` 写锁，未实现分批；`build/tests/test_report_evidence.py:446` 起的 90 条 × 约 0.4MB 夹具是正确性回归（终局评审单跑该用例约 3.41s），不含内存峰值、锁时长与并发写测项，本版也未补做这些测量。接受为 low risk 的依据：迁移幂等且带完成标记（每个库至多执行一次，完成后 connect 不再取写锁）；锁窗口为一次性且 `db._connect` 的 5s busy timeout 会让并发写方显式报错而非静默损坏；桌面单机场景下该窗口内并发写概率低。若后续服务器多成员部署或轨迹存量显著增大，须先实现分批/限流并补测内存峰值、锁时长与并发写，再重新评估；该项在最终问题总表按 risk-accept 记录，不作为已闭合项计入。
- appraise 暂存已知口径（终局意见建议项，如实声明、不改行为）：r4 已把评价对话框暂存收窄为仅当前 `tool|source` 条目（G2，`AppraiseDraftScopeGuardTests` 钉住），因此「在 R1 条目编辑后切到 R2 再关闭」会丢弃 R1 的未保存草稿——脏判定与写回调只看当前条目。若要保留切走的条目，需在切换时按 `tool|source` 分槽写入，属行为变更，本轮未做，维持 r4 已验收口径。
