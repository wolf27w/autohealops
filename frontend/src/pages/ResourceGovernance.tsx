// 资源治理：命名空间级资源上限/下限（LimitRange + ResourceQuota）+ 工作负载管控表（R1）
// 口径（老板 2026-10-15）：命名空间级是整个 namespace 的资源上限/下限，不是 Pod 粒度；
// 工作负载管控表按 集群+命名空间+工作负载 记录需求值/限制值，支持批量扩缩容/重启。
import { useEffect, useMemo, useState, type Key } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Button, Card, DatePicker, Form, Input, InputNumber, Modal, Popconfirm, Select, Space, Switch, Table, Tabs, Tag, message } from 'antd'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import dayjs, { type Dayjs } from 'dayjs'
import {
  getClusters, getClusterNamespaces, getNsLimitStatus, previewNsLimit, applyNsLimit, deleteNsLimit,
  getResourceGovernanceEntries, createResourceGovernanceEntry, updateResourceGovernanceEntry, deleteResourceGovernanceEntry,
  batchScaleResourceGovernance, batchRestartResourceGovernance,
} from '../services/api'
import type { NsLimitStatus, ResourceGovernanceEntry, CtlBatchResultItem } from '../services/api'
import type { Cluster } from '../types'

const WORKLOAD_KINDS = [
  { value: 'deployment', label: 'Deployment' },
  { value: 'statefulset', label: 'StatefulSet' },
  { value: 'daemonset', label: 'DaemonSet' },
]

// ============ 命名空间级治理（原有逻辑） ============
function NsLevelGovernance({ clusters, clusterId }: {
  clusters: Cluster[]; clusterId: number | undefined
}) {
  const [namespaces, setNamespaces] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [configNs, setConfigNs] = useState<string | null>(null)
  const [status, setStatus] = useState<NsLimitStatus | null>(null)
  const [statusLoading, setStatusLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [preview, setPreview] = useState<{ kind: string; name: string; yaml: string }[]>([])
  const [form] = Form.useForm()

  const loadNamespaces = () => {
    if (!clusterId) { setNamespaces([]); return }
    setLoading(true)
    getClusterNamespaces(clusterId).then((d) => setNamespaces(d || [])).catch(() => setNamespaces([])).finally(() => setLoading(false))
  }
  useEffect(loadNamespaces, [clusterId]) // eslint-disable-line react-hooks/exhaustive-deps

  const openConfig = async (ns: string) => {
    setConfigNs(ns); setPreview([])
    form.resetFields()
    form.setFieldsValue({ quotaOn: false, defCpu: '200m', defMem: '256Mi', qCpu: '', qMem: '', qPods: '' })
    setStatusLoading(true); setStatus(null)
    try { const s = await getNsLimitStatus(clusterId!, ns); setStatus(s) } catch { setStatus(null) } finally { setStatusLoading(false) }
  }

  const doPreview = async () => {
    const v = await form.validateFields()
    const default_request = v.defCpu || v.defMem ? { cpu: v.defCpu, memory: v.defMem } : {}
    const quota: Record<string, unknown> = v.quotaOn ? {
      'limits.cpu': v.qCpu, 'limits.memory': v.qMem, 'pods': v.qPods ? Number(v.qPods) : undefined,
    } : {}
    Object.keys(quota).forEach((k) => { if (!quota[k]) delete quota[k] })
    try {
      const r = await previewNsLimit({ cluster_id: clusterId, namespace: configNs, default_request, quota })
      const m = (r as { manifests?: { kind: string; name: string; yaml: string }[] })?.manifests || []
      setPreview(m)
    } catch { /* */ }
  }

  const doApply = async () => {
    const v = await form.validateFields()
    const default_request = v.defCpu || v.defMem ? { cpu: v.defCpu, memory: v.defMem } : {}
    const quota: Record<string, unknown> = {}
    if (v.quotaOn) {
      if (v.qCpu) quota['limits.cpu'] = v.qCpu
      if (v.qMem) quota['limits.memory'] = v.qMem
      if (v.qPods) quota['pods'] = Number(v.qPods)
    }
    setSaving(true)
    try {
      await applyNsLimit({ cluster_id: clusterId, namespace: configNs, default_request, quota })
      message.success('已上线（创建即生效，不影响已运行工作负载）')
      setConfigNs(null); loadNamespaces()
    } catch { /* */ } finally { setSaving(false) }
  }

  const doDelete = async () => {
    setSaving(true)
    try { await deleteNsLimit({ cluster_id: clusterId, namespace: configNs }); message.success('已下线（删除限制）'); setConfigNs(null); loadNamespaces() } catch { /* */ } finally { setSaving(false) }
  }

  return (
    <div>
      <div style={{ color: '#888', fontSize: 13, marginBottom: 10 }}>
        针对整个「命名空间」设置资源上限（ResourceQuota：CPU/内存总量、Pod 数）与下限（LimitRange：默认 request/limit）。创建即生效、删除即不再限制；不影响已运行的工作负载。
      </div>
      <Table rowKey={(r) => r} size="small" loading={loading} dataSource={namespaces} pagination={{ pageSize: 20 }}
        columns={[
          { title: '命名空间', dataIndex: 'ns', render: (_: unknown, r: string) => r },
          { title: '操作', width: 180, render: (_: unknown, r: string) => (
            <Space size="small">
              <Button size="small" type="primary" onClick={() => openConfig(r)}>配置上下限</Button>
            </Space>
          ) },
        ]} />

      <Modal title={`命名空间资源上下限：${configNs}`} open={!!configNs} onCancel={() => setConfigNs(null)}
        footer={<Space><Button onClick={doPreview}>预览 YAML</Button><Button type="primary" loading={saving} onClick={doApply}>上线</Button>
          <Popconfirm title="下线（删除该命名空间的资源限制）？" onConfirm={doDelete}><Button danger loading={saving}>下线</Button></Popconfirm></Space>}
        width={640} destroyOnClose>
        {status && (
          <Space style={{ marginBottom: 12 }} size="large">
            <span>当前已下发：<Tag color={status.limitrange_installed ? 'green' : 'default'}>LimitRange{status.limitrange_installed ? ' 已装' : ' 未装'}</Tag>
              <Tag color={status.quota_installed ? 'green' : 'default'}>ResourceQuota{status.quota_installed ? ' 已装' : ' 未装'}</Tag></span>
            {(status.others.limitrange.length > 0 || status.others.resourcequota.length > 0) && <span style={{ color: '#faad14', fontSize: 12 }}>⚠️ 存在其它 LimitRange/ResourceQuota</span>}
          </Space>
        )}
        <Form form={form} layout="vertical">
          <div style={{ fontWeight: 600, marginBottom: 6 }}>下限（LimitRange：默认 request/limit，只补默认不设 max/min）</div>
          <Space wrap>
            <Form.Item name="defCpu" label="默认 CPU request" style={{ marginBottom: 8 }}><Input placeholder="200m" style={{ width: 140 }} /></Form.Item>
            <Form.Item name="defMem" label="默认内存 request" style={{ marginBottom: 8 }}><Input placeholder="256Mi" style={{ width: 140 }} /></Form.Item>
          </Space>
          <Form.Item name="quotaOn" label="开启上限（ResourceQuota 总量）" valuePropName="checked" style={{ marginBottom: 8 }}><Switch /></Form.Item>
          <Form.Item noStyle shouldUpdate={(p, c) => p.quotaOn !== c.quotaOn}>
            {({ getFieldValue }) => getFieldValue('quotaOn') ? (
              <Space wrap>
                <Form.Item name="qCpu" label="CPU 总量上限" style={{ marginBottom: 8 }}><Input placeholder="如 8" style={{ width: 120 }} /></Form.Item>
                <Form.Item name="qMem" label="内存总量上限" style={{ marginBottom: 8 }}><Input placeholder="如 16Gi" style={{ width: 130 }} /></Form.Item>
                <Form.Item name="qPods" label="Pod 数上限" style={{ marginBottom: 8 }}><InputNumber min={1} placeholder="如 100" style={{ width: 110 }} /></Form.Item>
              </Space>
            ) : null}
          </Form.Item>
        </Form>
        {preview.length > 0 && preview.map((p) => (
          <pre key={p.kind + p.name} style={{ background: '#fafafa', padding: 8, fontSize: 12, maxHeight: 220, overflow: 'auto', whiteSpace: 'pre-wrap' }}>{p.yaml}</pre>
        ))}
      </Modal>
    </div>
  )
}

// ============ 工作负载管控表（R1） ============
function WorkloadGovernance({ clusters, clusterId }: {
  clusters: Cluster[]; clusterId: number | undefined
}) {
  const [entries, setEntries] = useState<ResourceGovernanceEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [namespaces, setNamespaces] = useState<string[]>([])
  const [namespaceFilter, setNamespaceFilter] = useState<string | undefined>(undefined)
  const [selectedRowKeys, setSelectedRowKeys] = useState<Key[]>([])
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<ResourceGovernanceEntry | null>(null)
  const [saving, setSaving] = useState(false)
  const [batchResult, setBatchResult] = useState<{ title: string; items: CtlBatchResultItem[] } | null>(null)
  const [form] = Form.useForm()

  const clusterName = useMemo(() => {
    const m = new Map<number, string>(clusters.map((c) => [c.id, c.name]))
    return (id: number) => m.get(id) ?? String(id)
  }, [clusters])

  useEffect(() => {
    if (!clusterId) { setNamespaces([]); return }
    getClusterNamespaces(clusterId).then((d) => setNamespaces(d || [])).catch(() => setNamespaces([]))
  }, [clusterId])

  const load = () => {
    setLoading(true)
    getResourceGovernanceEntries({ cluster_id: clusterId, namespace: namespaceFilter })
      .then((d) => setEntries(d || []))
      .catch(() => setEntries([]))
      .finally(() => setLoading(false))
  }
  useEffect(load, [clusterId, namespaceFilter]) // eslint-disable-line react-hooks/exhaustive-deps

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({
      cluster_id: clusterId,
      workload_kind: 'deployment',
      demand_replicas: -1,
      limit_replicas: -1,
      new_service_exempt: false,
      cpu_request: '', cpu_limit: '', memory_request: '', memory_limit: '',
      jvm_xms: '', jvm_xmx: '', remark: '',
    })
    setModalOpen(true)
  }

  const openEdit = (row: ResourceGovernanceEntry) => {
    setEditing(row)
    form.resetFields()
    form.setFieldsValue({
      cluster_id: row.cluster_id,
      namespace: row.namespace,
      workload_kind: row.workload_kind,
      workload_name: row.workload_name,
      demand_replicas: row.demand_replicas,
      limit_replicas: row.limit_replicas,
      cpu_request: row.cpu_request,
      cpu_limit: row.cpu_limit,
      memory_request: row.memory_request,
      memory_limit: row.memory_limit,
      jvm_xms: row.jvm_xms,
      jvm_xmx: row.jvm_xmx,
      new_service_exempt: row.new_service_exempt,
      exempt_start: row.exempt_start ? dayjs(row.exempt_start) : null,
      exempt_end: row.exempt_end ? dayjs(row.exempt_end) : null,
      remark: row.remark,
    })
    setModalOpen(true)
  }

  const handleSubmit = async () => {
    const v = await form.validateFields()
    const payload: Record<string, unknown> = {
      cluster_id: v.cluster_id,
      namespace: v.namespace,
      workload_kind: v.workload_kind,
      workload_name: v.workload_name,
      demand_replicas: v.demand_replicas,
      limit_replicas: v.limit_replicas,
      cpu_request: v.cpu_request ?? '',
      cpu_limit: v.cpu_limit ?? '',
      memory_request: v.memory_request ?? '',
      memory_limit: v.memory_limit ?? '',
      jvm_xms: v.jvm_xms ?? '',
      jvm_xmx: v.jvm_xmx ?? '',
      new_service_exempt: !!v.new_service_exempt,
      exempt_start: v.exempt_start ? (v.exempt_start as Dayjs).toISOString() : null,
      exempt_end: v.exempt_end ? (v.exempt_end as Dayjs).toISOString() : null,
      remark: v.remark ?? '',
    }
    setSaving(true)
    try {
      if (editing) { await updateResourceGovernanceEntry(editing.id, payload); message.success('已更新') }
      else { await createResourceGovernanceEntry(payload); message.success('已新增') }
      setModalOpen(false)
      load()
    } catch { /* */ } finally { setSaving(false) }
  }

  const handleDelete = async (id: number) => {
    try { await deleteResourceGovernanceEntry(id); message.success('已删除'); load() } catch { /* */ }
  }

  const handleBatchScale = async () => {
    setSaving(true)
    try {
      const items = await batchScaleResourceGovernance({ cluster_id: clusterId, namespace: namespaceFilter, workload_ids: selectedRowKeys as number[] })
      setBatchResult({ title: '批量扩缩容结果', items: items || [] })
      setSelectedRowKeys([])
      message.success('批量扩缩容已执行')
    } catch { /* */ } finally { setSaving(false) }
  }

  const handleBatchRestart = async () => {
    setSaving(true)
    try {
      const items = await batchRestartResourceGovernance({ cluster_id: clusterId, namespace: namespaceFilter, workload_ids: selectedRowKeys as number[] })
      setBatchResult({ title: '批量重启结果', items: items || [] })
      setSelectedRowKeys([])
      message.success('批量重启已执行')
    } catch { /* */ } finally { setSaving(false) }
  }

  const showReplicas = (v: number) => (v < 0 ? <span style={{ color: '#bbb' }}>不管控</span> : v)
  const showRes = (req: string, lim: string) => (req || lim) ? `${req || '-'} / ${lim || '-'}` : <span style={{ color: '#bbb' }}>—</span>
  const showJvm = (xms: string, xmx: string) => (xms || xmx) ? `-Xms${xms || '-'} / -Xmx${xmx || '-'}` : <span style={{ color: '#bbb' }}>—</span>
  const showExempt = (r: ResourceGovernanceEntry) => {
    if (!r.exempt_start && !r.exempt_end) return <span style={{ color: '#bbb' }}>—</span>
    const fmt = (s: string | null) => (s ? dayjs(s).format('MM-DD HH:mm') : '')
    return `${fmt(r.exempt_start)} ~ ${fmt(r.exempt_end)}`
  }

  const columns = [
    { title: '集群', dataIndex: 'cluster_id', width: 120, render: (id: number) => clusterName(id) },
    { title: '命名空间', dataIndex: 'namespace', width: 130 },
    { title: '类型', dataIndex: 'workload_kind', width: 110, render: (k: string) => <Tag>{k}</Tag> },
    { title: '工作负载', dataIndex: 'workload_name', width: 160 },
    { title: '需求副本', dataIndex: 'demand_replicas', width: 90, align: 'center' as const, render: showReplicas },
    { title: '限制副本', dataIndex: 'limit_replicas', width: 90, align: 'center' as const, render: showReplicas },
    { title: 'CPU(需求/限制)', width: 140, render: (_: unknown, r: ResourceGovernanceEntry) => showRes(r.cpu_request, r.cpu_limit) },
    { title: '内存(需求/限制)', width: 150, render: (_: unknown, r: ResourceGovernanceEntry) => showRes(r.memory_request, r.memory_limit) },
    { title: 'JVM', width: 170, render: (_: unknown, r: ResourceGovernanceEntry) => showJvm(r.jvm_xms, r.jvm_xmx) },
    { title: '新服务免确认', dataIndex: 'new_service_exempt', width: 100, align: 'center' as const, render: (v: boolean) => (v ? <Tag color="green">是</Tag> : <Tag>否</Tag>) },
    { title: '豁免窗口', width: 170, render: (_: unknown, r: ResourceGovernanceEntry) => showExempt(r) },
    { title: '备注', dataIndex: 'remark', width: 120, ellipsis: true },
    { title: '操作', width: 120, fixed: 'right' as const, render: (_: unknown, r: ResourceGovernanceEntry) => (
      <Space size="small">
        <Button size="small" onClick={() => openEdit(r)}>编辑</Button>
        <Popconfirm title="删除该管控条目？" onConfirm={() => handleDelete(r.id)}><Button size="small" danger>删除</Button></Popconfirm>
      </Space>
    ) },
  ]

  return (
    <div>
      <div style={{ color: '#888', fontSize: 13, marginBottom: 10 }}>
        按「集群 + 命名空间 + 工作负载」维护资源管控值（需求副本数 / 限制副本数 / CPU·内存 request+limit / JVM -Xms·-Xmx），支持批量扩缩容与批量重启。副本填 -1 表示不管控，资源项留空表示不管控；豁免窗口内允许突破限制副本数。
      </div>
      <Space wrap style={{ marginBottom: 12 }}>
        <Select style={{ width: 180 }} placeholder="命名空间过滤" allowClear showSearch value={namespaceFilter}
          onChange={(v) => setNamespaceFilter(v)} disabled={!clusterId}
          options={namespaces.map((n) => ({ value: n, label: n }))} />
        <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新增条目</Button>
        <Button onClick={handleBatchScale} loading={saving} disabled={selectedRowKeys.length === 0}>批量扩缩容 ({selectedRowKeys.length})</Button>
        <Button onClick={handleBatchRestart} loading={saving} disabled={selectedRowKeys.length === 0}>批量重启 ({selectedRowKeys.length})</Button>
      </Space>

      <Table rowKey="id" size="small" loading={loading} dataSource={entries} scroll={{ x: 1500 }}
        pagination={{ pageSize: 20 }}
        rowSelection={{ selectedRowKeys, onChange: (keys) => setSelectedRowKeys(keys) }}
        columns={columns} />

      <Modal title={editing ? `编辑管控条目：${editing.workload_name}` : '新增管控条目'} open={modalOpen}
        onCancel={() => setModalOpen(false)} onOk={handleSubmit} confirmLoading={saving} width={760} destroyOnClose>
        <Form form={form} layout="vertical">
          <Space wrap>
            <Form.Item name="cluster_id" label="集群" rules={[{ required: true, message: '请选择集群' }]}>
              <Select style={{ width: 200 }} options={clusters.map((c) => ({ value: c.id, label: c.name }))} />
            </Form.Item>
            <Form.Item name="namespace" label="命名空间" rules={[{ required: true, message: '请输入命名空间' }]}>
              <Input style={{ width: 180 }} placeholder="如 default" />
            </Form.Item>
            <Form.Item name="workload_kind" label="工作负载类型" rules={[{ required: true }]}>
              <Select style={{ width: 150 }} options={WORKLOAD_KINDS} />
            </Form.Item>
            <Form.Item name="workload_name" label="工作负载名" rules={[{ required: true, message: '请输入工作负载名' }]}>
              <Input style={{ width: 200 }} />
            </Form.Item>
          </Space>
          <Space wrap>
            <Form.Item name="demand_replicas" label="需求副本数" tooltip="按此副本数扩缩容；-1 = 不管控"><InputNumber min={-1} style={{ width: 120 }} /></Form.Item>
            <Form.Item name="limit_replicas" label="限制副本数" tooltip="扩缩容上限；-1 = 不限制"><InputNumber min={-1} style={{ width: 120 }} /></Form.Item>
            <Form.Item name="new_service_exempt" label="新服务免确认" valuePropName="checked"><Switch /></Form.Item>
          </Space>
          <Space wrap>
            <Form.Item name="cpu_request" label="CPU request"><Input placeholder="如 200m" style={{ width: 120 }} /></Form.Item>
            <Form.Item name="cpu_limit" label="CPU limit"><Input placeholder="如 500m" style={{ width: 120 }} /></Form.Item>
            <Form.Item name="memory_request" label="内存 request"><Input placeholder="如 256Mi" style={{ width: 130 }} /></Form.Item>
            <Form.Item name="memory_limit" label="内存 limit"><Input placeholder="如 512Mi" style={{ width: 130 }} /></Form.Item>
          </Space>
          <Space wrap>
            <Form.Item name="jvm_xms" label="JVM -Xms"><Input placeholder="如 512m" style={{ width: 120 }} /></Form.Item>
            <Form.Item name="jvm_xmx" label="JVM -Xmx"><Input placeholder="如 1024m" style={{ width: 120 }} /></Form.Item>
          </Space>
          <Space wrap>
            <Form.Item name="exempt_start" label="豁免窗口开始"><DatePicker showTime format="YYYY-MM-DD HH:mm" style={{ width: 190 }} /></Form.Item>
            <Form.Item name="exempt_end" label="豁免窗口结束"><DatePicker showTime format="YYYY-MM-DD HH:mm" style={{ width: 190 }} /></Form.Item>
          </Space>
          <Form.Item name="remark" label="备注" style={{ marginBottom: 4 }}><Input.TextArea rows={2} /></Form.Item>
        </Form>
      </Modal>

      <Modal title={batchResult?.title} open={!!batchResult} footer={<Button type="primary" onClick={() => setBatchResult(null)}>关闭</Button>}
        onCancel={() => setBatchResult(null)} width={560}>
        <Table rowKey="id" size="small" dataSource={batchResult?.items || []} pagination={false}
          columns={[
            { title: '命名空间', dataIndex: 'namespace', width: 120 },
            { title: '工作负载', dataIndex: 'name', width: 150 },
            { title: '目标副本', dataIndex: 'target_replicas', width: 90, render: (v?: number) => (v === undefined ? '—' : v) },
            { title: '结果', width: 70, align: 'center' as const, render: (_: unknown, r: CtlBatchResultItem) => (r.success ? <Tag color="green">成功</Tag> : <Tag color="red">失败</Tag>) },
            { title: '信息', dataIndex: 'message', ellipsis: true },
          ]} />
      </Modal>
    </div>
  )
}

export default function ResourceGovernance() {
  const [sp] = useSearchParams()
  const [clusters, setClusters] = useState<Cluster[]>([])
  const [clusterId, setClusterId] = useState<number | undefined>(() => {
    const c = sp.get('cluster')
    return c ? Number(c) : undefined
  })

  useEffect(() => { getClusters().then((d) => setClusters(d || [])).catch(() => {}) }, [])

  return (
    <Card size="small" title="资源治理" extra={
      <Space><Select style={{ width: 200 }} placeholder="选择集群" value={clusterId} onChange={setClusterId} allowClear
        options={clusters.map((c) => ({ value: c.id, label: c.name }))} /></Space>
    }>
      <Tabs
        items={[
          { key: 'ns', label: '命名空间级治理', children: <NsLevelGovernance clusters={clusters} clusterId={clusterId} /> },
          { key: 'workload', label: '工作负载管控', children: <WorkloadGovernance clusters={clusters} clusterId={clusterId} /> },
        ]}
      />
    </Card>
  )
}
