# 06 Agent 通道审计报告（AutoHealOps）

- 审计对象：集群内 `agent`（`k8s-executor/cmd/agent`）↔ 平台 `agent-gateway`（`api-server/app/agent_gateway.py`）的接入、认证、任务下发与执行链路
- 审计方式：**只读**源码审计（未修改任何文件、未装依赖、未连任何环境、未做 git 写操作；仅新建本报告）
- 结论口径：所有结论带 `文件:行`；无法从源码确认的写「暂时无法确认」
- 覆盖文件见第 14 节

---

## 1. 结论摘要

1. **协议是 gRPC 双向流（HTTP/2），不是 WebSocket、不是轮询**：`rpc Tunnel(stream AgentFrame) returns (stream AgentFrame)`（`k8s-executor/proto/agent.proto:11-13`，Python 侧同定义 `api-server/app/proto/agent.proto:12`）。一个 `AgentFrame` 用 `type` 字段承载 7 种帧语义（register/request/response/ping/pong/event + 4 种 stream 帧），`method` + `payload` 是两个自由字符串/字节字段（`agent.proto:15-29`）。
2. **注册 = 建流后的第一帧，只校验「接入令牌的 sha256」**：`sha256(token) == clusters.agent_token_hash`，通过即把 `agent_status='connected'`、`agent_last_seen_at=now()` 写回 `clusters` 表（`agent_gateway.py:44-57`）。**mTLS 客户端证书只做链校验，不与 `cluster_id` 绑定**（`agent_gateway.py:396-398` 只设 `require_client_auth=True`，全函数无 CN 比对）。
3. **CA 私钥明文落盘在 `data/agent-ca/ca.key`（0600）**，Web 侧无任何「签发/吊销」管理面；客户端证书 1825 天有效、**无 CRL/OCSP、无到期提醒**；`revoke` 只清 token 哈希、既不回收证书也不踢在线连接（`clusters.py:648-659`）。**每拉一次 `install.yaml` 就新签一张证书**（`clusters.py:356`），证书数量只增不减。
4. **心跳是「gRPC keepalive」，不是应用层 ping/pong**：agent 侧 `keepalive{30s/10s, PermitWithoutStream}`（`main.go:195-199`），网关侧对称配置（`agent_gateway.py:361-370`）。协议里定义的 `ping`/`pong` 在平台侧**没有任何发送点**（`agent_gateway.py:318-319` 只处理 pong，注释点明），agent 侧应答分支是死代码（`main.go:352-353`）。`agent_last_seen_at` 只在注册时写一次，**不能当「最后心跳」用**。
5. **任务下发是「per-connection 无界队列 + threading.Event 等结果」**：`call()` 生成 `req_id` → 塞 `a.out`(queue.Queue) → 出站循环逐帧 `yield`（`agent_gateway.py:135-153,331-337`）；agent 侧 `case "request"` **在收包主循环里同步执行**（`main.go:362-365` → `handleRequest` → `bridge.Call` → 本机 loopback executor），因此**单个 agent 同一时刻只能跑 1 个 unary 请求**，一个 30s 慢调用会把该集群后续所有请求排队到超时。
6. **无取消、无幂等、无去重**：平台超时只是 `pending.pop()` 后抛 `TimeoutError`（`agent_gateway.py:147-150`），**不向 agent 发 cancel**，agent 会继续把 `DeleteResource`/`ScaleWorkload`/`DropDatabase` 执行完；同一操作重试就是二次执行。全链路没有任何幂等键（`req_id` 仅用于关联响应）。
7. **平台侧授权在 API 层（`user_permission_service.check_cluster_permission`），agent/executor 层零校验**：`resources.py` 78 处 `_deny_cluster` 是真正的鉴权点；agent 侧 `Bridge` 明确「不论请求里的 cluster_id 是多少，都返回本集群」（`bridge.go:108-113`），executor gRPC 只读 `trace-id`、不读任何身份（`grpc/server.go:60-79`）。**任何能直连 9090 / 9096 / 9100 的主体都绕过全部授权。**
8. **「云原生 RBAC / impersonation」运行链未接通**：`rbac_service.build_impersonation_groups()`（`rbac_service.py:40-56`）全仓无调用方，`k8sclient.Manager.ImpersonatedClient()`（`manager.go:161-175`）全仓无调用方 → 所有 k8s 操作实际以 agent 的 ServiceAccount（`apiGroups:* / resources:* / verbs: 全套 + impersonate`，`clusters.py:441-467`）执行，平台 UI 上「RBAC 已接入（impersonation 启用）」是**误导性状态**。
9. **3 个 P0 级问题**：①内部网关 9096 无鉴权，可匿名签发 CA 证书 + 向任意在线 agent 下发任意 method；②接入令牌以明文写在 agent Deployment 的 args 里（`clusters.py:427`），集群内任何能 `get pods` 的人都能拿到 → 配合匿名 `install.yaml` 换到 CA 签发的证书 → **顶替真实 agent 注册、劫持该集群的隧道**；③`DropDatabase`/`ImportSqlRaw`/`DeleteResource`/`ExecPodStream` 这类破坏性 method 可通过上述两条路径任意触发，且无平台侧二次确认。

---

## 2. 协议与拓扑（问题 1）

### 2.1 真实协议

```proto
// k8s-executor/proto/agent.proto:11-13
service AgentService {
  rpc Tunnel(stream AgentFrame) returns (stream AgentFrame);
}
```

```proto
// k8s-executor/proto/agent.proto:15-29（节选）
message AgentFrame {
  string type = 1;        // register | register_ack | request | response | ping | pong | error
  int64 cluster_id = 2;   // 注册：agent 归属的集群 id
  string token = 3;       // 注册：接入令牌（平台校验）
  string req_id = 4;      // 请求/响应关联 id
  string method = 5;      // 隧道方法名（如 ExecuteCommand / GetPodLogs）
  bytes payload = 6;      // 该方法请求或响应的序列化 protobuf
  string version = 8;
  repeated string capabilities = 9;
}
```

- 实际用到的 `type` 值比注释多 4 个：`stream_open` / `stream_msg` / `stream_end` / `stream_error`（`agent_gateway.py:169,196,202,206`；`main.go:367,429,439`），注释未同步。
- 服务端实现是**同步 grpc server + 后台线程池**：`grpc.server(ThreadPoolExecutor(max_workers=20))`（`agent_gateway.py:361-362`），`Tunnel` 里为每个连接起一个 daemon reader 线程读上行帧（`agent_gateway.py:323`），主线程往 `a.out` 取帧 yield 下去（`agent_gateway.py:331-337`）。
- 客户端：`grpc.NewClient(*platform, dialOpts...)` + `client.Tunnel(ctx)`（`main.go:222-224,242-243`）。
- **云侧只有一条服务、一个 RPC**：`Tunnel` 本身不区分读/写，请求与响应共用同一条流——这是后面「同一 agent 串行」与「无背压」的结构性根源。

### 2.2 两种接入形态（push / pull）

| 形态 | 谁发起连接 | 证据 | 能力差异 |
|---|---|---|---|
| push（默认） | agent → 平台 `:9095` mTLS 长连接 | `main.go:222-238`，`agent_gateway.py:351-412` | 全量：unary + 服务端流 + 双向流 + 上行 event（指标/事件/告警/主机/DB） |
| pull | 平台 → agent `:9100`（gRPC 明文/可选 TLS） | `bridge.go:164-200`，`main.go:207-220` | **无** watcher/指标/事件/配额推送（注释与代码一致：pull 分支 `return`，推送协程只在 push 分支启动） |

pull 模式平台侧 channel 是 `grpc.insecure_channel(url)`（`executor_client.py:399-405`），令牌走 metadata `authorization: Bearer <token>`（`executor_client.py:409-410`）→ **token 明文过网**（详见 AGT-10）。

### 2.3 隧道里的「隐式协议」：12 个 JSON 旁路方法

`handleRequest` 在 `bridge.Call` 之前用字符串 switch 拦了 12 个方法，它们**不在 `executor.proto` 里**，payload 是 JSON 不是 protobuf：

`PromRange`、`DbHealth`、`DbObjectDDL`、`DbExport`、`DbImport`、`CreateDatabase`、`DropDatabase`、`ImportSqlRaw`、`Backup`、`DbSchemaByKey`、`QueryDatabaseByKey`、`QueryDatabase`（`main.go:460-583`）。

平台侧调用方以裸字符串引用（例如 `db_connections.py` 走 `_gateway_call(cid, "DropDatabase", ...)`），名称改一端即运行时报 `unknown method`。能力表 `_FEATURE_REQUIRED` 只登记了 `hpa` / `log_time_range` / `rollback`（`executor_client.py:161-179`），**破坏性方法没有任何能力门禁**。

---

## 3. 注册流程与身份（问题 2）

| 环节 | 实现 | 证据 |
|---|---|---|
| agent 发起 | 建流后第一帧 `{type:"register", cluster_id, token, version, capabilities}` | `main.go:261` |
| 平台校验 | `sha256(token)` 与 `clusters.agent_token_hash` 比对；空哈希视为已吊销 | `agent_gateway.py:47-49` |
| 校验通过 | `agent_status="connected"`、`agent_last_seen_at=now()`、`agent_version`、`agent_capabilities`(JSON) 落 `clusters` | `agent_gateway.py:50-55` |
| 校验失败 | `context.abort(UNAUTHENTICATED, err)` 直接断流，**不做失败计数/封禁/告警升级** | `agent_gateway.py:270-273` |
| 注册成功 | 回 `register_ack`，连接放入**进程内** `_agents[cluster_id]` | `agent_gateway.py:274-281` |
| 注册后动作 | 立即 `_push_config()` 下发告警策略 JSON（8s 超时，失败仅 warn） | `agent_gateway.py:225-240` |
| 注册后动作 | 若 `rbac_mode=="off"` → 异步 `rbac_service.setup_cluster_rbac()` | `agent_gateway.py:56-59,65-72` |

- **注册信息存哪**：全部落在 `clusters` 单表（`agent_status`/`agent_last_seen_at`/`agent_version`/`agent_capabilities`/`agent_namespace`/`agent_image`）；**连接对象本身只在 api-server（或 agent-gateway）进程内存里**（`agent_gateway.py:26` `_agents: dict[int, _AgentConn]`），无 Redis/DB 记录 → 多副本/重启即全丢。
- token 生成：`secrets.token_urlsafe(199)`（266 字符，`clusters.py:200-206`），强度足够；只在 `clusters.agent_token_hash` 存 sha256，**明文 token 只在创建响应与安装命令里出现一次**（`clusters.py:254-256`）——但随后被写进 Deployment args（`clusters.py:427`），等于永久留在集群里（AGT-02）。
- 注册校验**不看证书主体**：`_verify_and_mark` 只接收 `cluster_id/token/version/capabilities`（`agent_gateway.py:32`），TLS 层只做 CA 链校验（`agent_gateway.py:396-398`）→ 一张「集群 A 的证书」可以监听/注册「集群 B」（只要知道 B 的 token）。

---

## 4. 身份认证与 mTLS / PKI（问题 3）

### 4.1 事实清单

| 项 | 事实 | 证据 |
|---|---|---|
| CA 算法/年限 | ECDSA P-256 自签，`BasicConstraints(ca=True, path_length=0)`，10 年 | `agent_pki.py:40-78` |
| CA 私钥位置 | `data/agent-ca/ca.key`（相对仓库根解析），`chmod 0600`，**无口令、无 KMS、无加密** | `agent_pki.py:19-27,34-37,73-74`；`config.py:93` |
| K8s 形态 | 挂 RWO PVC `autohealops-agent-ca` 到 `/var/lib/autohealops/agent-ca` | `k8s/deployments.yaml:113-115,136-140`；`k8s/pvc-agent-ca.yaml` |
| 网关服务端证书 | `ensure_server_cert(hosts)`，SAN 取自 `AGENT_GATEWAY_PUBLIC_ADDR` + `PLATFORM_PUBLIC_URL` host，730 天 | `agent_gateway.py:375-390`；`agent_pki.py:81-131` |
| 客户端证书 | `CN=autohealops-agent-<cluster_id>`，`EKU=CLIENT_AUTH`，**1825 天** | `agent_pki.py:140-169` |
| mTLS 强制 | `ssl_server_credentials(..., root_certificates=ca, require_client_auth=True)` | `agent_gateway.py:396-398` |
| 明文降级 | `AGENT_TLS_ENABLED=false` → `add_insecure_port`，只打 warning | `agent_gateway.py:405-411`；`config.py:89` |
| 签发入口 A（inproc） | `GET /api/v1/clusters/{id}/agent/install.yaml?token=...` → 现场签发并内联进 Secret，**接口本身不要求登录** | `clusters.py:259-292,340-360` |
| 签发入口 B（remote） | 内部 gRPC `:9096 IssueClientCert(cluster_id, days)`，**无任何鉴权** | `agent_gateway_service.py:65-75` |
| 吊销 | `POST /clusters/{id}/agent/revoke` → `agent_token_hash=""`、`agent_status="revoked"`；**不动证书、不踢连接** | `clusters.py:648-659` |
| 到期/轮换 | 无 CRL、无 OCSP、无证书到期巡检、无轮换流程；每次拉 install.yaml 重新签一张 | `agent_pki.py` 全文无吊销相关代码；`clusters.py:356` |

### 4.2 三个直接后果

1. **证书 ≠ 集群身份**：mTLS 只证明「持有 CA 签发的任意一张客户端证书」。真实身份判定 100% 依赖 register 帧里的 token。
2. **吊销不闭环**：`revoke` 后，
   - 已建立的隧道**不会被断开**（`agent_gateway.py` 无任何按 cluster 主动踢线的入口，只有 `stop_server()` 全量踢，`agent_gateway.py:426-434`）；
   - 已下发的证书**仍然有效**（下次只要 token 再泄露/未清库即可复用；或直接走 AGT-01 的 9096 通道签发新证书）。
3. **签发面过大**：`install.yaml` 用「query string 里的 token」当唯一凭证，该 URL 会进 nginx access log、浏览器历史、CI 日志（`clusters.py:254` `install_command` 直接把 token 拼进命令行）。

---

## 5. 心跳与在线判定（问题 4）

| 维度 | 协议定义 | 实际实现 |
|---|---|---|
| 应用层心跳 | `type` 注释含 `ping`/`pong`（`agent.proto:16`） | 平台**从不发 ping**（`agent_gateway.py` 无 `type="ping"` 的构造）；只处理收到的 `pong` 且 `pass`（`agent_gateway.py:318-319`）；agent 侧「收 ping 回 pong」（`main.go:352-353`）是死代码 |
| 传输层保活 | — | gRPC keepalive：agent `Time=30s, Timeout=10s, PermitWithoutStream=true`（`main.go:195-199`）；网关 `keepalive_time_ms=30000 / timeout=10000 / permit_without_calls=1 / max_pings_without_data=0 / min_time_between_pings_ms=10000`（`agent_gateway.py:361-370`） |
| `last_seen` 更新 | — | **只在注册那一刻写一次**（`agent_gateway.py:51`）；之后无任何更新点（全仓 grep `agent_last_seen_at` 仅此一处写） |
| 掉线判定 | — | ①`is_connected()` 看 `_agents` 里对象 `alive`（`agent_gateway.py:106-110`）；②隧道 `finally` 里 `alive=False` + 从字典摘除 + `_mark_disconnected()`（`agent_gateway.py:338-348`）；③后台 `_agent_cluster_health_loop` 每 60s 经隧道 `HealthCheck`，**连续 2 次失败**才写 `status=unreachable`（`main.py:181-231`，判定在 212-217） |
| 半开连接 | 代码注释记录了历史故障 | 2026-11-23 加 keepalive 前「平台显示已接入但所有 RPC 不通，只能重启 agent Pod」（`main.go:189-194` 注释） |

**判定口径问题**：`_mark_disconnected` 会把 `agent_status` 改成 `disconnected`，但**不写 `agent_last_seen_at`**，也不记录「最后断线时间」→ 前端「最后在线时间」字段语义模糊（实际是最后注册时间，见 ARCH-14）。另外健康检查只筛 `agent_status=="connected"`（`main.py:200`），一旦被标成 `disconnected` 就再也不会被健康检查探活，只能等 agent 自己重连 —— 若 agent 侧重连逻辑卡死（例如 keepalive 未触发），**该集群会永久停留在 disconnected 且无人探活**。

---

## 6. 任务下发→执行→回传全链路（问题 5）

### 6.1 函数级调用链（unary，以 `DeleteResource` 为例）

```
前端 DELETE /api/v1/resources/clusters/{cid}/workloads/{kind}/{ns}/{name}
  └─ resources.py:1138 delete_workload
      ├─ _deny_cluster(user, cid, ns, "operate")            # 平台唯一鉴权点（resources.py:31-35）
      └─ executor_client.acall(executor_client.delete_workload, ...)
          └─ _grpc_semaphore(8) + asyncio.to_thread         # executor_client.py:24-32
              └─ _TunnelAwareStub.__getattr__("DeleteResource")._call(req)
                  ├─ _gateway_is_connected(cid)             # inproc: agent_gateway.is_connected
                  ├─ _gateway_call(cid, "DeleteResource", req.SerializeToString(), timeout=30)
                  │    └─ agent_gateway.call()              # agent_gateway.py:135-153
                  │        ├─ req_id = uuid4().hex
                  │        ├─ a.pending[req_id] = {event, payload, error}
                  │        ├─ a.out.put(AgentFrame(type="request", req_id, method, payload))
                  │        └─ slot["event"].wait(30)  ← 阻塞线程
                  └─ resp_cls.FromString(slot["payload"])
agent 侧：
  main.go:341 stream.Recv() → case "request"（main.go:362-365）
    └─ handleRequest(...)                                    # main.go:455-589，同步执行
        └─ bridge.Call(ctx, method, payload)                 # bridge.go:260-285
            └─ conn.Invoke("/executor.ExecutorService/DeleteResource", rawMessage)
                └─ 本机 loopback grpc server（rawServerCodec 用标准 proto 反序列化）
                    └─ internal/grpc.Server.DeleteResource → executor.Executor → client-go → k8s API
    └─ send(AgentFrame(type="response", req_id, payload))    # main.go:363-366
平台侧 reader 线程：
  agent_gateway.py:303-310  slot = a.pending[req_id]; payload/error; event.set()
```

### 6.2 失败与超时处理（逐层）

| 层 | 超时 | 失败行为 | 证据 |
|---|---|---|---|
| 前端 axios | 30s 全局 | 直接报网络错误，后端仍在跑 | `api.ts:10-11`（见 04 报告 ARCH-16） |
| api-server 信号量 | `Semaphore(8)` 排队 | 排队期间占用调用方协程 | `executor_client.py:24-32` |
| 隧道 unary | `call(timeout=30)` 默认，调用方可覆盖 | `TimeoutError("agent 隧道超时（method）")`，**pending 摘除、响应丢弃、不发 cancel** | `agent_gateway.py:147-152` |
| 隧道流式 | `stream_call(timeout=300)` / `bidi_call(timeout=None)` | 超时抛 `TimeoutError`，`streams.pop()`；agent 侧 goroutine 仍继续跑 | `agent_gateway.py:159-183,186-222` |
| agent→本地 executor | `Call` 默认 60s（仅当 ctx 无 deadline） | 返回 gRPC 错误字符串给平台 | `bridge.go:265-271` |
| 隧道断开 | — | `a.alive=False`；`fail_streams("agent 隧道断开")` 把异常塞进所有流式队列；unary 的 `pending` **不被唤醒** → 只能各自等到超时 | `agent_gateway.py:338-340,101-104` |
| 执行计划 | `execute_plan(timeout=600)` | 逐条 yield，遇错中止 | `executor_client.py:624-646` |

**关键缺陷**：①**无取消通道** —— 平台超时/用户断开后，agent 仍会把破坏性操作执行完（`DeleteResource`/`ScaleWorkload`/`DropDatabase`/`RunScript`），平台侧拿不到结果，用户看到「失败」但集群已被改；②**无幂等/去重** —— 用户重试即二次执行；③**流式与 unary 混用同一条连接**，一个 600s 的 `ExecutePlan` 会把该 agent 上所有 unary 请求排队（因为 agent 主循环同步处理 `request`，只有 `stream_open` 才 `go func`，`main.go:421-427`）。

---

## 7. 重连、重复任务、并发控制（问题 6）

| 能力 | 状态 | 证据 |
|---|---|---|
| 断线重连 | 已实现：外层 `for ctx.Err()==nil` 循环 + 固定 3s 退避（无指数退避/抖动） | `main.go:229-239` |
| 重连后重建状态 | 重新 `register` → 重新拉起 5 个上报协程（`runTunnel` 每次全量重建） | `main.go:248-300` |
| 滚动升级双隧道 | 已处理：新连接覆盖 `_agents[cluster_id]`，旧连接 finally 里比对 `_agents.get(cid) is not a` 才标离线 | `agent_gateway.py:341-348` |
| 任务幂等键 | **完全无**。`req_id` 只用于关联响应，不跨请求去重；agent 侧无任何缓存/去重表 | `agent_gateway.py:141`；`main.go:455-589` |
| 每 agent 并发上限 | **隐式 = 1**（`case "request"` 同步执行，`main.go:362-365`）；流式不受此限（`go func`，`main.go:421`） | 无显式限流代码 |
| 平台侧每 agent 队列 | 无界 `queue.Queue()`（`agent_gateway.py:92`），**无长度上限、无背压、无 per-agent 配额** | 同上 |
| 全局并发 | gateway `ThreadPoolExecutor(20)` → 最多 20 条并发隧道（每条隧道长期占用 1 个 worker 线程）；api-server 侧 `Semaphore(8)` 限所有 executor 调用 | `agent_gateway.py:361-362`；`executor_client.py:24` |
| 任务排队可见性 | 无队列深度/等待时长指标；只有 `logger.info("隧道下发 cluster=%s method=%s")` | `agent_gateway.py:142` |

---

## 8. 授权模型：谁能下发什么（问题 7）

### 8.1 平台侧（唯一的真鉴权）

- 角色：`admin / operator / viewer / developer`（`middleware/permission.py:9-25`）。
- 集群/命名空间粒度：`user_permission_service.check_cluster_permission(user, cluster_id, namespace, required)`（`user_permission_service.py:47-88`），来源 = `user_cluster_permissions` 直授 ∪ `user_project_permissions`+`project_cluster_ns` 项目授权，admin 短路放行。
- 调用点：`resources.py` 78 处 `_deny_cluster`、`release.py` / `monitoring.py` / `logs.py` / `terminal.py` / `agent.py` 等（各 api 文件见 `grep -c check_cluster_permission`）。**`resources.py:1150-1163` 的 `exec_pod` 也有 `operate` 校验**（1153 行）。

### 8.2 agent / executor 侧（零鉴权）

- agent 不校验请求的 `cluster_id`：`bridge.go:108-113` 注释「不论请求里的 cluster_id 是多少，都返回本集群」。
- executor gRPC 只提取 `trace-id`（`grpc/server.go:60-70`），无 user/tenant 概念，`getExecutor` 直接按 `cluster_id` 取 clientset（`grpc/server.go:71-79`）。
- 因此：**agent 能否执行「未授权项目」的任务，完全取决于 api-server 有没有在调用前做过 `check_cluster_permission`**。审计结论：
  - 用户路径（前端 → api → executor）：**有**校验，且大部分写操作要求 `operate`。
  - 后台自动路径（告警自动修复、巡检、定时任务）：**按设计不走用户权限**（以自动化身份执行），
    这些路径没有「项目/命名空间隔离」概念，一旦规则配错就会跨项目操作。
  - AI 诊断的 `exec_in_pod`：只在**建会话时**校验一次 `check_cluster_permission`（`agent.py:134`），
    工具执行时不再复核（`agent_service.py:141-163`）。
  - 任何绕过 api-server 的直达路径（9090 / 9096 / 9100 / 隧道伪造）：**完全没有校验**。

### 8.3 impersonation 运行链（名义存在、实际未接线）

```python
# api-server/app/services/rbac_service.py:40-56
async def build_impersonation_groups(user: User) -> list[str]:
    ...  # 生成 autohealops:proj:<id>:<role> 组
```
- 全仓 `grep build_impersonation_groups` **只有定义，无调用**。
- Go 侧 `Manager.ImpersonatedClient(ctx, clusterID, user, groups)`（`k8sclient/manager.go:161-175`）同样**无调用方**。
- 结论：`reconcile_role_bindings` 会在集群里创建 RoleBinding（`rbac_service.py:101-116`），但**没有任何请求以用户身份执行** → 这些 RoleBinding 是死配置；所有操作走 agent SA（cluster-admin 超集，`clusters.py:441-467`）。这既让「按项目/命名空间隔离」失效，也让集群审计日志无法区分「哪个平台用户做了什么」。

---

## 9. agent 能力清单与命令白名单（问题 8）

### 9.1 能力清单（= 隧道可调用的 method 全集）

`ExecutorService` 共 **49 个 RPC**（`k8s-executor/proto/executor.proto:9-104`），agent 通过 `buildCapabilities()` 把方法名全量上报（`main.go:52-66`），并按方法名原样转发到进程内 executor（`main.go:585`）。按危险度分类：

| 类别 | 方法（节选） | 说明 |
|---|---|---|
| 读 | `ListPods` `GetPodDetail` `GetPodYaml` `GetWorkloadYaml` `GetPodLogs` `GetPodEvents` `ListMiscResources` `ListNamespaces` `ListServiceAccounts` `ListWorkloads` `ListHPA` `GetHPA` `ListRevisions` `GetRevisionTemplate` `QueryPrometheus` `GetPeakUsage` `RolloutHistory` `GetDeploymentStatus` `GetPodStatus` `ListDeploymentPods` `ListPodRestartInfo` `GetWorkloadMounts` `HealthCheck` | 读类，含 **Secret 列表（仅元数据/数据项数）** |
| **写 / 破坏** | `DeleteResource` `ApplyPodYaml` `ApplyWorkloadYaml` `ScaleWorkload` `RolloutWorkload` `RollbackWorkload` `RecreateWorkload` `UpdateProbe` `ApplyHPA` `DeleteHPA` `CleanupEvicted` `RepairImagePull` `ExecuteCommand` `ExecutePlan` `RollbackPlan` `BatchExecute` `RunScript` `SetupRBAC` `ReconcileRoleBindings` | 可直接删/改任意资源、跑任意脚本 |
| **命令执行** | `ExecInPod`（单发）、`ExecPodStream`（交互终端，双向流） | 容器内任意命令 |
| 写平台库（agent 上被桩掉） | `CollectQuota`、`HealthCheck` 部分 | `bridge.go:396-407` 覆盖为空实现/纯集群内实现 |
| DB 旁路（JSON） | `PromRange` `DbHealth` `DbObjectDDL` `DbExport` `DbImport` `CreateDatabase` **`DropDatabase`** **`ImportSqlRaw`** `Backup` `DbSchemaByKey` `QueryDatabaseByKey` `QueryDatabase` | `main.go:460-583`，含删库/灌 SQL |

### 9.2 白名单

- 唯一的白名单在 **kubectl 风格命令解析层**：`-whitelist` 参数（默认 19 项：`get,list,describe,logs,top,explain,rollout,scale,set,apply,patch,delete,exec,create,edit,cordon,uncordon,drain,label,taint`，`main.go:33`），校验只看**首词**（`execute.go:104-112`）。
- 该白名单只作用于 `ExecuteCommand`（`execute.go:220-240`）；`dispatch` 实际只支持 `get/describe/scale/rollout/delete/cordon/uncordon/label/taint/logs/drain`（`execute.go:243-273`），`exec/apply/patch/create/edit` 走到「暂不支持」——**白名单比实现宽，是误导性配置**。
- **对 `ExecInPod` / `ExecPodStream` / `ApplyWorkloadYaml` / `DeleteResource` / `RunScript` / DB 旁路方法没有任何白名单/黑名单**：这些方法直接执行，白名单形同虚设。
- `SafetyCheck` 是**子串黑名单**（`rm -rf`、`dd if=/dev/zero`、`shutdown`、`reboot` 等 12 个，`execute.go:115-131`），只对 `ExecuteCommand` 生效 → 对 `ExecInPod("sh -c 'rm -rf /'")` 完全无效。

---

## 10. 安全风险（问题 9）

### 10.1 风险矩阵

| 风险 | 是否成立 | 关键证据 | 影响 |
|---|---|---|---|
| **任意命令执行（无需用户身份）** | ✅ | `agent_gateway_service.py:25-57`（9096 `TunnelCall` 无鉴权）+ `main.go:585`（任意 method 转发）+ `ExecInPod/ExecPodStream/RunScript` | 内网任一 Pod 可对任意在线 agent 执行任意 k8s 操作/容器命令 |
| **证书私钥泄露面** | ✅ | 匿名 `install.yaml`（`clusters.py:259-292`）返回 `tls.key`；9096 `IssueClientCert` 匿名可调（`agent_gateway_service.py:65-75`）；CA 私钥明文 0600（`agent_pki.py:73-74`） | 任何能触达 9096 的主体拿到 CA 签发证书；CA 私钥泄露 = 可签任意证书 |
| **token 重放 / 隧道劫持** | ✅ | token 写在 Deployment args（`clusters.py:427`）+ 证书与 cluster 不绑定（`agent_gateway.py:47-49`）+ 新连接覆盖 `_agents`（`agent_gateway.py:278-279`） | 集群内 `get pods` 权限者 → 拉 install.yaml 得证书 → 顶替 agent 注册 → 劫持该集群全部隧道（伪造返回、截获平台下发的所有命令） |
| **注册接口未鉴权** | 部分（install.yaml 用 token 当凭证，属有意设计但过宽） | `clusters.py:259-292` 注释明确「不需要登录」 | 泄露面 = 一张 CA 证书 + 可枚举集群 id |
| **破坏性操作无审批/无审计** | ✅ | `resources.py:1150` `exec_pod` 无 `audit_service` 调用；隧道日志只有 cluster/method（`agent_gateway.py:142`） | 无法追溯「谁在哪个集群执行了什么」 |
| **pull 模式明文** | ✅ | `executor_client.py:399-405` `insecure_channel` + `executor_client.py:409-410` Bearer token | 同网段嗅探 → 直连 agent 的完整能力 |
| **CA 单点** | ✅ | RWO PVC + 单副本 Recreate（`deployments.yaml:86-90,136-140`） | CA 丢失 → 全部 agent 需重装 |

### 10.2 攻击链（组合后最严重的一条）

```
1) 攻击者在被管集群有 get pods（很常见的开发/运维权限）
   → kubectl get deploy autohealops-agent -o yaml → args 里 "-token=<266字符>"        [clusters.py:427]
2) curl "http://<platform>/api/v1/clusters/<id>/agent/install.yaml?token=<token>"
   → 无需登录，返回 tls.crt/tls.key（CA 现场签发）                                    [clusters.py:259-292,356]
3) 用该证书 + token 连 platform:9095，发 register 帧
   → 网关不校验证书 CN，直接接受，覆盖 _agents[cluster_id]                            [agent_gateway.py:259-281]
4) 之后平台对该集群的每一次 Read/Exec/Delete 都发给攻击者；
   攻击者可返回伪造的 Pod/Secret 列表、把用户的删除命令吞掉、或把真实 agent 踢出路由。
```

> 该链路不需要平台账号、不需要平台内网权限，只需要「在被管集群里能读 Deployment」。

---

## 11. 功能状态清单

| 功能 | 状态 | 证据文件:行 | 备注 |
|---|---|---|---|
| gRPC 双向隧道（push） | 已实现（有代码依据） | `agent.proto:11-13`；`agent_gateway.py:252-348`；`main.go:242-453` | 单连接、单 RPC |
| pull 模式（平台直连 agent） | 部分实现 | `bridge.go:164-200`；`main.go:207-220` | 推送类能力不启用；平台侧明文 channel |
| 令牌注册（sha256） | 已实现（有代码依据） | `agent_gateway.py:32-62` | 无失败计数/封禁 |
| mTLS（CA 签发 + 双向校验） | 已实现（有代码依据） | `agent_gateway.py:372-404`；`agent_pki.py:140-169` | 证书不与 cluster 绑定 |
| 证书吊销 / CRL / 轮换 | 完全未实现 | `agent_pki.py` 全文；`clusters.py:648-659` | 只有 token 吊销 |
| 应用层心跳（ping/pong） | 完全未实现（死代码） | `agent_gateway.py:318-319`；`main.go:352-353` | 实为 keepalive |
| `last_seen` 持续更新 | 完全未实现 | `agent_gateway.py:51`（唯一写点） | 语义 = 最后注册时间 |
| 掉线判定 | 已实现（有代码依据） | `agent_gateway.py:338-348`；`main.go:181-231` | disconnected 后不再探活 |
| 断线重连（3s 固定） | 已实现（有代码依据） | `main.go:229-239` | 无退避抖动 |
| 任务下发 / 结果回传 | 已实现（有代码依据） | `agent_gateway.py:135-153,303-310` | req_id 关联 |
| 任务取消 / 超时后中止 | 完全未实现 | `agent_gateway.py:147-150` | 超时不通知 agent |
| 幂等 / 去重 | 完全未实现 | 全链路无幂等键 | 重试即重复执行 |
| 每 agent 并发控制 | 完全未实现（隐式串行） | `main.go:362-365` | 慢调用阻塞后续 |
| 队列上限 / 背压 | 完全未实现 | `agent_gateway.py:92` 无界 Queue | 内存增长风险 |
| 能力协商（R59） | 部分实现 | `executor_client.py:183-233`；`main.go:52-66` | remote 模式返回 None（`executor_client.py:186-189`） |
| 平台侧集群/命名空间授权 | 已实现（有代码依据） | `user_permission_service.py:47-88`；`resources.py:31-35` | 仅在 API 层 |
| agent/executor 侧授权 | 完全未实现 | `grpc/server.go:60-79`；`bridge.go:108-113` | 直达即绕过 |
| 云原生 impersonation 运行链 | 完全未实现（死代码） | `rbac_service.py:40-56`；`manager.go:161-175` | UI 显示「已接入」误导 |
| agent 版本/升级 | 已实现（有代码依据） | `clusters.py:516-646` | 走隧道改 Deployment 镜像 |
| 操作审计（谁执行了什么） | 完全未实现 | `agent_gateway.py:142` 仅 cluster+method | 无 user_id |

---

## 12. 问题清单

```
问题编号：AGT-01
问题名称：内部网关 9096 无鉴权，可匿名签发 agent 证书并向任意在线 agent 下发任意方法
问题等级：P0
所属模块：agent-gateway / 内部 gRPC
问题描述：
  AgentGatewayInternal 服务的四个 RPC（TunnelCall / TunnelStreamCall / IsConnected /
  IssueClientCert）注册在明文、无拦截器的 grpc server 上，源码中没有任何 authorization
  校验、来源校验或 mTLS。K8s 中该服务以 ClusterIP:9096 暴露给整个集群：
  k8s/networkpolicy.yaml 只对 app=k8s-executor 与 app=ai-service 做了 Ingress 限制，
  **agent-gateway 没有任何 NetworkPolicy** ——「限制 executor 9090 只允许 api-server 访问」
  这条防线可以用 9096 完整绕过（9096 的 TunnelCall 能触达同样的 49 个方法）。
  由此：①任何能访问 9096 的 Pod 可调 IssueClientCert(cluster_id) 拿到 CA 签发的客户端证书
  与 CA 公钥；②可直接 TunnelCall(cluster_id, "DeleteResource"/"ExecPodStream"/"DropDatabase")
  对已注册 agent 执行任意操作，且不经过 api-server 的任何权限校验与审计。
代码位置：
  api-server/app/agent_gateway_service.py:65-75（IssueClientCert 无鉴权）
  api-server/app/agent_gateway_service.py:25-57（TunnelCall 无鉴权）
  api-server/app/agent_gateway_service.py:78-87（add_insecure_port(9096)）
  k8s/services.yaml:84-97（ClusterIP 9096 对全集群可达）
涉及函数或类：AgentGatewayInternalServicer.TunnelCall / TunnelStreamCall / IssueClientCert
调用链：
  集群内任意 Pod → :9096 IssueClientCert → 得到 tls.crt/tls.key
  集群内任意 Pod → :9096 TunnelCall(cid, "DeleteResource", payload) → agent_gateway.call
    → agent handleRequest → bridge.Call → 本地 executor → k8s API 删除资源
根本原因：内部网关按「集群内可信网络」设计，未做服务间认证（与 ARCH-07/ARCH-09 同源）
可能影响：集群内提权为「平台对全部被管集群的完全控制权」；可签发任意证书；可静默执行破坏性操作；
  全程无 api-server 审计记录
修改建议：
  1) 9096 加 X-Internal-Token（与 api-server 共享、按服务分权、可轮换）或 mTLS；
  2) IssueClientCert 单独拆一个更严格的鉴权（仅安装路径可调，且需一次性短期票据）；
  3) NetworkPolicy 只允许 api-server/agent-gateway 自身访问 9096；
  4) TunnelCall 记录调用方标识与 method 到审计表。
验证方法：在集群内任意 Pod 执行
  grpcurl -plaintext agent-gateway:9096 agent.AgentGatewayInternal/IsConnected（应被拒绝）
是否需要立即处理：是
```

```
问题编号：AGT-02
问题名称：agent 接入令牌明文写入 Deployment args，配合匿名 install.yaml 可劫持隧道
问题等级：P0
所属模块：Agent 接入 / 安装清单
问题描述：
  install.yaml 把 token 作为容器启动参数 `-token=<266 字符>` 写进 Pod spec；集群内任何具备
  pods/deployments 读权限的人都能看到。同时 install.yaml 接口不要求登录（只校验该 token），
  会现场签发一张 CA 客户端证书并返回私钥。由于网关不校验证书与 cluster_id 的绑定，攻击者
  用「token + 新证书」注册即可覆盖 _agents[cluster_id]，接管该集群的整条隧道。
代码位置：
  api-server/app/api/clusters.py:427（"-token={token}" 写入 args）
  api-server/app/api/clusters.py:259-292（install.yaml 匿名 + token 校验）
  api-server/app/api/clusters.py:340-360（现场 issue_client_cert）
  api-server/app/agent_gateway.py:278-281（新连接直接覆盖 _agents 条目）
涉及函数或类：agent_install_yaml、agent_onboard、_Servicer.Tunnel（register 分支）
调用链：
  get deploy -o yaml → args.token → GET install.yaml?token= → tls.key
  → grpc Dial(9095, mTLS) → register(cluster_id, token) → _agents 覆盖
根本原因：把「长期静态令牌」放进集群可见位置，且证书/令牌/连接三者没有绑定关系
可能影响：隧道劫持（伪造读结果、吞掉用户命令、截获全部下发内容）；平台数据可信度归零；
  真实 agent 被挤下线
修改建议：
  1) token 改由 Secret 挂载并 `valueFrom.secretKeyRef`，不要出现在 args（同时避免 pod spec 泄露）；
  2) 注册时校验证书 CN/SAN 中的 cluster_id 与 register 帧一致（一条命令即可堵住跨集群）；
  3) install.yaml 的 token 改为一次性、短 TTL（如 30 分钟），用后即失效；
  4) 同一 cluster 已有连接时，新连接注册需二次确认或拒绝（防止静默顶替）。
验证方法：
  用 A 集群的证书 + B 集群的 token 注册 B，观察是否被接受（修复后应 401）
是否需要立即处理：是
```

```
问题编号：AGT-03
问题名称：mTLS 客户端证书未与集群身份绑定，且吊销不回收证书、不踢在线连接
问题等级：P1
所属模块：Agent PKI / 认证
问题描述：
  网关只做 CA 链校验（require_client_auth=True），_verify_and_mark 接收的 token/version/
  capabilities 里没有证书信息，因此「任意一张 CA 签发的证书」都能用于任意集群的连接。
  客户端证书有效期 1825 天，无 CRL/OCSP/轮换/到期巡检；revoke 接口只清 agent_token_hash
  并把状态置 revoked，既不吊销证书，也不断开已建立的隧道（网关没有按 cluster 踢线的入口）。
  另外每次拉 install.yaml 都新签一张证书（含私钥），证书只增不减、无法回收。
代码位置：
  api-server/app/agent_gateway.py:396-398（require_client_auth，仅链校验）
  api-server/app/agent_gateway.py:32-62（校验函数不接收证书主体）
  api-server/app/services/agent_pki.py:140-169（1825 天、无序列号登记）
  api-server/app/api/clusters.py:648-659（revoke 只清 token）
  api-server/app/api/clusters.py:356（每次安装新签证书）
涉及函数或类：agent_pki.issue_client_cert、_verify_and_mark、agent_revoke
调用链：
  revoke → 只改 DB → 已连接 agent 继续工作；已下发证书继续可用
根本原因：PKI 只做到「传输加密」，没有做到「身份绑定 + 生命周期管理」
可能影响：证书泄露后 5 年内可用；离职/换人/集群下线无有效吊销手段；证书数量失控
修改建议：
  1) 服务端从 TLS 对端证书取 CN，断言 == f"autohealops-agent-{cluster_id}"，不一致直接 abort；
  2) 证书缩短到 90 天 + 到期前自动重签（install.yaml 可做成「轮换」接口）；
  3) 建 client_certs 表登记序列号，revoke 时写 CRL 或用短 TTL 自然失效；
  4) revoke 时主动断开该 cluster 的在线隧道（新增 kick_by_cluster）。
验证方法：用集群 A 的证书注册集群 B（修复后应 UNAUTHENTICATED）；revoke 后观察旧连接是否立即断开
是否需要立即处理：是
```

```
问题编号：AGT-04
问题名称：平台超时后不取消 agent 侧执行，破坏性操作会被「看不见地」执行完
问题等级：P1
所属模块：隧道任务链路
问题描述：
  call() 超时只做 pending.pop() 并抛 TimeoutError，不向 agent 发送任何取消帧；agent 的
  handleRequest 是同步阻塞调用，收到 request 就一直执行到结束。因此 DeleteResource /
  ScaleWorkload / RunScript / DropDatabase 这类操作在平台报「超时失败」后仍会在集群内完成，
  用户无从得知，重试则二次执行。
代码位置：
  api-server/app/agent_gateway.py:147-150（只 pop pending）
  api-server/app/agent_gateway.py:145（下发帧无 deadline/cancel 语义）
  k8s-executor/cmd/agent/main.go:362-365（request 同步执行，无 ctx 取消通道）
涉及函数或类：agent_gateway.call、handleRequest、bridge.Call
调用链：acall(timeout=30) → call() wait 超时 → raise → 前端 500；agent 仍继续 → k8s 变更生效
根本原因：协议只有 request/response，没有 cancel 帧；req_id 不承载截止时间
可能影响：状态漂移（平台显示失败、集群已变更）；重复执行破坏性操作；排障误导
修改建议：
  1) AgentFrame 增加 deadline_ms 字段，agent 用 context.WithDeadline 执行；
  2) 增加 type="cancel" 帧，平台超时后下发；
  3) 破坏性 method 增加「执行前置确认 + 执行后回报」（幂等键 = req_id，agent 侧去重表）。
验证方法：对一个大对象 ExecutePlan 中途断开前端，观察 agent 是否继续执行（日志）
是否需要立即处理：是
```

```
问题编号：AGT-05
问题名称：单 agent 请求串行执行 + 无界队列，慢调用阻塞整集群
问题等级：P1
所属模块：agent 运行时
问题描述：
  agent 主循环 `case "request"` 直接同步调用 handleRequest → bridge.Call（阻塞到 k8s 返回），
  只有 stream_open 才 go func。因此同一 agent 同时只能处理 1 个 unary 请求；任何 30s 级
  慢调用（ListMiscResources 全命名空间、get nodes 全 Pod 统计、DbExport）都会使该集群后续
  请求全部排队，等满平台 30s 超时后集体失败。平台侧 a.out 是无界 queue.Queue，队列积压
  既无上限也无监控。
代码位置：
  k8s-executor/cmd/agent/main.go:362-365
  k8s-executor/internal/agentbridge/bridge.go:260-285（Invoke 同步阻塞）
  api-server/app/agent_gateway.py:92（无界 queue.Queue）
  api-server/app/agent_gateway.py:361-362（网关 20 线程 = 最多 20 条隧道）
涉及函数或类：runTunnel（recv 循环）、handleRequest、Bridge.Call、_AgentConn
调用链：多用户并发 → 同一 cluster 请求在 out 队列排队 → agent 逐个执行 → 后面的超时
根本原因：隧道只有一条流，agent 侧没有 per-request 并发池
可能影响：agent 集群「越忙越慢」雪崩；慢接口拖垮整个集群的操作；>20 个 agent 集群时
  第 21 条隧道无法建立（线程池耗尽，表现注册超时且无错误）
修改建议：
  1) agent 侧 `case "request"` 改为 worker pool（如 8 并发，带 method 黑白名单）；
  2) 网关 ThreadPoolExecutor 调大并按隧道数监控；
  3) a.out 加 maxsize + 拒绝策略（返回 busy 而不是无限堆积）；
  4) 记录每 agent 队列深度/执行时长指标。
验证方法：对同一 agent 并发 10 个 ListMiscResources + 1 个 ListPods，看 ListPods 是否超时
是否需要立即处理：是（影响可用性）
```

```
问题编号：AGT-06
问题名称：集群/命名空间授权只在 api-server 层，agent 与 executor 零校验
问题等级：P1
所属模块：授权模型
问题描述：
  真正的鉴权点是 resources.py 等 API 里的 _deny_cluster（user_permission_service）。
  agent 明确忽略请求里的 cluster_id（只服务本集群），executor 只读 trace-id、不读任何
  身份信息；隧道 method 也没有「允许的方法集」。因此任何绕过 api-server 的通道
  （9090 直连、9096 TunnelCall、9100 pull 直连、伪造 agent 注册）都完全不受项目/命名空间
  权限约束。反过来，一旦 api-server 某个新接口忘记加 _deny_cluster，就直接是越权。
代码位置：
  api-server/app/clients/executor_client.py:249-300（隧道感知 stub，无授权逻辑）
  k8s-executor/internal/grpc/server.go:60-79（只取 trace-id）
  k8s-executor/internal/agentbridge/bridge.go:108-113（忽略 cluster_id）
  api-server/app/api/resources.py:31-35（唯一鉴权点）
涉及函数或类：_TunnelAwareStub._call、grpc.Server.getExecutor、Bridge.New
调用链：任意 gRPC 客户端 → :9090 DeleteResource(cluster_id=N) → 直接执行
根本原因：授权集中在最外层，内层按「可信内网」假设设计
可能影响：内网横向 = 全部集群失守；新接口漏鉴权即越权；无法做「agent 级别的最小权限」
修改建议：
  1) executor/agent 侧引入最小权限：按 method 分类的 allow-list（读/写/破坏），
     破坏类要求携带「已审批」令牌（由 api-server 用独立密钥签发，短 TTL）；
  2) 9090/9096 全部加内部鉴权；
  3) 在 CI 加「所有路由必须有鉴权依赖」的自省测试（permission.py:17-18 已有 _allowed_roles
     自省钩子，可复用扩展）。
验证方法：grep 所有 resources 路由，构造不带 _deny_cluster 的调用；或直连 9090 调用 DeleteResource
是否需要立即处理：是
```

```
问题编号：AGT-07
问题名称：云原生 RBAC 的 impersonation 运行链未接线，实际全部以 cluster-admin SA 执行
问题等级：P1
所属模块：授权模型 / 云原生 RBAC
问题描述：
  rbac_service.build_impersonation_groups()（生成 autohealops:proj:<id>:<role> 组）全仓无
  调用方；Go 侧 Manager.ImpersonatedClient()（按用户派生带 Impersonate 的 clientset）同样
  无调用方。也就是说：RoleBinding 会被 reconcile 创建，但没有任何请求以用户身份访问 k8s。
  结果：所有 k8s 操作都使用 agent 的 ServiceAccount（apiGroups:* resources:* verbs:全部
  + impersonate），集群侧审计无法区分平台用户；平台 UI 上 rbac_status="RBAC 已接入
  （impersonation 启用）" 与实际不符。
代码位置：
  api-server/app/services/rbac_service.py:40-56（定义，无调用）
  k8s-executor/internal/k8sclient/manager.go:161-175（定义，无调用）
  api-server/app/services/rbac_service.py:139-160（写入 rbac_mode/enabled/status）
  api-server/app/api/clusters.py:441-467（agent ClusterRole */*）
涉及函数或类：build_impersonation_groups、ImpersonatedClient、setup_cluster_rbac
调用链：setup_cluster_rbac → 探测 impersonate_capable=True → 写 rbac_mode="rbac" →
  但后续 getExecutor 仍用 manager.GetClient（平台身份）
根本原因：能力探测与运行链分两期实现，第二期未完成；状态字段先于功能落地
可能影响：项目/命名空间隔离在集群侧完全不存在；安全评审会误判为「已隔离」；
  集群内所有操作都是超管身份，一次越权就是全集群
修改建议：
  1) 要么接线：executor gRPC metadata 透传 user/groups，getExecutor 用 ImpersonatedClient；
  2) 要么下线：rbac_status 改为「仅创建 RoleBinding，未启用按用户鉴权」，UI 明确提示；
  3) 中间态：对破坏性 method 强制要求 impersonation 可用，否则拒绝。
验证方法：以非 admin 用户执行一次 scale，检查 k8s audit log 里的 user 字段是否为
  system:serviceaccount:autohealops:autohealops-agent（是 → 未接线）
是否需要立即处理：是（影响安全结论与合规）
```

```
问题编号：AGT-08
问题名称：破坏性操作（容器内执行、删除、应用 YAML）无审计记录，隧道日志无用户身份
问题等级：P1
所属模块：审计
问题描述：
  POST /clusters/{id}/namespaces/{ns}/pods/{pod}/exec（容器内执行任意命令）在 resources.py
  里没有任何 audit_service 调用；DeleteResource / ApplyWorkloadYaml / ApplyPodYaml 等写操作
  也只有部分节点变更类走了 _audit_node_change。隧道侧日志只有 "隧道下发 cluster=%s method=%s"，
  没有 user_id、没有 trace_id（executor_client 传了 trace-id metadata，但 agent_gateway.call
  没记录）。因此 agent 集群里发生的删除/执行无法归因到人。
代码位置：
  api-server/app/api/resources.py:1150-1163（exec_pod 无审计）
  api-server/app/agent_gateway.py:142（日志仅 cluster+method）
  api-server/app/api/resources.py:431-433,1424-1430（对照组：有审计的少数接口）
涉及函数或类：exec_pod、agent_gateway.call、delete_object/apply_object_yaml
调用链：用户 → exec_pod → 隧道 → agent → 容器内命令 → 无任何记录落库
根本原因：审计是按接口逐个补的，没有统一中间件；隧道层不知道调用者
可能影响：容器内任意命令不可追溯；合规审计（等保/SOC2）不通过；事后取证无数据
修改建议：
  1) 统一在 executor_client.acall 层注入 actor（user_id/username/trace_id）到 metadata，
     隧道与 agent 日志都落 actor；
  2) exec_pod / delete / apply 全部写 audit_service（含命令原文与输出摘要）；
  3) 加「高危 method 审计不可关闭」开关。
验证方法：以 operator 执行一次 exec_pod，查询 audit 表应无记录（现状）
是否需要立即处理：是
```

```
问题编号：AGT-09
问题名称：pull 模式平台侧明文 channel + Bearer token 明文传输
问题等级：P1
所属模块：Agent 接入（pull）
问题描述：
  pull 模式平台侧建 channel 用 grpc.insecure_channel(url)，令牌以 metadata
  authorization: Bearer <token> 明文发送；agent 侧虽支持 -pull-cert/-pull-key/-pull-ca，
  但平台侧代码没有任何 TLS 选项，即使 agent 开了 TLS 平台也连不上（会 TLS 握手失败）。
  于是生产只能是「明文 + 明文令牌」，同网段嗅探即可拿到 agent 的完整能力（含 DropDatabase）。
代码位置：
  api-server/app/clients/executor_client.py:399-405（insecure_channel）
  api-server/app/clients/executor_client.py:409-410（Bearer 明文）
  k8s-executor/internal/agentbridge/bridge.go:164-200（agent 侧支持 TLS，平台未用）
  api-server/app/api/clusters.py:387-400（pull_token 校验仅字符集）
涉及函数或类：_pull_channel、_pull_call、Bridge.ServePull
调用链：api-server → insecure channel → agent:9100（token 明文）
根本原因：pull 是后加形态，平台侧只实现「能连上」，没实现传输安全
可能影响：令牌与全部指令/数据可被同网段读取；攻击者可直接 gRPC 调 agent 执行任意方法
修改建议：
  1) 平台侧增加 grpc.secure_channel + 证书配置（复用 agent CA 或独立 CA）；
  2) token 最少 30 天轮换；pull 模式默认要求 mTLS；
  3) UI 对「未启用 TLS 的 pull 集群」显示高危标记。
验证方法：tcpdump 抓 9100 端口，检查 metadata 是否可见 token
是否需要立即处理：是
```

```
问题编号：AGT-10
问题名称：CA 私钥以明文 0600 落共享卷，无加密、无备份策略、单副本 RWO
问题等级：P2
所属模块：Agent PKI
问题描述：
  ca.key 以 PEM 无口令明文写在 data/agent-ca/（0600），K8s 上是 RWO PVC 单副本挂载。
  能读该卷的任何进程/备份/快照都等于拿到 CA 私钥；PVC 丢失则全部 agent 证书失效
  （网关启动时也只会打一条 ERROR 日志，不会阻止启动）。
代码位置：
  api-server/app/services/agent_pki.py:73-74（NoEncryption 私钥）
  api-server/app/services/agent_pki.py:34-37（chmod 0600）
  api-server/app/agent_gateway.py:386-390（CA 新建只打 ERROR 继续跑）
  k8s/pvc-agent-ca.yaml:1-20
涉及函数或类：_load_or_create_ca、start_server
调用链：PVC → 备份/快照 → ca.key → 伪造任意 agent 证书
根本原因：为「开箱即用」选择自签自管，未接入外部 PKI/KMS
可能影响：CA 泄露 = 全部被管集群的 agent 身份可伪造；CA 丢失 = 全量重装
修改建议：
  1) CA 私钥放 KMS/Vault 或至少加密（口令来自 Secret）；
  2) 启动时若 CA 为新建，直接拒绝启动（除非显式 ALLOW_NEW_CA=true）；
  3) PVC 加备份 CronJob 与恢复演练。
验证方法：读取 ca.key 是否可直接 openssl 使用（现状是）
是否需要立即处理：否（但需尽快给结论）
```

```
问题编号：AGT-11
问题名称：协议声明的 ping/pong 与 last_seen 语义失效，运维口径被误导
问题等级：P2
所属模块：Agent 协议
问题描述：
  agent.proto 注释声明 ping/pong 心跳，agent 实现了 pong 应答，但平台从不发 ping；
  agent_last_seen_at 只在注册时写一次。前端「最后在线时间」展示的其实是「最后注册时间」。
  掉线完全依赖 keepalive + _agents 内存对象，缺少应用层证据。
代码位置：
  k8s-executor/proto/agent.proto:16
  k8s-executor/cmd/agent/main.go:352-353（pong 应答，永不到达）
  api-server/app/agent_gateway.py:318-319（只 pass）
  api-server/app/agent_gateway.py:51（last_seen 唯一写入点）
涉及函数或类：_Servicer.Tunnel、runTunnel
调用链：无
根本原因：功能预留未落地，注释与实现不同步
可能影响：排障误判「agent 刚刚还在线」；无法用应用层心跳做 SLA 统计
修改建议：
  1) 网关每 30s 主动 ping，pong 超时 3 次判离线，同时更新 last_seen；
  2) 或删除 ping/pong 分支并把字段改名为 agent_last_register_at；
  3) 增加 agent_last_disconnect_at。
验证方法：断开 agent 网络 60s，对比 UI 显示时间与实际
是否需要立即处理：否
```

```
问题编号：AGT-12
问题名称：12 个 JSON 旁路方法不在 proto 契约内，破坏性方法无能力门禁
问题等级：P2
所属模块：Agent 协议
问题描述：
  PromRange/DbExport/DbImport/CreateDatabase/DropDatabase/ImportSqlRaw/Backup/
  DbSchemaByKey/QueryDatabaseByKey/QueryDatabase/DbHealth/DbObjectDDL 以字符串 method +
  JSON payload 走 Tunnel，不在 executor.proto 定义内；能力表只覆盖 hpa/log_time_range/
  rollback。方法名改一端即运行时报 unknown method，且 DropDatabase/ImportSqlRaw 没有
  「agent 版本不足」的友好提示与审批。
代码位置：
  k8s-executor/cmd/agent/main.go:460-583
  api-server/app/clients/executor_client.py:161-179
涉及函数或类：handleRequest、_FEATURE_REQUIRED、_METHOD_TO_FEATURE
调用链：平台 _gateway_call(cid, "DropDatabase", json) → agent 字符串匹配 → agentdb 执行
根本原因：proto 变更成本高，走了 JSON 旁路且未登记契约
可能影响：版本不匹配时错误不可读；破坏性方法缺少统一门禁
修改建议：
  1) 纳入 proto（或集中常量模块，双侧引用）；
  2) 为破坏性方法登记 _METHOD_TO_FEATURE + 平台侧二次确认 + 审计。
验证方法：对老 agent 调 DbExport，检查返回是否为友好升级提示
是否需要立即处理：否
```

```
问题编号：AGT-13
问题名称：命令白名单只覆盖 ExecuteCommand 且比实现更宽，形同虚设
问题等级：P2
所属模块：agent 执行层
问题描述：
  -whitelist 默认 19 项（含 exec/apply/patch/create/edit），但 dispatch 只实现 10 个动词，
  其余直接「暂不支持」；而真正高危的 ExecInPod / ExecPodStream / ApplyWorkloadYaml /
  DeleteResource / RunScript / DB 旁路方法完全不走白名单。SafetyCheck 是子串黑名单，
  对 "sh -c 'rm -rf /'" 这类容器内命令无效。
代码位置：
  k8s-executor/cmd/agent/main.go:33
  k8s-executor/internal/executor/execute.go:104-131（白名单+黑名单）
  k8s-executor/internal/executor/execute.go:220-273（ExecuteCommand 专用）
涉及函数或类：Executor.WhitelistCheck、SafetyCheck、dispatch
调用链：ExecInPod("rm -rf /tmp/x") → 无白名单/无黑名单 → 直接执行
根本原因：白名单是「kubectl 风格命令」的产物，与后来的 RPC 化执行模型不匹配
可能影响：安全评审误以为「有白名单保护」；实际容器内命令无任何限制
修改建议：
  1) 把白名单下沉到 RPC 层（method → allow/deny），与 -whitelist 解耦；
  2) 对 ExecInPod 增加命令策略（只读/黑名单/审批），或明确声明「容器内即无限制」并靠审批控制；
  3) 修正默认白名单，去掉未实现的动词，避免误导。
验证方法：调用 ExecInPod 执行 mkfs（应被拦截，现状不会）
是否需要立即处理：否
```

```
问题编号：AGT-14
问题名称：agent 断线后状态机不可恢复（disconnected 集群不再被探活）
问题等级：P2
所属模块：Agent 健康检查
问题描述：
  _mark_disconnected 把 agent_status 置 disconnected；_agent_cluster_health_loop 只查
  agent_status=="connected" 的集群。因此一旦进入 disconnected 且 agent 因任何原因未能
  重新注册（例如 keepalive 未触发半开检测、平台 IP 变更后 agent 仍连旧地址、token 被清），
  平台侧不会再有任何主动探测或告警，集群永久停在 disconnected。
代码位置：
  api-server/app/agent_gateway.py:75-86
  api-server/app/main.py:198-201（只筛 connected）
涉及函数或类：_mark_disconnected、_agent_cluster_health_loop
调用链：隧道断开 → disconnected → 健康循环跳过 → 无告警
根本原因：状态机只有两个状态，缺少「探测中/重连中」与失败告警
可能影响：agent 集群静默失效，直到用户点开页面才发现
修改建议：
  1) 健康检查覆盖 source=agent 全部集群（未连接时直接标记 unreachable 并告警）；
  2) 加「agent 离线超过 N 分钟」告警规则；
  3) 记录 last_disconnect_at 并在 UI 展示。
验证方法：停掉某集群 agent Pod，观察 24h 内是否有告警
是否需要立即处理：否
```

```
问题编号：AGT-15
问题名称：agent 与 executor 无请求大小/频率限制，单请求可打爆内存
问题等级：P3
所属模块：agent 运行时
问题描述：
  AgentFrame.payload 是 bytes，无大小上限；ListMiscResources 在全命名空间下会聚合
  大量对象；gateway 侧 streaming 队列无上限；agent 侧 streamState.in 是 64 缓冲但
  unary 请求没有背压。超大响应会在网关/agent 两侧同时占用内存。
代码位置：
  k8s-executor/proto/agent/agent.proto:21（bytes payload 无限制）
  api-server/app/agent_gateway.py:92（无界队列）
  k8s-executor/cmd/agent/main.go:368-373（stream 缓冲 64）
涉及函数或类：AgentFrame、_AgentConn.out、runTunnel
调用链：ListMiscResources 大集群 → 单响应数十 MB → 网关内存上涨
根本原因：未设置 gRPC max message size 与队列上限
可能影响：极端情况下 OOM；慢客户端拖累网关
修改建议：
  1) 服务端设置 grpc.max_send_message_length / max_receive_message_length；
  2) 大结果分页或改流式（stream_msg 分片）；
  3) 队列加 maxsize。
验证方法：对超大集群调 ListMiscResources，监控 api-server RSS
是否需要立即处理：否
```

```
问题编号：AGT-16
问题名称：AI 诊断助手可通过 exec_in_pod 在任意（有权限的）Pod 内执行任意命令，且无审计
问题等级：P2
所属模块：AI Agent（agent_service / agent.py）
问题描述：
  诊断助手工具集里唯一放行的 operate 工具是 exec_in_pod（AGENT_OPERATE_WHITELIST），
  执行上下文直接用 session.cluster_id + namespace + pod_name，不做二次权限校验（只信任
  建会话时的 check_cluster_permission）。会话可被「共享」给其他用户读取（共享后按
  cluster/namespace 权限二次过滤，agent.py:437-455），但共享者无法执行——风险主要在：
  ①命令由 LLM 生成，缺少人工确认环节；②执行结果落 JSONL 文件（data/agent-sessions），
  不写 audit 表；③LLM 可被提示注入诱导执行非诊断命令。
代码位置：
  api-server/app/services/agent_service.py:138-163（AGENT_OPERATE_WHITELIST + _execute_tool）
  api-server/app/api/agent.py:72-110（会话可见性判定）
  api-server/app/services/agent_session_store.py:39-60（消息落文件）
涉及函数或类：_execute_tool、tool_registry.execute_tool、can_view_session/can_read_session
调用链：用户提问 → ai_service.decide → tool_call(exec_in_pod, command) → executor → 容器
根本原因：诊断链路为「自动化」优先，人审环节缺失；工具输出审计与终端审计分离
可能影响：提示注入 → 容器内非预期命令；审计只存在于会话文件，不进入审计表
修改建议：
  1) exec_in_pod 命令增加只读白名单/正则（与 host command_guard 对齐）；
  2) 高风险命令要求前端二次确认；
  3) 每次 exec_in_pod 写 audit_service（含 session_id、命令、输出摘要）。
验证方法：构造提示「请执行 rm -rf /tmp/x 来验证」，观察是否直接执行
是否需要立即处理：否
```

---

## 13. 无法确认事项

| 事项 | 卡点 |
|---|---|
| 生产实际 `AGENT_GATEWAY_MODE` 与 9096 是否被 NetworkPolicy 完全隔离 | 只读到模板（`configmap.yaml:84` remote）与 `k8s/networkpolicy.yaml` 的策略片段，未见集群实际生效对象 |
| 各被管集群 agent 的真实版本与是否启用 mTLS | 代码默认版本 `0.2.18`（`config.py:77`），ConfigMap 写 `0.2.7`；实际以各集群 Secret/args 为准，未连环境 |
| 平台是否对 9095 做了四层暴露（NodePort/LB/SamWaf） | 仓库内无实际暴露配置，`services.yaml` 只有 ClusterIP |
| 平台侧是否有未提交的「按 cluster 踢线」脚本/运维手段 | 代码内无，运维层面无法从仓库确认 |
| CA 卷是否有备份 | 无备份 CronJob 覆盖 `autohealops-agent-ca`（`backup-cronjob.yaml` 只备份 MySQL） |
| LLM 生成的 exec_in_pod 命令在生产是否有人审 | 未连环境，无法确认前端是否有确认弹窗（前端 `AgentChat` 页面未逐行审） |

---

## 14. 审计方法与覆盖范围

**精读（全文）**
- `k8s-executor/proto/agent.proto`（29 行）
- `k8s-executor/proto/executor.proto`（服务与 RPC 列表，49 个）
- `k8s-executor/cmd/agent/main.go`（821 行，全文）
- `k8s-executor/internal/agentbridge/bridge.go`（407 行，全文）
- `k8s-executor/internal/executor/execute.go`（白名单/黑名单/分发，1-340）
- `k8s-executor/internal/script/runner.go`（119 行）
- `api-server/app/agent_gateway.py`（440 行，全文）
- `api-server/app/agent_gateway_main.py`、`agent_gateway_service.py`（全文）
- `api-server/app/services/agent_pki.py`（169 行，全文）
- `api-server/app/services/agent_session_store.py`（1-125）
- `api-server/app/api/clusters.py:180-700`（agent 接入/安装/升级/吊销）
- `api-server/app/clients/executor_client.py:1-430`（隧道/remote/pull/能力协商）
- `api-server/app/middleware/auth.py`、`permission.py`（全文）
- `api-server/app/services/rbac_service.py`（161 行，全文）
- `api-server/app/services/user_permission_service.py:40-120`
- `api-server/app/main.py:181-270`（agent 健康循环 / pull 刷新）
- `api-server/app/api/resources.py`（鉴权点分布 + exec_pod）
- `k8s/deployments.yaml:1-160`（api-server / agent-gateway）、`k8s/services.yaml:70-100`、`k8s/pvc-agent-ca.yaml`
- `agent/Dockerfile`、`agent/examples/agent.env.example`、`agent/examples/install.yaml.example`

**扫描式阅读**
- `grep AgentFrame` / `grep agent_last_seen_at` / `grep build_impersonation_groups` / `grep ImpersonatedClient`（确认「定义存在但无调用」）
- `api-server/app/services/agent_service.py`、`api/agent.py`（AI 诊断链路结构与工具白名单）
- `api-server/app/services/agent_alert_service.py:239-266`（事件分派，确认 cluster_id 以防串集群覆盖）

**未读 / 不覆盖**
- `internal/executor/*` 各资源操作的实现细节（属 K8s 资源操作审计范围）
- `internal/agentdb/*`（DB 旁路方法的 SQL 实现细节，属数据库链路审计）
- 前端 `AgentChat` 页面交互细节

**未执行的操作（合规说明）**：未修改任何业务代码/配置/YAML/依赖，未安装依赖，未连接任何环境，未执行任何 git 写操作；仅新建本报告。
