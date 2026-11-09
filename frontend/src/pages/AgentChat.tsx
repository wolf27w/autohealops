// AI 诊断助手（会话式 Agent）对话页
import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Layout, Button, Input, Tag, Space, Empty, Modal, Form, Select, Spin,
  Collapse, Typography, message, Switch, Tooltip, Popconfirm, ConfigProvider,
} from 'antd'
import {
  PlusOutlined, SendOutlined, RobotOutlined, UserOutlined,
  CheckCircleOutlined, ToolOutlined, ReloadOutlined, DeleteOutlined,
} from '@ant-design/icons'
import {
  getClusters, getClusterNamespaces, getNamespacePods, getPodContainers, getAgentSessions, getAgentMessages,
  createAgentSession, sendAgentMessage, closeAgentSession, deleteAgentSession, updateSessionCollect,
} from '../services/api'
import type { Cluster } from '../types'
import { buildTheme } from '../utils/theme'

const { Sider, Content } = Layout
const { Text, Paragraph } = Typography

interface Session { id: number; cluster_id: number; namespace: string; pod_name: string; title: string; status: string; source?: string; merged_count?: number; service?: string; container?: string; created_at?: string; updated_at: string; cluster_name?: string; env_tag?: string; collect_enabled?: boolean; collect_interval_min?: number; collect_items?: string; collect_last_at?: string | null }
interface Msg { id: number; role: string; content: string; tool_name: string; tool_args: string; tool_output: string; created_at: string }
interface PodOption { label: string; value: string }

// 会话显示名：集群环境-namespace-容器名（老板 2026-10-16）
function sessionName(s: Session): string {
  const label = s.env_tag || s.cluster_name || ''
  const last = s.container || s.service || s.pod_name
  const parts = [label, s.namespace, last].filter(Boolean)
  if (parts.length) return parts.join('-')
  return s.title || `会话 ${s.id}`
}

const localDay = (iso?: string | Date): string => {
  if (!iso) return ''
  let dt = iso instanceof Date ? iso : new Date(String(iso))
  if (isNaN(dt.getTime())) dt = new Date(String(iso).replace(' ', 'T'))
  if (isNaN(dt.getTime())) return ''
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`
}

const fmtDay = (d: string): string => {
  if (!d) return '未知日期'
  const today = localDay(new Date())
  const y = localDay(new Date(Date.now() - 86400000))
  if (d === today) return '今天'
  if (d === y) return '昨天'
  return d
}

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
  const [containers, setContainers] = useState<string[]>([])
  const [nsLoading, setNsLoading] = useState(false)
  const [podsLoading, setPodsLoading] = useState(false)
  const [ctnLoading, setCtnLoading] = useState(false)
  // 选择器数据本地缓存：集群→namespace、集群/ns→pods、集群/ns/pod→containers（切回来瞬时）
  const nsCache = useRef<Record<number, string[]>>({})
  const podsCache = useRef<Record<string, PodOption[]>>({})
  const ctnCache = useRef<Record<string, string[]>>({})
  const [olderOpen, setOlderOpen] = useState<Record<string, boolean>>({})
  const [searchParams] = useSearchParams()
  const [loadingMsgs, setLoadingMsgs] = useState(false)
  const [deep, setDeep] = useState(true)  // 深度诊断开关（多轮工具取证，默认开：直接给结果）
  const bottomRef = useRef<HTMLDivElement>(null)
  const activeIdRef = useRef<number | null>(null)  // 当前会话 id（轮询时防竞态）

  const loadSessions = () => getAgentSessions().then((d) => setSessions((d || []) as Session[]))

  const handleDeleteSession = async (sid: number) => {
    try {
      await deleteAgentSession(sid)
      message.success('已删除会话')
      if (activeIdRef.current === sid) { activeIdRef.current = null; setActiveId(null); setMessages([]) }
      loadSessions()
    } catch { /* 拦截器已提示 */ }
  }

  useEffect(() => { loadSessions() }, [])

  // 支持从 JVM 诊断等入口带 ?session=<id> 直接打开会话
  useEffect(() => {
    const sid = Number(searchParams.get('session') || 0)
    if (sid) openSession(sid)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // R21（2026-11-09）：默认只展开当天，历史日期全部折叠（点日期展开）
  const todayKey = localDay(new Date())

  // 按天分组（新→旧）
  const sessionGroups = useMemo(() => {
    const m = new Map<string, Session[]>()
    for (const s of sessions) {
      const d = localDay(s.created_at || s.updated_at) || '0000-00-00'
      if (!m.has(d)) m.set(d, [])
      m.get(d)!.push(s)
    }
    return [...m.entries()].sort((a, b) => (a[0] < b[0] ? 1 : -1))
  }, [sessions])

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

  const loadClusters = () => getClusters().then((d) => { const list = (d || []) as Cluster[]; setClusters(list); return list })

  const fetchNamespaces = async (clusterId: number): Promise<string[]> => {
    const hit = nsCache.current[clusterId]
    if (hit) return hit
    const d = await getClusterNamespaces(clusterId)
    const list = (d || []) as string[]
    nsCache.current[clusterId] = list
    return list
  }

  // 打开新建窗口时并行预热所有集群的 namespace，选集群时基本秒开
  const prefetchNamespaces = (list: Cluster[]) => {
    for (const c of list) {
      if (nsCache.current[c.id]) continue
      getClusterNamespaces(c.id).then((d) => { nsCache.current[c.id] = (d || []) as string[] }).catch(() => { /* 预热失败静默，选中时再拉 */ })
    }
  }

  const handleClusterChange = (clusterId: number) => {
    form.setFieldsValue({ namespace: undefined, pod_name: undefined })
    setNamespaces([])
    setPods([]); setContainers([])
    const cached = nsCache.current[clusterId]
    if (cached) { setNamespaces(cached); return }
    setNsLoading(true)
    fetchNamespaces(clusterId).then((list) => setNamespaces(list)).catch(() => setNamespaces([])).finally(() => setNsLoading(false))
  }

  const handleNamespaceChange = (ns: string) => {
    form.setFieldsValue({ pod_name: undefined })
    setPods([]); setContainers([])
    const clusterId = form.getFieldValue('cluster_id')
    const key = `${clusterId}/${ns}`
    const cached = podsCache.current[key]
    if (cached) { setPods(cached); return }
    setPodsLoading(true)
    getNamespacePods(clusterId, ns).then((d) => {
      const opts = ((d || []) as Array<{ name: string }>).map((p) => ({ label: p.name, value: p.name }))
      podsCache.current[key] = opts
      setPods(opts)
    }).finally(() => setPodsLoading(false))
  }

  const handlePodChange = (pod: string) => {
    form.setFieldsValue({ container: undefined })
    setContainers([])
    const clusterId = form.getFieldValue('cluster_id')
    const ns = form.getFieldValue('namespace')
    if (!clusterId || !ns || !pod) return
    const key = `${clusterId}/${ns}/${pod}`
    const cached = ctnCache.current[key]
    if (cached) { setContainers(cached); return }
    setCtnLoading(true)
    getPodContainers(clusterId, ns, pod)
      .then((d) => {
        const names = (d as unknown as { containers?: string[] })?.containers || []
        ctnCache.current[key] = names
        setContainers(names)
      })
      .catch(() => setContainers([]))
      .finally(() => setCtnLoading(false))
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
      setContainers([])
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

  const handleCollectToggle = async (v: boolean) => {
    if (!activeId) return
    try {
      await updateSessionCollect(activeId, { enabled: v })
      message.success(v ? '已开启定时采集' : '已关闭定时采集')
      loadSessions()
    } catch { /* 拦截器已提示 */ }
  }

  const handleCollectInterval = async (v: number) => {
    if (!activeId) return
    try {
      await updateSessionCollect(activeId, { interval_min: v })
      loadSessions()
    } catch { /* 拦截器已提示 */ }
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
      return <Paragraph style={{ margin: 0, whiteSpace: 'pre-wrap', fontSize: 15, lineHeight: 1.7 }}>{m.content}</Paragraph>
    }
    if (m.role === 'tool') {
      return (
        <Collapse size="small" ghost style={{ background: '#fff' }}>
          <Collapse.Panel
            header={<Space size={4}><ToolOutlined style={{ color: '#722ed1' }} /><Tag color="purple">{m.tool_name || '工具'}</Tag><Text type="secondary" style={{ fontSize: 13 }}>{m.tool_args}</Text></Space>}
            key="1"
          >
            <pre style={{ margin: 0, fontSize: 13, whiteSpace: 'pre-wrap', maxHeight: 300, overflow: 'auto' }}>{m.tool_output}</pre>
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
          <Text strong style={{ fontSize: 15 }}>{fa.root_cause || '（未给出明确根因）'}</Text>
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
          <Paragraph style={{ margin: '6px 0 0', background: '#fff', color: '#1f1f1f', border: '1px solid #f0f0f0', padding: 8, borderRadius: 4 }}>
            {fa.handling_advice}
          </Paragraph>
        )}
      </div>
    )
  }

  const activeSession = sessions.find((s) => s.id === activeId)

  // 开启定时采集时，自动轮询刷新会话消息
  useEffect(() => {
    if (!activeId || !activeSession?.collect_enabled) return
    const t = setInterval(() => { if (activeIdRef.current === activeId) loadMessages(activeId) }, 60000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId, activeSession?.collect_enabled])

  return (
    // 本页为浅色设计：固定浅色主题，避免深色模式下白底白字看不见
    <ConfigProvider theme={buildTheme('light')}>
    <Layout style={{ height: 'calc(100vh - 120px)', background: '#fff' }}>
      <Sider width={320} theme="light" className="agentchat-sider" style={{ borderRight: '1px solid #f0f0f0', padding: 12, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        <Button type="primary" block icon={<PlusOutlined />} onClick={() => {
          setModalOpen(true)
          if (clusters.length) prefetchNamespaces(clusters)
          else loadClusters().then((list) => prefetchNamespaces(list))
        }}>
          新建诊断会话
        </Button>
        <div style={{ marginTop: 12, overflowY: 'auto', flex: 1 }}>
          {sessions.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无会话" />}
          {sessionGroups.map(([day, items]) => {
            const open = olderOpen[day] !== undefined ? olderOpen[day] : day === todayKey
            return (
              <div key={day} style={{ marginBottom: 4 }}>
                <div
                  onClick={() => setOlderOpen((p) => ({ ...p, [day]: !open }))}
                  style={{ fontSize: 13, color: '#8c8c8c', padding: '6px 6px', cursor: 'pointer',
                           display: 'flex', justifyContent: 'space-between', userSelect: 'none' }}>
                  <span>{fmtDay(day)}</span>
                  <span>{open ? '收起' : `${items.length} 条`}</span>
                </div>
                {open && items.map((s) => (
                  <div
                    key={s.id}
                    onClick={() => openSession(s.id)}
                    style={{ cursor: 'pointer', padding: '7px 10px', borderRadius: 6, marginBottom: 2,
                             background: s.id === activeId ? '#e6f4ff' : 'transparent',
                             display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 6 }}>
                    <Text ellipsis style={{ fontSize: 14.5, flex: 1, color: '#1f1f1f' }} title={sessionName(s)}>{sessionName(s)}</Text>
                    <Popconfirm title="删除该会话？" onConfirm={(e) => { e?.stopPropagation(); handleDeleteSession(s.id) }} onCancel={(e) => e?.stopPropagation()}>
                      <DeleteOutlined style={{ color: '#ccc', fontSize: 12 }} onClick={(e) => e.stopPropagation()} />
                    </Popconfirm>
                  </div>
                ))}
              </div>
            )
          })}
        </div>
      </Sider>
      <Content style={{ display: 'flex', flexDirection: 'column', padding: '0 16px' }}>
        {!activeSession ? (
          <Empty style={{ marginTop: 120 }} description="选择或新建一个诊断会话开始" />
        ) : (
          <>
            <div style={{ padding: '12px 0', borderBottom: '1px solid #f0f0f0', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <Space>
                <Text strong style={{ fontSize: 16 }}>{sessionName(activeSession)}</Text>
                <Tag color="blue">{activeSession.cluster_id ? `集群 ${activeSession.cluster_id}` : ''}</Tag>
                <Text type="secondary" style={{ fontSize: 14 }}>{activeSession.namespace}/{activeSession.pod_name}</Text>
              </Space>
              <Space>
                <Tooltip title="针对该容器定时采集 JVM 等信息，自动追加到会话（默认关）">
                  <Space size={4}>
                    <Text type="secondary" style={{ fontSize: 13 }}>定时采集</Text>
                    <Switch size="small" checked={!!activeSession.collect_enabled} onChange={handleCollectToggle} />
                    <Select size="small" value={activeSession.collect_interval_min || 5} style={{ width: 96 }}
                      disabled={!activeSession.collect_enabled} onChange={handleCollectInterval}
                      options={[{ label: '5 分钟', value: 5 }, { label: '10 分钟', value: 10 }, { label: '30 分钟', value: 30 }, { label: '60 分钟', value: 60 }]} />
                    {activeSession.collect_last_at && <Text type="secondary" style={{ fontSize: 12 }}>上次：{String(activeSession.collect_last_at).slice(5, 16)}</Text>}
                  </Space>
                </Tooltip>
                <Tooltip title="默认开启：AI 多轮自主取证直接给诊断结果；关闭 = 平台预采集证据单轮快出结论">
                  <Space size={4}>
                    <Text type="secondary" style={{ fontSize: 13 }}>深度诊断</Text>
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
                      background: '#fff', color: '#1f1f1f', border: '1px solid #f0f0f0',
                      fontSize: 15, lineHeight: 1.7,
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
                style={{ fontSize: 15 }}
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
            <Select placeholder={nsLoading ? '加载中…' : '选择命名空间'} onChange={handleNamespaceChange}
              disabled={nsLoading || !namespaces.length} loading={nsLoading} showSearch optionFilterProp="label"
              options={namespaces.map((n) => ({ label: n, value: n }))} />
          </Form.Item>
          <Form.Item name="pod_name" label="Pod" rules={[{ required: true, message: '请选择 Pod' }]}>
            <Select placeholder={podsLoading ? '加载中…' : '选择 Pod'} showSearch optionFilterProp="label"
              disabled={podsLoading || !pods.length} loading={podsLoading} options={pods} onChange={handlePodChange} />
          </Form.Item>
          <Form.Item name="container" label="容器（可选）" tooltip="会话名会变成 环境/命名空间/服务[容器]，便于区分同一 Pod 下不同容器">
            <Select placeholder={ctnLoading ? '加载中…' : '默认容器'} allowClear disabled={ctnLoading || !containers.length} loading={ctnLoading}
              options={containers.map((c) => ({ label: c, value: c }))} />
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
    </ConfigProvider>
  )
}
