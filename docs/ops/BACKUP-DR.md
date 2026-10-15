# 备份与 DR（灾难恢复）—— API-OPS 平台

> 整理：小吴　2026-10-15　对应需求：M11

## 1. 备份什么

| 资产 | 位置 | 备份方式 |
|---|---|---|
| **MySQL 库 `api_ops`** | 192.168.6.99:3306 | `scripts/backup_mysql.sh` / `k8s/backup-cronjob.yaml`（每日 2:00） |
| **ENCRYPTION_KEY** | `.env` / k8s Secret | **必须与备份同存**（否则库内 kubeconfig、渠道密码、日志源密码等密文解不开） |
| Redis | 192.168.6.99:6379 | 非权威数据（锁/缓存/代次），**可不备份**，但需确认持久化开启（见 §4） |
| 平台代码 / 配置 | 仓库 + `k8s/` | Git 版本控制；Secret 单独保管 |

## 2. 立即备份（手动 / 本地或跳板机）

```bash
./scripts/backup_mysql.sh            # 执行一次：mysqldump → gzip → ./backups/，保留 7 天
./scripts/backup_mysql.sh --dry-run  # 只看将做什么，不落盘
BACKUP_REMOTE=oss:apiops-backup/ ./scripts/backup_mysql.sh   # 顺带 rclone 异地（需装 rclone）
```

- 连接参数：优先 `MYSQL_HOST/MYSQL_PORT/MYSQL_USER/MYSQL_PASSWORD/MYSQL_DB` 环境变量，其次解析 `.env` 的 `DATABASE_URL`。
- 产物：`backups/api_ops-YYYYmmdd-HHMMSS.sql.gz` + 同名 `.keyinfo.json`（记录 ENCRYPTION_KEY 指纹）。
- 保留：`BACKUP_KEEP_DAYS`（默认 7）天，超期自动清理（`--dry-run` 不清理）。

## 3. K8s 定时备份

`k8s/backup-cronjob.yaml`（含 PVC + CronJob）：
- 先建 Secret（含 DB 连接与 `ENCRYPTION_KEY`）与 PVC（见文件头注释）。
- 产物落 **PVC**（持久），保留 7 天；异地副本按需在 command 里加 `ossutil/rclone`。

## 4. 恢复步骤

```bash
# 1) 准备：同版本的 ENCRYPTION_KEY（比对备份 .keyinfo.json 的 sha256 前 16 位）
gunzip -c backups/api_ops-YYYYmmdd-HHMMSS.sql.gz | mysql -h <host> -u <user> -p api_ops
# 2) 确认 .env / k8s Secret 的 ENCRYPTION_KEY 与备份时一致
# 3) 重启 api-server，验证：登录、集群 kubeconfig 可用、渠道 webhook 可发
```

## 5. Redis 持久化确认

```bash
./scripts/check_redis_persistence.sh
```
- 输出 `appendonly=yes` 或 `save` 非空 → 已持久化（✅）；都为否 → 建议开启 AOF（`CONFIG SET appendonly yes` + 写入 `redis.conf`）。
- 注：Redis 存的是锁/缓存/仪表盘代次等**可重建**数据，丢了对业务影响有限，但保留持久化更稳。

## 6. 待办 / 建议

- 异地备份：当前脚本支持 `BACKUP_REMOTE`（rclone）钩子，**对象存储桶与凭据需运维配置**。
- 备份校验：建议每月做一次「恢复到临时库」演练，验证备份可用（尚未自动化）。
- 保留策略当前 7 天，按合规要求可调（`BACKUP_KEEP_DAYS`）。
