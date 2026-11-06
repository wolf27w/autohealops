// 数据库管理面板（Navicat 式）：左侧实例列表，右上 SQL 编辑，右下结果。
// 直接嵌在「诊断中心 → 数据库管理」tab 里，不再是弹窗。
import { useMemo, useState } from 'react'
import { Button, Input, List, Tag, Space, Table, Empty, Popconfirm, Tooltip, Switch } from 'antd'
import { PlusOutlined, PlayCircleOutlined, ReloadOutlined, EditOutlined, DeleteOutlined, DownloadOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { execDBConnection, queryDBConnection, downloadDBQuery } from '../services/api'

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
  const head = useMemo(() => (sql.trim().split(/\s+/)[0] || '').toLowerCase(), [sql])
  const isRead = READ_HEADS.has(head)
  const filtered = useMemo(() => {
    const k = kw.trim().toLowerCase()
    return k ? conns.filter((c) => `${c.name} ${c.database || ''} ${c.db_type || ''}`.toLowerCase().includes(k)) : conns
  }, [conns, kw])

  const reset = () => { setRes(null); setErr('') }

  const run = async (write: boolean) => {
    if (!cur) return
    setBusy(true); reset()
    try {
      setRes((write ? await execDBConnection(cur.id, sql) : await queryDBConnection(cur.id, sql)) as Result)
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
                    </div>
                  </div>
                )
              }}
            />
          )}
        </div>
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
              {!isAgent && <Tag color="orange">直连库：平台不代执行 SQL</Tag>}
            </Space>

            <Input.TextArea
              value={sql}
              onChange={(e) => setSql(e.target.value)}
              disabled={!isAgent}
              placeholder={'select * from information_schema.tables limit 10\n-- 或 update t set c=1 where id=1'}
              style={{ fontFamily: 'Menlo, Consolas, monospace', fontSize: 13, resize: 'none', height: 130 }}
              onKeyDown={(e) => {
                if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && isAgent && sql.trim() && isRead) run(false)
              }}
            />

            <Space wrap>
              {isRead ? (
                <Button type="primary" loading={busy} disabled={!isAgent || !sql.trim()} onClick={() => run(false)}>
                  执行（Ctrl/Cmd+Enter）
                </Button>
              ) : (
                <Popconfirm title="确认执行写操作？" description="将真实修改数据库，无法自动回滚。"
                  okText="确认执行" cancelText="取消" okButtonProps={{ danger: true }} onConfirm={() => run(true)}>
                  <Button danger loading={busy} disabled={!isAgent || !sql.trim()}>执行（写）</Button>
                </Popconfirm>
              )}
              <Button icon={<DownloadOutlined />} disabled={!isAgent || !sql.trim() || !isRead} onClick={download}>下载 CSV</Button>
              {res?.affected_rows !== undefined && (
                <Tag color={(res.affected_rows || 0) > 0 ? 'green' : 'default'}>影响行数：{res.affected_rows}</Tag>
              )}
              <span style={{ fontSize: 12, color: '#8c8c8c' }}>
                {sql.trim() ? (isRead ? '识别为只读语句（最多 200 行）' : `识别为写语句（${head}）`) : '只读查询最多 200 行，导出最多 5000 行'}
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
      </div>
    </div>
  )
}
