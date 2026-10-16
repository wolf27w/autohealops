// 服务详情页（专项 D）：工作负载概览 + 副本增减 + 探针编辑 + 回滚/重启/重建 + Pods 列表（监控/日志/进容器/YAML）
import { useEffect, useRef, useState } from 'react'
import { Card, Descriptions, Button, Space, Table, Tag, InputNumber, Popconfirm, Modal, Form, Select, Input, message, Tabs, Drawer, Segmented, Empty, Dropdown, Switch, Tooltip, Divider, Collapse, Spin } from 'antd'
import { ArrowLeftOutlined, ReloadOutlined, DownOutlined, MinusOutlined, PlusOutlined, QuestionCircleOutlined, DownloadOutlined } from '@ant-design/icons'
import { useNavigate, useParams } from 'react-router-dom'
import { Line } from '@ant-design/charts'
import TerminalModal from '../components/TerminalModal'
import WorkloadYamlModal, { type WorkloadTarget } from '../components/WorkloadYamlModal'
import PodDetailDrawer from '../components/PodDetailDrawer'
import JvmDiagModal from '../components/JvmDiagModal'
import PodLogStreamModal from '../components/PodLogStreamModal'
import WorkloadMountsPanel from '../components/WorkloadMountsPanel'
import { getWorkloads, scaleWorkload, rolloutWorkload, getRolloutHistory, updateWorkloadProbe, recreateWorkload, updateWorkloadImage,
  getPodMetrics, getPodLogs, getPodYaml, applyPodYaml, execPod, getClusters, getClusterDetail, getWorkloadEvents, getPodContainers, listPodFiles, readPodFile, isolatePod, unisolatePod, getWorkloadLogs, getRegistryTags, getNodes, scheduleWorkload, getIsolatedPods, getWorkloadContainers, getClusterPods } from '../services/api'
import type { MetricPoint, PodTarget, Workload } from '../types'
import { podStatusColor, podReadyText } from '../utils/podStatus'
import type { AggLogPod, K8sNode } from '../services/api'

const KIND_LABEL: Record<string, string> = {
  deployment: '无状态 Deployment', statefulset: '有状态 StatefulSet',
  daemonset: '守护集 DaemonSet', cronjob: '定时任务 CronJob',
}

interface ServicePod {
  name: string
  namespace: string
  status?: string
  phase?: string
  ready?: boolean
  cpu_request?: string | number
  cpu_limit?: string | number
  memory_request?: string | number
  memory_limit?: string | number
  pod_ip?: string
  containers?: string[]
  init_containers?: string[]
  [key: string]: unknown
}

interface NsPods {
  namespace: string
  pods?: ServicePod[]
}

interface WorkloadEvent {
  pod: string
  summary?: string
  events?: string
}

export default function ServiceDetail() {
  const { id, kind = 'deployment', namespace = '', name = '' } = useParams()
  const navigate = useNavigate()
  const [clusterName, setClusterName] = useState('')
  const [workload, setWorkload] = useState<Workload | null>(null)
  const [replicas, setReplicas] = useState<number>(1)
  const [history, setHistory] = useState<number[]>([])
  const [probeModal, setProbeModal] = useState(false)
  const [probeForm] = Form.useForm()
  const [imageModal, setImageModal] = useState(false)
  const [imageForm] = Form.useForm()
  const [imgTags, setImgTags] = useState<string[]>([])
  const [imgTagLoading, setImgTagLoading] = useState(false)
  const [imgTagErr, setImgTagErr] = useState('')
  const [wlContainers, setWlContainers] = useState<{ name: string; image: string }[]>([])
  const [aggLogsOpen, setAggLogsOpen] = useState(false)
  const [aggLogs, setAggLogs] = useState<AggLogPod[]>([])
  const [aggLogsLoading, setAggLogsLoading] = useState(false)
  const [scheduleModal, setScheduleModal] = useState(false)
  const [scheduleNode, setScheduleNode] = useState<string | undefined>(undefined)
  const [scheduleNodes, setScheduleNodes] = useState<K8sNode[]>([])
  const [schedulePreview, setSchedulePreview] = useState<{ old_node_selector: Record<string, string>; new_node_selector: Record<string, string> } | null>(null)
  const [scheduleBusy, setScheduleBusy] = useState(false)
  const [metricsPod, setMetricsPod] = useState<PodTarget | null>(null)
  const [logPod, setLogPod] = useState<PodTarget | null>(null)
  const [execTarget, setExecTarget] = useState<PodTarget | null>(null)
  const [jvmPod, setJvmPod] = useState<PodTarget | null>(null)
  const [yamlPod, setYamlPod] = useState<PodTarget | null>(null)
  const [wlYaml, setWlYaml] = useState<WorkloadTarget | null>(null)
  const [detailPod, setDetailPod] = useState<PodTarget | null>(null)
  const [mountsOpen, setMountsOpen] = useState(false)
  const [mountTypes, setMountTypes] = useState<Array<'configMap' | 'secret' | 'pvc'>>(['configMap'])
  const [podTab, setPodTab] = useState('pods')
  const [pods, setPods] = useState<ServicePod[]>([])
  const [isolated, setIsolated] = useState<Set<string>>(new Set())
  const [liveWs, setLiveWs] = useState(false)
  const [events, setEvents] = useState<WorkloadEvent[]>([])
  const [eventsLoading, setEventsLoading] = useState(false)

  const clusterId = Number(id)

  const load = () => {
    getWorkloads(clusterId, namespace).then((d) => {
      const w = (d || []).find((x) => x.kind === kind && x.name === name)
      setWorkload(w || { kind, name, namespace, replicas: 0, ready_replicas: 0 })
      if (w) setReplicas(w.replicas)
    })
    getClusters().then((clusters) => {
      const c = clusters.find((x) => x.id === clusterId)
      if (c) setClusterName(c.name)
    })
    if (kind === 'deployment') {
      getRolloutHistory(clusterId, namespace, name).then((d) => setHistory((d || []) as number[]))
    }
  }

  useEffect(load, [id, kind, namespace, name])

  // Pods 列表：从集群详情的 pod 数据里过滤出该服务（deployment_name 前缀匹配）
  const loadPods = () => {
    // N3：以实时 Pod 列表为准（增删/创建过程即时反映），快照仅补资源 request/limit
    getClusterPods(clusterId, namespace).then((d) => {
      const live = (d?.pods || []).filter((p) => p.name === name || p.name.startsWith(name + '-'))
      getClusterDetail(clusterId).then((nsList) => {
        const snap: Record<string, Partial<ServicePod>> = {}
        for (const ns of (nsList || []) as NsPods[]) {
          if (ns.namespace !== namespace) continue
          for (const p of ns.pods || []) snap[p.name] = p
        }
        setPods(live.map((p) => {
          const s = snap[p.name] || {}
          return { ...p, status: p.status || p.phase || (p.ready ? 'Running' : 'Pending'),
            cpu_request: s.cpu_request, memory_request: s.memory_request,
            cpu_limit: s.cpu_limit, memory_limit: s.memory_limit }
        }))
      }).catch(() => setPods(live.map((p) => ({ ...p, status: p.status || p.phase || (p.ready ? 'Running' : 'Pending') }))))
    }).catch(() => setPods([]))
    getIsolatedPods(clusterId, namespace).then((dd) => setIsolated(new Set(dd?.pods || []))).catch(() => {})
  }
  useEffect(loadPods, [id, namespace, name])
  // N3：Pods 实时刷新（5s），滚动更新增删即时可见
  useEffect(() => {
    const t = setInterval(loadPods, 5000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, namespace, name])

  // R6 实时状态推送：WS 订阅命名空间 Pod/Deployment 变化；变化即刷新 Pod 列表（节流），副本数实时更新
  const loadPodsRef = useRef(loadPods)
  useEffect(() => { loadPodsRef.current = loadPods })
  useEffect(() => {
    if (!clusterId || !namespace || !name) return
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const token = localStorage.getItem('token') || ''
    const url = `${proto}//${window.location.host}/api/v1/ws/clusters/${clusterId}/namespaces/${namespace}/pods/status?token=${encodeURIComponent(token)}&name=${encodeURIComponent(name)}`
    let ws: WebSocket | null = null
    let closed = false
    let last = 0
    const trigger = () => { const now = Date.now(); if (now - last > 1500) { last = now; loadPodsRef.current() } }
    const openWs = () => {
      if (closed) return
      ws = new WebSocket(url)
      ws.onopen = () => setLiveWs(true)
      ws.onmessage = (ev) => {
        let j: { res?: string; name?: string; replicas?: number; readyReplicas?: number }
        try { j = JSON.parse(typeof ev.data === 'string' ? ev.data : '') } catch { return }
        if (j.res === 'deploy' && j.name === name) {
          setWorkload((w) => (w ? { ...w, replicas: j.replicas ?? w.replicas, ready_replicas: j.readyReplicas ?? w.ready_replicas } : w))
        }
        trigger()
      }
      ws.onclose = () => { ws = null; setLiveWs(false); if (!closed) window.setTimeout(openWs, 3000) }
      ws.onerror = () => { try { ws?.close() } catch { /* ignore */ } }
    }
    openWs()
    return () => { closed = true; try { ws?.close() } catch { /* ignore */ } }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clusterId, namespace, name])

  const handleScale = async () => {
    await scaleWorkload(clusterId, { kind, namespace, name, replicas })
    message.success(`已调整为 ${replicas} 副本`)
    load()
  }

  // 副本加/减（点一下即生效）
  const handleScaleDelta = async (delta: number) => {
    const cur = workload?.replicas ?? replicas ?? 0
    const next = Math.max(0, cur + delta)
    if (next === cur) return
    await scaleWorkload(clusterId, { kind, namespace, name, replicas: next })
    message.success(`副本数：${next}`)
    setReplicas(next)
    load()
  }

  // 事件（按 Pod 汇总）
  const loadEvents = () => {
    setEventsLoading(true)
    getWorkloadEvents(clusterId, { namespace, name, kind })
      .then((d) => setEvents((d as { pods?: WorkloadEvent[] }).pods || []))
      .finally(() => setEventsLoading(false))
  }
  useEffect(() => { if (podTab === 'events') loadEvents() }, [podTab, namespace, name, kind])

  const handleRollout = async (action: string) => {
    await rolloutWorkload(clusterId, { kind, namespace, name, action })
    message.success(action === 'restart' ? '已触发滚动重启' : '已触发回滚')
    load()
  }

  const handleRecreate = async () => {
    await recreateWorkload(clusterId, { kind, namespace, name })
    message.success('已触发重新创建')
    load()
  }

  const openImageModal = async () => {
    imageForm.resetFields(); setImageModal(true); setImgTags([]); setImgTagErr(''); setWlContainers([])
    try {
      const d = await getWorkloadContainers(clusterId, kind, namespace, name)
      const cs = d?.containers || []
      setWlContainers(cs)
      const first = cs[0]   // spec 第一个 = 服务主容器（sidecar 在后）
      if (first) {
        imageForm.setFieldsValue({ container: first.name, image: first.image })
        await loadImgTags()   // 自动列出当前镜像的 tag
      }
    } catch { /* ignore */ }
  }
  const handleImageUpdate = async () => {
    const v = await imageForm.validateFields()
    await updateWorkloadImage(clusterId, kind, namespace, name, { container: v.container, image: v.image })
    message.success('镜像已更新（滚动生效）')
    setImageModal(false)
    load()
  }
  const loadImgTags = async () => {
    const img = String(imageForm.getFieldValue('image') || '').trim()
    if (!img) { message.warning('请先填写镜像（如 harbor.efreight.cn/proj/app:tag）'); return }
    setImgTagLoading(true); setImgTagErr(''); setImgTags([])
    try {
      const d = await getRegistryTags(img, 100, { cluster_id: clusterId, namespace, kind, name })
      const tags = d?.tags || []
      setImgTags(tags)
      if (tags.length === 0) setImgTagErr('未列到标签（仓库可能私有需配凭据，或网络不通）')
    } catch (e) { setImgTagErr(String((e as Error)?.message || e)) }
    finally { setImgTagLoading(false) }
  }

  const loadAggLogs = async () => {
    setAggLogsOpen(true); setAggLogsLoading(true); setAggLogs([])
    getWorkloadLogs(clusterId, namespace, kind, name, { tail: 500 })
      .then((d) => setAggLogs(d.pods || []))
      .catch(() => {})
      .finally(() => setAggLogsLoading(false))
  }

  const handleProbeSubmit = async () => {
    const v = await probeForm.validateFields()
    await updateWorkloadProbe(clusterId, {
      namespace, name,
      container: v.container || '',
      probe_type: v.probe_type,
      probe_kind: v.probe_kind,
      path: v.path, port: v.port || 0, command: v.command ? v.command.split(/\s+/) : [],
      initial_delay_seconds: v.initial_delay_seconds || 0,
      period_seconds: v.period_seconds || 0,
      timeout_seconds: v.timeout_seconds || 0,
      failure_threshold: v.failure_threshold || 0,
      success_threshold: v.success_threshold || 0,
    })
    message.success('探针已更新')
    setProbeModal(false)
    load()
  }

  const openProbe = () => {
    probeForm.resetFields()
    probeForm.setFieldsValue({ probe_type: 'readiness', probe_kind: 'httpGet', port: 8080 })
    setProbeModal(true)
  }

  // A4：调度到指定节点
  const openSchedule = async () => {
    setScheduleNode(undefined); setSchedulePreview(null); setScheduleModal(true)
    try { setScheduleNodes((await getNodes(clusterId)) || []) } catch { setScheduleNodes([]) }
  }
  const handleSchedulePreview = async () => {
    setScheduleBusy(true)
    try {
      const d = await scheduleWorkload(clusterId, kind, namespace, name, { node: scheduleNode || '', preview: true })
      setSchedulePreview(d)
    } catch { /* 拦截器已提示 */ } finally { setScheduleBusy(false) }
  }
  const handleScheduleApply = async () => {
    setScheduleBusy(true)
    try {
      await scheduleWorkload(clusterId, kind, namespace, name, { node: scheduleNode || '' })
      message.success(scheduleNode ? `已调度到节点 ${scheduleNode}（滚动重建中）` : '已清除节点调度（恢复默认调度）')
      setScheduleModal(false)
      load()
    } catch { /* ignore */ } finally { setScheduleBusy(false) }
  }

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(`/clusters/${id}`)}>返回</Button>
        <span style={{ fontSize: 16, fontWeight: 'bold' }}>{clusterName || `集群 ${id}`} / {KIND_LABEL[kind] || kind} / {name}</span>
        <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
      </Space>

      <Card size="small" style={{ marginBottom: 16 }}>
        <Descriptions size="small" column={4}>
          <Descriptions.Item label="类型">{KIND_LABEL[kind] || kind}</Descriptions.Item>
          <Descriptions.Item label="命名空间">{namespace}</Descriptions.Item>
          <Descriptions.Item label="副本数">
            {workload?.replicas ?? '-'}（就绪 {workload?.ready_replicas ?? '-'}）
            <Tag color={liveWs ? 'green' : 'default'} style={{ marginLeft: 8 }}>{liveWs ? '实时' : '离线'}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="副本增减">
            <Space>
              <Button size="small" icon={<MinusOutlined />} onClick={() => handleScaleDelta(-1)} disabled={(workload?.replicas ?? 0) <= 0} />
              <span style={{ minWidth: 28, display: 'inline-block', textAlign: 'center', fontWeight: 600 }}>{workload?.replicas ?? replicas}</span>
              <Button size="small" icon={<PlusOutlined />} onClick={() => handleScaleDelta(1)} />
            </Space>
          </Descriptions.Item>
        </Descriptions>
        <Space style={{ marginTop: 8 }}>
          <Dropdown
            menu={{
              items: [
                { key: 'restart', label: '滚动重启' },
                { key: 'cm', label: '配置字典' },
                { key: 'secret', label: '配置密钥' },
                { key: 'pvc', label: '配置 PVC' },
                { key: 'yaml', label: '编辑 YAML（整个服务）' },
                { key: 'image', label: '更新镜像' },
                { key: 'schedule', label: '调度到节点' },
                { key: 'logs', label: '聚合日志' },
                ...(kind === 'deployment' ? [
                  { key: 'undo', label: '回滚上一版本' },
                  { key: 'probe', label: '编辑探针' },
                ] : []),
                { type: 'divider' as const },
                { key: 'recreate', label: '重新创建', danger: true },
              ],
              onClick: ({ key }) => {
                if (key === 'restart') handleRollout('restart')
                else if (key === 'yaml') setWlYaml({ clusterId, kind, namespace, name })
                else if (key === 'cm') { setMountTypes(['configMap']); setMountsOpen(true) }
                else if (key === 'secret') { setMountTypes(['secret']); setMountsOpen(true) }
                else if (key === 'pvc') { setMountTypes(['pvc']); setMountsOpen(true) }
                else if (key === 'image') openImageModal()
                else if (key === 'schedule') openSchedule()
                else if (key === 'logs') loadAggLogs()
                else if (key === 'undo') { Modal.confirm({ title: '回滚到上一版本？', onOk: () => handleRollout('undo') }) }
                else if (key === 'probe') openProbe()
                else if (key === 'recreate') { Modal.confirm({ title: '确认重新创建该服务？', okButtonProps: { danger: true }, onOk: () => handleRecreate() }) }
              },
            }}
          >
            <Button size="small">操作 <DownOutlined /></Button>
          </Dropdown>
          {history.length > 0 && (
            <span style={{ fontSize: 12, color: '#999' }}>历史版本：{history.join(', ')}</span>
          )}
        </Space>
      </Card>

      <Card size="small">
        <Tabs activeKey={podTab} onChange={setPodTab} items={[
          { key: 'pods', label: 'Pods', children: (
            <Table rowKey="name" size="small" dataSource={pods} pagination={false}
              columns={[
                { title: '名称', dataIndex: 'name', render: (v, r) => <a onClick={() => setDetailPod({ clusterId, namespace: r.namespace, name: r.name })}>{v}</a> },
                { title: '状态', dataIndex: 'status', width: 150, render: (s: string) => <Tag color={podStatusColor(s)}>{s}</Tag> },
                { title: '就绪', key: 'ready', width: 70, render: (_: unknown, r: ServicePod) => podReadyText(r.ready_containers, r.total_containers) },
                { title: '重启', dataIndex: 'restart_count', width: 70, render: (v: unknown) => (v == null ? '-' : String(v)) },
                { title: '节点', dataIndex: 'node_name', width: 160, render: (v: unknown) => (v ? String(v) : '-') },
                { title: 'Pod IP', dataIndex: 'pod_ip', width: 130, render: (v: unknown) => (v ? String(v) : '-') },
                { title: '创建时间', dataIndex: 'created_at', width: 165, render: (v: unknown) => (v ? String(v).slice(0, 19).replace('T', ' ') : '-') },
                { title: '资源（最小~最大）', render: (_, r) => {
                  const parts: string[] = []
                  if (r.cpu_request || r.cpu_limit) parts.push(`CPU ${r.cpu_request || '-'}~${r.cpu_limit || '-'}`)
                  if (r.memory_request || r.memory_limit) parts.push(`内存 ${r.memory_request || '-'}~${r.memory_limit || '-'}`)
                  return parts.join(' ｜ ') || '-'
                } },
                { title: '操作', render: (_, r) => (
                  <Space size="small">
                    <Button size="small" type="link" onClick={() => setMetricsPod({ ...r, clusterId })}>监控</Button>
                    <Button size="small" type="link" onClick={() => setLogPod({ ...r, clusterId })}>日志</Button>
                    <Button size="small" type="link" onClick={() => setExecTarget({ ...r, clusterId })}>进容器</Button>
                    <Button size="small" type="link" onClick={() => setJvmPod({ ...r, clusterId })}>JVM诊断</Button>
                    {isolated.has(r.name) ? (
                      <Popconfirm title="取消隔离？（还原标签，回到 Service 流量）" onConfirm={async () => { await unisolatePod(clusterId, r.namespace, r.name); message.success('已取消隔离'); loadPods() }}>
                        <Button size="small" type="link">取消隔离</Button>
                      </Popconfirm>
                    ) : (
                      <Popconfirm title="隔离该 Pod？（app / app.kubernetes.io/name 等标签改为 xxx-ALERT，脱离 Service 流量）" onConfirm={async () => { await isolatePod(clusterId, r.namespace, r.name); message.success('已隔离'); loadPods() }}>
                        <Button size="small" type="link">隔离</Button>
                      </Popconfirm>
                    )}
                    <Button size="small" type="link" onClick={() => setYamlPod({ ...r, clusterId })}>YAML</Button>
                  </Space>
                ) },
              ]}
              locale={{ emptyText: <Empty description="暂无 Pod（可能未采集或名称不匹配）" /> }} />
          ) },
          { key: 'events', label: '事件', children: (
            <div>
              <Button size="small" style={{ marginBottom: 8 }} onClick={loadEvents} loading={eventsLoading}>刷新事件</Button>
              {events.length === 0 ? <Empty description="暂无事件" /> : (
                <Collapse
                  // 默认只展开有 Warning 的 Pod，其余收起，一眼分清哪个 Pod 有事
                  defaultActiveKey={events.filter((e) => (e.events || '').includes('Warning')).map((e) => e.pod)}
                  items={events.map((e) => {
                    const ev = e.events || ''
                    const hasWarn = ev.includes('Warning')
                    const noEvents = !ev || ev.includes('（无事件）')
                    return {
                      key: e.pod,
                      label: (
                        <Space size={8} wrap>
                          <span style={{ fontWeight: 600 }}>{e.pod}</span>
                          <Tag color={hasWarn ? 'red' : noEvents ? 'default' : 'blue'}>
                            {hasWarn ? 'Warning' : noEvents ? '无事件' : 'Normal'}
                          </Tag>
                          <span style={{ color: '#999', fontSize: 12 }}>{e.summary}</span>
                        </Space>
                      ),
                      children: (
                        <pre style={{ maxHeight: 260, overflow: 'auto', background: '#fafafa', padding: 8, fontSize: 12, whiteSpace: 'pre-wrap', margin: 0 }}>{ev}</pre>
                      ),
                    }
                  })}
                />
              )}
            </div>
          ) },
          { key: 'mounts', label: '配置与存储', children: (
            <WorkloadMountsPanel clusterId={clusterId} kind={kind} namespace={namespace} name={name} />
          ) },
        ]} />
      </Card>

      <PodMetricsDrawer pod={metricsPod} onClose={() => setMetricsPod(null)} />
      <PodLogsDrawer pod={logPod} onClose={() => setLogPod(null)} />
      <TerminalModal pod={execTarget} onClose={() => setExecTarget(null)} />
      <JvmDiagModal pod={jvmPod} deployment={name} onClose={() => setJvmPod(null)} />
      <PodYamlModal pod={yamlPod} onClose={() => setYamlPod(null)} />
      <WorkloadYamlModal target={wlYaml} onClose={() => setWlYaml(null)} />
      <Modal title={`${mountTypes.map((t) => ({ configMap: '配置字典', secret: '配置密钥', pvc: 'PVC' } as const)[t]).join(' / ')}：${namespace}/${name}`}
        open={mountsOpen} onCancel={() => setMountsOpen(false)} footer={null} width={960} destroyOnClose>
        <WorkloadMountsPanel clusterId={clusterId} kind={kind} namespace={namespace} name={name} types={mountTypes} />
      </Modal>
      <PodDetailDrawer pod={detailPod} onClose={() => setDetailPod(null)} />

      <Modal title={`更新镜像：${name}`} open={imageModal} onOk={handleImageUpdate} onCancel={() => setImageModal(false)} width={520}>
        <Form form={imageForm} layout="vertical">
          <Form.Item name="container" label="容器（默认主容器，非 sidecar）">
            <Select placeholder="主容器" options={wlContainers.map((c) => ({ label: c.name, value: c.name }))}
              onChange={(v) => { const c = wlContainers.find((x) => x.name === v); if (c) { imageForm.setFieldsValue({ image: c.image }); setImgTags([]); setImgTagErr('') } }} />
          </Form.Item>
          <Space.Compact style={{ width: '100%' }}>
            <Form.Item name="image" label="镜像" style={{ flex: 1, marginBottom: 0 }} rules={[{ required: true, message: '请输入镜像' }]}>
              <Input placeholder="harbor.efreight.cn/xinnuo/xxx:tag" />
            </Form.Item>
            <Button style={{ marginTop: 30 }} loading={imgTagLoading} onClick={loadImgTags}>列仓库标签</Button>
          </Space.Compact>
        </Form>
        {imgTagErr && <div style={{ color: '#cf1322', fontSize: 12, marginTop: 8 }}>{imgTagErr}</div>}
        {imgTags.length > 0 && (
          <div style={{ marginTop: 8 }}>
            <div style={{ fontSize: 12, color: '#888', marginBottom: 4 }}>选择标签→填入镜像（共 {imgTags.length} 个）：</div>
            <Select style={{ width: '100%' }} showSearch placeholder="选择标签"
              filterOption={(i, o) => String(o?.value || '').includes(i)}
              onChange={(t) => {
                const repo = String(imageForm.getFieldValue('image') || '').split(':')[0]
                if (repo) imageForm.setFieldsValue({ image: `${repo}:${t}` })
              }}
              options={imgTags.map((t) => ({ value: t, label: t }))} />
          </div>
        )}
      </Modal>
      <Modal title={`调度到节点：${name}`} open={scheduleModal} onCancel={() => setScheduleModal(false)}
        onOk={handleScheduleApply} okText={scheduleNode ? '确认执行（调度到该节点）' : '确认执行（清除调度，恢复默认）'}
        confirmLoading={scheduleBusy} okButtonProps={{ danger: true }} width={560}>
        <div style={{ color: '#8c8c8c', fontSize: 13, marginBottom: 10 }}>
          给该工作负载的 Pod 模板加 <code>nodeSelector[kubernetes.io/hostname]</code>，把 Pod 固定调度到指定节点；变更会触发<b>滚动重建</b>。留空并执行 = 清除调度选择器（恢复默认调度）。
        </div>
        <Select style={{ width: '100%' }} allowClear showSearch placeholder="选择节点（留空=清除）"
          value={scheduleNode} onChange={(v) => { setScheduleNode(v); setSchedulePreview(null) }}
          filterOption={(i, o) => String(o?.value || '').includes(i)}
          options={scheduleNodes.map((n) => ({ value: n.name, label: `${n.name}${n.schedulable ? '' : '（已封锁）'}` }))} />
        <Button style={{ marginTop: 12 }} loading={scheduleBusy} onClick={handleSchedulePreview}>预览变更（不改集群）</Button>
        {schedulePreview && (
          <div style={{ marginTop: 10, fontSize: 12 }}>
            <div>原 nodeSelector：<code>{JSON.stringify(schedulePreview.old_node_selector)}</code></div>
            <div>新 nodeSelector：<code>{JSON.stringify(schedulePreview.new_node_selector)}</code></div>
          </div>
        )}
      </Modal>
      <Modal open={aggLogsOpen} title={`聚合日志：${name}（${aggLogs.length} Pod）`} footer={null} onCancel={() => setAggLogsOpen(false)} width={900}>
        <Spin spinning={aggLogsLoading}>
          <Collapse size="small" items={aggLogs.map((p) => ({
            key: p.pod,
            label: <Space><b>{p.pod}</b><Tag color={p.phase === 'Running' ? 'green' : 'red'}>{p.phase}</Tag></Space>,
            children: p.success
              ? <pre style={{ maxHeight: 320, overflow: 'auto', fontSize: 12, whiteSpace: 'pre-wrap', margin: 0 }}>{p.logs || '(无日志)'}</pre>
              : <div style={{ color: '#cf1322' }}>{p.error}</div>,
          }))} />
        </Spin>
      </Modal>
      <Modal title={`编辑探针：${name}`} open={probeModal} onOk={handleProbeSubmit} onCancel={() => setProbeModal(false)} width={560}>
        <Form form={probeForm} layout="vertical">
          <Form.Item name="container" label="容器名（空=第一个）"><Input placeholder="如 app" /></Form.Item>
          <Form.Item name="probe_type" label="探针类型" rules={[{ required: true }]}>
            <Select options={[
              { value: 'liveness', label: '存活探针 Liveness' },
              { value: 'readiness', label: '就绪探针 Readiness' },
              { value: 'startup', label: '启动探针 Startup' },
            ]} />
          </Form.Item>
          <Form.Item name="probe_kind" label="探测方式" rules={[{ required: true }]}>
            <Select options={[
              { value: 'httpGet', label: 'HTTP GET' },
              { value: 'tcpSocket', label: 'TCP Socket' },
              { value: 'exec', label: 'Exec 命令' },
              { value: 'none', label: '移除探针' },
            ]} />
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(p, c) => p.probe_kind !== c.probe_kind}>
            {({ getFieldValue }) => {
              const k = getFieldValue('probe_kind')
              if (k === 'httpGet') return (
                <Space>
                  <Form.Item name="path" label="路径"><Input placeholder="/health" style={{ width: 180 }} /></Form.Item>
                  <Form.Item name="port" label="端口"><InputNumber min={1} max={65535} /></Form.Item>
                </Space>
              )
              if (k === 'tcpSocket') return (
                <Form.Item name="port" label="端口"><InputNumber min={1} max={65535} /></Form.Item>
              )
              if (k === 'exec') return (
                <Form.Item name="command" label="命令"><Input placeholder="如 cat /tmp/healthy" /></Form.Item>
              )
              return null
            }}
          </Form.Item>
          <Space>
            <Form.Item name="initial_delay_seconds" label="启动延迟(秒)"><InputNumber min={0} style={{ width: 120 }} /></Form.Item>
            <Form.Item name="period_seconds" label="周期(秒)"><InputNumber min={1} style={{ width: 120 }} /></Form.Item>
            <Form.Item name="timeout_seconds" label="超时(秒)"><InputNumber min={1} style={{ width: 120 }} /></Form.Item>
          </Space>
          <Space>
            <Form.Item name="failure_threshold" label="失败阈值"><InputNumber min={1} style={{ width: 120 }} /></Form.Item>
            <Form.Item name="success_threshold" label="成功阈值"><InputNumber min={1} style={{ width: 120 }} /></Form.Item>
          </Space>
        </Form>
      </Modal>
    </div>
  )
}

// Pod/容器指标监控抽屉：CPU / 内存 / 入站流量 / 出站流量
function PodMetricsDrawer({ pod, onClose }: { pod: PodTarget | null; onClose: () => void }) {
  const [range, setRange] = useState<string>('1h')
  const [points, setPoints] = useState<MetricPoint[]>([])
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    if (!pod) return
    setLoading(true)
    getPodMetrics(pod.clusterId, pod.namespace, pod.name, range).then((d) => setPoints((d as { points?: MetricPoint[] }).points || [])).finally(() => setLoading(false))
  }, [pod, range])
  if (!pod) return null
  const ts = (v: MetricPoint) => (v?.sampled_at || '').slice(11, 19)
  const cpuData = points.map((p) => ({ t: ts(p), v: Number(p.cpu_usage) }))
  const memData = points.map((p) => ({ t: ts(p), v: Number(p.memory_usage) / 1024 / 1024 }))

  // 入站/出站流量：network_rx/tx 是**累计字节**，换算成速率（B/s → 自动 KB/s、MB/s）
  const raw = points.map((p) => ({
    t: ts(p),
    ts: new Date(String(p.sampled_at || '').replace(' ', 'T')).getTime(),
    rx: Number(p.network_rx || 0),
    tx: Number(p.network_tx || 0),
  }))
  const rxRate: number[] = [], txRate: number[] = []
  raw.forEach((cur, i) => {
    if (i === 0) { rxRate.push(0); txRate.push(0); return }
    const dt = Math.max(1, (cur.ts - raw[i - 1].ts) / 1000)
    rxRate.push(Math.max(0, cur.rx - raw[i - 1].rx) / dt)  // 计数器回绕/重启 → 视为 0
    txRate.push(Math.max(0, cur.tx - raw[i - 1].tx) / dt)
  })
  const peak = Math.max(...rxRate, ...txRate, 0)
  const unit = peak >= 1024 * 1024 ? 'MB/s' : peak >= 1024 ? 'KB/s' : 'B/s'
  const div = unit === 'MB/s' ? 1024 * 1024 : unit === 'KB/s' ? 1024 : 1
  const rxData = raw.map((r, i) => ({ t: r.t, v: Number((rxRate[i] / div).toFixed(2)) }))
  const txData = raw.map((r, i) => ({ t: r.t, v: Number((txRate[i] / div).toFixed(2)) }))
  const last = raw[raw.length - 1], first = raw[0]
  const totalRx = last && first ? Math.max(0, last.rx - first.rx) : 0
  const totalTx = last && first ? Math.max(0, last.tx - first.tx) : 0
  const fmtBytes = (b: number) => b >= 1024 ** 3 ? `${(b / 1024 ** 3).toFixed(2)} GB`
    : b >= 1024 ** 2 ? `${(b / 1024 ** 2).toFixed(2)} MB` : `${(b / 1024).toFixed(1)} KB`

  const base = { data: [], xField: 't', yField: 'v', smooth: true, height: 180, animation: false }
  return (
    <Drawer title={`容器监控：${pod.name}`} open={!!pod} onClose={onClose} width={720}>
      <Space style={{ marginBottom: 12 }}>
        <Segmented value={range} onChange={(v) => setRange(v as string)} options={[
          { value: '1h', label: '1小时' }, { value: '24h', label: '24小时' }, { value: '72h', label: '3天' },
        ]} />
      </Space>
      {loading ? <Empty description="加载中..." /> : points.length === 0 ? <Empty description="暂无指标数据" /> : (
        <div>
          <div style={{ marginBottom: 16 }}><b>CPU（core）</b><Line {...base} data={cpuData} /></div>
          <div style={{ marginBottom: 16 }}><b>内存（MB）</b><Line {...base} data={memData} /></div>
          <div style={{ marginBottom: 4 }}>
            <b>入站流量（{unit}）</b>
            <span style={{ marginLeft: 8, fontSize: 12, color: '#8c8c8c' }}>区间累计 {fmtBytes(totalRx)}</span>
          </div>
          <Line {...base} data={rxData} scale={{ y: { nice: true } }} />
          <div style={{ margin: '16px 0 4px' }}>
            <b>出站流量（{unit}）</b>
            <span style={{ marginLeft: 8, fontSize: 12, color: '#8c8c8c' }}>区间累计 {fmtBytes(totalTx)}</span>
          </div>
          <Line {...base} data={txData} scale={{ y: { nice: true } }} />
          <div style={{ marginTop: 8, fontSize: 12, color: '#8c8c8c' }}>
            说明：流量为容器网卡收发速率（由累计字节差分换算，采样桶间隔 {range === '1h' ? '1 分钟' : range === '24h' ? '5 分钟' : '15 分钟'}）。
          </div>
        </div>
      )}
    </Drawer>
  )
}

function PodLogsDrawer({ pod, onClose }: { pod: PodTarget | null; onClose: () => void }) {
  const [logs, setLogs] = useState('')
  const [loading, setLoading] = useState(false)
  const [container, setContainer] = useState('')
  const [streamOpen, setStreamOpen] = useState(false)
  // 实时查看容器标准输出（/dev/stdout）：默认关闭，开启后每 2 秒拉取最新日志
  const [live, setLive] = useState(false)
  const [autoScroll, setAutoScroll] = useState(true)
  const preRef = useRef<HTMLPreElement>(null)
  const timerRef = useRef<number | null>(null)

  const [containers, setContainers] = useState<string[]>([])
  // 容器内日志文件下载
  const [dirPath, setDirPath] = useState('/var/log')
  const [filePath, setFilePath] = useState('')
  const [dirEntries, setDirEntries] = useState<Array<{ name: string; size: string | number; is_dir: boolean; raw: string }>>([])
  const [listing, setListing] = useState(false)
  const [downloading, setDownloading] = useState(false)

  const fmtSize = (b: number) => b >= 1024 * 1024 ? `${(b / 1024 / 1024).toFixed(2)} MB` : b >= 1024 ? `${(b / 1024).toFixed(1)} KB` : `${b} B`

  const saveText = (name: string, text: string) => {
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = name; a.click(); URL.revokeObjectURL(url)
  }

  const doListDir = async () => {
    if (!pod) return
    setListing(true)
    try {
      const d = await listPodFiles(pod.clusterId, pod.namespace, pod.name, dirPath, container) as unknown as {
        dir?: string; entries?: Array<{ name: string; size: string | number; is_dir: boolean; raw: string }>
      }
      setDirEntries(d?.entries || [])
      if (!(d?.entries || []).length) message.info('该目录下没有可列出的文件（或容器内无 ls 命令）')
    } catch { /* 拦截器已提示 */ } finally { setListing(false) }
  }

  const doDownloadFile = async () => {
    if (!pod) return
    if (!filePath.trim()) { message.warning('请填写容器内文件路径'); return }
    setDownloading(true)
    try {
      const d = await readPodFile(pod.clusterId, pod.namespace, pod.name, filePath.trim(), container) as unknown as {
        content?: string; filename?: string; size?: number; truncated?: boolean
      }
      saveText(d?.filename || 'download.log', d?.content || '')
      message.success(`已下载 ${d?.filename || ''}${d?.truncated ? `（文件 ${fmtSize(Number(d?.size) || 0)}，仅取末尾 5MB）` : ''}`)
    } catch { /* 拦截器已提示 */ } finally { setDownloading(false) }
  }

  const fetchLogs = (tail: number) => {
    if (!pod) return
    getPodLogs(pod.clusterId, pod.namespace, pod.name, container, tail)
      .then((d) => setLogs((d as { logs?: string }).logs || '(无日志)'))
      .catch(() => setLogs('日志获取失败'))
      .finally(() => setLoading(false))
  }

  const load = () => {
    if (!pod) return
    setLoading(true)
    fetchLogs(live ? 500 : 200)
  }

  useEffect(() => {
    setContainer(''); setLive(false); load()
    // 拉取容器列表（多容器时可直接切换；空=自动选主容器）
    if (pod) {
      getPodContainers(pod.clusterId, pod.namespace, pod.name)
        .then((d) => setContainers((d as unknown as { containers?: string[] }).containers || []))
        .catch(() => setContainers([]))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pod])

  // 实时模式：每 2 秒刷新一次（关闭时自动清理定时器）
  useEffect(() => {
    if (!pod || !live) return
    fetchLogs(500)
    timerRef.current = window.setInterval(() => fetchLogs(500), 2000)
    return () => {
      if (timerRef.current !== null) window.clearInterval(timerRef.current)
      timerRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pod, live, container])

  // 实时模式下自动滚动到底部
  useEffect(() => {
    const el = preRef.current
    if (el && live && autoScroll) el.scrollTop = el.scrollHeight
  }, [logs, live, autoScroll])

  if (!pod) return null
  return (
    <Drawer title={`容器日志：${pod.name}`} open={!!pod} onClose={() => { setLive(false); onClose() }} width={900}>
      <Space style={{ marginBottom: 12 }} wrap>
        <Select
          style={{ width: 220 }} value={container || undefined} allowClear placeholder="容器（空=自动选主容器）"
          onChange={(v) => setContainer((v as string) || '')}
          options={(Array.isArray(containers) ? containers : []).map((c) => ({ value: c, label: c }))}
          onDropdownVisibleChange={(open) => { if (open && containers.length === 0 && pod) getPodContainers(pod.clusterId, pod.namespace, pod.name).then((d) => setContainers((d as unknown as { containers?: string[] }).containers || [])).catch(() => {}) }}
        />
        <Button type="primary" onClick={load}>查询</Button>
        <Button onClick={() => setStreamOpen(true)}>WS 实时跟随</Button>
        <Space size={6}>
          <Switch checked={live} onChange={setLive} />
          <span style={{ fontSize: 13 }}>实时查看 stdout</span>
            <Tooltip title="开启后每 2 秒自动拉取容器标准输出（/dev/stdout）最新日志；默认关闭">
            <QuestionCircleOutlined style={{ color: '#999' }} />
          </Tooltip>
        </Space>
        {live && (
          <Space size={6}>
            <Switch size="small" checked={autoScroll} onChange={setAutoScroll} />
            <span style={{ fontSize: 12, color: '#888' }}>自动滚到底部</span>
          </Space>
        )}
      </Space>
      <div style={{ marginBottom: 8, fontSize: 12, color: live ? '#52c41a' : '#8c8c8c' }}>
        {live ? '● 实时中（每 2 秒刷新，退出抽屉自动停止）' : '○ 实时已关闭（点「查询」看当前日志快照）'}
      </div>
      <pre ref={preRef} style={{ maxHeight: '52vh', overflow: 'auto', background: '#f5f5f5', padding: 12, fontSize: 12, whiteSpace: 'pre-wrap' }}>{loading && !logs ? '加载中...' : logs}</pre>
      <PodLogStreamModal
        pod={streamOpen && pod ? { clusterId: pod.clusterId, namespace: pod.namespace, name: pod.name } : null}
        onClose={() => setStreamOpen(false)} />

      {/* ===== 容器内日志文件（只读下载） ===== */}
      <Divider orientation="left" style={{ marginTop: 16 }}>容器内日志文件（只读下载）</Divider>
      <Space wrap style={{ marginBottom: 8 }}>
        <Input value={dirPath} onChange={(e) => setDirPath(e.target.value)} placeholder="/var/log"
          style={{ width: 220 }} />
        <Button onClick={doListDir} loading={listing}>列目录</Button>
        {dirEntries.length > 0 && (
          <Select
            style={{ minWidth: 260 }} placeholder="选择目录下的文件"
            value={filePath || undefined}
            onChange={(v: string) => setFilePath(v)}
            options={dirEntries.filter((x) => !x.is_dir).map((x) => ({
              value: `${dirPath.replace(/\/$/, '')}/${x.name}`, label: `${x.name}（${fmtSize(Number(x.size) || 0)}）`,
            }))}
          />
        )}
      </Space>
      <Space wrap>
        <Input value={filePath} onChange={(e) => setFilePath(e.target.value)}
          placeholder="/var/log/app.log（容器内绝对路径）" style={{ width: 320 }} />
        <Button type="primary" icon={<DownloadOutlined />} onClick={doDownloadFile} loading={downloading}>下载文件</Button>
        <Button icon={<DownloadOutlined />} onClick={() => saveText(`stdout-${pod.name}.log`, logs)}>下载当前 stdout</Button>
      </Space>
      <div style={{ marginTop: 6, fontSize: 12, color: '#8c8c8c' }}>
        只读下载（执行器白名单命令 cat/ls/tail/wc，不会修改容器）；单次最多 5MB，超出时取文件<strong>末尾</strong> 5MB。
        容器内无 shell 工具（如 distroless 镜像）时无法读取。
      </div>
    </Drawer>
  )
}

function PodYamlModal({ pod, onClose }: { pod: PodTarget | null; onClose: () => void }) {
  const [yaml, setYaml] = useState('')
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    if (!pod) return
    setLoading(true)
    getPodYaml(pod.clusterId, pod.namespace, pod.name).then((d) => setYaml((d as { yaml?: string }).yaml || '')).catch(() => message.error('YAML 获取失败')).finally(() => setLoading(false))
  }, [pod])
  const apply = async () => {
    if (!yaml.trim()) return
    setLoading(true)
    try { await applyPodYaml(pod!.clusterId, pod!.namespace, pod!.name, yaml); message.success('YAML 已应用'); onClose() } catch { message.error('YAML 应用失败') } finally { setLoading(false) }
  }
  if (!pod) return null
  return (
    <Modal title={`编辑 Pod YAML：${pod.name}`} open={!!pod} onCancel={onClose} onOk={apply} okText="应用" confirmLoading={loading} width={900}>
      <Input.TextArea value={yaml} onChange={(e) => setYaml(e.target.value)} rows={22} style={{ fontFamily: 'monospace', fontSize: 12 }} />
    </Modal>
  )
}
