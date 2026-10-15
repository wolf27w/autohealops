#!/usr/bin/env bash
# API 契约（OpenAPI）导出 / 漂移检查（M6）
#
# 用法：
#   ./scripts/openapi.sh           # 导出契约到 docs/openapi.json（提交用）
#   ./scripts/openapi.sh --check   # 重新生成并与 docs/openapi.json 比对，不一致则非 0（CI 用）
#
# 目的：把接口契约固化进仓库；接口有变动却忘了更新契约 → CI 直接失败（杜绝漂移）。
# 前端可选据此生成类型（需自行安装，属可选）：
#   cd frontend && npx openapi-typescript ../docs/openapi.json -o src/generated/api-types.ts
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHK="${1:-}"
OUT="$ROOT/docs/openapi.json"

gen() {  # $1 = 输出路径
  ( cd "$ROOT/api-server" && PYTHONPATH=. ./.venv/bin/python - "$1" <<'PY'
import json, re, sys
from app.main import app
spec = app.openapi()
# 规范化 operationId（按 方法+路径 生成，避免 catch-all 路由的 operationId 冲突导致契约不稳定）
_METHODS = ("get", "post", "put", "delete", "patch", "head", "options")
for path, item in (spec.get("paths") or {}).items():
    for method, op in (item or {}).items():
        if method in _METHODS and isinstance(op, dict):
            key = re.sub(r"[^0-9A-Za-z]+", "_", f"{method}{path}").strip("_")
            op["operationId"] = key
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(spec, f, ensure_ascii=False, indent=2, sort_keys=True)
    f.write("\n")
print("routes:", len(spec.get("paths", {})))
PY
  )
}

if [ "$CHK" = "--check" ]; then
  TMP="$(mktemp -t openapi-check.XXXXXX.json)"
  gen "$TMP" >/dev/null
  if ! diff -q "$OUT" "$TMP" >/dev/null 2>&1; then
    echo "❌ OpenAPI 契约与代码不一致：docs/openapi.json 需更新（运行 ./scripts/openapi.sh）" >&2
    diff "$OUT" "$TMP" | head -30 >&2 || true
    rm -f "$TMP"; exit 1
  fi
  rm -f "$TMP"
  echo "✅ OpenAPI 契约与代码一致"
else
  gen "$OUT"
  echo "✅ 契约已导出：$OUT"
fi
