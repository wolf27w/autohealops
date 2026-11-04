#!/usr/bin/env bash
# 备份平台自建 CA（agent 隧道 mTLS 用）—— 2026-11-04
#
# 为什么必须备份：
#   data/agent-ca/ca.key 是平台自建 CA 的私钥。丢了它：
#     · 网关服务端证书无法重签
#     · 所有 agent 的客户端证书都无法再签发
#     · 只能重建 CA 并给每个集群重新下发 install.yaml
#
# 用法：
#   ./scripts/backup_agent_ca.sh                # 备份到 ~/autohealops-backup/agent-ca
#   ./scripts/backup_agent_ca.sh /path/to/dest  # 指定目录（建议放 NAS / 对象存储 / 密码库）
#
# 建议：纳入现有备份流程（和 MySQL 备份一起，或单独定时）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CA_DIR="${AGENT_CA_DIR:-$ROOT/data/agent-ca}"
DEST="${1:-$HOME/autohealops-backup/agent-ca}"

if [ ! -f "$CA_DIR/ca.key" ] || [ ! -f "$CA_DIR/ca.crt" ]; then
  echo "✗ 未找到 CA：$CA_DIR/ca.key|crt（平台还没启动过或路径不对）" >&2
  exit 1
fi

mkdir -p "$DEST"
TS="$(date +%Y%m%d-%H%M%S)"
OUT="$DEST/agent-ca-$TS.tgz"
tar -C "$(dirname "$CA_DIR")" -czf "$OUT" "$(basename "$CA_DIR")"
chmod 600 "$OUT"
echo "✓ 已备份：$OUT"
echo "  恢复：tar -xzf $OUT -C <平台仓库根>/data  （保持目录名 agent-ca）"
