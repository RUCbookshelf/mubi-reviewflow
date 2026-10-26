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
- 前端无 Node 环境（`node --version` 为 command not found，`node_modules/playwright` 不存在），浏览器套件 15 项（含 `min_labeled 不在提交体`断言）本轮未运行；`workflow_draft_guard_browser.cjs` 的 seed 定位器已在 `760ae2f` 更正为 `max="2147483647"`，待 Node+Playwright+独立实例环境补跑并记录。
- 前端改动经逐行 diff 自查与词条静态核验：`report_evidence.js` 全部含 CJK 字面量均已注册十语言词条（含 9 个 change() 轨迹 label，静态守护测试豁免清单已清空）；唯一不进入检查范围的是数据分隔符「；」（纯标点、不含表意字符）。

## 终审修复（集成树第二轮）

- 契约④（high）：存量迁移不再把同批多主体事件合并为单主体事件——批次内逐行改写为各自主体的增量、不删除任何行，事件条数、唯一主体计数、修改主体计数与每日活动圆点不因迁移变化（新增回归测试钉住迁移前后 summary 计数一致）；`affected_subjects` 合并载荷与 `fold_to_one` 删除路径随之移除。
- G2（high）：评价对话框（appraise）暂存只保存当前 tool|source 条目（旧的全量映射暂存格式不再恢复），恢复只作用于当前条目，保存基线也只比较该条目——其他报告的评价不再被本地旧暂存回写/删除。
- G10（medium）：「新增发现」打开定性证据草稿时忽略并清空 `extract|…|new` 暂存槽，不再恢复可能已落库的旧内容/旧 id（r3 收窄：清空仅在「新增发现」按钮路径 `old===null` 时执行；工作菜单入口 `existingId===undefined` 不再被误清空，F08 的「重新打开同一对话框恢复未保存草稿」在该路径恢复生效）。
- G11（medium）：`index.html` 对 `research_trace.js`/`report_evidence_locale.js`/`report_evidence.js` 的引用上戳 `?v=2.1.4`（launcher 对非 HTML 静态资源下发 max-age=86400，旧戳会让升级用户最长 24h 继续跑旧 JS）；`WORKFLOW_CONTROLS.md` 的「所有改动脚本统一 20261012-workflow6」失实句已改为按区分戳如实表述。（历史记录：该上戳为第二轮动作；第三轮 r3 内容变更已再度上戳至 `?v=2.1.4-r3`，见下文 r3 小节。）
- G19/G20（low）：关系管理「解除」在版本冲突 409 自动刷新后仍重绘列表；「新增评估」改用固定 `confidence|new` 暂存槽，未保存的新建评估草稿不再因一次性 uuid 槽位成为不可达孤儿。
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
- r3 验证（含评审反馈迭代轮）：工作树 `E:/rf214-r3-frontend` 实跑 `C:/Users/16073/.codex/preview-data/reviewflow-2-1-1-venv/Scripts/python.exe -m pytest build/tests -q` → 60 passed（新增 `BackendTraceLabelCouplingTests` 1 项）；PATH 无全局 Node（`node --version` 仍 command not found），使用本机 Hermes Node 二进制（`C:/Users/16073/AppData/Local/hermes/node/node.exe`）对改动 JS 做 `--check` 语法校验，并以 vm 沙箱加载真实 locale 字典跑功能 harness：轨迹载荷渲染断言全过（英/日/韩界面、增量/摘要/截断/跳过标记、纯编码值 `{value:'可信'}` 原样不译、脱敏 locator 对象出本地化占位而非 `[object Object]`、`确认报告关系`/`解除报告关系` 等后端 label 出译文、appraisals `items` 键映射、英文摘要对已注册 label 零残留中文）；`extract()` 暂存 4 项断言全过（工作菜单恢复/新增发现清空/清空后不恢复/重暂存可恢复）。Playwright 浏览器套件仍未运行（同 G21 现状）。

## Deferred（本轮未做、按终审要求逐项声明）

- 高2（分析错误翻译与「查看原始信息」展开）：未实现。分析页错误文案翻译与原始错误展开 UI 属新增界面功能，本机无 Node/浏览器环境无法验证，且分析模块（analysis_*.js）本分支未改动。
- 高4（导入核验「待启用」/状态徽标/批次名/计数即时刷新）：未实现。涉及导入核验页新界面状态与交互，需浏览器验证，`unified_import_workspace.js` 本分支未改动。
- 高5（loading 动画静默合并）：未实现。`import_review_loading.js` 本分支未改动，动画合并且需浏览器验证。
- 高7（导入遮罩取消与已用时长）：未实现。同上，属导入流程交互改动，无浏览器环境验证。
- 高1（401 后返回原位）：部分交付。现有机制已具备——`index.html` 的 `api()` 对 401 弹登录过期 toast 并调 `logout()`（不改动 `location.hash`），重新登录后按 URL 片段解析返回原页面（`index.html` 登录成功路由）；终审所指 UX 规格的其余要求（规格原文不在仓库内）未验证、未实现。
- G21（前端行为零浏览器覆盖）：维持现状。本机无 Node（node: command not found）与 Playwright，本轮新增的前端改动（G2/G10/G19/G20）仍仅经静态守护测试与逐行自查覆盖。
- G14（一次性 fetchall 内存/锁峰值上限实测）：未做。需要大夹具实测环境；迁移先物化后折叠的设计（bounded Python memory 注释口径）未变。
