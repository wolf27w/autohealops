// 工作负载聚合日志弹窗（R5 剩余）：快照 + 跟随(SSE) + 下载(gzip)
// 数据源：
//   快照  GET /api/v1/resources/clusters/{id}/namespaces/{ns}/workloads/{kind}/{name}/logs
//   跟随  GET .../logs/stream（EventSource，SSE，token 走 query param）
//   下载  客户端把聚合 rows 合并成带 `[pod] ` 前缀的文本，CompressionStream 压 gzip
// 多容器：容器下拉为多选（留空=全部），每个容器各拉一次快照 / 各开一条 SSE 合并到同一列表，
//         多容器时行前缀额外带 `[容器]` 区分。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Modal, Space, Button, Select, Switch, Input, message, Tag, DatePicker } from 'antd'
import type { Dayjs } from 'dayjs'
import { DownloadOutlined, VerticalAlignBottomOutlined } from '@ant-design/icons'
import { getWorkloadContainers } from '../../services/api'
import { buildLogFilename } from '../../utils/logFilename'
import { applyHighlight, loadRules, saveRules, LogHighlightSettings, type HighlightRule } from '../LogHighlightSettings'

interface Props {
  clusterId: number
  namespace: string
  kind: string
  name: string
  open: boolean
  onClose: () => void
}

interface LogRow { pod: string; container: string; line: string }
interface ApiLogRow { pod: string; line: string }
interface AggregatePodResult { pod: string; phase?: string; success?: boolean; logs?: string; error?: string; fallback?: boolean }
interface AggregateData {
  columns: string[]
  rows: ApiLogRow[]
  truncated: boolean
  pods: AggregatePodResult[] | number
  errors?: { pod: string; error: string }[]
}

/** 汇总逐 Pod 的错误（后端把失败原因放在 pods[].error，errors 数组可能为空） */
function aggregateErrors(d: AggregateData): string[] {
  const out: string[] = []
  if (d.errors?.length) d.errors.forEach((e) => out.push(`[${e.pod}] ${e.error}`))
  if (Array.isArray(d.pods)) {
    d.pods.forEach((p) => {
      if (p.error && p.success !== true) out.push(`[${p.pod}] ${p.error}`)
    })
  }
  return out
}

const MAX_LINES = 5000
const DOWNLOAD_TAIL = 5000
const PALETTE = ['#4ec9b0', '#569cd6', '#dcdcaa', '#c586c0', '#ce9178', '#9cdcfe', '#b5cea8', '#e5c07b', '#f44747', '#7fdbca']

function hashColor(name: string): string {
  let h = 0
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0
  return PALETTE[h % PALETTE.length]
}
const podColor = hashColor
const containerColor = hashColor

// 多容器：默认选第一个非 sidecar（与 Pod 实时日志一致）
const SIDECAR_NAMES = ['filebeat', 'fluent-bit', 'fluentd', 'logstash', 'istio-proxy', 'envoy',
  'linkerd-proxy', 'sidecar', 'logtail', 'vector']
function pickDefaultContainer(list: string[]): string {
  return list.find((c) => !SIDECAR_NAMES.includes(c.toLowerCase())) || list[0] || ''
}

function buildStreamUrl(clusterId: number, ns: string, kind: string, name: string, container: string): string {
  const token = localStorage.getItem('token') || ''
  const p = new URLSearchParams({ container, token })
  return `/api/v1/resources/clusters/${clusterId}/namespaces/${encodeURIComponent(ns)}/workloads/${kind}/${name}/logs/stream?${p.toString()}`
}

async function gzipBlob(text: string): Promise<Blob> {
  const input = new Blob([text]).stream()
  const compressed = input.pipeThrough(new CompressionStream('gzip'))
  return new Response(compressed).blob()
}

export default function WorkloadLogsModal({ clusterId, namespace, kind, name, open, onClose }: Props) {
  const [containers, setContainers] = useState<string[]>([])
  const [selected, setSelected] = useState<string[]>([])
  // 2026-11-23：与 Pod 日志一致，实时改成单一开关（开=跟随，关=停在最近 200 行）
  const [following, setFollowing] = useState(false)
  const pendingRows = useRef<LogRow[]>([])
  const [connected, setConnected] = useState(false)
  const [autoScroll, setAutoScroll] = useState(true)
  const [lines, setLines] = useState<LogRow[]>([])
  const [podFilter, setPodFilter] = useState('')
  const [keyword, setKeyword] = useState('')          // 关键字过滤（空格分隔=同时包含）
  const [onlyErrors, setOnlyErrors] = useState(false) // 只看错误/异常
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [truncated, setTruncated] = useState(false)
  const [rules, setRules] = useState<HighlightRule[]>(() => loadRules())
  const [timeRange, setTimeRange] = useState<[Dayjs | null, Dayjs | null] | null>(null)

  const onChangeRules = useCallback((r: HighlightRule[]) => { setRules(r); saveRules(r) }, [])


  const esMapRef = useRef<Map<string, EventSource>>(new Map())
  const boxRef = useRef<HTMLPreElement | null>(null)

  // 留空 = 全部容器
  const effectiveContainers = useMemo(
    () => (selected.length ? selected : containers),
    [selected, containers],
  )

  const closeStreams = useCallback(() => {
    esMapRef.current.forEach((es) => { es.onopen = null; es.close() })
    esMapRef.current.clear()
    setConnected(false)
  }, [])

  const appendRows = useCallback((rows: LogRow[]) => {
    if (!rows.length) return
    setLines((prev) => {
      const next = [...prev, ...rows]
      return next.length > MAX_LINES ? next.slice(next.length - MAX_LINES) : next
    })
  }, [])

  // 快照加载（非跟随态）：逐容器拉取并合并到同一列表
  // 2026-11-23 修复：默认不限时间窗口，取最近 200 行（原 since_seconds=120 对安静服务返回空）
  const SNAPSHOT_TAIL = 200
  const loadSnapshot = useCallback(async () => {
    const targets = effectiveContainers
    if (!targets.length) { setLines([]); setTruncated(false); return }
    const token = localStorage.getItem('token') || ''
    const allRows: LogRow[] = []
    let anyTruncated = false
    for (const c of targets) {
      const p = new URLSearchParams({ container: c, tail: String(SNAPSHOT_TAIL) })
      const url = `/api/v1/resources/clusters/${clusterId}/namespaces/${encodeURIComponent(namespace)}/workloads/${kind}/${name}/logs?${p.toString()}`
      try {
        const resp = await fetch(url, { headers: { Authorization: `Bearer ${token}` } })
        const body = await resp.json()
        if (body.code !== 200) { message.error(body.message || '加载日志失败'); continue }
        const data = body.data as AggregateData
        allRows.push(...(data.rows || []).map((r) => ({ pod: r.pod, container: c, line: r.line })))
        if (data.truncated) anyTruncated = true
        aggregateErrors(data).forEach((msg) => message.error(msg))
      } catch (e) {
        message.error(String((e as Error)?.message || e))
      }
    }
    setLines(allRows.slice(-MAX_LINES))
    setTruncated(anyTruncated)
  }, [clusterId, namespace, kind, name, effectiveContainers])

  // 开始跟随：为每个容器各开一条 SSE（SSE 先推快照，再每 2s 增量）
  const startFollow = useCallback(() => {
    setLines([])
    setTruncated(false)   // 实时态没有“截断”概念，清掉快照遗留标记
    const targets = effectiveContainers
    for (const c of targets) {
      const es = new EventSource(buildStreamUrl(clusterId, namespace, kind, name, c))
      esMapRef.current.set(c, es)
      es.onopen = () => setConnected(true)
      es.addEventListener('log', (ev) => {
        try {
          const j = JSON.parse((ev as MessageEvent).data as string) as { pod?: string; line?: string }
          if (j && typeof j.pod === 'string' && typeof j.line === 'string') {
            const row: LogRow = { pod: j.pod, container: c, line: j.line }
            appendRows([row])
          }
        } catch { /* 忽略坏帧 */ }
      })
      es.addEventListener('error', (ev) => {
        const data = (ev as MessageEvent).data as string | undefined
        if (data) {
          try {
            const j = JSON.parse(data) as { pod?: string; error?: string }
            message.error(`[${j.pod || ''}] ${j.error || '日志流错误'}`)
          } catch { /* ignore */ }
        } else {
          // 断线（EventSource 会自动重连）；全部断线才标记未连接
          const anyOpen = Array.from(esMapRef.current.values()).some((x) => x.readyState === EventSource.OPEN)
          if (!anyOpen) setConnected(false)
        }
      })
    }
  }, [clusterId, namespace, kind, name, effectiveContainers, appendRows])

  // 容器列表
  useEffect(() => {
    if (!open) return
    setSelected([]); setContainers([]); setPodFilter(''); setCollapsed(new Set()); setTruncated(false)
    getWorkloadContainers(clusterId, kind, namespace, name)
      .then((d) => {
        const list = (d?.containers || []).map((c) => c.name)
        setContainers(list)
        // 2026-11-23 老板口径：聚合日志默认「留空=全部容器」——
        // 后端按 Pod 实际容器逐个拉取并加 [容器名] 前缀（含模板里看不到的 sidecar）
        setSelected([])
      })
      .catch(() => setContainers([]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, clusterId, namespace, kind, name])

  // 非跟随态：打开 / 容器变化 → 重载快照
  useEffect(() => {
    if (!open) return
    if (following) return
    loadSnapshot()
  }, [open, following, loadSnapshot])

  // 跟随态：开启 / 切换容器 → (重)开 SSE，切换时先关闭旧连接
  useEffect(() => {
    if (!open || !following) return
    closeStreams()
    startFollow()
    return () => closeStreams()
  }, [open, following, startFollow, closeStreams])

  // 关闭弹窗 → 关闭所有 SSE 并复位状态
  useEffect(() => {
    if (!open) {
      closeStreams()
      setFollowing(false)  // 再次打开仍默认显示最近 200 行（实时需手动开）
    }
  }, [open, closeStreams])

  // 卸载兜底：关闭所有 SSE
  useEffect(() => () => closeStreams(), [closeStreams])

  useEffect(() => { if (autoScroll && boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight }, [lines, autoScroll, collapsed, podFilter])

  const podNames = useMemo(() => Array.from(new Set(lines.map((l) => l.pod))).sort(), [lines])

  const toggleCollapse = (pod: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(pod)) next.delete(pod); else next.add(pod)
      return next
    })
  }

  // 关键字过滤：空格分隔多个关键字=同时命中（不区分大小写）
  const keywords = useMemo(
    () => keyword.trim().toLowerCase().split(/\s+/).filter(Boolean),
    [keyword],
  )

  const download = async () => {
    const token = localStorage.getItem('token') || ''
    const targets = effectiveContainers
    const multi = targets.length > 1
    const sections: string[] = []
    const hasRange = !!(timeRange && timeRange[0] && timeRange[1])
    for (const c of targets) {
      const p = new URLSearchParams({ container: c, all: 'true', tail: String(DOWNLOAD_TAIL) })
      if (timeRange && timeRange[0] && timeRange[1]) {
        p.set('start', String(Math.floor(timeRange[0].valueOf() / 1000)))
        p.set('end', String(Math.floor(timeRange[1].valueOf() / 1000)))
      }
      const url = `/api/v1/resources/clusters/${clusterId}/namespaces/${encodeURIComponent(namespace)}/workloads/${kind}/${name}/logs?${p.toString()}`
      try {
        const resp = await fetch(url, { headers: { Authorization: `Bearer ${token}` } })
        const body = await resp.json()
        if (body.code !== 200) { message.error(body.message || '下载失败'); continue }
        const data = body.data as AggregateData
        if (data.errors && data.errors.length) data.errors.forEach((e) => message.warning(`[${e.pod}] ${e.error}`))
        aggregateErrors(data).forEach((msg) => message.warning(msg))
        // 下载也只导当前筛选结果（关键字 / Pod / 只看错误），与页面上看到的一致
        const rows = (data.rows || []).filter((r) => {
          if (podFilter && r.pod !== podFilter) return false
          if (onlyErrors && !/\b(ERROR|ERR|FATAL|WARN|WARNING|Exception|panic)\b/i.test(r.line)) return false
          if (keywords.length) {
            const low = r.line.toLowerCase()
            if (!keywords.every((k) => low.includes(k))) return false
          }
          return true
        })
        const text = rows.map((r) => (multi ? `[${r.pod}][${c}] ${r.line}` : `[${r.pod}] ${r.line}`)).join('\n')
        if (text) sections.push(text)
      } catch (e) {
        message.error(String((e as Error)?.message || e))
      }
    }
    const text = sections.join('\n') + (sections.length ? '\n' : '')
    const filenameBase = {
      namespace,
      name,
      container: multi ? 'multi' : targets[0],
      tail: hasRange ? undefined : DOWNLOAD_TAIL,
      range: hasRange ? (timeRange as [Dayjs, Dayjs]) : undefined,
      filtered: keywords.length > 0,
      now: new Date(),
    }
    let blob: Blob
    let filename: string
    try {
      blob = await gzipBlob(text)
      filename = buildLogFilename(filenameBase)
    } catch {
      blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
      filename = buildLogFilename(filenameBase).replace(/\.gz$/, '')
    }
    const objectUrl = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = objectUrl
    a.download = filename
    a.click()
    setTimeout(() => URL.revokeObjectURL(objectUrl), 3000)
  }

  const shown = useMemo(() => lines.filter((l) => {
    if (podFilter && l.pod !== podFilter) return false
    if (collapsed.has(l.pod)) return false
    if (onlyErrors && !/\b(ERROR|ERR|FATAL|WARN|WARNING|Exception|panic)\b/i.test(l.line)) return false
    if (keywords.length) {
      const low = l.line.toLowerCase()
      if (!keywords.every((k) => low.includes(k))) return false
    }
    return true
  }), [lines, podFilter, collapsed, keywords, onlyErrors])

  const multi = effectiveContainers.length > 1

  return (
    <Modal open={open} onCancel={onClose} footer={null} width={1100} destroyOnClose maskClosable={false}
      title={<Space>聚合日志：{name}
        <Tag color="default" style={{ fontSize: 11 }}>{kind}</Tag>
        <span style={{ color: connected ? '#52c41a' : '#bfbfbf', fontSize: 12 }}>
          ● {following ? (connected ? '实时中' : '连接中…') : '最近 200 行'}
        </span>
        {truncated && !following && <Tag color="orange" style={{ fontSize: 11 }}>已截断</Tag>}
        <span style={{ color: '#8c8c8c', fontSize: 12 }}>{shown.length}/{lines.length} 行</span>
      </Space>}>
      <Space wrap style={{ marginBottom: 8 }}>
        <Select mode="multiple" style={{ width: 240 }} allowClear placeholder="容器(留空=全部)"
          value={selected.length ? selected : undefined}
          onChange={(v) => setSelected(v as string[])}
          options={containers.map((c) => ({ value: c, label: c }))} maxTagCount="responsive" />
        <Select style={{ width: 170 }} allowClear placeholder="过滤 Pod" value={podFilter || undefined}
          onChange={(v) => setPodFilter(v || '')} options={podNames.map((p) => ({ value: p, label: p }))} />
        <Input style={{ width: 220 }} allowClear value={keyword} onChange={(e) => setKeyword(e.target.value)}
          placeholder="关键字过滤（空格=同时包含）" />
        <Space size={4}><span>只看错误</span><Switch checked={onlyErrors} onChange={setOnlyErrors} /></Space>
        <Space size={4}><span>实时</span><Switch checked={following} onChange={setFollowing} /></Space>
        <Space size={4}><span>自动滚动</span><Switch checked={autoScroll} onChange={setAutoScroll} /></Space>
        <Button icon={<VerticalAlignBottomOutlined />} onClick={() => setAutoScroll((v) => !v)}>贴底</Button>
        <LogHighlightSettings rules={rules} onChange={onChangeRules} />
        <DatePicker.RangePicker size="small" showTime={{ format: 'HH:mm:ss' }} style={{ width: 320 }} allowClear
          value={timeRange} placeholder={['下载开始', '下载结束']}
          onChange={(v) => setTimeRange(v as [Dayjs | null, Dayjs | null] | null)} />
        <Button icon={<DownloadOutlined />} onClick={download}>下载(gzip)</Button>
      </Space>
      <pre ref={boxRef} style={{ maxHeight: '66vh', overflow: 'auto', background: '#1e1e1e', color: '#d4d4d4', padding: 12, fontSize: 12, whiteSpace: 'pre-wrap', margin: 0, fontFamily: 'monospace', borderRadius: 6 }}>
        {shown.length === 0
          ? <span style={{ color: '#888' }}>{following ? '(实时跟随中，等待新日志…)' : '(最近 200 行内没有日志)'}</span>
          : shown.map((l, i) => (
            <div key={i} style={{ display: 'flex' }}>
              <span onClick={() => toggleCollapse(l.pod)} title="点击折叠/展开该 Pod"
                style={{ color: podColor(l.pod), cursor: 'pointer', userSelect: 'none', marginRight: 8, flexShrink: 0 }}>[{l.pod}]</span>
              {multi && <span style={{ color: containerColor(l.container), marginRight: 8, flexShrink: 0 }}>[{l.container}]</span>}
              <span style={{ wordBreak: 'break-all' }}>
                {applyHighlight(l.line, rules).map((seg, j) => (
                  <span key={j} style={seg.color ? { color: seg.color, fontWeight: seg.bold ? 600 : undefined, background: `${seg.color}26` } : undefined}>{seg.text}</span>
                ))}
              </span>
            </div>
          ))}
      </pre>
    </Modal>
  )
}
