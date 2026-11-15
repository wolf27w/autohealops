// R8：堡垒机 Web 终端（类 JumpServer 布局）——左栏分组树 + 主机，右侧多标签 SSH 会话
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Alert, Button, Drawer, Empty, Input, List, Popconfirm, Space, Spin, Table, Tabs, Tag, Tooltip, Typography, Upload, message,
} from 'antd'
import type { UploadProps } from 'antd'
import {
  CloseOutlined, DownloadOutlined, FullscreenOutlined, HistoryOutlined, LoginOutlined, ReloadOutlined,
  SearchOutlined, FolderOpenOutlined, UploadOutlined, FileOutlined, FolderOutlined, DeleteOutlined,
} from '@ant-design/icons'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { getHosts, getHostGroups } from '../services/api'
import type { HostItem, HostGroup } from '../services/api'
import {
  buildHostTerminalWsUrl, getHostSessions, kickHostSession, sftpDownload, sftpList, sftpUpload,
} from '../services/hostConsoleApi'
import type { HostSessionItem, SftpEntry } from '../services/hostConsoleApi'
import { isAdmin } from '../utils/auth'

interface OpenTab { key: string; hostId: number; hostName: string; connected: boolean }

const FONT_SIZES = [12, 13, 14, 16, 18]

function TerminalPane({ host, fontSize, onStatus, onSessionId }: {
  host: HostItem
  fontSize: number
  onStatus: (connected: boolean) => void
  onSessionId: (id: number | null) => void
}) {
  const boxRef = useRef<HTMLDivElement>(null)
  const termRef = useRef<Terminal | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const fitRef = useRef<FitAddon | null>(null)
  const [error, setError] = useState('')
  const [connected, setConnected] = useState(false)

  const connect = useCallback(() => {
    const term = termRef.current
    if (!term) return
    setError('')
    term.reset()
    term.writeln(`\x1b[90m正在连接 ${host.name}（${host.address}:${host.ssh_port}）...\x1b[0m`)
    const fit = fitRef.current
    if (fit) fit.fit()
    const cols = term.cols || 120
    const rows = term.rows || 32
    const ws = new WebSocket(buildHostTerminalWsUrl(host.id, { cols, rows }))
    ws.binaryType = 'arraybuffer'
    wsRef.current = ws

    ws.onmessage = (ev) => {
      if (typeof ev.data === 'string') {
        try {
          const msg = JSON.parse(ev.data)
          if (msg.type === 'ready') {
            setConnected(true)
            onStatus(true)
            onSessionId(msg.session_id)
            term.writeln(`\x1b[32m已连接（会话 #${msg.session_id}，空闲 ${Math.round((msg.idle_timeout || 600) / 60)} 分钟超时）\x1b[0m`)
          } else if (msg.type === 'error') {
            setError(msg.message || '连接异常')
            setConnected(false)
            onStatus(false)
          }
        } catch { /* 非 JSON 文本当输出 */ }
        return
      }
      const buf = new Uint8Array(ev.data as ArrayBuffer)
      term.write(buf)
    }
    ws.onclose = (e) => {
      setConnected(false)
      onStatus(false)
      onSessionId(null)
      if (e.code !== 1000) term.writeln(`\r\n\x1b[33m连接已关闭（code=${e.code}${e.reason ? ' ' + e.reason : ''}）\x1b[0m`)
    }
    ws.onerror = () => setError('WebSocket 连接失败')
  }, [host, onSessionId, onStatus])

  useEffect(() => {
    const term = new Terminal({
      cursorBlink: true,
      fontSize,
      fontFamily: 'Menlo, Monaco, "Courier New", monospace',
      scrollback: 5000,
      theme: { background: '#1e1e1e', foreground: '#d4d4d4' },
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(boxRef.current!)
    fit.fit()
    termRef.current = term
    fitRef.current = fit
    term.onData((data) => {
      const ws = wsRef.current
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(data)
    })
    term.onResize(({ cols, rows }) => {
      const ws = wsRef.current
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'resize', cols, rows }))
    })
    connect()
    const onWinResize = () => fit.fit()
    window.addEventListener('resize', onWinResize)
    return () => {
      window.removeEventListener('resize', onWinResize)
      wsRef.current?.close()
      term.dispose()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (termRef.current) termRef.current.options.fontSize = fontSize
    fitRef.current?.fit()
  }, [fontSize])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {error && <Alert type="warning" showIcon message={error} style={{ margin: '8px 8px 0' }} closable onClose={() => setError('')} />}
      <div ref={boxRef} style={{ flex: 1, minHeight: 0, padding: '4px 8px', background: '#1e1e1e' }} />
      <Space style={{ padding: '6px 8px' }}>
        <Tag color={connected ? 'green' : 'default'}>{connected ? '已连接' : '未连接'}</Tag>
        <Button size="small" icon={<ReloadOutlined />} onClick={() => { wsRef.current?.close(); setTimeout(connect, 300) }}>重连</Button>
        <Button size="small" icon={<CloseOutlined />} onClick={() => wsRef.current?.close()}>断开</Button>
      </Space>
    </div>
  )
}

function SftpPanel({ host }: { host: HostItem }) {
  const [path, setPath] = useState('/tmp')
  const [entries, setEntries] = useState<SftpEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [pct, setPct] = useState<number | null>(null)

  const load = useCallback(async (p: string) => {
    setLoading(true)
    try {
      const d = await sftpList(host.id, p)
      setEntries(d.entries || [])
      setPath(d.path)
    } catch (e) {
      message.error((e as Error).message || '目录读取失败')
    } finally {
      setLoading(false)
    }
  }, [host.id])

  useEffect(() => { load('/tmp') }, [load])

  const uploadProps: UploadProps = {
    multiple: true,
    showUploadList: false,
    beforeUpload: async (file) => {
      setPct(0)
      try {
        await sftpUpload(host.id, path, file as File, (p) => setPct(p))
        message.success(`已上传 ${file.name}`)
        load(path)
      } catch (e) {
        message.error((e as Error).message || '上传失败')
      } finally {
        setPct(null)
      }
      return false
    },
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <Space style={{ padding: 8, flexWrap: 'wrap' }}>
        <Input
          value={path}
          onChange={(e) => setPath(e.target.value)}
          onPressEnter={() => load(path)}
          prefix={<FolderOpenOutlined />}
          style={{ width: 260 }}
        />
        <Button size="small" onClick={() => {
          const parent = path.replace(/\/[^/]+\/?$/, '') || '/'
          load(parent.startsWith('/tmp') || parent.startsWith('/home') || parent === '/' ? parent : '/tmp')
        }}>上级</Button>
        <Upload {...uploadProps}>
          <Button size="small" icon={<UploadOutlined />}>上传到 {path}</Button>
        </Upload>
        {pct !== null && <Tag color="blue">上传中 {pct}%</Tag>}
      </Space>
      <div style={{ flex: 1, overflow: 'auto' }}>
        <Table<SftpEntry>
          size="small"
          rowKey="path"
          loading={loading}
          dataSource={entries}
          pagination={false}
          columns={[
            {
              title: '名称', dataIndex: 'name',
              render: (v: string, r) => (
                <Space>
                  {r.is_dir ? <FolderOutlined /> : <FileOutlined />}
                  {r.is_dir
                    ? <a onClick={() => load(r.path)}>{v}</a>
                    : <span>{v}</span>}
                </Space>
              ),
            },
            { title: '大小', dataIndex: 'size', width: 100, render: (v: number) => (v > 1024 * 1024 ? `${(v / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(v / 1024))} KB`) },
            { title: '权限', dataIndex: 'perm', width: 80 },
            {
              title: '操作', width: 90,
              render: (_: unknown, r) => (!r.is_dir
                ? <Button size="small" type="link" icon={<DownloadOutlined />} onClick={() => sftpDownload(host.id, r.path).catch((e) => message.error(e.message))}>下载</Button>
                : null),
            },
          ]}
        />
      </div>
      <Typography.Text type="secondary" style={{ padding: 8, fontSize: 12 }}>
        允许范围：/tmp、/var/tmp、/home（系统设置 HOST_SFTP_ALLOW 可调）；单文件默认上限 200MB
      </Typography.Text>
    </div>
  )
}

export default function WebTerminal({ standalone = false }: { standalone?: boolean }) {
  const { hostId } = useParams()
  const navigate = useNavigate()
  const [groups, setGroups] = useState<HostGroup[]>([])
  const [hosts, setHosts] = useState<HostItem[]>([])
  const [keyword, setKeyword] = useState('')
  const [groupFilter, setGroupFilter] = useState<number | 'all'>('all')
  const [loading, setLoading] = useState(false)
  const [tabs, setTabs] = useState<OpenTab[]>([])
  const [activeKey, setActiveKey] = useState('')
  const [fontSize, setFontSize] = useState(13)
  const [sftpOpen, setSftpOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [sessions, setSessions] = useState<HostSessionItem[]>([])
  const [sessionBusy, setSessionBusy] = useState<number | null>(null)
  const [sessionIds, setSessionIds] = useState<Record<string, number | null>>({})

  const admin = isAdmin()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [hs, gs] = await Promise.all([getHosts(), getHostGroups()])
      setHosts(hs || [])
      setGroups(gs?.groups || [])
    } catch {
      /* 拦截器已提示 */
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const openHost = useCallback((h: HostItem) => {
    const key = `h-${h.id}`
    setTabs((prev) => (prev.some((t) => t.key === key) ? prev : [...prev, { key, hostId: h.id, hostName: h.name, connected: false }]))
    setActiveKey(key)
    if (!standalone) navigate(`/resources/terminal/${h.id}`, { replace: true })
  }, [navigate, standalone])

  // 直达 /web-terminal/:hostId
  useEffect(() => {
    if (standalone) return
    if (!hostId || !hosts.length) return
    const h = hosts.find((x) => String(x.id) === String(hostId))
    if (h) openHost(h)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [standalone, hostId, hosts.length])

  const closeTab = (key: string) => {
    setTabs((prev) => {
      const next = prev.filter((t) => t.key !== key)
      if (activeKey === key) setActiveKey(next.length ? next[next.length - 1].key : '')
      return next
    })
    setSessionIds((prev) => {
      const n = { ...prev }
      delete n[key]
      return n
    })
  }

  const activeTab = tabs.find((t) => t.key === activeKey)
  const activeHost = activeTab ? hosts.find((h) => h.id === activeTab.hostId) : undefined

  const visibleHosts = useMemo(() => {
    return hosts.filter((h) => {
      if (groupFilter !== 'all') {
        if (groupFilter === 0 ? h.group_id != null : h.group_id !== groupFilter) return false
      }
      if (keyword) {
        const k = keyword.toLowerCase()
        if (!h.name.toLowerCase().includes(k) && !h.address.toLowerCase().includes(k)) return false
      }
      return true
    })
  }, [hosts, keyword, groupFilter])

  const groupedHosts = useMemo(() => {
    const map = new Map<string, HostItem[]>()
    visibleHosts.forEach((h) => {
      const gname = h.group_name || '未分组'
      if (!map.has(gname)) map.set(gname, [])
      map.get(gname)!.push(h)
    })
    return [...map.entries()]
  }, [visibleHosts])

  const openHistory = async () => {
    if (!activeHost) return
    setHistoryOpen(true)
    try {
      setSessions(await getHostSessions(activeHost.id, 100))
    } catch {
      setSessions([])
    }
  }

  const refreshHistory = async () => {
    if (!activeHost) return
    try { setSessions(await getHostSessions(activeHost.id, 100)) } catch { /* ignore */ }
  }

  return (
    <div style={{ display: 'flex', height: standalone ? '100vh' : 'calc(100vh - 112px)', background: '#fff', borderRadius: 8, overflow: 'hidden' }}>
      {/* 左栏 */}
      <div style={{ width: 280, borderRight: '1px solid #f0f0f0', display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: 8 }}>
          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder="搜索主机名 / IP"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
          />
        </div>
        <div style={{ padding: '0 8px 8px' }}>
          <Space wrap size={4}>
            <Tag color={groupFilter === 'all' ? 'blue' : undefined} style={{ cursor: 'pointer' }} onClick={() => setGroupFilter('all')}>全部</Tag>
            <Tag color={groupFilter === 0 ? 'blue' : undefined} style={{ cursor: 'pointer' }} onClick={() => setGroupFilter(0)}>未分组</Tag>
            {groups.map((g) => (
              <Tag key={g.id} color={groupFilter === g.id ? 'blue' : undefined} style={{ cursor: 'pointer' }} onClick={() => setGroupFilter(g.id)}>
                {g.name}
              </Tag>
            ))}
          </Space>
        </div>
        <div style={{ flex: 1, overflow: 'auto', padding: '0 8px 8px' }}>
          {loading ? <Spin style={{ margin: 20 }} /> : !groupedHosts.length ? <Empty description="无主机" style={{ marginTop: 40 }} /> : groupedHosts.map(([gname, list]) => (
            <div key={gname} style={{ marginBottom: 8 }}>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>{gname}（{list.length}）</Typography.Text>
              <List
                size="small"
                dataSource={list}
                renderItem={(h) => (
                  <List.Item
                    style={{ padding: '6px 4px', cursor: 'pointer' }}
                    onClick={() => openHost(h)}
                    actions={[
                      <Tooltip key="login" title={h.source === 'agent' ? 'agent 主机不支持直连终端' : '登录'}>
                        <Button
                          size="small"
                          type="text"
                          disabled={h.source === 'agent'}
                          icon={<LoginOutlined />}
                          onClick={(e) => { e.stopPropagation(); openHost(h) }}
                        />
                      </Tooltip>,
                    ]}
                  >
                    <Space direction="vertical" size={0} style={{ lineHeight: 1.4 }}>
                      <Space size={4}>
                        <span>{h.name}</span>
                        <Tag color={h.status === 'online' ? 'green' : h.status === 'offline' ? 'red' : 'default'} style={{ marginInlineEnd: 0 }}>
                          {h.status === 'online' ? '在线' : h.status === 'offline' ? '离线' : '未知'}
                        </Tag>
                        {h.source === 'agent' && <Tag color="purple">agent</Tag>}
                      </Space>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>{h.address}:{h.ssh_port} · {h.ssh_user}</Typography.Text>
                    </Space>
                  </List.Item>
                )}
              />
            </div>
          ))}
        </div>
        <div style={{ padding: 8, borderTop: '1px solid #f0f0f0' }}>
          <Button block icon={<ReloadOutlined />} onClick={load}>刷新主机</Button>
        </div>
      </div>

      {/* 右侧终端区 */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <Space style={{ padding: '8px 12px', borderBottom: '1px solid #f0f0f0', flexWrap: 'wrap' }}>
          <Typography.Text strong>Web 终端</Typography.Text>
          {activeHost && (
            <>
              <Tag>{activeHost.name}</Tag>
              <Typography.Text type="secondary">{activeHost.address}:{activeHost.ssh_port} · {activeHost.ssh_user}</Typography.Text>
            </>
          )}
          <span style={{ flex: 1 }} />
          <Space size={4}>
            <Button size="small" icon={<HistoryOutlined />} disabled={!activeHost} onClick={openHistory}>会话历史</Button>
            <Button size="small" icon={<FolderOpenOutlined />} disabled={!activeHost} onClick={() => setSftpOpen(true)}>文件传输</Button>
            <Button
              size="small"
              icon={<FullscreenOutlined />}
              onClick={() => {
                const el = document.documentElement
                if (!document.fullscreenElement) el.requestFullscreen?.()
                else document.exitFullscreen?.()
              }}
            >全屏</Button>
            <Space.Compact>
              {FONT_SIZES.map((s) => (
                <Button key={s} size="small" type={fontSize === s ? 'primary' : 'default'} onClick={() => setFontSize(s)}>{s}</Button>
              ))}
            </Space.Compact>
          </Space>
        </Space>

        <div style={{ flex: 1, minHeight: 0 }}>
          {!tabs.length ? (
            <Empty style={{ marginTop: 80 }} description="从左侧选择主机开始会话（每个标签 = 一条独立 SSH 连接）" />
          ) : (
            <Tabs
              type="editable-card"
              hideAdd
              activeKey={activeKey}
              onChange={setActiveKey}
              onEdit={(key, action) => { if (action === 'remove') closeTab(String(key)) }}
              style={{ height: '100%' }}
              tabBarStyle={{ marginBottom: 0 }}
              items={tabs.map((t) => ({
                key: t.key,
                label: <span>{t.hostName}{t.connected ? ' ●' : ''}</span>,
                children: (() => {
                  const h = hosts.find((x) => x.id === t.hostId)
                  if (!h) return <Empty description="主机已删除" />
                  return (
                    <div style={{ display: activeKey === t.key ? 'block' : 'none', height: 'calc(100vh - 210px)' }}>
                      <TerminalPane
                        host={h}
                        fontSize={fontSize}
                        onStatus={(c) => setTabs((prev) => prev.map((x) => (x.key === t.key ? { ...x, connected: c } : x)))}
                        onSessionId={(sid) => setSessionIds((prev) => ({ ...prev, [t.key]: sid }))}
                      />
                    </div>
                  )
                })(),
              }))}
            />
          )}
        </div>
      </div>

      <Drawer title={`文件传输 · ${activeHost?.name || ''}`} width={620} open={sftpOpen} onClose={() => setSftpOpen(false)} destroyOnClose>
        {activeHost ? <SftpPanel host={activeHost} /> : <Empty />}
      </Drawer>

      <Drawer
        title={`会话历史 · ${activeHost?.name || ''}`}
        width={760}
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        extra={<Button size="small" onClick={refreshHistory}>刷新</Button>}
      >
        <Table<HostSessionItem>
          size="small"
          rowKey="id"
          dataSource={sessions}
          pagination={{ pageSize: 10 }}
          columns={[
            { title: '#', dataIndex: 'id', width: 60 },
            { title: '用户', dataIndex: 'username', width: 100 },
            { title: '来源 IP', dataIndex: 'source_ip', width: 130 },
            { title: '开始时间', dataIndex: 'started_at', width: 170 },
            { title: '时长', dataIndex: 'duration_sec', width: 80, render: (v: number) => `${v}s` },
            {
              title: '状态', dataIndex: 'status', width: 110,
              render: (v: string, r) => (
                <Space size={4}>
                  <Tag color={r.active ? 'green' : v === 'closed' ? 'default' : v === 'error' ? 'red' : 'orange'}>
                    {r.active ? '进行中' : v}
                  </Tag>
                  {r.close_reason && <Tooltip title={r.close_reason}><Typography.Text type="secondary" style={{ fontSize: 12 }}>{r.close_reason}</Typography.Text></Tooltip>}
                </Space>
              ),
            },
            {
              title: '操作', width: 150,
              render: (_: unknown, r) => (
                <Space size={4}>
                  {r.recorded && !r.active && (
                    <Button size="small" type="link" onClick={() => navigate(`/host-sessions/${r.id}/replay`)}>回放</Button>
                  )}
                  {r.active && admin && (
                    <Popconfirm title="强制下线该会话？" onConfirm={async () => {
                      setSessionBusy(r.id)
                      try { await kickHostSession(r.id); message.success('已踢下线'); refreshHistory() } catch (e) { message.error((e as Error).message) } finally { setSessionBusy(null) }
                    }}>
                      <Button size="small" danger type="link" loading={sessionBusy === r.id} icon={<DeleteOutlined />}>踢下线</Button>
                    </Popconfirm>
                  )}
                </Space>
              ),
            },
          ]}
        />
        {activeTab && sessionIds[activeTab.key] && (
          <Typography.Text type="secondary">当前标签会话 #{sessionIds[activeTab.key]}</Typography.Text>
        )}
      </Drawer>
    </div>
  )
}
