// 工单列表 + 详情抽屉 + 新建工单（N1 平台内部工单）
import { useEffect, useState } from 'react'
import { Button, Card, Col, Drawer, Empty, Form, Input, List, Modal, Popconfirm, Row, Select, Space, Statistic, Table, Tag, Typography, DatePicker, message } from 'antd'
import dayjs, { type Dayjs } from 'dayjs'
import { PlusOutlined, UserOutlined, CommentOutlined, DeleteOutlined, ClockCircleOutlined, SyncOutlined, CheckCircleOutlined, CloseCircleOutlined, InboxOutlined } from '@ant-design/icons'
import {
  getUsers, getClusters, getDBConnections,
  getTickets, getTicket, createTicket, updateTicket, assignTicket, addTicketComment, deleteTicket, getTicketStats,
} from '../services/api'
import type { Cluster, User, Ticket, TicketDetail, TicketComment, TicketStats } from '../types'

// ============ 本地类型（不修改 api.ts/types.ts，避免与并行任务冲突） ============
// ============ 常量（与后端口径一致） ============
const STATUS_LABEL: Record<string, string> = {
  open: '待受理', processing: '处理中', resolved: '已解决', closed: '已关闭',
}
const STATUS_COLOR: Record<string, string> = {
  open: 'default', processing: 'blue', resolved: 'green', closed: 'default',
}
const PRIORITY_LABEL: Record<string, string> = {
  low: '低', medium: '中', high: '高', urgent: '紧急',
}
const PRIORITY_COLOR: Record<string, string> = {
  low: 'default', medium: 'blue', high: 'orange', urgent: 'red',
}
const SOURCE_LABEL: Record<string, string> = {
  alert: '告警', approval: '审批', manual: '手动',
}
const NEXT_STATUS: Record<string, string[]> = {
  open: ['processing', 'resolved', 'closed'],
  processing: ['resolved', 'closed'],
  resolved: ['closed'],
  closed: [],
}

// ============ 类型化请求门面（复用默认 api 实例，拦截器已解包 body.data） ============
export default function Tickets() {
  const [items, setItems] = useState<Ticket[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize] = useState(20)
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState<string | undefined>()
  const [priority, setPriority] = useState<string | undefined>()
  const [source, setSource] = useState<string | undefined>()
  const [clusterId, setClusterId] = useState<number | undefined>()
  const [keyword, setKeyword] = useState('')
  const [dateRange, setDateRange] = useState<[Dayjs, Dayjs] | null>(null)

  const [detail, setDetail] = useState<TicketDetail | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [commentText, setCommentText] = useState('')
  const [assignModalOpen, setAssignModalOpen] = useState(false)
  const [assignUserId, setAssignUserId] = useState<number | undefined>()
  const [createOpen, setCreateOpen] = useState(false)
  const [createForm] = Form.useForm()

  const [users, setUsers] = useState<User[]>([])
  const [clusters, setClusters] = useState<Cluster[]>([])
  // N1 补充：DB 类工单可关联数据库连接
  const [dbOptions, setDbOptions] = useState<{ value: number; label: string }[]>([])
  // 当前用户处理工单统计（处理人=当前用户 按状态分布）
  const [stats, setStats] = useState<TicketStats | null>(null)

  const loadList = () => {
    setLoading(true)
    const params: Record<string, unknown> = { page, page_size: pageSize }
    if (status) params.status = status
    if (priority) params.priority = priority
    if (source) params.source = source
    if (clusterId) params.cluster_id = clusterId
    if (keyword) params.keyword = keyword
    if (dateRange?.[0]) params.created_after = dateRange[0].format('YYYY-MM-DD 00:00:00')
    if (dateRange?.[1]) params.created_before = dateRange[1].format('YYYY-MM-DD 23:59:59')
    getTickets(params)
      .then((d) => { setItems(d?.items ?? []); setTotal(d?.total ?? 0) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }

  const loadStats = () => {
    getTicketStats().then((d) => setStats(d ?? null)).catch(() => {})
  }

  const loadDetail = (id: number) => {
    getTicket(id)
      .then((d) => setDetail(d))
      .catch(() => {})
  }

  useEffect(() => { loadList() }, [page, status, priority, source, clusterId, keyword, dateRange]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { loadStats() }, [])
  useEffect(() => {
    getUsers().then((d) => setUsers(d ?? [])).catch(() => {})
    getClusters().then((d) => setClusters(d ?? [])).catch(() => {})
    getDBConnections().then((d) => setDbOptions(((d ?? []) as { id: number; name: string }[]).map((x) => ({ value: x.id, label: x.name })))).catch(() => {})
  }, [])

  const openDetail = (t: Ticket) => {
    setDetail(null)
    setDrawerOpen(true)
    loadDetail(t.id)
  }

  const doTransition = async (t: Ticket, next: string) => {
    await updateTicket(t.id, { status: next })
    message.success(`已流转到「${STATUS_LABEL[next]}」`)
    loadDetail(t.id)
    loadList()
    loadStats()
  }

  const doAssign = async () => {
    if (!detail) return
    await assignTicket(detail.ticket.id, assignUserId ?? null)
    message.success('已指派')
    setAssignModalOpen(false)
    setAssignUserId(undefined)
    loadDetail(detail.ticket.id)
    loadList()
    loadStats()
  }

  const doComment = async () => {
    if (!detail || !commentText.trim()) { message.warning('请输入留言内容'); return }
    await addTicketComment(detail.ticket.id, commentText.trim())
    message.success('已留言')
    setCommentText('')
    loadDetail(detail.ticket.id)
  }

  const isAdmin = (() => {
    try { return (JSON.parse(localStorage.getItem('user') || '{}') as { role?: string }).role === 'admin' } catch { return false }
  })()

  const doDelete = async (id: number) => {
    try {
      await deleteTicket(id)
      message.success('已删除')
      setDrawerOpen(false)
      loadList()
      loadStats()
    } catch { /* 拦截器已提示 */ }
  }

  const doCreate = async () => {
    const v = await createForm.validateFields()
    await createTicket(v)
    message.success('已建单')
    setCreateOpen(false)
    createForm.resetFields()
    loadList()
    loadStats()
  }

  const columns = [
    { title: '工单号', dataIndex: 'ticket_no', width: 160, render: (v: string) => <Typography.Text code>{v}</Typography.Text> },
    { title: '标题', dataIndex: 'title', ellipsis: true },
    {
      title: '来源', dataIndex: 'source', width: 80,
      render: (v: string) => <Tag>{SOURCE_LABEL[v] ?? v}</Tag>,
    },
    {
      title: '优先级', dataIndex: 'priority', width: 80,
      render: (v: string) => <Tag color={PRIORITY_COLOR[v]}>{PRIORITY_LABEL[v] ?? v}</Tag>,
    },
    {
      title: '状态', dataIndex: 'status', width: 90,
      render: (v: string) => <Tag color={STATUS_COLOR[v]}>{STATUS_LABEL[v] ?? v}</Tag>,
    },
    { title: '集群', dataIndex: 'cluster_name', width: 120, render: (v: string) => v || '-' },
    { title: '数据库', dataIndex: 'db_name', width: 130, render: (v: string) => v || '-' },
    { title: '资源', dataIndex: 'resource_name', width: 160, render: (v: string) => v || '-' },
    { title: '处理人', dataIndex: 'assignee_name', width: 100, render: (v: string) => v || '-' },
    { title: '创建时间', dataIndex: 'created_at', width: 170 },
    {
      title: '操作', width: 80, fixed: 'right' as const,
      render: (_: unknown, t: Ticket) => <Button type="link" size="small" onClick={() => openDetail(t)}>详情</Button>,
    },
  ]

  const userOptions = users.map((u) => ({ value: u.id, label: `${u.username}（${u.role}）` }))
  const clusterOptions = clusters.map((c) => ({ value: c.id, label: c.name }))

  const current = detail?.ticket

  return (
    <div>
      <Card size="small" style={{ marginBottom: 12 }} title="我处理的工单">
        <Row gutter={[12, 12]}>
          <Col xs={12} sm={8} md={4}>
            <Statistic title="待受理" value={stats?.mine?.open ?? 0} prefix={<ClockCircleOutlined style={{ color: '#8c8c8c' }} />} />
          </Col>
          <Col xs={12} sm={8} md={4}>
            <Statistic title="处理中" value={stats?.mine?.processing ?? 0} prefix={<SyncOutlined spin style={{ color: '#1677ff' }} />} />
          </Col>
          <Col xs={12} sm={8} md={4}>
            <Statistic title="已解决" value={stats?.mine?.resolved ?? 0} prefix={<CheckCircleOutlined style={{ color: '#52c41a' }} />} />
          </Col>
          <Col xs={12} sm={8} md={4}>
            <Statistic title="已关闭" value={stats?.mine?.closed ?? 0} prefix={<CloseCircleOutlined style={{ color: '#8c8c8c' }} />} />
          </Col>
          <Col xs={12} sm={8} md={4}>
            <Statistic title="总计" value={stats?.mine?.total ?? 0} prefix={<InboxOutlined style={{ color: '#722ed1' }} />} />
          </Col>
          <Col xs={12} sm={8} md={4}>
            <Statistic title="我创建" value={stats?.created ?? 0} prefix={<UserOutlined style={{ color: '#faad14' }} />} />
          </Col>
        </Row>
      </Card>

      <Space style={{ marginBottom: 12 }} wrap>
        <Select allowClear placeholder="状态（默认待处理/处理中）" style={{ width: 170 }} value={status}
          onChange={(v) => { setStatus(v); setPage(1) }}
          options={Object.entries(STATUS_LABEL).map(([k, v]) => ({ value: k, label: v }))} />
        <Select allowClear placeholder="优先级" style={{ width: 110 }} value={priority}
          onChange={(v) => { setPriority(v); setPage(1) }}
          options={Object.entries(PRIORITY_LABEL).map(([k, v]) => ({ value: k, label: v }))} />
        <Select allowClear placeholder="来源" style={{ width: 110 }} value={source}
          onChange={(v) => { setSource(v); setPage(1) }}
          options={Object.entries(SOURCE_LABEL).map(([k, v]) => ({ value: k, label: v }))} />
        <Input.Search allowClear placeholder="搜工单号/标题/资源" style={{ width: 220 }}
          onSearch={(v) => { setKeyword(v); setPage(1) }} />
        <DatePicker.RangePicker value={dateRange}
          onChange={(v) => { setDateRange(v && v[0] && v[1] ? (v as [Dayjs, Dayjs]) : null); setPage(1) }} />
        <Select allowClear showSearch optionFilterProp="label" placeholder="集群" style={{ width: 180 }}
          value={clusterId} onChange={(v) => { setClusterId(v); setPage(1) }} options={clusterOptions} />
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建工单</Button>
      </Space>

      <Table rowKey="id" loading={loading} dataSource={items} columns={columns} scroll={{ x: 1200 }}
        pagination={{
          current: page, pageSize, total, showSizeChanger: false,
          showTotal: (t) => `共 ${t} 条`, onChange: setPage,
        }} />

      {/* 详情抽屉 */}
      <Drawer width={560} title={current ? `${current.ticket_no} · ${current.title}` : '工单详情'}
        open={drawerOpen} onClose={() => setDrawerOpen(false)}>
        {current && (
          <Space direction="vertical" style={{ width: '100%' }} size={12}>
            <Space wrap>
              <Tag color={STATUS_COLOR[current.status]}>{STATUS_LABEL[current.status]}</Tag>
              <Tag color={PRIORITY_COLOR[current.priority]}>{PRIORITY_LABEL[current.priority]}</Tag>
              <Tag>{SOURCE_LABEL[current.source]}</Tag>
            </Space>
            <div style={{ fontSize: 13, color: '#595959' }}>
              <div>集群：{current.cluster_name || current.cluster_id || '-'}</div>
              <div>命名空间：{current.namespace || '-'}</div>
              <div>资源：{current.resource_name || '-'}</div>
              <div>处理人：{current.assignee_name || '未指派'}</div>
              <div>创建人：{current.creator_name || '-'}</div>
              <div>创建时间：{current.created_at}</div>
              {current.alert_id != null && <div>关联告警：#{current.alert_id}</div>}
              {current.approval_id != null && <div>关联审批：#{current.approval_id}</div>}
            </div>
            {current.description && (
              <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', background: '#fafafa', padding: 8, borderRadius: 4 }}>
                {current.description}
              </Typography.Paragraph>
            )}

            {/* 操作区 */}
            <Space wrap>
              <Button icon={<UserOutlined />} onClick={() => { setAssignUserId(current.assignee_id ?? undefined); setAssignModalOpen(true) }}>
                {current.assignee_id ? '改派' : '指派'}
              </Button>
              {NEXT_STATUS[current.status]?.map((s) => (
                <Button key={s} type={s === 'closed' ? 'default' : 'primary'} onClick={() => doTransition(current, s)}>
                  流转到「{STATUS_LABEL[s]}」
                </Button>
              ))}
              {isAdmin && (
                <Popconfirm title="删除该工单？（含留言记录，不可恢复）" onConfirm={() => doDelete(current.id)}>
                  <Button danger icon={<DeleteOutlined />}>删除</Button>
                </Popconfirm>
              )}
            </Space>

            {/* 留言区 */}
            <div>
              <Space.Compact style={{ width: '100%' }}>
                <Input.TextArea rows={2} value={commentText} onChange={(e) => setCommentText(e.target.value)}
                  placeholder="输入留言…" />
              </Space.Compact>
              <Button type="primary" size="small" icon={<CommentOutlined />} style={{ marginTop: 6 }}
                onClick={doComment}>留言</Button>
            </div>

            {/* 留言/流转记录 */}
            <div>
              <div style={{ fontWeight: 600, marginBottom: 6 }}>动态</div>
              {detail && detail.comments.length === 0 && <Empty description="暂无动态" image={Empty.PRESENTED_IMAGE_SIMPLE} />}
              <List size="small" dataSource={detail?.comments ?? []}
                renderItem={(c: TicketComment) => (
                  <List.Item>
                    <List.Item.Meta
                      title={
                        <span style={{ fontSize: 12, color: c.type === 'action' ? '#1677ff' : '#595959' }}>
                          {c.type === 'action' ? '【流转】' : ''}{c.username || '-'} · {c.created_at}
                        </span>
                      }
                      description={<span style={{ whiteSpace: 'pre-wrap' }}>{c.content}</span>}
                    />
                  </List.Item>
                )} />
            </div>
          </Space>
        )}
      </Drawer>

      {/* 指派 */}
      <Modal title="指派处理人" open={assignModalOpen} onOk={doAssign} onCancel={() => setAssignModalOpen(false)} destroyOnClose>
        <Select allowClear showSearch optionFilterProp="label" placeholder="选择处理人（留空=取消指派）"
          style={{ width: '100%' }} value={assignUserId} onChange={setAssignUserId} options={userOptions} />
      </Modal>

      {/* 新建工单 */}
      <Modal title="新建工单" open={createOpen} onOk={doCreate} onCancel={() => setCreateOpen(false)} destroyOnClose>
        <Form form={createForm} layout="vertical" initialValues={{ priority: 'medium' }}>
          <Form.Item name="title" label="标题" rules={[{ required: true, message: '请输入标题' }]}>
            <Input placeholder="工单标题" maxLength={200} />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={4} placeholder="问题描述（可选）" maxLength={5000} />
          </Form.Item>
          <Form.Item name="priority" label="优先级">
            <Select options={Object.entries(PRIORITY_LABEL).map(([k, v]) => ({ value: k, label: v }))} />
          </Form.Item>
          <Form.Item name="cluster_id" label="所属集群（可选）">
            <Select allowClear showSearch optionFilterProp="label" placeholder="选择集群" options={clusterOptions} />
          </Form.Item>
          <Form.Item name="db_connection_id" label="数据库连接（可选）">
            <Select allowClear placeholder="不关联数据库" options={dbOptions} />
          </Form.Item>
          <Form.Item name="namespace" label="命名空间（可选）">
            <Input placeholder="如 default" maxLength={100} />
          </Form.Item>
          <Form.Item name="resource_name" label="资源名（可选）">
            <Input placeholder="如 Pod / Deployment 名" maxLength={200} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
