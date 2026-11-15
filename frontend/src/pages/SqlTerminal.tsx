// 独立整页 SQL 终端（新标签页打开，不套平台 MainLayout）：顶栏 + DbWorkbench 铺满。
// 入口：数据库管理列表页「SQL 终端」按钮 → window.open('/sql-terminal')
import { useEffect, useState } from 'react'
import { Button, Tag, Empty, message } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import DbWorkbench, { type ConsoleConn } from '../components/DbWorkbench'
import { getDBConnections } from '../services/api'

// 后端返回的行类型（getDBConnections 返回 unknown，这里按需断言）
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
  collect_enabled?: boolean
  inspect_enabled?: boolean
}

const toConsoleConn = (r: ConnRow): ConsoleConn => ({
  id: r.id,
  name: r.name,
  db_type: r.db_type,
  database: r.database,
  group_name: r.group_name,
  host: r.host,
  port: r.port,
  source: r.source,
  status: r.status,
  cluster_id: r.cluster_id,
  collect_enabled: r.collect_enabled,
  inspect_enabled: r.inspect_enabled,
})

export default function SqlTerminal() {
  const [conns, setConns] = useState<ConsoleConn[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)

  const load = () => {
    setLoading(true)
    getDBConnections()
      .then((d) => {
        const rows = (d as unknown as ConnRow[]) || []
        const mapped = rows.map(toConsoleConn)
        setConns(mapped)
        // 保持当前选中项（仍存在），否则回落到第一条；无实例则置空
        setSelectedId((prev) => (mapped.some((c) => c.id === prev) ? prev : (mapped[0]?.id ?? null)))
      })
      .catch(() => message.error('加载数据库连接失败'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const sel = conns.find((c) => c.id === selectedId) || null

  // 其余操作统一提示回数据库管理页做（本页只做查询执行）
  const onlyInDbPage = () => message.info('请在「数据库管理」页操作')

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', background: '#fff' }}>
      {/* 顶栏 */}
      <div style={{
        height: 44, flex: '0 0 44px', display: 'flex', alignItems: 'center',
        padding: '0 16px', borderBottom: '1px solid #f0f0f0', gap: 12,
      }}>
        <span style={{ fontSize: 16, fontWeight: 600 }}>SQL 终端</span>
        {sel && (
          <>
            <Tag color="blue">{sel.name}</Tag>
            <span style={{ fontSize: 12, color: '#8c8c8c' }}>
              {sel.host}:{sel.port}{sel.database ? ` · ${sel.database}` : ''}
            </span>
          </>
        )}
        <span style={{ flex: 1 }} />
        <Button icon={<ReloadOutlined />} loading={loading} onClick={load}>刷新</Button>
      </div>

      {/* 主体 */}
      <div style={{ flex: 1, minHeight: 0, display: 'flex' }}>
        {conns.length === 0 ? (
          // 空态：仍保留左侧实例列表结构
          <div style={{ width: 280, flex: '0 0 280px', borderRight: '1px solid #f0f0f0', padding: 16 }}>
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无数据库实例，请先在数据库管理页添加" />
          </div>
        ) : (
          <DbWorkbench
            conns={conns}
            selectedId={selectedId}
            onSelect={(id) => setSelectedId(id)}
            onAdd={onlyInDbPage}
            onDiagnose={onlyInDbPage}
            onTest={onlyInDbPage}
            onCollect={onlyInDbPage}
            onEdit={onlyInDbPage}
            onDelete={onlyInDbPage}
            onToggleCollect={onlyInDbPage}
            onToggleInspect={onlyInDbPage}
            onReload={load}
          />
        )}
      </div>
    </div>
  )
}
