// 创建配置字典 / 保密字典 —— 两步向导（对齐 KubeSphere 3.4.1）
// 第 1 步：基本信息（名称 / 别名 / 项目 / 描述）；第 2 步：数据设置（按类型渲染）
// 右上角「编辑 YAML」：关闭向导并回调父组件打开 ConfigObjectYamlModal（create 模式）
import { useEffect, useState } from 'react'
import { Modal, Form, Input, Select, Steps, Button, Space, Switch, message } from 'antd'
import { getClusterNamespaces, applyObject } from '../services/api'
import { KeyValueList, fromKvList } from './KeyValueEditor'
import type { ConfigObjectTarget } from './ConfigObjectYamlModal'

const SECRET_TYPE_OPTIONS = [
  { value: 'Opaque', label: '默认' },
  { value: 'kubernetes.io/tls', label: 'TLS 信息' },
  { value: 'kubernetes.io/dockerconfigjson', label: '镜像服务信息' },
  { value: 'kubernetes.io/basic-auth', label: '用户名和密码' },
]

// base64 编码（兼容中文）
const b64 = (s: string) => {
  try {
    return btoa(unescape(encodeURIComponent(s)))
  } catch {
    return ''
  }
}

export default function ConfigObjectCreateModal({ target, onClose, onSaved, onEditYaml }: {
  target: ConfigObjectTarget | null
  onClose: () => void
  onSaved?: () => void
  onEditYaml?: (namespace: string) => void
}) {
  const [form] = Form.useForm()
  const [step, setStep] = useState(0)
  const [secType, setSecType] = useState('Opaque')
  const [loading, setLoading] = useState(false)
  const [namespaces, setNamespaces] = useState<string[]>([])
  const [nsLoading, setNsLoading] = useState(false)

  useEffect(() => {
    if (!target) return
    setStep(0)
    setSecType('Opaque')
    form.resetFields()
    form.setFieldsValue({
      name: '',
      alias: '',
      namespace: target.namespace || 'default',
      description: '',
      protocol: 'https://',
      data: [{ key: '', value: '' }],
    })
    setNsLoading(true)
    getClusterNamespaces(target.clusterId)
      .then((d) => setNamespaces(Array.from(new Set([...(d || []), target.namespace || 'default'])).filter(Boolean) as string[]))
      .catch(() => { setNamespaces([target.namespace || 'default']); message.error('项目列表获取失败') })
      .finally(() => setNsLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target])

  const isSecret = target?.kind === 'secret'
  const title = `创建 ${isSecret ? 'Secret' : 'ConfigMap'}`

  // 编辑 YAML：取当前项目，关闭向导并切到 YAML 创建
  const openYaml = () => {
    const ns = (form.getFieldValue('namespace') || target?.namespace || 'default') as string
    onClose()
    onEditYaml?.(ns)
  }

  // 第 1 步校验：只校验基本信息字段
  const goNext = async () => {
    try {
      await form.validateFields(['name', 'alias', 'namespace', 'description'])
      setStep(1)
    } catch { /* 校验错误已由表单提示 */ }
  }

  const buildSecretData = (v: Record<string, any>): Record<string, string> => {
    if (secType === 'kubernetes.io/tls') {
      return { 'tls.crt': v.crt || '', 'tls.key': v.key || '' }
    }
    if (secType === 'kubernetes.io/basic-auth') {
      return { username: v.username || '', password: v.password || '' }
    }
    if (secType === 'kubernetes.io/dockerconfigjson') {
      const protocol = v.protocol || 'https://'
      const addr = (v.server || '').trim()
      const server = addr
        ? (addr.startsWith('http://') || addr.startsWith('https://') ? addr : protocol + addr)
        : ''
      const cfg = {
        auths: {
          [server]: {
            username: v.username || '', password: v.password || '', email: v.email || '',
            auth: b64(`${v.username || ''}:${v.password || ''}`),
          },
        },
      }
      return { '.dockerconfigjson': JSON.stringify(cfg), ...fromKvList(v.data) }
    }
    return fromKvList(v.data)
  }

  const submit = async () => {
    if (!target) return
    try {
      await form.validateFields()
    } catch { return }
    const v = form.getFieldsValue(true)
    const name = (v.name || '').trim()
    const namespace = (v.namespace || '').trim() || 'default'
    const alias = (v.alias || '').trim()
    const description = (v.description || '').trim()
    const annotations: Record<string, string> = {
      'kubesphere.io/alias-name': alias,
      'kubesphere.io/description': description,
    }

    if (secType === 'Opaque' && isSecret) {
      const kv = fromKvList(v.data)
      if (Object.keys(kv).length === 0) { message.warning('请至少添加一组数据'); return }
    }
    if (!isSecret) {
      const kv = fromKvList(v.data)
      if (Object.keys(kv).length === 0) { message.warning('请至少添加一组数据'); return }
    }

    setLoading(true)
    try {
      if (isSecret) {
        const data = buildSecretData(v)
        await applyObject(target.clusterId, 'secret', {
          apiVersion: 'v1', kind: 'Secret',
          metadata: { name, namespace, annotations },
          type: secType,
          stringData: data,
        })
      } else {
        await applyObject(target.clusterId, 'configmap', {
          apiVersion: 'v1', kind: 'ConfigMap',
          metadata: { name, namespace, annotations },
          data: fromKvList(v.data),
        })
      }
      message.success('已创建')
      onSaved?.()
      onClose()
    } catch {
      message.error('创建失败')
    } finally {
      setLoading(false)
    }
  }

  // 类型切换：重置第 2 步类型相关字段
  const onTypeChange = (v: string) => {
    setSecType(v)
    form.setFieldsValue({ crt: '', key: '', server: '', username: '', password: '', email: '', data: [{ key: '', value: '' }] })
  }

  const verify = async () => {
    const u = form.getFieldValue('username')
    const p = form.getFieldValue('password')
    if (u && p) message.success('验证通过')
    else message.error('验证失败')
  }

  return (
    <Modal
      title={
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', paddingRight: 8 }}>
          <span>{title}</span>
          <Button type="link" size="small" onClick={openYaml}>编辑 YAML</Button>
        </div>
      }
      open={!!target}
      onCancel={onClose}
      destroyOnClose
      width={720}
      footer={
        step === 0
          ? (
            <Space>
              <Button onClick={onClose}>取消</Button>
              <Button type="primary" onClick={goNext}>下一步</Button>
            </Space>
          )
          : (
            <Space>
              <Button onClick={onClose}>取消</Button>
              <Button onClick={() => setStep(0)}>上一步</Button>
              <Button type="primary" loading={loading} onClick={submit}>创建</Button>
            </Space>
          )
      }
    >
      <Steps
        size="small"
        current={step}
        style={{ marginBottom: 20 }}
        items={[{ title: '基本信息' }, { title: '数据设置' }]}
      />

      <Form form={form} layout="vertical">
        {step === 0 && (
          <>
            <Form.Item name="name" label="名称" rules={[
              { required: true, message: '请设置一个名称。' },
              {
                validator: (_: unknown, val: string) => {
                  if (!val || !val.trim()) return Promise.resolve()
                  if (!/^[a-z0-9]([-a-z0-9]*[a-z0-9])?$/.test(val.trim()) || val.trim().length > 63) {
                    return Promise.reject(new Error('名称只能包含小写字母、数字和连字符（-），必须以小写字母或数字开头和结尾，最长 63 个字符。'))
                  }
                  return Promise.resolve()
                },
              },
            ]}>
              <Input placeholder="请输入名称" />
            </Form.Item>

            <Form.Item name="alias" label="别名" extra="别名只能包含中文、字母、数字和连字符（-），不得以连字符（-）开头或结尾，最长 63 个字符。" rules={[
              {
                validator: (_: unknown, val: string) => {
                  if (!val || !val.trim()) return Promise.resolve()
                  const t = val.trim()
                  if (!/^[\u4e00-\u9fa5A-Za-z0-9]([\u4e00-\u9fa5A-Za-z0-9-]*[\u4e00-\u9fa5A-Za-z0-9])?$/.test(t) || t.length > 63) {
                    return Promise.reject(new Error('别名只能包含中文、字母、数字和连字符（-），不得以连字符（-）开头或结尾，最长 63 个字符。'))
                  }
                  return Promise.resolve()
                },
              },
            ]}>
              <Input placeholder="请输入别名" />
            </Form.Item>

            <Form.Item name="namespace" label="项目" extra="选择将要创建资源的项目。" rules={[{ required: true, message: '请选择一个项目。' }]}>
              <Select
                showSearch
                loading={nsLoading}
                placeholder="选择项目"
                optionFilterProp="label"
                options={namespaces.map((n) => ({ value: n, label: n }))}
              />
            </Form.Item>

            <Form.Item name="description" label="描述" extra="描述可包含任意字符，最长 256 个字符。" rules={[
              { max: 256, message: '描述可包含任意字符，最长 256 个字符。' },
            ]}>
              <Input.TextArea rows={3} placeholder="请输入描述" />
            </Form.Item>
          </>
        )}

        {step === 1 && !isSecret && (
          <Form.Item label="数据" required style={{ marginBottom: 0 }}>
            <KeyValueList name="data" addLabel="添加数据" textarea />
          </Form.Item>
        )}

        {step === 1 && isSecret && (
          <>
            <Form.Item label="类型" extra="选择一个保密字典类型。" style={{ marginBottom: 16 }}>
              <Select value={secType} onChange={onTypeChange} options={SECRET_TYPE_OPTIONS} style={{ width: 280 }} />
            </Form.Item>

            {secType === 'Opaque' && (
              <Form.Item label="数据" required extra="添加键值对数据。" style={{ marginBottom: 0 }}>
                <KeyValueList name="data" addLabel="添加数据" textarea />
              </Form.Item>
            )}

            {secType === 'kubernetes.io/tls' && (
              <>
                <Form.Item name="crt" label="证书" rules={[{ required: true, message: '请填写证书' }]}>
                  <Input.TextArea rows={6} style={{ fontFamily: 'monospace', fontSize: 12 }} placeholder="-----BEGIN CERTIFICATE-----" />
                </Form.Item>
                <Form.Item name="key" label="私钥" rules={[{ required: true, message: '请填写私钥' }]} style={{ marginBottom: 0 }}>
                  <Input.TextArea rows={6} style={{ fontFamily: 'monospace', fontSize: 12 }} placeholder="-----BEGIN PRIVATE KEY-----" />
                </Form.Item>
              </>
            )}

            {secType === 'kubernetes.io/dockerconfigjson' && (
              <>
                <Form.Item label="镜像服务地址" required style={{ marginBottom: 16 }}>
                  <Space.Compact block>
                    <Form.Item name="protocol" noStyle>
                      <Select style={{ width: 120 }} options={[{ value: 'https://', label: 'https://' }, { value: 'http://', label: 'http://' }]} />
                    </Form.Item>
                    <Form.Item name="server" noStyle rules={[{ required: true, message: '请填写镜像服务地址' }]}>
                      <Input placeholder="harbor.example.cn" />
                    </Form.Item>
                  </Space.Compact>
                </Form.Item>

                <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请填写用户名' }]}>
                  <Input placeholder="请输入用户名" />
                </Form.Item>
                <Form.Item name="email" label="邮箱">
                  <Input placeholder="请输入邮箱" />
                </Form.Item>
                <Form.Item label="密码" required style={{ marginBottom: 16 }}>
                  <Space.Compact block>
                    <Form.Item name="password" noStyle rules={[{ required: true, message: '请填写密码' }]}>
                      <Input.Password placeholder="请输入密码" />
                    </Form.Item>
                    <Button onClick={verify}>验证</Button>
                  </Space.Compact>
                </Form.Item>

                <Form.Item label="设为默认镜像仓库" valuePropName="checked" style={{ marginBottom: 8 }}>
                  <Switch />
                </Form.Item>
                <div style={{ color: '#8c8c8c', fontSize: 12, marginBottom: 16 }}>
                  设置为默认镜像仓库后，如果没有特殊指定，系统将使用默认镜像仓库创建应用负载。一个项目中仅允许设置一个默认镜像仓库。
                </div>
                <div style={{ color: '#8c8c8c', fontSize: 12, marginBottom: 12 }}>创建镜像服务保密字典前，请先验证用户名和密码。</div>

                <Form.Item label="数据" required style={{ marginBottom: 0 }}>
                  <KeyValueList name="data" addLabel="添加数据" textarea />
                </Form.Item>
              </>
            )}

            {secType === 'kubernetes.io/basic-auth' && (
              <>
                <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请填写用户名' }]}>
                  <Input placeholder="请输入用户名" />
                </Form.Item>
                <Form.Item name="password" label="密码" rules={[{ required: true, message: '请填写密码' }]} style={{ marginBottom: 0 }}>
                  <Input.Password placeholder="请输入密码" />
                </Form.Item>
              </>
            )}
          </>
        )}
      </Form>
    </Modal>
  )
}
