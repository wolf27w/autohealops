# API-OPS 自愈平台

多 Kubernetes 集群管理 + AI 智能诊断自愈平台。统一管理多个 K8s 集群，通过 AI 智能诊断故障、生成修复方案，经决策审批后自动执行修复，全链路审计可追溯。

## 架构

```
┌──────────┐   HTTP    ┌─────────────┐   gRPC    ┌──────────────┐
│  frontend │ ────────▶ │  api-server  │ ────────▶ │ k8s-executor │
│ React+AntD│           │   FastAPI    │           │  Go+client-go │
└──────────┘           │  （唯一入口）  │           └──────┬───────┘
                       │              │   HTTP            │
                       │              │ ────────────────▶ │
                       └──────┬───────┘   ┌──────────────┐
                              │           │  ai-service  │
                              │           │   FastAPI    │
                              ▼           └──────┬───────┘
                     ┌─────────────────┐          │
                     │  MySQL（共享）   │◀─────────┘
                     └─────────────────┘
                     ┌─────────────────┐
                     │  Redis（锁/缓存）│
                     └─────────────────┘
```

- **api-server**（Python/FastAPI）：系统大脑和门面，所有业务逻辑，唯一对外 API 入口
- **k8s-executor**（Go/gRPC/client-go）：系统的手和眼睛，K8s 交互、故障检测（watch）、命令执行
- **ai-service**（Python/FastAPI）：系统的 AI 大脑，诊断、方案生成、多模型协同

## 核心能力

1. 多 K8s 集群管理（kubeconfig 加密存储，中心化管理）
2. 故障检测引擎（Watch Pod/Node 事件，5 分钟去重 + 告警聚合 + 入库限流）
3. AI 智能诊断（多模型适配 + 降级链 + 多模型协同仲裁 + Prometheus 指标 + ES 日志上下文）
4. AI 修复方案生成（操作类型识别 + 风险评估）
5. 决策引擎（操作类型 × 时间策略双维度，高危命令强制人工）
6. 审批工作流（钉钉/企微机器人消息交互 + 验签，单人/双人审批，超时自动升级）
7. 自动修复执行（灰度策略 + 熔断控制 + 自动回滚 + 白名单 + dry-run）
8. AI 修复验证（修复前后快照对比，失败自动二次诊断 + 回滚 + 人工兜底）
9. 全链路审计（SHA256 哈希链防篡改，trace_id 贯穿）
10. 知识库（向量嵌入 + RAG 检索，修复结果自动沉淀）
11. 批量处理（跨集群并行/滚动/分批执行）
12. 定制化定时任务（每集群独立 Cron，Redis 锁防重复）
13. 资源配额可见性（定时采集，非实时监控）
14. 权限管理（admin/operator/viewer 三角色 RBAC）
15. 通知服务（钉钉/企业微信，severity 路由，失败重试，限频）

## 快速开始

### 1. 环境要求

- Go 1.22+、Python 3.11+、Node 20+、Docker（可选，部署用）
- MySQL（中间件，本环境为 192.168.6.99）
- Redis（中间件，本环境为 192.168.6.99）

### 2. 配置

复制 `.env.example` 为 `.env`，填入真实配置（数据库、Redis、AI 模型 API Key、加密密钥等）。

```bash
cp .env.example .env
# 编辑 .env，填 ENCRYPTION_KEY / JWT_SECRET_KEY / AI 模型 Key 等
```

### 3. 初始化数据库

```bash
cd api-server
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/alembic upgrade head   # 建表
```

### 4. 启动服务（本地开发）

```bash
# 终端 1：k8s-executor
cd k8s-executor && go run ./cmd/executor

# 终端 2：ai-service
cd ai-service && .venv/bin/uvicorn app.main:app --port 9000

# 终端 3：api-server
cd api-server && .venv/bin/uvicorn app.main:app --port 8080

# 终端 4：前端
cd frontend && npm install && npm run dev
```

### 5. 访问

- 前端：http://localhost:5173
- API 文档（OpenAPI）：http://localhost:8080/docs

### 6. 默认账号

| 用户名 | 密码 | 角色 |
|---|---|---|
| admin | admin123 | 管理员 |
| operator | operator123 | 运维 |
| viewer | viewer123 | 只读 |

> 首次登录后请在「系统设置 → 修改密码」中修改默认密码。

## 加密互测

跨语言加密（Python ↔ Go）格式一致性验证：

```bash
make crypto-test
```

## 部署

- Docker Compose：`docker compose up -d`
- Kubernetes：见 `k8s/` 目录清单
- 详见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)

## 文档

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 架构详解
- [docs/API.md](docs/API.md) — API 列表
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — 部署与备份恢复
- [docs/ENCRYPTION.md](docs/ENCRYPTION.md) — 跨语言加密格式约定

## 环境变量说明

见 `.env.example`，关键项：

| 变量 | 说明 |
|---|---|
| DATABASE_URL | MySQL 连接串（mysql+aiomysql://user:pass@host:3306/db） |
| REDIS_URL | Redis 连接串 |
| ENCRYPTION_KEY | 32 字节，kubeconfig 加密密钥（定下后勿改） |
| JWT_SECRET_KEY | JWT 签名密钥 |
| OPENAI_API_KEY / DEEPSEEK_API_KEY / CLAUDE_API_KEY | AI 模型 Key（也可前端「AI 模型」页配置） |
| PROMETHEUS_URL / ES_URL | 可观测性（也可前端「系统设置」页配置） |
