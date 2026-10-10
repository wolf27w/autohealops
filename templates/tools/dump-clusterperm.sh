#!/bin/bash
# dump 按集群授权页结构（验证改成按项目）
set -e
ROOT="/Users/wolf/Documents/project/api-ops-platform"
TOKEN=$(cd "$ROOT/api-server" && ./.venv/bin/python -c "from app.core.security import create_jwt; print(create_jwt(1,'admin','admin'))")
cat > "$ROOT/frontend/public/__seed.html" <<'HTML'
<!doctype html><meta charset="utf-8"><script>
const p=new URLSearchParams(location.search);
localStorage.setItem('token',p.get('t')||'');
localStorage.setItem('user',JSON.stringify({id:1,username:'admin',role:'admin'}));
location.replace(p.get('to')||'/dashboard');
</script>
HTML
URL="http://localhost:5173/__seed.html?t=${TOKEN}&to=/user-permission"
node "$ROOT/templates/tools/dump-clusterperm.mjs" "$URL"
rm -f "$ROOT/frontend/public/__seed.html"
