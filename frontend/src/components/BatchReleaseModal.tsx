// 批量发布弹窗：勾选多个服务 → 选一个分支 → 批量触发
import { useEffect, useState } from 'react'
import { Modal, Select, Input, Button, Space, Spin, Alert, List, Tag } from 'antd'
import { getReleaseBranches, batchTriggerRelease } from '../services/api'
import type { BatchReleaseResult } from '../services/api'

interface Props {
  open: boolean
  clusterId: number
  namespace: string
  serviceNames: string[]
  onClose: () => void
  onDone?: () => void
}

export default function BatchReleaseModal({ open, clusterId, namespace, serviceNames, onClose, onDone }: Props) {
  const [choices, setChoices] = useState<string[]>([])
  const [manual, setManual] = useState(false)
  const [branch, setBranch] = useState<string>()
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [running, setRunning] = useState(false)
  const [results, setResults] = useState<BatchReleaseResult[] | null>(null)

  useEffect(() => {
    if (!open || !serviceNames.length) return
    setChoices([]); setManual(false); setBranch(undefined); setErr(''); setResults(null)
    setLoading(true)
    // 用第一个服务取分支选项（同一批服务通常指向同一个 job）
    getReleaseBranches({ cluster_id: clusterId, namespace, service_name: serviceNames[0] })
      .then((d) => {
        setChoices(d?.choices || [])
        setManual(!!d?.manual || !(d?.choices || []).length)
        if (d?.default) setBranch(d.default)
      })
      .catch((e: unknown) => setErr((e as { response?: { data?: { message?: string } } })?.response?.data?.message || '取分支失败'))
      .finally(() => setLoading(false))
  }, [open, clusterId, namespace, serviceNames])

  const doBatch = async () => {
    setErr(''); setRunning(true); setResults(null)
    try {
      const r = await batchTriggerRelease({ cluster_id: clusterId, namespace, service_names: serviceNames, branch })
      setResults(r.results || [])
      onDone?.()
    } catch (e) {
      setErr((e as { response?: { data?: { message?: string } } })?.response?.data?.message || '批量发布失败')
    } finally {
      setRunning(false)
    }
  }

  return (
    <Modal open={open} onCancel={onClose} footer={null} width={720} title={`批量发布（${serviceNames.length} 个服务）`} destroyOnClose>
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <div style={{ color: '#8c8c8c', fontSize: 12 }}>namespace：<b style={{ color: '#333' }}>{namespace}</b>　将发布：{serviceNames.join('、')}</div>

        {!results && (
          <>
            <Space align="center">
              <span>分支</span>
              {loading ? <Spin size="small" /> : manual
                ? <Input placeholder="手输分支/tag" value={branch} onChange={(e) => setBranch(e.target.value)} style={{ width: 240 }} />
                : <Select placeholder="选择分支" value={branch} onChange={setBranch} showSearch style={{ width: 300 }}
                    options={choices.map((b) => ({ value: b, label: b }))} />}
            </Space>
            <div style={{ color: '#8c8c8c', fontSize: 12 }}>
              同一分支应用到所有选中服务；未配置发布映射的服务会被跳过并在结果里标出。
            </div>
            {err && <Alert type="error" message={err} showIcon />}
            <Button type="primary" loading={running} onClick={doBatch} disabled={!branch || loading}>批量发布</Button>
          </>
        )}

        {results && (
          <>
            <Alert type="success" showIcon
              message={`完成：成功 ${results.filter((r) => r.ok).length} 个，失败 ${results.filter((r) => !r.ok).length} 个`} />
            <List size="small" bordered
              dataSource={results}
              renderItem={(r) => (
                <List.Item>
                  <Space>
                    <Tag color={r.ok ? 'green' : 'red'}>{r.ok ? '成功' : '失败'}</Tag>
                    <b>{r.service}</b>
                    {r.ok ? <span style={{ color: '#8c8c8c' }}>构建号 #{r.build_number}</span> : <span style={{ color: '#cf1322' }}>{r.error}</span>}
                  </Space>
                </List.Item>
              )} />
            <Button onClick={onClose}>关闭</Button>
          </>
        )}
      </Space>
    </Modal>
  )
}
