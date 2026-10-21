# AutoHealOps 平台 —— 重复项审计（2026-09-20）

> 整理：小吴　范围：前端页面/入口、配置项、数据字段、代码实现、文档
> 图例：🔴 真重复（建议清理）｜🟡 半重复/职责交叠（建议明确边界）｜🟢 有意保留（兼容/兜底，不建议动）

---

## A. 页面 / 入口重复

| # | 重复内容 | 位置 | 说明 | 建议 |
|---|---|---|---|---|
| A1 🔴 | **三套看板** | `/stats`（统计报表，旧）· `/dashboard`（仪表盘 v2）· `/screen`（大屏=仪表盘） | `/stats` 的指标与仪表盘重叠（集群数/告警/成本/成功率/MTTR/状态分布）；且 `/stats` 用的 5 个旧接口与新 `/stats/dashboard` 数据重叠 | **下线 `/stats`**，或改造成「报表导出 + 历史趋势」专职页 |
| A2 🟡 | **合并页 vs 独立路由（同一页面两套 URL）** | `/alert-handling`(内嵌 AlertList/FaultDiagnosis/ApprovalList/ProcessingRecords/BatchOperation)、`/strategy`(AlertPolicies/SelfHealRules/ApprovalFlows)、`/notify-center`(NotificationConfig/IMGroups)、`/user-permission`(UserManagement/ProjectManagement/Permissions)、`/ai-models`(AIModelConfig/PromptTemplates/AICallLogs) ⇄ `/alerts` `/approvals` `/records` `/batch` `/fault-diagnosis` `/alert-policies` `/self-heal-rules` `/approval-flows` `/notifications` `/im-groups` `/users` `/projects` `/permissions` `/prompts` | 共 17 条孤立路由（菜单里没有），同一组件两套入口 | 保留 2~3 个常用深链（如 `/alerts`），其余改为 `Navigate` 重定向到合并页 |
| A3 🟡 | **诊断入口交叉** | `/diagnostic`（数据库故障诊断）与 `/fault-diagnosis/cluster`、`/fault-diagnosis/app`、`/agent`（应用诊断助手） | 「数据库诊断」在 DiagnosticCenter 内还有一个 Tab 叫「数据库诊断」；「应用诊断」既有故障台账入口，又有会话式助手 | 明确：助手=会话式深挖，台账=批量看结果；页面文案区分 |
| A4 🟡 | **AIConfig vs AIModelConfig** | `pages/AIConfig.tsx`（Tab 壳）· `pages/AIModelConfig.tsx` | 壳页与内容页命名/职责交叠 | 保留壳页，内容页改名 `AIModelSettings` 或合并 |

## B. 配置重复

| # | 重复内容 | 位置 | 说明 | 建议 |
|---|---|---|---|---|
| B1 🟢 | ES 日志配置 | `system_settings.es_*` ⇄ `log_sources` 表 | 已按老板要求去重为「日志源页统一管理」，settings 保留**只读兜底** | 保留（迁移期兜底），稳定后可删 settings 键 |
| B2 🔴 | **日志索引两处可配** | `log_alert_rules.index_pattern` ⇄ `log_sources.index_pattern` | 规则既能选日志源、又自带索引字段；两处配同一件事，容易互相打架 | 规则未选源时用索引字段；**选源时应忽略规则索引**（或在 UI 上禁用并加说明） |
| B3 🟡 | **飞书配置两处** | `system_settings.feishu_app_id/secret`（应用凭证：个人消息 + 长连接）⇄ `notification_configs` 的 feishu 渠道 webhook（群卡片） | 两个飞书入口，分散在「系统设置 → 可观测性」和「通知配置」 | 统一到「通知配置」一处，或在系统设置里加「什么是应用凭证/什么是群机器人」说明 |
| B4 🔴 | **静默/维护窗口两套机制** | 统一通知策略 `alert_notify_policy.silence_windows` ⇄ `clusters.maintenance_window` | N8 同时消费两套；语义重叠（一个全局时间窗，一个按集群） | 保留两层但**在通知配置页显式说明优先级**；或集群维护窗口收敛进统一策略的「按集群窗口」 |
| B5 🔴 | **风险策略死配置** | `system_settings.risk_policy`（JSON，**无任何代码读取**）⇄ `decision_policies` 表（4 条，`/decisions` API 在用） | 真重复 + 死数据 | 删除 settings 里的 `risk_policy` 键 |
| B6 🟡 | **提示词两处** | 平台 `prompt_templates` 表（7 条，可编辑）⇄ ai-service 内置 `prompt_engine.DEFAULT_TEMPLATES` + `agent_service.SYSTEM_PROMPT`（硬编码） | 内置作为兜底可接受；但 `SYSTEM_PROMPT` 对话式诊断提示词不在平台可管 | 把 agent 的系统提示词也纳入平台模板（或明确标注「内置、平台不可改」） |
| B7 🔴 | **prompt_versions 表** | `prompt_versions`（0 行）+ `PromptVersion` 模型（api-server / ai-service 各一份） | 表与模型都在，但**无任何读写代码** → 死结构 | 确认无用后写迁移删除 |

## C. 数据 / 字段重复

| # | 重复内容 | 位置 | 说明 | 建议 |
|---|---|---|---|---|
| C1 🟡 | **诊断分类字段三处** | `alerts.diag_category`（N3 生成列）· `knowledge_entries.diag_category` · `knowledge_entries.category`（pod_crash/db/application/…） | 知识表上两个分类字段语义部分重叠 | 保留 `diag_category` 为检索口径；`category` 明确为「展示标签」 |
| C2 🔴 | **alerts.source 死列** | `alerts.source`（N6 迁移新增，`server_default=''`）· 与 `resource_type`（pod/node/log/db）语义重叠 | **无任何代码读写**（模型未映射） | 删除该列，或明确启用（谁写、用在哪） |
| C3 🟡 | **通知记录两张表** | `notification_logs`（296，渠道发送）· `in_app_messages`（202，站内消息） | 字段（标题/内容/时间/接收人）重复度高，前端展示分散 | 保留分表但统一「消息中心」聚合展示 |
| C4 🟡 | **报告类别枚举两套** | `diagnostic_reports.task_type`（app/db/cluster）· 新 `diag_category` 口径 | 取值口径需对齐 | 统一枚举来源（用 `diag_category` 常量） |
| C5 🟡 | **三套「策略」概念** | `alert_policies`（13 条，A/B/C/D + 级别）· `self_heal_rules`（**0 条**）· `decision_policies`（4 条，风险评分） | 边界需明确；`self_heal_rules` 事实未用 | 确认 `self_heal_rules` 是否下线；在「策略配置」页写清三者职责 |

## D. 代码重复

| # | 重复内容 | 位置 | 建议 |
|---|---|---|---|
| D1 🔴 | `infer_deployment_name` ×2 | `services/alert_auto_service.py` · `services/pod_restart_service.py` | 抽 `app/utils/pod.py` |
| D2 🔴 | `ExecutorClient()` 直接实例化 **14 处** | `api-server/app` 各处 | 统一单例/依赖注入 |
| D3 🟡 | 各模块自写 `_to_dict` / `_serialize` | self_heal_rules / alert_policies / log_alert_rules / report_schedules / log_sources / db_connections | 抽统一序列化工具 |
| D4 🟡 | **两服务同名模块各一份** | `es_client.py` `http_client.py` `database.py` `config.py` `security.py`（api-server ⇄ ai-service） | 抽共享包（或至少对齐实现与常量） |
| D5 🟡 | 前端 `http` 门面 ×2 | `services/api.ts` · `pages/Tickets.tsx`（页面内自建） | 统一用 `services/api.ts` |
| D6 🔴 | 前端本地 interface ×15 页 | 各页面自写类型（与 `types.ts` 重复） | 收敛到 `types.ts` |
| D7 🟡 | 级别/状态标签映射重复 | `SEV_COLOR`/`ALERT_STATUS_LABEL`/`STATUS_LABEL` 各页自写（已有 `SeverityTag`/`StatusTag` 组件） | 页面统一用公共组件 |
| D8 🟡 | 健康探活职责重叠 4 处 | `/health`、`/ready`、`/metrics`、`/stats/platform-health`（前两者 DB/Redis 各查一次，platform-health 再查一次） | `platform-health` 复用 `/ready` 结果 |

## E. 文档重复

| # | 重复内容 | 位置 | 建议 |
|---|---|---|---|
| E1 🔴 | **两份开发总纲** | 根目录 `PROMPT.md`(89KB) · `PROMPT-FINAL.md`(94KB)（diff 977 行，同源两版） | 删旧留新（或归档到 `docs/archive/`） |
| E2 🟡 | 需求类文档重叠 | `REQUIREMENTS-BACKLOG.md`(40KB) · `PENDING-REQUIREMENTS-DETAIL.md` · `DASHBOARD-V2-REQUIREMENTS.md` · `DESIGN-2026-09-10.md` · `CLUSTER-MGMT-REQUIREMENTS.md` + `CLUSTER-MGMT-UI-UPGRADE.md` · `AGENT-*.md`（4 份） | 合成「一份主账 + 分册」，主账只留结论与状态 |

## F. 有意保留（不算问题）

- `docker-compose.yml`（本地开发）vs `k8s/*.yaml`（部署）—— 两套环境，正常
- `/screen` 与 `/dashboard` —— 同一组件两种外壳（老板要求一致）
- `send_alert_notification` → `notify_alert` 兼容转发 —— 迁移期兼容

---

## 执行结果（2026-09-20 下午，干完的）

**备份**：git tag `backup/before-dedup-20260920`；数据导出 `logs/archive/dedup_backup_20260920/data/*.json`；代码副本 `.../code/`。

**⚠️ 复核纠错（审计时的误判，已核实后撤回）**：
- `system_settings.risk_policy` **不是死配置** —— `/decisions/risk-policy` GET/PUT 正在读写它（真正无关的是 `decision_policies` 表，那是另一类策略）
- `prompt_versions` **不是死表** —— ai-service `prompt_engine.update()` 会写历史版本，只是还没有模板被改过所以 0 行

**已删除/已收敛**：
| 项 | 处理 |
|---|---|
| C2 `alerts.source` 死列 | ✅ 迁移 `l5m6n7o8p9q0` 删除（经全仓核实无任何读写） |
| D2 `ExecutorClient()` 14 处 | ✅ 收敛为 `clients/executor_client.py` 进程级单例 |
| D1 `infer_deployment_name` ×3 | ✅ 收敛为 `app/utils/pod.py`（3 处改为引用） |
| A1 旧统计页 `/stats` | ✅ 拆分：导出+定时推送 → 新 `/reports` 报表页；重叠的统计卡片/图表删除；页面删除，`/stats` 重定向到 `/reports` |
| A1 旧接口 3 个 | ✅ `/stats/overview`、`/stats/repair-success-rate`、`/stats/alert-status-distribution` 删除（仅被该页使用） |
| A2 孤立路由 14 条 | ✅ 改为重定向到合并页（`/alerts→/alert-handling` 等），外部深链仍可用 |
| D5 `Tickets.tsx` 自建 http | ✅ 改用 `services/api.ts`（含新增 patch 与工单接口） |
| D6 工单本地类型 | ✅ 收敛到 `types.ts`（Ticket/TicketComment/TicketDetail） |
| D6+ 跨页重复类型 | ✅ `PodTarget`(2页)、`MetricPoint`(2页)、`AICallLog`(2页)、`DBConn/DBConnection`(3页) 合并为 types.ts 共用类型；跨页重复已归零 |
| D6 其余本地 interface | 🟢 保留：62 个均为**单页自有视图模型**（名字不重复），搬进 types.ts 只是换位置、不减重复 |
| B2 日志索引两处 | ✅ 消除歧义：**选了日志源以源索引为准**，未选才用规则索引；告警文案显示实际生效索引；前端加提示 |
| B4 静默窗口两套 | ✅ 保留两层，前端明确「两者任一命中即静默」的优先级说明 |
| E1 `PROMPT.md` 旧版 | ✅ 删除（归档副本在 backup 目录） |
| D8 探活重复 | ✅ `platform_health_service.check_dependencies()` 共用（/ready 与 /stats/platform-health） |
| D7 级别/状态标签 | ✅ 新增 `constants.ts` + 统一用 SeverityTag/StatusTag |
| E2 文档索引 | ✅ 新增 `docs/README.md`（主账/分册/归档） |

**后续追加清理（同批次）**：
- D8 健康探活重复 → 抽 `platform_health_service.check_dependencies()`，`/ready` 与 `/stats/platform-health` 共用
- D7 级别/状态标签 → 新增 `frontend/src/constants.ts`（文案）+ 统一用 `SeverityTag`/`StatusTag` 组件（Dashboard 已切换）
- A3/B3/C5 口径说明 → 在「数据库故障诊断 / 系统设置飞书 / 策略配置」页面写明各自边界，避免看起来像重复
- E2 文档收敛 → 新增 `docs/README.md` 索引（主账 + 分册 + 历史归档）

**保留未删（经核实后判定：非重复或不建议动）**：
- `risk_policy` 配置、`prompt_versions` 表+模型（误判，见上）
- `self_heal_rules`（0 条但是老板 09-10 明确保留的「决策覆盖层」功能）
- 飞书「应用凭证（个人消息）」vs「群机器人 webhook」——职责不同，仅补说明
- `notification_logs` vs `in_app_messages` —— 渠道发送记录 vs 站内消息，字段相似但语义不同
- 两服务同名基础模块（es_client/http_client/database/config/security）—— 独立部署的边界，抽共享包收益小于风险
- 其余各页本地 `interface`（62 个）—— 经脚本核对均为**单页自有视图模型**（跨文件零重复），搬到 types.ts 只是换位置，未动
- `notification_logs` vs `in_app_messages` 合并 —— **建议不做**（改动大、涉及历史数据迁移风险），保留现结构
- 两服务同名基础模块抽共享包 —— **建议不做**（会让独立部署的两个服务产生代码耦合）
- `self_heal_rules`（0 条）—— 保持菜单元件不变（页面自身已说明是「决策覆盖层 + 影子模式」，隐藏反而容易被误认为功能丢失）

> 老板 2026-09-20 13:48 明确：**以上按小吴建议执行（即保留现状，不做这三项改动）**。

## 建议清理顺序（按性价比）

1. **B5 risk_policy 死配置**、**B7 prompt_versions 死表**、**C2 alerts.source 死列** —— 纯清理，零风险
2. **A1 旧统计页 `/stats`** —— 影响用户体验（三个看板）
3. **D1/D2 后端重复实现**、**D6 前端类型收敛** —— 技术债
4. **B2 日志索引两处**、**B4 静默窗口两套**、**C5 三套策略** —— 需要老板口径确认后统一
5. **E1/E2 文档收敛** —— 顺手做
