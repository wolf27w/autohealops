// R8：堡垒机 Web 终端（类 JumpServer 布局）——左栏分组树 + 主机，右侧多标签 SSH 会话
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { Key } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import {
  Alert, Breadcrumb, Button, Drawer, Empty, Input, List, Modal, Popconfirm, Progress, Space, Spin, Table, Tabs, Tag, Tooltip, Tree, Typography, Upload, message,
} from 'antd'
import type { TreeDataNode, UploadProps } from 'antd'
import {
  AuditOutlined, CloseOutlined, DeleteOutlined, DownloadOutlined, EditOutlined, FileOutlined, FolderOpenOutlined, FolderOutlined, FolderViewOutlined,
  FullscreenOutlined, HistoryOutlined, LoginOutlined, PlusOutlined, ReloadOutlined, SearchOutlined, UploadOutlined,
} from '@ant-design/icons'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { getHosts, getHostGroups, getTerminalSecurity } from '../services/api'
import type { HostItem, HostGroup, TerminalSecurity } from '../services/api'
import {
  buildHostTerminalWsUrl, getHostSessions, kickHostSession, sftpDelete, sftpDownload, sftpList, sftpMkdir, sftpRead, sftpUpload, sftpWrite,
} from '../services/hostConsoleApi'
import type { HostSessionItem, SftpEntry } from '../services/hostConsoleApi'
import { isAdmin } from '../utils/auth'
import HostCommandAuditDrawer from '../components/HostCommandAuditDrawer'

interface OpenTab { key: string; hostId: number; hostName: string; connected: boolean }

const FONT_SIZES = [12, 13, 14, 16, 18]

function TerminalWatermark({ hostName }: { hostName: string }) {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 30000)
    return () => clearInterval(t)
  }, [])

  const username = (() => {
    try {
      const raw = localStorage.getItem('user')
      if (raw) {
        const u = JSON.parse(raw)
        if (u && typeof u.username === 'string') return u.username
      }
    } catch { /* ignore */ }
    return '未知用户'
  })()

  const pad = (n: number) => String(n).padStart(2, '0')
  const ts = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}`
  const text = [username, hostName, ts].filter(Boolean).join(' · ')

  const tiles: { top: string; left: string }[] = []
  for (let r = 0; r < 6; r++) {
    for (let c = 0; c < 4; c++) {
      tiles.push({ top: `${r * 18}%`, left: `${c * 26}%` })
    }
  }

  return (
    <div style={{ position: 'absolute', inset: 0, overflow: 'hidden', pointerEvents: 'none', zIndex: 10 }}>
      {tiles.map((t, i) => (
        <span
          key={i}
          style={{
            position: 'absolute', top: t.top, left: t.left,
            transform: 'rotate(-20deg)', opacity: 0.07, color: '#b0b0b0',
            fontSize: 13, whiteSpace: 'nowrap', userSelect: 'none',
          }}
        >{text}</span>
      ))}
    </div>
  )
}

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
  // 连接代次：重连/卸载时让旧 ws 的回调失效（dev StrictMode 会挂载两次，避免误报连接失败）
  const seqRef = useRef(0)
  const everConnectedRef = useRef(false)
  // 左下角状态条默认隐藏，鼠标靠近才显示（避免挡住终端内容）
  const [showStatus, setShowStatus] = useState(false)

  const connect = useCallback(() => {
    const term = termRef.current
    if (!term) return
    // 防重复连接：已有一条在建立/使用中就不再新建，避免服务端产生两条会话
    const prev = wsRef.current
    if (prev && (prev.readyState === WebSocket.CONNECTING || prev.readyState === WebSocket.OPEN)) {
      const sameHost = (prev as WebSocket & { __hostId?: number }).__hostId === host.id
      if (sameHost) return
      try { prev.close() } catch { /* ignore */ }
    }
    setError('')
    term.reset()
    term.writeln(`\x1b[90m正在连接 ${host.name}（${host.address}:${host.ssh_port}）...\x1b[0m`)
    const fit = fitRef.current
    if (fit) fit.fit()
    const cols = term.cols || 120
    const rows = term.rows || 32
    const seq = ++seqRef.current
    everConnectedRef.current = false
    const ws = new WebSocket(buildHostTerminalWsUrl(host.id, { cols, rows }))
    ws.binaryType = 'arraybuffer'
    ;(ws as WebSocket & { __hostId?: number }).__hostId = host.id
    wsRef.current = ws

    ws.onmessage = (ev) => {
      if (seq !== seqRef.current) return
      if (typeof ev.data === 'string') {
        try {
          const msg = JSON.parse(ev.data)
          if (msg.type === 'ready') {
            everConnectedRef.current = true
            setError('')
            setConnected(true)
            onStatus(true)
            onSessionId(msg.session_id)
            term.writeln(`\x1b[32m已连接（会话 #${msg.session_id}，空闲 ${Math.round((msg.idle_timeout || 600) / 60)} 分钟超时）\x1b[0m`)
          } else if (msg.type === 'error') {
            setError(msg.message || '连接异常')
            setConnected(false)
            onStatus(false)
          } else if (msg.type === 'command_blocked') {
            // 危险命令被后端拦截：不当终端输出打印，弹窗 + toast 提示
            message.warning(`命令已被拦截：${msg.command || ''}`)
            Modal.warning({
              title: '命令已被拦截',
              content: (
                <div style={{ lineHeight: 1.8 }}>
                  <div style={{ background: '#f5f5f5', padding: '6px 10px', borderRadius: 4, fontFamily: 'Menlo, Monaco, "Courier New", monospace', fontSize: 13, wordBreak: 'break-all' }}>
                    {msg.command || '-'}
                  </div>
                  {msg.reason && <div style={{ marginTop: 8 }}><span style={{ color: '#999' }}>原因：</span>{msg.reason}</div>}
                  <div style={{ marginTop: 8, color: '#999' }}>如确需执行，请联系管理员或改用受控流程。</div>
                </div>
              ),
            })
          }
        } catch { /* 非 JSON 文本当输出 */ }
        return
      }
      const buf = new Uint8Array(ev.data as ArrayBuffer)
      term.write(buf)
    }
    ws.onclose = (e) => {
      if (seq !== seqRef.current) return
      setConnected(false)
      onStatus(false)
      onSessionId(null)
      if (e.code !== 1000) term.writeln(`\r\n\x1b[33m连接已关闭（code=${e.code}${e.reason ? ' ' + e.reason : ''}）\x1b[0m`)
    }
    // 只有「从未连上」的异常才提示失败；已连上后断开由 onclose 的行内提示表达，
    // 避免出现「明明已连接却提示 WebSocket 连接失败」的误导。
    ws.onerror = () => {
      if (seq !== seqRef.current) return
      if (!everConnectedRef.current) setError('WebSocket 连接失败')
    }
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
      seqRef.current++  // 让即将触发的 onerror/onclose 全部失效
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
      <div style={{ position: 'relative', flex: 1, minHeight: 0 }}>
        <div ref={boxRef} style={{ height: '100%', padding: '4px 8px', background: '#1e1e1e' }} />
        {/* 左下角隐形热区：鼠标移到这里才显示连接状态与操作，避免常驻遮挡输入 */}
        <div
          onMouseEnter={() => setShowStatus(true)}
          style={{ position: 'absolute', left: 0, bottom: 0, width: 180, height: 30, cursor: 'default' }}
        />
        <div
          onMouseLeave={() => setShowStatus(false)}
          style={{
            position: 'absolute', left: 8, bottom: 32, display: 'flex', alignItems: 'center', gap: 8,
            padding: '4px 8px', borderRadius: 6, background: 'rgba(30,30,30,.92)',
            border: '1px solid #3a3a3a', boxShadow: '0 2px 8px rgba(0,0,0,.35)',
            opacity: showStatus ? 1 : 0, pointerEvents: showStatus ? 'auto' : 'none',
            transition: 'opacity .15s ease',
          }}
        >
          <Tag color={connected ? 'green' : 'default'} style={{ marginInlineEnd: 0 }}>
            {connected ? '已连接' : '未连接'}
          </Tag>
          <Button size="small" ghost icon={<ReloadOutlined />}
            onClick={() => { wsRef.current?.close(); setTimeout(connect, 300) }}>重连</Button>
          <Button size="small" ghost icon={<CloseOutlined />} onClick={() => wsRef.current?.close()}>断开</Button>
        </div>
      </div>
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

// ============ 文件管理侧栏（T3）：树形目录 + 上传/下载/编辑/删除 ============

const FM_ROOT = '/tmp'

interface FmNode extends TreeDataNode {
  path: string
  name: string
  is_dir: boolean
  size: number
  mtime: number
  children?: FmNode[]
}

function fmtSize(v: number) {
  if (v > 1048576) return `${(v / 1048576).toFixed(1)} MB`
  return `${Math.max(1, Math.round(v / 1024))} KB`
}

function fmtTime(ts: number) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function fmDirname(p: string) {
  const s = p.replace(/\/+$/, '')
  const i = s.lastIndexOf('/')
  return i <= 0 ? FM_ROOT : s.slice(0, i)
}

function updateTreeData(list: FmNode[], key: Key, children: FmNode[]): FmNode[] {
  return list.map((n) => {
    if (n.key === key) return { ...n, children }
    if (n.children) return { ...n, children: updateTreeData(n.children, key, children) }
    return n
  })
}

function FileManagerSidebar({ host, onClose }: { host: HostItem; onClose: () => void }) {
  const [treeData, setTreeData] = useState<FmNode[]>([])
  const [expandedKeys, setExpandedKeys] = useState<Key[]>([FM_ROOT])
  const [selectedKeys, setSelectedKeys] = useState<Key[]>([])
  const [currentDir, setCurrentDir] = useState(FM_ROOT)
  const [loadingPaths, setLoadingPaths] = useState<Set<string>>(new Set())
  const [pct, setPct] = useState<number | null>(null)

  const [editOpen, setEditOpen] = useState(false)
  const [editPath, setEditPath] = useState('')
  const [editName, setEditName] = useState('')
  const [editContent, setEditContent] = useState('')
  const [editLoading, setEditLoading] = useState(false)
  const [editSaving, setEditSaving] = useState(false)

  const [mkdirOpen, setMkdirOpen] = useState(false)
  const [mkdirName, setMkdirName] = useState('')
  const [mkdirBusy, setMkdirBusy] = useState(false)

  const rootNode = useCallback((): FmNode => ({
    key: FM_ROOT, path: FM_ROOT, name: FM_ROOT, is_dir: true, size: 0, mtime: 0,
    isLeaf: false, title: FM_ROOT,
  }), [])

  const entryToNode = useCallback((e: SftpEntry): FmNode => ({
    key: e.path, path: e.path, name: e.name, is_dir: e.is_dir,
    size: e.size, mtime: e.mtime, isLeaf: !e.is_dir, title: e.name,
  }), [])

  const loadDir = useCallback(async (path: string) => {
    setLoadingPaths((s) => new Set(s).add(path))
    try {
      const d = await sftpList(host.id, path)
      const children = (d.entries || []).map(entryToNode)
      setTreeData((prev) => updateTreeData(prev, path, children))
    } catch (e) {
      message.error((e as Error).message || '目录读取失败')
    } finally {
      setLoadingPaths((s) => { const n = new Set(s); n.delete(path); return n })
    }
  }, [host.id, entryToNode])

  // 初始加载根目录（组件按 host.id 重挂载，主机切换时自动重置到 /tmp）
  useEffect(() => {
    setTreeData([rootNode()])
    setExpandedKeys([FM_ROOT])
    setSelectedKeys([])
    setCurrentDir(FM_ROOT)
    loadDir(FM_ROOT)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const reload = useCallback(() => {
    setTreeData([rootNode()])
    setExpandedKeys([FM_ROOT])
    setSelectedKeys([])
    setCurrentDir(FM_ROOT)
    loadDir(FM_ROOT)
  }, [rootNode, loadDir])

  const onExpand = (keys: Key[], info: { node: FmNode; expanded: boolean }) => {
    setExpandedKeys(keys)
    if (info.expanded && !info.node.isLeaf) loadDir(info.node.path)
  }

  const onSelect = (keys: Key[], info: { node: FmNode }) => {
    setSelectedKeys(keys)
    const node = info.node
    setCurrentDir(node.is_dir ? node.path : fmDirname(node.path))
  }

  const doDownload = (n: FmNode) => {
    sftpDownload(host.id, n.path).catch((e) => message.error(e.message))
  }

  const doDelete = async (n: FmNode) => {
    try {
      await sftpDelete(host.id, n.path)
      message.success(`已删除 ${n.name}`)
      loadDir(fmDirname(n.path))
    } catch (e) {
      message.error((e as Error).message || '删除失败')
    }
  }

  const openEdit = async (path: string) => {
    setEditPath(path)
    setEditName(path.split('/').pop() || '')
    setEditContent('')
    setEditOpen(true)
    setEditLoading(true)
    try {
      const d = await sftpRead(host.id, path)
      setEditContent(d.content || '')
    } catch (e) {
      message.error((e as Error).message || '读取失败')
      setEditOpen(false)
    } finally {
      setEditLoading(false)
    }
  }

  const saveEdit = async () => {
    setEditSaving(true)
    try {
      await sftpWrite(host.id, editPath, editContent)
      message.success('已保存')
      setEditOpen(false)
    } catch (e) {
      message.error((e as Error).message || '保存失败')
    } finally {
      setEditSaving(false)
    }
  }

  const doMkdir = async () => {
    const name = mkdirName.trim()
    if (!name) { message.warning('请输入目录名'); return }
    if (name.includes('/')) { message.warning('目录名不能包含 /'); return }
    setMkdirBusy(true)
    try {
      await sftpMkdir(host.id, `${currentDir.replace(/\/+$/, '')}/${name}`)
      message.success('已创建目录')
      setMkdirOpen(false)
      setMkdirName('')
      setExpandedKeys((keys) => Array.from(new Set([...keys, currentDir])))
      loadDir(currentDir)
    } catch (e) {
      message.error((e as Error).message || '创建失败')
    } finally {
      setMkdirBusy(false)
    }
  }

  const uploadProps: UploadProps = {
    multiple: true,
    showUploadList: false,
    beforeUpload: async (file) => {
      setPct(0)
      try {
        await sftpUpload(host.id, currentDir, file as File, (p) => setPct(p))
        message.success(`已上传 ${file.name}`)
        loadDir(currentDir)
      } catch (e) {
        message.error((e as Error).message || '上传失败')
      } finally {
        setPct(null)
      }
      return false
    },
  }

  const renderNode = (node: FmNode) => {
    if (node.path === FM_ROOT) {
      return (
        <Space size={6}>
          {loadingPaths.has(node.path) && <Spin size="small" />}
          <FolderOutlined style={{ color: '#faad14' }} />
          <span>{node.name}</span>
        </Space>
      )
    }
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, width: '100%', minWidth: 0 }}>
        {loadingPaths.has(node.path) && <Spin size="small" />}
        {node.is_dir ? <FolderOutlined style={{ color: '#faad14' }} /> : <FileOutlined />}
        <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{node.name}</span>
        {!node.is_dir && <span style={{ color: '#999', fontSize: 11 }}>{fmtSize(node.size)}</span>}
        <span style={{ color: '#bbb', fontSize: 11 }}>{fmtTime(node.mtime)}</span>
        <Space size={0} onClick={(e) => e.stopPropagation()} onMouseDown={(e) => e.stopPropagation()}>
          {!node.is_dir && (
            <>
              <Button size="small" type="text" icon={<DownloadOutlined />} onClick={(e) => { e.stopPropagation(); doDownload(node) }} />
              <Button size="small" type="text" icon={<EditOutlined />} onClick={(e) => { e.stopPropagation(); openEdit(node.path) }} />
            </>
          )}
          <Popconfirm title={`确定删除${node.is_dir ? '目录' : '文件'}「${node.name}」？`} onConfirm={() => doDelete(node)} onPopupClick={(e) => e.stopPropagation()}>
            <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={(e) => e.stopPropagation()} />
          </Popconfirm>
        </Space>
      </div>
    )
  }

  const segs = currentDir.split('/').filter(Boolean)
  const breadcrumbItems = segs.map((s, i) => {
    const p = '/' + segs.slice(0, i + 1).join('/')
    return {
      key: p,
      title: (
        <a onClick={() => { setCurrentDir(p); setSelectedKeys([p]) }}>{s}</a>
      ),
    }
  })

  return (
    <div style={{ width: 320, minWidth: 320, borderLeft: '1px solid #f0f0f0', display: 'flex', flexDirection: 'column', background: '#fff' }}>
      <div style={{ padding: '6px 10px', borderBottom: '1px solid #f0f0f0', display: 'flex', alignItems: 'center' }}>
        <FolderViewOutlined />
        <Typography.Text strong style={{ marginLeft: 6 }}>文件管理</Typography.Text>
        <span style={{ flex: 1 }} />
        <Button size="small" type="text" icon={<CloseOutlined />} onClick={onClose} />
      </div>
      <div style={{ padding: '6px 10px', borderBottom: '1px solid #f0f0f0', display: 'flex', alignItems: 'center', gap: 4 }}>
        <Breadcrumb style={{ flex: 1, minWidth: 0, fontSize: 12 }} items={breadcrumbItems} />
        <Button size="small" type="text" icon={<ReloadOutlined />} onClick={reload} />
      </div>
      <div style={{ flex: 1, overflow: 'auto', padding: '4px 0' }}>
        <Tree
          blockNode
          showIcon={false}
          treeData={treeData}
          expandedKeys={expandedKeys}
          selectedKeys={selectedKeys}
          onExpand={onExpand}
          onSelect={onSelect}
          titleRender={renderNode}
        />
      </div>
      <div style={{ padding: 8, borderTop: '1px solid #f0f0f0' }}>
        {pct !== null && <Progress percent={pct} size="small" style={{ marginBottom: 6 }} />}
        <Space style={{ width: '100%' }}>
          <Upload {...uploadProps}>
            <Button size="small" icon={<UploadOutlined />}>上传</Button>
          </Upload>
          <Button size="small" icon={<PlusOutlined />} onClick={() => { setMkdirName(''); setMkdirOpen(true) }}>新建目录</Button>
        </Space>
        <Typography.Text type="secondary" style={{ fontSize: 11, display: 'block', marginTop: 6 }}>
          上传到：{currentDir}；文件≤1MB 可在线编辑
        </Typography.Text>
      </div>

      <Modal title="新建目录" open={mkdirOpen} onOk={doMkdir} onCancel={() => setMkdirOpen(false)} confirmLoading={mkdirBusy} okText="创建" cancelText="取消" destroyOnClose>
        <div style={{ marginBottom: 8 }}><Typography.Text type="secondary">创建于：{currentDir}</Typography.Text></div>
        <Input placeholder="目录名（不含 /）" value={mkdirName} onChange={(e) => setMkdirName(e.target.value)} onPressEnter={doMkdir} />
      </Modal>

      <Modal title={`编辑 · ${editName}`} open={editOpen} onOk={saveEdit} onCancel={() => setEditOpen(false)} confirmLoading={editSaving} okText="保存" cancelText="取消" width={560} destroyOnClose>
        <Spin spinning={editLoading}>
          <Input.TextArea
            value={editContent}
            onChange={(e) => setEditContent(e.target.value)}
            autoSize={{ minRows: 12, maxRows: 24 }}
            style={{ fontFamily: 'Menlo, Monaco, "Courier New", monospace', fontSize: 12 }}
          />
        </Spin>
      </Modal>
    </div>
  )
}

export default function WebTerminal({ standalone = false }: { standalone?: boolean }) {
  const { hostId } = useParams()
  const navigate = useNavigate()
  const [sp] = useSearchParams()
  const [groups, setGroups] = useState<HostGroup[]>([])
  const [hosts, setHosts] = useState<HostItem[]>([])
  const [keyword, setKeyword] = useState('')
  const [groupFilter, setGroupFilter] = useState<number | 'all'>('all')
  const [loading, setLoading] = useState(false)
  const [tabs, setTabs] = useState<OpenTab[]>([])
  const [activeKey, setActiveKey] = useState('')
  const [fontSize, setFontSize] = useState(13)
  const [sftpOpen, setSftpOpen] = useState(false)
  const [fmOpen, setFmOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [auditOpen, setAuditOpen] = useState(false)
  const [sessions, setSessions] = useState<HostSessionItem[]>([])
  const [sessionBusy, setSessionBusy] = useState<number | null>(null)
  const [sessionIds, setSessionIds] = useState<Record<string, number | null>>({})
  const [termSec, setTermSec] = useState<TerminalSecurity>({ block_dangerous: true, allow_paste: true, watermark: true, no_copy: true })
  const termAreaRef = useRef<HTMLDivElement>(null)

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

  // 终端安全开关：挂载时读一次（block_dangerous 由后端 WS 使用；前端只用 watermark/no_copy/allow_paste）
  useEffect(() => {
    getTerminalSecurity().then((d) => { if (d) setTermSec(d) }).catch(() => {})
  }, [])

  // 禁止复制 / 允许粘贴：在终端容器上拦截 copy/cut/contextmenu/paste（卸载时移除）
  useEffect(() => {
    const el = termAreaRef.current
    if (!el) return
    const clearClipboard = () => {
      try {
        const p = navigator.clipboard?.writeText('')
        if (p) p.catch(() => {})
      } catch { /* 静默 */ }
    }
    const onCopyCut = (e: ClipboardEvent) => {
      if (!termSec.no_copy) return
      e.preventDefault()
      e.stopPropagation()
      clearClipboard()
    }
    const onContextMenu = (e: MouseEvent) => {
      if (termSec.no_copy) e.preventDefault()
    }
    const onPaste = (e: ClipboardEvent) => {
      if (!termSec.allow_paste) {
        e.preventDefault()
        e.stopPropagation()
      }
    }
    el.addEventListener('copy', onCopyCut, true)
    el.addEventListener('cut', onCopyCut, true)
    el.addEventListener('contextmenu', onContextMenu)
    el.addEventListener('paste', onPaste, true)
    return () => {
      el.removeEventListener('copy', onCopyCut, true)
      el.removeEventListener('cut', onCopyCut, true)
      el.removeEventListener('contextmenu', onContextMenu)
      el.removeEventListener('paste', onPaste, true)
    }
  }, [termSec.no_copy, termSec.allow_paste])

  const openHost = useCallback((h: HostItem) => {
    const key = `h-${h.id}`
    setTabs((prev) => (prev.some((t) => t.key === key) ? prev : [...prev, { key, hostId: h.id, hostName: h.name, connected: false }]))
    setActiveKey(key)
    if (!standalone) navigate(`/terminal?host=${h.id}`, { replace: true })
  }, [navigate, standalone])

  // 独立整屏页支持 ?host=<id> 直达（节点管理行「终端」/旧地址重定向都走这里）
  const jumpedRef = useRef(false)
  useEffect(() => {
    if (jumpedRef.current) return
    if (!standalone) {
      if (!hostId || !hosts.length) return
      const h = hosts.find((x) => String(x.id) === String(hostId))
      if (h) { jumpedRef.current = true; openHost(h) }
      return
    }
    const hid = sp.get('host') || hostId
    if (!hid || !hosts.length) return
    const h = hosts.find((x) => String(x.id) === String(hid))
    if (!h) return
    const key = `h-${h.id}`
    jumpedRef.current = true
    setTabs((prev) => (prev.some((t) => t.key === key) ? prev : [...prev, { key, hostId: h.id, hostName: h.name, connected: false }]))
    setActiveKey(key)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [standalone, hostId, hosts.length, sp])

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
            <Button size="small" type={fmOpen ? 'primary' : 'default'} icon={<FolderViewOutlined />} disabled={!activeHost} onClick={() => setFmOpen((v) => !v)}>文件管理</Button>
            <Button size="small" icon={<AuditOutlined />} disabled={!activeHost} onClick={() => setAuditOpen(true)}>审计</Button>
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

        <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'row' }}>
          <div ref={termAreaRef} style={{ flex: 1, minWidth: 0, position: 'relative', overflow: 'hidden' }}>
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
          {termSec.watermark && <TerminalWatermark hostName={activeHost?.name || ''} />}
          </div>
          {fmOpen && activeHost && <FileManagerSidebar key={activeHost.id} host={activeHost} onClose={() => setFmOpen(false)} />}
        </div>
      </div>

      <Drawer title={`文件传输 · ${activeHost?.name || ''}`} width={620} open={sftpOpen} onClose={() => setSftpOpen(false)} destroyOnClose>
        {activeHost ? <SftpPanel host={activeHost} /> : <Empty />}
      </Drawer>

      <HostCommandAuditDrawer open={auditOpen} hostId={activeHost?.id ?? null} onClose={() => setAuditOpen(false)} />

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
