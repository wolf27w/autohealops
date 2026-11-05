# ============================================================
# AutoHealOps Agent 镜像 —— 自包含构建（只要有 Docker + 源码即可，无需本机 Go）
#
#   docker build -f agent/Dockerfile -t autohealops-agent:latest .
#   # 指定版本 / 换基础镜像（内网或国内环境）：
#   docker build -f agent/Dockerfile \
#     --build-arg VERSION=0.2.7 \
#     --build-arg GO_IMAGE=golang:1.22-alpine \
#     --build-arg BASE_IMAGE=debian:bookworm-slim \
#     --build-arg GOPROXY=https://goproxy.cn,direct \
#     -t autohealops-agent:0.2.7 .
#
# 注意：构建上下文是**仓库根目录**（因为 agent 与 executor 共用 Go module / internal 包）。
# ============================================================
ARG GO_IMAGE=golang:1.26-alpine
ARG BASE_IMAGE=debian:bookworm-slim

# ---------- 构建阶段：从源码编译 ----------
FROM ${GO_IMAGE} AS builder

# 国内网络可换：https://goproxy.cn,direct
ARG GOPROXY=https://goproxy.cn,direct
ENV GOPROXY=${GOPROXY}
# 允许自动拉取 go.mod 要求的工具链版本（go.mod 里可能高于基础镜像）
ENV GOTOOLCHAIN=auto

WORKDIR /src
# 先只拷依赖清单，命中缓存
COPY k8s-executor/go.mod k8s-executor/go.sum ./
RUN go mod download

# 再拷源码（agent 与 executor 同模块，必须整份拷贝）
COPY k8s-executor/ ./

ARG VERSION=dev
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath \
      -ldflags "-s -w -X main.version=${VERSION}" \
      -o /out/agent ./cmd/agent

# ---------- 运行阶段 ----------
FROM ${BASE_IMAGE}

LABEL org.opencontainers.image.title="autohealops-agent" \
      org.opencontainers.image.description="AutoHealOps cluster agent (reverse tunnel, in-cluster executor, watcher, db bridge)"

RUN (command -v groupadd >/dev/null && groupadd -g 1001 agent || true) \
    && (command -v useradd >/dev/null && useradd -u 1001 -g agent -m -s /bin/sh agent || true) \
    && mkdir -p /app && chown -R 1001:1001 /app

WORKDIR /app
COPY --from=builder --chown=1001:1001 /out/agent /app/agent
RUN chmod +x /app/agent

USER 1001
ENTRYPOINT ["/app/agent"]
