// 故障诊断（针对告警的 AI 诊断，与「应用诊断」「数据库诊断」分开）
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Table, Tag, Button, Space, Modal, message, Input } from 'antd'
import { getFaultDiagnoses, getFaultDiagnosis, diagnoseAlert, getAiFeedback, addAiFeedback } from '../services/api'
import type { AiFeedbackData, FaultDiagnosisItem } from '../types'
import SeverityTag from '../components/SeverityTag'
import StatusTag from '../components/StatusTag'
import Markdown from '../components/Markdown'

export default function FaultDiagnosis({ category = '' }: { category?: string }) {
  // N3 诊断分离：category = cluster / app / ''（空=全部）
  // react-query：诊断台账（15s 轮询）
  const { data: data = [], isLoading: loading, refetch } = useQuery({
    queryKey: ['fault-diagnoses', category],
    queryFn: () => getFaultDiagnoses({ page: 1, page_size: 100, category }),
    refetchInterval: 15000,
  })
  const [detail, setDetail] = useState<FaultDiagnosisItem | null>(null)
  const [currentAlertId, setCurrentAlertId] = useState<number | null>(null)
  const [diagnosing, setDiagnosing] = useState<number | null>(null)

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
      </div>
      <Table rowKey="alert_id" loading={loading} dataSource={data}
        columns={[
          { title: '告警ID', dataIndex: 'alert_id', width: 80 },
          { title: '集群', dataIndex: 'cluster_name', width: 120, render: (v) => v || '-' },
          { title: '故障类型', dataIndex: 'fault_type', width: 170 },
          { title: '资源', dataIndex: 'resource_name', ellipsis: true },
          { title: '诊断', dataIndex: 'has_diagnosis', width: 150, render: (v, r) => v
            ? <Tag color="green">已诊断（置信度 {(r.confidence ?? 0).toFixed(2)}）</Tag>
            : <Tag>未诊断</Tag> },
          { title: '根因摘要', dataIndex: 'root_cause', ellipsis: true, render: (v) => v || '-' },
          { title: '诊断时间', dataIndex: 'diagnosis_time', width: 160, render: (v) => v ? String(v).slice(0, 19) : '-' },
          { title: '操作', width: 150, render: (_, r) => (
            <Space>
              <Button size="small" onClick={() => handleDiagnose(r.alert_id)} loading={diagnosing === r.alert_id}>诊断</Button>
              {r.has_diagnosis && <Button size="small" type="link" onClick={() => { setCurrentAlertId(r.alert_id); viewDiagnosis(r.alert_id) }}>查看</Button>}
            </Space>
          ) },
        ]} />
      <Modal title="诊断结果" open={!!detail} onCancel={() => setDetail(null)} footer={null} width={720}>
        {detail && (() => {
          const j = parse(detail.diagnosis_json || '{}')
          const sug = j.suggestions
          return (
            <div>
              <div><b>根因</b></div>
              <Markdown text={typeof j.root_cause === 'string' ? j.root_cause : JSON.stringify(j.root_cause || '')} />
              {sug && (
                <>
                  <div style={{ marginTop: 8 }}><b>处理意见</b></div>
                  {Array.isArray(sug)
                    ? <ul style={{ paddingLeft: 20 }}>{sug.map((s: unknown, i: number) => <li key={i}>{String(s)}</li>)}</ul>
                    : <Markdown text={String(sug)} />}
                </>
              )}
              {j.handling_advice && <div style={{ background: '#fffbe6', padding: 8, marginTop: 8 }}><Markdown text={String(j.handling_advice)} /></div>}
              <p style={{ color: '#999' }}>
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
    </div>
  )
}
