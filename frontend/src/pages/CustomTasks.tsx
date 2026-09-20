// 定时任务页
import { useEffect, useState } from 'react'
import { Table, Button, Modal, Form, Input, Select, Switch, Space, Drawer, message } from 'antd'
import { getCustomTasks, createCustomTask, updateCustomTask, deleteCustomTask, toggleCustomTask, runCustomTaskNow, getCustomTaskLogs, getClusters } from '../services/api'
import StatusTag from '../components/StatusTag'
import type { Cluster, CustomTask } from '../types'

interface TaskLog {
  id: number
  status?: string
  output?: string
  start_time?: string
  [key: string]: unknown
}

export default function CustomTasks() {
  const [tasks, setTasks] = useState<CustomTask[]>([])
  const [clusters, setClusters] = useState<Cluster[]>([])
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<CustomTask | null>(null)
  const [logsOpen, setLogsOpen] = useState(false)
  const [logs, setLogs] = useState<TaskLog[]>([])
  const [form] = Form.useForm()

  const load = () => {
    getCustomTasks().then((d) => setTasks(d))
    getClusters().then((d) => setClusters(d))
  }
  useEffect(() => { load() }, [])

  const openModal = (t?: CustomTask) => {
    setEditing(t || null)
    setModalOpen(true)
    if (t) form.setFieldsValue(t)
  }

  const handleSave = async () => {
    const values = await form.validateFields()
    if (editing) await updateCustomTask(editing.id, values)
    else await createCustomTask(values)
    message.success('已保存')
    setModalOpen(false)
    form.resetFields()
    load()
  }

  const showLogs = async (id: number) => {
    const d = (await getCustomTaskLogs(id)) as TaskLog[]
    setLogs(d)
    setLogsOpen(true)
  }

  return (
    <div>
      <Button type="primary" style={{ marginBottom: 16 }} onClick={() => openModal()}>创建任务</Button>
      <Table rowKey="id" dataSource={tasks}
        columns={[
          { title: '名称', dataIndex: 'name' },
          { title: '类型', dataIndex: 'task_type' },
          { title: '目标', dataIndex: 'target_resource' },
          { title: 'Cron', dataIndex: 'cron_expression' },
          { title: '启用', dataIndex: 'is_enabled', render: (v, r) => <Switch checked={v} onChange={() => toggleCustomTask(r.id).then(load)} /> },
          { title: '上次状态', dataIndex: 'last_run_status', render: (s) => <StatusTag status={s} /> },
          { title: '操作', render: (_, r) => (
            <Space>
              <Button size="small" onClick={() => runCustomTaskNow(r.id).then(() => message.success('已触发'))}>立即执行</Button>
              <Button size="small" onClick={() => showLogs(r.id)}>日志</Button>
              <Button size="small" onClick={() => openModal(r)}>编辑</Button>
              <Button size="small" danger onClick={async () => { await deleteCustomTask(r.id); load() }}>删除</Button>
            </Space>
          )},
        ]} />
      <Modal title={editing ? '编辑任务' : '创建任务'} open={modalOpen} onOk={handleSave} onCancel={() => setModalOpen(false)}>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="cluster_id" label="集群" rules={[{ required: true }]}>
            <Select showSearch optionFilterProp="label" placeholder="选择集群"
              options={clusters.map((c) => ({ value: c.id, label: `${c.name} (ID:${c.id})` }))} />
          </Form.Item>
          <Form.Item name="task_type" label="类型" rules={[{ required: true }]}>
            <Select options={['restart', 'scale', 'inspect', 'script', 'once'].map((t) => ({ value: t, label: t }))} />
          </Form.Item>
          <Form.Item name="target_resource" label="目标资源"><Input placeholder="deployment 名称" /></Form.Item>
          <Form.Item name="target_namespace" label="命名空间"><Input placeholder="default" /></Form.Item>
          <Form.Item name="cron_expression" label="Cron 表达式" rules={[{ required: true }]}><Input placeholder="0 2 * * *" /></Form.Item>
          <Form.Item name="script_content" label="脚本内容"><Input.TextArea rows={3} /></Form.Item>
        </Form>
      </Modal>
      <Drawer title="执行日志" open={logsOpen} onClose={() => setLogsOpen(false)} width={600}>
        <Table rowKey="id" dataSource={logs} pagination={false}
          columns={[
            { title: '状态', dataIndex: 'status', render: (s) => <StatusTag status={s} /> },
            { title: '输出', dataIndex: 'output', ellipsis: true },
            { title: '时间', dataIndex: 'start_time' },
          ]} />
      </Drawer>
    </div>
  )
}
