// R8.5 会话回放：按录制块时间轴回放到 xterm.js，支持播放/暂停/倍速/进度/导出 .cast
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Alert, Button, Card, Descriptions, Select, Slider, Space, Spin, Tag, Typography } from 'antd'
import { CaretRightOutlined, PauseOutlined, RedoOutlined, DownloadOutlined, ArrowLeftOutlined } from '@ant-design/icons'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { getHostSessionReplay } from '../services/hostConsoleApi'
import { getHostSessionCast } from '../services/api'
import type { ReplayChunk } from '../services/hostConsoleApi'

// 解析 asciinema cast v2：首行 header(JSON) + 每行 [ts, "o"|"i", text]
function parseCast(text: string): { cols?: number; rows?: number; chunks: ReplayChunk[] } | null {
  const lines = String(text || '').trim().split('\n').filter(Boolean)
  if (!lines.length) return null
  let header: { version?: number; width?: number; height?: number } = {}
  try { header = JSON.parse(lines[0]) } catch { return null }
  if (!header || Number(header.version) !== 2) return null
  const chunks: ReplayChunk[] = []
  lines.slice(1).forEach((l, i) => {
    try {
      const arr = JSON.parse(l)
      if (!Array.isArray(arr) || arr.length < 3) return
      const [ts, dir, data] = arr
      chunks.push({
        seq: i,
        ts_ms: Math.round(Number(ts) * 1000),
        direction: dir === 'i' ? 'input' : 'output',
        text: String(data),
      })
    } catch { /* 跳过坏行 */ }
  })
  return { cols: Number(header.width) || undefined, rows: Number(header.height) || undefined, chunks }
}

export default function HostSessionReplay() {
  const { id } = useParams()
  const navigate = useNavigate()
  const boxRef = useRef<HTMLDivElement>(null)
  const termRef = useRef<Terminal | null>(null)
  const timerRef = useRef<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [chunks, setChunks] = useState<ReplayChunk[]>([])
  const [meta, setMeta] = useState<Record<string, unknown>>({})
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(1)
  const [cursor, setCursor] = useState(0) // 已回放块数
  const [progress, setProgress] = useState(0)

  const total = chunks.length
  const durationMs = useMemo(() => (total ? chunks[total - 1].ts_ms : 0), [chunks, total])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setLoading(true)
      try {
        const d = await getHostSessionReplay(Number(id))
        if (cancelled) return
        let list: ReplayChunk[] = d.chunks || []
        let m: Record<string, unknown> = d.session || {}
        // R43 H4：优先用新录像（asciinema cast），老会话没有 cast 时回落到录制块
        try {
          const parsed = parseCast(await getHostSessionCast(Number(id)))
          if (parsed && parsed.chunks.length) {
            list = parsed.chunks
            m = { ...m, cols: parsed.cols, rows: parsed.rows }
          }
        } catch { /* 404 / 无录像 → 用块回放 */ }
        setChunks(list)
        setMeta(m)
      } catch (e) {
        if (!cancelled) setError((e as Error).message || '加载回放失败')
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [id])

  useEffect(() => {
    const term = new Terminal({
      cursorBlink: false,
      fontSize: 13,
      fontFamily: 'Menlo, Monaco, "Courier New", monospace',
      scrollback: 5000,
      theme: { background: '#1e1e1e', foreground: '#d4d4d4' },
      disableStdin: true,
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(boxRef.current!)
    fit.fit()
    termRef.current = term
    const onResize = () => fit.fit()
    window.addEventListener('resize', onResize)
    return () => { window.removeEventListener('resize', onResize); term.dispose() }
  }, [])

  const stop = useCallback(() => {
    if (timerRef.current) { window.clearTimeout(timerRef.current); timerRef.current = null }
    setPlaying(false)
  }, [])

  const reset = useCallback(() => {
    stop()
    termRef.current?.reset()
    setCursor(0)
    setProgress(0)
  }, [stop])

  const stepPlay = useCallback((from: number, spd: number) => {
    if (from >= total) { setPlaying(false); return }
    const chunk = chunks[from]
    termRef.current?.write(chunk.text)
    const nextIdx = from + 1
    setCursor(nextIdx)
    setProgress(durationMs ? Math.round((chunk.ts_ms / durationMs) * 100) : 0)
    if (nextIdx >= total) { setPlaying(false); return }
    const wait = Math.max(0, ((chunks[nextIdx].ts_ms - chunk.ts_ms) / spd))
    timerRef.current = window.setTimeout(() => stepPlay(nextIdx, spd), wait)
  }, [chunks, durationMs, total])

  const play = useCallback(() => {
    if (!total) return
    if (cursor >= total) reset()
    setPlaying(true)
    stepPlay(cursor >= total ? 0 : cursor, speed)
  }, [cursor, reset, speed, stepPlay, total])

  const seek = (pct: number) => {
    stop()
    const target = Math.round((pct / 100) * total)
    const term = termRef.current
    if (term) {
      term.reset()
      for (let i = 0; i < target && i < total; i++) term.write(chunks[i].text)
    }
    setCursor(target)
    setProgress(pct)
  }

  const exportCast = () => {
    // asciinema v2 格式：首行 header + 每行 [time, "o", data]
    const head = JSON.stringify({ version: 2, width: Number(meta.cols || 120), height: Number(meta.rows || 32), timestamp: Date.now() / 1000 })
    const lines = [head]
    chunks.forEach((c) => {
      lines.push(JSON.stringify([c.ts_ms / 1000, c.direction === 'input' ? 'i' : 'o', c.text]))
    })
    const blob = new Blob([lines.join('\n') + '\n'], { type: 'application/x-asciicast' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `host-session-${id}.cast`
    a.click()
    setTimeout(() => URL.revokeObjectURL(a.href), 2000)
  }

  return (
    <div>
      <Space style={{ marginBottom: 12 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(-1)}>返回</Button>
        <Typography.Title level={4} style={{ margin: 0 }}>会话回放 #{id}</Typography.Title>
        {meta.host_name ? <Tag>{String(meta.host_name)}</Tag> : null}
      </Space>
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 12 }} />}
      <Card size="small" style={{ marginBottom: 12 }}>
        <Descriptions size="small" column={4}>
          <Descriptions.Item label="主机">{String(meta.host_name || '-')}</Descriptions.Item>
          <Descriptions.Item label="用户">{String(meta.username || '-')}</Descriptions.Item>
          <Descriptions.Item label="开始时间">{String(meta.started_at || '-')}</Descriptions.Item>
          <Descriptions.Item label="时长">{String(meta.duration_sec || 0)}s</Descriptions.Item>
        </Descriptions>
      </Card>
      <Space style={{ marginBottom: 8 }} wrap>
        <Button type="primary" icon={playing ? <PauseOutlined /> : <CaretRightOutlined />} onClick={() => (playing ? stop() : play())} disabled={!total}>
          {playing ? '暂停' : '播放'}
        </Button>
        <Button icon={<RedoOutlined />} onClick={reset} disabled={!total}>重播</Button>
        <Select value={speed} onChange={setSpeed} style={{ width: 100 }} options={[0.5, 1, 2, 4, 8].map((v) => ({ value: v, label: `${v}x` }))} />
        {playing && <Tag color="green">回放中 {progress}%</Tag>}
        <Button icon={<DownloadOutlined />} onClick={exportCast} disabled={!total}>导出 .cast</Button>
      </Space>
      <Slider value={progress} onChange={seek} tooltip={{ formatter: (v) => `${v}%` }} />
      <div style={{ position: 'relative', height: 'calc(100vh - 340px)', minHeight: 320, background: '#1e1e1e', borderRadius: 8, padding: 8 }}>
        {loading && <Spin style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }} />}
        {!loading && !total && <Alert type="info" showIcon message="没有录制内容（该会话未录制或录制已过期清理）" style={{ margin: 16 }} />}
        <div ref={boxRef} style={{ height: '100%' }} />
      </div>
    </div>
  )
}
