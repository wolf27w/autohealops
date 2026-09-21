// 审批列表页
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Tabs, Card, Space, Button, Table, Tag, Modal, Input, message, Empty, Checkbox, Select, Tooltip, Pagination } from 'antd'
import { getApprovals, getApproval, decideApproval, batchDecideApprovals, addApprover, transferApproval, getApprovalCommentTemplates } from '../services/api'
import type { Approval } from '../types'
import StatusTag from '../components/StatusTag'

export default function ApprovalList() {
  // react-query：待审批（服务端分页，不再一次拉 500 被 clamp 静默截断）+ 已处理（分页）+ 意见模板（一次性）
  const [handledPage, setHandledPage] = useState(1)
  const [pendingPage, setPendingPage] = useState(1)
  const PENDING_PAGE_SIZE = 20
  const [pageSize] = useState(20)
  const { data: pendingData, refetch: refetchPending } = useQuery({
    queryKey: ['approvals', 'pending', pendingPage],
    queryFn: () => getApprovals({ status: 'pending', page: pendingPage, page_size: PENDING_PAGE_SIZE }),
    refetchInterval: 15000,
  })
  const { data: handledData, refetch: refetchHandled } = useQuery({
    queryKey: ['approvals', 'handled', handledPage],
    queryFn: () => getApprovals({ status: 'handled', page: handledPage, page_size: pageSize }),
    refetchInterval: 15000,
  })
  const { data: templates = [] } = useQuery({
    queryKey: ['approval-comment-templates'],
    queryFn: () => getApprovalCommentTemplates(),
  })
  const refetch = () => { refetchPending(); refetchHandled() }
  const [modalOpen, setModalOpen] = useState(false)
  const [current, setCurrent] = useState<Approval | null>(null)
  const [comment, setComment] = useState('')
  const [detail, setDetail] = useState<Approval | null>(null)
  const [selected, setSelected] = useState<number[]>([])
  const [assignTarget, setAssignTarget] = useState<Approval | null>(null)  // 加签
  const [assignName, setAssignName] = useState('')
  const [transferTarget, setTransferTarget] = useState<Approval | null>(null)  // 转办
  const [transferName, setTransferName] = useState('')

  const openApproval = (a: Approval) => {
    setCurrent(a)
    setDetail(null)
    setModalOpen(true)
    getApproval(a.id).then((d) => setDetail(d)).catch(() => {})
  }

  const handleDecide = async (action: 'approve' | 'reject') => {
    await decideApproval(current!.id, { action, comment })
    message.success('已处理')
    setModalOpen(false)
    setComment('')
    refetch()
  }

  // 批量审批
  const handleBatch = async (action: 'approve' | 'reject') => {
    if (!selected.length) { message.warning('请先勾选待审批项'); return }
    const r = await batchDecideApprovals(selected, action, '')
    message.success(`批量${action === 'approve' ? '批准' : '拒绝'}完成：成功 ${r?.ok ?? 0}，跳过 ${r?.skipped ?? 0}`)
    setSelected([])
    refetch()
  }

  // 加签
  const handleAddApprover = async () => {
    if (!assignName.trim()) { message.warning('请输入用户名'); return }
    const r = await addApprover(assignTarget!.id, assignName.trim())
    if (r?.ok) { message.success('已加签'); setAssignTarget(null); setAssignName(''); refetch() }
    else message.error(r?.reason || '加签失败')
  }

  // 转办
  const handleTransfer = async () => {
    if (!transferName.trim()) { message.warning('请输入用户名'); return }
    const r = await transferApproval(transferTarget!.id, transferName.trim())
    if (r?.ok) { message.success('已转办'); setTransferTarget(null); setTransferName(''); refetch() }
    else message.error(r?.reason || '转办失败')
  }

  // 审批人/审核人列表（DB 存 JSON 字符串）→ 直接显示用户名
  const formatApprovers = (v: unknown) => {
    if (!v) return '-'
    try {
      const arr = typeof v === 'string' ? JSON.parse(v) : v
      return Array.isArray(arr) ? arr.join('、') : String(v)
    } catch {
      return String(v)
    }
  }

  const pending = pendingData?.items ?? []
  const pendingTotal = pendingData?.total ?? 0
  const handled = handledData?.items ?? []
  const handledTotal = handledData?.total ?? 0
  const manualPending = pending.filter((a) => a.kind === 'manual_assist')
  const riskPending = pending.filter((a) => a.kind !== 'manual_assist')

  const renderPendingList = (list: Approval[]) => (
    <div>
      <Space style={{ marginBottom: 8 }}>
        <Button size="small" type="primary" disabled={!selected.length} onClick={() => handleBatch('approve')}>批量批准 ({selected.length})</Button>
        <Button size="small" danger disabled={!selected.length} onClick={() => handleBatch('reject')}>批量拒绝 ({selected.length})</Button>
        <Button size="small" type="link" onClick={() => setSelected(list.map((a) => a.id))}>全选</Button>
        <Button size="small" type="link" onClick={() => setSelected([])}>清空</Button>
      </Space>
      <Space direction="vertical" style={{ width: '100%' }}>
        {list.map((a) => (
          <Card key={a.id} size="small">
            <Space direction="vertical" style={{ width: '100%' }} size={4}>
              <Space>
                <Checkbox checked={selected.includes(a.id)} onChange={(e) => {
                  setSelected(e.target.checked ? [...selected, a.id] : selected.filter((x) => x !== a.id))
                }} />
                <Tag color={a.kind === 'manual_assist' ? 'orange' : 'blue'}>
                  {a.kind === 'manual_assist' ? '人工协助' : '风险审批'}
                </Tag>
                <span>审批 #{a.id} ｜ 级别 {a.required_level === 5 ? '双人' : '单人'}</span>
                <Button size="small" type="primary" onClick={() => openApproval(a)}>审批</Button>
                <Tooltip title="追加一名审批人"><Button size="small" onClick={() => { setAssignTarget(a); setAssignName('') }}>加签</Button></Tooltip>
                <Tooltip title="转交给他人审批"><Button size="small" onClick={() => { setTransferTarget(a); setTransferName('') }}>转办</Button></Tooltip>
              </Space>
              {a.alert_fault_type && (
                <div style={{ color: '#595959', fontSize: 13 }}>
                  故障：<Tag color="red">{a.alert_fault_type}</Tag>
                  资源：{a.alert_resource} ｜ 命名空间：{a.alert_namespace} ｜ 级别：{a.alert_severity}
                </div>
              )}
            </Space>
          </Card>
        ))}
        {list.length === 0 && <Empty description="本页暂无该类型待审批（可翻页查看）" />}
      </Space>
      {/* 待审批服务端分页：后端不分 kind，两个 tab 共用同一总页数 */}
      <div style={{ marginTop: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
        <span style={{ color: '#999', fontSize: 12 }}>
          待审批共 {pendingTotal} 条（人工协助 + 风险审批合计，每页 {PENDING_PAGE_SIZE} 条）
        </span>
        <Pagination size="small" current={pendingPage} pageSize={PENDING_PAGE_SIZE} total={pendingTotal}
          showSizeChanger={false} onChange={setPendingPage} />
      </div>
    </div>
  )

  const renderHandled = () => (
    <Table rowKey="id" dataSource={handled}
      pagination={{ current: handledPage, pageSize, total: handledTotal, showSizeChanger: false, onChange: setHandledPage }}
      columns={[
        { title: 'ID', dataIndex: 'id' },
        { title: '计划', dataIndex: 'plan_id' },
        { title: '状态', dataIndex: 'status', render: (s) => <StatusTag status={s} label={s === 'cancelled' ? '解除' : undefined} /> },
        { title: '审批人', dataIndex: 'approved_by', render: (v) => formatApprovers(v) },
        { title: '决定时间', dataIndex: 'decided_at' },
      ]} />
  )

  return (
    <div>
      <Tabs
        items={[
          { key: 'manual', label: '人工协助', children: renderPendingList(manualPending) },
          { key: 'risk', label: '风险审批', children: renderPendingList(riskPending) },
          { key: 'handled', label: '已处理', children: renderHandled() },
        ]}
      />
      <Modal title="审批操作" open={modalOpen} onCancel={() => setModalOpen(false)}
        footer={
          <Space>
            <Button danger onClick={() => handleDecide('reject')}>拒绝</Button>
            <Button type="primary" onClick={() => handleDecide('approve')}>批准</Button>
          </Space>
        }>
        {current && (
          <div style={{ marginBottom: 12, padding: 8, background: '#fafafa', borderRadius: 4 }}>
            <div style={{ marginBottom: 6 }}>
              <Tag color={current.kind === 'manual_assist' ? 'orange' : 'blue'}>
                {current.kind === 'manual_assist' ? '人工协助' : '风险审批'}
              </Tag>
              <b>审批 #{current.id}</b>
            </div>
            {(detail?.alert || current.alert_fault_type) && (
              <div style={{ fontSize: 13, color: '#595959', whiteSpace: 'pre-wrap' }}>
                <div>故障类型：{detail?.alert?.fault_type || current.alert_fault_type}</div>
                <div>资源：{detail?.alert?.resource_name || current.alert_resource}</div>
                <div>命名空间：{detail?.alert?.namespace || current.alert_namespace}</div>
                <div>级别：{detail?.alert?.severity || current.alert_severity}</div>
                <div style={{ marginTop: 4 }}>告警详情：{(detail?.alert?.message || current.alert_message || '').slice(0, 300)}</div>
              </div>
            )}
            {detail?.plan_summary && (
              <div style={{ fontSize: 13, color: '#595959', marginTop: 6 }}>方案摘要：{detail.plan_summary}</div>
            )}
          </div>
        )}
        <div style={{ marginBottom: 8 }}>
          <Select placeholder="选择意见模板（可选）" allowClear style={{ width: '100%' }}
            onChange={(v) => setComment(v)} options={templates.map((t) => ({ value: t, label: t }))} />
        </div>
        <Input.TextArea value={comment} onChange={(e) => setComment(e.target.value)} placeholder="审批意见（可选）" />
      </Modal>

      {/* 加签 */}
      <Modal title={`加签：审批 #${assignTarget?.id ?? ''}`} open={!!assignTarget} onOk={handleAddApprover} onCancel={() => setAssignTarget(null)} destroyOnClose>
        <Input placeholder="追加的审批人用户名，如 admin" value={assignName} onChange={(e) => setAssignName(e.target.value)} />
      </Modal>

      {/* 转办 */}
      <Modal title={`转办：审批 #${transferTarget?.id ?? ''}`} open={!!transferTarget} onOk={handleTransfer} onCancel={() => setTransferTarget(null)} destroyOnClose>
        <Input placeholder="转交给谁（用户名）" value={transferName} onChange={(e) => setTransferName(e.target.value)} />
      </Modal>
    </div>
  )
}
