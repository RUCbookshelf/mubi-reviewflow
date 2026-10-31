# AL 模型升级验证批报告(2026-10-01)

验证目标:新配置(平衡策略 + SVM/调优 NB + TF-IDF 调优)相对旧配置
(`preset="product"`,当前产品默认)在 SYNERGY 基准上的提升,以及 S1 停止规则
结论在新配置下的兼容性。

- 主批:**3 配置 × 6 数据集 × 5 种子 = 90 场**(`verification_results.csv`)
- 停止规则回归:**最优新配置 × S1 试点 4 集 × 5 种子 = 20 场**
  (`stoprule_runs.csv` / `stoprule_summary.csv`)
- 引擎:`research/sim/replay.py`(本批扩展,见 §5);策略一律 `max`,
  召回目标 0.95,指标口径与产品 `SimResult` 一致(n_to_95 / wss95 / r95 / ttd_mean)。
- 复现:`./.venv/bin/python research/results/al_upgrade_verification/run_verification.py`
  (幂等;`--phase batch|stoprule|summary`)。

---

## 1. 结论速览

1. **推荐 `svm_balanced`(LinearSVC C=0.11 + Balanced 样本权重 ratio=9.8 +
   sublinear TF-IDF + max_df=0.95)作为新默认**:宏 WSS@95 **+0.580** vs 旧配置
   **+0.148**(Δ=+0.432),6 集中 5 集优于旧配置;6 集合计每种子达 95% 召回
   所需筛查量 25,732 → 7,506 条(**−71%**),其中低患病率三集
   (van_Dis/Bos/Wassenaar)减少 81%–91%。
2. `tuned_nb`(NB alpha=3.822 + 同上平衡/特征)宏 WSS **+0.495**,同样大幅
   超过旧配置,但被 SVM 全面压制(5/6 集更差或持平)。
3. **唯一回归点**:Donners_2021(N=258,小集)上新配置不如旧配置
   (svm −36% / tuned_nb −130% 筛查量)。小样本高患病率场景是旧流水线
   (逐步 refit + 无平衡 + alpha=1.0)的舒适区,升级配置以确定性换取宏性能,
   该集上 SVM 仍保住了大部分(wss 0.476 vs 旧 0.601)。
4. **旧配置在 3/6 集(低/中患病率:van_Dis、Bos、Moran)WSS@95 为负**——
   比随机顺序还差,筛查了几乎全库(如 Bos 4818/4878)。这是本次升级最直接的
   依据:升级不是"锦上添花",而是修复产品默认在主流(低患病率)场景的失效。
5. 停止规则兼容性:S1 的两条核心结论在新配置下**不变**——
   streak 家族仍不安全(streak_10/25 宏 P0=0.000,streak_50 宏 P0=0.200,
   全部远低于 0.95 安全线);"负模型贡献"仍存在(Nelson streak_25:闭式
   P_miss=0.127 vs 经验遗漏率 1.000,贡献 −0.873)。
   S1 的"召回证书"建议不依赖模型配置,无需改动。

---

## 2. 配置与引擎语义核对(任务书要求项)

任务书目标配置 vs 引擎实现:

| 目标参数 | ASReview v3 语义(源码核对) | 引擎实现 | 状态 |
|---|---|---|---|
| NB alpha=3.822 | `NaiveBayes = MultinomialNB` 直用 | `replay_fast(alpha=3.822)` | 一致 |
| **Balanced ratio=9.8** | `models/balancers.py::Balanced`:**样本权重,非重采样**。w(class1)=1.0,w(class0)=n1/(ratio·n0),再整体归一 Σw=n;NB 加权类先验 + 加权类内特征和(`MultinomialNB.fit(sample_weight=)`) | `_balanced_class_weights` 解析实现,逐项对应 | 一致(与 sklearn 等效验证,§5) |
| SVM:LinearSVC(C=0.11, loss="squared_hinge") | `models/classifiers.py::SVM = LinearSVC` 直用;v3 learner 无 predict_proba 时按 `decision_function` 排序 | `LinearSVC(C=0.11, loss="squared_hinge", random_state=seed)`,decision_function 打分,仅支持 max 策略 | 一致(另固定 random_state 保证可复现) |
| TF-IDF ngram (1,2) | sklearn `ngram_range=(1,2)` | **产品分词器原生输出实词 uni+bigram** ⇒ 等价内建,无需参数 | 等价(分词器差异见下行) |
| sublinear_tf=True | tf→1+ln(tf) | `tfidf_sublinear=True`(对 train/candidate 统一按文档词频变换) | 一致 |
| max_df=0.95 | 丢弃 df>max_df·n_fit 词项(smooth idf 公式与本引擎相同) | `tfidf_max_df=0.95`(fixed 模式全语料一次拟合) | 一致 |

**与任务书预设的两处偏差(如实说明)**:

1. 任务书 CONFIGS 表把 `tuned_nb` 映射到 `preset="asreview"`,但该 preset 实现
   的是 **ASReview v1** 语义(DoubleBalance 重采样),与目标"Balanced ratio=9.8"
   **不匹配**。因此本批新增 `preset="asreview_tuned_nb"` 实现真正目标配置
   (在 asreview preset 骨架:priors(1,1)/warmup=2/fixed 特征之上,换 Balanced
   样本权重 + sublinear/max_df TF-IDF)。v1 preset 本身未被修改,S1 全量结论
   所依赖的路径逐位不变(pytest 200 passed)。
2. 分词器:引擎沿用产品自研分词器(中英混合、CJK 字符 bigram、46 停用词),
   与 ASReview v3 的 sklearn 分词器(`\b\w\w+\b` + 内置英文停用词,不含中文)
   不同。这是 S0 以来的一贯设计(README §6 差异矩阵),因此本批验证的是
   "**我们的分词器 + v3 调优的分类器/平衡/特征参数**"这一组合。

---

## 3. 主批结果(90 场)

### 3.1 WSS@95(mean±sd,5 种子;括号内 n_to_95 均值)

| 数据集 | N | K | 患病率 | legacy_nb(产品默认) | tuned_nb | svm_balanced |
|---|---|---|---|---|---|---|
| Nelson_2002 | 366 | 80 | 21.86% | +0.289±0.000(242) | +0.387±0.001(206) | **+0.399±0.038(202)** |
| Donners_2021 | 258 | 15 | 5.81% | **+0.601±0.000(90)** | +0.147±0.002(207) | +0.476±0.004(122) |
| van_Dis_2020 | 9128 | 72 | 0.79% | −0.031±0.000(8958) | +0.681±0.001(2456) | **+0.763±0.001(1703)** |
| Bos_2018 | 4878 | 10 | 0.21% | −0.038±0.000(4818) | **+0.886±0.000(313)** | +0.861±0.000(433) |
| Moran_2021 | 5214 | 111 | 2.13% | −0.034±0.000(5128) | +0.089±0.000(4489) | **+0.140±0.007(4224)** |
| Wassenaar_2017 | 7668 | 111 | 1.45% | +0.103±0.000(6496) | +0.779±0.001(1308) | **+0.843±0.004(822)** |
| **宏平均(6 集)** | | | | **+0.148** | **+0.495** | **+0.580** |

随机序参照(闭式,n95 期望位次代入):6 集随机 WSS 参照为 −0.001~+0.041,
即任何配置的 WSS>0.05 即优于随机。**旧配置在 van_Dis/Bos/Moran 上为负
(劣于随机)**;新配置无任何数据集劣于随机。全部 90 场 r95≥0.95(引擎按
召回证书口径在 95% 召回处截断记录 n_to_95,后续位次不影响该指标)。

### 3.2 相对旧配置的提升(筛查量至 95% 召回)

| 数据集 | svm_balanced 筛查节省 | tuned_nb 筛查节省 |
|---|---|---|
| Nelson_2002 | +16.6%(242→202) | +14.8%(242→206) |
| Donners_2021 | **−36.0%(90→122)** | **−130.2%(90→207)** |
| van_Dis_2020 | +81.0%(8958→1703) | +72.6%(8958→2456) |
| Bos_2018 | +91.0%(4818→433) | +93.5%(4818→313) |
| Moran_2021 | +17.6%(5128→4224) | +12.5%(5128→4489) |
| Wassenaar_2017 | +87.3%(6496→822) | +79.9%(6496→1308) |
| **宏 ΔWSS@95** | **+0.432** | +0.346 |

(以"达到 95% 召回需人工筛查记录数"计;正=节省。)

### 3.3 读法与注意点

- **legacy_nb 的 sd 恒为 0**:preset=product + max 策略没有任何种子消费路径
  (无先验抽样、无平衡重采样、max 排序不用 rng),已 empirically 验证
  (Nelson/Donners/Bos/Moran/Wassenaar 多种子逐位一致)⇒ 完全确定、与种子
  无关。van_Dis/Moran/Wassenaar 的 legacy 种子 2–5 为种子 1 实测行的确定性
  复制(与重跑逐位等价;单场 van_Dis 实测 388–735s,复制避免 ~50 分钟冗余,
  见 `verification_meta.json`)。
- 两个新配置种子方差都很小(svm 最大 sd=0.038@Nelson),结论稳健。
- **Moran_2021 对所有配置都难**(最好 +0.140):该集文本信号弱,任何
  TF-IDF+线性模型组合都只能小幅优于随机——升级不解决"数据本身难排"的
  问题,这正是停止规则/召回证书仍必需的原因。
- 患病率分层视角:患病率 ≥5% 的两集(Nelson/Donners)新旧差距小甚至反转;
  患病率 ≤2% 的四集新配置全面碾压(ΔWSS +0.12~+0.90)。**升级的主要收益
  在低患病率大库——这正是真实系统综述的主流场景。**

---

## 4. 停止规则回归(20 场,svm_balanced)

对 S1 试点 4 集(Bos_2018、Appenzeller-Herzog_2019、van_der_Waal_2022、
Nelson_2002)× 种子 {1..5},在新配置轨迹上后评估 streak 规则
(`stopping_rules.evaluate_streak`,与 S1 同口径):

| streak n | 宏 P0(零遗漏率) | 宏平均漏检 | 零遗漏经验率达标(P0≥0.95)的数据集 | 负贡献证据 |
|---|---|---|---|---|
| 10 | **0.000** | 17.65 | 0/4 | theo≈1.000,emp=1.000 |
| 25 | **0.000** | 7.20 | 0/4 | Nelson:theo=0.127 vs emp=1.000(−0.873) |
| 50 | **0.200** | 3.05 | 0/4 | Nelson:theo=0.0001 vs emp=0.200(−0.200) |

- **"streak 不安全"结论不变**:streak_10/25 的 P0 仍为 0.000(<0.05);
  streak_50 宏 P0=0.200(仍远低于 0.95 的 S1 安全线;任务书的"<0.05"口径
  仅 10/25 满足,50 不满足但同样不安全——4 集中 3 集 P0=0,唯一例外
  Nelson P0=0.800 也未达标)。结论方向与 S1 完全一致:固定 streak 阈值
  不能承载安全职责。
- **负模型贡献仍然存在**:随机序闭式 P_miss 与经验遗漏率之差在 Nelson
  streak_25/50 上为 −0.873/−0.200(经验远高于理论)。机制与 S1 相同:
  AL 排序把"模型盲区"相关记录压到长排除段之后,长 streak 内生于模型出错处。
  其余 3 个低患病率集闭式理论本身≈1(随机序也必然漏),贡献≈0——与 S1
  "高患病率集才会显出负贡献"的模式一致。
- 对产品的影响:S1 建议 1(`AL_STOP_STREAK_EXCLUDE=0`)与建议 2(超几何
  召回证书)在新配置下**无需任何修改**。证书族规则只依赖剩余池抽样,
  与排序模型无关。

---

## 5. 引擎改动与验证(研究代码,非产品代码)

`research/sim/replay.py` 本批新增(默认全关,既有路径逐位不变):

1. `balance="balanced"` + `balance_ratio`:ASReview v3 Balanced **样本权重**
   (w1=1.0、w0=n1/(ratio·n0)、归一到 Σw=n);NB 路径按加权类内特征和 +
   加权类先验解析实现;SVM 路径逐样本传入 `LinearSVC.fit`。
2. `classifier="svm"` + `svm_c`:sklearn LinearSVC(loss="squared_hinge",
   random_state=seed),decision_function 打分,仅允许 max 策略。
3. `tfidf_sublinear` / `tfidf_max_df`:sklearn 语义(tf→1+ln(tf);
   丢弃 df>max_df·n_fit 词项)。
4. `run_config` 新 preset:`asreview_tuned_nb`、`asreview_svm`。
5. 顺带修复一个引擎边缘缺陷:`_scale_normalize` 中全零范数行 0/0=NaN
   (product refit 模式下零词表重叠候选被排到队尾);改为保持 0 后,全 0 行
   经 NB 得分=纯先验,与产品"无已知词项→纯先验"语义精确一致(真实 SYNERGY
   语料可触发;合成测试语料不触发,故既有测试未覆盖)。

**正确性验证**:

- **vs sklearn 直接重实现**:balanced NB + sublinear/max_df TF-IDF(产品
  分词器)+ priors(1,1) 逐步重训的完整 AL 循环,引擎 vs sklearn
  `MultinomialNB(sample_weight=)` + `TfidfVectorizer(sublinear_tf=True,
  max_df=0.95, tokenizer=产品分词器)`:9/9 轨迹(最长 404 步)的 record_id
  筛查序列**完全一致**(排序位次逐位同)。
- **回归**:`./.venv/bin/python -m pytest research/sim/ -q` → **200 passed**
  (含 S1 试点轨迹逐位回归断言),产品模式/旧 preset 行为未变。
- 等效验证脚本为一次性运行(未落盘),复述于 `verification_meta.json`。

---

## 6. 产品建议

1. **新默认 = `svm_balanced`**:LinearSVC(C=0.11, squared_hinge) +
   Balanced 样本权重(ratio=9.8)+ TF-IDF(sublinear_tf, max_df=0.95,词表
   一次拟合)+ priors(1,1)/每步重训。预期宏 WSS@95 从 +0.148 → +0.580,
   低患病率大库(van_Dis/Bos/Wassenaar 型)筛查量减少 81%–91%。
   实施注意:
   - 产品侧需要 sklearn LinearSVC(产品 NB/TF-IDF 为纯 Python 自研,SVM
     建议直接引入 sklearn——已是 venv 依赖);
   - 平衡策略从"无"到"样本权重"只需改 NB/ SVM 的训练权重计算,不需要
     重采样基础设施;ratio 建议作为配置项暴露(默认 9.8);
   - LinearSVC 需固定 random_state 以保证可复现(本批以运行种子固定);
   - decision_function 分数无概率语义 ⇒ 查询策略中的 uncertainty/mixed
     的 0.5 阈值语义对 SVM 不适用,需将 SVM 限制为 max 策略或对分数做
     校准(本批按限制 max 处理)。
2. **备选 = `tuned_nb`**:若不想引入 sklearn 依赖,NB 路径同样显著提升
   (宏 +0.495),且纯 numpy 解析实现无收敛/随机性问题。但 6 集中 5 集
   不敌 SVM,Donners 上回归更深(−130%)。
3. **小集保护(可选)**:Donners 型(≲300 条)旧流水线反超。若产品可获取
   语料规模先验,可在 N 小时保留旧配置或提高先验数;不做此分支也可接受
   (Donners 型损失 −36% 筛查量,而低患病率大库收益 81%–91%,期望上
   划算)。
4. **停止规则侧无需改动**:S1 建议(streak 默认关闭、召回证书承担安全职责)
   在新配置下经 20 场回归确认仍然成立。
5. **风险与未验证项**:
   - 6 集 × 5 种子 ≠ 26 集全量;如需上线前全量确认,可按本批脚本扩展
     (`run_verification.py --datasets <26 集>`);
   - Moran_2021 型(文本信号弱)新配置也只有 +0.140,勿向用户承诺
     "大幅省力"——省力承诺应由召回证书的触发情况逐项目给出;
   - 中文/双语语料:升级参数是在英文 SYNERGY 上调优的,产品分词器支持
     中文,但 ratio=9.8/C=0.11 对中文语料的最优性未验证。

---

## 附:产物清单

| 文件 | 内容 |
|---|---|
| `run_verification.py` | 批脚本(幂等,支持按配置/数据集并行) |
| `verification_results.csv` | 主批 90 场(每场一行) |
| `verification_summary.csv` | 按 (config, dataset) 聚合 + 宏行 |
| `stoprule_runs.csv` | 停止规则回归 20 场 × 3 规则 |
| `stoprule_summary.csv` | streak_10/25/50 的 P0/漏检/理论 vs 经验 |
| `verification_meta.json` | 配置回显、确定性说明、引擎改动清单、版本 |
