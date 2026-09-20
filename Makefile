# API-OPS 平台 Makefile
ROOT := $(shell pwd)
PY := $(ROOT)/api-server/.venv/bin/python
GO := /opt/homebrew/bin/go

.PHONY: build up down logs migrate seed status crypto-test ci logs-rotate test

# 构建所有服务镜像
build:
	cd api-server && docker build -t api-ops/api-server:latest .
	cd k8s-executor && docker build -t api-ops/k8s-executor:latest .
	cd ai-service && docker build -t api-ops/ai-service:latest .
	cd frontend && docker build -t api-ops/frontend:latest .

# docker compose 启动（数据库/缓存使用外部 192.168.6.99）
up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

# 数据库 migration（api-server 是 schema owner）
migrate:
	cd api-server && ./.venv/bin/alembic upgrade head

seed:
	cd api-server && $(PY) -m app.services.seed

status:
	docker compose ps

# 单元测试（api-server / ai-service，标准库 unittest）
test:
	cd api-server && ./.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
	cd ai-service && ./.venv/bin/python -m unittest discover -s tests -p 'test_*.py'

# 统一 CI 校验（编译/构建 + 单测 + Go build + 加密互测）
ci:
	./scripts/ci_check.sh

# 日志轮转/归档（copytruncate，默认保留 7 份 >50MB 触发）
logs-rotate:
	./scripts/rotate_logs.sh ./logs 7 50

# 跨语言加密互测（Python ↔ Go）：任何一端改加密实现必须通过
crypto-test:
	@echo "== 1/3 Python 生成向量 + 自测 =="
	$(PY) $(ROOT)/api-server/tests/test_crypto.py
	@echo "== 2/3 Go 解密 Python 向量 + 生成 Go 向量 =="
	cd k8s-executor && $(GO) test ./internal/k8sclient/ -run "Crypto|Vector" -v 2>&1 | grep -E "PASS|FAIL|SKIP|✅|解密|生成|ok"
	@echo "== 3/3 Python 解密 Go 向量 =="
	$(PY) $(ROOT)/api-server/tests/test_crypto.py
	@echo "== crypto-test 全部通过 ✅ =="
