# 木笔 ReviewFlow 召回证书与停止规则方法学报告（v1）

- 版本：v1 · 2026-10-30 · 随 2.1.4 发布（W7 空白格泳道）
- 被描述实现：`coscreen/al/stop_certificate.py`（召回证书）、`coscreen/al/stop.py`（streak，已降级为进度提示）、`coscreen/al/stop_registry.py`（规则注册表）与只读检查端点 `GET /api/tasks/{id}/al/stop-certificate/check`（`custom_backend/main.py`）
- 原始数据：本目录随库分发 S1 停止准则理论推导、90 场 AL 升级验证批与 20 场停止规则回归的**原始研究数据**（清单见 §4，逐文件 sha256 核验的原样副本）
- 结构：仿统计内核数值验证报告（`out/benchmark/验证报告_v1.md`）九节结构

---

## 1. 统计原理（超几何上界）

召回证书回答的问题是：**在已筛完一部分后，能否以可量化的置信度声称"剩余未筛文献中的相关文献数 ≤ k"，从而总召回仍 ≥ R"？**

做法是对剩余未筛池做一次**均匀无放回随机抽样**：从池中 m0 条抽 m 条、全部人工判读。设 X 为子样中新发现的相关文献数，则

> X | R0=r ~ Hypergeometric(m0, r, m)

**引理（随机占优）**：HG(m0, r, m) 随 r 随机递增，故检验"R0 ≤ k"的 p 值在最小违例 r = k+1 处取上确界（理论推导与数值网格验证见 `notes/stopping_rules.md` §2.1 引理 2.1 / 定理 2.1）。

**证书判定**：观测到 x=0（子样零新相关）时

> P(错误发证) = P(X=0 | R0=k+1) = Π_{j=0}^{k} (1 − m/(m0−j)) ≤ α ⟹ 声明 R0 ≤ k

产品实现（`stop_certificate.py::certified_confidence`）用 `math.log1p` 累加该乘积再取指数，置信度 = 1 − 该上界，**随 k 与实际抽验量逐次如实计算**（见 §6 第 3 条：不虚标固定 1−α）。

**为什么"剩余 ≤ 0"的零目标不可负担**：k=0 时最难备择是 r=1，P(X=0)=（m0−m)/m0 ≤ α ⟺ m ≥ (1−α)·m0——要"证明剩余为零"随机子样必须抽掉约 95% 的池（推论 2.2，"零证书 ≈ 普查"）。因此产品采用**召回目标型**阈值 k_t 随已发现数增长（§2），这是唯一可负担的证书形态；最小子样量前沿 m* ≈ m0·(1−α^{1/(k+1)})（相对误差 <1%，§2.3 前沿式）。

**与排序模型解耦**：证书只依赖剩余池的随机抽样，不使用模型分数/排序——S1 分析发现 AL 排序使 streak 类规则更不安全（§6 第 5 条），故本证书刻意与任何模型输出无关。

## 2. 参数含义（S1 全量分析推荐值）

| 参数 | 默认值 | 含义 |
|---|---|---|
| R（recall_target） | 0.95 | 召回目标：证书通过（且声明成立）时总召回 ≥ 95% |
| α（alpha） | 0.05 | **名义**显著性水平。当前实现中 α 不进入判定式：置信度由内核函数 `certified_confidence` 逐次计算，**界面与 API 均不输出该数值**（见 §3） |
| φ（sampling_fraction） | 0.30 | 抽样比例：需人工判读的最少抽验量 m = ceil(池大小 × φ) |
| look_fractions | (0.50, 0.70, 0.85, 0.95) | 建议检查进度点（已筛百分比）；是否到达由调用方（API/前端）判断 |
| k_t | floor(found × (1−R)/R) | 容许的剩余相关文献上限；found=已发现相关数，found≤0 时 k_t=0 |

**置信度数值表（产品内核实跑，F-04-01）**：池 100、抽验 30 时，k_t=0..8 的错误发证上界为
0.7000 / 0.4879 / 0.3385 / 0.2338 / 0.1608 / 0.1100 / 0.0749 / 0.0507 / 0.0342——
即置信度 0.30 / 0.51 / 0.66 / 0.77 / 0.84 / 0.89 / 0.93 / 0.95 / 0.97。**k_t=0（尚未发现相关）时置信度仅 0.30；k_t≥8（已发现 ≥152 条相关）时才 ≥95%**。筛序早期证书给不出有意义的保证，这是数学事实而非实现缺陷。注意：该数值目前**不经界面或 API 展示**（见 §3 披露），可经内核函数 `certified_confidence` 或本表核查。

## 3. 使用指南（产品内）

产品内置三条停止规则（`stop_registry.py`，`GET /api/tasks/{id}/al/stop-rules`）：

| 规则 | 状态 | 语义 |
|---|---|---|
| manual 手动停止 | — | 用户自行判断 |
| streak 连续排除提示 | **已废弃**（deprecated） | 连续排除 N 条后提醒，仅进度提示，**无安全保证**，默认关闭（`AL_STOP_STREAK_EXCLUDE=0`） |
| certificate 召回证书 | **推荐**（recommended） | 以如实计算的超几何置信度支持"总召回 ≥95%"声明 |

操作流程（初筛页停止建议面板，`custom_frontend/al_stop_panel.js`）：

1. 面板定期调用只读检查端点（端点本身不抽样、不写筛选决策；首次读取某筛员进度时会按既有机制懒初始化其空数据库文件）；
2. 已筛进度到达检查点（50%/70%/85%/95%）时，面板提示：从未筛池随机抽取 m = ceil(池×30%) 条判读。**样本选取由产品代劳**：面板的「开始抽验」按钮以 crypto 随机洗牌从当前未判读条目中选出 m 条并置顶队列（`al_stop_panel.js` 的 shuffle/startBtn 逻辑），**判读始终由人工完成**；检查端点本身不做任何选取；
3. 判读完成且**未发现新的相关文献**、抽验量足 → 证书通过。通过文案显示容许剩余相关数 k_t 并明示「置信度按超几何随已发现相关数与抽验量变化，并非固定 95%」——**当前界面与检查端点均不输出置信度数值**（`GET /api/tasks/{id}/al/stop-certificate/check` 响应无 confidence 字段），具体数值可经内核函数 `certified_confidence` 或本报告 §2 表核查；
4. 抽验中发现任何新相关文献 → 不满足停止条件，继续筛选（该结果即池的违例证据）。

证书是**停止建议**而非强制：最终是否停筛始终是研究者决定（manual 规则始终可用）。

**产品呈现层与证书数学的已知差距（如实披露，均非本组触碰域）**：

- 停止规则注册表 API（`GET /api/tasks/{id}/al/stop-rules`，数据源 `coscreen/al/stop_registry.py`）中证书条目的描述仍为旧口径「以 ≥95% 置信度保证总召回 ≥95%」——该表述在 k_t 较小时高估置信度（正确口径见 §2/§6；r7 钉子明确排除该文件，属后续后端泳道修正项）；界面词典 `custom_frontend/interface_locale.js` 亦残留一条「证书通过 ✅ 以 ≥95% 置信度……」旧词条（FL4 词典域）；面板实际渲染以 `al_stop_panel.js` 内嵌文案为准（含「并非固定 95%」限定）。
- 内核集中判定入口 `should_stop()`（含置信度计算调用）当前**无产品调用方**：面板按前端抽验计数直接判定通过（`newRelevant == 0`，与内核三路径语义一致但未复用同一函数），`certified_confidence` 因此只经由内核函数本身与本报告 §2 数值表可核查。
- 实测观察：筛员数据库文件损坏时，检查端点返回 500（a080fde 基线既有行为，非本次改动引入；正常失败路径未登录 401、未知任务 404、缺参 422、空语料 200，均非 500）。修复属后端泳道范围，在此如实记录。

## 4. 数据来源与分发清单

以下原始研究数据从研究仓 `research/` 树原样复制（复制时逐文件 sha256 核验一致），路径映射：`research/X` → `docs/methodology/X`：

| 分发文件 | 内容 |
|---|---|
| `notes/stopping_rules.md` | S1 停止准则理论推导全文（streak 闭式界、超几何证书、Pocock/OBF α-spending、组合规则、Kempny 2026 复现语义） |
| `notes/check_d1_streak.py` … `check_d4_combined.py`（4 文件） | 理论推导的数值自检脚本（闭式 vs 蒙特卡洛、证书覆盖、估计量无偏、组合规则闭环，覆盖记录见 notes §5 表） |
| `results/al_upgrade_verification/REPORT.md` | 90 场 AL 升级验证批报告（2026-10-01）+ 20 场停止规则回归 |
| `results/al_upgrade_verification/verification_results.csv` | 主批 90 场逐场结果（3 配置 × 6 数据集 × 5 种子） |
| `results/al_upgrade_verification/verification_summary.csv` | 按（配置, 数据集）聚合 + 宏平均行 |
| `results/al_upgrade_verification/stoprule_runs.csv` / `stoprule_summary.csv` | 停止规则回归 20 场 × streak{10,25,50} 逐场与汇总 |
| `results/al_upgrade_verification/run_verification.py` / `verification_meta.json` | 批脚本与配置回显（含确定性说明、环境版本） |
| `sim/`（11 个顶层文件 + `results/` 3 文件） | 研究模拟引擎（replay.py 等）与停止规则后评估实现 `stopping_rules.py`（产品 `stop_certificate.py` 的 k_t 同式来源），以及研究测试（`test_stopping_rules.py` 单文件 26 用例；`sim/` 全目录合计 200 用例——实测 `pytest --collect-only`） |
| `data/`（3 文件） | 公开基准语料：`synergy/Nelson_2002.csv`（SYNERGY v1，366 条 / 80 纳入）、`synergy/summary.csv`（逐集 sha256 与统计）、`provenance.md`（来源与许可全文） |
| `fixtures/`（9 文件） | 复现输入与期望：§8(b) 合成夹具（gen_simulate_fixture.py、corpus.csv、gold.csv、expected_simulate_mixed_s42.json）、§8(b2) 公开语料现跑（prepare_synergy_nelson.py、nelson_corpus.csv、nelson_gold.csv、expected_synergy_nelson_legacy_s1.json）、§8(c) D1–D4 运行器（run_d_check.py） |

分发文件共 **38 个**：研究原样 26 个（notes 5 + results 7 + sim 14，逐文件 sha256 与源一致）+ 公开语料 3 个（`data/`，sha256 与 SYNERGY summary.csv 记录一致）+ 本仓生成 9 个（`fixtures/`，实跑验证并用期望 JSON 锁定）+ 报告 1 个。注：`fixtures` 与 `data` 中部分文件（如 nelson_corpus.csv/gold.csv）是派生输入，可随时由对应脚本确定性重建。

**入库说明（r8）**：`data/` 三份语料是**随库分发的研究输入**，不是用户数据，必须随检出一起到位。仓库根 `.gitignore` 的 `data/` 规则（第 11 行）匹配任意层级的同名目录，曾把本目录整体吞掉——`git add` 静默跳过，干净检出后 `data/` 缺失，本报告 §4 与 W7 文档钉子随之变红。现已在第 11 行后加否定行 `!docs/methodology/data/`；`build/tests/test_w7_recall_certificate_docs.py::MethodologyCorpusIsCommittable` 以「静态规则重放 + `git check-ignore` + `git add --dry-run`」三层钉住，改坏任一层即红。否定规则只针对本目录，真正的用户数据目录（`data/`、`instance/`）仍被忽略。

数据集：主批使用的 6 个英文 SYNERGY 基准集（Nelson_2002、Donners_2021、van_Dis_2020、Bos_2018、Moran_2021、Wassenaar_2017）。其中 **Nelson_2002 的公开语料随库分发**（`data/synergy/`，SYNERGY v1 整包 CC0 1.0）用于 §8(b2) 现跑复现；其余 25 个 SYNERGY 集与全部中文语料**不随库分发**（见 §7）。

**未分发项（如实说明）**：

- S1 全量分析（650/650 场、26 数据集）的完整结果目录（`research/results/s1_full*`）不在本次取数范围内；"650/650 召回保证 100% 达成、3/26 数据集提前停止节省 11–34%"这一结论引自产品交付说明（`stop_certificate.py` 模块注释），本仓库内可核查的支撑是其理论推导（`notes/stopping_rules.md`）与 20 场停止规则回归（本目录）。
- 研究树 `research/notes/` 另有 4 个文件未分发：`bilingual_design.md`、`dual_oracle_design.md`、`literature_review.md`、`references.md`（研究侧设计/文献材料，与证书方法无直接依赖）。
- 因此随库 `sim/` 内保留的 3 处**研究树路径引用为悬空**（不改动逐字节一致的副本）：`sim/bilingual.py` 头部指向 `research/notes/bilingual_design.md`、`sim/dual_oracle.py` 头部指向 `research/notes/dual_oracle_design.md`（两者均在上列未分发项中）、`sim/README.md` 指向 `docs/research/2026-09-25-al-research-program.md`（该目录不在本仓库）。阅读时按"研究树原文、未随库分发"理解。
- 其余 25 个 SYNERGY 语料与中文语料（`research/data/chinese/`）体积或授权范围所限未分发。

## 5. 结果总览（90 场 + 20 场）

**主批（3 配置 × 6 数据集 × 5 种子 = 90 场，全部 r95≥0.95）**——WSS@95 宏平均（详细逐集表见 `results/al_upgrade_verification/REPORT.md` §3.1 / `verification_summary.csv`）：

| 配置 | 宏 WSS@95 | 6 集合计每种子所需筛查量（均值；legacy 为逐种子恒值，其余给 5 种子范围） |
|---|---|---|
| legacy_nb（旧产品默认） | +0.148 | 25,732 条（恒值） |
| tuned_nb | +0.495 | 8,978 条（8,966–8,989） |
| svm_balanced（新默认推荐） | **+0.580** | **7,506 条（7,441–7,550；均值较旧配置 −71%）** |

- 旧配置在 3/6 低/中患病率集（van_Dis、Bos、Moran）WSS@95 为**负**（劣于随机序）；新配置无任何数据集劣于随机。
- 唯一回归点：小集高患病率 Donners_2021（N=258）上新配置不如旧配置（svm −36% 筛查量）。
- 90 场逐场数据与种子方差、确定性说明（legacy 多种子逐位一致）见分发 CSV 与 `verification_meta.json`。

**停止规则回归（svm_balanced × S1 试点 4 集 × 5 种子 = 20 场）**——streak 家族在新配置轨迹上仍不安全：

| streak n | 宏 P0（零遗漏率） | 宏平均漏检 | P0≥0.95 达标数据集 |
|---|---|---|---|
| 10 | 0.000 | 17.65 | 0/4 |
| 25 | 0.000 | 7.20 | 0/4 |
| 50 | 0.200 | 3.05 | 0/4 |

"负模型贡献"仍存在：Nelson streak_25 闭式 P_miss=0.127 vs 经验遗漏率 1.000（贡献 −0.873）。S1 的两条产品建议（streak 默认关闭、召回证书承担安全职责）在新配置下经回归确认无需修改；证书族规则只依赖剩余池抽样，与排序模型无关。

## 6. 边界与如实披露

1. **Pocock 序贯监测目前只在文档/参数层声明，未进入产品判定**。理论推导（`notes/stopping_rules.md` §2.2）为多次检查定义了 Pocock（α/L 等分）/OBF 型 α-spending 预算并证明了任意依赖下的联合有效性（定理 2.2）；产品参数 `look_fractions` 也确实给出 4 个检查点。但 `should_stop()` 的实际判定是**单次抽样的零相关检查**（见下条），**并未按 look 数切分 α**：若用户在多个检查点反复通过证书并把每次的面值置信度都当真，跨检查点的总体错误概率没有被控制在 α——理论上的序贯校正（Bonferroni/Pocock 预算）在产品代码中未实现。模块注释中"Pocock 序贯监测"表述描述的是 S1 理论基础，不是已实现的多重比较校正。
2. **`should_stop` 的实际语义 = 子样本零相关**：抽验量足（`sample_size ≥ m`）且本次随机抽验**零新相关**（`sample_new_relevant == 0`）即通过；发现任何新相关即不通过。判定不依赖模型分数，也不依赖检查点位置（检查点只是提示何时抽样的建议）。
3. **α 是名义参数，置信度不虚标**：`certified_confidence` 按超几何上界逐次如实计算（§2 数值表：k_t 小时可能远低于 95%），界面与 API 明示"并非固定 95%"；α=0.05 目前仅作为设计目标回显，不作为判定阈值。这是对早期版本虚标固定 1−α 的修正（缺陷编号 F-04-01）。
4. **streak 已降级为进度提示**：S1 闭式界证明随机序下任何实用阈值（10–100）在低患病率集近乎必然遗漏（Bos 需 streak≈2005 才达 95% 安全），且 AL 序使长排除段与模型出错处内生耦合——streak 不能承载安全职责，默认关闭。
5. **停止建议 ≠ 强制**：证书通过只是"剩余相关 ≤ k_t"的统计声明；数据集层面"文本信号弱"（如 Moran 型）时任何配置的排序都只能小幅优于随机，省力承诺应由证书逐项目触发情况给出，不应预售。
6. **呈现层滞后于证书数学**：置信度数值当前不经界面/API 输出（通过文案只含 k_t 与「并非固定 95%」提示），停止规则注册表描述与界面词典仍残留「≥95% 置信度」旧口径（详见 §3 披露）——数学口径以本报告与内核实现为准。

## 7. 局限

1. 主批为 **6 集 × 5 种子**，不是 26 集全量；如需上线前全量确认可按分发脚本扩展（`run_verification.py --datasets`）。
2. 升级参数（平衡 ratio=9.8、SVM C=0.11）在**英文 SYNERGY** 上调优；中文/双语语料上的最优性未验证（证书本身与语言/模型无关）。
3. 证书在筛序早期（已发现相关少、k_t 小）给不出高置信度——这是超几何数学的结构性代价（§1），不是可通过工程手段消除的缺陷；"证明剩余为零"≈ 普查成本。
4. 研究数据产自研究环境（Python 3.12.3 / numpy 2.5.3 / scipy 1.18.1 / sklearn 1.9.1，见 `verification_meta.json`），与产品发布环境版本不同；90 场结果未在产品 venv 重跑（复现路径见 §8）。
5. SYNERGY 语料与 S1 全量（650 场）原始结果目录不随库分发，§4"未分发项"所列结论只能溯源到产品交付说明。
6. 本报告为方法学披露与数据分发，不构成对任何具体研究项目停止时点的裁决；停筛决定及其在 PRISMA/报告中的表述由研究者负责。

## 8. 复现

**(a) 证书数值自检（零依赖，产品 venv 即可）**——§2 数值表逐位复算：

```bash
python -c "from coscreen.al.stop_certificate import RecallCertificate as C; c=C(); print([round(1-c.certified_confidence(100,k,30),4) for k in range(9)])"
```

预期输出即 §2 的错误上界表（`[0.7, 0.4879, 0.3385, 0.2338, 0.1608, 0.11, 0.0749, 0.0507, 0.0342]`；另可验证 `C().threshold(152) == 8`）。

**(b) 产品 AL 模拟基准（随库确定性夹具，交付环境已实跑）**。入口是 `python -m coscreen simulate`（经 `coscreen/__main__.py`）——注意 **`python -m coscreen.cli simulate` 是静默无操作**（`cli.py` 无 `__main__` 守卫，实测 exit=0 零输出），勿用：

```bash
python docs/methodology/fixtures/gen_simulate_fixture.py   # 生成 corpus.csv / gold.csv（种子 20261030，确定性）
python -m coscreen import  --db <tmp>/fixture.sqlite --file docs/methodology/fixtures/corpus.csv
python -m coscreen simulate --db <tmp>/fixture.sqlite --gold docs/methodology/fixtures/gold.csv \
    --strategy mixed --seed 42
```

实测输出（两次重跑逐位一致，留档于 `fixtures/expected_simulate_mixed_s42.json`）：205 条 | 相关 12 | 达 95% 召回需筛 88 条 | WSS@95=0.5207 | R@95=1.00 | 平均发现位次 57.83；随机基线 WSS@95=0.0085 / 需筛 193 / 位次 113.08。**如实说明**：该语料为分发研究引擎的合成基准（中英混合、种子化），**不是公开数据集**——SYNERGY 公开语料不随库分发，且本报告构建环境无外网（实测 GitHub raw 不可达），蓝图设想的"公开数据集现跑复现"以"随库确定性夹具现跑"替代；取得 SYNERGY 语料与 §4 研究环境后可按 (c) 复跑完整 90 场批。对自有真实数据集，同命令换 `--db/--gold` 即可。

**(b2) 公开数据集现跑复现（SYNERGY v1 · Nelson_2002，交付环境已实跑）**。这是蓝图要求的"用公开数据集经 `cli.py simulate` 现跑"的落地项：

```bash
python docs/methodology/fixtures/prepare_synergy_nelson.py      # 校验 sha256 → 生成 nelson_corpus.csv / nelson_gold.csv
python -m coscreen import  --db <tmp>/nelson.sqlite --file docs/methodology/fixtures/nelson_corpus.csv
python -m coscreen simulate --db <tmp>/nelson.sqlite --gold docs/methodology/fixtures/nelson_gold.csv \
    --strategy max --seed 1
```

**每次请用全新的 `<tmp>/nelson.sqlite`**：对同一数据库重复 `import` 会触发同键更新并可能新增 1 条同内容哈希记录（产品既有去重行为，a080fde 基线同样如此），之后的 `simulate` 会因黄金标准缺该键而退出码 2。

实测输出（留档 `fixtures/expected_synergy_nelson_legacy_s1.json`）：**366 条 | 相关 80 | 达 95% 召回需筛 242 条 | WSS@95 = 0.2887978142076502 | R@95 = 0.95 | 平均发现位次 112.0**；随机基线 WSS@95=0.1030 / 需筛 310 / 位次 171.73。与 `results/al_upgrade_verification/verification_results.csv` 的 `legacy_nb / Nelson_2002 / seed=1` 行**六项逐位一致**（N、K、n_to_95、wss95、r95、ttd_mean，脚本比对全部 MATCH）。这使 90 场批次中至少一集可在交付环境端到端复核。

语料与许可：SYNERGY v1（`github.com/asreview/synergy-dataset` releases/v1，整包 **CC0 1.0**；持久存档 DOI 10.34894/HE6NAQ）。随库副本 sha256 `f451f025…f1a40b`，与 `data/synergy/summary.csv` 记录一致。来源、许可与逐集元数据见 `data/provenance.md`。键映射：OpenAlex Work ID 末段（`https://openalex.org/W…` → `W…`），因产品键契约禁止 `/`。

**(b3) 合成夹具冒烟（随库确定性，与 (b2) 互补）**：入口是 `python -m coscreen simulate`（经 `coscreen/__main__.py`）——注意 **`python -m coscreen.cli simulate` 是静默无操作**（`cli.py` 无 `__main__` 守卫，实测 exit=0 零输出），勿用：

```bash
python docs/methodology/fixtures/gen_simulate_fixture.py   # 生成 corpus.csv / gold.csv（种子 20261030，确定性）
python -m coscreen import  --db <tmp2>/fixture.sqlite --file docs/methodology/fixtures/corpus.csv
python -m coscreen simulate --db <tmp2>/fixture.sqlite --gold docs/methodology/fixtures/gold.csv \
    --strategy mixed --seed 42
```

实测输出（两次重跑逐位一致，留档 `fixtures/expected_simulate_mixed_s42.json`）：205 条 | 相关 12 | 达 95% 召回需筛 88 条 | WSS@95=0.5207 | R@95=1.00 | 平均发现位次 57.83；随机基线 WSS@95=0.0085 / 需筛 193 / 位次 113.08。该语料为研究引擎的**合成基准（非公开数据）**，仅用于零依赖冒烟；公开基准以上面 (b2) 为准。同上，(b3) 也须每次用全新数据库。

**(c) 理论数值自检与研究批全量重跑**（需 numpy/scipy 与若干 SYNERGY 集，见下）：

- D1–D4 自检脚本已随库分发（`notes/check_d1_streak.py` … `check_d4_combined.py`），覆盖记录见 `notes/stopping_rules.md` §5。**运行前置按脚本区分**：D1/D2/D3 无路径依赖，裸跑即可；**D4 会 `import stopping_rules` / `check_d1_streak`**，其 :19-20 行的 `sys.path.insert(0, "research/sim")` 是研究树布局，分发后不成立（实测裸跑 `ModuleNotFoundError: No module named 'stopping_rules'`）。因分发文件须与源逐字节一致，故不改副本，改用随库运行器：

  ```bash
  python docs/methodology/fixtures/run_d_check.py d4        # 或 d1/d2/d3，省略参数跑全部四组
  ```

  等价于 `PYTHONPATH="docs/methodology/sim;docs/methodology/notes"` 前置。交付环境实测 D1–D4 均为 `RESULT: ALL PASS`。

- 批脚本 `results/al_upgrade_verification/run_verification.py` 在本仓库布局下需先设 `PYTHONPATH=<仓库根>`（实测裸跑报 `ModuleNotFoundError: No module named 'coscreen'`；`PYTHONPATH=.` 后 `--help` 正常——研究布局中 `sim/replay.py` 以 `parents[2]` 定位仓库根，置于 `docs/methodology/sim/` 后该推导指向 `docs/`，故需显式指定）；完整 90 场重跑另需研究侧环境与其余 SYNERGY 语料。

## 9. 结论

木笔 ReviewFlow 的安全停止建立在**显式统计证书**上：随机子样 × 超几何上界，置信度在内核中逐次如实计算、与模型排序解耦；streak 家族经闭式界与 90+20 场仿真证实不能承载安全职责，已降级为默认关闭的进度提示。**随库可核查**：S1 闭式推导与 D1–D4 自检脚本（`run_d_check.py` 一键复核，交付环境 ALL PASS）、90/20 场原始数据、内核置信度数值表（§8(a) 可复算）、**公开基准 SYNERGY Nelson_2002 现跑复现且与 90 场批次对应行六项逐位一致**（§8(b2)）、合成夹具冒烟（§8(b3)）。**尚不可核查（如实）**：S1 全量 650/650 与 3/26 节省比例（仅存于产品交付注释，全量结果目录未随库分发）；置信度数值的界面/API 展示（当前不存在，§3/§6 披露）。最后重申边界：Pocock 序贯 α-spending 目前停留在理论与参数层，产品判定是单次抽样的零相关检查；跨检查点的多重比较校正未实现，使用者不应把多次检查的面值置信度叠加当真。
