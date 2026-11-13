// 编辑设置（路由 Ingress）：IngressClass / 规则(host + path → service)
import { useEffect, useState } from 'react'
import { Modal, Form, Input, Select, Button, Space, Alert, Checkbox, message } from 'antd'
import { MinusCircleOutlined, PlusOutlined } from '@ant-design/icons'
import { getObjectDetail, updateObjectSpec, applyObject, getMiscResources, getClusterNamespaces } from '../services/api'
import type { ConfigObjectTarget } from './ConfigObjectYamlModal'

interface PathRow { host?: string; path?: string; pathType?: string; serviceName?: string; servicePort?: string }

export default function IngressSettingsModal({ target, onClose, onSaved }: {
  target: ConfigObjectTarget | null
  onClose: () => void
  onSaved?: () => void
}) {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [tlsSecrets, setTlsSecrets] = useState<string[]>([])
  const [tlsEnabled, setTlsEnabled] = useState(false)
  const [tlsSecret, setTlsSecret] = useState<string>('')
  const [namespaces, setNamespaces] = useState<string[]>([])
  const [nsLoading, setNsLoading] = useState(false)
  const isCreate = !!target?.create

  useEffect(() => {
    if (!target) return
    getMiscResources(target.clusterId, target.namespace || 'default')
      .then((d) => setTlsSecrets((((d as { secrets?: Array<{ name: string; type?: string }> }).secrets) || [])
        .filter((s) => s.type === 'kubernetes.io/tls').map((s) => s.name)))
      .catch(() => setTlsSecrets([]))
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

  useEffect(() => {
    if (!target) return
    if (target.create) {
      form.resetFields()
      setTlsEnabled(false); setTlsSecret('')
      form.setFieldsValue({ name: '', namespace: target.namespace || 'default', ingressClassName: '', rows: [{ pathType: 'Prefix', path: '/' }] })
      return
    }
    setLoading(true)
    getObjectDetail(target.clusterId, target.kind, target.namespace, target.name)
      .then((d) => {
        const spec = ((d as { spec?: Record<string, unknown> }).spec || {}) as Record<string, unknown>
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
        const tls = (spec.tls as Array<Record<string, unknown>>) || []
        if (tls.length > 0) { setTlsEnabled(true); setTlsSecret((tls[0].secretName as string) || '') } else { setTlsEnabled(false); setTlsSecret('') }
      })
      .catch(() => message.error('加载失败'))
      .finally(() => setLoading(false))
  }, [target, form])

  const submit = async () => {
    if (!target) return
    const v = await form.validateFields()
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
    const tlsHosts = Array.from(new Set(((v.rows as PathRow[]) || []).map((r) => r && r.host).filter((h): h is string => !!h)))
    const tls = tlsEnabled ? [{ hosts: tlsHosts, secretName: tlsSecret || undefined }] : []
    setLoading(true)
    try {
      if (isCreate) {
        await applyObject(target.clusterId, 'ingress', {
          apiVersion: 'networking.k8s.io/v1', kind: 'Ingress',
          metadata: { name: (v.name || '').trim(), namespace: (v.namespace || '').trim() || 'default' },
          spec: { ingressClassName: v.ingressClassName || undefined, rules, tls },
        })
        message.success('已创建')
      } else {
        await updateObjectSpec(target.clusterId, target.kind, target.namespace, target.name, {
          ingressClassName: v.ingressClassName || undefined, rules, tls,
        })
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

  return (
    <Modal title={isCreate ? '创建路由' : `编辑设置：${target?.name || ''}（路由）`} open={!!target} onCancel={onClose}
      onOk={submit} okText={isCreate ? '创建' : '保存'} confirmLoading={loading} width={860}>
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
        <Form.Item label="HTTPS 证书（TLS）">
          <Space direction="vertical" size={4} style={{ display: 'flex' }}>
            <Checkbox checked={tlsEnabled} onChange={(e) => setTlsEnabled(e.target.checked)}>启用 HTTPS（配置 TLS 证书）</Checkbox>
            {tlsEnabled && (
              <Select value={tlsSecret || undefined} placeholder="选择证书 Secret（kubernetes.io/tls）" style={{ width: 360 }}
                allowClear onChange={(v) => setTlsSecret(v || '')}
                options={tlsSecrets.map((s) => ({ value: s, label: s }))} />
            )}
          </Space>
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
    </Modal>
  )
}
