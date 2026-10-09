# Netdata 告警 → 平台 fault_type 映射（草稿）

> 状态：**草稿（待评审 / 待用真实 Netdata 实例核实 alarm 名）**
> 日期：2026-10-09
> 关联：`NETDATA-MONITORING-REQUIREMENTS.md`（§十一 告警闭环）
> 决策：方案 A —— 指标阈值告警归 Netdata（webhook 入站）；事件型告警留平台自研 watcher

---

## 〇、目的

方案 A 下，Netdata 负责**指标阈值型**告警，经 **webhook** 汇入平台闭环。本表定义
**Netdata 告警 →平台 `fault_type` / `resource_type` / `severity`** 的映射，作为入站接口
（`POST /api/v1/webhooks/netdata`）的转换依据。

> 映射以「故障语义」为准；**Netdata 告警名（alarm 名）列须用真实实例核实**（见 §六）。

---

## 一、命名约定

### 1.1 resource_type（平台侧）

| resource_type | 含义 | 说明 |
|---|---|---|
| `host` | 集群外主机（ECS/VM） | **新增**（对应 `hosts` 模型） |
| `container` | 主机上的 Docker 容器 | **新增** |
| `node` / `pod` | K8s 节点 / Pod | 已有 |
| `db` | 数据库连接 | 已有（`db_alert`，不在本表） |

### 1.2 fault_type 命名

- 沿用现有 CamelCase 风格（`ContainerCpuHigh` / `NodeNotReady`…）。
- 主机类前缀 `Host*`；主机 Docker 类前缀 `DockerContainer*` / `DockerDaemon*`。

---

## 二、迁移：原 `resource_alert` 规则 → Netdata

> 原 `api-server/app/services/resource_alert_service.py` 的 4 类告警**迁移到 Netdata**，
> `fault_type` **保持不变**（前端/历史数据兼容）。

| 平台 fault_type | 触发语义 | Netdata 侧来源（告警名待核实） | resource_type | 默认 severity |
|---|---|---|---|---|
| `ContainerCpuHigh` | 容器 CPU 使用率 > 阈值 | cgroup CPU vs limit（cAdvisor/kubelet） | pod | high |
| `ContainerMemHigh` | 容器内存使用率 > 阈值 | cgroup mem vs limit（**扣 page cache**，沿用原口径） | pod | high |
| `ContainerNetRxHigh` | 容器入站流量 > 阈值 | container network received | pod | medium |
| `ContainerNetTxHigh` | 容器出站流量 > 阈值 | container network sent | pod | medium |

> 迁移后：原「告警策略 → 容器资源阈值」的规则**改为在 Netdata health 里配**；平台侧规则界面/数据**下线或只读留存**（待定，见 §七）。

---

## 三、新增：主机系统（resource_type = `host`）

> 平台当前**完全没有**主机级告警，全部为新增能力。

| 平台 fault_type | 触发语义 | Netdata 侧来源（模板/图表，待核实） | 默认 severity |
|---|---|---|---|
| `HostCpuHigh` | 主机 CPU 持续高 | `system.cpu` |
| `HostMemHigh` | 主机内存使用率高 | `system.ram` |
| `HostSwapHigh` | swap 使用高 | `system.swap` |
| `HostDiskUsageHigh` | 分区空间使用率高 | `disk_space`（每 fs） |
| `HostInodeHigh` | inode 使用率高 | `disk_inode` |
| `HostDiskIoHigh` | 磁盘 IO 延迟/繁忙高 | `disk`（io） |
| `HostNetErrors` | 网卡错包 / 丢包 | `net`（interface errors/drops） |
| `HostLoadHigh` | 系统负载高 | `system.load` |
| `HostConnHigh` | TCP 连接数高 | `system.tcp` / sockets |
| `HostProcessDown` | 关键进程不在 | `processes` / apps（需配目标进程） |

---

## 四、新增：主机 Docker（resource_type = `container`）

| 平台 fault_type | 触发语义 | Netdata 侧来源（模板/图表，待核实） | 默认 severity |
|---|---|---|---|
| `DockerContainerCpuHigh` | 容器 CPU 高 | cgroup cpu（per container） |
| `DockerContainerMemHigh` | 容器内存高 | cgroup mem（per container） |
| `DockerContainerOom` | 容器 OOM | cgroup oom events |
| `DockerContainerDown` | 容器退出 / 不健康 | docker container state |
| `DockerDaemonDown` | Docker 守护进程不可用 | docker daemon uptime/events | critical |

---

## 五、新增：K8s 节点 / 工作负载指标阈值（resource_type = `node`/`pod`/`deployment`）

> 这些是**指标阈值类**（合规：归 Netdata）。**事件类**（如 CrashLoop）**不在此表**（见 §六）。

| 平台 fault_type | 触发语义 | Netdata 侧来源（待核实） | resource_type | 默认 severity |
|---|---|---|---|---|
| `NodeDiskUsageHigh` | 节点磁盘使用率高 | node filesystem（kube-state-metrics） | node | high |
| `NodeCpuHigh` | 节点 CPU 高 | node cpu | node | high |
| `NodeMemHigh` | 节点内存高 | node memory | node | high |
| `PodNotReady` | Pod 长时间未就绪 | ksm pod status | pod | medium |
| `WorkloadReplicasMismatch` | 工作负载副本不达标 | ksm deployment/statefulset replicas | deployment | medium |

---

## 六、明确不接：事件型告警（留平台自研 watcher）

> 以下故障 Netdata **做不了**（K8s 事件语义），**仍由平台 `pod_watcher`/`node_watcher` 报**，
> **不进本映射表**（避免与 Netdata 指标告警重复）：

```
CrashLoopBackOff, OOMKilled, ImagePullBackOff / ErrImagePull,
PendingTimeout, TerminatingStuck, NodeNotReady,
MemoryPressure, DiskPressure, PIDPressure, NetworkUnavailable,
NodeCordoned, CreateContainerConfigError / CreateContainerError /
RunContainerError / ContainerCannotRun,
InvalidImageName / ErrImageNeverPull / ImageInspectError / RegistryUnavailable,
PodRestartOOM, PodRestartProbeFail
```

> ⚠️ **边界注意**：
> - `OOMKilled`（**事件**，来自 kubelet 上次终止原因）→ **watcher**；容器**内存使用率高**（**阈值**）→ Netdata（`ContainerMemHigh`）。
> - 容器**重启次数阈值**（指标）可归 Netdata；`CrashLoopBackOff`（**状态事件**）→ watcher。
> - 二者语义不同，**不可互替**，需在映射时严格区分。

---

## 七、severity 映射 & 兜底

**Netdata `status` → 平台 `severity` / `status`**：

| Netdata | 平台 severity | 平台 status |
|---|---|---|
| CRITICAL | high（`DockerDaemonDown`/节点故障可置 critical） | pending |
| WARNING | medium | pending |
| CLEAR | — | resolved（自动关闭） |

**未知告警兜底**：Netdata alarm 未在映射表中 → 归通用 `fault_type = NetdataUnclassified`，
`message` 保留**原始 alarm 名/图表**，便于后续补映射（**不丢弃**）。

---

## 八、实现形态建议

平台侧维护一张**映射表**（配置或 DB），入站接口按它转换：

```
webhook payload
  → 取 alarm 名（或 classification/component）
  → 查映射表 → fault_type / resource_type / severity
  → 组装平台 Alert → 写库 → 走闭环
```

映射表建议字段：`netdata_alarm` | `fault_type` | `resource_type` | `default_severity` | `enabled` | `note`。
维护入口可先放配置文件（Go/Python 常量或 YAML），后续再考虑做界面。

---

## 九、待核实事项（落地前必做）

1. **Netdata 真实 alarm 名**：用部署实例导出——
   - 父节点上：`ls /etc/netdata/health.d/ /usr/lib/netdata/conf.d/health.d/` + 看各 `alarm:` 名
   - 或 API：`GET /api/v1/alarms`（列当前告警） / `/api/v1/alarm_log`
   逐个把 §二~§五 的「Netdata 侧来源」列填成**真实 alarm 名**。
2. **K8s 侧告警是否依赖 kube-state-metrics**（本集群已知有，用于 `kube_pod_container_resource_limits`）。
3. **主机 `HostProcessDown` 需指定要盯的进程**（Netdata `apps`/`processes` 配置）。
4. **阈值口径**：迁移的 4 条要与原 `resource_alert` 阈值对齐（尤其内存要**扣 page cache**）。
5. **health 裁剪清单**：默认上百条 → 明确"只启用映射表内这些"，其余关闭。
6. **原 `resource_alert` 规则数据处置**：迁移后前端规则界面/历史告警如何保留（见需求 §七）。
