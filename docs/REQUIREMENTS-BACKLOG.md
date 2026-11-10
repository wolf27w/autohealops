# AutoHealOps 平台 —— 需求总账（Backlog）

> 整理：小吴　更新：2026-10-14　依据：DESIGN-2026-09-10.md + 工作日志 + 代码核查
> 用途：一张表看清「已做 / 待做 / 待确认」。状态以代码为准，未核实项标 ❓。
>
> 🔴 **最新状态与开发顺序请看 `docs/ROADMAP.md`（2026-10-15 复核，代码为准）。本文件为历史流水，R1–R13 多项已落地但此处未更新。**
>
> 🆕 **2026-10-21 新增方向：Agent-only 纳管**（平台进集群自接入 + 其它集群只下发 agent + 告警检测下沉），详见 **`docs/AGENT-ONLY-ARCHITECTURE.md`**；原 A2/A4/A3 已合并进该需求。

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

**背景**：与开源项目 **KubeDoor**（[CassInfra/KubeDoor](https://github.com/CassInfra/KubeDoor)，2.1，AI 驱动的多 K8S 管控平台）对比后，梳理出 AutoHealOps 的相对短板；老板 2026-10-12 决定纳入开发计划。

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

---

## 2026-11-05 新增需求（老板：先不开发，记入需求）

### 1) PostgreSQL 支持（经 agent 查库 / 健康采集）

- 现状：`internal/agentdb` 只接了 MySQL 驱动（`go-sql-driver/mysql`，随 gorm 传递依赖已在 go.mod）。
- 需求：支持 PostgreSQL 的「查询」与「健康采集」。
- 前置：需要给 agent 引入 Go 驱动（建议 `github.com/jackc/pgx/v5`，或 `lib/pq`），**需要联网拉依赖**，按老板「不擅自装包」原则待批准。
- 落地要点：agent 侧按 `db_type` 选驱动；健康采集按 PG 的口径重写（`pg_stat_activity` / `pg_stat_statements` /
  `pg_database_size` / 锁等待），前端复用「集群数据库」面板。

### 2) 多副本不影响接入（平台侧 + agent 侧）

**目标**：api-server、agent 都能多副本运行（高可用），且不产生重复上报 / 接入不一致。

**问题拆解（两类，别混）**
- **A. agent 多副本**（被管集群里 `replicas>1`）：多个 agent 都会跑 watcher + 推指标/快照/事件 →
  **重复告警、重复数据**；且同一个 cluster_id 有多条隧道，平台只保留最后一条，路由不确定。
- **B. 服务端多副本**（api-server `replicas>1`）：每个副本各有一份「cluster→隧道」注册表；
  agent 连到副本 A，请求落到副本 B 就找不到 → **接入不一致**（不是重复上报）。

**方案选定：二（老板 2026-11-05 定）—— api-server（含网关）多副本 + Redis 归属 + 跨副本转发**

> 否决方案一（拆网关单副本）：只是把单点从 api-server 挪到 gateway，网关仍是 SPOF + 吞吐瓶颈，
> 且没满足「多副本」这个要求本身。

设计要点：
1. **归属注册**：agent 注册成功后，副本写 Redis `agent:owner:{cluster_id}` = `{pod, addr, ts}`，
   TTL 30s、心跳每 10s 续期；断开时仅当 owner 是自己才删除。同一集群短时多连接（滚动升级）保留
   「多连接」语义，不互相覆盖。
2. **路由**：任何要调用 agent 的入口先查 Redis 归属 → owner 是自己则本地执行；否则转发到 owner 副本的
   内部 gRPC 端点（unary 直转，流式用双向流代理，覆盖 ExecutePlan / BatchExecute / ExecPodStream）。
3. **状态写库**：`agent_status / agent_last_seen_at` 只由 owner 副本写，避免多副本互相覆盖。
4. **前置改造：PKI 用 Secret 挂载（老板 2026-11-05 定）**

   现状：`agent_pki` 在 `AGENT_CA_DIR`（RWO PVC）上**自动生成** CA + 服务端证书；
   多副本时各副本会各自生成 → CA 分裂，已下发 agent 全废。所以必须改成「共享且只读」。

   **目标形态**
   - Secret `autohealops-agent-ca`（ns autohealops，Opaque）只放 **CA 一对**：`ca.crt` / `ca.key`
     （客户端证书签名必须用 CA 私钥，所以私钥躲不开）。
   - 以 **readOnly** 挂到各副本的 `AGENT_CA_DIR`（`/var/lib/autohealops/agent-ca`）。
   - `agent_pki` 改为：**只读使用**挂载的 CA，**禁止自动生成**（目录没有 CA 就直接报错，提示先初始化）。
   - **服务端证书不放进 Secret**：每个副本启动时用挂载的 CA **在内存/emptyDir 里现签**一份
     （SAN = `AGENT_GATEWAY_PUBLIC_ADDR` 的 host + Service 名 + localhost）。
     各副本证书不同但同一 CA、同一 SAN，agent 全都认——这样既不需要写 Secret，也能各副本独立轮换。
   - agent 的客户端证书仍按集群**现场签发**（拉 install.yaml 时）；现在改成「读挂载的 CA 私钥在内存签」，
     不落盘。

   **初始化与轮换（老板 2026-11-05 定：只能从「集群管理」生成）**
   - **生成入口只有一个**：平台「集群管理」里的 CA / 接入证书管理（API + UI）。
     平台用自身 ServiceAccount 写 Secret `autohealops-agent-ca`；**禁止** Pod 启动时自动生成（多副本会 CA 分裂），
     **禁止**把 `kubectl create secret` 当常规流程（仅应急手册保留）。
   - **未使用的自动清理**：平台生成的接入凭证（pending 集群记录 + 接入令牌 + 客户端证书），
     **5 小时内没有 agent 建立连接 → 自动删除**（后台定时任务，每 10 分钟扫一次）。
     目的：不留悬挂的令牌/证书，减少泄露面。
   - 轮换 CA = 平台生成新 CA 并更新 Secret → 已下发 agent 的客户端证书全部失效 → 需逐集群重新拉 install.yaml
     （CA 固有代价，与现状一致）。
   - RBAC：平台 SA 需要该 namespace 下 Secret 的 create/update/get；其它主体**不给** get
     （能读 CA 私钥就能签任意客户端证书）。
   - 本地开发（平台跑在 Mac 上、不在集群内）：仍用本地目录，且仅 dev 允许自动生成。

   **清单改动**：`k8s/pvc-agent-ca.yaml` → `k8s/secret-agent-ca.example.yaml`；Deployment 挂 Secret（readOnly）
   + `emptyDir` 给服务端证书；`AGENT_CA_DIR` 保持不变。
5. **可观测**：Redis 归属表加 TTL 监控，归属漂移/隧道双写要有日志与指标。

估时：2–3d（不含 PKI 存储改造，约 +0.5d）。

**agent 侧多副本（A）**：需要选主/租约（K8s Lease 或 Redis 锁），只让一个副本跑 watcher 与采集，
其它副本只承接无状态执行（exec/日志/YAML），避免重复告警与重复数据。~0.5–1d。

**当前实现约束（代码事实）**
- `api-server` 强制单副本：`agent_gateway._agents` 是进程内字典；`k8s/deployments.yaml` 已按单副本 + Recreate 配置。
- agent 的 `install.yaml` 目前 `replicas: 1`，且 `-watch=true`。

### 3) 数据库监控增强（老板 2026-11-05：先记需求）

**现状（代码事实）**
- 采集已经很全：11 类 / 57 个指标（连接、吞吐、缓存、内存、查询质量、InnoDB、锁等待、表空间、配置、速率）
  + 慢 SQL（`slow_sql_records`，含 `sampled_at` 时间戳、digest、执行次数、平均/最大耗时）。
- 但页面只是「指标平铺 + 趋势 + 慢 SQL TopN」，**没有规则判定**，所以看不到异常点。
- 「异常」目前只有 **AI 诊断报告**（整篇结论），非结构化；且只覆盖平台直连的 `db_connections`，
  **agent 集群内的数据库没有这条路**。

**3.1 异常点（结构化）**
- `db_anomaly_service` 规则引擎：对最新一次采集按阈值/趋势评估，输出
  `{level: critical|warning, metric, current, threshold, message, suggestion}`。
- 规则覆盖：连接使用率、buffer pool 命中率、临时表落盘比例、当前/累计锁等待、死锁增长、
  慢查询速率、表空间增长、连接失败率。
- 阈值可配置（系统设置里一份 JSON，按库覆盖可选）。
- 接口：`GET /db-connections/{id}/anomalies`（平台直连库）、
  `GET /clusters/{id}/db-connections/{conn_id}/anomalies`（agent 集群库，复用 DbHealth）。
- UI：数据库监控页顶部「异常点」卡片（红/黄分级 + 建议）；集群数据库面板同步展示。
- 可选：异常点接入 `db_alert_service` 形成告警（现在只有「连不上」告警）。

**3.2 慢 SQL 的时间维度**
- 数据侧已具备：`slow_sql_records.sampled_at` 是采集时间点（每 5 分钟一批）。
- 缺的是查询与展示：
  - 按时间段筛选（今天/昨天/自定义区间）看该时段的 TopN 慢 SQL；
  - 同一 digest 的耗时/次数演进曲线（识别「突然变慢」）；
  - 时段对比（两个时间段 TopN 差异，定位新出现的慢 SQL）；
  - 保留期：沿用快照/指标 7 天口径（超期清理）。

**3.3 慢 SQL 优化点与建议（结构化，不依赖 AI）**
- 规则识别（读 `performance_schema` 指标 + SQL 文本特征）：
  - 全表扫描（`Select_scan` / `Handler_read_rnd_next` 增长）→ 建议加索引/补索引前缀；
  - 临时表落盘（`Created_tmp_disk_tables` 占比高）→ 减小结果集 / 避免大排序；
  - filesort 明显（`Sort_merge_passes`）→ 索引顺序与 ORDER BY 对齐；
  - 锁等待/死锁关联的 SQL → 缩短事务、调整更新顺序；
  - 文本特征：`LIKE '%x%'`、`SELECT *`、大 `IN`、函数包裹列（隐式转换）、`OR` 拆分等。
- 每条建议输出：`规则命中 + 依据（指标/文本） + 优化动作 + 风险提示`；
  可选：对 TopN digest 执行 `EXPLAIN`（只读，需账号权限）拿执行计划要点。
- 与 3.1 打通：慢 SQL 优化点可作为异常点的一种（level=warning，metric=slow_sql）。


---

## 2026-11-05 需求细化（可直接开发的版本）

> 说明：把当天讨论的需求补上「验收标准 / 技术落点 / 拆分 / 估时 / 风险」，便于排期与验收。

### R1 PostgreSQL 支持（经 agent 查库 + 健康采集）— ✅ 已实现（2026-11-09，commit `1b7a008`；agent 0.2.15 已升级测试集群；待真 PG 实例验证）

**验收**
- 配置 `db_type=postgres` 的连接后，只读 SQL 能返回列/行（NULL、时间、数字类型正确）
- 健康检查返回：版本、连接数/上限、活跃连接、事务提交/回滚、锁等待、库/表大小 TOP10、慢 SQL TOP5
- `pg_stat_statements` 未安装时不报错，该项返回「不可用」

**落点**
- `k8s-executor/go.mod`：引入 `github.com/jackc/pgx/v5/stdlib`（**需联网拉包，待批准**）
- `internal/agentdb/query.go`：按 `db_type` 选驱动（`mysql` / `pgx`）；结果归一化（[]byte→string、time→RFC3339）复用
- `internal/agentdb/health.go`：新增 PG 分支（`pg_stat_activity` / `pg_stat_statements` / `pg_database_size`）
- 前端连接表单已支持 postgres，无需改

**风险**：依赖引入；只读账号权限可能不足（缺权限时降级返回「不可用」而不是失败）

### R2 多副本不影响接入 — 3~4d

**验收**
- api-server 跑 3 副本，agent 随机连任一副本；任意请求（列表/终端/日志流/计划执行）都成功，不再出现「无在线 agent」
- 任一副本重启或滚动升级，agent 3 秒内重连；期间其它副本请求自动转发到 owner
- agent Deployment 允许 2 副本时：**不产生重复告警、不产生重复采集数据**

**拆分**
| 步骤 | 内容 | 估时 |
|---|---|---|
| ① PKI Secret 化 | CA 放 Secret 只读挂载；`agent_pki` 禁止自动生成；各副本用 CA 现签 server 证书（同 CA/同 SAN） | 0.5d |
| ② Redis 归属 + unary 转发 | `agent:owner:{cid}` TTL 30s、心跳 10s 续期；内部 gRPC 转发端点；owner 独占写 `agent_status/last_seen` | 1d |
| ③ 流式转发 | ExecutePlan / BatchExecute / ExecPodStream 走双向流代理 | 1d |
| ④ agent 选主 | K8s Lease（优先）或 Redis 锁；仅 leader 跑 watcher + 采集上报，其余只承接无状态执行 | 0.5~1d |

**前置**：平台自身在 k8s 内运行（本地开发不受影响）；RBAC 允许平台 SA 读写该 Secret。

### R3 数据库监控增强 — ✅ 已实现（2026-11-09：R3.1 异常点 `f3a2451` / R3.3 慢SQL建议 + R3.2 时间段 `d96cfad` / digest 趋势 `36beed9`；两时间段并排对比未做）

**R3.1 异常点（1d）**
- 验收：给出一条结构化异常列表，形如 `{level, metric, current, threshold, message, suggestion}`；阈值可在系统设置里改；平台直连库与 agent 集群库都能看
- 规则：连接使用率、buffer pool 命中率、临时表落盘比例、当前/累计锁等待、死锁增长、慢查询速率、表空间增长、连接失败率
- 落点：`services/db_anomaly_service.py`；`GET /db-connections/{id}/anomalies`；`GET /clusters/{id}/db-connections/{conn_id}/anomalies`；DBMonitor 顶部卡片；集群数据库面板

**R3.2 慢 SQL 时间维度（0.5~1d）**
- 验收：可按时间段（今天/昨天/自定义）查 TopN；同一 digest 的耗时/次数曲线；两个时间段 TopN 对比
- 数据已具备（`slow_sql_records.sampled_at`），只需查询与展示

**R3.3 慢 SQL 优化建议（1d）**
- 验收：每条慢 SQL 给出「命中规则 + 依据 + 优化动作 + 风险提示」，至少覆盖：全表扫描、临时表落盘、filesort、锁等待、`LIKE '%x%'`、`SELECT *`、大 IN、函数包列
- 可选：对 TopN 执行 `EXPLAIN`（只读，需权限）返回执行计划要点
- 输出可作为 R3.1 的一种异常点（`metric=slow_sql`）

### R4 集群内数据库纳入「数据库监控」页 — 1~1.5d（新增）

**问题**：agent 集群里的库存在 `cluster_db_connections`，只有「集群详情 → 集群数据库」弹窗能看到；数据库监控页只列 `db_connections`，两套割裂。

**验收**
- 数据库监控页统一列出「平台直连库」+「集群内库」，带来源徽标（直连/集群内）
- 集群内库也能看：指标、慢 SQL、异常点、健康检查
- 集群内库的定时采集（走 agent DbHealth）落 `db_metric_snapshots`，与直连库同口径

**落点**：列表接口聚合两表（返回 `source`）；采集调度增加「集群内库」分支（经隧道）；前端按 source 分组。

### R5 接入凭证 5 小时未连接自动清理 — 0.2d

- 定时任务每 10 分钟：`agent_status='pending'` 且 `created_at < now()-5h` → 删除该集群记录（令牌随之失效）
- 日志留痕：删除时记录 cluster_id/name/token 前缀，便于审计

### 建议排期

| 顺序 | 项 | 理由 |
|---|---|---|
| 1 | R5（0.2d） | 独立、风险低，先清悬挂凭证 |
| 2 | R3.1 异常点（1d） | 业务感知最强，规则引擎后续被 R3.3 复用 |
| 3 | R3.3 慢 SQL 建议（1d） | 与 3.1 同一套规则框架 |
| 4 | R3.2 慢 SQL 时间维度（0.5~1d） | 纯查询/展示 |
| 5 | R4 集群内库并入监控（1~1.5d） | 统一入口，依赖 R3 |
| 6 | R1 PostgreSQL（0.5~1d） | 需批准依赖 |
| 7 | R2 多副本（3~4d） | 改动最大，单独排期 |

### R6 Agent 侧配置数据库并主动上报（老板 2026-11-05 定，替代平台存凭据的思路）— 1.5~2d

**背景/动机**
- agent 的目的是方便接入**外部集群**；外部集群里的数据库也要纳入监控。
- 老板要求：**数据库连接信息配置在 agent 侧**（MySQL 配 MySQL 账号密码；PostgreSQL 配 PG 账号密码），
  agent 自己扫描数据库信息，**主动上报**平台做统计/监控。
- 安全收益：数据库凭据**不出对方集群**，平台库里不存外部库密码。

**配置方式（agent 侧）**
```yaml
# 方式一（推荐）：Secret 挂载 JSON 文件，agent 用 -db-config 指定路径
# kubectl -n autohealops create secret generic agent-db-config --from-file=dbs.json
env:
  - name: AGENT_DB_CONFIG_FILE
    value: /etc/agent-db/dbs.json
---
# dbs.json
[
  {"key":"biz-mysql","type":"mysql","host":"mysql.default.svc","port":3306,
   "database":"biz","username":"monitor","password":"***"},
  {"key":"biz-pg","type":"postgres","host":"pg.default.svc","port":5432,
   "database":"biz","username":"monitor","password":"***"}
]
```
```yaml
# 方式二：直接环境变量（简单场景）
AGENT_DB_CONFIG: '[{"key":"biz-mysql","type":"mysql",...}]'
```
> `key` 是稳定标识（同一集群内唯一），平台据此把多次上报归到同一个数据库记录。

**采集与上报**
- agent 每 **10 秒**（老板 2026-11-05 定，可配 `-db-interval`）采集每个库的**指标**；
  慢 SQL 在同一上报里带上，但内部按 **60 秒** 采一次（performance_schema 全量 digest 查询较重，
  跟着 10 秒跑会把 DB 本身拖慢）——如要改，`-db-slow-interval` 可配。
  - 通用：版本、连接数/上限、活跃连接、锁等待、慢查询累计、库/表大小 TOP10
  - MySQL：`SHOW GLOBAL STATUS` + `performance_schema`（慢 SQL TOP5）
  - PostgreSQL：`pg_stat_activity` / `pg_stat_statements` / `pg_database_size`
  （**老板已批准引入 `jackc/pgx` 驱动**，见 R1）
- 经隧道上报：新增隧道方法 `DbMetrics`，payload = `{cluster_id, dbs:[{key, type, metrics:{...}, slow_sql:[...] }]}`
- 采集失败只上报 `error`，不中断其它库；平台侧记录 `last_error`。

**平台侧落地**
- `db_connections` 增加 `source`（direct/agent）+ `cluster_id` + `agent_db_key`（唯一键 cluster_id+agent_db_key）：
  agent 首次上报自动创建一条 `source=agent` 的记录（**不存密码**），后续按 key upsert。
- 指标写入现有 `db_metric_snapshots`（增加 `source` 列，值 `agent`），慢 SQL 写入 `slow_sql_records`（同样标记来源）。
- 「数据库监控」页统一展示直连库 + agent 库，带来源徽标（**满足 R4**）。
- agent 库**可以**手动查询：平台只下发 `{key, sql}`，agent 用本地账密执行（`QueryDatabaseByKey`），
  只读白名单 + 200 行上限 + 审计；健康检查/探活同样走隧道（`select 1`），不用平台账密。
  测试连接、告警探活、诊断采集对 agent 库统一走 agent，平台侧直连采集自动跳过（2026-11-06 修）。

**验收**
- agent 侧配 1 个 MySQL + 1 个 PG → 平台数据库监控页出现两条 `来源=agent` 的记录，指标曲线连续
- 断开隧道时记录变灰/标 stale，恢复后自动续上
- 平台库里查不到外部库的密码（直查 `db_connections` 该行 `password_encrypted` 为空）
- **集群管理的「集群数据库」入口已不存在**（下线完成，无残留按钮/接口/表）
- **平台可对该库执行只读查询**：选 `source=agent` 的实例 → 输入 `select ...` → 经隧道由 agent 用本地凭据执行 → 返回结果 ≤200 行，审计里有记录
- 非白名单 SQL（`delete/update/drop`）被拒，且拒绝行为进审计
- **实例名同步**：只改 agent 配置里的 `name` → 平台实例名跟着变、不新增记录、指标不断；
  从配置里删掉该项 → 平台该实例变「已下线」，历史数据仍在

**估时**：MySQL 路径 1d（可复用现有 HandleHealth 采集逻辑）；PG 0.5d（依赖 R1 驱动）；平台落库+UI 0.5d。

**边界（老板 2026-11-06 强调）**
- **agent 配的是「agent 所在集群内」需要监控的库**（host 写集群内 Service 名/Pod IP，如 `mysql.stg.svc.cluster.local`）；
- **平台自己的库 / 网络可达的库**走「直连」（`source=direct`，凭据在平台）；
- 两者不要混：把平台库配到 agent 里没有意义（agent 只需要能连通本集群内的库）。
- agent 侧建议用**只读账号**（SELECT / PROCESS / REPLICATION CLIENT），不要用 root。

**两种数据库来源并存（老板 2026-11-05 定）**
| 来源 | 配置在哪 | 采集方式 | 凭据存哪 |
|---|---|---|---|
| **直连**（本地/网络可达的库）| 平台「数据库连接管理」 | api-server 直连采集（现有 `db_collector`）| 平台库（加密） |
| **agent**（外部集群内的库）| agent 侧 Secret / env | agent 在集群内采集后经隧道上报 | **只在对方集群** |

- 两者在「数据库监控」页**统一展示**，带来源徽标；指标/慢 SQL 同一套表、同一套趋势图口径。
- 🔴 **原 `cluster_db_connections`（平台存凭据 + 经隧道临时查询）直接下线**（老板 2026-11-06 定）：
  删除集群详情「集群数据库」入口、`api/cluster_db.py` 接口与 `cluster_db_connections` 表；
  **账密一律只在 agent 侧配置**，平台不再保存被管集群的数据库凭据。
- 下线清单（开发时照此收口）：
  1. 前端：`ClusterDetail` 顶部「集群数据库」按钮 + `ClusterDbModal.tsx` + `api.ts` 里 `listClusterDbConnections/createClusterDbConnection/deleteClusterDbConnection/queryClusterDb/health`
  2. 后端：`api/cluster_db.py` 路由、`models/cluster_db_connection.py`、对应迁移（保留数据备份后再 drop）
  3. agent：`agentdb/query.go`、`agentdb/health.go` 不随之删除——改为下面「平台经 agent 查询」使用

**平台经 agent 查询（老板 2026-11-06 定：凭据不出集群，但平台要能查）**
- 入口：「数据库监控」/「数据库故障」里 `source=agent` 的实例 →「查询」按钮（原集群数据库弹窗的能力，挪到库维度）
- 链路：平台 `POST /api/v1/db-connections/{id}/query` → 该记录 `source=agent` → 按 `agent_db_key` 经隧道下发 → **agent 用自己本地配置的账号密码连库执行** → 结果回传
- 只读白名单：`select / show / describe / explain`（复用 agent `agentdb.Query` 的校验）
- 限制：最多返回 200 行、单次超时 30s、按集群+库限流
- 审计：记录「谁、哪个集群、哪个库（key）、SQL 摘要、耗时、返回行数、成功/失败」，**不落查询结果**
- 控制台两种模式（老板 2026-11-06 加）：
  - **只读查询**：白名单 select/show/describe/explain/with，最多 200 行；
  - **执行（管理员）**：DML/DDL，仅 admin + 前端二次确认，返回影响行数，**全部审计**（平台不做自动修改）；
  - **结果导出**：只读查询结果导出 CSV（最多 5000 行，UTF-8 BOM，Excel 直接打开）。
- 平台库里查不到该库的任何密码（验收项）


**命名规则（老板 2026-11-05 定：名称以 agent 配置为准）**
- agent 配置里每项必须有 `key`（稳定标识）和可选 `name`（显示名，缺省=key）：
  ```json
  {"key":"biz-mysql","name":"业务库(MySQL)","type":"mysql","host":"...","username":"...","password":"..."}
  ```
- 平台侧唯一键 = **(cluster_id, source_key)**；显示名 = 配置里的 `name`（没有则 `key`）。
- 页面上展示为「名称 + 来源集群」，避免两个集群都有 `biz-mysql` 时混淆。
- **实例名以 agent 配置为准并自动同步**（老板 2026-11-06 定）：
  - agent 配置里的 `name`（缺省 = `key`）就是平台展示的实例名，**平台侧不可编辑**
  - 改名（`name` 变）→ 平台**更新同一条记录的名称**，不新建实例、历史指标/慢 SQL 不断
  - `key` 变了 → 视为新数据库（新建实例），旧记录按「已下线」处理
  - agent 配置里删掉某一项 → 平台把该实例标记为 `removed`（页面显示「已下线」），
    **不物理删除**，历史指标/慢 SQL 保留可查
  - 平台手建的 `source=direct` 库不受影响，名称仍由平台维护
- 直连库沿用现有 `db_connections.name`（全局唯一），来源标记 `direct`。

### R7 Agent 侧配置主机（虚拟机）并 SSH 代采 — ✅ 已实现（2026-11-09）

**背景**：外部集群内网的虚拟机，平台 SSH 不可达；但 agent 在集群内，能连同网段 VM。
凭据（SSH 私钥）留在对方集群（Secret 挂载），平台不保存。

**配置（agent 侧）**
```yaml
# Secret 挂载
# hosts.json
[
  {"key":"biz-app-01","name":"业务应用 01","address":"192.168.10.21","port":22,
   "user":"ops","processes":[{"name":"java","path":"/opt/app/bin/start.sh"}]}
]
# agent 启动参数：-host-config=/etc/agent-host/hosts.json -ssh-key=/etc/agent-host/id_rsa
```

**采集与上报**
- agent 每 60 秒 SSH 执行 `ps -eo pid,pcpu,pmem,rss,args`，按 `processes[].path` 匹配：
  是否在跑 / PID / CPU% / 内存 MB / 状态翻转
- 隧道方法 `HostMetrics` 上报（含 `key`、采集时间、每进程结果、错误）
- SSH 连不上 → 上报 `error`，平台标记该主机 `unreachable` 并触发通知（与现有 host_monitor 口径一致）

**平台侧**
- `hosts` 增加 `source`（direct/agent）、`cluster_id`、`source_key`，唯一键 `(cluster_id, source_key)`
- agent 首次上报自动建记录（**不存 SSH 凭据**）；名称取 agent 配置的 `name`，展示带「来源集群」徽标
- 「主机管理」页统一展示直连主机 + agent 主机，可按来源筛选

**验收**
- agent 配 2 台 VM → 平台主机管理页出现两条 `来源=agent` 记录，进程状态/CPU/内存正常刷新
- 停掉某进程 → 60 秒内状态变「未运行」并通知
- 平台库里查不到 SSH 私钥

**与现有 SSH 直连纳管的关系**：两者并存，直连用于平台可达的机器；agent 用于外部内网机器。

### R8 主机（虚拟机）管理增强：分组 / 批量导入 / 凭据保存 / 堡垒机登录 / 文件传输 / 操作留痕 — ✅ 已完成（2026-11-10，8.1 分组按 R25 口径）

**背景**：现有「主机管理」只能单条添加、SSH 直连采进程，没有分组、批量导入，也不能直接登录操作。
老板要求做成「类堡垒机」：能分组、能批量导、能保存密码/密钥、能直接在页面登录操作、并且全程留痕。

#### 8.1 分组（权限口径见 **R25**，实现时按 R25 落地）
- 新表 `host_groups`（id, name, description, sort, created_at）；`hosts` 加 `group_id`（可空=未分组）。
- 页面：左侧分组树（含「未分组」/「全部」）+ 右侧主机列表；支持新建/改名/删除分组（删除时主机转「未分组」）、
  按分组筛选、拖拽或批量改分组。

#### 8.2 批量导入
- 支持 **CSV**（首选，模板可下载）与 **粘贴表格**；字段：
  `名称,地址,端口,用户名,认证方式(password|key),密码或私钥,分组,进程路径(分号分隔),说明`
- 校验：地址+端口+用户名重复提示（可选择跳过/覆盖）；IP/域名格式；group 不存在则自动创建。
- 导入结果返回：成功 N 条、跳过 M 条（含原因），可下载失败明细。
- （可选）支持导入 `~/.ssh/config` 格式。

#### 8.3 凭据保存（密码 / 密钥）
- 现状：`hosts` 有 `ssh_password_encrypted` 与 `ssh_key_path`（路径只对本机有效）。
- 改造：新增 `ssh_key_encrypted`（AES-256-GCM 存**私钥内容**），`auth_type`（password|key）；
  `ssh_key_path` 保留兼容（本地文件方式），优先用加密内容。
- 私钥/密码**永不下发前端**；接口只返回 `has_password` / `has_key`。
- 可选：私钥口令（passphrase）加密保存。

#### 8.4 堡垒机式 Web 登录（核心）
- 新 WebSocket：`/api/v1/ws/hosts/{host_id}/terminal?token=***`
  - 后端用 SSH 库建立交互式 shell（pty），xterm.js ↔ SSH 双向转发（参考现有 Pod 终端实现）
  - 认证：优先用保存的凭据；也支持「本次输入」临时凭据（不落库）
- 前端：主机列表「登录」按钮 → 终端弹窗（复用 TerminalModal 组件，改为 SSH 后端）
- 权限：仅 admin / 或「该主机所属分组的授权用户」可登录；登录前二次确认 + 记录来源 IP
- 会话控制：空闲超时（默认 10 分钟）、最大会话数、可强制踢下线（admin）

**依赖**：引入 **`asyncssh`**（老板 2026-11-05 批准）。原生 asyncio，与 FastAPI/现有 WebSocket 终端架构契合。

**页面形态（老板 2026-11-05 定：独立页面，类 JumpServer 布局，不用弹窗）**
```
┌──────────────────────────────────────────────────────────────┐
│ 顶部：Web 终端 | 当前用户 | 会话计时 | 全屏               │
├───────────────┬──────────────────────────────────────────────┤
│ 左栏(280px)   │ 会话标签页：[biz-app-01] [biz-app-02] ×      │
│ 搜索框        ├──────────────────────────────────────────────┤
│ ▸ 分组树      │                                              │
│   ▸ 生产      │        xterm.js 终端（单标签 = 单 SSH 会话） │
│     · 主机A   │                                              │
│     · 主机B   │                                              │
│   ▸ 测试      │                                              │
│ 最近登录      │                                              │
└───────────────┴──────────────────────────────────────────────┘
```
- 路由：`/web-terminal`（选择主机）、`/web-terminal/:hostId`（直达）
- 左栏：分组树 + 主机列表（可搜索、最近登录），点主机 → 新开一个标签页
- 多标签 = 多会话并行（每标签独立 SSH 连接、独立录制）
- 工具栏：主机信息（IP/账号/分组）、断开、重连、全屏、字体大小
- 会话超时/踢下线：标签变红并提示
- 右侧抽屉：**文件传输（SFTP）面板**（见 8.6）

#### 8.6 文件上传 / 下载（SFTP，老板 2026-11-05 加）
- 入口：Web 终端页右侧抽屉「文件」标签（与终端同主机、复用同一 SSH 连接）
- **上传**：浏览器选文件（支持多选/拖拽）→ 后端经 asyncssh SFTP 写到目标主机
  - **默认目录 `/tmp`**（可改；记忆上次路径）
  - 显示进度条；同名文件默认询问（覆盖/重命名）；支持大文件分块流式传输
  - 单文件大小上限可配（默认 200MB，超限直接拒绝）
- **下载**：远程目录浏览（列表：名称/大小/权限/修改时间）+ 点文件下载到浏览器（流式，不落服务器磁盘）
- 目录范围限制：默认允许 `/tmp` + 用户 home；可在系统设置里配白名单/黑名单
  （如禁止 `/etc`、`/root`），越权路径直接 403
- **审计**：上传/下载都写进会话留痕（文件名、大小、方向、目标路径、sha256、操作人、时间）
- 权限：与终端登录权限一致（能登录该主机才能传文件）

#### 8.5 操作留痕
- 新表 `host_sessions`（id, host_id, user_id, source_ip, started_at, ended_at, duration_sec, bytes_in, bytes_out, status）
- 新表 `host_session_chunks`（session_id, seq, direction(input|output), data(压缩), ts）——按块落库，避免长事务
- 录制：把 stdin/stdout 原始字节流带时间戳存起来（anscinema v2 格式即可前端回放）
- 页面：主机详情 → 「会话历史」列表 → 点开 **独立回放页** `/host-sessions/:id/replay`
  （类同 Web 终端布局：终端 + 时间轴播放/倍速/导出 .cast）
- 保留期：与日志口径一致（7 天），超期清理；审计表不参与清理（合规）
- 敏感命令可标红（可选规则：rm -rf / drop table / passwd 等）→ 列表里高亮

#### 验收
- 建 2 个分组，批量导入 10 台（含 1 条重复）→ 成功 9、跳过 1，分组正确
- 保存密码和私钥各一台，页面能看到 `has_password` / `has_key`，接口不返回明文
- 页面上点「登录」→ 打开终端能执行命令；断开后会话历史里能回放到刚才的输入输出
- 非授权用户看不到登录入口（403）

#### 估时
| 子项 | 估时 |
|---|---|
| 8.1 分组 + 8.2 批量导入 | 1d |
| 8.3 凭据保存改造 | 0.5d |
| 8.4 Web 终端（asyncssh 通道 + 独立页面布局 + 权限 + 超时）| 2~2.5d |
| 8.6 SFTP 上传下载（默认 /tmp、进度、目录白名单、审计）| 1~1.5d |
| 8.5 会话录制 + 回放页 + 清理 | 1.5~2d |
| 联调/自测 | 1d |

### R9 主机采集支持密码认证（改用 asyncssh）— ✅ 已修复（2026-11-09，commit `36881dc`；8 台主机全部 online）

**现状（Bug，2026-11-05 实测确认）**
- `api-server/app/services/host_monitor_service.py` 的 `ssh_ps()` 用系统 `ssh -o BatchMode=yes`，
  只支持 `-i <私钥路径>`；**保存的 `ssh_password_encrypted` 从未被使用**（代码里既没有 sshpass，也没有 SSH 库）。
- 结果：配置密码认证的主机**永远 Permission denied → 一直离线**。
  实测：7 台主机（192.168.6.41/42/236/237/183/13/14）全部 offline，只有 192.168.6.36 因为本机有免密密钥才 online。
- 附带问题：这 7 台 `process_paths` 为空 → 即使连上也只显示 online，不监控任何进程（需补配置）。

**改法（老板定：B 方案，改代码而不是铺公钥）**
- 引入 `asyncssh`（已批准，与 R8 共用），`ssh_ps()` 改为 asyncssh 连接：
  - 认证优先级：保存的私钥内容（`ssh_key_encrypted`，R8 新增）→ 私钥路径（兼容）→ 保存的密码
  - 仍执行 `ps -eo pid,pcpu,pmem,rss,args`，解析逻辑不变
- 连接失败要区分原因并落库/展示：`认证失败`（password/publickey） vs `网络不可达` vs `超时`，
  不要统一显示「离线」——现在这个问题就是「认证失败被当成离线」，排查困难。
- `hosts` 增加 `last_error` 字段（varchar 255），页面悬停显示。

**验收**
- 配置密码的主机能在 60 秒内变 online，进程状态正常刷新
- 故意填错密码 → 状态显示「离线（认证失败）」而不是干巴巴的离线
- 断网 → 显示「离线（连接超时）」

**估时**：0.5d（含错误分类与页面提示）。

### R10 提示词按场景体系化管理 — 2~3d（老板 2026-11-05 提）

**现状问题（代码事实）**
- `ai-service/app/services/diagnosis_service.py` 用 **if/else 硬编码** 选模板：
  `application_diagnosis` / `sql_optimization` / `pod_crash_diagnosis` / `node_pressure_diagnosis`，
  且**兜底是 node 模板** → 未知 fault_type 会拿节点模板去诊断，结论必然跑偏。
- 模板只有 `category`（diagnosis / repair_plan / risk_assessment / verification），没有「场景」概念；
  新增场景必须改代码；无灰度、无版本对比、无片段复用（输出格式/红线在每个模板里重复）。

**目标体系（三层）**
| 层 | 内容 | 存哪 |
|---|---|---|
| **① 公共片段** | 输出格式约定、红线（只给方案不执行）、术语、证据引用要求 | 新表 `prompt_fragments`，模板用 `{{fragment:key}}` 引用 |
| **② 场景模板** | 角色设定 + 变量集 + 该场景分析要点 | `prompt_templates` 加 `scene` 字段 |
| **③ 路由规则** | fault_type / 场景 → 用哪个模板（可配优先级、可覆盖） | 新表 `prompt_routing` |

**场景划分（建议）**
| scene | 判定依据 | 模板 | 状态 |
|---|---|---|---|
| `pod` | CrashLoopBackOff / OOMKilled / ImagePullBackOff / PendingTimeout | pod_diagnosis（原 pod_crash_diagnosis） | 已有，改造 |
| `node` | NodeNotReady / MemoryPressure / DiskPressure / PIDPressure | node_diagnosis | 已有，改造 |
| `workload` | 服务级：可用性下降、副本异常、探针失败、发布后异常 | workload_diagnosis（原 application_diagnosis） | 已有 |
| `database` | 数据库连接/锁/慢SQL/表空间 | db_diagnosis（原 db_diagnose） | 已有（2026-11-05 模板化） |
| `cluster` | 集群容量/调度失败/组件异常 | cluster_diagnosis | **新增** |
| `platform` | 平台自身异常（可选） | platform_diagnosis | 路线图 |
| —（动作类） | 不属诊断：生成修复方案 / 风险评估 / 验证 | repair_plan / risk_assessment / verification | 已有 |

**路由规则（替代硬编码）**
- 新表 `prompt_routing`：`(match_type: fault_type|scene, match_value, match_mode: exact|prefix|default, template_name, priority, enabled)`
- 查找顺序：精确 → 前缀 → 默认；**默认不能再用 node 模板**，用 `default_diagnosis`（通用排障）
- 未命中路由 → 记录日志并落库（便于补规则），页面显示「未命中路由」告警

**变量契约**
- 统一变量集：`cluster_name / namespace / resource_kind / resource_name / fault_type / severity /
  metrics / logs / events / describe_yaml / kb_hits`
- 模板声明 `variables` + **必填校验**（如 resource_name 缺失 → 直接报错，不发空 prompt）
- `sample_vars`（JSON）供前端「预览渲染」用

**前端（AI 配置 → 提示词）改造**
- 左侧按场景分组树（诊断 / 动作 / 其他），右侧编辑
- 每个场景：选模板版本、启用/停用、**灰度比例**（按百分比分流到新版本）
- 「预览渲染」：填样例变量看最终 prompt；「版本对比」：diff
- 「试跑」：选一条真实告警跑一次（手动触发，控制成本）
- 显示每个版本的效果（采纳率/满意度，基于 AIEffectivenessStat）

**验收**
- 新增一个 fault_type 只改配置（路由表）就能用新模板，无需改代码
- 未知 fault_type 走 `default_diagnosis`，不再错用 node 模板
- 页面能按场景看到模板、切换版本、预览渲染结果

**估时**：数据模型+迁移 0.5d；ai-service 路由与片段展开 0.5~1d；前端改造 1~1.5d。

### R11 诊断模板路由可配置（R10 的最小可用版，老板 2026-11-05 定：先做这个）— ✅ 已完成（2026-11-10，commit `90043bb`）

**问题（实测告警数据）**
- 现有选模板逻辑是 if/else 写死 4 个名字，**其余全部兜底到「节点压力」模板**。
- 实测 16 种 fault_type 中，有 **8 种（共 131 条告警）用错模板**：
  `TerminatingStuck(75)`、`PodRestartProbeFail(38)`、`PodRestartOOM(7)`、`NodeCordoned(4)`、
  `CreateContainerError(3)`、`ContainerMemHigh(2)`、`CreateContainerConfigError(1)`、`DBConnFailed(1)`。
- 另外：**页面新建的模板不会被用到**（没有路由指向），只能改现有 5 个名字的内容。

**方案（最小改动，不动表结构）**
- 系统设置新增 `diagnosis_template_routing`（JSON），形如：
  ```json
  {
    "exact": {"CrashLoopBackOff":"pod_crash_diagnosis", "TerminatingStuck":"pod_crash_diagnosis",
              "NodeCordoned":"node_pressure_diagnosis", "DBConnFailed":"db_diagnose"},
    "prefix": {"PodRestart":"pod_crash_diagnosis"},
    "default": "default_diagnosis"
  }
  ```
- `ai-service` 选模板改为：读配置 → **exact 精确匹配 → prefix 前缀匹配 → default**；
  配置缺失时回退内置默认表（即上面这份，保证不改配置也能修掉当前的错配）。
- 新增 `default_diagnosis` 模板（通用排障），**不再用节点模板兜底**。
- 命中结果与未命中都写日志；未命中时诊断结果里带 `template_missed: true` 提示。

**验收**
- 把 `TerminatingStuck` 路由到 `pod_crash_diagnosis` 后，该故障诊断走 pod 模板（看日志/结果）
- 在系统设置里新增一条映射（如 `ContainerMemHigh → application_diagnosis`）→ **不重启即生效**
- 未知 fault_type → 用 `default_diagnosis`，不再错用 node 模板
- 页面新建的模板，只要在路由里配上名字就能被诊断用到

**与 R10 的关系**：R11 是 R10 的先行子集（先让「配置化路由 + 能用新模板」跑起来）；
R10 落地后，这份 JSON 迁移成 `prompt_routing` 表并加场景/灰度/预览，语义不变。

### R12 应用故障分类体系（把「应用错误」变成可记录的 fault_type）— ✅ 已完成（2026-11-10，commit `bce802a`）

**问题**
- K8s 故障类型是**有限枚举**（CrashLoopBackOff / OOMKilled / NodeNotReady…），好记录、好路由。
- 应用错误是**日志文本**，没有标准类型。现状：所有应用日志告警统一写成 `fault_type="LogKeyword"`，
  导致 —— ① 告警列表看不出是哪类问题；② 无法按类型路由到不同诊断提示词（R11）；③ 统计无意义。

**方案：规则归一化（文本 → 标准 fault_type）**

1. **规则表扩展**：`log_alert_rules` 增加
   - `fault_type`（命中后写入的故障类型，默认 `LogKeyword` 兼容老数据）
   - `category`（app / db / infra / business，用于分组统计）
   - `priority`（同一日志命中多条时取最高）
   - `sample_pattern`（正则，用于从日志里抽取关键行做证据）

2. **内置应用故障分类（可一键导入，可按环境改）**

| 标准 fault_type | 典型特征 |
|---|---|
| `AppOOM` | OutOfMemoryError / Java heap space / cannot allocate memory |
| `AppCrash` | Segmentation fault / core dumped / panic: |
| `AppConnRefused` | Connection refused / dial tcp .* connect: refused |
| `AppTimeout` | timeout / timed out / DeadlineExceeded / Read timed out |
| `AppSQL` | SQLException / Deadlock found / Lock wait timeout / too many connections |
| `AppConfigError` | No such file or directory / config not found / missing env |
| `AppPermission` | Permission denied / AccessDenied / 403 Forbidden |
| `AppDependency` | 502/503/504 / upstream connect error / No route to host |
| `AppBusiness` | 自定义业务错误码（按项目配） |
| `LogKeyword` | 兜底：只命中关键词但归不了类 |

3. **写入告警**：命中规则 → `alerts.fault_type = 规则.fault_type`，`message` 带命中关键词 + 首行堆栈；
   同一 Pod/服务 5 分钟内多类命中 → 合并取最高优先级（沿用现有告警合并口径）。

4. **诊断路由**：R11 的路由表直接支持这些类型（如 `AppSQL → db_diagnose`、`AppOOM → pod_crash_diagnosis`），
   提示词里通过 `{app_error_type}` + `{log_snippet}` 变量让模型聚焦。

5. **前端**
   - 日志告警规则页：增加「故障类型 / 分类 / 优先级 / 样例正则」字段
   - 提供「导入内置规则」按钮（按语言栈选择：Java / Go / Node / Python）
   - 告警列表与仪表盘支持按「应用错误类型」聚合（替代现在清一色 LogKeyword）

**验收**
- 造 3 条日志（OOM、连接拒绝、SQL 死锁）→ 分别生成 `AppOOM` / `AppConnRefused` / `AppSQL` 三类告警
- 同一 Pod 同时命中 OOM 与 Timeout → 只保留一条（取高优先级），不重复建单
- 告警列表能按应用错误类型筛选；仪表盘有分布图
- 对应类型能路由到指定诊断模板（配合 R11）

**估时**：表结构+内置规则库 0.5d；扫描与合并逻辑 0.5d；前端规则页与聚合 0.5~1d。

### R13 未分类故障自动沉淀提示词（自学习闭环）— 2~3d（老板 2026-11-05 提）

**目标**
没出现过的故障 → 直接把日志/上下文给 AI 通用诊断 → 诊断完**顺手生成一个提示词草稿** →
人工审核采纳后进入路由。错误类型越多，模板库越厚，诊断越准，解决越快。

**流程**
```
告警/日志
  → R11 路由命中？ 是 → 用既有模板诊断（不走本流程）
                  否 → ① 通用诊断（default_diagnosis）
                       ② 标记 fault_type=Unclassified（落库，便于统计）
                       ③ 诊断完成后：生成「提示词草稿」（pending 状态，不启用）
                       ④ 同类未分类故障出现 ≥N 次（默认 3 次/7 天）→ 提升为「建议建模板」
  → 解决确认（告警恢复/工单关闭/人工标记有效）后，系统生成一条「提示词建议」（**不是模板**）
  → 人工在「提示词建议」页确认 → **才真正创建模板**（并启用、绑定路由）
  → 不确认 = 永远不创建，只能停留在建议列表（可忽略/删除）
```

**老板 2026-11-05 明确（最终口径）**：**必须人工确认后才创建提示词，不确认不会创建。**
AI 只负责「起草建议」，创建权在人手上。

**关键设计**
1. **两段式：建议（存 suggestions）→ 确认（写入 templates）**
   - 触发只由「确认解决」的案例产生（告警 resolved / 工单 closed / 人工标记有效），未解决的不生成建议
   - 建议内容可在页面上**编辑后再采纳**（人可以直接改提示词）
   - 点「采纳」才写 `prompt_templates`（`source=auto`，`status=active`）+ 自动绑定 fault_type 到路由
   - 不采纳：默认保留 30 天后清理，或手动忽略；**不会出现在模板列表、不会参与路由**
2. **采纳率熔断**：模板（含人工采纳的自动模板）上线后若采纳率 < 40%（或标记无效 ≥3 次），
   自动降级为 `draft` + 通知管理员，可回滚到上一版本。
3. **聚类门槛**：同类（关键词/异常类型/服务）累计 ≥3 次才提示生成，避免一次性偶发错误就造模板。
4. **相似度去重**：生成前先比对现有模板（名称/场景/向量），相似则建议「更新现有模板 v+1」而不是新建。
5. **脱敏**：送模型的日志先过滤 IP / 账号 / token / 连接串（复用现有脱敏工具）。
6. **限流**：每天最多生成 N 条建议（默认 5），建议积压超阈值提醒（提醒人去审核，不自动创建）。
7. **可回溯**：草稿记录生成依据（哪几条告警/日志摘要/当时 AI 结论），采纳后统计该模板的诊断采纳率。

**数据结构**
- `prompt_templates` 增加 `status`（draft/active/archived）、`source`（manual/auto）、`source_refs`（JSON：告警ID/知识库ID）、`parent_id`（由哪个模板演进）
- 新表 `prompt_suggestions`（聚类候选：特征、命中次数、样例日志、状态 pending/accepted/ignored）

**验收**
- 造一条从未见过的日志错误 → 通用诊断 → 标记「已解决」→ 生成 1 条**建议**（模板列表里看不到）
- 不确认：不创建模板、不参与路由（验证：诊断仍走通用模板）
- 确认采纳：才出现新模板并绑定 fault_type；此后同类故障直接命中新模板
- 建议页可编辑内容后采纳；模板被连续判无效 → 降级 draft + 通知

**估时**：建议生成与相似度去重 1d；触发/熔断/限流 0.5~1d；建议审核页（编辑/预览/试跑/采纳/忽略）+ 模板回滚 0.5~1d。

### R14 双接入模式：反向推送（push）+ 平台拉取（pull）— 4~5d（老板 2026-11-05 定）

**老板口径**：不管是完全隔离还是 DMZ 中转，本质只是**方向不同**；推送和拉取**两个都要有**，按环境选。

**模式定义**
| 模式 | 谁发起 | 适用 | 现状 |
|---|---|---|---|
| `push`（默认）| agent → 平台 9095（mTLS 长连接）| agent 能出站（公网/可出网内网）| ✅ 已实现 |
| `pull` | 平台 → agent `:9100`（HTTPS + mTLS）| agent 不能出站、但平台能入站（DMZ/VPN/内网可达）| ❌ 待实现 |

> **范围限定（老板 2026-11-06 定）：pull 接入模式只针对 agent 接入的集群**；
> 平台进集群自接入（in-cluster）的集群不需要 pull。

**agent 侧（pull 模式）**
- 启动参数 `-mode=pull -pull-addr=:9100`（默认仍是 push）
- 复用现有 agent CA 做**双向 mTLS**（平台验 agent 证书；agent 验平台证书 + Token 双因子）
- 接口：
  - `GET /agent/v1/collect` — 返回本轮采集结果（指标 / 事件 / 快照 / 配额 / 告警），带 `collected_at` + 单调 `sequence` 供平台去重
  - `GET /agent/v1/commands?limit=N` — 取待执行指令（exec / terminal 帧 / 诊断取数），**取走即出队**
  - `POST /agent/v1/commands/{id}/result` — 回传执行结果 / 终端输出
- 本地仍保留采集循环（间隔同 push：指标 10s、慢 SQL 60s）

**平台侧**
- `clusters.agent_mode`（push/pull）+ `agent_pull_url`（迁移新增两列）
- `agent_pull_worker`：按集群每 N 秒拉一次（默认 15s，可配），走与 push 相同的落库管线（复用 `agent_alert_service.handle_event` 的分支）
- **指令队列**：交互类请求（进容器/执行命令）投递到 `agent_command_queue` 表（集群维度、带过期时间），agent 拉取后执行、结果回传 → 平台唤醒等待中的请求
- 前端：集群详情显示「接入模式」，pull 模式标「拉取模式 · 约 N 秒延迟」

**DMZ 中转天然支持**
- `agent_pull_url` 可指向中转机（agent 推到中转机 → 平台拉中转机），无需额外协议；
  中转机只做转发/聚合，平台侧配置不变。

**关键约束**
1. **同一集群只能启用一种模式**（避免重复写入）；切换时先停另一种，页面提示。
2. **交互功能降级**：pull 模式下终端/exec 延迟 = 拉取周期（默认 15s，可调到 3~5s 抬升实时性）；
   页面明确标注，不假装实时。
3. **去重**：以 `(cluster_id, sequence)` 或时间戳窗口做幂等写入。
4. **离线安装前置**：pull 模式要求目标环境**开放入站到 agent Service**（NodePort/LB），并在 install.yaml 里
   生成对应 Service；完全气隙环境仍需预导入镜像 + 离线 install.yaml（另见部署文档）。

**验收**
- 同一套平台，集群 A 用 push、集群 B 用 pull，两边指标/事件/告警都能正常落库和展示
- pull 模式下调小周期到 5s，集群详情/监控刷新延迟符合预期
- pull 模式下「进容器」能连上，输入输出延迟 ≈ 拉取周期，页面有「拉取模式」标识
- 切换模式不产生重复数据（对比切换前后行数）

**估时**：agent 侧 pull 服务 + 指令队列 1.5~2d；平台拉取 worker + 落库复用 1~1.5d；
交互类降级适配（终端/exec 排队）1~1.5d；联调 1d。



### R16 数据库管理（Navicat 式工作台 + 对象/导入导出/定时任务）— 2026-11-06

> 定位：把「数据库」相关操作统一收到 **诊断中心 → 数据库管理**，监控页只留监控。
> 实现状态以代码为准（本轮已落地的标 ✅，未做的标 ⏳）。

**16.1 工作台布局 ✅**
- 三栏：左「实例列表」（搜索/状态点/直连·agent 徽标/已下线/任务数徽标）
  | 中「对象树」（表/视图/函数/存储过程/事件，可折叠+数量）
  | 右「SQL 编辑 + 结果网格」
- 实例切换即时生效；点表/视图生成 `select ... limit 100`，点函数/过程/事件拉 `SHOW CREATE` 填编辑器
- **直连库同样可用**（2026-11-06 修）：平台有账密 → 由 api-server 直接连库（`db_direct_service`），
  对象树 / 查询 / 执行 / 导出 / 导入 / 定时任务全部支持（当前直连限 mysql）

**16.2 SQL 控制台 ✅**
- 单入口自动识别只读/写：`select/show/describe/explain/with` 直接执行；
  其余（DML/DDL）仅 admin + 二次确认 + 返回影响行数
- **必须分号结尾**；多条语句时「选中哪条执行哪条」，未选中则拒绝多语句
- 结果导出 CSV（≤5000 行，UTF-8 BOM，中文文件名 RFC 5987）
- 审计：谁/集群/库 key/SQL 摘要/耗时/行数/影响行数/成败（不落结果）

**16.3 自动补全 ✅**
- 按 db_type 出关键字（MySQL/PG/Oracle 三套词表）
- 按当前账号可见的库名 + 表名补全（系统库过滤）
- 候选框**跟随光标**（镜像 div 量坐标）；↑↓ 选择、Enter/Tab 插入、Esc 关闭、Ctrl+Space 触发

**16.4 定时任务（cron → CSV 邮件）✅**
- 表 `db_sql_tasks`；字段：name / db_connection_id / sql / cron / recipients / is_enabled / last_run_*
- APScheduler 注册 + Redis 锁防多副本；启动时加载；只读校验 + 分号/单条校验
- 邮件：HTML 摘要（前 20 行）+ CSV 附件；收件人支持邮箱或平台用户名（取 email）
- 界面：实例列表「任务 N」徽标 + 时钟图标就地管理；支持 立即执行 / 测试(dry_run) / 编辑 / 删除
- 前置：**平台需配置 SMTP**（系统设置 → 通知/邮件）；未配置时任务明确失败并写 last_error
- 典型场景：每月 1 号 9 点跑一次「最近一个月」数据 → 发指定邮箱

**16.5 对象浏览 ✅（MySQL）**
- `information_schema` 的 tables / views / routines(FUNCTION|PROCEDURE) / events
- 平台侧过滤系统库；agent 端上限：表 3000、视图/函数/事件 各 500

**16.6 导入导出 ✅（MySQL）**
- 导出库/表：`CREATE TABLE` + `INSERT`；单表 ≤5000 行、总量 ≤5MB（超限标 truncated）
- 导入：`.sql`（按分号拆分执行）或 `.csv`（按表头 INSERT）；≤5MB；**仅 admin + 二次确认 + 审计**
- 已知限制：不处理 `DELIMITER`（含 BEGIN...END 的存储过程需单独执行）

**16.7 方言接口层 ✅（为多数据库铺路）**
- agent `internal/agentdb/dialect.go`：`Dialect` 接口（Query/Schema/DDL/Export/Import）+ `DialectFor(key)`
- 当前仅注册 mysql；新增 PG/Oracle = 实现一个 dialect + 注册，隧道/平台/前端不改

**16.8 待做（⏳）**
- PG / Oracle 方言实现（查询、元数据、DDL、导入导出）
- 导入支持 `DELIMITER`（存储过程/触发器脚本）
- 大文件导入导出：分片上传/下载、进度、断点续传（>5MB 场景）
- 表结构对比（两库 diff：表/列/索引/字符集）
- 导出格式扩展：仅结构 / 仅数据 / 结构与数据（当前固定两者都导）
- 导入前校验（目标表存在、列匹配、字符集）
- 结果网格：列排序/筛选/复制单元格（当前只有分页）
- 长任务可靠性：导出/导入/定时任务迁到独立 job-worker（见 R17）

**验收口径（已实现部分）**
- 读：`select count(*)` 返回正确行数；导出 CSV 中文不乱码
- 写：`update/insert/delete` 需 admin+confirm，返回影响行数，审计可查
- 分号：无分号/多语句均被拒
- 定时任务：dry_run 返回行数；真实执行在未配 SMTP 时明确报错并落 last_error
- 对象：表/视图/函数/过程/事件分类正确，系统库不出现

---

### R17 数据库同步 / 迁移（待排期，架构已定）— 估时待细化

> 老板 2026-11-06 提出：后续要考虑其他数据库、同步、迁移。**先定架构，不排期**。

**架构口径**
- 查询/对象/导入导出 → 继续走 agent（凭据不出集群）+ 平台编排，**不加新服务**
- **迁移**（结构 diff + 数据搬迁）→ 独立 `migration` 模块：任务表 + worker + 分阶段 + 进度 + 可回滚
- **同步**（持续增量）→ 独立 `cdc` 组件：MySQL binlog / PG logical replication，常驻进程，不属于请求-响应网关
- 触发拆服务的条件（满足两条再拆）：长任务（GB 级）、需要独立扩缩容、多租户配额、持续同步进程

**待细化项**
1. 迁移：源/目标库配置、结构 diff 报告、生成 DDL、数据搬迁（分批）、校验（行数/抽样）、回滚策略
2. 同步：全量初始化 + 增量位点（binlog file/pos、LSN）、冲突策略、断点续传、监控延迟
3. 任务框架：job 表（状态/进度/日志/重试）、worker 部署形态、与 API 的解耦
4. 权限与安全：迁移/同步属高危操作 → 审批流 + 审计 + 双人确认

---

### R15 工作负载级连续监控（Deployment × 容器，只读 Prometheus）— 1.5~2d（老板 2026-11-06 定）

**背景**：现「容器监控」是按 Pod 查的（`pod_metrics` 以 `pod_name` 为维度），
Deployment 滚动更新 / Pod 重建后曲线就断了——旧 Pod 的数据还在，新 Pod 换了名字，两条接不上。
老板要的是：**不管容器怎么变，Deployment 的监控始终连续**。

**老板口径（2026-11-06，已定）**
1. 一个 Deployment 下**按容器各画一条线**（CPU、内存），拼接跨 Pod 生效。
2. 网络同样每个容器一组（rx/tx 两条）；单容器 Pod = Pod 网络即容器网络（真实）；
   **多容器 Pod 共享 netns，网络拆不开**，只给 Pod 级汇总并明确标注，不伪造数据。
3. **只读 Prometheus，不自采、不落库**（平台零存储，`pod_metrics` 不动）。
4. **一套数据、多处入口**：工作负载详情页、大屏都用同一接口，只是入口不同。

**现状核实（2026-11-06 实测，三集群）**

| 项 | 结论 |
|---|---|
| CPU / 内存 | `container_cpu_usage_seconds_total`、`container_memory_working_set_bytes` **有 `container` 标签**（测试 1499 / 生产 1175 条序列）✅ |
| 网络 | `container_network_*` **无 `container` 标签**（uat/生产/测试均为 0 条），只挂在 pause 沙箱上 → 只能到 Pod 级 |
| 归属 | `kube_pod_owner` 可用（810 条）；`kube_replicaset_owner` **不存在** → Deployment 归属用 RS 名前缀正则；StatefulSet/DaemonSet 直接按 `owner_kind` + `owner_name` |
| `kube_pod_labels` | 存在但**没有任何 `label_*`**（kube-state-metrics 未开 label allowlist）→ 不能按 app 标签归属 |
| 保留期 | 集群 Prometheus `--storage.tsdb.retention.time=7d`，满足 7 天需求 |
| 阻塞项 | **agent 集群 range 通道不通**：`/prom/range` 报 `集群客户端不可用: client config 构建失败`（即时查询走隧道正常） |

**PromQL（已实测通过）**

```promql
# CPU（每容器；内存同款，换 container_memory_working_set_bytes）
sum by (container) (
  rate(container_cpu_usage_seconds_total{namespace="stg",container!="",container!="POD"}[5m])
  * on(namespace,pod) group_left(owner_name)
    kube_pod_owner{namespace="stg",owner_kind="ReplicaSet",owner_name=~"rocketmq-dashboard-[a-z0-9]+"}
)
# → {container="rocketmq-dashboard"} 0.0107 core；内存 376864768 B

# 网络（Pod 级，无容器维度）
sum by (pod) (
  rate(container_network_receive_bytes_total{namespace="stg",pod=~"rocketmq-dashboard-[a-z0-9]+-[a-z0-9]+"}[5m])
)
# → 66.29 B/s
```

**接口（唯一数据源）**
```
GET /api/v1/monitoring/clusters/{cid}/workloads/{kind}/{ns}/{name}/metrics?range=1h|24h|72h|168h
→ { containers:[{name, points[]}], network:[{pod, points[]}], pods:[{name, first_seen, last_seen}] }
```
- 服务端统一构造 PromQL（kind 决定归属写法）、30~60s 结果缓存、range 上限 7 天
- Prometheus HA（2 副本）固定择一，避免重复序列

**入口（同一接口，多处复用）**
1. 工作负载详情页 `ServiceDetail` →「监控」抽屉（每容器一条线 + Pod 段条）
2. 大屏 `monitor-bigscreen.html`「服务 / 容器监控」tab → 选中服务后按 Deployment 渲染
3. 预留：集群详情、告警详情跳转（不改数据层）

**前置依赖（必须先做）**
- agent 侧 `QueryPrometheusRange`：proto 加 RPC（Go + Python 双端重新生成）+ agent 实现（复用现有 Prometheus 代理，加 start/end/step）+ api-server 走隧道调用
- 不修这个，所有入口都只能取当前值，画不出历史曲线

**关键约束 / 风险**
1. 历史长度 = 集群 Prometheus retention（7d），不是平台承诺；看"7 天"时最早数据可能刚好过期
2. Prometheus 不可用 / PVC 写满 → 该集群监控无数据，页面需降级提示（不影响告警与自愈）
3. **多容器 Pod 网络不拆**（共享 netns，拆了就是复制假数据）；UI 必须标注
4. 单容器 Pod 的"容器网络"直接取 Pod 网络，数值真实，无需额外处理
5. 平台不再为监控落库 → 该功能不依赖 `pod_metrics`，也不增加其行数

**验收（照这个测）**
- 触发一次 Deployment 滚动更新（改镜像或重启）：容器名不变的前提下，Deployment 监控曲线**连续不断**
- 单容器 Deployment：每个容器出 rx/tx 两条线，数值 = 该 Pod 网络
- 多容器 Deployment：网络只出一条 Pod 级汇总，且有「共享 netns」标注；CPU/内存仍按容器各一条
- 7 天档位能正常出图（验证 retention 与 step 换算）
- 对比功能上线前后 `pod_metrics` 行数：不增长（验证零存储）
- 平台 `monitoring` 与集群 Prometheus 直查同一时刻数值一致（口径核验）

**估时**：range 通道 0.5d（含 proto 双端生成、agent 重启验证）；接口 + 缓存 0.3d；
前端组件（每容器多线 + 网络 + Pod 段条）0.5~0.8d；两处入口接入与联调 0.3d。合计 **1.5~2d**。

---

### R18 数据库连接：按类型区分认证方式与字段 — ✅ 已实现（2026-11-09，commit `846fd40`；迁移 `b5c6d7e8f9a0`）

**目标**：新建/编辑数据库连接时，**先选类型，再显示该类型对应的字段与校验**，不再用一套表单套所有库。
本轮先支持 **mysql / pg（postgres）/ oracle** 三种，后续新增类型单独加。

**字段与校验（按类型）**

| 类型 | 字段 | 必填 | 说明 |
|---|---|---|---|
| mysql | host / port(3306) / database / username / password | host、username 必填；**database 非必填** | 不填库名 = 连接实例但不选默认库（SQL 里需写全名 `db.table`） |
| pg | host / port(5432) / database / username / password | host、username、database 必填 | 与 mysql 同构，仅默认端口不同 |
| oracle | 连接方式（**Service Name / SID / TNS 描述符**）、host / port(1521)、service_or_sid、username / password、role（默认 / SYSDBA / SYSOPER） | host、service/sid、username 必填 | 字段与 mysql/pg 不同：**没有 database 概念**；TNS 描述符直接粘贴完整串 |

**交互**
- 表单顶部「数据库类型」下拉；切换类型时字段组随之切换，隐藏不相关字段
- 默认端口随类型自动填充（3306 / 5432 / 1521）
- 必填校验按类型动态生效
- 编辑已有连接时按实际类型渲染

**约束**
- 后端保存时按类型做校验（不能只靠前端）
- oracle / pg 的**执行能力**待方言实现（见 R16.7/R16.8）；本轮先解决「能建、能连、能测」
- 连接测试（/test）按类型走对应驱动

**验收**
- 选 mysql：数据库字段可留空，保存成功，连接测试通过
- 选 pg：库名必填，端口默认 5432
- 选 oracle：字段变为 Service Name/SID，端口默认 1521
- 切换类型不会把上一类型的值带过去（除 host/port/username/password 可保留）

**老板 2026-11-06 补充（已定）**
- Oracle **需要支持 `SYSDBA` / `SYSOPER` 角色**（连接串/驱动按 `mode=SYSDBA` 处理）
- Oracle **需要支持 TNS 描述符**（完整 `(DESCRIPTION=(ADDRESS=...)(CONNECT_DATA=...))` 直接粘贴）
- 因此 Oracle 的「连接方式」为三选一：`Service Name` / `SID` / `TNS 描述符`

**验收（补充）**
- 选 `SYSDBA` 能连上（测试连接返回成功，且会话权限符合预期）
- 粘贴 TNS 描述符能连接（字段校验：以 `(DESCRIPTION=` 开头、括号配对）
- 切换连接方式时清空上一种方式的残留值

---

### R19 集群管理内发布（对接 Jenkins）— ✅ 已实现（2026-11-09，`5e129e8`…`e496b07`；含 namespace 标签门控 / 映射 / 单发+批量 / Blue Ocean 阶段日志 / 发布历史 / 每用户 Token；回滚到指定构建号未做）

**目标**：在「集群管理」里直接发布服务，不用再去 Jenkins 页面点；后台统一配置 Jenkins 与服务映射，发布后弹出 pipeline 阶段视图与实时日志。

**后台配置（老板 2026-11-09 定：连接全局配 + 服务/Job 匹配按 namespace 配）**

**① Jenkins 连接（全局，放「系统设置 → 发布」）**
- Jenkins 实例：名称、地址、认证方式（用户名+API Token / 凭据 ID / 服务账号）、是否忽略证书
- 支持多实例并存；每用户 API Token 在「个人设置」里配（R19 认证方案 A，兜底服务账号）

**② 服务 ↔ Job 匹配（按 namespace 配，不是全局一张表）**
- 每个 namespace 一份「发布映射」：`服务名 → { Jenkins 实例, Job 名, 服务名参数, 分支参数 }`
- **一个 Job 对应多个服务（老板 2026-11-09 确认）**：多个「服务名」可映射到同一个 Job，
  发布时靠「服务名参数」区分；所以映射允许多行 `服务名 → 同一 Job 名`
- 配置入口：集群 → namespace 上编辑「发布映射」（选 namespace 后维护该 ns 的服务/Job 对照表）
- 点发布时按「当前服务名」查本 namespace 的映射；**未配映射的服务 → 提示「该服务未配置发布映射」**（不触发）

**③ 参数模板**
- 分支参数名（如 `BRANCH_TAG`）、服务名参数（默认 `SERVICE_NAME`/`Issue_command`）、构建参数默认值、是否允许覆盖

**④ 发布入口用 namespace 标签开关（老板 2026-11-09 定，不用白名单）**
- 机制：**看当前 namespace 上的 label**。带指定标签的 namespace，其下 **Deployment** 才会出现「发布」按钮；
  没打标签的 namespace **直接不渲染按钮**（不置灰、不提示），无需平台维护名单
  - 标签 = **开关**（控制按钮显隐）；上面的「发布映射」= **具体发布配置**（点发布时用），两层独立
- 标签 key/value 可配（系统设置 → 发布），默认建议 `autohealops.io/release=enabled`
- 标签由集群侧运维在 namespace 上打，**平台只读不写**；平台缓存的 namespace 列表要一并带 `labels`
- 后端同样校验：对无该标签的 namespace 调发布接口 → **403**（`namespace 未开启发布`），不能只靠前端隐藏
- 标签变更后按钮随之生效：namespace 缓存 TTL ≤ 60s，或发布页/服务列表刷新时重拉
- 构建/审计记录里保存「命中的 namespace 标签」，方便回溯为什么这个 ns 可以发布

**集群管理 → 发布（老板 2026-11-06 细化：按服务发布 + 单独选分支）**
- 入口：**在某个服务上点「发布」**（集群详情 / 服务列表行内操作）
- Jenkins Job 有两个参数：`分支` 和 `服务名`。平台侧：
  - **服务名 = 当前点击的服务**（只读，自动带入，不允许在弹窗里改）
  - **分支 = 需要单独选择**（下拉/可搜索，见下）
- 发布弹窗内容：
  - 服务：`<只读>`（当前服务）
  - Jenkins 实例：默认取「该 namespace 映射里配的实例」，可切换
  - **分支**：下拉选择，**来源 = Jenkins Job 参数定义**（老板 2026-11-06 定）
    - 调 `GET /job/{name}/api/json?tree=property[parameterDefinitions[name,type,choices]]`
    - 取「分支参数」（默认 `BRANCH_TAG`，可配）的 `choices` 作为下拉选项
    - 参数类型是 `ChoiceParameterDefinition` → 直接用；是 `StringParameterDefinition` → 退化为可输入（无列表）
    - Jenkins 取不到（不可达/无该参数）→ 允许手输，并给出提示
  - 参数名可配置：`服务名参数`（默认 `SERVICE_NAME`/`Issue_command`）、`分支参数`（默认 `BRANCH_TAG`）
- 触发后：**弹出 pipeline 面板**，按阶段展示（下载代码 → 打包 → 构建镜像 → 推送 → 部署），
  每个阶段可展开看日志；日志**直接取 Jenkins 构建日志**（不是平台自己的执行器日志）
- 用户：构建记录/审计里记录**当前登录用户**（谁发的、哪个集群、哪个服务、**选的分支**、job、构建号）

**验收（补充）**
- 在服务 A 上点发布：弹窗里服务名固定为 A，不出现服务选择器
- 分支下拉直接来自 Jenkins 参数 choices（与 Jenkins 页面完全一致）；参数缺失时提示并可手输
- 构建参数里 `服务名 = A`、`分支 = 选中值`，Jenkins 构建页面能对上
- **namespace 标签**：带 `autohealops.io/release=enabled` 的 namespace 下 Deployment 有「发布」按钮；未打标签的看不到按钮；绕过前端直接调接口 → 403

**技术要点**
- Jenkins API：`POST /job/{name}/buildWithParameters`（需 crumb + 认证）、`GET /job/{name}/{n}/api/json` 取阶段/状态、
  `GET /job/{name}/{n}/consoleText`（或 progressiveText）取日志
- 阶段信息：优先用 Blue Ocean / `wfapi/describe`（Pipeline 才有阶段）；普通 Freestyle 只有整体日志
- 日志推送：后端轮询 Jenkins progressiveText → SSE/WS 推前端（或前端直连轮询平台代理）
- 权限：仅 admin / 指定角色可发布；发布动作进审计

**验收**
- 后台配置好一个 Jenkins + 一个服务映射后，集群管理里能看到「发布」并选择分支触发
- 构建开始后弹出面板，阶段与 Jenkins 页面一致；日志随构建推进持续输出
- 构建结束状态（成功/失败）与 Jenkins 一致；审计里能查到操作人
- Jenkins 不可达/凭据错误时有明确提示（不吞异常）

**认证与发布身份（老板 2026-11-09 最终口径）**

现状：平台与 Jenkins 都是 LDAP。老板要求：**管理员配置一次、用户不配任何东西、只选分支**，且希望 Jenkins 里能看到真实用户。

结论（已实现）：
- **服务账号触发**（管理员在「系统设置 → 发布」配一次 Jenkins 连接：URL + 服务账号/API Token）
- 用户发布时只选**分支**（Job 由管理员在「服务↔Job 映射」里预先配好）
- 触发后用 Jenkins `submitDescription` 把**真实平台 LDAP 用户名写进构建描述**（Jenkins 构建页可见：`平台发布人: xxx`）
- 原因：Jenkins 的「Started by」就是调 API 的凭据，要显示真实用户只能每人配 Token（老板嫌复杂）或 LDAP 直通（密码红线+会话过期）——两者都不选
- （可选/保留）每用户 API Token 能力（方案 A）仍在后端，用户若自己配了就优先用，否则回退服务账号

方案对比（历史记录）：

| 方案 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| A. 每用户 Jenkins API Token | 用户填一次 Token（加密存）；发布用它 | Started by 是真实用户 | 每人都要配（老板 2026-11-09 否决：太复杂） |
| **B. 服务账号 + 构建描述标真实用户（已采用）** | 统一服务账号触发，描述/参数写平台用户名 | 零维护，用户只选分支；Jenkins 能看到发布人 | Started by 是服务账号（不是真实用户） |
| C. LDAP 直通 | 登录时把 LDAP 凭据转给 Jenkins 换会话 | Started by 真实用户 | 不能存 LDAP 密码（红线）；会话过期；复杂 |

**其它口径**
- 支持多 Jenkins 实例并存（每个实例一条配置，各自独立凭据）
- 分支选项来自 Job 的 `BRANCH_TAG` 参数 `allValueItems`（GitParameterDefinition，实测测试K8s Jenkins 返回 83 个分支）
- 是否支持「回滚到指定构建号」：先记为 ⏳（本轮先做「重新发布同一分支」）

---

### R20 集群巡检增强：无外部请求的「僵尸服务」检测 — ⏳ 待开发（老板 2026-11-06）

**目标**：巡检不只查资源，还要查「应用在跑但没人访问」的服务（长期零请求 = 疑似废弃/流量未接入）。

**检测口径（老板 2026-11-06 修订：没有 ingress 指标就走 nginx 日志）**
- 对象：namespace 下的 **Deployment**（运行中、副本 Ready）；报告里**记录到 Deployment 级别**
- 数据来源优先级：
  1. Ingress/网关指标（有则用）
  2. **前端 nginx 服务的访问日志**（没有 ingress 指标时的主要来源）：
     配置哪些 Pod/namespace 是 nginx（或按镜像名/容器名识别 `nginx`），
     采集 access log，按 `host / upstream / 路径` 归到后端 Deployment
  3. 都没有 → 标「无法判定」，不误报
- 判定：过去 N 天（默认 7 天）**外部请求量 ≈ 0**
- **排除探针**：kubelet 探针来源、`/health` `/ready` `/live` `/healthz` 等路径、探针 User-Agent
- 归因：nginx 日志里的 upstream/host 映射到 Service → Deployment（映射不到的单列「未归因」）
- **排除探针**：健康检查（readiness/liveness/startup）产生的请求不计入；
  来源过滤：kubelet 探针源 IP、`/health` `/ready` `/live` 等路径、探针 User-Agent
- 输出：巡检报告新增「疑似僵尸服务」清单（namespace / 服务 / 副本 / 最近请求时间 / 依据）

**约束/风险**
- 无 Ingress/网格的集群可能拿不到请求维度 → 明确标注「无数据，无法判定」，不误报
- 阈值可配（天数、请求数阈值、是否包含内部调用）
- 只读检测，不做任何变更

**验收**
- 造一个无流量的 Deployment（有探针）→ 巡检报告能列出，且不因探针流量误判为「有请求」
- 正常业务服务不出现误报
- 无指标来源的集群显示「无法判定」而不是列出全部服务
- 有前端 nginx 的集群：能从 access log 统计到后端 Deployment 的请求量，并正确排除探针请求
- 报告条目包含：namespace / Deployment / 副本数 / 最近一次外部请求时间 / 数据来源（ingress 指标 / nginx 日志）/ 判定依据

---

### R21 应用诊断助手：会话按天折叠（默认只展开当天） — ✅ 已实现（2026-11-09，commit `7a44313`）

**目标**：会话列表按日期分组，**默认只展开当天**，历史日期默认收起，减少干扰。

**交互**
- 左侧会话列表按「今天 / 昨天 / 更早日期」分组，组头显示日期 + 会话数
- 默认：今天展开；其它日期收起（点击展开）
- 记住用户展开状态（本次会话内或 localStorage），刷新后保持
- 同一天内按最后消息时间倒序；当前打开的会话若在收起的分组里，自动展开该组

**验收**
- 打开页面：只有「今天」展开，历史日期折叠
- 点历史日期组头可展开/收起
- 当前会话所在日期组始终可见

---

### R22 修复：手工重启过的 Deployment 永不告警（restartedAt 注解导致排除） — ✅ 已修复（2026-11-06）

**现象**：生产 `prod-eft/efreight-afbase` 于 15:22:55 因 liveness 探针失败被 kill 并重启（restartCount 0→1），
平台**没有建告警、没有通知**。

**根因**
1. 该 Deployment 模板上带有 `kubectl.kubernetes.io/restartedAt=2026-11-06T12:10:01+08:00`
   （此前手工/平台「滚动重启」留下，会一直被 Pod 继承）
2. executor `ListPodRestartInfo` 把该注解回传给平台（`RestartedAt` 字段）
3. `pod_restart_service._detect_restart_diffs` 见到 `restarted_at` 非空就 **`continue`**：
   既不上报增量、**也不写基线** → 该 Deployment 的所有 Pod 永久退出重启检测

**证据**
- Redis `podrestart:base:3` 里没有 `prod-eft/efreight-afbase-9d4b6565-n7rwb`
- `kubectl describe` 里探针失败证据齐全（`[15:23:25] Readiness probe failed`）→ 若未被排除会正常建单
- 生产最近一条 PodRestart* 告警是 11:02（另一批 Pod）；15:22 这次没有任何记录
- 生产共 25 个 Pod 带该注解（prod-eft 12 个），全部在排除名单里

**修复口径（老板 2026-11-06 最终定）**
1. **默认任何服务都没有宽限期**：重启次数增加就告警，不再因 `restartedAt` 注解跳过
2. **连续异常重启 3 次**后，该 Pod 进入**宽限期 10 分钟**：期间不再重复告警（防风暴）
3. **恢复即解除**：Pod ready 且本轮无新增量 → 清零连续计数、删除宽限期，下次异常照常告警
4. 连续计数/宽限期按 `Pod（namespace/name）` 粒度存在 Redis：
   `podrestart:streak:{cluster}:{ns/pod}`（TTL 1h）、`podrestart:grace:{cluster}:{ns/pod}`（TTL 10min）

**影响面**：生产 25 个 Pod；测试环境同类注解也会被排除

**修复记录（2026-11-06）**
- `_detect_restart_diffs`：**移除** restartedAt 排除逻辑（注解不再影响判定，仍照常写基线）
- `_process_increment`：宽限期内直接返回；否则建单→累计连续次数；达到 3 次后设置 10 分钟宽限期（本次仍告警）
- `scan_cluster` 恢复分支：Pod ready 且无新增量时删除 streak/grace 键
- 回归测试 `tests/test_pod_restart_annotation.py` 6 例（注解不排除/非法注解/首次发现/增量/无变化/阈值=3）
- 验证：`podrestart:base:3` 已包含此前被排除的 `prod-eft/efreight-afbase-9d4b6565-*`（809 条基线）

---

### R23 新增告警：Pod 被驱逐（Evicted）无任何告警 — ✅ 已修复（2026-11-06）

**现象**：生产环境当前有 **99 个 Evicted Pod**（95 个因 `DiskPressure`、4 个因 ephemeral-storage 不足），
平台**没有任何告警**。

**根因**
- `k8s-executor/internal/watcher/pod_watcher.go` 只检测两件事：
  1. 容器 `Waiting` 状态（CrashLoopBackOff / ImagePullBackOff / 配置错误等）
  2. `PodPending` 超过 5 分钟 → `PendingTimeout`
- **完全没有处理 `status.phase=Failed` + `status.reason=Evicted`**（全仓库仅 `diag_category.py` 里有个 `PodEvicted` 分类名，从未产生告警）
- Evicted Pod 的容器状态是 `Terminated`/空，不会进入 Waiting 分支，也不会是 Pending → 两个分支都命中不了

**修复方案**
1. watcher 增加分支：`pod.Status.Phase == Failed && pod.Status.Reason == "Evicted"`
   → 上报 `PodEvicted`（severity: medium；同一 workload 合并计数，避免一次驱逐刷几十条）
   → message 带 `status.message`（如 `The node had condition: [DiskPressure]`）+ 节点名
2. 恢复/关闭口径：Pod 被删除 或 同 workload 有新 Pod Ready → 关闭
3. 可选自动处置：**自动删除 Evicted Pod**（它们是已终止对象，删了不影响业务）；
   建议默认开启但受「自愈开关」控制，且记录审计
4. 节点维度联动：同一节点短时间内多次 Evicted → 关联/升级为节点问题（DiskPressure/内存），
   在节点页展示驱逐趋势与 Top 节点
5. 平台展示：集群详情/节点详情增加「Evicted 数量 + 原因分布」

**验收**
- 造一个 Evicted Pod（或直接 `kubectl` 构造 Failed/Evicted）→ 60 秒内产生 `PodEvicted` 告警并通知
- 同节点连续驱逐 10 个 Pod → 告警合并（不是 10 条独立告警），并提示节点条件
- 清理该 Pod（或同 workload 新 Pod Ready）→ 告警自动关闭
- 现有 CrashLoop/ImagePull/Pending 检测行为不变

**影响面**：生产 99 个 Evicted 对象；测试/UAT 同样不报

**修复记录（2026-11-06）**
- `pod_watcher.go` 新增分支：`Phase==Failed && Reason=="Evicted"` → 上报 `PodEvicted`（severity medium，
  message 含节点名 + 原始原因）
- 去重/节流：同 Pod（UID）24h 只报一次；同 workload（namespace+deployment）10 分钟一条；map 自动清理过期
- **年龄过滤**：只报最近 24h 内创建的；历史遗留 Evicted（生产现有 99 个均为 11-02 创建）不再刷屏
- 平台 `alert_auto_service`：`PodEvicted` **只通知、不做 AI 诊断/自动修复**；恢复扫描在「清理该 Pod /
  同 workload 新 Pod 就绪」后自动关闭
- 告警策略种子新增 `PodEvicted`（category B，medium）
- 回归测试：`internal/watcher/pod_evicted_test.go`（节流/去重 + 端到端 handlePodEvent：最近上报、
  重复不报、历史不报）
- agent 0.2.12 已发布并升级测试集群；executor 已重启（生产/UAT 生效）
- 验证：升级/重启后生产**没有**产生历史 Evicted 告警（年龄过滤生效），后续新驱逐会正常告警

---

### R24 告警/工单增加「环境」维度（集群 → 环境） — ✅ 已实现（2026-11-06，commits `4355d3e`/`a6dbdbf`/`bf2a8cf`）

**实现摘要**：`db_connections.env_tag` / `alerts.env` / `tickets.env` + 回填；executor/agent/DB 三条建单路径写 env；
告警列表返回 `env`+`source`、按 env 过滤；工单按 env 过滤；大屏（容器 Top10）、仪表盘（实时告警/异常 Pod/水位）、
故障诊断、处理记录的「集群」列统一改「环境」（中文标签 qa→测试/release→UAT/prod→生产，见 `frontend/src/utils/env.ts`）；
数据库告警使用独立 IM 模板（无项目/集群/命名空间）。**未做**：仪表盘按环境分组的 KPI 汇总（当前是筛选+标签）。

**背景**：告警/工单里的「集群」列，当初只按 k8s 集群设计；数据库告警没有集群概念
（`alerts.cluster_id=0`、`namespace` 为空）→ 列表里「项目/集群」是空的，无法按环境归类。
老板要求：**统一按「环境」聚合**，集群和数据库只是环境下的资源来源。

**目标模型**
- 环境（env）= 一级维度：生产 / UAT / 测试 …（`clusters.env_tag` 已存在；`db_connections` 增加 `env_tag`）
- 资源（集群 / 数据库连接 / 主机）都挂在某个环境下
- 告警/工单/诊断/报表：**展示与筛选用「环境」**，集群名作为二级信息（可展开看）

**落地项**
1. 数据模型
   - `db_connections` 增加 `env_tag`（已有分组 `group_name`，两者可并存：environ 用于统计，分组用于列表折叠）
   - `alerts` / `tickets` 增加 `env`（建单时写入：集群告警取 `clusters.env_tag`；DB 告警取连接 `env_tag`；
     agent 库取所属集群的 env）
   - 迁移时回填：集群按 `env_tag`，DB 按连接配置或默认「未分组」
2. 后端
   - 告警/工单列表接口：新增 `env` 过滤；返回 `env`
   - 统计（仪表盘/大屏/报表）：按 env 聚合（替换原来按 cluster）
   - IM 模板变量：新增 `{env}`，保留 `{cluster}`（兼容）
3. 前端
   - 告警列表、工单列表、诊断中心、报表：列名「集群」→「环境」，另加「来源」（集群名/数据库名）
   - 筛选下拉：环境（主）+ 集群/数据库（次）
   - 仪表盘卡片：按环境分组

**兼容**
- 老数据 `env` 为空 → 显示「未分组」，不阻塞
- 集群 `env_tag` 为空时回落为集群名（可后续补）

**验收**
- 数据库告警显示「环境=测试环境，来源=测试环境MySQL(stg)」，不再是空
- 集群告警显示「环境=生产，来源=生产集群」
- 告警/工单列表可按环境筛选；仪表盘可按环境聚合
- 老数据不报错，未设置环境显示「未分组」

**估时**：迁移+回填 0.5d；接口过滤/聚合 0.5d；前端列表/筛选/仪表盘 0.5~1d。合计 **1.5~2d**。



---

### R25 主机/虚拟机「分组 + 分组权限」；创建虚拟机必须先选分组 — ✅ 已实现（2026-11-09）

**背景**：主机管理（`hosts` 表 = 主机 + 虚拟机混管）目前只有平铺列表，没有分组也没有归属权限：
所有能进页面的账号都能看到全部主机、都能 SSH 探活/（后续 R8）登录操作。老板要求：
1. 主机与虚拟机都要有**组分类**；
2. 组要能**控制权限**（谁能看/谁能操作）；
3. **创建虚拟机必须先选分组**（不允许出现"无组虚拟机"）。

> **2026-11-09 老板补充口径**：**物理机和虚拟机都按同一套分组 + 分组权限做**，不存在"只给虚拟机分组"的说法；
> 唯一差异是强制项——**创建虚拟机必须选组**，物理机创建时允许暂不分组（后续可在列表里移入组，移组受 `manage` 权限约束）。

**数据模型**
- 新表 `host_groups`：`id` / `name`(唯一) / `description` / `parent_id`(可空，支持两层即可) / `sort` / `created_at` / `updated_at`
- `hosts` 增加：
  - `kind`：`host`（物理机）/ `vm`（虚拟机），默认 `host`，创建后不可改
  - `group_id`：外键 → `host_groups.id`；**`kind='vm'` 时 NOT NULL**，`kind='host'` 允许空（历史数据/临时采集）
  - `env_tag`：可选（与 R24 环境维度对齐，组也可带环境）
- 新表 `host_group_members`：`user_id` / `group_id` / `role`（`admin` 组管理 / `operator` 查看+操作 / `viewer` 只读）/ 唯一键 (user_id, group_id)
- 迁移：建一个「默认分组」（`name='默认分组'`，`is_default=1`），现存 `hosts` 全部挂进去；`kind` 默认 `host`

**权限口径**
- 平台 `admin` 角色：全部组、全部主机/虚拟机，可建/删组、指派组权限
- 其他用户：
  - 未加入任何组 → 主机管理页**看不到任何主机**，只有提示"请联系管理员分配主机组权限"
  - 组 `viewer`：只看列表/详情/采集状态，不能探活、不能编辑、不能删除、不能登录终端
  - 组 `operator`：可探活、可编辑进程路径/备注，可登录终端（R8），不能删主机、不能改分组归属
  - 组 `admin`：组内增删改 + 组内成员管理（不能再建新组）
- 后端统一依赖：`require_host_group_access(host_id, need="view|operate|manage")`；
  列表接口按「可见组集合」收敛（`host_group_members` ∪ 全量 if admin）
- 审计：建组/改组/移组/加人/删人 全部写 audit（复用现有审计入口）

**创建流程（关键约束）**
- 前端「新建」改为**两步向导**：① 选类型（主机 / 虚拟机）→ ② 选分组（必填下拉，仅列当前用户有 `manage|operator` 权限的组；无组可选时给"申请权限/让管理员建组"提示）
- 后端强校验：
  - `kind='vm'` 且 `group_id` 为空/不存在/无权限 → **400**（文案：`创建虚拟机必须先选择分组`）
  - `group_id` 指向不存在或已删除的组 → 400
  - 普通用户只能在自己有 `operator|manage` 权限的组下创建
- 主机（物理机）允许"暂不分组"，但列表里默认归到「未分组」并在提示条里标黄（避免长期无组）

**列表/筛选**
- 左侧分组树（全部 / 各分组 / 未分组），支持按组筛选、按类型（主机/虚拟机）筛选、关键字搜索
- 列表新增列：`分组`、`类型`；操作列按权限隐藏（无权限的按钮直接不渲染，不只置灰）
- 组内批量：探活、改分组（仅 `manage`）、导出

**告警/报表联动**
- 主机掉线等告警在 message/详情里带上「分组 + 类型」，便于按组排查（`alerts` 不新增列，展示时 join）
- 报表/巡检统计：可按组汇总主机在线率

**接口清单**
- `GET /host-groups`（返回可见组 + 成员数 + 主机数）、`POST/PUT/DELETE /host-groups/{id}`
- `GET/POST/DELETE /host-groups/{id}/members`（`user_id`, `role`）
- `GET /hosts?group_id=&kind=&keyword=`（按可见性收敛）
- `POST /hosts`（校验 kind/group_id 规则）、`PUT /hosts/{id}`（移组需 `manage`）、`DELETE /hosts/{id}`（需 `manage`）
- `POST /hosts/{id}/check`（需 `operate`）

**验收标准**
1. 新建虚拟机不选分组 → 前端不允许下一步，绕过前端直接调 API → 400 `创建虚拟机必须先选择分组`
2. 用户 A 只在「生产组」有 viewer → 列表只看到生产组主机；探活/编辑/删除按钮不可见，直接调接口 403
3. 用户 B 在「测试组」有 operator → 可探活/编辑，但不能删除、不能改分组
4. 组管理员可管理本组成员；普通 operator 看不到成员管理入口
5. 历史主机迁移后全部落在「默认分组」，页面无 500、无孤儿数据
6. 删除分组：组内主机转「未分组」（虚拟机不允许删除非空组 → 先移走，避免出现无组虚拟机）
7. 全部组/成员变更在审计里可查

**估时**：表结构+迁移+权限依赖 0.5d；接口（组 CRUD/成员/可见性收敛/创建校验）0.7d；前端分组树+向导+按权限渲染 0.8d → 合计 **2d**（与 R8 一起做可压缩到 1.2d 左右，因为 R8 已有分组树工作量）

---

### R26 集群级「自愈/自动诊断」开关（老板 2026-11-09）— ✅ 已实现（2026-11-09；一个总开关）

**背景**：不同环境故障性质不同。测试环境异常 Pod 很多是代码问题，自动 AI 诊断/自愈没有意义，还浪费算力。需要**按集群**控制是否自动诊断/自愈。

**口径（默认值，老板可改）**
- `clusters` 增加 `auto_heal_enabled`（bool，默认 **1=开**，保持现有行为）。**老板确认：一个总开关**（同时控制自动诊断 + 自动自愈）。
- **关闭后**：
  - 告警照常产生、照常通知（不丢故障）；
  - **不自动调用 AI 诊断**、**不自动生成修复方案/不自动执行自愈**；
  - 告警停在待人工处理（`pending`/`manual_required`），并在告警详情写明「该集群已关闭自动自愈」；
  - **手动诊断不受影响**（诊断助手 / 告警页手动「诊断」「处理」照常可用）；
  - 主动巡检里的 AI 诊断同样跳过（可配）。
- **不关闭时**：行为不变。

**入口/权限**
- 集群管理列表 + 集群详情：一个「自愈」列/开关（Switch），admin/operator 可操作，变更写审计。
- 前端显示当前状态（开/关），并提示「关闭后仅通知，不自动诊断/自愈」。

**落点**
- 模型/迁移：`clusters.auto_heal_enabled`（默认 1）。
- `alert_auto_service.process_alert` 入口判断：集群关闭 → 直接标记人工处理 + 通知，跳过 AI 全流程（省算力）。
- 主动巡检后台循环同样判断。
- 集群 API 返回该字段；前端开关调用更新接口。

**估时**：0.5~1d（含开关 UI + 跳过逻辑 + 单测）

---

### R27 监控采集落库演进（写入路径 / 保留期口径）— ❓ 待拍板（2026-11-10 讨论，暂不动手）

**背景（现状，代码核实）**
- 监控数据**采完直接写 MySQL**，没有消息队列、写缓冲或时序库：
  - 平台直连集群：executor（Go）每 60s 采集 → `db.Create()` / upsert 直写
  - agent 接入集群：agent 集群内采集 → 隧道 event 帧（`PodMetrics` / `CollectSnapshot` / `Events` / `HostMetrics` / `DbMetrics`）→ api-server（Python）`session.add_all()` + `commit`
- 落库表：`pod_metrics`、`pod_metrics_5m`（5 分钟降采样）、`quota_snapshots`、`resource_snapshots`、`k8s_events`、`db_metric_snapshots`、`slow_sql_records`
- 不落库：日志（查日志源 ES/Loki）、Prometheus 曲线（按需 `query_range`，仅缓存数据源解析结果 60s）
- 现存量级：`resource_snapshots` 700 万+ 行、`db_metric_snapshots` 87.8 万行；`pod_metrics` 原始保 1 天 + 5m 聚合保 7 天

**本次讨论结论**
1. **暂不引入 Redis write-behind**。理由：实测 digest 中已无 ≥0.5s 查询，最慢是事件批量 upsert（~160ms）；历史上 11~14s 的慢查询都是「取最新一条」的查询写法问题（已用时间窗+索引修掉）。当前瓶颈不在写入侧，引入缓冲属于「先加组件再找收益」。
2. Redis 前置只能解决**写入侧削峰**和**最新值读取**，**不能消除查询侧慢 SQL**（历史/趋势聚合仍扫 MySQL 大表）。
3. 若将来要做，必须用 **Redis Stream（消费组 + ack）**，不能用 KV/hash 当缓冲区；并且要求：幂等键（`pod_metrics` 目前无唯一键，重试会重复）、双阈值 flush（5~10s 或 2000~5000 行先到先发）、`MAXLEN` 截断、`maxmemory` 显式限制 + `noeviction`、独立 db/实例隔离缓存。

**需求项（按优先级）**

| # | 需求 | 落点 | 优先级 | 说明 |
|---|---|---|---|---|
| R27-1 ✅ | **保留期口径统一**（2026-11-10 完成） | executor `internal/metrics/collector.go` 的 `cleanup()` vs api-server `metrics_rollup.cleanup_metrics()` | P1 | 两端统一读 `POD_METRICS_RAW_RETENTION_HOURS`（默认 24h）/ `POD_METRICS_ROLLUP_RETENTION_DAYS`（默认 7d）；executor 原写死 7 天已改 |
| R27-2 ✅ | **`pod_metrics` 幂等键 + 分区**（2026-11-10 完成） | 迁移 `j7e8f9a0b1c2`：`uk_pod_metric_sample` + PK `(id,sampled_at)` + 按天分区 | P1 | 写入改 `ON DUPLICATE KEY UPDATE`（Go GORM `OnConflict` / Python `mysql_insert`）；`maintain_pod_metric_partitions()` 每小时 drop 过期分区，尾巴仍由分批 DELETE 保证 24h 精确 |
| R27-3 ✅ | **仪表盘最新值走 Redis**（2026-11-10 完成） | `stats.py::_latest_pod_usage()`，键 `pm:latest_usage:v1`，TTL 15s | P2 | 仪表盘/大屏 5s 轮询 → DB 聚合查询压力降到 1/3；缓存失效/结构异常自动回退直查 |
| R27-4 ✅ | **查询强制带时间窗**（2026-11-10 完成） | `docs/REVIEW-CHECKLIST-QUERIES.md` + `pod_events`/Pod 名回溯补窗口 | P2 | 检查单含铁律 6 条、真实反例 5 例（含实测数字）；历史类接口统一 `min(hours, 24*30)` 钳制 |

**备选方案（触发条件未到，先记录口径）**

- **方案 A：Redis Stream write-behind**
  - 链路：采集 → `XADD`（`MAXLEN`）+ 最新值 hash → flusher `XREADGROUP` 批量事务写 MySQL → 成功 `XACK`
  - 同步间隔：**5~10s 或累计 2000~5000 行，先到先发**；单批设上限（HDD 上大事务同样慢）
  - 数据可见性：DB 落地有 5~10s 延迟，排查口径要写明「Redis 有、DB 可能还没有」
  - **风险（务必先解决）**：当前 Redis `192.168.6.99:6379` 为 `maxmemory=0` + `noeviction`，与 MySQL 同机（7G RAM），db0 与缓存/锁共用。不设上限就承载监控数据 = 可能把 MySQL 一起拖死。估算：3000 Pod × 1440 条/天 × ~100B ≈ 430MB/天
- **方案 B：时序库（VictoriaMetrics / remote write）**
  - 指标类切时序存储（内建降采样与过期），MySQL 只保留「最新快照 + 业务状态」，`resource_snapshots` 可降级为每命名空间一行 upsert
  - 属大改，仅在其他方案都不够时考虑

**触发条件（满足任一才启动方案 A/B）**
1. `pod_metrics` 日增量或总行数较当前翻 5~10 倍；
2. 出现「DB 抖动/慢查询导致采集断档、丢样本」的实际事故；
3. 纳管集群数或 Pod 规模翻倍，批量写入开始进入慢 SQL 榜。

**验收标准（R27-1 ~ R27-4）**
1. 两种接入方式下，`pod_metrics` 原始数据保留时长一致，文档与页面对「可查时长」的表述统一
2. 同一 `(cluster_id, namespace, pod_name, sampled_at)` 重复写入不产生重复行
3. `pod_metrics` 历史清理走分区 drop，不再对大表执行 `DELETE ... WHERE sampled_at < ?`
4. 仪表盘首屏「最新值」不再触发全表/全历史扫描；接口 P99 与现状持平或更好
5. 关键监控查询均带时间窗（review checklist 落地）

**估时**：R27-1 0.3d；R27-2 0.7d（含迁移与历史数据验证）；R27-3 0.5d；R27-4 0.3d → 合计 **约 1.8d**（方案 A/B 不在本次估算内）
