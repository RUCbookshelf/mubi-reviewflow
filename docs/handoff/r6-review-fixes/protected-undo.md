# r6 复审修复指示 —— 受保护撤销（workspace_undo / coding_evidence）

> 基线：fix/2.1.4 已含 r5-undo 修复（257d96c/5693b79）。复审确认 t8 的
> section/position 已放行、主路径（正常保存移除图片→GC）已修复；
> i18n 词条缺口已由集成轮补（commit 993ea9f）。剩余条目按本指示落实。

## R6-U1（medium）U3-P3 仅半修复：description 编辑仍阻断 coding 撤销（需产品仲裁）

- 对应复审条目：两条同题复审（workspace_undo.py:138-186 / :24,_SEMANTIC_DEFINITION_COLUMNS 含 description）。
- 复现要点（复审实测）：保存编码值后仅 `update_dimension(description='new description')` → 该维度所有历史 coding 撤销条目永久 409，报误导性文案 "The saved result or downstream work has changed"；修复以 coding_evidence.definition() 口径把 description 留在语义键集，并在测试 docstring 文档化。
- 修复指示（两案，需产品定夺后其一）：
  - 方案 A（按首审口径）：把 description 从 `_SEMANTIC_DEFINITION_COLUMNS` 与 stable_fields 的 definition_json 比较面剔除；撤销恢复时以快照内 definitions 整体恢复（含旧 description，即撤销会把说明文字一并回滚——需在确认弹窗文案中写明）；测试 `test_description_edit_still_blocks_coding_undo` 相应改写。
  - 方案 B（维持现状）：把 U3-P3 显式改判「部分修复/口径变更」，在 changelog 与首审核销表注明 description 属守卫比较面、其编辑不属「纯布局编辑」，并优化 409 文案指出具体冲突字段（description 变更）。
  - 集成轮建议：默认方案 B（改动最小、语义自洽），除非产品认为说明文字编辑必须可撤销共存——那是方案 A。
- 验收标准：仲裁结论写入本文件与 changelog；对应测试与文案同步；不再有「复审按首审口径判不闭环」的悬置。

## R6-U2（medium）coding 表单内容未变、仅 updated_at 刷新即废掉既有撤销

- 对应复审条目：『coding_values 从 (v1, 16:01:01) 变为 (v1, 16:01:02)、业务值相同、journal rows=2、screen/fulltext 撤销 409；同一秒重复保存则无此问题』。
- 位置：`coscreen/coding_evidence.py:300,311`（save_form 无条件刷新 updated_at）；`coscreen/workspace_undo.py:69-71,163-185`（快照含 updated_at 且 _guard_state 参与比较）；`custom_frontend/index.html:8125`（#cbSave 无 dirty 门禁）。
- 修复指示：
  1. save_form 增加业务值 dirty 检查：coding_values/notes/images 与库内一致时不刷新 updated_at、不追加 coding journal（幂等保存）。
  2. 双保险：`_guard_state` 比较面剔除 `coding_values.updated_at`（它是实现细节，不是语义状态）。
  3. 前端 #cbSave 在表单 dirty 为 false 时禁用或点击提示无变更。
- 验收标准：复审 attack_u3.py AP1 场景重放 → ≥1 秒后无变更重复保存：journal rows=1、既有 screen/fulltext 撤销仍可用；有变更保存照常产生 journal。

## R6-U3（low）U3-P8 残留：undo 路径脱离引用的上传永不进入回收窗口

- 对应复审条目：『undo 删除 coding_note_images 行但不复位 attached；undone 条目未被 superseded 且 after_json 仍含路径，release_detached_uploads 的 LIKE 判定持续保留文件；30 天 cleanup_staging 只收 attached=0；低频编辑文章事实上永不回收』。
- 位置：`coscreen/workspace_undo.py:294-317`（release_detached_uploads 仅由 save_form 调用、查询不过滤 undone）与 undo() 路径（约 320-408 无 attached 复位）。
- 修复指示：undo 补偿删除 coding_note_images 行后，对该条目 after_json 内的上传路径执行 attached 复位判定（若无**非 undone** 的活跃引用即置 attached=0）；release_detached_uploads 的 LIKE 保护集查询排除 `undone=1` 的条目。
- 验收标准：复审 attack_u3.py AP4 场景 + 一次后续普通保存 → attached=0 且 GC 可回收；正常保存移除图片的主路径不回归。

## R6-U4（low）latest()/状态接口仍是读写连接（自愈建库副作用，与 5693b79 提交说明不符）

- 对应复审条目：『先 DROP decisions.tags 再调 latest() 会自愈补回；对不存在的库调用 latest() 创建 290,816 字节、32 张表的 DB』。
- 位置：`coscreen/workspace_undo.py:254-259`（latest 用 coscreen.db._connect）；`coscreen/db.py:552-566`；`custom_backend/workspace_undo_api.py:144-152`。
- 修复指示：latest()/status GET 走只读连接（`sqlite3.connect(f"file:{path}?mode=ro", uri=True)`），打开前检查文件存在性，不存在返回 None/空态；DDL 自愈仅保留在写路径（begin_save/undo）。
- 验收标准：复审 attack_u3b.py AP12/AP7 重放 → DROP 后 tags 不被补回、不存在的库不产生新文件；既有读写路径自愈不回归。

## R6-U5（low）status 只返回 latest 不跑 snapshot_matches，前端 409 后可反复点击

- 对应复审条目：『守卫误报根因已修，但 status 与守卫一致性未收口：真实下游变更下 latest 仍返回 entry_id=1，前端 409 分支只 refresh，同一 409 可无限复现』。
- 位置：`custom_backend/workspace_undo_api.py:145-152`；`coscreen/workspace_undo.py:225-233`；`custom_frontend/workspace_undo.js:22-34,84-96`。
- 修复指示：status GET 增加 `guard_ok`/`undo_available` 字段（服务端跑只读版 snapshot_matches 判定，只读连接与 R6-U4 共用）；前端按 `undo_available=false` 禁用按钮并在 guard 失败时提示需刷新，409 后 refresh 拿到 disabled 即停止循环点击。
- 验收标准：复审场景（save_decision 后写 coding value）→ status 返回不可撤销、按钮禁用；正常可撤销路径行为不变。

## 【已修·集成轮】小项

- i18n（low）：`MISSING_EVIDENCE_FILE` 中文单语文案已注册 `interface_locale.js` 十语言双键块，commit 993ea9f。泳道复核 grep 命中即可。
