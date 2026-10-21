#!/usr/bin/env bash
# AutoHealOps 平台数据库备份（M11 备份/DR）
#
# 用法：
#   ./scripts/backup_mysql.sh                # 立即执行一次备份
#   ./scripts/backup_mysql.sh --dry-run      # 只打印将要做什么，不落盘/不删除
#   ./scripts/backup_mysql.sh --help
#
# 连接参数优先取环境变量，其次解析 .env 里的 DATABASE_URL：
#   MYSQL_HOST MYSQL_PORT MYSQL_USER MYSQL_PASSWORD MYSQL_DB
# 备份落地：BACKUP_DIR（默认 ./backups），文件名 api_ops-YYYYmmdd-HHMMSS.sql.gz
# 保留：BACKUP_KEEP_DAYS 天（默认 7），超期自动清理（--dry-run 不删）
# 异地：BACKUP_REMOTE 非空时，备份完成后执行 `rclone copy <file> <BACKUP_REMOTE>`
#       （未装 rclone 则跳过并告警；也可换成 scp/ossutil，按需改 _upload）
#
# ⚠️ 恢复必须同时有 ENCRYPTION_KEY（否则库内 kubeconfig/密码等密文解不开）：
#    本脚本会把当前 ENCRYPTION_KEY 一并写入 <备份文件>.keyinfo.json。
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-$(cd "$(dirname "$0")/.." && pwd)/backups}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-7}"
DRY_RUN=0

for arg in "$@"; do
  case "$arg" in
    --dry-run|-n) DRY_RUN=1 ;;
    --help|-h) sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "未知参数：$arg（用 --help 查看用法）" >&2; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# 1) 解析连接参数（env 优先，其次 .env 的 DATABASE_URL）
if [ -z "${MYSQL_HOST:-}" ] && [ -f "$ROOT/.env" ]; then
  DBURL="$(grep -E '^DATABASE_URL=' "$ROOT/.env" | head -1 | cut -d= -f2- || true)"
  if [ -n "${DBURL:-}" ]; then
    # mysql+aiomysql://user:pass@host:port/db
    _proto_rest="${DBURL#*://}"
    _creds="${_proto_rest%@*}"
    _hostrest="${_proto_rest#*@}"
    MYSQL_USER="${MYSQL_USER:-${_creds%%:*}}"
    MYSQL_PASSWORD="${MYSQL_PASSWORD:-${_creds#*:}}"
    MYSQL_HOST="${MYSQL_HOST:-${_hostrest%%:*}}"
    _portdb="${_hostrest#*:}"
    MYSQL_PORT="${MYSQL_PORT:-${_portdb%%/*}}"
    MYSQL_DB="${MYSQL_DB:-${_portdb##*/}}"
  fi
fi

MYSQL_HOST="${MYSQL_HOST:-127.0.0.1}"
MYSQL_PORT="${MYSQL_PORT:-3306}"
MYSQL_USER="${MYSQL_USER:-root}"
MYSQL_PASSWORD="${MYSQL_PASSWORD:-}"
MYSQL_DB="${MYSQL_DB:-api_ops}"
BACKUP_REMOTE="${BACKUP_REMOTE:-}"

TS="$(date +%Y%m%d-%H%M%S)"
OUT="$BACKUP_DIR/${MYSQL_DB}-${TS}.sql.gz"

echo "== 备份 $MYSQL_DB @ $MYSQL_HOST:$MYSQL_PORT -> $OUT（保留 ${BACKUP_KEEP_DAYS} 天）=="
if [ "$DRY_RUN" = "1" ]; then
  echo "[dry-run] 将执行 mysqldump … | gzip > $OUT"
  echo "[dry-run] 将写入 $OUT.keyinfo.json（含 ENCRYPTION_KEY 指纹）"
  echo "[dry-run] 将清理 $BACKUP_DIR 下超过 ${BACKUP_KEEP_DAYS} 天的 *.sql.gz"
  [ -n "$BACKUP_REMOTE" ] && echo "[dry-run] 将 rclone copy $OUT $BACKUP_REMOTE"
  exit 0
fi

command -v mysqldump >/dev/null 2>&1 || { echo "缺少 mysqldump（安装 mysql-client）" >&2; exit 1; }
mkdir -p "$BACKUP_DIR"

# 2) mysqldump（一致快照，含存储过程/触发器/事件）
MYSQL_PWD="$MYSQL_PASSWORD" mysqldump \
  -h "$MYSQL_HOST" -P "$MYSQL_PORT" -u "$MYSQL_USER" \
  --single-transaction --routines --triggers --events --set-gtid-purged=OFF \
  "$MYSQL_DB" | gzip > "$OUT"

# 3) 记录恢复所需的关键密钥（不写明文，只写指纹与来源提示）
_KEY="${ENCRYPTION_KEY:-}"
if [ -z "$_KEY" ] && [ -f "$ROOT/.env" ]; then
  _KEY="$(grep -E '^ENCRYPTION_KEY=' "$ROOT/.env" | head -1 | cut -d= -f2- || true)"
fi
_KEY_FP="$(printf '%s' "$_KEY" | shasum -a 256 2>/dev/null | cut -c1-16 || true)"
cat > "$OUT.keyinfo.json" <<EOF
{
  "db": "$MYSQL_DB", "host": "$MYSQL_HOST", "time": "$TS",
  "encryption_key_sha256_16": "$_KEY_FP",
  "note": "恢复本备份时需提供与上述指纹一致的 ENCRYPTION_KEY，否则密文字段无法解密"
}
EOF

echo "✅ 备份完成：$OUT （$(du -h "$OUT" | cut -f1)）"

# 4) 异地副本（可选）
if [ -n "$BACKUP_REMOTE" ]; then
  if command -v rclone >/dev/null 2>&1; then
    rclone copy "$OUT" "$BACKUP_REMOTE" && echo "✅ 已同步到 $BACKUP_REMOTE" || echo "⚠️ 异地同步失败：$BACKUP_REMOTE" >&2
  else
    echo "⚠️ BACKUP_REMOTE 已设置但未安装 rclone，跳过异地同步" >&2
  fi
fi

# 5) 保留策略：删除超过 N 天的备份
find "$BACKUP_DIR" -maxdepth 1 -name "${MYSQL_DB}-*.sql.gz" -mtime "+${BACKUP_KEEP_DAYS}" -print -delete
find "$BACKUP_DIR" -maxdepth 1 -name "${MYSQL_DB}-*.sql.gz.keyinfo.json" -mtime "+${BACKUP_KEEP_DAYS}" -print -delete
echo "✅ 保留策略执行完毕（>${BACKUP_KEEP_DAYS} 天已清理）"
