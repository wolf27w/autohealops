// 表结构设计器（R37 §6.6，2026-11-16）
// 能力：改字段（加/删/改名/改类型/可空/默认值/注释）、加删索引、表注释/引擎/字符集、重命名表。
// 安全：提交前先走后端 dry_run 拿 DDL 预览；命中审核策略（428）时把 SQL 交给父组件的审核弹窗。
import { useCallback, useEffect, useState } from 'react'
import {
  Alert, AutoComplete, Button, Empty, Input, Modal, Popconfirm, Space, Switch, Table, Tabs, Tag, message,
} from 'antd'
import { DeleteOutlined } from '@ant-design/icons'
import {
  alterTable, getTableSchema,
  type DbTableChanges, type DbTableColumn, type DbTableSchema, type DbTableIndex,
} from '../../services/api'
import TableCompareModal from './TableCompareModal'

export interface TableDesignerModalProps {
  open: boolean
  connId: number
  database: string
  table: string
  onClose: () => void
  /** 执行成功或提交审核后回调；newTable 为改名后的表名（未改名为原表名） */
  onApplied: (newTable: string) => void
  /** 命中审核策略：把生成的 DDL 交给父组件弹「提交审核」 */
  onNeedApproval: (sql: string) => void
}

interface ColRow {
  key: string
  orig?: DbTableColumn
  name: string
  type: string
  nullable: boolean
  def: string
  comment: string
  autoInc: boolean
  keyFlag: string
}

interface IdxRow {
  key: string
  orig?: DbTableIndex
  name: string
  columns: string
  unique: boolean
}

let rowSeq = 0
const nextKey = () => `row-${++rowSeq}`
const str = (v: unknown) => (v === null || v === undefined ? '' : String(v))

const TYPE_HINTS = [
  'INT', 'BIGINT', 'SMALLINT', 'TINYINT', 'VARCHAR(64)', 'VARCHAR(255)', 'TEXT', 'LONGTEXT',
  'DATETIME', 'TIMESTAMP', 'DATE', 'DECIMAL(10,2)', 'CHAR(36)', 'JSON', 'BOOLEAN',
]

// Oracle 只给这些类型（方言层亦会映射 MySQL 类型 → Oracle 类型，此处只做提示/下拉）
const ORACLE_TYPES = [
  'NUMBER', 'NUMBER(10)', 'NUMBER(19)', 'VARCHAR2(64)', 'VARCHAR2(255)', 'CHAR(36)',
  'DATE', 'TIMESTAMP', 'CLOB', 'BLOB', 'RAW(16)', 'FLOAT',
]

function toColRow(c: DbTableColumn): ColRow {
  return {
    key: nextKey(), orig: c, name: c.name, type: c.type || '', nullable: !!c.nullable,
    def: str(c.default), comment: c.comment || '', autoInc: (c.extra || '').toLowerCase().includes('auto_increment'),
    keyFlag: c.key || '',
  }
}

function toIdxRow(i: DbTableIndex): IdxRow {
  return { key: nextKey(), orig: i, name: i.name, columns: (i.columns || []).join(', '), unique: !!i.unique }
}

export default function TableDesignerModal({
  open, connId, database, table, onClose, onApplied, onNeedApproval,
}: TableDesignerModalProps) {
  const [loading, setLoading] = useState(false)
  const [schema, setSchema] = useState<DbTableSchema | null>(null)
  const [cols, setCols] = useState<ColRow[]>([])
  const [idxs, setIdxs] = useState<IdxRow[]>([])
  const [droppedCols, setDroppedCols] = useState<string[]>([])
  const [droppedIdx, setDroppedIdx] = useState<string[]>([])
  const [comment, setComment] = useState('')
  const [engine, setEngine] = useState('')
  const [charset, setCharset] = useState('')
  const [collation, setCollation] = useState('')
  const [renameTo, setRenameTo] = useState('')
  const [statements, setStatements] = useState<string[]>([])
  const [previewing, setPreviewing] = useState(false)
  const [executing, setExecuting] = useState(false)
  const [compareOpen, setCompareOpen] = useState(false)

  // 按方言切换：Oracle 下隐藏自增/引擎/字符集/排序规则，类型下拉换 Oracle 类型集
  const isOracle = (schema?.db_type || 'mysql').toLowerCase() === 'oracle'
  const typeHints = isOracle ? ORACLE_TYPES : TYPE_HINTS

  const reset = useCallback((s: DbTableSchema) => {
    setSchema(s)
    setCols((s.columns || []).map(toColRow))
    setIdxs((s.indexes || []).map(toIdxRow))
    setDroppedCols([])
    setDroppedIdx([])
    setComment(s.comment || '')
    setEngine(s.engine || '')
    setCharset('')
    setCollation('')
    setRenameTo('')
    setStatements([])
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const s = await getTableSchema(connId, database, table)
      reset(s)
    } catch {
      /* 拦截器已提示 */
    } finally {
      setLoading(false)
    }
  }, [connId, database, table, reset])

  useEffect(() => {
    if (open) void load()
  }, [open, load])

  const patchCol = (key: string, patch: Partial<ColRow>) => {
    setCols((prev) => prev.map((r) => (r.key === key ? { ...r, ...patch } : r)))
  }
  const patchIdx = (key: string, patch: Partial<IdxRow>) => {
    setIdxs((prev) => prev.map((r) => (r.key === key ? { ...r, ...patch } : r)))
  }

  const addCol = () => {
    setCols((prev) => [...prev, {
      key: nextKey(), name: '', type: isOracle ? 'VARCHAR2(255)' : 'VARCHAR(255)', nullable: true, def: '', comment: '', autoInc: false, keyFlag: '',
    }])
  }
  const removeCol = (row: ColRow) => {
    if (row.orig) setDroppedCols((prev) => [...prev, row.orig!.name])
    setCols((prev) => prev.filter((r) => r.key !== row.key))
  }
  const addIdx = () => {
    setIdxs((prev) => [...prev, { key: nextKey(), name: '', columns: '', unique: false }])
  }
  const removeIdx = (row: IdxRow) => {
    if (row.name.toUpperCase() === 'PRIMARY') {
      message.warning('主键索引不能在设计器里删除（请用 SQL 语句处理）')
      return
    }
    if (row.orig) setDroppedIdx((prev) => [...prev, row.orig!.name])
    setIdxs((prev) => prev.filter((r) => r.key !== row.key))
  }

  /** 计算与原始结构的差异；无改动返回 null */
  const buildChanges = (): DbTableChanges | null => {
    if (!schema) return null
    const ch: DbTableChanges = {}
    const addCols: NonNullable<DbTableChanges['add_columns']> = []
    const modCols: NonNullable<DbTableChanges['modify_columns']> = []
    const changeCols: NonNullable<DbTableChanges['change_columns']> = []

    for (const r of cols) {
      const name = r.name.trim()
      if (!name) continue
      const base = {
        name,
        type: r.type.trim(),
        nullable: r.nullable,
        comment: r.comment,
        default: r.def === '' ? null : r.def,
      }
      if (!r.orig) {
        addCols.push({ ...base, extra: r.autoInc ? 'auto_increment' : '' })
        continue
      }
      if (name !== r.orig.name) {
        changeCols.push({ ...base, from: r.orig.name, extra: r.autoInc ? 'auto_increment' : '' })
        continue
      }
      const changed = name !== r.orig.name
        || r.type.trim() !== (r.orig.type || '')
        || r.nullable !== !!r.orig.nullable
        || r.def !== str(r.orig.default)
        || r.comment !== (r.orig.comment || '')
        || r.autoInc !== (r.orig.extra || '').toLowerCase().includes('auto_increment')
      if (changed) modCols.push({ ...base, extra: r.autoInc ? 'auto_increment' : '' })
    }

    const addIdx: NonNullable<DbTableChanges['add_indexes']> = []
    for (const r of idxs) {
      const colsArr = r.columns.split(',').map((x) => x.trim()).filter(Boolean)
      if (!r.orig) {
        if (colsArr.length) addIdx.push({ name: r.name.trim() || undefined, columns: colsArr, unique: r.unique })
      }
    }

    if (addCols.length) ch.add_columns = addCols
    if (modCols.length) ch.modify_columns = modCols
    if (changeCols.length) ch.change_columns = changeCols
    if (droppedCols.length) ch.drop_columns = droppedCols
    if (addIdx.length) ch.add_indexes = addIdx
    if (droppedIdx.length) ch.drop_indexes = droppedIdx
    if (schema && comment !== (schema.comment || '')) ch.comment = comment
    if (schema && engine && engine !== (schema.engine || '')) ch.engine = engine
    if (charset.trim()) ch.charset = charset.trim()
    if (collation.trim()) ch.collation = collation.trim()
    const renamed = renameTo.trim()
    if (renamed && renamed !== table) ch.rename_table = renamed

    const touched = Object.keys(ch).length > 0
    return touched ? ch : null
  }

  const preview = async () => {
    const changes = buildChanges()
    if (!changes) { message.info('没有检测到改动'); return }
    setPreviewing(true)
    try {
      const r = await alterTable(connId, { database, table, changes, dry_run: true })
      setStatements(r.statements || [])
    } catch { /* 拦截器已提示 */ } finally { setPreviewing(false) }
  }

  const submit = async () => {
    const changes = buildChanges()
    if (!changes) { message.info('没有检测到改动'); return }
    setExecuting(true)
    let stmts: string[] = []
    try {
      const p = await alterTable(connId, { database, table, changes, dry_run: true })
      stmts = p.statements || []
      setStatements(stmts)
      await alterTable(connId, { database, table, changes, dry_run: false })
      message.success('表结构已更新')
      onApplied(renameTo.trim() || table)
      onClose()
    } catch (e) {
      const err = e as { needApproval?: boolean }
      if (err?.needApproval) {
        if (stmts.length) onNeedApproval(stmts.join(';\n'))
        else message.warning('该操作需要审核，请先点「预览 DDL」生成语句后再提交')
        onClose()
      }
    } finally { setExecuting(false) }
  }

  const colColumns = [
    {
      title: '字段名', dataIndex: 'name', width: 150,
      render: (_: unknown, r: ColRow) => (
        <Input size="small" value={r.name} placeholder="column_name"
          onChange={(e) => patchCol(r.key, { name: e.target.value })} />
      ),
    },
    {
      title: '类型', dataIndex: 'type', width: 170,
      render: (_: unknown, r: ColRow) => (
        <AutoComplete
          size="small"
          value={r.type}
          placeholder={isOracle ? 'VARCHAR2(255)' : 'VARCHAR(255)'}
          options={typeHints.map((t) => ({ value: t }))}
          onChange={(v) => patchCol(r.key, { type: v })}
          style={{ width: '100%' }}
        />
      ),
    },
    {
      title: '可空', dataIndex: 'nullable', width: 70,
      render: (v: boolean, r: ColRow) => (
        <Switch size="small" checked={v} onChange={(c) => patchCol(r.key, { nullable: c })} />
      ),
    },
    {
      title: '默认值', dataIndex: 'def', width: 150,
      render: (v: string, r: ColRow) => (
        <Input size="small" value={v} placeholder="NULL / 0 / CURRENT_TIMESTAMP"
          onChange={(e) => patchCol(r.key, { def: e.target.value })} />
      ),
    },
    {
      title: '注释', dataIndex: 'comment', width: 150,
      render: (v: string, r: ColRow) => (
        <Input size="small" value={v} onChange={(e) => patchCol(r.key, { comment: e.target.value })} />
      ),
    },
    {
      title: '属性', dataIndex: 'keyFlag', width: 110,
      render: (_: unknown, r: ColRow) => (
        <Space size={4} wrap>
          {r.orig ? <Tag color={r.keyFlag === 'PRI' ? 'gold' : 'default'}>{r.keyFlag || '普通'}</Tag> : <Tag color="blue">新增</Tag>}
          {!isOracle && (
            <Tag color={r.autoInc ? 'green' : 'default'} style={{ cursor: 'pointer' }}
              onClick={() => patchCol(r.key, { autoInc: !r.autoInc })}>
              {r.autoInc ? 'AUTO_INC' : '自增关'}
            </Tag>
          )}
        </Space>
      ),
    },
    {
      title: '', width: 50,
      render: (_: unknown, r: ColRow) => (
        <Popconfirm title={`删除字段 ${r.name || '(未命名)'}？`} onConfirm={() => removeCol(r)}>
          <Button size="small" danger type="text" icon={<DeleteOutlined />} />
        </Popconfirm>
      ),
    },
  ]

  const idxColumns = [
    {
      title: '索引名', dataIndex: 'name', width: 180,
      render: (v: string, r: IdxRow) => (
        <Input size="small" value={v} placeholder="idx_name（留空自动命名）" disabled={!!r.orig}
          onChange={(e) => patchIdx(r.key, { name: e.target.value })} />
      ),
    },
    {
      title: '字段（逗号分隔）', dataIndex: 'columns', width: 240,
      render: (v: string, r: IdxRow) => (
        <Input size="small" value={v} placeholder="col1, col2" disabled={!!r.orig}
          onChange={(e) => patchIdx(r.key, { columns: e.target.value })} />
      ),
    },
    {
      title: '唯一', dataIndex: 'unique', width: 70,
      render: (v: boolean, r: IdxRow) => <Tag color={v ? 'purple' : 'default'}>{v ? 'UNIQUE' : '普通'}</Tag>,
    },
    {
      title: '类型', dataIndex: 'orig', width: 90,
      render: (o?: DbTableIndex) => <span style={{ color: '#8c8c8c', fontSize: 12 }}>{o?.type || 'BTREE'}</span>,
    },
    {
      title: '', width: 50,
      render: (_: unknown, r: IdxRow) => (
        <Button size="small" danger type="text" icon={<DeleteOutlined />} disabled={r.name.toUpperCase() === 'PRIMARY'}
          onClick={() => removeIdx(r)} />
      ),
    },
  ]

  return (
    <Modal
      open={open}
      onCancel={onClose}
      width={1080}
      destroyOnClose
      maskClosable={false}
      title={`表结构 · ${database}.${table}`}
      footer={[
        <Button key="compare" onClick={() => setCompareOpen(true)}>对比</Button>,
        <Button key="preview" onClick={preview} loading={previewing}>预览 DDL</Button>,
        <Button key="cancel" onClick={onClose}>取消</Button>,
        <Button key="submit" type="primary" loading={executing} disabled={!schema} onClick={submit}>执行</Button>,
      ]}
    >
      <TableCompareModal
        open={compareOpen}
        connId={connId}
        database={database}
        table={table}
        onClose={() => setCompareOpen(false)}
        onNeedApproval={onNeedApproval}
        onApplied={onApplied}
      />
      {!schema ? (
        <div style={{ padding: 24 }}><Empty description={loading ? '加载表结构…' : '未获取到表结构'} /></div>
      ) : (
        <>
          <Space size={8} wrap style={{ marginBottom: 10, fontSize: 12, color: '#8c8c8c' }}>
            {isOracle ? (
              <Tag color="blue">Oracle</Tag>
            ) : (
              <>
                <Tag color="blue">{schema.engine || '-'}</Tag>
                <Tag>{schema.charset || '-'}</Tag>
                <Tag>{schema.collation || '-'}</Tag>
              </>
            )}
            <span>约 {schema.rows ?? 0} 行</span>
          </Space>
          <Tabs
            size="small"
            items={[
              {
                key: 'columns',
                label: `字段（${cols.length}）`,
                children: (
                  <>
                    <Button size="small" onClick={addCol} style={{ marginBottom: 8 }}>添加字段</Button>
                    <Table<ColRow> size="small" rowKey="key" columns={colColumns} dataSource={cols}
                      pagination={false} scroll={{ y: 380, x: 900 }} />
                  </>
                ),
              },
              {
                key: 'indexes',
                label: `索引（${idxs.length}）`,
                children: (
                  <>
                    <Button size="small" onClick={addIdx} style={{ marginBottom: 8 }}>添加索引</Button>
                    <Table<IdxRow> size="small" rowKey="key" columns={idxColumns} dataSource={idxs}
                      pagination={false} scroll={{ y: 380 }} />
                    <div style={{ fontSize: 12, color: '#8c8c8c', marginTop: 6 }}>
                      已有索引只能删除后重建（改名请在 SQL 终端执行）。
                    </div>
                  </>
                ),
              },
              {
                key: 'options',
                label: '表选项',
                children: (
                  <Space direction="vertical" style={{ width: '100%' }} size={10}>
                    <Space style={{ width: '100%' }}>
                      <span style={{ width: 110, display: 'inline-block', color: '#595959' }}>表注释</span>
                      <Input value={comment} onChange={(e) => setComment(e.target.value)} style={{ width: 420 }} />
                    </Space>
                    {!isOracle && (
                      <>
                        <Space>
                          <span style={{ width: 110, display: 'inline-block', color: '#595959' }}>存储引擎</span>
                          <Input value={engine} onChange={(e) => setEngine(e.target.value)} style={{ width: 200 }} placeholder="InnoDB" />
                        </Space>
                        <Space>
                          <span style={{ width: 110, display: 'inline-block', color: '#595959' }}>字符集</span>
                          <Input value={charset} onChange={(e) => setCharset(e.target.value)} style={{ width: 200 }}
                            placeholder="留空不改（CONVERT TO 会转换已有数据）" />
                        </Space>
                        <Space>
                          <span style={{ width: 110, display: 'inline-block', color: '#595959' }}>排序规则</span>
                          <Input value={collation} onChange={(e) => setCollation(e.target.value)} style={{ width: 240 }}
                            placeholder="如 utf8mb4_unicode_ci" />
                        </Space>
                      </>
                    )}
                    <Space>
                      <span style={{ width: 110, display: 'inline-block', color: '#595959' }}>重命名表</span>
                      <Input value={renameTo} onChange={(e) => setRenameTo(e.target.value)} style={{ width: 240 }}
                        placeholder={table} />
                    </Space>
                    <div style={{ fontSize: 12, color: '#8c8c8c' }}>
                      类型提示：{typeHints.join(' / ')}
                    </div>
                  </Space>
                ),
              },
            ]}
          />
          {statements.length > 0 && (
            <Alert
              style={{ marginTop: 8 }}
              type="info"
              showIcon
              message="DDL 预览"
              description={
                <pre style={{ margin: 0, fontSize: 12, whiteSpace: 'pre-wrap', fontFamily: 'Menlo, Consolas, monospace' }}>
                  {statements.join(';\n')}
                </pre>
              }
            />
          )}
        </>
      )}
    </Modal>
  )
}
