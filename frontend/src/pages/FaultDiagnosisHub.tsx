// 故障诊断一页三 Tab：应用 / 集群 / 数据库（R32-P0 菜单三级→两级合并）
import { Tabs } from 'antd'
import { useSearchParams } from 'react-router-dom'
import FaultDiagnosis from './FaultDiagnosis'
import DiagnosticCenter from './DiagnosticCenter'

export default function FaultDiagnosisHub() {
  const [sp, setSp] = useSearchParams()
  const tab = sp.get('tab') || 'app'
  return (
    <div>
      <Tabs
        activeKey={tab}
        onChange={(k) => setSp({ tab: k }, { replace: true })}
        items={[
          { key: 'app', label: '应用故障', children: <FaultDiagnosis category="app" /> },
          { key: 'cluster', label: '集群故障', children: <FaultDiagnosis category="cluster" /> },
          { key: 'db', label: '数据库诊断', children: <DiagnosticCenter /> },
        ]}
      />
    </div>
  )
}
