// SQL 结果网格：列排序 / 按列筛选 / 复制单元格 / 复制筛选结果（TSV）。
// 复用：SqlTerminal 已接入；DbWorkbench 待父会话决定是否接入（结果结构同为 columns:string[] + rows:unknown[][]）。
import { useMemo, useRef, useState } from 'react'
import type React from 'react'
import { Button, Input, Select, Space, Table, Tooltip, message } from 'antd'
import { FilterOutlined, CopyOutlined } from '@ant-design/icons'
import type { ColumnType as AntdColumnType } from 'antd/es/table'
import {
  cellToString, compareCells, inferColumnType, distinctValues, applyFilters, sortRows, toTsv,
  type ColumnFilter, type ColumnType, type SortDirection,
} from './resultGridLogic'

// 低基数阈值：去重值不超过该值时用下拉精确筛选，否则用输入框包含匹配。
const LOW_CARDINALITY_MAX = 20

interface ResultGridProps {
  columns: string[]
  rows: unknown[][]
}

interface RecordShape { __i: number; row: unknown[] }
interface SelectedCell { row: unknown[]; col: number }

/** 复制文本：优先 navigator.clipboard，失败降级 textarea + execCommand，再失败提示。 */
async function copyText(text: string, okMsg: string): Promise<void> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      message.success(okMsg)
      return
    }
    throw new Error('clipboard unavailable')
  } catch {
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand('copy')
      document.body.removeChild(ta)
      if (ok) message.success(okMsg)
      else message.error('复制失败，请手动复制')
    } catch {
      message.error('复制失败，请手动复制')
    }
  }
}

export default function ResultGrid({ columns, rows }: ResultGridProps) {
  const [sort, setSort] = useState<{ col: number; dir: SortDirection } | null>(null)
  const [filters, setFilters] = useState<Record<number, ColumnFilter>>({})
  const [selected, setSelected] = useState<SelectedCell | null>(null)
  // 去重值缓存：仅在打开某列下拉时才计算该列，避免大结果集一次算完所有列。
  const distinctCache = useRef<Map<number, string[]>>(new Map())

  const colTypes = useMemo<ColumnType[]>(
    () => columns.map((_, ci) => inferColumnType(rows, ci)),
    [columns, rows],
  )

  const filteredRows = useMemo(() => applyFilters(rows, filters), [rows, filters])
  const dataSource = useMemo<RecordShape[]>(
    () => filteredRows.map((row, idx) => ({ __i: idx, row })),
    [filteredRows],
  )

  const getDistinct = (ci: number): string[] => {
    let v = distinctCache.current.get(ci)
    if (!v) { v = distinctValues(rows, ci); distinctCache.current.set(ci, v) }
    return v
  }

  const setFilter = (ci: number, f: ColumnFilter | undefined) => {
    setFilters((prev) => {
      const next = { ...prev }
      if (f) next[ci] = f
      else delete next[ci]
      return next
    })
  }

  const hasFilter = Object.keys(filters).length > 0

  const copyCell = async () => {
    if (!selected) { message.warning('先点击某个单元格选中'); return }
    await copyText(cellToString(selected.row[selected.col]), '单元格已复制')
  }

  const copyResult = async () => {
    const ordered = sort ? sortRows(filteredRows, sort.col, sort.dir, colTypes[sort.col]) : filteredRows
    await copyText(toTsv(columns, ordered), `已复制 ${ordered.length} 行（TSV，含表头）`)
  }

  const tableColumns: AntdColumnType<RecordShape>[] = columns.map((name, ci) => {
    const type = colTypes[ci]
    const distinct = getDistinct(ci)
    const lowCard = distinct.length <= LOW_CARDINALITY_MAX
    const curFilter = filters[ci]
    const curSort = sort?.col === ci ? sort.dir : null

    return {
      title: name,
      key: String(ci),
      dataIndex: ['row', ci],
      ellipsis: true,
      sorter: (a: RecordShape, b: RecordShape) => compareCells(a.row[ci], b.row[ci], type),
      sortOrder: curSort === 'asc' ? 'ascend' : curSort === 'desc' ? 'descend' : null,
      sortDirections: ['ascend', 'descend'] as const,
      filterIcon: (filtered: boolean) => (
        <FilterOutlined style={{ color: filtered ? '#1677ff' : undefined }} />
      ),
      filteredValue: curFilter ? [curFilter.value] : null,
      filterDropdown: () => (
        <div
          style={{ padding: 8, background: '#fff', borderRadius: 6, boxShadow: '0 3px 6px -4px rgba(0,0,0,.12)', minWidth: 200 }}
          onClick={(e) => e.stopPropagation()}
        >
          {lowCard ? (
            <Select
              size="small"
              style={{ width: '100%' }}
              placeholder="精确匹配"
              allowClear
              showSearch
              value={curFilter?.value}
              options={distinct.map((d) => ({ label: d, value: d }))}
              onChange={(v: string | undefined) => setFilter(ci, v ? { value: v, mode: 'equals' } : undefined)}
            />
          ) : (
            <Input
              size="small"
              placeholder="包含匹配（不区分大小写）"
              value={curFilter?.value ?? ''}
              onChange={(e) => {
                const v = e.target.value
                setFilter(ci, v ? { value: v, mode: 'contains' } : undefined)
              }}
            />
          )}
          <div style={{ marginTop: 6, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: 12, color: '#8c8c8c' }}>
              {lowCard ? `去重 ${distinct.length} 值` : '输入即筛'}
            </span>
            <Button size="small" type="link" style={{ padding: 0 }} disabled={!curFilter}
              onClick={() => setFilter(ci, undefined)}>清空</Button>
          </div>
        </div>
      ),
      render: (v: unknown) => {
        if (v === null) return <span style={{ color: '#bfbfbf', fontStyle: 'italic' }}>NULL</span>
        if (v === undefined) return <span style={{ color: '#bfbfbf' }}>∅</span>
        return cellToString(v)
      },
      onCell: (record: RecordShape) => ({
        onClick: () => setSelected({ row: record.row, col: ci }),
        style: {
          cursor: 'pointer',
          background: selected && selected.row === record.row && selected.col === ci ? '#e6f4ff' : undefined,
        },
      }),
    }
  })

  return (
    <div>
      {/* 工具栏：行数 + 复制 + 清空筛选 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 2px 6px' }}>
        <span style={{ fontSize: 12, color: '#595959' }}>
          筛选后 <b>{filteredRows.length}</b> / 总 <b>{rows.length}</b> 行
        </span>
        {selected && (
          <Tooltip title={`已选中：第 ${filteredRows.indexOf(selected.row) + 1} 行 · 第 ${selected.col + 1} 列`}>
            <span style={{ fontSize: 12, color: '#1677ff' }}>已选中单元格</span>
          </Tooltip>
        )}
        <span style={{ flex: 1 }} />
        <Button size="small" icon={<CopyOutlined />} disabled={!selected} onClick={copyCell}>复制单元格</Button>
        <Button size="small" icon={<CopyOutlined />} disabled={filteredRows.length === 0} onClick={copyResult}>
          复制结果（TSV）
        </Button>
        {hasFilter && (
          <Button size="small" type="link" onClick={() => { setFilters({}) }}>清空筛选</Button>
        )}
      </div>

      <Table<RecordShape>
        size="small"
        rowKey="__i"
        dataSource={dataSource}
        columns={tableColumns}
        pagination={{ pageSize: 20, showSizeChanger: false, size: 'small' }}
        scroll={{ x: 'max-content' }}
        onChange={(_pg, _fl, sorter) => {
          const s = Array.isArray(sorter) ? sorter[0] : sorter
          if (!s || s.order === undefined) { setSort(null); return }
          setSort({ col: Number(s.columnKey), dir: s.order === 'ascend' ? 'asc' : 'desc' })
        }}
      />
    </div>
  )
}
