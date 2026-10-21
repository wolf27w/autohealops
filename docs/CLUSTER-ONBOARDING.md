# 集群接入（三种方式）需求 —— 2026-10-16 老板提出

> 状态：**待开发**（老板 2026-10-16 11:48：「三个方式我都想实现」）
> 关联：R12 agent 反向隧道（`bf5d219`）、`k8s/*.yaml`（平台自身部署清单）、`api-server/app/agent_gateway.py`

---

## 一、需求原话

> 「如果我的服务部署到集群里就自动会把集群接入进来；如果部署在集群外部可以通过 config 文件接入；也可以通过和 KubeSphere 那样 webhook 配置接入到一个平台里。三个方式我都想实现。」

拆成三条：

| # | 方式 | 场景 | 期望体验 |
|---|---|---|---|
| **M1** | **集群内自托管 → 自动接入** | 平台（api-server/executor/ai-service）部署在**被管集群自己**里 | 装完即用：启动时**自动**把「本集群」登记进来（无需传 kubeconfig） |
| **M2** | **集群外 + config 文件接入** | 平台在集群外（本地/独立主机） | 用 **kubeconfig 文件**接入；支持**多 context 批量** |
| **M3** | **Agent 接入（KubeSphere 风格）** | 被管集群**不开放入站端口** | 平台生成**接入令牌 + 一键安装清单**；在目标集群 `kubectl apply` 后 agent **主动外连**，平台**自动登记**该集群 |

> 注：M3 说的「webhook 配置」= KubeSphere 的**集群接入（agent）**那种「平台给一段 YAML/命令，目标集群 apply 即接入」，**不是** k8s 的 Admission Webhook。

---

## 二、现状（代码核实）

| 能力 | 现状 | 落点 |
|---|---|---|
| kubeconfig 接入 | ✅ **已有**：上传/粘贴 kubeconfig → AES-256-GCM 加密存库 | `models/cluster.py: kubeconfig_encrypted`；`api/clusters.py: create_cluster`；`ClusterList.tsx` 添加集群 |
| 平台自身容器化部署 | 🟡 **清单已有，未上生产** | `k8s/*.yaml`（ns=autohealops：deployments/services/configmap/secret/hpa/pdb/networkpolicy/ingress） |
| Agent 反向隧道（R12） | 🟡 **半成品**：隧道通了（agent 在线 → executor 请求走隧道），**但没有「接入闭环」** | `api-server/app/agent_gateway.py`（gRPC 双向流 `AgentService.Tunnel`）、`proto/agent.proto` |
| 集群来源/接入方式字段 | ❌ 无 | `clusters` 表只有 `kubeconfig_encrypted`，没有 `source` / agent 令牌 / 心跳 |
| 多 context 批量 / 启动导入 | ❌ 无 | 添加集群只支持单份 kubeconfig |

**结论**：真正缺的是 **M1 自注册** 和 **M3 接入闭环**；M2 只差「多 context 批量 + 启动导入」的增强。

---

## 三、设计

### 统一抽象（三方式共用）

- `clusters` 新增 `source`（`kubeconfig` | `in-cluster` | `agent`，默认 `kubeconfig`），前端列表显示来源徽标。
- **幂等键**：接入时用集群唯一标识判重 —— 优先**集群 UID**（`kube-system` ns 的 UID），回退 kubeconfig 的 `server` URL。已存在则**更新**而非新建。
- 迁移：`ALTER TABLE clusters ADD source ...`（回填 `kubeconfig`）+ 新增 agent 令牌/心跳列（或独立表 `cluster_agent_tokens`）。

### M1 集群内自托管 → 自动接入

- **检测**：进程内存在 `KUBERNETES_SERVICE_HOST`/`PORT` + SA token 文件 → in-cluster 模式。
- executor 支持 **in-cluster config**（Go `rest.InClusterConfig()`），无需 kubeconfig 即可操作本集群。
- api-server 启动钩子 `bootstrap_incluster_cluster()`：
  - `AUTO_REGISTER_CLUSTER=true`（默认 true）时，探测本集群 UID + 名称（env `CLUSTER_NAME`，缺省用 ns `autohealops` 的 UID 短码）→ **upsert** 一条 `source=in-cluster` 的 Cluster。
  - 该记录不存 kubeconfig；executor 对该集群走 in-cluster 凭据。
- 前端：集群列表标「**本集群**」；该记录**不允许删除**（只能停用/改环境标签）。
- 落点：`k8s-executor`（in-cluster config 分支）、`api-server/app/main.py`（启动钩子）、`clusters` 模型 + `ClusterList.tsx`。

### M2 集群外 + config 文件（含「**自动下发客户端**」开关）

> 老板 2026-10-16 定：**平台部署到 k8s 后，用 kubeconfig 接入其他集群时，可选自动下发一个 agent 到目标集群，之后由平台托管更新**；新建集群时要有个**开关**——开了才下发，不开不下发；**默认关（关闭）**。

- **平台侧**：添加集群弹窗支持「**解析 kubeconfig → 列出所有 context**」→ 勾选多个 → **批量接入**（每条 Cluster 一个 context，名称可改，默认取 context 名）。
- **新增开关**：`自动下发客户端（Agent）` —— **默认 ❌ 关**（老板 2026-10-16 修正：「默认是关闭，不是开」）；**开了才下发**，不开则纯 kubeconfig 直连。
  - **开关开**：用刚接入的 kubeconfig 去目标集群 **apply agent 清单**（ns/SA/ClusterRole+Binding/令牌 Secret/Deployment）→ 成功即**转为 agent 通道**（免入站、**凭据免维护**）；之后可停用/删除 kubeconfig（agent 已接管）。
  - **开关关（默认）**：纯直连（凭据会过期，靠第八节到期预警 + 手动换）。
  - **权限不足**（目标集群没权限建 ns/RBAC/Deployment）→ 退化为「**给命令人工安装**」（即 M3 的一键清单）。
- **部署侧**：`install.sh` 增 `--import-kubeconfig <file>`（或 env `BOOTSTRAP_KUBECONFIG=/path/to/config`）→ 首次启动自动导入该文件里全部 context（同样遵从这个开关，默认不下发）。
- 落点：`create_cluster` 支持 `contexts: []` 批量 + `deploy_agent: bool`（**默认 false**）；新增 `POST /clusters/{id}/agent/deploy`（幂等 server-side apply）；前端 kubeconfig 解析（js-yaml 只列 context）+ 开关（默认关）；`install.sh` 参数。

### Agent 托管更新（平台统一版本管理）

- 平台记录每个集群的 **agent 版本 + 心跳 + 状态**；列表可见「agent 版本不匹配 / 离线」。
- **平台升级时**：一键（或自动）把各集群 agent **滚动升级**到与平台匹配的版本（改 Deployment image）；支持**批量升级 / 单集群升级 / 暂停 / 回退**。
- 删除/下线集群时可选**卸载 agent**（删清单；数据保留/清除需确认）。
- 幂等：重复下发用 server-side apply；agent 版本与平台不兼容时提示升级。

### M3 Agent 接入（KubeSphere 风格）

**接入向导（平台侧，3 步）**
1. 填**集群名称 + 环境标签 + 平台对外地址**（默认取当前站点 URL）。
2. 生成**接入令牌**（一次性/长期，可吊销）+ **一键安装命令**：
   ```
   kubectl apply -f https://<platform>/api/v1/clusters/agent/install.yaml?token=<TOKEN>
   ```
   （或「下载 YAML」按钮）
3. 页面显示「**等待连接…**」→ agent 首连后自动转为「**已连接**」。

**下发的清单内容**：`Namespace` + `ServiceAccount` + `ClusterRole/ClusterRoleBinding`（只读+所需动作，最小权限）+ `Secret`（token + 平台地址）+ `Deployment`（agent 容器，主动外连平台）。
- agent 复用 **R12 反向隧道**（`AgentService.Tunnel`）连 `agent_gateway`。
- 平台侧收到首连：校验 token → **upsert** `clusters(source=agent, name=向导填的名)` → `agent_status=connected`；心跳丢失 → `unreachable`；吊销 token → agent 断开、记录置 `revoked`。

**前端**：集群列表「**接入集群**」按钮 → 向导；列表显示 `agent 状态 / 最后心跳 / agent 版本`。
**落点**：`agent_gateway.py`（token 校验 + 首连注册 + 心跳/状态）、`api/clusters.py`（令牌签发/吊销 + `install.yaml` 渲染）、`ClusterList.tsx`（向导 + 状态列）；复用 `k8s/*.yaml` 里的 RBAC/Deployment 模板。

---

## 四、落点清单（汇总）

| 层 | 改动 |
|---|---|
| DB | `clusters` 加 `source`、`agent_token_hash`、`agent_status`、`agent_last_seen_at`、`agent_version`（或独立表）；迁移 + 回填 |
| api-server | 启动自注册（`main.py`）；`create_cluster` 批量 context；agent 令牌签发/吊销 + `install.yaml` 渲染；`agent_gateway` 首连注册/心跳 |
| executor | in-cluster config 分支（`rest.InClusterConfig()`） |
| 前端 | 添加集群弹窗（多 context 勾选批量）；集群列表来源徽标 + agent 状态列；接入向导（M3）；「本集群」标记 |
| 部署 | `install.sh --import-kubeconfig` / `BOOTSTRAP_KUBECONFIG`；agent 安装 YAML 模板 |

---

## 五、排期建议（先小后大，共用抽象先行）

| 批次 | 内容 | 工时 |
|---|---|---|
| 0 | 统一抽象：`clusters.source` + 幂等键 + 列表来源徽标（三方式共用） | ~0.5d |
| 1 | **M2 增强**：多 context 批量接入 + **自动下发 agent 开关（默认关）** + **agent 托管更新** + `install.sh` 导入 | ~1.5d |
| 2 | **M3 Agent 接入闭环**：令牌签发/吊销 + 安装清单 + 首连自动登记 + 心跳/状态 + 向导（复用 R12 隧道） | ~3–4d |
| 3 | **M1 集群内自注册**：executor in-cluster config + 启动 upsert + 「本集群」标记 | ~1.5–2d |

**验收**
- 三种方式各接入一个测试集群，均能在集群列表看到，且 `health-check` 通过；
- **幂等**：同一集群重复接入不产生重复记录（按集群 UID 判重）；
- M3：吊销令牌后 agent 断开、记录置 `revoked`；agent 断线后状态变 `unreachable`；
- M1：平台部署进测试集群后**无 kubeconfig** 也能自动出现「本集群」并可执行 Pod 列表/日志。

---

## 六、待老板确认（小口径，不影响开工）

1. M1 自注册默认**开启**（`AUTO_REGISTER_CLUSTER=true`）还是默认关、由部署参数显式开？
2. M3 令牌是**一次性**（接入后失效）还是**长期**（可续期/吊销）？默认建议：**一次性换取长期连接凭据**（token 只用于首连鉴权）。
3. M1 自动接入的集群默认 **env_tag** 取什么？（建议 `prod` 或按部署参数 `CLUSTER_ENV_TAG`）

---

## 七、部署拓扑与「环境区分」（老板 2026-10-16 定：**单平台多集群**）

**结论**：三种接入方式解决的是「**被管集群怎么连到这一份平台**」，不是「每个集群装一份平台」。老板已定：**一套平台 + 多集群**（平台只跑一份）。

- ✅ 推荐：平台跑一处（集群外主机 或 其中一个集群内→那台自动成为「本集群」），其余集群 kubeconfig/agent 接入；告警/AI/工单/审计**只有一套**，靠 `cluster_id + env_tag` 区分环境。
- ❌ 反模式：每个集群各装一份 → N 套平台、数据割裂（除刻意做环境物理隔离）。
- 后续可选：若真要「每环境一套」又想要统一大盘 → 需做**多实例联邦汇总**（复杂，暂不做）。

**环境维度（现状 + 缺口）**
- 已有：`clusters.env_tag`（qa/release/prod）；告警/工单/诊断/审计都带 `cluster_id`；仪表盘支持按 cluster 过滤。
- 缺（本需求补）：告警列表 / 工单 / AI 诊断会话 **显示 + 按 env_tag 筛选**；仪表盘按环境聚合；通知模板带「环境/集群」。

| 落地项 | 落点 | 工时 |
|---|---|---|
| 环境维度展示/筛选（告警列表、工单、诊断会话） | alerts/tickets/agent API + 前端列表加 env 列/筛选 | ~1d |
| 仪表盘按环境聚合 + env 过滤 | `stats.py` + `Dashboard.tsx` | ~0.5d |
| 通知模板带「环境/集群」 | `notification_service` 模板变量 | ~0.3d |

---

## 八、凭据到期治理（老板 2026-10-16 问：config 过期还要手动更新吗？）

**现状**：平台**没有任何到期检查**（代码无 `notAfter`/`expires` 处理）；过期表现 = health-check 失败 → `status=unreachable`，**不会提前告知**。

**结论（按凭据类型）**

| 接入方式 | 凭据 | 到期后 |
|---|---|---|
| M2 kubeconfig（**客户端证书**，最常见） | 集群 CA 签的 client cert | ⚠️ **只能手动换**（平台无权自动续；除非集群开放 CSR/TokenRequest 且有权限，一般不开放）→ 但可做到**到期预警 + 一键替换** |
| M2 kubeconfig（**Token/OIDC**） | SA token / OIDC | token 有 exp；OIDC 可凭 refresh token **自动续**（按需支持） |
| **M3 Agent / M1 集群内自托管** | **集群内 SA**（kubelet 自动轮换） | ✅ **永不需要手动更新** —— 免维护正解 |

**改进项（并入本需求）**
1. 接入/健康检查时**解析凭据类型与到期**（client cert `notAfter` / token `exp`）→ 存 `clusters.cred_type` / `cred_expires_at`
2. 集群列表显示「**凭据有效期**」+ 到期前 **30/7/1 天预警**（列表标红 + 站内/IM 通知）
3. **一键更新凭据**：粘贴新 kubeconfig 覆盖 → 保留集群 id/历史告警与工单（**不删集群重加**）
4. **kubeconfig → agent 平滑迁移**：切换接入方式不丢数据，之后免维护
5. （可选高级）若集群允许 CSR 自动签发 → 平台自动续签 client cert（默认不做）

**工时**：~1d（到期解析 + 列表展示 + 预警通知 + 一键替换）

**老板建议**：**能给 agent 就给 agent**（免维护）；必须用 kubeconfig 的，做到「到期前预警 + 一键换」。

---

## 九、各方式「是否需要客户端」/ 前置条件

> 别名（说话时用）：**M1 = 平台自托管（集群内）**｜**M2 = kubeconfig 直连**｜**M3 = Agent 接入**

| 方式 | 被管集群侧要装什么 | 要 agent | 连接方向 | 凭据 |
|---|---|---|---|---|
| **M1 平台自托管** | 要——**部署平台本体**（`k8s/*.yaml`） | ❌ 不用单独 agent（**平台自己就是客户端**，in-cluster SA） | 平台 → 本集群 API | SA（kubelet 自动轮换，**免维护**） |
| **M2 kubeconfig 直连** | **什么都不用装**（只需集群侧账号/RBAC + 一份 kubeconfig） | ❌ 不用 | 平台 → 集群 API **直连** | client cert/token（**会过期**，需手动换） |
| **M3 Agent 接入** | 要——装 **agent**（Deployment + ClusterRole/Binding + 令牌 Secret） | ✅ **需要** | **agent → 平台**（主动外连/反向隧道） | 令牌只用于首连鉴权，之后用集群内 SA（**免维护**） |

**权衡**：M2 不装但要求**平台→apiserver 入站可达** + 凭据会过期；M3 要装 agent 但**免入站** + 凭据免维护（生产多集群首选）；M1 是平台装进集群（自带客户端）。
**M3 前置**：集群能拉 **agent 镜像** + 能访问**平台地址**（出站）。

---

## 十、平台自身的更新/升级（集群内 vs 集群外）

> 关键：**平台升级 ≠ 被管集群升级**。平台升级只动平台自己，不影响被管业务集群；反之被管集群变更与平台无关（只需 API 版本兼容）。

| 平台部署形态 | 升级方式 | 回滚 | 现状 |
|---|---|---|---|
| **集群内（M1）/ 独立机 + kubectl** | `scripts/release_platform.sh --version vX`（构建**带版本 tag** 镜像 → push → `kubectl set image` → 等 rollout；记录 `.deploy-history`） | `scripts/rollback_platform.sh`（Deployment revisionHistory 保留 5 版，零停机 + PDB） | ✅ M10 已完成 |
| **集群外 · Docker/compose** | `git pull` → `install.sh --docker`（重建镜像 + 迁移 + 起容器） | ⬜ **缺**：无版本 tag / 无 `.deploy-history` / 无 `--rollback` | 🟡 待补（对齐 M10） |
| **集群外 · 本地 nohup**（当前 Mac） | `git pull` → `install.sh --local --skip-deps`（或装依赖） → `alembic upgrade head` → `./scripts/dev-reload.sh` / `start.sh restart` | 回退代码 + 迁移降级（或备份恢复） | 🟡 手工 |

**统一升级流程（两种形态都一样）**：
```
备份 DB（backup_mysql.sh）→ 上新版（镜像/代码）→ 执行迁移（alembic upgrade head）→ 重启服务 → 健康检查 /health → 失败则回滚上一版
```

**待补（并入本线）**：给集群外的 Docker/本地模式也加 **`install.sh --version <tag>` + `.deploy-history` + `--rollback`**，升级前自动 `backup_mysql.sh`；工时 ~0.5d。

**有状态组件**：MySQL/Redis 一般用外部实例（当前外部 MySQL `192.168.6.99` / 本机），**升级平台不动它们**；但迁移只向前 → 回滚需靠备份（M11 已具备）。
