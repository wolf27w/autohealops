# 遗留项处置说明（2026-11-23）

本文件记录四项「非开发类遗留」的核查结论与可执行步骤。

---

## 1. R49 多副本部署验证

### 已完成（代码/清单侧，2026-11-23 实测）

在测试集群（192.168.6.36）执行 `kubectl apply --dry-run=server`，三个清单全部通过：

| 清单 | 结果 |
|---|---|
| `k8s/deployments.yaml` | api-server / agent-gateway / k8s-executor / ai-service / frontend 全部 `created (server dry run)` |
| `k8s/services.yaml` | 6 个 Service 全部通过 |
| `k8s/hpa.yaml` | api-server-hpa（2-5）、ai-service-hpa（2-4）通过 |

### 关键前提已就位（核对过）

- `configmap.yaml`: `AGENT_GATEWAY_MODE=remote`、`AGENT_GATEWAY_INTERNAL_ADDR=agent-gateway:9096` → **api-server 可多副本**（agent 连接表在独立 agent-gateway Deployment，不再是进程内）
- api-server 2 副本、ai-service 2、frontend 2、agent-gateway 1、k8s-executor 1
- `k8s/pdb.yaml` 存在

### 待部署窗口（需要环境）

```bash
kubectl apply -f k8s/namespace.yaml -f k8s/configmap.yaml -f k8s/generated/secret.yaml
kubectl apply -f k8s/deployments.yaml -f k8s/services.yaml -f k8s/hpa.yaml -f k8s/pdb.yaml
kubectl get pods -n autohealops -w
```

冒烟验证清单（多副本必须验这 4 条）：
1. `kubectl get pods` → api-server 2/2、ai-service 2/2、frontend 2/2
2. 集群列表 → agent 集群 `connected`（隧道只挂在 agent-gateway，不应出现两条重复连接）
3. 触发一条告警 → 只处理一次（无重复诊断/重复工单）
4. 容器日志 / 终端 → 正常（两个 api-server 副本都能转发到 agent-gateway）

---

## 2. M4/D2 分区与保留策略

### 复核结果（`scripts/check_partitions.py` 2026-11-23）

| 表 | 状态 |
|---|---|
| `resource_snapshots` | 已分区 12 个（p20261117 ~ pmax），808 万行 |
| `pod_metrics` | **已分区 6 个**，39.5 万行（先前记录的 261 万行已消化） |
| `quota_snapshots` | 未分区，14,744 行 |
| `slow_sql_records` | 未分区，11,228 行 |

### 结论：小表不做分区，改用保留策略

分区要求所有唯一键包含分区列；两张小表主键都是 `id`，要分区必须改成 `(id, snapshot_time)` / `(id, bucket_at)`，会影响 ORM 主键语义。**2 万行的收益不值得这个风险。**

可执行脚本：`scripts/retention_small_tables.sql`（含影响行数查询 + 分批删除 + 可选 OPTIMIZE）。建议 quota 保留 30 天、slow_sql 保留 90 天。

---

## 3. P4 agent 网关对外（9095）

`k8s/services.yaml` 当前是 ClusterIP。三种对外方式，按环境选一个：

### A. NodePort（最简单）

```bash
kubectl -n autohealops patch svc agent-gateway -p '{"spec":{"type":"NodePort","ports":[{"name":"tunnel","port":9095,"targetPort":9095,"nodePort":30905}]}}'
```
然后 `k8s/configmap.yaml`：
```yaml
AGENT_GATEWAY_PUBLIC_ADDR: "<任一节点IP>:30905"
```
> ⚠️ **改完必须重启 api-server**（或 agent-gateway）：服务端证书 SAN 在网关启动时按该地址重签，否则 agent 报 `certificate is valid for <旧地址>, not <新地址>`。

### B. LB / Ingress TCP（云上）

- 阿里云 SLB → 后端 9095；或 Nginx stream 四层转发
- 同样把 LB 域名:端口写进 `AGENT_GATEWAY_PUBLIC_ADDR`

### C. 已有 SamWaf/Nginx 四层

在现有反代上新增 TCP 9095 转发到 `agent-gateway:9095`。

**提醒**：26666/SamWaf 管理口曾有公网可达问题，9095 对外时建议限制源 IP（只允许被管集群网段）。

---

## 4. A6 平台自身生产部署

材料已齐：`docs/DEPLOYMENT.md`、`k8s/backup-cronjob.yaml`、`scripts/backup_agent_ca.sh`、`scripts/backup_mysql.sh`、`pvc-agent-ca.yaml`。

上线前检查清单：

- [ ] `./scripts/gen-k8s-config.sh` 生成 configmap/secret（覆盖度校验通过）
- [ ] `AGENT_GATEWAY_PUBLIC_ADDR` 填真实域名/IP，生成后重启 api-server
- [ ] `autohealops-agent-ca` PVC 使用持久化存储（**丢了所有 agent 证书失效**）
- [ ] api-server 不超过清单副本（2），确认 `AGENT_GATEWAY_MODE=remote`
- [ ] 备份 Job 已启用（MySQL 每日 + CA 每日）
- [ ] 生产集群直连（kubeconfig），不套 agent（2026-11-20 老板口径）
- [ ] 告警通知渠道（飞书/邮件）在生产配置里验证一条
- [ ] 首次部署后跑一遍：登录 → 集群列表 → 容器日志 → 一条告警全链路
