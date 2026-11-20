// 工作负载详情页（整页）：概览 / 元数据 / 环境变量 / 实时事件 / 历史事件 / 监控
// 路由形态：/clusters/:id/workloads/:kind/:namespace/:name（路由由父会话接线，此处用 useParams）
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Breadcrumb, Card, Descriptions, Button, Space, Table, Tag, Modal, InputNumber, Input, Empty, Dropdown, Tabs, Select, message,
} from 'antd'
import { ReloadOutlined, MoreOutlined, DownOutlined } from '@ant-design/icons'
import { useNavigate, useParams } from 'react-router-dom'
import WorkloadYamlModal, { type WorkloadTarget } from '../../components/WorkloadYamlModal'
import WorkloadMetricsDrawer from '../../components/WorkloadMetricsDrawer'
import WorkloadLogsModal from '../../components/cluster/WorkloadLogsModal'
import PodLogStreamModal from '../../components/PodLogStreamModal'
import TerminalModal from '../../components/TerminalModal'
import PodEventsModal from '../../components/PodEventsModal'
import JvmDiagModal from '../../components/JvmDiagModal'
import WorkloadMountsPanel from '../../components/WorkloadMountsPanel'
import WorkloadCreateModal, { type WorkloadCreateTarget } from '../../components/WorkloadCreateModal'
import { parseYaml, asMap, asArr, asStr, yamlToValues, type YamlNode, type WorkloadValues, type VolumeMountValue, type VolumeType } from '../../components/workloadYaml'
import MonitorPanel from '../../components/cluster/MonitorPanel'
import HpaPanel from '../../components/cluster/HpaPanel'
import {
  getWorkloads, getClusters, getClusterDetail, getClusterPods, getWorkloadContainers,
  getWorkloadYaml, getWorkloadEventsRealtime, getWorkloadEventHistory, scaleWorkload, rolloutWorkload, deleteWorkload,
  getPodYaml, applyPodYaml, isolatePod, unisolatePod, getIsolatedPods,
} from '../../services/api'
import type { ClusterPod, WorkloadEventRow, WorkloadRealtimeEventRow } from '../../services/api'
import type { PodTarget, Workload } from '../../types'
import { podStatusColor, podReadyText } from '../../utils/podStatus'

// 卷类型 → Tag 颜色（元数据 Tab 的「挂载的卷 / 挂载的配置」用）
const VOL_TYPE_COLOR: Record<string, string> = {
  configMap: 'blue', secret: 'purple', pvc: 'green', emptyDir: 'default',
  hostPath: 'orange', nfs: 'cyan', projected: 'geekblue', csi: 'magenta', other: 'default',
}

const KIND_SHORT: Record<string, string> = {
  deployment: '无状态', statefulset: '有状态', daemonset: '守护进程', cronjob: '定时任务',
}

interface WorkloadMeta {
  labels: Record<string, string>
  annotations: Record<string, string>
  created: string
  strategy: string
  desired: number | null
  updated: number | null
  ready: number | null
  available: number | null
  images: string[]
}

// 实时事件行（后端按 Pod 取 k8s Event，同 Type+Reason+Message 聚合）
type RealtimeEventRow = WorkloadRealtimeEventRow

function strMap(n: YamlNode): Record<string, string> {
  const m = asMap(n)
  const out: Record<string, string> = {}
  for (const k of Object.keys(m)) out[k] = asStr(m[k])
  return out
}

// 解析出的 YAML → 向导表单值（编辑设置回填；仅覆盖向导支持的字段）

export default function WorkloadDetail() {
  const { id, kind = 'deployment', namespace = '', name = '' } = useParams()
  const navigate = useNavigate()
  const clusterId = Number(id)

  const [clusterName, setClusterName] = useState('')
  const [workload, setWorkload] = useState<Workload | null>(null)
  const [updateTime, setUpdateTime] = useState('')
  const [pods, setPods] = useState<ClusterPod[]>([])
  const [containers, setContainers] = useState<{ name: string; image: string }[]>([])
  const [yaml, setYaml] = useState('')
  const [events, setEvents] = useState<WorkloadEventRow[]>([])
  const [eventsLoading, setEventsLoading] = useState(false)
  const [realtimeEvents, setRealtimeEvents] = useState<RealtimeEventRow[]>([])
  const [realtimeLoading, setRealtimeLoading] = useState(false)
  const [historyHours, setHistoryHours] = useState(24)

  // 弹窗 / 抽屉
  const [wlYaml, setWlYaml] = useState<WorkloadTarget | null>(null)
  const [wlEdit, setWlEdit] = useState<WorkloadCreateTarget | null>(null)
  const [scaleOpen, setScaleOpen] = useState(false)
  const [scaleVal, setScaleVal] = useState<number>(1)
  const [metricsOpen, setMetricsOpen] = useState(false)
  const [logPod, setLogPod] = useState<PodTarget | null>(null)
  const [aggLogsOpen, setAggLogsOpen] = useState(false)
  const [execTarget, setExecTarget] = useState<PodTarget | null>(null)
  const [eventsPod, setEventsPod] = useState<PodTarget | null>(null)
  const [jvmPod, setJvmPod] = useState<PodTarget | null>(null)
  const [yamlPod, setYamlPod] = useState<PodTarget | null>(null)
  const [isolated, setIsolated] = useState<Set<string>>(new Set())

  const scalable = kind === 'deployment' || kind === 'statefulset'
  const restartable = kind === 'deployment' || kind === 'statefulset' || kind === 'daemonset'

  const meta: WorkloadMeta = useMemo(() => {
    const root = asMap(parseYaml(yaml))
    const metadata = asMap(root.metadata)
    const spec = asMap(root.spec)
    const status = asMap(root.status)
    const tplSpec = asMap(asMap(spec.template).spec)
    const images = asArr(tplSpec.containers).map((c) => asStr(asMap(c).image)).filter(Boolean)
    return {
      labels: strMap(metadata.labels),
      annotations: strMap(metadata.annotations),
      created: asStr(metadata.creationTimestamp),
      strategy: asStr(asMap(spec.strategy).type) || asStr(asMap(spec.updateStrategy).type),
      desired: spec.replicas != null ? Number(spec.replicas) : null,
      updated: status.updatedReplicas != null ? Number(status.updatedReplicas) : null,
      ready: status.readyReplicas != null ? Number(status.readyReplicas) : null,
      available: status.availableReplicas != null ? Number(status.availableReplicas) : null,
      images,
    }
  }, [yaml])

  const editValues = useMemo<Partial<WorkloadValues> | undefined>(() => {
    if (!yaml) return undefined
    return yamlToValues(parseYaml(yaml), kind)
  }, [yaml, kind])

  const load = () => {
    getWorkloads(clusterId, namespace).then((d) => {
      const w = (d || []).find((x) => x.kind === kind && x.name === name)
      setWorkload(w || { kind, name, namespace, replicas: 0, ready_replicas: 0 })
    }).catch(() => setWorkload({ kind, name, namespace, replicas: 0, ready_replicas: 0 }))
    getClusters().then((clusters) => {
      const c = clusters.find((x) => x.id === clusterId)
      if (c) setClusterName(c.name)
    }).catch(() => {})
    // 更新时间：来自集群详情快照（资源列表里带 update_time）
    getClusterDetail(clusterId).then((nsList) => {
      for (const ns of (nsList || []) as Array<{
        namespace: string
        deployments?: { name: string; update_time?: string }[]
        statefulsets?: { name: string; update_time?: string }[]
        daemonsets?: { name: string; update_time?: string }[]
        cronjobs?: { name: string; update_time?: string }[]
      }>) {
        if (ns.namespace !== namespace) continue
        const arr = kind === 'deployment' ? ns.deployments
          : kind === 'statefulset' ? ns.statefulsets
            : kind === 'daemonset' ? ns.daemonsets : ns.cronjobs
        const w = (arr || []).find((x) => x.name === name)
        if (w) { setUpdateTime(w.update_time || ''); break }
      }
    }).catch(() => {})
    // 容器（镜像）
    getWorkloadContainers(clusterId, kind, namespace, name)
      .then((d) => setContainers(d?.containers || []))
      .catch(() => setContainers([]))
    // YAML（元数据 / 策略 / 资源状态）
    getWorkloadYaml(clusterId, kind, namespace, name)
      .then((d) => setYaml((d as { yaml?: string }).yaml || ''))
      .catch(() => setYaml(''))
  }

  useEffect(load, [id, kind, namespace, name])

  const loadPods = () => {
    getClusterPods(clusterId, namespace).then((d) => {
      const live = (d?.pods || []).filter((p) => p.name === name || p.name.startsWith(name + '-'))
      setPods(live)
    }).catch(() => setPods([]))
  }
  useEffect(loadPods, [id, namespace, name])

  // 加载当前命名空间下「已隔离 Pod」集合（与容器组页 PodsList 口径一致）
  useEffect(() => {
    getIsolatedPods(clusterId, namespace)
      .then((d) => setIsolated(new Set(((d as { pods?: string[] }).pods || []).map((n) => `${namespace}-${n}`))))
      .catch(() => setIsolated(new Set()))
  }, [clusterId, namespace])

  const loadEvents = (hours = historyHours) => {
    setEventsLoading(true)
    getWorkloadEventHistory(clusterId, kind, namespace, name, hours)
      .then((d) => setEvents(d?.events || []))
      .catch(() => setEvents([]))
      .finally(() => setEventsLoading(false))
  }

  // 实时事件：后端按 Pod 汇总 k8s Event（结构化 + 次数），进入 Tab 时自动加载一次，不做轮询
  const loadRealtimeEvents = () => {
    setRealtimeLoading(true)
    getWorkloadEventsRealtime(clusterId, { namespace, name, kind })
      .then((d) => {
        const evs = (d as { events?: RealtimeEventRow[] }).events || []
        setRealtimeEvents(evs)
      })
      .catch(() => setRealtimeEvents([]))
      .finally(() => setRealtimeLoading(false))
  }

  const desired = meta.desired ?? workload?.replicas ?? 0
  const readyReplicas = meta.ready ?? workload?.ready_replicas ?? 0
  const status = desired === 0 ? '已停止' : readyReplicas >= desired ? '运行中' : '更新中'
  const statusColor = { 运行中: 'green', 更新中: 'orange', 已停止: 'default' }[status]

  // —— 自动刷新：滚动更新中 3s、稳定 5s；页面隐藏时跳过（只更新数据，不重建视图/不触发提示）——
  const loadRef = useRef(load)
  const loadPodsRef = useRef(loadPods)
  const desiredRef = useRef(desired)
  const readyRef = useRef(readyReplicas)
  loadRef.current = load
  loadPodsRef.current = loadPods
  desiredRef.current = desired
  readyRef.current = readyReplicas

  useEffect(() => {
    let stopped = false
    let timer: number
    const tick = () => {
      if (stopped) return
      if (!document.hidden) {
        loadRef.current()
        loadPodsRef.current()
      }
      const d = desiredRef.current
      const r = readyRef.current
      const updating = d > 0 && r < d
      timer = window.setTimeout(tick, updating ? 3000 : 5000)
    }
    timer = window.setTimeout(tick, 5000)
    return () => { stopped = true; window.clearTimeout(timer) }
  }, [id, kind, namespace, name])

  const fmtTime = (v?: string) => (v ? String(v).slice(0, 19).replace('T', ' ') : '-')

  const handleScale = async () => {
    await scaleWorkload(clusterId, { kind, namespace, name, replicas: scaleVal })
    setScaleOpen(false)
    load()
  }

  const handleRollout = async () => {
    await rolloutWorkload(clusterId, { kind, namespace, name, action: 'restart' })
    load()
  }

  const handleDelete = () => {
    Modal.confirm({
      title: `删除 ${KIND_SHORT[kind] || kind} ${namespace}/${name}？`,
      content: '删除后该工作负载的全部 Pod 将被回收，请谨慎操作。',
      okButtonProps: { danger: true },
      onOk: async () => { await deleteWorkload(clusterId, kind, namespace, name); navigate(`/clusters/${id}`) },
    })
  }

  // —— Pod 行操作（与容器组页 PodsList 对齐）——
  const confirmDeletePod = (r: ClusterPod) => {
    Modal.confirm({
      title: `删除容器组 ${r.namespace}/${r.name}？`,
      content: 'Pod 被控制器托管时会自动重建；裸 Pod 删除后不会恢复。',
      okText: '确认删除',
      okButtonProps: { danger: true },
      onOk: () => deleteWorkload(clusterId, 'pod', r.namespace, r.name)
        .then(() => { message.success('已删除'); loadPods() })
        .catch(() => message.error('删除失败')),
    })
  }

  const handleIsolate = (p: ClusterPod) => {
    Modal.confirm({
      title: `隔离容器组 ${p.namespace}/${p.name}？`,
      content: '隔离后该 Pod 会从 Service 端点摘除（不再接收新流量），请确认。',
      okText: '隔离',
      onOk: async () => {
        try {
          await isolatePod(clusterId, p.namespace, p.name)
          message.success('已隔离')
          setIsolated((prev) => new Set(prev).add(`${p.namespace}-${p.name}`))
        } catch { message.error('隔离失败') }
      },
    })
  }

  const handleUnisolate = async (p: ClusterPod) => {
    try {
      await unisolatePod(clusterId, p.namespace, p.name)
      message.success('已取消隔离')
      setIsolated((prev) => { const n = new Set(prev); n.delete(`${p.namespace}-${p.name}`); return n })
    } catch { message.error('取消隔离失败') }
  }

  const kvRows = (m: Record<string, string>) => Object.entries(m).map(([k, v]) => ({ k, v }))

  const podColumns = [
    { title: '名称', dataIndex: 'name', render: (v: string) => <span style={{ fontWeight: 600 }}>{v}</span> },
    { title: '状态', dataIndex: 'status', width: 150, render: (_: unknown, r: ClusterPod) => <Tag color={podStatusColor(r.status || r.phase)}>{r.status || r.phase || '-'}</Tag> },
    { title: '就绪', key: 'ready', width: 70, render: (_: unknown, r: ClusterPod) => podReadyText(r.ready_containers, r.total_containers) },
    { title: '重启', dataIndex: 'restart_count', width: 70, render: (v: unknown) => (v == null ? '-' : String(v)) },
    { title: '节点', dataIndex: 'node_name', width: 160, render: (v: unknown) => (v ? String(v) : '-') },
    { title: '创建时间', dataIndex: 'created_at', width: 165, render: (v: unknown) => fmtTime(v as string) },
    {
      title: '操作', width: 100, render: (_: unknown, r: ClusterPod) => {
        const key = `${r.namespace}-${r.name}`
        return (
        <Dropdown trigger={['click']} menu={{
          items: [
            { key: 'metrics', label: '监控' },
            { key: 'logs', label: '日志' },
            { key: 'exec', label: '进容器' },
            { key: 'jvm', label: 'JVM 诊断' },
            { key: 'events', label: '事件' },
            { key: 'yaml', label: '编辑 YAML' },
            isolated.has(key) ? { key: 'unisolate', label: '取消隔离' } : { key: 'isolate', label: '隔离' },
            { type: 'divider' as const },
            { key: 'delete', label: '删除', danger: true },
          ],
          onClick: ({ key: k }) => {
            const t = { clusterId, namespace: r.namespace, name: r.name }
            if (k === 'metrics') setMetricsOpen(true)
            else if (k === 'logs') setLogPod(t)
            else if (k === 'exec') setExecTarget(t)
            else if (k === 'jvm') setJvmPod(t)
            else if (k === 'events') setEventsPod(t)
            else if (k === 'yaml') setYamlPod(t)
            else if (k === 'isolate') handleIsolate(r)
            else if (k === 'unisolate') handleUnisolate(r)
            else if (k === 'delete') confirmDeletePod(r)
          },
        }}>
          <Button size="small" type="link"><MoreOutlined /></Button>
        </Dropdown>
        )
      },
    },
  ]

  const overviewTab = (
    <div>
      <Descriptions size="small" column={2} bordered style={{ marginBottom: 16 }}>
        <Descriptions.Item label="命名空间">{namespace}</Descriptions.Item>
        <Descriptions.Item label="副本">期望 {desired} · 就绪 {readyReplicas} · 可用 {meta.available == null ? '-' : meta.available} · 已更新 {meta.updated == null ? '-' : meta.updated}</Descriptions.Item>
        <Descriptions.Item label="镜像" span={2}>
          {containers.length
            ? containers.map((c) => `${c.name}: ${c.image}`).join(' ｜ ') || (meta.images.join(' ｜ '))
            : meta.images.join(' ｜ ') || '-'}
        </Descriptions.Item>
        <Descriptions.Item label="更新策略">{meta.strategy || '-'}</Descriptions.Item>
        <Descriptions.Item label="创建时间">{fmtTime(meta.created)}</Descriptions.Item>
        <Descriptions.Item label="更新时间">{fmtTime(updateTime)}</Descriptions.Item>
      </Descriptions>
      <div style={{ fontWeight: 600, marginBottom: 8 }}>容器组（Pod）</div>
      <Table rowKey="name" size="small" dataSource={pods} pagination={false} columns={podColumns}
        locale={{ emptyText: <Empty description="暂无 Pod（可能未采集或名称不匹配）" /> }} />
    </div>
  )

  // 卷与挂载：从 YAML 解析（只读展示，编辑在「存储挂载」Tab）
  const storageInfo = useMemo(() => {
    const root = asMap(parseYaml(yaml))
    const spec = asMap(asMap(root).spec)
    const tplSpec = asMap(asMap(spec.template).spec)
    const vols: { name: string; type: string; source: string; mounts: string[] }[] = []
    for (const v of asArr(tplSpec.volumes)) {
      const vm = asMap(v)
      const name = asStr(vm.name)
      let type = 'other', source = ''
      if (vm.configMap) { type = 'configMap'; source = asStr(asMap(vm.configMap).name) }
      else if (vm.secret) { type = 'secret'; source = asStr(asMap(vm.secret).secretName) }
      else if (vm.persistentVolumeClaim) { type = 'pvc'; source = asStr(asMap(vm.persistentVolumeClaim).claimName) }
      else if (vm.emptyDir !== undefined) { type = 'emptyDir'; source = '临时目录' }
      else if (vm.hostPath) { type = 'hostPath'; source = asStr(asMap(vm.hostPath).path) }
      else if (vm.nfs) { type = 'nfs'; source = `${asStr(asMap(vm.nfs).server)}:${asStr(asMap(vm.nfs).path)}` }
      else if (vm.projected) { type = 'projected'; source = 'projected' }
      else if (vm.csi) { type = 'csi'; source = asStr(asMap(vm.csi).driver) }
      vols.push({ name, type, source, mounts: [] })
    }
    const refs = new Map<string, { type: string; name: string; containers: string[] }>()
    const allContainers = [...asArr(tplSpec.containers), ...asArr(tplSpec.initContainers)]
    for (const c of allContainers) {
      const cm = asMap(c)
      const cname = asStr(cm.name)
      for (const m of asArr(cm.volumeMounts)) {
        const mm = asMap(m)
        const vname = asStr(mm.name)
        const vol = vols.find((v) => v.name === vname)
        const path = asStr(mm.mountPath) + (asStr(mm.subPath) ? ` (${asStr(mm.subPath)})` : '') + (asStr(mm.readOnly) === 'true' ? ' [只读]' : '')
        if (vol) vol.mounts.push(`${cname} → ${path}`)
        if (vol && ['configMap', 'secret', 'pvc'].includes(vol.type) && vol.source) {
          const key = `${vol.type}-${vol.source}`
          const cur = refs.get(key) || { type: vol.type, name: vol.source, containers: [] }
          if (!cur.containers.includes(cname)) cur.containers.push(cname)
          refs.set(key, cur)
        }
      }
    }
    return { volumes: vols, refs: Array.from(refs.values()) }
  }, [yaml])

  const metadataTab = (
    <div>
      <div style={{ fontWeight: 600, marginBottom: 8 }}>Labels</div>
      <Table rowKey="k" size="small" pagination={false} dataSource={kvRows(meta.labels)}
        columns={[
          { title: 'Key', dataIndex: 'k', width: 300, render: (v: string) => <code>{v}</code> },
          { title: 'Value', dataIndex: 'v' },
        ]}
        locale={{ emptyText: '无 Labels' }} style={{ marginBottom: 16 }} />
      <div style={{ fontWeight: 600, marginBottom: 8 }}>Annotations</div>
      <Table rowKey="k" size="small" pagination={false}
        dataSource={kvRows(meta.annotations).filter((r) => !r.k.startsWith('kubectl.kubernetes.io/'))}
        columns={[
          { title: 'Key', dataIndex: 'k', width: 300, render: (v: string) => <code>{v}</code> },
          { title: 'Value', dataIndex: 'v', render: (v: string) => <span style={{ wordBreak: 'break-all' }}>{v}</span> },
        ]}
        locale={{ emptyText: '无 Annotations' }} />
    </div>
  )

  const realtimeEventsTab = (
    <div>
      <Space style={{ marginBottom: 8 }} wrap>
        <Button size="small" icon={<ReloadOutlined />} onClick={loadRealtimeEvents} loading={realtimeLoading}>刷新</Button>
        <span style={{ fontSize: 12, color: '#8c8c8c' }}>实时拉取 k8s Event（按 Pod 汇总、同类型同原因合并，进入本 Tab 时自动加载一次）</span>
      </Space>
      <Table rowKey={(_, i) => String(i)} size="small" dataSource={realtimeEvents} pagination={{ pageSize: 10, hideOnSinglePage: true }}
        columns={[
          { title: '类型', dataIndex: 'type', width: 90, render: (v: string) => <Tag color={v === 'Warning' ? 'red' : 'default'}>{v}</Tag> },
          { title: '原因', dataIndex: 'reason', width: 160 },
          { title: '对象', dataIndex: 'pods', width: 220, ellipsis: true, render: (v: string[]) => <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{(v || []).join('、') || '-'}</span> },
          { title: '消息', dataIndex: 'message', ellipsis: true },
          { title: '次数', dataIndex: 'count', width: 60, render: (v: number) => (v == null ? '-' : String(v)) },
          { title: '最近时间', dataIndex: 'last_timestamp', width: 160, render: (v: string) => fmtTime(v) },
        ]}
        locale={{ emptyText: <Empty description="暂无实时事件" /> }} />
    </div>
  )

  const historyEventsTab = (
    <div>
      <Space style={{ marginBottom: 8 }} wrap>
        <Button size="small" icon={<ReloadOutlined />} onClick={() => loadEvents()} loading={eventsLoading}>刷新</Button>
        <span style={{ fontSize: 12, color: '#8c8c8c' }}>时间范围</span>
        <Select
          size="small"
          style={{ width: 110 }}
          value={historyHours}
          onChange={(v) => { setHistoryHours(v); loadEvents(v) }}
          options={[
            { value: 6, label: '6 小时' },
            { value: 24, label: '24 小时' },
            { value: 72, label: '72 小时' },
            { value: 168, label: '7 天' },
          ]}
        />
        <span style={{ fontSize: 12, color: '#8c8c8c' }}>平台自存 k8s_events，含该工作负载下所有 Pod</span>
      </Space>
      <Table rowKey={(_, i) => String(i)} size="small" dataSource={events} pagination={{ pageSize: 10, hideOnSinglePage: true }}
        columns={[
          { title: '类型', dataIndex: 'type', width: 90, render: (v: string) => <Tag color={v === 'Warning' ? 'red' : 'blue'}>{v}</Tag> },
          { title: '原因', dataIndex: 'reason', width: 160 },
          { title: '消息', dataIndex: 'message', ellipsis: true },
          { title: '次数', dataIndex: 'count', width: 60 },
          { title: '资源', dataIndex: 'resource_kind', width: 130, render: (v: string, r: WorkloadEventRow) => (v ? `${v}/${r.resource_name}` : r.resource_name || '-') },
          { title: '首次', dataIndex: 'first_timestamp', width: 160, render: (v: string) => fmtTime(v) },
          { title: '最后', dataIndex: 'last_timestamp', width: 160, render: (v: string) => fmtTime(v) },
        ]}
        locale={{ emptyText: <Empty description="暂无事件" /> }} />
    </div>
  )

  return (
    <div>
      <Breadcrumb style={{ marginBottom: 12 }} items={[
        { title: <a onClick={() => navigate('/clusters')}>集群管理</a> },
        { title: <a onClick={() => navigate(`/clusters/${id}`)}>应用负载</a> },
        { title: <a onClick={() => navigate(`/clusters/${id}/workloads/${kind}`)}>{KIND_SHORT[kind] || kind}</a> },
        { title: name },
      ]} />

      <Card size="small" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
          <Space size={12} wrap>
            <span style={{ fontSize: 18, fontWeight: 700 }}>{name}</span>
            <Tag color={statusColor}>{status}</Tag>
            <Tag>{KIND_SHORT[kind] || kind}</Tag>
            <span style={{ fontSize: 12, color: '#999' }}>{clusterName ? `${clusterName} / ` : ''}{namespace}</span>
          </Space>
          <Space>
            <Button onClick={() => setAggLogsOpen(true)}>聚合日志</Button>
            <Button onClick={() => setWlEdit({ clusterId, kind, namespace, name, create: false, initialValues: editValues, existingYaml: yaml })}>编辑设置</Button>
            <Dropdown trigger={['click']} menu={{
              items: [
                { key: 'yaml', label: '编辑 YAML（高级）' },
                ...(scalable ? [{ key: 'scale', label: '变更副本' }] : []),
                ...(restartable ? [{ key: 'restart', label: '滚动重启' }] : []),
                { type: 'divider' as const },
                { key: 'delete', label: '删除', danger: true },
              ],
              onClick: ({ key: k }) => {
                if (k === 'yaml') setWlYaml({ clusterId, kind, namespace, name })
                else if (k === 'scale') { setScaleVal(desired); setScaleOpen(true) }
                else if (k === 'restart') {
                  Modal.confirm({ title: '触发滚动重启？', onOk: handleRollout })
                } else if (k === 'delete') handleDelete()
              },
            }}>
              <Button>更多 <DownOutlined /></Button>
            </Dropdown>
            <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
          </Space>
        </div>
      </Card>

      <Card size="small">
        <Tabs defaultActiveKey="overview" onChange={(key) => { if (key === 'events-realtime') loadRealtimeEvents() }} items={[
          { key: 'overview', label: '概览', children: overviewTab },
          { key: 'metadata', label: '元数据', children: metadataTab },
          { key: 'storage', label: '存储与配置', children: (
            <div>
              <WorkloadMountsPanel clusterId={clusterId} kind={kind} namespace={namespace} name={name} onlySection="storage" />
              <div style={{ fontWeight: 600, margin: '24px 0 8px' }}>挂载的配置（ConfigMap / Secret / PVC）</div>
              <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 8 }}>
                汇总本工作负载引用的配置字典 / 密钥 / 持久卷声明，以及各自被哪些容器挂载（配置内容本身在「配置」页查看）。
              </div>
              <Table rowKey={(r) => `${r.type}-${r.name}`} size="small" pagination={false} dataSource={storageInfo.refs}
                columns={[
                  { title: '类型', dataIndex: 'type', width: 140, render: (v: string) => <Tag color={VOL_TYPE_COLOR[v] || 'default'}>{v}</Tag> },
                  { title: '名称', dataIndex: 'name', width: 300, render: (v: string) => <code>{v}</code> },
                  { title: '被哪些容器挂载', dataIndex: 'containers', render: (v: string[]) => (v.length ? v.join('、') : '-') },
                  {
                    title: '操作', width: 140,
                    render: (r: { type: string }) => (
                      <Button size="small" type="link"
                        onClick={() => navigate(`/clusters/${clusterId}/configs${r.type === 'pvc' ? '' : ''}`)}>
                        {r.type === 'pvc' ? '去存储页' : '去配置页'}
                      </Button>
                    ),
                  },
                ]}
                locale={{ emptyText: '没有引用配置字典 / 密钥 / PVC' }} />
            </div>
          ) },
          { key: 'env', label: '环境变量', children: (
            <WorkloadMountsPanel clusterId={clusterId} kind={kind} namespace={namespace} name={name} onlySection="env" />
          ) },
          { key: 'hpa', label: '弹性伸缩', children: (
            <HpaPanel clusterId={clusterId} kind={kind} namespace={namespace} name={name} />
          ) },
          { key: 'events-realtime', label: '实时事件', children: realtimeEventsTab },
          { key: 'events-history', label: '历史事件', children: historyEventsTab },
          { key: 'monitor', label: '监控', children: (
            <MonitorPanel clusterId={clusterId} kind={kind} namespace={namespace} name={name} />
          ) },
        ]} />
      </Card>

      <WorkloadYamlModal target={wlYaml} onClose={() => setWlYaml(null)} onApplied={load} />
      <WorkloadMetricsDrawer
        target={metricsOpen ? { clusterId, kind, namespace, name } : null}
        onClose={() => setMetricsOpen(false)}
      />
      <PodLogStreamModal pod={logPod} onClose={() => setLogPod(null)} />
      <WorkloadLogsModal
        clusterId={clusterId}
        namespace={namespace}
        kind={kind}
        name={name}
        open={aggLogsOpen}
        onClose={() => setAggLogsOpen(false)}
      />
      <TerminalModal pod={execTarget} onClose={() => setExecTarget(null)} />
      <PodEventsModal pod={eventsPod} onClose={() => setEventsPod(null)} />
      <JvmDiagModal pod={jvmPod} onClose={() => setJvmPod(null)} />
      <PodYamlModal pod={yamlPod} onClose={() => setYamlPod(null)} />

      <WorkloadCreateModal target={wlEdit} onClose={() => setWlEdit(null)} onSaved={load} />

      <Modal title={`变更副本：${name}`} open={scaleOpen} onCancel={() => setScaleOpen(false)}
        onOk={handleScale} okText="确定" width={360}>
        <div style={{ marginBottom: 8, color: '#8c8c8c', fontSize: 13 }}>调整为指定副本数（0 = 停止）。</div>
        <InputNumber min={0} value={scaleVal} onChange={(v) => setScaleVal(v ?? 0)} style={{ width: '100%' }} />
      </Modal>
    </div>
  )
}

// 编辑 Pod YAML 弹窗（与容器组页 PodsList / ClusterDetail 的局部实现一致；尚无共享组件，故此处复刻）
function PodYamlModal({ pod, onClose }: { pod: PodTarget | null; onClose: () => void }) {
  const [yaml, setYaml] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!pod) return
    setLoading(true)
    getPodYaml(pod.clusterId, pod.namespace, pod.name)
      .then((d) => setYaml((d as { yaml?: string }).yaml || ''))
      .catch(() => message.error('YAML 获取失败'))
      .finally(() => setLoading(false))
  }, [pod])

  const apply = async () => {
    if (!yaml.trim()) return
    setLoading(true)
    try {
      await applyPodYaml(pod!.clusterId, pod!.namespace, pod!.name, yaml)
      message.success('YAML 已应用')
      onClose()
    } catch {
      message.error('YAML 应用失败')
    } finally {
      setLoading(false)
    }
  }

  if (!pod) return null
  return (
    <Modal title={`编辑 Pod YAML：${pod.name}`} open={!!pod} onCancel={onClose} onOk={apply} okText="应用" confirmLoading={loading} width={900}>
      <Input.TextArea value={yaml} onChange={(e) => setYaml(e.target.value)} rows={22} style={{ fontFamily: 'monospace', fontSize: 12 }} />
    </Modal>
  )
}
