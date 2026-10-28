#!/bin/bash
# Co-Bookshelf 自定义后端启动脚本（开发/局域网用）
# 生产部署：务必置于 nginx 等 TLS 反向代理之后（HTTPS 终止在代理层），并关闭 --reload。
# R6-S1：--no-proxy-headers 保证 request.client.host 恒为直连对端——uvicorn
# 默认 proxy_headers=True 会把环回对端 XFF 写进 client.host，使应用层「按
# 直连对端 IP 分桶」被伪造 XFF 改写（真实 uvicorn 实测轮换 XFF 可绕过）。
# 反代场景按真实客户端 IP 的限流由 nginx limit_req 承担（见部署文档）。
set -e
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
  echo "[cobookshelf] 缺少 .venv，请先创建 Python 3.12 虚拟环境" >&2
  exit 1
fi
.venv/bin/pip install -q -r build/requirements-app.txt  # MM7-05: no requirements.txt exists at the repo root
exec .venv/bin/uvicorn custom_backend.main:app --host 0.0.0.0 --port 8613 --no-proxy-headers "$@"
