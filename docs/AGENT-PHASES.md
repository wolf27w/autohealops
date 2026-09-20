# Agent 阶段规划

> 状态：**已定稿**（2026-08-27 阶段 7 细化完成，老板确认按文档定稿）
> 依据：`AGENT-EVALUATION.md`（评估与设计）、`AGENT-ROADMAP.md`（开发清单）
> 核心定位：**Agent = Pod 报警自愈**；应用诊断 / 数据库诊断是诊断能力（做不了 agent）

---

## 阶段总览

| 阶段 | 主题 | 状态 | 说明 |
|---|---|---|---|
| 阶段 1 | 基础自愈闭环 | ✅ 已完成 | 告警→诊断→风险评分→自愈/审批→通知 |
| 阶段 2 | 根因分层决策 | ✅ 已完成 | 诊断输出根因层 A/B/C/D，决策前路由 |
| 阶段 3 | 诊断增强 | ✅ 已完成 | 进容器查崩溃日志/进程，日志分析落盘 |
| 阶段 4 | 告警治理 | ✅ 已完成 | Deployment 聚合 + 风暴抑制 + 应用问题识别 |
| 阶段 5 | 知识库治理 | ✅ 已完成 | 分类 + 去重 + 价值过滤 |
| 阶段 6 | 规则引擎 + 边界演进 | ✅ 已完成核心 | 场景规则 + 影子模式 |
| 阶段 7 | Agent 决策循环 + 统一工具 | ⏳ 待做 | ReAct 循环、工具注册表、任务记忆 |
| 阶段 8 | 全自主编排 + 发布对接 | ⏸ 缓行 | 阶段 7 跑稳后再议 |

---

## 各阶段说明

### 阶段 1：基础自愈闭环 ✅
- 告警产生 → AI 诊断 → 生成修复方案 → 风险评分 → 自动执行 / 审批 → 通知
- 已有能力：告警闭环、审批流、自愈时段策略、通知路由

### 阶段 2：根因分层决策 ✅
- 诊断结果增加「根因层」：A 资源/编排层、B 应用代码/配置层、C 数据库层、D 基础设施层
- 决策前按根因层路由：A 层才走 Pod 自愈；B 转发布、C 转 DBA、D 转工单
- 落地：`root_cause_layer` 字段 + `_infer_layer` 兜底 + 决策前路由

### 阶段 3：诊断增强 ✅
- 诊断「进容器查」：崩溃容器采 `logs --previous`、运行容器 `exec ps aux`（只读）
- 诊断模板补全日志/指标/容器信息字段
- 崩溃日志分析结果落盘知识库（带分类 + 价值过滤）

### 阶段 4：告警治理 ✅
- 告警按 Deployment 聚合（不按 Pod 实例分散），event_count 累加
- 风暴抑制：5 分钟同源超 5 次不重复报警
- 应用问题识别：崩溃日志代码异常 → 跳过 AI 诊断，直接转人工
- 恢复后：同源历史告警自动关闭（留痕）
- 镜像类故障短路 + 循环抑制（反复失败转人工）

### 阶段 5：知识库治理 ✅
- 分类（pod_crash/db/application/resource/infra）
- 去重（同类型同解决方法只留一条）
- 价值过滤（删除/重启/关闭等临时操作不落库）

### 阶段 6：规则引擎 + 边界演进 ✅（核心）
- 场景规则库（force_manual/force_auto 覆盖 + 影子模式）
- 自动化边界可配置、可演进
- 待补：规则 API 前端管理页（已有基础，待完善）

### 阶段 7：Agent 决策循环 + 统一工具 ⏳
- **统一工具注册表**：应用诊断/DB 采集/知识库检索封装成统一工具（已有 tool_registry 基础）
- **Agent 决策循环（ReAct）**：诊断「多轮自主调工具」，而非单次 AI 调用
- **任务记忆**：同资源历次诊断/自愈/审批注入上下文
- **执行决策矩阵完整落地**：根因层 + 风险分 + 规则引擎统一决策
- 👉 细化设计见下文「阶段 7 细化设计」

### 阶段 8：全自主编排 + 发布对接 ⏸（缓行）
- 告警闭环路径选择交给 Agent 自主
- B 层发布系统对接（Jenkins/ArgoCD 等，需先确认选型）

---

## 阶段 7 细化设计（2026-08-27 细化）

> 依据现有代码：`api-server/app/services/tool_registry.py`（已实现注册表+护栏）、`decision_engine.py`（四维风险评分）、`k8s-executor/internal/executor/pod_status.go`（结构化 Pod 状态）。
> 原则：**诊断循环只调只读工具；operate 工具只由决策引擎触发，不暴露给 LLM。**

### 7.1 ReAct 决策循环

**目标**：把报警自愈的诊断从「单次 AI 调用」升级为「多轮自主调工具」的循环（thought → tool_call → observe → 继续/收敛），AI 自己决定「下一步查什么」。

**循环结构**（伪代码）：

```
输入：告警(alert) + 任务记忆(memory) + 工具清单(tools)
round = 0
observations = [初始上下文：告警信息 + 任务记忆摘要]

while round < max_rounds:
    resp = llm.chat(system_prompt + observations)   # 产出 thought + action
    if resp.action == "tool_call":
        result = tool_registry.execute_tool(resp.tool, resp.args, context)  # 走护栏
        observations.append(f"[工具 {resp.tool}] {result.output[:6000]}")
        round += 1
    elif resp.action == "final_answer":
        return resp            # {root_cause_layer, diagnosis, suggestion}
    else:
        return single_shot(observations)   # 无法解析 → 单次兜底

return single_shot(observations)            # 达到上限 → 强制收敛
```

**关键参数**（可配，`system_settings` 的 `agent_policy` JSON）：

| 参数 | 默认值 | 说明 |
|---|---|---|
| max_rounds | 5 | 最多 5 轮工具调用（不含最终回答） |
| per_tool_timeout | 15s | 单次工具执行超时 |
| total_timeout | 90s | 整个循环超时 |
| tool_output_truncate | 6000 字符 | 单次工具输出截断长度（防上下文爆炸） |

**终止条件**（任一触发即收敛）：
1. LLM 返回 `final_answer`（正常终止，产出 root_cause_layer + 诊断结论 + 建议）
2. 达到 `max_rounds`（强制收敛：用已有 observations 做单次诊断兜底）
3. 达到 `total_timeout`（同上）
4. 工具连续失败 ≥ 2 次（降级为单次诊断，避免空转）

**降级策略**（保证闭环鲁棒性）：
- 任何异常（LLM 解析失败 / 超时 / 工具连续失败）→ **降级为现有单次诊断** `diagnosis_service.diagnose_alert`，告警闭环不中断
- 降级时记录 `degrade_reason` 到审计，用于后续优化 prompt/工具
- 兜底原则：**宁可用单次诊断结果，也不能让告警卡在循环里**

**安全约束**：
- 诊断循环只允许调 `readonly` 工具（`execute_tool` 已内置存在性 + 参数 schema 校验）
- `operate` 类工具（重启/删 Pod/apply yaml）**不暴露给诊断循环**，只在 `decision_engine` 判定后由代码触发
- 每轮工具调用全量审计（tool 名 + args + 输出摘要 + 耗时）

### 7.2 统一工具注册表

**现状**：`tool_registry.py` 已实现注册表 + 参数校验 + 统一执行入口（P0-1/P0-2 雏形），已注册 6 个只读工具。

**工具清单**（现状 + 待补）：

| 工具名 | 域 | 权限 | 数据源 | 状态 |
|---|---|---|---|---|
| exec_in_pod | application | readonly | executor ExecInPod | ✅ 已注册 |
| describe_pod | application | readonly | executor describe | ✅ 已注册 |
| search_logs | application | readonly | ES 日志 | ✅ 已注册 |
| get_pod_metrics | application | readonly | Prometheus | ✅ 已注册 |
| query_db_health | database | readonly | db_collector（只读账号） | ✅ 已注册 |
| rag_search | all | readonly | 知识库 RAG | ✅ 已注册 |
| get_pod_logs | application | readonly | executor GetPodLogs gRPC（CLUSTER-MGMT 需求二） | ⏳ 待接入 |
| get_pod_yaml | application | readonly | executor GetPodYaml gRPC（CLUSTER-MGMT 需求四） | ⏳ 待接入 |

**operate 类工具**（**不注册进 LLM 工具清单**，仅决策引擎内部触发，供参考）：

| 工具名 | 权限 | 触发方 |
|---|---|---|
| restart_pod | operate | decision_engine（A 层自愈） |
| delete_pod | operate | decision_engine（A 层自愈，高危） |
| update_resource | operate | decision_engine（A 层自愈） |
| apply_pod_yaml | operate | 人工（CLUSTER-MGMT 需求四，operator 权限） |
| repair_image_pull | operate | alert_auto_service（镜像短路规则） |

**工具定义 schema**（已实现，保持不变）：

```
{
  "name": str,            # 小写下划线，LLM 调用名
  "label": str,           # 展示名（审计/前端）
  "description": str,     # 给 LLM 的用途说明
  "args_schema": {        # 参数校验 schema（P0-2 护栏）
     "param": {"type": "string|int|number|bool", "required": bool, "description": str, "default": any}
  },
  "permission": "readonly" | "operate",
  "domain": "application" | "database" | "knowledge" | "cluster" | "all",
  "handler": async callable(args, context) -> {"success", "output", "error"}
}
```

**新增工具 = 注册一条定义**：写 handler + `_register({...})`，编排代码（agent 循环 / 决策引擎）零改动。

### 7.3 任务记忆

**目标**：同资源（cluster + namespace + deployment）历次诊断/自愈/审批记录注入当前诊断上下文，避免「每次从零诊断」、支持跨告警复用历史方案。

**数据结构**（两方案）：

- **方案 A（独立表 `task_memory`）**：

```
task_memory:
  id
  resource_key       # cluster_id + namespace + deployment_name 归一化
  fault_type
  root_cause_layer   # A/B/C/D
  diagnosis_summary  # AI 诊断结论摘要
  action_taken       # self_heal / approval / publish / dba / ticket
  outcome            # resolved / repeated / escalated
  created_at
```

- **方案 B（轻量，不建表）**：按 resource_key 实时聚合现有表——alerts（历次告警 + fault_type + status）、repair_plans（自愈方案）、approvals（审批结果）、knowledge_entries（沉淀方案）。

**推荐**：先方案 B（零迁移，读现有表）；数据量上来或需要 AI 摘要时再切方案 A。核心是**注入策略**，不是存储。

**注入策略**：
- 诊断前按 resource_key 拉最近 N=5 条历史，拼成结构化摘要注入 system prompt：

```
[历史记录] 该服务(deployment=xxx)近 7 天：
- 2026-08-26 OOMKilled → 根因层 A → 自愈(重启+调limit) → 已恢复
- 2026-08-25 CrashLoopBackOff → 根因层 B → 转发布 → 已修复
```

- 注入原则：只注入**结论性摘要**（根因层 + 处理方式 + 结果），不注入原始日志/指标，控制 token 消耗
- 重复故障识别：历史同 fault_type 已自愈失败 ≥ 1 次 → prompt 提示「该故障曾反复，优先考虑根因是否在上层（B/C/D）」，呼应循环抑制
- 知识库命中（rag_search）作为任务记忆的一部分，优先注入
- **降级**：历史查询失败/超时 → 跳过记忆注入，正常单次诊断（不阻塞闭环）

---

## 细化结果对照（原「明日待细化项」）

- [x] 阶段 7 的 ReAct 循环细节（最大步数、超时、降级策略）→ 见 7.1
- [x] 统一工具注册表的工具清单与 schema → 见 7.2
- [x] 任务记忆的数据结构与注入策略 → 见 7.3
- [x] 集群管理 Pod 状态的字段采集细节（restart_count/ready）→ 见 `CLUSTER-MGMT-REQUIREMENTS.md` 需求一
