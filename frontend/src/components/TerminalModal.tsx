// 交互式终端弹窗（xterm.js + WebSocket → api-server → executor → k8s exec TTY）
import { useEffect, useRef, useState } from 'react'
import { Modal, Input, Button, Select, Space, Alert } from 'antd'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { getPodContainers } from '../services/api'

// 将底层报错翻译成可读原因
function explainError(msg: string): string {
  const m = msg || ''
  if (/executable file not found|no such file or directory|OCI runtime exec failed|\.\/bin\/sh/.test(m)) {
    return '该容器镜像里没有 shell（如 distroless/scratch 镜像），无法进入终端。可改用「日志」「容器信息（describe/events）」查看。'
  }
  if (/a container name must be specified/.test(m)) {
    return '多容器 Pod，需指定容器名（请在上方「容器」下拉选择后重连）。'
  }
  if (/container not found|is not running|not found/.test(m)) {
    return '容器不存在或未运行（可能正在重启/已退出），无法进入终端。'
  }
  if (/forbidden|cannot|RBAC/.test(m)) {
    return '权限不足（RBAC 拒绝了 pods/exec），请确认账号具备该命名空间的操作权限。'
  }
  return m
}

function buildWsUrl(clusterId: number, ns: string, pod: string, container = ''): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const token = localStorage.getItem('token') || ''
  const c = container ? `&container=${encodeURIComponent(container)}` : ''
  return `${proto}//${window.location.host}/api/v1/ws/clusters/${clusterId}/namespaces/${ns}/pods/${pod}/exec?token=${token}${c}`
}

interface TerminalPod {
  clusterId: number
  namespace: string
  name: string
}

export default function TerminalModal({ pod, onClose }: { pod: TerminalPod | null; onClose: () => void }) {
  const [container, setContainer] = useState('')
  const [containerInput, setContainerInput] = useState('')
  const [containers, setContainers] = useState<string[]>([])
  const [errMsg, setErrMsg] = useState('')
  const containerRef = useRef<HTMLDivElement>(null)
  const [connected, setConnected] = useState(false)
  const termRef = useRef<Terminal | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const fitRef = useRef<FitAddon | null>(null)

  useEffect(() => {
    if (!pod) return
    setContainer('')
    setContainerInput('')
    setErrMsg('')
    setContainers([])
    getPodContainers(pod.clusterId, pod.namespace, pod.name)
      .then((d) => setContainers((d as unknown as { containers?: string[] }).containers || []))
      .catch(() => {})
  }, [pod])

  useEffect(() => {
    if (!pod) return
    const term = new Terminal({
      cursorBlink: true,
      fontSize: 13,
      fontFamily: 'Menlo, Monaco, "Courier New", monospace',
      theme: { background: '#1e1e1e', foreground: '#d4d4d4' },
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(containerRef.current!)
    fit.fit()
    termRef.current = term
    fitRef.current = fit

    const ws = new WebSocket(buildWsUrl(pod.clusterId, pod.namespace, pod.name, container))
    wsRef.current = ws
    ws.binaryType = 'arraybuffer'

    ws.onopen = () => {
      setConnected(true)
      term.focus()
      term.writeln('\x1b[32m已连接交互式终端（/bin/sh）\x1b[0m')
    }
    ws.onmessage = (ev) => {
      if (typeof ev.data === 'string') {
        try {
          const j = JSON.parse(ev.data)
          if (j.type === 'exit') {
            const hint = j.code === 130 ? '（静默超时自动断开）' : ''
            term.writeln(`\r\n\x1b[33m[会话结束，退出码 ${j.code}${hint}]\x1b[0m`)
            setConnected(false)
          } else if (j.type === 'error') {
            const friendly = explainError(j.message)
            setErrMsg(friendly)
            term.writeln(`\r\n\x1b[31m[无法进入容器] ${friendly}\x1b[0m`)
          }
        } catch { /* 非 JSON 文本忽略 */ }
      } else {
        term.write(new Uint8Array(ev.data))
      }
    }
    ws.onerror = () => { term.writeln('\r\n\x1b[31m[连接错误]\x1b[0m'); setErrMsg('WebSocket 连接错误（后端/executor 不可达？）') }
    ws.onclose = () => { setConnected(false); term.writeln('\r\n\x1b[33m[连接已关闭]\x1b[0m') }

    // 键盘输入 → stdin
    const dataDisposable = term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(data)
    })
    // resize → WebSocket JSON
    const resizeDisposable = term.onResize(({ cols, rows }) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'resize', cols, rows }))
      }
    })

    const onWindowResize = () => { try { fit.fit() } catch { /* ignore */ } }
    window.addEventListener('resize', onWindowResize)

    return () => {
      window.removeEventListener('resize', onWindowResize)
      dataDisposable.dispose()
      resizeDisposable.dispose()
      ws.close()
      term.dispose()
      termRef.current = null
      wsRef.current = null
    }
  }, [pod, container])

  if (!pod) return null
  return (
    <Modal
      title={`交互式终端：${pod.namespace}/${pod.name}${connected ? '' : '（连接中...）'}`}
      open={!!pod}
      onCancel={onClose}
      footer={null}
      width={900}
      destroyOnClose
    >
      <div style={{ marginBottom: 8, display: 'flex', gap: 8, alignItems: 'center' }}>
        <Select
          size="small"
          style={{ width: 300 }}
          placeholder={`容器（留空=自动选择）${containers.length ? '：' + containers.join(' / ') : ''}`}
          value={containerInput || undefined}
          onChange={(v) => setContainerInput(v || '')}
          allowClear
          options={containers.map((c) => ({ value: c, label: c }))}
        />
        <Button size="small" type="primary" onClick={() => { setErrMsg(''); setContainer(containerInput.trim()) }}>连接</Button>
        {containers.length === 0 && <span style={{ color: '#bbb', fontSize: 12 }}>（未取到容器列表，可留空自动选择）</span>}
      </div>
      {errMsg && <Alert type="warning" showIcon style={{ marginBottom: 8 }} message={errMsg} />}
      <div
        ref={containerRef}
        style={{ height: '56vh', background: '#1e1e1e', borderRadius: 4, padding: 8 }}
      />
      <div style={{ fontSize: 12, color: '#999', marginTop: 6 }}>
        交互式终端（/bin/sh），支持 Tab 补全、方向键；权限由 k8s RBAC 管控，命令审计留痕；静默 5 分钟自动断开。
      </div>
    </Modal>
  )
}
