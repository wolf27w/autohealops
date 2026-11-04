// 节点管理（P1-R4）：节点列表（容量/用量）+ 单个/批量 cordon·uncordon + 均衡计划（只读建议）
import { useEffect, useState, type Key } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Card, Table, Button, Space, Select, Tag, message, Popconfirm, Progress, Modal, InputNumber, Alert, Collapse, Input, Checkbox, Tooltip, Divider } from 'antd'
import { ReloadOutlined, ApartmentOutlined } from '@ant-design/icons'
import { getClusters, getNodes, cordonNode, uncordonNode, batchNodes, getNodeBalancePlan, drainPreview, drainNode, diagnoseNode, setNodeLabels, setNodeTaints, isolateNode, unisolateNode } from '../services/api'
import type { K8sNode, BalancePlan, NodeDiagnosis, NodeTaint } from '../services/api'
import type { Cluster } from '../types'

const pctColor = (v?: number | null) => (!v && v !== 0) ? '#999' : v >= 85 ? '#ff4d4f' : v >= 70 ? '#faad14' : '#52c41a'

const ISOLATE_LABEL = 'autohealops.io/isolated'
const isNodeIsolated = (n?: K8sNode) => !!(n?.labels && n.labels[ISOLATE_LABEL])
// 稳定序列化（比较“有没有变”）
const canonLabels = (m: Record<string, string>) => JSON.stringify(Object.keys(m).sort().map((k) => [k, m[k]]))
const canonTaints = (ts: { key: string; value: string; effect: string }[]) =>
  JSON.stringify(ts.map((t) => [t.key, t.value, t.effect]).sort())

type LabelRow = { key: string; value: string }
type TaintRow = { key: string; value: string; effect: string }

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
  // 节点诊断（P1，只读）
  const [diagOpen, setDiagOpen] = useState(false)
  const [diagNode, setDiagNode] = useState('')
  const [diag, setDiag] = useState<NodeDiagnosis | null>(null)
  const [diagLoading, setDiagLoading] = useState(false)
  // 标签 / 污点编辑（2026-11-04）
  const [metaOpen, setMetaOpen] = useState(false)
  const [metaNode, setMetaNode] = useState('')
  const [labelRows, setLabelRows] = useState<LabelRow[]>([])
  const [taintRows, setTaintRows] = useState<TaintRow[]>([])
  const [origLabels, setOrigLabels] = useState<Record<string, string>>({})
  const [origTaints, setOrigTaints] = useState<TaintRow[]>([])
  const [metaForce, setMetaForce] = useState(false)
  const [metaSaving, setMetaSaving] = useState(false)
  // 隔离 / 解除隔离
  const [isoOpen, setIsoOpen] = useState(false)
  const [isoNode, setIsoNode] = useState('')
  const [isoReason, setIsoReason] = useState('')
  const [isoTaint, setIsoTaint] = useState(true)

  useEffect(() => { getClusters().then((d) => setClusters(d || [])).catch(() => {}) }, [])

  const openDiagnose = (name: string) => {
    if (!clusterId) return
    setDiagNode(name); setDiag(null); setDiagOpen(true); setDiagLoading(true)
    diagnoseNode(clusterId, name).then((d) => setDiag(d)).catch(() => message.error('诊断失败（集群/executor 不可达）')).finally(() => setDiagLoading(false))
  }

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

  // ---- 标签 / 污点编辑 ----
  const openMeta = (node: string) => {
    const n = nodes.find((x) => x.name === node)
    const labels = n?.labels || {}
    const visibleLabels = Object.fromEntries(Object.entries(labels).filter(([k]) => k !== ISOLATE_LABEL && k !== 'autohealops.io/isolate-reason'))
    const taints = (n?.taints_detail || []).map((t: NodeTaint) => ({ key: t.key, value: t.value, effect: t.effect }))
    setMetaNode(node)
    setOrigLabels(visibleLabels)
    setOrigTaints(taints)
    setLabelRows(Object.entries(visibleLabels).map(([key, value]) => ({ key, value })))
    setTaintRows(taints)
    setMetaForce(false)
    setMetaOpen(true)
  }

  const saveMeta = async () => {
    if (!clusterId || !metaNode) return
    const desired: Record<string, string> = {}
    for (const r of labelRows) {
      const k = r.key.trim()
      if (!k) continue
      if (k in desired) { message.error(`标签 key 重复：${k}`); return }
      desired[k] = r.value.trim()
    }
    const taints = taintRows.filter((t) => t.key.trim()).map((t) => ({ key: t.key.trim(), value: t.value.trim(), effect: t.effect }))
    const seen = new Set<string>()
    for (const t of taints) {
      const sig = `${t.key}:${t.effect}`
      if (seen.has(sig)) { message.error(`污点重复（同 key + effect）：${sig}`); return }
      seen.add(sig)
    }
    const labelsChanged = canonLabels(desired) !== canonLabels(origLabels)
    const taintsChanged = canonTaints(taints) !== canonTaints(origTaints)
    if (!labelsChanged && !taintsChanged) { message.info('没有变更'); return }
    setMetaSaving(true)
    try {
      if (labelsChanged) await setNodeLabels(clusterId, metaNode, desired, metaForce)
      if (taintsChanged) await setNodeTaints(clusterId, metaNode, taints)
      message.success(`已更新 ${metaNode}${labelsChanged ? ' 标签' : ''}${taintsChanged ? ' 污点' : ''}`)
      setMetaOpen(false)
      load()
    } catch (e) {
      message.error(String((e as Error)?.message || e))
    } finally { setMetaSaving(false) }
  }

  const doIsolate = async () => {
    if (!clusterId || !isoNode) return
    try {
      await isolateNode(clusterId, isoNode, { reason: isoReason || 'manual', taint: isoTaint })
      message.success(`已隔离 ${isoNode}`)
      setIsoOpen(false); setIsoReason(''); load()
    } catch (e) { message.error(String((e as Error)?.message || e)) }
  }

  const doUnisolate = async (node: string) => {
    if (!clusterId) return
    try { await unisolateNode(clusterId, node); message.success(`已解除隔离 ${node}`); load() }
    catch (e) { message.error(String((e as Error)?.message || e)) }
  }

  const pctCell = (v?: number | null) => (
    <div style={{ minWidth: 96 }}>
      <Progress percent={v ?? 0} size="small" strokeColor={pctColor(v)} format={() => (v === null || v === undefined ? '-' : `${v}%`)} />
    </div>
  )

  return (
    <div style={{ padding: 16 }}>
      <Card size="small" title="集群节点（K8s）"
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
          scroll={{ x: 1400 }}
          columns={[
            { title: '节点', dataIndex: 'name', width: 160, fixed: 'left' },
            { title: '状态', dataIndex: 'ready', width: 90, render: (v: string) => <Tag color={v === 'Ready' ? 'green' : 'red'}>{v === 'Ready' ? '就绪' : '未就绪'}</Tag> },
            { title: '调度', dataIndex: 'schedulable', width: 90, render: (v: boolean) => v ? <Tag color="green">启用</Tag> : <Tag color="orange">停止</Tag> },
            { title: 'CPU', width: 130, render: (_, r) => pctCell(r.cpu_pct) },
            { title: '内存', width: 130, render: (_, r) => pctCell(r.mem_pct) },
            { title: 'Pod 用量', width: 110, render: (_, r) => `${r.pods_count ?? '-'} / ${r.pods_allocatable || '-'}` },
            { title: '角色', dataIndex: 'roles', width: 130, render: (v) => v || '-' },
            {
              title: '标签', width: 220, render: (_, r) => {
                const entries = Object.entries(r.labels || {})
                if (!entries.length) return '-'
                return (
                  <Space size={4} wrap>
                    {entries.slice(0, 2).map(([k, v]) => (
                      <Tooltip key={k} title={`${k}=${v}`} overlayStyle={{ maxWidth: 420 }}>
                        <Tag color={k === ISOLATE_LABEL ? 'orange' : 'blue'} style={{ maxWidth: 96, overflow: 'hidden', textOverflow: 'ellipsis' }}>{k.split('/').pop()}</Tag>
                      </Tooltip>
                    ))}
                    {entries.length > 2 && (
                      <Tooltip title={<div style={{ whiteSpace: 'pre-line' }}>{entries.slice(2).map(([k, v]) => `${k}=${v}`).join('\n')}</div>}>
                        <Tag>+{entries.length - 2}</Tag>
                      </Tooltip>
                    )}
                  </Space>
                )
              }
            },
            {
              title: '污点', width: 210, render: (_, r) => {
                const ts = r.taints || []
                if (!ts.length) return '-'
                return (
                  <Space size={4} wrap>
                    {ts.slice(0, 1).map((t) => <Tag key={t} color="volcano" style={{ maxWidth: 170, overflow: 'hidden', textOverflow: 'ellipsis' }}>{t}</Tag>)}
                    {ts.length > 1 && <Tooltip title={<div style={{ whiteSpace: 'pre-line' }}>{ts.slice(1).join('\n')}</div>}><Tag>+{ts.length - 1}</Tag></Tooltip>}
                  </Space>
                )
              }
            },
            { title: 'Kubelet', dataIndex: 'kubelet_version', width: 110, render: (v) => v || '-' },
            {
              title: '操作', width: 300, fixed: 'right', render: (_, r) => (
                <Space size="small" wrap>
                  <a onClick={() => openDiagnose(r.name)}>诊断</a>
                  <a onClick={() => openMeta(r.name)}>标签/污点</a>
                  {isNodeIsolated(r)
                    ? <a style={{ color: '#389e0d' }} onClick={() => doUnisolate(r.name)}>解除隔离</a>
                    : <a style={{ color: '#d46b08' }} onClick={() => { setIsoNode(r.name); setIsoReason(''); setIsoTaint(true); setIsoOpen(true) }}>隔离</a>}
                  {!isNodeIsolated(r) && (r.schedulable
                    ? <Popconfirm title={`停止调度 ${r.name}？（禁止新 Pod 调度到该节点）`} onConfirm={() => act(r.name, 'cordon')}><a style={{ color: '#cf1322' }}>停止调度</a></Popconfirm>
                    : <Popconfirm title={`启用调度 ${r.name}？（允许新 Pod 调度到该节点）`} onConfirm={() => act(r.name, 'uncordon')}><a style={{ color: '#389e0d' }}>启用调度</a></Popconfirm>)}
                  <a style={{ color: '#cf1322' }} onClick={() => openDrain(r.name)}>排空</a>
                </Space>
              )
            },
          ]}
          locale={{ emptyText: '请选择集群（或该集群 node_exporter/metrics-server 不可用）' }} />
      </Card>

      <Modal open={diagOpen} title={`节点诊断 · ${diagNode}`} footer={null} width={860}
        onCancel={() => setDiagOpen(false)} destroyOnClose>
        {diagLoading ? <div style={{ padding: 24, textAlign: 'center' }}>采集中…</div> : diag && (
          <div>
            <Alert
              type={diag.findings.some(f => f.level === 'critical') ? 'error'
                : diag.findings.some(f => f.level === 'high') ? 'warning' : 'info'}
              showIcon
              message={diag.ready ? '节点 Ready' : '节点 NotReady'}
              description={`压力条件：${diag.pressure.length ? diag.pressure.join('、') : '无'}　·　只读诊断，不执行任何变更`}
              style={{ marginBottom: 12 }} />
            <Space size="large" wrap style={{ marginBottom: 12 }}>
              <span>内存 <b>{diag.metrics.mem_pct ?? '-'}%</b></span>
              <span>load1 <b>{diag.metrics.load1 ?? '-'}</b></span>
              <span>进程数 <b>{diag.metrics.processes ?? '-'}</b></span>
              <span>磁盘 <b>{diag.metrics.disk?.[0] ? `${diag.metrics.disk[0].label} ${diag.metrics.disk[0].value.toFixed(1)}%` : '-'}</b></span>
              <span>inode <b>{diag.metrics.inode?.[0] ? `${diag.metrics.inode[0].label} ${diag.metrics.inode[0].value.toFixed(1)}%` : '-'}</b></span>
              <span>网络错/丢 <b>{diag.metrics.net_err_per_s ?? '-'} / {diag.metrics.net_drop_per_s ?? '-'}</b></span>
            </Space>
            <Table rowKey="code" size="small" pagination={false} dataSource={diag.findings}
              columns={[
                { title: '级别', dataIndex: 'level', width: 90, render: (v: string) => (
                  <Tag color={v === 'critical' ? 'red' : v === 'high' ? 'volcano' : v === 'warn' ? 'gold' : 'blue'}>{v}</Tag>) },
                { title: '结论', dataIndex: 'title' },
                { title: '细节', dataIndex: 'detail', render: (v?: string) => <span style={{ fontSize: 12, color: '#666' }}>{v || '-'}</span> },
                { title: '建议', dataIndex: 'advice', width: 220, render: (v?: string) => <span style={{ fontSize: 12 }}>{v || '-'}</span> },
              ]} />
            <div style={{ marginTop: 12, fontSize: 12, color: '#888' }}>
              处置命令见 <code>docs/ops/NODE-RUNBOOK.md</code>；平台不会在节点上执行任何命令。
            </div>
          </div>
        )}
      </Modal>

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

      <Modal open={metaOpen} title={`编辑节点标签 / 污点 · ${metaNode}`} width={860}
        onCancel={() => setMetaOpen(false)} onOk={saveMeta} okText="保存" confirmLoading={metaSaving} destroyOnClose>
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message="标签用于调度选择（nodeSelector / 亲和性），污点用于拒绝调度。系统保留域（*.kubernetes.io）由 kubelet 维护，默认不可改。" />
        <Space style={{ marginBottom: 8 }} wrap>
          <b>标签（{labelRows.length}）</b>
          <Button size="small" onClick={() => setLabelRows([...labelRows, { key: '', value: '' }])}>+ 添加标签</Button>
          <Checkbox checked={metaForce} onChange={(e) => setMetaForce(e.target.checked)}>允许修改系统保留标签（危险）</Checkbox>
        </Space>
        <Table rowKey={(_, i) => `l${i}`} size="small" pagination={false} dataSource={labelRows}
          locale={{ emptyText: '无标签' }}
          columns={[
            {
              title: 'Key', width: '45%', render: (_, r, i) => (
                <Input value={r.key} placeholder="例如 disk 或 example.com/zone"
                  status={r.key && !/^([a-z0-9]([-a-z0-9.]*[a-z0-9])?\/)?[A-Za-z0-9]([-_.A-Za-z0-9]*[A-Za-z0-9])?$/.test(r.key) ? 'error' : undefined}
                  onChange={(e) => { const n = [...labelRows]; n[i] = { ...n[i], key: e.target.value }; setLabelRows(n) }} />
              )
            },
            {
              title: 'Value', render: (_, r, i) => (
                <Input value={r.value} placeholder="例如 ssd"
                  onChange={(e) => { const n = [...labelRows]; n[i] = { ...n[i], value: e.target.value }; setLabelRows(n) }} />
              )
            },
            { title: '', width: 60, render: (_, __, i) => <a style={{ color: '#cf1322' }} onClick={() => setLabelRows(labelRows.filter((_, j) => j !== i))}>删除</a> },
          ]} />
        <Divider style={{ margin: '16px 0 12px' }} />
        <Space style={{ marginBottom: 8 }}>
          <b>污点（{taintRows.length}）</b>
          <Button size="small" onClick={() => setTaintRows([...taintRows, { key: '', value: '', effect: 'NoSchedule' }])}>+ 添加污点</Button>
        </Space>
        <Table rowKey={(_, i) => `t${i}`} size="small" pagination={false} dataSource={taintRows}
          locale={{ emptyText: '无污点' }}
          columns={[
            {
              title: 'Key', width: '35%', render: (_, r, i) => (
                <Input value={r.key} placeholder="例如 dedicated"
                  onChange={(e) => { const n = [...taintRows]; n[i] = { ...n[i], key: e.target.value }; setTaintRows(n) }} />
              )
            },
            {
              title: 'Value（可空）', width: '25%', render: (_, r, i) => (
                <Input value={r.value} placeholder="例如 gpu"
                  onChange={(e) => { const n = [...taintRows]; n[i] = { ...n[i], value: e.target.value }; setTaintRows(n) }} />
              )
            },
            {
              title: 'Effect', width: 170, render: (_, r, i) => (
                <Select style={{ width: '100%' }} value={r.effect}
                  options={[{ value: 'NoSchedule' }, { value: 'PreferNoSchedule' }, { value: 'NoExecute' }]}
                  onChange={(v) => { const n = [...taintRows]; n[i] = { ...n[i], effect: v }; setTaintRows(n) }} />
              )
            },
            { title: '', width: 60, render: (_, __, i) => <a style={{ color: '#cf1322' }} onClick={() => setTaintRows(taintRows.filter((_, j) => j !== i))}>删除</a> },
          ]} />
        <div style={{ marginTop: 10, fontSize: 12, color: '#888' }}>
          说明：隔离标记（autohealops.io/isolated）不在此处编辑，请用行内「隔离 / 解除隔离」；kubelet 自动维护的污点（disk-pressure / memory-pressure / not-ready 等）平台不开放修改。
        </div>
      </Modal>

      <Modal open={isoOpen} title={`隔离节点 · ${isoNode}`} width={560}
        onCancel={() => setIsoOpen(false)} onOk={doIsolate} okText="确认隔离" okButtonProps={{ danger: true }}>
        <Alert type="warning" showIcon style={{ marginBottom: 12 }}
          message="隔离 = 停止调度（cordon）+ 打标记，不驱逐已有 Pod；需要清空节点请用「排空」。" />
        <div style={{ marginBottom: 6 }}>原因（写入标签 <code>autohealops.io/isolate-reason</code>）：</div>
        <Input value={isoReason} onChange={(e) => setIsoReason(e.target.value)} placeholder="例如 磁盘告警-待清理" maxLength={63} />
        <Checkbox style={{ marginTop: 12 }} checked={isoTaint} onChange={(e) => setIsoTaint(e.target.checked)}>
          同时加 NoSchedule 污点（autohealops.io/isolated=true）
        </Checkbox>
      </Modal>
    </div>
  )
}
