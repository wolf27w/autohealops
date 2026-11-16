// 平台设置 →「Oracle 客户端」：多版本管理（启用/下载/上传/删除）
// 说明：python-oracledb thick 模式在一个进程内只能初始化一次，所以同一时间只启用一个版本，
// 切换后需要重启 api-server 才生效。
import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Empty, Input, Popconfirm, Space, Table, Tag, Tooltip, message } from 'antd'
import { CloudDownloadOutlined, UploadOutlined, CheckCircleOutlined } from '@ant-design/icons'
import {
  activateOracleClient, deleteOracleClient, downloadOracleClient, getOracleClients, uploadOracleClient,
  type OracleCatalogItem, type OracleClientItem, type OracleClientsData,
} from '../../services/oracleClientApi'

const PLATFORM_LABEL: Record<string, string> = {
  'macos-arm64': 'macOS（Apple Silicon）',
  'linux-x64': 'Linux x86_64',
}

export default function OracleClientsPanel() {
  const [data, setData] = useState<OracleClientsData | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState('')
  const [uploadName, setUploadName] = useState('')
  const fileRef = useRef<HTMLInputElement | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    getOracleClients()
      .then((d) => setData(d))
      .catch((e) => message.error((e as Error).message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const doDownload = async (c: OracleCatalogItem) => {
    setBusy(c.id)
    try {
      const r = await downloadOracleClient({ catalog_id: c.id })
      message.success(`已安装 ${r?.name || c.id}（${r?.size_mb ?? '?'} MB）`)
      load()
    } catch (e) {
      message.error((e as Error).message)
    } finally { setBusy('') }
  }

  const doUpload = async () => {
    const f = fileRef.current?.files?.[0]
    if (!f) { message.warning('请选择 zip / tar.gz 包'); return }
    const name = uploadName.trim() || f.name.replace(/\.(zip|tar\.gz|tgz|dmg|pkg)$/i, '')
    setBusy('upload')
    try {
      await uploadOracleClient(name, f)
      message.success(`已安装 ${name}`)
      setUploadName('')
      if (fileRef.current) fileRef.current.value = ''
      load()
    } catch (e) {
      message.error((e as Error).message)
    } finally { setBusy('') }
  }

  const doActivate = async (name: string) => {
    setBusy(`act:${name}`)
    try {
      await activateOracleClient(name)
      message.success('已切换启用版本，重启 api-server 后生效')
      load()
    } catch (e) {
      message.error((e as Error).message)
    } finally { setBusy('') }
  }

  const doDelete = async (name: string) => {
    setBusy(`del:${name}`)
    try {
      await deleteOracleClient(name)
      message.success('已删除')
      load()
    } catch (e) {
      message.error((e as Error).message)
    } finally { setBusy('') }
  }

  const columns = [
    { title: '版本名', dataIndex: 'name', width: 200,
      render: (v: string, r: OracleClientItem) => (
        <Space size={6}>
          <b>{v}</b>
          {r.active && <Tag color="green" icon={<CheckCircleOutlined />}>启用中</Tag>}
        </Space>
      ) },
    { title: '关键库文件', dataIndex: 'libs', render: (v: string[]) => (v?.length ? v.join(', ') : <span style={{ color: '#cf1322' }}>未找到 libclntsh</span>) },
    { title: '大小', dataIndex: 'size_mb', width: 100, render: (v: number) => `${v ?? 0} MB` },
    { title: '目录', dataIndex: 'path', ellipsis: true },
    { title: '安装时间', dataIndex: 'created_at', width: 170 },
    { title: '操作', width: 160, render: (_: unknown, r: OracleClientItem) => (
      <Space size={8}>
        <Button size="small" type={r.active ? 'default' : 'primary'} disabled={r.active || !r.usable}
          loading={busy === `act:${r.name}`} onClick={() => doActivate(r.name)}>
          {r.active ? '已启用' : '启用'}
        </Button>
        <Popconfirm title={`删除 ${r.name}？`} onConfirm={() => doDelete(r.name)} disabled={r.active}>
          <Tooltip title={r.active ? '启用中的版本不能删除' : ''}>
            <Button size="small" danger disabled={r.active} loading={busy === `del:${r.name}`}>删除</Button>
          </Tooltip>
        </Popconfirm>
      </Space>
    ) },
  ]

  const catalogColumns = [
    { title: '版本', dataIndex: 'label' },
    { title: '平台', dataIndex: 'platform', width: 180, render: (v: string) => PLATFORM_LABEL[v] || v },
    { title: '操作', width: 200, render: (_: unknown, c: OracleCatalogItem) => (
      c.manual
        ? <Tooltip title="该版本需 Oracle 账号登录下载，请下载后在本页「上传安装」"><Tag>需手动上传</Tag></Tooltip>
        : <Button size="small" icon={<CloudDownloadOutlined />} loading={busy === c.id} onClick={() => doDownload(c)}>下载安装</Button>
    ) },
  ]

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Alert
        type="info"
        showIcon
        message="Oracle 老版本（11g 等）需要 Instant Client（thick 模式）"
        description={(
          <div style={{ fontSize: 12, lineHeight: 1.8 }}>
            平台同一时间只启用 <b>一个</b> 客户端版本（python-oracledb 限制），切换后需<b>重启 api-server</b> 生效。<br />
            安装目录：{data?.home || '-'}
            {data?.current_lib_dir ? <> · 当前生效：<code>{data.current_lib_dir}</code></> : ' · 当前未启用任何客户端（thin 模式，仅支持 12.1+）'}
          </div>
        )}
      />

      <Card size="small" title="已安装的客户端版本"
        extra={<Button size="small" onClick={load} loading={loading}>刷新</Button>}>
        <Table<OracleClientItem>
          size="small"
          rowKey="name"
          loading={loading}
          dataSource={data?.clients || []}
          columns={columns}
          pagination={false}
          locale={{ emptyText: <Empty description="还没安装任何 Instant Client（可在下方下载或上传）" /> }}
        />
      </Card>

      <Card size="small" title="上传安装（自己下载的 zip / tar.gz）">
        <Space wrap>
          <Input placeholder="版本名（如 19.19-linux-x64）" value={uploadName}
            onChange={(e) => setUploadName(e.target.value)} style={{ width: 260 }} />
          <input ref={fileRef} type="file" accept=".zip,.gz,.tgz,.dmg,.pkg" />
          <Button type="primary" icon={<UploadOutlined />} loading={busy === 'upload'} onClick={doUpload}>上传并安装</Button>
          <span style={{ fontSize: 12, color: '#8c8c8c' }}>支持 zip / tar.gz / dmg / pkg（自动处理顶层目录），≤600MB</span>
        </Space>
      </Card>

      <Card size="small" title="常用版本（一键下载）">
        <Table<OracleCatalogItem>
          size="small"
          rowKey="id"
          dataSource={data?.catalog || []}
          columns={catalogColumns}
          pagination={false}
        />
        <div style={{ fontSize: 12, color: '#8c8c8c', marginTop: 8 }}>
          11.2 / 12.2 需 Oracle 账号登录下载（官方限制），下载后在上一栏上传即可。
        </div>
      </Card>
    </Space>
  )
}
