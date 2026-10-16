// 工作负载「配置/密钥/PVC」管理面板（三者相互独立）：查看/添加/移除 Pod 模板挂载与环境变量注入
// props.types 决定本面板展示哪几类（configMap / secret / pvc）；每类一个独立区块
import { useCallback, useEffect, useState } from 'react'
import { Button, Space, Table, Tag, Modal, Form, Select, Input, Switch, Popconfirm, message, Empty, Spin, Typography, Alert, Radio, Divider } from 'antd'
import { PlusOutlined, ReloadOutlined, DeleteOutlined } from '@ant-design/icons'
import { getWorkloadMounts, addWorkloadMount, removeWorkloadMount, getMiscResources, type AddMountPayload } from '../services/api'

type SourceType = 'configMap' | 'secret' | 'pvc'

interface VolumeMountInfo { name: string; mount_path: string; sub_path?: string; read_only?: boolean; sub_path_expr?: string; propagation?: string }
interface VolumeInfo { name: string; type: string; source: string; read_only?: boolean }
interface ContainerMounts { name: string; mounts: VolumeMountInfo[] }
interface EnvRef { container: string; source_type: string; source_name: string; prefix?: string; optional?: boolean }
interface WorkloadMounts { volumes: VolumeInfo[]; containers: ContainerMounts[]; init_containers: ContainerMounts[]; env_refs?: EnvRef[] }
interface MiscRow { namespace: string; name: string; [key: string]: unknown }

const TYPE_LABEL: Record<SourceType, string> = { configMap: '配置字典', secret: '密钥', pvc: 'PVC' }
// 面板展示的类型 → volumes.type 字段值
const VOL_TYPE: Record<SourceType, string> = { configMap: 'configMap', secret: 'secret', pvc: 'persistentVolumeClaim' }

export default function WorkloadMountsPanel({ clusterId, kind, namespace, name, types = ['configMap', 'secret', 'pvc'] as SourceType[] }: {
  clusterId: number; kind: string; namespace: string; name: string; types?: SourceType[]
}) {
  const [data, setData] = useState<WorkloadMounts | null>(null)
  const [loading, setLoading] = useState(false)
  const [addType, setAddType] = useState<SourceType | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [misc, setMisc] = useState<{ configmaps: MiscRow[]; secrets: MiscRow[]; pvcs: MiscRow[] }>({ configmaps: [], secrets: [], pvcs: [] })
  const [form] = Form.useForm()

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

  const volumesOf = (t: SourceType) => (data?.volumes || []).filter((v) => v.type === VOL_TYPE[t])
  const envRefsOf = (t: SourceType) => (data?.env_refs || []).filter((e) => e.source_type === t)

  // 某卷被哪些容器挂载到哪些路径
  const mountsOfVolume = (volName: string) => {
    const rows: { container: string; mount_path: string; sub_path?: string; read_only?: boolean }[] = []
    for (const c of allContainers) {
      for (const m of c.mounts || []) {
        if (m.name === volName) rows.push({ container: c.name, mount_path: m.mount_path, sub_path: m.sub_path, read_only: m.read_only })
      }
    }
    return rows
  }

  const optionsFor = (t: SourceType) => {
    const list = t === 'configMap' ? misc.configmaps : t === 'secret' ? misc.secrets : misc.pvcs
    return list.map((x) => ({ value: x.name, label: x.name }))
  }

  const openAdd = (t: SourceType) => {
    setAddType(t)
    form.resetFields()
    form.setFieldsValue({ mode: 'volume', read_only: t !== 'pvc', containers: [] })
  }

  const submitAdd = async () => {
    const v = await form.validateFields()
    if (!addType) return
    setSubmitting(true)
    try {
      const mode = addType === 'pvc' ? 'volume' : (v.mode || 'volume')
      const payload: AddMountPayload = {
        mode,
        source_type: addType,
        source_name: v.source_name,
        containers: v.containers || [],
        ...(mode === 'volume' ? {
          mount_path: v.mount_path,
          sub_path: v.sub_path || undefined,
          read_only: !!v.read_only,
          volume_name: v.volume_name || undefined,
        } : {}),
      }
      await addWorkloadMount(clusterId, kind, namespace, name, payload)
      message.success(mode === 'env' ? '已注入环境变量，正在滚动更新 Pod' : '已挂载，正在滚动更新 Pod')
      setAddType(null)
      load()
    } catch { /* 拦截器已提示 */ } finally { setSubmitting(false) }
  }

  const doRemove = async (volumeName: string) => {
    try {
      await removeWorkloadMount(clusterId, kind, namespace, name, { mode: 'volume', volume_name: volumeName })
      message.success('已移除挂载，正在滚动更新 Pod')
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

  const volumeColumns = [
    { title: '卷名', dataIndex: 'name', width: 190, render: (v: string) => <span style={{ fontWeight: 600 }}>{v}</span> },
    { title: '来源', dataIndex: 'source', width: 220, render: (v: string) => v || '-' },
    { title: '挂载（容器 → 路径）', key: 'mounts', render: (_: unknown, r: VolumeInfo) => {
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
  ]

  const renderSection = (t: SourceType) => {
    const vols = volumesOf(t)
    const envs = t === 'pvc' ? [] : envRefsOf(t)
    return (
      <div key={t} style={{ marginBottom: 8 }}>
        <Space style={{ marginBottom: 8 }} wrap>
          <Typography.Text strong style={{ fontSize: 14 }}>{TYPE_LABEL[t]}</Typography.Text>
          <Button size="small" type="primary" icon={<PlusOutlined />} onClick={() => openAdd(t)}>添加{TYPE_LABEL[t]}</Button>
        </Space>
        <Table rowKey="name" size="small" pagination={false} dataSource={vols} columns={volumeColumns}
          locale={{ emptyText: `未挂载 ${TYPE_LABEL[t]}（卷）` }} />
        {t !== 'pvc' && (
          <>
            <Typography.Text type="secondary" style={{ fontSize: 12, display: 'inline-block', margin: '10px 0 4px' }}>环境变量注入（envFrom）</Typography.Text>
            <Table rowKey={(r) => `${r.container}-${r.source_name}`} size="small" pagination={false} dataSource={envs}
              columns={[
                { title: '容器', dataIndex: 'container', width: 200 },
                { title: '来源', dataIndex: 'source_name' },
                { title: '前缀', dataIndex: 'prefix', width: 160, render: (v: string) => v || '-' },
                { title: '操作', width: 90, render: (_: unknown, r: EnvRef) => (
                  <Popconfirm title={`移除 ${r.source_name} 的环境变量注入？`} description="会触发 Pod 滚动更新" okButtonProps={{ danger: true }} onConfirm={() => doRemoveEnv(r.source_name, r.container)}>
                    <Button size="small" type="link" danger icon={<DeleteOutlined />}>移除</Button>
                  </Popconfirm>
                ) },
              ]}
              locale={{ emptyText: `未注入 ${TYPE_LABEL[t]} 环境变量` }} />
          </>
        )}
      </div>
    )
  }

  return (
    <div>
      <Space style={{ marginBottom: 10 }}>
        <Button size="small" icon={<ReloadOutlined />} onClick={load}>刷新</Button>
      </Space>
      <Alert type="info" showIcon style={{ marginBottom: 12 }}
        message="挂载为数据卷（volumes + volumeMounts，需填容器内路径）；或将配置字典/密钥注入为环境变量（envFrom）。均写入该工作负载 Pod 模板并触发滚动更新。" />
      {loading ? (
        <div style={{ textAlign: 'center', padding: '30px 0' }}><Spin /></div>
      ) : !data ? (
        <Empty description="配置获取失败" />
      ) : (
        types.map((t, i) => (
          <div key={t}>
            {i > 0 && <Divider style={{ margin: '8px 0 14px' }} />}
            {renderSection(t)}
          </div>
        ))
      )}

      <Modal title={addType ? `添加${TYPE_LABEL[addType]}` : ''}
        open={!!addType} onCancel={() => setAddType(null)} onOk={submitAdd} confirmLoading={submitting} okText="确定" destroyOnClose>
        <Form form={form} layout="vertical" preserve={false}>
          {addType !== 'pvc' && (
            <Form.Item name="mode" label="方式" rules={[{ required: true }]}>
              <Radio.Group>
                <Radio.Button value="volume">挂载为数据卷</Radio.Button>
                <Radio.Button value="env">注入环境变量</Radio.Button>
              </Radio.Group>
            </Form.Item>
          )}
          <Form.Item name="source_name" label={addType ? TYPE_LABEL[addType] : '资源'} rules={[{ required: true, message: '请选择' }]}>
            <Select showSearch placeholder={addType ? `选择命名空间 ${namespace} 下的${TYPE_LABEL[addType]}` : ''}
              options={addType ? optionsFor(addType) : []} optionFilterProp="label"
              notFoundContent="该命名空间下没有可选资源" />
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(p, c) => p.mode !== c.mode}>
            {({ getFieldValue }) => {
              const mode = addType === 'pvc' ? 'volume' : getFieldValue('mode')
              const isEnv = mode === 'env'
              return (
                <>
                  {!isEnv && (
                    <>
                      <Form.Item name="mount_path" label="挂载路径（容器内）" rules={[{ required: true, message: '请填写容器内路径' }, { pattern: /^\//, message: '必须是绝对路径（以 / 开头）' }]}>
                        <Input placeholder={addType === 'pvc' ? '/data' : '/etc/config'} />
                      </Form.Item>
                      <Form.Item name="sub_path" label="文件名 / 子路径（可选）" tooltip="挂载单个文件时填该文件在来源里的 key/文件名（会以同名文件挂到上面的路径下）；留空则整体挂载为目录">
                        <Input placeholder="如 application.yml（挂单文件时填）" />
                      </Form.Item>
                      <Form.Item name="volume_name" label="卷名（可选）" tooltip="留空自动按来源名生成；重复会自动加序号">
                        <Input placeholder="自动生成" />
                      </Form.Item>
                    </>
                  )}
                  <Form.Item name="containers" label="挂到哪些容器" tooltip="留空=全部业务容器">
                    <Select mode="multiple" allowClear placeholder="默认全部容器" options={containerNames.map((n) => ({ value: n, label: n }))} />
                  </Form.Item>
                  {!isEnv && (
                    <Form.Item name="read_only" label="只读" valuePropName="checked">
                      <Switch />
                    </Form.Item>
                  )}
                </>
              )
            }}
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
