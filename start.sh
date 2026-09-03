#!/usr/bin/env bash
# 一键启动 AI 小镇：后端 FastAPI(:9000) + 前端 Vite(:5174)
set -e
cd "$(dirname "$0")"

(cd server && source .venv/bin/activate && uvicorn main:app --reload --port 9000) &
(cd client && npm run dev) &

trap 'kill 0' EXIT
wait
