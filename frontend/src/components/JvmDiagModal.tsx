// JVM 诊断弹窗（P0-R2）：对 Pod 内 Java 进程一键执行 jstack / jmap / jcmd / JFR
import { useCallback, useEffect, useState } from 'react'
import { Modal, Button, Select, Space, Input, message, Spin, Empty, Tag, Alert, Tooltip, Collapse } from 'antd'
import { ReloadOutlined, BugOutlined, CopyOutlined } from '@ant-design/icons'
import { getJvmActions, getJvmProcesses, jvmDiagnose, getPodContainers, getJvmParams, jvmAiAnalyze, jvmAiAnalyzeAll, type JvmAiAnalysis, type JvmCollectItem } from '../services/api'
import type { JvmAction, JvmProcess, JvmDiagResult, JvmParamsContainer } from '../services/api'

interface JvmPod { clusterId: number; namespace: string; name: string }

interface Props { pod: JvmPod | null; deployment?: string; kind?: string; onClose: () => void }

export default function JvmDiagModal({ pod, deployment, onClose }: Props) {
  const [containers, setContainers] = useState<string[]>([])
  const [container, setContainer] = useState('')
  const [actions, setActions] = useState<JvmAction[]>([])
  const [procs, setProcs] = useState<JvmProcess[]>([])
  const [pid, setPid] = useState<number | undefined>(undefined)
  const [loadingProcs, setLoadingProcs] = useState(false)
  const [running, setRunning] = useState('')
  const [result, setResult] = useState<JvmDiagResult | null>(null)
  const [err, setErr] = useState('')
  const [jvmNote, setJvmNote] = useState('')
  const [params, setParams] = useState<JvmParamsContainer[]>([])
  const [evidence, setEvidence] = useState<{ action: string; output: string }[]>([])
  const [aiResult, setAiResult] = useState<JvmAiAnalysis | null>(null)
  const [aiItems, setAiItems] = useState<JvmCollectItem[]>([])
  const [aiLoading, setAiLoading] = useState(false)

  useEffect(() => {
    if (!pod) return
    setContainer(''); setProcs([]); setPid(undefined); setResult(null); setErr(''); setParams([]); setJvmNote(''); setEvidence([]); setAiResult(null); setAiItems([])
    getJvmActions().then(setActions).catch(() => {})
    getPodContainers(pod.clusterId, pod.namespace, pod.name).then((c) => setContainers((c as unknown as { containers?: string[] }).containers || [])).catch(() => {})
    if (deployment) {
      getJvmParams(pod.clusterId, pod.namespace, deployment)
        .then((d) => setParams(d.containers || [])).catch(() => {})
    }
  }, [pod, deployment])

  const refreshProcs = useCallback(() => {
    if (!pod) return
    setLoadingProcs(true); setErr('')
    getJvmProcesses(pod.clusterId, pod.namespace, pod.name, container)
      .then((d) => {
        const list = (d.processes || []).filter((p) => !p.is_jcmd)
        setProcs(list)
        setJvmNote(d.hint || '')
        if (!pid && list.length) setPid(list[0].pid)
      })
      .catch((e) => setErr(String(e?.message || e)))
      .finally(() => setLoadingProcs(false))
  }, [pod, container, pid])

  useEffect(() => { if (pod) refreshProcs() }, [pod, container]) // eslint-disable-line react-hooks/exhaustive-deps

  const run = (a: JvmAction) => {
    if (!pod) return
    setRunning(a.action); setErr(''); setResult(null)
    jvmDiagnose(pod.clusterId, pod.namespace, pod.name, { action: a.action, container, pid })
      .then((d) => {
        setResult(d)
        const out = d?.output || (d?.file_path ? `产物文件：${d.file_path}` : '')
        if (out) setEvidence((prev) => [...prev, { action: a.label || a.action, output: out }])
      })
      .catch((e) => setErr(String(e?.message || e)))
      .finally(() => setRunning(''))
  }

  // 一键：自动采集全部轻量动作（jstack/jmap/jcmd/jattach 文本类）→ 发 AI 综合诊断
  const runAiAll = () => {
    if (!pod) return
    setAiLoading(true); setAiResult(null); setAiItems([])
    jvmAiAnalyzeAll(pod.clusterId, pod.namespace, pod.name, { container, pid })
      .then((d) => { setAiItems(d?.items || []); setAiResult(d?.analysis || null) })
      .catch((e) => setErr(String(e?.message || e)))
      .finally(() => setAiLoading(false))
  }

  // 仅用「已手动采集的证据」发 AI 综合诊断
  const runAi = () => {
    if (!pod || evidence.length === 0) return
    setAiLoading(true); setAiResult(null)
    const text = evidence.map((e) => `### ${e.action}\n${e.output}`).join('\n\n')
    jvmAiAnalyze(pod.clusterId, pod.namespace, pod.name, { evidence: text, container })
      .then(setAiResult).catch(() => setAiResult(null)).finally(() => setAiLoading(false))
  }

  if (!pod) return null

  return (
    <Modal open title={<Space><BugOutlined />JVM 诊断：{pod.name}</Space>}
      footer={null} onCancel={onClose} width={860} destroyOnClose>
      {params.length > 0 && (
        <Collapse size="small" style={{ marginBottom: 12 }} items={params.map((c) => ({
          key: c.container,
          label: <Space>JVM 启动参数 · <b>{c.container}</b><Tag color={c.params.xmx ? 'blue' : 'default'}>{c.params.xmx ? `-Xmx${c.params.xmx}` : '未设置 -Xmx'}</Tag></Space>,
          children: (
            <div style={{ fontSize: 12, fontFamily: 'monospace', lineHeight: 1.9 }}>
              <div>-Xms: {c.params.xms || '-'}　-Xmx: {c.params.xmx || '-'}　-Xss: {c.params.xss || '-'}　MaxMetaspace: {c.params.max_metaspace || '-'}</div>
              {c.params.others.length > 0 && <div style={{ color: '#888' }}>其它: {c.params.others.join(' ')}</div>}
              <div style={{ color: '#aaa' }}>image: {c.image}</div>
            </div>
          ),
        }))} />
      )}
      <div style={{ marginBottom: 12 }}>
        <Space size="small" wrap>
          <span style={{ color: '#888', fontSize: 12 }}>JVM 资源趋势/监控见 Pod「监控」（现有 Prometheus 容器指标，无需 JMX Exporter）</span>
        </Space>
      </div>
      <Space style={{ marginBottom: 12 }} wrap>
        <span>容器：</span>
        <Select style={{ width: 200 }} allowClear placeholder="默认容器"
          value={container || undefined} onChange={(v) => setContainer(v || '')}
          options={containers.map((c) => ({ value: c, label: c }))} />
        <span>Java 进程：</span>
        <Select style={{ width: 240 }} allowClear placeholder="自动定位" loading={loadingProcs}
          value={pid} onChange={setPid}
          options={procs.map((p) => ({ value: p.pid, label: `${p.pid}　${p.target}` }))} />
        <Button size="small" icon={<ReloadOutlined />} onClick={refreshProcs} loading={loadingProcs}>刷新进程</Button>
      </Space>

      {err && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={err}
        description="容器无 JDK 工具（jstack/jmap/jcmd）时，平台会自动注入 jattach 以 JRE 兼容方式执行；若仍失败请确认容器有外网或改用基于 JDK 的镜像。" />}
      {!err && jvmNote && <Alert type="info" showIcon style={{ marginBottom: 12 }} message={jvmNote} />}

      <div style={{ marginBottom: 12 }}>
        <Space wrap size={[8, 8]}>
          {actions.map((a) => (
            <Tooltip key={a.action} title={a.desc}>
              <Button size="small" loading={running === a.action} onClick={() => run(a)}>
                {a.label}{a.file ? <Tag color="orange" style={{ marginLeft: 6 }}>产物</Tag> : null}
              </Button>
            </Tooltip>
          ))}
        </Space>
      </div>

      {running && <Spin tip="诊断执行中…（jmap/jfr 可能较慢）" style={{ display: 'block', margin: '12px 0' }}><div style={{ height: 40 }} /></Spin>}

      <Space style={{ marginBottom: 8 }} wrap>
        <Button size="small" type="primary" loading={aiLoading} disabled={!pod} onClick={runAiAll}>
          一键采集全部 + AI 综合诊断
        </Button>
        {evidence.length > 0 && (
          <Button size="small" loading={aiLoading} onClick={runAi}>发 AI（仅用已采集 {evidence.length} 项）</Button>
        )}
        {evidence.length > 0 && <Button size="small" type="link" onClick={() => { setEvidence([]); setAiResult(null) }}>清空证据</Button>}
      </Space>
      <div style={{ color: '#999', fontSize: 12, marginBottom: 12 }}>
        一键自动采集 thread_dump / heap_histo / heap_info / gc_class_histo / vm_flags / sys_props 后再发 AI；
        重操作（heap dump / JFR）需手动单独执行。
      </div>

      {aiItems.length > 0 && (
        <Collapse size="small" style={{ marginBottom: 12 }}
          items={aiItems.map((it) => ({
            key: it.action,
            label: <span>{it.label} {it.ok ? <Tag color="green">OK</Tag> : <Tag color="red">失败</Tag>}</span>,
            children: (
              <Input.TextArea readOnly value={it.ok ? (it.output || '(无输出)') : (it.message || '(采集失败)')}
                autoSize={{ minRows: 4, maxRows: 18 }} style={{ fontFamily: 'monospace', fontSize: 12 }} />
            ),
          }))} />
      )}

      {aiResult && (
        <div style={{ border: '1px solid #d9f7be', background: '#fcfffb', borderRadius: 6, padding: 10, marginBottom: 12 }}>
          <Space size={6} wrap style={{ marginBottom: 6 }}>
            <Tag color="green">AI 诊断</Tag>
            {aiResult.risk_level && <Tag color={aiResult.risk_level === 'critical' ? 'red' : aiResult.risk_level === 'high' ? 'orange' : 'blue'}>风险：{aiResult.risk_level}</Tag>}
            {typeof aiResult.confidence === 'number' && <Tag>置信度：{(aiResult.confidence * 100).toFixed(0)}%</Tag>}
            {aiResult.model_used && <span style={{ color: '#999', fontSize: 12 }}>{aiResult.model_used}</span>}
          </Space>
          <div style={{ fontSize: 13, lineHeight: 1.8 }}>
            <div><b>根因：</b>{aiResult.root_cause || '-'}</div>
            {(aiResult.suggestions || []).length > 0 && (
              <div style={{ marginTop: 4 }}><b>建议：</b>
                <ol style={{ margin: '4px 0 0 18px', padding: 0 }}>
                  {(aiResult.suggestions || []).map((s, i) => <li key={i}>{s}</li>)}
                </ol>
              </div>
            )}
          </div>
        </div>
      )}

      {result && (
        <div>
          <Space style={{ marginBottom: 6 }} wrap>
            <Tag color="blue">{result.command}</Tag>
            {result.pid && <Tag>pid={result.pid}</Tag>}
            {result.file_path && (
              <>
                <Tag color="orange">产物：{result.file_path}</Tag>
                <Button size="small" icon={<CopyOutlined />}
                  onClick={() => { navigator.clipboard?.writeText(result.file_path || ''); message.success('路径已复制') }}>
                  复制路径
                </Button>
              </>
            )}
          </Space>
          {result.hint && <div style={{ color: '#999', fontSize: 12, marginBottom: 6 }}>{result.hint}</div>}
          <Input.TextArea readOnly value={result.output || '(无输出)'} autoSize={{ minRows: 8, maxRows: 22 }}
            style={{ fontFamily: 'monospace', fontSize: 12 }} />
        </div>
      )}

      {!result && !running && !err && (
        <Empty description="选择一个诊断动作开始（线程栈 / 堆信息 / 参数 / 转储）" />
      )}
    </Modal>
  )
}
