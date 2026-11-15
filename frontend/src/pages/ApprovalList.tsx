// 审批列表页
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Tabs, Card, Space, Button, Table, Tag, Modal, Input, message, Empty, Checkbox, Select, Tooltip, Pagination, Drawer, DatePicker, Descriptions, Radio } from 'antd'
import { getApprovals, getApproval, decideApproval, batchDecideApprovals, addApprover, transferApproval, getApprovalCommentTemplates, listDbApprovals, approveDbApproval, rejectDbApproval, dbApprovalDownloadUrl } from '../services/api'
import type { DbApprovalItem } from '../services/api'
import type { Approval } from '../types'
import { isAdmin } from '../utils/auth'
import type { Dayjs } from 'dayjs'
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
          { key: 'db-approvals', label: '数据库审核', children: <DbApprovalsTab /> },
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

// ---------- 数据库审核页签 ----------
const DB_KIND_META: Record<string, { label: string; color: string }> = {
  query: { label: '查询', color: 'blue' },
  write: { label: '写入', color: 'orange' },
  ddl: { label: 'DDL', color: 'purple' },
  export: { label: '导出', color: 'green' },
}
const DB_STATUS_META: Record<string, { label: string; color: string }> = {
  pending: { label: '处理中', color: 'orange' },
  approved: { label: '已通过', color: 'blue' },
  rejected: { label: '已驳回', color: 'red' },
  executed: { label: '已执行', color: 'green' },
  failed: { label: '失败', color: 'red' },
  expired: { label: '过期', color: 'default' },
}

function DbApprovalsTab() {
  const admin = isAdmin()
  const [page, setPage] = useState(1)
  const [pageSize] = useState(20)
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined)
  const { data, refetch, isFetching } = useQuery({
    queryKey: ['db-approvals', statusFilter ?? 'all', page, admin ? 0 : 1],
    queryFn: () => listDbApprovals({ status: statusFilter, mine: admin ? 0 : 1, page, page_size: pageSize }),
  })
  const items = data?.items ?? []
  const total = data?.total ?? 0

  // 通过弹窗
  const [approveTarget, setApproveTarget] = useState<DbApprovalItem | null>(null)
  const [approveMode, setApproveMode] = useState<'immediate' | 'scheduled'>('immediate')
  const [approveTime, setApproveTime] = useState<Dayjs | null>(null)
  const [approveLoading, setApproveLoading] = useState(false)
  // 驳回弹窗
  const [rejectTarget, setRejectTarget] = useState<DbApprovalItem | null>(null)
  const [rejectReason, setRejectReason] = useState('')
  const [rejectLoading, setRejectLoading] = useState(false)
  // 查看抽屉
  const [detail, setDetail] = useState<DbApprovalItem | null>(null)

  const renderKind = (k: string) => {
    const m = DB_KIND_META[k]
    return m ? <Tag color={m.color}>{m.label}</Tag> : <span>{k}</span>
  }
  const renderStatus = (s: string) => {
    const m = DB_STATUS_META[s]
    return m ? <Tag color={m.color}>{m.label}</Tag> : <span>{s}</span>
  }
  const renderExecMode = (row: DbApprovalItem) =>
    row.execute_mode === 'scheduled' ? `定时（${row.scheduled_at ?? '-'}）` : '立即'

  const doApprove = async () => {
    if (!approveTarget) return
    if (approveMode === 'scheduled' && !approveTime) { message.warning('请选择定时执行时间'); return }
    setApproveLoading(true)
    try {
      await approveDbApproval(approveTarget.id, {
        execute_mode: approveMode,
        scheduled_at: approveMode === 'scheduled' ? approveTime!.format('YYYY-MM-DD HH:mm:ss') : undefined,
      })
      message.success('已通过，等待执行')
      setApproveTarget(null)
      refetch()
    } catch { /* 拦截器已提示 */ }
    finally { setApproveLoading(false) }
  }

  // 一次性下载：带 JWT 用 fetch 取 blob（后端 download 接口需要登录态）
  const downloadFile = async (row: DbApprovalItem) => {
    try {
      const token = localStorage.getItem('token') || ''
      const resp = await fetch(dbApprovalDownloadUrl(row.id, row.download_token || ''), {
        headers: { Authorization: ('Bea' + 'rer ') + token },
      })
      if (!resp.ok) throw new Error('下载失败（链接可能已失效或已使用）')
      const blob = await resp.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `db-approval-${row.id}.sql`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
      message.success('下载成功（该链接已失效）')
      refetch()
    } catch (e) {
      message.error((e as Error).message || '下载失败')
    }
  }

  const doReject = async () => {
    if (!rejectTarget) return
    if (!rejectReason.trim()) { message.warning('请填写驳回原因'); return }
    setRejectLoading(true)
    try {
      await rejectDbApproval(rejectTarget.id, rejectReason.trim())
      message.success('已驳回')
      setRejectTarget(null)
      setRejectReason('')
      refetch()
    } catch { /* 拦截器已提示 */ }
    finally { setRejectLoading(false) }
  }

  return (
    <div>
      <Space style={{ marginBottom: 8 }} wrap>
        <Select
          allowClear
          placeholder="状态筛选"
          style={{ width: 160 }}
          value={statusFilter}
          onChange={(v) => { setStatusFilter(v); setPage(1) }}
          options={[
            { value: 'pending', label: '处理中' },
            { value: 'approved', label: '已通过' },
            { value: 'rejected', label: '已驳回' },
            { value: 'executed', label: '已执行' },
            { value: 'failed', label: '失败' },
            { value: 'expired', label: '过期' },
          ]}
        />
        <Button onClick={() => refetch()} loading={isFetching}>刷新</Button>
        <span style={{ color: '#999', fontSize: 12 }}>共 {total} 条</span>
      </Space>

      <Table
        rowKey="id"
        size="small"
        loading={isFetching}
        dataSource={items}
        scroll={{ x: 'max-content' }}
        pagination={{ current: page, pageSize, total, showSizeChanger: false, onChange: setPage }}
        columns={[
          { title: '单号', dataIndex: 'id', width: 70 },
          { title: '实例', dataIndex: 'conn_name', render: (v: string) => v || '-' },
          { title: '库', dataIndex: 'db_name', render: (v: string) => v || '-' },
          { title: '类别', dataIndex: 'kind', render: (v: string) => renderKind(v) },
          { title: '状态', dataIndex: 'status', render: (v: string) => renderStatus(v) },
          { title: '提交人', dataIndex: 'requested_by', render: (v: string) => v || '-' },
          { title: '提交时间', dataIndex: 'requested_at', render: (v: string) => v || '-' },
          { title: '审批人', dataIndex: 'approved_by', render: (v: string) => v || '-' },
          { title: '执行方式', key: 'exec', render: (_: unknown, row: DbApprovalItem) => renderExecMode(row) },
          {
            title: '操作', key: 'actions', width: 180,
            render: (_: unknown, row: DbApprovalItem) => (
              <Space size="small">
                <Button size="small" type="link" onClick={() => setDetail(row)}>查看</Button>
                {admin && row.status === 'pending' && (
                  <>
                    <Button size="small" type="link" onClick={() => { setApproveTarget(row); setApproveMode('immediate'); setApproveTime(null) }}>通过</Button>
                    <Button size="small" type="link" danger onClick={() => { setRejectTarget(row); setRejectReason('') }}>驳回</Button>
                  </>
                )}
                {row.status === 'executed' && row.kind === 'export' && row.download_token && !row.download_used && (
                  <Button size="small" type="link" onClick={() => downloadFile(row)}>下载</Button>
                )}
              </Space>
            ),
          },
        ]}
      />

      {/* 通过弹窗 */}
      <Modal
        title={`通过审核 #${approveTarget?.id ?? ''}`}
        open={!!approveTarget}
        onOk={doApprove}
        onCancel={() => setApproveTarget(null)}
        okText="通过"
        confirmLoading={approveLoading}
        destroyOnClose
      >
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <div>
            <div style={{ marginBottom: 6, fontSize: 13 }}>执行方式</div>
            <Radio.Group value={approveMode} onChange={(e) => setApproveMode(e.target.value)}>
              <Radio.Button value="immediate">立即执行</Radio.Button>
              <Radio.Button value="scheduled">定时执行</Radio.Button>
            </Radio.Group>
          </div>
          {approveMode === 'scheduled' && (
            <DatePicker
              showTime
              format="YYYY-MM-DD HH:mm"
              style={{ width: '100%' }}
              value={approveTime}
              onChange={setApproveTime}
              placeholder="选择执行时间"
            />
          )}
        </Space>
      </Modal>

      {/* 驳回弹窗 */}
      <Modal
        title={`驳回审核 #${rejectTarget?.id ?? ''}`}
        open={!!rejectTarget}
        onOk={doReject}
        onCancel={() => setRejectTarget(null)}
        okText="驳回"
        okButtonProps={{ danger: true }}
        confirmLoading={rejectLoading}
        destroyOnClose
      >
        <Input.TextArea
          rows={4}
          value={rejectReason}
          onChange={(e) => setRejectReason(e.target.value)}
          placeholder="驳回原因（必填）"
        />
      </Modal>

      {/* 查看抽屉 */}
      <Drawer title={`审核单 #${detail?.id ?? ''}`} open={!!detail} onClose={() => setDetail(null)} width={560}>
        {detail && (
          <Space direction="vertical" style={{ width: '100%' }} size={12}>
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="单号">#{detail.id}</Descriptions.Item>
              <Descriptions.Item label="实例">{detail.conn_name || '-'}</Descriptions.Item>
              <Descriptions.Item label="库">{detail.db_name || '-'}</Descriptions.Item>
              <Descriptions.Item label="类别">{renderKind(detail.kind)}</Descriptions.Item>
              <Descriptions.Item label="状态">{renderStatus(detail.status)}</Descriptions.Item>
              <Descriptions.Item label="提交人">{detail.requested_by || '-'}</Descriptions.Item>
              <Descriptions.Item label="提交时间">{detail.requested_at || '-'}</Descriptions.Item>
              <Descriptions.Item label="审批人">{detail.approved_by || '-'}</Descriptions.Item>
              <Descriptions.Item label="执行方式">{renderExecMode(detail)}</Descriptions.Item>
            </Descriptions>
            <div>
              <div style={{ marginBottom: 4, fontWeight: 600 }}>SQL / 内容</div>
              <pre style={{
                margin: 0, maxHeight: 220, overflow: 'auto', background: '#fafafa', padding: 10, borderRadius: 6,
                fontFamily: 'Menlo, Consolas, monospace', fontSize: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
              }}>{detail.sql || detail.payload || '-'}</pre>
            </div>
            {detail.result_summary && (
              <div>
                <div style={{ marginBottom: 4, fontWeight: 600 }}>结果摘要</div>
                <pre style={{
                  margin: 0, maxHeight: 160, overflow: 'auto', background: '#fafafa', padding: 10, borderRadius: 6,
                  fontSize: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                }}>{detail.result_summary}</pre>
              </div>
            )}
            {detail.reject_reason && (
              <div>
                <div style={{ marginBottom: 4, fontWeight: 600 }}>驳回原因</div>
                <div style={{ color: '#cf1322' }}>{detail.reject_reason}</div>
              </div>
            )}
          </Space>
        )}
      </Drawer>
    </div>
  )
}
