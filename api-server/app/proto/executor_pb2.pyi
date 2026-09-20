from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class StartWatchRequest(_message.Message):
    __slots__ = ("cluster_id",)
    CLUSTER_ID_FIELD_NUMBER: _ClassVar[int]
    cluster_id: int
    def __init__(self, cluster_id: _Optional[int] = ...) -> None: ...

class StartWatchResponse(_message.Message):
    __slots__ = ("success", "message")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    success: bool
    message: str
    def __init__(self, success: _Optional[bool] = ..., message: _Optional[str] = ...) -> None: ...

class StopWatchRequest(_message.Message):
    __slots__ = ("cluster_id",)
    CLUSTER_ID_FIELD_NUMBER: _ClassVar[int]
    cluster_id: int
    def __init__(self, cluster_id: _Optional[int] = ...) -> None: ...

class StopWatchResponse(_message.Message):
    __slots__ = ("success", "message")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    success: bool
    message: str
    def __init__(self, success: _Optional[bool] = ..., message: _Optional[str] = ...) -> None: ...

class ExecuteCommandRequest(_message.Message):
    __slots__ = ("cluster_id", "namespace", "command", "trace_id")
    CLUSTER_ID_FIELD_NUMBER: _ClassVar[int]
    NAMESPACE_FIELD_NUMBER: _ClassVar[int]
    COMMAND_FIELD_NUMBER: _ClassVar[int]
    TRACE_ID_FIELD_NUMBER: _ClassVar[int]
    cluster_id: int
    namespace: str
    command: str
    trace_id: str
    def __init__(self, cluster_id: _Optional[int] = ..., namespace: _Optional[str] = ..., command: _Optional[str] = ..., trace_id: _Optional[str] = ...) -> None: ...

class ExecuteCommandResponse(_message.Message):
    __slots__ = ("success", "output", "error")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    success: bool
    output: str
    error: str
    def __init__(self, success: _Optional[bool] = ..., output: _Optional[str] = ..., error: _Optional[str] = ...) -> None: ...

class ExecutePlanRequest(_message.Message):
    __slots__ = ("cluster_id", "plan_id", "plan_json", "trace_id")
    CLUSTER_ID_FIELD_NUMBER: _ClassVar[int]
    PLAN_ID_FIELD_NUMBER: _ClassVar[int]
    PLAN_JSON_FIELD_NUMBER: _ClassVar[int]
    TRACE_ID_FIELD_NUMBER: _ClassVar[int]
    cluster_id: int
    plan_id: int
    plan_json: str
    trace_id: str
    def __init__(self, cluster_id: _Optional[int] = ..., plan_id: _Optional[int] = ..., plan_json: _Optional[str] = ..., trace_id: _Optional[str] = ...) -> None: ...

class ExecutePlanResponse(_message.Message):
    __slots__ = ("step_index", "step_command", "success", "output", "message", "finished", "circuit_triggered", "rollback_command")
    STEP_INDEX_FIELD_NUMBER: _ClassVar[int]
    STEP_COMMAND_FIELD_NUMBER: _ClassVar[int]
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    FINISHED_FIELD_NUMBER: _ClassVar[int]
    CIRCUIT_TRIGGERED_FIELD_NUMBER: _ClassVar[int]
    ROLLBACK_COMMAND_FIELD_NUMBER: _ClassVar[int]
    step_index: int
    step_command: str
    success: bool
    output: str
    message: str
    finished: bool
    circuit_triggered: bool
    rollback_command: str
    def __init__(self, step_index: _Optional[int] = ..., step_command: _Optional[str] = ..., success: _Optional[bool] = ..., output: _Optional[str] = ..., message: _Optional[str] = ..., finished: _Optional[bool] = ..., circuit_triggered: _Optional[bool] = ..., rollback_command: _Optional[str] = ...) -> None: ...

class RollbackPlanRequest(_message.Message):
    __slots__ = ("cluster_id", "plan_id", "plan_json", "trace_id")
    CLUSTER_ID_FIELD_NUMBER: _ClassVar[int]
    PLAN_ID_FIELD_NUMBER: _ClassVar[int]
    PLAN_JSON_FIELD_NUMBER: _ClassVar[int]
    TRACE_ID_FIELD_NUMBER: _ClassVar[int]
    cluster_id: int
    plan_id: int
    plan_json: str
    trace_id: str
    def __init__(self, cluster_id: _Optional[int] = ..., plan_id: _Optional[int] = ..., plan_json: _Optional[str] = ..., trace_id: _Optional[str] = ...) -> None: ...

class RollbackPlanResponse(_message.Message):
    __slots__ = ("success", "message", "executed_rollbacks")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    EXECUTED_ROLLBACKS_FIELD_NUMBER: _ClassVar[int]
    success: bool
    message: str
    executed_rollbacks: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, success: _Optional[bool] = ..., message: _Optional[str] = ..., executed_rollbacks: _Optional[_Iterable[str]] = ...) -> None: ...

class HealthCheckRequest(_message.Message):
    __slots__ = ("cluster_id", "trace_id")
    CLUSTER_ID_FIELD_NUMBER: _ClassVar[int]
    TRACE_ID_FIELD_NUMBER: _ClassVar[int]
    cluster_id: int
    trace_id: str
    def __init__(self, cluster_id: _Optional[int] = ..., trace_id: _Optional[str] = ...) -> None: ...

class HealthCheckResponse(_message.Message):
    __slots__ = ("success", "status", "version", "error")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    success: bool
    status: str
    version: str
    error: str
    def __init__(self, success: _Optional[bool] = ..., status: _Optional[str] = ..., version: _Optional[str] = ..., error: _Optional[str] = ...) -> None: ...

class CollectQuotaRequest(_message.Message):
    __slots__ = ("cluster_id", "namespace", "trace_id")
    CLUSTER_ID_FIELD_NUMBER: _ClassVar[int]
    NAMESPACE_FIELD_NUMBER: _ClassVar[int]
    TRACE_ID_FIELD_NUMBER: _ClassVar[int]
    cluster_id: int
    namespace: str
    trace_id: str
    def __init__(self, cluster_id: _Optional[int] = ..., namespace: _Optional[str] = ..., trace_id: _Optional[str] = ...) -> None: ...

class CollectQuotaResponse(_message.Message):
    __slots__ = ("success", "message")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    success: bool
    message: str
    def __init__(self, success: _Optional[bool] = ..., message: _Optional[str] = ...) -> None: ...

class RunScriptRequest(_message.Message):
    __slots__ = ("cluster_id", "script_content", "parameters", "task_id", "trace_id")
    CLUSTER_ID_FIELD_NUMBER: _ClassVar[int]
    SCRIPT_CONTENT_FIELD_NUMBER: _ClassVar[int]
    PARAMETERS_FIELD_NUMBER: _ClassVar[int]
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    TRACE_ID_FIELD_NUMBER: _ClassVar[int]
    cluster_id: int
    script_content: str
    parameters: str
    task_id: int
    trace_id: str
    def __init__(self, cluster_id: _Optional[int] = ..., script_content: _Optional[str] = ..., parameters: _Optional[str] = ..., task_id: _Optional[int] = ..., trace_id: _Optional[str] = ...) -> None: ...

class RunScriptResponse(_message.Message):
    __slots__ = ("success", "output", "error")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    success: bool
    output: str
    error: str
    def __init__(self, success: _Optional[bool] = ..., output: _Optional[str] = ..., error: _Optional[str] = ...) -> None: ...

class BatchExecuteRequest(_message.Message):
    __slots__ = ("cluster_ids", "namespace", "command", "trace_id")
    CLUSTER_IDS_FIELD_NUMBER: _ClassVar[int]
    NAMESPACE_FIELD_NUMBER: _ClassVar[int]
    COMMAND_FIELD_NUMBER: _ClassVar[int]
    TRACE_ID_FIELD_NUMBER: _ClassVar[int]
    cluster_ids: _containers.RepeatedScalarFieldContainer[int]
    namespace: str
    command: str
    trace_id: str
    def __init__(self, cluster_ids: _Optional[_Iterable[int]] = ..., namespace: _Optional[str] = ..., command: _Optional[str] = ..., trace_id: _Optional[str] = ...) -> None: ...

class BatchExecuteResponse(_message.Message):
    __slots__ = ("cluster_id", "success", "output", "error")
    CLUSTER_ID_FIELD_NUMBER: _ClassVar[int]
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    cluster_id: int
    success: bool
    output: str
    error: str
    def __init__(self, cluster_id: _Optional[int] = ..., success: _Optional[bool] = ..., output: _Optional[str] = ..., error: _Optional[str] = ...) -> None: ...

class GetPodStatusRequest(_message.Message):
    __slots__ = ("cluster_id", "namespace", "pod_name")
    CLUSTER_ID_FIELD_NUMBER: _ClassVar[int]
    NAMESPACE_FIELD_NUMBER: _ClassVar[int]
    POD_NAME_FIELD_NUMBER: _ClassVar[int]
    cluster_id: int
    namespace: str
    pod_name: str
    def __init__(self, cluster_id: _Optional[int] = ..., namespace: _Optional[str] = ..., pod_name: _Optional[str] = ...) -> None: ...

class ContainerStatusInfo(_message.Message):
    __slots__ = ("name", "state", "ready", "restart_count", "reason")
    NAME_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    READY_FIELD_NUMBER: _ClassVar[int]
    RESTART_COUNT_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    name: str
    state: str
    ready: bool
    restart_count: int
    reason: str
    def __init__(self, name: _Optional[str] = ..., state: _Optional[str] = ..., ready: _Optional[bool] = ..., restart_count: _Optional[int] = ..., reason: _Optional[str] = ...) -> None: ...

class GetPodStatusResponse(_message.Message):
    __slots__ = ("success", "phase", "ready", "restart_count", "message", "container_statuses")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    PHASE_FIELD_NUMBER: _ClassVar[int]
    READY_FIELD_NUMBER: _ClassVar[int]
    RESTART_COUNT_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    CONTAINER_STATUSES_FIELD_NUMBER: _ClassVar[int]
    success: bool
    phase: str
    ready: bool
    restart_count: int
    message: str
    container_statuses: _containers.RepeatedCompositeFieldContainer[ContainerStatusInfo]
    def __init__(self, success: _Optional[bool] = ..., phase: _Optional[str] = ..., ready: _Optional[bool] = ..., restart_count: _Optional[int] = ..., message: _Optional[str] = ..., container_statuses: _Optional[_Iterable[_Union[ContainerStatusInfo, _Mapping]]] = ...) -> None: ...

class RepairImagePullRequest(_message.Message):
    __slots__ = ("cluster_id", "namespace", "pod_name")
    CLUSTER_ID_FIELD_NUMBER: _ClassVar[int]
    NAMESPACE_FIELD_NUMBER: _ClassVar[int]
    POD_NAME_FIELD_NUMBER: _ClassVar[int]
    cluster_id: int
    namespace: str
    pod_name: str
    def __init__(self, cluster_id: _Optional[int] = ..., namespace: _Optional[str] = ..., pod_name: _Optional[str] = ...) -> None: ...

class RepairImagePullResponse(_message.Message):
    __slots__ = ("success", "message", "fixed")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    FIXED_FIELD_NUMBER: _ClassVar[int]
    success: bool
    message: str
    fixed: bool
    def __init__(self, success: _Optional[bool] = ..., message: _Optional[str] = ..., fixed: _Optional[bool] = ...) -> None: ...

class GetDeploymentStatusRequest(_message.Message):
    __slots__ = ("cluster_id", "namespace", "deployment_name")
    CLUSTER_ID_FIELD_NUMBER: _ClassVar[int]
    NAMESPACE_FIELD_NUMBER: _ClassVar[int]
    DEPLOYMENT_NAME_FIELD_NUMBER: _ClassVar[int]
    cluster_id: int
    namespace: str
    deployment_name: str
    def __init__(self, cluster_id: _Optional[int] = ..., namespace: _Optional[str] = ..., deployment_name: _Optional[str] = ...) -> None: ...

class GetDeploymentStatusResponse(_message.Message):
    __slots__ = ("success", "replicas", "ready_replicas", "ready", "message")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    REPLICAS_FIELD_NUMBER: _ClassVar[int]
    READY_REPLICAS_FIELD_NUMBER: _ClassVar[int]
    READY_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    success: bool
    replicas: int
    ready_replicas: int
    ready: bool
    message: str
    def __init__(self, success: _Optional[bool] = ..., replicas: _Optional[int] = ..., ready_replicas: _Optional[int] = ..., ready: _Optional[bool] = ..., message: _Optional[str] = ...) -> None: ...

class ExecInPodRequest(_message.Message):
    __slots__ = ("cluster_id", "namespace", "pod_name", "container", "command", "trace_id")
    CLUSTER_ID_FIELD_NUMBER: _ClassVar[int]
    NAMESPACE_FIELD_NUMBER: _ClassVar[int]
    POD_NAME_FIELD_NUMBER: _ClassVar[int]
    CONTAINER_FIELD_NUMBER: _ClassVar[int]
    COMMAND_FIELD_NUMBER: _ClassVar[int]
    TRACE_ID_FIELD_NUMBER: _ClassVar[int]
    cluster_id: int
    namespace: str
    pod_name: str
    container: str
    command: str
    trace_id: str
    def __init__(self, cluster_id: _Optional[int] = ..., namespace: _Optional[str] = ..., pod_name: _Optional[str] = ..., container: _Optional[str] = ..., command: _Optional[str] = ..., trace_id: _Optional[str] = ...) -> None: ...

class ExecInPodResponse(_message.Message):
    __slots__ = ("success", "output", "error")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    success: bool
    output: str
    error: str
    def __init__(self, success: _Optional[bool] = ..., output: _Optional[str] = ..., error: _Optional[str] = ...) -> None: ...

class ListDeploymentPodsRequest(_message.Message):
    __slots__ = ("cluster_id", "namespace", "deployment_name")
    CLUSTER_ID_FIELD_NUMBER: _ClassVar[int]
    NAMESPACE_FIELD_NUMBER: _ClassVar[int]
    DEPLOYMENT_NAME_FIELD_NUMBER: _ClassVar[int]
    cluster_id: int
    namespace: str
    deployment_name: str
    def __init__(self, cluster_id: _Optional[int] = ..., namespace: _Optional[str] = ..., deployment_name: _Optional[str] = ...) -> None: ...

class DeploymentPodInfo(_message.Message):
    __slots__ = ("name", "phase", "ready", "restart_count")
    NAME_FIELD_NUMBER: _ClassVar[int]
    PHASE_FIELD_NUMBER: _ClassVar[int]
    READY_FIELD_NUMBER: _ClassVar[int]
    RESTART_COUNT_FIELD_NUMBER: _ClassVar[int]
    name: str
    phase: str
    ready: bool
    restart_count: int
    def __init__(self, name: _Optional[str] = ..., phase: _Optional[str] = ..., ready: _Optional[bool] = ..., restart_count: _Optional[int] = ...) -> None: ...

class ListDeploymentPodsResponse(_message.Message):
    __slots__ = ("success", "pods", "all_ready", "message")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    PODS_FIELD_NUMBER: _ClassVar[int]
    ALL_READY_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    success: bool
    pods: _containers.RepeatedCompositeFieldContainer[DeploymentPodInfo]
    all_ready: bool
    message: str
    def __init__(self, success: _Optional[bool] = ..., pods: _Optional[_Iterable[_Union[DeploymentPodInfo, _Mapping]]] = ..., all_ready: _Optional[bool] = ..., message: _Optional[str] = ...) -> None: ...

class QueryPrometheusRequest(_message.Message):
    __slots__ = ("cluster_id", "promql", "trace_id")
    CLUSTER_ID_FIELD_NUMBER: _ClassVar[int]
    PROMQL_FIELD_NUMBER: _ClassVar[int]
    TRACE_ID_FIELD_NUMBER: _ClassVar[int]
    cluster_id: int
    promql: str
    trace_id: str
    def __init__(self, cluster_id: _Optional[int] = ..., promql: _Optional[str] = ..., trace_id: _Optional[str] = ...) -> None: ...

class QueryPrometheusResponse(_message.Message):
    __slots__ = ("success", "data", "error")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    success: bool
    data: str
    error: str
    def __init__(self, success: _Optional[bool] = ..., data: _Optional[str] = ..., error: _Optional[str] = ...) -> None: ...

class GetPodLogsRequest(_message.Message):
    __slots__ = ("cluster_id", "namespace", "pod_name", "container", "tail_lines", "previous")
    CLUSTER_ID_FIELD_NUMBER: _ClassVar[int]
    NAMESPACE_FIELD_NUMBER: _ClassVar[int]
    POD_NAME_FIELD_NUMBER: _ClassVar[int]
    CONTAINER_FIELD_NUMBER: _ClassVar[int]
    TAIL_LINES_FIELD_NUMBER: _ClassVar[int]
    PREVIOUS_FIELD_NUMBER: _ClassVar[int]
    cluster_id: int
    namespace: str
    pod_name: str
    container: str
    tail_lines: int
    previous: bool
    def __init__(self, cluster_id: _Optional[int] = ..., namespace: _Optional[str] = ..., pod_name: _Optional[str] = ..., container: _Optional[str] = ..., tail_lines: _Optional[int] = ..., previous: _Optional[bool] = ...) -> None: ...

class GetPodLogsResponse(_message.Message):
    __slots__ = ("success", "logs", "error")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    LOGS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    success: bool
    logs: str
    error: str
    def __init__(self, success: _Optional[bool] = ..., logs: _Optional[str] = ..., error: _Optional[str] = ...) -> None: ...

class GetPodYamlRequest(_message.Message):
    __slots__ = ("cluster_id", "namespace", "pod_name")
    CLUSTER_ID_FIELD_NUMBER: _ClassVar[int]
    NAMESPACE_FIELD_NUMBER: _ClassVar[int]
    POD_NAME_FIELD_NUMBER: _ClassVar[int]
    cluster_id: int
    namespace: str
    pod_name: str
    def __init__(self, cluster_id: _Optional[int] = ..., namespace: _Optional[str] = ..., pod_name: _Optional[str] = ...) -> None: ...

class GetPodYamlResponse(_message.Message):
    __slots__ = ("success", "yaml", "error")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    YAML_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    success: bool
    yaml: str
    error: str
    def __init__(self, success: _Optional[bool] = ..., yaml: _Optional[str] = ..., error: _Optional[str] = ...) -> None: ...

class ApplyPodYamlRequest(_message.Message):
    __slots__ = ("cluster_id", "namespace", "yaml_content", "trace_id")
    CLUSTER_ID_FIELD_NUMBER: _ClassVar[int]
    NAMESPACE_FIELD_NUMBER: _ClassVar[int]
    YAML_CONTENT_FIELD_NUMBER: _ClassVar[int]
    TRACE_ID_FIELD_NUMBER: _ClassVar[int]
    cluster_id: int
    namespace: str
    yaml_content: str
    trace_id: str
    def __init__(self, cluster_id: _Optional[int] = ..., namespace: _Optional[str] = ..., yaml_content: _Optional[str] = ..., trace_id: _Optional[str] = ...) -> None: ...

class ApplyPodYamlResponse(_message.Message):
    __slots__ = ("success", "message", "error")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    success: bool
    message: str
    error: str
    def __init__(self, success: _Optional[bool] = ..., message: _Optional[str] = ..., error: _Optional[str] = ...) -> None: ...

class SetupRBACRequest(_message.Message):
    __slots__ = ("cluster_id", "trace_id")
    CLUSTER_ID_FIELD_NUMBER: _ClassVar[int]
    TRACE_ID_FIELD_NUMBER: _ClassVar[int]
    cluster_id: int
    trace_id: str
    def __init__(self, cluster_id: _Optional[int] = ..., trace_id: _Optional[str] = ...) -> None: ...

class SetupRBACResponse(_message.Message):
    __slots__ = ("success", "rbac_capable", "impersonate_capable", "cluster_roles", "sa_created", "error")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    RBAC_CAPABLE_FIELD_NUMBER: _ClassVar[int]
    IMPERSONATE_CAPABLE_FIELD_NUMBER: _ClassVar[int]
    CLUSTER_ROLES_FIELD_NUMBER: _ClassVar[int]
    SA_CREATED_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    success: bool
    rbac_capable: bool
    impersonate_capable: bool
    cluster_roles: _containers.RepeatedScalarFieldContainer[str]
    sa_created: bool
    error: str
    def __init__(self, success: _Optional[bool] = ..., rbac_capable: _Optional[bool] = ..., impersonate_capable: _Optional[bool] = ..., cluster_roles: _Optional[_Iterable[str]] = ..., sa_created: _Optional[bool] = ..., error: _Optional[str] = ...) -> None: ...

class RoleBindingSpec(_message.Message):
    __slots__ = ("namespace", "group", "cluster_role")
    NAMESPACE_FIELD_NUMBER: _ClassVar[int]
    GROUP_FIELD_NUMBER: _ClassVar[int]
    CLUSTER_ROLE_FIELD_NUMBER: _ClassVar[int]
    namespace: str
    group: str
    cluster_role: str
    def __init__(self, namespace: _Optional[str] = ..., group: _Optional[str] = ..., cluster_role: _Optional[str] = ...) -> None: ...

class ReconcileRoleBindingsRequest(_message.Message):
    __slots__ = ("cluster_id", "bindings", "trace_id")
    CLUSTER_ID_FIELD_NUMBER: _ClassVar[int]
    BINDINGS_FIELD_NUMBER: _ClassVar[int]
    TRACE_ID_FIELD_NUMBER: _ClassVar[int]
    cluster_id: int
    bindings: _containers.RepeatedCompositeFieldContainer[RoleBindingSpec]
    trace_id: str
    def __init__(self, cluster_id: _Optional[int] = ..., bindings: _Optional[_Iterable[_Union[RoleBindingSpec, _Mapping]]] = ..., trace_id: _Optional[str] = ...) -> None: ...

class ReconcileRoleBindingsResponse(_message.Message):
    __slots__ = ("success", "created", "updated", "deleted", "error")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    CREATED_FIELD_NUMBER: _ClassVar[int]
    UPDATED_FIELD_NUMBER: _ClassVar[int]
    DELETED_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    success: bool
    created: int
    updated: int
    deleted: int
    error: str
    def __init__(self, success: _Optional[bool] = ..., created: _Optional[int] = ..., updated: _Optional[int] = ..., deleted: _Optional[int] = ..., error: _Optional[str] = ...) -> None: ...
