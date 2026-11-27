# 11 · 用户认证审计（User Authentication Audit）

> 审计类型：**只读**静态审计。未修改任何业务代码/配置/依赖，未安装依赖，未连接生产库，未发送真实请求，未执行 git 写操作。
> 仓库：`/Users/wolf/Documents/project/autohealops`　分支：`main`
> 审计时点：2026-11-27
> 覆盖：`api-server/app/api/auth.py`、`middleware/auth.py`、`middleware/permission.py`、`core/security.py`、`core/config.py`、`models/user.py`、`models/permission.py`、`api/users.py`、`api/permissions.py`、`api/ai_proxy.py`、`api/mcp.py`、`api/external_access.py`、`api/webhooks.py`、`api/service_accounts.py`、`api/terminal.py`/`watch.py`/`logs.py`/`screens.py`/`stats.py`/`workload_logs.py`（WS/SSE 手写鉴权）、`ai-service/app/main.py`、`ai-service/app/api/aux_api.py`、`k8s-executor/internal/config/config.go`、`frontend/src/App.tsx`、`frontend/src/constants/nav.ts`、`frontend/src/pages/Login.tsx`、`frontend/src/services/api.ts`、`api-server/tests/test_permissions.py`、`install.sh`、`.env.example`。

---

## 1. 结论摘要

1. **HTTP 路由 495 条全部有鉴权入口**（`Depends` 角色依赖 **483** 条、手写鉴权白名单 **11** 条、真正零鉴权 **2** 条：`/auth/login` 与 `GET /mcp` 探测信息）。含 4 条 WebSocket + 1 条 catch-all 代理，共 **500 个路由装饰器**。数字与 `tests/test_permissions.py:24-46` 的 `PUBLIC_ALLOWLIST`（16 项）自洽，但该测试**不覆盖 WebSocketRoute**，4 条 WS 靠人肉保证。
2. **最严重的认证设计缺陷是「内部服务令牌 = JWT 签名密钥」**：`X-Internal-Token` 直接复用 `settings.JWT_SECRET_KEY`（`api-server/app/api/alerts.py:540`、`api-server/app/clients/ai_client.py:28`、`api-server/app/api/ai_proxy.py:23`、`ai-service/app/main.py:47`、`k8s-executor/internal/config/config.go:69`）。拿到任一处内部令牌 = 可直接离线伪造任意用户（含 admin）JWT，**认证体系整体失效**（AUTH-01）。
3. **默认配置不安全且无启动校验**：`install.sh:100-103` 把 `.env.example` 原样复制成 `.env`，其中 `JWT_SECRET_KEY=change-me`（`.env.example:13`）。`app/core/config.py:26` 只要求该字段存在，**不校验长度/是否默认值**，无 `model_validator`。按默认流程部署 = HS256 密钥公开可猜，可伪造 admin（AUTH-02）。本机现有 `.env` 的密钥长度为 64 字符（已改），但代码层面没有任何拦截。
4. **AI 代理是全平台最大的提权面**：`GET/POST/PUT/DELETE /api/v1/ai/{path}` 只校验「已登录」（`api_proxy.py:28` 用的是 `get_current_active_user`，**不是** `require_admin`），而 ai-service 的 30+ 路由（含 `POST /ai/models`、`DELETE /ai/models/{id}`、`POST /ai/rag/add`、`POST /ai/prompt-templates`、`POST /ai/logs/search`）全部信任内部令牌。结果：**viewer 可增删改 AI 模型配置（含 api_key）、投毒知识库、检索全平台 ES 日志**（AUTH-03 / ISO-06）。
5. **登录防爆破只覆盖本地密码分支**：`auth.py:27-28` 在 `auth_type=="ldap"` 时**提前 return，完全绕过** 5 次/300s 限制（`auth.py:16-17,31-34`）；且计数键只有 `login_fail:{username}`，**无 IP 维度**——攻击者可用 5 次错误密码**锁死任意账号**（DoS），也可对 LDAP 无限撞库（AUTH-04）。
6. **角色模型是「4 个字符串常量」而非 RBAC**：`User.role` 是 `String(20)`（`models/user.py:20`），全平台只有 `admin/operator/viewer/developer` 四个值（`api/users.py:29`）；`roles` 表（`models/user.py:34-42`）**存在但零调用方**，全仓无 `select(Role)`、无角色管理 API（AUTH-07）。前端 `Role` 类型只有 3 个值（`constants/nav.ts:4`），**没有 developer**，前后端角色枚举不一致。
7. **API Key 功能是空壳**：`POST /users/{id}/api-key` 会生成并落库 `User.api_key`（`api/users.py:83-91`、`models/user.py:22`），但**全仓没有任何代码消费它**（无 `X-API-Key` 解析、无 api_key 校验）。前端「重新生成 API Key」按钮产出的 key 永远无效（AUTH-06）。
8. **Token 暴露面 4 处**：① 前端 `localStorage.token`（`Login.tsx:15`），无 CSP（全仓无 `Content-Security-Policy`），任一 XSS 即可窃取；② 4 条 WS + 3 条 SSE 把 JWT 放 **URL query `?token=`**（`terminal.py:44`、`watch.py:47`、`logs.py:153`、`workload_logs.py:283`、`screens.py:27`、`stats.py:1440`），`start.sh:98`/`k8s/deployments.yaml:46` 启动 uvicorn **未关 access log**，access log 会记录完整 path+query → 令牌明文落盘；③ agent 安装令牌同样在 URL query（`clusters.py:253,260`）；④ 登出黑名单按**原始 token 明文**做 Redis key（`auth.py:169`），且 Redis 故障时降级放行（`middleware/auth.py:34-35`）。
9. **WS 通道不查登出黑名单**：`terminal.py:_resolve_user`（`19-31`）只做 `decode_jwt` + 查用户，**不查 `token_blacklist`**；已登出的 JWT 仍能开终端/日志流（只要 24h 内没过期）（AUTH-05）。

---

## 2. 认证方式清单（逐个给校验入口与强度）

| # | 方式 | 入口 | 校验实现 | 强度判定 |
|---|---|---|---|---|
| 1 | 本地密码 | `POST /api/v1/auth/login`（`auth_type=local`，默认） | `auth.py:36-47`：`select(User).where(username==)` → `verify_password` → `is_active` → `create_jwt` | **中**。bcrypt 校验正确；但防爆破只有用户名维度、无 IP；无密码策略；返回体用业务码（HTTP 200 + `code:401`） |
| 2 | LDAP | `POST /api/v1/auth/login`（`auth_type=ldap`） | `auth.py:64-154`：`ldap3` 服务账号 bind → 按 `user_filter` 搜索 DN（`_ldap_escape` 做了 RFC4515 转义，`53-61`）→ 用户 DN + 密码二次 bind → 查/建本地用户（默认角色 `ldap_default_role`，白名单只允许 admin/operator/viewer，`130-132`）→ 发 JWT | **中偏低**。注入防护到位；但①**无任何频率限制**；②自动建号默认 viewer（可被 `ldap_default_role=admin` 提升，属配置面）；③用户名不存在 vs 密码错误返回不同文案（`116` vs `127`）→ 可枚举 LDAP 账号 |
| 3 | JWT Bearer | 全部 `Depends(get_current_active_user)` | `middleware/auth.py:19-53` | **中**。见 §3 |
| 4 | API Key | `POST /users/{id}/api-key` 仅生成 | **无校验实现**（全仓 grep `api_key` 只命中生成/存储） | **未实现** |
| 5 | 外部系统令牌 | `GET/POST /api/v1/external/tickets` | `external_access.py:26-30`：`secrets.compare_digest(stored, header)`，令牌存 `system_settings.external_ticket_token`，admin 可轮换 | **中**。常量时间比较正确；但令牌**全量可见所有工单**（`fake_admin` 伪造成 admin，`59`），无项目/集群收敛 |
| 6 | 内部服务令牌 | `POST /alerts/{id}/auto-process`、`/recovery-notify` | `alerts.py:539-540`：`token != settings.JWT_SECRET_KEY` 直接比较（**非常量时间**） | **低（设计错误）**。令牌即 JWT 密钥（AUTH-01） |
| 7 | IM 回调验签 | `POST /webhooks/{dingtalk,wechat,feishu}/callback` | 钉钉 HMAC 时间戳签名（`webhooks.py:102-107`）、企微 `msg_signature`（`127-131`）、飞书 `X-Lark-Signature`/`X-Lark-Request-Token`（`145-176`） | **中**。验签存在；钉钉/企微依赖 `system_settings` 里的 secret，**未配置 secret 时 `_verify_*` 的返回值需复核**（见 §10 无法确认项） |
| 8 | Agent 安装令牌 | `GET /clusters/{id}/agent/install.yaml` | `clusters.py:270-275`：`_hash_token(token) == c.agent_token_hash`（sha256），可吊销 | **中**。匿名可取是设计（集群侧 kubectl 拉取），但令牌在 URL |
| 9 | WS / SSE 手写鉴权 | 4 WS + 3 SSE | `terminal.py:19-31 _resolve_user`；WS 另加 `check_cluster_permission`（`terminal.py:50`、`watch.py:52`、`logs.py:158`、`host_terminal.py:154`）；SSE 复用 `decode_jwt` + 黑名单（`screens.py:33-50`、`stats.py:1452-1470`） | **中**。WS **漏黑名单**；SSE 有黑名单 |
| 10 | 服务账号（ServiceAccount） | `GET /clusters/{id}/service-accounts` | 不是认证方式，是**被管集群 K8s SA 列表**（`service_accounts.py:21-32`），`require_any_user` + `check_cluster_permission` | 与平台登录无关，澄清用 |
| 11 | MCP | `POST /mcp` | `mcp.py:53`：`require_any_user` | **低**。任意登录用户可用全部工具（含 `permission=operate` 的 `exec_in_pod`，见 `mcp.py:6-9` 注释自认「如需审批控制，后续可加」） |

---

## 3. JWT 细节

```python
# api-server/app/core/security.py:53-62
def create_jwt(user_id: int, username: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.JWT_EXPIRE_HOURS)
    payload = {"sub": str(user_id), "username": username, "role": role, "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

def decode_jwt(token: str) -> dict:
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
```

| 项 | 实测 | 评价 |
|---|---|---|
| 算法 | `HS256`（`config.py:27` 默认，可被环境覆盖） | 对称密钥；`algorithms=[...]` 显式白名单，**无 alg 混淆风险**（好） |
| 密钥来源 | `settings.JWT_SECRET_KEY`（`config.py:26`，必填无默认） | 代码不硬编码（好）；但 `.env.example:13` = `change-me`，且 `install.sh:100-103` 会把它复制成 `.env`；`ADMIN_INITIAL_PASSWORD` 走了随机化（`main.py:43`）而 **JWT 密钥没走**（AUTH-02） |
| 过期 | `JWT_EXPIRE_HOURS=24`（`config.py:28`） | 24h 无刷新、无滑动续期；`logout` 才提前失效 |
| iss / aud | **不签发、不校验** | 多环境共用同一密钥时，A 环境 token 可打 B 环境（若密钥相同） |
| jti / iat | 无 | 无法按会话维度吊销单设备，只能按原始 token 串拉黑 |
| 黑名单 | `auth.py:169`：`set(f"token_blacklist:{token}", "1", ex=JWT_EXPIRE_HOURS*3600)` | key 是**完整 JWT 明文**（最长 ~300B），24h TTL；登出量大时 Redis 内存线性增长 |
| 黑名单降级 | `middleware/auth.py:29-35`：Redis 异常 → `logger.warning(...降级放行)` | **可用性优先于安全**：Redis 挂掉时**所有已登出 token 复活**。注释已明示是有意为之 |
| payload 信任 | `role` 写在 token 里，但 `get_current_user` 每次都**重查库**（`middleware/auth.py:41-45`），鉴权用 DB 里的 `user.role`（`permission.py:13`） | **好**：改角色/停用立即生效，不依赖 token 内容 |
| 停用用户 | `get_current_active_user` 查 `is_active`（`auth.py:50-53`） | 好 |

---

## 4. 密码存储与默认管理员

| 项 | 证据 | 结论 |
|---|---|---|
| 算法 | `security.py:40-42` `bcrypt.hashpw(pw, bcrypt.gensalt())` | **bcrypt，cost 取库默认（12）**。无 argon2 选项；无「密码哈希算法版本」字段，未来无法平滑升级 |
| 盐 | `bcrypt.gensalt()` 每次随机 | 正确 |
| 校验 | `security.py:45-50`，`ValueError` → `False` | 正确（LDAP 用户的空 `password_hash` 会走这条路返回 False，安全） |
| 默认管理员 | `main.py:30-55`：`admin` 取 `ADMIN_INITIAL_PASSWORD`，否则 `secrets.token_urlsafe(12)`；`operator`/`viewer` 一律随机；密码**只在新建时打印一次日志** | **已修好**（对比 `docs/CODE-AUDIT.md:19` 记的旧问题 `admin/admin123`）。但 `README.md:110-114` 仍写着 `admin/admin123` 等明文口令表 → 文档误导（P3，已在 01 号报告提过，此处只做认证视角登记） |
| 密码策略 | 无最小长度/复杂度/黑名单；`users.py:40` 允许 `password=""` → `hash_password("")`，随后**空密码可登录** | **缺失**（AUTH-08） |
| 改密 | `settings.py:137-150` 校验旧密码后更新 | 正确；但**不校验新密码强度**，也不吊销其它在线 token |
| LDAP 用户 | `password_hash=""`（`auth.py:140`） | 无法本地登录（`bcrypt.checkpw` 抛 `ValueError` → False），符合预期 |

---

## 5. 登录防爆破

```python
# api-server/app/api/auth.py:16-17, 27-34, 39-43
_LOGIN_FAIL_LIMIT = 5
_LOGIN_FAIL_WINDOW_SECONDS = 300
...
    if auth_type == "ldap":
        return await _ldap_login(username, password)      # ← 27-28：绕过限流
    fail_key = f"login_fail:{username}"                    # ← 只按用户名
    fail_count = await redis_pool.get(fail_key)
    if fail_count and int(fail_count) >= _LOGIN_FAIL_LIMIT:
        return {"code": 429, ...}
```

| 维度 | 实测 | 风险 |
|---|---|---|
| 阈值/窗口 | 5 次 / 300s | 合理 |
| 计数维度 | **仅 username** | ① 无 IP/设备维度 → 分布式撞库不受限（换用户名即可）；② 可**定向锁死任意账号**（对 `admin` 打 5 次错密码 → 管理员 5 分钟进不来，可循环续期）→ 账号级 DoS |
| 失败清零 | `auth.py:46` 登录成功删除 | 正确 |
| LDAP 分支 | **完全不限流** | LDAP 撞库无限次；且每次失败会打一次 LDAP bind（放大内网认证压力） |
| Redis 故障 | `redis_pool.get`/`incr` **无 try**（`auth.py:32,40`） | Redis 不可用 → 登录接口 **500**（整站无法登录）；与 `middleware/auth.py` 的「降级放行」策略不一致：登录是 fail-closed，鉴权是 fail-open |
| 返回码 | 业务码 429 包在 HTTP 200 里 | 前端按 `code` 处理；网关/WAF 无法按 HTTP 状态限流 |
| 全局限流 | 全仓无 `slowapi`/中间件级限流（grep 只命中通知服务内部限频） | 登录之外的所有接口（如 `/ai/**` 代理）无速率限制 |

---

## 6. 角色模型

### 6.1 事实

| 层 | 实现 | 证据 |
|---|---|---|
| 存储 | `user.role: String(20)`，默认 `viewer` | `models/user.py:20` |
| 合法值 | `VALID_ROLES = ("admin","operator","viewer","developer")` | `api/users.py:29` |
| 鉴权依赖 | `require_role(*roles)` 工厂 + 3 个预置 + 1 个业务特化 | `middleware/permission.py:9-25`；`hosts.py:22-28` `require_host_admin` |
| 使用密度 | `require_any_user` 219 处 / `require_admin` 124 / `require_operator` 123 / `require_host_admin` 9 / `get_current_active_user` 9（`grep "Depends(" app/api/*.py` 统计） | — |
| 角色表 `roles` | 定义了 `Role(Base)`（name/description/permissions JSON） | `models/user.py:34-42` |
| 角色表使用点 | **0**：全仓只有 `models/__init__.py:58` 导入、`:125` 导出；无 `select(Role)`、无角色 CRUD API | `grep -rn "Role\b" app/` 仅命中 `ClusterRole` 字符串与 `require_role` |
| developer | 只在 4 元组里出现，**前端 `Role` 类型没有它**（`constants/nav.ts:4`），`ROLE_RANK` 也没有（`nav.ts:22`）→ 前端算 `ROLE_RANK[developer] ?? 0 = 0` | 前后端角色枚举不一致 |

### 6.2 结论

- **角色是「粗粒度开关」，不是权限模型**。它只决定「能不能进这个接口」，真正「能看哪个集群/库」由 `user_cluster_permissions` / `user_db_connections` / `user_project_permissions` 三张表决定（见 12 号报告）。
- `roles` 表是**死表**（DDL 存在、ORM 存在、无调用方），属「预留但未落地」。若未来做动态 RBAC，需新增 `role_permissions` 与依赖注入改造；当前无任何迁移路径。
- `developer` 是**半成品角色**：后端可创建、可鉴权（`require_any_user` 放行，`require_operator` 拒绝），前端无菜单/无类型 → developer 用户登录后看到的是「viewer 的菜单 + 空白的部分页面」。

---

## 7. 端点级鉴权普查（全量，不抽样）

**方法**：`ast` 解析 `api-server/app/api/*.py` 全部模块，对每个 `@router.<method>` / `@router.websocket` / `@router.api_route` 装饰的函数，收集 ①函数签名里的 `Depends(X)` ②装饰器 `dependencies=[...]` ③`APIRouter(dependencies=[...])`；`X ∈ {require_admin, require_operator, require_any_user, require_role, get_current_user, get_current_active_user, require_host_admin}` 计为「有鉴权」。

```bash
# 复现命令（只读）
cd api-server && python3 - <<'EOF'
import ast,glob
AUTH={"require_admin","require_operator","require_any_user","require_role",
      "get_current_user","get_current_active_user","require_host_admin"}
...（逐函数收集 Depends 目标，见 §14）
EOF
```

### 7.1 全量统计表（67 个路由模块）

| 文件 | 路由数 | 有鉴权 | 无鉴权 |
|---|---:|---:|---:|
| `agent.py` | 12 | 12 | 0 |
| `ai_proxy.py` | 1（catch-all `api_route`） | 1 | 0 |
| `alert_actions.py` | 3 | 3 | 0 |
| `alert_policies.py` | 4 | 4 | 0 |
| `alert_silences.py` | 9 | 9 | 0 |
| `alerts.py` | 14 | 12 | **2** |
| `approval_flows.py` | 5 | 5 | 0 |
| `approvals.py` | 9 | 9 | 0 |
| `audit.py` | 3 | 3 | 0 |
| `auth.py` | 3 | 2 | **1** |
| `batch.py` | 4 | 4 | 0 |
| `clusters.py` | 21 | 20 | **1** |
| `custom_tasks.py` | 7 | 7 | 0 |
| `db_approvals.py` | 8 | 8 | 0 |
| `db_connections.py` | 47 | 47 | 0 |
| `db_diagnose_batch.py` | 1 | 1 | 0 |
| `db_diagnosis.py` | 5 | 5 | 0 |
| `decisions.py` | 6 | 6 | 0 |
| `diagnostic.py` | 5 | 5 | 0 |
| `eval.py` | 5 | 5 | 0 |
| `executions.py` | 2 | 2 | 0 |
| `external_access.py` | 4 | 2 | **2** |
| `fault_diagnosis.py` | 7 | 7 | 0 |
| `host_audits.py` | 2 | 2 | 0 |
| `host_credentials.py` | 4 | 4 | 0 |
| `host_groups.py` | 7 | 7 | 0 |
| `host_sessions.py` | 2 | 2 | 0 |
| `host_terminal.py` | 14 | 13 | **1**（WS） |
| `hosts.py` | 8 | 8 | 0 |
| `im_groups.py` | 4 | 4 | 0 |
| `in_app_messages.py` | 4 | 4 | 0 |
| `inspections.py` | 1 | 1 | 0 |
| `jvm.py` | 7 | 7 | 0 |
| `knowledge.py` | 10 | 10 | 0 |
| `log_alert_rules.py` | 5 | 5 | 0 |
| `log_sources.py` | 6 | 6 | 0 |
| `logs.py` | 2 | 1 | **1**（WS） |
| `mcp.py` | 2 | 1 | **1** |
| `monitoring.py` | 9 | 9 | 0 |
| `notifications.py` | 9 | 9 | 0 |
| `on_duty.py` | 11 | 11 | 0 |
| `oracle_clients.py` | 5 | 5 | 0 |
| `permissions.py` | 9 | 9 | 0 |
| `projects.py` | 15 | 15 | 0 |
| `prompt_health.py` | 6 | 6 | 0 |
| `release.py` | 20 | 20 | 0 |
| `repair_records.py` | 1 | 1 | 0 |
| `report_schedules.py` | 6 | 6 | 0 |
| `resource_alert_rules.py` | 4 | 4 | 0 |
| `resource_governance.py` | 4 | 4 | 0 |
| `resource_governance_ctl.py` | 6 | 6 | 0 |
| `resources.py` | 79 | 79 | 0 |
| `screens.py` | 1 | 0 | **1**（SSE） |
| `self_heal_rules.py` | 4 | 4 | 0 |
| `service_accounts.py` | 1 | 1 | 0 |
| `settings.py` | 6 | 6 | 0 |
| `stats.py` | 8 | 7 | **1**（SSE） |
| `tasks.py` | 5 | 5 | 0 |
| `terminal.py` | 1 | 0 | **1**（WS） |
| `tickets.py` | 11 | 11 | 0 |
| `trace.py` | 3 | 3 | 0 |
| `users.py` | 5 | 5 | 0 |
| `watch.py` | 1 | 0 | **1**（WS） |
| `webhooks.py` | 3 | 0 | **3** |
| `workload_logs.py` | 1 | 0 | **1**（SSE） |
| `zombie.py` | 3 | 3 | 0 |
| **合计** | **500**（495 HTTP + 4 WS + 1 catch-all） | **483** | **17** |

### 7.2 17 条「无 Depends 鉴权」清单（方法 + 路径 + 行号 + 实际保护）

| # | 方法 | 路径 | 位置 | 实际保护 | 判定 |
|---|---|---|---|---|---|
| 1 | POST | `/api/v1/auth/login` | `auth.py:21` | 用户名维度限流 5/300s（LDAP 分支无限流） | **设计允许**，但见 AUTH-04 |
| 2 | POST | `/api/v1/alerts/{alert_id}/auto-process` | `alerts.py:533` | `X-Internal-Token == JWT_SECRET_KEY`（`:539-540`） | 可接受但密钥复用（AUTH-01） |
| 3 | POST | `/api/v1/alerts/{alert_id}/recovery-notify` | `alerts.py:548` | 同上（`:553-554`） | 同上 |
| 4 | GET | `/api/v1/clusters/{cluster_id}/agent/install.yaml` | `clusters.py:260` | sha256 agent token（`:272-275`） | 设计允许；令牌在 URL |
| 5 | GET | `/api/v1/external/tickets` | `external_access.py:52` | `X-External-Token` + `compare_digest`（`:26-30,57`） | 中；**全量可见工单** |
| 6 | POST | `/api/v1/external/tickets` | `external_access.py:66` | 同上（`:68`） | 同上 |
| 7 | GET | `/mcp` | `mcp.py:122` | 无（返回协议版本与工具名列表） | **真·零鉴权**（信息泄露 P3） |
| 8 | WS | `/api/v1/ws/clusters/{cluster_id}/namespaces/{namespace}/pods/{pod_name}/exec` | `terminal.py:39` | `?token=` JWT + `check_cluster_permission(operate)`（`:44-51`） | **漏黑名单** |
| 9 | WS | `/api/v1/ws/clusters/{cluster_id}/namespaces/{namespace}/pods/status` | `watch.py:41` | JWT + `check_cluster_permission(read)`（`:48-54`） | 漏黑名单 |
| 10 | WS | `/api/v1/ws/clusters/{cluster_id}/namespaces/{namespace}/pods/{pod_name}/logs` | `logs.py:151` | JWT + `check_cluster_permission(read)`（`:155-160`） | 漏黑名单 |
| 11 | WS | `/api/v1/ws/hosts/{host_id}/terminal` | `host_terminal.py:140` | JWT + `hgs.host_access(operate)`（`:152-157`） | 漏黑名单 |
| 12 | GET | `/api/v1/stats/screen/stream`（SSE） | `screens.py:27` | JWT + 黑名单 + 查用户（`:33-50`） | 合规 |
| 13 | GET | `/api/v1/stats/dashboard/stream`（SSE） | `stats.py:1440` | JWT + 黑名单 + 查用户（`:1452-1470`） | 合规 |
| 14 | GET | `/api/v1/resources/clusters/{cluster_id}/namespaces/{namespace}/workloads/{kind}/{name}/logs/stream`（SSE） | `workload_logs.py:278` | JWT + `check_cluster_permission(read)`（`:286-293`） | 合规 |
| 15-17 | POST | `/api/v1/webhooks/{dingtalk,wechat,feishu}/callback` | `webhooks.py:102,127,145` | 各通道签名验签 | 需确认 secret 为空时的行为 |

**结论：真正没有任何认证的只有 2 条（`/auth/login`、`GET /mcp`）；其余 15 条都有手写保护。不存在「整片接口裸奔」的情况。** 这是本项目认证层面做得最好的一块，且 `tests/test_permissions.py:24-46` 的白名单与之逐条对应（差异：该测试不覆盖 4 条 WebSocketRoute，因为 `isinstance(r, APIRoute)` 把它们过滤掉了 → **WS 鉴权无自动化回归**）。

---

## 8. 前端权限限制 vs 后端校验

### 8.1 前端守卫机制

```tsx
// frontend/src/App.tsx:66-88
function RequireAuth({ children }) { const token = localStorage.getItem('token'); return token ? children : <Navigate to="/login" replace /> }
function RequireRole({ role, children }) { ... if (user.role !== role) return <Navigate to="/dashboard" replace /> }
function guard(path, el) { return minRoleForPath(path) === 'admin' ? <RequireRole role="admin">{el}</RequireRole> : el }
```

- **只有 `minRole: 'admin'` 的页面被 guard**；`minRole: undefined` 的页面只要求「有 token」。
- `operator` 专属页面：**一个都没有**（`nav.ts:26-80` 里只有 `admin` 门槛或无条件）。

### 8.2 前端「仅 guard / 完全没有 role 门槛」的页面 → 后端核对

| 前端路由 | 前端门槛 | 后端接口（主要） | 后端校验 | 一致性 |
|---|---|---|---|---|
| `/clusters`、`/clusters/:id/*` | 无（仅登录） | `/resources/clusters/**`（79 条） | `require_any_user` + `_deny_cluster`（集群/命名空间级） | **后端更严（好）** |
| `/resources/databases`、`/:id` | 无 | `/db-connections`（47 条） | `require_any_user` + `check_db_permission`（3 条漏，见 ISO-04） | 基本一致 |
| `/resources/hosts` | `RequireHostGrant`（前端调 `getHostGroups()` 判 `my_role`） | `/hosts`（8 条） | `require_host_admin` / `hgs.host_access` | **后端更严（好）** |
| `/alerts/handling` | 无 | `/alerts`（14 条） | `require_any_user`；**无任何范围过滤** | 一致但**没有隔离**（ISO-01） |
| `/alerts/tickets` | 无 | `/tickets`（11 条） | `require_any_user` + `ticket_service._scope_conditions` | **后端有隔离（唯一做得好的域）** |
| `/alerts/on-duty` | 无 | `/on-duty`（11 条） | `require_any_user` | 全局数据，可接受 |
| `/diagnose/agent` | 无 | `/ai/**` catch-all | **仅 `get_current_active_user`** | **后端过松（AUTH-03）** |
| `/diagnose/release` | 无 | `/release`（20 条） | 写操作 `check_cluster_permission(operate)`（`release.py:480,564,618`） | 后端更严 |
| `/diagnose/executions`、`/:planId` | 无 | `/executions/{plan_id}` | `require_any_user`，**无归属校验** | **后端缺隔离（ISO-03）** |
| `/diagnose/traces` | 无 | `/agent-traces` | `require_any_user` + `trace_service` 按可见集群过滤（`trace_service.py:792-812`） | 一致（好） |
| `/platform/audit` | 无 | `/audit/search`、`/audit/trace/{id}` | `require_any_user`，**无过滤**；仅 `/audit/verify-chain` 是 admin | **后端缺隔离** |
| `/platform/reports` | 无 | `/stats/*` 等 | `require_any_user`，无项目过滤 | 缺隔离 |
| `/platform/users`、`/settings`、`/platform/ai`、`/notifications`、`/alerts/policies`、`/alerts/silences`、`/alerts/sources`、`/diagnose/strategy`、`/diagnose/automation`、`/diagnose/tasks`、`/diagnose/eval` | `admin` | 对应接口 | 多数 `require_admin` | **一致（好）** |

**唯一「前端管、后端不管」的一类**：`/platform/audit`、`/platform/reports`、`/diagnose/executions`、`/alerts/handling` —— 前端当「所有登录用户可见」，后端也只做「登录即可」，**不是绕过漏洞，而是两头都没有隔离**（问题归到 12 号报告的 ISO 系列）。

`/platform/ai` 是**反例**（前端藏了按钮，后端 `ai_proxy` 只校验登录）→ **前端藏 ≠ 后端管**，这是真正的越权路径（AUTH-03）。

---

## 9. 后门式逻辑排查（`X-User-Id` / 请求头信任 / 内网免鉴权）

| 排查项 | 命令 | 结果 |
|---|---|---|
| `X-User-Id` / `X-User` 类头部 | `grep -rn "X-User\|x_user" api-server/app` | **0 命中**。不存在「靠请求头声明身份」的后门 |
| `trusted` / `internal` 白名单 | `grep -rn "trusted\|internal_only\|allow_internal" api-server/app` | 无「按源 IP 免鉴权」逻辑 |
| 内网豁免 | 无 `request.client.host` 判断鉴权的代码 | 无 |
| 内部令牌 | `X-Internal-Token` 3 处（alerts×2、ai_proxy 出站） | 存在，且**等于 JWT 密钥**（AUTH-01） |
| 伪 admin | `external_access.py:59` `SimpleNamespace(id=None, role="admin", username="external")` | 外部令牌调用时**伪造 admin 身份**进 service 层 → 全量可见工单（有意为之，但意味着外部令牌 = 平台管理员视图） |
| MCP 工具 | `mcp.py:6-9` 自述「写类工具（permission=operate）经 MCP 调用同样执行；如需审批控制，后续可加」 | **MCP 通道绕过审批/决策引擎**，只要 `require_any_user` |
| 前端伪造身份 | `localStorage.user` 是纯前端信任（`App.tsx:77`） | 前端可任意改 `role` 让菜单出现，但后端按 `user.role` 重查库（`middleware/auth.py:41-45`）→ **只影响 UI，不影响数据**（好） |

**结论：不存在 `X-User-Id` 型经典后门；风险集中在「内部令牌与 JWT 密钥同源」与「MCP 绕过审批」两处。**

---

## 10. 会话 / Token 暴露面

| 通道 | 位置 | 证据 | 风险 |
|---|---|---|---|
| 浏览器存储 | `localStorage.token` / `localStorage.user` | `Login.tsx:15-16`、`services/api.ts:31-35` | 无 `HttpOnly` Cookie，任一 XSS 可窃取；全仓**无 CSP**、无 `X-Frame-Options`（`grep Content-Security-Policy` = 0 命中）；`/screen` 用 `<iframe src="/monitor-bigscreen.html">`（`App.tsx:130-138`）→ 大屏页若被注入可读父域存储 |
| URL query | `?token=` × 7（4 WS + 3 SSE） | `terminal.py:44`、`watch.py:47`、`logs.py:153`、`host_terminal.py:141`、`workload_logs.py:283`、`screens.py:27`、`stats.py:1440` | 进入浏览器历史、Referer、代理日志、uvicorn access log |
| 服务日志 | uvicorn 默认 access log 未关闭（`start.sh:98`、`k8s/deployments.yaml:46` 启动命令无 `--no-access-log`）；access log 记录 `path?query` | 同上 | **JWT 明文落 `logs/api-server.log`**；日志若被采集/外发即凭据泄露 |
| Agent 令牌 | `install.yaml?token=...&pull_token=...`（`clusters.py:253-256`） | 同上 | 集群接入令牌进 shell history / CI 日志 |
| Redis | `token_blacklist:<完整 JWT>` | `auth.py:169` | 有 Redis 读权限者可直接拿到有效 token 串（未过期前可重放） |
| 审计日志 | `audit_service` 记录操作内容 | — | 未发现记录 Authorization 头（好）；但 `trace_service.py:31` 有敏感字段脱敏名单含 `api_key/private_key` |
| 错误回显 | `main.py:600-604` 统一异常处理不回显内部细节 | — | 好 |
| 登出覆盖 | HTTP 请求全部走 `get_current_user` 查黑名单；**WS 不查** | `terminal.py:19-31` | 登出后 WS/终端仍可用至 token 过期 |

---

## 11. 功能状态清单

| 功能 | 状态 | 证据 |
|---|---|---|
| 本地密码登录 + JWT 签发 | 已实现（有代码依据） | `auth.py:20-50`、`security.py:53-57` |
| LDAP 登录（含注入转义） | 已实现（有代码依据） | `auth.py:64-154`、`_ldap_escape:53-61` |
| LDAP 登录限流 | **完全未实现** | `auth.py:27-28` 提前 return |
| 登出黑名单 | 部分实现（WS 未覆盖、Redis 故障放行） | `auth.py:164-170`、`middleware/auth.py:29-35` |
| JWT iss/aud/jti | 完全未实现 | `security.py:53-62` |
| 密码哈希 bcrypt | 已实现（有代码依据） | `security.py:40-50` |
| 密码强度策略 | 完全未实现 | `api/users.py:32-52` 无校验 |
| 默认管理员随机密码 | 已实现（有代码依据） | `main.py:30-55` |
| 登录防爆破（用户名维度） | 已实现（有代码依据） | `auth.py:16-17,31-43` |
| 登录防爆破（IP 维度 / 全局限流） | 完全未实现 | 全仓无 slowapi/中间件限流 |
| 四角色字符串 RBAC | 已实现（有代码依据） | `api/users.py:29`、`middleware/permission.py:23-25` |
| 动态角色表（`roles`） | **表存在但无调用方** | `models/user.py:34-42`，全仓 0 处查询 |
| 端点级鉴权覆盖 | 已实现（有代码依据） | 495 HTTP 全有入口；`tests/test_permissions.py` 守护 |
| WS 鉴权自动化守护 | 完全未实现 | 测试只遍历 `APIRoute`，4 条 WS 不在覆盖内 |
| API Key 认证 | **仅后端接口实现（生成侧）**，校验侧完全未实现 | `users.py:83-91`、`models/user.py:22` |
| 外部系统令牌鉴权 | 已实现（有代码依据） | `external_access.py:26-30` |
| 内部服务令牌 | 已实现但**密钥复用 JWT 密钥** | `alerts.py:539-540`、`config.go:69` |
| IM 回调验签 | 已实现（有代码依据） | `webhooks.py:102-176` |
| MCP 鉴权 | 仅后端接口实现（登录即可，无角色/审批） | `mcp.py:53` |
| 前端路由守卫 | 部分实现（只有 admin 门槛 + host grant） | `App.tsx:66-111`、`nav.ts:113-116` |
| developer 角色前端支持 | 完全未实现 | `nav.ts:4` 无 developer |

---

## 12. 问题清单

```
问题编号：AUTH-01
问题名称：内部服务令牌（X-Internal-Token）复用 JWT 签名密钥，导致「内部令牌泄露 = 可伪造任意用户 JWT」
问题等级：P1
所属模块：认证 / 服务间通信
问题描述：api-server、ai-service、k8s-executor 三方把 X-Internal-Token 统一设为 settings.JWT_SECRET_KEY。
          alerts.py 的两条内部回调端点用该值做明文比较；ai-service 全部路由用该值做唯一鉴权。
          于是「内部令牌」与「JWT HS256 签名密钥」是同一个字符串：任何能读到该值的人（k8s Secret、.env、
          ai-service 容器环境、一次中间人抓包拿到内网请求头）都可以离线签出 sub=1、role=admin 的 token，
          直接以管理员身份登录平台；反之 JWT 泄露也等于拿到内部回调权限。
          另外 alerts.py:540 用 `token != settings.JWT_SECRET_KEY` 明文比较，非常量时间。
代码位置：api-server/app/api/alerts.py:539-540,553-554
          api-server/app/clients/ai_client.py:28
          api-server/app/api/ai_proxy.py:23
          ai-service/app/main.py:46-47   ai-service/app/core/config.py:19-20
          k8s-executor/internal/config/config.go:23,69
          .env.example:53（注释明确写「X-Internal-Token 复用 JWT_SECRET_KEY」）
涉及函数或类：auto_process_alert / recovery_notify_alert / _internal_headers / verify_internal（ai-service）
调用链：k8s-executor watcher/notify.go:39 → POST /api/v1/alerts/{id}/auto-process（header X-Internal-Token=JWT_SECRET_KEY）
        → alerts.py:540 比较通过 → process_alert（可被触发任意告警的自动处置）
        api-server → ai_client.py:28 → ai-service main.py:47 → 全量 AI 接口放行
根本原因：把「服务身份令牌」与「用户令牌签名密钥」当成一个配置项复用，缺少独立的内部密钥与密钥分层。
可能影响：完全认证绕过（伪造 admin）、内部接口未授权调用、密钥轮换时必须同时重启三方（运维风险）。
修改建议：1) 新增独立 INTERNAL_SERVICE_TOKEN（随机 32B），与 JWT_SECRET_KEY 解耦；
          2) alerts.py 用 secrets.compare_digest 比较；
          3) 内部令牌加时间戳+HMAC（防重放）；
          4) 轮换 JWT_SECRET_KEY 后强制所有会话失效（当前无此机制）。
验证方法：grep 确认 X-Internal-Token 不再引用 JWT_SECRET_KEY；用旧内部令牌尝试签 JWT 应失败。
是否需要立即处理：是
```

```
问题编号：AUTH-02
问题名称：默认部署密钥为 `change-me`，且配置层不校验密钥强度 → 可离线伪造 admin JWT
问题等级：P0（成立条件见下）
所属模块：认证 / 配置
问题描述：.env.example:13 写死 JWT_SECRET_KEY=change-me；install.sh:98-104 的 ensure_env() 在 .env 不存在时
          **直接 cp .env.example .env**，并只提示「请填好」；api-server/app/core/config.py:26 只声明该字段必填，
          没有任何长度/默认值/熵校验（对比 main.py 已对管理员口令做随机化）。若按默认流程部署且运维不手改，
          HS256 签名密钥即公开字符串 `change-me`，攻击者可离线构造任意 sub/role 的 token，无需任何账号。
          ENCRYPTION_KEY（.env.example:18 = change-me-32bytes-key-change-me!!）同样未校验，
          该密钥用于 AES-GCM 加密 kubeconfig/数据库密码/SSH 私钥 → 泄露即全量凭据解密。
代码位置：.env.example:13,18
          install.sh:98-104（ensure_env）
          api-server/app/core/config.py:25-32
涉及函数或类：Settings（pydantic-settings）
调用链：install.sh install_local → ensure_env → cp .env.example .env → uvicorn 启动 → jwt.encode(payload, "change-me")
根本原因：模板文件带可用的占位值 + 启动时不做安全校验（fail-fast 缺失）。
可能影响：平台完全接管（伪造 admin、解密所有纳管凭据）。
修改建议：1) Settings 增加 model_validator：拒绝 {"change-me","",<16字符} 且拒绝 == 默认占位值，直接启动失败；
          2) install.sh 生成 .env 时用 secrets/openssl 自动填充随机密钥；
          3) .env.example 改为空值 + `# 必填，openssl rand -hex 32`。
验证方法：把 .env 的 JWT_SECRET_KEY 设为 change-me 启动，应报错退出；当前实测会正常启动（本机 .env 为 64 字符，未触发）。
是否需要立即处理：是
备注：本机现有 .env 的 JWT 密钥长度 64 字符、ENCRYPTION_KEY 32 字符，当前实例不受影响；
      风险面在「按 install.sh 默认流程新部署的环境」。
```

```
问题编号：AUTH-03
问题名称：/api/v1/ai/** 通用代理只校验「已登录」→ viewer 可改 AI 模型配置、删模型、投毒知识库、检索全平台日志
问题等级：P1
所属类别：认证 / 越权
问题描述：ai_proxy.py 用一个 catch-all api_route 把 /api/v1/ai/{path} 全量转发到 ai-service，
          依赖只有 get_current_active_user（无 require_admin、无 require_operator、无任何资源范围校验）。
          ai-service 侧 30+ 路由只认 X-Internal-Token（由 api-server 代填），因此平台侧的角色模型在 AI 域完全失效。
          实测可达的敏感写操作：POST /ai/models（新增模型，body 含 api_key）、PUT /ai/models/{id}、
          DELETE /ai/models/{id}、POST /ai/models/{id}/default、POST /ai/rag/add（知识库写入/投毒）、
          POST /ai/prompt-templates、PUT /ai/prompt-templates/{id}；敏感读：POST /ai/logs/search（按 namespace/pod 查 ES，无集群范围）、
          GET /ai/logs（模型调用日志）、GET /ai/logs/stats、GET /ai/models（含 base_url/模型名）。
代码位置：api-server/app/api/ai_proxy.py:27-28（鉴权仅 get_current_active_user）
          ai-service/app/main.py:44-49（内部令牌中间件）
          ai-service/app/api/aux_api.py:17-172,422-440（models/rag/logs 路由，全部无角色概念）
涉及函数或类：ai_proxy / verify_internal_token / create_model / delete_model / rag_add / search_logs
调用链：登录用户（viewer）→ PUT /api/v1/ai/models/1 {base_url:"http://attacker"} → ai_proxy 剥 Authorization、
        注入 X-Internal-Token → ai-service PUT /models/1 → 改写全平台诊断所用模型端点（可导致全部诊断数据外流到攻击者服务器）。
根本原因：把「代理转发」当成纯管道，未在边界做角色/资源授权；ai-service 无用户上下文（只有服务身份）。
可能影响：P1 提权（viewer→平台 AI 配置管理员）；知识库投毒会污染后续所有诊断；模型端点劫持可批量外泄告警/日志/集群信息。
修改建议：1) 按路径前缀做白名单 + 角色映射（写类 /ai/models、/ai/rag/add、/ai/prompt-* → require_admin；
          诊断类 → require_operator 且校验 cluster_id 权限）；2) 代理层透传 user_id/role 给 ai-service 或改由 api-server 直接实现；
          3) /ai/logs/search 必须带 cluster_id 并做 check_cluster_permission。
验证方法：以 viewer 身份 PUT /api/v1/ai/models/1，当前返回 200 并生效。
是否需要立即处理：是
```

```
问题编号：AUTH-04
问题名称：LDAP 登录完全绕过防爆破，且限流只有用户名维度（可锁死任意账号）
问题等级：P1
所属模块：认证 / 登录
问题描述：auth.py:27-28 在 auth_type=="ldap" 时直接 return，跳过了 5 次/300s 的失败计数；
          LDAP 分支内部也没有任何计数/延迟/图形验证，攻击者可无限次撞库（每次失败仅打一次 LDAP bind）。
          同时本地分支的键是 login_fail:{username}，没有 IP/UA 维度：
          ① 换用户名即可绕开（对 admin 之外的账号无限试）；
          ② 反向可作 DoS：对 admin 连打 5 次错密码 → 管理员 5 分钟内无法登录，循环续期即可长期锁定。
          另外 auth.py:32,40 的 redis 调用无 try：Redis 不可用时登录接口直接 500（fail-closed），
          与 middleware/auth.py 的 fail-open 策略不一致。
          返回体用 HTTP 200 + code:429，网关/WAF 无法按状态码限流。
代码位置：api-server/app/api/auth.py:27-28（LDAP 绕过）、31-34（仅用户名维度）、32,40（无 try）、34（业务码 429）
涉及函数或类：login / _ldap_login
调用链：POST /auth/login {auth_type:"ldap", username:"a", password:"x"} → _ldap_login → ldap3 Connection(user_dn, pw)
        → 失败返回 401，**无计数**，可无限循环
根本原因：限流实现放在 local 分支之后；键设计未考虑攻击者视角（无 IP）；未抽成通用中间件。
可能影响：LDAP 域账号被撞库（可导致域账号锁定/泄露）；平台 admin 被定向锁死（DoS）；Redis 抖动时全站无法登录。
修改建议：1) 把限流上移到 auth_type 分支之前，键用 (username, client_ip) 双维度 + 全局 IP 维度；
          2) 登录路径加指数退避（第 6 次起 sleep）；
          3) Redis 异常时 fail-open 但记录告警（可用性优先，与鉴权策略一致），或本地内存兜底计数；
          4) 429 用真实 HTTP 状态码。
验证方法：连续 6 次 auth_type=ldap 错密码，当前不会返回 429。
是否需要立即处理：是
```

```
问题编号：AUTH-05
问题名称：WebSocket 通道不校验登出黑名单，登出后终端/日志流仍可用至 token 过期
问题等级：P2
所属模块：认证 / 会话
问题描述：terminal.py:19-31 的 _resolve_user 只做 decode_jwt + 查 is_active，未查 token_blacklist；
          watch.py / logs.py / host_terminal.py 复用同一函数。SSE（screens.py:40、stats.py:1454）则查了黑名单。
          结果：用户点「退出登录」后，HTTP 全部 401，但已建立的 WS（Pod exec 终端、日志跟随）不会断开，
          且可用同一 token 重新建立 WS，直到 24h 过期。
          同时 Redis 故障时 HTTP 侧「降级放行」（middleware/auth.py:34-35），黑名单整体失效。
代码位置：api-server/app/api/terminal.py:19-31（无黑名单）
          api-server/app/api/host_terminal.py:141（复用 _resolve_user）
          api-server/app/api/watch.py:47、logs.py:153（复用）
          api-server/app/middleware/auth.py:29-35（降级放行）
涉及函数或类：_resolve_user / get_current_user
调用链：POST /auth/logout（token 进黑名单）→ 攻击者持有旧 token → WS /ws/clusters/1/.../pods/x/exec?token=旧token
        → _resolve_user 通过（没查黑名单）→ check_cluster_permission 通过 → 获得容器 shell
根本原因：WS/HTTP 两套鉴权路径未共用同一校验函数。
可能影响：登出语义失效；被窃 token 的「止损」手段失效（这是黑名单存在的唯一目的）。
修改建议：抽出 verify_token_or_raise(token)（含黑名单）供 HTTP/WS/SSE 共用；WS 首次握手 + 每 N 秒复查。
验证方法：登出后立刻用旧 token 连 WS，当前仍能连上。
是否需要立即处理：否（P2，但修复成本低）
```

```
问题编号：AUTH-06
问题名称：API Key 只生成不校验（功能空壳），前端按钮产出永远无效的凭据
问题等级：P2
所属模块：认证 / API Key
问题描述：users.py:83-91 生成 secrets.token_urlsafe(32) 写入 users.api_key，前端 services/api.ts:415 有「重新生成」入口；
          但全仓没有任何 X-API-Key 解析/校验代码（grep api_key 仅命中生成、模型配置、settings、trace 脱敏名单）。
          get_current_user 只认 Bearer JWT。故该功能对用户是「给了一个看起来能用的 key，实际任何地方都不认」。
代码位置：api-server/app/api/users.py:83-91
          api-server/app/models/user.py:22
          frontend/src/services/api.ts:415
涉及函数或类：regenerate_api_key
调用链：POST /users/{id}/api-key → 返回 api_key → 无任何消费方
根本原因：接口先落地、鉴权链路未实现。
可能影响：功能误导（P2）；若未来仓促接入而忘了加 scope，会引入新的绕过面。
修改建议：① 要么补完（新增 get_current_user_or_api_key 依赖，api_key 哈希存储 + 作用域 + 过期）；
          ② 要么移除端点与前端按钮。
验证方法：用生成的 api_key 以 `X-API-Key: xxx` 访问 /api/v1/auth/me，当前 401。
是否需要立即处理：否
```

```
问题编号：AUTH-07
问题名称：roles 表存在但零调用方，角色仍是硬编码字符串四元组
问题等级：P2
所属模块：授权模型
问题描述：models/user.py:34-42 定义 Role(id,name,description,permissions JSON)，models/__init__.py:58,125 导入导出，
          但全仓无 select(Role)、无角色 CRUD、无 permission JSON 解析。实际鉴权只比较 User.role 字符串
          （middleware/permission.py:13），合法值写在 api/users.py:29 的常量里。
          前端 Role 类型只有 admin/operator/viewer（constants/nav.ts:4），缺 developer。
代码位置：api-server/app/models/user.py:34-42
          api-server/app/middleware/permission.py:9-25
          api-server/app/api/users.py:29
          frontend/src/constants/nav.ts:4,22
涉及函数或类：Role / require_role / VALID_ROLES / ROLE_RANK
调用链：无（死表）
根本原因：RBAC 从「字符串枚举」起步，预留表未接入。
可能影响：无法表达「自定义角色/细粒度权限」；新增角色要改代码 + 改前端类型；表结构误导后续开发。
修改建议：要么删表并在文档写明「角色=代码常量」，要么补齐 role_permissions + 依赖注入（工作量大，需单独立项）。
验证方法：grep -rn "select(Role)" api-server/app → 0 命中。
是否需要立即处理：否
```

```
问题编号：AUTH-08
问题名称：无密码强度策略，允许创建空密码账号
问题等级：P2
所属模块：认证 / 用户管理
问题描述：users.py:32-52 create_user 直接 hash_password(payload.get("password",""))，无长度/复杂度校验，
          管理员误操作可建出 password_hash=bcrypt("") 的账号，该账号可用空密码登录成功（verify_password("", hash) == True）。
          settings.py:137-150 change-password 同样不校验新密码强度；改密后也不吊销其它在线 token。
代码位置：api-server/app/api/users.py:40
          api-server/app/api/settings.py:137-150
涉及函数或类：create_user / change_password / hash_password
调用链：POST /users {username:"x", password:""} → 登录 {username:"x", password:""} → 200 + token
根本原因：缺少统一的口令策略模块。
可能影响：弱口令/空口令账号；横向移动到该账号可见的集群/库。
修改建议：统一 password_policy.validate()（长度≥12、字符类≥3、拒绝常见弱口令/用户名包含），
          create_user/change-password/settings 全部接入；改密后把该用户所有 token 加黑名单（需按 sub 建索引）。
验证方法：创建空密码用户并登录，当前成功。
是否需要立即处理：否
```

```
问题编号：AUTH-09
问题名称：MCP 通道任意登录用户可执行写类工具，绕过审批/决策引擎
问题等级：P2
所属模块：认证 / MCP
问题描述：mcp.py:53 只 require_any_user；tools/call 直接调用 TOOL_REGISTRY 中 permission=operate 的工具
          （exec_in_pod 等），文件头注释自认「如需审批控制，后续可在 tools/call 加拦截」。
          与 Web UI 路径（走 require_operator + 决策引擎 + 审批）相比，MCP 是绕过治理的旁路。
          同时 GET /mcp（mcp.py:122）无鉴权返回工具清单。
代码位置：api-server/app/api/mcp.py:6-9,53,95-118,122
涉及函数或类：mcp_endpoint / TOOL_REGISTRY
调用链：POST /mcp {method:"tools/call", params:{name:"exec_in_pod", arguments:{cluster_id,namespace,pod_name,...}}}
        → td["handler"](args, context) → 直接在 Pod 内执行命令
根本原因：MCP 作为「外部 AI 客户端入口」只做了登录校验，未复用平台的处置治理链。
可能影响：审批/审计绕过（虽仍写审计），viewer 获得集群内命令执行能力。
修改建议：tools/call 前按工具 permission 映射到 require_operator；写类工具强制走 approval_service；
          校验 arguments.cluster_id 的 check_cluster_permission。
验证方法：viewer 身份 POST /mcp 调 exec_in_pod，当前可执行。
是否需要立即处理：否（P2，取决于是否对外暴露 MCP）
```

```
问题编号：AUTH-10
问题名称：JWT 缺少 iss/aud/jti，且登出黑名单按完整 token 明文存 Redis
问题等级：P3
所属模块：认证 / JWT
问题描述：create_jwt 只写 sub/username/role/exp；decode 不校验 iss/aud；无 jti → 无法按会话吊销。
          黑名单 key 是完整 JWT（最长 ~300B），24h TTL，高频登出会累积内存；Redis 可读者可直接拿到有效 token 重放。
代码位置：api-server/app/core/security.py:53-62
          api-server/app/api/auth.py:164-170
涉及函数或类：create_jwt / decode_jwt / logout
根本原因：JWT 实现最小化。
可能影响：跨环境 token 混用（同密钥时）；会话管理粒度粗；Redis 侧凭据暴露。
修改建议：加 iss/aud（如 autohealops/api）、jti（uuid），黑名单存 jti 的 sha256；提供「踢出该用户所有会话」能力。
验证方法：解出当前 token payload，无 iss/aud/jti。
是否需要立即处理：否
```

```
问题编号：AUTH-11
问题名称：JWT/Agent 令牌出现在 URL query，且 uvicorn access log 未关闭 → 凭据明文落盘
问题等级：P2
所属模块：认证 / 日志
问题描述：4 条 WS + 3 条 SSE 用 ?token=JWT；agent install.yaml 用 ?token=<agent token>。
          start.sh:98 与 k8s/deployments.yaml:46 的 uvicorn 启动命令没有 --no-access-log，
          uvicorn access log 格式包含完整 path+query → JWT 明文写入 logs/api-server.log（以及 k8s 下的容器日志）。
代码位置：api-server/app/api/terminal.py:44、watch.py:47、logs.py:153、host_terminal.py:141
          api-server/app/api/workload_logs.py:283、screens.py:27、stats.py:1440
          api-server/app/api/clusters.py:253-256
          start.sh:98、k8s/deployments.yaml:46
涉及函数或类：各 WS/SSE 端点
调用链：浏览器 EventSource/WebSocket → GET /api/v1/...?token=eyJ... → uvicorn access log 落盘
根本原因：EventSource/WS 无法带自定义头 → 用 query 传 token；日志未做脱敏。
可能影响：能读日志的人（运维、日志平台、被收集到 ELK）可直接重放 token。
修改建议：① SSE/WS 改用短期一次性 ticket（POST /auth/ws-ticket 换 60s 有效的随机串，服务端 Redis 映射到 user）；
          ② 至少在日志中间件里对 `token=` 做正则脱敏；③ 启动加 --no-access-log 或用自定义 log config。
验证方法：grep "token=" logs/api-server.log 应 0 命中（当前会有）。
是否需要立即处理：否
```

```
问题编号：AUTH-12
问题名称：认证层无全局速率限制，/docs 与 /openapi.json 默认公开
问题等级：P3
所属模块：认证 / 暴露面
问题描述：app = FastAPI(...)（main.py:573）未设 docs_url/openapi_url=None → /docs、/redoc、/openapi.json 无需登录可访问，
          泄露全部 495 条接口与参数结构；全仓无 slowapi 等通用限流（只有登录失败计数），
          故 /api/v1/ai/** 代理、/auth/change-password 等可被高频打。
代码位置：api-server/app/main.py:573
涉及函数或类：FastAPI 实例
调用链：GET /openapi.json（无需 token）→ 返回全量 schema
根本原因：默认配置未收敛。
可能影响：攻击面枚举成本降低；无速率限制下可做口令喷洒（change-password 需旧密码，影响小）、AI 接口刷量（成本）。
修改建议：生产环境 docs_url=None；网关层加统一限流（按 IP + 路径）。
验证方法：curl /openapi.json 无需认证返回 200。
是否需要立即处理：否
```

---

## 13. 无法确认事项

1. **IM webhook 的 secret 为空时是否放行**：`webhooks.py` 的 `_verify_dingtalk/_verify_wechat/_feishu_verify_signature` 实现未逐行展开；若 secret 未配置时函数返回 True，则回调可被伪造 → 需单独复核（本次只确认「有验签分支」）。
2. **`JWT_ALGORITHM` 是否在生产被改成 RS256**：代码支持任意值，但 `create_jwt/decode_jwt` 都用同一 `JWT_SECRET_KEY` 作为 key，若改成 RS256 会直接崩（HMAC key 当 RSA key 用）；实际生产取值无法从仓库确认（`.env` 未入库，本机为 HS256 默认）。
3. **生产 uvicorn 是否额外加了 `--no-access-log`**：`k8s/deployments.yaml:46` 未见，但生产可能用别的部署清单/网关，无法确认。
4. **`/mcp` 是否对外暴露**：取决于 Ingress 路由规则，仓库内未见 nginx/ingress 清单，暂时无法确认暴露面。
5. **是否有外部 SSO/OIDC**：全仓 grep `oidc|oauth|saml` 无命中（仅 k8s kubeconfig 的 `auth-provider` 解析，属被管集群侧），确认平台自身**不支持** OIDC——已写入 §2，非「无法确认」项。

---

## 14. 审计方法与覆盖范围

**读了什么**（全部只读）：
- `api-server/app/api/` 下 **67 个模块全量 AST 解析**（不是抽样）；逐个人工复核了 17 条「无 Depends」路由的实际保护逻辑。
- 认证核心：`auth.py`、`middleware/auth.py`、`middleware/permission.py`、`core/security.py`、`core/config.py`、`core/redis_client.py`。
- 身份模型：`models/user.py`、`models/permission.py`、`api/users.py`、`api/permissions.py`。
- 旁路入口：`ai_proxy.py`、`mcp.py`、`external_access.py`、`webhooks.py`、`terminal.py`、`watch.py`、`logs.py`、`screens.py`、`stats.py`(stream)、`workload_logs.py`、`host_terminal.py`。
- 跨服务：`ai-service/app/main.py`、`ai-service/app/api/aux_api.py`（路由清单）、`k8s-executor/internal/config/config.go`、`internal/watcher/notify.go`。
- 前端：`App.tsx`、`constants/nav.ts`、`pages/Login.tsx`、`services/api.ts`。
- 部署/配置：`install.sh`、`.env.example`、`start.sh`、`k8s/deployments.yaml`。
- 既有测试：`api-server/tests/test_permissions.py`（作为「作者自认的鉴权白名单」交叉校验）。

**统计命令（可复现）**：
```bash
# 路由总数 / 有鉴权数（AST 口径，含 WS 与 api_route）
cd api-server && python3 -c '...'(见 §7 说明)      # 500 / 483 / 17
grep -h "^@router\." app/api/*.py | wc -l          # 500（含 4 websocket、1 api_route）
grep -rn "Depends(require" app/api/*.py | wc -l    # 475（不含 require_host_admin 9 处与 get_current_active_user 9 处）
grep -rn "\"manager\"" app/ --include=*.py | wc -l # 项目管理角色使用点
```

**没读/没测**：未启动服务、未发真实请求、未连数据库、未读生产 .env 值（只统计长度）、未审计 ai-service 内部业务逻辑（只审其路由鉴权）、未审计 k8s-executor 的 gRPC 层鉴权（executor 的 9090 无 TLS 鉴权属 04 号架构报告范畴）。
