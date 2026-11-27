# 02 · 技术栈（Technology Stack）

> 只读审计。所有结论给 `文件:行` 证据；无证据处明确写「暂时无法确认」，不做推断。

---

## 1. 总览：有 / 无 一览

| 能力 | 有无 | 证据 |
|---|---|---|
| 前端 SPA | **有** | `frontend/package.json:11-30` |
| 后端 HTTP API | **有** | `api-server/requirements.txt:2-3`、`api-server/app/main.py:573` |
| 内部 gRPC | **有** | `api-server/requirements.txt:16`、`k8s-executor/proto/executor.proto:9` |
| 反向隧道网关（mTLS） | **有** | `api-server/app/agent_gateway.py`、`.env.example:76` |
| 关系库（平台） | **有（MySQL，硬耦合）** | `.env.example:7`、`api-server/app/services/metrics_rollup.py:137,201` |
| 缓存 / 分布式锁 | **有（Redis）** | `api-server/app/core/redis_client.py:11,42-67` |
| 消息队列（Kafka/RMQ/MQ） | **无** | 全仓无客户端依赖；仅监控指标名 `resource_alert_service.py:75-79` |
| 任务调度 | **有（进程内）** | APScheduler `api-server/requirements.txt:15`（实际后台循环见 `main.py:58-71`）；Go 侧 `robfig/cron` `go.mod:9` |
| ES 日志 | **有（客户端自研 httpx）** | `api-server/app/services/es_client.py:1-25` |
| Prometheus | **有（查询 + 暴露）** | `api-server/app/main.py:631-670`、`api-server/app/services/prometheus_service.py` |
| 分布式链路追踪（OTel/Jaeger） | **无** | 仅 `trace_id` 透传 `main.py:588-596`；全仓无 otel/jaeger/zipkin/sentry 依赖 |
| 前端单测/E2E | **无** | `frontend/package.json:6-10` 无 test 脚本 |
| 容器化 | **有** | 4 个 Dockerfile + `docker-compose.yml` |
| K8s 部署 | **有（裸 YAML）** | `k8s/*.yaml`；无 `Chart.yaml`（Helm 无） |
| Helm | **无** | `find k8s -name Chart.yaml` 为空 |

---

## 2. 前端技术栈

| 项 | 版本 | 证据 |
|---|---|---|
| React | ^18.3.1 | `frontend/package.json:20-21` |
| Ant Design | ^5.21.1 | `frontend/package.json:17` |
| @ant-design/icons | ^5.5.1 | `frontend/package.json:13` |
| @ant-design/charts | ^2.2.1 | `frontend/package.json:12` |
| react-router-dom | ^6.26.2 | `frontend/package.json:22` |
| @tanstack/react-query | ^5.59.0 | `frontend/package.json:14`、`src/main.tsx:12-20`（retry 1 / staleTime 30s） |
| axios | ^1.7.7 | `frontend/package.json:18`、`src/services/api.ts` |
| dayjs | ^1.11.13 | `frontend/package.json:19` |
| xterm | ^6.0.0 + addon-fit ^0.11.0 | `frontend/package.json:15-16`（Web 终端） |
| TypeScript | ^5.5.4 | `frontend/package.json:28` |
| Vite | ^5.4.3 | `frontend/package.json:29`、`vite.config.ts:1` |
| 构建命令 | `tsc -b && vite build` | `frontend/package.json:8` |
| dev 代理 | `/api/v1` → `127.0.0.1:8080`（ws 开启） | `vite.config.ts:11-20` |
| 生产托管 | nginx，SPA fallback + `/api/v1` 反代 `api-server:8080` | `frontend/nginx.conf:1-22` |
| 分包策略 | charts / xterm / query 三类手动 chunk，antd+react 交给 Rollup | `vite.config.ts:29-35` |
| 路由组织 | 全部懒加载；集群内为嵌套整页路由 | `src/App.tsx:12-64,205-230` |
| 大屏 | 独立 iframe 载入静态 `public/monitor-bigscreen.html` | `src/App.tsx:130-138` |

注：前端无 `eslint`/`prettier`/`vitest`/`jest` 依赖（`package.json:24-29` 仅 typescript/vite/@types）。

---

## 3. 后端技术栈（api-server）

| 项 | 版本 | 证据 |
|---|---|---|
| Python | 3.11（镜像 `python:3.11-slim`） | `api-server/Dockerfile:2` |
| FastAPI | 0.115.0 | `requirements.txt:2` |
| Uvicorn | 0.30.6（standard） | `requirements.txt:3` |
| SQLAlchemy | 2.0.35（asyncio） | `requirements.txt:4` |
| MySQL 驱动 | aiomysql 0.2.0 | `requirements.txt:5` |
| PostgreSQL 驱动 | asyncpg 0.31.0 | `requirements.txt:6`（用于**被管** DB，非平台库） |
| Oracle 驱动 | oracledb 4.0.2 | `requirements.txt:7` |
| Alembic | 1.13.3 | `requirements.txt:8`、`api-server/Dockerfile:16` |
| Pydantic | 2.9.2 / pydantic-settings 2.5.2 | `requirements.txt:9-10` |
| Redis 客户端 | redis 5.0.8 | `requirements.txt:11` |
| JWT | python-jose[cryptography] 3.3.0 | `requirements.txt:12` |
| 加密 | cryptography 50.0.2（AES-256-GCM） | `requirements.txt:13`、`app/core/security.py` |
| HTTP 客户端 | httpx 0.27.2 | `requirements.txt:14` |
| 调度 | APScheduler 3.10.4 | `requirements.txt:15`（注意：实际后台任务是自写 `asyncio` 循环，见 `app/main.py:58-71`） |
| gRPC | grpcio 1.66.2 + grpcio-tools | `requirements.txt:16-17` |
| 密码哈希 | bcrypt 4.2.0 | `requirements.txt:18` |
| SSH | asyncssh 2.24.1 | `requirements.txt:19`（主机纳管） |

配置管理：`pydantic-settings` 统一从项目根 `.env` 读取，`extra="ignore"`，代码无硬编码默认密钥（`app/core/config.py:9-10,124-128`）。

---

## 4. AI 服务技术栈（ai-service）

| 项 | 版本 | 证据 |
|---|---|---|
| Python / FastAPI / Uvicorn | 3.11 / 0.115.0 / 0.30.6 | `ai-service/Dockerfile:2`、`requirements.txt:2-3` |
| OpenAI SDK | 1.51.0（`AsyncOpenAI`） | `requirements.txt:10`、`app/services/model_openai.py:3,14` |
| Anthropic SDK | 0.39.0（Claude） | `requirements.txt:11`、`app/services/model_claude.py` |
| DeepSeek | 自研 HTTP 适配（`POST https://api.deepseek.com/v1/chat/completions`） | `app/services/model_deepseek.py:10,13` |
| numpy | 2.1.1 | `requirements.txt:12` |
| Redis | 5.0.8（仅 /ready 探活与可选缓存） | `requirements.txt:13`、`app/main.py:94-101` |
| 向量检索 | 自研 RAG（`rag_service.py` + `embedding_service.py`，embedding 走远程 API） | `ai-service/Dockerfile:1` 注释「embedding 走远程 API，无需本地模型依赖」 |

服务鉴权：所有非探活路径必须带 `X-Internal-Token`，值必须等于 `JWT_SECRET_KEY`（`app/main.py:37-49`）；豁免路径 `/health,/live,/ready,/docs,/openapi.json`（`:44`）。

---

## 5. Executor / Agent 技术栈（Go）

| 项 | 版本 / 说明 | 证据 |
|---|---|---|
| Go | **go.mod 声明 1.26.6** | `k8s-executor/go.mod:3` |
| gRPC | google.golang.org/grpc 1.83.0 | `go.mod:14` |
| protobuf | 1.36.12 | `go.mod:15` |
| client-go | k8s.io/client-go/api/apimachinery/metrics **v0.36.3** | `go.mod:18-21` |
| 日志 | go.uber.org/zap 1.28.0（JSON） | `go.mod:11`、`api-server/app/core/logging.py:9`（与 Go zap 格式对齐） |
| 配置 | spf13/viper 1.21.0 + godotenv | `go.mod:10,8` |
| ORM / 驱动 | gorm 1.31.2 + gorm mysql 1.6.0、go-sql-driver/mysql、jackc/pgx v5 | `go.mod:16-17,6-7` |
| 定时 | robfig/cron/v3 3.0.1 | `go.mod:9` |
| 并发 | golang.org/x/sync 0.24.0 | `go.mod:13` |
| 加密 | golang.org/x/crypto 0.58.0（与 Python 端 AES-GCM 互测） | `go.mod:12`、`Makefile:48-55` |
| 构建 | `CGO_ENABLED=0 go build -o /executor ./cmd/executor` | `k8s-executor/Dockerfile:12` |
| 运行镜像 | alpine + grpc_health_probe v0.4.24 | `k8s-executor/Dockerfile:15-20` |
| 构建镜像 | golang:1.22-alpine（**与 go.mod 1.26.6 不一致**） | `k8s-executor/Dockerfile:2` |
| Agent 构建 | 同一 Go module，`./cmd/agent`，静态 linux/amd64，debian bookworm-slim 运行 | `agent/Dockerfile:15-38,41` |
| Agent 版本注入 | `-X main.version=${VERSION}` | `agent/Dockerfile:36-38`、`agent/Makefile:22-23` |
| 包结构 | 19 个 internal 包（executor/watcher/agentbridge/agentdb/logstream/metrics/quota/jvm/rbac/script/hostagent…） | `ls k8s-executor/internal` |

gRPC 契约：`ExecutorService` 共 **49 个 rpc**，含 `ExecutePlan`(stream)、`ExecPodStream`(双向流)、`QueryPrometheus`、`JvmDiagnose` 等（`proto/executor.proto:9-104`）；Agent 侧只有一条双向流 `AgentService.Tunnel`（`proto/agent/agent.proto:11-12`）。
**双份 proto 强一致要求**：`k8s-executor/proto/executor.proto` 与 `api-server/app/proto/executor.proto` 由 CI diff 校验（`scripts/ci_check.sh:6-7`）。

---

## 6. 数据库 / 缓存

### 6.1 平台主库：MySQL（硬耦合，不可替换）

- 连接串：`mysql+aiomysql://root:root@192.168.6.99:3306/autohealops`（`.env.example:7`）。
- Alembic 直接读该变量（`api-server/alembic/env.py:21`）。
- **MySQL 专有语法**出现在核心保留期逻辑中：`information_schema.partitions`、`ALTER TABLE ... DROP PARTITION`、`REORGANIZE PARTITION ... VALUES LESS THAN (MAXVALUE)`（`api-server/app/services/metrics_rollup.py:137,185,201-208`）。结论：平台库只能是 MySQL（5.7 需分区支持/8.x 均可，具体版本**暂时无法确认**）。
- 读写方：api-server、ai-service（共享同一 `DATABASE_URL`，`ai-service/app/core/config.py:17`）、k8s-executor（`internal/database/db.go:18-19` gorm+mysql，`internal/watcher/sink.go:215` 直接 `db.Create(&alert)`）。
- ORM：87 个模型类、123 个迁移版本（单 head `r81a1b2c3d4e5`），迁移只能由 api-server 执行（`Makefile:25-27`、`api-server/Dockerfile:16`）。

> 注意区分：`asyncpg`（PostgreSQL）、`oracledb`（Oracle）、executor 里的 `pgx` 是**被管理数据库**的多方言支持（`app/services/dialects/`、`k8s-executor/internal/agentdb/dialect.go`），不是平台自身存储。

### 6.2 缓存 / 锁：Redis

- 客户端：`redis==5.0.8`（api-server `requirements.txt:11`、ai-service `requirements.txt:13`）。
- 用途（代码可证）：
  - 分布式锁 + 续租（Lua 脚本，`app/core/redis_client.py:42-67`）；
  - JWT 登出黑名单 + Redis 不可用降级放行（`app/middleware/auth.py:31-40`）；
  - 登录防爆破计数 5 次 / 300s（`app/api/auth.py:15-16,31-38`）；
  - 仪表盘缓存代次（`app/core/orm_hooks.py:9,22`）。
- Redis 服务端版本：**暂时无法确认**（未连接）。

### 6.3 消息队列：无

未发现任何 MQ 客户端依赖（`requirements.txt`/`go.mod`/`package.json` 均无 kafka/rabbitmq/rocketmq/pika/nats/celery）。`resource_alert_service.py:75-79` 只是「监控别的 MQ 的 Prometheus 指标名」，不代表自用。

---

## 7. AI 模型接入方式

- **三家适配器 + 工厂**：`provider ∈ {openai, deepseek, claude}`（`api-server/app/models/ai_model.py:17` 注释、`ai-service/app/services/model_adapter.py:119-133`）。
- **OpenAI 兼容**：`OpenAIAdapter` 用 `AsyncOpenAI(base_url=config.api_base or None)`（`model_openai.py:14`），即任何 OpenAI 兼容网关都能接。
- **DeepSeek**：自研 httpx 直连 `https://api.deepseek.com`（可被 `api_base` 覆盖）（`model_deepseek.py:10,13`）。
- **Claude**：Anthropic SDK（`requirements.txt:11`、`model_claude.py`）。
- **配置来源是数据库不是 env**：`ai_model_configs` 表（`api-server/app/models/ai_model.py:11-31`），API Key **AES-256-GCM 加密存储**（`ai-service/app/services/model_adapter.py:38-60` 基类与 `_decrypt_key` 解密逻辑），支持 `priority` 降级链、`min_severity` 分级选模型、`is_default`。
- **成本控制**：`DAILY_COST_LIMIT` / `SINGLE_ALERT_COST_LIMIT`（`.env.example:47-49`），调用写 `ai_call_logs`（`ai_model.py:33`）。
- **温度/超时**：结构化输出低温 0.2（`ai-service/app/core/config.py:47`）、单模型 HTTP 120s（`:43`）。
- **前端不直连 AI**：`api-server/app/api/ai_proxy.py`（prefix `/api/v1/ai`）做代理，ai-service 只信任内部 token。

---

## 8. 部署方式

| 方式 | 组成 | 证据 |
|---|---|---|
| 本地（主用） | `start.sh` nohup 4 进程 + 预编译 `k8s-executor/bin/executor` | `start.sh:84-164`、`k8s-executor/bin/executor`（73MB 产物存在） |
| Docker Compose | 4 服务，端口 8080/9090/9000/80，MySQL+Redis 外部 | `docker-compose.yml:3-41` |
| Kubernetes | 5 个 Deployment（api-server 2 副本、agent-gateway 1、executor 1、ai-service 2、frontend 2）+ Service/HPA/PDB/NetworkPolicy/Ingress + 备份/Purge CronJob | `k8s/deployments.yaml:11-271`、`k8s/ingress.yaml:9-20`、`k8s/backup-cronjob.yaml:26-28`、`k8s/retention-cronjob.yaml:24-26` |
| 配置事实源 | 根 `.env` → `gen-k8s-config.sh` → ConfigMap/Secret（generated 目录 gitignore） | `k8s/README.md:3-16` |

部署拓扑约束（文档声明且代码一致）：agent 隧道连接表在进程内，`AGENT_GATEWAY_MODE=remote` 时拆成独立单副本 Deployment（`k8s/README.md:31-34`、`api-server/app/core/config.py:97-101`、`app/main.py:550-557`）。NetworkPolicy 限制 ai-service/executor 只接受 api-server 入站（`k8s/networkpolicy.yaml:1-20`）。

---

## 9. 可观测性

| 维度 | 实现 | 证据 |
|---|---|---|
| 指标暴露 | `/metrics` 输出 Prometheus 文本（集群数/健康数/待处理告警/待审批/AI 成本等），**无鉴权** | `api-server/app/main.py:631-670` |
| 指标采集 | api-server 查询 Prometheus（`prometheus_service.py`），executor 代理 `QueryPrometheus` | `proto/executor.proto:41`、`app/services/prometheus_service.py` |
| Pod 指标存储 | 自建 `pod_metrics` 表 + 5 分钟降采样 + 分区保留（24h/7d） | `app/services/metrics_rollup.py`、`.env.example:82-83` |
| 日志 | Python JSON 结构化（service/level/ts/trace_id），Go zap JSON 对齐 | `app/core/logging.py:9-33`、`go.mod:11` |
| 链路 | 仅 `X-Trace-Id` 透传 + 审计表；**无分布式追踪 SDK** | `app/main.py:588-596`、全仓 grep otel/jaeger/zipkin 无命中 |
| 日志检索 | Elasticsearch（自研 httpx 客户端，配置存 `log_sources`/`system_settings`） | `app/services/es_client.py:1-25` |
| 探针 | `/health`（不看依赖）、`/ready`（DB+Redis） | `app/main.py:608-629`；ai-service 同构 `app/main.py:69-107` |
| 日志轮转 | `scripts/rotate_logs.sh`（copytruncate，保留 7 份 >50MB 触发） | `Makefile:44-46` |

---

## 10. 测试框架

| 端 | 框架 | 规模 | 证据 |
|---|---|---|---|
| api-server | 标准库 `unittest` | 97 个 `test_*.py` | `Makefile:36-38`、`api-server/tests/` |
| ai-service | 标准库 `unittest` | 14 个 | `Makefile:38`、`ai-service/tests/` |
| k8s-executor | Go `testing` | 12 个 `_test.go`（28.7k 行代码） | `find k8s-executor -name "*_test.go" \| wc -l` |
| 跨语言 | Python↔Go AES-GCM 向量互测 | `make crypto-test` 3 步 | `Makefile:48-55` |
| frontend | **无** | 0 | `package.json:6-10` |

---

## 11. 构建与 CI

- 前端：`npm run build` = `tsc -b && vite build`（`package.json:8`）；Docker 多阶段 node:20-alpine → nginx:alpine（`frontend/Dockerfile:1-11`）。
- 后端镜像：`python:3.11-slim`，`CMD` 先 `alembic upgrade head` 再起 uvicorn（`api-server/Dockerfile:15-16`）。
- Executor 镜像：多阶段，`CGO_ENABLED=0` 静态二进制（`k8s-executor/Dockerfile:1-27`）。
- CI：GitHub Actions 单 job（Python 3.11 / Node 20 / Go 1.22，`.github/workflows/ci.yml:15-28`）执行 `scripts/ci_check.sh` 8 步：proto 一致 → 两套 `compileall` → `tsc -b --noEmit` → `go build` → 两套 unittest → 加密互测 → OpenAPI 漂移 → K8s 配置覆盖度（`scripts/ci_check.sh:6-40`）。

---

## 12. 问题清单

```
问题编号：TECH-01
问题名称：ai-service CORS 配置 allow_origins=["*"] 且 allow_credentials=True
问题等级：P2
所属模块：ai-service / 安全配置
问题描述：ai-service 全局 CORS 允许任意来源且允许携带凭据（app/main.py:57-63）。虽然该服务只应被 api-server 调用并有 X-Internal-Token 中间件，但 CORS 配置与「仅内部」定位矛盾；若未来网关暴露 9000 或浏览器可直达，会放大风险（浏览器侧带 cookie 请求被任意站点读取响应）。
代码位置：ai-service/app/main.py:57-63
涉及函数或类：CORSMiddleware 注册块
调用链：浏览器 → :9000/api/v1/ai/* 
根本原因：模板复制（与 api-server 的显式白名单策略相反，api-server 见 app/main.py:576-586）
可能影响：内部服务被误暴露时跨源读取诊断数据/模型列表
修改建议：改为显式白名单（或干脆不注册 CORS，内部调用无需 CORS）
验证方法：curl -H "Origin: http://evil.com" -I http://host:9000/api/v1/ai/models 观察 Access-Control-Allow-Origin
是否需要立即处理：否（前提是 9000 不对外；K8s NetworkPolicy 已限制，见 k8s/networkpolicy.yaml）
```

```
问题编号：TECH-02
问题名称：内部服务鉴权复用 JWT_SECRET_KEY 作为共享静态口令
问题等级：P2
所属模块：ai-service / 服务间鉴权
问题描述：ai-service 用 `X-Internal-Token == JWT_SECRET_KEY` 判断来源（app/main.py:46-48），即用户态 JWT 签名密钥同时充当服务间共享密钥。密钥一旦用于两处，轮换任一端会同时影响登录与内部调用；且静态口令无时效、无法区分调用方。
代码位置：ai-service/app/main.py:44-49、api-server/app/core/config.py:26
涉及函数或类：internal_auth_middleware
调用链：api-server(ai_client/ai_proxy) → HTTP :9000 
根本原因：省事复用同一 env 变量（.env.example:13 与 :53 注释也印证「复用 JWT_SECRET_KEY」）
可能影响：密钥泄漏面扩大；无法按调用方撤销
修改建议：独立 `INTERNAL_SERVICE_TOKEN`（或 mTLS/短时 JWT），与 JWT_SECRET_KEY 解耦
验证方法：改 JWT_SECRET_KEY 后观察登录与 AI 诊断是否同时失效
是否需要立即处理：否
```

```
问题编号：TECH-03
问题名称：Go 工具链版本三处不一致（go.mod 1.26.6 vs Dockerfile/CI 1.22）
问题等级：P2
所属模块：k8s-executor / 构建
问题描述：见 01-OPS-03 同源问题，从技术栈角度补充：Go 1.21+ 的 toolchain 机制会在 go.mod 要求更高版本时自动下载工具链（`GOTOOLCHAIN` 默认 auto），Dockerfile 未显式设置，受限网络下构建失败；CI 固定 go-version 1.22 也存在同样风险。
代码位置：k8s-executor/go.mod:3、k8s-executor/Dockerfile:2、.github/workflows/ci.yml:28
涉及函数或类：—
调用链：docker build → go build → toolchain 下载
根本原因：本地 Go 升级后 go.mod 被改写
可能影响：构建不稳定（依赖外网）、版本口径混乱
修改建议：三处统一；或在 Dockerfile 显式 `ENV GOTOOLCHAIN=local` 并降 go.mod
验证方法：离线环境 `go build ./cmd/executor`
是否需要立即处理：否
```

```
问题编号：TECH-04
问题名称：/metrics 无鉴权暴露业务计数
问题等级：P3
所属模块：可观测性 / 安全
问题描述：`GET /metrics` 不需要任何认证，返回集群总数、健康集群数、待处理告警、待审批单数（app/main.py:631-670）。若 8080 被反代到公网（Ingress 只转发 /api/v1 与 /，k8s/ingress.yaml:12-20，风险较低），会泄漏运营数据。
代码位置：api-server/app/main.py:631-670
涉及函数或类：metrics()
调用链：任意客户端 → GET /metrics
根本原因：为 Prometheus 抓取便利放弃鉴权
可能影响：低（仅计数，无敏感明细）
修改建议：加内网 IP 白名单或 basic auth，或在 Ingress 层屏蔽 /metrics
验证方法：curl http://host:8080/metrics 不带 token 是否 200
是否需要立即处理：否
```

```
问题编号：TECH-05
问题名称：两套 executor.proto 靠 CI diff 保证一致，缺代码生成流水线
问题等级：P3
所属模块：k8s-executor / 契约管理
问题描述：Python 侧 `api-server/app/proto/executor.proto` 与 Go 侧 `k8s-executor/proto/executor.proto` 是两份物理文件，只在 CI 里 `diff -q` 校验（scripts/ci_check.sh:6-7），生成的 stub（*.pb.go / *_pb2.py）提交在仓库内。改协议要手工同步 + 手工重生成，容易出现「proto 一致但 stub 未重生成」的漂移。
代码位置：scripts/ci_check.sh:6-7、k8s-executor/proto/executor.pb.go、api-server/app/proto/
涉及函数或类：—
调用链：改 proto → 手工 grpc_tools.protoc / protoc → 提交
根本原因：双语言单仓未做生成流水线
可能影响：stub 与 proto 不一致时运行期字段丢失/序列化错误
修改建议：加 `make proto` 生成脚本并纳入 CI（生成后 git diff 必须为空）
验证方法：改 proto 字段不重生成 stub，跑 ci_check 是否失败
是否需要立即处理：否
```

---

## 13. 无法确认事项

| 事项 | 卡点 |
|---|---|
| MySQL / Redis 服务端版本 | 只读审计不连中间件；代码只锁客户端库 |
| Prometheus / ES 服务端版本与地址 | 配置在 `system_settings` 表或 `.env`（空值），未连接 |
| embedding 模型实际供应商 | `embedding_service` 走远程 API，具体 endpoint 由模型配置决定（`ai-service/Dockerfile:1`；`.env.example:38` 默认 `sentence-transformers/...` 但注释与实现可能不一致）→ 需运行时确认 |
| 前端生产是否启用了 CDN/压缩 | `vite.config.ts` 未配 `base`/压缩插件，nginx.conf 无 gzip 配置段（只读到前 22 行）→ 暂时无法确认是否有外部 nginx |
| K8s 集群实际版本（client-go v0.36.3 对应 K8s 1.36 时代） | 未连接集群 |

---

## 14. 覆盖范围

**读了**：`api-server/requirements.txt`、`Dockerfile`、`app/core/config.py`、`app/core/redis_client.py`、`app/core/logging.py`、`app/main.py`、`app/models/ai_model.py`、`app/services/metrics_rollup.py`（关键段）、`app/services/es_client.py`（头部）、`ai-service/requirements.txt`、`Dockerfile`、`app/main.py`、`app/core/config.py`、`app/services/model_adapter.py`、`model_openai.py`、`model_deepseek.py`、`k8s-executor/go.mod`、`Dockerfile`、`proto/*.proto`、`internal/database/db.go`、`internal/watcher/sink.go`、`agent/Dockerfile`、`agent/Makefile`、`frontend/package.json`、`vite.config.ts`、`nginx.conf`、`docker-compose.yml`、`k8s/*.yaml`、`.github/workflows/ci.yml`、`scripts/ci_check.sh`、`Makefile`。

**没读**：三个 `internal/` 包的实现细节（只看依赖与包名）、前端 `src/services/api.ts` 全文、AI 提示词工程实现（`prompt_engine.py` 等）、`docs/*.md` 技术说明（以代码为准，不采信文档）。
