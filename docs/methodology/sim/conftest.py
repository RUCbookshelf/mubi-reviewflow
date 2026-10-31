"""research/sim 测试的独立 conftest:把项目根与本目录加入 sys.path。

`./.venv/bin/python -m pytest research/sim/` 从仓库根运行时,产品包
``coscreen`` 需要仓库根在 sys.path;``import replay`` 需要本目录在 sys.path。
产品 tests/conftest.py 不在收集路径上,不会干扰本目录测试。
"""

from __future__ import annotations

import sys
from pathlib import Path

SIM_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SIM_DIR.parents[1]

for _p in (str(PROJECT_ROOT), str(SIM_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
