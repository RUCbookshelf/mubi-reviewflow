#!/bin/bash
# 一键启动 Co-Bookshelf（FastAPI 后端 :8613 + 静态前端 :8614）
# 注意：生产环境请把两个端口置于同一反向代理（nginx/Caddy）之后并启用 TLS（HTTPS），
#       同时用环境变量 COBOOKSHELF_CORS_ORIGINS 配置准确的同源地址。
# MM7-05：依赖文件是 build/requirements-app.txt（仓库根目录没有 requirements.txt）；
#         set -e + uvicorn 存在性检查保证启动失败时不再打印成功地址。
set -e
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
  echo "[cobookshelf] 缺少 .venv，请先创建 Python 3.12 虚拟环境" >&2
  exit 1
fi
if [ ! -x .venv/bin/uvicorn ]; then
  echo "[cobookshelf] 缺少 .venv/bin/uvicorn，无法启动后端（请先 pip install -r build/requirements-app.txt）" >&2
  exit 1
fi
.venv/bin/pip install -q -r build/requirements-app.txt
.venv/bin/uvicorn custom_backend.main:app --host 0.0.0.0 --port 8613 --reload --no-proxy-headers &  # R6-S1：peer 计数维度不被 XFF 改写
python3 -m http.server 8614 -d custom_frontend &
echo "Backend: http://localhost:8613/api"
echo "Frontend: http://localhost:8614"
wait
