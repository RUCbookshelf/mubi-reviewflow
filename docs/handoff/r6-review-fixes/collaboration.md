# r6 复审修复指示 —— 协作权限（task_collaboration / main / auth）

> 基线：fix/2.1.4 已含 r5-collab 修复（a6faac8 合并）。复审确认 r5 修复了
> OwnerUnavailableError fail-closed、register 的撞名拦截等；剩余条目按本指示落实。

## R6-C1（medium）U4-P2 不彻底：存量撞名账号可被邀请，原 500 症状完整复现

- 对应复审条目：两条同题（main.py:1275-1294 invite 未接 reserved_screener_collision；Temp/rv5_p2_probe.py 实测）。
- 复现要点（复审实测）：直插 users.db 造存量 'collaboration' 账号 → owner 邀请 201；该成员 GET work-queue/screen → 500（shared_corpus.py:39 ValueError 穿透 sync_catalog）；register_screener 把 'collaboration' 永久写进 task.json screeners（撤销端点不回收）；权限库 collaboration.db 被写表污染；team_progress 统计读到 collaboration.db。登录 warnings 前端未消费（index.html:8177-8179 只读 token/user）。
- 修复指示：
  1. invite 端点对目标用户名调用 `auth.reserved_screener_collision` 拒绝（409/422，带 message_key），存量账号同口径。
  2. 撤销/拒绝邀请时回收 task.json 的 screeners 登记。
  3. `sync_catalog` 与 work-queue 路径对该 ValueError 从 500 收敛为受控错误码；登录 warnings 前端 toast 呈现（改名指引）。
- 验收标准：复审 probe 重放 → 邀请被拒；不再出现 500；撤销后 screeners 不残留；前端可见撞名提示。

## R6-C2（medium）U4-P1 子项未闭合：无属主迁移/修复接口，且新 409 变成无恢复路径的硬锁死

- 对应复审条目：『全仓仅 create_task 调用 initialize_task_store，没有迁移命令/端点，handoff 文档却要求“使用受支持的迁移/管理流程”（不存在）；create_task 对 initialize_task_store 的 sqlite3.Error 不捕获 → 幽灵 legacy 任务同样不可见不可删；409 message 为英文且 message_key 前端不使用』。
- 位置：`custom_backend/main.py:1227-1250 (_require_task_owner), 1168-1178 (create_task)`；`coscreen/task_collaboration.py:134-180`；`docs/handoff/2026-09-30-cloud-collaboration.md:27,71`。
- 修复指示：
  1. 提供受支持的属主修复入口：CLI（如 `python -m custom_backend.set-task-owner <task_id> --user <uid>`）或 owner 认证的管理端点，为 legacy/无属主任务补 collaboration.db 属主绑定行；对 task_id 不匹配的库同样可修复（重绑 task_id）。
  2. create_task 捕获 sqlite3.Error，失败时回滚已建目录+task.json，不留幽灵任务。
  3. 409 collaboration_owner_unset 的 message 注册前端 i18n（interface_locale.js 双键块），文案指明恢复入口。
  4. 更新 cloud-collaboration handoff 文档指向真实命令。
- 验收标准：u4_p1_p2.py 场景 → 原属主经修复接口恢复管理权（PUT/DELETE/roles 正常）；幽灵任务不再产生；409 文案十语言。

## R6-C3（medium×2）task_id 尾点别名绕过锁互斥与 legacy 折叠

- 对应复审条目：两条（coding_consensus_api.py:93-106 锁按原始路径 id 分键，别名写与规范 id 撤销不互斥，alias_toctou.py 实测撤销期间写落地；review_workflow_api.py:47-56 get_owner 收到原始 id → legacy ctx，owner paused 守卫被绕过，alias_policy2.py 实验成员决定落库）。
- 位置：`coscreen/tasks.py:274-283 (find_task 解析尾点别名)`；`custom_backend/main.py:329-344 (require_user 用 info.task_id)`；两个 API 模块的锁与 ctx。
- 复现要点：Windows 下 `/api/tasks/{tid}.` 与 `/api/tasks/{tid}` 解析到同一物理任务目录，但字符串 id 不同 → 锁分键、get_owner 判 legacy、409 闸门不触发。
- 修复指示：在入口层统一规范化 task_id——路由处理前（middleware 或各 API 的公共 helper）把路径参数解析为规范 id（复用 find_task 的解析结果），锁 key、get_owner、require_user、ctx 一律使用规范 id；find_task 本身保留别名解析（兼容文件系统），但对外路径不再以原始字符串作为权限/锁键。
- 验收标准：复审 alias_toctou.py / alias_policy2.py 重放 → 别名路径写阻塞期间撤销不可完成（互斥成立）；owner paused 时别名路径 POST /decisions → 409 stage_paused；alias ctx 不再 legacy 折叠。补别名路径回归测试（r5 的 test_u4p5_* 均用规范 id，需补别名用例）。

## R6-C4（low）桌面模式协作库损坏 → 全路由 503 且任务隐藏（可用性回归）

- 对应复审条目：desktop_corrupt.py 实测：collaboration.db 非 SQLite → PUT/GET articles/workflow-settings 全 503，任务列表 200 但任务不在；修复前同一情形按 legacy 可见可管理（单机无多用户安全收益）。
- 位置：`custom_backend/main.py:273-289 (全局 503), 1149-1165 (list_all_tasks), 1207-1225 (_task_visible_to)`。
- 修复指示：desktop 模式（REVIEWFLOW_MODE=desktop）对 OwnerUnavailableError/库损坏降级为 legacy 可见可管理（与 task_collaboration.py:3-19 docstring 披露的单机兼容口径一致），仅 server 模式保持 fail-closed；503 文案在永久损坏场景给出恢复指引（备份后删除/改名 collaboration.db）并写入部署文档。
- 验收标准：desktop 模式损坏库 → 任务可见可管理；server 模式行为不回归（409/503 fail-closed 保留）。

## R6-C5（low）task_collaboration 公开函数的属主-任务绑定校验不一致（防御纵深）

- 对应复审条目：p8_rest.py 实测：get_owner fail-closed（返回 None），但 authorize_action/is_owner/is_participant/grant_role 在 task_id 省略时仍 True/成功写库；docstring 声称「绑定校验不可静默跳过」。
- 位置：`coscreen/task_collaboration.py:184-199 (_owner), 234-263, 265-341, 369-401`。
- 修复指示：除只读查询（is_owner/is_participant 可保守返回 False + warning 日志）外，authorize_action/grant_role/revoke_role 在 task_id 缺省或与库内绑定不匹配时抛 ValueError（或显式 owner_binding 参数）；全仓调用点补齐 task_id 传参；docstring 更新。
- 验收标准：p8_rest.py 场景 → authorize_action 拒绝、grant_role 拒绝；现有生产调用（均已传 task_id）不回归。

## R6-C6（low）list_participant_ids 降级语义：『读失败』被折叠成『空参与者表』

- 对应复审条目：rv5_stats_probe.py 实测：A1 陌生用户在锁窗口拿到 []（而非 PermissionError）；B1 team_progress 以 200 返回 members:[]（统计清零）；C1 降级下 notify 静默丢事件。
- 位置：`coscreen/task_collaboration.py:337-368`；`custom_backend/main.py:1299-1303`；`custom_backend/review_workflow_api.py:236-250 (notify)`。
- 修复指示：降级分支（锁超时/库不可读）区分于「确无参与者」——返回 sentinel（如 `None` 或抛 OwnerUnavailableError）；team_progress 收到降级时返回 503 + Retry-After 或带 `degraded: true` 标记；notify 对降级至少记日志/入队补发，不再静默丢弃。降级分支不得跳过 actor 成员资格判定（陌生用户仍应被拒）。
- 验收标准：复审 A1/A2/B1/C1 场景 → 成员资格拒绝或 503/degraded，绝不返回伪空表；正常路径不回归。

## R6-C7（low）get_owner 对 task_id 不匹配仍折叠 legacy（desktop 残留披露项）

- 对应复审条目：rv5_p1_probe.py C3：整目录复制为新 task_id → get_owner 返回 None（折叠 legacy）。影响已收敛（server 模式 409 fail-closed），属残留披露。
- 修复指示：task_id 不匹配时抛显式错误（与读失败同族），desktop 模式在任务详情/列表给出「协作库归属不匹配，需运行修复入口（R6-C2）」提示；至少在模块 docstring 与 handoff 文档披露该折叠语义。
- 验收标准：C3 场景返回显式错误或带提示，不再静默 None。

## R6-C8（low）任务列表在协作库锁窗口把属主自己的任务静默隐藏

- 对应复审条目：list_all_tasks 对 OwnerUnavailableError 按 continue 降级不可见（不区分属主/他人）；单任务端点同场景 503+Retry-After（口径不一致）。
- 修复指示：属主可见性判定失败时保留条目并标 `degraded: true`（前端列表给「稍后刷新」标记），或至少与详情端点口径一致；commit 已披露该权衡，属可接受但应知悉的副作用——泳道择一收口并在文档注明。
- 验收标准：锁窗口内属主刷新列表 → 自己的任务不消失（或有明确 degraded 提示）。
