// 集群节点页：节点列表（状态/角色/CPU/内存/Pod/IP）+ 10s 轮询
// 操作收 ⋯：诊断 / 隔离·解除隔离 / 封锁·解封 / 驱逐（先预览再确认）/ 标签·污点
// 名称可点击展开该节点上的 Pod 列表（getClusterPods 按 node_name 过滤）
import { useEffect, useState, type Key } from 'react'
import { Card, Table, Button, Space, Tag, message, Modal, Input, Progress, Dropdown, Select, Checkbox, Alert, Divider } from 'antd'
import { ReloadOutlined, MoreOutlined } from '@ant-design/icons'
import {
  getNodes, cordonNode, uncordonNode, drainPreview, drainNode, diagnoseNode,
  setNodeLabels, setNodeTaints, isolateNode, unisolateNode, getClusterPods,
} from '../../services/api'
import type { K8sNode, NodeDiagnosis, NodeTaint, ClusterPod } from '../../services/api'

const pctColor = (v?: number | null) => (!v && v !== 0) ? '#999' : v >= 85 ? '#ff4d4f' : v >= 70 ? '#faad14' : '#52c41a'

const ISOLATE_LABEL = 'autohealops.io/isolated'
const isNodeIsolated = (n?: K8sNode) => !!(n?.labels && n.labels[ISOLATE_LABEL])
const canonLabels = (m: Record<string, string>) => JSON.stringify(Object.keys(m).sort().map((k) => [k, m[k]]))
const canonTaints = (ts: { key: string; value: string; effect: string }[]) =>
  JSON.stringify(ts.map((t) => [t.key, t.value, t.effect]).sort())

type LabelRow = { key: string; value: string }
type TaintRow = { key: string; value: string; effect: string }

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

  const load = () => {
    setLoading(true)
    getNodes(clusterId)
      .then((d) => setNodes(d || []))
      .catch(() => setNodes([]))
      .finally(() => setLoading(false))
  }

  // 首拉 + 10s 轮询
  useEffect(() => {
    load()
    const t = setInterval(load, 10000)
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

  const pctCell = (v?: number | null) => (
    <div style={{ minWidth: 96 }}>
      <Progress percent={v ?? 0} size="small" strokeColor={pctColor(v)} format={() => (v === null || v === undefined ? '-' : `${v}%`)} />
    </div>
  )

  const menuFor = (r: K8sNode) => {
    const isolated = isNodeIsolated(r)
    const items: { key: string; label: string; danger?: boolean }[] = [
      { key: 'diag', label: '节点诊断' },
      { key: 'meta', label: '标签/污点' },
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
            else if (key === 'meta') openMeta(r.name)
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
        extra={<Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>}>
        <Table
          rowKey="name"
          size="small"
          loading={loading}
          dataSource={nodes}
          pagination={{ pageSize: 20 }}
          scroll={{ x: 1200 }}
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
                    pagination={false}
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
            { title: '状态', dataIndex: 'ready', width: 90, render: (v: string) => <Tag color={v === 'Ready' ? 'green' : 'red'}>{v === 'Ready' ? 'Ready' : 'NotReady'}</Tag> },
            { title: '角色', width: 110, render: (_, r) => <Tag color={roleOf(r) === 'worker' ? 'default' : 'blue'}>{roleOf(r)}</Tag> },
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
      <Modal open={metaOpen} title={`编辑节点标签 / 污点 · ${metaNode}`} width={860}
        onCancel={() => setMetaOpen(false)} onOk={saveMeta} okText="保存" confirmLoading={metaSaving} destroyOnClose>
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
        <Divider style={{ margin: '16px 0 12px' }} />
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
      </Modal>

      {/* 隔离节点 */}
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
