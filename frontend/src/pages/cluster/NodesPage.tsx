// 集群节点页：节点列表（状态/角色/CPU/内存/Pod/IP）+ 10s 轮询
// 操作收 ⋯：诊断 / 隔离·解除隔离 / 封锁·解封 / 驱逐（先预览再确认）/ 标签·污点
// 名称可点击展开该节点上的 Pod 列表（getClusterPods 按 node_name 过滤）
import { useEffect, useState, type Key } from 'react'
import { Card, Table, Button, Space, Tag, message, Modal, Input, Progress, Dropdown, Select, Checkbox, Alert, Divider, InputNumber, Empty, Tooltip } from 'antd'
import { ReloadOutlined, MoreOutlined, ApartmentOutlined, CheckCircleOutlined, ExclamationCircleOutlined } from '@ant-design/icons'
import {
  getNodes, cordonNode, uncordonNode, drainPreview, drainNode, diagnoseNode,
  setNodeLabels, setNodeTaints, isolateNode, unisolateNode, getClusterPods,
  batchNodes, getNodeBalancePlan,
} from '../../services/api'
import type { K8sNode, NodeDiagnosis, NodeTaint, ClusterPod, BalancePlan } from '../../services/api'

const pctColor = (v?: number | null) => (!v && v !== 0) ? '#999' : v >= 85 ? '#ff4d4f' : v >= 70 ? '#faad14' : '#52c41a'

const ISOLATE_LABEL = 'autohealops.io/isolated'
const isNodeIsolated = (n?: K8sNode) => !!(n?.labels && n.labels[ISOLATE_LABEL])
const canonLabels = (m: Record<string, string>) => JSON.stringify(Object.keys(m).sort().map((k) => [k, m[k]]))
const canonTaints = (ts: { key: string; value: string; effect: string }[]) =>
  JSON.stringify(ts.map((t) => [t.key, t.value, t.effect]).sort())

type LabelRow = { key: string; value: string }
type TaintRow = { key: string; value: string; effect: string }


// 节点状态（对齐 KubeSphere 的三态）：运行中 / 告警 / 无法调度 / 未就绪
// 告警 = Ready 但有 MemoryPressure、DiskPressure、PIDPressure、NetworkUnavailable 等为 True
const BAD_CONDITIONS = ['MemoryPressure', 'DiskPressure', 'PIDPressure', 'NetworkUnavailable']
function nodeState(n: K8sNode): { label: string; color: string; tip: string; key: string } {
  const conds = n.conditions || []
  const bad = conds.filter((c) => (BAD_CONDITIONS.includes(c.type) || c.type === 'Ready') && c.status === 'True'
    && c.type !== 'Ready')
  if (n.ready !== 'Ready') {
    const c = conds.find((x) => x.type === 'Ready')
    return { key: 'notready', label: '未就绪', color: 'red', tip: c?.message || '节点未就绪（kubelet 上报 NotReady）' }
  }
  if (n.schedulable === false) {
    return { key: 'unschedulable', label: '无法调度', color: 'orange', tip: '节点已封锁（unschedulable），不会调度新 Pod' }
  }
  if (bad.length) {
    return { key: 'warning', label: '告警', color: 'gold', tip: bad.map((c) => `${c.type}${c.message ? `：${c.message}` : ''}`).join('；') }
  }
  return { key: 'running', label: '运行中', color: 'green', tip: '节点健康' }
}

// 角色：优先 node-role.kubernetes.io/* 标签；无则视为 worker
const roleOf = (n: K8sNode): string => {
  const roles: string[] = []
  for (const k of Object.keys(n.labels || {})) {
    if (k.startsWith('node-role.kubernetes.io/')) {
      const r = k.slice('node-role.kubernetes.io/'.length)
      if (r) roles.push(r)
    }
  }
  return roles.length ? roles.join(', ') : 'worker'
}

export default function NodesPage({ clusterId }: { clusterId: number }) {
  const [nodes, setNodes] = useState<K8sNode[]>([])
  const [stateFilter, setStateFilter] = useState<string>('all')
  const [loading, setLoading] = useState(false)
  // 展开节点 Pod 列表
  const [expandedKeys, setExpandedKeys] = useState<readonly Key[]>([])
  const [nodePods, setNodePods] = useState<Record<string, ClusterPod[]>>({})
  // 诊断
  const [diagOpen, setDiagOpen] = useState(false)
  const [diagNode, setDiagNode] = useState('')
  const [diag, setDiag] = useState<NodeDiagnosis | null>(null)
  const [diagLoading, setDiagLoading] = useState(false)
  // 驱逐
  const [drainOpen, setDrainOpen] = useState(false)
  const [drainNodeName, setDrainNodeName] = useState('')
  const [drainText, setDrainText] = useState('')
  const [drainLoading, setDrainLoading] = useState(false)
  const [draining, setDraining] = useState(false)
  // 标签 / 污点
  const [metaOpen, setMetaOpen] = useState(false)
  const [metaMode, setMetaMode] = useState<'labels' | 'taints'>('labels')
  const [metaNode, setMetaNode] = useState('')
  const [labelRows, setLabelRows] = useState<LabelRow[]>([])
  const [taintRows, setTaintRows] = useState<TaintRow[]>([])
  const [origLabels, setOrigLabels] = useState<Record<string, string>>({})
  const [origTaints, setOrigTaints] = useState<TaintRow[]>([])
  const [metaSaving, setMetaSaving] = useState(false)
  // 隔离
  const [isoOpen, setIsoOpen] = useState(false)
  const [isoNode, setIsoNode] = useState('')
  const [isoReason, setIsoReason] = useState('')
  const [isoTaint, setIsoTaint] = useState(true)
  // 批量调度
  const [selected, setSelected] = useState<Key[]>([])
  const [busy, setBusy] = useState(false)
  // 均衡计划（只读建议）
  const [planOpen, setPlanOpen] = useState(false)
  const [plan, setPlan] = useState<BalancePlan | null>(null)
  const [threshold, setThreshold] = useState(80)
  const [planLoading, setPlanLoading] = useState(false)

  // showLoading: 只有首拉需要遮罩；轮询/操作后刷新一律静默，避免 10s 一次整表闪烁
  const load = (showLoading = false) => {
    if (showLoading) setLoading(true)
    getNodes(clusterId)
      .then((d) => setNodes(d || []))
      .catch(() => setNodes([]))
      .finally(() => setLoading(false))
  }

  // 首拉 + 10s 轮询（轮询静默，页面不可见时跳过）
  useEffect(() => {
    load(true)
    const t = setInterval(() => {
      if (document.hidden) return
      load(false)
    }, 10000)
    return () => clearInterval(t)
  }, [clusterId]) // eslint-disable-line react-hooks/exhaustive-deps

  const togglePods = async (nodeName: string) => {
    if (expandedKeys.includes(nodeName)) {
      setExpandedKeys(expandedKeys.filter((k) => k !== nodeName))
      return
    }
    setExpandedKeys([...expandedKeys, nodeName])
    if (!nodePods[nodeName]) {
      try {
        const d = await getClusterPods(clusterId, '')
        const pods = ((d as { pods?: ClusterPod[] }).pods || []).filter((p) => p.node_name === nodeName)
        setNodePods((prev) => ({ ...prev, [nodeName]: pods }))
      } catch {
        setNodePods((prev) => ({ ...prev, [nodeName]: [] }))
      }
    }
  }

  // ---- 诊断 ----
  const openDiagnose = (name: string) => {
    setDiagNode(name); setDiag(null); setDiagOpen(true); setDiagLoading(true)
    diagnoseNode(clusterId, name)
      .then((d) => setDiag(d))
      .catch(() => message.error('诊断失败（集群/executor 不可达）'))
      .finally(() => setDiagLoading(false))
  }

  // ---- 驱逐 ----
  const openDrain = async (name: string) => {
    setDrainNodeName(name); setDrainOpen(true); setDrainText(''); setDrainLoading(true)
    try {
      const d = await drainPreview(clusterId, name)
      setDrainText(d?.output || d?.error || '')
    } catch (e) {
      setDrainText(String((e as Error)?.message || e))
    } finally {
      setDrainLoading(false)
    }
  }
  const doDrain = async () => {
    if (!drainNodeName) return
    setDraining(true)
    try {
      const d = await drainNode(clusterId, drainNodeName)
      message.success('已执行排空')
      setDrainText(d?.output || '')
      setDrainOpen(false)
      load()
    } catch (e) {
      message.error(String((e as Error)?.message || e))
    } finally {
      setDraining(false)
    }
  }

  // ---- 标签 / 污点 ----
  const openMeta = (name: string) => {
    const n = nodes.find((x) => x.name === name)
    const labels = n?.labels || {}
    const visibleLabels = Object.fromEntries(Object.entries(labels).filter(([k]) => k !== ISOLATE_LABEL && k !== 'autohealops.io/isolate-reason'))
    const taints = (n?.taints_detail || []).map((t: NodeTaint) => ({ key: t.key, value: t.value, effect: t.effect }))
    setMetaNode(name)
    setOrigLabels(visibleLabels)
    setOrigTaints(taints)
    setLabelRows(Object.entries(visibleLabels).map(([key, value]) => ({ key, value })))
    setTaintRows(taints)
    setMetaOpen(true)
  }
  const saveMeta = async () => {
    if (!metaNode) return
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
      if (labelsChanged) await setNodeLabels(clusterId, metaNode, desired, false)
      if (taintsChanged) await setNodeTaints(clusterId, metaNode, taints)
      message.success(`已更新 ${metaNode}`)
      setMetaOpen(false)
      load()
    } catch (e) {
      message.error(String((e as Error)?.message || e))
    } finally {
      setMetaSaving(false)
    }
  }

  // ---- 隔离 / 解除隔离 ----
  const doIsolate = async () => {
    if (!isoNode) return
    try {
      await isolateNode(clusterId, isoNode, { reason: isoReason || 'manual', taint: isoTaint })
      message.success(`已隔离 ${isoNode}`)
      setIsoOpen(false); setIsoReason(''); load()
    } catch (e) {
      message.error(String((e as Error)?.message || e))
    }
  }
  const doUnisolate = (name: string) => {
    Modal.confirm({
      title: `解除隔离 ${name}？`,
      onOk: async () => {
        try { await unisolateNode(clusterId, name); message.success(`已解除隔离 ${name}`); load() }
        catch (e) { message.error(String((e as Error)?.message || e)) }
      },
    })
  }

  // ---- 封锁 / 解封 ----
  const doCordon = (name: string, op: 'cordon' | 'uncordon') => {
    const label = op === 'cordon' ? '封锁（禁止新 Pod 调度）' : '解封（允许新 Pod 调度）'
    Modal.confirm({
      title: `${label} ${name}？`,
      onOk: async () => {
        try {
          if (op === 'cordon') { await cordonNode(clusterId, name); message.success(`已封锁 ${name}`) }
          else { await uncordonNode(clusterId, name); message.success(`已解封 ${name}`) }
          load()
        } catch (e) { message.error(String((e as Error)?.message || e)) }
      },
    })
  }

  // ---- 批量调度（cordon / uncordon） ----
  const doBatch = (op: 'cordon' | 'uncordon') => {
    const names = selected.map(String)
    if (!names.length) { message.warning('请先选择节点'); return }
    const label = op === 'cordon' ? '批量停止调度' : '批量启用调度'
    Modal.confirm({
      title: `${label} ${names.length} 个节点？`,
      content: op === 'cordon' ? '禁止新 Pod 调度到选中节点（不驱逐已有 Pod）。' : '允许新 Pod 调度到选中节点。',
      okText: op === 'cordon' ? '停止调度' : '启用调度',
      onOk: async () => {
        setBusy(true)
        try {
          const d = await batchNodes(clusterId, op, names)
          const okN = (d.results || []).filter((r) => r.success).length
          message.success(`批量${op === 'cordon' ? '停止' : '启用'}调度完成：成功 ${okN}/${names.length}`)
          setSelected([])
          load()
        } catch (e) {
          message.error(String((e as Error)?.message || e))
        } finally {
          setBusy(false)
        }
      },
    })
  }

  // ---- 均衡计划（只读建议） ----
  const runPlan = () => {
    setPlanOpen(true); setPlanLoading(true); setPlan(null)
    getNodeBalancePlan(clusterId, threshold)
      .then(setPlan)
      .catch(() => message.error('获取均衡计划失败'))
      .finally(() => setPlanLoading(false))
  }
  // 候选 Pod：跨热点节点汇总，按 CPU 请求降序取前 20
  const planCandidates = (() => {
    const pods: { ns: string; name: string; owner: string; cpu: number; node: string }[] = []
    for (const b of plan?.batches || []) {
      for (const p of b.candidate_pods || []) pods.push({ ns: p.namespace, name: p.name, owner: p.owner, cpu: p.cpu_request_milli, node: b.node })
    }
    return pods.sort((a, b) => b.cpu - a.cpu).slice(0, 20)
  })()

  const pctCell = (v?: number | null) => (
    <div style={{ minWidth: 96 }}>
      <Progress percent={v ?? 0} size="small" strokeColor={pctColor(v)} format={() => (v === null || v === undefined ? '-' : `${v}%`)} />
    </div>
  )

  const menuFor = (r: K8sNode) => {
    const isolated = isNodeIsolated(r)
    const items: { key: string; label: string; danger?: boolean }[] = [
      { key: 'diag', label: '节点诊断' },
      { key: 'labels', label: '编辑标签' },
      { key: 'taints', label: '编辑污点' },
      isolated ? { key: 'unisolate', label: '解除隔离' } : { key: 'isolate', label: '隔离' },
      r.schedulable ? { key: 'cordon', label: '封锁' } : { key: 'uncordon', label: '解封' },
      { key: 'drain', label: '驱逐', danger: true },
    ]
    return (
      <Dropdown
        trigger={['click']}
        menu={{
          items,
          onClick: ({ key }) => {
            if (key === 'diag') openDiagnose(r.name)
            else if (key === 'labels') { setMetaMode('labels'); openMeta(r.name) }
            else if (key === 'taints') { setMetaMode('taints'); openMeta(r.name) }
            else if (key === 'isolate') { setIsoNode(r.name); setIsoReason(''); setIsoTaint(true); setIsoOpen(true) }
            else if (key === 'unisolate') doUnisolate(r.name)
            else if (key === 'cordon') doCordon(r.name, 'cordon')
            else if (key === 'uncordon') doCordon(r.name, 'uncordon')
            else if (key === 'drain') openDrain(r.name)
          },
        }}
      >
        <Button size="small" type="link" icon={<MoreOutlined />} />
      </Dropdown>
    )
  }

  return (
    <div style={{ padding: 16 }}>
      <Card size="small" title="集群节点（K8s）"
        extra={
          <Space>
            <Button icon={<ApartmentOutlined />} onClick={runPlan}>均衡计划</Button>
            <Button icon={<ReloadOutlined />} onClick={() => load(true)}>刷新</Button>
          </Space>
        }>
        <Space style={{ marginBottom: 8, width: '100%' }} wrap>
          <span style={{ color: selected.length ? undefined : '#999' }}>已选 {selected.length} 个节点</span>
          <Button size="small" type="primary" danger disabled={!selected.length} loading={busy} onClick={() => doBatch('cordon')}>批量停止调度</Button>
          <Button size="small" disabled={!selected.length} loading={busy} onClick={() => doBatch('uncordon')}>批量启用调度</Button>
          <Button size="small" type="link" disabled={!selected.length} onClick={() => setSelected([])}>清除选择</Button>
          <Select
            size="small"
            style={{ width: 150, marginLeft: 'auto' }}
            value={stateFilter}
            onChange={setStateFilter}
            options={[
              { value: 'all', label: '全部状态' },
              { value: 'running', label: '运行中' },
              { value: 'warning', label: '告警' },
              { value: 'unschedulable', label: '无法调度' },
              { value: 'notready', label: '未就绪' },
            ]}
          />
        </Space>
        <Table
          rowKey="name"
          size="small"
          loading={{ spinning: loading, delay: 300 }}
          dataSource={stateFilter === 'all' ? nodes : nodes.filter((n) => nodeState(n).key === stateFilter)}
          pagination={{ pageSize: 20 }}
          rowSelection={{ selectedRowKeys: selected, onChange: setSelected }}
          scroll={{ x: 1400 }}
          expandable={{
            expandedRowKeys: expandedKeys,
            onExpand: (_expanded, record) => togglePods(record.name),
            expandedRowRender: (record) => {
              const pods = nodePods[record.name] || []
              return (
                <div style={{ margin: '4px 0' }}>
                  <Alert type="info" showIcon style={{ marginBottom: 8 }} message={`${record.name} 上的 Pod（${pods.length}）`} />
                  <Table
                    rowKey={(p) => `${p.namespace}-${p.name}`}
                    size="small"
                    pagination={{
                      pageSize: 10,
                      size: 'small',
                      showSizeChanger: true,
                      pageSizeOptions: ['10', '20', '50'],
                      showTotal: (t: number) => `共 ${t} 个`,
                    }}
                    dataSource={pods}
                    columns={[
                      { title: '命名空间', dataIndex: 'namespace', width: 180 },
                      { title: '名称', dataIndex: 'name' },
                      { title: '状态', dataIndex: 'phase', width: 110, render: (v: string) => <Tag color={v === 'Running' ? 'green' : v === 'Pending' ? 'orange' : 'default'}>{v || '-'}</Tag> },
                      { title: 'Pod IP', dataIndex: 'pod_ip', width: 140, render: (v: string) => v || '-' },
                      { title: '重启', dataIndex: 'restart_count', width: 80, render: (v: number) => v ?? '-' },
                    ]}
                    locale={{ emptyText: '该节点暂无 Pod' }}
                  />
                </div>
              )
            },
          }}
          columns={[
            {
              title: '名称', dataIndex: 'name', width: 200, fixed: 'left',
              render: (v: string) => <a onClick={() => togglePods(v)}>{v}</a>,
            },
            {
              title: '状态', width: 110,
              render: (_: unknown, r) => {
                const st = nodeState(r)
                const icon = st.key === 'running' ? <CheckCircleOutlined /> : <ExclamationCircleOutlined />
                return (
                  <Tooltip title={st.tip}>
                    <Tag color={st.color} icon={icon} style={{ cursor: 'help' }}>{st.label}</Tag>
                  </Tooltip>
                )
              },
            },
            { title: '角色', width: 110, render: (_, r) => <Tag color={roleOf(r) === 'worker' ? 'default' : 'blue'}>{roleOf(r)}</Tag> },
            { title: 'Kubelet', dataIndex: 'kubelet_version', width: 110, ellipsis: true, render: (v?: string) => v || '-' },
            { title: 'CPU', width: 130, render: (_, r) => pctCell(r.cpu_pct) },
            { title: '内存', width: 130, render: (_, r) => pctCell(r.mem_pct) },
            { title: 'Pod 数', width: 110, render: (_, r) => `${r.pods_count ?? '-'} / ${r.pods_allocatable || '-'}` },
            { title: 'IP', dataIndex: 'internal_ip', width: 140, render: (v: string) => v || '-' },
            { title: '创建时间', dataIndex: 'created_at', width: 130, render: (v: string) => (v ? String(v).slice(0, 19).replace('T', ' ') : '-') },
            { title: '操作', width: 70, fixed: 'right', render: (_, r) => menuFor(r) },
          ]}
          locale={{ emptyText: '无节点（或 executor 不可达）' }}
        />
      </Card>

      {/* 节点诊断 */}
      <Modal open={diagOpen} title={`节点诊断 · ${diagNode}`} footer={null} width={860}
        onCancel={() => setDiagOpen(false)} destroyOnClose>
        {diagLoading ? <div style={{ padding: 24, textAlign: 'center' }}>采集中…</div> : diag && (
          <div>
            <Alert
              type={diag.findings.some((f) => f.level === 'critical') ? 'error'
                : diag.findings.some((f) => f.level === 'high') ? 'warning' : 'info'}
              showIcon
              message={diag.ready ? '节点 Ready' : '节点 NotReady'}
              description={`压力条件：${diag.pressure.length ? diag.pressure.join('、') : '无'}　·　只读诊断，不执行任何变更`}
              style={{ marginBottom: 12 }}
            />
            <Space size="large" wrap style={{ marginBottom: 12 }}>
              <span>内存 <b>{diag.metrics.mem_pct ?? '-'}%</b></span>
              <span>load1 <b>{diag.metrics.load1 ?? '-'}</b></span>
              <span>进程数 <b>{diag.metrics.processes ?? '-'}</b></span>
              <span>磁盘 <b>{diag.metrics.disk?.[0] ? `${diag.metrics.disk[0].label} ${diag.metrics.disk[0].value.toFixed(1)}%` : '-'}</b></span>
            </Space>
            <Table
              rowKey="code" size="small" pagination={false} dataSource={diag.findings}
              columns={[
                { title: '级别', dataIndex: 'level', width: 90, render: (v: string) => <Tag color={v === 'critical' ? 'red' : v === 'high' ? 'volcano' : v === 'warn' ? 'gold' : 'blue'}>{v}</Tag> },
                { title: '结论', dataIndex: 'title' },
                { title: '细节', dataIndex: 'detail', render: (v?: string) => <span style={{ fontSize: 12, color: '#666' }}>{v || '-'}</span> },
                { title: '建议', dataIndex: 'advice', width: 220, render: (v?: string) => <span style={{ fontSize: 12 }}>{v || '-'}</span> },
              ]}
            />
          </div>
        )}
      </Modal>

      {/* 节点排空（驱逐） */}
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

      {/* 标签 / 污点 */}
      <Modal open={metaOpen} title={`${metaMode === 'labels' ? '编辑节点标签' : '编辑节点污点'} · ${metaNode}`} width={860}
        onCancel={() => setMetaOpen(false)} onOk={saveMeta} okText="保存" confirmLoading={metaSaving} destroyOnClose>
        {metaMode === 'labels' && <>
        <Space style={{ marginBottom: 8 }} wrap>
          <b>标签（{labelRows.length}）</b>
          <Button size="small" onClick={() => setLabelRows([...labelRows, { key: '', value: '' }])}>+ 添加标签</Button>
        </Space>
        <Table
          rowKey={(_, i) => `l${i}`} size="small" pagination={false} dataSource={labelRows}
          locale={{ emptyText: '无标签' }}
          columns={[
            { title: 'Key', width: '45%', render: (_, r, i) => <Input value={r.key} placeholder="例如 disk 或 example.com/zone" onChange={(e) => { const n = [...labelRows]; n[i] = { ...n[i], key: e.target.value }; setLabelRows(n) }} /> },
            { title: 'Value', render: (_, r, i) => <Input value={r.value} placeholder="例如 ssd" onChange={(e) => { const n = [...labelRows]; n[i] = { ...n[i], value: e.target.value }; setLabelRows(n) }} /> },
            { title: '', width: 60, render: (_, __, i) => <a style={{ color: '#cf1322' }} onClick={() => setLabelRows(labelRows.filter((_, j) => j !== i))}>删除</a> },
          ]}
        />
        </>}
        {metaMode === 'taints' && <>
        <Space style={{ marginBottom: 8 }}>
          <b>污点（{taintRows.length}）</b>
          <Button size="small" onClick={() => setTaintRows([...taintRows, { key: '', value: '', effect: 'NoSchedule' }])}>+ 添加污点</Button>
        </Space>
        <Table
          rowKey={(_, i) => `t${i}`} size="small" pagination={false} dataSource={taintRows}
          locale={{ emptyText: '无污点' }}
          columns={[
            { title: 'Key', width: '35%', render: (_, r, i) => <Input value={r.key} placeholder="例如 dedicated" onChange={(e) => { const n = [...taintRows]; n[i] = { ...n[i], key: e.target.value }; setTaintRows(n) }} /> },
            { title: 'Value（可空）', width: '25%', render: (_, r, i) => <Input value={r.value} placeholder="例如 gpu" onChange={(e) => { const n = [...taintRows]; n[i] = { ...n[i], value: e.target.value }; setTaintRows(n) }} /> },
            { title: 'Effect', width: 170, render: (_, r, i) => <Select style={{ width: '100%' }} value={r.effect} options={[{ value: 'NoSchedule' }, { value: 'PreferNoSchedule' }, { value: 'NoExecute' }]} onChange={(v) => { const n = [...taintRows]; n[i] = { ...n[i], effect: v }; setTaintRows(n) }} /> },
            { title: '', width: 60, render: (_, __, i) => <a style={{ color: '#cf1322' }} onClick={() => setTaintRows(taintRows.filter((_, j) => j !== i))}>删除</a> },
          ]}
        />
        </>}
      </Modal>

      {/* 隔离节点 */}
      {/* 均衡计划（只读建议） */}
      <Modal open={planOpen} title="节点均衡计划（只读建议，不执行任何变更）" footer={null} width={920}
        onCancel={() => setPlanOpen(false)} destroyOnClose>
        <Space style={{ marginBottom: 12 }}>
          <span>CPU/内存阈值：</span>
          <InputNumber min={1} max={100} value={threshold} onChange={(v) => setThreshold(Number(v) || 80)} addonAfter="%" />
          <Button size="small" onClick={runPlan} loading={planLoading}>重新计算</Button>
        </Space>
        {plan?.suggestion && <Alert type="info" showIcon style={{ marginBottom: 12 }} message={plan.suggestion} />}
        <div style={{ marginBottom: 8 }}>
          <Tag color="orange">热点节点 {plan?.hot_nodes.length ?? 0}</Tag>
          <Tag color="blue">冷门节点 {plan?.cold_nodes.length ?? 0}</Tag>
        </div>
        <div style={{ marginBottom: 8, fontWeight: 600 }}>热点节点</div>
        <Table
          rowKey="node" size="small" loading={planLoading} pagination={false}
          dataSource={plan?.batches || []}
          style={{ marginBottom: 16 }}
          columns={[
            { title: '节点名', dataIndex: 'node', width: 200, ellipsis: true },
            { title: 'CPU%', dataIndex: 'cpu_pct', width: 100, render: (v: number | null) => <span style={{ color: pctColor(v), fontWeight: 600 }}>{v ?? '-'}{v != null ? '%' : ''}</span> },
            { title: '内存%', dataIndex: 'mem_pct', width: 100, render: (v: number | null) => <span style={{ color: pctColor(v), fontWeight: 600 }}>{v ?? '-'}{v != null ? '%' : ''}</span> },
            { title: '可迁移 Pod 数', dataIndex: 'total_candidates', width: 120 },
          ]}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无热点节点（或 metrics-server 不可用）" /> }}
        />
        <div style={{ marginBottom: 8, fontWeight: 600 }}>候选 Pod（按 CPU 请求降序，前 20）</div>
        <Table
          rowKey={(r) => `${r.node}-${r.ns}-${r.name}`} size="small" pagination={false}
          dataSource={planCandidates}
          columns={[
            { title: 'Pod', render: (_, r) => `${r.ns}/${r.name}`, ellipsis: true },
            { title: '所属节点', dataIndex: 'node', width: 200, ellipsis: true },
            { title: '控制器', dataIndex: 'owner', width: 160, ellipsis: true },
            { title: 'CPU 请求', dataIndex: 'cpu', width: 120, render: (v: number) => `${v}m` },
          ]}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无可迁移 Pod" /> }}
        />
      </Modal>

      <Modal open={isoOpen} title={`隔离节点 · ${isoNode}`} width={560}
        onCancel={() => setIsoOpen(false)} onOk={doIsolate} okText="确认隔离" okButtonProps={{ danger: true }}>
        <Alert type="warning" showIcon style={{ marginBottom: 12 }}
          message="隔离 = 停止调度（cordon）+ 打标记，不驱逐已有 Pod；需要清空节点请用「驱逐」。" />
        <div style={{ marginBottom: 6 }}>原因（写入标签 <code>autohealops.io/isolate-reason</code>）：</div>
        <Input value={isoReason} onChange={(e) => setIsoReason(e.target.value)} placeholder="例如 磁盘告警-待清理" maxLength={63} />
        <Checkbox style={{ marginTop: 12 }} checked={isoTaint} onChange={(e) => setIsoTaint(e.target.checked)}>
          同时加 NoSchedule 污点（autohealops.io/isolated=true）
        </Checkbox>
      </Modal>
    </div>
  )
}
