# r6 复审修复指示 —— 上传与路径安全（custom_backend/security / tasks / fulltext / launcher）

> 基线【已修·集成轮】：复审确认 r5 上传修复三提交（ba36b99→3311316→42a5a3b）
> 仅存在于 reflog/悬空对象（分支被 reset 回 eb11741、工作树空转）。集成轮已
> 将三提交 cherry-pick 回 fix/2.1.4（9197221 / c854809 / 21cb0cb，零冲突），
> U5-P1..P12 修复代码现已在场（含 test_upload_security_regression.py）。
> 下列条目是复审**针对修复后代码（@42a5a3b）发现的缺陷**，泳道在新基线上
> 逐项收口；复审的 5 条抽验复现（P1/P2/P5/P7/P9 的旧症状）需先在新基线
> 重跑核对哪些已被三提交覆盖，再修残余。

## R6-S1（medium）XFF 全部落 xff-untrusted 单桶，未认证 5 请求锁死全站登录 60 秒

- 对应复审条目：『文档 nginx 每个请求都带 XFF，全站登录/注册共享 5 次/分钟；5 次错误登录后第 6 次正确密码 + 不同 XFF 仍 429/Retry-After 60，无 XFF 登录 200；代理 limit_req 不移除应用层 429』（security.py:224-237 @42a5a3b；UBUNTU_SERVER.md:149-151）。
- 修复指示：
  1. xff-untrusted 桶按路径域分键（至少 login/register 独立于其他端点），且只对**失败尝试**计数（成功登录注册即清零/不计数），避免错误锁定合法用户。
  2. 更新 UBUNTU_SERVER.md 的 nginx 配置：可信反代场景 set X-Real-IP / 规范化 XFF（单个可信值），或在应用层为受信代理回执提供 opt-in（环境变量声明可信代理数）。
  3. 429 响应说明计数维度，帮助运维定位。
- 验收标准：复审场景重放——5 次错误后正确密码登录不被无关请求的共享桶拒绝；不同 XFF 轮换绕过不复现（对同一账号维度仍限流）。

## R6-S2（medium）U5-P2 仅部分修复：DELETE 与并发读/写仍无互斥，inflight 是快照

- 对应复审条目：『检查后到 shutil.move 之间无删除中状态；暂停 move 时并发 GET /articles、/progress 均 200，随后 DELETE 200 且任务目录消失；并发导入写路径可写入 .trash（Linux）或 Windows 半截副本/500；inflight 计数含所有读请求，后台轮询使 DELETE 误报 409』。
- 位置：`custom_backend/main.py:1457-1461`（合并后行号以三提交后为准）；`coscreen/tasks.py:496-509`；`custom_backend/main.py:203-290`（中间件）。
- 修复指示：
  1. DELETE 流程在检查 inflight 前先置 `deleting` 标记（内存 registry + task.json 原子写），中间件对 `deleting` 任务的**一切**请求（含 GET）返回 409/410；move 完成后清标记。
  2. inflight 统计只计写方法（POST/PUT/DELETE/PATCH），读请求与后台轮询不再计入。
  3. 补 E2E 并发测试：暂停 move 期间 GET 返回 409/410（而非 200+目录消失）；并发导入在 deleting 置位后被拒。
- 验收标准：复审实测脚本重放 → 并发读不再 200 后消失；DELETE 不因后台轮询误报 409。

## R6-S3（low）multipart 文件 part 计数可被绕过（首块 '-' / 超大 part 头分块）

- 对应复审条目：『首块只发 "-" 使首边界不被识别，2 个文件 part、max_files=1 时 files=1 不抛；每个 part 头 >4096B 且按 100B 分块时 3 个文件 part files=0 不抛；Starlette max_files=1000，已认证者可让最多 1000 part 在后置校验前 spool』（security.py:397-436 @42a5a3b）。
- 修复指示：解析前对 Content-Type 头与请求体前 N 字节做 part 头尺寸/畸形预检（超限 413/422）；或对 `multipart/form-data` 强制先读完整 headers 再解析（禁用宽松流式）；保留现有后置校验作第二层。
- 验收标准：复审两种绕过构造 → 均在解析层被拒；正常单文件上传不回归。

## R6-S4（low）U5-P12 未闭合：/api/events 仍接受 24h JWT ?token=，出货前端仍用 EventSource

- 对应复审条目：『票据端点（POST /api/events/ticket）对实际客户端无效——前端仍用 EventSource("/api/events?token="+token)，完整凭据仍进 URL/反代日志/浏览器历史』（main.py:1457-1461 + index.html:5525 @42a5a3b）。
- 修复指示：前端改用 ticket 流（先 POST 换短时票据，再 EventSource `/api/events?ticket=...`；票据 60s 一次性）；服务端对 query token 设置下线时间表（先 deprecation 头，后 410），文档同步。
- 验收标准：出货前端不再把 24h JWT 放入 URL；SSE 断线重连走新票据；旧 token 路径按时间表拒绝。

## R6-S5（low）最外层 UploadSizeLimitMiddleware 错误响应绕过 SecurityHeaders/CORS

- 对应复审条目：『400/401/411/413 直接 send，实测未认证 multipart 401 响应缺 nosniff、CSP、Access-Control-Allow-Origin——本次修复新引入的安全头/错误契约回归』（main.py:174-182,343-348 + security.py:512-522 @42a5a3b）。
- 修复指示：中间件错误路径复用 SecurityHeaders 的加头逻辑（提取 pure helper，两层共用）；CORS 预检（OPTIONS）在最外层放行并带头。
- 验收标准：401/413 响应包含全部安全头；浏览器跨域错误路径契约恢复（复测 CORS 预检 200 且带头）。

## R6-S6 复审抽验与代码级核销条目的重验（流程性）

- 对应复审条目：5 条实测复现（U5-P1 尾点别名锁互斥 + P2 同进程持锁 delete_task、P5 不存在 zotero_key 上传 500、P7 XFF 伪造轮换绕过限流、P9 中文 fulltext 下载 latin-1 500 + _file_response 汇点）与 7 条代码级核销（P3/P4/P6/P8/P10/P11/P12）。
- 修复指示：三提交已 cherry-pick 回基线后，逐条重跑复审探针（rt_u5_p1.py 等）确认哪些症状已被覆盖；仍未覆盖的按首审 U5 原文修复。特别核对：
  - P9 `_file_response`（main.py:969-980）`filename*=UTF-8''{safe_name}` 未 quote() 的汇点——三提交是否已覆盖（复审在 42a5a3b 之后又发现该汇点，需确认）；
  - P5 上传 FOREIGN KEY 500 → 400/404 受控错误；
  - P1/P2 与 R6-C3 的别名规范化联动（协作泳道同根）。
- 验收标准：复审全部 U5 探针在新基线重跑，逐条给出「已覆盖（证据）/仍存在（修复+测试）」结论表，附在本文件末尾。
