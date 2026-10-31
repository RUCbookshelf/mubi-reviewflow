"""生成 §8(b) 产品 simulate 复现夹具（W7 r2，确定性、离线、零网络）。

用随库分发的研究引擎 ``docs/methodology/sim/replay.py::make_synthetic_corpus``
（种子 20261030）确定性生成 200 条中英混合合成题录与黄金标签，写出：

- ``corpus.csv``——产品 CSV 导入格式（key/title/creator/abstract note 列，
  与 coscreen/parsers/csv_parser.py 的列别名逐字对应）；
- ``gold.csv``——黄金标准（zotero_key, decision 两列，simulate 输入）。

随后按报告 §8(b) 的两条产品命令现跑（db 建在系统临时目录，不进仓库）：

    python -m coscreen import  --db <tmp>/fixture.sqlite --file corpus.csv
    python -m coscreen simulate --db <tmp>/fixture.sqlite --gold gold.csv \
        --strategy mixed --seed 42 --out <tmp>/result.json

合成语料仅用于可复现的端到端演练，**不代表真实题录分布，也不是公开基准**
（SYNERGY 公开语料不随库分发；报告 §7/§8 如实说明）。本脚本仅读写本目录
与调用方指定的输出路径，import 时零网络、零副作用。
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

# 导入研究引擎前关闭字节码写入：不在分发的 sim/ 目录留 __pycache__（W7a
# 分发口径为不含 __pycache__ 的原样镜像）。
sys.dont_write_bytecode = True

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))                                  # coscreen
sys.path.insert(0, str(REPO / "docs" / "methodology" / "sim"))  # replay

from replay import make_synthetic_corpus  # noqa: E402

SEED = 20261030
N_DOCS = 200

OUT = Path(__file__).resolve().parent


def main() -> int:
    records, gold = make_synthetic_corpus(N_DOCS, seed=SEED)
    with (OUT / "corpus.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["key", "title", "creator", "abstract note", "publication title"])
        for rec in records:
            writer.writerow(
                [rec.zotero_key, rec.title, rec.authors, rec.abstract, rec.journal]
            )
    with (OUT / "gold.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["zotero_key", "decision"])
        for key, decision in sorted(gold.items()):
            writer.writerow([key, decision])
    n_rel = sum(1 for value in gold.values() if value == "include")
    print(
        f"corpus={len(records)} gold={len(gold)} relevant={n_rel} "
        f"(seed={SEED}, docs={N_DOCS})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
