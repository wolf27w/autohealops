// 资源治理：按「命名空间」管理资源上限/下限（LimitRange 默认值 + ResourceQuota 总量）
// 口径（老板 2026-10-15）：整个 namespace 的资源上线/下线，不是 Pod/容器粒度。
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Button, Card, Form, Input, InputNumber, Modal, Popconfirm, Select, Space, Switch, Table, Tag, message } from 'antd'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { getClusters, getClusterNamespaces, getNsLimitStatus, previewNsLimit, applyNsLimit, deleteNsLimit } from '../services/api'
import type { NsLimitStatus } from '../services/api'
import type { Cluster } from '../types'

export default function ResourceGovernance() {
  const [sp] = useSearchParams()
  const [clusters, setClusters] = useState<Cluster[]>([])
  const [clusterId, setClusterId] = useState<number | undefined>(() => {
    const c = sp.get('cluster')
    return c ? Number(c) : undefined
  })
  const [namespaces, setNamespaces] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [configNs, setConfigNs] = useState<string | null>(null)
  const [status, setStatus] = useState<NsLimitStatus | null>(null)
  const [statusLoading, setStatusLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [preview, setPreview] = useState<{ kind: string; name: string; yaml: string }[]>([])
  const [form] = Form.useForm()

  useEffect(() => { getClusters().then((d) => setClusters(d || [])).catch(() => {}) }, [])

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
    <Card size="small" title="资源治理（命名空间级资源上限 / 下限）"
      extra={<Space><Select style={{ width: 200 }} placeholder="选择集群" value={clusterId} onChange={setClusterId}
        options={clusters.map((c) => ({ value: c.id, label: c.name }))} />
        <Button icon={<ReloadOutlined />} onClick={loadNamespaces}>刷新</Button></Space>}>
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
    </Card>
  )
}
