// AI 诊断助手（会话式 Agent）对话页
import { useEffect, useRef, useState } from 'react'
import {
  Layout, Button, Input, List, Tag, Space, Empty, Modal, Form, Select, Spin,
  Card, Collapse, Typography, message, Switch, Tooltip,
} from 'antd'
import {
  PlusOutlined, SendOutlined, RobotOutlined, UserOutlined,
  CheckCircleOutlined, ToolOutlined, ReloadOutlined, RadarChartOutlined,
} from '@ant-design/icons'
import {
  getClusters, getClusterNamespaces, getNamespacePods, getAgentSessions, getAgentMessages,
  createAgentSession, sendAgentMessage, closeAgentSession, runProactiveScan,
} from '../services/api'
import type { Cluster } from '../types'

const { Sider, Content } = Layout
const { Text, Paragraph } = Typography

interface Session { id: number; cluster_id: number; namespace: string; pod_name: string; title: string; status: string; source?: string; updated_at: string }
interface Msg { id: number; role: string; content: string; tool_name: string; tool_args: string; tool_output: string; created_at: string }
interface PodOption { label: string; value: string }

function parseFinalAnswer(content: string) {
  try {
    return JSON.parse(content)
  } catch {
    return { root_cause: content, suggestions: [], handling_advice: '' }
  }
}

const KB_HIT_TAG = (
  <Tooltip title="命中知识库历史方案，本次未调用 AI（零成本）">
    <Tag color="green">知识库复用</Tag>
  </Tooltip>
)

export default function AgentChat() {
  const [sessions, setSessions] = useState<Session[]>([])
  const [activeId, setActiveId] = useState<number | null>(null)
  const [messages, setMessages] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [form] = Form.useForm()
  const [clusters, setClusters] = useState<Cluster[]>([])
  const [namespaces, setNamespaces] = useState<string[]>([])
  const [pods, setPods] = useState<PodOption[]>([])
  const [loadingMsgs, setLoadingMsgs] = useState(false)
  const [deep, setDeep] = useState(true)  // 深度诊断开关（多轮工具取证，默认开：直接给结果）
  const [scanning, setScanning] = useState(false)
  const isAdmin = (() => { try { return JSON.parse(localStorage.getItem('user') || '{}').role === 'admin' } catch { return false } })()
  const bottomRef = useRef<HTMLDivElement>(null)
  const activeIdRef = useRef<number | null>(null)  // 当前会话 id（轮询时防竞态）

  const loadSessions = () => getAgentSessions().then((d) => setSessions((d || []) as Session[]))

  const handleProactiveScan = async () => {
    setScanning(true)
    try {
      const d = await runProactiveScan()
      if (d?.skipped) message.info(`巡检跳过：${d.reason || '已有巡检进行中'}`)
      else message.success(`巡检完成：命中 ${d?.hits ?? 0}，自动诊断 ${d?.diagnosed ?? 0}（跳过在途告警 ${d?.skipped_open_alert ?? 0} / 冷却 ${d?.skipped_cooldown ?? 0}）`)
      loadSessions()
    } catch { message.error('触发巡检失败') } finally { setScanning(false) }
  }

  useEffect(() => { loadSessions() }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, sending])

  const loadMessages = (id: number) => {
    setLoadingMsgs(true)
    getAgentMessages(id).then((d) => setMessages((d || []) as Msg[])).finally(() => setLoadingMsgs(false))
  }

  const openSession = (id: number) => {
    activeIdRef.current = id
    setActiveId(id)
    loadMessages(id)
  }

  // 首轮诊断在后台执行（方案 A）：轮询直到出现 assistant 结论或超时
  const pollUntilAnswered = async (id: number, maxMs = 300000) => {
    setSending(true)
    const started = Date.now()
    try {
      while (Date.now() - started < maxMs) {
        await new Promise((r) => setTimeout(r, 2500))
        if (activeIdRef.current !== id) return  // 已切走，停止轮询
        const msgs = ((await getAgentMessages(id)) || []) as Msg[]
        setMessages(msgs)
        if (msgs.length && msgs[msgs.length - 1].role === 'assistant') return
      }
      message.warning('诊断耗时较长，可稍后点「刷新」查看结果')
    } catch {
      // 轮询失败不阻断页面，用户可手动刷新
    } finally {
      setSending(false)
      loadSessions()
    }
  }

  const loadClusters = () => getClusters().then((d) => setClusters(d || []))

  const handleClusterChange = (clusterId: number) => {
    form.setFieldsValue({ namespace: undefined, pod_name: undefined })
    setNamespaces([])
    setPods([])
    getClusterNamespaces(clusterId).then((d) => setNamespaces(d || []))
  }

  const handleNamespaceChange = (ns: string) => {
    form.setFieldsValue({ pod_name: undefined })
    const clusterId = form.getFieldValue('cluster_id')
    getNamespacePods(clusterId, ns).then((d) => {
      setPods(((d || []) as Array<{ name: string }>).map((p) => ({ label: p.name, value: p.name })))
    })
  }

  const handleCreate = async () => {
    let values
    try {
      values = await form.validateFields()
    } catch {
      return  // 表单校验未通过：保留窗口
    }
    try {
      const res = (await createAgentSession(values)) as { id?: number; diagnosing?: boolean }
      // 后端建好会话即返回（首轮诊断后台跑）→ 立刻关窗，再打开会话并轮询结果
      message.success('会话已创建')
      setModalOpen(false)
      form.resetFields()
      setPods([])
      loadSessions()
      if (res?.id) {
        openSession(res.id)
        if (res?.diagnosing) pollUntilAnswered(res.id)
      }
    } catch {
      // 创建失败：响应拦截器已提示错误，保留窗口便于重试
    }
  }

  const handleSend = async () => {
    const text = input.trim()
    if (!text || !activeId || sending) return
    setInput('')
    try {
      await sendAgentMessage(activeId, text, deep)  // 后端已改为后台诊断，立即返回
      pollUntilAnswered(activeId)  // 后台轮询直到出结论（内部管理 sending 状态）
    } catch {
      message.error('发送失败')
      setSending(false)
    }
  }

  const handleClose = async () => {
    if (!activeId) return
    const res = (await closeAgentSession(activeId)) as { sunk?: boolean; closed?: boolean; reason?: string }
    if (res?.sunk) message.success('已确认完成，诊断结论已沉淀到知识库')
    else if (res?.closed) message.warning('会话已关闭，但知识库沉淀失败（可能 embedding 未配置）')
    else message.warning(res?.reason || '关闭失败')
    loadSessions()
    loadMessages(activeId)
  }

  const renderContent = (m: Msg) => {
    if (m.role === 'user') {
      return <Paragraph style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{m.content}</Paragraph>
    }
    if (m.role === 'tool') {
      return (
        <Collapse size="small" ghost style={{ background: '#fafafa' }}>
          <Collapse.Panel
            header={<Space size={4}><ToolOutlined style={{ color: '#722ed1' }} /><Tag color="purple">{m.tool_name || '工具'}</Tag><Text type="secondary" style={{ fontSize: 12 }}>{m.tool_args}</Text></Space>}
            key="1"
          >
            <pre style={{ margin: 0, fontSize: 12, whiteSpace: 'pre-wrap', maxHeight: 300, overflow: 'auto' }}>{m.tool_output}</pre>
          </Collapse.Panel>
        </Collapse>
      )
    }
    // assistant：AI 诊断结论
    const fa = parseFinalAnswer(m.content)
    return (
      <div>
        <Space style={{ marginBottom: 6 }} wrap>
          <Tag color="red">根因</Tag>
          {fa.kb_hit ? KB_HIT_TAG : null}
          {fa.model_used ? <Tag>{fa.model_used}</Tag> : null}
          <Text strong>{fa.root_cause || '（未给出明确根因）'}</Text>
        </Space>
        {fa.suggestions && (Array.isArray(fa.suggestions) ? fa.suggestions.length > 0 : String(fa.suggestions).length > 0) && (
          <div style={{ marginBottom: 6 }}>
            <Tag color="blue">处理意见</Tag>
            {Array.isArray(fa.suggestions)
              ? <ul style={{ margin: '4px 0 0 20px', padding: 0 }}>{(fa.suggestions as string[]).map((s: string, i: number) => <li key={i}>{String(s)}</li>)}</ul>
              : <div style={{ marginTop: 4 }}>{String(fa.suggestions)}</div>}
          </div>
        )}
        {fa.handling_advice && (
          <Paragraph style={{ margin: '6px 0 0', background: '#fffbe6', padding: 8, borderRadius: 4 }}>
            {fa.handling_advice}
          </Paragraph>
        )}
      </div>
    )
  }

  const activeSession = sessions.find((s) => s.id === activeId)

  return (
    <Layout style={{ height: 'calc(100vh - 120px)', background: '#fff' }}>
      <Sider width={280} theme="light" style={{ borderRight: '1px solid #f0f0f0', padding: 12 }}>
        <Button type="primary" block icon={<PlusOutlined />} onClick={() => { loadClusters(); setModalOpen(true) }}>
          新建诊断会话
        </Button>
        {isAdmin && (
          <Button block style={{ marginTop: 8 }} icon={<RadarChartOutlined />} loading={scanning} onClick={handleProactiveScan}>
            立即巡检（主动诊断）
          </Button>
        )}
        <List
          style={{ marginTop: 12 }}
          dataSource={sessions}
          locale={{ emptyText: <Empty description="暂无会话" /> }}
          renderItem={(s) => (
            <List.Item
              onClick={() => openSession(s.id)}
              style={{ cursor: 'pointer', padding: '8px 12px', borderRadius: 6, background: s.id === activeId ? '#e6f4ff' : 'transparent' }}
            >
              <div style={{ width: '100%' }}>
                <Space size={4} style={{ width: '100%', justifyContent: 'space-between' }}>
                  <Text ellipsis style={{ maxWidth: 150 }}>{s.title}</Text>
                  <Space size={2}>
                    {s.source === 'proactive' && <Tag color="orange">巡检</Tag>}
                    <Tag color={s.status === 'closed' ? 'green' : 'blue'}>{s.status === 'closed' ? '已闭环' : '进行中'}</Tag>
                  </Space>
                </Space>
                <div style={{ color: '#999', fontSize: 12 }}>{s.namespace}/{s.pod_name}</div>
              </div>
            </List.Item>
          )}
        />
      </Sider>
      <Content style={{ display: 'flex', flexDirection: 'column', padding: '0 16px' }}>
        {!activeSession ? (
          <Empty style={{ marginTop: 120 }} description="选择或新建一个诊断会话开始" />
        ) : (
          <>
            <div style={{ padding: '12px 0', borderBottom: '1px solid #f0f0f0', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <Space>
                <Text strong>{activeSession.title}</Text>
                <Tag color="blue">{activeSession.cluster_id ? `集群 ${activeSession.cluster_id}` : ''}</Tag>
                <Text type="secondary">{activeSession.namespace}/{activeSession.pod_name}</Text>
              </Space>
              <Space>
                <Tooltip title="默认开启：AI 多轮自主取证直接给诊断结果；关闭 = 平台预采集证据单轮快出结论">
                  <Space size={4}>
                    <Text type="secondary" style={{ fontSize: 12 }}>深度诊断</Text>
                    <Switch size="small" checked={deep} onChange={setDeep} />
                  </Space>
                </Tooltip>
                <Button size="small" icon={<ReloadOutlined />} onClick={() => loadMessages(activeId!)}>刷新</Button>
                <Button
                  size="small" type="primary" icon={<CheckCircleOutlined />}
                  disabled={activeSession.status === 'closed'}
                  onClick={handleClose}
                >
                  确认处理完成（沉淀知识库）
                </Button>
              </Space>
            </div>

            <div style={{ flex: 1, overflow: 'auto', padding: '16px 0' }}>
              {loadingMsgs ? <Spin style={{ display: 'block', margin: '40px auto' }} /> : (
                messages.map((m) => (
                  <div key={m.id} style={{ display: 'flex', marginBottom: 16, justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start' }}>
                    {m.role !== 'user' && (
                      <div style={{ marginRight: 8, marginTop: 4 }}>
                        {m.role === 'tool' ? <ToolOutlined style={{ color: '#722ed1' }} /> : <RobotOutlined style={{ color: '#1677ff' }} />}
                      </div>
                    )}
                    <div style={{
                      maxWidth: '78%', padding: '10px 14px', borderRadius: 8,
                      background: m.role === 'user' ? '#1677ff' : m.role === 'tool' ? '#fafafa' : '#f5f5f5',
                      color: m.role === 'user' ? '#fff' : 'inherit',
                    }}>
                      {renderContent(m)}
                    </div>
                    {m.role === 'user' && (
                      <div style={{ marginLeft: 8, marginTop: 4 }}><UserOutlined /></div>
                    )}
                  </div>
                ))
              )}
              {sending && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#999' }}>
                  <RobotOutlined /> AI 诊断中，正在调用工具采集信息...
                </div>
              )}
              <div ref={bottomRef} />
            </div>

            <div style={{ padding: '12px 0', borderTop: '1px solid #f0f0f0', display: 'flex', gap: 8 }}>
              <Input.TextArea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="描述你遇到的问题，或继续追问（如：为什么内存一直在涨？）"
                autoSize={{ minRows: 1, maxRows: 4 }}
                onPressEnter={(e) => { if (!e.shiftKey) { e.preventDefault(); handleSend() } }}
                disabled={activeSession.status === 'closed'}
              />
              <Button type="primary" icon={<SendOutlined />} onClick={handleSend} loading={sending} disabled={activeSession.status === 'closed'}>
                发送
              </Button>
            </div>
          </>
        )}
      </Content>

      <Modal title="新建诊断会话" open={modalOpen} onOk={handleCreate} onCancel={() => setModalOpen(false)} destroyOnClose>
        <Form form={form} layout="vertical">
          <Form.Item name="cluster_id" label="集群" rules={[{ required: true, message: '请选择集群' }]}>
            <Select placeholder="选择集群" onChange={handleClusterChange} options={clusters.map((c) => ({ label: c.name, value: c.id }))} />
          </Form.Item>
          <Form.Item name="namespace" label="命名空间" rules={[{ required: true, message: '请选择命名空间' }]}>
            <Select placeholder="选择命名空间" onChange={handleNamespaceChange} disabled={!namespaces.length}
              options={namespaces.map((n) => ({ label: n, value: n }))} />
          </Form.Item>
          <Form.Item name="pod_name" label="Pod" rules={[{ required: true, message: '请选择 Pod' }]}>
            <Select placeholder="选择 Pod" showSearch optionFilterProp="label" disabled={!pods.length} options={pods} />
          </Form.Item>
          <Form.Item name="problem" label="问题描述">
            <Input.TextArea rows={3} placeholder="描述应用的问题现象，如：服务 502、内存泄漏、启动失败..." />
          </Form.Item>
          <Form.Item name="deep" label="深度诊断" valuePropName="checked" initialValue={true}
            tooltip="默认开启：AI 多轮自主取证直接给诊断结果（较慢）。关闭：平台预采集证据单轮快出结论。">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </Layout>
  )
}
