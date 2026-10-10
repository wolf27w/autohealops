#!/bin/bash
# dump 真实运行中某页弹窗的表单字段（验证 UI 结构）
# 用法: bash dump.sh <路由> <点击按钮文本>
set -e
ROOT="/Users/wolf/Documents/project/api-ops-platform"
TARGET="$1"
CLICK="$2"
TOKEN=$(cd "$ROOT/api-server" && ./.venv/bin/python -c "from app.core.security import create_jwt; print(create_jwt(1,'admin','admin'))")
cat > "$ROOT/frontend/public/__seed.html" <<'HTML'
<!doctype html><meta charset="utf-8"><script>
const p=new URLSearchParams(location.search);
localStorage.setItem('token',p.get('t')||'');
localStorage.setItem('user',JSON.stringify({id:1,username:'admin',role:'admin'}));
location.replace(p.get('to')||'/dashboard');
</script>
HTML
URL="http://localhost:5173/__seed.html?t=${TOKEN}&to=${TARGET}"
node "$ROOT/templates/tools/dump-modal.mjs" "$URL" "$CLICK"
rm -f "$ROOT/frontend/public/__seed.html"
