"""report_evidence 浏览器回归的隔离实例启动器（G21 配套，可从仓库自 reproduced）。

以独立数据目录 + 指定端口启动本工作树代码的 build.launcher 实例，供
build/tests/report_evidence_browser.cjs 直连。用法（后台常驻，跑完手动停）：

    python build/tests/report_evidence_instance.py --port 18772 --data-dir E:/uxt-r4-frontend

参数均可省略：默认端口与 cjs 默认一致（18772）。实例健康检查通过后先跑
build/tests/report_evidence_prep.py 准备账号/任务/文献并复位证据工作区。
"""
import argparse
import os
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--port', type=int, default=18772, help='监听端口（默认 18772，避开其他泳道）')
    parser.add_argument('--host', default='127.0.0.1', help='监听地址（默认 127.0.0.1，仅本机）')
    parser.add_argument('--data-dir', required=True,
                        help='独立数据目录（COBOOKSHELF_DATA_DIR，须与 prep/cjs 使用同一目录）')
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ['COBOOKSHELF_DATA_DIR'] = str(data_dir)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 仓库根（本工作树代码）

    import uvicorn  # 延迟导入：先就绪数据目录与 sys.path 再加载应用

    from build.launcher import build_app

    uvicorn.run(build_app(), host=args.host, port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
