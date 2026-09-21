// 通知配置页：渠道管理 + 传输模式/反馈方式/长连接参数 + 测试
import { useEffect, useState } from 'react'
import { Card, Button, Modal, Form, Input, Select, Switch, Space, Tag, Table, message, Alert as AntAlert, Divider } from 'antd'
import { PlusOutlined, SendOutlined, SaveOutlined, DeleteOutlined } from '@ant-design/icons'
import {
  getNotificationConfigs, createNotificationConfig, updateNotificationConfig,
  deleteNotificationConfig, getNotificationLogs, testNotificationConfig,
  getNotifyPolicy, updateNotifyPolicy,
} from '../services/api'
import type { NotificationConfig, NotifyPolicy, SilenceWindow } from '../types'

interface NotifLog {
  id: number
  config_id?: number
  status?: string
  sent_at?: string
}

const CHANNEL_OPTIONS = [
  { value: 'dingtalk', label: '钉钉' },
  { value: 'wechat', label: '企业微信' },
  { value: 'feishu', label: '飞书' },
]

const TRANSPORT_OPTIONS = [
  { value: 'webhook', label: 'Webhook 回调（有公网）' },
  { value: 'long_conn', label: '长连接（无公网，待 SDK）' },
]

// 发送方式：群机器人 webhook / 应用机器人（飞书自建应用 App ID+Secret，经 IM API 发出）
const SEND_MODE_OPTIONS = [
  { value: 'webhook', label: '群机器人 webhook' },
  { value: 'app', label: '应用机器人（飞书自建应用）' },
]

const FEEDBACK_OPTIONS = [
  { value: 'text', label: '文本回复' },
  { value: 'button', label: '卡片按钮' },
  { value: 'both', label: '文本 + 按钮' },
]

const SCENE_OPTIONS = [
  { value: 'alert', label: '告警机器人' },
  { value: 'approval_duty', label: '审批-值班机器人' },
  { value: 'approval_admin', label: '审批-管理员机器人' },
  { value: 'both', label: '通用（告警+审批都收）' },
]

const SCENE_COLOR: Record<string, string> = {
  alert: 'red', approval_duty: 'blue', approval_admin: 'purple', both: 'default',
}

// IM 通知模板预置（按渠道各一套，占位符：{project}/{cluster}/{namespace}/{resource}/{fault_type}/{severity}/{message}/{approval_id}/{plan_summary}/{risk_level}）
const IM_TEMPLATE_PRESET = {
  dingtalk: '【API-OPS 告警】\n项目: {project}\n集群: {cluster}\n资源: {resource}\n命名空间: {namespace}\n故障: {fault_type}\n级别: {severity}\n详情: {message}',
  wechat: '【API-OPS 告警】\n项目: {project}\n集群: {cluster}\n资源: {resource}\n命名空间: {namespace}\n故障: {fault_type}\n级别: {severity}\n详情: {message}',
  feishu: '【API-OPS 告警】\n项目: {project}\n集群: {cluster}\n资源: {resource}\n命名空间: {namespace}\n故障: {fault_type}\n级别: {severity}\n详情: {message}',
}

const IM_TEMPLATE_PLACEHOLDER_HINT = '占位符：{project} {cluster} {namespace} {resource} {fault_type} {severity} {message} {approval_id} {plan_summary} {risk_level}'

const SEVERITY_LEVELS = [
  { value: 'critical', label: 'Critical（严重）' },
  { value: 'high', label: 'High（高）' },
  { value: 'medium', label: 'Medium（中）' },
  { value: 'low', label: 'Low（低）' },
]

export default function NotificationConfig() {
  const [configs, setConfigs] = useState<NotificationConfig[]>([])
  const [logs, setLogs] = useState<NotifLog[]>([])
  const [policy, setPolicy] = useState<NotifyPolicy>({ enabled: true, severity_routes: {}, silence_windows: [] })
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<NotificationConfig | null>(null)
  const [form] = Form.useForm()
  const channelType = Form.useWatch('channel_type', form)
  const sendMode = Form.useWatch('send_mode', form)
  const useAppMode = channelType === 'feishu' && sendMode === 'app'

  const load = () => {
    getNotificationConfigs().then((d) => setConfigs(d))
    getNotificationLogs().then((d) => setLogs(d as NotifLog[]))
    getNotifyPolicy().then((p) => setPolicy(p))
  }
  useEffect(load, [])

  // ============ 统一告警通知策略（N8） ============
  const setSeverityChannels = (sev: string, chans: string[]) => {
    setPolicy((p) => ({ ...p, severity_routes: { ...p.severity_routes, [sev]: chans } }))
  }
  const updateWindow = (idx: number, patch: Partial<SilenceWindow>) => {
    setPolicy((p) => ({
      ...p,
      silence_windows: p.silence_windows.map((w, i) => (i === idx ? { ...w, ...patch } : w)),
    }))
  }
  const addWindow = () => setPolicy((p) => ({
    ...p,
    silence_windows: [...p.silence_windows, { enabled: true, start: '22:00', end: '08:00', days: '' }],
  }))
  const removeWindow = (idx: number) => setPolicy((p) => ({
    ...p,
    silence_windows: p.silence_windows.filter((_, i) => i !== idx),
  }))
  const savePolicy = async () => {
    await updateNotifyPolicy(policy)
    message.success('统一告警通知策略已保存')
    load()
  }

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({ transport_mode: 'webhook', feedback_mode: 'text', scene: 'both', namespaces: [], send_mode: 'webhook', receive_id_type: 'chat_id' })
    setModalOpen(true)
  }

  const openEdit = (c: NotificationConfig) => {
    setEditing(c)
    form.resetFields()
    let imTpl: Record<string, string> = {}
    try { imTpl = JSON.parse(c.im_template || '{}') } catch { imTpl = {} }
    form.setFieldsValue({
      name: c.name, channel_type: c.channel_type, webhook_url: c.webhook_url,
      secret: '', transport_mode: c.transport_mode, feedback_mode: c.feedback_mode,
      scene: c.scene || 'both', namespaces: c.namespaces || [],
      stream_config: c.stream_config || '', is_active: c.is_active,
      send_mode: c.send_mode || 'webhook', app_id: c.app_id || '', app_secret: '',
      receive_id: c.receive_id || '', receive_id_type: c.receive_id_type || 'chat_id',
      im_dingtalk: imTpl.dingtalk || '', im_wechat: imTpl.wechat || '', im_feishu: imTpl.feishu || '',
    })
    setModalOpen(true)
  }

  const handleSubmit = async () => {
    const values = await form.validateFields()
    const im_template = JSON.stringify({
      dingtalk: values.im_dingtalk || '',
      wechat: values.im_wechat || '',
      feishu: values.im_feishu || '',
    })
    const payload: Record<string, unknown> = {
      ...values,
      namespaces: Array.isArray(values.namespaces) ? values.namespaces : [],
      stream_config: values.stream_config || '',
      is_active: values.is_active ?? true,
      send_mode: values.channel_type === 'feishu' ? (values.send_mode || 'webhook') : 'webhook',
      im_template,
    }
    delete payload.im_dingtalk
    delete payload.im_wechat
    delete payload.im_feishu
    // 应用机器人模式：清掉 webhook，避免误填；非应用模式清掉应用字段
    if (payload.send_mode === 'app') {
      payload.webhook_url = ''
      payload.receive_id_type = 'chat_id'
      if (!payload.app_secret) delete payload.app_secret
    } else {
      payload.app_id = ''
      payload.app_secret = ''
      payload.receive_id = ''
    }
    if (editing) {
      // 编辑时不传 secret（留空=不改），保留原值
      if (!payload.secret) delete payload.secret
      await updateNotificationConfig(editing.id, payload)
      message.success('已更新')
    } else {
      await createNotificationConfig(payload)
      message.success('已添加')
    }
    setModalOpen(false)
    load()
  }

  const handleTest = async (id: number) => {
    try {
      await testNotificationConfig(id)
      message.success('测试消息已发送')
    } catch {
      /* 拦截器已提示错误 */
    }
  }

  return (
    <div>
      <Card title="统一告警通知策略"
        extra={<Button type="primary" icon={<SaveOutlined />} onClick={savePolicy}>保存策略</Button>}
        style={{ marginBottom: 16 }}>
        <AntAlert type="info" showIcon style={{ marginBottom: 12 }}
          message="适用范围：全部告警（集群 / 容器 / 日志 / 数据库 / Pod 重启），全平台共用一份策略" />
        <Space style={{ marginBottom: 8 }}>
          <Switch checked={policy.enabled} checkedChildren="策略启用" unCheckedChildren="策略停用"
            onChange={(v) => setPolicy((p) => ({ ...p, enabled: v }))} />
        </Space>
        <Divider orientation="left">分级渠道（严重级别 → 允许发送的渠道，留空=该级别不通知）</Divider>
        {SEVERITY_LEVELS.map((s) => (
          <div key={s.value} style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
            <span style={{ width: 140 }}>{s.label}</span>
            <Select mode="multiple" style={{ flex: 1 }} value={policy.severity_routes[s.value] || []}
              options={CHANNEL_OPTIONS} placeholder="留空 = 不通知"
              onChange={(v) => setSeverityChannels(s.value, v)} />
          </div>
        ))}
        <Divider orientation="left">静默 / 维护窗口（命中则不发任何告警通知；时间 HH:mm，星期 1-7 逗号分隔，空=每天，支持跨天）</Divider>
        <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 8 }}>
          这里配的是<strong>全平台静默窗口</strong>；各集群还有自己的「维护窗口」（集群管理 → 编辑集群）。
          两者<strong>任一命中即静默</strong>（集群窗口只影响该集群告警）。
        </div>
        {policy.silence_windows.map((w, idx) => (
          <Space key={idx} style={{ marginBottom: 8 }} wrap>
            <Switch size="small" checked={w.enabled} onChange={(v) => updateWindow(idx, { enabled: v })} />
            <Input style={{ width: 96 }} value={w.start} placeholder="开始 22:00"
              onChange={(e) => updateWindow(idx, { start: e.target.value })} />
            <Input style={{ width: 96 }} value={w.end} placeholder="结束 08:00"
              onChange={(e) => updateWindow(idx, { end: e.target.value })} />
            <Input style={{ width: 200 }} value={w.days} placeholder="星期 1,2,3,4,5（空=每天）"
              onChange={(e) => updateWindow(idx, { days: e.target.value })} />
            <Button size="small" danger icon={<DeleteOutlined />} onClick={() => removeWindow(idx)}>删除</Button>
          </Space>
        ))}
        <Button size="small" icon={<PlusOutlined />} onClick={addWindow}>添加静默窗口</Button>
      </Card>

      <Space style={{ marginBottom: 16 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>添加通知渠道</Button>
      </Space>
      <Space wrap>
        {configs.map((c) => (
          <Card key={c.id} size="small" style={{ width: 360 }}>
            <div style={{ fontWeight: 'bold' }}>
              {c.name} <Tag>{CHANNEL_OPTIONS.find((o) => o.value === c.channel_type)?.label || c.channel_type}</Tag>
            </div>
            <div style={{ fontSize: 12, color: '#999', marginBottom: 8 }}>
              {c.channel_type === 'feishu' && c.send_mode === 'app'
                ? `应用机器人 · ${c.app_id || ''} → ${c.receive_id || ''}（${c.receive_id_type || 'chat_id'}）`
                : c.webhook_url}
            </div>
            <div style={{ fontSize: 12, marginBottom: 8 }}>
              用途：<Tag color={SCENE_COLOR[c.scene] || 'default'}>{SCENE_OPTIONS.find((o) => o.value === c.scene)?.label || c.scene}</Tag>
              {c.scene === 'alert' && c.namespaces?.length > 0 && (
                <span style={{ marginLeft: 8 }}>namespace：{((c.namespaces as unknown as string[]) || []).join(', ')}</span>
              )}
            </div>
            <div style={{ fontSize: 12, marginBottom: 8 }}>
              传输：<Tag color={c.transport_mode === 'long_conn' ? 'orange' : 'blue'}>{c.transport_mode}</Tag>
              反馈：<Tag color={c.feedback_mode === 'button' ? 'purple' : 'default'}>{c.feedback_mode}</Tag>
            </div>
            <Space>
              <Switch size="small" checked={c.is_active} disabled />
              <Button size="small" icon={<SendOutlined />} onClick={() => handleTest(c.id)}>测试</Button>
              <Button size="small" onClick={() => openEdit(c)}>编辑</Button>
              <Button size="small" danger onClick={async () => { await deleteNotificationConfig(c.id); load() }}>删除</Button>
            </Space>
          </Card>
        ))}
      </Space>

      <Card title="发送日志" style={{ marginTop: 16 }}>
        <Table rowKey="id" dataSource={logs} pagination={false}
          columns={[
            { title: '渠道ID', dataIndex: 'config_id' },
            { title: '状态', dataIndex: 'status', render: (s) => <Tag color={s === 'success' ? 'green' : 'red'}>{s}</Tag> },
            { title: '发送时间', dataIndex: 'sent_at' },
          ]} />
      </Card>

      <Modal title={editing ? '编辑通知渠道' : '添加通知渠道'} open={modalOpen} onOk={handleSubmit} onCancel={() => setModalOpen(false)}>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="channel_type" label="渠道" rules={[{ required: true }]}>
            <Select options={CHANNEL_OPTIONS} disabled={!!editing} />
          </Form.Item>
          {channelType === 'feishu' && (
            <Form.Item name="send_mode" label="发送方式" rules={[{ required: true }]}
              tooltip="群机器人 webhook：单向通知；应用机器人：填飞书自建应用 App ID/Secret，消息由该机器人经 IM API 发出">
              <Select options={SEND_MODE_OPTIONS} />
            </Form.Item>
          )}
          {useAppMode ? (
            <>
              <Form.Item name="app_id" label="App ID" rules={[{ required: true }]}>
                <Input placeholder="cli_xxxxxxxx（飞书开放平台 → 自建应用）" />
              </Form.Item>
              <Form.Item name="app_secret" label="App Secret（编辑留空=不修改）">
                <Input.Password placeholder="应用凭证 App Secret" />
              </Form.Item>
              <Form.Item name="receive_id" label="通知群 chat_id" rules={[{ required: true }]}
                tooltip="应用机器人是一个独立身份，可给多个群/人发消息，所以要指定发到哪个群：填群 chat_id（oc_xxx）。群机器人 webhook 则因 URL 已绑定群而无需此项">
                <Input placeholder="oc_xxxxx（把机器人拉进群后可在群设置/链接里看到）" />
              </Form.Item>
            </>
          ) : (
            <Form.Item name="webhook_url" label="Webhook URL（飞书填群机器人 webhook）" rules={[{ required: true }]}><Input /></Form.Item>
          )}
          <Form.Item name="secret" label="加签密钥（编辑留空=不修改）"><Input.Password /></Form.Item>
          <Form.Item name="scene" label="用途" rules={[{ required: true }]}>
            <Select options={SCENE_OPTIONS} />
          </Form.Item>
          <Form.Item name="namespaces" label="告警 namespace 匹配（仅告警机器人，留空=全部）"
            tooltip="不同 namespace 的告警发到不同群：填该群负责的 namespace，多个用回车分隔；留空表示接收所有 namespace 的告警（默认兜底群）">
            <Select mode="tags" placeholder="如 qa-eft、qa-bgs，回车添加；留空=全部" tokenSeparators={[',', '，']} />
          </Form.Item>
          <Form.Item name="transport_mode" label="传输模式" rules={[{ required: true }]}>
            <Select options={TRANSPORT_OPTIONS} />
          </Form.Item>
          <Form.Item name="feedback_mode" label="反馈方式" rules={[{ required: true }]}>
            <Select options={FEEDBACK_OPTIONS} />
          </Form.Item>
          <Form.Item name="stream_config" label="长连接参数（JSON，可选）">
            <Input.TextArea rows={3} placeholder='{"appKey":"...","appSecret":"..."}' />
          </Form.Item>
          <Form.Item label="IM 通知模板（按渠道各一套，留空用内置默认）">
            <div style={{ fontSize: 12, color: '#999', marginBottom: 6 }}>{IM_TEMPLATE_PLACEHOLDER_HINT}</div>
            <Form.Item name="im_dingtalk" noStyle>
              <Input.TextArea rows={3} placeholder={`钉钉模板（点右侧按钮套预置）`} style={{ marginBottom: 8 }} />
            </Form.Item>
            <Form.Item name="im_wechat" noStyle>
              <Input.TextArea rows={3} placeholder="企微模板" style={{ marginBottom: 8 }} />
            </Form.Item>
            <Form.Item name="im_feishu" noStyle>
              <Input.TextArea rows={3} placeholder="飞书模板" />
            </Form.Item>
            <div style={{ marginTop: 8 }}>
              {['dingtalk', 'wechat', 'feishu'].map((ch) => (
                <Button key={ch} size="small" style={{ marginRight: 8 }} onClick={() => {
                  form.setFieldsValue({ [`im_${ch}`]: IM_TEMPLATE_PRESET[ch as keyof typeof IM_TEMPLATE_PRESET] })
                }}>
                  套 {ch} 预置模板
                </Button>
              ))}
            </div>
          </Form.Item>
          {editing && (
            <Form.Item name="is_active" label="启用" valuePropName="checked"><Switch /></Form.Item>
          )}
        </Form>
      </Modal>
    </div>
  )
}
