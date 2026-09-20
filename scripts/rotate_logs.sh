#!/usr/bin/env bash
# 日志轮转/归档（A2）
# 用法：./scripts/rotate_logs.sh [目录] [保留份数] [单文件阈值MB]
#   ./scripts/rotate_logs.sh                 # 默认轮转 ./logs，保留 7 份，>50MB 触发
#   ./scripts/rotate_logs.sh ./logs 14 100   # 保留 14 份，阈值 100MB
# 说明：copytruncate 方式（先拷贝再清空），不影响正在写日志的进程（uvicorn/executor/vite）。
set -euo pipefail

DIR="${1:-./logs}"
KEEP="${2:-7}"
THRESHOLD_MB="${3:-50}"

mkdir -p "$DIR/archive"
STAMP="$(date +%Y%m%d-%H%M%S)"
THRESHOLD=$(( THRESHOLD_MB * 1024 * 1024 ))

shopt -s nullglob
rotated=0
for f in "$DIR"/*.log; do
  [ -f "$f" ] || continue
  size=$(stat -f%z "$f" 2>/dev/null || stat -c%s "$f")
  if [ "$size" -lt "$THRESHOLD" ]; then
    continue
  fi
  base="$(basename "$f" .log)"
  target="$DIR/archive/${base}-${STAMP}.log"
  cp "$f" "$target"
  : > "$f"                                  # 清空（copytruncate）
  gzip -f "$target"
  rotated=$((rotated + 1))
  echo "rotated: $f (${size} bytes) -> ${target}.gz"
done

# 清理超出保留份数的归档（只动 $DIR/archive 下的文件，避免 glob 无匹配时误操作）
ARCHIVE="$DIR/archive"
if [ -d "$ARCHIVE" ]; then
  for base in $(ls -1 "$ARCHIVE" 2>/dev/null | sed -E 's/^([A-Za-z0-9._-]+)-[0-9]{8}-[0-9]{6}\.log\.gz$/\1/' | sort -u); do
    [ -n "$base" ] || continue
    files=()
    while IFS= read -r line; do
      [ -n "$line" ] && files+=("$line")
    done < <(ls -1t "$ARCHIVE" 2>/dev/null | grep -E "^${base}-[0-9]{8}-[0-9]{6}\.log\.gz$" || true)
    count=${#files[@]}
    if [ "$count" -gt "$KEEP" ]; then
      for old in "${files[@]:$KEEP}"; do
        rm -f "$ARCHIVE/$old"
        echo "pruned: $ARCHIVE/$old"
      done
    fi
  done
fi

echo "done: rotated=$rotated keep=$KEEP threshold=${THRESHOLD_MB}MB dir=$DIR"
