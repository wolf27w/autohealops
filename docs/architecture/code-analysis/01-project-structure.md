# 01 · 全局目录结构与服务职责

> **代码基线**：`main @ b3b9f03`（2026-11-27 09:22）
> **分析方式**：只读源码检索（`grep` / `wc -l` / AST 静态统计）+ 关键文件逐行阅读；未运行构建、测试、迁移，未安装依赖，未连接数据库或集群。
> **路径约定**：本文所有 `x/y.py:N` 均相对项目根 `/Users/wolf/Documents/project/autohealops/`。
> **标注口径**：【事实】= 源码可直接验证；【推断】= 基于证据的分析判断；【待验证】= 需运行期或回归验证。
> **排除范围**：`.venv/`、`node_modules/`、`__pycache__/`、`*.pyc`、`*.gz`、`bin/`、`dist/`、`proto/*.pb.go`、`*_pb2*.py` 等生成代码与缓存。

---

## 0. 结论摘要

1. 【事实】这是一个 **模块化单体 + 卫星执行器** 结构：`api-server` 是唯一对外 API 与业务中枢（67 个 API 文件、495 个路由装饰器、109 个 service、59 个模型文件、123 个迁移），`ai-service` / `k8s-executor` / agent 都是它的能力外延。
2. 【事实】`agent/` 一级目录**不含任何 Go 源码**，只是构建与部署物料；agent 真实源码在 `k8s-executor/cmd/agent`，与 executor 同属一个 Go module。
3. 【事实】`api-server` 明显过载：单个 `main.py` 承载全部后台调度（26 处 `create_task`），单个 `api/resources.py` 2,883 行覆盖 7 个业务域，单个 `services/alert_auto_service.py` 3,867 行覆盖 10 个子域。
4. 【事实】存在可确认的历史遗留与重复：2 个前端孤儿页面、5 对跨服务同名但实现分叉的核心模块、2 份重复的 proto 契约、ai-service 重复定义 14 张表、123 个迁移中多组 `add → drop → add`。
5. 【推断】结构问题的主因不是「目录分得不够细」，而是**分层边界没有约束**：api 层直接写 SQL、直调 gRPC，service 之间互相 import，跨服务靠「口头契约」共享表结构。

---

## 1. 分析方法与规模口径

【事实】本次实测源码规模（`find ... -print0 | xargs -0 wc -l`）：

| 范围 | 文件数 | 行数 |
|---|---:|---:|
| `api-server/app/*.py` | 272 | 68,888 |
| `ai-service/app/*.py` | 33 | 4,979 |
| `frontend/src/*.ts` | 19 | 4,483 |
| `frontend/src/*.tsx` | 128 | 30,580 |
| `k8s-executor/*.go` | 78 | 28,750 |

> 【事实】`docs/architecture/audit/23-audit-summary.md` 记录的 api-server 88,121 行是**整个 api-server 目录下全部 .py**（含 tests、alembic），与上表 `app/` 口径不同，两者不矛盾。
> 【事实】路由数口径：本次 grep 到 495 个路由装饰器；审计报告 500 条 = 495 HTTP + 4 WS + 1 catch-all，同样不矛盾。

---

## 2. 一级目录

| 目录 | 实际作用 | 证据 |
|---|---|---|
| `agent/` | agent 的 Dockerfile / Makefile / install.yaml 示例 / 预编译二进制，**0 个 .go 文件** | `agent/README.md:15-31` 明确「Go 源码在 k8s-executor/cmd/agent」；`agent/Makefile:22-23` 编译 `./cmd/agent` |
| `ai-service/` | FastAPI AI 服务：诊断、方案、验证、RAG、Embedding、Prompt 管理 | `ai-service/app/main.py:34`；`app/api/core_api.py:13-83`、`aux_api.py:17-765` |
| `api-server/` | 唯一对外 API + 全部业务编排 + 平台库 schema owner（alembic） | `api-server/app/main.py:573`；`alembic/versions/` 123 个版本 |
| `data/` | **运行产物**，非源码：agent CA 私钥、961 个诊断会话 jsonl、Oracle Instant Client | `data/agent-ca/ca.key`、`data/agent-sessions/100.jsonl`、`data/oracle_clients/.../libclntsh.dylib` |
| `docs/` | 需求/设计/审计/运维手册/原型 HTML，约 117 个 md | `docs/ARCHITECTURE.md`、`docs/architecture/audit/01~23` |
| `frontend/` | React SPA + nginx 静态托管 | `frontend/src/main.tsx:34`、`src/App.tsx:124` |
| `k8s/` | 15 份部署清单（Deployment/Service/Ingress/HPA/PDB/NetworkPolicy/PVC/备份 CronJob） | `k8s/deployments.yaml:13,83,144,196,250` |
| `k8s-executor/` | Go：executor 与 agent 双入口 + 20 余个 internal 包 | `k8s-executor/cmd/executor/main.go:33`、`cmd/agent/main.go:84` |
| `logs/` | 运行日志、轮转归档、JSON 备份（含 `executor.log` 约 875MB） | `logs/executor.log`、`logs/archive/` |
| `scripts/` | 运维脚本 18 个：CI、备份/恢复、发布/回滚、日志轮转、数据修复 | `scripts/ci_check.sh:8-40`、`scripts/merge_duplicate_alerts.py` |
| `templates/` | **UI 换肤素材**，不参与构建：before/after 截图、5 套 HTML 候选稿、无头浏览器截图脚本 | `templates/README.md:1-11,33-45`；`templates/tools/shot.mjs` |
| 根文件 | 编排入口 | `start.sh:98-121`、`docker-compose.yml:4-41`、`Makefile:2`、`install.sh:1-3`、`autohealops:1-3`、`PROMPT-FINAL.md`（94KB 历史总纲） |

【推断】`data/`、`logs/`、`templates/` 三个目录与源码并列放在根目录，容易被误当作交付物的一部分；其中 `data/agent-ca/*.key` 是运行期私钥，属于部署资产而非代码资产。

---

## 3. 各服务内部目录职责

### 3.1 `api-server/app/`

| 子目录 | 职责 | 规模/证据 |
|---|---|---|
| `api/` | HTTP/WS 路由层 | 67 文件 / 495 路由装饰器 / 19,176 行；`main.py:671-737` 平铺注册 66 个 router |
| `services/` | 业务编排（告警、诊断、审批、执行、通知、DB、主机、发布、报表……） | 109 文件；含 `dialects/` 子包（mysql/pg/oracle/base） |
| `models/` | SQLAlchemy ORM，平台库 schema 的唯一主定义 | 59 文件 / 87 张表；`models/__init__.py:3-60` 导出 80+ 类 |
| `clients/` | 跨服务通信 | `ai_client.py:16`（HTTP :9000）、`executor_client.py:51`（gRPC :9090，1,407 行） |
| `core/` | config / database / redis_client / security / http_client / orm_hooks / fault_taxonomy | `core/database.py:20-30` 异步连接池 |
| `middleware/` | JWT 解析与角色依赖 | `auth.py:19`、`permission.py:9,23-25` |
| `proto/` | **手写 .proto 与生成的 _pb2.py 混放** | `executor.proto`、`executor_pb2.py`、`executor_pb2_grpc.py` 同目录 |
| 顶层 | `main.py`、`agent_gateway.py`、`agent_gateway_main.py`、`agent_gateway_service.py` | `main.py:551-558` inproc/remote 二选一 |

### 3.2 `ai-service/app/`

| 子目录 | 职责 | 证据 |
|---|---|---|
| `api/` | 2 个文件 46 个路由：`core_api.py`（诊断/方案/验证/agent）、`aux_api.py`（模型/日志/ES/Embedding/RAG/Prompt） | `core_api.py:13,23,33,43,53,68,83`；`aux_api.py:441-765` |
| `services/` | 22 个文件：模型适配器、诊断、方案、验证、RAG、Prompt 引擎、路由、健康 | `services/model_adapter.py:184`、`diagnosis_service.py:132`、`rag_service.py:66` |
| `models/` | 仅 `__init__.py`（247 行），**重复定义 api-server 的表** | `models/__init__.py:2` 注释自述「与 api-server 对应表字段一致」 |
| `core/` | config / database / security / http_client / es_client | `core/config.py:15-35` 共享 DB、JWT、加密密钥 |
| `main.py` | 内部鉴权 + 健康探针，不建表 | `main.py:38-49` `X-Internal-Token`；`:52-55` Embedding 配置异常转 503 |

### 3.3 `frontend/src/`

`pages/` 53 个、`components/` 62 个、`services/api.ts` 1,455 行、`utils/` 6 个。`constants.ts`(15 行) 与 `constants/nav.ts`(186 行) **两个常量入口并存**；`App.tsx:12-64` 全量 lazy 加载、`:128-230` 集中路由；`public/monitor-bigscreen.html` 是 980 行原生 HTML 大屏，不进 TS 构建体系。

### 3.4 `k8s-executor/`

`cmd/executor/main.go:33` 与 `cmd/agent/main.go:84` 两个 main 共用 `internal/` 20+ 包：`executor/`（真实 K8s 动作）、`watcher/`（Pod/Node 故障检测 + sink）、`k8sclient/`（多集群管理与 AES-GCM 解密）、`agentbridge/`（进程内 gRPC 反代 + pull 服务）、`agentdb/`、`eventstore/`、`grpc/`（1,126 行统一 server）、`quota/`、`prometheus/`、`script/`、`logstream/`。`proto/` 同时放 `.proto` 与 `.pb.go` 生成码。

### 3.5 `agent/`

无源码。`README.md:99-112` 给出运行参数（`-platform` / `-cluster-id` / `-token` / `-ca,cert,key-file` / `-watch` / `-whitelist`）；`Dockerfile:15-55` 多阶段构建；`build/agent` 为 56MB 静态链接 ELF。

---

## 4. 五个服务的职责五要素

| 服务 | 入口 | 核心模块 | 核心业务对象 | 对外接口 | 依赖 | 职责越界实例 |
|---|---|---|---|---|---|---|
| frontend | `main.tsx:34` / `App.tsx:127` | 53 pages / 62 components / `services/api.ts` | 集群、告警、诊断、审批、执行、报表 | nginx :80 → `/api/v1` | 仅 api-server | `services/api.ts` 一个文件集中全部领域 API；`components/{eval,trace}/api.ts` 又另起 157/105 行【推断】 |
| api-server | `main.py:573`；lifespan `:544` | `api/`、`services/`、`models/`、`clients/` | Alert / RepairPlan / Approval / Cluster / Ticket / KnowledgeEntry | :8080 HTTP；:9095 agent 网关（inproc） | MySQL、Redis、ai-service、k8s-executor、ES、Prometheus | 内嵌 gRPC 网关 `agent_gateway.py` 440 行；api 层直接写 SQL（`resources.py:491`）【事实】 |
| ai-service | `main.py:34` | model_adapter / diagnosis_service / prompt_engine / rag_service | AIModelConfig / PromptTemplate / KnowledgeEntry / AIDiagnosisResult | :9000 HTTP（内部令牌） | 同一 MySQL、LLM API、ES | `models/__init__.py` 直连平台库并维护自有 ORM【事实】 |
| k8s-executor | `cmd/executor/main.go:33` | `internal/executor`、`watcher`、`k8sclient`、`grpc` | Cluster kubeconfig / Pod / Watcher 事件 | gRPC :9090、日志流 :9099 | MySQL（**直写 alerts**）、K8s API、Prometheus | watcher 直接 GORM 写平台中心库（`internal/watcher/sink.go:137-144`）【事实】 |
| agent | `k8s-executor/cmd/agent/main.go:84` | `agentbridge`、`watcher`、`agentdb`、`eventstore` | 隧道帧、集群内采集数据 | 主动外连 :9095（mTLS）；pull 模式 :9100 | 平台网关、被管集群 K8s API | 二进制内嵌完整 `ExecutorService`（`main.go:142-143`、`bridge.go:116-127`），既是客户端又是服务端【事实】 |

---

## 5. `api-server` 过载量化

【事实】

| 维度 | 数量 | 证据 |
|---|---:|---|
| router / API 文件 | 66 / 67 | `main.py:671-737`、`find app/api -name '*.py'` |
| 路由装饰器 | 495 | grep 统计 |
| service 文件 | 109 | `find app/services -name '*.py'` |
| ORM 模型文件 / 表 | 59 / 87 | `models/__init__.py:3-60` |
| alembic 版本 | 123 | `alembic/versions/` |
| 后台协程 | 26 处 `create_task` | `main.py`（`startup_tasks:294-540` 单函数 247 行） |
| 定时 worker | 13 个 `_interval_worker` + 12 个 `background_loop` | `main.py:403-505`、`:370` 告警自动处理 30s |
| 业务域 | 7 大类 / 66 领域模块 | 告警 7、集群 11、库 5、主机 8、诊断 10、审批 12、平台 19 |

【推断】过载的核心不是文件数量，而是**三件事集中在同一进程**：① 全部对外 HTTP 路由；② 全部后台调度（无 leader 选举）；③ agent 隧道网关（inproc 模式）。这使任何一处慢调用或状态变更的影响面都是全站级的。

---

## 6. 职责重叠、历史遗留与无效模块

### 6.1 跨服务同名模块分叉【事实】

`api-server/app/core/` 与 `ai-service/app/core/` 存在 5 对同名文件，`md5` 全部不同：`http_client.py`(47/45 行)、`database.py`(39/35)、`security.py`(82/32)、`es_client.py`(318/89)、`fault_taxonomy.py`(170/62)。另有 `api-server/app/services/diag_category.py` ↔ `ai-service/app/services/diagnosis_impact_prompt.py` 等语义重复。

【推断】这是「复制而非共享包」的产物：配置、连接池、加密、ES 客户端、故障分类规则各自演进，改一处必须人工同步另一处。

### 6.2 表结构双份定义【事实】

`ai-service/app/models/__init__.py:2` 自述「与 api-server 对应表字段一致；表结构由 api-server alembic 管理」，重复定义 14 张表（`:24,34,53,70,95,115,127,141,154,167,183,195,223,237`），且 ai-service 无 alembic 目录。

### 6.3 proto 契约双份 + 生成码混放【事实】

`executor.proto` / `agent.proto` 在 api-server 与 k8s-executor 各存一份；`api-server/app/proto/` 内 `.proto` 与 `*_pb2.py` 同目录；`k8s-executor/proto/` 内 `.proto`、`executor.pb.go`(9,888 行)、`executor_grpc.pb.go`(2,030 行) 同目录。`scripts/ci_check.sh:28-33` 用一致性校验来弥补物理重复。

### 6.4 迁移历史反复【事实】

123 个版本中存在成组 `add → drop → add`：资源治理（`v5w6…` / `a2b3…` / `bfe0…`）、自愈策略（`44d1…` / `d6e7…` / `a1b2…`）、cluster_db_connections（`a1b2…` / `c9d0…`），另有 `l5m6…drop_alerts_source`、`e0f1…drop_decision_approval_timeout`。

### 6.5 前端孤儿页【事实】

`frontend/src/pages/DiagnosticCenter.tsx`、`FaultDiagnosis.tsx` 无任何组件 import：`App.tsx` 有 47 处 lazy / 54 处 `<Route>`，但这两个名字 0 命中；全仓检索仅命中 `types.ts:424` 的类型名与文档注释。【待验证】`docs/RESOURCE-CENTER-LAYOUT-REQUIREMENTS-2026-11-13.md:44` 曾称 DiagnosticCenter 是活代码，与当前代码不符，需确认是否刻意保留待回迁。

### 6.6 多套原型与常量入口【事实】

`frontend/public/*.html` 17 个 demo、`docs/mockups/` 6 个、`templates/proposals/` 5 套 HTML 并存；`src/constants.ts` 与 `src/constants/nav.ts` 并存。

### 6.7 网关三文件为双模式残留【推断】

`agent_gateway.py`(440 行)、`agent_gateway_main.py`(43)、`agent_gateway_service.py`(87)：仅 `agent_gateway_main.py:12` 引用另两者，`main.py:551-558` 依据 `AGENT_GATEWAY_MODE` 二选一。三份文件共同描述一个组件，缺少单一入口。

### 6.8 文档多套口径【事实】

`docs/` 下 `DEV-PLAN-{11-13,11-18}`、`DEV-ORDER-{11-06,11-09}`、`REQUIREMENTS-TODO-{11-20,11-23}`、`ROADMAP.md` / `AGENT-ROADMAP.md`、`BACKLOG-2026-11-13.md` / `REQUIREMENTS-BACKLOG.md` 并存；`docs/DUPLICATION-AUDIT.md`、`docs/LEGACY-ITEMS-2026-11-23.md` 自承重复。`README.md:157` 仍指向已过期的 `ROADMAP.md`。

---

## 7. 结构性问题清单（衔接后续文档）

| 编号 | 问题 | 详见 |
|---|---|---|
| S-01 | api 层直接写 SQL / 直调 executor，分层边界未约束 | 02、04 |
| S-02 | `alert_auto_service.py` ↔ `approval_service.py` / `action_verify.py` 循环依赖 | 02 |
| S-03 | 全局可变状态（网关连接表、RAG 索引、缓存代次） | 02 |
| S-04 | 4 个 >1000 行巨型文件 + 2 个 >1000 行超长函数 | 04 |
| S-05 | 重复权限校验 `_deny_cluster` 复制 5 处 | 04、05 |
| S-06 | 前后端重复维护环境/风险/状态枚举 | 04 |
| S-07 | 表结构双份定义、无 ForeignKey、状态机分散 | 04（数据模型章节）、02 |
| S-08 | 核心链路测试空白（审批→执行、审计链、Agent 隧道 Python 侧） | 04（测试章节） |
| S-09 | 生成代码与手写代码混放，契约双份 | 01 §6.3 |
| S-10 | 孤儿页面、遗留网关三文件、多套文档口径 | 01 §6 |

---

## 8. 待验证事项

1. 两个前端孤儿页面是否为刻意保留的回迁候选（需产品确认）。
2. `agent_gateway_main.py` 的 remote 模式在生产是否实际启用（当前默认 inproc，`main.py:551`）。
3. 5 对同名模块的行为差异是否已造成线上语义漂移（需逐函数比对，本次仅比对文件哈希）。
4. `main.py` 26 个后台协程在多副本下的重复执行影响（审计已有结论，本次未做运行期验证）。
