// 数据库「执行命令」弹窗（诊断中心用）：DML/DDL，仅管理员，二次确认 + 全额审计。
// 只读查询请用「数据库监控」页的 SQL 控制台（那边有下载 CSV）。
import { useState } from 'react'
import { Modal, Input, Alert, Button, Space, Tag, Table, Popconfirm } from 'antd'
import { execDBConnection } from '../services/api'

interface Props {
  conn: { id: number; name: string; database?: string; source?: string } | null
  onClose: () => void
}

export default function DbExecModal({ conn, onClose }: Props) {
  const [sql, setSql] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [res, setRes] = useState<{ columns?: string[]; rows?: unknown[][]; affected_rows?: number } | null>(null)

  const isAgent = (conn?.source || 'direct') === 'agent'

  return (
    <Modal
      title={`执行命令：${conn?.name || ''}${conn?.database ? ` / ${conn.database}` : ''}`}
      open={!!conn}
      onCancel={() => { setRes(null); setErr(''); onClose() }}
      footer={null}
      width={880}
    >
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 8 }}
        message="执行模式会真实修改数据库，仅管理员可用，无法自动回滚。"
        description="平台记录：谁 / 集群 / 库 / SQL 摘要 / 耗时 / 影响行数 / 成败（不记录结果数据）。"
      />
      {!isAgent && (
        <Alert type="error" showIcon style={{ marginBottom: 8 }}
          message="仅 agent 接入的库支持平台执行（平台不保存该库账密）。直连库请用数据库客户端操作。" />
      )}
      <Input.TextArea rows={7} value={sql} onChange={(e) => setSql(e.target.value)}
        disabled={!isAgent} placeholder="update t set c=1 where id=1" />

      {err && <Alert type="error" showIcon style={{ marginTop: 8 }} message={err} />}

      <Space style={{ marginTop: 10 }}>
        <Popconfirm
          title="确认执行？"
          description="将真实修改数据库，无法自动回滚。"
          okText="确认执行" cancelText="取消" okButtonProps={{ danger: true }}
          onConfirm={async () => {
            if (!conn) return
            setBusy(true); setErr(''); setRes(null)
            try {
              const d = await execDBConnection(conn.id, sql)
              setRes(d as unknown as { columns?: string[]; rows?: unknown[][]; affected_rows?: number })
            } catch (e) {
              const x = e as { response?: { data?: { message?: string } }; message?: string }
              setErr(x?.response?.data?.message || x?.message || '执行失败')
            } finally { setBusy(false) }
          }}
        >
          <Button danger disabled={!isAgent || !sql.trim()} loading={busy}>执行命令</Button>
        </Popconfirm>
        {res && <Tag color={(res.affected_rows || 0) > 0 ? 'green' : 'default'}>影响行数：{res.affected_rows ?? 0}</Tag>}
      </Space>

      {res && (res.rows || []).length > 0 && (
        <div style={{ marginTop: 10 }}>
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
        </div>
      )}
    </Modal>
  )
}
