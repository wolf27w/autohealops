#!/usr/bin/env bash
# AutoHealOps 平台发布脚本（M10 平台自身发布/回滚）
#
# 流程：构建镜像（带版本 tag）→ 推送 → kubectl set image → 滚动发布并等待完成。
#      发布前把各 Deployment 的当前镜像记录到 .deploy-history，便于回滚对照。
#
# 用法：
#   ./scripts/release_platform.sh --version v2026.10.15            # 默认全量 4 服务
#   ./scripts/release_platform.sh --version abc123 --services api-server,frontend
#   ./scripts/release_platform.sh --version t1 --dry-run          # 只打印将执行的动作
#   ./scripts/release_platform.sh --version t1 --skip-build       # 只 set image（镜像已就绪）
#   ./scripts/release_platform.sh --help
#
# 环境变量：
#   PLATFORM_REGISTRY   镜像仓库前缀（默认 api-ops，如 harbor.example.com/apiops）
#   PLATFORM_NAMESPACE  K8s 命名空间（默认 api-ops）
#   CONTAINER_ENGINE    podman|docker（默认自动探测）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REGISTRY="${PLATFORM_REGISTRY:-api-ops}"
NS="${PLATFORM_NAMESPACE:-api-ops}"
SERVICES="api-server,ai-service,k8s-executor,frontend"
VERSION=""
SKIP_BUILD=0
DRY_RUN=0
HISTORY="$ROOT/.deploy-history"

usage() { sed -n '2,20p' "$0"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="${2:?}"; shift 2 ;;
    --services) SERVICES="${2:?}"; shift 2 ;;
    --registry) REGISTRY="${2:?}"; shift 2 ;;
    --namespace) NS="${2:?}"; shift 2 ;;
    --skip-build) SKIP_BUILD=1; shift ;;
    --dry-run|-n) DRY_RUN=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "未知参数：$1（--help）" >&2; exit 2 ;;
  esac
done

# 版本号：默认取 git 短 sha，其次日期时间
if [ -z "$VERSION" ]; then
  VERSION="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || true)"
  [ -z "$VERSION" ] && VERSION="$(date +%Y%m%d-%H%M%S)"
fi
[ "$VERSION" = "latest" ] && { echo "版本号不能是 latest（回滚要靠唯一 tag）" >&2; exit 1; }

# 容器引擎
ENGINE="${CONTAINER_ENGINE:-}"
if [ -z "$ENGINE" ]; then
  if command -v podman >/dev/null 2>&1; then ENGINE=podman
  elif command -v docker >/dev/null 2>&1; then ENGINE=docker
  fi
fi
command -v kubectl >/dev/null 2>&1 || echo "⚠️ 未找到 kubectl，set image/rollout 将无法执行" >&2

echo "== 发布版本：$VERSION ｜ 仓库：$REGISTRY ｜ 命名空间：$NS ｜ 服务：$SERVICES =="

IFS=',' read -r -a SVC_ARR <<< "$SERVICES"
for SVC in "${SVC_ARR[@]}"; do
  SVC="$(echo "$SVC" | xargs)"   # trim
  [ -z "$SVC" ] && continue
  IMG="$REGISTRY/$SVC:$VERSION"
  echo "---- $SVC ----"
  if [ "$SKIP_BUILD" = "1" ]; then
    echo "[skip-build] 跳过构建，直接用 $IMG"
  elif [ "$DRY_RUN" = "1" ]; then
    echo "[dry-run] $ENGINE build -t $IMG $ROOT/$SVC && $ENGINE push $IMG"
  else
    [ -n "$ENGINE" ] || { echo "缺少容器引擎（podman/docker）" >&2; exit 1; }
    "$ENGINE" build -t "$IMG" "$ROOT/$SVC"
    "$ENGINE" push "$IMG"
  fi
  # 记录回滚前的当前镜像
  if [ "$DRY_RUN" != "1" ] && command -v kubectl >/dev/null 2>&1; then
    CUR="$(kubectl -n "$NS" get deploy "$SVC" -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)"
    echo "$(date '+%F %T')  $SVC  $CUR  ->  $IMG" >> "$HISTORY"
    kubectl -n "$NS" set image "deploy/$SVC" "$SVC=$IMG"
  else
    echo "[dry-run] kubectl -n $NS set image deploy/$SVC $SVC=$IMG"
  fi
done

if [ "$DRY_RUN" = "1" ]; then
  echo "[dry-run] kubectl -n $NS rollout status deploy/<svc> --timeout=300s"
  exit 0
fi

# 等待滚动完成（失败可立即用 rollback_platform.sh 回滚）
if command -v kubectl >/dev/null 2>&1; then
  for SVC in "${SVC_ARR[@]}"; do
    SVC="$(echo "$SVC" | xargs)"; [ -z "$SVC" ] && continue
    echo "等待 $SVC 滚动完成…"
    kubectl -n "$NS" rollout status "deploy/$SVC" --timeout=300s || {
      echo "❌ $SVC 滚动未在超时内完成，请检查：kubectl -n $NS describe deploy/$SVC" >&2
      echo "   回滚：./scripts/rollback_platform.sh --service $SVC" >&2
      exit 1
    }
  done
  echo "✅ 发布完成：$VERSION（回滚：./scripts/rollback_platform.sh）"
fi
