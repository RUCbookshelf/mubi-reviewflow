#!/bin/bash
# Co-Bookshelf 自定义后端启动脚本（开发/局域网用）
# 生产部署：务必置于 nginx 等 TLS 反向代理之后（HTTPS 终止在代理层），并关闭 --reload。
set -e
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
  echo "[cobookshelf] 缺少 .venv，请先创建 Python 3.12 虚拟环境" >&2
  exit 1
fi
.venv/bin/pip install -q -r requirements.txt
exec .venv/bin/uvicorn custom_backend.main:app --host 0.0.0.0 --port 8613 "$@"
