// 诊断中心：数据库诊断（按需触发）+ 报告 + 趋势（应用诊断在 AI 诊断助手）
import { useEffect, useRef, useState } from 'react'
import type { DBConnection } from '../types'
import {
  Tabs, Card, Button, Table, Modal, Form, Input, Select, AutoComplete, InputNumber, Space, Tag, message, Popconfirm, Drawer, Switch, Progress, Alert,
} from 'antd'
import { useNavigate } from 'react-router-dom'
import { PlusOutlined, PlayCircleOutlined, DatabaseOutlined, LineChartOutlined } from '@ant-design/icons'
import Markdown from '../components/Markdown'
import {
  getDBConnections, createDBConnection, updateDBConnection,
  deleteDBConnection, testDBConnection, collectSlowSQL,
  runDbDiagnosis, getDbDiagnosisReports, downloadInspectionReport, getDbDiagnosisProgress, listDbGroups,
} from '../services/api'

interface DBReport {
  id: number
  db_name?: string
  db_type?: string
  conclusion?: string
  root_cause?: string
  suggestion?: string
  created_at?: string
}

interface TrendPoint {
  sampled_at?: string
  value?: number
  metric_name?: string
}

interface SuggestionItem {
  title?: string
  detail?: string
  sql_before?: string
  sql_after?: string
  impact?: string
}

interface DiagnosisResult {
  conclusion?: string
  root_cause?: string
  suggestions?: Array<string | SuggestionItem>
}

function Suggestion({ s, idx }: { s: string | SuggestionItem; idx: number }) {
  if (typeof s === 'string') return <li>{s}</li>
  return (
    <div style={{ marginBottom: 12, paddingBottom: 10, borderBottom: '1px solid #f0f0f0' }}>
      <div style={{ fontWeight: 600 }}>{idx}. {s.title || '优化项'}</div>
      {s.detail && <Markdown text={s.detail} />}
      {s.sql_before && (
        <div style={{ marginTop: 4 }}>
          <div style={{ fontSize: 12, color: '#999' }}>优化前 SQL（问题 SQL）</div>
          <pre style={{ whiteSpace: 'pre-wrap', background: '#fff1f0', padding: 8, borderRadius: 4, margin: 0 }}>{s.sql_before}</pre>
        </div>
      )}
      {s.sql_after && (
        <div style={{ marginTop: 6 }}>
          <div style={{ fontSize: 12, color: '#999' }}>优化后 SQL</div>
          <pre style={{ whiteSpace: 'pre-wrap', background: '#f6ffed', padding: 8, borderRadius: 4, margin: 0 }}>{s.sql_after}</pre>
        </div>
      )}
      {s.impact && <div style={{ marginTop: 6, color: '#fa8c16' }}>预计优化前后对比：{s.impact}</div>}
    </div>
  )
}

export default function DiagnosticCenter() {
  const navigate = useNavigate()
  return (
    <>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="数据库实例纳管、监控、慢 SQL 与 SQL 工作台已迁到「资源中心 → 数据库」"
        description="这里只保留跨实例的诊断报告（看结果）；单个库的监控 / 慢 SQL / 工作台 / 发起诊断请在资源详情页操作。"
        action={<Button size="small" onClick={() => navigate('/resources/databases')}>去资源中心</Button>}
      />
      <Tabs
        defaultActiveKey="reports"
        items={[
          { key: 'reports', label: '诊断报告', children: <ReportsTab /> },
        ]}
      />
    </>
  )
}

// ============ 批量发起诊断 ============
type BatchStatus = 'waiting' | 'running' | 'done' | 'failed' | 'cancelled'

interface BatchItem {
  id: number
  name: string
  status: BatchStatus
  error?: string
}

const BATCH_STATUS_MAP: Record<BatchStatus, { text: string; color: string }> = {
  waiting: { text: '等待', color: 'default' },
  running: { text: '诊断中', color: 'processing' },
  done: { text: '完成', color: 'success' },
  failed: { text: '失败', color: 'error' },
  cancelled: { text: '已取消', color: 'default' },
}

const errMsg = (e: unknown) => {
  const err = e as { message?: string; response?: { data?: { detail?: string; message?: string } } }
  return err?.response?.data?.detail || err?.message || '诊断失败'
}

function BatchDiagnosisModal({ open, onClose, onRefresh }: { open: boolean; onClose: () => void; onRefresh: () => void }) {
  const [connections, setConnections] = useState<DBConnection[]>([])
  const [loadingList, setLoadingList] = useState(false)
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [phase, setPhase] = useState<'select' | 'running' | 'done'>('select')
  const [items, setItems] = useState<BatchItem[]>([])
  const [cancelRequested, setCancelRequested] = useState(false)
  const cancelRef = useRef(false)
  const runningRef = useRef(false)

  // 只在打开弹窗时拉取连接列表（不进页面就拉）
  useEffect(() => {
    if (!open) return
    if (runningRef.current) { setPhase('running'); return }
    setPhase('select')
    setSelectedIds([])
    setItems([])
    setCancelRequested(false)
    cancelRef.current = false
    setLoadingList(true)
    getDBConnections()
      .then((d) => setConnections(((d || []) as DBConnection[])))
      .catch(() => setConnections([]))
      .finally(() => setLoadingList(false))
  }, [open])

  const start = async () => {
    if (!selectedIds.length || runningRef.current) return
    runningRef.current = true
    cancelRef.current = false
    setCancelRequested(false)
    const queue = selectedIds
      .map((id) => connections.find((c) => c.id === id))
      .filter((c): c is DBConnection => !!c)
      .map((c) => ({ id: c.id, name: c.name || `#${c.id}` }))
    setItems(queue.map((q) => ({ ...q, status: 'waiting' as BatchStatus })))
    setPhase('running')

    let success = 0
    let fail = 0
    let next = 0
    const patch = (id: number, p: Partial<BatchItem>) =>
      setItems((prev) => prev.map((it) => (it.id === id ? { ...it, ...p } : it)))

    // 简单 Promise 池：并发上限 3；已发出的不中断，取消后未发出的不再发
    const worker = async () => {
      while (true) {
        if (cancelRef.current) return
        const i = next
        if (i >= queue.length) return
        next = i + 1
        const q = queue[i]
        patch(q.id, { status: 'running' })
        try {
          await runDbDiagnosis(q.id)
          patch(q.id, { status: 'done' })
          success += 1
          onRefresh() // 完成一个立刻刷新报告列表
        } catch (e) {
          patch(q.id, { status: 'failed', error: errMsg(e) })
          fail += 1
        }
      }
    }
    await Promise.all(Array.from({ length: Math.min(3, queue.length) }, () => worker()))

    const cancelled = queue.length - success - fail
    setItems((prev) => prev.map((it) => (it.status === 'waiting' ? { ...it, status: 'cancelled' } : it)))
    setPhase('done')
    runningRef.current = false
    const parts = [`成功 ${success}`, `失败 ${fail}`]
    if (cancelled > 0) parts.push(`已取消 ${cancelled}`)
    message.success(`批量诊断完成：${parts.join('，')}`)
  }

  return (
    <Modal
      title="批量发起诊断"
      open={open}
      onCancel={onClose}
      width={640}
      maskClosable={phase !== 'running'}
      footer={
        phase === 'select' ? (
          <Space>
            <Button onClick={onClose}>取消</Button>
            <Button type="primary" disabled={!selectedIds.length || loadingList} onClick={start}>
              开始诊断{selectedIds.length ? `（${selectedIds.length}）` : ''}
            </Button>
          </Space>
        ) : (
          <Space>
            {phase === 'running' && (
              <Button disabled={cancelRequested} onClick={() => { cancelRef.current = true; setCancelRequested(true) }}>
                取消剩余
              </Button>
            )}
            <Button type="primary" disabled={phase === 'running'} onClick={onClose}>关闭</Button>
          </Space>
        )
      }
    >
      {phase === 'select' ? (
        <Table
          rowKey="id"
          size="small"
          loading={loadingList}
          dataSource={connections}
          rowSelection={{ selectedRowKeys: selectedIds, onChange: (keys) => setSelectedIds(keys as number[]) }}
          columns={[
            { title: '实例', dataIndex: 'name' },
            { title: '类型', dataIndex: 'db_type', width: 100, render: (v: string) => v ? <Tag>{v}</Tag> : '-' },
            { title: '地址', width: 220, render: (_, r: DBConnection) => r.host ? `${r.host}${r.port ? ':' + r.port : ''}` : '-' },
          ]}
          pagination={false}
        />
      ) : (
        <Table
          rowKey="id"
          size="small"
          dataSource={items}
          columns={[
            { title: '实例', dataIndex: 'name' },
            { title: '状态', width: 120, render: (_, r: BatchItem) => <Tag color={BATCH_STATUS_MAP[r.status].color}>{BATCH_STATUS_MAP[r.status].text}</Tag> },
            { title: '错误信息', dataIndex: 'error', ellipsis: true, render: (v: string) => v || '-' },
          ]}
          pagination={false}
        />
      )}
    </Modal>
  )
}

// ============ 诊断报告 ============
function ReportsTab() {
  const [reports, setReports] = useState<DBReport[]>([])
  const [detail, setDetail] = useState<DBReport | null>(null)
  const [logPage, setLogPage] = useState(1)
  const [logTotal, setLogTotal] = useState(0)
  const [batchOpen, setBatchOpen] = useState(false)
  const PAGE_SIZE = 20

  const load = (page = 1) => {
    getDbDiagnosisReports(page, PAGE_SIZE).then((d) => {
      setReports(((d?.items) || []) as DBReport[])
      setLogTotal(d?.total || 0)
      setLogPage(d?.page || page)
    })
  }
  useEffect(() => { load(1) }, [])

  return (
    <div>
      <Card size="small" title="诊断报告" extra={<Button size="small" icon={<PlayCircleOutlined />} onClick={() => setBatchOpen(true)}>批量发起诊断</Button>}>
        <Table rowKey="id" dataSource={reports} size="small"
          pagination={{ current: logPage, pageSize: PAGE_SIZE, total: logTotal, showSizeChanger: false, showTotal: (t) => `共 ${t} 条`, onChange: (p) => load(p) }}
          columns={[
            { title: 'ID', dataIndex: 'id', width: 60 },
            { title: '数据库', dataIndex: 'db_name', width: 150, render: (v: string, r: DBReport) => v ? <span>{v}{r.db_type ? <Tag style={{ marginLeft: 4 }}>{r.db_type}</Tag> : null}</span> : '-' },
            { title: '结论', dataIndex: 'conclusion', ellipsis: true },
            { title: '根因', dataIndex: 'root_cause', ellipsis: true },
            { title: '时间', dataIndex: 'created_at', width: 170 },
            { title: '操作', width: 140, render: (_, r) => (
              <Space>
                <Button size="small" onClick={() => setDetail(r)}>详情</Button>
                <Button size="small" onClick={async () => {
                  try {
                    const blob = await downloadInspectionReport(r.id)
                    const url = URL.createObjectURL(blob)
                    const a = document.createElement('a')
                    a.href = url; a.download = `db-inspect-${r.id}.md`; a.click()
                    URL.revokeObjectURL(url)
                  } catch { message.error('下载失败') }
                }}>下载</Button>
              </Space>
            ) },
          ]} />
      </Card>
      <Drawer title={`诊断报告 #${detail?.id}`} open={!!detail} onClose={() => setDetail(null)} width={720}>
        {detail && (
          <div>
            <div style={{ marginBottom: 12 }}><b>结论</b><Markdown text={detail.conclusion} /></div>
            <div style={{ marginBottom: 12 }}><b>根因</b><Markdown text={detail.root_cause} /></div>
            <div style={{ marginBottom: 12 }}><b>优化建议</b>
              {(() => {
                try {
                  const arr = JSON.parse(detail.suggestion || '[]')
                  if (Array.isArray(arr)) return <div style={{ marginTop: 6 }}>{arr.map((s, i) => <Suggestion key={i} s={s} idx={i + 1} />)}</div>
                } catch { /* 非 JSON，按 Markdown 展示 */ }
                return <Markdown text={detail.suggestion} />
              })()}
            </div>
          </div>
        )}
      </Drawer>
      <BatchDiagnosisModal open={batchOpen} onClose={() => setBatchOpen(false)} onRefresh={() => load(1)} />
    </div>
  )
}
