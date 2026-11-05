# Agent-only 纳管架构（需求 / 设计基线）

> 提出：老板 2026-10-21 10:52–11:04 讨论后定方向
> 整理：小吴　状态：**P1–P4 全部完成**（2026-11-04：自接入 / 版本上报 / 流式 RPC / 升级回退 / mTLS）
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
| in-cluster 自接入（本集群自动登记） | ✅ **已完成（2026-11-04）**：executor 启动 `bootstrap.EnsureInCluster`，幂等键 apiserver 地址，`source=in-cluster`，客户端走 ServiceAccount（不落 kubeconfig） | `internal/bootstrap/incluster.go`、`k8sclient.Manager.MarkInCluster` |
| agent 版本上报 / 可见 | ✅ **已完成（2026-11-04）**：AgentFrame 增 `version`，注册时上报并落 `clusters.agent_version`，集群列表显示 | `proto/agent.proto`、`agent_gateway.py`、`cmd/agent/main.go`（`-ldflags -X main.version`） |
| agent 批量升级 / 灰度 / 回退 | ✅ **已完成（2026-11-04）**：经隧道改 Deployment 镜像并等待重连回报版本；批量顺序滚动；回退=传旧 tag | `api/clusters.py`（`/agent/upgrade`、`/agents/upgrade`） |
| 隧道安全 | ✅ **mTLS（2026-11-04）**：平台自建 CA 签发服务端/客户端证书，网关 `require_client_auth=True`；agent 用 `-ca-file/-cert-file/-key-file`；明文连接被拒 | `services/agent_pki.py`、`agent_gateway.start_server`、`cmd/agent/main.go` |
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


---

## 九、Agent 镜像构建 / 推送（2026-11-04 落地）

```bash
# 1) 本机交叉编译（不需要目标机装 Go）
cd k8s-executor
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath \
  -ldflags "-s -w -X main.version=0.1.0" -o /tmp/autohealops-agent ./cmd/agent

# 2) 传到有 docker 的机器（192.168.6.149 已验证）
scp /tmp/autohealops-agent agent/Dockerfile root@192.168.6.149:/tmp/agent-build/
ssh root@192.168.6.149 'cd /tmp/agent-build && mv autohealops-agent agent && docker build -f Dockerfile -t harbor.efreight.cn/ops/autohealops-agent:0.1.0 .'

# 3) 推送
ssh root@192.168.6.149 'docker tag harbor.efreight.cn/ops/autohealops-agent:0.1.0 harbor.efreight.cn/ops/autohealops-agent:latest && docker push harbor.efreight.cn/ops/autohealops-agent:0.1.0 && docker push harbor.efreight.cn/ops/autohealops-agent:latest'
```

- 底包：`harbor.efreight.cn/ops/python:3.10-slim`（Debian slim，带 CA，方便日后 mTLS）；静态二进制，非 root（uid 1001）。
- 平台配置（`.env`）：`AGENT_IMAGE` / `AGENT_IMAGE_PULL_SECRET`（私有 harbor 时填目标集群已有的 Secret 名）/ `AGENT_GATEWAY_PUBLIC_ADDR`（agent 能访问的 host:port，不填则按平台域名 + 9095 推导）。
- 自测（2026-11-04 已通过）：容器在 192.168.6.149 上跑通注册（version=0.1.0）+ 隧道执行 ListNodes + 平台正确记录 `agent_version`。


---

## 十、隧道安全（P4，2026-11-04 落地——老板定：平台自建 CA + 不允许明文）

- **CA**：平台首次启动生成 ECDSA P-256 自签 CA（10 年），落在 `data/agent-ca/`（`ca.crt` / `ca.key`，私钥 0600），**已加入 .gitignore，严禁入库**。
- **服务端证书**：网关启动时用 CA 签发 `server.crt/server.key`（2 年），SAN 取自 `AGENT_GATEWAY_PUBLIC_ADDR` + `PLATFORM_PUBLIC_URL` + localhost/127.0.0.1；SAN 变化或临期 30 天会自动重签。
- **客户端证书**：每次渲染 `install.yaml` 时为该集群现场签发（CN=`autohealops-agent-<cluster_id>`，5 年，EKU clientAuth），随 Secret `autohealops-agent-tls` 下发，挂载到 `/tls`。
- **网关**：`grpc.ssl_server_credentials(..., require_client_auth=True)` → 没有客户端证书的连接直接握手失败（明文 agent 会报 `error reading server preface: EOF`）。
- **agent**：`-ca-file / -cert-file / -key-file`（+ 可选 `-server-name`）；未配置时退回明文并打印告警（**仅本地调试**）。
- 开关：`AGENT_TLS_ENABLED`（默认 true）、`AGENT_CA_DIR`（默认 `data/agent-ca`）。
- 验证（2026-11-04）：本地 agent 带证书 → 注册 v0.2.0、隧道 ListNodes 50 节点；不带证书 → 握手失败、3s 重连循环。
