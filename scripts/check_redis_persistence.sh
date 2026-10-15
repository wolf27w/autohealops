#!/usr/bin/env bash
# 确认 Redis 持久化是否开启（M11 备份/DR 的一部分）
#
# 用法：./scripts/check_redis_persistence.sh
# 连接：优先环境变量 REDIS_URL，其次 .env 里的 REDIS_URL
# 判定：appendonly=yes（AOF）或 save 非空（RDB 定时快照）→ 视为已持久化。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REDIS_URL="${REDIS_URL:-}"
if [ -z "$REDIS_URL" ] && [ -f "$ROOT/.env" ]; then
  REDIS_URL="$(grep -E '^REDIS_URL=' "$ROOT/.env" | head -1 | cut -d= -f2- || true)"
fi
if [ -z "$REDIS_URL" ]; then
  echo "未找到 REDIS_URL（env 或 .env）" >&2; exit 1
fi

if ! command -v redis-cli >/dev/null 2>&1; then
  echo "未安装 redis-cli（可用容器：podman run --rm redis redis-cli -u <url> ...）" >&2
  exit 1
fi

echo "== Redis 持久化检查（$REDIS_URL）=="
AOF="$(redis-cli -u "$REDIS_URL" config get appendonly | tail -1 || echo "?")"
SAVE="$(redis-cli -u "$REDIS_URL" config get save | tail -1 || echo "?")"
DIR="$(redis-cli -u "$REDIS_URL" config get dir | tail -1 || echo "?")"
echo "appendonly = $AOF"
echo "save       = $SAVE"
echo "dir        = $DIR"
echo "--- INFO persistence（节选）---"
redis-cli -u "$REDIS_URL" info persistence | grep -E 'rdb_last_bgsave_status|rdb_last_save_time|aof_enabled|aof_last_write_status' || true

if [ "$AOF" = "yes" ] || { [ -n "$SAVE" ] && [ "$SAVE" != '""' ]; }; then
  echo "✅ Redis 持久化：已开启"
else
  echo "⚠️ Redis 持久化：未开启（appendonly=no 且 save 为空）——重启会丢数据，建议开启 AOF 或保存快照" >&2
  exit 3
fi
