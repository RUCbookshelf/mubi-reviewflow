# research/sim — S0 等效性基座与快速回放引擎

S0 交付物(研究计划:`docs/research/2026-09-25-al-research-program.md` S0 节):

| 文件 | 内容 |
|---|---|
| `equivalence_check.py` | 手写 TF-IDF / MultinomialNB vs sklearn 交叉验证(可复现脚本) |
| `replay.py` | 快速回放引擎(numpy/scipy 向量化,与 `coscreen/al/simulation.py` 语义一致)+ `run_config` + priors/balance 开关 + 轨迹日志 |
| `test_replay.py` | 一致性断言(轨迹逐位)+ 指标手算例 + 平台自检 + 性能冒烟(142 用例) |
| `results/equivalence_results.json` | 等效性验证原始结果 |
| `results/example-trajectory-mixed-s42.*` | 轨迹日志示例(产品 preset、mixed、seed=42、合成 200 条) |

复现命令(仓库根,venv 内 numpy 2.5.3 / scipy 1.18.1 / scikit-learn 1.9.1):

```bash
./.venv/bin/python research/sim/equivalence_check.py   # 任务1:等效性交叉验证
./.venv/bin/python -m pytest research/sim/ -q          # 任务2:142 用例(约 37s)
```

本目录只读引用 `coscreen/al/*` 与 `coscreen/config.py`,未改任何产品代码与产品测试。

---

## 1. 产品算法参数提取表(逐项,含实现行号)

### 1.1 `coscreen/al/features.py` — 分词与 TF-IDF

| 项 | 定义 | 行号 |
|---|---|---|
| 停用词表 | `ENGLISH_STOPWORDS`,46 个虚词;停用词不输出且**不阻断**相邻实词结 bigram | L42–50, L114–115 |
| 小写化 | `text.lower()` 后逐字符处理 | L71 |
| 保留字符 | CJK 统一表意文字 U+4E00–U+9FFF(L53–55);Unicode 字母 `L*`/数字 `N*`(L58–60) | L53–60 |
| 标点/符号 | `P*`/`S*` **删除且不切词**(`co-teaching`→`coteaching`,`students'`→`students`) | L84–85 |
| 词边界 | 空白/控制字符等其他字符作切分边界 | L86–90 |
| 英文 | 实词 unigram + 相邻实词 bigram `term1_term2`;CJK 段阻断英文 bigram | L106–120 |
| 中文 | CJK 最大连续段;长度 ≥2 输出全部字符 bigram `c1c2`,长度 1 输出单字 | L107–113 |
| df | `set(tokenize(text))` 计文档频 | L139–141 |
| 词表 | term 按字典序,`vocab={term: 序号}`(输入顺序无关) | L142 |
| idf | `ln((1+n)/(1+df)) + 1`(sklearn `smooth_idf=True` 公式);`n=len(texts)`(**含空文档**) | L143–147 |
| tf | 文档内原始词频(重复 token 累加) | L153–156 |
| 加权与归一化 | `tf×idf` 后按文档 L2 归一化(范数为 0 不归一) | L157–160 |
| 词表外 | `transform` 忽略词表外 term;空文档 → `{}` | L154–156 |

### 1.2 `coscreen/al/nb.py` — MultinomialNB(二分类,1=include)

| 项 | 定义 | 行号 |
|---|---|---|
| 平滑 | `alpha=1.0`(Lidstone),须为正 | L46–50 |
| 类内特征和 | `feat_sum[c][t]` 按**训练行序**逐文档累加(浮点累加顺序固定 ⇒ 可复现) | L68–79 |
| 词表大小 | `V = fit 时 X 中出现过的不同词项数` | L67, L81–82 |
| 类先验 | `class_log_prior_[c] = log(n_c/n)`;某类样本数 0 → `-inf`(该类后验恒 0) | L83–86 |
| 特征对数概率 | `log(feat_sum[c][t] + alpha) − log(Σ_t feat_sum[c][t] + alpha·V)`;分母求和为 dict 插入序的顺序和 | L87–94 |
| 打分 | `P(y=1|x) = exp(jll1 − logsumexp2(jll0, jll1))`;jll 按行 dict 顺序累加;未见词项忽略;无已知词项 → 纯先验 `n1/n` | L96–117(2 参 logsumexp L29–34) |

### 1.3 `coscreen/al/ranker.py` — 流水线

| 项 | 定义 | 行号 |
|---|---|---|
| 文本 | `" ".join(title, abstract, authors, journal)`(缺省为空串) | L79–85 |
| 训练集 | 仅 `include`/`exclude`(`maybe` 不训练、留在队列);按传入顺序构造 | L43, L113–116 |
| 候选 | 无决策 + maybe,按传入顺序 | L120–124 |
| 向量化 | `TfidfVectorizer().fit_transform(train_texts)`(每次调用全新拟合) | L130–131 |
| 分类器 | `MultinomialNB(alpha=1.0)` | L132 |
| 排序 | `query_order(cand_keys, scores, strategy, seed)` | L147 |
| 门槛 | 可用标签 < `min_labeled`(config `AL_MIN_LABELED=5`)→ 返回 None | L117–118 |

### 1.4 `coscreen/al/strategies.py` — 查询策略

| 项 | 定义 | 行号 |
|---|---|---|
| max | `sorted(keys, key=score.get(k,0.0), reverse=True)`:降序、**同分稳定保持 keys 原序**(CPython sorted 稳定且 reverse=True 不破坏稳定) | L24–30 |
| 缺失分数 | 三种策略通用:按 `0.0` 参与排序 | L48–49, L30 |
| uncertainty | `abs(score−0.5)` 升序(最不确定优先),同分稳定 | L102–104 |
| mixed 95/5 | ①`rng=random.Random(rng_seed)`;②`shuffled=list(keys); rng.shuffle(shuffled)`(洗牌先消耗随机流);③空分数字典 → 返回 `shuffled`;④否则取 max 序,对每个位置 i **恰好调用一次** `rng.random()`,若 `< AL_MIXED_RANDOM_RATIO`(=0.05)则取 `shuffled[pos]` 经 `order.index(k)` 换位上浮,`pos+=1` | L106–125(精确算法定义 L64–90) |
| 随机比例 | `AL_MIXED_RANDOM_RATIO = 0.05`(`coscreen/config.py` L28) | L16 |

### 1.5 `coscreen/al/simulation.py` — 回放基准

| 项 | 定义 | 行号 |
|---|---|---|
| WARMUP | `=5`:前 5 条筛完之前不训练,队列保持初始顺序 | L54 |
| MAX_RECORDS | `=2000`,超过抛 ValueError | L57 |
| 黄金校验 | 缺失 → KeyError;`strip().lower()` 后非 include/exclude → ValueError;多出键忽略 | L85–105 |
| Warm-start | `query_order(keys, {}, strategy, seed)`:max/uncertainty = 输入序;mixed = 种子化随机序 | L175 |
| 逐条筛选 | `order.pop(0)`;黄金标签立即入训练集(训练顺序=筛选顺序) | L180–182 |
| 重训节奏 | `if learn and len(labeled) >= WARMUP and order:`——第 5 条筛完后**每筛 1 条**全量 refit;全部相关已发现时 break 在 refit 之前(不再重训) | L185–189 |
| 重排基准 | 对**当前队列顺序**调用 `query_order(order, scores, strategy, seed)`(同分保持既有先后) | L189–190 |
| n_to_95 | 第 k 小相关发现位次,k 为满足 `k/n_relevant >= 0.95−1e-12` 的最小计数 | L138–144 |
| wss95 | `(N−n_to_95)/N − (1−0.95)`,截断 [-1,1] | L145–146 |
| r95 / ttd_mean | 筛到 n_to_95 时实际召回 / 相关记录首发现位次均值 | L140–147 |
| 随机基线 | `simulate_random_baseline` = mixed + 永远空分数 + `learn=False` | L222–231 |
| 常量 | `AL_DEFAULT_STRATEGY="max"`、`AL_SEED=42`、`SIM_RECALL_TARGET=0.95`(`coscreen/config.py` L25–26, L32) | — |

---

## 2. 任务 1:vs sklearn 等效性交叉验证(数值地基)

### 2.1 sklearn 参数映射表

手写实现与 sklearn **可对齐**,映射如下(`equivalence_check.py` 固定使用):

| 我方组件/行为 | sklearn 对应参数 | 说明 |
|---|---|---|
| 分词(含大小写、停用词、英文 uni+bigram、CJK 字符 bigram) | `analyzer="word", tokenizer=tokenize, token_pattern=None, lowercase=False, stop_words=None` | bigram 在 `tokenize` 内嵌,故 sklearn 必须用 `ngram_range=(1,1)`;lowercase/停用词由 tokenize 自理 |
| `idf = ln((1+n)/(1+df)) + 1` | `smooth_idf=True` | 公式逐字符相同 |
| tf = 原始词频 | `sublinear_tf=False` | — |
| 文档 L2 归一化 | `norm="l2"` | — |
| 词表字典序 | (sklearn 内建 `sorted`) | 逐位同序 |
| NB `alpha=1.0`、先验 `log(n_c/n)` | `MultinomialNB(alpha=1.0, fit_prior=True, class_prior=None)` | — |
| dtype | `dtype=np.float64` | 双方 float64 |

### 2.2 结果表(24 篇中英混合训练 + 4 篇候选,固定语料,含边界用例)

| 检查项 | 结果 |
|---|---|
| 词表(内容与顺序) | **完全一致**(779 项) |
| idf 向量 | **逐位相等**(max abs diff = 0.0) |
| TF-IDF 矩阵(918 个非零值) | max abs diff **1.11e-16**;412/918 个值差 1 ulp;≤1e-12 ✅ |
| NB `class_log_prior_` | max abs diff **1.11e-16** |
| NB `feature_log_prob_` | **逐位相等**(max abs diff = 0.0) |
| NB `predict_proba`(候选) | **逐位相等**(max abs diff = 0.0)≤1e-12 ✅ |
| 端到端流水线(ActiveLearningRanker vs sklearn 全流水线) | max abs diff **0.0** ✅ |

**判定:无算法级系统性差异。** idf 公式、归一化时机、平滑、先验均与 sklearn 精确一致;
全部残差都源于**浮点求和顺序**(L2 范数的行内求和序、jll 点积的归并序),量级 ≤1.2e-16,
远低于 1e-12 验收线。因此任务 2 采用"numpy 向量化重实现我们自己的算法"路线
(与任务书预案一致),sklearn 仅作旁证。

**唯一不对齐项(记录,不硬凑)**:单类训练。我方对缺席类 `prior=-inf`、得分恒 0/1;
sklearn `classes_` 只含所见类,`predict_proba` 返回单列全 1。语义等价(缺席类后验为 0),
接口不同。见 `equivalence_results.json` 的 `nb_single_class_divergence_documented`。

---

## 3. 任务 2:快速回放引擎设计

### 3.1 结构

```
records ──一次性 tokenize──> _CorpusIndex(全语料固定词表 + CSR 计数矩阵 + 每文档词项表)
                                  │
每步(第 warmup 条起):            ▼
  df 增量更新(仅新筛文档的词项 +1)
  idf = log((1+n)/(1+df)) + 1   (np.log,与 math.log 逐位同)
  tf×idf + 行 L2 归一化          (_scale_normalize,除法 x/norm 与产品同序)
  NB 类内和 = CSR 按列行序累加   (csr.sum(axis=0),与产品逐文档 dict 累加逐位同)
  feature_log_prob / 先验 / 分母 (np.log + math.log,逐位同)
  候选打分 = 稀疏矩阵 × (V×2) 稠密 + 向量化 logsumexp
  排序 = np.argsort(stable) 重实现产品 query_order(mixed 用键→下标映射替代 order.index)
控制流与产品 _simulate 逐行同构(warm-start/pop/重训条件/提前终止/指标公式)
```

### 3.2 逐位等效的数值依据(平台自检在 `test_replay.py::test_platform_bitwise_assumptions`)

| 运算 | 保证 |
|---|---|
| `np.log`/`np.exp` vs `math.log`/`math.exp`(float64) | 逐位相同(x86-64 Linux / numpy 2.5.3 实测;若未来平台不满足需重估) |
| `(1.0+n)/(1.0+df)` vs Python `(1+n)/(1+df)` | 逐位相同 |
| `csr.sum(axis=0)` vs 产品按文档序逐项累加 | **逐位相同**(行序顺序累加) |
| L2 范数 / NB 分母 / jll 点积 | **~1e-16 相对噪声**(产品:文档内首现序;引擎:CSR 列序 / pairwise)。实证:轨迹不受影响;**完全相同的行(重复文档)在双方产生逐位相同的分数 ⇒ 产品中的精确同分仍是精确同分,稳定排序打破方式一致** |

### 3.3 轨迹日志格式

`ReplayResult.save_logs(prefix)` 写两个文件:

- `prefix.trajectory.jsonl` — **每筛 1 条一行**,字段:

| 字段 | 含义 |
|---|---|
| `i` | 筛选位次(1 起) |
| `record_id` | 记录键 |
| `label` | 黄金标签 include/exclude |
| `phase` | `prior`(先验)/ `warmup` 前的输入序阶段 / `model`(模型驱动) |
| `score_when_head` | 该记录上一次被模型打分的分数(训练前为 null) |
| `n_labeled` | 当前已筛(参与训练)条数 |
| `n_relevant_found` / `n_relevant_total` / `recall_so_far` | 召回进度 |
| `n_remaining` | 剩余队列长度 |
| `exclude_streak` | 当前连续 exclude 数(S1 连续排除规则可直接后评估) |
| `trained_this_step` | 本步是否重训重排 |
| `model_top_remaining` | 重训后新队列前 top-k 的 `[record_id, score]`(默认 k=5;未训练为 null;`top_k_log=0` 时为空表 —— 大规模全量运行用此开关省盘,见 s1_full) |
| `remaining_score_sum` | 剩余池分数和 Σs_i(每**训练步**;`np.sum`,O(n);未训练为 null) |
| `remaining_score_quantiles` | 剩余池分数分位,格 = `REMAINING_SCORE_QUANTILES` = [0.1,0.25,0.5,0.75,0.9,0.95,0.99,1.0](notes/stopping_rules.md §3.2 的 [q10,q25,q50,q75,q90] ∪ 调度任务的 [0.5,0.75,0.9,0.95,0.99,1.0];`np.quantile` 线性插值,introselect partition 平均 O(n);未训练为 null)。两字段均为纯日志增强:不改变回放行为、轨迹顺序与任何指标(test_replay 有产品独立核算 + 试点产物逐位回归断言) |
| `step_elapsed_s` | 该步耗时 |

- `prefix.summary.json` — 配置回显 + 全部指标(`n_to_95/wss95/r95/ttd_mean/...`)+ `n_trajectory_rows`。

设计目的:**一次模拟、多规则后评估**——S1 的任何停止规则(连续排除、超几何上界、
负二项估计等)都只依赖轨迹序列,无需重跑模拟。

### 3.4 研究扩展(默认全关;产品模式逐位不受影响)

- `priors`:先验协议 = ASReview 论文语义。`"asreview"` = (1,1);`np.random.RandomState(seed)`
  无放回 `choice`,**先纳入后排除**,与 ASReview v1.1 `sample_prior_knowledge` 同语义
  (测试 `test_priors_asreview_semantics` 逐位对照该实现)。先验作为最初已筛记录,
  计入发现位次与训练集。
- `balance`:ASReview v1 论文版 dynamic resampling(**DoubleBalance**),参数取自
  ASReview v1.1 源码默认:`a=2.155, alpha=0.94, b=0.789, beta=1.0`;`RandomState(seed)`
  播种一次、随机流跨训练步持续(镜像 ASReview 模型构造);`random_round` 随机舍入、
  `fill_training` 复制+无放回抽样、合并后 shuffle;单类时 fallback 不重采样。词表不因
  重采样改变。产品代码无任何等价物(`ranker.py`/`nb.py` 全量训练)。
- `feature_refit="fixed"`:全语料一次性 TF-IDF(ASReview 风格);`"product"`(默认)
  = 每步在已筛标签上重拟合。

---

## 4. 一致性断言结果(快速引擎 ≡ 产品算法)

`./.venv/bin/python -m pytest research/sim/ -q` → **200 passed**(≈70s,含
bilingual/dual_oracle/stopping_rules;其中 test_replay.py 142 例)。对拍方式:
产品 `coscreen.al.simulation` 自身函数插桩(`_validated_gold/_article_text/
_score_remaining/query_order`),产品 2000 条上限内比较。

| 断言 | 覆盖 | 结果 |
|---|---|---|
| 逐步队首序列(位次, record_id)逐位相等 | 合成语料 100/160 条 + 3 个边缘语料(空文档/纯停用词/重复文档/单相关/全相关/首 5 全排除单类)× max/uncertainty/mixed × 多种子 | ✅ 全部逐位一致 |
| `n_to_95 / wss95 / r95 / ttd_mean` | 同上 + 500 条 + 随机基线 | ✅ `==` 精确相等(非近似) |
| 分数残差(产品每次 refit vs 引擎 top-k) | 同上 | max **8.7e-15** ≤ 1e-12 ✅ |
| 排序函数 vs 产品 `query_order` | 120 组随机输入(含全同分/缺分数/量化分数)× 3 策略 | ✅ 逐位同序 |
| 指标手算例 | 单相关(位次 4):`n_to_95=4, wss95=(6-4)/6-(1.0-0.95)`;全相关 12 篇:`n_to_95=12, wss95=-(1.0-0.95), ttd_mean=6.5, refit 7 次` | ✅ |
| 输入校验 | 缺标签 KeyError / 非法标签 ValueError / 多余键忽略 / 大小写空白规范化 / 键唯一性 | ✅ 与产品同语义 |
| 剩余池分数摘要独立核算(2026-09-25 增强) | 每训练步 `remaining_score_sum`/`remaining_score_quantiles` vs **产品插桩分数**独立重算(fsum + 纯 Python 线性插值分位),9 个对拍语料全训练步 | ✅ 和与分位偏差 ≤1e-8;未训练步为 null;JSON 往返无损 |
| 增强不改回放(回归) | (a) 与产品轨迹/WSS 逐位一致(上表既有断言,增强后复跑);(b) preset=asreview 重跑 Nelson_2002 s0 与 s1_pilot 试点轨迹**逐位一致**(除耗时与新摘要字段),WSS/n_to_95/r95/ttd_mean 与试点摘要完全相等 | ✅ |

---

## 5. 性能基准(实测,最终代码;CPU x86-64 WSL2)

合成基准语料(`make_synthetic_corpus`,中英混合,~110 token/篇,6% 相关):

| 语料 | 策略 | 引擎耗时 | 训练步数 | n_to_95 |
|---|---|---|---|---|
| n=2000(seed 42) | max | **0.9 s** | 194 | 147 |
| n=2000 | mixed | **1.3 s** | 347 | 224 |
| n=8000(seed 42) | max | **7.5 s** | 515 | 489 |
| n=8000 | mixed | **9.1 s** | 558 | 538 |
| n=8000 **对抗性**(标签与文本无关) | max | **15.4 s** | 1301 | 484 |
| n=8000 对抗性 | mixed | **21.5 s** | 1419 | 494 |

**结论:SYNERGY 单集(≤8k 条)单种子全量回放 ≤60s 的目标达成,最坏实测 21.5s,
裕量 ≈2.8×。** 对照:产品实现在 n=160 时单次回放 1.37s,快速引擎 0.047s(**~29×**);
产品在数千条规模因逐步全量重分词实际不可行(这正是本引擎的动机)。

---

## 6. 产品 vs ASReview 论文默认配置差异矩阵

ASReview 侧事实来源:van de Schoot et al. 2021(Nat Mach Intell 3:125)+ ASReview
v1.1 源码(`asreview/models/balance/double.py`、`classifiers/nb.py`、
`feature_extraction/tfidf.py`、`review/simulate.py::sample_prior_knowledge`)。

| 维度 | coscreen/al 产品 | ASReview v1 论文默认 | 引擎桥接 |
|---|---|---|---|
| 先验 | 无;前 5 条按导入序(mixed=随机)作 warmup,不特选 | 随机抽 n_prior_included/excluded(CLI 默认 10+10;**用户协议 = 1+1**) | `priors="asreview"`:`RandomState(seed)` 无放回,先纳入后排除 |
| 首次训练前已筛数 | warmup=5 | 先验立即训练,此后每步重训 | `warmup` 参数(preset asreview → 2) |
| 重训节奏 | 第 5 条后每筛 1 条全量 refit | 每步(n_instances=1)refit | 与产品同构;`warmup` 可调 |
| TF-IDF | 自研分词(中英混合、CJK 字符 bigram、46 停用词、uni+bigram)+ smooth_idf + l2 | sklearn `TfidfVectorizer(ngram_range=(1,1), stop_words="english")`(默认 `\b\w\w+\b` 分词,**不含中文**) | 引擎用产品向量化器;ASReview 分词差异记录在案,不做融合 |
| 特征拟合时机 | 每步在已筛标签上 refit | 全语料一次拟合,固定特征矩阵 | `feature_refit="fixed"` |
| NB alpha | 1.0 | **3.822** | `alpha` 参数(preset asreview → 3.822) |
| 类不平衡处理 | 无(全量训练) | **dynamic resampling(double)**:重采样训练集,a=2.155/α=0.94/b=0.789/β=1.0,单类 fallback | `balance=True`(默认关;`DOUBLE_BALANCE_DEFAULTS`) |
| 类先验 | `log(n_c/n)`(全集标签比例) | sklearn `fit_prior=True` 于(重)采样集 | balance 开启时先验取自重采样标签(ASReview 语义) |
| 查询策略 | max(默认)/uncertainty/mixed(95%max+5%random) | max(=Top-down);mixed 可选(95% max + 5% random,与我们 mixed 同型) | `strategy` 参数 |
| 指标 | WSS@95/n_to_95/r95/ttd_mean | WSS@95、RF@10 等 | 同 WSS@95 定义 |

preset 封装:`run_config(preset="product")` = 产品语义逐位;`preset="asreview"` =
先验(1,1)+ balance 开 + alpha=3.822 + warmup=2 + fixed 特征。
`preset="asreview_tuned_nb"` / `preset="asreview_svm"` = ASReview v3 调优研究
配置(Balanced **样本权重** ratio=9.8,v3 `balancers.py` 语义,非 DoubleBalance
重采样;TF-IDF sublinear_tf + max_df=0.95;分类器分别为 NB alpha=3.822 与
LinearSVC C=0.11;svm 仅支持 max 策略)。产品分词器原生输出实词 uni+bigram,
故调优配置的 ngram_range=(1,2) 无需参数(等价内建)。引擎 balanced-NB 与
sklearn `MultinomialNB(sample_weight=...)` + `TfidfVectorizer(sublinear_tf=True,
max_df=0.95, tokenizer=产品分词器)` 的直接重实现在 9 条轨迹(最长 404 步)上
record_id 序列逐位一致。
Sanity(合成 200 条,seed 42,max):product n95=120/wss=0.365;asreview n95=34/wss=0.784
(合成语料信号强,仅供参考,非结论)。

---

## 7. 待 S1 / S2 的接口说明

### S1(停止准则)

```python
from replay import run_config
r = run_config(dataset_csv="research/data/<synergy>.csv", seed=42,
               strategy="max", log_dir="research/results/sim",
               log_prefix="synergy-<name>-max-s42")
# r.trajectory: 逐行含 exclude_streak / n_remaining / recall_so_far /
# model_top_remaining ⇒ 任何停止规则都可“事后”在轨迹上求停止时刻:
#   - 连续排除规则:扫描 exclude_streak 首次达到阈值的行
#   - 超几何/负二项估计器:用 n_remaining + n_relevant_found 序列
# 一次模拟、多规则复用;指标 r.metrics_dict() 与产品 SimResult 字段一致。
```

- 数据集加载:`load_dataset_csv(path, id_col=None, label_col=None)` 自动识别
  SYNERGY 风格列(`record_id`、`label_included` 等;keywords 列刻意不用,保持
  产品四字段拼接语义)。
- 多种子批量:`for seed in range(25): run_config(..., seed=seed, log_dir=...)`。
- 注意:引擎不设 2000 条上限(产品 `MAX_RECORDS` 不适用);内存占用 ≈ CSR 计数矩阵。

### S2(双盲双筛员)

- `replay_fast(records, gold, strategy, seed, ...)` 的 `strategy`/`seed` 正交:
  两名筛员可用同排序不同种子(独立 mixed 序)或独立训练的模型排序;
  `ReplayResult.trajectory` 给出每名筛员的发现位次序列,可直接构造分歧样本集
  (同一 record 在两轨迹中的 label 一致性)与仲裁成本模型。
- `top_k_log` / `collect_trajectory=False` 可在大规模模拟中关闭日志省时。

### 已知限制(如实记录)

- 逐位一致性声称依赖平台自检(`test_platform_bitwise_assumptions`);换硬件/BLAS
  需先跑该测试。
- 引擎分数与产品分数存在 ≤1e-14 级残差(求和顺序);轨迹实证不受影响,但理论上
  若两条不同文档的分数差 < 1e-14 且排序相邻,顺序可能翻转(未在任一测试语料观测到)。
- `balance`+`feature_refit="product"` 组合可用但非规范 ASReview 组合(规范组合为
  fixed 特征 + balance);文档以上述差异矩阵为准。
