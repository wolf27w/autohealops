// 故障诊断（针对告警的 AI 诊断，与「应用诊断」「数据库诊断」分开）
import { envLabel, envColor } from '../utils/env'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Table, Tag, Button, Space, Modal, message, Input } from 'antd'
import { getFaultDiagnosesPaged, getFaultDiagnosis, diagnoseAlert, getAiFeedback, addAiFeedback,
  getDiagnosisFailures, getDiagnosisQuality, createAgentSessionFromAlert } from '../services/api'
import { canWrite } from '../utils/auth'
import type { AiFeedbackData, FaultDiagnosisItem, DiagnosisFailureItem, DiagnosisQuality } from '../types'
import SeverityTag from '../components/SeverityTag'
import StatusTag from '../components/StatusTag'
import Markdown from '../components/Markdown'
import DiagnosisSections from '../components/DiagnosisSections'

export default function FaultDiagnosis({ category = '' }: { category?: string }) {
  // N3 诊断分离：category = cluster / app / ''（空=全部）
  // react-query：诊断台账（15s 轮询；服务端分页，with_total=true 精确 total）
  const PAGE_SIZE = 50
  const [page, setPage] = useState(1)
  const writable = canWrite()
  const { data: paged, isLoading: loading, refetch } = useQuery({
    queryKey: ['fault-diagnoses', category, page],
    queryFn: () => getFaultDiagnosesPaged({ page, page_size: PAGE_SIZE, category }),
    refetchInterval: 15000,
  })
  const data = paged?.items ?? []
  const total = paged?.total ?? 0
  // B1 诊断失败记录（2026-10-29）：单独台账 + 统计
  const [failOpen, setFailOpen] = useState(false)
  const [failPage, setFailPage] = useState(1)
  const { data: quality } = useQuery({
    queryKey: ['diagnosis-quality'],
    queryFn: () => getDiagnosisQuality(7) as unknown as Promise<DiagnosisQuality>,
    refetchInterval: 60000,
  })
  const { data: failures, isLoading: failLoading } = useQuery({
    queryKey: ['diagnosis-failures', failPage],
    queryFn: () => getDiagnosisFailures({ page: failPage, page_size: 20 }) as unknown as Promise<{ items: DiagnosisFailureItem[]; total: number }>,
    enabled: failOpen,
    refetchInterval: failOpen ? 30000 : false,
  })
  const [detail, setDetail] = useState<FaultDiagnosisItem | null>(null)
  const [currentAlertId, setCurrentAlertId] = useState<number | null>(null)
  const [diagnosing, setDiagnosing] = useState<number | null>(null)
  const [toAgent, setToAgent] = useState<number | null>(null)
  const navigate = useNavigate()

  // 闭环（老板 2026-11-11）：把这条告警交给「应用诊断助手」接着排查（建立关联会话后直接跳过去）
  const handleToAgent = async (alertId: number) => {
    setToAgent(alertId)
    try {
      const d = await createAgentSessionFromAlert({ alert_id: alertId, deep: true })
      navigate(`/diagnose/agent?session=${d.id}`)
    } catch { /* 拦截器已提示 */ } finally { setToAgent(null) }
  }

  const handleDiagnose = async (id: number) => {
    setDiagnosing(id)
    try {
      await diagnoseAlert(id)
      message.success('诊断已触发')
      setTimeout(refetch, 1500)
    } catch { /* 拦截器已提示 */ } finally { setDiagnosing(null) }
  }

  const viewDiagnosis = async (id: number) => {
    try {
      const d = await getFaultDiagnosis(id)
      setDetail(d)
      setFeedback({})
      getAiFeedback(id).then((f) => setFeedback(f || {})).catch(() => {})
    } catch { message.info('暂无诊断结果') }
  }

  // AI 反馈闭环（C5）
  const [feedback, setFeedback] = useState<AiFeedbackData>({})
  const [fbComment, setFbComment] = useState('')
  const submitFeedback = async (rating: number) => {
    if (!detail) return
    try {
      await addAiFeedback(currentAlertId as number, rating, fbComment)
      message.success('感谢反馈')
      const f = await getAiFeedback(currentAlertId as number)
      setFeedback(f || {})
      setFbComment('')
    } catch { /* 拦截器已提示 */ }
  }

  const parse = (s: string) => { try { return JSON.parse(s) } catch { return {} } }

  return (
    <div>
      <div style={{ marginBottom: 12, color: '#666' }}>
        {(category === 'cluster' ? '集群故障诊断' : category === 'app' ? '应用故障诊断' : '故障诊断')}
        ：针对<strong>告警</strong>的 AI 诊断结果台账
        {category === 'cluster' && '（只含节点 / 集群层故障）'}
        {category === 'app' && '（只含 Pod / 工作负载 / 容器 / 日志故障）'}
        （处理闭环记录见「告警处理 → 处理记录」）。
        <div style={{ marginTop: 4, color: '#999', fontSize: 12 }}>
          已按「集群 + 命名空间 + 资源 + 故障类型」<strong>归并</strong>：同一问题只列最新一条，
          「次数」= 该问题告警次数；重复诊断已自动复用上次结论（窗口可在系统设置 diagnosis_reuse_minutes 调整）。
        </div>
        <div style={{ marginTop: 6 }}>
          <Space>
            <Button size="small" onClick={() => { setFailPage(1); setFailOpen(true) }}>诊断失败记录</Button>
            {quality && (
              <span style={{ color: '#888', fontSize: 12 }}>
                近 {quality.days} 天诊断 {quality.diagnoses} 条，已评价 {quality.judged} 条：
                有用 {quality.useful} / 无用 {quality.useless}
                {quality.useful_rate !== null && `（有用率 ${quality.useful_rate}%）`}
              </span>
            )}
          </Space>
        </div>
      </div>
      <Table rowKey="alert_id" loading={loading} dataSource={data}
        pagination={{
          current: page, pageSize: PAGE_SIZE, total,
          showSizeChanger: false,
          showTotal: (t) => `共 ${t} 条` ,
          onChange: (p) => setPage(p),
        }}
        columns={[
          { title: '最新告警', dataIndex: 'alert_id', width: 90 },
          { title: '环境', dataIndex: 'env', width: 100, render: (v: string) => v ? <Tag color={envColor(v)}>{envLabel(v)}</Tag> : '-' },
          { title: '故障类型', dataIndex: 'fault_type', width: 170 },
          { title: '次数', dataIndex: 'occurrences', width: 80, sorter: (a, b) => (a.occurrences || 0) - (b.occurrences || 0),
            render: (v?: number) => (v && v > 1) ? <Tag color="orange">×{v}</Tag> : (v || 1) },
          { title: '资源', dataIndex: 'resource_name', ellipsis: true },
          { title: '诊断', dataIndex: 'has_diagnosis', width: 150, render: (v, r) => v
            ? <Tag color="green">已诊断（置信度 {(r.confidence ?? 0).toFixed(2)}）</Tag>
            : <Tag>未诊断</Tag> },
          { title: '根因摘要', dataIndex: 'root_cause', ellipsis: true, render: (v) => v || '-' },
          { title: '诊断时间', dataIndex: 'diagnosis_time', width: 160, render: (v) => v ? String(v).slice(0, 19) : '-' },
          { title: '操作', width: 220, render: (_, r) => (
            <Space>
              {writable && <Button size="small" onClick={() => handleDiagnose(r.alert_id)} loading={diagnosing === r.alert_id}>诊断</Button>}
              {r.has_diagnosis && <Button size="small" type="link" onClick={() => { setCurrentAlertId(r.alert_id); viewDiagnosis(r.alert_id) }}>查看</Button>}
              <Button size="small" type="link" onClick={() => handleToAgent(r.alert_id)} loading={toAgent === r.alert_id}>交给助手</Button>
            </Space>
          ) },
        ]} />
      <Modal title="诊断结果" open={!!detail} onCancel={() => setDetail(null)} footer={null} width={720}>
        {detail && (() => {
          const j = parse(detail.diagnosis_json || '{}')
          return (
            <div>
              <DiagnosisSections j={j} />
              {j.handling_advice && <div style={{ background: '#fffbe6', padding: 8, marginTop: 8 }}><Markdown text={String(j.handling_advice)} /></div>}
              <p style={{ color: '#999', marginTop: 8 }}>
                {j.risk_level ? `风险：${j.risk_level} ｜ ` : ''}{j.root_cause_layer ? `层面：${j.root_cause_layer} ｜ ` : ''}
                置信度：{detail.confidence} ｜ 诊断时间：{detail.created_at}
              </p>
              <div style={{ borderTop: '1px solid #f0f0f0', paddingTop: 8 }}>
                <Space>
                  <b>这个诊断有用吗？</b>
                  <Button size="small" type="primary" ghost onClick={() => submitFeedback(1)}>有用 👍</Button>
                  <Button size="small" danger ghost onClick={() => submitFeedback(-1)}>无用 👎</Button>
                  <span style={{ color: '#999', fontSize: 12 }}>已有：有用 {feedback.useful || 0} / 无用 {feedback.useless || 0}</span>
                </Space>
                <Input style={{ marginTop: 6 }} placeholder="补充说明（可选）" value={fbComment} onChange={(e) => setFbComment(e.target.value)} />
                {(feedback.items || []).length > 0 && (
                  <div style={{ marginTop: 6, maxHeight: 120, overflow: 'auto', fontSize: 12, color: '#666' }}>
                    {(feedback.items || []).map((f) => (
                      <div key={f.id}>{f.rating > 0 ? '👍' : '👎'} {f.username} · {String(f.created_at).slice(0, 19)} {f.comment ? `— ${f.comment}` : ''}</div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )
        })()}
      </Modal>
      <Modal title="诊断失败记录（近 7 天）" open={failOpen} onCancel={() => setFailOpen(false)} footer={null} width={900}>
        <div style={{ color: '#888', fontSize: 12, marginBottom: 8 }}>
          AI 诊断失败 / 方案生成失败都会单独留痕（stage=diagnose|plan，reason=ai_error|timeout|empty_result|plan_error）。
        </div>
        <Table rowKey="id" size="small" loading={failLoading}
          dataSource={failures?.items ?? []}
          pagination={{ current: failPage, pageSize: 20, total: failures?.total ?? 0, onChange: setFailPage, showSizeChanger: false }}
          columns={[
            { title: '时间', dataIndex: 'created_at', width: 160, render: (v) => v ? String(v).slice(0, 19) : '-' },
            { title: '告警', dataIndex: 'alert_id', width: 80 },
            { title: '故障类型', dataIndex: 'fault_type', width: 160 },
            { title: '资源', dataIndex: 'resource_name', ellipsis: true },
            { title: '阶段', dataIndex: 'stage', width: 90, render: (v) => <Tag>{String(v || '-')}</Tag> },
            { title: '原因', dataIndex: 'reason', width: 120, render: (v) => <Tag color="red">{String(v || '-')}</Tag> },
            { title: '尝试', dataIndex: 'attempts', width: 70 },
            { title: '错误', dataIndex: 'error', ellipsis: true, render: (v) => String(v || '-').slice(0, 120) },
          ]} />
      </Modal>
    </div>
  )
}
