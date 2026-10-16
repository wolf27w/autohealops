// 工作负载「配置」面板（KubeSphere 风格）：
//   存储     —— 统一的卷列表（配置字典/密钥/PVC/临时卷），每卷显示挂载点（容器 → 路径），可添加/编辑/移除
//   环境变量 —— env 批量编辑器（KubeSphere 风格：逐行填/删，整份保存）+ envFrom 注入
// props.onlySection 传 'storage' | 'env' 时只渲染单块，不传则内部用 Tabs
import { useCallback, useEffect, useState } from 'react'
import { Button, Space, Table, Tag, Modal, Form, Select, Input, Switch, Popconfirm, message, Empty, Spin, Typography, Alert, Tabs, Radio, Divider } from 'antd'
import { PlusOutlined, ReloadOutlined, DeleteOutlined, EditOutlined, EyeOutlined } from '@ant-design/icons'
import {
  getWorkloadMounts, addWorkloadMount, removeWorkloadMount, editWorkloadMount,
  replaceWorkloadEnv, deleteWorkloadEnv, getMiscResources, getObjectDetail, type AddMountPayload,
} from '../services/api'

type MountType = 'configMap' | 'secret' | 'pvc' | 'emptyDir'
type EnvType = 'value' | 'configMap' | 'secret' | 'field' | 'resource'

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

interface EnvRow { name: string; type: EnvType; value: string; refName: string; refKey: string; fieldPath: string; resource: string }

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
const ENV_TYPES: { value: EnvType; label: string }[] = [
  { value: 'value', label: '直接填值' },
  { value: 'configMap', label: '配置字典(某 key)' },
  { value: 'secret', label: '密钥(某 key)' },
  { value: 'field', label: '字段 fieldRef' },
  { value: 'resource', label: '资源 resourceFieldRef' },
]
const typeLabel = (t: string) => TYPE_LABEL[t] || t || '-'
const volTypeToMount = (t: string): MountType => (t === 'persistentVolumeClaim' ? 'pvc' : (t as MountType))
const emptyRow = (): EnvRow => ({ name: '', type: 'value', value: '', refName: '', refKey: '', fieldPath: '', resource: '' })

export default function WorkloadMountsPanel({ clusterId, kind, namespace, name, onlySection }: {
  clusterId: number; kind: string; namespace: string; name: string; onlySection?: 'storage' | 'env'
}) {
  const [data, setData] = useState<WorkloadMounts | null>(null)
  const [loading, setLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [misc, setMisc] = useState<{ configmaps: MiscRow[]; secrets: MiscRow[]; pvcs: MiscRow[] }>({ configmaps: [], secrets: [], pvcs: [] })

  const [volModal, setVolModal] = useState<{ open: boolean; edit?: VolumeInfo }>({ open: false })
  const [envFromModal, setEnvFromModal] = useState<{ open: boolean; edit?: EnvRef }>({ open: false })
  const [envEdit, setEnvEdit] = useState<{ open: boolean; container: string; rows: EnvRow[] }>({ open: false, container: '', rows: [] })
  const [content, setContent] = useState<{ open: boolean; title: string; loading: boolean; secret: boolean; rows: { key: string; value: string }[]; revealed: Record<string, boolean> }>({ open: false, title: '', loading: false, secret: false, rows: [], revealed: {} })
  const [volForm] = Form.useForm()
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
  const cmSecretOptions = (t: EnvType) => (t === 'configMap' ? misc.configmaps : misc.secrets).map((x) => ({ value: x.name, label: x.name }))

  // 选项：把当前值也包进 options（否则值不在列表时 Select 可能显示空白）
  const withCurrent = (opts: { value: string; label: string }[], cur?: string) =>
    cur && !opts.some((o) => o.value === cur) ? [{ value: cur, label: cur }, ...opts] : opts

  // 卷弹窗：打开后再回填（避免在字段挂载前 setFieldsValue 丢失值）
  useEffect(() => {
    if (!volModal.open) return
    const v = volModal.edit
    if (v) {
      const rows = mountsOfVolume(v.name)
      volForm.setFieldsValue({
        type: volTypeToMount(v.type), source_name: v.type === 'emptyDir' ? undefined : v.source,
        mount_path: rows[0]?.mount_path, sub_path: rows[0]?.sub_path, read_only: !!rows[0]?.read_only,
        containers: containersOfVolume(v.name), volume_name: v.name,
      })
    } else {
      volForm.setFieldsValue({ type: 'pvc', read_only: false, containers: [] })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [volModal.open, volModal.edit])

  // envFrom 弹窗：打开后再回填
  useEffect(() => {
    if (!envFromModal.open) return
    const e = envFromModal.edit
    if (e) envFromForm.setFieldsValue({ env_type: e.source_type, source_name: e.source_name, prefix: e.prefix, containers: [e.container] })
    else envFromForm.setFieldsValue({ env_type: 'configMap', containers: [] })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [envFromModal.open, envFromModal.edit])

  // 查看配置内容（配置字典/密钥的 key → value；密钥默认打码）
  const openContent = async (t: MountType, name: string) => {
    const kind = t === 'configMap' ? 'configmap' : 'secret'
    const isSecret = t === 'secret'
    setContent({ open: true, title: `${typeLabel(t)}：${name}`, loading: true, secret: isSecret, rows: [], revealed: {} })
    try {
      const d = await getObjectDetail(clusterId, kind, namespace, name)
      const data = ((d as { data?: Record<string, string> }).data) || {}
      setContent({ open: true, title: `${typeLabel(t)}：${name}`, loading: false, secret: isSecret,
        rows: Object.entries(data).map(([key, value]) => ({ key, value: String(value ?? '') })), revealed: {} })
    } catch {
      setContent({ open: true, title: `${typeLabel(t)}：${name}`, loading: false, secret: isSecret, rows: [], revealed: {} })
    }
  }
  const closeContent = () => setContent({ open: false, title: '', loading: false, secret: false, rows: [], revealed: {} })
  const toggleReveal = (key: string) => setContent((s) => ({ ...s, revealed: { ...s.revealed, [key]: !s.revealed[key] } }))
  const allRevealed = !!content.rows.length && content.rows.every((r) => content.revealed[r.key])

  // ---------- 卷 ----------
  const openAddVol = () => { volForm.resetFields(); setVolModal({ open: true }) }
  const openEditVol = (v: VolumeInfo) => { volForm.resetFields(); setVolModal({ open: true, edit: v }) }
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
  const doRemoveVol = async (volumeName: string) => {
    try { await removeWorkloadMount(clusterId, kind, namespace, name, { mode: 'volume', volume_name: volumeName }); message.success('已移除，正在滚动更新 Pod'); load() } catch { /* */ }
  }

  // ---------- 环境变量（批量编辑） ----------
  const envRowsOf = (container: string): EnvRow[] => {
    const list = (data?.envs || []).filter((e) => e.container === container)
    if (list.length === 0) return [emptyRow()]
    return list.map((e) => ({
      name: e.name,
      type: (e.vf_type || 'value') as EnvType,
      value: e.value ?? '',
      refName: e.vf_name ?? '', refKey: e.vf_key ?? '',
      fieldPath: e.vf_field_path ?? '', resource: e.vf_resource ?? '',
    }))
  }
  const openEnvEdit = (container?: string) => {
    const c = container || containerNames[0] || ''
    setEnvEdit({ open: true, container: c, rows: envRowsOf(c) })
  }
  const switchEnvContainer = (c: string) => setEnvEdit((s) => ({ ...s, container: c, rows: envRowsOf(c) }))
  const setRow = (i: number, patch: Partial<EnvRow>) =>
    setEnvEdit((s) => ({ ...s, rows: s.rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)) }))
  const addRow = () => setEnvEdit((s) => ({ ...s, rows: [...s.rows, emptyRow()] }))
  const delRow = (i: number) => setEnvEdit((s) => ({ ...s, rows: s.rows.filter((_, idx) => idx !== i) }))
  // 引用整个配置字典/密钥：读出所有 key，逐行加为 valueFrom
  const importKeys = async (t: EnvType, resName: string) => {
    if (!resName) return
    try {
      const d = await getObjectDetail(clusterId, t === 'configMap' ? 'configmap' : 'secret', namespace, resName)
      const keys = Object.keys(((d as { data?: Record<string, string> }).data) || {})
      if (keys.length === 0) { message.info('该来源没有数据项'); return }
      setEnvEdit((s) => ({
        ...s,
        rows: [...s.rows.filter((r) => r.name || r.refKey || r.value), ...keys.map((k) => ({ ...emptyRow(), name: k, type: t, refName: resName, refKey: k }))],
      }))
    } catch { /* 拦截器已提示 */ }
  }
  const submitEnvEdit = async () => {
    const rows = envEdit.rows.filter((r) => r.name.trim())
    for (const r of rows) {
      if ((r.type === 'configMap' || r.type === 'secret') && (!r.refName || !r.refKey)) { message.error(`「${r.name}」请选择来源与 key`); return }
      if (r.type === 'field' && !r.fieldPath) { message.error(`「${r.name}」请填字段路径`); return }
      if (r.type === 'resource' && !r.resource) { message.error(`「${r.name}」请填资源`); return }
    }
    const seen = new Set<string>()
    for (const r of rows) { if (seen.has(r.name)) { message.error(`变量名重复：${r.name}`); return } seen.add(r.name) }
    setSubmitting(true)
    try {
      await replaceWorkloadEnv(clusterId, kind, namespace, name, {
        container: envEdit.container,
        envs: rows.map((r) => ({
          name: r.name.trim(),
          ...(r.type === 'value' ? { value: r.value }
            : r.type === 'field' ? { value_from: { type: 'field' as const, field_path: r.fieldPath } }
              : r.type === 'resource' ? { value_from: { type: 'resource' as const, resource: r.resource } }
                : { value_from: { type: r.type as 'configMap' | 'secret', name: r.refName, key: r.refKey } }),
        })),
      })
      message.success('已保存环境变量，正在滚动更新 Pod')
      setEnvEdit({ open: false, container: '', rows: [] })
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

  // ---------- envFrom ----------
  const openAddEnvFrom = () => { envFromForm.resetFields(); setEnvFromModal({ open: true }) }
  const openEditEnvFrom = (e: EnvRef) => { envFromForm.resetFields(); setEnvFromModal({ open: true, edit: e }) }
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
        <Button size="small" type="primary" icon={<EditOutlined />} onClick={() => openEnvEdit()}>编辑环境变量</Button>
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
              {btn('编辑', () => openEnvEdit(r.container))}
              <Popconfirm title={`删除环境变量 ${r.name}？`} okButtonProps={{ danger: true }} onConfirm={() => doDeleteEnv(r)}>
                {btn('删除', () => { /* confirm */ }, true, <DeleteOutlined />)}
              </Popconfirm>
            </Space>
          ) },
        ]}
        locale={{ emptyText: '暂无环境变量（点「编辑环境变量」添加）' }} />

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
        message="存储：挂载为数据卷（volumes + volumeMounts）；环境变量：env 逐行批量编辑 + 配置字典/密钥整份 envFrom 注入。均写入该工作负载 Pod 模板并触发滚动更新。" />
      {body}

      {/* 添加/编辑卷 */}
      <Modal title={volModal.edit ? `编辑卷：${volModal.edit.name}` : '添加卷'} open={volModal.open} forceRender
        onCancel={() => setVolModal({ open: false })} onOk={submitVol} confirmLoading={submitting} okText="确定">
        <Form form={volForm} layout="vertical">
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
                        options={withCurrent(optionsFor(t), volModal.edit ? volModal.edit.source : undefined)} optionFilterProp="label" notFoundContent="该命名空间下没有可选资源" />
                    </Form.Item>
                  )}
                  {(t === 'configMap' || t === 'secret') && (
                    <Form.Item noStyle shouldUpdate={(p, c) => p.source_name !== c.source_name}>
                      {({ getFieldValue }) => {
                        const sn = getFieldValue('source_name') as string | undefined
                        return sn ? (
                          <div style={{ margin: '-14px 0 8px' }}>
                            <Button size="small" type="link" icon={<EyeOutlined />} onClick={() => openContent(t, sn)}>查看配置内容</Button>
                          </div>
                        ) : null
                      }}
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

      {/* 环境变量批量编辑（KubeSphere 风格：逐行编辑，整份保存） */}
      <Modal title="编辑环境变量" open={envEdit.open} forceRender onCancel={() => setEnvEdit({ open: false, container: '', rows: [] })}
        onOk={submitEnvEdit} confirmLoading={submitting} okText="保存" width={900}>
        <Space style={{ marginBottom: 10 }} wrap>
          <span>容器</span>
          <Select size="small" style={{ width: 200 }} value={envEdit.container} onChange={switchEnvContainer}
            options={allContainers.map((c) => ({ value: c.name, label: c.name }))} />
          <Button size="small" icon={<PlusOutlined />} onClick={addRow}>添加变量</Button>
          <Select size="small" style={{ width: 190 }} placeholder="引用配置字典（导入所有 key）" value={undefined} showSearch optionFilterProp="label"
            options={(misc.configmaps || []).map((x) => ({ value: x.name, label: x.name }))}
            onChange={(v) => importKeys('configMap', String(v))} />
          <Select size="small" style={{ width: 190 }} placeholder="引用密钥（导入所有 key）" value={undefined} showSearch optionFilterProp="label"
            options={(misc.secrets || []).map((x) => ({ value: x.name, label: x.name }))}
            onChange={(v) => importKeys('secret', String(v))} />
        </Space>
        <div style={{ border: '1px solid #f0f0f0', borderRadius: 6, padding: 8, maxHeight: 420, overflow: 'auto' }}>
          {envEdit.rows.length === 0 && <Empty description="暂无环境变量，点「添加变量」" />}
          {envEdit.rows.map((r, i) => (
            <div key={i} style={{ display: 'flex', gap: 6, marginBottom: 6, alignItems: 'center' }}>
              <Input style={{ width: 190 }} placeholder="变量名" value={r.name} onChange={(e) => setRow(i, { name: e.target.value })} />
              <Select style={{ width: 150 }} value={r.type} onChange={(v) => setRow(i, { type: v })} options={ENV_TYPES} />
              {r.type === 'value' && (
                <Input style={{ flex: 1 }} placeholder="值" value={r.value} onChange={(e) => setRow(i, { value: e.target.value })} />
              )}
              {(r.type === 'configMap' || r.type === 'secret') && (
                <>
                  <Select style={{ width: 200 }} showSearch optionFilterProp="label" placeholder={r.type === 'configMap' ? '选择配置字典' : '选择密钥'}
                    value={r.refName || undefined} options={cmSecretOptions(r.type)} onChange={(v) => setRow(i, { refName: String(v) })} />
                  <Input style={{ width: 160 }} placeholder="key" value={r.refKey} onChange={(e) => setRow(i, { refKey: e.target.value })} />
                </>
              )}
              {r.type === 'field' && (
                <Input style={{ flex: 1 }} placeholder="如 metadata.name" value={r.fieldPath} onChange={(e) => setRow(i, { fieldPath: e.target.value })} />
              )}
              {r.type === 'resource' && (
                <Input style={{ flex: 1 }} placeholder="如 limits.cpu" value={r.resource} onChange={(e) => setRow(i, { resource: e.target.value })} />
              )}
              <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={() => delRow(i)} />
            </div>
          ))}
        </div>
        <Divider style={{ margin: '10px 0 0' }} />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          逐行编辑后点「保存」整份写入该容器的 env（空行忽略；同名会报错）。改单个 key 引用可直接改上面行的来源/key。
        </Typography.Text>
      </Modal>

      {/* 查看配置内容 */}
      <Modal title={content.title} open={content.open} onCancel={closeContent}
        footer={null} width={760} forceRender>
        {content.loading ? (
          <div style={{ textAlign: 'center', padding: '24px 0' }}><Spin /></div>
        ) : content.rows.length === 0 ? (
          <Empty description="无数据项" />
        ) : (
          <>
            {content.secret && (
              <div style={{ marginBottom: 8, textAlign: 'right' }}>
                <Button size="small" onClick={() => setContent((s) => ({ ...s, revealed: Object.fromEntries(s.rows.map((r) => [r.key, !allRevealed])) }))}>
                  {allRevealed ? '全部隐藏' : '全部显示'}
                </Button>
              </div>
            )}
            <Table rowKey="key" size="small" pagination={false} dataSource={content.rows}
              columns={[
                { title: 'Key', dataIndex: 'key', width: 240 },
                { title: 'Value', dataIndex: 'value', render: (v: string, r: { key: string }) => {
                  if (content.secret && !content.revealed[r.key]) {
                    return <Space size={8}><Typography.Text type="secondary">{'••••••••'}</Typography.Text>
                      <a onClick={() => toggleReveal(r.key)}>显示</a></Space>
                  }
                  return (
                    <Space align="start" size={8}>
                      <Typography.Text style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all', fontSize: 12 }}>{v || '-'}</Typography.Text>
                      {content.secret && <a onClick={() => toggleReveal(r.key)}>隐藏</a>}
                    </Space>
                  )
                } },
              ]} />
          </>
        )}
      </Modal>

      {/* 注入/编辑 envFrom */}
      <Modal title={envFromModal.edit ? '编辑环境变量注入' : '注入配置字典/密钥'} open={envFromModal.open} forceRender
        onCancel={() => setEnvFromModal({ open: false })} onOk={submitEnvFrom} confirmLoading={submitting} okText="确定">
        <Alert type="info" showIcon style={{ marginBottom: 12 }} message="把整份配置字典/密钥注入为环境变量（envFrom）。改来源=先移除再注入。" />
        <Form form={envFromForm} layout="vertical">
          <Form.Item name="env_type" label="类型" rules={[{ required: true }]}>
            <Radio.Group disabled={!!envFromModal.edit}>
              <Radio.Button value="configMap">配置字典</Radio.Button>
              <Radio.Button value="secret">密钥</Radio.Button>
            </Radio.Group>
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(p, c) => p.env_type !== c.env_type}>
            {({ getFieldValue }) => (
              <Form.Item name="source_name" label="来源" rules={[{ required: true, message: '请选择来源' }]}>
                <Select showSearch placeholder="选择来源" options={withCurrent(optionsFor(getFieldValue('env_type')), envFromModal.edit?.source_name)} optionFilterProp="label"
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
