"""主动学习排序子包（借鉴 ASReview）。

依赖边界（r9 b07·F1-117：旧版包级说明把整个子包描述成"纯 Python 实现、
除测试外不引入第三方库"——该描述对 :mod:`coscreen.al.balancer` 与
``svm``/``cjk`` 预设不成立）：

- **纯标准库载体**：:mod:`coscreen.al.features`、:mod:`coscreen.al.nb`、
  :mod:`coscreen.al.strategies`、:mod:`coscreen.al.simulation`、
  :mod:`coscreen.al.profiles`（纯数据）、:mod:`coscreen.al.stop`、
  :mod:`coscreen.al.stop_registry`（纯数据）、
  :mod:`coscreen.al.stop_certificate`——import 与运行都只用标准库，
  sklearn 仅在这些模块的测试里作对照基准；
- **需要 numpy**：:mod:`coscreen.al.balancer`（模块顶层 ``import numpy``，
  无守卫）；
- **需要 sklearn**：:mod:`coscreen.al.svm`（构造函数内
  ``from sklearn.svm import LinearSVC``，延迟导入）。

因此 ``tuned`` 预设（balanced 权重）需要 numpy，``svm``/``cjk`` 预设
还需要 scikit-learn；三者都是生产可达的（``PUT
/api/tasks/{id}/al/profile`` 写入、``GET /al/rank`` 使用）。官方发行包
把 numpy/scipy/scikit-learn 列为运行时依赖（``build/requirements-app.txt``），
按「零第三方依赖」裁剪依赖的自定义部署会在选用上述预设时 ImportError。

规格依据的权威出处（r9 b07·F1-118：仓库内不存在名为 SPEC 的规格文件、
也不存在功能清单与 ``docs/handoff/2026-09-30-al-model-upgrade-spec.md``，
此前对这些文件的引用均为悬空锚点）：参数口径、行号表与验证报告的
可复核副本在 ``docs/methodology/sim/README.md``（§1.1–§1.5）与
``docs/methodology/results/al_upgrade_verification/REPORT.md``。

本 ``__init__`` 刻意不做任何子模块导入（避免包加载即引入实现细节，
也便于各子模块独立演进）；调用方按需显式导入，例如::

    from coscreen.al.features import tokenize, TfidfVectorizer
    from coscreen.al.nb import MultinomialNB
"""
