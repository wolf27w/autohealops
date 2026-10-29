# AutoHealOps 平台 —— 剩余需求与开发顺序（Roadmap）

> 整理：小吴　更新：2026-10-28　依据：代码核实（10-28 完成 P2 agent 告警下沉 + 值班排班 UI）
> 用途：一张表看清「还剩什么没做 + 建议先做哪个」。状态一律以**代码为准**，`REQUIREMENTS-BACKLOG.md` 为历史流水。

**图例**：🟢 可立即开发（无外部依赖）｜🟡 需外部前置/部署决策｜🔴 待老板拍板口径｜⚪ 未排期建议项

---

## 〇、剩余需求速览（2026-10-16 更新，最新口径）

> 2026-10-16 老板要求「不影响集群的先开发完」，已完成：C1 KPI、集群接入统一抽象、凭据到期治理、环境维度、M2 批量接入、M4 日志保留、M7(核实暂缓)。以下为**当前仍未做**的。

> **2026-10-21 新增方向**：**Agent-only 纳管**（平台进集群自接入 + 其它集群只下发 agent + 告警检测下沉），
> 详细需求/现状/分期/验收见 **`docs/AGENT-ONLY-ARCHITECTURE.md`**；本节 A2/A4/A3 合并进该需求。

> **2026-10-19 复核（代码为准）剩余清单**：可开发 = A2 Agent 托管更新、A2 Agent 托管更新、M1 平台自接入本集群、D1 凭据到期主动通知、值班排班 UI、A4 开关下发（并入 A2）；部署前置 = A6.1/A6.2/B3；待拍板 = B1/B2/B3（其中 B2 准入代码已不存在，建议销项）。本次已完成：N12/N13；已核实完成、原先误列为待办的：C1、M2 多 context、M4 分区机制、告警协作、审批增强、知识库导入导出、报表定时推送、AI 反馈闭环、LDAP 切换。

### A. 🟡 影响集群（老板明确先不做，等部署决策）

| # | 需求 | 缺口 | 工时 |
|---|---|---|---|
| A1 | **M3 Agent 接入闭环**：令牌签发/吊销 + 一键安装清单 + 首连自动登记 + 心跳/状态 + 前端向导 | ✅ **已完成（2026-10-16，测试环境真连通过）** | — |
| A2 | **Agent 托管更新**：平台升级时批量滚动升级各集群 agent（版本/暂停/回退） | 无 | ~1d |
| A3 | **M1 集群内自托管 → 自动接入本集群**：executor in-cluster config + 启动 upsert + 「本集群」标记 | 无 | ~1.5–2d |
| A4 | **M2 下发 agent 开关的实际下发动作**（开关默认关） | 只做了开关字段/批量接入，下发未做 | 并入 A1 |
| A5 | ~~JMX Exporter 集群侧安装~~ | ✅ **已取消（2026-10-16 老板：JMX 属监控，暂不装，用现有 Prometheus 展示）** | — |
| A6 | **B3 平台自身生产部署** | M10/M11 已备，未上生产 | 部署类 |
| A6.1 | **agent 镜像构建/推送**：`linux/amd64` 构建 `apiops-agent` → 推到目标集群能拉的仓库（填 `AGENT_IMAGE`）——否则 `install.yaml` 起的 pod 会 ImagePullBackOff | 无镜像 | 部署前置 |
| A6.2 | **平台 agent 网关（9095）对外可达**：填 `AGENT_GATEWAY_PUBLIC_ADDR`；跨集群需 NodePort/LB 或 nginx/SamWaf TCP 四层转发（仅 ClusterIP 只集群内可达） | 仅本地 127.0.0.1 | 部署前置 |

### B. 🔴 待老板拍板（非开发）

| # | 事项 | 现状 |
|---|---|---|
| B1 | 「诊断失败」是否单独记录 | 现映射为 pending 未转人工 |
| B2 | R1 准入最终风险口径 | 准入 webhook 已删，可能作废。**2026-10-19 核实**：`k8s-executor/internal/` 已无 `admission` 模块 → 服务本体不存在，属既成作废，待老板销项确认 |
| B3 | 专项 E：砍自愈策略死代码（`SelfHealPolicy`） | 破坏性删除，等拍板 |

### C. ⚪ 未排期建议项（2026-10-19 复核：只剩 1 项）

~~告警协作（认领/备注）｜审批增强（批量/加签/转办）｜知识库治理（导入导出）｜报表导出+定时推送｜AI 反馈闭环｜LDAP↔本地登录切换~~ → ✅ **均已实现**（前后端入口核实在位：`AlertList.assignAlert`、`ApprovalList` 加签/转办、`KnowledgeBase` 导入导出、`Reports` 定时推送、`FaultDiagnosis` 反馈、`PlatformSettings` LDAP）。

~~唯一缺口：值班排班/换班/日历 UI~~ → ✅ **已完成（2026-10-28，`36ab1c7`）**：新增 `frontend/src/pages/OnDuty.tsx`（排班规则 CRUD / 值班日历月视图 / 换班代班 + 节假日），路由 `/on-duty`，菜单「告警与工单 → 值班排班」。C 组已清空。

### D. 🟢 技术债/小尾巴（不影响集群，可插空做）

| # | 事项 | 工时 |
|---|---|---|
| D1 | 凭据到期**主动通知**（30/7/1 天预警站内/IM；已做列表红标） | ~0.3d |
| D2 | M4 **大表时间分区**（`pod_metrics` 618 万行等）——需运维窗口手动 DDL，勿自动跑 | 机制已具备：`resource_service.maintain_snapshot_partitions` 表清单含 `pod_metrics`/`resource_snapshots`，日志每日维护；仅需核对存量表分区是否都已建 |
| D3 | 仪表盘其余打磨 / 大屏审美复核（`monitor-bigscreen.html` 等验收） | 视反馈 |

### 🆕 Agent-only 纳管（老板 2026-10-21 方向，详见 `docs/AGENT-ONLY-ARCHITECTURE.md`）

| # | 需求 | 现状（代码核实 2026-10-21） | 工时 |
|---|---|---|---|
| **P2** | **告警检测下沉**：watcher 在 agent 内跑，事件经隧道上报 | ✅ **完成（2026-10-28，`50eba3c`）**：`AlertSink` 抽象（dbSink/tunnelSink）+ agent 收平台下发的告警策略后自起 Pod/Node watcher，事件经隧道上报，平台统一去重落库并触发自动处理 | — |
| **P1** | **全方法反向代理** | ✅ **完成（2026-10-28，`009d680`）**：agent 进程内起真实 ExecutorService + raw codec 转发，33 个 unary RPC 全部可用（流式 3 个待做）；平台侧 stub 代理让 30+ 调用点零改动 | — |
| **P3** | **in-cluster 自接入 + agent 版本管理/灰度/回退** | ❌ 均未实现（`clusters.source` 字段已预留） | 2.5–3.5d |
| **P4** | 隧道 TLS/mTLS + agent 网关 9095 对外 | ⚠️ 现为明文 gRPC，仅本机可用 | 1d |

**建议顺序**：~~P2~~ → ~~P1~~ → P3 → P4（P2/P1 已完成 2026-10-28；下次做 P3 in-cluster 自接入 + agent 版本管理/灰度/回退）。
**本方向合并原**：A2（agent 托管更新）、A4（下发开关动作）、A3/M1（集群内自接入）。

### G. ✅ 已完成（老板 2026-10-19 登记，当日开发完成）

> 详见 **`docs/NEW-REQUIREMENTS-2026-10-19.md`**（现状核实 + 根因 + 方案 + 验收 + 工时 + 实现记录）

| # | 需求 | 完成情况 | 工时 |
|---|---|---|---|
| **N12** | 🐞 **监控刷新不闪，自动/实时** | ✅ `patchHTML` 节点级 diff + `chartPool` 实例复用（不 dispose）+ 总览 5s / 监控 3s / 趋势 30s；selftest 验证 DOM 与图表均复用 | ~1.5–2d |
| **N13** | 🏗️ **监控到容器级别** | ✅ 总览跨集群「容器 CPU/内存 Top 10」+ 服务详情「Pod × 容器明细」（状态/limits/重启/终止原因）；容器指标统一走 Prometheus | ~2–2.5d |
| **N14** | 🕘 **容器历史监控**（单容器看 3 天，最多 7 天） | ✅ 趋势区范围切换 1h/6h/24h/3d/7d + 容器选择；容器表「历史」按钮一键看 3 天；后端 range 校验 ≤7 天 | ~0.5d |

**已定口径（2026-10-19）**：N12 刷新 **3s/5s**（SSE 推送 + 5s 轮询）；N13 **容器指标统一走 Prometheus**（不扩自采集 `pod_metrics`，无数据源集群降级提示）；容器 Top 默认上大屏首页。

建议：两条同批做（N12 先改渲染骨架 → N13 直接复用它做容器下钻），合计 ~3.5–4.5d。

---

## 一、总体状态

- **R1–R13（对标 KubeDoor 短板补齐）主体已全部落地**（详见第二节"已完成"速览）。
- 真正未完成的只有**功能缺口 2 项**（监控前端、准入集群内落地）+ **2 项外部前置**（JMX Exporter、准入拓扑）。
- 另有 **M1–M12 运维/平台化建议项**（已采纳、未排期）。
- 若干口径项**卡在老板拍板**，未定不影响开发，定了一并收口。

### 已完成速览（账本滞后未更新，此为准）

| 项 | 提交 | 说明 |
|---|---|---|
| R1 资源治理 + 准入下发 | `c8acef8` `b3c1d78` | 管控表/API/高峰采集/前端/准入模块 + 集群下发 install/uninstall/status；命名空间 LimitRange+可选 ResourceQuota |
| R2 JVM 深度诊断 | `0c43d5f` | 9 动作 + jattach 后端（JRE 免 JDK）；Deployment 参数采集 |
| R2.3 JVM 堆/G1 指标（平台侧） | `377cd1b` | `/jvm/.../jvm-metrics` + 前端展示 + `docs/jvm-exporter/` 物料 |
| R3 镜像更新 | `ff6e11d` | 列仓库标签（Registry v2）+ 一键改镜像 |
| R4 节点管理 + 均衡 + drain | `1912dc1` `0408a5a` | 列表/容量使用率/批量 cordon/均衡计划 + drain（PDB 感知 + 预览 + 强确认） |
| R5 实时日志跟随 + gzip 下载 | `ed17fbe` `63f36ea` | executor 日志流(:9099) + WS + 前端；Deployment 聚合日志(`getWorkloadLogs`)已有 |
| R6 实时状态推送 | `e0bced3` | executor `/watch` + WS + ServiceDetail 实时刷新 |
| R7 大屏 | 多个提交 | 已重做为自研 `public/monitor-bigscreen.html`（iframe，接真实 `/stats`、`/clusters`、`/alerts`、`/monitoring` 数据） |
| R8 Pod 隔离 | `resources.py` | 改标签脱流量 |
| R9 MCP 标准接口 | `api/mcp.py` | Streamable HTTP + JSON-RPC 2.0 |
| R10 AI 助手增强 | `86e3010` | 自由 PromQL `query_metrics` + 断线续传 + 知识库记忆 |
| R11 告警屏蔽 | `alert_silences.py` | Alertmanager 风格 + 预览 |
| R12 agent 主动外连 | `bf5d219` | 单 Deployment + gRPC 反向隧道 |
| R13 一键部署 | `a48b9a1` | `install.sh`（本地/Docker 双模式） |
| 项目级审批 | 迁移 `t3u4v5w6x7y8` | `projects.approval_flow_id` + 项目优先流程 |
| 外部工单接入 | `external_access.py` | token + `/tickets` 查询/创建 |
| 邮件/站内消息渠道 | `notification_service.py` | smtplib + 站内消息中心 |

---

## 二、剩余需求清单

### A. 🟢 可立即开发（后端已就绪 / 无外部依赖）

| # | 需求 | 现状 | 落点 | 工时 |
|---|---|---|---|---|
| **A1** | **Prometheus 监控前端落地**：仪表盘「告警与总览｜监控」Tab + 监控下钻页（节点/工作负载/容器 × CPU/内存/网络/磁盘） | ✅ 完成（`96f5fd4`）——新增 `Monitor.tsx`（实时 Top + range 趋势），`Dashboard.tsx` 加 Tabs | — |
| **A2** | **后台「监控数据源」配置** | ✅ 完成（`737746b`）——新表 `monitor_sources`（按集群覆盖）> 全局 `prometheus_url` > 自动发现；直连不通自动回退；系统设置→可观测性加「按集群覆盖」表格 | — |
| **A3** | 大屏（R7）审美复核 / 细节打磨 | 自研 HTML 大屏已接真实数据，**等老板验收** | `public/monitor-bigscreen.html` | 视反馈 |
| **A4** | R4 尾巴：**调度 Pod 到指定节点** | ✅ 完成（`7179e1a`）——pod template 加 nodeSelector[kubernetes.io/hostname]（保留其他选择器）+ preview 预览；ServiceDetail「操作」菜单入口 | — |

### B. 🟡 需外部前置 / 部署决策

| # | 需求 | 缺口 | 前置条件 |
|---|---|---|---|
| **B1** | **R1 准入 server 在「被管集群内」落地** | 平台能下发 Service+MutatingWebhookConfiguration；但准入服务本体（executor `internal/admission`）需跑在集群内 | 部署拓扑决策：随集群部署 executor/admission Sidecar 或独立 Deployment |
| **B2** | ~~R2.3 集群侧 JMX Exporter 安装~~ | ✅ 已取消（老板 2026-10-16）：JVM 指标用现有 Prometheus 容器指标展示，不装 JMX Exporter | — |
| **B3** | **平台自身生产部署** | 当前仅本地/测试验证 | 关联 M10（发布/回滚）、M11（备份/DR）|

### C. 🔴 待老板拍板口径（非开发，定了一并收口）

| # | 事项 | 状态 |
|---|---|---|
| **C1** | 仪表盘「自动处理」KPI 口径 | ✅ **2026-10-16 已定稿并落地**（`231913a`：`stats.py` 已输出 `auto_processed_with_action` / `action_alert_count`） |
| **C2** | 「诊断失败」是否单独记录 | ❓ 现映射为 `pending` 未转人工 |
| **C3** | R1 准入最终风险口径 | ❓ 现 `fail-open`（准入 webhook 已删，可能一并作废） |

#### C1 定稿：仪表盘「自动处理/成功率」口径（老板 2026-10-16，依小吴建议）

**问题**：仪表盘有两处都叫「自动处理」，但单位不同 → 顶部 KPI 卡是**告警条数**、同页卡片四块是**动作数**（`stats.py:629/640`），容易误读。

**定稿口径（三条）**：

1. **顶部 KPI「自动处理（24 小时）」= 告警条数**口径（与成功率/MTTR 统一，保持现状不变）
   - `= |{近24h status='self_healed' 的告警 id} ∪ {近24h 内有【success】执行动作的告警 id}|`（按告警去重）
   - 保持 `now - 24h` **滑动窗口**
2. **卡片改名 → 「自动处理动作（最近 24 小时）」**，明确单位=**动作数**
   - 四块 `self_heal / restart / scale / rollback` 保持（仅 `status=success`）；`action_failed` 单列、不计入
   - 卡片补一行汇总：**「涉及告警 N 条」** = 触发过动作的告警去重数
3. **`self_healed` 但无执行动作的告警：仍计入** KPI「自动处理」，但卡片**单列**一项「其中：平台标记自愈（无动作）N」
   - 目的：区分「平台动作的功劳」 vs 「故障自己恢复」，避免口径被误解

**落点**：
- `api-server/app/api/stats.py`：`kpi` 增 `auto_processed_with_action` / `auto_healed_without_action`；action 卡片数据增 `alert_count`（触发过动作的告警去重数）与 `healed_without_action`
- `frontend/src/pages/Dashboard.tsx`：KPI 卡保持「自动处理(24小时)」；卡片标题改「自动处理动作（最近 24 小时）」+ 加汇总行

**验收**：`./scripts/ci_check.sh` 全绿 + `/stats/dashboard` 真接口对账（KPI = 有动作告警 ∪ 自愈告警；卡片动作数 ≥ 有动作告警数；with_action + without_action = auto_processed_24h）

**工时**：~0.5d（口径不动数据，纯统计输出 + 文案）

### D. ⚪ 未排期建议项（M1–M12，运维/平台化）

> 来源：`docs/PENDING-REQUIREMENTS-DETAIL.md` 第三节。

| # | 需求 | 触发条件 | 工时 |
|---|---|---|---|
| M1 | 平台健康（探针 + 右上角展示） | 平台上 k8s | ✅ 已具备（N4 探针 + 仪表盘右上角「平台健康」） |
| M2 | executor 高可用（探针+自动重启+掉线告警；长期拆 watcher/执行） | 线上可用性要求提高 | ✅ 完成（`08961b0`）：探针/重启已有 + 掉线探测告警（平台侧） |
| M3 | 告警风暴降噪（根因聚合 + 抑制窗口 + 限流/摘要） | 出现告警风暴 | ✅ 已具备（N8 风暴抑制+30min 限流+静默窗口；R11 alert_silences；告警同故障同服务合并；未解决≥10 推汇总） |
| M4 | 数据增长治理（统一保留策略 + 大表时间分区 + audit 归档不删） | 表数据到千万级 | 2d |
| M5 | 前端首屏/打包优化（React.lazy + 动态 import + vendor 拆包） | 首屏慢被投诉 | ✅ 完成（`c9371ca`）：入口包 3.6MB→36KB + vendor 分包 + 路由 lazy |
| M6 | API 契约（导出 openapi.json + 前端自动生成类型） | 接口变动频繁 | ✅ 完成（`5cf0011`）：导出 docs/openapi.json + CI 漂移检查（第7步）；前端类型生成可选 |
| M7 | 时区统一（DB 存 UTC + 展示转时区） | ⏸ 暂缓（**2026-10-16 核实：无当前 bug**） | 现状：MySQL `NOW()`=CST、app `datetime.now()`=CST、前端直接显示，**全链路一致 Asia/Shanghai**；改 UTC 属「多时区支持」重构，单时区场景无收益，风险大 → 暂缓 |
| M8 | 权限越权测试入 CI（低权限遍历所有 API 断言 403） | 安全审计前 | ✅ 完成（`7632aae`）：tests/test_permissions.py（309 路由结构覆盖 + viewer 越权 403）入 ci |
| M9 | 配置热更新（统一配置读取层 + 「已生效」提示） | 改配置要重启被烦到 | ✅ 基本具备：设置存 DB 逐次读取（get_setting 无进程缓存），改完即时生效；仅 .env 级（密钥/端口）需重启 |
| M10 | 平台自身发布/回滚（容器化 + 滚动发布 + 保留上一版镜像） | 平台正式上线 | ✅ 完成（`58bb89a`） |
| M11 | 备份 / DR（MySQL 定时备份 + 异地；确认 Redis 持久化） | 上线前 | ✅ 完成（`2f9b762`） |
| M12 | AI 用量异常告警（单日成本突增 / 异常调用量） | 接入付费模型后 | ✅ 完成（`3a88a59`）：ai_usage_service（今日 vs N日基线，超倍数/绝对阈值→通知）+ GET /stats/ai-usage |

---

### E. 🟢 智能诊断证据完备（2026-10-15 新增线）

| # | 需求 | 状态 | 落点 / 缺口 | 工时 |
|---|---|---|---|---|
| **E1** | **JVM 结果全量接入应用诊断** | ✅ 完成（`6583f63`） | 新增诊断工具 `jvm_diag`（9 动作）；快速诊断预采集探测到 Java 进程自动追加 JVM 证据（vm_flags/heap_info/gc_class_histo/thread_dump）；真机冒烟通过 | — |
| **E2** | **诊断日志口径对齐** | ✅ 完成（`6583f63`） | `search_logs` 改用容器 stdout + `--previous`（上次崩溃实例），不再走 ES（此前重构遗漏） | — |
| **E3** | **主动诊断入口** | ✅ 完成（`85f13ff`） | 巡检服务：持续高水位/频繁重启但无告警 → 自动 AI 全面诊断（source=proactive，不建告警）；后台每 30min + AgentChat「立即巡检」；真机冒烟通过 | — |
| **E4** | **按故障类型诊断模板** | ✅ 完成（`d529995`） | 预采集按关键词命中模板：探针失败（get_probe_config+get_deployment）/内存OOM/资源高水位/崩溃重启；新增 `get_probe_config` 工具；证据加「故障类型判定」段 | — |

---

### F. 🟢 集群接入三方式（2026-10-16 老板新增线）

> 详见 **`docs/CLUSTER-ONBOARDING.md`**（现状核实 + 设计 + 落点 + 排期 + 验收）

| # | 需求 | 现状 | 缺口 | 工时 |
|---|---|---|---|---|
| **M1** | 平台部署在集群内 → **自动接入本集群** | ❌ 无（`k8s/*.yaml` 有部署清单） | executor in-cluster config + 启动 upsert + 「本集群」标记 | ~1.5–2d |
| **M2** | 集群外 → **config 文件接入**（可选**自动下发 agent**，**默认关**） | 🟡 仅单份 kubeconfig | 多 context 批量接入 + 下发开关 + agent 托管更新 + `install.sh` 启动导入 | ~1.5d |
| **M3** | **Agent 接入**（KubeSphere 风格：令牌+一键清单 → 主动外连） | 🟡 R12 隧道有、接入闭环无 | 令牌签发/吊销 + `install.yaml` + 首连自动登记 + 心跳/状态 + 向导 | ~3–4d |
| — | 统一抽象（三方式共用） | ❌ | `clusters.source` + 幂等键（集群 UID）+ 列表来源徐标 | ~0.5d |

建议顺序：**0 统一抽象 → 1 M2 增强 → 2 M3 Agent → 3 M1 自接入**（先小后大，复用 R12）。

**拓扑（老板 2026-10-16 定）**：**单平台多集群** —— 平台只跑一份，告警/AI/工单一套，靠 `cluster_id + env_tag` 区分环境。反模式：每集群装一份（数据割裂）。

**本线附带落地项**

| 项 | 说明 | 工时 |
|---|---|---|
| 环境维度展示/筛选 | 告警列表/工单/AI 诊断会话 加 env 列 + 筛选；仪表盘按环境聚合；通知模板带「环境/集群」 | ~1.8d |
| 凭据到期治理 | 解析 cert/token 到期 → 列表显示有效期 + 30/7/1 天预警 + **一键替换 kubeconfig**（不丢历史）；能上 agent 则免维护 | ~1d |

---

## 三、建议开发顺序（批次）

> 原则：**先快赢（无依赖、后端已备）→ 再上线前置 → 再治理质量 → 最后需外部前置/决策项**。

| 批次 | 内容 | 合计 | 说明 |
|---|---|---|---|
| **第 1 批（快赢）** | A1 监控 Tab/下钻 + A2 监控数据源配置 | ~3–4d | 后端全就绪，纯前端接线，能立刻见效；A1 完成后大屏/仪表盘监控闭环 |
| **第 2 批（上线前置）** | M11 备份/DR → M10 发布/回滚 → M2 executor 高可用 → M1 平台健康 | ~6d | 正式上生产前必须先有可用性 + 备份 + 回滚 |
| **第 3 批（治理/质量）** | M3 告警风暴降噪 → M8 越权测试入 CI → M4 数据治理 → M9 配置热更新 → M7 时区 → M6 API 契约 → M5 前端优化 | ~10d | 稳定性/安全/可维护性，按风险排 |
| **第 4 批（需外部前置/决策）** | B1 准入集群内落地 → B2 JMX Exporter 落地 →（C1/C2/C3 口径确认后收口） | ~3–4d | 依赖部署拓扑与集群侧配合 |
| **第 5 批（增强）** | A4 调度到指定节点 → A3 大屏打磨 → M12 AI 用量告警 | ~3d | 锦上添花 |

### 依赖关系

```
A1/A2（纯前端接线）──► A3 大屏打磨（共用监控数据）
B1 准入集群内落地 ──► C3 准入风险口径（落地后定 fail-open/strict）
B2 JMX Exporter ──► R2.3 数据可用（平台侧已完成）
M10 发布/回滚 ─┐
M11 备份/DR  ─┴─► B3 平台生产部署
M2 executor HA ─► B3
```

---

## 四、风险 / 备注

- **exec 链路与冷启动偏慢**：单接口 40–60s、api-server 冷启动 3–4min（环境冷 + HDD），开发验证时预留时间。
- **重启服务只杀监听进程**（勿 `lsof -ti tcp:8080 | xargs kill`，会误杀 vite）——见 `TOOLS.md`。
- 各批次完成 → `./scripts/ci_check.sh` 全绿 + 真实接口冒烟 + 按路径 `git commit`。

---

## 五、2026-10-29 诊断去重（老板反馈「同一个问题诊断有很多」）

**问题**：同一 Pod 同一故障在窗口内反复告警（探针抖动 / 重启计数递增 / watcher 每轮扫描），
每条告警都调一次 AI 并落一条 `ai_diagnosis_results` → 台账里同一问题十几条（实测同一 Pod 最多 15 条），
既费钱又看不出结论。诊断 753 条里按「集群+ns+资源+fault_type」归并后只有 512 条（重复占 32%）。

**改法（提交 `feat(诊断): 同一问题诊断去重`）**：
1. 新增 `api-server/app/services/diagnosis_reuse.py`
   - `find_reusable_diagnosis(cluster_id, ns, resource, fault_type, window)`：窗口内最近一条**有结论**的诊断
   - `find_alert_diagnosis(alert_id)`：同一告警自己的历史诊断（N5 聚合告警重复诊断的根治）
2. `alert_auto_service.process_alert`：本告警无诊断时，先按窗口复用同资源同故障诊断 → 不调 AI
3. `pod_restart_service._diagnose_and_notify`：① 同告警已诊断 → 永不复诊；② 再按窗口跨告警复用
4. `api/fault_diagnosis.py`：列表默认 `group=true`，按「集群+ns+资源+fault_type」归并，返回
   `occurrences`（告警次数）/ `diagnosis_count`（诊断条数），代表行=最新告警；`group=false` 保留原行为
5. 前端 `FaultDiagnosis.tsx`：新增「次数（×N）」列 + 顶部归并说明；`types.ts` 补字段
6. 新设置 `diagnosis_reuse_minutes`（默认 30，0=关闭复用）

**验证**：`py_compile` / `tsc -b` 通过；真库实测 2090 号告警走 `_diagnose_and_notify` → 复用 #755、AI 调用 0 次；
`/fault-diagnoses?with_total=true` → 总组数 1110（原 1795 行）；CDP 页面实测列头含「次数」、行内 `×6/×8` 标签正常。

**后续（当日已修）**：诊断上下文里 `metrics_trend` 753/753 全空（ai-service 的 `PROMETHEUS_URL` 为空，
与 api-server 的 `prometheus_service` 两套配置不互通）→ 诊断只能看日志/事件，结论停在「探针失败」表层。
2026-10-29 已改为 **api-server 采集指标注入诊断上下文**（`services/diagnosis_metrics.py`）：Pod 容器
CPU/内存（含 limit 使用率）/重启/就绪 + 节点 Ready/CPU/内存/load1/磁盘，近 60 分钟 min/max/avg。

### 探针专项（2026-10-29 当日完成，`services/probe_audit.py`）

探针类故障（`PodRestartProbeFail` / `CrashLoopBackOff`）占告警大头，原诊断只会说「探针失败，去看 health indicators」。
现在诊断前自动采集并注入（`probe_evidence`）：

1. 从 Pod YAML 解析 liveness / readiness / startup 的**真实参数**（path、port、timeoutSeconds、periodSeconds、failureThreshold、initialDelaySeconds）
2. 容器 running 时**进容器实测健康端点**一次（`curl -w '%{http_code} %{time_total}'`，无 curl 退 `wget -S`；executor 只允许单命令不经 shell，所以不能写管道/脚本）
3. 本地规则判定（不靠模型）：实测耗时 > timeout → 「探针必然超时」；端点非 2xx → 「端点本身不健康」；timeout<2s / failureThreshold<3 / period<5s → 参数激进；readiness 与 liveness 同端点 → 慢初始化会被杀

实测样例（`prod-lch/efreight-prm-…`）：三探针 `timeout=30s failureThreshold=10 initialDelay=80s`，端点 HTTP 200 / 6ms
→ 结论：**探针配置不背锅**，重启是应用/依赖侧问题（此前的 AI 结论把它归为「探针超时」是错的）。
ai-service 侧：`render_ctx` 增 `{probe_evidence}`，存量模板没占位符时自动追加在 prompt 末尾（不改库里的提示词）。
