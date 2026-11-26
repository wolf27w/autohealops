// 慢 SQL 执行详情抽屉：概览（执行信息 + 完整 SQL）/ 执行计划 / 索引（AI 诊断走列表「诊断」按钮）
// 2026-11-26（老板）：详情只展示事实（概览/完整 SQL/执行计划/索引），不做 AI 诊断——
import { useEffect, useMemo, useState } from 'react'
import {
  Drawer, Tabs, Spin, Tag, Descriptions, Space, List, Table, Card, Button, Typography, Empty, message,
} from 'antd'
import { CopyOutlined, ThunderboltOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import {
  inspectSlowSQL,
  type SlowSqlDiagnoseData,
  type SlowSqlEventItem,
} from '../../services/api'

const { Paragraph, Text } = Typography

const VERDICT_META: Record<string, { color: string; label: string }> = {
  optimizable: { color: 'green', label: '可优化' },
  maybe: { color: 'orange', label: '可能可优化' },
  hard: { color: 'default', label: '优化空间有限' },
}
const RISK_META: Record<string, { color: string; label: string }> = {
  low: { color: 'green', label: '低' },
  medium: { color: 'orange', label: '中' },
  high: { color: 'red', label: '高' },
}
const SEVERITY_COLOR: Record<string, string> = {
  high: 'red', medium: 'orange', low: 'gold', warning: 'orange', error: 'red', info: 'blue',
}

function copyText(text: string) {
  if (!text) return
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text)
      .then(() => message.success('已复制'))
      .catch(() => message.error('复制失败'))
  } else {
    message.error('当前环境不支持复制')
  }
}

function CopyButton({ text, label = '复制' }: { text: string; label?: string }) {
  return <Button size="small" icon={<CopyOutlined />} onClick={() => copyText(text)}>{label}</Button>
}

function fmtNum(v: number | null | undefined, digits?: number): string {
  if (v == null) return '-'
  return digits != null ? v.toFixed(digits) : String(v)
}

interface Props {
  connId: number
  /** 被查看的执行记录；null = 关闭抽屉 */
  row: SlowSqlEventItem | null
  onClose: () => void
}

export default function SlowSqlDetailDrawer({ connId, row, onClose }: Props) {
  const open = !!row
  const digest = row?.digest ?? null

  const [data, setData] = useState<SlowSqlDiagnoseData | null>(null)
  const [loading, setLoading] = useState(false)
  // 2026-11-26（老板）：详情 = 事实（SQL/执行计划/索引/参数体检），不做 AI 诊断；
  // AI 诊断走列表行的「诊断」按钮，两者分开。详情只跑快速体检（不调 AI，实测 ~0.5s）。
  useEffect(() => {
    if (!open) { setData(null); setLoading(false); return }
    if (!digest) { setData(null); setLoading(false); return }
    setLoading(true)
    setData(null)
    inspectSlowSQL(connId, digest, { sql_text: row?.sql_text, schema_name: row?.schema_name })
      .then(setData)
      .catch(() => {
        setData(null)
        message.error('加载执行计划/索引失败，请稍后重试')
      })
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, digest, connId])

  // 执行计划动态列（取 rows[0] 的 key）
  const explainColumns = useMemo(() => {
    if (!data?.explain?.rows?.length) return []
    const keys = Object.keys(data.explain.rows[0])
    return keys.map((k) => ({
      title: k, dataIndex: k, key: k, ellipsis: true,
      render: (v: unknown) => {
        const s = v == null ? '' : String(v)
        let color: string | undefined
        if (k === 'type' && /ALL/i.test(s)) color = '#cf1322'
        else if (k === 'Extra' && /filesort|temporary/i.test(s)) color = '#d46b08'
        return color ? <span style={{ color }}>{s}</span> : s
      },
    }))
  }, [data])

  const indexColumns = [
    { title: '表', dataIndex: 'table', ellipsis: true },
    { title: '索引名', dataIndex: 'index', ellipsis: true },
    { title: '列', dataIndex: 'columns', ellipsis: true },
    { title: '唯一', dataIndex: 'unique', width: 70, render: (v: boolean) => (v ? <Tag color="blue">唯一</Tag> : '-') },
  ]

  const verdictMeta = VERDICT_META[data?.verdict ?? ''] || { color: 'default', label: '' }
  const riskMeta = RISK_META[data?.risk_level ?? ''] || { color: 'default', label: '-' }

  const overview = (
    <div>
      <Descriptions size="small" column={2} bordered style={{ marginBottom: 12 }}>
        <Descriptions.Item label="执行开始时间">{row?.start_time ? dayjs(row.start_time).format('YYYY-MM-DD HH:mm:ss') : '-'}</Descriptions.Item>
        <Descriptions.Item label="执行耗时(秒)">{fmtNum(row?.exec_seconds, 2)}</Descriptions.Item>
        <Descriptions.Item label="锁等待耗时(秒)">{fmtNum(row?.lock_seconds, 2)}</Descriptions.Item>
        <Descriptions.Item label="返回行数">{fmtNum(row?.rows_sent)}</Descriptions.Item>
        <Descriptions.Item label="扫描行数">{fmtNum(row?.rows_examined)}</Descriptions.Item>
        <Descriptions.Item label="访问来源">{row?.client_host || '-'}</Descriptions.Item>
        <Descriptions.Item label="数据库用户">{row?.db_user || '-'}</Descriptions.Item>
        <Descriptions.Item label="线程 ID">{fmtNum(row?.thread_id)}</Descriptions.Item>
        <Descriptions.Item label="数据库">{row?.schema_name || '-'}</Descriptions.Item>
        <Descriptions.Item label="表名">{row?.tables || '-'}</Descriptions.Item>
        <Descriptions.Item label="标签">{row?.tag || '-'}</Descriptions.Item>
        <Descriptions.Item label="SQL ID" span={2}>{row?.digest || '-'}</Descriptions.Item>
      </Descriptions>
      <Card size="small" title={
        <Space wrap>
          <Text strong style={{ fontSize: 13 }}>完整 SQL</Text>
          {/* 2026-11-26（老板）：要能看出这条 SQL 是不是完整的 */}
          {row?.sql_truncated
            ? <Tag color="orange">疑似被截断 · {(row?.sql_len ?? (row?.sql_text || '').trim().length)} 字符</Tag>
            : <Tag color="green">完整 · {(row?.sql_len ?? (row?.sql_text || '').trim().length)} 字符</Tag>}
          <CopyButton text={row?.sql_text || ''} label="复制 SQL" />
        </Space>
      }>
        <pre style={{
          margin: 0, fontSize: 12, lineHeight: '18px', whiteSpace: 'pre-wrap', wordBreak: 'break-all',
          maxHeight: 360, overflow: 'auto',
        }}>{row?.sql_text || '-'}</pre>
        {row?.sql_truncated && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            说明：MySQL performance_schema 的 DIGEST_TEXT 只保留 1024 字符，原文更长时会在此处被硬截断；
            需要完整语句请在「诊断」里粘贴完整 SQL 重新分析。
          </Text>
        )}
      </Card>
    </div>
  )

  const explainTab = (
    <Card size="small" title="执行计划" styles={{ body: { padding: 12 } }}>
      {data?.explain?.available
        ? (data.explain.rows?.length
          ? <Table size="small" rowKey={(_, i) => i as number} columns={explainColumns} dataSource={data.explain.rows}
              pagination={false} scroll={{ x: 'max-content' }} />
          : (data.explain.text
            ? <pre style={{ whiteSpace: 'pre-wrap', margin: 0, fontSize: 12 }}>{data.explain.text}</pre>
            : <Text type="secondary">未取到执行计划明细</Text>))
        : <Text type="secondary">{data?.explain?.error || '未取到执行计划（可能无 EXPLAIN 权限或实例类型不支持）'}</Text>}
    </Card>
  )

  const indexTab = (
    <Card size="small" title="索引现状" styles={{ body: { padding: 12 } }}>
      {data?.indexes?.length
        ? <Table size="small" rowKey={(r) => `${r.table}:${r.index}`} columns={indexColumns}
            dataSource={data.indexes} pagination={false} scroll={{ x: 'max-content' }} />
        : <Text type="secondary">未取到相关表索引信息</Text>}
    </Card>
  )

  const items = [
    { key: 'overview', label: '概览', children: overview },
    { key: 'explain', label: '执行计划', children: loading ? <Spin /> : (data ? explainTab : <Empty description="暂无执行计划" />) },
    { key: 'index', label: '索引', children: loading ? <Spin /> : (data ? indexTab : <Empty description="暂无索引信息" />) },
  ]

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width={900}
      title={`慢 SQL 执行详情（digest ${digest?.slice(0, 16) ?? ''}${digest ? '…' : ''}）`}
      destroyOnClose={false}
    >
      {loading && (
        <div style={{ textAlign: 'center', padding: '48px 0' }}>
          <Spin size="large" />
          <div style={{ marginTop: 12, color: '#8c8c8c' }}>正在读取执行计划与索引…</div>
        </div>
      )}
      {!loading && <Tabs defaultActiveKey="overview" items={items} />}
    </Drawer>
  )
}
