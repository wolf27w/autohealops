// B8：命令审计抽屉——列表 / 筛选 / 服务端分页 / 导出 CSV
import { useCallback, useEffect, useState } from 'react'
import { Button, DatePicker, Drawer, Empty, Input, Select, Space, Table, Tag, Tooltip, message } from 'antd'
import type { Dayjs } from 'dayjs'
import { DownloadOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import { getHosts } from '../services/api'
import type { HostItem } from '../services/api'
import { downloadHostCommandAudits, listHostCommandAudits } from '../services/hostConsoleApi'
import type { HostCommandAuditItem } from '../services/hostConsoleApi'

const RISK_MAP: Record<string, { color: string; label: string }> = {
  high: { color: 'red', label: '高' },
  medium: { color: 'orange', label: '中' },
  low: { color: 'default', label: '低' },
}

const PAGE_SIZE = 20

interface AuditQuery {
  host_id?: number
  user_id?: string
  keyword?: string
  action?: 'allowed' | 'blocked'
  start?: string
  end?: string
}

/**
 * 命令审计面板（列表 / 筛选 / 服务端分页 / 导出 CSV）
 * - 抽屉模式：HostCommandAuditDrawer（终端页内打开）
 * - 内嵌模式：节点管理页「命令审计」Tab，active=true 时才拉数据
 */
export function HostCommandAuditPanel({ active = true, hostId = null }: {
  active?: boolean
  hostId?: number | null
}) {
  // 筛选草稿态
  const [hosts, setHosts] = useState<HostItem[]>([])
  const [filterHostId, setFilterHostId] = useState<number | null>(null)
  const [userId, setUserId] = useState('')
  const [keyword, setKeyword] = useState('')
  const [action, setAction] = useState<'all' | 'allowed' | 'blocked'>('all')
  const [dateRange, setDateRange] = useState<[Dayjs, Dayjs] | null>(null)
  // 已应用的查询 + 分页
  const [query, setQuery] = useState<AuditQuery>({})
  const [page, setPage] = useState(1)
  const [items, setItems] = useState<HostCommandAuditItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [exporting, setExporting] = useState(false)

  // 打开/切到本 Tab 时重置筛选为当前主机（或全部），并拉主机列表
  useEffect(() => {
    if (!active) return
    setFilterHostId(hostId ?? null)
    setUserId('')
    setKeyword('')
    setAction('all')
    setDateRange(null)
    setQuery({})
    setPage(1)
    getHosts().then((h) => setHosts(h || [])).catch(() => setHosts([]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, hostId])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const d = await listHostCommandAudits({ ...query, page, page_size: PAGE_SIZE })
      setItems(d?.items || [])
      setTotal(d?.total || 0)
    } catch {
      setItems([])
      setTotal(0)
    } finally {
      setLoading(false)
    }
  }, [query, page])

  useEffect(() => {
    if (active) load()
  }, [active, load])

  const applyQuery = () => {
    const q: AuditQuery = {}
    if (filterHostId != null) q.host_id = filterHostId
    const uid = userId.trim()
    if (uid) q.user_id = uid
    const kw = keyword.trim()
    if (kw) q.keyword = kw
    if (action !== 'all') q.action = action
    if (dateRange && dateRange[0] && dateRange[1]) {
      q.start = dateRange[0].toISOString()
      q.end = dateRange[1].toISOString()
    }
    setPage(1)
    setQuery(q)
  }

  const reset = () => {
    setFilterHostId(hostId ?? null)
    setUserId('')
    setKeyword('')
    setAction('all')
    setDateRange(null)
    setPage(1)
    setQuery({})
  }

  const doExport = async () => {
    setExporting(true)
    try {
      await downloadHostCommandAudits(query)
      message.success('导出成功')
    } catch (e) {
      message.error((e as Error).message || '导出失败')
    } finally {
      setExporting(false)
    }
  }

  const hostOptions = hosts.map((h) => ({ value: h.id, label: h.name }))

  const columns = [
    { title: '时间', dataIndex: 'created_at', width: 170 },
    { title: '主机', dataIndex: 'host_name', width: 120, render: (v: string) => v || '-' },
    { title: '用户', dataIndex: 'username', width: 100, render: (v: string) => v || '-' },
    {
      title: '命令', dataIndex: 'command',
      render: (v: string) => (
        <Tooltip title={v || ''} placement="topLeft">
          <span style={{ fontFamily: 'Menlo, Monaco, "Courier New", monospace', fontSize: 12 }}>{v || '-'}</span>
        </Tooltip>
      ),
    },
    {
      title: '风险', dataIndex: 'risk', width: 80,
      render: (v: string) => {
        const m = RISK_MAP[v]
        return m ? <Tag color={m.color}>{m.label}</Tag> : <span>{v || '-'}</span>
      },
    },
    {
      title: '结果', dataIndex: 'action', width: 90,
      render: (v: string) => (
        v === 'blocked' ? <Tag color="red">拦截</Tag> : v === 'allowed' ? <Tag color="green">放行</Tag> : <span>{v || '-'}</span>
      ),
    },
    { title: '原因', dataIndex: 'reason', width: 200, ellipsis: true, render: (v: string) => v || '-' },
    { title: '来源 IP', dataIndex: 'client_ip', width: 130, render: (v: string) => v || '-' },
    { title: '来源会话', dataIndex: 'session_id', width: 120, render: (v: string | null) => v || '-' },
  ]

  return (
    <>
      <Space style={{ marginBottom: 12, flexWrap: 'wrap' }}>
        <Select
          allowClear
          showSearch
          optionFilterProp="label"
          placeholder="主机（全部）"
          style={{ width: 180 }}
          value={filterHostId ?? undefined}
          onChange={(v) => setFilterHostId(v ?? null)}
          options={hostOptions}
        />
        <Input
          allowClear
          placeholder="用户 ID"
          style={{ width: 120 }}
          value={userId}
          onChange={(e) => setUserId(e.target.value)}
          onPressEnter={applyQuery}
        />
        <Input
          allowClear
          placeholder="命令关键字"
          style={{ width: 180 }}
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onPressEnter={applyQuery}
        />
        <Select
          placeholder="结果"
          style={{ width: 110 }}
          value={action}
          onChange={(v) => setAction(v)}
          options={[
            { value: 'all', label: '全部' },
            { value: 'allowed', label: '放行' },
            { value: 'blocked', label: '拦截' },
          ]}
        />
        <DatePicker.RangePicker
          value={dateRange}
          onChange={(v) => setDateRange(v && v[0] && v[1] ? (v as [Dayjs, Dayjs]) : null)}
        />
        <Button type="primary" icon={<SearchOutlined />} onClick={applyQuery}>查询</Button>
        <Button icon={<ReloadOutlined />} onClick={reset}>重置</Button>
        <Button icon={<DownloadOutlined />} loading={exporting} onClick={doExport}>导出 CSV</Button>
      </Space>

      <Table<HostCommandAuditItem>
        size="small"
        rowKey="id"
        loading={loading}
        dataSource={items}
        columns={columns}
        scroll={{ x: 1100 }}
        locale={{ emptyText: <Empty description="暂无记录" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
        pagination={{
          current: page,
          pageSize: PAGE_SIZE,
          total,
          showSizeChanger: false,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (p) => setPage(p),
        }}
      />
    </>
  )
}

/** 抽屉壳：终端页「审计」按钮用 */
export default function HostCommandAuditDrawer({ open, hostId, onClose }: {
  open: boolean
  hostId?: number | null
  onClose: () => void
}) {
  return (
    <Drawer title="命令审计" width={1080} open={open} onClose={onClose} destroyOnClose>
      <HostCommandAuditPanel active={open} hostId={hostId ?? null} />
    </Drawer>
  )
}
