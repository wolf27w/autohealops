// 发布弹窗（R19）：选分支 → 发布 → Blue Ocean 式阶段视图（每阶段看日志）
import { useEffect, useState } from 'react'
import { Modal, Select, Input, Button, Space, Spin, Alert } from 'antd'
import { getReleaseBranches, triggerRelease } from '../services/api'
import PipelineView from './PipelineView'

interface Props {
  open: boolean
  clusterId: number
  namespace: string
  serviceName: string
  onClose: () => void
}

export default function ReleaseModal({ open, clusterId, namespace, serviceName, onClose }: Props) {
  const [choices, setChoices] = useState<string[]>([])
  const [manual, setManual] = useState(false)
  const [branch, setBranch] = useState<string>()
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [triggering, setTriggering] = useState(false)
  const [build, setBuild] = useState<{ buildId: number; number: number; job: string } | null>(null)

  useEffect(() => {
    if (!open) return
    setChoices([]); setManual(false); setBranch(undefined); setErr(''); setBuild(null)
    setLoading(true)
    getReleaseBranches({ cluster_id: clusterId, namespace, service_name: serviceName })
      .then((d) => {
        setChoices(d?.choices || [])
        setManual(!!d?.manual || !(d?.choices || []).length)
        if (d?.default) setBranch(d.default)
      })
      .catch((e: unknown) => setErr((e as { response?: { data?: { message?: string } } })?.response?.data?.message || '取分支失败'))
      .finally(() => setLoading(false))
  }, [open, clusterId, namespace, serviceName])

  const doTrigger = async () => {
    setErr(''); setTriggering(true)
    try {
      const r = await triggerRelease({ cluster_id: clusterId, namespace, service_name: serviceName, branch })
      setBuild({ buildId: r.build_id, number: r.build_number, job: r.job_name })
    } catch (e) {
      setErr((e as { response?: { data?: { message?: string } } })?.response?.data?.message || '触发失败')
    } finally {
      setTriggering(false)
    }
  }

  return (
    <Modal open={open} onCancel={onClose} footer={null} width={820} title={`发布：${serviceName}`} destroyOnClose>
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <Space wrap>
          <span style={{ color: '#8c8c8c' }}>namespace</span><b>{namespace}</b>
          <span style={{ color: '#8c8c8c' }}>服务</span><b>{serviceName}</b>
        </Space>

        {!build && (
          <>
            <Space align="center">
              <span>分支</span>
              {loading ? <Spin size="small" /> : manual
                ? <Input placeholder="手输分支/tag" value={branch} onChange={(e) => setBranch(e.target.value)} style={{ width: 240 }} />
                : <Select placeholder="选择分支" value={branch} onChange={setBranch} showSearch style={{ width: 300 }}
                    options={choices.map((b) => ({ value: b, label: b }))} />}
            </Space>
            <div style={{ color: '#8c8c8c', fontSize: 12 }}>
              发布用平台配置的服务账号触发，构建描述会标注你的平台用户名（Jenkins 里可见）。
            </div>
            {err && <Alert type="error" message={err} showIcon />}
            <Button type="primary" loading={triggering} onClick={doTrigger} disabled={!branch || loading}>发布</Button>
          </>
        )}

        {build && (
          <PipelineView buildId={build.buildId} jobName={build.job} buildNumber={build.number} live />
        )}
      </Space>
    </Modal>
  )
}
