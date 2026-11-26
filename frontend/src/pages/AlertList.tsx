// 告警列表页：筛选 + 表格 + 批量解决 + 行展开诊断 + 日志查看（诊断已自动，保留手动重试）
import { envLabel, envColor } from '../utils/env'
import { useEffect, useState } from 'react'
import type { Key, ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Table, Select, Button, Space, Drawer, message, Modal, Form, Input, Tag, Tooltip, Badge, Collapse, Alert as AntAlert } from 'antd'
import { getAlertsPaged, diagnoseAlert, getDiagnosis, getAlertDescribe, batchResolveAlerts, manualResolveAlert, searchLogs, assignAlert, ackAlert, getAlertComments, addAlertComment } from '../services/api'
import { canWrite } from '../utils/auth'
import StatusTag from '../components/StatusTag'
import SeverityTag from '../components/SeverityTag'
import CodeBlock from '../components/CodeBlock'
import AlertActionPanel from '../components/AlertActionPanel'
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
  const [status, setStatus] = useState<string>('active')
  const [env, setEnv] = useState<string>()
  const [manualOnly, setManualOnly] = useState<boolean | undefined>()
  const [faultType, setFaultType] = useState<string>()
  const [faultFamily, setFaultFamily] = useState<string>()
  const [page, setPage] = useState(1)
  // 行展开（受控）：操作列「AI 处置」按钮直接展开该行，不必找左侧小箭头
  const [expandedKeys, setExpandedKeys] = useState<Key[]>([])
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
        <Select placeholder="状态" style={{ width: 150 }} value={status}
          onChange={(v) => { setStatus(v ?? 'active'); setPage(1) }}
          options={[
            { value: 'active', label: '未修复' },
            { value: 'all', label: '全部状态' },
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
        pagination={{ current: page, pageSize: PAGE_SIZE, total: alertTotal, showSizeChanger: false, showTotal: (t) => `共 ${t} 条（同故障已合并）`, onChange: (p) => setPage(p) }}
        rowSelection={{ selectedRowKeys, onChange: (keys) => setSelectedRowKeys(keys as number[]) }}
        expandable={{
          expandedRowKeys: expandedKeys,
          onExpandedRowsChange: (keys) => setExpandedKeys([...keys]),
          expandedRowRender: (record) => (
            <div>
              <AlertActionPanel alertId={record.id} onChanged={refetch} />
              <Collapse
                size="small" style={{ marginTop: 8 }}
                items={[{ key: 'diag', label: '诊断详情（证据 / 处理方法 / 原始结果）', children: <DiagnosisPanel alert={record} /> }]}
              />
            </div>
          ),
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
                  <Button size="small" loading={diagnosing === record.id}
                    onClick={() => setExpandedKeys((prev) => prev.includes(record.id) ? prev.filter((k) => k !== record.id) : [...prev, record.id])}>AI 处置</Button>
                )}
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

// 诊断结果面板（行展开）—— R68（2026-11-23）：展示七字段（现象/首次出现时间/原因/证据/把握度/已做处置/影响面），缺字段占位不空白
interface SuggestionsDetail {
  immediate?: string[]
  root_fix?: string[]
  verification?: string[]
  prevention?: string[]
}

interface DiagnosisData {
  confidence?: number | string | null
  diagnosis_json?: string
  created_at?: string
  // 2026-11-26：stale=true 表示这条诊断属于上一轮故障（告警复发合并复用同一行，本轮尚未重新诊断）
  stale?: boolean
  episode_started_at?: string
  root_cause?: string
  evidence_refs?: string[]
  impact?: unknown
  root_cause_layer?: string
  suggestions?: unknown
  // 2026-11-26（老板）：诊断还要给「解决方法」——结构化四段 + 处置说明
  suggestions_detail?: SuggestionsDetail
  handling_advice?: string
  risk_level?: string
  eliminated?: string[]
  root_cause_chain?: Record<string, string>
  confidence_basis?: string
  degraded?: boolean
}

// 告警状态 → 「已做处置」文案（与后端 _handling_status_label 对齐）
const HANDLING_LABELS: Record<string, string> = {
  self_healed: '已自愈（AI 自动修复成功）',
  ticketed: '已转工单（待人工处理）',
  awaiting_approval: '已转审批（待人工放行）',
  resolved: '已解决',
  pending: '待人工（分析/自动处置中）',
}

// 2026-11-26（老板问「把握度 0% 是什么意思」）：0% 原来混了两个含义——「未评估」和「0 分」。
// 现在按 confidence_basis 区分：unknown=未评估（降级/证据不足）、evidence=证据直接判定、model=模型自评。
function renderConfidence(c?: number | string | null, basis?: string, degraded?: boolean): string {
  const n = (c === null || c === undefined || c === '') ? null : Number(c)
  if (basis === 'unknown' || (degraded && (!n || n <= 0))) {
    return '未评估（降级结论，按已采集证据直接给出，不是模型评分）'
  }
  if (n === null || Number.isNaN(n)) return '未知'
  if (n <= 0) return '0%（模型判定无法确定）'
  if (n >= 0 && n <= 1) {
    const pct = `${Math.round(n * 100)}%`
    return basis === 'evidence' ? `${pct}（证据直接判定）` : pct
  }
  return String(c)
}

function renderImpact(d: DiagnosisData): string {
  const imp = d.impact as any
  if (imp && typeof imp === 'object') {
    const objs = Array.isArray(imp.affected_objects) ? imp.affected_objects.map(String) : []
    const scopeMap: Record<string, string> = { single_instance: '单实例', whole_service: '整个服务', cross_service: '跨服务', unknown: '未知影响面' }
    const percMap: Record<string, string> = { none: '无感知', slight: '轻微', partial: '部分不可用', full: '全不可用', unknown: '感知程度未知' }
    const parts: string[] = []
    if (objs.length) parts.push(`影响对象：${objs.join('、')}`)
    parts.push(`影响面：${scopeMap[imp.scope] || '未知影响面'}`)
    parts.push(`用户感知：${percMap[imp.perceptibility] || '感知程度未知'}`)
    return parts.join('；')
  }
  if (typeof imp === 'string' && imp.trim()) return imp
  return d.root_cause_layer || '未知'
}

function renderRootCause(d: DiagnosisData, alert: Alert): string {
  if (d.root_cause) return d.root_cause
  if (alert.status === 'pending' && !alert.manual_required) return '分析中'
  return '未能确定根因（缺失证据）'
}

// 2026-11-26（老板）：「解决方法」分四段展示；命令/配置片段用等宽字体，方便直接复制
const _CMD_RE = /^(kubectl|mvn|unzip|curl|docker|crictl|helm|git|systemctl|grep|find|java|jstack)\b/i
function SolutionItem({ text }: { text: string }) {
  const isCmd = _CMD_RE.test(text.trim()) || /<exclusion>|BOOT-INF|dependencyManagement|pom\.xml/i.test(text)
  return (
    <li style={{ margin: '2px 0', lineHeight: 1.6 }}>
      {isCmd ? (
        <code style={{ background: '#f6f6f6', padding: '1px 4px', borderRadius: 3, fontSize: 12, whiteSpace: 'pre-wrap' }}>{text}</code>
      ) : text}
    </li>
  )
}

function SolutionSection({ title, color, items }: { title: string; color: string; items?: string[] }) {
  if (!items || !items.length) return null
  return (
    <div style={{ marginTop: 6 }}>
      <b style={{ color }}>{title}</b>
      <ul style={{ margin: '2px 0 0 18px', padding: 0 }}>
        {items.map((t, i) => <SolutionItem key={i} text={String(t)} />)}
      </ul>
    </div>
  )
}

function DiagnosisPanel({ alert }: { alert: Alert }) {
  const [data, setData] = useState<DiagnosisData | null>(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    getDiagnosis(alert.id).then((d) => setData(d as DiagnosisData)).catch(() => setData(null)).finally(() => setLoading(false))
  }, [alert.id])
  if (loading) return <div>加载中...</div>
  if (!data) return <div>暂无诊断结果（告警写入后系统立即自动诊断/修复，已恢复的会自动解决）</div>

  const phenomenon = (alert.message || '').trim() || `${alert.fault_type || ''}（${alert.resource_name || ''}）`
  const firstOccurrence = alert.occurred_at || alert.detected_at || '-'
  const handling = HANDLING_LABELS[alert.status] || alert.status || '待人工'
  const rootCause = renderRootCause(data, alert)
  const evidence = data.evidence_refs?.length ? data.evidence_refs.join('；') : '无'
  const confidence = renderConfidence(data.confidence, data.confidence_basis, data.degraded)
  const impact = renderImpact(data)
  const sug = data.suggestions || ''

  const field = (label: string, value: ReactNode, warn = false) => (
    <div style={{ marginTop: 6 }}>
      <b>{label}：</b>
      <span style={warn ? { color: '#faad14' } : undefined}>{value || '—'}</span>
    </div>
  )

  return (
    <div style={{ padding: '4px 0' }}>
      {data.stale && (
        <AntAlert
          type="warning"
          showIcon
          style={{ marginBottom: 8 }}
          message="这条诊断属于上一轮故障，本轮尚未重新诊断"
          description={
            `诊断时间 ${String(data.created_at || '').slice(0, 19) || '未知'}` +
            `；本轮故障始于 ${String(data.episode_started_at || alert.detected_at || '').slice(0, 19) || '未知'}` +
            '。以「原因」为准前请先确认是否已重新诊断，否则可能是上一次故障的结论。'
          }
        />
      )}
      {field('现象', phenomenon)}
      {field('首次出现时间', firstOccurrence)}
      {field('原因', rootCause, !data.root_cause)}
      {field('证据', evidence)}
      {field('把握度', confidence)}
      {field('已做处置', handling)}
      {field('影响面', impact)}
      {(() => {
        // 2026-11-26（老板）：不止诊断结果，必须给「解决方法」
        const sd = data.suggestions_detail || {}
        const hasDetail = (sd.immediate?.length || sd.root_fix?.length || sd.verification?.length || sd.prevention?.length) as unknown as boolean
        const flat = Array.isArray(sug) ? sug : (sug ? [String(sug)] : [])
        if (!hasDetail && !flat.length) return null
        return (
          <div style={{ marginTop: 10, background: '#fafafa', border: '1px solid #f0f0f0', borderRadius: 4, padding: '8px 10px' }}>
            <b style={{ fontSize: 13 }}>解决方法：</b>
            {hasDetail ? (
              <>
                <SolutionSection title="① 立即止血" color="#cf1322" items={sd.immediate} />
                <SolutionSection title="② 根因修复" color="#1677ff" items={sd.root_fix} />
                <SolutionSection title="③ 验证方法" color="#389e0d" items={sd.verification} />
                <SolutionSection title="④ 防复发" color="#8c8c8c" items={sd.prevention} />
              </>
            ) : (
              <div style={{ marginTop: 4 }}>{flat.join('；')}</div>
            )}
            {data.handling_advice ? (
              <div style={{ marginTop: 8, color: '#595959', fontSize: 12, lineHeight: 1.7, whiteSpace: 'pre-wrap' }}>
                <b>处置说明：</b>{data.handling_advice}
              </div>
            ) : null}
          </div>
        )
      })()}
      {data.diagnosis_json ? <CodeBlock code={data.diagnosis_json} /> : null}
    </div>
  )
}
