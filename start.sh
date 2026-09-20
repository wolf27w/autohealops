#!/usr/bin/env bash
# ============================================================
# API-OPS 平台本地启动/管理脚本（nohup 后台方式）
#
# 用法:
#   ./start.sh            # 启动全部服务（默认）
#   ./start.sh start      # 同上
#   ./start.sh stop       # 停止全部服务
#   ./start.sh restart    # 重启全部服务
#   ./start.sh status     # 查看运行状态
#
# 服务:
#   api-server    :8080  (FastAPI)
#   ai-service    :9000  (FastAPI)
#   k8s-executor  gRPC :9090  (Go)
#   frontend      :5173  (Vite)
# ============================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

# ---------- 基础工具 ----------
port_listening() { lsof -iTCP:"$1" -sTCP:LISTEN -n -P >/dev/null 2>&1; }

kill_port() {  # $1=port $2=label
  local pids
  pids=$(lsof -tiTCP:"$1" -sTCP:LISTEN -n -P 2>/dev/null)
  if [ -n "$pids" ]; then
    kill $pids 2>/dev/null
    echo "  [停止] $2 (pid: $pids)"
  else
    echo "  [未运行] $2"
  fi
}

kill_pattern() {  # $1=pattern $2=label
  if pgrep -f "$1" >/dev/null 2>&1; then
    pkill -f "$1" 2>/dev/null
    echo "  [停止] $2"
  else
    echo "  [未运行] $2"
  fi
}

http_code() { curl -s -o /dev/null -w "%{http_code}" "$1" 2>/dev/null; }

# ---------- 启动 ----------
start_api_server() {
  if port_listening 8080; then echo "  [跳过] api-server 已在运行 (:8080)"; return; fi
  (cd "$ROOT/api-server" && nohup ./.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080 >> "$LOG_DIR/api-server.log" 2>&1 &)
  echo "  [启动] api-server -> :8080"
}

start_ai_service() {
  if port_listening 9000; then echo "  [跳过] ai-service 已在运行 (:9000)"; return; fi
  (cd "$ROOT/ai-service" && nohup ./.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 9000 >> "$LOG_DIR/ai-service.log" 2>&1 &)
  echo "  [启动] ai-service -> :9000"
}

start_executor() {
  if pgrep -f "k8s-executor/bin/executor" >/dev/null 2>&1; then echo "  [跳过] k8s-executor 已在运行"; return; fi
  (cd "$ROOT" && nohup ./k8s-executor/bin/executor >> "$LOG_DIR/executor.log" 2>&1 &)
  echo "  [启动] k8s-executor -> gRPC :9090"
}

start_frontend() {
  if port_listening 5173; then echo "  [跳过] frontend 已在运行 (:5173)"; return; fi
  (cd "$ROOT/frontend" && nohup ./node_modules/.bin/vite >> "$LOG_DIR/frontend.log" 2>&1 &)
  echo "  [启动] frontend -> :5173"
}

start_all() {
  echo "== 启动 API-OPS 服务 =="
  start_api_server
  start_ai_service
  start_executor
  start_frontend
  echo ""
  echo "等待服务拉起..."
  sleep 4
  status
}

# ---------- 停止 ----------
stop_all() {
  echo "== 停止 API-OPS 服务 =="
  kill_port 8080 "api-server"
  kill_port 9000 "ai-service"
  kill_port 5173 "frontend"
  kill_pattern "k8s-executor/bin/executor" "k8s-executor"
}

# ---------- 状态 ----------
status() {
  echo "== 服务状态 =="
  if port_listening 8080; then echo "  api-server    :8080  UP  (health $(http_code http://localhost:8080/health))"; else echo "  api-server    :8080  DOWN"; fi
  if port_listening 9000; then echo "  ai-service    :9000  UP  (docs $(http_code http://localhost:9000/docs))"; else echo "  ai-service    :9000  DOWN"; fi
  if pgrep -f "k8s-executor/bin/executor" >/dev/null 2>&1; then echo "  k8s-executor  :9090  UP"; else echo "  k8s-executor  :9090  DOWN"; fi
  if port_listening 5173; then echo "  frontend      :5173  UP"; else echo "  frontend      :5173  DOWN"; fi
}

# ---------- 入口 ----------
case "${1:-start}" in
  start)   start_all ;;
  stop)    stop_all ;;
  restart) stop_all; echo ""; sleep 2; start_all ;;
  status)  status ;;
  *)
    echo "用法: $0 {start|stop|restart|status}" >&2
    exit 1
    ;;
esac
