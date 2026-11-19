// 表结构对比纯函数（R16 §16.8，2026-11-19）
// 与后端 app/services/db_schema_diff.py 口径一致：字段按 name、索引按 name 匹配，
// 三态 = added / removed / modified；表选项逐 key 比较。
import type { DbTableChanges, DbTableColumn, DbTableIndex, DbTableSchema } from '../../services/api'

export interface DiffList<T> {
  added: T[]
  removed: T[]
  modified: Array<{ key: string; before: T; after: T }>
}

export interface TableDiff {
  columns: DiffList<DbTableColumn>
  indexes: DiffList<DbTableIndex>
  options: {
    added: never[]
    removed: never[]
    modified: Array<{ key: string; before: string; after: string }>
  }
}

const OPTION_KEYS = ['engine', 'charset', 'collation', 'comment'] as const

const normStr = (v: unknown): string => (v === null || v === undefined ? '' : String(v))

function colFingerprint(c: DbTableColumn): string {
  return JSON.stringify([
    (c.type || '').trim(),
    !!c.nullable,
    normStr(c.default),
    c.extra || '',
    c.comment || '',
  ])
}

function idxFingerprint(i: DbTableIndex): string {
  return JSON.stringify([!!i.unique, i.type || '', i.columns || []])
}

function diffList<T>(
  source: T[],
  target: T[],
  keyFn: (x: T) => string,
  cmpFn: (x: T) => string,
): DiffList<T> {
  const s = new Map(source.map((x) => [keyFn(x), x]))
  const t = new Map(target.map((x) => [keyFn(x), x]))
  const added: T[] = []
  const removed: T[] = []
  const modified: Array<{ key: string; before: T; after: T }> = []
  for (const [k, item] of t) if (!s.has(k)) added.push(item)
  for (const [k, item] of s) {
    if (!t.has(k)) removed.push(item)
    else if (cmpFn(s.get(k)!) !== cmpFn(t.get(k)!)) modified.push({ key: k, before: s.get(k)!, after: t.get(k)! })
  }
  return { added, removed, modified }
}

export function diffTableSchema(source: DbTableSchema, target: DbTableSchema): TableDiff {
  return {
    columns: diffList(source.columns || [], target.columns || [], (c) => c.name, colFingerprint),
    indexes: diffList(source.indexes || [], target.indexes || [], (i) => i.name, idxFingerprint),
    options: {
      added: [],
      removed: [],
      modified: OPTION_KEYS.filter(
        (k) => normStr((source as unknown as Record<string, unknown>)[k])
          !== normStr((target as unknown as Record<string, unknown>)[k]),
      ).map((k) => ({
        key: k,
        before: normStr((source as unknown as Record<string, unknown>)[k]),
        after: normStr((target as unknown as Record<string, unknown>)[k]),
      })),
    },
  }
}

/** 目标字段 → DbTableChanges 的列定义（extra 归一为 'auto_increment' 供后端 DDL 生成消费） */
function colOut(c: DbTableColumn): DbTableColumn {
  return {
    name: c.name,
    type: c.type || '',
    nullable: !!c.nullable,
    default: c.default,
    comment: c.comment || '',
    extra: (c.extra || '').toLowerCase().includes('auto_increment') ? 'auto_increment' : '',
  }
}

/** diff → DbTableChanges（复用既有 alterTable 的 DDL 生成与审核链路，不自行拼接 DDL） */
export function diffToChanges(diff: TableDiff): DbTableChanges {
  const changes: DbTableChanges = {}
  if (diff.columns.added.length) changes.add_columns = diff.columns.added.map(colOut)
  if (diff.columns.removed.length) changes.drop_columns = diff.columns.removed.map((c) => c.name)
  if (diff.columns.modified.length) changes.modify_columns = diff.columns.modified.map((m) => colOut(m.after))
  if (diff.indexes.added.length)
    changes.add_indexes = diff.indexes.added.map((i) => ({ name: i.name, columns: i.columns || [], unique: !!i.unique }))
  if (diff.indexes.removed.length) changes.drop_indexes = diff.indexes.removed.map((i) => i.name)
  for (const m of diff.options.modified) {
    if (m.key === 'engine') changes.engine = m.after
    else if (m.key === 'charset') changes.charset = m.after
    else if (m.key === 'collation') changes.collation = m.after
    else if (m.key === 'comment') changes.comment = m.after
  }
  return changes
}
