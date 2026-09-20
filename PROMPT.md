═══════════════════════════════════════════════════════════════
项目：API-OPS自愈平台 - 三服务架构完整开发
工具：OpenClaw
要求：自动完成所有代码开发，无需手动干预
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
- 三个服务 → 同一个PostgreSQL数据库
- api-server → Redis（缓存/限流/会话）

【核心功能清单】
1. 多K8s集群管理（kubeconfig加密存储，中心化管理）
2. 故障检测引擎（Watch Pod/Node事件，5分钟去重）
3. AI智能诊断（多模型适配+降级链+多模型协同仲裁）
4. AI修复方案生成（操作类型识别+风险评估）
5. 决策引擎（操作类型+时间策略双维度，查询自动/增删改人工确认）
6. 审批工作流（钉钉/企微交互式卡片，单人/双人审批，超时自动升级）
7. 自动修复执行（灰度策略+熔断控制+自动回滚）
8. AI修复验证（修复前后快照对比）
9. 全链路审计（SHA256哈希链防篡改）
10. 知识库（向量嵌入+RAG检索）
11. 批量处理（跨集群并行/滚动/分批执行）
12. 定制化定时任务（每集群独立Cron配置）
13. 资源配额可见性（定时采集，非实时监控）
14. 权限管理（admin/operator/viewer三角色RBAC）
15. 通知服务（钉钉/企业微信，severity路由，失败重试）

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
│   │   │   └── webhooks.py              # 钉钉/企微回调API
│   │   ├── services/                    # 业务逻辑层
│   │   │   ├── __init__.py
│   │   │   ├── approval_service.py      # 审批工作流
│   │   │   ├── notification_service.py  # 通知服务
│   │   │   ├── decision_engine.py       # 决策引擎
│   │   │   ├── batch_service.py         # 批量处理
│   │   │   ├── audit_service.py         # 审计服务
│   │   │   ├── scheduler_service.py     # 定时调度
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
│   │   │   ├── redis_client.py          # Redis连接
│   │   │   ├── security.py              # 加密/JWT
│   │   │   └── logging.py               # 日志
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
│   │   │   ├── pod_watcher.go           # Pod事件监听
│   │   │   ├── node_watcher.go          # Node事件监听
│   │   │   └── manager.go               # Watch管理器
│   │   ├── executor/
│   │   │   ├── execute.go               # 命令执行
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
│   │   │   └── manager.go               # 多集群客户端池
│   │   ├── database/
│   │   │   ├── db.go                    # GORM连接
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
│   │   │   ├── model_adapter.py         # 统一模型适配器
│   │   │   ├── model_openai.py          # OpenAI适配器
│   │   │   ├── model_deepseek.py        # DeepSeek适配器
│   │   │   ├── model_claude.py          # Claude适配器
│   │   │   ├── prompt_engine.py         # 提示词引擎
│   │   │   ├── diagnosis_service.py     # 诊断服务
│   │   │   ├── plan_service.py          # 方案生成服务
│   │   │   ├── verification_service.py  # 验证服务
│   │   │   ├── multi_model_service.py   # 多模型协同
│   │   │   ├── embedding_service.py     # 向量嵌入
│   │   │   └── rag_service.py           # RAG检索
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
│   │   │   ├── ExecutionDetail.tsx
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
│   │   │   ├── api.ts                   # API调用封装
│   │   │   └── websocket.ts             # WebSocket封装
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
│   ├── configmap-ai-service.yaml
│   ├── secret.yaml
│   ├── deployment-api-server.yaml
│   ├── deployment-k8s-executor.yaml
│   ├── deployment-ai-service.yaml
│   ├── deployment-frontend.yaml
│   ├── service-api-server.yaml
│   ├── service-k8s-executor.yaml
│   ├── service-ai-service.yaml
│   ├── service-frontend.yaml
│   ├── ingress.yaml
│   ├── hpa-api-server.yaml
│   ├── hpa-k8s-executor.yaml
│   ├── hpa-ai-service.yaml
│   ├── pdb.yaml
│   ├── networkpolicy.yaml
│   └── backup-cronjob.yaml
│
├── docs/
│   ├── ARCHITECTURE.md
│   ├── API.md
│   └── DEPLOYMENT.md
│
├── docker-compose.yml
├── Makefile
└── README.md

现在开始按以下8个模块依次开发。每个模块完成后继续下一个模块，不需要停下确认。


═══════════════════════════════════════════════════════════════
模块1：api-server 数据库模型 + 核心基础设施
═══════════════════════════════════════════════════════════════

【1.1 api-server/requirements.txt】
包含：
fastapi==0.115.0
uvicorn[standard]==0.30.6
sqlalchemy[asyncio]==2.0.35
asyncpg==0.29.0
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
- DATABASE_URL: str（PostgreSQL连接串）
- REDIS_URL: str
- JWT_SECRET_KEY: ***
- JWT_ALGORITHM: str = "HS256"
- JWT_EXPIRE_HOURS: int = 24
- ENCRYPTION_KEY: str（32…4编码）
- K8S_EXECUTOR_GRPC_ADDR: str（默认localhost:9090）
- AI_SERVICE_HTTP_ADDR: str（默认http://localhost:9000）
- DINGTALK_WEBHOOK: str = ""
- WECHAT_WEBHOOK: str = ""
- LOG_LEVEL: str = "INFO"
- Settings类使用@lru_cache缓存单例

【1.3 api-server/app/core/database.py】
- SQLAlchemy 2.0异步引擎
- engine = create_async_engine(DATABASE_URL, echo=False, pool_size=20, max_overflow=40)
- async_session = async_sessionmaker(engine, expire_on_commit=False)
- Base = DeclarativeBase
- get_db()依赖：yield session，finally close

【1.4 api-server/app/core/security.py】
实现以下函数：
- encrypt_aes256_gcm(plaintext: str) -> str：使用ENCRYPTION_KEY加密，返回base64(IV+ciphertext+tag)
- decrypt_aes256_gcm(ciphertext_b64: str) -> str：解密
- hash_password(password: str) -> str：bcrypt哈希
- verify_password(plain, hashed) -> bool
- create_jwt(user_id: int, username: str, role: str) -> str：生成JWT，exp=now+JWT_EXPIRE_HOURS
- decode_jwt(token: str) -> dict：验证并解析，失败抛异常
- generate_api_key() -> str：secrets.token_urlsafe(32)
- generate_trace_id() -> str：uuid4().hex
- generate_decision_id() -> str：uuid4().hex

【1.5 api-server/app/core/logging.py】
- 使用Python标准logging
- format: "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
- get_logger(name)函数

【1.6 api-server/app/core/redis_client.py】
- redis.asyncio连接
- get_redis()依赖

【1.7 api-server/app/models/所有模型文件】

每个模型文件包含完整的SQLAlchemy定义。所有模型继承Base。使用Mapped和mapped_column。

具体字段：
Cluster: id(Integer PK), name(String 100 unique not null), description(String 500 default ""), kubeconfig_encrypted(Text not null), status(String 20 default "unknown"), version(String 50 default ""), created_at(DateTime default now), updated_at(DateTime onupdate now)

Alert: id(Integer PK), cluster_id(Integer index not null), resource_type(String 10 not null), resource_name(String 200 not null), namespace(String 100 default ""), fault_type(String 50 not null), severity(String 10 not null), status(String 20 default "pending"), message(Text), detected_at(DateTime default now index), resolved_at(DateTime nullable)

Task: id(Integer PK), alert_id(Integer index not null), assignee(String 50 default ""), status(String 20 default "pending" index), resolution(Text default ""), created_at(DateTime default now), updated_at(DateTime onupdate now)

TaskAuditLog: id(Integer PK), task_id(Integer index not null), action(String 50 not null), old_status(String 20 default ""), new_status(String 20 not null), operator(String 50 default ""), comment(String 500 default ""), created_at(DateTime default now)

Approval: id(Integer PK), plan_id(Integer index not null), required_level(Integer not null), status(String 20 default "pending" index), approvers(Text default "[]"), approved_by(Text default "[]"), rejected_by(String 50 default ""), comments(Text default ""), created_at(DateTime default now), decided_at(DateTime nullable)

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

KnowledgeEntry: id(Integer PK), fault_type(String 50 index not null), root_cause(Text), repair_steps(Text), cluster_info(String 200 default ""), tags(String 500 default ""), success_count(Integer default 0), usage_count(Integer default 0), vector_embedding(LargeBinary nullable), created_at(DateTime default now), updated_at(DateTime onupdate now)

AuditRecord: id(Integer PK), decision_id(String 64 index not null), trace_id(String 64 index not null), trigger_snapshot(Text default ""), ai_call_detail(Text default ""), decision_detail(Text default ""), execution_detail(Text default ""), verification_detail(Text default ""), hash_value(String 64 not null), previous_hash(String 64 default ""), created_at(DateTime default now)

User: id(Integer PK), username(String 50 unique not null), password_hash(String 255 not null), email(String 100 default ""), role(String 20 default "viewer"), is_active(Boolean default True), api_key(String 64 default ""), created_at(DateTime default now), updated_at(DateTime onupdate now)

Role: id(Integer PK), name(String 50 unique not null), description(String 200 default ""), permissions(Text default "[]")

AIModelConfig: id(Integer PK), provider(String 20 not null), model_name(String 100 not null), api_key_encrypted(Text default ""), api_base(String 300 default ""), max_tokens(Integer default 4096), temperature(Float default 0.7), priority(Integer default 100), is_active(Boolean default True), cost_per_1k_tokens(Float default 0.0), created_at(DateTime default now), updated_at(DateTime onupdate now)

AICallLog: id(Integer PK), model_config_id(Integer index), request_type(String 30 default ""), system_prompt(Text default ""), user_prompt(Text default ""), response_content(Text default ""), token_usage(Integer default 0), latency_ms(Integer default 0), cost(Float default 0.0), status(String 20 default "success"), error(Text default ""), created_at(DateTime default now index)

PromptTemplate: id(Integer PK), name(String 100 unique not null), category(String 30 not null), system_prompt(Text not null), user_prompt_template(Text not null), variables(String 500 default "[]"), version(Integer default 1), is_active(Boolean default True), created_at(DateTime default now), updated_at(DateTime onupdate now)

PromptVersion: id(Integer PK), template_id(Integer index not null), version(Integer not null), system_prompt(Text), user_prompt_template(Text), changelog(String 500 default ""), created_at(DateTime default now)

AIDiagnosisResult: id(Integer PK), alert_id(Integer index not null), model_config_id(Integer nullable), template_id(Integer nullable), context_snapshot(Text default ""), diagnosis_json(Text default ""), confidence(Float default 0.0), is_useful(Boolean nullable), created_at(DateTime default now)

RepairPlan: id(Integer PK), alert_id(Integer index not null), diagnosis_id(Integer nullable), plan_json(Text not null), operation_types(String 100 default ""), risk_level(String 20 default "medium"), status(String 20 default "pending" index), created_at(DateTime default now)

ExecutionLog: id(Integer PK), plan_id(Integer index not null), step_index(Integer not null), command(Text not null), status(String 20 default "running"), output(Text default ""), started_at(DateTime default now), completed_at(DateTime nullable)

RepairVerification: id(Integer PK), plan_id(Integer index not null), before_snapshot(Text default ""), after_snapshot(Text default ""), metrics_comparison(Text default ""), ai_verdict(String 30 default ""), status(String 20 default "pending"), created_at(DateTime default now)

AIEffectivenessStat: id(Integer PK), date(Date index not null), model_config_id(Integer nullable), fault_type(String 50 default ""), total_diagnoses(Integer default 0), correct_diagnoses(Integer default 0), total_repairs(Integer default 0), successful_repairs(Integer default 0), avg_mttr_seconds(Integer default 0), total_cost(Float default 0.0), created_at(DateTime default now)

ModelAccuracyStat: id(Integer PK), model_config_id(Integer index not null), fault_type(String 50 default ""), total_attempts(Integer default 0), success_count(Integer default 0), accuracy_rate(Float default 0.0), updated_at(DateTime onupdate now)

【1.8 api-server/app/models/__init__.py】
导入所有模型，确保Base.metadata包含所有表。

【1.9 api-server/app/main.py】
- FastAPI(title="API-OPS平台", version="1.0.0")
- 加载CORS中间件（允许所有来源）
- 异常处理器（统一返回{code, message, data}格式）
- 请求日志中间件
- 健康检查：GET /health
- 启动事件：Base.metadata.create_all自动建表
- 启动事件：初始化默认数据（决策策略4条、默认用户3个）
- 启动事件：注册APScheduler定时任务
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
- gorm.io/driver/postgres
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

所有message定义完整，包含：cluster_id、namespace、command、plan_id、step_index、step_command、rollback_command、script_content、parameters、success、message、output等字段。

【2.3 k8s-executor/internal/config/config.go】
- Viper读取配置
- 支持环境变量覆盖
- DATABASE_URL、GRPC_PORT(默认9090)、LOG_LEVEL

【2.4 k8s-executor/internal/database/models.go】
GORM结构体（与api-server的SQLAlchemy模型对应）：
- Cluster: ID, Name, Description, KubeconfigEncrypted, Status, Version, CreatedAt, UpdatedAt
- Alert: ID, ClusterID, ResourceType, ResourceName, Namespace, FaultType, Severity, Status, Message, DetectedAt, ResolvedAt
- ExecutionLog: ID, PlanID, StepIndex, Command, Status, Output, StartedAt, CompletedAt
- ResourceSnapshot: ID, ClusterID, Namespace, ResourceType, ResourceName, Status, Replicas, ReadyReplicas, ContainerCount, ImageVersion, CPUUsage, MemoryUsage, CreatedAt, SnapshotTime
- QuotaSnapshot: ID, ClusterID, Namespace, CPULimit, CPUUsed, MemoryLimit, MemoryUsed, PodLimit, PodUsed, PVCLimit, PVCUsed, ServiceLimit, ServiceUsed, SnapshotTime
- CustomTask: ID, ClusterID, Name, TaskType, TargetResource, TargetNamespace, CronExpression, ScriptContent, Parameters, IsEnabled, LastRunAt, LastRunStatus, CreatedAt, UpdatedAt
- CustomTaskLog: ID, TaskID, ClusterID, StartTime, EndTime, Status, Output, ErrorMessage

表名与api-server保持一致（复数形式：clusters、alerts、execution_logs、resource_snapshots、quota_snapshots、custom_tasks、custom_task_logs）。

【2.5 k8s-executor/internal/database/db.go】
- GORM初始化连接PostgreSQL
- AutoMigrate所有模型
- GetDB()

【2.6 k8s-executor/internal/k8sclient/manager.go】
- ClusterClient结构体：ID uint, Clientset *kubernetes.Clientset
- NewClusterClient(clusterID, kubeconfigJSON)：解析kubeconfig创建clientset
- Manager结构体：mu sync.RWMutex, clients map[uint]*ClusterClient
- GetClient(clusterID)：从缓存获取，不存在则从数据库加载kubeconfig后创建
- RemoveClient(clusterID)
- ListClients()
- DecryptKubeconfig(encrypted)：使用AES-256-GCM解密（Go实现，与Python端算法一致）

【2.7 k8s-executor/internal/watcher/pod_watcher.go】
- PodWatcher：clusterID, clientset, ctx, cancel
- Start()：使用client-go的Watch接口监听所有namespace的Pod事件
- handlePodEvent(event)：
  解析Pod状态
  检测CrashLoopBackOff（containerStatuses.waiting.reason）→ high
  检测ImagePullBackOff/ErrImagePull → medium
  检测OOMKilled（lastState.terminated.reason）→ high
  检测Pending超过5分钟（phase=Pending且创建时间>5分钟）→ medium
- deduplicateCheck(alert)：查询数据库5分钟内是否有相同告警
- createAlert(alert)：写入alerts表
- message格式："Pod xxx in namespace xxx is in CrashLoopBackOff state. Container xxx: reason=CrashLoopBackOff"

【2.8 k8s-executor/internal/watcher/node_watcher.go】
- NodeWatcher：clusterID, clientset, ctx, cancel
- Start()：Watch Node事件
- 检测NotReady（Ready condition=False/Unknown）→ critical
- 检测MemoryPressure/DiskPressure/PIDPressure（condition=True）→ high

【2.9 k8s-executor/internal/watcher/manager.go】
- WatchManager：mu, watchers map[uint]*ClusterWatchers
- ClusterWatchers：podWatcher, nodeWatcher
- StartClusterWatch(clusterID)：创建client、启动两个watcher goroutine
- StopClusterWatch(clusterID)：cancel context
- StartAllWatches()：查询所有集群，逐个启动
- RestartClusterWatch(clusterID)：停止后重新启动

【2.10 k8s-executor/internal/executor/execute.go】
- ExecuteCommand(clusterID, namespace, command)：使用clientset执行命令，返回输出
- IdempotencyCheck(clusterID, command)：解析命令，检查资源是否已处于目标状态
- SafetyCheck(command)：检查包含rm -rf、drop、format、purge等危险词返回false
- BlastRadiusEvaluate(clusterID, planJSON)：统计影响的Pod数量

【2.11 k8s-executor/internal/executor/gray_release.go】
- GrayExecutor：clusterID, clientset
- ExecuteWithGrayStrategy(plan, notifyFn)：
  解析planJSON获取steps
  评估blastRadius
  ≤1个Pod：直接执行所有步骤
  2-10个Pod：执行step[0]→notifyFn(等待30秒)→验证→执行剩余步骤
  >10个Pod：分10批→每批执行后notifyFn(等待60秒)
  每步执行后记录ExecutionLog
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
- ExecuteCommand：单条命令
- ExecutePlan：流式返回，每步执行完推送进度
- RollbackPlan
- HealthCheck
- CollectQuota
- RunScript
- BatchExecute：流式返回每集群结果

【2.18 k8s-executor/cmd/executor/main.go】
初始化顺序：config → zap logger → database → k8sclient.Manager → WatchManager.StartAllWatches → 健康检查调度器 → 配额采集调度器 → gRPC server启动

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
asyncpg==0.29.0
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
- OPENAI_API_KEY、DEEPSEEK_API_KEY、CLAUDE_API_KEY
- EMBEDDING_MODEL（默认sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2）
- LOG_LEVEL

【3.3 ai-service/app/core/database.py】
- 与api-server相同的SQLAlchemy异步引擎配置
- Base = DeclarativeBase（独立Base，但表结构相同）

【3.4 ai-service/app/models/】
AI相关模型定义（与api-server的对应模型字段一致）：
- AIModelConfig
- AICallLog
- PromptTemplate
- PromptVersion
- AIDiagnosisResult
- RepairPlan
- RepairVerification
- AIEffectivenessStat
- ModelAccuracyStat
- KnowledgeEntry

【3.5 ai-service/app/main.py】
- FastAPI(title="AI-Service")
- 健康检查
- 启动时自动建表
- 启动时调用SeedDefaultTemplates()

【3.6 ai-service/app/services/model_adapter.py】
- AIResponse类（dataclass）：content, token_usage, cost, model_name, provider
- BaseAIModelAdapter抽象类：
  async chat_completion(self, system_prompt: str, user_prompt: str) -> AIResponse
- ModelFactory：
  get_adapter(config: AIModelConfig) -> BaseAIModelAdapter
  根据provider返回OpenAIAdapter/DeepSeekAdapter/ClaudeAdapter
- call_with_fallback(system_prompt, user_prompt, request_type) -> tuple[AIResponse, str]：
  查询所有is_active=True的模型配置，按priority升序
  依次调用，失败切换下一个
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
user_prompt_template: "请诊断以下Pod崩溃问题：\n集群名称：{cluster_name}\n命名空间：{namespace}\nPod名称：{pod_name}\n重启次数：{restart_count}\n状态信息：{status_info}\n最近日志：\n{recent_logs}"

模板2：node_pressure_diagnosis（category=diagnosis）
system_prompt: "你是一个Kubernetes节点故障诊断专家。请分析以下节点异常并返回JSON格式的诊断结果。JSON必须包含字段：root_cause、suggestions、risk_level、confidence。"
user_prompt_template: "请诊断以下节点异常：\n集群名称：{cluster_name}\n节点名称：{node_name}\n异常类型：{fault_type}\n节点Conditions：{conditions}\n最近事件：{recent_events}"

模板3：repair_plan_generation（category=repair_plan）
system_prompt: "你是一个Kubernetes自动化运维专家。请基于诊断结果生成安全的修复方案。返回JSON格式，包含steps数组。每个step必须包含：command（kubectl命令）、description（步骤描述）、expected_result（预期结果）、rollback_command（回滚命令）。"
user_prompt_template: "诊断结果：\n{diagnosis_result}\n\n集群名称：{cluster_name}\n资源信息：{resource_info}\n\n请生成修复方案。"

模板4：risk_assessment（category=risk_assessment）
system_prompt: "你是风险评估专家。请评估修复方案的风险。返回JSON格式，包含risk_level（low/medium/high/critical）、blast_radius（影响范围描述）、data_loss_risk（布尔值）、mitigations（缓解措施数组）。"
user_prompt_template: "修复方案：\n{repair_plan}\n\n目标资源：{target_resource}"

模板5：repair_verification（category=verification）
system_prompt: "你是Kubernetes修复验证专家。请对比修复前后的状态判断修复是否成功。返回JSON格式，包含verdict（healed/degraded/unchanged/worsened四选一）、reason（判断理由）、follow_up（后续建议数组）。"
user_prompt_template: "修复前快照：\n{before_snapshot}\n\n修复后快照：\n{after_snapshot}\n\n指标对比：\n{metrics_comparison}"

【3.11 ai-service/app/services/diagnosis_service.py】
- DiagnoseAlert(alert_data: dict, context_data: dict)：
  根据alert_data.fault_type匹配模板（CrashLoopBackOff/ImagePullBackOff/OOMKilled/PendingTimeout→pod_crash_diagnosis；NodeNotReady/MemoryPressure/DiskPressure/PIDPressure→node_pressure_diagnosis）
  渲染提示词
  调用call_with_fallback(request_type="diagnosis")
  解析JSON（root_cause、suggestions、risk_level、confidence）
  存入ai_diagnosis_results表
  返回{diagnosis_id, root_cause, suggestions, risk_level, confidence, model_used}

- MultiDiagnose(alert_data, context_data)：
  获取所有活跃模型（最多3个）
  asyncio.gather并发调用
  收集每个模型的分析结果
  提取共识root_cause（出现次数最多的根因）
  冲突仲裁：无共识时查询model_accuracy_stats选择准确率最高模型的结论
  更新model_accuracy_stats（total_attempts+1，成功则success_count+1，重新计算accuracy_rate）
  返回{consensus_root_cause, model_analyses, recommended_action, confidence_score}

【3.12 ai-service/app/services/plan_service.py】
- GeneratePlan(diagnosis_data, alert_data)：
  调用repair_plan_generation模板
  渲染（diagnosis_result=诊断JSON，cluster_name，resource_info）
  call_with_fallback(request_type="repair_plan")
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
    最终：low/medium/high/critical（high+2=critical）
  存入repair_plans表（operation_types=逗号分隔的操作类型列表）
  返回{plan_id, steps, operation_types, risk_level}

【3.13 ai-service/app/services/verification_service.py】
- VerifyRepair(plan_id, before_snapshot, after_snapshot, metrics_comparison)：
  调用repair_verification模板
  call_with_fallback(request_type="verification")
  解析verdict/reason/follow_up
  存入repair_verifications表
  返回{verdict, reason, follow_up}

【3.14 ai-service/app/services/embedding_service.py】
- EmbeddingService类：
  _model = None（懒加载）
  get_model()：首次调用时加载SentenceTransformer(EMBEDDING_MODEL)
  embed(text)：返回768维float列表
  batch_embed(texts)：批量

【3.15 ai-service/app/services/rag_service.py】
- SearchSimilar(query, top_k=5)：
  query_embedding = embedding_service.embed(query)
  从knowledge_entries表读取所有条目（排除vector_embedding为NULL的）
  计算余弦相似度
  返回top_k个{id, fault_type, root_cause, repair_steps, similarity}
- AddKnowledge(fault_type, root_cause, repair_steps, tags)：
  text = root_cause + " " + repair_steps
  embedding = embedding_service.embed(text)
  bytes_embedding = np.array(embedding, dtype=np.float32).tobytes()
  存入knowledge_entries表
- InjectSimilarCases(alert_data, top_k=3)：
  调用SearchSimilar
  将结果格式化为文本段落
  返回可注入诊断提示词的上下文文本

【3.16 ai-service/app/api/下所有接口】

diagnosis.py:
- POST /api/v1/ai/diagnose
  请求体：{alert_id, alert_data: {cluster_name, namespace, pod_name/node_name, fault_type, restart_count, status_info, recent_logs, conditions, recent_events}, context_data: {}}
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
- POST /api/v1/ai/models：创建模型配置
- POST /api/v1/ai/models/{id}/test：测试连通性
- GET /api/v1/ai/logs：AI调用日志
- GET /api/v1/ai/model-stats：模型准确率统计

embedding.py:
- POST /api/v1/ai/embed：{text}→{embedding}
- POST /api/v1/ai/embed-batch：{texts}→{embeddings}

rag.py:
- POST /api/v1/ai/rag/search：{query, top_k}→{results}
- POST /api/v1/ai/rag/add：{fault_type, root_cause, repair_steps, tags}→{knowledge_id}

【3.17 ai-service/Dockerfile】
python:3.11-slim基础镜像
安装依赖
注意sentence-transformers需要额外系统依赖（libgomp1）


═══════════════════════════════════════════════════════════════
模块4：api-server 完整实现
═══════════════════════════════════════════════════════════════

【4.1 api-server/app/clients/executor_client.py】
ExecutorClient类：
- __init__：创建gRPC channel连接K8S_EXECUTOR_GRPC_ADDR
- start_watch(cluster_id)：调用StartWatch
- stop_watch(cluster_id)：调用StopWatch
- execute_command(cluster_id, namespace, command)：单条命令
- execute_plan(cluster_id, plan_id, steps)：流式接收执行进度
- rollback_plan(cluster_id, plan_id, steps)：回滚
- health_check(cluster_id)：健康检查
- collect_quota(cluster_id)：配额采集
- run_script(cluster_id, script_content, parameters)：脚本执行
- batch_execute(cluster_ids, command)：批量执行

每个方法包含：超时控制（30秒默认）、错误处理、日志记录

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
- 所有方法使用httpx.AsyncClient

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
  请求：{name, description, kubeconfig}
  逻辑：
    1. 检查name是否已存在
    2. encrypt_aes256_gcm(kubeconfig)
    3. 创建Cluster记录（status=unknown）
    4. 调用executor_client.health_check验证连通性
    5. 连通→更新status=healthy+version→调用executor_client.start_watch启动Watch
    6. 不连通→更新status=unreachable→仍然创建但标记不可达
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
    2. 调用executor_client获取Pod/Node状态和日志作为context_data
    3. 调用ai_client.diagnose
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
  buttons格式：[{title, actionURL}]
- send_wechat_text(webhook, content, mentioned_list)
- send_wechat_markdown(webhook, content)
- send_alert_notification(alert)：
  查询所有is_active的NotificationConfig
  severity=critical：发所有渠道
  severity=high：发钉钉+企微
  severity=medium：仅钉钉
  severity=low：不发送
  失败重试3次（间隔2s/4s/8s）
  记录NotificationLog
- send_approval_notification(approval, plan)：
  发送ActionCard
  title: "【审批】修复方案待审批"
  text: 包含故障信息、修复方案摘要、风险等级
  buttons: [{title:"✅ 批准", actionURL: f"http://API_HOST/api/v1/approvals/{id}/callback?action=approve&user_id={user_id}"}, {title:"❌ 拒绝", actionURL: f"...action=reject..."}]
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
  发送审批通知（钉钉ActionCard）
- approve(approval_id, user_id, comment)：
  检查状态是否pending
  单人：直接approved
  双人：approved_by追加user_id，2人后approved
  更新DecidedAt
  审批通过后自动触发执行修复
- reject(approval_id, user_id, comment)：
  任一人拒绝→rejected
- timeout_handler()：
  APScheduler每分钟检查
  created_at + approval_timeout < now 且 status=pending
  → status=timeout
  → 通知升级
- 审批通过后触发execute_approved_plan(approval)：
  更新RepairPlan status=executing
  调用executor_client.execute_plan（流式接收进度）
  执行完成后更新RepairPlan status=completed/failed

【4.12 api-server/app/api/approvals.py】
- POST /api/v1/approvals（require_operator）：{plan_id}→创建审批
- GET /api/v1/approvals（require_any_user）：列表，?status=pending筛选
- GET /api/v1/approvals/{id}（require_any_user）：详情
- POST /api/v1/approvals/{id}/callback（无需认证，因为从钉钉/企微回调）
  参数：action=approve|reject, user_id, comment
  调用approval_service.approve/reject
  返回HTML页面（钉钉ActionCard跳转后展示结果）

【4.13 api-server/app/api/webhooks.py】
- POST /api/v1/webhooks/dingtalk/callback：
  接收钉钉POST回调
  解析approval_id和action
  调用approval_service处理
- POST /api/v1/webhooks/wechat/callback：同上

【4.14 api-server/app/services/batch_service.py】
- create_batch_operation(name, alert_ids, plan_id, strategy, failure_policy, batch_size)：
  根据alert_ids查询告警→确定集群列表
  创建BatchOperation（total_clusters=去重后的集群数）
  为每个集群创建BatchOperationItem
- execute_batch(batch_operation_id)：
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
  add_job(execute_task, trigger, args=[custom_task.id], id=f"custom_task_{id}")
- unregister_task(task_id)
- execute_task(task_id)：
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

【4.22 api-server/app/api/knowledge.py】
- GET /api/v1/knowledge/search?q=xxx&top_k=5（require_any_user）：
  调用ai_client.search_similar
- GET /api/v1/knowledge（require_any_user）：列表
- POST /api/v1/knowledge（require_operator）：
  调用ai_client.add_knowledge
- DELETE /api/v1/knowledge/{id}（require_admin）

【4.23 api-server/app/api/auth.py】
- POST /api/v1/auth/login：
  验证用户名密码
  返回{token, user: {id, username, role, email}}
- GET /api/v1/auth/me（require_any_user）：当前用户信息
- POST /api/v1/auth/logout（require_any_user）：Token加入Redis黑名单

【4.24 api-server/app/api/users.py】
- GET /api/v1/users（require_admin）
- POST /api/v1/users（require_admin）：{username, password, email, role}
- PUT /api/v1/users/{id}（require_admin）：更新角色/邮箱/状态
- DELETE /api/v1/users/{id}（require_admin）
- POST /api/v1/users/{id}/api-key（require_admin）：重新生成API Key

【4.25 api-server/app/api/notifications.py】
- GET /api/v1/notifications/configs（require_any_user）
- POST /api/v1/notifications/configs（require_admin）
- DELETE /api/v1/notifications/configs/{id}（require_admin）
- GET /api/v1/notifications/logs（require_any_user）

【4.26 api-server/app/main.py 完整启动逻辑】
1. 创建FastAPI应用
2. 注册中间件
3. 注册所有路由
4. 启动事件：
   - Base.metadata.create_all建表
   - seed_default_policies()插入默认决策策略
   - seed_default_users()创建admin/operator/viewer三个用户
   - 调用ai_client确保预置提示词模板已创建
   - scheduler_service.load_all_tasks()+start()
   - 启动审批超时检查定时任务（每分钟）

【4.27 api-server/Dockerfile】
python:3.11-slim
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080


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
  getApprovals, getApproval, createApproval, approveApproval, rejectApproval
  executePlan, getExecutionStatus
  getBatchList, getBatchDetail, createBatch, executeBatch
  getCustomTasks, createCustomTask, updateCustomTask, deleteCustomTask, toggleCustomTask, runCustomTaskNow, getCustomTaskLogs
  getResourceOverview, getNamespaces, getNamespaceQuota, getNamespacePods, getNamespaceDeployments, triggerCollect
  searchKnowledge, getKnowledgeList, addKnowledge, deleteKnowledge
  getAuditTrace, searchAudit, getAuditReport, verifyAuditChain
  getStatsOverview, getAIEffectiveness, getRepairSuccessRate
  getUsers, createUser, updateUser, deleteUser, regenerateAPIKey

【5.3 frontend/src/services/websocket.ts】
- 连接WebSocket：ws://{host}/ws/executions/{planId}
- onMessage回调
- 自动重连（3次）
- close

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
- healthy/success/resolved/approved/completed → green
- unreachable/failed/rejected → red
- pending/acknowledged/running → blue
- in_progress/executing → orange
- timeout/skipped → gray
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
- Table：名称/描述/状态Tag/版本/创建时间/操作
- 操作列：健康检查按钮/删除按钮（Popconfirm）
- 添加按钮→Modal：name/description/kubeconfig文件上传（Upload组件）
- 删除调用deleteCluster

AlertList.tsx：
- 筛选区：集群Select/严重级别Select/状态Select/故障类型Select/日期RangePicker
- Table：集群/资源类型/资源名/命名空间/故障类型/SeverityTag/StatusTag/时间/操作
- 行展开：展示AI诊断结果（如果有）——根因/建议/置信度Progress/风险Tag
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
- 卡片列表：Provider/ModelName/Priority/成本参数/状态
- 添加Modal：provider选择(openai/deepseek/claude)/model_name/api_key/max_tokens/temperature/priority/cost_per_1k_tokens
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
- 待审批：卡片列表（方案摘要/操作类型Tag/风险Tag/发起时间/批准/拒绝按钮）
- 审批操作Modal：意见输入
- 已处理：Table（状态Tag/审批人/决定时间/意见）

ExecutionDetail.tsx：
- Steps组件展示执行步骤
- 当前步骤高亮（根据WebSocket推送）
- 实时日志面板（黑色背景滚动日志）
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
- 知识卡片列表：故障类型Tag/根因摘要/修复步骤/成功率/使用次数/相似度

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
- Table：用户名/邮箱/角色Tag/状态/创建时间
- 创建Modal：username/password/email/role
- 角色编辑Select
- API Key查看/重新生成按钮

【5.10 frontend/Dockerfile】
node:20-alpine → 构建 → nginx:alpine
nginx配置：/api/v1反向代理到api-server Service


═══════════════════════════════════════════════════════════════
模块6：部署配置
═══════════════════════════════════════════════════════════════

【6.1 docker-compose.yml】
services:
  postgres:
    image: postgres:15-alpine
    environment: POSTGRES_USER=apiops, POSTGRES_PASSWORD=*** POSTGRES_DB=api_ops
    volumes: postgres_data:/var/lib/postgresql/data
    ports: "5432:5432"
  redis:
    image: redis:7-alpine
    ports: "6379:6379"
  api-server:
    build: ./api-server
    environment: DATABASE_URL=postgresql+asyncpg://apiops:apiops123@postgres:5432/api_ops, REDIS_URL=redis://redis:6379/0, K8S_EXECUTOR_GRPC_ADDR=k8s-executor:9090, AI_SERVICE_HTTP_ADDR=http://ai-service:9000
    ports: "8080:8080"
    depends_on: [postgres, redis]
  k8s-executor:
    build: ./k8s-executor
    environment: DATABASE_URL=postgresql://apiops:***@postgres:5432/api_ops, GRPC_PORT=9090
    ports: "9090:9090"
    depends_on: [postgres]
  ai-service:
    build: ./ai-service
    environment: DATABASE_URL=postgresql+asyncpg://apiops:apiops123@postgres:5432/api_ops, OPENAI_API_KEY=*** DEEPSEEK_API_KEY=*** CLAUDE_API_KEY=
    *** "9000:9000"
    depends_on: [postgres]
  frontend:
    build: ./frontend
    ports: "80:80"
    depends_on: [api-server]
volumes: postgres_data

【6.2 k8s/下所有清单文件】
- namespace.yaml：api-ops
- configmap-api-server.yaml：DATABASE_URL、REDIS_URL、K8S_EXECUTOR_GRPC_ADDR、AI_SERVICE_HTTP_ADDR
- configmap-k8s-executor.yaml：DATABASE_URL、GRPC_PORT
- configmap-ai-service.yaml：DATABASE_URL、EMBEDDING_MODEL
- secret.yaml：JWT_SECRET_KEY、ENCRYPTION_KEY、OPENAI_API_KEY、DEEPSEEK_API_KEY、CLAUDE_API_KEY（base64）
- deployment-api-server.yaml：3副本、podAntiAffinity（topologyKey: kubernetes.io/hostname）、livenessProbe和readinessProbe（HTTP GET /health）、resources limits/requests
- deployment-k8s-executor.yaml：2副本、反亲和
- deployment-ai-service.yaml：2副本、反亲和
- deployment-frontend.yaml：2副本
- service-api-server.yaml：ClusterIP:8080
- service-k8s-executor.yaml：ClusterIP:9090
- service-ai-service.yaml：ClusterIP:9000
- service-frontend.yaml：ClusterIP:80
- ingress.yaml：/ → frontend:80，/api/v1 → api-server:8080
- hpa-api-server.yaml：minReplicas=3, maxReplicas=5, targetCPUUtilizationPercentage=70
- hpa-k8s-executor.yaml：minReplicas=2, maxReplicas=4
- hpa-ai-service.yaml：minReplicas=2, maxReplicas=4
- pdb.yaml：api-server minAvailable=2
- networkpolicy.yaml：ai-service和k8s-executor只允许api-server命名空间的入站
- backup-cronjob.yaml：每日2:00 pg_dump到对象存储

【6.3 Makefile】
目标：build/up/down/logs/migrate/seed/status

【6.4 README.md】
包含：项目简介、架构图（ASCII）、快速开始、环境变量说明、默认账号

【6.5 docs/ARCHITECTURE.md】
三服务架构详解、服务间通信、数据流、部署拓扑

【6.6 docs/API.md】
所有API端点列表+请求/响应示例

【6.7 docs/DEPLOYMENT.md】
Docker Compose部署步骤+K8s部署步骤+备份恢复


═══════════════════════════════════════════════════════════════
最终要求
═══════════════════════════════════════════════════════════════

1. 所有代码有中文注释（注释说明该代码块的功能）
2. 所有API统一响应格式：{code: 200, message: "success", data: {...}}
3. 错误响应：{code: 4xx/5xx, message: "错误描述", data: null}
4. 三个服务共享同一个PostgreSQL数据库，表名一致
5. api-server是唯一对外入口，前端只和api-server通信
6. k8s-executor只接收api-server的gRPC调用
7. ai-service只接收api-server的HTTP调用
8. 审计记录在关键节点自动写入
9. 数据库模型字段与本文档定义完全一致
10. Go代码使用zap日志，Python代码使用logging
11. 所有外部API调用（AI模型/钉钉/企微）使用httpx异步
12. 代码可直接编译运行

请开始开发。按模块顺序逐个完成，不要跳过任何模块，不要精简任何功能。


═══════════════════════════════════════════════════════════════
【优化修正 V1.3】（2026-08-17 老板确认，优先级高于原提示词，冲突处以本修正为准）
═══════════════════════════════════════════════════════════════

1. 【调度防重复：Redis 分布式锁】（V1.2 更新，替代原“数据库原子更新”方案）
   原数据库条件更新方案存在锁表/锁行风险（长事务锁行拖累其他副本），改用 Redis 分布式锁，不碰数据库锁。
   - 锁实现：`SET lock_key lock_value NX PX 30000`（30 秒过期），返回 OK 的副本才执行；Redis 单实例即可，不需要 Redlock。
   - 锁 key 设计：`lock:approval_timeout`（审批超时检查，全局一把）、`lock:custom_task:{task_id}`（定时任务）、`lock:batch:{batch_id}`（批量执行）。
   - 锁 value：uuid4 唯一标识；释放用 Lua 脚本（GET 比对 value 后 DEL），防止误删其他副本的锁。
   - 自动续期：任务执行超过锁过期时间时定期续期（PEXPIRE），执行结束（含异常）在 finally 中释放。
   - 双保险：审批超时检查在拿锁后仍用条件更新兜底（`UPDATE approvals SET status='timeout' WHERE id=? AND status='pending'`，受影响行数=0 则跳过）。
   - 审批 approve/reject 仍用数据库条件更新防并发（`WHERE status='pending'`），与锁互补。

2. 【删除 WebSocket，执行进度改轮询】
   前端不连接任何 WebSocket 端点，/ws/executions/{planId} 接口不存在。
   - frontend/src/services/websocket.ts 文件删除。
   - ExecutionDetail.tsx 改为轮询：每 3 秒 GET /api/v1/executions/{planId}（或等价的状态查询接口）刷新执行状态和日志；页面提供"刷新"按钮，执行完成后停止轮询。
   - api-server 不实现 WebSocket 端点，无 WebSocket 依赖。
   - 轮询接口设计为增量拉取：`GET /api/v1/executions/{planId}?since_step=3` 只返回新步骤和增量日志，避免每次全量拖日志。
   - 执行进度必须落库（ExecutionLog 表实时写入），轮询从数据库读，天然支持 api-server 多副本与重启。
   - 前端用 visibilitychange 在后台 tab 暂停轮询，切回时恢复。

3. 【embedding 维度修正：768 → 384】
   sentence-transformers 模型 paraphrase-multilingual-MiniLM-L12-v2 实际输出 384 维向量。
   - embedding_service.py 的 embed() 返回 384 维 float 列表，注释注明模型实际维度。
   - 存储格式：np.array(embedding, dtype=np.float32).tobytes()（384 * 4 = 1536 字节）。
   - RAG 相似度计算按 384 维实现；禁止任何写死 768 的代码。
   - 若后续更换模型，维度从模型实际输出读取，不硬编码。
   - 模型离线/内网部署：Dockerfile 预下载模型打进镜像，或挂载模型缓存 volume（HF_HUB_OFFLINE=1），避免冷启动卡在下载。
   - 长期演进：知识库条目超过 10 万后全表扫描算相似度变慢，预留 pgvector 迁移路径（向量存扩展列 + 索引，SQL 直接 `ORDER BY embedding <-> $1 LIMIT k`）。MVP 先全表扫。

4. 【审批回调：验签 + user_id 审计，非公开 URL 跳转】
   审批交互通过钉钉/企微机器人消息回调完成（接收消息、判断意图、响应），不使用可公开访问的 URL 直接批准。
   - webhooks.py 的钉钉/企微回调必须做签名校验（钉钉加签 timestamp+sign、企微签名验证），防止伪造 POST 到 webhook 地址；验签失败返回 403。
   - user_id 从回调中平台返回的真实身份获取（钉钉 userId / 企微 userid），禁止从 URL 参数中读取可篡改的 user_id。
   - 审批记录（Approval.approved_by / rejected_by）必须记录审批者 user_id，便于审计"谁审批的、谁使用的"。
   - 原提示词【4.8】【4.12】中 ActionCard 按钮 URL 直连 callback 的方式废弃，改为机器人消息交互 + 验签回调。
   - 回调幂等：钉钉/企微可能重推同一条消息，用消息 ID（钉钉 eventId / 企微 msgId）去重，防止同一审批被处理两次。
   - user_id 映射表：钉钉 userId / 企微 userid 不是平台 User.id，需映射表（或按邮箱/手机号匹配）转换为平台 user_id 后才执行审批；查不到映射的拒绝处理并转管理员人工确认，默认不放行。
   - 审批动作（approve/reject/超时）本身写入审计链（operator + 时间 + 意见）。

5. 【跨语言加密：增加 Python↔Go 互测】
   kubeconfig 加密（api-server Python 端）与解密（k8s-executor Go 端）必须逐字节兼容，开发时先实现互测用例再写业务代码：
   - 统一格式：AES-256-GCM，随机 12 字节 IV，密文 + 16 字节 tag，base64 编码拼接为 base64(IV + ciphertext + tag)，IV 在前。
   - 新增 docs/ENCRYPTION.md 记录格式约定。
   - 新增互测用例（Python 脚本生成密文 → Go 测试解密；Go 生成 → Python 解密），放入各自测试目录，CI/本地验证必须通过。
   - ENCRYPTION_KEY 为 32 字节（256 位），两端的密钥派生方式必须一致（直接使用 32 字节密钥，不额外派生）。
   - 互测用例做成 Makefile target（`make crypto-test`）或 CI 步骤，改加密代码时一键验证；两端代码注释互相引用 docs/ENCRYPTION.md。

6. 【client-go 确认保留】
   client-go 为官方 Go 客户端库，工作方式即“读取 kubeconfig（证书配置）→ 在 k8s-executor 进程内建立客户端连接集群 API Server”，不需要在被管理集群中部署任何 agent/应用。kubeconfig 加密存储、中心化管理的方案不变。k8s-executor 是部署在平台侧的独立服务，通过 kubeconfig 从外部连接各个集群（同本地 kubectl 配 kubeconfig 连远端集群）。
   - NewClusterClient 解析 kubeconfig 时显式指定 context（多集群 kubeconfig 常有多个 context），禁止默认取 current-context，防止连错集群。
   - 证书过期提示：集群连接失败时区分“证书过期/网络不通/权限不足”，平台侧给出明确提示，不能只显示 authentication failed。

7. 【Prometheus 接入】（V1.2 新增，环境已有 Prometheus）
   目的：增强 AI 诊断上下文（指标趋势），减少自研指标采集工作。
   - ai-service 新增 `app/services/prometheus_client.py`：使用 httpx.AsyncClient 查询 Prometheus HTTP API（`/api/v1/query` 和 `/api/v1/query_range`）。
   - 配置：ai-service 环境变量 `PROMETHEUS_URL`（加入 configmap-ai-service.yaml）。
   - 诊断上下文增强：DiagnoseAlert / GeneratePlan 渲染提示词前，按告警资源类型拉取指标趋势并格式化为文本注入：
     - Pod：`rate(container_cpu_usage_seconds_total{namespace=~"$ns", pod=~"$pod"}[5m])`、`container_memory_working_set_bytes`、`kube_pod_container_status_restarts_total`
     - Node：`node_cpu_seconds_total`、`node_memory_MemAvailable_bytes`、`node_filesystem_avail_bytes`
     - 错误率（如有）：`sum(rate(http_requests_total{status=~"5.."}[5m]))`
   - 降级策略：Prometheus 查询失败不影响主流程——无指标上下文继续诊断，错误记日志；超时 5 秒。
   - 可选增强（后续）：① Prometheus AlertManager webhook 接入作为告警源（Prometheus 告警 → 平台 Alert）；② 资源配额/采集复用 kube-state-metrics 数据（executor 采集作为降级）。

8. 【executor 单副本 watch 防重复】（V1.2 新增）
   executor 是有状态服务（每集群持长连接 watcher），多副本会重复 watch 同一集群导致告警重复/竞态。
   - k8s-executor 部署固定 1 副本（deployment-k8s-executor.yaml replicas: 1），不配置 HPA（hpa-k8s-executor.yaml 删除或禁用）。
   - 若未来必须多副本，再引入 watch 权抢占（watch_leases 表，谁抢到谁 watch）。

9. 【数据库 Migration 统一管理】（V1.2 新增）
   三个服务共享同一 PG，启动时都建表会竞态（表已存在/列冲突）。
   - 只有 api-server 使用 alembic 管理 migration（schema owner）。
   - k8s-executor（GORM）与 ai-service（SQLAlchemy）启动时**不执行 AutoMigrate/create_all**，只校验数据库连接；表结构变更统一由 api-server 的 alembic 迁移完成。

10. 【备份必须包含加密密钥】（V1.2 新增）
    backup-cronjob 备份包必须同时包含 ENCRYPTION_KEY（单独安全存储或备份包内加密存放），否则恢复后 kubeconfig 密文无法解密。

11. 【告警风暴治理】（V1.3 新增）
    Watch 全量监听 + 多集群在故障风暴时会打爆数据库和通知。
    - 告警聚合：同集群 + 同故障类型 + 同资源的告警合并为一条活跃告警（附事件列表，message 追加事件计数），避免同一故障刷屏。
    - 静默期：集群/资源支持维护窗口配置（maintenance window），窗口内告警不通知、不触发自愈。
    - 入库限流：告警写入限速（如每分钟每集群最多 N 条，超出合并为风暴告警）；通知按 severity 限频（同告警 30 分钟内不重复通知，配合 5 分钟去重）。
    - 实现位置：k8s-executor 的 deduplicateCheck 扩展（聚合 + 限流），api-server 通知层限频。

12. 【执行安全兜底：白名单 + dry-run】（V1.3 新增）
    黑名单（SafetyCheck 危险词）不够，AI 生成的命令必须双保险。
    - 白名单策略：executor 配置允许的命令类型集合（get/list/describe/logs/rollout restart/scale/set image/apply 等），不在白名单的命令默认拒绝（可按操作类型在决策策略中配置）。
    - dry-run 预演：写操作（apply/patch/scale/delete/rollout）执行前先用 client-go DryRun 选项预演（等价 kubectl --dry-run=client），预演成功才真执行。
    - 高危强制人工：含 delete/rm/purge/drop 的命令，即使决策策略为 auto 也强制升级为人工确认（决策引擎联动）。
    - BlastRadiusEvaluate（已有）结果写入审计与审批上下文。

13. 【AI 成本控制】（V1.3 新增）
    多模型并发 + 降级重试会让 AI 成本失控。
    - severity 分级选模型：critical 告警用高优先级（贵）模型，medium/low 用低优先级（便宜）模型（call_with_fallback 增加 severity/等级参数，按告警级别过滤模型池）。
    - 成本上限：配置每日总成本上限 + 单告警诊断成本上限，超限自动降级到最便宜模型或暂停 AI 诊断（告警转人工）。
    - 成本告警：按日聚合 AICallLog.cost，今日成本超阈值通知 admin。

14. 【验证失败人工兜底】（V1.3 新增）
    修复验证失败后流程不能断。
    - 流程：AI 验证 verdict=worsened 或连续 2 次非 healed → 自动二次诊断 → 生成新方案（复用原审批流）→ 再次失败升级人工（创建高优任务 + 通知 admin/operator）。
    - 验证失败且存在已执行步骤 → 自动执行回滚（走 rollback_command），回滚结果纳入验证。
    - 每次验证失败自动写审计（verification_detail）。

15. 【知识库自动沉淀闭环】（V1.3 新增）
    修复闭环数据回流知识库，形成越用越准的飞轮。
    - 修复成功 + AI 验证通过（healed）→ 自动更新 KnowledgeEntry：命中同 fault_type 则 success_count+1，无条目则创建（root_cause 取诊断结论、repair_steps 取执行成功的步骤），可配置自动写入/人工确认。
    - 修复失败 → usage_count+1 且不增加 success_count；若方案与知识库建议冲突，标记待人工 review。
    - 实现位置：api-server 执行完成回调 + ai-service 知识库接口。

16. 【可观测性：结构化日志 + trace_id 贯穿】（V1.3 新增，二期）
    - 三服务日志统一 JSON 结构化格式（Python 配 json formatter，Go zap 已结构化），字段：trace_id、service、level、ts、msg。
    - trace_id 在审计链已有，日志也必须贯穿：api-server 生成 trace_id → 通过 gRPC metadata / HTTP header 传给 executor / ai-service，全链路日志可串联。

17. 【消息队列异步化】（V1.3 新增，二期，规模上来再做）
    - 故障风暴时 api-server 同步调 ai-service 会阻塞；演进用 Redis Stream（已有 Redis，零新增组件）做事件链：executor 检测告警 → 事件入 Stream → api-server 消费 → 诊断 → 方案 → 审批 → 执行。
    - MVP 保持同步调用，架构上预留事件模型（告警/诊断/审批事件字段先定义）。

18. 【数据库改为 MySQL + 中间件外部化】（V1.5 新增，老板决策）
    项目数据库由 PostgreSQL 改为 MySQL（192.168.6.99 现有环境，root/root）：
    - api-server / ai-service：SQLAlchemy 异步驱动 asyncpg → aiomysql，连接串 mysql+aiomysql://user:pass@host:3306/dbname
    - k8s-executor：GORM driver postgres → mysql，DSN user:pass@tcp(host:3306)/dbname?charset=utf8mb4&parseTime=True&loc=Local
    - 建库：CREATE DATABASE api_ops CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
    - 中间件（MySQL/Redis）统一部署在 192.168.6.99：MySQL 3306（root/root），Redis 6379（密码 RedisDBforeFt20200911）；Redis 连接串 redis://:密码@192.168.6.99:6379/0
    - docker-compose 移除 postgres 服务，只编排应用服务，连接外部中间件
    - 备份：pg_dump → mysqldump
    - 生产建议：应用使用专用 MySQL 账号（仅业务库权限）

【开发执行计划】（2026-08-17 确认，按此顺序开发）
总原则：后端先行 + 最小闭环（纵向切片）验证架构，再横向铺功能；API 契约先行（FastAPI /docs OpenAPI 作为前后端契约）。
1. 数据库模型 + 基础设施：alembic 初始化 + 全部模型 + 加密互测（make crypto-test 先行通过）+ 日志框架
2. k8s-executor：proto → gRPC server → k8sclient → watcher → execute/gray/circuit/rollback → quota/health/script
3. ai-service：模型适配器 + prompt 模板 + 诊断/方案/验证 + embedding/RAG + Prometheus 上下文
4. api-server 核心链路：认证 + 集群管理（含加密存 kubeconfig）+ 告警 + 诊断联动 + 决策引擎
5. 最小闭环联调：登录 → 添加集群 → watch 出告警 → AI 诊断 → 生成方案 → 决策 → 执行 → 验证（用 curl/脚本验证，不依赖 UI）
6. 前端核心页：Login / ClusterList / AlertList（含诊断展示）/ ExecutionDetail（轮询）
7. 横向补齐：审批流（含钉钉/企微回调+验签）、批量、定时任务（Redis 锁）、通知、知识库、审计、统计、用户管理、剩余前端页面
8. 部署配置：docker-compose + k8s 清单（含修正 8/9/10：executor 单副本、migration 统一、备份含密钥）+ README/docs
前端分两期：一期（步骤6）核心闭环页；二期（步骤7 内）统计/用户/审计/知识库/批量/定时任务页。

═══════════════════════════════════════════════════════════════
