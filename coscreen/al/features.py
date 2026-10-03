"""特征提取：英/中混合分词与确定性 TF-IDF 向量化（见 SPEC.md §12）。

``tokenize`` 规则（确定性，输出按文本出现顺序）：

- 统一小写；标点与符号（Unicode 类别 ``P*``/``S*``）**直接删除且不切断单词**
  （``"co-teaching"→"coteaching"``、``"students'"→"students"``、全角
  ``"，"`` 删除后两侧汉字并入同一段）；空白/控制字符作为切分边界；
  保留 CJK 汉字（U+4E00–U+9FFF）与字母/数字（Unicode ``L*``/``N*``）。
- 英文：实词 unigram + 相邻实词 bigram（``"term1_term2"``）。内置极小英文
  停用词表 :data:`ENGLISH_STOPWORDS`（46 个常见虚词，刻意保持很小：题录
  文本以实词为主）中的词不输出、也不阻断相邻实词结成 bigram——与 sklearn
  "先删停用词再拼 n-gram" 的行为一致；CJK 段会阻断英文 bigram
  （``"TESOL 教师 narrative"`` 不产生 ``"tesol_narrative"``）。
- 中文：取 CJK 最大连续段；长度 ≥2 的段输出**全部**字符 bigram
  （``"c1c2"``），长度 1 输出单字。
- 空串 / ``None`` → ``[]``。

``TfidfVectorizer``：

- :meth:`~TfidfVectorizer.fit` 构建词表 ``vocab = {term: 序号}``，term 按
  **字典序**排列（输入顺序无关，完全可复现）；
- ``idf = ln((1 + n) / (1 + df)) + 1``（sklearn ``smooth_idf`` 公式）；
- 值 = ``tf × idf``（tf 为文档内原始词频），再按文档 **L2 归一化**；
- :meth:`~TfidfVectorizer.transform` 忽略词表外的 term；空文档（或全停用词
  文档）→ ``{}``；空语料 fit 不报错（词表为空，transform 一律返回 ``{}``）。

升级参数（2026-09-30 AL 模型升级，见
``docs/handoff/2026-09-30-al-model-upgrade-spec.md`` §1）——``ngram_range`` /
``sublinear_tf`` / ``max_df`` / ``cjk_bigram`` 均有默认值，**不传则与升级前
输出逐位一致**（向后兼容）：

- ``ngram_range=(min_n, max_n)``：在 :func:`tokenize` 输出的词元序列上拼接
  相邻词元生成 n-gram（连接符为单个空格，``"a b"``）；``(1, 1)`` 即不生成
  额外 n-gram。ASReview Optuna 推荐值 ``(1, 2)``。
- ``sublinear_tf=True`` 时 tf 用 ``1 + ln(count)``（count > 0）代替原始词频；
- ``max_df``（0 < max_df ≤ 1）：fit 构建词表时，文档频率 >
  ``max_df × n_docs`` 的词不入词表（仅 fit 生效，transform 逻辑不变）；
  ``1.0`` 表示不过滤。ASReview Optuna 推荐值 ``0.95``；
- ``cjk_bigram``（默认 ``True``，我们的优势）：中文按字符 bigram 切分；
  ``False`` 时中文逐字输出单字词元。

运行时零第三方依赖（仅标准库）；sklearn 仅在测试中作基准。
"""

from __future__ import annotations

import math
import unicodedata
from collections import Counter

__all__ = ["ENGLISH_STOPWORDS", "TfidfVectorizer", "tokenize"]


#: 内置极小英文停用词表（46 个常见虚词，全部小写；tokenize 已先行小写）。
#: 刻意不收录学术文本中常有实词义的词（如 may/might 等情态动词仅保留
#: 最常见者），以降低对文献题录的误伤。
ENGLISH_STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "if", "of", "at", "by", "for",
        "with", "about", "into", "to", "from", "in", "on", "is", "are", "was",
        "were", "be", "been", "being", "this", "that", "these", "those", "it",
        "its", "as", "we", "our", "you", "your", "they", "their", "he", "she",
        "his", "her", "not", "no", "can", "will",
    }
)


def _is_cjk(ch: str) -> bool:
    """单个字符是否属于 CJK 统一表意文字区 U+4E00–U+9FFF。"""
    return "\u4e00" <= ch <= "\u9fff"


def _is_word_char(ch: str) -> bool:
    """单个字符是否为保留字符（Unicode 字母 L* 或数字 N*）。"""
    return unicodedata.category(ch)[0] in ("L", "N")


def _raw_segments(text: str) -> list[str]:
    """把文本切成有序的原始段：英文/数字词（已小写）与 CJK 连续段。

    标点/符号（P*/S*）删除且不切词；空白/控制字符等其他字符作为切分边界。
    """
    segments: list[str] = []
    buf: list[str] = []
    buf_is_cjk = False
    for ch in text.lower():
        if _is_cjk(ch):
            if buf and not buf_is_cjk:
                segments.append("".join(buf))
                buf = []
            buf_is_cjk = True
            buf.append(ch)
        elif _is_word_char(ch):
            if buf and buf_is_cjk:
                segments.append("".join(buf))
                buf = []
            buf_is_cjk = False
            buf.append(ch)
        elif unicodedata.category(ch)[0] in ("P", "S"):
            continue  # 标点/符号：删除、不切词（"co-teaching"→"coteaching"）
        else:
            if buf:  # 空白/控制字符等：切分边界
                segments.append("".join(buf))
                buf = []
            buf_is_cjk = False
    if buf:
        segments.append("".join(buf))
    return segments


def tokenize(text: str | None, cjk_bigram: bool = True) -> list[str]:
    """英/中混合分词：英文 unigram+bigram、中文 CJK 字符 bigram。

    规则见模块 docstring。确定性：同一输入永远得到同一输出（顺序即在
    文本中的出现顺序；每词先输出 unigram，再输出与前一实词的 bigram）。

    ``cjk_bigram``（默认 ``True``，与历史行为一致）：``True`` 时长度 ≥2 的
    CJK 段输出全部字符 bigram；``False`` 时逐字输出单字词元。
    """
    if not isinstance(text, str) or not text:
        return []
    tokens: list[str] = []
    prev_english: str | None = None
    for seg in _raw_segments(text):
        if _is_cjk(seg[0]):
            prev_english = None  # CJK 段阻断英文 bigram
            if len(seg) == 1:
                tokens.append(seg)
            elif cjk_bigram:
                for i in range(len(seg) - 1):
                    tokens.append(seg[i : i + 2])
            else:
                tokens.extend(seg)  # 逐字 unigram
        elif seg in ENGLISH_STOPWORDS:
            continue  # 停用词不输出，也不阻断相邻实词结成 bigram
        else:
            tokens.append(seg)
            if prev_english is not None:
                tokens.append(prev_english + "_" + seg)
            prev_english = seg
    return tokens


class TfidfVectorizer:
    """确定性 TF-IDF 向量化器（纯 Python，接口见 SPEC.md §12）。

    升级版（2026-09-30 AL 模型升级，见 handoff 规格书 §1）：新增参数均有
    默认值，不传则与升级前行为**逐位一致**——保证向后兼容。ASReview Optuna
    推荐值：``ngram_range=(1, 2), sublinear_tf=True, max_df=0.95``。

    参数：
        ngram_range: ``(min_n, max_n)``。在 :func:`tokenize` 输出的词元序列
            上由 :meth:`_ngrams` 拼接相邻词元生成 n-gram（连接符为空格）；
            ``(1, 1)`` 不生成额外 n-gram。
        sublinear_tf: ``True`` 时 tf 用 ``1 + ln(count)``（count > 0）代替
            原始词频 count。
        max_df: 构建词表时，文档频率 > ``max_df × n_docs`` 的词不入词表
            （仅在 fit 生效；transform 逻辑不变）。``1.0`` 表示不过滤。
        cjk_bigram: ``True``（默认，我们的优势）时中文按字符 bigram 切分；
            ``False`` 时中文逐字输出单字词元。

    属性：
        vocab: ``{term: 序号}``，term 按字典序排列；fit 前为 ``{}``。
        idf:   ``{term: idf 值}``，键集合与 ``vocab`` 一致。
    """

    def __init__(
        self,
        ngram_range: tuple[int, int] = (1, 1),
        sublinear_tf: bool = False,
        max_df: float = 1.0,
        cjk_bigram: bool = True,
    ) -> None:
        if (
            not isinstance(ngram_range, (tuple, list))
            or len(ngram_range) != 2
            or not all(isinstance(v, int) and not isinstance(v, bool) for v in ngram_range)
            or ngram_range[0] < 1
            or ngram_range[1] < ngram_range[0]
        ):
            raise ValueError(
                f"ngram_range 必须是 (min_n, max_n) 且 1 <= min_n <= max_n，"
                f"收到 {ngram_range!r}"
            )
        if not max_df > 0.0:
            raise ValueError(f"max_df 必须大于 0（比例），收到 {max_df!r}")
        self.ngram_range: tuple[int, int] = (ngram_range[0], ngram_range[1])
        self.sublinear_tf: bool = bool(sublinear_tf)
        self.max_df: float = float(max_df)
        self.cjk_bigram: bool = bool(cjk_bigram)
        self.vocab: dict[str, int] = {}
        self.idf: dict[str, float] = {}

    def _ngrams(self, tokens: list[str]) -> list[str]:
        """生成 n-gram（ngram_range=(min_n, max_n)）；(1, 1) 时原样返回。

        n=1 保留原词元；n≥2 用空格拼接相邻词元（``tokens[i:i+n]``）。
        """
        result: list[str] = []
        for n in range(self.ngram_range[0], self.ngram_range[1] + 1):
            if n == 1:
                result.extend(tokens)
            else:
                result.extend(
                    " ".join(tokens[i : i + n])
                    for i in range(len(tokens) - n + 1)
                )
        return result

    def _tokenize(self, text: str | None) -> list[str]:
        """按 ``cjk_bigram`` 设置分词（其余规则同 :func:`tokenize`）。"""
        return tokenize(text, cjk_bigram=self.cjk_bigram)

    def fit(self, texts: list[str]) -> None:
        """在语料上构建词表与 idf（词表按字典序，可复现）；空语料不报错。

        ``max_df`` 过滤仅在此处生效：文档频率 > ``max_df × n_docs`` 的词
        不入词表（也不进 idf，transform 自然忽略之）。
        """
        n_docs = len(texts)
        df: Counter[str] = Counter()
        for text in texts:
            for term in set(self._ngrams(self._tokenize(text))):
                df[term] += 1
        max_count = self.max_df * n_docs
        df = Counter(
            {term: count for term, count in df.items() if count <= max_count}
        )
        self.vocab = {term: idx for idx, term in enumerate(sorted(df))}
        self.idf = {
            term: math.log((1 + n_docs) / (1 + count)) + 1.0
            for term, count in df.items()
        }

    def transform(self, texts: list[str]) -> list[dict[str, float]]:
        """把文本转为稀疏 TF-IDF 向量（L2 归一化）；词表外 term 忽略。

        ``sublinear_tf=True`` 时 tf = ``1 + ln(count)``（count > 0）。
        """
        vectors: list[dict[str, float]] = []
        for text in texts:
            tf: dict[str, float] = {}
            for term in self._ngrams(self._tokenize(text)):
                if term in self.idf:
                    tf[term] = tf.get(term, 0.0) + 1.0
            weighted = {
                term: (1.0 + math.log(count) if self.sublinear_tf else count)
                * self.idf[term]
                for term, count in tf.items()
            }
            norm = math.sqrt(sum(value * value for value in weighted.values()))
            if norm > 0.0:
                weighted = {term: value / norm for term, value in weighted.items()}
            vectors.append(weighted)
        return vectors

    def fit_transform(self, texts: list[str]) -> list[dict[str, float]]:
        """等价于先 :meth:`fit` 再 :meth:`transform`。"""
        self.fit(texts)
        return self.transform(texts)
