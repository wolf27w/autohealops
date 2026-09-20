// 审计追溯页：按业务维度（集群/命名空间/资源）展示，替代难区分的 TraceID
import { useEffect, useState } from 'react'
import { Input, Button, Table, Drawer, Timeline, Modal, message, Space, Select, Tag } from 'antd'
import { searchAudit, getAuditTrace, verifyAuditChain, getClusters } from '../services/api'
import type { Cluster } from '../types'

interface AuditRecord {
  id: number
  trace_id: string
  cluster_name?: string
  namespace?: string
  resource?: string
  fault_type?: string
  created_at?: string
  [key: string]: unknown
}

interface TraceRecord {
  trigger_snapshot?: string
  ai_call_detail?: string
  decision_detail?: string
  execution_detail?: string
  verification_detail?: string
  created_at?: string
}

interface VerifyResult {
  total_records?: number
  verified?: number
  integrity_ok?: boolean
}

export default function AuditTrace() {
  const [records, setRecords] = useState<AuditRecord[]>([])
  const [clusters, setClusters] = useState<Cluster[]>([])
  const [traceId, setTraceId] = useState('')
  const [clusterId, setClusterId] = useState<number | undefined>()
  const [namespace, setNamespace] = useState('')
  const [resource, setResource] = useState('')
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [trace, setTrace] = useState<TraceRecord[]>([])
  const [verifyModal, setVerifyModal] = useState(false)
  const [verifyResult, setVerifyResult] = useState<VerifyResult | null>(null)

  const load = (p = page) => {
    searchAudit({
      trace_id: traceId || undefined,
      cluster_id: clusterId,
      namespace: namespace || undefined,
      resource: resource || undefined,
      page: p, page_size: 20,
    }).then((d) => { const r = d as { items?: AuditRecord[]; total?: number }; setRecords(r.items || []); setTotal(r.total || 0) })
  }
  useEffect(() => { load(); getClusters().then((d) => setClusters(d || [])) }, [])

  const showTrace = async (id: string) => {
    const d = (await getAuditTrace(id)) as { records?: TraceRecord[] }
    setTrace(d?.records || [])
    setDrawerOpen(true)
  }

  // 把审计记录展开为「故障→诊断→决策→执行→恢复」阶段节点
  const stageItems = trace.flatMap((r: TraceRecord) => {
    const stages = [
      { label: '① 故障触发', color: 'red', content: r.trigger_snapshot },
      { label: '② AI诊断（原因）', color: 'blue', content: r.ai_call_detail },
      { label: '③ 决策（方案/审批）', color: 'orange', content: r.decision_detail },
      { label: '④ 执行处理', color: 'purple', content: r.execution_detail },
      { label: '⑤ 恢复验证', color: 'green', content: r.verification_detail },
    ].filter((s) => s.content)
    return stages.map((s) => ({
      color: s.color,
      children: (
        <div>
          <b style={{ color: s.color }}>{s.label}</b>
          <div style={{ color: '#555', marginTop: 4 }}>{s.content}</div>
          {r.created_at && <div style={{ fontSize: 12, color: '#999' }}>{r.created_at}</div>}
        </div>
      ),
    }))
  })

  const handleVerify = async () => {
    const r = (await verifyAuditChain()) as VerifyResult
    setVerifyResult(r)
    setVerifyModal(true)
  }

  return (
    <div>
      <Space style={{ marginBottom: 16 }} wrap>
        <Select
          allowClear placeholder="集群" style={{ width: 160 }} value={clusterId}
          onChange={(v) => { setClusterId(v); setPage(1) }}
          options={clusters.map((c) => ({ value: c.id, label: c.name }))}
        />
        <Input placeholder="命名空间" style={{ width: 160 }} value={namespace} onChange={(e) => setNamespace(e.target.value)} />
        <Input placeholder="资源/Pod" style={{ width: 160 }} value={resource} onChange={(e) => setResource(e.target.value)} />
        <Input placeholder="TraceID（可选）" style={{ width: 200 }} value={traceId} onChange={(e) => setTraceId(e.target.value)} />
        <Button type="primary" onClick={() => { setPage(1); load(1) }}>搜索</Button>
        <Button onClick={handleVerify}>哈希链完整性验证</Button>
      </Space>
      <Table rowKey="id" dataSource={records}
        pagination={{ current: page, pageSize: 20, total, showSizeChanger: false, onChange: (p) => { setPage(p); load(p) } }}
        columns={[
          { title: '集群', dataIndex: 'cluster_name', render: (v) => v || '-' },
          { title: '命名空间', dataIndex: 'namespace', render: (v) => v || '-' },
          { title: '资源', dataIndex: 'resource', render: (v) => v || '-' },
          { title: '故障类型', dataIndex: 'fault_type', render: (v) => (v ? <Tag color="orange">{v}</Tag> : '-') },
          { title: '时间', dataIndex: 'created_at', render: (v) => (v ? v.slice(0, 19).replace('T', ' ') : '-') },
          { title: '操作', render: (_, r) => <Button size="small" type="primary" onClick={() => showTrace(r.trace_id)}>查看完整链路</Button> },
        ]} />
      <Drawer title="完整处理链路（故障 → 原因 → 处理 → 恢复）" open={drawerOpen} onClose={() => setDrawerOpen(false)} width={680}>
        {stageItems.length === 0 ? <div>该 Trace 暂无审计记录</div> : <Timeline items={stageItems} />}
      </Drawer>
      <Modal title="哈希链完整性验证" open={verifyModal} onCancel={() => setVerifyModal(false)} footer={null}>
        {verifyResult && (
          <div>
            <p>总记录数：{verifyResult.total_records}</p>
            <p>验证通过：{verifyResult.verified}</p>
            <p style={{ color: verifyResult.integrity_ok ? 'green' : 'red', fontWeight: 'bold' }}>
              {verifyResult.integrity_ok ? '✅ 完整性校验通过，哈希链未被篡改' : '❌ 检测到篡改！'}
            </p>
          </div>
        )}
      </Modal>
    </div>
  )
}
