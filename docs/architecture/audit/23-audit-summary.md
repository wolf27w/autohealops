# 23 审计总结（AutoHealOps 第一阶段）

- 审计对象：`/Users/wolf/Documents/project/autohealops`（分支 `main`，基线提交 `b3b9f03`）
- 审计性质：**只读代码审计**。未修改业务代码/配置/迁移/YAML，未安装依赖，未连接任何生产环境或集群，未执行数据库写入，未改动 Git。
- 审计时间：2026-11-27
- 报告总数：17 份模块报告（01–17）+ 6 份结论报告（18–23），合计 12,129 行
- 结论口径：所有数字来自命令输出或源码行号；无法从源码确认的写「暂时无法确认」。

---

## 1. 一句话结论

**这是一个「能力覆盖很宽、工程完成度中等、安全边界基本没有」的单体拆分平台**：K8s/VM/DB/AI 四条业务线都有真实代码（不是空壳），AI ReAct 循环、Agent gRPC 隧道、数据库审核、审批流都真实存在；但**执行面没有统一入口、权限与审批没有强制收敛、内部服务之间没有认证**，导致 21 个 P0 里有 14 个可以被「一个 operator 账号」或「集群内任意 Pod」直接打穿。当前状态**不适合直接暴露到生产网络**，第一阶段应先做安全止血与统一执行网关，再谈功能扩展。

---

## 2. 关键数字

| 指标 | 数值 | 来源 |
|---|---:|---|
| 源码文件 / 行数 | 约 807 文件 / 16.05 万行 | 01 号报告 |
| api-server（Python） | 88,121 行 | `find api-server -name '*.py' \| xargs wc -l` |
| k8s-executor（Go） | 28,750 行 | 同上 |
| frontend（TS/TSX） | 35,102 行 | 同上 |
| ai-service（Python） | 5,971 行 | 同上 |
| HTTP/WS 路由 | 500 条（495 HTTP + 4 WS + 1 catch-all） | 11 号报告静态解析 |
| 其中零鉴权 | **2 条**（`/auth/login`、`GET /mcp` 探测） | 11 号报告 |
| 有角色依赖 | 483 条 | 11 号报告 |
| 手写鉴权 | 15 条（内部令牌/外部令牌/IM 验签/SSE/WS/agent） | 11 号报告 |
| 数据库模型 | 87 个（58 个模型文件） | 03 号报告 |
| alembic 迁移 | 124 个版本 | 04 号报告 |
| 后台常驻协程 | 约 26 个，无 leader 选举 | 04 号报告 |
| 审计问题总数 | **230 条**（P0 21 / P1 66 / P2 102 / P3 41） | 20 号报告逐条比对 |
| 审计覆盖率 | 约 9%（25 个审计点 / 285 个写路由） | 13 号报告 |
| 自动化测试 | 真实通过 1,315 个（api-server 1189、ai-service 100、Go 25；skip 3/1） | 16 号报告实测 |
| 前端测试 | **0** | 16 号报告 |

> P0 计数含跨报告重复描述同一缺陷（9090/9096 各被 3 份报告记录），去重后为 **17 个独立 P0 缺陷**。

---

## 3. 技术栈（实测）

| 层 | 技术 | 证据 |
|---|---|---|
| 前端 | React + TypeScript + Vite + Ant Design（`package.json`），`App.tsx` 集中路由 | `frontend/package.json`、`src/App.tsx:128-231` |
| 控制面 | Python 3.11 + FastAPI + SQLAlchemy 2.x(async) + Alembic | `api-server/requirements.txt`、`app/main.py` |
| 执行面 | Go（`go.mod` 声明 **1.26.6**，Dockerfile 却是 **1.22**）+ gRPC + client-go | `k8s-executor/go.mod:3`、`Dockerfile:2` |
| AI 服务 | Python FastAPI + 多模型适配（DeepSeek/OpenAI/Claude 兼容） | `ai-service/app/services/model_*.py` |
| Agent | Go 单二进制，gRPC 双向流（`AgentService.Tunnel`），**不是 WebSocket** | `proto/agent.proto:10-12`、`cmd/agent/main.go:242-262` |
| 数据库 | MySQL（平台库），**executor 也直连同一个库写 alerts** | `internal/database/db.go:18`、`watcher/sink.go:215` |
| 缓存 | Redis（锁、缓存、登录防爆破、token 黑名单） | `core/redis_client.py` |
| 消息队列 | **无**（用 Redis 锁 + 数据库轮询 + HTTP 回调替代） | 02 号报告 |
| 日志检索 | Elasticsearch（可选） | `services/es_client.py` |
| 监控 | Prometheus（executor 代理 /metrics） | `internal/prometheus/proxy.go` |
| 部署 | Docker Compose（单机）/ K8s manifests（api-server 2 副本 + HPA 5） | `docker-compose.yml`、`k8s/deployments.yaml` |
| 通知 | 钉钉/企微/飞书 webhook + 长连接 | `long_conn_client.py`、`feishu_service.py` |

---

## 4. 当前架构（真相）

```
浏览器
  │ HTTPS
  ▼
frontend (nginx, :80)
  │ /api/*
  ▼
api-server (FastAPI, :8080) ──HTTP──► ai-service (:9000) ──► 大模型 API
  │  │                                  ▲
  │  │ gRPC :9090 (无鉴权)               │ X-Internal-Token = JWT_SECRET_KEY
  │  ▼                                  │
  │ k8s-executor (Go) ──► K8s API / SSH / DB
  │  │ gRPC :9095 (mTLS)
  │  ▼
  │ agent-gateway (:9096 内部明文, 无鉴权)
  │  ▲
  │  │ gRPC 双向流 + 客户端证书
  │ agent (集群内 Pod) ──► 被管集群 K8s API / 主机
  │
  ├── MySQL（平台库，executor 双写）
  ├── Redis（锁/缓存）
  └── Elasticsearch / Prometheus（可选）
```

三个结构性问题：

1. **两套拓扑不一致**：K8s 是独立 `agent-gateway` + `remote` 模式；Compose 是 `inproc`（网关在 api-server 进程内）且只映射 8080，**Docker 形态下 agent 隧道不可用**（ARCH-01）。remote 模式下仍有代码直连 inproc 全局字典，会静默失败（ARCH-02）。
2. **执行面有 8 条平行入口**：资源变更 API、`/batch`、`/alert-actions`、`/custom-tasks`、MCP、回滚、executor gRPC、agent pull。审批只覆盖「AI 生成修复计划」这一条（10 号报告）。
3. **无状态化不彻底**：26 个后台协程在每个副本全量启动，仅部分抢 Redis 锁；慢 SQL 采集、分区 DROP、仪表盘预热无锁（ARCH-03）。

---

## 5. 能力盘点（摘要，详见 18/19 号）

### 真实可用（有完整链路 + 有测试或实跑证据）

- K8s 多集群接入（直连 kubeconfig + AES-GCM 加密存储 + agent 代理两种模式）
- Pod/Deployment/Node/Service/Ingress/Storage/ConfigMap 查询，日志（HTTP 流 + WS）、Events
- 重启 / 扩缩容 / 回滚 / 镜像更新 / YAML apply / 删除 / 容器 exec
- Agent 注册（sha256 token）+ mTLS 客户端证书 + gRPC 双向流隧道 + 全量 RPC 转发
- SSH 主机接入、Web 终端、凭据 AES 加密、会话录像回放、主机指标采集
- MySQL/PG/Oracle 接入、慢 SQL 采集与诊断、SQL 审核单、备份/导入
- AI 诊断 ReAct 循环（8 轮默认/16 上限/90s 总预算）、8 个诊断工具、证据护栏（主链路）、RAG、Prompt 版本与灰度
- 修复计划 → 决策引擎 → autonomy_gate → 审批 → 执行 → 验证（`action_verify`）
- 告警采集/合并/抑制/转工单/通知、值班、静默；批量操作（有 Redis 锁）
- 仪表盘/大屏、审计哈希链（有实现但可被更新打断）

### 名义存在 / 链路不通（对外宣传风险）

| 能力 | 真实状态 | 证据 |
|---|---|---|
| 云原生 RBAC / impersonation | `ImpersonatedClient` 是死代码，实际全部用 cluster-admin SA | 05 号报告 |
| 灰度发布 | `gray_release.go` 只是修复计划分批，不是 K8s 灰度 | 05 号报告 |
| Agent 心跳 ping/pong | 协议有定义，平台从不发送；仅 HTTP/2 keepalive | 04 号报告 |
| Agent 任务幂等/取消 | 未实现；超时后破坏性操作仍执行完 | 06 号报告 |
| 项目级资源隔离 | 87 个模型仅 3 张关联表有 `project_id`，8 个业务域零过滤 | 12 号报告 |
| VM 服务管理 / 日志查询 / 文件传输 | 未实现（仅终端 + 指标） | 07 号报告 |
| 审计完整性 | 覆盖约 9%，授权变更类全部无审计 | 13 号报告 |
| 前端权限 | 菜单 guard 只是 UI，后端另有校验（这点没问题）；但前端 0 测试 | 11/16 号报告 |
| Docker 部署的实时日志跟随 | compose 未注入 `EXECUTOR_LOGSTREAM_ADDR`、未暴露 9099 | 01 号报告 |
| `admin/admin123` | README 写的默认账号，代码实际是随机强密码 | 01 号报告 |

---

## 6. 安全问题：17 个独立 P0（去重后）

### A. 执行面裸奔（3 个，最高优先）

| 编号 | 问题 | 证据 |
|---|---|---|
| EXE-03 / SEC-02 | executor `:9090` gRPC 无认证、开 reflection，compose 直接发布到宿主机 → 等价被管集群 cluster-admin | `cmd/executor/main.go:95-109`、`docker-compose.yml:22-23` |
| K8S-04 / AGT-01 / SEC-03 | agent-gateway `:9096` 明文无鉴权，可 `IssueClientCert` 为任意集群签发证书、`TunnelCall` 下发任意 RPC；且**不在任何 NetworkPolicy 内** | `agent_gateway.py:361-406`、`k8s/networkpolicy.yaml`（只含 executor/ai-service） |
| EXE-05 | agent pull 模式 NodePort 暴露 9100，令牌可为空 | 06 号报告 |

### B. 越权与租户隔离（5 个）

| 编号 | 问题 | 证据 |
|---|---|---|
| K8S-01 | apply 用请求 namespace 鉴权，Go 侧用 YAML 里的 `metadata.namespace` 覆盖 → 跨 ns 写 | `pod_list_events.go:461-472`、`pod_ops.go:97-102` |
| K8S-02 | `namespace=""` 退化为「任意一个 ns 有权限即可」→ 跨 ns 读 | `user_permission_service.py:56-58` |
| K8S-03 | `GetWorkloadYaml` 把 Secret base64 解码成明文 `stringData` 返回给 read 用户 | `pod_list_events.go:439-441,528-554` |
| EXE-04 | 大量资源变更端点只需 operator + 项目权限，无审批（多数无审计） | 10 号报告 |
| ISO 系列 | 告警/审批/执行/任务/审计/批量/统计 8 个域无 project 范围过滤 | 12 号报告 |

### C. 审批可绕过（4 个）

| 编号 | 问题 | 证据 |
|---|---|---|
| EXE-01 | `/batch` 任意 operator 可对任意集群执行任意计划（无审批/无集群权限/无审计） | `api/batch.py:14-31`、`batch_service.py:99-125` |
| APP-01 | 加签/转办只校验 operator，不校验是否本单审批人 → 自己加签自己批 | `api/approvals.py:132-154` |
| DB-01 | 审核单 kind/action 自填且不与 SQL 交叉校验 → `kind=query` 执行 DELETE | `db_approval_service.py:155-179` |
| DB-02 | DB 审核无四眼，项目管理员可自审自批 | `api/db_approvals.py:270-298` |

### D. 凭据与密钥（3 个）

| 编号 | 问题 | 证据 |
|---|---|---|
| AUTH-01 | `X-Internal-Token` 直接复用 `JWT_SECRET_KEY` → 拿到内部令牌即可伪造 admin JWT | `alerts.py:540`、`ai_client.py:28`、`ai_proxy.py:23` |
| AUTH-02 | 默认 `change-me` 且无强度校验，install.sh 原样复制 | `.env.example:13`、`config.py` |
| AGT-02 | agent 接入令牌明文写在 Deployment `args`，install.yaml 匿名可取 | `agent/examples/install.yaml.example:93` |

### E. 任意执行 / SSRF（2 个）

| 编号 | 问题 | 证据 |
|---|---|---|
| PERM-02 / EXE-02 / PERM-01 | MCP `tools/call` 直接调 handler，绕过 `allow_operate` 与集群归属校验 → viewer 可 exec_in_pod；`custom-tasks` script 类型任意集群任意 shell | `mcp.py:87-110`、`tool_registry.py:1817`、`scheduler_service.py:82-83`、`script/runner.go:53` |
| SEC-01 | `registry/tags` 反射型 SSRF：host 不匹配回退全局 registry 凭据并 Basic Auth 发给攻击者域名 | `registry_service.py:55-80`、`resources.py:2162` |

### F. 其他 P0

- DB-03 只读仅靠首词白名单，无数据库级只读；`WITH ... DELETE`、`SELECT ... INTO OUTFILE`、`FOR UPDATE` 全部放行（`dialects/mysql.py:30-38`）。

---

## 7. 八个系统性问题（比单个漏洞更重要）

1. **没有统一执行网关**：变更能力散落在 8 条入口，任何新入口都会再次绕过审批。
2. **认证只做了「用户→API」，没做「服务→服务」**：9090/9096 裸奔 + 内部令牌复用 JWT 密钥，是同一个根因。
3. **权限判定分散**：66 个路由模块只有 17 个做资源级校验，`resources.py` 是唯一正确范式（79 路由 77 个 `_deny_cluster`）。
4. **审批只绑单不绑参**：审批单存计划文本，执行时重新取 plan，缺参数哈希/签名；DB 审核同理。
5. **多租户是「授权代理」不是「数据分区」**：没有行级过滤中间件，靠每个查询自觉。
6. **AI 护栏是「软约束」**：证据校验只查引文、异常即放行、降级路径无护栏；工具权限靠调用方自觉传 `allow_operate`。
7. **可观测性/审计不足**：审计覆盖 9%，无结构化字段；26 个后台协程无 leader，重复执行风险。
8. **测试集中在纯函数**：审批、权限、危险动作、审计 0 测试；前端 0 测试；CI 不跑 Go 用例。

---

## 8. 最高优先级修复顺序（建议 72 小时内）

1. **换密钥 + 拆内部令牌**（AUTH-01/02）：生成独立 `INTERNAL_SERVICE_TOKEN`，轮换 `JWT_SECRET_KEY`，禁止默认值启动。先做这条，因为后面所有鉴权改造都依赖它。
2. **关端口 + 加网络策略**（EXE-03、K8S-04、EXE-05）：9090/9096/9100 不发布到宿主机；给 agent-gateway 补 NetworkPolicy；内部 RPC 加 mTLS 或短期 token。
3. **堵三条「一键拿集群」路径**（PERM-02/MCP、EXE-01/batch、PERM-02/custom-tasks）：先禁用或加 operator+集群权限+审批，再重构到统一 Action Gateway。
4. **修审批自批与加签**（APP-01、DB-02）：强制 `requested_by != approved_by`，加签/转办校验操作人身份。
5. **修 namespace 覆盖与 Secret 明文**（K8S-01/02/03）：Go 侧以鉴权 namespace 为准；Secret 默认打码，明文需二次授权 + 审计。
6. **DB 只读与 kind 交叉校验**（DB-01/03）：按 SQL 解析结果判定写操作，不只信请求参数；加只读事务。

---

## 9. 路线图摘要（详见 22 号）

- **第一阶段（0–3 月，6 个 Sprint）**：安全止血 → 统一 Action Gateway + 四眼审批 → 项目隔离（行级过滤）→ LDAP + 多项目多角色 → Agent 可靠性（幂等/取消/心跳/证书绑定）→ VM 补齐 + DB 加固 + 审计结构化 + 测试补齐。
- **第二阶段（3–9 月）**：代码仓/CI-CD 接入、版本→代码定位、AI 生成修复、沙箱测试、灰度发布。**前置条件**：第一阶段执行网关与审计必须先落地。
- **第三阶段（9–18 月）**：SAST/SCA/镜像扫描、SBOM、AI 风险分析、选择性修复。**前置条件**：第二阶段的代码索引与构建沙箱。

明确不做：第一阶段不引入消息队列/微服务拆分/多集群控制面；不把二、三阶段功能提前塞进第一阶段。

---

## 10. 无法确认的事项

1. **生产网络暴露面**：仓库内没有实际的 Ingress/NodePort/LB 配置，9090/9096 是否真的可从办公网访问无法从代码确认（compose 是发布的；K8s 需看现场 Service 类型）。
2. **`.env` 实际值**：只读了键名，未读取/记录真实密钥；默认值是否已在生产使用需现场核对。
3. **`ldap3`/`dingtalk-stream`/`lark-oapi` 是否在运行环境已装**：requirements 缺失，但本机 venv 可能有（16 号报告标注了 venv 与 requirements 漂移）。
4. **Go 1.26.6 是否真实存在/内网工具链**：`go.mod` 声明 1.26.6，Dockerfile 用 1.22；构建是否靠 GOTOOLCHAIN 联网拉取未验证（未执行构建）。
5. **Agent 在生产集群的实际 RBAC**：`ClusterRole */*` 是模板值，现场是否收窄未验证。
6. **数据库审核策略的实际配置**：代码支持按库配置审批策略，生产是否全部开启未验证。

---

## 11. 交付物索引

| 文件 | 内容 | 行数 |
|---|---|---:|
| `01-project-overview.md` | 项目定位、规模、启动流程、Git 状态 | 317 |
| `02-technology-stack.md` | 技术栈逐项证据 | 313 |
| `03-module-inventory.md` | 模块清单（路径/职责/接口/状态） | 219 |
| `04-system-architecture.md` | 服务拓扑、调用图、时序图、故障域 | 878 |
| `05-kubernetes-audit.md` | K8s 接入/查询/变更/越权 | 860 |
| `06-agent-audit.md` | Agent 协议/注册/心跳/任务/证书 | 814 |
| `07-vm-audit.md` | SSH/凭据/终端/录像/审计 | 767 |
| `08-database-audit.md` | 接入/只读/审核/慢 SQL/备份 | 571 |
| `09-ai-engine-audit.md` | ReAct/工具/证据/权限/成本 | 477 |
| `10-execution-approval-audit.md` | 审批/执行/旁路/审计 | 811 |
| `11-user-auth-audit.md` | 认证/JWT/LDAP/端点普查 | 651 |
| `12-project-isolation-audit.md` | 项目模型/隔离缺口/越权推演 | 648 |
| `13-permission-audit.md` | 四层权限/绕过路径/审计覆盖 | 800 |
| `14-security-audit.md` | 密钥/注入/SSRF/暴露面 | 704 |
| `15-code-quality-audit.md` | 规模/重复/异常/超时/事务 | 649 |
| `16-test-coverage-audit.md` | 实测测试结果/覆盖缺口 | 492 |
| `17-dependency-security-audit.md` | 依赖/镜像/供应链/密钥扫描 | 519 |
| `18-existing-capabilities.md` | 已实现能力（带证据与可用性） | 见文件 |
| `19-missing-capabilities.md` | 缺口（按三阶段）与伪能力 | 见文件 |
| `20-risk-priorities.md` | 风险总表与修复批次 | 见文件 |
| `21-target-architecture.md` | 目标架构与关键决策 | 见文件 |
| `22-development-roadmap.md` | 三阶段路线图与人力 | 见文件 |

---

## 12. 审计方法（可复现）

- 静态阅读 + `grep -rn` 定位 + `wc -l` 统计 + `go test`/`unittest` 实跑（只读、无网络）。
- 18 份模块报告由并行审计产出，主审对 10 条 P0 逐行回源码复核（MCP、9090/9096、YAML namespace、Secret 明文、内部令牌、DB kind/自批、custom-tasks、registry SSRF、审批加签、go 版本漂移）。
- 未执行：任何写入、任何集群/数据库连接、任何依赖安装、任何构建（Go 工具链版本冲突未验证）。
