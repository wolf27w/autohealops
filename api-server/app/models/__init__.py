# -*- coding: utf-8 -*-
"""模型汇总导入：确保 Base.metadata 包含所有表（alembic 自动生成 migration 用）"""
from app.models.agent_session import DiagnosisMessage, DiagnosisSession
from app.models.ai_feedback import AiFeedback
from app.models.ai_model import AICallLog, AIModelConfig
from app.models.im_group import IMGroup
from app.models.in_app_message import InAppMessage
from app.models.resource_alert_rule import ResourceAlertRule
from app.models.db_metric_snapshot import DBMetricSnapshot
from app.models.alert import Alert, AlertComment
from app.models.alert_policy import AlertPolicy
from app.models.approval import Approval
from app.models.approval_flow import ApprovalFlow, DEFAULT_FLOW_NAME, DEFAULT_FLOW_NODES
from app.models.audit import AuditRecord
from app.models.batch import BatchOperation, BatchOperationItem
from app.models.cluster import Cluster
from app.models.custom_task import CustomTask, CustomTaskLog
from app.models.db_connection import DBConnection
from app.models.decision import DecisionPolicy, DecisionResult
from app.models.diagnosis import AIDiagnosisResult
from app.models.diagnostic import DiagnosticReport, DiagnosticTask
from app.models.execution import ExecutionLog
from app.models.knowledge import KnowledgeEntry
from app.models.log_alert_rule import LogAlertRule
from app.models.log_source import LogSource
from app.models.notification import NotificationConfig, NotificationLog
from app.models.on_duty import Holiday, OnDutySchedule
from app.models.duty_override import DutyOverride
from app.models.permission import AlertSubscription, UserClusterPermission, UserDBConnection
from app.models.project import Project, ProjectClusterNS, ProjectDBConnection, UserProjectPermission
from app.models.pod_metric import PodMetric
from app.models.prompt import PromptTemplate, PromptVersion
from app.models.repair_plan import RepairPlan
from app.models.resource import QuotaSnapshot, ResourceSnapshot
from app.models.report_schedule import ReportSchedule
from app.models.self_heal_rule import SelfHealRule
from app.models.slow_sql import SlowSQLRecord
from app.models.stats import AIEffectivenessStat, ModelAccuracyStat
from app.models.system_setting import SystemSetting
from app.models.task import Task, TaskAuditLog
from app.models.user import Role, User
from app.models.verification import RepairVerification

__all__ = [
    "DiagnosisSession",
    "DiagnosisMessage",
    "AiFeedback",
    "Cluster",
    "Alert",
    "AlertComment",
    "AlertPolicy",
    "Task",
    "TaskAuditLog",
    "Approval",
    "ApprovalFlow",
    "DEFAULT_FLOW_NAME",
    "DEFAULT_FLOW_NODES",
    "NotificationConfig",
    "NotificationLog",
    "InAppMessage",
    "IMGroup",
    "ResourceAlertRule",
    "DBMetricSnapshot",
    "Holiday",
    "OnDutySchedule",
    "DutyOverride",
    "UserClusterPermission",
    "UserDBConnection",
    "AlertSubscription",
    "Project",
    "ProjectClusterNS",
    "ProjectDBConnection",
    "UserProjectPermission",
    "PodMetric",
    "DecisionPolicy",
    "DecisionResult",
    "BatchOperation",
    "BatchOperationItem",
    "CustomTask",
    "CustomTaskLog",
    "ResourceSnapshot",
    "QuotaSnapshot",
    "ReportSchedule",
    "SelfHealRule",
    "SlowSQLRecord",
    "KnowledgeEntry",
    "LogAlertRule",
    "LogSource",
    "AuditRecord",
    "User",
    "Role",
    "AIModelConfig",
    "AICallLog",
    "PromptTemplate",
    "PromptVersion",
    "AIDiagnosisResult",
    "DiagnosticTask",
    "DiagnosticReport",
    "DBConnection",
    "RepairPlan",
    "ExecutionLog",
    "RepairVerification",
    "AIEffectivenessStat",
    "ModelAccuracyStat",
    "SystemSetting",
]
