# 03 · 核心业务流程与真实代码调用链

> **代码基线**：`main @ b3b9f03`　**方式**：逐跳阅读源码（非按文件名推断）；未运行任何代码
> **路径缩写**（均相对项目根 `/Users/wolf/Documents/project/autohealops/`）：
> `A/` = `api-server/app/`　`I/` = `ai-service/app/`　`F/` = `frontend/src/`　`G/` = `k8s-executor/`
> **标注**：【事实】= 逐文件 grep/read 确认；【推断】= 未逐行核实但证据指向明确；【待验证】= 需运行期确认

---

## 0. 链路总览与收敛点

【事实】七条链路存在 4 个事实上的收敛点，重构时应优先保护：

| 收敛点 | 位置 | 汇入的入口 |
|---|---|---|
| 告警自愈主流程 | `A/services/alert_auto_service.py:2464 _process_one` | `process_alert:3519`、`auto_process_alerts:3837`、`A/api/alerts.py:459`、`pod_restart_service.py:384` |
| 修复执行 | `A/services/plan_runner.py:82 run_plan` | auto 路径 `alert_auto_service.py:1280`、审批路径 `approval_service.py:896` |
| ReAct 深诊 | `A/services/agent_loop.py:1108 diagnose_alert_react` | 告警 `:2864`、手动 `A/api/alerts.py:444`、工单态 `:3826` |
| 集群执行路由 | `A/clients/executor_client.py:266-281` | 隧道 → pull → 直连，所有 K8s 动作共用 |

---

## A. 用户登录与权限验证

【事实】调用序列：

1. `F/pages/Login.tsx:12 onFinish` → `F/services/api.ts:82 login` → `POST /auth/login`
2. `F/services/api.ts:31 api.interceptors.request.use` 注入 `Authorization: Bearer`；`:40` 响应拦截器 `code!==200` 全局报错、`:47` 428 转「需审核」异常、401 → `promptLogin():16`
3. `A/api/auth.py:21 login` → `:39 verify_password`（`A/core/security.py:45 bcrypt.checkpw`）或 `:28 _ldap_login`（`:64`，服务账号绑定 `:102` + 用户 DN 二次绑定 `:124`）
4. `A/api/auth.py:47` → `A/core/security.py:53 create_jwt`（`jwt.encode`，payload `sub/username/role/exp`）；解码 `:60 decode_jwt`
5. 请求到达 → `A/middleware/auth.py:19 get_current_user` → `:30` 查 Redis `token_blacklist:{token}`（Redis 故障 `:34` 降级放行）→ `:37 decode_jwt` → `:41 select(User)` → `:50 get_current_active_user`
6. → `A/middleware/permission.py:9 require_role` / `:25 require_any_user`（`:23 require_admin`、`:24 require_operator`）
7. 资源级：`A/api/resources.py:31 _deny_cluster` → `A/services/user_permission_service.py:47 check_cluster_permission`（直接授权 ∪ 项目授权取最高）/ `:91 check_db_permission`；`A/api/service_accounts.py:16` 是同构复制

旁支【事实】：登出黑名单 `A/api/auth.py:169 logout`（Redis `SET ex`）；外部令牌 `A/api/external_access.py:26 _verify_token`（`X-External-Token` + `compare_digest`）；IM 回调验签 `A/api/webhooks.py:65 _verify_dingtalk`、`:75 _verify_wechat`、`:56 _feishu_verify_signature`，入口 `:102/:127/:145`。

【事实·跨层】`A/api/auth.py:36` 与 `:134` 在路由函数内直接 `async_session()` 查/建 User，未经过 user service。

---

## B. Kubernetes 集群接入与资源查询

【事实】接入：

1. `A/api/clusters.py:160 import_multi` / `:213 agent_onboard` / `:661 create_cluster`
2. → `A/core/security.py:20 encrypt_aes256_gcm`（调用点 `clusters.py:185/:234/:686`）
3. → `A/models/cluster.py:20 kubeconfig_encrypted` 落库
4. agent 接入分支：`clusters.py:195 _hash_token`、`:200 _new_agent_token` → `:235 Cluster(source="agent", agent_token_hash=...)` → `:259 agent_install_yaml` 渲染安装清单

【事实】查询：

1. `A/api/resources.py:396 pods` / `:547 pod_logs` / `:580 pod_yaml` → `Depends(require_any_user)`
2. → `resources.py:31 _deny_cluster` → `user_permission_service.py:47`
3. → `A/clients/executor_client.py:549 acall`（`asyncio.to_thread` + 信号量 `:31`）→ `:849 get_pod_logs` / `:875 get_pod_yaml` / `:903 list_pods`
4. → `executor_client.py:867 _agent_call(..., "GetPodLogs")` 或 `:869 _get_stub().GetPodLogs`
5. → `G/proto/executor.proto:47 GetPodLogs` / `:49 GetPodYaml` / `:53 ListPods`
6. → `G/internal/grpc/server.go:375 GetPodLogs` → `ex.GetPodLogs` → `G/internal/k8sclient/manager.go`；`:388`、`:413` 同构

【待验证】`ex.GetPodLogs` 的具体实现文件（推测 `G/internal/executor/pod_ops.go`，本次未逐行核）。

【事实·跨层】`A/api/resources.py:14` 在 API 层直接 import `executor_client` 并在 `:53/:69/:228/:257/:276/:567/:584` 直调；`:491 pod_metrics` 直接 `session.execute(text("SELECT ... FROM pod_metrics_5m ..."))`；`A/api/clusters.py:36` 直接落库。鉴权、SQL、gRPC 编排三者都在 api 层。

---

## C. 告警产生到告警处理

### C-1 来源一：Go watcher 直写平台库【事实】

1. `G/internal/watcher/pod_watcher.go:220 handlePodEvent` → `:476 createAlert` → `G/internal/watcher/sink.go:89 CurrentSink().Report`
2. `sink.go:137 dbSink.Report` → `:144 reportPodAlert`（`db.Where(...).First` 去重 → `db.Create(&alert)`）/ `:229 reportNodeAlert`
3. 节点侧：`G/internal/watcher/node_watcher.go:75 handleNodeEvent` → `:207 Report`

### C-2 来源二：agent 隧道上报【事实】

1. `G/internal/watcher/sink.go:378 tunnelSink.Report` → `:387 t.send("AlertEvent", payload)`
2. → `A/agent_gateway.py:316`（`f.type == "event"`）→ `:243 _handle_event` → `:247 handle_event`
3. → `A/services/agent_alert_service.py:239 handle_event`（`:249 method=="AlertEvent"`）→ `:173 handle_alert_event` → `:88 _merge_or_insert`

### C-3 聚合与自动处理【事实】

1. 去重合并主入口 `A/services/alert_merge.py:57 find_merge_target`（`ACTIVE_STATUSES:29`、`get_window_minutes:37`）→ `:100 apply_merge`（`screen_bus.publish:119`）；调用点 `agent_alert_service.py:107/:125`、`resource_alert_service.py:166`、`db_alert_service.py:147`、`pod_restart_service.py:238`
2. 后台循环：`A/main.py:573 FastAPI(lifespan=lifespan)` → `:544 lifespan` → `:548 startup_tasks` → `:370 _interval_worker("告警自动处理", auto_process_alerts, 150.0)`；另有 `:420 recovery_sweep_loop`（`alert_auto_service.py:3639`）、`:428 alert_digest_sweep_loop`
3. 主流程：`A/services/alert_auto_service.py:3837 auto_process_alerts` → `:3519 process_alert`（Redis 锁 `:3521`）→ `:2464 _process_one`
4. `_process_one` 内分流：`:2860 is_react_loop_enabled`（`agent_loop.py:135`）→ ReAct `:2864` 或单次 `:2870 ai_client.diagnose`
5. 通知 `A/services/notification_service.py`、处置 `executor_client.cleanup_evicted`（实测 `_process_one:2499` 对 PodEvicted 直接调用）
6. 超时转人工 `:3659 escalate_stale_alerts` → `:1084 _mark_manual_and_notify`
7. HTTP 入口：`A/api/alerts.py:360 diagnose_alert`、`:533 auto_process_alert` → `:542 process_alert`、`:548 recovery_notify_alert`（`X-Internal-Token`）

【推断】`process_alert` 由告警写入事件触发（`:3519` 注释如此，调度接线未逐行核实）。

【事实·跨层】Go watcher `sink.go:144` 由 executor 直接写平台中心库，是全链路最大的跨层；`A/api/alerts.py:533` 直接 import 并调用 `process_alert`，绕过常驻 service 编排层。

---

## D. AI 故障诊断完整流程

### D-1 单轮诊断【事实】

1. `A/services/alert_auto_service.py:2870 ai_client.diagnose` → `A/clients/ai_client.py:42 diagnose` → `:21 _post`（`POST /api/v1/ai/diagnose`）
2. 令牌/超时：`ai_client.py:28 X-Internal-Token=JWT_SECRET_KEY`、`:44 timeout=AI_DIAGNOSE_TIMEOUT_SECONDS`、`:35 connect=5s`
3. `I/main.py:46 internal_auth_middleware` 校验 → `I/api/core_api.py:13 diagnose`
4. `I/services/diagnosis_service.py:132 diagnose_alert` → `:138 template_routing.load_routing` / `:140 _get_template` / `:153 resolve_effective_template`（Prompt 灰度）
5. **指标与日志进入提示词**：`:158 _collect_context`（`:96`）→ `I/services/prometheus_client.py:108 get_pod_metrics`、`I/services/es_client.py:115 search_logs`；NODE 类 `:127 get_node_metrics`；结果经 `:172-173 _sanitize_external` 包 `<<<EXTERNAL_DATA>>>` 注入 `:184 user_prompt`。api-server 侧另把 `metrics_trend/probe_evidence/termination_evidence` 塞入 `context_data`（`alert_auto_service.py:2840-2854`）→ `:160 ctx.update`
6. `:193 call_with_fallback` → `I/services/model_adapter.py:184`：` :206 severity 过滤` → `:220 order_model_configs` → `:246 ModelFactory.get_adapter:119`（openai/deepseek/claude）→ `:256` 失败降级、`:272` 空返回重试、`:173 decision_temperature=0.2`
7. `:196 _parse_diagnosis_json`（`:708`）→ `I/utils/json_util.py:89 extract_json`；失败 → `:677 _salvage_partial_diagnosis` 抢救截断 JSON → 空结论用 `diagnosis_uncertain` 模板重问（`:200-214`）
8. `:218 _correct_probe_root_cause` → `:220` 落 `AIDiagnosisResult` → `:233` 返回 `root_cause/confidence/category/evidence_refs/root_cause_chain/analysis/suggestions_detail`

### D-2 ReAct 深诊【事实】

1. 入口 `A/services/agent_loop.py:1108 diagnose_alert_react` → `:1115 load_agent_policy`（默认 8 步 / 90s，`:54 MAX_ROUNDS_HARD_LIMIT=16`，`:157 apply_scenario_policy` 启动失败放宽到 12 步 / 240s）→ `:1142 run_react_loop`
2. `:1039 run_react_loop` → `:1070 list_tools(readonly)`、`:1083 wait_for(total_timeout)` → `:921 _run_loop_body`
3. `:938 while rounds < max_rounds` → `:941 _default_decide:900` → `ai_client.agent_decide:106` → `I/api/core_api.py:68 agent_decide` → `I/services/agent_service.py:145 decide_next_step`（`SYSTEM_PROMPT:32`、`:23 _history_limits` 上下文瘦身、`:204` 40s `wait_for`、`:213` 解析失败纠正重问一次、`:231 _unparseable_decision:128`）
4. **工具执行**：`:972 execute` → `:909 _default_execute` → `A/services/tool_registry.py:1795 execute_tool`（注册表 `:1294 TOOL_REGISTRY`、`:1297 _register`、`:1759 list_tools`、`:1811 validate_args`、`:1817 allow_operate` 权限闸门、`:1827 handler`）；例 `_h_describe_pod:116`、`_h_exec_in_pod:108` → `executor_client.acall:26`
5. **证据回灌**：`agent_loop.py:1015 messages.append({"role":"tool"})` → 下一轮 decide；`:1002 state["tool_steps"]`、`:989 audit`；连败 ≥2 → `:1019 _degrade:438`
6. **收敛**：`:949 final_answer` → `:950 _normalize_final` / `:1032 _wrap_up` / `:1091 _deterministic_root_cause:573`；`A/services/alert_auto_service.py:1643 _persist_react_diagnosis` 补落库

【事实】用户主动入口与告警入口不同：`A/api/alerts.py:442` 手动重诊断（ReAct 优先、`:459` 降级单次）；`A/api/agent.py:113 create_session` → `:24 _run_first_turn` → `A/services/agent_service.py:294 run_agent_turn`，`deep=False` 走 `:335 ai_client.agent_diagnose_once:112` → `I/api/core_api.py:83` → `I/services/agent_service.py:240 diagnose_once`（一次 LLM、`:285` 强制 final_answer）；`deep=True` 走 `:407 ai_client.agent_decide` 多轮。

【事实】解析失败不伪造结论：`I/services/agent_service.py:105 _parse_decision` 失败返回 `unparseable`（`:128`）→ `agent_loop.py:953 _degrade`；`diagnose_once:277` 返回空 `root_cause`。

---

## E. AI 修复流程

【事实】调用序列（收敛点为 `_process_one` 与 `run_plan`）：

1. 诊断结论 → `alert_auto_service.py:2883 root_cause` / `:2891 repair_steps_text`
2. `:3183 ai_client.generate_plan:63` → `I/api/core_api.py:43` → `I/services/plan_service.py:84 generate_plan`（`:24 _ACTION_GUIDE`、`:42 analyze_operation_type`、`:56 assess_risk_level`、`:121` 落 `RepairPlan`）→ `alert_auto_service.py:3235 _materialize_platform_actions` / `:3236 _sanitize_plan_steps`
3. 决策：`alert_auto_service.py:3297 _decide_action`（`:927`）→ `A/services/decision_engine.py:196 score_steps`（四维：操作/资源/环境/影响）→ `:241 decide_by_level`（`level<4 auto` / `>=4 approval_2` / 否则 `approval_1`）；叠加 `:3365 decision_score.evaluate:120` 与 `:3336 _evaluate_autonomy_gate` → `A/services/autonomy_gate.py:107 evaluate`
4. 分流：`:3305 full→auto`、`:3311 平台动作→approval_1`、`:3317 match_rule`、`:3346 闸门拦截转人工`、`:3392 enforce 降级`、`:3402 低置信度拦截`
5. auto：`:3418 _execute_auto_plan:1243` → `:1280 plan_runner.run_plan`；审批：`:3510 create_approval` → `A/services/approval_service.py:350 create_approval`（`:79 _expand_flow`、`:140 _expand_dual_approvers`）
6. 审批回调：`A/api/approvals.py:177 decide` → `approval_service.py:415 approve`；IM 回调 `A/api/webhooks.py:93 handle_callback` → `approval_service.py:675 handle_callback` → `:700 approve` → `:466 create_task(execute_approved_plan)` → `:851 execute_approved_plan`
7. 执行：`A/services/plan_runner.py:82 run_plan` → `:102 _run_platform_action:70` 或 `:120 executor_client.execute_plan` → `A/clients/executor_client.py:624 execute_plan`（gRPC `ExecutePlan`，timeout 600s `:627`）
8. 灰度/熔断/回滚：`executor_client.py:641 circuit_triggered`（`G/proto/executor.proto:152`）由 executor 返回；`plan_runner.py:138 AUTO_ROLLBACK_ON_FAILURE` → `:145 rollback_plan` → `executor_client.py:648 RollbackPlan`；一键回滚 `A/api/executions.py:60 rollback_execution`
9. 验证：`alert_auto_service.py:3421 _verify_auto_repair_ex:1403`（180s + 20s 稳定窗）→ `:3422 record_repair_verification:1492`；审批侧 `:1472 verify_repair_outcome`；动作级 `:1317 action_verify.verify_action`；AI 侧 `I/services/verification_service.py:12 verify_repair`（`ai_client.verify_repair:67`）
10. 失败兜底：`:3460-3477` 清诊断缓存、`:3481 plan 回 pending`、`:1521 _try_rebuild_once`、`:3287 _mark_manual_and_notify`、`:3659 escalate_stale_alerts`；二次诊断 `:3751 refresh_ticketed_diagnosis` → `:3780 _run_diagnosis_only` → `:3826 diagnose_alert_react`
11. 知识沉淀：`:3452 _dedup_knowledge:952` → `A/services/knowledge_service.py:15 add_knowledge` → `ai_client.add_knowledge:80` → `I/api/aux_api.py:149 rag_add`
12. 复发转工单：`A/services/repair_recurrence_service.py:65 detect_recurrence` → `:136 mark_plan_failed` → `:161 escalate_ticket`

【事实】内部令牌：`ai_client.py:28`、`A/api/ai_proxy.py:23` 均注入 `X-Internal-Token=JWT_SECRET_KEY`（代理在 `:22` 剥离 `Authorization`），`I/main.py:44-48` 校验，`/health` 等豁免。
【事实】超时：`ai_client.py:19 DEFAULT=120s`；`A/core/config.py:44-48`（`AGENT_DECIDE=45 / AI_DIAGNOSE=100 / AI_PLAN=60`）；`I/core/config.py:36` `LLM_AGENT_DECIDE=40`、`:43` `LLM_HTTP=120`。
【事实】重试：`alert_auto_service.py:2861` 两次尝试（ReAct 失败 `sleep 2` 降级单次）；`model_adapter.py:242-276` 模型降级 + 空返回重试（`LLM_EMPTY_RESPONSE_RETRIES=1`）；`I/services/agent_service.py:213-231` 决策 JSON 解析纠正重问一次；`_post` 本身无 HTTP 重试（仅 `raise_for_status:36`）。
【待验证】Go 侧 `circuit_triggered` 的触发条件与灰度比例、rag_service 内部写入逻辑（本次仅确认路由接线）。

---

## F. Agent 接入流程（16 步）

【事实】连续调用序列：

1. `G/cmd/agent/main.go:84 main` 解析 flag：`-platform=127.0.0.1:9095`(`:85`)、`-token`(`:87`)、`-ca/-cert/-key-file`(`:91-93`)、`-mode`(`:101`)
2. `main.go:126 buildRestConfig` / `:148 agentdb.LoadConfigs` / `:158 agentbridge.New`（起进程内 ExecutorService）
3. `main.go:167-191` 读 CA 与证书，`credentials.NewTLS`(`:187`)；**无证书时回退明文**(`:190`)
4. `main.go:195-199` 追加 gRPC keepalive（30s/10s）
5. `main.go:222 grpc.NewClient(*platform)` 主动外连 9095；`:231` 进入 3s 重连循环
6. `main.go:246-247 runTunnel` 建双向流（`G/proto/agent/agent.proto:12 Tunnel`）
7. `main.go:261` 发送 `AgentFrame{Type:"register", Token, Version, Capabilities}`（`:59 buildCapabilities`）
8. `A/agent_gateway.py:253 _Servicer.Tunnel` → `:256 reader` → `:259 f.type=="register"` → `:263 _verify_and_mark`；mTLS 在握手层 `:396-399`（`require_client_auth=True`），证书由 `A/services/agent_pki.py:140 issue_client_cert` 签发
9. `agent_gateway.py:32 _verify_and_mark`：`sha256(token) == c.agent_token_hash`(`:47-49`)；写 `agent_status/agent_last_seen_at/version/capabilities`(`:50-55`)
10. `agent_gateway.py:274-284` 新建 `_AgentConn`、登记 `_agents[cluster_id]`(`:278-279`)、回 `register_ack`(`:280`)、`_push_config`(`:225-238`)；agent 侧 `main.go:350-351` 收 ack
11. **心跳结论**：协议两端都有分支，但**无应用层心跳发起方** —— agent 仅 `case "ping" → pong`（`main.go:352-353`），平台仅 `elif f.type=="pong": pass`（`agent_gateway.py:318-319`），全仓无 `type="ping"` 构造点，`agent_last_seen_at` 只在注册 `:51` 写一次。真正保活是 gRPC/HTTP2 keepalive（`main.go:195-199`、`agent_gateway.py:361-369`）。【推断】ping/pong 为遗留死代码
12. 任务下发：`A/clients/executor_client.py:581 execute_command` → `:466 _agent_call` → `:131 _gateway_call` → `agent_gateway.py:135 call`（通用路径 `:257 _TunnelAwareStub.__getattr__._call`）
13. `agent_gateway.py:141-145 call`：`uuid4` 生成 `req_id`、`pending[req_id]=slot`、`a.out.put(type="request")`；`:331-337` 从队列 `yield` 下发
14. `G/cmd/agent/main.go:362-366` → `:455 handleRequest`（DB 类方法例外直查 `:460-584`）→ `:585 bridge.Call`
15. `G/internal/agentbridge/bridge.go:260 Call` → `:273 conn.Invoke` → 进程内 `G/internal/grpc/server.go:128 ExecuteCommand`（或对应 RPC）
16. 回传：`main.go:457-591` 组 `response` 帧 → `:364 send` → `agent_gateway.py:303-310` 按 `req_id` 找 slot 并 `event.set()` → `:147-153` 返回 payload（`executor_client.py:471 resp_cls.FromString`）；流式对应 `agent_gateway.py:159 stream_call` / `main.go:367-428`

【事实】push / pull：agent 侧开关 `main.go:101 -mode`（默认 push），pull 分支 `:212-219` → `bridge.go:164 ServePull`（:9100）；平台侧 `A/api/clusters.py:223-225 agent_mode`、`A/main.py:234-271 _pull_clusters_refresh_loop`（30s 刷新）→ `executor_client.py:382 set_pull_target` / `:413 _pull_call`；路由优先级 `executor_client.py:266-281`（隧道 → pull → 直连）。**pull 下不可用**：`main.go:210-211`、`bridge.go:152-163` 明确 watcher 告警上报、PodMetrics / DB 指标、Events、配额快照、主机代采均依赖隧道（`start*Push` 只在 push 分支 `:267-284` 启动）；但 `bridge.go:170` 仍注册完整 `ExecutorService`，33 个 unary + 3 个流式可用。

【事实】inproc / remote：`A/core/config.py:100` 开关（`:97-99` 记录 pydantic `extra="ignore"` 曾吞掉环境变量）；`A/main.py:551-558` inproc 起 9095，`A/agent_gateway_main.py:22-27` remote 独立进程起 9095+9096；remote 契约 `A/proto/agent_gateway_internal.proto:9-17`。**已知缺口**：bidi 不支持（`executor_client.py:146-150`）、能力/版本查询返回 `None/""`（`:182-194`）、`_agents` 仍是进程内全局、网关读线程仍直接写平台库（`agent_gateway.py:32,75,243`）。

【事实·全局状态】`agent_gateway.py:26-29 _agents/_agents_lock/_server/_loop`；`_AgentConn.out=queue.Queue()`(`:92`)、`pending`(`:93`)、`streams`(`:95`) 均**无界**，`:145/:169/:196/:202/:237` 的 `put` 永不阻塞；agent 侧 `streams` + `sendMu` 全局串行（`main.go:326-337,253-258`），`case "request"` 同步处理（`:363`），`bridge.Call` 最长 60s。【推断】存在队头阻塞与无背压风险。

---

## G. 数据库接入与诊断流程

【事实】调用序列：

1. CRUD：`A/api/db_connections.py:288 create_connection` → `:307/:309 encrypt_aes256_gcm` → `A/models/db_connection.py:25 password_encrypted` / `:28 readonly_password_encrypted`
2. 查询与只读判定：`db_connections.py:343 query_connection` → `:352 head = sql.split()[0].lower()` → `:353` 白名单 `select/show/describe/desc/explain` → `:360 check_db_permission`（`user_permission_service.py:91`）
3. 428 审核信封：`db_connections.py:365 _check_approval` → `:420` → `A/services/db_approval_service.py:58 needs_approval` → `:425 {"code":428, ...}`；前端解包 `F/services/api.ts:47`
4. 分派执行：`db_connections.py:430 _run_sql` → `:441` `A/services/db_direct_service.py:20 query / :24 exec_sql` → `A/services/dialects/__init__.py:20 get_dialect`（`_REGISTRY:12`）→ `dialects/base.py:65 Dialect` → `mysql.py:374 connect(readonly=True)`（`:38 _info` 优先只读账号）
5. agent 库旁路：`db_connections.py:433` → `:436 agent_gateway.call(..., "QueryDatabaseByKey")`
6. 慢 SQL 采集：`A/services/db_collector.py:197 collect_connection_slow_sql` / `:303 scan_all_connections`；`db_slow_event_service.py:366 collect_connection_slow_events` → `:342 _insert_events` → `:505 collect_all_slow_events`（注册 `main.py:378`）
7. 诊断/优化：`A/api/db_diagnosis.py:48 run_diagnosis` → `A/services/db_diagnosis_service.py:83 diagnose_db`（`:66 _format_slow_sql`）→ `sql_optimize_service.py:749 optimize` / `:908 inspect`
8. 审核单/定时执行：`A/api/db_approvals.py:113 create_db_approval` → `:271 approve_db_approval` → `db_approval_service.py:130 execute_request` → `:259 run_due_scheduled`（`main.py:74 _run_due_db_approvals`，注册 `:365`）；SQL 定时任务 `main.py:319-322 sql_task_service.load_all()` → `sql_task_service.py:219`

【事实·跨层】`db_connections.py:356` 路由内直接 `async_session()`，`:352` 内联只读白名单，`:436` 直调网关，`:443` 直调 `db_direct_service`，`:382` 内联审计写库；`:1938-1963` 与 `db_approvals.py` 各自内联一份 428 信封，没有统一网关层。

---

## 附：跨链路共用的隐式契约

| 契约 | 位置 | 影响【推断】 |
|---|---|---|
| 内部令牌 = JWT 签名密钥 | `A/clients/ai_client.py:28`、`A/api/ai_proxy.py:23`、`G/internal/config/config.go:66` | 服务间信任与用户身份共用一把密钥 |
| 状态字符串硬编码 | `ticket_service.py:455,547,701,807-810`、`db_approval_service.py:119,143,251,264`、`I/services/diagnosis_service.py:157,167-175` | 无枚举/常量，跨语言与跨服务靠字面量对齐 |
| `alerts` 双写 | `G/internal/watcher/sink.go:144` 与 `A/services/alert_merge.py:100` | 去重规则两份实现 |
| 表结构双份定义 | `I/app/models/__init__.py:2` | schema 变更需人工同步两处 |

## 待验证事项汇总

1. B-6 `ex.GetPodLogs` 的 Go 实现文件；`circuit_triggered` 触发条件与灰度比例。
2. C 中 `process_alert` 的调度接线（注释与常驻循环的关系）。
3. E 中 `rag_service.add_knowledge` 内部写入逻辑。
4. F 中 pull 模式的能力协商缺失是否有其他补偿；remote 模式 `_agents` 是否完全旁路。
5. 所有链路的超时/重试在真实网络抖动下的行为（本次为静态阅读，未执行）。
