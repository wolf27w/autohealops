#!/bin/bash
# 截图真实运行中的后台（可点按钮后截图）
# 用法: bash shoot.sh <路由> <输出png> [点击按钮文本]
set -e
ROOT="/Users/wolf/Documents/project/api-ops-platform"
TARGET="$1"
OUT="$2"
CLICK="${3:-}"

# 生成 admin 令牌
TOKEN=$(cd "$ROOT/api-server" && ./.venv/bin/python -c "from app.core.security import create_jwt; print(create_jwt(1,'admin','admin'))")

# 临时种子页（注入令牌后跳转）
cat > "$ROOT/frontend/public/__seed.html" <<'HTML'
<!doctype html><meta charset="utf-8"><script>
const p=new URLSearchParams(location.search);
localStorage.setItem('token',p.get('t')||'');
localStorage.setItem('user',JSON.stringify({id:1,username:'admin',role:'admin'}));
location.replace(p.get('to')||'/dashboard');
</script>
HTML

URL="http://localhost:5173/__seed.html?t=${TOKEN}&to=${TARGET}"
mkdir -p "$(dirname "$OUT")"
if [ -n "$CLICK" ]; then
  node "$ROOT/templates/tools/shot-click.mjs" "$URL" "$OUT" "$CLICK" 1500 1440 900
else
  node "$ROOT/templates/tools/shot.mjs" "$URL" "$OUT" 1440 900 4500
fi
rm -f "$ROOT/frontend/public/__seed.html"
