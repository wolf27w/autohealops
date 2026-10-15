#!/usr/bin/env bash
# API-OPS 平台回滚脚本（M10）
#
# 用 K8s Deployment 的 revisionHistory（保留 5 版，见 k8s/deployments.yaml revisionHistoryLimit）
# 回滚到上一个/指定版本。镜像 tag 唯一（非 latest），因此 ReplicaSet 历史可回溯。
#
# 用法：
#   ./scripts/rollback_platform.sh                       # 全部服务回滚上一版
#   ./scripts/rollback_platform.sh --service api-server  # 只回滚一个服务
#   ./scripts/rollback_platform.sh --service api-server --to-revision 3
#   ./scripts/rollback_platform.sh --status              # 只看 rollout 历史
#   ./scripts/rollback_platform.sh --help
#
# 环境变量：PLATFORM_NAMESPACE（默认 api-ops）
set -euo pipefail

NS="${PLATFORM_NAMESPACE:-api-ops}"
SERVICES="api-server,ai-service,k8s-executor,frontend"
TO_REVISION=""
STATUS_ONLY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --service) SERVICES="${2:?}"; shift 2 ;;
    --namespace) NS="${2:?}"; shift 2 ;;
    --to-revision) TO_REVISION="${2:?}"; shift 2 ;;
    --status) STATUS_ONLY=1; shift ;;
    --help|-h) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "未知参数：$1（--help）" >&2; exit 2 ;;
  esac
done

command -v kubectl >/dev/null 2>&1 || { echo "缺少 kubectl" >&2; exit 1; }

IFS=',' read -r -a SVC_ARR <<< "$SERVICES"
for SVC in "${SVC_ARR[@]}"; do
  SVC="$(echo "$SVC" | xargs)"; [ -z "$SVC" ] && continue
  echo "==== $SVC 发布历史 ===="
  kubectl -n "$NS" rollout history "deploy/$SVC" || true
  if [ "$STATUS_ONLY" = "1" ]; then
    kubectl -n "$NS" rollout status "deploy/$SVC" --timeout=5s || true
    continue
  fi
  if [ -n "$TO_REVISION" ]; then
    echo "回滚 $SVC → revision $TO_REVISION …"
    kubectl -n "$NS" rollout undo "deploy/$SVC" --to-revision="$TO_REVISION"
  else
    echo "回滚 $SVC → 上一版 …"
    kubectl -n "$NS" rollout undo "deploy/$SVC"
  fi
  kubectl -n "$NS" rollout status "deploy/$SVC" --timeout=300s
done
echo "✅ 回滚完成"
