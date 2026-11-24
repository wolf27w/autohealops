# 遗留项处置说明（2026-11-23）

> **2026-11-24 更新：R49 + P4 已在测试集群 192.168.6.36 实测完成**，期间发现并修复 3 个阻断缺陷（详见文末「2026-11-24 实测结果」一节）。

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

---

## 2026-11-24 实测结果（测试集群 192.168.6.36）

### 部署形态

| 资源 | 结果 |
|---|---|
| api-server | 2 副本（HPA 2~5，修完缺陷后 CPU 25% 回落） |
| ai-service / frontend | 各 2 副本 |
| agent-gateway | 1 副本（`Recreate`），mTLS + token 校验 |
| k8s-executor | 1 副本 |
| Service | `api-server-agent-gateway` NodePort **9095→30905**；`frontend` NodePort **80→30090**（30080 已被 archery 占用） |
| 配置 | `AGENT_GATEWAY_MODE=remote`、`AGENT_GATEWAY_INTERNAL_ADDR=agent-gateway:9096`、`AGENT_GATEWAY_PUBLIC_ADDR=192.168.6.36:30905` |

### R49 四项验收

1. **多副本跑起来** ✅ api-server/ai-service/frontend 多副本全部 Ready，无 CrashLoop。
2. **agent 只连一条** ✅ agent 只有一个 `-platform=192.168.6.36:30905`，连到 agent-gateway；DB `agent_status=connected`、`agent_last_seen_at` 持续刷新。
3. **一条告警只处理一次** ✅ 多副本运行窗口内：60 分钟 12 条诊断、按 alert_id 分组**重复 0 条**（Redis 单飞锁生效）。
4. **容器日志/终端经 agent-gateway 转发** ✅ `GET /api/v1/resources/clusters/999025/namespaces/autohealops/pods/<pod>/logs/download` 返回真实日志（gzip，解压后是 k8s-executor watcher 日志）；api-server 日志 6 处「由独立 agent-gateway 服务承载」，ERROR 0 条。

### 期间发现并修复的缺陷（2026-11-24，commit 见 git log）

| 缺陷 | 现象 | 根因 | 修法 |
|---|---|---|---|
| **A. AppleDouble 污染镜像** | api-server CrashLoop：`alembic … SyntaxError: source code string cannot contain null bytes` | macOS 打包把 `._*.py` 元数据带进 `/app/alembic/versions/`，alembic 当迁移脚本加载 | 新增 5 个 `.dockerignore`（`**/._*`、`**/__pycache__`）；重建镜像后实测镜像内 `._*` = 0 |
| **B. `AGENT_GATEWAY_MODE` 不生效** | 设 `remote` 仍走进程内网关 → 实时日志/终端全失败，后台 ERROR 循环把 CPU 打满、HPA 扩到 5 | `Settings` 未声明该字段且 `model_config: extra="ignore"`，pydantic 直接丢弃环境变量 | `config.py` 补 `AGENT_GATEWAY_MODE` / `AGENT_GATEWAY_INTERNAL_ADDR` 两个字段；补进 `gen_k8s_config.py` 的 `API_CONFIGMAP` |
| **C. 多副本并发迁移** | 每个副本启动都跑 `alembic upgrade head` → 抢 DDL 锁、Pod 长时间不 Ready、CPU 490m+ | 镜像 CMD 自带迁移 | Deployment 加 `RUN_MIGRATIONS=false` 分支 + 新增 `k8s/migrate-job.yaml` 单独跑迁移 |

| **D. 告警自动处理必然抛异常** | 多副本日志每轮刷 `告警自动处理 异常: cannot access local variable 'AIDiagnosisResult' where it is not associated with a value`，CPU 被空转打高 | `_process_one()` 里有一处局部 `from app.models import AIDiagnosisResult`，把该名字变成函数局部变量 → 前面第 1740 行先用后赋值（UnboundLocalError） | 删掉该局部导入（模块级已导入），AST 校验函数内无同名导入 |

> 顺带修掉：`api-server/requirements.txt` 里 `cryptography==43.0.1` 与 `asyncssh==2.24.1`（要求 >=48.0.1）冲突 → **镜像根本构建不出来**，已改为 `50.0.2`（本机 venv 同版本，crypto 互测通过）。

### 镜像

`harbor.efreight.cn/ops/autohealops-api-server:20261124`（含上面三处修复；agent-gateway 复用同一镜像）。
