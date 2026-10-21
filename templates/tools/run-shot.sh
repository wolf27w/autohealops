#!/bin/bash
set -e
TARGET="$1"   # e.g. /dashboard
OUT="$2"
WAIT="${3:-4500}"
cd /Users/wolf/Documents/project/autohealops/api-server
TOKEN=$(./.venv/bin/python -c "from app.core.security import create_jwt; print(create_jwt(1,'admin','admin'))")
URL="http://localhost:5173/__seed.html?t=${TOKEN}&to=${TARGET}"
mkdir -p "$(dirname "$OUT")"
node templates/tools/shot.mjs "$URL" "$OUT" 1440 900 "$WAIT"
