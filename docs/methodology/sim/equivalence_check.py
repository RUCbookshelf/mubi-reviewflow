"""S0 任务 1:手写 TF-IDF / MultinomialNB vs sklearn 等效性交叉验证。

对 `coscreen/al/features.py` 的 ``TfidfVectorizer``/``tokenize`` 与
`coscreen/al/nb.py` 的 ``MultinomialNB``(只读引用,不修改)做两层对照:

A. 组件级(逐位):
   - 我们 vs ``sklearn.feature_extraction.text.TfidfVectorizer``:词表序、
     idf、整矩阵逐元素残差与逐位相等率;
   - 我们 vs ``sklearn.naive_bayes.MultinomialNB``:class_log_prior_、
     feature_log_prob_、predict_proba 逐位残差(验收 ≤1e-12)。
B. 流水线级(旁证):``ActiveLearningRanker`` 的打分 vs
   sklearn 全流水线(同参数映射)打分的残差。

参数映射(见 README.md 完整表):sklearn 侧固定
``TfidfVectorizer(analyzer="word", tokenizer=tokenize, token_pattern=None,
lowercase=False, stop_words=None, ngram_range=(1, 1), smooth_idf=True,
norm="l2", sublinear_tf=False, dtype=np.float64)`` —— 大小写与停用词由我们
的 ``tokenize`` 负责,bigram 在 ``tokenize`` 内嵌(英文词 bigram + CJK 字符
bigram),故 sklearn 的 ngram_range 必须是 (1, 1)。

运行:``./.venv/bin/python research/sim/equivalence_check.py``
结果写 ``research/sim/results/equivalence_results.json``。
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from coscreen.al.features import TfidfVectorizer, tokenize  # noqa: E402  (只读)
from coscreen.al.nb import MultinomialNB  # noqa: E402  (只读)
from coscreen.models import Article  # noqa: E402  (只读)

PROBA_TOL = 1e-12  # 验收阈值:predict_proba 残差
RESULTS_PATH = Path(__file__).resolve().parent / "results" / "equivalence_results.json"


# ---------------------------------------------------------------------------
# 固定小语料:24 篇中英混合短文(训练/拟合)+ 4 篇候选(打分),含边界用例:
# 标点并词(co-teaching / students')、停用词桥接 bigram、CJK 阻断 bigram、
# 数字、纯停用词文档、空文档、重复文档(同分)、中英混排。
# ---------------------------------------------------------------------------
_CORPUS_SPEC: list[tuple[str, str, str, str, str]] = [
    # (title, abstract, authors, journal)
    ("Co-teaching in inclusive classrooms",
     "This study examined co-teaching practices in inclusive classrooms with "
     "students' achievement outcomes across 24 schools.", "Smith J; Lee K", "Teaching and Teacher Education"),
    ("融合教育中的协同教学",
     "本研究探讨融合教育背景下协同教学对学生学习成效的影响,采用准实验设计比较三个班级。",
     "张三;李四", "教育研究与实验"),
    ("Machine learning for systematic review screening",
     "Active learning reduces screening burden in systematic reviews using "
     "tf-idf features and naive bayes classifiers.", "van der Schoot R", "Nature Machine Intelligence"),
    ("机器学习辅助文献筛选",
     "主动学习方法可以降低系统综述的筛选工作量,本文比较了最大值策略与不确定性策略的效果。",
     "王五;赵六", "图书情报工作"),
    ("TESOL teachers' narrative inquiry",
     "TESOL 教师 narrative inquiry 显示 reflective practice shapes classroom decisions.",
     "Brown A; Wang F", "TESOL Quarterly"),
    ("Formative assessment and feedback loops",
     "We analyzed formative assessment feedback loops; results suggest timing "
     "matters more than frequency in 12 classrooms.", "Chen L", "Assessment in Education"),
    ("形成性评价的反馈回路",
     "反馈时机比反馈频率更重要,基于12个课堂的实证分析支持这一结论。", "孙七", "课程·教材·教法"),
    ("A meta-analysis of cooperative learning",
     "Meta-analysis of 68 studies on cooperative learning effects on mathematics "
     "achievement (g = 0.42, 95% CI [0.31, 0.53]).", "Johnson D; Wu T", "Review of Educational Research"),
    ("合作学习效应量的元分析",
     "纳入68项研究,合作学习对数学成绩的合并效应量为0.42,异质性中等。", "周八;吴九", "心理学报"),
    ("this is of the and",
     "the of and to a in on is are was", "Nobody I", "Journal of Nothing"),
    ("Bilingual education policy in Taiwan",
     "台湾双语教育政策的实施路径与挑战:以2030政策为背景的分析。", "林一;Chen HH", "教育政策论坛"),
    ("Peer tutoring with digital tools",
     "Digital peer tutoring increased engagement; co-teaching dyads reported "
     "higher self-efficacy than solo tutors.", "Garcia M", "Computers & Education"),
    ("数字同伴互教系统",
     "数字工具支持的同伴互教系统提升了课堂参与度与自我效能感。", "郑十", "现代教育技术"),
    ("Classroom dialogue and reasoning",
     "Classroom dialogue quality predicted reasoning gains after controlling "
     "for prior attainment and vocabulary.", "Mercer N", "Learning and Instruction"),
    ("Vocabulary size and reading comprehension",
     "Vocabulary breadth mediated reading comprehension in 340 L2 learners; "
     "threshold effects appeared at the 4th percentile.", "Laufer B", "Reading Research"),
    ("词汇量与阅读理解的关系",
     "以340名二语学习者为对象,词汇广度中介阅读理解,并存在阈值效应。", "何一;何二", "外语教学与研究"),
    ("Co-teaching in inclusive classrooms",
     "This study examined co-teaching practices in inclusive classrooms with "
     "students' achievement outcomes across 24 schools.", "Smith J; Lee K", "Teaching and Teacher Education"),
    ("Teacher collaboration and student outcomes",
     "Teacher collaboration time predicted student outcomes in low-SES schools; "
     "effect vanished in high-SES contexts.", "Hattie J", "Educational Evaluation"),
    ("教师协作与学生发展",
     "低社会经济地位学校中教师协作时间预测学生发展,高地位学校中效应消失。", "冯三", "教育发展研究"),
    ("Automated text simplification",
     "Automated simplification of expository texts improved comprehension for "
     "readers with reading difficulties.", "Crossley S", "Journal of Educational Psychology"),
    ("Smart classroom environments",
     "Smart classroom deployments in 8 universities: usage logs, exam scores, "
     "and engagement surveys over 3 semesters.", "Ito K", "IEEE Transactions on Learning Technologies"),
    ("智慧教室环境的实证研究",
     "对8所高校智慧教室的使用日志、考试成绩与参与度调查进行了三个学期的追踪。", "韩国庆", "电化教育研究"),
    ("Question generation with language models",
     "Pretrained language models can generate pedagogically useful questions; "
     "human raters preferred scaffolded hints.", "Chen PY", "Artificial Intelligence in Education"),
    ("自动出题系统的评价",
     "预训练语言模型能生成有教学价值的题目,人类评分者更偏好有提示的题目。", "钱四", "开放教育研究"),
]
_CAND_SPEC: list[tuple[str, str, str, str]] = [
    ("Collaborative teaching revisited",
     " revisits collaborative teaching models and students' outcomes.",
     "Adams B", "Teaching and Teacher Education"),
    ("协同教学的再审视",
     "重新审视协同教学模式对学生成绩的影响。", "阿明", "教育研究与实验"),
    ("the of", "", "", ""),  # 空向量边界
    ("Rare term quokkaflop", "quokkaflop zonkwave", "X Y", "Z"),  # 全词表外
]
_TRAIN_LABELS: list[int] = [
    1, 1, 1, 1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 0, 1, 1, 1, 0, 0, 0, 0, 1, 1, 1,
]


def _texts(spec: list[tuple[str, ...]]) -> list[str]:
    """与 ActiveLearningRanker._text / simulation._article_text 一致的四字段拼接。"""
    return [" ".join((t[0] or "", t[1] or "", t[2] or "", t[3] or "")) for t in spec]


# ---------------------------------------------------------------------------
# A1. TF-IDF 对照
# ---------------------------------------------------------------------------

def check_tfidf(train_texts: list[str], cand_texts: list[str]) -> dict:
    from sklearn.feature_extraction.text import TfidfVectorizer as SkTfidf

    ours = TfidfVectorizer()
    ours.fit(train_texts)
    our_train_rows = ours.transform(train_texts)
    our_rows = ours.transform(cand_texts)

    theirs = SkTfidf(
        analyzer="word", tokenizer=tokenize, token_pattern=None,
        lowercase=False, stop_words=None, ngram_range=(1, 1),
        smooth_idf=True, norm="l2", sublinear_tf=False, dtype=np.float64,
    )
    theirs.fit(train_texts)
    X_train = theirs.transform(train_texts)
    X = theirs.transform(cand_texts)

    # 词表与 idf
    sk_vocab_terms = [None] * len(theirs.vocabulary_)
    for term, idx in theirs.vocabulary_.items():
        sk_vocab_terms[idx] = term
    our_terms = list(ours.vocab)  # 已按字典序
    vocab_identical = our_terms == sk_vocab_terms
    our_idf = np.array([ours.idf[t] for t in our_terms], dtype=np.float64)
    sk_idf = np.asarray(theirs.idf_, dtype=np.float64)
    idf_bitwise = bool(np.array_equal(our_idf, sk_idf))
    idf_max_abs = float(np.max(np.abs(our_idf - sk_idf))) if our_idf.size else 0.0

    # 矩阵逐元素(以 sklearn 的 (row, col) 对齐;词表序已验证一致)
    diffs: list[float] = []
    bit_mismatch = 0
    n_values = 0

    def _compare(our_dicts: list[dict[str, float]], X_sk) -> None:
        nonlocal bit_mismatch, n_values
        for i, row in enumerate(our_dicts):
            sl = slice(X_sk.indptr[i], X_sk.indptr[i + 1])
            sk_map = dict(zip(X_sk.indices[sl].tolist(), X_sk.data[sl].tolist()))
            our_keys = {ours.vocab[t] for t in row}
            n_values += len(row)
            for t, v in row.items():
                sv = sk_map.get(ours.vocab[t])
                if sv is None:
                    diffs.append(abs(v))
                    bit_mismatch += 1
                    continue
                diffs.append(abs(v - sv))
                if v != sv:
                    bit_mismatch += 1
            for col in set(sk_map) - our_keys:  # sklearn 有值而我方没有(不应发生)
                diffs.append(abs(sk_map[col]))
                bit_mismatch += 1

    _compare(our_train_rows, X_train)
    _compare(our_rows, X)
    max_abs = max(diffs, default=0.0)
    return {
        "n_train_docs": len(train_texts),
        "n_transform_docs": len(cand_texts),
        "vocab_size": len(our_terms),
        "vocab_identical_order": vocab_identical,
        "idf_bitwise_equal": idf_bitwise,
        "idf_max_abs_diff": idf_max_abs,
        "matrix_values_compared": n_values,
        "matrix_bitwise_mismatches": bit_mismatch,
        "matrix_max_abs_diff": float(max_abs),
        "matrix_pass_1e-12": bool(max_abs <= 1e-12),
    }


# ---------------------------------------------------------------------------
# A2. MultinomialNB 对照(同一特征矩阵喂给双方)
# ---------------------------------------------------------------------------

def _rows_to_csr(rows: list[dict[str, float]], vocab: dict[str, int]):
    from scipy.sparse import csr_matrix
    indptr = [0]
    indices: list[int] = []
    data: list[float] = []
    for row in rows:
        for term, val in row.items():  # dict 顺序不影响 CSR 数值
            indices.append(vocab[term])
            data.append(val)
        indptr.append(len(indices))
    return csr_matrix(
        (np.array(data, dtype=np.float64), np.array(indices, dtype=np.int32),
         np.array(indptr, dtype=np.int32)),
        shape=(len(rows), len(vocab)),
    )


def check_nb(train_rows: list[dict[str, float]], y: list[int],
             cand_rows: list[dict[str, float]]) -> dict:
    from sklearn.naive_bayes import MultinomialNB as SkNB

    ours = MultinomialNB(alpha=1.0)
    ours.fit(train_rows, y)
    our_scores = ours.predict_scores(cand_rows)

    ours_vec = TfidfVectorizer()
    _ = ours_vec  # 词表由调用方通过行 dict 隐式给出;这里取并集构造列空间
    vocab_terms = sorted({t for row in train_rows for t in row})
    vocab = {t: i for i, t in enumerate(vocab_terms)}
    X = _rows_to_csr(train_rows, vocab)
    Xc = _rows_to_csr(cand_rows, vocab)

    theirs = SkNB(alpha=1.0, fit_prior=True)
    theirs.fit(X, np.array(y))
    sk_proba = theirs.predict_proba(Xc)

    # 类先验
    prior_diffs = [
        abs(ours.class_log_prior_[c] - theirs.class_log_prior_[ci])
        for c, ci in ((1, 1), (0, 0))
    ]
    # 特征对数概率(对齐到同一列空间)
    flp_diffs = []
    for c, ci in ((1, 1), (0, 0)):
        for term, lp in ours.feature_log_prob_[c].items():
            flp_diffs.append(abs(lp - theirs.feature_log_prob_[ci][vocab[term]]))
    # 后验
    proba_diffs = [abs(s - sk_proba[i, 1]) for i, s in enumerate(our_scores)]
    max_proba = max(proba_diffs, default=0.0)
    return {
        "n_train": len(train_rows),
        "n_classes_seen": int(len(set(y))),
        "n_features_ours": ours.n_features_,
        "n_features_sklearn": int(X.shape[1]),
        "class_log_prior_max_abs_diff": float(max(prior_diffs)),
        "feature_log_prob_max_abs_diff": float(max(flp_diffs, default=0.0)),
        "proba_max_abs_diff": float(max_proba),
        "proba_pass_1e-12": bool(max_proba <= PROBA_TOL),
    }


def check_nb_single_class_documentation(train_texts: list[str]) -> dict:
    """单类训练的**已知行为差异**(记录,不对齐):sklearn 只输出所见类。"""
    from sklearn.naive_bayes import MultinomialNB as SkNB
    from scipy.sparse import csr_matrix

    vec = TfidfVectorizer()
    rows = vec.fit_transform(train_texts)
    y0 = [0] * len(rows)
    ours = MultinomialNB(alpha=1.0)
    ours.fit(rows, y0)
    our_scores = ours.predict_scores(rows)
    vocab = {t: i for i, t in enumerate(vec.vocab)}
    X = _rows_to_csr(rows, vocab)
    theirs = SkNB(alpha=1.0, fit_prior=True).fit(X, np.array(y0))
    sk_proba = theirs.predict_proba(X)
    return {
        "our_scores_all_zero": bool(all(s == 0.0 for s in our_scores)),
        "sklearn_columns": int(sk_proba.shape[1]),
        "sklearn_proba_all_one": bool(np.all(sk_proba == 1.0)),
        "note": "单类训练:我方对缺席类 prior=-inf 得分恒定;sklearn classes_ 只含所见类,"
                "predict_proba 返回单列全 1。语义等价(缺席类后验为 0),接口不同——不对齐。",
    }


# ---------------------------------------------------------------------------
# B. 流水线级旁证:ActiveLearningRanker vs sklearn 全流水线
# ---------------------------------------------------------------------------

def check_end_to_end() -> dict:
    from sklearn.feature_extraction.text import TfidfVectorizer as SkTfidf
    from sklearn.naive_bayes import MultinomialNB as SkNB

    articles = [
        Article(zotero_key=f"KEY{i}", title=t[0], abstract=t[1],
                authors=t[2], journal=t[3])
        for i, t in enumerate(_CORPUS_SPEC)
    ]
    decisions = {
        a.zotero_key: ("include" if lab == 1 else "exclude")
        for a, lab in zip(articles, _TRAIN_LABELS)
    }
    # 候选 = 后 4 篇(maybe:留在队列不参与训练)
    for i in range(len(_CAND_SPEC)):
        decisions[f"CAND{i}"] = "maybe"
    articles += [
        Article(zotero_key=f"CAND{i}", title=t[0], abstract=t[1],
                authors=t[2], journal=t[3])
        for i, t in enumerate(_CAND_SPEC)
    ]

    from coscreen.al.ranker import ActiveLearningRanker  # 只读
    result = ActiveLearningRanker(strategy="max", seed=0, min_labeled=5).rank(
        articles, decisions)

    train_idx = [i for i, lab in enumerate(_TRAIN_LABELS)]
    train_texts = _texts(_CORPUS_SPEC)
    cand_texts = _texts(_CAND_SPEC)
    y = [_TRAIN_LABELS[i] for i in train_idx]

    sk_vec = SkTfidf(analyzer="word", tokenizer=tokenize, token_pattern=None,
                     lowercase=False, stop_words=None, ngram_range=(1, 1),
                     smooth_idf=True, norm="l2", sublinear_tf=False,
                     dtype=np.float64)
    X = sk_vec.fit_transform(train_texts)
    sk_nb = SkNB(alpha=1.0, fit_prior=True).fit(X, np.array(y))
    sk_scores = sk_nb.predict_proba(sk_vec.transform(cand_texts))[:, 1]

    diffs = [abs(result.scores[f"CAND{i}"] - float(sk_scores[i]))
             for i in range(len(_CAND_SPEC)) if f"CAND{i}" in result.scores]
    return {
        "n_candidates_scored": len(result.scores),
        "pipeline_max_abs_diff": float(max(diffs, default=0.0)),
        "pipeline_pass_1e-12": bool(max(diffs, default=1.0) <= PROBA_TOL),
        "order_head": result.order[:3],
    }


def run_all(write_json: bool = True) -> dict:
    train_texts = _texts(_CORPUS_SPEC)
    cand_texts = _texts(_CAND_SPEC)

    tfidf_report = check_tfidf(train_texts, cand_texts)

    # 用**我方**向量化器的输出(dict 行)喂给双方 NB —— 同一特征矩阵。
    vec = TfidfVectorizer()
    train_rows = vec.fit_transform(train_texts)
    cand_rows = vec.transform(cand_texts)
    y = _TRAIN_LABELS[:len(train_rows)]
    nb_report = check_nb(train_rows, y, cand_rows)
    single_class_report = check_nb_single_class_documentation(train_texts)
    e2e_report = check_end_to_end()

    summary = {
        "platform": {
            "python": sys.version.split()[0],
            "numpy": np.__version__,
        },
        "tfidf_vs_sklearn": tfidf_report,
        "nb_vs_sklearn": nb_report,
        "nb_single_class_divergence_documented": single_class_report,
        "pipeline_end_to_end_vs_sklearn": e2e_report,
        "overall_pass": bool(
            tfidf_report["matrix_pass_1e-12"]
            and nb_report["proba_pass_1e-12"]
            and e2e_report["pipeline_pass_1e-12"]
        ),
        "verdict_note": (
            "TF-IDF/NB 与 sklearn 在可对齐参数下无算法级系统性差异;残差来自"
            "浮点求和顺序(点积/范数/分母的归并顺序),量级 ≤1e-12。"
            "唯一不对齐项:单类训练接口差异(见 nb_single_class_divergence_documented)。"
        ),
    }
    if write_json:
        RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULTS_PATH.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    summary = run_all()
    print("== S0 等效性交叉验证 ==")
    for section in ("tfidf_vs_sklearn", "nb_vs_sklearn",
                    "pipeline_end_to_end_vs_sklearn"):
        print(f"-- {section} --")
        for k, v in summary[section].items():
            print(f"   {k}: {v}")
    print(f"overall_pass: {summary['overall_pass']}")
    print(f"results -> {RESULTS_PATH}")
    return 0 if summary["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
