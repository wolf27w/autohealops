// 任务详情页：状态流转 + 时间线
import { useEffect, useState } from 'react'
import { Card, Descriptions, Button, Space, Timeline, Modal, Input, message } from 'antd'
import { useParams } from 'react-router-dom'
import { getTask, acknowledgeTask, startTask, resolveTask } from '../services/api'
import StatusTag from '../components/StatusTag'

interface TaskInfo {
  status: string
  assignee?: string
  resolution?: string
}

interface TimelineEntry {
  action?: string
  old_status?: string
  new_status?: string
  operator?: string
  comment?: string
  created_at?: string
}

interface TaskDetailData {
  task: TaskInfo
  timeline?: TimelineEntry[]
}

export default function TaskDetail() {
  const { id } = useParams()
  const [task, setTask] = useState<TaskDetailData | null>(null)
  const [modalOpen, setModalOpen] = useState(false)
  const [comment, setComment] = useState('')

  const load = () => getTask(Number(id)).then((d) => setTask(d as TaskDetailData))
  useEffect(() => { load() }, [id])

  if (!task) return null
  const t = task.task
  const timeline = task.timeline || []

  const act = async (fn: (id: number, data?: Record<string, unknown>) => Promise<unknown>) => {
    setModalOpen(false)
    await fn(Number(id), { comment })
    setComment('')
    message.success('操作成功')
    load()
  }

  return (
    <div>
      <Card title={`任务 #${id}`}>
        <Descriptions column={2}>
          <Descriptions.Item label="状态"><StatusTag status={t.status} /></Descriptions.Item>
          <Descriptions.Item label="处理人">{t.assignee || '-'}</Descriptions.Item>
          <Descriptions.Item label="解决方案" span={2}>{t.resolution || '-'}</Descriptions.Item>
        </Descriptions>
        <Space style={{ marginTop: 16 }}>
          {t.status === 'pending' && <Button type="primary" onClick={() => setModalOpen(true)}>确认(acknowledge)</Button>}
          {t.status === 'acknowledged' && <Button type="primary" onClick={() => act(startTask)}>开始(start)</Button>}
          {t.status === 'in_progress' && <Button type="primary" onClick={() => setModalOpen(true)}>解决(resolve)</Button>}
        </Space>
      </Card>

      <Card title="操作时间线" style={{ marginTop: 16 }}>
        <Timeline
          items={timeline.map((l: TimelineEntry) => ({
            children: (
              <div>
                <b>{l.action}</b>：{l.old_status || '-'} → {l.new_status} ｜ 操作者 {l.operator || '-'} ｜ {l.comment}
                <div style={{ color: '#999', fontSize: 12 }}>{l.created_at}</div>
              </div>
            ),
          }))}
        />
      </Card>

      <Modal title="操作备注" open={modalOpen} onCancel={() => setModalOpen(false)}
        onOk={() => act(t.status === 'pending' ? acknowledgeTask : resolveTask)}>
        <Input.TextArea value={comment} onChange={(e) => setComment(e.target.value)} placeholder="备注（可选）" />
      </Modal>
    </div>
  )
}
