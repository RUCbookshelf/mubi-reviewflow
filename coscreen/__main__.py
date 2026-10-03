"""`python -m coscreen` 入口：调用 cli.main 并以返回值作为退出码。"""

import sys

from coscreen.cli import main

if __name__ == "__main__":
    sys.exit(main())
