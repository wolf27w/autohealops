// R13：提示词建议（未分类故障 → AI 起草 → 人工确认后建模板 + 绑路由）
import { useEffect, useState } from 'react'
import {
  Alert, Button, Descriptions, Form, Input, Modal, Popconfirm, Space, Table, Tag, Tooltip, message,
} from 'antd'
import { CheckOutlined, ReloadOutlined } from '@ant-design/icons'
import {
  getPromptSuggestions, updatePromptSuggestion, acceptPromptSuggestion, ignorePromptSuggestion,
} from '../services/api'
import type { PromptSuggestion } from '../services/api'

const STATUS: Record<string, { color: string; label: string }> = {
  pending: { color: 'gold', label: '待审核' },
  accepted: { color: 'green', label: '已采纳' },
  ignored: { color: 'default', label: '已忽略' },
}

export default function PromptSuggestions() {
  const [list, setList] = useState<PromptSuggestion[]>([])
  const [loading, setLoading] = useState(false)
  const [detail, setDetail] = useState<PromptSuggestion | null>(null)
  const [form] = Form.useForm()

  const load = () => {
    setLoading(true)
    getPromptSuggestions()
      .then((d) => setList(d || []))
      .finally(() => setLoading(false))
  }
  useEffect(load, [])

  const openDetail = (s: PromptSuggestion) => {
    setDetail(s)
    form.setFieldsValue({ name: s.suggestion_name, system_prompt: s.system_prompt || '', user_prompt_template: s.user_prompt_template || '' })
  }

  const doAccept = async () => {
    const v = await form.validateFields()
    if (!detail) return
    await acceptPromptSuggestion(detail.id, v)
    message.success('已采纳：已创建模板并绑定路由')
    setDetail(null)
    load()
  }

  const doIgnore = async (id: number) => {
    await ignorePromptSuggestion(id)
    message.success('已忽略')
    load()
  }

  const doSaveDraft = async () => {
    const v = await form.validateFields()
    if (!detail) return
    await updatePromptSuggestion(detail.id, v)
    message.success('草稿已保存')
    load()
  }

  const pendingCount = list.filter((s) => s.status === 'pending').length

  return (
    <div>
      <Space style={{ marginBottom: 12 }}>
        <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
        {pendingCount > 0 && <Tag color="gold">待审核 {pendingCount} 条</Tag>}
      </Space>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="未分类故障确认解决后，系统会起草「提示词建议」。必须人工确认后才创建模板并绑定路由；不确认不会创建、不参与诊断。"
      />
      <Table<PromptSuggestion>
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={list}
        columns={[
          { title: '#', dataIndex: 'id', width: 60 },
          { title: '故障类型', dataIndex: 'fault_type', width: 180, render: (v: string) => <Tag>{v}</Tag> },
          { title: '建议模板名', dataIndex: 'suggestion_name', ellipsis: true },
          { title: '命中次数', dataIndex: 'hit_count', width: 90 },
          { title: '相似度', dataIndex: 'similarity_note', width: 200, ellipsis: true, render: (v: string) => (v ? <Tooltip title={v}>{v}</Tooltip> : <span style={{ color: '#999' }}>—</span>) },
          { title: '状态', dataIndex: 'status', width: 90, render: (s: string) => <Tag color={STATUS[s]?.color}>{STATUS[s]?.label || s}</Tag> },
          { title: '生成时间', dataIndex: 'created_at', width: 170 },
          {
            title: '操作', width: 160,
            render: (_: unknown, r: PromptSuggestion) => (
              <Space size={4}>
                <Button size="small" type="link" onClick={() => openDetail(r)}>查看/编辑</Button>
                {r.status === 'pending' && (
                  <Popconfirm title="忽略该建议？" onConfirm={() => doIgnore(r.id)}>
                    <Button size="small" type="link" danger>忽略</Button>
                  </Popconfirm>
                )}
              </Space>
            ),
          },
        ]}
      />

      <Modal
        title={`提示词建议 #${detail?.id} · ${detail?.fault_type || ''}`}
        open={!!detail}
        onCancel={() => setDetail(null)}
        width={780}
        destroyOnClose
        footer={detail?.status === 'pending' ? [
          <Button key="save" onClick={doSaveDraft}>保存草稿</Button>,
          <Button key="cancel" onClick={() => setDetail(null)}>关闭</Button>,
          <Button key="accept" type="primary" icon={<CheckOutlined />} onClick={doAccept}>采纳并建模板</Button>,
        ] : [
          <Button key="close" onClick={() => setDetail(null)}>关闭</Button>,
        ]}
      >
        {detail && (
          <>
            <Descriptions size="small" column={2} style={{ marginBottom: 12 }}>
              <Descriptions.Item label="故障类型">{detail.fault_type}</Descriptions.Item>
              <Descriptions.Item label="命中次数">{detail.hit_count}</Descriptions.Item>
              <Descriptions.Item label="相似度" span={2}>{detail.similarity_note || '—（建议新建）'}</Descriptions.Item>
              {detail.root_cause && <Descriptions.Item label="诊断根因" span={2}>{detail.root_cause.slice(0, 400)}</Descriptions.Item>}
            </Descriptions>
            {detail.sample_logs && (
              <div style={{ marginBottom: 12 }}>
                <div style={{ color: '#8c8c8c', fontSize: 12, marginBottom: 4 }}>样例日志（已脱敏）</div>
                <pre style={{ maxHeight: 140, overflow: 'auto', background: '#fafafa', padding: 8, fontSize: 12, borderRadius: 4 }}>{detail.sample_logs}</pre>
              </div>
            )}
            <Form form={form} layout="vertical">
              <Form.Item name="name" label="模板名" rules={[{ required: true }]}><Input /></Form.Item>
              <Form.Item name="system_prompt" label="系统提示词" rules={[{ required: true }]}>
                <Input.TextArea rows={6} style={{ fontFamily: 'monospace' }} />
              </Form.Item>
              <Form.Item name="user_prompt_template" label="用户提示词模板（{变量} 占位）" rules={[{ required: true }]}>
                <Input.TextArea rows={8} style={{ fontFamily: 'monospace' }} />
              </Form.Item>
            </Form>
          </>
        )}
      </Modal>
    </div>
  )
}
