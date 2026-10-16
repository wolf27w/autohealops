// 工作负载「配置」面板（KubeSphere 风格）：
//   存储     —— 统一的卷列表（配置字典/密钥/PVC/临时卷），每卷显示挂载点（容器 → 路径），可添加/编辑/移除
//   环境变量 —— 已配置的 env + envFrom 列表，可新增/编辑/删除/注入/移除
// props.onlySection 传 'storage' | 'env' 时只渲染单块，不传则内部用 Tabs
import { useCallback, useEffect, useState } from 'react'
import { Button, Space, Table, Tag, Modal, Form, Select, Input, Switch, Popconfirm, message, Empty, Spin, Typography, Alert, Tabs, Radio } from 'antd'
import { PlusOutlined, ReloadOutlined, DeleteOutlined, EditOutlined } from '@ant-design/icons'
import {
  getWorkloadMounts, addWorkloadMount, removeWorkloadMount, editWorkloadMount,
  upsertWorkloadEnv, deleteWorkloadEnv, getMiscResources, type AddMountPayload,
} from '../services/api'

type MountType = 'configMap' | 'secret' | 'pvc' | 'emptyDir'

interface VolumeMountInfo { name: string; mount_path: string; sub_path?: string; read_only?: boolean }
interface VolumeInfo { name: string; type: string; source: string; read_only?: boolean }
interface ContainerMounts { name: string; mounts: VolumeMountInfo[] }
interface EnvRef { container: string; source_type: string; source_name: string; prefix?: string; optional?: boolean }
interface EnvVar {
  container: string; name: string; value?: string; value_from?: string; is_init?: boolean
  vf_type?: string; vf_name?: string; vf_key?: string; vf_field_path?: string; vf_resource?: string
}
interface WorkloadMounts { volumes: VolumeInfo[]; containers: ContainerMounts[]; init_containers: ContainerMounts[]; env_refs?: EnvRef[]; envs?: EnvVar[] }
interface MiscRow { namespace: string; name: string; [key: string]: unknown }

const TYPE_LABEL: Record<string, string> = {
  configMap: '配置字典', secret: '密钥', pvc: '持久卷声明(PVC)', emptyDir: '临时卷',
  persistentVolumeClaim: '持久卷声明(PVC)', hostPath: 'hostPath', projected: 'projected', nfs: 'NFS', csi: 'CSI',
}
const TYPE_COLOR: Record<string, string> = { configMap: 'blue', secret: 'purple', persistentVolumeClaim: 'green', emptyDir: 'default' }
const ADD_TYPES: { value: MountType; label: string }[] = [
  { value: 'pvc', label: '持久卷声明(PVC)' },
  { value: 'configMap', label: '配置字典' },
  { value: 'secret', label: '密钥' },
  { value: 'emptyDir', label: '临时卷' },
]
const typeLabel = (t: string) => TYPE_LABEL[t] || t || '-'
// 卷类型显示值 → 添加/编辑用的 MountType
const volTypeToMount = (t: string): MountType => (t === 'persistentVolumeClaim' ? 'pvc' : (t as MountType))

export default function WorkloadMountsPanel({ clusterId, kind, namespace, name, onlySection }: {
  clusterId: number; kind: string; namespace: string; name: string; onlySection?: 'storage' | 'env'
}) {
  const [data, setData] = useState<WorkloadMounts | null>(null)
  const [loading, setLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [misc, setMisc] = useState<{ configmaps: MiscRow[]; secrets: MiscRow[]; pvcs: MiscRow[] }>({ configmaps: [], secrets: [], pvcs: [] })

  const [volModal, setVolModal] = useState<{ open: boolean; edit?: VolumeInfo }>({ open: false })
  const [envModal, setEnvModal] = useState<{ open: boolean; edit?: EnvVar }>({ open: false })
  const [envFromModal, setEnvFromModal] = useState<{ open: boolean; edit?: EnvRef }>({ open: false })
  const [volForm] = Form.useForm()
  const [envForm] = Form.useForm()
  const [envFromForm] = Form.useForm()

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

  const mountsOfVolume = (volName: string) => {
    const rows: { container: string; mount_path: string; sub_path?: string; read_only?: boolean }[] = []
    for (const c of allContainers) {
      for (const m of c.mounts || []) {
        if (m.name === volName) rows.push({ container: c.name, mount_path: m.mount_path, sub_path: m.sub_path, read_only: m.read_only })
      }
    }
    return rows
  }
  const containersOfVolume = (volName: string) => mountsOfVolume(volName).map((r) => r.container)

  const optionsFor = (t?: MountType) => {
    const list = t === 'configMap' ? misc.configmaps : t === 'secret' ? misc.secrets : t === 'pvc' ? misc.pvcs : []
    return list.map((x) => ({ value: x.name, label: x.name }))
  }

  // ---------- 卷：添加/编辑 ----------
  const openAddVol = () => { setVolModal({ open: true }); volForm.resetFields(); volForm.setFieldsValue({ type: 'pvc', read_only: false, containers: [] }) }
  const openEditVol = (v: VolumeInfo) => {
    const rows = mountsOfVolume(v.name)
    setVolModal({ open: true, edit: v })
    volForm.resetFields()
    volForm.setFieldsValue({
      type: volTypeToMount(v.type), source_name: v.type === 'emptyDir' ? undefined : v.source,
      mount_path: rows[0]?.mount_path, sub_path: rows[0]?.sub_path, read_only: !!rows[0]?.read_only,
      containers: containersOfVolume(v.name), volume_name: v.name,
    })
  }
  const submitVol = async () => {
    const v = await volForm.validateFields()
    const t: MountType = v.type
    setSubmitting(true)
    try {
      if (volModal.edit) {
        await editWorkloadMount(clusterId, kind, namespace, name, {
          volume_name: volModal.edit.name,
          mount_path: v.mount_path, sub_path: v.sub_path || '', read_only: !!v.read_only,
          containers: v.containers || [],
          ...(t !== 'emptyDir' ? { source_type: t, source_name: v.source_name } : {}),
        })
        message.success('已保存，正在滚动更新 Pod')
      } else {
        const payload: AddMountPayload = {
          mode: 'volume', source_type: t, source_name: t === 'emptyDir' ? '' : v.source_name,
          mount_path: v.mount_path, sub_path: v.sub_path || undefined, read_only: !!v.read_only,
          volume_name: v.volume_name || undefined, containers: v.containers || [],
        }
        await addWorkloadMount(clusterId, kind, namespace, name, payload)
        message.success('已挂载，正在滚动更新 Pod')
      }
      setVolModal({ open: false })
      load()
    } catch { /* 拦截器已提示 */ } finally { setSubmitting(false) }
  }

  // ---------- env：新增/编辑 ----------
  const openAddEnv = () => { setEnvModal({ open: true }); envForm.resetFields(); envForm.setFieldsValue({ container: containerNames[0], vf_type: 'literal' }) }
  const openEditEnv = (e: EnvVar) => {
    setEnvModal({ open: true, edit: e })
    envForm.resetFields()
    envForm.setFieldsValue({
      container: e.container, name: e.name,
      vf_type: e.vf_type || 'literal', vf_name: e.vf_name, vf_key: e.vf_key,
      vf_field_path: e.vf_field_path, vf_resource: e.vf_resource, value: e.value,
    })
  }
  const submitEnv = async () => {
    const v = await envForm.validateFields()
    setSubmitting(true)
    try {
      const t = v.vf_type || 'literal'
      const value_from = t === 'literal' ? undefined
        : t === 'field' ? { type: 'field' as const, field_path: v.vf_field_path }
          : t === 'resource' ? { type: 'resource' as const, resource: v.vf_resource }
            : { type: t as 'configMap' | 'secret', name: v.vf_name, key: v.vf_key }
      await upsertWorkloadEnv(clusterId, kind, namespace, name, {
        container: v.container, name: v.name, old_name: envModal.edit?.name || v.name,
        value: t === 'literal' ? (v.value ?? '') : undefined, value_from,
      })
      message.success('已保存，正在滚动更新 Pod')
      setEnvModal({ open: false })
      load()
    } catch { /* 拦截器已提示 */ } finally { setSubmitting(false) }
  }
  const doDeleteEnv = async (e: EnvVar) => {
    try {
      await deleteWorkloadEnv(clusterId, kind, namespace, name, { container: e.container, var_name: e.name })
      message.success('已删除，正在滚动更新 Pod')
      load()
    } catch { /* 拦截器已提示 */ }
  }

  // ---------- envFrom：注入/编辑（前缀） ----------
  const openAddEnvFrom = () => { setEnvFromModal({ open: true }); envFromForm.resetFields(); envFromForm.setFieldsValue({ env_type: 'configMap', containers: [] }) }
  const openEditEnvFrom = (e: EnvRef) => { setEnvFromModal({ open: true, edit: e }); envFromForm.resetFields(); envFromForm.setFieldsValue({ env_type: e.source_type, source_name: e.source_name, prefix: e.prefix, containers: [e.container] }) }
  const submitEnvFrom = async () => {
    const v = await envFromForm.validateFields()
    setSubmitting(true)
    try {
      await addWorkloadMount(clusterId, kind, namespace, name, {
        mode: 'env', source_type: v.env_type, source_name: v.source_name, prefix: v.prefix, containers: v.containers || [],
      } as AddMountPayload & { prefix?: string })
      message.success('已保存，正在滚动更新 Pod')
      setEnvFromModal({ open: false })
      load()
    } catch { /* 拦截器已提示 */ } finally { setSubmitting(false) }
  }

  const doRemoveVol = async (volumeName: string) => {
    try { await removeWorkloadMount(clusterId, kind, namespace, name, { mode: 'volume', volume_name: volumeName }); message.success('已移除，正在滚动更新 Pod'); load() } catch { /* */ }
  }
  const doRemoveEnvFrom = async (sourceName: string, container: string) => {
    try { await removeWorkloadMount(clusterId, kind, namespace, name, { mode: 'env', source_name: sourceName, container }); message.success('已移除，正在滚动更新 Pod'); load() } catch { /* */ }
  }

  const btn = (label: string, onClick: () => void, danger = false, icon = <EditOutlined />) => (
    <Button size="small" type="link" danger={danger} icon={icon} onClick={onClick}>{label}</Button>
  )

  // ---------------- 存储 ----------------
  const storagePane = (
    <div>
      <Space style={{ marginBottom: 10 }}>
        <Button size="small" type="primary" icon={<PlusOutlined />} onClick={openAddVol}>添加卷</Button>
        <Button size="small" icon={<ReloadOutlined />} onClick={load}>刷新</Button>
      </Space>
      <Table rowKey="name" size="small" pagination={false} dataSource={data?.volumes || []}
        columns={[
          { title: '卷名', dataIndex: 'name', width: 170, render: (v: string) => <span style={{ fontWeight: 600 }}>{v}</span> },
          { title: '类型', dataIndex: 'type', width: 150, render: (v: string) => <Tag color={TYPE_COLOR[v] || 'default'}>{typeLabel(v)}</Tag> },
          { title: '来源', dataIndex: 'source', width: 190, render: (v: string) => v || '-' },
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
          { title: '操作', width: 160, render: (_: unknown, r: VolumeInfo) => (
            <Space size={0}>
              {btn('编辑', () => openEditVol(r))}
              <Popconfirm title={`移除卷 ${r.name} 的全部挂载？`} description="会触发 Pod 滚动更新" okButtonProps={{ danger: true }} onConfirm={() => doRemoveVol(r.name)}>
                {btn('移除', () => { /* confirm */ }, true, <DeleteOutlined />)}
              </Popconfirm>
            </Space>
          ) },
        ]}
        locale={{ emptyText: '暂无卷' }} />
    </div>
  )

  // ---------------- 环境变量 ----------------
  const envPane = (
    <div>
      <Space style={{ marginBottom: 10 }}>
        <Button size="small" type="primary" icon={<PlusOutlined />} onClick={openAddEnv}>添加环境变量</Button>
        <Button size="small" icon={<PlusOutlined />} onClick={openAddEnvFrom}>注入配置字典/密钥（envFrom）</Button>
        <Button size="small" icon={<ReloadOutlined />} onClick={load}>刷新</Button>
      </Space>
      <Typography.Text strong>环境变量（env）</Typography.Text>
      <Table style={{ marginTop: 6 }} rowKey={(r) => `${r.container}-${r.name}`} size="small" pagination={false} dataSource={data?.envs || []}
        columns={[
          { title: '容器', dataIndex: 'container', width: 170, render: (v: string, r: EnvVar) => <Space size={4}>{r.is_init && <Tag color="purple">init</Tag>}{v}</Space> },
          { title: '变量名', dataIndex: 'name', width: 200 },
          { title: '值', dataIndex: 'value', render: (v: string) => (v ? <Typography.Text code>{v}</Typography.Text> : '-') },
          { title: '来源（valueFrom）', dataIndex: 'value_from', render: (v: string) => v || '-' },
          { title: '操作', width: 140, render: (_: unknown, r: EnvVar) => (
            <Space size={0}>
              {btn('编辑', () => openEditEnv(r))}
              <Popconfirm title={`删除环境变量 ${r.name}？`} okButtonProps={{ danger: true }} onConfirm={() => doDeleteEnv(r)}>
                {btn('删除', () => { /* confirm */ }, true, <DeleteOutlined />)}
              </Popconfirm>
            </Space>
          ) },
        ]}
        locale={{ emptyText: '暂无环境变量' }} />

      <Typography.Text strong style={{ display: 'inline-block', margin: '16px 0 6px' }}>环境变量注入（envFrom，整份配置字典/密钥）</Typography.Text>
      <Table rowKey={(r) => `${r.container}-${r.source_name}`} size="small" pagination={false} dataSource={data?.env_refs || []}
        columns={[
          { title: '容器', dataIndex: 'container', width: 170 },
          { title: '类型', dataIndex: 'source_type', width: 110, render: (v: string) => <Tag color={v === 'configMap' ? 'blue' : 'purple'}>{v === 'configMap' ? '配置字典' : '密钥'}</Tag> },
          { title: '来源', dataIndex: 'source_name' },
          { title: '前缀', dataIndex: 'prefix', width: 150, render: (v: string) => v || '-' },
          { title: '操作', width: 140, render: (_: unknown, r: EnvRef) => (
            <Space size={0}>
              {btn('编辑', () => openEditEnvFrom(r))}
              <Popconfirm title={`移除 ${r.source_name} 的环境变量注入？`} description="会触发 Pod 滚动更新" okButtonProps={{ danger: true }} onConfirm={() => doRemoveEnvFrom(r.source_name, r.container)}>
                {btn('移除', () => { /* confirm */ }, true, <DeleteOutlined />)}
              </Popconfirm>
            </Space>
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
    <Tabs items={[{ key: 'storage', label: '存储', children: storagePane }, { key: 'env', label: '环境变量', children: envPane }]} />
  )

  return (
    <div>
      <Alert type="info" showIcon style={{ marginBottom: 12 }}
        message="存储：挂载为数据卷（volumes + volumeMounts）；环境变量：env 单变量 / 配置字典·密钥整份 envFrom 注入。均写入该工作负载 Pod 模板并触发滚动更新。" />
      {body}

      {/* 添加/编辑卷 */}
      <Modal title={volModal.edit ? `编辑卷：${volModal.edit.name}` : '添加卷'} open={volModal.open}
        onCancel={() => setVolModal({ open: false })} onOk={submitVol} confirmLoading={submitting} okText="确定" destroyOnClose>
        <Form form={volForm} layout="vertical" preserve={false}>
          <Form.Item name="type" label="卷类型" rules={[{ required: true }]}>
            <Select options={ADD_TYPES} disabled={!!volModal.edit} />
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
                  {!volModal.edit && (
                    <Form.Item name="volume_name" label="卷名（可选）" tooltip="留空自动生成；重复会自动加序号">
                      <Input placeholder="自动生成" />
                    </Form.Item>
                  )}
                  <Form.Item name="containers" label="挂到哪些容器" tooltip="编辑时按此列表增/删挂载；留空=不挂任何容器">
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

      {/* 添加/编辑单个环境变量 */}
      <Modal title={envModal.edit ? `编辑环境变量：${envModal.edit.name}` : '添加环境变量'} open={envModal.open}
        onCancel={() => setEnvModal({ open: false })} onOk={submitEnv} confirmLoading={submitting} okText="确定" destroyOnClose>
        <Form form={envForm} layout="vertical" preserve={false}>
          <Form.Item name="container" label="容器" rules={[{ required: true, message: '请选择容器' }]}>
            <Select options={allContainers.map((c) => ({ value: c.name, label: c.name }))} />
          </Form.Item>
          <Form.Item name="name" label="变量名" rules={[{ required: true, message: '请填写变量名' }]}>
            <Input placeholder="如 LOG_LEVEL" />
          </Form.Item>
          <Form.Item name="vf_type" label="值来源" rules={[{ required: true }]}>
            <Select options={[
              { value: 'literal', label: '直接填值' },
              { value: 'configMap', label: '配置字典（某 key）' },
              { value: 'secret', label: '密钥（某 key）' },
              { value: 'field', label: 'Pod 字段（fieldRef）' },
              { value: 'resource', label: '容器资源（resourceFieldRef）' },
            ]} />
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(p, c) => p.vf_type !== c.vf_type}>
            {({ getFieldValue }) => {
              const t = getFieldValue('vf_type')
              if (t === 'literal') return <Form.Item name="value" label="值"><Input placeholder="值" /></Form.Item>
              if (t === 'field') return <Form.Item name="vf_field_path" label="字段路径" rules={[{ required: true, message: '如 metadata.name / status.podIP' }]}><Input placeholder="metadata.name" /></Form.Item>
              if (t === 'resource') return <Form.Item name="vf_resource" label="资源" rules={[{ required: true, message: '如 limits.cpu / requests.memory' }]}><Input placeholder="limits.cpu" /></Form.Item>
              return (
                <>
                  <Form.Item name="vf_name" label={t === 'configMap' ? '配置字典' : '密钥'} rules={[{ required: true, message: '请选择' }]}>
                    <Select showSearch options={optionsFor(t === 'configMap' ? 'configMap' : 'secret')} optionFilterProp="label" notFoundContent="该命名空间下没有可选资源" />
                  </Form.Item>
                  <Form.Item name="vf_key" label="键（key）" rules={[{ required: true, message: '请填写 key' }]}><Input placeholder="如 application.yml" /></Form.Item>
                </>
              )
            }}
          </Form.Item>
        </Form>
      </Modal>

      {/* 注入/编辑 envFrom */}
      <Modal title={envFromModal.edit ? '编辑环境变量注入' : '注入配置字典/密钥'} open={envFromModal.open}
        onCancel={() => setEnvFromModal({ open: false })} onOk={submitEnvFrom} confirmLoading={submitting} okText="确定" destroyOnClose>
        <Alert type="info" showIcon style={{ marginBottom: 12 }} message="把整份配置字典/密钥注入为环境变量（envFrom）。改来源=先移除再注入。" />
        <Form form={envFromForm} layout="vertical" preserve={false}>
          <Form.Item name="env_type" label="类型" rules={[{ required: true }]}>
            <Radio.Group disabled={!!envFromModal.edit}>
              <Radio.Button value="configMap">配置字典</Radio.Button>
              <Radio.Button value="secret">密钥</Radio.Button>
            </Radio.Group>
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(p, c) => p.env_type !== c.env_type}>
            {({ getFieldValue }) => (
              <Form.Item name="source_name" label="来源" rules={[{ required: true, message: '请选择来源' }]}>
                <Select showSearch placeholder="选择来源" options={optionsFor(getFieldValue('env_type'))} optionFilterProp="label"
                  notFoundContent="该命名空间下没有可选资源" disabled={!!envFromModal.edit} />
              </Form.Item>
            )}
          </Form.Item>
          <Form.Item name="prefix" label="前缀（可选）" tooltip="注入的环境变量会统一加此前缀">
            <Input placeholder="如 APP_" />
          </Form.Item>
          <Form.Item name="containers" label="注入到哪些容器" tooltip="留空=全部业务容器">
            <Select mode="multiple" allowClear placeholder="默认全部容器" options={containerNames.map((n) => ({ value: n, label: n }))} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
