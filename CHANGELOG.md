# 更新日志 · Changelog

各版本的详细更新内容，按类别分节。本页只记变更；产品介绍见 [README](README.md)。每个版本保留发布当时的原始说明：2.1.5 起以中文与 English 双语详录，更早版本沿用当时的多语言原文。

Detailed, categorized release notes live here; see the [README](README.md) for the product itself. Each version keeps its notes as published: bilingual (中文 / English) from 2.1.5 onward, earlier versions in their original languages.

---

## [2.1.5] — 2026-11-13

### 中文

#### 变更摘要

- 十项新能力：筛选二选一化、筛选训练、检索式管理、ERIC 增量提醒、全文归档
  与整包交接、主题聚类排序、特征表/证据概览表、标准对照面板、撤稿关注、
  重复合并质量。
- 两轮独立审查共 62+5 项修复（安全、数字正确性、产品口径、回归钉）；
  发布流水线加固（版本标签防覆盖）与 Windows 崩溃恢复修复。
- 依赖安全升级（pypdf/Pillow）与导入复核加载提速约两成。
- 界面焕新：8 套主题、6 种开源字体、移动端界面、高亮批注。
- 双路形式化验证（12 名异构模型静态走查 + 真实浏览器动态验证）驱动的
  交互与稳定性修复批。
- 升级注意 8 条，特别是：存量「暂不确定」自动重置（有备份）、回收站保留期
  对旧删除任务生效。

#### 新功能

##### 界面焕新（主题 / 字体 / 移动端 / 批注）

- **8 套主题**（玉青/素简/澄蓝/柔彩/蓝晶/霜境/墨夜/石墨）× 浅色/深色 ×
  桌面/移动端共 32 态，主题来源链接随附；**6 种开源字体**（思源黑体/宋体、
  霞鹜文楷、Inter、Source Serif 4、JetBrains Mono）+ 本地字体导入（仅存
  本浏览器，不上传），界面字体不影响 PDF 自身排版。
- **移动端界面**：底部导航、更多菜单与阅读工具；桌面端紧凑功能菜单与
  阅读优先布局、首页与设置分类改版、独立模型设置页、关键词词组分栏管理。
- **高亮块批注**：正文高亮上直接写批注，保存/刷新读回；两个窗口同时编辑
  同一批注时以「预期原内容」比对新旧（200→409→改后再存），草稿不丢。
  已知边界：iOS Safari、系统软键盘与安全区表现待真机验证。

##### 交互与稳定性修复批（双路形式化验证驱动）

经 12 名双异构模型静态走查（160 项）+ 真实浏览器动态验证（120 项），
修复全部 10 项实测高危与九组强信号问题，要点：任一槽保存不再清空其他
配置草稿；弹窗打开期间 1/3 与 Ctrl+Z 不再写入底层文献；筛选卡在任务
切换竞态后不再失效；关系保存冲突（409）后不再用旧值覆盖他人修改；
AI 建议生成新增「取消等待」；RoB 评价草稿受离页保护、已存评估可回填
继续编辑；保存时 PDF 不再重置回第 1 页；后台轮询不再打断阅读（内容
未变不重建）；30 秒轮询/高亮等九组体验问题一并收口。macOS/Linux 单机
版新增找回密码通道（安装目录重置工具随包提供）。

##### 决策模型厂商目录补全

「决策模型 API Key」厂商目录新增 **TypeSafe 官方**（api.typesafe.ai，
Jev 官方 API）——与 CommandCode GOAT 中转并存；选择厂商即自动填写
接口地址与模型。另：界面上「钥匙」措辞统一为「API Key」。

##### 记住我与找回密码

登录页新增「记住我」：勾选后登录保持 7 天（关浏览器再开仍在）；任何一次修改或
重置密码，所有设备的长时令牌立即失效。新增「忘记密码」：凭用户名+一次性重置码
（15 分钟有效）设置新密码——服务器部署由管理员运行取码命令线下转交（不经邮件，
与「系统不发邮件」承诺一致），单机版在安装目录运行 `windows_start.py
--reset-user 用户名` 弹窗取码（macOS/Linux 为安装目录重置工具）。

##### 初筛/复筛改为二选一，「暂不确定」退场

正式筛选（初筛与复筛）全程只允许「纳入 / 排除」。「暂不确定」不再能通过正式
筛选保存——服务端会返回明确的冲突提示，你刚才的决定保持不变；撤销恢复历史
「暂不确定」记录不受影响。「暂不确定」唯一的居所是筛选训练：导入合并 CSV 与
仲裁历史里的旧「暂不确定」数据仍可读取、合并，只是不能再新存。任务设置里的
「显示暂不确定」开关退役，复筛入队只接受初筛「纳入」的条目，多轮筛选机制
（轮次标记、剩余未定数）整体移除；历史轮次数据原地保留，回滚旧版本仍可读。

##### 筛选训练（可选模块）

正式筛选开始前，团队成员可对小样本题录独立盲筛，系统对照负责人录入的金标准
自动计算一致率、κ 与 PABAK；分歧条目与「拿不准」边界池可直接作为共识讨论
材料，讨论后修订标准全程留痕。负责人可选开启「达标才解锁正式筛选」（默认只
展示状态、不拦截；开启后未达标成员写正式初筛会被引导提示，负责人本人豁免）。
**准入与方法学结论只认每套训练题的首次交卷成绩**——揭示答案后重做只算练习，
历史保留并带「练习，不计入门」标记。校准过程（样本量、阈值、结果、修订）
可一键生成中英文方法学引用段。

##### 检索式管理

团队管理界面新增【检索式管理】：构建器把 PICO/关键词概念块转写为各数据库
语法（PubMed 含 MeSH 建议位、Embase、Cochrane、ERIC 字段代码、Web of
Science 专业语法），PubMed 与 ERIC 可显式预演真实命中数（手动触发、短超时、
失败如实标「暂不可得」），其余库给语法与可点链接、不猜数。检索式库存放全部
检索式：状态分草稿/定稿/历史，**定稿与历史不可删除**、只能被新版本替代，带
版本链、订阅徽标、执行日期与命中数；不可变快照满足 PRISMA「最终检索式+执行
日期+命中数」的证据链要求。活体综述面板退化为只读订阅视图，已保存检索式
统一并入同一库，团队任何人都能看到当前定稿是哪一条。

##### ERIC 增量检索提醒

活体综述的增量检查从 PubMed 单源扩展为 PubMed + ERIC 双源（教育场景推荐
ERIC）。ERIC 采用「编号差集」识别新增文献：把检索式当前命中与任务内已知的
ERIC 编号对照，差集即增量，天然覆盖极老文献的补录。增量结果一律进入导入
复核「待审」，绝不直入任务。单次最多比对 500 条命中，超出时下一次检查
**从断点继续**比对尾部（断点持久化、检索式改写后自动失效从头重扫），全程
纯手动触发、无后台常驻。

##### 全文规范归档与整包导出/导入

「全文归档导出」把任务里的 PDF 按规范命名打包：每个文件叫
`{编号}-{一作姓氏}-{年份}.pdf`（中文姓氏原样保留），可选范围（全部已取全文 /
初筛纳入 / 复筛纳入），一眼可认、可直接拖回 Zotero 或共享盘；元数据缺年份或
作者按 unknown 命名、不跳过，文件缺失才跳过并如实计数。「整包导出」一键打包
任务全部数据（题录与各成员库、协作库、导入历史、轨迹、可选全文）用于换机、
交接与备份，不含任何凭据与环境信息；导入侧整体校验、幂等恢复。整包体积上限
2GB（超限明确提示），单成员 100MB、解压总量 2GB、压缩比 2000x 三道限额
声明值与实际字节双重校验。**2.1.5 早期构建（路径相对化修复之前）导出的
整包（全文路径为源机器绝对路径）仍可正常导入**，现导出的整包一律使用任务
目录相对路径——整包导出本就是 2.1.5 新功能，2.1.4 及更早版本没有整包文件。

##### 主题聚类排序（第四种排序策略）

排序设置新增「主题聚类」：按标题+摘要的词法相似度把同主题文献排在一起，
连着十几篇同一族方法学的文献依次过，判断尺度前后一致，不用在质性访谈和
RCT 之间来回切脑。它是唯一**不依赖任何模型配置**的排序策略——没配 AI 服务
也能用；纯本地计算、零联网、零模型文件，已做决定的条目顺序冻结、只重排未决
段，连续排除提醒与随机种子（同种子同顺序、可复现）照常生效。复筛队列同样
适用。帮助文档：`docs/TOPIC_CLUSTER_ORDER.md`。

##### 特征表与证据概览表自动生成

「特征表（Table 1）」从提取数据自动排版：选字段（默认全部维度，可勾选、排序、
分组）与纳入口径，输出 CSV 与 Word（表头分组、列宽自适应、表注自动生成），
行按作者-年份排序，数字一律来自提取数据、不新算。「证据概览表
（Summary of Findings）」是装配器：行=结局，列=纳入数、效应估计与置信区间、
研究局限性（来自文献评价）、GRADE 四构件降级建议与理由、总体置信度；缺
数据的格子留空加脚注（如「未评价」），绝不臆造；降级「建议」自动计算、最终
判定由研究者填写，两者分离展示。与编码矩阵同口径，初筛+复筛均纳入为默认
集合。

##### 标准对照常驻面板

初筛/复筛卡（及筛选训练卡）侧边新增可折叠面板，常驻显示本任务的纳入标准、
排除标准与关键术语定义（如「干预 ≥ 8 周」「同伴支持」的团队约定界定），新手
不必切出去翻方案。数据是任务级的、全员同一份；编辑只在方案设计器进行
（写入口唯一），面板刷新即达；折叠状态按成员记忆，没建方案的任务显示引导
跳转。

##### 撤稿/更正关注（Retraction Watch）

对已纳入的文献按 DOI 到 Crossref 查撤稿、编辑关切与更正，给受影响条目打
⚠ 徽标（撤稿类红、编辑关切橙、更正及其它未知更新灰），发现清单全员可读；
**纯手动触发、无后台常驻，且绝不自动改动任何筛选或编码决定**。检查分批
推进（每批 50 条、可断点续查），单次最多查 **300 条带 DOI 的条目**，超出时
提示剩余条数并支持一键续查；每次运行与发现只追加留痕。隐私上出站只到
Crossref、请求里只有 DOI（不发送摘要、全文或联系邮箱）；「未发现 ≠ 没有」
——只覆盖 Crossref 已收录且出版方已上报的通告，无 DOI 的文献无法检查，
是否剔除撤稿文献由团队决定。

帮助文档：`docs/RETRACTION_WATCH.zh-CN.md`。

##### 重复合并质量

批量确认重复的判据扩展：除「DOI 完全一致」外，一侧有 DOI、一侧缺 DOI 而
标题/一作姓/年份完全一致的组也可批量确认（仍需人点确认）；系统按单一判据
统一给出「可批量/不可批量+原因」。都是新导入时**建议保留信息更全的一条**
（DOI > 摘要 > 期刊 > 年份 > 作者 > 标题），界面写明理由（如「有 DOI、有
摘要」）。合并执行「只补空」：保留条目的空字段从被合并记录补上，两边都有
但不同的字段直接用保留条目、不进人工复核；DOI 碰撞有保护，合并可撤销。
信息差异支持**逐字段采用**（标题用原来的、期刊用新的），AI 助手可对冲突
字段给出建议（规则层零出站、六字段白名单、拿不准就标「不确定」，采纳留痕
区分规则/AI/人工）。

#### 改进与修复

- **仲裁盲裁决会话**：仲裁员逐条独立裁决，先判后示（做完才揭示他人裁决与
  理由）、断点续裁、finish 幂等；队列不泄露未裁决条目。仲裁决定二选一与
  「排除必填理由」改为**服务端强制**，不再依赖前端约束。
- **文献评价**：内置 16 个评价工具（CASP 系列 11 种、RoB 2、MMAT、AXIS、
  AMSTAR 2、CERQual），逐文献评价与总体档位；双评一致性看板（κ、一致率、
  逐条目对照）与 CSV 导出（导出带公式注入防护）；GRADE 证据评价的输入端点
  与研究局限性档位聚合，供证据概览表取数。双评揭示前评分逐人隔离、互不可见。
- **每日打卡与提醒**：每日筛选目标、连击天数与完成度；提醒支持邮件（SMTP）
  与通知通道，另有**日历订阅**——每位成员可自助生成/撤销订阅令牌，日历事件
  只含目标数字（今日已筛/剩余/连击），绝不含文献标题、摘要或决定；事件
  生成采用无夏令时陷阱的标准时区写法，同一任务两名成员的事件不会在日历端
  互相覆盖，订阅地址错误与任务不存在返回相同响应（不能用来探测任务是否存在）。
  通知通道依赖可选组件 Apprise（未安装时通道如实标记不可用，其余功能不受
  影响）。
- **导入复核 AI 助手**：对导入与任务内文献的信息差异逐字段给出「用左/用右/
  不确定」建议，只高亮不预选，采纳留痕；API 密钥仅存于本机，出站地址有
  链路本地/内网段拦截。
- **关键词高亮**：按成员维护词条集（多词、大小写/变音敏感可配），在题录
  标题与摘要中高亮命中；词条集上限 500，词条按边界等价去重并设命中预算，
  超长词条集不再拖慢队列。
- **研究设计识别扩类**：标题+摘要的规则识别从 7 类临床研究设计扩展到 11 类，
  新增混合方法、话语分析、质性研究、调查研究；证据地图与筛选辅助同步受益
  （规则为精确优先的启发式，都不中归类为「其它」）。
- **期刊样式 DOI 不再作为同一文献判定键**：形如期刊缩写式的 DOI 串不再把
  不同文献误判为重复（用户验证发现）。
- **SQL 全面参数化**：后端所有数据库查询改为参数绑定，动态 SQL 扫描器零
  未白名单告警，并纳入永久门禁。
- **依赖安全升级**：pypdf 6.20.1→6.21.4、Pillow 12.3.0→13.0.0。pypdf 负责
  解析导入的 PDF，本次升级消除其 12 条已知的拒绝服务类漏洞通告（恶意 PDF
  曾可让解析挂死或内存暴涨）；Pillow 为防御性升级（升级前版本下本产品
  无可达通告）。
- **导入复核更快**：导入批次视图与重复确认的加载路径提速约两成（消除每
  请求的重复建表与数百次逐条数据库查询），大型导入批次的复核界面响应
  明显更顺。
- **Windows 崩溃恢复**：后端在 Windows 上异常退出后，残留的任务锁不再
  卡死任务——重启应用即自动接管，此前需要手工删除锁文件才能打开任务。
- **检索式转写修正**：撇号关键词、带引号短语的字段标签、年份区间映射
  （未映射区间如实降级）等转写细节修正。
- 若干错误场景从「500 通用错误」收敛为结构化错误（个人库被占用→503 稍后
  重试、Unicode 数字字段→400、损坏的登录凭据存储→按既定格式返回错误、
  训练种子越界→422 等），前端可给出可读提示。

#### 内部深度审查修复摘要（62 项）

发布前用两组相互独立的人工智能审查交叉复核了 2.1.5 全部新增后端代码，
82 条原始发现去重合并为 63 条，其中 62 条经复现确认并已全部修复、配套
「去掉防护即失败」的回归测试（另 1 条未复现，经排查在发布版中触发路径
不可达）。按用户可感知的类别归纳：

- **安全修复（7 项，其中 2 项为最高严重度）**：一是通知通道曾可把任意环境
  变量（包括登录令牌签名密钥、AI 服务密钥）的值发送到通道地址——现改为
  「只允许白名单前缀的变量名」，保存与发送两处都拦截；二是恶意压缩包曾可
  把数 GB 内容解压进内存或永久占满磁盘——现改为流式解压，单成员 100MB、
  解压总量 2GB、压缩比 2000x 三道限额对声明值与实际字节双重校验（实测
  13KB 恶意包峰值内存仅约 406KB、零残留），全部整包临时目录 **60 分钟自动
  清理**、导入/导出/归档加**全局单操作互斥**。其余 5 项：评价导出 CSV 补
  公式注入防护；AI 服务地址拦截链路本地/内网/云元数据段（本机回环自托管
  不受影响）；三类联网组件补空代理口径；预览计数接口加条数上限与去重；
  全文读取路径收窄到任务目录。
- **研究产出数字正确性（6 项）**：证据概览表在「全低风险」这类最常见的好
  数据形态下曾整体报错，风险档位显示词曾错用降级语义；比值比夹具、未选
  变体分母、AXIS 反向题计分、RoB 2「无信息」组合判定、评价工具标识规范化
  逐一修正——这些直接影响 SoF/GRADE 导出的数字。
- **检索式转写与错误提示（10 项）**：检索式转写三处修正；七类「500 通用
  错误」收敛为可读提示。
- **产品口径裁定项（11 项）**：筛选训练只认首次交卷成绩；2.1.5 之前创建的
  单机旧任务建训练集回退本人个人库；升级首次打开即执行「暂不确定」重置的
  承诺补齐到单机旧任务；ERIC 增量断点续查；日历事件 UID 混入成员标识哈希、
  订阅 404 形状统一；整包路径相对化且旧绝对路径包兼容导入；归档导出总量
  上限 2GB；补空合并后发布全新验收快照；逐字段采用幂等；成员名校验与导入
  前检查口径统一。
- **其余修复与加固（28 项）**：关键词高亮词条去重与命中预算等资源防护
  2 项；κ 展示精度注记等低危散项 4 项；其余 22 项为「防护当前有效但缺
  回归测试」而补充的回归测试，不改变产品行为。

#### 已知边界与升级注意

0. **撤销泳道升级语义**：摘要形态的历史撤销快照在升级迁移时继续按守卫
   失配阻断（409），不静默放宽、也不静默保留。
1. **升级即自动重置存量「暂不确定」（一次性、幂等）**：每个成员库首次打开
   时，初筛与复筛中 `decision='maybe'` 的行删除（回到未筛）；初筛被重置
   条目的复筛决定一并删除。下游数据不删：编码值、提取数据、证据、PDF、
   轨迹原样保留，条目重新纳入后自动恢复。每个库旁写
   `{库名}.pre-binary-reset.bak` 备份（含 sha256 校验）。升级后待筛队列
   会变多，建议先完成筛选训练统一尺度，再全员重筛。
2. **全文与 AI 助手只认任务目录内的 PDF**：历史遗留指向任务目录外的登记行
   不再被读取；恢复办法=重新上传该 PDF 或重新执行 PDF 获取。
3. **旧绝对路径整包兼容**：2.1.5 早期构建导出的整包仍可正常导入；现导出
   一律任务目录相对路径。
4. **回收站保留期对旧删除的任务同样生效**：`.trash` 默认保留 30 天，升级后
   第一次打开任务列表会清除已满 30 天的旧条目。想保留请提前移出，或设
   `REVIEWFLOW_TRASH_TTL_DAYS=0` 关闭自动清理。
5. **撤稿关注的边界**：只覆盖 Crossref 已收录且出版方已上报的通告；无 DOI
   无法检查；是否剔除由团队决定。
6. **增量检索的边界**：ERIC 单次比对上限 500 条（可断点续查）；PubMed 按
   日期窗口；均手动触发、无后台轮询。
7. **通知通道的可选组件**：Apprise 需自行安装（未随包内置）；未安装时该
   通道标记不可用，邮件与日历订阅不受影响。
8. **前端交付口径**：全部新功能的界面入口已随本版本一并交付；行为口径
   前后端一致执行。

#### 升级提示（请转达团队成员）

升级后待筛队列会变多：原先「暂不确定」的条目已回到未筛，需要重新以
「纳入 / 排除」决策；升级前已自动备份，编码与提取成果没有丢失。管理员
请注意回收站 30 天清理对旧删除任务生效（见上第 4 条）。

### English

#### Summary

- Ten headline capabilities: binary screening decisions, pilot screening training, search-strategy management, ERIC incremental alerts, full-text archiving with whole-task package export/import, topic-cluster ordering, Table 1 / Summary-of-Findings generation, a standing eligibility-criteria panel, retraction watch, and duplicate-merge quality.
- 62+5 fixes from two independent AI-driven review rounds (security, numerical correctness, product semantics, regression nails), plus release-pipeline hardening (tag-overwrite guards) and a Windows crash-recovery fix.
- Dependency security upgrades (pypdf/Pillow) and ~20% faster import-review loading.
- Interface refresh: 8 themes, 6 open-source fonts, a mobile interface, and highlight annotations.
- An interaction-and-stability fix batch driven by dual-track formal verification (static review by 12 heterogeneous models + real-browser dynamic verification).
- 8 upgrade notes — notably: existing "Not Sure Now" decisions are auto-reset (with backups), and the trash-retention window now applies to previously deleted tasks.

#### New features

##### Interface refresh (themes / fonts / mobile / annotations)

- **8 themes** (Jade, Minimal, Things, AnuPpuccin, Blue Topaz, Nord, Obsidianite, Atom — sources credited) × light/dark × desktop/mobile = 32 states; **6 open-source fonts** (Noto Sans/Serif SC, LXGW WenKai Lite, Inter, Source Serif 4, JetBrains Mono) plus local font import (stored only in your browser, never uploaded). UI fonts never alter PDF typesetting.
- **Mobile interface**: bottom navigation, a "more" menu and reading tools; on desktop, compact feature menus, reading-first layouts, a reorganized Home & Settings with a dedicated model-settings page, and a two-pane keyword manager.
- **Highlight annotations**: write notes directly on text highlights, with save/refresh round-trip; concurrent edits in two windows are guarded by expected-content comparison (200→409→edit and re-save), and your draft is never lost. Known limit: iOS Safari, the system soft keyboard and safe areas await real-device verification.

##### Interaction & stability fix batch (dual-track formal verification)

After a static sweep by 12 heterogeneous models (160 findings) plus real-browser dynamic verification (120 findings), all 10 measured high-severity issues and nine strong-signal clusters were fixed. Highlights: saving one slot no longer clears drafts in other slots; pressing 1/3 or Ctrl+Z while a dialog is open no longer writes to the article underneath; the screening card no longer dies after a task-switch race; a 409 on relation save no longer lets stale values overwrite someone else's edit; AI suggestion generation gained "cancel wait"; RoB assessment drafts are protected against page loss and saved assessments can be reloaded for editing; saving no longer resets the PDF to page 1; background polling no longer interrupts reading when nothing changed. macOS/Linux desktop editions gained a password-reset path (a reset tool shipped with the installation).

##### Decision-model provider directory

The "Decision model API Key" directory adds **TypeSafe (official)** (api.typesafe.ai, the official Jev API) alongside the CommandCode GOAT relay; picking a provider auto-fills the base URL and model. UI wording now consistently says "API Key".

##### Remember me & password reset

"Remember me" keeps you signed in for 7 days across browser restarts; any password change or reset immediately invalidates long-lived tokens on all devices. "Forgot password" uses username + a one-time reset code (valid 15 minutes) — on server deployments an administrator runs the code-issuing command and hands it over offline (no email, consistent with the no-email promise); on desktop, run the reset tool in the installation folder.

##### Binary screening decisions; "Not Sure Now" retires

Both formal stages (title/abstract and full-text) accept only Include / Exclude. "Not Sure Now" can no longer be saved in formal screening — the server returns a clear conflict message and your latest decision stands; undoing history that contains "Not Sure Now" still works. Its only home is pilot screening: old data in import-merge CSVs and arbitration history remains readable and mergeable, just no longer savable. Full-text queues now accept only records included at initial screening; the repeat-round mechanism is removed (historical round data stays in place).

##### Pilot screening training (optional)

Before formal screening, team members blind-screen a small sample; the tool computes agreement, κ and PABAK against the owner's gold standard. Disagreements and an "uncertain" pool feed consensus discussions, with revision history kept. Owners may enable "training gate" (off by default; when on, members below the bar are guided on formal saves — the owner is exempt). **Admission and methodological conclusions count only each training set's first submission**; redoing after reveal is practice, marked as such. Calibration (sample size, thresholds, results, revisions) exports as a citable bilingual methodology paragraph.

##### Search-strategy management

A new team-management panel: the builder converts PICO/keyword blocks into database syntaxes (PubMed with MeSH suggestions, Embase, Cochrane, ERIC field codes, Web of Science), with explicit real hit-count previews for PubMed and ERIC (manual, short timeout, honest "unavailable" on failure) and syntax + links (no guessed numbers) elsewhere. The library tracks draft/final/history — final and history entries are immutable and can only be superseded, with version chains, subscription badges, execution dates and hit counts, satisfying PRISMA's evidence chain. Living-review panels become read-only subscription views.

##### ERIC incremental alerts

Incremental checks extend from PubMed-only to PubMed + ERIC (recommended for education topics). ERIC uses an ID-set difference so back-catalogue additions are covered. Results always land in import review, never directly in the task. 500 hits per comparison with persisted breakpoints (invalidated on query rewrite); fully manual, no background polling.

##### Full-text archiving & whole-task packages

Archive export names PDFs `{id}-{first-author}-{year}.pdf` (Chinese surnames preserved), with selectable scopes; missing metadata becomes "unknown" rather than a skip. Whole-task export bundles everything (records, member libraries, collaboration library, import history, trace, optional full texts) — no credentials, no environment info — with validated, idempotent import. Limits: 2GB package cap, 100MB per member, 2GB decompressed, 2000× compression ratio, checked against both declared and actual bytes. Packages from early 2.1.5 builds (absolute paths) still import; new packages use task-relative paths. (Package export itself is new in 2.1.5.)

##### Topic-cluster ordering (fourth ordering strategy)

Orders unscreened records by lexical similarity of title+abstract so methodologically similar studies are screened consecutively. It is the only strategy that needs no model configuration — purely local, no network, no model files; decided records keep their order, and streak reminders and reproducible random seeds still apply. Works for full-text queues too.

##### Table 1 & Summary of Findings

Table 1 typesets itself from extracted data (field picker, inclusion scope, grouped headers, auto footnotes; numbers come from your data, never recomputed). The SoF table is an assembler: rows are outcomes; columns include纳入 counts, effect estimates with CIs, risk-of-bias limitations, GRADE factor downgrade suggestions with reasons, and overall certainty. Missing cells stay empty with footnotes — never invented. Suggestions and final judgments are computed vs. human and shown separately.

##### Standing eligibility panel

A collapsible side panel on screening cards shows this task's inclusion criteria, exclusion criteria and key term definitions; data is task-level and identical for everyone, edited only in the protocol designer; collapse state is remembered per member.

##### Retraction watch

Manually triggered DOI checks against Crossref for retraction/editorial-expression/correction notices; affected records get a ⚠ badge (red/orange/grey). Findings are visible team-wide; nothing is ever auto-changed. Batches of 50 with resume, up to 300 DOI-bearing records per run. Outbound goes only to Crossref with DOI only — no abstracts, no full texts, no emails. "No finding" ≠ "none": coverage is Crossref-indexed, publisher-reported notices only; no DOI means no check; removal stays the team's decision.

##### Duplicate-merge quality

Batch confirmation now also covers groups where one side has a DOI and the other matches on title/first-author/year; a single rule decides "batchable or not, with reason". For two new imports the fuller record is recommended (DOI > abstract > journal > year > author > title) with the reason shown. Merges fill empty fields only; DOI collisions are protected; merges are undoable. Field-level adoption is supported, and the AI assistant may advise on conflict fields (rule-based zero-outbound, six-field whitelist, "unsure" when uncertain; adoption records rule/AI/human provenance).

#### Improvements & fixes

- **Blind adjudication sessions**: item-by-item independent adjudication, reveal-after-decision, resume support, idempotent finish; queues never leak undecided items. Binary adjudication and mandatory exclusion reasons are now enforced server-side.
- **Article appraisal**: 16 built-in tools (11 CASP variants, RoB 2, MMAT, AXIS, AMSTAR 2, CERQual), per-article and overall ratings; a dual-rating agreement dashboard (κ, agreement, per-item comparison) with CSV export (formula-injection guarded); GRADE inputs and risk-of-bias aggregations feed the SoF table. Ratings stay isolated per rater until reveal.
- **Daily goals & reminders**: daily screening goals, streaks and completion; email (SMTP) and notification channels plus **calendar subscriptions** — events contain goal numbers only (never titles, abstracts or decisions); DST-safe timezone handling; wrong-address and unknown-task subscription URLs return identical responses. Notification channels need the optional Apprise component (honestly marked unavailable without it).
- **Import-review AI assistant**: per-field use-left/use-right/unsure suggestions on information differences — highlight only, no pre-selection; adoption is logged; keys stay local and outbound addresses are screened against link-local/intranet ranges.
- **Keyword highlighting**: per-member term sets (multi-word, case/diacritic sensitivity configurable), capped at 500 terms with boundary-equivalent dedup and a hit budget so oversized sets can't slow the queue.
- **Study-design recognition** extends from 7 to 11 classes (adds mixed-methods, discourse analysis, qualitative, survey); evidence maps and screening assistance benefit.
- **Journal-style DOI strings** no longer false-match distinct records as duplicates.
- **SQL fully parameterized**; the dynamic-SQL scanner runs with zero un-whitelisted hits as a permanent gate.
- **Dependency security upgrades**: pypdf 6.20.1→6.21.4 (clears 12 known DoS-class advisories in PDF parsing) and Pillow 12.3.0→13.0.0 (defensive).
- **~20% faster import review** (eliminates per-request table rebuilds and hundreds of per-row queries).
- **Windows crash recovery**: stale task locks no longer wedge tasks after a backend crash — restarting the app takes them over.
- Search-translation fixes (apostrophe keywords, quoted-phrase field tags, year-range mapping with honest degradation) and several 500s converted to structured errors (503/400/422 with readable messages).

#### Internal deep-review summary (62 fixes)

Two independent AI review rounds over all new 2.1.5 backend code produced 63 deduplicated findings; 62 were reproduced, fixed, and pinned with remove-the-guard-and-it-fails regression tests (1 unreproducible, unreachable in the release build). By category:

- **Security (7, two of top severity)**: notification channels could exfiltrate arbitrary environment variables (including token-signing and AI keys) — now prefix-whitelisted at save and send; malicious archives could exhaust memory/disk — now streamed with triple limits (100MB/member, 2GB total, 2000× ratio) double-checked against actual bytes, 60-minute temp cleanup and a global single-operation mutex. Also: CSV formula-injection guards on appraisal exports; AI endpoint screening for link-local/intranet/metadata ranges (loopback self-hosting unaffected); empty-proxy handling for three networked components; preview-count caps; full-text reads confined to the task directory.
- **Research-output numerical correctness (6)**: SoF crashed on all-low-risk data; risk-tier wording used downgrade semantics; odds-ratio fixtures, unselected-variant denominators, AXIS reverse-scoring, RoB 2 "no information" combinations and tool-id normalization — all directly affect SoF/GRADE numbers.
- **Search translation & error messages (10)**.
- **Product-semantics rulings (11)**: first-submission-only training scores; legacy desktop tasks fall back to the owner's personal library for training sets; reset-on-upgrade extended to legacy desktop tasks; ERIC checkpoint resume; calendar UIDs; package paths relativized with backward compatibility; 2GB archive cap; fresh acceptance snapshots after fill-empty merges; idempotent field adoption; unified name validation.
- **Hardening & misc (28)**: resource guards (2), low-risk items (4), and 22 regression tests added for guards that worked but lacked nails.

#### Known boundaries & upgrade notes

0. Digest-form historical undo snapshots keep failing the upgraded guard with 409 (no silent relaxation or retention).
1. **"Not Sure Now" auto-reset on upgrade (once, idempotent)**: `maybe` rows return to unscreened per member library; downstream data (coding, extraction, evidence, PDFs, trace) is kept and revives on re-inclusion; a `.pre-binary-reset.bak` (with sha256) is written beside each affected library. Queues will grow — consider a training round first.
2. **Full-text & AI read only PDFs inside the task directory**; legacy out-of-directory registrations are no longer read — re-upload or re-fetch the PDF to restore.
3. Early-2.1.5 packages (absolute paths) still import; new packages use relative paths.
4. **Trash retention applies retroactively**: `.trash` keeps 30 days by default; the first task-list open after upgrade purges expired entries. Move anything you want to keep out of `.trash` first, or set `REVIEWFLOW_TRASH_TTL_DAYS=0`.
5. Retraction watch covers only Crossref-indexed, publisher-reported notices; no DOI means no check; removal is the team's decision.
6. Incremental search limits: ERIC 500 hits per run (resumable); PubMed by date window; manual only.
7. Apprise is optional and not bundled; without it the channel is marked unavailable (email and calendars unaffected).
8. All new UI entries ship in this version; frontend and backend enforce the same semantics.

#### Upgrade notes for your team

Queues will grow after upgrading: former "Not Sure Now" records are back to unscreened and need Include/Exclude decisions; backups were written automatically and no coding/extraction work is lost. Admins: the 30-day trash purge applies to previously deleted tasks (see note 4).

---

## [v2.1.4] — 2026-11-08

## Mubi·ReviewFlow 2.1.4

### 2.1.4 变更摘要

本版在 2.1.3 证据工作区的基础上，新增文献获取与检索通道，整批修复界面体验与导入流程，并公开统计验证与召回证书方法学材料；同时完成多轮评审与夜间扫描批次的加固（要点归纳自 2.1.3 以来的修复提交）：

- 从本机 Zotero 导入（默认关闭）：导入页新增「从本机 Zotero 导入」，可连接测试、浏览集合并导入所选集合。只连接本机 `127.0.0.1:23119` 的 Zotero 7 本地 API（无需钥匙），不访问 Zotero 云端，也不向任何外部地址发送数据；每次连接都由你手动触发。集合条目经既有受保护导入管线进入解析、去重、待审与溯源，失败时给出可照做的提示而非中断导入。
- PDF 自动获取（默认关闭）：任务设置开启后，初筛卡「获取全文」可按 DOI 经 Unpaywall 查找开放版本，下载后经既有 PDF 校验落盘并写入轨迹；批量入口逐条返回结果。隐私披露：开启后，应用会把**文献 DOI** 与负责人填写的**联系 email** 发送到 Unpaywall（`https://api.unpaywall.org`，其服务条款要求带 email），并可能向 Crossref（`https://api.crossref.org`）查询元数据建议，随后向 Unpaywall 返回的开放获取 PDF 链接发起下载；设置页常显「出站访问范围」，指向本机、私网或链路本地地址的 PDF 链接一律拒绝。元数据建议只读、绝不写库；文献已有全文时不静默覆盖，需显式确认。
- PubMed 检索订阅（默认关闭）：活体综述面板恢复「立即检查更新（PubMed）」，按已保存的检索式增量拉取，结果合成虚拟文件进入受保护导入待审（不直接入库、不重算合并）；可开启会话内自动检查（关闭应用即停，服务器不起后台线程、不做后台联网）；失败降级为「网络不可达，请手动导出文件后导入」，手动导出导入的离线路径保持可用。
- ERIC 检索通道：导入页新增「从 ERIC 检索文献」，提供检索式、命中预览（命中总数与前 N 条）与一键导入；ERIC 题录逐字段映射后经同一受保护导入管线进入待审，去重、溯源能力不缺失；界面十语言，仅访问官方 `api.ies.ed.gov`，且只在用户点击时出站。本功能只把题录写入用户本地任务，不新增任何公开再分发或导出面。
- 研究设计徽标（本地规则，非机器学习）：初筛卡按题名、摘要与原始出版类型字段显示研究设计分类徽标（Meta 分析、系统综述、随机对照试验、队列、病例对照、横断面、诊断准确性；未识别则不提示）；判为具体的非 RCT 设计时预选「研究设计不符」排除理由，但只填控件、绝不代存。分类与证据映射共用同一套规则，避免两处判定漂移。评估材料随库公开：48 篇文献（7 个设计类各 6 条，共 42 条；另 6 条为负类对照，负类不显示徽标），报告如实披露小样本、取样词与规则同源、摘要截断等偏差——这些数值是规则覆盖面的度量，不是性能承诺，请人工核对。
- 停止规则与召回证书：到达检查点（已筛 50%／70%／85%／95%）时，从未筛池随机抽取 30% 供人工判读（面板可一键随机置顶待判读）；抽验量足且未发现新的相关文献时，声明「总召回 ≥95%」。该声明的置信度按**超几何上界**随已发现相关数与抽验量逐次计算，界面明示「并非固定 95%」，精确数值暂不在界面显示（数值表见方法学报告）；「连续排除 N 条」类规则经仿真证明不能承载安全职责，已降级为默认关闭的进度提示，停止始终由研究者决定。方法学推导、90 场验证批原始数据与边界披露见 `docs/methodology/recall_certificate.md`。
- PWA 离线壳：应用可安装为独立窗口；离线时显示上次在线会话的页面外壳，并在页面顶部明确提示「离线只读快照」，不提供离线编辑与同步。离线壳不做预取、不访问任何外部端点；离线期间的写操作仍由既有错误处理优雅降级。
- 公开 API 文档：新增 `docs/API_REFERENCE.md`（稳定核心子集：认证、任务、导入与导入核验、筛选决策、全文、导出、主动学习排序；实验性端点明确声明不承诺稳定）与 `docs/openapi.json`（由离线脚本生成的全部已注册路由快照）。运行时仍不暴露 `/docs`、`/openapi.json` 与 `/redoc`（保持 404）。文档同时如实列出机器可读契约的已知限制（无响应模型、无 `securitySchemes`、查询参数与请求体位置按 FastAPI 签名规则），避免生成的客户端误用。
- 界面体验 60 项（34 项修复 + 26 项随版缓解；原批准单 61 项中有 1 项经复核不成立而放弃）：初筛排除理由记住上次选择，并支持「最近使用」与输入过滤；机器学习状态（已训练条数、标签不足提示）回到卡片可见位置，分数文案改为「预测纳入概率」；已决历史中不可点的「…」改为可展开完整列表；未保存提示列出草稿内容，草稿不可保存时按钮置灰并可一键清空；批量重排时显示「排序更新中」；导入批次下拉显示文件名、条数与时间，核验完成后启用按钮计数即时刷新；保存反馈出现在操作按钮旁而不是只在页顶状态行；撤销按钮禁用时说明原因（如启用后核验窗口已关闭）；移动端、导航、文案、加载等待与错误恢复各项按批准单逐条落地。
- 导入流程 12 项（全部按简报推荐方案，多为文案级）：「已完成」页签用状态徽章区分「已加入任务／结论已存且待启用／任务级遗留核验」，并注明该页签的混合构成；「待启用」改称「可加入任务」，按钮改为「加入任务／补充来源」，并注明这不是筛选纳入决定；导入横幅按真实条件区分「已有筛选或编码记录→追加导入暂存」与「无研究记录→初始导入」，不再使用「空任务」这一失实说法；旧式（legacy）候选组单独分区；可启用计数在前后端两侧同步。
- 撤销可用范围修正：撤销入口不再与全局工作流版本绑定。此前任一其他批次完成核验都会让全局版本加一，从而静默关闭本批次完好的撤销入口；现在失效判定改由与条目作用域一致的三道守卫承担（行状态可用、快照逐位一致、无已激活记录），写时并发安全仍由版本守卫把关。
- 升级后旧标签页的事件流连接（需知悉）：2.1.3 已打开且未刷新的标签页仍在用 `/api/events?token=` 连接，升级后这类请求会被拒绝（410 Gone，含长期令牌的 URL 参数已下线），页面退回 30 秒轮询，协作通知的实时性暂时丢失，刷新页面即恢复。按既定决定，界面不额外增加引导提示；现行前端只用一次性票据，不受影响。
- 统计方法验证材料公开（A7）：统计内核与 R 社区（metafor／meta／metadat）公开发表值做过逐位数值对拍——10 个经典数据集、204 项检查，159 项直接通过，11 项差异经逐项复核后全部归因于参考材料本身（内核算术无误），其余 31 项为设计内未运行、3 项只记录无判定参照。夹具、逐项明细、原始语料来源与复现命令随库分发，README 提供十语言链接。
- 夜间扫描批次确认的 286 项问题：约 268 项修妥，6 项经复核销账（基线已修，留探针证据），其余为部分落地并登记后续事项。用户可感知的结果包括：贝叶斯分析不再因样本量 n≥200 被整体拒绝（改用分片 CDF）；支持向量机（SVM）路径稠密化导致的内存峰值由约 152MB 降至约 1.1MB；退化标准误、浮点求和溢出、深拷贝深度、来源溯源与备份完整性等同族问题现在按同一套规则统一处理，减少同类边界情况各自产生差异行为。
- 构建与安装链：截断的下载不再被当作完整包信任，BOM 处理与「双卸载器互删」等问题修复；发布前的测试与静态校验不再在失败时被静默放过。Windows 引导不再使用滚动的 `get-pip.py`，改为从 PyPI 下载固定版本的 pip／setuptools／wheel／packaging wheel（发布后不可变），每个引导文件都按脚本内固定的 sha256 用 certutil 校验后才执行，缓存每次构建都会重新校验；下载失败与校验不符会给出不同提示。旧构建留下的 `build/downloads/get-pip.py` 已不再使用，可安全删除。桌面启动器在 `REVIEWFLOW_MODE=server` 下不再静默切到桌面模式：它会直接报错退出（返回码 2），不读取也不改动数据目录。
- 上传与路径安全：任务锁规范化（URL 别名不再派生第二把锁）、上传体积与文件部件数预检、限流按真实连接来源计数（伪造转发头无法绕过）、上传端点鉴权前置；SSE 进度流改用短期票据，长期令牌不再出现在 URL／反代日志／浏览器历史中。
- 认证与账户安全：注册／登录请求体上限与 422 回显收敛、bcrypt 口令长度上限显式校验、登录限流只计失败尝试；CSV 导出补公式注入防护。
- 协作权限与受保护撤销：属主解析失败的重试语义、legacy 任务统一口径、保留名／幽灵任务收口；撤销守卫幂等化并核验证据文件存在性。
- 受保护撤销的证据摘要与升级迁移（进阶说明，需运维知悉）：证据摘要不再把备注时间戳纳入，因此「同值重存刷新时间戳」不再误判为下游变更（修复前这类条目会被拒绝撤销）。修复范围**分两类，口径如下（勿扩大）**：全文快照形态（2.1.3 及更早）的存量 screen/fulltext 条目在升级后可直接撤销（纯时间戳漂移不再阻断），历史审计快照原样保留、迁移不改写；摘要形态（2.1.4 早期构建）只有可证明「内容未变、仅摘要公式变更」的条目（存量摘要恰等于当前内容按旧公式重算）才会被迁移为新公式并保留旧摘要，否则**保留旧摘要、继续按守卫失配阻断**——摘要不可逆，升级前已发生时间戳漂移或内容编辑的摘要形态条目**无法**在升级时判定或修复，仍会提示「已保存结果或下游工作已变更」，属有意的保守取舍（撤销不得静默覆盖较新的下游工作），可用「重建任务协作／在副本上人工核对」处理。
- 去重引擎保守化：自动合并仅保留可核验证据，模糊／键冲突改入人工候选；CJK 尾标点不再破坏 DOI 相等；消歧键拼接前安全截断，杜绝死锁文献。
- 文献解析器：RIS 标签映射与键消歧补齐、编码双候选择优与 GBK 回退、缺 ER 不再整文件拒绝。
- PRISMA 表单与流程图：数字勾稽补全（other 列、理由余量、研究≤报告）、DOT 标签转义、空决策不计入分母。
- AI 抽取建议（自带钥匙，BYOK）：个人／任务钥匙保险柜、逐条确认留痕、引文核验门槛分级。
- 轨迹导出与迁移：origin 结构化脱敏、events 分卷与存量迁移增量修复。
- 前端：导入复核页签／按钮／实际可启用三口径计数一致；401 强制登出与缓存令牌-内容绑定；后台分页在启用失败后不再停滞；加载动画、轮询与十语言词条多项修复。
- 发布链：release workflow 最小权限（构建作业只读且不持久化凭据）、appimagetool 固定版本与 sha256 校验、CI 内先跑测试套件再跑静态校验器。
- 仲裁页从服务器加载团队决策：联网团队模式下，合并仲裁页新增「从服务器加载全部筛选员决策」，读取任务目录下全部筛选员数据库，沿用既有的全员决策读取逻辑；双盲阶段进行中会被拒绝并提示，防止泄盲；加载后走同一套 κ 与冲突仲裁流程。离线 CSV 上传流程原样保留，服务器模式下也保留 CSV 作为回退；至少 2 位成员有决策记录才可加载（与 CSV 2–9 份同口径）。若某个筛选员库正被其他操作占用或暂时不可读，界面提示「稍后重试」而不是显示缺员的合并表，也不会把不完整的合并结果写入仲裁记录。
- 裁决员筛选开关：仲裁权保底不可关闭；「同时允许参与筛选」改为负责人对每名成员的开关，操作留痕并覆盖十语言。存量裁决员默认保留筛选权（不静默收回），新授权默认不带（勾选框默认不勾）；无筛选权的裁决员在筛选队列看到引导文案而不是裸 403。负责人切换开关后，成员进入初筛／复筛页会重新读取权限，不再沿用进入任务时的旧状态。
- 「暂不确定」只在第一轮：题录初筛进入第 2 轮及以后，已进入过复筛轮次的文献不再提供「2 暂不确定」——按钮隐藏、快捷键无效并给出说明，后端同样拒绝（此前任何轮次都可重复待定）；随后新导入、从未进入轮次的文献仍按第一轮提供。全文复筛保持单轮不变，「暂不确定」照常可用；已保存的决定与进入全文的流转不受影响。
- 终扫与验收修复（r13／r14，均为用户可感知项）：导入批次卡片的统计行在十种语言下都曾显示 `[object HTMLSpanElement]`，现恢复为真实数字与「条 / 组」单位；在「全部批次」列表停留时切换界面语言，列表会立即用已加载的数据重渲染，不重新请求服务器；合并仲裁页在「从服务器加载」或 CSV 合并后，阶段选择、CSV 选择与最近记录不再被永久禁用（此前一次加载后控件会一直处于禁用态）；正在进行的服务器合并请求在切换任务后不会再落到新任务上。PDF 自动获取对同一文献的多个已校验地址按序尝试连接，仅连接级失败才换址，且绝不重新解析域名；Crossref 元数据建议遇到上游空响应时返回空建议（200），不再 500。长时间导入期间，偏倚风险／DTA 结果页不再因数据库被占用而返回 500。日语界面中报告证据的「可信度」统一为「信頼性」，与术语表及其他模块一致。`network_random` 上报的 REML 对数似然更正为标准误差对照密度口径（含 `−log|X'X|` 项），与同库四个姊妹模块一致；若你比较过此前版本导出的 `fit.reml_log_likelihood`，数值会变化。效应数超过 1000 时使用的分块可辨识性特征值与条件数，现与稠密路径精确同量（普通规模走稠密路径，结果逐位不变）。
- 期刊样式 DOI 不再作为同一文献判定键（r17）：ERIC 等来源给整刊或整期填写的期刊样式 DOI（形如 `10.1002/(issn)1545-7249`）此后只作为书目信息保留在文献记录、导出与展示中，不再参与同一文献判定——同刊不同文献不会因共用一个整刊 DOI 而被合并或误配成「信息差异」；已在复核中的此类误配可稍后处理后重新导入。
- 仲裁提交强制二选一（r18，后端契约）：仲裁结论只接受「纳入」或「排除」，不能再保存为「暂不确定」；裁决为「排除」时必须填写仲裁理由。CSV 上传与「从服务器加载后保存」两条提交路径同口径，被拒时返回结构化错误（`arbitration_decision_invalid` / `arbitration_reason_required`）且不写入任何记录。已保存的历史仲裁记录保持 append-only、零改写，读取与导出不受影响；筛选员个人决策中的「暂不确定」仍照常合并。仲裁页下拉只保留「纳入／排除」及排除时理由必填的界面同步，由前端线按契约另行落地。
- 对标工程两项（ML 版预置分类器、SYNERGY 式公开外部回测与发表）：这两项在本版的计划范围内，但**未随本版交付**——本版交付的是上面的规则版研究设计分类器，源码中没有随附机器学习分类器模型或公开外部回测报告。若后续交付，会另行说明。

### English summary

- Import from local Zotero (off by default): the import page gains “Import from local Zotero” with a connection test, collection browsing and import of selected collections. It connects only to Zotero 7’s local API on `127.0.0.1:23119` (no key), never reaches the Zotero cloud and sends nothing to any external address; every connection is triggered manually. Items go through the existing protected-import pipeline (parsing, de-duplication, review, provenance), and failures give actionable guidance instead of breaking the import.
- PDF auto-fetch (off by default): once enabled in task settings, “Get full text” on the screening card looks up an open version by DOI via Unpaywall, downloads it, validates it through the existing PDF checks and writes it to disk with a trace event; the batch entry reports per-item results. Privacy disclosure: when enabled, the app sends the **article DOI** and the **contact email** entered by the task owner to Unpaywall (`https://api.unpaywall.org`, whose terms require an email) and may query Crossref (`https://api.crossref.org`) for metadata suggestions, then downloads from the open-access PDF link returned by Unpaywall; the settings page always shows the outbound endpoints, and PDF links pointing to loopback, private or link-local addresses are refused. Metadata suggestions are read-only and never write to the library; an existing full text is never overwritten silently and requires explicit confirmation.
- PubMed search subscription (off by default): the living-review panel restores “Check now (PubMed)”, fetching incrementally with the saved search strategy and staging results in protected import review (never straight into the library and without recomputing merges); in-session automatic checks can be enabled (they stop when the app closes; the server never starts a background thread or connects in the background). Failures degrade to “Network unreachable; export the file manually and import it”, so the manual export/import path keeps working.
- ERIC search channel: the import page gains “Search ERIC”, with a query, a hit preview (total hits and the first N records) and one-click import; ERIC records are mapped field by field and enter the same protected import review, so de-duplication and provenance still apply. The UI is available in ten languages, contacts only the official `api.ies.ed.gov`, and only on user click. This feature only writes records into the user’s local task and adds no public redistribution or export surface.
- Study-design badges (local rules, not machine learning): screening cards show a study-design badge derived from title, abstract and raw publication-type fields (meta-analysis, systematic review, randomized controlled trial, cohort, case-control, cross-sectional, diagnostic accuracy; nothing is shown when unrecognized); when a specific non-RCT design is identified, the “study design mismatch” exclusion reason is prefilled but never saved on the user’s behalf. Classification and evidence mapping share one rule set, so the two cannot drift. Evaluation materials ship with the app: 48 articles — 42 across seven design classes (6 each) plus 6 negative-class controls, which never show a badge. The report discloses small-sample bias, sampling terms overlapping the rules, and abstract truncation; these numbers measure rule coverage, not a performance promise, and manual verification is expected.
- Stopping rules and recall certificate: at check points (50% / 70% / 85% / 95% screened), a random 30% sample of the unscreened pool is drawn for manual judgement (the panel can move a random sample to the top of the queue); with a sufficient sample and zero newly found relevant articles, the tool reports “total recall ≥ 95%”. The confidence of that claim follows the **hypergeometric upper bound** and is recomputed as relevant articles are found and the sample grows; the interface states it is “not a fixed 95%” and does not yet show the numeric value (the table is in the methodology report). Simulations showed that “N consecutive exclusions” rules cannot carry safety responsibility, so they are demoted to a progress hint, off by default; stopping remains the researcher’s decision. Methodology, the raw data of the 90-run validation batch and the boundary disclosures are in `docs/methodology/recall_certificate.md`.
- PWA offline shell: the app can be installed as a standalone window; while offline it shows the page shell from the last online session and clearly labels it as a read-only snapshot, with no offline editing or syncing. The shell does no prefetching and contacts no external endpoint; writes made while offline still degrade through the existing error handling.
- Public API documentation: new `docs/API_REFERENCE.md` (stable core subset: auth, tasks, import and import review, screening decisions, full text, exports, active-learning ranking; experimental endpoints are explicitly outside the stability promise) and `docs/openapi.json` (an offline-generated snapshot of every registered route). The runtime still does not expose `/docs`, `/openapi.json` or `/redoc` (all remain 404). The documentation also states the known limits of the machine-readable contract (no response models, no `securitySchemes`, query-versus-body placement following FastAPI’s signature rules) so generated clients are not misused.
- 60 interface-experience items (34 fixes plus 26 in-release mitigations; 1 of the 61 approved items was dropped after review showed it did not hold): the exclusion-reason dropdown remembers the last choice and offers “recently used” plus type-to-filter; machine-learning status (trained-label count, insufficient-labels hint) is visible on the card again and the score wording becomes “predicted inclusion probability”; the inert “…” in decision history expands into a full list; the unsaved-changes prompt lists the draft and greys out saving when it cannot be saved, with one-click clearing; batch re-ranking shows an “updating order” indicator; import batch dropdowns show file name, item count and time, and the activation count refreshes as soon as review completes; save feedback appears next to the action buttons instead of only in the page-top status line; a disabled Undo button states why (for example, the review window closed on activation); mobile, navigation, wording, loading-wait and error-recovery items were implemented as approved.
- 12 import-flow items (all following the brief’s recommended option, mostly wording-level): the “Completed” tab uses status badges to distinguish “already in the task / decision saved and awaiting activation / task-level legacy review” and notes the tab’s mixed contents; “Ready to activate” becomes “Ready to add to task”, with buttons “Add to task / Add as source” and an explicit note that this is not a screening-inclusion decision; the import banner distinguishes “existing screening or coding records → additional import is staged” from “no research records → initial import” instead of the misleading “empty task”; legacy candidate groups get their own section; the activatable count is synchronised on both the frontend and backend.
- Undo availability fixed: the Undo entry point is no longer tied to the global workflow version. Previously, any other batch completing review bumped the global version and silently closed this batch’s still-valid undo entry. Invalidity is now decided by three guards scoped to the entry (row state available, snapshot identical field by field, no activated records), while concurrent-write safety still goes through the version guard.
- Old tabs after upgrading (please note): a 2.1.3 tab that is still open and not refreshed keeps connecting with `/api/events?token=`; after the upgrade those requests are refused (410 Gone — the long-lived-token URL parameter has been retired), the page falls back to 30-second polling, real-time collaboration notifications are temporarily lost, and refreshing the page restores them. As decided, no additional guidance prompt is shown; the current frontend uses one-time tickets only and is unaffected.
- Statistical validation materials published (A7): the statistical kernel was compared value by value against published results from the R ecosystem (metafor / meta / metadat) — 10 classic datasets and 204 checks, 159 direct matches, 11 differences all traced by item-level cross-checking to the reference material itself (no algorithmic error in the kernel), with the remaining 31 not run by design and 3 recorded as informational without a reference verdict. Fixtures, per-check details, source corpora and reproduction commands ship with the app, linked from the README in ten languages.
- 286 findings confirmed by the overnight scan: about 268 fixed, 6 closed after re-check as already fixed on the baseline with probe evidence, and the rest partially landed with tracked follow-ups. User-perceivable outcomes include: Bayesian analysis no longer rejects large samples (n ≥ 200) wholesale (paged CDF); the memory peak caused by densification on the support-vector-machine (SVM) path drops from about 152 MB to about 1.1 MB; and degenerate standard errors, floating-point summation overflow, deep-copy depth, source provenance and backup integrity are now handled by the same unified rules, so edge cases of the same family behave consistently.
- Build and install chain: truncated downloads are no longer trusted as complete packages, BOM handling and the “two uninstallers deleting each other” problem are fixed, and the pre-release test and static checks no longer fail open — a failing check is no longer silently ignored. The Windows bootstrap no longer uses the rolling `get-pip.py`: it downloads fixed-version pip / setuptools / wheel / packaging wheels from PyPI (immutable once published), verifies each one with `certutil` against a sha256 pinned in the script before running it, and re-verifies the cache on every build; download failures and checksum mismatches now give distinct messages. An old `build/downloads/get-pip.py` is no longer used and can be deleted safely. In `REVIEWFLOW_MODE=server` the desktop launcher no longer silently falls back to desktop mode: it refuses to start (exit code 2) and neither reads nor modifies the data directory.
- Security-hardening release: upload and path safety (canonical task locks, size and part-count prechecks, rate limiting counted per real connecting peer so forged forwarding headers cannot bypass it, auth before locks), short-lived SSE tickets replacing long-lived tokens in URLs — long-lived tokens no longer appear in URLs, reverse-proxy logs or browser history.
- Account security: register/login body limits with tightened 422 error output, bcrypt length validation, failure-only login throttling, CSV formula-injection protection.
- Collaboration and protected undo: owner-resolution retry semantics, unified legacy-task handling, reserved-name and ghost-task cleanup, idempotent undo guards with evidence-file checks.
- Undo evidence digests and upgrade migration (advanced note for operators): evidence digests no longer include note timestamps, so a same-value re-save no longer looks like downstream work (such entries were previously refused). The fix covers **two distinct cases — read the scope exactly as written**: full-snapshot journal entries **for screen/fulltext records** (2.1.3 and earlier) become undoable after upgrading (pure timestamp drift no longer blocks), and the migration never rewrites them, so their audit text is preserved; **digest-form** entries (early 2.1.4 builds) are migrated only where it can prove the content is unchanged and only the digest formula differs (the stored digest equals the current content hashed with the old formula), as a new digest plus a retained old one; otherwise they **keep the old digest and stay blocked** by the guard. Digests are irreversible: digest-form entries that had already drifted or been edited before the upgrade **cannot** be judged or repaired at upgrade time and will still report “saved result or downstream work has changed” — a deliberate conservative trade-off (Undo must never silently overwrite newer downstream work); recover them by recreating task collaboration or by reconciling on a copy.
- Deduplication made conservative: auto-merge only with verifiable evidence; ambiguous cases become manual candidates; DOI equality no longer broken by CJK punctuation; the disambiguation key is safely truncated before concatenation, so records can no longer deadlock on over-long keys.
- RIS parser: tag mapping, key disambiguation, dual-candidate encoding selection with GBK fallback, and a missing ER no longer rejects the whole file.
- PRISMA forms and diagrams: numeric cross-checks, DOT label escaping, empty decisions excluded from denominators.
- Optional AI extraction suggestions (bring-your-own key) with a personal/task key vault, per-item confirmation trails and tiered citation-verification thresholds.
- Trajectory export and migration: structured origin redaction, event volume splitting and incremental repair of existing migrations.
- Import review: counts consistent across tabs, buttons and activation; forced logout on 401 and cache token–content binding; background paging survives activation failures; loading animations, polling and ten-language entries fixed in several places.
- Release chain least privilege: read-only build jobs without persisted credentials, pinned appimagetool with sha256, and the test suite runs before the static validators in CI.
- Loading team decisions from the server on the arbitration page: in networked team mode, the merge-arbitration page gains “Load all screeners’ decisions from the server”, reading every screener database under the task directory with the existing all-screener read logic. Loading is refused with a message while a double-blind stage is in progress, to prevent unblinding; loaded decisions go through the same κ and conflict-arbitration flow. The offline CSV upload flow is preserved unchanged and remains available as a fallback in server mode; loading requires at least 2 members with recorded decisions (the same rule as 2–9 CSV files). If a screener database is in use or temporarily unreadable, the page asks you to try again later instead of showing a merge table that is missing a member, and the incomplete merge is never saved as an arbitration record.
- Adjudicator screening toggle: the adjudication right can never be switched off; “also participates in screening” becomes a per-member switch for the task owner, recorded in the audit trail and covered in ten languages. Existing adjudicators keep their screening right by default (it is never silently withdrawn), while new grants default to off (the checkbox starts unchecked); adjudicators without screening rights see guidance text in the screening queue instead of a bare 403. After the owner changes the switch, members re-read their permission when they open a screening page, instead of keeping the state read when they entered the task.
- “Not Sure Now” only in the first round: from round 2 of title-and-abstract screening onward, articles that have already entered a repeat round no longer offer “2 Not Sure Now” — the button is hidden, the shortcut is refused with an explanation, and the backend rejects it too (previously any round could re-save an uncertain decision); articles imported later that have never entered a round keep the first-round behaviour. Full-text screening remains a single pass with “Not Sure Now” available as before; already-saved decisions and the hand-off to full text are unaffected.
- Final-sweep and acceptance fixes (r13 / r14, all user-perceivable): the batch-card statistics line showed `[object HTMLSpanElement]` in all ten languages and now shows the real numbers with their “records / groups” units; switching interface language while staying on the “all batches” list re-renders that list from already-loaded data without re-requesting the server; on the merge-arbitration page the stage selector, CSV selector and recent-records control are no longer left permanently disabled after a server load or a CSV merge; an in-flight server merge no longer lands on a different task after you switch tasks. PDF auto-fetch tries each already-validated address of a candidate in order, only moving on after a connection-level failure and never resolving a hostname again; Crossref metadata suggestions return an empty suggestion set (200) when the upstream message is empty instead of failing with 500. During long imports, bias-risk and DTA result pages no longer answer 500 because the database is busy. In Japanese, report-evidence “credibility” now reads 「信頼性」 consistently with the terminology table and the rest of the app. The `network_random` REML log-likelihood now reports the standard error-contrast density (including the `−log|X'X|` term), consistent with the four sibling modules; if you compared `fit.reml_log_likelihood` from an earlier build, the number will change. The blockwise identifiability eigenvalues and condition numbers used above 1000 effects now match the dense path exactly (normal sizes use the dense path, whose results are bit-for-bit unchanged).
- Journal-style DOIs no longer act as same-article identity keys (r17): journal-level DOIs that some sources (such as ERIC) fill in for a whole journal or issue (for example `10.1002/(issn)1545-7249`) are still kept as bibliographic information in article records, exports and the interface, but no longer take part in same-article matching — different articles in the same journal are no longer merged or misreported as “information differences” because they share a journal DOI. Any such mismatch already in review can be handled later and re-imported.
- Adjudication submissions are now binary (r18, backend contract): an arbitration outcome accepts only Include or Exclude and can no longer be saved as Not Sure Now; an Exclude outcome requires an adjudication reason. The CSV-upload path and the “load from server, then save” path apply the same rule and return structured errors (`arbitration_decision_invalid` / `arbitration_reason_required`) without writing any record when rejected. Already-saved arbitration records stay append-only and unchanged, and reading/export are unaffected; screeners’ own “Not Sure Now” votes still merge as before. The matching interface change (dropdown limited to Include/Exclude, with a required reason for Exclude) is delivered separately by the frontend lane under the contract.
- Two scope items (ML-based pre-set classifier, SYNERGY-style public external backtest and publication): they are part of the 2.1.4 scope plan but **were not delivered in this release** — what ships is the rule-based study-design classifier described above, and the source tree contains no machine-learning classifier model or public external backtest report. Any later delivery will be described separately.

### 2.1.3

（以下为 2.1.3 原文正文，未改写。）

### Added

- Report and sample relationships: save personal proposals, confirm relationships through adjudication, revise or unlink them, and undo the latest changes while retaining original reports.
- Qualitative evidence drafts with findings, supporting quotations, page references, context and reviewer interpretations; export CSV and JSON with original source filenames.
- Optional workspaces for JBI meta-aggregation, thematic synthesis, framework synthesis and meta-ethnography, plus mixed-method integration notes, JBI/CASP appraisal and CERQual/ConQual confidence drafts.
- A theme–evidence matrix and human review of shared original evidence across reports.

### Improved

- Machine Learning ranking refreshes after 20 changed article decisions by default, with a personal interval from 1 to 1000. Decisions are saved immediately between ranking updates.
- Machine Learning settings and model selections are personal in team tasks, preserving independent screening.
- Clearer import-review wording and more compact information, retaining the familiar Todo card and existing desktop/mobile layouts.
- Reviewed, eligible records can be activated while other records continue loading in the background.

### Fixed

- Repeated loading caused by an outdated import-review refresh baseline, overlapping refresh checks and stale background reads during activation.
- Unnecessary save or policy-review prompts after reverting an edit or receiving count-only updates.
- Save dialogs that requested corrections to hidden fields.
- Confusing manual training-minimum input: the application now uses its existing system default.

The new evidence and appraisal tools record researchers’ judgments. They do not automatically identify shared samples, exclude studies, synthesize qualitative findings or change existing statistical models.

Download the Windows installer, macOS disk image or Linux AppImage below. Existing research data is retained when the application is upgraded.

### 中文

### Added

- 报告与样本关联：个人线索、团队裁决、修改、解除及撤销，保留原始报告。
- 定性证据草稿与主题—证据矩阵，可记录多条发现、引文、出处、情境和研究者解释，导出带原始来源文件名的 CSV／JSON。
- JBI、主题综合、框架综合、元民族志及混合方法工作区；JBI／CASP 方法学评价和 CERQual／ConQual 置信度草稿。

### Improved

- 机器学习默认每 20 篇发生变化的决定更新排序，可按个人设置 1–1000 篇；每篇决定仍立即保存。
- 模型、策略与更新间隔按成员保存，独立阶段使用各自标签。
- 导入说明和按钮更清楚，减少重复文字，保留原有待办卡片与桌面／移动布局。
- 后台加载期间也可启用已核验且具备条件的记录。

### Fixed

- 修复已完成核验列表因刷新版本基线过旧而反复加载，以及启用时旧后台响应覆盖状态的问题。
- 修复恢复原值、仅统计变化时误报未保存或策略冲突的问题。
- 保存窗口显示需要纠正的实际字段；训练启动门槛由系统使用现有默认值管理。

新增综合和评价工作区用于记录人工判断，不自动生成综合结论、质量总分或新的统计模型。

---

## [v2.1.3] — 2026-10-14

## Mubi·ReviewFlow 2.1.3

### Added

- Report and sample relationships: save personal proposals, confirm relationships through adjudication, revise or unlink them, and undo the latest changes while retaining original reports.
- Qualitative evidence drafts with findings, supporting quotations, page references, context and reviewer interpretations; export CSV and JSON with original source filenames.
- Optional workspaces for JBI meta-aggregation, thematic synthesis, framework synthesis and meta-ethnography, plus mixed-method integration notes, JBI/CASP appraisal and CERQual/ConQual confidence drafts.
- A theme–evidence matrix and human review of shared original evidence across reports.

### Improved

- Machine Learning ranking refreshes after 20 changed article decisions by default, with a personal interval from 1 to 1000. Decisions are saved immediately between ranking updates.
- Machine Learning settings and model selections are personal in team tasks, preserving independent screening.
- Clearer import-review wording and more compact information, retaining the familiar Todo card and existing desktop/mobile layouts.
- Reviewed, eligible records can be activated while other records continue loading in the background.

### Fixed

- Repeated loading caused by an outdated import-review refresh baseline, overlapping refresh checks and stale background reads during activation.
- Unnecessary save or policy-review prompts after reverting an edit or receiving count-only updates.
- Save dialogs that requested corrections to hidden fields.
- Confusing manual training-minimum input: the application now uses its existing system default.

The new evidence and appraisal tools record researchers’ judgments. They do not automatically identify shared samples, exclude studies, synthesize qualitative findings or change existing statistical models.

Download the Windows installer, macOS disk image or Linux AppImage below. Existing research data is retained when the application is upgraded.

### 中文

### Added

- 报告与样本关联：个人线索、团队裁决、修改、解除及撤销，保留原始报告。
- 定性证据草稿与主题—证据矩阵，可记录多条发现、引文、出处、情境和研究者解释，导出带原始来源文件名的 CSV／JSON。
- JBI、主题综合、框架综合、元民族志及混合方法工作区；JBI／CASP 方法学评价和 CERQual／ConQual 置信度草稿。

### Improved

- 机器学习默认每 20 篇发生变化的决定更新排序，可按个人设置 1–1000 篇；每篇决定仍立即保存。
- 模型、策略与更新间隔按成员保存，独立阶段使用各自标签。
- 导入说明和按钮更清楚，减少重复文字，保留原有待办卡片与桌面／移动布局。
- 后台加载期间也可启用已核验且具备条件的记录。

### Fixed

- 修复已完成核验列表因刷新版本基线过旧而反复加载，以及启用时旧后台响应覆盖状态的问题。
- 修复恢复原值、仅统计变化时误报未保存或策略冲突的问题。
- 保存窗口显示需要纠正的实际字段；训练启动门槛由系统使用现有默认值管理。

新增综合和评价工作区用于记录人工判断，不自动生成综合结论、质量总分或新的统计模型。

---

## [v2.1.2] — 2026-10-13

## Mubi·ReviewFlow 2.1.2

### 中文

### 新增功能

- 新增紧凑的动态圆环加载提示，显示已加载记录数量；加载中断后可重试。
- 新增批量核验：确认 DOI 一致且无身份冲突的重复候选，或批量保留已有文献信息；操作前可查看清单，启用前支持整批撤销。

### 功能改进

- 首批记录加载后即可开始核验，其余记录在后台分批加载。
- 后台加载时保留当前记录、已选选项、展开的详情和页面位置。
- 加载提示支持十种语言；页脚显示实际应用版本。
- 导入核验采用紧凑布局：批次与加载状态集中显示，来源文件按需展开，分类选项均分一行；手机端提供上一条、记录选择和下一条。
- 确认结果保存后直接更新受影响记录并显示下一项；辅助统计在后台更新，减少连续核验时的等待。

### 问题修复

- 修复大量核验记录加载期间页面长时间停留在加载提示、无法先行操作的问题。
- 修复后台旧数据覆盖已保存核验结果的问题；保存失败保留当前记录和选择，批量操作任一项失效时整批回滚。

### English

### Added

- Added a compact animated loading ring that shows the number of records loaded and provides a retry option if loading is interrupted.
- Added bulk review for duplicate candidates with matching DOI values and no identity conflict, and for retaining existing bibliographic information. Preview the affected items before confirming and undo the whole action before activation.

### Improved

- The first batch of records is available for review while the remaining records load in the background.
- Background loading preserves the current record, selected options, expanded details and page position.
- Loading messages are available in ten languages, and the footer displays the running application version.
- Made import review more compact: batch and loading status together, expandable source files, equally spaced category tabs, and previous/select/next record controls on phones.
- Saved reviews update affected records and make the next item available immediately after the save response; supporting statistics refresh in the background.

### Fixed

- Fixed the long wait before users could work with large import-review lists.
- Fixed stale background responses overwriting saved reviews. Failed saves retain the current record and selection; an invalid bulk item rolls back the entire action.

### Français

### Nouveautés

- Ajout d’un indicateur circulaire animé et compact, qui affiche le nombre d’enregistrements chargés et permet de réessayer après une interruption.
- Ajout de la vérification en lot des doublons avec DOI identique et sans conflit d’identité, et de la conservation des informations existantes en lot. Une liste précède la confirmation et le lot peut être annulé avant activation.

### Améliorations

- Le premier lot d’enregistrements peut être vérifié pendant le chargement des suivants en arrière-plan.
- Le chargement en arrière-plan conserve l’enregistrement actuel, les options sélectionnées, les détails ouverts et la position de la page.
- Les messages de chargement sont disponibles en dix langues et le pied de page affiche la version de l’application.
- Interface de vérification plus compacte : lot et chargement réunis, fichiers sources dépliables, onglets répartis sur une ligne et navigation précédent/sélection/suivant sur téléphone.
- Après la réponse de sauvegarde, les notices concernées sont mises à jour et la suivante devient disponible ; les statistiques sont actualisées en arrière-plan.

### Corrections

- Correction de l’attente prolongée avant de pouvoir traiter les longues listes de vérification d’importation.
- Correction des réponses anciennes écrasant les vérifications sauvegardées. Un échec conserve la notice et la sélection ; un élément invalide annule tout le lot.

### Русский

### Добавлено

- Добавлен компактный анимированный круговой индикатор: он показывает число загруженных записей и позволяет повторить загрузку после сбоя.
- Добавлена пакетная проверка дубликатов с одинаковым DOI без конфликта идентичности и пакетное сохранение текущих сведений. Перед подтверждением показан список; до активации можно отменить весь пакет.

### Улучшено

- Первую группу записей можно проверять, пока остальные загружаются в фоновом режиме.
- Фоновая загрузка сохраняет текущую запись, выбранные параметры, раскрытые сведения и положение страницы.
- Сообщения о загрузке доступны на десяти языках; в нижней части страницы отображается версия приложения.
- Проверка импорта стала компактнее: пакет и загрузка рядом, раскрываемые исходные файлы, равномерные вкладки и переход назад/выбор/вперёд на телефоне.
- После ответа о сохранении обновляются затронутые записи и доступна следующая; вспомогательная статистика обновляется в фоне.

### Исправлено

- Устранено длительное ожидание перед началом работы с большими списками проверки импорта.
- Старые фоновые ответы больше не перезаписывают сохранённую проверку. При сбое сохраняются запись и выбор; недопустимый элемент откатывает весь пакет.

### Español

### Añadido

- Se añadió un indicador circular animado y compacto que muestra cuántos registros se han cargado y permite reintentar si la carga se interrumpe.
- Añadida revisión en lote de duplicados con DOI coincidente y sin conflicto de identidad, y conservación de información existente en lote. Se muestra una lista antes de confirmar y se puede deshacer el lote antes de activarlo.

### Mejorado

- El primer grupo de registros queda disponible para su revisión mientras los demás se cargan en segundo plano.
- La carga en segundo plano conserva el registro actual, las opciones seleccionadas, los detalles abiertos y la posición de la página.
- Los mensajes de carga están disponibles en diez idiomas y el pie de página muestra la versión de la aplicación.
- Revisión de importaciones más compacta: lote y carga juntos, archivos fuente desplegables, pestañas repartidas en una fila y controles anterior/selección/siguiente en móviles.
- Tras la respuesta de guardado, se actualizan los registros afectados y se habilita el siguiente; las estadísticas se actualizan en segundo plano.

### Corregido

- Se corrigió la larga espera antes de poder trabajar con listas extensas de revisión de importaciones.
- Corregidas respuestas antiguas que sobrescribían revisiones guardadas. Los fallos conservan registro y selección; un elemento no válido revierte todo el lote.

### 日本語

### 追加

- 読み込み済み件数を示し、中断後に再試行できる、コンパクトなアニメーション付き円形インジケーターを追加しました。
- DOI が一致し同一性の競合がない重複候補の一括確認と、既存書誌情報の一括保持を追加。確認前に一覧を表示し、有効化前は一括操作を取り消せます。

### 改善

- 残りの記録をバックグラウンドで読み込む間に、最初の記録から確認できるようになりました。
- バックグラウンド読み込み中も、現在の記録、選択内容、展開した詳細、ページ位置を保持します。
- 読み込みメッセージが10言語に対応し、フッターにアプリのバージョンを表示します。
- 取り込み確認をコンパクト化。バッチと読み込み状況を集約し、ソースファイルを展開表示。分類タブを均等配置し、スマートフォンに前／選択／次の操作を追加。
- 保存応答後に対象レコードを更新して次の項目を操作可能にし、補助統計はバックグラウンドで更新します。

### 修正

- 大量のインポート検証記録を操作できるまで長時間待つ問題を修正しました。
- 古い読み込み応答が保存済みの確認を上書きする問題を修正。保存失敗時はレコードと選択を保持し、一括操作に無効な項目があれば全体を取り消します。

### Português

### Adicionado

- Foi adicionado um indicador circular compacto e animado, que mostra a quantidade de registros carregados e permite tentar novamente após uma interrupção.
- Adicionada revisão em lote de duplicados com DOI correspondente e sem conflito de identidade, e manutenção das informações existentes em lote. A lista aparece antes da confirmação e o lote pode ser desfeito antes da ativação.

### Melhorias

- O primeiro grupo de registros fica disponível para revisão enquanto os demais carregam em segundo plano.
- O carregamento em segundo plano preserva o registro atual, as opções selecionadas, os detalhes abertos e a posição da página.
- As mensagens de carregamento estão disponíveis em dez idiomas, e o rodapé exibe a versão do aplicativo.
- Revisão de importações mais compacta: lote e carregamento juntos, arquivos de origem expansíveis, abas distribuídas em uma linha e controles anterior/seleção/próximo no celular.
- Após a resposta de salvamento, os registros afetados são atualizados e o próximo fica disponível; estatísticas auxiliares são atualizadas em segundo plano.

### Correções

- Foi corrigida a longa espera antes de ser possível trabalhar com listas extensas de revisão de importação.
- Corrigidas respostas antigas que sobrescreviam revisões salvas. Falhas mantêm o registro e a seleção; um item inválido reverte todo o lote.

### Deutsch

### Neu

- Ein kompakter, animierter Ladekreis zeigt die Anzahl geladener Datensätze an und ermöglicht einen erneuten Versuch nach einer Unterbrechung.
- Sammelprüfung für Duplikate mit gleichem DOI ohne Identitätskonflikt und gesammeltes Beibehalten vorhandener Informationen hinzugefügt. Eine Liste erscheint vor der Bestätigung; vor Aktivierung ist die gesamte Aktion rückgängig zu machen.

### Verbesserungen

- Der erste Datensatzstapel kann geprüft werden, während weitere Datensätze im Hintergrund geladen werden.
- Beim Laden im Hintergrund bleiben der aktuelle Datensatz, ausgewählte Optionen, geöffnete Details und die Seitenposition erhalten.
- Ladehinweise sind in zehn Sprachen verfügbar; die Fußzeile zeigt die Anwendungsversion.
- Kompaktere Importprüfung: Stapel und Ladestatus zusammen, aufklappbare Quelldateien, gleichmäßig verteilte Kategorien und Zurück/Auswahl/Weiter auf Smartphones.
- Nach der Speicherantwort werden betroffene Datensätze aktualisiert und der nächste wird bedienbar; ergänzende Statistiken werden im Hintergrund aktualisiert.

### Fehlerbehebungen

- Die lange Wartezeit, bevor umfangreiche Importprüflisten bearbeitet werden konnten, wurde behoben.
- Veraltete Hintergrundantworten überschreiben keine gespeicherten Prüfungen mehr. Fehler behalten Datensatz und Auswahl; ein ungültiges Element setzt die gesamte Sammelaktion zurück.

### Српски

### Додато

- Додат је компактан анимирани кружни показатељ који приказује број учитаних записа и омогућава поновни покушај након прекида.
- Додата групна провера дупликата са истим DOI без сукоба идентитета и групно задржавање постојећих података. Листа се приказује пре потврде; цела радња може се опозвати пре активације.

### Побољшано

- Прва група записа доступна је за проверу док се остали учитавају у позадини.
- Учитавање у позадини чува тренутни запис, изабране опције, отворене детаље и положај странице.
- Поруке о учитавању доступне су на десет језика, а подножје приказује верзију апликације.
- Компактнија провера увоза: пакет и учитавање заједно, склопиве изворне датотеке, равномерне категорије и претходни/избор/следећи запис на телефону.
- По одговору о чувању ажурирају се погођени записи и следећи је доступан; помоћна статистика се ажурира у позадини.

### Исправљено

- Отклоњено је дуго чекање пре почетка рада са великим листама за проверу увоза.
- Стари позадински одговори више не преписују сачуване провере. Грешка задржава запис и избор; неважећа ставка поништава целу групну радњу.

### 한국어

### 추가

- 불러온 기록 수를 표시하고 중단 후 다시 시도할 수 있는 작고 움직이는 원형 로딩 표시를 추가했습니다.
- DOI가 같고 식별 정보 충돌이 없는 중복 후보의 일괄 확인과 기존 서지 정보 일괄 유지를 추가했습니다. 확인 전에 목록을 표시하며 활성화 전에 전체 작업을 취소할 수 있습니다.

### 개선

- 나머지 기록이 백그라운드에서 로드되는 동안 첫 번째 기록 묶음부터 검토할 수 있습니다.
- 백그라운드 로딩 중에도 현재 기록, 선택한 옵션, 펼친 상세 내용과 페이지 위치를 유지합니다.
- 로딩 메시지는 10개 언어를 지원하며, 바닥글에 앱 버전을 표시합니다.
- 가져오기 검토를 간결하게 정리했습니다. 배치와 로딩 상태 통합, 원본 파일 펼치기, 균등한 분류 탭, 모바일 이전/선택/다음 기록 탐색을 제공합니다.
- 저장 응답 후 해당 기록을 갱신하고 다음 항목을 바로 사용할 수 있게 하며 보조 통계는 백그라운드에서 갱신합니다.

### 수정

- 가져오기 검토 기록이 많을 때 작업을 시작하기까지 오래 기다려야 하는 문제를 수정했습니다.
- 오래된 로딩 응답이 저장한 검토를 덮어쓰는 문제를 수정했습니다. 저장 실패 시 기록과 선택을 유지하고 일괄 항목이 유효하지 않으면 전체 작업을 되돌립니다.

---

## [v2.1.1] — 2026-10-13

## Mubi·ReviewFlow 2.1.1

### 中文

### 新增功能

- **统一导入核验：** 在导入文献页集中处理重复候选、信息差异、待启用、已完成和稍后处理的记录。尚未启用时，可撤销符合安全条件的上次核验操作。

### 功能改进

- **完整导入工作区：** 分设导入与报告、导入核验和导入历史；选择文件后明确确认导入，查看统计并下载原始上传文件。悬浮待办可收缩为仅显示当前事项的胶囊，全部完成后自动隐藏。
- **导出结果：** 按筛选结果、编码数据和全文高亮笔记组织导出卡片，说明文件格式、内容和适用用途，并显示生成与下载状态。保留原有七种导出操作。
- **自适应布局：** 桌面分类条占满一行；手机页面提供适合触控的卡片与操作区域。首页和导入文献页的页脚显示实际版本号。

### 问题修复

- 核验后保持页面位置和操作区高度，自动呈现当前分类中的下一条记录，减少反复滚动。改进分页、刷新、任务切换和导出反馈状态。

### English

### Added

- **Unified import review:** Review duplicate candidates, information differences, records ready for activation, completed records and deferred items in one workspace. Undo the last eligible review action before activation.

### Improved

- **Import workspace:** Separate upload and report, review, and history tabs; confirm selected files before importing, inspect counts and download original uploads. A floating checklist collapses to the current item and hides when complete.
- **Results exports:** Cards organize screening results, coding data and full-text highlight notes, explain formats and contents, and show generation and download status. All seven existing exports remain available.
- **Responsive layout:** Desktop category tabs fill a row; mobile cards and controls support touch use. Home and import footers show the running application version.

### Fixed

- Keep the page position and review controls stable after confirmation, then display the next item in the current category. Improve pagination, refresh, task switching and export feedback.

### Français

### Nouveautés

- Vérification unifiée des doublons, différences d’informations, éléments à activer, éléments terminés et éléments différés. Annulation de la dernière vérification admissible avant activation.

### Améliorations et corrections

- Onglets d’importation et rapport, vérification et historique ; confirmation des fichiers sélectionnés et téléchargement des originaux. Liste flottante réductible à la tâche actuelle, masquée une fois terminée.
- Exports classés en résultats de sélection, données de codage et notes du texte intégral, avec indications de format et état du téléchargement. Les sept exports existants sont conservés.
- Catégories réparties sur une ligne sur ordinateur, commandes tactiles sur mobile et version dans les pieds de page. Position stable après vérification ; pagination, actualisation et changement de tâche améliorés.

### Русский

### Новое

- Единая проверка кандидатов в дубликаты, различий метаданных, готовых к активации, завершённых и отложенных записей. Последнюю допустимую проверку можно отменить до активации.

### Улучшения и исправления

- Вкладки импорта и отчёта, проверки и истории; подтверждение выбранных файлов, статистика и загрузка оригиналов. Плавающий список сворачивается до текущего пункта и скрывается после завершения.
- Экспорт результатов отбора, данных кодирования и заметок полного текста с пояснениями форматов и состоянием загрузки. Сохранены все семь вариантов экспорта.
- Категории занимают строку на компьютере, мобильные элементы удобны для касаний; внизу указана версия. Положение страницы сохраняется после проверки; улучшены обновление, страницы и переключение задач.

### Español

### Novedades

- Revisión unificada de duplicados, diferencias de información, registros listos para activar, completados y pendientes. Deshacer la última revisión válida antes de activar.

### Mejoras y correcciones

- Pestañas de importación e informe, revisión e historial; confirmación de archivos y descarga de originales. La lista flotante se reduce al elemento actual y se oculta al terminar.
- Exportación por resultados de selección, datos de codificación y notas del texto completo, con formatos y estado de descarga. Se conservan las siete exportaciones existentes.
- Categorías a todo el ancho en escritorio y controles táctiles en móvil; versión en el pie de página. Posición estable tras revisar; mejoras de paginación, actualización y cambio de tarea.

### 日本語

### 新機能

- 重複候補、情報の相違、有効化待ち、完了、後で確認する記録を一か所で確認できます。有効化前は、安全条件を満たす最後の確認操作を取り消せます。

### 改善・修正

- 取り込みとレポート、確認、履歴のタブを用意し、選択ファイルを確認してから取り込みます。統計と元ファイルを確認できます。フローティングのチェックリストは現在の項目に縮小でき、完了後に非表示になります。
- スクリーニング結果、コーディングデータ、全文ハイライトのノートをカードで整理し、形式と内容、出力状態を表示します。従来の7種類の出力を維持しています。
- デスクトップの分類タブは行全体を使い、モバイルはタッチ操作に対応します。フッターに実際のバージョンを表示します。確認後のページ位置を維持し、ページ切り替え、更新、タスク切り替えを改善しました。

### Português

### Novidades

- Revisão unificada de duplicatas, diferenças de informação, registros prontos para ativar, concluídos e adiados. Desfaça a última revisão elegível antes da ativação.

### Melhorias e correções

- Abas de importação e relatório, revisão e histórico; confirmação dos arquivos e download dos originais. A lista flutuante reduz-se ao item atual e desaparece ao concluir.
- Exportações de resultados de triagem, dados de codificação e notas do texto completo, com descrição de formatos e estado de download. As sete exportações existentes foram mantidas.
- Categorias ocupam uma linha no desktop e controles móveis são adequados ao toque; rodapés mostram a versão. Posição estável após a revisão, com melhorias de paginação, atualização e troca de tarefa.

### Deutsch

### Neu

- Einheitliche Prüfung von Duplikatkandidaten, Informationsunterschieden, zur Aktivierung bereiten, abgeschlossenen und zurückgestellten Datensätzen. Die letzte zulässige Prüfung lässt sich vor der Aktivierung rückgängig machen.

### Verbesserungen und Korrekturen

- Registerkarten für Import und Bericht, Prüfung und Verlauf; Dateiauswahl bestätigen und Originaldateien herunterladen. Die schwebende Checkliste lässt sich auf den aktuellen Punkt verkleinern und verschwindet nach Abschluss.
- Exportkarten für Screening-Ergebnisse, Kodierdaten und Volltextnotizen erläutern Format, Inhalt und Downloadstatus. Alle sieben bisherigen Exporte bleiben verfügbar.
- Kategorien füllen am Desktop eine Zeile; mobile Bedienelemente unterstützen Touch. Fußzeilen zeigen die Version. Die Seitenposition bleibt nach Prüfung erhalten; Blättern, Aktualisieren und Aufgabenwechsel wurden verbessert.

### Српски

### Ново

- Јединствена провера дупликата, разлика у подацима, записа спремних за активирање, завршених и одложених ставки. Последња дозвољена провера може се поништити пре активирања.

### Побољшања и исправке

- Картице за увоз и извештај, проверу и историју; потврда изабраних датотека и преузимање оригинала. Плутајућа листа се своди на тренутну ставку и нестаје када се заврши.
- Извоз резултата скрининга, података кодирања и белешки пуног текста са описима формата и статусом преузимања. Свих седам постојећих извоза је задржано.
- Категорије заузимају цео ред на рачунару; мобилне контроле подржавају додир, а подножје приказује верзију. Положај странице остаје стабилан после провере; побољшани су страничење, освежавање и промена задатка.

### 한국어

### 새 기능

- 중복 후보, 정보 차이, 활성화 대기, 완료 및 보류 항목을 한 작업 공간에서 검토합니다. 활성화 전에 안전 조건을 충족하는 마지막 검토 작업을 실행 취소할 수 있습니다.

### 개선 및 수정

- 가져오기 및 보고서, 검토, 기록 탭을 구분했습니다. 선택 파일을 확인한 후 가져오고 통계와 원본 파일을 확인할 수 있습니다. 떠 있는 체크리스트는 현재 항목만 표시하도록 축소하며 완료되면 숨겨집니다.
- 선별 결과, 코딩 데이터, 전문 하이라이트 메모를 내보내기 카드로 구성하고 형식, 내용 및 다운로드 상태를 표시합니다. 기존 7개 내보내기 기능을 유지합니다.
- 데스크톱 분류 탭은 한 행을 채우고 모바일은 터치에 적합한 컨트롤을 제공합니다. 바닥글에 실제 버전을 표시합니다. 검토 후 페이지 위치를 유지하며 페이지 이동, 새로 고침 및 작업 전환을 개선했습니다.

---

Existing screening decisions, coding values, evidence and original uploads are preserved. The import-review undo history uses an additive database migration with a backup before upgrading. See [2.1.1 upgrade and rollback notes](https://github.com/RUCbookshelf/mubi-reviewflow/blob/v2.1.1/docs/releases/2.1.1.md).

---

## [v2.1.0] — 2026-10-13

## 木笔 ReviewFlow 2.1.0

### English

- Mobile presentation now follows the approved concept: compact navigation, separate language and theme controls, animated search, sheets and disclosures. Original desktop controls, research logic, permissions and data remain shared.
- Trace search accepts a partial article title or identifier across matching history, with date/stage/action filters and reviewer isolation. Selected Trace exports retain the same search filter. Redundant headings were removed; disclosure indicators now use SVG.

- Fixed a false unsaved-change warning after a correction opened from Trace completes the screening queue. Saved decisions now allow normal navigation; genuinely unsaved reason edits still require confirmation. This replacement build remains version 2.1.0.

### 中文

- 移动端按已确认概念重新实现：紧凑导航、独立语言与主题按钮、搜索框动画、底部面板和详情展开；复用电脑端控件、研究逻辑、权限和数据。
- 轨迹支持按文献标题片段或标识检索符合筛选条件的历史记录，保留日期、阶段、操作筛选和个人记录隔离。删除重复标题，展开图标统一使用 SVG。

- 修复从轨迹返回并更正决定、完成筛选队列后仍提示未保存的错误；已保存的决定可正常离开页面，真正未保存的排除原因仍受保护。本次替换构建仍为 2.1.0。

### Français

- La présentation mobile suit le concept approuvé : navigation compacte, langue et thème séparés, recherche animée, panneaux et détails dépliables. Les commandes, la logique de recherche, les permissions et les données restent communes au bureau.
- La recherche dans Trace accepte une partie du titre ou de l’identifiant, avec les filtres de date, d’étape et d’action et l’isolation des évaluateurs. Les titres redondants sont supprimés et les indicateurs utilisent SVG.

- Correction de l’alerte erronée de modifications non enregistrées après une correction depuis Trace terminant la file de sélection. Les décisions enregistrées permettent la navigation ; les motifs réellement non enregistrés restent protégés. Cette compilation de remplacement conserve la version 2.1.0.

### Русский

- Мобильный интерфейс следует утверждённому макету: компактная навигация, отдельные кнопки языка и темы, анимированный поиск, панели и раскрытие деталей. Элементы управления, исследовательская логика, права и данные общие с настольной версией.
- Поиск в Trace поддерживает часть названия или идентификатора статьи с фильтрами даты, этапа и действия и изоляцией записей рецензентов. Удалены повторяющиеся заголовки; индикаторы раскрытия используют SVG.

- Исправлено ложное предупреждение о несохранённых изменениях после исправления решения из Trace и завершения очереди отбора. Сохранённые решения позволяют переходить между страницами; несохранённые причины исключения по-прежнему защищены. Версия заменяющей сборки остаётся 2.1.0.

### Español

- La interfaz móvil sigue el concepto aprobado: navegación compacta, idioma y tema separados, búsqueda animada, paneles y detalles desplegables. Comparte controles, lógica de investigación, permisos y datos con el escritorio.
- Trace busca por parte del título o identificador, manteniendo filtros de fecha, etapa y acción y el aislamiento entre revisores. Se eliminaron títulos redundantes y los indicadores usan SVG.

- Se corrigió el aviso incorrecto de cambios sin guardar después de una corrección desde Trace que completa la cola de cribado. Las decisiones guardadas permiten navegar; los motivos realmente sin guardar siguen protegidos. Esta compilación de reemplazo mantiene la versión 2.1.0.

### 日本語

- 承認済みのモバイル案に合わせ、コンパクトなナビゲーション、独立した言語・テーマ操作、検索アニメーション、パネルと詳細展開を実装。デスクトップと同じ操作、研究処理、権限、データを使用します。
- Trace は文献タイトルの一部または識別子で履歴を検索し、日付・段階・操作の条件とレビュアー間の記録分離を維持します。重複見出しを削除し、展開アイコンを SVG に統一しました。

- Trace から開いた文献の判定を修正して選別キューを完了した後、未保存の変更があると誤表示される問題を修正しました。保存済みの判定からは正常に移動でき、実際に未保存の除外理由は引き続き保護されます。差し替え版のバージョンは 2.1.0 のままです。

### Português

- A interface móvel segue o conceito aprovado: navegação compacta, idioma e tema separados, busca animada, painéis e detalhes expansíveis. Compartilha controles, lógica de pesquisa, permissões e dados com o computador.
- A busca no Trace aceita parte do título ou identificador, mantendo filtros de data, etapa e ação e o isolamento entre revisores. Títulos redundantes foram removidos e indicadores usam SVG.

- Corrigido o aviso indevido de alterações não salvas após uma correção pelo Trace que conclui a fila de triagem. Decisões salvas permitem navegar; motivos realmente não salvos continuam protegidos. Esta compilação de substituição mantém a versão 2.1.0.

### Deutsch

- Die mobile Oberfläche folgt dem freigegebenen Entwurf: kompakte Navigation, getrennte Sprach- und Themensteuerung, animierte Suche, Panels und aufklappbare Details. Bedienelemente, Forschungslogik, Rechte und Daten werden mit dem Desktop geteilt.
- Die Trace-Suche unterstützt Teile des Artikeltitels oder der Kennung mit Datums-, Phasen- und Aktionsfiltern sowie getrennten Gutachterdaten. Doppelte Überschriften wurden entfernt; Aufklappindikatoren verwenden SVG.

- Falsche Warnungen vor ungespeicherten Änderungen nach einer Korrektur aus Trace beim Abschluss der Screening-Warteschlange wurden behoben. Gespeicherte Entscheidungen erlauben die Navigation; tatsächlich ungespeicherte Ausschlussgründe bleiben geschützt. Die Ersatzversion bleibt 2.1.0.

### Српски

- Мобилни интерфејс прати одобрени концепт: компактна навигација, одвојене контроле језика и теме, анимирана претрага, панели и прошириви детаљи. Контроле, истраживачка логика, дозволе и подаци заједнички су са приказом на рачунару.
- Trace претрага прихвата део наслова или идентификатора, уз филтере датума, фазе и операције и изолацију записа рецензената. Уклоњени су поновљени наслови; индикатори користе SVG.

- Исправљено је нетачно упозорење о несачуваним изменама после исправке из Trace која завршава ред за скрининг. Сачуване одлуке омогућавају навигацију; стварно несачувани разлози искључења и даље су заштићени. Заменска верзија остаје 2.1.0.

### 한국어

- 승인된 모바일 콘셉트에 맞춰 간결한 탐색, 별도 언어 및 테마 버튼, 검색 애니메이션, 패널과 상세 펼치기를 구현했습니다. 데스크톱과 같은 조작, 연구 로직, 권한 및 데이터를 사용합니다.
- Trace는 문헌 제목 일부나 식별자로 이력을 검색하며 날짜·단계·작업 필터와 검토자별 기록 분리를 유지합니다. 중복 제목을 제거하고 펼치기 아이콘을 SVG로 통일했습니다.

- Trace에서 연 문헌의 결정을 수정하여 선별 대기열을 완료한 뒤 나타나던 잘못된 미저장 경고를 수정했습니다. 저장된 결정에서는 정상적으로 이동할 수 있으며, 실제로 저장하지 않은 제외 사유는 계속 보호됩니다. 교체 빌드의 버전은 2.1.0을 유지합니다.

### Distribution and compatibility

Windows x64 installer, macOS DMG, Linux x86_64 AppImage and SHA256SUMS.txt are built by GitHub Actions. All ten interface languages and desktop light/dark palettes remain available. Mobile layout applies at widths up to 760 CSS pixels.

This update introduces no persistent database migration. Existing screening, coding, Undo, CSV exports and PRISMA manual/task-data modes are retained. The Trace API adds an optional title-search query; the exact subject_id filter remains compatible. No Remote Operation or tunnel is added. Server access still requires a reachable, configured server.

Back up the complete data directory and close the application before upgrading.

### Validation

- Syntax checks for changed Python, JavaScript, inline scripts and language packs.
- Prior isolated GUI review: 12 routes at mobile and desktop widths, separate language/theme controls, ten languages, responsive restoration, PDF controls, menus and disclosure cleanup.
- Focused disposable Trace search checks: pagination, title/identifier matching, stage/date filters, exact-ID compatibility and reviewer isolation.
- Replacement-build checks with disposable data: Trace corrections, last-save Undo, genuine unsaved-reason protection, and navigation after queue completion at desktop and mobile widths.
- Physical iOS/Android devices and a production HTTPS proxy were not validated; no complete automated regression suite was run.

[Mobile interface documentation](https://github.com/RUCbookshelf/mubi-reviewflow/blob/v2.1.0/docs/MOBILE_INTERFACE.md)
