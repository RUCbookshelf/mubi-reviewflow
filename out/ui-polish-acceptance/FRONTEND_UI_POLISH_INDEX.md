# UI 优化波交付索引

本索引汇总本波代码、逐项处置和验收证据。范围来自 [frontend_ui_optimization_wave_scope.md](E:/mubi-reviewflow/out/release_plan/frontend_ui_optimization_wave_scope.md)，基准问题来自 [120 项动态验证报告](E:/mubi-reviewflow/out/release_plan/frontend_formal_verification_2026-11-12/frontend_formal_verification_report.md)。工作树为 `E:/rf215-authfe`，分支 `feat/2.1.5-ui-polish`，起点 `2230523caf296b01af0c465805ddf3ff49a1afd2`。

[120 项逐条处置表](E:/rf215-authfe/out/ui-polish-acceptance/FRONTEND_UI_POLISH_120_DISPOSITION.md) 保留了原报告的全部 120 个主编号，A/B/C/D/E/F 分别为 22/16/19/19/26/18。四个跨泳道重复别名仅记录一次。原严重度 high 10、medium 68、low 42。每行分别说明实际浏览器、Node、源码/词条检查或接口边界；没有将逐项记录写成“120 项全部实测通过”。

## 主要交付

- 关系表单遇到真实 409 后并列本地和最新值，确认差异前继续锁写；保留草稿并保持关系稳定 ID，避免无提示覆盖。
- 两处 API Key 设置保留跨槽、跨面板、语言切换中的作用域内草稿；保存/测试语义、未知共享槽状态和取消等待均有明示。
- 导入复核保留跨页选择，显示 AI 置信度、生成等待和分块进度；设置冲突可恢复，稳定数据不重建阅读区。
- 筛选和全文工作区稳定轮询、阅读位置、焦点与切任务动画；模态期间快捷键不写入，鼠标可触发撤销，失败状态有可见重试。
- 完整评价、证据、旧 RoB、GRADE 与 SoF 各自保留草稿和来源说明；旧 RoB 可回填修订，来源互链准确定位；证据保存不重新下载未变 PDF。
- 登录恢复、任务状态和空态可见性已改善；“记住我”说明登录时可见且明确下一次登录生效。原报告 A13 按成功后任务卡重绘修复，未混写为重复提交问题。

## 验收记录

| 验证 | 已有证据 | 范围 |
|---|---|---|
| Node 集成 | 196/196 通过，0 失败 | [验收汇总](E:/rf215-authfe/out/ui-polish-acceptance/gates/summary.json)；A20 最后静态迁移后已全量重跑 196 条，同时重跑导入静态；其余五个校验器沿用该静态节点迁移前的通过记录，时间与指纹分别保存在 gates/summary.json。 |
| 六个验证器 | 全部 exit 0 | 导入静态、团队 PRISMA 静态、证据词条、导入 API、筛选轮次 API、团队 PRISMA API。 |
| 核心真实浏览器 | 9/9 场景通过，page_errors=[] | 模态无后台写入、重命名取消/Enter、下一卡焦点、初筛阅读位置、切任务动画、鼠标撤销、复筛位置、503 重试、Windows 恢复命令。 |
| 工作流真实浏览器 | 四轮自然轮询约 124 秒；其他客户端改无关策略时摘要滚动 850 保持 | 另有训练 20 项创建201、设置滚动 300 保持、帮助后退、390px、断连提示与 E10 聚类提示。 |
| 模型与导入真实浏览器 | 两配置入口、双槽草稿、取消、语言、跨页选择与移动端 | 真实新树后端连接回环模型；没有调用真实第三方 AI 或真实用户 Key。 |
| 关系/SoF/旧 RoB | 25/25 lane Node；真实双标签页 409 与同范围/改范围保存 | 同对字段冲突及改报告对冲突都需确认；确认后仅一条关系，共享修订不变。 |
| 完整评价/证据/GRADE | 实际备注草稿、重载、保存；PDF第8页/9页、116%缩放保持 | 同一 PDF URL，不新增 PDF/图片 GET；规则/来源互链另有桌面及390px截图。 |
| 末轮补验 | E04 反向来源跳转；E10 异步策略提示；A20 说明可见性 | 各自新增回归和真实浏览器证据，均记录无未捕获页面错误。 |

实际浏览器使用独立合成账号/任务，服务为 `http://127.0.0.1:18931`，来源是本波新树。页面故障注入只用于对应错误分支；E-DYN-31 使用真实后端 409。桌面主视口 1440×1000（部分泳道1440×900）、手机 390×844。截图通过与像素溢出检查不能代替完整辅助技术审计。

## 分工与证据入口

| 包 | 处置文档 | 主要实测 |
|---|---|---|
| 模型设置 | [handoff](E:/rf215-authfe/out/ui-polish-acceptance/keys/handoff.md) | [模型浏览器结果](E:/rf215-authfe/out/ui-polish-acceptance/keys/browser_results.json)、[核心9场景](E:/rf215-authfe/out/ui-polish-acceptance/keys/core-browser/results.json)、[A20专项](E:/rf215-authfe/out/ui-polish-acceptance/keys/core-browser/remember-note-after.json) |
| 导入/启动加载 | [import-disposition](E:/rf215-authfe/out/ui-polish-acceptance/import/import-disposition.md) | [导入结果](E:/rf215-authfe/out/ui-polish-acceptance/import/qa/result.json)、[延迟模块加载](E:/rf215-authfe/out/ui-polish-acceptance/import/qa/lazy-result.json) |
| 工作流/训练/阅读 | [coverage](E:/rf215-authfe/out/ui-polish-acceptance/workflow/coverage.md) | [自然轮询](E:/rf215-authfe/out/ui-polish-acceptance/workflow/qa/workflow-results.json)、[E10聚类提示](E:/rf215-authfe/out/ui-polish-acceptance/workflow/qa/e10-settled-results.json) |
| 完整评价/证据/GRADE | [HANDOFF](E:/rf215-authfe/out/ui-polish-acceptance/evidence/HANDOFF.md) | [浏览器结果](E:/rf215-authfe/out/ui-polish-acceptance/evidence/browser-results.json)、[错误本地化](E:/rf215-authfe/out/ui-polish-acceptance/evidence/error-language-results.json) |
| 关系/SoF/旧 RoB | [DELIVERY](E:/rf215-authfe/out/ui-polish-acceptance/relations/DELIVERY.md) | [双标签409](E:/rf215-authfe/out/ui-polish-acceptance/relations/relation-browser-results.json)、[RoB/SoF](E:/rf215-authfe/out/ui-polish-acceptance/relations/rob-sof-browser-results.json)、[E04反向互链](E:/rf215-authfe/out/ui-polish-acceptance/relations/e04-browser-results.json) |

归档清单见 [ARCHIVE_FILES](E:/rf215-authfe/out/ui-polish-acceptance/ARCHIVE_FILES.md) 与 [SHA256清单](E:/rf215-authfe/out/ui-polish-acceptance/ARCHIVE_MANIFEST.json)。归档仅含报告、测试结果和筛选截图；浏览器认证 fixture/session/token 文件不属于交付。

## 保留边界与发布状态

B-DYN-14 的逐槽共享 metadata、C-DYN-20 的第二模型就绪预查、C-DYN-21/F-DYN-16 的必要轮询 GET，以及 E-18 的真正撤销/清空未判定状态仍受现有接口限制。已完成前端能够准确表达和执行的部分，未伪造其余能力。共享成员完整权限矩阵、真实第三方模型、三平台成品安装器和所有原报告性能计时没有本波完整重测。

本波本地提交为 `6c14965d8021b164435996a8b6964f0b3f58102a`，共 60 个文件。提交前已执行 GitNexus detect_changes：暂存范围 269 个符号、3 条评价相关流程、medium；相对 main 的整体比较含发布基线此前的认证等改动，为 196 文件、122 流程、critical。当前提交没有 custom_backend 变更。内联函数／缺失符号以源码与实际行为核查补充，代码图不代表全覆盖。已检查本地提交后的受跟踪文件干净；未合并 main、推送 origin 或发布。随后提出的独立四页布局 HTML 预览属于下一项工作，未作为这 120 项修复计数，也未替换产品布局。

主代理另核对：120 个唯一主编号与原报告集合完全相等，原报告 SHA256 一致。基线使用 CRLF 的文件保留原换行；按 cr-at-eol 检查提交差异通过。
