// 提示词模板页：列表 + 新增 + 查看 + 编辑（version 自动+1）
import { useEffect, useState } from 'react'
import { Table, Tag, Drawer, Descriptions, Button, Input, Form, Select, message } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { getPromptTemplates, createPromptTemplate, updatePromptTemplate } from '../services/api'

interface PromptTemplate {
  id: number
  name: string
  category?: string
  version?: number
  is_active?: boolean
  system_prompt?: string
  user_prompt_template?: string
  variables?: string
  [key: string]: unknown
}

export default function PromptTemplates() {
  const [templates, setTemplates] = useState<PromptTemplate[]>([])
  const [loading, setLoading] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [editing, setEditing] = useState(false)
  const [creating, setCreating] = useState(false)
  const [current, setCurrent] = useState<PromptTemplate | null>(null)
  const [form] = Form.useForm()

  const load = () => {
    setLoading(true)
    getPromptTemplates().then((d) => setTemplates(d as PromptTemplate[])).finally(() => setLoading(false))
  }
  useEffect(load, [])

  const openCreate = () => {
    setCreating(true)
    setEditing(true)
    setCurrent(null)
    form.resetFields()
    form.setFieldsValue({ category: 'diagnosis' })
    setDrawerOpen(true)
  }

  const openView = (t: PromptTemplate) => {
    setCurrent(t)
    setCreating(false)
    setEditing(false)
    form.setFieldsValue(t)
    setDrawerOpen(true)
  }

  const openEdit = (t: PromptTemplate) => {
    setCurrent(t)
    setCreating(false)
    setEditing(true)
    form.setFieldsValue(t)
    setDrawerOpen(true)
  }

  const handleSave = async () => {
    const values = await form.validateFields()
    if (creating) {
      await createPromptTemplate(values)
      message.success('模板已创建')
    } else {
      await updatePromptTemplate(current!.id, values)
      message.success('已保存（版本号已 +1）')
    }
    setDrawerOpen(false)
    load()
  }

  return (
    <div>
      <Button type="primary" icon={<PlusOutlined />} style={{ marginBottom: 16 }} onClick={openCreate}>新增模板</Button>
      <Table
        rowKey="id"
        loading={loading}
        dataSource={templates}
        columns={[
          { title: '模板名', dataIndex: 'name' },
          { title: '分类', dataIndex: 'category', render: (c) => <Tag>{c}</Tag> },
          { title: '版本', dataIndex: 'version' },
          { title: '状态', dataIndex: 'is_active', render: (v) => (v ? '启用' : '停用') },
          { title: '操作', render: (_, r) => (
            <>
              <Button size="small" onClick={() => openView(r)} style={{ marginRight: 8 }}>查看</Button>
              <Button size="small" type="primary" onClick={() => openEdit(r)}>编辑</Button>
            </>
          )},
        ]}
      />
      <Drawer title={creating ? '新增模板' : editing ? `编辑模板：${current?.name}` : `查看模板：${current?.name}`} open={drawerOpen}
        onClose={() => setDrawerOpen(false)} width={700}
        extra={editing && <Button type="primary" onClick={handleSave}>保存</Button>}>
        <Form form={form} layout="vertical">
          {creating && (
            <>
              <Form.Item name="name" label="模板名（唯一）" rules={[{ required: true }]}><Input /></Form.Item>
              <Form.Item name="category" label="分类">
                <Select options={['diagnosis', 'repair_plan', 'risk_assessment', 'verification'].map((c) => ({ value: c, label: c }))} />
              </Form.Item>
            </>
          )}
          {!creating && (
            <Descriptions column={1} style={{ marginBottom: 16 }}>
              <Descriptions.Item label="分类">{current?.category}</Descriptions.Item>
              <Descriptions.Item label="版本">{current?.version}</Descriptions.Item>
            </Descriptions>
          )}
          <Form.Item name="system_prompt" label="System Prompt" rules={[{ required: true }]}>
            <Input.TextArea rows={4} disabled={!editing} />
          </Form.Item>
          <Form.Item name="user_prompt_template" label="User Prompt 模板" rules={[{ required: true }]}>
            <Input.TextArea rows={6} disabled={!editing} />
          </Form.Item>
          {!creating && <Form.Item name="variables" label="变量列表"><Input disabled /></Form.Item>}
        </Form>
      </Drawer>
    </div>
  )
}
