"""docs/methodology 静态守卫：阻止 pytest 收集随库分发的研究原始数据。

本目录按 W7a（2.1.4 r8）从研究仓 ``research/`` 原样分发（sha256 逐文件
核验）三类原始数据：``notes/stopping_rules.md``（S1 停止准则理论推导）、
``results/al_upgrade_verification/``（90 场 AL 升级验证 + 20 场停止规则
回归）、``sim/``（研究模拟引擎与测试，针对研究环境 Python 3.12 venv，
非产品 CI 环境）。

``sim/`` 内的 ``test_*.py`` 是研究仓原始文件（保留原名以保真），其导入
（scipy/sklearn 版本、``import replay`` 的 sys.path 布局）依赖研究 venv，
不适合在产品测试环境收集执行；产品测试的权威范围是 ``build/tests``
（README「开发与贡献」给定的命令 ``python -m pytest build/tests -q``）。
为防止有人在仓库根裸跑 ``python -m pytest`` 时误收研究测试，本 conftest
把 ``sim/`` 从收集中排除——对 ``pytest build/tests`` 完全惰性（该命令不
经过本目录）。

钉子：build/tests/test_w7_recall_certificate_docs.py 断言本文件存在且含
``collect_ignore = ["sim"]``（守卫被删即红）。
"""

from __future__ import annotations

import sys

# 分发目录保持不含 __pycache__（研究源镜像口径；pytest 会缓存本 conftest）。
sys.dont_write_bytecode = True

collect_ignore = ["sim"]
