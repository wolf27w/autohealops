// 慢 SQL 诊断弹窗：调用后端 AI 诊断接口，展示结论 / 执行计划 / 索引现状 / 优化建议
import { useEffect, useMemo, useState } from 'react'
import {
  Modal, Spin, Tag, Descriptions, Space, List, Table, Card, Button, Typography, Collapse, Empty, message,
} from 'antd'
import { CopyOutlined } from '@ant-design/icons'
import {
  diagnoseSlowSQL,
  type SlowSqlDiagnoseData,
} from '../../services/api'

const { Paragraph, Text } = Typography

// 结论标签（verdict → 颜色 / 兜底文案）
const VERDICT_META: Record<string, { color: string; label: string }> = {
  optimizable: { color: 'green', label: '可优化' },
  maybe: { color: 'orange', label: '可能可优化' },
  hard: { color: 'default', label: '优化空间有限' },
}
// 变更风险（risk_level → 颜色 / 文案）
const RISK_META: Record<string, { color: string; label: string }> = {
  low: { color: 'green', label: '低' },
  medium: { color: 'orange', label: '中' },
  high: { color: 'red', label: '高' },
}
// 问题 severity → Tag 颜色
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

// 复制按钮（放在代码块右上）
function CopyButton({ text, label = '复制 SQL' }: { text: string; label?: string }) {
  return (
    <Button size="small" icon={<CopyOutlined />} onClick={() => copyText(text)} style={{ float: 'right' }}>
      {label}
    </Button>
  )
}

// 建议卡片里的代码块（DDL / 优化后 SQL）
function SqlBlock({ title, sql }: { title: string; sql: string }) {
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ marginBottom: 4, color: '#595959', fontSize: 12 }}>
        {title} <CopyButton text={sql} />
      </div>
      <pre style={{
        background: '#f6f8fa', border: '1px solid #e8e8e8', borderRadius: 4,
        padding: 10, fontSize: 12, overflowX: 'auto', whiteSpace: 'pre', margin: 0,
      }}>{sql}</pre>
    </div>
  )
}

interface Props {
  connId: number
  /** 被诊断的行；null = 关闭弹窗 */
  row: { digest?: string; sql_text?: string; schema_name?: string } | null
  onClose: () => void
}

export default function SlowSqlDiagnoseModal({ connId, row, onClose }: Props) {
  const open = !!row
  const digest = row?.digest ?? null

  // 按 digest 缓存已加载结果（关闭弹窗不清缓存）
  const [cache, setCache] = useState<Record<string, SlowSqlDiagnoseData>>({})
  const [data, setData] = useState<SlowSqlDiagnoseData | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!open || !digest) return
    if (cache[digest]) {
      setData(cache[digest])
      setLoading(false)
      return
    }
    setLoading(true)
    setData(null)
    diagnoseSlowSQL(connId, digest, { sql_text: row?.sql_text, schema_name: row?.schema_name })
      .then((d) => {
        setCache((c) => ({ ...c, [digest]: d }))
        setData(d)
      })
      .catch(() => setData(null)) // 403/500 已由拦截器 message.error 提示
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, digest, connId])

  // 执行计划动态列（取 rows[0] 的 key）
  const explainColumns = useMemo(() => {
    if (!data?.explain?.rows?.length) return []
    const keys = Object.keys(data.explain.rows[0])
    return keys.map((k) => ({
      title: k,
      dataIndex: k,
      key: k,
      ellipsis: true,
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

  return (
    <Modal
      open={open}
      onCancel={onClose}
      width={960}
      title={`慢 SQL 诊断（digest ${digest?.slice(0, 16) ?? ''}…）`}
      footer={<Button onClick={onClose}>关闭</Button>}
      destroyOnClose={false}
    >
      {loading && (
        <div style={{ textAlign: 'center', padding: '48px 0' }}>
          <Spin size="large" />
          <div style={{ marginTop: 12, color: '#8c8c8c' }}>正在读取执行计划并分析…</div>
        </div>
      )}

      {!loading && !data && (
        <Empty description="诊断失败或暂无结果，请稍后重试" style={{ padding: '24px 0' }} />
      )}

      {!loading && data && (
        <div>
          {/* 结论区 */}
          <Space wrap style={{ marginBottom: 12 }}>
            <Tag color={verdictMeta.color}>{data.verdict_label || verdictMeta.label}</Tag>
            <Tag color={riskMeta.color}>变更风险 {riskMeta.label}</Tag>
            {data.model ? <Text type="secondary" style={{ fontSize: 12 }}>模型：{data.model}</Text> : null}
          </Space>

          <Descriptions size="small" column={4} bordered style={{ marginBottom: 12 }}>
            <Descriptions.Item label="库">{data.stats?.schema_name || '-'}</Descriptions.Item>
            <Descriptions.Item label="平均耗时(ms)">{data.stats?.avg_latency_ms ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="最大(ms)">{data.stats?.max_latency_ms ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="执行次数">{data.stats?.exec_count ?? '-'}</Descriptions.Item>
          </Descriptions>

          {data.summary && (
            <Card size="small" style={{ marginBottom: 12 }} styles={{ body: { padding: '8px 12px' } }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <Text strong style={{ fontSize: 13 }}>诊断结论</Text>
                <Button size="small" icon={<CopyOutlined />} onClick={() => copyText(data.summary)}>复制</Button>
              </div>
              <Paragraph style={{ margin: '8px 0 0', whiteSpace: 'pre-wrap' }}>{data.summary}</Paragraph>
            </Card>
          )}

          {/* 问题点 */}
          {!!data.issues?.length && (
            <Card size="small" title="问题点" style={{ marginBottom: 12 }}>
              <List
                size="small"
                dataSource={data.issues}
                renderItem={(it) => (
                  <List.Item style={{ alignItems: 'flex-start' }}>
                    <Space direction="vertical" size={2} style={{ width: '100%' }}>
                      <Space size={6}>
                        <Text strong>{it.type}</Text>
                        {it.severity ? <Tag color={SEVERITY_COLOR[it.severity] || 'default'}>{it.severity}</Tag> : null}
                      </Space>
                      <Text type="secondary" style={{ whiteSpace: 'pre-wrap' }}>{it.detail}</Text>
                    </Space>
                  </List.Item>
                )}
              />
            </Card>
          )}

          {/* 执行计划 */}
          <Card size="small" title="执行计划" style={{ marginBottom: 12 }}>
            {data.explain?.available
              ? (data.explain.rows?.length
                ? <Table size="small" rowKey={(_, i) => i as number} columns={explainColumns} dataSource={data.explain.rows}
                    pagination={false} scroll={{ x: 'max-content' }} />
                : (data.explain.text
                  ? <pre style={{ whiteSpace: 'pre-wrap', margin: 0, fontSize: 12 }}>{data.explain.text}</pre>
                  : <Text type="secondary">未取到执行计划明细</Text>))
              : <Text type="secondary">{data.explain?.error || '未取到执行计划（可能无 EXPLAIN 权限或实例类型不支持）'}</Text>}
          </Card>

          {/* 索引现状 */}
          <Card size="small" title="索引现状" style={{ marginBottom: 12 }}>
            {data.indexes?.length
              ? <Table size="small" rowKey={(r) => `${r.table}:${r.index}`} columns={indexColumns}
                  dataSource={data.indexes} pagination={false} scroll={{ x: 'max-content' }} />
              : <Text type="secondary">未取到相关表索引信息</Text>}
          </Card>

          {/* 优化建议 */}
          <Card size="small" title={`优化建议（${data.suggestions?.length ?? 0}）`}>
            {data.suggestions?.length
              ? <Space direction="vertical" size={8} style={{ width: '100%' }}>
                  {data.suggestions.map((s, i) => (
                    <Card key={i} size="small" type="inner" title={<Text strong>{i + 1}. {s.title}</Text>}>
                      {s.detail && <Paragraph style={{ margin: '0 0 4px', whiteSpace: 'pre-wrap' }}>{s.detail}</Paragraph>}
                      {s.impact && <div style={{ fontSize: 13, marginBottom: 2 }}><Text type="secondary">影响：</Text>{s.impact}</div>}
                      {s.risk && <div style={{ fontSize: 13 }}><Text type="secondary">风险：</Text>{s.risk}</div>}
                      {s.index_ddl && <SqlBlock title="推荐索引 DDL" sql={s.index_ddl} />}
                      {s.sql_after && <SqlBlock title="优化后 SQL" sql={s.sql_after} />}
                      {s.sql_before && (
                        <Collapse size="small" ghost items={[{
                          key: 'before', label: <Text style={{ fontSize: 12, color: '#8c8c8c' }}>原始 SQL</Text>,
                          children: <pre style={{ whiteSpace: 'pre-wrap', margin: 0, fontSize: 12, color: '#8c8c8c' }}>{s.sql_before}</pre>,
                        }]} />
                      )}
                    </Card>
                  ))}
                </Space>
              : <Text type="secondary">暂无优化建议</Text>}
          </Card>

          <div style={{ marginTop: 12, color: '#8c8c8c', fontSize: 12 }}>
            诊断只给方案，不会执行任何变更；请在测试环境验证后由 DBA 人工执行。
          </div>
        </div>
      )}
    </Modal>
  )
}
