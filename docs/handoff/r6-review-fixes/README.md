# r6 复审修复分派（集成轮产出）

日期：2026-10-27 · 分支：`fix/2.1.4`（工作树 `E:/rf214-integration`）

## 集成轮已完成（复审【集成缺口·阻断】两条 + 可直接修复的小项）

1. **合并 `fix/2.1.4-r5-ingest`（1612a6d）** → merge commit 2ce1318。
   复审确认去重引擎全部 r5 修复不在 fix/2.1.4 HEAD（merge-base 验证），
   merge-tree 预演 0 冲突后合入；U1/U2 修复恢复在场。
2. **找回 r5-upload 悬空三提交**（ba36b99/3311316/42a5a3b，分支被 reset、
   修复仅存 reflog）→ cherry-pick 为 9197221/c854809/21cb0cb，零冲突；
   U5-P1..P12 修复代码与 test_upload_security_regression.py 回到基线。
   **注意**：复审对 @42a5a3b 修复本身的缺陷见 upload-security.md R6-S1..S6。
3. **直接修复的小项**（各泳道复核确认即可）：
   - 去重引擎 b391900：CJK 尾标点破坏 DOI 相等（U1-P5 残余）、
     living_review 混合候选组标注失真（含前端组文案十语言）。
   - 文献解析器 ae0e6e1：U2-P1 Author 列名映射（high）、RIS 缺 ER 边界
     校验（U2-P8+中部吞并，high/medium）、GBK CSV 回退（low）、
     仅表头 CSV 的 CLI 警告（low）。
   - PRISMA fa12f16：_esc CR/LF 转义（U6-P8，high）、
     /api/prisma/generate 非法表单不产出导出物（契约缺口，low）。
   - 受保护撤销 993ea9f：MISSING_EVIDENCE_FILE 十语言词条（i18n，low）。
   - 回归钉 1d2e7e3：build/tests/test_r6_integration_regressions.py
     （17 个用例，覆盖上列全部修复）。
4. 全量测试：合并后基线 211 passed+38 subtests；修复后收口见各轮提交时输出。

## 泳道待落实（大项指示）

| 泳道文件 | 大项数 | 最高严重度 |
| --- | --- | --- |
| dedup-engine.md | 7（R6-D1..D7） | high |
| parsers.md | 4（R6-P1..P4） | medium |
| protected-undo.md | 5（R6-U1..U5，U1 需产品仲裁） | medium |
| collaboration.md | 8（R6-C1..C8） | medium |
| upload-security.md | 6（R6-S1..S6） | medium |
| prisma-form.md | 8（R6-F1..F9，F3/F8 已修） | high |

修复提交到本分支（fix/2.1.4），中文 conventional commit，逐条对应 U 编号。
