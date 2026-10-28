# Agent-only 纳管架构（需求 / 设计基线）

> 提出：老板 2026-10-21 10:52–11:04 讨论后定方向
> 整理：小吴　状态：**进行中**（2026-10-28：P2 告警检测下沉 + P1 全方法反向代理已完成；剩 P3/P4）
> 现状核实：2026-10-21，以代码为准（`cmd/agent/main.go`、`clients/executor_client.py`、`agent_gateway.py`）

---

## 一、目标（老板原话归纳）

1. 平台**自身部署进生产集群**，启动后**自动把本集群加进来**（不落任何 kubeconfig）；
2. 再要纳管其它集群（测试 / UAT / 客户集群），**只下发一个 agent** 即可，不用 kubeconfig、不用开入站端口；
3. 一个中心平台管所有集群，**告警 / 通知 / 工单全部集中**；
4. **不再担心凭据到期**：agent 用集群内 ServiceAccount（in-cluster token 由 kubelet 自动轮转）；
5. 集群/平台升级后，agent 能**跟随升级并回报版本**（版本可见、可灰度、可回退）。

## 二、目标拓扑（推荐形态）

```
                   ┌─ 生产集群（平台就住这）
                   │    ├─ api-server / ai-service / frontend / executor / DB(或外部)
   浏览器 ────────► │    └─ 本集群：in-cluster ServiceAccount 直连（无凭据、无证书、不套 agent）
                   ├─ 测试集群：agent（反向隧道，平台不开入站端口）
                   └─ UAT / 客户集群：agent
```

- **中心平台**：诊断、决策、自愈、告警、工单、通知、数据库、UI —— 全部集中
- **agent**：集群内的「手和眼」，无状态、无数据库、不落配置；只接受平台下发的请求并回传结果

> ❌ **不推荐**「自己的集群也套一个 agent」（老板问的"平台里也需要一个 agent"）：技术上可行，
> 但会给自己管自己多绕一跳、多维护一个 Pod，收益只是"代码路径统一"，不值得。

## 三、与 B/S 的区别（术语澄清）

- **B/S** 说的是界面层：浏览器访问服务端 —— 本项目前端本来就是 B/S。
- **本需求**说的是部署拓扑：**中心-边缘（hub & spoke）/ 管理面与数据面分离**，
  同类参考：Zabbix Server + agent、Prometheus + node-exporter、K8s 控制面 + kubelet、Rancher + cluster-agent。

## 四、现状核实（2026-10-21，代码为准）

| 能力 | 状态 | 证据 |
|---|---|---|
| 反向隧道 + 令牌签发/吊销 + 心跳 + 一键安装清单 | ✅ 已有（2026-10-16 测试环境真连通过） | `api-server/app/agent_gateway.py`、`api/clusters.py`（`/agent-onboard`、`/agent/install.yaml`）、`cmd/agent/main.go` |
| **agent 支持的方法** | ⚠️ **只有 2 个**：`ExecuteCommand`、`GetPodLogs` | `cmd/agent/main.go` → `handleRequest()` 的 switch；其余走 `default: agent 暂不支持的方法` |
| **api-server 走 agent 的调用** | ⚠️ 只有 2 处 | `clients/executor_client.py` 的 `_agent_call`（execute_command、get_pod_logs）；`get_pod_status` / `list_pods` / `list_workloads` / `query_prometheus` / `StartWatch` 等仍直连集群 |
| **告警检测（watcher）** | ✅ **已下沉（2026-10-28, `50eba3c`）**：agent 内跑 Pod/Node watcher（策略由平台 config 帧下发），事件经隧道上报，平台统一落库/去重/触发自动处理 | `executor_client.start_watch()` → `StartWatch`；`internal/watcher/{pod_watcher,node_watcher}.go` |
| in-cluster 自接入（本集群自动登记） | ❌ 未实现 | 无启动 upsert；`clusters.source` 字段已预留 |
| agent 版本管理 / 自动升级 / 回退 | ❌ 未实现 | 只有 `AGENT_IMAGE` 常量用于 install.yaml |
| 隧道安全 | ⚠️ **明文 gRPC**（`insecure`），当前仅 127.0.0.1 使用 | `cmd/agent/main.go` → `grpc.WithTransportCredentials(insecure.NewCredentials())` |
| agent 网关对外可达 | ❌ 仅本机（`AGENT_GATEWAY_ADDR=0.0.0.0:9095`，无外网入口） | `config.py` / 部署清单 |

**结论**：现状是「agent 只能执行命令 + 取日志」的半成品；**agent-only 模式下不会有任何告警**，
与"集中告警"目标直接冲突 → 必须先补「告警检测下沉」。

## 五、分期与工时（建议顺序 P2 → P1 → P3 → P4）

| 期 | 内容 | 要点 | 工时 |
|---|---|---|---|
| **P2 告警检测下沉**（最高优先） | watcher 在 agent 内运行，事件经隧道**主动上报**平台；平台侧改为接收端 | 复用 `internal/watcher`（Pod/节点 watcher）；隧道新增 `event` 帧类型；断线重连后补发最近 N 分钟事件；平台侧做去重（同集群同资源同故障） | 3–5d |
| **P1 全方法反向代理** | agent 变成「通用反向代理」：复用平台 `internal/grpc.Server` 的实现，把 20+ 方法全部接到隧道；客户端**优先 agent、不可达再回退直连** | 除 `StartWatch` 外全覆盖（StartWatch 由 P2 取代）；超时/取消透传；单集群并发上限 | 3–5d |
| **P3 自接入 + 版本管理** | ①平台启动时用 in-cluster config upsert 本集群（打「本集群」标记）②agent 上报版本/心跳，平台可批量灰度升级、暂停、回退 | upsert 幂等键 = 集群 UID；升级用 Deployment 滚动；版本落后可见 | 2.5–3.5d |
| **P4 隧道安全与打通** | gRPC TLS / mTLS（agent 侧带证书），9095 对外（NodePort / LB / nginx·SamWaf 四层） | 证书可复用平台 CA；token 与证书双因子 | 1d |
| **合计** | | | **9.5–14d** |

## 六、验收标准

1. 平台部署进集群后，**5 分钟内自动出现在集群列表**，标记「本集群」，无任何 kubeconfig 落盘；
2. 目标集群**只 apply 一个 install.yaml**，平台即可：列出命名空间/工作负载/Pod、看容器监控、跑诊断、**产生告警**、执行自愈；
3. 断开 agent 隧道后：该集群状态标「离线」，**不产生误告警**；恢复后自动补数据；
4. 一键吊销 agent token → 隧道立刻断开且无法重连；
5. 平台侧看得到每个集群的 **agent 版本**，可批量升级/暂停/回退；
6. 抓包确认隧道流量已加密（P4 完成后）。

## 七、风险与不做

| 风险 | 处理 |
|---|---|
| **重复自愈**（同一集群被两套平台 watch） | 平台侧强约束：同一集群只允许一个在线 agent/watcher；接入时检测冲突并拒绝 |
| watcher 下沉后平台侧「本地无感知」 | 事件上报 + 心跳超时判定离线；告警合并仍在平台侧做，避免边缘重复发通知 |
| 隧道明文 | P4 前**禁止跨公网**使用，只允许内网/本机 |
| 升级 agent 影响在跑的修复 | 升级前检查该集群是否有执行中的 plan，有则跳过本轮 |
| 权限过大 | agent 使用独立 SA + RBAC（`SetupRBAC` 已有），不允许 cluster-admin 长期绑定 |

**明确不做**：
- 不为「平台自己所在集群」再套一个 agent（in-cluster 直连即可）；
- 不做多平台联邦（3 套平台互相聚合）——重复自愈与数据分裂风险高；
- 不把 watcher 放在边缘却由边缘发通知（通知统一在中心，避免风暴）。

## 八、与其它需求的关系

- 取代/合并原 ROADMAP **A2（agent 托管更新）**、**A4（下发开关动作）**、**A3/M1（集群内自接入）**；
- 与 **D1 凭据到期通知** 互补：走完 agent-only 后，kubeconfig 只剩兜底用途，D1 仍建议保留（防兜底凭据过期）；
- 与 **P2 cordon/drain 审批隔离**、**节点诊断** 无冲突：这些都走"平台侧决策 + 集群侧执行"，
  agent 化后执行通道换成隧道，决策逻辑不变。
