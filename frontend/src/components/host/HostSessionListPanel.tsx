// 会话录制列表（R43 H4）：主机终端会话 + 是否有录像 + 回放/下载入口
import { useEffect, useState } from 'react'
import { Button, Card, Input, Select, Space, Table, Tag, message } from 'antd'
import { DownloadOutlined, PlayCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { listHostSessions, getHostSessionCast, type HostSessionItem } from '../../services/api'

const STATUS: Record<string, { label: string; color: string }> = {
  active: { label: '进行中', color: 'green' },
  closed: { label: '已结束', color: 'default' },
  timeout: { label: '超时断开', color: 'orange' },
  kicked: { label: '被踢下线', color: 'volcano' },
  error: { label: '异常', color: 'red' },
}

function fmtSize(n?: number): string {
  const v = Number(n || 0)
  if (!v) return '-'
  if (v < 1024) return `${v} B`
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`
  return `${(v / 1024 / 1024).toFixed(1)} MB`
}

function fmtDuration(sec?: number): string {
  const s = Math.max(0, Math.floor(Number(sec || 0)))
  if (!s) return '-'
  const m = Math.floor(s / 60)
  const h = Math.floor(m / 60)
  if (h) return `${h}h${m % 60}m`
  if (m) return `${m}m${s % 60}s`
  return `${s}s`
}

export default function HostSessionListPanel() {
  const navigate = useNavigate()
  const [rows, setRows] = useState<HostSessionItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [status, setStatus] = useState<string>('')
  const [username, setUsername] = useState('')
  const [loading, setLoading] = useState(false)

  const load = (p = page, ps = pageSize) => {
    setLoading(true)
    listHostSessions({ page: p, page_size: ps, status: status || undefined, username: username || undefined })
      .then((d) => { setRows(d?.items || []); setTotal(d?.total || 0) })
      .catch(() => { setRows([]); setTotal(0) })
      .finally(() => setLoading(false))
  }

  useEffect(() => { load(1, pageSize); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [])

  const download = async (r: HostSessionItem) => {
    try {
      const text = await getHostSessionCast(r.id)
      const blob = new Blob([String(text || '')], { type: 'text/plain;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `session-${r.id}.cast`
      a.click()
      setTimeout(() => URL.revokeObjectURL(url), 3000)
    } catch (e) {
      message.error(String((e as Error)?.message || '下载录像失败'))
    }
  }

  const columns = [
    { title: '主机', dataIndex: 'host_name', render: (v: string) => v || '-' },
    { title: '用户', dataIndex: 'username', width: 110, render: (v: string) => v || '-' },
    { title: '来源 IP', dataIndex: 'source_ip', width: 130, render: (v: string) => v || '-' },
    { title: '开始时间', dataIndex: 'started_at', width: 170, render: (v: string) => v || '-' },
    { title: '时长', dataIndex: 'duration_sec', width: 90, render: (v: number) => fmtDuration(v) },
    { title: '录像大小', dataIndex: 'record_bytes', width: 100, render: (v: number) => fmtSize(v) },
    { title: '状态', dataIndex: 'status', width: 110, render: (v: string) => {
      const s = STATUS[v] || { label: v || '-', color: 'default' }
      return <Tag color={s.color}>{s.label}</Tag>
    } },
    { title: '操作', width: 150, render: (_: unknown, r: HostSessionItem) => (
      <Space size="small">
        <Button size="small" type="link" icon={<PlayCircleOutlined />} disabled={!r.record_exists}
          onClick={() => navigate(`/host-sessions/${r.id}/replay`)}>回放</Button>
        <Button size="small" type="link" icon={<DownloadOutlined />} disabled={!r.record_exists}
          onClick={() => download(r)}>录像</Button>
      </Space>
    ) },
  ]

  return (
    <Card size="small" title="会话录制"
      extra={<Space>
        <Input allowClear placeholder="按用户过滤" style={{ width: 150 }} value={username}
          onChange={(e) => setUsername(e.target.value)} onPressEnter={() => { setPage(1); load(1, pageSize) }} />
        <Select allowClear placeholder="状态" style={{ width: 130 }} value={status || undefined}
          onChange={(v) => setStatus(v || '')}
          options={Object.entries(STATUS).map(([k, v]) => ({ value: k, label: v.label }))} />
        <Button onClick={() => { setPage(1); load(1, pageSize) }}>查询</Button>
        <Button icon={<ReloadOutlined />} onClick={() => load(page, pageSize)}>刷新</Button>
      </Space>}>
      <Table rowKey="id" size="small" loading={loading} dataSource={rows} columns={columns}
        scroll={{ x: 'max-content' }}
        pagination={{
          current: page, pageSize, total, showSizeChanger: true,
          pageSizeOptions: [20, 50, 100],
          onChange: (p, ps) => { setPage(p); setPageSize(ps); load(p, ps) },
          showTotal: (t) => `共 ${t} 条`,
        }} />
    </Card>
  )
}
