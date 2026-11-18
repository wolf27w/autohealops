// 工作负载聚合日志弹窗（R5 剩余）：快照 + 跟随(SSE) + 下载(gzip)
// 数据源：
//   快照  GET /api/v1/resources/clusters/{id}/namespaces/{ns}/workloads/{kind}/{name}/logs
//   跟随  GET .../logs/stream（EventSource，SSE，token 走 query param）
//   下载  客户端把聚合 rows 合并成带 `[pod] ` 前缀的文本，CompressionStream 压 gzip
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Modal, Space, Button, Select, Switch, Input, message, Tag } from 'antd'
import { DownloadOutlined, PlayCircleOutlined, PauseCircleOutlined, VerticalAlignBottomOutlined } from '@ant-design/icons'
import { getWorkloadContainers } from '../../services/api'

interface Props {
  clusterId: number
  namespace: string
  kind: string
  name: string
  open: boolean
  onClose: () => void
}

interface LogRow { pod: string; line: string }
interface AggregateData { columns: string[]; rows: LogRow[]; truncated: boolean; pods: number; errors: { pod: string; error: string }[] }

const MAX_LINES = 5000
const DOWNLOAD_TAIL = 5000
const PALETTE = ['#4ec9b0', '#569cd6', '#dcdcaa', '#c586c0', '#ce9178', '#9cdcfe', '#b5cea8', '#e5c07b', '#f44747', '#7fdbca']

function podColor(pod: string): string {
  let h = 0
  for (let i = 0; i < pod.length; i++) h = (h * 31 + pod.charCodeAt(i)) >>> 0
  return PALETTE[h % PALETTE.length]
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
  const [container, setContainer] = useState('')
  const [following, setFollowing] = useState(false)
  const [paused, setPaused] = useState(false)          // 暂停=停渲染但后台继续收
  const pausedRef = useRef(false)
  const pendingRows = useRef<LogRow[]>([])
  const [connected, setConnected] = useState(false)
  const [autoScroll, setAutoScroll] = useState(true)
  const [lines, setLines] = useState<LogRow[]>([])
  const [podFilter, setPodFilter] = useState('')
  const [keyword, setKeyword] = useState('')          // 关键字过滤（空格分隔=同时包含）
  const [onlyErrors, setOnlyErrors] = useState(false) // 只看错误/异常
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [truncated, setTruncated] = useState(false)

  useEffect(() => { pausedRef.current = paused }, [paused])

  const esRef = useRef<EventSource | null>(null)
  const boxRef = useRef<HTMLPreElement | null>(null)
  const containerRef = useRef(container)
  useEffect(() => { containerRef.current = container }, [container])

  const closeStream = useCallback(() => {
    const es = esRef.current
    if (es) { es.onopen = null; es.close(); esRef.current = null }
    setConnected(false)
    setFollowing(false)
  }, [])

  const appendRows = useCallback((rows: LogRow[]) => {
    if (!rows.length) return
    setLines((prev) => {
      const next = [...prev, ...rows]
      return next.length > MAX_LINES ? next.slice(next.length - MAX_LINES) : next
    })
  }, [])

  // 快照加载（非跟随态）
  const DEFAULT_WINDOW_SECONDS = 120

  const loadSnapshot = useCallback(async (c: string) => {
    const token = localStorage.getItem('token') || ''
    const p = new URLSearchParams({ container: c, since_seconds: String(DEFAULT_WINDOW_SECONDS), tail: String(200) })
    const url = `/api/v1/resources/clusters/${clusterId}/namespaces/${encodeURIComponent(namespace)}/workloads/${kind}/${name}/logs?${p.toString()}`
    try {
      const resp = await fetch(url, { headers: { Authorization: `Bearer ${token}` } })
      const body = await resp.json()
      if (body.code !== 200) { message.error(body.message || '加载日志失败'); return }
      const data = body.data as AggregateData
      setLines(data.rows || [])
      setTruncated(Boolean(data.truncated))
      if (data.errors && data.errors.length) {
        data.errors.forEach((e) => message.error(`[${e.pod}] ${e.error}`))
      }
    } catch (e) {
      message.error(String((e as Error)?.message || e))
    }
  }, [clusterId, namespace, kind, name])

  // 开始跟随：清空，打开 SSE（SSE 先推快照，再每 2s 增量）
  const startFollow = useCallback(() => {
    closeStream()
    setLines([])
    setFollowing(true)
    const es = new EventSource(buildStreamUrl(clusterId, namespace, kind, name, containerRef.current))
    esRef.current = es
    es.onopen = () => setConnected(true)
    es.addEventListener('log', (ev) => {
      try {
        const j = JSON.parse((ev as MessageEvent).data as string) as LogRow
        if (j && typeof j.pod === 'string' && typeof j.line === 'string') {
          if (pausedRef.current) pendingRows.current.push(j)
          else appendRows([j])
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
        setConnected(false) // 连接断开（EventSource 会自动重连）
      }
    })
  }, [clusterId, namespace, kind, name, appendRows, closeStream])

  // 容器列表
  useEffect(() => {
    if (!open) return
    setContainer(''); setPodFilter(''); setCollapsed(new Set()); setTruncated(false)
    getWorkloadContainers(clusterId, kind, namespace, name)
      .then((d) => setContainers((d?.containers || []).map((c) => c.name)))
      .catch(() => setContainers([]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, clusterId, namespace, kind, name])

  // 非跟随态：打开 / 容器 / tail 变化 → 重载快照
  useEffect(() => {
    if (!open) return
    if (following) return
    loadSnapshot(container)
  }, [open, container, following, loadSnapshot])

  // 卸载 / 关闭：关闭 SSE
  useEffect(() => () => closeStream(), [closeStream])

  useEffect(() => { if (autoScroll && boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight }, [lines, autoScroll, collapsed, podFilter])

  const podNames = useMemo(() => Array.from(new Set(lines.map((l) => l.pod))).sort(), [lines])

  const toggleCollapse = (pod: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(pod)) next.delete(pod); else next.add(pod)
      return next
    })
  }

  const download = async () => {
    const token = localStorage.getItem('token') || ''
    const c = containerRef.current
    const p = new URLSearchParams({ container: c, all: 'true', tail: String(DOWNLOAD_TAIL) })
    const url = `/api/v1/resources/clusters/${clusterId}/namespaces/${encodeURIComponent(namespace)}/workloads/${kind}/${name}/logs?${p.toString()}`
    try {
      const resp = await fetch(url, { headers: { Authorization: `Bearer ${token}` } })
      const body = await resp.json()
      if (body.code !== 200) { message.error(body.message || '下载失败'); return }
      const data = body.data as AggregateData
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
      const text = rows.map((r) => `[${r.pod}] ${r.line}`).join('\n') + '\n'
      let blob: Blob
      let filename: string
      try {
        blob = await gzipBlob(text)
        filename = `${namespace}_${name}${keywords.length ? '_filtered' : ''}_${Date.now()}.log.gz`
      } catch {
        blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
        filename = `${namespace}_${name}${keywords.length ? '_filtered' : ''}_${Date.now()}.log`
      }
      const objectUrl = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = objectUrl
      a.download = filename
      a.click()
      setTimeout(() => URL.revokeObjectURL(objectUrl), 3000)
    } catch (e) {
      message.error(String((e as Error)?.message || e))
    }
  }

  // 关键字过滤：空格分隔多个关键字=同时命中（不区分大小写）
  const keywords = useMemo(
    () => keyword.trim().toLowerCase().split(/\s+/).filter(Boolean),
    [keyword],
  )
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

  const colorize = (l: string) => {
    if (/\bERROR\b/i.test(l)) return { color: '#ff7875' }
    if (/\bWARN(ING)?\b/i.test(l)) return { color: '#ffc069' }
    if (/\bINFO\b/i.test(l)) return { color: '#95de64' }
    return undefined
  }

  return (
    <Modal open={open} onCancel={onClose} footer={null} width={1100} destroyOnClose
      title={<Space>聚合日志：{name}
        <Tag color="default" style={{ fontSize: 11 }}>{kind}</Tag>
        <span style={{ color: connected ? '#52c41a' : '#bfbfbf', fontSize: 12 }}>
          ● {following ? (paused ? '已暂停（后台继续接收）' : (connected ? '实时' : '连接中…')) : '已暂停（最近 2 分钟）'}
        </span>
        {truncated && <Tag color="orange" style={{ fontSize: 11 }}>已截断</Tag>}
        <span style={{ color: '#8c8c8c', fontSize: 12 }}>{shown.length}/{lines.length} 行</span>
      </Space>}>
      <Space wrap style={{ marginBottom: 8 }}>
        <Select style={{ width: 170 }} allowClear placeholder="容器(默认=全部)" value={container || undefined}
          onChange={(v) => setContainer(v || '')} options={containers.map((c) => ({ value: c, label: c }))} />
        <Select style={{ width: 170 }} allowClear placeholder="过滤 Pod" value={podFilter || undefined}
          onChange={(v) => setPodFilter(v || '')} options={podNames.map((p) => ({ value: p, label: p }))} />
        <Input style={{ width: 220 }} allowClear value={keyword} onChange={(e) => setKeyword(e.target.value)}
          placeholder="关键字过滤（空格=同时包含）" />
        <Space size={4}><span>只看错误</span><Switch checked={onlyErrors} onChange={setOnlyErrors} /></Space>
        {(!following || paused)
          ? <Button type="primary" icon={<PlayCircleOutlined />} onClick={() => {
            if (!following) { setPaused(false); pausedRef.current = false; startFollow() }
            else {
              setPaused(false); pausedRef.current = false
              const rest = pendingRows.current; pendingRows.current = []
              if (rest.length) appendRows(rest)
            }
          }}>{following ? '恢复' : '实时日志'}</Button>
          : <Button icon={<PauseCircleOutlined />} onClick={() => setPaused(true)}>暂停</Button>}
        <Space size={4}><span>自动滚动</span><Switch checked={autoScroll} onChange={setAutoScroll} /></Space>
        <Button icon={<VerticalAlignBottomOutlined />} onClick={() => setAutoScroll((v) => !v)}>贴底</Button>
        <Button icon={<DownloadOutlined />} onClick={download}>下载(gzip)</Button>
      </Space>
      <pre ref={boxRef} style={{ maxHeight: '66vh', overflow: 'auto', background: '#1e1e1e', color: '#d4d4d4', padding: 12, fontSize: 12, whiteSpace: 'pre-wrap', margin: 0, fontFamily: 'monospace', borderRadius: 6 }}>
        {shown.length === 0
          ? <span style={{ color: '#888' }}>{following ? '(等待日志…)' : '(无日志)'}</span>
          : shown.map((l, i) => (
            <div key={i} style={{ display: 'flex' }}>
              <span onClick={() => toggleCollapse(l.pod)} title="点击折叠/展开该 Pod"
                style={{ color: podColor(l.pod), cursor: 'pointer', userSelect: 'none', marginRight: 8, flexShrink: 0 }}>[{l.pod}]</span>
              <span style={{ ...(colorize(l.line) || {}), wordBreak: 'break-all' }}>{l.line}</span>
            </div>
          ))}
      </pre>
    </Modal>
  )
}
