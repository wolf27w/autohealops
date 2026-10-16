// 工作负载「配置」面板（KubeSphere 风格）：
//   存储     —— 统一的卷列表（配置字典/密钥/PVC/临时卷），每卷显示挂载点（容器 → 路径），可添加/移除
//   环境变量 —— 已配置的 env + envFrom 列表，可注入/移除（配置字典/密钥）
// props.onlySection 传 'storage' | 'env' 时只渲染单块（用于拆成两个页签），不传则内部用 Tabs
import { useCallback, useEffect, useState } from 'react'
import { Button, Space, Table, Tag, Modal, Form, Select, Input, Switch, Popconfirm, message, Empty, Spin, Typography, Alert, Tabs, Radio } from 'antd'
import { PlusOutlined, ReloadOutlined, DeleteOutlined } from '@ant-design/icons'
import { getWorkloadMounts, addWorkloadMount, removeWorkloadMount, getMiscResources, type AddMountPayload } from '../services/api'

type MountType = 'configMap' | 'secret' | 'pvc' | 'emptyDir'

interface VolumeMountInfo { name: string; mount_path: string; sub_path?: string; read_only?: boolean }
interface VolumeInfo { name: string; type: string; source: string; read_only?: boolean }
interface ContainerMounts { name: string; mounts: VolumeMountInfo[] }
interface EnvRef { container: string; source_type: string; source_name: string; prefix?: string; optional?: boolean }
interface EnvVar { container: string; name: string; value?: string; value_from?: string; is_init?: boolean }
interface WorkloadMounts { volumes: VolumeInfo[]; containers: ContainerMounts[]; init_containers: ContainerMounts[]; env_refs?: EnvRef[]; envs?: EnvVar[] }
interface MiscRow { namespace: string; name: string; [key: string]: unknown }

const TYPE_LABEL: Record<string, string> = {
  configMap: '配置字典', secret: '密钥', pvc: '持久卷声明(PVC)', emptyDir: '临时卷',
  persistentVolumeClaim: '持久卷声明(PVC)', hostPath: 'hostPath', projected: 'projected', nfs: 'NFS', csi: 'CSI',
}
const TYPE_COLOR: Record<string, string> = {
  configMap: 'blue', secret: 'purple', persistentVolumeClaim: 'green', emptyDir: 'default',
}
// 添加卷的类型选项（KubeSphere 风格）
const ADD_TYPES: { value: MountType; label: string }[] = [
  { value: 'pvc', label: '持久卷声明(PVC)' },
  { value: 'configMap', label: '配置字典' },
  { value: 'secret', label: '密钥' },
  { value: 'emptyDir', label: '临时卷' },
]
const typeLabel = (t: string) => TYPE_LABEL[t] || t || '-'

export default function WorkloadMountsPanel({ clusterId, kind, namespace, name, onlySection }: {
  clusterId: number; kind: string; namespace: string; name: string; onlySection?: 'storage' | 'env'
}) {
  const [data, setData] = useState<WorkloadMounts | null>(null)
  const [loading, setLoading] = useState(false)
  const [addOpen, setAddOpen] = useState(false)
  const [addEnvOpen, setAddEnvOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [misc, setMisc] = useState<{ configmaps: MiscRow[]; secrets: MiscRow[]; pvcs: MiscRow[] }>({ configmaps: [], secrets: [], pvcs: [] })
  const [form] = Form.useForm()
  const [envForm] = Form.useForm()

  const load = useCallback(() => {
    setLoading(true)
    getWorkloadMounts(clusterId, kind, namespace, name)
      .then((d) => setData(d as WorkloadMounts))
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [clusterId, kind, namespace, name])

  useEffect(load, [load])
  useEffect(() => {
    getMiscResources(clusterId, namespace)
      .then((d) => {
        const m = d as { configmaps?: MiscRow[]; secrets?: MiscRow[]; pvcs?: MiscRow[] }
        setMisc({ configmaps: m.configmaps || [], secrets: m.secrets || [], pvcs: m.pvcs || [] })
      })
      .catch(() => setMisc({ configmaps: [], secrets: [], pvcs: [] }))
  }, [clusterId, namespace])

  const allContainers = [...(data?.containers || []), ...(data?.init_containers || [])]
  const containerNames = (data?.containers || []).map((c) => c.name)

  // 某卷被哪些容器挂载到哪些路径（挂载点）
  const mountsOfVolume = (volName: string) => {
    const rows: { container: string; mount_path: string; sub_path?: string; read_only?: boolean }[] = []
    for (const c of allContainers) {
      for (const m of c.mounts || []) {
        if (m.name === volName) rows.push({ container: c.name, mount_path: m.mount_path, sub_path: m.sub_path, read_only: m.read_only })
      }
    }
    return rows
  }

  const optionsFor = (t?: MountType) => {
    const list = t === 'configMap' ? misc.configmaps : t === 'secret' ? misc.secrets : t === 'pvc' ? misc.pvcs : []
    return list.map((x) => ({ value: x.name, label: x.name }))
  }

  const openAdd = () => { setAddOpen(true); form.resetFields(); form.setFieldsValue({ type: 'pvc', read_only: false, containers: [] }) }
  const openAddEnv = () => { setAddEnvOpen(true); envForm.resetFields(); envForm.setFieldsValue({ env_type: 'configMap', containers: [] }) }

  const submitAdd = async () => {
    const v = await form.validateFields()
    setSubmitting(true)
    try {
      const payload: AddMountPayload = {
        mode: 'volume',
        source_type: v.type,
        source_name: v.type === 'emptyDir' ? '' : v.source_name,
        mount_path: v.mount_path,
        sub_path: v.sub_path || undefined,
        read_only: !!v.read_only,
        volume_name: v.volume_name || undefined,
        containers: v.containers || [],
      }
      await addWorkloadMount(clusterId, kind, namespace, name, payload)
      message.success('已挂载，正在滚动更新 Pod')
      setAddOpen(false)
      load()
    } catch { /* 拦截器已提示 */ } finally { setSubmitting(false) }
  }

  const submitAddEnv = async () => {
    const v = await envForm.validateFields()
    setSubmitting(true)
    try {
      const payload: AddMountPayload = {
        mode: 'env', source_type: v.env_type, source_name: v.source_name, containers: v.containers || [],
      }
      await addWorkloadMount(clusterId, kind, namespace, name, payload)
      message.success('已注入环境变量，正在滚动更新 Pod')
      setAddEnvOpen(false)
      load()
    } catch { /* 拦截器已提示 */ } finally { setSubmitting(false) }
  }

  const doRemove = async (volumeName: string) => {
    try {
      await removeWorkloadMount(clusterId, kind, namespace, name, { mode: 'volume', volume_name: volumeName })
      message.success('已移除，正在滚动更新 Pod')
      load()
    } catch { /* 拦截器已提示 */ }
  }
  const doRemoveEnv = async (sourceName: string, container: string) => {
    try {
      await removeWorkloadMount(clusterId, kind, namespace, name, { mode: 'env', source_name: sourceName, container })
      message.success('已移除环境变量注入，正在滚动更新 Pod')
      load()
    } catch { /* 拦截器已提示 */ }
  }

  // ---------------- 存储 ----------------
  const storagePane = (
    <div>
      <Space style={{ marginBottom: 10 }}>
        <Button size="small" type="primary" icon={<PlusOutlined />} onClick={openAdd}>添加卷</Button>
        <Button size="small" icon={<ReloadOutlined />} onClick={load}>刷新</Button>
      </Space>
      <Table rowKey="name" size="small" pagination={false} dataSource={data?.volumes || []}
        columns={[
          { title: '卷名', dataIndex: 'name', width: 180, render: (v: string) => <span style={{ fontWeight: 600 }}>{v}</span> },
          { title: '类型', dataIndex: 'type', width: 150, render: (v: string) => <Tag color={TYPE_COLOR[v] || 'default'}>{typeLabel(v)}</Tag> },
          { title: '来源', dataIndex: 'source', width: 200, render: (v: string) => v || '-' },
          { title: '挂载点（容器 → 路径）', key: 'mounts', render: (_: unknown, r: VolumeInfo) => {
            const rows = mountsOfVolume(r.name)
            if (rows.length === 0) return <Typography.Text type="secondary">未挂载到容器</Typography.Text>
            return (
              <Space direction="vertical" size={2}>
                {rows.map((m) => (
                  <span key={`${m.container}-${m.mount_path}-${m.sub_path || ''}`} style={{ fontSize: 12 }}>
                    <Tag>{m.container}</Tag>{m.mount_path}{m.sub_path ? <span style={{ color: '#8c8c8c' }}>（文件: {m.sub_path}）</span> : null}{m.read_only ? <Tag color="orange">只读</Tag> : null}
                  </span>
                ))}
              </Space>
            )
          } },
          { title: '操作', width: 90, render: (_: unknown, r: VolumeInfo) => (
            <Popconfirm title={`移除卷 ${r.name} 的全部挂载？`} description="会触发 Pod 滚动更新" okButtonProps={{ danger: true }} onConfirm={() => doRemove(r.name)}>
              <Button size="small" type="link" danger icon={<DeleteOutlined />}>移除</Button>
            </Popconfirm>
          ) },
        ]}
        locale={{ emptyText: '暂无卷' }} />
    </div>
  )

  // ---------------- 环境变量 ----------------
  const envPane = (
    <div>
      <Space style={{ marginBottom: 10 }}>
        <Button size="small" type="primary" icon={<PlusOutlined />} onClick={openAddEnv}>注入环境变量</Button>
        <Button size="small" icon={<ReloadOutlined />} onClick={load}>刷新</Button>
      </Space>
      <Typography.Text strong>环境变量（env）</Typography.Text>
      <Table style={{ marginTop: 6 }} rowKey={(r) => `${r.container}-${r.name}`} size="small" pagination={false} dataSource={data?.envs || []}
        columns={[
          { title: '容器', dataIndex: 'container', width: 200, render: (v: string, r: EnvVar) => <Space size={4}>{r.is_init && <Tag color="purple">init</Tag>}{v}</Space> },
          { title: '变量名', dataIndex: 'name', width: 240 },
          { title: '值', dataIndex: 'value', render: (v: string) => (v ? <Typography.Text code>{v}</Typography.Text> : '-') },
          { title: '来源（valueFrom）', dataIndex: 'value_from', render: (v: string) => v || '-' },
        ]}
        locale={{ emptyText: '暂无单独环境变量' }} />

      <Typography.Text strong style={{ display: 'inline-block', margin: '16px 0 6px' }}>环境变量注入（envFrom，整份配置字典/密钥）</Typography.Text>
      <Table rowKey={(r) => `${r.container}-${r.source_name}`} size="small" pagination={false} dataSource={data?.env_refs || []}
        columns={[
          { title: '容器', dataIndex: 'container', width: 200 },
          { title: '类型', dataIndex: 'source_type', width: 120, render: (v: string) => <Tag color={v === 'configMap' ? 'blue' : 'purple'}>{v === 'configMap' ? '配置字典' : '密钥'}</Tag> },
          { title: '来源', dataIndex: 'source_name' },
          { title: '前缀', dataIndex: 'prefix', width: 160, render: (v: string) => v || '-' },
          { title: '操作', width: 90, render: (_: unknown, r: EnvRef) => (
            <Popconfirm title={`移除 ${r.source_name} 的环境变量注入？`} description="会触发 Pod 滚动更新" okButtonProps={{ danger: true }} onConfirm={() => doRemoveEnv(r.source_name, r.container)}>
              <Button size="small" type="link" danger icon={<DeleteOutlined />}>移除</Button>
            </Popconfirm>
          ) },
        ]}
        locale={{ emptyText: '暂无 envFrom 注入' }} />
    </div>
  )

  const body = loading ? (
    <div style={{ textAlign: 'center', padding: '30px 0' }}><Spin /></div>
  ) : !data ? (
    <Empty description="配置获取失败" />
  ) : onlySection === 'storage' ? storagePane : onlySection === 'env' ? envPane : (
    <Tabs items={[
      { key: 'storage', label: '存储', children: storagePane },
      { key: 'env', label: '环境变量', children: envPane },
    ]} />
  )

  return (
    <div>
      <Alert type="info" showIcon style={{ marginBottom: 12 }}
        message="存储：挂载为数据卷（volumes + volumeMounts，需填容器内路径）；环境变量：把配置字典/密钥注入为环境变量（envFrom）。均写入该工作负载 Pod 模板并触发滚动更新。" />
      {body}

      {/* 添加卷 */}
      <Modal title="添加卷" open={addOpen} onCancel={() => setAddOpen(false)} onOk={submitAdd} confirmLoading={submitting} okText="确定" destroyOnClose>
        <Form form={form} layout="vertical" preserve={false}>
          <Form.Item name="type" label="卷类型" rules={[{ required: true }]}>
            <Select options={ADD_TYPES} />
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(p, c) => p.type !== c.type}>
            {({ getFieldValue }) => {
              const t: MountType = getFieldValue('type')
              return (
                <>
                  {t && t !== 'emptyDir' && (
                    <Form.Item name="source_name" label="来源" rules={[{ required: true, message: '请选择来源' }]}>
                      <Select showSearch placeholder={`选择命名空间 ${namespace} 下的${typeLabel(t)}`}
                        options={optionsFor(t)} optionFilterProp="label" notFoundContent="该命名空间下没有可选资源" />
                    </Form.Item>
                  )}
                  <Form.Item name="mount_path" label="挂载路径（容器内）" rules={[{ required: true, message: '请填写容器内路径' }, { pattern: /^\//, message: '必须是绝对路径（以 / 开头）' }]}>
                    <Input placeholder="/data" />
                  </Form.Item>
                  <Form.Item name="sub_path" label="文件名 / 子路径（可选）" tooltip="挂载单个文件时填来源里的 key/文件名；留空则整体挂载为目录">
                    <Input placeholder="如 application.yml（挂单文件时填）" />
                  </Form.Item>
                  <Form.Item name="volume_name" label="卷名（可选）" tooltip="留空自动生成；重复会自动加序号">
                    <Input placeholder="自动生成" />
                  </Form.Item>
                  <Form.Item name="containers" label="挂到哪些容器" tooltip="留空=全部业务容器">
                    <Select mode="multiple" allowClear placeholder="默认全部容器" options={containerNames.map((n) => ({ value: n, label: n }))} />
                  </Form.Item>
                  <Form.Item name="read_only" label="只读" valuePropName="checked">
                    <Switch />
                  </Form.Item>
                </>
              )
            }}
          </Form.Item>
        </Form>
      </Modal>

      {/* 注入环境变量 */}
      <Modal title="注入环境变量" open={addEnvOpen} onCancel={() => setAddEnvOpen(false)} onOk={submitAddEnv} confirmLoading={submitting} okText="确定" destroyOnClose>
        <Alert type="info" showIcon style={{ marginBottom: 12 }} message="把整个配置字典/密钥注入为环境变量（envFrom）。单个变量请直接编辑工作负载 YAML。" />
        <Form form={envForm} layout="vertical" preserve={false}>
          <Form.Item name="env_type" label="类型" rules={[{ required: true }]}>
            <Radio.Group>
              <Radio.Button value="configMap">配置字典</Radio.Button>
              <Radio.Button value="secret">密钥</Radio.Button>
            </Radio.Group>
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(p, c) => p.env_type !== c.env_type}>
            {({ getFieldValue }) => (
              <Form.Item name="source_name" label="来源" rules={[{ required: true, message: '请选择来源' }]}>
                <Select showSearch placeholder="选择来源" options={optionsFor(getFieldValue('env_type'))} optionFilterProp="label"
                  notFoundContent="该命名空间下没有可选资源" />
              </Form.Item>
            )}
          </Form.Item>
          <Form.Item name="containers" label="注入到哪些容器" tooltip="留空=全部业务容器">
            <Select mode="multiple" allowClear placeholder="默认全部容器" options={containerNames.map((n) => ({ value: n, label: n }))} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
