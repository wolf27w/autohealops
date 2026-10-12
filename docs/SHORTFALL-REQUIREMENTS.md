# 短板补齐开发需求（对标 KubeDoor）

> 来源：2026-10-12 与老板对比 GitHub CassInfra/KubeDoor（2.1）后，梳理出 API-OPS 的相对短板。
> 用途：明确「补短板」要开发的项、优先级、落点、验收与工时估算。
> 状态：已纳入 `REQUIREMENTS-BACKLOG.md`（2026-10-12 节）；⏸ **暂缓——老板 2026-10-12：先不排期，后面再做**。
> 粒度：P0~P3 分四批，工时按「人日」估算（量级参考，非承诺）。

---

## 一、现状基线（已有，不重复开发）

对比前先声明 —— 下面这些**平台已经具备**，不在需求清单里：

- K8s 资源 CRUD / YAML 编辑：`api/resources.py`（objects/workloads 的 yaml get/put/delete/apply）
- Pod 列表/详情/事件/日志/metrics：`api/resources.py`
- Pod exec + 交互式终端：`api/terminal.py`（WebSocket ↔ gRPC `ExecPodStream`）
- 容器文件浏览：`resources.py` files/file
- scale / rollout / rollout history / recreate / probe：`resources.py` + executor
- 工作负载 / 命名空间管理
- 定时/周期任务：`api/custom_tasks.py`（CRUD + toggle + run-now + logs）
- AI 诊断助手会话：`pages/AgentChat.tsx` + `services/agent_service.py`
- 告警 / 工单 / 审批 / 审计（哈希链）/ 知识库 RAG / DB 诊断 / 中间件监控
- Prometheus 查询：executor `QueryPrometheus`
- 仪表盘实时刷新（SSE stream + 缓存代次）

---

## 二、需求清单

### P0 — 填补核心护城河缺口（KubeDoor 主打、我方完全空白）

#### R1 高峰期资源治理 + 准入控制
- **目标**：按业务高峰时段采集真实资源用量，通过 MutatingWebhook 准入把 Deployment 的副本数 / requests / limits / JVM 参数对齐到管控值；调度更准、碎片更少。
- **现状**：`admission` / `MutatingWebhook` / `准入` 全库 **0 命中**，完全没有。
- **实现要点**：
  1. executor 新增高峰期采集（P80 CPU/内存、P95 堆/G1，按可配置高峰时段）
  2. executor 落地 MutatingWebhook（Go，管控命名空间），`fail-closed` + `kubedoor-ignore` 同类忽略标签 + 应急删除
  3. 管控表（需求值/限制值/副本数/JVM）+ 批量扩缩容/重启/「配置」/「新增资源」
  4. 新服务策略（免确认开关）、临时扩容豁免窗口
- **落点**：`k8s-executor/internal/`（新 admission 模块）；`api-server/app/api/`（新 resource-governance 路由）；`frontend/src/pages/ResourceGovernance.tsx`
- **验收**：开启准入后，发布一个未加固的 Deployment，副本/requests/limits 被改写为管控值；关闭时正常放行；应急删除 webhook 配置可恢复。
- **工时**：~9d（采集 2 + 准入 3 + 管控台 3 + 策略/豁免 1）

#### R2 JVM 深度诊断 + 参数管控
- **目标**：Java 微服务的启动参数管理与一键诊断。
- **现状**：`jstack` / `jfr` 全库 **0 命中**；只有通用 exec，无 JVM 专项。
- **实现要点**：
  1. 一键诊断：Dump / Jstack / JFR / JVM（executor exec 进 Pod 执行，产物可上传对象存储）
  2. JVM 启动参数采集（`-Xms/-Xmx/-Xss/-XX:MaxMetaspaceSize`）与接管（配合 R1 准入改写）
  3. 堆内存 / G1 Eden P95 指标采集 + JVM 告警规则
- **落点**：executor 新 jvm 模块；`api/resources.py` 加诊断接口；前端 Deployment/Pod 页加诊断入口
- **验收**：对一个 Java Pod 执行 jstack 能返回线程栈并推送通知；参数修改后下次发布生效。
- **工时**：~7d（诊断执行 2 + 前端 2 + 参数管控 2 + 指标告警 1）

---

### P1 — 产品力明显提升

#### R3 镜像更新（列仓库标签一键更新）
- **现状**：只有 `RepairImagePull`（补 imagePullSecrets），**无镜像更新**。
- **要点**：对接 ACR / SWR / Harbor 列最近标签；一键更新；进度推送 IM；只读账号可白名单授权。
- **落点**：executor + `api/resources.py`；`frontend/src/pages/`（Deployment 页入口）
- **工时**：~5d

#### R4 节点管理 + 节点均衡
- **现状**：`节点均衡` / `load_balance` **0 命中**；仅 `decision_engine.py` 识别 cordon/drain 风险词，**无节点管理功能**。
- **要点**：节点列表（CPU/内存/磁盘/Pod 用量与可分配量）、批量 cordon/uncordon、按 CPU 负载生成迁移计划（隔离式分批）；调度到指定节点。
- **落点**：executor + 新 `frontend/src/pages/NodeManagement.tsx`
- **工时**：~6d（列表/用量 2 + cordon 批量 1 + 均衡算法 3）

#### R5 实时日志跟随 + 日志增强
- **现状**：`logs.py` / `resources.py` 无 `follow` / `stream`，**只有历史查询**。
- **要点**：实时跟随（WebSocket 流式）、关键字筛选、ERROR/WARN/INFO 多色、ANSI 颜色保留、重启前日志、Deployment 级聚合日志、完整日志 gzip 下载。
- **落点**：executor 流式日志接口 + `api/` WebSocket + 前端日志查看器组件
- **工时**：~3d

#### R6 实时状态推送（WebSocket）
- **现状**：Deployment 状态靠轮询；仪表盘有 SSE 但无资源实时推送。
- **要点**：WebSocket 推送就绪/期望副本、滚动更新状态、Pod 明细增删。
- **落点**：executor watch → api-server WS → 前端
- **工时**：~3d

#### R7 统一监控大屏增强
- **现状**：有 `/screen` 但与仪表盘同组件，缺多集群统一总览增强。
- **要点**：全部集群 / 单集群切换、集群/节点/工作负载/Pod/PV 计数、风险节点徽章、TopN 使用率、Top10 异常 Pod/事件、告警趋势。
- **落点**：`frontend/src/pages/`（大屏）；可选内嵌 Grafana
- **工时**：~3d

#### R8 Pod 隔离
- **现状**：`isolate` **0 命中**。
- **要点**：改 `app` 标签（`<原值>-ALERT`）脱离 Service 流量、保留现场，可同时临时扩容 1 个。
- **落点**：executor + `api/resources.py` + 前端
- **工时**：~2d

---

### P2 — 工程/生态

#### R9 MCP 标准接口
- **现状**：`mcp` **0 命中**。
- **要点**：把平台能力以 MCP（Streamable HTTP / SSE）开放给 Cursor / Claude Code 等外部客户端；复用平台账号与权限；写操作走审批。
- **落点**：`api-server` 新 MCP server 模块
- **工时**：~4d

#### R10 AI 助手增强（对标 KubeDoor）
- **现状**：AgentChat 已有会话基础；缺全局记忆、自由指标查询（MetricsQL）、会话断线续传、统一受控工具集。
- **工时**：~4d

#### R11 告警屏蔽收尾（Alertmanager 风格）
- **现状**：`docs/ALERT-SUPPRESSION.md` 为"方案确认中，待开发"；已有告警合并（grouping）。
- **要点**：匹配条件（`=`/`!=`/`=~`/`!~`）+ 命中预览 + 到期失效 + 生效/待生效/过期/解除；只拦通知不丢数据。
- **工时**：~3d

---

### P3 — 架构级（投入大）

#### R12 agent 主动外连接入模式
- **现状**：api-server 集中连集群；KubeDoor 是 agent 主动 WS 外连、业务集群不开入站端口。
- **要点**：executor 反向连接模式（或双模式），业务集群免开入站端口。**架构改动大，需评估。**
- **工时**：~5d+（含安全评估）

#### R13 一键部署脚本
- **现状**：3 服务 + MySQL + Redis，手工/本地态。
- **要点**：`install.sh` + 配置文件，脚本化部署；可选内嵌时序库。
- **工时**：~3d

---

## 三、批次建议

| 批次 | 内容 | 合计工时 |
|---|---|---|
| **P0** | R1 资源治理+准入、R2 JVM | ~16d |
| **P1** | R3 镜像更新、R4 节点管理与均衡、R5 实时日志、R6 实时状态、R7 大屏、R8 Pod 隔离 | ~22d |
| **P2** | R9 MCP、R10 AI 助手增强、R11 告警屏蔽 | ~11d |
| **P3** | R12 外连模式、R13 一键部署 | ~8d+ |

**默认建议**：先做 **P0**（R1+R2）。理由：这两块是 KubeDoor 的核心差异化能力、我方完全空白，且直接命中你们的 Java 微服务场景与资源成本治理诉求；R1 与 R2 的 JVM 参数管控天然联动（准入一起改写）。

---

## 四、决策点（需老板确认）

1. **做起止范围**：只补 P0？还是 P0+P1 一起排？
2. **R1 准入控制**风险口径：是否接受 KubeDoor 那种 `fail-closed`（webhook 挂了业务发布会被拒），还是改成 `fail-open`（挂了放行）？—— 生产影响面大，需拍板。
3. **R2 诊断产物**：jstack/jfr/dump 文件是否上传对象存储？用哪个 OSS？
4. **R9 MCP**：对外开放口是否要鉴权分级（只读/读写）？
5. **R12 外连模式**：是否真要做（架构级，且现有集中连模式已能用）？
