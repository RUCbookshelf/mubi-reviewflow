"""S0 任务 2:快速回放引擎(numpy/scipy 向量化,算法与 coscreen.al.simulation 语义一致)。

与产品 :func:`coscreen.al.simulation.replay` 的语义契约(见该模块 docstring 与
README.md 的参数提取表):

- 同 warmup=5、自第 5 条起每筛 1 条**全量 refit**(TF-IDF 在"迄今全部已筛标签"
  上重拟合)+ ``MultinomialNB(alpha=1.0)`` + 对剩余队列重排;
- 重排键序基准 = 当前队列顺序(同分稳定保持既有先后);
- ``max``/``uncertainty``/``mixed`` 策略定义、mixed 的 95/5 精确算法(洗牌先
  消耗随机流、每位置恰好消耗一次 ``rng.random()``、``order.index`` 换位)与
  产品 ``strategies.query_order`` 完全一致——本模块提供 O(1) 查位的等价
  重实现,``test_replay.py`` 断言与产品函数对随机输入逐位同序;
- WSS@95/n_to_95/r95/ttd_mean 的公式与浮点运算顺序与产品 ``_metrics`` 一致。

数值设计(与产品逐位对齐的平台依据,x86-64 Linux / numpy 2.x,
``test_replay.py`` 含平台自检):

- ``np.log``/``np.exp`` 与 ``math.log``/``math.exp`` 在 float64 上逐位相同;
- ``csr.sum(axis=0)`` 对每列按行序顺序累加,与产品 NB 的逐文档 dict 累加
  逐位相同 ⇒ 类内特征和逐位一致;
- idf 的 ``(1+n)/(1+df)`` 浮点除法与 Python int/int 除法逐位相同;
- **残余噪声**(~1e-16 相对量级,实证不影响轨迹,见 README):L2 范数
  (产品按文档内首现序求和,本引擎按列序)、NB 分母(产品 dict 序顺序和,
  本引擎 pairwise)、jll 点积(首现序 vs CSR 列序)。完全相同的行(重复
  文档)在双方产生逐位相同的分数 ⇒ 产品中的精确同分在本引擎仍是精确同分,
  稳定排序的打破方式一致。

研究扩展(默认全关,不影响产品模式;差异矩阵见 README):

- ``priors``:ASReview 论文语义的先验(``RandomState(seed)`` 无放回抽 1 纳入
  +1 排除,先纳入后排除);
- ``balance``:``True``/``"double"`` = ASReview v1 论文版 dynamic resampling
  (DoubleBalance,a=2.155 / alpha=0.94 / b=0.789 / beta=1.0;
  ``np.random.RandomState(seed)`` 播种一次、随机流跨训练步持续;单类时退化
  为不重采样);``"balanced"`` = ASReview **v3** 论文语义的 Balanced 样本权重
  (``asreview/models/balancers.py``,非重采样:class-1 权重 1.0、class-0 权重
  n1/(ratio·n0),再整体归一到权重和 = n;ratio 经 :data:`balance_ratio` 传入,
  调优研究取 9.8)。NB 路径按类内加权和 + 加权类先验解析实现(与 sklearn
  ``MultinomialNB.fit(sample_weight=...)`` 等价,见 _nb_fit_score);
- ``classifier="svm"``:``sklearn.svm.LinearSVC(loss="squared_hinge", C=svm_c,
  random_state=seed)``(ASReview v3 ``models/classifiers.py::SVM`` 即 sklearn
  LinearSVC 直用;v3 learner 无 predict_proba 时以 decision_function 排序,
  与本引擎 max 策略的降序一致)。SVM 仅支持 ``strategy="max"``;
- ``feature_refit="fixed"``:全语料一次性 TF-IDF(ASReview 风格;产品为
  逐步 refit);
- ``tfidf_sublinear`` / ``tfidf_max_df``:sklearn 语义的 tf→1+ln(tf) 与词表
  剪枝(丢弃 df > max_df·n_fit 的词项;调优研究取 0.95)。注意本引擎分词器
  (产品自研)原生输出实词 uni+bigram,故 ASReview 调优配置的
  ``ngram_range=(1,2)`` 在本引擎为"已内建",无需参数。

轨迹日志:每次筛选一行(:meth:`ReplayResult.save_logs`),供 S1 停止规则
做"一次模拟、多规则后评估"。每训练步另附剩余池分数摘要
``remaining_score_sum`` / ``remaining_score_quantiles``
(:func:`_remaining_score_summary`,O(n)/步,纯日志 —— 不改变回放行为、
轨迹顺序与任何指标;notes/stopping_rules.md §3.2 的估计器需求)。

性能目标:8k 条单种子全量回放 ≤60s(实测见 README 基准表)。
"""

from __future__ import annotations

import csv
import json
import math
import random
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from coscreen import config  # noqa: E402  (只读:SIM_RECALL_TARGET / AL_* 常量)
from coscreen.al.features import TfidfVectorizer, tokenize  # noqa: E402  (只读)

__all__ = [
    "WARMUP", "MAX_RECORDS", "FastRecord", "ReplayResult", "replay_fast",
    "simulate_random_baseline_fast", "run_config", "load_dataset_csv",
    "make_synthetic_corpus", "DOUBLE_BALANCE_DEFAULTS", "TUNED_AL_DEFAULTS",
    "REMAINING_SCORE_QUANTILES",
]

#: 预热期条数(与 coscreen.al.simulation.WARMUP 一致)。
WARMUP: int = 5
#: 产品单次回放规模上限(本引擎不强制;超限由调用方自行抽样,见 README)。
MAX_RECORDS: int = 2000

_GOLD_LABELS = ("include", "exclude")

#: ASReview v3 调优研究(Ye et al. 2025)给出的升级配置默认参数:
#: Balanced ratio=9.8 / SVM C=0.11 / NB alpha=3.822 / TF-IDF max_df=0.95。
TUNED_AL_DEFAULTS: dict[str, float] = {
    "ratio": 9.8, "svm_c": 0.11, "alpha": 3.822, "max_df": 0.95,
}

#: ASReview v1.1 DoubleBalance(dynamic resampling "double")默认参数。
DOUBLE_BALANCE_DEFAULTS: dict[str, float] = {
    "a": 2.155, "alpha": 0.94, "b": 0.789, "beta": 1.0,
}

#: 每训练步记录的剩余池分数分位格(notes/stopping_rules.md §3.2 估计器需求
#: [q10,q25,q50,q75,q90] ∪ 调度任务 [0.5,0.75,0.9,0.95,0.99,1.0] 的并集;
#: 线性插值分位,np.quantile 语义)。
REMAINING_SCORE_QUANTILES: tuple[float, ...] = (
    0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99, 1.0)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FastRecord:
    """与 ``coscreen.models.Article`` 鸭子类型兼容的最小题录(四字段拼接一致)。"""

    zotero_key: str
    title: str = ""
    abstract: str = ""
    authors: str = ""
    journal: str = ""


@dataclass
class ReplayResult:
    """一次快速回放的指标 + 完整轨迹(指标字段与产品 ``SimResult`` 对齐)。"""

    strategy: str
    seed: int
    n_records: int
    n_relevant: int
    n_to_95: int
    wss95: float
    r95: float
    ttd_mean: float
    # 扩展字段
    n_trained_steps: int = 0
    elapsed_s: float = 0.0
    config: dict = field(default_factory=dict)
    trajectory: list[dict] = field(default_factory=list)

    def metrics_dict(self) -> dict:
        return {
            "strategy": self.strategy, "seed": self.seed,
            "n_records": self.n_records, "n_relevant": self.n_relevant,
            "n_to_95": self.n_to_95, "wss95": self.wss95, "r95": self.r95,
            "ttd_mean": self.ttd_mean,
        }

    def to_dict(self) -> dict:
        return {
            **self.metrics_dict(),
            "n_trained_steps": self.n_trained_steps,
            "elapsed_s": self.elapsed_s,
            "config": self.config,
            "trajectory": self.trajectory,
        }

    def save_logs(self, prefix: str | Path) -> tuple[Path, Path]:
        """写轨迹 JSONL(每次筛选一行)+ 摘要 JSON;返回 (jsonl, json) 路径。"""
        prefix = Path(prefix)
        prefix.parent.mkdir(parents=True, exist_ok=True)
        jsonl_path = prefix.with_suffix(".trajectory.jsonl")
        json_path = prefix.with_suffix(".summary.json")
        summary = {k: v for k, v in self.to_dict().items() if k != "trajectory"}
        summary["n_trajectory_rows"] = len(self.trajectory)
        with jsonl_path.open("w", encoding="utf-8") as f:
            for row in self.trajectory:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        json_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return jsonl_path, json_path


# ---------------------------------------------------------------------------
# 查询策略:产品 query_order 的逐位等价快速实现
# ---------------------------------------------------------------------------

def _max_order(queue: list[str], score_arr: np.ndarray) -> list[str]:
    """分数降序、同分稳定(等价于产品 ``sorted(keys, key, reverse=True)``)。

    ``np.argsort(-score, kind="stable")`` 与 Python 稳定排序 ``reverse=True``
    给出同一排列:相等元素保持原相对顺序。
    """
    perm = np.argsort(-score_arr, kind="stable")
    return [queue[i] for i in perm]


def _uncertainty_order(queue: list[str], score_arr: np.ndarray) -> list[str]:
    """|score-0.5| 升序、同分稳定(缺失分数按 0.0,与产品一致)。"""
    perm = np.argsort(np.abs(score_arr - 0.5), kind="stable")
    return [queue[i] for i in perm]


def _mixed_order(queue: list[str], score_arr: np.ndarray, seed: int,
                 score_by_key: dict[str, float] | None,
                 ratio: float | None = None) -> list[str]:
    """产品 mixed 策略的逐位复刻(每位置恰好一次 ``rng.random()``,O(1) 查位)。

    与 ``coscreen.al.strategies.query_order`` 的唯一实现差异:用键→下标映射
    替代 ``order.index(key)``(键唯一 ⇒ 同一元素;交换后同步维护映射)。
    """
    ratio = config.AL_MIXED_RANDOM_RATIO if ratio is None else ratio
    rng = random.Random(seed)
    shuffled = list(queue)
    rng.shuffle(shuffled)  # 洗牌先消耗随机流(与产品一致)
    if not score_by_key:
        return shuffled
    order = _max_order(queue, score_arr)
    index_of = {k: i for i, k in enumerate(order)}
    pos = 0
    n = len(order)
    for i in range(n):
        if rng.random() < ratio and pos < len(shuffled):
            key = shuffled[pos]
            j = index_of[key]
            order[i], order[j] = order[j], order[i]
            index_of[order[i]] = i
            index_of[order[j]] = j
            pos += 1
    return order


def _rank_queue(queue: list[str], score_arr: np.ndarray, strategy: str, seed: int,
                score_by_key: dict[str, float] | None) -> list[str]:
    if strategy == "max":
        return _max_order(queue, score_arr)
    if strategy == "uncertainty":
        return _uncertainty_order(queue, score_arr)
    if strategy == "mixed":
        return _mixed_order(queue, score_arr, seed, score_by_key)
    raise ValueError(f"未知查询策略: {strategy!r}")


# ---------------------------------------------------------------------------
# 语料索引:一次性分词 + 全语料固定词表计数矩阵
# ---------------------------------------------------------------------------

def _article_text(rec) -> str:
    """与 ActiveLearningRanker._text / simulation._article_text 一致的四字段拼接。"""
    return " ".join((rec.title or "", rec.abstract or "",
                     rec.authors or "", rec.journal or ""))


class _CorpusIndex:
    """全语料固定词表 + 文档-词计数矩阵(CSR,float64,列序 canonical)。"""

    def __init__(self, records: list) -> None:
        self.keys: list[str] = [r.zotero_key for r in records]
        counters: list[Counter] = []
        vocab_set: set[str] = set()
        for rec in records:
            cnt = Counter(tokenize(_article_text(rec)))
            counters.append(cnt)
            vocab_set.update(cnt)
        self.vocab: list[str] = sorted(vocab_set)
        self.term_index: dict[str, int] = {t: i for i, t in enumerate(self.vocab)}
        n = len(self.keys)
        V = len(self.vocab)
        indptr = np.zeros(n + 1, dtype=np.int64)
        indices: list[int] = []
        data: list[float] = []
        self.doc_terms: list[np.ndarray] = []
        for i, cnt in enumerate(counters):
            cols = sorted(self.term_index[t] for t in cnt)
            self.doc_terms.append(np.array(cols, dtype=np.int64))
            indices.extend(cols)
            data.extend(float(cnt[self.vocab[c]]) for c in cols)
            indptr[i + 1] = len(indices)
        self.counts = csr_matrix(
            (np.array(data, dtype=np.float64),
             np.array(indices, dtype=np.int32),
             indptr.astype(np.int32)),
            shape=(n, V),
        )
        self.n = n
        self.V = V


# ---------------------------------------------------------------------------
# ASReview v1 DoubleBalance(dynamic resampling double)—— 论文语义平衡采样
# ---------------------------------------------------------------------------

def _random_round(value: float, rs: np.random.RandomState) -> int:
    base = int(math.floor(value))
    if rs.rand() < value - base:
        base += 1
    return base


def _fill_training(src: np.ndarray, n_train: int,
                   rs: np.random.RandomState) -> np.ndarray:
    n_copy = int(n_train / len(src))
    n_sample = n_train - n_copy * len(src)
    dest = np.tile(src, n_copy)
    if n_sample > 0:
        dest = np.append(dest, rs.choice(src, n_sample, replace=False))
    return dest


def _double_balance_sample(y: np.ndarray, rs: np.random.RandomState,
                           params: dict[str, float]) -> np.ndarray:
    """ASReview DoubleBalance:返回重采样后的训练行下标(0..len(y)-1 内)。

    单类时退化为不重采样(SimpleBalance fallback)。词表不变(重采样只改变
    训练行,与 ASReview 的固定特征矩阵语义一致)。
    """
    one_idx = np.flatnonzero(y == 1)
    zero_idx = np.flatnonzero(y == 0)
    if one_idx.size == 0 or zero_idx.size == 0:
        return np.arange(len(y))
    a, alpha = params["a"], params["alpha"]
    b, beta = params["b"], params["beta"]
    n_one, n_zero = int(one_idx.size), int(zero_idx.size)
    n_train = n_one + n_zero
    one_weight = a * (n_one / n_zero) ** (-alpha)
    zero_weight = 1.0 - (1.0 - b) * (1.0 + math.log(n_train)) ** (-beta)
    tot_zo_weight = one_weight * n_one + zero_weight * n_zero
    n_one_train = _random_round(one_weight * n_one * n_train / tot_zo_weight, rs)
    n_one_train = max(1, min(n_train - 2, n_one_train))
    n_zero_train = n_train - n_one_train
    one_train = _fill_training(one_idx, n_one_train, rs)
    zero_train = _fill_training(zero_idx, n_zero_train, rs)
    all_idx = np.concatenate([one_train, zero_train])
    rs.shuffle(all_idx)
    return all_idx


def _balanced_class_weights(n1: int, n0: int, ratio: float) -> tuple[float, float]:
    """ASReview v3 ``Balanced`` 样本权重(``asreview/models/balancers.py``)。

    语义(与 sklearn ``compute_sample_weight`` + 归一化逐项对应):

    - class-1(相关)基准权重 1.0,class-0(不相关)权重
      ``n1 / (ratio * n0)`` ⇒ 等效"class-0:class-1 有效样本比 = ratio:1";
    - 整体归一 ``w *= n / sum(w)``(n = n1+n0;常数因子对类内比与排序无影响,
      但会通过平滑 alpha 影响 NB 后验,故按 v3 原式保留);
    - 需两类均非空(单类由调用方 fallback 不加权)。
    """
    w0_raw = n1 / (ratio * n0)
    scale = (n1 + n0) / (n1 + n0 * w0_raw)
    return scale, w0_raw * scale  # (w1, w0)


# ---------------------------------------------------------------------------
# 指标:与产品 simulation._metrics 运算顺序一致(逐位同值)
# ---------------------------------------------------------------------------

def _compute_metrics(strategy: str, seed: int, n_records: int, n_relevant: int,
                     ttd: list[int], recall_target: float) -> tuple:
    if n_relevant == 0 or n_records == 0:
        return strategy, seed, n_records, 0, 0, 0.0, 0.0, 0.0
    sorted_ttd = sorted(ttd)
    n_to_95 = n_records
    r95 = 1.0
    for count, position in enumerate(sorted_ttd, start=1):
        if count / n_relevant >= recall_target - 1e-12:
            n_to_95 = position
            r95 = count / n_relevant
            break
    wss95 = (n_records - n_to_95) / n_records - (1.0 - recall_target)
    wss95 = max(-1.0, min(1.0, wss95))
    ttd_mean = sum(sorted_ttd) / len(sorted_ttd)
    return strategy, seed, n_records, n_relevant, n_to_95, wss95, r95, ttd_mean


def _validated_gold(keys: list[str], gold: dict[str, str]) -> dict[str, str]:
    """与产品 ``_validated_gold`` 同语义:缺失 → KeyError;非法 → ValueError。"""
    missing = [k for k in keys if k not in gold]
    if missing:
        sample = ", ".join(repr(k) for k in missing[:5])
        raise KeyError(f"黄金标准缺少 {len(missing)} 条文献的标签，例如: {sample}")
    labels: dict[str, str] = {}
    for k in keys:
        label = str(gold[k]).strip().lower()
        if label not in _GOLD_LABELS:
            raise ValueError(
                f"黄金标签必须是 include/exclude，键 {k!r} 得到 {gold[k]!r}")
        labels[k] = label
    return labels


def _select_priors(keys: list[str], labels: dict[str, str],
                   n_included: int, n_excluded: int, seed: int) -> list[str]:
    """ASReview ``sample_prior_knowledge`` 语义:``RandomState(seed)`` 无放回
    ``choice``,先纳入后排除;先验作为最初已筛记录(位置 1..k)。"""
    rs = np.random.RandomState(seed)
    inc = np.array([i for i, k in enumerate(keys) if labels[k] == "include"])
    exc = np.array([i for i, k in enumerate(keys) if labels[k] == "exclude"])
    if inc.size < n_included:
        raise ValueError(f"先验纳入数 {n_included} 超过语料纳入总数 {inc.size}")
    if exc.size < n_excluded:
        raise ValueError(f"先验排除数 {n_excluded} 超过语料排除总数 {exc.size}")
    picked_inc = rs.choice(inc, n_included, replace=False) if n_included else []
    picked_exc = rs.choice(exc, n_excluded, replace=False) if n_excluded else []
    return [keys[i] for i in list(picked_inc) + list(picked_exc)]


# ---------------------------------------------------------------------------
# 稀疏工具
# ---------------------------------------------------------------------------

def _scale_normalize(m: csr_matrix, idf_full: np.ndarray) -> csr_matrix:
    """tf×idf 后按行 L2 归一化(除法用 ``x / norm``,与产品运算一致)。

    行内平方和按 CSR 列序顺序累加(np.add.reduceat 段内顺序累加);空行范数
    为 0、无存储元素,不会除零。
    """
    out = csr_matrix(m, copy=True)
    out.data = out.data * idf_full[out.indices]
    sq = out.data * out.data
    indptr = out.indptr
    nnz_rows = np.diff(indptr) > 0
    norms_sq = np.zeros(m.shape[0], dtype=np.float64)
    if nnz_rows.any():
        norms_sq[nnz_rows] = np.add.reduceat(sq, indptr[:-1][nnz_rows])
    norms = np.sqrt(norms_sq)
    # 范数 0 的行(全部词项在拟合语料外 ⇒ 加权后全 0)保持 0:全 0 行经
    # NB 点积后 jll=0 ⇒ 得分 = 纯先验,与产品"无已知词项 → 纯先验"精确一致
    # (除以 0 会得 NaN 并把该行排到最后,偏离产品语义;真实 SYNERGY 语料
    # 在 product refit 模式下会触发此边缘)。
    safe_norms = np.where(norms > 0.0, norms, 1.0)
    out.data = out.data / np.repeat(safe_norms, np.diff(indptr))
    return out


def _rows_to_matrix(rows: list[dict[str, float]], vocab: dict[str, int],
                    n_cols: int) -> csr_matrix:
    """产品向量化器的 dict 行 → CSR(列序 canonical;数值不变)。"""
    indptr = np.zeros(len(rows) + 1, dtype=np.int64)
    indices: list[int] = []
    data: list[float] = []
    for i, row in enumerate(rows):
        for t, v in sorted(row.items(), key=lambda kv: vocab[kv[0]]):
            indices.append(vocab[t])
            data.append(v)
        indptr[i + 1] = len(indices)
    return csr_matrix(
        (np.array(data, dtype=np.float64), np.array(indices, dtype=np.int32),
         indptr.astype(np.int32)),
        shape=(len(rows), n_cols),
    )


def _remaining_score_summary(score_arr: np.ndarray) -> tuple[float, list[float]]:
    """剩余池分数摘要(S1 估计器需求,notes §3.2):分数和 + 分位向量。

    纯日志增强:只消费已算出的分数数组,不触碰任何回放状态。成本 O(n)/步
    (``np.sum`` 线性;``np.quantile`` 走 introselect partition,平均线性)。
    数值与既有字段同为 float64 经 JSON 序列化的完整精度。
    """
    arr = np.asarray(score_arr, dtype=np.float64)
    total = float(arr.sum())
    if arr.size == 0:
        return total, [float("nan")] * len(REMAINING_SCORE_QUANTILES)
    qs = np.quantile(arr, REMAINING_SCORE_QUANTILES)
    return total, [float(v) for v in qs]


def _traj_row(position: int, key: str, label: str, phase: str,
              score_head: float | None, n_labeled: int, n_found: int,
              n_relevant: int, n_remaining: int, exclude_streak: int,
              trained: bool, top: list | None, elapsed: float,
              remaining_sum: float | None = None,
              remaining_quantiles: list[float] | None = None) -> dict:
    return {
        "i": position,
        "record_id": key,
        "label": label,
        "phase": phase,
        "score_when_head": score_head,
        "n_labeled": n_labeled,
        "n_relevant_found": n_found,
        "n_relevant_total": n_relevant,
        "recall_so_far": (n_found / n_relevant) if n_relevant else 0.0,
        "n_remaining": n_remaining,
        "exclude_streak": exclude_streak,
        "trained_this_step": trained,
        "model_top_remaining": top,
        "remaining_score_sum": remaining_sum,
        "remaining_score_quantiles": remaining_quantiles,
        "step_elapsed_s": elapsed,
    }


# ---------------------------------------------------------------------------
# 主引擎
# ---------------------------------------------------------------------------

def replay_fast(
    records: list,
    gold: dict[str, str],
    strategy: str = config.AL_DEFAULT_STRATEGY,
    seed: int = config.AL_SEED,
    *,
    learn: bool = True,
    warmup: int = WARMUP,
    alpha: float = 1.0,
    recall_target: float = config.SIM_RECALL_TARGET,
    priors: tuple[int, int] | str | None = None,
    balance: bool | str = False,
    balance_params: dict[str, float] | None = None,
    balance_ratio: float = 1.0,
    classifier: str = "nb",
    svm_c: float = TUNED_AL_DEFAULTS["svm_c"],
    tfidf_sublinear: bool = False,
    tfidf_max_df: float | None = None,
    feature_refit: str = "product",
    top_k_log: int = 5,
    collect_trajectory: bool = True,
) -> ReplayResult:
    """产品 ``replay`` 的向量化等价实现(+可选研究扩展)。

    参数
    ----
    records:
        具有 ``zotero_key/title/abstract/authors/journal`` 属性的记录列表
        (``coscreen.models.Article`` 或 :class:`FastRecord`),键唯一;
        顺序 = 基准顺序(导入顺序)。
    gold:
        ``zotero_key -> "include" | "exclude"`` 完整标注(缺失 → KeyError)。
    strategy / seed:
        与产品一致;``max`` 默认,``mixed`` 由 ``random.Random(seed)`` 种子化。
    warmup:
        训练前的最少已筛条数(产品语义 = 5;之后每筛 1 条 refit 重排)。
    alpha:
        NB Lidstone 平滑(产品 = 1.0;ASReview 论文默认 3.822)。
    priors:
        ``None``/``"none"`` = 产品模式(无先验);``"asreview"`` = (1, 1);
        或显式 ``(n_included, n_excluded)``——``RandomState(seed)`` 无放回
        抽取,作为最初已筛记录并计入发现位次与训练集。
    balance:
        ``False``/``None``/``"none"`` = 不平衡(产品模式);``True``/``"double"`` =
        ASReview v1 DoubleBalance(dynamic resampling;重采样后 NB 的类先验取自
        重采样标签,与 sklearn ``fit_prior=True`` 一致,词表不受重采样影响);
        ``"balanced"`` = ASReview v3 Balanced 样本权重(配合 :data:`balance_ratio`,
        见 :func:`_balanced_class_weights`)。NB 路径下权重作用于类内特征和与类
        先验(与 sklearn ``sample_weight`` 一致,即 ASReview v3 语义);SVM 路径
        下逐样本传入 ``LinearSVC.fit``。
    balance_ratio:
        ``balance="balanced"`` 的 ratio(class-0:class-1 有效样本比;调优研究 9.8)。
    classifier:
        ``"nb"``(默认,MultinomialNB 语义)或 ``"svm"``(sklearn LinearSVC,
        ``loss="squared_hinge"``、``C=svm_c``、``random_state=seed``;以
        decision_function 分数排序 ⇒ 仅支持 ``max`` 策略)。
    tfidf_sublinear:
        True 时 tf → 1+ln(tf)(sklearn ``sublinear_tf`` 语义;对 train/candidate
        统一按文档内词频变换,与特征拟合语料无关)。
    tfidf_max_df:
        词表剪枝:丢弃拟合语料中 df > max_df·n_fit 的词项(sklearn ``max_df``
        float 语义;"fixed" 模式在全语料拟合,"product" 模式逐步在已筛标签拟合)。
    feature_refit:
        ``"product"`` = 每步在已筛标签上重拟合 TF-IDF(产品语义);
        ``"fixed"`` = 全语料一次性 TF-IDF(ASReview 语义)。
    """
    t_start = time.perf_counter()
    keys = [r.zotero_key for r in records]
    if len(set(keys)) != len(keys):
        raise ValueError("records 的 zotero_key 必须唯一")
    if strategy not in ("max", "uncertainty", "mixed"):
        raise ValueError(f"未知查询策略: {strategy!r}")
    if classifier == "svm" and strategy != "max":
        raise ValueError("classifier='svm' 仅支持 strategy='max'"
                         "(decision_function 分数无 [0,1] 概率语义)")
    if classifier not in ("nb", "svm"):
        raise ValueError(f"未知 classifier: {classifier!r}")
    if balance is True:
        bal_mode = "double"
    elif balance in (False, None, "none"):
        bal_mode = "none"
    elif balance in ("double", "balanced"):
        bal_mode = str(balance)
    else:
        raise ValueError(f"未知 balance: {balance!r}")
    if bal_mode == "balanced" and balance_ratio <= 0:
        raise ValueError(f"balance_ratio 必须为正,得到 {balance_ratio!r}")
    labels = _validated_gold(keys, gold)
    relevant = {k for k in keys if labels[k] == "include"}
    n_relevant = len(relevant)

    if priors in (None, "none", (0, 0)):
        prior_inc, prior_exc = 0, 0
    elif priors == "asreview":
        prior_inc, prior_exc = 1, 1
    else:
        prior_inc, prior_exc = int(priors[0]), int(priors[1])

    bal_params = dict(DOUBLE_BALANCE_DEFAULTS) if balance_params is None else dict(balance_params)
    # DoubleBalance 随机流跨训练步持续(镜像 ASReview 模型构造时一次播种)。
    balance_rs = np.random.RandomState(seed) if bal_mode == "double" else None

    index = _CorpusIndex(records)
    pos_of = {k: i for i, k in enumerate(index.keys)}
    n0, V = index.n, index.V

    # sublinear tf 是逐文档单元格变换(与拟合语料无关)⇒ 一次性预变换。
    counts_fit = index.counts
    if tfidf_sublinear:
        counts_fit = index.counts.copy()
        counts_fit.data = 1.0 + np.log(counts_fit.data)

    feats: csr_matrix | None = None
    if feature_refit == "fixed":
        if not tfidf_sublinear and tfidf_max_df is None:
            # 原路径(与既有轨迹逐位一致,勿动)。
            vec = TfidfVectorizer()
            rows = vec.fit_transform([_article_text(r) for r in records])
            feats = _rows_to_matrix(rows, vec.vocab, V)
        else:
            # sklearn 语义:smooth idf(n=全语料)+ sublinear tf + max_df 词表剪枝。
            df_full = np.zeros(V, dtype=np.int64)
            for cols_ in index.doc_terms:
                df_full[cols_] += 1
            nz = np.flatnonzero(df_full)
            idf_full = np.zeros(V, dtype=np.float64)
            idf_full[nz] = np.log((1.0 + n0) / (1.0 + df_full[nz])) + 1.0
            if tfidf_max_df is not None:
                nz = nz[df_full[nz] <= tfidf_max_df * n0]
            feats = _scale_normalize(counts_fit[:, nz], idf_full[nz])
    elif feature_refit != "product":
        raise ValueError(
            f"feature_refit 必须是 'product' 或 'fixed',得到 {feature_refit!r}")

    df = np.zeros(V, dtype=np.int64)
    trajectory: list[dict] = []
    n_trained_steps = 0

    def _features(train_pos_list: list[int], cand_pos_arr: np.ndarray):
        """返回 (train 归一化矩阵, cand 归一化矩阵, support)。fixed 模式
        support=None(词表 = 全语料固定词表,可能已被 max_df 剪枝)。"""
        if feature_refit == "fixed":
            return feats[train_pos_list], feats[cand_pos_arr], None
        n = len(train_pos_list)
        sup = np.flatnonzero(df)
        if tfidf_max_df is not None:
            sup = sup[df[sup] <= tfidf_max_df * n]
        idf_full = np.zeros(V, dtype=np.float64)
        idf_full[sup] = np.log((1.0 + n) / (1.0 + df[sup])) + 1.0
        Xt = _scale_normalize(counts_fit[train_pos_list], idf_full)
        Xc = _scale_normalize(counts_fit[cand_pos_arr], idf_full)
        return Xt, Xc, sup

    def _nb_fit_score(train_pos_list: list[int], y_train: list[int],
                      cand_pos_arr: np.ndarray) -> np.ndarray:
        """NB 拟合 + 候选打分;返回与 cand_pos_arr 对齐的分数数组。"""
        Xt, Xc, sup = _features(train_pos_list, cand_pos_arr)
        y_arr = np.asarray(y_train, dtype=np.int64)
        if bal_mode == "double" and len(y_arr) >= 2 and (y_arr == 1).any() and (y_arr == 0).any():
            # DoubleBalance 重采样(总行数不变,但含复制/抽样与洗牌 ⇒ 必须应用)
            sel = _double_balance_sample(y_arr, balance_rs, bal_params)
            Xt = Xt[sel]
            y_arr = y_arr[sel]
        n_tr = int(Xt.shape[0])
        n1 = int((y_arr == 1).sum())
        n0c = n_tr - n1
        # Balanced(v3 样本权重):w1/w0 作用于类内特征和与类先验
        #(与 sklearn MultinomialNB.fit(sample_weight=...) 逐项对应;单类 fallback
        # 不加权,镜像 v1 DoubleBalance 的单类退化)。
        if bal_mode == "balanced" and n1 and n0c:
            w1, w0 = _balanced_class_weights(n1, n0c, balance_ratio)
            cw1, cw0 = w1 * n1, w0 * n0c
            p1 = math.log(cw1 / (cw1 + cw0))
            p0 = math.log(cw0 / (cw1 + cw0))
        else:
            w1 = w0 = 1.0
            # 类先验:math.log(n_c/n),与产品逐位一致;单类 → -inf。
            p1 = math.log(n1 / n_tr) if n1 else -math.inf
            p0 = math.log(n0c / n_tr) if n0c else -math.inf
        s1 = np.asarray(Xt[y_arr == 1].sum(axis=0)).ravel()
        s0 = np.asarray(Xt[y_arr == 0].sum(axis=0)).ravel()
        if w1 != 1.0:
            s1 = s1 * w1
        if w0 != 1.0:
            s0 = s0 * w0
        # 词表宽:refit 模式 = 支撑集大小;fixed 模式 = 固定特征矩阵列数
        #(max_df 剪枝后可能 < 全语料 V)。
        Vt = int(sup.size) if sup is not None else int(Xt.shape[1])
        if sup is not None:
            s1v, s0v = s1[sup], s0[sup]
        else:
            s1v, s0v = s1, s0
        denom1 = float(s1v.sum()) + alpha * Vt
        denom0 = float(s0v.sum()) + alpha * Vt
        # 全宽特征对数概率向量(词表外为 0 ⇒ 候选行词表外项贡献恰为 0,
        # 与产品"忽略未见词项"等价)。
        F1 = np.zeros(int(Xc.shape[1]), dtype=np.float64)
        F0 = np.zeros(int(Xc.shape[1]), dtype=np.float64)
        if sup is not None:
            F1[sup] = np.log(s1v + alpha) - math.log(denom1)
            F0[sup] = np.log(s0v + alpha) - math.log(denom0)
        else:
            F1[:] = np.log(s1v + alpha) - math.log(denom1)
            F0[:] = np.log(s0v + alpha) - math.log(denom0)
        jll = Xc @ np.column_stack([F0, F1])
        j0 = p0 + jll[:, 0]
        j1 = p1 + jll[:, 1]
        hi = np.maximum(j0, j1)
        with np.errstate(under="ignore", divide="ignore", invalid="ignore"):
            le = hi + np.log(np.exp(j0 - hi) + np.exp(j1 - hi))
            le = np.where(np.isfinite(hi), le, hi)
            scores = np.exp(j1 - le)
        return scores

    def _svm_fit_score(train_pos_list: list[int], y_train: list[int],
                       cand_pos_arr: np.ndarray) -> np.ndarray:
        """LinearSVC 拟合 + 候选 decision_function(ASReview v3 SVM 语义)。"""
        import warnings as _warnings

        from sklearn.svm import LinearSVC
        Xt, Xc, _sup = _features(train_pos_list, cand_pos_arr)
        y_arr = np.asarray(y_train, dtype=np.int64)
        if bal_mode == "double" and len(y_arr) >= 2 and (y_arr == 1).any() and (y_arr == 0).any():
            sel = _double_balance_sample(y_arr, balance_rs, bal_params)
            Xt = Xt[sel]
            y_arr = y_arr[sel]
        sample_weight = None
        if bal_mode == "balanced":
            n1 = int((y_arr == 1).sum())
            n0c = int((y_arr == 0).sum())
            if n1 and n0c:
                w1, w0 = _balanced_class_weights(n1, n0c, balance_ratio)
                sample_weight = np.where(y_arr == 1, w1, w0)
        # random_state 固定为运行种子(v3 learner 未播种 ⇒ 不可复现;本研究
        # 以可复现优先,liblinear 的随机性仅影响坐标顺序,不影响最优解)。
        clf = LinearSVC(C=svm_c, loss="squared_hinge", random_state=seed)
        with _warnings.catch_warnings():
            _warnings.simplefilter("ignore")  # ConvergenceWarning 只影响精度尾数
            clf.fit(Xt, y_arr, sample_weight=sample_weight)
        return np.asarray(clf.decision_function(Xc), dtype=np.float64)

    _fit_score = _svm_fit_score if classifier == "svm" else _nb_fit_score

    def _score_order(order_list: list[str]) -> tuple[np.ndarray, dict[str, float]]:
        cand_pos = np.array([pos_of[k] for k in order_list], dtype=np.int64)
        arr = _fit_score(labeled_pos, y_labeled, cand_pos)
        return arr, {k: float(s) for k, s in zip(order_list, arr)}

    def _log_ranking(trained: bool, sdict: dict[str, float] | None,
                     new_order: list[str],
                     remaining: tuple[float, list[float]] | None = None) -> None:
        """把"当步重训后的 top 剩余分数摘要"附到最后一条轨迹行。"""
        if not collect_trajectory or not trajectory:
            return
        trajectory[-1]["trained_this_step"] = trained
        if trained and sdict:
            trajectory[-1]["model_top_remaining"] = [
                [k2, sdict[k2]] for k2 in new_order[:top_k_log] if k2 in sdict]
            if remaining is not None:
                trajectory[-1]["remaining_score_sum"] = remaining[0]
                trajectory[-1]["remaining_score_quantiles"] = remaining[1]

    # ---- 回放主体(控制流与产品 _simulate 一致)----
    if n_relevant == 0:
        m = _compute_metrics(strategy, seed, n0, 0, [], recall_target)
        return ReplayResult(*m, n_trained_steps=0,
                            elapsed_s=time.perf_counter() - t_start,
                            config={}, trajectory=[])

    labeled_pos: list[int] = []
    y_labeled: list[int] = []
    ttd: list[int] = []
    position = 0
    exclude_streak = 0
    last_scores: dict[str, float] | None = None

    # ---- 初始顺序(warm-start:空分数)或先验后首排 ----
    if prior_inc or prior_exc:
        prior_keys = _select_priors(index.keys, labels, prior_inc, prior_exc, seed)
        prior_set = set(prior_keys)
        for k in prior_keys:
            position += 1
            p = pos_of[k]
            labeled_pos.append(p)
            y_labeled.append(1 if labels[k] == "include" else 0)
            if k in relevant:
                ttd.append(position)
                exclude_streak = 0
            else:
                exclude_streak += 1
            df[index.doc_terms[p]] += 1
            if collect_trajectory:
                trajectory.append(_traj_row(
                    position, k, labels[k], "prior", None, len(labeled_pos),
                    len(ttd), n_relevant, n0 - position, exclude_streak,
                    False, None, 0.0))
        rest = [k for k in index.keys if k not in prior_set]
        if rest and learn and len(labeled_pos) >= max(warmup, 1):
            arr, sdict = _score_order(rest)
            order = _rank_queue(rest, arr, strategy, seed, sdict)
            last_scores = sdict
            n_trained_steps += 1
            _log_ranking(True, sdict, order,
                         _remaining_score_summary(arr) if collect_trajectory else None)
        else:
            order = _rank_queue(rest, np.zeros(len(rest)), strategy, seed, None)
    else:
        order = _rank_queue(list(index.keys), np.zeros(n0), strategy, seed, None)

    while order:
        t_step = time.perf_counter()
        key = order.pop(0)
        position += 1
        p = pos_of[key]
        labeled_pos.append(p)
        y_labeled.append(1 if labels[key] == "include" else 0)
        if key in relevant:
            ttd.append(position)
            exclude_streak = 0
        else:
            exclude_streak += 1
        df[index.doc_terms[p]] += 1
        done = len(ttd) == n_relevant

        trained = False
        new_order = order
        rem_summary: tuple[float, list[float]] | None = None
        if learn and len(labeled_pos) >= warmup and order and not done:
            arr, sdict = _score_order(order)
            new_order = _rank_queue(order, arr, strategy, seed, sdict)
            last_scores = sdict
            trained = True
            n_trained_steps += 1
            if collect_trajectory:
                rem_summary = _remaining_score_summary(arr)

        if collect_trajectory:
            score_head = last_scores.get(key) if last_scores else None
            n_prior = prior_inc + prior_exc
            if position <= n_prior:
                phase = "prior"
            elif position <= max(warmup, n_prior):
                phase = "warmup"
            else:
                phase = "model"
            trajectory.append(_traj_row(
                position, key, labels[key], phase,
                score_head, len(labeled_pos), len(ttd), n_relevant,
                len(order), exclude_streak, trained,
                ([[k2, last_scores[k2]] for k2 in new_order[:top_k_log]
                  if k2 in last_scores] if trained and last_scores else None),
                time.perf_counter() - t_step,
                rem_summary[0] if rem_summary else None,
                rem_summary[1] if rem_summary else None))

        order = new_order
        if done:
            break

    m = _compute_metrics(strategy, seed, n0, n_relevant, ttd, recall_target)
    cfg = {
        "warmup": warmup, "alpha": alpha, "learn": learn,
        "priors": [prior_inc, prior_exc], "balance": balance,
        "balance_ratio": balance_ratio if bal_mode == "balanced" else None,
        "classifier": classifier,
        "svm_c": svm_c if classifier == "svm" else None,
        "tfidf_sublinear": tfidf_sublinear,
        "tfidf_max_df": tfidf_max_df,
        "feature_refit": feature_refit, "recall_target": recall_target,
        "balance_params": bal_params if bal_mode == "double" else None,
    }
    return ReplayResult(*m, n_trained_steps=n_trained_steps,
                        elapsed_s=time.perf_counter() - t_start,
                        config=cfg, trajectory=trajectory)


def simulate_random_baseline_fast(records: list, gold: dict[str, str],
                                  seed: int = config.AL_SEED) -> ReplayResult:
    """纯随机顺序基线(产品 ``simulate_random_baseline`` 的等价实现)。"""
    return replay_fast(records, gold, "mixed", seed, learn=False)


# ---------------------------------------------------------------------------
# run_config / 数据加载 / 合成语料
# ---------------------------------------------------------------------------

def run_config(
    dataset_csv: str | Path | None = None,
    records: list | None = None,
    gold: dict[str, str] | None = None,
    *,
    preset: str = "product",
    seed: int = config.AL_SEED,
    strategy: str = config.AL_DEFAULT_STRATEGY,
    priors: tuple[int, int] | str | None = None,
    balance: bool | str | None = None,
    balance_ratio: float | None = None,
    alpha: float | None = None,
    classifier: str | None = None,
    svm_c: float | None = None,
    warmup: int | None = None,
    feature_refit: str | None = None,
    tfidf_sublinear: bool | None = None,
    tfidf_max_df: float | None = None,
    learn: bool = True,
    recall_target: float = config.SIM_RECALL_TARGET,
    top_k_log: int = 5,
    collect_trajectory: bool = True,
    log_dir: str | Path | None = None,
    log_prefix: str | None = None,
) -> ReplayResult:
    """基准回放配置封装。

    preset
    ------
    ``"product"``(默认)= 产品语义:priors 无、balance 关、alpha=1.0、
    warmup=5、feature_refit="product"、max 策略。
    ``"asreview"`` = ASReview v1 论文默认语义:priors=(1,1)(``RandomState(seed)``
    无放回,先纳入后排除)、balance=DoubleBalance 开、alpha=3.822、
    warmup=2(先验后每筛 1 条重训)、feature_refit="fixed"(全语料一次
    TF-IDF)。显式 kwargs 覆盖 preset 默认。
    ``"asreview_tuned_nb"`` / ``"asreview_svm"`` = ASReview **v3** 调优研究
    (Ye et al. 2025)的升级配置:在 asreview preset 骨架(priors (1,1) /
    warmup 2 / fixed 特征)上,平衡策略换成 Balanced **样本权重**
    ratio=9.8(v3 语义,非 DoubleBalance 重采样),TF-IDF 开 sublinear_tf 与
    max_df=0.95;分类器分别为 MultinomialNB(alpha=3.822)与 LinearSVC
    (C=0.11, squared_hinge)。注意 ngram_range=(1,2) 无对应参数:本引擎
    分词器原生输出实词 uni+bigram,等价内建。svm preset 仅支持 max 策略。
    """
    if (dataset_csv is None) == (records is None):
        raise ValueError("dataset_csv 与 records 二选一")
    if records is None:
        records, loaded_gold = load_dataset_csv(dataset_csv)
        if gold is None:
            gold = loaded_gold
    if gold is None:
        raise ValueError("传入 records 时必须同时传 gold")

    if preset == "product":
        priors = priors if priors is not None else None
        balance = balance if balance is not None else False
        alpha = alpha if alpha is not None else 1.0
        warmup = warmup if warmup is not None else WARMUP
        feature_refit = feature_refit or "product"
    elif preset == "asreview":
        priors = priors if priors is not None else "asreview"
        balance = balance if balance is not None else True
        alpha = alpha if alpha is not None else 3.822
        warmup = warmup if warmup is not None else 2
        feature_refit = feature_refit or "fixed"
    elif preset in ("asreview_tuned_nb", "asreview_svm"):
        priors = priors if priors is not None else "asreview"
        balance = balance if balance is not None else "balanced"
        balance_ratio = (balance_ratio if balance_ratio is not None
                         else TUNED_AL_DEFAULTS["ratio"])
        alpha = alpha if alpha is not None else 3.822
        warmup = warmup if warmup is not None else 2
        feature_refit = feature_refit or "fixed"
        tfidf_sublinear = tfidf_sublinear if tfidf_sublinear is not None else True
        tfidf_max_df = (tfidf_max_df if tfidf_max_df is not None
                        else TUNED_AL_DEFAULTS["max_df"])
        if preset == "asreview_tuned_nb":
            classifier = classifier or "nb"
        else:
            classifier = classifier or "svm"
            svm_c = svm_c if svm_c is not None else TUNED_AL_DEFAULTS["svm_c"]
    else:
        raise ValueError(f"未知 preset: {preset!r}")

    result = replay_fast(
        records, gold, strategy, seed,
        learn=learn, warmup=warmup, alpha=alpha, recall_target=recall_target,
        priors=priors, balance=balance,
        balance_ratio=(balance_ratio if balance_ratio is not None else 1.0),
        classifier=classifier or "nb",
        svm_c=svm_c if svm_c is not None else TUNED_AL_DEFAULTS["svm_c"],
        tfidf_sublinear=bool(tfidf_sublinear),
        tfidf_max_df=tfidf_max_df,
        feature_refit=feature_refit,
        top_k_log=top_k_log,
        collect_trajectory=collect_trajectory,
    )
    result.config.update({"preset": preset, "strategy": strategy, "seed": seed})
    if log_dir is not None:
        prefix = Path(log_dir) / (log_prefix or f"replay-{preset}-{strategy}-s{seed}")
        paths = result.save_logs(prefix)
        result.config["log_files"] = [str(p) for p in paths]
    return result


_ID_COLS = ("record_id", "zotero_key", "id", "key")
_LABEL_COLS = ("label_included", "included_label", "label", "included_in_review",
               "final_included", "included")
_INCLUDE_VALUES = {"1", "true", "yes", "include", "included"}
_EXCLUDE_VALUES = {"0", "false", "no", "exclude", "excluded"}


def load_dataset_csv(path: str | Path, id_col: str | None = None,
                     label_col: str | None = None) -> tuple[list[FastRecord], dict[str, str]]:
    """加载完整标注数据集(SYNERGY 风格 CSV)。

    列名自动识别(可用 ``id_col``/``label_col`` 显式覆盖):
    - id: record_id / zotero_key / id / key
    - label: label_included / included_label / label / included_in_review /
      final_included / included(1/true/yes/include → include;0/false/no/
      exclude → exclude)
    - 文本: title / abstract / authors / journal(SYNERGY 的 keywords 列刻意
      不用,保持与产品四字段拼接一致)
    """
    path = Path(path)
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        cols = reader.fieldnames or []
        id_c = id_col or next((c for c in _ID_COLS if c in cols), None)
        lab_c = label_col or next((c for c in _LABEL_COLS if c in cols), None)
        if id_c is None or lab_c is None:
            raise ValueError(f"无法识别 id/label 列;实际列: {cols}")
        text_cols = [c for c in ("title", "abstract", "authors", "journal")
                     if c in cols]

        def _norm_label(v) -> str:
            s = str(v).strip().lower()
            if s in _INCLUDE_VALUES:
                return "include"
            if s in _EXCLUDE_VALUES:
                return "exclude"
            raise ValueError(f"无法解析标签值 {v!r}(列 {lab_c})")

        records: list[FastRecord] = []
        gold: dict[str, str] = {}
        for i, row in enumerate(reader):
            key = (row.get(id_c) or f"ROW{i}").strip()
            records.append(FastRecord(
                zotero_key=key,
                **{c: row.get(c) or "" for c in text_cols},
            ))
            gold[key] = _norm_label(row.get(lab_c))
    return records, gold


def make_synthetic_corpus(n_docs: int, seed: int = 0,
                          relevant_ratio: float = 0.06,
                          avg_len: int = 110) -> tuple[list[FastRecord], dict[str, str]]:
    """合成基准语料(种子化;中英混合;含重复/纯停用词/空文档边缘用例)。

    仅用于性能基准与测试,不代表真实题录分布。键格式 ``SYN%06d``,重复文档
    追加在语料尾部(键 ``SYNDUP%04d``,文本与源文档完全相同 ⇒ 同分场景)。
    """
    rng = random.Random(seed)
    en_words = [
        "learning", "teaching", "classroom", "students", "assessment",
        "cooperative", "technology", "education", "intervention", "outcomes",
        "randomized", "trial", "achievement", "motivation", "teachers",
        "schools", "digital", "reading", "comprehension", "vocabulary",
        "instruction", "meta-analysis", "effect", "study", "practice",
    ]
    cn_words = [
        "教育", "教学", "课堂", "学生", "评价", "合作", "技术", "干预",
        "效果", "随机", "试验", "成绩", "动机", "教师", "学校", "阅读",
        "理解", "词汇", "研究", "实践", "智慧", "素养", "课程", "改革",
    ]
    records: list[FastRecord] = []
    gold: dict[str, str] = {}
    n_relevant = max(1, int(n_docs * relevant_ratio))
    relevant_pos = set(rng.sample(range(n_docs), n_relevant))
    for i in range(n_docs):
        include = i in relevant_pos
        words = [rng.choice(en_words) for _ in range(avg_len // 3)]
        words += [rng.choice(cn_words) for _ in range(avg_len // 3)]
        rng.shuffle(words)
        title = " ".join(words[:6])
        abstract = " ".join(words[6:]) + " " + "".join(
            rng.choice(cn_words) for _ in range(20))
        authors = f"Author{rng.randint(1, 999)}; Co{rng.randint(1, 99)}"
        journal = rng.choice(["J Educ A", "J Educ B", "中华教育刊", "Learning C"])
        hint = "cooperative learning 成效" if include else "unrelated buffer matter"
        if i % 997 == 13:  # 边缘:纯停用词文档
            rec = FastRecord(zotero_key=f"SYN{i:06d}", title="the of and")
        elif i % 1001 == 7:  # 边缘:完全空文档
            rec = FastRecord(zotero_key=f"SYN{i:06d}")
        else:
            rec = FastRecord(zotero_key=f"SYN{i:06d}", title=f"{title} {hint}",
                             abstract=abstract, authors=authors, journal=journal)
        records.append(rec)
        gold[rec.zotero_key] = "include" if include else "exclude"
    # 重复文档(同分场景):追加在尾部,文本与某个 exclude 源完全相同。
    n_dup = 0
    for rec in list(records[:50]):
        if n_dup >= 5:
            break
        if gold[rec.zotero_key] == "exclude":
            dup = FastRecord(zotero_key=f"SYNDUP{n_dup:04d}", title=rec.title,
                             abstract=rec.abstract, authors=rec.authors,
                             journal=rec.journal)
            records.append(dup)
            gold[dup.zotero_key] = "exclude"
            n_dup += 1
    return records, gold
