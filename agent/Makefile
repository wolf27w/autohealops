# Agent 构建（交叉编译 + 镜像）
# 用法：make -C agent build VERSION=0.2.7

VERSION ?= dev
IMAGE   ?= harbor.efreight.cn/ops/autohealops-agent
BUILD_DIR := build

.PHONY: build image push clean

# 交叉编译 linux/amd64 静态二进制（源码在 k8s-executor/cmd/agent）
build:
	@mkdir -p $(BUILD_DIR)
	cd ../k8s-executor && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath \
		-ldflags "-s -w -X main.version=$(VERSION)" -o ../agent/$(BUILD_DIR)/agent ./cmd/agent
	@echo "✓ built agent/$(BUILD_DIR)/agent ($(VERSION))"

# 打镜像（需在有 docker 的机器上）
image: build
	cd $(BUILD_DIR) && docker build -f ../Dockerfile -t $(IMAGE):$(VERSION) .
	@echo "✓ image $(IMAGE):$(VERSION)"

push:
	docker push $(IMAGE):$(VERSION)

clean:
	rm -rf $(BUILD_DIR)
