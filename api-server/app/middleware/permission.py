# -*- coding: utf-8 -*-
"""RBAC 权限中间件：角色校验"""
from fastapi import Depends, HTTPException

from app.middleware.auth import get_current_active_user
from app.models import User


def require_role(*allowed_roles: str):
    """依赖工厂：要求当前用户属于指定角色之一"""

    async def _dependency(user: User = Depends(get_current_active_user)) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(status_code=403, detail="权限不足")
        return user

    # 自省用：供权限覆盖测试（tests/test_permissions.py）遍历路由时识别角色要求
    _dependency._allowed_roles = tuple(allowed_roles)
    return _dependency


# 常用角色依赖
require_admin = require_role("admin")
require_operator = require_role("admin", "operator")
require_any_user = require_role("admin", "operator", "viewer", "developer")
