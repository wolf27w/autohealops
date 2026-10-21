# 平台发布与回滚（M10）

> 整理：小吴　2026-10-15　对象：AutoHealOps 平台自身（api-server / ai-service / k8s-executor / frontend）

## 1. 容器化

四个服务各有 Dockerfile：`api-server/Dockerfile`、`ai-service/Dockerfile`、`k8s-executor/Dockerfile`、`frontend/Dockerfile`。
本地/单机可用 `docker-compose.yml` 一键起（依赖外部 MySQL/Redis `192.168.6.99`）。

K8s 侧清单在 `k8s/`：`deployments.yaml`（4 个 Deployment + 探针 + 反亲和）、`services.yaml`、`ingress.yaml`、`configmap.yaml`、`secret.yaml`、`hpa.yaml`、`pdb.yaml`、`networkpolicy.yaml`。

## 2. 发布（滚动）

```bash
# 全量发布（版本 tag 必填，禁止 latest —— 回滚靠唯一 tag）
./scripts/release_platform.sh --version v2026.10.15

# 只发部分服务 / 干跑 / 仅改镜像（镜像已 build+push 好）
./scripts/release_platform.sh --version v2026.10.15 --services api-server,frontend
./scripts/release_platform.sh --version v2026.10.15 --dry-run
./scripts/release_platform.sh --version v2026.10.15 --skip-build
```

脚本动作：`build → push → kubectl set image → rollout status`；发布前把各 Deployment 当前镜像追加到 `.deploy-history`（回滚对照）。

环境变量：`PLATFORM_REGISTRY`（镜像前缀，默认 `api-ops`）、`PLATFORM_NAMESPACE`（默认 `api-ops`）、`CONTAINER_ENGINE`（podman/docker 自动探测）。

## 3. 回滚

```bash
./scripts/rollback_platform.sh                       # 全部服务回滚上一版
./scripts/rollback_platform.sh --service api-server  # 单服务
./scripts/rollback_platform.sh --service api-server --to-revision 3
./scripts/rollback_platform.sh --status              # 只看历史，不动作
```

原理：`kubectl rollout undo` 依赖 Deployment 的 ReplicaSet 历史（`revisionHistoryLimit: 5`）。

## 4. 发布策略（k8s/deployments.yaml）

| 服务 | strategy | 说明 |
|---|---|---|
| api-server (3) | RollingUpdate maxUnavailable=0 / maxSurge=1 | 不中断、逐台替换 |
| ai-service (2) | RollingUpdate 同上 | 同上 |
| frontend (2) | RollingUpdate 同上 | 同上 |
| **k8s-executor (1)** | **Recreate** | 有状态 watcher（单写者），避免新旧同时 watch 双写 |

- `revisionHistoryLimit: 5`：保留 5 个历史 ReplicaSet 供回滚。
- `progressDeadlineSeconds: 600`：滚动超时判定。

## 5. 上线前 checklist

1. 镜像 tag 唯一（非 latest），推送到镜像仓库；
2. Secret/ConfigMap 就绪（含 `ENCRYPTION_KEY`、`JWT_SECRET_KEY` 等）；
3. 数据库已迁移：`kubectl -n api-ops exec deploy/api-server -- alembic upgrade head`；
4. 备份完成（见 `docs/ops/BACKUP-DR.md`）；
5. `rollout status` 全绿；`/ready` 探针通过。

## 6. 待办 / 说明

- CI（`.github/workflows/ci.yml`）当前只做校验（编译/测试）；**镜像构建+推送的流水线**建议按部署环境接 Jenkins/GitLab CI（需仓库凭据）。
- 灰度/金丝雀发布（按比例引流）未做，如需可在 ingress 层加权重。
- 本文档为流程与脚本，**未对任何集群执行**；实际发布由运维在目标环境按上述步骤操作。
