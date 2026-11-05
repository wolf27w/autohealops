# 集群管理交互升级需求文档（对齐业界同类产品）

> 状态：**需求文档**（2026-08-27 老板提出，讨论确认，待排期开发）
> 范围：集群管理页（ClusterDetail）的工作负载层级、Pod 日志查看器、容器终端的交互升级
> 目标：交互尽量向业界同类产品靠齐

---

## 一、需求背景

当前集群管理页（`frontend/src/pages/ClusterDetail.tsx`）交互较简陋：

1. **层级扁平**：`无状态 / 有状态 / 定时任务 / 容器 / Pod` 五个 Tab 平级，Pod 不是工作负载的子级。
2. **日志简陋**：日志抽屉只有「容器名 + 刷新」，无时间戳、无搜索、无自动刷新等开关。
3. **进容器是假终端**：弹窗填单条命令执行，输出追加，不是真正的交互式 shell。

老板诉求（2026-08-27）：三个需求一起上，交互向业界同类产品靠齐——「工作负载 → 独立详情页 → Pod 列表 → 日志/终端」。

---

## 二、需求一：工作负载层级调整 + 独立详情页

### 现状
集群详情页（`clusters/:id`）用 Tabs 平级展示：无状态(deployments) / 有状态(statefulsets) / 定时任务(cronjobs) / 守护进程(daemonsets) / Pod(pods)。

### 目标（对齐业界同类产品）

1. 集群详情页 Tab 只保留工作负载四类：**无状态 / 有状态 / 守护进程 / 定时任务**（移除「Pod」平级 Tab）。
2. 点击某个工作负载行 → 跳转**独立详情页**（老板 2026-08-27 明确：独立页面跳转，非 Drawer）。
3. 详情页内「**Pod 列表**」Tab 展示该工作负载下的 Pod（子级），不再与工作负载平级。

### 路由设计

```
/clusters/:id                          → 集群详情（工作负载四类 Tab）
/clusters/:id/workloads/:type/:name    → 工作负载详情页（独立页面）
```

- `:type` ∈ `deployments | statefulsets | daemonsets | cronjobs`
- 详情页结构：顶部概览（名称/命名空间/状态/副本/镜像/更新时间等）+ Tab「Pod 列表 / 事件 / YAML」
- Pod 列表每行操作：**监控 / 日志 / 终端 / YAML**（沿用现有，改造日志和终端）

### 数据接口
- 现有 `get_cluster_detail` 已返回 `deployments / statefulsets / cronjobs / daemonsets / pods`，工作负载与 Pod 的关联字段（如 Pod 的 owner/workload 名）需确认：executor 的 `ListDeploymentPods` 已支持按 Deployment 拉 Pod，详情页 Pod 列表可复用/扩展该能力，按工作负载名过滤 Pod。

---

## 三、需求二：Pod 日志查看器（带开关）

### 现状
`PodLogsDrawer`：容器名输入框 + 刷新，`getPodLogs(clusterId, ns, pod, container, tail, previous)`。

### 目标（对齐业界同类产品 日志容器）

日志面板带以下开关（数据源=容器 stdout/stderr，走 kubectl logs）：

| 能力 | 说明 |
|---|---|
| 容器下拉 | 多容器 Pod 选容器 |
| 行数选择 | 最近 N 行（默认 200） |
| 上次崩溃日志开关 | previous 开关 |
| 显示时间戳开关 | 日志行前显示/隐藏时间戳（kubectl logs 加 `--timestamps`） |
| 自动刷新/跟随开关 | 定时拉取最新 + 滚动跟随 |

> 不做：ES 关键字搜索、时间范围（老板 2026-08-27 明确：日志就是 stdout/stderr，不依赖 ES）。

### 数据源

- 日志走 `GetPodLogs`（kubectl logs），取容器 **stdout/stderr**，支持 container / tail_lines / previous / timestamps。
- 不依赖 ES，不做关键字搜索、时间范围。

---

## 四、需求三：交互式容器终端（真 shell）

### 现状
`ExecPodModal`：容器名 + 命令输入框，`execPod` 单次执行，输出追加。`ExecInPod` gRPC 是 **unary（单次命令 + 只读白名单）**，做不了交互式终端。

### 目标（对齐业界同类产品 终端）

点「终端」→ 弹出 **xterm.js 终端窗口**，直接进入容器 shell，可交互：
- 敲命令、Tab 补全、上下键历史、Ctrl+C/Ctrl+D
- 多容器可切换容器
- 关闭即断开

### 权限与安全（重要，与「诊断铁律」的边界）

- 现有 `ExecInPod` 的**只读命令白名单是针对 AI 诊断**的（诊断铁律：只读，禁增改删）。
- 交互式终端是**人工操作入口**，人是主动进去排查/处理的，**不受只读白名单限制**（等同 kubectl exec -it）。
- **shell 用 bash**（老板 2026-08-27 明确）：exec 命令为 `/bin/bash`；容器无 bash 时降级 `/bin/sh`。
- 约束：**operator 权限**（已有 require_operator）+ **命令级审计**（记录每条执行的命令留痕）。

### 技术方案（三端改造）

**K8s 交互式 exec 核心**：client-go `remotecommand` 需 stdin/stdout/stderr **双向流**。

| 端 | 改动 |
|---|---|
| executor(Go) | 新增 HTTP **WebSocket 端点** `/ws/exec`，用 `remotecommand` + 标准流对接 WS（stdin←WS、stdout/stderr→WS）；参数 cluster_id/namespace/pod/container 走 query |
| api-server(Python) | 新增 WebSocket **代理端点**（如 `/api/v1/ws/exec`），鉴权(require_operator)后反代到 executor 的 `/ws/exec`；executor 不直接暴露给前端 |
| frontend | 引入 `xterm.js` + `@xterm/addon-fit`，点「终端」→ 全屏/弹窗终端 → 连 WS → 交互 |

**备选方案**：executor 用 gRPC bidirectional stream 替代 WS，api-server 做 gRPC stream ↔ WebSocket 桥接。**推荐 WS 方案**（更简单，且 remotecommand 与 WS 天然契合）。

### 依赖
- executor 引入 WebSocket 库（如 `gorilla/websocket`）——老板已确认可加。
- 前端引入 `xterm` 相关 npm 包——老板已确认可加。

### 命令级审计（独立表）

老板 2026-08-27 明确：终端审计用**独立表**，命令级留痕。

`exec_audit_logs` 表字段设计：

| 字段 | 说明 |
|---|---|
| id | 主键 |
| session_id | 一次终端会话 UUID（同会话多条命令关联） |
| cluster_id / namespace / pod_name / container | 目标资源定位 |
| user_id / username | 操作人 |
| command | 执行的命令 |
| created_at | 记录时间 |

> 命令采集点：executor 的 `/ws/exec` 在 stdin 收到每条输入（回车分隔）时落一条；api-server 侧记录用户身份。

---

## 五、涉及服务与改动点汇总

| 需求 | executor(Go) | api-server(Python) | frontend(React) |
|---|---|---|---|
| 一、层级+详情页 | 补工作负载→Pod 关联查询 | 补工作负载详情接口 | 加独立详情页路由 + Pod 子列表，去 Pod 平级 Tab |
| 二、日志开关 | GetPodLogs 加 timestamps 支持 | 透传 | 日志面板加开关组件 |
| 三、交互式终端 | 新增 `/ws/exec` 交互式 exec | 新增 WS 代理端点 | xterm.js 终端组件 |

---

## 六、验收标准

1. **层级**：集群详情页无「Pod」平级 Tab；点有状态服务 → 进入独立详情页 → 能看到该服务下的 Pod 列表；Pod 操作（监控/日志/终端/YAML）可用。
2. **日志**：日志面板有容器下拉、行数、时间戳开关、自动刷新、previous 开关（数据源=容器 stdout/stderr，kubectl logs）。
3. **终端**：点「终端」进入 xterm 终端（bash），能交互敲命令、Tab 补全、Ctrl+C，多容器可切换，关闭断开；需 operator 权限，命令级审计留痕。

---

## 七、已确认事项 / 待办

- [x] 前端新增依赖（xterm、gorilla/websocket 等）——老板 2026-08-27 已确认可加
- [x] 需求二日志数据源：走 kubectl logs（容器 stdout/stderr），**不做** ES 搜索/时间范围
- [x] 终端 shell 用 bash（无 bash 降级 /bin/sh）
- [x] 终端审计粒度：**命令级留痕**
- [x] 终端审计存储：**独立表** `exec_audit_logs`（命令级，见需求三）
