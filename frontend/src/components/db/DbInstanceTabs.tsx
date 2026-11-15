// 数据库实例多页签（树视图右区）：外层=已打开实例页签（可关闭），内层=单实例 6 个子页签。
// 子页签复用 DbDetail 的现有面板组件（概览为本地 Descriptions，其余 5 个复用 components/db/*）。
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Tabs, Descriptions, Tag, Space, Button, Empty, message } from 'antd'
import { ThunderboltOutlined, ExportOutlined } from '@ant-design/icons'
import type { DbTreeConn } from './DbInstanceTree'
import type { ConsoleConn } from '../DbWorkbench'
import { runDbDiagnosis } from '../../services/api'
import DbMonitorPanel from './DbMonitorPanel'
import DbSlowSqlPanel from './DbSlowSqlPanel'
import DbAnomalyPanel from './DbAnomalyPanel'
import DbConsolePanel from './DbConsolePanel'
import DbDiagnosisPanel from './DbDiagnosisPanel'

// DbTreeConn 只承诺树/页签所需的最小字段；概览要展示 host:port 等富信息，这里用可选字段补齐。
// 运行时 conns 实际是 DbRow（含 host/port 等），字段存在即展示、不存在即回退。
interface TabConn extends DbTreeConn {
  host?: string
  port?: number
  cluster_id?: number | null
  username?: string
  env_tag?: string
  collect_enabled?: boolean
  inspect_enabled?: boolean
}

interface Props {
  conns: DbTreeConn[]
  openIds: number[]
  activeId: number | null
  onActive: (id: number) => void
  onClose: (id: number) => void
}

const TYPE_COLOR: Record<string, string> = { mysql: 'blue', pg: 'green', oracle: 'volcano' }

// 单实例子页签面板（概览 + 监控 + 慢SQL + 异常 + SQL 工作台 + 诊断）
function InstancePane({ conn }: { conn: TabConn }) {
  const navigate = useNavigate()
  const [diagnosing, setDiagnosing] = useState(false)
  const connId = conn.id

  const diagnose = async () => {
    setDiagnosing(true)
    try {
      await runDbDiagnosis(connId)
      message.success('已发起诊断')
    } catch {
      message.error('诊断失败')
    } finally {
      setDiagnosing(false)
    }
  }

  const overview = (
    <Descriptions column={2} size="small" bordered style={{ marginBottom: 12 }}>
      <Descriptions.Item label="名称">{conn.name || '-'}</Descriptions.Item>
      <Descriptions.Item label="类型">
        <Tag color={TYPE_COLOR[conn.db_type || ''] || 'default'}>{conn.db_type || '-'}</Tag>
      </Descriptions.Item>
      <Descriptions.Item label="库">{conn.database || '-'}</Descriptions.Item>
      <Descriptions.Item label="地址">
        {conn.host ? `${conn.host}${conn.port ? `:${conn.port}` : ''}` : '-'}
      </Descriptions.Item>
      <Descriptions.Item label="分组">{conn.group_name || '-'}</Descriptions.Item>
      <Descriptions.Item label="来源">
        {conn.source === 'agent' ? <Tag color="purple">集群 Agent</Tag> : <Tag>手动纳管</Tag>}
      </Descriptions.Item>
    </Descriptions>
  )

  return (
    <div>
      <Space style={{ marginBottom: 12 }}>
        <Button icon={<ExportOutlined />} onClick={() => navigate(`/resources/databases/${connId}`)}>
          打开完整详情
        </Button>
        <Button type="primary" ghost icon={<ThunderboltOutlined />} loading={diagnosing} onClick={diagnose}>
          发起诊断
        </Button>
      </Space>
      <Tabs
        destroyInactiveTabPane
        defaultActiveKey="overview"
        items={[
          { key: 'overview', label: '概览', children: overview },
          { key: 'monitor', label: '监控', children: <DbMonitorPanel connId={connId} dbType={conn.db_type} /> },
          { key: 'slow-sql', label: '慢 SQL', children: <DbSlowSqlPanel connId={connId} /> },
          { key: 'anomalies', label: '异常', children: <DbAnomalyPanel connId={connId} /> },
          { key: 'console', label: 'SQL 工作台', children: <DbConsolePanel conn={conn as unknown as ConsoleConn} /> },
          { key: 'diagnosis', label: '诊断', children: <DbDiagnosisPanel connId={connId} /> },
        ]}
      />
    </div>
  )
}

export default function DbInstanceTabs({ conns, openIds, activeId, onActive, onClose }: Props) {
  if (openIds.length === 0) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 360 }}>
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="从左侧选择实例开始" />
      </div>
    )
  }

  const byId = new Map<number, TabConn>(conns.map((c) => [c.id, c as TabConn]))

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
