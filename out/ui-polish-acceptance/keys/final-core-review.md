# 最终集成复核（2026-11-12）

目标：E:/rf215-authfe，feat/2.1.5-ui-polish @ 2230523 加当前工作区变更。独立审查后，经根代理授权只移动一处静态提示节点，并补回归；无函数变更。

旧 session 12197 已不可查询；原 results.json 于 21:31:41 写出完整九项通过，晚于 index.html 最后修改 21:26:01。独立重新运行 debug_core_acceptance.py，session 55181 正常 exit 0，结果于 21:38:18 更新，日志 final-rerun.log。

| 真实浏览器检查 | 结果 |
|---|---|
| 弹层打开时 1/3/Ctrl+Z 不写入底层决策 | 通过 |
| 输入确认框中的取消按钮 Enter 返回取消 | 通过 |
| 键盘提交后焦点恢复到下一卡判定按钮 | 通过 |
| 离开初筛再返回保留文献与摘要阅读位置 | 通过 |
| 退场动画中切任务后新卡可见且可继续操作 | 通过 |
| toast 内按钮可被真实鼠标点击 | 通过 |
| 离开复筛再返回保留当前文献 | 通过 |
| work-item 503 显示常显错误与重试，重试恢复按钮 | 通过 |
| 持久登录恢复后登出、重置页面与 Windows 命令 | 通过 |

集成树 Node：core 9 + recovery 2 + module loader 4，初次共 15/15，无失败或跳过。A-DYN-20 后续新增第 10 条 core 回归，修前明确失败，修后 core 10/10 通过；此次静态移动不复跑全套 Python，已通知 import 代理重跑受影响 Node/static。

新增确认问题已关闭：A-DYN-20 的记住我解释文字原先被放在 authResetFields，登录时父容器隐藏，重置时自身隐藏，三模式均不可见。已将唯一的静态 p 移至 authRememberRow 后。真实 Edge 再次执行 login → reset → register → login，登录可见、其他模式隐藏，aria-describedby 正确且无页面异常。修前证据 core-browser/remember-note-probe.json，修后 core-browser/remember-note-after.json，目检截图 core-browser/remember-note-visible.png。

审查覆盖当前 index.html 的认证恢复、401、RFConfirm、导航与队列位置、动画清理、复筛失败恢复；同时查看 module loader、workflow_controls 弹层和守卫、launcher 恢复命令。当前没有尚未关闭的新确认问题。GitNexus 当前索引 query 提示 FTS 缺失，内联 logout/RFConfirm 无符号结果；相关结论以磁盘源码与实际回归为据，不能用空图结果声称全覆盖。

最简风险：核心操作已复验；共享 API Key 逐槽详情仍受后端元数据能力限制；macOS/Linux 安装包未原生验收。page_errors 为空不代表全产品网络请求无错误，503 为定点注入，合成无 PDF 文献出现的 404 属预期。
