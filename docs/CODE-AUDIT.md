# API-OPS 自愈平台 全面代码审计报告

> 审计日期：2026-08-26
> 审计范围：api-server（Python FastAPI，143 文件 / 约 1.5 万行）、k8s-executor（Go，26 文件 / 6700 行）、ai-service（Python FastAPI，23 文件）、frontend（React+AntD，40 文件 / 4750 行）
> 分类：漏洞 / 功能重复 / 优化点

---

## 一、代码漏洞 / 安全漏洞

### 🔴 高危（必须优先处理）

| # | 服务 | 位置 | 问题 | 建议 |
|---|---|---|---|---|
| 1 | api-server | `api/ai_proxy.py:14` | AI 代理路由完全无鉴权，任何未认证者可访问 ai-service 全部能力 | 加 `get_current_active_user` + path 白名单 |
| 2 | ai-service | `main.py` | 全服务接口无鉴权（模型配置/API Key/调用日志全裸奔） | 接入统一鉴权 |
| 3 | executor | `executor/exec_in_pod.go:16` | 只读命令白名单可被 `awk/sed` 的 `system()` 绕过 → 任意命令执行 | 移除 awk/sed 等可执行外部命令的工具 |
| 4 | executor | `k8sclient/manager.go:96` | `buildConfigWithContext` 忽略传入 kubeconfig，可能连错集群 | 用 `RESTConfigFromKubeConfig` 基于 DB 配置构建 |
| 5 | api-server | `main.py:25` | 启动硬编码弱口令 `admin/admin123` 等 | 首次启动强制设密或生成随机密码 |
| 6 | api-server | `alert_auto_service.py:126` | kubectl 命令 f-string 拼接告警字段，命令注入隐患 | 资源名严格校验 + 结构化参数 |
| 7 | api-server | `approval_service.py:183` | 审批 approve/reject 读-改-写非原子，并发竞态 | `with_for_update` 或条件 UPDATE |
| 8 | ai-service | `model_openai.py:13` | API Key 明文存储，字段名却叫 `api_key_encrypted` | 真正加密存储 |
| 9 | ai-service | `rag_service.py` / `diagnosis_service.py` | Prompt 注入：知识库内容/日志/告警未清洗直接进 prompt | 输入清洗 + 边界隔离 |
| 10 | ai-service | `model_adapter.py` | SSRF：api_base 可任意配置指向内网/元数据地址 | 校验 base_url 白名单 |
| 11 | frontend | `App.tsx:32` | 鉴权仅判断 token 存在，admin 路由无角色守卫 | 加角色守卫 |

### 🟠 中危

- **api-server**：IM 回调验签未配置直接放行（`webhooks.py`）；CORS `*` + credentials 并存；异常回显内部细节（`main.py:153`）；密钥明文存储+明文回显（`system_setting_service.py`）；数据库诊断 IDOR 越权（`db_diagnosis.py`）；Mass Assignment（`custom_tasks.py` / `decisions.py`）；飞书 JSON 手工拼接未转义（`feishu_service.py`）
- **executor**：`pickContext` 多 context 随机选择（`manager.go:74`）；`execGet` 空指针解引用（`execute.go:248`）；A 类故障上报绕过维护窗口/限流（`pod_watcher.go`）；告警去重并发竞态（`createAlert` 先查后插无唯一索引）
- **ai-service**：PromQL 注入（`prometheus_client.py` f-string 拼接）；错误信息回传泄露内部细节
- **frontend**：诊断结果双重解包（`DiagnosticCenter.tsx:146`）；API Key 明文 toast（`UserManagement.tsx:49`）；防抖计时器用 `let` 非 `useRef`（`KnowledgeBase.tsx:12`）；`JSON.parse(localStorage)` 无 try/catch

### 🟡 低危

- api-server：登录无防爆破、登出 no-op、LDAP 异常回显、资源/日志/集群接口缺资源级鉴权、审计哈希链并发分叉等
- executor：白名单与实际实现脱节、SafetyCheck 子串匹配可绕过、FieldSelector 拼接注入、脚本任务 `sh -c` 无沙箱
- frontend：401 处理不完整、错误双重弹窗

> ✅ **正面结论**：三端均无真实 SQL 注入（SQLAlchemy ORM / GORM 参数化）；前端无 XSS（dangerouslySetInnerHTML/innerHTML/eval 均 0）、无硬编码密钥字面量。

---

## 二、功能重复项

| # | 位置 | 重复内容 | 建议 |
|---|---|---|---|
| 1 | api-server `alert_auto_service.py:34` vs `notification_service.py:166` + executor `pod_watcher.go:327` vs `repair_image_pull.go:114` | `infer_deployment_name` 重复实现 4 处（跨服务 2×2） | 抽公共工具 |
| 2 | frontend `OnDuty.tsx` vs `AlertPolicies.tsx` | `DutyTab` 值班组件两份，逻辑几乎一字不差 | 抽共享组件 |
| 3 | frontend 15+ 个页面 | CRUD 骨架（load/openModal/save/Table/Modal/Form）重复 | 抽共享 hook/组件 |
| 4 | ai-service `_parse_diagnosis_json`/`_extract_json`/`_parse_decision`/`_parse_verdict` | AI 返回 JSON 解析逻辑重复 4 份 | 抽公共工具 |
| 5 | api-server `notification_service.py` vs `messaging.py` | IM 发送层（钉钉/企微/飞书）完全重复 | 统一收敛 messaging |
| 6 | executor `checkRecovered`/`GetPodStatus`/`ListDeploymentPods` | Pod 恢复判定逻辑三处重复 | 抽公共函数 |
| 7 | api-server `alert_auto_service.py:170` vs `decision_engine.py` | 高危命令判定逻辑重复且口径不一致 | 统一调 decision_engine |
| 8 | api-server 10+ 模块 | `ExecutorClient()` 重复实例化 | 单例注入 |
| 9 | api-server `_in_time_range`/`_ns_match` | 时间范围判断、namespace 通配匹配各重复 2 处 | 抽公共工具 |
| 10 | ai-service `diagnose_alert` vs `multi_diagnose` | render_ctx 诊断上下文构建重复 | 抽公共函数 |

---

## 三、需要优化的地方

### 性能

| # | 服务 | 位置 | 问题 | 建议 |
|---|---|---|---|---|
| 1 | ai-service | `rag_service.py:25` | RAG 检索全表遍历 + Python 逐条余弦，O(N) 无索引 | 引入 pgvector/faiss/ES dense_vector |
| 2 | api-server | `projects.py:66` / `tasks.py:30` / `self_heal_rules.py:17` / `db_connections.py:69` | 多处 N+1 查询 | JOIN / 批量 in 查询 |
| 3 | api-server | `ai_client.py:22` | `httpx.AsyncClient` 每次请求新建，无连接池复用 | 全局复用 AsyncClient |
| 4 | frontend | `AlertList.tsx:28` | 写死 `page_size:100`，超 100 条告警丢失 | 后端分页 |
| 5 | frontend | 大量列表 | `pagination={false}` 一次性拉全量 | 加分页 |

### 代码质量

| # | 服务 | 问题 | 建议 |
|---|---|---|---|
| 1 | frontend | `any` 类型 227 处，完全放弃类型保护 | 定义类型 |
| 2 | api-server | 宽泛 `except Exception` 吞异常，无告警无重试 | 分类处理 + 告警 |
| 3 | api-server | 函数内联 `import`/`__import__` 泛滥 | 统一顶部导入 |
| 4 | ai-service | `multi_diagnose` 绕过 `call_with_fallback`，无日志/降级/成本控制 | 统一走 fallback |
| 5 | ai-service | `_pick_best_model` 用 `__import__` 动态导入 | 正常导入 |
| 6 | frontend | `@tanstack/react-query` 已声明但零使用，接口重复拉取无缓存 | 启用 react-query 或移除 |
| 7 | api-server | 死代码（`scheduler_service.py:15` try/except 两分支相同） | 清理 |
| 8 | api-server | 命名不一致（`infer_deployment_name` vs `_infer_deployment_name`） | 统一命名 |

---

## 处理优先级建议

**P0（安全红线，立即）**：
1. executor 白名单 awk/sed 绕过（任意命令执行）
2. api-server ai_proxy + ai-service 无鉴权
3. 硬编码弱口令
4. 密钥明文存储

**P1（重要）**：
5. kubectl 命令注入隐患
6. 审批并发竞态
7. buildConfigWithContext 连错集群
8. Prompt 注入 / SSRF / IDOR / Mass Assignment

**P2（优化）**：
9. 功能重复收敛（infer_deployment_name、DutyTab、IM 发送层、JSON 解析）
10. N+1 查询、RAG 向量索引、any 类型

---

*报告由 4 个并行审计子代理（api-server / k8s-executor / ai-service / frontend）扫描生成，已人工复核汇总。*
