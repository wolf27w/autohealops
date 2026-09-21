// 知识库页：查询所有用户；新增 operator；编辑 admin 全量 / operator 仅人工条目（AI 条目锁定）
import { useEffect, useState } from 'react'
import { Input, Card, Tag, Space, List, Empty, Button, Modal, Form, message, Popconfirm, Row, Col, Statistic, Upload } from 'antd'
import { SearchOutlined, PlusOutlined, EditOutlined, LockOutlined, UnlockOutlined, DeleteOutlined, DownloadOutlined, UploadOutlined, StopOutlined, PlayCircleOutlined } from '@ant-design/icons'
import { getKnowledgeList, getKnowledgeStats, exportKnowledge, importKnowledge, setKnowledgeStatus, searchKnowledge, addKnowledge, updateKnowledge, deleteKnowledge, dedupKnowledge } from '../services/api'
import type { KnowledgeEntry } from '../types'
import { canWrite, isAdmin } from '../utils/auth'

interface KnowledgeStats {
  total?: number
  active?: number
  inactive?: number
  hit_entries?: number
  hit_rate?: number
  total_usage?: number
}

export default function KnowledgeBase() {
  const writable = canWrite()
  const admin = isAdmin()
  const [query, setQuery] = useState('')
  const [list, setList] = useState<KnowledgeEntry[]>([])
  const [stats, setStats] = useState<KnowledgeStats>({})
  const [importOpen, setImportOpen] = useState(false)
  const [importText, setImportText] = useState('')
  const [modalOpen, setModalOpen] = useState(false)
  const [form] = Form.useForm()
  let debounceTimer: ReturnType<typeof setTimeout> | undefined

  const loadStats = () => getKnowledgeStats().then((d) => setStats((d || {}) as KnowledgeStats)).catch(() => {})
  const loadList = () => getKnowledgeList().then((d) => setList(d))
  useEffect(() => { loadList(); loadStats() }, [])

  const handleToggle = async (item: KnowledgeEntry) => {
    await setKnowledgeStatus(item.id, !(item.is_active !== false))
    message.success(item.is_active !== false ? '已失效（不再参与检索）' : '已启用')
    loadList(); loadStats()
  }

  const handleExport = async () => {
    const d = (await exportKnowledge()) as { count?: number }
    const blob = new Blob([JSON.stringify(d, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `knowledge-export-${Date.now()}.json`
    a.click()
    URL.revokeObjectURL(url)
    message.success(`已导出 ${d?.count ?? 0} 条`)
  }

  const handleImport = async () => {
    try {
      const parsed = JSON.parse(importText)
      const items = Array.isArray(parsed) ? parsed : (parsed.items || [])
      if (!items.length) { message.warning('没有可导入的条目'); return }
      const r = (await importKnowledge(items)) as { ok?: number; failed?: number }
      message.success(`导入完成：成功 ${r?.ok ?? 0}，失败 ${r?.failed ?? 0}`)
      setImportOpen(false); setImportText(''); loadList(); loadStats()
    } catch (e) {
      message.error('JSON 解析失败：' + (e as Error).message)
    }
  }

  const handleSearch = (q: string) => {
    setQuery(q)
    clearTimeout(debounceTimer)
    debounceTimer = setTimeout(() => {
      if (q) searchKnowledge(q).then((d) => setList(((d as { results?: KnowledgeEntry[] }).results || []).map((r) => ({ ...r, fault_type: r.fault_type, root_cause: r.root_cause, repair_steps: r.repair_steps, similarity: r.similarity }))))
      else loadList()
    }, 300)
  }

  const handleAdd = async () => {
    const values = await form.validateFields()
    await addKnowledge(values)
    message.success('已添加')
    setModalOpen(false)
    form.resetFields()
    loadList()
  }

  // 清理重复：解决方法相同的条目合并为一条（保留最早）
  const handleDedup = async () => {
    try {
      const d = (await dedupKnowledge()) as { groups?: number; removed?: number }
      message.success(`已合并 ${d?.groups ?? 0} 组，删除 ${d?.removed ?? 0} 条重复条目`)
      loadList()
    } catch { /* 拦截器已提示（如无权限） */ }
  }

  // 编辑：admin 全量（可解锁 AI 条目）；operator 仅人工条目（后端 403 拦截兜底）
  const [editing, setEditing] = useState<KnowledgeEntry | null>(null)
  const openEdit = (item: KnowledgeEntry) => {
    setEditing(item)
    form.resetFields()
    form.setFieldsValue({
      fault_type: item.fault_type, root_cause: item.root_cause,
      repair_steps: item.repair_steps, tags: item.tags, execution_steps: item.execution_steps,
    })
    setModalOpen(true)
  }
  const handleEdit = async () => {
    const values = await form.validateFields()
    try {
      await updateKnowledge(editing!.id, values)
      message.success('已更新')
      setModalOpen(false)
      loadList()
    } catch { /* 拦截器已提示（如无权限） */ }
  }

  return (
    <div>
      <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
        {[
          { title: '条目总数', value: stats.total || 0 },
          { title: '启用', value: stats.active || 0, color: '#3f8600' },
          { title: '已失效', value: stats.inactive || 0, color: '#cf1322' },
          { title: '命中条目', value: stats.hit_entries || 0 },
          { title: '命中率', value: `${((stats.hit_rate || 0) * 100).toFixed(1)}%` },
          { title: '累计使用次数', value: stats.total_usage || 0, color: '#1677ff' },
        ].map((s) => (
          <Col key={s.title} xs={12} sm={8} md={4}>
            <Card size="small"><Statistic title={s.title} value={s.value} valueStyle={{ color: s.color, fontSize: 20 }} /></Card>
          </Col>
        ))}
      </Row>
      <Space style={{ marginBottom: 16 }} wrap>
        <Input prefix={<SearchOutlined />} placeholder="搜索知识（防抖300ms）" style={{ width: 300 }} onChange={(e) => handleSearch(e.target.value)} />
        {writable && <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>新增知识</Button>}
        <Button icon={<DownloadOutlined />} onClick={handleExport}>导出</Button>
        {writable && <Button icon={<UploadOutlined />} onClick={() => setImportOpen(true)}>导入</Button>}
        {writable && (
          <Popconfirm title="合并「解决方法相同」的重复条目？（保留最早一条，合并使用计数）" onConfirm={handleDedup}>
            <Button icon={<DeleteOutlined />}>清理重复</Button>
          </Popconfirm>
        )}
      </Space>
      <List
        dataSource={list}
        pagination={{
          pageSize: 10,
          showSizeChanger: true,
          showTotal: (total) => `共 ${total} 条`,
        }}
        locale={{ emptyText: <Empty description="暂无知识条目" /> }}
        renderItem={(item: KnowledgeEntry) => (
          <List.Item>
            <Card size="small"
              title={
                <Space>
                  <Tag>{item.fault_type}</Tag>
                  {item.source === 'ai' && <Tag color="gold">AI 生成</Tag>}
                  {item.is_locked
                    ? <Tag icon={<LockOutlined />} color="orange">已锁定</Tag>
                    : <Tag icon={<UnlockOutlined />}>可编辑</Tag>}
                  {item.is_active === false && <Tag color="red">已失效</Tag>}
                  <Tag color="blue">命中 {item.usage_count || 0} 次</Tag>
                </Space>
              }>
              <div><b>根因：</b>{item.root_cause?.slice(0, 100)}</div>
              <div><b>修复：</b>{item.repair_steps?.slice(0, 100)}</div>
              {item.execution_steps && <div style={{ fontSize: 12, color: '#666' }}><b>可执行：</b>{item.execution_steps.slice(0, 120)}...</div>}
              {item.success_count !== undefined && <div style={{ color: '#999', fontSize: 12 }}>成功 {item.success_count} 次 / 使用 {item.usage_count} 次</div>}
              <Space style={{ marginTop: 8 }}>
                {writable && <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(item)}>编辑</Button>}
                {writable && (
                  <Popconfirm title={item.is_active === false ? '确认启用该条目？' : '确认失效该条目？（失效后不再参与检索复用）'}
                    onConfirm={() => handleToggle(item)}>
                    <Button size="small" icon={item.is_active === false ? <PlayCircleOutlined /> : <StopOutlined />}>
                      {item.is_active === false ? '启用' : '失效'}
                    </Button>
                  </Popconfirm>
                )}
                {admin && (
                  <Popconfirm title="确认删除该条目？（仅 admin）" onConfirm={async () => { await deleteKnowledge(item.id); message.success('已删除'); loadList(); loadStats() }}>
                    <Button size="small" danger>删除</Button>
                  </Popconfirm>
                )}
              </Space>
            </Card>
          </List.Item>
        )}
      />
      <Modal title={editing ? '编辑知识条目' : '新增知识'} open={modalOpen} onOk={editing ? handleEdit : handleAdd} onCancel={() => { setModalOpen(false); setEditing(null) }}>
        <Form form={form} layout="vertical">
          <Form.Item name="fault_type" label="故障类型" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="root_cause" label="根因" rules={[{ required: true }]}><Input.TextArea rows={3} /></Form.Item>
          <Form.Item name="repair_steps" label="修复步骤" rules={[{ required: true }]}><Input.TextArea rows={3} /></Form.Item>
          <Form.Item name="execution_steps" label="可执行步骤（JSON 数组，命令用占位符 {namespace}/{pod_name} 等）"
            tooltip='示例：[{"command":"kubectl -n {namespace} delete pod {pod_name}","description":"删 pod 重启"}]'>
            <Input.TextArea rows={3} placeholder='[{"command":"kubectl -n {namespace} delete pod {pod_name}","description":"..."}]' />
          </Form.Item>
          <Form.Item name="tags" label="标签"><Input /></Form.Item>
        </Form>
      </Modal>

      {/* 导入（粘贴导出 JSON） */}
      <Modal title="导入知识条目" open={importOpen} onOk={handleImport} onCancel={() => setImportOpen(false)} width={720}>
        <div style={{ marginBottom: 8, color: '#888', fontSize: 12 }}>
          粘贴导出格式的 JSON（数组，或含 items 的对象）。字段：fault_type / root_cause / repair_steps / execution_steps / tags / category
        </div>
        <Input.TextArea rows={12} value={importText} onChange={(e) => setImportText(e.target.value)} placeholder='[{"fault_type":"OOMKilled","root_cause":"...","repair_steps":"..."}]' />
        <Upload showUploadList={false} accept=".json"
          beforeUpload={(file) => {
            const reader = new FileReader()
            reader.onload = () => setImportText(String(reader.result || ''))
            reader.readAsText(file)
            return false
          }}>
          <Button size="small" style={{ marginTop: 8 }}>选择 JSON 文件</Button>
        </Upload>
      </Modal>
    </div>
  )
}
