# R2 平台多副本（api-server HA）设计方案

> 2026-11-11 整理。当前状态：`k8s/deployments.yaml` 里 api-server 固定 `replicas: 1`，
> 原因写在注释里 —— agent 反向隧道网关的连接注册表 `agent_gateway._agents` 在**进程内**，
> 多副本时 agent 只挂在其中一个 Pod 上，其余副本转发不到。

## 1. 目标

- api-server 支持 `replicas >= 2`（滚动更新不中断 API、HPA 可扩）
- agent 隧道能力不退化（Pod 详情/命名空间/日志/诊断照常）
- 不做「无状态化大改」，控制风险：先把**有状态部分**单独拆出去

## 2. 现状依赖分析

| 组件 | 位置 | 状态 |
|---|---|---|
| agent 隧道连接表 | `api-server/app/agent_gateway.py::_agents` | 进程内 dict，**有状态** |
| 9095 gRPC gateway | api-server 进程内 | 每副本都会监听（SO_REUSEPORT 会抢连接） |
| agent 证书签发（CA） | api-server + PVC `autohealops-agent-ca` | RWO 卷，只能挂一个 Pod |
| 隧道调用入口 | `executor_client._TunnelAwareStub` → `agent_gateway.call()` | 同进程直调 |

## 3. 方案（分两阶段）

### 阶段 1：网关独立 Deployment（推荐先做，2~3d）

1. 新服务 `agent-gateway`（复用 executor 镜像或独立二进制），职责：
   - 监听 9095，处理 agent 的 `Tunnel` 双向流，维护连接表
   - 暴露内部 gRPC `TunnelCall(cluster_id, method, payload) → (payload)` + `StreamCall` + `IsConnected`
   - 挂 `autohealops-agent-ca` PVC（CA 私钥只在这个服务里），证书签发/续签也搬过来
2. api-server：`_TunnelAwareStub` 改为调用 `agent-gateway:9096` 的内部 gRPC（不再有进程内表）
3. `deployments.yaml`：
   - `agent-gateway`：`replicas: 1`（**这是新单点，但只影响隧道，不影响 API**），`strategy: Recreate`（RWO 卷）
   - `api-server`：`replicas: 2` + HPA(2~5) + PDB
4. 验收：2 副本下滚动重启 API 无 5xx；agent 掉线/重连仍正常；诊断链路全通

### 阶段 2：网关多副本（可选，1~2d）

连接仍在各网关 Pod 内，需要**共享注册表**才能多副本：
- Redis 存 `agentgw:cluster:<id> → gatewayPod 地址`（TTL 心跳刷新）
- api-server 查 Redis 定位网关 Pod，直接转发（或网关之间互相转发）
- 网关 `replicas: 2` + 反亲和；CA 改为 Secret（提前生成并持久化，避免多副本各签一份）

## 4. 其他必须一起改的点

- **CA 持久化**：`autohealops-agent-ca` PVC 是 RWO → 阶段 1 只能单网关 Pod；阶段 2 要换成 Secret（离线生成、只读写一次）
- **心跳/状态写库**：`clusters.agent_status/agent_last_seen_at` 目前由 api-server 写；拆网关后由网关写，api-server 只读
- **告警 watcher 上报**：走隧道 → 网关收到后转调 api-server 内部接口（或直接写库，避免多一层）
- **限流/鉴权**：内部 gRPC 走 mTLS 或集群内 NetworkPolicy 白名单

## 5. 风险与回滚

- 风险：拆网关后隧道调用多一跳（+1~2ms），agent 重连期间调用失败率上升（现有 `unavailable` 兜底不变）
- 回滚：保留 `agent_gateway` 进程内实现（开关 `AGENT_GATEWAY_MODE=inproc|remote`），出问题切回 inproc + 单副本
