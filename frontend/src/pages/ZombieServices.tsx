// R20 僵尸服务检测（只读）：跑起来但长期没人访问的 Deployment
import { useEffect, useState } from 'react'
import {
  Alert, Button, Card, Col, Descriptions, Form, Input, InputNumber, Modal, Row, Select, Space, Statistic,
  Table, Tag, Tooltip, message,
} from 'antd'
import { ReloadOutlined, SearchOutlined, SettingOutlined } from '@ant-design/icons'
import { getClusters, scanZombieServices, getZombieConfig, updateZombieConfig } from '../services/api'
import type { ZombieItem, ZombieScanResult } from '../services/api'
import type { Cluster } from '../types'

const STATUS: Record<string, { color: string; label: string }> = {
  zombie: { color: 'red', label: '疑似僵尸' },
  active: { color: 'green', label: '有流量' },
  unknown: { color: 'default', label: '无法判定' },
  unmapped: { color: 'orange', label: '未归因' },
}

export default function ZombieServices({ fixedClusterId, hideClusterSelect }: {
  /** 集群内页面固定集群（不再让用户切） */
  fixedClusterId?: number
  hideClusterSelect?: boolean
} = {}) {
  const [clusters, setClusters] = useState<Cluster[]>([])
  const [clusterId, setClusterId] = useState<number | undefined>(fixedClusterId)
  const [namespace, setNamespace] = useState('')
  const [days, setDays] = useState(7)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<ZombieScanResult | null>(null)
  const [cfgOpen, setCfgOpen] = useState(false)
  const [cfg, setCfg] = useState<Record<string, unknown>>({})
  const [cfgText, setCfgText] = useState('')

  useEffect(() => {
    if (fixedClusterId) setClusterId(fixedClusterId)
  }, [fixedClusterId])

  useEffect(() => {
    getClusters().then((d) => {
      const list = (d || []) as Cluster[]
      setClusters(fixedClusterId ? list.filter((c) => c.id === fixedClusterId) : list)
      if (fixedClusterId) setClusterId(fixedClusterId)
      else if (d && d.length && !clusterId) setClusterId(d[0].id)
    }).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const scan = async () => {
    if (!clusterId) { message.warning('请选择集群'); return }
    setLoading(true)
    try {
      setResult(await scanZombieServices({ cluster_id: clusterId, namespace: namespace.trim() || undefined, days }))
    } catch { /* 拦截器已提示 */ } finally { setLoading(false) }
  }

  const openConfig = async () => {
    try {
      const d = await getZombieConfig()
      setCfg(d.config || {})
      setCfgText(JSON.stringify(d.config || {}, null, 2))
      setCfgOpen(true)
    } catch { /* ignore */ }
  }

  const saveConfig = async () => {
    try {
      const parsed = JSON.parse(cfgText)
      const r = await updateZombieConfig(parsed)
      setCfg(r && typeof r === 'object' ? (r as Record<string, unknown>) : parsed)
      message.success('配置已保存')
      setCfgOpen(false)
    } catch (e) {
      message.error((e as Error).message.includes('JSON') ? 'JSON 格式不正确' : (e as Error).message)
    }
  }

  const s = result?.summary
  const items = result?.items || []

  return (
    <div>
      <Card
        size="small"
        title="僵尸服务检测（只读）"
        extra={<Button icon={<SettingOutlined />} onClick={openConfig}>检测配置</Button>}
      >
        <Space wrap style={{ marginBottom: 12 }}>
          {!hideClusterSelect && (
            <Select
              style={{ width: 200 }} placeholder="选择集群" value={clusterId} onChange={setClusterId}
              options={clusters.map((c) => ({ value: c.id, label: c.name }))}
              showSearch optionFilterProp="label"
            />
          )}
          <Input
            style={{ width: 180 }} placeholder="namespace（空=全部）" value={namespace}
            onChange={(e) => setNamespace(e.target.value)} allowClear
          />
          <Select style={{ width: 120 }} value={days} onChange={setDays}
            options={[1, 3, 7, 14, 30].map((v) => ({ value: v, label: `近 ${v} 天` }))} />
          <Button type="primary" icon={<SearchOutlined />} loading={loading} onClick={scan}>开始检测</Button>
          <Button icon={<ReloadOutlined />} onClick={scan} disabled={!result}>重新检测</Button>
        </Space>

        <Alert
          type="info" showIcon style={{ marginBottom: 12 }}
          message="判定口径：运行中且副本 Ready 的 Deployment，统计窗口内外部请求数 ≈ 0（排除 kubelet 探针与健康检查路径）。数据源优先用 ingress 指标，其次 nginx 访问日志；两者都没有时标「无法判定」，不会误报。"
        />

        {s && (
          <>
            <Row gutter={16} style={{ marginBottom: 12 }}>
              <Col span={4}><Statistic title="检测 Deployment" value={s.deployments} /></Col>
              <Col span={4}><Statistic title="疑似僵尸" value={s.zombie} valueStyle={{ color: s.zombie ? '#cf1322' : undefined }} /></Col>
              <Col span={4}><Statistic title="有流量" value={s.active} valueStyle={{ color: '#3f8600' }} /></Col>
              <Col span={4}><Statistic title="无法判定" value={s.unknown} /></Col>
              <Col span={4}><Statistic title="未归因" value={s.unmapped} /></Col>
              <Col span={4}><Statistic title="窗口" value={result.days} suffix="天" /></Col>
            </Row>
            <Descriptions size="small" column={2} style={{ marginBottom: 12 }}>
              <Descriptions.Item label="数据源">
                {result.source === 'prometheus' ? <Tag color="blue">Ingress 指标</Tag>
                  : result.source === 'nginx_log' ? <Tag color="purple">nginx 访问日志</Tag>
                  : <Tag color="default">无（无法判定）</Tag>}
                <span style={{ color: '#8c8c8c', marginLeft: 8 }}>{result.source_note}</span>
              </Descriptions.Item>
              <Descriptions.Item label="检测时间">{result.scanned_at}</Descriptions.Item>
            </Descriptions>
          </>
        )}

        {result && (
          <Table<ZombieItem>
            rowKey={(r) => `${r.namespace}/${r.deployment}`}
            size="small"
            dataSource={items}
            pagination={{ pageSize: 20, showSizeChanger: false }}
            columns={[
              { title: 'Namespace', dataIndex: 'namespace', width: 160 },
              { title: 'Deployment', dataIndex: 'deployment', width: 240, ellipsis: true },
              { title: '副本', width: 90, render: (_: unknown, r) => `${r.ready_replicas ?? 0}/${r.replicas ?? 0}` },
              {
                title: '窗口内请求', width: 110,
                render: (_: unknown, r) => (r.requests === null ? <span style={{ color: '#999' }}>—</span> : r.requests),
              },
              {
                title: '判定', width: 110,
                render: (_: unknown, r) => <Tag color={STATUS[r.status]?.color}>{STATUS[r.status]?.label || r.status}</Tag>,
              },
              {
                title: '依据', ellipsis: true,
                render: (_: unknown, r) => <Tooltip title={r.evidence}><span>{r.evidence}</span></Tooltip>,
              },
            ]}
          />
        )}
      </Card>

      <Modal
        title="僵尸服务检测配置" open={cfgOpen} onCancel={() => setCfgOpen(false)} onOk={saveConfig}
        width={720} okText="保存"
      >
        <Alert
          type="warning" showIcon style={{ marginBottom: 12 }}
          message="JSON 配置：days（窗口天数）/ max_requests（阈值，≤该值算僵尸）/ nginx_pod_keywords / exclude_namespaces / exclude_paths / exclude_ua / log_source_id / es_max_docs"
        />
        <Input.TextArea
          rows={16} value={cfgText} onChange={(e) => setCfgText(e.target.value)}
          style={{ fontFamily: 'monospace', fontSize: 12 }}
          placeholder={JSON.stringify(cfg, null, 2)}
        />
      </Modal>
    </div>
  )
}
