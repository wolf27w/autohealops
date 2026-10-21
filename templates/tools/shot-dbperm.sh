#!/bin/bash
# 进按数据库授权 tab，选项目后截图
set -e
ROOT="/Users/wolf/Documents/project/autohealops"
TOKEN=*** "$ROOT/api-server" && ./.venv/bin/python -c "from app.core.security import create_jwt; print(create_jwt(1,'admin','admin'))")
cat > "$ROOT/frontend/public/__seed.html" <<'HTML'
<!doctype html><meta charset="utf-8"><script>
const p=new URLSearchParams(location.search);
localStorage.setItem('token',p.get('t')||'');
localStorage.setItem('user',JSON.stringify({id:1,username:'admin',role:'admin'}));
location.replace(p.get('to')||'/dashboard');
</script>
HTML
URL="http://localhost:5173/__seed.html?t=${TOKEN}&to=/user-permission"
node "$ROOT/templates/tools/shot-dbperm.mjs" "$URL" "/tmp/shot-dbperm.png"
rm -f "$ROOT/frontend/public/__seed.html"
