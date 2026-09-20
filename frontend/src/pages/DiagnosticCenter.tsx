// 诊断中心：数据库诊断（按需触发）+ 报告 + 趋势（应用诊断在 AI 诊断助手）
import { useEffect, useState } from 'react'
import type { DBConnection } from '../types'
import {
  Tabs, Card, Button, Table, Modal, Form, Input, Select, InputNumber, Space, Tag, message, Popconfirm, Drawer, Switch,
} from 'antd'
import { PlusOutlined, PlayCircleOutlined, DatabaseOutlined, ThunderboltOutlined, LineChartOutlined } from '@ant-design/icons'
import { Line } from '@ant-design/charts'
import {
  getDBConnections, createDBConnection, updateDBConnection,
  deleteDBConnection, testDBConnection, getSlowSQL, collectSlowSQL, getSlowSQLTrend,
  runDbDiagnosis, getDbDiagnosisReports, getDbTrends,
} from '../services/api'

interface DBReport {
  id: number
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

interface SlowSQL {
  id: number
  sql_text?: string
  exec_count?: number
  avg_latency_ms?: number
  max_latency_ms?: number
}

interface SlowTrendPoint {
  bucket?: string
  avg_latency_ms?: number
}

interface DiagnosisResult {
  conclusion?: string
  root_cause?: string
  suggestions?: string[]
}

export default function DiagnosticCenter() {
  return (
    <Tabs
      defaultActiveKey="db"
      items={[
        { key: 'db', label: '数据库诊断', children: <DBConnections /> },
        { key: 'reports', label: '诊断报告与趋势', children: <ReportsTab /> },
      ]}
    />
  )
}

// ============ 诊断报告与趋势 ============
function ReportsTab() {
  const [reports, setReports] = useState<DBReport[]>([])
  const [conns, setConns] = useState<DBConnection[]>([])
  const [connId, setConnId] = useState<number>()
  const [metricType, setMetricType] = useState('connections')
  const [trends, setTrends] = useState<TrendPoint[]>([])
  const [detail, setDetail] = useState<DBReport | null>(null)

  const load = () => {
    getDbDiagnosisReports().then((d) => setReports((d || []) as DBReport[]))
    getDBConnections().then((d) => setConns((d || []) as DBConnection[]))
  }
  useEffect(() => { load() }, [])

  const loadTrend = (cid?: number, mt?: string) => {
    const id = cid ?? connId
    const type = mt ?? metricType
    if (!id) return
    getDbTrends(id, type).then((d) => setTrends((d || []) as TrendPoint[]))
  }

  const trendConfig = {
    data: trends,
    xField: 'sampled_at',
    yField: 'value',
    seriesField: 'metric_name',
    height: 280,
    smooth: true,
  }

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Select placeholder="选择数据库" style={{ width: 220 }} value={connId} onChange={(v) => { setConnId(v); loadTrend(v, metricType) }}
          options={conns.map((c) => ({ value: c.id, label: `${c.name}（${c.db_type}）` }))} />
        <Select style={{ width: 160 }} value={metricType} onChange={(v) => { setMetricType(v); loadTrend(connId, v) }}
          options={[
            { value: 'connections', label: '连接数' },
            { value: 'lock_wait', label: '锁等待' },
            { value: 'tablespace', label: '表空间' },
          ]} />
      </Space>
      <Card size="small" title="指标趋势（诊断时采集，多次诊断形成）">
        {trends.length ? <Line {...trendConfig} /> : <div style={{ color: '#999', padding: 16 }}>选择数据库后查看趋势；每次点「诊断」都会采集一次指标</div>}
      </Card>
      <Card size="small" title="诊断报告" style={{ marginTop: 16 }}>
        <Table rowKey="id" dataSource={reports} pagination={false} size="small"
          columns={[
            { title: 'ID', dataIndex: 'id', width: 60 },
            { title: '结论', dataIndex: 'conclusion', ellipsis: true },
            { title: '根因', dataIndex: 'root_cause', ellipsis: true },
            { title: '时间', dataIndex: 'created_at', width: 170 },
            { title: '操作', width: 80, render: (_, r) => <Button size="small" onClick={() => setDetail(r)}>详情</Button> },
          ]} />
      </Card>
      <Drawer title={`诊断报告 #${detail?.id}`} open={!!detail} onClose={() => setDetail(null)} width={720}>
        {detail && (
          <div>
            <div style={{ marginBottom: 12 }}><b>结论：</b>{detail.conclusion}</div>
            <div style={{ marginBottom: 12 }}><b>根因：</b>{detail.root_cause}</div>
            <div style={{ marginBottom: 12 }}><b>建议：</b><pre style={{ whiteSpace: 'pre-wrap' }}>{detail.suggestion}</pre></div>
          </div>
        )}
      </Drawer>
    </div>
  )
}


function DBConnections() {
  const [conns, setConns] = useState<DBConnection[]>([])
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<DBConnection | null>(null)
  const [slowSQL, setSlowSQL] = useState<SlowSQL[]>([])
  const [slowTrend, setSlowTrend] = useState<SlowTrendPoint[]>([])
  const [slowDrawer, setSlowDrawer] = useState(false)
  const [slowConn, setSlowConn] = useState<DBConnection | null>(null)
  const [sqlDetail, setSqlDetail] = useState<string>('')
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
      if (!payload.readonly_password) delete payload.readonly_password
      await updateDBConnection(editing.id, payload)
    } else {
      await createDBConnection(values)
    }
    message.success('已保存')
    setModalOpen(false)
    load()
  }

  const openSlowSQL = async (c: DBConnection) => {
    setSlowConn(c)
    setSlowDrawer(true)
    getSlowSQL(c.id).then((d) => setSlowSQL((d || []) as SlowSQL[]))
    getSlowSQLTrend(c.id, 24).then((d) => setSlowTrend((d || []) as SlowTrendPoint[]))
  }

  const runDiagnosis = async (c: DBConnection) => {
    message.loading({ content: `正在诊断 ${c.name}...`, key: 'diag' })
    try {
      const res = (await runDbDiagnosis(c.id)) as { data?: DiagnosisResult }
      const d = res?.data
      message.destroy('diag')
      if (d) {
        message.success('诊断完成')
        Modal.info({ title: `诊断结果：${c.name}`, width: 640, content: (
          <div>
            <div style={{ marginBottom: 8 }}><b>结论：</b>{d.conclusion || '-'}</div>
            <div style={{ marginBottom: 8 }}><b>根因：</b>{d.root_cause || '-'}</div>
            <div><b>建议：</b></div>
            <ul>{(d.suggestions || []).map((s: string, i: number) => <li key={i}>{s}</li>)}</ul>
            <div style={{ color: '#999', fontSize: 12, marginTop: 8 }}>⚠️ 诊断仅提供解决方案，不执行任何修改，具体操作由 DBA 人工执行</div>
          </div>
        ) })
      }
    } catch (e) {
      message.destroy('diag')
      message.error((e as { response?: { data?: { message?: string } } })?.response?.data?.message || '诊断失败')
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
      <Button type="primary" icon={<DatabaseOutlined />} style={{ marginBottom: 16 }} onClick={() => openModal()}>添加数据库连接</Button>
      <Table rowKey="id" dataSource={conns} pagination={false}
        columns={[
          { title: '名称', dataIndex: 'name' },
          { title: '类型', dataIndex: 'db_type', render: (t) => <Tag>{t}</Tag> },
          { title: '地址', render: (_, r) => `${r.host}:${r.port}` },
          { title: '库', dataIndex: 'database' },
          { title: '慢SQL阈值(s)', dataIndex: 'slow_sql_threshold' },
          { title: '状态', dataIndex: 'status', render: (s) => <Tag color={s === 'connected' ? 'green' : 'default'}>{s}</Tag> },
          { title: '采集', dataIndex: 'collect_enabled', width: 70, render: (v, r) => <Switch size="small" checked={v} onChange={(checked) => toggleCollect(r, checked)} /> },
          { title: '巡检', dataIndex: 'inspect_enabled', width: 70, render: (v, r) => <Switch size="small" checked={v} onChange={(checked) => toggleInspect(r, checked)} /> },
          { title: '操作', render: (_, r) => (
            <Space>
              <Button size="small" type="primary" icon={<PlayCircleOutlined />} onClick={() => runDiagnosis(r)}>诊断</Button>
              <Button size="small" icon={<ThunderboltOutlined />} onClick={() => openSlowSQL(r)}>慢SQL</Button>
              <Button size="small" onClick={() => testConn(r)}>测试连接</Button>
              <Button size="small" onClick={async () => { await collectSlowSQL(r.id); message.success('已触发采集') }}>采集</Button>
              <Button size="small" onClick={() => openModal(r)}>编辑</Button>
              <Popconfirm title="确认删除？" onConfirm={async () => { await deleteDBConnection(r.id); load() }}>
                <Button size="small" danger>删除</Button>
              </Popconfirm>
            </Space>
          ) },
        ]} />

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
          <Form.Item name="username" label="用户名" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="password" label={editing ? '密码（留空=不修改）' : '密码'} rules={editing ? [] : [{ required: true }]}>
            <Input.Password />
          </Form.Item>
          <div style={{ color: '#999', fontSize: 12, marginBottom: 8 }}>独立只读诊断账号（最小权限：查系统视图/performance_schema，诊断与采集统一用它）：</div>
          <Form.Item name="readonly_username" label="只读账号"><Input placeholder="如 readonly_user" /></Form.Item>
          <Form.Item name="readonly_password" label={editing ? '只读账号密码（留空=不修改）' : '只读账号密码'}>
            <Input.Password placeholder="用于诊断采集（连接数/锁/表空间/慢SQL）" />
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

      <Drawer title={`慢SQL：${slowConn?.name || ''}`} open={slowDrawer} onClose={() => setSlowDrawer(false)} width={860}>
        {slowTrend.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontWeight: 600, marginBottom: 8 }}>平均耗时趋势（按小时，近 24h）</div>
            <Line
              data={slowTrend}
              xField="bucket"
              yField="avg_latency_ms"
              height={200}
              point={{ size: 3 }}
              tooltip={{ title: 'bucket' }}
            />
          </div>
        )}
        <Table rowKey="id" dataSource={slowSQL} pagination={false} size="small"
          columns={[
            { title: 'SQL', dataIndex: 'sql_text', render: (v) => (
              <Space size={4} style={{ maxWidth: '100%' }}>
                <span style={{ maxWidth: 480, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'inline-block', verticalAlign: 'middle' }}>{v}</span>
                <Button size="small" type="link" onClick={() => setSqlDetail(v)}>完整</Button>
              </Space>
            ) },
            { title: '执行次数', dataIndex: 'exec_count', width: 90 },
            { title: '平均(ms)', dataIndex: 'avg_latency_ms', width: 100 },
            { title: '最大(ms)', dataIndex: 'max_latency_ms', width: 100 },
          ]} />
      </Drawer>
      <Modal title="完整 SQL" open={!!sqlDetail} onCancel={() => setSqlDetail('')} footer={null} width={820}>
        <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all', background: '#f5f6fa', padding: 12, borderRadius: 6, maxHeight: 420, overflow: 'auto', fontSize: 13 }}>{sqlDetail}</pre>
      </Modal>
    </div>
  )
}
