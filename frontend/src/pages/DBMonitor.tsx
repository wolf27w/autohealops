// 数据库监控：按数据库切换，展示全部监控指标（分组）+ 指标趋势
import { useEffect, useState } from 'react'
import { Card, Select, Space, Table, Tag, Tabs, Button, Empty, Spin } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { Line } from '@ant-design/charts'
import { getDBConnections, getDbLatestMetrics, getDbTrends } from '../services/api'

interface Metric {
  metric_type: string
  metric_name: string
  value: number
  detail: string
  sampled_at: string
}
interface TrendPoint { metric_name: string; value: number; sampled_at: string }
interface DBConn { id: number; name: string; db_type: string }

const TYPE_LABEL: Record<string, string> = {
  connections: '连接', throughput: '吞吐', network: '网络', cache: '缓存/缓冲池',
  query_quality: '查询质量', innodb: 'InnoDB 锁/事务', tablespace: '表空间/文件',
  config: '参数配置', lock_wait: '锁等待', replication: '主从复制', rate: '速率',
}

const NAME_LABEL: Record<string, string> = {
  connections_used: '当前连接数', max_connections: '最大连接数', connection_usage_pct: '连接使用率(%)',
  active_connections: '活跃连接', threads_cached: '线程缓存', threads_created: '已创建线程',
  aborted_connects: '失败连接', max_used_connections: '历史最大连接', thread_cache_size: '线程缓存大小',
  queries_total: '总查询数', tps_total: '事务累计', qps: 'QPS', tps: 'TPS',
  buffer_pool_hit_rate: '缓冲池命中率(%)', buffer_pool_size_mb: '缓冲池大小(MB)',
  buffer_pool_usage_pct: '缓冲池使用率(%)', buffer_pool_dirty_pages: '脏页数', innodb_log_waits: '日志等待',
  slow_queries: '慢查询累计', slow_queries_per_sec: '慢查询/秒', select_scan_total: '全表扫描次数',
  handler_read_rnd_next: '无索引读行', tmp_tables: '临时表', tmp_disk_tables: '磁盘临时表',
  tmp_disk_tables_pct: '磁盘临时表占比(%)', sort_merge_passes: '排序合并', table_locks_waited: '表锁等待',
  row_lock_waits: '行锁等待', row_lock_time_avg_ms: '行锁平均等待(ms)', row_lock_current_waits: '当前行锁等待',
  deadlocks: '死锁', db_size_mb: '库大小(MB)', table_count: '表数量', top_tables: 'Top大表(MB)',
  open_tables: '打开表', table_open_cache_usage_pct: '表缓存使用率(%)', open_files: '打开文件',
  opened_tables: '已打开表累计', long_query_time: '慢查询阈值(s)', slow_query_log: '慢查询日志(1开)',
  innodb_buffer_pool_size_mb: '缓冲池配置(MB)', slave_running: '从库运行(1正常)', replication_lag_seconds: '复制延迟(s)',
  lock_wait_count: '锁等待数', bytes_received: '接收字节', bytes_sent: '发送字节',
  rx_bytes_per_sec: '接收速率(B/s)', tx_bytes_per_sec: '发送速率(B/s)',
  com_select: 'SELECT', com_insert: 'INSERT', com_update: 'UPDATE', com_delete: 'DELETE',
  com_commit: 'COMMIT', com_rollback: 'ROLLBACK',
}

export default function DBMonitor() {
  const [conns, setConns] = useState<DBConn[]>([])
  const [connId, setConnId] = useState<number>()
  const [metrics, setMetrics] = useState<Metric[]>([])
  const [loading, setLoading] = useState(false)
  const [activeType, setActiveType] = useState<string>('')
  const [trends, setTrends] = useState<TrendPoint[]>([])

  useEffect(() => { getDBConnections().then((d) => setConns((d || []) as DBConn[])) }, [])

  const loadMetrics = (cid?: number) => {
    const id = cid ?? connId
    if (!id) return
    setLoading(true)
    getDbLatestMetrics(id).then((d) => {
      const arr = (d || []) as Metric[]
      setMetrics(arr)
      const types = Array.from(new Set(arr.map((m) => m.metric_type)))
      setActiveType((t) => (t && types.includes(t) ? t : (types[0] || '')))
    }).finally(() => setLoading(false))
  }
  useEffect(() => { loadMetrics() }, [connId])

  const loadTrend = (mt: string) => {
    if (!connId) return
    getDbTrends(connId, mt).then((d) => setTrends((d || []) as TrendPoint[]))
  }
  useEffect(() => { if (activeType && connId) loadTrend(activeType) }, [activeType, connId])

  const types = Array.from(new Set(metrics.map((m) => m.metric_type)))
  const byType = (t: string) => metrics.filter((m) => m.metric_type === t)
  const sampledAt = metrics[0]?.sampled_at || '-'

  const fmt = (m: Metric) => {
    const v = m.value
    if (m.metric_name.endsWith('_bytes')) return `${(v / 1048576).toFixed(2)} MB`
    if (Number.isInteger(v)) return v.toLocaleString()
    return v
  }

  const columns = [
    { title: '指标', dataIndex: 'metric_name', width: 240, render: (n: string) => NAME_LABEL[n] || n },
    { title: '值', width: 160, render: (_: unknown, m: Metric) => <b>{fmt(m)}</b> },
    { title: '说明/明细', dataIndex: 'detail', ellipsis: true, render: (v: string) => v || '-' },
  ]

  return (
    <div>
      <Card bodyStyle={{ padding: '10px 16px' }} style={{ marginBottom: 12 }}>
        <Space wrap>
          <span style={{ fontWeight: 600 }}>数据库监控</span>
          <Select placeholder="选择数据库" style={{ width: 240 }} value={connId} onChange={setConnId}
            options={conns.map((c) => ({ value: c.id, label: `${c.name}（${c.db_type}）` }))} />
          <Button icon={<ReloadOutlined />} onClick={() => loadMetrics()} disabled={!connId}>刷新</Button>
          <span style={{ fontSize: 12, color: '#8c8c8c' }}>采集时间：{sampledAt}（数据来自最近一次诊断/采集）</span>
        </Space>
      </Card>

      {!connId ? <Empty description="请选择数据库" /> : loading ? <Spin /> : (
        <>
          <Tabs
            activeKey={activeType}
            onChange={setActiveType}
            items={types.map((t) => ({
              key: t,
              label: `${TYPE_LABEL[t] || t}（${byType(t).length}）`,
              children: (
                <>
                  <Table rowKey="metric_name" size="small" dataSource={byType(t)} columns={columns} pagination={false} />
                  <Card size="small" title={`${TYPE_LABEL[t] || t} 趋势`} style={{ marginTop: 12 }}>
                    {trends.length ? (
                      <Line data={trends} xField="sampled_at" yField="value" seriesField="metric_name" height={260} smooth />
                    ) : <div style={{ color: '#999', padding: 16 }}>暂无趋势数据（多诊断几次形成趋势）</div>}
                  </Card>
                </>
              ),
            }))}
          />
          <div style={{ marginTop: 8, fontSize: 12, color: '#8c8c8c' }}>
            共 {metrics.length} 项指标 · <Tag>{types.length} 组</Tag>
          </div>
        </>
      )}
    </div>
  )
}
