// 处理记录页：任务+审批合并，展示告警处理闭环（处理方式 + 审核人）+ 一键回滚
import { useEffect, useState } from 'react'
import { Table, Tag, Select, Space, Button, Popconfirm, message } from 'antd'
import { getRepairRecords, rollbackExecution } from '../services/api'
import SeverityTag from '../components/SeverityTag'

const handleTypeColor: Record<string, string> = {
  自愈: 'green', 审核: 'blue', 待处理: 'orange', 已解决: 'default',
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
  handle_type?: string
  rollbackable?: boolean
  plan_id?: number
  plan_status?: string
  plan_step_count?: number
  plan_steps?: PlanStep[]
  execution_steps?: ExecutionStep[]
  [key: string]: unknown
}

const stepStatusColor: Record<string, string> = {
  success: 'green', failed: 'red', running: 'blue', rolled_back: 'orange',
}

export default function ProcessingRecords() {
  const [records, setRecords] = useState<RepairRecord[]>([])
  const [handleType, setHandleType] = useState<string>()
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

  const filtered = handleType ? records.filter((r) => r.handle_type === handleType) : records

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Select placeholder="处理方式" allowClear style={{ width: 140 }} onChange={setHandleType}
          options={['自愈', '审核', '待处理', '已解决'].map((t) => ({ value: t, label: t }))} />
      </Space>
      <Table
        rowKey="alert_id"
        loading={loading}
        dataSource={filtered}
        expandable={{
          rowExpandable: (r) => !!((r.plan_steps?.length) || (r.execution_steps?.length)),
          expandedRowRender: (r) => (
            <div style={{ padding: '8px 16px' }}>
              {r.plan_steps?.length ? (
                <div style={{ marginBottom: 12 }}>
                  <div style={{ fontWeight: 600, marginBottom: 6 }}>
                    修复计划（共 {r.plan_step_count} 步）
                    {r.plan_status ? <Tag style={{ marginLeft: 8 }}>{r.plan_status}</Tag> : null}
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
                      {s.output ? <div style={{ color: '#888', whiteSpace: 'pre-wrap', fontSize: 12, marginTop: 2 }}>{s.output}</div> : null}
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          ),
        }}
        columns={[
          { title: '告警ID', dataIndex: 'alert_id', width: 80 },
          { title: '集群', dataIndex: 'cluster_name', render: (v) => v || '-' },
          { title: '故障类型', dataIndex: 'fault_type' },
          { title: '资源', dataIndex: 'resource_name', ellipsis: true },
          { title: '命名空间', dataIndex: 'namespace', width: 140 },
          { title: '级别', dataIndex: 'severity', render: (s) => <SeverityTag severity={s} /> },
          { title: '处理方式', dataIndex: 'handle_type', render: (t) => <Tag color={handleTypeColor[t]}>{t}</Tag> },
          { title: '审核人', dataIndex: 'approver', render: (v) => v || '-' },
          { title: '处理人', dataIndex: 'resolved_by', render: (v) => v || '-' },
          { title: '状态', dataIndex: 'status', render: (s) => <Tag>{s}</Tag> },
          { title: '检测时间', dataIndex: 'detected_at', width: 170 },
          { title: '详情', dataIndex: 'message', ellipsis: true },
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
