# 团队业务角色·前端交接（2.1.6 FLOW 阶段 II-A 角色部分）

日期：2026-11-14。后端契约已实施并隔离验证（`out/maintenance216/team_role_stage2a_report.md`）；
**custom_frontend 未写入**（既定分工），本文是角色这部分后续合并总交接的精确
API/字段/错误/逐组件规格。词典键均需十语言注册（`RFServerMessage` 语料 +
改词典必 bump `?v=` 令牌）。

## 1. 管理端 API（属主管理门 `_collaboration_owner_store`；server 模式）

### GET `/api/tasks/{task_id}/collaboration/business-role`

响应 `{ "roles": [ {...}, ... ] }`，每行：

| 字段 | 类型 | 说明 |
|---|---|---|
| `user_id` | int | 稳定身份 |
| `username` | string\|null | 账号名（未知账号 null） |
| `is_owner` | bool | 属主行 |
| `role` | "coder"\|"reconciler"\|null | 当前**有效**业务角色 |
| `source` | "choice"\|"grant"\|"none" | choice=显式配置；grant=单一授权复用；none=未配置 |
| `config` | "ok"\|"unconfigured"\|"conflict" | 配置状态（conflict=双授权未选择） |
| `grants` | ["coder","reconciler"] | 历史/来源授权（只读展示，不驱动权限） |
| `participant` | bool | 恒 true（choice 不授已移除者成员权，已移除者不出现） |

参与者 = 属主 + 当前 grant 持有者。GET 纯读不建表；库暂不可读 503。

### PUT `/api/tasks/{task_id}/collaboration/business-role/{target_user_id}`

体 `{ "business_role": "coder" | "reconciler" }`。响应 `{ "role": { user_id,
business_role, previous_role, is_owner, set_by, changed_at, username } }`。

- 属主可给自己配；普通成员/非属主 403（既有管理门文案）；目标非当前参与者
  （含未知账号、已移除者）400 `team.role.invalid`（params.participant）。
- 成员切到尚无授权的角色=显式授权（同事务补 grant+审计）；切换不触发撤销/
  隔离/改名，历史授权与意见保留。
- SSE：全部参与者收到 `{"type":"team-role-changed","reason":"role.changed",
  "task_id":"<canonical>","task_name":...}`（按 task_id 识别，勿按任务名）。

## 2. 任务卡投影（GET `/api/tasks` 行内新键 `active_screeners`）

仅 team 任务出现（personal/legacy **无此键**，不得当空名单渲染）；读取失败
时省略键（前端按"暂不可用"处理，勿清零）。每条：

`{user_id, username, slug, is_owner, role, source, required, config}`

- `required`=当前应筛（coder 恒 true；reconciler 按参与开关；未配置/冲突 false）
- `slug`=`{slug}.db` 文件名主干（装载层同源）；username 解析失败为 null（保留 user_id）
- 旧 `screeners` 键（三源兼容名单）**合同不变**，继续用于历史场景。

## 3. 错误合同（业务写门；沿 RoB `{error_code,message_key,params,details,message}` 形）

| 场景 | HTTP | error_code=message_key | params | message（中文源串） |
|---|---|---|---|---|
| team 参与者角色未配置（筛选写/仲裁写/编码提交/评价写/共识写） | 409 | `team.role.unconfigured` | `{participant}` | 该成员的业务角色尚未配置，请先在成员管理中选择编码者或仲裁者。 |
| 双授权未选择 | 409 | `team.role.conflict` | `{participant}` | 该成员同时持有编码者与仲裁者授权，请先选择单一有效业务角色。 |
| 已配置但角色不符该动作 | 403 | `team.role.denied` | `{participant, action, required_role}` | 当前业务角色无权执行该操作。 |
| PUT 目标非法/非参与者/非法值 | 400 | `team.role.invalid` | `{participant}` | （details 带具体原因） |
| reconciler 参与开关关（既有键） | 403 | `screening_not_enabled` | `{}` | 既有文案不变 |

注意：`_coding_authorize` 注入通道（编码共识/RoB 共享端点）保留消费者既有
403 形状（`analysis.rob.rob_access_denied` 等），**不**出现 team.role.*——
前端在这些页面继续按旧键映射；team.role.* 只出现在上表场景。

## 4. 读能力保留（R-1，前端不得加"角色不足"提示）

属主无论角色配置状态，以下**照旧可用**：仲裁 latest/记录详情/server-merge
视图、SoF rob_tool 预览/导出、评价一致性/导出/grade-input、PRISMA 聚合读、
任务管理。普通成员这些读仍须有效 reconciler（403 沿各端点既有键：
`tables.rob_requires_consensus` 等，响应无档位）。

## 5. 逐组件规格

| 组件 | 规格 |
|---|---|
| 成员管理（index.html 任务卡协作区） | 属主自身行=业务角色单选（coder/reconciler，未配置高亮+引导）；普通成员行同单选；双授权行显示"需选择"徽标+单选；**管理徽标（属主）与业务徽标分离**；保存调 PUT；`previous_role` 展示最近一次变更；开关（reconciler 参与筛选）对属主-仲裁者行同样可用 |
| 任务卡 | `active_screeners` 渲染当前筛选成员（required=true 徽标；config!=ok 显示配置警示）；与旧 `screeners` 名单分栏 |
| 筛选页（初筛/复筛提交） | 409 `team.role.*` → 顶部横幅"完成任务角色配置后继续"+跳转管理锚点（`team.role.blocked_hint`）；403 `screening_not_enabled` 沿旧文案 |
| 编码/评价页 | PUT 409/403 同上映射；个人历史读不变；能力探测（submission-evidence available）为布尔，不展示角色细节 |
| 仲裁页 | 批量链守卫文案原样；属主-仲裁者操作入口不变；属主-编码者/未配置时保存按钮置灰+同横幅 |
| SSE | 订阅 `team-role-changed`（按 task_id 刷新角色徽标/名单）；`tasks-changed` 照旧 |

## 6. 词典键清单（新增，十语言）

`team.role.unconfigured` / `team.role.conflict` / `team.role.denied` /
`team.role.invalid` / `team.role.blocked_hint` / `team.role.switched`（可选，
切换成功提示）。注册走 RFRegisterServerMessages（`team.*` 域）；index.html
词典变更须 bump `?v=`。字段名（participant/action/required_role）不做翻译
拼接，params 仅用于定位展示。

## 7. 兼容与后续

- personal/legacy/solo/未关联手工分析零变化（无新键、无新门）。
- 本批不含逐条资格（awaiting_members/awaiting_arbitration 等）UI——那是
  核心复审通过后的下一批（词典键沿用合同 §6 清单，届时合并进总交接）。
- 结构化错误载荷都包在 FastAPI `{"detail": {...}}` 内（与 RoB 错误一致）。

## 8. fix-r1 修订（2026-11-14 角色 II-A 修复轮 1；覆盖上文对应条目）

1. **`source` 枚举新增 `"stale_choice"`**（§1/§2 行）：成员显式 choice 的所选
   grant 被撤销后的失效选择——`role=null`、`config="unconfigured"`（业务不可
   用，须属主在成员管理显式重选后才恢复；不回退另一旧 grant）。前端按
   "需要重新选择"徽标处理（同 unconfigured 横幅）。
2. **PUT 响应新增 `previous_config`**（"ok"|"unconfigured"|"conflict"）：切换前
   配置状态；`previous_role` 现为切换前**有效单值角色**（旧 grant 派生也计入，
   如单 coder 授权未配置成员首切 reconciler → `previous_role:"coder"`；未配/
   冲突为 null，由 `previous_config` 区分，审计事件 `old_role` 同口径）。
3. **非法 `business_role` 一律 400 `team.role.invalid`**（空串/空白/超长/未知
   字符串；params `{participant, role}`，role 回显截断 64 字符）。非字符串/
   缺失仍 422（pydantic 畸形载荷）。合法值仍精确二选一。
4. **desktop 已确认 team 可达角色管理**：§1 GET/PUT 与成员参与开关
   （PUT `/collaboration/members/{id}/screening`）在 desktop 模式对 team 任务
   开放（属主校验/身份检查不变）；其余协作管理端点仍 server-only 404。
   desktop 非 team 任务同 404。
5. **team 编码业务写统一角色门**：`POST coding/values`、`POST
   coding/values-batch`、coding undo 恢复、编码证据上传（非 canonical）、
   canonical 提交证据——team 任务拒绝时给 §3 的 `team.role.*` 结构化
   409/403（此前部分入口为裸 403）；personal/legacy 不变。
6. **筛选职责检查入保存窗口**：初筛/复筛/批注（增/改/删高亮）/导入撤销的
   筛选写在锁内复查——角色切换或参与开关关闭完成后，在途请求按 §3 映射
   拒绝（409 `team.role.*` / 403 `screening_not_enabled`）；前端无需新键。
7. **`active_screeners` 不可读省略键**（明确化）：已确认 team 后协作库不可读
   （库消失/属主绑定异常）时**省略键**（不出现 `active_screeners: []`——
   可读名册恒含属主，空数组必为故障）；账户库查询故障时该成员条目
   `username/slug` 为 null（成员保留）。
8. **属主读例外扩充**（§4 追加）：`GET coding/freezes`（列表/详情）、
   `GET coding/records`、`GET coding/consensus`、`POST coding/agreement`、
   共享 RoB 列表/历史/汇总读——属主无论角色状态照旧可读（普通成员仍须
   有效 reconciler，普通 coder 隐私不放松）；业务写/冻结/提交不借读例外。
