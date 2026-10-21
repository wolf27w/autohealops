#!/usr/bin/env bash
# ============================================================
# AutoHealOps 平台「开发重载 + 验证」脚本（本地开发用）
#
# 用法:
#   ./scripts/dev-reload.sh            # 静态校验 + 只重启 api-server（最常用）
#   ./scripts/dev-reload.sh --go       # 额外：重编并重启 k8s-executor
#   ./scripts/dev-reload.sh --fe       # 额外：前端 tsc -b + vite build
#   ./scripts/dev-reload.sh --all      # 以上全部
#
# 关键点（吸取的教训）:
#   - 重启只杀「LISTEN」进程：lsof -tiTCP:<port> -sTCP:LISTEN
#     （直接 lsof -ti tcp:8080 会把 vite 的前端代理出站连接一起杀掉）
#   - executor 同时占 9090/9099，起后必须确认 9099 也在听
# ============================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

DO_GO=0; DO_FE=0
for a in "$@"; do
  case "$a" in
    --go) DO_GO=1 ;;
    --fe) DO_FE=1 ;;
    --all) DO_GO=1; DO_FE=1 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "未知参数: $a" >&2; exit 1 ;;
  esac
done

port_listening() { lsof -iTCP:"$1" -sTCP:LISTEN -n -P >/dev/null 2>&1; }
wait_port_free() { for _ in $(seq 1 20); do port_listening "$1" || return 0; sleep 0.5; done; return 1; }
wait_port_up()   { for _ in $(seq 1 40); do port_listening "$1" && return 0; sleep 0.5; done; return 1; }

echo "== 1) 静态校验 =="
( cd "$ROOT/api-server" && ./.venv/bin/python -m py_compile app/main.py ) || { echo "  ✗ api-server py_compile 失败"; exit 1; }
echo "  ✓ api-server py_compile"
if [ "$DO_FE" = 1 ]; then
  ( cd "$ROOT/frontend" && npx tsc -b ) || { echo "  ✗ 前端 tsc 失败"; exit 1; }
  echo "  ✓ 前端 tsc"
fi

if [ "$DO_GO" = 1 ]; then
  echo "== 2) 重编 k8s-executor =="
  ( cd "$ROOT/k8s-executor" && go build -o bin/executor ./cmd/executor ) || { echo "  ✗ go build 失败"; exit 1; }
  pkill -f "k8s-executor/bin/executor" 2>/dev/null && echo "  停旧 executor"
  # 等 9090/9099 都释放（旧进程可能在慢 SQL 写入，TERM 后要几秒才退；不退就 KILL）
  freed=1
  for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
    if ! port_listening 9090 && ! port_listening 9099; then freed=0; break; fi
    [ "$i" = 6 ] && pkill -9 -f "k8s-executor/bin/executor" 2>/dev/null
    sleep 1
  done
  [ "$freed" = 1 ] && { echo "  ✗ 9090/9099 未释放"; exit 1; }
  ( cd "$ROOT" && nohup ./k8s-executor/bin/executor >> "$LOG_DIR/executor.log" 2>&1 & )
  wait_port_up 9090 || { echo "  ✗ executor 启动失败（见 logs/executor.log）"; exit 1; }
  echo "  ✓ executor :9090 UP"
  wait_port_up 9099 && echo "  ✓ executor :9099 UP" || { echo "  ✗ :9099 未监听（旧进程未死透，重跑一次）"; exit 1; }
fi

echo "== 3) 重启 api-server（只杀监听） =="
pids=$(lsof -tiTCP:8080 -sTCP:LISTEN -n -P 2>/dev/null)
if [ -n "$pids" ]; then kill $pids 2>/dev/null; echo "  停 api-server ($pids)"; fi
wait_port_free 8080 || { echo "  ✗ 8080 未释放"; exit 1; }
( cd "$ROOT/api-server" && nohup ./.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080 >> "$LOG_DIR/api-server.log" 2>&1 & )
wait_port_up 8080 && echo "  ✓ api-server :8080 UP" || { echo "  ✗ api-server 启动失败（见 logs/api-server.log）"; exit 1; }

if [ "$DO_FE" = 1 ]; then
  echo "== 4) 构建前端产物（vite build；dev 模式其实会热更，此步只做产物校验） =="
  ( cd "$ROOT/frontend" && npm run build >/dev/null ) && echo "  ✓ vite build" || { echo "  ✗ vite build 失败"; exit 1; }
fi

echo "== 状态 =="
echo "  api-server :8080  $(port_listening 8080 && echo "UP (health $(curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/health))" || echo DOWN)"
echo "  executor   :9090  $(port_listening 9090 && echo UP || echo DOWN)"
echo "  executor   :9099  $(port_listening 9099 && echo UP || echo DOWN)"
echo "  frontend   :5173  $(port_listening 5173 && echo "UP (vite dev)" || echo "DOWN（vite dev 未运行）")"
