# AutoHealOps agent 镜像（反向隧道 + 集群内执行 + 告警检测下沉）
#
# 构建方式（推荐）：本机交叉编译 → 传到有 docker 的机器上 build（不依赖目标机 Go 环境）
#   CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath \
#     -ldflags "-s -w -X main.version=<版本>" -o /tmp/apiops-agent ./cmd/agent
#   scp /tmp/apiops-agent Dockerfile.agent root@host:/tmp/agent-build/
#   docker build -f Dockerfile.agent -t harbor.efreight.cn/ops/apiops-agent:<版本> .
#
# 说明：静态二进制；底包用 harbor 内已有的 debian 系 slim（带 CA，方便日后 mTLS）。
FROM harbor.efreight.cn/ops/python:3.10-slim

LABEL org.opencontainers.image.title="apiops-agent" \
      org.opencontainers.image.description="AutoHealOps cluster agent (reverse tunnel, in-cluster executor, watcher)"

RUN groupadd -g 1001 agent && useradd -u 1001 -g agent -m -s /bin/sh agent \
    && mkdir -p /app && chown -R agent:agent /app

WORKDIR /app
COPY --chown=agent:agent agent /app/agent
RUN chmod +x /app/agent

USER 1001
ENTRYPOINT ["/app/agent"]
