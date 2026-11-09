// Blue Ocean 式构建视图：阶段卡片 + 点阶段看该段日志 + 全部控制台日志
import { useEffect, useRef, useState } from 'react'
import { Space, Tag, Spin, Button } from 'antd'
import { getReleaseStages, getReleaseStageLog, getReleaseBuildStatus, getReleaseBuildLog } from '../services/api'
import type { ReleaseStage } from '../services/api'

interface Props {
  buildId: number
  jobName?: string
  buildNumber?: number
  live?: boolean          // true = 轮询刷新（刚触发）
}

const stColor = (s: string) => {
  if (s === 'SUCCESS') return 'green'
  if (s === 'FAILED' || s === 'FAILURE') return 'red'
  if (s === 'IN_PROGRESS') return 'blue'
  if (s === 'ABORTED') return 'orange'
  return 'default'
}
const stText = (s: string) => {
  const m: Record<string, string> = { SUCCESS: '成功', FAILED: '失败', FAILURE: '失败', IN_PROGRESS: '进行中', NOT_EXECUTED: '未执行', ABORTED: '已中止', PAUSED: '暂停' }
  return m[s] || s || '-'
}

export default function PipelineView({ buildId, jobName, buildNumber, live }: Props) {
  const [stages, setStages] = useState<ReleaseStage[]>([])
  const [loading, setLoading] = useState(true)
  const [status, setStatus] = useState('')
  const [selStage, setSelStage] = useState<ReleaseStage | null>(null)
  const [stageLog, setStageLog] = useState('')
  const [loadingLog, setLoadingLog] = useState(false)
  const [fullLog, setFullLog] = useState('')
  const [showFull, setShowFull] = useState(false)
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)

  const loadStages = () => {
    getReleaseStages(buildId).then((d) => setStages(d || [])).catch(() => {}).finally(() => setLoading(false))
  }
  const loadStatus = () => {
    getReleaseBuildStatus(buildId).then((s) => setStatus(s.status)).catch(() => {})
  }

  useEffect(() => {
    setStages([]); setLoading(true); setStatus(''); setSelStage(null); setStageLog(''); setFullLog(''); setShowFull(false)
    loadStatus(); loadStages()
    if (live) {
      timer.current = setInterval(() => { loadStatus(); loadStages() }, 3000)
    }
    return () => { if (timer.current) clearInterval(timer.current) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buildId, live])

  const openStage = (st: ReleaseStage) => {
    setSelStage(st); setShowFull(false); setLoadingLog(true); setStageLog('')
    getReleaseStageLog(buildId, st.id).then((d) => setStageLog(d?.text || '（该阶段无日志）')).catch(() => setStageLog('取日志失败')).finally(() => setLoadingLog(false))
  }

  const openFull = () => {
    setShowFull(true); setSelStage(null); setStageLog('')
    getReleaseBuildLog(buildId, 0).then((d) => setFullLog(d?.text || '')).catch(() => setFullLog('取日志失败'))
  }

  return (
    <div>
      <Space wrap style={{ marginBottom: 12 }}>
        <Tag color={stColor(status)}>{stText(status)}</Tag>
        {jobName && <span style={{ color: '#8c8c8c', fontSize: 13 }}>{jobName} #{buildNumber}</span>}
        <Button size="small" onClick={openFull}>全部控制台日志</Button>
      </Space>

      {loading ? <Spin size="small" /> : stages.length === 0 ? (
        <div style={{ color: '#999', padding: 8 }}>暂无阶段信息（Freestyle job 只有整体日志，点「全部控制台日志」看）</div>
      ) : (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
          {stages.map((st) => (
            <div key={st.id} onClick={() => openStage(st)}
              style={{
                cursor: 'pointer', minWidth: 120, padding: '8px 12px', borderRadius: 8,
                border: `1px solid ${selStage?.id === st.id ? '#1677ff' : '#e8e8e8'}`,
                background: selStage?.id === st.id ? '#e6f4ff' : '#fff',
              }}>
              <div style={{ fontWeight: 600, fontSize: 13 }}>{st.name}</div>
              <Tag color={stColor(st.status)} style={{ marginTop: 4 }}>{stText(st.status)}</Tag>
              {st.duration_ms ? <span style={{ fontSize: 11, color: '#8c8c8c' }}>{(st.duration_ms / 1000).toFixed(1)}s</span> : null}
            </div>
          ))}
        </div>
      )}

      {(selStage || showFull) && (
        <div>
          <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 4 }}>
            {showFull ? '全部控制台日志' : `阶段日志：${selStage?.name}`}
          </div>
          {loadingLog ? <Spin size="small" /> : (
            <pre style={{ maxHeight: 320, overflow: 'auto', background: '#0b0b0b', color: '#d9d9d9', padding: 12, borderRadius: 6, fontSize: 12, margin: 0 }}>
              {(showFull ? fullLog : stageLog) || '（空）'}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}
