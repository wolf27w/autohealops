// 告警策略页：审核人值班 + 日志告警规则
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card, Tabs, Table, Button, Modal, Form, Input, InputNumber, Select, Switch, Space, Tag, Popconfirm, message, Calendar } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import {
  getOnDuty, createOnDuty, updateOnDuty, deleteOnDuty,
  getLogAlertRules, createLogAlertRule, updateLogAlertRule, deleteLogAlertRule, getUsers, getLogSources,
  getResourceAlertRules, createResourceAlertRule, updateResourceAlertRule, deleteResourceAlertRule, getClusters,
  getAlertPolicies, updateAlertPolicy, getNotificationConfigs,
  getDutyCalendar, createDutyOverride, deleteDutyOverride,
} from '../services/api'
import type { Cluster, LogSource, NotificationConfig, User } from '../types'
import type { Dayjs } from 'dayjs'

const dayTypeLabel: Record<string, string> = { weekday: '工作日', weekend: '周末' }

interface DutyItem {
  id: number
  name?: string
  day_type?: string
  shift_type?: string
  start_time?: string
  end_time?: string
  user_ids?: string
  enabled?: boolean
  [key: string]: unknown
}

interface LogAlertRule {
  id: number
  name?: string
  index_pattern?: string
  log_source_id?: number | null
  keywords?: string | string[]
  time_window?: number
  threshold?: number
  severity?: string
  enabled?: boolean
  [key: string]: unknown
}

interface ResourceAlertRule {
  id: number
  name?: string
  cluster_id?: number
  namespace_match?: string
  container_match?: string
  metric_type?: string
  threshold_pct?: number
  duration_seconds?: number
  severity?: string
  enabled?: boolean
  [key: string]: unknown
}

interface AlertPolicy {
  id: number
  fault_type?: string
  category?: string
  severity?: string
  observe_seconds?: number
  restart_threshold?: number
  notify_config_id?: number | null
  enabled?: boolean
  [key: string]: unknown
}

interface DutyDay {
  date: string
  holiday?: string
  users?: string[]
  overrides?: Array<{ substitute?: string }>
  [key: string]: unknown
}

// ============ 审核人值班 ============
function DutyTab() {
  const [data, setData] = useState<DutyItem[]>([])
  const [users, setUsers] = useState<User[]>([])
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<DutyItem | null>(null)
  const [form] = Form.useForm()

  const load = () => getOnDuty().then((d) => setData(d as DutyItem[]))
  useEffect(() => { load(); getUsers().then((d) => setUsers(d || [])) }, [])

  const nameMap: Record<number, string> = {}
  users.forEach((u) => { nameMap[u.id] = u.username })
  const toNames = (ids: string) => (ids || '').split(',').filter(Boolean).map((x) => nameMap[Number(x)] || x).join('、')
  const toIdArray = (ids: string) => (ids || '').split(',').filter(Boolean).map(Number)

  const save = async () => {
    const v = await form.validateFields()
    const payload = { ...v, user_ids: Array.isArray(v.user_ids) ? v.user_ids.join(',') : v.user_ids }
    if (editing) await updateOnDuty(editing.id, payload)
    else await createOnDuty(payload)
    message.success('已保存'); setOpen(false); form.resetFields(); load()
  }

  const openEdit = (r: DutyItem) => { setEditing(r); form.setFieldsValue({ ...r, user_ids: toIdArray(r.user_ids ?? '') }); setOpen(true) }

  return (
    <Card>
      <div style={{ marginBottom: 12, color: '#666' }}>
        所有异常都由 AI 诊断处理；需要审核的操作，由这里的值班审核人处理。当天值班人优先，无值班人时由管理员兜底。
      </div>
      <Button type="primary" icon={<PlusOutlined />} style={{ marginBottom: 16 }} onClick={() => { setEditing(null); form.resetFields(); setOpen(true) }}>新增审核人值班</Button>
      <Table rowKey="id" dataSource={data} columns={[
        { title: '名称', dataIndex: 'name' },
        { title: '适用日', dataIndex: 'day_type', render: (d) => <Tag>{dayTypeLabel[d] || d}</Tag> },
        { title: '班次', dataIndex: 'shift_type', render: (s) => (s === 'day' ? '白班' : '夜班') },
        { title: '时间段', render: (_, r) => `${r.start_time} - ${r.end_time}` },
        { title: '审核人', dataIndex: 'user_ids', render: (ids) => toNames(ids) || '-' },
        { title: '启用', dataIndex: 'enabled', render: (v) => (v ? '是' : '否') },
        { title: '操作', render: (_, r) => (
          <Space>
            <Button size="small" onClick={() => openEdit(r)}>编辑</Button>
            <Popconfirm title="确认删除？" onConfirm={async () => { await deleteOnDuty(r.id); load() }}>
              <Button size="small" danger>删除</Button>
            </Popconfirm>
          </Space>
        ) },
      ]} />
      <Modal title={editing ? '编辑审核人值班' : '新增审核人值班'} open={open} onOk={save} onCancel={() => setOpen(false)}>
        <Form form={form} layout="vertical" initialValues={{ day_type: 'weekday', shift_type: 'day', start_time: '09:00', end_time: '18:00', enabled: true }}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input placeholder="如 工作日白班" /></Form.Item>
          <Form.Item name="day_type" label="适用日"><Select options={[
            { value: 'weekday', label: '工作日' }, { value: 'weekend', label: '周末' },
          ]} /></Form.Item>
          <Form.Item name="shift_type" label="班次"><Select options={[
            { value: 'day', label: '白班' }, { value: 'night', label: '夜班' },
          ]} /></Form.Item>
          <Form.Item name="start_time" label="开始时间"><Input placeholder="09:00" /></Form.Item>
          <Form.Item name="end_time" label="结束时间"><Input placeholder="18:00（夜班可跨天如 09:00）" /></Form.Item>
          <Form.Item name="user_ids" label="审核人（用户名）" rules={[{ required: true }]}>
            <Select mode="multiple" placeholder="选择审核人" optionFilterProp="label"
              options={users.map((u) => ({ value: u.id, label: u.username }))} />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked"><Switch /></Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}

// ============ 日志告警规则 ============
function LogRuleTab() {
  const navigate = useNavigate()
  const [data, setData] = useState<LogAlertRule[]>([])
  const [sources, setSources] = useState<LogSource[]>([])
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<LogAlertRule | null>(null)
  const [form] = Form.useForm()

  const load = () => getLogAlertRules().then((d) => setData(d as LogAlertRule[]))
  useEffect(() => { load(); getLogSources().then((d) => setSources(d || [])) }, [])

  const sourceOptions = sources.map((s) => ({ value: s.id, label: `${s.name}（${s.es_url}）` }))
  const sourceName = (id: number | null | undefined) => {
    if (!id) return '默认源'
    const s = sources.find((x) => x.id === id)
    return s ? s.name : `#${id}`
  }

  const save = async () => {
    const v = await form.validateFields()
    if (editing) await updateLogAlertRule(editing.id, v)
    else await createLogAlertRule(v)
    message.success('已保存'); setOpen(false); form.resetFields(); load()
  }

  return (
    <Card>
      <div style={{ marginBottom: 12, color: '#8c8c8c', fontSize: 12 }}>
        ES 日志源在「<a onClick={() => navigate('/log-sources')}>日志源</a>」页维护（支持多实例；可分别配置时间字段 / 搜索字段等）——
        这里每条规则可以<strong>指定使用哪个源</strong>；不指定则用默认源。扫描周期约 2 分钟。
      </div>
      <Button type="primary" icon={<PlusOutlined />} style={{ marginBottom: 16 }} onClick={() => { setEditing(null); form.resetFields(); setOpen(true) }}>新增规则</Button>
      <Table rowKey="id" dataSource={data} columns={[
        { title: '规则名', dataIndex: 'name' },
        { title: '索引', dataIndex: 'index_pattern' },
        { title: '日志源', dataIndex: 'log_source_id', render: (v) => sourceName(v) },
        { title: '关键词', dataIndex: 'keywords', ellipsis: true },
        { title: '窗口(分)', dataIndex: 'time_window' },
        { title: '阈值', dataIndex: 'threshold' },
        { title: '级别', dataIndex: 'severity' },
        { title: '启用', dataIndex: 'enabled', render: (v) => (v ? '是' : '否') },
        { title: '操作', render: (_, r) => (
          <Space>
            <Button size="small" onClick={() => { setEditing(r); form.setFieldsValue({ ...r, keywords: typeof r.keywords === 'string' ? r.keywords : JSON.stringify(r.keywords) }); setOpen(true) }}>编辑</Button>
            <Popconfirm title="确认删除？" onConfirm={async () => { await deleteLogAlertRule(r.id); load() }}>
              <Button size="small" danger>删除</Button>
            </Popconfirm>
          </Space>
        ) },
      ]} />
      <Modal title={editing ? '编辑规则' : '新增规则'} open={open} onOk={save} onCancel={() => setOpen(false)}>
        <Form form={form} layout="vertical" initialValues={{ cluster_id: 0, time_window: 5, threshold: 3, severity: 'medium', enabled: true }}>
          <Form.Item name="name" label="规则名" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="index_pattern" label="ES 索引（如 app-*）" rules={[{ required: true }]}
            tooltip="不选日志源时用这里的索引；选了日志源则以该源的索引为准（此处会被忽略）"><Input /></Form.Item>
          <Form.Item name="log_source_id" label="日志源（不选=默认源）"
            tooltip="选择后使用该日志源（地址/账号/索引都取该源）">
            <Select allowClear placeholder="默认源（不指定）" options={sourceOptions} />
          </Form.Item>
          <Form.Item name="keywords" label="关键词（JSON 数组，如 [&apos;error&apos;,&apos;exception&apos;]）" rules={[{ required: true }]}><Input placeholder='["error","exception"]' /></Form.Item>
          <Form.Item name="cluster_id" label="绑定集群（0=全局）"><InputNumber min={0} style={{ width: '100%' }} /></Form.Item>
          <Form.Item name="namespace_filter" label="命名空间过滤（可选）"><Input /></Form.Item>
          <Form.Item name="time_window" label="统计窗口（分钟）"><InputNumber min={1} style={{ width: '100%' }} /></Form.Item>
          <Form.Item name="threshold" label="阈值（命中次数）"><InputNumber min={1} style={{ width: '100%' }} /></Form.Item>
          <Form.Item name="severity" label="告警级别"><Select options={['critical', 'high', 'medium', 'low'].map((s) => ({ value: s, label: s }))} /></Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked"><Switch /></Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}

// ============ 容器资源阈值规则（Prometheus） ============
const METRIC_SHORT: Record<string, string> = {
  cpu: 'CPU', memory: '内存', net_rx: '入站流量', net_tx: '出站流量',
}
const METRIC_TAG_COLOR: Record<string, string> = {
  cpu: 'blue', memory: 'green', net_rx: 'cyan', net_tx: 'purple',
}
const isPercentMetric = (m?: string) => m !== 'net_rx' && m !== 'net_tx'

const METRIC_OPTIONS = [
  { label: '容器资源', options: [
    { value: 'cpu', label: '容器 CPU 使用率（%）' },
    { value: 'memory', label: '容器内存使用率（%）' },
    { value: 'net_rx', label: '容器入站流量（MB/s）' },
    { value: 'net_tx', label: '容器出站流量（MB/s）' },
  ] },
  { label: '中间件（需集群部署对应 exporter）', options: [
    { value: 'redis_connections', label: 'Redis 连接数' },
    { value: 'redis_memory_pct', label: 'Redis 内存使用率（%）' },
    { value: 'mysql_connections', label: 'MySQL 连接数' },
    { value: 'mysql_slow_queries', label: 'MySQL 慢查询速率（/s）' },
    { value: 'rocketmq_backlog', label: 'RocketMQ 消费者积压' },
    { value: 'rocketmq_get_count', label: 'RocketMQ 消费速率（/s）' },
    { value: 'mongodb_connections', label: 'MongoDB 连接数' },
    { value: 'elasticsearch_status', label: 'ES 集群状态（0绿/1黄/2红）' },
    { value: 'rabbitmq_connections', label: 'RabbitMQ 连接数' },
  ] },
]

function ResourceRuleTab() {
  const [data, setData] = useState<ResourceAlertRule[]>([])
  const [clusters, setClusters] = useState<Cluster[]>([])
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<ResourceAlertRule | null>(null)
  const [form] = Form.useForm()

  const load = () => getResourceAlertRules().then((d) => setData(d as ResourceAlertRule[]))
  useEffect(() => { load(); getClusters().then((d) => setClusters(d || [])) }, [])

  const clusterOptions = [
    { value: 0, label: '全部集群' },
    ...clusters.map((c) => ({ value: c.id, label: `${c.name} (#${c.id})` })),
  ]
  const clusterName = (id: number) => {
    const c = clusters.find((x) => x.id === id)
    return c ? c.name : (id ? `#${id}` : '全部')
  }

  const save = async () => {
    const v = await form.validateFields()
    if (editing) await updateResourceAlertRule(editing.id, v)
    else await createResourceAlertRule(v)
    message.success('已保存'); setOpen(false); form.resetFields(); load()
  }

  return (
    <Card>
      <div style={{ marginBottom: 12, color: '#666' }}>
        容器 CPU/内存使用率（usage ÷ limit）超阈值持续 N 秒后告警，走项目路由发送；<b>只通知不自动修复</b>。
        无 limit 的容器自动跳过。namespace 支持前缀通配（如 <Tag>prod-*</Tag> 圈生产）。数据源：Prometheus。
        <br />中间件指标（Redis/MySQL/RocketMQ 等）需集群部署对应 exporter 后才有数据，规则可先建，exporter 上线自动生效。
      </div>
      <Button type="primary" icon={<PlusOutlined />} style={{ marginBottom: 16 }}
        onClick={() => { setEditing(null); form.resetFields(); form.setFieldsValue({ cluster_id: 0, metric_type: 'memory', threshold_pct: 80, duration_seconds: 300, severity: 'medium', enabled: true }); setOpen(true) }}>
        新增资源阈值规则
      </Button>
      <Table rowKey="id" dataSource={data} columns={[
        { title: '规则名', dataIndex: 'name', width: 160 },
        { title: '集群', dataIndex: 'cluster_id', width: 110, render: (v) => clusterName(v) },
        { title: 'namespace', dataIndex: 'namespace_match', width: 120, render: (v) => v || '全部' },
        { title: '容器', dataIndex: 'container_match', width: 120, render: (v) => v || '全部' },
        { title: '指标', dataIndex: 'metric_type', width: 130, render: (t: string) => <Tag color={METRIC_TAG_COLOR[t] || 'default'}>{METRIC_SHORT[t] || t}</Tag> },
        { title: '阈值', dataIndex: 'threshold_pct', width: 110, render: (v: number, r: ResourceAlertRule) => isPercentMetric(r.metric_type) ? `${v}%` : `${v} MB/s` },
        { title: '持续(秒)', dataIndex: 'duration_seconds', width: 90 },
        { title: '级别', dataIndex: 'severity', width: 80 },
        { title: '启用', dataIndex: 'enabled', width: 70, render: (v) => (v ? '是' : '否') },
        { title: '操作', render: (_, r) => (
          <Space>
            <Button size="small" onClick={() => { setEditing(r); form.setFieldsValue(r); setOpen(true) }}>编辑</Button>
            <Popconfirm title="确认删除？" onConfirm={async () => { await deleteResourceAlertRule(r.id); load() }}>
              <Button size="small" danger>删除</Button>
            </Popconfirm>
          </Space>
        ) },
      ]} />
      <Modal title={editing ? '编辑资源阈值规则' : '新增资源阈值规则'} open={open} onOk={save} onCancel={() => setOpen(false)}>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="规则名" rules={[{ required: true }]}><Input placeholder="如 生产内存超限告警" /></Form.Item>
          <Form.Item name="cluster_id" label="集群（空=全部）"><Select options={clusterOptions} allowClear placeholder="选择集群" /></Form.Item>
          <Form.Item name="namespace_match" label="namespace 匹配" tooltip="空=全部；prod-* 表示所有 prod 开头的 namespace（圈生产环境）">
            <Input placeholder="prod-*（支持 * 前缀通配）" />
          </Form.Item>
          <Form.Item name="container_match" label="容器名匹配（空=全部容器）"><Input placeholder="如 app / redis" /></Form.Item>
          <Form.Item name="metric_type" label="指标">
            <Select options={METRIC_OPTIONS} />
          </Form.Item>
          <Form.Item name="threshold_pct" label={isPercentMetric(Form.useWatch('metric_type', form)) ? '阈值（%）' : '阈值（MB/s）'}
            tooltip="百分比类指标单位是 %；网络入站/出站流量指标单位是 MB/s"
            rules={[{ required: true }]}><InputNumber min={0.01} step={isPercentMetric(Form.useWatch('metric_type', form)) ? 1 : 0.5} style={{ width: '100%' }} /></Form.Item>
          <Form.Item name="duration_seconds" label="持续超限（秒）" tooltip="持续超阈值这么久才告警（防抖）"><InputNumber min={10} style={{ width: '100%' }} /></Form.Item>
          <Form.Item name="severity" label="告警级别"><Select options={['high', 'medium'].map((s) => ({ value: s, label: s }))} /></Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked"><Switch /></Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}


// ============ 故障分类策略（AlertPolicy：分类 + 通知渠道） ============
const CATEGORY_LABEL: Record<string, string> = { A: '观察', B: '直接报', C: '自动处理' }

function PolicyTab() {
  const [data, setData] = useState<AlertPolicy[]>([])
  const [configs, setConfigs] = useState<NotificationConfig[]>([])

  const load = () => {
    getAlertPolicies().then((d) => setData((d || []) as AlertPolicy[]))
    getNotificationConfigs().then((d) => setConfigs(d || []))
  }
  useEffect(() => { load() }, [])

  const configOptions = configs.map((c) => ({ value: c.id, label: `${c.name}（${c.channel_type}）` }))
  const configName = (id: number | null | undefined) => {
    if (!id) return '未配置（只站内）'
    const c = configs.find((x) => x.id === id)
    return c ? `${c.name}（${c.channel_type}）` : `#${id}`
  }

  const setNotify = async (id: number, notifyConfigId: number | undefined) => {
    await updateAlertPolicy(id, { notify_config_id: notifyConfigId || null })
    message.success('已更新通知渠道')
    load()
  }

  return (
    <Card>
      <div style={{ marginBottom: 12, color: '#666' }}>
        按故障类型配置告警通知渠道（<b>通知发该群并 @值班+审批</b>）；未配置则只写站内。分类：A=观察、B=直接报、C=自动处理。
      </div>
      <Table rowKey="id" dataSource={data} columns={[
        { title: '故障类型', dataIndex: 'fault_type' },
        { title: '分类', dataIndex: 'category', render: (c) => <Tag color={c === 'C' ? 'green' : c === 'B' ? 'red' : 'blue'}>{CATEGORY_LABEL[c] || c}</Tag> },
        { title: '严重级别', dataIndex: 'severity' },
        { title: '观察期(秒)', dataIndex: 'observe_seconds' },
        { title: '重启阈值', dataIndex: 'restart_threshold' },
        { title: '通知渠道', dataIndex: 'notify_config_id', render: (_, r) => (
          <Select
            size="small"
            style={{ width: 220 }}
            value={r.notify_config_id}
            placeholder="未配置（只站内）"
            allowClear
            onChange={(v) => setNotify(r.id, v)}
            options={configOptions}
          />
        ) },
        { title: '启用', dataIndex: 'enabled', render: (v) => (v ? '是' : '否') },
      ]} />
    </Card>
  )
}


// ============ 值班日历 / 换班代班（C6） ============
function DutyCalendarTab() {
  const [days, setDays] = useState<DutyDay[]>([])
  const [users, setUsers] = useState<User[]>([])
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()

  const load = () => getDutyCalendar().then((d) => setDays((d as { days?: DutyDay[] }).days || []))
  useEffect(() => { load(); getUsers().then((d) => setUsers(d || [])) }, [])

  const dayMap: Record<string, DutyDay> = {}
  days.forEach((d) => { dayMap[d.date] = d })
  const fmt = (dt: Dayjs) => dt.format('YYYY-MM-DD')

  const save = async () => {
    const v = await form.validateFields()
    await createDutyOverride(v)
    message.success('已保存换班/代班')
    setOpen(false); form.resetFields(); load()
  }

  const userOptions = users.map((u) => ({ value: u.username, label: u.username }))

  return (
    <Card>
      <div style={{ marginBottom: 12, color: '#666' }}>
        值班日历：按「排班规则 + 节假日」自动算出每天值班人；临时变动用<b>换班（指定替班的人）/ 代班（整班换人）</b>覆盖当天。点日期即可新增。
      </div>
      <Calendar
        cellRender={(current, info) => {
          if (info.type !== 'date') return info.originNode
          const d = dayMap[fmt(current)]
          if (!d) return null
          return (
            <div style={{ fontSize: 12, lineHeight: '18px' }}>
              {d.holiday && <Tag color="red" style={{ marginInlineEnd: 2 }}>{d.holiday}</Tag>}
              {(d.users || []).length === 0
                ? <span style={{ color: '#bbb' }}>无值班</span>
                : (d.users as string[]).map((u) => (
                  <Tag key={u} color={d.overrides?.some((o) => o.substitute === u) ? 'orange' : 'blue'} style={{ marginBottom: 2 }}>{u}</Tag>
                ))}
            </div>
          )
        }}
        onSelect={(dt) => { form.resetFields(); form.setFieldsValue({ date: fmt(dt), type: 'swap' }); setOpen(true) }}
      />
      <Modal title="换班 / 代班" open={open} onOk={save} onCancel={() => setOpen(false)}>
        <Form form={form} layout="vertical" initialValues={{ type: 'swap' }}>
          <Form.Item name="date" label="日期" rules={[{ required: true }]}><Input disabled /></Form.Item>
          <Form.Item name="type" label="类型">
            <Select options={[{ value: 'swap', label: '换班（替换指定值班人）' }, { value: 'substitute', label: '代班（加一名值班人）' }]} />
          </Form.Item>
          <Form.Item name="original_username" label="被替换人（换班时填；代班留空）">
            <Select allowClear options={userOptions} placeholder="如 zhangsan" />
          </Form.Item>
          <Form.Item name="substitute_username" label="替班人" rules={[{ required: true }]}>
            <Select options={userOptions} placeholder="选择替班人" />
          </Form.Item>
          <Form.Item name="note" label="备注"><Input placeholder="如：家里有事，调休" /></Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}


export default function AlertPolicies() {
  return (
    <Tabs items={[
      { key: 'policy', label: '故障分类策略', children: <PolicyTab /> },
      { key: 'duty', label: '审核人值班', children: <DutyTab /> },
      { key: 'duty-calendar', label: '值班日历', children: <DutyCalendarTab /> },
      { key: 'log', label: '日志告警规则', children: <LogRuleTab /> },
      { key: 'resource', label: '容器资源阈值', children: <ResourceRuleTab /> },
    ]} />
  )
}
