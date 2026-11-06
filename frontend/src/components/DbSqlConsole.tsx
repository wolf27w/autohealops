// 数据库 SQL 控制台（诊断中心用）：一个入口，按语句类型自动区分只读/写。
// - 只读（select/show/describe/explain/with）：直接执行，可导出 CSV（≤5000 行）
// - 写（insert/update/delete/ddl…）：仅管理员 + 二次确认，返回影响行数，全额审计
import { useMemo, useState } from 'react'
import { Modal, Input, Alert, Button, Space, Tag, Table, Popconfirm } from 'antd'
import { execDBConnection, queryDBConnection, downloadDBQuery } from '../services/api'

interface Props {
  conn: { id: number; name: string; database?: string; source?: string } | null
  onClose: () => void
}

type Result = { columns?: string[]; rows?: unknown[][]; truncated?: boolean; affected_rows?: number }

const READ_HEADS = new Set(['select', 'show', 'desc', 'describe', 'explain', 'with'])

export default function DbSqlConsole({ conn, onClose }: Props) {
  const [sql, setSql] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [res, setRes] = useState<Result | null>(null)

  const isAgent = (conn?.source || 'direct') === 'agent'
  const head = useMemo(() => (sql.trim().split(/\s+/)[0] || '').toLowerCase(), [sql])
  const isRead = READ_HEADS.has(head)

  const reset = () => { setRes(null); setErr('') }

  const runRead = async () => {
    if (!conn) return
    setBusy(true); reset()
    try {
      setRes(await queryDBConnection(conn.id, sql) as Result)
    } catch (e) {
      const x = e as { response?: { data?: { message?: string } }; message?: string }
      setErr(x?.response?.data?.message || x?.message || '执行失败')
    } finally { setBusy(false) }
  }

  const runWrite = async () => {
    if (!conn) return
    setBusy(true); reset()
    try {
      setRes(await execDBConnection(conn.id, sql) as Result)
    } catch (e) {
      const x = e as { response?: { data?: { message?: string } }; message?: string }
      setErr(x?.response?.data?.message || x?.message || '执行失败')
    } finally { setBusy(false) }
  }

  const download = async () => {
    if (!conn) return
    try {
      const blob = await downloadDBQuery(conn.id, sql)
      const url = URL.createObjectURL(blob as Blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${conn.name || 'db'}-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '')}.csv`
      document.body.appendChild(a); a.click(); a.remove()
      URL.revokeObjectURL(url)
    } catch (e) {
      const x = e as { response?: { data?: { message?: string } }; message?: string }
      setErr(x?.response?.data?.message || x?.message || '导出失败')
    }
  }

  return (
    <Modal
      title={`SQL 控制台：${conn?.name || ''}${conn?.database ? ` / ${conn.database}` : ''}`}
      open={!!conn}
      onCancel={() => { reset(); onClose() }}
      footer={null}
      width={900}
    >
      <Alert
        type={sql.trim() && !isRead ? 'warning' : 'info'}
        showIcon
        style={{ marginBottom: 8 }}
        message={sql.trim() && !isRead
          ? '写操作：仅管理员可执行，需要二次确认，无法自动回滚；平台记录影响行数并审计。'
          : '平台只下发 key + SQL，账号密码在 agent 本地；只读查询最多 200 行，导出最多 5000 行。'}
        description={!isAgent ? '仅 agent 接入的库支持平台操作（平台不保存该库账密）。' : undefined}
      />
      <Input.TextArea rows={7} value={sql} onChange={(e) => setSql(e.target.value)} disabled={!isAgent}
        placeholder={'select * from information_schema.tables limit 10\n-- 或：update t set c=1 where id=1'} />

      {err && <Alert type="error" showIcon style={{ marginTop: 8 }} message={err} />}

      <Space style={{ marginTop: 10 }} wrap>
        {isRead ? (
          <Button type="primary" loading={busy} disabled={!isAgent || !sql.trim()} onClick={runRead}>执行</Button>
        ) : (
          <Popconfirm
            title="确认执行写操作？"
            description="将真实修改数据库，无法自动回滚。"
            okText="确认执行" cancelText="取消" okButtonProps={{ danger: true }}
            onConfirm={runWrite}
          >
            <Button danger loading={busy} disabled={!isAgent || !sql.trim()}>执行（写）</Button>
          </Popconfirm>
        )}
        <Button disabled={!isAgent || !sql.trim() || !isRead} onClick={download}>下载 CSV</Button>
        {res?.affected_rows !== undefined && (
          <Tag color={(res.affected_rows || 0) > 0 ? 'green' : 'default'}>影响行数：{res.affected_rows}</Tag>
        )}
        <span style={{ fontSize: 12, color: '#8c8c8c' }}>
          {sql.trim() ? (isRead ? '识别为只读语句' : `识别为写语句（${head || '未知'}）`) : '输入 SQL 后自动判断读写'}
        </span>
      </Space>

      {res && (
        <div style={{ marginTop: 10 }}>
          <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 4 }}>
            返回 {(res.rows || []).length} 行{res.truncated ? '（已截断）' : ''}
          </div>
          {(res.rows || []).length > 0 && (
            <Table
              size="small"
              rowKey={(_, i) => String(i)}
              dataSource={(res.rows || []).map((r, i) => ({ __i: i, r }))}
              columns={(res.columns || []).map((c, idx) => ({
                title: c, dataIndex: ['r', idx], render: (v: unknown) => (v === null ? 'NULL' : String(v)),
              }))}
              pagination={{ pageSize: 10, showSizeChanger: false }}
              scroll={{ x: 'max-content' }}
            />
          )}
        </div>
      )}
    </Modal>
  )
}
