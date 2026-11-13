// 诊断中心：数据库诊断（按需触发）+ 报告 + 趋势（应用诊断在 AI 诊断助手）
import { useEffect, useState } from 'react'
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

// ============ 诊断报告 ============
function ReportsTab() {
  const [reports, setReports] = useState<DBReport[]>([])
  const [detail, setDetail] = useState<DBReport | null>(null)
  const [logPage, setLogPage] = useState(1)
  const [logTotal, setLogTotal] = useState(0)
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
      <Card size="small" title="诊断报告">
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
    </div>
  )
}
