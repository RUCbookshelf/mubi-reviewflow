"""把随库分发的公开 SYNERGY 语料转成产品 CLI 可用的 import/gold 文件（W7 r3）。

数据源：``docs/methodology/data/synergy/Nelson_2002.csv``——**公开数据集** SYNERGY
v1（``github.com/asreview/synergy-dataset`` releases/v1，整包 CC0 1.0；
等价持久存档 DataverseNL V1, DOI 10.34894/HE6NAQ）。本文件为研究树
``research/data/synergy/Nelson_2002.csv`` 的原样副本，sha256
``f451f025a222579869fe049379645543a41506135992cfa711d7054e08f1a40b``，与同目录
``summary.csv`` 的逐集 sha256 一致（366 条 / 80 条纳入 / 患病率 21.86%）。
许可与来源全文见 ``docs/methodology/data/provenance.md`` §1/§3。

源列：``record_id,title,abstract,included``（included 为 0/1）。
转出（写在本目录）：

- ``nelson_corpus.csv``——产品 CSV 导入格式（key/title/creator/abstract note/
  publication title；列别名见 ``coscreen/parsers/csv_parser.py``）；
- ``nelson_gold.csv``——黄金标准（zotero_key,decision；1→include，0→exclude）。

**键规范化（关键）**：SYNERGY 的 ``record_id`` 是 URL 形
（``https://openalex.org/W2037688346``），而产品键契约
（``custom_backend/main.py:166`` 的 ``_KEY_RE``）禁止 ``/``、``\`` 且限长 300，
直接用会被后端降级为自动生成键（``auto-…``），导致黄金标准对不上。故取
URL 末段 OpenAlex Work ID（``W2037688346``）作为 key，双文件同一映射，
逐行与原 ``record_id`` 一一对应（末段全局唯一，summary.csv 已核 ``n_duplicate_record_ids=0``）。
映射规则：``record_id.rsplit("/", 1)[-1]``。

确定性：按源文件行序、无随机、无网络；重复执行逐字节一致。

配套的**夹具**（合成语料，非公开数据）在 ``docs/methodology/fixtures/``，
用于端到端冒烟；本脚本产出的是真实公开基准的现跑复现输入（报告 §8(b2)）。
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "data" / "synergy" / "Nelson_2002.csv"
EXPECTED_SHA256 = "f451f025a222579869fe049379645543a41506135992cfa711d7054e08f1a40b"


def safe_key(record_id: str) -> str:
    """OpenAlex Work ID（URL 末段）——满足产品键契约（禁 / \\、≤300 字符）。"""
    return record_id.rstrip("/").rsplit("/", 1)[-1]


def main() -> int:
    import hashlib

    raw = SOURCE.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_SHA256:
        print(
            f"语料校验失败：sha256={digest} 与 summary.csv 记录不符，拒绝转换。",
            file=sys.stderr,
        )
        return 2

    with SOURCE.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    n_included = 0
    with (HERE / "nelson_corpus.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["key", "title", "creator", "abstract note", "publication title"])
        for row in rows:
            writer.writerow(
                [safe_key(row["record_id"]), row["title"], "", row["abstract"], ""]
            )
            if row["included"] in ("1", "true", "True"):
                n_included += 1
    with (HERE / "nelson_gold.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["zotero_key", "decision"])
        for row in rows:
            decision = "include" if row["included"] in ("1", "true", "True") else "exclude"
            writer.writerow([safe_key(row["record_id"]), decision])
    print(f"nelson_corpus={len(rows)} nelson_gold={len(rows)} relevant={n_included} sha256=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
