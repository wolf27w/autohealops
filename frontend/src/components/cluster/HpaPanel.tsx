// R57 弹性伸缩面板：展示指向当前工作负载的 HPA，支持创建/编辑/查看 YAML/删除
import { useCallback, useEffect, useState } from 'react'
import {
  Card, Table, Tag, Button, Space, Modal, Input, Empty, Progress, message, Descriptions, Alert, Spin,
} from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import {
  listWorkloadHpa, getHpa, deleteHpa, type HpaInfo, type HpaMetric,
} from '../../services/api'
import HpaFormModal from './HpaFormModal'

const fmtTime = (v?: string) => (v ? String(v).slice(0, 19).replace('T', ' ') : '-')

const fmtWindow = (s?: number) => {
  if (!s) return '默认'
  if (s >= 60 && s % 60 === 0) return `${s / 60} 分钟`
  return `${s} 秒`
}

function metricTarget(m: HpaMetric): string {
  if (m.target_type === 'Utilization') return `${m.target_utilization ?? '-'}%`
  return m.target_value || '-'
}

function metricCurrent(m: HpaMetric) {
  if (m.current_available === false) return <span style={{ color: '#bbb' }}>unknown</span>
  if (m.target_type === 'Utilization') return m.current_utilization != null ? `${m.current_utilization}%` : '-'
  return m.current_value || '-'
}

function metricPercent(m: HpaMetric): number | null {
  if (m.current_available === false) return null
  if (m.target_type === 'Utilization') {
    if (!m.target_utilization || m.current_utilization == null) return null
    return Math.round((m.current_utilization / m.target_utilization) * 100)
  }
  const tv = parseFloat(m.target_value || '')
  const cv = parseFloat(m.current_value || '')
  if (!tv || Number.isNaN(cv)) return null
  return Math.round((cv / tv) * 100)
}

function condColor(c: string): string {
  if (c.includes('=False') || c.includes('Failed')) return 'red'
  if (c.includes('=True')) return 'green'
  return 'default'
}

export default function HpaPanel(props: { clusterId: number; kind: string; namespace: string; name: string }) {
  const { clusterId, kind, namespace, name } = props
  const [items, setItems] = useState<HpaInfo[]>([])
  const [loading, setLoading] = useState(false)
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<HpaInfo | null>(null)
  const [yamlTarget, setYamlTarget] = useState<HpaInfo | null>(null)

  const scalable = kind === 'deployment' || kind === 'statefulset'

  const load = useCallback(async () => {
    try {
      const d = await listWorkloadHpa(clusterId, namespace, kind, name)
      setItems(d?.items || [])
    } catch {
      setItems([])
    }
  }, [clusterId, namespace, kind, name])

  // 进入即加载 + 8s 轮询（页面隐藏时跳过）；组件卸载清理
  useEffect(() => {
    load()
    const t = setInterval(() => {
      if (!document.hidden) load()
    }, 8000)
    return () => clearInterval(t)
  }, [load])

  const refresh = async () => {
    setLoading(true)
    await load()
    setLoading(false)
  }

  const handleDelete = (hpa: HpaInfo) => {
    Modal.confirm({
      title: `删除 HPA ${hpa.name}？`,
      content: '删除后副本将冻结在当前值，不会自动缩回。',
      okText: '删除',
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await deleteHpa(clusterId, hpa.namespace, hpa.name)
          message.success('已删除')
          load()
        } catch (e) {
          message.error(errMsg(e))
        }
      },
    })
  }

  const metricColumns = [
    {
      title: '指标',
      key: 'name',
      render: (_: unknown, m: HpaMetric) => (
        <Space size={6}><Tag color="geekblue">{m.type}</Tag><code>{m.name || '-'}</code></Space>
      ),
    },
    { title: '目标', key: 'target', render: (_: unknown, m: HpaMetric) => metricTarget(m) },
    { title: '当前', key: 'current', render: (_: unknown, m: HpaMetric) => metricCurrent(m) },
    {
      title: '进度',
      key: 'progress',
      width: 180,
      render: (_: unknown, m: HpaMetric) => {
        const p = metricPercent(m)
        if (p == null) return <span style={{ color: '#bbb' }}>-</span>
        return (
          <Progress
            percent={Math.min(p, 100)}
            size="small"
            strokeColor={p > 100 ? '#ff4d4f' : undefined}
            format={() => `${p}%`}
          />
        )
      },
    },
  ]

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
        <Space wrap>
          <span style={{ fontWeight: 600 }}>弹性伸缩（HPA）</span>
        </Space>
        <Space>
          {scalable && (
            <Button type="primary" onClick={() => { setEditing(null); setFormOpen(true) }}>创建 HPA</Button>
          )}
          <Button icon={<ReloadOutlined />} loading={loading} onClick={refresh}>刷新</Button>
        </Space>
      </div>

      {items.length === 0
        ? <Empty description="该工作负载暂无 HPA" />
        : items.map((hpa) => (
          <Card
            key={`${hpa.namespace}/${hpa.name}`}
            size="small"
            style={{ marginBottom: 12 }}
            title={(
              <Space size={8} wrap>
                <span style={{ fontWeight: 700 }}>{hpa.name}</span>
                <Tag color="blue">当前 {hpa.current_replicas} / 期望 {hpa.desired_replicas}</Tag>
              </Space>
            )}
            extra={(
              <Space>
                <Button size="small" onClick={() => { setEditing(hpa); setFormOpen(true) }}>编辑</Button>
                <Button size="small" onClick={() => setYamlTarget(hpa)}>编辑 YAML</Button>
                <Button size="small" danger onClick={() => handleDelete(hpa)}>删除</Button>
              </Space>
            )}
          >
            <Descriptions size="small" column={2} style={{ marginBottom: 8 }}>
              <Descriptions.Item label="副本范围">{hpa.min_replicas} ~ {hpa.max_replicas}</Descriptions.Item>
              <Descriptions.Item label="最近扩缩容">{hpa.last_scale_time ? fmtTime(hpa.last_scale_time) : '从未'}</Descriptions.Item>
            </Descriptions>

            {hpa.metrics?.length ? (
              <Table
                rowKey={(m, i) => `${m.type}-${m.name}-${i}`}
                size="small"
                pagination={false}
                dataSource={hpa.metrics}
                columns={metricColumns}
                style={{ marginBottom: 8 }}
              />
            ) : (
              <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 8 }}>暂无指标</div>
            )}

            {hpa.conditions?.length ? (
              <Space size={4} wrap style={{ marginBottom: 8 }}>
                {hpa.conditions.map((c) => <Tag key={c} color={condColor(c)}>{c}</Tag>)}
              </Space>
            ) : null}

            <Space size={12} wrap style={{ fontSize: 12, color: '#8c8c8c' }}>
              <span>扩容稳定窗口：{fmtWindow(hpa.scale_up_stabilization_seconds)}</span>
              <span>缩容稳定窗口：{fmtWindow(hpa.scale_down_stabilization_seconds)}</span>
            </Space>
          </Card>
        ))}

      <HpaFormModal
        open={formOpen}
        clusterId={clusterId}
        kind={kind}
        namespace={namespace}
        workloadName={name}
        editing={editing}
        onClose={() => setFormOpen(false)}
        onSaved={load}
      />
      <HpaYamlModal hpa={yamlTarget} clusterId={clusterId} onClose={() => setYamlTarget(null)} />
    </div>
  )
}

function errMsg(e: unknown): string {
  const a = e as { response?: { data?: { message?: string; detail?: string } }; message?: string }
  return a?.response?.data?.message || a?.response?.data?.detail || a?.message || String(e)
}

// HPA YAML 只读查看弹窗（本轮不做 YAML 写回，写回统一走表单）
function HpaYamlModal({ hpa, clusterId, onClose }: { hpa: HpaInfo | null; clusterId: number; onClose: () => void }) {
  const [yaml, setYaml] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!hpa) return
    setLoading(true)
    setYaml('')
    getHpa(clusterId, hpa.namespace, hpa.name)
      .then((d) => setYaml(d?.yaml || ''))
      .catch(() => message.error('YAML 获取失败'))
      .finally(() => setLoading(false))
  }, [hpa, clusterId])

  const copy = async () => {
    if (!yaml) return
    try {
      await navigator.clipboard.writeText(yaml)
      message.success('已复制到剪贴板')
    } catch {
      message.error('复制失败')
    }
  }

  if (!hpa) return null
  return (
    <Modal
      title={`HPA YAML：${hpa.name}`}
      open={!!hpa}
      onCancel={onClose}
      width={900}
      footer={[
        <Button key="copy" onClick={copy}>复制</Button>,
        <Button key="close" type="primary" onClick={onClose}>关闭</Button>,
      ]}
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 8 }}
        message="此处仅供查看 HPA 的 YAML。本轮不支持 YAML 直接写回，如需修改请使用「编辑」通过表单提交。"
      />
      <Spin spinning={loading}>
        <Input.TextArea value={yaml} readOnly rows={20} style={{ fontFamily: 'monospace', fontSize: 12 }} />
      </Spin>
    </Modal>
  )
}
