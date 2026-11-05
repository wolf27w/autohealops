# 部署指南

## 方式一：Docker Compose（推荐，单机）

### 前置

- Docker + Docker Compose
- 外部中间件：MySQL + Redis（本环境 192.168.6.99，已就绪）

### 步骤

1. 配置 `.env`（复制 `.env.example` 填入真实值）

```bash
cp .env.example .env
vim .env   # 填 ENCRYPTION_KEY / JWT_SECRET_KEY / AI 模型 Key 等
```

2. 初始化数据库（api-server 是 schema owner，用 alembic 建表）

```bash
cd api-server
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/alembic upgrade head
```

3. 构建并启动

```bash
docker compose up -d --build
```

4. 验证

```bash
curl http://localhost:8080/health
# 前端 http://localhost
```

### 首次启动注意

- `ai-service` 镜像构建时安装 sentence-transformers（含 torch），体积较大、构建较慢，属正常。
- k8s-executor 启动时从数据库加载 kubeconfig（加密），需 `ENCRYPTION_KEY` 正确。

## 方式二：Kubernetes

### 配置注入原则（务必先看）

**Pod 里没有 `.env` 文件**。三个服务都优先读环境变量（pydantic-settings / viper），
所以集群部署只需要 ConfigMap + Secret：

- **非敏感项** → ConfigMap（`k8s/configmap.yaml`）
- **敏感项**（连接串里的密码、JWT/加密密钥、仓库凭据）→ Secret（`autohealops-secrets`）
- Deployment 一律 `envFrom` 整包注入，**不要**在 Deployment 里逐个写 `env`（漏一项就会静默用默认值）

实际部署用生成器，避免「.env 改了、集群配置没跟上」：

```bash
./scripts/gen-k8s-config.sh           # 从项目根 .env 生成 k8s/generated/{configmap,secret}.yaml
./scripts/gen-k8s-config.sh --check   # 生成 + YAML/API 校验
```

生成器会做**覆盖度校验**：把 `api-server/ai-service` 的 `Settings` 字段和 Go 端
`v.GetString(...)` 全部解析出来，逐个确认在生成物里有归属，缺一项直接报错退出。
生成物在 `k8s/generated/`（`.gitignore` 忽略）——真实密钥不进 git。

### 清单说明

`k8s/` 目录：

| 文件 | 说明 |
|---|---|
| namespace.yaml | autohealops 命名空间 |
| configmap.yaml | 三个服务各自的非敏感配置（含集群内 Service 地址） |
| secret.example.yaml | 敏感配置**模板**（占位值，不能直接上线） |
| deployments.yaml | 4 个 Deployment（探针 + envFrom + agent CA 卷） |
| services.yaml | 各服务 Service（含 agent 网关 9095、日志流 9099） |
| pvc-agent-ca.yaml | 平台自建 CA 持久卷（agent 隧道 mTLS 用） |
| ingress.yaml | 入口（/ → frontend，/api/v1 → api-server） |
| hpa.yaml | ai-service 自动扩缩容（api-server 暂不做 HPA，见下） |
| pdb.yaml | Pod 中断预算 |
| networkpolicy.yaml | 网络策略（ai-service/executor 只允许 api-server 入站） |
| backup-cronjob.yaml | 每日备份 |

### 关键部署约束

1. **api-server 固定 1 副本**：agent 反向隧道网关的连接注册表在进程内
   （`api-server/app/agent_gateway.py` 的 `_agents`）。多副本时 agent 只挂在其中一个 Pod，
   其它副本转发不到 → 调用失败。要 api-server 多副本 HA，必须先把网关拆成独立 Deployment（未实施）。
   因此 `strategy` 用 `Recreate`（agent CA 是 RWO 卷，滚动更新会卡在挂载上）。
2. **k8s-executor 固定 1 副本**：有状态 watcher，多副本会重复 watch 同一集群导致告警重复，**不配置 HPA**。
3. **`autohealops-agent-ca` PVC 必须持久化**：CA 私钥丢了 → 所有已下发 agent 的客户端证书失效，
   要逐集群重新拉 install.yaml。
4. **agent 网关地址（`AGENT_GATEWAY_PUBLIC_ADDR`）是 mTLS 证书 SAN 的来源**：
   改地址后必须重启 api-server 才会重签服务端证书，否则 agent 报
   `certificate is valid for <旧地址>, not <新地址>`。
5. **数据库 migration 由 api-server 统一管理**：api-server 启动时执行 `alembic upgrade head`。
6. **备份必须包含 ENCRYPTION_KEY**：否则恢复后 kubeconfig 密文无法解密。

### 部署

```bash
./scripts/gen-k8s-config.sh --check

kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/pvc-agent-ca.yaml
kubectl apply -f k8s/generated/configmap.yaml
kubectl apply -f k8s/generated/secret.yaml
kubectl apply -f k8s/deployments.yaml
kubectl apply -f k8s/services.yaml
kubectl apply -f k8s/hpa.yaml -f k8s/pdb.yaml -f k8s/networkpolicy.yaml
kubectl apply -f k8s/ingress.yaml
```

> `k8s/configmap.yaml` 是人工维护的模板（占位 `REPLACE_ME`），
> `k8s/generated/configmap.yaml` 是从本机 `.env` 生成的真实值版本。
> **只 apply 其中一个**（同名 ConfigMap，后 apply 的覆盖前一个）；推荐用 generated。

### 对外暴露 agent 网关（9095）

被管集群的 agent 主动连平台 9095，所以这个端口必须让外部集群可达，三选一：

1. `type: NodePort`（简单，如 `30905`；`AGENT_GATEWAY_PUBLIC_ADDR=<节点IP>:30905`）
2. `type: LoadBalancer`（云环境）
3. 保持 ClusterIP + 外层 nginx/SamWaf 做 **TCP 四层**转发（不要 HTTP 反代，gRPC 长连接）

## 备份与恢复

### 备份

- 定时备份：`k8s/backup-cronjob.yaml`（每日 2:00 mysqldump 到对象存储）
- **关键**：备份包必须同时包含 `ENCRYPTION_KEY`（单独安全存储，或备份包内加密存放）

### 恢复流程（顺序重要）

1. **先恢复 ENCRYPTION_KEY**（否则 kubeconfig 密文解不开）
2. 恢复 MySQL 数据

```bash
mysql -uroot -p autohealops < backup.sql
```

3. 重启服务

```bash
kubectl rollout restart deployment/api-server
kubectl rollout restart deployment/k8s-executor
kubectl rollout restart deployment/ai-service
```

## 中间件

| 中间件 | 地址 | 说明 |
|---|---|---|
| MySQL | 192.168.6.99:3306 | 库 autohealops（utf8mb4），root/root |
| Redis | 192.168.6.99:6379 | 密码见 .env |

> 生产环境建议：应用使用专用 MySQL 账号（仅业务库权限），中间件密码改强密码。

## 加密密钥管理

- `ENCRYPTION_KEY` 用于加密 kubeconfig，**一旦定下不可更改**（否则已存 kubeconfig 解不开）。
- 跨语言格式（Python ↔ Go）严格一致，改动加密实现后必须通过 `make crypto-test`。
- 详见 [docs/ENCRYPTION.md](ENCRYPTION.md)。

## Agent 隧道 PKI（mTLS）持久化与备份（2026-11-04）

平台自建 CA 用于签发网关服务端证书和每个 agent 的客户端证书，**必须持久化并备份**，否则平台重装/换容器后：

- 网关会生成新 CA → 所有已装 agent 的客户端证书全部失效（握手被拒）；
- 只能重建 CA 并给每个集群**重新下发 install.yaml**。

### 1. 持久化

| 部署形态 | 做法 |
|---|---|
| 本机 / 物理机 | 保持 `data/agent-ca/` 在磁盘上即可（默认路径）；建议放到数据盘 |
| Docker | `-v /data/autohealops/agent-ca:/app/data/agent-ca` |
| Kubernetes | 挂 PVC 到 `/app/data/agent-ca`（重启/滚动发布不丢） |

路径可用 `AGENT_CA_DIR` 覆盖（相对路径按仓库根解析）。启动时若发现 CA 是**新建**的，日志会打 `ERROR`：说明 CA 丢了，需重新下发 agent。

### 2. 备份（建议纳入定时任务）

```bash
./scripts/backup_agent_ca.sh                 # 默认 ~/autohealops-backup/agent-ca
./scripts/backup_agent_ca.sh /mnt/nas/ah-ca  # 指定异地目录
```

产物是 `agent-ca-<时间戳>.tgz`（600），含 `ca.crt/ca.key/server.crt/server.key`。
**恢复**：`tar -xzf agent-ca-<ts>.tgz -C <仓库根>/data`（保持目录名 `agent-ca`），重启 api-server。
`ca.key` 属于最高敏感级，别放 git、别进普通文件共享。

### 3. 接入前置（常见卡点）

- **镜像拉取凭据**：`AGENT_IMAGE_PULL_SECRET`（默认 `autohealops`）。
  - 平台配了 `AGENT_REGISTRY_USERNAME` / `AGENT_REGISTRY_PASSWORD` → 生成的 `install.yaml` **自动创建**该 docker-registry Secret；
  - 未配 → 要求目标集群 agent 命名空间里**已存在**同名 Secret，否则 Pod `ImagePullBackOff`。
  ```bash
  kubectl -n default get secret autohealops -o yaml \
    | sed 's/namespace: default/namespace: autohealops-agent/' | kubectl apply -f -
  ```
- **命名空间**：`AGENT_NAMESPACE`（默认 `autohealops-agent`）。若目标集群已有现成的拉取 Secret 环境，可改成那个命名空间，省一次复制。
- **install.yaml 是匿名的**：集群侧 `kubectl apply -f <平台地址>/api/v1/clusters/<id>/agent/install.yaml?token=<token>` 不需要平台账号，只校验接入令牌（可吊销）。返回裸 YAML + 正确状态码（令牌错 → 403）。
