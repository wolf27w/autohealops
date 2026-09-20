═══════════════════════════════════════════════════════════════
项目：API-OPS自愈平台 - 三服务架构完整开发（最终合并版）
工具：OpenClaw
要求：自动完成所有代码开发，无需手动干预
说明：本文件为最终执行标准，已合并全部优化修正（V1.3），正文即最终设计，直接按本文件开发
═══════════════════════════════════════════════════════════════

你是全栈开发AI助手。请独立完成以下整个项目的开发，无需问我任何问题，所有技术决策由你自行做出。

OpenClaw会自动执行代码生成和文件写入。你只需要完成代码实现，不需要运行部署和测试。

【项目定位】
构建一个API-OPS自愈平台，管理多个Kubernetes集群，通过AI进行智能诊断和自动修复。

【三服务架构】
- api-server（Python/FastAPI）：系统大脑和门面，所有业务逻辑，唯一对外API入口
- k8s-executor（Go/gRPC/client-go）：系统的手和眼睛，K8s交互、故障检测、命令执行
- ai-service（Python/FastAPI）：系统的AI大脑，诊断、方案生成、多模型协同

【服务间通信】
- 前端 → api-server：HTTP（唯一对外入口）
- api-server → k8s-executor：gRPC
- api-server → ai-service：HTTP
- 三个服务 → 同一个MySQL数据库（192.168.6.99）
- api-server → Redis（分布式锁/缓存/限流/会话）

【核心功能清单】
1. 多K8s集群管理（kubeconfig加密存储，中心化管理）
2. 故障检测引擎（Watch Pod/Node事件，5分钟去重+告警聚合+入库限流）
3. AI智能诊断（多模型适配+降级链+多模型协同仲裁+Prometheus指标+ES日志上下文）
4. AI修复方案生成（操作类型识别+风险评估）
5. 决策引擎（操作类型+时间策略双维度，查询自动/增删改人工确认，高危命令强制人工）
6. 审批工作流（钉钉/企微机器人消息交互+验签回调，单人/双人审批，超时自动升级）
7. 自动修复执行（灰度策略+熔断控制+自动回滚+白名单+dry-run预演）
8. AI修复验证（修复前后快照对比，失败自动二次诊断+回滚+人工兜底）
9. 全链路审计（SHA256哈希链防篡改，trace_id贯穿日志）
10. 知识库（向量嵌入+RAG检索，修复结果自动沉淀闭环）
11. 批量处理（跨集群并行/滚动/分批执行）
12. 定制化定时任务（每集群独立Cron配置，Redis锁防重复执行）
13. 资源配额可见性（定时采集，非实时监控）
14. 权限管理（admin/operator/viewer三角色RBAC）
15. 通知服务（钉钉/企业微信，severity路由，失败重试，限频防轰炸）

【项目目录结构】
api-ops-platform/
├── api-server/                          # 核心API服务（Python/FastAPI）
│   ├── app/
│   │   ├── api/                         # API路由层
│   │   │   ├── __init__.py
│   │   │   ├── clusters.py              # 集群管理API
│   │   │   ├── alerts.py                # 告警管理API
│   │   │   ├── tasks.py                 # 任务管理API
│   │   │   ├── approvals.py             # 审批流程API
│   │   │   ├── notifications.py         # 通知配置API
│   │   │   ├── decisions.py             # 决策策略API
│   │   │   ├── batch.py                 # 批量处理API
│   │   │   ├── custom_tasks.py          # 定时任务API
│   │   │   ├── resources.py             # 资源配额API
│   │   │   ├── knowledge.py             # 知识库API
│   │   │   ├── audit.py                 # 审计追溯API
│   │   │   ├── stats.py                 # 统计报表API
│   │   │   ├── auth.py                  # 认证API
│   │   │   ├── users.py                 # 用户管理API
│   │   │   ├── webhooks.py              # 钉钉/企微回调API（验签）
│   │   │   └── logs.py                  # 日志查询API（ES代理，新增）
│   │   ├── services/                    # 业务逻辑层
│   │   │   ├── __init__.py
│   │   │   ├── approval_service.py      # 审批工作流（Redis锁+幂等）
│   │   │   ├── notification_service.py  # 通知服务（限频）
│   │   │   ├── decision_engine.py       # 决策引擎
│   │   │   ├── batch_service.py         # 批量处理（Redis锁）
│   │   │   ├── audit_service.py         # 审计服务
│   │   │   ├── scheduler_service.py     # 定时调度（Redis锁）
│   │   │   ├── resource_service.py      # 资源管理
│   │   │   └── knowledge_service.py     # 知识库管理
│   │   ├── clients/                     # 服务间通信客户端
│   │   │   ├── __init__.py
│   │   │   ├── executor_client.py       # gRPC客户端（→k8s-executor）
│   │   │   └── ai_client.py             # HTTP客户端（→ai-service）
│   │   ├── models/                      # 数据库模型（主定义）
│   │   │   ├── __init__.py
│   │   │   ├── cluster.py
│   │   │   ├── alert.py
│   │   │   ├── task.py
│   │   │   ├── approval.py
│   │   │   ├── notification.py
│   │   │   ├── decision.py
│   │   │   ├── batch.py
│   │   │   ├── custom_task.py
│   │   │   ├── resource.py
│   │   │   ├── knowledge.py
│   │   │   ├── audit.py
│   │   │   ├── user.py
│   │   │   ├── ai_model.py
│   │   │   ├── prompt.py
│   │   │   ├── diagnosis.py
│   │   │   ├── repair_plan.py
│   │   │   ├── execution.py
│   │   │   ├── verification.py
│   │   │   └── stats.py
│   │   ├── middleware/                  # 中间件
│   │   │   ├── __init__.py
│   │   │   ├── auth.py                  # JWT认证
│   │   │   └── permission.py            # RBAC权限
│   │   ├── core/                        # 基础设施
│   │   │   ├── __init__.py
│   │   │   ├── config.py                # 配置管理
│   │   │   ├── database.py              # 数据库连接
│   │   │   ├── redis_client.py          # Redis连接+分布式锁工具
│   │   │   ├── security.py              # 加密/JWT
│   │   │   └── logging.py               # 日志（JSON结构化+trace_id）
│   │   └── main.py                      # FastAPI入口
│   ├── requirements.txt
│   └── Dockerfile
│
├── k8s-executor/                        # K8s执行引擎（Go）
│   ├── cmd/
│   │   └── executor/
│   │       └── main.go                  # 入口
│   ├── internal/
│   │   ├── grpc/
│   │   │   └── server.go                # gRPC服务端
│   │   ├── watcher/
│   │   │   ├── pod_watcher.go           # Pod事件监听（聚合+限流）
│   │   │   ├── node_watcher.go          # Node事件监听（聚合+限流）
│   │   │   └── manager.go               # Watch管理器
│   │   ├── executor/
│   │   │   ├── execute.go               # 命令执行（白名单+dry-run）
│   │   │   ├── gray_release.go          # 灰度执行
│   │   │   ├── circuit_breaker.go       # 熔断控制
│   │   │   └── rollback.go              # 自动回滚
│   │   ├── health/
│   │   │   └── checker.go               # 健康检查
│   │   ├── quota/
│   │   │   └── collector.go             # 配额采集
│   │   ├── script/
│   │   │   └── runner.go                # 脚本执行
│   │   ├── k8sclient/
│   │   │   └── manager.go               # 多集群客户端池（显式context）
│   │   ├── database/
│   │   │   ├── db.go                    # GORM连接（不迁移）
│   │   │   └── models.go                # GORM模型
│   │   └── config/
│   │       └── config.go                # 配置
│   ├── proto/
│   │   └── executor.proto               # gRPC协议定义
│   ├── go.mod
│   ├── go.sum
│   └── Dockerfile
│
├── ai-service/                          # AI智能服务（Python/FastAPI）
│   ├── app/
│   │   ├── api/                         # API路由层
│   │   │   ├── __init__.py
│   │   │   ├── diagnosis.py             # 诊断接口
│   │   │   ├── repair_plan.py           # 方案生成接口
│   │   │   ├── verification.py          # 修复验证接口
│   │   │   ├── multi_model.py           # 多模型协同接口
│   │   │   ├── embedding.py             # 向量嵌入接口
│   │   │   └── rag.py                   # RAG检索接口
│   │   ├── services/                    # 业务逻辑层
│   │   │   ├── __init__.py
│   │   │   ├── model_adapter.py         # 统一模型适配器（severity分级）
│   │   │   ├── model_openai.py          # OpenAI适配器
│   │   │   ├── model_deepseek.py        # DeepSeek适配器
│   │   │   ├── model_claude.py          # Claude适配器
│   │   │   ├── prompt_engine.py         # 提示词引擎
│   │   │   ├── diagnosis_service.py     # 诊断服务（Prometheus上下文）
│   │   │   ├── plan_service.py          # 方案生成服务
│   │   │   ├── verification_service.py  # 验证服务
│   │   │   ├── multi_model_service.py   # 多模型协同
│   │   │   ├── embedding_service.py     # 向量嵌入（384维）
│   │   │   ├── rag_service.py           # RAG检索
│   │   │   ├── prometheus_client.py     # Prometheus指标查询
│   │   │   └── es_client.py             # Elasticsearch日志查询（新增）
│   │   ├── models/                      # AI相关数据库模型
│   │   │   ├── __init__.py
│   │   │   ├── ai_model.py
│   │   │   ├── prompt.py
│   │   │   ├── diagnosis.py
│   │   │   ├── repair_plan.py
│   │   │   ├── verification.py
│   │   │   └── stats.py
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   ├── config.py
│   │   │   └── database.py
│   │   └── main.py                      # FastAPI入口
│   ├── requirements.txt
│   └── Dockerfile
│
├── frontend/                            # 前端（React+TypeScript+Ant Design）
│   ├── src/
│   │   ├── pages/                       # 页面组件（19个）
│   │   │   ├── Login.tsx
│   │   │   ├── Dashboard.tsx
│   │   │   ├── ClusterList.tsx
│   │   │   ├── AlertList.tsx
│   │   │   ├── TaskList.tsx
│   │   │   ├── TaskDetail.tsx
│   │   │   ├── NotificationConfig.tsx
│   │   │   ├── AIModelConfig.tsx
│   │   │   ├── PromptTemplates.tsx
│   │   │   ├── DecisionPolicies.tsx
│   │   │   ├── ApprovalList.tsx
│   │   │   ├── ExecutionDetail.tsx      # 轮询进度（无WebSocket）
│   │   │   ├── BatchOperation.tsx
│   │   │   ├── CustomTasks.tsx
│   │   │   ├── ResourceQuota.tsx
│   │   │   ├── KnowledgeBase.tsx
│   │   │   ├── AuditTrace.tsx
│   │   │   ├── StatsDashboard.tsx
│   │   │   └── UserManagement.tsx
│   │   ├── components/                  # 通用组件
│   │   │   ├── MainLayout.tsx
│   │   │   ├── StatusTag.tsx
│   │   │   ├── SeverityTag.tsx
│   │   │   └── CodeBlock.tsx
│   │   ├── services/
│   │   │   └── api.ts                   # API调用封装（含执行进度轮询）
│   │   ├── App.tsx                      # 路由
│   │   └── main.tsx                     # 入口
│   ├── package.json
│   ├── tsconfig.json
│   └── Dockerfile
│
├── k8s/                                 # K8s部署清单
│   ├── namespace.yaml
│   ├── configmap-api-server.yaml
│   ├── configmap-k8s-executor.yaml
│   ├── configmap-ai-service.yaml        # 含PROMETHEUS_URL
│   ├── secret.yaml
│   ├── deployment-api-server.yaml
│   ├── deployment-k8s-executor.yaml     # 固定1副本
│   ├── deployment-ai-service.yaml
│   ├── deployment-frontend.yaml
│   ├── service-api-server.yaml
│   ├── service-k8s-executor.yaml
│   ├── service-ai-service.yaml
│   ├── service-frontend.yaml
│   ├── ingress.yaml
│   ├── hpa-api-server.yaml
│   ├── hpa-ai-service.yaml              # （hpa-k8s-executor.yaml 已禁用）
│   ├── pdb.yaml
│   ├── networkpolicy.yaml
│   └── backup-cronjob.yaml              # 备份含ENCRYPTION_KEY
│
├── docs/
│   ├── ARCHITECTURE.md
│   ├── API.md
│   ├── DEPLOYMENT.md
│   └── ENCRYPTION.md                    # 跨语言加密格式约定（新增）
│
├── docker-compose.yml
├── Makefile                             # 含 crypto-test 目标
└── README.md

现在开始按以下6个模块依次开发。每个模块完成后继续下一个模块，不需要停下确认。


═══════════════════════════════════════════════════════════════
模块1：api-server 数据库模型 + 核心基础设施
═══════════════════════════════════════════════════════════════

【1.1 api-server/requirements.txt】
包含：
fastapi==0.115.0
uvicorn[standard]==0.30.6
sqlalchemy[asyncio]==2.0.35
aiomysql==0.2.0
alembic==1.13.3
pydantic==2.9.2
pydantic-settings==2.5.2
redis==5.0.8
python-jose[cryptography]==3.3.0
cryptography==43.0.1
httpx==0.27.2
apscheduler==3.10.4
grpcio==1.66.2
grpcio-tools==1.66.2
bcrypt==4.2.0

【1.2 api-server/app/core/config.py】
使用pydantic-settings：
- DATABASE_URL: str（MySQL连接串，格式 mysql+aiomysql://user:pass@host:3306/dbname，如 mysql+aiomysql://root:root@192.168.6.99:3306/api_ops）
- REDIS_URL: str
- JWT_SECRET_KEY: str（必填，从环境变量注入，不写死）
- JWT_ALGORITHM: str = "HS256"
- JWT_EXPIRE_HOURS: int = 24
- ENCRYPTION_KEY: str（必填，32字节=256位，从环境变量注入）
- K8S_EXECUTOR_GRPC_ADDR: str（默认localhost:9090）
- AI_SERVICE_HTTP_ADDR: str（默认http://localhost:9000）
- DINGTALK_WEBHOOK: str = ""
- DINGTALK_SECRET: str = ""（钉钉加签密钥，回调验签用）
- WECHAT_WEBHOOK: str = ""
- WECHAT_TOKEN: str = ""（企微回调验签用）
- LOG_LEVEL: str = "INFO"
- API_HOST: str = ""（对外地址，通知/回调使用）
- Settings类使用@lru_cache缓存单例

【1.3 api-server/app/core/database.py】
- SQLAlchemy 2.0异步引擎（驱动：mysql+aiomysql）
- engine = create_async_engine(DATABASE_URL, echo=False, pool_size=20, max_overflow=40)
- async_session = async_sessionmaker(engine, expire_on_commit=False)
- Base = DeclarativeBase
- get_db()依赖：yield session，finally close
- 注意：本服务是数据库 schema owner，表结构变更通过 alembic migration 管理（见【1.9】）
- MySQL 建库要求：数据库字符集 utf8mb4、排序规则 utf8mb4_unicode_ci（建库语句：CREATE DATABASE api_ops CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;）

【1.4 api-server/app/core/security.py】
实现以下函数：
- encrypt_aes256_gcm(plaintext: str) -> str：使用ENCRYPTION_KEY加密，AES-256-GCM，随机12字节IV，返回base64(IV+ciphertext+tag)，IV在前
- decrypt_aes256_gcm(ciphertext_b64: str) -> str：解密（格式与Go端一致，见docs/ENCRYPTION.md）
- hash_password(password: str) -> str：bcrypt哈希
- verify_password(plain, hashed) -> bool
- create_jwt(user_id: int, username: str, role: str) -> str：生成JWT，exp=now+JWT_EXPIRE_HOURS
- decode_jwt(token: str) -> dict：验证并解析，失败抛异常
- generate_api_key() -> str：secrets.token_urlsafe(32)
- generate_trace_id() -> str：uuid4().hex
- generate_decision_id() -> str：uuid4().hex
- generate_lock_value() -> str：uuid4().hex（Redis分布式锁value）

【1.5 api-server/app/core/logging.py】
- 使用Python标准logging + JSON格式化
- JSON格式字段：{"ts": 时间戳, "level": levelname, "service": "api-server", "trace_id": 关联ID, "msg": 消息}
- trace_id 来源：请求中间件从 X-Trace-Id 请求头读取或生成，贯穿该请求的所有日志与下游调用（gRPC metadata / HTTP header 透传）
- format_extra(trace_id)辅助函数
- get_logger(name)函数

【1.6 api-server/app/core/redis_client.py】
- redis.asyncio连接
- get_redis()依赖
- 分布式锁工具函数（供调度/审批/批量使用）：
  - acquire_lock(redis, lock_key: str, timeout_ms: int = 30000) -> str|None：
    生成 uuid4 value，SET lock_key value NX PX timeout_ms，成功返回value，失败返回None
  - release_lock(redis, lock_key: str, value: str)：Lua脚本（GET比对value后DEL），防止误删其他副本的锁
  - renew_lock(redis, lock_key: str, value: str, timeout_ms: int)：PEXPIRE续期，长任务定期调用

【1.7 api-server/app/models/所有模型文件】

每个模型文件包含完整的SQLAlchemy定义。所有模型继承Base。使用Mapped和mapped_column。

具体字段：
Cluster: id(Integer PK), name(String 100 unique not null), description(String 500 default ""), kubeconfig_encrypted(Text not null), status(String 20 default "unknown"), version(String 50 default ""), maintenance_window(Text default "{}"), created_at(DateTime default now), updated_at(DateTime onupdate now)
（maintenance_window 存储维护窗口配置JSON，如 {"enabled": true, "start": "02:00", "end": "04:00", "days": "1,2,3,4,5"}，窗口内告警不通知不触发自愈）

Alert: id(Integer PK), cluster_id(Integer index not null), resource_type(String 10 not null), resource_name(String 200 not null), namespace(String 100 default ""), fault_type(String 50 not null), severity(String 10 not null), status(String 20 default "pending"), message(Text), event_count(Integer default 1), detected_at(DateTime default now index), resolved_at(DateTime nullable)
（event_count：聚合告警的事件计数，见告警聚合）

Task: id(Integer PK), alert_id(Integer index not null), assignee(String 50 default ""), status(String 20 default "pending" index), resolution(Text default ""), created_at(DateTime default now), updated_at(DateTime onupdate now)

TaskAuditLog: id(Integer PK), task_id(Integer index not null), action(String 50 not null), old_status(String 20 default ""), new_status(String 20 not null), operator(String 50 default ""), comment(String 500 default ""), created_at(DateTime default now)

Approval: id(Integer PK), plan_id(Integer index not null), required_level(Integer not null), status(String 20 default "pending" index), approvers(Text default "[]"), approved_by(Text default "[]"), rejected_by(String 50 default ""), comments(Text default ""), source_msg_id(String 64 default ""), created_at(DateTime default now), decided_at(DateTime nullable)
（source_msg_id：钉钉eventId/企微msgId，回调幂等去重用）

NotificationConfig: id(Integer PK), name(String 100 not null), channel_type(String 20 not null), webhook_url(String 500 not null), secret(String 200 default ""), is_active(Boolean default True), created_at(DateTime default now)

NotificationLog: id(Integer PK), config_id(Integer index), alert_id(Integer nullable), status(String 20), message(Text), error(Text default ""), sent_at(DateTime default now)

DecisionPolicy: id(Integer PK), name(String 50 not null), schedule_type(String 20 not null), days_of_week(String 20 default "1,2,3,4,5"), start_time(String 5 default "09:00"), end_time(String 5 default "18:00"), read_rule(String 30 default "auto"), create_rule(String 30 default "approval_1"), update_rule(String 30 default "approval_1"), delete_rule(String 30 default "approval_2"), approval_timeout(Integer default 1800), auto_escalation(Boolean default False), created_at(DateTime default now), updated_at(DateTime onupdate now)

DecisionResult: id(Integer PK), plan_id(Integer index not null), schedule_type(String 20), overall_operation_type(String 20), overall_decision(String 30), read_ops_count(Integer default 0), create_ops_count(Integer default 0), update_ops_count(Integer default 0), delete_ops_count(Integer default 0), created_at(DateTime default now)

BatchOperation: id(Integer PK), name(String 100), fault_type(String 50 default ""), operation_type(String 20 default ""), plan_id(Integer nullable), strategy(String 20 default "parallel"), batch_size(Integer default 5), failure_policy(String 20 default "continue"), total_clusters(Integer default 0), success_count(Integer default 0), failed_count(Integer default 0), status(String 20 default "pending"), created_at(DateTime default now)

BatchOperationItem: id(Integer PK), batch_operation_id(Integer index not null), cluster_id(Integer not null), alert_id(Integer nullable), execution_id(Integer nullable), status(String 20 default "pending"), error_message(Text default ""), started_at(DateTime nullable), completed_at(DateTime nullable)

CustomTask: id(Integer PK), cluster_id(Integer index not null), name(String 100 not null), task_type(String 20 not null), target_resource(String 200 default ""), target_namespace(String 100 default ""), cron_expression(String 50 not null), script_content(Text default ""), parameters(Text default "{}"), is_enabled(Boolean default True), last_run_at(DateTime nullable), last_run_status(String 20 default ""), created_at(DateTime default now), updated_at(DateTime onupdate now)

CustomTaskLog: id(Integer PK), task_id(Integer index not null), cluster_id(Integer not null), start_time(DateTime default now), end_time(DateTime nullable), status(String 20 default "running"), output(Text default ""), error_message(Text default "")

ResourceSnapshot: id(Integer PK), cluster_id(Integer index not null), namespace(String 100 index), resource_type(String 20 not null), resource_name(String 200 not null), status(String 50 default ""), replicas(Integer default 0), ready_replicas(Integer default 0), container_count(Integer default 0), image_version(String 200 default ""), cpu_usage(String 20 default ""), memory_usage(String 20 default ""), created_at(DateTime default now), snapshot_time(DateTime index)

QuotaSnapshot: id(Integer PK), cluster_id(Integer index not null), namespace(String 100 index), cpu_limit(String 20 default "0"), cpu_used(String 20 default "0"), memory_limit(String 20 default "0"), memory_used(String 20 default "0"), pod_limit(Integer default 0), pod_used(Integer default 0), pvc_limit(Integer default 0), pvc_used(Integer default 0), service_limit(Integer default 0), service_used(Integer default 0), snapshot_time(DateTime index)

KnowledgeEntry: id(Integer PK), fault_type(String 50 index not null), root_cause(Text), repair_steps(Text), cluster_info(String 200 default ""), tags(String 500 default ""), success_count(Integer default 0), usage_count(Integer default 0), vector_embedding(LargeBinary nullable), pending_review(Boolean default False), created_at(DateTime default now), updated_at(DateTime onupdate now)
（pending_review：修复方案与知识库建议冲突时标记人工review）

AuditRecord: id(Integer PK), decision_id(String 64 index not null), trace_id(String 64 index not null), trigger_snapshot(Text default ""), ai_call_detail(Text default ""), decision_detail(Text default ""), execution_detail(Text default ""), verification_detail(Text default ""), hash_value(String 64 not null), previous_hash(String 64 default ""), created_at(DateTime default now)

User: id(Integer PK), username(String 50 unique not null), password_hash(String 255 not null), email(String 100 default ""), role(String 20 default "viewer"), is_active(Boolean default True), api_key(String 64 default ""), dingtalk_user_id(String 100 default ""), wechat_userid(String 100 default ""), created_at(DateTime default now), updated_at(DateTime onupdate now)
（dingtalk_user_id / wechat_userid：审批回调外部身份→平台用户映射）

Role: id(Integer PK), name(String 50 unique not null), description(String 200 default ""), permissions(Text default "[]")

AIModelConfig: id(Integer PK), provider(String 20 not null), model_name(String 100 not null), api_key_encrypted(Text default ""), api_base(String 300 default ""), max_tokens(Integer default 4096), temperature(Float default 0.7), priority(Integer default 100), is_active(Boolean default True), cost_per_1k_tokens(Float default 0.0), min_severity(String 10 default "low"), created_at(DateTime default now), updated_at(DateTime onupdate now)
（min_severity：该模型可服务的最低告警级别，实现severity分级选模型：critical模型只服务critical/high，flash类模型服务medium/low）

AICallLog: id(Integer PK), model_config_id(Integer index), request_type(String 30 default ""), system_prompt(Text default ""), user_prompt(Text default ""), response_content(Text default ""), token_usage(Integer default 0), latency_ms(Integer default 0), cost(Float default 0.0), status(String 20 default "success"), error(Text default ""), created_at(DateTime default now index)

PromptTemplate: id(Integer PK), name(String 100 unique not null), category(String 30 not null), system_prompt(Text not null), user_prompt_template(Text not null), variables(String 500 default "[]"), version(Integer default 1), is_active(Boolean default True), created_at(DateTime default now), updated_at(DateTime onupdate now)

PromptVersion: id(Integer PK), template_id(Integer index not null), version(Integer not null), system_prompt(Text), user_prompt_template(Text), changelog(String 500 default ""), created_at(DateTime default now)

AIDiagnosisResult: id(Integer PK), alert_id(Integer index not null), model_config_id(Integer nullable), template_id(Integer nullable), context_snapshot(Text default ""), diagnosis_json(Text default ""), confidence(Float default 0.0), is_useful(Boolean nullable), created_at(DateTime default now)

RepairPlan: id(Integer PK), alert_id(Integer index not null), diagnosis_id(Integer nullable), plan_json(Text not null), operation_types(String 100 default ""), risk_level(String 20 default "medium"), status(String 20 default "pending" index), created_at(DateTime default now)

ExecutionLog: id(Integer PK), plan_id(Integer index not null), step_index(Integer not null), command(Text not null), status(String 20 default "running"), output(Text default ""), started_at(DateTime default now), completed_at(DateTime nullable)
（执行进度实时写入本表，前端轮询从此表读取）

RepairVerification: id(Integer PK), plan_id(Integer index not null), before_snapshot(Text default ""), after_snapshot(Text default ""), metrics_comparison(Text default ""), ai_verdict(String 30 default ""), status(String 20 default "pending"), created_at(DateTime default now)

AIEffectivenessStat: id(Integer PK), date(Date index not null), model_config_id(Integer nullable), fault_type(String 50 default ""), total_diagnoses(Integer default 0), correct_diagnoses(Integer default 0), total_repairs(Integer default 0), successful_repairs(Integer default 0), avg_mttr_seconds(Integer default 0), total_cost(Float default 0.0), created_at(DateTime default now)

ModelAccuracyStat: id(Integer PK), model_config_id(Integer index not null), fault_type(String 50 default ""), total_attempts(Integer default 0), success_count(Integer default 0), accuracy_rate(Float default 0.0), updated_at(DateTime onupdate now)

【1.8 api-server/app/models/__init__.py】
导入所有模型，确保Base.metadata包含所有表。

【1.9 api-server/app/main.py】
- FastAPI(title="API-OPS平台", version="1.0.0")
- 加载CORS中间件（允许所有来源）
- 异常处理器（统一返回{code, message, data}格式）
- 请求日志中间件（生成/透传trace_id，记录请求耗时，日志带trace_id）
- 健康检查：GET /health
- 启动事件：
  - 执行alembic migration升级（alembic upgrade head），不使用Base.metadata.create_all建表（本服务是schema owner，migration统一管理）
  - seed_default_policies()插入默认决策策略
  - seed_default_users()创建admin/operator/viewer三个用户（初始密码写入README，首次登录强制改密）
  - 调用ai_client确保预置提示词模板已创建
  - scheduler_service.load_all_tasks()+start()（定时任务执行内部使用Redis锁防多副本重复）
  - 启动审批超时检查定时任务（每分钟，执行时先获取Redis锁lock:approval_timeout，抢到锁才执行）
- 包含所有API路由


═══════════════════════════════════════════════════════════════
模块2：k8s-executor 完整实现
═══════════════════════════════════════════════════════════════

【2.1 k8s-executor/go.mod】
module github.com/api-ops-platform/k8s-executor
go 1.22
require:
- google.golang.org/grpc
- google.golang.org/protobuf
- gorm.io/gorm
- gorm.io/driver/mysql
- k8s.io/client-go
- k8s.io/apimachinery
- k8s.io/api
- github.com/robfig/cron/v3
- go.uber.org/zap
- github.com/spf13/viper
- github.com/google/uuid

【2.2 k8s-executor/proto/executor.proto】
完整proto定义：
syntax = "proto3";
package executor;
option go_package = "github.com/api-ops-platform/k8s-executor/proto;executor";

service ExecutorService {
  rpc StartWatch(StartWatchRequest) returns (StartWatchResponse);
  rpc StopWatch(StopWatchRequest) returns (StopWatchResponse);
  rpc ExecuteCommand(ExecuteCommandRequest) returns (ExecuteCommandResponse);
  rpc ExecutePlan(ExecutePlanRequest) returns (stream ExecutePlanResponse);
  rpc RollbackPlan(RollbackPlanRequest) returns (RollbackPlanResponse);
  rpc HealthCheck(HealthCheckRequest) returns (HealthCheckResponse);
  rpc CollectQuota(CollectQuotaRequest) returns (CollectQuotaResponse);
  rpc RunScript(RunScriptRequest) returns (RunScriptResponse);
  rpc BatchExecute(BatchExecuteRequest) returns (stream BatchExecuteResponse);
}

所有message定义完整，包含：cluster_id、namespace、command、plan_id、step_index、step_command、rollback_command、script_content、parameters、success、message、output、trace_id等字段。
（trace_id通过gRPC metadata传递，日志贯穿）

【2.3 k8s-executor/internal/config/config.go】
- Viper读取配置
- 支持环境变量覆盖
- DATABASE_URL、GRPC_PORT(默认9090)、LOG_LEVEL、COMMAND_WHITELIST（命令白名单，逗号分隔，默认"get,list,describe,logs,top,explain,rollout restart,scale,set image,apply,patch,delete,exec"）

【2.4 k8s-executor/internal/database/models.go】
GORM结构体（与api-server的SQLAlchemy模型对应，字段一致）：
- Cluster: ID, Name, Description, KubeconfigEncrypted, Status, Version, MaintenanceWindow, CreatedAt, UpdatedAt
- Alert: ID, ClusterID, ResourceType, ResourceName, Namespace, FaultType, Severity, Status, Message, EventCount, DetectedAt, ResolvedAt
- ExecutionLog: ID, PlanID, StepIndex, Command, Status, Output, StartedAt, CompletedAt
- ResourceSnapshot: ID, ClusterID, Namespace, ResourceType, ResourceName, Status, Replicas, ReadyReplicas, ContainerCount, ImageVersion, CPUUsage, MemoryUsage, CreatedAt, SnapshotTime
- QuotaSnapshot: ID, ClusterID, Namespace, CPULimit, CPUUsed, MemoryLimit, MemoryUsed, PodLimit, PodUsed, PVCLimit, PVCUsed, ServiceLimit, ServiceUsed, SnapshotTime
- CustomTask: ID, ClusterID, Name, TaskType, TargetResource, TargetNamespace, CronExpression, ScriptContent, Parameters, IsEnabled, LastRunAt, LastRunStatus, CreatedAt, UpdatedAt
- CustomTaskLog: ID, TaskID, ClusterID, StartTime, EndTime, Status, Output, ErrorMessage

表名与api-server保持一致（复数形式：clusters、alerts、execution_logs、resource_snapshots、quota_snapshots、custom_tasks、custom_task_logs）。

【2.5 k8s-executor/internal/database/db.go】
- GORM初始化连接MySQL（driver: mysql，DSN格式：user:pass@tcp(host:3306)/dbname?charset=utf8mb4&parseTime=True&loc=Local）
- **不执行AutoMigrate**（表结构由api-server的alembic统一管理），启动时只做连接校验（ping）
- GetDB()

【2.6 k8s-executor/internal/k8sclient/manager.go】
- ClusterClient结构体：ID uint, Clientset *kubernetes.Clientset
- NewClusterClient(clusterID, kubeconfigJSON)：解析kubeconfig创建clientset
  注意：解析后显式指定context（kubeconfig中 contexts 里匹配 cluster name 或第一个可用的context），禁止依赖current-context，防止连错集群
- Manager结构体：mu sync.RWMutex, clients map[uint]*ClusterClient
- GetClient(clusterID)：从缓存获取，不存在则从数据库加载kubeconfig后创建
- RemoveClient(clusterID)
- ListClients()
- DecryptKubeconfig(encrypted)：使用AES-256-GCM解密（Go实现，与Python端格式严格一致：base64(IV12B+ciphertext+tag16B)，IV在前；格式详见docs/ENCRYPTION.md，实现后必须通过make crypto-test互测）
- 连接错误分类：区分"证书过期/网络不通/权限不足"（解析错误信息中的 x509/connection refused/forbidden 特征），返回结构化错误供上层提示

【2.7 k8s-executor/internal/watcher/pod_watcher.go】
- PodWatcher：clusterID, clientset, ctx, cancel
- Start()：使用client-go的Watch接口监听所有namespace的Pod事件
- handlePodEvent(event)：
  解析Pod状态
  检测CrashLoopBackOff（containerStatuses.waiting.reason）→ high
  检测ImagePullBackOff/ErrImagePull → medium
  检测OOMKilled（lastState.terminated.reason）→ high
  检测Pending超过5分钟（phase=Pending且创建时间>5分钟）→ medium
- deduplicateCheck(alert)：
  查询数据库5分钟内是否有相同告警（cluster+resource+fault_type）
  命中则聚合：更新原告警event_count+1、刷新detected_at、更新message，不新建记录
- rateLimitCheck(clusterID)：每分钟每集群最多写入N条告警（默认20），超出部分合并为风暴告警（fault_type=ALERT_STORM，message含事件数）
- maintenanceWindowCheck(clusterID, now)：集群处于维护窗口内则不创建告警（直接丢弃事件）
- createAlert(alert)：写入alerts表
- message格式："Pod xxx in namespace xxx is in CrashLoopBackOff state. Container xxx: reason=CrashLoopBackOff"

【2.8 k8s-executor/internal/watcher/node_watcher.go】
- NodeWatcher：clusterID, clientset, ctx, cancel
- Start()：Watch Node事件
- 检测NotReady（Ready condition=False/Unknown）→ critical
- 检测MemoryPressure/DiskPressure/PIDPressure（condition=True）→ high
- 同样应用 deduplicateCheck（聚合）、rateLimitCheck（限流）、maintenanceWindowCheck（静默期）

【2.9 k8s-executor/internal/watcher/manager.go】
- WatchManager：mu, watchers map[uint]*ClusterWatchers
- ClusterWatchers：podWatcher, nodeWatcher
- StartClusterWatch(clusterID)：创建client、启动两个watcher goroutine
- StopClusterWatch(clusterID)：cancel context
- StartAllWatches()：查询所有集群，逐个启动
- RestartClusterWatch(clusterID)：停止后重新启动
- 注：本服务固定单副本部署（见模块6），所有集群的watch由唯一副本持有

【2.10 k8s-executor/internal/executor/execute.go】
- ExecuteCommand(clusterID, namespace, command)：使用client-go执行命令（PodExec方式：kubectl exec底层，适用于容器内命令；kubectl类服务端操作如rollout restart/scale翻译为client-go对应API调用），返回输出
- WhitelistCheck(command)：解析命令首词，不在COMMAND_WHITELIST中的命令直接拒绝（返回错误"command not allowed"）
- SafetyCheck(command)：检查包含rm -rf、drop、format、purge、:(){:|:&};:、mkfs等危险模式返回false
- DryRunCheck(command)：写操作（apply/patch/scale/delete/rollout）执行前先用client-go DryRun选项预演（等价kubectl --dry-run=client），预演成功才真执行；预演失败返回错误
- IdempotencyCheck(clusterID, command)：解析命令，检查资源是否已处于目标状态（如replicas已等于目标值则跳过）
- BlastRadiusEvaluate(clusterID, planJSON)：统计影响的Pod数量
- 执行优先级：WhitelistCheck → SafetyCheck → IdempotencyCheck → DryRunCheck → 执行

【2.11 k8s-executor/internal/executor/gray_release.go】
- GrayExecutor：clusterID, clientset
- ExecuteWithGrayStrategy(plan, notifyFn)：
  解析planJSON获取steps
  评估blastRadius
  ≤1个Pod：直接执行所有步骤
  2-10个Pod：执行step[0]→notifyFn(等待30秒)→验证→执行剩余步骤
  >10个Pod：分10批→每批执行后notifyFn(等待60秒)
  每步执行后记录ExecutionLog（实时写入，供前端轮询）
  notifyFn用于gRPC流式推送

【2.12 k8s-executor/internal/executor/circuit_breaker.go】
- CircuitBreaker：failedSteps int, consecutiveFailures int, samples []latencySample
- RecordStep(success bool, latencyMs)
- CheckTriggered() bool：
  连续2步失败
  或错误率>5%（最近20个样本）
  或平均延迟>预期200%

【2.13 k8s-executor/internal/executor/rollback.go】
- RollbackPlan(clusterID, planJSON)：
  解析planJSON获取steps（逆序遍历）
  执行每步的rollback_command
  更新ExecutionLog状态为rolled_back

【2.14 k8s-executor/internal/health/checker.go】
- HealthCheck(clusterID)：调用ServerVersion()，更新clusters表status和version
- CheckAllClusters()：遍历所有集群
- StartHealthCheckScheduler()：robfig/cron每60秒执行CheckAllClusters

【2.15 k8s-executor/internal/quota/collector.go】
- CollectClusterQuota(clusterID)：
  获取Node列表：总数、就绪数、非就绪数
  汇总CPU/内存容量
- CollectNamespaceQuota(clusterID, namespace)：
  查询ResourceQuota
  提取CPU/Memory/Pod/PVC/Service的limit和used
- CollectResourceCounts(clusterID, namespace)：
  统计Deployment数量、Pod总数、Running数、异常数、Service数量
- CollectAllClusters()
- StartQuotaCollectScheduler()：每60秒执行
- （后续增强：可复用Prometheus kube-state-metrics数据替代部分采集，本实现保留client-go采集作为基础能力）

【2.16 k8s-executor/internal/script/runner.go】
- RunScript(clusterID, scriptContent, parameters)：
  方式1：直接执行kubectl命令（通过clientset REST API）
  方式2：创建临时Job执行
  默认300秒超时
  结果写入custom_task_logs

【2.17 k8s-executor/internal/grpc/server.go】
- Server：实现所有RPC方法
- StartWatch：调用WatchManager.StartClusterWatch
- StopWatch：调用WatchManager.StopClusterWatch
- ExecuteCommand：单条命令（白名单+dry-run+安全检查）
- ExecutePlan：流式返回，每步执行完推送进度（ExecutionLog同步落库）
- RollbackPlan
- HealthCheck
- CollectQuota
- RunScript
- BatchExecute：流式返回每集群结果
- 所有方法从gRPC metadata读取trace_id写入日志

【2.18 k8s-executor/cmd/executor/main.go】
初始化顺序：config → zap logger（JSON格式，含trace_id字段） → database → k8sclient.Manager → WatchManager.StartAllWatches → 健康检查调度器 → 配额采集调度器 → gRPC server启动

【2.19 k8s-executor/Dockerfile】
多阶段构建：golang:1.22-alpine → alpine:latest
包含grpc_health_probe


═══════════════════════════════════════════════════════════════
模块3：ai-service 完整实现
═══════════════════════════════════════════════════════════════

【3.1 ai-service/requirements.txt】
fastapi==0.115.0
uvicorn[standard]==0.30.6
sqlalchemy[asyncio]==2.0.35
aiomysql==0.2.0
pydantic==2.9.2
pydantic-settings==2.5.2
httpx==0.27.2
openai==1.51.0
anthropic==0.39.0
sentence-transformers==3.1.1
numpy==2.1.1
redis==5.0.8

【3.2 ai-service/app/core/config.py】
- DATABASE_URL（与api-server共享）
- OPENAI_API_KEY、DEEPSEEK_API_KEY、CLAUDE_API_KEY（环境变量注入）
- EMBEDDING_MODEL（默认sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2）
- PROMETHEUS_URL: str = ""（Prometheus地址，配置后启用指标上下文）
- ES_URL: str = ""（Elasticsearch地址，如http://es:9200，配置后启用日志查询）
- ES_INDEX_PATTERN: str = "k8s-*"（日志索引模式，按环境调整）
- ES_USERNAME: str = "" / ES_PASSWORD: str = ""（ES认证，可选）
- DAILY_COST_LIMIT: float = 0.0（每日AI总成本上限，0=不限制）
- SINGLE_ALERT_COST_LIMIT: float = 0.0（单告警诊断成本上限，0=不限制）
- LOG_LEVEL

【3.3 ai-service/app/core/database.py】
- 与api-server相同的SQLAlchemy异步引擎配置
- Base = DeclarativeBase（独立Base，但表结构相同）
- **启动时不执行create_all**（表结构由api-server的alembic统一管理），只校验连接

【3.4 ai-service/app/models/】
AI相关模型定义（与api-server的对应模型字段一致）：
- AIModelConfig（含min_severity字段）
- AICallLog
- PromptTemplate
- PromptVersion
- AIDiagnosisResult
- RepairPlan
- RepairVerification
- AIEffectivenessStat
- ModelAccuracyStat
- KnowledgeEntry（含pending_review字段）

【3.5 ai-service/app/main.py】
- FastAPI(title="AI-Service")
- 健康检查
- 启动时不建表（连接校验即可）
- 启动时调用SeedDefaultTemplates()

【3.6 ai-service/app/services/model_adapter.py】
- AIResponse类（dataclass）：content, token_usage, cost, model_name, provider
- BaseAIModelAdapter抽象类：
  async chat_completion(self, system_prompt: str, user_prompt: str) -> AIResponse
- ModelFactory：
  get_adapter(config: AIModelConfig) -> BaseAIModelAdapter
  根据provider返回OpenAIAdapter/DeepSeekAdapter/ClaudeAdapter
- call_with_fallback(system_prompt, user_prompt, request_type, severity="high") -> tuple[AIResponse, str]：
  查询所有is_active=True的模型配置
  按severity过滤：config.min_severity 与当前告警severity匹配（critical/high → 可用min_severity<=severity且priority高的模型；medium/low → 优先低cost模型）
  按priority升序依次调用，失败切换下一个
  成本上限检查：今日累计成本（AICallLog聚合）>=DAILY_COST_LIMIT 或单告警累计>=SINGLE_ALERT_COST_LIMIT 时，降级到最便宜模型；超过上限直接返回降级错误（调用方转人工）
  记录开始时间，计算latency_ms
  每次调用写入AICallLog（含token_usage、cost计算）
  全部失败时抛出RuntimeError

【3.7 ai-service/app/services/model_openai.py】
OpenAIAdapter：
- 使用openai.AsyncOpenAI(api_key, base_url)
- chat_completion：await client.chat.completions.create(model, messages=[{system},{user}], max_tokens, temperature)
- 提取content、usage.total_tokens
- 计算cost = total_tokens/1000 * cost_per_1k_tokens

【3.8 ai-service/app/services/model_deepseek.py】
DeepSeekAdapter：
- 使用httpx.AsyncClient
- POST https://api.deepseek.com/v1/chat/completions
- Headers: Authorization: Bearer {api_key}
- Body: {model, messages, max_tokens, temperature}
- 响应格式与OpenAI兼容

【3.9 ai-service/app/services/model_claude.py】
ClaudeAdapter：
- 使用anthropic.AsyncAnthropic(api_key)
- chat_completion：await client.messages.create(model, system=system_prompt, messages=[{role:"user", content:user_prompt}], max_tokens, temperature)
- 提取content[0].text、usage.input_tokens+output_tokens

【3.10 ai-service/app/services/prompt_engine.py】
- RenderTemplate(template, context)：使用正则\{(\w+)\}替换变量
- ExtractVariables(template)：返回所有变量名列表
- CreateTemplate(name, category, system_prompt, user_prompt_template)
- UpdateTemplate(id, system_prompt, user_prompt_template)：version+1，旧版本存PromptVersion
- TestTemplate(id, variables)：渲染后调用call_with_fallback返回结果
- SeedDefaultTemplates()：检查数据库，不存在则插入以下5个模板：

模板1：pod_crash_diagnosis（category=diagnosis）
system_prompt: "你是一个Kubernetes故障诊断专家。请分析以下Pod崩溃问题并返回JSON格式的诊断结果。JSON必须包含字段：root_cause（字符串）、suggestions（字符串数组）、risk_level（low/medium/high）、confidence（0到1之间的小数）。"
user_prompt_template: "请诊断以下Pod崩溃问题：\n集群名称：{cluster_name}\n命名空间：{namespace}\nPod名称：{pod_name}\n重启次数：{restart_count}\n状态信息：{status_info}\n最近日志：\n{recent_logs}\n指标趋势：\n{metrics_trend}"

模板2：node_pressure_diagnosis（category=diagnosis）
system_prompt: "你是一个Kubernetes节点故障诊断专家。请分析以下节点异常并返回JSON格式的诊断结果。JSON必须包含字段：root_cause、suggestions、risk_level、confidence。"
user_prompt_template: "请诊断以下节点异常：\n集群名称：{cluster_name}\n节点名称：{node_name}\n异常类型：{fault_type}\n节点Conditions：{conditions}\n最近事件：{recent_events}\n指标趋势：\n{metrics_trend}"

模板3：repair_plan_generation（category=repair_plan）
system_prompt: "你是一个Kubernetes自动化运维专家。请基于诊断结果生成安全的修复方案。返回JSON格式，包含steps数组。每个step必须包含：command（kubectl命令）、description（步骤描述）、expected_result（预期结果）、rollback_command（回滚命令）。"
user_prompt_template: "诊断结果：\n{diagnosis_result}\n\n集群名称：{cluster_name}\n资源信息：{resource_info}\n\n请生成修复方案。"

模板4：risk_assessment（category=risk_assessment）
system_prompt: "你是风险评估专家。请评估修复方案的风险。返回JSON格式，包含risk_level（low/medium/high/critical）、blast_radius（影响范围描述）、data_loss_risk（布尔值）、mitigations（缓解措施数组）。"
user_prompt_template: "修复方案：\n{repair_plan}\n\n目标资源：{target_resource}"

模板5：repair_verification（category=verification）
system_prompt: "你是Kubernetes修复验证专家。请对比修复前后的状态判断修复是否成功。返回JSON格式，包含verdict（healed/degraded/unchanged/worsened四选一）、reason（判断理由）、follow_up（后续建议数组）。"
user_prompt_template: "修复前快照：\n{before_snapshot}\n\n修复后快照：\n{after_snapshot}\n\n指标对比：\n{metrics_comparison}"

【3.11 ai-service/app/services/prometheus_client.py】（新增）
PrometheusClient类：
- __init__：base_url = PROMETHEUS_URL，httpx.AsyncClient
- query(promql: str) -> dict：GET /api/v1/query
- query_range(promql: str, start, end, step) -> dict：GET /api/v1/query_range
- get_pod_metrics(namespace, pod) -> str：
  rate(container_cpu_usage_seconds_total{namespace=~"$ns", pod=~"$pod"}[5m])
  container_memory_working_set_bytes{namespace=~"$ns", pod=~"$pod"}
  kube_pod_container_status_restarts_total{namespace=~"$ns", pod=~"$pod"}
  格式化为文本（最近1小时趋势，step=5m）
- get_node_metrics(node) -> str：
  node_cpu_seconds_total（使用率）
  node_memory_MemAvailable_bytes
  node_filesystem_avail_bytes
  格式化为文本
- get_error_rate(namespace, pod) -> str（如有业务指标）：
  sum(rate(http_requests_total{status=~"5.."}[5m]))
- 统一超时5秒；任何查询失败返回空字符串并记日志（降级不影响主流程）
- PROMETHEUS_URL 为空时所有方法直接返回空字符串

【3.11b ai-service/app/services/es_client.py】（新增，日志对接现有 Elasticsearch）
ESClient类：
- __init__：base_url = ES_URL，httpx.AsyncClient，可选basic auth
- search_logs(namespace, pod, tail=50, time_range_min=60) -> str：
  POST {ES_URL}/{ES_INDEX_PATTERN}/_search
  body: {"query": {"bool": {"filter": [{"term": {"kubernetes.namespace_name": namespace}}, {"term": {"kubernetes.pod_name": pod}}]}}, "sort": [{"@timestamp": "desc"}], "size": tail}
  将命中的日志行（@timestamp + message）格式化为文本（时间正序）
  适配常见日志索引字段（kubernetes.namespace_name/kubernetes.pod_name/@timestamp/message；若索引字段不同，在配置中可调字段映射）
- 统一超时5秒；任何失败返回空字符串并记日志（降级不影响主流程）
- ES_URL 为空时直接返回空字符串

【3.12 ai-service/app/services/diagnosis_service.py】
- DiagnoseAlert(alert_data: dict, context_data: dict)：
  根据alert_data.fault_type匹配模板（CrashLoopBackOff/ImagePullBackOff/OOMKilled/PendingTimeout→pod_crash_diagnosis；NodeNotReady/MemoryPressure/DiskPressure/PIDPressure→node_pressure_diagnosis）
  调用prometheus_client获取指标趋势，注入metrics_trend变量（PROMETHEUS_URL未配置或查询失败时为空）
  调用es_client.search_logs获取最近日志，注入recent_logs变量（ES_URL未配置或查询失败时为空；ES可用时优先用ES日志，替代executor的kubectl logs）
  渲染提示词
  调用call_with_fallback(request_type="diagnosis", severity=alert_data.severity)
  解析JSON（root_cause、suggestions、risk_level、confidence）
  存入ai_diagnosis_results表
  返回{diagnosis_id, root_cause, suggestions, risk_level, confidence, model_used}

- MultiDiagnose(alert_data, context_data)：
  获取所有活跃模型（最多3个，按severity过滤）
  asyncio.gather并发调用
  收集每个模型的分析结果
  提取共识root_cause（出现次数最多的根因）
  冲突仲裁：无共识时查询model_accuracy_stats选择准确率最高模型的结论
  更新model_accuracy_stats（total_attempts+1，成功则success_count+1，重新计算accuracy_rate）
  返回{consensus_root_cause, model_analyses, recommended_action, confidence_score}

【3.13 ai-service/app/services/plan_service.py】
- GeneratePlan(diagnosis_data, alert_data)：
  调用repair_plan_generation模板
  渲染（diagnosis_result=诊断JSON，cluster_name，resource_info）
  call_with_fallback(request_type="repair_plan", severity=alert_data.severity)
  解析AI返回的steps数组
  AnalyzeOperationType(command)：
    READ：包含get/describe/logs/top/explain/list/watch
    CREATE：包含create/run/install/add，或apply且资源不存在，或scale增加副本
    UPDATE：包含edit/patch/set image/rollout/upgrade/update，或apply且资源已存在，或scale减少副本
    DELETE：包含delete/remove/uninstall/purge/cleanup
  AssessRiskLevel(operation_types, target_resources)：
    基础：仅READ=low，含CREATE/UPDATE=medium，含DELETE=high
    +1：目标包含PVC/PV/NetworkPolicy/RBAC/CRD/Namespace
    +1：steps数量>10
    +1：无rollback_command的步骤>50%
    数值映射：low=1, medium=2, high=3, critical=4；基础值+附加分后 clamp 到 [1,4]，high+2及以上=critical
  存入repair_plans表（operation_types=逗号分隔的操作类型列表）
  返回{plan_id, steps, operation_types, risk_level}

【3.14 ai-service/app/services/verification_service.py】
- VerifyRepair(plan_id, before_snapshot, after_snapshot, metrics_comparison)：
  调用repair_verification模板
  call_with_fallback(request_type="verification")
  解析verdict/reason/follow_up
  存入repair_verifications表
  返回{verdict, reason, follow_up}

【3.15 ai-service/app/services/embedding_service.py】
- EmbeddingService类：
  _model = None（懒加载）
  get_model()：首次调用时加载SentenceTransformer(EMBEDDING_MODEL)；模型文件离线预下载（Dockerfile构建时或挂载volume），支持HF_HUB_OFFLINE=1
  embed(text)：返回384维float列表（模型paraphrase-multilingual-MiniLM-L12-v2实际输出384维；维度从len(embedding)动态获取，不硬编码）
  batch_embed(texts)：批量

【3.16 ai-service/app/services/rag_service.py】
- SearchSimilar(query, top_k=5)：
  query_embedding = embedding_service.embed(query)
  从knowledge_entries表读取所有条目（排除vector_embedding为NULL和pending_review=True的）
  计算余弦相似度（按实际向量维度，384）
  返回top_k个{id, fault_type, root_cause, repair_steps, similarity}
  （演进：条目超10万时迁移pgvector，SQL直接ORDER BY embedding <-> $1 LIMIT k）
- AddKnowledge(fault_type, root_cause, repair_steps, tags)：
  text = root_cause + " " + repair_steps
  embedding = embedding_service.embed(text)
  bytes_embedding = np.array(embedding, dtype=np.float32).tobytes()（384*4=1536字节）
  存入knowledge_entries表
- InjectSimilarCases(alert_data, top_k=3)：
  调用SearchSimilar
  将结果格式化为文本段落
  返回可注入诊断提示词的上下文文本

【3.17 ai-service/app/api/下所有接口】

diagnosis.py:
- POST /api/v1/ai/diagnose
  请求体：{alert_id, alert_data: {cluster_name, namespace, pod_name/node_name, fault_type, severity, restart_count, status_info, recent_logs, conditions, recent_events}, context_data: {}}
  响应：{code: 200, message: "success", data: {diagnosis_id, root_cause, suggestions, risk_level, confidence, model_used}}
- POST /api/v1/ai/multi-diagnose
  请求体：{alert_id, alert_data, context_data}
  响应：{code: 200, message: "success", data: {consensus_root_cause, model_analyses: [], recommended_action, confidence_score}}

repair_plan.py:
- POST /api/v1/ai/generate-plan
  请求体：{diagnosis_id, alert_data, diagnosis_data}
  响应：{code: 200, message: "success", data: {plan_id, steps: [{command, description, expected_result, rollback_command}], operation_types, risk_level}}

verification.py:
- POST /api/v1/ai/verify-repair
  请求体：{plan_id, before_snapshot, after_snapshot, metrics_comparison}
  响应：{code: 200, message: "success", data: {verdict, reason, follow_up}}

multi_model.py:
- GET /api/v1/ai/models：模型配置列表
- POST /api/v1/ai/models：创建模型配置（含min_severity）
- POST /api/v1/ai/models/{id}/test：测试连通性
- GET /api/v1/ai/logs：AI调用日志
- GET /api/v1/ai/model-stats：模型准确率统计

embedding.py:
- POST /api/v1/ai/embed：{text}→{embedding}（384维）
- POST /api/v1/ai/embed-batch：{texts}→{embeddings}

rag.py:
- POST /api/v1/ai/rag/search：{query, top_k}→{results}
- POST /api/v1/ai/rag/add：{fault_type, root_cause, repair_steps, tags}→{knowledge_id}

logs.py:
- POST /api/v1/ai/logs/search：{cluster_name, namespace, pod, tail}→{logs: [{timestamp, message}]}
  调用es_client.search_logs（供api-server日志查看代理调用）
- GET /api/v1/ai/logs/health：ES连通性检查

【3.18 ai-service/Dockerfile】
python:3.11-slim基础镜像
安装依赖
注意sentence-transformers需要额外系统依赖（libgomp1）
构建时预下载embedding模型（HF_HUB_OFFLINE=1）或启动时从挂载的模型缓存volume加载


═══════════════════════════════════════════════════════════════
模块4：api-server 完整实现
═══════════════════════════════════════════════════════════════

【4.1 api-server/app/clients/executor_client.py】
ExecutorClient类：
- __init__：创建gRPC channel连接K8S_EXECUTOR_GRPC_ADDR（interceptor注入trace_id到metadata）
- start_watch(cluster_id)：调用StartWatch
- stop_watch(cluster_id)：调用StopWatch
- execute_command(cluster_id, namespace, command)：单条命令
- execute_plan(cluster_id, plan_id, steps)：流式接收执行进度（实时写入ExecutionLog表）
- rollback_plan(cluster_id, plan_id, steps)：回滚
- health_check(cluster_id)：健康检查
- collect_quota(cluster_id)：配额采集
- run_script(cluster_id, script_content, parameters)：脚本执行
- batch_execute(cluster_ids, command)：批量执行

每个方法包含：超时控制（30秒默认）、错误处理、日志记录（带trace_id）

【4.2 api-server/app/clients/ai_client.py】
AIClient类：
- __init__：base_url = AI_SERVICE_HTTP_ADDR
- diagnose(alert_id, alert_data, context_data)
- multi_diagnose(alert_id, alert_data, context_data)
- generate_plan(diagnosis_id, alert_data, diagnosis_data)
- verify_repair(plan_id, before_snapshot, after_snapshot, metrics_comparison)
- search_similar(query, top_k)
- add_knowledge(fault_type, root_cause, repair_steps, tags)
- embed(text)
- search_logs(cluster_name, namespace, pod, tail=100)：调ai-service日志查询接口（供前端日志查看/诊断上下文）
- 所有方法使用httpx.AsyncClient，请求头携带X-Trace-Id

【4.3 api-server/app/middleware/auth.py】
- get_current_user依赖：
  从Header获取Authorization: Bearer {token}
  decode_jwt
  查询User
  返回User对象
- get_current_active_user：检查is_active

【4.4 api-server/app/middleware/permission.py】
- require_role(*allowed_roles)依赖工厂：
  返回依赖函数
  检查current_user.role是否在allowed_roles中
  不在则403
- require_admin = require_role("admin")
- require_operator = require_role("admin", "operator")
- require_any_user = require_role("admin", "operator", "viewer")

【4.5 api-server/app/api/clusters.py】
- POST /api/v1/clusters（require_operator）
  请求：{name, description, kubeconfig, maintenance_window}
  逻辑：
    1. 检查name是否已存在
    2. encrypt_aes256_gcm(kubeconfig)
    3. 创建Cluster记录（status=unknown）
    4. 调用executor_client.health_check验证连通性
    5. 连通→更新status=healthy+version→调用executor_client.start_watch启动Watch
    6. 不连通→更新status=unreachable→仍然创建但标记不可达（返回错误分类：证书过期/网络不通/权限不足）
  返回创建的集群信息
- GET /api/v1/clusters（require_any_user）：列表
- GET /api/v1/clusters/{id}（require_any_user）：详情
- DELETE /api/v1/clusters/{id}（require_admin）
  逻辑：调用stop_watch→删除集群+关联的alerts/tasks
- POST /api/v1/clusters/{id}/health-check（require_operator）
  调用executor_client.health_check，更新状态

【4.6 api-server/app/api/alerts.py】
- GET /api/v1/alerts（require_any_user）
  查询参数：cluster_id, severity, status, fault_type, start_time, end_time, page, page_size
  返回分页结果
- GET /api/v1/alerts/{id}（require_any_user）：详情+关联的AI诊断结果
- POST /api/v1/alerts/{id}/resolve（require_operator）：status=resolved+resolved_at
- POST /api/v1/alerts/{id}/diagnose（require_operator）
  逻辑：
    1. 查询告警信息
    2. 调用executor_client获取Pod/Node状态（describe），日志通过ai_client.search_logs从ES获取作为context_data（ES不可用时降级为空）
    3. 调用ai_client.diagnose（带severity）
    4. 审计记录：update_audit_record（ai_call_detail）
    5. 返回诊断结果
- GET /api/v1/alerts/{id}/diagnosis（require_any_user）

【4.7 api-server/app/api/tasks.py】
- GET /api/v1/tasks（require_any_user）：含关联Alert信息
- GET /api/v1/tasks/{id}（require_any_user）：含TaskAuditLog时间线
- POST /api/v1/tasks/{id}/acknowledge（require_operator）：pending→acknowledged
- POST /api/v1/tasks/{id}/start（require_operator）：acknowledged→in_progress
- POST /api/v1/tasks/{id}/resolve（require_operator）：in_progress→resolved（需resolution字段）
- 状态转换校验在service层，非法转换返回400
- 每次变更自动写TaskAuditLog

【4.8 api-server/app/services/notification_service.py】
- send_dingtalk_text(webhook, content, at_mobiles)：POST webhook
- send_dingtalk_markdown(webhook, title, content, at_mobiles)
- send_dingtalk_action_card(webhook, title, text, buttons, at_mobiles)
  buttons格式：[{title, actionURL}]（仅用于跳转查看详情，审批操作不走URL直连）
- send_wechat_text(webhook, content, mentioned_list)
- send_wechat_markdown(webhook, content)
- send_alert_notification(alert)：
  查询所有is_active的NotificationConfig
  severity=critical：发所有渠道
  severity=high：发钉钉+企微
  severity=medium：仅钉钉
  severity=low：不发送
  失败重试3次（间隔2s/4s/8s）
  限频：同告警（alert_id）30分钟内不重复通知（Redis记录最后通知时间）
  维护窗口内不发送
  记录NotificationLog
- send_approval_notification(approval, plan)：
  通过钉钉/企微**机器人交互卡片**发送待审批消息（含批准/拒绝按钮，按钮触发机器人消息事件回传，不直接跳转公开URL）
  title: "【审批】修复方案待审批"
  text: 包含故障信息、修复方案摘要、风险等级、操作类型
- send_repair_complete_notification(alert, verification)：发送修复完成通知
- format_alert_message(alert)：格式化告警消息

【4.9 api-server/app/services/decision_engine.py】
- DecisionLevel类：
  auto=0, auto_with_log=1, auto_with_confirm=2, approval_1=3, approval_1_with_escalation=4, approval_2=5
- get_schedule_type(dt)：
  判断周末（周六日）→"weekend"
  判断节假日→"holiday"（通过配置的假期列表）
  判断工作时间（周一至五9:00-18:00）→"working_hours"
  其余→"off_hours"
- get_policy(schedule_type)：从数据库查询
- get_operation_decision(operation_type, policy)：
  READ→policy.read_rule
  CREATE→policy.create_rule
  UPDATE→policy.update_rule
  DELETE→policy.delete_rule
- evaluate_plan(plan_id)：
  读取RepairPlan
  解析plan_json中的steps
  遍历每步，调用AnalyzeOperationType（与ai-service中逻辑一致）
  统计READ/CREATE/UPDATE/DELETE数量
  取所有操作的最高决策级别
  **高危强制人工**：任一step命令含delete/rm/purge/drop，且决策级别<approval_1（3）时，强制提升为approval_1（至少单人审批）
  获取当前时间schedule_type对应的policy
  最终决策=MAX(操作最高级别, 时间策略对应级别)
  存入DecisionResult
  返回决策详情
- seed_default_policies()：
  working_hours（1,2,3,4,5, 09:00-18:00, READ=auto, CREATE=approval_1, UPDATE=approval_1, DELETE=approval_2, timeout=1800）
  off_hours（1,2,3,4,5, READ=auto, CREATE=auto_with_confirm, UPDATE=approval_1, DELETE=approval_2, timeout=900, escalation=true）
  weekend（6,7, READ=auto, CREATE=auto_with_log, UPDATE=auto_with_log, DELETE=approval_1_with_escalation, timeout=600, escalation=true）
  holiday（READ=auto, CREATE=auto_with_log, UPDATE=auto_with_log, DELETE=approval_1_with_escalation, timeout=900, escalation=true）

【4.10 api-server/app/api/decisions.py】
- POST /api/v1/decisions/evaluate（require_operator）：{plan_id}→{decision, reason, schedule_type, operation_counts}
- GET /api/v1/decisions/policies（require_any_user）
- POST /api/v1/decisions/policies（require_admin）
- PUT /api/v1/decisions/policies/{id}（require_admin）

【4.11 api-server/app/services/approval_service.py】
- create_approval(plan_id, decision_level, current_user)：
  decision_level>=4（approval_2）：required_level=5，双人审批
  decision_level>=3（approval_1）：required_level=3，单人审批
  创建Approval记录
  发送审批通知（钉钉/企微机器人交互卡片）
- approve(approval_id, user_id, comment)：
  数据库条件更新防并发：UPDATE approvals SET ... WHERE id=? AND status='pending'，受影响行数=0则返回"已处理"
  单人：直接approved
  双人：approved_by追加user_id，2人后approved
  更新DecidedAt
  审计：记录operator（平台user_id）+时间+意见
  审批通过后自动触发execute_approved_plan(approval)
- reject(approval_id, user_id, comment)：
  任一人拒绝→rejected（条件更新防并发）
- timeout_handler()：
  先获取Redis锁 lock:approval_timeout（SET NX PX 30000，抢到锁才执行，Lua释放）
  每分钟检查
  created_at + approval_timeout < now 且 status=pending
  → 条件更新status=timeout（WHERE status='pending'兜底）
  → 通知升级
- 回调处理（供webhooks调用）：
  handle_callback(source_msg_id, external_user_id, action, comment)：
    幂等检查：source_msg_id在Redis或Approval表中已存在则忽略（钉钉eventId/企微msgId）
    通过映射表查外部user_id → 平台user_id（按dingtalk_user_id/wechat_userid匹配，查不到拒绝并转管理员人工确认）
    调用approve/reject
- execute_approved_plan(approval)：
  更新RepairPlan status=executing
  调用executor_client.execute_plan（流式接收进度，实时写ExecutionLog）
  执行完成后更新RepairPlan status=completed/failed
  执行完成后触发修复验证（见【4.11b】）

【4.11b 修复验证与闭环】（新增，对应V1.3修正14/15）
- verify_and_handle(plan_id)：
  执行完成后采集修复前后快照 → 调用ai_client.verify_repair
  verdict=healed：更新验证状态；触发知识库沉淀（修复成功：命中同fault_type则success_count+1，无条目则创建root_cause=诊断结论、repair_steps=成功步骤；可配置自动/人工确认）
  verdict=degraded：再等待30秒重新验证（最多2次）
  verdict=unchanged：记录并重新触发诊断
  verdict=worsened 或连续2次非healed：
    - 已执行步骤存在→自动调用executor_client.rollback_plan回滚
    - 自动二次诊断→生成新方案（复用审批流）
    - 再次失败→升级人工：创建高优Task + 通知admin/operator
  每次验证结果写审计（verification_detail）

【4.12 api-server/app/api/approvals.py】
- POST /api/v1/approvals（require_operator）：{plan_id}→创建审批
- GET /api/v1/approvals（require_any_user）：列表，?status=pending筛选
- GET /api/v1/approvals/{id}（require_any_user）：详情（含审批人user_id）
- 审批操作统一走钉钉/企微机器人消息回调（webhooks），不提供公开URL直连审批接口
- 平台内审批操作（可选）：POST /api/v1/approvals/{id}/decide（require_operator）body:{action, comment}，走同一approval_service逻辑（条件更新+审计）

【4.13 api-server/app/api/webhooks.py】
- POST /api/v1/webhooks/dingtalk/callback：
  钉钉机器人消息回调（加签校验：timestamp+sign，sign=HmacSHA256(timestamp+secret)），验签失败403
  解析消息内容（审批卡片按钮回调：approval_id、action、发送者userId）
  幂等：按eventId去重
  调用approval_service.handle_callback
  响应钉钉要求的JSON格式
- POST /api/v1/webhooks/wechat/callback：
  企微回调（msg_signature验签 + AES解密消息体），验签失败403
  解析审批消息（msgId去重）
  调用approval_service.handle_callback
  返回"success"明文（企微要求）

【4.14 api-server/app/services/batch_service.py】
- create_batch_operation(name, alert_ids, plan_id, strategy, failure_policy, batch_size)：
  根据alert_ids查询告警→确定集群列表
  创建BatchOperation（total_clusters=去重后的集群数）
  为每个集群创建BatchOperationItem
- execute_batch(batch_operation_id)：
  先获取Redis锁 lock:batch:{batch_id}（防多副本重复执行，Lua释放）
  读取BatchOperation和Items
  parallel：asyncio.gather并发执行
  rolling：for循环逐个执行
  batch：按batch_size分批，每批内gather
  执行失败时按failure_policy：
    continue：记录失败，继续
    stop：标记后续为skipped，停止
    rollback：回滚当前，停止后续
  更新success_count/failed_count/status
- get_batch_detail(batch_operation_id)：含各Item状态

【4.15 api-server/app/api/batch.py】
- POST /api/v1/batch（require_operator）：创建
- POST /api/v1/batch/{id}/execute（require_operator）：执行
- GET /api/v1/batch（require_any_user）：列表
- GET /api/v1/batch/{id}（require_any_user）：详情

【4.16 api-server/app/services/scheduler_service.py】
- scheduler = AsyncIOScheduler()
- register_task(custom_task)：
  根据cron_expression创建CronTrigger
  add_job(execute_task, trigger, args=[custom_task.id], id=f"custom_task_{id}", coalesce=True, max_instances=1, misfire_grace_time=60)
- unregister_task(task_id)
- execute_task(task_id)：
  先获取Redis锁 lock:custom_task:{task_id}（SET NX PX 30000，抢到才执行，finally Lua释放；任务超过30秒自动续期PEXPIRE）
  查询CustomTask
  创建CustomTaskLog（status=running）
  根据task_type：
    restart：executor_client.execute_command(kubectl rollout restart deployment/{target} -n {ns})
    scale：executor_client.execute_command(kubectl scale deployment/{target} --replicas={param} -n {ns})
    inspect：executor_client.collect_quota
    script：executor_client.run_script
    once：执行后设置is_enabled=False
  更新CustomTaskLog（status/output）
  更新CustomTask.last_run_at/last_run_status
- load_all_tasks()：启动时加载所有is_enabled=True的任务
- refresh_task(task_id)：先unregister再register
- start()：scheduler.start()

【4.17 api-server/app/api/custom_tasks.py】
- GET /api/v1/custom-tasks（require_any_user）
- POST /api/v1/custom-tasks（require_operator）：创建+注册
- PUT /api/v1/custom-tasks/{id}（require_operator）：更新+刷新注册
- DELETE /api/v1/custom-tasks/{id}（require_admin）：删除+注销
- POST /api/v1/custom-tasks/{id}/toggle（require_operator）：启用/暂停
- POST /api/v1/custom-tasks/{id}/run-now（require_operator）：立即执行
- GET /api/v1/custom-tasks/{id}/logs（require_any_user）

【4.18 api-server/app/api/resources.py】
- GET /api/v1/resources/clusters/{cluster_id}/overview（require_any_user）：
  从quota_snapshots表取最新一条集群级快照
  返回节点数/CPU/内存概览
- GET /api/v1/resources/clusters/{cluster_id}/namespaces（require_any_user）：
  按namespace聚合的最新配额数据
- GET /api/v1/resources/clusters/{cluster_id}/namespaces/{namespace}/quota（require_any_user）
- GET /api/v1/resources/clusters/{cluster_id}/namespaces/{namespace}/pods（require_any_user）
- GET /api/v1/resources/clusters/{cluster_id}/namespaces/{namespace}/deployments（require_any_user）
- POST /api/v1/resources/clusters/{cluster_id}/collect（require_operator）：触发立即采集

【4.19 api-server/app/services/audit_service.py】
- create_audit_record(trace_id, trigger_snapshot=None, ai_call_detail=None, decision_detail=None, execution_detail=None, verification_detail=None)：
  查询最后一条记录的hash_value作为previous_hash
  拼接当前数据（各字段JSON序列化后拼接）
  hash_value = SHA256(previous_hash + 拼接数据)
  创建AuditRecord
  返回decision_id
- update_audit_record(trace_id, field, data)：
  查询该trace_id的AuditRecord
  更新指定字段
  重新计算hash_value = SHA256(previous_hash + 全部字段拼接)
- verify_audit_chain()：
  按ID升序遍历所有记录
  逐条验证hash_value == SHA256(previous_hash + data)
  返回{total_records, verified, integrity_ok}
- get_trace(trace_id)
- search_audit(cluster_name, operation_type, start_time, end_time, trace_id, page, page_size)
- generate_report(trace_id)：组装完整审计报告JSON

【4.20 api-server/app/api/audit.py】
- GET /api/v1/audit/trace/{trace_id}（require_any_user）
- GET /api/v1/audit/search（require_any_user）
- GET /api/v1/audit/report?trace_id=xxx（require_any_user）
- POST /api/v1/audit/verify-chain（require_admin）

【4.21 api-server/app/api/stats.py】
- GET /api/v1/stats/overview（require_any_user）：
  集群总数、健康集群数、待处理告警数、待审批数、今日AI调用成本
- GET /api/v1/stats/ai-effectiveness?range=7d|30d|90d（require_any_user）：
  AIEffectivenessStat按日期聚合
  返回诊断准确率、修复成功率、MTTR趋势
- GET /api/v1/stats/repair-success-rate（require_any_user）
- GET /api/v1/stats/cost-alert（require_admin）：
  按日聚合AICallLog.cost，今日成本超过阈值（DAILY_COST_LIMIT的80%）时返回告警标记（成本告警通知admin，见定时任务）

【4.22 api-server/app/api/knowledge.py】
- GET /api/v1/knowledge/search?q=xxx&top_k=5（require_any_user）：
  调用ai_client.search_similar
- GET /api/v1/knowledge（require_any_user）：列表（含pending_review标记）
- POST /api/v1/knowledge（require_operator）：
  调用ai_client.add_knowledge
- PUT /api/v1/knowledge/{id}/review（require_operator）：处理pending_review条目（确认/拒绝）
- DELETE /api/v1/knowledge/{id}（require_admin）

【4.23 api-server/app/api/auth.py】
- POST /api/v1/auth/login：
  验证用户名密码
  返回{token, user: {id, username, role, email}}
- GET /api/v1/auth/me（require_any_user）：当前用户信息
- POST /api/v1/auth/logout（require_any_user）：Token加入Redis黑名单

【4.24 api-server/app/api/users.py】
- GET /api/v1/users（require_admin）
- POST /api/v1/users（require_admin）：{username, password, email, role, dingtalk_user_id, wechat_userid}
- PUT /api/v1/users/{id}（require_admin）：更新角色/邮箱/状态/外部ID映射
- DELETE /api/v1/users/{id}（require_admin）
- POST /api/v1/users/{id}/api-key（require_admin）：重新生成API Key

【4.25 api-server/app/api/notifications.py】
- GET /api/v1/notifications/configs（require_any_user）
- POST /api/v1/notifications/configs（require_admin）
- DELETE /api/v1/notifications/configs/{id}（require_admin）
- GET /api/v1/notifications/logs（require_any_user）

【4.26 api-server/app/main.py 完整启动逻辑】
1. 创建FastAPI应用
2. 注册中间件（CORS、请求日志trace_id、异常处理）
3. 注册所有路由
4. 启动事件：
   - alembic upgrade head（migration统一管理，不用create_all）
   - seed_default_policies()插入默认决策策略
   - seed_default_users()创建admin/operator/viewer三个用户
   - 调用ai_client确保预置提示词模板已创建
   - scheduler_service.load_all_tasks()+start()
   - 启动审批超时检查定时任务（每分钟，Redis锁）
   - 启动AI成本告警检查定时任务（每小时，今日成本超限通知admin）

【4.27 api-server/Dockerfile】
python:3.11-slim
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080

【4.28 api-server/app/api/logs.py】（新增，日志查看对接现有 Elasticsearch）
- GET /api/v1/logs/search（require_any_user）
  参数：cluster_name, namespace, pod, tail（默认100）
  逻辑：调用ai_client.search_logs（ai-service → ES），返回{logs: [{timestamp, message}]}
  代理层，不直接连ES（日志查询统一走ai-service，保持数据访问收敛）
  异常/ES不可用时返回空列表+warning字段，不影响页面
- 前端日志查看入口：AlertList行展开"查看日志"、TaskDetail、ExecutionDetail日志面板，均调用本接口


═══════════════════════════════════════════════════════════════
模块5：前端完整实现
═══════════════════════════════════════════════════════════════

【5.1 frontend/package.json】
dependencies:
- react: ^18.3.1
- react-dom: ^18.3.1
- react-router-dom: ^6.26.2
- antd: ^5.21.1
- @ant-design/icons: ^5.5.1
- axios: ^1.7.7
- @tanstack/react-query: ^5.59.0
- dayjs: ^1.11.13
- @ant-design/charts: ^2.2.1
devDependencies:
- typescript, vite, @vitejs/plugin-react, @types/react, @types/react-dom

【5.2 frontend/src/services/api.ts】
- axios实例：baseURL="/api/v1"
- 请求拦截器：从localStorage获取token，添加Authorization header
- 响应拦截器：code!==200时显示错误message，401跳转/login
- 导出所有API调用函数（与后端接口一一对应）：
  login, getMe, logout
  getClusters, createCluster, deleteCluster, healthCheckCluster
  getAlerts, getAlert, resolveAlert, diagnoseAlert, getDiagnosis
  getTasks, getTask, acknowledgeTask, startTask, resolveTask
  getNotificationConfigs, createNotificationConfig, deleteNotificationConfig, getNotificationLogs
  getAIModels, createAIModel, testAIModel, getAICallLogs, getModelStats
  getPromptTemplates, createPromptTemplate, updatePromptTemplate, testPromptTemplate
  evaluateDecision, getDecisionPolicies, createDecisionPolicy, updateDecisionPolicy
  getApprovals, getApproval, createApproval, decideApproval
  getExecutionStatus（轮询执行状态：GET /executions/{planId}?since_step=3，增量拉取）
  searchLogs（GET /logs/search?cluster_name=&namespace=&pod=&tail=，日志查看，ES数据源）
  getBatchList, getBatchDetail, createBatch, executeBatch
  getCustomTasks, createCustomTask, updateCustomTask, deleteCustomTask, toggleCustomTask, runCustomTaskNow, getCustomTaskLogs
  getResourceOverview, getNamespaces, getNamespaceQuota, getNamespacePods, getNamespaceDeployments, triggerCollect
  searchKnowledge, getKnowledgeList, addKnowledge, deleteKnowledge, reviewKnowledge
  getAuditTrace, searchAudit, getAuditReport, verifyAuditChain
  getStatsOverview, getAIEffectiveness, getRepairSuccessRate, getCostAlert
  getUsers, createUser, updateUser, deleteUser, regenerateAPIKey

（注：无WebSocket相关代码，执行进度全部通过轮询getExecutionStatus获取）

【5.3 frontend/src/services/websocket.ts】
（已删除，不存在此文件。执行进度通过【5.2】中的getExecutionStatus轮询获取）

【5.4 frontend/src/App.tsx】
路由配置：
/login → Login
/ → MainLayout（需要登录）
  /dashboard → Dashboard
  /clusters → ClusterList
  /alerts → AlertList
  /tasks → TaskList
  /tasks/:id → TaskDetail
  /notifications → NotificationConfig
  /ai-models → AIModelConfig
  /prompts → PromptTemplates
  /decisions → DecisionPolicies
  /approvals → ApprovalList
  /executions/:planId → ExecutionDetail
  /batch → BatchOperation
  /custom-tasks → CustomTasks
  /resources → ResourceQuota
  /knowledge → KnowledgeBase
  /audit → AuditTrace
  /stats → StatsDashboard
  /users → UserManagement

【5.5 frontend/src/components/MainLayout.tsx】
- Ant Design Layout
- Sider：Logo区域+Menu（19个菜单项，带图标）
- Header：面包屑+用户下拉（用户名/角色/退出按钮）
- Content：Outlet渲染子页面
- Menu选中状态与路由同步

【5.6 frontend/src/components/StatusTag.tsx】
根据status值渲染不同颜色Tag：
- healthy/success/resolved/approved/completed/healed → green
- unreachable/failed/rejected/worsened → red
- pending/acknowledged/running → blue
- in_progress/executing → orange
- timeout/skipped/degraded → gray
- unknown → default

【5.7 frontend/src/components/SeverityTag.tsx】
- critical → red
- high → orange
- medium → gold
- low → blue

【5.8 frontend/src/components/CodeBlock.tsx】
- 深色背景(#1e1e1e)
- 等宽字体
- 右上角复制按钮
- 白色文字

【5.9 所有19个页面详细实现】

Login.tsx：
- 居中卡片布局
- Form：username+password
- 提交后存储token和user信息到localStorage
- 跳转/dashboard

Dashboard.tsx：
- 4个Statistic卡片：集群总数/健康集群/待处理告警/待审批
- 集群健康状态表格（名称/状态Tag/版本/节点数）
- 最近10条告警列表
- 配额使用概览（Progress组件展示CPU/内存使用率）

ClusterList.tsx：
- Table：名称/描述/状态Tag/版本/维护窗口/创建时间/操作
- 操作列：健康检查按钮/删除按钮（Popconfirm）
- 添加按钮→Modal：name/description/kubeconfig文件上传（Upload组件）/维护窗口配置
- 删除调用deleteCluster

AlertList.tsx：
- 筛选区：集群Select/严重级别Select/状态Select/故障类型Select/日期RangePicker
- Table：集群/资源类型/资源名/命名空间/故障类型/SeverityTag/StatusTag/事件数/时间/操作
- 行展开：展示AI诊断结果（如果有）——根因/建议/置信度Progress/风险Tag；"查看日志"按钮（从ES查询，Drawer展示）
- 操作：解决按钮/触发AI诊断按钮
- 批量选择：Table rowSelection+批量处理按钮

TaskList.tsx：
- 筛选：状态Select
- Table：ID/关联告警信息/状态Tag/处理人/创建时间
- 点击行→跳转TaskDetail

TaskDetail.tsx：
- Descriptions展示关联告警信息
- 状态流转按钮组（根据当前状态显示可用操作）
- 备注输入Modal
- Timeline展示TaskAuditLog（操作者/动作/旧状态→新状态/时间/备注）

NotificationConfig.tsx：
- 卡片列表：名称/渠道Tag/Webhook URL（脱敏）/状态Switch
- 添加Modal：name/channel_type/webhook_url/secret
- 发送日志Table（分页）

AIModelConfig.tsx：
- 卡片列表：Provider/ModelName/Priority/最低severity/成本参数/状态
- 添加Modal：provider选择(openai/deepseek/claude)/model_name/api_key/max_tokens/temperature/priority/min_severity/cost_per_1k_tokens
- 测试连通按钮
- AI调用日志Table（含Token消耗和成本）

PromptTemplates.tsx：
- Table：名称/分类Tag/版本/状态/操作
- 编辑Drawer：System Prompt输入框/User Prompt Template输入框/变量提取展示
- 测试面板：填入测试变量→调用AI→展示结果

DecisionPolicies.tsx：
- Table：策略名称/时间段/时间范围/READ规则/CREATE规则/UPDATE规则/DELETE规则/审批超时/自动升级
- 编辑Modal：全部字段可编辑

ApprovalList.tsx：
- Tabs：待审批/已处理
- 待审批：卡片列表（方案摘要/操作类型Tag/风险Tag/发起时间/批准/拒绝按钮——平台内审批入口，钉钉/企微审批走机器人）
- 审批操作Modal：意见输入
- 已处理：Table（状态Tag/审批人/决定时间/意见）

ExecutionDetail.tsx：
- Steps组件展示执行步骤（当前步骤高亮）
- 进度轮询：每3秒调用getExecutionStatus(planId, since_step)增量拉取，执行完成后停止；页面提供"刷新"按钮
- 实时日志面板（黑色背景滚动日志，增量追加）
- visibilitychange：后台tab暂停轮询，切回恢复
- 熔断Alert（红色警告）
- 回滚按钮

BatchOperation.tsx：
- Table：名称/类型/策略Tag/失败策略/状态Tag/成功数/失败数/创建时间
- 创建Modal：选择告警（勾选）→策略选择（并行/滚动/分批）→失败策略选择
- 详情Drawer：各集群执行状态表格

CustomTasks.tsx：
- Table：名称/集群/类型Tag/Cron表达式/目标资源/启用Switch/上次执行状态/操作
- 创建Modal：集群Select/任务类型Select/目标资源填写/Cron表达式输入/脚本编辑器（TextArea）
- 操作：编辑/立即执行/查看日志Drawer

ResourceQuota.tsx：
- 集群Select
- 概览卡片：节点数/总CPU/总内存
- namespace配额Table：namespace/CPU进度条/内存进度条/Pod进度条
- Tab切换：Pods/Deployments/Services列表

KnowledgeBase.tsx：
- 搜索框（防抖300ms）
- 知识卡片列表：故障类型Tag/根因摘要/修复步骤/成功率/使用次数/相似度/待审核标记（pending_review显示审核按钮）
- 新增知识Modal

AuditTrace.tsx：
- 搜索筛选：时间RangePicker/集群/操作类型/TraceID输入框
- Table：TraceID/DecisionID/时间/操作摘要
- 点击→Drawer展示Timeline（触发→AI诊断→决策→审批→执行→验证）
- 哈希链验证按钮→Modal展示完整性验证结果

StatsDashboard.tsx：
- 时间范围Select（7d/30d/90d）
- 4个图表（@ant-design/charts）：
  Line：诊断准确率趋势
  Line：修复成功率趋势
  Line：MTTR趋势
  Column：AI调用成本

UserManagement.tsx：
- Table：用户名/邮箱/角色Tag/状态/创建时间/钉钉ID/企微ID
- 创建Modal：username/password/email/role/dingtalk_user_id/wechat_userid
- 角色编辑Select
- API Key查看/重新生成按钮

【5.10 frontend/Dockerfile】
node:20-alpine → 构建 → nginx:alpine
nginx配置：/api/v1反向代理到api-server Service


═══════════════════════════════════════════════════════════════
模块6：部署配置
═══════════════════════════════════════════════════════════════

【6.1 docker-compose.yml】（数据库/缓存使用外部中间件 192.168.6.99，compose 只编排应用服务）
services:
  api-server:
    build: ./api-server
    environment: DATABASE_URL=mysql+aiomysql://root:root@192.168.6.99:3306/api_ops, REDIS_URL=redis://:RedisDBforeFt20200911@192.168.6.99:6379/0, K8S_EXECUTOR_GRPC_ADDR=k8s-executor:9090, AI_SERVICE_HTTP_ADDR=http://ai-service:9000, ENCRYPTION_KEY=***, JWT_SECRET_KEY=***
    ports: "8080:8080"
  k8s-executor:
    build: ./k8s-executor
    environment: DATABASE_URL=root:root@tcp(192.168.6.99:3306)/api_ops?charset=utf8mb4&parseTime=True&loc=Local, GRPC_PORT=9090
    ports: "9090:9090"
  ai-service:
    build: ./ai-service
    environment: DATABASE_URL=mysql+aiomysql://root:root@192.168.6.99:3306/api_ops, OPENAI_API_KEY=***, DEEPSEEK_API_KEY=***, CLAUDE_API_KEY=***, PROMETHEUS_URL=http://prometheus:9090, ES_URL=http://elasticsearch:9200
    ports: "9000:9000"
  frontend:
    build: ./frontend
    ports: "80:80"
    depends_on: [api-server]
注：生产环境建议为应用创建专用 MySQL 账号（GRANT 仅业务库权限），并在 secret.yaml 中管理连接信息。

【6.2 k8s/下所有清单文件】
- namespace.yaml：api-ops
- configmap-api-server.yaml：DATABASE_URL、REDIS_URL、K8S_EXECUTOR_GRPC_ADDR、AI_SERVICE_HTTP_ADDR
- configmap-k8s-executor.yaml：DATABASE_URL、GRPC_PORT、COMMAND_WHITELIST
- configmap-ai-service.yaml：DATABASE_URL、EMBEDDING_MODEL、PROMETHEUS_URL、ES_URL、ES_INDEX_PATTERN
- secret.yaml：JWT_SECRET_KEY、ENCRYPTION_KEY、OPENAI_API_KEY、DEEPSEEK_API_KEY、CLAUDE_API_KEY、DINGTALK_SECRET、WECHAT_TOKEN（base64）
- deployment-api-server.yaml：3副本、podAntiAffinity（topologyKey: kubernetes.io/hostname）、livenessProbe和readinessProbe（HTTP GET /health）、resources limits/requests
- deployment-k8s-executor.yaml：**固定1副本**（有状态watcher，多副本重复watch导致告警重复）、反亲和、readinessProbe（gRPC健康检查）
- deployment-ai-service.yaml：2副本、反亲和
- deployment-frontend.yaml：2副本
- service-api-server.yaml：ClusterIP:8080
- service-k8s-executor.yaml：ClusterIP:9090
- service-ai-service.yaml：ClusterIP:9000
- service-frontend.yaml：ClusterIP:80
- ingress.yaml：/ → frontend:80，/api/v1 → api-server:8080
- hpa-api-server.yaml：minReplicas=3, maxReplicas=5, targetCPUUtilizationPercentage=70
- hpa-ai-service.yaml：minReplicas=2, maxReplicas=4
（hpa-k8s-executor.yaml：已禁用/删除——executor为有状态服务，固定单副本，不做HPA）
- pdb.yaml：api-server minAvailable=2
- networkpolicy.yaml：ai-service和k8s-executor只允许api-server命名空间的入站
- backup-cronjob.yaml：每日2:00 mysqldump备份到对象存储，**备份包必须同时包含ENCRYPTION_KEY**（单独安全存储或备份包内加密存放），否则恢复后kubeconfig密文无法解密

【6.3 Makefile】
目标：build/up/down/logs/migrate/seed/status/crypto-test
- crypto-test：执行Python↔Go跨语言加密互测（Python生成密文→Go测试解密，Go生成→Python解密，两端必须通过）

【6.4 README.md】
包含：项目简介、架构图（ASCII）、快速开始、环境变量说明、默认账号（admin/operator/viewer初始密码，首次登录强制改密）

【6.5 docs/ARCHITECTURE.md】
三服务架构详解、服务间通信、数据流、部署拓扑
外部依赖接入说明：监控指标→Prometheus（ai-service直查），日志→Elasticsearch（ai-service查询，api-server代理给前端），均复用现有基础设施，不自行采集存储

【6.6 docs/API.md】
所有API端点列表+请求/响应示例

【6.7 docs/DEPLOYMENT.md】
Docker Compose部署步骤+K8s部署步骤+备份恢复（恢复流程：先恢复ENCRYPTION_KEY再恢复数据库）

【6.8 docs/ENCRYPTION.md】（新增）
跨语言加密格式约定：
- 算法：AES-256-GCM
- 随机12字节IV + 密文 + 16字节tag，base64编码拼接：base64(IV + ciphertext + tag)，IV在前
- ENCRYPTION_KEY：32字节（256位），两端直接使用，不额外派生
- 两端代码注释互相引用本文档


═══════════════════════════════════════════════════════════════
最终要求
═══════════════════════════════════════════════════════════════

1. 所有代码有中文注释（注释说明该代码块的功能）
2. 所有API统一响应格式：{code: 200, message: "success", data: {...}}
3. 错误响应：{code: 4xx/5xx, message: "错误描述", data: null}
4. 三个服务共享同一个MySQL数据库，表名一致；表结构由api-server的alembic统一管理（migration），k8s-executor与ai-service启动时不建表；中间件（MySQL/Redis）部署在 192.168.6.99
5. api-server是唯一对外入口，前端只和api-server通信
6. k8s-executor只接收api-server的gRPC调用；固定单副本部署，不做HPA
7. ai-service只接收api-server的HTTP调用
8. 审计记录在关键节点自动写入（含审批动作、验证结果）
9. 数据库模型字段与本文档定义完全一致
10. Go代码使用zap日志（JSON结构化），Python代码使用logging（JSON结构化）；日志全程带trace_id
11. 所有外部API调用（AI模型/钉钉/企微/Prometheus）使用httpx异步
12. 代码可直接编译运行
13. 所有调度类任务（审批超时检查/定时任务/批量执行）必须使用Redis分布式锁防多副本重复执行
14. 钉钉/企微回调必须验签；审批身份通过外部user_id映射表转换为平台user_id
15. 跨语言加密必须通过make crypto-test互测
16. kubeconfig加密密钥（ENCRYPTION_KEY）必须随备份一起保存
17. 日志查询对接现有 Elasticsearch（ES），监控/诊断指标对接现有 Prometheus，不自行采集存储日志与监控数据（ES/Prometheus不可用时降级为空，不影响主流程）

请开始开发。按模块顺序逐个完成，不要跳过任何模块，不要精简任何功能。

═══════════════════════════════════════════════════════════════
开发执行计划（2026-08-17 确认，按此顺序开发）
═══════════════════════════════════════════════════════════════

总原则：后端先行 + 最小闭环（纵向切片）验证架构，再横向铺功能；API契约先行（FastAPI /docs OpenAPI 作为前后端契约）。

1. 数据库模型 + 基础设施：alembic 初始化 + 全部模型 + 加密互测（make crypto-test 先行通过）+ 日志框架
2. k8s-executor：proto → gRPC server → k8sclient → watcher → execute/gray/circuit/rollback → quota/health/script
3. ai-service：模型适配器 + prompt 模板 + 诊断/方案/验证 + embedding/RAG + Prometheus 上下文
4. api-server 核心链路：认证 + 集群管理（含加密存 kubeconfig）+ 告警 + 诊断联动 + 决策引擎
5. 最小闭环联调：登录 → 添加集群 → watch 出告警 → AI 诊断 → 生成方案 → 决策 → 执行 → 验证（用 curl/脚本验证，不依赖 UI）
6. 前端核心页：Login / ClusterList / AlertList（含诊断展示）/ ExecutionDetail（轮询）
7. 横向补齐：审批流（含钉钉/企微回调+验签）、批量、定时任务（Redis 锁）、通知、知识库、审计、统计、用户管理、剩余前端页面
8. 部署配置：docker-compose + k8s 清单（executor 单副本、migration 统一、备份含密钥）+ README/docs

前端分两期：一期（步骤6）核心闭环页；二期（步骤7 内）统计/用户/审计/知识库/批量/定时任务页。
二期演进（暂不实现，架构预留）：结构化日志已含trace_id（一期实现）；Redis Stream 事件链异步化（故障风暴时再上）；pgvector 向量检索（知识库超10万条再上）。

═══════════════════════════════════════════════════════════════
