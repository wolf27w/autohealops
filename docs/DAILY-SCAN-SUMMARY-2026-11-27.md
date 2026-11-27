# AutoHealOps · 2026-11-27 扫描结果与功能 / 完成度汇总

> **生成**：本次会话汇总，2026-11-27
> **代码基线**：`main @ b3b9f03`（今日唯一提交：诊断报告详情三段折叠 + 独立滚动）
> **数据来源**：`docs/architecture/audit/01~23`（2026-11-27 只读审计，17 份模块报告 + 6 份结论报告，12,129 行）、`docs/REQUIREMENTS-TODO-2026-11-23.md`（2026-11-25 核实口径）、`docs/ROADMAP.md`（10-28 旧口径）、`git log`、本次会话源码复核
> **口径**：文中数字均为上述审计实测或文档原值，本文件只做归纳，不新增审计结论。`docs/architecture/` 目前在 git 中为**未跟踪**状态。
> **本次动作**：仅新建本汇总文件，未修改任何业务代码、配置、迁移或 YAML。

---

## 0. 一句话结论

平台形态是 **「多集群 K8s 运维台 + 单机/多库运维台 + AI 诊断建议器」做得扎实，「多租户平台 + 强制审批执行器 + 研发闭环」仍是半成品**。功能密度高、边界薄：审计定性为「能力覆盖很宽、工程完成度中等、安全边界基本没有」，**当前不适合直接暴露到生产网络**。

需求台账口径下，主体功能已开发完毕，真正未开发的只剩 **R17（数据库 CDC）** 与 **R41（vm-app 虚拟机应用域）** 两项大工程；但审计口径下仍有 **230 条问题（17 个独立 P0）** 与 **28 项产品阶段缺口** 未收敛。两套口径回答的是不同问题，需分开看。

---

## 1. 今日扫描结果（2026-11-27 只读审计）

### 1.1 实测规模

| 指标 | 数值 |
|---|---:|
| 源码文件 / 行数 | 约 807 文件 / 16.05 万行 |
| 分服务行数 | api-server 88,121 · frontend 35,102 · k8s-executor 28,750 · ai-service 5,971 |
| HTTP/WS 路由 | 500 条（495 HTTP + 4 WS + 1 catch-all） |
| 路由鉴权 | 零鉴权 2 条 · 有角色依赖 483 条 · 手写鉴权 15 条 |
| 数据模型 / 迁移 | 87 个模型 · 124 个 alembic 版本（单 head） |
| 后台常驻协程 | 约 26 个，无 leader 选举 |
| 写操作审计覆盖 | 约 9%（25 处 / 285 个写路由） |
| 审计报告 | 23 份正式报告 + 1 模板，合计 12,129 行 |

### 1.2 问题分级（20 号报告逐条比对）

| 等级 | 条数 | 占比 |
|---|---:|---:|
| P0 | **21** | 9.1% |
| P1 | 66 | 28.7% |
| P2 | 102 | 44.3% |
| P3 | 41 | 17.8% |
| **合计** | **230** | 100% |

> 21 条 P0 存在跨报告重复（9090/9096 各被 3 份报告记录），**去重后为 17 个独立 P0 缺陷**。

### 1.3 17 个独立 P0 按攻击面归类

| 类别 | 代表问题 | 证据 |
|---|---|---|
| 执行面裸奔 | `:9090` gRPC 无认证 + reflection 且 compose 发布宿主机；`:9096` 明文网关可 `IssueClientCert` 签发任意集群证书；pull 模式 `:9100` 令牌可空 | `cmd/executor/main.go:95-109`、`docker-compose.yml:22-23`、`agent_gateway.py:361-406` |
| 越权与隔离 | YAML `metadata.namespace` 覆盖鉴权 namespace；namespace 留空退化为集群级读；Secret 被解码为明文 `stringData`；8 个业务域无 project 过滤 | `pod_list_events.go:461-472`、`user_permission_service.py:56-58` |
| 审批可绕过 | `/batch` 任意 operator 对任意集群执行任意计划；加签/转办不校验操作人；DB 审核 `kind` 自填 + 无四眼 | `api/batch.py:14-31`、`api/approvals.py:132-154`、`db_approval_service.py:155-179` |
| 凭据与密钥 | `X-Internal-Token` 复用 `JWT_SECRET_KEY`（泄露即可伪造 admin JWT）；默认 `change-me` 无启动校验；agent 令牌明文写入 Deployment args | `ai_client.py:28`、`ai_proxy.py:23`、`.env.example:13` |
| 任意执行 / SSRF | MCP `tools/call` 直调 handler 绕过 `allow_operate`；`custom-tasks` script 类型任意 shell；`registry/tags` 反射 SSRF 并外泄全局仓库凭据 | `api/mcp.py:87-110`、`scheduler_service.py:82-83`、`registry_service.py:55-80` |
| 数据库只读绕过 | 只读仅靠 SQL 首词白名单，`WITH ... DELETE`、`SELECT ... INTO OUTFILE`、`FOR UPDATE` 全放行 | `dialects/mysql.py:30-38` |

### 1.4 能力盘点（18 号报告，204 条能力）

| 实现程度 | 条数 | 生产可用性 | 条数 |
|---|---:|---|---:|
| 已实现 | 133 | 可用 | 72 |
| 部分实现 | 26 | 有条件可用 | 70 |
| 完全未实现 | 38（含 1 条有意不实现） | 不可用 | **61** |
| 逻辑不完整 | 3 | 不适用 | 1 |
| 仅前端 / 仅后端 / 仅建表 | 4 | | |

> **最值得警惕的一组：23 条「有代码但生产不可用」**——写了、能编译、有路由，但链路被绕过或存在 P0 缺陷（不可用 61 减去 38 条完全未实现）。

### 1.5 测试实测（16 号报告，今天真实跑过）

| 服务 | 用例 | 结果 |
|---|---:|---|
| api-server（Python） | 1,190 | 1,189 通过 / 1 skipped，3.1s |
| ai-service（Python） | 100 | 全部通过，0.14s |
| k8s-executor（Go） | 28 | 25 passed / 3 skipped（需真实集群或 DB） |
| frontend | 0 | 无测试框架；`npx tsc --noEmit` 通过 |

关键空白：`approval_service.py`（审批→执行）、`audit_service.py`（哈希链）、`rbac_service.py`、`db_approval_service.py`、`action_verify.py` **5 个核心模块 0 测试引用**；97 个测试文件仅 1 个用 `TestClient`，494 条路由 HTTP 层未验证；`scripts/ci_check.sh` **不跑 `go test`**，无覆盖率门禁；`ai-service/.venv` 与 `requirements.txt` 版本大面积漂移（fastapi 0.141.1 vs 0.115.0 等），本地通过不代表生产依赖组合。

### 1.6 八个系统性根因（比单点漏洞更重要）

1. 没有统一执行网关：变更能力散落在 **8 条平行入口**，审批只覆盖「AI 生成修复计划」一条。
2. 认证只做「用户→API」，没做「服务→服务」：9090/9096 裸奔 + 内部令牌复用 JWT 密钥同源。
3. 权限判定分散：66 个路由模块仅 17 个做资源级校验。
4. 审批只绑单不绑参：存计划文本、执行时重取 plan，缺参数哈希/签名。
5. 多租户是「授权代理」不是「数据分区」：无行级过滤中间件。
6. AI 护栏是软约束：证据校验异常即放行、降级路径无护栏。
7. 审计覆盖 9%，26 个后台协程无 leader。
8. 测试集中在纯函数，审批/权限/危险动作/审计 0 测试，前端 0 测试。

---

## 2. 代码与功能盘点

### 2.1 服务与技术栈

| 模块 | 技术 | 职责 |
|---|---|---|
| `frontend/` | React 18 + TS + Vite 5 + AntD 5 + react-query + axios | 148 个源文件，`App.tsx:128-230` 集中路由；`/screen` 大屏为 980 行独立 HTML（不进构建） |
| `api-server/` | Python 3.11 + FastAPI + SQLAlchemy2(async) + Alembic | 唯一对外入口：66 路由模块 / 499 端点 / 约 150 个 service / 90+ 模型 |
| `ai-service/` | FastAPI + OpenAI/DeepSeek/Claude 适配 | 多模型降级与仲裁、ReAct 决策、Prompt 版本灰度、RAG、Embedding、SQL/JVM 分析 |
| `k8s-executor/` | Go + gRPC + client-go | 约 50 个 RPC（3 个流）、Pod/Node watcher、命令执行/回滚、批量、配额、Prometheus 代理、日志流 9099 |
| `agent/` + `cmd/agent` | Go 单二进制 + gRPC 双向流 | 集群内 agent，mTLS 反向隧道至 `agent-gateway` 9095 → 9096 |
| 基础设施 | MySQL（平台库，executor 双写）、Redis（锁/缓存/黑名单）、可选 ES / Prometheus | 无消息队列，用 Redis 锁 + DB 轮询 + HTTP 回调替代 |

### 2.2 功能模块（按业务域）

1. **集群接入**：kubeconfig 导入/校验/多 context、AES-256-GCM 加密存储（Python↔Go 互测）、直连 executor 与 agent-only 两套拓扑、agent 一键接入与版本升级、集群巡检、健康度。
2. **K8s 查询与变更**：Namespace/Pod/Deployment/STS/DS/Job/Node/Service/Ingress/PVC/PV/CM/Secret/HPA/Events 查询；日志 tail + WS/SSE 跟随 + 下载；扩缩容、滚动重启、回滚、镜像更新、YAML apply、删除、容器内 exec、交互终端、节点 cordon/drain/label/taint。
3. **故障检测与告警**：watcher 识别 CrashLoop / OOM / ImagePull / Evicted / Pending / TerminatingStuck；指纹去重、5 分钟窗口聚合、静默/抑制、指派/ack/评论、恢复通知、转工单、三通道通知（钉钉/企微/飞书 + 验签 + 限频）。
4. **AI 诊断**：ReAct 多轮自主调查（默认 8 轮 / 硬上限 16 / 90s）、22 个注册工具、规则确定性兜底、证据幻觉护栏、探针 exitCode 校正、Prompt 模板/片段/路由/版本 diff/dry-run、RAG 检索与三级去重、知识库管理、诊断过程回放。
5. **决策 → 审批 → 执行 → 验证**：四维风险评分（操作/资源/环境/影响）→ `auto / approval_1 / approval_2`；autonomy_gate 置信度闸门；单人/双人/IM 审批、超时提醒、定时执行；6 个平台动作注册表；灰度分批、熔断、逆序回滚、dry-run、白名单；动作级验证、失败二次诊断、复发转工单、结果沉淀知识库。
6. **主机 / VM**：主机 CRUD 与分组、凭据托管（不回显）、SSH 采集、Web 交互终端、会话录制（DB chunks + asciinema cast）与回放、行级命令审计、危险命令拦截、SFTP 浏览/上传/编辑。
7. **数据库**：MySQL/PG/Oracle 多方言、数据源纳管、只读账号、SQL 执行 + 428 审核信封、审核单、表结构设计器、导出/整库备份/导入、慢 SQL 摘要与明细采集、EXPLAIN + AI 优化建议、健康指标与异常判定。
8. **平台治理**：JWT + 四角色 RBAC（前端 3 角色）、项目/集群/namespace/DB 授权、SHA256 审计哈希链 + trace_id、APScheduler + Redis 锁定时任务、报表定时推送、值班日历、工单、发布对接（Jenkins 遥控器）、MCP JSON-RPC、仪表盘与大屏。
9. **工程化**：`install.sh` 本地/Docker 双模式、compose 4 服务、K8s 15 份清单（HPA/PDB/NetworkPolicy/备份 CronJob）、`make crypto-test` 跨语言加密互测、`scripts/ci_check.sh` 8 步校验、备份恢复与日志轮转、数据保留策略。

### 2.3 名义存在 / 伪能力（19 号报告，15 条；此处列宣传风险最高的 8 条）

| 能力 | 表面 | 实际 |
|---|---|---|
| 多租户隔离 | 项目菜单、成员页、筛选 | 业务表无 `project_id`，8 个域任何登录用户看全平台 |
| 强制审批 | 审批中心、双人、IM 审批 | 8 条平行入口不审批；加签无校验；DB 审核可自批 |
| 审计防篡改 | 哈希链 + `/audit/verify-chain` | 25/285 写路由有审计；更新记录后链必断 |
| 云原生 RBAC / impersonation | 集群页显示「已接入」 | `ImpersonatedClient()` 全仓无调用方，仍用 cluster-admin SA |
| API Key 开放接口 | 用户页「生成 API Key」 | 只生成落库，无任何 `X-API-Key` 校验，key 永远无效 |
| 数据库只读查询 | 只读账号配置项 | 未配置时静默回落主账号；只读靠 SQL 首词白名单 |
| 灰度 / 金丝雀发布 | `gray_release.go` | 实为修复计划分批执行 + 熔断，与流量切分无关 |
| VM 管理 | 菜单「主机/VM」、`kind=vm` | 无创建/快照/迁移/销毁，只把 VM 当 SSH 主机纳管 |

其余：MCP 写审批、Agent 心跳与在线时长、agent 网关 remote 模式、容器内「只读」执行、角色权限点管理、审批超时自动升级（无终态）、in-cluster 一键接入。

---

## 3. 已完成

### 3.1 需求台账口径（以 `REQUIREMENTS-TODO-2026-11-23.md` 2026-11-25 核实为准）

- **已开发 25 项**：R53、R60-9、R60-P3（L-b 镜像层内容对比，Harbor 真实镜像验证）、R61–R74、R75（诊断质量与修复闭环 8 个子项：知识库参考注入、模型降级可观测、补丁建议、复发判定、平台动作风险评分、STS/DS 回滚覆盖、证据缺口显式化、诊断深度结构化）。
- **AI Agent 化**：R68–R73 全部完成（故障统一入口、通用诊断循环、动作库、自主处置三条件闸门、用户感知判定 + 生产审批、执行验证 + 结果沉淀）。
- **代码尾巴清零**：Oracle 方言 export/import/backup（33 单测）、STS/DS 发布变更检测 + 回滚 kind 透传、proto 同步、OpenAPI 契约、配置覆盖度。
- **M4 / D2**：改为保留策略（`scripts/retention_purge.py` + `k8s/retention-cronjob.yaml`，quota 30d / slow_sql 90d），测试集群 03:30 CronJob 已建。
- **R49 多副本 + P4 网关对外**：2026-11-24 测试集群实测通过（2 副本、agent 单连接 connected、60 分钟 12 条诊断零重复、日志经网关转发正常），期间修掉 3 个阻断缺陷。
- **待拍板清零**：R51-charset 定稿「只改默认、不动存量」；R53 大屏「节省人工」改为可配 `labor_minutes_per_heal`。
- **Agent-only 方向**：P1 全方法反向代理、P2 告警检测下沉、P3 in-cluster 自接入 + 版本管理、mTLS 均完成。
- **平台能力**：告警→诊断→处置→工单闭环、通知三通道、Prompt 管理面、慢 SQL 诊断、发布对接、值班 UI、仪表盘与大屏、1,314 个自动化用例、跨语言加密互测。

### 3.2 近两日提交（11-26 ~ 11-27，代表性）

AI 处置台（候选动作 + auto 直执 / approve 建单）、AI 处置授权矩阵可配、诊断空响应重试 + 上下文瘦身 + 低温决策、诊断预算耗尽三层确定性收口、故障指纹加入「故障阶段」、容器内日志文件优先取证与 OOM 专项取证包、告警恢复判定与闭环收敛、慢 SQL 明细诊断与展示优化、节点分组「未分组」、独立 uat 环境标签、工单命名空间列、集群访问 HTTP/2 GOAWAY 修复、Redis 抖动不再整站 500、**今日 `b3b9f03` 诊断报告详情三段折叠**。

### 3.3 可直接对外讲的能力（18 号报告结论）

多集群 K8s 查询与常用变更、kubeconfig 加密纳管、Agent 反向隧道接入、主机 SSH 终端 + 录像回放、数据库纳管 + 慢 SQL 分析、AI 多轮诊断 + 工具取证 + Prompt 管理、告警→诊断→处置→工单闭环、通知三通道。

---

## 4. 未完成

### 4.1 需求层面（老板视角唯一待办清单）

| 分类 | 事项 | 卡点 |
|---|---|---|
| **未开发** | **R17** 数据库同步/迁移（CDC） | 源/目标库组合、选型（自研 go-mysql / canal / Debezium）、同名冲突策略未定 |
| **未开发** | **R41** 虚拟机应用域（vm-app） | 三域 Agent 剩余部分，需先定方向 |
| **运维窗口** | **A6** 平台自身生产部署 | 部署窗口 + 备份/DR 演练 |
| **运维窗口** | **P4** 9095 网关对外落地 | NodePort/LB 决策 + `AGENT_GATEWAY_PUBLIC_ADDR` + 重启 api-server |
| **运维窗口** | R49 生产多副本部署 | 测试集群已实测，生产窗口未排 |
| **未实机验证** | Oracle 全链路导出/导入/备份 | 本机与测试集群均无 Oracle 实例，仅 33 个单测覆盖 SQL 生成/切分/转义 |
| **暂缓** | S1–S5 存储优化、M7 时区统一 UTC、阶段 8 全自主编排、R44 提示词收敛 | 库 3.4GB 未到阈值 / 无 bug / ReAct 先跑稳 / 硬路由承载确定性自愈 |

> `ROADMAP.md`（10-28）中的 A2/A3/A4 已并入 Agent-only、B2 已销项、C 组已清空、D2 已改保留策略；该文件口径已过期，README 仍称其「最新口径」，需以 11-23 清单为准。

### 4.2 审计层面（未修复）

- **17 个独立 P0 / 230 条问题全部未修复**；其中 14 个 P0 可由「一个 operator 账号」或「集群内任意 Pod」直接打穿。
- 23 条「有代码但生产不可用」能力、15 条伪能力未收口。
- 5 个核心模块 0 测试、前端 0 测试、CI 不跑 `go test`、Go 版本漂移（`go.mod` 1.26.6 vs Dockerfile 1.22）、`requirements.txt` 缺 `ldap3` / `dingtalk-stream` / `lark-oapi`。

### 4.3 产品三阶段缺口（19 号报告，合计 28 项，其中完全未实现 13 项）

- **第一阶段（15 项，平台化底座）**：LDAP 组映射与同步、用户多项目多角色可配、项目级数据分区、项目管理员管成员、VM 服务管理/日志/文件传输、DB 审核四眼、K8s 操作审批全覆盖、AI 工具统一闸门、审计完整性、Agent 幂等/取消/并发、HA/leader 选举、内部端口鉴权、密钥治理、告警值班与升级链、WS/SSE 令牌改 ticket。
- **第二阶段（8 项，研发闭环）**：代码仓接入（唯一硬前置）、CI/CD 流水线与制品、版本→代码定位、AI 生成代码级 patch/PR、测试执行、镜像构建与制品管理、灰度发布、部署后业务级验证。
- **第三阶段（5 项，安全左移）**：漏洞扫描、SAST/密钥/IaC 扫描、依赖/镜像扫描 + SBOM、AI 代码风险分析、选择性修复/自动 PR/灰度验证。

### 4.4 质量与资产债

巨型文件（`alert_auto_service.py` 3,867 行 / 单函数 1,053 行、`services/api.ts` 1,455 行）、26 个后台协程无 leader、审计 `FOR UPDATE` 串行且更新断链、同步 gRPC 靠 `to_thread`、前端无 ErrorBoundary 且 `nginx.conf` 缺 WS/SSE 升级头、130+ 份文档口径打架、`logs/executor.log` 约 875MB、961 个会话 jsonl 无清理、`data/agent-ca/*.key` 私钥落盘、多处明文凭据风险。

### 4.5 建议顺序（23 / 19 号报告）

**72 小时内**：① 换密钥 + 拆 `INTERNAL_SERVICE_TOKEN`（禁止默认值启动）→ ② 关 9090/9096/9100 暴露面 + 补 NetworkPolicy → ③ 堵 MCP / batch / custom-tasks 三条越权路径 → ④ 修审批自批与加签 → ⑤ 修 namespace 覆盖与 Secret 明文 → ⑥ DB 只读做 SQL 解析交叉校验。

**第一阶段（0–3 月，6 个 Sprint）**：安全止血 → 统一 Action Gateway + 四眼审批 → 行级项目隔离 → LDAP 多项目多角色 → Agent 可靠性 → VM 补齐 + DB 加固 + 审计结构化 + 测试补齐。
**第二阶段（3–9 月）**：前置为执行网关与审计落地；**第三阶段（9–18 月）**：前置为代码索引与构建沙箱。明确不做：第一阶段不引入消息队列 / 微服务拆分 / 多集群控制面。

---

## 5. 口径冲突提示（查阅时务必注意）

1. `README.md:157` 称 `docs/ROADMAP.md` 为「剩余需求清单（最新口径）」，实际该文件停留在 2026-10-28，已被 `REQUIREMENTS-TODO-2026-11-23.md` 取代。
2. `PROMPT-FINAL.md` 是 V1.3 开发总纲（三服务 / 19 页 / 15 项功能），与现状（500 路由、agent 隧道、DB 工作台、AI 处置台）严重脱节，属历史基线。
3. `docs/README.md`（2026-09-20 索引）把已实现项标「暂不开发」；`REQUIREMENTS-TODO-2026-11-20.md` 自列 10 处文档-代码矛盾与 5 组编号冲突（R1–R13 / S / D / M / P 各有 2~4 套定义）。
4. 需求编号当前最大为 **R75**；建议新需求一律 R 系列续编。

---

## 附录：数据来源文件

- 审计总览：`docs/architecture/audit/23-audit-summary.md`
- 风险总表：`docs/architecture/audit/20-risk-priorities.md`
- 能力盘点：`docs/architecture/audit/18-existing-capabilities.md`
- 能力缺口：`docs/architecture/audit/19-missing-capabilities.md`
- 测试实测：`docs/architecture/audit/16-test-coverage-audit.md`
- 目标架构 / 路线图：`docs/architecture/audit/21-target-architecture.md`、`22-development-roadmap.md`
- 需求台账：`docs/REQUIREMENTS-TODO-2026-11-23.md`、`docs/ROADMAP.md`、`docs/AGENT-ONLY-ARCHITECTURE.md`
- 项目说明：`README.md`、`PROMPT-FINAL.md`
