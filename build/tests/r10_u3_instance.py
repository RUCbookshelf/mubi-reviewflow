"""r10-u3 移动端泳道的隔离实例启动器（独立端口 + 独立数据目录）。

用法：
    python r10u3_instance.py --port 18833 --data-dir E:/rf214tmp/r10/u3/data

数据目录必须与本泳道 cjs 探针/测试使用同一目录。跑完由调用方杀进程。
"""
import argparse
import os
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18833)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--data-dir', required=True)
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ['COBOOKSHELF_DATA_DIR'] = str(data_dir)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

    import uvicorn
    from build.launcher import build_app

    uvicorn.run(build_app(), host=args.host, port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
