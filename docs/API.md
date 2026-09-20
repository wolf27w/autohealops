# API 列表

统一响应格式：
- 成功：`{"code": 200, "message": "success", "data": {...}}`
- 失败：`{"code": 4xx/5xx, "message": "错误描述", "data": null}`

认证：请求头 `Authorization: Bearer <token>`（除登录和回调外均需认证）。

角色：`admin`（管理员）/ `operator`（运维）/ `viewer`（只读）。

## 认证

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| POST | /api/v1/auth/login | 登录（支持 local/ldap），返回 token | 无 |
| GET | /api/v1/auth/me | 当前用户信息 | 登录 |
| POST | /api/v1/auth/logout | 登出 | 登录 |
| POST | /api/v1/auth/change-password | 修改密码 | 登录 |

## 集群管理

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| POST | /api/v1/clusters | 添加集群（kubeconfig 加密存储） | operator |
| GET | /api/v1/clusters | 集群列表 | 登录 |
| GET | /api/v1/clusters/{id} | 集群详情 | 登录 |
| DELETE | /api/v1/clusters/{id} | 删除集群 | admin |
| POST | /api/v1/clusters/{id}/health-check | 健康检查 | operator |

## 告警

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| GET | /api/v1/alerts | 告警列表（分页/筛选） | 登录 |
| GET | /api/v1/alerts/{id} | 告警详情 + 诊断 | 登录 |
| POST | /api/v1/alerts/{id}/resolve | 解决告警 | operator |
| POST | /api/v1/alerts/batch-resolve | 批量解决 | operator |
| POST | /api/v1/alerts/{id}/diagnose | 触发 AI 诊断 | operator |
| GET | /api/v1/alerts/{id}/diagnosis | 查询诊断结果 | 登录 |

## 任务

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| GET | /api/v1/tasks | 任务列表 | 登录 |
| GET | /api/v1/tasks/{id} | 任务详情 + 时间线 | 登录 |
| POST | /api/v1/tasks/{id}/acknowledge | 确认（pending→acknowledged） | operator |
| POST | /api/v1/tasks/{id}/start | 开始（acknowledged→in_progress） | operator |
| POST | /api/v1/tasks/{id}/resolve | 解决（in_progress→resolved） | operator |

## 审批

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| POST | /api/v1/approvals | 创建审批 | operator |
| GET | /api/v1/approvals | 审批列表 | 登录 |
| GET | /api/v1/approvals/{id} | 审批详情 | 登录 |
| POST | /api/v1/approvals/{id}/decide | 平台内审批（approve/reject） | operator |

## 决策

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| POST | /api/v1/decisions/evaluate | 评估计划决策 | operator |
| GET | /api/v1/decisions/policies | 决策策略列表 | 登录 |
| POST | /api/v1/decisions/policies | 创建策略 | admin |
| PUT | /api/v1/decisions/policies/{id} | 更新策略 | admin |

## 批量处理

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| POST | /api/v1/batch | 创建批量操作 | operator |
| POST | /api/v1/batch/{id}/execute | 执行批量 | operator |
| GET | /api/v1/batch | 批量列表 | 登录 |
| GET | /api/v1/batch/{id} | 批量详情 | 登录 |

## 定时任务

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| GET | /api/v1/custom-tasks | 任务列表 | 登录 |
| POST | /api/v1/custom-tasks | 创建任务 | operator |
| PUT | /api/v1/custom-tasks/{id} | 更新任务 | operator |
| DELETE | /api/v1/custom-tasks/{id} | 删除任务 | admin |
| POST | /api/v1/custom-tasks/{id}/toggle | 启用/暂停 | operator |
| POST | /api/v1/custom-tasks/{id}/run-now | 立即执行 | operator |
| GET | /api/v1/custom-tasks/{id}/logs | 执行日志 | 登录 |

## 资源配额

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| GET | /api/v1/resources/clusters/{id}/overview | 集群概览 | 登录 |
| GET | /api/v1/resources/clusters/{id}/detail | 集群详情（namespace 工作负载 + Pod） | 登录 |
| GET | /api/v1/resources/clusters/{id}/namespaces | namespace 配额 | 登录 |
| POST | /api/v1/resources/clusters/{id}/collect | 触发采集 | operator |

## 知识库

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| GET | /api/v1/knowledge/search | 搜索（RAG） | 登录 |
| GET | /api/v1/knowledge | 列表 | 登录 |
| POST | /api/v1/knowledge | 添加 | operator |
| DELETE | /api/v1/knowledge/{id} | 删除 | admin |

## 审计

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| GET | /api/v1/audit/trace/{trace_id} | 审计链路 | 登录 |
| GET | /api/v1/audit/search | 搜索 | 登录 |
| POST | /api/v1/audit/verify-chain | 哈希链验证 | admin |

## 统计

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| GET | /api/v1/stats/dashboard | 仪表盘聚合（筛选/健康分/KPI/趋势/…） | 登录 |
| GET | /api/v1/stats/ai-effectiveness | AI 有效性趋势（报表导出用） | 登录 |
| GET | /api/v1/stats/model-cost | 模型成本（报表导出用） | 登录 |
| GET | /api/v1/stats/ai-dashboard | AI 运行/成本看板 | 登录 |
| GET | /api/v1/stats/platform-health | 平台健康探活 | 登录 |

> 去重说明：旧端点 `/stats/overview`、`/stats/repair-success-rate`、`/stats/alert-status-distribution` 与仪表盘重复，已移除。

## 用户管理

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| GET | /api/v1/users | 用户列表 | admin |
| POST | /api/v1/users | 创建用户 | admin |
| PUT | /api/v1/users/{id} | 更新用户 | admin |
| DELETE | /api/v1/users/{id} | 删除用户 | admin |
| POST | /api/v1/users/{id}/api-key | 重置 API Key | admin |

## 通知

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| GET | /api/v1/notifications/configs | 通知配置列表 | 登录 |
| POST | /api/v1/notifications/configs | 创建配置 | admin |
| DELETE | /api/v1/notifications/configs/{id} | 删除配置 | admin |
| GET | /api/v1/notifications/logs | 发送日志 | 登录 |

## 系统设置

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| GET | /api/v1/settings | 读取设置（Prometheus/ES/LDAP） | admin |
| PUT | /api/v1/settings | 更新设置 | admin |

## 日志查询

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| GET | /api/v1/logs/search | 日志查询（ES 数据源） | 登录 |

## 钉钉/企微回调

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| POST | /api/v1/webhooks/dingtalk/callback | 钉钉回调（验签） | 无 |
| POST | /api/v1/webhooks/wechat/callback | 企微回调（验签） | 无 |

## AI 服务代理（api-server → ai-service）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | /api/v1/ai/models | 模型配置列表/创建 |
| GET | /api/v1/ai/logs | AI 调用日志 |
| GET | /api/v1/ai/prompt-templates | 提示词模板列表 |
| PUT | /api/v1/ai/prompt-templates/{id} | 更新模板 |
| POST | /api/v1/ai/diagnose | AI 诊断 |
| POST | /api/v1/ai/multi-diagnose | 多模型诊断 |
| POST | /api/v1/ai/generate-plan | 生成方案 |
| POST | /api/v1/ai/verify-repair | 修复验证 |
| POST | /api/v1/ai/rag/search | 知识检索 |
| POST | /api/v1/ai/rag/add | 添加知识 |
| POST | /api/v1/ai/logs/search | 日志查询 |
