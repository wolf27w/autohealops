# 集群监控接入 Netdata —— 需求文档

> 状态：**草案（待老板评审）**
> 日期：2026-10-09
> 关联：`ARCHITECTURE.md`（监控复用原则）、`CLUSTER-MGMT-REQUIREMENTS.md`、`MIDDLEWARE-MONITORING.md`、`REQUIREMENTS-BACKLOG.md`
> 决策人：老板

---

## 一、需求背景

### 1.1 现状与痛点

- **展示层弱**：集群管理页「监控」能力有限——Pod 指标曲线为自研采集（`k8s-executor/internal/metrics/collector.go`），**每分钟全量、仅 CPU/内存/网络三项、保留 3 天**；展示仅有单 Pod 抽屉曲线，**无节点级 / 集群级实时全景**。
- **采集层重复造轮子**：`ARCHITECTURE.md` 已定原则「**监控指标复用现有 Prometheus，平台不自行采集存储监控数据**」，但实际自研了 `metrics/collector.go`（Pod 指标）+ `quota/collector.go`（配额快照）。
- **根因**：**展示层弱 + 采集层重复**，是老板判断「自研监控做得不好」的核心。

### 1.2 关键事实（澄清）

| 问题 | 结论 |
|---|---|
| Netdata 的秒级监控是否基于平台 Prometheus？ | **否**。Netdata 自采集自存储（原生 collector + dbengine），与 Prometheus **无依赖**，可并存 |
| Netdata 与平台 Prometheus 的关系 | 各自独立；K8s 场景 Netdata 读 **kubelet** 的 cadvisor 端点（kubelet 自带），**非**平台 Prometheus 服务器 |
| 部署位置 | **装在被管的每个集群**（指标须在被监控机器上采）；AIOps 中心管控面按**开关**下发 |
| parent / child | 同一集群内**一起部署**（一份多文档 manifest）：child = DaemonSet(每节点) + parent = Deployment + PVC + Service |

---

## 二、老板拍板口径（2026-10-09）

- **引入开源 Netdata 做「实时监控 + 指标阈值告警」**，把"监控"与"业务闭环（诊断/通知/修复）"分层。
- **告警按性质分工（2026-10-09 修正 · 方案 A）**：
  - **指标阈值型告警 → Netdata**（内置 health 检查更全、秒级、开箱即用）
  - **事件型告警 → 平台自研 watcher**（K8s 事件，Netdata 不做）
  - 两者都经字段映射汇入平台闭环（§十一）
- **方案 A**：平台 `resource_alert` 的 4 类规则（容器 CPU/内存/网络阈值）**迁移到 Netdata**（`fault_type` 不变），指标阈值告警整体交给 Netdata。
- **保留**：事件型 watcher（`pod_watcher`/`node_watcher`）、DB 告警（`db_alert`）**不变**。
- **parent 使用 PVC**（持久化，重启不丢历史；使用**集群默认 StorageClass**）。
- **Netdata 版本**：开源 **stable**（镜像 `netdata/netdata:stable`）。
- **镜像来源**：**Docker Hub**（`netdata/netdata`）。
- **stream key**：每集群随机生成，**平台加密留档**（同 kubeconfig，AES-256-GCM）+ 写入目标集群 ConfigMap。

---

## 三、目标与范围

### 3.1 目标

1. **开关驱动部署**：集群接入 AIOps 后**默认不装**；用户打开「Netdata 监控」开关后，才向该集群下发 Netdata（parent + child）。
2. **按集群展示**：集群管理页提供**各集群独立**的监控入口，iframe 内嵌该集群实时监控；用户无需跳出平台、无需多套登录。
3. **监控/告警分层 + 分工**：Netdata 提供实时展示 + **指标阈值告警**；平台提供**事件型告警 + 闭环**（诊断/通知/修复/工单）。两者告警统一汇入平台闭环。

### 3.2 范围（明确不做）

- ❌ **不改事件型告警链路**（`pod_watcher` / `node_watcher` 不动）。
- ❌ **不改 DB 告警**（`db_alert` 不动）。
- ❌ **不做数据融合**（不把 Netdata 指标拉回平台自己的库 / 图 / 告警引擎）。
- ❌ **不替换 DB 监控**（`db_collector` 慢SQL/锁/表空间为业务诊断数据，保留）。
- ❌ **不替换配额/工作负载快照**（`quota/collector`，为"业务视角"，保留）。
- ♻️ **例外（方案 A）**：`resource_alert` 的 4 类规则**迁移到 Netdata**（`fault_type` 不变）；平台侧规则界面/数据处置见 §七。
- ❌ **`pod_metrics` 自研采集暂不删除**（见 §七 评估项 1）。

---

## 四、需求详细

### R1 集群开关驱动（默认关；开=安装/启动，关=停服务不卸载）

- **触发点**：集群接入**默认不装** Netdata（开关默认关）；用户打开「Netdata 监控」开关 → `POST /clusters/{id}/monitoring/enable` **异步**下发安装/启动（不阻塞接入流程）。
- **关闭开关** → `POST /clusters/{id}/monitoring/disable` **仅停服务**（停 parent/child 工作负载），**保留 PVC 及全部资源**，不卸载。
- **重开**：复用已有 PVC 恢复运行（历史数据延续）。
- **复用现有模式**：mirror `rbac_service.setup_cluster_rbac()` 的「自举」。
- **下发内容**（目标集群，命名空间 `apiops-monitoring`）：

| 组件 | 说明 |
|---|---|
| parent | Deployment(1 副本) + **PVC(持久化 dbengine)** + Service(**ClusterIP**) |
| child | DaemonSet(每节点；hostNetwork/hostPID + 宿主机挂载) |
| RBAC | ServiceAccount + ClusterRole + ClusterRoleBinding |
| ConfigMap | stream 配置（parent 接收 key + child 目标） |

- **失败处理**：不阻塞集群接入；结果写入 `clusters.monitoring_status`，前端可见，支持手动重试。

### R2 存量集群手动开启

- 存量集群默认 `monitoring_enabled=false`；管理员在集群详情/列表**手动打开开关**即触发下发（同一 `monitoring/enable` 端点）。
- **不自动补装**（无 health-check 自动触发）。

### R3 集群管理页监控入口

- 集群列表/详情：加「Netdata 监控」**开关（Switch）**（开=启用服务/关=停服务）；另设「卸载」入口（删除资源但**默认保留 PVC**）。开启后显示部署状态（部署中/成功/失败），成功后显示「监控」按钮。
- 点击 → iframe 打开该集群监控（**平台内**，不跳出）。

### R4 监控展示（平台内）

- 后端反代：新增路由 `GET /api/v1/clusters/{id}/monitoring/*`。
- 链路：**AIOps 后端 → apiserver Service Proxy（复用 `prometheus/proxy.go` 手法）→ 目标集群 `netdata-parent:19999`**。
- 好处：parent 仅 ClusterIP，**不出网、无需 Ingress、无需打通跨环境网络**；鉴权统一在 AIOps。

### R5 幂等 / 升级 / 停用 / 卸载

- **幂等**：executor 用 **server-side apply**，重复下发不报错。
- **升级**：改镜像 tag / 留存天数 → 重跑 enable。
- **停用（关开关）**：停 parent/child 工作负载（scale 0），**保留** PVC / ConfigMap / SA / RBAC / stream key；重开即恢复，数据延续。
- **卸载（单独操作，非关开关）**：删除 Netdata 资源；**PVC 默认保留**（历史数据不丢），重开复用；如需彻底删数据须显式确认。
- （`delete_cluster` 时随集群删除 Netdata 资源，同 `stop_watch`。）

---

## 五、技术方案

### 5.1 数据模型（1 条 alembic 迁移）

`clusters` 表新增：

| 字段 | 类型 | 说明 |
|---|---|---|
| monitoring_enabled | bool (default false) | 是否启用 Netdata 监控（**用户开关**，默认关；开=安装/启动，关=停服务不卸载） |
| monitoring_status | varchar(255) | 实际状态（none/deploying/running/stopping/stopped/failed/uninstalled；mirror `rbac_status`） |
| monitoring_url | varchar(255) | 平台侧反代逻辑路径 |
| monitoring_ns | varchar(64) | 部署命名空间（默认 `apiops-monitoring`） |
| parent_storage_size | varchar(16) | parent PVC 容量（默认 `20Gi`，集群默认 SC） |
| monitoring_retention_days | int | parent 留存天数（默认 7） |
| stream_key_encrypted | text | stream API key 密文（AES-256-GCM；**平台留档**，同 kubeconfig） |

### 5.2 平台服务（api-server）

| 文件 | 改动 |
|---|---|
| **新增** `services/monitoring_service.py` | mirror `rbac_service.py`：`setup_cluster_monitoring()` / `teardown_cluster_monitoring()` |
| `clients/executor_client.py` | 加 `setup_monitoring` / `teardown_monitoring` |
| `api/clusters.py` | 新增 `monitoring/enable`（开=安装/启动）、`monitoring/disable`（关=停服务）、`monitoring/uninstall`（删资源保留 PVC）端点；不自动触发 |
| **新增** `api/monitoring.py` | 反代路由（也可并入 clusters 路由） |

### 5.3 executor 新增 RPC

```proto
rpc SetupMonitoring(SetupMonitoringRequest) returns (SetupMonitoringResponse);    // 安装（首次）/启动（已装但停）
rpc StopMonitoring(StopMonitoringRequest) returns (StopMonitoringResponse);       // 停服务（保留资源与 PVC）
rpc TeardownMonitoring(TeardownMonitoringRequest) returns (TeardownMonitoringResponse);  // 删资源（默认保留 PVC）

message SetupMonitoringRequest {
  int64  cluster_id     = 1;
  string namespace      = 2;   // 默认 apiops-monitoring
  int32  retention_days = 3;   // 默认 7
  string storage_size   = 4;   // 默认 20Gi
  string trace_id       = 5;
}
message SetupMonitoringResponse {
  bool   success         = 1;
  string parent_endpoint = 2;  // netdata-parent.apiops-monitoring.svc:19999
  string error           = 3;
}
```

- **Go 实现**：`dynamic.Interface` + `unstructured` + `types.ApplyPatchType`（**server-side apply**）下发多文档 manifest，天然幂等。

### 5.4 Netdata manifest（骨架，参数化）

> ⚠️ 落地前须对照所用 `netdata/netdata` 版本核验：stream 配置机制（env vs `stream.conf`）、容器运行时 socket 路径（docker vs containerd）。

组件清单（全部落入命名空间 `apiops-monitoring`）：

1. **Namespace** `apiops-monitoring`
2. **ServiceAccount** `netdata`
3. **ClusterRole** `apiops-netdata`（nodes/pods/services/... get·list·watch；nonResourceURL `/metrics`）
4. **ClusterRoleBinding** `apiops-netdata`
5. **ConfigMap** `netdata-stream`（`parent-stream.conf` 接收 key / `child-stream.conf` 目标+key）
6. **PVC** `netdata-parent-data`（20Gi，集群默认 SC）
7. **Deployment** `netdata-parent`（replicas=1；挂 PVC + `stream.conf`；`NETDATA_DISABLE_TELEMETRY=1`）
8. **Service** `netdata-parent`（ClusterIP:19999）
9. **DaemonSet** `netdata-child`（hostNetwork/hostPID；挂 `/proc` `/sys` `/sys/fs/cgroup` `/etc/os-release` `/var/run/docker.sock`；流式发 parent）

关键片段（示意）：

```yaml
# parent（聚合 + 存储 + UI）
kind: Deployment
metadata: {name: netdata-parent, namespace: apiops-monitoring}
spec:
  replicas: 1
  template:
    spec:
      serviceAccountName: netdata
      containers:
      - name: netdata
        image: netdata/netdata:stable
        args: ["-D"]
        env: [{name: NETDATA_DISABLE_TELEMETRY, value: "1"}]
        volumeMounts:
        - {name: data,   mountPath: /var/lib/netdata}
        - {name: stream, mountPath: /etc/netdata/stream.conf, subPath: parent-stream.conf}
      volumes:
      - {name: data,   persistentVolumeClaim: {claimName: netdata-parent-data}}
      - {name: stream, configMap: {name: netdata-stream}}
---
# child（每节点，采宿主机级）
kind: DaemonSet
metadata: {name: netdata-child, namespace: apiops-monitoring}
spec:
  template:
    spec:
      hostNetwork: true
      hostPID: true
      tolerations: [{operator: Exists}]
      containers:
      - name: netdata
        image: netdata/netdata:stable
        args: ["-D"]
        securityContext: {capabilities: {add: ["SYS_PTRACE","SYS_ADMIN"]}}
        volumeMounts:
        - {name: proc,   mountPath: /host/proc, readOnly: true}
        - {name: sys,    mountPath: /host/sys,  readOnly: true}
        - {name: cgroup, mountPath: /host/sys/fs/cgroup, readOnly: true}
        - {name: stream, mountPath: /etc/netdata/stream.conf, subPath: child-stream.conf}
```

### 5.5 前端

- 集群管理页（`ClusterDetail` / 集群列表）：监控入口按钮 + 部署状态 + iframe 容器。
- `services/api.ts`：新增监控相关调用。

---

## 六、访问与安全

- parent **只开 ClusterIP**；仅经 AIOps 后端反代访问。
- 鉴权走平台现有登录/权限（复用集群读权限）。
- 关闭 Netdata 自带告警与云端遥测（`NETDATA_DISABLE_TELEMETRY=1`，不 claim 云，纯自建）。
- **stream API key** 每集群随机生成，**平台加密留档**（`clusters.stream_key_encrypted`，AES-256-GCM，同 kubeconfig）+ 写入目标集群 ConfigMap。

---

## 七、评估项 / 开放问题

### 已拍板（2026-10-09 老板确认）

| 项 | 决定 |
|---|---|
| parent PVC 存储类 | **集群默认 StorageClass** |
| Netdata 版本 | **开源 stable**（`netdata/netdata:stable`） |
| 镜像来源 | **Docker Hub**（`netdata/netdata`） |
| stream key 留档 | **平台加密留档**（`clusters.stream_key_encrypted`，AES-256-GCM）+ 写目标集群 ConfigMap |

### 待评估（实现期确认，非老板决策）

1. **`pod_metrics` 是否随展示切换下线？ → 暂不。**
   核实：它仍被 ① **AI 诊断工具** `get_pod_metrics`（`services/tool_registry.py`）；② **仪表盘** CPU/内存汇总（`api/stats.py`）消费。故**保留**；待展示全面切 Netdata 后再评估收敛。
2. **多容器运行时**：DaemonSet hostPath socket（`docker.sock` vs `containerd.sock`）按环境适配——实现期按目标集群运行时探测。

---

## 八、验收标准

1. 新接入一个集群 → **无需手动操作**，目标集群出现 `apiops-monitoring` 命名空间，parent/child 就绪。
2. 集群管理页该集群出现「监控」入口，点开可见该集群**秒级实时监控**（节点/容器全景）。
3. 卸载集群 → Netdata 资源一并清理。
4. **告警链路零变化**：接入 Netdata 前后，平台告警行为完全一致（回归验证）。
5. **幂等**：重复下发不产生重复资源/报错。

---

## 九、分期建议

| 期 | 内容 |
|---|---|
| **P1** | manifest + executor RPC + 自动下发 + 展示入口（核心闭环） |
| **P2** | 存量集群补装 + 卸载 + 升级 |
| **P3** | 独立 DB 实例 Netdata 总览（可选补充） |
| **P4** | Netdata 告警 webhook 入站 + 字段映射 + health 裁剪（指标阈值告警接入平台闭环）；ECS/主机接入；`resource_alert` 规则迁移（详见 §十、§十一） |
| **P5** | 主机/Docker 诊断 + 修复能力（补齐闭环后两环） |

---

## 十、扩展：集群外主机（ECS）接入监控

### 10.1 背景

- 平台现有监控面向 K8s 集群；ECS 等**集群外主机**（VM/裸机）**无覆盖**。
- Netdata agent 可安装在**任意 Linux 主机**，天生支持主机级监控，并**自动发现该主机上的 Docker 容器**。

### 10.2 与「集群接入」的区别（关键）

| | K8s 集群 | 集群外 ECS |
|---|---|---|
| 接入凭据 | kubeconfig | **无**（裸主机） |
| AIOps 自动下发 | 可（走 k8s API） | **不可**（无 k8s API） |
| Netdata 装法 | DaemonSet（编排下发） | **脚本安装 / SSH 远程装 / 标准镜像** |
| 数据模型 | `clusters` 表 | **新增 `hosts` 表**（主机/资产） |

> 结论：ECS 接入**不能复用**集群接入流程，需**另立一类「主机监控」**。

### 10.3 需求

| # | 需求 | 说明 |
|---|---|---|
| **H1** | 主机接入 | 登记主机（名称/地址/环境标签/上报 parent）；主机 agent 上报到指定 parent |
| **H2** | 部署方式（三选一） | ① 手动脚本安装（**起步推荐**）② SSH 自动安装（需引入主机凭据管理）③ 标准镜像预置 netdata |
| **H3** | **Docker 容器监控** | **同一 agent 自动覆盖**（读 `docker.sock`/`containerd.sock` + cgroup），**无需额外组件**；每容器 CPU/内存/网络/磁盘IO/状态；支持按容器 / compose 项目 / 镜像分组过滤 |
| **H4** | 展示 | AIOps 新增「主机监控」入口，与「集群监控」**并列**；主机构 → 下钻到其上 Docker 容器 |
| **H5** | parent 方案 | 外部主机**专用 parent**（网络隔离时，推荐）；或复用某集群 parent（同网段时） |

### 10.4 边界

- 主机/Docker 的**告警**见 §十一（走 Netdata）。
- ECS 只需开放 **agent → parent 的出站**（主机侧**无需入站端口**）。
- DB 监控照旧走平台 `db_collector`（Netdata 可在同一主机顺带监控数据库实例作为补充）。

---

## 十一、扩展：Netdata 告警 ↔ AIOps 闭环

### 11.1 口径（2026-10-09 修正）：按「告警性质」分工，不按「集群/主机」

> 修正原因：老板指出平台自研告警在**指标阈值广度**上不及 Netdata（后者内置上百条 health 检查、秒级、开箱即用）。故**按告警性质分工**，方案 A：

| 告警性质 | 来源 | 理由 |
|---|---|---|
| **指标阈值型**（CPU/内存/磁盘/IO/网络/系统/K8s 资源/主机/Docker…） | **Netdata** | 内置 health **更全**、秒级、**默认开** |
| **事件型**（CrashLoop/OOM/ImagePull/Pending/TerminatingStuck/NodeNotReady…） | **平台自研 watcher** | Netdata **不做事件**（K8s watch） |
| **业务闭环**（诊断/通知/修复/工单） | **平台** | 两边告警统一汇入 |

- 二者互补 = **全覆盖**：Netdata 补指标广度，平台补事件 + 业务逻辑。
- **去重边界**：Netdata 只报**指标阈值类**；平台只报**事件类**，重叠最小。
- **方案 A**：`resource_alert` 规则**迁移到 Netdata**（`fault_type` 不变）。
- **接入方式：webhook**（Netdata → 平台入站接口）。
- 具体 **Netdata alarm → `fault_type` 映射清单**见 **`docs/NETDATA-ALARM-MAPPING.md`**（草稿）。

### 11.2 闭环链路

```
Netdata 告警触发（WARNING/CRITICAL）
   ↓ webhook（带 token，Netdata → AIOps）
AIOps 入站接口 POST /api/v1/webhooks/netdata（复用 webhooks.py + 鉴权）
   ↓ 字段映射
写 alerts 表（复用：去重 / 聚合 / 维护窗口 / 限频）
   ↓ triggerAlertProcess
【诊断】知识库优先 → AI 诊断（★ 主机/Docker 场景需扩展诊断工具）
   ↓
【通知】notification_service（复用，零改造）
   ↓
【修复/处置】自动修复 / 转审批 / 转工单（★ 主机/Docker 修复需扩展执行器）
   ↓
Netdata 告警 CLEAR → webhook → 平台告警自动 resolved（闭环关闭）
```

### 11.3 字段映射（Netdata → 平台 Alert）

| Netdata | 平台 Alert |
|---|---|
| alarm 名（如 `disk_space_usage` / `docker_container_oom`） | `fault_type`（映射表定义，如 `HostDiskHigh` / `DockerContainerOom`） |
| host / chart / container | `resource_type`(host/container) + `resource_name` |
| status：CRITICAL / WARNING | `severity`：high / medium；`status=pending` |
| status：CLEAR | `status=resolved`（自动关闭） |
| info / value / units | `message` |

> 具体逐项映射（Netdata alarm → `fault_type`/`resource_type`/`severity`）见 **`docs/NETDATA-ALARM-MAPPING.md`**（草稿）。

### 11.4 复用 vs 需扩展

| 环节 | 复用平台现有 | 需扩展 |
|---|---|---|
| 告警接入 / 去重 / 聚合 / 生命周期 | ✅ | — |
| 通知 | ✅ `notification_service` | — |
| 工单 | ✅ 自动转工单 | — |
| **诊断** | 知识库优先 ✅ | ★ **主机/Docker 诊断工具**（现有为 K8s 语义：`exec_in_pod`/`describe_pod` 等） |
| **修复** | 审批 / 工单流 ✅ | ★ **主机/Docker 执行能力**（SSH 到主机 / docker API：重启容器 / 清磁盘等） |

### 11.5 关键限制（务必知悉）

- **闭环的「诊断 + 修复」两环，对主机/Docker 场景是新能力**，不是现成复用——**这是主要工作量**。
- Netdata 会按指标**自行 CLEAR**；平台侧「已解决」**不回写** Netdata（无需）。但若指标未恢复，Netdata 会**重申**，需靠平台去重 / 冷却抑制（复用现有逻辑）。
- 告警接入用 **webhook（实时）**；建议同时留一个**轮询 `/api/v1/alarms` 兑底**（webhook 丢失时对账）。
- **Netdata 告警默认开、且偏吵**：默认 notification **未配**（需配 webhook 指向平台）；默认 health 检查**需裁剪/覆盖**（`health.d/*.conf`：关不需要的、调阈值、映射 `fault_type`），不可全放开。

### 11.6 分期

- **P4**：Netdata 告警 webhook 入站 + 字段映射 + health 裁剪（指标阈值告警接入平台闭环）；ECS/主机接入；`resource_alert` 规则迁移到 Netdata。
- **P5**：主机/Docker 诊断 + 修复能力（补齐闭环后两环）。

---

## 附：部署要点（勿踩坑）

1. **parent 必须 PVC**：否则 pod 重启历史数据全丢。
2. **child 必须挂宿主机路径**（`/proc` `/sys` `/cgroup` socket）：不挂看不到宿主机级细节，等于白装。
3. **每集群一套**：parent + child 同集群内一起部署（一份 manifest，一次 apply）。
4. **别把 Netdata manifest 塞进平台自己的 `k8s/`**：`k8s/` 是部署 AIOps 自己的；Netdata 是"下发到被管集群的模板"，应独立（内嵌 executor 或用 `k8s/templates/`）。
5. **UI 必须加鉴权**：Netdata UI 默认无认证，切勿裸奔到集群外。
