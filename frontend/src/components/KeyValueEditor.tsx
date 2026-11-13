// 键值对编辑（Form.List）—— 编辑信息(标签/注解) / 编辑设置(数据) 复用
import { useState } from 'react'
import { Form, Input, Button, Space, Modal, message } from 'antd'
import { MinusCircleOutlined, PlusOutlined, ImportOutlined } from '@ant-design/icons'

export interface KvItem { key: string; value: string }

export const toKvList = (m?: Record<string, string>): KvItem[] =>
  Object.entries(m || {}).map(([key, value]) => ({ key, value: value ?? '' }))

export const fromKvList = (list?: KvItem[]): Record<string, string> => {
  const o: Record<string, string> = {}
  for (const it of list || []) {
    if (it && it.key) o[it.key] = it.value ?? ''
  }
  return o
}

// 解析批量粘贴文本：每行 KEY=VALUE，支持 # 注释行与空行；空 key 忽略；value 内允许 =
function parseBulk(text: string): KvItem[] {
  const out: KvItem[] = []
  for (const raw of (text || '').split('\n')) {
    const line = raw.trim()
    if (!line || line.startsWith('#')) continue
    const idx = line.indexOf('=')
    if (idx < 0) continue
    const key = line.slice(0, idx).trim()
    if (!key) continue
    out.push({ key, value: line.slice(idx + 1).trim() })
  }
  return out
}

export function KeyValueList({ name, addLabel, keyPlaceholder = 'key', valuePlaceholder = 'value', textarea = false }: {
  name: string | (string | number)[]
  addLabel: string
  keyPlaceholder?: string
  valuePlaceholder?: string
  textarea?: boolean
}) {
  const [bulkOpen, setBulkOpen] = useState(false)
  const [bulkText, setBulkText] = useState('')

  return (
    <Form.List name={name}>
      {(fields, { add, remove }) => {
        // 追加/替换：替换时先清空整列，再逐个 add（Form.List remove 支持 number[]）
        const applyBulk = (mode: 'append' | 'replace') => {
          const items = parseBulk(bulkText)
          if (items.length === 0) {
            message.info('没有可解析的键值对（每行 KEY=VALUE）')
            return
          }
          if (mode === 'replace') remove(fields.map((f) => f.name))
          items.forEach((it) => add(it))
          setBulkOpen(false)
          setBulkText('')
        }
        return (
          <>
            {fields.map(({ key, name: n, ...rest }) => (
              textarea ? (
                <div key={key} style={{ border: '1px solid #f0f0f0', borderRadius: 6, padding: 8, marginBottom: 8 }}>
                  <Space align="baseline" style={{ display: 'flex', width: '100%' }}>
                    <Form.Item {...rest} name={[n, 'key']} rules={[{ required: true, message: 'key 必填' }]} style={{ marginBottom: 6, flex: '0 0 320px' }}>
                      <Input placeholder={keyPlaceholder} />
                    </Form.Item>
                    <MinusCircleOutlined onClick={() => remove(n)} />
                  </Space>
                  <Form.Item {...rest} name={[n, 'value']} style={{ marginBottom: 0 }}>
                    <Input.TextArea autoSize={{ minRows: 2, maxRows: 24 }} placeholder={valuePlaceholder} style={{ fontFamily: 'monospace', fontSize: 12 }} />
                  </Form.Item>
                </div>
              ) : (
                <Space key={key} align="baseline" style={{ display: 'flex' }}>
                  <Form.Item {...rest} name={[n, 'key']} rules={[{ required: true, message: '必填' }]}>
                    <Input placeholder={keyPlaceholder} style={{ width: 220 }} />
                  </Form.Item>
                  <Form.Item {...rest} name={[n, 'value']}>
                    <Input placeholder={valuePlaceholder} style={{ width: 340 }} />
                  </Form.Item>
                  <MinusCircleOutlined onClick={() => remove(n)} />
                </Space>
              )
            ))}
            <Space>
              <Button type="dashed" onClick={() => add()} icon={<PlusOutlined />}>{addLabel}</Button>
              <Button type="dashed" onClick={() => setBulkOpen(true)} icon={<ImportOutlined />}>批量粘贴</Button>
            </Space>

            <Modal
              title="批量粘贴键值对"
              open={bulkOpen}
              onCancel={() => setBulkOpen(false)}
              width={560}
              footer={[
                <Button key="cancel" onClick={() => setBulkOpen(false)}>取消</Button>,
                <Button key="append" onClick={() => applyBulk('append')}>追加</Button>,
                <Button key="replace" type="primary" onClick={() => applyBulk('replace')}>替换</Button>,
              ]}
            >
              <div style={{ color: '#8c8c8c', fontSize: 13, marginBottom: 8 }}>
                每行一个 <code>KEY=VALUE</code>；支持 <code>#</code> 注释行与空行；空 key 忽略，value 内允许 <code>=</code>。
              </div>
              <Input.TextArea
                rows={8}
                value={bulkText}
                onChange={(e) => setBulkText(e.target.value)}
                placeholder={'KEY1=value1\nKEY2=value2\n# 这是注释'}
                style={{ fontFamily: 'monospace', fontSize: 12 }}
              />
            </Modal>
          </>
        )
      }}
    </Form.List>
  )
}
