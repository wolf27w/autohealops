# 漏洞修复开发任务清单

> 依据：`CODE-AUDIT.md`（2026-08-26 全面审计）
> 说明：按优先级分 P0/P1/P2，每条含任务、位置、修复要点，可直接排期开发。

---

## P0 —— 安全红线（立即修复）

| 任务 | 服务 | 位置 | 修复要点 |
|---|---|---|---|
| SEC-01 命令白名单绕过（任意命令执行） | executor | `executor/exec_in_pod.go:16` | 移除 `awk`/`sed`/`echo` 等可执行外部命令的工具，白名单收紧为纯只读命令 |
| SEC-02 ai-service 全服务无鉴权 | ai-service | `main.py` | 接入统一鉴权（token/JWT），至少内部接口加网关鉴权 |
| SEC-03 AI 代理路由无鉴权 | api-server | `api/ai_proxy.py:14` | 加 `get_current_active_user` + path 白名单 + 剥离敏感头 |
| SEC-04 硬编码弱口令 | api-server | `main.py:25`（seed_default_users） | 不自动创建默认密码；首次启动强制设密或生成随机强密码 |
| SEC-05 密钥明文存储/回显 | api-server + ai-service | `system_setting_service.py`、`model_openai.py` | 密钥 `encrypt_aes256_gcm` 加密存储，读取脱敏 `***` |

## P1 —— 重要（尽快修复）

| 任务 | 服务 | 位置 | 修复要点 |
|---|---|---|---|
| SEC-06 kubectl 命令注入隐患 | api-server | `alert_auto_service.py:126` | 资源名/命名空间严格校验 `^[a-z0-9]([-a-z0-9]*[a-z0-9])?$`；命令结构化参数 |
| SEC-07 审批并发竞态 | api-server | `approval_service.py:183` | `with_for_update()` 或条件 UPDATE `WHERE status='pending'` |
| SEC-08 buildConfigWithContext 连错集群 | executor | `k8sclient/manager.go:96` | 用 `RESTConfigFromKubeConfig` 基于 DB 配置构建 |
| SEC-09 Prompt 注入 | ai-service | `rag_service.py` / `diagnosis_service.py` | 输入清洗 + 边界隔离（知识库/日志/告警内容不直接作为指令） |
| SEC-10 SSRF | ai-service | `model_adapter.py` | `api_base` 校验白名单，禁止内网/元数据地址 |
| SEC-11 前端无角色守卫 | frontend | `App.tsx:32` | admin 路由加角色守卫，非 admin 跳转 |

## P2 —— 中危（排期修复）

| 任务 | 服务 | 位置 | 修复要点 |
|---|---|---|---|
| SEC-12 IM 回调验签可绕过 | api-server | `webhooks.py:33` | 强制要求配置 secret；飞书改用官方 `X-Lark-Signature` |
| SEC-13 CORS 配置错误 | api-server | `main.py:135` | `allow_origins=["*"]` + `credentials=True` 改显式白名单 |
| SEC-14 异常回显内部细节 | api-server | `main.py:153` | 客户端返回通用文案，`exc` 只进日志 |
| SEC-15 数据库诊断 IDOR | api-server | `db_diagnosis.py:14` | 统一加 `check_db_permission(user, conn_id)` |
| SEC-16 Mass Assignment | api-server | `custom_tasks.py:48` / `decisions.py:74` | 改显式字段白名单赋值 |
| SEC-17 飞书 JSON 未转义 | api-server | `feishu_service.py:56` | `json.dumps` 组装消息体 |
| SEC-18 登录防爆破 / 登出失效 | api-server | `auth.py` | 加速率限制 + token 黑名单 |
| SEC-19 资源/日志接口资源级鉴权 | api-server | `resources.py` / `logs.py` | 加 `check_cluster_permission` |
| SEC-20 类型转换未捕获 / 审计哈希链竞态 | api-server | `db_connections.py:43` / `audit_service.py:40` | try/except + 事务一致性 |

---

## 备注

- **正面结论**：三端无真实 SQL 注入、前端无 XSS、无硬编码密钥字面量——基础安全良好，问题集中在鉴权缺口、命令拼接、密钥明文。
- 部分任务（SEC-01 白名单、SEC-16 Mass Assignment 写法、SEC-15 鉴权）在今日开发中已有部分代码基础，可复用。
