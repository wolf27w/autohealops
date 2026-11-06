// 诊断中心：数据库诊断（按需触发）+ 报告 + 趋势（应用诊断在 AI 诊断助手）
import { useEffect, useState } from 'react'
import type { DBConnection } from '../types'
import {
  Tabs, Card, Button, Table, Modal, Form, Input, Select, InputNumber, Space, Tag, message, Popconfirm, Drawer, Switch, Progress,
} from 'antd'
import { PlusOutlined, PlayCircleOutlined, DatabaseOutlined, LineChartOutlined } from '@ant-design/icons'
import Markdown from '../components/Markdown'
import DBMonitor from './DBMonitor'
import DbWorkbench, { type ConsoleConn } from '../components/DbWorkbench'
import {
  getDBConnections, createDBConnection, updateDBConnection,
  deleteDBConnection, testDBConnection, collectSlowSQL,
  runDbDiagnosis, getDbDiagnosisReports, downloadInspectionReport, getDbDiagnosisProgress,
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
  return (
    <>
      <div style={{ marginBottom: 8, color: '#8c8c8c', fontSize: 12 }}>
        <b>数据库管理</b>：连接实例 + SQL 控制台（指标采集 / 慢 SQL / 巡检报告）。集群故障诊断见「诊断 → 集群故障诊断」；Pod / 应用类见「应用故障诊断」；会话式深挖用「应用诊断助手」。
      </div>
    <Tabs
      defaultActiveKey="db"
      items={[
        { key: 'db', label: '数据库管理', children: <DBConnections /> },
        { key: 'reports', label: '诊断报告', children: <ReportsTab /> },
        { key: 'monitor', label: '数据库监控', children: <DBMonitor /> },
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


function DBConnections() {
  const [conns, setConns] = useState<DBConnection[]>([])
  const [selId, setSelId] = useState<number | null>(null)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<DBConnection | null>(null)
  const [diag, setDiag] = useState<{ name: string; pct: number; step: string } | null>(null)
  const [form] = Form.useForm()

  const load = () => getDBConnections().then((d) => setConns((d || []) as DBConnection[]))
  const testConn = async (r: DBConnection) => {
    try {
      await testDBConnection(r.id)
      message.success('连接成功')
    } catch { /* 拦截器已提示失败原因 */ }
  }
  useEffect(() => { load() }, [])

  const openModal = (c?: DBConnection) => {
    setEditing(c || null)
    setModalOpen(true)
    if (c) {
      form.setFieldsValue({ ...c, password: '' })
    } else {
      form.resetFields()
      form.setFieldsValue({ db_type: 'mysql', port: 3306, slow_sql_threshold: 1.0, collect_interval: 5, collect_enabled: true, inspect_enabled: true })
    }
  }

  const handleSave = async () => {
    const values = await form.validateFields()
    if (editing) {
      const payload: Record<string, unknown> = { ...values }
      if (!payload.password) delete payload.password
      await updateDBConnection(editing.id, payload)
    } else {
      await createDBConnection(values)
    }
    message.success('已保存')
    setModalOpen(false)
    load()
  }

  const runDiagnosis = async (c: DBConnection) => {
    setDiag({ name: c.name, pct: 5, step: '准备中…' })
    const timer = setInterval(() => {
      getDbDiagnosisProgress(c.id)
        .then((p) => { if (p && typeof p.pct === 'number') setDiag((s) => (s ? { ...s, pct: p.pct, step: p.step } : s)) })
        .catch(() => {})
    }, 800)
    try {
      // 拦截器已把 AxiosResponse 解包为 body.data → res 本身就是诊断结果对象
      // （report_id/conclusion/root_cause/suggestions/health），不要再取 res.data
      const d = (await runDbDiagnosis(c.id)) as DiagnosisResult | null
      if (d) {
        setDiag({ name: c.name, pct: 100, step: '完成' })
        setTimeout(() => setDiag(null), 400)
        Modal.info({ title: `诊断结果：${c.name}`, width: 640, content: (
          <div>
            <div style={{ marginBottom: 8 }}><b>结论</b><Markdown text={d.conclusion} /></div>
            <div style={{ marginBottom: 8 }}><b>根因</b><Markdown text={d.root_cause} /></div>
            <div style={{ marginBottom: 4 }}><b>优化建议</b></div>
            <div>{(d.suggestions || []).map((s, i) => <Suggestion key={i} s={s} idx={i + 1} />)}</div>
            <div style={{ color: '#999', fontSize: 12, marginTop: 8 }}>⚠️ 诊断仅提供解决方案，不执行任何修改，具体操作由 DBA 人工执行</div>
          </div>
        ) })
      }
    } catch (e) {
      message.error((e as { response?: { data?: { message?: string } } })?.response?.data?.message || '诊断失败')
    } finally {
      clearInterval(timer)
      setDiag(null)
    }
  }

  const toggleCollect = async (c: DBConnection, enabled: boolean) => {
    await updateDBConnection(c.id, { collect_enabled: enabled })
    message.success(enabled ? '已开启采集' : '已关闭采集')
    load()
  }

  const toggleInspect = async (c: DBConnection, enabled: boolean) => {
    await updateDBConnection(c.id, { inspect_enabled: enabled })
    message.success(enabled ? '已开启巡检' : '已关闭巡检')
    load()
  }

  return (
    <div>
      <DbWorkbench
        conns={conns as unknown as ConsoleConn[]}
        selectedId={selId ?? (conns[0]?.id ?? null)}
        onSelect={setSelId}
        onAdd={() => openModal()}
        onDiagnose={(c) => runDiagnosis(c as unknown as DBConnection)}
        onTest={(c) => testConn(c as unknown as DBConnection)}
        onCollect={async (c) => { await collectSlowSQL(c.id); message.success('已触发采集') }}
        onEdit={(c) => openModal(c as unknown as DBConnection)}
        onDelete={async (c) => { await deleteDBConnection(c.id); load() }}
        onToggleCollect={(c, v) => toggleCollect(c as unknown as DBConnection, v)}
        onToggleInspect={(c, v) => toggleInspect(c as unknown as DBConnection, v)}
      />

      <Modal title={diag ? `正在诊断：${diag.name}` : '诊断'} open={!!diag} footer={null} closable={false}
        maskClosable={false} keyboard={false} width={420}>
        <Progress percent={diag?.pct ?? 0} status="active" />
        <div style={{ marginTop: 10, color: '#666' }}>{diag?.step || ''}</div>
      </Modal>

      <Modal title={editing ? '编辑数据库连接' : '添加数据库连接'} open={modalOpen} onOk={handleSave} onCancel={() => setModalOpen(false)}>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="db_type" label="类型" rules={[{ required: true }]}>
            <Select options={['mysql', 'pg', 'oracle'].map((t) => ({ value: t, label: t }))} />
          </Form.Item>
          <Space>
            <Form.Item name="host" label="主机" rules={[{ required: true }]}><Input /></Form.Item>
            <Form.Item name="port" label="端口" rules={[{ required: true }]}><InputNumber min={1} max={65535} /></Form.Item>
          </Space>
          <Form.Item name="database" label="数据库" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="username" label="用户名" rules={[{ required: true }]} extra="建议用只读权限账号（诊断采集只需查系统视图 / performance_schema）"><Input /></Form.Item>
          <Form.Item name="password" label={editing ? '密码（留空=不修改）' : '密码'} rules={editing ? [] : [{ required: true }]}>
            <Input.Password />
          </Form.Item>
          <Space>
            <Form.Item name="slow_sql_threshold" label="慢SQL阈值(s)"><InputNumber min={0.001} step={0.1} /></Form.Item>
            <Form.Item name="collect_interval" label="采集间隔(分钟)"><InputNumber min={1} /></Form.Item>
          </Space>
          <Form.Item name="collect_enabled" label="采集开关" valuePropName="checked"><Switch /></Form.Item>
          <Form.Item name="inspect_enabled" label="巡检开关" valuePropName="checked" tooltip="关闭后该库不参与巡检/诊断报告生成"><Switch /></Form.Item>
          <Form.Item name="inspect_cron" label="定时巡检 Cron" tooltip="分 时 日 月 周；如 0 9 * * * = 每天 09:00；留空=不定时，仅手动点「诊断」">
            <Input placeholder="留空=不定时，如 0 9 * * *" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
