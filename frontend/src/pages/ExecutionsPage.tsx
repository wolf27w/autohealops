// 执行明细页（H9 / D5）：只展示执行步骤/结果/耗时/失败原因，审批动作归「告警处理 → 审批列表」
import { useEffect, useState } from 'react'
import { Table, Tag, Alert, Popconfirm, Button, message } from 'antd'
import { getRepairRecords, rollbackExecution } from '../services/api'
import SeverityTag from '../components/SeverityTag'
import { envLabel, envColor } from '../utils/env'

// 处理方式（handle_type）颜色：自愈/已审核/已驳回/审核中/审核/待处理/已解决
const handleTypeColor: Record<string, string> = {
  自愈: 'green', 已审核: 'blue', 已驳回: 'red', 审核中: 'orange', 审核: 'blue', 待处理: 'orange', 已解决: 'default',
}

// 修复计划状态颜色
const planStatusColor: Record<string, string> = {
  pending: 'default', running: 'blue', completed: 'green', failed: 'red', rolled_back: 'orange',
}

// 执行步骤状态颜色
const stepStatusColor: Record<string, string> = {
  success: 'green', failed: 'red', running: 'blue', rolled_back: 'orange',
}

interface PlanStep {
  command?: string
  description?: string
  expected_result?: string
  rollback_command?: string
}
interface ExecutionStep {
  step_index?: number
  command?: string
  status?: string
  output?: string
  started_at?: string
}
interface RepairRecord {
  alert_id: number
  cluster_id?: number
  cluster_name?: string
  env?: string
  source?: string
  fault_type?: string
  resource_type?: string
  resource_name?: string
  namespace?: string
  severity?: string
  status?: string
  handle_type?: string
  plan_id?: number
  plan_status?: string
  plan_step_count?: number
  plan_steps?: PlanStep[]
  execution_steps?: ExecutionStep[]
  rollbackable?: boolean
  resolved_by?: string
  detected_at?: string
  resolved_at?: string
  message?: string
  [key: string]: unknown
}

// 耗时：检测 → 解决（整体处理耗时，精确到秒/分/时）
const fmtDuration = (start?: string, end?: string): string => {
  if (!start || !end) return '-'
  const s = new Date(start).getTime()
  const e = new Date(end).getTime()
  if (Number.isNaN(s) || Number.isNaN(e) || e <= s) return '-'
  const sec = Math.floor((e - s) / 1000)
  if (sec < 60) return `${sec}s`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}m ${sec % 60}s`
  const h = Math.floor(min / 60)
  return `${h}h ${min % 60}m`
}

export default function ExecutionsPage() {
  const [records, setRecords] = useState<RepairRecord[]>([])
  const [loading, setLoading] = useState(false)
  const [rollingId, setRollingId] = useState<number | null>(null)

  const load = () => {
    setLoading(true)
    getRepairRecords().then((d) => setRecords(d as RepairRecord[])).finally(() => setLoading(false))
  }
  useEffect(load, [])

  const handleRollback = async (planId: number) => {
    setRollingId(planId)
    try {
      const res = (await rollbackExecution(planId)) as { success?: boolean }
      if (res?.success) message.success('已执行回滚')
      else message.warning('回滚未完全成功，请到执行详情查看日志')
      load()
    } catch (e) {
      message.error((e as { response?: { data?: { message?: string } } })?.response?.data?.message || '回滚失败')
    } finally {
      setRollingId(null)
    }
  }

  const renderResource = (r: RepairRecord) => {
    const name = r.resource_name || '-'
    const ns = r.namespace ? ` · ${r.namespace}` : ''
    const cluster = r.cluster_name ? `${r.cluster_name} / ` : ''
    return (
      <span>
        <span style={{ color: '#595959', fontSize: 12 }}>{cluster}</span>
        {name}
        <span style={{ color: '#999', fontSize: 12 }}>{ns}</span>
      </span>
    )
  }

  return (
    <div>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="本页仅展示「执行明细」（执行步骤 / 结果 / 耗时 / 失败原因）。审批动作（待审批、审批历史）请前往「告警处理 → 审批列表」。"
      />

      <Table
        rowKey="alert_id"
        loading={loading}
        dataSource={records}
        scroll={{ x: 'max-content' }}
        expandable={{
          rowExpandable: (r) => !!((r.execution_steps?.length) || (r.plan_steps?.length)),
          expandedRowRender: (r) => (
            <div style={{ padding: '8px 16px' }}>
              {r.plan_steps?.length ? (
                <div style={{ marginBottom: 12 }}>
                  <div style={{ fontWeight: 600, marginBottom: 6 }}>
                    修复计划（共 {r.plan_step_count} 步）
                    {r.plan_status ? <Tag style={{ marginLeft: 8 }} color={planStatusColor[r.plan_status] || 'default'}>{r.plan_status}</Tag> : null}
                  </div>
                  {r.plan_steps.map((s: PlanStep, i: number) => (
                    <div key={i} style={{ padding: '4px 0', fontSize: 13 }}>
                      <b>第 {i + 1} 步：</b><span style={{ fontFamily: 'monospace' }}>{s.command || '-'}</span>
                      {s.description ? <span style={{ color: '#666', marginLeft: 8 }}>{s.description}</span> : null}
                    </div>
                  ))}
                </div>
              ) : null}
              {r.execution_steps?.length ? (
                <div>
                  <div style={{ fontWeight: 600, marginBottom: 6 }}>执行步骤（{r.execution_steps.length}）</div>
                  {r.execution_steps.map((s: ExecutionStep, i: number) => (
                    <div key={i} style={{ padding: '4px 0', fontSize: 13 }}>
                      <Tag color={stepStatusColor[s.status || ''] || 'default'}>{s.status}</Tag>
                      <span style={{ fontFamily: 'monospace' }}>{s.command}</span>
                      {s.started_at ? <span style={{ color: '#999', fontSize: 12, marginLeft: 8 }}>{s.started_at}</span> : null}
                      {s.output ? <div style={{ color: '#888', whiteSpace: 'pre-wrap', fontSize: 12, marginTop: 2 }}>{s.output}</div> : null}
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          ),
        }}
        columns={[
          { title: '时间', dataIndex: 'detected_at', width: 160 },
          { title: '目标资源', key: 'resource', render: (_: unknown, r: RepairRecord) => renderResource(r) },
          { title: '环境', dataIndex: 'env', width: 90, render: (v: string) => v ? <Tag color={envColor(v)}>{envLabel(v)}</Tag> : '-' },
          { title: '故障类型', dataIndex: 'fault_type', ellipsis: true },
          { title: '级别', dataIndex: 'severity', width: 90, render: (s: string) => <SeverityTag severity={s} /> },
          { title: '执行动作', dataIndex: 'handle_type', width: 100, render: (t: string) => <Tag color={handleTypeColor[t]}>{t}</Tag> },
          { title: '执行状态', dataIndex: 'plan_status', width: 100, render: (s: string) => s ? <Tag color={planStatusColor[s] || 'default'}>{s}</Tag> : '-' },
          { title: '步骤', key: 'steps', width: 70, render: (_: unknown, r: RepairRecord) => (r.execution_steps?.length || r.plan_step_count || 0) },
          { title: '耗时', key: 'duration', width: 100, render: (_: unknown, r: RepairRecord) => fmtDuration(r.detected_at, r.resolved_at) },
          { title: '执行人', dataIndex: 'resolved_by', width: 100, render: (v: string) => v || '-' },
          {
            title: '操作', width: 110, fixed: 'right' as const,
            render: (_: unknown, r: RepairRecord) => r.rollbackable ? (
              <Popconfirm
                title="确认一键回滚？"
                description="将按步骤逆序执行回滚命令，属变更操作。"
                okText="确认回滚" cancelText="取消"
                onConfirm={() => handleRollback(r.plan_id!)}
              >
                <Button danger size="small" loading={rollingId === r.plan_id}>一键回滚</Button>
              </Popconfirm>
            ) : <span style={{ color: '#bbb' }}>-</span>,
          },
        ]}
      />
    </div>
  )
}
