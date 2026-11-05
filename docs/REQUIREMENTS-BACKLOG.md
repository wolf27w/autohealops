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

### R1 PostgreSQL 支持（经 agent 查库 + 健康采集）— 0.5~1d

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

### R3 数据库监控增强 — 2~3d

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
- 页面上对 agent 库禁用「手动查询」（因为没有凭据）；健康检查/异常点照常可用（用最新上报数据，不走隧道实时查）。

**验收**
- agent 侧配 1 个 MySQL + 1 个 PG → 平台数据库监控页出现两条 `来源=agent` 的记录，指标曲线连续
- 断开隧道时记录变灰/标 stale，恢复后自动续上
- 平台库里查不到外部库的密码

**估时**：MySQL 路径 1d（可复用现有 HandleHealth 采集逻辑）；PG 0.5d（依赖 R1 驱动）；平台落库+UI 0.5d。

**两种数据库来源并存（老板 2026-11-05 定）**
| 来源 | 配置在哪 | 采集方式 | 凭据存哪 |
|---|---|---|---|
| **直连**（本地/网络可达的库）| 平台「数据库连接管理」 | api-server 直连采集（现有 `db_collector`）| 平台库（加密） |
| **agent**（外部集群内的库）| agent 侧 Secret / env | agent 在集群内采集后经隧道上报 | **只在对方集群** |

- 两者在「数据库监控」页**统一展示**，带来源徽标；指标/慢 SQL 同一套表、同一套趋势图口径。
- 现有 `cluster_db_connections`（平台存凭据 + 经隧道临时查询）保留为**按需排查**入口，不做长期采集。

**命名规则（老板 2026-11-05 定：名称以 agent 配置为准）**
- agent 配置里每项必须有 `key`（稳定标识）和可选 `name`（显示名，缺省=key）：
  ```json
  {"key":"biz-mysql","name":"业务库(MySQL)","type":"mysql","host":"...","username":"...","password":"..."}
  ```
- 平台侧唯一键 = **(cluster_id, source_key)**；显示名 = 配置里的 `name`（没有则 `key`）。
- 页面上展示为「名称 + 来源集群」，避免两个集群都有 `biz-mysql` 时混淆。
- 改名不影响归属（key 不变 → 更新同一条记录）；key 改了视为新数据库。
- 直连库沿用现有 `db_connections.name`（全局唯一），来源标记 `direct`。

### R7 Agent 侧配置主机（虚拟机）并 SSH 代采 — 1~1.5d（老板 2026-11-05 定）

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

### R8 主机（虚拟机）管理增强：分组 / 批量导入 / 凭据保存 / 堡垒机登录 / 文件传输 / 操作留痕 — 6~8d

**背景**：现有「主机管理」只能单条添加、SSH 直连采进程，没有分组、批量导入，也不能直接登录操作。
老板要求做成「类堡垒机」：能分组、能批量导、能保存密码/密钥、能直接在页面登录操作、并且全程留痕。

#### 8.1 分组
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

### R9 主机采集支持密码认证（改用 asyncssh）— 0.5d（老板 2026-11-05 定：走 B 方案）

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

### R11 诊断模板路由可配置（R10 的最小可用版，老板 2026-11-05 定：先做这个）— 0.5d

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

### R12 应用故障分类体系（把「应用错误」变成可记录的 fault_type）— 1.5~2d（老板 2026-11-05 提）

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
