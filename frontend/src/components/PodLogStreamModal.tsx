// 实时日志跟随弹窗（P1-R5）：浏览器 ↔ api-server(WS) ↔ executor 日志流 HTTP（apiserver follow）
import { useEffect, useRef, useState } from 'react'
import { Modal, Space, Input, Button, Select, Switch, message } from 'antd'
import { PauseCircleOutlined, PlayCircleOutlined, ClearOutlined, VerticalAlignBottomOutlined, DownloadOutlined } from '@ant-design/icons'
import { getPodContainers, downloadPodLogs } from '../services/api'

interface LogPod { clusterId: number; namespace: string; name: string }
interface Props { pod: LogPod | null; onClose: () => void }

function buildWsUrl(clusterId: number, ns: string, pod: string, container: string, tail: number, follow: boolean, previous: boolean) {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const token = localStorage.getItem('token') || ''
  const p = new URLSearchParams({ token, container, tail: String(tail), follow: String(follow), previous: String(previous) })
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


export default function PodLogStreamModal({ pod, onClose }: Props) {
  const [containers, setContainers] = useState<string[]>([])
  const [container, setContainer] = useState('')
  const [tail, setTail] = useState(200)
  const [follow, setFollow] = useState(true)
  const [previous, setPrevious] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [lines, setLines] = useState<string[]>([])
  const [paused, setPaused] = useState(false)
  const [connected, setConnected] = useState(false)
  const [autoScroll, setAutoScroll] = useState(true)
  const wsRef = useRef<WebSocket | null>(null)
  const boxRef = useRef<HTMLPreElement>(null)
  const bufRef = useRef('')
  const pausedRef = useRef(false)
  useEffect(() => { pausedRef.current = paused }, [paused])

  const closeWs = () => {
    const w = wsRef.current
    if (w) { w.onclose = null; try { w.close() } catch { /* ignore */ } wsRef.current = null }
    setConnected(false)
  }
  const appendText = (text: string) => {
    bufRef.current += text
    const arr = bufRef.current.split('\n')
    bufRef.current = arr.pop() || ''
    if (arr.length) setLines((l) => { const n = [...l, ...arr]; return n.length > MAX_LINES ? n.slice(n.length - MAX_LINES) : n })
  }
  const connect = () => {
    if (!pod) return
    closeWs(); setLines([]); bufRef.current = ''
    const ws = new WebSocket(buildWsUrl(pod.clusterId, pod.namespace, pod.name, container, tail, follow, previous))
    wsRef.current = ws
    ws.onopen = () => setConnected(true)
    ws.onclose = () => setConnected(false)
    ws.onerror = () => setConnected(false)
    ws.onmessage = (ev) => {
      const text = typeof ev.data === 'string' ? ev.data : ''
      if (!text) return
      if (text.startsWith('{"type"')) { try { const j = JSON.parse(text); if (j.message) { setLines((l) => [...l, `[错误] ${j.message}`]); return } } catch { /* ignore */ } }
      if (pausedRef.current) { bufRef.current += text; return }
      appendText(text)
    }
  }

  useEffect(() => {
    if (!pod) return
    setContainer(''); setLines([]); setKeyword(''); bufRef.current = ''
    getPodContainers(pod.clusterId, pod.namespace, pod.name)
      .then((d) => {
        const list = (d as unknown as { containers?: string[] }).containers || []
        setContainers(list)
        // 多容器时给个默认容器（否则后端 502「取日志失败」）
        setContainer(pickDefaultContainer(list))
      }).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pod])

  // pod / 容器 / 行数 / 跟随 / 重启前 变化 → 重连
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (pod) connect(); return () => closeWs() }, [pod, container, tail, follow, previous])

  useEffect(() => { if (autoScroll && boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight }, [lines, autoScroll])

  if (!pod) return null
  const shown = keyword ? lines.filter((l) => l.includes(keyword)) : lines
  const colorize = (l: string) => {
    if (/\bERROR\b/i.test(l)) return { color: '#ff7875' }
    if (/\bWARN(ING)?\b/i.test(l)) return { color: '#ffc069' }
    if (/\bINFO\b/i.test(l)) return { color: '#95de64' }
    return undefined
  }
  return (
    <Modal open title={<Space>实时日志：{pod.name}
      <span style={{ color: connected ? '#52c41a' : '#bfbfbf', fontSize: 12 }}>● {connected ? '已连接' : '未连接'}</span>
    </Space>} footer={null} onCancel={onClose} width={1000} destroyOnClose>
      <Space wrap style={{ marginBottom: 8 }}>
        <Select style={{ width: 170 }} allowClear placeholder="容器(默认)" value={container || undefined}
          onChange={(v) => setContainer(v || '')} options={containers.map((c) => ({ value: c, label: c }))} />
        <Input type="number" value={tail} onChange={(e) => setTail(Number(e.target.value) || 200)} style={{ width: 96 }} addonBefore="行数" />
        <Space size={4}><span>跟随</span><Switch checked={follow} onChange={setFollow} /></Space>
        <Space size={4}><span>重启前</span><Switch checked={previous} onChange={setPrevious} /></Space>
        <Input placeholder="关键字过滤" value={keyword} onChange={(e) => setKeyword(e.target.value)} style={{ width: 150 }} allowClear />
        <Button icon={<ClearOutlined />} onClick={() => { setLines([]); bufRef.current = '' }}>清空</Button>
        <Button icon={paused ? <PlayCircleOutlined /> : <PauseCircleOutlined />}
          onClick={() => { if (paused) { appendText(''); setPaused(false) } else setPaused(true) }}>{paused ? '继续' : '暂停'}</Button>
        <Button icon={<VerticalAlignBottomOutlined />} onClick={() => setAutoScroll((v) => !v)}>{autoScroll ? '取消贴底' : '自动贴底'}</Button>
        <Button icon={<DownloadOutlined />} onClick={async () => {
          if (!pod) return
          try {
            const blob = await downloadPodLogs(pod.clusterId, pod.namespace, pod.name, { container, tail, previous })
            const url = URL.createObjectURL(blob)
            const a = document.createElement('a'); a.href = url; a.download = `${pod.namespace}_${pod.name}.log.gz`; a.click()
            setTimeout(() => URL.revokeObjectURL(url), 3000)
          } catch (e) { message.error(String((e as Error)?.message || e)) }
        }}>下载</Button>
        <Button type="primary" onClick={connect}>重连</Button>
      </Space>
      <pre ref={boxRef} style={{ maxHeight: '66vh', overflow: 'auto', background: '#1e1e1e', color: '#d4d4d4', padding: 12, fontSize: 12, whiteSpace: 'pre-wrap', margin: 0, fontFamily: 'monospace' }}>
        {shown.length === 0
          ? <span style={{ color: '#888' }}>{connected ? '(等待日志…)' : '(未连接)'}</span>
          : shown.map((l, i) => <div key={i} style={colorize(l)}>{l}</div>)}
      </pre>
    </Modal>
  )
}
