#!/usr/bin/env bash
# 一键启动 OJ：FastAPI 后端（8000）+ Streamlit 前端（8501）
# 用法：./run.sh —— 已在运行的会跳过；Ctrl+C 停止本脚本启动的服务
set -e
cd "$(dirname "$0")"

BACKEND_URL="http://127.0.0.1:8000/api/languages/"
FRONTEND_URL="http://127.0.0.1:8501"
STARTED=()

cleanup() {
  for pid in "${STARTED[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT
trap 'exit 130' INT TERM

wait_url() {  # 等待 URL 可访问（最多 20s）
  for _ in $(seq 1 40); do
    curl --noproxy '*' -s -o /dev/null --max-time 1 "$1" 2>/dev/null && return 0
    sleep 0.5
  done
  return 1
}

echo "== Online Judge 一键启动 =="

if curl --noproxy '*' -s -o /dev/null --max-time 1 "$BACKEND_URL" 2>/dev/null; then
  echo "✓ 后端已在运行（8000）"
else
  .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 &
  STARTED+=("$!")
  if wait_url "$BACKEND_URL"; then
    echo "✓ 后端已启动：http://127.0.0.1:8000"
  else
    echo "✗ 后端启动失败，请检查上方日志" >&2
    exit 1
  fi
fi

if curl --noproxy '*' -s -o /dev/null --max-time 1 "$FRONTEND_URL" 2>/dev/null; then
  echo "✓ Streamlit 前端已在运行（8501）"
else
  .venv/bin/streamlit run app.py --server.headless true --server.port 8501 &
  STARTED+=("$!")
  if wait_url "$FRONTEND_URL"; then
    echo "✓ Streamlit 前端已启动：$FRONTEND_URL"
  else
    echo "✗ Streamlit 启动失败，请检查上方日志" >&2
    exit 1
  fi
fi

echo
echo "--------------------------------------------"
echo "  Streamlit 前端  : http://localhost:8501"
echo "  后端 API        : http://localhost:8000"
echo "  管理员账号      : admin / admintestpassword"
echo "--------------------------------------------"

if [ "${#STARTED[@]}" -eq 0 ]; then
  echo "所有服务此前已在运行，未启动新进程。"
  exit 0
fi

echo "按 Ctrl+C 停止本脚本启动的服务。"
wait
