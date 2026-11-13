# Core 独立复核与回归

独立审查 E:/rf215-authfe 的 index.html、launcher.py、macOS/Linux 打包脚本；核心函数修复由主代理处理。本代理增补 ui_polish_core.test.cjs、ui_polish_recovery.test.cjs，后经授权直接移动 A-DYN-20 唯一静态提示节点，无函数变更。

## 本轮发现与关闭

| 发现 | 处理与验证 |
|---|---|
| RFConfirm input 模式捕获 dialog 任意焦点的 Enter，Tab 到取消后按 Enter 也提交输入 | 主代理限定 event.target===input；Node 实际执行 RFConfirm，输入 Enter 返回文本、取消 Enter 保留按钮默认行为并返回 false，两者归还焦点。 |
| 清除退场动画后旧 220ms callback 仍推进队列，同任务离页可扰动位置 | 主代理在异步关键点核对 animation version；原测试确实红，修后通过；旧 callback 既不 advance，也不清除新动画。 |
| fulltext/coding 同任务切来源时，旧 queue 的当前项压过目标来源记忆 | 主代理新增队列 positionScope；实际执行两 loader，目标来源使用目标记忆，同来源重载保留最新阅读位置。 |
| advanceALQueue 抛错时外层已跳过恢复 disabled 控件，移除 out 后原卡仍不可操作 | 主代理 catch 中重绘；原恢复断言红，最新通过。 |
| Windows 复制重置命令的双引号允许特殊输入进入 PowerShell 展开 | 主代理改为单引号且双写内部单引号；Node 三平台断言保留完整字面量，不执行生成的命令。 |
| 复筛 work-item 加载失败的错误与重试位于默认关闭的批注面板，正文没有可见恢复入口 | 真实浏览器注入 503 复现；主代理移到标题下的常显 #ftLoadError；独立探针与完整流均确认批注面板关闭时重试可见，解除注入并重试后判定按钮恢复。 |
| 持久登录恢复的忙态通过 authSubmit.textContent 重建按钮内容，删除 #submitText，随后登出触发 setAuthMode 空引用 | 真实完整流捕获；主代理仅更新 #submitText。新增 Node 第 9 条验证恢复成功和 401 两分支保留标签节点；真实完整流验证持久登录、登出、忘记密码与命令渲染全部通过。 |
| A-DYN-20 记住我解释文字位于重置字段组，所有模式均不可见 | 经主代理授权只把静态 p 移至记住我旁；新增 Node 第 10 条修前红、修后绿；真实 Edge 的 login → reset → register → login 验证登录可见、其他模式隐藏，截图已目检。 |

最新独立检查没有尚未解决的新发现。这不是对所有 GUI 路径的穷尽保证。

## 回归证据

- ui_polish_core.test.cjs：10/10。模态 1/3 不落到底层、RFConfirm 输入/取消 Enter、动画 stale/error 路径、来源作用域、toast pointer events、两 loader 来源恢复、三平台命令引用、持久登录恢复保留提交标签节点、记住我说明三模式可见性。
- ui_polish_recovery.test.cjs：2/2。通过实际 Python 子进程执行新 launcher CLI，在 TemporaryDirectory 合成账号库验证：配置目录优先级、未知账号拒绝、真实签发与一次性消费、pending 迁移拒绝、参数错误拒绝、不启动 app、不写代码到日志；另校验两平台包含 recovery 模块。
- 未实际构建/安装 macOS 或 Linux 包；跨平台包装清单检查与 Windows 上执行 Python CLI 行为验证不能替代原生安装包验收。
- 未打印/保存恢复码，临时数据由 TemporaryDirectory 自动清理。
- debug_core_acceptance.py：真实新树 desktop 后端 http://127.0.0.1:18931，自建合成账号、两个任务与 RIS。9/9 浏览器断言通过：模态快捷键不误提交、取消 Enter、下一卡焦点、初筛阅读位置、切任务中的退场动画恢复、toast 鼠标点击、复筛位置、503 可见重试、持久登录后登出及 Windows 重置命令。保留真实 CSP；所有操作等待最多 15 秒。结果 core-browser/results.json，未捕获页面异常。
- core_fulltext_probe.py：独立确认批注侧栏关闭时重试按钮可见且恢复可操作，结果 core-browser/fulltext-probe.json，截图 core-browser/fulltext-retry-visible.png。
- 真实浏览器流的 503 是定点故障注入，合成文献无 PDF 时存在预期 404；未捕获页面异常并不代表所有网络请求都为 2xx，也不代替全产品 API 审计。

这两个测试文件已由主代理纳入 release.yml；ui_polish_core.test.cjs 最新第 10 条断言已安装至真实树并验证同 hash。最后一轮 core 10/10；此前 recovery 2/2、module loader 4/4。受影响 Node/static 门禁已交 import 代理重跑，无需重跑全套 Python。
