// 主机/虚拟机纳管（N11）：非 K8s 服务器，按「进程路径」监控服务（SSH 采集，无 agent）
import { useEffect, useState } from 'react'
import { Button, Card, Form, Input, InputNumber, Modal, Popconfirm, Select, Space, Switch, Table, Tag, message } from 'antd'
import { PlusOutlined, ReloadOutlined, SyncOutlined } from '@ant-design/icons'
import { getHosts, createHost, updateHost, deleteHost, checkHost } from '../services/api'
import type { HostItem, HostCheckResult } from '../services/api'

const STATUS: Record<string, { color: string; label: string }> = {
  online: { color: 'green', label: '在线' }, offline: { color: 'red', label: '离线' }, unknown: { color: 'default', label: '未知' },
}

export default function HostManagement() {
  const [hosts, setHosts] = useState<HostItem[]>([])
  const [loading, setLoading] = useState(false)
  const [modal, setModal] = useState(false)
  const [editing, setEditing] = useState<HostItem | null>(null)
  const [checking, setChecking] = useState<number | null>(null)
  const [checkResult, setCheckResult] = useState<HostCheckResult | null>(null)
  const [form] = Form.useForm()

  const load = () => { setLoading(true); getHosts().then((d) => setHosts(d || [])).catch(() => {}).finally(() => setLoading(false)) }
  useEffect(load, [])

  const openCreate = () => { setEditing(null); form.resetFields(); form.setFieldsValue({ ssh_port: 22, ssh_user: 'root', enabled: true, process_paths: [] }); setModal(true) }
  const openEdit = (h: HostItem) => {
    setEditing(h); form.resetFields()
    form.setFieldsValue({ ...h, process_paths: h.process_paths || [] })
    setModal(true)
  }

  const save = async () => {
    const v = await form.validateFields()
    const payload = { ...v, process_paths: (v.process_paths || []).map((p: { name?: string; path?: string }) => ({ name: (p.name || p.path || '').trim(), path: (p.path || '').trim() })).filter((p: { path: string }) => p.path) }
    if (editing) { await updateHost(editing.id, payload); message.success('已更新') }
    else { await createHost(payload); message.success('已添加') }
    setModal(false); load()
  }

  const doCheck = async (id: number) => {
    setChecking(id)
    try { const r = await checkHost(id); setCheckResult(r.check) } catch { /* */ } finally { setChecking(null) }
  }

  const del = async (id: number) => { await deleteHost(id); message.success('已删除'); load() }

  return (
    <Card size="small" title="主机 / 虚拟机（非集群节点）"
      extra={<Space><Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>添加主机</Button></Space>}>
      <div style={{ color: '#888', fontSize: 13, marginBottom: 10 }}>
        纳管非 K8s 的服务器：SSH 采集进程，按「进程路径」监控服务存活/CPU/内存；进程掉线/恢复会自动通知。每 60 秒自动检测一次。
      </div>
      <Table rowKey="id" size="small" loading={loading} dataSource={hosts} pagination={false}
        columns={[
          { title: '名称', dataIndex: 'name' },
          { title: '地址', dataIndex: 'address', render: (v, r) => `${v}:${r.ssh_port}` },
          { title: '用户', dataIndex: 'ssh_user', width: 80 },
          { title: '状态', dataIndex: 'status', width: 80, render: (s: string) => <Tag color={STATUS[s]?.color}>{STATUS[s]?.label || s}</Tag> },
          { title: '监控进程', width: 260, render: (_, r) => (r.process_paths || []).map((p) => p.name || p.path).join('、') || '-' },
          { title: '启用', dataIndex: 'enabled', width: 70, render: (v: boolean) => v ? <Tag color="green">是</Tag> : <Tag>否</Tag> },
          { title: '最近检测', dataIndex: 'last_seen', width: 160, render: (v: string | null) => v ? String(v).slice(0, 19).replace('T', ' ') : '-' },
          { title: '操作', width: 180, render: (_, r) => (
            <Space size="small">
              <Button size="small" type="link" loading={checking === r.id} icon={<SyncOutlined />} onClick={() => doCheck(r.id)}>检测</Button>
              <Button size="small" type="link" onClick={() => openEdit(r)}>编辑</Button>
              <Popconfirm title="删除该主机？" onConfirm={() => del(r.id)}><a style={{ color: '#cf1322' }}>删除</a></Popconfirm>
            </Space>
          ) },
        ]} />

      {checkResult && (
        <Card size="small" style={{ marginTop: 12 }} title={`检测结果${checkResult.online ? '' : '（SSH 失败）'}`}>
          {checkResult.error && <div style={{ color: '#cf1322' }}>{checkResult.error}</div>}
          <Table rowKey="path" size="small" pagination={false} dataSource={checkResult.paths}
            columns={[
              { title: '进程', dataIndex: 'name' },
              { title: '路径', dataIndex: 'path' },
              { title: '状态', dataIndex: 'running', render: (v: boolean) => <Tag color={v ? 'green' : 'red'}>{v ? '运行中' : '已停止'}</Tag> },
              { title: 'PID 数', dataIndex: 'pid_count', width: 80 },
              { title: 'CPU%', dataIndex: 'cpu_pct', width: 80 },
              { title: '内存(MB)', dataIndex: 'mem_mb', width: 100 },
            ]} />
        </Card>
      )}

      <Modal title={editing ? '编辑主机' : '添加主机'} open={modal} onOk={save} onCancel={() => setModal(false)} width={620} destroyOnClose>
        <Form form={form} layout="vertical">
          <Space.Compact block style={{ width: '100%' }}>
            <Form.Item name="name" label="名称" style={{ width: '40%' }} rules={[{ required: true }]}><Input placeholder="如 db-192.168.6.99" /></Form.Item>
            <Form.Item name="address" label="IP / 主机名" style={{ width: '40%' }} rules={[{ required: true }]}><Input placeholder="192.168.6.99" /></Form.Item>
            <Form.Item name="ssh_port" label="端口" style={{ width: '20%' }}><InputNumber min={1} max={65535} style={{ width: '100%' }} /></Form.Item>
          </Space.Compact>
          <Space.Compact block style={{ width: '100%' }}>
            <Form.Item name="ssh_user" label="SSH 用户" style={{ width: '50%' }}><Input /></Form.Item>
            <Form.Item name="ssh_password" label="SSH 口令（可选，留空=用密钥）" style={{ width: '50%' }}><Input.Password placeholder={editing?.has_password ? '已设置（留空不变）' : ''} /></Form.Item>
          </Space.Compact>
          <Form.Item name="ssh_key_path" label="私钥路径（可选，留空=默认 ~/.ssh/id_rsa）"><Input /></Form.Item>
          <Form.Item name="enabled" label="启用监控" valuePropName="checked"><Switch /></Form.Item>
          <Form.Item label="监控进程路径（一行一个服务：名称 + 命令行匹配路径）">
            <Form.List name="process_paths">
              {(fields, { add, remove }) => (
                <>
                  {fields.map(({ key, name: idx }) => (
                    <Space key={key} style={{ display: 'flex', marginBottom: 4 }} align="baseline">
                      <Form.Item name={[idx, 'name']} style={{ marginBottom: 0 }}><Input placeholder="服务名" style={{ width: 140 }} /></Form.Item>
                      <Form.Item name={[idx, 'path']} rules={[{ required: true, message: '填路径' }]} style={{ marginBottom: 0 }}><Input placeholder="/usr/local/app/app.jar 或 mysqld" style={{ width: 300 }} /></Form.Item>
                      <a onClick={() => remove(idx)} style={{ color: '#cf1322' }}>删除</a>
                    </Space>
                  ))}
                  <Button size="small" type="dashed" onClick={() => add()} style={{ marginTop: 4 }}>+ 加一个进程</Button>
                </>
              )}
            </Form.List>
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}
