#!/usr/bin/env bash
# ============================================================
# 从项目根 .env 生成 K8s 配置物料（ConfigMap + Secret）
#
#   ./scripts/gen-k8s-config.sh            # 生成 k8s/generated/{configmap,secret}.yaml
#   ./scripts/gen-k8s-config.sh --check    # 生成 + kubectl --dry-run=client 校验
#   ./scripts/gen-k8s-config.sh --print    # 只做覆盖度检查，不写文件
#
# 生成物在 k8s/generated/（.gitignore 忽略）—— 真实密钥不进 git。
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/api-server/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

exec "$PY" "$ROOT/scripts/gen_k8s_config.py" "$@"
