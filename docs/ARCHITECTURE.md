# 架构设计

## 三服务架构

API-OPS 自愈平台采用三服务 + 前端 + 共享数据库的架构：

| 服务 | 技术栈 | 职责 |
|---|---|---|
| api-server | Python/FastAPI | 系统大脑和门面，所有业务逻辑，唯一对外 API 入口 |
| k8s-executor | Go/gRPC/client-go | 系统的手和眼睛，K8s 交互、故障检测、命令执行 |
| ai-service | Python/FastAPI | 系统的 AI 大脑，诊断、方案生成、多模型协同 |
| frontend | React/TypeScript/AntD | 用户界面 |

## 服务间通信

```
前端 ──HTTP──▶ api-server ──gRPC──▶ k8s-executor
                  │
                  └──HTTP──▶ ai-service
                  │
                  └──▶ MySQL（共享）
                  └──▶ Redis（锁/缓存/限流/会话）
```

- 前端只与 api-server 通信（唯一入口）
- api-server 通过 gRPC 调用 k8s-executor（含流式执行进度）
- api-server 通过 HTTP 调用 ai-service（诊断/方案/验证/RAG/日志）
- 三个服务共享同一个 MySQL 数据库（表结构由 api-server 的 alembic 统一管理）
- api-server 独享 Redis（分布式锁/缓存/限流/会话）

## 数据流（核心闭环）

```
K8s 事件 → k8s-executor Watch 检测故障 → 写入 alerts 表
   ↓
api-server 告警自动处理（每 1 分钟扫描）
   ├─ 已恢复 → 自动 resolve
   └─ 未恢复 → ai-service AI 诊断（多模型 + Prometheus 指标 + ES 日志）
        ↓
   ai-service 生成修复方案（操作类型识别 + 风险评估）
        ↓
   api-server 决策引擎（操作类型 × 时间策略，高危强制人工）
        ↓
   审批流（钉钉/企微交互卡片，单人/双人，超时升级）
        ↓
   k8s-executor 灰度执行（白名单 + dry-run + 熔断 + 回滚）
        ↓
   ai-service 修复验证（前后快照对比，失败二次诊断/回滚/人工兜底）
        ↓
   全链路审计（SHA256 哈希链）+ 知识库沉淀
```

## 关键设计

### 1. 决策引擎（安全阀）

双维度决策：
- **操作类型**：READ（只读）/ CREATE / UPDATE / DELETE，越危险越严格
- **时间策略**：工作时间 / 非工作时间 / 周末 / 节假日，休息时段更保守

高危命令（delete/rm/purge/drop）即使策略为 auto 也强制至少单人审批。

### 2. 分布式锁（防多副本重复执行）

api-server 多副本部署时，定时任务/审批超时检查/批量执行用 Redis 锁（SET NX PX + Lua 释放 + 自动续期）防止重复触发。

### 3. 跨语言加密（kubeconfig 安全）

kubeconfig 用 AES-256-GCM 加密存储（api-server 加密，k8s-executor 解密），格式严格一致（见 docs/ENCRYPTION.md），通过 `make crypto-test` 双向验证。

### 4. 审计哈希链（防篡改）

审计记录用 SHA256 哈希链串联：`hash = SHA256(previous_hash + 数据)`，任何篡改都会破坏链，可通过「审计追溯 → 哈希链完整性验证」检测。

### 5. 可观测性复用

- 监控指标：复用现有 Prometheus（诊断上下文注入指标趋势）
- 日志：复用现有 Elasticsearch（诊断上下文 + 前端日志查看）
- 平台不自行采集存储监控/日志数据

## 部署拓扑

- k8s-executor：固定单副本（有状态 watcher，多副本会重复 watch）
- api-server：多副本 + HPA（无状态，调度任务用 Redis 锁防重复）
- ai-service：多副本 + HPA（无状态）
- 中间件：MySQL + Redis 外部部署（本环境 192.168.6.99）

## 目录结构

见项目根目录，三服务各一个子目录 + frontend + k8s + docs。
