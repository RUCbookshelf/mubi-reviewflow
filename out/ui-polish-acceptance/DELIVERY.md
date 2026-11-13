# UI 优化波 · 本地交付

代码位于 E:/rf215-authfe，分支 feat/2.1.5-ui-polish；基线 release/2.1.5 @ 2230523，提交 **6c14965d8021b164435996a8b6964f0b3f58102a**。60 个文件；受跟踪文件干净。尚未合并、推送、发布或原生打包。

[全波索引](E:/rf215-authfe/out/ui-polish-acceptance/FRONTEND_UI_POLISH_INDEX.md) · [120 项处置表](E:/rf215-authfe/out/ui-polish-acceptance/FRONTEND_UI_POLISH_120_DISPOSITION.md) · [测试记录](E:/rf215-authfe/out/ui-polish-acceptance/gates/summary.md)

优先问题已修：弹层快捷键穿透、卡片动画失效、跨槽/跨面板草稿丢失、409 无确认覆盖；同时处理登录延载与恢复、AI 生成取消和进度、轮询重绘、阅读位置、评价回填、PDF/证据图片保留、多语言及平台密码恢复入口。

最终 Node 196/196。六个校验器先全部通过；最后仅移动“记住我”静态说明节点并增加回归，再完整运行 196 条 Node 和导入静态（143 脚本、3 段内联）。核心浏览器9场景、关系真实双标签409、两入口多槽草稿等各有专项结果。A20、E04、E10末轮补验也已通过。证据表区分浏览器、Node、源码/词条与接口边界，未称120项均做了完整动态复测。

保留的边界：共享 API Key 逐槽元数据、第二模型 readiness、必要轮询请求、SoF 真正撤销/清空仍需后端支持；英文错误当前做前端已知串映射。Mac/Linux恢复命令与打包声明已实现并测试逻辑，未在本机原生构建和安装两平台成品。PDF视口专项使用九页合成空白PDF，不作为正文渲染验收。

新增布局需求单独预览：[阅读优先四页 HTML](E:/mubi-reviewflow/out/workspace_layout_preview/index.html)。预览未移植产品，等待用户目检。测试使用独立合成账号/任务/库，没有使用真实研究数据。
