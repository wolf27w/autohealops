# Agent 构建（交叉编译 + 镜像）
# 用法：
#   make -C agent build VERSION=0.2.7                 # 只交叉编译
#   make -C agent image VERSION=0.2.7                 # 本机有 docker：从源码构建（自包含，只需 Docker）
#   make -C agent image-local VERSION=0.2.7           # 本机有 docker + Go：先编译再打包（快）
#   make -C agent remote-image VERSION=0.2.7          # 本机没 docker：scp 到远端构建并推送（默认 192.168.6.149）
#   make -C agent push  VERSION=0.2.7
#
# 远端构建可覆盖：REMOTE=root@1.2.3.4 REMOTE_DIR=/tmp/agent-build

VERSION    ?= dev
IMAGE      ?= harbor.efreight.cn/ops/autohealops-agent
BUILD_DIR  := build
REMOTE     ?= root@192.168.6.149
REMOTE_DIR ?= /tmp/agent-build

.PHONY: build image image-local push remote-image clean

# 交叉编译 linux/amd64 静态二进制（源码在 k8s-executor/cmd/agent）
build:
	@mkdir -p $(BUILD_DIR)
	cd ../k8s-executor && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath \
		-ldflags "-s -w -X main.version=$(VERSION)" -o ../agent/$(BUILD_DIR)/agent ./cmd/agent
	@echo "✓ built agent/$(BUILD_DIR)/agent ($(VERSION))"

# 从源码构建（自包含：容器内编译，本机只需 Docker，无需 Go）
# 构建上下文必须是仓库根（agent 与 executor 共用 Go module）
image:
	docker build -f agent/Dockerfile --build-arg VERSION=$(VERSION) -t $(IMAGE):$(VERSION) .
	@echo "✓ image $(IMAGE):$(VERSION)（source build）"

# 先本机交叉编译，再用预编译二进制打包（需要本机 Go）
image-local: build
	cd $(BUILD_DIR) && docker build -f ../Dockerfile.prebuilt -t $(IMAGE):$(VERSION) .
	@echo "✓ image $(IMAGE):$(VERSION)（prebuilt binary）"

push:
	docker push $(IMAGE):$(VERSION)
	docker tag $(IMAGE):$(VERSION) $(IMAGE):latest
	docker push $(IMAGE):latest

# 本机没有 docker 时：交叉编译后 scp 到远端构建 + 推送（生产用这条）
remote-image: build
	ssh $(REMOTE) 'rm -rf $(REMOTE_DIR) && mkdir -p $(REMOTE_DIR)'
	scp -q $(BUILD_DIR)/agent $(REMOTE):$(REMOTE_DIR)/agent
	scp -q Dockerfile.prebuilt $(REMOTE):$(REMOTE_DIR)/Dockerfile
	ssh $(REMOTE) 'cd $(REMOTE_DIR) && docker build -f Dockerfile -t $(IMAGE):$(VERSION) . \
		&& docker tag $(IMAGE):$(VERSION) $(IMAGE):latest \
		&& docker push $(IMAGE):$(VERSION) \
		&& docker push $(IMAGE):latest'
	@echo "✓ 远端构建并推送完成：$(IMAGE):$(VERSION) / :latest"

clean:
	rm -rf $(BUILD_DIR)
