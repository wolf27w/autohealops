// 编辑设置：ConfigMap→键值；Secret→按类型（镜像服务/证书/普通键值）
import { useEffect, useState } from 'react'
import { Modal, Form, Input, Select, Alert, message } from 'antd'
import { getObjectDetail, updateObjectData, applyObject } from '../services/api'
import { KeyValueList, toKvList, fromKvList } from './KeyValueEditor'
import type { ConfigObjectTarget } from './ConfigObjectYamlModal'

const SECRET_TYPES = [
  { value: 'Opaque', label: 'Opaque（普通键值）' },
  { value: 'kubernetes.io/dockerconfigjson', label: '镜像服务（dockerconfigjson）' },
  { value: 'kubernetes.io/tls', label: 'TLS（证书 + 私钥）' },
  { value: 'kubernetes.io/basic-auth', label: '用户名密码（basic-auth）' },
  { value: 'kubernetes.io/ssh-auth', label: 'SSH（ssh-auth）' },
]

interface Detail {
  data?: Record<string, string>
  type?: string
}

export default function ConfigObjectDataModal({ target, onClose, onSaved }: {
  target: ConfigObjectTarget | null
  onClose: () => void
  onSaved?: () => void
}) {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [secType, setSecType] = useState('Opaque')
  const isCreate = !!target?.create

  useEffect(() => {
    if (!target) return
    if (target.create) {
      form.resetFields()
      setSecType('Opaque')
      form.setFieldsValue({ name: '', namespace: target.namespace || 'default', data: [{ key: '', value: '' }] })
      return
    }
    setLoading(true)
    getObjectDetail(target.clusterId, target.kind, target.namespace, target.name)
      .then((d) => {
        const t = d as Detail
        setFormValues(t)
      })
      .catch(() => message.error('加载失败'))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target])

  const setFormValues = (t: Detail) => {
    if (target?.kind === 'configmap') {
      form.setFieldsValue({ data: toKvList(t.data) })
      return
    }
    const data = t.data || {}
    const type = t.type || 'Opaque'
    setSecType(type)
    if (type === 'kubernetes.io/dockerconfigjson') {
      let server = '', username = '', password = '', email = ''
      try {
        const j = JSON.parse(data['.dockerconfigjson'] || '{}')
        const s = Object.keys(j.auths || {})[0] || ''
        const a = (j.auths || {})[s] || {}
        server = s; username = a.username || ''; password = a.password || ''; email = a.email || ''
      } catch { /* ignore */ }
      form.setFieldsValue({ server, username, password, email })
    } else if (type === 'kubernetes.io/tls') {
      form.setFieldsValue({ crt: data['tls.crt'] || '', key: data['tls.key'] || '' })
    } else if (type === 'kubernetes.io/basic-auth') {
      form.setFieldsValue({ username: data['username'] || '', password: data['password'] || '' })
    } else if (type === 'kubernetes.io/ssh-auth') {
      form.setFieldsValue({ 'ssh-privatekey': data['ssh-privatekey'] || '' })
    } else {
      form.setFieldsValue({ data: toKvList(data) })
    }
  }

  const onTypeChange = (v: string) => {
    setSecType(v)
    form.resetFields()
  }

  const buildSecretData = (v: Record<string, any>): Record<string, string> => {
    if (secType === 'kubernetes.io/dockerconfigjson') {
      const cfg = { auths: { [v.server || '']: { username: v.username || '', password: v.password || '', email: v.email || '', auth: btoa(`${v.username || ''}:${v.password || ''}`) } } }
      return { '.dockerconfigjson': JSON.stringify(cfg) }
    }
    if (secType === 'kubernetes.io/tls') {
      return { 'tls.crt': v.crt || '', 'tls.key': v.key || '' }
    }
    if (secType === 'kubernetes.io/basic-auth') {
      return { username: v.username || '', password: v.password || '' }
    }
    if (secType === 'kubernetes.io/ssh-auth') {
      return { 'ssh-privatekey': v['ssh-privatekey'] || '' }
    }
    return fromKvList(v.data)
  }

  const submit = async () => {
    if (!target) return
    const v = await form.validateFields()
    setLoading(true)
    try {
      const data = target.kind === 'configmap' ? fromKvList(v.data) : buildSecretData(v)
      if (isCreate) {
        const metadata = { name: (v.name || '').trim(), namespace: (v.namespace || '').trim() || 'default' }
        const object = target.kind === 'configmap'
          ? { apiVersion: 'v1', kind: 'ConfigMap', metadata, data }
          : { apiVersion: 'v1', kind: 'Secret', metadata, type: secType, stringData: data }
        await applyObject(target.clusterId, target.kind, object)
        message.success('已创建')
      } else {
        await updateObjectData(target.clusterId, target.kind, target.namespace, target.name, data,
          target.kind === 'secret' ? secType : undefined)
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

  const isSecret = target?.kind === 'secret'
  const label = isSecret ? '保密字典' : '配置字典'
  return (
    <Modal title={`${isCreate ? '创建' : '编辑设置'}：${isCreate ? label : (target?.name || '')}${isCreate ? '' : `（${label}）`}`} open={!!target} onCancel={onClose}
      onOk={submit} okText={isCreate ? '创建' : '保存'} confirmLoading={loading} width={760}>
      <Form form={form} layout="vertical">
        {isCreate && (
          <><Form.Item name="name" label="名称" rules={[{ required: true, message: '名称必填' }]}><Input /></Form.Item>
          <Form.Item name="namespace" label="命名空间" rules={[{ required: true }]}><Input /></Form.Item></>
        )}
        {!isSecret && (
          <Form.Item label="数据（key → value）" style={{ marginBottom: 0 }}>
            <KeyValueList name="data" addLabel="添加数据项" textarea />
          </Form.Item>
        )}

        {isSecret && (
          <Form.Item label="类型">
            <Select value={secType} onChange={onTypeChange} options={SECRET_TYPES} style={{ width: 360 }} />
          </Form.Item>
        )}

        {isSecret && secType === 'kubernetes.io/dockerconfigjson' && (
          <>
            <Alert type="info" showIcon style={{ marginBottom: 8 }} message="镜像服务：填仓库地址 + 用户名/密码，自动生成 dockerconfigjson。" />
            <Form.Item name="server" label="镜像仓库地址" rules={[{ required: true }]}><Input placeholder="https://harbor.example.cn/" /></Form.Item>
            <Form.Item name="username" label="用户名" rules={[{ required: true }]}><Input /></Form.Item>
            <Form.Item name="password" label="密码" rules={[{ required: true }]}><Input.Password /></Form.Item>
            <Form.Item name="email" label="邮箱（可选）"><Input /></Form.Item>
          </>
        )}

        {isSecret && secType === 'kubernetes.io/tls' && (
          <>
            <Alert type="info" showIcon style={{ marginBottom: 8 }} message="TLS：粘贴证书与私钥（PEM 文本）。" />
            <Form.Item name="crt" label="证书 tls.crt" rules={[{ required: true }]}>
              <Input.TextArea rows={8} style={{ fontFamily: 'monospace', fontSize: 12 }} placeholder="-----BEGIN CERTIFICATE-----" />
            </Form.Item>
            <Form.Item name="key" label="私钥 tls.key" rules={[{ required: true }]} style={{ marginBottom: 0 }}>
              <Input.TextArea rows={8} style={{ fontFamily: 'monospace', fontSize: 12 }} placeholder="-----BEGIN PRIVATE KEY-----" />
            </Form.Item>
          </>
        )}

        {isSecret && secType === 'kubernetes.io/basic-auth' && (
          <>
            <Form.Item name="username" label="用户名" rules={[{ required: true }]}><Input /></Form.Item>
            <Form.Item name="password" label="密码" rules={[{ required: true }]}><Input.Password /></Form.Item>
          </>
        )}

        {isSecret && secType === 'kubernetes.io/ssh-auth' && (
          <Form.Item name="ssh-privatekey" label="SSH 私钥" rules={[{ required: true }]} style={{ marginBottom: 0 }}>
            <Input.TextArea rows={8} style={{ fontFamily: 'monospace', fontSize: 12 }} />
          </Form.Item>
        )}

        {isSecret && !['kubernetes.io/dockerconfigjson', 'kubernetes.io/tls', 'kubernetes.io/basic-auth', 'kubernetes.io/ssh-auth'].includes(secType) && (
          <Form.Item label="数据（key → value）" style={{ marginBottom: 0 }}>
            <KeyValueList name="data" addLabel="添加数据项" textarea />
          </Form.Item>
        )}
      </Form>
    </Modal>
  )
}
