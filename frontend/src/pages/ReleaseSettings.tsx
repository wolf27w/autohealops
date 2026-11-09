// 发布配置（R19）：Jenkins 连接（全局）+ 服务↔Job 映射（按 cluster/namespace）
import { useEffect, useState } from 'react'
import { Card, Table, Button, Modal, Form, Input, Select, Space, Switch, Tag, message, Popconfirm } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import {
  getJenkinsInstances, createJenkinsInstance, updateJenkinsInstance, deleteJenkinsInstance,
  getReleaseMappings, upsertReleaseMapping, deleteReleaseMapping,
  getClusters, getClusterNamespaces,
} from '../services/api'
import type { JenkinsInstance, ReleaseMapping } from '../services/api'
import type { Cluster } from '../types'

const AUTH_TYPE: Record<string, string> = {
  api_token: '用户名 + API Token', credential_id: '凭据 ID', service_account: '服务账号',
}

export default function ReleaseSettings() {
  const [insts, setInsts] = useState<JenkinsInstance[]>([])
  const [clusters, setClusters] = useState<Cluster[]>([])
  const [namespaces, setNamespaces] = useState<string[]>([])
  const [clusterId, setClusterId] = useState<number>()
  const [ns, setNs] = useState<string>()
  const [mappings, setMappings] = useState<ReleaseMapping[]>([])

  const [instModal, setInstModal] = useState(false)
  const [instEditing, setInstEditing] = useState<JenkinsInstance | null>(null)
  const [instForm] = Form.useForm()

  const [mapModal, setMapModal] = useState(false)
  const [mapEditing, setMapEditing] = useState<ReleaseMapping | null>(null)
  const [mapForm] = Form.useForm()

  const loadInsts = () => getJenkinsInstances().then((d) => setInsts(d || []))
  const loadClusters = () => getClusters().then((d) => setClusters((d || []) as Cluster[]))
  const loadNs = (cid: number) => getClusterNamespaces(cid).then((d) => setNamespaces(d || []))
  const loadMappings = (cid?: number, n?: string) => {
    if (!cid || !n) { setMappings([]); return }
    getReleaseMappings({ cluster_id: cid, namespace: n }).then((d) => setMappings(d || []))
  }

  useEffect(() => { loadInsts(); loadClusters() }, [])

  const openInst = (j?: JenkinsInstance) => {
    setInstEditing(j || null); setInstModal(true)
    if (j) instForm.setFieldsValue({ ...j, api_token: '' })
    else instForm.resetFields()
  }
  const saveInst = async () => {
    const v = await instForm.validateFields()
    const payload = { ...v }
    if (!payload.api_token) delete payload.api_token
    if (instEditing) { await updateJenkinsInstance(instEditing.id, payload); message.success('已更新') }
    else { await createJenkinsInstance(payload); message.success('已添加') }
    setInstModal(false); loadInsts()
  }

  const openMap = (m?: ReleaseMapping) => {
    setMapEditing(m || null); setMapModal(true)
    if (m) mapForm.setFieldsValue(m)
    else mapForm.resetFields()
  }
  const saveMap = async () => {
    const v = await mapForm.validateFields()
    await upsertReleaseMapping({ cluster_id: clusterId, namespace: ns, ...v, enabled: v.enabled !== false })
    message.success('已保存'); setMapModal(false); loadMappings(clusterId, ns)
  }

  const instCols = [
    { title: '名称', dataIndex: 'name' },
    { title: '地址', dataIndex: 'url', ellipsis: true },
    { title: '认证', dataIndex: 'auth_type', render: (v: string) => AUTH_TYPE[v] || v },
    { title: '用户名', dataIndex: 'username', render: (v: string) => v || '-' },
    { title: '忽略证书', dataIndex: 'ignore_ssl', width: 90, render: (v: boolean) => v ? <Tag color="orange">是</Tag> : <Tag>否</Tag> },
    { title: '操作', width: 120, render: (_: unknown, r: JenkinsInstance) => (
      <Space size="small">
        <Button size="small" type="link" onClick={() => openInst(r)}>编辑</Button>
        <Popconfirm title="删除该实例？" onConfirm={async () => { await deleteJenkinsInstance(r.id); message.success('已删除'); loadInsts() }}><a style={{ color: '#cf1322' }}>删除</a></Popconfirm>
      </Space>
    ) },
  ]

  const mapCols = [
    { title: '服务名', dataIndex: 'service_name' },
    { title: 'Jenkins 实例', dataIndex: 'jenkins_instance_id', render: (v: number) => insts.find((i) => i.id === v)?.name || v },
    { title: 'Job 名', dataIndex: 'job_name', ellipsis: true },
    { title: '服务名参数', dataIndex: 'service_param', width: 110 },
    { title: '分支参数', dataIndex: 'branch_param', width: 110 },
    { title: '启用', dataIndex: 'enabled', width: 70, render: (v: boolean) => v ? <Tag color="green">是</Tag> : <Tag>否</Tag> },
    { title: '操作', width: 120, render: (_: unknown, r: ReleaseMapping) => (
      <Space size="small">
        <Button size="small" type="link" onClick={() => openMap(r)}>编辑</Button>
        <Popconfirm title="删除该映射？" onConfirm={async () => { await deleteReleaseMapping(r.id); message.success('已删除'); loadMappings(clusterId, ns) }}><a style={{ color: '#cf1322' }}>删除</a></Popconfirm>
      </Space>
    ) },
  ]

  return (
    <div>
      <Card size="small" title="Jenkins 连接（全局）" style={{ marginBottom: 16 }}
        extra={<Button type="primary" size="small" icon={<PlusOutlined />} onClick={() => openInst()}>添加实例</Button>}>
        <Table rowKey="id" size="small" dataSource={insts} columns={instCols} pagination={false} />
      </Card>

      <Card size="small" title="服务 ↔ Job 映射（按 namespace 配）">
        <Space style={{ marginBottom: 12 }} wrap>
          <span>集群</span>
          <Select placeholder="选择集群" style={{ width: 220 }} value={clusterId}
            onChange={(v) => { setClusterId(v); setNs(undefined); setMappings([]); loadNs(v) }}
            options={clusters.map((c) => ({ value: c.id, label: c.name }))} />
          <span>namespace</span>
          <Select placeholder="选择 namespace" style={{ width: 200 }} value={ns}
            onChange={(v) => { setNs(v); loadMappings(clusterId, v) }}
            options={namespaces.map((n) => ({ value: n, label: n }))} />
          <Button type="primary" size="small" icon={<PlusOutlined />} disabled={!clusterId || !ns} onClick={() => openMap()}>添加映射</Button>
        </Space>
        {!clusterId || !ns
          ? <div style={{ color: '#999', padding: 8 }}>先选集群和 namespace</div>
          : <Table rowKey="id" size="small" dataSource={mappings} columns={mapCols} pagination={false} />}
      </Card>

      <Modal title={instEditing ? '编辑 Jenkins 实例' : '添加 Jenkins 实例'} open={instModal} onOk={saveInst} onCancel={() => setInstModal(false)}>
        <Form form={instForm} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="url" label="地址" rules={[{ required: true }]} extra="如 http://jenkins:8080（不带尾斜杠）"><Input /></Form.Item>
          <Form.Item name="auth_type" label="认证方式" initialValue="api_token">
            <Select options={Object.entries(AUTH_TYPE).map(([v, l]) => ({ value: v, label: l }))} />
          </Form.Item>
          <Form.Item name="username" label="用户名"><Input /></Form.Item>
          <Form.Item name="api_token" label={instEditing ? 'API Token（留空=不修改）' : 'API Token'}>
            <Input.Password />
          </Form.Item>
          <Form.Item name="credential_id" label="凭据 ID（credential_id 模式用）"><Input /></Form.Item>
          <Form.Item name="ignore_ssl" label="忽略证书" valuePropName="checked" initialValue={false}><Switch /></Form.Item>
        </Form>
      </Modal>

      <Modal title={mapEditing ? '编辑映射' : '添加映射'} open={mapModal} onOk={saveMap} onCancel={() => setMapModal(false)}>
        <Form form={mapForm} layout="vertical">
          <Form.Item name="service_name" label="服务名" rules={[{ required: true }]} extra="与 Deployment 名一致；支持一次填多个（逗号/顿号/换行分隔），多个服务可指向同一个 Job">
            <Input.TextArea rows={3} placeholder="如 svc-order，或一行一个：
svc-order
svc-user
svc-pay" />
          </Form.Item>
          <Form.Item name="jenkins_instance_id" label="Jenkins 实例" rules={[{ required: true }]}>
            <Select options={insts.map((i) => ({ value: i.id, label: `${i.name}（${i.url}）` }))} />
          </Form.Item>
          <Form.Item name="job_name" label="Job 名" rules={[{ required: true }]}><Input placeholder="如 prod/order-backend" /></Form.Item>
          <Space>
            <Form.Item name="service_param" label="服务名参数" initialValue="SERVICE_NAME"><Input /></Form.Item>
            <Form.Item name="branch_param" label="分支参数" initialValue="BRANCH_TAG"><Input /></Form.Item>
          </Space>
          <Form.Item name="enabled" label="启用" valuePropName="checked" initialValue={true}><Switch /></Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
