// 创建工作负载（无状态/有状态/守护集/定时任务）：**页签表单**（基本信息 / 容器与资源 / 存储挂载 / 高级设置）
// 参考 KubeSphere：所有分区并列在同一个弹窗里，不用一步步点「下一步」；创建与编辑共用同一套表单。
// 编辑模式：target.create=false + target.name + target.initialValues 回填现有值
import { useEffect, useState } from 'react'
import { Modal, Form, Input, InputNumber, Switch, Select, Tabs, Divider, Button, Space, message, Alert } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { applyWorkload, getClusterNamespaces, getMiscResources } from '../services/api'
import { KeyValueList, type KvItem } from './KeyValueEditor'
import { buildYaml, mergeWorkloadYaml, type WorkloadValues, type WorkloadContainerValue, type VolumeType } from './workloadYaml'

export interface WorkloadCreateTarget {
  clusterId: number
  kind: string        // deployment/statefulset/daemonset/cronjob
  namespace: string
  name?: string       // 编辑模式：已存在的工作负载名
  create?: boolean    // 默认 true（创建）；false = 编辑
  initialValues?: Partial<WorkloadValues>
  existingYaml?: string // 编辑模式：现有完整 YAML（用于字段级 merge，避免丢弃高级字段）
}

const KIND_LABEL: Record<string, string> = {
  deployment: '无状态（Deployment）',
  statefulset: '有状态（StatefulSet）',
  daemonset: '守护集（DaemonSet）',
  cronjob: '定时任务（CronJob）',
}

const HAS_REPLICAS = (kind: string) => kind === 'deployment' || kind === 'statefulset'

const VOLUME_TYPES: { value: VolumeType; label: string }[] = [
  { value: 'pvc', label: '持久卷声明(PVC)' },
  { value: 'configMap', label: '配置字典' },
  { value: 'secret', label: '密钥' },
  { value: 'emptyDir', label: '临时卷' },
]

const IMAGE_PULL_POLICIES = [
  { value: 'IfNotPresent', label: 'IfNotPresent（本地优先）' },
  { value: 'Always', label: 'Always（总是拉取）' },
  { value: 'Never', label: 'Never（仅本地）' },
]

export default function WorkloadCreateModal({ target, onClose, onSaved }: {
  target: WorkloadCreateTarget | null
  onClose: () => void
  onSaved?: () => void
}) {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [namespaces, setNamespaces] = useState<string[]>([])
  const [nsLoading, setNsLoading] = useState(false)
  const [pullSecretOptions, setPullSecretOptions] = useState<string[]>([])
  const kind = target?.kind || ''
  const isCron = kind === 'cronjob'
  const isCreate = target ? target.create !== false : false
  const livenessEnabled = Form.useWatch('livenessEnabled', form)
  const readinessEnabled = Form.useWatch('readinessEnabled', form)
  const strategyType = Form.useWatch('strategyType', form)


  useEffect(() => {
    if (!target) return
    form.resetFields()
    const defaults = {
      name: target.name || '',
      namespace: target.namespace || 'default',
      replicas: 1,
      image: kind === 'cronjob' ? 'busybox:latest' : 'nginx:latest',
      imagePullPolicy: 'IfNotPresent',
      schedule: '*/5 * * * *',
      command: 'echo hello',
      env: [] as KvItem[],
      labels: [] as KvItem[],
      volumes: [] as WorkloadValues['volumes'],
      livenessEnabled: false,
      livenessPath: '/healthz',
      readinessEnabled: false,
      readinessPath: '/healthz',
      // 更新策略（与集群默认一致）
      strategyType: 'RollingUpdate',
      maxSurge: '25%',
      maxUnavailable: '25%',
      podManagementPolicy: 'OrderedReady',
      updateStrategyType: 'RollingUpdate',
      imagePullSecrets: [] as string[],
      containers: [] as WorkloadContainerValue[],
    }
    const init = isCreate ? {} : (target.initialValues || {})
    // 附加容器命令：yamlToValues 里是数组，表单里存逗号分隔字符串，回填时归一化
    const formContainers = (init.containers || []).map((c) => ({
      ...c,
      command: Array.isArray(c.command) ? c.command.join(', ') : (c.command || ''),
    }))
    form.setFieldsValue({ ...defaults, ...init, containers: formContainers })
  }, [target, kind, form, isCreate])

  useEffect(() => {
    if (!target) return
    const current = target.namespace || 'default'
    setNsLoading(true)
    getClusterNamespaces(target.clusterId)
      .then((d) => setNamespaces(Array.from(new Set([...(d || []), current]))))
      .catch(() => { setNamespaces([current]); message.error('命名空间列表获取失败') })
      .finally(() => setNsLoading(false))
  }, [target])

  // 镜像拉取密钥选项：只列 dockerconfigjson 类型的 Secret（拿不到时留空即可）
  useEffect(() => {
    if (!target) return
    const ns = target.namespace || 'default'
    getMiscResources(target.clusterId, ns)
      .then((d) => {
        const secrets = ((d as { secrets?: { name?: string; type?: string }[] }).secrets || [])
          .filter((s) => s.type === 'kubernetes.io/dockerconfigjson')
          .map((s) => s.name || '')
          .filter(Boolean)
        setPullSecretOptions(secrets)
      })
      .catch(() => setPullSecretOptions([]))
  }, [target])

  const submit = async () => {
    if (!target) return
    const v = await form.validateFields()
    // 存储挂载：非 emptyDir 必须有来源
    const vols = (v.volumes || []) as WorkloadValues['volumes']
    for (const m of vols || []) {
      if (m && m.type && m.type !== 'emptyDir' && !m.source) {
        message.error('请填写每个卷的来源名称（ConfigMap/Secret/PVC）')
        return
      }
      if (m && m.type && m.type !== 'emptyDir' && !m.mountPath) {
        message.error('请填写每个卷的挂载路径')
        return
      }
    }
    // 附加容器：命令在表单里是逗号分隔字符串 → 转成数组
    const containers: WorkloadContainerValue[] = (v.containers || [])
      .filter((c: WorkloadContainerValue & { command?: string | string[] }) => c && c.name && c.image)
      .map((c: WorkloadContainerValue & { command?: string | string[] }) => ({
        ...c,
        command: typeof c.command === 'string' && c.command.trim()
          ? c.command.split(',').map((s: string) => s.trim()).filter(Boolean)
          : undefined,
      }))
    const finalV: WorkloadValues = { ...v, containers }
    setLoading(true)
    try {
      await applyWorkload(target.clusterId, {
        kind: target.kind,
        namespace: (v.namespace || '').trim(),
        name: isCreate ? '' : (target.name || ''),
        yaml: isCreate ? buildYaml(target.kind, finalV) : mergeWorkloadYaml(target.existingYaml || '', finalV, target.kind),
      })
      message.success(isCreate ? '已创建' : '已保存（正在滚动更新 Pod）')
      onSaved?.()
      onClose()
    } catch (e) {
      const msg = (e as { response?: { data?: { message?: string } } })?.response?.data?.message
      message.error(msg || (isCreate ? '创建失败' : '保存失败'))
    } finally {
      setLoading(false)
    }
  }

  const cronNote = (
    <Alert type="info" showIcon style={{ marginBottom: 8 }}
      message="定时任务（CronJob）不涉及资源配额 / 健康检查 / 存储挂载，此步可直接「下一步」。" />
  )

  const step1 = (
    <>
      <Form.Item name="name" label="名称" rules={[{ required: true, message: '名称必填' }]}>
        <Input placeholder="如 nginx-demo" disabled={!isCreate} />
      </Form.Item>
      <Form.Item name="namespace" label="命名空间" rules={[{ required: true, message: '命名空间必填' }]}>
        <Select showSearch optionFilterProp="label" loading={nsLoading} placeholder="选择命名空间" disabled={!isCreate}
          options={namespaces.map((n) => ({ value: n, label: n }))} style={{ width: 320 }} />
      </Form.Item>
      {HAS_REPLICAS(kind) && (
        <Form.Item name="replicas" label="副本数" rules={[{ required: true }]}>
          <InputNumber min={0} style={{ width: 160 }} />
        </Form.Item>
      )}
      {isCron && (
        <Form.Item name="schedule" label="定时表达式（Cron）" rules={[{ required: true }]} tooltip="分 时 日 月 周；如 */5 * * * * = 每 5 分钟">
          <Input placeholder="*/5 * * * *" />
        </Form.Item>
      )}
      <Form.Item name="image" label="镜像" rules={[{ required: true, message: '镜像必填' }]}>
        <Input placeholder="nginx:latest" />
      </Form.Item>
      {!isCron && (
        <Form.Item name="containerPort" label="容器端口">
          <InputNumber min={1} max={65535} placeholder="80" style={{ width: 160 }} />
        </Form.Item>
      )}
      {isCron && (
        <Form.Item name="command" label="执行命令" tooltip="以 /bin/sh -c 执行">
          <Input placeholder="echo hello" />
        </Form.Item>
      )}
    </>
  )

  const step2 = isCron ? cronNote : (
    <>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <Form.Item name="cpuRequest" label="CPU 请求" style={{ flex: 1, minWidth: 120 }}><Input placeholder="如 100m" /></Form.Item>
        <Form.Item name="cpuLimit" label="CPU 上限" style={{ flex: 1, minWidth: 120 }}><Input placeholder="如 500m" /></Form.Item>
        <Form.Item name="memoryRequest" label="内存请求" style={{ flex: 1, minWidth: 120 }}><Input placeholder="如 256Mi" /></Form.Item>
        <Form.Item name="memoryLimit" label="内存上限" style={{ flex: 1, minWidth: 120 }}><Input placeholder="如 512Mi" /></Form.Item>
      </div>
      <Divider style={{ margin: '8px 0 12px' }} />
      <Form.Item name="livenessEnabled" label="存活探针 livenessProbe" valuePropName="checked"><Switch /></Form.Item>
      {livenessEnabled && (
        <div style={{ display: 'flex', gap: 12 }}>
          <Form.Item name="livenessPath" label="路径" style={{ flex: 2 }}><Input placeholder="/healthz" /></Form.Item>
          <Form.Item name="livenessPort" label="端口" style={{ flex: 1 }}><InputNumber min={1} max={65535} placeholder="80" style={{ width: '100%' }} /></Form.Item>
        </div>
      )}
      <Form.Item name="readinessEnabled" label="就绪探针 readinessProbe" valuePropName="checked"><Switch /></Form.Item>
      {readinessEnabled && (
        <div style={{ display: 'flex', gap: 12 }}>
          <Form.Item name="readinessPath" label="路径" style={{ flex: 2 }}><Input placeholder="/healthz" /></Form.Item>
          <Form.Item name="readinessPort" label="端口" style={{ flex: 1 }}><InputNumber min={1} max={65535} placeholder="80" style={{ width: '100%' }} /></Form.Item>
        </div>
      )}
      <Divider style={{ margin: '8px 0 12px' }} />
      <div style={{ fontWeight: 500, marginBottom: 8 }}>多容器（附加容器）</div>
      <Form.List name="containers">
        {(fields, { add, remove }) => (
          <>
            {fields.map(({ key, name, ...rest }) => (
              <div key={key} style={{ border: '1px solid #f0f0f0', borderRadius: 6, padding: 12, marginBottom: 12, position: 'relative' }}>
                <Button type="link" danger size="small" style={{ position: 'absolute', top: 2, right: 4 }} onClick={() => remove(name)}>移除</Button>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 16px' }}>
                  <Form.Item {...rest} name={[name, 'name']} label="容器名称" rules={[{ required: true, message: '容器名称必填' }]}>
                    <Input placeholder="如 sidecar" />
                  </Form.Item>
                  <Form.Item {...rest} name={[name, 'image']} label="镜像" rules={[{ required: true, message: '镜像必填' }]}>
                    <Input placeholder="nginx:latest" />
                  </Form.Item>
                  <Form.Item {...rest} name={[name, 'containerPort']} label="端口">
                    <InputNumber min={1} max={65535} style={{ width: '100%' }} />
                  </Form.Item>
                  <Form.Item {...rest} name={[name, 'command']} label="启动命令（可选，逗号分隔）" tooltip="覆盖镜像默认启动命令，如：/bin/sh,-c,echo hi">
                    <Input placeholder="/bin/sh,-c,echo hi" />
                  </Form.Item>
                  <Form.Item {...rest} name={[name, 'cpuRequest']} label="CPU 请求"><Input placeholder="100m" /></Form.Item>
                  <Form.Item {...rest} name={[name, 'cpuLimit']} label="CPU 上限"><Input placeholder="500m" /></Form.Item>
                  <Form.Item {...rest} name={[name, 'memoryRequest']} label="内存请求"><Input placeholder="256Mi" /></Form.Item>
                  <Form.Item {...rest} name={[name, 'memoryLimit']} label="内存上限"><Input placeholder="512Mi" /></Form.Item>
                </div>
              </div>
            ))}
            <Button type="dashed" block icon={<PlusOutlined />} onClick={() => add({ name: '', image: '' })}>添加容器</Button>
          </>
        )}
      </Form.List>
    </>
  )

  const step3 = isCron ? cronNote : (
    <Form.List name="volumes">
      {(fields, { add, remove }) => (
        <>
          {fields.map(({ key, name, ...rest }) => (
            <div key={key} style={{ border: '1px solid #f0f0f0', borderRadius: 6, padding: 12, marginBottom: 12, position: 'relative' }}>
              <Button type="link" danger size="small" style={{ position: 'absolute', top: 2, right: 4 }} onClick={() => remove(name)}>移除</Button>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 16px' }}>
                <Form.Item {...rest} name={[name, 'type']} label="类型" rules={[{ required: true, message: '请选择类型' }]}>
                  <Select options={VOLUME_TYPES} placeholder="选择卷类型" />
                </Form.Item>
                <Form.Item {...rest} name={[name, 'name']} label="卷名" tooltip="留空自动生成 vol-N（挂载路径与卷按此名关联）">
                  <Input placeholder="如 data（留空自动）" />
                </Form.Item>
                <Form.Item {...rest} name={[name, 'source']} label="来源名称" tooltip="ConfigMap/Secret/PVC 的名称；临时卷（emptyDir）留空">
                  <Input placeholder="如 my-config / my-pvc" />
                </Form.Item>
                <Form.Item {...rest} name={[name, 'mountPath']} label="挂载路径（容器内）" rules={[{ required: true, message: '请填写容器内路径' }, { pattern: /^\//, message: '必须是绝对路径（以 / 开头）' }]}>
                  <Input placeholder="/data" />
                </Form.Item>
                <Form.Item {...rest} name={[name, 'subPath']} label="文件名 / 子路径（可选）" tooltip="挂载单个文件时填来源里的 key/文件名；留空整体挂载为目录">
                  <Input placeholder="如 application.yml" />
                </Form.Item>
                <Form.Item {...rest} name={[name, 'readOnly']} label="只读" valuePropName="checked">
                  <Switch />
                </Form.Item>
              </div>
            </div>
          ))}
          <Button type="dashed" block icon={<PlusOutlined />} onClick={() => add({ type: 'pvc', readOnly: false })}>添加卷</Button>
        </>
      )}
    </Form.List>
  )

  const step4 = (
    <>
      <Form.Item name="imagePullPolicy" label="镜像拉取策略 imagePullPolicy">
        <Select options={IMAGE_PULL_POLICIES} style={{ width: 320 }} />
      </Form.Item>
      {kind === 'deployment' && (
        <>
          <Divider style={{ margin: '8px 0 12px' }} />
          <div style={{ fontWeight: 500, marginBottom: 8 }}>更新策略</div>
          <Form.Item name="strategyType" label="更新策略类型">
            <Select options={[{ value: 'RollingUpdate', label: '滚动更新 RollingUpdate' }, { value: 'Recreate', label: '重建 Recreate' }]} style={{ width: 320 }} />
          </Form.Item>
          {strategyType !== 'Recreate' && (
            <div style={{ display: 'flex', gap: 12 }}>
              <Form.Item name="maxSurge" label="最大超出副本数 maxSurge" style={{ flex: 1 }}><Input placeholder="25%" /></Form.Item>
              <Form.Item name="maxUnavailable" label="最大不可用 maxUnavailable" style={{ flex: 1 }}><Input placeholder="25%" /></Form.Item>
            </div>
          )}
        </>
      )}
      {kind === 'statefulset' && (
        <>
          <Divider style={{ margin: '8px 0 12px' }} />
          <div style={{ fontWeight: 500, marginBottom: 8 }}>更新策略</div>
          <Form.Item name="podManagementPolicy" label="Pod 管理策略">
            <Select options={[{ value: 'OrderedReady', label: '顺序就绪 OrderedReady' }, { value: 'Parallel', label: '并行 Parallel' }]} style={{ width: 320 }} />
          </Form.Item>
          <Form.Item name="updateStrategyType" label="更新策略类型">
            <Select options={[{ value: 'RollingUpdate', label: '滚动更新 RollingUpdate' }, { value: 'OnDelete', label: '删除后重建 OnDelete' }]} style={{ width: 320 }} />
          </Form.Item>
        </>
      )}
      {!isCron && (
        <>
          <Divider style={{ margin: '8px 0 12px' }} />
          <Form.Item name="imagePullSecrets" label="镜像拉取密钥 imagePullSecrets" tooltip="仅列出 dockerconfigjson 类型的密钥；留空则不指定">
            <Select mode="multiple" allowClear showSearch optionFilterProp="label" placeholder="选择镜像拉取密钥"
              options={pullSecretOptions.map((n) => ({ value: n, label: n }))} style={{ width: 480 }} />
          </Form.Item>
        </>
      )}
      <Divider style={{ margin: '8px 0 12px' }} />
      <Form.Item label="环境变量">
        <KeyValueList name="env" addLabel="添加环境变量" keyPlaceholder="变量名" valuePlaceholder="变量值" />
      </Form.Item>
      <Form.Item label="标签">
        <KeyValueList name="labels" addLabel="添加标签" keyPlaceholder="标签 key" valuePlaceholder="标签 value" />
      </Form.Item>
    </>
  )

  const body = (
    <Form form={form} layout="vertical">
      <Tabs
        items={[
          { key: 'basic', label: '基本信息', children: step1 },
          { key: 'container', label: '容器与资源', children: step2 },
          { key: 'storage', label: '存储挂载', children: step3 },
          { key: 'advanced', label: '高级设置', children: step4 },
        ]}
      />
    </Form>
  )

  return (
    <Modal
      title={`${isCreate ? '创建' : '编辑设置'}工作负载：${KIND_LABEL[kind] || kind}${isCreate ? '' : `（${target?.namespace}/${target?.name}）`}`}
      open={!!target}
      onCancel={onClose}
      footer={null}
      width={880}
      destroyOnClose
    >
      {body}
      <Divider style={{ margin: '8px 0 16px' }} />
      <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
        <Button onClick={onClose}>取消</Button>
        <Button type="primary" loading={loading} onClick={submit}>{isCreate ? '创建' : '保存'}</Button>
      </Space>
    </Modal>
  )
}
