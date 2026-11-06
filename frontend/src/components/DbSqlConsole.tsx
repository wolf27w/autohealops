// 数据库 SQL 控制台（诊断中心用）——Navicat 式布局：
// 左边：数据库连接实例列表（可搜索、可切换）；右上：SQL 编辑区；右下：结果网格。
// 一个入口，按语句类型自动区分只读/写：
// - 只读（select/show/describe/explain/with）→ 直接执行 + 导出 CSV（≤5000 行）
// - 写（insert/update/delete/ddl…）→ 仅管理员 + 二次确认，返回影响行数，全额审计
import { useEffect, useMemo, useState } from 'react'
import { Modal, Input, Alert, Button, Space, Tag, Table, Popconfirm, Empty, List } from 'antd'
import { execDBConnection, queryDBConnection, downloadDBQuery } from '../services/api'

export interface ConsoleConn {
  id: number
  name: string
  db_type?: string
  database?: string
  source?: string
  status?: string
  cluster_id?: number | null
}

interface Props {
  open: boolean
  conns: ConsoleConn[]
  initialId?: number | null
  onClose: () => void
}

type Result = { columns?: string[]; rows?: unknown[][]; truncated?: boolean; affected_rows?: number }

const READ_HEADS = new Set(['select', 'show', 'desc', 'describe', 'explain', 'with'])

export default function DbSqlConsole({ open, conns, initialId, onClose }: Props) {
  const [curId, setCurId] = useState<number | null>(initialId ?? null)
  const [kw, setKw] = useState('')
  const [sql, setSql] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [res, setRes] = useState<Result | null>(null)

  useEffect(() => { if (open) { setCurId(initialId ?? conns[0]?.id ?? null); setRes(null); setErr('') } },
    [open, initialId, conns])

  const cur = useMemo(() => conns.find((c) => c.id === curId) || null, [conns, curId])
  const isAgent = (cur?.source || 'direct') === 'agent'
  const head = useMemo(() => (sql.trim().split(/\s+/)[0] || '').toLowerCase(), [sql])
  const isRead = READ_HEADS.has(head)
  const filtered = useMemo(() => {
    const k = kw.trim().toLowerCase()
    return k ? conns.filter((c) => `${c.name} ${c.database || ''} ${c.db_type || ''}`.toLowerCase().includes(k)) : conns
  }, [conns, kw])

  const reset = () => { setRes(null); setErr('') }

  const runRead = async () => {
    if (!cur) return
    setBusy(true); reset()
    try {
      setRes(await queryDBConnection(cur.id, sql) as Result)
    } catch (e) {
      const x = e as { response?: { data?: { message?: string } }; message?: string }
      setErr(x?.response?.data?.message || x?.message || '执行失败')
    } finally { setBusy(false) }
  }

  const runWrite = async () => {
    if (!cur) return
    setBusy(true); reset()
    try {
      setRes(await execDBConnection(cur.id, sql) as Result)
    } catch (e) {
      const x = e as { response?: { data?: { message?: string } }; message?: string }
      setErr(x?.response?.data?.message || x?.message || '执行失败')
    } finally { setBusy(false) }
  }

  const download = async () => {
    if (!cur) return
    try {
      const blob = await downloadDBQuery(cur.id, sql)
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

  return (
    <Modal
      title="SQL 控制台"
      open={open}
      onCancel={() => { reset(); onClose() }}
      footer={null}
      width="92vw"
      style={{ top: 16, maxWidth: 1500 }}
      destroyOnClose
    >
      <div style={{ display: 'flex', gap: 12, height: 'calc(100vh - 170px)' }}>
        {/* 左：实例列表 */}
        <div style={{ width: 260, flex: '0 0 260px', borderRight: '1px solid #f0f0f0', paddingRight: 8, display: 'flex', flexDirection: 'column' }}>
          <Input.Search size="small" allowClear placeholder="搜索实例 / 库" value={kw}
            onChange={(e) => setKw(e.target.value)} style={{ marginBottom: 8 }} />
          <div style={{ flex: 1, overflow: 'auto' }}>
            {filtered.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无数据库" /> : (
              <List
                size="small"
                dataSource={filtered}
                renderItem={(c) => {
                  const active = c.id === curId
                  const agent = (c.source || 'direct') === 'agent'
                  return (
                    <div
                      onClick={() => { setCurId(c.id); reset() }}
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
                      <div style={{ fontSize: 11, color: '#8c8c8c', marginLeft: 13, display: 'flex', gap: 4 }}>
                        <Tag bordered={false} color={agent ? 'purple' : 'default'} style={{ marginInlineEnd: 0, fontSize: 10, lineHeight: '16px' }}>
                          {agent ? `agent·${c.cluster_id ?? '-'}` : '直连'}
                        </Tag>
                        <span>{c.db_type}{c.database ? ` / ${c.database}` : ''}</span>
                      </div>
                    </div>
                  )
                }}
              />
            )}
          </div>
        </div>

        {/* 右：SQL + 结果 */}
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ fontSize: 12, color: '#8c8c8c' }}>
            {cur ? <>当前：<b>{cur.name}</b>{cur.database ? ` / ${cur.database}` : ''}</> : '请选择左侧数据库'}
            {cur && !isAgent && <Tag color="orange" style={{ marginLeft: 8 }}>直连库：平台不代执行 SQL</Tag>}
          </div>

          <Input.TextArea
            value={sql}
            onChange={(e) => setSql(e.target.value)}
            disabled={!isAgent}
            placeholder={'select * from information_schema.tables limit 10\n-- 或 update t set c=1 where id=1'}
            style={{ fontFamily: 'Menlo, Consolas, monospace', fontSize: 13, resize: 'none', height: 150 }}
            onKeyDown={(e) => {
              if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && isAgent && sql.trim() && isRead) runRead()
            }}
          />

          <Space wrap>
            {isRead ? (
              <Button type="primary" loading={busy} disabled={!isAgent || !sql.trim()} onClick={runRead}>执行（Ctrl/Cmd+Enter）</Button>
            ) : (
              <Popconfirm title="确认执行写操作？" description="将真实修改数据库，无法自动回滚。"
                okText="确认执行" cancelText="取消" okButtonProps={{ danger: true }} onConfirm={runWrite}>
                <Button danger loading={busy} disabled={!isAgent || !sql.trim()}>执行（写）</Button>
              </Popconfirm>
            )}
            <Button disabled={!isAgent || !sql.trim() || !isRead} onClick={download}>下载 CSV</Button>
            {res?.affected_rows !== undefined && (
              <Tag color={(res.affected_rows || 0) > 0 ? 'green' : 'default'}>影响行数：{res.affected_rows}</Tag>
            )}
            <span style={{ fontSize: 12, color: '#8c8c8c' }}>
              {sql.trim() ? (isRead ? '识别为只读语句（最多 200 行）' : `识别为写语句（${head}）`) : '只读查询最多 200 行，导出最多 5000 行'}
            </span>
          </Space>

          {err && <Alert type="error" showIcon message={err} />}

          <div style={{ flex: 1, minHeight: 0, border: '1px solid #f0f0f0', borderRadius: 6, overflow: 'auto', padding: res ? 0 : 24 }}>
            {!res ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="执行后在这里看结果" />
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
        </div>
      </div>
    </Modal>
  )
}
