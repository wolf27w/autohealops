// 批量处理页
import { useEffect, useState } from 'react'
import { Table, Button, Modal, Form, Input, Select, InputNumber, Space, Drawer, message } from 'antd'
import { getBatchList, createBatch, executeBatch, getBatchDetail, getClusters } from '../services/api'
import StatusTag from '../components/StatusTag'
import type { Cluster } from '../types'

interface BatchItem {
  id: number
  name?: string
  strategy?: string
  failure_policy?: string
  status?: string
  success_count?: number
  failed_count?: number
  [key: string]: unknown
}

interface BatchDetail {
  items?: Array<Record<string, unknown>>
}

export default function BatchOperation() {
  const [batches, setBatches] = useState<BatchItem[]>([])
  const [clusters, setClusters] = useState<Cluster[]>([])
  const [modalOpen, setModalOpen] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [detail, setDetail] = useState<BatchDetail | null>(null)
  const [form] = Form.useForm()

  const load = () => {
    getBatchList().then((d) => setBatches(d as BatchItem[]))
    getClusters().then((d) => setClusters(d))
  }
  useEffect(() => { load() }, [])

  const handleCreate = async () => {
    const values = await form.validateFields()
    await createBatch(values)
    message.success('批量操作已创建')
    setModalOpen(false)
    form.resetFields()
    load()
  }

  const handleDetail = async (id: number) => {
    const d = (await getBatchDetail(id)) as BatchDetail
    setDetail(d)
    setDrawerOpen(true)
  }

  return (
    <div>
      <Button type="primary" style={{ marginBottom: 16 }} onClick={() => setModalOpen(true)}>创建批量操作</Button>
      <Table rowKey="id" dataSource={batches}
        columns={[
          { title: '名称', dataIndex: 'name' },
          { title: '策略', dataIndex: 'strategy' },
          { title: '失败策略', dataIndex: 'failure_policy' },
          { title: '状态', dataIndex: 'status', render: (s) => <StatusTag status={s} /> },
          { title: '成功/失败', render: (_, r) => `${r.success_count}/${r.failed_count}` },
          { title: '操作', render: (_, r) => (
            <Space>
              <Button size="small" onClick={() => executeBatch(r.id).then(() => { message.success('已触发'); load() })}>执行</Button>
              <Button size="small" onClick={() => handleDetail(r.id)}>详情</Button>
            </Space>
          )},
        ]} />
      <Modal title="创建批量操作" open={modalOpen} onOk={handleCreate} onCancel={() => setModalOpen(false)}>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="cluster_ids" label="选择集群（多选）" rules={[{ required: true }]}>
            <Select mode="multiple" showSearch optionFilterProp="label" placeholder="选择要批量处理的集群"
              options={clusters.map((c) => ({ value: c.id, label: `${c.name} (ID:${c.id})` }))} />
          </Form.Item>
          <Form.Item name="strategy" label="执行策略" initialValue="parallel">
            <Select options={['parallel', 'rolling', 'batch'].map((s) => ({ value: s, label: s }))} />
          </Form.Item>
          <Form.Item name="failure_policy" label="失败策略" initialValue="continue">
            <Select options={['continue', 'stop', 'rollback'].map((s) => ({ value: s, label: s }))} />
          </Form.Item>
          <Form.Item name="batch_size" label="批次大小" initialValue={5}><InputNumber min={1} /></Form.Item>
        </Form>
      </Modal>
      <Drawer title="批量详情" open={drawerOpen} onClose={() => setDrawerOpen(false)} width={500}>
        {detail && (
          <Table rowKey="cluster_id" dataSource={detail.items} pagination={false}
            columns={[
              { title: '集群', dataIndex: 'cluster_id' },
              { title: '状态', dataIndex: 'status', render: (s) => <StatusTag status={s} /> },
              { title: '错误', dataIndex: 'error' },
            ]} />
        )}
      </Drawer>
    </div>
  )
}
