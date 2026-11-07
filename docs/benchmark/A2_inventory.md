# A2 统计模块调用契约盘点（统计模块基准测试用）

- 仓库：`E:/mubi-reviewflow`，分支 main，HEAD `501e1f3`（feat: release 2.1.3 evidence workspaces and workflow fixes），只读盘点。
- 验证解释器：`C:/Users/16073/.codex/preview-data/reviewflow-2-1-1-venv/Scripts/python.exe`（Python 3.11.15；scipy 1.17.1 / statsmodels 0.14.5 / pandas 2.3.3 / numpy 2.4.6）。
- 本文所有"最小可调用样例"均已用上述 venv 在 `E:/mubi-reviewflow` 目录下实际跑通，样例输出值附在各节。
- 结论行号均为 `path:line`（相对仓库根）。

---

## 0. 总原则：基准测试走直接函数调用，不走 HTTP API

统计内核全部是 `coscreen` 包内的**纯函数**（输入为 list[dict] / DataFrame / 数值，输出为 dict / dataclass，无 IO）。HTTP 层（FastAPI，`custom_backend/main.py`）只是薄封装，且额外叠加：

1. JWT 鉴权（除 login/register/health 外全部要 `Authorization: Bearer`，`custom_backend/main.py:10-12`）；
2. `task_id` -> 任务目录 -> 每用户 screener SQLite 的间接寻址（`_screener_db(_task_or_404(task_id), ...)`，见 `custom_backend/main.py:2831`）；
3. 预置数据库内容（效果量表、编码维度等）与用户/任务夹具；
4. 序列化/Pydantic 422、业务 ValueError->400 的错误转换。

因此**基准测试应直接 import coscreen 内核函数调用**：消除网络/鉴权/DB 寻址噪声，统计内核数值可独立对拍（与 R meta/metafor、RevMan、sklearn 基准对齐）；HTTP 层性能如需单独测量，属于 API 基准而非统计基准。仅两个例外必须带 DB 夹具（见 §1.4、§6.1、§7：`synthesize`/`network_analysis.synthesize`/`small_study_effects` 的公开入口吃 DB 路径，但内核 `_synthesize_rows` 有纯函数形态可替代大部分场景）。

---

## 1. 通用逆方差固定/随机合并（fixed / DL / Paule-Mandel / REML）

### 1.1 入口与签名

- 公开 DB 入口：`synthesize(db, comparison, outcome, timepoint, measure, model="fixed", ci_method="normal") -> dict`，`coscreen/review_analysis.py:471-484`。`model ∈ {fixed, random(DL), random_pm(Paule-Mandel), random_reml(REML)}`，`ci_method ∈ {normal, hksj}`（fixed 不允许 hksj），校验在 `coscreen/review_analysis.py:478-480`。
- **纯函数内核（基准推荐）**：`_synthesize_rows(rows, measure, model, ci_method) -> dict`，`coscreen/review_analysis.py:487-575`。DL τ² 在 `coscreen/review_analysis.py:497`；Paule-Mandel τ² 经 statsmodels `combine_effects(..., method_re="iterated")` 在 `coscreen/review_analysis.py:498-502`；REML τ² 调 `profile_reml_tau2` 在 `coscreen/review_analysis.py:503-505`。
- REML τ² 估计器本体：`profile_reml_tau2(effects, sampling_variances) -> tuple[float, float]`，`coscreen/meta_regression_model.py:82-92`（profile REML 目标函数 `coscreen/meta_regression_model.py:29-45`，优化器 `coscreen/meta_regression_model.py:48-79`）。
- 兼容性守卫：`require_compatible_effects(rows, expected_measure)`，`coscreen/pool_compatibility.py:26`（SMD 估计量/方向校验，SMD 行需带 `estimator_metadata` 或保持全 "unclassified"）。
- 随机对照效应量构造（可选，用于从臂数据生成 rows）：`binary_effect` `coscreen/review_analysis.py:36-59`、`continuous_effect` `coscreen/review_analysis.py:62-84`。
- 度量集合：`RATIOS = {RR,OR,HR,RATE_RATIO}` `coscreen/review_analysis.py:31`；`MEASURES` `coscreen/review_analysis.py:33`。RATIOS 在分析尺度上取 log（`coscreen/review_analysis.py:491`）。

### 1.2 输入数据结构（纯函数形态）

`rows` 为 list[dict]，每个元素至少含 `study_id: str`、`estimate: float`、`se: float`（RATIOS 的 estimate/se 均为**自然尺度数值**，函数内部取 log；`input_data`/`measure` 键可省，兼容性守卫会以传入 measure 兜底，见 `coscreen/pool_compatibility.py:28-29`）。要求 ≥2 行（`coscreen/review_analysis.py:482-483` 仅在公开入口检查；`_synthesize_rows` 自身不查行数）。

### 1.3 最小可调用样例（已验证）

```python
import sys; sys.path.insert(0, r"E:/mubi-reviewflow")
from coscreen.review_analysis import _synthesize_rows

rows = [  # MD（自然尺度），异质性数据 -> DL/REML 的 tau2 > 0
    {"study_id": "S1", "estimate": -0.20, "se": 0.15},
    {"study_id": "S2", "estimate": -0.80, "se": 0.16},
    {"study_id": "S3", "estimate": -0.45, "se": 0.14},
    {"study_id": "S4", "estimate": -1.10, "se": 0.15},
    {"study_id": "S5", "estimate": -0.35, "se": 0.17},
]
for model in ("fixed", "random", "random_reml"):
    print(model, _synthesize_rows(rows, "MD", model, "normal")["tau2"])
```

实测输出（10 位精度见 §1.5）：DL `tau2=0.111784, q=23.018639, i2_percent=82.622778, pooled_analysis=-0.580531`；REML `tau2=0.11046, pooled_analysis=-0.580535`；fixed `tau2=None`（`coscreen/review_analysis.py:573`）。

### 1.4 DB 夹具样例（公开入口 `synthesize`，已验证）

```python
import sys, tempfile, os; sys.path.insert(0, r"E:/mubi-reviewflow")
from coscreen.db import init_db, _connect
from coscreen.review_analysis import save_study, save_effect, synthesize

tmp = os.path.join(tempfile.mkdtemp(), "bench.db")
init_db(tmp)                                   # 建库（coscreen/db.py:603）
for i in range(1, 11):                         # ≥2 项研究；此处 10 项
    sid, key = f"S{i:02d}", f"zot-{i:02d}"
    with _connect(tmp) as conn:
        conn.execute("INSERT INTO articles(zotero_key, import_order) VALUES (?,?)", (key, i))
    save_study(tmp, sid, sid, [key])           # coscreen/review_analysis.py:93
    save_effect(tmp, sid, "T vs C", "pain", "post", "MD",
                -0.3 - 0.05*i, 0.25 - 0.015*i, key, entry_method="manual")  # :283
r = synthesize(tmp, "T vs C", "pain", "post", "MD", model="random")
```

注意：DB 路径下 SMD 度量会被 SMD 契约拦截（manual SMD 需显式 `standardizer`/`bias_correction` 或统一 unclassified，`coscreen/pool_compatibility.py:54-64,76-85`），基准夹具建议用 MD/RD 或在 `input_data` 写明 `estimator_metadata`。

### 1.5 输出 dict 数值字段（`coscreen/review_analysis.py:553-575`）

| 键 | 含义 |
|---|---|
| `pooled_analysis` / `ci_analysis_low` / `ci_analysis_high` | 分析尺度（RATIOS 为 log 尺度）合并值与 95%CI |
| `pooled` / `ci_low` / `ci_high` | 展示尺度（RATIOS 已 exp；溢出降级 None，`:522-529`） |
| `prediction_interval_analysis_95` / `prediction_interval_95` | 随机模型且 ≥3 研究时的预测区间（`:533-536`） |
| `q` / `q_df` / `q_p` | Cochran's Q、df=k-1、χ² p（`:494-495,571-572`） |
| `i2_percent` | `max(0,(Q-df)/Q)*100`（`:506`） |
| `tau2` | DL/PM/REML τ²；fixed 时为 None（`:573`） |
| `n_studies` / `study_ids` / `measure` / `model` / `method` / `ci_method` / `effect_scale` / `warnings` / `effects` | 元信息 |

HTTP 对应：`GET /api/tasks/{task_id}/analysis/synthesis`，`custom_backend/main.py:2826-2834`（额外挂 `heterogeneity_intervals`，见 §4）。

---

## 2. Mantel-Haenszel（固定效应，RR/OR/RD）

- 入口：`pool_mantel_haenszel(studies: list[dict], measure: str) -> dict`，`coscreen/mantel_haenszel.py:61-186`；`measure ∈ {RR,OR,RD}`（`:67-68`）。计数校验 `_validated_studies` 在 `coscreen/mantel_haenszel.py:14-52`。
- 输入：`studies` 每项 `{"study_id": str, "events_t": int, "total_t": int, "events_c": int, "total_c": int}`（总数必须为正、事件不超过总数；bool 会被拒，`:31-49`）。无连续性校正；零贡献层记入 `excluded_studies`（`:81-86,114-119`）。
- 最小样例（已验证）：

```python
import sys; sys.path.insert(0, r"E:/mubi-reviewflow")
from coscreen.mantel_haenszel import pool_mantel_haenszel
studies = [
    {"study_id": "S1", "events_t": 10, "total_t": 50, "events_c": 20, "total_c": 50},
    {"study_id": "S2", "events_t": 12, "total_t": 60, "events_c": 15, "total_c": 58},
    {"study_id": "S3", "events_t": 8,  "total_t": 45, "events_c": 18, "total_c": 47},
]
print(pool_mantel_haenszel(studies, "OR"))
```

实测：`pooled=0.4636475077263075, ci_low=0.2761684371456256, ci_high=0.7783981895175635, se=0.26434854257473517, se_scale='log', n_studies=3`。
- 输出数值字段：`pooled, ci_low, ci_high, se, se_scale("log"|"natural"), measure, n_studies, included_studies, excluded_studies, method, warnings`（`coscreen/mantel_haenszel.py:172-186`）。RR/OR 的 pooled 与 CI 在自然尺度、se 在 log 尺度；RD 全部自然尺度（`:103-106,135-139,157-163`）。
- HTTP：`GET /api/tasks/{task_id}/analysis/binary-pooling?method=mh`，`custom_backend/main.py:3050-3082`（要求全部研究以 binary arms 形式保存计数，`:3073-3076`）。基准走直接函数调用。

---

## 3. Peto OR（固定效应）

- 入口：`pool_peto(studies: list[dict]) -> dict`，`coscreen/peto.py:7-88`。只做 OR（`"measure": "OR"`，`:78`）。
- 输入：与 MH 相同的四计数 + `study_id`（必含键校验 `coscreen/peto.py:14,19-21`；计数须非负 int 且 0<events≤total，`:27-32`）。
- 最小样例（已验证）：同 §2 的 `studies`，`pool_peto(studies)` 实测 `pooled=0.472412, ci_low=0.286054, ci_high=0.78018, se=0.25596`（OR 尺度，se 为 log 尺度）。
- 输出数值字段：`pooled, ci_low, ci_high, se, measure, n_studies, included_studies, excluded_studies, study_statistics(逐研究 O-E 与方差), method, warnings`（`coscreen/peto.py:73-88`）。零方差研究（双臂零事件或全事件）被排除（`:39-52`）。
- HTTP：`GET /api/tasks/{task_id}/analysis/binary-pooling?method=peto`（仅 OR，`custom_backend/main.py:3054-3055,3084-3085`）。

---

## 4. 异质性 Q / I² / τ²（及区间）

- **点估计**随 §1 合并输出：Q `coscreen/review_analysis.py:494`、df `:495`、I² `:506`、τ²（DL `:497` / PM `:500-502` / REML `:503-505`）。
- **Higgins-Thompson I²/H 95% 区间**：`i2_h_interval(q: float, k: int, level=0.95) -> dict`，`coscreen/heterogeneity_interval.py:20-101`（纯计算、无数据访问，模块 docstring `:1-7`）。
  - 最小样例（已验证）：`i2_h_interval(18.0, 10)` -> `i2_percent=50.0, h=1.4142, i2_ci_percent=(0.0, 75.7553), h_ci=(1.0, 2.0309)`。
  - 输出字段：`i2_percent, i2_ci_percent(tuple), h, h_ci(tuple), level, method, input_data, warnings`（`coscreen/heterogeneity_interval.py:92-101`）。要求 k≥3、q≥0（`:43-51`）。
- HTTP 挂载：`custom_backend/heterogeneity_view.py:4-14`（`heterogeneity_intervals(result)` 包成 `{"available": True, ...}` 附到 synthesis 结果，`custom_backend/main.py:2832-2833`）。
- 相关但未列入 A1 基准范围的：τ² profile 似然区间 `coscreen/tau_profile_interval.py`、τ²-Q profile 区间 `coscreen/tau_q_profile_interval.py`（HTTP `custom_backend/main.py:3350` 附近 audit 段）。

---

## 5. Cohen / Light（及"双评分者字段级"）kappa —— 注意：**Fleiss 未实现**

全仓库（`*.py`）大小写不敏感检索 "fleiss" 零命中：**本仓库没有 Fleiss kappa 实现**。多人一致性用 Light's kappa（两两 Cohen κ 的等权平均）承担，基准如需 Fleiss 需自带参考实现（如 `statsmodels.stats.inter_rater.fleiss_kappa`，statsmodels 0.14.5 已在 venv 内可用，可与仓库口径并列对照，但注意分母口径差异）。

### 5.1 Cohen's kappa（纯 Python，无 sklearn 依赖）

- 入口：`cohen_kappa(labels_a, labels_b, labels=DEFAULT_LABELS) -> KappaResult`，`coscreen/stats/kappa.py:64-119`；`DEFAULT_LABELS=("include","exclude","maybe")` `:16`。按位 zip 配对，任一方为空/表外即丢弃（`:82-86`）；n=0 约定 kappa=0（`:88-96`）；pe=1 边界约定（`:108-111`）。
- 多人：`pairwise_kappa(named_labels) -> dict[tuple[str,str], KappaResult]` `coscreen/stats/kappa.py:126-144`；`light_kappa(named_labels) -> float | None`（Light 1971，可算配对等权平均）`coscreen/stats/kappa.py:147-157`。
- `KappaResult` 字段：`kappa, po, pe, n, interpretation, confusion`（`coscreen/stats/kappa.py:28-41`；confusion 键为 (label_a,label_b) 元组）。
- 最小样例（已验证）：

```python
import sys; sys.path.insert(0, r"E:/mubi-reviewflow")
from coscreen.stats.kappa import cohen_kappa, light_kappa
r = cohen_kappa(["include","exclude","maybe","include","include"],
                ["include","exclude","include","include","exclude"])
# 实测: kappa=0.2857142857, po=0.6, pe=0.44, n=5
print(light_kappa({"A": ["include","exclude","maybe","include"],
                   "B": ["include","exclude","maybe","maybe"],
                   "C": ["include","exclude","include","include"]}))  # 实测 0.5084175084
```

- HTTP（参考）：筛选合并+一致性 `POST /api/merge/decisions`（`custom_backend/main.py:2132`，逐对+Light，`:2151-2179`）；编码矩阵 `POST /api/coding/agreement`（`custom_backend/main.py:2260`）。

### 5.2 编码矩阵一致性引擎（N≥2 编码员，逐维度 κ / Light）

- 入口 1：`merge_coding(named_matrices: dict[str, pd.DataFrame]) -> pd.DataFrame`，`coscreen/stats/coding_agreement.py:184-331`（矩阵需含 `zotero_key` 列且各矩阵维度列完全一致，`:230-256`；输出长表列 `zotero_key,title,dimension,dtype,value_<coder>...,n_coded,status`，`:318-327`）。
- 入口 2：`summarize_coding(merged, dtype_map) -> CodingAgreementSummary`，`coscreen/stats/coding_agreement.py:574-652`（dtype_map 必须覆盖全部维度，`:590-597`；单选=逐对多类 Cohen κ 的平均=Light，`:386-417`；多选=逐 option 0/1 κ，`:428-491`；文本 κ=None）。
- 输出字段：`CodingAgreementSummary{n_cells,n_agree,n_conflict,n_incomplete,agreement_rate,per_dimension[]}`（`coscreen/stats/coding_agreement.py:544-549`）；每维度 `dimension,dtype,n,n_agree,n_conflict,n_incomplete,agreement_rate,kappa,light,detail`（`:628-643`）；JSON 化用 `agreement_payload` `:683-701`。
- 最小样例（已验证）：

```python
import sys; sys.path.insert(0, r"E:/mubi-reviewflow")
import pandas as pd
from coscreen.stats.coding_agreement import merge_coding, summarize_coding
m1 = pd.DataFrame({"zotero_key": ["K1","K2","K3","K4"], "species": ["yes","no","yes","yes"]})
m2 = pd.DataFrame({"zotero_key": ["K1","K2","K3","K4"], "species": ["yes","no","no","yes"]})
s = summarize_coding(merge_coding({"alice": m1, "bob": m2}), {"species": "choice"})
# 实测: n_cells=4 n_agree=3 n_conflict=1 agreement_rate=0.75 kappa=0.5 light=0.5
```

### 5.3 字段级双评分者一致性（含二人间 Cohen κ 键）

- 入口：`summarize_agreement(records, rater_ids, field_type, *, absolute_tolerance=None) -> dict`，`coscreen/coding_agreement.py:9-96`。records 每项 `{"item_key": str, "rater_id": str, "field_key": str, "value": ...}`（同一字段一次一批，`:31-33`）；categorical 且 ≥2 对时输出 `cohen_kappa` 键（`:86-95`）。
- 输出字段：`field_type, rater_ids, n_items, n_paired, missing_by_rater, item_results, agreement_rate`（numeric 另有 `absolute_tolerance, mean_absolute_difference`；categorical 另有 `cohen_kappa, kappa_status`）。
- 最小样例（已验证）：4 对分类值 `["x","y","x","x"]` vs `["x","x","x","y"]` -> `agreement_rate=0.5, cohen_kappa=-0.3333333333, kappa_status='available', n_paired=4`。

---

## 6. 网络 Meta（对比法）

### 6.1 DB 版（对照表驱动，固定效应 WLS）

- 入口：`synthesize(db, outcome, timepoint, measure, reference) -> dict`，`coscreen/network_analysis.py:12-75`；`measure ∈ {RR,OR,RD,MD,SMD}`（`:17-18`）。
- 数据契约：效果量表中以 `"A vs B"` 命名 comparison（`:27-29`）；每研究仅一条对照（多臂/重复研究直接报错，`:30-32`）；网络 ≥3 治疗且连通、reference 必须在场（`:34-48`）。比例量内部取 log（`:52-53`）。
- DB 夹具样例（已验证，接 §1.4 的 tmp 库继续插入）：

```python
from coscreen.network_analysis import synthesize as net_synthesize
for i, (sid, comp, est, se) in enumerate(
        [("N1","A vs B",-0.2,0.20),("N2","A vs C",-0.5,0.22),("N3","B vs C",-0.3,0.25)], 1):
    key = f"zn-{i}"
    with _connect(tmp) as conn:
        conn.execute("INSERT INTO articles(zotero_key, import_order) VALUES (?,?)", (key, 100+i))
    save_study(tmp, sid, sid, [key])
    save_effect(tmp, sid, comp, "bp", "post", "MD", est, se, key, entry_method="manual")
net = net_synthesize(tmp, "bp", "post", "MD", "A")
# 实测: n_studies=3, treatments=['A','B','C'], residual_q=0.0,
#       A vs B -0.2 (se 0.171455), A vs C -0.5 (se 0.181318), B vs C -0.3 (se 0.191347)
```

- 输出字段：`method, measure, outcome, timepoint, reference, n_studies, treatments, estimates[], residual_q, residual_df, residual_p, warning`（`coscreen/network_analysis.py:70-75`）；`estimates[]` 每项 `treatment, comparator, estimate, se, ci_low, ci_high`（RATIOS 为自然尺度、se 为 log 尺度，`:65-69`）。
- HTTP：`GET /api/tasks/{task_id}/analysis/network`，`custom_backend/main.py:3626`。

### 6.2 纯函数版（研究级协方差块 GLS，基准推荐）

- 入口：`synthesize_network(studies: list[dict], measure: str, reference: str) -> dict`，`coscreen/network_gls.py:101-257`；研究块解析与校验 `_parse_study` `coscreen/network_gls.py:33-91`、单研究校验 `validate_study` `:94-98`。
- 输入契约（`coscreen/network_gls.py:33-91`）：每研究 `{"study_id": str, "covariance_scale": "log"|"natural"(须与度量匹配), "contrasts": [{"treatment","comparator","estimate"}...], "covariance": m×m 对称正定阵}`；RATIOS 的 estimate 为**正的自然尺度值**、covariance 为 log 尺度（`:39-41,58-60,101-107`）。
- 最小样例（已验证）：

```python
import sys; sys.path.insert(0, r"E:/mubi-reviewflow")
from coscreen.network_gls import synthesize_network
studies = [
    {"study_id": "S1", "covariance_scale": "log",
     "contrasts": [{"treatment": "A", "comparator": "B", "estimate": 0.90}],
     "covariance": [[0.04]]},
    {"study_id": "S2", "covariance_scale": "log",
     "contrasts": [{"treatment": "A", "comparator": "C", "estimate": 0.70}],
     "covariance": [[0.05]]},
    {"study_id": "S3", "covariance_scale": "log",
     "contrasts": [{"treatment": "B", "comparator": "C", "estimate": 0.905}],
     "covariance": [[0.06]]},
]
net = synthesize_network(studies, "OR", "A")
# 实测: residual_q=0.153003, residual_df=1, residual_p=0.6957,
#       A vs B OR=0.864366 (se 0.17127), A vs C OR=0.736256, B vs C OR=0.851788
```

- 输出字段：`method, measure, reference, treatments, n_studies, n_contrasts, estimates[]（treatment,comparator,estimate,se,se_scale,ci_low,ci_high）, residual_q, residual_df, residual_p, warnings`（`coscreen/network_gls.py:241-257`）。
- HTTP：`GET /api/tasks/{task_id}/analysis/network/gls`，`custom_backend/main.py:3685-3697`。
- 相关（随机效应/不一致/节点劈裂，未列入 A1 范围）：`coscreen/network_random.py`、`network_inconsistency.py`、`network_node_split.py` 及 `network_random_*` 系列。

---

## 7. Egger（小研究效应回归）

- 入口：`small_study_effects(db, comparison, outcome, timepoint, measure, study_ids=None) -> dict`，`coscreen/advanced_analysis.py:98-119`（DB 入口；要求 ≥10 项研究 `:105-106`，SE 有变异 `:107-109`；单组度量拒绝 `:102-103`；Egger 回归 OLS `:110-111`）。
- 输入：效果量表中的选择效应（同 §1.4 夹具，10 项 MD 即可）。RATIOS 内部取 log（`:110`）。
- 已验证样例：接 §1.4 的 10 研究 MD 夹具，`small_study_effects(tmp, "T vs C", "pain", "post", "MD")` -> `n_studies=10, egger_intercept=3.333333, egger_p=0.0`（本例效应与精度完全线性，p 触底；构造基准数据时刻意加噪声可得到非退化 p）。
- 输出数值字段：`n_studies, points[]（study_id,result_id,source_key,source_locator,effect,se）, egger_intercept, egger_p, method, method_sources, warning`（`coscreen/advanced_analysis.py:112-119`）。
- HTTP：`GET /api/tasks/{task_id}/analysis/advanced/small-study-effects`（方法分发表 `custom_backend/main.py:3398-3401`，路由装饰器 `:3392`）。
- 基准建议：Egger 无纯函数形态（数据装配在 DB 层），用 §1.4 的 10 研究 DB 夹具直调；或按 `:107-111` 的两行核心公式（OLS(y/se ~ 1/se)）自行对拍 statsmodels。

---

## 8. Peters 检验（单比例漏斗不对称）

- 入口：`peters_test(rows: list[dict], *, source_provenance: dict | None = None) -> dict`，`coscreen/peters_test.py:161-256`；行校验 `:63-92`（每行 `{"study_id": str, "events": int, "total": int}`，≥3 行 `:64-66`，非边界研究 ≥3 `:179-183`）。
- 口径：logit 比例对 1/total 加权回归，权重 `e(n-e)/n`；边界研究（events=0 或 =total）零权重剔除（`:95-117`）；统计量为斜率 t，df=纳入数-2（`:195-198`）。复刻 R meta 8.5-0 `metabias(method.bias="Peters")`（模块 docstring `:1-14`）。
- 最小样例（已验证）：

```python
import sys; sys.path.insert(0, r"E:/mubi-reviewflow")
from coscreen.peters_test import peters_test
rows = [{"study_id": f"S{i}", "events": e, "total": t} for i,(e,t) in enumerate(
    [(12,80),(20,100),(5,60),(30,120),(8,70),(15,90)], 1)]
pt = peters_test(rows)
# 实测: n_studies=6, n_included=6, statistic=-25.165948, df=4, p_value=1.5e-05,
#       coefficients['slope_on_inverse_total']['estimate']=-156.246583
```

- 输出数值字段：`n_studies, n_included, excluded_studies, coefficients{intercept, slope_on_inverse_total}（各含 estimate,se,ci_low,ci_high,ci_method）, statistic_name, statistic, df, p_value, p_value_method, residual_sigma, ...`（`coscreen/peters_test.py:215-256`）。
- HTTP：`GET /api/tasks/{task_id}/analysis/single-group/peters`（`custom_backend/peters_view.py:8`）。

---

## 9. Trim-and-fill（Duval-Tweedie L0，固定效应）

- 入口：`trim_and_fill(rows, measure, side, *, confidence_level=0.95) -> dict`，`coscreen/trim_fill.py:122-229`；行校验 `:34-68`（每行 `{"study_id","measure"(须与入参一致),"estimate","se"}`，≥2 行；RATIOS 内部取 log，se 须为分析尺度）。
- 最小样例（已验证）：

```python
import sys; sys.path.insert(0, r"E:/mubi-reviewflow")
from coscreen.trim_fill import trim_and_fill
rows = [{"study_id": f"S{i}", "measure": "SMD", "estimate": est, "se": se}
        for i,(est,se) in enumerate(
            [(-0.2,0.30),(-0.3,0.25),(-0.5,0.20),(-0.7,0.15),(-0.9,0.12),(-1.1,0.10)], 1)]
tf = trim_and_fill(rows, "SMD", "left")
# 实测: n_missing=3, observed.pooled=-0.838847, adjusted.pooled=-0.951948, imputed 3 篇
```

- 输出数值字段：`n_studies, n_missing, confidence_level, estimate_scale, analysis_scale, se_scale, observed{pooled,se,ci_low,ci_high}, adjusted{...}, imputed_studies[], iterations, ...`（`coscreen/trim_fill.py:201-229`；observed/adjusted 由 `_pooled_result` 生成 `:107-119`）。
- HTTP：`POST /api/tasks/{task_id}/analysis/publication-bias/trim-fill`（`custom_backend/main.py:3480-3488`）；扩展版 `trim_fill_extended` 同路由分支（`:3490-3491`，`coscreen/trim_fill_extended.py`）。

## 9bis.（顺带可得）Begg 秩相关 / Rosenthal fail-safe N

- 入口：`diagnose_publication_bias(rows, measure, method, *, null_effect=0.0, target_effect=None) -> dict`，`coscreen/publication_bias.py:88-197`；`method ∈ {rank_correlation(Begg-Mazumdar), rosenthal_fail_safe_n}`（`:102-108`；orwin 明确未实现 `:102-106`）。
- 最小样例（已验证）：10 行 `{"study_id","measure":"SMD","estimate","se"}` -> Begg `statistic(kendall_tau_b)=0.911111, p_value=0.000245`；Rosenthal `fail_safe_n=673, sum_z=-51.221934, combined_z=-16.197798`。
- 输出字段：公共 `method, measure, n_studies, effect_scale, small_study_warning, limitation`（`:70-85`）；Begg 另有 `method_name, statistic_name, statistic, p_value, p_value_method, assumptions`（`:151-163`）；Rosenthal 另有 `null_effect, sum_z, combined_z, combined_p_value, alpha_two_sided, critical_z, formula_value, fail_safe_n, assumed_unpublished_z, assumptions`（`:180-196`）。
- HTTP：`GET /api/tasks/{task_id}/analysis/publication-bias`（`custom_backend/main.py:3420-3429`）。

---

## 10. 不可数值验证 / 非统计内核的模块（基准排除清单）

以下模块为 UI、导出、报告、流程或存储类，无可对拍的统计量输出，建议排除在统计基准之外：

- 导出/报告：`coscreen/export.py`、`coscreen/manuscript_builder.py`（含 Egger 字段引用 `:4956` 属报告拼装）、`coscreen/research_trace_export.py`、`custom_backend/analysis_audit_report.py`（docx/pptx/csv/tex 审计导出）。
- 图形渲染：`coscreen/svg_render.py`、`coscreen/evidence_mapper.py`。
- PRISMA 流程表单：`coscreen/stats/prisma.py`、`coscreen/stats/prisma_form.py`、`custom_backend/prisma_task_metrics.py`（计数类可另做一致性检查，非统计推断）。
- GRADE/风险偏倚评估流程：`coscreen/grade_assessor.py`、`coscreen/rob_storage.py`、`coscreen/risk_of_bias_contract.py`、`rob*_sensitivity.py`（证据分级规则引擎）。
- 筛选/去重/全文/工作流存储：`coscreen/screening_rounds.py`、`screening_arbitration.py`、`dedup.py`、`fulltext.py`、`review_workflow.py`、`tasks.py`、`db.py`、`local_storage.py`、`analysis_storage.py`、`workspace_undo.py` 等。
- 前端与后端表现层：`custom_frontend/`、`custom_backend/main.py` 的鉴权/上传/任务管理路由。

---

## 11. 夹具配方汇总表

| 方法 | 函数（path:line） | 输入键 | 输出键（数值） | 建议断言字段 |
|---|---|---|---|---|
| IV 固定效应 | `_synthesize_rows` coscreen/review_analysis.py:487 | rows[{study_id, estimate, se}] + measure="MD" + model="fixed" | pooled_analysis, ci_analysis_low/high, q, i2_percent | pooled_analysis, ci_analysis_low, ci_analysis_high, q, i2_percent |
| IV DL 随机 | 同上 model="random" | 同上 | + tau2, prediction_interval_analysis_95 | pooled_analysis, tau2, q, i2_percent, prediction_interval_analysis_95 |
| IV REML 随机 | 同上 model="random_reml"（τ² 来自 profile_reml_tau2 coscreen/meta_regression_model.py:82） | 同上 | 同 DL（critical 用 norm.ppf，review_analysis.py:515） | pooled_analysis, tau2, ci_analysis_low/high |
| IV Paule-Mandel（备选） | 同上 model="random_pm" | 同上 | 同上 | tau2, pooled_analysis |
| （REML τ² 单测） | `profile_reml_tau2` coscreen/meta_regression_model.py:82 | effects[], variances[] | (tau2, objective) | tau2 |
| Mantel-Haenszel OR/RR/RD | `pool_mantel_haenszel` coscreen/mantel_haenszel.py:61 | studies[{study_id, events_t, total_t, events_c, total_c}] + measure | pooled, ci_low, ci_high, se, se_scale, n_studies | pooled, ci_low, ci_high, se, n_studies |
| Peto OR | `pool_peto` coscreen/peto.py:7 | 同上四计数（无 measure 入参） | pooled, ci_low, ci_high, se, n_studies, study_statistics[].variance | pooled, ci_low, ci_high, se |
| 异质性区间 | `i2_h_interval` coscreen/heterogeneity_interval.py:20 | q, k(, level) | i2_percent, i2_ci_percent, h, h_ci | i2_percent, i2_ci_percent, h_ci |
| Cohen κ | `cohen_kappa` coscreen/stats/kappa.py:64 | labels_a[], labels_b[](, labels) | KappaResult.kappa/po/pe/n | kappa, po, pe, n |
| Light κ | `light_kappa` coscreen/stats/kappa.py:147 | {coder: labels[]} | float 或 None | 返回值（浮点） |
| 编码矩阵 κ/Light | `merge_coding`+`summarize_coding` coscreen/stats/coding_agreement.py:184/574 | {coder: DataFrame[zotero_key,维度...]} + dtype_map | summary.agreement_rate / per_dimension[].kappa,light | agreement_rate, per_dimension[].kappa, per_dimension[].light |
| 字段级双人 κ | `summarize_agreement` coscreen/coding_agreement.py:9 | records[{item_key,rater_id,field_key,value}] + rater_ids + field_type | agreement_rate, cohen_kappa, n_paired | agreement_rate, cohen_kappa |
| 网络 Meta（纯函数 GLS） | `synthesize_network` coscreen/network_gls.py:101 | studies[{study_id, covariance_scale, contrasts[{treatment,comparator,estimate}], covariance}] + measure + reference | estimates[].estimate/se/ci_low/ci_high, residual_q, residual_df, residual_p, n_contrasts | residual_q, estimates[].estimate, estimates[].se |
| 网络 Meta（DB WLS） | `network_analysis.synthesize` coscreen/network_analysis.py:12 | DB 夹具（"A vs B" comparison）+ outcome/timepoint/measure/reference | estimates[].estimate/se/ci_low/ci_high, residual_q, residual_df, residual_p | residual_q, estimates[].estimate |
| Egger | `small_study_effects` coscreen/advanced_analysis.py:98 | DB 夹具（≥10 研究）+ comparison/outcome/timepoint/measure | egger_intercept, egger_p, n_studies | egger_intercept, egger_p |
| Peters | `peters_test` coscreen/peters_test.py:161 | rows[{study_id, events, total}] | statistic, df, p_value, coefficients.slope_on_inverse_total.estimate | statistic, p_value, coefficients.slope_on_inverse_total.estimate |
| Trim-fill | `trim_and_fill` coscreen/trim_fill.py:122 | rows[{study_id, measure, estimate, se}] + measure + side | n_missing, observed.pooled, adjusted.pooled, imputed_studies | n_missing, observed.pooled, adjusted.pooled |
| （备选）Begg/Rosenthal | `diagnose_publication_bias` coscreen/publication_bias.py:88 | rows[{study_id, measure, estimate, se}] + measure + method | statistic/p_value（Begg）；fail_safe_n/sum_z（Rosenthal） | Begg: statistic, p_value；Rosenthal: fail_safe_n |

### DB 夹具配方（synthesize / network_analysis / small_study_effects 共用）

`init_db(tmp)`（coscreen/db.py:603）→ 逐研究 `INSERT INTO articles(zotero_key, ...)` → `save_study(db, sid, label, [key])`（coscreen/review_analysis.py:93）→ `save_effect(db, sid, comparison, outcome, timepoint, measure, estimate, se, key, entry_method="manual")`（coscreen/review_analysis.py:283）。SMD 度量在 DB 路径需额外满足 SMD 契约（coscreen/pool_compatibility.py:54-85），基准夹具建议用 MD/RD。

### 与 A1 对齐说明

- "DL 与 REML 两口径"：`_synthesize_rows` 的 `random`（DL）与 `random_reml`（REML）同函数同输入，仅 model 参数不同，可复用同一 rows 夹具直接对拍两口径 τ²。
- "Fleiss"：仓库未实现（见 §5），建议基准以 Light κ 替代或自带 statsmodels `fleiss_kappa` 参考值并注明口径差异。
