// JVM 诊断弹窗（R2）：诊断 Tab（jstack/jfr/heapdump/jcmd）+ 参数 Tab（-Xms/-Xmx/-Xss/-XX:MaxMetaspaceSize + java_version）
import { useEffect, useState } from 'react'
import { Modal, Tabs, Select, Input, Button, Space, Alert, Spin, Empty, Tag, message } from 'antd'
import { BugOutlined, CopyOutlined } from '@ant-design/icons'
import { jvmDiagnose, jvmParams, getPodContainers } from '../services/api'
import type { JvmDiagResult } from '../services/api'

interface JvmPod { clusterId: number; namespace: string; name: string }
interface Props { pod: JvmPod | null; deployment?: string; onClose: () => void }

const ACTIONS = [
  { value: 'jstack', label: 'jstack 线程栈', file: false },
  { value: 'jfr', label: 'jfr 飞行记录', file: true },
  { value: 'heapdump', label: 'heapdump 堆转储', file: true },
  { value: 'jcmd', label: 'jcmd 命令', file: false },
]

const ARGS_HINT: Record<string, string> = {
  jstack: 'pid（如 1）',
  jfr: 'pid [时长]（如 1 或 1 15s）',
  heapdump: 'pid（如 1）',
  jcmd: 'pid 子命令（如 1 VM.system_properties）',
}

export default function JvmDiagModal({ pod, onClose }: Props) {
  const [containers, setContainers] = useState<string[]>([])
  const [container, setContainer] = useState('')
  const [action, setAction] = useState('jstack')
  const [args, setArgs] = useState('')
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<JvmDiagResult | null>(null)
  const [err, setErr] = useState('')

  const [paramText, setParamText] = useState('')
  const [paramErr, setParamErr] = useState('')
  const [paramLoading, setParamLoading] = useState(false)

  useEffect(() => {
    if (!pod) return
    setContainer(''); setAction('jstack'); setArgs(''); setResult(null); setErr('')
    setParamText(''); setParamErr('')
    getPodContainers(pod.clusterId, pod.namespace, pod.name)
      .then((c) => setContainers((c as unknown as { containers?: string[] }).containers || []))
      .catch(() => setContainers([]))
  }, [pod])

  const runDiagnose = () => {
    if (!pod) return
    setRunning(true); setErr(''); setResult(null)
    jvmDiagnose(pod.clusterId, pod.namespace, pod.name, { action, container, args })
      .then((d) => setResult(d))
      .catch((e) => setErr(String(e?.message || e)))
      .finally(() => setRunning(false))
  }

  const loadParams = () => {
    if (!pod) return
    setParamLoading(true); setParamErr(''); setParamText('')
    jvmParams(pod.clusterId, pod.namespace, pod.name, container)
      .then((d) => setParamText(d.params || ''))
      .catch((e) => setParamErr(String(e?.message || e)))
      .finally(() => setParamLoading(false))
  }

  if (!pod) return null

  const isFileAction = action === 'jfr' || action === 'heapdump'

  const diagTab = (
    <div>
      <Space style={{ marginBottom: 12 }} wrap>
        <span>动作：</span>
        <Select style={{ width: 180 }} value={action} onChange={(v) => setAction(v as string)}
          options={ACTIONS.map((a) => ({ value: a.value, label: a.label }))} />
        <span>args：</span>
        <Input style={{ width: 280 }} placeholder={ARGS_HINT[action]} value={args}
          onChange={(e) => setArgs(e.target.value)} onPressEnter={runDiagnose} />
        <Button type="primary" loading={running} onClick={runDiagnose}>执行</Button>
      </Space>
      <div style={{ color: '#999', fontSize: 12, marginBottom: 12 }}>
        {ARGS_HINT[action]}；jfr/heapdump 产物落盘 executor 本地，返回 file_path
      </div>

      {running && <Spin tip="诊断执行中…（jfr/heapdump 较慢）" style={{ display: 'block', margin: '12px 0' }}><div style={{ height: 40 }} /></Spin>}
      {err && <Alert type="error" showIcon style={{ marginBottom: 12 }} message={err} />}

      {result && (
        <div>
          {isFileAction ? (
            <div>
              <Space style={{ marginBottom: 6 }} wrap>
                <Tag color="orange">产物路径</Tag>
                <Button size="small" icon={<CopyOutlined />}
                  onClick={() => { navigator.clipboard?.writeText(result.file_path || ''); message.success('路径已复制') }}>
                  复制路径
                </Button>
              </Space>
              <Input.TextArea readOnly value={result.file_path || '(无产物路径)'} autoSize={{ minRows: 2, maxRows: 6 }}
                style={{ fontFamily: 'monospace', fontSize: 12 }} />
            </div>
          ) : (
            <Input.TextArea readOnly value={result.output || '(无输出)'} autoSize={{ minRows: 8, maxRows: 22 }}
              style={{ fontFamily: 'monospace', fontSize: 12 }} />
          )}
        </div>
      )}

      {!result && !running && !err && <Empty description="选择动作与 args 后点击「执行」（jstack 优先）" />}
    </div>
  )

  const paramsTab = (
    <div>
      <Space style={{ marginBottom: 12 }} wrap>
        <Button type="primary" loading={paramLoading} onClick={loadParams}>采集启动参数</Button>
        <span style={{ color: '#999', fontSize: 12 }}>容器：{container || '默认（第一个）'}</span>
      </Space>
      {paramErr && <Alert type="error" showIcon style={{ marginBottom: 12 }} message={paramErr} />}
      {paramText ? (
        <Input.TextArea readOnly value={paramText} autoSize={{ minRows: 6, maxRows: 16 }}
          style={{ fontFamily: 'monospace', fontSize: 12 }} />
      ) : !paramLoading && !paramErr ? (
        <Empty description="点击「采集启动参数」获取 -Xms/-Xmx/-Xss/-XX:MaxMetaspaceSize 与 java_version" />
      ) : null}
    </div>
  )

  return (
    <Modal open title={<Space><BugOutlined />JVM 诊断：{pod.name}</Space>}
      footer={null} onCancel={onClose} width={760} destroyOnClose>
      <Space style={{ marginBottom: 12 }} wrap>
        <span>容器：</span>
        <Select style={{ width: 220 }} allowClear placeholder="默认容器（第一个）"
          value={container || undefined} onChange={(v) => setContainer(v ? String(v) : '')}
          options={containers.map((c) => ({ value: c, label: c }))} />
      </Space>
      <Tabs items={[
        { key: 'diag', label: '诊断', children: diagTab },
        { key: 'params', label: '参数', children: paramsTab },
      ]} />
    </Modal>
  )
}
