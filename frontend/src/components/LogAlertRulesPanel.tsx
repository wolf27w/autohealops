// 日志告警规则管理面板（可在「日志源」页按源配置，也可在「策略配置」页总览）
// 传 logSourceId 时：只显示该源的规则、隐藏「日志源」下拉、新建时自动归属该源
import { useEffect, useState } from 'react'
import { Table, Button, Modal, Form, Input, InputNumber, Select, Switch, Space, Popconfirm, message, Dropdown, Tag } from 'antd'
import { PlusOutlined, ImportOutlined, DownOutlined } from '@ant-design/icons'
import {
  getLogAlertRules, createLogAlertRule, updateLogAlertRule, deleteLogAlertRule,
  getLogSources, getNotificationConfigs, importBuiltinLogAlertRules,
} from '../services/api'
import type { LogSource, NotificationConfig } from '../types'

interface LogAlertRule {
  id: number
  name: string
  cluster_id?: number
  index_pattern?: string
  log_source_id?: number | null
  notify_config_id?: number | null
  keywords?: string | string[]
  fault_type?: string
  category?: string
  priority?: number
  sample_pattern?: string
  namespace_filter?: string
  time_window?: number
  threshold?: number
  severity?: string
  enabled?: boolean
}

// R12 标准应用故障类型（与后端 log_rule_presets.APP_FAULT_TYPES 一致）
const APP_FAULT_OPTIONS = [
  { value: 'AppOOM', label: 'AppOOM 内存溢出' },
  { value: 'AppCrash', label: 'AppCrash 进程崩溃' },
  { value: 'AppConnRefused', label: 'AppConnRefused 连接拒绝' },
  { value: 'AppTimeout', label: 'AppTimeout 超时' },
  { value: 'AppSQL', label: 'AppSQL SQL异常' },
  { value: 'AppConfigError', label: 'AppConfigError 配置错误' },
  { value: 'AppPermission', label: 'AppPermission 权限拒绝' },
  { value: 'AppDependency', label: 'AppDependency 下游依赖故障' },
  { value: 'AppBusiness', label: 'AppBusiness 业务错误' },
  { value: 'LogKeyword', label: 'LogKeyword 兜底关键词' },
]
const CATEGORY_OPTIONS = [
  { value: 'app', label: '应用' },
  { value: 'db', label: '数据库' },
  { value: 'infra', label: '基础设施' },
  { value: 'business', label: '业务' },
]

export default function LogAlertRulesPanel({ logSourceId }: { logSourceId?: number }) {
  const [data, setData] = useState<LogAlertRule[]>([])
  const [sources, setSources] = useState<LogSource[]>([])
  const [configs, setConfigs] = useState<NotificationConfig[]>([])
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<LogAlertRule | null>(null)
  const [form] = Form.useForm()

  const load = () => getLogAlertRules().then((d) => {
    const all = (d || []) as LogAlertRule[]
    setData(logSourceId ? all.filter((r) => r.log_source_id === logSourceId) : all)
  })
  useEffect(() => {
    load()
    getLogSources().then((d) => setSources((d || []) as LogSource[]))
    getNotificationConfigs().then((d) => setConfigs((d || []) as NotificationConfig[]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [logSourceId])

  const sourceOptions = sources.map((s) => ({ value: s.id, label: `${s.name}（${s.es_url}）` }))
  const sourceName = (id?: number | null) => {
    if (!id) return '默认源'
    const s = sources.find((x) => x.id === id)
    return s ? s.name : `#${id}`
  }
  const configName = (id?: number | null) => {
    if (!id) return <span style={{ color: '#999' }}>统一策略</span>
    const c = configs.find((x) => x.id === id)
    return c ? c.name : `#${id}`
  }

  const openNew = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({ cluster_id: 0, time_window: 5, threshold: 3, severity: 'medium', enabled: true, fault_type: 'LogKeyword', category: 'app', priority: 100, ...(logSourceId ? { log_source_id: logSourceId } : {}) })
    setOpen(true)
  }
  const openEdit = (r: LogAlertRule) => {
    setEditing(r)
    form.setFieldsValue({ ...r, keywords: typeof r.keywords === 'string' ? r.keywords : JSON.stringify(r.keywords) })
    setOpen(true)
  }
  const save = async () => {
    const v = await form.validateFields()
    if (logSourceId && !editing) v.log_source_id = logSourceId
    if (editing) await updateLogAlertRule(editing.id, v)
    else await createLogAlertRule(v)
    message.success('已保存'); setOpen(false); form.resetFields(); load()
  }

  const importBuiltin = async (lang: string) => {
    const r = await importBuiltinLogAlertRules(lang)
    message.success(`已导入 ${r.created} 条（跳过已有 ${r.skipped} 条）`)
    load()
  }

  const importMenu = {
    items: [
      { key: 'java', label: 'Java' },
      { key: 'go', label: 'Go' },
      { key: 'node', label: 'Node.js' },
      { key: 'python', label: 'Python' },
      { key: 'all', label: '全部' },
    ],
    onClick: ({ key }: { key: string }) => importBuiltin(key),
  }

  return (
    <div>
      <div style={{ marginBottom: 12, color: '#8c8c8c', fontSize: 12 }}>
        后台每 30 秒扫描一次，命中关键词超阈值即建告警。可给规则指定「通知渠道」（套用通知源）——命中后发到该渠道；
        留空则走全平台统一告警通知策略。同一规则 5 分钟内只聚合、不刷屏。
      </div>
      <Button type="primary" icon={<PlusOutlined />} style={{ marginBottom: 16 }} onClick={openNew}>新增规则</Button>
      <Dropdown menu={importMenu}>
        <Button icon={<ImportOutlined />} style={{ marginBottom: 16, marginLeft: 8 }}>
          导入内置规则 <DownOutlined />
        </Button>
      </Dropdown>
      <Table rowKey="id" dataSource={data} size="small" pagination={false} columns={[
        { title: '规则名', dataIndex: 'name' },
        ...(!logSourceId ? [{ title: '日志源', dataIndex: 'log_source_id', render: (v: number | null) => sourceName(v) }] : []),
        { title: '关键词', dataIndex: 'keywords', ellipsis: true },
        { title: '故障类型', dataIndex: 'fault_type', width: 120, render: (v: string) => <Tag color={v === 'LogKeyword' ? 'default' : 'geekblue'}>{v}</Tag> },
        { title: '分类', dataIndex: 'category', width: 80, render: (v: string) => CATEGORY_OPTIONS.find((c) => c.value === v)?.label || v },
        { title: '优先级', dataIndex: 'priority', width: 70 },
        { title: '窗口(分)', dataIndex: 'time_window', width: 90 },
        { title: '阈值', dataIndex: 'threshold', width: 70 },
        { title: '级别', dataIndex: 'severity', width: 80 },
        { title: '通知渠道', dataIndex: 'notify_config_id', width: 140, render: (v: number | null) => configName(v) },
        { title: '启用', dataIndex: 'enabled', width: 60, render: (v: boolean) => (v ? '是' : '否') },
        { title: '操作', width: 130, render: (_: unknown, r: LogAlertRule) => (
          <Space>
            <Button size="small" onClick={() => openEdit(r)}>编辑</Button>
            <Popconfirm title="确认删除？" onConfirm={async () => { await deleteLogAlertRule(r.id); load() }}>
              <Button size="small" danger>删除</Button>
            </Popconfirm>
          </Space>
        ) },
      ]} />
      <Modal title={editing ? '编辑规则' : '新增规则'} open={open} onOk={save} onCancel={() => setOpen(false)}>
        <Form form={form} layout="vertical" initialValues={{ cluster_id: 0, time_window: 5, threshold: 3, severity: 'medium', enabled: true, fault_type: 'LogKeyword', category: 'app', priority: 100 }}>
          <Form.Item name="name" label="规则名" rules={[{ required: true }]}><Input /></Form.Item>
          {!logSourceId && (
            <Form.Item name="log_source_id" label="日志源（不选=默认源）"
              tooltip="选择后使用该日志源（地址/账号/索引都取该源）">
              <Select allowClear placeholder="默认源（不指定）" options={sourceOptions} />
            </Form.Item>
          )}
          <Form.Item name="index_pattern" label="ES 索引（如 app-*）" rules={[{ required: true }]}
            tooltip="不选日志源时用这里的索引；选了日志源则以该源的索引为准（此处会被忽略）"><Input /></Form.Item>
          <Form.Item name="keywords" label="关键词（JSON 数组，如 ['error','exception']）" rules={[{ required: true }]}><Input placeholder='["error","exception"]' /></Form.Item>
          <Space.Compact block style={{ width: '100%' }}>
            <Form.Item name="fault_type" label="故障类型" style={{ width: '50%' }} tooltip="命中后写入告警的 fault_type（用于分类/统计/诊断路由）">
              <Select options={APP_FAULT_OPTIONS} />
            </Form.Item>
            <Form.Item name="category" label="分类" style={{ width: '50%' }}><Select options={CATEGORY_OPTIONS} /></Form.Item>
          </Space.Compact>
          <Space.Compact block style={{ width: '100%' }}>
            <Form.Item name="priority" label="优先级（越小越高）" style={{ width: '50%' }} tooltip="同服务多类命中时取最高优先级合并">
              <InputNumber min={1} max={100} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="sample_pattern" label="样例正则（可选）" style={{ width: '50%' }} tooltip="从日志抽取关键行做证据">
              <Input placeholder="如 .*OutOfMemoryError.*" />
            </Form.Item>
          </Space.Compact>
          <Form.Item name="notify_config_id" label="通知渠道（套用通知源）"
            tooltip="命中后发到该通知渠道；留空=走全平台统一告警通知策略">
            <Select allowClear placeholder="统一策略（不指定）"
              options={configs.map((c) => ({ value: c.id, label: `${c.name}（${c.channel_type}）` }))} />
          </Form.Item>
          <Form.Item name="cluster_id" label="绑定集群（0=全局）"><InputNumber min={0} style={{ width: '100%' }} /></Form.Item>
          <Form.Item name="namespace_filter" label="命名空间过滤（可选）"><Input /></Form.Item>
          <Form.Item name="time_window" label="统计窗口（分钟）"><InputNumber min={1} style={{ width: '100%' }} /></Form.Item>
          <Form.Item name="threshold" label="阈值（命中次数）"><InputNumber min={1} style={{ width: '100%' }} /></Form.Item>
          <Form.Item name="severity" label="告警级别"><Select options={['critical', 'high', 'medium', 'low'].map((s) => ({ value: s, label: s }))} /></Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked"><Switch /></Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
