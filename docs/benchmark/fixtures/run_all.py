"""Run every benchmark fixture in order. Idempotent: results/ JSON files are overwritten.

Usage (Windows Git Bash):
  "C:/Users/16073/.codex/preview-data/reviewflow-2-1-1-venv/Scripts/python.exe" \
      E:/mubi-reviewflow/out/benchmark/fixtures/run_all.py [--only name1,name2]
"""

from __future__ import annotations

import sys

import bcg
import dk2007
import egger2001
import kappa_cohen
import kappa_fleiss_light
import linde2005
import normand1999
import raudenbush1985
import rothman2008
import senn2013
import yusuf1985

MODULES = [
    ("normand1999", normand1999),
    ("dk2007", dk2007),
    ("linde2005", linde2005),
    ("raudenbush1985", raudenbush1985),
    ("bcg", bcg),
    ("rothman2008", rothman2008),
    ("yusuf1985", yusuf1985),
    ("egger2001", egger2001),
    ("kappa_cohen", kappa_cohen),
    ("senn2013", senn2013),
    ("kappa_fleiss_light", kappa_fleiss_light),
]


def main() -> int:
    only = None
    if len(sys.argv) > 2 and sys.argv[1] == "--only":
        only = set(sys.argv[2].split(","))
    failures = []
    for name, module in MODULES:
        if only and name not in only:
            continue
        try:
            module.run()
        except Exception:
            import traceback

            traceback.print_exc()
            failures.append(name)
    if failures:
        print(f"FIXTURE CRASHES: {failures}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
