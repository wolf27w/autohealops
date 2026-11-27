# 02 · 模块依赖与服务调用关系

> **代码基线**：`main @ b3b9f03`　**方式**：只读源码检索 + import 图静态解析 + 关键路径逐行阅读
> **路径约定**：相对项目根 `/Users/wolf/Documents/project/autohealops/`
> **标注**：【事实】源码可证 /【推断】基于证据的判断 /【待验证】需运行期或回归确认
> 本文回答「改一个模块会波及谁」，不评价功能正确性。

---

## 0. 结论摘要

1. 【事实】系统实际是**星型依赖**：`api-server` 是唯一枢纽，`frontend` 只依赖它；`ai-service` 与 `k8s-executor` 之间**没有直接调用**，但两者共享同一个 MySQL 库与同一个 `ENCRYPTION_KEY`。
2. 【事实】存在 2 组可确认的 Python 循环依赖：`alert_auto_service → approval_service → alert_auto_service`、`alert_auto_service → action_verify → alert_auto_service`（靠函数内延迟 import 规避启动期错误）。
3. 【事实】分层约束失效：49 个 api 文件直接写 SQL，12 个 api 文件直接调 executor，53/67 个 api 文件直接 import services 并内联业务规则。
4. 【事实】耦合方式以「全局可变状态 + 口头契约」为主：进程内网关连接表、RAG 内存索引、缓存代次、跨服务重复 ORM、复用 JWT 密钥的内部令牌。
5. 【推断】最高波及面模块是 `clients/executor_client.py`（44 个文件引用）与 `models/`（59 个模型 + ai-service 247 行 ORM + 123 个迁移），这两处应作为重构的**最后**动，而不是最先动。

---

## 1. 服务间依赖矩阵

| 调用方 → 被调方 | 协议 / 端口 | 代码位置 | 认证方式 |
|---|---|---|---|
| frontend → api-server | HTTP `/api/v1` | `frontend/src/services/api.ts:9-12`、`vite.config.ts:11-20`、`nginx.conf:14-19` | Bearer JWT（`api.ts:31-37`） |
| api-server → ai-service | HTTP :9000 | `api-server/app/clients/ai_client.py:12-124`（`_post:21`），`config.py:40` | `X-Internal-Token = JWT_SECRET_KEY`（`ai_client.py:28`） |
| api-server → k8s-executor | gRPC :9090 | `api-server/app/clients/executor_client.py:544-1407`，`acall:549` | 无（Go 侧裸 `grpc.NewServer()`，`cmd/executor/main.go:95-109`） |
| api-server ↔ agent-gateway | 进程内 / gRPC :9096 | `agent_gateway.py:135 call`、`agent_gateway_service.py:25-75` | inproc 无；remote 无 |
| agent → agent-gateway | gRPC 双向流 :9095 | `k8s-executor/cmd/agent/main.go:246-247`、`api-server/app/agent_gateway.py:253` | mTLS 客户端证书 + token sha256（`agent_gateway.py:32-55`） |
| api-server → agent（pull） | gRPC :9100 | `executor_client.py:382-464` | Bearer token（`agentbridge/bridge.go:180-246`） |
| executor / agent → MySQL | GORM / SQL | `k8s-executor/internal/watcher/sink.go:137-144`、`internal/database/db.go` | 同一 `DATABASE_URL` |
| executor / agent → K8s API | client-go | `internal/k8sclient/manager.go`、`internal/executor/` | kubeconfig / in-cluster SA |

【事实】`ai-service` 与 `k8s-executor` 无相互调用；二者的一致性依赖「共享数据库 + 共享加密密钥 + 重复 ORM 定义」，而非接口契约。

---

## 2. 分层现状（实际而非理想）

```
frontend  ──HTTP──▶  api/  ──▶  services/  ──▶  clients/  ──▶  ai-service / executor
                      │            │                 │
                      │            └──▶ models/      └──▶ MySQL / Redis
                      │                 ▲
                      ├── 直接写 SQL ───┤   （49 个 api 文件）
                      ├── 直调 executor_client      （12 个 api 文件）
                      └── 直调 agent_gateway.call   （db_connections.py:436）
```

【事实】越层证据：

| 越层类型 | 位置 |
|---|---|
| api 层原生 SQL | `api/resources.py:491-495`（`text("SELECT ... pod_metrics_5m")`）、`api/stats.py:355-442`（20+ `select/text`）、`api/resources.py:206-220,279-283,630-632`、`api/host_sessions.py:86-111`、`api/host_terminal.py:74-75` |
| api 层直调 executor | `api/resources.py:53,69,257,424,462,567,584,598,699,738,820,853,881`、`api/jvm.py:131-132,590`、`api/workload_logs.py:123,168,194` |
| api 层直接落库 | `api/auth.py:36,134`、`api/clusters.py:36`、`api/db_connections.py:356` |
| 路由内业务规则 | `api/db_connections.py:352-360` 内联只读白名单、`:382` 内联审计写库；`api/stats.py:1468-1487` 内联 SSE + 缓存逻辑 |

【推断】`api/` 目录名义上是「路由层」，实际承担了鉴权编排、SQL 聚合、gRPC 调用、状态机判断四类职责；这使得同一业务规则在 `api/` 与 `services/` 各有一份实现（见 04 文档）。

---

## 3. 循环依赖【事实】

用 Python 解析 `from app.services.X import ...` 构造有向图并 DFS，得到两组环：

| 环 | 路径 | 规避方式 |
|---|---|---|
| 环 1 | `alert_auto_service → approval_service → alert_auto_service` | 函数体内延迟 import |
| 环 2 | `alert_auto_service → action_verify → alert_auto_service` | 函数体内延迟 import；`action_verify.py:118` 引用 `_dedup_knowledge` |

【推断】延迟 import 掩盖了结构问题：从业务语义看，告警编排（orchestrator）依赖审批（approval）与验证（verify），而后两者又反向需要告警侧的告警状态写入与知识去重能力，说明**缺少一个独立的「告警状态/结果写入」下层模块**。这是 05 文档拆分方案的直接依据。

---

## 4. Service 之间的耦合热度【事实】

`grep -rhoE 'from app\.services\.[a-z_]+' services/*.py | sort | uniq -c` 的主要结果：

| 被依赖 service | 引用次数 | 含义 |
|---|---:|---|
| `system_setting_service` | 26 | 配置读取散落各处，带进程内缓存（`system_setting_service.py:19`） |
| `termination_evidence` | 15 | 诊断证据构造被多处复用 |
| `notification_service` | 15 | 通知与业务编排双向耦合 |
| `alert_auto_service` | 12 | 告警主流程被 DB 告警、工单、自动处置等多入口引用 |
| `scheduler_service` | 11 | 定时任务与业务 service 互调 |

【推断】`system_setting_service` 的 26 次引用 + 进程内 `_cache` 意味着配置变更的生效时机不统一；`alert_auto_service` 的 12 次跨模块引用意味着它的任何签名变更都会波及告警、工单、DB 告警、Pod 重启四条链路。

---

## 5. 跨层调用与公共工具类

【事实】`grep -rlE 'session\.execute|select\(|text\(' api-server/app/api/*.py` → 49 个文件；`grep -rln 'executor_client' api/*.py` → 12 个文件；53/67 个 api 文件直接 import services。

【事实·工具类并未「过度肥胖」而是「分叉」】`core/http_client.py` 在两端各 47/45 行且内容不同；真正肥胖的是 `clients/executor_client.py`(1,407)、`utils/pod_logs.py`(574)、`frontend/src/services/api.ts`(1,455)。

【推断】`clients/executor_client.py` 同时承担 4 种传输路径（`:94` 直连、`:131` 网关、`:413` pull、`:466` agent 隧道）+ 能力协商(`:197`) + 50 余个 RPC 包装(`:544-1310`) + protobuf 转换，是全仓最典型的「一个模块承担多个维度」。`executor_client.py:249-373 _TunnelAwareStub.__getattr__` 用动态反代把 4 套路径合一，可读性与可测试性代价较高。

---

## 6. 隐式状态与全局耦合

| 状态 | 位置 | 影响【推断】 |
|---|---|---|
| 进程内连接表 `_agents` / `_agents_lock` / `_loop` | `api-server/app/agent_gateway.py:26-29`；`_agents[cluster_id]` 写入 `:278-279` | inproc 模式无法多副本共享；remote 模式只是搬迁进程，未外置 |
| 无界队列 | `agent_gateway.py:92-95`（`out=queue.Queue()`、`pending`、`streams`），`put` 在 `:145/:169/:196/:202/:237` 均无界 | 慢/断线 agent 下请求与事件无限堆积，无背压 |
| RAG 内存索引 | `ai-service/app/services/rag_service.py:24` 全局 `_INDEX` + TTL(`:7`) + `invalidate_index():36` | 多副本各自持有，60s 内可能脏读 |
| 仪表盘缓存代次 | `api-server/app/core/orm_hooks.py:22,32,58` → `redis_client.bump_dashboard_generation_sync`；`api/stats.py:268,1469-1477` | ORM 写入与 SSE 推送通过 Redis 隐式耦合 |
| watcher 全局态 | `k8s-executor/internal/watcher/sink.go:72,96-97,325`；`pod_watcher.go:76-78,521` 内存去重表 | 重启丢失、多实例不一致 |
| 共享密钥 | `ai_client.py:28`、`api/ai_proxy.py:23`、`k8s-executor/internal/config/config.go:66` | 内部令牌 = `JWT_SECRET_KEY`，跨服务信任边界共用一把密钥 |
| 表结构口头契约 | `ai-service/app/models/__init__.py:2` | 无共享包、无契约测试，字段变更需人工同步两处 |

---

## 7. 数据 ownership（谁写哪张表）

【事实】

| 表/域 | 写入方 |
|---|---|
| `alerts` | ① Go watcher 直写：`k8s-executor/internal/watcher/sink.go:137-144 dbSink.Report`；② Python 侧：`services/agent_alert_service.py:88 _merge_or_insert`、`services/alert_merge.py:100 apply_merge` |
| 平台配置 / 模型 / Prompt | api-server 与 ai-service 均可写（`ai-service/app/models/__init__.py` 重复 ORM） |
| K8s 事件 / 指标快照 | executor 与 agent 经隧道上报 → `services/agent_alert_service.py:239`、`eventstore` |
| 其余业务表 | api-server 为主 |

【推断】`alerts` 是唯一明显的**双写者**表，且两写者分属不同语言、不同进程、无统一去重约束（Go 侧 `db.Where(...).First` 去重，Python 侧 `find_merge_target` 窗口合并）。这是「改告警模型影响面最大」的根因。

---

## 8. 波及面分析

【事实】`grep -rl <模块> api-server/app --include='*.py' | wc -l`：

| 改动模块 | 波及文件数 | 备注 |
|---|---:|---|
| `executor_client` | 44 | 12 个 api 直接调用，50+ RPC 包装 |
| `ai_client` | 15 | 诊断/方案/验证/RAG 全链路 |
| `screen_bus` | 9 | 大屏 SSE |
| `orm_hooks` | 5 | 但通过 Redis 代次间接影响所有仪表盘读取 |
| `fault_taxonomy` | 直接 import 3，字符串命中 40+ | 两服务两份实现，靠字符串复制扩散 |
| `models/` | 59 个 api-server 模型 + ai-service 247 行 ORM + 123 迁移 | schema owner 与实际使用方分离 |

【推断】重构顺序应**从低波及面、高内聚的叶子模块开始**（如 `_deny_cluster` 统一依赖、`stats_dashboard_repo`），把 `executor_client` 与 `models/` 留到有回归网之后再动。

---

## 9. 渐进式解耦建议（不改变部署与 API）

| 优先级 | 动作 | 做法 | 兼容性 |
|---|---|---|---|
| P0 | 切断两组循环依赖 | 抽出 `alert_result_writer.py`，把「告警状态写入 / 知识去重」下沉；`alert_auto_service`、`approval_service`、`action_verify` 都依赖它，不再互相 import | 纯内部模块，无对外接口变化 |
| P1 | 统一资源级鉴权 | 把 5 份 `_deny_cluster` 收敛为 `Depends(cluster_guard)`；原函数保留为薄包装 | 路由签名与 403 行为不变 |
| P1 | 收敛 executor 传输路径 | `ExecutorClient` 保留为门面，内部拆 `transport` 策略（直连/网关/pull/隧道） | 所有 `executor_client.xxx()` 调用点不改 |
| P2 | 共享契约包 | 把 `fault_taxonomy`、env/status 枚举抽到单一包，两服务以只读依赖引入；proto 生成物移入 `generated/` 并在 CI 加一致性校验 | 需同步两处 import，不改表结构 |
| P2 | 外置网关连接表 | `_agents` 迁到 Redis（已有 Redis 依赖） | 涉及多副本行为，需运行期验证 |
| P3 | 统一 `alerts` 写入口 | Go watcher 改为经隧道上报，由 Python 侧单一写者落库 | 涉及部署拓扑，属于较大改动，建议放到最后 |

【推断】P0/P1 属于「同一进程内搬家」，风险可控且可单测覆盖；P2/P3 涉及跨服务与运行期行为，必须先补齐回归测试（见 04 文档测试章节）。

---

## 10. 待验证事项

1. 两组循环依赖是否还有第三组（本次仅解析 `from app.services.X import`，未覆盖 `import app.services.X` 与动态 import）。
2. `system_setting_service._cache` 的实际失效策略与多副本一致性（未做运行期验证）。
3. `alerts` 双写在并发下是否产生重复行（需压测或查线上数据，本次仅静态阅读）。
4. remote 网关模式下 `_agents` 是否真的被完全旁路（审计报告称仍有直连 inproc 的代码路径，本次未逐条复核）。
