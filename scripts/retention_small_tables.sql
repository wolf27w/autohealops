-- 小表保留策略（2026-11-23，替代分区方案）
--
-- 背景：M4/D2 复核结果（`scripts/check_partitions.py` 2026-11-23）
--   resource_snapshots  已分区（12 个）8.08M 行
--   pod_metrics         已分区（6 个）395K 行
--   quota_snapshots     未分区，仅 14.7K 行
--   slow_sql_records    未分区，仅 11.2K 行
--
-- 结论：后两张表**不做分区**——
--   分区要求所有唯一键包含分区列，而两表主键都是 id，
--   改成 (id, snapshot_time)/(id, bucket_at) 会影响 ORM 主键语义与既有代码，
--   收益（2 万行）远小于风险。改为「保留策略」：定期删旧数据即可控。
--
-- 执行方式：由运维窗口手动执行或挂到 cronjob（先 --dry-run 看影响行数）。
-- 建议保留期：quota_snapshots 30 天；slow_sql_records 90 天。

-- 1) 先看影响行数（只读）
SELECT COUNT(*) AS quota_old_rows
FROM quota_snapshots
WHERE snapshot_time < DATE_SUB(NOW(), INTERVAL 30 DAY);

SELECT COUNT(*) AS slowsql_old_rows
FROM slow_sql_records
WHERE bucket_at < DATE_SUB(NOW(), INTERVAL 90 DAY);

-- 2) 清理（确认行数后再执行；分批删避免长事务）
DELETE FROM quota_snapshots
WHERE snapshot_time < DATE_SUB(NOW(), INTERVAL 30 DAY)
ORDER BY id
LIMIT 5000;

DELETE FROM slow_sql_records
WHERE bucket_at < DATE_SUB(NOW(), INTERVAL 90 DAY)
ORDER BY id
LIMIT 5000;

-- 3) 清理后回收空间（可选，窗口内执行）
-- OPTIMIZE TABLE quota_snapshots;
-- OPTIMIZE TABLE slow_sql_records;
