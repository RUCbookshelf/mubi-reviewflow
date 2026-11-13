# UI 优化波：120 项逐条处置与验证范围

本表按原动态报告顺序保留 120 个主条目，逐行对应原问题、最终改动和实际验证范围。120 行表示已完成逐项处置记录，不表示 120 项均独立做过真实浏览器复跑。

依据：[获批范围](E:/mubi-reviewflow/out/release_plan/frontend_ui_optimization_wave_scope.md)；[原始动态报告](E:/mubi-reviewflow/out/release_plan/frontend_formal_verification_2026-11-12/frontend_formal_verification_report.md)。本波工作树为 `E:/rf215-authfe`，分支 `feat/2.1.5-ui-polish`，起点 `2230523caf296b01af0c465805ddf3ff49a1afd2`。

| 主泳道 | A | B | C | D | E | F | 合计 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 条目 | 22 | 16 | 19 | 19 | 26 | 18 | 120 |

原报告严重度：high 10、medium 68、low 42。A-DYN-16/F-DYN-17、A-DYN-21/F-DYN-10、B-DYN-03/C-DYN-04、B-DYN-05/C-DYN-08 是四组合并条目，别名不另计行。

验证栏的“真实”指对本波新树后端与合成数据执行的实际浏览器操作。受控断网/503/404等故障注入在相应条目说明；关系 E-DYN-31 的 409 来自真实后端版本竞争。Node 是生产模块的自动化行为或结构回归，不等于完整端到端测试；源码/词条检查不等于运行实测。

| ID（合并别名） | 原严重度 | 原动态问题 | 处置 | 最终改动 | 验证强度与实际边界 | 证据 |
|---|---|---|---|---|---|---|
| A-DYN-01 | medium | 中文概览连续展示两张英文的新建任务卡，两个按钮均为「Create a task」。 | 已实现 | 零任务概览合为一卡；修正八行中文列中的英文占位。 | Node词条/源码；无专属浏览器断言。 | [W][W] |
| A-DYN-02 | low | 同一个页面显示两次「首页与设置」。 | 已实现 | 隐藏重复首页标题，保留面包屑。 | 源码检查；专属视觉验收未记录。 | [W][W] |
| A-DYN-03 | medium | 标签返回后已填内容仍在，阅读位置和当前输入焦点丢失。 | 已实现 | 设置标签按标签记忆阅读区滚动。 | 真实浏览器：切回保持300→300。 | [W-follow][W-follow] |
| A-DYN-04 | medium | 桌面流程给出含「用户名」占位符的 Python 命令，页面上没有取码工具入口或可直接使用的步骤。 | 已实现 | 找回密码按平台给可复制命令；launcher增加reset-user入口，macOS/Linux打包纳入恢复模块。 | Node调用真实Python隔离恢复一次消费；真实Windows登录页复制恢复命令。macOS/Linux打包清单源码检查，未运行其成品安装器。 | [N-recovery][N-recovery]、[R-core][R-core] |
| A-DYN-05 | medium | 导航展开后要等整个分批动效结束，已经显示的项目才可操作。 | 已实现 | 导航展开总动画调整为300ms。 | 样式/语法检查；未记录最终设备上的耗时实测。 | [W][W] |
| A-DYN-06 | medium | 请求在途时按钮仍显示「＋ 新建任务」且可点击。 | 已实现 | 新建任务在途锁按钮并显示正在创建，结束后恢复。 | 当前树源码检查；本波未记录专属慢响应浏览器断言。 | [ROOT][ROOT] |
| A-DYN-07 | medium | 当前任务的流程设置保存和重新载入控件显示在六个标签的底部，包括外观与存储。 | 已实现 | 工作流保存条仅在任务/编码设置显示。 | 真实浏览器：外观标签保存条隐藏。 | [W][W] |
| A-DYN-08 | low | 桌面单用户下显示一个始终禁用的下拉，仅含当前账号。 | 已实现 | 桌面单账号显示只读身份文本，隐藏恒禁用下拉。 | 当前树源码检查。 | [ROOT][ROOT] |
| A-DYN-09 | low | 当前任务卡可继续初筛或编码，缺少继续全文复筛入口。 | 已实现 | 当前任务卡补全文复筛入口。 | 源码检查。 | [W][W] |
| A-DYN-10 | medium | 空闲停留时，概览当前任务卡仍被销毁并重建。 | 已实现 | 设置签名不变时保留来源选择、当前任务卡及焦点。 | 真实浏览器：刷新后原来源节点/焦点保持；Node相关回归。 | [W-live][W-live] |
| A-DYN-11 | medium | 会话恢复进行时，页面仍显示可操作的登录表单。 | 已实现 | 恢复会话期间登录表单inert并标明正在恢复登录。 | 源码检查；最终登录恢复回归见懒加载剧本，未单独证明慢响应全时窗。 | [I-lazy][I-lazy] |
| A-DYN-12 | medium | 来源名呈现为输入框，能够聚焦但无法编辑。 | 已实现 | 来源名称呈明确只读文本，来源类型仍可修改。 | 源码检查。 | [W][W] |
| A-DYN-13 | medium | 重命名、归档、恢复均返回成功，当前任务卡仍显示旧标题或旧操作。 | 已实现 | 任务列表收到新值后重绘任务卡，重命名/归档/恢复成功后显示新状态；相同列表不重建。 | 当前树loadTasks/renderHub源码检查；不将新建按钮busy覆盖冒认此项。 | [ROOT][ROOT] |
| A-DYN-14 | medium | 任务列表加载中登录屏已收起，主界面显示功能卡和「未选择任务」，任务区没有加载指示。 | 已实现 | 登入后任务下拉显示载入状态并禁用；失败保留重试入口。 | 源码检查；主代理最终慢任务列表剧本待补。 | [ROOT][ROOT] |
| A-DYN-15 | medium | 保存工作流设置在途没有忙碌状态，成功后没有状态提示。 | 已实现 | 工作流保存增加互斥、控件禁用与保存中/成功/失败状态。 | 源码与相关回归；专属慢响应浏览器门禁待主代理。 | [W][W] |
| A-DYN-16（F-DYN-17） | medium | 登录页在解析期间加载 136 个同步外部 JS，包含导入与分析模块。<br>登录首载需要约 2 MB HTML 和 136 个同步外部脚本。 | 已实现 | 认证前仅载入登录必需模块；认证后按原顺序激活123个工作区脚本，失败可重试且不重复执行已成功模块。 | 真实冷启动17脚本/123延迟模块，登录重试、深链、存储令牌恢复；Node4例；Service Worker被阻止以统一计数。 | [I-lazy][I-lazy] |
| A-DYN-17 | low | 功能介绍卡看起来像功能入口，点击仍留在概览。 | 已实现 | 功能介绍卡增加可点击/键盘操作和对应功能导航。 | 当前树源码检查。 | [ROOT][ROOT] |
| A-DYN-18 | low | 新建任务空态会把焦点移到名称输入框，输入后按 Enter 不提交。 | 已实现 | 新建任务输入支持Enter，并尊重输入法及模态状态。 | 源码检查；输入确认组件Enter/取消语义有Node回归。 | [N-core][N-core] |
| A-DYN-19 | low | 一次过期触发两个不同措辞的过期 toast，工作流入口还显示第三个通用失败提示。 | 已实现 | 会话过期由统一处理器公告，抑制调用方重复过期toast。 | 源码检查；并发401终跑结果待主代理回填。 | [ROOT][ROOT] |
| A-DYN-20 | low | 在另一个已停留登录表单的标签取消「记住我」后，已保存会话仍让新标签自动登录。 | 语义澄清并修复可见性 | 记住我旁明示选项在下次登录生效；说明移出隐藏找回密码字段组，登录时可见并关联aria-describedby。 | 新增Node布局回归先红后绿；真实login→reset→register→login：仅登录可见、关联正确、JS错误0。会话所有权语义保持。 | [B-A20][B-A20]、[N-core][N-core] |
| A-DYN-21（F-DYN-10） | low | 重命名弹出浏览器原生 prompt。<br>任务重命名使用 Chromium 原生输入弹窗，未保存更改使用应用内弹窗。 | 已实现 | 任务重命名改应用内可输入RFConfirm；Enter提交输入，取消按钮保留取消语义并归还焦点。 | Node；真实浏览器重命名取消与Enter分项通过。原表合并F-DYN-10。 | [N-core][N-core]、[R-core][R-core] |
| A-DYN-22 | low | 创建与使用日期直接显示为 ISO 字符串。 | 已实现 | 创建/最近使用日期按当前界面语言格式化。 | 当前树Intl.DateTimeFormat源码检查。 | [ROOT][ROOT] |
| B-DYN-01 | medium | 取消格式选择会丢掉刚选的厂商，再从格式按钮确认会进入自定义，地址和模型未填。 | 已实现 | 取消格式选择保留厂商意图；重开按当前意图选择，未确认多协议时阻止旧组合提交。 | Node+真实浏览器取消/重开及唯一协议。 | [B-live][B-live] |
| B-DYN-02 | medium | 换协议会覆盖手填地址和模型，切到自定义还会留下原厂商的值，允许协议和地址错配。 | 已实现 | 只更新仍等于旧目录默认的地址/模型，手填值保留；自定义明确手填。 | Node+真实浏览器编辑值保留。 | [B-live][B-live] |
| B-DYN-03（C-DYN-04） | high | 保存或删除个人槽时，其他槽和任务 AI 面板的部分未保存输入被静默清空。<br>保存个人 LLM 卡清空另一张个人决策卡的未保存服务商与 Key | 已实现 | 两配置入口保留未提交槽、任务表单和向导状态；草稿仅账号/任务作用域页面内存，删除只清目标槽。 | Node+真实浏览器两入口保存/关闭重开；原表合并C-DYN-04。 | [B-live][B-live]、[I-live][I-live] |
| B-DYN-04 | medium | 输入未保存的新地址后，「测试连接」仍验证已保存的旧配置，返回成功且未说明来源。 | 已实现 | 明确测试的是已保存配置，未保存输入不参与，成功回执说明来源。 | 真实新树后端连接本机模拟模型；未使用真实第三方AI或真实Key。 | [B-live][B-live] |
| B-DYN-05（C-DYN-08） | low | 标题和按钮写「钥匙」，字段写「API Key」。<br>同张卡使用「通用 LLM 钥匙」「API Key」及「保存钥匙」等不同术语 | 已实现 | 两个入口统一API Key术语并保留品牌名。 | 十语言词条检查+真实界面；原表合并C-DYN-08。 | [B][B]、[I][I] |
| B-DYN-06 | low | 个人和任务共享决策槽均缺用途说明，仍给出 OpenAI 兼容地址和 gpt-4o-mini 示例。 | 已实现 | 说明决策槽仅用于导入同判定；字段采用中性API地址/模型ID标签。 | Node+真实浏览器TypeSafe决策配置。 | [B-live][B-live] |
| B-DYN-07 | low | 目录 503 时只剩禁用的自定义占位和手填字段，无目录失败说明；运行模式接口失败时桌面版的 Ollama 也被禁用。 | 已实现 | 目录失败显示手填/重试；运行模式未知时明确桌面专属厂商暂不可选。 | Node目录失败、重试及模式失败；无专属真实慢网剧本。 | [B][B] |
| B-DYN-08 | low | 格式按钮和选项显示 `openai_chat`、`openai_responses`、`systemone` 等内部标识。 | 已实现 | 协议显示本地语名称及技术枚举。 | Node十语言检查。 | [B][B] |
| B-DYN-09 | low | 厂商显示名和自动填的服务商 ID 不一致。 | 已实现 | 已知厂商显示品牌并隐藏内部ID；自定义才提供ID输入。 | Node+真实浏览器隐藏TypeSafe内部ID。 | [B-live][B-live] |
| B-DYN-10 | low | Methods 尚未生成时，点「复制中文」会清空已有剪贴板并提示「已复制。」。 | 已实现 | Methods空内容时禁用复制；重建保留已生成文本，旧任务响应隔离。 | Node生产函数行为验证。 | [B][B] |
| B-DYN-11 | low | 对某字段的建议点「忽略」后，其他字段建议的手动展开状态也被重置。 | 已实现 | 建议展开态按账号/任务/文献/模式保存，忽略另一项不重置当前折叠。 | Node；未声称多人真实浏览器协作已跑。 | [B][B] |
| B-DYN-12 | low | 切换界面语言会关闭未完成的格式向导并丢掉刚选的厂商，已填写的模型草稿仍在。 | 已实现 | 语言重绘先恢复表单，再恢复厂商/格式意图与向导开闭。 | Node+真实中英往返。 | [B-live][B-live] |
| B-DYN-13 | medium | 仅将服务商拼写改为品牌写法，已选的原生协议仍被切成默认 OpenAI chat。 | 已实现 | 手改厂商进入自定义模式但保留所选协议。 | Node Anthropic协议保持。 | [B][B] |
| B-DYN-14 | medium | 只配置决策槽时，两槽都显示空表单和删除按钮。删空的通用槽仍提示「已删除」，已配置的决策槽仍保留。 | 前端缓解/后端边界 | 显示共享槽详细状态暂不可用；未知槽使用清除请求措辞，不把总览推断为每槽配置。 | 真实浏览器验证提示及清空通用槽不影响决策槽；每槽metadata/用量/空槽禁删需后端契约。 | [B-live][B-live] |
| B-DYN-20 | medium | 负责人可配置「样本量→Synthetic sample size」并点击「采纳」填入 48。受邀编码成员可用该共享模型并编辑自己的同名维度，设置页却没有字段映射控件。成员建议卡只有「复制」「编辑后采纳」「忽略」，旁边仍写「采纳将复制建议值」 | 已实现 | 普通成员也可编辑本人字段映射；保留账号/任务隔离，未映射建议给明确操作。 | Node；真实受邀成员协作端到端未记录。 | [B][B] |
| B-DYN-21 | medium | 生成未完成时缺少取消按钮，点击下一篇仍停在原文献，提示「请等待当前保存或上传完成。」完成后下一篇立即恢复正常。 | 已实现 | 编码生成提供取消等待，中断浏览器请求；说明服务端可能继续，旧请求收尾不干扰新请求。 | Node取消signal/状态释放；未实测真实慢第三方模型。 | [B][B] |
| C-DYN-01 | high | 生成期间关闭按钮禁用，Escape 无效，页面由模态框占用 | 已实现 | 生成模态显示项数/耗时，关闭/Escape/停止等待中断浏览器请求并保留选择。 | Node+真实浏览器/本机模拟模型；只证明取消等待，不宣称服务端停止计算。 | [I-live][I-live] |
| C-DYN-02 | medium | 高置信和一般队列徽章颜色相同。中置信同一文献只显示判定，灰色只显示降级 | 已实现 | 三色徽标并列判定、置信度和等级，颜色不单独承载含义。 | 真实浏览器浅深色桌面及390px移动端。 | [I-live][I-live] |
| C-DYN-03 | medium | 第 2 页没有勾选项时，仍可提交第 1 页看不见的题录 | 已实现 | 跨页选择显式列出，可逐条移除或清空；当前页范围明示。 | 真实25组/50条合成数据浏览器。 | [I-live][I-live] |
| C-DYN-05 | medium | 题名相同的记录在下拉中显示两项完全相同的标签，尽管期刊和 DOI 不同 | 已实现 | 保留记录选项并列标题、年份、DOI和稳定短ID。 | 源码检查；无专属同标题真实断言记录。 | [I][I] |
| C-DYN-06 | medium | 单条生成的 0.97 期刊和摘要建议未进入批量预览，已有同组高置信重复建议时也没有可展开摘要 | 按裁定保留并澄清 | 批量仅应用高置信度same建议；删除不可达冲突分支，说明字段合并须逐条确认。 | Node确认字段建议不能入批量；未增加conflict_picks。 | [I][I] |
| C-DYN-07 | low | 版本冲突后弹窗提示重试，原按钮却保留旧文案并禁用 | 已实现 | 设置409刷新版本后可重试；若目标已生效则更新并关闭；失权仍只读。 | Node生产函数行为验证；非真实后端并发剧本。 | [I][I] |
| C-DYN-09 | medium | 0.97 的「不同文献」建议在队列没有任何徽章，打开阅读区才看得见 | 已实现 | different建议显示中性徽标及置信度，不进入重复项批量。 | Node行为验证。 | [I][I] |
| C-DYN-10 | low | 弹窗只有标题和 CSV／JSON，实际下载请求只包含当前批次 | 已实现 | 审计导出注明当前导入批次及批次名。 | 源码检查。 | [I][I] |
| C-DYN-11 | low | 应用 102 条时两个实际请求分别提交 100 和 2 条，第二块开始时仍只显示「正在保存…」 | 已实现 | 应用前说明100项分块/最后成功块撤销范围；执行中显示项数及块进度，后块失败保留前块成功。 | Node102项产生100+2请求，版本链及受控409；非真实后端事务测试。 | [I][I] |
| C-DYN-12 | medium | 批量资格 409 错误显示原始键 import_merge.ineligible.doi_conflict | 已实现 | 资格原因使用实际存在的importMerge.ineligible词典前缀。 | 源码/词条检查及导入静态验证器。 | [I][I] |
| C-DYN-13 | low | 应用成功后同时出现两处撤销，刷新并重开批次后仅通用「撤销上一步」可用 | 已实现 | 移除重复AI撤销按钮，成功反馈指向权威通用撤销入口。 | 源码检查；复用后台批次状态恢复。 | [I][I] |
| C-DYN-14 | low | 单选一条元数据差异时双源开关不可用，说明只写厂商配置要求 | 已实现 | 说明双源交叉检查仅适用于重复组；纯元数据保持禁用。 | 源码检查。 | [I][I] |
| C-DYN-15 | low | 「AI 建议」与「AI 建议（本页）」打开同一组 20 条当前页题录，前者初始勾 0 条，后者初始勾 20 条 | 已实现 | 两个入口区分选择本页项目与预选本页全部项目。 | 源码/词条检查；不冒充全批次选择。 | [I][I] |
| C-DYN-16 | low | 点一条队列记录时整队列及桌面隐藏的移动选择框被重建，选择框生成 104 项，该次同步操作读取暂缓记录键 644 次 | 已实现 | 每轮渲染只读一次暂缓状态；签名相同复用队列按钮，桌面不生成隐藏移动选项。 | 真实浏览器：reads=1、节点同一、桌面mobileOptions=0；390px布局。 | [I-live][I-live] |
| C-DYN-17 | medium | 展开的记分卡和审计区随队列点击关闭。重开记分卡再次请求任务级数据，旁边导出走批次级且两处均未写清范围 | 已实现 | 记录切换保留记分卡/审计展开态，同版本复用数据，明确任务与批次范围。 | 源码检查；无专属真实端到端计时记录。 | [I][I] |
| C-DYN-18 | medium | 未启用且无 Key 与已启用但无 Key 两个状态的工具栏文字完全相同，均无生成按钮与原因说明 | 已实现 | 工具条说明AI禁用、无可用Key、当前页无合格待处理项。 | 源码检查。 | [I][I] |
| C-DYN-19 | medium | 个人 LLM 卡下点击保存，空 Key 错误与保存成功均在长表单底部，当前视口看不到反馈 | 已实现 | Key验证、保存、错误、成功反馈放在对应卡内，失败保留未提交输入。 | 真实浏览器保存卡反馈及草稿。 | [I-live][I-live] |
| C-DYN-20 | low | 仅有个人主源时仍能勾选双源验证，生成后才报告「第二个 AI 来源不可用」 | 前端缓解/后端边界 | 发送前说明重复组适用及独立第二来源要求，并提供配置入口；不从共享总览推断第二模型已就绪。 | 源码检查；现API无第二来源就绪预查，降级响应仍保留。 | [I][I] |
| C-DYN-21 | medium | 仅另会话暂停编码阶段、未修改导入记录，也会让下一次导入核验轮询重新读取全部批次页并重建队列与工具条 | DOM修复/网络边界 | 签名不变保留队列/工具条/阅读面及引导节点；全局版本改变仍读取分页数据。 | 源码+相关浏览器节点复用；后端无导入专用revision/digest，必要GET保留，未宣称消除全部轮询。 | [I][I] |
| D-01 | medium | 任务另一阶段策略变更后，下轮轮询重建当前初筛卡，长摘要回顶，原焦点落到 BODY。 | 已实现 | 活跃阶段策略签名不变时不重载阅读面，版本单独更新。 | 真实四轮自然轮询124秒及另一客户端暂停编码：卡片/摘要/焦点不变，滚动850。 | [W-live][W-live] |
| D-02 | low | 策略未变时，30 秒轮询仍替换常用理由的选项节点。 | 已实现 | 理由集合与下拉装饰按签名更新。 | 同124秒真实轮询+Node同库测试。 | [W-live][W-live] |
| D-03 | medium | 保存响应较慢时，按键后看不到进行中状态。 | 已实现 | 判定保存期间显示状态、禁用操作并防重复提交。 | 当前树screenDecide源码检查；本波未记录专属慢响应浏览器断言。 | [ROOT][ROOT] |
| D-04 | medium | 选过 RIS 后，需连点两个同名按钮才能进入导入核验，文件未随跳转进入导入队列。 | 已实现 | Living Review导入入口单次导航；移除无效文件选择器，另标分析上下文。 | 7项既有Living Review回归；入口源码检查，专属浏览器目标点击待补。 | [W][W] |
| D-05 | low | 默认中文下，理由草稿标题和清除按钮仍显示英文。 | 已实现 | 筛选理由草稿图例/按钮改为正确中文词条。 | 词条回归。 | [W][W] |
| D-06 | low | 下拉无可访问名称，且键盘 End 会选到「将此理由存为常用」动作并回到先前值。 | 已实现 | 理由下拉有可访问名称，另设保存常用理由按钮，End不触发写操作。 | 真实阅读截图+源码；专属End键写入断言未记录。 | [W][W] |
| D-07 | low | 下一篇在加载期间暂时没有题名和摘要。启用高亮时，高亮可能比正文晚出现。 | 已实现 | 下一篇加载时保留旧内容并锁控件，成功后整体替换，错误给持续重试。 | 当前树renderScreen源码检查；未记录专属慢卡加载浏览器断言。 | [ROOT][ROOT] |
| D-08 | medium | 启动抽验直接更换筛选卡并清掉未提交理由。重新打开同一任务后，抽验进度消失。 | 已实现 | 抽样前先检查草稿；样本状态按账号/任务/筛选员保存恢复。 | Node范围恢复与账号隔离；后端正式决定仍独立保存。 | [W][W] |
| D-09 | low | 英文界面的训练向导仍显示中文标签「备注」。 | 已实现 | 训练备注及暂不确定注册十语言别名。 | Node+真实英文训练界面Comments。 | [W-follow][W-follow] |
| D-10 | low | 1,500 条题录一次生成所有勾选行，等待时只留下旧步骤，界面无加载提示。 | 已实现 | 训练名单显示加载状态并按50项分页，选择跨页保存。 | Node1500项渲染50；真实60项任务加载与分页。 | [W-follow][W-follow] |
| D-11 | medium | 手选题录步骤同时显示样本数、随机种子和最后一步的阈值与备注。 | 已实现 | 训练步骤hidden优先于网格样式。 | 真实手选步骤：阈值/样本量字段网格可见数0。 | [W-follow][W-follow] |
| D-12 | low | 浏览器离线期间，同一服务连接告警约每 30 秒再弹一次。 | 已实现 | 断连合为一个可收起提示，退避最长5分钟，手动重试绕过延迟。 | Node断连恢复+真实中断/恢复及提示定位。 | [W-final][W-final] |
| D-13 | low | 开启筛选训练后，较窄的桌面窗口中，进度与工具控件混在一个状态行内换成两排。 | 已实现 | 窄屏筛选页将训练与辅助操作归组。 | 当前树布局源码检查；390px整体布局已记录，非所有桌面宽度矩阵。 | [ROOT][ROOT]、[W-final][W-final] |
| D-DYN-20 | high | 保存判定后，在旧卡退场动画期间切换任务，新任务卡片保留透明退场态，快捷键不能提交判定。 | 已实现 | 任务/导航切换清理退出态并作废旧回调；旧回调不清除新动画。 | Node过期/失败/新旧动画竞争；真实任务切换后可见且可继续操作。 | [N-core][N-core]、[R-core][R-core] |
| D-DYN-21 | medium | 手选 20 条后进入阈值步，「已手选 20 条」提示消失，样本量仍为 50，按钮允许提交，服务端返回 400 后才说明数量不一致 | 已实现 | 纯手选样本量取实际选择数，最终步骤前校验10–100并显示数量。 | Node+真实20项POST201，size=20。 | [W-follow][W-follow] |
| D-DYN-22 | medium | 强制门禁提示没有关闭按钮，Escape、关闭训练弹窗和导航往返均保留提示。另一会话通过训练后，本页正式初筛请求已成功，旧提示仍称需要完成训练 | 已实现 | 训练门槛提示可关闭，导航重查，成功决定后触发重查。 | 源码检查及主代理成功决定接线；未单列另一客户端通过后的完整实时剧本。 | [W][W] |
| D-DYN-23 | low | 说明键盘操作、排除理由和即时保存的整句默认收起。入口为没有可见文字的三点图标。 | 已实现 | 阅读帮助入口恢复可见标签。 | 真实Edge截图。 | [W][W] |
| D-DYN-24 | medium | 当前初筛卡没有直接的前后切换或跳过控件，内联已决历史只有序号。 | 已实现 | 初筛补不提交判定的前后浏览操作，历史项增加题名。 | 当前树源码检查；未记录专属前后浏览与历史标题的浏览器断言。 | [ROOT][ROOT] |
| D-DYN-25 | low | 同一任务的单篇和批量全文获取分处不同入口。 | 已实现 | 初筛批量获取入口导航至编码PDF获取设置。 | 真实按钮可见；目标路径源码检查，专属点击验收未记录。 | [W][W] |
| E-01 | high | 同任务返回保留输入，重载后只恢复上次保存的内容。编码证据干净时，评价评注不会触发离页保护，切任务再切回后静默丢失。 | 已实现 | 完整评价独立注册草稿守卫，并按任务/用户/筛选员/文献/工具暂存。 | 真实仅修改备注：离页守卫、留页、重载恢复、保存清理。 | [E-live][E-live] |
| E-02 | medium | 默认变体 22 题按 5 域全部展开。总进度在表顶，保存位于表尾。到最后一题看不到进度，也没有域跳转或下一未答入口。 | 已实现 | 评价表增加置顶进度/保存/下一未答及域跳转。 | 真实默认RoB2仍22题、5域，未改工具算法。 | [E-live][E-live] |
| E-03 | medium | 工具切换及保存后焦点落到 BODY。 | 已实现 | 评价工具/保存按钮按稳定焦点键恢复，弹窗关闭归还触发点。 | 真实键盘工具/置顶保存焦点检查。 | [E-live][E-live] |
| E-04 | medium | 编码中已保存完整 RoB 2，分析页旧偏倚风险列表仍显示 `[]`。旧表单提示不执行官方信号问题算法，页面没有到完整评价表的链接。 | 已实现 | 完整评价说明逐文献来源并直达分析RoB；legacy说明结果级人工判定独立存储，反向按钮只定位匹配来源报告。 | Node守卫/精确来源/缺失不替代；真实正反向点击，Stay保留草稿，R1准确定位，390px无横溢，JS错误0。 | [E][E]、[R-E04][R-E04] |
| E-05 | medium | GRADE 评估面板有「导出 SoF 表」和研究局限性输入，实际「GRADE 判定写回」位于导出向导的逐结局审计卡。GRADE 评估面板缺少指向写回入口的引导。 | 已实现 | GRADE指向SoF写回，反向按真实分析子路由返回，并遵从导航守卫。 | 真实已填SoF打开与GRADE返回。 | [E][E]、[R][R] |
| E-06 | medium | 旧提案确认返回 409，弹窗已更新到新报告对，却提示「请刷新后重新确认」。 | 已实现 | 共享提案409成功重读后提示列表已自动刷新，失败重读不误报成功。 | Node摘要/兼容/刷新路径回归；本波未单列真实共享409。 | [R][R] |
| E-07 | medium | 审计卡显示「采纳自动建议」，重开已保存判定时来源下拉显示「人工定稿」。原备注依然预填。 | 已实现 | 已采纳来源按adopt回填；切换已存来源须确认后写入。 | Node+真实：未确认仍adopted，确认后manual。 | [R-live][R-live] |
| E-08 | medium | 勾选结局后预览和审计卡立刻消失，状态栏为空。 | 已实现 | 设置变化后明确提示重新预览，不留无说明空白。 | 真实结局勾选后提示检查。 | [R-live][R-live] |
| E-09 | low | 尚无 RoB 评价时只显示表头，预填按钮缺席，通用提示仍要求确认后保存。 | 已实现 | 无关联评价时解释须完成文献评价及效应来源链接，不伪造可预填动作。 | 真实CASP空结果与说明。 | [E][E] |
| E-10 | medium | 主题聚类成功运行后，初筛仍展示「按模型预测的纳入概率」和「试试机器学习排序」引导。 | 已实现 | 聚类排序时隐藏机器学习纳入概率引导，异步设置加载/保存后同步已建提示；页码译名为补充项。 | 新增Node先红后绿；真实旧ML→cluster设置/排序完成：聚类按钮、文献载入、旧提示隐藏，JS错误0。 | [W][W]、[W-E10][W-E10] |
| E-11 | medium | 只改一项评注并保存时，两项编码维度的三张已存图片全部再次请求。三轮每轮三个 HTTP 200，均为 `private, no-store`，浏览器记录每张都有字节传输。 | 已实现 | 保存证据合并关联ID并复用未变图片/URL，只更新变化画廊。 | 真实仅改备注：0图片GET、原图片URL保持。 | [E-live][E-live] |
| E-12 | low | 切参考报告、切评价工具都会把展开的问题重置为仅第 1 题。切回原报告或原工具再展开第 6 题，原答案和依据仍可读取。 | 已实现 | 定性JBI/CASP保留题目展开序号。 | 真实第1/6题在参考报告及工具往返后仍展开。 | [R-qual][R-qual] |
| E-13 | medium | 已存 RoB 及 DTA 来源记录在相应披露区中以原始 JSON 显示，未在列表中提供逐条编辑或删除按钮。定性综合另有独立可读的记录分组。 | 已实现 | RoB与DTA提供可读判断/依据/来源记录，保留原始JSON；RoB支持载入编辑。 | Node字面文本与DTA显示；真实RoB。DTA编辑/删除未新增。 | [R][R] |
| E-14 | low | 中文界面把建议依据渲染为机器字段。两篇高风险评价时，GRADE 显示「规则 majority_high_risk · high_risk_studies=2 · n_studies=2」。SoF 快照同样展示 `i2_percent`、`ci_width_ratio` 等字段。 | 已实现 | GRADE与SoF将已知规则/计数/I²/CI/p转为可读标签，原始信号折叠保留。 | GRADE既有Node信号测试；真实SoF截图。展示数值4位有效数字，原始值未改。 | [E][E]、[R][R] |
| E-15 | high | 旧表单保存后，再打开同一研究、同一框架，原比较组、结局、时间点、总体判定、总体依据和五域的十个判定／依据控件为空。已存记录可从原始数据披露读取。 | 已实现 | 旧RoB完整回填；同范围修订，改范围显式确认新建或修改目标记录；草稿按账号任务隔离。 | Node+真实：同范围1条；改时间点取消1条、确认2条。 | [R-live][R-live] |
| E-16 | high | 点「保存本篇」后，同一 PDF 被再次下载，阅读从第 8 页、120% 回到第 1 页、104% 适宽。滚动从 9,451 px 回到 15 px。 | 已实现 | 证据保存不再重建PDF阅读器。 | 真实第8/9页、116%缩放、8646滚动及PDF对象保持，无PDF GET。 | [E-live][E-live] |
| E-17 | medium | 同一合成结局，前一入口按已保存 GRADE 评估显示「中」及研究者备注，另一入口仍显示自动建议「高」。两份预览列不同。 | 已实现 | 两份SoF明确分别使用分析已存GRADE与向导判定/当前建议，并互链。 | 真实来源说明及跨入口导航；未合并两套后端记录。 | [E][E]、[R-live][R-live] |
| E-18 | medium | 采纳后没有撤销或清空控制。审计卡仅有写回，写回弹窗仅有关闭、保存和取消，以及人工定稿／采纳两个来源。 | 前端缓解/后端边界 | 上一版值可回填为人工修订；明确当前不支持清空为未判定。 | Node无立即写入且为manual新版本；真实入口。真正撤销/删除/历史采纳快照恢复缺API。 | [R-live][R-live] |
| E-19 | medium | 手填备注保存后再开，输入框前部被混入自动生成的降级差异文本。 | 已实现 | 人工备注与系统覆盖差异分开；旧混合文本只读保留，避免猜测拆分。 | Node重复保存回读+真实后端重载。 | [E-live][E-live] |
| E-20 | medium | 更改降级和备注后重载，没有未保存提醒。降级和备注恢复成原保存值。 | 已实现 | GRADE行级草稿注册守卫并按完整结局范围暂存；只清本次成功提交版本。 | 真实重载恢复/保存清理；Node账号任务隔离及保存竞争。 | [E-live][E-live] |
| E-21 | medium | 中文标题和图例下的同一表头出现 `Outcomes`、`№ of participants (studies)`、`Risk of bias`、`Comments`，中间列为中文。 | 已实现 | GRADE SoF预览/CSV九列表头本地化，切语言重绘缓存表头。 | 真实中文九列检查。 | [E-live][E-live] |
| E-22 | medium | 漏填随机化域依据时，HTTP 400 原文 `domain rationale is required: randomization process` 直接作为通知显示。去掉域判定时，前端也显示英文句。 | 已实现/后端待结构化 | 必填范围/总体/域判断及依据在请求前校验并聚焦；映射已知后端英文错误。 | Node+真实中文缺域依据；后端code+params升级未做。 | [R-live][R-live] |
| E-DYN-11 | medium | 保存期间编辑 ICC 来源，返回 503 后结果区为空，按钮恢复可用，没有错误通知。 | 已实现 | 整群试验保存后继续编辑也保留失败通知，标明对应先前提交输入。 | 当前树源码检查；未记录专属受控503加保存后继续编辑的浏览器复测。 | [ROOT][ROOT] |
| E-DYN-32 | low | 空先验触发中文校验句，结果节点标记 `lang="en"`，页面语言为 `zh`。 | 已实现 | 贝叶斯本地化校验文本沿用当前界面语言属性。 | 当前树源码检查；无真实屏幕阅读器测试。 | [ROOT][ROOT] |
| E-DYN-30 | medium | 从个人编码切到共享提交证据后，两个开关的颜色、样式及辅助技术状态均未改变，页名、网址、说明与保存按钮也相同。编码值输入与提取面板消失，笔记仍可编辑 | 已实现 | 个人/共享证据按钮有选中样式和aria-pressed；保存区写明共享提交的私有证据范围。 | Node模式及缓存发现更新；真实受邀共享权限团队剧本未记录。 | [E][E] |
| E-DYN-31 | high | 个人线索保存遇到 409 后，界面提示已刷新到最新，但相关报告、类型、同样本勾选、依据和页码仍为旧值。再次保存会覆盖另一个窗口的新依据，或把已替换的旧报告对写回。 | 已实现 | 个人关系409并列本地/最新值，确认差异前锁写；稳定ID替换，跨关闭重开保持草稿与确认闸。 | Node4例；真实两标签页同字段/改报告对409，未确认不覆盖，确认后单关系、共享revision不变；桌面/手机。 | [R-conflict][R-conflict] |
| F-DYN-01 | medium | 切去其他面板再回初筛，同一篇长摘要回到顶部。 | 已实现 | 按账号/任务/筛选员/来源/顺序记忆阅读位置和摘要滚动。 | Node作用域隔离+真实阅读位置往返分项通过。 | [N-core][N-core]、[R-core][R-core] |
| F-DYN-02 | medium | 键盘可以判定，提交成功后焦点回到 BODY。 | 已实现 | 判定后新卡恢复原判定区焦点。 | 真实键盘下一卡分项通过；不代表完整辅助技术审计。 | [R-core][R-core] |
| F-DYN-03 | medium | 登录和初筛保存通知可见，通知及其容器均没有 live region 或 status 语义。 | 已实现 | 普通toast容器增加status/aria-live公告语义。 | 当前DOM源码检查；未使用屏幕阅读器实际朗读测试。 | [ROOT][ROOT] |
| F-DYN-04 | medium | 两个工作区都在翻到第 2 篇、离开再返回后回到第 1 篇。 | 已实现 | 复筛/编码以报告键保存队列位置，同来源重载优先当前阅读位置。 | Node两工作区来源隔离；真实复筛往返位置通过；编码位置未单列专属真实剧本。 | [N-core][N-core]、[R-core][R-core] |
| F-DYN-05 | medium | 分析显示方法目录和短时错误通知，启动引导隐藏，面板没有可见重试按钮。 | 已实现 | 分析加载失败保留持续状态与重试入口。 | 当前树源码检查；未记录专属分析断连浏览器剧本。 | [ROOT][ROOT] |
| F-DYN-06 | medium | 服务停止后打开初筛，点击「重试／刷新」后仍显示「正在载入文献队列…」。 | 已实现 | 初筛前置队列失败显示明确错误及重试，不停留在载入中。 | 当前树源码检查；未记录专属初筛前置队列断连浏览器剧本。 | [ROOT][ROOT] |
| F-DYN-07 | high | 可见的「撤销」按钮收不到鼠标点击，事件穿透至底层。 | 已实现 | toast内容接收pointer事件，外层透明区域仍穿透。 | Node样式断言+真实鼠标点击撤销分项通过。 | [N-core][N-core]、[R-core][R-core] |
| F-DYN-08 | high | 切离首页后返回时，个人通用模型的未保存输入静默清空。 | 已实现 | API Key设置跨面板返回保留同账号任务内存草稿。 | 真实import→home往返；不跨刷新保存秘密字段。 | [B-live][B-live] |
| F-DYN-09 | low | 弹窗初始焦点落到克隆的策略下拉。 | 已实现 | 草稿守卫先聚焦留在当前页，取消恢复调用点。 | 源码+模态相关Node；真实旧RoB草稿选择Stay保留内容，未单列所有入口焦点矩阵。 | [W][W]、[N-core][N-core]、[R-E04][R-E04] |
| F-DYN-11 | medium | 编码方案为空且从未编辑，离开时仍弹出「未保存的更改」。 | 已实现 | 空个人/共享编码方案载入后建立干净基线并清载入态。 | 新增Node个人/共享两模式：dirty=false、无可提交表单、返回仍干净；未单列空方案真实浏览器剧本。 | [N-evidence][N-evidence] |
| F-DYN-15 | medium | 入页读卡有多段前后依赖，同时存在并行请求。 | 已实现 | 工作流与两理由库并发读取，保留必要版本依赖。 | 源码与相关刷新回归；未给出优化后首卡耗时基准。 | [W][W] |
| F-DYN-16 | low | 导入与报告前台空闲时，每轮发出 7 项 GET，导入区节点每轮发生两批替换。 | DOM修复/网络边界 | 无变化的工作流/导入提示保留DOM；为其他客户端变化继续必要轮询。 | workflow真实自然轮询及import节点复用；不宣称所有7个GET已消失。 | [W-live][W-live]、[I][I] |
| F-DYN-12 | high | 焦点在弹窗的「留在此页」按钮时按 `1`，底层文献被实际纳入，弹窗仍打开。 | 已实现 | 原生/应用模态期间拦截初筛及复筛数字快捷键。 | Node两阶段1/3；真实模态无后台写入分项通过。 | [N-core][N-core]、[R-core][R-core] |
| F-DYN-13 | medium | 焦点在确认弹窗按钮时按 `Ctrl+Z`，底层筛选决定被撤销，确认弹窗仍打开。 | 已实现 | 全局撤销键尊重共享模态守卫。 | Node真实键盘回调；真实模态快捷键分项通过。 | [W][W]、[N-core][N-core]、[R-core][R-core] |
| F-DYN-14 | medium | 初筛空态有「导入文献」，复筛和编码空队列都缺少通往前置阶段的主操作。复筛还显示「该文献尚未上传 PDF」，编码显示「正在载入 PDF…」。 | 已实现 | 空复筛/编码队列给前置阶段入口并隐藏无文献PDF动作；空方案给配置入口。 | 源码及空schema Node；未运行空任务所有入口的完整浏览器矩阵。 | [E][E]、[N-evidence][N-evidence]、[ROOT][ROOT] |
| F-DYN-18 | medium | 点下一篇时单篇请求失败，页面仍显示上一条标题和 1／4 序号，纳入、排除及理由均禁用。错误 toast 消失后无本篇状态提示 | 已实现 | 复筛本篇失败在始终可见ftLoadError中持续显示并提供重试，成功后清除。 | 真实受控503：持续失败区域可见；恢复后重试成功。核心浏览器9/9全部通过。 | [R-core][R-core] |
| F-DYN-19 | low | 编码单篇的错误行留在中文，切换英文后，旁边重试按钮和状态已变成英文 | 已实现 | 保留错误对象/作用域并随语言切换重译已有行，不重复请求。 | 真实注入契约404：中文→英文且0重请求。 | [E-error][E-error] |
| F-DYN-20 | medium | 「帮助→打开导出结果」替换当前历史，阶段菜单仍指向复筛，浏览器后退跳过帮助页。同一目标用侧栏入口时，后退返回帮助，阶段菜单指向导出 | 已实现 | 页内导航复用历史与阶段记忆逻辑，后退能回到帮助。 | 真实go(export)从Help返回Help。 | [W-follow][W-follow] |

## 保留的接口与验证边界

- B-DYN-14：共享设置总览没有可靠的逐槽 metadata/用量，前端只能显示未知状态并避免错误推断。
- C-DYN-20：现接口没有第二来源就绪预检查；前端解释要求和配置入口，服务端仍可返回降级结果。
- C-DYN-21/F-DYN-16：同签名 DOM 保持稳定；全局版本/协作者变化仍需必要 GET，未声称清除了全部轮询。
- E-18：现有 SoF API 只有写入修订，未提供删除/真撤销。已有值可载入人工编辑后形成新修订；无法纯前端恢复未判定状态。
- E-22：前端拦截常见缺项并映射已知错误；后端统一 code + params 结构不在本波。
- 共享成员权限、真实第三方 AI、macOS/Linux 安装器、真实屏幕阅读器及所有原报告计时基准没有完整复验；各行保留其实际覆盖。

## 证据索引

- **W**：[工作流处置表](E:/rf215-authfe/out/ui-polish-acceptance/workflow/coverage.md)
- **W-live**：[工作流自然轮询实测](E:/rf215-authfe/out/ui-polish-acceptance/workflow/qa/workflow-results.json)
- **W-follow**：[训练/设置滚动/历史实测](E:/rf215-authfe/out/ui-polish-acceptance/workflow/qa/followup-results.json)
- **W-final**：[工作流模态/断连/移动端实测](E:/rf215-authfe/out/ui-polish-acceptance/workflow/qa/final-results.json)
- **W-E10**：[E10聚类提示真实实测](E:/rf215-authfe/out/ui-polish-acceptance/workflow/qa/e10-settled-results.json)
- **B**：[模型设置处置表](E:/rf215-authfe/out/ui-polish-acceptance/keys/handoff.md)
- **B-live**：[模型设置真实浏览器](E:/rf215-authfe/out/ui-polish-acceptance/keys/browser_results.json)
- **B-A20**：[A20记住我说明真实实测](E:/rf215-authfe/out/ui-polish-acceptance/keys/core-browser/remember-note-after.json)
- **I**：[导入处置表](E:/rf215-authfe/out/ui-polish-acceptance/import/import-disposition.md)
- **I-live**：[导入真实浏览器](E:/rf215-authfe/out/ui-polish-acceptance/import/qa/result.json)
- **I-lazy**：[延迟模块加载真实浏览器](E:/rf215-authfe/out/ui-polish-acceptance/import/qa/lazy-result.json)
- **E**：[完整评价/证据/GRADE交接](E:/rf215-authfe/out/ui-polish-acceptance/evidence/HANDOFF.md)
- **E-live**：[完整评价/证据/GRADE真实浏览器](E:/rf215-authfe/out/ui-polish-acceptance/evidence/browser-results.json)
- **E-error**：[已有错误切语言实测](E:/rf215-authfe/out/ui-polish-acceptance/evidence/error-language-results.json)
- **R**：[关系/SoF/旧RoB交接](E:/rf215-authfe/out/ui-polish-acceptance/relations/DELIVERY.md)
- **R-live**：[旧RoB/SoF真实浏览器](E:/rf215-authfe/out/ui-polish-acceptance/relations/rob-sof-browser-results.json)
- **R-conflict**：[关系双标签页真实409](E:/rf215-authfe/out/ui-polish-acceptance/relations/relation-browser-results.json)
- **R-qual**：[定性评价展开态实测](E:/rf215-authfe/out/ui-polish-acceptance/relations/qual-browser-results.json)
- **R-E04**：[E04反向来源跳转真实浏览器](E:/rf215-authfe/out/ui-polish-acceptance/relations/e04-browser-results.json)
- **ROOT**：[当前集成源码](E:/rf215-authfe/custom_frontend/index.html)
- **N-core**：[核心Node回归](E:/rf215-authfe/build/tests/ui_polish_core.test.cjs)
- **N-recovery**：[本地恢复Node回归](E:/rf215-authfe/build/tests/ui_polish_recovery.test.cjs)
- **N-evidence**：[证据保存与空方案Node回归](E:/rf215-authfe/build/tests/workflow_evidence_save_ui.test.cjs)
- **R-core**：[核心九场景真实浏览器](E:/rf215-authfe/out/ui-polish-acceptance/keys/core-browser/results.json)

[W]: E:/rf215-authfe/out/ui-polish-acceptance/workflow/coverage.md "工作流处置表"
[W-live]: E:/rf215-authfe/out/ui-polish-acceptance/workflow/qa/workflow-results.json "工作流自然轮询实测"
[W-follow]: E:/rf215-authfe/out/ui-polish-acceptance/workflow/qa/followup-results.json "训练/设置滚动/历史实测"
[W-final]: E:/rf215-authfe/out/ui-polish-acceptance/workflow/qa/final-results.json "工作流模态/断连/移动端实测"
[W-E10]: E:/rf215-authfe/out/ui-polish-acceptance/workflow/qa/e10-settled-results.json "E10聚类提示真实实测"
[B]: E:/rf215-authfe/out/ui-polish-acceptance/keys/handoff.md "模型设置处置表"
[B-live]: E:/rf215-authfe/out/ui-polish-acceptance/keys/browser_results.json "模型设置真实浏览器"
[B-A20]: E:/rf215-authfe/out/ui-polish-acceptance/keys/core-browser/remember-note-after.json "A20记住我说明真实实测"
[I]: E:/rf215-authfe/out/ui-polish-acceptance/import/import-disposition.md "导入处置表"
[I-live]: E:/rf215-authfe/out/ui-polish-acceptance/import/qa/result.json "导入真实浏览器"
[I-lazy]: E:/rf215-authfe/out/ui-polish-acceptance/import/qa/lazy-result.json "延迟模块加载真实浏览器"
[E]: E:/rf215-authfe/out/ui-polish-acceptance/evidence/HANDOFF.md "完整评价/证据/GRADE交接"
[E-live]: E:/rf215-authfe/out/ui-polish-acceptance/evidence/browser-results.json "完整评价/证据/GRADE真实浏览器"
[E-error]: E:/rf215-authfe/out/ui-polish-acceptance/evidence/error-language-results.json "已有错误切语言实测"
[R]: E:/rf215-authfe/out/ui-polish-acceptance/relations/DELIVERY.md "关系/SoF/旧RoB交接"
[R-live]: E:/rf215-authfe/out/ui-polish-acceptance/relations/rob-sof-browser-results.json "旧RoB/SoF真实浏览器"
[R-conflict]: E:/rf215-authfe/out/ui-polish-acceptance/relations/relation-browser-results.json "关系双标签页真实409"
[R-qual]: E:/rf215-authfe/out/ui-polish-acceptance/relations/qual-browser-results.json "定性评价展开态实测"
[R-E04]: E:/rf215-authfe/out/ui-polish-acceptance/relations/e04-browser-results.json "E04反向来源跳转真实浏览器"
[ROOT]: E:/rf215-authfe/custom_frontend/index.html "当前集成源码"
[N-core]: E:/rf215-authfe/build/tests/ui_polish_core.test.cjs "核心Node回归"
[N-recovery]: E:/rf215-authfe/build/tests/ui_polish_recovery.test.cjs "本地恢复Node回归"
[N-evidence]: E:/rf215-authfe/build/tests/workflow_evidence_save_ui.test.cjs "证据保存与空方案Node回归"
[R-core]: E:/rf215-authfe/out/ui-polish-acceptance/keys/core-browser/results.json "核心九场景真实浏览器"
