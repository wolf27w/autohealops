// 发布弹窗（R19）：选分支 → 触发 → 展示 pipeline 阶段 + 实时日志
import { useEffect, useRef, useState } from 'react'
import { Modal, Select, Input, Button, Space, Tag, Spin, Alert, Divider } from 'antd'
import { getReleaseBranches, triggerRelease, getReleaseBuildStatus, getReleaseBuildLog } from '../services/api'

interface Props {
  open: boolean
  clusterId: number
  namespace: string
  serviceName: string
  onClose: () => void
}

export default function ReleaseModal({ open, clusterId, namespace, serviceName, onClose }: Props) {
  const [branches, setBranches] = useState<string[]>([])
  const [manual, setManual] = useState(false)
  const [branch, setBranch] = useState<string>()
  const [loadingBranches, setLoadingBranches] = useState(false)
  const [err, setErr] = useState('')
  const [triggering, setTriggering] = useState(false)
  const [build, setBuild] = useState<{ buildId: number; number: number; job: string } | null>(null)
  const [stages, setStages] = useState<{ name: string; status: string }[]>([])
  const [status, setStatus] = useState('')
  const [log, setLog] = useState('')
  const [logStart, setLogStart] = useState(0)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    if (!open) return
    setBranches([]); setManual(false); setBranch(undefined); setErr('')
    setBuild(null); setStages([]); setStatus(''); setLog(''); setLogStart(0)
    setLoadingBranches(true)
    getReleaseBranches({ cluster_id: clusterId, namespace, service_name: serviceName })
      .then((d) => {
        setBranches(d?.choices || [])
        setManual(!!d?.manual || !(d?.choices || []).length)
        if (d?.default) setBranch(d.default)
      })
      .catch((e: unknown) => setErr((e as { response?: { data?: { message?: string } } })?.response?.data?.message || '取分支失败'))
      .finally(() => setLoadingBranches(false))
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [open, clusterId, namespace, serviceName])

  const doTrigger = async () => {
    setErr(''); setTriggering(true)
    try {
      const r = await triggerRelease({ cluster_id: clusterId, namespace, service_name: serviceName, branch })
      setBuild({ buildId: r.build_id, number: r.build_number, job: r.job_name })
      setStatus('queued')
      poll(r.build_id)
    } catch (e) {
      setErr((e as { response?: { data?: { message?: string } } })?.response?.data?.message || '触发失败')
    } finally {
      setTriggering(false)
    }
  }

  const poll = (buildId: number) => {
    if (timerRef.current) clearInterval(timerRef.current)
    timerRef.current = setInterval(async () => {
      try {
        const st = await getReleaseBuildStatus(buildId)
        setStatus(st.status)
        setStages(st.stages || [])
        if (!st.building && st.result) {
          if (timerRef.current) clearInterval(timerRef.current)
        }
      } catch { /* 轮询失败忽略 */ }
      try {
        const lg = await getReleaseBuildLog(buildId, logStart)
        if (lg.text) {
          setLog((prev) => prev + lg.text)
          setLogStart(lg.size)
        }
      } catch { /* 日志轮询失败忽略 */ }
    }, 2500)
  }

  const statusTag = (s: string) => {
    const m: Record<string, { c: string; l: string }> = {
      queued: { c: 'default', l: '排队中' }, running: { c: 'processing', l: '构建中' },
      success: { c: 'success', l: '成功' }, failure: { c: 'error', l: '失败' }, aborted: { c: 'warning', l: '已中止' },
    }
    return m[s] || { c: 'default', l: s }
  }

  return (
    <Modal open={open} onCancel={onClose} footer={null} width={760} title={`发布：${serviceName}`} destroyOnClose>
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <Space>
          <span style={{ color: '#8c8c8c' }}>namespace</span><b>{namespace}</b>
          <span style={{ color: '#8c8c8c' }}>服务</span><b>{serviceName}</b>
        </Space>

        {!build && (
          <>
            <Space align="center">
              <span>分支</span>
              {loadingBranches ? <Spin size="small" /> : manual
                ? <Input placeholder="手输分支" value={branch} onChange={(e) => setBranch(e.target.value)} style={{ width: 220 }} />
                : <Select placeholder="选择分支" value={branch} onChange={setBranch} showSearch style={{ width: 240 }}
                    options={branches.map((b) => ({ value: b, label: b }))} />}
            </Space>
            {err && <Alert type="error" message={err} showIcon />}
            <Button type="primary" loading={triggering} onClick={doTrigger} disabled={!branch}>发布</Button>
          </>
        )}

        {build && (
          <>
            <Space wrap>
              <Tag color={statusTag(status).c}>{statusTag(status).l}</Tag>
              <span style={{ color: '#8c8c8c', fontSize: 13 }}>Job：{build.job}　构建号 #{build.number}</span>
            </Space>
            {stages.length > 0 && (
              <Space wrap>
                {stages.map((s, i) => {
                  const ok = s.status === 'SUCCESS'; const fail = s.status === 'FAILED'
                  const run = s.status === 'IN_PROGRESS'
                  return <Tag key={i} color={ok ? 'green' : fail ? 'red' : run ? 'processing' : 'default'}>{s.name}</Tag>
                })}
              </Space>
            )}
            <Divider style={{ margin: '8px 0' }} />
            <pre style={{ maxHeight: 360, overflow: 'auto', background: '#0b0b0b', color: '#d9d9d9', padding: 12, borderRadius: 6, fontSize: 12, margin: 0 }}>
              {log || '（等待日志…）'}
            </pre>
          </>
        )}
      </Space>
    </Modal>
  )
}
