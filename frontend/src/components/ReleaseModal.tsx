// 发布弹窗（R19）：选 Jenkins 实例 + Job → 动态渲染 Job 参数 → 触发 → 阶段 + 实时日志
import { useEffect, useRef, useState } from 'react'
import { Modal, Select, Input, Button, Space, Tag, Spin, Alert, Divider, message, Switch } from 'antd'
import {
  getJenkinsInstances, getReleaseJobs, getReleaseJobParams, triggerRelease,
  getReleaseBuildStatus, getReleaseBuildLog, getMyJenkinsTokens, setMyJenkinsToken,
} from '../services/api'
import type { JenkinsInstance, JobParam, MyJenkinsToken } from '../services/api'

interface Props {
  open: boolean
  clusterId: number
  namespace: string
  serviceName: string
  onClose: () => void
}

export default function ReleaseModal({ open, clusterId, namespace, serviceName, onClose }: Props) {
  const [insts, setInsts] = useState<JenkinsInstance[]>([])
  const [instId, setInstId] = useState<number>()
  const [jobs, setJobs] = useState<{ name: string }[]>([])
  const [job, setJob] = useState<string>()
  const [params, setParams] = useState<JobParam[]>([])
  const [values, setValues] = useState<Record<string, unknown>>({})
  const [loadingJobs, setLoadingJobs] = useState(false)
  const [loadingParams, setLoadingParams] = useState(false)
  const [err, setErr] = useState('')
  const [triggering, setTriggering] = useState(false)
  const [build, setBuild] = useState<{ buildId: number; number: number; job: string } | null>(null)
  const [stages, setStages] = useState<{ name: string; status: string }[]>([])
  const [status, setStatus] = useState('')
  const [log, setLog] = useState('')
  const [logStart, setLogStart] = useState(0)
  const [authOpen, setAuthOpen] = useState(false)
  const [myTokens, setMyTokens] = useState<MyJenkinsToken[]>([])
  const [authForms, setAuthForms] = useState<Record<number, { username: string; token: string }>>({})
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    if (!open) return
    setBuild(null); setStages([]); setStatus(''); setLog(''); setLogStart(0); setErr('')
    setJobs([]); setJob(undefined); setParams([]); setValues({})
    getJenkinsInstances().then((d) => {
      const arr = d || []
      setInsts(arr)
      if (arr.length) { setInstId(arr[0].id); loadJobs(arr[0].id) }
    }).catch((e: unknown) => setErr((e as { response?: { data?: { message?: string } } })?.response?.data?.message || '取实例失败'))
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [open, clusterId, namespace, serviceName])

  const loadJobs = (iid: number) => {
    setLoadingJobs(true); setJobs([]); setJob(undefined); setParams([]); setValues({})
    getReleaseJobs(iid).then((d) => setJobs(d || [])).catch((e: unknown) => setErr((e as { response?: { data?: { message?: string } } })?.response?.data?.message || '取 job 失败')).finally(() => setLoadingJobs(false))
  }

  const loadParams = (iid: number, j: string) => {
    setLoadingParams(true); setParams([]); setValues({})
    getReleaseJobParams(iid, j).then((d) => {
      const arr = d || []
      setParams(arr)
      const init: Record<string, unknown> = {}
      arr.forEach((p) => {
        if (p.kind === 'boolean') init[p.name] = false
        else init[p.name] = p.default ?? ''
      })
      setValues(init)
    }).catch((e: unknown) => setErr((e as { response?: { data?: { message?: string } } })?.response?.data?.message || '取参数失败')).finally(() => setLoadingParams(false))
  }

  const doTrigger = async () => {
    setErr(''); setTriggering(true)
    const p: Record<string, unknown> = {}
    for (const d of params) {
      if (d.kind === 'file') continue  // 文件参数无法经 API 传，跳过
      let v = values[d.name]
      if (d.kind === 'multiselect' && typeof v === 'string') {
        v = v.split(/[;,]/).map((s) => s.trim()).filter(Boolean)
      }
      if (v === undefined || v === null || v === '') continue
      p[d.name] = v
    }
    try {
      const r = await triggerRelease({ cluster_id: clusterId, namespace, service_name: serviceName, jenkins_instance_id: instId!, job_name: job!, params: p })
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
        setStatus(st.status); setStages(st.stages || [])
        if (!st.building && st.result) { if (timerRef.current) clearInterval(timerRef.current) }
      } catch { /* ignore */ }
      try {
        const lg = await getReleaseBuildLog(buildId, logStart)
        if (lg.text) { setLog((prev) => prev + lg.text); setLogStart(lg.size) }
      } catch { /* ignore */ }
    }, 2500)
  }

  const openAuth = async () => {
    setAuthOpen(true)
    const list = (await getMyJenkinsTokens()) || []
    setMyTokens(list)
    const m: Record<number, { username: string; token: string }> = {}
    list.forEach((t) => { m[t.instance_id] = { username: t.username || '', token: '' } })
    setAuthForms(m)
  }

  const saveAuth = async (iid: number) => {
    const f = authForms[iid]; if (!f) return
    await setMyJenkinsToken(iid, { username: f.username, api_token: f.token })
    message.success('已保存（发布将用你的身份）')
    openAuth()
  }

  const statusTag = (s: string) => {
    const m: Record<string, { c: string; l: string }> = {
      queued: { c: 'default', l: '排队中' }, running: { c: 'processing', l: '构建中' },
      success: { c: 'success', l: '成功' }, failure: { c: 'error', l: '失败' }, aborted: { c: 'warning', l: '已中止' },
    }
    return m[s] || { c: 'default', l: s }
  }

  const renderField = (p: JobParam) => {
    const v = values[p.name]
    switch (p.kind) {
      case 'select':
        return <Select value={v as string} onChange={(x) => setValues((s) => ({ ...s, [p.name]: x }))}
          showSearch style={{ width: 280 }} options={(p.choices || []).map((c) => ({ value: c, label: c }))} />
      case 'boolean':
        return <Switch checked={!!v} onChange={(x) => setValues((s) => ({ ...s, [p.name]: x }))} />
      case 'textarea':
        return <Input.TextArea rows={3} value={v as string} onChange={(e) => setValues((s) => ({ ...s, [p.name]: e.target.value }))} style={{ width: 360 }} />
      case 'file':
        return <span style={{ color: '#8c8c8c' }}>文件参数需在 Jenkins 页面操作（本次跳过）</span>
      case 'multiselect':
        return <Input value={v as string} onChange={(e) => setValues((s) => ({ ...s, [p.name]: e.target.value }))}
          placeholder="多个用 ; 或 , 分隔" style={{ width: 360 }} />
      default:
        return <Input value={v as string} onChange={(e) => setValues((s) => ({ ...s, [p.name]: e.target.value }))}
          placeholder={p.kind === 'branch' ? '分支/tag' : ''} style={{ width: 280 }} />
    }
  }

  return (
    <Modal open={open} onCancel={onClose} footer={null} width={780} title={`发布：${serviceName}`} destroyOnClose>
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <Space wrap>
          <span style={{ color: '#8c8c8c' }}>namespace</span><b>{namespace}</b>
          <span style={{ color: '#8c8c8c' }}>服务</span><b>{serviceName}</b>
        </Space>

        {!build && (
          <>
            <Space wrap align="center">
              <span>Jenkins</span>
              <Select value={instId} style={{ width: 220 }} onChange={(v) => { setInstId(v); loadJobs(v) }}
                options={insts.map((i) => ({ value: i.id, label: `${i.name}（${i.url}）` }))} />
              <span>Job</span>
              <Select value={job} showSearch style={{ width: 320 }} loading={loadingJobs} filterOption={(inp, o) => String(o?.label ?? '').toLowerCase().includes(inp.toLowerCase())}
                placeholder="选择 Job" onChange={(v) => { setJob(v); loadParams(instId!, v) }}
                options={jobs.map((j) => ({ value: j.name, label: j.name }))} />
            </Space>

            {loadingParams ? <Spin size="small" /> : params.length > 0 && (
              <div style={{ maxHeight: 320, overflow: 'auto' }}>
                {params.map((p) => (
                  <div key={p.name} style={{ padding: '6px 0', display: 'flex', gap: 12, alignItems: 'center' }}>
                    <div style={{ width: 180, textAlign: 'right' }}>
                      <b>{p.name}</b>
                      {p.description && <div style={{ color: '#8c8c8c', fontSize: 12 }}>{p.description}</div>}
                    </div>
                    {renderField(p)}
                  </div>
                ))}
              </div>
            )}

            {err && <Alert type="error" message={err} showIcon />}
            <Space>
              <Button type="primary" loading={triggering} onClick={doTrigger} disabled={!job || loadingParams}>发布</Button>
              <Button onClick={openAuth}>配置我的 Jenkins 认证</Button>
            </Space>
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
                  const ok = s.status === 'SUCCESS'; const fail = s.status === 'FAILED'; const run = s.status === 'IN_PROGRESS'
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

      <Modal open={authOpen} onCancel={() => setAuthOpen(false)} footer={null} width={560} title="我的 Jenkins 认证（用你的身份发布）" destroyOnClose>
        <div style={{ color: '#8c8c8c', fontSize: 13, marginBottom: 8 }}>
          在 Jenkins → 个人 → Configure 里生成 API Token，填到这里（加密保存）。未配置的实例发布时回退到服务账号。
        </div>
        {myTokens.length === 0 && <div style={{ color: '#999', padding: 8 }}>暂无可配置的 Jenkins 实例</div>}
        {myTokens.map((t) => (
          <div key={t.instance_id} style={{ padding: '10px 0', borderBottom: '1px solid #f0f0f0' }}>
            <div style={{ marginBottom: 6 }}><b>{t.instance_name}</b> <span style={{ color: '#8c8c8c', fontSize: 12 }}>{t.url}</span>
              {t.has_token && <Tag color="green" style={{ marginLeft: 6 }}>已配置</Tag>}
            </div>
            <Space>
              <Input placeholder="Jenkins 用户名（默认=平台用户名）" style={{ width: 180 }}
                value={authForms[t.instance_id]?.username || ''}
                onChange={(e) => setAuthForms((p) => ({ ...p, [t.instance_id]: { ...p[t.instance_id], username: e.target.value } }))} />
              <Input.Password placeholder="API Token（留空=不修改）" style={{ width: 200 }}
                value={authForms[t.instance_id]?.token || ''}
                onChange={(e) => setAuthForms((p) => ({ ...p, [t.instance_id]: { ...p[t.instance_id], token: e.target.value } }))} />
              <Button size="small" type="primary" onClick={() => saveAuth(t.instance_id)}>保存</Button>
            </Space>
          </div>
        ))}
      </Modal>
    </Modal>
  )
}
