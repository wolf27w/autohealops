#!/usr/bin/env bash
# 统一 CI 校验（A2）：编译/构建检查，失败即退出非 0
# 用法：./scripts/ci_check.sh [--with-frontend-build]
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

echo "== 0/7 proto 单一来源检查（api-server 与 k8s-executor 的 executor.proto 必须一致）=="
diff -q k8s-executor/proto/executor.proto api-server/app/proto/executor.proto >/dev/null   || { echo "FAIL: 两份 executor.proto 不一致（改协议请同步后再重新生成 Go/Python stub）"; exit 1; }
echo "  ✓ proto 一致"

echo "== 1/7 api-server py_compile =="
"$ROOT/api-server/.venv/bin/python" -m compileall -q api-server/app api-server/alembic || { echo "FAIL api-server"; exit 1; }

echo "== 2/7 ai-service py_compile =="
"$ROOT/ai-service/.venv/bin/python" -m compileall -q ai-service/app || { echo "FAIL ai-service"; exit 1; }

echo "== 3/7 frontend tsc =="
( cd frontend && npx tsc -b --noEmit ) || { echo "FAIL frontend tsc"; exit 1; }

echo "== 4/7 k8s-executor go build =="
( cd k8s-executor && go build -o /tmp/executor-ci ./cmd/executor ) || { echo "FAIL go build"; exit 1; }

echo "== 5/7 单元测试（api-server / ai-service）=="
( cd api-server && ./.venv/bin/python -m unittest discover -s tests -p 'test_*.py' ) || { echo "FAIL api-server unit tests"; exit 1; }
( cd ai-service && ./.venv/bin/python -m unittest discover -s tests -p 'test_*.py' ) || { echo "FAIL ai-service unit tests"; exit 1; }

echo "== 6/7 跨语言加密互测（如存在） =="
if [ -f api-server/tests/test_crypto.py ]; then
  make crypto-test || { echo "FAIL crypto-test"; exit 1; }
else
  echo "skip (no crypto test)"
fi

echo "== 7/7 OpenAPI 契约漂移检查 =="
"$ROOT/scripts/openapi.sh" --check || { echo "FAIL OpenAPI drift"; exit 1; }

if [ "${1:-}" = "--with-frontend-build" ]; then
  echo "== extra: frontend production build =="
  ( cd frontend && npm run build ) || { echo "FAIL frontend build"; exit 1; }
fi

echo "✅ CI check passed"
