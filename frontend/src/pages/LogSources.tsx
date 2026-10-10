// 日志源管理：自定义 ES 多实例（列表 + 新增/编辑弹窗 + 测试连通性 + 删除）
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card, Table, Button, Modal, Form, Input, Switch, Space, Tag, Popconfirm, message, Drawer } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { getLogSources, createLogSource, updateLogSource, deleteLogSource, testLogSource, testLogSourceById } from '../services/api'
import type { LogSource } from '../types'
import LogAlertRulesPanel from '../components/LogAlertRulesPanel'

export default function LogSources() {
  const navigate = useNavigate()
  const [data, setData] = useState<LogSource[]>([])
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<LogSource | null>(null)
  const [testing, setTesting] = useState(false)
  const [rowTesting, setRowTesting] = useState<number | null>(null)
  const [ruleDrawerFor, setRuleDrawerFor] = useState<LogSource | null>(null)
  const [form] = Form.useForm()

  const load = () => getLogSources().then((d) => setData(d || []))
  useEffect(() => { load() }, [])

  const save = async () => {
    const v = await form.validateFields()
    if (editing) await updateLogSource(editing.id, v)
    else await createLogSource(v)
    message.success('已保存'); setOpen(false); form.resetFields(); load()
  }

  const openEdit = (r: LogSource) => {
    setEditing(r)
    form.setFieldsValue({ ...r, password: '' })
    setOpen(true)
  }

  const handleTest = async () => {
    const v = await form.validateFields()
    setTesting(true)
    try {
      const r = await testLogSource(v)
      if (r.success) message.success(r.message)
      else message.warning(r.message)
    } catch {
      // 失败时拦截器已提示
    } finally {
      setTesting(false)
    }
  }

  const handleRowTest = async (id: number) => {
    setRowTesting(id)
    try {
      const r = await testLogSourceById(id)
      if (r.success) message.success(r.message)
      else message.warning(r.message)
    } catch {
      // 失败时拦截器已提示
    } finally {
      setRowTesting(null)
    }
  }

  return (
    <Card title="日志源管理">
      <div style={{ marginBottom: 12, color: '#666' }}>
        自定义 Elasticsearch 日志源，支持多实例并存、不强绑环境。日志告警规则可选指定日志源；
        规则不选日志源时使用<b>默认源</b>（标记「默认」的源优先，否则回退全局系统设置里的 ES 配置）。
        密码加密存储，仅回显「是否已配置」，留空表示不修改。
      </div>
      <div style={{ marginBottom: 12, color: '#8c8c8c', fontSize: 12 }}>
        配好源后，点该行「<b>告警规则</b>」即可在本地配关键词 / 阈值 / 通知渠道（套用通知源）——不同源可发不同群；也可到「策略配置 → 日志告警规则」总览。
        非 k8s 结构的 ES：按实际索引填「时间字段 / 搜索字段」（Pod、命名空间字段可留空）。
      </div>
      <Button type="primary" icon={<PlusOutlined />} style={{ marginBottom: 16 }}
        onClick={() => { setEditing(null); form.resetFields(); form.setFieldsValue({ index_pattern: 'k8s-*', is_default: false, enabled: true, time_field: '@timestamp', pod_field: 'kubernetes.pod_name', namespace_field: 'kubernetes.namespace_name', message_field: '' }); setOpen(true) }}>
        新增日志源
      </Button>
      <Table rowKey="id" dataSource={data} columns={[
        { title: '名称', dataIndex: 'name', width: 140 },
        { title: 'ES 地址', dataIndex: 'es_url', ellipsis: true },
        { title: '索引模式', dataIndex: 'index_pattern', width: 120 },
        { title: '账号', dataIndex: 'username', width: 100, render: (v) => v || '-' },
        { title: '密码', dataIndex: 'has_password', width: 80, render: (v) => (v ? <Tag color="green">已配置</Tag> : <Tag>未配置</Tag>) },
        { title: '默认', dataIndex: 'is_default', width: 70, render: (v) => (v ? <Tag color="blue">默认</Tag> : '-') },
        { title: '启用', dataIndex: 'enabled', width: 70, render: (v) => (v ? '是' : '否') },
        { title: '操作', width: 290, render: (_, r) => (
          <Space>
            <Button size="small" type="primary" ghost onClick={() => setRuleDrawerFor(r)}>告警规则</Button>
            <Button size="small" onClick={() => openEdit(r)}>编辑</Button>
            <Button size="small" loading={rowTesting === r.id} onClick={() => handleRowTest(r.id)}>测试</Button>
            <Popconfirm title="确认删除？" onConfirm={async () => { await deleteLogSource(r.id); load() }}>
              <Button size="small" danger>删除</Button>
            </Popconfirm>
          </Space>
        ) },
      ]} />
      <Modal title={editing ? '编辑日志源' : '新增日志源'} open={open} onOk={save} onCancel={() => setOpen(false)} width={560}>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input placeholder="如 生产 ES / 测试环境 ES" /></Form.Item>
          <Form.Item name="es_url" label="ES 地址" rules={[{ required: true }]}><Input placeholder="http://192.168.6.99:9200" /></Form.Item>
          <Form.Item name="index_pattern" label="索引 / 索引模式" rules={[{ required: true }]}><Input placeholder="k8s-*" /></Form.Item>
          <Form.Item name="username" label="账号（可选）"><Input autoComplete="off" /></Form.Item>
          <Form.Item name="password" label="密码（可选；留空不修改）"><Input.Password autoComplete="new-password" /></Form.Item>
          <Form.Item name="time_field" label="时间字段" tooltip="范围过滤/排序用。k8s/filebeat 日志一般是 @timestamp；应用日志可能是 finishTime 等"
            initialValue="@timestamp"><Input placeholder="@timestamp" /></Form.Item>
          <Form.Item name="pod_field" label="Pod 字段（留空=该 ES 无此维度）"
            tooltip="k8s 日志是 kubernetes.pod_name；非 k8s 结构的 ES（如应用操作日志）留空即可"><Input placeholder="kubernetes.pod_name" /></Form.Item>
          <Form.Item name="namespace_field" label="命名空间字段（留空=该 ES 无此维度）"
            tooltip="k8s 日志是 kubernetes.namespace_name；非 k8s 结构留空"><Input placeholder="kubernetes.namespace_name" /></Form.Item>
          <Form.Item name="message_field" label="正文字段（留空=自动取 message/log）"
            tooltip="日志正文所在字段；不同 ES 可能是 description/content 等"><Input placeholder="留空自动" /></Form.Item>
          <Form.Item name="search_field" label="关键词搜索字段（留空=全字段）"
            tooltip="留空=全字段 query_string（k8s 日志够用）；填字段名则按「包含匹配」查该字段，适配 keyword 类型的应用日志（如 description）"><Input placeholder="如 description（留空=全字段）" /></Form.Item>
          <Form.Item name="is_default" label="是否默认源" valuePropName="checked" tooltip="不指定日志源时优先使用；多个默认源取最早创建者">
            <Switch />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked"><Switch /></Form.Item>
          <Button loading={testing} onClick={handleTest}>测试连通性</Button>
        </Form>
      </Modal>
      <Drawer title={`日志告警规则：${ruleDrawerFor?.name || ''}`} open={!!ruleDrawerFor} onClose={() => setRuleDrawerFor(null)} width={840} destroyOnClose>
        {ruleDrawerFor && <LogAlertRulesPanel logSourceId={ruleDrawerFor.id} />}
      </Drawer>
    </Card>
  )
}
