# AI 配置 / B 泳道交付

目标树：E:/rf215-authfe，feat/2.1.5-ui-polish，基线 2230523。本代理只在 scratch 编写，主代理安装。未改后端、未提交。

## 文件

- custom_frontend/ai_providers_catalog.js
- custom_frontend/ai_providers_locale.js
- custom_frontend/ai_suggest.js
- build/tests/ai_providers_wizard.test.cjs
- build/tests/ai_settings_drafts.test.cjs（新增，主代理需纳入 release.yml）

## B 泳道逐项处置

| ID | 处置 | 实现与证据 |
|---|---|---|
| B-DYN-01 | 已修 | 选择厂商立即记录意图；取消保留厂商；格式按钮使用当前意图；唯一可用格式直接应用。多格式尚未确认时阻止提交旧厂商/协议组合。Node + TypeSafe/OpenAI 真实浏览器。 |
| B-DYN-02 | 已修 | 仅仍等于旧目录默认值的地址/模型自动更新；手填值保留；自定义协议界面明确要求手填地址与模型。Node + 真实浏览器。 |
| B-DYN-03 | 已修 | 设置页重建前捕获两卡所有表单和向导状态，同一账号/任务隔离的页面内存草稿；删除仅丢弃目标槽；初始加载空表单不覆盖服务端元数据。导入入口由 import 代理实现，联调通过保存/关闭重开保留其他槽。Node + 两入口真实浏览器。 |
| B-DYN-04 | 已修 | 按钮明确“测试已保存配置”；说明未保存输入不参与；成功回执标明已保存配置。新树真实后端调用本地模拟模型，未保存坏地址仍成功且来源说明正确。 |
| B-DYN-05 | 已修，跨模块联调 | 本代理所有旧中文“钥匙”改 API Key，品牌名保留；同一词条十语种行保持完整。导入入口旧 locale 残留已交 import 代理同步。见 terminology.md。 |
| B-DYN-06 | 已修 | 两个设置页决策槽说明仅用于导入复核的同判定；抽取和合并字段用通用模型。地址/模型字段改为中立的“API 接口地址 / 模型 ID”。Node + 真实浏览器 TypeSafe。 |
| B-DYN-07 | 已修 | 目录失败提供可手填说明和重试；运行模式失败明确桌面专属厂商暂不可选；保留安全降级，不把未知运行模式当桌面。Node 目录失败重试、模式失败通过。 |
| B-DYN-08 | 已修 | 格式显示中文/本地语名 + 技术枚举，如“聊天补全 (openai_chat)”“决策接口 (systemone)”。十语种完整，Node locale 断言。 |
| B-DYN-09 | 已修 | 下拉显示品牌，目录厂商内部 ID 输入隐藏/只读；自定义显示“自定义服务商 ID”；已有 OpenAI 等品牌写法按规范化 ID/品牌重新识别。真实浏览器隐藏 TypeSafe ID；Node 协议保留。 |
| B-DYN-10 | 已修 | Methods 未生成文本时复制按钮禁用；重建保留已生成文本；任务切换后旧响应不污染新任务。Node 空内容禁用通过。 |
| B-DYN-11 | 已修 | 建议按账号/任务/文献/展示模式识别当前阅读面，重绘时保留每个字段 details.open；不把另一文献展开态带入。Node 忽略另一项仍保留收起状态。 |
| B-DYN-12 | 已修 | 共享 snapshotWizards/restoreWizards API 保留厂商意图、已应用默认、待选协议和打开态；语言重绘先恢复输入再恢复向导。Node + 中英往返真实浏览器。 |
| B-DYN-13 | 已修 | 手填服务商仅进入自定义模式，不重置已选协议。Node Anthropic 协议保留。 |
| B-DYN-14 | 前端已降级；真实槽元数据属于后端边界 | 新树共享 GET 只有 enabled/provider_name，总览不能推导槽。显示“共享槽详细状态暂不可用”，未知槽操作命名“清除此槽配置”，确认明确只影响此槽，回执“清除请求已完成”，不声称空槽已删除。真实每槽回显、空槽禁删、用量回显不能在当前契约实现；未伪造。 |
| B-DYN-20 | 已修 | 负责人/成员共用本人字段映射控件；持久化仍按 task+user；成员加载本人维度；未映射建议提示复制粘贴或设置本人映射。Node 成员已有映射回填且不被重绘清除。 |
| B-DYN-21 | 已修 | 编码生成按钮旁提供“取消等待”，AbortController 传入真实 api；中断客户端等待释放写请求占用，并说明服务器可能继续处理；任务切换中断旧等待，旧 finally 不解除新请求状态。Node 取消信号与状态释放通过。 |
| F-DYN-08 | 等价覆盖 | 设置卡跨页面返回使用同作用域内存草稿，真实 import→home 往返保留。 |
| F-DYN-10 / A-DYN-21 的本文件部分 | 等价覆盖 | ai_suggest 唯一原生 confirm 改 RFConfirm；真实浏览器确认框与删除流程通过。 |

B 报告实际有 01–14、20、21 共 16 项；没有 B-DYN-15–19，未虚构缺失项目。

## 验证边界

- Node：两套共 16/16 通过（向导 9，设置/状态 7）；每套直接运行生产源码，未复制被测算法。
- 浏览器：browser_acceptance.py 使用 18931 新树真实 desktop 后端、自注册合成账号、合成任务/导入记录；第三方模型替换为本机 HTTP 模拟服务，未调用真实 AI，也无真实 API Key。
- browser_results.json 当前记录 11 场景、0 uncaught errors；settings-drafts.png、import-key-drafts.png 为目检证据。B14 真实共享槽与 390px 视口扩展也已通过；mobile-key-settings.png 为移动布局证据。
- B11/B20/B21 目前为源码执行 Node 验证，尚未伪称跑完多人浏览器协作或真实慢模型取消剧本。
- 草稿仅页面内存，跨刷新/关闭浏览器不保留 API Key；切换账号清空；未写 browser storage。非秘密字段映射继续沿用既有按账号/任务 localStorage。

## GitNexus

改前 upstream 分析：detect/renderSuggestions/renderBenchTools 为 MEDIUM，分别 6/9/6 直接依赖；renderKeyCard/renderTaskCard/renderOwnerBlock/renderPrefsBlock/resetForScopeChange/snapshotInputs/restoreInputs/renderAuditBlock/generate/buildItemCard/renderMemberBlock 为 LOW。索引未收录 ai_providers_catalog 的 mount/load/applyChoice/appendMeta/testButton 和 renderSlot，报告 UNKNOWN，源码核两入口调用点；未把同名 report_evidence.mount 的风险当成本模块结果。没有 HIGH/CRITICAL。提交/比较检测由主代理在真实工作树统一完成。

## 共享辅助 API

`snapshotWizards(host)` / `restoreWizards(host,snapshots)` 使用 mount 的 `draftName` 匹配；调用方先恢复普通输入，再恢复向导。`getFormat()` 在切换厂商而多格式未确认时抛出已本地化提示，应在保存 try 内调用。API Key 内容不进入辅助快照，只由两个入口自己的临时内存输入快照管理。
