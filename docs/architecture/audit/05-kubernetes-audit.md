# 05 Kubernetes 能力审计报告（AutoHealOps）

- 审计对象：`/Users/wolf/Documents/project/autohealops`
- 审计方式：**只读**源码审计。未修改任何业务代码/配置/YAML/依赖，未安装依赖，**未执行任何 kubectl**，未连接任何集群或生产环境，未做 git 写操作（本报告为唯一新增文件）。
- 审计口径：所有结论带 `文件:行`；每条能力给出「已实现 / 部分实现 / 仅前端 / 仅后端接口 / 逻辑不完整 / 未实现」状态标签。
- 覆盖：api-server K8s 相关 API 与客户端层、k8s-executor（Go）全部 executor/k8sclient/grpc/watcher 关键文件、`k8s/` 部署与 RBAC、`frontend/src/pages/cluster/*` 与 `services/api.ts`。

---

## 1. 结论摘要

1. **K8s 能力面很大、落地很实**：`resources.py` 79 条路由、`clusters.py` 21 条路由、`executor.proto` 49 个 RPC，Go 侧 `server.go` 实现 49 个 RPC 方法（另 1 个内部辅助 `getExecutor`），覆盖 Pod/日志/事件/工作负载/HPA/节点/PVC/ConfigMap/Secret/SA/终端。**不是空壳**。
2. **接入方式有三种，且三条路径真的存在**：`source=kubeconfig`（平台侧直连）、`source=agent`（push 反向隧道 / pull 平台直连 agent）、`source=in-cluster`（Go 侧 `rest.InClusterConfig()` 自动登记）。分支判定集中在 `executor_client._TunnelAwareStub` 与 `k8sclient.Manager.GetClient`。
3. **kubeconfig 用 AES-256-GCM 加密存储，密钥是环境变量 `ENCRYPTION_KEY`（32 字节）**，Python/Go 两侧算法与拼接顺序一致；**平台没有任何回显 kubeconfig 的接口**（只有写入）。但密钥是**单把静态密钥、无 KMS/无轮换**，且 `k8s/backup-cronjob.yaml` 明确要求备份包与密钥一起保存。
4. **权限判定全部在 Python 应用层（SQL 查询授权表），没有一处在 K8s 侧用 impersonation 兜底**：`ImpersonatedClient` 是死代码（`manager.go:161-183`，全仓无调用方）；平台连被管集群用的是 `*/*` 的超管凭据。因此**应用层漏检即为越权**。
5. **确认 4 类可利用的越权/泄露（P0）**：① YAML 里的 `metadata.namespace` 会覆盖鉴权时用的 namespace → 越权写其他命名空间；② `namespace` 参数留空时只校验「该集群任意一个 ns 有权限」→ 越权读全集群；③ Secret 的 `data` 被解码成明文返回给只有 read 权限的用户；④ 9096 内部网关**无鉴权、且没有任何 NetworkPolicy**——集群内任意 Pod 可对**任意在线 agent 集群**直接下发任意 RPC（含 `DeleteResource`/`ExecInPod`），同时 `IssueClientCert` 还能无门槛为任意 cluster_id 签发 agent 客户端证书；而 agent 的 ClusterRole 是 `apiGroups:["*"] resources:["*"]`。
6. **破坏性 K8s 操作没有审批，绝大多数没有审计**：数据库侧有 `code=428 需审核`（`db_connections.py:425`），K8s 侧 `delete/scale/exec/改镜像/apply YAML` 全都没有审批；审计只覆盖 4 类（回滚、Evicted 清理、批量工作负载、节点元数据变更）。
7. **「灰度发布」在 Kubernetes 侧不存在**：`gray_release.go` 是「修复计划按影响面分批执行 + 熔断」，与工作负载金丝雀/流量切分无关；前端也没有灰度入口。
8. **两个「看起来有、实际不可用/不安全」的点**：`in-cluster` 自动接入在 `k8s/` 里**没有任何平台自身的 ClusterRole**，登记为 healthy 但后续操作会 forbidden；`9099` 日志流/产物 HTTP 端口**无任何鉴权**，本地启动时监听 `0.0.0.0`，`/files?path=` 可直接下载 JVM heapdump（堆内存含凭据）。
9. **工程健壮性尚可但口径分散**：gRPC 默认 30s、长任务 120/360/600s 分层；传输层关 HTTP/2 + 瞬时错误重试（`retry.go`）做过专项加固；但 gRPC **未设 `MaxRecvMsgSize`**（默认 4MiB）导致大列表/整份日志容易失败，且全局 8 个信号量把后台任务和用户请求混在一个池里。

---

## 2. 逐项回答（审计任务要求的 9 个问题）

### 2.1 集群接入方式与代码分支判断逻辑

**三种接入方式，由 `clusters.source` 字段区分**（`api-server/app/models/cluster.py`，写入点：`clusters.py:186,687`（kubeconfig）、`:235`（agent）、`k8s-executor/internal/bootstrap/incluster.go:57-64`（in-cluster））。

| 方式 | 建立入口 | kubeconfig | 执行路径 |
|---|---|---|---|
| `kubeconfig` 直连 | `POST /api/v1/clusters`（`clusters.py:660`）、`POST /clusters/parse-kubeconfig`（`:137`）、`POST /clusters/import-multi`（`:160`） | 加密存 `clusters.kubeconfig_encrypted` | api-server → gRPC 9090 → executor → client-go |
| `agent`（push） | `POST /clusters/agent-onboard`（`clusters.py:212-256`）+ 匿名拉取 `GET /clusters/{id}/agent/install.yaml?token=`（`:259-500`） | **不存**（`encrypt_aes256_gcm("")`，`:234`） | api-server → 9096 `TunnelCall` → agent → 进程内 loopback ExecutorService → client-go |
| `agent`（pull） | 同上，`agent_mode=pull` + `agent_pull_url` | 不存 | api-server 直连 `agent_pull_url` 的 gRPC，`authorization: Bearer <pull_token>` |
| `in-cluster` | 自动：`bootstrap.EnsureInCluster`，仅当 `KUBERNETES_SERVICE_HOST` 存在（`bootstrap/incluster.go:28-31`） | **不存**（`:61`） | `rest.InClusterConfig()` + ServiceAccount（`manager.go:216-232`） |

**判定逻辑（这是关键，因为一次调用到底走哪条路只能运行时才知道）**：

```python
# api-server/app/clients/executor_client.py:263-296
def _call(req, timeout=None, metadata=None, **kwargs):
    cid = int(getattr(req, "cluster_id", 0) or 0)
    if cid:
        if _gateway_is_connected(cid):                 # ① 内存在线表 → 反向隧道
            data = _gateway_call(cid, name, req.SerializeToString(), ...)
            return resp_cls.FromString(data)
        out = _pull_call(cid, name, req, resp_cls, ...) # ② pull 目标表 → 平台直连 agent
        if out is not None:
            return out
    return fn(req, timeout=timeout, metadata=metadata, **kwargs)  # ③ 直连 executor
```

- 在线判定：`inproc` 模式读进程内 `_agents` 字典（`agent_gateway.py:106-110`），`remote` 模式走 9096 `IsConnected`（`executor_client.py:82-91`）。
- 回退语义：agent 集群掉线后会落到本机 executor，报 `no configuration has been provided`，被改写为中文提示（`executor_client.py:286-296`）。
- `in-cluster` 分支在 Go 侧：`manager.MarkInCluster(clusterID)` 后 `GetClient` 走 `rest.InClusterConfig()`（`manager.go:216-232`）。

### 2.2 kubeconfig 存储与加密

| 项目 | 结论 | 证据 |
|---|---|---|
| 算法 | AES-256-GCM，`base64(IV(12B) + ciphertext + tag(16B))`，IV 在前 | Python `core/security.py:18-31`；Go `k8sclient/crypto.go:22-56` |
| 密钥来源 | 环境变量 `ENCRYPTION_KEY`，**必须 32 字节**，Go 启动时强校验，不派生 | `k8sclient/crypto.go:16-20`；`cmd/executor/main.go:53-55`；`k8s/secret.example.yaml:27` |
| 密钥管理 | 单把静态密钥，无 KMS/无轮换/无版本号；备份要求随包保存 | `k8s/backup-cronjob.yaml:3,11` |
| 存哪 | `clusters.kubeconfig_encrypted`（MySQL 明文列） | 写入点 `clusters.py:186`、`:686`、`:733` |
| 能否解密回显 | **平台不提供任何回显接口**（写接口只进不出；`GET /clusters/{id}` 返回字段列表无 kubeconfig，`clusters.py:787-806`） | 全仓 grep `decrypt_aes256_gcm` 在 clusters.py 只用于 `agent_pull_token`（`:871`） |
| 谁解密 | k8s-executor `loadKubeconfig()` 启动后按需解密并缓存在 `rest.Config`（明文常驻内存） | `cmd/executor/main.go:135-141`；`manager.go:236-253` |
| 风险 | `ENCRYPTION_KEY` 泄露 = 全部被管集群 kubeconfig（多为 cluster-admin）一次性失守；且与 `JWT_SECRET_KEY` 同存于同一个 Secret | `k8s/secret.example.yaml:25-27`；`config.go:66` |

补充：`agent` 模式的客户端证书/私钥被渲染进安装清单的 Secret（`clusters.py:337-392`），这属于**有意设计**（集群侧需要），但该清单只由 token 保护，token 泄露即可重装 agent。

### 2.3 是否支持 agent 模式接入；agent 模式下 K8s API 怎么走

**支持，且两条子模式都真实可用。**

- **push（默认）**：agent 在集群内起 gRPC 客户端，连平台 9095（mTLS），建流后第一帧 `register` 上报 `cluster_id`/`token`/版本/能力（`cmd/agent/main.go:261`）。平台校验 `sha256(token)==clusters.agent_token_hash`（`agent_gateway.py:44-58`，`Tunnel` 在 `:253-350`）。
- **agent 侧执行真实 K8s API**：agent 进程内用 `rest.InClusterConfig()`（或传入 kubeconfig）+ `kubernetes.Clientset`，并在 `127.0.0.1:0` 起一个**真实的 ExecutorService gRPC server**（raw codec），隧道请求按「方法名 + 原始 protobuf 字节」转发进去（`internal/agentbridge/bridge.go:104-140`）→ 因此 49 个 RPC 全量可用，不需要逐个写 switch。
- **平台侧路由**：`_TunnelAwareStub` 按 `req.cluster_id` 判断在线（见 2.1），流式方法另有 `ExecutePlan`/`BatchExecute`（服务端流）与 `ExecPodStream`（双向流，先窥探首帧取 cluster_id）的特化实现（`executor_client.py:301-371`）。
- **pull 模式**：平台 `_PULL_TARGETS` 进程内表（30s 刷新，`main.py:299-338`），直连 agent `:9100`，`authorization: Bearer <pull_token>`（`executor_client.py:399-464`）；agent 侧 `bridge.ServePull`（`cmd/agent/main.go:212-220`）。
- **能力差异（源码明确）**：pull 模式**不启动** Pod/Node watcher、Pod/DB/主机指标上报、K8s 事件、配额快照（只在 push 分支创建协程，`cmd/agent/main.go:207-220,264-300`）。UI 没有按模式置灰。
- **能力协商**：平台按 `_FEATURE_REQUIRED` 拦截（HPA 4 方法、日志时间段 2 字段），缺能力返回「请升级 agent」而不是原始 `unknown method`（`executor_client.py:161-172`、`:197-215`）。

### 2.4 namespace 管理与权限过滤（SQL 还是应用层）

**结论：只有「查询」，没有管理（无 create/delete/patch namespace）；权限过滤 100% 在应用层，且实现不一致。**

- namespace 数据来源两条：① `ResourceSnapshot` 表 distinct（最近 60 分钟，`resources.py:189-236`）；② 无快照时实时经隧道 `list_workloads`+`list_pods` 反推（`:228-236`）。**没有 `POST/DELETE /namespaces` 路由**（全仓 grep 无命中），`DeleteResource` 在 Go 侧虽支持 `kind=namespace`（`pod_list_events.go:499`、`workloadGVR:404`），但 API 层 kind 白名单不含 namespace（`_CONFIG_OBJECT_KINDS`/`_WORKLOAD_KINDS`，`resources.py:724,995`）。
- 过滤发生在**应用层 SQL 查授权表**：`user_permission_service.check_cluster_permission()`（`user_permission_service.py:53-100`）查 `user_cluster_permissions` + `project_cluster_ns`/`user_project_permissions` 两跳 join。**没有任何调用改成 K8s impersonation**。
- 语义缺陷（重要）：
  ```python
  # user_permission_service.py:56-58
  # - namespace 为空：表示只校验集群级（任意 namespace 授权即可）
  if namespace and p.namespace and p.namespace != namespace: continue
  ```
  → 只要用户在集群里**任意一个** ns 有权限，`namespace=""` 的调用就通过。而多个列表接口默认 `namespace=""`（`/pods`、`/workloads`、`/misc-resources`、`/service-accounts`、`/namespaces`、`/namespace-list`、`/nodes`、`/detail`）→ **能读到全部命名空间**。
- 项目授权的 namespace 匹配是**精确等值**（`ProjectClusterNS.namespace == namespace`，`:89`），直接授权允许 `p.namespace == ""`（全集群）；两套语义不对齐。
- 告警/统计链路**完全不做用户过滤**：`GET /alerts`（`alerts.py:60-80`）除 `cluster_id` 查询参数外没有任何 `visible_ids`/`check_cluster_permission`（全文件 grep 无 `permission`/`visible`）。

### 2.5 Pod 查询 / 日志 / Events 的实现与限制

**Pod 查询**

| 能力 | 实现 | 限制 |
|---|---|---|
| 单 ns Pod 列表 | `GET /resources/clusters/{id}/namespaces/{ns}/pods` → `resource_service.get_namespace_pods`（DB 快照） | 依赖快照采集；agent 集群可能为空走实时 |
| 集群 Pod 实时列表 | `GET /resources/clusters/{id}/pods?namespace=` → `ListPods`（`resources.py:604-613`） | **无分页、全量返回**；`namespace=""` 返回全集群 |
| Pod 详情/YAML | `GetPodDetail` / `GetPodYaml` | YAML 去掉 managedFields | 
| 容器名 | describe 解析（带 10 分钟 Redis 缓存，`resources.py:245-266`） | 多容器需手选 |

**日志（三条不同链路，口径不一致）**

1. **unary tail**：`GET .../pods/{pod}/logs?tail=` → `GetPodLogs`，**tail 夹在 1..2000**（`resources.py:546-577`），gRPC 超时 `TIMEOUT=30s`（`executor_client.py:547,867`）。多容器未指定时按「default-container 注解 → 非 sidecar → 第一个」自动选（`pod_ops.go:57-77`）。
2. **实时跟随（WS + 9099 HTTP）**：前端 WS `/ws/clusters/{id}/namespaces/{ns}/pods/{pod}/logs?token=`（`logs.py:151-186`）→ api-server 用 `httpx.AsyncClient(timeout=None)` 流式转发到 executor 9099 `/logs`（`logs.py:105-148`）→ `PodLogs(follow=true, timestamps=true)` 逐 32KB flush（`logstream/server.go:113-186`）。**无总时长/总字节上限**，靠客户端断开结束。
3. **聚合/SSE**：`.../workloads/{kind}/{name}/logs/stream`（`workload_logs.py:277-302`）——先快照再**每 2s 轮询**每个 Pod 的 tail 500 行（`STREAM_TAIL=500`、`STREAM_INTERVAL=2.0`，`:42-45`），并发上限 `_AGG_SEMAPHORE=6`（`:40`），**无连接数上限、无总超时**（生成器 `_stream_events` 在 `:225-276`）。
4. **整份/时间段下载**：`.../logs/download?all=1|start&end`（`resources.py:2830-2883`）走 9099 `all=true`（timeout 120s）或 agent 隧道 `GetPodLogs(timestamps, since_seconds)`；**整份日志一次性读进 api-server 内存**（无字节上限），只有 gRPC 4MiB 默认上限做「意外保护」。

**Events**

| 能力 | 实现 | 限制 |
|---|---|---|
| Pod 实时事件 | `GetPodEvents`（`pod_list_events.go:328-365`） | k8s 侧 `List` 后**硬编码截断 100 条**（`:361-363`），无时间过滤（依赖 apiserver event-ttl，默认 1h） |
| Pod 历史事件 | 平台自存 `k8s_events`，窗口 7 天，`limit 200`（`resources.py:615-649`） | 依赖 eventstore collector |
| 工作负载维度事件 | `k8s_events` 按 owner/前缀匹配，`hours<=720`，`limit 500`（`resources.py:651-692`） | 前缀匹配可能误命中同名服务 |
| 实时事件面板 | `workload-events-realtime`（`resources.py:342-393`） | 见上 |

### 2.6 Deployment 管理：哪些真能执行、入口在哪、是否走审核

| 操作 | 状态 | 入口（前端 → API → RPC） | 审批 | 审计 |
|---|---|---|---|---|
| 滚动重启（deployment/statefulset/daemonset） | 已实现 | `WorkloadsList/WorkloadDetail` → `POST /resources/.../workloads/rollout` 或 `/workloads/recreate` → `RolloutWorkload`（`workload_ops.go:105-158`） | 无 | **无** |
| 扩缩容（含缩到 0） | 已实现 | `POST /workloads/scale`、`POST /workloads/batch` → `ScaleWorkload`（`workload_ops.go:79-104`） | 无（批量 delete 需 `confirm=true`） | 批量有，单个无 |
| 回滚（undo / 指定 revision） | 已实现 | `POST /.../workloads/{kind}/{name}/rollback` → `RollbackWorkload` → `undoDeployment`（按 ReplicaSet template 回写，`workload_ops.go:166-232`） | 无 | **有**（`resources.py:1422-1443`） |
| 镜像更新 | 已实现 | `POST /workloads/{kind}/{ns}/{name}/image`（读 YAML → 改 image → SSA，`resources.py:1487-1524`） | 无 | **无** |
| 探针编辑 / 调度到节点 / 挂载 / 环境变量 | 已实现 | `/workloads/probe`、`/schedule`、`/mounts`、`/env` | 无 | 无 |
| HPA 创建/更新/删除 | 已实现 | `/hpa/apply`、`DELETE /hpa/{ns}/{name}` → `hpa_ops.go` | 无 | 无 |
| 删除工作负载 / PVC / Secret / Service | 已实现 | `DELETE /workloads/...`、`DELETE /objects/...` → `DeleteResource`（`pod_list_events.go:494-526`） | **无**（仅 `confirm` 前端弹窗） | 无 |
| **灰度发布（金丝雀/流量切分）** | **未实现** | 无入口 | — | — |
| 「重新创建」（先建新再删旧） | 已实现 | `POST /workloads/recreate` → `recreateDeployment`（`workload_ops.go:361-404`） | 无 | 无 |

> 关于「灰度」：`internal/executor/gray_release.go` 的文件名容易误解，其内容是**修复计划按 blast radius 分 1/10/10 批、批间等 30s/60s、连续失败 2 步或错误率 >5% 熔断**（`gray_release.go:33-125`、`circuit_breaker.go:68-93`），与 Deployment 的灰度发布无关。全仓 `灰度` 只命中这里与「提示词模板灰度比例」（`PromptTemplates.tsx:486`）。

### 2.7 执行结果回传与错误处理

- **单发 RPC**：Go 侧统一返回 `success=false + error` 而不是 gRPC error（如 `ExecuteCommand`，`server.go:128-139`），但 `getExecutor` 失败会返回 `codes.Internal`（`:71-79`）→ api-server 侧 `try/except` 统一转成 `{"success": False, "error": str(e)}`。
- **流式**：`ExecutePlan` 每步 `notify` 推一帧（`step_index/success/output/message/finished/circuit_triggered/rollback_command`），结束时再推 `finished=true`（`server.go:141-165`）；`BatchExecute` 逐集群继续（部分失败不中断，`:206-224`）。
- **部分失败**：灰度执行器「第一步失败即整体中止」并写 `execution_logs`（`gray_release.go:88-99`）；`RollbackPlan` 逆序执行、遇错即停并返回已执行列表（`rollback.go:35-62`）。
- **超时分层（实测值）**：默认 `TIMEOUT=30s`（`executor_client.py:547`）；`exec_in_pod` gRPC 侧 120s（`:742`），Go 侧内部还有 90s `context.WithTimeout`（`exec_in_pod.go:79-84`）；`jvm_diagnose` 90s（`:604`）；`rollback/apply/recreate` 120s；`execute_plan` 600s（`:624-646`）；`query_prometheus` 60s；Prometheus range 60s。
- **已知缺口**：① 前端 axios 全局 30s（`frontend/src/services/api.ts:10-11`）与后端 120/600s 不匹配；② 全局 `asyncio.Semaphore(8)` 把后台循环与用户请求混在一起（`executor_client.py:23-32`），长任务可把交互请求排队到前端超时；③ gRPC 未设消息上限，大响应直接 `RESOURCE_EXHAUSTED`（见 K8S-12）。

### 2.8 危险操作清单与防护

| 危险操作 | 入口 | 当前防护 | 缺口 |
|---|---|---|---|
| 容器内执行任意命令（单命令） | `POST .../pods/{pod}/exec`（`resources.py:1150`） | 拦 shell 解释器 + 拦 `\| > < ; $( \` && \|\|`（`exec_in_pod.go:22-60`）；90s 超时 | 允许 `cat /var/run/secrets/.../token`、`env`、`curl` 等；**无审计、无输出上限**（`bytes.Buffer` 全量累积，`exec_in_pod.go:75-83`） |
| 交互式 shell（TTY） | WS `.../exec?token=`（`terminal.py:38-99`） | `operate` 权限；executor 侧 5 分钟静默断开（`exec_stream.go:43`、`server.go:1087-1100`） | **无录像/无审计**（宿主机终端有录像 `host_session_record_service`，容器终端没有） |
| 删除工作负载/PVC/Secret | `DELETE /workloads/...`、`DELETE /objects/...` | `require_operator` + `operate` 权限 | **无审批、无审计**；PVC 删除不可逆 |
| 节点 cordon/drain/label/taint/isolate | `POST /nodes/...` | `operate`；drain 需 `confirm=true`；保留域（`*.kubernetes.io`）默认禁改；跳过 DaemonSet/mirror/Succeeded（`execute.go:646-704`） | **只校验「任意 ns 的 operate」**（跨租户）；drain/isolate 影响全集群 |
| 缩到 0 | `POST /workloads/scale` | replicas>=0；记住原副本（Redis 30 天） | 无审批/无审计 |
| apply 任意 YAML | `/workloads/apply`、`/objects/{kind}/apply`、`/pods/{pod}/yaml` | Kind 一致性校验 | **namespace 由 YAML 覆盖**（K8S-01） |
| 删除 namespace | 无 API 入口 | API kind 白名单不含 namespace | Go RPC 仍支持（`pod_list_events.go:499`）——9096 无鉴权时可被直接调用 |
| JVM heapdump/jfr | `POST .../jvm/diagnose` | `operate` 权限 | 产物落 executor `/tmp/autohealops-jvm`，`/files?path=` **无鉴权下载**（K8S-07） |
| 批量操作（≤200 个） | `POST /workloads/batch` | 逐 ns 鉴权 + `confirm`（delete） | 有审计（`resources.py:1116`） |

### 2.9 前端有但后端没有的能力（逐个核对）

**核对方法**：把 `frontend/src/services/api.ts` 中所有含 `cluster/resource/pod/workload/node/object` 的 URL 模板抽出（约 70 条），与 `resources.py`（79 路由）+ `clusters.py`（21）+ `service_accounts.py`/`monitoring.py`/`jvm.py`/`workload_logs.py`/`logs.py`/`terminal.py` 的路由逐一对照。

**结论：没有发现「前端调用了但后端完全没有实现」的 K8s 能力。** 逐项说明：

| 项 | 判定 | 证据/备注 |
|---|---|---|
| 所有 `pages/cluster/*` 页面按钮（重启/扩缩容/回滚/改镜像/HPA/挂载/环境变量/隔离/节点操作/drain/YAML/日志/终端） | 后端全部有对应路由 | 例：drain UI `NodesPage.tsx:466-476` ↔ `resources.py:2802-2829` |
| 前端「发布」按钮（`WorkloadsList.tsx:441`，受 `publish_enabled` 门控） | 有，但走 **Jenkins 发布**，不是 K8s 灰度 | `api/release.py:462 trigger_release`；`resources.py:404-417` 计算 `publish_enabled` |
| 前端「弹性伸缩」Tab（HPA） | 有；**老 agent 集群会失败**，UI 未按能力置灰 | 后端有 `check_agent_feature`（`executor_client.py:197-215`），前端只显示错误文案 |
| 前端 StorageConfig「存储类」 | 只能编辑信息/删除，**没有创建**（文件头注释明说） | `StorageConfig.tsx:4`「存储类不提供创建」 |
| `execPod()`（`POST .../pods/{pod}/exec`） | **前端死代码**：api.ts 导出但无页面引用（`grep execPod` 仅命中定义与 PodsList 的本地 state 名） | `api.ts:439-440` |
| `GET /clusters/{id}/agent-upgrade`（返回升级命令文本） | **后端孤儿接口**：前端只用 `POST .../agent/upgrade` | `clusters.py:578-616` |
| `POST .../pods/{pod}/jvm/diagnose`（resources.py）vs `/jvm/.../diagnose` | 两套并存，前端用后者 | `resources.py:452`、`jvm.py:456` |
| 命名空间创建/删除 | 前端无入口、后端也没有 | 一致（能力缺口，不是前后端不一致） |

---

## 3. 功能状态清单

| # | 功能 | 状态 | 证据（文件:行） | 备注 |
|---|---|---|---|---|
| 1 | kubeconfig 直连接入（含多 context 批量） | 已实现（有代码依据） | `clusters.py:149-205,660-718` | 幂等键 = apiserver server URL |
| 2 | kubeconfig 加密存储 | 已实现（有代码依据） | `security.py:18-31`；`k8sclient/crypto.go:22-56` | AES-256-GCM，单密钥 |
| 3 | kubeconfig 回显/下载 | 完全未实现 | `clusters.py:787-806` | 有意为之（安全正向） |
| 4 | agent push 接入（隧道 + mTLS） | 已实现（有代码依据） | `clusters.py:212-500`；`agent_gateway.py:253-350` | — |
| 5 | agent pull 接入 | 部分实现 | `executor_client.py:378-464`；`cmd/agent/main.go:207-220` | 推送类能力（watcher/指标/事件）不启用 |
| 6 | in-cluster 自动接入 | 部分实现 | `bootstrap/incluster.go:33-106` | `k8s/` 无平台自身 RBAC → 权限不足 |
| 7 | 集群健康检查 | 已实现（有代码依据） | `health/checker.go:44-58` | 仅 `ServerVersion`，权限不足时仍 healthy |
| 8 | namespace 列表 | 已实现（有代码依据） | `resources.py:181-243` | 快照 + 实时回退 + 300s 缓存 |
| 9 | namespace 创建/删除/配额编辑 | 完全未实现 | 全仓无路由 | — |
| 10 | namespace/集群级权限过滤 | 部分实现 | `user_permission_service.py:53-100` | 应用层 SQL；空 ns 语义宽松；多个端点漏检 |
| 11 | Pod 列表 / 详情 / YAML | 已实现（有代码依据） | `resources.py:395-401,604-613,694-704` | 无分页 |
| 12 | Pod 日志 tail（≤2000 行） | 已实现（有代码依据） | `resources.py:546-577`；`pod_ops.go:19-55` | 30s 超时 |
| 13 | Pod 日志实时跟随（WS） | 已实现（有代码依据） | `logs.py:151-186`；`logstream/server.go:113-186` | 无总超时；9099 无鉴权 |
| 14 | 工作负载聚合日志 + SSE 跟随 | 已实现（有代码依据） | `workload_logs.py:225-302` | 2s 轮询，无上限 |
| 15 | 日志整份/时间段下载 | 部分实现 | `resources.py:2830-2883` | 全量进内存；agent 需新版本 |
| 16 | Events（实时 + 7 天历史） | 部分实现 | `pod_list_events.go:328-365`；`resources.py:615-692` | 实时硬编码 100 条 |
| 17 | Deployment 重启 | 已实现（有代码依据） | `workload_ops.go:105-158` | 无审计 |
| 18 | 扩缩容（含 0） | 已实现（有代码依据） | `workload_ops.go:79-104` | 无审计 |
| 19 | 回滚（undo/指定 revision） | 已实现（有代码依据） | `workload_ops.go:166-232` | 仅 deployment；有审计 |
| 20 | 镜像更新 | 已实现（有代码依据） | `resources.py:1487-1524` | 读改写，无审计 |
| 21 | 灰度/金丝雀发布 | 完全未实现 | `gray_release.go:1-16`（实为修复计划分批） | — |
| 22 | HPA 管理 | 已实现（有代码依据） | `hpa_ops.go`；`resources.py:1290-1376` | agent 能力门控 |
| 23 | exec（非交互单命令） | 部分实现（高风险） | `exec_in_pod.go:22-60` | 注释称「只读白名单」，实际任意二进制 |
| 24 | 交互终端（全 shell） | 已实现（有代码依据） | `terminal.py:38-99`；`exec_stream.go:68-140` | 无录像/无审计 |
| 25 | 节点 cordon/uncordon/label/taint/isolate | 已实现（有代码依据） | `resources.py:2453-2737`；`execute.go:439-470` | 有审计；只校验任意 ns 权限 |
| 26 | 节点 drain（PDB 感知） | 已实现（有代码依据） | `execute.go:646-704`；`resources.py:2815-2829` | 无审计（仅 `confirm`） |
| 27 | PVC/ConfigMap/Secret/Service/Ingress/StorageClass 管理 | 已实现（有代码依据） | `resources.py:705-935`；`misc_resources.go` | **Secret 明文返回** |
| 28 | ServiceAccount 列表 | 已实现（有代码依据） | `service_accounts.py:23-31` | 集群范围可读 |
| 29 | 云原生 RBAC（ClusterRole/RoleBinding 下发） | 部分实现 | `rbac_service.py:120-160`；`internal/rbac/rbac.go` | 只下发不执行（无 impersonation 运行链） |
| 30 | 告警来源（Pod/Node watcher） | 已实现（有代码依据） | `watcher/manager.go:36-60`；`pod_watcher.go`；`node_watcher.go` | 全 ns 监听，无用户维度 |
| 31 | 集群巡检 | 已实现（有代码依据） | `clusters.py:952-962`；`inspection_service` | **无集群权限校验** |
| 32 | 操作审计（K8s） | 部分实现 | `resources.py:431,1116,1424,2580,2652,2699,2736` | 仅 4 类操作 |

---

## 4. 问题清单（按等级排序）

```
问题编号：K8S-01
问题名称：应用 YAML 时 metadata.namespace 覆盖鉴权用的 namespace，可越权写其他命名空间
问题等级：P0
所属模块：api-server / resources API + k8s-executor / executor
问题描述：
  所有 apply 类接口的鉴权用「路径或请求体里的 namespace」，但真正落到 K8s 时，
  Go 侧优先采用 YAML 内的 metadata.namespace 覆盖该值。用户只要在 A 命名空间有 operate，
  就能在请求里把 YAML 的 namespace 写成 B，从而在 B 里创建/修改 Deployment、Pod、
  ConfigMap、Secret、Service、Ingress、PVC、ServiceAccount（受 kind 白名单限制）。
  apply_object_yaml 的命名空间还是靠「逐行扫 namespace:」提取，JSON 风格 YAML
  （{"metadata":{"namespace":"b"}}）根本提取不到 → ns 为空 → 校验退化为「任意 ns 有权限即可」。
代码位置：
  api-server/app/api/resources.py:590-603   （apply_pod_yaml：只按路径 ns 鉴权）
  api-server/app/api/resources.py:744-768   （apply_object_yaml：逐行扫 namespace:，可绕过）
  api-server/app/api/resources.py:912-936   （object_apply：取 object.metadata.namespace 鉴权，但 Go 侧仍以 YAML 为准）
  api-server/app/api/resources.py:1002-1022 （apply_workload：payload.namespace 鉴权）
  k8s-executor/internal/executor/pod_ops.go:97-102      （if ns := obj.GetNamespace(); ns != "" { namespace = ns }）
  k8s-executor/internal/executor/pod_list_events.go:461-472（同上，工作负载/配置对象）
涉及函数或类：apply_pod_yaml、apply_object_yaml、object_apply、apply_workload、
  Executor.ApplyPodYaml、Executor.ApplyWorkloadYaml
调用链：
  POST /api/v1/resources/clusters/{id}/workloads/apply
  → _deny_cluster(user, cluster_id, payload.namespace=A, "operate") 通过
  → ApplyWorkloadYaml(kind, ns=A, yaml 内含 metadata.namespace=B)
  → obj.GetNamespace()=B 覆盖 → dynamic.Resource(...).Namespace(B).Apply(...)
根本原因：鉴权对象（请求参数）与执行对象（YAML 内容）不是同一个事实源，服务端未做一致性校验
可能影响：租户/项目间横向越权写入；可篡改他人命名空间的 Secret/ConfigMap、替换镜像、
  注入 sidecar、改 Service 指向；也能借此拿到他人命名空间的凭据（写入后读回）
修改建议：
  1) Go 侧：当入参 namespace 非空且与 obj.GetNamespace() 不一致时直接报错（拒绝，而非覆盖）；
  2) Python 侧：统一以「解析后的对象 namespace」做鉴权，并在 apply 前把解析结果回传校验；
  3) 禁止在对象 YAML 里携带与请求不一致的 namespace（cluster-scoped kind 例外白名单）；
  4) 加单测：A 用户对 ns A 有权限、YAML 写 ns B，必须 403。
验证方法：用只授权 ns-a 的 operator 账号，POST /workloads/apply，body.namespace=ns-a，
  yaml.metadata.namespace=ns-b；改前返回 200 且 ns-b 出现对象，改后必须 403。
是否需要立即处理：是
```

```
问题编号：K8S-02
问题名称：namespace 参数留空时权限校验退化为「任意一个命名空间有权限」，导致跨命名空间读取
问题等级：P0
所属模块：api-server / 权限服务 + resources API
问题描述：
  check_cluster_permission 明确定义「namespace 为空 = 只校验集群级（任意 namespace 授权即可）」，
  而下列接口默认 namespace=""，直接返回**整个集群**的数据：
  /resources/clusters/{id}/pods、/workloads、/misc-resources、/namespaces、/namespace-list、
  /detail、/nodes、/clusters/{id}/service-accounts。
  即：只被授权 qa-eft 一个命名空间的用户，能看到生产命名空间的 Pod 名/IP/节点、全部
  Deployment、全部 Secret/ConfigMap 名称与数据项数、StorageClass、节点清单。
代码位置：
  api-server/app/services/user_permission_service.py:56-58（语义定义与实现）
  api-server/app/api/resources.py:604-613（cluster_pods，namespace 默认 ""）
  api-server/app/api/resources.py:1246-1268（list_workloads，namespace 默认 ""）
  api-server/app/api/resources.py:705-730（misc_resources，namespace 默认 ""）
  api-server/app/api/resources.py:181-243（namespaces / namespace-list）
  api-server/app/api/service_accounts.py:23-31（service-accounts，namespace 默认 ""）
涉及函数或类：check_cluster_permission、_deny_cluster、cluster_pods、list_workloads、
  misc_resources、namespace_list、service_accounts
调用链：
  GET /api/v1/resources/clusters/1/pods?namespace=
  → _deny_cluster(user, 1, "", "read") → 任意 ns 授权即 True
  → executor ListPods(namespace="") → metav1.NamespaceAll 全量返回
根本原因：把「集群级授权」与「命名空间级授权」混用同一函数，且列表接口没有做结果侧过滤
可能影响：跨租户/跨环境信息泄露（Pod、工作负载、Secret 名称、节点、SA）；
  为后续定向攻击提供完整资产清单
修改建议：
  1) 区分两个函数：check_cluster_access（集群级，需显式 cluster-level 授权）
     与 check_namespace_access（必须命中具体 ns）；
  2) 列表接口在 namespace 为空时，先取「用户可见 ns 集合」并下推到 executor（或结果侧过滤）；
  3) 若产品确实允许「看全集群」，需要新增显式的 cluster_read 授权位，不能用「任意 ns」代替；
  4) 前端默认传当前选中 namespace，而不是空。
验证方法：只授权 ns-a 的 viewer 调 /pods?namespace=（空），应只返回 ns-a 的 Pod；当前返回全部。
是否需要立即处理：是
```

```
问题编号：K8S-03
问题名称：Secret 数据被解码成明文返回给仅有 read 权限的用户（含 imagePullSecret 的 dockerconfigjson）
问题等级：P0
所属模块：k8s-executor / executor + api-server / objects API
问题描述：
  GetWorkloadYaml 对 kind=secret 会调用 revealSecretData：把 data 里的 base64 解码成
  stringData（「如 imagePullSecret 的 .dockerconfigjson 解码后可直接看到用户名/密码」——
  源码注释原文）。object_detail 又把 stringData/ data 拼成 data 字段返回。
  这两个接口只需要 read 权限（require_any_user + _deny_cluster(..., "read")），
  没有二次确认、没有审批、没有审计、没有脱敏开关。
代码位置：
  k8s-executor/internal/executor/pod_list_events.go:439-441（GetWorkloadYaml 调 revealSecretData）
  k8s-executor/internal/executor/pod_list_events.go:528-554（revealSecretData 实现与注释）
  api-server/app/api/resources.py:731-742（GET objects/{kind}/{ns}/{name}/yaml，read 权限）
  api-server/app/api/resources.py:785-817（object_detail，明确「Secret 为明文」）
涉及函数或类：Executor.GetWorkloadYaml、revealSecretData、object_yaml、object_detail
调用链：
  GET /api/v1/resources/clusters/1/objects/secret/prod/db-password/yaml
  → _deny_cluster(user, 1, "prod", "read") 通过（viewer 即可）
  → GetWorkloadYaml → revealSecretData → YAML 含明文 stringData → 直接返回前端
根本原因：把「查看 Secret」当成普通只读能力，未按「凭据读取」单独授权/审计
可能影响：集群内所有 Secret（数据库密码、镜像仓库凭据、TLS 私钥、SA token Secret）
  对任何 viewer/developer 可见 → 凭据泄露、横向移动到外部系统
修改建议：
  1) Secret 读取单独鉴权（如 require_operator + 显式 secret_read 授权），并按次审计；
  2) 默认返回脱敏视图（只列 key 与长度），明文需二次确认 + 一次性授权（类似 vault）；
  3) 取消「自动 base64 解码」，改为前端按需 reveal 且必须走后端受控接口；
  4) 记录 who/when/which secret（审计哈希链）。
验证方法：viewer 账号 GET 任意 Secret 的 /yaml 与 /detail，应无明文；当前可直接读到明文。
是否需要立即处理：是
```

```
问题编号：K8S-04
问题名称：9096 内部网关无鉴权，可对任意在线 agent 集群直接下发任意 RPC（并可无门槛签发 agent 客户端证书）
问题等级：P0
所属模块：Agent 网关 / 证书签发 / 部署网络策略
问题描述：
  agent-gateway 的内部 gRPC（9096，明文）没有任何鉴权（无 token、无 mTLS、无来源校验），
  暴露 4 个方法：TunnelCall / TunnelStreamCall / IsConnected / IssueClientCert。
  ① 主风险：TunnelCall(cluster_id=N, method, payload) 会把 payload 原样转给**已在线**的
    N 号 agent 并同步回包（agent_gateway.call → 该连接的出站队列 → agent 本地
    ExecutorService）。也就是说，集群内任意 Pod 只要能连 9096，就能对任意 agent 集群
    执行 DeleteResource（支持删 namespace/deployment/pod/PVC/webhook…）、ExecInPod
    （任意命令）、ApplyWorkloadYaml——**完整绕过平台的 JWT 鉴权、命名空间授权与审批**。
  ② 次风险：IssueClientCert(cluster_id=N, days=1825) 无鉴权即可为任意集群签发客户端证书
    （CA 私钥就在网关 PVC 上）。单独持有证书仍不能注册（注册还要 agent token，
    agent_gateway.py:44-58），但它把「token 泄露」直接放大为「可长期冒充该 agent」，
    且能给尚无 agent 的集群预置证书。
  而 agent 的 ClusterRole 是 apiGroups:["*"] resources:["*"] + impersonate，
  等于被管集群 cluster-admin。此外 k8s/networkpolicy.yaml 只保护 k8s-executor 与
  ai-service，**没有给 agent-gateway 加任何 NetworkPolicy**，9096 是 ClusterIP，
  集群内任意 Pod 可达（平台集群一旦有业务 Pod 被入侵，即等于拿到所有被管集群的写权限）。
代码位置：
  api-server/app/agent_gateway_service.py:23-70（TunnelCall/TunnelStreamCall 无鉴权）
  api-server/app/agent_gateway_service.py:71-86（IssueClientCert：cluster_id 来者不拒）
  api-server/app/agent_gateway_service.py:95-104（add_insecure_port，明文）
  api-server/app/agent_gateway.py:135-157（call：按 cluster_id 找在线连接并阻塞等回包）
  api-server/app/agent_gateway.py:44-58（注册校验 token —— 证书单独不足，需 token）
  api-server/app/api/clusters.py:441-467（agent ClusterRole: ["*"]/["*"] + impersonate）
  k8s/networkpolicy.yaml:1-16（只覆盖 executor 与 ai-service）
  k8s/services.yaml:84-99（agent-gateway 9096 ClusterIP）
涉及函数或类：AgentGatewayInternalServicer.TunnelCall / TunnelStreamCall / IssueClientCert、
  agent_gateway.call、agent_pki.issue_client_cert
调用链：
  集群内任意 Pod → grpc.insecure_channel("agent-gateway:9096")
  → TunnelCall(cluster_id=1, method="DeleteResource", payload=DeleteResourceRequest)
  → agent_gateway.call → 1 号 agent 出站队列 → agent 本地 ExecutorService
  → 以 agent 的 */* SA 删除目标资源 → 回包
根本原因：内部网关按「内网即可信」设计，且把「签发证书」这种高权操作与「转发调用」放在同一无鉴权端口
可能影响：被管集群完全失守（删任意资源、exec 任意 Pod、改任意工作负载）；
  且攻击面在平台集群内部（任何被入侵的业务 Pod 都能打）
修改建议：
  1) 9096 全部方法加 X-Internal-Token（独立密钥）或 mTLS 校验，并校验调用方 SA；
  2) IssueClientCert 单独端口/单独鉴权 + 审计（谁为哪个 cluster 签了证书），
     并限制调用方只能是 api-server；
  3) 给 agent-gateway 补 NetworkPolicy（仅允许 app=api-server 入站，端口级限制）；
  4) agent ClusterRole 收敛（见 ARCH-13），去掉 impersonate 或拆独立 SA；
  5) 平台侧对 TunnelCall 增加「方法白名单 + 调用来源」校验（纵深防御）。
验证方法：在平台集群内起一个临时 Pod，无凭据调用 IsConnected/TunnelCall/IssueClientCert，
  均应被拒绝；当前 IsConnected 与 IssueClientCert 会直接成功。
是否需要立即处理：是
```

```
问题编号：K8S-05
问题名称：节点级破坏性操作只校验「任意命名空间的 operate」，单租户操作员可排空整个集群节点
问题等级：P1
所属模块：api-server / 节点管理
问题描述：
  cordon/uncordon/drain/label/taint/isolate 全部是**集群级**影响（节点被 cordon 后所有租户
  的 Pod 都不能调度上去；drain 会驱逐该节点上所有命名空间的 Pod），但鉴权调用是
  _deny_cluster(user, cluster_id, required="operate")（namespace 为空）→ 只要用户在**任意一个**
  命名空间有 operate 权限就通过。
代码位置：
  api-server/app/api/resources.py:2453-2471（cordon/uncordon）
  api-server/app/api/resources.py:2473-2501（批量 cordon/uncordon）
  api-server/app/api/resources.py:2526-2583（节点标签）
  api-server/app/api/resources.py:2584-2658（节点污点）
  api-server/app/api/resources.py:2660-2738（节点隔离/解除）
  api-server/app/api/resources.py:2815-2829（drain，仅 confirm=true）
涉及函数或类：cordon_node、uncordon_node、batch_node_op、set_node_labels、set_node_taints、
  isolate_node、unisolate_node、node_drain
调用链：
  POST /resources/clusters/1/nodes/node-3/drain {confirm:true}
  → _deny_cluster(user, 1, "", "operate")（任意 ns operate 即通过）
  → execute_command("drain node-3") → executor.execDrain → cordon + Evictions
根本原因：节点是集群级资源，但平台没有「集群级 operate」这一授权位，复用了命名空间授权
可能影响：A 项目操作员可驱逐 B 项目全部 Pod（可用性攻击/误操作）；配合 isolate 可长期
  让节点不承载业务；跨租户破坏
修改建议：
  1) 节点/集群级操作要求显式 cluster_operate 授权（或仅 admin）；
  2) 高危操作（drain/isolate）走审批流（DB 侧已有 428 机制，可复用）；
  3) 对 drain 增加「影响面提示 + 二次确认 + 审计」（当前无审计）；
  4) 记录受影响 Pod 清单到审计详情。
验证方法：仅授权 ns-a 的 operator 调 drain，应 403；当前会真的驱逐 ns-b 的 Pod。
是否需要立即处理：是
```

```
问题编号：K8S-06
问题名称：K8s 破坏性操作无审批、绝大多数无审计（对比 DB 侧有 428 审核）
问题等级：P1
所属模块：api-server / resources API / 审计
问题描述：
  数据库侧删除/导入等操作返回 {"code":428,"message":"该操作需要审核"}（有审批流）；
  K8s 侧同类危险操作全部直接执行：删除工作负载/PVC/Secret/Service、缩容到 0、
  改镜像、apply 任意 YAML、容器内 exec、节点 drain。
  审计只有 4 类：Evicted 清理、工作负载批量操作、工作负载回滚、节点元数据变更。
  delete_workload / scale_workload / update_workload_image / apply_*_yaml / exec_pod
  都没有 audit_service 调用（resources.py 全文件 grep `audit` 仅 12 行命中，集中在
  import/辅助函数与上述 4 类操作）。
代码位置：
  api-server/app/api/resources.py:1137-1148（delete_workload，无审计）
  api-server/app/api/resources.py:1269-1288（scale_workload，无审计）
  api-server/app/api/resources.py:1487-1524（update_workload_image，无审计）
  api-server/app/api/resources.py:590-603,744-768,1002-1022（apply 系列，无审计）
  api-server/app/api/resources.py:1150-1170（exec_pod，无审计）
  api-server/app/api/resources.py:1116（批量有审计，作为对照）
  api-server/app/api/db_connections.py:425（DB 侧 428 审批，作为对照）
涉及函数或类：delete_workload、scale_workload、update_workload_image、exec_pod、
  audit_service.create_audit_record
调用链：
  DELETE /resources/clusters/1/workloads/deployment/prod/payment
  → 仅 require_operator + operate 权限 → 直接 executor.DeleteResource → 完成，无留痕
根本原因：K8s 能力迭代优先做了「能用」，审批/审计只补了少数几个入口
可能影响：误删/恶意删除无法追溯责任；等保/审计要求不满足；事后无法复盘
修改建议：
  1) 建「K8s 危险动作 → 是否需审批」矩阵（delete/pvc delete/scale 0/exec/drain 需审批）；
  2) 全部写操作统一在 API 层包一层 audit（记录 operator/cluster/ns/kind/name/前后差异）；
  3) 复用现有 approval_flows，而不是新造；
  4) exec 记录命令 + 输出摘要（不落全量输出，避免日志膨胀）。
验证方法：删一个 Deployment 后查审计表，应有记录；当前无。
是否需要立即处理：是
```

```
问题编号：K8S-07
问题名称：9099 日志流/产物端口无鉴权，且可下载 JVM heapdump（堆内存含凭据）
问题等级：P1
所属模块：k8s-executor / logstream
问题描述：
  9099 的 HTTP 服务注册了 /healthz、/logs、/watch、/prom/range、/files，全部**没有
  Authorization/token 校验**。/files?path=<name> 直接把 jvm.ArtifactDir（默认
  /tmp/autohealops-jvm）里的文件发出去 —— 这个目录里是 jfr 与 **heapdump**。
  heapdump 是整个 JVM 堆的内存快照，通常包含数据库密码、token、session、密钥。
  K8s 形态靠 NetworkPolicy（仅 app=api-server）兜底；但本地 start.sh 直接监听
  0.0.0.0:9099（config 默认 `:9099`），同网段任何人知道文件名即可下载。
代码位置：
  k8s-executor/internal/logstream/server.go:42-46（路由注册，无中间件）
  k8s-executor/internal/logstream/server.go:71-89（handleFile：只防路径穿越，无鉴权）
  k8s-executor/internal/jvm/jvm.go:23-29（ArtifactDir = os.TempDir()/autohealops-jvm，0644）
  k8s-executor/internal/config/config.go:78-80（LogStreamAddr 默认 ":9099"）
  k8s/networkpolicy.yaml:1-16（唯一防护，且只覆盖 9099 所在 Pod）
涉及函数或类：logstream.Server.handleFile / handleLogs / handlePromRange、jvm.ArtifactDir
调用链：
  GET http://<host>:9099/files?path=heapdump-<pod>-<ts>.hprof → 直接返回二进制
根本原因：内部端口按「集群内可信」设计，未考虑本机/多租户/横向移动
可能影响：日志内容泄露（可能含凭据/业务数据）；heapdump 泄露 = 容器内全部运行时秘密；
  且产物无清理策略，长期占用磁盘
修改建议：
  1) 9099 加 X-Internal-Token（与 api-server 对齐）或改 mTLS；
  2) 默认监听 127.0.0.1，集群内再 0.0.0.0（用环境变量切换）；
  3) /files 增加「一次性下载 token + 过期时间」，路径改为不可猜测的随机名；
  4) heapdump 产物 TTL（如 24h）自动清理，并写审计；
  5) NetworkPolicy 加端口级限制。
验证方法：curl :9099/files?path=x 不带任何头，若返回 400/401 才算修好；当前会尝试读取文件。
是否需要立即处理：是
```

```
问题编号：K8S-08
问题名称：告警列表与仪表盘统计不做集群/命名空间维度的用户过滤，跨租户泄露
问题等级：P1
所属模块：api-server / 告警
问题描述：
  GET /api/v1/alerts 只按查询参数过滤（cluster_id/severity/status/...），**没有**
  get_visible_cluster_ids 或 check_cluster_permission，任何登录用户（含 viewer）都能看到
  全部集群、全部命名空间的告警（含 Pod 名、命名空间、错误消息、服务名）。
  clusters 列表做了过滤（clusters.py:753-761），说明过滤能力存在，只是告警链路没用。
代码位置：
  api-server/app/api/alerts.py:60-80（list_alerts 定义与查询构建）
  api-server/app/api/alerts.py:79（select(Alert)，无条件追加可见性）
  api-server/app/api/clusters.py:753-761（对照：clusters 列表有 visible_ids 过滤）
  api-server/app/services/user_permission_service.py:24-49（get_visible_cluster_ids 已实现但此处未调用）
涉及函数或类：list_alerts、get_visible_cluster_ids
调用链：
  GET /api/v1/alerts?status=all&page_size=100 → select(Alert) 全表 → 返回
根本原因：告警是「运维全局视角」，产品上未区分租户；但平台已有项目/命名空间授权模型，二者冲突
可能影响：跨项目信息泄露（哪个服务在报错、Pod 名、命名空间拓扑）；viewer 也能看
修改建议：
  1) 在 list_alerts 前加 visible_ids 过滤（与 clusters 列表一致），并支持按 namespace 授权过滤；
  2) 统计接口（stats/screens）同样处理；
  3) 若确实需要全局视角，只对 admin/on-duty 角色开放，并在 UI 明确标注。
验证方法：viewer（只授权 ns-a）调 /alerts?status=all，应只看到 ns-a 的告警；当前看到全部。
是否需要立即处理：是
```

```
问题编号：K8S-09
问题名称：容器内 exec 实际允许任意命令（仅拦 shell 解释器），无输出上限，注释与实现不一致
问题等级：P1
所属模块：k8s-executor / executor（ExecInPod）
问题描述：
  API 层注释写「只读白名单」，Go 侧注释写「允许任意命令」——以 Go 为准：
  checkCommand 只做两件事：拒绝 sh/bash/python 等解释器；拒绝 | > < ; $( ` && ||。
  因此 `cat /var/run/secrets/kubernetes.io/serviceaccount/token`、`env`、`curl`、
  `wget`、`base64`、`find`、`tar`、`cat /proc/1/environ` 全部可用；
  输出用 bytes.Buffer 全量累积（无 maxBytes），`cat /dev/zero` 之类会持续占用内存
  直到 90s 超时 → 单请求可打爆 executor 内存（limit 1Gi）。
  exec 结果既不审计也不限长，前端还可能把整段 base64（如 tar 打包）当文本渲染。
代码位置：
  k8s-executor/internal/executor/exec_in_pod.go:22-39（blockedShellVerbs：只拦解释器）
  k8s-executor/internal/executor/exec_in_pod.go:44-60（checkCommand：只拦 shell 元字符）
  k8s-executor/internal/executor/exec_in_pod.go:75-87（bytes.Buffer 全量 + 90s 超时）
  api-server/app/clients/executor_client.py:733-748（gRPC 侧 120s）
  api-server/app/api/resources.py:1150-1170（注释「只读白名单」）
涉及函数或类：Executor.ExecInPod、checkCommand、exec_pod、_pod_exec
调用链：
  POST /resources/clusters/1/namespaces/prod/pods/x/exec {"command":"cat /var/run/secrets/..."}
  → _deny_cluster(operate) → ExecInPod → remotecommand exec → 返回 token 明文
根本原因：为「应用诊断要能在容器里装工具、改配置」放开了任意命令（老板口径），
  但缺少输出上限、审计与敏感路径防护
可能影响：容器内凭据/SAToken 窃取（可进一步访问 apiserver，取决于 SA 权限）；
  executor 内存 DoS；无审计无法追责
修改建议：
  1) 输出硬上限（如 1MiB，超出截断并标记）+ 流式读取；
  2) 敏感路径黑名单（/var/run/secrets、/proc/*/environ、*.key 等）或脱敏；
  3) 命令与结果摘要写审计；
  4) 修正 API 文档注释，避免误导安全评审；
  5) 把「装工具/改配置」这类写操作与诊断读操作分成不同权限位。
验证方法：exec `cat /var/run/secrets/kubernetes.io/serviceaccount/token` 应被拒或脱敏；
  exec `cat /dev/zero` 应在 1MiB 处截断且 executor 内存不飙。
是否需要立即处理：是
```

```
问题编号：K8S-10
问题名称：多个集群级接口漏做集群权限校验（关自愈/触发巡检/健康检查/读升级命令）
问题等级：P1
所属模块：api-server / clusters API
问题描述：
  clusters.py 中部分接口只校验角色（require_operator/require_any_user），没有
  check_cluster_permission：
  - PUT /{id}/auto-heal：任一 operator 可关闭**任意集群**的自动诊断/自愈（跨租户写，影响
    其他项目的故障处置能力）；
  - POST /{id}/inspect：任一 operator 可对任意集群触发巡检并**同步拿到报告**（含资源明细）；
  - GET /{id}/inspect/progress：任一用户可读任意集群巡检进度；
  - POST /{id}/health-check：任一 operator 可探测任意集群并改写其 status/version；
  - GET /{id}/agent-upgrade：任一用户可拿到任意集群的 agent namespace/镜像/版本。
  同一文件里 GET /{id}/inspection-reports 却加了权限校验（注释写着「P1 越权修复」），
  说明这是遗漏而不是设计。
代码位置：
  api-server/app/api/clusters.py:814-825（set_auto_heal，无 check）
  api-server/app/api/clusters.py:926-941（health_check，无 check）
  api-server/app/api/clusters.py:952-962（inspect_cluster，无 check）
  api-server/app/api/clusters.py:965-970（inspect_progress，无 check）
  api-server/app/api/clusters.py:578-616（agent_upgrade_command，无 check）
  api-server/app/api/clusters.py:972-982（对照：inspection-reports 有 check）
涉及函数或类：set_auto_heal、health_check、inspect_cluster、inspect_progress、
  agent_upgrade_command
调用链：
  PUT /api/v1/clusters/9/auto-heal {"enabled": false}
  → require_operator 通过 → 直接改库 → 集群 9 不再自动诊断/自愈
根本原因：鉴权只做了「角色级」，资源级授权是逐个接口手写的，容易漏
可能影响：跨租户关闭自愈（安全/稳定性）、巡检报告泄露、集群状态被第三方改写
修改建议：
  1) 统一用依赖注入式守卫（router 级 dependency 或装饰器）强制集群级校验，避免逐接口手写；
  2) 高危写操作（auto-heal 关闭）要求 admin 或集群级 manage；
  3) 加一个「路由覆盖测试」：遍历所有 /clusters/{id}/* 路由，断言都调用了 check_cluster_permission。
验证方法：只授权集群 1 的 operator 调 /clusters/2/auto-heal，应 403；当前返回 200。
是否需要立即处理：是
```

```
问题编号：K8S-11
问题名称：「云原生 RBAC 已接入」名不副实：impersonation 未接入运行链，实际仍用超管凭据 + 应用层自判
问题等级：P2
所属模块：api-server / rbac_service + k8s-executor / k8sclient
问题描述：
  rbac_service 会向被管集群下发 autohealops-read/operate/manager 三档 ClusterRole 与
  RoleBinding，并把 cluster.rbac_status 置为「RBAC 已接入（impersonation 启用）」。
  但 k8s-executor 的 ImpersonatedClient 全仓**无任何调用方**（只有定义），
  所有请求都用平台自身的 kubeconfig/SA（agent 侧是 */* 的 SA）。
  也就是说：集群 RBAC 只是「摆在那里的策略」，平台真正判权仍靠 Python 查授权表。
代码位置：
  api-server/app/services/rbac_service.py:137-146（状态文案「impersonation 启用」）
  k8s-executor/internal/k8sclient/manager.go:161-183（ImpersonatedClient，无调用方）
  api-server/app/api/clusters.py:451-454（agent ClusterRole 给 impersonate 权限）
  api-server/app/api/resources.py:31-35（实际鉴权：Python 查表）
涉及函数或类：rbac_service.setup_cluster_rbac、Manager.ImpersonatedClient、_deny_cluster
调用链：
  setup_cluster_rbac → SetupRBAC + ReconcileRoleBindings（只下发）
  用户请求 → _deny_cluster（Python 判定）→ executor 用超管 client 执行
根本原因：RBAC 先做了「接入与展示」，运行链切换未完成（或刻意保留超管以兼容全部功能）
可能影响：误导安全评审（以为有 K8s 侧兜底）；应用层漏检（如 K8S-02/03）无任何兜底；
  集群里多出一套无人使用的 RoleBinding，增加维护负担
修改建议：
  1) 要么真正把用户身份透传到 executor（ImpersonatedClient + 每请求带 user/groups），
     让 K8s 做最终判定；要么明确把 rbac_status 文案改为「策略已下发（未启用执行）」；
  2) 若走 impersonation，需要为「平台自身后台任务」保留 automation 身份，避免误伤；
  3) 加开关 rbac_mode=enforce/shadow，灰度验证后再切。
验证方法：在集群里查 audit 日志（apiserver audit），看请求的 user 是 autohealops:<username>
  还是平台 SA；当前应是后者。
是否需要立即处理：否（但需与产品对齐对外口径）
```

```
问题编号：K8S-12
问题名称：gRPC 服务无鉴权、无 TLS、无消息大小上限、无拦截器，大响应直接失败
问题等级：P2
所属模块：k8s-executor / grpc server
问题描述：
  cmd/executor 用裸 grpc.NewServer()（无 unary/stream 拦截器、无 MaxRecvMsgSize/
  MaxSendMsgSize、无 keepalive enforcement），监听 :9090 全接口、明文。
  默认 4MiB 上限意味着：整份大日志（GetPodLogs all）、大 YAML、misc-resources 全量
  列表在对象多时容易触发 RESOURCE_EXHAUSTED，而错误被统一包成 dict，
  前端只看到「获取失败」，很难定位是大小限制。
代码位置：
  k8s-executor/cmd/executor/main.go:103-107（grpc.NewServer() 无选项）
  k8s-executor/cmd/executor/main.go:113-120（net.Listen("tcp", ":"+GRPCPort)）
  k8s-executor/internal/grpc/server.go:71-79（错误统一 Internal，无大小/超时治理）
  k8s/networkpolicy.yaml:1-16（唯一防护）
涉及函数或类：main、grpc.NewServer、Server.getExecutor
调用链：
  api-server → :9090（明文，无 token）→ 任意 RPC
根本原因：内部服务按可信网络设计；proto 变更成本高（本机缺 protoc 插件）导致很多能力走 HTTP 旁路
可能影响：内网横向可直连 9090 执行任意 RPC（绕过 api-server 的鉴权与审批）；
  大对象场景功能不稳定
修改建议：
  1) 加 X-Internal-Token 拦截器（与 9099 一起做）；
  2) 显式设置 MaxRecvMsgSize（如 16MiB）并在错误里区分「超限」；
  3) 大列表改分页（Limit/Continue），日志走流式而不是 unary；
  4) 保留 NetworkPolicy，并考虑 mTLS。
验证方法：不带 token 直接 grpcurl :9090 调 ListPods，应被拒；当前可用。
是否需要立即处理：否（K8s 形态有 NetworkPolicy 兜底；本地形态与 K8S-07 同级处理）
```

```
问题编号：K8S-13
问题名称：灰度发布（金丝雀/流量切分）完全未实现，只有「修复计划分批执行」
问题等级：P2
所属模块：k8s-executor / executor、发布链路
问题描述：
  源码中唯一叫 gray 的文件 gray_release.go 实现的是：按 blast radius 把**修复计划步骤**
  分成 1/10/10 批，批间等 30s/60s，配合熔断器（连续 2 步失败或错误率 >5% 中止）。
  它与 Deployment 的灰度发布无关：没有 Ingress/Service 权重、没有 canary ReplicaSet、
  没有按百分比导流、没有指标对比与自动推进/回滚。
  发布链路是 Jenkins 触发（api/release.py），平台只负责「触发 + 记录 + 失败回滚」，
  不做流量编排。
代码位置：
  k8s-executor/internal/executor/gray_release.go:1-16,33-125（分批执行 + 熔断）
  k8s-executor/internal/executor/circuit_breaker.go:68-93（熔断条件）
  api-server/app/api/release.py:462-541（发布=Jenkins 触发）
  api-server/app/api/release.py:543-606（回滚=rollout undo）
涉及函数或类：GrayExecutor.ExecuteWithGrayStrategy、CircuitBreaker、trigger_release、rollback_release
调用链：
  （无灰度入口）→ 若做发布，走 POST /api/v1/release/trigger → Jenkins → 集群自行滚动
根本原因：产品优先级与实现成本（流量切分需要网关/服务网格支持）
可能影响：无法做小流量验证；「灰度」一词在文档/UI 中可能被误解为已支持
修改建议：
  1) 明确术语：把 gray_release 改名 plan_batch_executor，避免误导；
  2) 若要做真灰度：基于 Service/Ingress 权重（nginx-ingress canary annotation）或
     Argo Rollouts/Flagger（优先复用开源，避免自研）；
  3) UI 上标注「当前无灰度能力」。
验证方法：在 UI 与 API 中搜索灰度入口，均不存在。
是否需要立即处理：否
```

```
问题编号：K8S-14
问题名称：超时与并发治理缺陷：全局 8 信号量 + 600s 长任务 + 前端 30s + SSE 无上限
问题等级：P2
所属模块：api-server / executor_client、日志流
问题描述：
  ① acall 用模块级 asyncio.Semaphore(8) 限制所有对 executor 的调用（走 to_thread），
    后台循环（健康检查/慢 SQL/恢复收敛）与用户请求共享同一配额；
    execute_plan 单次最长 600s，一旦占满，交互请求排队到前端 30s 超时；
  ② 前端 axios 全局 30s，与后端 120/600s 不匹配（部分接口单独覆盖，属「按接口记忆」）；
  ③ 日志聚合 SSE 每 2s 轮询每个 Pod，无连接数上限、无总时长上限；每个连接持续占用
    线程池与 gRPC 配额，客户端不关就一直跑；
  ④ 日志 WS 转发用 httpx timeout=None，executor 侧不返回就一直挂着。
代码位置：
  api-server/app/clients/executor_client.py:23-32（Semaphore 8 + acall）
  api-server/app/clients/executor_client.py:624-646（execute_plan timeout=600）
  frontend/src/services/api.ts:10-11（axios 30s）
  api-server/app/api/workload_logs.py:40-45,225-276（SSE 2s 轮询、无上限）
  api-server/app/api/logs.py:119（httpx timeout=None）
涉及函数或类：acall、execute_plan、_stream_events、_pump_logs
调用链：
  10 个并发 execute_plan（各 600s）→ 占满 8 槽 → 普通资源接口排队 → 前端 30s 超时
根本原因：单池无优先级隔离；长任务没有统一的「异步任务 + 轮询」范式
可能影响：高峰期页面卡死/雪崩；后台任务把用户请求饿死；长连接堆积导致内存/句柄上涨
修改建议：
  1) 拆两个信号量（交互 8 / 后台 4），长任务不占交互配额；
  2) 长任务统一改「提交→task_id→轮询/SSE」（executions 已有轮询范式可复用）；
  3) SSE 加最大连接数、单连接最大时长（如 30min）与心跳超时回收；
  4) 前端长任务接口统一用 withLongTimeout 包装。
验证方法：压测 10 个 execute_plan 同时跑，测普通资源接口 P99 与失败率。
是否需要立即处理：否
```

```
问题编号：K8S-15
问题名称：in-cluster 自动接入缺少平台自身 RBAC 清单，healthy 但后续操作 forbidden
问题等级：P2
所属模块：部署清单 / bootstrap
问题描述：
  bootstrap.EnsureInCluster 在集群内启动时会把本集群登记进 clusters（source=in-cluster），
  并用 rest.InClusterConfig() 的 ServiceAccount 访问 apiserver。
  但 k8s/ 下**没有任何平台自身的 ServiceAccount/ClusterRole/ClusterRoleBinding**
  （deployments.yaml 也没有 serviceAccountName，用 default SA）。
  default SA 只有极小的权限：HealthCheck 调 Discovery().ServerVersion() 能过
  （/version 对认证用户开放）→ 集群显示 healthy；随后 list pods/delete/exec 全部 forbidden。
代码位置：
  k8s-executor/internal/bootstrap/incluster.go:33-106（自动登记）
  k8s-executor/internal/health/checker.go:44-58（健康=ServerVersion）
  k8s/deployments.yaml（全文无 serviceAccountName / ClusterRole）
  k8s/README.md（无 RBAC 说明）
涉及函数或类：bootstrap.EnsureInCluster、health.Checker.HealthCheck
调用链：
  Pod 启动 → EnsureInCluster 建 cluster 行 → 健康检查 ServerVersion 成功 → status=healthy
  → 用户点进集群 → ListPods → Forbidden → 报错
根本原因：本集群自动接入只做了「登记」，没做「授权」；RBAC 清单缺失（对比 agent 清单有完整 ClusterRole）
可能影响：K8s 部署形态下本集群页面大面积 500/Forbidden，且状态显示 healthy 误导排障
修改建议：
  1) 补 k8s/rbac.yaml：ServiceAccount + ClusterRole（按最小权限：get/list/watch + 允许的操作）
     + ClusterRoleBinding，并在 deployments 里指定 serviceAccountName；
  2) 启动时做一次 authz 自检（SelfSubjectAccessReview：list pods/delete deployments），
     不通过则标记 rbac_status=insufficient，而不是 healthy；
  3) 文档补「本集群需要哪些权限」。
验证方法：在 K8s 部署环境点进本集群的 Pod 列表，能返回数据（当前应为 Forbidden）。
是否需要立即处理：否（需确认生产是否手工补过 RBAC——本次只读未见）
```

```
问题编号：K8S-16
问题名称：kubeconfig 加密依赖单把静态密钥，无轮换/KMS，备份要求明文带密钥
问题等级：P2
所属模块：安全 / 凭据管理
问题描述：
  AES-256-GCM 实现正确（随机 IV、Python/Go 一致），但密钥是 `ENCRYPTION_KEY` 环境变量
  （32 字节），无版本号、无轮换流程、无 KMS/信封加密；`k8s/backup-cronjob.yaml`
  明确要求「备份包必须与 ENCRYPTION_KEY 一起保存」，等于把密钥与密文放同一备份介质。
  同一 Secret 里还有 JWT_SECRET_KEY（同时是内部服务 token）。
代码位置：
  api-server/app/core/security.py:18-31
  k8s-executor/internal/k8sclient/crypto.go:16-20
  k8s/secret.example.yaml:25-27
  k8s/backup-cronjob.yaml:3,11
涉及函数或类：encrypt_aes256_gcm / decrypt_aes256_gcm、SetEncryptionKey
调用链：
  备份 → mysqldump（含 kubeconfig_encrypted）→ 与 ENCRYPTION_KEY 同包保存
根本原因：为了部署简单，选择单密钥对称加密，没有密钥管理设计
可能影响：备份泄露 = 全部被管集群凭据泄露；密钥无法轮换（换密钥需要全量重加密，
  代码无 migrate 逻辑）；密钥一旦丢失所有 kubeconfig/DB 密码不可恢复
修改建议：
  1) 引入密钥版本（key_id 前缀）与双密钥解密窗口，支持轮换；
  2) 生产用 KMS/外部 secret manager（Vault）做信封加密，DB 只存 data key 密文；
  3) 备份不与应用密钥同介质保存（分离职责）；
  4) 与 JWT_SECRET_KEY 解耦（见 ARCH-09）。
验证方法：用旧密钥加密的数据在轮换后仍能解（需要版本机制，当前不支持）。
是否需要立即处理：否
```

```
问题编号：K8S-17
问题名称：缓存与限值的零散一致性问题（SWR 陈旧窗口、事件硬编码 100 条、前端死代码）
问题等级：P3
所属模块：k8s-executor / misc 缓存、events、前端
问题描述：
  ① ListMiscResources 的 SWR 缓存 TTL 15s，删除 Secret/ConfigMap 后列表最多 15s 仍显示旧数据
    （且后台保活会持续刷新最近访问的 key）；
  ② GetPodEvents 硬编码截断 100 条，无参数可调；容器重启多、事件多的 Pod 会丢关键早期事件；
  ③ api.ts 导出 execPod（POST /pods/{pod}/exec）但无页面使用（死代码）；
    GET /clusters/{id}/agent-upgrade 后端有、前端未用（孤儿接口）；
  ④ resources.py 与 jvm.py 各有一套 JVM 诊断路由（/resources/.../jvm/diagnose 与 /jvm/.../diagnose）。
代码位置：
  k8s-executor/internal/executor/misc_resources.go:113（miscCacheTTL=15s）
  k8s-executor/internal/executor/misc_resources.go:137-196（SWR 返回旧值 + 后台刷新）
  k8s-executor/internal/executor/pod_list_events.go:361-363（截断 100）
  frontend/src/services/api.ts:439-440（execPod 未被引用）
  api-server/app/api/clusters.py:578-616（agent-upgrade 未被前端引用）
涉及函数或类：ListMiscResources、GetPodEvents、execPod、agent_upgrade_command
调用链：—
根本原因：性能优化引入缓存后未做写后失效；限值散落在代码里
可能影响：运维看到「已删除的资源还在」产生误判；事件缺失影响排障；死代码增加维护噪音
修改建议：
  1) delete/apply 成功后主动删缓存 key（已有 singleflight，可加 Invalidate）；
  2) 事件条数改为参数（默认 200），并按 lastTimestamp 倒序保留最新；
  3) 清理死代码/孤儿接口，或补前端入口。
验证方法：删除 ConfigMap 后立即刷新页面，应立刻消失。
是否需要立即处理：否
```

---

## 5. 无法确认事项

| 事项 | 卡点 |
|---|---|
| 生产 k8s/ 实际是否手工补过平台自身 RBAC（ClusterRole/SA） | 仓库内无清单；未连集群，无法 `kubectl auth can-i` |
| `AGENT_GATEWAY_MODE` 在生产实际值 | 只读到 ConfigMap 模板（remote）与代码默认（inproc）；未读运行时配置 |
| 9095/9096/9099 是否被外层 nginx/SamWaf 做四层转发或端口映射 | `k8s/services.yaml:60-72` 只给三选一注释；无实际 NodePort/LB 配置 |
| agent 实际版本分布（是否都支持 timestamps/RollbackWorkload） | ConfigMap `0.2.7` 与代码默认不一致；未连环境 |
| `EXECUTOR_MAX_CONCURRENCY` 生产值 | ConfigMap 写 8；是否被 Secret/环境覆盖未知 |
| 是否有外部网关对 `/api/v1` 做二次鉴权（如 SSO 前置） | 仓库内未见；超出本次范围 |
| Kubernetes 侧是否有集群级 audit log 可作为越权追溯 | 取决于被管集群配置，源码无法确认 |

---

## 6. 审计方法与覆盖范围

**精读（全文或主体）**

- `api-server/app/api/clusters.py`（979 行全文）、`resources.py`（2883 行：路由表 + 鉴权前缀 + Pod/日志/exec/工作负载/HPA/节点/drain/下载等关键段全文）、`workload_logs.py`（309 行全文）、`logs.py`（关键段）、`terminal.py`（全文）、`alerts.py:60-120`、`monitoring.py:55-160`、`service_accounts.py`（全文）、`jvm.py`（路由与产物段）
- `api-server/app/clients/executor_client.py`（1-470 行逐行 + 各方法超时/签名扫描）、`core/security.py`（全文）、`core/config.py`（相关项）、`services/user_permission_service.py`（全文）、`services/cluster_credential_service.py`（全文）、`services/rbac_service.py:1-160`、`agent_gateway_service.py`（全文）、`middleware/permission.py`（全文）
- `k8s-executor/internal/k8sclient/{manager.go, crypto.go, retry.go}`（全文）
- `k8s-executor/internal/executor/{execute.go, exec_in_pod.go, exec_stream.go, pod_ops.go, pod_restart.go, workload_ops.go, rollback.go, gray_release.go, circuit_breaker.go, namespaces.go, misc_resources.go（1-200）, pod_list_events.go（DeleteResource/GetWorkloadYaml/revealSecretData/GetPodEvents/workloadGVR）}`
- `k8s-executor/internal/grpc/server.go`（路由方法清单 + 1-230 + 585-720 + ExecPodStream 993-1126）
- `k8s-executor/cmd/executor/main.go`（全文）、`internal/bootstrap/incluster.go`（全文）、`internal/health/checker.go:1-80`、`internal/config/config.go`（全文）、`internal/logstream/server.go`（全文）、`internal/watcher/{manager.go, pod_watcher.go(头部), node_watcher.go(头部)}`、`internal/agentbridge/bridge.go:1-140`、`internal/rbac/rbac.go:1-80`
- `k8s/{deployments.yaml(关键段), services.yaml, networkpolicy.yaml, configmap.yaml(相关项), secret.example.yaml, backup-cronjob.yaml(头注释)}`
- `frontend/src/services/api.ts`（K8s 相关全部 URL）、`pages/cluster/*.tsx`（按钮/操作入口扫描）、`components/TerminalModal.tsx`（入口）

**扫描式（grep 定点）**

- `grep "@router"` 统计路由数（resources 79 / clusters 21 / monitoring 9）
- `grep -c "  rpc "` 统计 proto RPC（49）；`grep -c "^func (s \*Server)"`（50，含 getExecutor）
- `grep "check_cluster_permission|visible_ids"` 全 api 目录（定位漏检接口）
- `grep "decrypt_aes256_gcm|kubeconfig"`（确认无回显）
- `grep "audit" api-server/app/api/resources.py`（确认审计覆盖面）
- `grep "MaxRecvMsgSize|MaxCallRecvMsgSize"`（确认无配置）
- `grep "428"`（确认 K8s 侧无审批）

**未读/未做（说明原因）**

- `k8s-executor/internal/{agentdb, hostagent, quota, metrics, eventstore, prometheus, script}` 的业务细节（属 DB 审计/指标链路范围，本次只看调度关系）
- `api-server/app/services/inspection_service`、`resource_service`、`prometheus_service` 的完整实现（只确认调用入口与返回结构）
- 前端页面组件逐个内部实现（只核对请求层与操作入口）
- **未执行**：任何 kubectl / 集群连接 / 依赖安装 / 单元测试 / git 命令；未修改任何既有文件。
