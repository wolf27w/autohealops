// 任务列表页
import { useEffect, useState } from 'react'
import { Table, Select, Space } from 'antd'
import { useNavigate } from 'react-router-dom'
import { getTasks } from '../services/api'
import StatusTag from '../components/StatusTag'

interface TaskItem {
  id: number
  alert_id?: number
  alert_fault?: string
  status?: string
  assignee?: string
  created_at?: string
  [key: string]: unknown
}

export default function TaskList() {
  const [tasks, setTasks] = useState<TaskItem[]>([])
  const [status, setStatus] = useState<string>()
  const navigate = useNavigate()

  useEffect(() => {
    getTasks(status ? { status } : undefined).then((d) => setTasks(d as TaskItem[]))
  }, [status])

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Select placeholder="状态" allowClear style={{ width: 160 }} onChange={setStatus}
          options={['pending', 'acknowledged', 'in_progress', 'resolved'].map((s) => ({ value: s, label: s }))} />
      </Space>
      <Table
        rowKey="id"
        dataSource={tasks}
        onRow={(record) => ({ onClick: () => navigate(`/tasks/${record.id}`), style: { cursor: 'pointer' } })}
        columns={[
          { title: 'ID', dataIndex: 'id' },
          { title: '关联告警', dataIndex: 'alert_id' },
          { title: '故障类型', dataIndex: 'alert_fault' },
          { title: '状态', dataIndex: 'status', render: (s) => <StatusTag status={s} /> },
          { title: '处理人', dataIndex: 'assignee' },
          { title: '创建时间', dataIndex: 'created_at' },
        ]}
      />
    </div>
  )
}
