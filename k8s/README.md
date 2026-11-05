# k8s/ 部署清单

配置只有**一个事实源**：项目根 `.env`（开发态）→ 生成器 → ConfigMap/Secret（集群态）。

```bash
./scripts/gen-k8s-config.sh --check     # 生成 + 覆盖度校验 + YAML 校验
```

| 文件 | 内容 | 是否进 git |
|---|---|---|
| `configmap.yaml` | 非敏感配置**模板**（占位 `REPLACE_ME`） | 是 |
| `secret.example.yaml` | 敏感配置模板（占位值） | 是 |
| `generated/configmap.yaml` | 从 `.env` 生成的真实值 | **否**（gitignore） |
| `generated/secret.yaml` | 从 `.env` 生成的真实密钥 | **否**（gitignore） |
| `deployments.yaml` | 4 个 Deployment（envFrom ConfigMap + Secret） | 是 |
| `services.yaml` | 服务发现（含 agent 网关 9095、日志流 9099） | 是 |
| `pvc-agent-ca.yaml` | agent 隧道 mTLS 的 CA 持久卷 | 是 |
| `hpa.yaml` / `pdb.yaml` / `networkpolicy.yaml` / `ingress.yaml` | 运维策略 | 是 |
| `backup-cronjob.yaml` | 每日 MySQL 备份（另需 `autohealops-backup-secret`） | 是 |

## 部署顺序

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/pvc-agent-ca.yaml
kubectl apply -f k8s/generated/configmap.yaml
kubectl apply -f k8s/generated/secret.yaml
kubectl apply -f k8s/deployments.yaml
kubectl apply -f k8s/services.yaml
kubectl apply -f k8s/hpa.yaml
kubectl apply -f k8s/pdb.yaml
kubectl apply -f k8s/networkpolicy.yaml
kubectl apply -f k8s/ingress.yaml
```

## 三个硬约束

1. **api-server 单副本**：agent 网关连接表在进程内，多副本会丢隧道调用（要 HA 得先拆网关）。
2. **agent CA 必须持久化**：丢了所有 agent 证书失效。
3. **`AGENT_GATEWAY_PUBLIC_ADDR` 变更后必须重启 api-server**：服务端证书 SAN 在启动时重签，
   否则 agent 报 `certificate is valid for <旧地址>, not <新地址>`。

跨集群接入时 9095 需要对外（NodePort / LB / nginx-SamWaf TCP 四层），详见 `docs/DEPLOYMENT.md`。
