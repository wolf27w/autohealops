// 数据库资源详情（R34-P1）：整页 + Tab 骨架
// 概览 / 监控 / 慢SQL / 异常 / SQL 工作台 / 诊断 —— 各 Tab 由 components/db/* 提供
import { useEffect, useMemo, useState } from 'react'
import { Button, Card, Descriptions, Empty, Space, Spin, Switch, Tabs, Tag, Tooltip, message } from 'antd'
import { ArrowLeftOutlined, ReloadOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { getDBConnections, collectSlowSQL, getDbApprovalPolicy, updateDbApprovalPolicy } from '../services/api'
import type { DbApprovalPolicy } from '../services/api'
import { isAdmin } from '../utils/auth'
import type { ConsoleConn } from '../components/DbWorkbench'
import DbMonitorPanel from '../components/db/DbMonitorPanel'
import DbSlowSqlPanel from '../components/db/DbSlowSqlPanel'
import DbAnomalyPanel from '../components/db/DbAnomalyPanel'
import DbConsolePanel from '../components/db/DbConsolePanel'
import DbDiagnosisPanel from '../components/db/DbDiagnosisPanel'

// 数据库实例（后端 GET /db-connections 返回字段）
interface DbConn extends ConsoleConn {
  username?: string
  connect_mode?: string
  service_name?: string
  sid?: string
  env_tag?: string
  collect_interval?: number
  collect_enabled?: boolean
  inspect_enabled?: boolean
  inspect_cron?: string
  slow_sql_threshold?: number
  agent_db_key?: string
  last_error?: string
  last_seen_at?: string
  created_at?: string
}

// 后端 status 真实取值：connected / failed / unknown / removed
const STATUS: Record<string, { color: string; label: string }> = {
  connected: { color: 'green', label: '已连接' },
  online: { color: 'green', label: '在线' },
  ok: { color: 'green', label: '正常' },
  failed: { color: 'red', label: '采集失败' },
  offline: { color: 'red', label: '离线' },
  error: { color: 'red', label: '异常' },
  removed: { color: 'default', label: '已下线' },
  unknown: { color: 'default', label: '未知' },
}

const TYPE_COLOR: Record<string, string> = { mysql: 'blue', pg: 'green', oracle: 'volcano' }

const TABS = ['overview', 'monitor', 'slow-sql', 'anomalies', 'console', 'diagnosis', 'approval-policy']

// 审核策略四类操作开关配置（B12-3a）
const APPROVAL_ROWS: { key: keyof DbApprovalPolicy; title: string; desc: string }[] = [
  { key: 'need_query', title: '查询需要审核', desc: 'SELECT/SHOW 等只读查询' },
  { key: 'need_write', title: '写入需要审核', desc: 'INSERT/UPDATE/DELETE/REPLACE' },
  { key: 'need_ddl', title: 'DDL 需要审核', desc: 'CREATE/ALTER/DROP/TRUNCATE/RENAME' },
  { key: 'need_export', title: '导出需要审核', desc: '导出结构与数据' },
]

function DbApprovalPolicyPanel({ connId }: { connId: number }) {
  const [policy, setPolicy] = useState<DbApprovalPolicy | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const editable = isAdmin()

  useEffect(() => {
    let mounted = true
    setLoading(true)
    getDbApprovalPolicy(connId)
      .then((p) => { if (mounted) setPolicy(p) })
      .catch(() => {})
      .finally(() => { if (mounted) setLoading(false) })
    return () => { mounted = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connId])

  const save = async () => {
    if (!policy) return
    setSaving(true)
    try {
      await updateDbApprovalPolicy(connId, policy)
      message.success('已保存')
    } catch {
      message.error('保存失败')
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <Card size="small">
        <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}><Spin /></div>
      </Card>
    )
  }

  return (
    <Card size="small">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
        {APPROVAL_ROWS.map((row) => (
          <div key={row.key} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <Switch
              checked={policy?.[row.key] ?? false}
              disabled={!editable}
              onChange={(v) => setPolicy((prev) => (prev ? { ...prev, [row.key]: v } : prev))}
            />
            <div>
              <div>{row.title}</div>
              <div style={{ color: '#8c8c8c', fontSize: 12 }}>{row.desc}</div>
            </div>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 24 }}>
        {editable ? (
          <Button type="primary" loading={saving} onClick={save}>保存</Button>
        ) : (
          <span style={{ color: '#8c8c8c', fontSize: 12 }}>仅管理员/项目管理员可修改</span>
        )}
      </div>
    </Card>
  )
}

export default function DbDetail() {
  const { id } = useParams()
  const connId = Number(id)
  const navigate = useNavigate()
  const [sp, setSp] = useSearchParams()
  const tab = TABS.includes(sp.get('tab') || '') ? (sp.get('tab') as string) : 'overview'
  const [conn, setConn] = useState<DbConn | null>(null)
  const [loading, setLoading] = useState(true)
  const [collecting, setCollecting] = useState(false)

  const load = () => {
    setLoading(true)
    getDBConnections()
      .then((list) => {
        const rows = (list || []) as DbConn[]
        setConn(rows.find((r) => Number(r.id) === connId) || null)
      })
      .catch(() => setConn(null))
      .finally(() => setLoading(false))
  }
  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connId])

  const doCollect = async () => {
    setCollecting(true)
    try {
      await collectSlowSQL(connId)
      message.success('已触发采集')
    } catch {
      message.error('采集失败')
    } finally {
      setCollecting(false)
    }
  }

  const status = STATUS[conn?.status || 'unknown'] || STATUS.unknown

  const overview = useMemo(() => {
    if (!conn) return null
    const oracleExtra = conn.db_type === 'oracle'
    return (
      <Card size="small">
        <Descriptions column={2} size="small" bordered>
          <Descriptions.Item label="名称">{conn.name}</Descriptions.Item>
          <Descriptions.Item label="状态">
            <Tag color={status.color}>{status.label}</Tag>
            {conn.last_error ? (
              <Tooltip title={conn.last_error}>
                <Tag color="red" style={{ marginLeft: 6 }}>最近错误</Tag>
              </Tooltip>
            ) : null}
          </Descriptions.Item>
          <Descriptions.Item label="类型">
            <Tag color={TYPE_COLOR[conn.db_type || ''] || 'default'}>{conn.db_type || '-'}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="接入方式">
            {conn.source === 'agent'
              ? <Tag color="purple">集群 Agent 上报{conn.agent_db_key ? `（${conn.agent_db_key}）` : ''}</Tag>
              : <Tag>平台直连</Tag>}
          </Descriptions.Item>
          <Descriptions.Item label="地址" span={2}>
            {conn.host || '-'}{conn.port ? `:${conn.port}` : ''}
            {conn.database ? ` / ${conn.database}` : ''}
          </Descriptions.Item>
          <Descriptions.Item label="账号">{conn.username || '-'}</Descriptions.Item>
          <Descriptions.Item label="分组 / 环境">
            {conn.group_name || '-'} {conn.env_tag ? <Tag style={{ marginLeft: 6 }}>{conn.env_tag}</Tag> : null}
          </Descriptions.Item>
          {oracleExtra && (
            <Descriptions.Item label="Oracle 连接" span={2}>
              {conn.connect_mode || '-'}
              {conn.service_name ? ` · service_name=${conn.service_name}` : ''}
              {conn.sid ? ` · sid=${conn.sid}` : ''}
            </Descriptions.Item>
          )}
          <Descriptions.Item label="采集">
            {conn.collect_enabled
              ? <Tag color="green">开 · 每 {conn.collect_interval || 5} 分钟</Tag>
              : <Tag>关</Tag>}
          </Descriptions.Item>
          <Descriptions.Item label="巡检">
            {conn.inspect_enabled ? <Tag color="green">开{conn.inspect_cron ? ` · ${conn.inspect_cron}` : ''}</Tag> : <Tag>关</Tag>}
          </Descriptions.Item>
          <Descriptions.Item label="慢 SQL 阈值">{conn.slow_sql_threshold ? `${conn.slow_sql_threshold} ms` : '-'}</Descriptions.Item>
          <Descriptions.Item label="最近采集">{conn.last_seen_at || '-'}</Descriptions.Item>
          <Descriptions.Item label="所属集群">{conn.cluster_id ? `#${conn.cluster_id}` : '-'}</Descriptions.Item>
          <Descriptions.Item label="创建时间">{conn.created_at || '-'}</Descriptions.Item>
        </Descriptions>
      </Card>
    )
  }, [conn, status])

  if (loading) {
    return <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 120 }}><Spin size="large" /></div>
  }
  if (!conn) {
    return (
      <Card>
        <Empty description="数据库实例不存在或无权访问">
          <Button type="primary" onClick={() => navigate('/resources/databases')}>返回数据库列表</Button>
        </Empty>
      </Card>
    )
  }

  return (
    <div>
      <Space style={{ marginBottom: 12 }} size={12} wrap>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/resources/databases')}>返回列表</Button>
        <b style={{ fontSize: 16 }}>{conn.name}</b>
        <Tag color={TYPE_COLOR[conn.db_type || ''] || 'default'}>{conn.db_type}</Tag>
        <Tag color={status.color}>{status.label}</Tag>
        <span style={{ color: '#8c8c8c', fontSize: 12 }}>{conn.host}{conn.port ? `:${conn.port}` : ''}</span>
        <Button size="small" icon={<ReloadOutlined />} onClick={load}>刷新</Button>
        {isAdmin() && conn.source !== 'agent' && (
          <Button size="small" loading={collecting} onClick={doCollect}>立即采集</Button>
        )}
        <Button size="small" type="primary" ghost icon={<ThunderboltOutlined />} onClick={() => setSp({ tab: 'diagnosis' }, { replace: true })}>
          一键诊断
        </Button>
      </Space>
      <Tabs
        activeKey={tab}
        onChange={(k) => setSp({ tab: k }, { replace: true })}
        destroyInactiveTabPane
        items={[
          { key: 'overview', label: '概览', children: overview },
          { key: 'monitor', label: '监控', children: <DbMonitorPanel connId={connId} dbType={conn.db_type} /> },
          { key: 'slow-sql', label: '慢 SQL', children: <DbSlowSqlPanel connId={connId} /> },
          { key: 'anomalies', label: '异常', children: <DbAnomalyPanel connId={connId} /> },
          { key: 'console', label: 'SQL 工作台', children: <DbConsolePanel conn={conn} /> },
          { key: 'diagnosis', label: '诊断', children: <DbDiagnosisPanel connId={connId} /> },
          { key: 'approval-policy', label: '审核策略', children: <DbApprovalPolicyPanel connId={connId} /> },
        ]}
      />
    </div>
  )
}
