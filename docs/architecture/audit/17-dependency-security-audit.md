# 17 · 依赖与供应链安全审计（Dependency & Supply-Chain Audit）

> 审计类型：**只读**。未安装/升级任何依赖，未联网查询漏洞库，未连接生产，未执行 git 写操作。
> 仓库：`/Users/wolf/Documents/project/autohealops`　审计时点：2026-11-27
> **重要限制**：本机无漏洞库（无 `pip-audit`/`npm audit`/`govulncheck`，且不允许安装、不允许联网）。
> 因此本报告分两类结论：**「离线可确认」**（版本区间明确、公告我记得清楚）与**「需联网核实」**（只给疑点，不下结论）。

---

## 1. 结论摘要

1. **锁定情况总体合格**：Python 两个 `requirements.txt` 全部 `==` 精确锁定（api 18 项、ai 12 项）；Go 有 `go.sum`（208 行）；前端有 `package-lock.json`（v3，331 个包，330 条 integrity）——**无 `*`、无 `latest`、无版本范围依赖**（除 package.json 的 `^` 语义版本，已被 lock 固定）。
2. **但有一个 P1 级漏声明**：`api-server/requirements.txt` **缺 `ldap3`、`dingtalk-stream`、`lark-oapi`** 三个实际被代码 import 的包。它们恰好是「LDAP 登录」和「钉钉/飞书长连接审批闭环」的依赖 —— 按 requirements 构建的生产镜像里，这两个能力会**静默降级**（代码有 try/except 兜底，只打一条 warning）。
3. **Go 构建版本自相矛盾（P1）**：`k8s-executor/go.mod:3` 是 `go 1.26.6`，而 `k8s-executor/Dockerfile:2` 是 `golang:1.22-alpine`、CI 是 `go-version: '1.22'`。多阶段构建要成功只能靠 `GOTOOLCHAIN=auto` 在构建时**联网下载 1.26.6 工具链**；离线/受限网络构建直接失败。同仓库 `agent/Dockerfile` 用的是 `golang:1.26-alpine` 且显式 `ENV GOTOOLCHAIN=auto`——说明这是漏改。
4. **4 个容器镜像全部以 root 运行**：`api-server`、`ai-service`、`frontend`、`k8s-executor` 的 Dockerfile 均无 `USER`，k8s 清单里也**无任何 `securityContext`**（`grep securityContext k8s/*.yaml` → 0）。仅 `agent/Dockerfile:53` 用了 `USER 1001`（做得对）。
5. **镜像不可复现（P2）**：k8s 清单里 6 个自建镜像全是 `:latest`；基础镜像 `nginx:alpine`、`alpine:latest` 浮动标签，全部无 digest 固定。
6. **前端镜像构建绕过了 lockfile（P2）**：`frontend/Dockerfile` 只 `COPY package.json ./` 然后 `npm install`，**没拷 `package-lock.json`** → 每次构建按 `^` 范围重新解析，可能装到未经测试的新版本（与本地 `npm ci` 行为不同）。
7. **离线可确认的漏洞 4 项**：`python-jose 3.3.0`（CVE-2024-33663 / CVE-2024-33664，修复版 3.4.0）、其传递依赖 `ecdsa 0.19.2`（CVE-2024-23342，上游明确不修）、`starlette 0.38.6`（CVE-2024-47874，修复版 0.40.0）、前端 `esbuild 0.21.5`（GHSA-67mh-4wv8-2f99，仅 dev server 受影响）。均为 P2/P3，非「立刻可被利用的 RCE」。
8. **密钥卫生良好**：`.env` / `.env.bak-*` / `k8s/generated/` / `data/agent-ca/` 全部在 `.gitignore` 且**未被 git 跟踪**（`git ls-files | grep env` 只有两个 `.example`）；全仓高熵/`PRIVATE KEY`/AK-SK 扫描**零命中**；`secret.example.yaml` 只有占位符；无 `postinstall`/`preinstall` 脚本。

---

## 2. 方法与限制

| 项 | 说明 |
|---|---|
| 已执行 | 读取 4 份依赖清单；`pip show` 读取 venv 实际版本；解析 `package-lock.json`；`grep` 扫描 Dockerfile/CI/k8s/仓库密钥；`go.sum` 行数统计 |
| 未执行 | `pip-audit` / `safety`（未安装）、`npm audit`（需联网且会写 `package-lock`）、`govulncheck`（未安装）、任何在线 CVE 查询（禁止联网） |
| CVE 结论口径 | 「离线可确认」= 版本区间 + 修复版本我有确定记忆；「需联网核实」= 只报疑点，**不写死结论** |

---

## 3. 依赖清单

### 3.1 api-server（Python，`api-server/requirements.txt`）

| 依赖 | 声明版本 | venv 实际 | 用途 | 备注 |
|---|---|---|---|---|
| fastapi | 0.115.0 | 0.115.0 | Web 框架 | 传递 starlette 0.38.6 ⚠️ |
| uvicorn[standard] | 0.30.6 | 0.30.6 | ASGI server | |
| sqlalchemy[asyncio] | 2.0.35 | 2.0.35 | ORM | |
| aiomysql | 0.2.0 | 0.2.0 | MySQL 驱动 | |
| asyncpg | 0.31.0 | 0.31.0 | PG 驱动 | |
| oracledb | 4.0.2 | 4.0.2 | Oracle 驱动（thin/thick） | |
| alembic | 1.13.3 | 1.13.3 | 迁移 | 容器启动时执行 |
| pydantic | 2.9.2 | 2.9.2 | 校验 | |
| pydantic-settings | 2.5.2 | 2.5.2 | 配置 | |
| redis | 5.0.8 | 5.0.8 | 缓存/锁/黑名单 | |
| python-jose[cryptography] | 3.3.0 | 3.3.0 | JWT | ⚠️ CVE-2024-33663/33664 |
| cryptography | 50.0.2 | 50.0.2 | AES-GCM / 证书 | 与 ai-service 43.0.1 分裂 |
| httpx | 0.27.2 | 0.27.2 | HTTP 客户端 | |
| apscheduler | 3.10.4 | 3.10.4 | 定时任务 | |
| grpcio | 1.66.2 | 1.66.2 | gRPC | |
| grpcio-tools | 1.66.2 | 1.66.2 | **编译期工具**（生产镜像不需要） | 建议拆分 |
| bcrypt | 4.2.0 | 4.2.0 | 口令哈希 | |
| asyncssh | 2.24.1 | 2.24.1 | SSH 纳管/终端 | |

**venv 中存在但 requirements 未声明（代码实际 import）**：

| 包 | venv 版本 | 被谁 import | 缺失后果 |
|---|---|---|---|
| `ldap3` | 2.9.1 | `app/api/auth.py:88`（懒加载） | LDAP 登录返回 501「依赖未安装」 |
| `dingtalk-stream` | 0.24.3 | `app/services/long_conn_client.py:109`（懒加载） | 钉钉 Stream 长连接（群内审批）不可用 |
| `lark-oapi` | 1.7.3 | `app/services/long_conn_client.py:28`（try/except 包裹） | 飞书长连接 + 卡片回调审批不可用，启动只打 warning（`main.py:344-347`） |

> 这三个是**功能级缺失**而不是崩溃：代码都做了容错。但「按 requirements 部署的生产环境里，IM 审批闭环是关着的」这件事没有任何告警，只在日志里一行 warning。

### 3.2 ai-service（Python，`ai-service/requirements.txt`）

| 依赖 | 声明 | venv 实际（漂移） |
|---|---|---|
| fastapi | 0.115.0 | **0.141.1** |
| uvicorn[standard] | 0.30.6 | **0.52.3** |
| sqlalchemy[asyncio] | 2.0.35 | **2.0.52** |
| aiomysql | 0.2.0 | **0.3.2** |
| pydantic / pydantic-settings | 2.9.2 / 2.5.2 | **2.13.4 / 2.15.0** |
| httpx | 0.27.2 | **0.28.1** |
| cryptography | **43.0.1** | 43.0.1 |
| openai | 1.51.0 | **3.1.0** |
| anthropic | 0.39.0 | **0.122.0** |
| numpy | 2.1.1 | **2.4.6** |
| redis | 5.0.8 | **8.1.0** |

⚠️ 两个问题：(a) venv 与 requirements 全面不符（详见报告 16 TST-05）；(b) **同一仓库内 cryptography 有两个主版本**（api 50.0.2 / ai 43.0.1），跨服务传递的加密约定（kubeconfig、凭据）依赖 crypto 行为一致，版本分裂是长期隐患。

### 3.3 k8s-executor（Go，`k8s-executor/go.mod`）

直接依赖 17 个：

| 模块 | 版本 | 备注 |
|---|---|---|
| github.com/go-sql-driver/mysql | v1.8.1 | |
| github.com/jackc/pgx/v5 | v5.12.0 | |
| github.com/joho/godotenv | v1.5.1 | |
| github.com/robfig/cron/v3 | v3.0.1 | |
| github.com/spf13/viper | v1.21.0 | 拉入一堆间接依赖 |
| go.uber.org/zap | v1.28.0 | 结构化日志 |
| golang.org/x/crypto | v0.58.0 | |
| golang.org/x/sync | v0.24.0 | |
| google.golang.org/grpc | v1.83.0 | |
| google.golang.org/protobuf | v1.36.12 | |
| gorm.io/driver/mysql | v1.6.0 | |
| gorm.io/gorm | v1.31.2 | |
| k8s.io/api | v0.36.3 | |
| k8s.io/apimachinery | v0.36.3 | |
| k8s.io/client-go | v0.36.3 | |
| k8s.io/metrics | v0.36.3 | |
| sigs.k8s.io/yaml | v1.6.0 | |

- 52 条间接依赖，含 `k8s.io/klog/v2 v2.140.0`、`golang.org/x/net v0.60.0`、`golang.org/x/oauth2 v0.36.0`、`sigs.k8s.io/structured-merge-diff/v6`。
- `go.sum` 208 行，`GOPROXY=off go test ./...` 能全量跑通 → **依赖已完整缓存、go.sum 自洽** ✅。

### 3.4 frontend（`package.json` + `package-lock.json` v3）

| 包 | 声明 | lock 解析版本 | 备注 |
|---|---|---|---|
| react / react-dom | ^18.3.1 | 18.3.1 | |
| antd | ^5.21.1 | 5.29.3 | |
| @ant-design/icons | ^5.5.1 | 5.6.1 | |
| @ant-design/charts | ^2.2.1 | 2.6.7 | |
| @tanstack/react-query | ^5.59.0 | 5.101.4 | |
| react-router-dom | ^6.26.2 | 6.30.4 | |
| axios | ^1.7.7 | **1.19.0** | 已超出 CVE-2025-27152 修复线（1.8.2）✅ |
| dayjs | ^1.11.13 | 1.11.22 | |
| @xterm/xterm | ^6.0.0 | 6.0.0 | |
| @xterm/addon-fit | ^0.11.0 | 0.11.0 | |
| typescript | ^5.5.4 | 5.9.3 | |
| vite | ^5.4.3 | **5.4.21** | 已含 5.4.12~5.4.19 的多个 dev-server 修复 ✅ |
| @vitejs/plugin-react | ^4.3.1 | 4.7.0 | |
| **esbuild（传递）** | — | **0.21.5** | ⚠️ GHSA-67mh-4wv8-2f99，仅 dev |

lock 完整性：`"integrity"` 出现 330 次 / 331 个包（根 package 无 integrity，正常）✅。

---

## 4. 版本锁定与可复现性

| 生态 | 锁定方式 | 结论 |
|---|---|---|
| Python | 全部 `==` 精确版本 | ✅ 但**无 hash**（`pip install` 不校验包完整性） |
| Go | `go.sum` 208 行 + `-mod=readonly` | ✅ |
| Node | `package-lock.json` v3 + 330 integrity | ✅ 本机/CI（`npm ci`）可复现 |
| **Node 镜像构建** | `npm install` + **未 copy lockfile** | ❌ **不可复现**（DEP-07） |
| 容器镜像 | `:latest` / `nginx:alpine` / `alpine:latest` / 无 digest | ❌ 不可复现（DEP-06） |
| Python 镜像 | `python:3.11-slim`（无 digest） | ⚠️ 同 tag 内容会变 |

---

## 5. 已知风险 / 漏洞

### 5.1 离线可确认（版本区间明确）

| 依赖 | 当前版本 | 问题 | 修复版本 | 影响面（本项目实际情况） | 级别 |
|---|---|---|---|---|---|
| `python-jose` | 3.3.0 | **CVE-2024-33663**（算法混淆/密钥混淆，JWT 伪造风险）、**CVE-2024-33664**（JWT 解压炸弹，DoS） | 3.4.0 | JWT 签发/校验主链路（`core/security.py:57,62`）。本项目 `algorithms=["HS256"]` 固定，33663 的可利用性低；33664 的 DoS 不受算法限制，仍存在 | P2 |
| `ecdsa` | 0.19.2（python-jose 传递依赖） | **CVE-2024-23342**（Minerva 计时侧信道，P-256 私钥可被恢复） | **上游明确不修** | 项目用 HS256，不走 ECDSA；仅当未来切 ES256 才暴露 | P3 |
| `starlette` | 0.38.6（fastapi 0.115.0 传递） | **CVE-2024-47874**（multipart/form-data 无上限解析导致内存耗尽） | 0.40.0 | **当前无 multipart 端点**（`grep UploadFile/File(...)` 仅命中 zipfile 误报），暂不可达；一旦加文件上传即暴露 | P2 |
| `esbuild` | 0.21.5（vite 5.4.21 传递） | **GHSA-67mh-4wv8-2f99**（任意网站可读 dev server 响应，仅 `vite dev` 暴露） | 0.25.0 | 仅开发者本机 `npm run dev`；生产构建产物不受影响 | P3 |

### 5.2 需联网核实（只列疑点，不下结论）

| 依赖 | 疑点 | 怎么核实 |
|---|---|---|
| `cryptography 43.0.1`（ai-service） | 43.x 系列内置 OpenSSL 3.3.x，2025 年有若干 OpenSSL 层 CVE（如 RFC7250 相关）；需确认 43.0.1 是否受影响、修复在 44.x | `pip-audit -r ai-service/requirements.txt` 或查 NVD |
| `grpcio 1.66.2` | gRPC C-core 1.66 系列是否有已知 CVE（HTTP/2 相关） | `pip-audit` |
| `oracledb 4.0.2` | 是否随附可被利用的 Oracle 客户端库问题 | 厂商公告 |
| `k8s.io/client-go v0.36.3` | 与 kube-apiserver 的兼容性 + 是否有 CVE（client-go 常有 SSRF/DoS 类） | `govulncheck ./...` |
| `golang.org/x/crypto v0.58.0` | 版本号高于我所知的公开版本，可能存在未披露修复 | `govulncheck` |
| `antd 5.29.3` / `@ant-design/charts 2.6.7` | React 生态 XSS（`dangerouslySetInnerHTML` 类） | `npm audit` |
| `vite 5.4.21` | 5.4 线在 2025 年连续修了 4 个 dev-server 文件读取 CVE，需确认 5.4.21 之后是否还有 | `npm audit` |

> 说明：本机禁止联网，上表**均未核实**。建议在允许联网的环境执行一次：
> `pip-audit -r api-server/requirements.txt`、`pip-audit -r ai-service/requirements.txt`、`cd frontend && npm audit --omit=dev`、`cd k8s-executor && govulncheck ./...`。

---

## 6. 容器镜像安全

### 6.1 基础镜像与构建

| 服务 | 构建阶段 | 运行阶段 | USER | multi-stage | 问题 |
|---|---|---|---|---|---|
| api-server | `python:3.11-slim`（单阶段） | 同左 | **无 → root** | ❌ | 单阶段把 pip 缓存/构建残留带进运行镜像（虽有 `--no-cache-dir`）；启动直接 `alembic upgrade head` |
| ai-service | `python:3.11-slim` | 同左 | **无 → root** | ❌ | 同左 |
| frontend | `node:20-alpine` | `nginx:alpine` | **无 → root**（nginx 默认 master 是 root） | ✅ | 见 DEP-07 |
| k8s-executor | `golang:1.22-alpine` | `alpine:latest` | **无 → root** | ✅ | 版本与 go.mod 不符（DEP-01）；wget 下载二进制无校验 |
| agent | `${GO_IMAGE:-golang:1.26-alpine}` | `${BASE_IMAGE:-debian:bookworm-slim}` | **`USER 1001`** ✅ | ✅ | 唯一做对的；但 `GOARCH=amd64` 写死 |

### 6.2 k8s 清单

```bash
grep -rn "securityContext\|runAsNonRoot\|readOnlyRootFilesystem\|allowPrivilegeEscalation" k8s/*.yaml
# → 0 命中
```

- 无 `securityContext`、无 `runAsNonRoot`、无 `readOnlyRootFilesystem`、无 `drop: ALL capabilities`。
- 无 `hostNetwork`/`hostPID`/`hostPath`/`privileged` ✅（这点做对了）。
- 有 `NetworkPolicy`（`k8s/networkpolicy.yaml`，只允许 api-server 入站 executor/ai-service）✅、有 HPA/PDB/PVC ✅。
- 镜像：`autohealops/*:latest` ×6（其中 `retention-cronjob.yaml:56` 用了 `r49-20261123` 固定 tag，是全场唯一固定版本）。
- Secret 走 `envFrom` 整包注入（`deployments.yaml:52,108,169,220`），模板见 `k8s/secret.example.yaml`（占位符）✅。

### 6.3 镜像内是否含构建工具/密钥

- `k8s-executor` 运行镜像 `apk add ca-certificates curl` → 带 `curl`（可被利用做横向下载）。`grpc_health_probe` 从 GitHub release `wget` 下载，**无 sha256 校验**（DEP-10）。
- 无 `gcc`/`build-essential` 进运行镜像 ✅。
- 无密钥 COPY 进镜像 ✅（`.env` 不在 build context 有效范围、`.dockerignore` 排除了 `data/`、`logs/`）。

---

## 7. 供应链

| 项 | 现状 | 评价 |
|---|---|---|
| pip 源 | 未自定义（Dockerfile/CI 均用默认 PyPI） | ⚠️ 无内网镜像、无 `--require-hashes` |
| npm registry | 无 `.npmrc`，默认 registry.npmjs.org | ⚠️ 锁文件有 integrity，风险可控 |
| Go proxy | `k8s-executor/Dockerfile:6 ENV GOPROXY=https://goproxy.cn,direct`；`agent/Dockerfile` 默认同；CI 用默认 | ⚠️ 第三方中转（国内镜像），`direct` 兜底；CI 与镜像构建走的源不同 → 行为可能不一致 |
| postinstall 脚本 | `grep postinstall\|preinstall\|prepare frontend/package.json` → **0** | ✅ |
| 依赖数量 | 331 个 npm 包、52 个 Go 间接依赖、18+12 Python 直接依赖 | 中等，可接受 |
| 传递依赖审计 | 无 SBOM、无 `pip freeze` 快照入库、无 Renovate/Dependabot 配置 | ❌（`.github/` 下只有 ci.yml） |

---

## 8. 密钥 / 凭据泄露扫描

```bash
# 高熵/常见凭据前缀
grep -rnIE "(AKIA[0-9A-Z]{16}|-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-)" \
  --exclude-dir=node_modules --exclude-dir=.venv --exclude-dir=.git --exclude-dir=__pycache__ .
# → 0 命中

# 形如 token="xxxx..." 的硬编码字面量（python/go/ts/yaml/json）
grep -rnIE "(token|secret|password|api_key)[\"']?\s*[:=]\s*[\"'][A-Za-z0-9_\-/+=]{16,}[\"']" ...
# → 0 命中
```

| 检查 | 结果 |
|---|---|
| 硬编码 AK/SK、私钥、token | ✅ 零命中 |
| `.env` 是否入库 | ✅ 未跟踪（`git ls-files | grep env` → 仅 `.env.example`、`agent/examples/agent.env.example`） |
| `.env.bak-*` | ✅ 被 `.gitignore:3` 忽略且未跟踪 |
| 平台 CA 私钥（`data/agent-ca/`） | ✅ 被忽略（`.gitignore` 显式注释「严禁入库」） |
| `k8s/generated/`（真实 Secret 生成物） | ✅ 被忽略，`git ls-files k8s/generated` 为空 |
| `k8s/secret.example.yaml` | ✅ 仅占位符 |
| `.env.example` 中 `JWT_SECRET_KEY` / `ENCRYPTION_KEY` | 长度为 9 / 33 字符 → 是**占位串**而非真实密钥 ✅ |
| 日志中的凭据 | ⚠️ 见报告 15 QUA-10（启动日志打印初始管理员明文密码） |

---

## 9. 依赖风险排序表

| 排序 | 风险项 | 级别 | 依据 | 可利用性/影响 |
|---:|---|---|---|---|
| 1 | `k8s-executor` 构建链版本矛盾（golang:1.22 vs go 1.26.6，CI 也是 1.22） | **P1** | `Dockerfile:2`、`ci.yml:28`、`go.mod:3` | 构建依赖联网自动拉工具链；离线/内网构建直接失败。属于「随时会炸」的交付风险 |
| 2 | `requirements.txt` 漏 3 个实际依赖（ldap3 / dingtalk-stream / lark-oapi） | **P1** | `requirements.txt` 全 18 行 vs `auth.py:88`、`long_conn_client.py:28,109` | 生产镜像里 LDAP 登录 + IM 长连接审批静默失效，无告警 |
| 3 | 4 个镜像 root 运行 + k8s 无 securityContext | P2 | 4 个 Dockerfile 无 USER；`grep securityContext` = 0 | 容器逃逸/横向移动的放大面；合规检查必挂 |
| 4 | `python-jose 3.3.0`（CVE-2024-33663/33664） | P2 | requirements:12 | DoS 实际可用；算法混淆因固定 HS256 而受限 |
| 5 | 镜像 tag `:latest` + 基础镜像浮动 + 无 digest | P2 | `k8s/deployments.yaml` 6 处 `:latest` | 回滚不可靠、环境间不一致、无法追溯「线上跑的是哪次提交」 |
| 6 | 前端镜像 `npm install` 且未 copy lockfile | P2 | `frontend/Dockerfile:4-5` | 构建时可能装上未经测试的新版本（axios/vite 都在 `^` 范围） |
| 7 | `starlette 0.38.6`（CVE-2024-47874） | P2 | 传递依赖 | 当前无 multipart 端点 → 暂不可达；加文件上传即暴露 |
| 8 | `cryptography` 跨服务版本分裂（50.0.2 / 43.0.1） | P2 | 两个 requirements | 加密行为/API 差异；43.0.1 是否含已修 CVE 需联网核实 |
| 9 | `golang:1.22`/`alpine:latest`/`nginx:alpine` 浮动基础镜像 | P2 | 各 Dockerfile | 同 tag 内容随时间变化 |
| 10 | `esbuild 0.21.5`（GHSA-67mh-4wv8-2f99） | P3 | lock | 仅 dev server |
| 11 | `grpc_health_probe` wget 无 checksum | P3 | `k8s-executor/Dockerfile:16-17` | 供应链投毒面（虽走 HTTPS） |
| 12 | pip 无 hash / 无内网源 / 无 SBOM / 无自动升级机器人 | P3 | 全仓 | 长期维护风险 |
| 13 | `grpcio-tools` 进生产镜像 | P3 | requirements:16 | 体积与攻击面 |

---

## 10. 建议升级/替换清单

### 10.1 可直接做（低风险，不需要产品决策）

| 动作 | 具体改动 | 收益 |
|---|---|---|
| 补 3 个漏声明依赖 | `api-server/requirements.txt` 追加 `ldap3==2.9.1`、`dingtalk-stream==0.24.3`、`lark-oapi==1.7.3` | 修复 P1 功能静默缺失 |
| 修 Go 构建版本 | `k8s-executor/Dockerfile` 改 `FROM golang:1.26-alpine`（对齐 `agent/Dockerfile`），并显式 `ENV GOTOOLCHAIN=local`；CI `go-version: '1.26'` | 消除「靠联网拉工具链」的隐式依赖 |
| 前端镜像用 lock | `COPY package.json package-lock.json ./` + `RUN npm ci --no-audit --no-fund` | 构建可复现 |
| 镜像 tag 去 latest | 部署改 `:${GIT_SHA}` 或语义版本；`nginx:alpine` → `nginx:1.27-alpine`、`alpine:latest` → `alpine:3.20` | 可回滚、可追溯 |
| python-jose 升 3.4.0 | 3.4.0 修复 CVE-2024-33663/33664，API 兼容 | 消除 DoS |
| 统一 cryptography | ai-service 升到与 api 同版本（或两边都降到同一 LTS 线），验证 kubeconfig 加解密互测（`make crypto-test` 已有） | 消除版本分裂 |

### 10.2 需老板决策

| 决策点 | 选项 | 影响 |
|---|---|---|
| **是否给 4 个服务加非 root 用户** | A. 加 `USER 10001` + k8s `securityContext`（runAsNonRoot、readOnlyRootFilesystem、drop ALL）；B. 维持现状 | A 需要处理「写 /app 目录、alembic 迁移、nginx 监听 80 端口」等适配（nginx 需改 8080 或用 unprivileged 镜像），有一定回归成本；B 过不了等保/安全扫描 |
| **是否启用严格供应链** | A. 引入 `pip --require-hashes` + 内网 PyPI 镜像 + Renovate/Dependabot + SBOM（syft）；B. 只做版本锁定 | A 增加流水线复杂度，B 保持现在「能跑但不可审计」 |
| **是否升 fastapi/starlette** | A. fastapi 0.115 → 0.11x 最新 + starlette ≥0.40；B. 只把 starlette 单独 pin 到 0.40+（需确认 fastapi 0.115 兼容区间） | 升级要回归 495 条路由（当前无 HTTP 层测试，风险高，建议先补报告 16 的 §7 #8 再升） |
| **ai-service 依赖是否要按 requirements 重建** | A. 重建 venv 并修 openai 1.x→3.x 的适配；B. 把 requirements 改成实际的 3.1.0 并测试 | A 可能有代码改动；B 是「把现状写成事实」，但 openai 3.x 是否还在生产用需确认 |
| **是否移除 grpcio-tools 出生产镜像** | A. 移到 `requirements-dev.txt`；B. 保持 | 需确认运行时不需要动态生成 stub（当前 `app/proto/*` 已生成入库，应该不需要） |

---

## 11. 问题清单

```
问题编号：DEP-01
问题名称：k8s-executor 构建链 Go 版本矛盾（Dockerfile/CI 1.22 vs go.mod 1.26.6）
问题等级：P1
所属模块：构建/交付
问题描述：go.mod 声明 go 1.26.6，Dockerfile 用 golang:1.22-alpine 且未设 GOTOOLCHAIN，CI 用 go-version 1.22；构建成功依赖 GOTOOLCHAIN=auto 联网下载新工具链，离线/内网构建必失败。同仓 agent/Dockerfile 已正确用 1.26 且显式 GOTOOLCHAIN=auto，说明 executor 侧漏改。
代码位置：k8s-executor/Dockerfile:2；.github/workflows/ci.yml:28；k8s-executor/go.mod:3；对比 agent/Dockerfile:16-21
涉及函数或类：—
调用链：docker build → go mod download → go build ./cmd/executor
根本原因：升级 go.mod 时未同步 Dockerfile 与 CI
可能影响：内网 Harbor 构建流水线随机失败；CI 与本地行为不一致；构建时间不可控
修改建议：Dockerfile 改 golang:1.26-alpine 并显式 GOTOOLCHAIN=local（锁死，避免隐式下载）；CI go-version 改 1.26
验证方法：断网构建 docker build ./k8s-executor 应成功；CI go build 步骤不再依赖网络拉工具链
是否需要立即处理：是
```

```
问题编号：DEP-02
问题名称：requirements.txt 漏声明 ldap3 / dingtalk-stream / lark-oapi，生产镜像功能静默缺失
问题等级：P1
所属模块：api-server 依赖
问题描述：三个包在 venv 中存在且被代码 import，但 requirements.txt 未列出；代码用懒加载 + try/except 兜底，导致生产环境 LDAP 登录返回 501、钉钉/飞书长连接审批不启动，只有一行 warning。
代码位置：api-server/requirements.txt:1-18；api-server/app/api/auth.py:88；api-server/app/services/long_conn_client.py:28,109；api-server/app/main.py:342-347
涉及函数或类：_ldap_login、_start_dingtalk_stream、start_feishu_long_conn_global
调用链：启动 lifespan → long_conn_client.start_* → import lark_oapi / dingtalk_stream
根本原因：新增可选依赖时只在本机 pip install，未回写 requirements
可能影响：IM 审批闭环（群内审批、卡片回调）在生产不可用；LDAP 用户无法登录；且无显式告警
修改建议：requirements 追加三个包并精确锁版本；启动时对「配置了 IM 渠道但依赖缺失」打 error 并在系统设置页展示状态
验证方法：按 requirements 新建 venv 后 `python -c "import ldap3, dingtalk_stream, lark_oapi"` 成功；启动日志无「依赖未安装」
是否需要立即处理：是
```

```
问题编号：DEP-03
问题名称：python-jose 3.3.0 存在 CVE-2024-33663 / CVE-2024-33664
问题等级：P2
所属模块：认证
问题描述：3.3.0 受算法混淆（CVE-2024-33663）与 JWT 解压炸弹 DoS（CVE-2024-33664）影响，修复版 3.4.0。本项目固定 algorithms=["HS256"]，混淆利用受限，但 DoS 不受此限制。
代码位置：api-server/requirements.txt:12；api-server/app/core/security.py:62
涉及函数或类：decode_jwt
调用链：任意带 Bearer token 的请求 → decode_jwt → jose.jwt.decode
根本原因：依赖长期未升级
可能影响：超大压缩 token 可耗尽 CPU/内存（认证前置，无需登录）
修改建议：升 python-jose[cryptography]==3.4.0；顺带评估替换为 PyJWT（维护更活跃），需改 2 处调用
验证方法：升级后 unittest 全绿 + 登录/登出冒烟
是否需要立即处理：否（建议下个迭代）
```

```
问题编号：DEP-04
问题名称：4 个服务镜像以 root 运行，k8s 清单无 securityContext
问题等级：P2
所属模块：容器安全
问题描述：api-server/ai-service/frontend/k8s-executor 的 Dockerfile 均无 USER；k8s/*.yaml 中 securityContext/runAsNonRoot/readOnlyRootFilesystem 命中数为 0。
代码位置：api-server/Dockerfile:1-18、ai-service/Dockerfile:1-14、frontend/Dockerfile:1-12、k8s-executor/Dockerfile:1-24；k8s/deployments.yaml
涉及函数或类：—
调用链：kubelet 启动容器 → 以镜像默认用户（root）运行
根本原因：早期为省事未加 USER
可能影响：应用层 RCE 直接获得容器 root；配合内核漏洞放大逃逸风险；等保/安全扫描不通过
修改建议：镜像加 USER 10001 + chown；k8s 加 securityContext（runAsNonRoot: true, allowPrivilegeEscalation: false, capabilities.drop: [ALL], readOnlyRootFilesystem: true + emptyDir 挂 /tmp）
验证方法：`kubectl exec -- id` 输出非 0；`kubectl get pod -o yaml | grep runAsNonRoot`
是否需要立即处理：否（需老板决策，见 §10.2）
```

```
问题编号：DEP-05
问题名称：自建镜像全部 :latest，基础镜像浮动且无 digest，发布不可复现
问题等级：P2
所属模块：交付
问题描述：k8s 清单 6 处 autohealops/*:latest；基础镜像 nginx:alpine、alpine:latest、python:3.11-slim、node:20-alpine 均无 digest 固定。
代码位置：k8s/deployments.yaml:35,101,163,217,271；k8s/migrate-job.yaml:34；各 Dockerfile FROM 行
涉及函数或类：—
调用链：kubectl apply → 拉取 latest
根本原因：图省事
可能影响：同一 YAML 在不同时间部署出不同版本；回滚到「上一个 latest」不可能；事故复盘无法定位代码版本
修改建议：镜像 tag 用 git sha（CI 已能拿到）；基础镜像固定到 minor + digest（如 python:3.11.16-slim@sha256:...）
验证方法：`kubectl get deploy -o jsonpath` 中 image 含 sha
是否需要立即处理：否
```

```
问题编号：DEP-06
问题名称：前端镜像构建忽略 lockfile，构建结果不可复现且可能引入未测版本
问题等级：P2
所属模块：前端交付
问题描述：frontend/Dockerfile 只 COPY package.json 后 `npm install`，未拷 package-lock.json，也未用 `npm ci`；而 package.json 全部是 `^` 范围。
代码位置：frontend/Dockerfile:3-6
涉及函数或类：—
调用链：docker build → npm install（重新解析 ^ 范围）
根本原因：早期写 Dockerfile 时 lockfile 尚未提交
可能影响：CI 本地 `npm ci` 装 5.4.21，镜像里可能装到 5.9.x（vite 主版本变化会直接构建失败或行为差异）
修改建议：`COPY package.json package-lock.json ./` + `RUN npm ci --no-audit --no-fund`
验证方法：构建日志中 npm 使用 lockfile；镜像内 `npx vite -v` 与 lock 一致
是否需要立即处理：否
```

```
问题编号：DEP-07
问题名称：starlette 0.38.6 受 CVE-2024-47874 影响（当前无 multipart 端点，暂不可达）
问题等级：P2
所属模块：Web 框架
问题描述：fastapi 0.115.0 锁定的 starlette 0.38.6 存在 multipart 解析无上限导致的内存 DoS；修复版 0.40.0。当前代码无文件上传端点，暂不可利用，但属于「加个上传功能就中招」的定时炸弹。
代码位置：api-server/requirements.txt:2（传递依赖，venv 实测 starlette 0.38.6）
涉及函数或类：—
调用链：uvicorn → starlette Request.form()
根本原因：fastapi 版本较旧
可能影响：一旦新增 multipart 端点即暴露；升级前建议先补 HTTP 层测试
修改建议：随 fastapi 升级一并升到 starlette ≥0.40；或短期在网关层限制 body 大小
验证方法：`pip show starlette` ≥0.40；上传 1GB multipart 被拒
是否需要立即处理：否
```

```
问题编号：DEP-08
问题名称：cryptography 跨服务版本分裂（api 50.0.2 / ai 43.0.1）
问题等级：P2
所属模块：依赖一致性
问题描述：同一平台两个 Python 服务锁定不同的 cryptography 主版本；加解密约定（kubeconfig、凭据、token）依赖 crypto 行为一致。
代码位置：api-server/requirements.txt:13（50.0.2）；ai-service/requirements.txt:9（43.0.1）
涉及函数或类：app/core/security.py、ai-service 相关解密
调用链：api-server AES-GCM 加密 → ai-service（若解密）
根本原因：两个服务 requirements 各自演进
可能影响：算法/填充/默认参数差异导致跨服务解密失败（难排查）；43.0.1 是否含已修 CVE 需联网核实
修改建议：统一到同一版本；用 `make crypto-test`（跨语言互测）覆盖 Python↔Python 与 Python↔Go
验证方法：两服务 `pip show cryptography` 版本一致；互测通过
是否需要立即处理：否
```

```
问题编号：DEP-09
问题名称：Go 代理走第三方中转 goproxy.cn，且与 CI 使用的源不一致
问题等级：P3
所属模块：供应链
问题描述：k8s-executor/agent 的 Dockerfile 设 GOPROXY=https://goproxy.cn,direct；CI 未设置（走默认 proxy.golang.org）。两条构建路径拉包源不同，且 goproxy.cn 是第三方中转。
代码位置：k8s-executor/Dockerfile:6；agent/Dockerfile:19
涉及函数或类：—
调用链：docker build → go mod download
根本原因：国内网络加速
可能影响：源不一致可能导致校验差异（go.sum 会兜底）；第三方源可用性与信任度依赖外部
修改建议：CI 与镜像统一 GOPROXY（或自建 Athens）；保留 GONOSUMDB 默认（不要关 GOSUMDB）
验证方法：两处 env 一致；`go mod verify` 通过
是否需要立即处理：否
```

```
问题编号：DEP-10
问题名称：grpc_health_probe 二进制 wget 下载无完整性校验
问题等级：P3
所属模块：容器供应链
问题描述：executor 运行镜像构建时从 GitHub release wget 二进制并 chmod +x，无 sha256 校验。
代码位置：k8s-executor/Dockerfile:16-17
涉及函数或类：—
调用链：docker build → wget → chmod
根本原因：图方便
可能影响：上游 release 被篡改/GitHub 中间人时植入后门（HTTPS 降低了概率但不等于零）
修改建议：加 `echo "<sha256>  /bin/grpc_health_probe" | sha256sum -c -`
验证方法：构建日志显示校验通过
是否需要立即处理：否
```

```
问题编号：DEP-11
问题名称：esbuild 0.21.5 受 GHSA-67mh-4wv8-2f99 影响（仅 dev）
问题等级：P3
所属模块：前端开发依赖
问题描述：vite 5.4.21 依赖 esbuild 0.21.5，dev server 允许任意网站读取响应；修复版 0.25.0（vite 6+ 才默认带）。
代码位置：frontend/package-lock.json（node_modules/esbuild 0.21.5）
涉及函数或类：—
调用链：npm run dev → esbuild transform API
根本原因：vite 5 依赖线
可能影响：开发者本机 dev server 被恶意网页读取源码/文件；生产构建不受影响
修改建议：开发者本机 `vite --host 127.0.0.1` 限制监听；或升级 vite 6（需回归）
验证方法：`npm ls esbuild`
是否需要立即处理：否
```

```
问题编号：DEP-12
问题名称：agent 镜像固定 GOARCH=amd64；与 executor 镜像 Go 版本不一致
问题等级：P3
所属模块：agent 交付
问题描述：agent/Dockerfile 构建时 `GOOS=linux GOARCH=amd64` 写死；同仓 k8s-executor/Dockerfile 用 1.22 而 agent 用 1.26。
代码位置：agent/Dockerfile:33-35；k8s-executor/Dockerfile:2
涉及函数或类：—
调用链：docker build → go build ./cmd/agent
根本原因：为简化构建
可能影响：ARM 集群（如鲲鹏/Apple Silicon 测试集群）无法使用该镜像；两个二进制由不同 Go 版本编译，行为可能细微不同
修改建议：用 buildx `--platform` 或 `ARG TARGETARCH` 传 GOARCH；统一 Go 版本
验证方法：`docker buildx build --platform linux/arm64` 成功
是否需要立即处理：否
```

```
问题编号：DEP-13
问题名称：生产镜像包含编译期工具 grpcio-tools；容器启动即跑 alembic（root）
问题等级：P3
所属模块：镜像精简/启动流程
问题描述：requirements.txt 含 grpcio-tools（仅代码生成需要，stub 已入库）；api-server 容器 CMD 为 `alembic upgrade head && uvicorn ...`，迁移在应用进程前以 root 执行。
代码位置：api-server/requirements.txt:16；api-server/Dockerfile:17
涉及函数或类：—
调用链：docker run → alembic upgrade head → uvicorn
根本原因：单镜像兼顾迁移与运行
可能影响：镜像体积/攻击面增大；多副本同时启动时并发跑迁移（`migrate-job.yaml` 已单列，但 Dockerfile 仍内置）
修改建议：grpcio-tools 移入 requirements-dev；镜像启动不再跑迁移，迁移统一由 `k8s/migrate-job.yaml`
验证方法：镜像内 `pip show grpcio-tools` 不存在；Pod 重启不再触发 alembic
是否需要立即处理：否
```

---

## 12. 无法确认事项

1. 所有「需联网核实」的 CVE（§5.2）——本机禁止联网且无审计工具，**未核实**。
2. `go.mod` 中 `k8s.io/* v0.36.3`、`golang.org/x/crypto v0.58.0` 等版本号高于我在训练数据中见过的公开版本，无法判断是否为私有 fork 或未来版本 → 需 `go version -m` 与 `govulncheck`。
3. `frontend/package-lock.json` 的 330 条 integrity 是否与 npm registry 当前内容一致（是否被本地篡改）→ 需 `npm ci --dry-run` + 联网。
4. `k8s/generated/` 目录在本机是否存在真实密钥文件（目录被忽略但可能在磁盘上）→ 本次只确认「未被 git 跟踪」，未列目录内容（避免读取真实密钥）。

---

## 13. 审计覆盖范围

**已读全文**：`api-server/requirements.txt`、`ai-service/requirements.txt`、`k8s-executor/go.mod`（含间接依赖）、`frontend/package.json`、`frontend/package-lock.json`（脚本解析 331 个包）、5 个 Dockerfile（api/ai/frontend/executor/agent）、`.dockerignore`、`docker-compose.yml`、`.github/workflows/ci.yml`、`k8s/deployments.yaml`（镜像/env/资源段）、`k8s/secret.example.yaml` 头部、`k8s/networkpolicy.yaml` 头部、`.gitignore`。
**已执行**：`pip list`（两个 venv）、`pip show`（版本比对）、`go.sum` 行数、`git ls-files`/`git check-ignore`、密钥 grep 扫描（高熵 + 前缀模式）、`grep securityContext/USER/FROM/PROXY`。
**未执行**：`pip-audit`、`npm audit`、`govulncheck`、`syft`/`trivy`（均未安装且不允许安装）、任何在线 CVE 库查询、镜像实际拉取与扫描（无 docker 操作授权）。
