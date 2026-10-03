"""主动学习排序子包（借鉴 ASReview；见 SPEC.md §12）。

纯 Python 实现，运行时零第三方依赖（sklearn 仅用于测试基准）：

- :mod:`coscreen.al.features` —— 英/中混合分词与确定性 TF-IDF 向量化；
- :mod:`coscreen.al.nb` —— 多项式朴素贝叶斯（与 sklearn MultinomialNB 数值等价）。

本 ``__init__`` 刻意不做任何子模块导入（避免包加载即引入实现细节，
也便于各子模块独立演进）；调用方按需显式导入，例如::

    from coscreen.al.features import tokenize, TfidfVectorizer
    from coscreen.al.nb import MultinomialNB
"""
