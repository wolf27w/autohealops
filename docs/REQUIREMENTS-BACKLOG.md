# API-OPS 平台 —— 需求总账（Backlog）

> 整理：小吴　更新：2026-10-14　依据：DESIGN-2026-09-10.md + 工作日志 + 代码核查
> 用途：一张表看清「已做 / 待做 / 待确认」。状态以代码为准，未核实项标 ❓。
>
> 🔴 **最新状态与开发顺序请看 `docs/ROADMAP.md`（2026-10-15 复核，代码为准）。本文件为历史流水，R1–R13 多项已落地但此处未更新。**

**状态图例**：✅ 已完成 ｜ ⏳ 待办 ｜ ❓ 待确认（老板拍板 / 状态不明） ｜ ⏸ 暂缓

---

## 一、已完成（截至 2026-09-11）

| 阶段 / 专项 | 内容 | 状态 |
|---|---|---|
| 阶段0 安全兜底 | P0 三项（Prompt 注入、Mass Assignment、告警去重竞态）| ✅ |
| 阶段1 权限地基 | 专项 A：权限↔项目管理合并（成员三级 只读/可操作/管理员）| ✅ |
| 阶段2 审核闭环 | 批一 7 条（人工协助/高风险审批、站内消息、IM 模板按渠道、处理记录处理人）| ✅ |
| 阶段3 云原生 RBAC | 专项 B：Impersonation、自动下发三档 ClusterRole、reconcile RoleBinding、存量自动接入 | ✅ |
| 阶段4 集群改造 + 终端 | 专项 D：去 Pod Tab、服务详情、工作负载操作、xterm.js 交互终端、静默 5 分钟断开 | ✅ |
| 阶段5 状态检测补全 | 批三：容器级 8 类 + 节点级 2 类、TerminatingStuck 60s 告警/120s 强删 | ✅ |
| 阶段6 体验优化（核心项） | 菜单折叠、15s 自动刷新、AI 配置合并、幽灵页部分清理、告警静默/维护窗口、平台 /metrics | ✅ |
| 9-11 追加 | 集群详情分页+搜索、审批去 admin 节点、诊断三分类（故障/应用/数据库）、developer 角色、DB 连接测试、消息显示名称、处理记录倒序、状态列含副本数、更新时间列 | ✅ |

---

## 二、待办（明确未完成，代码已核实）

| # | 需求 | 落点 | 核实结论 |
|---|---|---|---|
| 1 | **专项 C · 仪表盘 / 大屏驾驶舱** | 前端 Dashboard 重构 + 新增 `/screen` 大屏路由 | ✅ 2026-09-12（见第五节） |
| 2 | **批二 · AI 诊断提速**（默认单轮、深度诊断开关） | ai-service/agent_service | ✅ 2026-09-12（知识库优先 + 单轮预采集 + 深度开关） |
| 3 | **批二 · 命名空间加载提速** | resources 接口 | ✅ 2026-09-12（Redis 短缓存 + (cluster_id,namespace) 联合索引，索引迁移待执行） |
| 4 | **一键回滚修复** | 处理记录 / 执行详情 行内按钮 | ✅ 2026-09-12（复用 executor RollbackPlan 逆序回滚 + 审计） |
| 5 | **RAG 向量索引**（原 O(N) 全表余弦） | ai-service 知识库检索 | ✅ 2026-09-12（内存归一化矩阵 + 向量化余弦 + TTL/失效；非 faiss/hnsw） |
| 6 | **httpx 连接池复用**（现每请求新建） | api-server / ai-service 多处 | ✅ 2026-09-12（共享客户端，见下） |
| 7 | **CI/CD + 日志归档策略** | 仓库级 | ✅ 2026-09-12（`scripts/ci_check.sh` + GH Actions + `scripts/rotate_logs.sh`） |
| 8 | **菜单合并 23→~12** | MainLayout 菜单 IA | ✅ 2026-09-12（合并为 15 项：告警处理/策略配置/通知配置/用户与权限 合并页） |
| 9 | **幽灵页清理** | `OnDuty.tsx`（无路由无菜单） | ✅ 2026-09-12（已移除，未引用） |
| 10 | **P3 工程化**：类型化（any 227 处）、重复收敛、react-query、关键路径单测 | 全栈 | ✅ 基本完成：any 全清(0)、react-query(4 轮询页)、单测+CI；仅剩深层次重复收敛 |

---

## 三、待确认（需老板拍板 / 澄清）

| # | 事项 | 说明 |
|---|---|---|
| 1 | **专项 E · 砍自愈策略死代码** | `SelfHealPolicy`/`current_mode()` 确认死代码，删除属破坏性操作，等拍板。规则（SelfHealRule）保留 |
| 2 | **安全复查「仍待修」5 项** | Prompt 注入清洗（`diagnosis_service.py` 未见）、资源/日志接口鉴权覆盖、IM 回调验签（飞书官方签名）等是否随阶段0 已覆盖 → 需复核 | ✅ 2026-09-12 复核：①Prompt 注入已做（`_sanitize_external/_ident`）②Mass Assignment 已做（custom_tasks/decisions 显式白名单）④资源/日志鉴权已做（`_deny_cluster`/`check_cluster_permission`）⑤钉钉/企微/飞书（X-Lark-Request-Token）验签已做。③告警去重并发：已用进程内 dedupLocks（同进程安全；水平扩容需 DB 唯一索引兑底，代码已注释）；飞书 `X-Lark-Signature`（Encrypt Key 模式）为可选加固。 |
| 3 | **V4.1-Flash 模型** | 9-11 待办：加模型（待确认 model_name，复用现有 DeepSeek key/base） |
| 4 | **诊断分离口径** | 老板说「故障诊断 / 应用诊断」应分开；已做三分类页面，是否即达标待确认 |
| 5 | **批二/批一遗留小项** | 见 DESIGN 第 9 节功能清单（告警协作、审批增强、知识库治理、报表导出、AI 反馈闭环/成本看板、值班换班/日历、LDAP 切换）|

---

## 四、产品向「功能缺失」清单（DESIGN 第 9 节，未排期）

- 告警协作（确认 / 认领 / 备注 / 工单）
- 审批增强（批量 / 加签 / 转办 / 意见模板）
- 知识库治理（命中率 / 失效 / 导入导出）
- 报表 / 导出 / 定时推送
- AI 反馈闭环 / 成本看板
- 值班换班 / 代班 / 日历
- LDAP / 本地登录切换（`auth_type` 已有）
- 平台自监控（仪表盘卡片，后端 `/metrics` 已做）

---

## 五、2026-09-12 本轮完成（离线开发，未动数据库）

1. **AI 诊断提速（批二-1）**：`run_agent_turn(deep=False)` 默认走快速诊断——① 知识库优先（相似度 ≥0.9 直接复用，零 AI 调用）；② 未命中则平台预采集 describe/日志/指标 → 单轮 AI 出结论；`deep=true` 保留原多轮工具循环。ai-service 新增 `/api/v1/ai/agent/diagnose`；前端 AgentChat 加「深度诊断」开关 + 「知识库复用」标记。
2. **命名空间加载提速（批二-2）**：`namespace-list` 加 Redis 短缓存（300s，Redis 不可用自动降级直查）；新增迁移 `b8c9d0e1f2a3` 加 `resource_snapshots(cluster_id, namespace)` 联合索引。
3. **一键回滚（批二/第9节）**：`POST /api/v1/executions/{plan_id}/rollback`（operator+）→ 调 executor `RollbackPlan` 逆序执行 `rollback_command`，成功后步骤置 `rolled_back`，写审计；处理记录/执行详情加「一键回滚」按钮。
4. **幽灵页清理**：移除未引用的 `pages/OnDuty.tsx`。
5. **httpx 连接池复用**（本轮追加）：新增 `app/core/http_client.py`（两服务各一份）——按 `trust_env` 分组的进程级共享 `AsyncClient`（连接池 100/keepalive 20、单请求 timeout 可覆盖）；改造 8 个文件共 20 处调用（messaging/notification_service/feishu_service/es_client/ai_client/ai_proxy + ai-service model_deepseek/prometheus_client/es_client）；lifespan 退出时 `close_http_clients()`；行为兼容（保留各请求原 timeout / auth / trust_env）。
6. **专项 C 仪表盘/大屏（本轮追加）**：
   - 后端 `GET /stats/dashboard`（聚合：核心指标含 AI 自愈成功率/MTTR/今日处理数 + 24h 趋势 + 集群墙 + 实时告警 + 审批流水 + AI 运行区 + 平台健康）与 `GET /stats/platform-health`（DB/Redis 探活）。
   - 前端 `Dashboard.tsx` 重写：响应式 Grid 铺满 + 核心指标卡 + 24h 折线（@ant-design/charts，已内置依赖）+ 集群健康墙 + 实时告警 + 流水 + AI 运行区 + 平台健康；15s 轮询；**全屏按钮**（Fullscreen API）。
   - 新增独立大屏路由 `/screen`（无侧栏/顶栏框架，深色、多页 20s 轮播、15s 轮询）。
   - `MainLayout`：Header 加「大屏」入口 + 全屏按钮；Content 去固定 padding 自适应。
7. **菜单合并 23→15（本轮追加）**：新增合并页 `AlertHandling/StrategyConfig/NotificationCenter/UserPermission`（Tabs），菜单改为 告警处理 / 策略配置 / 通知配置 / 用户与权限 四项；旧路由保留（兼容内部跳转/深链）。
8. **安全「仍待修」5 项复核**：①Prompt 注入、②Mass Assignment、④资源/日志鉴权、⑤IM 回调验签 均已闭环；③告警去重并发已有进程内锁（水平扩容需 DB 索引兜底，注释已标）。
9. **C1 告警协作（确认/认领/备注）**：`alerts` 加 assigned_to/assigned_at/acknowledged/acknowledged_by/acknowledged_at；新增 `alert_comments` 表（迁移 `c9d0e1f2a3b4`）；API `POST /alerts/{id}/assign|/ack`、`GET|POST /alerts/{id}/comments`；列表返回协作字段；前端 AlertList 加「协作」列 + 认领/确认/备注按钮 + 备注抽屉（协作记录）。工单未做。

> 待环境恢复后执行：`cd api-server && ./.venv/bin/python -m alembic upgrade head`（新迁移 `b8c9d0e1f2a3` + `c9d0e1f2a3b4`）。

---

## 六、未开发清单（2026-09-12 11:50 复核）

> 已完成（今日追加）：C1 告警协作（确认/认领/备注）｜专项 C 仪表盘/大屏（`/dashboard` 即大屏，实时数据）｜菜单合并+导航伸缩｜故障诊断/处理记录去重｜性能：dashboard 缓存+索引、处理记录 N+1 修复｜迁移 `b8c9d0e1f2a3` / `c9d0e1f2a3b4` / `d0e1f2a3b4c5` 已执行。

### A. 工程/平台类| # | 项 | 现状 |
|---|---|---|
| A1 | RAG 向量索引（现 O(N) 全表余弦） | ✅ 2026-09-12（内存归一化矩阵 + 向量化余弦 + TTL/失效） |
| A2 | CI/CD + 日志轮转/归档 | ✅ 2026-09-12（ci_check.sh + GH Actions + rotate_logs.sh + 文档） |
| A3 | P3 工程化：`any` 类型化、重复实现收敛、react-query、关键路径单测 | 🟡 已做：单测+入 CI、types.ts+api.ts 类型化、**组件 any 全清（357→0，opencode 批量）**、react-query（provider + AlertList/Screen/ApprovalList/FaultDiagnosis 4 页）；**未做**：深层次重复实现收敛（仅收敛了 webhooks `_get_channel_field`） |
| A4 | 后台循环超时/隔离（gRPC 已改 to_thread，但 30s 超时仍占线程池；建议后续给后台循环加并发上限/更短超时） | ✅ 2026-09-12 已做：executor_client `acall` 加全局信号量（`EXECUTOR_MAX_CONCURRENCY` 默认 8）限并发 |

### B. 待老板拍板/确认
| # | 项 |
|---|---|
| B1 | 专项 E：砍 `SelfHealPolicy` 死代码（破坏性） | ✅ 2026-09-12 已删（模型/路由/服务/引用/前端 api + 迁移 drop 表） |
| B2 | V4.1-Flash 模型（待确认 model_name） |
| B3 | 诊断分离口径（三分类+去重已做，是否达标） |

### C. 产品向功能（代码核实无实现）
| # | 项 | 现状 |
|---|---|---|
| C2 | 审批增强（批量 / 加签 / 转办 / 意见模板） | ✅ 2026-09-12（批量通过/拒绝、加签、转办、意见模板） |
| C3 | 知识库治理（命中率 / 失效 / 导入导出） | ✅ 2026-09-12（命中统计、失效停用、导入导出、统计卡） |
| C4 | 报表 / 导出 / 定时推送 | ✅ 2026-09-12（趋势/成本 CSV 导出 + 报表定时推送） |
| C5 | AI 反馈闭环 / 成本看板 | ✅ 2026-09-12（AI 运行/成本看板 Tab + 诊断有用/无用反馈） |
| C6 | 值班换班 / 代班 / 日历 | ✅ 2026-09-12（值班日历 + 换班/代班覆盖） |

### D. 可选安全加固
- D1 飞书 `X-Lark-Signature`（Encrypt Key 模式）验签 — ✅ 2026-09-12 已做（`encrypt_key` 字段 + 签名校验 + AES 解密；空则回退 token 模式）
- D2 告警去重 DB 层兜底（水平扩容）— ✅ 2026-09-12 已做（`alerts.dedup_key` STORED 生成列 + 唯一索引 + Go 侧 duplicate 合并）

### F. 已修 Bug（2026-09-12）
| 编号 | 内容 | 状态 |
|---|---|---|
| B1 | **集群连接检查失败时不写回状态**（UAT 明明连不上却显示 healthy）→ 已改为成功/失败都写回 + 连续 2 次才判失败 | ✅ |
| B2 | **同步 gRPC 跑在 async 事件循环**（`query_prometheus`/`collect_quota`/`execute_plan` 等）→ 一个慢调用（最长 30s）把整个 API 锁死（实测 14:07 起无响应、SIGTERM 也杀不掉，只能 kill -9）→ 新增 `executor_client.acall()`，39 处调用改写 + 3 个生成器循环改 `asyncio.to_thread` | ✅ |

### E. 环境/数据问题（2026-09-12 发现）
- E1 ~~**UAT 集群（cluster 2）快照/指标自 09-11 18:09 停更** → 需排查采集/连通~~ ✅ 2026-09-12 定位：UAT API `10.10.75.253:6443` 网络不可达（非平台问题）；已修健康检查失败不写回状态的 Bug，并加大屏“数据陈旧”标记
- E2 executor 指标采集疑似单位 bug（个别 Pod `cpu_usage` 报 5 万+ 核）→ ✅ 2026-09-12 已修：根因=metrics-server 偶发返回异常 CPU（rocketmq-broker-0-master-0 实测最高 440 万核，非代码单位 bug，`AsApproximateFloat64` 本身正确）；在 collector.go 加脏数据防护（单 Pod CPU>256 核 / 内存>2TiB 置 0 + 告警日志），Go 重编译重启。历史脏数据保留（api-server 侧已过滤）。

> 数据保留：已有 `cleanup_old_snapshots(retention_days=7)`（启动+每日），非缺失项。

---

## 七、环境备注（2026-09-12）

- 本机 IP = `192.168.1.10`；`192.168.6.99`（MySQL/Redis）、`192.168.6.36` 均 **不可达**（无路由，疑似 VPN/网络切换）→ 与平台代码无关。
- 本地四服务（api-server:8080 / ai-service:9000 / executor:9090 / vite）**当前全部未运行**。
- 项目无 git 仓库（改动未纳入版本控制）。

---

## 八、2026-09-15 本轮（环境恢复：四服务已启动）

> 本地环境已恢复（MySQL/Redis `192.168.6.99` 可达），四服务已起：api-server:8080 / ai-service:9000 / executor:9090 / vite:5173。

### 8.1 已修 Bug

| 编号 | 内容 | 落点 | 状态 |
|---|---|---|---|
| **B3** | **应用诊断「新建诊断会话」点确定后窗口不关，但会话已建成**。后端在 `POST /agent/sessions` 里同步跑完首轮诊断（KB 预检 + 3 个取证工具 + LLM，实测 145s），前端 axios 超时 30s 先中止 → `await createAgentSession()` 抛错 → `setModalOpen(false)` 执行不到。实测 09:35 连点 4 次生成 12–15 号同内容重复会话 | `api-server/app/api/agent.py`、`frontend/src/pages/AgentChat.tsx` | ✅ **方案A**：创建接口只建会话并立即返回（新增 `diagnosing` 标记），首轮诊断 `asyncio.create_task` 后台执行（带异常兜底 + 任务引用集合防 GC）；前端拿到 id 立刻关窗 + 打开会话 + 每 2.5s 轮询 `/messages` 直到出 assistant 结论（切走自动停）；`handleCreate` 加 try/catch，失败保留窗口可重试 |
| **B4** | **深度诊断动不动就「诊断未完成（达到最大工具步数 12）」**。实测会话 12：12 步里 5 步是同一工具同参数的重复调用（被去重拦截但**仍占轮数**）、2 步参数类型报错（`{"tail":"100"}` 期望 int）、2 步容器内命令不存在，有效信息只有 5 步 → 模型始终不收敛 | `api-server/app/services/agent_service.py`、`app/services/tool_registry.py` | ✅ 三修：①`used_steps` 只在真实执行时 +1，**重复调用不占额**；连续 2 次重复（`MAX_CONSECUTIVE_DUP`）强制收敛；`MAX_DECIDE_ROUNDS=24` 防其他空转 ②新增 `coerce_args()` 参数类型容错（`"100"`→`100`、`"5"`→`5`、`"true"`→`True`），`execute_tool` 校验前归一化，编排循环用归一化后的参数做去重键；`list_tools()` 给 LLM 的工具描述带上「类型：int，必填」 ③剩余预算 ≤2 步注入收敛提示，循环结束仍无结论时追加一次「强制收敛」LLM 调用；兜底文案带真实原因（重复调用/未知工具/决策失败）|
| **B5** | **审批列表出现不该有的「审核超时」**：人工协助单（`manual_assist`）关联告警已自愈，单据仍一路升级到 `timeout` | `api-server/app/services/approval_service.py`、`app/services/alert_auto_service.py` | ✅ 根因两条：①`_cancel_related_approvals()` 只 `JOIN repair_plans ON id = approvals.plan_id`，而人工协助单 `plan_id` 为 NULL → **被 inner join 整个漏掉**（该函数本就是「资源已恢复→取消 pending 审批单」用的）②`k8s-executor` 强删卡死 Pod 后**直接写库** `alerts.status='self_healed'`（`pod_watcher.go:584`），api-server 完全感知不到，且 `TerminatingStuck` 不在 `POD_RECOVERABLE_FAULTS` 里，恢复关闭路径也不会跑。修：①`_cancel_related_approvals` 改 `outerjoin` + `or_(RepairPlan.alert_id, Approval.alert_id)` 覆盖人工协助单 ②`timeout_handler` 每轮先查关联告警，`self_healed/resolved` 则置 `cancelled`（原因写「关联告警已恢复」）而非 `timeout`；日志加「恢复取消 N 条」。历史脏数据已补偿修正 4 张（#17/18/24/28），备份 `logs/archive/approval_timeout_fix_backup_20260915.json` |

| **B6** | **审批终态未同步关联告警 → 僵尸单**（原计划 P5）。两类：①人工协助单整轮超时后告警仍挂 `awaiting_approval`，但已无 pending 单可审 → 从「待审批」列表消失又回不到自动处理池（告警 309）②人工协助单已 `approved` 但告警仍挂 `awaiting_approval`（告警 308，`execute_approved_plan(None)` 是空跑）| `api-server/app/services/approval_service.py` | ✅ 新增 `_settle_alert_for_manual()`：人工协助单 `approved`（值班人已受理）/`rejected`（人工裁定无需处理）→ 关联告警置 `resolved` + 记 `resolved_by` + 留言留痕；`approved` 且无 plan 时不再调 `execute_approved_plan(None)`。实践验证：老板拒绝 #36（告警 309）→ 告警自动置 `resolved` |
| **B7** | **审批不该有「超时」**（老板 09-15 口径）：人工协助未恢复又没人处理 → 值班人 **40 分钟**未处理 → **通知所有人**，但**不超时**；之后服务自愈 → **审批流程解除**。原实现会一路升级到 `timeout` 把单子关掉 | `api-server/app/services/approval_service.py`、`app/models/approval_flow.py`、`frontend/src/pages/ApprovalFlows.tsx`、`pages/ApprovalList.tsx`、`components/StatusTag.tsx` | ✅ ①`timeout_handler` 重写：**取消 `timeout` 状态**；当前节点到「提醒时间」→ 推进下一节点并重新通知；到**最后一节点**后保持 `pending`（不再提醒、不判超时）；关联告警自愈/已解决 → `cancelled`（解除）②默认流程：值班人 20→**40** 分钟、末节点不再参与推进；③前端：审批列表 `cancelled` 显示「**解除**」（`StatusTag` 加 `label` 可选项）；审批流程页「超时(分钟)」→「**提醒时间(分钟)**」+ 说明文案；④历史 `timeout` 单 #27 → 解除；⑤删除上轮加的「超时重开单」逻辑（已无超时，无需重开）|

### 8.2 验证与回归

- 新增单测 10 个（`api-server/tests/test_units.py`）：`coerce_args` 7 项 + 深度循环预算行为 3 项（重复不占额 / 预算用尽强制收敛 / 收敛失败兜底带原因）。
- `./scripts/ci_check.sh` 全绿：py_compile ×2 / frontend tsc / k8s-executor go build / 单测（api-server 17、ai-service 5）/ 跨语言加密互测。
- B5 用真实库双路径验证：`timeout_handler` 与 `_cancel_related_approvals` 各跑一遍，已自愈告警的 pending 单 → `cancelled`；未恢复告警（对照组）保持 `pending`。
- B6 用真实库验证：告警 308（审批已通过）→ `resolved`；告警 309（超时无单）→ 重开 pending 单 #36；后续老板拒绝 #36 → 告警 309 自动 `resolved`（新逻辑生效）。
- B7 用真实库双场景验证：A) 单子已在末节点、3 小时前通知过 → 保持 `pending`（**不判超时**）；B) 单子在值班人节点、41 分钟前通知过 → 自动推进到节点 2（@群所有人）并重新通知。均已断言通过。

### 8.3 待办 / 待确认（2026-09-17：P1 老板定不做，P2/P4/P6 已做）

| # | 事项 | 说明 |
|---|---|---|
| **P1** | 真实 E2E 验证 | ❌ 老板 09-17 定「不做了」 |
| **P2** | `MAX_TOOL_STEPS` 可配置 | ✅ 2026-09-17：新增 system_settings `agent_max_tool_steps`（默认 12，1~100 限幅）+ `agent_service._get_max_tool_steps()` 读配置 + 前端「AI 诊断」Tab 可调 |
| **P3** | ~~历史重复诊断会话清理~~ | ✅ 2026-09-15 已清 8 个会话 / 77 条消息（4–11 号），备份 `logs/archive/diagnosis_sessions_backup_20260915_4-11.json`（⚠️ 12–15 号的备份文件被同名覆盖丢失，均为重复垃圾数据）|
| **P4** | 处理记录把「自愈」显示成「审核」 | ✅ 2026-09-17：`repair_records.py` 改为自愈优先 + 忽略 `cancelled` 审批单；实测 3 条自愈告警（297/298/307）原误显「审核」现正确显示「自愈」 |
| **P5** | ~~超时后告警僵尸态~~ | ✅ 2026-09-15 已处理（见 B6/B7）|
| **P6** | 后台 30s 循环被慢任务拖后 | ✅ 2026-09-17：`main.py` 拆分为独立循环（审批超时 30s/60s 超时、告警自动处理 30s/150s、日志告警扫描 30s/120s）+ 独立维护循环；`lock:alert_auto` TTL 30s→300s 防重叠 |
| **P7** | ~~人工协助单终态语义确认~~ | ✅ 2026-09-15 已明确：`approved`/`rejected` → 告警 `resolved`（留痕）；审批**无超时**，仅 40 分钟未处理时通知所有人（见 B7）|
| **P8** | ~~`DecisionPolicy.approval_timeout` 字段已无实际作用~~ | ✅ 2026-09-15 已清理：删除模型字段 + `decisions` API 白名单/响应 + 前端列表列/表单项；迁移 `e0f1a2b3c4d5_drop_decision_approval_timeout`（`upgrade head` 已执行，列已删），备份 `logs/archive/decision_policies_backup_20260915.txt` |

### 8.4 本轮数据/配置操作（均已备份）

| 操作 | 说明 | 备份 |
|---|---|---|
| 默认审批流程调参 | 值班人 20→**40** 分钟；末节点 `timeout` 30→**0**（不再参与推进） | `logs/archive/approval_flow_backup_20260915.json` |
| 在途单 #39 快照刷新 | 与上述流程对齐（20/30→40/0），避免旧快照提前提醒 | — |
| 历史 `timeout` 单 #27 | 按「审批无超时」语义置为解除 | `logs/archive/approval_timeout_legacy_backup_20260915.txt` |
| 历史误报 timeout 单 #17/18/24/28 | 关联告警已自愈 → 解除 | `logs/archive/approval_timeout_fix_backup_20260915.json` |
| 告警 308/309 僵尸态 | 308 → resolved；309 → 重开 #36（后被老板拒绝→ 自动 resolved） | — |
| 历史重复诊断会话 4–11 | 删除 8 单 / 77 条消息 | `logs/archive/diagnosis_sessions_backup_20260915_4-11.json` |
| 迁移 | `e0f1a2b3c4d5`（删除 `decision_policies.approval_timeout`）已 `upgrade head` | — |

---

## 九、2026-09-17 老板新增需求（待排期）

> 老板原话 8 条，按模块归并。状态：待确认/待开发。

### A. 告警处理 · 审批列表
| # | 需求 | 说明 / 现状 |
|---|---|---|
| 1 | 同一 pod 的审批去重 | 现状：`list_approvals` 按 `Approval` 逐条返回，同一 pod（同一告警）的多张审批单会重复罗列。需按 pod/resource 去重，只保留最新一张（或合并展示） |
| 2 | 审批列表翻页 | 现状：无分页，一次性拉全量。需加 `page/page_size/total` + 前端翻页 |
| 6 | 人工协助/处理记录闭环修复（3 子项） | ① 人工协助单：人工未处理、告警已恢复 → **提醒应解除**（审批置 cancelled）。此点在 B5/B7 已做过，老板反馈仍不生效，需重新核查触发路径；② 已处理计划应**完整**，不能断断续续（修复计划/执行步骤展示不完整）；③ 处理记录里「审核人/处理人」字段断断续续（部分行空） |

### B. 仪表盘
| # | 需求 | 说明 / 现状 |
|---|---|---|
| 3 | 去掉「告警状态」板块 | 该板块展示正常/异常（已解决/已自愈），与上面的「审批流水」信息重复，删除 |
| 4 | MTTR 释义（问答） | **MTTR = Mean Time To Repair（平均修复时长）**。当前算法：近 24h 内已解决告警的 `resolved_at − detected_at` 取平均值，单位分钟（`stats.py` mttr_minutes） |
| 5 | 集群概览 pod 数/负载不准 | 现状：pod 数取 `resource_snapshots`「每集群×资源类型最新一次快照」的 pod 计数，但该快照是**分批落库**的，最新快照批次可能只含部分 pod → 计数在 60~100 间波动；真实集群 ~782。负载（CPU/内存）同理受采样影响。需改用真实采集口径（如 executor 实时工作负载数/节点资源） |

### C. 数据库诊断
| # | 需求 | 说明 / 现状 |
|---|---|---|
| 7 | 数据库采集指标多样化 + 巡检报告 + 开关 | 现状：`db_collector` 健康采集仅连接数（used/max/usage_pct）。需扩展：当前连接数、活跃连接数、连接使用率、QPS、TPS、慢查询、缓存命中率等；支持生产库巡检报告内容可配；数据库巡检增加开关 |

### D. 集群管理
| # | 需求 | 说明 / 现状 |
|---|---|---|
| 8 | 集群巡检 + 自动生成巡检报告 + 开关 | 现状：无集群巡检功能（`inspection` 无实现）。需新增：集群巡检（节点/资源/异常等体检），可设置自动生成巡检报告，巡检报告加开关 |

### 待老板拍板/确认
- #5：确认「真实 pod 数」以哪个口径为准（`kubectl get pods -A` 全量 782？），以便对齐概览卡数字。
- #6-②：「已处理计划断断续续」具体指哪里的展示（执行详情步骤？处理记录？），需给个截图/示例定位。
- #7/#8：巡检报告是否需要定时（cron）自动生成并推送？还是仅手动触发 + 界面查看？开关是全局还是按库/按集群？
- #1 去重口径：按「告警/pod」去重，还是按「资源名+命名空间」去重？

---

## 2026-09-18 落地记录（老板拍板后全部完成）

> 拍板口径：① pod 数用 `kubectl get pods -A` 全量 ② #6-② 指处理记录 ③ 巡检报告按库开关 ④ 集群巡检按集群开关、不定时推送。

| # | 需求 | 状态（09-18） |
|---|---|---|
| 1 | 审批去重 | ✅ 09-17 已做（命名空间+资源名去重） |
| 2 | 审批翻页 | ✅ 09-17 已做（page/page_size/total） |
| 3 | 去「告警状态」板块 | ✅ 09-17 已做（Screen.tsx 无此板块） |
| 4 | MTTR 释义 | ✅ 问答：近24h已解决告警 resolved_at−detected_at 均值（分钟） |
| 5 | pod 数/负载真实口径 | ✅ executor 新增 `_pods_` 快照（kubectl get pods -A 全量），stats.py 读它；实测集群1=778 |
| 6-① | 人工协助恢复解除 | ✅ 根因=node_watcher 无恢复关闭逻辑；新增 resolveNodeFaultAlerts，实测告警425→resolved、审批134→cancelled |
| 6-② | 处理记录计划完整 | ✅ repair_records 返回 plan_steps+execution_steps，前端展开行展示 |
| 6-③ | 审核人/处理人补全 | ✅ resolved+manual 且空时从审批单取审批人 |
| 7 | 数据库指标+巡检开关 | ✅ 加活跃连接数/QPS/TPS/缓存命中率 + DBConnection.inspect_enabled（按库） |
| 8 | 集群巡检+开关 | ✅ 新增 inspection_service + Cluster.inspect_enabled（按集群，不定时推送） |

迁移：`f1a2b3c4d5e6_add_inspect_enabled`（已 upgrade head）。


---

---

## 2026-09-18 新增需求（讨论定稿）

> 巡检定时补充：`Cluster`/`DBConnection` 加 `inspect_cron`（可设置定期，空=仅手动点击巡检）；迁移 `f2b3c4d5e6f7`。
> 仪表盘改造详见 `docs/DASHBOARD-V2-REQUIREMENTS.md`。

### A. 仪表盘改造（Dashboard v2，改造现有 `/dashboard`）
- **布局 6 段**：① 顶部（项目/集群筛选·时间范围·刷新·全局健康分）② KPI 7 项 ③ 告警趋势 + AI 诊断结果分布（环形）④ 实时告警流 + Top 告警类型 ⑤ 异常 Pod Top N + 数据库健康概览 ⑥ 自动处理动作统计·时间线 + 集群资源水位 Top
- **刷新**：前端 5s / 后端缓存 15s
- **全局健康分**：集群健康 40% + 活跃告警 30% + DB 健康 30%
- **KPI 口径**：自动处理=近一年累计；其余当前态，成功率/MTTR 随时间范围
- **异常 Pod** = 有告警的 Pod（含刚重启）；**移除**「项目健康评分」；项目不做告警

### B. 待办需求
| # | 需求 | 说明 |
|---|---|---|
| N1 | 工单 | 告警/审批转工单（派基础设施团队 / 对接外部工单系统） |
| N2 | 深层次重复实现收敛 | infer_deployment_name×3、ExecutorClient()×13、值班 Tab、IM 发送层、AI JSON 解析 |
| N3 | 诊断分离口径确认 | 故障/应用诊断三分类是否达标 |
| N4 | 健康检查 readiness/liveness | 拆就绪/存活探针 |
| N5 | 异常 Pod 重启原因 AI 诊断 + 告警通知 | RESTARTS 增加（OOM/探针，排除手动）→ 自动 AI 诊断 → 诊断结果告警通知（不审批） |
| N6 | DB 报错告警接入告警策略 | 数据库报错（连接失败等）走告警策略 |
| N7 | 日志源自定义（多实例） | 支持自定义多个日志源（ES 地址/索引/账号），不强绑环境；日志告警规则可选日志源；兼容现有全局 ES |
| N8 | 统一告警通知策略 | 集群告警 + 日志告警全部走同一套告警通知策略（全平台统一一份） |

---

## 2026-09-20 老板拍板（三项待确认已定）

| # | 事项 | 结论 |
|---|---|---|
| N1 | 工单 | **平台内部工单**；外部系统**仅接入**（查询/创建）；**报警与工单机制不变**。详见 `docs/PENDING-REQUIREMENTS-DETAIL.md` 第二部分 N1（3 天） |
| N3 | 诊断分离 | **集群故障 / 应用故障 / 数据库故障 三类分开**（入口、数据、知识库、报告、权限）。详见 PENDING 文档 N3（2.5 天） |
| — | V4.1-Flash 模型 | 老板称**本地已有**；待补：地址 / 模型名 / 协议(是否 OpenAI 兼容) / 是否需 key / 用途 |

> 详细需求描述与工时：`docs/PENDING-REQUIREMENTS-DETAIL.md`
> 2026-09-20 已 `git init` + 首次提交（基线 352 文件），改动纳入版本控制。


## 2026-09-20 开发进度

| 项 | 状态 |
|---|---|
| 仪表盘 v2（改造 /dashboard） | ✅ 已完成（后端 `/stats/dashboard` 扩展 + 前端 `pages/Dashboard.tsx` 6 段布局 / 5s 轮询；/screen 大屏与 /stats 保留）。验证：接口实测 200（健康分/KPI/趋势/AI分布/异常Pod/DB健康/动作统计/资源水位全有真数据）；`tsc -b` 0 error、`vite build` 通过 |
| git 基线 | ✅ `git init` + 首次提交（352 文件），后续改动逐次提交 |
| 待补 | 本地 Flash 模型接入参数（地址/模型名/协议/key/用途） |
| 待确认口径 | ①「自动处理」按执行动作数统计（镜像修复/纯恢复自愈不落日志→偏低），是否改按告警条数 ②「诊断失败」现映射为 pending 未转人工，是否单独记录 ③ project_id 过滤后集群数=0（项目是否未绑资源） |

### N4 健康检查 readiness/liveness 拆分 ✅ 2026-09-20
- api-server：`/health` 为存活（不查依赖）；新增 `/ready` 查 DB+Redis，异常返回 503
- ai-service：新增 `/live`（存活）与 `/ready`（查 DB + 已配置则查 Redis），中间件放行探活路径
- `k8s/deployments.yaml`：api-server（live `/health` / ready `/ready`）、ai-service（live `/live` / ready `/ready`）、k8s-executor（补 liveness tcpSocket）、frontend（补 liveness/readiness httpGet `/`）
- 实测：api-server `/health` 200、`/ready` 200(database/redis=true)；ai-service `/health` `/live` `/ready` 均 200

### N6 数据库报错接入告警策略 ✅ 2026-09-20（待执行迁移）
- 新增 `app/services/db_alert_service.py`：每 60s 只读探活 → 失败建告警（`DBConnFailed`/`DBAuthFailed`，`resource_type='db'`，`manual_required=True`）→ 复用 `notification_service.send_alert_notification` 走同一通知/策略；恢复 → 自动 `resolved` + 复用 `_cancel_related_approvals` 解除审批
- `alert_auto_service._process_one`：`resource_type=='db'` 跳过自动修复（红线：只给方案不执行）
- 默认告警策略加 `DBConnFailed`/`DBAuthFailed`（category B）
- **迁移 `g1b2c3d4e5f6`（未执行）**：`alerts.cluster_id` 改可空 + 新增 `alerts.source` + 重建 `dedup_key` 生成列（原表达式对 db/log 撞唯一索引）
- 顺带修复：`Alert.dedup_key` 生成列被 ORM 当可写列（改 `server_default=FetchedValue()`），否则日志/资源类告警建单会失败
- 前端：`AlertList` 对 `resource_type='db'` 隐藏 Pod 专属按钮
- 验证：py_compile ✅ / tsc ✅；临时不可达 DBConnection 端到端实测（建单→去重→通知→恢复关闭→DBAuthFailed 分类）后**测试数据已清理**；「本机mysql」无误报

### 大屏与仪表盘统一 ✅ 2026-09-20（老板：大屏要和仪表盘一样）
- `/screen` 直接复用 `Dashboard` 组件（新增 `screen` 属性：无侧栏/顶栏框架，自行补边距与底色铺满视口），**内容/布局/数据/5s 轮询与 /dashboard 完全一致**
- 旧独立大屏实现 `pages/Screen.tsx`（深色/单屏/15s/轮播）已移除
- 验证：`tsc -b` 0 error、`vite build` 通过

### B8 重复建单修复 ✅ 2026-09-20（当日发现当日修）
- 现象：告警 502（TerminatingStuck）每 30s 被后台扫描重新决策 → 累计 101 张审批单（99 张重复）
- 根因：`auto_process_alerts()` 选取条件含 `awaiting_approval`，`_process_one` 对「待审批」告警又跑一遍风险决策并 `create_approval`，无幂等保护
- 修复（`alert_auto_service.py`）：① 待审批告警若已有在途（pending）审批单 → 直接跳过（恢复关闭逻辑在此之前，不受影响）② 建单前二次校验已有在途单则不再建，仅对齐告警状态
- 数据修正：502 的 101 张单 → 2 张 approved + 99 张 cancelled；告警 502 → resolved。备份 `logs/archive/approvals_alert502_dups_backup_20260920.json`
- 验证：api-server 重启后 >45s 无新增审批单，pending 审批 0

### 仪表盘 KPI 口径修正 ✅ 2026-09-20
- 问题：`auto_processed_1y` 原按执行日志**全部状态**计数（含 184 条 failed）→ 「自动处理 195」虚高
- 修正：`action_stat` 只统计**成功执行**的动作；失败单列 `action_failed`；`auto_processed_1y` = 近一年「自愈告警数 ∪ 有成功执行动作的告警数」（按告警去重）
- 实测：auto_processed_1y 195 → **144**（142 自愈 + 2 有成功执行动作）；action_stat self_heal=15；action_failed=182
- 项目筛选核查：项目 1 不存在（库里唯一项目 = #8 demo，绑 cluster 2 / ns jenkins）→ 之前「project_id=1 集群数 0」非 bug
- 待老板确认：是否改为纯「自愈告警条数」口径

### N5 异常 Pod 重启原因 AI 诊断 + 告警通知 ✅ 2026-09-20
- executor（Go）：新增 `ListPodRestartInfo` RPC + `internal/executor/pod_restart.go`（全集群 Pod 每容器重启计数 + `lastState.terminated.reason` + `restartedAt` 注解）
- api-server：新增 `pod_restart_service.py`（60s 扫描：重启增量检测 → 原因判定 → 建单 → AI 诊断 → 通知 → 恢复关闭）+ `executor_client.list_pod_restart_info` + 后台循环注册
- 规则：增量对比（Redis 基线）；**排除**手动重启（restartedAt 注解）与首次发现的新 Pod（滚动发布）；OOMKilled → `PodRestartOOM`，探针失败 → `PodRestartProbeFail`；按 deployment 聚合去重；**不建审批单**；`alert_auto_service` 显式跳过这两类
- 默认策略补 `PodRestartOOM`/`PodRestartProbeFail`（B 类，high）
- 验证：go build / py_compile ✅；executor 重启后 RPC 正常（集群1 781 Pod、310 有重启；集群2 95 Pod、4 有重启）；无 UNIMPLEMENTED；扫描循环已启动；首轮只建基线不报（符合预期，无告警风暴）
- 已知小缺口：Pod 被删除/缩容时其在途告警不会自动关闭（后续可补「快照外 Pod 告警回收」）

### N7 日志源自定义（多实例）+ 与系统设置去重 ✅ 2026-09-20
- 新增 `log_sources` 表（name/es_url/index_pattern/username/password_encrypted/is_default/enabled）+ CRUD/测试连通性 API + 前端「日志源」页（策略配置 → 日志源）
- 日志告警规则加 `log_source_id`（空=默认源）；日志查询支持 `log_source_id`
- **去重（老板口径）**：系统设置「可观测性」里的 ES 四项退休（前端表单移除、后端 DEPRECATED_KEYS 只读兜底）；首次读取日志源列表时**自动把 system_settings.es_* 导入为「默认日志源」**；解析顺序 log_sources.is_default → 回退 system_settings（存量规则/查询不受影响）
- 密码 AES-256-GCM 加密存储，列表只回 `has_password`
- 迁移 `h1i2j3k4l5m6` **已执行**（head）；实测接口 200 且已自动生成默认源（127.0.0.1:9200 / k8s-* / demo）
- 备注：`/logs/search` 由代理 ai-service 改为 api-server 直查 ES（支持多源；结果一致）

### N8 统一告警通知策略 ✅ 2026-09-20
- 统一入口 `notification_service.notify_alert(alert)`：策略开关 → 分级渠道（severity_routes）→ 静默/维护窗口 → 限频(30min) → 风暴抑制(5min 同源≥5) → 模板渲染 → 发送+写日志；旧 `send_alert_notification` 保留为一行兼容转发
- 全平台一份策略存 `system_settings.alert_notify_policy`（JSON，不新增表）；接口 `GET/PUT /notifications/policy`；前端「通知配置」顶部新增统一策略卡片（分级渠道 + 静默窗口）
- 五类告警全部改走统一入口：集群/容器（转人工路径）、**日志告警（原来建单后不通知，现补上）**、数据库、Pod 重启、容器资源
- `im_template` 模板开始对告警生效（新增 `{source}`/`{alert_id}`，渲染失败回退默认）；集群 `maintenance_window` 存量字段被真正消费
- 验证：py_compile ✅ / tsc ✅ / 接口 200（策略返回）/ 4 类来源 dry-run 命中渠道数与静默/限频断言通过
- 未做（扩展点）：邮件/站内消息渠道未接线

### N1 补充（工单）→ 见下节
### N3 诊断分离 ✅（清单见 e4ddfb2 提交）

### N1 补充 ✅ 2026-09-20
- 工单支持关联**数据库连接**（`tickets.db_connection_id`，迁移 `k4l5m6n7o8p9` 已执行）：新建工单可选数据库；可见性/详情按 `check_db_permission` 收敛（与集群权限并列）；列表/详情展示库名
- 新增**删除工单**（仅 admin，连带删除留言；不影响关联告警/审批）：`DELETE /api/v1/tickets/{id}` + 前端详情抽屉删除按钮（admin 可见）
- 实测：建单（带 db=2）→ 详情显示「本机mysql」→ 删除 → 404，链路通过
- 仍未做（扩展点）：外部系统 token 接入（查询/创建）

---

## 2026-09-20 收尾：本轮全部需求完成

| 项 | 状态 |
|---|---|
| 仪表盘 v2（/dashboard） | ✅ |
| 大屏 = 仪表盘（/screen 复用同一组件） | ✅ |
| N4 探针拆分 | ✅ |
| N5 异常 Pod 重启原因 AI 诊断 + 通知 | ✅（含 Pod 回收） |
| N6 DB 报错接入告警策略 | ✅ |
| N7 日志源多实例 + 与系统设置去重 | ✅ |
| N8 统一告警通知策略 | ✅ |
| N1 平台内部工单 | ✅（含 DB 关联 + 删除） |
| N3 诊断三类分离（集群/应用/数据库） | ✅ |
| B8 重复建单修复 | ✅ |
| 迁移 | `g1b2c3d4e5f6` / `h1i2j3k4l5m6` / `i2j3k4l5m6n7` / `j3k4l5m6n7o8` / `k4l5m6n7o8p9` 全部已执行，head = k4l5m6n7o8p9 |
| 未排期 | M1~M12 建议项（已采纳未排期） |

### Embedding 配置能力就绪（老板：本地暂无 embedding，先把配置开发好，以后可直接配）✅ 2026-09-20
- 配置入口：平台设置 → Embedding 配置（供应商 / API 地址 / 模型名 / API Key），存 system_settings，**改完立即生效**（保存后自动通知 ai-service 清缓存，不用等 60s）
- **API Key 可选**：本地/内网 embedding 服务（Ollama/vLLM/LM Studio 等）无密钥也能用
- 新增**连通性测试**：`POST /api/v1/settings/embedding/test`（支持「先测再存」传未保存配置），前端 Embedding 页有「测试连通性」按钮 + 已配置/未配置状态提示
- **优雅降级**：未配置时知识库向量检索**自动跳过并返回空**（不再 503 打断诊断主流程）；显式调用仍返回明确指引文案
- 修 bug：embedding 请求走了**系统代理**（macOS 系统代理导致 127.0.0.1 请求被代理返回 502）→ 改用共享客户端 `trust_env=False` 内网直连
- 修 bug：知识库**按诊断域过滤**的相似度掩码写法错误（负数相似度 + 乘法掩码导致排序错乱、跨域误命中）→ 改为 `-inf` 屏蔽；实测 app 域命中、cluster 域 0、不传类别正常
- 验证：用本地 mock OpenAI 兼容 /embeddings 服务端到端跑通「配置 → 测试(维度 8) → 新增带向量知识 → 分域检索 → 恢复空配置 → 降级返回空」；测试数据已清理，配置已还原为空
- 备注：DeepSeek `deepseek-v4-flash` 模型**已在平台配置好**，Flash 接入需求关闭

### 容器实时日志开关 ✅ 2026-09-20
- 位置：集群管理 → 集群详情 → 工作负载 → Pods 列表 → 「日志」抽屉
- 新增开关「实时查看 stdout」**默认关闭**；开启后**每 2 秒**自动拉取容器标准输出（/dev/stdout）最新日志（tail 500），可开「自动滚到底部」；关闭或退出抽屉即停止轮询
- 后端：`/api/v1/resources/clusters/{id}/namespaces/{ns}/pods/{pod}/logs` 增加 **tail 限幅 1~2000**（防实时轮询打爆执行器）；沿用集群/ns 读权限，只读
- 实测：真实 Pod 拉到 stdout 日志 20 行；tail=99999 被限幅仍 200；指定容器/previous 参数正常
- 实现说明：采用「定时拉取最新 N 行」而非 WebSocket 长连接（无需改 gRPC/proto，稳定、开销可控）；如需毫秒级逐行推送，后续可加 gRPC server-streaming + SSE

### 容器监控增加入站/出站流量 ✅ 2026-09-20
- 位置：集群管理 → 工作负载 → Pods → 「监控」抽屉（标题改为「容器监控」）
- 新增 **入站流量 / 出站流量** 两个速率曲线（KB/s~MB/s 自动换单位）+ 各自「区间累计」总量
- 口径：`pod_metrics.network_rx/tx` 是**累计字节**，前端按相邻采样桶差分换算速率（计数器回绕/重启导致的负增量视为 0）；采样桶 1h=1min、24h=5min、72h=15min
- 后端接口已有该字段（无需改动采集/模型）；实测 1h 窗口 45 个点，速率与区间累计计算正确
- 可扩展（待老板确认）：容器资源告警策略目前只支持 CPU/内存阈值，如需「网络出入站超阈值告警」可加 metric_type

### 容器资源告警支持网络入站/出站阈值 ✅ 2026-09-20
- 新增指标：`net_rx`（容器入站流量，MB/s）、`net_tx`（容器出站流量，MB/s）；PromQL 用 cAdvisor 计数器 rate → MB/s
- 新 fault_type：`ContainerNetRxHigh` / `ContainerNetTxHigh`，加入 `RESOURCE_ALERT_FAULT_TYPES`（只通知、不自动修复）
- 阈值字段复用 `resource_alert_rules.threshold_pct`（百分比指标单位 %，网络指标单位 MB/s —— 前端按指标类型动态切换单位与提示）
- 告警文案按指标带单位（如「入站流量（MB/s） 当前 42.5 MB/s，超过阈值 5.0 MB/s」）；恢复文案统一「指标已回落，系统自动关闭」
- 前端「策略配置 → 容器资源阈值」：指标下拉新增两项，列表显示中文指标名 + 对应单位阈值
- 验证：假 Prometheus 响应端到端跑通（建单→文案正确→恢复自动关闭→清理干净），未发真实通知

### 日志源支持「不同结构的 ES」做日志告警 ✅ 2026-09-20（老板口径：多 ES 源 + 告警即可，容器日志是另一回事）
- **背景**：原日志告警查询把字段写死成 k8s 格式（`@timestamp` / `kubernetes.pod_name` / `kubernetes.namespace_name`），
  换成别的 ES（如应用操作日志索引 `eft_opt_log`，时间字段 `finishTime`、正文是 keyword）会**查不到数据**
- **新增每源字段映射**（迁移 `m6n7o8p9q0r1`）：`time_field`（时间字段，默认 @timestamp）、`namespace_field` / `pod_field`（留空=该 ES 无此维度，不做过滤/聚合）、`message_field`（正文字段）
- **新增关键词搜索字段**（迁移 `n7o8p9q0r1s2`）：`search_field` —— 填了则按该字段做 **wildcard 包含匹配**（适配 keyword 类型正文），留空保持原「全字段 query_string」行为
- 日志查询（查看日志）同步支持：按源的字段映射取值与排序，非 k8s 源不再按 pod 硬过滤
- 前端「日志源」表单新增 4 个字段（带默认值与说明）；日志告警规则仍按 N7 的方式「选源」
- 实测（真实 ES，测试集群 `192.168.6.36:30406`）：
  1. 默认日志源已指到该 ES（原 `127.0.0.1:9200` 不可达），连通性「连接成功，索引可查询」
  2. 用**应用操作日志索引** `eft_opt_log`（时间字段 `finishTime`、搜索字段 `description`）建源 + 规则 → 命中 **13 次** → 自动建日志告警（`LogKeyword`）→ 清理干净
- 结论：**容器日志**看「实时 stdout」（走 k8s API）；**ES 日志告警**现在支持任意结构的 ES 源

### 修复：命名空间选择不持久（操作/返回后回到「全部命名空间」）✅ 2026-09-20
- 现象：集群详情选 kube-system → 对 Pod 操作 → 返回后列表变回全部命名空间
- 原因：命名空间只是页面内存状态，页面重新挂载即回默认 `_all_`
- 修复：选择写入 URL `?ns=`（支持返回/刷新/深链）+ localStorage 记忆（按集群），进页面时自动同步

### 口径修正：不是 AI 处理的都算「已解决」✅ 2026-09-20（老板口径）
- **规则**：只有 **AI 自动修复并验证通过** 才标「已自愈」；其余（资源自行恢复 / watcher 自动处理 / 人工 / 审批解除）**一律「已解决」**
- 代码：
  - `alert_auto_service._mark_alert_recovered` → 统一置 resolved（不再 pending→self_healed）
  - 新增「恢复收敛扫描」（60s）：待处理 / 审批中的 Pod 告警，资源恢复即关闭并解除审批单
  - 执行器 Go：`emitSelfHealed`→`emitAutoResolved`（status=resolved）；TerminatingStuck 强删留痕、node_watcher 恢复关闭 均改为 resolved
  - 处理记录展示：AI 自愈→处理人「AI 自愈」；系统恢复→「系统恢复」
- **历史数据订正**：144 条 self_healed 中，**136 条无 AI 处理痕迹**（无 completed 计划/成功执行）→ 改为 resolved；保留 8 条真 AI 自愈。备份 `logs/archive/self_healed_backfill_20260920.json`
- 仪表盘：
  - 「异常 Pod Top20」只列**未恢复**的（待处理/审批中），不再混入历史状态
  - 「AI 诊断结果分布」改为**当前态 + 随所选时间范围**（历史已关闭的不再堆在环里）

### 状态口径细化：人工审核通过 → 「已审核」✅ 2026-09-20
- 处理记录（处理方式）：新增 `已审核`（人工审核通过）/ `已驳回`（人工裁定不处理）/ `审核中`（在途审批），并带审批人
- 告警列表：状态列对「已解决 + 有关联通过的审批」显示 **已审核**；被驳回显示 **已驳回**（接口新增 `approval_status` 字段，按 alert_id 直连 + repair_plans 关联兜底）
- 完整口径：AI 修复验证通过 →「已自愈」；人工审核通过 →「已审核」；人工驳回 →「已驳回」；资源自行恢复/系统处理 →「已解决」；在途 →「待处理 / 审核中」
- 实测：告警 512/510/508/417 → 处理记录「已审核」（处理人 admin）；告警列表返回 approval_status=approved

### 仪表盘「集群资源水位」新增「负载均衡」✅ 2026-09-20
- 列显示 `LB 数 / 服务总数`（悬停显示「服务总数 N（其中 type=LoadBalancer M）」）
- 数据来源：执行器采集时统计 Service 总数 + `type=LoadBalancer` 数量，写入集群级快照（`namespace='_cluster_'` 的 `service_limit`=服务总数 / `service_used`=LB 数，复用现有字段，无需改表）
- 接口：`/stats/dashboard` 的 `cluster_details` / `cluster_waterline` 增加 `lb_count`、`service_total`
- 实测：测试环境 0/919（无 LoadBalancer 服务）、UAT 2/70

### 仪表盘时间范围改版：10分钟 / 3小时 / 8小时 / 24小时 ✅ 2026-09-20
- 前端 Segmented 选项 `['10m','3h','8h','24h']`，默认 **10m**
- 后端分桶：10m→1 分钟/桶、3h→5 分钟/桶、8h→15 分钟/桶、24h→小时/桶（旧 7d/30d/90d/1y 保留兼容）
- 实测：10m=11 桶、3h=37 桶、8h=33 桶、24h=25 桶，KPI/健康分正常

### 仪表盘时间范围改为「可选时间段」✅ 2026-09-20（老板：不要一行显示，按时间段自己选）
- 前端：`Segmented` → **DatePicker.RangePicker（日期+时间，精确到分钟）**，带快捷预设（最近 10 分钟 / 3 小时 / 8 小时 / 24 小时，选预设会跟随最新自动滚动）；右侧显示当前生效区间，自定义时标注「（自定义）」
- 后端 `/stats/dashboard` 新增 `start` / `end`（YYYY-MM-DD HH:mm[:ss]），与 `range` 二选一时优先用 start/end；趋势/成功率/MTTR/AI 分布均按该窗口统计（含上界）
- 分桶按跨度自动：≤30min→1min、≤6h→5min、≤12h→15min、≤36h→1h、更长→按天；缓存 key 带上区间参数
- 实测：17:50-18:05→16 桶(1min)；10:00-12:00→25 桶(5min)；09-18~09-20→3 桶(按天)；预设 10m/3h/8h/24h 正常

### 仪表盘去掉「项目筛选」✅ 2026-09-20（老板：项目颗粒度太粗，按集群看即可）
- 前端移除项目下拉与相关状态/请求（`getProjects`、`projectId`、queryKey 中的 projectId），保留集群筛选 + 时间段选择
- 后端 `/stats/dashboard` 仍保留 `project_id` 参数（不影响使用，其它调用方可用）
- 实测：全集群 → 水位 2 条（测试环境/uat）；`cluster_id=1` → cluster_total=1、水位 1 条

### 容器日志抽屉支持「下载容器内指定路径的日志文件」✅ 2026-09-21
- **入口**：集群管理 → 工作负载 → Pods → 日志 抽屉，新增「容器内日志文件（只读下载）」区
- 能力：① 输入目录 → **列目录**（可下拉选文件）② 输入/选择文件路径 → **下载文件**（浏览器另存）③ **下载当前 stdout** 日志
- 接口：`GET .../pods/{pod}/files?dir=`（列目录，返回 ls -l 原文 + 解析条目）、`GET .../pods/{pod}/file?path=`（读文件内容，返回 size/truncated/content/filename）
- 安全：走执行器**只读命令白名单**（ls/cat/tail/wc），路径必须是绝对路径且禁止空格/引号/管道等元字符；越权字符直接 400；单次最多 5MB（超出取**末尾** 5MB 并标记 truncated）；沿用集群/ns 读权限
- 顺带修复：执行器 `ExecInPod` 容器名为空时**自动选主容器**（多容器 Pod 否则报 "a container name must be specified"）
- 实测（真实 Pod `qa-eft/efreight-prm-…`）：列 `/var/log` → 7 条；下载 `/var/log/dpkg.log` → 200（10595 字节，内容正常）；不存在文件 → 404 明确提示；`/var/log/x;ls` → 400 拒绝

---

## 2026-09-23 告警↔工单闭环 + 告警合并 + 扫描加固 + 重复收敛（全部完成）

| # | 需求 | 状态 |
|---|---|---|
| 1 | **工单解决/关闭/删除 → 告警自动闭环**（老板口径：告警↔工单是一个闭环，无需在告警处理里再手动「解决」）| ✅ `ticket_service._close_alert_flow`：resolved/closed/删除 关闭「同故障同服务」全部在途告警 + 解除关联审批单 |
| 2 | **已开单抑制**（同故障同服务已有未关闭工单时新告警并入，不重复建单/通知）| ✅ `alert_auto_service._find_open_ticket_for_alert` |
| 3 | **告警合并显示**（同类型同服务只列最新，合并计数）| ✅ `alerts.py group 参数` + `alert_grouping.py` + 前端「同类型×N」标签 |
| 4 | executor TerminatingStuck 扫描加固（分页 List + 重试）| ✅ `pod_watcher.go` |
| 5 | 深层次重复收敛：AI JSON 解析×5 统一、IM 发送层委托 messaging | ✅ `ai-service/app/utils/json_util.py` + `notification_service` |

- 待办账本核对：RAG 向量索引 / CI-CD+日志归档 早在 09-12 已完成（本文件历史条目过期）。
- 数据回填：2 条孤儿 ticketed 告警（#603/#604）→ resolved。
- CI 全绿；四服务已重启。提交 ee58a5f / 031dc2f / 4823f3c / 3ae4eaa。

---

## 2026-10-09 新增需求：集群监控接入 Netdata（草案，待老板评审）

| # | 需求 | 状态 |
|---|---|---|
| 1 | **监控/告警分层+分工（方案 A）**：Netdata 做实时监控展示 + **指标阈值告警**；平台做**事件型告警 + 闭环**；`resource_alert` 规则迁移到 Netdata | 📝 草案 |
| 2 | **开关驱动下发**：集群接入 AIOps 后**默认不装** Netdata；用户打开「Netdata 监控」开关后才向该集群下发（parent+child），mirror `rbac_service` 自举模式 | 📝 草案 |
| 3 | **按集群展示**：集群管理页各集群独立监控入口，iframe 内嵌（后端反代走 apiserver Service Proxy） | 📝 草案 |
| 4 | **parent 用 PVC**；parent+child 同集群一起部署（一份多文档 manifest） | 📝 草案 |
| 5 | **幂等/升级/卸载**：executor server-side apply；卸载随集群删除 | 📝 草案 |
| 6 | **集群外主机（ECS）+ Docker 接入**：新增「主机监控」，同一 agent 覆盖主机+Docker（另立 `hosts` 模型，不走集群接入流程） | 📝 草案 |
| 7 | **Netdata 告警 ↔ AIOps 闭环**：webhook 入站 + 字段映射；**指标阈值告警→Netdata，事件型告警→自研**，统一汇入闭环；诊断/修复两环需新增主机/Docker 能力 | 📝 草案 |
| 8 | **Netdata alarm→fault_type 映射草稿**：`docs/NETDATA-ALARM-MAPPING.md`（webhook 入站转换依据） | 📝 草案 |

关键澄清：
- Netdata **不依赖**平台 Prometheus（自采集自存储）；K8s 场景读 kubelet cadvisor 端点。
- 部署位置：**装在每个被管集群**（物理分开），**动作由 AIOps 自动下发**（体验一起）。
- **不做数据融合**、**不替换 DB 监控/配额快照**；`pod_metrics` **暂不删除**（仍被 AI 诊断工具 `get_pod_metrics` + 仪表盘消费）。

详见：**`docs/NETDATA-MONITORING-REQUIREMENTS.md`**

---

## 2026-10-09 新增需求：项目级审批（审核机制补项目维度）

**背景**：告警**通知路由**已有项目/namespace 维度（`notify_alert` → 项目 `notify_config_id`）；**审核机制没有**（`approval_flows` 全局选默认流程）。老板：告警路由是有的，只是审核机制没有。

| # | 需求 | 状态 |
|---|---|---|
| 1 | **项目绑审批流程**：`projects` 加 `approval_flow_id`，对称通知的 `notify_config_id` | 📝 草案 |
| 2 | `_load_default_flow()` 改为按 `(cluster,ns)→项目→flow`，无则回退默认 | 📝 草案 |
| 3 | 前端项目编辑加「审批流程」下拉 | 📝 草案 |

边界：通知路由/决策引擎不动；只改「审核走哪套流程」，不改「是否要审核」。

详见：**`docs/PROJECT-LEVEL-APPROVAL-REQUIREMENT.md`**

---

## 2026-10-09 待开发需求总清单

本轮讨论（Netdata 监控接入 + 告警闭环 + 集群外主机 + 项目级审批）的全部开发需求已整理成清单：
分 **A. Netdata 接入 / B. 集群外主机(ECS)+Docker / C. 告警↔闭环 / D. 主机/Docker 诊断修复 / E. 项目级审批**，含落点、规模、依赖、建议排期。

详见：**`docs/DEV-REQUIREMENTS-2026-10-09.md`**

---

## 2026-10-12 新增需求：对标 KubeDoor · 短板补齐

**背景**：与开源项目 **KubeDoor**（[CassInfra/KubeDoor](https://github.com/CassInfra/KubeDoor)，2.1，AI 驱动的多 K8S 管控平台）对比后，梳理出 API-OPS 的相对短板；老板 2026-10-12 决定纳入开发计划。

**现状澄清（已具备，不重复开发）**：K8s 资源 CRUD / YAML 编辑、Pod exec + 交互终端、Pod 文件浏览、scale / rollout / recreate / probe、定时任务（`custom_tasks`）、AI 诊断助手会话（`AgentChat`）、告警 / 工单 / 审批 / 审计（哈希链）/ 知识库 RAG / DB 诊断 / 中间件监控、Prometheus 查询、仪表盘实时刷新（SSE + 缓存代次）。

### 需求清单

| # | 需求 | 对应 KubeDoor 能力 | 现状 | 优先级 | 工时 |
|---|---|---|---|---|---|
| **R1** | 高峰期资源治理 + 准入控制(MutatingWebhook) | 核心卖点 | ❌ 空白（准入 0 命中） | **P0** | ~9d |
| **R2** | JVM 深度诊断(Dump/Jstack/JFR) + 参数管控 | JVM 管控 | ❌ 空白（jstack/jfr 0 命中） | **P0** | ~7d |
| R3 | 镜像更新（列仓库标签一键改） | 镜像更新 | ❌ 仅 RepairImagePull | P1 | ~5d |
| R4 | 节点管理 + 节点均衡迁移 | 节点均衡 | ❌ 空白 | P1 | ~6d |
| R5 | 实时日志跟随 + 日志增强 | 实时日志 | ⚠️ 仅历史查询（无 follow） | P1 | ~3d |
| R6 | Deployment 实时状态推送(WebSocket) | 2.1 特性 | ⚠️ 靠轮询 | P1 | ~3d |
| R7 | 多集群统一监控大屏增强 | 资源总览大屏 | ⚠️ 大屏与仪表盘同组件 | P1 | ~3d |
| R8 | Pod 隔离（改标签脱流量） | Pod 隔离 | ❌ 空白 | P1 | ~2d |
| R9 | MCP 标准接口 | MCP 对外 | ❌ 空白 | P2 | ~4d |
| R10 | AI 助手增强（全局记忆/自由指标/断线续传） | AI 助手 | ⚠️ 有会话基础 | P2 | ~4d |
| R11 | 告警屏蔽收尾（Alertmanager 风格） | 告警屏蔽 | ⏸ 方案待开发 | P2 | ~3d |
| R12 | agent 主动外连接入模式 | 不开入站端口 | ❌ 集中连模式 | P3 | ~5d+ |
| R13 | 一键部署脚本 | install.sh | ⚠️ 手工部署 | P3 | ~3d |

### 范围与排期（2026-10-12 老板：范围 P0–P3；当晚「开始动手」→ 进行中）

**老板 2026-10-12 定：短板补齐（R1–R13）全部纳入计划范围。**

> 🔨 **当前状态：已启动（P0 进行中）**。老板 2026-10-12 20:15「开始动手」。

**进度（P0）**
- ✅ **R2.1 JVM 一键诊断**（jstack/jmap/jcmd/JFR 九动作）——后端 `api-server/app/api/jvm.py` + 前端 `JvmDiagModal` + ServiceDetail 入口，复用 `ExecInPod`、**零 proto 改动**（本机缺 protoc-gen-go，改 proto 生成不了 stub，故全 P0 绕开 proto）；提交 `ab58b95`；**端到端冒烟通过**（测试集群 `efreight-gateway` → `jcmd 7 VM.flags` 返回真实参数）。
- ✅ **R2.2 JVM 启动参数采集**（Deployment `command/args` + env(`JAVA_OPTS/JAVA_TOOL_OPTIONS/JVM_OPTS/CATALINA_OPTS`) 提取，只读；管控改写归 R1 准入）；接口 `GET /api/v1/jvm/.../deployments/{name}/params`，前端 JVM 弹窗展示。提取逻辑单测通过。
- ⚠️ **R2.3 堆/G1 指标**：测试集群 Prometheus **无任何 `jvm_*` 指标**（应用未暴露 JMX/Prometheus），无数据源，暂缓（待有 JVM 指标的集群再接）。
- 🔨 **R1 资源治理+准入**（进行中）：
  - ✅ 管控表 `resource_governance`（模型 + 迁移 `v5w6x7y8z9a0` + API `/api/v1/resource-governance`）。
  - ✅ executor 准入 webhook（`internal/admission`：JSONPatch 补丁计算 + 自签证书 HTTPS server + 读管控表 + **fail-open**；`go build`/`go test` 全绿）。配置项 `ADMISSION_ENABLED/ADDR/CLUSTER_ID/CERT_FILE/KEY_FILE`。
  - ✅ 管控台前端页 `ResourceGovernance.tsx`（路由 + 菜单已接，admin 可见）；API 读/写冒烟通过（GET 列表、POST upsert）。
  - ✅ 准入补丁本地合成验证通过（HTTP+DB+JSONPatch；未碰任何集群）。
  - ⚠️ **部署拓扑约束**：MutatingWebhook 需由 **集群 apiserver 可达** 的地址提供 → webhook 必须**跑在被管集群内**（生产上 executor 在集群内没问题；本地 executor 在 Mac 上，无法给远端集群提供 webhook）。需配套：向集群下发 Service + MutatingWebhookConfiguration（+ TLS）。
  - ⬜ 向集群下发 webhook 配置（Service+MutatingWebhookConfiguration）、高峰采集。
- ⚠️ 备注：exec 链路与 api-server 冷启动均较慢（单次接口 40-60s、启动 3-4min），环境冷/HDD。

| 批次 | 内容 | 合计工时 | 状态 |
|---|---|---|---|
| **P0** | R1 资源治理+准入、R2 JVM | ~16d | 🔨 进行中（R2.1 完成） |
| **P1** | R3 镜像更新、R4 节点管理与均衡、R5 实时日志、R6 实时状态、R7 大屏、R8 Pod 隔离 | ~22d | ⏸ 暂缓 |
| **P2** | R9 MCP、R10 AI 助手增强、R11 告警屏蔽 | ~11d | ⏸ 暂缓 |
| **P3** | R12 agent 外连模式、R13 一键部署 | ~8d+ | ⏸ 暂缓 |

**执行方式（启动后）**：按 P0→P1→P2→P3 顺序；跨栈需求用子代理并行、父会话收口（公共接线文件统一改）；每批完成提交 + 冒烟验证。

### 口径（默认，待开发时再定）

1. **R1 准入风险口径**：老板 2026-10-12 —— **用默认（`fail-open`），后续开发完再解决**（严格治理策略待定）。
2. **R2 诊断产物**：沿用平台对象存储/本地落盘策略（无则本地 + 页面下载）。
3. **R9 MCP**：复用平台账号鉴权，只读账号只能调只读工具。
4. **R12 外连模式**：**双模式并存**（保留现有集中连，新增 agent 主动外连），不破坏现状。
5. **起止范围**：P0–P3 全做（范围已定，排期暂缓）。

详见：**`docs/SHORTFALL-REQUIREMENTS.md`**

---

## 2026-10-14 状态复核（以代码为准）

> 复核：小吴 2026-10-14。上面 10-12 的「需求清单」表是当时快照，**已滞后**——多项标 ❌ 实际已在 10-12/10-13 落地（见下表）。此节为最新口径。

### R1–R13 最新状态

| # | 需求 | 状态 | 落点 / 缺口（代码核实） |
|---|---|---|---|
| R1 | 资源治理 + 准入控制 | ✅ 完成（差集群内部署） | 管控表/API/高峰采集/前端/executor 准入模块 + **准入配置下发 install/uninstall/status（`b3c1d78`，测试集群实测）**；仅剩：准入服务本体在被管集群内部署（拓扑决策） |
| R2 | JVM 深度诊断 + 参数管控 | ✅ 完成 | `api/jvm.py`（9 动作）+ `JvmDiagModal` + Deployment 参数采集；**2026-10-14 新增 jattach 后端**（JRE 镜像免装 JDK 也能 threaddump/堆信息/VM 参数）/明确报错/检测补全，commit `0c43d5f`；生产 JRE 容器实测通过 |
| R2.3 | JVM 堆/G1 指标 + 告警 | ⏸ 暂缓 | 集群 Prometheus 无任何 `jvm_*` 指标，无数据源 |
| R3 | 镜像更新（列仓库标签一键改） | ✅ 完成 | 一键改镜像本已有（`/workloads/.../image`）；新增**列仓库标签**（Registry v2，Harbor/ACR/SWR）（`ff6e11d`）+ 前端标签下拉。私有仓库需配 `REGISTRY_CREDS` 只读凭据 |
| R4 | 节点管理 + 均衡迁移 | ✅ 完成（差 drain 执行） | `NodeManagement.tsx`：节点列表 + **容量/使用率（cpu_pct/mem_pct）+ 多选批量 cordon/uncordon + 均衡计划（只读建议：热点/冷门 + 可迁移 Pod + 分批）**（`1912dc1`）；executor `get nodes/pods -o json`。未做：一键 drain 执行（需评估高风险）、调度到指定节点 |
| R5 | 实时日志跟随 + 增强 | ✅ 完成 | executor 日志流 HTTP(:9099，client-go follow) + api-server WS + 前端 `PodLogStreamModal`（`ed17fbe`）；未做：Deployment 聚合日志跟随、gzip 下载 |
| R6 | 实时状态推送（WebSocket） | ✅ 完成 | executor `/watch`（client-go Watch Pod+Deploy） + api-server WS + ServiceDetail 实时刷新（`e0bced3`） |
| R7 | 统一监控大屏增强 | ↩️ 已回退（待重做） | 自研 `Screen.tsx` 视觉未通过老板审美，**2026-10-14 已回退**（`3ba584a`）：`/screen` 恢复 `<Dashboard screen />`。建议改走**仪表盘加「监控」Tab 下钻**（非独立大屏） |
| R8 | Pod 隔离 | ✅ 完成 | `resources.py`：`POST /clusters/{id}/namespaces/{ns}/pods/{pod}/isolate` |
| R9 | MCP 标准接口 | ✅ 完成 | `api/mcp.py`（Streamable HTTP + JSON-RPC 2.0），已注册 `main.py` |
| R10 | AI 助手增强 | ✅ 完成（基本） | 新增 `query_metrics`（自由 PromQL，`86e3010`）→ 自动进 agent + MCP 统一工具集；断线续传（会话/消息持久化）与全局记忆（知识库 rag_search）已有 |
| R11 | 告警屏蔽（Alertmanager 风格） | ✅ 完成 | `alert_silences` 模型 + `api/alert_silences.py`（含预览）+ `AlertSilences.tsx`（匹配/到期） |
| R12 | agent 主动外连接入模式 | ❌ 未做 | 仍为集中连 |
| R13 | 一键部署脚本 | ✅ 完成 | 新增 `install.sh`（本地/Docker 双模式 + `--check`/`--no-start`/`--skip-deps`）（`a48b9a1`）；README 加快速部署段 |

### 其它线

- **集群监控（Prometheus 数据源，2026-10-13 定稿）**
  - ✅ 即时查询：`prometheus_service.py` + `/api/v1/monitoring/*`（`/metrics`、`/clusters/{id}/metrics/{m}`、`/clusters/{id}/query`）
  - ✅ **趋势（range）**：executor `/prom/range`（apiserver proxy → query_range）+ `/monitoring/clusters/{id}/metrics/{m}/range`（`697096c`）；前端展示已随大屏回退撤下
  - ⬜ 待做：**仪表盘加「告警｜监控」Tab + 监控下钻页**（节点/工作负载/容器 × CPU/内存/网络，range 已就绪）、后台「监控数据源」配置
- **Netdata 监控接入（2026-10-09 的 A–D）**：❌ 已整套回滚（2026-10-13，代码删除）；依赖它的**主机(ECS)/Docker 接入 + 诊断（B/D）**一并取消（无 `hosts` 模型）
- **项目级审批（2026-10-09 E）**：✅ 完成——`projects.approval_flow_id`（迁移 `t3u4v5w6x7y8`）+ `approval_service._load_default_flow` 项目优先 + 前端 `ProjectManagement` 审批流程下拉

### 小结

- **从零未做**：R12（agent 外连，架构级）
- **半成品**：无（R4 主功能完成；仅「一键 drain 执行」待定）
- **暂缓**：R2.3、Prometheus 监控趋势（老板「先不做」）
- **已取消**：Netdata 接入及依赖项（主机/Docker）

> 2026-10-14 本轮开发（老板「继续开发」）：R1 下发（`b3c1d78`）、R5（`ed17fbe`）、R6（`e0bced3`）、R7（`4a22884`）、R4（`1912dc1`）；另修 `.env` COMMAND_WHITELIST 补 `cordon,uncordon`。

---

## 2026-10-16 Agent 接入（M3）落地 + 待部署前置

**已完成**
- **M3 Agent 接入闭环**：`clusters` 加 agent 令牌/状态/心跳/版本（迁移 `x1y2z3a4b5c6`）；`POST /clusters/agent-onboard`、`GET /clusters/{id}/agent/install.yaml`、`POST /clusters/{id}/agent/revoke`；gateway 按集群令牌校验 + 状态更新；前端「Agent 接入」向导 + 状态列。**测试环境真连验证通过**（经隧道 get nodes 返回 50 节点）。commit `9407401`。
- 统一抽象/凭据治理/环境维度/C1 KPI/M2 批量/M4 日志保留 一并完成（详见 `ROADMAP.md` 与提交记录）。

**待部署前置（归入「需求」，属平台部署线）**
| # | 需求 | 说明 |
|---|---|---|
| A6.1 | **agent 镜像构建/推送** | `linux/amd64` 构建 `apiops-agent` → 推到目标集群能拉的仓库（填 `AGENT_IMAGE`）；否则 `install.yaml` 起的 pod 会 ImagePullBackOff |
| A6.2 | **平台 agent 网关 9095 对外可达** | 填 `AGENT_GATEWAY_PUBLIC_ADDR`；跨集群需 NodePort/LB 或 nginx/SamWaf TCP 四层转发（ClusterIP 仅集群内可达） |

**未做（影响集群/部署）**：A2 agent 托管更新、A3 M1 集群内自注册、A4 M2 下发动作、A6 平台生产部署。
**已取消**：R2.3 / JMX Exporter（老板 2026-10-16）。
**已核实暂缓**：M7 时区（全链路 CST 一致，无 bug）。
