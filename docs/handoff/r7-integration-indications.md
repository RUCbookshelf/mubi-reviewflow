# r7 集成轮复审——大项指示（按单元归属，交产品仲裁/后续轮落实）

本文档登记 2026-10-28 集成轮复审中**未在本轮直接修复**的大项：涉及产品口径
仲裁、跨轮工程量或需真实浏览器/线上环境，按铁律 R5「修不动如实停下升级」
如实列出，不硬凑。本轮已落地的小项见各提交说明（2d358cb..3d0ad52）。

## 去重与键回归

### 大项 D-1：模糊候选对数上限的产品口径（r5 #4/#10）
- **现状**：找回的 r6-regress 链已含并查集生成树去重＋50000 对上限
  （`coscreen/dedup.py` 模糊层，3537fb8）；复审实测 n=300 同模板标题
  fuzzy_pairs=44,850（=n(n-1)/2）、0.28s、峰值 8.8MB——上限存在但
  「达到上限后是截断、采样还是降级为分块只报 top-N」是产品口径问题。
- **需仲裁**：超限时对用户的呈现（截断告警的文案与建议动作）、是否按
  来源/时间分窗降低对数、以及 50000 这个常量是否随文献量档位分档。
- **落点**：`coscreen/dedup.py` 模糊层 + `coscreen/cli.py` 提示文案。

### 大项 D-2：r5 #9 的两处文案与语义对齐（低危遗留）
- **现状**：库内冲突成员改名到组根键而非加后缀（`stabilize_conflicting_keys`
  改名循环对「与库内冲突」分支仍用组根键）与「键消歧 N 处（…DOI/标题证实
  并非同一文献…已加确定性后缀）」提示语在无 DOI/标题冲突证据场景的两处
  不符。批内路径已被解析层降级修复覆盖（对账用例钉住），库内冲突路径
  无批量真实场景钉，改动需先构造跨批夹具。
- **指示**：改名循环对库内冲突分支改为「成员键 + 确定性后缀」（与批内
  分支同法），提示文案按实际证据分支拆分（有 DOI/标题证据才说「证实
  并非同一文献」）。补跨批真实 CLI 断言（lib.ris 预置 + grp.ris 导入）。

## 文献解析器

### 大项 P-1：编码判据的残余不可判定类（如实声明）
- **现状**：新判据（双候选择优）解决了繁体误拒、Größe、生僻字、损坏
  字节、UTF-16 五类实测形态；**残余**：ASCII 主体 GBK 文件中含 ≥2 个
  扩展区汉字（罕见形态）时仍会择优 cp1252 并附「若应为中文请转存 UTF-8」
  告警——「单 GBK 扩展区字符 + 全 ASCII 上下文」与「cp1252 重音字母 +
  ASCII」在字节层不可判定，属信息论残余，非缺陷。
- **指示**：若产品要求消除，需引入字符频率先验或 n-gram 语言模型（工程
  量大、误判面需重新评估）；当前以告警文案兜底（已如实告知转存动作）。

## 协作权限

### 大项 C-1：R6-C2 属主修复入口（legacy 死任务的产品级恢复路径）
- **现状**：本轮已收口 create_task 保留名拒绝、ghost 目录回滚、文案统一；
  纯 server 部署的**存量** legacy 任务仍只能「临时切 desktop 恢复」
  （409 文案如实指引）。handoff 指示的 claim/adopt CLI 或管理端点未建。
- **需仲裁**：入口形态（CLI 子命令 / 属主认证端点 / 运维脚本）与「属主
  身份如何核验」（本地数据目录所有权？账号体系绑定？）。
- **落点**：`custom_backend/main.py` 新端点或 `coscreen/cli.py` 子命令；
  验收=纯 server 部署可完成 legacy 任务的属主迁移/删除/改名。

### 大项 C-2：F9 team 口径的产品裁决（任务页 vs CLI）
- **现状**：reviewer 口径已对齐（同库相等，本轮落地）；team 模式任务页
  只计全员一致/已仲裁记录、CLI 计「至少一人有实际决策」——两数在部分
  已筛/有分歧时不等（`prisma_task_metrics.outcomes` team 分支 vs
  `prisma_from_merge`）。
- **需仲裁**：统一到「最严已决」还是「至少一人已筛」，或任务页呈现两
  个口径并标注；裁决后改 `outcomes` team 分支并补同库断言
  （ScreenedCaliberAlignmentTests 的 team 用例）。

## 上传与路径安全

### 大项 U-1：R6-S1 反代拓扑语义登记（产品仲裁/risk-accepted）
- **现状**：按用户批示「回退到修复前语义」——应用层按直连对端分桶、
  XFF 一律不参与；本轮补齐传输层前提（start.sh --no-proxy-headers，真实
  uvicorn 下轮换 XFF 不再改写计数维度）与「只计失败＋成功清零」。
- **残留（需登记 risk-accepted）**：反代部署下应用层对端恒为反代本机，
  5 次/分钟为全站合计桶——真实按客户端 IP 的限流完全依赖 nginx
  limit_req（UBUNTU_SERVER.md 已给完整配置，本轮收窄到 login/register
  两个精确 location）。若反代层配置缺失，存在未认证客户端 5 请求/分钟
  连坐全站登录的 DoS 放大面（r5 #42 场景）。
- **指示**：产品登记该取舍（或后续轮为应用层增加可信代理网段配置项，
  仅对可信反代解 XFF 第一跳）。

### 大项 U-2：R6-S4 出货前端换票流的浏览器行为用例
- **现状**：静态字符串守护已在 r6-upload 链（pytest 内不可驱动浏览器）；
  async connectEvents、票据失败路径、onerror 15s 重连未被执行过。
- **指示**：在浏览器门禁三套件之外新增 `sse_ticket_browser.cjs`
  （Playwright：登录取票→EventSource 连接→票据过期→onerror 重连→断言
  URL 不含 24h JWT），随门禁一并跑；参考 `workflow_draft_guard_browser.cjs`
  的实例隔离手法（report_evidence_instance.py 起隔离实例）。

### 大项 U-3：真实 uvicorn/反代联测（部署验收）
- **现状**：S1/S2/S5 的应用层断言全绿；真实 uvicorn proxy_headers 行为、
  nginx limit_req 与应用层 429 的叠加语义只有静态配置核验（本机无 nginx，
  `nginx -v` exit 127）。
- **指示**：部署验收清单加「真实 uvicorn + nginx 拓扑下：轮换 XFF 六连
  登录、/api/auth/me 高频刷新、删除进行中列表读」三项手测，纳入发布前
  检查单。

## PRISMA

### 大项 F-1：prisma_two_stage 的 resolved1 入参
- **现状**：stage1 经仲裁纳入的键不在键域（docstring 已如实声明，越域
  按 warning 忽略）；扩展 `resolved1` 参数需同步 prisma_task_metrics 的
  team 仲裁数据流，属跨模块工程。
- **落点**：`coscreen/stats/prisma.py` 加 `resolved1: pd.DataFrame | None`，
  键域 = 一致 include ∪ resolved1 include；补「stage1 仲裁纳入 + stage2
  agree」端到端断言。

### 大项 F-2：R14/R10W 等新词条的翻译复核
- **现状**：12 条模板已登记十语言（静态守护钉住行存在性与列数）；译文
  由集成轮按领域惯例撰写（医学统计术语），未经各语种母语评审。
- **指示**：发布前安排一轮译文复核（沿用既有词条评审流程），重点
  「脚注**」「资格评估」「推断口径」等术语与既有词条的一致性。

## 门禁与验证

- 全量 pytest 门禁：本轮结束前在集成树复跑（仅本会话，见提交说明）。
- 浏览器三套件（guard/ux_high/report_evidence）：对隔离实例复跑全绿
  （实例启动/收尾手法同 r6 门禁记录）。
- 顺序依赖既有 flake（与本轮无关，基线同序复现）：
  `test_collaboration_hardening.py::test_u4p2_existing_colliding_account_login_gets_guidance`
  在「r6_undo+workspace_undo+collab+import_actions」子集顺序下 users.username
  撞名——全套顺序不触发；修法建议：u4p2 用例用例级清理 INSERT 的
  'collaboration' 行（本文件内已知，未动以避免混入本轮范围）。

---

# r8 复审追加（2026-10-28 第二批）——本轮已修与如实登记

## 本轮已落地（提交见 git log：ac062b6 起两批）
- #30 owner-as-actor 轮次表 500 回归；#34 四处锁键规范化；#41/42/48 限流
  双维重构（peer 原子占位＋账号失败桶＋成功仅退本次）；#43 urlencoded
  表单洪水解析前 415（鉴权 401 优先、删除互斥 409 外层优先）；#44 part
  超限真实端点 413；#45/47 六语种启动命令补 proxy_headers=False＋旧
  location 残留修正；#49 F9 v2 回退 db-only（导入表单 R2/R3 恢复）；
  #11 GB2312 一级字保持 GBK（常见中文名不再误判 cp1252）；#16 尾位两字
  母缩写豁免（'Smith ja'→smith，'John Wu'→wu 保持）；#2 hash/content 层
  年份门（跨文件年份异→新行＋警告，同文件 _fallback_id 层 FB3 保持）；
  #1 adoption 保留库内非空字段＋显式警告。

## 登记：deferred / 产品仲裁（本轮未修，按 R5 如实升级）
- **D-组**：#3/#4/#8 姓名完整改造（点拆分/中间名/姓助词 given 剥离/Jr-
  Sr-III 后缀）——已在 normalize 落尾位豁免与两级门，完整表驱动改造涉
  键公式联动（R1 验收面），归下一轮姓名专项；#5/#6-Web/#15 Web
  _stabilize 内容分层＋注入冲突门；#7 ID/AN 候选优先级遍历；#9 R6-D6
  分隔符歧义（维持 risk-accepted，DOI/作者门兜底实测共存）；#10 R6-D7
  CLI --report/--strict-candidates。
- **P-组**：#12/#17 GBK→合法 UTF-8 直通静默乱码族（'郑爽'→'֣ˬ'，9.2%
  CJK 基本区在族内；需 CJK 罕见字/Latin-ext 混排启发式告警，登记为
  U2-P11 残余类）；#13/#19 嵌合多重告警定位（open_line/ty_count 推进）
  与拆分后告警重算；#14 前端 parse_warnings 消费（API 字段已返回）；
  #18 Shift-JIS 从响亮拒绝变 GBK 乱码导入（rare 档放宽副作用，窄面）。
- **U-组（撤销）**：#20 no-op 重放假成功（form_requests 摘要无 journal
  可重放 200）；#21 save_decision/save_stage2 幂等（跨秒 no-op 假撤销
  条目）；#22 trace can_undo 文件存在性；#23 edit→revert digest 时间戳
  残留（#23 核销注记补登记：备注 A→B→A 后纯时间戳差阻断 screen 撤销，
  属已知残余）；#24/#25 R6-U3 attached 复位＋undone 排除；#26 R6-U4
  latest 只读连接＋存在性预检；#23-U5 守卫预判；#29 前端 dirty 门禁。
- **C-组**：#31/#40 R6-C4 desktop 损坏库「可管理→可见+指引」保守化偏差
  与 server 503 文案（永久损坏区分）；#32 create_task 409 的 message 键
  ＋词条登记（含 503 三条）；#33 撤销仅在全无 grant 时回收 screeners＋
  db_files 兜底呈现；#35 is_owner/is_participant/revoke_role 缺 task_id
  保守化；#36 sync_catalog 存量中毒 ValueError 500 收敛（409+指引）。
- **F-组**：#50 F5 冲突重复键（include+exclude 同键）首条口径；#51
  unscreened/identified 桶的 multi/two_stage/DOT/summary 统一；#52 其他
  方法列行级 i18n；#53 R10W 正则锚定不全；#54 F8/F9 docstring 同步；
  #56 R5 db 列余量词条；#57 report=None 勾稽告警不可达；#58 prFriendlyMsg
  case 'R8' 无条件改写（存量）。
