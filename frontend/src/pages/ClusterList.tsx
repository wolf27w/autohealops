// 集群管理页：列表 + 添加（kubeconfig 上传落库）+ 健康检查 + 删除 + env_tag
import { useEffect, useState } from 'react'
import { Table, Button, Modal, Form, Input, Upload, Space, message, Select, Tag, Switch, Tooltip, Dropdown, Checkbox, Alert, Typography } from 'antd'
import { PlusOutlined, ReloadOutlined, MoreOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { getClusters, createCluster, updateCluster, deleteCluster, healthCheckCluster, replaceClusterKubeconfig, parseClusterKubeconfig, importMultiClusters, agentOnboardCluster, getAgentImage, agentUpgrade, agentsBatchUpgrade } from '../services/api'
import StatusTag from '../components/StatusTag'
import type { Cluster } from '../types'

const ENV_TAG_COLOR: Record<string, string> = { qa: 'green', release: 'orange', prod: 'red' }
const SOURCE_LABEL: Record<string, string> = { kubeconfig: 'kubeconfig', 'in-cluster': '集群内', agent: 'Agent' }
const SOURCE_COLOR: Record<string, string> = { kubeconfig: 'default', 'in-cluster': 'blue', agent: 'cyan' }

type ClusterWithExtra = Cluster & {
  env_tag?: string
  source?: string
  cred_type?: string
  cred_expires_at?: string
  agent_status?: string
  agent_last_seen_at?: string
  agent_version?: string
  description?: string
  rbac_status?: string
  version?: string
  inspect_cron?: string
}

const RBAC_MODE_LABEL: Record<string, string> = {
  off: '未接入', rbac: '云原生 RBAC', fallback: '平台权限+白名单',
}
const RBAC_MODE_COLOR: Record<string, string> = {
  off: 'default', rbac: 'green', fallback: 'orange',
}

export default function ClusterList() {
  const navigate = useNavigate()
  const [clusters, setClusters] = useState<ClusterWithExtra[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<ClusterWithExtra | null>(null)
  const [form] = Form.useForm()
  const [kubeconfig, setKubeconfig] = useState('')
  const [newKubeconfig, setNewKubeconfig] = useState('')
  const [batchOpen, setBatchOpen] = useState(false)
  const [batchKc, setBatchKc] = useState('')
  const [batchCtxs, setBatchCtxs] = useState<{ name: string; server: string }[]>([])
  const [batchSel, setBatchSel] = useState<string[]>([])
  const [batchEnv, setBatchEnv] = useState('prod')
  const [batchBusy, setBatchBusy] = useState(false)
  const [agentOpen, setAgentOpen] = useState(false)
  const [agentName, setAgentName] = useState('')
  const [agentEnv, setAgentEnv] = useState('prod')
  const [agentResult, setAgentResult] = useState<{ id: number; token: string; install_command: string } | null>(null)
  // Agent 升级 / 回退（2026-11-04）
  const [agentUpOpen, setAgentUpOpen] = useState(false)
  const [agentUpTarget, setAgentUpTarget] = useState<ClusterWithExtra | null>(null)
  const [agentUpImage, setAgentUpImage] = useState('')
  const [agentUpIds, setAgentUpIds] = useState<number[]>([])
  const [agentUpBusy, setAgentUpBusy] = useState(false)
  const [agentUpResult, setAgentUpResult] = useState<string>('')
  const [agentBusy, setAgentBusy] = useState(false)

  const load = () => {
    setLoading(true)
    getClusters().then((d) => setClusters(d)).finally(() => setLoading(false))
  }

  useEffect(load, [])

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({ env_tag: 'prod', inspect_enabled: true, inspect_cron: '' })
    setKubeconfig('')
    setNewKubeconfig('')
    setModalOpen(true)
  }

  const openEdit = (c: ClusterWithExtra) => {
    setEditing(c)
    let mw: { enabled: boolean; start: string; end: string; days: string } = { enabled: false, start: '02:00', end: '04:00', days: '1,2,3,4,5' }
    try { mw = JSON.parse(c.maintenance_window || '{}') } catch { mw = { enabled: false, start: '02:00', end: '04:00', days: '1,2,3,4,5' } }
    form.setFieldsValue({ description: c.description, env_tag: c.env_tag, mw_enabled: mw.enabled, mw_start: mw.start, mw_end: mw.end, mw_days: mw.days, inspect_enabled: c.inspect_enabled !== false, inspect_cron: c.inspect_cron || '' })
    setModalOpen(true)
  }

  const handleSubmit = async () => {
    const values = await form.validateFields()
    if (editing) {
      const mw = {
        enabled: values.mw_enabled ?? false,
        start: values.mw_start || '02:00',
        end: values.mw_end || '04:00',
        days: values.mw_days || '1,2,3,4,5',
      }
      await updateCluster(editing.id, { description: values.description, env_tag: values.env_tag, maintenance_window: mw, inspect_enabled: values.inspect_enabled ?? true, inspect_cron: values.inspect_cron || '' })
      if (newKubeconfig.trim()) {
        await replaceClusterKubeconfig(editing.id, newKubeconfig.trim())
        message.success('已更新（凭据已替换）')
      } else {
        message.success('已更新')
      }
    } else {
      await createCluster({ ...values, kubeconfig })
      message.success('集群已添加')
    }
    setModalOpen(false)
    form.resetFields()
    setKubeconfig('')
    setNewKubeconfig('')
    load()
  }

  const handleHealthCheck = async (id: number) => {
    await healthCheckCluster(id)
    message.success('健康检查完成')
    load()
  }

  const handleAgentOnboard = async () => {
    if (!agentName.trim()) { message.warning('请填集群名称'); return }
    setAgentBusy(true)
    try {
      const d = await agentOnboardCluster({ name: agentName.trim(), env_tag: agentEnv }) as { id: number; token: string; install_command: string }
      setAgentResult(d)
      load()
    } catch { /* 拦截器已提示 */ } finally { setAgentBusy(false) }
  }

  const handleParseKc = async () => {
    if (!batchKc.trim()) { message.warning('请先粘贴 kubeconfig'); return }
    setBatchBusy(true)
    try {
      const d = await parseClusterKubeconfig(batchKc.trim()) as { contexts?: { name: string; server: string }[] }
      setBatchCtxs(d.contexts || [])
      setBatchSel([])
      if (!(d.contexts || []).length) message.info('未解析到任何 context')
    } catch { /* 拦截器已提示 */ } finally { setBatchBusy(false) }
  }

  const handleImportMulti = async () => {
    if (batchSel.length === 0) { message.warning('请勾选要接入的 context'); return }
    setBatchBusy(true)
    try {
      const d = await importMultiClusters({ kubeconfig: batchKc.trim(), contexts: batchSel, env_tag: batchEnv }) as { created?: unknown[]; skipped?: unknown[] }
      const created = (d.created || []).length
      const skipped = (d.skipped || []).length
      message.success(`已接入 ${created} 个${skipped ? `，跳过 ${skipped} 个（已接入）` : ''}`)
      setBatchOpen(false)
      setBatchKc(''); setBatchCtxs([]); setBatchSel([])
      load()
    } catch { /* 拦截器已提示 */ } finally { setBatchBusy(false) }
  }

  const openAgentUpgrade = (target: ClusterWithExtra | null) => {
    setAgentUpTarget(target)
    setAgentUpIds(target ? [Number(target.id)] : [])
    setAgentUpResult('')
    setAgentUpOpen(true)
    getAgentImage().then((d) => setAgentUpImage(d?.image || '')).catch(() => {})
  }

  const submitAgentUpgrade = async () => {
    const ids = agentUpTarget ? [Number(agentUpTarget.id)] : agentUpIds
    if (!ids.length) { message.warning('请选择要升级的集群'); return }
    setAgentUpBusy(true)
    setAgentUpResult('升级中：会滚动重启 agent Pod 并等待其以新版本重连，最长约 90 秒…')
    try {
      if (agentUpTarget) {
        const r = await agentUpgrade(Number(agentUpTarget.id), { image: agentUpImage || undefined, wait: 90 })
        setAgentUpResult(`集群 ${agentUpTarget.name}：${r.previous_version || '-'} → ${r.new_version || '(未重连)'}\n镜像：${r.image}${r.error ? `\n错误：${r.error}` : ''}`)
        if (r.ok) message.success('升级完成')
      } else {
        const r = await agentsBatchUpgrade({ cluster_ids: ids, image: agentUpImage || undefined, wait: 90 })
        setAgentUpResult(`成功 ${r.ok}/${r.total}：\n` + r.results.map((x) => {
          const nm = clusters.find((c) => Number(c.id) === x.cluster_id)?.name || x.cluster_id
          return `${nm}：${x.previous_version || '-'} → ${x.new_version || '(未重连)'}${x.error ? `（${x.error}）` : ''}`
        }).join('\n'))
        message.success(`完成：成功 ${r.ok}/${r.total}`)
      }
      load()
    } catch { /* 拦截器已提示 */ } finally { setAgentUpBusy(false) }
  }

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>添加集群</Button>
        <Button onClick={() => { setBatchOpen(true); setBatchKc(''); setBatchCtxs([]); setBatchSel([]); setBatchEnv('prod') }}>批量接入（多 context）</Button>
        <Button onClick={() => { setAgentOpen(true); setAgentName(''); setAgentEnv('prod'); setAgentResult(null) }}>Agent 接入</Button>
        <Button onClick={() => openAgentUpgrade(null)}>Agent 升级 / 回退</Button>
        <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
      </Space>
      <Table
        rowKey="id"
        loading={loading}
        dataSource={clusters}
        columns={[
          { title: '名称', dataIndex: 'name', render: (name, record) => (
            <Tooltip title={record.description || undefined}>
              <a onClick={() => navigate(`/clusters/${record.id}`)}>{name}</a>
            </Tooltip>
          ) },
          { title: '环境', dataIndex: 'env_tag', render: (t) => <Tag color={ENV_TAG_COLOR[t] || 'default'}>{t}</Tag> },
          { title: '接入方式', dataIndex: 'source', render: (s) => <Tag color={SOURCE_COLOR[s] || 'default'}>{SOURCE_LABEL[s] || s || 'kubeconfig'}</Tag> },
          { title: 'RBAC', dataIndex: 'rbac_mode', render: (m, record) => (
            <span title={record.rbac_status}>
              <Tag color={RBAC_MODE_COLOR[m] || 'default'}>{RBAC_MODE_LABEL[m] || m || '未接入'}</Tag>
            </span>
          ) },
          { title: '状态', dataIndex: 'status', render: (s, record: ClusterWithExtra) => (
            <Tooltip title={record.last_check_at ? `上次检查：${String(record.last_check_at).slice(0, 19)}${record.last_check_error ? `\n错误：${record.last_check_error}` : ''}` : '尚未检查'}>
              <span><StatusTag status={s} /></span>
            </Tooltip>
          ) },
          { title: '版本', dataIndex: 'version' },
          { title: '凭据有效期', dataIndex: 'cred_expires_at', render: (v, r: ClusterWithExtra) => {
            if (r.source === 'in-cluster' || r.source === 'agent') return <Tag color="blue">免维护</Tag>
            if (r.cred_type === 'unknown') {
              return <Tooltip title="kubeconfig 中没有可解析的客户端证书/令牌（或使用了 exec / 云厂商插件，无法离线判断）"><Tag>未知</Tag></Tooltip>
            }
            if (!v) return <Tag color={r.cred_type === 'token' ? 'orange' : 'default'}>{r.cred_type === 'token' ? '长期 token' : '未解析'}</Tag>
            const days = Math.ceil((new Date(String(v).replace(' ', 'T')).getTime() - Date.now()) / 86400000)
            const tip = `${r.cred_type === 'cert' ? '客户端证书' : r.cred_type === 'token' ? 'Token' : 'OIDC'} · 到期 ${String(v).slice(0, 19)}`
            let tag
            if (days < 0) tag = <Tag color="red">已过期</Tag>
            else if (days <= 7) tag = <Tag color="red">{days} 天后过期</Tag>
            else if (days <= 30) tag = <Tag color="orange">{days} 天后过期</Tag>
            else tag = <Tag>{String(v).slice(0, 10)}</Tag>
            return <Tooltip title={tip}>{tag}</Tooltip>
          } },
          { title: 'Agent 状态', dataIndex: 'agent_status', render: (v, r: ClusterWithExtra) => {
            if (r.source !== 'agent') return '-'
            const m: Record<string, { c: string; t: string }> = { pending: { c: 'orange', t: '等待连接' }, connected: { c: 'green', t: '已连接' }, disconnected: { c: 'red', t: '已断开' }, revoked: { c: 'default', t: '已吊销' } }
            const x = m[v] || { c: 'default', t: v || '-' }
            const ver = r.agent_version ? ` v${r.agent_version.replace(/^v/, '')}` : ''
            const tip = r.agent_version ? `agent 版本 ${r.agent_version}` : '尚未上报版本（旧版 agent 或未连接）'
            return <Tooltip title={tip}><Tag color={x.c}>{x.t}{ver}</Tag></Tooltip>
          } },
          { title: '创建时间', dataIndex: 'created_at' },
          {
            title: '操作', width: 70, render: (_, record) => (
              <Dropdown
                trigger={['click']}
                menu={{
                  items: [
                    { key: 'health', label: '健康检查' },
                    { key: 'edit', label: '编辑' },
                    ...(record.source === 'agent' ? [{ key: 'agentUpgrade', label: '升级 / 回退 Agent' }] : []),
                    { type: 'divider' as const },
                    { key: 'delete', label: '删除', danger: true },
                  ],
                  onClick: ({ key }) => {
                    if (key === 'agentUpgrade') openAgentUpgrade(record as ClusterWithExtra)
                    else if (key === 'health') handleHealthCheck(record.id)
                    else if (key === 'edit') openEdit(record)
                    else if (key === 'delete') Modal.confirm({
                      title: `确认删除集群「${record.name}」？`,
                      okButtonProps: { danger: true },
                      onOk: async () => { await deleteCluster(record.id); load() },
                    })
                  },
                }}
              >
                <Button size="small" type="link"><MoreOutlined /></Button>
              </Dropdown>
            ),
          },
        ]}
      />
      <Modal title={editing ? '编辑集群' : '添加集群'} open={modalOpen} onOk={handleSubmit} onCancel={() => setModalOpen(false)}>
        <Form form={form} layout="vertical">
          {!editing && (
            <Form.Item name="name" label="集群名称" rules={[{ required: true }]}><Input /></Form.Item>
          )}
          <Form.Item name="description" label="描述"><Input /></Form.Item>
          <Form.Item name="env_tag" label="环境标签" rules={[{ required: true }]}>
            <Select options={[
              { value: 'qa', label: 'qa（测试）' },
              { value: 'release', label: 'release（预发）' },
              { value: 'prod', label: 'prod（生产）' },
            ]} />
          </Form.Item>
          {editing && (
            <>
              <Form.Item name="inspect_enabled" label="巡检开关" valuePropName="checked" tooltip="关闭后该集群不参与巡检/报告生成"><Switch /></Form.Item>
              <Form.Item name="inspect_cron" label="定时巡检 Cron" tooltip="分 时 日 月 周；如 0 * * * * = 每小时；留空=不定时，仅手动点「巡检」">
                <Input placeholder="留空=不定时，如 0 * * * *" />
              </Form.Item>
            </>
          )}
          {editing && (
            <>
              <Form.Item name="mw_enabled" label="维护窗口（静默告警）" valuePropName="checked"><Switch /></Form.Item>
              <Space>
                <Form.Item name="mw_start" label="开始"><Input placeholder="02:00" style={{ width: 100 }} /></Form.Item>
                <Form.Item name="mw_end" label="结束"><Input placeholder="04:00" style={{ width: 100 }} /></Form.Item>
                <Form.Item name="mw_days" label="星期"><Input placeholder="1,2,3,4,5" style={{ width: 120 }} /></Form.Item>
              </Space>
              <div style={{ fontSize: 12, color: '#999', marginTop: -8 }}>维护窗口内该集群不告警（1-7 表示周一到周日；跨天如 23:00-02:00 支持）。与「通知配置」里的全平台静默窗口任一命中即静默</div>
            </>
          )}
          {editing && (
            <Form.Item label="更新凭据（粘贴新 kubeconfig，可选）" tooltip="证书/Token 过期时在此粘贴新文件替换，不删集群、不丢历史告警/工单；留空=不更新">
              <Input.TextArea rows={4} style={{ fontFamily: 'monospace', fontSize: 12 }} placeholder="粘贴新 kubeconfig（留空=不更新凭据）" onChange={(e) => setNewKubeconfig(e.target.value)} />
            </Form.Item>
          )}
          {!editing && (
            <Form.Item label="kubeconfig（上传文件）" required>
              <Upload
                beforeUpload={(file) => {
                  const reader = new FileReader()
                  reader.onload = () => setKubeconfig(reader.result as string)
                  reader.readAsText(file)
                  return false
                }}
                maxCount={1}
              >
                <Button icon={<PlusOutlined />}>选择 kubeconfig 文件</Button>
              </Upload>
            </Form.Item>
          )}
        </Form>
      </Modal>

      {/* 批量接入（多 context） */}
      <Modal title={agentUpTarget ? `升级 / 回退 Agent · ${agentUpTarget.name}` : 'Agent 批量升级 / 回退'}
        open={agentUpOpen} onCancel={() => setAgentUpOpen(false)} onOk={submitAgentUpgrade}
        okText="开始升级" confirmLoading={agentUpBusy} width={620} destroyOnClose>
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message="升级会滚动重启目标集群的 autohealops-agent Pod（短暂断开），平台等它用新版本重连；回退 = 填旧 tag（例如 .../autohealops-agent:0.1.1）。" />
        <div style={{ marginBottom: 6 }}>目标镜像：</div>
        <Input value={agentUpImage} onChange={(e) => setAgentUpImage(e.target.value)}
          placeholder="harbor.efreight.cn/ops/autohealops-agent:<版本>" />
        {!agentUpTarget && (
          <>
            <div style={{ margin: '12px 0 6px' }}>选择集群（仅 agent 接入方式）：</div>
            {clusters.filter((c) => c.source === 'agent').length === 0
              ? <Alert type="warning" showIcon message="当前没有 agent 接入的集群" />
              : (
                <Checkbox.Group
                  style={{ display: 'flex', flexDirection: 'column', gap: 6 }}
                  value={agentUpIds}
                  onChange={(v) => setAgentUpIds(v as number[])}
                  options={clusters.filter((c) => c.source === 'agent').map((c) => ({
                    value: Number(c.id),
                    label: `${c.name}（${(c as ClusterWithExtra).agent_status || '-'} v${((c as ClusterWithExtra).agent_version || '-').replace(/^v/, '')}）`,
                  }))}
                />
              )}
          </>
        )}
        {agentUpResult && (
          <pre style={{ marginTop: 12, background: '#f6f8fa', padding: 8, borderRadius: 6, fontSize: 12, whiteSpace: 'pre-wrap' }}>{agentUpResult}</pre>
        )}
      </Modal>
      <Modal title="批量接入（kubeconfig 多 context）" open={batchOpen} onCancel={() => setBatchOpen(false)}
        footer={[
          <Button key="cancel" onClick={() => setBatchOpen(false)}>取消</Button>,
          <Button key="import" type="primary" loading={batchBusy} disabled={batchSel.length === 0} onClick={handleImportMulti}>批量接入</Button>,
        ]} width={640}>
        <Form layout="vertical">
          <Form.Item label="kubeconfig（粘贴）" required>
            <Input.TextArea rows={6} style={{ fontFamily: 'monospace', fontSize: 12 }} value={batchKc} onChange={(e) => setBatchKc(e.target.value)} placeholder="粘贴整份 kubeconfig（可含多个 context）" />
          </Form.Item>
          <Space style={{ marginBottom: 8 }}>
            <Button onClick={handleParseKc} loading={batchBusy}>解析 context</Button>
            <Select value={batchEnv} onChange={setBatchEnv} style={{ width: 140 }} options={[
              { value: 'qa', label: 'qa（测试）' }, { value: 'release', label: 'release（预发）' }, { value: 'prod', label: 'prod（生产）' },
            ]} />
          </Space>
          {batchCtxs.length > 0 && (
            <Form.Item label={`选择要接入的 context（共 ${batchCtxs.length} 个）`}>
              <Checkbox.Group value={batchSel} onChange={(v) => setBatchSel(v as string[])} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {batchCtxs.map((c) => (
                  <Checkbox key={c.name} value={c.name}>{c.name}<span style={{ color: '#999', fontSize: 12 }}>　{c.server || ''}</span></Checkbox>
                ))}
              </Checkbox.Group>
            </Form.Item>
          )}
        </Form>
      </Modal>

      {/* Agent 接入向导 */}
      <Modal title="Agent 接入" open={agentOpen} onCancel={() => setAgentOpen(false)}
        footer={null} width={680}>
        {!agentResult ? (
          <Form layout="vertical">
            <Form.Item label="集群名称" required><Input value={agentName} onChange={(e) => setAgentName(e.target.value)} placeholder="如 uat-cluster-a" /></Form.Item>
            <Form.Item label="环境标签">
              <Select value={agentEnv} onChange={setAgentEnv} style={{ width: 180 }} options={[
                { value: 'qa', label: 'qa（测试）' }, { value: 'release', label: 'release（预发）' }, { value: 'prod', label: 'prod（生产）' },
              ]} />
            </Form.Item>
            <Button type="primary" loading={agentBusy} onClick={handleAgentOnboard}>生成接入令牌</Button>
          </Form>
        ) : (
          <div>
            <Alert type="success" showIcon style={{ marginBottom: 12 }} message="接入令牌已生成（令牌只显示这一次，请复制保存）" />
            <Typography.Paragraph copyable code style={{ wordBreak: 'break-all', fontSize: 12 }}>{agentResult.install_command}</Typography.Paragraph>
            <Alert type="info" showIcon style={{ marginTop: 8 }}
              message="在目标集群执行上面命令（kubectl apply）→ agent 主动连回平台 → 列表状态变「已连接」。" />
            <Button style={{ marginTop: 8 }} onClick={() => { setAgentOpen(false); setAgentResult(null) }}>完成</Button>
          </div>
        )}
      </Modal>
    </div>
  )
}
