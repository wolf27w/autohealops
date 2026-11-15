// 路由适配层（R32-P1）：子页面统一接收 clusterId 等 props，这里从 URL 里取。
// 放在单独文件里，避免每个页面自己耦合 useParams（也便于将来换路由形态）。
import { useParams } from 'react-router-dom'
import ClusterOverview from './ClusterOverview'
import NodesPage from './NodesPage'
import WorkloadsList from './WorkloadsList'
import PodsList from './PodsList'
import ServicesList from './ServicesList'
import IngressList from './IngressList'
import StorageConfig from './StorageConfig'
import ClusterInspection from './ClusterInspection'
import ClusterMonitoring from './ClusterMonitoring'
import GovernancePage from './GovernancePage'
import ServiceAccountsPage from './ServiceAccountsPage'

type WlKind = 'deployment' | 'statefulset' | 'daemonset' | 'cronjob'
const KINDS: WlKind[] = ['deployment', 'statefulset', 'daemonset', 'cronjob']

function useClusterId(): number {
  const { id } = useParams()
  return Number(id || 0)
}

export function ClusterOverviewRoute() {
  return <ClusterOverview clusterId={useClusterId()} />
}

export function NodesRoute() {
  return <NodesPage clusterId={useClusterId()} />
}

export function WorkloadsRoute() {
  const { kind } = useParams()
  const k = (KINDS as string[]).includes(kind || '') ? (kind as WlKind) : 'deployment'
  return <WorkloadsList clusterId={useClusterId()} kind={k} />
}

export function PodsRoute() {
  return <PodsList clusterId={useClusterId()} />
}

export function ServicesRoute() {
  return <ServicesList clusterId={useClusterId()} />
}

export function IngressListRoute() {
  return <IngressList clusterId={useClusterId()} />
}

export function StorageRoute() {
  return <StorageConfig clusterId={useClusterId()} initialTab="pvcs" />
}

export function ConfigMapsRoute() {
  return <StorageConfig clusterId={useClusterId()} initialTab="configmaps" />
}

export function SecretsRoute() {
  return <StorageConfig clusterId={useClusterId()} initialTab="secrets" />
}

export function StorageClassesRoute() {
  return <StorageConfig clusterId={useClusterId()} initialTab="storageclasses" />
}

export function ServiceAccountsRoute() {
  return <ServiceAccountsPage clusterId={useClusterId()} />
}

export function ConfigsRoute() {
  return <StorageConfig clusterId={useClusterId()} initialTab="configmaps" />
}

export function InspectionRoute() {
  return <ClusterInspection clusterId={useClusterId()} />
}

export function MonitoringRoute() {
  return <ClusterMonitoring clusterId={useClusterId()} />
}

export function GovernanceRoute() {
  return <GovernancePage clusterId={useClusterId()} />
}
