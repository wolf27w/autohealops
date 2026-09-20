# -*- coding: utf-8 -*-
"""系统设置服务：key-value 配置读写（Prometheus/ES/LDAP 等）"""
import json

from sqlalchemy import select

from app.core.database import async_session
from app.core.security import decrypt_aes256_gcm, encrypt_aes256_gcm
from app.models import SystemSetting

# 脱敏占位符（前端回显 + 更新时跳过）
MASKED = "***"

# ============ 统一告警通知策略（N8）：全平台一份 ============
# 分级渠道（severity → 允许发送的渠道类型）、静默/维护窗口（全局）。
# 渠道类型：dingtalk/wechat/feishu（现有 webhook 渠道）；email/inapp 为预留扩展点，暂未接线。
ALERT_NOTIFY_POLICY_DEFAULT = {
    "enabled": True,
    "severity_routes": {
        "critical": ["dingtalk", "wechat", "feishu"],
        "high": ["dingtalk", "wechat", "feishu"],
        "medium": ["dingtalk"],
        "low": [],
    },
    # 全局静默窗口列表：[{"enabled": true, "start": "22:00", "end": "08:00", "days": "1,2,3,4,5,6,7"}]
    # days: 1=周一...7=周日，逗号分隔，空=每天；支持跨天（end < start）
    "silence_windows": [],
}

# 敏感密钥字段：写入时加密存储，读取回显时脱敏
SENSITIVE_KEYS = {"es_password", "ldap_bind_password", "feishu_app_secret", "embedding_api_key"}

# 已迁移到「日志源」（log_sources 表）的字段：只读兜底，不再接受写入（N7 统一由日志源管理）
DEPRECATED_KEYS = {"es_url", "es_index_pattern", "es_username", "es_password"}

# 默认设置项定义（key: 默认值 + 描述）
DEFAULT_SETTINGS = {
    "prometheus_url": ("", "Prometheus 地址（监控指标查询）"),
    "es_url": ("", "Elasticsearch 地址（已迁移到「日志源」，只读兜底）"),
    "es_index_pattern": ("k8s-*", "ES 日志索引模式（已迁移到「日志源」，只读兜底）"),
    "es_username": ("", "ES 用户名（已迁移到「日志源」，只读兜底）"),
    "es_password": ("", "ES 密码（已迁移到「日志源」，只读兜底）"),
    "ldap_enabled": ("false", "是否开启 LDAP 登录"),
    "ldap_host": ("", "LDAP 服务器地址"),
    "ldap_port": ("389", "LDAP 端口"),
    "ldap_base_dn": ("", "LDAP Base DN"),
    "ldap_bind_dn": ("", "LDAP 绑定 DN（如 cn=admin,dc=example,dc=com）"),
    "ldap_bind_password": ("", "LDAP 绑定密码"),
    "ldap_user_filter": ("(uid={username})", "LDAP 用户过滤模板"),
    "ldap_default_role": ("viewer", "LDAP 认证通过后自动创建用户的默认角色（admin/operator/viewer）"),
    "feishu_app_id": ("", "飞书应用 App ID（个人消息通知）"),
    "feishu_app_secret": ("", "飞书应用 App Secret"),
    # Embedding 模型配置（知识库向量检索，OpenAI 兼容格式，可随时切换）
    "embedding_provider": ("", "Embedding 供应商（siliconflow/zhipu/dashscope/openai 等）"),
    "embedding_base_url": ("", "Embedding API 地址（OpenAI 兼容，如 https://api.siliconflow.cn/v1）"),
    "embedding_model": ("", "Embedding 模型名（如 BAAI/bge-m3 / text-embedding-v3）"),
    "embedding_api_key": ("", "Embedding API Key"),
    # 风险评分策略（JSON：操作/资源/影响面/环境分值、等级分段、审核阈值、超时时间）
    "risk_policy": ("", "风险评分策略 JSON（留空用默认）"),
    # 审批意见模板（JSON 字符串数组，审批时可直接选用）
    "approval_comment_templates": ("[]", "审批意见模板（JSON 字符串数组）"),
    # AI 诊断：深度诊断单次对话最大「有效」工具步数（重复调用不占额，按环境可调）
    "agent_max_tool_steps": ("12", "深度诊断单次对话最大工具步数（1~100）"),
    # 统一告警通知策略（N8）：分级渠道 + 全局静默窗口，全平台一份
    "alert_notify_policy": (json.dumps(ALERT_NOTIFY_POLICY_DEFAULT, ensure_ascii=False), "统一告警通知策略 JSON（分级渠道/静默窗口）"),
}


async def get_all_settings() -> dict:
    """读取全部设置（合并默认值）；敏感字段脱敏为 ***"""
    async with async_session() as session:
        result = await session.execute(select(SystemSetting))
        db_settings = {s.key: s.value for s in result.scalars()}
    merged = {}
    for key, (default, _desc) in DEFAULT_SETTINGS.items():
        raw = db_settings.get(key, default)
        merged[key] = MASKED if (key in SENSITIVE_KEYS and raw) else raw
    return merged


async def update_settings(settings_dict: dict) -> None:
    """批量更新设置（upsert）；敏感字段加密存储，脱敏占位符/空值跳过不更新

    已迁移到「日志源」的 ES 四项（DEPRECATED_KEYS）不再接受写入（只读兜底）。
    """
    async with async_session() as session:
        for key, value in settings_dict.items():
            if key not in DEFAULT_SETTINGS:
                continue
            if key in DEPRECATED_KEYS:
                continue  # 已迁移到日志源，只读兜底，不覆盖
            if key in SENSITIVE_KEYS:
                if value in ("", None, MASKED):
                    continue  # 空值或脱敏占位符不覆盖
                value = encrypt_aes256_gcm(str(value))
            result = await session.execute(select(SystemSetting).where(SystemSetting.key == key))
            existing = result.scalar_one_or_none()
            if existing:
                existing.value = str(value)
            else:
                session.add(SystemSetting(key=key, value=str(value), description=DEFAULT_SETTINGS[key][1]))
        await session.commit()


async def get_setting(key: str, default: str = "") -> str:
    """读取单个设置；敏感字段解密返回（兼容存量明文）"""
    async with async_session() as session:
        result = await session.execute(select(SystemSetting).where(SystemSetting.key == key))
        s = result.scalar_one_or_none()
    value = s.value if s else DEFAULT_SETTINGS.get(key, (default, ""))[0]
    if key in SENSITIVE_KEYS and value:
        try:
            return decrypt_aes256_gcm(value)
        except Exception:  # noqa: BLE001 存量明文数据，兼容返回原值
            return value
    return value
