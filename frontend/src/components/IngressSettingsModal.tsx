// 编辑路由规则 / 编辑注解（Ingress）：按 mode 切换
// mode='rules'（默认，向后兼容）：ingressClassName + 规则（域名 host / 路径 path / 后端服务）
// mode='annotations'：仅注解键值编辑（标签 labels 保留原值）
// mode='tls'：仅 HTTPS/TLS 证书配置（我们追加的入口，KubeSphere 原版没有，保留功能不砍）
import { useEffect, useState } from 'react'
import { Modal, Form, Input, Select, Button, Space, Alert, message, Checkbox } from 'antd'
import { MinusCircleOutlined, PlusOutlined } from '@ant-design/icons'
import { getObjectDetail, updateObjectSpec, updateObjectMetadata, applyObject, getClusterNamespaces, getMiscResources } from '../services/api'
import { KeyValueList, toKvList, fromKvList } from './KeyValueEditor'
import type { ConfigObjectTarget } from './ConfigObjectYamlModal'

interface PathRow { host?: string; path?: string; pathType?: string; serviceName?: string; servicePort?: string }

export default function IngressSettingsModal({ target, mode = 'rules', onClose, onSaved }: {
  target: ConfigObjectTarget | null
  mode?: 'rules' | 'annotations' | 'tls'
  onClose: () => void
  onSaved?: () => void
}) {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [namespaces, setNamespaces] = useState<string[]>([])
  const [nsLoading, setNsLoading] = useState(false)
  const [labels, setLabels] = useState<Record<string, string>>({})
  // TLS（mode='tls'）：证书来自同命名空间下 type=kubernetes.io/tls 的密钥
  const [tlsSecrets, setTlsSecrets] = useState<string[]>([])
  const [tlsEnabled, setTlsEnabled] = useState(false)
  const [tlsSecret, setTlsSecret] = useState('')
  const [tlsHosts, setTlsHosts] = useState<string[]>([])
  const isCreate = !!target?.create

  useEffect(() => {
    if (!target) return
    const current = target.namespace || 'default'
    setNsLoading(true)
    getClusterNamespaces(target.clusterId)
      .then((d) => setNamespaces(Array.from(new Set([...(d || []), current]))))
      .catch(() => { setNamespaces([current]); message.error('命名空间列表获取失败') })
      .finally(() => setNsLoading(false))
  }, [target])

  useEffect(() => {
    if (!target) return
    if (target.create) {
      form.resetFields()
      form.setFieldsValue({ name: '', namespace: target.namespace || 'default', ingressClassName: '', rows: [{ pathType: 'Prefix', path: '/' }] })
      return
    }
    if (mode === 'tls') {
      getMiscResources(target.clusterId, target.namespace)
        .then((d) => {
          const secs = ((d as { secrets?: { name: string; type?: string }[] }).secrets || [])
          setTlsSecrets(secs.filter((s) => (s.type || '').includes('tls')).map((s) => s.name))
        })
        .catch(() => setTlsSecrets([]))
    }
    setLoading(true)
    getObjectDetail(target.clusterId, target.kind, target.namespace, target.name)
      .then((d) => {
        if (mode === 'annotations') {
          const t = d as { labels?: Record<string, string>; annotations?: Record<string, string> }
          setLabels(t.labels || {})
          form.setFieldsValue({ annotations: toKvList(t.annotations) })
        } else {
          const spec = ((d as { spec?: Record<string, unknown> }).spec || {}) as Record<string, unknown>
          const tl = (spec.tls as Array<Record<string, unknown>>) || []
          if (tl.length) { setTlsEnabled(true); setTlsSecret((tl[0].secretName as string) || '') } else { setTlsEnabled(false); setTlsSecret('') }
          const rows: PathRow[] = []
          for (const r of ((spec.rules as Array<Record<string, unknown>>) || [])) {
            const host = (r.host as string) || ''
            const paths = (((r.http as Record<string, unknown>) || {}).paths as Array<Record<string, unknown>>) || []
            for (const p of paths) {
              const backend = (p.backend as Record<string, unknown>) || {}
              const svc = (backend.service as Record<string, unknown>) || {}
              const port = (svc.port as Record<string, unknown>) || {}
              rows.push({
                host, path: (p.path as string) || '/', pathType: (p.pathType as string) || 'Prefix',
                serviceName: (svc.name as string) || '', servicePort: String(port.number ?? port.name ?? ''),
              })
            }
          }
          form.setFieldsValue({ ingressClassName: (spec.ingressClassName as string) || '', rows })
        }
      })
      .catch(() => message.error('加载失败'))
      .finally(() => setLoading(false))
  }, [target, form, mode])

  const submitTls = async () => {
    if (!target) return
    setLoading(true)
    try {
      const tls = tlsEnabled
        ? [{ hosts: tlsHosts.length ? tlsHosts : undefined, secretName: tlsSecret || undefined }]
        : []
      await updateObjectSpec(target.clusterId, target.kind, target.namespace, target.name, { tls })
      message.success('已保存（TLS 配置已更新）')
      onSaved?.()
      onClose()
    } catch {
      message.error('保存失败')
    } finally {
      setLoading(false)
    }
  }

  const submit = async () => {
    if (!target) return
    const v = await form.validateFields()
    setLoading(true)
    try {
      if (mode === 'annotations') {
        await updateObjectMetadata(target.clusterId, target.kind, target.namespace, target.name, labels, fromKvList(v.annotations))
        message.success('已保存')
      } else {
        const grouped: Record<string, Array<Record<string, unknown>>> = {}
        for (const r of ((v.rows as PathRow[]) || [])) {
          if (!r || !r.serviceName) continue
          const host = r.host || ''
          const portNum = Number(r.servicePort)
          const backend = { service: { name: r.serviceName, port: isNaN(portNum) ? { name: r.servicePort } : { number: portNum } } }
          grouped[host] = grouped[host] || []
          grouped[host].push({ path: r.path || '/', pathType: r.pathType || 'Prefix', backend })
        }
        const rules = Object.entries(grouped).map(([host, paths]) => ({ host, http: { paths } }))
        if (isCreate) {
          await applyObject(target.clusterId, 'ingress', {
            apiVersion: 'networking.k8s.io/v1', kind: 'Ingress',
            metadata: { name: (v.name || '').trim(), namespace: (v.namespace || '').trim() || 'default' },
            spec: { ingressClassName: v.ingressClassName || undefined, rules },
          })
          message.success('已创建')
        } else {
          await updateObjectSpec(target.clusterId, target.kind, target.namespace, target.name, {
            ingressClassName: v.ingressClassName || undefined, rules,
          })
          message.success('已保存')
        }
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
    ? '创建路由'
    : mode === 'annotations' ? `编辑注解：${target?.name || ''}`
      : mode === 'tls' ? `编辑 TLS：${target?.name || ''}`
        : `编辑路由规则：${target?.name || ''}`

  return (
    <Modal title={title} open={!!target} onCancel={onClose}
      onOk={mode === 'tls' ? submitTls : submit} okText={isCreate ? '创建' : '保存'} confirmLoading={loading} width={860}>
      {mode === 'tls' ? (
        <div>
          <Alert type="info" showIcon style={{ marginBottom: 12 }}
            message="只更新 HTTPS/TLS 配置；路由规则、注解不受影响。证书需在同命名空间下，类型为 kubernetes.io/tls 的密钥。" />
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Checkbox checked={tlsEnabled} onChange={(e) => setTlsEnabled(e.target.checked)}>启用 HTTPS（配置 TLS 证书）</Checkbox>
            <Select
              style={{ width: 360 }}
              placeholder="选择 TLS 密钥"
              value={tlsSecret || undefined}
              onChange={setTlsSecret}
              disabled={!tlsEnabled}
              options={tlsSecrets.map((n) => ({ value: n, label: n }))}
              notFoundContent="该命名空间下没有 kubernetes.io/tls 类型的密钥"
            />
            {tlsHosts.length > 0 && <div style={{ color: '#8c8c8c', fontSize: 12 }}>证书域名将沿用当前规则里的 host：{tlsHosts.join('、')}</div>}
          </Space>
        </div>
      ) : mode === 'annotations' ? (
        <Form form={form} layout="vertical">
          <Alert type="info" showIcon style={{ marginBottom: 8 }} message="只更新注解；标签(labels)保留原值。" />
          <Form.Item label="注解 Annotations" style={{ marginBottom: 0 }}>
            <KeyValueList name="annotations" addLabel="添加注解" />
          </Form.Item>
        </Form>
      ) : (
        <>
          <Alert type="info" showIcon style={{ marginBottom: 8 }} message="规则按 host 分组；一行 = 一条 path → 后端服务。" />
          <Form form={form} layout="vertical">
            {isCreate && (
              <><Form.Item name="name" label="名称" rules={[{ required: true, message: '名称必填' }]}><Input /></Form.Item>
              <Form.Item name="namespace" label="命名空间" rules={[{ required: true }]}>
                <Select showSearch optionFilterProp="label" loading={nsLoading} placeholder="选择命名空间"
                  options={namespaces.map((n) => ({ value: n, label: n }))} style={{ width: 320 }} />
              </Form.Item></>
            )}
            <Form.Item name="ingressClassName" label="IngressClass">
              <Input placeholder="如 nginx" style={{ width: 260 }} />
            </Form.Item>
            <Form.Item label="规则 Rules" style={{ marginBottom: 0 }}>
              <Form.List name="rows">
                {(fields, { add, remove }) => (
                  <>
                    {fields.map(({ key, name: n, ...rest }) => (
                      <Space key={key} align="baseline" style={{ display: 'flex', flexWrap: 'wrap' }}>
                        <Form.Item {...rest} name={[n, 'host']}><Input placeholder="host" style={{ width: 200 }} /></Form.Item>
                        <Form.Item {...rest} name={[n, 'path']}><Input placeholder="/path" style={{ width: 140 }} /></Form.Item>
                        <Form.Item {...rest} name={[n, 'pathType']}><Select style={{ width: 100 }} options={[{ value: 'Prefix' }, { value: 'Exact' }, { value: 'ImplementationSpecific' }]} /></Form.Item>
                        <Form.Item {...rest} name={[n, 'serviceName']} rules={[{ required: true, message: '服务必填' }]}><Input placeholder="服务名" style={{ width: 180 }} /></Form.Item>
                        <Form.Item {...rest} name={[n, 'servicePort']} rules={[{ required: true, message: '端口必填' }]}><Input placeholder="端口" style={{ width: 90 }} /></Form.Item>
                        <MinusCircleOutlined onClick={() => remove(n)} />
                      </Space>
                    ))}
                    <Button type="dashed" onClick={() => add({ pathType: 'Prefix', path: '/' })} icon={<PlusOutlined />}>添加规则</Button>
                  </>
                )}
              </Form.List>
            </Form.Item>
          </Form>
        </>
      )}
    </Modal>
  )
}
