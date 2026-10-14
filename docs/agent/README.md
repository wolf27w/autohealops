# R12 · Agent 主动外连接入模式（单 Deployment + gRPC 反向隧道）

## 何时用
平台**直连不到**目标集群 apiserver（NAT/受限网络）时，用它替代「平台主动连集群」的 pull 模式。

## 架构
- 集群内以 **单个 Deployment** 部署 `agent`（`k8s-executor/cmd/agent`）。
- agent 用 in-cluster 身份 + RBAC，**主动外连**平台 Agent 网关（gRPC 双向流 `AgentService.Tunnel`）。
- 平台把 executor 请求（`method` = 对应 `ExecutorService` 的 rpc 名 + 序列化 payload）经隧道下发；
  agent 在集群内执行（复用 executor 内核）后回传响应。
- **路由判定**：某集群有 agent 在线 → 该集群的请求走隧道；否则走原来的直连（零影响、可灰度、随时回退）。

## 平台侧
- 网关监听 `AGENT_GATEWAY_ADDR`（默认 `0.0.0.0:9095`），启动即生效（api-server lifespan）。
- 接入令牌：环境变量 `AGENT_ACCESS_TOKEN`（为空则不校验，仅建议内网/测试）。
- 已接入的方法：`ExecuteCommand`、`GetPodLogs`（可扩展，见 `agent_gateway.handleRequest` / `cmd/agent`）。

## 部署
1. 平台放通 `9095`（对外）；设置 `AGENT_ACCESS_TOKEN`。
2. 集群内 `kubectl apply -f agent-deploy.yaml`（替换镜像/网关地址/集群ID/令牌）。
3. 平台侧对该集群的「运行命令/日志」等即自动改走隧道（其余方法暂仍直连）。

## 构建
```
cd k8s-executor && go build -o bin/agent ./cmd/agent
# 容器镜像：基于 k8s-executor/Dockerfile，入口改为 /bin/agent
```
> 说明：agent 内置最小 whitelist（可用 `-whitelist` 覆盖）；命令安全检查/白名单与 executor 一致。
