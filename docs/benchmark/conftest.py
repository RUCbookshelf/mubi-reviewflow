"""docs/benchmark 的收集守卫（与 docs/methodology/conftest.py 同款，惰性）。

本目录按 A7（r10-U7）从 ``out/benchmark/`` 原样入库统计内核数值对拍的夹具与
结果。``fixtures/*.py`` 是研究态脚本而非测试：模块级 ``import common`` 依赖同
目录被插入 ``sys.path``，两个夹具（``egger2001.py`` / ``senn2013.py``）在被调用
时会在本目录写临时 sqlite。它们**按现有命名不会被 pytest 收集**（默认 pattern
只收 ``test_*.py`` / ``*_test.py``，实测 ``pytest docs/benchmark --collect-only``
→ "no tests collected"，有没有本文件都一样），所以本文件是第二道显式护栏：将来
谁往 ``fixtures/`` 里加 ``test_*.py``，收集会被这里挡住，而不是把研究脚本当产品
测试跑起来。

对 ``pytest build/tests`` 完全惰性（该命令不经过本目录）。钉子：
``build/tests/test_r10_u7_benchmark_docs.py`` 断言本文件存在、含
``collect_ignore = ["fixtures"]``，且 ``docs/benchmark/`` 下不存在可被默认收集的
测试文件。
"""

collect_ignore = ["fixtures"]
