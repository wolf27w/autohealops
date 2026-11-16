// 数据库实例多页签（树视图右区）：外层=已打开实例页签（可关闭），内层=单实例 SQL 工作台。
// 树模式（「数据库连接」）只保留 SQL 工作台；监控/慢SQL/诊断/审核等页签在「数据库列表」详情页提供。
import { Tabs, Empty } from 'antd'
import type { DbTreeConn } from './DbInstanceTree'
import type { ConsoleConn } from '../DbWorkbench'
import DbConsolePanel from './DbConsolePanel'

interface Props {
  conns: DbTreeConn[]
  openIds: number[]
  activeId: number | null
  onActive: (id: number) => void
  onClose: (id: number) => void
}

// 单实例面板：只保留 SQL 工作台（复用详情页的 DbConsolePanel，autoConnect 进入即自动连接）
function InstancePane({ conn }: { conn: DbTreeConn }) {
  return <DbConsolePanel conn={conn as unknown as ConsoleConn} />
}

export default function DbInstanceTabs({ conns, openIds, activeId, onActive, onClose }: Props) {
  if (openIds.length === 0) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 360 }}>
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="从左侧选择实例开始" />
      </div>
    )
  }

  const byId = new Map<number, DbTreeConn>(conns.map((c) => [c.id, c]))

  return (
    <Tabs
      type="editable-card"
      hideAdd
      activeKey={activeId != null ? String(activeId) : undefined}
      onChange={(k) => onActive(Number(k))}
      onEdit={(targetKey, action) => {
        if (action === 'remove') onClose(Number(targetKey))
      }}
      items={openIds.map((id) => {
        const conn = byId.get(id)
        return {
          key: String(id),
          label: conn ? conn.name : `#${id}`,
          children: conn ? <InstancePane conn={conn} /> : <Empty description="实例不存在或已被删除" />,
        }
      })}
    />
  )
}
