// 数据库管理面板（Navicat 式）：左侧实例列表，右上 SQL 编辑，右下结果。
// 直接嵌在「诊断中心 → 数据库管理」tab 里，不再是弹窗。
import { useEffect, useMemo, useRef, useState } from 'react'
import { Button, Input, List, Tag, Space, Table, Empty, Popconfirm, Tooltip, Switch, Modal, Form, message } from 'antd'
import { PlusOutlined, PlayCircleOutlined, ReloadOutlined, EditOutlined, DeleteOutlined, DownloadOutlined, ThunderboltOutlined, ClockCircleOutlined, TableOutlined, EyeOutlined, FunctionOutlined, FieldTimeOutlined, ExportOutlined, ImportOutlined } from '@ant-design/icons'
import { execDBConnection, queryDBConnection, downloadDBQuery, getDBSchema, listSqlTasks, createSqlTask, updateSqlTask, deleteSqlTask, runSqlTask, getSqlTaskCounts, getObjectDDL, exportDBObjects, importDBObjects, type SqlTaskRow } from '../services/api'

export interface ConsoleConn {
  id: number
  name: string
  db_type?: string
  database?: string
  host?: string
  port?: number
  source?: string
  status?: string
  cluster_id?: number | null
  collect_enabled?: boolean
  inspect_enabled?: boolean
}

interface Props {
  conns: ConsoleConn[]
  selectedId: number | null
  onSelect: (id: number) => void
  onAdd: () => void
  onDiagnose: (c: ConsoleConn) => void
  onTest: (c: ConsoleConn) => void
  onCollect: (c: ConsoleConn) => void
  onEdit: (c: ConsoleConn) => void
  onDelete: (c: ConsoleConn) => void
  onToggleCollect: (c: ConsoleConn, v: boolean) => void
  onToggleInspect: (c: ConsoleConn, v: boolean) => void
}

type Result = { columns?: string[]; rows?: unknown[][]; truncated?: boolean; affected_rows?: number }

// SQL 关键字补全（按数据库类型）
const BASE_KEYWORDS = [
  'select', 'from', 'where', 'and', 'or', 'not', 'in', 'exists', 'between', 'like', 'is null', 'is not null',
  'group by', 'order by', 'having', 'limit', 'offset', 'join', 'left join', 'right join', 'inner join',
  'outer join', 'on', 'as', 'distinct', 'union', 'union all', 'insert into', 'values', 'update', 'set',
  'delete from', 'create table', 'alter table', 'drop table', 'truncate table', 'create index', 'drop index',
  'primary key', 'foreign key', 'show tables', 'show databases', 'show create table', 'describe', 'explain',
  'count', 'sum', 'avg', 'min', 'max', 'case when', 'then', 'else', 'end', 'begin', 'commit', 'rollback',
  'with', 'asc', 'desc', 'null', 'default', 'if', 'coalesce', 'cast',
]
const DIALECT_KEYWORDS: Record<string, string[]> = {
  mysql: [...BASE_KEYWORDS, 'auto_increment', 'engine', 'charset', 'collate', 'use', 'database', 'table',
    'view', 'grant', 'revoke', 'replace into', 'on duplicate key update', 'straight_join', 'force index'],
  pg: [...BASE_KEYWORDS, 'returning', 'ilike', 'schema', 'sequence', 'materialized view', 'vacuum', 'analyze',
    'on conflict', 'do update', 'do nothing', 'lateral', 'serial', 'jsonb', 'array'],
  oracle: [...BASE_KEYWORDS, 'rownum', 'dual', 'nvl', 'nvl2', 'decode', 'varchar2', 'number', 'merge into',
    'connect by', 'start with', 'sysdate', 'sequence.nextval'],
}

interface SchemaData {
  databases: string[]
  tables: { db: string; name: string }[]
  views: { db: string; name: string }[]
  routines: { db: string; name: string; type: string }[]
  events: { db: string; name: string; extra?: string }[]
}
const EMPTY_SCHEMA: SchemaData = { databases: [], tables: [], views: [], routines: [], events: [] }


const READ_HEADS = new Set(['select', 'show', 'desc', 'describe', 'explain', 'with'])

export default function DbWorkbench({
  conns, selectedId, onSelect, onAdd, onDiagnose, onTest, onCollect, onEdit, onDelete,
  onToggleCollect, onToggleInspect,
}: Props) {
  const [kw, setKw] = useState('')
  const [sql, setSql] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [res, setRes] = useState<Result | null>(null)
  const cur = useMemo(() => conns.find((c) => c.id === selectedId) || null, [conns, selectedId])
  const isAgent = (cur?.source || 'direct') === 'agent'
  // 直连库也支持平台执行（平台存账密，直接连库）；目前直连仅 mysql
  const canSql = !!cur && (isAgent || (cur.db_type || 'mysql').toLowerCase() === 'mysql')
  const head = useMemo(() => (sql.trim().split(/\s+/)[0] || '').toLowerCase(), [sql])
  const isRead = READ_HEADS.has(head)
  const filtered = useMemo(() => {
    const k = kw.trim().toLowerCase()
    return k ? conns.filter((c) => `${c.name} ${c.database || ''} ${c.db_type || ''}`.toLowerCase().includes(k)) : conns
  }, [conns, kw])

  const [schema, setSchema] = useState<SchemaData>(EMPTY_SCHEMA)
  const [sugg, setSugg] = useState<{ items: string[]; start: number; end: number; idx: number } | null>(null)
  const taRef = useRef<{ resizableTextArea?: { textArea: HTMLTextAreaElement } } | null>(null)
  const mirrorRef = useRef<HTMLDivElement | null>(null)
  const [caretPos, setCaretPos] = useState<{ top: number; left: number }>({ top: 0, left: 0 })
  // 定时任务
  const [tasksOpen, setTasksOpen] = useState(false)
  const [tasks, setTasks] = useState<SqlTaskRow[]>([])
  const [counts, setCounts] = useState<Record<number, number>>({})
  const [selLen, setSelLen] = useState(0)
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({ table: true, view: true })
  const [editTaskId, setEditTaskId] = useState<number | null>(null)
  const [taskForm] = Form.useForm()

  // 选中库变化 → 拉一次可见库/表（用于补全，60s 服务端缓存）
  useEffect(() => {
    setSchema(EMPTY_SCHEMA)
    if (!cur || (cur.source || 'direct') !== 'agent') return
    let alive = true
    getDBSchema(cur.id)
      .then((d) => {
        if (!alive) return
        const raw = d as unknown as {
          databases?: string[]; tables?: { db: string; name: string }[]
          views?: { db: string; name: string }[]
          routines?: { db: string; name: string; type: string }[]
          events?: { db: string; name: string; extra?: string }[]
        }
        setSchema({
          databases: (raw?.databases || []).filter((x) => !['information_schema', 'performance_schema', 'mysql', 'sys'].includes(x)),
          tables: raw?.tables || [], views: raw?.views || [],
          routines: raw?.routines || [], events: raw?.events || [],
        })
      })
      .catch(() => setSchema(EMPTY_SCHEMA))
    return () => { alive = false }
  }, [cur?.id, cur?.source])

  const candidates = useMemo(() => {
    const kws = DIALECT_KEYWORDS[(cur?.db_type || 'mysql').toLowerCase()] || BASE_KEYWORDS
    const set = new Set<string>()
    kws.forEach((k) => set.add(k))
    schema.databases.forEach((x) => set.add(x))
    schema.tables.forEach((t) => set.add(t.name))
    return [...set]
  }, [cur?.db_type, schema])

  // 用镜像 div 量光标坐标 → 候选框贴在输入框里的光标下方
  const measureCaret = (text: string, caret: number) => {
    const ta = taRef.current?.resizableTextArea?.textArea
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
  }

  const groups = useMemo(() => ([
    { key: 'table', label: '表', kind: 'table', items: schema.tables.map((t) => ({ name: t.name })) },
    { key: 'view', label: '视图', kind: 'view', items: schema.views.map((t) => ({ name: t.name })) },
    { key: 'function', label: '函数', kind: 'function', items: schema.routines.filter((r) => (r.type || '').toUpperCase() === 'FUNCTION').map((r) => ({ name: r.name })) },
    { key: 'procedure', label: '存储过程', kind: 'procedure', items: schema.routines.filter((r) => (r.type || '').toUpperCase() === 'PROCEDURE').map((r) => ({ name: r.name })) },
    { key: 'event', label: '事件', kind: 'event', items: schema.events.map((e) => ({ name: e.name })) },
  ]), [schema])

  const syncSuggest = (text: string, caret: number) => {
    const before = text.slice(0, caret)
    const m = before.match(/[A-Za-z0-9_$.]+$/)
    const word = m ? m[0] : ''
    if (!word) { setSugg(null); return }
    const low = word.toLowerCase()
    const items = candidates.filter((c) => c.toLowerCase().startsWith(low) && c.toLowerCase() !== low).slice(0, 20)
    if (!items.length) { setSugg(null); return }
    measureCaret(text, caret)
    setSugg({ items, start: caret - word.length, end: caret, idx: 0 })
  }

  const applySugg = (item: string) => {
    if (!sugg) return
    const next = sql.slice(0, sugg.start) + item + sql.slice(sugg.end)
    setSql(next)
    setSugg(null)
    const pos = sugg.start + item.length
    requestAnimationFrame(() => {
      const el = taRef.current?.resizableTextArea?.textArea
      if (el) { el.focus(); el.setSelectionRange(pos, pos) }
    })
  }

  const reset = () => { setRes(null); setErr('') }

  // 取本次要执行的 SQL：有选中就只执行选中部分
  const pickSql = (): { text: string; selected: boolean } => {
    const ta = taRef.current?.resizableTextArea?.textArea
    if (ta) {
      const a = ta.selectionStart ?? 0
      const b = ta.selectionEnd ?? 0
      if (b > a) return { text: sql.slice(a, b), selected: true }
    }
    return { text: sql, selected: false }
  }

  // 校验：必须以分号结尾；一次只能一条（多条请选中一条执行）
  const validateSqlText = (text: string): string => {
    const t = text.trim()
    if (!t) return 'SQL 不能为空'
    if (!t.endsWith(';')) return 'SQL 语句必须以分号结尾（;）'
    const body = t.slice(0, -1)
    if (body.includes(';')) return '检测到多条语句：请选中要执行的那一条（每条以分号结尾）'
    return ''
  }

  const run = async (write: boolean) => {
    if (!cur) return
    const { text } = pickSql()
    const why = validateSqlText(text)
    if (why) { setErr(why); setRes(null); return }
    const payloadSql = text.trim().slice(0, -1) // 去掉结尾分号再下发
    setBusy(true); reset()
    try {
      setRes((write ? await execDBConnection(cur.id, payloadSql) : await queryDBConnection(cur.id, payloadSql)) as Result)
    } catch (e) {
      const x = e as { response?: { data?: { message?: string } }; message?: string }
      setErr(x?.response?.data?.message || x?.message || '执行失败')
    } finally { setBusy(false) }
  }

  const download = async () => {
    if (!cur) return
    const { text } = pickSql()
    const why = validateSqlText(text)
    if (why) { setErr(why); return }
    try {
      const blob = await downloadDBQuery(cur.id, text.trim().slice(0, -1))
      const url = URL.createObjectURL(blob as Blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${cur.name || 'db'}-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '')}.csv`
      document.body.appendChild(a); a.click(); a.remove()
      URL.revokeObjectURL(url)
    } catch (e) {
      const x = e as { response?: { data?: { message?: string } }; message?: string }
      setErr(x?.response?.data?.message || x?.message || '导出失败')
    }
  }

  // ---------- 对象 / 导入导出 ----------
  const openObject = async (kind: string, name: string) => {
    if (!cur) return
    if (kind === 'table' || kind === 'view') {
      setSql(`select * from \`${name}\` limit 100;`)
      return
    }
    try {
      const r = await getObjectDDL(cur.id, kind, name, cur.database)
      if (r?.ddl) setSql(r.ddl)
      else message.warning('未获取到 DDL')
    } catch { /* ignore */ }
  }

  const doExport = async (scope: 'db' | 'table') => {
    if (!cur) return
    if (scope === 'table') {
      message.info('请在对象树里点表名右侧的导出，或先在 SQL 里选中表相关语句')
      return
    }
    try {
      const r = await exportDBObjects(cur.id, { scope, rows: 1000 })
      const blob = new Blob([r?.sql || ''], { type: 'text/plain;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = `${cur.name}-${new Date().toISOString().slice(0, 10)}.sql`
      document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url)
      message.success(`已导出 ${r?.tables ?? 0} 张表 / ${r?.rows ?? 0} 行${r?.truncated ? '（超 5MB 已截断）' : ''}`)
    } catch { /* 拦截器已提示 */ }
  }

  const doExportTable = async (table: string) => {
    if (!cur) return
    try {
      const r = await exportDBObjects(cur.id, { scope: 'table', table, rows: 5000 })
      const blob = new Blob([r?.sql || ''], { type: 'text/plain;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = `${table}-${new Date().toISOString().slice(0, 10)}.sql`
      document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url)
      message.success(`已导出 ${table}（${r?.rows ?? 0} 行）`)
    } catch { /* ignore */ }
  }

  const doImport = (file: File) => {
    if (!cur) return
    const fmt = file.name.toLowerCase().endsWith('.csv') ? 'csv' : 'sql'
    const table = fmt === 'csv' ? (window.prompt('CSV 导入到哪张表？填表名') || '') : ''
    if (fmt === 'csv' && !table) return
    if (!window.confirm(`确认导入 ${file.name}（${Math.round(file.size / 1024)} KB）？会真实写库，无法自动回滚。`)) return
    const reader = new FileReader()
    reader.onload = async () => {
      try {
        const r = await importDBObjects(cur.id, {
          format: fmt, table, content: String(reader.result || ''), confirm: true,
        })
        message.success(`导入完成：执行 ${r?.executed ?? 0} 条`)
      } catch { /* ignore */ }
    }
    reader.readAsText(file)
  }

  // ---------- 定时任务 ----------
  const refreshCounts = async () => {
    try {
      const rows = await getSqlTaskCounts()
      const m: Record<number, number> = {}
      for (const r of rows || []) m[r.db_connection_id] = r.total
      setCounts(m)
    } catch { /* ignore */ }
  }

  const refreshTasks = async (cid?: number) => {
    const id = cid ?? cur?.id
    if (!id) return
    try { setTasks((await listSqlTasks(id)) || []) } catch { setTasks([]) }
  }

  const openTasks = async (cid?: number) => {
    if (cid && cid !== cur?.id) onSelect(cid)
    setTasksOpen(true)
    setEditTaskId(null)
    taskForm.resetFields()
    taskForm.setFieldsValue({ cron_expression: '0 9 1 * *', is_enabled: true, recipients: '' })
    await refreshTasks(cid)
    await refreshCounts()
  }

  useEffect(() => { refreshCounts() }, [conns.length])

  const startEditTask = (t: SqlTaskRow) => {
    setEditTaskId(t.id)
    taskForm.setFieldsValue({
      name: t.name, cron_expression: t.cron_expression, recipients: t.recipients, is_enabled: t.is_enabled,
    })
    setSql(t.sql)
  }

  const saveTask = async () => {
    if (!cur) return
    const why = validateSqlText(sql)
    if (why) { message.error(why); return }
    const v = await taskForm.validateFields()
    const payload = { ...v, sql }
    try {
      if (editTaskId) await updateSqlTask(cur.id, editTaskId, payload)
      else await createSqlTask(cur.id, payload)
      message.success(editTaskId ? '已更新' : '已创建')
      setEditTaskId(null)
      taskForm.resetFields()
      taskForm.setFieldsValue({ cron_expression: '0 9 1 * *', is_enabled: true, recipients: '' })
      await refreshTasks()
      await refreshCounts()
    } catch { /* 拦截器已提示 */ }
  }

  const runTaskNow = async (t: SqlTaskRow, dryRun: boolean) => {
    if (!cur) return
    try {
      const r = await runSqlTask(cur.id, t.id, dryRun)
      if (dryRun) message.success(`测试通过，返回 ${r?.rows ?? 0} 行（未发邮件）`)
      else message.success(`已执行并发送${r?.rows != null ? `（${r.rows} 行）` : ''}`)
      await refreshTasks()
    } catch { /* 拦截器已提示（如 SMTP 未配置） */ }
  }

  const removeTask = async (t: SqlTaskRow) => {
    if (!cur) return
    try { await deleteSqlTask(cur.id, t.id); await refreshTasks(); await refreshCounts() } catch { /* ignore */ }
  }

  return (
    <div style={{ display: 'flex', gap: 12, height: 'calc(100vh - 250px)', minHeight: 460 }}>
      {/* 左：实例列表 */}
      <div style={{ width: 280, flex: '0 0 280px', display: 'flex', flexDirection: 'column', borderRight: '1px solid #f0f0f0', paddingRight: 10 }}>
        <Button type="primary" icon={<PlusOutlined />} block style={{ marginBottom: 8 }} onClick={onAdd}>添加数据库连接</Button>
        <Input.Search size="small" allowClear placeholder="搜索实例 / 库" value={kw}
          onChange={(e) => setKw(e.target.value)} style={{ marginBottom: 8 }} />
        <div style={{ flex: 1, overflow: 'auto' }}>
          {filtered.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无数据库" /> : (
            <List
              size="small"
              dataSource={filtered}
              renderItem={(c) => {
                const active = c.id === selectedId
                const agent = (c.source || 'direct') === 'agent'
                return (
                  <div
                    onClick={() => { onSelect(c.id); reset() }}
                    style={{
                      padding: '6px 8px', borderRadius: 6, cursor: 'pointer', marginBottom: 2,
                      background: active ? '#e6f4ff' : 'transparent',
                      border: active ? '1px solid #91caff' : '1px solid transparent',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span style={{
                        width: 7, height: 7, borderRadius: 4, flex: '0 0 7px',
                        background: c.status === 'connected' ? '#52c41a' : c.status === 'failed' ? '#ff4d4f' : '#d9d9d9',
                      }} />
                      <span style={{ fontWeight: active ? 600 : 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={c.name}>
                        {c.name}
                      </span>
                    </div>
                    <div style={{ fontSize: 11, color: '#8c8c8c', marginLeft: 13, display: 'flex', gap: 4, alignItems: 'center' }}>
                      <Tag bordered={false} color={agent ? 'purple' : 'default'} style={{ marginInlineEnd: 0, fontSize: 10, lineHeight: '16px' }}>
                        {agent ? `agent·${c.cluster_id ?? '-'}` : '直连'}
                      </Tag>
                      <span>{c.db_type}{c.database ? ` / ${c.database}` : ''}</span>
                      {c.status === 'removed' && <Tag color="red" style={{ fontSize: 10, lineHeight: '16px' }}>已下线</Tag>}
                      {(counts[c.id] || 0) > 0 && (
                        <Tag color="blue" style={{ fontSize: 10, lineHeight: '16px' }}>任务 {counts[c.id]}</Tag>
                      )}
                      <span style={{ flex: 1 }} />
                      <Tooltip title="定时任务">
                        <ClockCircleOutlined
                          style={{ fontSize: 12, color: (counts[c.id] || 0) > 0 ? '#1677ff' : '#bfbfbf' }}
                          onClick={(e) => { e.stopPropagation(); onSelect(c.id); openTasks(c.id) }}
                        />
                      </Tooltip>
                    </div>
                  </div>
                )
              }}
            />
          )}
        </div>
      </div>

      {/* 中：对象树（表/视图/函数/存储过程/事件） */}
      <div style={{ width: 210, flex: '0 0 210px', borderRight: '1px solid #f0f0f0', paddingRight: 8, overflow: 'auto' }}>
        {!cur ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择实例" /> : (
          <>
            <div style={{ fontSize: 11, color: '#8c8c8c', marginBottom: 4 }}>
              {cur.database || '-'} · {schema.tables.length} 表 / {schema.views.length} 视图
            </div>
            {groups.map((g) => (
              <div key={g.key} style={{ marginBottom: 4 }}>
                <div
                  onClick={() => setOpenGroups((m) => ({ ...m, [g.key]: !m[g.key] }))}
                  style={{ cursor: 'pointer', fontSize: 12, fontWeight: 600, padding: '3px 2px', color: '#595959' }}
                >
                  {openGroups[g.key] ? '▾' : '▸'} {g.label}（{g.items.length}）
                </div>
                {openGroups[g.key] && g.items.map((it) => (
                  <div
                    key={`${g.key}-${it.name}`}
                    onClick={() => openObject(g.kind, it.name)}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer',
                      fontSize: 12, padding: '2px 4px 2px 12px', borderRadius: 4, color: '#262626',
                    }}
                    onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = '#f5f5f5' }}
                    onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = 'transparent' }}
                  >
                    {g.kind === 'table' ? <TableOutlined style={{ fontSize: 11, color: '#8c8c8c' }} />
                      : g.kind === 'view' ? <EyeOutlined style={{ fontSize: 11, color: '#8c8c8c' }} />
                      : g.kind === 'event' ? <FieldTimeOutlined style={{ fontSize: 11, color: '#8c8c8c' }} />
                      : <FunctionOutlined style={{ fontSize: 11, color: '#8c8c8c' }} />}
                    <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={it.name}>{it.name}</span>
                    {g.kind === 'table' && (
                      <Tooltip title="导出该表">
                        <ExportOutlined style={{ fontSize: 11, color: '#bfbfbf' }}
                          onClick={(e) => { e.stopPropagation(); doExportTable(it.name) }} />
                      </Tooltip>
                    )}
                  </div>
                ))}
              </div>
            ))}
            {schema.tables.length + schema.views.length === 0 && (
              <div style={{ fontSize: 11, color: '#bfbfbf' }}>无对象（或直连库不支持）</div>
            )}
          </>
        )}
      </div>

      {/* 右：操作条 + SQL + 结果 */}
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
        {!cur ? <Empty description="请选择左侧数据库" style={{ marginTop: 80 }} /> : (
          <>
            <Space wrap size={6}>
              <b>{cur.name}</b>
              <span style={{ fontSize: 12, color: '#8c8c8c' }}>{cur.host}:{cur.port}{cur.database ? ` / ${cur.database}` : ''}</span>
              <Button size="small" type="primary" icon={<PlayCircleOutlined />} onClick={() => onDiagnose(cur)}>诊断</Button>
              <Button size="small" icon={<ThunderboltOutlined />} onClick={() => onTest(cur)}>测试连接</Button>
              <Button size="small" icon={<ReloadOutlined />} onClick={() => onCollect(cur)}>采集</Button>
              <Button size="small" icon={<EditOutlined />} onClick={() => onEdit(cur)}>编辑</Button>
              <Popconfirm title="确认删除该连接？" onConfirm={() => onDelete(cur)}>
                <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
              </Popconfirm>
              <Tooltip title="采集开关"><span style={{ fontSize: 12, color: '#8c8c8c' }}>采集</span></Tooltip>
              <Switch size="small" checked={!!cur.collect_enabled} onChange={(v) => onToggleCollect(cur, v)} />
              <Tooltip title="巡检开关（关闭后不参与诊断报告生成）"><span style={{ fontSize: 12, color: '#8c8c8c' }}>巡检</span></Tooltip>
              <Switch size="small" checked={!!cur.inspect_enabled} onChange={(v) => onToggleInspect(cur, v)} />
              {!isAgent && <Tag color="cyan">直连库（平台直连执行）</Tag>}
            </Space>

            <div style={{ position: 'relative' }}>
              <Input.TextArea
                ref={taRef as never}
                value={sql}
                onChange={(e) => { setSql(e.target.value); syncSuggest(e.target.value, e.target.selectionStart ?? 0) }}
                onSelect={(e) => {
                  const el = e.target as HTMLTextAreaElement
                  setSelLen(Math.max(0, (el.selectionEnd ?? 0) - (el.selectionStart ?? 0)))
                }}
                onBlur={() => setTimeout(() => setSugg(null), 120)}
                disabled={!canSql}
                placeholder={'select * from information_schema.tables limit 10\n-- 或 update t set c=1 where id=1'}
                style={{ fontFamily: 'Menlo, Consolas, monospace', fontSize: 13, resize: 'none', height: 130 }}
                onKeyDown={(e) => {
                  if (sugg) {
                    if (e.key === 'ArrowDown') { e.preventDefault(); setSugg({ ...sugg, idx: (sugg.idx + 1) % sugg.items.length }); return }
                    if (e.key === 'ArrowUp') { e.preventDefault(); setSugg({ ...sugg, idx: (sugg.idx - 1 + sugg.items.length) % sugg.items.length }); return }
                    if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); applySugg(sugg.items[sugg.idx]); return }
                    if (e.key === 'Escape') { e.preventDefault(); setSugg(null); return }
                  }
                  if ((e.ctrlKey || e.metaKey) && e.key === ' ') {
                    e.preventDefault()
                    const el = e.target as HTMLTextAreaElement
                    syncSuggest(sql, el.selectionStart ?? 0)
                    return
                  }
                  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && canSql && sql.trim() && isRead) run(false)
                }}
              />
              <div ref={mirrorRef} style={{
                position: 'absolute', left: -99999, top: 0, visibility: 'hidden',
                whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontFamily: 'Menlo, Consolas, monospace',
                fontSize: 13, pointerEvents: 'none',
              }} />
              {sugg && (
                <div style={{
                  position: 'absolute', top: caretPos.top, left: caretPos.left, zIndex: 20, width: 320,
                  background: '#fff', border: '1px solid #d9d9d9', borderRadius: 6,
                  boxShadow: '0 6px 16px rgba(0,0,0,.12)', maxHeight: 220, overflow: 'auto',
                }}>
                  {sugg.items.map((it, i) => (
                    <div
                      key={it}
                      onMouseDown={(e) => { e.preventDefault(); applySugg(it) }}
                      style={{
                        padding: '4px 10px', fontSize: 12, cursor: 'pointer',
                        fontFamily: 'Menlo, Consolas, monospace',
                        background: i === sugg.idx ? '#e6f4ff' : 'transparent',
                      }}
                    >{it}</div>
                  ))}
                  <div style={{ padding: '3px 10px', fontSize: 11, color: '#bfbfbf', borderTop: '1px solid #f5f5f5' }}>
                    ↑↓ 选择 · Enter/Tab 插入 · Esc 关闭
                  </div>
                </div>
              )}
            </div>

            <Space wrap>
              {isRead ? (
                <Button type="primary" loading={busy} disabled={!canSql || !sql.trim()} onClick={() => run(false)}>
                  执行（Ctrl/Cmd+Enter）
                </Button>
              ) : (
                <Popconfirm title="确认执行写操作？" description="将真实修改数据库，无法自动回滚。"
                  okText="确认执行" cancelText="取消" okButtonProps={{ danger: true }} onConfirm={() => run(true)}>
                  <Button danger loading={busy} disabled={!canSql || !sql.trim()}>执行（写）</Button>
                </Popconfirm>
              )}
              <Button icon={<DownloadOutlined />} disabled={!canSql || !sql.trim() || !isRead} onClick={download}>下载 CSV</Button>
              <Button icon={<ClockCircleOutlined />} disabled={!canSql} onClick={() => openTasks()}>定时任务</Button>
              <Button icon={<ExportOutlined />} disabled={!canSql} onClick={() => doExport('db')}>导出库</Button>
              <Popconfirm title="导入 SQL/CSV 会真实写库，确定继续？"
                onConfirm={() => { const el = document.getElementById('db-import-file') as HTMLInputElement | null; el?.click() }}>
                <Button icon={<ImportOutlined />} disabled={!canSql}>导入</Button>
              </Popconfirm>
              <input id="db-import-file" type="file" accept=".sql,.csv" style={{ display: 'none' }}
                onChange={(e) => { const f = e.target.files?.[0]; if (f) doImport(f); e.currentTarget.value = '' }} />
              {res?.affected_rows !== undefined && (
                <Tag color={(res.affected_rows || 0) > 0 ? 'green' : 'default'}>影响行数：{res.affected_rows}</Tag>
              )}
              <span style={{ fontSize: 12, color: '#8c8c8c' }}>
                {selLen > 0 ? `将执行选中内容（${selLen} 字符）` : '将执行全部内容'}
                {' · '}语句必须以分号结尾
                {sql.trim() ? (isRead ? ' · 只读（≤200 行）' : ` · 写语句（${head}）`) : ''}
              </span>
            </Space>

            {err && <div style={{ color: '#ff4d4f', fontSize: 13 }}>{err}</div>}

            <div style={{ flex: 1, minHeight: 0, border: '1px solid #f0f0f0', borderRadius: 6, overflow: 'auto' }}>
              {!res ? (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="执行后在这里看结果" style={{ marginTop: 40 }} />
              ) : (res.rows || []).length === 0 ? (
                <div style={{ padding: 12, color: '#8c8c8c', fontSize: 13 }}>
                  执行成功{res.affected_rows !== undefined ? `，影响 ${res.affected_rows} 行` : '，无返回行'}
                </div>
              ) : (
                <>
                  <div style={{ padding: '6px 10px', fontSize: 12, color: '#8c8c8c' }}>
                    返回 {(res.rows || []).length} 行{res.truncated ? '（已截断）' : ''}
                  </div>
                  <Table
                    size="small"
                    rowKey={(_, i) => String(i)}
                    dataSource={(res.rows || []).map((r, i) => ({ __i: i, r }))}
                    columns={(res.columns || []).map((c, idx) => ({
                      title: c, dataIndex: ['r', idx], render: (v: unknown) => (v === null ? 'NULL' : String(v)),
                    }))}
                    pagination={{ pageSize: 20, showSizeChanger: false, size: 'small' }}
                    scroll={{ x: 'max-content' }}
                  />
                </>
              )}
            </div>
          </>
        )}

      <Modal
        title={`SQL 定时任务：${cur?.name || ''}`}
        open={tasksOpen}
        onCancel={() => setTasksOpen(false)}
        footer={null}
        width={860}
      >
        <Form form={taskForm} layout="inline" style={{ marginBottom: 10, rowGap: 8 }}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '必填' }]}>
            <Input placeholder="如 月度订单报表" style={{ width: 170 }} />
          </Form.Item>
          <Form.Item name="cron_expression" label="Cron" rules={[{ required: true, message: '必填' }]}
            tooltip="5 段：分 时 日 月 周。每月 1 号 9:00 = 0 9 1 * *">
            <Input placeholder="0 9 1 * *" style={{ width: 120 }} />
          </Form.Item>
          <Form.Item name="recipients" label="收件邮箱" rules={[{ required: true, message: '必填' }]}
            tooltip="多个用逗号分隔；也可填平台用户名（取其邮箱）">
            <Input placeholder="a@x.com,b@y.com" style={{ width: 240 }} />
          </Form.Item>
          <Form.Item name="is_enabled" label="启用" valuePropName="checked"><Switch /></Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" onClick={saveTask}>{editTaskId ? '保存修改' : '新建（用当前 SQL）'}</Button>
              {editTaskId && <Button onClick={() => { setEditTaskId(null); taskForm.resetFields(); taskForm.setFieldsValue({ cron_expression: '0 9 1 * *', is_enabled: true, recipients: '' }) }}>取消编辑</Button>}
            </Space>
          </Form.Item>
        </Form>

        <Table
          size="small"
          rowKey="id"
          dataSource={tasks}
          pagination={false}
          columns={[
            { title: '名称', dataIndex: 'name' },
            { title: 'Cron', dataIndex: 'cron_expression', width: 110 },
            { title: '收件人', dataIndex: 'recipients', ellipsis: true },
            { title: '上次执行', dataIndex: 'last_run_at', width: 150 },
            {
              title: '状态', dataIndex: 'last_status', width: 90,
              render: (v: string) => <Tag color={v === 'success' ? 'green' : v === 'failed' ? 'red' : 'default'}>{v || '-'}</Tag>,
            },
            {
              title: '操作', width: 230, render: (_: unknown, t: SqlTaskRow) => (
                <Space size={4}>
                  <Button size="small" onClick={() => startEditTask(t)}>编辑</Button>
                  <Button size="small" type="primary" onClick={() => runTaskNow(t, false)}>立即执行</Button>
                  <Button size="small" onClick={() => runTaskNow(t, true)}>测试</Button>
                  <Popconfirm title="删除该任务？" onConfirm={() => removeTask(t)}>
                    <Button size="small" danger>删除</Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
        <div style={{ marginTop: 8, fontSize: 12, color: '#8c8c8c' }}>
          任务执行的是<b>当前编辑器里的 SQL</b>（保存时取当前内容）；只允许只读语句；
          结果以 CSV 附件 + 邮件正文摘要发送。平台需先配置 SMTP（系统设置 → 通知/邮件）。
        </div>
      </Modal>
      </div>
    </div>
  )
}
