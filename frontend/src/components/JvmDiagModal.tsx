// JVM 诊断弹窗（P0-R2）：对 Pod 内 Java 进程一键执行 jstack / jmap / jcmd / JFR
import { useCallback, useEffect, useState } from 'react'
import { Modal, Button, Select, Space, Input, message, Spin, Empty, Tag, Alert, Tooltip, Collapse } from 'antd'
import { ReloadOutlined, BugOutlined, CopyOutlined } from '@ant-design/icons'
import { getJvmActions, getJvmProcesses, jvmDiagnose, getPodContainers, getJvmParams } from '../services/api'
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
  const [params, setParams] = useState<JvmParamsContainer[]>([])

  useEffect(() => {
    if (!pod) return
    setContainer(''); setProcs([]); setPid(undefined); setResult(null); setErr(''); setParams([])
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
      .then((d) => setResult(d))
      .catch((e) => setErr(String(e?.message || e)))
      .finally(() => setRunning(''))
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
        description="若容器未安装 JDK 工具（jstack/jmap/jcmd），请确认镜像基于 JDK 而非 JRE。" />}

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
