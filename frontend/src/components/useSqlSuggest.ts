// SQL 自动补全（公共 hook）：关键字 + 库/表/字段候选，跟随光标位置的候选框。
// 说明：从 components/DbWorkbench.tsx 的补全实现抽出，供独立 SQL 终端与工作台共用。
import { useCallback, useRef, useState } from 'react'

export interface SuggestState {
  items: string[]
  start: number
  end: number
  idx: number
}

export const BASE_KEYWORDS = [
  'select', 'from', 'where', 'and', 'or', 'not', 'in', 'exists', 'between', 'like', 'is null', 'is not null',
  'group by', 'order by', 'having', 'limit', 'offset', 'join', 'left join', 'right join', 'inner join',
  'outer join', 'on', 'as', 'distinct', 'union', 'union all', 'insert into', 'values', 'update', 'set',
  'delete from', 'create table', 'alter table', 'drop table', 'truncate table', 'create index', 'drop index',
  'primary key', 'foreign key', 'show tables', 'show databases', 'show create table', 'describe', 'explain',
  'count', 'sum', 'avg', 'min', 'max', 'case when', 'then', 'else', 'end', 'begin', 'commit', 'rollback',
  'with', 'asc', 'desc', 'null', 'default', 'if', 'coalesce', 'cast',
]

export const DIALECT_KEYWORDS: Record<string, string[]> = {
  mysql: [...BASE_KEYWORDS, 'auto_increment', 'engine', 'charset', 'collate', 'use', 'database', 'table',
    'view', 'grant', 'revoke', 'replace into', 'on duplicate key update', 'straight_join', 'force index'],
  pg: [...BASE_KEYWORDS, 'returning', 'ilike', 'schema', 'sequence', 'materialized view', 'vacuum', 'analyze',
    'on conflict', 'do update', 'do nothing', 'lateral', 'serial', 'jsonb', 'array'],
  oracle: [...BASE_KEYWORDS, 'rownum', 'dual', 'nvl', 'nvl2', 'decode', 'varchar2', 'number', 'merge into',
    'connect by', 'start with', 'sysdate', 'sequence.nextval'],
}

export function keywordsFor(dbType?: string): string[] {
  return DIALECT_KEYWORDS[(dbType || 'mysql').toLowerCase()] || BASE_KEYWORDS
}

/** 从 SQL 里粗略提取被引用的表名（from / join / update / into 后面那个标识符） */
export function referencedTables(sql: string): string[] {
  const out = new Set<string>()
  const re = /\b(?:from|join|update|into)\s+`?([A-Za-z0-9_$.]+)`?/gi
  let m: RegExpExecArray | null
  while ((m = re.exec(sql))) {
    const name = m[1].split('.').pop()
    if (name && !['select', 'where'].includes(name.toLowerCase())) out.add(name)
  }
  return [...out]
}

/** 光标坐标测量用的隐藏镜像 div 样式（与 textarea 字体一致才能对准） */
export const MIRROR_STYLE: React.CSSProperties = {
  position: 'absolute',
  top: 0,
  left: -9999,
  visibility: 'hidden',
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
  overflow: 'hidden',
  pointerEvents: 'none',
}

export interface UseSqlSuggestResult {
  sugg: SuggestState | null
  caretPos: { top: number; left: number }
  mirrorRef: React.RefObject<HTMLDivElement | null>
  syncSuggest: (value: string, caret: number) => void
  applySugg: (value: string, item: string, onApplied: (next: string, caret: number) => void) => void
  /** 键盘交互：返回 true 表示已消费该按键 */
  handleKey: (e: React.KeyboardEvent<HTMLTextAreaElement>, value: string, onApplied: (next: string, caret: number) => void) => boolean
  close: () => void
}

/**
 * @param getCandidates 返回候选词（每次按键都取最新：关键字 + 库/表/字段）
 * @param getTextarea 取真实 textarea 元素（antd Input.TextArea 需 resizableTextArea.textArea）
 */
export function useSqlSuggest(
  getCandidates: () => string[],
  getTextarea: () => HTMLTextAreaElement | null,
): UseSqlSuggestResult {
  const [sugg, setSugg] = useState<SuggestState | null>(null)
  const [caretPos, setCaretPos] = useState<{ top: number; left: number }>({ top: 0, left: 0 })
  const mirrorRef = useRef<HTMLDivElement | null>(null)

  const measureCaret = useCallback((text: string, caret: number) => {
    const ta = getTextarea()
    const mirror = mirrorRef.current
    if (!ta || !mirror) return
    const cs = window.getComputedStyle(ta)
    mirror.style.font = cs.font
    mirror.style.lineHeight = cs.lineHeight
    mirror.style.letterSpacing = cs.letterSpacing
    mirror.style.padding = cs.padding
    mirror.style.width = `${ta.clientWidth}px`
    mirror.textContent = text.slice(0, caret)
    const marker = document.createElement('span')
    marker.textContent = '\u200b'
    mirror.appendChild(marker)
    const lineH = parseFloat(cs.lineHeight || '19') || 19
    const top = marker.offsetTop - ta.scrollTop + lineH
    const left = marker.offsetLeft - ta.scrollLeft
    setCaretPos({
      top: Math.max(0, Math.min(top, ta.clientHeight - 24)),
      left: Math.max(0, Math.min(left, Math.max(0, ta.clientWidth - 220))),
    })
    mirror.textContent = ''
  }, [getTextarea])

  const syncSuggest = useCallback((value: string, caret: number) => {
    const before = value.slice(0, caret)
    const m = before.match(/[A-Za-z0-9_$.]+$/)
    const word = m ? m[0] : ''
    if (!word || word.length < 1) { setSugg(null); return }
    const low = word.toLowerCase()
    const seen = new Set<string>()
    const items: string[] = []
    for (const c of getCandidates()) {
      const k = c.toLowerCase()
      if (seen.has(k) || k === low) continue
      if (k.startsWith(low)) { seen.add(k); items.push(c); if (items.length >= 20) break }
    }
    if (!items.length) { setSugg(null); return }
    measureCaret(value, caret)
    setSugg({ items, start: caret - word.length, end: caret, idx: 0 })
  }, [getCandidates, measureCaret])

  const applySugg = useCallback((value: string, item: string, onApplied: (next: string, caret: number) => void) => {
    const ta = getTextarea()
    const text = ta ? ta.value : value
    const caret = ta ? (ta.selectionStart ?? text.length) : text.length
    const before = text.slice(0, caret)
    const m = before.match(/[A-Za-z0-9_$.]+$/)
    const start = caret - (m ? m[0].length : 0)
    const next = text.slice(0, start) + item + text.slice(caret)
    setSugg(null)
    onApplied(next, start + item.length)
  }, [getTextarea])

  const close = useCallback(() => setSugg(null), [])

  const handleKey = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>, value: string,
    onApplied: (next: string, caret: number) => void): boolean => {
    if (!sugg) return false
    if (e.key === 'ArrowDown') { e.preventDefault(); setSugg({ ...sugg, idx: (sugg.idx + 1) % sugg.items.length }); return true }
    if (e.key === 'ArrowUp') { e.preventDefault(); setSugg({ ...sugg, idx: (sugg.idx - 1 + sugg.items.length) % sugg.items.length }); return true }
    if (e.key === 'Enter' || e.key === 'Tab') {
      e.preventDefault(); e.stopPropagation()
      applySugg(value, sugg.items[sugg.idx], onApplied)
      return true
    }
    if (e.key === 'Escape') { e.preventDefault(); setSugg(null); return true }
    return false
  }, [sugg, applySugg])

  return { sugg, caretPos, mirrorRef, syncSuggest, applySugg, handleKey, close }
}
