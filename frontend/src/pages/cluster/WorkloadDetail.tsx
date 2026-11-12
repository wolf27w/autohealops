// 工作负载详情页（整页）：概览 / 资源状态 / 元数据 / 环境变量 / 事件 / 监控
// 路由形态：/clusters/:id/workloads/:kind/:namespace/:name（路由由父会话接线，此处用 useParams）
import { useEffect, useMemo, useState } from 'react'
import {
  Breadcrumb, Card, Descriptions, Button, Space, Table, Tag, Modal, InputNumber, Empty, Dropdown, Tabs,
} from 'antd'
import { ReloadOutlined, MoreOutlined, DownOutlined } from '@ant-design/icons'
import { useNavigate, useParams } from 'react-router-dom'
import WorkloadYamlModal, { type WorkloadTarget } from '../../components/WorkloadYamlModal'
import WorkloadMetricsDrawer from '../../components/WorkloadMetricsDrawer'
import PodLogStreamModal from '../../components/PodLogStreamModal'
import TerminalModal from '../../components/TerminalModal'
import PodEventsModal from '../../components/PodEventsModal'
import WorkloadMountsPanel from '../../components/WorkloadMountsPanel'
import WorkloadCreateModal, { type WorkloadCreateTarget } from '../../components/WorkloadCreateModal'
import type { WorkloadValues, VolumeMountValue, VolumeType } from '../../components/workloadYaml'
import MonitorPanel from '../../components/cluster/MonitorPanel'
import {
  getWorkloads, getClusters, getClusterDetail, getClusterPods, getWorkloadContainers,
  getWorkloadYaml, getWorkloadEventHistory, scaleWorkload, rolloutWorkload, deleteWorkload,
} from '../../services/api'
import type { ClusterPod, WorkloadEventRow } from '../../services/api'
import type { PodTarget, Workload } from '../../types'
import { podStatusColor, podReadyText } from '../../utils/podStatus'

const KIND_SHORT: Record<string, string> = {
  deployment: '部署', statefulset: '有状态', daemonset: '守护集', cronjob: '定时任务',
}

// ============ 极简 YAML 子集解析（无第三方库，仅解析 k8s 工作负载 YAML 需要的字段） ============
type YamlNode = string | number | boolean | null | YamlNode[] | { [k: string]: YamlNode }

interface L { indent: number; text: string }

function stripTrailingComment(s: string): string {
  let inS = false, inD = false
  for (let i = 0; i < s.length; i++) {
    const c = s[i]
    if (c === "'" && !inD) inS = !inS
    else if (c === '"' && !inS) inD = !inD
    else if (c === '#' && !inS && !inD) {
      if (i === 0 || s[i - 1] === ' ' || s[i - 1] === '\t') return s.slice(0, i).trimEnd()
    }
  }
  return s
}

function preprocess(src: string): L[] {
  const out: L[] = []
  for (const raw of src.split('\n')) {
    if (raw.trim() === '') continue
    const m = raw.match(/^(\s*)/)
    const indent = m ? m[1].length : 0
    let text = raw.slice(indent)
    if (text.startsWith('#')) continue
    if (text === '---' || text === '...') continue
    text = stripTrailingComment(text)
    out.push({ indent, text })
  }
  return out
}

// 键冒号位置（冒号后是空格或行尾才算键分隔；忽略引号内与值里的冒号如 http://）
function keyColonIndex(s: string): number {
  let inS = false, inD = false
  for (let i = 0; i < s.length; i++) {
    const c = s[i]
    if (c === "'" && !inD) inS = !inS
    else if (c === '"' && !inS) inD = !inD
    else if (c === ':' && !inS && !inD) {
      const after = s[i + 1]
      if (after === undefined || after === ' ' || after === '\t') return i
    }
  }
  return -1
}

function parseScalar(s: string): YamlNode {
  const t = s.trim()
  if (t === '' || t === 'null' || t === 'Null' || t === 'NULL' || t === '~') return null
  if (t === 'true' || t === 'True') return true
  if (t === 'false' || t === 'False') return false
  if ((t.startsWith('"') && t.endsWith('"')) || (t.startsWith("'") && t.endsWith("'"))) return t.slice(1, -1)
  if (/^-?\d+(\.\d+)?$/.test(t)) return Number(t)
  return t
}

function isDash(t: string) { return t === '-' || t.startsWith('- ') }

function parseNode(lines: L[], i: number): { val: YamlNode; next: number } {
  if (i >= lines.length) return { val: null, next: i }
  if (isDash(lines[i].text)) return parseList(lines, i, lines[i].indent)
  return parseMap(lines, i, lines[i].indent)
}

// 键后面的值在下一行：可能是缩进的 map / 同级 compact 列表 / 空
function parseValueAfterKey(lines: L[], i: number, indent: number): { val: YamlNode; next: number } {
  if (i >= lines.length) return { val: null, next: i }
  const nxt = lines[i]
  if (isDash(nxt.text)) return parseList(lines, i, nxt.indent)
  if (nxt.indent > indent) return parseMap(lines, i, nxt.indent)
  return { val: null, next: i }
}

function parseMap(lines: L[], i: number, indent: number): { val: YamlNode; next: number } {
  const obj: Record<string, YamlNode> = {}
  while (i < lines.length) {
    const line = lines[i]
    if (line.indent < indent) break
    if (line.indent > indent) break
    if (isDash(line.text)) break
    const idx = keyColonIndex(line.text)
    if (idx < 0) { i++; continue }
    const key = line.text.slice(0, idx).trim()
    const rest = line.text.slice(idx + 1).trim()
    if (rest === '') {
      const sub = parseValueAfterKey(lines, i + 1, indent)
      obj[key] = sub.val
      i = sub.next
    } else if (/^[|>]/.test(rest)) {
      // 块标量（如 last-applied-configuration 的多行 JSON）：吞掉更深缩进的行
      let j = i + 1
      const chunks: string[] = []
      while (j < lines.length && lines[j].indent > indent) { chunks.push(lines[j].text); j++ }
      obj[key] = chunks.join('\n') || null
      i = j
    } else {
      obj[key] = parseScalar(rest)
      i++
    }
  }
  return { val: obj, next: i }
}

function parseList(lines: L[], i: number, indent: number): { val: YamlNode; next: number } {
  const arr: YamlNode[] = []
  while (i < lines.length) {
    const line = lines[i]
    if (line.indent < indent) break
    if (line.indent > indent) break
    if (!isDash(line.text)) break
    const afterDash = line.text === '-' ? '' : line.text.slice(2).trim()
    if (afterDash === '') {
      if (i + 1 < lines.length && lines[i + 1].indent > indent) {
        const sub = parseNode(lines, i + 1)
        arr.push(sub.val)
        i = sub.next
      } else {
        arr.push(null)
        i++
      }
    } else if (keyColonIndex(afterDash) >= 0) {
      // 内联 map 项：`- key: value` + 后续更深缩进的续行
      const itemObj: Record<string, YamlNode> = {}
      const idx = keyColonIndex(afterDash)
      const key = afterDash.slice(0, idx).trim()
      const rest = afterDash.slice(idx + 1).trim()
      let j = i + 1
      if (rest === '') {
        if (j < lines.length && (lines[j].indent > indent || isDash(lines[j].text))) {
          const sub = parseValueAfterKey(lines, j, indent)
          itemObj[key] = sub.val
          j = sub.next
        } else {
          itemObj[key] = null
        }
      } else {
        itemObj[key] = parseScalar(rest)
      }
      while (j < lines.length && lines[j].indent > indent && !isDash(lines[j].text)) {
        const sub = parseMap(lines, j, lines[j].indent)
        Object.assign(itemObj, sub.val as Record<string, YamlNode>)
        j = sub.next
      }
      arr.push(itemObj)
      i = j
    } else {
      arr.push(parseScalar(afterDash))
      i++
    }
  }
  return { val: arr, next: i }
}

function parseYaml(src: string): YamlNode {
  const lines = preprocess(src || '')
  if (lines.length === 0) return null
  return parseNode(lines, 0).val
}

const asMap = (n: YamlNode): Record<string, YamlNode> => (n && typeof n === 'object' && !Array.isArray(n) ? n as Record<string, YamlNode> : {})
const asArr = (n: YamlNode): YamlNode[] => (Array.isArray(n) ? n : [])
const asStr = (n: YamlNode): string => (n == null ? '' : String(n))

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

function strMap(n: YamlNode): Record<string, string> {
  const m = asMap(n)
  const out: Record<string, string> = {}
  for (const k of Object.keys(m)) out[k] = asStr(m[k])
  return out
}

// 解析出的 YAML → 向导表单值（编辑设置回填；仅覆盖向导支持的字段）
function yamlToValues(root: YamlNode, kind: string): Partial<WorkloadValues> {
  const spec = asMap(asMap(root).spec)
  const tplSpec = asMap(asMap(spec.template).spec)
  const containers = asArr(tplSpec.containers)
  const c0 = asMap(containers[0])
  const meta = asMap(asMap(root).metadata)

  const resources = asMap(c0.resources)
  const requests = asMap(resources.requests)
  const limits = asMap(resources.limits)

  const env = asArr(c0.env)
    .map((e) => asMap(e))
    .filter((e) => e.value != null)
    .map((e) => ({ key: asStr(e.name), value: asStr(e.value) }))

  const labels = strMap(meta.labels)
  delete labels.app

  const volumes: VolumeMountValue[] = []
  const volList = asArr(tplSpec.volumes).map((x) => asMap(x))
  const mounts = asArr(c0.volumeMounts).map((x) => asMap(x))
  for (const m of mounts) {
    const vn = asStr(m.name)
    const vol = volList.find((x) => asStr(x.name) === vn) || {}
    let type: VolumeType = 'emptyDir'
    let source: string | undefined
    if (vol.configMap != null) { type = 'configMap'; source = asStr(asMap(vol.configMap).name) }
    else if (vol.secret != null) { type = 'secret'; source = asStr(asMap(vol.secret).secretName) }
    else if (vol.persistentVolumeClaim != null) { type = 'pvc'; source = asStr(asMap(vol.persistentVolumeClaim).claimName) }
    else if (vol.emptyDir != null) { type = 'emptyDir' }
    volumes.push({
      name: vn, type, source, mountPath: asStr(m.mountPath),
      subPath: m.subPath != null ? asStr(m.subPath) : undefined,
      readOnly: m.readOnly === true,
    })
  }

  const live = asMap(c0.livenessProbe)
  const liveGet = asMap(live.httpGet)
  const ready = asMap(c0.readinessProbe)
  const readyGet = asMap(ready.httpGet)

  const out: Partial<WorkloadValues> = {
    name: asStr(meta.name),
    namespace: asStr(meta.namespace),
    image: asStr(c0.image),
    imagePullPolicy: asStr(c0.imagePullPolicy) || 'IfNotPresent',
    replicas: spec.replicas != null ? Number(spec.replicas) : undefined,
    cpuRequest: asStr(requests.cpu) || undefined,
    cpuLimit: asStr(limits.cpu) || undefined,
    memoryRequest: asStr(requests.memory) || undefined,
    memoryLimit: asStr(limits.memory) || undefined,
    env,
    labels: Object.entries(labels).map(([key, value]) => ({ key, value })),
    volumes,
    livenessEnabled: live.httpGet != null,
    livenessPath: asStr(liveGet.path) || '/healthz',
    livenessPort: liveGet.port != null ? Number(liveGet.port) : undefined,
    readinessEnabled: ready.httpGet != null,
    readinessPath: asStr(readyGet.path) || '/healthz',
    readinessPort: readyGet.port != null ? Number(readyGet.port) : undefined,
  }

  const ports = asArr(c0.ports).map((x) => asMap(x))
  const cp = ports.find((p) => p.containerPort != null)
  if (cp) out.containerPort = Number(cp.containerPort)

  if (kind === 'cronjob') {
    out.schedule = asStr(spec.schedule)
    const cmd = asArr(c0.command)
    out.command = cmd.length >= 3 ? asStr(cmd[2]) : (cmd.length ? asStr(cmd[cmd.length - 1]) : '')
  }

  return out
}

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

  // 弹窗 / 抽屉
  const [wlYaml, setWlYaml] = useState<WorkloadTarget | null>(null)
  const [wlEdit, setWlEdit] = useState<WorkloadCreateTarget | null>(null)
  const [scaleOpen, setScaleOpen] = useState(false)
  const [scaleVal, setScaleVal] = useState<number>(1)
  const [metricsOpen, setMetricsOpen] = useState(false)
  const [logPod, setLogPod] = useState<PodTarget | null>(null)
  const [execTarget, setExecTarget] = useState<PodTarget | null>(null)
  const [eventsPod, setEventsPod] = useState<PodTarget | null>(null)

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

  const loadEvents = () => {
    setEventsLoading(true)
    getWorkloadEventHistory(clusterId, kind, namespace, name, 24)
      .then((d) => setEvents(d?.events || []))
      .catch(() => setEvents([]))
      .finally(() => setEventsLoading(false))
  }

  const desired = meta.desired ?? workload?.replicas ?? 0
  const readyReplicas = meta.ready ?? workload?.ready_replicas ?? 0
  const status = desired === 0 ? '已停止' : readyReplicas >= desired ? '运行中' : '更新中'
  const statusColor = { 运行中: 'green', 更新中: 'orange', 已停止: 'default' }[status]

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

  const kvRows = (m: Record<string, string>) => Object.entries(m).map(([k, v]) => ({ k, v }))

  const podColumns = [
    { title: '名称', dataIndex: 'name', render: (v: string) => <span style={{ fontWeight: 600 }}>{v}</span> },
    { title: '状态', dataIndex: 'status', width: 150, render: (_: unknown, r: ClusterPod) => <Tag color={podStatusColor(r.status || r.phase)}>{r.status || r.phase || '-'}</Tag> },
    { title: '就绪', key: 'ready', width: 70, render: (_: unknown, r: ClusterPod) => podReadyText(r.ready_containers, r.total_containers) },
    { title: '重启', dataIndex: 'restart_count', width: 70, render: (v: unknown) => (v == null ? '-' : String(v)) },
    { title: '节点', dataIndex: 'node_name', width: 160, render: (v: unknown) => (v ? String(v) : '-') },
    { title: '创建时间', dataIndex: 'created_at', width: 165, render: (v: unknown) => fmtTime(v as string) },
    {
      title: '操作', width: 100, render: (_: unknown, r: ClusterPod) => (
        <Dropdown trigger={['click']} menu={{
          items: [
            { key: 'metrics', label: '监控' },
            { key: 'logs', label: '日志' },
            { key: 'exec', label: '进容器' },
            { key: 'events', label: '事件' },
          ],
          onClick: ({ key: k }) => {
            const t = { clusterId, namespace: r.namespace, name: r.name }
            if (k === 'metrics') setMetricsOpen(true)
            else if (k === 'logs') setLogPod(t)
            else if (k === 'exec') setExecTarget(t)
            else if (k === 'events') setEventsPod(t)
          },
        }}>
          <Button size="small" type="link"><MoreOutlined /></Button>
        </Dropdown>
      ),
    },
  ]

  const overviewTab = (
    <div>
      <Descriptions size="small" column={2} bordered style={{ marginBottom: 16 }}>
        <Descriptions.Item label="命名空间">{namespace}</Descriptions.Item>
        <Descriptions.Item label="副本数">{desired}（就绪 {readyReplicas}）</Descriptions.Item>
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

  const resourceTab = (
    <Descriptions size="small" column={2} bordered>
      <Descriptions.Item label="期望副本">{desired}</Descriptions.Item>
      <Descriptions.Item label="已更新副本">{meta.updated == null ? '-' : meta.updated}</Descriptions.Item>
      <Descriptions.Item label="就绪副本">{readyReplicas}</Descriptions.Item>
      <Descriptions.Item label="可用副本">{meta.available == null ? '-' : meta.available}</Descriptions.Item>
    </Descriptions>
  )

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

  const eventsTab = (
    <div>
      <Space style={{ marginBottom: 8 }}>
        <Button size="small" onClick={loadEvents} loading={eventsLoading}>刷新事件</Button>
        <span style={{ fontSize: 12, color: '#8c8c8c' }}>最近 24 小时（平台自存 k8s_events，含该工作负载下所有 Pod）</span>
      </Space>
      <Table rowKey={(_, i) => String(i)} size="small" dataSource={events} pagination={{ pageSize: 10, hideOnSinglePage: true }}
        columns={[
          { title: '类型', dataIndex: 'type', width: 90, render: (v: string) => <Tag color={v === 'Warning' ? 'red' : 'blue'}>{v}</Tag> },
          { title: '原因', dataIndex: 'reason', width: 160 },
          { title: '消息', dataIndex: 'message', render: (v: string) => <span style={{ whiteSpace: 'pre-wrap' }}>{v}</span> },
          { title: '次数', dataIndex: 'count', width: 60 },
          { title: '资源', dataIndex: 'resource_kind', width: 110, render: (v: string, r: WorkloadEventRow) => (v ? `${v}/${r.resource_name}` : r.resource_name || '-') },
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
            <Button onClick={() => setWlEdit({ clusterId, kind, namespace, name, create: false, initialValues: editValues })}>编辑设置</Button>
            <Dropdown menu={{
              items: [
                { key: 'yaml', label: '编辑 YAML' },
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
        <Tabs defaultActiveKey="overview" items={[
          { key: 'overview', label: '概览', children: overviewTab },
          { key: 'resource', label: '资源状态', children: resourceTab },
          { key: 'metadata', label: '元数据', children: metadataTab },
          { key: 'env', label: '环境变量', children: (
            <WorkloadMountsPanel clusterId={clusterId} kind={kind} namespace={namespace} name={name} onlySection="env" />
          ) },
          { key: 'events', label: '事件', children: eventsTab },
          { key: 'monitor', label: '监控', children: (
            <MonitorPanel clusterId={clusterId} kind={kind} namespace={namespace} name={name} />
          ) },
        ]} />
      </Card>

      <WorkloadYamlModal target={wlYaml} onClose={() => setWlYaml(null)} />
      <WorkloadMetricsDrawer
        target={metricsOpen ? { clusterId, kind, namespace, name } : null}
        onClose={() => setMetricsOpen(false)}
      />
      <PodLogStreamModal pod={logPod} onClose={() => setLogPod(null)} />
      <TerminalModal pod={execTarget} onClose={() => setExecTarget(null)} />
      <PodEventsModal pod={eventsPod} onClose={() => setEventsPod(null)} />

      <WorkloadCreateModal target={wlEdit} onClose={() => setWlEdit(null)} onSaved={load} />

      <Modal title={`变更副本：${name}`} open={scaleOpen} onCancel={() => setScaleOpen(false)}
        onOk={handleScale} okText="确定" width={360}>
        <div style={{ marginBottom: 8, color: '#8c8c8c', fontSize: 13 }}>调整为指定副本数（0 = 停止）。</div>
        <InputNumber min={0} value={scaleVal} onChange={(v) => setScaleVal(v ?? 0)} style={{ width: '100%' }} />
      </Modal>
    </div>
  )
}
