# r6 复审修复指示 —— 去重引擎（dedup / normalize / cli）

> 基线：集成轮已把 `fix/2.1.4-r5-ingest`（1612a6d）合入 `fix/2.1.4`
> （复审【集成缺口·阻断】条目闭环），并直接修复标注【已修·集成轮】的小项。
> 本文件剩余条目为泳道待落实的大项，全部提交到 `E:/rf214-integration`（fix/2.1.4）。
> 复审证据中所有 CLI 实测复现均在 r5-ingest 分支代码上完成，现在已在基线内。

## R6-D1（high）键相等的自动合并仅看「同键 + 标题 token_set_ratio>=90」

- 对应复审条目：『键相等的自动合并只按 zotero_key 相等 + 标题 token_set_ratio>=90 认定同一文献，完全不看作者/年份』（high）与『U1-P3 残余窗口：同来源键+双方 DOI 为空+一词之差标题仍静默合并并覆盖』（medium）——同一问题的两面。
- 位置：`coscreen/dedup.py` `_key_merge_confirmed`（合并后约 135-146 行）；CLI 路径 `coscreen/cli.py:180-190` 附近。
- 复现要点（复审实测）：两条 `ID - 1`、作者 Smith/Jones、标题 `A phase 2 trial...` vs `A phase 3 trial...`（token_set_ratio=93.18）→ find_duplicates unique=1、exact_key 自动合并；DB 只剩 phase 3/Jones，phase 2 无提示被覆盖。women/men、retraction、词序反转标题同样 exact_key。Web /import 第二条 outcome=exact_staged、candidates=[]，phase 3 既非候选也无法独立激活。
- 修复指示：
  1. `_key_merge_confirmed` 收紧为：键相等时**标题规范化后完全相等**，或**标题相似达标且首作者姓氏一致 + 年份一致（双方均有年份）**，才允许自动合并；其余键相等情形一律产出 `exact_key_conflict` 候选（两条都进入筛选，不覆盖）。
  2. 模糊层（标题相似但键不同）的 90 阈值人工候选语义保持不变——同一阈值证据在键路径上不再允许自动合并。
  3. 同步核对 Web 路径 `coscreen/review_imports.py:196-211` 的合并判定与 CLI 判定共用同一函数（不得两处口径分叉）。
- 验收标准：phase2/phase3、women/men、retraction、词序反转、`Metformin and renal outcomes...` vs `...hepatic outcomes...`（同键无 DOI）全部产出 key_conflict 候选、DB 两行共存、无覆盖；全同记录仍自动合并。补「一词之差落在冲突侧」的回归测试（现有 `test_same_key_title_adjudicates_when_doi_absent` 只钉住全同/完全不同两端）。

## R6-D2（high/medium 三条同族）auto-* 键公式变更断裂既有库重导幂等

- 对应复审条目：『auto-* 键公式从 sha1(doi|title) 改为 sha1(content_hash|姓氏|年份) 且无迁移』（medium）、『键方案变更断裂既有库重导幂等（回归）：旧库 auto-ab84b05d60c2 重导同文献 → 新增 1 条重复行』（high，解析器泳道亦报）、『living_review 同样复现』（medium）。
- 位置：`coscreen/normalize.py` `build_zotero_key`；`coscreen/cli.py` `stabilize_conflicting_keys` 调用（`cli.py:185-191` 附近）；`custom_backend/main.py:1403-1435` Web `_fallback_id` 映射；`coscreen/db.py:646-650`（『重复导入同一文件是幂等的』承诺）。
- 复现要点（复审实测）：按 2.1.3 公式预置旧键行后，同一无 ID/无 DOI RIS 重导 → 旧 2 行 + 新 auto-dfa1e3aad012 等 2 行 = 4 行，输出『新增 2 条，更新 0 条』，无任何重复/候选提示。living_review 同样复制新行。
- 修复指示（三选一，按成本递增，推荐 2）：
  1. 迁移命令：新增 `migrate-auto-keys` CLI/管理端点，把旧 `auto-<sha1(doi|title)[:12]>` 行按内容重算新键并改写主键（含引用表级联）。
  2. 导入时旧键兼容：`stabilize_conflicting_keys`（CLI 与 Web `_stabilize_generated_import_keys` 双路径）在生成新 auto 键前，对库内已有行做**内容匹配**——按 DOI 相等，或（规范化标题相等 + 首作者姓氏相等 + 年份相等）命中旧 `auto-*` 行时沿用旧键走更新路径，保持身份连续。
  3. 回退键公式为 2.1.3 的 sha1(doi|title)（放弃 r5 的姓氏/年份输入）——代价是 R6-D4 的同姓覆盖问题回到旧形态，不推荐。
- 验收标准：复审实测脚本重放——旧公式键库 + 同内容重导 → 行数不变、无新行、无候选告警；CLI 与 living_review 双路径一致；补跨版本重导回归测试。

## R6-D3（medium）`_is_name_initial` 把任意 1–2 字母 token 当缩写，姓氏取反

- 对应复审条目：『first_author_surname 对“名 姓”且姓为 1–2 字母的姓名取到名：John Wu→john、Wei Li→wei、John Smith Jr→john smith』。
- 位置：`coscreen/normalize.py:204-207,247-267`（合并后行号约 220-290）。
- 复现要点（复审实测）：同标题 'Identical Title For Author Order Test'、'John Wu' vs 'Wu, John' → 2 行、无模糊候选（fuzz.ratio=0），首审 U1-P2/P11 的同标题真重复漏配仍成立；'Wei Li'/'Li Wei'/'Wang Wei' 同内容/年份下生成同 auto 键，跨批次互相覆盖。
- 修复指示：
  1. `_is_name_initial` 仅在 token 带点（"J."）或前一个 token 已按逗号语法识别为姓氏（"Wu, John" 形态）时才视为缩写；裸 1–2 字母 token（中文拼音姓 Wu/Li/He 等）不得当缩写。
  2. `first_author_surname` 对无逗号的 "名 姓" 序列：末 token 为非缩写 token 时取末 token 为姓氏；处理 Jr/Sr/III 等后缀（剔除后再判定）。
  3. 该函数同时是 R6-D2 内容匹配与 auto 键摘要输入，修复后需与 R6-D2 联测。
- 验收标准：'John Wu'/'Wu, John' 同标题 → 姓氏同为 wu → 模糊候选命中；'Wei Li' 与 'Wang Wei' 不再生成同键。补姓氏抽取表驱动测试（含中文名、带点缩写、后缀）。

## R6-D4（medium）auto 键只含第一作者姓氏，同姓不同名 + 同标题/年份跨批覆盖

- 对应复审条目：『Smith, John / Editorial / 2020 与 Smith, Jane / Editorial / 2020 两个独立 RIS 先后导入 → 第二次「新增 0 条，更新 1 条」，DB 仅 1 行且 authors=Smith, Jane（U1-P6 残余）』。
- 位置：`coscreen/normalize.py:306-312`（键摘要输入）；`coscreen/dedup.py:374-407`（stabilize 的 compatible 判定只看标题）。
- 修复指示：
  1. 键摘要输入加入名的区分度（如 first_author_given_initial，或全量首作者串），使同姓不同名不再同键。
  2. `stabilize_conflicting_keys` 的 compatible 判定在标题相似达标外要求首作者姓氏一致（或全作者串一致），否则视为冲突加后缀。
  3. **键公式再变更必须与 R6-D2 的旧键兼容方案捆绑落地并一次性定案**，禁止再次无迁移改键。
- 验收标准：复审 John/Jane 两步导入场景 → DB 两行共存且第二条提示键消歧；同批内 occupied 后缀行为不回归。

## R6-D5（low/medium 两条同族）冲突后缀取自整文件 sha256:行号，字节变化即漂移

- 对应复审条目：『重导出后缀漂移致同文行累积：加 FN 前言重导出 → 新增第 3 行』（low）；『仅追加一个空行使文件字节变化后重导出，第二行消歧后缀变化，DB 同文 3 行且继续累积』（medium）。
- 位置：`coscreen/dedup.py:320-336` `_conflict_suffix_key` 依赖 `coscreen/parsers/ris_parser.py` `raw['_fallback_id']=f"{source_digest}:{record_no}"`；csv_parser 同构。
- 修复指示：后缀改用**记录内容标识**（如 sha256(content_hash|规范化标题|规范化作者串)[:12]，内容相同则后缀稳定），或把 fallback→key 映射持久化到库（表或 meta），不再依赖整文件字节摘要。
- 验收标准：U2-P4 场景首次导入两行；仅追加空行/FN 前言后重导出 → 行数不变（幂等）；CLI 提示语与实际稳定语义一致。

## R6-D6（medium）content_hash 分隔符歧义：sha1(f"{doi}|{title}")

- 对应复审条目：『DOI 10.1000/a|b + title c 与 DOI 10.1000/a + title b|c 得同一 content_hash 与同一 auto 键，CLI/living_review upsert 后 DB 仅 1 行（静默丢条目）』。
- 位置：`coscreen/models.py:12` `compute_content_hash`；`coscreen/normalize.py`、两个 parser 的调用。
- 修复指示：改用长度前缀 `sha1(f"{len(doi)}:{doi}|{title}")` 或分别哈希后拼接；与 R6-D2/R6-D4 的键方案一并定案、同批落地（见 R6-D4 第 3 点约束）。
- 验收标准：复审两条构造 RIS → 两个不同键、两行共存；正常文献键值回归测试更新后全绿。

## R6-D7（low）CLI 模糊/键冲突候选只打 stdout 不落库

- 对应复审条目：『脚本化/无人值守导入只看退出码时信号完全丢失（对照：living_review 有 title_review_groups、Web 有候选清单）；实测 wm.db 两行均无标记』。
- 位置：`coscreen/cli.py` `cmd_import`（`mark_pairs=exact_pairs` 分支）。
- 修复指示：候选对持久化——最小方案是把 fuzzy/key_conflict 对以候选态写入库（如 `articles.import_note` 或独立 `import_candidates` 表），或导出 `--report <path>` JSON（含 pairs 与 method）供脚本消费；退出码在存在未决候选时可加 `--strict-candidates` 开关置非零。
- 验收标准：复审 wm.db 场景重放 → 两行有候选标记或产出报告文件；无候选路径输出不变。

## 【已修·集成轮】小项清单（泳道复核确认即可）

- CJK 尾标点破坏 DOI 相等（U1-P5 残余，low）：`normalize._strip_doi_tail_punct` 已补 CJK 句读与 NFKC 折叠形态，commit b391900；实测 '10.1234/abc。' vs '10.1234/abc' 现判 exact_doi。复审建议的『DOI 不等但标题/作者强匹配时保留候选兜底』仍可作为 fuzzy 层增强考虑（DOI 均非空使模糊层被排除的规则是否放宽，由泳道定夺）。
- living_review 混合候选组标注失真（low）：`_candidate_review_groups` 分记账，mixed 前缀 + key_conflict 成员标记，commit b391900。**注意**：Web 路径 `custom_backend/main.py:_title_review_groups` 未混入键冲突边，暂无此问题；若后续 Web 候选清单引入 exact_key_conflict 边，需同构改造。
