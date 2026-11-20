# 旧团队库升级（team-ledger bootstrap）前端规格

日期：2026-11-15。文档性质：**规格交接，非实现**。`custom_frontend` 全程只读未写入；本文是后续前端实现线的逐组件/词典键/验收规格。

## 0. 状态边界（先读，不得违背）

1. **后端 bootstrap API 为 S2 待实施**：`POST /api/tasks/{task_id}/collaboration/team-ledger/bootstrap` 是已裁定候选合同（`team_flow_integration_design.md` §12.3，前端部分被 §13.7 覆盖），**当前仓库无此端点**（已核实 `custom_backend/` 无 team-ledger 路由；`install_in_db` 仅有 S1a ledger 模块内实现、无 custom_backend 消费者）。S1a 账本模块（`coscreen/team_vote_ledger.py`，format 2）已独立 Spec/Quality/测试 PASS（`out/maintenance216/review_team_vote_ledger_s1a_sol.md`）。
2. **UI 升级入口未交付**：`custom_frontend` 现无任何升级入口/升级提示/逐成员结果组件（已核实前端零 `team-ledger` 引用）。UI 实现前**不得声称旧团队流程升级路径已完整可达**（§13.7 明文；撤销"API 即完备"表述）。
3. **本文全部响应字段、状态枚举、词典键均为 CANDIDATE**，最终 DTO 与键名在 S2 冻结；前端实现线不得把本文当已冻结合同直接编码，S2 冻结后以冻结版为准。
4. 本文仅新增 bootstrap 这一个端点、不新增权限策略（C8 对既有已冻结合同端点如 `GET /collaboration/business-role` 的复用不属于新增）。
5. 引用依据（真实存在文档）：`out/maintenance216/team_flow_transaction_ruling_sol.md`（§旧库升级的产品可达性、§安装自愈分层）；`out/maintenance216/team_flow_integration_design.md` §12.3（被 §13.7 覆盖的前端部分以 §13.7 为准）/§13.7/§15；`out/maintenance216/team_vote_ledger_conservation_ruling_sol.md`；冻结 `docs/maintenance/TEAM_FLOW_ROLE_FRONTEND_HANDOFF_20261114.md`（下称"角色交接"）；`docs/maintenance/OWNER_BUSINESS_ROLE_DECISION_20261114.md`；`docs/maintenance/TEAM_FLOW_REPAIR_CONTRACT_20261114.md`。

---

## 1. 已核前端现状（只读事实；行号为当前 index.html/team_roles.js 实测）

实现线必须基于这些真实锚点，不得编造不存在的组件 ID。

| 锚点 | 位置 | 事实 |
|---|---|---|
| `#teamPanel` | index.html:1016-1026 | 团队进度面板卡片，含 `#teamTitle`/`#teamStorage`/`#teamList`/`#teamInvite`（`#teamInviteName`/`#teamInviteRole`/`#teamInviteBtn`）/`#teamMessage`（role=status） |
| `refreshTeam()` | index.html:6080-6111 | 数据源 `GET {taskApi()}/team-progress`；**server-only**（:6082 `panel.hidden = runtimeMode !== 'server' || !S.task`）；按 `renderSignature` 跳过重渲染；`can_manage` 控制 `#teamInvite` 显隐与行内管理动作 |
| `teamMemberIdentity()` | team_roles.js:2-19 | 成员身份徽标 `.team-identity` + `.team-role-badge role-owner/coder/reconciler`（纯 SVG 徽章组件，角色标签为主指示） |
| `teamMemberActions()` | index.html:5930-6005 | 属主侧成员操作菜单 `.team-member-actions`；菜单 `#team-member-menu-{user_id}`；项：`.team-change-role` + `.team-role-options`（coder/reconciler 单选）、`.team-screening-toggle`（reconciler 参与开关）、`.team-remove` |
| `mutateTeamMember()` | index.html:6153-6168 | **单飞锁** `teamMutationTaskId`（同时刻仅一个成员变更在途）；动作期间禁用 `#teamPanel` 全部按钮；完成后 `loadTasks()+refreshTeam()`（部分失败也重载）；失败写 `#teamMessage`，成功 `toast()` |
| `changeTeamMemberRole()` / `setMemberScreening()` / `removeTeamMember()` | index.html:6112-6152 | 分别调 `POST /collaboration/roles`、`PUT /collaboration/members/{id}/screening`、`DELETE /collaboration/roles/{uid}/{role}` |
| SSE `connectEvents()` | index.html:6015-6079 | server 模式；监听 `task-workflow-changed`/`tasks-changed`/`task-ownership-changed`；**无 `team-role-changed` 监听**（角色交接 §5 已规格，属下一批）；`tasks-changed` 目前按 `task_name` 匹配（SSE 规格转向按 `task_id`） |
| 词典机制 | index.html:5313-5332 | `SERVER_MESSAGE_SOURCES`（key→规范中文源串）+ `window.RFRegisterServerMessages`（增量注册）+ `window.RFServerMessage(key, params)`——**精确 key 查找，未注册返回 null**（绝不把 error_code 当展示串） |
| 错误渲染回退 | index.html:5503-5524 | `RFServerMessage(payload.message_key, params)` → 未命中回退 `payload.message/detail/details` 走 `T(source)`；`error.code`/`error.errorCode`（即 `error_code`）始终保留在 ApiError 上供程序化分支 |
| 十语言注册模式 | interface_locale.js（如 :6095-6102 团队会话族） | `Object.assign(["中文","英文",…8 语…], {aliases:['team.xxx']})` → `RFLang.register(rows)`；index.html 对应 `<script src="...locale.js?v=令牌">`，改词典必 bump `?v=`（现值如 `interface_locale.js?v=20261110-arbitration`） |
| 后端现状 | custom_backend/main.py:2740 | `GET /team-progress` **server-only**（desktop 404"团队进度仅在服务器模式下开放"）→ 桌面端成员管理入口无现存面板，属 planned |
| 前端零消费 | 全 custom_frontend 检索 | `active_screeners`、`business-role`、`team-role-changed`、`team-ledger` 均**无任何前端代码**（角色交接为规格、未实现） |

---

## 2. API 契约候选（S2 冻结；全部 CANDIDATE）

### 2.1 端点

`POST /api/tasks/{task_id}/collaboration/team-ledger/bootstrap`

- 权限：owner-only（挂 `_collaboration_owner_store(desktop_team_ok=True)` 属主闸）；持 canonical task RLock。**server 与 desktop team 任务均可达**；不要求 owner 先选业务角色；不受 chain/角色门反锁（管理配置面，非业务写）。
- 行为：锁内现读当前 roster → 对每个**现存**成员库 `{slug}.db` 逐库安装账本（seed=1，**不改任何票**）；缺失库**跳过、绝不创建**；**幂等可重跑**；不清理历史残留；broken 库**不自动修复**（含 format 1 不原 ID 升级）。
- 结果逐成员（CANDIDATE，S2 冻结最终形状）：

```
{ "results": [ { "user_id": int, "username": string|null,
                 "state": "installed" | "already" | "absent_db" | "failed",
                 "reason": string|null } ],        // 仅 failed 携带；账本分支=REASONS 七枚举，其他故障=独立 error_code/分类（CANDIDATE，S2 冻结）
  "summary": { "installed": n, "already": n, "absent_db": n, "failed": n } }  // CANDIDATE
```

- `state` 语义（§12.3 原文）：`installed`=本次新装成功；`already`=幂等命中已装；`absent_db`=成员库文件**确证不存在**（跳过不建；只有确证缺文件才可用此态）；`failed`=本次失败。`failed` 的分类字段**不限于** `coscreen/team_vote_ledger.py::REASONS` 七枚举——七枚举仅覆盖 `VoteLedgerUnavailable`（账本）分支；SQLite busy/Permission/IO、成员身份无法解析等其他故障需独立 `error_code`（或同等分类字段，CANDIDATE，S2 冻结）明确上报，**不得伪映射为 `overflow`/`unmigrated`，不得吞为空结果/跳过**。分类→呈现映射见 C7 表，词典键见 §4。`username` 为 null（账号缺失）**行保留**——不屏蔽缺账号的合法旧身份（角色交接 §2/§8.7 同口径）。
- 预期失败面（复用既有门，不新增文案族）：403 非属主（既有管理门文案）；409 legacy owner 未登记（既有 `LEGACY_OWNER_UNSET_MESSAGE`）；503 协作库不可读（既有 503 族）；400/422 形状错误按既有。**部分成功不是 HTTP 失败**：只要有逐成员结果即 2xx，由前端按 `summary` 呈现部分成功。

### 2.2 成功后的刷新通知（S2 必交付）

成功（含部分成功）后按既有 SSE 面广播任务变更，事件载荷携带 **canonical `task_id`**（§5.3：`notify_tasks_changed` 增可选 `task_id`；前端按 `task_id` 匹配、不按任务名）。前端据此刷新任务卡/队列/团队面板。

---

## 3. 前端逐组件规格（清单一）

标注 **[现有]** = 基于上文已核现存锚点；**[planned]** = 现前端无对应组件，按本规格实现，不得假称既存。所有新组件不得从"有无票"反推职责，不全局过滤 screener 名单，不做 A7 自动清理，不动 generic history/CSV/离线导出。

### C1. Owner 管理入口（升级动作按钮）[planned，宿主区域现有]

- 位置：成员管理区（`#teamPanel` 内属主管理区；server 模式现存面板）。桌面端宿主见 C8。
- 显隐：仅 `can_manage`（属主）可见；普通成员不可见（其视角为 C6）。
- 行为：点击**直接**调 bootstrap（§2.1）→ 进入 C3 结果呈现——**无确认弹窗**（bootstrap 幂等且不改票，不设 approval 流；`in_progress` 状态反馈保留）。**禁重复点请求**：复用 `mutateTeamMember` 同款单飞锁模式（新增独立 in-flight 守卫或并入既有 `teamMutationTaskId` 机制），动作期间禁用面板全部按钮（现实现 :6156-6157 已禁 `#teamPanel` 全按钮，沿用）。
- **属主未配业务角色（coder/reconciler 二选一）仍可执行升级**：管理入口的可用性只取决于属主身份，不取决于业务角色配置状态（OWNER_BUSINESS_ROLE_DECISION 规则 1/4：管理身份不代替业务角色，业务角色缺失只标记"需要配置"，管理功能仍可用）。入口不得因角色未配置而隐藏或禁用，也不得把升级按钮与业务角色单选（角色交接 §5）捆绑成一个流程。
- 文案：平实语言"升级任务流程信息"（§4 词典）；不出现 SQLite/ledger/UUID/账本等技术词。

### C2. 未升级提示（横幅/警示）[planned]

两条互斥视角：

- **属主视角（先分类，不说一律"需升级"）**：业务流返回 503 `team.flow.revision_unavailable`（`VoteLedgerUnavailable`，params `{member, reason}`）时按 C7 表分类呈现——`unmigrated`（可升级）→"本任务的团队流程信息需要升级"+ C1 入口引导；broken 族与 `overflow`（等待不自愈）→"团队流程信息需要负责人处理"指引（broken 另按 C4 修复指引），**不得呈现为"再升级一次即可"**；其他暂态故障（SQLite busy/IO 族，非账本 error_code）→ 沿既有 503 重试语义提示。reason 仅程序化分类（C7 表），不直接展示给用户。
- **普通成员视角**：同错误显示"任务流程信息尚未准备完成，请联系任务负责人处理"（无入口、无重试、无技术细节）。**后端不能只返 503 而称正常流程可达**（§13.7）——本组件就是该产品限制的用户侧呈现。
- 触发判定用 `error.errorCode === 'team.flow.revision_unavailable'` 程序化匹配（错误渲染回退链已保留 errorCode，index.html:5524）；不依赖 messageKey 已注册（未注册时 RFServerMessage 返回 null，UI 仍须正确分支）。
- 提示不得基于"该成员有无票/有无进度"推断（不从数据存在性反推职责）；缺账号成员（username null）照常出现在提示涉及的成员范围中。

### C3. 升级进度与逐成员结果 [planned]

- 触发：C1 动作完成后，在成员管理区（`#teamList` 之上或属主菜单展开层）渲染逐成员结果表，行 key= `user_id`（username null 行保留，显示既有"未知账号"兜底样式）。
- 每行状态（CANDIDATE）→ 平实文案：
  - `installed` → "已准备"
  - `already` → "本已是最新"（幂等命中，不是错误）
  - `absent_db` → "该成员暂无任务数据，已跳过"（**不创建**；不渲染为失败）
  - `failed` → 先按 C7 表分类再给文案：`unmigrated`/可证暂态 → "暂时无法处理，可重试"；broken 族 → "该成员的数据需要修复"+修复指引；`overflow` → "需要负责人按恢复流程处理"（无重试暗示）；其他非账本 error_code → 按其分类键呈现（CANDIDATE）。reason/error_code 明细一律不进产品文案（技术附录 §6）
- 汇总行：`summary` 计数（"已准备 X 项 · 需处理 Y 项"）。
- 与角色交接 §5 的"管理徽标（属主）与业务徽标分离"一致：升级状态不得混入 `.team-role-badge` 业务角色徽标渲染（`teamMemberIdentity` 不改动语义）。

### C4. 失败分类与重试 [planned]

- **先分类后出动作**：重试动作只在存在"可重试/未迁移"类 failed 行时显示（`unmigrated` 与可证暂态故障）；broken/overflow 类行不进重试文案与重试范围，给明确处理指引，不诱导反复升级。
- 可重试类：整体重试再次调同一 bootstrap（幂等；已 `installed`/`already` 的行返回 `already`，无副作用）。按钮显示与可重试提示仅由 `unmigrated`（未装、装即成）与**可证暂态**故障（SQLite busy/IO 占用族，非账本 error_code）决定。请求仍由后端按当前完整名册处理，客户端不提交或筛选成员清单；其他 broken 行仍须重新验证并如实报告 failed，不因整体重试而修复。
- **broken 类（`meta_corrupt`/`trigger_missing`/`row_missing`/`conservation_mismatch`）的重试行为如实**：重试仍将 failed——UI 不隐瞒，但**不得把重试包装成"修复"**：对 broken 行显示"需要修复"指引（指向负责人按 `TEAM_FLOW_REPAIR_CONTRACT_20261114.md` 流程处理），**不得诱导反复点击升级来"修好旧身份"**（transaction ruling §旧库升级的产品可达性；conservation ruling：broken 不得以原 library_id 自动补好，将来修复须单独授权+备份+轮换 ID）。
- **`overflow` 不是瞬时失败**：整数 counter/revision 到上限，等待不会自愈——与 broken 同类处理："需负责人按独立恢复流程处理"指引；**不给"稍后重试"文案、不进重试动作范围**。
- `absent_db` 行无重试语义（不创建缺席库是正确行为，提示语明说"已跳过"）。

### C5. 升级后刷新 [planned]

- 请求成功返回（含部分成功）后：沿用 `mutateTeamMember` 收尾模式 → `loadTasks()` + `refreshTeam()`（即使部分失败也重载，现实现 :6163-6164 同款）。
- 同时依赖 SSE（§2.2）：监听 `tasks-changed`（载荷含 canonical `task_id` 后按 `task_id` 匹配刷新，不再按任务名；角色交接 §1 SSE 同规则）；跨端同步：其他成员/其他标签页收到事件后重新拉取任务状态，未升级 503 提示随之收敛消失。
- 刷新后队列/计数展示遵循 TSR 资格语义（升级成功的成员不再触发 `revision_unavailable`；仍缺席/损坏的成员按缺票或 503 呈现，**不伪造全员就绪**）。

### C6. 普通成员视角 [planned]

- 无 C1 入口、无重试、无逐成员结果；只看到 C2 成员视角提示与既有业务横幅。
- 任何 `team.role.*`/资格门错误映射继续按角色交接 §3 表（本规格不改动）。

### C7. broken 与 unmigrated 的区别呈现 [planned，程序化]

| reason（后端七枚举，实测 `coscreen/team_vote_ledger.py:66-74`） | 用户侧分类 | 呈现 |
|---|---|---|
| `unmigrated` | 可升级 | 升级入口正常覆盖；升级后该成员变 `installed` |
| `meta_corrupt` / `trigger_missing` / `row_missing` / `conservation_mismatch` | broken（需独立修复） | "需要修复"指引（C4）；**禁止**暗示重试可修复；不得重复升级重建原身份 |
| `overflow` | 需独立恢复（计数/版本到上限，等待不自愈） | "需负责人按恢复流程处理"指引；不给重试文案、不进重试范围 |
| 其他非账本故障（独立 error_code，CANDIDATE：SQLite busy/Permission/IO、成员身份无法解析等） | 按分类：仅可证暂态可重试 | busy/IO 族→"暂时无法处理，可重试"；身份无法解析→`failed` 明确上报（不得当缺席/跳过）；Permission/持久 IO→负责人处理指引 |
| `path_absent` | 对应 `absent_db` 态 | "暂无任务数据，已跳过"（不创建） |

- 读路径 503（未升级提示）与升级结果里的 failed 共用此映射；`reason` 原文一律不进产品文案，仅程序化分类 + 技术附录（§6）。

### C8. Server / Desktop 能力正交 [planned]

- 后端事实：bootstrap 按 §12.3 挂 `desktop_team_ok=True` 属主闸——**双模式可达**；但现 `GET /team-progress` 为 server-only（main.py:2740），现 `#teamPanel` 仅 server 模式渲染（index.html:6082）。两者是**正交事实**：不得因桌面端无团队进度面板推断桌面端不可升级，也不得把 server 面板假设搬进桌面端。
- Server：C1-C5 挂现有 `#teamPanel`。
- Desktop team：现无成员管理面板 → **planned** 成员来源用角色交接 §1 的 `GET /collaboration/business-role`（已冻结合同，§8.4 明确 desktop team 可达；其行含 `user_id/username/is_owner/role`）渲染管理入口与逐成员结果；bootstrap 与业务角色单选同为管理面、互不依赖（C1）。desktop 非 team 任务维持 404 现状，不新增提示。
- 前端能力探测按模式+任务种类分支，不通过"有没有票/有没有库"猜测。

### C9. Owner 业务角色与管理分离（沿用冻结合同，本规格只声明边界）

- Owner 业务角色仍须 coder/reconciler 二选一（角色交接 §5 单选 + OWNER_BUSINESS_ROLE_DECISION 规则 1），配置 UI 不变。
- 升级管理动作**不要求**先完成业务角色配置；管理动作**不绕过**业务流程（不代筛、不代裁、不改票、不触发任何资格终局——bootstrap 只装账本，seed 不改票）。
- 二者徽标/入口分离；升级结果不写入业务角色行。

### C10. 明确不做（红线）

1. 不从有无票/有无库反推成员职责或显示职责徽标。
2. 不屏蔽缺账号（username null）的合法旧身份成员；结果行/提示范围保留。
3. 不全局过滤 `screener_options`/`load_screener_decision_packages`/generic latest/history/CSV/离线导出（screen 阶段无逐条门；旧名单兼容原义）。
4. 不做 A7"无 grant 有 db 即收敛"自动清理；不清理历史残留库；撤销/隔离幂等行为零改动。
5. 不提供任何"重建原身份/轮换 ID/修复账本"的前端动作——修复是独立授权合同（TEAM_FLOW_REPAIR_CONTRACT），本轮不夹带。
6. 仅新增 bootstrap 这一个端点（C8 复用既有已冻结合同端点如 `GET /collaboration/business-role` 合法、不算新增）；不把 bootstrap 结果当作业务资格数据源（资格只读 TSR 派生）。

---

## 4. 词典键候选（清单二之一；不写实际词典、不 bump 版本）

注册方式（候选，沿现机制）：键→中文源串经 `RFRegisterServerMessages`（`team.*` 域）注册（index.html:5321）；中文源串十语言行按 interface_locale.js `Object.assign([...], {aliases:['team.flow.upgrade.*']})` 模式补齐（**十语言一次补齐，不留英文回退**——interface_locale.js FL4 注释既定口径）；词典变更 bump 对应 `<script src>` 的 `?v=` 令牌（格式沿用 `?v=YYYYMMDD-<slug>`；实际令牌值由前端实现线定）。params 只用于定位展示，不做字段名翻译拼接（角色交接 §6 同口径）。

| 键（CANDIDATE） | params | 中文源串（源串=词典主键） | 英文 |
|---|---|---|---|
| `team.flow.upgrade.action` | — | 升级任务流程信息 | Prepare team workflow data |
| `team.flow.upgrade.in_progress` | — | 正在准备团队流程信息… | Preparing team workflow data… |
| `team.flow.upgrade.done` | `{ready:int}` | 团队流程信息已准备完成（{ready} 项）。 | Team workflow data is ready ({ready}). |
| `team.flow.upgrade.partial` | `{ready:int,` `pending:int}` | 已准备 {ready} 项，另有 {pending} 项需要处理。 | {ready} prepared, {pending} need attention. |
| `team.flow.upgrade.all_failed` | — | 本次准备未完成，请查看各成员的处理提示。 | Preparation did not complete this time. Check the hint for each member. |
| `team.flow.upgrade.member_ready` | — | 已准备 | Prepared |
| `team.flow.upgrade.member_current` | — | 本已是最新 | Already up to date |
| `team.flow.upgrade.member_absent` | — | 该成员暂无任务数据，已跳过 | No task data for this member; skipped |
| `team.flow.upgrade.member_retryable` | — | 暂时无法处理，可重试 | Temporarily unavailable; retry allowed |
| `team.flow.upgrade.member_broken` | — | 该成员的数据需要修复 | This member's data needs repair |
| `team.flow.upgrade.broken_hint` | — | 请联系任务负责人，按修复说明单独处理；重复升级不会修复该数据。 | Contact the task owner and follow the separate repair instructions; retrying the upgrade will not repair this data. |
| `team.flow.upgrade.member_owner_action` | — | 需要负责人按恢复流程处理 | Needs the task owner to follow the recovery process |
| `team.flow.upgrade.owner_action_hint` | — | 请联系任务负责人按恢复流程单独处理；重试升级不会改变该状态。 | Contact the task owner to follow the recovery process separately; retrying the upgrade will not change this state. |
| `team.flow.upgrade.retry` | — | 重试 | Retry |
| `team.flow.upgrade.contact_owner` | `{participant:string\|int}` | 任务流程信息尚未准备完成，请联系负责人（{participant}）处理。 | Team workflow data is not ready yet. Contact the task owner ({participant}). |
| `team.flow.upgrade.blocked_hint` | — | 本任务的团队流程信息需要负责人升级后才能继续。 | The task owner must prepare team workflow data before you can continue. |

- `team.flow.revision_unavailable`（**后端已定键**，`VoteLedgerUnavailable.code=message_key` 实测 team_vote_ledger.py:165-166）：当前前端**未注册**（实测 `SERVER_MESSAGE_SOURCES` 仅 3 个 team.* 键）。候选注册：中文源串"团队成员的任务流程信息尚未准备完成，请联系任务负责人处理。"——**不含 `{reason}` 占位**：`RFServerMessage` 对 params 做精确格式化，源串含 `{reason}` 时即便注释声明"不得直出"仍可能把技术枚举渲染出来；params `{member:string|int|null, reason:string}` 仅用于程序化分支（reason→用户侧分类走 C7 表键），格式化文案一律不引用 reason。技术枚举完整清单仅在 §6 附录。是否注册该键由前端实现线按 C2 需要决定（未注册时走 errorCode 分支 + 自有文案，同样合规）。
- `summary`/`state`/`reason` 等字段名不翻译（同角色交接 §6）。
- **防乱注册**：`RFServerMessage` 为精确 key 查找、未注册返回 null（index.html:5330-5331）——实现线只注册上表实际键；行为分支一律优先 `error.errorCode`（回退链 index.html:5503-5524 已保留），不得为分支臆造未在 S2 冻结清单中的 messageKey。过度技术性说明（SQLite/账本/守恒/UUID/revision）一律不进词典，进 §6 附录。

---

## 5. 后端 S2 必须交付清单（清单二之二；前端依赖面，非前端可实现）

1. **端点**：`POST /api/tasks/{task_id}/collaboration/team-ledger/bootstrap`（§2.1 全部行为合同：owner-only、双模式可达、锁内 roster、仅现存库、mode=rw 绝不创建、seed 不改票、幂等、逐成员结果、不清理、不自动 repair）。
2. **owner 锁内授权**：`_collaboration_owner_store(desktop_team_ok=True)` 属主闸 + canonical task RLock 持有至逐库处理完成；不新增权限策略、不借业务角色门。
3. **当前 roster 解析**：锁内现读（`active_screening_roster` 同源），不接受客户端提交名单；username 解析失败成员保留在名册（§10.7），bootstrap 内呈现为该成员 `failed`（明确错误分类，CANDIDATE）并计入 summary.failed——**不得按"跳过"成功处理，无法解析的 required 成员不得被当缺席成功跳过**；只有确证成员库文件不存在才 `absent_db`。
4. **现存路径 mode=rw**：复用 S1a `team_vote_ledger.install_in_db`（实测 mode=rw、文件缺失 `path_absent` 拒绝创建、broken 拒绝自动补齐、幂等返回现值）；TSR/只读面绝不调 install。
5. **部分结果与明确分类**：逐成员 `installed/already/absent_db/failed`；失败/部分成功可重试（重试语义仅覆盖可证暂态与未迁移类）；`failed` 分类字段明确——账本分支为 `REASONS` 七枚举，其他故障（SQLite busy/Permission/IO、成员身份无法解析等）为独立 `error_code`/分类字段（CANDIDATE，S2 冻结），不伪映射七枚举、不吞为空/跳过；最终 DTO S2 冻结。
6. **不自动 repair**：broken（含 format 1）不原 ID 升级、不补触发器沿用旧身份（conservation ruling；§13.6/§15）。
7. **成功刷新 canonical task_id SSE**：成功/部分成功后 `notify_tasks_changed(..., task_id=info.task_id)`（§5.3 可选参数，S4 交付）；事件失败不改业务成功语义（§13.5）。
8. **格式 2**：`LEDGER_FORMAT_VERSION=2`（实测已实现）；守恒校验、七 reason、Python 任意精度加法等按 §15/conservation ruling，本清单不重复。

---

## 6. 技术附录（format2 内部细节；产品文案与词典禁止引用本节术语）

- 账本对象：成员库内 `team_vote_revision_meta`（singleton、format_version=2、library_id 32hex、seeded_stage1/seeded_stage2/advances 非负整数 CHECK）与 `team_vote_revisions`（stage1/stage2、item_key、revision≥1，WITHOUT ROWID），六触发器（decisions/stage2_decisions 各 insert/update/delete）每条 upsert 同事务 `advances+1`（UPDATE 双 upsert +2；meta 缺失 RAISE ABORT）。
- 守恒不变量：全 stage 含墓碑 Σrevision == seeded_stage1+seeded_stage2+advances；同 snapshot 流式 Python 任意精度验证，禁 SQL SUM/REAL；孤立行删除/降值后正常写不可洗白（不依赖先读取发现）。
- reason 精确语义（`coscreen/team_vote_ledger.py:66-74` 实测注释）：`unmigrated`=库未装（absent）；`meta_corrupt`=meta/revisions 结构不符（含 format 不受支持）；`conservation_mismatch`=守恒破坏；`trigger_missing`=触发器缺名或定义不符；`row_missing`=同 snapshot 票行缺 ledger 行；`overflow`=写路径溢出业务映射令牌（S2 写窗口用）；`path_absent`=install 目标文件不存在拒绝创建。
- `install(conn)`：调用方连接不得持未结事务（编程违约 RuntimeError）；单 BEGIN IMMEDIATE、禁 executescript；顺序=meta/revisions 表→seed 现有票 revision1（不推进 advances）→六触发器→最后插 meta 完成标记→同事务守恒验证→commit；任一步失败整回滚无半装态；并发后来者复用首个已提交 library_id；幂等返回前同样完整可信验证。
- broken 边界：外部 DROP trigger 期间写入后手动恢复、协同改 counter、等额补偿、整文件回滚**不在**守恒自证范围（conservation ruling §明确边界）；将来修复须单独授权+备份+轮换 library_id（TEAM_FLOW_REPAIR_CONTRACT 体系）。
- 读路径 503 载荷：`{"detail":{error_code:"team.flow.revision_unavailable", message_key:同, params:{member, reason}, message:…}}`（FastAPI detail 包裹，同角色交接 §7）。

---

## 7. 验收场景（前端实现线 + S2 联调；均需隔离箱，不触真实数据）

| # | 场景 | 断言要点 |
|---|---|---|
| 1 | owner 未配业务角色执行升级 | 入口可见可用；升级成功；业务角色单选状态不受影响；管理不产生任何票/仲裁/资格写 |
| 2 | 普通成员调用/触达 | bootstrap 403（既有管理门文案）；读路径 503 时成员视角提示=联系负责人；无任何升级入口 |
| 3 | 缺库成员 | 结果 `absent_db`；文件系统无新库创建；行呈现"已跳过"非失败 |
| 4 | 票不变 | 升级前后成员库决策行逐字节相等（后端断言）；UI 不呈现任何"数据被修改"暗示 |
| 5 | 部分成功与分类重试 | 有 failed 行时 2xx+partial 提示；重试动作仅出现在可重试/未迁移分类行；重试后已装行= `already`、failed 行按分类收敛；重复点请求被单飞锁阻止（无确认弹窗，直接请求） |
| 6 | broken/overflow 不修不自愈 | broken 四 reason 与 `overflow` 成员反复重试恒 failed；UI 给处理指引（broken=修复指引、overflow=恢复流程指引）、无"稍后重试"文案、无"重试即修复"暗示；旧身份不被重建 |
| 7 | 升级后刷新 | 成功后任务卡/队列/团队面板刷新；此前 503 提示消失；仍缺席成员按缺票呈现、不伪造全员就绪 |
| 8 | SSE canonical task_id | 事件载荷含 canonical `task_id`；前端按 task_id 匹配刷新（同名任务不误刷）；其他成员端同步收敛 |
| 9 | desktop/server 一致 | 同一 team 任务：双模式升级入口均可达且结果一致；desktop 成员来源=business-role GET（角色交接 §1/§8.4）；desktop personal 任务 404 不变 |
| 10 | 幂等 | 重复升级全部 `already`；无重复 seed、票仍逐字节不变 |
| 11 | 未升级读取拒绝 | bootstrap 前（成员未自触）团队流 503 `team.flow.revision_unavailable`；bootstrap 后不再对该团队返此错误——两态同时钉住（§12.3 验收原文） |
| 12 | 缺账号旧身份 | username null 成员出现在结果行与提示范围；不屏蔽、不除名 |
| 13 | format1 拒绝 | 旧 format1 库 `failed`（broken 族 reason）、不原 ID 升级（后端断言；UI 呈现修复指引） |
| 14 | 非账本故障不伪映射 | SQLite busy/IO failed 行呈可重试提示；成员身份无法解析的 required 成员 `failed` 计入 summary.failed（不呈现 absent_db/跳过成功）；Permission/持久 IO 给负责人处理指引 |

---

## 8. 完成边界声明

本文为只写规格文档：未编辑 `custom_frontend`/`custom_backend`/`coscreen`/测试/词典实物；未运行 app/API/pytest/浏览器/网络；未触真实 data/8613/密钥；未 add/commit/push/release。文中前端事实均来自当前工作树静态读取（文件:行号见 §1）；后端 bootstrap 端点、SSE task_id、business-role 前端消费均未实施。S2 冻结 DTO/键名后，以冻结版覆盖本文 CANDIDATE 项。
