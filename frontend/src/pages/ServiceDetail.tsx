// 服务详情页（专项 D）：工作负载概览 + 副本增减 + 探针编辑 + 回滚/重启/重建 + Pods 列表（监控/日志/进容器/YAML）
import { useEffect, useRef, useState } from 'react'
import { Card, Descriptions, Button, Space, Table, Tag, InputNumber, Popconfirm, Modal, Form, Select, Input, message, Tabs, Drawer, Segmented, Empty, Dropdown, Switch, Tooltip } from 'antd'
import { ArrowLeftOutlined, ReloadOutlined, DownOutlined, MinusOutlined, PlusOutlined, QuestionCircleOutlined } from '@ant-design/icons'
import { useNavigate, useParams } from 'react-router-dom'
import { Line } from '@ant-design/charts'
import TerminalModal from '../components/TerminalModal'
import { getWorkloads, scaleWorkload, rolloutWorkload, getRolloutHistory, updateWorkloadProbe, recreateWorkload,
  getPodMetrics, getPodLogs, getPodYaml, applyPodYaml, execPod, getClusters, getClusterDetail, getWorkloadEvents, getPodContainers } from '../services/api'
import type { MetricPoint, PodTarget, Workload } from '../types'

const KIND_LABEL: Record<string, string> = {
  deployment: '无状态 Deployment', statefulset: '有状态 StatefulSet',
  daemonset: '守护集 DaemonSet', cronjob: '定时任务 CronJob',
}

interface ServicePod {
  name: string
  namespace: string
  status?: string
  cpu_request?: string | number
  cpu_limit?: string | number
  memory_request?: string | number
  memory_limit?: string | number
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
  const [metricsPod, setMetricsPod] = useState<PodTarget | null>(null)
  const [logPod, setLogPod] = useState<PodTarget | null>(null)
  const [execTarget, setExecTarget] = useState<PodTarget | null>(null)
  const [yamlPod, setYamlPod] = useState<PodTarget | null>(null)
  const [podTab, setPodTab] = useState('pods')
  const [pods, setPods] = useState<ServicePod[]>([])
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
    getClusterDetail(clusterId).then((nsList) => {
      const matched: ServicePod[] = []
      for (const ns of (nsList || []) as NsPods[]) {
        if (ns.namespace !== namespace) continue
        for (const p of ns.pods || []) {
          if (p.name.startsWith(name + '-')) matched.push({ ...p, namespace: ns.namespace })
        }
      }
      setPods(matched)
    })
  }
  useEffect(loadPods, [id, namespace, name])

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
                ...(kind === 'deployment' ? [
                  { key: 'undo', label: '回滚上一版本' },
                  { key: 'probe', label: '编辑探针' },
                ] : []),
                { type: 'divider' as const },
                { key: 'recreate', label: '重新创建', danger: true },
              ],
              onClick: ({ key }) => {
                if (key === 'restart') handleRollout('restart')
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
                { title: '名称', dataIndex: 'name' },
                { title: '状态', dataIndex: 'status', render: (s) => <Tag color={s === 'Running' ? 'green' : 'red'}>{s}</Tag> },
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
                    <Button size="small" type="link" onClick={() => setYamlPod({ ...r, clusterId })}>YAML</Button>
                  </Space>
                ) },
              ]}
              locale={{ emptyText: <Empty description="暂无 Pod（可能未采集或名称不匹配）" /> }} />
          ) },
          { key: 'events', label: '事件', children: (
            <div>
              <Button size="small" style={{ marginBottom: 8 }} onClick={loadEvents} loading={eventsLoading}>刷新事件</Button>
              {events.length === 0 ? <Empty description="暂无事件" /> : events.map((e) => (
                <div key={e.pod} style={{ marginBottom: 12 }}>
                  <div style={{ fontWeight: 600, marginBottom: 4 }}>{e.pod} <span style={{ color: '#999', fontWeight: 400, fontSize: 12 }}>{e.summary}</span></div>
                  <pre style={{ maxHeight: 220, overflow: 'auto', background: '#fafafa', padding: 8, fontSize: 12, whiteSpace: 'pre-wrap', margin: 0 }}>{e.events}</pre>
                </div>
              ))}
            </div>
          ) },
        ]} />
      </Card>

      <PodMetricsDrawer pod={metricsPod} onClose={() => setMetricsPod(null)} />
      <PodLogsDrawer pod={logPod} onClose={() => setLogPod(null)} />
      <TerminalModal pod={execTarget} onClose={() => setExecTarget(null)} />
      <PodYamlModal pod={yamlPod} onClose={() => setYamlPod(null)} />

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
  // 实时查看容器标准输出（/dev/stdout）：默认关闭，开启后每 2 秒拉取最新日志
  const [live, setLive] = useState(false)
  const [autoScroll, setAutoScroll] = useState(true)
  const preRef = useRef<HTMLPreElement>(null)
  const timerRef = useRef<number | null>(null)

  const [containers, setContainers] = useState<string[]>([])

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
      <pre ref={preRef} style={{ maxHeight: '70vh', overflow: 'auto', background: '#f5f5f5', padding: 12, fontSize: 12, whiteSpace: 'pre-wrap' }}>{loading && !logs ? '加载中...' : logs}</pre>
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
