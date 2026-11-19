// 慢 SQL 面板（详情页 Tab）：优先展示执行明细（阿里云慢日志样式：一行 = 一次执行），
// 新接口不可用（404 / supported=false）时自动回退到旧的 digest 聚合视图。
import { useEffect, useState } from 'react'
import {
  Card, Table, Tag, Button, Space, DatePicker, Modal, Tooltip, message, Empty,
  Input, InputNumber, Select, Switch, Form,
} from 'antd'
import {
  ReloadOutlined, BarChartOutlined, ThunderboltOutlined, SyncOutlined, LineChartOutlined,
} from '@ant-design/icons'
import dayjs, { type Dayjs } from 'dayjs'
import { Line } from '@ant-design/charts'
import {
  getSlowSQL, getSlowSQLDigestTrend, compareSlowSQL, getSlowSQLTrend,
  getSlowSqlEvents, collectSlowSqlEvents, collectSlowSQL, type SlowSqlEventItem,
} from '../../services/api'
import SlowSqlDiagnoseModal from './SlowSqlDiagnoseModal'
import SlowSqlEventTable, { SqlCell } from './SlowSqlEventTable'
import SlowSqlDetailDrawer from './SlowSqlDetailDrawer'
import { listSlowSqlTags, setSlowSqlTag, clearSlowSqlTag, type SlowSqlTagItem } from '../../services/api'

// 慢 SQL 单条记录（聚合视图，后端字段可选容错）
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
type OrderVal = 'time' | 'exec' | 'rows_examined'

// 明细筛选条件（时间范围 + 明细专属过滤）
interface EventFilter {
  range: RangeVal
  schema: string
  host: string
  user: string
  keyword: string
  minSeconds: number | null
  order: OrderVal
  tag: string
}

const FALLBACK_NOTE = '该实例暂不支持执行明细，已回退到按 SQL 聚合视图'

// 明细时间范围预设（5 分钟/1 小时/6 小时/24 小时/7 天）
const eventRangePresets = [
  { label: '近 5 分钟', value: [dayjs().subtract(5, 'minute'), dayjs()] as [Dayjs, Dayjs] },
  { label: '近 1 小时', value: [dayjs().subtract(1, 'hour'), dayjs()] as [Dayjs, Dayjs] },
  { label: '近 6 小时', value: [dayjs().subtract(6, 'hour'), dayjs()] as [Dayjs, Dayjs] },
  { label: '近 24 小时', value: [dayjs().subtract(24, 'hour'), dayjs()] as [Dayjs, Dayjs] },
  { label: '近 7 天', value: [dayjs().subtract(7, 'day'), dayjs()] as [Dayjs, Dayjs] },
]

// 两时段对比的时间预设（沿用旧口径）
const cmpRangePresets = [
  { label: '今天', value: [dayjs().startOf('day'), dayjs()] as [Dayjs, Dayjs] },
  { label: '昨天', value: [dayjs().subtract(1, 'day').startOf('day'), dayjs().startOf('day')] as [Dayjs, Dayjs] },
  { label: '近 1 小时', value: [dayjs().subtract(1, 'hour'), dayjs()] as [Dayjs, Dayjs] },
  { label: '近 24 小时', value: [dayjs().subtract(24, 'hour'), dayjs()] as [Dayjs, Dayjs] },
  { label: '近 7 天', value: [dayjs().subtract(7, 'day'), dayjs()] as [Dayjs, Dayjs] },
]

const ORDER_OPTIONS = [
  { value: 'time', label: '时间倒序' },
  { value: 'exec', label: '耗时倒序' },
  { value: 'rows_examined', label: '扫描行数倒序' },
]

const STATUS_LABEL: Record<string, string> = { up: '变慢', new: '新增', down: '变快', gone: '消失', same: '持平' }
const STATUS_COLOR: Record<string, string> = { up: 'red', new: 'volcano', down: 'green', gone: 'blue', same: 'default' }

export default function DbSlowSqlPanel({ connId }: { connId: number }) {
  // 明细模式 vs 聚合回退
  const [mode, setMode] = useState<'events' | 'aggregate'>('events')
  const [fallback, setFallback] = useState<string | null>(null)

  // 筛选条件
  const [f, setF] = useState<EventFilter>(() => ({
    range: [dayjs().subtract(1, 'hour'), dayjs()],
    schema: '', host: '', user: '', keyword: '',
    minSeconds: null, order: 'time', tag: '',
  }))
  // 文本类筛选（schema/host/user/keyword）先存草稿，回车/查询时提交
  const [textDraft, setTextDraft] = useState({ schema: '', host: '', user: '', keyword: '' })
  const [autoRefresh, setAutoRefresh] = useState(false)
  const [reloadTick, setReloadTick] = useState(0)

  // 明细分页
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)

  // 明细数据
  const [eventItems, setEventItems] = useState<SlowSqlEventItem[]>([])
  const [eventTotal, setEventTotal] = useState(0)
  const [eventLoading, setEventLoading] = useState(false)
  const [collecting, setCollecting] = useState(false)

  // 聚合数据
  const [rows, setRows] = useState<SlowSQLRow[]>([])
  const [aggLoading, setAggLoading] = useState(false)

  // 弹窗：digest 趋势 / 整体趋势 / 诊断 / 详情抽屉 / 两时段对比
  const [trend, setTrend] = useState<{ digest: string; data: TrendPoint[] } | null>(null)
  const [overallTrendOpen, setOverallTrendOpen] = useState(false)
  const [overallTrend, setOverallTrend] = useState<TrendPoint[]>([])
  const [diagRow, setDiagRow] = useState<{ digest?: string; sql_text?: string; schema_name?: string } | null>(null)
  const [detailRow, setDetailRow] = useState<SlowSqlEventItem | null>(null)

  // S2：标签（打标/编辑/取消 + 按标签筛选）
  const [tagItems, setTagItems] = useState<SlowSqlTagItem[]>([])
  const [tagEditRow, setTagEditRow] = useState<SlowSqlEventItem | null>(null)
  const [tagDraft, setTagDraft] = useState({ tag: '', note: '' })
  const [tagSaving, setTagSaving] = useState(false)
  const [cmpOpen, setCmpOpen] = useState(false)
  const [cmpA, setCmpA] = useState<RangeVal>([dayjs().subtract(1, 'day').startOf('day'), dayjs().startOf('day')])
  const [cmpB, setCmpB] = useState<RangeVal>([dayjs().startOf('day'), dayjs()])
  const [cmpLoading, setCmpLoading] = useState(false)
  const [cmpData, setCmpData] = useState<{ diff: CmpDiffRow[]; summary: Record<string, number> } | null>(null)

  // 拉取执行明细（不支持/404 时回退聚合）
  const loadEvents = async () => {
    if (!connId) return
    setEventLoading(true)
    try {
      // S2：tag 筛选字段（api.ts 的 SlowSqlEventsQuery 尚未包含，先本地扩展类型）
      const params: Parameters<typeof getSlowSqlEvents>[1] & { tag?: string } = {
        start: f.range?.[0]?.toISOString(),
        end: f.range?.[1]?.toISOString(),
        schema: f.schema || undefined,
        host: f.host || undefined,
        user: f.user || undefined,
        keyword: f.keyword || undefined,
        min_seconds: f.minSeconds ?? undefined,
        order: f.order,
        tag: f.tag || undefined,
        page,
        page_size: pageSize,
      }
      const d = await getSlowSqlEvents(connId, params)
      if (d?.supported === false) {
        setMode('aggregate')
        setFallback(d.note || FALLBACK_NOTE)
      } else {
        setMode('events')
        setFallback(null)
        setEventItems(d?.items || [])
        setEventTotal(d?.total || 0)
      }
    } catch {
      setMode('aggregate')
      setFallback(FALLBACK_NOTE)
    } finally {
      setEventLoading(false)
    }
  }

  // 拉取聚合列表（回退视图）
  const loadAggregate = async () => {
    if (!connId) return
    setAggLoading(true)
    try {
      const d = await getSlowSQL(connId, 200, f.range?.[0]?.toISOString(), f.range?.[1]?.toISOString())
      setRows((d || []) as SlowSQLRow[])
    } catch {
      setRows([])
    } finally {
      setAggLoading(false)
    }
  }

  // S2：加载标签列表（筛选下拉 + 编辑时回填备注）
  const loadTagOptions = async () => {
    if (!connId) return
    try {
      const d = await listSlowSqlTags(connId)
      setTagItems(d?.items || [])
    } catch {
      /* 忽略：标签接口不可用时仅不展示下拉 */
    }
  }

  // S2：打开打标/编辑弹窗
  const openTagEdit = (r: SlowSqlEventItem) => {
    const existing = tagItems.find((t) => t.digest === r.digest)
    setTagDraft({ tag: existing?.tag || r.tag || '', note: existing?.note || '' })
    setTagEditRow(r)
  }

  // S2：保存标签（tag 传空 = 取消标签，由后端统一处理）
  const saveTag = async () => {
    if (!connId || !tagEditRow?.digest) return
    setTagSaving(true)
    try {
      const tag = tagDraft.tag.trim()
      await setSlowSqlTag(connId, { digest: tagEditRow.digest, tag, note: tagDraft.note.trim() })
      message.success(tag ? '标签已保存' : '已取消标签')
      setTagEditRow(null)
      await loadTagOptions()
      setReloadTick((x) => x + 1)
    } catch {
      /* 拦截器已提示 */
    } finally {
      setTagSaving(false)
    }
  }

  // S2：取消标签（单独入口，明确语义）
  const clearTag = async () => {
    if (!connId || !tagEditRow?.digest) return
    setTagSaving(true)
    try {
      await clearSlowSqlTag(connId, tagEditRow.digest)
      message.success('已取消标签')
      setTagEditRow(null)
      await loadTagOptions()
      setReloadTick((x) => x + 1)
    } catch {
      /* 拦截器已提示 */
    } finally {
      setTagSaving(false)
    }
  }

  // 首载/筛选/分页变化：按当前模式拉对应数据源
  useEffect(() => {
    if (!connId) return
    if (mode === 'aggregate') loadAggregate()
    else loadEvents()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connId, mode, f, page, pageSize, reloadTick])

  // S2：连接变化时加载标签列表
  useEffect(() => {
    loadTagOptions()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connId])

  // 自动刷新（默认关；开启后 15s，页面隐藏时跳过）
  useEffect(() => {
    if (!autoRefresh) return
    const t = setInterval(() => {
      if (document.hidden) return
      setReloadTick((x) => x + 1)
    }, 15000)
    return () => clearInterval(t)
  }, [autoRefresh])

  // 提交文本筛选（回车 / 查询按钮）
  const commitText = () => {
    setF((prev) => ({ ...prev, schema: textDraft.schema, host: textDraft.host, user: textDraft.user, keyword: textDraft.keyword }))
    setPage(1)
  }
  // 文本输入变化：草稿即时更新；清空（allowClear）立即生效
  const textChange = (key: 'schema' | 'host' | 'user' | 'keyword') =>
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const v = e.target.value
      setTextDraft((d) => ({ ...d, [key]: v }))
      if (v === '') {
        setF((prev) => ({ ...prev, [key]: '' }))
        setPage(1)
      }
    }

  // 点 digest 看趋势（明细 / 聚合共用）
  const openTrend = (digest: string) => {
    if (!digest) return
    getSlowSQLDigestTrend(connId, digest, 24)
      .then((d) => {
        const pts = ((d || []) as { bucket: string; avg_latency_ms: number; max_latency_ms: number; exec_count: number }[])
        setTrend({ digest, data: pts.map((x) => ({ metric_name: 'avg', series: '平均耗时(ms)', value: x.avg_latency_ms, sampled_at: x.bucket })) })
      })
      .catch(() => message.error('趋势查询失败'))
  }

  // 整体慢 SQL 趋势
  const openOverallTrend = () => {
    if (!connId) return
    setOverallTrendOpen(true)
    setOverallTrend([])
    getSlowSQLTrend(connId, 24)
      .then((d) => {
        const pts: TrendPoint[] = []
        for (const r of (d || []) as { bucket: string; avg_latency_ms: number; max_latency_ms: number }[]) {
          pts.push({ metric_name: 'avg', series: '平均耗时(ms)', value: r.avg_latency_ms, sampled_at: r.bucket })
          pts.push({ metric_name: 'max', series: '最大耗时(ms)', value: r.max_latency_ms, sampled_at: r.bucket })
        }
        setOverallTrend(pts)
      })
      .catch(() => { setOverallTrend([]); message.error('趋势查询失败') })
  }

  // 立即采集：明细模式走新 collect，聚合回退走旧 collect
  const onCollect = async () => {
    if (!connId) return
    setCollecting(true)
    try {
      if (mode === 'aggregate') await collectSlowSQL(connId)
      else await collectSlowSqlEvents(connId)
      message.success('采集完成')
      setReloadTick((x) => x + 1)
    } catch {
      /* 拦截器已提示 */
    } finally {
      setCollecting(false)
    }
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

  // 建议渲染（命中规则 + 优化建议标签，Tooltip 展示详情）——聚合视图用
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

  // 聚合视图列（回退用，保留原有字段与交互）
  const aggColumns = [
    { title: 'digest', dataIndex: 'digest', width: 150, ellipsis: true,
      render: (v: string) => v
        ? <Button type="link" size="small" style={{ padding: 0 }} onClick={() => openTrend(v)}>{v.slice(0, 16)}…</Button>
        : '-' },
    { title: 'SQL', dataIndex: 'sql_text', width: 460,
      render: (v: string) => <SqlCell sql={v} /> },
    { title: '库', dataIndex: 'schema_name', width: 110, render: (v: string) => v || '-' },
    { title: '次数', dataIndex: 'exec_count', width: 80, sorter: (a: SlowSQLRow, b: SlowSQLRow) => (a.exec_count || 0) - (b.exec_count || 0) },
    { title: '平均耗时(ms)', dataIndex: 'avg_latency_ms', width: 120, sorter: (a: SlowSQLRow, b: SlowSQLRow) => (a.avg_latency_ms || 0) - (b.avg_latency_ms || 0),
      render: (v: number) => <b style={{ color: (v || 0) > 1000 ? '#cf1322' : undefined }}>{v ?? '-'}</b> },
    { title: '最大(ms)', dataIndex: 'max_latency_ms', width: 100, render: (v: number) => v ?? '-' },
    { title: '命中规则/建议', width: 240, render: (_: unknown, r: SlowSQLRow) => renderSuggestions(r.suggestions, r) },
    { title: '诊断', width: 110, fixed: 'right' as const,
      render: (_: unknown, r: SlowSQLRow) => (
        <Button size="small" type="link" icon={<ThunderboltOutlined />}
          disabled={!r.digest} onClick={() => setDiagRow(r)}>AI 诊断</Button>
      ) },
  ]

  const cmpColumns = [
    { title: '状态', dataIndex: 'status', width: 80, render: (v: string) => (
      <Tag color={STATUS_COLOR[v] || 'default'}>{STATUS_LABEL[v] || v}</Tag>) },
    { title: 'SQL', dataIndex: 'sql_text', width: 460, render: (v: string) => <SqlCell sql={v} /> },
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
          <Button size="small" icon={<SyncOutlined />} loading={collecting} onClick={onCollect}>立即采集</Button>
          <Button size="small" icon={<BarChartOutlined />} onClick={() => { setCmpOpen(true); setCmpData(null) }}>两时段对比</Button>
          <Button size="small" icon={<LineChartOutlined />} onClick={openOverallTrend}>慢 SQL 趋势</Button>
        </Space>
      }>
        {/* 筛选条 */}
        <div style={{ marginBottom: 12, display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
          <DatePicker.RangePicker
            showTime={{ format: 'HH:mm' }}
            format="YYYY-MM-DD HH:mm"
            allowClear
            value={f.range}
            placeholder={['开始时间', '结束时间']}
            presets={eventRangePresets}
            onChange={(v) => { setF((prev) => ({ ...prev, range: v as RangeVal })); setPage(1) }}
            style={{ width: 340 }}
          />
          {mode === 'events' && (
            <>
              <Input allowClear placeholder="数据库(schema)" style={{ width: 140 }} value={textDraft.schema}
                onChange={textChange('schema')} onPressEnter={commitText} />
              <Input allowClear placeholder="访问来源(host)" style={{ width: 140 }} value={textDraft.host}
                onChange={textChange('host')} onPressEnter={commitText} />
              <Input allowClear placeholder="用户" style={{ width: 110 }} value={textDraft.user}
                onChange={textChange('user')} onPressEnter={commitText} />
              <Input allowClear placeholder="关键字(SQL)" style={{ width: 180 }} value={textDraft.keyword}
                onChange={textChange('keyword')} onPressEnter={commitText} />
              <InputNumber placeholder="最小耗时(秒)" style={{ width: 120 }} min={0}
                value={f.minSeconds} onChange={(v) => { setF((prev) => ({ ...prev, minSeconds: v ?? null })); setPage(1) }} />
              <Select value={f.order} options={ORDER_OPTIONS} style={{ width: 140 }}
                onChange={(v) => { setF((prev) => ({ ...prev, order: v })); setPage(1) }} />
              <Select allowClear placeholder="标签" style={{ width: 120 }}
                value={f.tag || undefined}
                options={tagItems.map((t) => ({ value: t.tag, label: t.tag })).filter((o) => o.value)}
                onChange={(v) => { setF((prev) => ({ ...prev, tag: v ?? '' })); setPage(1) }} />
              <Space size={4}>
                <Switch size="small" checked={autoRefresh} onChange={setAutoRefresh} />
                <span style={{ fontSize: 12, color: '#8c8c8c' }}>自动刷新</span>
              </Space>
              <Button size="small" type="primary" onClick={commitText}>查询</Button>
            </>
          )}
          <Button size="small" icon={<ReloadOutlined />} onClick={() => setReloadTick((x) => x + 1)}>刷新</Button>
        </div>

        {/* 回退提示 */}
        {fallback && (
          <div style={{
            marginBottom: 12, padding: '6px 12px', background: '#fafafa',
            border: '1px solid #eee', borderRadius: 4, color: '#8c8c8c', fontSize: 12,
          }}>{fallback}</div>
        )}

        {/* 明细 / 聚合 双视图 */}
        {mode === 'events' ? (
          eventItems.length || eventLoading
            ? <SlowSqlEventTable
                items={eventItems} loading={eventLoading} total={eventTotal} page={page} pageSize={pageSize}
                onPageChange={(p, ps) => { setPage(p); setPageSize(ps) }}
                onOpenTrend={(r) => openTrend(r.digest || '')}
                onOpenDetail={(r) => setDetailRow(r)}
                onDiagnose={(r) => setDiagRow(r)}
                onEditTag={openTagEdit}
              />
            : <Empty description="暂无慢 SQL 执行明细" />
        ) : (
          rows.length
            ? <Table rowKey="id" size="small" dataSource={rows} columns={aggColumns} loading={aggLoading}
                pagination={{ pageSize: 10, showSizeChanger: false, showTotal: (t) => `共 ${t} 条` }}
                scroll={{ x: 'max-content' }} />
            : (aggLoading ? <Table rowKey="id" size="small" dataSource={[]} columns={aggColumns} loading pagination={false} />
               : <Empty description="暂无慢 SQL 采集数据" />)
        )}
      </Card>

      {/* 慢 SQL AI 诊断弹窗 */}
      <SlowSqlDiagnoseModal connId={connId} row={diagRow} onClose={() => setDiagRow(null)} />

      {/* 慢 SQL 执行详情抽屉 */}
      <SlowSqlDetailDrawer connId={connId} row={detailRow} onClose={() => setDetailRow(null)} />

      {/* S2：慢 SQL 标签打标/编辑弹窗 */}
      <Modal
        open={!!tagEditRow}
        title={`慢 SQL 标签（SQL ID ${(tagEditRow?.digest || '').slice(0, 16)}${(tagEditRow?.digest || '').length > 16 ? '…' : ''}）`}
        onCancel={() => setTagEditRow(null)}
        width={480}
        footer={[
          <Button key="clear" danger disabled={!tagDraft.tag && !tagEditRow?.tag}
            onClick={clearTag}>取消标签</Button>,
          <Button key="cancel" onClick={() => setTagEditRow(null)}>取消</Button>,
          <Button key="ok" type="primary" loading={tagSaving} onClick={saveTag}>保存</Button>,
        ]}
      >
        <Form layout="vertical">
          <Form.Item label="标签（自由文本，如「核心链路」/「可忽略」）">
            <Input value={tagDraft.tag} maxLength={100} allowClear placeholder="输入标签"
              onChange={(e) => setTagDraft((d) => ({ ...d, tag: e.target.value }))} />
          </Form.Item>
          <Form.Item label="备注（可选）">
            <Input.TextArea value={tagDraft.note} maxLength={500} rows={2}
              placeholder="备注，如忽略原因 / 负责人"
              onChange={(e) => setTagDraft((d) => ({ ...d, note: e.target.value }))} />
          </Form.Item>
        </Form>
      </Modal>

      {/* digest 趋势弹窗 */}
      <Modal open={!!trend} onCancel={() => setTrend(null)} footer={null} width={720}
        title={`慢 SQL 趋势（digest ${trend?.digest?.slice(0, 16) ?? ''}… · 近 24 小时）`}>
        {trend && (trend.data.length
          ? <Line data={trend.data} xField="sampled_at" yField="value" seriesField="series" height={300} smooth />
          : <Empty description="该 digest 无趋势数据（多次采集后形成）" />)}
      </Modal>

      {/* 整体慢 SQL 趋势弹窗 */}
      <Modal open={overallTrendOpen} onCancel={() => setOverallTrendOpen(false)} footer={null} width={720}
        title="慢 SQL 趋势（近 24 小时 · 按小时聚合）">
        {overallTrend.length
          ? <Line data={overallTrend} xField="sampled_at" yField="value" seriesField="series" height={300} smooth />
          : <Empty description="暂无趋势数据" />}
      </Modal>

      {/* 两时段对比弹窗 */}
      <Modal open={cmpOpen} onCancel={() => setCmpOpen(false)} footer={null} width={1000}
        title="慢 SQL 两时段对比（TopN）">
        <Space style={{ marginBottom: 12 }} wrap>
          <span>基准 A：</span>
          <DatePicker.RangePicker size="small" showTime={{ format: 'HH:mm' }} format="MM-DD HH:mm" allowClear={false}
            value={cmpA} presets={cmpRangePresets} onChange={(v) => setCmpA(v as RangeVal)} />
          <span>对比 B：</span>
          <DatePicker.RangePicker size="small" showTime={{ format: 'HH:mm' }} format="MM-DD HH:mm" allowClear={false}
            value={cmpB} presets={cmpRangePresets} onChange={(v) => setCmpB(v as RangeVal)} />
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
