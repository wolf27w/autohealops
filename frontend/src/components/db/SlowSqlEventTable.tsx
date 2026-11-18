// 慢 SQL 执行明细表（阿里云慢日志样式：一行 = 一次执行）
// 14 列：执行开始时间 → SQL ID → SQL 展开 → 数据库 → 执行耗时 → 锁等待耗时 → 返回行数
//        → 扫描行数 → 访问来源 → 用户 → 线程 → 表名 → 标签 → 操作
import { useState } from 'react'
import { Table, Button, Tag, Space, type TableColumnsType } from 'antd'
import { ThunderboltOutlined, ProfileOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import type { SlowSqlEventItem } from '../../services/api'

// SQL 单元格：保留原换行（DIGEST_TEXT 常是超长单行），默认折 2 行可展开，避免省略号挡住后半段。
// 导出供聚合回退视图复用。
export function SqlCell({ sql, maxLines = 2 }: { sql?: string; maxLines?: number }) {
  const [open, setOpen] = useState(false)
  if (!sql) return <>-</>
  const long = sql.length > 120 || sql.split('\n').length > maxLines
  const clamped = long && !open
  return (
    <div>
      <pre style={{
        margin: 0, fontFamily: 'inherit', fontSize: 12, lineHeight: '18px',
        whiteSpace: 'pre-wrap', wordBreak: 'break-all',
        ...(clamped
          ? { display: '-webkit-box', WebkitLineClamp: maxLines, WebkitBoxOrient: 'vertical' as const, overflow: 'hidden' }
          : {}),
      }}>{sql}</pre>
      {long && (
        <Button type="link" size="small" style={{ padding: 0, height: 18, fontSize: 12 }}
          onClick={() => setOpen((v) => !v)}>{open ? '收起' : '展开'}</Button>
      )}
    </div>
  )
}

// 数值格式化：null/undefined 显示 '-'，带小数位则 toFixed
function fmtNum(v: number | null | undefined, digits?: number): string {
  if (v == null) return '-'
  return digits != null ? v.toFixed(digits) : String(v)
}

interface Props {
  items: SlowSqlEventItem[]
  loading: boolean
  total: number
  page: number
  pageSize: number
  onPageChange: (page: number, pageSize: number) => void
  onOpenTrend: (row: SlowSqlEventItem) => void
  onOpenDetail: (row: SlowSqlEventItem) => void
  onDiagnose: (row: SlowSqlEventItem) => void
}

export default function SlowSqlEventTable({
  items, loading, total, page, pageSize,
  onPageChange, onOpenTrend, onOpenDetail, onDiagnose,
}: Props) {
  const columns: TableColumnsType<SlowSqlEventItem> = [
    {
      title: '执行开始时间', dataIndex: 'start_time', width: 168,
      render: (v: string) => (v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '-'),
    },
    {
      title: 'SQL ID', dataIndex: 'digest', width: 148,
      render: (v: string, r) => (v
        ? <Button type="link" size="small" style={{ padding: 0 }} onClick={() => onOpenTrend(r)}>{v.slice(0, 16)}…</Button>
        : '-'),
    },
    { title: 'SQL', dataIndex: 'sql_text', width: 380, render: (v: string) => <SqlCell sql={v} /> },
    { title: '数据库', dataIndex: 'schema_name', width: 96, render: (v: string) => v || '-' },
    {
      title: '执行耗时(秒)', dataIndex: 'exec_seconds', width: 108, align: 'right',
      render: (v: number) => <b style={{ color: (v || 0) > 1 ? '#cf1322' : undefined }}>{fmtNum(v, 2)}</b>,
    },
    { title: '锁等待耗时(秒)', dataIndex: 'lock_seconds', width: 118, align: 'right', render: (v: number) => fmtNum(v, 2) },
    { title: '返回行数', dataIndex: 'rows_sent', width: 90, align: 'right', render: (v: number) => fmtNum(v) },
    { title: '扫描行数', dataIndex: 'rows_examined', width: 96, align: 'right', render: (v: number) => fmtNum(v) },
    { title: '访问来源', dataIndex: 'client_host', width: 128, render: (v: string) => v || '-' },
    { title: '用户', dataIndex: 'db_user', width: 96, render: (v: string) => v || '-' },
    { title: '线程', dataIndex: 'thread_id', width: 78, align: 'right', render: (v: number) => fmtNum(v) },
    { title: '表名', dataIndex: 'tables', width: 140, ellipsis: true, render: (v: string) => v || '-' },
    { title: '标签', dataIndex: 'tag', width: 100, render: (v: string) => (v ? <Tag color="purple">{v}</Tag> : '-') },
    {
      title: '操作', width: 132, fixed: 'right',
      render: (_: unknown, r) => (
        <Space size={4}>
          <Button size="small" type="link" icon={<ProfileOutlined />} onClick={() => onOpenDetail(r)}>详情</Button>
          <Button size="small" type="link" icon={<ThunderboltOutlined />}
            disabled={!r.digest} onClick={() => onDiagnose(r)}>诊断</Button>
        </Space>
      ),
    },
  ]

  return (
    <Table
      rowKey="id"
      size="small"
      dataSource={items}
      columns={columns}
      loading={loading}
      scroll={{ x: 'max-content' }}
      pagination={{
        current: page,
        pageSize,
        total,
        showSizeChanger: true,
        pageSizeOptions: [20, 50, 100, 200],
        showTotal: (t) => `共 ${t} 条`,
        onChange: (p, ps) => onPageChange(p, ps),
      }}
    />
  )
}
