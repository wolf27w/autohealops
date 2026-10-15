// 资源治理·管控台（P0-R1）：维护管控表（副本数/资源配额/JVM 参数），供准入控制改写 Deployment
import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Card, Table, Button, Space, Select, Input, Modal, Form, InputNumber, Switch, message, Popconfirm, Tag, Tooltip } from 'antd'
import { PlusOutlined, ReloadOutlined, FundOutlined } from '@ant-design/icons'
import {
  getClusters, getResourceGovernance, upsertResourceGovernance, deleteResourceGovernance, getResourceGovernancePeak,
  getAdmissionStatus, installAdmission, uninstallAdmission,
  getNsLimitStatus, previewNsLimit, applyNsLimit, deleteNsLimit,
} from '../services/api'
import type { ResourceGovernanceRule, PeakResult, AdmissionStatus, NsLimitStatus } from '../services/api'
import type { Cluster } from '../types'

export default function ResourceGovernance() {
  const [sp] = useSearchParams()
  const [clusters, setClusters] = useState<Cluster[]>([])
  const [clusterId, setClusterId] = useState<number | undefined>(() => {
    const c = sp.get('cluster')
    return c ? Number(c) : undefined
  })
  const [namespace, setNamespace] = useState('')
  const [rows, setRows] = useState<ResourceGovernanceRule[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editRow, setEditRow] = useState<ResourceGovernanceRule | null>(null)
  const [form] = Form.useForm()
  const [peakOpen, setPeakOpen] = useState(false)
  const [peakData, setPeakData] = useState<PeakResult[]>([])
  const [peakLoading, setPeakLoading] = useState(false)
  const [admission, setAdmission] = useState<AdmissionStatus | null>(null)
  const [admLoading, setAdmLoading] = useState(false)
  // 命名空间资源限制（LimitRange/Quota）
  const [nsNs, setNsNs] = useState('')
  const [nsStatus, setNsStatus] = useState<NsLimitStatus | null>(null)
  const [nsLoading, setNsLoading] = useState(false)
  const [nsPreview, setNsPreview] = useState<{ kind: string; name: string; yaml: string }[]>([])
  const [nsForm, setNsForm] = useState({ cpuReq: '200m', memReq: '256Mi', cpuLim: '', memLim: '', quotaOn: false, qCpuLim: '', qMemLim: '', qPods: '' })

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

  const loadAdmission = useCallback(() => {
    if (!clusterId) { setAdmission(null); return }
    getAdmissionStatus(clusterId).then(setAdmission).catch(() => setAdmission(null))
  }, [clusterId])
  useEffect(() => { loadAdmission() }, [loadAdmission])

  const doInstall = async () => {
    if (!clusterId) return
    setAdmLoading(true)
    try { await installAdmission({ cluster_id: clusterId }); message.success('已下发准入配置'); loadAdmission() }
    catch (e) { message.error(String((e as Error)?.message || e)) }
    finally { setAdmLoading(false) }
  }
  const doUninstall = async () => {
    if (!clusterId) return
    setAdmLoading(true)
    try { await uninstallAdmission({ cluster_id: clusterId }); message.success('已回滚准入'); loadAdmission() }
    catch (e) { message.error(String((e as Error)?.message || e)) }
    finally { setAdmLoading(false) }
  }

  // ---- 命名空间资源限制 ----
  const loadNsLimit = useCallback(() => {
    if (!clusterId || !nsNs) { setNsStatus(null); setNsPreview([]); return }
    setNsLoading(true)
    getNsLimitStatus(clusterId, nsNs).then(setNsStatus).catch(() => setNsStatus(null)).finally(() => setNsLoading(false))
  }, [clusterId, nsNs])
  useEffect(() => { loadNsLimit() }, [loadNsLimit])

  const nsBody = () => ({
    cluster_id: clusterId, namespace: nsNs, force: true,
    default_request: { cpu: nsForm.cpuReq, memory: nsForm.memReq },
    default_limit: { cpu: nsForm.cpuLim, memory: nsForm.memLim },
    quota: nsForm.quotaOn ? {
      'requests.cpu': nsForm.qCpuLim, 'limits.cpu': nsForm.qCpuLim,
      'requests.memory': nsForm.qMemLim, 'limits.memory': nsForm.qMemLim, pods: nsForm.qPods,
    } : undefined,
  })
  const doNsPreview = async () => {
    if (!clusterId || !nsNs) { message.warning('选择集群并填命名空间'); return }
    try { const d = (await previewNsLimit(nsBody())) as unknown as { manifests?: { kind: string; name: string; yaml: string }[] }; setNsPreview(d?.manifests || []) }
    catch (e) { message.error(String((e as Error)?.message || e)) }
  }
  const doNsApply = async () => {
    if (!clusterId || !nsNs) return
    setNsLoading(true)
    try {
      const d = (await applyNsLimit(nsBody())) as unknown as { steps?: { success: boolean }[] }
      const ok = Array.isArray(d?.steps) && d.steps.every((s) => s.success)
      message[ok ? 'success' : 'error'](ok ? '已下发（创建才生效）' : '部分失败')
      loadNsLimit()
    } catch (e) { message.error(String((e as Error)?.message || e)) } finally { setNsLoading(false) }
  }
  const doNsDelete = async () => {
    if (!clusterId || !nsNs) return
    setNsLoading(true)
    try { await deleteNsLimit({ cluster_id: clusterId, namespace: nsNs }); message.success('已删除（不再限制）'); loadNsLimit() }
    catch (e) { message.error(String((e as Error)?.message || e)) } finally { setNsLoading(false) }
  }

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

      {clusterId && (
        <Card size="small" title="准入控制下发（MutatingWebhook）" style={{ marginTop: 12 }}
          extra={
            <Space>
              <Tag color={admission?.webhook_installed ? 'green' : 'default'}>
                {admission?.webhook_installed ? 'Webhook 已下发' : 'Webhook 未下发'}
              </Tag>
              <Button size="small" icon={<ReloadOutlined />} onClick={loadAdmission}>刷新</Button>
              <Popconfirm title="下发/更新准入配置（Namespace+Service+MutatingWebhookConfiguration）到该集群？" onConfirm={doInstall}>
                <Button size="small" type="primary" loading={admLoading}>下发准入</Button>
              </Popconfirm>
              <Popconfirm title="回滚准入（删除 webhook + Service）？" onConfirm={doUninstall}>
                <Button size="small" danger loading={admLoading}>回滚准入</Button>
              </Popconfirm>
            </Space>
          }>
          <div style={{ color: '#888', fontSize: 12 }}>{admission?.hint || '选择集群后查看/下发准入配置（下发仅装配置，准入服务需在集群内运行）。'}</div>
        </Card>
      )}

      {clusterId && (
        <Card size="small" style={{ marginTop: 12 }}
          title={<span>命名空间资源限制（LimitRange · <span style={{ color: '#888', fontWeight: 400 }}>创建才生效，不创建不限制</span>）</span>}
          extra={
            <Space>
              <Input placeholder="命名空间" style={{ width: 150 }} value={nsNs} allowClear
                onChange={(e) => setNsNs(e.target.value)} onPressEnter={loadNsLimit} />
              <Button size="small" icon={<ReloadOutlined />} onClick={loadNsLimit}>检测</Button>
              <Button size="small" onClick={doNsPreview} disabled={!nsNs}>预览</Button>
              <Popconfirm title="下发到该命名空间？（创建才生效，仅补默认值、不改动已有对象）" onConfirm={doNsApply}>
                <Button size="small" type="primary" loading={nsLoading} disabled={!nsNs}>下发</Button>
              </Popconfirm>
              <Popconfirm title="删除本平台下发的 LimitRange/Quota？（删除即不再限制）" onConfirm={doNsDelete}>
                <Button size="small" danger loading={nsLoading} disabled={!nsNs}>删除</Button>
              </Popconfirm>
            </Space>
          }>
          <Space wrap style={{ marginBottom: 8 }}>
            <span>默认请求 CPU</span>
            <Input size="small" style={{ width: 90 }} value={nsForm.cpuReq} onChange={(e) => setNsForm({ ...nsForm, cpuReq: e.target.value })} />
            <span>内存</span>
            <Input size="small" style={{ width: 100 }} value={nsForm.memReq} onChange={(e) => setNsForm({ ...nsForm, memReq: e.target.value })} />
            <span>默认上限 CPU</span>
            <Input size="small" style={{ width: 90 }} value={nsForm.cpuLim} onChange={(e) => setNsForm({ ...nsForm, cpuLim: e.target.value })} />
            <span>内存</span>
            <Input size="small" style={{ width: 100 }} value={nsForm.memLim} onChange={(e) => setNsForm({ ...nsForm, memLim: e.target.value })} />
          </Space>
          <Space wrap style={{ marginBottom: 8 }}>
            <Switch size="small" checked={nsForm.quotaOn} onChange={(v) => setNsForm({ ...nsForm, quotaOn: v })} />
            <span>命名空间配额(ResourceQuota)</span>
            {nsForm.quotaOn && (<>
              <span>CPU 上限</span>
              <Input size="small" style={{ width: 80 }} value={nsForm.qCpuLim} onChange={(e) => setNsForm({ ...nsForm, qCpuLim: e.target.value })} />
              <span>内存上限</span>
              <Input size="small" style={{ width: 90 }} value={nsForm.qMemLim} onChange={(e) => setNsForm({ ...nsForm, qMemLim: e.target.value })} />
              <span>Pod 数</span>
              <Input size="small" style={{ width: 70 }} value={nsForm.qPods} onChange={(e) => setNsForm({ ...nsForm, qPods: e.target.value })} />
            </>)}
          </Space>
          <div style={{ fontSize: 12 }}>
            {nsStatus ? (<>
              <Tag color={nsStatus.limitrange_installed ? 'green' : 'default'}>{nsStatus.limitrange_installed ? 'LimitRange 已下发' : 'LimitRange 未下发'}</Tag>
              <Tag color={nsStatus.quota_installed ? 'green' : 'default'}>{nsStatus.quota_installed ? 'Quota 已下发' : 'Quota 未下发'}</Tag>
              {((nsStatus.others?.limitrange?.length || 0) + (nsStatus.others?.resourcequota?.length || 0)) > 0 && (
                <Tag color="orange">该 ns 已有其它对象：{[...(nsStatus.others.limitrange || []), ...(nsStatus.others.resourcequota || [])].join('、')}（不会被改动）</Tag>
              )}
              <div style={{ color: '#888', marginTop: 4 }}>{nsStatus.hint}</div>
            </>) : <span style={{ color: '#888' }}>输入命名空间后点「检测」查看状态；命名空间级对象，创建才生效。</span>}
          </div>
          {nsPreview.length > 0 && (
            <pre style={{ marginTop: 8, background: '#f6f8fa', padding: 8, borderRadius: 6, fontSize: 11, maxHeight: 240, overflow: 'auto' }}>
              {nsPreview.map((m) => m.yaml).join('\n')}
            </pre>
          )}
        </Card>
      )}

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
