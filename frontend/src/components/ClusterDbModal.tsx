// 集群内数据库：经 agent 查对方集群里的数据库（连接管理 + 只读查询）
import { useCallback, useEffect, useState } from 'react'
import { Modal, Button, Table, Form, Input, InputNumber, Select, Space, Divider, Popconfirm, message, Empty, Descriptions, Tag } from 'antd'
import {
  listClusterDbConnections, createClusterDbConnection, deleteClusterDbConnection, queryClusterDb,
  getClusterDbHealth, type ClusterDbConn, type DbHealthData,
} from '../services/api'

export default function ClusterDbModal({ clusterId, onClose }: { clusterId: number | null; onClose: () => void }) {
  const [conns, setConns] = useState<ClusterDbConn[]>([])
  const [loading, setLoading] = useState(false)
  const [selId, setSelId] = useState<number | undefined>(undefined)
  const [sql, setSql] = useState('SHOW DATABASES')
  const [result, setResult] = useState<{ columns: string[]; rows: unknown[][] } | null>(null)
  const [running, setRunning] = useState(false)
  const [health, setHealth] = useState<DbHealthData | null>(null)
  const [healthLoading, setHealthLoading] = useState(false)
  const [form] = Form.useForm()

  const load = useCallback(() => {
    if (!clusterId) return
    setLoading(true)
    listClusterDbConnections(clusterId).then((d) => { setConns(d || []); setSelId((s) => s ?? (d?.[0]?.id)) }).finally(() => setLoading(false))
  }, [clusterId])

  useEffect(() => { load() }, [load])

  if (clusterId === null) return null

  const doCreate = async (v: Record<string, unknown>) => {
    await createClusterDbConnection(clusterId, v)
    message.success('已添加')
    form.resetFields()
    load()
  }
  const doDelete = async (id: number) => {
    await deleteClusterDbConnection(clusterId, id)
    message.success('已删除')
    if (selId === id) setSelId(undefined)
    load()
  }
  const doQuery = async () => {
    if (!selId) { message.warning('请先选择连接'); return }
    if (!sql.trim()) { message.warning('请输入 SQL'); return }
    setRunning(true); setResult(null)
    try {
      const d = await queryClusterDb(clusterId, selId, sql.trim())
      setResult({ columns: d?.columns || [], rows: d?.rows || [] })
    } finally { setRunning(false) }
  }

  const doHealth = async () => {
    if (!selId) { message.warning('请先选择连接'); return }
    setHealthLoading(true); setHealth(null)
    try { setHealth(await getClusterDbHealth(clusterId, selId)) } finally { setHealthLoading(false) }
  }

  const healthBlock = health ? (
    <div style={{ marginTop: 12 }}>
      <Descriptions size="small" column={3} bordered>
        <Descriptions.Item label="版本">{health.version || '-'}</Descriptions.Item>
        <Descriptions.Item label="运行时长">{Math.floor((health.uptime_seconds || 0) / 3600)} 小时</Descriptions.Item>
        <Descriptions.Item label="库">{health.database || '-'}</Descriptions.Item>
        <Descriptions.Item label="连接数">
          {health.threads_connected ?? '-'} / {health.max_connections ?? '-'}
        </Descriptions.Item>
        <Descriptions.Item label="活跃线程">{health.threads_running ?? '-'}</Descriptions.Item>
        <Descriptions.Item label="历史最大连接">{health.max_used_connections ?? '-'}</Descriptions.Item>
        <Descriptions.Item label="慢查询累计">{health.slow_queries ?? '-'}</Descriptions.Item>
        <Descriptions.Item label="行锁等待累计">{health.row_lock_waits ?? '-'}</Descriptions.Item>
        <Descriptions.Item label="当前锁等待">
          <Tag color={(health.lock_waits ?? 0) > 0 ? 'red' : 'green'}>{health.lock_waits ?? '-'}</Tag>
        </Descriptions.Item>
      </Descriptions>
      <Divider orientation="left" style={{ margin: '12px 0 6px' }}>表空间 TOP10</Divider>
      <Table size="small" rowKey="table" pagination={false} dataSource={health.top_tables || []}
        locale={{ emptyText: '无数据' }}
        columns={[
          { title: '表', dataIndex: 'table' },
          { title: '行数', dataIndex: 'rows', width: 120 },
          { title: '大小(MB)', dataIndex: 'size_mb', width: 120 },
        ]} />
      <Divider orientation="left" style={{ margin: '12px 0 6px' }}>慢 SQL TOP5</Divider>
      <Table size="small" rowKey={(_, i) => String(i)} pagination={false} dataSource={health.top_slow || []}
        locale={{ emptyText: '无数据' }}
        columns={[
          { title: 'SQL', dataIndex: 'sql_text', render: (v: string) => <span style={{ fontSize: 12 }}>{(v || '').slice(0, 120)}</span> },
          { title: '次数', dataIndex: 'exec_count', width: 90 },
          { title: '平均(ms)', dataIndex: 'avg_ms', width: 100, render: (v: number) => Number(v || 0).toFixed(2) },
          { title: '最大(ms)', dataIndex: 'max_ms', width: 100, render: (v: number) => Number(v || 0).toFixed(2) },
        ]} />
    </div>
  ) : null

  const resultTable = result ? (
    <Table
      size="small"
      pagination={false}
      scroll={{ x: 'max-content', y: 300 }}
      dataSource={(result.rows || []).map((r, i) => ({ key: i, row: r }))}
      columns={(result.columns || []).map((c, i) => ({
        title: c, dataIndex: 'row', width: 160,
        render: (row: unknown[]) => (row?.[i] === null || row?.[i] === undefined ? 'NULL' : String(row[i])),
      }))}
    />
  ) : <Empty description="输入 SQL 后点击执行" />

  return (
    <Modal title="集群内数据库（经 agent 查询）" open onCancel={onClose} footer={null} width={900} destroyOnClose>
      <Divider orientation="left" style={{ marginTop: 0 }}>连接</Divider>
      <Table
        size="small"
        rowKey="id"
        loading={loading}
        pagination={false}
        dataSource={conns}
        locale={{ emptyText: '暂无连接' }}
        rowSelection={{ type: 'radio', selectedRowKeys: selId ? [selId] : [], onChange: (k) => setSelId(k[0] as number) }}
        columns={[
          { title: '名称', dataIndex: 'name', render: (v: string) => v || '-' },
          { title: '类型', dataIndex: 'db_type', width: 80 },
          { title: '地址', render: (_: unknown, r: ClusterDbConn) => `${r.host}:${r.port}` },
          { title: '库', dataIndex: 'database', render: (v: string) => v || '-' },
          { title: '账号', dataIndex: 'username' },
          {
            title: '操作', width: 70, render: (_: unknown, r: ClusterDbConn) => (
              <Popconfirm title="删除该连接？" onConfirm={() => doDelete(r.id)}>
                <Button size="small" type="link" danger>删除</Button>
              </Popconfirm>
            ),
          },
        ]}
      />
      <Form form={form} layout="inline" style={{ marginTop: 8, rowGap: 8 }} onFinish={doCreate}>
        <Form.Item name="name" label="名称"><Input placeholder="可选" style={{ width: 120 }} /></Form.Item>
        <Form.Item name="db_type" label="类型" initialValue="mysql"><Select style={{ width: 90 }} options={[{ value: 'mysql', label: 'MySQL' }, { value: 'postgres', label: 'PG' }]} /></Form.Item>
        <Form.Item name="host" label="Host" rules={[{ required: true, message: '必填' }]}><Input placeholder="Service 名/IP" style={{ width: 150 }} /></Form.Item>
        <Form.Item name="port" label="端口" initialValue={3306}><InputNumber min={1} max={65535} style={{ width: 80 }} /></Form.Item>
        <Form.Item name="database" label="库"><Input placeholder="库名" style={{ width: 110 }} /></Form.Item>
        <Form.Item name="username" label="账号"><Input style={{ width: 100 }} /></Form.Item>
        <Form.Item name="password" label="密码"><Input.Password style={{ width: 120 }} /></Form.Item>
        <Form.Item><Button type="primary" htmlType="submit" size="small">添加</Button></Form.Item>
      </Form>

      <Divider orientation="left">查询（只读：select/show/describe/explain）</Divider>
      <Space.Compact style={{ width: '100%', marginBottom: 8 }}>
        <Input.TextArea rows={2} value={sql} onChange={(e) => setSql(e.target.value)} placeholder="SHOW PROCESSLIST;  SELECT ... " style={{ fontFamily: 'monospace' }} />
      </Space.Compact>
      <Space style={{ marginBottom: 12 }}>
        <Button type="primary" size="small" loading={running} onClick={doQuery}>执行</Button>
        <Button size="small" loading={healthLoading} onClick={doHealth}>健康检查</Button>
        <span style={{ fontSize: 12, color: '#999' }}>最多返回 200 行</span>
      </Space>
      {resultTable}
      {healthBlock}
    </Modal>
  )
}
