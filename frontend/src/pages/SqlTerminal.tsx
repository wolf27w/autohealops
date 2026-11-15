// 独立整页 SQL 终端（新标签页打开，不套平台 MainLayout）：客户端级界面。
// 布局：顶部工具栏 + 左侧资源树（实例→库→表/视图/函数/事件）+ 右侧上半 SQL 编辑器 + 下半多结果区。
// 支持：切换当前库、表级操作（DDL/导出/删除）、Ctrl/Cmd+Enter 执行、多结果标签、导入 SQL、导出、备份。
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Button, Tag, Space, Table, Empty, Popconfirm, Modal, message, Tabs, Radio, Input, Select, Alert, Collapse,
} from 'antd'
import {
  ReloadOutlined, ImportOutlined, ExportOutlined, CloudDownloadOutlined, PlayCircleOutlined, EyeOutlined, DeleteOutlined,
} from '@ant-design/icons'
import DbInstanceTree, { type DbTreeConn, type DbTreeSelectNode } from '../components/db/DbInstanceTree'
import {
  getDBConnections, queryDBConnection, execDBConnection, getObjectDDL, exportDBObjects,
  importSqlScript, downloadDBBackup, getDBSchema, createDatabase, dropDatabase, type SqlImportResult,
} from '../services/api'

// ---------- 后端返回的连接行类型（getDBConnections 返回 unknown，这里按需断言） ----------
interface ConnRow {
  id: number
  name: string
  db_type?: string
  database?: string
  group_name?: string
  host?: string
  port?: number
  source?: string
  status?: string
  cluster_id?: number | null
}

const toTreeConn = (r: ConnRow): DbTreeConn => ({
  id: r.id, name: r.name, db_type: r.db_type, database: r.database,
  group_name: r.group_name, status: r.status, source: r.source,
})

// ---------- 结果相关类型 ----------
interface ResultItem {
  sql: string
  ok: boolean
  err?: string
  columns?: string[]
  rows?: unknown[][]
  affected?: number
  truncated?: boolean
  elapsed?: number
}
interface ResultGroup { key: number; label: string; time: string; items: ResultItem[] }

// 选中对象（表/视图/函数/事件）→ 右侧对象操作区
interface SelectedObj { kind: 'table' | 'view' | 'routine' | 'event'; db: string; name: string }

const SYS_DBS = new Set(['information_schema', 'performance_schema', 'mysql', 'sys'])
const READ_HEADS = new Set(['select', 'show', 'desc', 'describe', 'explain', 'with'])

// 把树节点 kind 映射到 object-ddl 接口的 kind
const DDL_KIND: Record<SelectedObj['kind'], string> = {
  table: 'table', view: 'view', routine: 'function', event: 'event',
}

// 按分号拆分（忽略引号/反引号内的分号）；尾巴无分号视为缺结尾
function splitStatements(text: string): { list: string[]; missingEnd: boolean } {
  const list: string[] = []
  let cur = ''
  let quote: string | null = null
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (quote) {
      cur += ch
      if (ch === quote && text[i - 1] !== '\\') quote = null
      continue
    }
    if (ch === "'" || ch === '"' || ch === '`') { quote = ch; cur += ch; continue }
    if (ch === ';') { if (cur.trim()) list.push(cur.trim()); cur = ''; continue }
    cur += ch
  }
  const rest = cur.trim()
  return { list, missingEnd: !!rest }
}

const isReadStmt = (st: string) => READ_HEADS.has((st.trim().split(/\s+/)[0] || '').toLowerCase())
const errMsg = (e: unknown) => {
  const x = e as { response?: { data?: { message?: string } }; message?: string }
  return x?.response?.data?.message || x?.message || '执行失败'
}
// 导出文件下载（blob + 触发 a 标签下载）
function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
const ts = () => new Date().toISOString().slice(0, 19).replace(/[:T]/g, '')

export default function SqlTerminal() {
  const [conns, setConns] = useState<DbTreeConn[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [currentDb, setCurrentDb] = useState<string | null>(null)
  const [selObj, setSelObj] = useState<SelectedObj | null>(null)

  // 编辑器
  const [sql, setSql] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  // 多结果
  const [groups, setGroups] = useState<ResultGroup[]>([])
  const [activeKey, setActiveKey] = useState<number | null>(null)
  const groupSeq = useRef(0)

  // 库列表（导出/备份多选用）
  const [dbList, setDbList] = useState<string[]>([])

  // 导入 SQL 弹窗
  const [importOpen, setImportOpen] = useState(false)
  const [importMode, setImportMode] = useState<'stop' | 'skip'>('stop')
  const [importText, setImportText] = useState('')
  const [importLoading, setImportLoading] = useState(false)
  const [importResult, setImportResult] = useState<SqlImportResult | null>(null)

  // 导出弹窗
  const [exportOpen, setExportOpen] = useState(false)
  const [exportMode, setExportMode] = useState<'structure' | 'data' | 'both'>('both')
  const [exportDbs, setExportDbs] = useState<string[]>([])
  const [exportLoading, setExportLoading] = useState(false)

  // 备份弹窗
  const [backupOpen, setBackupOpen] = useState(false)
  const [backupDbs, setBackupDbs] = useState<string[]>([])
  const [backupSchemaOnly, setBackupSchemaOnly] = useState(false)
  const [backupLoading, setBackupLoading] = useState(false)

  // DDL 查看弹窗
  const [ddlOpen, setDdlOpen] = useState(false)
  const [ddlText, setDdlText] = useState('')
  const [ddlTitle, setDdlTitle] = useState('')

  const cur = useMemo(() => conns.find((c) => c.id === selectedId) || null, [conns, selectedId])

  const load = () => {
    setLoading(true)
    getDBConnections()
      .then((d) => {
        const rows = (d as unknown as ConnRow[]) || []
        const mapped = rows.map(toTreeConn)
        setConns(mapped)
        setSelectedId((prev) => (mapped.some((c) => c.id === prev) ? prev : (mapped[0]?.id ?? null)))
      })
      .catch(() => message.error('加载数据库连接失败'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  // 切换实例：重置当前库/选中对象，并拉取库列表（导出/备份用）
  useEffect(() => {
    setCurrentDb(null)
    setSelObj(null)
    if (!cur) { setDbList([]); return }
    let alive = true
    getDBSchema(cur.id)
      .then((d) => {
        if (!alive) return
        const raw = d as unknown as { databases?: string[] }
        setDbList((raw?.databases || []).filter((x) => !SYS_DBS.has(x)))
      })
      .catch(() => { if (alive) setDbList([]) })
    return () => { alive = false }
  }, [cur?.id])

  // ---------- 资源树回调 ----------
  const handleSelectNode = (node: DbTreeSelectNode) => {
    if (node.kind === 'db') {
      setCurrentDb(node.db ?? null)
      setSelObj(null)
    } else if (node.kind === 'table' || node.kind === 'view' || node.kind === 'routine' || node.kind === 'event') {
      setSelObj({ kind: node.kind, db: node.db ?? '', name: node.name ?? '' })
    }
  }

  // ---------- 执行 ----------
  const pushGroup = (items: ResultItem[]) => {
    const key = ++groupSeq.current
    const g: ResultGroup = { key, label: `执行 ${key}`, time: new Date().toLocaleTimeString(), items }
    setGroups((prev) => {
      const next = [...prev, g]
      return next.length > 5 ? next.slice(next.length - 5) : next
    })
    setActiveKey(key)
  }

  const closeGroup = (key: number) => {
    setGroups((prev) => prev.filter((g) => g.key !== key))
  }

  const run = async () => {
    if (!cur) return
    const { list, missingEnd } = splitStatements(sql)
    if (missingEnd) { setErr('SQL 语句必须以分号结尾（;）'); return }
    if (!list.length) { setErr('SQL 不能为空'); return }

    const writes = list.filter((st) => !isReadStmt(st))
    if (writes.length && !window.confirm(`包含 ${writes.length} 条写操作，将真实修改数据库且无法自动回滚，继续？`)) return

    setBusy(true)
    setErr('')
    const items: ResultItem[] = []

    // 当前库：随请求下发给后端（直连库会在连接时选库；agent 库忽略该参数，沿用 agent 侧默认库）
    const useDb = currentDb || cur.database || undefined

    for (const st of list) {
      const t0 = Date.now()
      try {
        const d = (isReadStmt(st) ? await queryDBConnection(cur.id, st, useDb) : await execDBConnection(cur.id, st, useDb)) as {
          columns?: string[]; rows?: unknown[][]; affected_rows?: number; truncated?: boolean
        }
        items.push({
          sql: st, ok: true, columns: d?.columns, rows: d?.rows,
          affected: d?.affected_rows, truncated: d?.truncated, elapsed: Date.now() - t0,
        })
      } catch (e) {
        items.push({ sql: st, ok: false, err: errMsg(e), elapsed: Date.now() - t0 })
      }
    }
    setBusy(false)
    pushGroup(items)
  }

  // ---------- 表级/对象操作 ----------
  const viewDDL = async (o: SelectedObj) => {
    if (!cur) return
    try {
      const r = await getObjectDDL(cur.id, DDL_KIND[o.kind], o.name, o.db)
      if (r?.ddl) { setDdlText(r.ddl); setDdlTitle(`${o.db}.${o.name}`); setDdlOpen(true) }
      else message.warning('未获取到 DDL')
    } catch { /* 拦截器已提示 */ }
  }

  const exportTable = async (o: SelectedObj) => {
    if (!cur) return
    try {
      const r = await exportDBObjects(cur.id, { scope: 'table', table: o.name, rows: 5000 })
      const blob = new Blob([(r as { sql?: string })?.sql || ''], { type: 'text/plain;charset=utf-8' })
      saveBlob(blob, `${o.name}-${ts()}.sql`)
      message.success(`已导出 ${o.name}`)
    } catch { /* 拦截器已提示 */ }
  }

  const dropTable = async (o: SelectedObj) => {
    if (!cur) return
    try {
      await execDBConnection(cur.id, `DROP TABLE \`${o.db}\`.\`${o.name}\``)
      message.success(`已删除表 ${o.db}.${o.name}`)
      setSelObj(null)
    } catch { /* 拦截器已提示 */ }
  }

  // ---------- 导入 SQL ----------
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const doImport = async () => {
    if (!cur) return
    const text = importText.trim()
    if (!text) { message.error('请选择文件或粘贴 SQL 内容'); return }
    if (!window.confirm('导入会真实写库且无法自动回滚，确定继续？')) return
    setImportLoading(true)
    setImportResult(null)
    try {
      const r = await importSqlScript(cur.id, text, importMode)
      setImportResult(r)
    } catch { /* 拦截器已提示 */ }
    finally { setImportLoading(false) }
  }
  const onImportFile = (f: File | undefined) => {
    if (!f) return
    const reader = new FileReader()
    reader.onload = () => setImportText(String(reader.result || ''))
    reader.readAsText(f)
  }

  // ---------- 导出 ----------
  const doExport = async () => {
    if (!cur) return
    if (!exportDbs.length) { message.error('请至少选择一个库'); return }
    setExportLoading(true)
    try {
      const r = await exportDBObjects(cur.id, { mode: exportMode, databases: exportDbs })
      const blob = new Blob([(r as { sql?: string })?.sql || ''], { type: 'text/plain;charset=utf-8' })
      saveBlob(blob, `${cur.name}-export-${ts()}.sql`)
      message.success('导出完成')
      setExportOpen(false)
    } catch { /* 拦截器已提示 */ }
    finally { setExportLoading(false) }
  }

  // ---------- 备份 ----------
  const doBackup = async () => {
    if (!cur) return
    if (!backupDbs.length) { message.error('请至少选择一个库'); return }
    setBackupLoading(true)
    try {
      const blob = await downloadDBBackup(cur.id, backupDbs, backupSchemaOnly)
      saveBlob(blob, `${cur.name}-backup-${ts()}.sql`)
      message.success('备份下载完成')
      setBackupOpen(false)
    } catch { /* 拦截器已提示 */ }
    finally { setBackupLoading(false) }
  }

  // 建库
  const createDb = () => {
    if (!cur) return
    let name = ''
    Modal.confirm({
      title: '新建数据库',
      content: (
        <Input
          placeholder="库名（字母/数字/下划线，≤64）"
          onChange={(e) => { name = e.target.value }}
          onPressEnter={(e) => { name = (e.target as HTMLInputElement).value }}
        />
      ),
      onOk: async () => {
        if (!/^[A-Za-z0-9_]{1,64}$/.test(name)) {
          message.error('库名仅允许字母/数字/下划线，长度 1~64')
          throw new Error('invalid name')
        }
        await createDatabase(cur.id, name)
        message.success(`已创建数据库 ${name}`)
        load()
      },
    })
  }

  // 删库（当前库）
  const dropDb = () => {
    if (!cur || !currentDb) return
    Modal.confirm({
      title: `删除数据库 ${currentDb}？`,
      content: '将执行 DROP DATABASE，数据不可恢复，请确认已在别处备份。',
      okButtonProps: { danger: true },
      okText: '删除',
      onOk: async () => {
        await dropDatabase(cur.id, currentDb)
        message.success(`已删除数据库 ${currentDb}`)
        setCurrentDb(null)
        load()
      },
    })
  }

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', background: '#fff' }}>
      {/* 顶部工具栏 */}
      <div style={{
        height: 48, flex: '0 0 48px', display: 'flex', alignItems: 'center',
        padding: '0 16px', borderBottom: '1px solid #f0f0f0', gap: 12,
      }}>
        <span style={{ fontSize: 16, fontWeight: 600 }}>SQL 终端</span>
        {cur && (
          <>
            <Tag color="blue">{cur.name}</Tag>
            <span style={{ fontSize: 12, color: '#8c8c8c' }}>
              当前库：<b style={{ color: '#1677ff' }}>{currentDb || (cur.database || '-')}</b>
            </span>
          </>
        )}
        <span style={{ flex: 1 }} />
        <Button icon={<ReloadOutlined />} loading={loading} onClick={load}>刷新</Button>
        <Button disabled={!cur} onClick={createDb}>新建库</Button>
        <Button danger disabled={!cur || !currentDb} onClick={dropDb}>删除库</Button>
        <Button icon={<ImportOutlined />} disabled={!cur} onClick={() => { setImportResult(null); setImportOpen(true) }}>导入 SQL</Button>
        <Button icon={<ExportOutlined />} disabled={!cur} onClick={() => { setExportDbs(currentDb ? [currentDb] : []); setExportOpen(true) }}>导出</Button>
        <Button icon={<CloudDownloadOutlined />} disabled={!cur} onClick={() => { setBackupDbs(currentDb ? [currentDb] : []); setBackupOpen(true) }}>备份</Button>
      </div>

      {/* 主体 */}
      <div style={{ flex: 1, minHeight: 0, display: 'flex' }}>
        {/* 左：资源树 */}
        <div style={{ width: 300, flex: '0 0 300px', borderRight: '1px solid #f0f0f0', padding: 10, overflow: 'hidden' }}>
          <DbInstanceTree
            conns={conns}
            activeId={selectedId}
            onOpen={(id) => setSelectedId(id)}
            onSelectNode={handleSelectNode}
          />
        </div>

        {/* 右：编辑器 + 结果 */}
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          {/* 编辑器区 */}
          <div style={{ flex: '0 0 auto', padding: '10px 12px 6px', borderBottom: '1px solid #f0f0f0' }}>
            {selObj && (
              <div style={{
                display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, padding: '6px 10px',
                background: '#fafafa', border: '1px solid #f0f0f0', borderRadius: 6,
              }}>
                <span style={{ fontSize: 12, color: '#595959' }}>
                  已选 <b>{selObj.kind === 'table' ? '表' : selObj.kind === 'view' ? '视图' : selObj.kind === 'routine' ? '函数' : '事件'}</b>：{selObj.db}.{selObj.name}
                </span>
                <span style={{ flex: 1 }} />
                <Button size="small" icon={<EyeOutlined />} onClick={() => viewDDL(selObj)}>查看结构</Button>
                {selObj.kind === 'table' && (
                  <>
                    <Button size="small" icon={<ExportOutlined />} onClick={() => exportTable(selObj)}>导出该表</Button>
                    <Popconfirm title={`确认删除表 ${selObj.db}.${selObj.name}？不可恢复`} onConfirm={() => dropTable(selObj)}>
                      <Button size="small" danger icon={<DeleteOutlined />}>删除表</Button>
                    </Popconfirm>
                  </>
                )}
              </div>
            )}
            <Input.TextArea
              value={sql}
              onChange={(e) => setSql(e.target.value)}
              placeholder={'select * from information_schema.tables limit 10;\n-- 多条语句用分号分隔，Ctrl/Cmd+Enter 执行'}
              style={{ fontFamily: 'Menlo, Consolas, monospace', fontSize: 13, resize: 'none', height: 150 }}
              onKeyDown={(e) => {
                if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && !busy && sql.trim()) { e.preventDefault(); run() }
              }}
            />
            <Space style={{ marginTop: 6 }} wrap>
              <Button type="primary" icon={<PlayCircleOutlined />} loading={busy} disabled={!cur || !sql.trim()} onClick={run}>
                执行（Ctrl/Cmd+Enter）
              </Button>
              <span style={{ fontSize: 12, color: '#8c8c8c' }}>
                每条以分号结尾 · 含写操作需确认
              </span>
            </Space>
            {err && <div style={{ color: '#ff4d4f', fontSize: 13, marginTop: 4 }}>{err}</div>}
          </div>

          {/* 结果区：多结果标签（最多 5 组，可关闭） */}
          <div style={{ flex: 1, minHeight: 0, padding: '0 12px 12px', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            {groups.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="执行后在这里看结果（每次执行一组，最多保留 5 组）" style={{ marginTop: 40 }} />
            ) : (
              <Tabs
                type="editable-card"
                hideAdd
                size="small"
                style={{ height: '100%' }}
                activeKey={activeKey != null ? String(activeKey) : undefined}
                onChange={(k) => setActiveKey(Number(k))}
                onEdit={(targetKey, action) => { if (action === 'remove') closeGroup(Number(targetKey)) }}
                items={groups.map((g) => ({
                  key: String(g.key),
                  label: `${g.label} ${g.time}`,
                  children: (
                    <div style={{ maxHeight: '100%', overflow: 'auto', padding: '4px 4px 0' }}>
                      {g.items.map((r, i) => (
                        <div key={i} style={{ marginBottom: 14 }}>
                          <div style={{ fontFamily: 'Menlo, Consolas, monospace', fontSize: 12, color: '#595959', marginBottom: 4, wordBreak: 'break-all' }}>
                            {i + 1}. {r.sql.replace(/\s+/g, ' ').slice(0, 100)}{r.sql.length > 100 ? '…' : ''}
                            {r.elapsed != null ? `（${r.elapsed} ms）` : ''}
                          </div>
                          {r.ok ? (
                            (r.rows || []).length === 0 ? (
                              <div style={{ padding: 8, color: '#8c8c8c', fontSize: 13 }}>
                                执行成功{r.affected !== undefined ? `，影响 ${r.affected} 行` : '，无返回行'}
                              </div>
                            ) : (
                              <div>
                                <div style={{ padding: '2px 2px', fontSize: 12, color: '#8c8c8c' }}>
                                  返回 {(r.rows || []).length} 行{r.truncated ? '（已截断）' : ''}
                                </div>
                                <Table
                                  size="small"
                                  rowKey={(_, idx) => String(idx)}
                                  dataSource={(r.rows || []).map((row, idx) => ({ __i: idx, row }))}
                                  columns={(r.columns || []).map((c, ci) => ({
                                    title: c, dataIndex: ['row', ci],
                                    render: (v: unknown) => (v === null ? 'NULL' : String(v)),
                                  }))}
                                  pagination={{ pageSize: 20, showSizeChanger: false, size: 'small' }}
                                  scroll={{ x: 'max-content' }}
                                />
                              </div>
                            )
                          ) : (
                            <Alert type="error" showIcon message={r.err} description={<code>{r.sql}</code>} />
                          )}
                        </div>
                      ))}
                    </div>
                  ),
                }))}
              />
            )}
          </div>
        </div>
      </div>

      {/* 导入 SQL 弹窗 */}
      <Modal
        title="导入 SQL"
        open={importOpen}
        onOk={doImport}
        onCancel={() => setImportOpen(false)}
        okText="开始导入"
        confirmLoading={importLoading}
        width={720}
        destroyOnClose
      >
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <div>
            <div style={{ marginBottom: 6, fontSize: 13 }}>执行模式</div>
            <Radio.Group value={importMode} onChange={(e) => setImportMode(e.target.value)}>
              <Radio.Button value="stop">遇错即停</Radio.Button>
              <Radio.Button value="skip">跳过继续</Radio.Button>
            </Radio.Group>
          </div>
          <div>
            <div style={{ marginBottom: 6, fontSize: 13 }}>SQL 内容（选择文件或直接粘贴）</div>
            <Space style={{ marginBottom: 6 }}>
              <Button onClick={() => fileInputRef.current?.click()}>选择 .sql 文件</Button>
              {importText && <Button size="small" onClick={() => setImportText('')}>清空</Button>}
              <span style={{ fontSize: 12, color: '#8c8c8c' }}>{importText ? `已载入 ${importText.length} 字符` : '不限大小'}</span>
            </Space>
            <input
              ref={fileInputRef}
              type="file"
              accept=".sql,text/plain"
              style={{ display: 'none' }}
              onChange={(e) => { onImportFile(e.target.files?.[0]); e.currentTarget.value = '' }}
            />
            <Input.TextArea
              rows={8}
              value={importText}
              onChange={(e) => setImportText(e.target.value)}
              placeholder="粘贴 SQL 脚本（多条用分号分隔）"
              style={{ fontFamily: 'Menlo, Consolas, monospace', fontSize: 12 }}
            />
          </div>
          {importResult && (
            <div style={{ border: '1px solid #f0f0f0', borderRadius: 6, padding: 10 }}>
              <Space wrap>
                <Tag color="green">执行 {importResult.executed} 条</Tag>
                {importResult.failed != null && <Tag color="red">失败 {importResult.failed} 条</Tag>}
                {importResult.skipped != null && <Tag color="orange">跳过 {importResult.skipped} 条</Tag>}
                {importResult.stopped && <Tag color="red">已中止（遇错即停）</Tag>}
              </Space>
              {importResult.errors?.length > 0 && (
                <Collapse
                  size="small"
                  style={{ marginTop: 8 }}
                  items={importResult.errors.map((e, i) => ({
                    key: String(i),
                    label: <span style={{ fontFamily: 'monospace' }}>#{e.index} {e.statement_head || '（无语句头）'}</span>,
                    children: <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-all', fontSize: 12 }}>{e.error}</pre>,
                  }))}
                />
              )}
            </div>
          )}
        </Space>
      </Modal>

      {/* 导出弹窗 */}
      <Modal
        title="导出数据库"
        open={exportOpen}
        onOk={doExport}
        onCancel={() => setExportOpen(false)}
        okText="导出"
        confirmLoading={exportLoading}
        destroyOnClose
      >
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <div>
            <div style={{ marginBottom: 6, fontSize: 13 }}>导出内容</div>
            <Radio.Group value={exportMode} onChange={(e) => setExportMode(e.target.value)}>
              <Radio.Button value="structure">仅结构</Radio.Button>
              <Radio.Button value="data">仅数据</Radio.Button>
              <Radio.Button value="both">结构 + 数据</Radio.Button>
            </Radio.Group>
          </div>
          <div>
            <div style={{ marginBottom: 6, fontSize: 13 }}>选择库（多选）</div>
            <Select
              mode="multiple"
              style={{ width: '100%' }}
              placeholder="选择要导出的库"
              value={exportDbs}
              onChange={(v) => setExportDbs(v)}
              options={dbList.map((d) => ({ label: d, value: d }))}
            />
          </div>
        </Space>
      </Modal>

      {/* 备份弹窗 */}
      <Modal
        title="备份数据库"
        open={backupOpen}
        onOk={doBackup}
        onCancel={() => setBackupOpen(false)}
        okText="备份下载"
        confirmLoading={backupLoading}
        destroyOnClose
      >
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <div>
            <div style={{ marginBottom: 6, fontSize: 13 }}>选择库（多选）</div>
            <Select
              mode="multiple"
              style={{ width: '100%' }}
              placeholder="选择要备份的库"
              value={backupDbs}
              onChange={(v) => setBackupDbs(v)}
              options={dbList.map((d) => ({ label: d, value: d }))}
            />
          </div>
          <Radio.Group value={backupSchemaOnly ? 'schema' : 'full'} onChange={(e) => setBackupSchemaOnly(e.target.value === 'schema')}>
            <Radio value="full">完整备份（结构 + 数据）</Radio>
            <Radio value="schema">仅结构</Radio>
          </Radio.Group>
        </Space>
      </Modal>

      {/* DDL 查看弹窗 */}
      <Modal
        title={`结构 DDL：${ddlTitle}`}
        open={ddlOpen}
        onCancel={() => setDdlOpen(false)}
        footer={<Button onClick={() => setDdlOpen(false)}>关闭</Button>}
        width={760}
      >
        <pre style={{
          margin: 0, maxHeight: '60vh', overflow: 'auto', background: '#fafafa',
          padding: 12, borderRadius: 6, fontFamily: 'Menlo, Consolas, monospace', fontSize: 12, whiteSpace: 'pre-wrap',
        }}>{ddlText}</pre>
      </Modal>
    </div>
  )
}
