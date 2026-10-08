// 键值对编辑（Form.List）—— 编辑信息(标签/注解) / 编辑设置(数据) 复用
import { Form, Input, Button, Space } from 'antd'
import { MinusCircleOutlined, PlusOutlined } from '@ant-design/icons'

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

export function KeyValueList({ name, addLabel, keyPlaceholder = 'key', valuePlaceholder = 'value' }: {
  name: string | (string | number)[]
  addLabel: string
  keyPlaceholder?: string
  valuePlaceholder?: string
}) {
  return (
    <Form.List name={name}>
      {(fields, { add, remove }) => (
        <>
          {fields.map(({ key, name: n, ...rest }) => (
            <Space key={key} align="baseline" style={{ display: 'flex' }}>
              <Form.Item {...rest} name={[n, 'key']} rules={[{ required: true, message: '必填' }]}>
                <Input placeholder={keyPlaceholder} style={{ width: 220 }} />
              </Form.Item>
              <Form.Item {...rest} name={[n, 'value']}>
                <Input placeholder={valuePlaceholder} style={{ width: 340 }} />
              </Form.Item>
              <MinusCircleOutlined onClick={() => remove(n)} />
            </Space>
          ))}
          <Button type="dashed" onClick={() => add()} icon={<PlusOutlined />}>{addLabel}</Button>
        </>
      )}
    </Form.List>
  )
}
