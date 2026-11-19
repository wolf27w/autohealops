// SQL 结果网格纯逻辑（不依赖 React）：排序比较 / 列类型推断 / 筛选匹配 / TSV 序列化。
// 供 ResultGrid 组件复用，也便于将来接入 DbWorkbench 或写单测。

export type SortDirection = 'asc' | 'desc'
export type ColumnType = 'number' | 'date' | 'string'
export interface ColumnFilter {
  /** 筛选关键词（下拉筛选时是精确值） */
  value: string
  /** equals = 低基数下拉精确匹配；contains = 高基数输入框包含匹配（不区分大小写） */
  mode: 'equals' | 'contains'
}

/** 单元格 → 展示/复制用字符串：null 显示为 "NULL"。 */
export function cellToString(v: unknown): string {
  if (v === null) return 'NULL'
  if (v === undefined) return ''
  if (typeof v === 'object') {
    try { return JSON.stringify(v) } catch { return String(v) }
  }
  return String(v)
}

/** 空值判定：null / undefined / 空字符串统一视为「空」，排序时排最后。 */
export function emptyRank(v: unknown): 0 | 1 {
  return (v === null || v === undefined || v === '') ? 1 : 0
}

function isDateLike(s: string): boolean {
  return /^\d{4}[-/]\d{1,2}[-/]\d{1,2}/.test(s) && !Number.isNaN(Date.parse(s))
}

/** 按整列样本推断类型：数字列按数值、日期列按时间、其余按字符串。 */
export function inferColumnType(rows: unknown[][], colIndex: number): ColumnType {
  let hasValue = false
  let allNumber = true
  let allDate = true
  for (const row of rows) {
    const v = row[colIndex]
    if (v === null || v === undefined) continue
    const s = String(v).trim()
    if (s === '') continue
    hasValue = true
    const isNum = typeof v === 'number' || (typeof v === 'string' && !Number.isNaN(Number(s)))
    const isDate = typeof v === 'string' && isDateLike(s)
    if (!isNum) allNumber = false
    if (!isDate) allDate = false
    if (!allNumber && !allDate) break
  }
  if (!hasValue) return 'string'
  if (allNumber) return 'number'
  if (allDate) return 'date'
  return 'string'
}

/** 非空值比较（调用方需保证两侧非空）。 */
function compareValues(a: unknown, b: unknown, type: ColumnType): number {
  if (type === 'number') {
    const d = Number(a) - Number(b)
    return d === 0 ? 0 : d > 0 ? 1 : -1
  }
  if (type === 'date') {
    const d = Date.parse(String(a)) - Date.parse(String(b))
    return d === 0 ? 0 : d > 0 ? 1 : -1
  }
  const c = String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' })
  return c === 0 ? 0 : c > 0 ? 1 : -1
}

/** 两单元格比较：空值恒排最后（与方向无关）。供 antd Table sorter 用。 */
export function compareCells(a: unknown, b: unknown, type: ColumnType): number {
  const ea = emptyRank(a)
  const eb = emptyRank(b)
  if (ea !== eb) return ea - eb
  if (ea === 1) return 0
  return compareValues(a, b, type)
}

/** 稳定排序（返回新数组，不修改入参）。空值统一排最后，升/降序均如此。 */
export function sortRows(
  rows: unknown[][],
  colIndex: number,
  direction: SortDirection,
  type?: ColumnType,
): unknown[][] {
  const t = type ?? inferColumnType(rows, colIndex)
  const sign = direction === 'asc' ? 1 : -1
  const indexed = rows.map((r, i) => ({ r, i }))
  indexed.sort((x, y) => {
    const ea = emptyRank(x.r[colIndex])
    const eb = emptyRank(y.r[colIndex])
    if (ea !== eb) return ea - eb
    if (ea === 1) return x.i - y.i
    const c = compareValues(x.r[colIndex], y.r[colIndex], t)
    return c !== 0 ? c * sign : x.i - y.i
  })
  return indexed.map((x) => x.r)
}

/** 单单元格是否命中筛选。 */
export function matchesFilter(cell: unknown, filter: ColumnFilter | undefined): boolean {
  if (!filter || filter.value === '') return true
  const s = cellToString(cell)
  if (filter.mode === 'equals') return s === filter.value
  return s.toLowerCase().includes(filter.value.toLowerCase())
}

/** 多列筛选：所有生效的筛选条件须同时命中（AND）。 */
export function applyFilters(rows: unknown[][], filters: Record<number, ColumnFilter>): unknown[][] {
  const entries = Object.entries(filters)
  if (entries.length === 0) return rows
  return rows.filter((r) => entries.every(([ci, f]) => matchesFilter(r[Number(ci)], f)))
}

/** 某列去重值（按出现顺序，用 cellToString 归一化）。用于低基数下拉。 */
export function distinctValues(rows: unknown[][], colIndex: number): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const r of rows) {
    const s = cellToString(r[colIndex])
    if (!seen.has(s)) { seen.add(s); out.push(s) }
  }
  return out
}

function tsvEscape(v: string): string {
  if (/[\t\n\r"]/.test(v)) return '"' + v.replace(/"/g, '""') + '"'
  return v
}

/** 结果集 → TSV（含表头）。 */
export function toTsv(columns: string[], rows: unknown[][]): string {
  const lines: string[] = [columns.map(tsvEscape).join('\t')]
  for (const r of rows) lines.push(r.map((v) => tsvEscape(cellToString(v))).join('\t'))
  return lines.join('\n')
}
