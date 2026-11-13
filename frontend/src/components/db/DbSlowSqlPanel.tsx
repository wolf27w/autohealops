// 慢 SQL 面板（详情页 Tab）：时间范围筛选 + 慢 SQL 表格 + digest 趋势 + 两时段对比
import { useEffect, useState } from 'react'
import { Card, Table, Tag, Button, Space, DatePicker, Modal, Tooltip, message, Empty } from 'antd'
import { ReloadOutlined, BarChartOutlined } from '@ant-design/icons'
import dayjs, { type Dayjs } from 'dayjs'
import { Line } from '@ant-design/charts'
import { getSlowSQL, getSlowSQLDigestTrend, compareSlowSQL } from '../../services/api'

// 慢 SQL 单条记录（后端字段，可选容错）
interface SlowSQLRow {
  id: number
  digest?: string
  sql_text?: string
  schema_name?: string
  exec_count?: number
  avg_latency_ms?: number
  max_latency_ms?: number
  sampled_at?: string
  hit_rules?: string[]
  suggestions?: { rule: string; hint: string; action: string; risk: string }[]
}

// digest 趋势点（Line 图所需）
interface TrendPoint { metric_name: string; series: string; value: number; sampled_at: string }

// 两时段对比 diff 行
interface CmpDiffRow {
  digest: string
  sql_text: string
  a_exec: number
  b_exec: number
  exec_delta: number
  a_avg: number
  b_avg: number
  avg_delta_pct: number | null
  status: string
}

type RangeVal = [Dayjs | null, Dayjs | null] | null

// 时间段预设（对比弹窗复用）
const rangePresets = [
  { label: '今天', value: [dayjs().startOf('day'), dayjs()] as [Dayjs, Dayjs] },
  { label: '昨天', value: [dayjs().subtract(1, 'day').startOf('day'), dayjs().startOf('day')] as [Dayjs, Dayjs] },
  { label: '近 1 小时', value: [dayjs().subtract(1, 'hour'), dayjs()] as [Dayjs, Dayjs] },
  { label: '近 24 小时', value: [dayjs().subtract(24, 'hour'), dayjs()] as [Dayjs, Dayjs] },
  { label: '近 7 天', value: [dayjs().subtract(7, 'day'), dayjs()] as [Dayjs, Dayjs] },
]

const STATUS_LABEL: Record<string, string> = { up: '变慢', new: '新增', down: '变快', gone: '消失', same: '持平' }
const STATUS_COLOR: Record<string, string> = { up: 'red', new: 'volcano', down: 'green', gone: 'blue', same: 'default' }

export default function DbSlowSqlPanel({ connId }: { connId: number }) {
  const [rows, setRows] = useState<SlowSQLRow[]>([])
  const [loading, setLoading] = useState(false)
  const [range, setRange] = useState<RangeVal>(null)

  // digest 趋势弹窗
  const [trend, setTrend] = useState<{ digest: string; data: TrendPoint[] } | null>(null)

  // 两时段对比弹窗
  const [cmpOpen, setCmpOpen] = useState(false)
  const [cmpA, setCmpA] = useState<RangeVal>([dayjs().subtract(1, 'day').startOf('day'), dayjs().startOf('day')])
  const [cmpB, setCmpB] = useState<RangeVal>([dayjs().startOf('day'), dayjs()])
  const [cmpLoading, setCmpLoading] = useState(false)
  const [cmpData, setCmpData] = useState<{ diff: CmpDiffRow[]; summary: Record<string, number> } | null>(null)

  // 拉取慢 SQL 列表
  const loadSlow = (r?: RangeVal) => {
    if (!connId) return
    const pair = r ?? range
    const s = pair?.[0]
    const e = pair?.[1]
    setLoading(true)
    getSlowSQL(connId, 200, s?.toISOString(), e?.toISOString())
      .then((d) => setRows((d || []) as SlowSQLRow[]))
      .catch(() => { setRows([]); message.error('慢 SQL 查询失败') })
      .finally(() => setLoading(false))
  }

  useEffect(() => { loadSlow(null) }, [connId])

  // 点 digest 看趋势
  const openTrend = (r: SlowSQLRow) => {
    if (!r.digest) return
    getSlowSQLDigestTrend(connId, r.digest, 24)
      .then((d) => {
        const pts = ((d || []) as { bucket: string; avg_latency_ms: number; max_latency_ms: number; exec_count: number }[])
        setTrend({ digest: r.digest!, data: pts.map((x) => ({ metric_name: 'avg', series: '平均耗时(ms)', value: x.avg_latency_ms, sampled_at: x.bucket })) })
      })
      .catch(() => message.error('趋势查询失败'))
  }

  // 两时段对比
  const runCompare = () => {
    if (!connId) return
    setCmpLoading(true)
    setCmpData(null)
    compareSlowSQL(connId, {
      a_start: cmpA?.[0]?.toISOString(), a_end: cmpA?.[1]?.toISOString(),
      b_start: cmpB?.[0]?.toISOString(), b_end: cmpB?.[1]?.toISOString(),
      limit: 30,
    }).then((d) => setCmpData(d as never)).catch(() => setCmpData(null)).finally(() => setCmpLoading(false))
  }

  // 建议渲染（命中规则 + 优化建议标签，Tooltip 展示详情）
  const renderSuggestions = (v: SlowSQLRow['suggestions'], r: SlowSQLRow) => {
    const rules = r.hit_rules || []
    if ((!v || !v.length) && !rules.length) return '-'
    const tip = (v || []).map((s) => `• ${s.hint}：${s.action}（风险：${s.risk}）`).join('\n')
    return (
      <Space size={4} wrap>
        {rules.map((h) => <Tag key={h} color="orange">{h}</Tag>)}
        {!!(v && v.length) && (
          <Tooltip title={<div style={{ whiteSpace: 'pre-wrap', maxWidth: 520 }}>{tip}</div>}>
            <span style={{ color: '#999', fontSize: 12 }}>建议 {v.length} 条</span>
          </Tooltip>
        )}
      </Space>
    )
  }

  const columns = [
    { title: 'digest', dataIndex: 'digest', width: 150, ellipsis: true,
      render: (v: string, r: SlowSQLRow) => v
        ? <Button type="link" size="small" style={{ padding: 0 }} onClick={() => openTrend(r)}>{v.slice(0, 16)}…</Button>
        : '-' },
    { title: 'SQL', dataIndex: 'sql_text', ellipsis: true,
      render: (v: string) => v ? <Tooltip title={<pre style={{ maxWidth: 700, whiteSpace: 'pre-wrap', margin: 0 }}>{v}</pre>}>{v}</Tooltip> : '-' },
    { title: '库', dataIndex: 'schema_name', width: 110, render: (v: string) => v || '-' },
    { title: '次数', dataIndex: 'exec_count', width: 80, sorter: (a: SlowSQLRow, b: SlowSQLRow) => (a.exec_count || 0) - (b.exec_count || 0) },
    { title: '平均耗时(ms)', dataIndex: 'avg_latency_ms', width: 120, sorter: (a: SlowSQLRow, b: SlowSQLRow) => (a.avg_latency_ms || 0) - (b.avg_latency_ms || 0),
      render: (v: number) => <b style={{ color: (v || 0) > 1000 ? '#cf1322' : undefined }}>{v ?? '-'}</b> },
    { title: '最大(ms)', dataIndex: 'max_latency_ms', width: 100, render: (v: number) => v ?? '-' },
    { title: '命中规则/建议', width: 240, render: (_: unknown, r: SlowSQLRow) => renderSuggestions(r.suggestions, r) },
  ]

  const cmpColumns = [
    { title: '状态', dataIndex: 'status', width: 80, render: (v: string) => (
      <Tag color={STATUS_COLOR[v] || 'default'}>{STATUS_LABEL[v] || v}</Tag>) },
    { title: 'SQL', dataIndex: 'sql_text', ellipsis: true, render: (v: string) => v ? <Tooltip title={<pre style={{ maxWidth: 700, whiteSpace: 'pre-wrap', margin: 0 }}>{v}</pre>}>{v}</Tooltip> : '-' },
    { title: 'A 执行次数', dataIndex: 'a_exec', width: 100 },
    { title: 'B 执行次数', dataIndex: 'b_exec', width: 100 },
    { title: '次数变化', dataIndex: 'exec_delta', width: 90, render: (v: number) => (
      <span style={{ color: v > 0 ? '#cf1322' : v < 0 ? '#3f8600' : 'inherit' }}>{v > 0 ? '+' : ''}{v}</span>) },
    { title: 'A 平均(ms)', dataIndex: 'a_avg', width: 100 },
    { title: 'B 平均(ms)', dataIndex: 'b_avg', width: 100 },
    { title: '耗时变化', dataIndex: 'avg_delta_pct', width: 90, render: (v: number | null) =>
      v === null ? '-' : <span style={{ color: v > 20 ? '#cf1322' : v < -20 ? '#3f8600' : 'inherit' }}>{v > 0 ? '+' : ''}{v}%</span> },
  ]

  return (
    <div>
      <Card size="small" title="慢 SQL" extra={
        <Space>
          <DatePicker.RangePicker
            showTime={{ format: 'HH:mm' }}
            format="YYYY-MM-DD HH:mm"
            allowClear
            value={range}
            placeholder={['开始时间', '结束时间']}
            presets={rangePresets}
            onChange={(v) => setRange(v as RangeVal)}
            style={{ width: 340 }}
          />
          <Button size="small" onClick={() => loadSlow(null)}>筛选</Button>
          <Button size="small" icon={<ReloadOutlined />} onClick={() => loadSlow(null)}>刷新</Button>
          <Button size="small" icon={<BarChartOutlined />} onClick={() => { setCmpOpen(true); setCmpData(null) }}>两时段对比</Button>
        </Space>
      }>
        {rows.length
          ? <Table rowKey="id" size="small" dataSource={rows} columns={columns} loading={loading}
              pagination={{ pageSize: 10, showSizeChanger: false, showTotal: (t) => `共 ${t} 条` }}
              scroll={{ x: 'max-content' }} />
          : (loading ? <Table rowKey="id" size="small" dataSource={[]} columns={columns} loading pagination={false} />
             : <Empty description="暂无慢 SQL 采集数据" />)}
      </Card>

      {/* digest 趋势弹窗 */}
      <Modal open={!!trend} onCancel={() => setTrend(null)} footer={null} width={720}
        title={`慢 SQL 趋势（digest ${trend?.digest?.slice(0, 16) ?? ''}… · 近 24 小时）`}>
        {trend && (trend.data.length
          ? <Line data={trend.data} xField="sampled_at" yField="value" seriesField="series" height={300} smooth />
          : <Empty description="该 digest 无趋势数据（多次采集后形成）" />)}
      </Modal>

      {/* 两时段对比弹窗 */}
      <Modal open={cmpOpen} onCancel={() => setCmpOpen(false)} footer={null} width={1000}
        title="慢 SQL 两时段对比（TopN）">
        <Space style={{ marginBottom: 12 }} wrap>
          <span>基准 A：</span>
          <DatePicker.RangePicker size="small" showTime={{ format: 'HH:mm' }} format="MM-DD HH:mm" allowClear={false}
            value={cmpA} presets={rangePresets} onChange={(v) => setCmpA(v as RangeVal)} />
          <span>对比 B：</span>
          <DatePicker.RangePicker size="small" showTime={{ format: 'HH:mm' }} format="MM-DD HH:mm" allowClear={false}
            value={cmpB} presets={rangePresets} onChange={(v) => setCmpB(v as RangeVal)} />
          <Button size="small" type="primary" loading={cmpLoading} onClick={runCompare}>对比</Button>
          <span style={{ color: '#8c8c8c', fontSize: 12 }}>变慢 &gt;20% / 变快 &lt;20% / 新增 / 消失</span>
        </Space>
        {cmpData && (
          <>
            {cmpData.summary && (
              <Space size={8} style={{ marginBottom: 8 }} wrap>
                <Tag color="red">变慢 {cmpData.summary.up ?? 0}</Tag>
                <Tag color="volcano">新增 {cmpData.summary.new ?? 0}</Tag>
                <Tag color="green">变快 {cmpData.summary.down ?? 0}</Tag>
                <Tag>消失 {cmpData.summary.gone ?? 0}</Tag>
              </Space>
            )}
            <Table rowKey="digest" size="small" dataSource={cmpData.diff} columns={cmpColumns}
              pagination={{ pageSize: 10, showSizeChanger: false }} scroll={{ x: 'max-content' }} />
          </>
        )}
        {!cmpData && <div style={{ color: '#999', padding: 8 }}>{cmpLoading ? '正在对比…' : '请选择两个时段后点击对比'}</div>}
      </Modal>
    </div>
  )
}
