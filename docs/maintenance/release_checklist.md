# 发布检查表（2.1.6 起适用）

目的：把 2.1.5 发布链的教训固化为打标前逐项核对单。每次打标/发布逐项勾选并留痕；**最终发行必须由用户给出明确发布指令**——代理不擅自 tag/publish，演练通过也不等于发布授权。

参考案例：2.1.5 —— run #34（release/2.1.5 @ 80dd35f）2026-11-14 三平台 success；Release v2.1.5 公开发布（github-actions[bot]，2026-11-14T02:25:46Z）。

## 0. 工具链（本机演练用）

- **release Python（演练/测试统一用它）**：`C:/Users/16073/.codex/preview-data/reviewflow-2-1-1-venv/Scripts/python.exe`（Python 3.11.15；2026-11-14 核实存在）。
- 不使用 hermes 附带 Python：依赖不全，不作为发布链环境。
- Node v22.x。gh 未安装——GitHub 操作走网页或公开 API。

## 1. 待发布提交核对

- [ ] 待发布分支 HEAD = 预期发布提交（记录短哈希：＿＿）；`git status` 干净（无未提交/未跟踪，已登记豁免除外）。
- [ ] 与上一发布提交 diff 快审：变更范围与本次版本相称；`VERSION` 文件已 bump（当前 2.1.5）且与 CHANGELOG 最新条目一致。

## 2. 三平台最近构建结果

- [ ] GitHub Actions 该分支最近一次 run：windows / ubuntu / macos 三平台 job 全绿（run 号：＿＿）。
- [ ] 无跳过（skip）的平台 job——skip 不算绿。

## 3. runner 镜像存续

- [ ] 打标前查 GitHub runner-images 公告：工作流所用镜像（当前含 macos-15）均未在退役名单。
- [ ] 教训锚点：macos-14 因 GitHub 2026-11-02 退役曾致 run #26 红，已重打标切 macos-15（548c5a9）；每年打标前留意同型公告。

## 4. 演练构建

- [ ] **演练前置断言（不满足则禁止 dispatch）**：确认拟运行提交的 `publish` 作业已带仅标签守卫 `if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/')`。守卫未落地的提交**绝不能 dispatch**（基线 80dd35f 的 publish 无作业级 `if`，dispatch 会直接进入 publish、真实打标并创建公开 Release）；不得采用「先 dispatch 再观察 build、事后取消 publish」的冒险备选。（2026-11-14 注：该守卫目前仅在本地工作树未提交改动中，尚未成为任何提交的一部分——以实际将 dispatch 的提交为准核对。）
- [ ] 前置断言通过后：对 `.github/workflows/release.yml`（支持 workflow_dispatch）跑一次演练构建，确认三平台产物可生成；演练链路含 assemble（产物汇集+六载荷检查+SHA 校验+desktop-release-assets 资产包，2.1.6 起），守卫已落地时 publish 必为 skipped。
- [ ] 本地完整门禁（2.1.6 起，与 CI 同一入口）：`python build/run_validation.py`——整目录 pytest + 六个静态校验器 + 全部 node --test 单测逐命令执行、任一非零退出即败（只读查看计划：`--list`；测试消费导出：`--list --json`）。依赖按单一源安装：`python -m pip install -r build/requirements-test.txt`；node 需在 PATH（或设 `RF_NODE`）。
- [ ] 服务器模式本地冒烟（可选，用 release Python）：`python -m uvicorn build.launcher:build_app --factory`——必须带 `--factory` 的应用工厂形态（build_app 已把前端静态目录挂到根路径；2.1.5 教训：纯 API 入口形态会导致首页 404）。
- [ ] 演练产物不入库、不留发布目录污染（跑完清理临时目录）。

## 5. 成品/清单/校验文件完整

- [ ] Release 附件齐全（共七件 = 六载荷 + 1 校验文件）：三平台安装包 + 三份构建清单 `build-manifest-{windows,macos,linux}.json` + 校验文件 `SHA256SUMS.txt`。
- [ ] 校验覆盖核对：`SHA256SUMS.txt` 须覆盖全部六载荷（三安装包 + 三构建清单）逐名在列，无缺漏。
- [ ] 抽验：下载一份安装包对 hash，与 SHA256SUMS 一致。
- [ ] 发布卡片渲染核对：标签注释与 release notes 在 GitHub 页面显示正常（无被吞的标记/乱码）。

## 6. 标签与文案纪律

- [ ] 未发布标签可重打（2.1.5 曾三次重打均系未发布标签，P1-1 明文允许）；已公开发布的标签不可改写。
- [ ] `generate_release_notes: false`（工作流既有设置）保持。
- [ ] 发布文案=纯产品语言：不写开发细节/内部代号/统计术语（标签注释会被 GitHub 渲染成发布卡片）；README 不写更新内容，更新只进 CHANGELOG。

## 7. 最终发行

- [ ] 以上全部勾选后，向用户呈报：commit、run 号、演练结果、附件清单。
- [ ] **等用户明确发布指令后再打标/发布**；发布后回填：tag、run 号、发布时间、附件清单。
- [ ] 发布后健康检查：三平台安装包可下载、校验文件一致、发布卡片显示正常。

## 留痕

| 次序 | 版本 | commit | run 号 | 演练 | 用户指令 | 发布时间 |
|---|---|---|---|---|---|---|
| 1 | v2.1.5 | 80dd35f | #34 | （2.1.5 链已完成） | 已给 | 2026-11-14T02:25:46Z |
| 2 | | | | | | |
