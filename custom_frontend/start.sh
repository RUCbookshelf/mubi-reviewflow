#!/bin/bash
# 一键启动 Co-Bookshelf（FastAPI 后端 :8613 + 静态前端 :8614）
# 注意：生产环境请把两个端口置于同一反向代理（nginx/Caddy）之后并启用 TLS（HTTPS），
#       同时用环境变量 COBOOKSHELF_CORS_ORIGINS 配置准确的同源地址。
cd "$(dirname "$0")/.."
.venv/bin/pip install -q -r requirements.txt
.venv/bin/uvicorn custom_backend.main:app --host 0.0.0.0 --port 8613 --reload &
python3 -m http.server 8614 -d custom_frontend &
echo "Backend: http://localhost:8613/api"
echo "Frontend: http://localhost:8614"
wait
