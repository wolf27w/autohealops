// 节点管理（P1-R4）：节点列表（容量/用量）+ 单个/批量 cordon·uncordon + 均衡计划（只读建议）
import { useEffect, useState, type Key } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Card, Table, Button, Space, Select, Tag, message, Popconfirm, Progress, Modal, InputNumber, Alert, Collapse } from 'antd'
import { ReloadOutlined, ApartmentOutlined } from '@ant-design/icons'
import { getClusters, getNodes, cordonNode, uncordonNode, batchNodes, getNodeBalancePlan, drainPreview, drainNode } from '../services/api'
import type { K8sNode, BalancePlan } from '../services/api'
import type { Cluster } from '../types'

const pctColor = (v?: number | null) => (!v && v !== 0) ? '#999' : v >= 85 ? '#ff4d4f' : v >= 70 ? '#faad14' : '#52c41a'

export default function NodeManagement() {
  const [clusters, setClusters] = useState<Cluster[]>([])
  const [sp] = useSearchParams()
  const [clusterId, setClusterId] = useState<number | undefined>(() => {
    const c = sp.get('cluster')
    return c ? Number(c) : undefined
  })
  const [nodes, setNodes] = useState<K8sNode[]>([])
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<Key[]>([])
  const [busy, setBusy] = useState(false)
  const [planOpen, setPlanOpen] = useState(false)
  const [plan, setPlan] = useState<BalancePlan | null>(null)
  const [threshold, setThreshold] = useState(80)
  const [planLoading, setPlanLoading] = useState(false)
  const [drainOpen, setDrainOpen] = useState(false)
  const [drainNodeName, setDrainNodeName] = useState('')
  const [drainText, setDrainText] = useState('')
  const [drainLoading, setDrainLoading] = useState(false)
  const [draining, setDraining] = useState(false)

  useEffect(() => { getClusters().then((d) => setClusters(d || [])).catch(() => {}) }, [])

  const load = () => {
    if (!clusterId) { setNodes([]); return }
    setLoading(true)
    getNodes(clusterId).then((d) => setNodes(d || [])).catch(() => {}).finally(() => setLoading(false))
  }
  useEffect(load, [clusterId]) // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (node: string, op: 'cordon' | 'uncordon') => {
    if (!clusterId) return
    if (op === 'cordon') { await cordonNode(clusterId, node); message.success(`已 cordon ${node}`) }
    else { await uncordonNode(clusterId, node); message.success(`已 uncordon ${node}`) }
    load()
  }
  const openDrain = async (node: string) => {
    if (!clusterId) return
    setDrainNodeName(node); setDrainOpen(true); setDrainText(''); setDrainLoading(true)
    try { const d = await drainPreview(clusterId, node); setDrainText(d?.output || d?.error || '') }
    catch (e) { setDrainText(String((e as Error)?.message || e)) }
    finally { setDrainLoading(false) }
  }
  const doDrain = async () => {
    if (!clusterId || !drainNodeName) return
    setDraining(true)
    try { const d = await drainNode(clusterId, drainNodeName); message.success('已执行排空'); setDrainText(d?.output || ''); setDrainOpen(false); load() }
    catch (e) { message.error(String((e as Error)?.message || e)) }
    finally { setDraining(false) }
  }
  const doBatch = async (op: 'cordon' | 'uncordon') => {
    if (!clusterId || selected.length === 0) { message.warning('请先选择节点'); return }
    setBusy(true)
    try {
      const d = await batchNodes(clusterId, op, selected.map(String))
      const okN = (d.results || []).filter((r) => r.success).length
      message.success(`批量 ${op} 完成：成功 ${okN}/${selected.length}`)
      setSelected([])
      load()
    } catch (e) { message.error(String((e as Error)?.message || e)) }
    finally { setBusy(false) }
  }
  const runPlan = async () => {
    if (!clusterId) { message.warning('请先选择集群'); return }
    setPlanOpen(true); setPlanLoading(true); setPlan(null)
    getNodeBalancePlan(clusterId, threshold).then(setPlan).catch(() => {}).finally(() => setPlanLoading(false))
  }

  const pctCell = (v?: number | null) => (
    <div style={{ minWidth: 96 }}>
      <Progress percent={v ?? 0} size="small" strokeColor={pctColor(v)} format={() => (v === null || v === undefined ? '-' : `${v}%`)} />
    </div>
  )

  return (
    <div style={{ padding: 16 }}>
      <Card size="small" title="节点管理"
        extra={
          <Space>
            <Select allowClear placeholder="选择集群" style={{ width: 180 }} value={clusterId}
              onChange={(v) => { setClusterId(v); setSelected([]) }} options={clusters.map((c) => ({ value: c.id, label: c.name }))} />
            <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
            <Button icon={<ApartmentOutlined />} onClick={runPlan}>均衡计划</Button>
          </Space>
        }>
        {selected.length > 0 && (
          <Space style={{ marginBottom: 8 }}>
            <span>已选 {selected.length} 个：</span>
            <Popconfirm title={`批量停止调度选中 ${selected.length} 个节点？`} onConfirm={() => doBatch('cordon')}>
              <Button size="small" loading={busy}>批量停止调度</Button>
            </Popconfirm>
            <Popconfirm title="批量启用调度？" onConfirm={() => doBatch('uncordon')}>
              <Button size="small" loading={busy}>批量启用调度</Button>
            </Popconfirm>
            <Button size="small" type="link" onClick={() => setSelected([])}>清除选择</Button>
          </Space>
        )}
        <Table rowKey="name" size="small" loading={loading} dataSource={nodes} pagination={{ pageSize: 20 }}
          rowSelection={{ selectedRowKeys: selected, onChange: setSelected }}
          scroll={{ x: 1000 }}
          columns={[
            { title: '节点', dataIndex: 'name', width: 160, fixed: 'left' },
            { title: '状态', dataIndex: 'ready', width: 90, render: (v: string) => <Tag color={v === 'Ready' ? 'green' : 'red'}>{v === 'Ready' ? '就绪' : '未就绪'}</Tag> },
            { title: '调度', dataIndex: 'schedulable', width: 90, render: (v: boolean) => v ? <Tag color="green">启用</Tag> : <Tag color="orange">停止</Tag> },
            { title: 'CPU', width: 130, render: (_, r) => pctCell(r.cpu_pct) },
            { title: '内存', width: 130, render: (_, r) => pctCell(r.mem_pct) },
            { title: 'Pod 用量', width: 110, render: (_, r) => `${r.pods_count ?? '-'} / ${r.pods_allocatable || '-'}` },
            { title: '角色', dataIndex: 'roles', width: 150, render: (v) => v || '-' },
            { title: 'Kubelet', dataIndex: 'kubelet_version', width: 110, render: (v) => v || '-' },
            {
              title: '操作', width: 140, fixed: 'right', render: (_, r) => (
                <Space size="small">
                  {r.schedulable
                    ? <Popconfirm title={`停止调度 ${r.name}？（禁止新 Pod 调度到该节点）`} onConfirm={() => act(r.name, 'cordon')}><a style={{ color: '#cf1322' }}>停止调度</a></Popconfirm>
                    : <Popconfirm title={`启用调度 ${r.name}？（允许新 Pod 调度到该节点）`} onConfirm={() => act(r.name, 'uncordon')}><a style={{ color: '#389e0d' }}>启用调度</a></Popconfirm>}
                  <a style={{ color: '#cf1322' }} onClick={() => openDrain(r.name)}>排空</a>
                </Space>
              )
            },
          ]}
          locale={{ emptyText: '请选择集群（或该集群 node_exporter/metrics-server 不可用）' }} />
      </Card>

      <Modal open={planOpen} title="节点均衡计划（只读建议，不执行）" footer={null} onCancel={() => setPlanOpen(false)} width={920}>
        <Space style={{ marginBottom: 12 }}>
          <span>CPU/内存阈值：</span>
          <InputNumber min={50} max={95} value={threshold} onChange={(v) => setThreshold(Number(v) || 80)} addonAfter="%" />
          <Button size="small" onClick={runPlan} loading={planLoading}>重新计算</Button>
        </Space>
        {plan?.suggestion && <Alert type="info" showIcon style={{ marginBottom: 12 }} message={plan.suggestion} />}
        <div style={{ marginBottom: 8 }}>
          <Tag color="orange">热点节点 {plan?.hot_nodes.length ?? 0}</Tag>
          <Tag color="blue">冷门节点 {plan?.cold_nodes.length ?? 0}</Tag>
        </div>
        <Table rowKey="node" size="small" loading={planLoading} pagination={false} dataSource={plan?.batches || []}
          columns={[
            { title: '热点节点', dataIndex: 'node', width: 160 },
            { title: 'CPU', dataIndex: 'cpu_pct', width: 80, render: (v) => (v ?? '-') + '%' },
            { title: '内存', dataIndex: 'mem_pct', width: 80, render: (v) => (v ?? '-') + '%' },
            { title: '可迁移 Pod', dataIndex: 'total_candidates', width: 100 },
            {
              title: '候选（按 CPU 请求降序，前 20）', render: (_, r) => (
                <Collapse size="small" ghost items={[{
                  key: 'p', label: `展开 ${r.candidate_pods.length} 个`,
                  children: (
                    <div style={{ fontSize: 12, fontFamily: 'monospace' }}>
                      {r.candidate_pods.map((p) => (
                        <div key={`${p.namespace}/${p.name}`}>{p.namespace}/{p.name} · {p.owner} · cpu {p.cpu_request_milli}m</div>
                      ))}
                    </div>
                  ),
                }]} />
              )
            },
          ]}
          locale={{ emptyText: '无热点节点（或 metrics-server 不可用）' }} />
      </Modal>

      <Modal open={drainOpen} title={`节点排空（drain）预览：${drainNodeName}`} width={760}
        onCancel={() => setDrainOpen(false)}
        okText="确认排空（cordon + 驱逐）"
        okButtonProps={{ danger: true, loading: draining }}
        onOk={doDrain}>
        <Alert type="warning" showIcon style={{ marginBottom: 8 }}
          message="排空会 cordon 该节点并驱逐其上的 Pod（PDB 感知，跳过 DaemonSet/mirror）。请先确认上层控制器能重新调度。" />
        <pre style={{ background: '#f6f8fa', padding: 8, borderRadius: 6, fontSize: 12, maxHeight: 320, overflow: 'auto', whiteSpace: 'pre-wrap' }}>
          {drainLoading ? '预览计算中…' : drainText}
        </pre>
      </Modal>
    </div>
  )
}
