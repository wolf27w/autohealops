// 批量诊断弹窗：接收多选实例，逐条发起诊断并展示逐实例结果（成功/失败/跳过 + 原因）。
import { useEffect, useState } from 'react'
import { Button, Modal, Result, Space, Spin, Table, Tag } from 'antd'
import { runDbDiagnoseBatch, type DbBatchDiagnoseItem, type DbBatchDiagnoseResult } from '../../services/api'

export interface BatchDiagTarget {
  id: number
  name: string
}

interface BatchResultItem extends DbBatchDiagnoseItem {}

interface BatchResult extends DbBatchDiagnoseResult {}

const STATUS_TAG: Record<BatchResultItem['status'], { color: string; label: string }> = {
  success: { color: 'green', label: '成功' },
  failed: { color: 'red', label: '失败' },
  skipped: { color: 'orange', label: '跳过' },
}


export default function DbBatchDiagnoseModal({
  open, targets, onClose, onDone,
}: {
  open: boolean
  targets: BatchDiagTarget[]
  onClose: () => void
  onDone?: () => void
}) {
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<BatchResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const nameOf = (id: number) => targets.find((t) => t.id === id)?.name ?? `#${id}`

  const start = async () => {
    if (targets.length === 0) return
    setRunning(true)
    setResult(null)
    setError(null)
    try {
      const r = await runDbDiagnoseBatch(targets.map((t) => t.id))
      setResult(r)
      onDone?.()
    } catch (e) {
      setError((e as Error)?.message || '批量诊断请求失败')
    } finally {
      setRunning(false)
    }
  }

  // 打开即发起一次；关闭/切换选择时重置
  useEffect(() => {
    if (open) {
      setResult(null)
      setError(null)
      void start()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const columns = [
    { title: '实例', dataIndex: 'conn_id', render: (v: number) => nameOf(v) },
    {
      title: '状态', dataIndex: 'status', width: 90,
      render: (v: BatchResultItem['status']) => {
        const s = STATUS_TAG[v]
        return <Tag color={s.color}>{s.label}</Tag>
      },
    },
    {
      title: '详情', dataIndex: 'message', ellipsis: true,
      render: (v: string, r: BatchResultItem) =>
        r.status === 'success' && r.report_id ? `报告 #${r.report_id} · ${v || ''}` : v || '-',
    },
  ]

  return (
    <Modal
      title={`批量诊断（${targets.length} 个实例）`}
      open={open}
      onCancel={onClose}
      footer={[
        <Button key="retry" disabled={running || targets.length === 0} onClick={start}>重新诊断</Button>,
        <Button key="close" type="primary" onClick={onClose}>关闭</Button>,
      ]}
      width={680}
      destroyOnClose
    >
      {running ? (
        <div style={{ textAlign: 'center', padding: '32px 0' }}>
          <Spin size="large" />
          <div style={{ marginTop: 12, color: '#8c8c8c' }}>
            正在逐实例发起诊断（采集 + AI 分析），请稍候…
          </div>
        </div>
      ) : error ? (
        <Result status="error" title="批量诊断失败" subTitle={error} />
      ) : result ? (
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Space size="large">
            <span>成功 <b style={{ color: '#389e0d' }}>{result.success}</b></span>
            <span>失败 <b style={{ color: '#cf1322' }}>{result.failed}</b></span>
            <span>跳过 <b style={{ color: '#d46b08' }}>{result.skipped}</b></span>
            <span style={{ color: '#8c8c8c' }}>共 {result.total} 个</span>
          </Space>
          <Table
            rowKey="conn_id"
            size="small"
            dataSource={result.results}
            columns={columns}
            pagination={false}
            scroll={{ y: 360 }}
          />
        </Space>
      ) : (
        <div style={{ textAlign: 'center', padding: '24px 0', color: '#8c8c8c' }}>
          已选择 {targets.length} 个实例，点击「重新诊断」发起。
        </div>
      )}
    </Modal>
  )
}
