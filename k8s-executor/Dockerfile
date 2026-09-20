# 多阶段构建：编译 → 精简运行镜像
FROM golang:1.22-alpine AS builder

WORKDIR /build
# 配置 Go 代理（国内镜像加速）
ENV GOPROXY=https://goproxy.cn,direct

COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 go build -o /executor ./cmd/executor/

# 运行镜像（精简 + grpc_health_probe）
FROM alpine:latest

RUN apk add --no-cache ca-certificates curl && \
    GRPC_HEALTH_PROBE_VERSION=v0.4.24 && \
    wget -qO/bin/grpc_health_probe https://github.com/grpc-ecosystem/grpc-health-probe/releases/download/${GRPC_HEALTH_PROBE_VERSION}/grpc_health_probe-linux-amd64 && \
    chmod +x /bin/grpc_health_probe

WORKDIR /app
COPY --from=builder /executor /app/executor

EXPOSE 9090

ENTRYPOINT ["/app/executor"]
