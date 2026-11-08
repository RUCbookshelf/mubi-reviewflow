# 撤稿/更正关注（Retraction Watch 检查）— 行为与帮助源

2.1.5 W9。对**已纳入**的文献按 DOI 到 Crossref 查撤稿 / 编辑关切 / 更正记录，
把受影响的条目在列表和复筛视图里打上 ⚠ 徽标。

本文件是**应用内帮助文案的唯一来源**（设计 §四 B5）。界面词条与帮助页由前端按
`out/release_plan/drafts/frontend_contract_2.1.5-w9-retraction-watch.md` 实现；
后端只回结构化 `message_key`，不携带任何用户可见长文案。

## 它做什么

- **手动触发**：负责人（属主 / can_manage）在文献列表或复筛工具区点
  **「检查撤稿/更正」**，选范围（默认 **复筛纳入**，可选 **初筛纳入**）。
  系统不会自己跑：没有定时任务、没有后台常驻、关掉页面就停。
- **分批推进**：每次请求检查 50 条，返回进度和游标；前端连续调用直到完成。
  断网后带上游标可以从断点继续，不会重复请求同一批。
- **单次上限**：一次运行最多检查 **300 条带 DOI 的条目**；超出时结果里会明确
  告知「另有 N 条未检查」，并给出**下一段的起点**——点「继续检查剩余 N 条」
  即可查完剩下的条目（普通「检查」按钮只会重新从第 1 条开始）。
- **结果 = 提示，不是判决**：只加徽标、只列发现清单。
  **系统不会自动剔除任何文献，也不会修改任何筛选决定。**
- **留痕**：每次运行单独记一条 run（范围、时间、触发人、命中/无法确认条数）。
  重复运行会产生新的 run，历史随时可查，旧记录不会被改写。
- **重复题录**：两条文献录了同一个 DOI 时，同一批内 Crossref 只查一次（两条
  恰好被排进不同批次时会各查一次，结果相同），但**两条都会**打上徽标
  （漏掉警示比重复更糟）。

## 覆盖局限（请务必转达团队）

- **「未发现」不等于「没有」。** 本检查只反映 Crossref **当前收录**的撤稿/
  更正记录。出版方没有上报给 Crossref 的撤稿、以及 Crossref 尚未同步的最新
  更新，都查不到。结果干净不代表文献一定没问题。
- **只覆盖有 DOI 的文献。** Crossref 按 DOI 索引；没有 DOI 的条目无法检查，
  本次计入「无法确认」，**不会**用标题做猜测匹配（误报比漏报更害人）。
- **更新关系的分档**：撤稿类（retraction / 部分撤稿 partial retraction / 撤下
  withdrawal / 移除 removal）标红并排在最高；编辑关切（expression of concern）
  标橙；更正与勘误（correction / erratum / corrigendum）标灰。
  **其它更新关系不会丢**：Crossref 给出但不在上述清单里的类型（如 addendum、
  clarification、新版本）也会列出来，按最低档灰显示——宁可多显示一条，也不
  漏掉一条撤稿。
- **同一条文献同时有撤稿和更正时，徽标按撤稿（红）显示**：晚出现的更正不会
  把撤稿「降级」成灰色。展开发现在 hover/详情里仍能看到全部类型。
- **数据有时滞。** Crossref 的更新记录由出版方维护，可能滞后于出版方网站的
  公告；发现异常时请以出版方原始公告为准。
- **撤稿不等于该研究必须剔除。** 保留撤稿原文做敏感性分析是完全正当的做法。
  要不要剔除、剔除后怎么写进报告，由团队判断并在方法学部分说明。

## 隐私与出站

- 出站只到 **Crossref**（默认 `https://api.crossref.org`；自托管镜像可由负责人
  在 PDF 获取设置里配置同一 `crossref_base_url`）。
- 出站请求**只有 DOI**（题录标识符）——不发送标题、摘要、全文或任何批注；
  **即使 PDF 获取设置里填了联系邮箱，也不会随撤稿检查的请求发出**。
- 短超时（15 秒）、拒绝跟随重定向、响应 2 MiB 读取上限；两次请求之间留短间隔
  以遵守礼貌访问。
- **单条失败不影响整批**：某个 DOI 超时或上游报错时，该条标为「无法确认」，
  其余条目照常出结果，绝不会让整个页面报错。

## 权限

| 动作 | 谁可以 |
|---|---|
| 触发检查（`POST …/retraction-check`） | 任务属主 / can_manage |
| 查看历次运行、运行详情、徽标 | 任务全部参与者（团队成员都该看到警示） |
| 非任务参与者 | 403 |

> server 模式下，创建于协作机制之前的 legacy 任务（没有属主记录）会被既有的
> 协作属主闸门直接拦下（409「旧任务缺少协作属主记录」并给出迁移指引），
> 这是升级前任务一贯的行为；单机（desktop）模式的 legacy 任务维持原有的
> 可管理性。

## 错误与状态键

后端只回 `{"error_code", "message_key", "params"}`，界面文案由前端词条渲染：

`retraction.scope_invalid` · `retraction.cursor_conflict` ·
`retraction.cursor_invalid` · `retraction.task_forbidden` ·
`retraction.owner_required` · `retraction.run_not_found` ·
`retraction.run_in_progress` · `retraction.scope_mismatch` ·
`retraction.cursor_stale` · `retraction.legacy_owner_unset` ·
`retraction.storage_unavailable` · `retraction.check_progress` ·
`retraction.check_done`

披露语键（原文见契约 §五）：

`retraction.disclosure_not_found_is_not_none` ·
`retraction.disclosure_no_doi_unavailable` ·
`retraction.disclosure_no_auto_removal` ·
`retraction.disclosure_crossref_coverage`

## 数据落在哪里

任务目录下协作库 `collaboration.db` 的两张表：

- `retraction_runs`：每次运行一行（范围、状态、进度、命中/无法确认条数、触发人、
  起止时间、本次目标清单）；
- `retraction_findings`：发现行（文献键、DOI、更新类型、通告 DOI/日期/标题/链接）。

两张表都是**追加式**：旧运行与其发现不会被后续运行改写。降级到旧版本程序时
这两张表被忽略、数据保留；升级回来即可继续查看。

**不写筛选员库、不改任何决定。** 检查前后 `decisions` / `stage2_decisions`
逐字节不变（后端钉子覆盖）。
