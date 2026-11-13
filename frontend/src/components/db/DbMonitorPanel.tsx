// 单实例数据库监控面板（详情页「监控」Tab）
// 仅指标 + 趋势，不含慢 SQL / 异常列表（由其它面板负责）
import { useEffect, useState } from 'react'
import { Card, Table, Tag, Empty, Spin, Select, Space } from 'antd'
import { Line } from '@ant-design/charts'
import { getDbLatestMetrics, getDbTrends } from '../../services/api'

interface Metric {
  metric_type: string
  metric_name: string
  value: number
  detail: string
  sampled_at: string
}
interface TrendPoint { metric_name: string; metric_type?: string; series?: string; value: number; sampled_at: string }

interface Props {
  connId: number
  dbType?: string
}

const TYPE_LABEL: Record<string, string> = {
  connections: '连接', throughput: '吞吐', network: '网络', cache: '缓存/缓冲池',
  query_quality: '查询质量', innodb: 'InnoDB', tablespace: '表空间/文件',
  config: '参数配置', lock_wait: '锁等待', replication: '主从复制', rate: '速率', memory: '内存',
  vacuum: '自动清理', wait: '等待事件', info: '基本信息',
}
const TYPE_COLOR: Record<string, string> = {
  connections: 'blue', throughput: 'geekblue', network: 'cyan', cache: 'green',
  query_quality: 'orange', innodb: 'purple', tablespace: 'magenta', config: 'default',
  lock_wait: 'red', replication: 'gold', rate: 'lime', memory: 'volcano', info: 'default',
  vacuum: '#87d068', wait: '#c41d7f',
}

const NAME_LABEL: Record<string, string> = {  connections_used: '当前连接数', max_connections: '最大连接数', connection_usage_pct: '连接使用率(%)',
  active_connections: '活跃连接', threads_cached: '线程缓存', threads_created: '已创建线程',
  aborted_connects: '累计连接失败(次)', aborted_clients: '累计客户端异常断开(次)', max_used_connections: '历史最大连接', thread_cache_size: '线程缓存大小',
  queries_total: '总查询数', tps_total: '事务累计', qps: 'QPS', tps: 'TPS',
  buffer_pool_hit_rate: '缓冲池命中率(%)', buffer_pool_size_mb: '缓冲池大小(MB)',
  buffer_pool_usage_pct: '缓冲池使用率(%)', buffer_pool_dirty_pages: '脏页数', innodb_log_waits: '日志等待',
  slow_queries: '慢查询累计', slow_queries_per_sec: '慢查询/秒', select_scan_total: '全表扫描次数',
  handler_read_rnd_next: '无索引读行', tmp_tables: '临时表', tmp_disk_tables: '磁盘临时表',
  tmp_disk_tables_pct: '磁盘临时表占比(%)', sort_merge_passes: '排序合并', table_locks_waited: '表锁等待',
  row_lock_waits: '行锁等待', row_lock_time_avg_ms: '行锁平均等待(ms)', row_lock_current_waits: '当前行锁等待',
  deadlocks: '死锁', db_size_mb: '库大小(MB)', table_count: '表数量', top_tables: 'Top大表(MB)',
  open_tables: '打开表', table_open_cache_usage_pct: '表缓存使用率(%)', open_files: '打开文件',
  opened_tables: '已打开表累计', long_query_time: '慢查询阈值(s)', slow_query_log: '慢查询日志(1已开)',
  innodb_buffer_pool_size_mb: '缓冲池配置(MB)', slave_running: '从库运行(1正常)', replication_lag_seconds: '复制延迟(s)',
  lock_wait_count: '锁等待数', version: '数据库版本', cache_hit_pct: '缓存命中率(%)', bytes_received: '累计接收字节', bytes_sent: '累计发送字节',
  rx_bytes_per_sec: '接收速率(B/s)', tx_bytes_per_sec: '发送速率(B/s)',
  aborted_connects_per_sec: '连接失败速率(/s)', aborted_clients_per_sec: '客户端异常断开速率(/s)',
  com_select: 'SELECT 次数', com_insert: 'INSERT 次数', com_update: 'UPDATE 次数', com_delete: 'DELETE 次数',
  com_commit: 'COMMIT 次数', com_rollback: 'ROLLBACK 次数',
  mysql_memory_total_mb: 'MySQL 进程内存(MB)', innodb_buffer_pool_data_mb: '缓冲池已用(MB)',
  // PostgreSQL 健康指标
  idle_connections: '空闲连接', idle_in_transaction: '空闲事务连接', waiting_connections: '等待连接',
  commits_total: '提交次数', rollbacks_total: '回滚次数', rollback_pct: '回滚率(%)',
  blks_hit: '缓存命中块', blks_read: '磁盘读取块', checkpoint_timed: '定时检查点', checkpoint_req: '请求检查点',
  conflicts: '冲突', temp_files: '临时文件数', temp_bytes: '临时文件字节', long_running_queries: '长查询数',
  index_size_mb: '索引大小(MB)', blocked_queries: '被阻塞查询数', is_in_recovery: '恢复模式(1是)',
  replication_lag_sec: '复制延迟(s)', dead_tuples: '死元组', live_tuples: '活跃元组',
  tables_needing_vacuum: '待清理表数', autovacuum_count: '自动清理次数', last_vacuum_age_sec: '上次清理距今(s)',
  shared_buffers_mb: '共享缓冲(MB)', work_mem_mb: '工作内存(MB)', max_worker_processes: '最大工作进程数',
  effective_cache_size_mb: '有效缓存(MB)', temp_buffers_mb: '临时缓冲(MB)',
  // Oracle 健康指标
  sessions_total: '会话总数', sessions_active: '活跃会话', sessions_inactive: '非活跃会话',
  sessions_usage_pct: '会话使用率(%)', processes_used: '已用进程数', processes_limit: '进程上限',
  processes_usage_pct: '进程使用率(%)', executes_total: '执行次数', user_commits_total: '用户提交次数',
  user_rollbacks_total: '用户回滚次数', qps_total: '查询累计',
  buffer_cache_hit_rate: '缓冲缓存命中率(%)', library_cache_hit_rate: '库缓存命中率(%)',
  dictionary_cache_hit_rate: '字典缓存命中率(%)', sorts_disk_ratio: '磁盘排序占比(%)',
  physical_reads: '物理读次数', consistent_gets: '一致性读次数', db_block_gets: '当前块读次数',
  parses_total: '解析次数', blocked_sessions: '被阻塞会话数', enqueue_waits: '队列等待次数',
  top_wait_events: 'Top等待事件', total_wait_ms: '总等待(ms)', tablespace_max_pct: '表空间最大使用率(%)',
  tablespace_count: '表空间数量', total_size_mb: '总大小(MB)', top_tablespace: 'Top表空间(MB)',
  sga_size_mb: 'SGA 大小(MB)', pga_size_mb: 'PGA 大小(MB)', sga_free_mb: 'SGA 空闲(MB)',
  dataguard_lag_sec: 'DataGuard 延迟(s)',
}

export default function DbMonitorPanel({ connId, dbType }: Props) {
  const [metrics, setMetrics] = useState<Metric[]>([])
  const [loading, setLoading] = useState(false)
  const [trends, setTrends] = useState<TrendPoint[]>([])
  const [trendType, setTrendType] = useState<string>('all')
  const [trendHours, setTrendHours] = useState<number>(1)

  // 加载最新指标
  useEffect(() => {
    if (!connId) return
    setLoading(true)
    getDbLatestMetrics(connId)
      .then((d) => setMetrics((d || []) as Metric[]))
      .catch(() => setMetrics([]))
      .finally(() => setLoading(false))
  }, [connId])

  // 加载趋势（按 metric_type + 时间范围）
  useEffect(() => {
    if (!connId) return
    getDbTrends(connId, trendType, trendHours)
      .then((d) => {
        const rows = ((d || []) as TrendPoint[]).map((r) => ({ ...r, series: NAME_LABEL[r.metric_name] || r.metric_name }))
        setTrends(rows)
      })
      .catch(() => setTrends([]))
  }, [connId, trendType, trendHours])

  const types = Array.from(new Set(metrics.map((m) => m.metric_type)))
  const sampledAt = metrics[0]?.sampled_at || '-'

  const fmt = (m: Metric) => {
    const v = m.value
    if (m.metric_name.endsWith('_bytes')) return `${(v / 1048576).toFixed(2)} MB`
    if (Number.isInteger(v)) return v.toLocaleString()
    return v
  }

  // 单位（避免把 ms 当次数误读）
  const unitOf = (name: string): string => {
    if (name.endsWith('_pct') || name === 'buffer_pool_hit_rate') return '%'
    if (name.endsWith('_ms')) return 'ms'
    if (name.endsWith('_mb') || name === 'top_tables') return 'MB'
    if (name.endsWith('_seconds') || name.endsWith('_sec') || name === 'long_query_time') return 's'
    if (name.endsWith('_bytes_per_sec')) return 'B/s'
    if (name.endsWith('_bytes')) return 'MB'
    if (name === 'qps' || name === 'tps' || name.endsWith('_per_sec')) return '/s'
    if (name === 'table_count') return '张'
    return '次'
  }

  const metricCols = [
    { title: '监控项', dataIndex: 'metric_name', width: 240, render: (n: string) => NAME_LABEL[n] || n },
    { title: '值', width: 170, render: (_: unknown, m: Metric) => <><b>{fmt(m)}</b> <span style={{ color: '#999', fontSize: 12 }}>{unitOf(m.metric_name)}</span></> },
    { title: '说明/明细', dataIndex: 'detail', ellipsis: true, render: (v: string) => v || '-' },
  ]

  const trendOptions = [{ value: 'all', label: '全部指标' }, ...types.map((t) => ({ value: t, label: TYPE_LABEL[t] || t }))]

  if (loading) {
    return <Spin style={{ display: 'block', margin: '40px auto' }} />
  }
  if (metrics.length === 0) {
    return <Empty description="暂无监控指标数据" />
  }

  return (
    <div>
      <div style={{ marginBottom: 12, fontSize: 12, color: '#8c8c8c' }}>
        {dbType && <Tag color="blue" style={{ marginRight: 8 }}>{dbType}</Tag>}
        采集时间：{sampledAt}（不同数据库类型采集项不同）
      </div>

      {types.map((t) => {
        const items = metrics.filter((m) => m.metric_type === t)
        return (
          <Card key={t} size="small" style={{ marginBottom: 12 }}
            title={<><Tag color={TYPE_COLOR[t] || 'default'}>{TYPE_LABEL[t] || t}</Tag><span style={{ fontSize: 13, color: '#8c8c8c' }}>{items.length} 项</span></>}>
            <Table rowKey="metric_name" size="small" dataSource={items} columns={metricCols} pagination={false} />
          </Card>
        )
      })}

      <Card size="small"
        title={`指标趋势（${trendType === 'all' ? '全部指标' : TYPE_LABEL[trendType] || trendType} · ${trendHours}h · ${new Set(trends.map((t) => t.series)).size} 条线）`}
        extra={<Space size={6}>
          <Select size="small" value={trendType} style={{ width: 140 }} onChange={setTrendType} options={trendOptions} />
          <Select size="small" value={trendHours} style={{ width: 90 }} onChange={setTrendHours}
            options={[{ value: 1, label: '近 1h' }, { value: 6, label: '近 6h' }, { value: 24, label: '近 24h' }]} />
        </Space>}>
        {trends.length === 0
          ? <Empty description="暂无趋势数据（采集几次后形成）" />
          : <Line data={trends} xField="sampled_at" yField="value" seriesField="series" height={320} smooth
              legend={{ color: { position: 'bottom', layout: { justifyContent: 'center' } } }} />}
      </Card>
    </div>
  )
}
