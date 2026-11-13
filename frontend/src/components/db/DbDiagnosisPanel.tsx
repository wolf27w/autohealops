// 数据库诊断 Tab（单实例）：手动触发诊断 + 进度轮询 + 诊断报告列表 + 指标趋势
import { useCallback, useEffect, useRef, useState } from 'react'
import { Button, Card, Drawer, Empty, Progress, Space, Table, Tag, message } from 'antd'
import { PlayCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import { Line } from '@ant-design/charts'
import Markdown from '../Markdown'
import {
  getDbDiagnosisProgress, getDbDiagnosisReports, getDbTrends, runDbDiagnosis,
} from '../../services/api'

// 诊断报告（后端返回可能带 db_id / db_connection_id，用于按实例过滤）
interface DBReport {
  id: number
  db_id?: number
  db_connection_id?: number
  db_name?: string
  db_type?: string
  conclusion?: string
  root_cause?: string
  suggestion?: string
  created_at?: string
}

// 诊断进度
interface DiagProgress { pct: number; step: string }

// 趋势点（与 DBMonitor 对齐：getDbTrends 返回数组）
interface TrendPoint { metric_name: string; series?: string; value: number; sampled_at: string }

interface Props { connId: number }

// 建议字段可能是 JSON 数组（字符串或带 title/detail 的对象），也可能是 Markdown 文本
interface SuggestionItem { title?: string; detail?: string; sql_before?: string; sql_after?: string; impact?: string }

const PAGE_SIZE = 20

export default function DbDiagnosisPanel({ connId }: Props) {
  const [diag, setDiag] = useState<DiagProgress | null>(null)
  const [reports, setReports] = useState<DBReport[]>([])
  const [hasDbField, setHasDbField] = useState(false) // 后端是否带 db_id / db_connection_id 字段
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [detail, setDetail] = useState<DBReport | null>(null)
  const [trends, setTrends] = useState<TrendPoint[]>([])
  const [loadingReports, setLoadingReports] = useState(false)
  const timerRef = useRef<number | null>(null)

  // 清轮询定时器（卸载/完成时都要清）
  const clearTimer = useCallback(() => {
    if (timerRef.current != null) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [])

  // 卸载时清掉定时器
  useEffect(() => () => clearTimer(), [clearTimer])

  // 拉取报告：直接带 conn_id 让后端只返回本实例的报告（不再前端猜字段过滤）
  const load = useCallback((p = 1) => {
    setLoadingReports(true)
    getDbDiagnosisReports(p, PAGE_SIZE, connId).then((d) => {
      const items = ((d as unknown as { items?: DBReport[] })?.items || []) as DBReport[]
      setReports(items)
      setHasDbField(true)
      setTotal((d as unknown as { total?: number })?.total || items.length)
      setPage((d as unknown as { page?: number })?.page || p)
    }).catch(() => setReports([])).finally(() => setLoadingReports(false))
  }, [connId])

  useEffect(() => { load(1) }, [load])

  // 指标趋势（近 1 小时，全指标）
  useEffect(() => {
    getDbTrends(connId, 'all', 1).then((d) => {
      const rows = ((d || []) as TrendPoint[]).map((r) => ({ ...r, series: r.series || r.metric_name }))
      setTrends(rows)
    }).catch(() => setTrends([]))
  }, [connId])

  // 开始诊断：触发 runDbDiagnosis + 并行轮询进度，完成后停止轮询并刷新报告
  const startDiagnosis = async () => {
    if (diag) return
    setDiag({ pct: 5, step: '准备中…' })
    timerRef.current = window.setInterval(() => {
      getDbDiagnosisProgress(connId)
        .then((p) => {
          if (p && typeof p.pct === 'number') {
            setDiag({ pct: p.pct, step: p.step || '诊断中…' })
          }
        })
        .catch(() => {})
    }, 800)
    try {
      await runDbDiagnosis(connId)
      setDiag({ pct: 100, step: '完成' })
      clearTimer()
      setTimeout(() => setDiag(null), 600)
      message.success('诊断完成')
      load(1)
    } catch (e) {
      clearTimer()
      setDiag(null)
      message.error((e as { response?: { data?: { message?: string } } })?.response?.data?.message || '诊断失败')
    }
  }

  // 复制报告全文
  const copyReport = async (r: DBReport) => {
    const text = [
      `诊断报告 #${r.id}`,
      `数据库：${r.db_name || '-'}`,
      '',
      '结论：',
      r.conclusion || '-',
      '',
      '根因：',
      r.root_cause || '-',
      '',
      '优化建议：',
      r.suggestion || '-',
    ].join('\n')
    try {
      await navigator.clipboard.writeText(text)
      message.success('已复制')
    } catch {
      message.error('复制失败（浏览器可能未授权剪贴板）')
    }
  }

  // 建议字段渲染：JSON 数组走结构化展示，否则按 Markdown
  const renderSuggestion = (suggestion?: string) => {
    if (!suggestion) return <div style={{ color: '#999' }}>-</div>
    try {
      const arr = JSON.parse(suggestion)
      if (Array.isArray(arr)) {
        return (
          <div style={{ marginTop: 6 }}>
            {arr.map((s, i) => {
              if (typeof s === 'string') return <div key={i} style={{ marginBottom: 6 }}>• {s}</div>
              const item = s as SuggestionItem
              return (
                <div key={i} style={{ marginBottom: 12, paddingBottom: 10, borderBottom: '1px solid #f0f0f0' }}>
                  <div style={{ fontWeight: 600 }}>{i + 1}. {item.title || '优化项'}</div>
                  {item.detail && <Markdown text={item.detail} />}
                  {item.sql_before && (
                    <div style={{ marginTop: 4 }}>
                      <div style={{ fontSize: 12, color: '#999' }}>优化前 SQL</div>
                      <pre style={{ whiteSpace: 'pre-wrap', background: '#fff1f0', padding: 8, borderRadius: 4, margin: 0 }}>{item.sql_before}</pre>
                    </div>
                  )}
                  {item.sql_after && (
                    <div style={{ marginTop: 6 }}>
                      <div style={{ fontSize: 12, color: '#999' }}>优化后 SQL</div>
                      <pre style={{ whiteSpace: 'pre-wrap', background: '#f6ffed', padding: 8, borderRadius: 4, margin: 0 }}>{item.sql_after}</pre>
                    </div>
                  )}
                  {item.impact && <div style={{ marginTop: 6, color: '#fa8c16' }}>预计优化前后对比：{item.impact}</div>}
                </div>
              )
            })}
          </div>
        )
      }
    } catch { /* 非 JSON，按 Markdown 展示 */ }
    return <Markdown text={suggestion} />
  }

  return (
    <div>
      {/* 顶部：开始诊断按钮 + 进度条 */}
      <Card size="small" style={{ marginBottom: 12 }}>
        <Space direction="vertical" style={{ width: '100%' }}>
          <Space wrap>
            <Button type="primary" icon={<PlayCircleOutlined />} loading={!!diag} onClick={startDiagnosis}>
              {diag ? '诊断中…' : '开始诊断'}
            </Button>
            <Button icon={<ReloadOutlined />} onClick={() => load(page)} disabled={!!diag}>刷新报告</Button>
            {hasDbField ? <Tag color="blue">本实例报告</Tag> : <Tag>全部实例报告</Tag>}
          </Space>
          {diag && (
            <div style={{ width: '100%' }}>
              <Progress percent={diag.pct} status={diag.pct >= 100 ? 'success' : 'active'} />
              <div style={{ marginTop: 4, color: '#666' }}>{diag.step}</div>
            </div>
          )}
        </Space>
      </Card>

      {/* 诊断报告列表 */}
      <Card size="small" title="诊断报告">
        <Table
          rowKey="id"
          size="small"
          loading={loadingReports}
          dataSource={reports}
          pagination={{ current: page, pageSize: PAGE_SIZE, total, showSizeChanger: false, showTotal: (t) => `共 ${t} 条`, onChange: (p) => load(p) }}
          columns={[
            { title: 'ID', dataIndex: 'id', width: 60 },
            { title: '数据库', dataIndex: 'db_name', width: 150, render: (v: string, r: DBReport) => v ? <span>{v}{r.db_type ? <Tag style={{ marginLeft: 4 }}>{r.db_type}</Tag> : null}</span> : '-' },
            { title: '结论', dataIndex: 'conclusion', ellipsis: true, render: (v: string) => v || '-' },
            { title: '根因', dataIndex: 'root_cause', ellipsis: true, render: (v: string) => v || '-' },
            { title: '建议', dataIndex: 'suggestion', ellipsis: true, render: (v: string) => {
              if (!v) return '-'
              try { const arr = JSON.parse(v); if (Array.isArray(arr)) return arr.map((s: string | SuggestionItem) => typeof s === 'string' ? s : (s.title || '')).filter(Boolean).join('；') } catch { /* ignore */ }
              return v
            } },
            { title: '创建时间', dataIndex: 'created_at', width: 170 },
            { title: '操作', width: 90, render: (_, r) => (
              <Button size="small" type="link" onClick={() => setDetail(r)}>详情</Button>
            ) },
          ]}
        />
        {reports.length === 0 && !loadingReports && <Empty description="暂无诊断报告（点击上方「开始诊断」生成）" />}
      </Card>

      {/* 报告详情 Drawer（支持复制） */}
      <Drawer
        title={`诊断报告 #${detail?.id ?? ''}`}
        open={!!detail}
        onClose={() => setDetail(null)}
        width={720}
        extra={detail && (
          <Button size="small" onClick={() => copyReport(detail)}>复制</Button>
        )}
      >
        {detail && (
          <div>
            <div style={{ marginBottom: 12 }}><b>结论</b><Markdown text={detail.conclusion} /></div>
            <div style={{ marginBottom: 12 }}><b>根因</b><Markdown text={detail.root_cause} /></div>
            <div style={{ marginBottom: 12 }}><b>优化建议</b>{renderSuggestion(detail.suggestion)}</div>
          </div>
        )}
      </Drawer>

      {/* 底部：指标趋势（近 1 小时，全指标） */}
      <Card size="small" style={{ marginTop: 12 }} title={`指标趋势（近 1 小时 · ${new Set(trends.map((t) => t.series || t.metric_name)).size} 条线）`}>
        {trends.length === 0
          ? <div style={{ color: '#999', padding: 8 }}>暂无趋势数据（采集几次后形成）</div>
          : <Line data={trends} xField="sampled_at" yField="value" seriesField="series" height={300} smooth
              legend={{ color: { position: 'bottom', layout: { justifyContent: 'center' } } }} />}
      </Card>
    </div>
  )
}
