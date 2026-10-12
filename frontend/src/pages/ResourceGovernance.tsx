// 资源治理·管控台（P0-R1）：维护管控表（副本数/资源配额/JVM 参数），供准入控制改写 Deployment
import { useCallback, useEffect, useState } from 'react'
import { Card, Table, Button, Space, Select, Input, Modal, Form, InputNumber, Switch, message, Popconfirm, Tag, Tooltip } from 'antd'
import { PlusOutlined, ReloadOutlined, FundOutlined } from '@ant-design/icons'
import {
  getClusters, getResourceGovernance, upsertResourceGovernance, deleteResourceGovernance, getResourceGovernancePeak,
} from '../services/api'
import type { ResourceGovernanceRule, PeakResult } from '../services/api'
import type { Cluster } from '../types'

export default function ResourceGovernance() {
  const [clusters, setClusters] = useState<Cluster[]>([])
  const [clusterId, setClusterId] = useState<number | undefined>(undefined)
  const [namespace, setNamespace] = useState('')
  const [rows, setRows] = useState<ResourceGovernanceRule[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editRow, setEditRow] = useState<ResourceGovernanceRule | null>(null)
  const [form] = Form.useForm()
  const [peakOpen, setPeakOpen] = useState(false)
  const [peakData, setPeakData] = useState<PeakResult[]>([])
  const [peakLoading, setPeakLoading] = useState(false)

  useEffect(() => {
    getClusters().then((d) => setClusters(d || [])).catch(() => {})
  }, [])

  const load = useCallback(() => {
    setLoading(true)
    getResourceGovernance({ cluster_id: clusterId, namespace: namespace || undefined })
      .then((d) => setRows(d || []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [clusterId, namespace])

  useEffect(() => { load() }, [load])

  const openAdd = () => {
    setEditRow(null)
    form.resetFields()
    form.setFieldsValue({ cluster_id: clusterId, namespace, workload_kind: 'deployment', replicas: -1, admission_enabled: true })
    setModalOpen(true)
  }
  const openEdit = (r: ResourceGovernanceRule) => {
    setEditRow(r)
    form.setFieldsValue(r)
    setModalOpen(true)
  }

  const submit = async () => {
    const v = await form.validateFields()
    await upsertResourceGovernance(v)
    message.success('已保存')
    setModalOpen(false)
    load()
  }

  const collectPeak = async () => {
    if (!clusterId) { message.warning('请先选择集群'); return }
    setPeakOpen(true); setPeakLoading(true); setPeakData([])
    getResourceGovernancePeak({ cluster_id: clusterId, namespace: namespace || undefined })
      .then((d) => setPeakData(d || []))
      .catch(() => {})
      .finally(() => setPeakLoading(false))
  }

  const clusterName = (id: number) => clusters.find((c) => c.id === id)?.name || `#${id}`

  return (
    <div style={{ padding: 16 }}>
      <Card size="small" title="资源治理 · 管控表"
        extra={
          <Space>
            <Select allowClear placeholder="全部集群" style={{ width: 180 }} value={clusterId}
              onChange={(v) => setClusterId(v)} options={clusters.map((c) => ({ value: c.id, label: c.name }))} />
            <Input placeholder="命名空间（空=全部）" style={{ width: 160 }} value={namespace}
              onChange={(e) => setNamespace(e.target.value)} onPressEnter={load} allowClear />
            <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
            <Button icon={<FundOutlined />} onClick={collectPeak}>采集高峰</Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={openAdd}>新增</Button>
          </Space>
        }>
        <Table rowKey="id" size="small" loading={loading} dataSource={rows} pagination={{ pageSize: 20 }} scroll={{ x: 1100 }}
          columns={[
            { title: '集群', dataIndex: 'cluster_id', width: 120, render: (v: number) => clusterName(v) },
            { title: '命名空间', dataIndex: 'namespace', width: 140 },
            { title: '工作负载', dataIndex: 'workload_name', width: 200, render: (v, r) => <span>{v}<Tag style={{ marginLeft: 6 }}>{r.workload_kind}</Tag></span> },
            { title: '容器', dataIndex: 'container', width: 120, render: (v) => v || '(第一个)' },
            { title: '副本', dataIndex: 'replicas', width: 70, render: (v) => (v >= 0 ? v : <span style={{ color: '#bbb' }}>不管控</span>) },
            { title: 'CPU(req/lim)', render: (_, r) => r.cpu_request || r.cpu_limit ? `${r.cpu_request || '-'} / ${r.cpu_limit || '-'}` : <span style={{ color: '#bbb' }}>-</span>, width: 140 },
            { title: '内存(req/lim)', render: (_, r) => r.memory_request || r.memory_limit ? `${r.memory_request || '-'} / ${r.memory_limit || '-'}` : <span style={{ color: '#bbb' }}>-</span>, width: 160 },
            { title: 'JVM(-Xmx)', dataIndex: 'jvm_xmx', width: 100, render: (v) => v || <span style={{ color: '#bbb' }}>-</span> },
            { title: '准入', dataIndex: 'admission_enabled', width: 70, render: (v: boolean) => v ? <Tag color="green">开</Tag> : <Tag>关</Tag> },
            {
              title: '操作', width: 120, fixed: 'right', render: (_, r) => (
                <Space>
                  <a onClick={() => openEdit(r)}>编辑</a>
                  <Popconfirm title="删除该管控条目？" onConfirm={async () => { await deleteResourceGovernance(r.id); message.success('已删除'); load() }}>
                    <a style={{ color: '#cf1322' }}>删除</a>
                  </Popconfirm>
                </Space>
              )
            },
          ]} />
      </Card>

      <Modal open={modalOpen} title={editRow ? '编辑管控条目' : '新增管控条目'} onOk={submit}
        onCancel={() => setModalOpen(false)} width={640} destroyOnClose>
        <Form form={form} layout="vertical">
          <Space wrap>
            <Form.Item name="cluster_id" label="集群" rules={[{ required: true }]}>
              <Select style={{ width: 180 }} disabled={!!editRow} options={clusters.map((c) => ({ value: c.id, label: c.name }))} />
            </Form.Item>
            <Form.Item name="namespace" label="命名空间" rules={[{ required: true }]}>
              <Input style={{ width: 200 }} disabled={!!editRow} />
            </Form.Item>
            <Form.Item name="workload_kind" label="类型" rules={[{ required: true }]}>
              <Select style={{ width: 140 }} disabled={!!editRow} options={[{ value: 'deployment', label: 'deployment' }]} />
            </Form.Item>
            <Form.Item name="workload_name" label="工作负载名" rules={[{ required: true }]}>
              <Input style={{ width: 220 }} disabled={!!editRow} />
            </Form.Item>
            <Form.Item name="container" label="容器（空=第一个）">
              <Input style={{ width: 180 }} />
            </Form.Item>
          </Space>
          <Space wrap>
            <Form.Item name="replicas" label="副本数（-1=不管控）" tooltip="-1 表示不改写副本数">
              <InputNumber min={-1} style={{ width: 160 }} />
            </Form.Item>
            <Form.Item name="admission_enabled" label="纳入准入改写" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Space>
          <Space wrap>
            <Form.Item name="cpu_request" label="CPU request"><Input placeholder="如 600m" style={{ width: 130 }} /></Form.Item>
            <Form.Item name="cpu_limit" label="CPU limit"><Input placeholder="如 1500m" style={{ width: 130 }} /></Form.Item>
            <Form.Item name="memory_request" label="内存 request"><Input placeholder="如 1Gi" style={{ width: 130 }} /></Form.Item>
            <Form.Item name="memory_limit" label="内存 limit"><Input placeholder="如 1Gi" style={{ width: 130 }} /></Form.Item>
          </Space>
          <Space wrap>
            <Form.Item name="jvm_xms" label="JVM -Xms"><Input placeholder="如 512m" style={{ width: 110 }} /></Form.Item>
            <Form.Item name="jvm_xmx" label="JVM -Xmx"><Input placeholder="如 1024m" style={{ width: 110 }} /></Form.Item>
            <Form.Item name="jvm_xss" label="JVM -Xss"><Input placeholder="如 512k" style={{ width: 110 }} /></Form.Item>
            <Form.Item name="jvm_max_metaspace" label="MaxMetaspace"><Input placeholder="如 256m" style={{ width: 120 }} /></Form.Item>
          </Space>
          <Form.Item name="remark" label="备注"><Input.TextArea rows={2} /></Form.Item>
        </Form>
        <Tooltip title="准入控制需配套：webhook 部署在集群内 + 下发 MutatingWebhookConfiguration（见 docs）">
          <span style={{ color: '#999', fontSize: 12 }}>提示：管控值在 Deployment 创建/发布时由准入 webhook 写回 spec。</span>
        </Tooltip>
      </Modal>

      <Modal open={peakOpen} title="高峰资源采集（P80）" footer={null} onCancel={() => setPeakOpen(false)} width={900}>
        <Table rowKey={(r) => `${r.namespace}/${r.deployment}`} size="small" loading={peakLoading} dataSource={peakData} pagination={{ pageSize: 20 }}
          columns={[
            { title: '命名空间', dataIndex: 'namespace', width: 150 },
            { title: '工作负载', dataIndex: 'deployment' },
            { title: 'Pod 数', dataIndex: 'pod_count', width: 80 },
            { title: 'CPU P80', dataIndex: 'cpu_p80_str', width: 110 },
            { title: '内存 P80', dataIndex: 'mem_p80_str', width: 110 },
            { title: '采样点', dataIndex: 'samples', width: 90 },
          ]} />
      </Modal>
    </div>
  )
}
