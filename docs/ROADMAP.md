# API-OPS 平台 —— 剩余需求与开发顺序（Roadmap）

> 整理：小吴　更新：2026-10-15　依据：代码核实（最新提交 `7fb3426` / 2026-10-14 18:30，工作树干净）
> 用途：一张表看清「还剩什么没做 + 建议先做哪个」。状态一律以**代码为准**，`REQUIREMENTS-BACKLOG.md` 为历史流水。

**图例**：🟢 可立即开发（无外部依赖）｜🟡 需外部前置/部署决策｜🔴 待老板拍板口径｜⚪ 未排期建议项

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
| **B2** | **R2.3 集群侧 JMX Exporter 安装** | 物料已备（`docs/jvm-exporter/`），无集群安装 → 无 `jvm_*` 指标（接口 `connected=false`） | 需在目标应用/集群挂 javaagent:9404 + Prometheus 采集 |
| **B3** | **平台自身生产部署** | 当前仅本地/测试验证 | 关联 M10（发布/回滚）、M11（备份/DR）|

### C. 🔴 待老板拍板口径（非开发，定了一并收口）

| # | 事项 | 现状口径 |
|---|---|---|
| **C1** | 仪表盘「自动处理」KPI 口径 | 现按**成功执行动作数**统计（偏低）；是否改按**告警条数** |
| **C2** | 「诊断失败」是否单独记录 | 现映射为 `pending` 未转人工 |
| **C3** | R1 准入最终风险口径 | 现 `fail-open`（老板 10-12 定：先用默认，开发完再定） |

### D. ⚪ 未排期建议项（M1–M12，运维/平台化）

> 来源：`docs/PENDING-REQUIREMENTS-DETAIL.md` 第三节。

| # | 需求 | 触发条件 | 工时 |
|---|---|---|---|
| M1 | 平台健康（探针 + 右上角展示） | 平台上 k8s | 0.5d |
| M2 | executor 高可用（探针+自动重启+掉线告警；长期拆 watcher/执行） | 线上可用性要求提高 | 3d |
| M3 | 告警风暴降噪（根因聚合 + 抑制窗口 + 限流/摘要） | 出现告警风暴 | 2d |
| M4 | 数据增长治理（统一保留策略 + 大表时间分区 + audit 归档不删） | 表数据到千万级 | 2d |
| M5 | 前端首屏/打包优化（React.lazy + 动态 import + vendor 拆包） | 首屏慢被投诉 | 1d |
| M6 | API 契约（导出 openapi.json + 前端自动生成类型） | 接口变动频繁 | 1.5d |
| M7 | 时区统一（DB 存 UTC、展示层转时区） | 出现时区错乱 | 1.5d |
| M8 | 权限越权测试入 CI（低权限遍历所有 API 断言 403） | 安全审计前 | 1d |
| M9 | 配置热更新（统一配置读取层 + 「已生效」提示） | 改配置要重启被烦到 | 1d |
| M10 | 平台自身发布/回滚（容器化 + 滚动发布 + 保留上一版镜像） | 平台正式上线 | 1.5d |
| M11 | 备份 / DR（MySQL 定时备份 + 异地；确认 Redis 持久化） | 上线前 | 1d |
| M12 | AI 用量异常告警（单日成本突增 / 异常调用量） | 接入付费模型后 | 0.5d |

---

### E. 🟢 智能诊断证据完备（2026-10-15 新增线）

| # | 需求 | 状态 | 落点 / 缺口 | 工时 |
|---|---|---|---|---|
| **E1** | **JVM 结果全量接入应用诊断** | ✅ 完成（`6583f63`） | 新增诊断工具 `jvm_diag`（9 动作）；快速诊断预采集探测到 Java 进程自动追加 JVM 证据（vm_flags/heap_info/gc_class_histo/thread_dump）；真机冒烟通过 | — |
| **E2** | **诊断日志口径对齐** | ✅ 完成（`6583f63`） | `search_logs` 改用容器 stdout + `--previous`（上次崩溃实例），不再走 ES（此前重构遗漏） | — |
| **E3** | **主动诊断入口** | ✅ 完成（`85f13ff`） | 巡检服务：持续高水位/频繁重启但无告警 → 自动 AI 全面诊断（source=proactive，不建告警）；后台每 30min + AgentChat「立即巡检」；真机冒烟通过 | — |
| **E4** | **按故障类型诊断模板** | ✅ 完成（`d529995`） | 预采集按关键词命中模板：探针失败（get_probe_config+get_deployment）/内存OOM/资源高水位/崩溃重启；新增 `get_probe_config` 工具；证据加「故障类型判定」段 | — |

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
