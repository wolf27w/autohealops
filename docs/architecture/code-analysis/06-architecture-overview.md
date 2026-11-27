# 06 · AutoHealOps 总体架构说明

> **代码基线**：`main @ b3b9f03`（2026-11-27 09:22）
> **方式**：只读源码检索（`grep` / `wc -l` / AST 静态统计）+ 调用链逐跳阅读；未运行构建、测试、迁移，未安装依赖，未连接数据库或集群。
> **路径约定**：相对项目根 `/Users/wolf/Documents/project/autohealops/`；缩写 `A/`=`api-server/app/`、`I/`=`ai-service/app/`、`F/`=`frontend/src/`、`G/`=`k8s-executor/`。
> **标注**：【事实】源码可直接验证 /【推断】基于证据的架构判断 /【待验证】需运行期或回归确认。
> **本文定位**：01~05 的综合视图与索引，不引入新的独立结论；所有证据位置以 01~05 为准。

---

## 0. 结论摘要

1. 【事实】系统形态是 **「模块化单体 + 卫星执行器 + 双模式 agent 接入」**：`api-server` 是唯一对外 API 与业务中枢（67 个 API 文件 / 495 个路由装饰器 / 109 个 service / 87 张表 / 123 个迁移），`ai-service` 与 `k8s-executor` 都是它的能力外延，`frontend` 只与它通信。
2. 【事实】**没有消息队列**。跨进程协作由「共享 MySQL + Redis 锁 + 常驻后台协程 + HTTP/gRPC 回调 + gRPC 双向流」拼装而成；26 处 `create_task` 集中在 `A/main.py:294-540`。
3. 【事实】数据平面有 **两条并行的执行通路**：平台直连 `executor :9090`，以及集群内 agent 经 mTLS 反连 `:9095` 的反向隧道（另有 pull `:9100`）。两者最终汇入同一个 `A/clients/executor_client.py:266-281` 路由优先级。
4. 【事实】`alerts` 是唯一明确的**双写者表**（Go watcher 直写平台库 + Python 侧合并写入），且 `ai-service` 重复定义 14 张表、proto 契约双份；跨语言一致性依赖「口头契约 + CI 校验」而非共享包。
5. 【推断】架构的主要矛盾不是「服务拆得不够」，而是**进程内分层边界未约束、进程间契约未固化**。因此演进方向是「在现有部署拓扑内收紧模块边界」，而不是引入新服务或中间件。

---

## 1. 分层视图【事实·结构 / 推断·归类】

```
┌──────────────────────────────────────────────────────────────────────┐
│ 接入层   frontend  React18 + TS + Vite + AntD                          │
│          F/main.tsx:34 → F/App.tsx:127  →  F/services/api.ts:9         │
│          nginx :80  ──HTTP /api/v1──▶                                  │
├──────────────────────────────────────────────────────────────────────┤
│ 应用层   api-server :8080（唯一对外入口 / schema owner / 调度中枢）     │
│   api/        67 文件，495 路由装饰器                                  │
│   services/  109 文件，业务编排                                        │
│   models/     59 文件，87 张表；alembic 123 版本                       │
│   clients/    ai_client.py / executor_client.py                        │
│   main.py     26 处 create_task；lifespan :544                          │
│   agent_gateway.py（inproc :9095）  ← 内嵌在应用进程内                 │
├───────────────┬──────────────────────────────┬───────────────────────┤
│ 能力层        │ 执行层                       │ 数据层                │
│ ai-service    │ k8s-executor :9090 (gRPC)    │ MySQL（平台库）        │
│ :9000 HTTP    │ G/internal/grpc/server.go    │ Redis（锁/黑名单/代次）│
│ 诊断/ReAct/   │ G/cmd/agent → :9095 mTLS     │ ES / Prometheus        │
│ Prompt/RAG    │ G/internal/watcher 直写 DB   │ 外部 LLM / IM 回调     │
├───────────────┴──────────────────────────────┴───────────────────────┤
│ 被管对象：多 K8s 集群 / VM 主机 / MySQL·PG·Oracle 实例                 │
└──────────────────────────────────────────────────────────────────────┘
```

【推断】这张图与常见「三层架构」的差别有两处：① `api-server` 同时是 API 层、业务层、调度层和网关层；② Go 执行器**反向写入**平台库（`G/internal/watcher/sink.go:137-144`），绕过了应用层。这两点是后续多数结构问题的根源（详见 02 §2、§7）。

---

## 2. 运行时拓扑与端口【事实】

| 端口 | 提供方 | 消费方 | 协议 / 认证 | 证据 |
|---:|---|---|---|---|
| :80 | frontend/nginx | 浏览器 | HTTP，静态 + `/api/v1` 反代 | `frontend/nginx.conf:14-19` |
| :8080 | api-server | frontend | HTTP，Bearer JWT | `A/main.py:573` |
| :9000 | ai-service | api-server | HTTP，`X-Internal-Token` | `A/clients/ai_client.py:28`、`I/main.py:44-48` |
| :9090 | k8s-executor | api-server | gRPC，无应用层鉴权 | `A/clients/executor_client.py:544-1407`、`G/cmd/executor/main.go:95-109` |
| :9095 | agent-gateway（inproc） | 集群内 agent | gRPC 双向流 + mTLS + token sha256 | `A/agent_gateway.py:253,396-399`、`G/cmd/agent/main.go:246-247` |
| :9096 | agent-gateway（remote 模式） | api-server | 内部 gRPC | `A/agent_gateway_main.py:22-27`、`A/main.py:551-558` |
| :9100 | agent（pull 模式） | api-server | gRPC，Bearer token | `G/internal/agentbridge/bridge.go:164`、`A/clients/executor_client.py:382-464` |
| :9099 | executor 日志流 | api-server | gRPC stream | `G/internal/grpc/server.go` |

【事实】`AGENT_GATEWAY_MODE` 决定 9095 是嵌在 api-server 进程内（inproc，默认）还是独立进程（remote）：`A/core/config.py:100`、`A/main.py:551-558`。两种模式下 `_agents` 连接表都是**进程内字典**（`A/agent_gateway.py:26-29`），并未因 remote 而外置。

---

## 3. 五个服务的职责边界（摘要，详见 01 §4）【事实】

| 服务 | 核心职责 | 核心业务对象 | 不应承担但实际承担了 |
|---|---|---|---|
| frontend | UI 与交互 | 集群/告警/诊断/审批/执行视图 | `F/services/api.ts` 单文件集中 473 个导出、20+ 业务域 |
| api-server | 对外 API、业务编排、schema owner、调度 | Alert / RepairPlan / Approval / Cluster / Ticket / KnowledgeEntry | 内嵌 gRPC 网关；api 层直接写 SQL、直调 executor |
| ai-service | AI 能力：诊断、ReAct 决策、方案、验证、RAG、Prompt | AIModelConfig / PromptTemplate / AIDiagnosisResult | 直连平台库并维护自有 ORM（`I/models/__init__.py:2`） |
| k8s-executor | K8s 动作执行、watch 故障检测、指标与日志采集 | kubeconfig / Pod / Watcher 事件 | 直接 GORM 写平台中心库 `alerts` |
| agent | 跨网络接入，集群内采集与执行 | 隧道帧、集群内采集数据 | 二进制内嵌完整 `ExecutorService`（`G/cmd/agent/main.go:142-143`） |

【推断】`agent` 与 `executor` 共用同一 Go module 与同一套 `internal/`，本质上是**同一份能力的两种部署形态**，而非两个独立服务；`agent/` 一级目录只是构建/部署物料，0 个 `.go` 文件（01 §2）。

---

## 4. 核心链路与四个收敛点【事实】

七条链路的完整「文件:行号 → 函数名 → 下一跳」序列见 03 文档。这里只给出架构上必须保护的事实收敛点：

| 收敛点 | 位置 | 汇入的入口 | 重构含义 |
|---|---|---|---|
| 告警自愈主流程 | `A/services/alert_auto_service.py:2464 _process_one` | `process_alert:3519`、`auto_process_alerts:3837`、`A/api/alerts.py:459`、Pod 重启服务 | 主链路单点，拆分必须门面化 |
| 修复执行 | `A/services/plan_runner.py:82 run_plan` | auto 路径 `:1280`、审批路径 `approval_service.py:896` | 两条审批分支在此合流 |
| ReAct 深诊 | `A/services/agent_loop.py:1108 diagnose_alert_react` | 告警 `:2864`、手动 `A/api/alerts.py:444`、工单态 `:3826` | 工具名/提示词属外部契约 |
| 集群执行路由 | `A/clients/executor_client.py:266-281` | 隧道 → pull → 直连 | 44 个文件引用，最后动 |

【事实】链路索引：A 登录与权限（03 §A）、B 集群接入与资源查询（§B）、C 告警产生到处理（§C，双来源）、D AI 诊断（§D，单轮 / ReAct 双路径）、E AI 修复（§E，12 步）、F Agent 接入（§F，16 步）、G 数据库接入与诊断（§G）。

---

## 5. 数据 ownership 与一致性模型【事实 / 推断】

| 数据域 | 写入方 | 一致性机制 |
|---|---|---|
| `alerts` | ① Go watcher `sink.go:144`；② Python `alert_merge.py:100` + `agent_alert_service.py:88` | 两侧各自去重（Go `db.Where().First` / Python 30 分钟窗口合并），**无统一约束** |
| 业务配置 / 模型 / Prompt | api-server 为主，ai-service 可写（重复 ORM） | 无契约测试，靠人工同步字段 |
| 事件 / 指标快照 | executor / agent 上报 → `agent_alert_service.py:239` / `eventstore` | 隧道帧 + req_id 关联 |
| 其余业务表 | api-server | alembic 123 版本管理 |

【事实】`models/` 全目录及全部迁移中 `ForeignKey` / `relationship` / `back_populates` **零匹配**，87 张表靠裸整型 id + 手写 join（04 §5.2）；schema 使用 STORED 生成列、`JSON_EXTRACT`、`ON DUPLICATE KEY UPDATE`，强绑 MySQL。

【推断】跨语言一致性目前靠三件东西维系：共享 `DATABASE_URL`、共享 `ENCRYPTION_KEY` / `JWT_SECRET_KEY`、以及 `scripts/ci_check.sh:28-33` 的 proto 一致性校验。**这不是契约，而是约定**；任何字段或枚举变更都需人工同步 4 处（Python 模型、ai-service ORM、迁移、Go GORM 结构体）。

---

## 6. 隐式状态与跨服务契约【事实】

| 类型 | 位置 | 架构影响【推断】 |
|---|---|---|
| 进程内连接表 / 无界队列 | `A/agent_gateway.py:26-29,92-95` | inproc 模式无法多副本共享；慢 agent 下请求无限堆积、无背压 |
| RAG 内存索引 | `I/services/rag_service.py:24` | 多副本各自持有，TTL 内可能脏读 |
| 缓存代次（ORM hook → Redis → SSE） | `A/core/orm_hooks.py:22,32,58` → `A/api/stats.py:268,1469-1477` | 写入与推送通过 Redis 隐式耦合 |
| watcher 内存去重表 | `G/internal/watcher/pod_watcher.go:76-78,521` | 重启丢失、多实例不一致 |
| 内部令牌 = JWT 签名密钥 | `A/clients/ai_client.py:28`、`A/api/ai_proxy.py:23`、`G/internal/config/config.go:66` | 服务间信任与用户身份共用一把密钥 |
| 状态字符串字面量 | `ticket_service.py:455,547,701`、`db_approval_service.py:119,143,251` 等 | 跨语言靠字面量对齐，无编译期保证 |
| 应用层心跳缺失 | `G/cmd/agent/main.go:352-353` 只有 `ping→pong`，`A/agent_gateway.py:318-319` 只忽略 `pong` | 【推断】ping/pong 为遗留死代码，实际保活靠 HTTP/2 keepalive |

---

## 7. 架构特征判断【推断】

**可判定为刻意设计的部分**（有明确代码结构与配置开关支撑）：
- 单体集中编排 + 卫星执行器：跨语言边界放在 gRPC 而非共享库（`A/clients/executor_client.py`、`G/proto/executor.proto`）。
- 执行面安全机制：灰度 / 熔断 / 逆序回滚 / dry-run（`A/services/plan_runner.py:82-145`）、决策四维评分与审批分流（`A/services/decision_engine.py:196,241`）、审计 SHA256 哈希链（`A/models/audit.py:11-26`）。
- 双模式接入：push 隧道 / pull 直连 / inproc / remote 均有配置开关与独立启动路径。
- 兼容优先：`_TunnelAwareStub`（`A/clients/executor_client.py:249-373`）用动态反代把 4 套传输对上层收敛为同一接口。

**更接近自然生长的部分**（无统一设计、靠复制与延迟 import 维系）：
- api 层越层：49 个文件直接写 SQL、12 个文件直接调 executor、53/67 个文件内联业务规则（02 §2）。
- 两组 Python 循环依赖靠函数内延迟 import 规避（02 §3）。
- 5 份 `_deny_cluster`、2 份 428 审核信封、5 对跨服务同名 core 模块、双份 proto 与 ORM、2 个前端孤儿页面、3 个网关残留文件（01 §6、04 §4）。
- `alerts` 双写、ping/pong 死分支、123 个迁移中的 `add → drop → add` 组。

【推断】这一区分对重构很关键：**前者应保护，后者才应治理**。例如 `_rt_store/_load/_refresh/_cached` 缓存四件套（`A/api/resources.py:100-167`）虽显啰嗦，但属于合理抽象，不建议为减行数改动（04 §4）。

---

## 8. 不可破坏的外部契约（重构边界）【事实 / 推断】

| 契约 | 证据 | 破坏后果 |
|---|---|---|
| HTTP 路由路径 / 方法 / 响应结构 | `A/main.py:671-737` 平铺注册 66 个 router | 前端 473 个导出方法全部受影响 |
| 428 审核信封字段 | `A/api/db_connections.py:420` → `F/services/api.ts:47` | 前端解包失败 |
| JWT payload 与角色名 | `A/core/security.py:53`、`A/middleware/permission.py:9,23-25` | 鉴权失效 |
| 工具名字符串 / 工具清单 | `A/services/tool_registry.py:1294 TOOL_REGISTRY`、`:1759 list_tools` | 进入 LLM 提示词与 agent 能力协商 |
| Prompt 模板与版本灰度 | `I/services/diagnosis_service.py:138-153` | 诊断行为漂移 |
| gRPC RPC 名与 proto 字段 | `G/proto/executor.proto`、`A/proto/` | 跨语言调用失败 |
| DB schema / 迁移链 | `A/models/`、`alembic/versions/` 123 版本 | 数据不可用 |
| 部署拓扑与端口 | `docker-compose.yml`、`k8s/`、`install.sh` | 运维体系失效 |

【推断】05 文档所有拆分方案之所以统一采用「原路径门面 + 再导出」，正是为了在不触碰上述契约的前提下改变内部模块边界。

---

## 9. 现状架构对演进的约束【推断】

1. **调度与网关同进程**：26 个后台协程与 inproc 网关共存于 `api-server`，任何进程级改动（多副本、外置连接表）都必须先解决状态共享，不能只改配置。
2. **先补回归网再动主链路**：审批→执行、审计哈希链、RBAC、AI 修复验证、Agent 隧道（Python 侧）均为 0 覆盖，前端 0 测试（04 §6）；`_process_one` 的 1,053 行拆分若没有行为快照，风险不可控。
3. **高波及面模块后置**：`executor_client`（44 文件引用）与 `models/`（59 模型 + 247 行重复 ORM + 123 迁移）应留到回归网完备后（02 §8）。
4. **跨服务契约固化是长线事项**：共享包 / 契约测试涉及 Python 与 Go 两侧同步，属于 P3，且需运行期验证环境（05 §1）。

---

## 10. 目标架构（演进后，部署拓扑不变）【推断】

```
frontend ──HTTP──▶ api-server
                    ├── api/          薄路由（协议适配 + 依赖注入鉴权）
                    ├── services/     按域内聚，orchestrator 只做阶段编排
                    │   └── alert/    orchestrator / gates / recovery / kb /
                    │                 ticket / result_writer（切断环）
                    ├── clients/      ExecutorClient 门面 + transport 策略
                    ├── models/       schema owner 不变
                    └── main.py       调度注册收敛、可观测
                 ──HTTP──▶ ai-service（契约包共享枚举与分类）
                 ──gRPC──▶ k8s-executor / agent（proto 单一来源）
```

【推断】目标态与现状的服务数量、端口、协议、表结构**完全一致**；差别只在进程内的分层约束、循环依赖消除、重复实现收敛与回归测试覆盖。这与 05 §0 的六条硬约束一致。

---

## 11. 文档索引与阅读顺序

| 文档 | 回答的问题 | 建议读者 |
|---|---|---|
| 01-project-structure.md | 目录是什么、谁负责什么、有哪些遗留 | 首次接手项目 |
| 02-module-dependencies.md | 改一个模块会波及谁、环与隐式状态在哪 | 准备动手重构前 |
| 03-core-business-flows.md | 七条链路的真实调用序列与收敛点 | 排查问题 / 评估改动影响 |
| 04-code-quality-issues.md | 巨型文件、复杂函数、重复代码、模型与测试缺口 | 制定治理清单 |
| 05-refactoring-recommendations.md | 分批方案、三个优先模块、收益/风险/工作量 | 排期与评审 |
| 06-architecture-overview.md（本文） | 总体视图、契约边界与索引 | 汇报 / 对齐口径 |

【事实】六份文档均以同一基线 `b3b9f03` 为准，且均为**本次新增**，未覆盖 `docs/architecture/audit/01~23` 与任何既有文档。审计文档与本次分析是两套互补口径：前者回答「能不能上生产」，本组回答「代码是怎么组织的、以后怎么改」。

---

## 12. 待验证事项汇总（合并 01~05）

1. `ex.GetPodLogs` 的 Go 实现文件、`circuit_triggered` 触发条件与灰度比例（03 §B / §E）。
2. `process_alert` 的调度接线与常驻循环的关系；`rag_service.add_knowledge` 内部写入逻辑（03 §C / §E）。
3. 两个前端孤儿页面是否刻意保留待回迁；remote 网关模式生产是否启用；5 对同名模块是否已造成语义漂移（01 §8）。
4. 是否还有第三组循环依赖（本次仅解析 `from app.services.X import` 形式）；`system_setting_service._cache` 多副本一致性（02 §10）。
5. `alerts` 双写在并发下是否产生重复行；97 处宽 `except Exception` 中哪些掩盖修复失败（02 §10、04 §8）。
6. 前后端枚举是否已实际漂移；37 个「tests 中 0 次提及」的 service 是否被路由层间接覆盖（04 §8）。
7. `SqlTerminal.tsx` 与 Go 方法的粗估行数需精确解析器复核；`cluster_guard` 依赖化的注入时机等价性；前端桶文件的同名导出冲突（04 §8、05 §8）。
8. 全部工作量估算（05 §1）基于静态规模，未计入真实回归环境与联调时间；本次未执行任何构建或测试，不能据此推断运行期行为。
