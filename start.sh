#!/usr/bin/env bash
# ============================================================
# AutoHealOps 平台本地启动/管理脚本（nohup 后台方式）
#
# 用法:
#   ./start.sh            # 启动全部服务（默认）
#   ./start.sh start      # 同上
#   ./start.sh stop       # 停止全部服务
#   ./start.sh restart    # 重启全部服务
#   ./start.sh status     # 查看运行状态（含 api-server 双端口归属校验）
#
# 服务:
#   api-server    :8080 (HTTP) + :9095 (agent gRPC 网关)   同一进程必须同时持有
#   ai-service    :9000  (FastAPI)
#   k8s-executor  gRPC :9090 + 日志流 :9099  (Go)
#   frontend      :5173  (Vite)
#
# ⚠️ 2026-11-10 踩坑：api-server 收到 SIGTERM 后 gRPC 线程会卡住不退出，
#    而 gRPC 用 SO_REUSEPORT，新进程仍能 bind 9095 → 出现「旧进程持隧道、新进程持 8080」，
#    agent 集群的实时 RPC 全部回退本机 executor（报 client config 构建失败）。
#    本脚本 stop 时会按进程名强杀 + 校验 8080/9095 释放；start 后校验两端口属于同一 PID。
# ============================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

# ---------- 基础工具 ----------
port_listening() { lsof -iTCP:"$1" -sTCP:LISTEN -n -P >/dev/null 2>&1; }
pids_listening() { lsof -tiTCP:"$1" -sTCP:LISTEN -n -P 2>/dev/null | sort -u | tr '\n' ' ' | sed 's/ $//'; }

http_code() { curl -s --max-time 3 -o /dev/null -w "%{http_code}" "$1" 2>/dev/null; }

wait_port_free() {  # $1=port $2=超时秒
  local i=0
  while [ -n "$(pids_listening "$1")" ] && [ "$i" -lt "${2:-6}" ]; do sleep 1; i=$((i+1)); done
  [ -z "$(pids_listening "$1")" ]
}

kill_pattern() {  # $1=pattern $2=label
  if pgrep -f "$1" >/dev/null 2>&1; then
    pkill -f "$1" 2>/dev/null
    echo "  [停止] $2"
  else
    echo "  [未运行] $2"
  fi
}

# api-server 专用停止：按进程名（不是按端口）杀，SIGTERM 无效则 -9，最后清理 8080/9095 残留占用
stop_api_server() {
  local label="api-server"
  local pids
  local pat="uvicorn app.main:app --host 0.0.0.0 --port 8080"
  pids=$(pgrep -f "$pat" | tr '\n' ' ' | sed 's/ $//')
  if [ -n "$pids" ]; then
    echo "  [停止] $label (pid: $pids)"
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    local i=0
    while pgrep -f "$pat" >/dev/null 2>&1 && [ "$i" -lt 6 ]; do sleep 1; i=$((i+1)); done
    if pgrep -f "$pat" >/dev/null 2>&1; then
      echo "  [强杀] $label 未响应 SIGTERM（gRPC 线程卡住，已知现象）"
      pkill -9 -f "$pat" 2>/dev/null || true
      sleep 1
    fi
  else
    echo "  [未运行] $label"
  fi
  # 兜底：谁还占着 api-server 的端口就结束谁（历史残留进程可能 cmdline 不匹配）
  local port holders
  for port in 8080 9095; do
    holders=$(pids_listening "$port")
    if [ -n "$holders" ]; then
      echo "  [清理] :$port 仍被占用（pid: $holders），强制结束"
      # shellcheck disable=SC2086
      kill -9 $holders 2>/dev/null || true
      wait_port_free "$port" 5 || echo "  [警告] :$port 释放失败，请手动检查"
    fi
  done
}

# ---------- 启动 ----------
start_api_server() {
  local cur8080 cur9095
  cur8080=$(pids_listening 8080 | awk '{print $1}')
  cur9095=$(pids_listening 9095 | awk '{print $1}')
  if [ -n "$cur8080" ] && [ "$cur8080" = "$cur9095" ]; then
    echo "  [跳过] api-server 已在运行 (:8080/:9095 pid=$cur8080)"
    return 0
  fi
  if [ -n "$cur8080" ] || [ -n "$cur9095" ]; then
    echo "  [拒绝启动] 端口归属异常：:8080=[${cur8080:-空}] :9095=[${cur9095:-空}]"
    echo "             先执行 ./start.sh stop（避免双进程劈端口导致 agent 隧道失效）"
    return 1
  fi
  nohup sh -c "cd '$ROOT/api-server' && exec ./.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080" >> "$LOG_DIR/api-server.log" 2>&1 < /dev/null &
  echo "  [启动] api-server -> :8080 (+:9095)"

  # 启动后校验：同一个 PID 必须同时持有 8080 与 9095
  local i=0 p8080="" p9095=""
  while [ "$i" -lt 15 ]; do
    sleep 1
    p8080=$(pids_listening 8080 | awk '{print $1}')
    p9095=$(pids_listening 9095 | awk '{print $1}')
    [ -n "$p8080" ] && [ -n "$p9095" ] && break
    i=$((i+1))
  done
  if [ -n "$p8080" ] && [ "$p8080" = "$p9095" ]; then
    echo "  [校验] api-server pid=$p8080 同时持有 :8080/:9095 ✓"
  else
    echo "  [警告] 端口归属异常：:8080=[${p8080:-空}] :9095=[${p9095:-空}]"
    echo "         agent 隧道可能落在旧进程上，实时 RPC 会失败 → 请立即 ./start.sh restart"
    return 1
  fi
}

start_ai_service() {
  if port_listening 9000; then echo "  [跳过] ai-service 已在运行 (:9000)"; return; fi
  nohup sh -c "cd '$ROOT/ai-service' && exec ./.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 9000" >> "$LOG_DIR/ai-service.log" 2>&1 < /dev/null &
  echo "  [启动] ai-service -> :9000"
}

start_executor() {
  if pgrep -f "k8s-executor/bin/executor" >/dev/null 2>&1; then echo "  [跳过] k8s-executor 已在运行"; return; fi
  nohup sh -c "cd '$ROOT' && exec ./k8s-executor/bin/executor" >> "$LOG_DIR/executor.log" 2>&1 < /dev/null &
  echo "  [启动] k8s-executor -> gRPC :9090 / 日志流 :9099"
}

start_frontend() {
  if port_listening 5173; then echo "  [跳过] frontend 已在运行 (:5173)"; return; fi
  nohup sh -c "cd '$ROOT/frontend' && exec ./node_modules/.bin/vite" >> "$LOG_DIR/frontend.log" 2>&1 < /dev/null &
  echo "  [启动] frontend -> :5173"
}

start_all() {
  echo "== 启动 AutoHealOps 服务 =="
  start_api_server
  start_ai_service
  start_executor
  start_frontend
  echo ""
  echo "等待服务拉起..."
  sleep 3
  status
}

# ---------- 停止 ----------
stop_all() {
  echo "== 停止 AutoHealOps 服务 =="
  stop_api_server
  kill_pattern "uvicorn app.main:app --host 0.0.0.0 --port 9000" "ai-service"
  local holders
  holders=$(pids_listening 9000)
  if [ -n "$holders" ]; then
    echo "  [清理] :9000 仍被占用（pid: $holders），强制结束"
    # shellcheck disable=SC2086
    kill -9 $holders 2>/dev/null || true
  fi
  # 前端
  holders=$(pids_listening 5173)
  if [ -n "$holders" ]; then
    echo "  [停止] frontend (pid: $holders)"
    # shellcheck disable=SC2086
    kill $holders 2>/dev/null || true
  else
    echo "  [未运行] frontend"
  fi
  kill_pattern "k8s-executor/bin/executor" "k8s-executor"
}

# ---------- 状态 ----------
status() {
  echo "== 服务状态 =="
  local p8080 p9095
  p8080=$(pids_listening 8080 | awk '{print $1}')
  p9095=$(pids_listening 9095 | awk '{print $1}')
  if [ -n "$p8080" ]; then
    echo "  api-server    :8080  UP  (pid=${p8080}, health $(http_code http://localhost:8080/health))"
  else
    echo "  api-server    :8080  DOWN"
  fi
  if [ -n "$p9095" ]; then
    if [ "$p9095" = "$p8080" ]; then
      echo "  agent 网关     :9095  UP  (pid=${p9095}, 与 :8080 同进程 ✓)"
    else
      echo "  agent 网关     :9095  ⚠ 归属异常（pid=${p9095} ≠ :8080 pid=${p8080:-无}）"
      echo "                         双进程劈端口：agent 实时 RPC 会失败，执行 ./start.sh restart"
    fi
  else
    echo "  agent 网关     :9095  DOWN（agent 无法接入）"
  fi
  if [ -n "$(pids_listening 9000)" ]; then echo "  ai-service    :9000  UP  (docs $(http_code http://localhost:9000/docs))"; else echo "  ai-service    :9000  DOWN"; fi
  if pgrep -f "k8s-executor/bin/executor" >/dev/null 2>&1; then echo "  k8s-executor  :9090  UP  (日志流 :9099 $( [ -n "$(pids_listening 9099)" ] && echo UP || echo DOWN ))"; else echo "  k8s-executor  :9090  DOWN"; fi
  if [ -n "$(pids_listening 5173)" ]; then echo "  frontend      :5173  UP"; else echo "  frontend      :5173  DOWN"; fi
}

# ---------- 入口 ----------
case "${1:-start}" in
  start)   start_all ;;
  stop)    stop_all ;;
  restart) stop_all; echo ""; sleep 1; start_all ;;
  status)  status ;;
  *)
    echo "用法: $0 {start|stop|restart|status}" >&2
    exit 1
    ;;
esac
