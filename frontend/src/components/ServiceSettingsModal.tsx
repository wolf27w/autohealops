// 编辑服务 / 编辑外部访问（Service）：按 mode 切换字段显隐
// mode='service'（默认，向后兼容）：选择器(selector) / 端口(ports) / 会话保持(sessionAffinity) 等服务本身字段
// mode='external'：类型(type) / nodePort / 外部 IP(externalIPs) / 外部流量策略(externalTrafficPolicy)
// 提交仍是同一个 applyObject（创建）/ updateObjectSpec（编辑，浅合并，未提交字段保留）
import { useEffect, useState } from 'react'
import { Modal, Form, Input, Select, Button, Space, Alert, message } from 'antd'
import { MinusCircleOutlined, PlusOutlined } from '@ant-design/icons'
import { getObjectDetail, updateObjectSpec, applyObject, getWorkloads, getWorkloadServiceInfo, getClusterNamespaces } from '../services/api'
import { KeyValueList, toKvList, fromKvList } from './KeyValueEditor'
import type { ConfigObjectTarget } from './ConfigObjectYamlModal'

interface SvcPort { name?: string; port?: number; targetPort?: number | string; nodePort?: number; protocol?: string }

export default function ServiceSettingsModal({ target, mode = 'service', onClose, onSaved }: {
  target: ConfigObjectTarget | null
  mode?: 'service' | 'external'
  onClose: () => void
  onSaved?: () => void
}) {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [workloads, setWorkloads] = useState<Array<{ kind: string; name: string }>>([])
  const [wlKey, setWlKey] = useState<string>('')
  const [namespaces, setNamespaces] = useState<string[]>([])
  const [nsLoading, setNsLoading] = useState(false)
  const isCreate = !!target?.create

  useEffect(() => {
    if (!target) return
    setWlKey('')
    getWorkloads(target.clusterId, target.namespace || 'default')
      .then((d) => setWorkloads(((d || []) as Array<{ kind: string; name: string }>).filter((w) => ['deployment', 'statefulset', 'daemonset'].includes(w.kind))))
      .catch(() => setWorkloads([]))
  }, [target])

  useEffect(() => {
    if (!target) return
    const current = target.namespace || 'default'
    setNsLoading(true)
    getClusterNamespaces(target.clusterId)
      .then((d) => setNamespaces(Array.from(new Set([...(d || []), current]))))
      .catch(() => { setNamespaces([current]); message.error('命名空间列表获取失败') })
      .finally(() => setNsLoading(false))
  }, [target])

  const onPickWorkload = async (val?: string) => {
    setWlKey(val || '')
    if (!val || !target) return
    const [k, n] = val.split('/')
    try {
      const d = await getWorkloadServiceInfo(target.clusterId, k, target.namespace || 'default', n) as
        { selector?: Record<string, string>; ports?: Array<{ name?: string; port?: number; protocol?: string }> }
      form.setFieldsValue({
        selector: toKvList(d.selector || {}),
        ports: (d.ports || []).map((p) => ({ name: p.name || '', port: p.port, targetPort: p.port, nodePort: undefined, protocol: p.protocol || 'TCP' })),
      })
      message.success('已按工作负载填充选择器/端口')
    } catch {
      message.error('填充失败')
    }
  }

  useEffect(() => {
    if (!target) return
    if (target.create) {
      form.resetFields()
      form.setFieldsValue({ name: '', namespace: target.namespace || 'default', type: 'ClusterIP', selector: [{ key: '', value: '' }], ports: [{ protocol: 'TCP' }], sessionAffinity: 'None', externalIPs: [], externalTrafficPolicy: 'Cluster' })
      return
    }
    setLoading(true)
    getObjectDetail(target.clusterId, target.kind, target.namespace, target.name)
      .then((d) => {
        const spec = ((d as { spec?: Record<string, unknown> }).spec || {}) as Record<string, unknown>
        form.setFieldsValue({
          type: (spec.type as string) || 'ClusterIP',
          selector: toKvList((spec.selector as Record<string, string>) || {}),
          ports: ((spec.ports as SvcPort[]) || []).map((p) => ({
            name: p.name || '', port: p.port, targetPort: p.targetPort, nodePort: p.nodePort, protocol: p.protocol || 'TCP',
          })),
          sessionAffinity: (spec.sessionAffinity as string) || 'None',
          externalIPs: (spec.externalIPs as string[]) || [],
          externalTrafficPolicy: (spec.externalTrafficPolicy as string) || 'Cluster',
        })
      })
      .catch(() => message.error('加载失败'))
      .finally(() => setLoading(false))
  }, [target, form])

  const submit = async () => {
    if (!target) return
    const v = await form.validateFields()
    const ports = (v.ports || []).filter((p: SvcPort) => p && p.port).map((p: SvcPort) => {
      const o: Record<string, unknown> = { port: Number(p.port), protocol: p.protocol || 'TCP' }
      if (p.name) o.name = p.name
      if (p.targetPort !== undefined && p.targetPort !== null && p.targetPort !== '') {
        o.targetPort = isNaN(Number(p.targetPort)) ? p.targetPort : Number(p.targetPort)
      }
      if (p.nodePort) o.nodePort = Number(p.nodePort)
      return o
    })
    setLoading(true)
    try {
      if (isCreate) {
        await applyObject(target.clusterId, 'service', {
          apiVersion: 'v1', kind: 'Service',
          metadata: { name: (v.name || '').trim(), namespace: (v.namespace || '').trim() || 'default' },
          spec: { type: v.type, selector: fromKvList(v.selector), sessionAffinity: v.sessionAffinity || 'None', ports },
        })
        message.success('已创建')
      } else {
        const spec: Record<string, unknown> = { ports }
        if (mode === 'external') {
          spec.type = v.type
          spec.externalIPs = v.externalIPs || []
          spec.externalTrafficPolicy = v.externalTrafficPolicy || 'Cluster'
        } else {
          spec.selector = fromKvList(v.selector)
          spec.sessionAffinity = v.sessionAffinity || 'None'
        }
        await updateObjectSpec(target.clusterId, target.kind, target.namespace, target.name, spec)
        message.success('已保存')
      }
      onSaved?.()
      onClose()
    } catch {
      message.error(isCreate ? '创建失败' : '保存失败')
    } finally {
      setLoading(false)
    }
  }

  const title = isCreate
    ? '创建服务'
    : (mode === 'external' ? `编辑外部访问：${target?.name || ''}` : `编辑服务：${target?.name || ''}`)

  return (
    <Modal title={title} open={!!target} onCancel={onClose}
      onOk={submit} okText={isCreate ? '创建' : '保存'} confirmLoading={loading} width={760}>
      <Alert type="info" showIcon style={{ marginBottom: 8 }}
        message={mode === 'external'
          ? '仅合并更新 类型 / nodePort / 外部 IP / 外部流量策略，其余字段保留。'
          : '仅合并更新 选择器 / 端口 / 会话保持，其余字段（如 clusterIP）保留。'} />
      <Form form={form} layout="vertical">
        {isCreate && (
          <><Form.Item name="name" label="名称" rules={[{ required: true, message: '名称必填' }]}><Input /></Form.Item>
          <Form.Item name="namespace" label="命名空间" rules={[{ required: true }]}>
            <Select showSearch optionFilterProp="label" loading={nsLoading} placeholder="选择命名空间"
              options={namespaces.map((n) => ({ value: n, label: n }))} style={{ width: 320 }} />
          </Form.Item></>
        )}
        {/* 类型：创建或「编辑外部访问」时显示 */}
        {(isCreate || mode === 'external') && (
          <Form.Item name="type" label="类型" rules={[{ required: true }]}>
            <Select options={['ClusterIP', 'NodePort', 'LoadBalancer', 'ExternalName'].map((v) => ({ value: v, label: v }))} style={{ width: 220 }} />
          </Form.Item>
        )}
        {/* 服务本身字段：创建或「编辑服务」时显示 */}
        {(isCreate || mode === 'service') && (
          <>
            <Form.Item label="指定工作负载（自动带出选择器/端口，可选）">
              <Select value={wlKey || undefined} placeholder="选择工作负载自动填充" allowClear style={{ width: 360 }}
                onChange={onPickWorkload}
                options={workloads.map((w) => ({ value: `${w.kind}/${w.name}`, label: `${w.kind}/${w.name}` }))} />
            </Form.Item>
            <Form.Item label="选择器 Selector">
              <KeyValueList name="selector" addLabel="添加选择器" keyPlaceholder="如 app" valuePlaceholder="如 eft-auth" />
            </Form.Item>
            <Form.Item name="sessionAffinity" label="会话保持">
              <Select options={[{ value: 'None', label: 'None' }, { value: 'ClientIP', label: 'ClientIP' }]} style={{ width: 220 }} />
            </Form.Item>
          </>
        )}
        {/* 外部访问字段：仅「编辑外部访问」时显示 */}
        {mode === 'external' && !isCreate && (
          <>
            <Form.Item name="externalIPs" label="外部 IP（externalIPs）">
              <Select mode="tags" tokenSeparators={[',']} placeholder="输入外部 IP 后回车" style={{ width: 420 }} />
            </Form.Item>
            <Form.Item name="externalTrafficPolicy" label="外部流量策略">
              <Select options={[{ value: 'Cluster', label: 'Cluster' }, { value: 'Local', label: 'Local' }]} style={{ width: 220 }} />
            </Form.Item>
          </>
        )}
        <Form.Item label="端口 Ports" style={{ marginBottom: 0 }}>
          <Form.List name="ports">
            {(fields, { add, remove }) => (
              <>
                {fields.map(({ key, name: n, ...rest }) => (
                  <Space key={key} align="baseline" style={{ display: 'flex', flexWrap: 'wrap' }}>
                    <Form.Item {...rest} name={[n, 'name']}><Input placeholder="名称" style={{ width: 110 }} /></Form.Item>
                    <Form.Item {...rest} name={[n, 'port']} rules={[{ required: true, message: '端口必填' }]}><Input placeholder="port" style={{ width: 90 }} /></Form.Item>
                    <Form.Item {...rest} name={[n, 'targetPort']}><Input placeholder="targetPort" style={{ width: 110 }} /></Form.Item>
                    {/* nodePort 字段始终挂载（保留原值），仅「编辑外部访问」时可见 */}
                    <Form.Item {...rest} name={[n, 'nodePort']} style={{ display: mode === 'external' ? undefined : 'none' }}>
                      <Input placeholder="nodePort" style={{ width: 100 }} />
                    </Form.Item>
                    <Form.Item {...rest} name={[n, 'protocol']}><Select style={{ width: 90 }} options={[{ value: 'TCP' }, { value: 'UDP' }]} /></Form.Item>
                    <MinusCircleOutlined onClick={() => remove(n)} />
                  </Space>
                ))}
                <Button type="dashed" onClick={() => add({ protocol: 'TCP' })} icon={<PlusOutlined />}>添加端口</Button>
              </>
            )}
          </Form.List>
        </Form.Item>
      </Form>
    </Modal>
  )
}
