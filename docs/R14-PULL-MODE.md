# R14 双接入：pull 模式（平台拉取）

> 2026-11-11 开发中。目标：agent 无法主动出网连平台（或平台需要按需调用）时，改由平台直连 agent。

## 1. 两种模式

| 模式 | 方向 | 用法 | 适用 |
|---|---|---|---|
| `push`（默认，现状） | agent → 平台 gateway:9095 反向隧道 | `-mode=push -platform=x:9095 -token=...` | 集群可出网 |
| `pull`（新增） | 平台 → agent:9100 直连 gRPC | `-mode=pull -listen=:9100 -pull-token=...` | 集群不出网、平台能进集群 |

`push` 行为完全不变（默认值），已有部署零改动。

## 2. agent 侧（已完成，2026-11-11）

新增参数：

```
-mode=push|pull         默认 push
-listen=:9100           pull 监听地址
-pull-token=xxx         访问令牌（平台侧配置同一个值；空=不校验，仅限内网调试）
-pull-cert/-pull-key    TLS 服务端证书（都给才启用 TLS）
-pull-ca                客户端 CA（给了就要求并校验客户端证书 = mTLS）
```

实现：`agentbridge.Bridge.ServePull()` 在进程内复用**同一个** `ExecutorService` 实现
（与隧道模式同一份代码，33 个 unary RPC 自动可用），额外挂：
- unary/stream 鉴权拦截器（`authorization: Bearer <token>`）
- 可选 TLS / mTLS

**pull 模式的功能边界**：agent 侧的推送型能力依赖隧道，pull 模式下**不启用**
（watcher 告警上报、Pod 指标/事件/配额定时上报）。平台侧改为按需拉取
（`QueryDatabaseByKey` / `ListPods` 等仍可用，因为走的是同一套 RPC）。

单元测试：`internal/agentbridge/pull_test.go` —— 无令牌/错令牌 → `Unauthenticated`，
正确令牌 → 直达业务逻辑。

## 3. 平台侧（待开发）

1. `clusters` 加字段：`agent_mode`(push/pull)、`agent_pull_url`、`agent_pull_token_encrypted`（AES-256-GCM）
2. `executor_client._TunnelAwareStub` 增加第三分支：集群 `agent_mode=pull` → 建到 `agent_pull_url` 的
   gRPC 通道（带 `authorization: Bearer` 元数据），不再回退本机 executor
3. 集群管理页：接入方式选 agent 时可选 push/pull，pull 填地址+令牌；「测试连接」按钮走 HealthCheck
4. 流式方法（ExecutePlan/BatchExecute/ExecPodStream）：pull 模式下直接走该通道（原生流式，不需要隧道帧）

## 4. 安全

- token 与 agent 侧 `-pull-token` 一致，平台加密存储，接口只回 `has_pull_token`
- 生产环境建议同时开 TLS + mTLS（`-pull-cert/-pull-key/-pull-ca`）
- pull 端口 9100 只对平台出口 IP 开放（NetworkPolicy / 安全组）
