// 数据库监控：按数据库切换，一屏展示全部监控项（+ 慢 SQL）
// 不同数据库类型（mysql/pg/oracle）采集项不同，按采集到的内容展示
import { useEffect, useState } from 'react'
import { Card, Select, Space, Table, Tag, Button, Empty, Spin, Tooltip, Switch } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { Line } from '@ant-design/charts'
import { getDBConnections, getDbLatestMetrics, getSlowSQL, getDbTrends } from '../services/api'

interface Metric {
  metric_type: string
  metric_name: string
  value: number
  detail: string
  sampled_at: string
}
interface SlowSQL {
  id: number
  sql_text?: string
  schema_name?: string
  exec_count?: number
  avg_latency_ms?: number
  max_latency_ms?: number
  sampled_at?: string
}
interface DBConn { id: number; name: string; db_type: string }
interface TrendPoint { metric_name: string; metric_type?: string; series?: string; value: number; sampled_at: string }

const TYPE_LABEL: Record<string, string> = {
  connections: '连接', throughput: '吞吐', network: '网络', cache: '缓存/缓冲池',
  query_quality: '查询质量', innodb: 'InnoDB', tablespace: '表空间/文件',
  config: '参数配置', lock_wait: '锁等待', replication: '主从复制', rate: '速率',
}
const TYPE_COLOR: Record<string, string> = {
  connections: 'blue', throughput: 'geekblue', network: 'cyan', cache: 'green',
  query_quality: 'orange', innodb: 'purple', tablespace: 'magenta', config: 'default',
  lock_wait: 'red', replication: 'gold', rate: 'lime',
}

const NAME_LABEL: Record<string, string> = {  connections_used: '当前连接数', max_connections: '最大连接数', connection_usage_pct: '连接使用率(%)',
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
  opened_tables: '已打开表累计', long_query_time: '慢查询阈值(s)', slow_query_log: '慢查询日志(1已开)',
  innodb_buffer_pool_size_mb: '缓冲池配置(MB)', slave_running: '从库运行(1正常)', replication_lag_seconds: '复制延迟(s)',
  lock_wait_count: '锁等待数', bytes_received: '累计接收字节', bytes_sent: '累计发送字节',
  rx_bytes_per_sec: '接收速率(B/s)', tx_bytes_per_sec: '发送速率(B/s)',
  com_select: 'SELECT 次数', com_insert: 'INSERT 次数', com_update: 'UPDATE 次数', com_delete: 'DELETE 次数',
  com_commit: 'COMMIT 次数', com_rollback: 'ROLLBACK 次数',
}

export default function DBMonitor() {
  const [conns, setConns] = useState<DBConn[]>([])
  const [connId, setConnId] = useState<number>()
  const [metrics, setMetrics] = useState<Metric[]>([])
  const [slowSQL, setSlowSQL] = useState<SlowSQL[]>([])
  const [loading, setLoading] = useState(false)
  const [trends, setTrends] = useState<TrendPoint[]>([])
  const [groupByScale, setGroupByScale] = useState(false)

  useEffect(() => {
    getDBConnections().then((d) => {
      const arr = (d || []) as DBConn[]
      setConns(arr)
      setConnId((v) => (v ?? (arr[0]?.id)))   // 默认选中第一个库
    })
  }, [])

  const load = (cid?: number) => {
    const id = cid ?? connId
    if (!id) return
    setLoading(true)
    Promise.all([
      getDbLatestMetrics(id).then((d) => setMetrics((d || []) as Metric[])),
      getSlowSQL(id, 200).then((d) => setSlowSQL((d || []) as SlowSQL[])).catch(() => setSlowSQL([])),
    ]).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [connId])

  const types = Array.from(new Set(metrics.map((m) => m.metric_type)))
  // 趋势：全部指标一起展示（最近 1 小时，多线按指标名着色）
  useEffect(() => {
    if (!connId) return
    getDbTrends(connId, 'all', 1).then((d) => {
      const rows = ((d || []) as TrendPoint[]).map((r) => ({ ...r, series: NAME_LABEL[r.metric_name] || r.metric_name }))
      setTrends(rows)
    }).catch(() => setTrends([]))
  }, [connId])

  const cur = conns.find((c) => c.id === connId)
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
    if (name.endsWith('_seconds') || name === 'long_query_time') return 's'
    if (name.endsWith('_bytes_per_sec')) return 'B/s'
    if (name.endsWith('_bytes')) return 'MB'
    if (name === 'qps' || name === 'tps' || name.endsWith('_per_sec')) return '/s'
    if (name === 'table_count') return '张'
    return '次'
  }

  const metricCols = [
    { title: '分类', dataIndex: 'metric_type', width: 120,
      render: (t: string) => <Tag color={TYPE_COLOR[t] || 'default'}>{TYPE_LABEL[t] || t}</Tag>,
      filters: Array.from(new Set(metrics.map((m) => m.metric_type))).map((t) => ({ text: TYPE_LABEL[t] || t, value: t })),
      onFilter: (v: unknown, m: Metric) => m.metric_type === v },
    { title: '监控项', dataIndex: 'metric_name', width: 220, render: (n: string) => NAME_LABEL[n] || n },
    { title: '值', width: 170, render: (_: unknown, m: Metric) => <><b>{fmt(m)}</b> <span style={{ color: '#999', fontSize: 12 }}>{unitOf(m.metric_name)}</span></> },
    { title: '说明/明细', dataIndex: 'detail', ellipsis: true, render: (v: string) => v || '-' },
  ]

  const slowCols = [
    { title: '平均耗时(ms)', dataIndex: 'avg_latency_ms', width: 120, sorter: (a: SlowSQL, b: SlowSQL) => (a.avg_latency_ms || 0) - (b.avg_latency_ms || 0),
      render: (v: number) => <b style={{ color: (v || 0) > 1000 ? '#cf1322' : undefined }}>{v ?? '-'}</b> },
    { title: '最大(ms)', dataIndex: 'max_latency_ms', width: 100 },
    { title: '次数', dataIndex: 'exec_count', width: 80 },
    { title: '库', dataIndex: 'schema_name', width: 120, render: (v: string) => v || '-' },
    { title: '采样时间', dataIndex: 'sampled_at', width: 170 },
    { title: 'SQL', dataIndex: 'sql_text', ellipsis: true, render: (v: string) => <Tooltip title={<pre style={{ maxWidth: 700, whiteSpace: 'pre-wrap' }}>{v}</pre>}>{v}</Tooltip> },
  ]

  return (
    <div>
      <Card bodyStyle={{ padding: '10px 16px' }} style={{ marginBottom: 12 }}>
        <Space wrap>
          <span style={{ fontWeight: 600 }}>数据库监控</span>
          <Select placeholder="选择数据库" style={{ width: 240 }} value={connId} onChange={setConnId}
            options={conns.map((c) => ({ value: c.id, label: `${c.name}（${c.db_type}）` }))} />
          <Button icon={<ReloadOutlined />} onClick={() => load()} disabled={!connId}>刷新</Button>
          {cur && <Tag color="blue">{cur.db_type}</Tag>}
          <span style={{ fontSize: 12, color: '#8c8c8c' }}>采集时间：{sampledAt}</span>
          <span style={{ fontSize: 12, color: '#8c8c8c' }}>（不同数据库类型采集项不同）</span>
        </Space>
      </Card>

      {!connId ? <Empty description="请选择数据库" /> : loading ? <Spin /> : (
        <>
          <Card size="small" title={`监控指标（共 ${metrics.length} 项 · ${new Set(metrics.map((m) => m.metric_type)).size} 类）`}>
            <Table rowKey="metric_name" size="small" dataSource={metrics} columns={metricCols}
              pagination={false} scroll={{ y: 520 }} />
          </Card>

          <Card size="small" style={{ marginTop: 12 }}
            title={`指标趋势（全部指标 · 最近 1 小时 · ${new Set(trends.map((t) => t.series)).size} 条线）`}
            extra={<Space size={6}><span style={{ fontSize: 12, color: '#8c8c8c' }}>按量级分组</span>
              <Switch size="small" checked={groupByScale} onChange={setGroupByScale} /></Space>}>
            {trends.length === 0
              ? <div style={{ color: '#999', padding: 8 }}>暂无趋势数据（采集几次后形成）</div>
              : !groupByScale
                ? <Line data={trends} xField="sampled_at" yField="value" seriesField="series" height={320} smooth
                    legend={{ color: { position: 'bottom', layout: { justifyContent: 'center' } } }} />
                : (() => {
                  const maxOf: Record<string, number> = {}
                  trends.forEach((t) => { const s = t.series || t.metric_name; maxOf[s] = Math.max(maxOf[s] || 0, Number(t.value) || 0) })
                  const bucketOf = (mx: number) => (mx <= 200 ? '小值 / 百分比（≤ 200）' : mx <= 1e6 ? '中值 / 计数（≤ 100 万）' : '大值 / 字节（> 100 万）')
                  const groups: Record<string, TrendPoint[]> = {}
                  trends.forEach((t) => { const b = bucketOf(maxOf[t.series || t.metric_name] || 0); (groups[b] = groups[b] || []).push(t) })
                  const order = ['小值 / 百分比（≤ 200）', '中值 / 计数（≤ 100 万）', '大值 / 字节（> 100 万）']
                  return order.filter((b) => groups[b]?.length).map((b) => (
                    <div key={b} style={{ marginBottom: 16 }}>
                      <div style={{ fontWeight: 600, marginBottom: 4 }}>{b}</div>
                      <Line data={groups[b]} xField="sampled_at" yField="value" seriesField="series" height={240} smooth
                        legend={{ color: { position: 'bottom', layout: { justifyContent: 'center' } } }} />
                    </div>
                  ))
                })()}
          </Card>

          <Card size="small" title={`慢 SQL（Top ${slowSQL.length}）`} style={{ marginTop: 12 }}>
            {slowSQL.length
              ? <Table rowKey="id" size="small" dataSource={slowSQL} columns={slowCols}
                  pagination={{ pageSize: 10, showSizeChanger: false, showTotal: (t) => `共 ${t} 条` }} />
              : <div style={{ color: '#999', padding: 8 }}>暂无慢 SQL 采集数据</div>}
          </Card>
        </>
      )}
    </div>
  )
}
