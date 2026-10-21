#!/bin/bash
set -e
cd /Users/wolf/Documents/project/autohealops/api-server
TOKEN=$(./.venv/bin/python -c "from app.core.security import create_jwt; print(create_jwt(1,'admin','admin'))")
B="http://localhost:5173"
D="/Users/wolf/Documents/project/autohealops/templates/before"
mkdir -p "$D"

# 登录页（无需 token）
node templates/tools/shot.mjs "$B/login" "$D/00-login.png" 1440 900 3500

shoot() { # $1=name $2=path $3=wait
  node templates/tools/shot.mjs "$B/__seed.html?t=$TOKEN&to=$2" "$D/$1.png" 1440 900 "${3:-5000}"
}

shoot 01-dashboard        "/dashboard"
shoot 02-clusters         "/clusters"
shoot 03-alert-handling   "/alert-handling"
shoot 04-tickets          "/tickets"
shoot 05-strategy         "/strategy"
shoot 06-fault-diagnosis  "/fault-diagnosis/app"
shoot 07-knowledge        "/knowledge"
shoot 08-settings         "/settings"

echo "=== done ==="
ls -la "$D"
