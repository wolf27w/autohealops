#!/usr/bin/env bash
# ============================================================
# API-OPS 自愈平台 —— 一键部署 / 环境预检（P3-R13）
#
# 用法:
#   ./install.sh                 # 本地模式：准备依赖 + 迁移 + 启动（默认）
#   ./install.sh --local         # 同上（显式）
#   ./install.sh --docker        # Docker 模式：构建镜像 + compose 起 + 迁移
#   ./install.sh --check         # 只做环境预检，不改动任何东西
#   ./install.sh --no-start      # 安装/构建 + 迁移，但不启动服务
#   ./install.sh --skip-deps     # 跳过依赖安装（复用已有 .venv / node_modules / bin）
#
# 依赖: 本地模式需 python3.10+ / node18+ / go1.21+；Docker 模式需 docker + docker compose。
# 配置: 统一读项目根 .env（缺失时从 .env.example 生成，需自行填写数据库/Redis 等）。
# ============================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

MODE=local
CHECK_ONLY=0
NO_START=0
SKIP_DEPS=0

if [ -t 1 ]; then C_R="\033[31m"; C_G="\033[32m"; C_Y="\033[33m"; C_B="\033[36m"; C_0="\033[0m"; else C_R=""; C_G=""; C_Y=""; C_B=""; C_0=""; fi
log()  { printf "%b\n" "${C_B}[install]${C_0} $*"; }
ok()   { printf "%b\n" "${C_G}  ✔${C_0} $*"; }
warn() { printf "%b\n" "${C_Y}  ! ${C_0}$*"; }
err()  { printf "%b\n" "${C_R}  ✘${C_0} $*" >&2; }
die()  { err "$*"; exit 1; }

usage() { sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

for a in "$@"; do
  case "$a" in
    --local) MODE=local ;;
    --docker) MODE=docker ;;
    --check) CHECK_ONLY=1 ;;
    --no-start) NO_START=1 ;;
    --skip-deps) SKIP_DEPS=1 ;;
    -h|--help) usage ;;
    *) die "未知参数：$a（用 --help 查看用法）" ;;
  esac
done

have() { command -v "$1" >/dev/null 2>&1; }

# 版本比较：ver_ge <实际> <要求>（去除非数字/点后逐段比较）
ver_ge() {
  local a="${1#v}" b="${2#v}"
  awk -v A="$a" -v B="$b" 'BEGIN{
    na=split(A,x,"."); nb=split(B,y,".");
    for(i=1;i<=(na>nb?na:nb);i++){ if((x[i]+0)>(y[i]+0)){print "1";exit} if((x[i]+0)<(y[i]+0)){print "0";exit} }
    print "1"
  }'
}

# 选一个 >=3.10 的 python
pick_python() {
  local cand p v
  for cand in python3.12 python3.11 python3.10 python3; do
    if have "$cand"; then
      v="$("$cand" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)"
      [ -n "$v" ] && [ "$(ver_ge "$v" 3.10)" = "1" ] && { echo "$cand"; return 0; }
    fi
  done
  return 1
}

# ---------------- 预检 ----------------
preflight() {
  log "环境预检（模式：$MODE）"
  local fail=0
  if [ "$MODE" = "docker" ]; then
    have docker && ok "docker $(docker --version 2>/dev/null | awk '{print $3}' | tr -d ,)" || { err "缺 docker"; fail=1; }
    docker compose version >/dev/null 2>&1 && ok "docker compose 可用" || { err "缺 docker compose"; fail=1; }
  else
    local py; py="$(pick_python || true)"
    [ -n "$py" ] && ok "python: $py ($("$py" --version 2>&1 | awk '{print $2}'))" || { err "缺 python3.10+"; fail=1; }
    if have node; then
      local nv; nv="$(node --version | tr -d v)"
      [ "$(ver_ge "$nv" 18)" = "1" ] && ok "node: v$nv" || { warn "node v$nv < 18（前端构建可能需要）"; }
    else warn "缺 node（仅影响前端）"; fi
    if have go; then
      local gv; gv="$(go version | awk '{print $3}' | sed 's/go//')"
      [ "$(ver_ge "$gv" 1.21)" = "1" ] && ok "go: $gv" || { warn "go $gv < 1.21"; }
    else warn "缺 go（仅影响 executor 构建）"; fi
  fi
  [ -f "$ROOT/.env" ] && ok ".env 已存在" || warn ".env 不存在（将尝试从 .env.example 生成，需自行填写）"
  [ -f "$ROOT/.env.example" ] || warn "缺 .env.example"
  [ -f "$ROOT/api-server/alembic.ini" ] && ok "alembic 配置就绪" || warn "缺 api-server/alembic.ini"
  echo
  if [ "$fail" = "1" ]; then die "预检未通过：请先补齐上述 ✘ 项"; fi
  ok "预检通过"
}

ensure_env() {
  if [ ! -f "$ROOT/.env" ]; then
    if [ -f "$ROOT/.env.example" ]; then
      cp "$ROOT/.env.example" "$ROOT/.env"
      warn "已由 .env.example 生成 .env —— 请填好 DATABASE_URL / REDIS_URL / ENCRYPTION_KEY / JWT_SECRET_KEY 后再启动"
    else
      die "缺 .env 且无 .env.example"
    fi
  fi
}

# ---------------- 本地模式 ----------------
install_local() {
  local py; py="$(pick_python || die '需要 python3.10+')"
  ensure_env
  if [ "$SKIP_DEPS" = "0" ]; then
    log "准备 Python 依赖（api-server / ai-service）"
    if ! [ -x "$ROOT/api-server/.venv/bin/python" ]; then "$py" -m venv "$ROOT/api-server/.venv"; fi
    "$ROOT/api-server/.venv/bin/pip" install -q --upgrade pip
    "$ROOT/api-server/.venv/bin/pip" install -q -r "$ROOT/api-server/requirements.txt"
    if ! [ -x "$ROOT/ai-service/.venv/bin/python" ]; then "$py" -m venv "$ROOT/ai-service/.venv"; fi
    "$ROOT/ai-service/.venv/bin/pip" install -q --upgrade pip
    "$ROOT/ai-service/.venv/bin/pip" install -q -r "$ROOT/ai-service/requirements.txt"
    ok "Python 依赖就绪"
    if have node && [ ! -d "$ROOT/frontend/node_modules" ]; then
      log "安装前端依赖（npm）"; (cd "$ROOT/frontend" && npm install --no-audit --no-fund) && ok "前端依赖就绪" || warn "前端 npm install 失败（可稍后重试）"
    fi
    if have go; then log "构建 k8s-executor"; (cd "$ROOT/k8s-executor" && go build -o bin/executor ./cmd/executor) && ok "executor 构建完成" || warn "executor 构建失败"; fi
  else
    warn "跳过依赖安装（--skip-deps）"
  fi
  migrate
  [ "$NO_START" = "0" ] && { log "启动服务"; bash "$ROOT/start.sh" start; } || warn "跳过启动（--no-start）"
}

# ---------------- Docker 模式 ----------------
install_docker() {
  local dc="docker compose"
  ensure_env
  log "构建镜像（$dc build）"; $dc build || die "镜像构建失败"
  log "启动容器（$dc up -d）"; $dc up -d || die "compose 启动失败"
  log "等待 api-server 就绪…"
  local i okh=0
  for i in $(seq 1 30); do
    if curl -fsS -m 3 http://localhost:8080/health >/dev/null 2>&1; then okh=1; break; fi
    sleep 3
  done
  [ "$okh" = "1" ] && ok "api-server 已就绪" || warn "api-server 未在预期时间内就绪（可 docker compose logs 查看）"
  migrate_docker
}

# ---------------- 迁移 ----------------
migrate() {
  log "数据库迁移（alembic upgrade head）"
  if [ -x "$ROOT/api-server/.venv/bin/alembic" ]; then
    (cd "$ROOT/api-server" && ./.venv/bin/alembic upgrade head) && ok "迁移完成" || die "迁移失败（检查 .env 的 DATABASE_URL）"
  else
    warn "未找到 api-server/.venv/bin/alembic，跳过迁移"
  fi
}
migrate_docker() {
  log "数据库迁移（容器内）"
  docker compose exec -T api-server alembic upgrade head && ok "迁移完成" || warn "容器内迁移失败（可手动 docker compose exec api-server alembic upgrade head）"
}

# ---------------- 汇总 ----------------
summary() {
  echo
  log "完成。访问入口："
  if [ "$MODE" = "docker" ]; then
    echo "  前端/大屏   http://localhost/        (compose 前端 :80)"
    echo "  api-server  http://localhost:8080/health"
    echo "  ai-service  http://localhost:9000/docs"
  else
    echo "  前端/大屏   http://localhost:5173/   （大屏 /screen）"
    echo "  api-server  http://localhost:8080/health"
    echo "  ai-service  http://localhost:9000/docs"
    echo "  executor    gRPC :9090 / 日志流 HTTP :9099"
  fi
  [ "$NO_START" = "1" ] && echo "  （未启动：./start.sh start 手动启动）" || true
}

# ---------------- 主流程 ----------------
preflight
[ "$CHECK_ONLY" = "1" ] && { ok "--check 完成（未做任何改动）"; exit 0; }
case "$MODE" in
  docker) install_docker ;;
  *) install_local ;;
esac
summary
