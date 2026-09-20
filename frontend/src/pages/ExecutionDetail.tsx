// 执行详情页：步骤进度轮询（无 WebSocket）+ 一键回滚
import { useEffect, useState, useRef } from 'react'
import { Steps, Card, Alert, Button, message, Space, Popconfirm } from 'antd'
import { useParams } from 'react-router-dom'
import { getExecutionStatus, rollbackExecution } from '../services/api'
import StatusTag from '../components/StatusTag'

interface ExecStep {
  status?: string
  command?: string
  step_index?: number
}

interface ExecStatus {
  steps?: ExecStep[]
  logs?: string[]
  finished?: boolean
  rollbackable?: boolean
}

export default function ExecutionDetail() {
  const { planId } = useParams()
  const [steps, setSteps] = useState<ExecStep[]>([])
  const [logs, setLogs] = useState<string[]>([])
  const [finished, setFinished] = useState(false)
  const [rollbackable, setRollbackable] = useState(false)
  const [rolling, setRolling] = useState(false)
  const sinceStepRef = useRef(0)
  const timerRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined)

  const poll = async () => {
    try {
      const data = (await getExecutionStatus(Number(planId), sinceStepRef.current)) as ExecStatus
      if (data?.steps) {
        setSteps(data.steps)
        sinceStepRef.current = data.steps.length
      }
      const logs = data.logs
      if (logs) setLogs((prev) => [...prev, ...logs])
      if (data?.finished) {
        setFinished(true)
        clearInterval(timerRef.current)
      }
      setRollbackable(!!data?.rollbackable)
    } catch (e) {
      // 忽略轮询错误
    }
  }

  useEffect(() => {
    poll()
    timerRef.current = setInterval(poll, 3000) // 3 秒轮询
    const onVisibility = () => {
      if (document.hidden) clearInterval(timerRef.current)
      else { clearInterval(timerRef.current); timerRef.current = setInterval(poll, 3000) }
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      clearInterval(timerRef.current)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [planId])

  const handleRollback = async () => {
    setRolling(true)
    try {
      const res = (await rollbackExecution(Number(planId))) as { success?: boolean }
      if (res?.success) message.success('已执行回滚（逆序执行各步骤 rollback_command）')
      else message.warning('回滚未完全成功，请查看执行日志')
      sinceStepRef.current = 0
      setSteps([])
      setLogs([])
      poll()
    } catch (e) {
      message.error((e as { response?: { data?: { message?: string } } })?.response?.data?.message || '回滚失败')
    } finally {
      setRolling(false)
    }
  }

  return (
    <div>
      <Card
        title={`执行详情 #${planId}`}
        extra={
          <Space>
            {finished ? <StatusTag status="completed" /> : <StatusTag status="executing" />}
            <Popconfirm
              title="确认一键回滚？"
              description="将按步骤逆序执行回滚命令，属变更操作，请确认。"
              okText="确认回滚" cancelText="取消"
              onConfirm={handleRollback}
            >
              <Button danger size="small" loading={rolling} disabled={!rollbackable}>一键回滚</Button>
            </Popconfirm>
          </Space>
        }
      >
        <Steps
          direction="vertical"
          current={steps.filter((s) => s.status === 'success').length}
          items={steps.map((s) => ({
            title: s.command || `步骤 ${s.step_index}`,
            description: s.status,
            status: s.status === 'success' ? 'finish' : s.status === 'failed' ? 'error' : 'process',
          }))}
        />
      </Card>

      {finished && <Alert type="success" message="执行完成" style={{ marginTop: 16 }} />}

      <Card title="实时日志" style={{ marginTop: 16 }}>
        <pre style={{ background: '#1e1e1e', color: '#d4d4d4', padding: 12, borderRadius: 4, maxHeight: 400, overflow: 'auto' }}>
          {logs.join('\n') || '（无日志）'}
        </pre>
      </Card>
    </div>
  )
}
