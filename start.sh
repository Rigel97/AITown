#!/usr/bin/env bash
# 一键启动 AI 小镇：后端 FastAPI(:8000) + 前端 Vite(:5173)
set -e
cd "$(dirname "$0")"

(cd server && source .venv/bin/activate && uvicorn main:app --reload --port 8000) &
(cd client && npm run dev) &

trap 'kill 0' EXIT
wait
