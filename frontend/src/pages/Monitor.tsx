// 监控下钻页（A1）：集群 Prometheus 数据源 —— 节点 / 工作负载(Pod) / 容器 × CPU/内存/网络/磁盘
// 数据源：GET /monitoring/metrics（指标目录）+ /clusters/{id}/metrics/{m}（即时）+ .../range（趋势）
import { useEffect, useMemo, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Card, Col, Empty, Row, Segmented, Select, Space, Spin, Table, Tag } from 'antd'
import { Line } from '@ant-design/charts'
import {
  getClusters, getMonitoringMetrics, getClusterMetric, getClusterMetricRange,
} from '../services/api'
import { useThemeMode } from '../utils/theme'
import type { Cluster } from '../types'

const RANGES = [
  { label: '10分钟', value: 10 },
  { label: '30分钟', value: 30 },
  { label: '1小时', value: 60 },
  { label: '6小时', value: 360 },
  { label: '24小时', value: 1440 },
]
const SCOPE_LABEL: Record<string, string> = { node: '节点', pod: '工作负载/Pod', container: '容器' }

interface RowItem { key: number; name: string; value: number | null }
interface Point { t: number; v: number; s: string }

function seriesName(m: Record<string, string>): string {
  const parts = [m.pod || m.instance, m.container, m.namespace].filter(Boolean)
  return parts.length ? parts.join(' / ') : (m.__name__ || 'value')
}

export default function Monitor({ clusterId: fixedClusterId }: { clusterId?: number }) {
  const { mode } = useThemeMode()
  const isDark = mode === 'dark'
  const chartTheme = isDark ? ({ theme: 'classicDark' } as const) : {}

  const [clusterId, setClusterId] = useState<number | undefined>(fixedClusterId)
  const [metric, setMetric] = useState<string>('')
  const [minutes, setMinutes] = useState(60)

  const { data: clusters } = useQuery({ queryKey: ['monitor-clusters'], queryFn: () => getClusters() })
  useEffect(() => {
    if (fixedClusterId || clusterId || !clusters?.length) return
    setClusterId((clusters as Cluster[])[0].id)
  }, [clusters, clusterId, fixedClusterId])

  const { data: metrics } = useQuery({ queryKey: ['monitor-metrics'], queryFn: () => getMonitoringMetrics() })
  useEffect(() => { if (!metric && metrics?.length) setMetric(metrics[0].metric) }, [metrics, metric])

  const cur = metrics?.find((m) => m.metric === metric)
  const enabled = !!clusterId && !!metric

  const { data: instant, isFetching } = useQuery({
    queryKey: ['monitor-instant', clusterId, metric],
    queryFn: () => getClusterMetric(clusterId!, metric),
    enabled, refetchInterval: 15000, placeholderData: keepPreviousData,
  })

  const step = minutes <= 30 ? 15 : minutes <= 360 ? 60 : 300
  const { data: range } = useQuery({
    queryKey: ['monitor-range', clusterId, metric, minutes],
    queryFn: () => getClusterMetricRange(clusterId!, metric, minutes, step),
    enabled, placeholderData: keepPreviousData,
  })

  const label = instant?.label || cur?.label || metric || '指标'
  const unit = instant?.unit || cur?.unit || ''
  const rows: RowItem[] = (instant?.items || []).map((it, i) => ({ key: i, name: seriesName(it.metric), value: it.value }))

  const chartData = useMemo<Point[]>(() => {
    const out: Point[] = []
    ;(range?.series || []).slice(0, 8).forEach((s) => {
      const nm = seriesName(s.metric)
      s.values.forEach(([ts, v]) => out.push({ t: ts * 1000, v, s: nm }))
    })
    return out
  }, [range])

  const valFmt = (v: number | null) => (v == null ? '-' : Math.abs(v) >= 1000 ? v.toFixed(1) : v.toFixed(2))

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <Space wrap>
        {!fixedClusterId && (
          <Select style={{ width: 180 }} placeholder="集群" value={clusterId}
            onChange={setClusterId}
            options={(clusters || []).map((c) => ({ label: c.name, value: c.id }))} />
        )}
        <Select style={{ width: 260 }} placeholder="指标" value={metric || undefined} onChange={setMetric}
          options={(metrics || []).map((m) => ({ label: `[${SCOPE_LABEL[m.scope] || m.scope}] ${m.label}`, value: m.metric }))} />
        <Segmented value={minutes} onChange={(v) => setMinutes(v as number)} options={RANGES} />
        <Tag color="blue">数据源：集群 Prometheus</Tag>
      </Space>
      <Row gutter={[12, 12]}>
        <Col xs={24} lg={10}>
          <Card size="small" title={`实时 Top（${label}${unit ? ` · ${unit}` : ''}）`}
            extra={isFetching ? <Spin size="small" /> : null}
            styles={{ body: { maxHeight: 420, overflow: 'auto' } }}>
            {rows.length ? (
              <Table size="small" pagination={false} dataSource={rows} columns={[
                { title: '对象', dataIndex: 'name' },
                { title: `值${unit ? `(${unit})` : ''}`, dataIndex: 'value', width: 130, align: 'right' as const, render: valFmt },
              ]} />
            ) : <Empty description="无数据（集群 Prometheus 未接入或无该指标）" />}
          </Card>
        </Col>
        <Col xs={24} lg={14}>
          <Card size="small" title={`趋势（最近 ${RANGES.find((r) => r.value === minutes)?.label || ''}）`}>
            {chartData.length ? (
              <Line data={chartData} xField="t" yField="v" colorField="s" height={380} smooth {...chartTheme}
                axis={{ x: { labelFormatter: (v: number) => new Date(v).toLocaleTimeString('zh-CN', { hour12: false }) } }}
                legend={{ color: { position: 'top' } }} />
            ) : <Empty description="无趋势数据" />}
          </Card>
        </Col>
      </Row>
    </div>
  )
}
