// 集群内「资源治理」页（B18）：把平台侧三块治理能力（命名空间配额 / 工作负载管控 / 僵尸服务）
// 固定到当前集群上下文（clusterId 由 URL 提供），不再显示集群下拉。
import { useEffect, useState } from 'react'
import { Card, Tabs } from 'antd'
import { getClusters } from '../../services/api'
import type { Cluster } from '../../types'
import { NsLevelGovernance, WorkloadGovernance } from '../ResourceGovernance'
import ZombieServices from '../ZombieServices'

export default function GovernancePage({ clusterId }: { clusterId: number }) {
  const [clusters, setClusters] = useState<Cluster[]>([])

  useEffect(() => {
    getClusters()
      .then((d) => setClusters((d || []).filter((c) => c.id === clusterId)))
      .catch(() => setClusters([]))
  }, [clusterId])

  return (
    <Card size="small" title="资源治理">
      <Tabs
        items={[
          { key: 'ns', label: '命名空间配额', children: <NsLevelGovernance clusters={clusters} clusterId={clusterId} /> },
          { key: 'workload', label: '工作负载管控', children: <WorkloadGovernance clusters={clusters} clusterId={clusterId} hideClusterSelect /> },
          { key: 'zombie', label: '僵尸服务', children: <ZombieServices /> },
        ]}
      />
    </Card>
  )
}
