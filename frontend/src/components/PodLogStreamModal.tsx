// 实时日志跟随弹窗（P1-R5）：浏览器 ↔ api-server(WS) ↔ executor 日志流 HTTP（apiserver follow）
import { useEffect, useMemo, useRef, useState } from 'react'
import { Modal, Space, Input, Button, Select, Switch, message } from 'antd'
import { PauseCircleOutlined, PlayCircleOutlined, ClearOutlined, VerticalAlignBottomOutlined, DownloadOutlined } from '@ant-design/icons'
import { getPodContainers, downloadPodLogs } from '../services/api'

interface LogPod { clusterId: number; namespace: string; name: string }
interface Props { pod: LogPod | null; onClose: () => void }
interface LogRow { container: string; line: string }

// 默认（暂停态）取最近 200 行、不限时间；点「实时日志」才切到 2 分钟增量跟随。
// 2026-11-23 修复：原默认 since_seconds=120 对「2 分钟内无输出」的服务返回 0 字节，
// 弹窗会一直卡在「等待日志…」，看起来像日志功能坏了。
const DEFAULT_TAIL = 200
const LIVE_WINDOW_SECONDS = 120

function buildWsUrl(clusterId: number, ns: string, pod: string, container: string, follow: boolean,
  previous: boolean, sinceSeconds: number, tail: number) {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const token = localStorage.getItem('token') || ''
  const p = new URLSearchParams({
    token, container, follow: String(follow), previous: String(previous), tail: String(tail),
  })
  if (sinceSeconds > 0) p.set('since_seconds', String(sinceSeconds))
  return `${proto}//${window.location.host}/api/v1/logs/ws/clusters/${clusterId}/namespaces/${ns}/pods/${pod}/logs?${p.toString()}`
}

const MAX_LINES = 5000

// 多容器 Pod（应用 + filebeat 等 sidecar）必须指定容器才能取日志；
// 默认选第一个非 sidecar，避免后端报 "a container name must be specified"。
const SIDECAR_NAMES = ['filebeat', 'fluent-bit', 'fluentd', 'logstash', 'istio-proxy', 'envoy',
  'linkerd-proxy', 'sidecar', 'logtail', 'vector']
function pickDefaultContainer(list: string[]): string {
  return list.find((c) => !SIDECAR_NAMES.includes(c.toLowerCase())) || list[0] || ''
}

// 容器前缀配色（与工作负载聚合日志 pod 配色同一思路：名字哈希 → 调色板）
const PALETTE = ['#4ec9b0', '#569cd6', '#dcdcaa', '#c586c0', '#ce9178', '#9cdcfe', '#b5cea8', '#e5c07b', '#f44747', '#7fdbca']
function containerColor(name: string): string {
  let h = 0
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0
  return PALETTE[h % PALETTE.length]
}

async function gzipBlob(text: string): Promise<Blob> {
  const input = new Blob([text]).stream()
  const compressed = input.pipeThrough(new CompressionStream('gzip'))
  return new Response(compressed).blob()
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a'); a.href = url; a.download = filename; a.click()
  setTimeout(() => URL.revokeObjectURL(url), 3000)
}

export default function PodLogStreamModal({ pod, onClose }: Props) {
  const [containers, setContainers] = useState<string[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [live, setLive] = useState(false)   // false=最近 2 分钟快照(暂停) / true=实时跟随
  const [previous, setPrevious] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [rows, setRows] = useState<LogRow[]>([])
  const [paused, setPaused] = useState(false)
  const [connected, setConnected] = useState(false)
  const [autoScroll, setAutoScroll] = useState(true)
  const wsMapRef = useRef<Map<string, WebSocket>>(new Map())
  const boxRef = useRef<HTMLPreElement>(null)
  const bufRef = useRef<Map<string, string>>(new Map())       // 每容器未完成行的缓冲
  const pendingRef = useRef<Map<string, string>>(new Map())   // 每容器暂停期间后台继续收的缓冲
  const pausedRef = useRef(false)
  const openCountRef = useRef(0)
  useEffect(() => { pausedRef.current = paused }, [paused])

  // 留空 = 全部容器
  const effectiveContainers = useMemo(
    () => (selected.length ? selected : containers),
    [selected, containers],
  )

  const closeAllWs = () => {
    wsMapRef.current.forEach((w) => { w.onclose = null; w.onerror = null; try { w.close() } catch { /* ignore */ } })
    wsMapRef.current.clear()
    openCountRef.current = 0
    setConnected(false)
  }

  const appendContainerText = (c: string, text: string) => {
    const prev = bufRef.current.get(c) || ''
    const arr = (prev + text).split('\n')
    bufRef.current.set(c, arr.pop() || '')
    if (!arr.length) return
    const newRows: LogRow[] = arr.map((line) => ({ container: c, line }))
    setRows((r) => { const n = [...r, ...newRows]; return n.length > MAX_LINES ? n.slice(n.length - MAX_LINES) : n })
  }

  const flushPending = () => {
    pendingRef.current.forEach((text, c) => { if (text) appendContainerText(c, text) })
    pendingRef.current.clear()
  }

  const connect = () => {
    if (!pod) return
    closeAllWs()
    setRows([])
    bufRef.current.clear()
    pendingRef.current.clear()
    const targets = effectiveContainers
    for (const c of targets) {
      const ws = new WebSocket(buildWsUrl(
        pod.clusterId, pod.namespace, pod.name, c, live, previous,
        live ? LIVE_WINDOW_SECONDS : 0, DEFAULT_TAIL,
      ))
      wsMapRef.current.set(c, ws)
      ws.onopen = () => { openCountRef.current++; setConnected(true) }
      ws.onclose = () => {
        wsMapRef.current.delete(c)
        openCountRef.current = Math.max(0, openCountRef.current - 1)
        if (openCountRef.current === 0) setConnected(false)
      }
      ws.onerror = () => { /* 断连由 onclose 兜底 */ }
      ws.onmessage = (ev) => {
        const text = typeof ev.data === 'string' ? ev.data : ''
        if (!text) return
        if (text.startsWith('{"type"')) {
          try {
            const j = JSON.parse(text)
            if (j.message) {
              // info = 服务端提示（如「该范围无日志」），error = 真实错误
              const errText = j.type === 'info' ? `— ${j.message}\n` : `[错误] ${j.message}\n`
              if (pausedRef.current) pendingRef.current.set(c, (pendingRef.current.get(c) || '') + errText)
              else appendContainerText(c, errText)
            }
          } catch { /* ignore */ }
          return
        }
        if (pausedRef.current) { pendingRef.current.set(c, (pendingRef.current.get(c) || '') + text); return }
        appendContainerText(c, text)
      }
    }
  }

  useEffect(() => {
    if (!pod) return
    setContainers([]); setSelected([]); setRows([]); setKeyword(''); bufRef.current.clear(); pendingRef.current.clear()
    getPodContainers(pod.clusterId, pod.namespace, pod.name)
      .then((d) => {
        const list = (d as unknown as { containers?: string[] }).containers || []
        setContainers(list)
        const def = pickDefaultContainer(list)
        setSelected(def ? [def] : [])
      }).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pod])

  // pod / 容器 / 跟随 / 重启前 变化 → 重连
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (pod && effectiveContainers.length) connect(); return () => closeAllWs() }, [pod, effectiveContainers, live, previous])

  useEffect(() => { if (autoScroll && boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight }, [rows, autoScroll])

  if (!pod) return null
  const multi = effectiveContainers.length > 1
  const shown = keyword ? rows.filter((r) => r.line.includes(keyword)) : rows
  const colorize = (l: string) => {
    if (/\bERROR\b/i.test(l)) return { color: '#ff7875' }
    if (/\bWARN(ING)?\b/i.test(l)) return { color: '#ffc069' }
    if (/\bINFO\b/i.test(l)) return { color: '#95de64' }
    return undefined
  }
  const download = async () => {
    if (!pod) return
    try {
      const targets = effectiveContainers
      if (targets.length === 1) {
        // 单容器：沿用原有整包 gzip 下载
        const blob = await downloadPodLogs(pod.clusterId, pod.namespace, pod.name, { container: targets[0], previous, all: true })
        saveBlob(blob, `${pod.namespace}_${pod.name}.log.gz`)
      } else {
        // 多容器：逐容器取纯文本，按 [容器名] 分段拼接后再客户端 gzip
        const parts: string[] = []
        for (const c of targets) {
          const blob = await downloadPodLogs(pod.clusterId, pod.namespace, pod.name, { container: c, gzip: 0 })
          const text = await blob.text()
          parts.push(`========== [${c}] ==========\n${text.replace(/\n+$/, '')}`)
        }
        const gz = await gzipBlob(parts.join('\n') + '\n')
        saveBlob(gz, `${pod.namespace}_${pod.name}.log.gz`)
      }
    } catch (e) { message.error(String((e as Error)?.message || e)) }
  }
  return (
    <Modal open maskClosable={false} title={<Space>实时日志：{pod.name}
      <span style={{ color: connected ? '#52c41a' : '#bfbfbf', fontSize: 12 }}>
        ● {live ? (connected ? '实时' : '连接中…') : '最近 200 行'}
      </span>
    </Space>} footer={null} onCancel={onClose} width={1000} destroyOnClose>
      <Space wrap style={{ marginBottom: 8 }}>
        <Select mode="multiple" style={{ width: 240 }} allowClear placeholder="容器(留空=全部)"
          value={selected.length ? selected : undefined}
          onChange={(v) => setSelected(v as string[])}
          options={containers.map((c) => ({ value: c, label: c }))} maxTagCount="responsive" />
        <Space size={4}><span>重启前</span><Switch checked={previous} onChange={setPrevious} /></Space>
        <Input placeholder="关键字过滤" value={keyword} onChange={(e) => setKeyword(e.target.value)} style={{ width: 150 }} allowClear />
        <Button icon={<ClearOutlined />} onClick={() => { setRows([]); bufRef.current.clear(); pendingRef.current.clear() }}>清空</Button>
        {(!live || paused)
          ? <Button type="primary" icon={<PlayCircleOutlined />} onClick={() => {
            // 进入/恢复实时：暂停期间的缓冲一次性补齐，再继续渲染
            setPaused(false)
            pausedRef.current = false
            if (!live) setLive(true)
            flushPending()
          }}>{live ? '恢复' : '实时日志'}</Button>
          : <Button icon={<PauseCircleOutlined />} onClick={() => {
            // 暂停 = 停渲染但后台继续收（socket 不断开）
            pausedRef.current = true
            setPaused(true)
          }}>暂停</Button>}
        <Button icon={<VerticalAlignBottomOutlined />} onClick={() => setAutoScroll((v) => !v)}>{autoScroll ? '取消贴底' : '自动贴底'}</Button>
        <Button icon={<DownloadOutlined />} onClick={download}>下载</Button>
        <Button type="primary" onClick={connect}>重连</Button>
      </Space>
      <pre ref={boxRef} style={{ maxHeight: '66vh', overflow: 'auto', background: '#1e1e1e', color: '#d4d4d4', padding: 12, fontSize: 12, whiteSpace: 'pre-wrap', margin: 0, fontFamily: 'monospace' }}>
        {shown.length === 0
          ? <span style={{ color: '#888' }}>
            {!connected
              ? '(连接中…)'
              : live
                ? '(实时跟随中，等待新日志…)'
                : '(最近 200 行内没有日志，点「实时日志」持续跟随)'}
          </span>
          : shown.map((r, i) => (
            <div key={i} style={colorize(r.line)}>
              {multi && <span style={{ color: containerColor(r.container), marginRight: 4 }}>[{r.container}]</span>}
              {r.line}
            </div>
          ))}
      </pre>
    </Modal>
  )
}
