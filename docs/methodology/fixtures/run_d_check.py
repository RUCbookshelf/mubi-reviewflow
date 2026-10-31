"""D1–D4 理论自检脚本的随库运行器（W7 r3）。

背景：``docs/methodology/notes/check_d4_combined.py`` 是研究树原样副本（sha256
与源一致），其 :19-20 行硬编码 ``sys.path.insert(0, "research/sim")`` /
``"research/notes"``（研究树布局）。分发到 ``docs/methodology/notes/`` 后该路径
不存在，裸跑 D4 报 ``ModuleNotFoundError: No module named 'stopping_rules'``；
D1/D2/D3 无路径依赖，裸跑即可。为**不改动逐字节一致的分发原文件**，本运行器
把正确目录（``docs/methodology/sim`` + ``docs/methodology/notes``）插到
``sys.path`` 前部，再以 ``runpy`` 执行目标脚本——等价于研究报告里的
``PYTHONPATH="docs/methodology/sim;docs/methodology/notes"`` 前置。

用法（在仓库根）：``python docs/methodology/fixtures/run_d_check.py d4``
（d1..d4 任一；argv 缺省跑全部四个）。退出码：全部 RESULT/ALL PASS 为 0。

依赖：numpy + scipy（产品运行环境已含），无网络、无写文件。
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

# 子进程/导入均不写字节码：分发目录须保持不含 __pycache__（研究源镜像口径）。
sys.dont_write_bytecode = True
import os  # noqa: E402

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

NOTES = Path(__file__).resolve().parents[1] / "notes"
SIM = Path(__file__).resolve().parents[1] / "sim"

SCRIPTS = {
    "d1": "check_d1_streak.py",
    "d2": "check_d2_subsample.py",
    "d3": "check_d3_estimators.py",
    "d4": "check_d4_combined.py",
}


def main(argv: list[str]) -> int:
    # 与研究报告的 PYTHONPATH 前置等价：sim 与 notes 都要在导入路径上。
    for path in (str(SIM), str(NOTES)):
        if path not in sys.path:
            sys.path.insert(0, path)
    names = [a.lower() for a in argv[1:]] or list(SCRIPTS)
    worst = 0
    for name in names:
        script = SCRIPTS.get(name)
        if script is None:
            print(f"未知脚本：{name}（可选 {', '.join(SCRIPTS)}）", file=sys.stderr)
            return 2
        target = NOTES / script
        print(f"\n===== {script} =====")
        try:
            runpy.run_path(str(target), run_name="__main__")
        except SystemExit as exc:  # 脚本自带退出码时如实传递
            worst = max(worst, int(exc.code or 0))
    return worst


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
