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

### 清单说明

`k8s/` 目录：

| 文件 | 说明 |
|---|---|
| namespace.yaml | autohealops 命名空间 |
| configmap-*.yaml | 各服务环境变量（DATABASE_URL、PROMETHEUS_URL 等） |
| secret.yaml | 敏感配置（ENCRYPTION_KEY、JWT_SECRET_KEY、AI Key）base64 |
| deployment-*.yaml | 各服务 Deployment |
| service-*.yaml | 各服务 Service |
| ingress.yaml | 入口（/ → frontend，/api/v1 → api-server） |
| hpa-*.yaml | api-server/ai-service 自动扩缩容 |
| pdb.yaml | Pod 中断预算 |
| networkpolicy.yaml | 网络策略（ai-service/executor 只允许 api-server 入站） |
| backup-cronjob.yaml | 每日备份 |

### 关键部署约束

1. **k8s-executor 固定 1 副本**：有状态 watcher，多副本会重复 watch 同一集群导致告警重复，**不配置 HPA**。
2. **数据库 migration 由 api-server 统一管理**：api-server 启动时执行 `alembic upgrade head`，k8s-executor/ai-service 启动时不建表。
3. **备份必须包含 ENCRYPTION_KEY**：否则恢复后 kubeconfig 密文无法解密。

### 部署

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap-*.yaml
kubectl apply -f k8s/secret.yaml
kubectl apply -f k8s/deployment-*.yaml
kubectl apply -f k8s/service-*.yaml
kubectl apply -f k8s/ingress.yaml
```

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

- **镜像拉取凭据**：`AGENT_IMAGE_PULL_SECRET=eft-image-auth` 时，该 Secret 必须**已存在于 agent 命名空间**（默认 `autohealops-agent`）。不存在 → Pod `ImagePullBackOff`。
  ```bash
  kubectl -n default get secret eft-image-auth -o yaml \
    | sed 's/namespace: default/namespace: autohealops-agent/' | kubectl apply -f -
  ```
- **命名空间**：`AGENT_NAMESPACE`（默认 `autohealops-agent`）。若目标集群已有现成的拉取 Secret 环境，可改成那个命名空间，省一次复制。
- **install.yaml 是匿名的**：集群侧 `kubectl apply -f <平台地址>/api/v1/clusters/<id>/agent/install.yaml?token=<token>` 不需要平台账号，只校验接入令牌（可吊销）。返回裸 YAML + 正确状态码（令牌错 → 403）。
