// 告警列表页：筛选 + 表格 + 批量解决 + 行展开诊断 + 日志查看（诊断已自动，保留手动重试）
import { envLabel, envColor } from '../utils/env'
import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Table, Select, Button, Space, Drawer, message, Modal, Form, Input, Tag, Tooltip, Badge } from 'antd'
import { getAlertsPaged, diagnoseAlert, getDiagnosis, getAlertDescribe, batchResolveAlerts, manualResolveAlert, searchLogs, assignAlert, ackAlert, getAlertComments, addAlertComment } from '../services/api'
import { canWrite } from '../utils/auth'
import StatusTag from '../components/StatusTag'
import SeverityTag from '../components/SeverityTag'
import CodeBlock from '../components/CodeBlock'
import type { Alert, AlertComment } from '../types'

// 容器故障（无需看服务日志，看 describe + 诊断结果）
const CONTAINER_FAULTS = ['CrashLoopBackOff', 'OOMKilled', 'ImagePullBackOff', 'ErrImagePull', 'PendingTimeout']

// R44：L2 故障族（与后端 fault_taxonomy.L2_FAMILIES 保持一致）
const FAULT_FAMILIES: Array<[string, string]> = [
  ['resource', '资源不足'], ['config', '配置错误'], ['image', '镜像'], ['schedule', '调度'],
  ['probe', '探针'], ['dependency', '下游依赖'], ['code', '代码缺陷'], ['capacity', '容量'],
  ['lock', '锁与慢查询'], ['network', '网络'], ['node_health', '节点健康'], ['unknown', '未分类'],
]
const FAMILY_LABEL: Record<string, string> = Object.fromEntries(FAULT_FAMILIES)

export default function AlertList() {
  const [severity, setSeverity] = useState<string>()
  const [status, setStatus] = useState<string>()
  const [env, setEnv] = useState<string>()
  const [manualOnly, setManualOnly] = useState<boolean | undefined>()
  const [faultType, setFaultType] = useState<string>()
  const [faultFamily, setFaultFamily] = useState<string>()
  const [page, setPage] = useState(1)
  const PAGE_SIZE = 20
  const writable = canWrite()
  const [selectedRowKeys, setSelectedRowKeys] = useState<number[]>([])
  const [diagnosing, setDiagnosing] = useState<number>()
  const [logOpen, setLogOpen] = useState(false)
  const [logs, setLogs] = useState('')
  const [resolveTarget, setResolveTarget] = useState<Alert | null>(null)
  const [batchOpen, setBatchOpen] = useState(false)
  const [form] = Form.useForm()
  const [batchForm] = Form.useForm()
  // 协作（C1）：备注抽屉
  const [commentTarget, setCommentTarget] = useState<Alert | null>(null)
  const [comments, setComments] = useState<AlertComment[]>([])
  const [commentText, setCommentText] = useState('')
  const [sendingComment, setSendingComment] = useState(false)

  // react-query：告警列表查询（5s 轮询 + 缓存 + 筛选变化自动重查；服务端分页）
  const { data: alertsData, isLoading: loading, refetch } = useQuery({
    queryKey: ['alerts', severity, status, env, manualOnly, faultType, faultFamily, page],
    queryFn: () => getAlertsPaged({ severity, status, env_tag: env, manual_required: manualOnly, fault_type: faultType, fault_family: faultFamily, page, page_size: PAGE_SIZE }),
    refetchInterval: 5000,
  })
  const alerts = alertsData?.items ?? []
  const alertTotal = alertsData?.total ?? 0

  const handleDiagnose = async (id: number) => {
    setDiagnosing(id)
    try {
      await diagnoseAlert(id)
      message.success('诊断已触发')
      refetch()
    } finally {
      setDiagnosing(undefined)
    }
  }

  const handleBatchResolve = () => {
    if (selectedRowKeys.length === 0) {
      message.warning('请先勾选要解决的告警')
      return
    }
    batchForm.resetFields()
    setBatchOpen(true)
  }

  const handleResolve = async () => {
    const v = await form.validateFields()
    await manualResolveAlert(resolveTarget!.id, v)
    message.success('已解决')
    setResolveTarget(null)
    refetch()
  }

  const handleBatchResolveSubmit = async () => {
    const v = await batchForm.validateFields()
    await batchResolveAlerts(selectedRowKeys, v.resolution)
    message.success(`已批量解决 ${selectedRowKeys.length} 条告警`)
    setBatchOpen(false)
    setSelectedRowKeys([])
    refetch()
  }

  const handleViewLogs = async (record: Alert) => {
    setLogOpen(true)
    setLogs('')
    const data = (await searchLogs({ namespace: record.namespace || 'default', pod: record.resource_name, cluster_id: record.cluster_id })) as { logs?: string[]; error?: string }
    if (data?.error) {
      setLogs(`⚠️ 无法获取日志：${data.error}`)
      message.warning(data.error)
      return
    }
    setLogs((data?.logs || []).join('\n') || '（该 Pod 暂无日志输出）')
  }

  const handleViewDescribe = async (record: Alert) => {
    setLogOpen(true)
    setLogs('')
    const data = (await getAlertDescribe(record.id)) as { describe?: string }
    setLogs(data?.describe || '（无容器信息）')
  }

  // ===== 协作（C1）：认领 / 确认 / 备注 =====
  const handleAssign = async (record: Alert) => {
    try {
      await assignAlert(record.id, record.assigned_to ? '' : undefined)
      message.success(record.assigned_to ? '已取消认领' : '已认领')
      refetch()
    } catch (e) {
      message.error((e as { response?: { data?: { message?: string } } })?.response?.data?.message || '操作失败')
    }
  }

  const handleAck = async (record: Alert) => {
    try {
      await ackAlert(record.id, !record.acknowledged)
      message.success(record.acknowledged ? '已取消确认' : '已确认')
      refetch()
    } catch (e) {
      message.error((e as { response?: { data?: { message?: string } } })?.response?.data?.message || '操作失败')
    }
  }

  const openComments = (record: Alert) => {
    setCommentTarget(record)
    setCommentText('')
    getAlertComments(record.id).then((d) => setComments(d || []))
  }

  const submitComment = async () => {
    const text = commentText.trim()
    if (!text) return
    setSendingComment(true)
    try {
      await addAlertComment(commentTarget!.id, text)
      setCommentText('')
      const d = await getAlertComments(commentTarget!.id)
      setComments(d || [])
    } finally {
      setSendingComment(false)
    }
  }

  return (
    <div>
      <Space style={{ marginBottom: 16 }} wrap>
        <Select placeholder="环境" allowClear style={{ width: 120 }} onChange={(v) => { setEnv(v); setPage(1) }}
          options={[{ value: 'qa', label: 'qa' }, { value: 'release', label: 'release' }, { value: 'prod', label: 'prod' }]} />
        <Select placeholder="严重级别" allowClear style={{ width: 140 }} onChange={(v) => { setSeverity(v); setPage(1) }}
          options={['critical', 'high', 'medium', 'low'].map((s) => ({ value: s, label: s }))} />
        <Select placeholder="故障族（L2）" allowClear style={{ width: 150 }} onChange={(v) => { setFaultFamily(v); setPage(1) }}
          options={FAULT_FAMILIES.map(([value, label]) => ({ value, label }))} />
        <Select placeholder="故障类型" allowClear showSearch style={{ width: 180 }} onChange={(v) => { setFaultType(v); setPage(1) }}
          options={[
            { value: 'AppOOM', label: 'AppOOM 内存溢出' },
            { value: 'AppCrash', label: 'AppCrash 进程崩溃' },
            { value: 'AppConnRefused', label: 'AppConnRefused 连接拒绝' },
            { value: 'AppTimeout', label: 'AppTimeout 超时' },
            { value: 'AppSQL', label: 'AppSQL SQL异常' },
            { value: 'AppConfigError', label: 'AppConfigError 配置错误' },
            { value: 'AppPermission', label: 'AppPermission 权限拒绝' },
            { value: 'AppDependency', label: 'AppDependency 下游依赖' },
            { value: 'AppBusiness', label: 'AppBusiness 业务错误' },
            { value: 'LogKeyword', label: 'LogKeyword 兜底' },
          ]} />
        <Select placeholder="状态" allowClear style={{ width: 140 }} onChange={(v) => { setStatus(v); setPage(1) }}
          options={[
            { value: 'pending', label: '待处理' },
            { value: 'resolved', label: '已解决' },
            { value: 'self_healed', label: '已自愈' },
            { value: 'awaiting_approval', label: '待审批' },
            { value: 'ticketed', label: '已转工单' },
          ]} />
        <Select placeholder="处理方式" allowClear style={{ width: 150 }} onChange={(v) => { setManualOnly(v === 'manual' ? true : v === 'auto' ? false : undefined); setPage(1) }}
          options={[
            { value: 'manual', label: '需人工协助' },
            { value: 'auto', label: '平台自动处理' },
          ]} />
        {writable && (
          <Button type="primary" danger onClick={handleBatchResolve} disabled={selectedRowKeys.length === 0}>
            批量解决 ({selectedRowKeys.length})
          </Button>
        )}
      </Space>
      <Table
        rowKey="id"
        loading={loading}
        dataSource={alerts}
        pagination={{ current: page, pageSize: PAGE_SIZE, total: alertTotal, showSizeChanger: false, showTotal: (t) => `共 ${t} 条`, onChange: (p) => setPage(p) }}
        rowSelection={{ selectedRowKeys, onChange: (keys) => setSelectedRowKeys(keys as number[]) }}
        expandable={{
          expandedRowRender: (record) => <DiagnosisPanel alertId={record.id} />,
        }}
        columns={[
          { title: '来源', dataIndex: 'source', render: (v, r: Alert) => v || r.cluster_name || '-' },
          { title: '环境', dataIndex: 'env_tag', width: 90, render: (v) => v ? <Tag color={envColor(v)}>{envLabel(v)}</Tag> : '-' },
          { title: '资源', dataIndex: 'resource_name' },
          { title: '类型', dataIndex: 'resource_type' },
          { title: '命名空间', dataIndex: 'namespace' },
          { title: '故障类型', dataIndex: 'fault_type' },
          { title: '故障族', dataIndex: 'fault_family', width: 110,
            render: (v: string | undefined) => v ? <Tag color={v === 'unknown' ? 'default' : 'blue'}>{FAMILY_LABEL[v] || v}</Tag> : '-' },
          { title: '级别', dataIndex: 'severity', render: (s) => <SeverityTag severity={s} /> },
          { title: '状态', dataIndex: 'status', render: (s, r: Alert) => (
            <Space size={4}>
              <StatusTag
                status={s}
                label={
                  s === 'ticketed' ? '已转工单'
                    : s === 'resolved' && r.approval_status === 'approved' ? '已审核'
                    : s === 'resolved' && r.approval_status === 'rejected' ? '已驳回'
                      : undefined
                }
              />
              {r.manual_required && s !== 'ticketed' && (
                <Tooltip title={r.manual_reason || '平台/AI 无法自动解决，需人工协助'}>
                  <Tag color="red">需人工协助</Tag>
                </Tooltip>
              )}
            </Space>
          ) },
          { title: '协作', dataIndex: 'assigned_to', render: (v, r: Alert) => (
            <Space size={4}>
              {r.acknowledged && <Tooltip title={`${r.acknowledged_by || ''} 已确认`}><Tag color="green">已确认</Tag></Tooltip>}
              {v ? <Tag color="blue">认领：{v}</Tag> : <span style={{ color: '#bbb' }}>未认领</span>}
            </Space>
          ) },
          { title: '事件数', dataIndex: 'event_count', render: (v, r: Alert) => (
            <Space size={4}>
              <span>{v}</span>
              {(r.group_count ?? 1) > 1 && (
                <Tooltip title={`已合并 ${r.group_count} 条同类型同服务告警，仅显示最新`}>
                  <Tag color="orange">同类型 ×{r.group_count}</Tag>
                </Tooltip>
              )}
            </Space>
          ) },
          { title: '报错时间', dataIndex: 'occurred_at', render: (t) => t || '-' },
          { title: '检测时间', dataIndex: 'detected_at' },
          {
            title: '操作', render: (_, record) => (
              <Space>
                {record.resource_type !== 'db' && writable && (
                  <Button size="small" type="primary" loading={diagnosing === record.id}
                    onClick={() => handleDiagnose(record.id)}>重新诊断</Button>
                )}
                {record.resource_type !== 'db' && (
                  <>
                    {CONTAINER_FAULTS.includes(record.fault_type) ? (
                      <Button size="small" onClick={() => handleViewDescribe(record)}>查看容器信息</Button>
                    ) : (
                      <Button size="small" onClick={() => handleViewLogs(record)}>查看日志</Button>
                    )}
                  </>
                )}
                {record.status === 'pending' && writable && (
                  <Button size="small" onClick={() => { form.resetFields(); setResolveTarget(record) }}>解决</Button>
                )}
                {writable && <Button size="small" onClick={() => handleAssign(record)}>{record.assigned_to ? '取消认领' : '认领'}</Button>}
                {writable && <Button size="small" onClick={() => handleAck(record)}>{record.acknowledged ? '取消确认' : '确认'}</Button>}
                {writable && <Button size="small" onClick={() => openComments(record)}>备注</Button>}
              </Space>
            ),
          },
        ]}
      />
      <Drawer title="故障详情" open={logOpen} onClose={() => setLogOpen(false)} width={700}>
        <pre style={{ whiteSpace: 'pre-wrap', background: '#1e1e1e', color: '#d4d4d4', padding: 12, borderRadius: 4, maxHeight: 500, overflow: 'auto' }}>
          {logs}
        </pre>
      </Drawer>
      <Modal title={`解决告警：${resolveTarget?.resource_name || ''}`} open={!!resolveTarget} onOk={handleResolve} onCancel={() => setResolveTarget(null)}>
        <Form form={form} layout="vertical">
          <Form.Item name="resolution" label="解决方法" rules={[{ required: true, message: '请填写解决方法' }]}>
            <Input.TextArea rows={3} placeholder="描述如何解决的（必填）" />
          </Form.Item>
          <Form.Item name="root_cause_type" label="根因类型">
            <Select allowClear placeholder="选择根因类型（可选）"
              options={['环境问题', '服务问题', '代码问题', '配置问题', '其他'].map((t) => ({ value: t, label: t }))} />
          </Form.Item>
          <Form.Item name="root_cause" label="根因描述">
            <Input.TextArea rows={2} placeholder="根因描述（可选）" />
          </Form.Item>
        </Form>
      </Modal>
      <Modal title={`批量解决 ${selectedRowKeys.length} 条告警`} open={batchOpen} onOk={handleBatchResolveSubmit} onCancel={() => setBatchOpen(false)}>
        <Form form={batchForm} layout="vertical">
          <Form.Item name="resolution" label="统一解决方法" rules={[{ required: true, message: '请填写解决方法' }]}>
            <Input.TextArea rows={3} placeholder="填写本次批量解决的统一方法（必填）" />
          </Form.Item>
        </Form>
      </Modal>
      <Drawer title={`协作备注：${commentTarget?.resource_name || ''}`} open={!!commentTarget} onClose={() => setCommentTarget(null)} width={520}>
        <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
          <Input.TextArea value={commentText} onChange={(e) => setCommentText(e.target.value)} rows={2}
            placeholder="记录处理进展 / @协同..." onPressEnter={(e) => { if (!e.shiftKey) { e.preventDefault(); submitComment() } }} />
          <Button type="primary" loading={sendingComment} onClick={submitComment}>发送</Button>
        </div>
        {comments.length === 0 ? <div style={{ color: '#999' }}>暂无备注</div> : comments.map((c) => (
          <div key={c.id} style={{ padding: '6px 0', borderBottom: '1px solid #f0f0f0' }}>
            <Space size={6}><b>{c.username || '系统'}</b><span style={{ color: '#999', fontSize: 12 }}>{String(c.created_at).slice(0, 19)}</span></Space>
            <div style={{ whiteSpace: 'pre-wrap', marginTop: 2 }}>{c.content}</div>
          </div>
        ))}
      </Drawer>
    </div>
  )
}

// 诊断结果面板（行展开）
interface DiagnosisData {
  confidence?: number
  diagnosis_json: string
}

function DiagnosisPanel({ alertId }: { alertId: number }) {
  const [data, setData] = useState<DiagnosisData | null>(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    getDiagnosis(alertId).then((d) => setData(d as DiagnosisData)).catch(() => setData(null)).finally(() => setLoading(false))
  }, [alertId])
  if (loading) return <div>加载中...</div>
  if (!data) return <div>暂无诊断结果（告警写入后系统立即自动诊断/修复，已恢复的会自动解决）</div>
  let rc = ''
  let sug: unknown = ''
  try {
    const j = JSON.parse(data.diagnosis_json || '{}')
    rc = String(j.root_cause || j.conclusion || '')
    sug = j.suggestions || j.handling_advice || ''
  } catch { /* 非 JSON 直接展示原文 */ }
  return (
    <div>
      <div>置信度：{data.confidence}</div>
      {rc
        ? <>
            <div style={{ marginTop: 4 }}><b>根因：</b>{rc}</div>
            {!!sug && <div style={{ marginTop: 4 }}><b>处理意见：</b>{Array.isArray(sug) ? sug.join('；') : String(sug)}</div>}
          </>
        : <div style={{ marginTop: 4, color: '#faad14' }}>AI 未给出结论（模型返回为空/失败，可在告警操作里点「重新诊断」）</div>}
      <CodeBlock code={data.diagnosis_json} />
    </div>
  )
}
