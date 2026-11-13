// 告警策略页：审核人值班 + 日志告警规则
import { useEffect, useState } from 'react'
import { Alert, Card, Tabs, Table, Button, Modal, Form, Input, InputNumber, Select, Switch, Space, Tag, Popconfirm, message } from 'antd'
import { useNavigate } from 'react-router-dom'
import { PlusOutlined } from '@ant-design/icons'
import {
  getResourceAlertRules, createResourceAlertRule, updateResourceAlertRule, deleteResourceAlertRule, getClusters,
  getAlertPolicies, updateAlertPolicy, getNotificationConfigs,
} from '../services/api'
import type { Cluster, NotificationConfig } from '../types'

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


export default function AlertPolicies() {
  const navigate = useNavigate()
  return (
    <>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="审核人值班已独立成页"
        description="排班规则、值班日历、换班/代班与节假日统一在「告警与工单 → 值班排班」维护；审核时当天值班人优先，无值班人时由管理员兜底。"
        action={<Button size="small" type="primary" ghost onClick={() => navigate('/alerts/on-duty')}>去值班排班</Button>}
      />
      <Tabs items={[
        { key: 'policy', label: '故障分类策略', children: <PolicyTab /> },
        { key: 'resource', label: '容器资源阈值', children: <ResourceRuleTab /> },
      ]} />
    </>
  )
}
