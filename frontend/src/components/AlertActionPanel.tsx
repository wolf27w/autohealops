// 告警 AI 处置台（2026-11-26）：展示候选动作 + 当前授权策略判定，并可一键触发处置。
// 说明：真正执行仍走平台原有链路（AI 自动处置 → 修复计划 → 审批 → executor），
// 本面板不绕过授权策略。
import { useEffect, useState } from 'react'
import { Alert, Button, Card, Space, Table, Tag, Tooltip, Typography, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { dispatchAlertAction, dispatchSpecificAction, getCandidateActions, getDiagnosis } from '../services/api'

const { Text } = Typography

interface Candidate {
  key: string
  name: string
  description?: string
  risk: string
  verify?: string
  rollback_note?: string
  decision: string
  level?: string
  reasons?: string[]
}

interface CandidateResp {
  fault_type?: string
  env?: string
  mode?: string
  confidence?: number | null
  candidates: Candidate[]
}

interface DiagBrief {
  root_cause?: string
  confidence?: number | string | null
  confidence_basis?: string
  created_at?: string
  stale?: boolean
  suggestions?: unknown
  suggestions_detail?: { immediate?: string[]; root_fix?: string[] }
}

const RISK_COLOR: Record<string, string> = { green: 'green', yellow: 'gold', red: 'red', forbidden: 'magenta' }
const DECISION_META: Record<string, { color: string; label: string }> = {
  auto: { color: 'green', label: '可自动执行' },
  approve: { color: 'orange', label: '需人工审批' },
  manual: { color: 'default', label: '仅人工' },
}
const MODE_LABEL: Record<string, string> = {
  by_env: '按环境（生产审批 / 测试自动）',
  assist: '全部审批',
  full: '全部直接执行',
}

export default function AlertActionPanel({ alertId, onChanged, onReprocess }: {
  alertId: number
  onChanged?: () => void
  onReprocess?: () => void
}) {
  const [data, setData] = useState<CandidateResp | null>(null)
  const [diag, setDiag] = useState<DiagBrief | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)

  const load = () => {
    setLoading(true)
    getCandidateActions(alertId)
      .then((d) => setData(d as unknown as CandidateResp))
      .catch(() => setData(null))
      .finally(() => setLoading(false))
    getDiagnosis(alertId)
      .then((d) => setDiag(d as unknown as DiagBrief))
      .catch(() => setDiag(null))
  }

  useEffect(() => { load() }, [alertId])

  const run = async (action: 'ai' | 'manual') => {
    setBusy(action)
    try {
      await dispatchAlertAction(alertId, action)
      message.success(action === 'ai' ? '已交给 AI 处置' : '已转人工建单')
      onChanged?.()
      load()
    } catch (e) {
      message.error(`操作失败：${(e as Error).message}`)
    } finally {
      setBusy(null)
    }
  }

  const runSpecific = async (c: Candidate) => {
    setBusy(c.key)
    try {
      const r = (await dispatchSpecificAction(alertId, c.key)) as { decision?: string; plan_id?: number; approval_id?: number; message?: string }
      message.success(r?.message || (r?.approval_id ? `已提交审批 #${r.approval_id}，通过后自动执行` : '已开始执行'))
      onChanged?.()
      load()
    } catch (e) {
      message.error(`发起失败：${(e as Error).message}`)
    } finally {
      setBusy(null)
    }
  }

  const columns: ColumnsType<Candidate> = [
    { title: '动作', dataIndex: 'name', width: 170 },
    {
      title: '风险', dataIndex: 'risk', width: 80,
      render: (v: string) => <Tag color={RISK_COLOR[v] || 'default'}>{v || '-'}</Tag>,
    },
    {
      title: '当前判定', dataIndex: 'decision', width: 120,
      render: (v: string, r) => {
        const meta = DECISION_META[v] || { color: 'default', label: v || '-' }
        return (
          <Tooltip title={(r.reasons || []).join('；')}>
            <Tag color={meta.color}>{meta.label}{r.level ? ` ${r.level}` : ''}</Tag>
          </Tooltip>
        )
      },
    },
    {
      title: '验证 / 回滚', dataIndex: 'rollback_note',
      render: (v: string, r) => (
        <Space direction="vertical" size={0}>
          {r.verify ? <Text type="secondary" style={{ fontSize: 12 }}>验证：{r.verify}</Text> : null}
          {v ? <Text type="secondary" style={{ fontSize: 12 }}>回滚：{v}</Text> : null}
        </Space>
      ),
    },
    {
      title: '操作', width: 110,
      render: (_, r) => {
        if (r.decision === 'auto') {
          return <Button size="small" type="primary" loading={busy === r.key} onClick={() => runSpecific(r)}>立即执行</Button>
        }
        if (r.decision === 'approve') {
          return <Button size="small" loading={busy === r.key} onClick={() => runSpecific(r)}>提交审批</Button>
        }
        return <Button size="small" disabled>{r.decision === 'forbidden' ? '禁止' : '仅人工'}</Button>
      },
    },
  ]

  return (
    <Card
      size="small" loading={loading}
      title={<Space>AI 处置<Tag color="blue">{MODE_LABEL[data?.mode || 'by_env'] || data?.mode}</Tag></Space>}
      style={{ marginTop: 12 }}
      extra={
        <Space>
          <Button size="small" loading={busy === 'ai'} onClick={() => run('ai')}>重新分析并处置</Button>
          <Button size="small" loading={busy === 'manual'} onClick={() => run('manual')}>转人工</Button>
        </Space>
      }
    >
      <Alert
        type={diag?.root_cause ? 'success' : 'warning'} showIcon style={{ marginBottom: 8, padding: '4px 10px' }}
        message={
          <span>
            <b>诊断原因：</b>
            {diag?.root_cause ? diag.root_cause : '暂无诊断结论（可重新分析或直接交给 AI）'}
            {typeof diag?.confidence === 'number' ? `（把握度 ${Math.round(diag.confidence * 100)}%）` : ''}
            {diag?.stale ? '（上一轮诊断，本轮尚未重新分析）' : ''}
          </span>
        }
        description={
          diag?.suggestions_detail?.root_fix?.length
            ? <div style={{ fontSize: 12 }}>根因修复建议：{diag.suggestions_detail.root_fix.slice(0, 3).join('；')}</div>
            : undefined
        }
      />
      <Alert
        type="info" showIcon style={{ marginBottom: 8, padding: '4px 10px' }}
        message="真正执行受「自动化策略」约束：判定为需审批的动作会生成审批单，等人工放行后才执行。"
      />
      <Table
        rowKey="key" size="small" pagination={false}
        columns={columns} dataSource={data?.candidates || []}
      />
    </Card>
  )
}
