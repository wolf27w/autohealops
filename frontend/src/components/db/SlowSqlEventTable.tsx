// 慢 SQL 执行明细表（阿里云慢日志样式：一行 = 一次执行）
// 14 列：执行开始时间 → SQL ID → SQL 展开 → 数据库 → 执行耗时 → 锁等待耗时 → 返回行数
//        → 扫描行数 → 访问来源 → 用户 → 线程 → 表名 → 标签 → 操作
import { useState } from 'react'
import { Table, Button, Tag, Space, message, type TableColumnsType } from 'antd'
import { ThunderboltOutlined, ProfileOutlined, CopyOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import type { SlowSqlEventItem } from '../../services/api'

// SQL 单元格：保留原换行（DIGEST_TEXT 常是超长单行），默认折 2 行可展开，避免省略号挡住后半段。
// 导出供聚合回退视图复用。
export function SqlCell({ sql, maxLines = 2 }: {
  sql?: string
  maxLines?: number
  /** 兼容旧调用（不再展示截断提示，2026-11-26 老板要求） */
  truncated?: boolean
  len?: number
}) {
  const [open, setOpen] = useState(false)
  if (!sql) return <>-</>
  const long = sql.length > 120 || sql.split('\n').length > maxLines
  const clamped = long && !open
  // 2026-11-26（老板）：按原样显示 SQL（保留数据库里的换行与缩进）
  return (
    <div>
      <pre style={{
        margin: 0, fontFamily: 'inherit', fontSize: 12, lineHeight: '18px',
        whiteSpace: 'pre-wrap', wordBreak: 'break-all',
        ...(clamped
          ? { display: '-webkit-box', WebkitLineClamp: maxLines, WebkitBoxOrient: 'vertical' as const, overflow: 'hidden' }
          : {}),
      }}>{sql}</pre>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 2 }}>
        {long && (
          <Button type="link" size="small" style={{ padding: 0, height: 18, fontSize: 12 }}
            onClick={() => setOpen((v) => !v)}>{open ? '收起' : '展开全文'}</Button>
        )}
        {open && (
          <Button type="link" size="small" style={{ padding: 0, height: 18, fontSize: 12 }}
            icon={<CopyOutlined />}
            onClick={() => {
              navigator.clipboard?.writeText(sql)
                .then(() => message.success('已复制完整 SQL'))
                .catch(() => message.error('复制失败'))
            }}>复制</Button>
        )}
      </div>
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
  /** 当前排序（time/time_asc/exec/exec_asc/rows_examined/rows_examined_asc），点表头切换 */
  order?: string
  onOrderChange?: (order: string) => void
  onOpenTrend: (row: SlowSqlEventItem) => void
  onOpenDetail: (row: SlowSqlEventItem) => void
  onDiagnose: (row: SlowSqlEventItem) => void
  onEditTag: (row: SlowSqlEventItem) => void
}

export default function SlowSqlEventTable({
  items, loading, total, page, pageSize,
  onPageChange, onOpenTrend, onOpenDetail, onDiagnose, onEditTag,
  order = 'time', onOrderChange,
}: Props) {
  // 表头排序 ↔ 后端 order 参数（desc=xxx，asc=xxx_asc）；不排序回落到时间倒序
  const sortOf = (col: 'time' | 'exec' | 'rows_examined') =>
    (order === col ? 'descend' : order === `${col}_asc` ? 'ascend' : null)
  const onTableChange = (
    _p: unknown, _f: unknown,
    sorter: { columnKey?: string; order?: string },
    extra?: { action?: string },
  ) => {
    // 2026-11-26 老板反馈「翻页失败」：翻页/筛选也会触发 onChange，
    // 原来无条件把 order 重置成 time 并把页码打回 1，表现为「点第 2 页自己跳回第 1 页」。
    // 只有真正的排序动作（action === 'sort'）才改排序。
    if (!onOrderChange || extra?.action !== 'sort') return
    const key = sorter?.columnKey
    if (!key || !sorter?.order) { onOrderChange('time'); return }
    onOrderChange(sorter.order === 'descend' ? key : `${key}_asc`)
  }
  const columns: TableColumnsType<SlowSqlEventItem> = [
    {
      title: '执行开始时间', dataIndex: 'start_time', width: 168, key: 'time',
      sorter: true, sortOrder: sortOf('time'),
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
      title: '执行耗时(秒)', dataIndex: 'exec_seconds', width: 108, align: 'right', key: 'exec',
      sorter: true, sortOrder: sortOf('exec'),
      render: (v: number) => <b style={{ color: (v || 0) > 1 ? '#cf1322' : undefined }}>{fmtNum(v, 2)}</b>,
    },
    { title: '锁等待耗时(秒)', dataIndex: 'lock_seconds', width: 118, align: 'right', render: (v: number) => fmtNum(v, 2) },
    { title: '返回行数', dataIndex: 'rows_sent', width: 90, align: 'right', render: (v: number) => fmtNum(v) },
    { title: '扫描行数', dataIndex: 'rows_examined', width: 96, align: 'right', key: 'rows_examined', sorter: true, sortOrder: sortOf('rows_examined'), render: (v: number) => fmtNum(v) },
    { title: '访问来源', dataIndex: 'client_host', width: 128, render: (v: string) => v || '-' },
    { title: '用户', dataIndex: 'db_user', width: 96, render: (v: string) => v || '-' },
    { title: '线程', dataIndex: 'thread_id', width: 78, align: 'right', render: (v: number) => fmtNum(v) },
    { title: '表名', dataIndex: 'tables', width: 140, ellipsis: true, render: (v: string) => v || '-' },
    { title: '标签', dataIndex: 'tag', width: 110, render: (v: string, r) => (
      v
        ? <Tag color="purple" style={{ cursor: 'pointer' }} onClick={() => onEditTag(r)}>{v}</Tag>
        : <Button size="small" type="link" style={{ padding: 0, height: 18 }} onClick={() => onEditTag(r)}>打标</Button>
    ) },
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
      onChange={onTableChange as never}
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
