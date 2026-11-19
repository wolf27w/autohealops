// 表结构对比弹窗（R16 §16.8，2026-11-19）
// 选择目标表（同库或跨实例）→ 展示字段/索引/表选项三态 diff → 可生成 ALTER
// （复用 alterTable 的 DDL 生成 + onNeedApproval 审核提交链路，不自行拼 DDL）。
import { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Empty, Input, Modal, Select, Space, Table, Tag, message } from 'antd'
import {
  alterTable, getDBConnections, getTableSchema,
  type DbTableChanges, type DbTableColumn, type DbTableIndex,
} from '../../services/api'
import type { DBConnection } from '../../types'
import { diffTableSchema, diffToChanges, type TableDiff } from './tableDiff'

export interface TableCompareModalProps {
  open: boolean
  connId: number
  database: string
  table: string
  onClose: () => void
  /** 命中审核策略：把生成的 DDL 交给父组件弹「提交审核」 */
  onNeedApproval: (sql: string) => void
  onApplied: (newTable: string) => void
}

type DiffRow = { key: string; kind: 'added' | 'removed' | 'modified'; name: string; detail: string }

const colSummary = (c: DbTableColumn) =>
  [
    c.type || '',
    c.nullable === false ? 'NOT NULL' : '',
    c.default !== null && c.default !== undefined && c.default !== '' ? `DEFAULT ${c.default}` : '',
    c.comment ? `COMMENT '${c.comment}'` : '',
    (c.extra || '').toLowerCase().includes('auto_increment') ? 'AUTO_INCREMENT' : '',
  ].filter(Boolean).join(' ') || '(空定义)'

const idxSummary = (i: DbTableIndex) =>
  `${i.unique ? 'UNIQUE ' : ''}(${(i.columns || []).join(', ')})`

const kindTag = (kind: DiffRow['kind']) => {
  if (kind === 'added') return <Tag color="green">新增</Tag>
  if (kind === 'removed') return <Tag color="red">删除</Tag>
  return <Tag color="orange">修改</Tag>
}

export default function TableCompareModal({
  open, connId, database, table, onClose, onNeedApproval, onApplied,
}: TableCompareModalProps) {
  const [conns, setConns] = useState<DBConnection[]>([])
  const [targetConnId, setTargetConnId] = useState<number>(connId)
  const [targetDb, setTargetDb] = useState(database)
  const [targetTable, setTargetTable] = useState('')
  const [loading, setLoading] = useState(false)
  const [diff, setDiff] = useState<TableDiff | null>(null)
  const [statements, setStatements] = useState<string[]>([])
  const [executing, setExecuting] = useState(false)

  useEffect(() => {
    if (!open) return
    setTargetConnId(connId)
    setTargetDb(database)
    setTargetTable('')
    setDiff(null)
    setStatements([])
    getDBConnections().then((list) => setConns(list as DBConnection[])).catch(() => setConns([]))
  }, [open, connId, database])

  const doCompare = async () => {
    const tgtTable = targetTable.trim()
    if (!tgtTable) { message.error('请输入目标表名'); return }
    setLoading(true)
    try {
      const [src, tgt] = await Promise.all([
        getTableSchema(connId, database, table),
        getTableSchema(targetConnId, targetDb.trim() || database, tgtTable),
      ])
      setDiff(diffTableSchema(src, tgt))
      setStatements([])
    } catch { /* 拦截器已提示 */ } finally { setLoading(false) }
  }

  const changes = useMemo<DbTableChanges>(() => (diff ? diffToChanges(diff) : {}), [diff])

  const generateAlter = async () => {
    if (!Object.keys(changes).length) { message.info('两张表结构一致，无需生成 ALTER'); return }
    try {
      const r = await alterTable(connId, { database, table, changes, dry_run: true })
      setStatements(r.statements || [])
    } catch { /* 拦截器已提示 */ }
  }

  const submit = async () => {
    if (!Object.keys(changes).length) { message.info('没有检测到改动'); return }
    setExecuting(true)
    let stmts: string[] = []
    try {
      const p = await alterTable(connId, { database, table, changes, dry_run: true })
      stmts = p.statements || []
      setStatements(stmts)
      await alterTable(connId, { database, table, changes, dry_run: false })
      message.success('表结构已同步为目标表结构')
      onApplied(table)
      onClose()
    } catch (e) {
      const err = e as { needApproval?: boolean }
      if (err?.needApproval) {
        if (stmts.length) onNeedApproval(stmts.join(';\n'))
        else message.warning('该操作需要审核，请先点「生成 ALTER」再提交')
        onClose()
      }
    } finally { setExecuting(false) }
  }

  const diffRows = useMemo<DiffRow[]>(() => {
    if (!diff) return []
    const rows: DiffRow[] = []
    diff.columns.added.forEach((c, i) => rows.push({ key: `ca${i}`, kind: 'added', name: c.name, detail: colSummary(c) }))
    diff.columns.removed.forEach((c, i) => rows.push({ key: `cr${i}`, kind: 'removed', name: c.name, detail: colSummary(c) }))
    diff.columns.modified.forEach((m, i) => rows.push({
      key: `cm${i}`, kind: 'modified', name: m.key,
      detail: `${colSummary(m.before)} → ${colSummary(m.after)}`,
    }))
    diff.indexes.added.forEach((ix, i) => rows.push({ key: `ia${i}`, kind: 'added', name: ix.name, detail: idxSummary(ix) }))
    diff.indexes.removed.forEach((ix, i) => rows.push({ key: `ir${i}`, kind: 'removed', name: ix.name, detail: idxSummary(ix) }))
    diff.indexes.modified.forEach((m, i) => rows.push({
      key: `im${i}`, kind: 'modified', name: m.key,
      detail: `${idxSummary(m.before)} → ${idxSummary(m.after)}`,
    }))
    diff.options.modified.forEach((m, i) => rows.push({
      key: `o${i}`, kind: 'modified', name: `表选项.${m.key}`,
      detail: `${m.before || '(空)'} → ${m.after || '(空)'}`,
    }))
    return rows
  }, [diff])

  const hasDiff = diffRows.length > 0
  const columns = [
    { title: '变更', dataIndex: 'kind', width: 80, render: (k: DiffRow['kind']) => kindTag(k) },
    { title: '对象', dataIndex: 'name', width: 180 },
    { title: '说明', dataIndex: 'detail' },
  ]

  return (
    <Modal
      open={open}
      onCancel={onClose}
      width={860}
      destroyOnClose
      maskClosable={false}
      title={`表结构对比 · ${database}.${table}`}
      footer={[
        <Button key="compare" type="primary" onClick={doCompare} loading={loading}>开始对比</Button>,
        <Button key="alter" onClick={generateAlter} disabled={!hasDiff}>生成 ALTER</Button>,
        <Button key="submit" onClick={submit} loading={executing} disabled={!hasDiff}>执行同步</Button>,
        <Button key="cancel" onClick={onClose}>取消</Button>,
      ]}
    >
      <Space direction="vertical" style={{ width: '100%' }} size={10}>
        <Space size={8} wrap>
          <span style={{ color: '#595959' }}>目标连接</span>
          <Select
            style={{ width: 260 }}
            value={targetConnId}
            onChange={(v) => setTargetConnId(v)}
            options={conns.map((c) => ({
              value: c.id,
              label: `${c.name} (${c.db_type || 'mysql'}${c.host ? '@' + c.host : ''})`,
            }))}
            showSearch
            optionFilterProp="label"
          />
          <span style={{ color: '#595959' }}>目标库</span>
          <Input style={{ width: 140 }} value={targetDb} placeholder={database}
            onChange={(e) => setTargetDb(e.target.value)} />
          <span style={{ color: '#595959' }}>目标表</span>
          <Input style={{ width: 140 }} value={targetTable} placeholder="table_name"
            onChange={(e) => setTargetTable(e.target.value)} />
        </Space>
        <div style={{ fontSize: 12, color: '#8c8c8c' }}>
          对比方向：把当前表（{database}.{table}）同步为目标表结构；新增/删除/修改三态，改动默认走审核链路。
        </div>

        {!diff ? (
          <Empty description={loading ? '对比中…' : '选择目标表后点「开始对比」'} style={{ padding: 24 }} />
        ) : !hasDiff ? (
          <Alert type="success" showIcon message="两张表结构一致，没有差异" />
        ) : (
          <Table<DiffRow> size="small" rowKey="key" columns={columns} dataSource={diffRows}
            pagination={false} scroll={{ y: 320 }} />
        )}

        {statements.length > 0 && (
          <Alert
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
      </Space>
    </Modal>
  )
}
