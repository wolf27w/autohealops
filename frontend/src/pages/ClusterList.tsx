// 集群管理页：列表 + 添加（kubeconfig 上传落库）+ 健康检查 + 删除 + env_tag
import { useEffect, useState } from 'react'
import { Table, Button, Modal, Form, Input, Upload, Space, message, Select, Tag, Switch, Tooltip, Dropdown } from 'antd'
import { PlusOutlined, ReloadOutlined, MoreOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { getClusters, createCluster, updateCluster, deleteCluster, healthCheckCluster } from '../services/api'
import StatusTag from '../components/StatusTag'
import type { Cluster } from '../types'

const ENV_TAG_COLOR: Record<string, string> = { qa: 'green', release: 'orange', prod: 'red' }

type ClusterWithExtra = Cluster & {
  env_tag?: string
  description?: string
  rbac_status?: string
  version?: string
  inspect_cron?: string
}

const RBAC_MODE_LABEL: Record<string, string> = {
  off: '未接入', rbac: '云原生 RBAC', fallback: '平台权限+白名单',
}
const RBAC_MODE_COLOR: Record<string, string> = {
  off: 'default', rbac: 'green', fallback: 'orange',
}

export default function ClusterList() {
  const navigate = useNavigate()
  const [clusters, setClusters] = useState<ClusterWithExtra[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<ClusterWithExtra | null>(null)
  const [form] = Form.useForm()
  const [kubeconfig, setKubeconfig] = useState('')

  const load = () => {
    setLoading(true)
    getClusters().then((d) => setClusters(d)).finally(() => setLoading(false))
  }

  useEffect(load, [])

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({ env_tag: 'prod', inspect_enabled: true, inspect_cron: '' })
    setKubeconfig('')
    setModalOpen(true)
  }

  const openEdit = (c: ClusterWithExtra) => {
    setEditing(c)
    let mw: { enabled: boolean; start: string; end: string; days: string } = { enabled: false, start: '02:00', end: '04:00', days: '1,2,3,4,5' }
    try { mw = JSON.parse(c.maintenance_window || '{}') } catch { mw = { enabled: false, start: '02:00', end: '04:00', days: '1,2,3,4,5' } }
    form.setFieldsValue({ description: c.description, env_tag: c.env_tag, mw_enabled: mw.enabled, mw_start: mw.start, mw_end: mw.end, mw_days: mw.days, inspect_enabled: c.inspect_enabled !== false, inspect_cron: c.inspect_cron || '' })
    setModalOpen(true)
  }

  const handleSubmit = async () => {
    const values = await form.validateFields()
    if (editing) {
      const mw = {
        enabled: values.mw_enabled ?? false,
        start: values.mw_start || '02:00',
        end: values.mw_end || '04:00',
        days: values.mw_days || '1,2,3,4,5',
      }
      await updateCluster(editing.id, { description: values.description, env_tag: values.env_tag, maintenance_window: mw, inspect_enabled: values.inspect_enabled ?? true, inspect_cron: values.inspect_cron || '' })
      message.success('已更新')
    } else {
      await createCluster({ ...values, kubeconfig })
      message.success('集群已添加')
    }
    setModalOpen(false)
    form.resetFields()
    setKubeconfig('')
    load()
  }

  const handleHealthCheck = async (id: number) => {
    await healthCheckCluster(id)
    message.success('健康检查完成')
    load()
  }

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>添加集群</Button>
        <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
      </Space>
      <Table
        rowKey="id"
        loading={loading}
        dataSource={clusters}
        columns={[
          { title: '名称', dataIndex: 'name', render: (name, record) => (
            <a onClick={() => navigate(`/clusters/${record.id}`)}>{name}</a>
          ) },
          { title: '描述', dataIndex: 'description' },
          { title: '环境', dataIndex: 'env_tag', render: (t) => <Tag color={ENV_TAG_COLOR[t] || 'default'}>{t}</Tag> },
          { title: 'RBAC', dataIndex: 'rbac_mode', render: (m, record) => (
            <span title={record.rbac_status}>
              <Tag color={RBAC_MODE_COLOR[m] || 'default'}>{RBAC_MODE_LABEL[m] || m || '未接入'}</Tag>
            </span>
          ) },
          { title: '状态', dataIndex: 'status', render: (s, record: ClusterWithExtra) => (
            <Tooltip title={record.last_check_at ? `上次检查：${String(record.last_check_at).slice(0, 19)}${record.last_check_error ? `\n错误：${record.last_check_error}` : ''}` : '尚未检查'}>
              <span><StatusTag status={s} /></span>
            </Tooltip>
          ) },
          { title: '版本', dataIndex: 'version' },
          { title: '创建时间', dataIndex: 'created_at' },
          {
            title: '操作', width: 70, render: (_, record) => (
              <Dropdown
                trigger={['click']}
                menu={{
                  items: [
                    { key: 'health', label: '健康检查' },
                    { key: 'edit', label: '编辑' },
                    { type: 'divider' as const },
                    { key: 'delete', label: '删除', danger: true },
                  ],
                  onClick: ({ key }) => {
                    if (key === 'health') handleHealthCheck(record.id)
                    else if (key === 'edit') openEdit(record)
                    else if (key === 'delete') Modal.confirm({
                      title: `确认删除集群「${record.name}」？`,
                      okButtonProps: { danger: true },
                      onOk: async () => { await deleteCluster(record.id); load() },
                    })
                  },
                }}
              >
                <Button size="small" type="link"><MoreOutlined /></Button>
              </Dropdown>
            ),
          },
        ]}
      />
      <Modal title={editing ? '编辑集群' : '添加集群'} open={modalOpen} onOk={handleSubmit} onCancel={() => setModalOpen(false)}>
        <Form form={form} layout="vertical">
          {!editing && (
            <Form.Item name="name" label="集群名称" rules={[{ required: true }]}><Input /></Form.Item>
          )}
          <Form.Item name="description" label="描述"><Input /></Form.Item>
          <Form.Item name="env_tag" label="环境标签" rules={[{ required: true }]}>
            <Select options={[
              { value: 'qa', label: 'qa（测试）' },
              { value: 'release', label: 'release（预发）' },
              { value: 'prod', label: 'prod（生产）' },
            ]} />
          </Form.Item>
          {editing && (
            <>
              <Form.Item name="inspect_enabled" label="巡检开关" valuePropName="checked" tooltip="关闭后该集群不参与巡检/报告生成"><Switch /></Form.Item>
              <Form.Item name="inspect_cron" label="定时巡检 Cron" tooltip="分 时 日 月 周；如 0 * * * * = 每小时；留空=不定时，仅手动点「巡检」">
                <Input placeholder="留空=不定时，如 0 * * * *" />
              </Form.Item>
            </>
          )}
          {editing && (
            <>
              <Form.Item name="mw_enabled" label="维护窗口（静默告警）" valuePropName="checked"><Switch /></Form.Item>
              <Space>
                <Form.Item name="mw_start" label="开始"><Input placeholder="02:00" style={{ width: 100 }} /></Form.Item>
                <Form.Item name="mw_end" label="结束"><Input placeholder="04:00" style={{ width: 100 }} /></Form.Item>
                <Form.Item name="mw_days" label="星期"><Input placeholder="1,2,3,4,5" style={{ width: 120 }} /></Form.Item>
              </Space>
              <div style={{ fontSize: 12, color: '#999', marginTop: -8 }}>维护窗口内该集群不告警（1-7 表示周一到周日；跨天如 23:00-02:00 支持）。与「通知配置」里的全平台静默窗口任一命中即静默</div>
            </>
          )}
          {!editing && (
            <Form.Item label="kubeconfig（上传文件）" required>
              <Upload
                beforeUpload={(file) => {
                  const reader = new FileReader()
                  reader.onload = () => setKubeconfig(reader.result as string)
                  reader.readAsText(file)
                  return false
                }}
                maxCount={1}
              >
                <Button icon={<PlusOutlined />}>选择 kubeconfig 文件</Button>
              </Upload>
            </Form.Item>
          )}
        </Form>
      </Modal>
    </div>
  )
}
