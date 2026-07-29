#!/usr/bin/env bash
set -e

# Kill both processes on Ctrl+C
cleanup() {
  kill $BACKEND_PID $FRONTEND_PID 2>/dev/null
  exit
}
trap cleanup INT TERM

echo "Starting backend on :8000 ..."
.venv/bin/uvicorn app.main:app --reload --port 8000 &
BACKEND_PID=$!

echo "Starting frontend on :5173 ..."
cd frontend && npm run dev &
FRONTEND_PID=$!

wait
