// Package database 提供 GORM 模型（与 api-server SQLAlchemy 表结构一致）
package database

import (
	"time"
)

// Cluster K8s 集群（与 api-server clusters 表一致）
type Cluster struct {
	ID                  uint   `gorm:"primaryKey" json:"id"`
	Name                string `gorm:"size:100;uniqueIndex;not null" json:"name"`
	Description         string `gorm:"size:500" json:"description"`
	KubeconfigEncrypted string `gorm:"type:text;not null" json:"-"`
	Status              string `gorm:"size:20;default:unknown" json:"status"`
	Version             string `gorm:"size:50" json:"version"`
	// 接入方式（kubeconfig / in-cluster / agent）与 apiserver 地址（幂等键）
	Source       string `gorm:"column:source;size:20;default:kubeconfig" json:"source"`
	APIServerURL string `gorm:"column:api_server_url;size:500;default:''" json:"api_server_url"`
	// 环境标签（qa/release/prod）
	EnvTag            string     `gorm:"column:env_tag;size:20;default:prod" json:"env_tag"`
	LastCheckAt       *time.Time `gorm:"column:last_check_at" json:"last_check_at"`
	LastCheckError    string     `gorm:"column:last_check_error;size:255" json:"last_check_error"`
	MaintenanceWindow string     `gorm:"type:text" json:"maintenance_window"`
	CreatedAt         time.Time  `json:"created_at"`
	UpdatedAt         time.Time  `json:"updated_at"`
}

// Alert 故障告警
type Alert struct {
	ID             uint       `gorm:"primaryKey" json:"id"`
	ClusterID      uint       `gorm:"index;not null" json:"cluster_id"`
	ResourceType   string     `gorm:"size:10;not null" json:"resource_type"`
	ResourceName   string     `gorm:"size:200;not null" json:"resource_name"`
	DeploymentName string     `gorm:"size:200;index" json:"deployment_name"`
	Namespace      string     `gorm:"size:100" json:"namespace"`
	FaultType      string     `gorm:"size:50;not null" json:"fault_type"`
	Severity       string     `gorm:"size:10;not null" json:"severity"`
	Status         string     `gorm:"size:20;index;default:pending" json:"status"`
	Message        string     `gorm:"type:text" json:"message"`
	EventCount     int        `gorm:"default:1" json:"event_count"`
	OccurredAt     *time.Time `json:"occurred_at"`
	DetectedAt     time.Time  `gorm:"index" json:"detected_at"`
	ResolvedAt     *time.Time `json:"resolved_at"`
}

// ExecutionLog 修复执行步骤日志
type ExecutionLog struct {
	ID          uint       `gorm:"primaryKey" json:"id"`
	PlanID      uint       `gorm:"index;not null" json:"plan_id"`
	StepIndex   int        `gorm:"not null" json:"step_index"`
	Command     string     `gorm:"type:text;not null" json:"command"`
	Status      string     `gorm:"size:20;default:running" json:"status"`
	Output      string     `gorm:"type:text" json:"output"`
	StartedAt   time.Time  `json:"started_at"`
	CompletedAt *time.Time `json:"completed_at"`
}

// ResourceSnapshot 资源状态快照
type ResourceSnapshot struct {
	ID             uint       `gorm:"primaryKey" json:"id"`
	ClusterID      uint       `gorm:"index;not null" json:"cluster_id"`
	Namespace      string     `gorm:"size:100;index" json:"namespace"`
	ResourceType   string     `gorm:"size:20;not null" json:"resource_type"`
	ResourceName   string     `gorm:"size:200;not null" json:"resource_name"`
	Status         string     `gorm:"size:50" json:"status"`
	Replicas       int        `json:"replicas"`
	ReadyReplicas  int        `json:"ready_replicas"`
	ContainerCount int        `json:"container_count"`
	ImageVersion   string     `gorm:"size:200" json:"image_version"`
	CPUUsage       string     `gorm:"size:20" json:"cpu_usage"`
	MemoryUsage    string     `gorm:"size:20" json:"memory_usage"`
	CPURequest     string     `gorm:"size:20" json:"cpu_request"`
	CPULimit       string     `gorm:"size:20" json:"cpu_limit"`
	MemoryRequest  string     `gorm:"size:20" json:"memory_request"`
	MemoryLimit    string     `gorm:"size:20" json:"memory_limit"`
	UpdateTime     *time.Time `json:"update_time"` // 工作负载最后更新时间（k8s）
	CreatedAt      time.Time  `json:"created_at"`
	SnapshotTime   time.Time  `gorm:"index" json:"snapshot_time"`
}

// QuotaSnapshot 命名空间配额快照
type QuotaSnapshot struct {
	ID           uint      `gorm:"primaryKey" json:"id"`
	ClusterID    uint      `gorm:"index;not null" json:"cluster_id"`
	Namespace    string    `gorm:"size:100;index" json:"namespace"`
	CPULimit     string    `gorm:"size:20" json:"cpu_limit"`
	CPUUsed      string    `gorm:"size:20" json:"cpu_used"`
	MemoryLimit  string    `gorm:"size:20" json:"memory_limit"`
	MemoryUsed   string    `gorm:"size:20" json:"memory_used"`
	PodLimit     int       `json:"pod_limit"`
	PodUsed      int       `json:"pod_used"`
	PVCLimit     int       `json:"pvc_limit"`
	PVCUsed      int       `json:"pvc_used"`
	ServiceLimit int       `json:"service_limit"`
	ServiceUsed  int       `json:"service_used"`
	SnapshotTime time.Time `gorm:"index" json:"snapshot_time"`
}

// CustomTask 定时任务
type CustomTask struct {
	ID              uint       `gorm:"primaryKey" json:"id"`
	ClusterID       uint       `gorm:"index;not null" json:"cluster_id"`
	Name            string     `gorm:"size:100;not null" json:"name"`
	TaskType        string     `gorm:"size:20;not null" json:"task_type"`
	TargetResource  string     `gorm:"size:200" json:"target_resource"`
	TargetNamespace string     `gorm:"size:100" json:"target_namespace"`
	CronExpression  string     `gorm:"size:50;not null" json:"cron_expression"`
	ScriptContent   string     `gorm:"type:text" json:"script_content"`
	Parameters      string     `gorm:"type:text" json:"parameters"`
	IsEnabled       bool       `gorm:"default:true" json:"is_enabled"`
	LastRunAt       *time.Time `json:"last_run_at"`
	LastRunStatus   string     `gorm:"size:20" json:"last_run_status"`
	CreatedAt       time.Time  `json:"created_at"`
	UpdatedAt       time.Time  `json:"updated_at"`
}

// CustomTaskLog 定时任务执行日志
type CustomTaskLog struct {
	ID           uint       `gorm:"primaryKey" json:"id"`
	TaskID       uint       `gorm:"index;not null" json:"task_id"`
	ClusterID    uint       `gorm:"not null" json:"cluster_id"`
	StartTime    time.Time  `json:"start_time"`
	EndTime      *time.Time `json:"end_time"`
	Status       string     `gorm:"size:20;default:running" json:"status"`
	Output       string     `gorm:"type:text" json:"output"`
	ErrorMessage string     `gorm:"type:text" json:"error_message"`
}

// PodMetric Pod 指标采样（每 1 分钟全量采样，保留 7 天自动清理）
type PodMetric struct {
	ID          uint64    `gorm:"primaryKey" json:"id"`
	ClusterID   uint      `gorm:"index;not null" json:"cluster_id"`
	Namespace   string    `gorm:"size:100;index" json:"namespace"`
	PodName     string    `gorm:"size:200;index" json:"pod_name"`
	CPUUsage    float64   `json:"cpu_usage"`    // 单位：core
	MemoryUsage int64     `json:"memory_usage"` // 单位：byte
	NetworkRx   int64     `json:"network_rx"`   // 单位：byte（累计）
	NetworkTx   int64     `json:"network_tx"`   // 单位：byte（累计）
	SampledAt   time.Time `gorm:"index" json:"sampled_at"`
}

// K8sEvent k8s 事件历史（平台自存）：k8s 默认 event-ttl 只有 1 小时，重启/过期后事件就没了，
// 这里每 60s 采集一次并 upsert，保留 7 天，供「容器历史事件」查询。
type K8sEvent struct {
	ID           uint64     `gorm:"primaryKey" json:"id"`
	ClusterID    uint       `gorm:"uniqueIndex:uk_k8s_event;index" json:"cluster_id"`
	UID          string     `gorm:"size:64;uniqueIndex:uk_k8s_event" json:"uid"`
	Namespace    string     `gorm:"size:100;index" json:"namespace"`
	ResourceKind string     `gorm:"size:40;index" json:"resource_kind"`
	ResourceName string     `gorm:"size:200;index" json:"resource_name"`
	// 事件所属工作负载（Pod 事件经 ownerReferences 解析）
	OwnerKind    string     `gorm:"size:40;index" json:"owner_kind"`
	OwnerName    string     `gorm:"size:200;index" json:"owner_name"`
	Reason       string     `gorm:"size:100" json:"reason"`
	Type         string     `gorm:"size:20" json:"type"`
	Message      string     `gorm:"type:text" json:"message"`
	Count        int32      `json:"count"`
	FirstSeen    *time.Time `json:"first_seen"`
	LastSeen     *time.Time `gorm:"index" json:"last_seen"`
	CreatedAt    time.Time  `json:"created_at"`
}

// AlertPolicy 告警策略（与 api-server alert_policies 表一致）
type AlertPolicy struct {
	ID                uint   `gorm:"primaryKey" json:"id"`
	FaultType         string `gorm:"size:50;uniqueIndex;not null" json:"fault_type"`
	Category          string `gorm:"size:1;default:A" json:"category"`
	ObserveSeconds    int    `gorm:"default:60" json:"observe_seconds"`
	RestartThreshold  int    `gorm:"default:2" json:"restart_threshold"`
	TimeWindowSeconds int    `gorm:"default:300" json:"time_window_seconds"`
	Severity          string `gorm:"size:10;default:high" json:"severity"`
	Enabled           bool   `gorm:"default:true" json:"enabled"`
}

// TableName 显式指定表名（与 api-server 一致，复数小写）
func (Cluster) TableName() string          { return "clusters" }
func (Alert) TableName() string            { return "alerts" }
func (ExecutionLog) TableName() string     { return "execution_logs" }
func (ResourceSnapshot) TableName() string { return "resource_snapshots" }
func (QuotaSnapshot) TableName() string    { return "quota_snapshots" }
func (CustomTask) TableName() string       { return "custom_tasks" }
func (CustomTaskLog) TableName() string    { return "custom_task_logs" }
func (PodMetric) TableName() string        { return "pod_metrics" }
func (K8sEvent) TableName() string         { return "k8s_events" }
func (AlertPolicy) TableName() string      { return "alert_policies" }
