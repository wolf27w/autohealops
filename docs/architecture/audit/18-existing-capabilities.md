# 18 · 现有能力盘点（按领域，跨报告收敛）

> 输入：`docs/architecture/audit/01~17`（约 1.05 万行）+ 复核源码（HEAD `b3b9f03`）。
> 本文只做归纳与判断，不重复报告推理过程；每行可在「备注」列的 `报告编号 §节 / 问题号` 追到原文证据。
> 判断口径：
> - **实现程度**：已实现 / 部分实现 / 仅前端 / 仅后端接口 / 逻辑不完整 / 完全未实现
> - **生产可用性**：可用（配置正确即可上生产）/ 有条件可用（能跑，但存在已知前提、绕行或安全前提未满足）/ 不可用（有代码但链路不通、被绕过或存在 P0 级缺陷）
> 统计（本文 §1~§12 表格实测）：**能力条目 204 条**。按实现程度：已实现类 **133**、部分实现 **26**、完全未实现 **38**（含 1 条"有意不实现"：kubeconfig 回显）、逻辑不完整 **3**、仅前端/仅后端/仅建表 **4**。按生产可用性：可用 **72**、有条件可用 **70**、不可用 **61**、不适用 **1**。
> 其中 **「有代码但生产不可用」= 23 条**（不可用 61 减去 38 条完全未实现），这是最值得警惕的一组：写了、能编译、有路由，但链路被绕过或存在 P0 缺陷。
> 说明：把"完全未实现"也列在表内是为了让同一领域内的"有/没有"一眼可比，不代表它有实现。

---

## 0. 一句话结论

平台的真实形态是：**「多集群 K8s 运维台 + 单机/多库运维台 + AI 诊断建议器」做得相当扎实；「多租户平台 + 强制审批执行器 + 研发闭环」还是半成品。**
前者的代码量、路由数、真实调用链都在；后者的缺口不是零星 bug，而是**模型层缺字段（project_id）、执行层缺闸门（8 条平行执行入口）、信任层缺边界（9090/9096/9099 裸端口）**。

---

## 1. 认证与用户

| 能力 | 实现程度 | 关键代码证据 | 生产可用性 | 备注 |
|---|---|---|---|---|
| 本地账号密码登录 + JWT 签发 | 已实现 | `api-server/app/api/auth.py:20-50`；`app/core/security.py:53-57` | 有条件可用 | 11 §11。前提是 `JWT_SECRET_KEY` 必须改；`install.sh:100-103` 默认复制 `.env.example` 的 `change-me`（11 AUTH-02） |
| 密码哈希 bcrypt | 已实现 | `app/core/security.py:40-50` | 可用 | 11 §4 |
| LDAP 登录（含 LDAP 注入转义） | 已实现 | `api/auth.py:64-154`；`_ldap_escape:53-61` | 有条件可用 | 11 §2。无组映射、无用户同步；且 `api-server/requirements.txt` 缺 `ldap3`，按 requirements 构建会静默降级（17 §1.2） |
| 登录防爆破（用户名维度） | 已实现 | `api/auth.py:16-17,31-43`；Redis 计数 5 次/300s | 可用 | 无 IP 维度，"5 次错误锁死任意账号"是可用性风险（11 AUTH-04） |
| LDAP 分支登录限流 | 完全未实现 | `api/auth.py:27-28`（`auth_type=="ldap"` 提前 return） | 不可用 | LDAP 可无限撞库（11 AUTH-04） |
| 登出黑名单 | 部分实现 | `api/auth.py:164-170`；`app/middleware/auth.py:29-35` | 有条件可用 | 4 条 WS 不查黑名单；Redis 故障时降级放行（11 AUTH-05） |
| 四角色 RBAC（admin/operator/viewer/developer） | 已实现 | `app/middleware/permission.py:9-25`；`api/users.py:29`；475 处 `Depends(require_*)` | 可用 | 13 §1。角色是 4 个字符串常量，不是权限模型 |
| 动态角色表（`roles` / 权限点） | 仅建表 | `app/models/user.py:34-42`；全仓 0 处 `select(Role)` | 不可用 | 11 AUTH-07。表存在但没有调用方、没有管理 API |
| 密码强度策略 | 完全未实现 | `api/users.py:32-52` 无任何校验 | 不可用 | 11 §11 |
| 默认管理员随机密码 | 已实现 | `api-server/app/main.py:30-55` | 可用 | 首次启动打印/落盘随机密码 |
| API Key 认证 | 仅后端接口（生成侧） | `api/users.py:83-91`；`models/user.py:22` | 不可用 | 全仓无 `X-API-Key` 解析、无校验分支 → 前端"重新生成 API Key"产出的 key 永远无效（11 AUTH-06） |
| 外部系统令牌鉴权 | 已实现 | `api/external_access.py:26-30` | 有条件可用 | 独立令牌，与 JWT 分离（正向） |
| 内部服务令牌（api↔ai↔executor） | 已实现 | `api/alerts.py:539-540`；`app/clients/ai_client.py:28`；`k8s-executor/internal/config/config.go:66` | 不可用 | **令牌值 = `JWT_SECRET_KEY`**，拿到任一处即可离线伪造 admin JWT（11 AUTH-01 / 14 §1.4） |
| IM 回调验签（钉钉/企微/飞书） | 已实现 | `api/webhooks.py:102-176` | 可用 | `hmac.compare_digest`，未配置密钥直接拒绝（14 §十.4） |
| 前端路由守卫 | 仅前端（弱） | `frontend/src/App.tsx:86-111`；`constants/nav.ts:113-116` | 有条件可用 | 只看 admin 门槛 + host grant；`/platform/audit`、`/platform/reports` 未加 guard（03 §1.3） |
| developer 角色的前端支持 | 完全未实现 | `frontend/src/constants/nav.ts:4` 只有 3 个角色 | 不可用 | 前后端角色枚举不一致（11 §11） |

---

## 2. 项目与资源授权

| 能力 | 实现程度 | 关键代码证据 | 生产可用性 | 备注 |
|---|---|---|---|---|
| 项目 CRUD + 关联多集群×多 namespace + 多 DB | 已实现 | `api/projects.py:141-165`；`models/project.py:16-70` | 可用 | 12 §2。`ProjectClusterNS` 无唯一约束，靠应用层判重 |
| 用户-项目-角色（一人多项目多角色，判定侧） | 已实现 | `services/user_permission_service.py:72-87`；`api/projects.py:257-266` | 有条件可用 | 12 §3。表结构与判定都通，但缺唯一索引；写入口全 `require_admin` |
| 项目管理员自助管成员 | 完全未实现 | `api/projects.py:142,201,251,276,320` 全部 `require_admin` | 不可用 | 与"项目管理员管成员"目标相反（12 §8） |
| 集群/namespace 级授权 `check_cluster_permission` | 已实现 | `services/user_permission_service.py:44-100` | 有条件可用 | 语义正确（直接授权 ∪ 项目授权取最高，权重 read=1/operate=2/manage=3），但 `namespace=""` 退化为"任意 ns 有权即可" |
| namespace 精确隔离 | 逻辑不完整 | `services/user_permission_service.py:47-70`；`api/resources.py:605-611,706-720` | 不可用 | 传空 ns 即可读全集群 Pod/CM/Secret/PVC（12 ISO-02 / 05 P0②） |
| DB 级授权 `check_db_permission` | 已实现 | `api/db_connections.py`（39 处）；`api/db_approvals.py:189-240` | 有条件可用 | 47 条 DB 路由 44 条有校验；`object-ddl`、`sql-tasks` 列表、`test` 3 条漏检（12 ISO-04） |
| 主机授权（分组 + 三级角色） | 部分实现 | `api/hosts.py:99-113` | 有条件可用 | `update_host` 允许改 `address/ssh_port/credential_id` 且不校验凭据归属 → 组 operator 可换凭据横向（07 §8） |
| 项目作为「数据分区」（`project_id` 列） | 完全未实现 | 87 个 ORM 模型中仅 3 张关联表有 `project_id`；业务表 0 | 不可用 | 12 §1。"项目"是授权代理，不是数据分区 |
| 告警/审批/执行/任务/诊断/审计的项目过滤 | 完全未实现 | `api/alerts.py:60`、`api/approvals.py:31`、`api/executions.py:38` 均 `require_any_user` 无范围条件 | 不可用 | 任何登录用户 = 全平台视图（12 §1.4 / PERM-05） |
| 集群内 K8s RBAC（impersonation 运行链） | 部分实现 | `services/rbac_service.py:40-56,120-160`；`k8sclient/manager.go:161-183` | 不可用 | 只下发 RoleBinding，`build_impersonation_groups()` 与 `ImpersonatedClient()` **全仓无调用方** → 实际仍用超管 SA 执行（06 §7.8） |
| 前端 `project_id` 作为可信输入 | 仅前端筛选 | `frontend/src/pages/ApprovalList.tsx:219`；`api/stats.py:1424-1435`（`_user` 未使用） | 不可用 | 手工构造请求可看别项目聚合指标（12 ISO-11） |

---

## 3. 集群接入与 kubeconfig 管理

| 能力 | 实现程度 | 关键代码证据 | 生产可用性 | 备注 |
|---|---|---|---|---|
| kubeconfig 导入 / 校验 / 多 context 批量导入 | 已实现 | `api/clusters.py:149-205,660-718` | 可用 | 05 §3#1。幂等键 = apiserver server URL |
| kubeconfig AES-256-GCM 加密存储（Python↔Go 互通） | 已实现 | `app/core/security.py:18-31`；`internal/k8sclient/crypto.go:22-56` | 有条件可用 | 单把静态 `ENCRYPTION_KEY`，无 KMS/无轮换；同一 key 还保护 DB 密码/主机私钥/AI key（07 §1.2 / 14 §1.4） |
| kubeconfig 回显 / 下载接口 | 完全未实现（有意） | `api/clusters.py:787-806` 只有写入路径 | 不适用 | 安全正向，04/05 均确认 |
| Agent 一键接入（签发客户端证书 + 渲染 install.yaml） | 已实现 | `api/clusters.py:212-500`；`app/agent_pki.py` | 有条件可用 | 06 §3。每次拉 install.yaml 重签一张证书，只增不减 |
| agent pull 模式（平台直连 agent） | 部分实现 | `app/clients/executor_client.py:378-464`；`k8s-executor/cmd/agent/main.go:207-220` | 有条件可用 | 推送类能力（watcher/指标/事件）在 pull 下不启用（04 §8） |
| in-cluster 自动接入 | 部分实现 | `k8s-executor/internal/bootstrap/incluster.go:33-106` | 不可用 | `k8s/` 没有平台自身 ClusterRole → 登记 healthy 但后续操作 forbidden（05 §1.8） |
| 集群健康检查 | 已实现（弱） | `internal/health/checker.go:44-58` | 有条件可用 | 只查 `ServerVersion`，权限不足也判 healthy |
| ServiceAccount / 凭据管理 | 已实现 | `api/service_accounts.py:23-31` | 有条件可用 | SA 列表是集群范围可读 |
| Agent 版本升级（走隧道改 Deployment 镜像） | 已实现 | `api/clusters.py:516-646` | 可用 | 06 §11 |
| K8s 操作以「集群超管 SA」执行 | 已实现（有意设计） | `api/clusters.py:441-467`（ClusterRole `apiGroups:*/resources:*/verbs:全套`） | 有条件可用 | 意味着**应用层漏检即等价越权**，无 K8s 侧兜底（05 §1.4） |

---

## 4. K8s 查询能力

| 能力 | 实现程度 | 关键代码证据 | 生产可用性 | 备注 |
|---|---|---|---|---|
| namespace 列表（快照 + 实时回退 + 300s 缓存） | 已实现 | `api/resources.py:181-243` | 可用 | 05 §3#8 |
| namespace 创建 / 删除 / 配额编辑 | 完全未实现 | 全仓无路由 | 不可用 | 05 §3#9。平台是"只读基础设施"定位 |
| Pod 列表 / 详情 / YAML | 已实现 | `api/resources.py:395-401,604-613,694-704` | 可用 | 无分页（大集群全量返回）（05 §3#11） |
| Pod 日志 tail（≤2000 行，30s 超时） | 已实现 | `api/resources.py:546-577`；`internal/executor/pod_ops.go:19-55` | 可用 | 05 §3#12 |
| Pod 日志实时跟随（WS / SSE） | 已实现 | `api/logs.py:151-186`；`internal/logstream/server.go:113-186` | 有条件可用 | 9099 端口无鉴权、无总超时（05 §1.8 / 14 §1.2） |
| 工作负载聚合日志 + SSE 跟随 | 已实现 | `api/workload_logs.py:225-302` | 可用 | 2s 轮询，无上限 |
| 日志整份 / 时间段下载 | 部分实现 | `api/resources.py:2830-2883` | 有条件可用 | 全量进 api-server 内存；agent 集群需新版本 agent |
| Events（实时 + 7 天历史） | 部分实现 | `internal/executor/pod_list_events.go:328-365`；`api/resources.py:615-692` | 有条件可用 | 实时分支硬编码 100 条 |
| Deployment / StatefulSet / DaemonSet / Job 列表与详情 | 已实现 | `api/resources.py`（79 路由段）；`internal/executor/workload_ops.go` | 可用 | 05 §3 全表 |
| Node 列表 / 详情 / 指标 | 已实现 | `internal/executor/node_list.go`；`api/resources.py` | 可用 | 曾因全集群 Pod list 慢，已加 10s TTL 缓存（TOOLS.md 性能口径） |
| 存储：PVC / PV / StorageClass 列表与详情 | 已实现 | `api/resources.py:705-935`；`internal/executor/misc_resources.go` | 可用 | 无创建/扩容 PVC |
| ConfigMap / Secret 查看 | 已实现 | `api/resources.py:705-935`；`misc_resources.go` | 不可用 | **Secret `data` 被 base64 解码成明文返回给 read 权限用户**（05 P0③） |
| Service / Ingress / ServiceAccount 查询 | 已实现 | `api/resources.py:705-935`；`api/service_accounts.py:23-31` | 可用 | — |
| HPA 查询 | 已实现 | `internal/executor/hpa_ops.go`；`api/resources.py:1290-1376` | 可用 | agent 侧有版本能力门控 |
| 容器列表 / 容器内文件读取 | 已实现 | `api/resources.py:1587`（容器列表）；容器内只读命令 + 路径过滤 | 有条件可用 | 05 §3#23 附近 |
| 镜像仓库 tag / 镜像解析 | 已实现 | `services/registry_service.py`；`api/resources.py` registry 路由 | 不可用 | **SSRF + 全局 registry 凭据外泄**（14 P0①：`registry_service.py:71-90`） |
| Prometheus 趋势查询（经隧道 → 9099 /prom/range 回退） | 已实现 | `services/prometheus_service.py:220-244` | 可用 | 04 §8 |
| 集群巡检报告 | 已实现 | `api/clusters.py:952-962`；`services/inspection_service` | 有条件可用 | **无集群权限校验**（05 §3#31） |

---

## 5. K8s 变更能力

| 能力 | 实现程度 | 关键代码证据 | 生产可用性 | 备注 |
|---|---|---|---|---|
| 扩缩容（含缩到 0） | 已实现 | `internal/executor/workload_ops.go:79-104` | 有条件可用 | 能执行，但**无审批、无审计**（05 §3#18） |
| 滚动重启 | 已实现 | `internal/executor/workload_ops.go:105-158` | 有条件可用 | 无审计 |
| 回滚（undo / 指定 revision） | 已实现 | `internal/executor/workload_ops.go:166-232`；`rollback.go` | 可用 | 仅 Deployment；这一类**有审计**（05 §3#19） |
| 镜像更新（读改写） | 已实现 | `api/resources.py:1487-1524` | 有条件可用 | 无审计、无审批 |
| YAML apply（工作负载 / 任意对象） | 已实现 | `api/resources.py:754-760`；`internal/executor/pod_list_events.go:467-469` | 不可用 | 鉴权用请求体 namespace，执行用 YAML 内 namespace → 越权写别的 ns（05 P0①/ PERM-04） |
| 删除资源（Pod/Deployment/任意 kind） | 已实现 | `internal/executor/execute.go:557`（`execDelete`）；`DeleteResource` RPC | 不可用 | 无审批；且 9090/9096 无鉴权可直达（05 §1.6 / 10 §1.1） |
| Pod 内执行单命令 | 部分实现 | `internal/executor/exec_in_pod.go:22-60` | 不可用 | 注释称"只读白名单"，实际 `exec_in_pod.go:54` 走任意二进制（05 §1.7/#23） |
| 交互式容器终端（全 shell） | 已实现 | `api/terminal.py:38-99`；`internal/executor/exec_stream.go:68-140` | 有条件可用 | 能用，但**无录像、无命令审计**（07 §10 对照组） |
| 节点 cordon / uncordon / label / taint / isolate | 已实现 | `api/resources.py:2453-2737`；`execute.go:439-470` | 可用 | 有审计；只校验"任意 ns 有权限" |
| 节点 drain（PDB 感知） | 已实现 | `execute.go:646-704`；`api/resources.py:2815-2829` | 有条件可用 | 无审计，只有 `confirm` 参数 |
| HPA 创建 / 修改 / 删除 | 已实现 | `internal/executor/hpa_ops.go`；`api/resources.py:1290-1376` | 可用 | agent 版本门控 |
| 驱逐 Pod 清理 | 已实现 | `internal/executor/pod_evicted.go:12` | 可用 | 有去重/节流判定与审计 |
| 灰度 / 金丝雀 / 流量切分 | 完全未实现 | `gray_release.go:1-16` 实为"修复计划按影响面分批 + 熔断" | 不可用 | 前端也无入口（05 §1.7/#21） |
| 批量跨集群执行 | 逻辑不完整 | `services/batch_service.py:42-125`；`api/batch.py:14-31` | 不可用 | 无审批 / 无集群授权 / 无审计，失败策略只有 continue（10 §11 / PERM-03） |
| 定时任务执行任意脚本 | 已实现（危险） | `api/custom_tasks.py`；`services/scheduler_service.py:82-83`；`internal/script/*.go:53` | 不可用 | 任意 `cluster_id` + `script_content` 以 `/bin/sh -c` 起 Job，不校验授权/不审批（PERM-02） |

---

## 6. Agent 网关

| 能力 | 实现程度 | 关键代码证据 | 生产可用性 | 备注 |
|---|---|---|---|---|
| gRPC HTTP/2 双向流隧道（push，mTLS） | 已实现 | `app/agent_gateway.py:252-350`；`k8s-executor/cmd/agent/main.go:242-453`；`proto/agent.proto:11-13` | 可用 | 06 §2。不是 WebSocket、不是轮询 |
| 注册（token sha256 比对） | 已实现 | `app/agent_gateway.py:44-57` | 有条件可用 | 无失败计数/封禁；token 明文写在 agent Deployment args（`clusters.py:427`）→ 集群内可读（06 P0②） |
| mTLS 客户端证书校验 | 已实现 | `app/agent_gateway.py:372-404`；`app/agent_pki.py:140-169` | 有条件可用 | 只做链校验，**证书不与 cluster_id 绑定**（无 CN 比对） |
| CA / 证书管理面（签发/吊销/CRL/轮换） | 完全未实现 | `agent_pki.py` 全文；`api/clusters.py:648-659` 只清 token 哈希 | 不可用 | 1825 天有效、无 CRL/OCSP/到期提醒；revoke 不回收证书、不踢连接 |
| 应用层心跳（ping/pong） | 完全未实现（死代码） | `agent_gateway.py:318-319`；`main.go:352-353` | 不可用 | 实际靠 gRPC keepalive（30s/10s） |
| `agent_last_seen_at` 持续更新 | 完全未实现 | `agent_gateway.py:51` 是唯一写点 | 不可用 | 语义其实是"最后注册时间"，不能当在线判据 |
| 掉线判定 | 已实现 | `agent_gateway.py:338-348`；`main.go:181-231` | 可用 | disconnected 后不再探活 |
| 断线重连 | 已实现 | `main.go:229-239`（固定 3s） | 可用 | 无退避抖动 |
| 任务下发 / 结果回传（req_id 关联） | 已实现 | `agent_gateway.py:135-153,303-310` | 有条件可用 | 单连接、单 RPC 串行 |
| 任务取消 / 超时中止 | 完全未实现 | `agent_gateway.py:147-150` 只 pop pending | 不可用 | 超时不通知 agent，慢调用会继续把写操作做完（06 §1.6） |
| 幂等 / 去重 | 完全未实现 | 全链路无幂等键 | 不可用 | 重试 = 二次执行 |
| 每 agent 并发控制 | 完全未实现（隐式串行） | `main.go:362-365` 在收包主循环同步执行 | 不可用 | 一个 30s 慢调用阻塞该集群后续全部请求 |
| 队列上限 / 背压 | 完全未实现 | `agent_gateway.py:92` 无界 `queue.Queue` | 不可用 | 内存增长风险 |
| 能力协商 | 部分实现 | `app/clients/executor_client.py:183-233`；`main.go:52-66` | 有条件可用 | remote 模式返回 None（`executor_client.py:186-189`） |
| 网关独立部署（remote 模式） | 部分实现 | `app/agent_gateway_main.py`；`app/agent_gateway_service.py` | 不可用 | 双向流未实现（`executor_client.py:146-150` 直接抛错），多处仍直连 inproc 模块（04 ARCH-02） |
| agent 侧操作审计（谁执行了什么） | 完全未实现 | `agent_gateway.py:142` 只记 cluster+method | 不可用 | 无 user_id（06 §11） |

---

## 7. 主机 / VM

| 能力 | 实现程度 | 关键代码证据 | 生产可用性 | 备注 |
|---|---|---|---|---|
| 主机 CRUD / 分组 / 标签 | 已实现 | `api/hosts.py:98-315`；`api/host_groups.py` | 可用 | 删未分组主机无二次确认 |
| 凭据托管（私钥/密码，AES-256-GCM） | 已实现 | `api/host_credentials.py`；`services/host_credential_service.py` | 可用 | 私钥字段 2000 字符上限 |
| 凭据回显防护（只回 `has_*`） | 已实现 | `services/host_credential_service.py:19-32` | 可用 | 14 §十.6 |
| 直连 SSH 采集（进程） | 已实现 | `services/host_monitor_service.py:33-89` | 有条件可用 | 每 60s 一次 `ps`；**不写 `last_metrics`** → 前端"最近指标"永远为空 |
| agent 侧代采（进程） | 已实现 | `internal/hostagent/hostagent.go`；`services/agent_alert_service.py:268-331` | 有条件可用 | 单私钥、不校验 host key（`InsecureIgnoreHostKey`） |
| 主机级指标（CPU/内存/磁盘/网络/负载） | 完全未实现 | 无 `iostat/df/free/uptime` 采集 | 不可用 | 07 §4 |
| 服务管理（systemctl start/stop/status） | 完全未实现 | 无相关代码 | 不可用 | 07 §5 |
| 日志查询（journalctl / tail） | 完全未实现 | 无相关代码 | 不可用 | 07 §5 |
| 非交互一次性命令（拿 stdout/exit_code） | 完全未实现 | 无相关 API | 不可用 | 只有交互终端，导致 AI 无法在主机上取证 |
| Web 交互终端 | 已实现 | `api/host_terminal.py:143-330` | 可用 | 单用户 5 并发、10min 空闲超时、可踢下线 |
| 会话录制（DB chunks，gzip） | 已实现 | `api/host_session_service.py`；`models/host_session.py:43-54` | 有条件可用 | **用户可用 `record=0` 关掉录制**（`host_terminal.py:165`） |
| 会话录制（asciinema cast v2 文件） | 已实现 | `services/host_session_record_service.py:43-96` | 有条件可用 | 双写无一致性校验、无篡改检测 |
| 会话回放 | 已实现 | `frontend/src/pages/HostSessionReplay.tsx`；`api/host_terminal.py:455-500` | 可用 | 仅 admin |
| 命令审计（行级） | 部分实现 | `api/host_terminal.py:246-267`；`api/host_audits.py` | 有条件可用 | 无 exit_code、不关联输出；`↑` 历史回溯与 `!!` 展开可绕过（07 §6） |
| 危险命令拦截 | 部分实现 | `services/command_guard.py:18-39`（12 条正则黑名单：10 高危 + 2 中危） | 有条件可用 | 挡不住 `find / -delete`、`systemctl poweroff`、解释器内命令、换行拆分 |
| 命令审计 CSV 导出 | 已实现 | `api/host_audits.py:103-134` | 可用 | 仅 admin |
| SFTP 浏览 / 下载 / 上传 | 已实现 | `api/host_terminal.py:529-648` | 有条件可用 | 200MB 上限；默认白名单含 `/home` → 可读 `/home/*/.ssh/id_rsa`（07 §9） |
| SFTP 在线编辑 / 删除 / 建目录 | 已实现 | `api/host_terminal.py:668-760` | 有条件可用 | ≤1MB 编辑；无审批 |
| 文件操作审计 | 部分实现 | `api/host_terminal.py:588-595,643-646` | 有条件可用 | 记 sha256，但只进 `audit_records` 摘要 |
| agent 纳管主机的终端 / SFTP | 完全未实现（明确拒绝） | `api/host_terminal.py:154-156,523` | 不可用 | 产品缺口：agent 集群里的主机只能采集不能用 |
| 跳板机 / ProxyJump / 端口转发 | 完全未实现 | 无相关代码 | 不可用 | 07 §2 |
| SSH 主机密钥校验 | 完全未实现 | `services/host_ssh.py:64`（`known_hosts=None`）；`hostagent.go:127` | 不可用 | 可被同网段 MITM |
| VM 生命周期（创建/快照/迁移/销毁） | 完全未实现 | `models/host.py:39` 只有 `kind` 字段（host/vm 标签）；全仓无 vmware/proxmox/libvirt | 不可用 | 平台只"把 VM 当 SSH 主机纳管"（03 §1.1） |

---

## 8. 数据库

| 能力 | 实现程度 | 关键代码证据 | 生产可用性 | 备注 |
|---|---|---|---|---|
| 数据源管理（CRUD / 分组 / 环境标签） | 已实现 | `api/db_connections.py:287-341,1549-1612` | 可用 | admin 建、operator 可管 |
| 连接密码加密存储 / 不回显 | 已实现 | `models/db_connection.py:25-28`；`api/db_connections.py:59-65` | 可用 | 单密钥全平台共用 |
| 多方言支持（MySQL / PG / Oracle 直连） | 已实现 | `services/dialects/`；`internal/agentdb/dialect.go` | 可用 | agent 侧 DDL/备份/导入实际只支持 MySQL（08 §1.1） |
| 独立只读账号 | 部分实现 | `dialects/mysql.py:41-44`；`pg.py:76-80`；`oracle.py:76-80` | 有条件可用 | **未配置只读账号时静默回落主账号**；慢 SQL 摘要采集固定用主账号 |
| 只读查询（首词白名单） | 部分实现 | `api/db_connections.py:344-400`；`internal/agentdb/query.go:35-120` | 有条件可用 | 只是 SQL 首词白名单，包一层 `/* */` 或 `WITH` 即可绕过语义 |
| 数据库级只读（只读事务 / 会话） | 完全未实现 | grep `SET SESSION TRANSACTION READ ONLY` / `sql_safe_updates` 0 命中 | 不可用 | 08 §1.4 |
| SQL 执行（DML/DDL，admin + confirm） | 已实现 | `api/db_connections.py:1312-1349` | 有条件可用 | 有 428 审核信封（`db_connections.py:421-425`），但审核可自批 |
| 数据库审核单（策略 / 审批 / 定时执行） | 已实现 | `api/db_approvals.py:53-336`；`services/db_approval_service.py:41-279` | 不可用 | `kind`/`action` 由请求方自填不与 SQL 校验；申请人可自批（08 DB-01/02 / PERM-07） |
| 审核单结果一次性下载 | 已实现 | `api/db_approvals.py:319-336`；`db_approval_service.py:195-248` | 可用 | token 24h、一次性 |
| 表结构设计器（改表 / 改库字符集） | 已实现 | `api/db_connections.py:717-830` | 可用 | 标识符/类型白名单校验 |
| 库/表/数据导出（SQL 文本） | 已实现 | `api/db_connections.py:832-877`；`internal/agentdb/objects.go:142-268` | 可用 | 单表 ≤5000 行 |
| 整库备份 | 已实现 | `api/db_connections.py:1242-1310`；`internal/agentdb/backup.go:20-85` | 有条件可用 | 无审批、无审计 |
| 导入（SQL / CSV / 分片 / import-sql） | 已实现 | `api/db_connections.py:878-1077,1119-1241` | 有条件可用 | 审核覆盖不全（4 条写入口只有 3 条读策略） |
| 查询结果 CSV 下载 | 已实现 | `api/db_connections.py:1350-1401,1932-1985` | 有条件可用 | 两通道策略不一致 |
| SQL 定时任务（cron + 邮件） | 已实现 | `services/sql_task_service.py:59-320` | 有条件可用 | 无审核、可任意外发邮件 |
| 慢 SQL 摘要采集（digest） | 已实现 | `services/db_collector.py:83-280` | 可用 | 默认 5 分钟一轮 |
| 慢 SQL 明细采集（history_long） | 已实现 | `services/db_slow_event_service.py:366-452` | 有条件可用 | 每 30s 一轮 + 额外 information_schema 探测，常态开销明确（08 §1.8） |
| 慢 SQL 诊断 / 优化建议（EXPLAIN + AI） | 已实现 | `services/sql_optimize_service.py:462-655`；`db_diagnosis_service.py:83-160` | 可用 | 只出方案不执行（正向） |
| 数据库健康指标采集与异常判定 | 已实现 | `services/db_collector.py:487-1190`；`db_anomaly_service.py:74-260` | 可用 | agent / 直连双口径 |
| Oracle Instant Client 管理 | 已实现 | `api/oracle_clients.py:23-130` | 有条件可用 | admin 可配置自定义下载 URL |
| 数据库操作审计（哈希链） | 部分实现 | `api/db_connections.py:481-499` | 有条件可用 | 建库/删库/备份/对象导出/审核单执行都不写审计；SQL 只留前 500 字符 |
| 语句级超时 / 资源组限制 | 完全未实现 | `dialects/mysql.py:381-392`（无 timeout）；PG 无 `command_timeout` | 不可用 | 直连路径的 `timeout` 参数被丢弃，大查询可挂满线程 |
| 结果集服务端 LIMIT | 逻辑不完整 | `mysql.py:381-392` 默认缓冲游标 + `fetchmany(limit)` | 不可用 | 整份结果集先读进 api-server 内存 |

---

## 9. AI 诊断

| 能力 | 实现程度 | 关键代码证据 | 生产可用性 | 备注 |
|---|---|---|---|---|
| ReAct 多轮自主调查（decide→tool→observe） | 已实现 | `api-server/app/services/agent_loop.py:741-857` | 可用 | 默认 8 轮 / 硬上限 16 轮 / 90s；启动失败等场景放大（09 §1.1） |
| 会话式深诊多轮取证 | 已实现 | `ai-service/app/services/agent_service.py:355-470` | 可用 | 12 步 / 24 轮 |
| 工具集（22 个注册工具） | 已实现 | `api-server/app/services/tool_registry.py:1301-1760`（`_register` 22 次） | 可用 | exec_in_pod / describe_pod / search_logs / 证据包 / 指标 / DB 健康 / RAG / HPA 等 |
| 工具权限闸门（`allow_operate`） | 已实现 | `services/tool_registry.py:1817-1825`；`execute_tool` | 有条件可用 | 只在 `execute_tool()` 生效；MCP 与部分旁路不走它 |
| 工具结果回灌 + 双重截断 | 已实现 | `agent_loop.py:229-268,796-819`；`agent_service.py:16-24` | 可用 | 单条 2500 字、最近 8 条（ai-service 侧 1200 字） |
| 规则 / 确定性结论兜底 | 已实现 | `agent_loop.py:395-760`；`agent_service.py:139-177` | 可用 | 预算耗尽/命中特征时零 AI 调用出结论 |
| 证据幻觉护栏 | 部分实现 | `services/diagnosis_evidence_guard.py:59-85` | 有条件可用 | 只校验引号内引文与异常类名；命中只打标 + 置信度置 0，不硬阻断（09 §1.4） |
| 探针护栏（exitCode 校正） | 已实现 | `ai-service/app/services/diagnosis_service.py:511-536` | 可用 | — |
| 根因 / 方案 / 修复验证（`verify-repair`） | 已实现 | `ai-service/app/api/core_api.py:43,53`；`plan_service.py` | 可用 | 验证是"指标/状态复核"，不是业务回归 |
| Prompt 版本 / 灰度 / 回滚 | 已实现 | `ai-service/app/services/prompt_engine.py:97-118,283-330`；`prompt_health.py` | 可用 | 09 §4 |
| Prompt 模板 / 片段 / 路由 / dry-run / 版本 diff | 已实现 | `ai-service/app/api/aux_api.py:441-765`（20+ 路由） | 可用 | 平台里少见的"有完整管理面"的模块 |
| Prompt 注入硬隔离 | 部分实现 | `diagnosis_service.py:17-52`（软声明 + 清洗） | 有条件可用 | 09 §4 |
| RAG 检索 / 去重 / 降级 | 已实现 | `ai-service/app/services/rag_service.py:66-120,228-320` | 有条件可用 | embedding 依赖外部 API，未配置时 503（`ai-service/app/main.py:52-55`） |
| 知识库条目管理（启停/导入导出） | 已实现 | `api/knowledge.py`（10 路由）；`services/knowledge_service.py` | 可用 | — |
| 模型降级链 / 成本统计 / SSRF 防护 | 已实现 | `ai-service/app/services/model_adapter.py:86-290` | 可用 | severity 分级选模型，`priority` 排序降级（03 §3） |
| 修复动作走审批（不直执） | 已实现 | `services/decision_engine.py:282-314`；`autonomy_gate.py:99-169` | 有条件可用 | 非 prod 默认 `full` 全自动（10 §1.8），prod 才走审批 |
| MCP 工具调用（JSON-RPC） | 已实现 | `api/mcp.py:53-118` | 不可用 | `tools/call` 直接调 handler，**绕过 `allow_operate` 与审批** → viewer 可执行 operate 工具（09 §1.6 / PERM-01） |
| LLM 流式输出 | 完全未实现 | `model_deepseek.py:24-47` 等 | 不可用 | 前端只能等整段 |
| 单告警成本上限 | 完全未实现 | `model_adapter.py:219-221` | 不可用 | 有成本统计，无预算闸门 |
| 前端调查过程回放（真实后端事件） | 已实现 | `AgentChat.tsx:317-324`；`AgentTrace.tsx` | 可用 | 09 §4 |

---

## 10. 处置与执行

| 能力 | 实现程度 | 关键代码证据 | 生产可用性 | 备注 |
|---|---|---|---|---|
| 动作注册表（6 个平台动作） | 已实现 | `services/action_registry.py:29,39,49,59,69,79` | 可用 | rebuild_pod / delete_stuck_pod / rolling_restart / rollback_deployment / fix_image_pull_secret / drain_traffic，每个带 risk/verify/rollback_note |
| 决策引擎（评分 → 选动作） | 已实现 | `services/decision_engine.py`；`decision_score.py` | 可用 | 有单测（16 §5.1） |
| autonomy_gate 三条件闸门 | 已实现 | `services/autonomy_gate.py:107-225` | 有条件可用 | full 模式几乎全放行（10 §1.8） |
| 审批单（单人 / 双人） | 已实现 | `api/approvals.py:17-28`；`services/approval_service.py:350-412` | 有条件可用 | 模型无申请人字段 → 无法做职责分离（10 §1.3） |
| 审批流自定义（节点 / 定时提醒 / group_all） | 已实现 | `api/approval_flows.py:87-162`；`approval_service.py:79-137` | 可用 | 无"超时终态" |
| 平台内审批 / 批量审批 | 已实现 | `api/approvals.py:120-134,176-194` | 可用 | 批量逐条开事务 |
| 加签 / 转办 | 已实现 | `approval_service.py:498-549` | 不可用 | **不校验操作人是否为当前审批人**，任何 operator 可自加签再批准（10 §1.2） |
| 审批 → 自动执行 | 已实现 | `approval_service.py:851-1034` | 有条件可用 | plan_id 弱绑定；不拦 `cancelled`；无补偿 |
| 定时执行 | 已实现 | `approval_service.py:481-497` | 有条件可用 | 到点不校验告警/计划有效性 |
| 审批超时升级 | 部分实现 | `approval_service.py:726-849` | 有条件可用 | 只推进提醒，无终态（不会自动拒绝/关闭） |
| 执行结果验证 | 部分实现 | `services/action_verify.py:54-94`；`approval_service.py:956-1016` | 有条件可用 | `plan_runner` 不回传步骤失败 → 全失败也可能置 completed；无指标直接关单标"未验证"（10 §1.5） |
| 执行明细（步骤 / 日志） | 部分实现 | `api/executions.py:33-56` | 有条件可用 | 平台动作步骤不落 `execution_logs` |
| 一键回滚修复 | 已实现 | `api/executions.py:59-114` | 不可用 | 无审批、无集群权限校验 → 可回滚别人项目（12 ISO-03） |
| AI 处置台（候选动作 + 一键执行） | 已实现 | `services/alert_action_service.py:50-97,183-267` | 有条件可用 | 判定与执行同源；未校验集群权限 |
| 告警 auto 直执 / approve 建单 | 已实现 | `api/alert_actions.py`（3 路由）；`services/alert_auto_service.py` | 有条件可用 | 非 prod 默认 full 直执 |
| 操作审计（哈希链） | 部分实现 | `services/audit_service.py:31-33,116-140` | 不可用 | **`update_audit_record` 只重算本行 hash，不更新后续行 previous_hash → 任何一次更新后 verify-chain 必失败**（10 §1.6） |
| 审计覆盖面（写操作） | 部分实现 | `create_audit_record` 全仓 25 处 vs 285 个写路由 | 不可用 | batch / alert-actions / custom-tasks / MCP / 多数资源变更端点完全不写（13 §1.10） |
| 审计删除保护 | 已实现 | `api/audit.py:11-31` | 可用 | 应用层无删除接口 |
| 审计检索（trace_id / 全文） | 已实现 | `api/audit.py:12,20`；`audit/search` | 有条件可用 | 用 `require_any_user`，无租户/项目过滤（12 §1.4） |
| executor gRPC 鉴权 | 完全未实现 | `k8s-executor/cmd/executor/main.go:95-109`（裸 `grpc.NewServer()` + reflection） | 不可用 | 任意能连 9090 的人 = 被管集群 cluster-admin；compose 把 9090 发布到所有网卡（10 §1.7 / 14 P0②） |
| 内部网关（9096）鉴权 | 完全未实现 | `app/agent_gateway_service.py:68-83`（含 `IssueClientCert`） | 不可用 | 匿名可为任意 cluster_id 签证书 + 下发任意 RPC（06 P0①） |

---

## 11. 告警与工单

| 能力 | 实现程度 | 关键代码证据 | 生产可用性 | 备注 |
|---|---|---|---|---|
| Pod / Node 故障 watcher（executor 侧 + agent 侧） | 已实现 | `internal/watcher/pod_watcher.go`/`node_watcher.go`；`cmd/agent/main.go:305-330` | 可用 | 全 ns 监听，无用户维度；executor 是第二个 DB 写者（04 §1.5） |
| 告警去重 / 聚合 / 限流 | 已实现 | `services/alert_merge.py`；`alert_grouping.py`；`alert_auto_service.py` | 可用 | 5 分钟窗口（README:46） |
| 告警列表 / 详情 / 诊断 / 处置 | 已实现 | `api/alerts.py:60-640`（14 路由）；`api/diagnostic.py` | 有条件可用 | 无租户隔离 → 任意登录用户看全平台告警（12 ISO-01） |
| 告警策略（policy） | 已实现 | `api/alert_policies.py:22-65` | 可用 | 全局对象，不能按项目配 |
| 静默 / 抑制（silence / inhibition） | 已实现 | `api/alert_silences.py:57-291` | 可用 | 有 preview 接口（正向） |
| 告警指派 / ack / 协作评论 | 已实现 | `api/alerts.py:468-515` | 可用 | — |
| 批量解决 / 恢复通知 | 已实现 | `api/alerts.py:308,547` | 可用 | — |
| 告警 → 工单 | 已实现 | `api/tickets.py:50`；`services/ticket_service.py` | 可用 | — |
| 审批 → 工单 | 已实现 | `api/tickets.py:61` | 可用 | — |
| 工单流转 / 指派 / 评论 / 关闭前复查故障恢复 | 已实现 | `api/tickets.py:72-127`；`ticket_service.py` | 可用 | 工单是隔离做得最好的域（`ticket_service.py:221-238`） |
| 工单统计 | 已实现 | `api/tickets.py:33` | 可用 | — |
| 通知三通道（钉钉 / 企微 / 飞书）+ 限频重试 | 已实现 | `services/notification_service.py`；`feishu_service.py`；`long_conn_client.py` | 有条件可用 | `dingtalk-stream` / `lark-oapi` 未写进 requirements → 长连接能力可能静默降级（17 §1.2） |
| 值班 / 排班 / on-call 轮转 | 完全未实现 | 全仓无 oncall/值班/shift 相关代码 | 不可用 | 告警只能"指派给某人"，没有值班表 |
| 告警升级链（N 分钟未响应升级） | 完全未实现 | 只有审批超时升级，无告警升级 | 不可用 | — |

---

## 12. 平台工程

| 能力 | 实现程度 | 关键代码证据 | 生产可用性 | 备注 |
|---|---|---|---|---|
| 单一 API 出口（/api/v1，前端 nginx 反代） | 已实现 | `frontend/nginx.conf:14-19`；`k8s/ingress.yaml:9-25` | 可用 | 04 §8 |
| 多租户隔离（统一中间件） | 部分实现 | 17/66 路由模块用资源级授权；`check_cluster_permission` 42 处、`check_db_permission` 39 处 | 不可用 | 无统一装饰器，靠 handler 手写 `if denied := await _deny_cluster(...)`（13 §1.3） |
| 操作审计（全链路） | 部分实现 | 25/285 写路由；`audit_records` 无 operator/action/target/result 结构化字段 | 不可用 | 10 §1.6 / 13 §1.10 |
| 平台自监控 `/metrics`（Prometheus 文本） | 已实现 | `api-server/app/main.py:631-640` | 有条件可用 | **无鉴权**（注释自认，供监控抓取）；无 OpenTelemetry/链路追踪 |
| 平台健康度 / 大屏 SSE | 已实现 | `api/stats.py:1423-1505`；`api/screens.py:26-73`；`screen_bus.py` | 可用 | 大屏是静态 HTML iframe |
| 仪表盘缓存实时失效 | 已实现 | `app/core/redis_client.py:88-150`；`core/orm_hooks.py` | 可用 | 04 §8 |
| 数据库迁移链健康度 | 已实现 | `api-server/alembic/versions` 123 个 revision，单 head `r81a1b2c3d4e5` | 可用 | 01 §1.6，无分叉 |
| 部署：Compose（4 服务 + 中间件） | 已实现 | `docker-compose.yml`；`start.sh:97-116` | 有条件可用 | **compose 与代码不同步**：9099 未暴露/未注入 → Docker 下实时日志跟随指向容器内 localhost（01 OPS-01） |
| 部署：K8s 清单（ingress/deployments/configmap/networkpolicy/backup cronjob） | 已实现 | `k8s/*.yaml` | 有条件可用 | NetworkPolicy 只保护 executor 与 ai-service，**不保护 agent-gateway(9095/9096) 与 api-server(8080)**（14 §1.3） |
| 容器镜像硬化（非 root / digest 固定 / securityContext） | 完全未实现 | 4 个 Dockerfile 均无 `USER`；`grep securityContext k8s/*.yaml` = 0；6 个自建镜像全是 `:latest` | 不可用 | 仅 `agent/Dockerfile:53` 用了 `USER 1001`（17 §1.4/1.5） |
| 后端单测 | 已实现 | `api-server` 1,190 用例全过（1 skipped）；`ai-service` 100 全过；Go 25 passed / 3 skipped | 可用 | 16 §1.1-1.2 |
| 核心链路单测（审批/审计/验证/RBAC/DB审批） | 完全未实现 | 5 个模块 0 引用：`approval_service.py`、`audit_service.py`、`rbac_service.py`、`db_approval_service.py`、`action_verify.py` | 不可用 | 16 §5.1 |
| 前端测试 | 完全未实现 | `frontend/package.json:6-10` 只有 dev/build/preview | 不可用 | 147 个 ts/tsx、35k 行，0 测试 |
| CI（Python 单测 / Go 单测 / 覆盖率门禁） | 部分实现 | `scripts/ci_check.sh` 第 5/8 步只跑两个 Python unittest | 有条件可用 | **不跑 `go test`**，28 个 Go 用例在 CI 从未执行；无覆盖率门禁（16 §1.6） |
| 依赖版本锁定 | 已实现 | Python `==`（18/19 行）；`go.sum` 208 行；`package-lock.json` v3 | 有条件可用 | 但 `api-server/requirements.txt` 缺 `ldap3`/`dingtalk-stream`/`lark-oapi`；Go 1.26.6 vs Dockerfile 1.22（17 §1.2/1.3） |
| API 契约层（schemas / response_model） | 完全未实现 | `response_model` 0 处、`Body(...)` 0 处、210 处 `payload: dict` | 不可用 | OpenAPI 只能覆盖路径/方法，覆盖不到字段（03 §1.4 / 15 §1.8） |
| HA / leader 选举 / 多副本安全 | 完全未实现 | 全仓无 election 代码；`k8s-executor` Recreate 单副本、`agent-gateway` 单副本 + RWO CA PVC；inproc 模式下 `_agents` 是进程内全局字典 | 不可用 | 04 §1.8/§9。api-server 双副本时 inproc 隧道表不共享 |

---

## 13. 「真实可用」vs「名义存在」对照表

> 判定标准：**真实可用** = 有 UI/接口 + 有真实后端逻辑 + 链路能跑通 + 不依赖未满足的前提。**名义存在** = 有路由/按钮/表/注释，但链路断、被绕过、或只有一半。

| # | 能力 | 对外呈现 | 实际情况 | 判定 | 证据 |
|---|---|---|---|---|---|
| 1 | API Key 认证 | 用户页有"生成/重新生成 API Key"按钮 | 只生成落库，**全仓无任何 `X-API-Key` 校验** → key 永远无效 | 有界面但链路不通 | `api/users.py:83-91`；`models/user.py:22`（11 AUTH-06） |
| 2 | 云原生 RBAC / impersonation | 集群详情显示"RBAC 已接入 / impersonation 启用" | 只下发 RoleBinding，`build_impersonation_groups()` 与 `ImpersonatedClient()` **无调用方**，实际仍用超管 SA | 有界面但链路不通（且误导） | `services/rbac_service.py:40-56`；`k8sclient/manager.go:161-183`（06 §7.8） |
| 3 | 灰度 / 金丝雀发布 | 有 `gray_release.go` 文件名 | 实为"修复计划按影响面分批执行 + 熔断"，与流量切分无关，前端无入口 | 名义存在 | `internal/executor/gray_release.go:1-16`（05 §1.7） |
| 4 | Agent 网关独立部署（remote 模式） | 有独立 `agent-gateway` 服务、9095/9096 | 双向流未实现（直接抛错）、能力协商返回 None、多处仍直连 inproc | 有接口但无闭环 | `clients/executor_client.py:146-150,186-189`（04 ARCH-02） |
| 5 | in-cluster 自动接入 | 集群登记为 connected/healthy | `k8s/` 无平台自身 ClusterRole，后续操作全部 forbidden | 有接口但不可用 | `bootstrap/incluster.go:33-106`（05 §3#6） |
| 6 | 动态角色 / 权限点管理 | `roles` 表、迁移都在 | 全仓 0 处查询、无管理 API，角色实际是 4 个字符串常量 | 有表无逻辑 | `models/user.py:34-42`（11 AUTH-07） |
| 7 | 审计防篡改（哈希链） | 有 `/audit/verify-chain`（admin） | `update_audit_record` 断链：任何一次更新后 verify-chain 必失败 | 有接口但结论不可信 | `services/audit_service.py:76-138`（10 §1.6） |
| 8 | MCP 写操作审批 | `mcp.py` 注释称"写操作需审批" | `tools/call` 直接调 handler，绕过 `allow_operate` 与审批，viewer 可执行 operate 工具 | 注释有、实现无 | `api/mcp.py:78-108`（09 §1.6 / PERM-01） |
| 9 | VM 管理 | 主机页有 `kind=vm` 标记、菜单叫"主机/VM" | 无创建/快照/迁移/销毁，只是把 VM 当 SSH 主机纳管 | 名义存在 | `models/host.py:39`；全仓无 vmware/proxmox/libvirt（03 §1.1） |
| 10 | Agent 应用层心跳 | 协议里有 `ping`/`pong` 帧 | 平台侧无发送点、agent 侧应答是死代码，`last_seen` 只在注册时写一次 | 协议有、实现无 | `agent_gateway.py:318-319`；`main.go:352-353`（06 §1.4） |
| 11 | "只读"工具（exec_in_pod） | 注释称"只读白名单" | 实际走任意二进制，`sed/apt` 都能改容器 | 注释有、实现无 | `internal/executor/exec_in_pod.go:22-60`（05 §3#23） |
| 12 | 数据库"只读查询" | 有独立只读账号配置项 | 未配置时**静默回落主账号**；只读限制只是 SQL 首词白名单 | 名义只读 | `dialects/mysql.py:41-44`；`pg.py:76-80`（08 §1.4） |
| 13 | Events「7 天历史」 | 详情页有 Events 页签 | 实时分支硬编码 100 条；历史依赖落库且不完整 | 部分存在 | `pod_list_events.go:328-365`（05 §3#16） |
| 14 | 审批超时「升级」 | 有 escalation 字段与定时任务 | 只推进提醒，无终态（不会自动拒绝/关闭） | 半实现 | `approval_service.py:726-849`（10 §10） |

**对外宣传风险最高的 5 条**：#1 API Key、#2 impersonation、#7 审计防篡改、#8 MCP 审批、#12 数据库只读——这五条都容易被写进方案/标书，但一演示就露。

---

## 14. 盘点结论（给决策用）

1. **可以直接对外讲的能力**：多集群 K8s 查询与常用变更、kubeconfig 加密纳管、Agent 反向隧道接入、主机 SSH 终端 + 录像回放、数据库纳管 + 慢 SQL 分析、AI 多轮诊断 + 工具取证 + Prompt 管理、告警→诊断→处置→工单闭环、通知三通道。
2. **必须加"前提条件"才能讲的能力**：所有变更类操作（无审批/无审计）、所有 AI 自动处置（非 prod 默认 full）、所有跨项目读（无租户隔离）、LDAP（缺依赖且无限流）。
3. **不能对外讲的能力**：多租户隔离、审批强制闸门、审计合规、RBAC/impersonation、API Key、灰度发布、VM 管理、代码/CI-CD 闭环。
4. **一句话**：这是一个**功能密度很高、边界很薄**的平台。功能广度已经超过多数同类开源方案，但"谁能做什么"这条线目前只画在 API 层的一层薄壳上，下面直接就是 cluster-admin。
