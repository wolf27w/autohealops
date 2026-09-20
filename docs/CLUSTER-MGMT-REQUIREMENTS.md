# 集群管理功能需求文档

> 状态：**已定稿**（2026-08-27 字段采集细化完成，老板确认按文档定稿）
> 关联：`SECURITY-TASKS.md`（漏洞修复开发任务）、`ARCHITECTURE.md`（三服务架构）
> 范围：集群管理页（ClusterDetail）的容器运维能力增强

---

## 一、需求背景

当前集群管理页（`ClusterDetail.tsx`）已有：namespace 下拉、工作负载列表（无状态/有状态/定时任务/Pod）、Pod 指标监控（CPU/内存/网络趋势）。

**痛点**：日常运维最常用的容器级操作缺失——看不到 Pod 详细运行状态（重启次数/就绪/容器数）、不能直接看容器日志、不能进容器排查、不能改 Pod 配置（YAML）。运维遇到问题只能去命令行 kubectl，效率低。

**目标**：在集群管理页补齐「运行状态 + 看日志 + 进容器 + 改 YAML」四项容器运维能力，实现"网页内完成日常 Pod 运维"。

---

## 二、需求详细说明

### 需求一：Pod 状态（启动 / 等待 / 运行）

**功能描述**：Pod 列表展示完整的 Pod 生命周期状态，替代现在单一的 Running/Pending 标签。

**Pod 状态划分**（老板 2026-08-26 明确）：

| 状态分类 | 对应 K8s 状态 | 说明 |
|---|---|---|
| 🟡 启动（启动中） | ContainerCreating / PodInitializing | 容器创建中、初始化中 |
| 🟠 等待（等待） | Pending | 等待调度/资源/镜像拉取 |
| 🟢 运行（运行中） | Running | 正常运行 |
| 🔴 异常 | CrashLoopBackOff / Failed / ImagePullBackOff | 崩溃循环、失败、镜像拉取失败 |
| ⚪ 终止 | Succeeded / Terminated / Completed | 正常退出（Job/CronJob 等） |

**展示字段**：

| 字段 | 说明 | 数据来源 |
|---|---|---|
| 状态分类 | 启动/等待/运行/异常/终止（带颜色） | 已有（phase 映射） |
| 容器数 | container_count | 已有 |
| 重启次数 | restart_count（所有容器之和） | 需 executor 采集补充 |
| 就绪状态 | ready（所有容器 Ready） | 需 executor 采集补充 |

**交互**：Pod 表格直接增加「重启次数」「就绪」列；重启次数 > 0 的 Pod 用橙色/红色高亮提示异常。

**边界条件**：
- 多容器 Pod：重启次数为所有容器之和
- Pod 删除/重建后：重启次数归零，属正常

**技术方案**：
- executor 采集（ResourceSnapshot）补充 `restart_count` / `ready` 字段
- `resource_service.get_cluster_detail` 返回字段补充
- 前端 Pod 表格加列 + 异常高亮

**验收标准**：
- Pod 列表能看到每个 Pod 的状态 / 容器数 / 重启次数 / 就绪
- 重启次数 > 0 的 Pod 有明显异常标识

#### 字段采集细化（2026-08-27）

**数据来源（已具备）**：executor `GetPodStatus`（`k8s-executor/internal/executor/pod_status.go`）已结构化采集 `Phase / Ready / RestartCount / ContainerStates`，其中：
- `RestartCount` = 所有容器 `restartCount` 之和（多容器累加）
- `Ready` = 所有容器 `Ready` 且容器数 > 0

**现状缺口**：该数据当前只用于「恢复判定」（`checkRecovered`），**未落 ResourceSnapshot 快照，也未暴露给 get_cluster_detail**。

**采集方案**：
1. executor `ResourceSnapshot` 模型（`internal/database/models.go`）增加两个字段：
   - `RestartCount int`（json: `restart_count`）
   - `Ready bool`（json: `ready`）
2. `pod_watcher.go` 写快照时复用 `GetPodStatus` 的采集逻辑（或直接调 `GetPodStatus`），填 `restart_count` / `ready`
3. 采集口径对齐：`restart_count = Σ ContainerStatuses.RestartCount`；`ready = all containers ready && len(containers) > 0`

**语义与边界**：
- 重启次数 = 多容器之和（不是只看主容器）
- Pod 删除/重建后 `restartCount` 归零（K8s 原生语义，正常）
- `ready=false` 但 `phase=Running` 的 Pod（如探针未就绪）→ 显示「运行中（未就绪）」，黄色提醒
- `Pending` 无容器 → `ready=false`

**api-server 返回**：`resource_service.get_cluster_detail` 的 Pod 列表补充 `restart_count` / `ready`

**前端展示**（`ClusterDetail.tsx` Pod 表格）：
- 新增「重启次数」列：`>0` 橙色高亮，`≥5` 红色
- 新增「就绪」列：✅ / ❌（就绪 / 未就绪）
- 与状态分类列配合：异常(红) + 重启>0(橙) 双重提示

**验收**：
- 任一 Pod 能看到 `restart_count`（多容器累加正确）
- `ready` 与 `kubectl get pod` 的 READY 列一致

---

### 需求二：查看容器日志

**功能描述**：从 Pod 列表点「日志」，查看指定容器的日志。

**交互**：
1. 点「日志」→ 右侧抽屉展示日志
2. 顶部工具栏：容器下拉（多容器 Pod 可选）、最近行数、刷新按钮

**参数**：
- 容器名（下拉，空=第一个容器）
- 最近 N 行（默认 200）
- previous（是否取上一次崩溃日志，可选开关）

**展示**：等宽字体、自动换行、可滚动、可手动刷新

**技术方案**：executor 新增 `GetPodLogs` gRPC（client-go `GetLogs`，支持 container/tail/previous）；api-server 代理；前端抽屉

**边界**：多容器未指定 → 第一个容器；日志量大 → 只取最近 N 行；无日志 → 空提示

**验收**：能查看任意 Pod 指定容器的日志，支持刷新、崩溃日志（previous）

---

### 需求三：进入容器

**功能描述**：从 Pod 列表点「进容器」，在容器内执行命令排查问题。

**交互**：
1. 点「进容器」→ 弹窗（黑底终端风格）
2. 容器下拉（多容器 Pod 可选）+ 命令输入框
3. 回车或点「执行」→ 展示命令输出

**支持**：
- 容器名指定（空=第一个容器）
- 命令回车执行，可连续执行多条（输出追加）

**只读白名单**（对齐诊断铁律，禁止增改删）：ps / env / df / free / ls / cat / ss / netstat / top / wc / head / tail / du / uptime / uname / id / which / hostname / date 等

**技术方案**：复用 executor `ExecInPod`（已有只读命令白名单）；api-server 代理；前端弹窗

**安全红线**：只读白名单，禁止管道/重定向/awk/sed（命令注入防护）；需 operator 权限

**边界**：容器无 shell / 命令不在白名单 → 错误提示；多容器未指定 → 第一个容器

**验收**：能进容器执行只读命令查看进程/环境/磁盘/端口；非法命令被拦截

---

### 需求四：修改 Pod YAML

**功能描述**：查看并修改 Pod 的 YAML 配置，应用后生效。

**交互**：
1. 点「YAML」→ 弹窗加载当前 Pod YAML（等宽字体、可编辑）
2. 编辑 YAML
3. 点「应用」→ server-side apply 生效

**技术方案**：
- executor 新增 `GetPodYaml` gRPC（获取 Pod 对象序列化为 YAML，去掉 managedFields/resourceVersion 等噪音）
- executor 新增 `ApplyPodYaml` gRPC（dynamic client server-side apply）
- api-server 代理端点：`/pods/{pod}/yaml` 的 GET / PUT
- 前端：YAML 编辑弹窗

**安全红线**：
- **仅允许修改 Pod 资源**（Kind 校验，拒绝 Deployment/Service/Namespace 等）
- 应用 YAML 需 `operator` 权限
- 操作留审计

**边界条件**：
- YAML 格式非法 → 提示解析失败
- 修改了非 Pod 资源 → 拒绝并提示
- 修改了不可变字段（如 spec.nodeName）→ apply 失败返回 K8s 错误

**验收标准**：
- 能查看 Pod YAML
- 编辑后应用，配置变更生效
- 非法 YAML / 非 Pod 资源被拦截

---

## 三、涉及服务与改动点汇总

| 需求 | executor（Go） | api-server（Python） | frontend（React） |
|---|---|---|---|
| 运行状态 | 采集补 restart/ready | get_cluster_detail 补字段 | Pod 表格加列 + 高亮 |
| 日志 | 新增 GetPodLogs gRPC | 加 logs 端点 | 日志抽屉 |
| 进容器 | 复用 ExecInPod | 加 exec 端点 | 进容器弹窗 |
| 改 YAML | 新增 GetPodYaml + ApplyPodYaml | 加 yaml GET/PUT 端点 | YAML 编辑弹窗 |

---

## 四、权限矩阵

| 操作 | 权限 |
|---|---|
| 查看运行状态 / 日志 / YAML | 任意登录用户（require_any_user） |
| 进入容器执行命令 | operator |
| 应用 YAML | operator |

---

## 五、优先级建议

- P0：查看容器日志（最高频运维操作）
- P1：进入容器、修改 YAML
- P2：运行状态字段增强（重启次数/就绪）

---

## 六、备注

- 本需求已于当日（2026-08-26）按技术方案完成了一版实现（proto + Go + Python + 前端均已改动），服务已恢复运行。老板指示"代码先不修改"，该实现暂保留，按本需求文档排期后再定。
