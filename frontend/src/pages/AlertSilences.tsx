// 告警屏蔽（P2-R11，Alertmanager 风格）：匹配条件命中时只拦通知、不丢数据
import { useEffect, useState } from 'react'
import { Card, Table, Button, Space, Modal, Form, Input, Select, Tag, message, Popconfirm } from 'antd'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { getAlertSilences, createAlertSilence, deleteAlertSilence } from '../services/api'
import type { AlertSilence, SilenceMatcher } from '../services/api'

const KEYS = [
  { value: 'cluster_id', label: '集群 ID' },
  { value: 'namespace', label: '命名空间' },
  { value: 'resource_type', label: '资源类型' },
  { value: 'resource_name', label: '资源名' },
  { value: 'fault_type', label: '故障类型' },
  { value: 'severity', label: '级别' },
]
const OPS = ['=', '!=', '=~', '!~']

function isoLater(hours: number) {
  return new Date(Date.now() + hours * 3600_000).toISOString().slice(0, 19)
}

export default function AlertSilences() {
  const [rows, setRows] = useState<AlertSilence[]>([])
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()

  const load = () => {
    setLoading(true)
    getAlertSilences().then((d) => setRows(d || [])).catch(() => {}).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])

  const submit = async () => {
    const v = await form.validateFields()
    const matchers = (v.matchers || []).filter((m: SilenceMatcher) => m && m.key && m.value !== '')
    await createAlertSilence({ name: v.name, matchers, ends_at: v.ends_at, comment: v.comment })
    message.success('已创建')
    setOpen(false)
    load()
  }

  const isActive = (r: AlertSilence) => {
    const now = Date.now()
    return new Date(r.starts_at).getTime() <= now && new Date(r.ends_at).getTime() >= now
  }

  return (
    <div style={{ padding: 16 }}>
      <Card size="small" title="告警屏蔽"
        extra={<Space>
          <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => { form.resetFields(); form.setFieldsValue({ ends_at: isoLater(2), matchers: [{}] }); setOpen(true) }}>新增屏蔽</Button>
        </Space>}>
        <Table rowKey="id" size="small" loading={loading} dataSource={rows} pagination={{ pageSize: 20 }} scroll={{ x: 900 }}
          columns={[
            { title: '名称', dataIndex: 'name', width: 140, render: (v) => v || '-' },
            { title: '匹配条件', dataIndex: 'matchers', render: (m: SilenceMatcher[]) => (m || []).map((x) => <Tag key={x.key} color="blue">{x.key} {x.op} {x.value}</Tag>) },
            { title: '开始', dataIndex: 'starts_at', width: 150, render: (v) => String(v).slice(0, 19) },
            { title: '结束', dataIndex: 'ends_at', width: 150, render: (v) => String(v).slice(0, 19) },
            { title: '状态', width: 90, render: (_, r) => isActive(r) ? <Tag color="green">生效中</Tag> : <Tag>已过期</Tag> },
            { title: '备注', dataIndex: 'comment', render: (v) => v || '-' },
            {
              title: '操作', width: 90, render: (_, r) => (
                <Popconfirm title="删除该屏蔽？" onConfirm={async () => { await deleteAlertSilence(r.id); message.success('已删除'); load() }}>
                  <a style={{ color: '#cf1322' }}>删除</a>
                </Popconfirm>
              )
            },
          ]} />
      </Card>

      <Modal open={open} title="新增告警屏蔽" onOk={submit} onCancel={() => setOpen(false)} width={640} destroyOnClose>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称（可选）"><Input placeholder="如：压测期间屏蔽 xxx 告警" /></Form.Item>
          <Form.Item label="匹配条件（全部命中才屏蔽）">
            <Form.List name="matchers">
              {(fields, { add, remove }) => (
                <>
                  {fields.map(({ key, name }) => (
                    <Space key={key} align="baseline" style={{ marginBottom: 6 }}>
                      <Form.Item name={[name, 'key']} rules={[{ required: true, message: '选字段' }]}><Select placeholder="字段" style={{ width: 130 }} options={KEYS} /></Form.Item>
                      <Form.Item name={[name, 'op']} initialValue="="><Select style={{ width: 70 }} options={OPS.map((o) => ({ value: o, label: o }))} /></Form.Item>
                      <Form.Item name={[name, 'value']} rules={[{ required: true, message: '填值' }]}><Input placeholder="值（=~ 用正则）" style={{ width: 200 }} /></Form.Item>
                      <Button type="link" danger onClick={() => remove(name)}>删</Button>
                    </Space>
                  ))}
                  <Button size="small" type="dashed" onClick={() => add({ op: '=' })} block>+ 添加条件</Button>
                </>
              )}
            </Form.List>
          </Form.Item>
          <Form.Item name="ends_at" label="结束时间（ISO，本地时间）" rules={[{ required: true }]}><Input placeholder="2026-10-12T23:00:00" /></Form.Item>
          <Form.Item name="comment" label="备注"><Input.TextArea rows={2} /></Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
