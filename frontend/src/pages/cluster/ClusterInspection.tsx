// 集群内「巡检与诊断」页（替换 ComingSoon 占位）
// 功能：报告历史列表 + 触发巡检（operator 及以上可写）+ 进度轮询 + 报告详情（Modal）
// 对接接口：inspectCluster / getInspectionReports / getClusterInspectProgress / downloadInspectionReport
import { useEffect, useMemo, useState } from 'react'
import {
  Card, Button, Table, Tag, Progress, Empty, Space, Modal, Descriptions, message, Typography,
} from 'antd'
import { ReloadOutlined, ThunderboltOutlined, DownloadOutlined, EyeOutlined } from '@ant-design/icons'
import {
  inspectCluster, getInspectionReports, getClusterInspectProgress, downloadInspectionReport,
} from '../../services/api'
import { canWrite } from '../../utils/auth'

const { Title, Text } = Typography

interface InspectItem {
  category?: string
  name?: string
  value?: string
  status?: string
  detail?: string
}

interface FaultTopItem {
  fault_type?: string
  count?: number
}

interface InspectReport {
  report_id?: number
  health?: string
  summary?: string
  inspected_at?: string
  version?: string
  items?: InspectItem[]
  stats?: Record<string, unknown>
  fault_top?: FaultTopItem[]
}

interface Progress {
  pct: number
  step: string
}

const HEALTH_COLOR: Record<string, string> = { healthy: 'green', warning: 'orange', critical: 'red' }

// 巡检项状态：后端 items[].status 用 healthy/warning，ok 布尔未在报告内 → 直接映射颜色
const ITEM_STATUS_COLOR: Record<string, string> = { healthy: 'green', warning: 'orange', critical: 'red' }

// 统计字段 → 中文标签（stats 里的 workloads 是对象，单独展开）
const STAT_LABELS: Array<{ key: string; label: string; render: (s: Record<string, unknown>) => string }> = [
  { key: 'node_ready', label: '节点就绪', render: (s) => `${s.node_ready ?? '-'} / ${s.node_total ?? '-'}` },
  { key: 'pod_running', label: 'Pod 运行', render: (s) => `${s.pod_running ?? '-'} / ${s.pod_total ?? '-'}` },
  { key: 'namespace_count', label: '命名空间', render: (s) => String(s.namespace_count ?? '-') },
  { key: 'pending_alerts', label: '待处理告警', render: (s) => String(s.pending_alerts ?? '-') },
  { key: 'awaiting_alerts', label: '审批中告警', render: (s) => String(s.awaiting_alerts ?? '-') },
]

export default function ClusterInspection({ clusterId }: { clusterId: number }) {
  const [history, setHistory] = useState<InspectReport[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [report, setReport] = useState<InspectReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [prog, setProg] = useState<Progress | null>(null)
  // 报告详情（当前报告或历史报告共用）
  const [detail, setDetail] = useState<InspectReport | null>(null)

  const writable = canWrite()

  const loadHistory = () => {
    setHistoryLoading(true)
    getInspectionReports(clusterId)
      .then((d) => setHistory((d as InspectReport[]) || []))
      .catch(() => setHistory([]))
      .finally(() => setHistoryLoading(false))
  }

  // 触发巡检：POST 巡检（同步返回报告）+ 进度轮询
  const run = () => {
    if (loading) return
    setLoading(true)
    setProg({ pct: 5, step: '准备中…' })
    const timer = window.setInterval(() => {
      getClusterInspectProgress(clusterId)
        .then((p) => { if (p) setProg({ pct: p.pct, step: p.step }) })
        .catch(() => {})
    }, 800)
    inspectCluster(clusterId)
      .then((d) => {
        setReport(d as InspectReport)
        message.success('巡检完成')
        loadHistory()
      })
      .catch((e) => {
        message.error((e as { response?: { data?: { message?: string } } })?.response?.data?.message || '巡检失败')
      })
      .finally(() => {
        window.clearInterval(timer)
        setProg(null)
        setLoading(false)
      })
  }

  const downloadReport = async (reportId: number) => {
    try {
      const blob = await downloadInspectionReport(reportId)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `cluster-inspect-${clusterId}-${reportId}.md`
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      message.error('下载失败')
    }
  }

  useEffect(() => {
    loadHistory()
  }, [clusterId]) // eslint-disable-line react-hooks/exhaustive-deps

  // 历史报告里展开 workloads 对象为可读文本
  const workloadsText = useMemo(() => {
    const w = (detail?.stats?.workloads || {}) as Record<string, unknown>
    const entries = Object.entries(w)
    if (!entries.length) return '-'
    return entries.map(([k, v]) => `${k} ${v}`).join(' / ')
  }, [detail])

  const currentItems = report?.items || []
  const detailItems = detail?.items || []

  return (
    <div>
      <Card
        size="small"
        style={{ marginBottom: 16 }}
        title={<Space><Title level={5} style={{ margin: 0 }}>巡检与诊断</Title>
          <Text type="secondary" style={{ fontSize: 12 }}>集群体检：节点 / Pod / 工作负载 / 活跃告警 / 数据新鲜度</Text>
        </Space>}
        extra={
          <Space>
            {writable && (
              <Button type="primary" icon={<ThunderboltOutlined />} loading={loading} onClick={run}>
                触发巡检
              </Button>
            )}
            <Button icon={<ReloadOutlined />} loading={historyLoading} onClick={loadHistory}>刷新历史</Button>
          </Space>
        }
      >
        {loading && prog && (
          <Card size="small" style={{ marginBottom: 12 }} styles={{ body: { padding: 12 } }}>
            <Progress percent={prog.pct} status="active" />
            <div style={{ marginTop: 6, color: '#666', fontSize: 12 }}>{prog.step}</div>
          </Card>
        )}

        {report ? (
          <Card size="small" style={{ marginBottom: 12 }} styles={{ body: { padding: 12 } }}>
            <Space direction="vertical" size={4} style={{ width: '100%' }}>
              <Space>
                <Tag color={HEALTH_COLOR[report.health || 'warning'] || 'default'}>{report.health || '-'}</Tag>
                <Text strong>{report.summary || ''}</Text>
                <Button size="small" type="link" icon={<EyeOutlined />} onClick={() => setDetail(report)}>
                  查看详情
                </Button>
                {report.report_id && (
                  <Button size="small" type="link" icon={<DownloadOutlined />} onClick={() => downloadReport(report.report_id!)}>
                    下载
                  </Button>
                )}
              </Space>
              <Text type="secondary" style={{ fontSize: 12 }}>
                巡检时间：{report.inspected_at || '-'}{report.version ? ` · 版本 ${report.version}` : ''}
              </Text>
            </Space>
          </Card>
        ) : null}

        {!report && !loading && (
          <Empty description={writable ? '点击「触发巡检」开始集群体检' : '暂无巡检报告，仅有写权限用户可触发巡检'} />
        )}
      </Card>

      <Card size="small" title="历史报告" extra={<Text type="secondary" style={{ fontSize: 12 }}>最近 {history.length} 条</Text>}>
        <Table<InspectReport>
          rowKey={(r) => String(r.report_id ?? r.inspected_at ?? Math.random())}
          size="small"
          loading={historyLoading}
          dataSource={history}
          pagination={false}
          locale={{ emptyText: '暂无巡检报告' }}
          columns={[
            { title: '时间', dataIndex: 'inspected_at', width: 170, render: (v: string) => v || '-' },
            { title: '结论', dataIndex: 'summary', ellipsis: true },
            {
              title: '健康', dataIndex: 'health', width: 90,
              render: (s: string) => <Tag color={HEALTH_COLOR[s] || 'default'}>{s || '-'}</Tag>,
            },
            {
              title: '操作', width: 140,
              render: (_: unknown, r: InspectReport) => (
                <Space size={4}>
                  <Button size="small" type="link" icon={<EyeOutlined />} onClick={() => setDetail(r)}>详情</Button>
                  <Button size="small" type="link" icon={<DownloadOutlined />}
                    onClick={() => r.report_id && downloadReport(r.report_id)}>下载</Button>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Modal
        title="巡检报告详情"
        open={!!detail}
        onCancel={() => setDetail(null)}
        footer={<Button onClick={() => setDetail(null)}>关闭</Button>}
        width={820}
      >
        {detail && (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Space>
              <Tag color={HEALTH_COLOR[detail.health || 'warning'] || 'default'}>{detail.health || '-'}</Tag>
              <Text strong>{detail.summary || '-'}</Text>
            </Space>
            <Text type="secondary" style={{ fontSize: 12 }}>
              巡检时间：{detail.inspected_at || '-'}{detail.version ? ` · 版本 ${detail.version}` : ''}
            </Text>

            <Descriptions size="small" column={3} bordered>
              {STAT_LABELS.map((s) => (
                <Descriptions.Item key={s.key} label={s.label}>
                  {s.render(detail.stats || {})}
                </Descriptions.Item>
              ))}
              <Descriptions.Item label="工作负载">{workloadsText}</Descriptions.Item>
            </Descriptions>

            <div>
              <Text strong style={{ display: 'block', marginBottom: 8 }}>体检项</Text>
              <Table<InspectItem>
                rowKey={(it, i) => `${it.name}-${i}`}
                size="small"
                pagination={false}
                dataSource={detailItems}
                locale={{ emptyText: '无体检项' }}
                columns={[
                  { title: '类别', dataIndex: 'category', width: 90 },
                  { title: '体检项', dataIndex: 'name', width: 140 },
                  { title: '结果', dataIndex: 'value', ellipsis: true },
                  {
                    title: '状态', dataIndex: 'status', width: 90,
                    render: (s: string) => <Tag color={ITEM_STATUS_COLOR[s] || 'default'}>{s || '-'}</Tag>,
                  },
                ]}
              />
            </div>

            {(detail.fault_top || []).length > 0 && (
              <div>
                <Text strong style={{ display: 'block', marginBottom: 8 }}>故障 Top</Text>
                <Space wrap>
                  {(detail.fault_top || []).map((f) => (
                    <Tag key={String(f.fault_type)} color="red">{String(f.fault_type)} ×{String(f.count)}</Tag>
                  ))}
                </Space>
              </div>
            )}
          </Space>
        )}
      </Modal>
    </div>
  )
}
