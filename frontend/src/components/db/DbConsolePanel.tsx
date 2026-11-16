// 数据库详情页「SQL 工作台」Tab：把 DbWorkbench 包装成单实例工作台。
// 通过 hideSidebar 隐藏左侧实例列表，编辑器/对象树铺满；纳管类操作（增删改、采集开关、诊断、测试）
// 不在详情页重复提供，统一提示回资源列表操作，避免操作入口重复。
import { message } from 'antd'
import DbWorkbench, { type ConsoleConn } from '../DbWorkbench'

interface Props {
  conn: ConsoleConn
}

// 详情页不提供纳管入口：相关按钮只提示，不做真实操作
const notify = () => message.info('请在资源列表操作')
const noop = () => {}

export default function DbConsolePanel({ conn }: Props) {
  return (
    <DbWorkbench
      conns={[conn]}
      selectedId={conn.id}
      onSelect={noop}
      onAdd={noop}
      onDiagnose={notify}
      onTest={notify}
      onCollect={notify}
      onEdit={notify}
      onDelete={notify}
      onToggleCollect={notify}
      onToggleInspect={notify}
      hideSidebar
    />
  )
}
