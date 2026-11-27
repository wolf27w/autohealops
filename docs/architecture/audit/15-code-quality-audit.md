# 15 · 代码质量审计（Code Quality Audit）

> 审计类型：**只读**。未修改任何业务代码/配置/依赖，未安装任何工具，未连接生产，未执行 git 写操作。
> 仓库：`/Users/wolf/Documents/project/autohealops`　审计时点：2026-11-27
> 所有数字来自本文档内给出的命令实测输出；未执行的检查在 §2 明确标注。

---

## 1. 结论摘要

1. **规模与集中度失衡**：`api-server/app` 272 个 Python 文件 / **68,888 行**，其中 `app/api` 67 文件 19,176 行、`app/services` 114 文件 39,935 行；单文件最大 `services/alert_auto_service.py` **3,867 行**，`api/resources.py` 2,883 行。
2. **函数粒度失控**：`api-server/app` 共 2,475 个函数，**12 个 > 200 行、47 个 > 100 行、208 个 > 50 行**；两个函数越过 1,000 行（`stats.py:327 _compute_dashboard` 1,058 行、`alert_auto_service.py:2464 _process_one` 1,053 行）——这两处是全平台最不可能被测试覆盖、最难 Review 的代码。
3. **异常处理是「一律捕获 + 静默」**：`except Exception` **861 处**、`# noqa: BLE001` **856 处**（同一批），其中 **137 处 except 分支只有 `pass`**，不记录日志（TOP：`api/resources.py` 12、`services/alert_auto_service.py` 12、`api/host_terminal.py` 8）。好处是接口不 500，代价是真实缺陷被吞掉。
4. **重复代码真实存在**：排除 protobuf 生成代码后，仍有 **46 个 12 行以上重复块**；最典型的是 SQL 切分逻辑被复制两份——`dialects/base.py:160 split_sql_statements`（153 行）与 `dialects/oracle.py:347 _split_oracle_sql`（150 行）近乎逐行相同。
5. **HTTP 超时纪律良好，gRPC 有缺口**：httpx 已收敛到进程级共享客户端，默认 30s/连接 10s（`core/http_client.py:21-36`），31 处 httpx 调用仅 1 处同文件内未写 timeout（走默认）；但**直连本地 executor 的 unary 调用在未传 timeout 时是「无 deadline」**（`clients/executor_client.py:263-283` → `fn(req, timeout=None)`），一旦 executor 卡住会挂满线程池。
6. **事务以「分段 commit」为主**：`.commit()` **332 处** vs `.rollback()` **11 处**，无一处 `session.begin()`；`get_db()` 只 `close()`（隐式回滚），关键链路（审批通过→执行）存在 **check-then-act 竞态**（先查 status 再置 executing，非原子）。
7. **索引覆盖与查询不匹配**：模型里 `index=True` 161 处，但 **64 处 `created_at` 无索引**，而 `where(created_at)` 出现 24 次；`execution_logs.status`、`k8s_events`/`audit` 的时间列同样裸奔。
8. **契约层仍为空**：`response_model` **0 处**，`Body(...)` **0 处**，495 条路由中 **210 处**请求体是 `payload: dict`（TOP：`api/resources.py` 30、`api/db_connections.py` 24、`api/clusters.py` 9）——字段校验全靠手写 `if not payload.get(...)`。

---

## 2. 审计方法与工具可用性（先说清「没做什么」）

### 2.1 环境探测（真实输出）

| 探测项 | 结果 |
|---|---|
| `python3 --version` | `Python 3.9.6`（系统自带） |
| `api-server/.venv/bin/python -V` | `Python 3.11.16` ✅ |
| `api-server/.venv/bin/python -m pytest --version` | ❌ `No module named pytest`（**未安装，跳过 pytest 相关检查**） |
| `api-server/.venv/bin/python -m ruff --version` | ❌ `No module named ruff`（**未安装，跳过 lint**） |
| `go version` | `go1.26.6 darwin/arm64` ✅ |
| `node -v` | `v24.18.0` ✅ |
| `frontend` 下 `npx tsc -v` | `Version 5.9.3`，`node_modules` 存在（未联网） |

### 2.2 执行的检查 / 跳过的检查

| 检查 | 状态 | 说明 |
|---|---|---|
| 文件规模（`find`+`wc -l`） | ✅ 已执行 | 排除 `node_modules`/`.venv`/`dist`/`__pycache__`/`.git` |
| 函数规模（`ast` 脚本） | ✅ 已执行 | 脚本写在 `/tmp/fnstat.py`，未写入项目 |
| Go 函数规模（`/tmp/gofn.py`） | ✅ 已执行 | 同上 |
| 重复代码（12 行滑窗，`/tmp/dup2.py`） | ✅ 已执行 | 已排除 `app/proto` 生成代码 |
| `grep` 类静态统计 | ✅ 已执行 | 见各节命令 |
| `python -m compileall` | ⛔ **未执行** | 会写 `__pycache__`（副作用），按约定改为 `unittest` 导入即验证语法 |
| `ruff` / `flake8` / `mypy` | ⛔ **未执行** | 未安装；不装任何依赖 |
| `go vet ./...` | ⛔ **未执行** | 会写 build cache；改为 `go test ./...`（见报告 16），编译期已覆盖大部分 vet 类问题 |
| `npx tsc --noEmit` | ✅ 已执行（在报告 16 记录结果） | 不联网、不写产物 |

> 说明：`wc -l`/`ast`/`grep` 的统计均包含注释与空行，属「物理行」口径；结论里区分了「物理行」与「函数体行数」。

---

## 3. 代码规模

### 3.1 文件规模 TOP20（物理行，全仓源码）

```bash
find . -type f \( -name '*.py' -o -name '*.go' -o -name '*.ts' -o -name '*.tsx' -o -name '*.js' \
  -o -name '*.html' -o -name '*.sql' -o -name '*.sh' \) \
  -not -path '*/node_modules/*' -not -path '*/.venv/*' -not -path '*/dist/*' \
  -not -path '*/__pycache__/*' -not -path './.git/*' -exec wc -l {} + | sort -rn | head -25
```

实测（合计 **174,485 行**，含 js/html/sql/sh 口径）：

| # | 行数 | 文件 | 性质 |
|---:|---:|---|---|
| 1 | 9,888 | `k8s-executor/proto/executor.pb.go` | 生成代码 |
| 2 | 3,867 | `api-server/app/services/alert_auto_service.py` | 业务主体 |
| 3 | 2,883 | `api-server/app/api/resources.py` | 业务主体（79 路由） |
| 4 | 2,203 | `api-server/app/proto/executor_pb2_grpc.py` | 生成代码 |
| 5 | 2,106 | `api-server/app/api/db_connections.py` | 业务主体（47 路由） |
| 6 | 2,030 | `k8s-executor/proto/executor_grpc.pb.go` | 生成代码 |
| 7 | 1,832 | `api-server/app/services/tool_registry.py` | 业务主体（AI 工具表） |
| 8 | 1,648 | `api-server/app/api/stats.py` | 业务主体 |
| 9 | 1,636 | `api-server/app/services/notification_service.py` | 业务主体 |
| 10 | 1,455 | `frontend/src/services/api.ts` | 前端 API 层（单文件） |
| 11 | 1,407 | `api-server/app/clients/executor_client.py` | gRPC 客户端封装 |
| 12 | 1,346 | `frontend/public/monitor-bigscreen-old.html` | 遗留文件 |
| 13 | 1,264 | `api-server/app/services/db_collector.py` | 业务主体 |
| 14 | 1,244 | `frontend/src/pages/SqlTerminal.tsx` | 页面组件 |
| 15 | 1,177 | `api-server/app/services/ticket_service.py` | 业务主体 |
| 16 | 1,149 | `api-server/app/services/agent_loop.py` | ReAct 循环 |
| 17 | 1,126 | `k8s-executor/internal/grpc/server.go` | gRPC 服务端 |
| 18 | 1,055 | `api-server/app/services/dialects/oracle.py` | 方言实现 |
| 19 | 1,055 | `api-server/app/services/approval_service.py` | 审批执行 |
| 20 | 989 | `frontend/src/pages/WebTerminal.tsx` | 页面组件 |
| — | 983 | `api-server/app/services/sql_optimize_service.py` | — |
| — | 980 | `frontend/public/monitor-bigscreen.html` | — |
| — | 979 | `logs/archive/dedup_backup_20260920/code/alert_auto_service.py` | ⚠️ `logs/archive` 里躺着一份旧代码副本（已被 .gitignore 排除） |

分目录口径：

```bash
find api-server/app -name '*.py' | wc -l      # 272
find api-server/app -name '*.py' -exec cat {} + | wc -l   # 68888
find api-server/app/services -name '*.py' -exec cat {} + | wc -l  # 39935 / 114 文件
find api-server/app/api -name '*.py' -exec cat {} + | wc -l       # 19176 / 67 文件
find frontend/src -name '*.ts*' -exec cat {} + | wc -l            # 35063 / 147 文件
```

### 3.2 函数规模 TOP20（`api-server/app`，函数体行数）

```bash
cat > /tmp/fnstat.py <<'EOF'
import ast, os
rows=[]
for dp,dn,fn in os.walk('api-server/app'):
    dn[:]=[d for d in dn if d!='__pycache__']
    for f in fn:
        if not f.endswith('.py'): continue
        p=os.path.join(dp,f)
        t=ast.parse(open(p,encoding='utf-8').read())
        for node in ast.walk(t):
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                rows.append((node.end_lineno-node.lineno+1,p,node.lineno,node.name))
rows.sort(reverse=True)
for r in rows[:25]: print(r)
print(len(rows), sum(1 for r in rows if r[0]>200), sum(1 for r in rows if r[0]>100))
EOF
python3 /tmp/fnstat.py
```

实测：共 **2,475** 个函数；**>200 行 12 个、>100 行 47 个、>50 行 208 个**。

| # | 行数 | 位置 | 函数 |
|---:|---:|---|---|
| 1 | 1,058 | `api-server/app/api/stats.py:327` | `_compute_dashboard` |
| 2 | 1,053 | `api-server/app/services/alert_auto_service.py:2464` | `_process_one` |
| 3 | 252 | `api-server/app/proto/executor_pb2_grpc.py:623` | `add_ExecutorServiceServicer_to_server`（生成） |
| 4 | 251 | `api-server/app/proto/executor_pb2_grpc.py:32` | `__init__`（生成） |
| 5 | 247 | `api-server/app/services/db_collector.py:918` | `_oracle_health_sync` |
| 6 | 247 | `api-server/app/main.py:294` | `startup_tasks` |
| 7 | 244 | `api-server/app/api/clusters.py:260` | `agent_install_yaml` |
| 8 | 239 | `api-server/app/services/agent_service.py:294` | `run_agent_turn` |
| 9 | 232 | `api-server/app/api/host_terminal.py:140` | `ws_host_terminal` |
| 10 | 222 | `api-server/app/services/trace_service.py:168` | `_build_steps` |
| 11 | 206 | `api-server/app/services/db_collector.py:695` | `_pg_collect_health` |
| 12 | 206 | `api-server/app/services/db_collector.py:487` | `_mysql_collect_health` |
| 13 | 183 | `api-server/app/services/approval_service.py:851` | `execute_approved_plan` |
| 14 | 179 | `api-server/app/services/startup_evidence_service.py:395` | `collect` |
| 15 | 175 | `api-server/app/services/inspection_service.py:53` | `inspect_cluster` |
| 16 | 162 | `api-server/app/api/alerts.py:61` | `list_alerts` |
| 17 | 158 | `api-server/app/services/sql_optimize_service.py:749` | `optimize` |
| 18 | 158 | `api-server/app/api/repair_records.py:38` | `list_records` |
| 19 | 153 | `api-server/app/services/host_import_service.py:154` | `import_hosts` |
| 20 | 153 | `api-server/app/services/dialects/base.py:160` | `split_sql_statements` |

Go 侧（`/tmp/gofn.py`，按大括号配对统计）：**1,802 个函数，12 个 >100 行**，最大 `internal/executor/misc_resources.go:189 fetchMiscResources` **352 行**，其次 `internal/quota/collector.go:60 CollectClusterQuotaOnce` 178 行、`internal/executor/describe_node.go:20 describeNode` 163 行。

---

## 4. 重复代码线索

### 4.1 统计口径与结果

```bash
python3 /tmp/dup2.py   # 12 行滑窗、去空白、排除 app/proto、要求块内≥9 行互不相同
# dup 12-line blocks: 46
```

排除 protobuf 生成代码后仍有 **46 个 12 行重复块**。典型（真业务代码）：

| 重复次数 | 位置 | 重复内容 |
|---:|---|---|
| 3 | `services/dialects/base.py:233` / `mysql.py:133` / `oracle.py:415` | 引号/转义感知的 SQL 逐字符扫描循环 |
| 2 | `services/sql_optimize_service.py:770` / `:930` | 同文件内 `_stored` 全文替换 + FTS upsert 分支（约 40 行复制） |
| 2 | `services/resource_service.py:360` / `services/metrics_rollup.py:195` | 时间分桶（`boundaries`/`keep`/`cursor`）分区创建逻辑 |
| 2 | `services/db_collector.py:443` / `services/db_alert_service.py:100` | `aiomysql.connect(... connect_timeout=5)` 连接参数块 |
| 2 | `services/dialects/pg.py:553` / `oracle.py:228` | `add_indexes` 校验 + DDL 拼装 |
| 2 | `api/stats.py:1457` / `api/screens.py:41` | SSE 鉴权（`decode_jwt` + 401 分支） |
| 2 | `api/fault_diagnosis.py:80` / `:155` | 告警行 → 视图 dict 组装 |

### 4.2 同名函数复制（结构性重复）

```bash
grep -rhoE "^\s*(async )?def \w+\(" api-server/app/{services,api} --include='*.py' | sort | uniq -c | sort -rn
```

| 次数 | 函数名 | 说明 |
|---:|---|---|
| 8 | `background_loop` | 8 个 service 各自实现后台轮询循环（扫描间隔、异常处理、退出逻辑都各写一遍） |
| 6 | `_parse_dt` | `api/host_audits.py:25`、`api/alert_silences.py:26`、`api/resource_governance_ctl.py:51`、`api/host_sessions.py:35`、`services/agent_alert_service.py:35`、`services/db_approval_service.py:94`——同一「ISO 时间字符串→datetime」逻辑 6 份 |
| 6 | `query` / `schema` / `ddl` / `exec_sql` | 各方言类实现（属正常多态，不算问题） |

**最大的单点重复**：`dialects/base.py:160 split_sql_statements`（153 行）与 `dialects/oracle.py:347 _split_oracle_sql`（150 行）是同一算法的两份拷贝（dup 检测在 `base.py:233` 与 `oracle.py:415` 命中 3 次）。修一处必须记得改另一处，否则 MySQL/Oracle 的 SQL 切分行为会漂移——这是 DB 审核/执行链路上的高危复制。

---

## 5. 异常处理

```bash
grep -rn --include='*.py' "except Exception" api-server/app | wc -l   # 861
grep -rn --include='*.py' "except:"          api-server/app | wc -l   # 0
grep -rn --include='*.py' "noqa: BLE001"     api-server/app | wc -l   # 856
# except 分支后首个语句是 pass 的数量（python 脚本统计）
# silent pass total 137
```

- **好消息**：没有裸 `except:`（0 处），全部是 `except Exception` / 具名异常，不会吞 `KeyboardInterrupt`/`SystemExit`。
- **坏消息**：`noqa: BLE001` 856 处 = 「屏蔽 lint 的盲捕获」成体系。137 处只 `pass`，无日志无计数：

| 文件 | 静默 pass 次数 |
|---|---:|
| `api-server/app/api/resources.py` | 12 |
| `api-server/app/services/alert_auto_service.py` | 12 |
| `api-server/app/api/host_terminal.py` | 8 |
| `api-server/app/api/release.py` | 6 |
| `api-server/app/api/logs.py` | 5 |
| `api-server/app/services/db_collector.py` | 5 |

**风险**：自愈链路（`alert_auto_service`）里静默失败意味着「告警没被处理，也没人知道为什么没被处理」。建议：静默分支至少 `logger.debug(..., exc_info=True)`，并把「自愈失败原因」写进告警的 `manual_reason`（该字段已存在，见 `models/alert.py`）。

**捕获后有日志的比例**：`grep -A2 "except Exception" | grep -c "logger\.(exception|error|warning)"` → 366 / 861 ≈ 42%。其余 58% 要么静默、要么 `logger.info/debug`。

---

## 6. 日志

### 6.1 结构化程度

`api-server/app/core/logging.py` 实现了 `JsonFormatter`（`ts/level/service/trace_id/msg/exc` 五字段 JSON，对齐 Go zap），`get_logger()` 98 处使用；但仍有 15 处直接用 `logging.getLogger()`，会走 uvicorn 默认文本格式（同一条日志流里 JSON 与纯文本混排，采集侧解析会漏）。

`trace_id` 靠 `LoggerAdapter` 注入（`logging.py:38-46`），`TraceIdFilter` 已定义但**未在 handler 上注册**（`grep -rn "TraceIdFilter" api-server/app` 只有定义处），所以不带 adapter 的 logger 打出来的 `trace_id` 恒为空串。

### 6.2 敏感信息

```bash
grep -rn --include='*.py' -E "logger\.\w+\(.*(password|secret|token|passwd|credential)" api-server/app
```

命中 5 处，其中 **1 处是真泄露**：

```python
# api-server/app/main.py:54
logger.warning("已创建默认用户 %s，初始密码: %s（请首次登录后立即修改）", username, password)
```

启动时把明文初始密码写进容器日志（`docker logs` / Loki 可见）。其余 4 处是 `app_id[:8]`、异常文本等，可接受。

### 6.3 级别使用

`logger.info` 用于「执行进度」「agent 隧道读取结束」等高频/异常场景（`approval_service.py:898`、`agent_gateway.py:323`），生产 LOG_LEVEL=INFO 时会持续刷；建议异常路径统一 `warning/error`。

---

## 7. 超时

```bash
grep -rn --include='*.py' -E "(httpx\.(get|post|put|delete|request)|requests\.(get|...))\(" api-server/app ai-service/app | grep -v timeout
# → 仅 1 处，且是误报：oom_evidence_service.py:261 里的 `requests.get(cname, 0)` 是 dict 取名，不是 HTTP
grep -rn --include='*.py' "AsyncClient(" ...   # 16 处构造
```

| 项 | 结论 |
|---|---|
| httpx 统一入口 | ✅ `core/http_client.py:22` `_DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)`，连接池 100/20 |
| httpx 显式无限超时 | ⚠️ 3 处 `timeout=None`：`api/oracle_clients.py:92`、`api/logs.py:119`、`api/watch.py:22`（都是流式转发，属有意为之，但客户端断连依赖对端 FIN，缺心跳） |
| TLS 校验关闭 | ⚠️ `services/image_compare_service.py:219,541,661`、`image_file_reader.py:225` `verify=False`（内网自签仓库，已知设计，但无白名单校验） |
| gRPC unary（本地 executor） | ❌ **无默认 deadline**：`clients/executor_client.py:263` `def _call(req, timeout=None, ...)` → 末尾 `return fn(req, timeout=timeout, ...)`，`timeout=None` 时 gRPC 视为无限期等待。仅在 `cluster_id != 0`（走隧道/pull）分支才用 `timeout or ExecutorClient.TIMEOUT`（30s） |
| gRPC 流式 | ⚠️ `__getattr__` 对 `_STREAMING_METHODS` 直接 `return fn`，`ExecutePlan`/`BatchExecute`/`ExecPodStream` 等默认无 deadline；隧道侧由 `_remote_stream_call` 兜 300s |
| 内部网关 IsConnected | ✅ `timeout=5.0`（`executor_client.py:86`） |

调用侧证据：`grep -rn "executor_client\.\w+\(" api-server/app | wc -l` → 224 处调用，显式传 `timeout=` 的只有 **1 处**。

---

## 8. 重试

| 位置 | 策略 | 评价 |
|---|---|---|
| `services/notification_service.py:227-250` | `for attempt in range(3)` + `asyncio.sleep(2 ** attempt)`（1s/2s/4s） | 有指数退避 ✅；但**重试的是非幂等 webhook 发送**（钉钉/企微/飞书无去重键），网络抖动时同一告警可能重复推送；另外第 3 次失败后仍会 `sleep(4)`（`break` 只在成功时），白白拖 4s |
| `k8sclient/retry.go:65-88` | 3 次，200ms/500ms/1s，仅对 `IsTransientErr`（GOAWAY/EOF/429/5xx）生效 | 设计正确 ✅；无 jitter（多副本同时重试会打齐）；调用点集中在 `misc_resources.go` 的 7 处 **list** 操作，未包裹 delete/scale 等非幂等写 ✅ |
| `k8sclient/retry.go:39` `RetryOnConflict` | `retry.DefaultBackoff` 包裹 `Update`（node_meta 污点） | ✅ 标准做法 |
| `api/clusters.py:561,641` / `api/logs.py:95` | `asyncio.sleep(1~2)` 后重试 | 固定间隔、无上限逻辑说明，属「等 agent 上线」的轮询，非严格重试 |
| ai-service `model_adapter` | 有降级链（多模型失败切换），重试在 `ai-service/app/services/model_adapter.py` | 详细策略见报告 04/07，本文不重复 |

**缺退避/缺幂等的点**：`services/jenkins_client.py:139` `await asyncio.sleep(1)` 轮询构建状态（固定 1s，无上限判断可见于同函数）。

---

## 9. 并发

- `async def` 共 **1,363** 个；`asyncio.create_task` **65** 处（后台任务，多数用集合强引用防 GC，如 `core/orm_hooks.py:41 _plan_cancel_tasks` ✅）。
- **async 中阻塞调用**：
  - `agent_gateway.py:327 time.sleep(0.1)`——位于 gRPC servicer 的**同步**生成器（跑在 `ThreadPoolExecutor(max_workers=20)` 线程里），不阻塞事件循环，但会占用一个 servicer 线程最多 30s（`deadline = time.time() + 30`）。20 个 agent 同时接入即打满。
  - `services/oracle_client_service.py:256,281,289` `subprocess.run(timeout=180/120/300)`：在 `api/db_connections.py` 里经 `asyncio.to_thread` 调用 ✅；但**同一服务若被直接在 async 上下文调用**会阻塞 3~5 分钟。
  - gRPC 同步调用统一走 `asyncio.to_thread`（`executor_client.py:32,555`）✅，并有并发上限注释（A4）。
- **全局可变状态**（进程级，多副本部署时不共享）：
  - `clients/executor_client.py:378-379` `_PULL_TARGETS` / `_PULL_CHANNELS`（每 30s 由后台循环刷新，`_PULL_CHANNELS` **只增不减**）
  - `services/prometheus_service.py:94` `_http_clients: dict[int, AsyncClient]`（按 timeout 缓存，无关闭）
  - `services/feishu_service.py:21,141` `_token_cache` / `_member_cache`（无上限）
  - `services/crash_log_service.py:105-107` 三个去重 dict（pod 维度，随集群规模线性增长，无 TTL 清理）
  - `services/system_setting_service.py:19` `_cache`（有 TTL 元组 ✅）
- **gRPC 流并发写**：`agent_gateway.py` 每条 agent 连接一个 `reader` 线程 + `a.out` 队列（`queue.Queue`），写侧 `send` 与 `asyncio.run_coroutine_threadsafe` 交互；未见对同一 stream 的并发 `send` 加锁——当前是「读线程 + 主协程」两方，风险低，但 `ExecPodStream` 双向流（`_gateway_bidi_call`）多路复用同一隧道时需要确认序列化（本次未深入，见报告 04 结论）。

---

## 10. 资源泄漏

| 资源 | 位置 | 问题 |
|---|---|---|
| httpx 客户端 | `services/prometheus_service.py:94-102` | `_http_clients[timeout] = c` 建了就不关，`close_http_clients()` 只关 `core/http_client` 里的池，**这里不在其中**；实测 `grep -n "aclose\|close()" prometheus_service.py` → 0 |
| gRPC channel | `clients/executor_client.py:379-405` | `_PULL_CHANNELS` 无 `close()`；`set_pull_target(cid, url="")` 只删 `_PULL_TARGETS`，**channel 留在字典里**。agent 重建/换 IP 后每次新 URL 都新建一个 channel，永久泄漏（每个 channel 两条 HTTP/2 连接） |
| 数据库连接 | 测试运行尾部告警 | `pytest`/`unittest` 跑完出现 `ResourceWarning: Unclosed connection <aiomysql.connection.Connection ...>` + `RuntimeError: Event loop is closed`（来自 `tests/test_db_direct_dialects.py` / `test_db_r48_r50_r51.py` 里未关闭的 `aiomysql.connect` mock） |
| 文件句柄 | `grep "= open("` → 0 处裸 open | ✅ 全部 `with open(...)` 或 `Path.read_text()` |
| `AsyncSession` | `core/database.py:31-38` `get_db()` | `finally: await session.close()` ✅（`close()` 隐式回滚未提交事务） |

---

## 11. 事务

```bash
grep -rn --include='*.py' "\.commit()"   api-server/app | wc -l   # 332
grep -rn --include='*.py' "\.rollback()" api-server/app | wc -l   # 11
grep -rn --include='*.py' "begin()"      api-server/app | wc -l   # 0
```

- 无显式事务边界（0 处 `begin()`），全部是「ORM 赋值 + commit」；跨表一致性靠**分段 commit**。
- `execute_approved_plan`（`approval_service.py:851-940`）一个业务流程内至少 **4 次独立 commit**：置 executing → 执行 → 置 completed → 写知识库。中途崩溃会停在中间态（有 `except` 兜底置 failed，但兜底本身也是新 session + commit）。
- **check-then-act 竞态**（`approval_service.py:869-879`）：

```python
if plan.status in ("completed", "executing"):   # 读
    return
...
plan.status = "executing"                       # 写（无 WHERE status 条件、无行锁）
await session.commit()
```

两个并发的「审批通过」请求（双击 / IM 回调重放 / 双副本）可以同时通过上面的判断 → 同一修复计划执行两遍。同类模式见 `api/db_approvals.py:279-291`（`req.status != "pending"` → `req.status = "approved"`）。
**修法**：`UPDATE repair_plans SET status='executing' WHERE id=:id AND status NOT IN ('executing','completed')` 后检查 `rowcount==1`，或用 `SELECT ... FOR UPDATE`（`with_for_update()`）。

- 11 处 rollback 分布：集中在少数 service；**21+ 个 `api/*.py` 文件有 commit 但零 rollback**（如 `api/clusters.py` 13 次 commit、`api/on_duty.py` 7 次、`api/alerts.py` 6 次）。异常时依赖 `session.close()` 隐式回滚，行为正确但不可见、不可审计。

---

## 12. 索引

```bash
grep -rn --include='*.py' "index=True" api-server/app/models | wc -l   # 161
grep -rhoE "\.where\([A-Za-z]+\.\w+" --include='*.py' api-server/app | sed 's/.*\.//' | sort | uniq -c | sort -rn
#  47 id / 39 alert_id / 35 status / 24 created_at / 21 cluster_id / 15 user_id ...
```

| 高频查询字段 | 模型索引 | 结论 |
|---|---|---|
| `id`（47） | 主键 | ✅ |
| `alert_id`（39） | 多数有 `index=True`（`models/alert.py:82`、`repair_plan.py:17`） | ✅ |
| `status`（35） | `alert`/`repair_plan`/`diagnostic` 有（`alert.py:28`、`repair_plan.py:22`、`diagnostic.py:28`） | ⚠️ `models/execution.py:20` `ExecutionLog.status` **无索引**，而前端按 `plan_id + status` 轮询 |
| `created_at`（24） | **全仓 64 处 `created_at` 无 `index=True`**（`models/audit.py:26`、`models/alert.py:85`、`models/diagnostic.py:32,50`、`models/repair_plan.py:23`） | ❌ 审计搜索（`/audit/search` 按时间范围）、告警列表分页、报表聚合都会全表扫 |
| `cluster_id`（21） | `alert`/`ticket`/`diagnostic`/`custom_task` 等有 | 部分模型（`execution` 经 plan 间接）缺 |
| `username`（10）/`role`（9） | `models/user.py` 需核对 | `user.username` 有 `unique=True`（隐含索引）✅ |

**建议**（不改 schema，只提迁移建议）：给 `audit.created_at`、`alert_events.created_at`、`k8s_events.last_seen`、`execution_logs(status, plan_id)` 加组合索引；现有 alembic 链 123 个 revision 可继续追加。

---

## 13. 输入校验

```bash
grep -rn --include='*.py' "payload: dict" api-server/app | wc -l   # 210
grep -rn --include='*.py' -E "@router\.(get|post|put|patch|delete)" api-server/app/api | wc -l  # 495
grep -rn --include='*.py' "response_model" api-server/app | wc -l  # 0
grep -rn --include='*.py' "Body(" api-server/app | wc -l          # 0
```

- 报告 03 提到「190 处 `payload: dict`」，本次实测 **210 处**（增长来自近期新增接口）。占 495 条路由的 **42%**（写接口占比更高）。
- **TOP 文件**：

| 文件 | `payload: dict` 数 |
|---|---:|
| `api/resources.py` | 30 |
| `api/db_connections.py` | 24 |
| `api/clusters.py` | 9 |
| `api/release.py` | 8 |
| `api/agent.py` | 7 |
| `api/projects.py` / `api/approvals.py` | 各 6 |
| `api/resource_governance_ctl.py` / `notifications.py` / `host_terminal.py` / `alerts.py` / `alert_silences.py` | 各 5 |

- `: dict` 出现 676 次（含返回值、内部结构），`response_model` 与 `Body(...)` 均为 0 → 全平台无 Pydantic 契约；`app/` 下也无 `schemas/` 目录。后果：OpenAPI 文档里所有请求体是 `{}`，前端类型（`frontend/src/types.ts` 654 行）与后端靠人工同步。
- 缓解事实：多数接口在函数前几行做了 `if not payload.get("x")` 式手写校验（如 `api/db_connections.py`），但**类型错误（`"3"` vs `3`）、多余字段、超长字符串**无人拦截。

---

## 14. 问题清单

```
问题编号：QUA-01
问题名称：审批通过后的计划执行存在 check-then-act 竞态，可能重复执行同一修复计划
问题等级：P1
所属模块：审核审批 / 修复执行
问题描述：执行前用「读 status → 判断 → 置 executing → commit」两步非原子流程，并发请求（双击/IM 回调重放/双副本）可同时通过判断，导致同一计划被执行两次；DB 审核单的 approve 同样模式。
代码位置：api-server/app/services/approval_service.py:869-879；api-server/app/api/db_approvals.py:279-291
涉及函数或类：execute_approved_plan、approve_db_approval
调用链：POST /api/v1/approvals/{id}/approve → approval_service.approve → asyncio.create_task(execute_approved_plan) → plan_runner.run_plan → executor gRPC
根本原因：缺少行锁或条件更新（CAS），依赖应用层状态判断
可能影响：同一修复动作重复下发到生产集群（重复重启/重复扩缩容），审计链出现两条执行记录
修改建议：改为条件 UPDATE（WHERE status NOT IN (...)）并校验 rowcount，或 with_for_update() 锁行；IM 回调路径加幂等键
验证方法：并发 2 个 approve 请求打同一 plan_id，断言只有一次进入 plan_runner（可加单测用两个 session 并发）
是否需要立即处理：是
```

```
问题编号：QUA-02
问题名称：本地 executor 的 gRPC unary 调用默认无 deadline，服务卡死会挂满线程池
问题等级：P1
所属模块：api-server / gRPC 客户端
问题描述：_TunnelAwareStub.__getattr__ 生成的 _call 默认 timeout=None，直连分支直接 fn(req, timeout=None)，gRPC 视为无限期等待；224 处 executor_client 调用中仅 1 处显式传 timeout。
代码位置：api-server/app/clients/executor_client.py:263-283、:547（TIMEOUT=30 仅对隧道分支生效）
涉及函数或类：_TunnelAwareStub._call、ExecutorClient
调用链：任意 API → executor_client.xxx() → stub._call → grpc channel.unary_unary（无 deadline）
根本原因：默认参数 timeout=None 被原样透传给 gRPC
可能影响：executor 或 apiserver 网络半开时，asyncio.to_thread 的线程池（默认 40 线程）被逐个占满，全站接口雪崩
修改建议：_call 内改为 timeout or ExecutorClient.TIMEOUT（与隧道分支一致）；流式方法给出显式默认值
验证方法：单测 mock stub 断言 timeout 不为 None；或本地 kill -STOP executor 观察 40 个请求后接口是否全部挂起
是否需要立即处理：是
```

```
问题编号：QUA-03
问题名称：全平台无请求/响应契约（response_model=0，210 处 payload: dict）
问题等级：P2
所属模块：api 层
问题描述：495 条路由中 210 处请求体为裸 dict，response_model 与 Body(...) 均为 0，无 schemas 目录；类型/范围/多余字段完全依赖手写 if 校验。
代码位置：api-server/app/api/resources.py（30 处）、db_connections.py（24）、clusters.py（9）等
涉及函数或类：全部写接口 handler
调用链：前端 axios → /api/v1/* → handler(payload: dict)
根本原因：快速迭代期未引入 Pydantic 模型
可能影响：非法类型导致 500 而非 422；前端 types.ts 与后端字段漂移无人发现；OpenAPI 文档失去意义
修改建议：按模块逐步引入 schemas，优先 resources/db_connections/approvals 三个高风险模块；短期至少在中间件做 JSON schema 白名单校验
验证方法：给任一接口传 {"replicas": "abc"}，当前返回 500/静默错误，改造后应为 422
是否需要立即处理：否（分阶段）
```

```
问题编号：QUA-04
问题名称：巨型函数（2 个 >1000 行、12 个 >200 行）不可测试不可 Review
问题等级：P2
所属模块：stats / alert_auto / db_collector / approval
问题描述：stats._compute_dashboard 1058 行、alert_auto_service._process_one 1053 行，单函数承载 10+ 职责分支；api-server/app 共 2475 函数中 208 个超 50 行。
代码位置：api-server/app/api/stats.py:327；api-server/app/services/alert_auto_service.py:2464；db_collector.py:487/695/918；approval_service.py:851
涉及函数或类：_compute_dashboard、_process_one、_mysql_collect_health、_pg_collect_health、_oracle_health_sync、execute_approved_plan
调用链：GET /api/v1/stats/dashboard → _compute_dashboard；告警处理循环 → _process_one
根本原因：按「一个接口一个函数」堆叠，未抽子函数
可能影响：修改风险高，回归靠人眼；与报告 16 的「这些模块 0 测试」叠加
修改建议：按职责切分为 _collect_k8s_stats/_collect_ai_stats/... 与 _decide/_execute/_verify 等私有函数，保持对外签名不变
验证方法：重构后 unittest 全绿 + 接口返回字段 diff 为空
是否需要立即处理：否
```

```
问题编号：QUA-05
问题名称：SQL 切分算法整段复制（base.py 与 oracle.py 各 150 行）
问题等级：P2
所属模块：数据库方言层
问题描述：split_sql_statements(153 行) 与 _split_oracle_sql(150 行) 近乎逐行相同；另有 6 份 _parse_dt、8 份 background_loop、sql_optimize_service 内 40 行重复。
代码位置：api-server/app/services/dialects/base.py:160-312；dialects/oracle.py:347-496；api/host_audits.py:25 等 6 处 _parse_dt
涉及函数或类：split_sql_statements、_split_oracle_sql
调用链：SQL 审核单/导入 → split_sql_statements → 逐条执行
根本原因：复制粘贴后各自演进
可能影响：一处修复漏另一处，导致 Oracle 与 MySQL 的语句边界行为不一致（PL/SQL 块切错 = 执行错语句）
修改建议：提取公共扫描器（带 quote/escape 配置参数），oracle 只覆盖差异；_parse_dt 收敛到 app/utils/time.py
验证方法：对同一批含分号/引号/注释的 SQL，三方（base/mysql/oracle）切分结果一致性测试
是否需要立即处理：否
```

```
问题编号：QUA-06
问题名称：137 处 except 分支只 pass，静默吞掉异常且无日志
问题等级：P2
所属模块：全局
问题描述：861 处 except Exception（856 处 noqa: BLE001），其中 137 处首个语句为 pass；资源/告警自愈文件中最多（各 12 处）。
代码位置：api-server/app/api/resources.py（12）、services/alert_auto_service.py（12）、api/host_terminal.py（8）、api/release.py（6）
涉及函数或类：见各文件
调用链：自愈主循环 alert_auto_service._process_one → 各工具调用 → 静默失败
根本原因：以「接口不报错」为优先目标
可能影响：故障处理失败无人知晓，排障只能靠复现；监控无法统计失败率
修改建议：静默分支至少 logger.debug(exc_info=True)；业务关键路径把失败原因写入 manual_reason/审计
验证方法：grep 复查 pass 数量下降；构造异常输入确认日志中出现
是否需要立即处理：否
```

```
问题编号：QUA-07
问题名称：时间列普遍缺索引，与高频时间范围查询不匹配
问题等级：P2
所属模块：数据层
问题描述：64 处 created_at 无 index=True，而 where(created_at) 出现 24 次；ExecutionLog.status 无索引但被 plan_id+status 轮询。
代码位置：api-server/app/models/audit.py:26、models/alert.py:85、models/diagnostic.py:32,50、models/repair_plan.py:23、models/execution.py:20
涉及函数或类：AuditRecord、AlertEvent、ExecutionLog
调用链：GET /api/v1/audit/search、告警列表分页、报表聚合
根本原因：建表时只给外键加了索引
可能影响：审计表/告警事件表数据量上来后，按时间过滤全表扫，P95 劣化
修改建议：追加 alembic 迁移：ix_audit_created_at、ix_alert_events_created_at、ix_execution_logs_plan_status
验证方法：EXPLAIN 对比 type=ALL → range
是否需要立即处理：否
```

```
问题编号：QUA-08
问题名称：进程级全局缓存/连接池无上限、无清理，随集群/租户数增长泄漏
问题等级：P2
所属模块：executor_client / prometheus_service / feishu_service / crash_log_service
问题描述：_PULL_CHANNELS 只增不减（移除 pull 目标时未 close channel）；prometheus_service._http_clients 从不 aclose；feishu _token_cache/_member_cache 与 crash_log 三个去重 dict 无 TTL 上限。
代码位置：api-server/app/clients/executor_client.py:378-405；services/prometheus_service.py:94-102；services/feishu_service.py:21,141；services/crash_log_service.py:105-107
涉及函数或类：set_pull_target、_pull_channel、_client_for、crash_log_service
调用链：后台 30s 刷新 pull 目标 → _PULL_CHANNELS；告警处理 → crash_log 去重
根本原因：用模块级 dict 做缓存，未设计淘汰
可能影响：长跑进程内存/连接数缓慢上涨；agent 换 IP 后旧 channel 永久占用 fd
修改建议：移除 pull 目标时 ch.close() 并 pop；prom 客户端纳入统一 close_http_clients()；dict 加 LRU/TTL（可用 OrderedDict 或简单时间戳，无需新依赖）
验证方法：反复 set_pull_target 1→空 100 次，观察 len(_PULL_CHANNELS) 与进程 fd 数
是否需要立即处理：否
```

```
问题编号：QUA-09
问题名称：事务以分段 commit 为主，21+ 个模块零 rollback，跨表一致性靠兜底
问题等级：P2
所属模块：全局
问题描述：332 处 commit vs 11 处 rollback，0 处 begin()；审批执行流程 4 段独立提交，中途失败靠后续 except 分支再开 session 修补。
代码位置：api-server/app/services/approval_service.py:851-940；core/database.py:31-38
涉及函数或类：execute_approved_plan、get_db
调用链：approve → execute_approved_plan（4 次 commit）→ 告警/计划/审计三表
根本原因：未使用 Unit of Work 思路
可能影响：进程 kill -9 时停留在 executing 半态；审计记录与业务记录可能不同步
修改建议：状态机流转（executing→completed/failed）在同一事务内完成；启动时增加「executing 超时回收」扫描（已有 5s 兜底扫表，可扩展）
验证方法：手工 kill 进程在 executing 阶段，重启后确认无僵尸计划
是否需要立即处理：否
```

```
问题编号：QUA-10
问题名称：启动日志打印默认管理员明文初始密码
问题等级：P2
所属模块：启动流程
问题描述：创建默认用户时把明文密码写入 warning 日志，容器日志/Loki 可查。
代码位置：api-server/app/main.py:54
涉及函数或类：startup_tasks / 默认用户初始化
调用链：app 启动 lifespan → startup_tasks
根本原因：便于首次部署抄密码
可能影响：任何有日志读权限的人可拿到超管口令（若未改密）
修改建议：只打印「已创建默认用户 X，请用 ADMIN_INITIAL_PASSWORD 环境变量中的口令登录」，不回显值
验证方法：重启看日志不含密码
是否需要立即处理：是（一行改动，风险极低）
```

```
问题编号：QUA-11
问题名称：3 处 httpx 无限超时 + 5 处 verify=False 未白名单化
问题等级：P3
所属模块：日志/监控/镜像对比
问题描述：流式转发用 timeout=None（合理但有挂死风险）；镜像仓库访问全局关闭 TLS 校验，未限定目标地址。
代码位置：api-server/app/api/oracle_clients.py:92、api/logs.py:119、api/watch.py:22；services/image_compare_service.py:219,541,661、image_file_reader.py:225
涉及函数或类：ores 流式代理、image_compare
调用链：GET /api/v1/logs/stream → httpx stream
根本原因：流式场景怕超时切断
可能影响：对端不回 FIN 时连接长期占用；verify=False 可被中间人替换镜像内容
修改建议：改用 timeout=httpx.Timeout(connect=10, read=None, write=30, pool=10)；verify=False 限定为配置的 registry host 白名单
验证方法：单测断言 timeout 非 None；grep 复查 verify=False 处数
是否需要立即处理：否
```

```
问题编号：QUA-12
问题名称：通知重试无幂等键，且最后一次失败后仍等待 4s
问题等级：P3
所属模块：通知
问题描述：webhook 3 次重试（1s/2s/4s）针对非幂等外部推送；循环末尾无条件 sleep，第 3 次失败后仍阻塞 4s。
代码位置：api-server/app/services/notification_service.py:227-250
涉及函数或类：_send_webhook_with_retry
调用链：告警通知 → _send_webhook_with_retry → 钉钉/企微/飞书
根本原因：简单重试模板
可能影响：IM 收到重复告警；通知协程被无谓拖慢
修改建议：仅在 attempt < 2 时 sleep；内容中带 alert_id+时间戳供人去重，或对 IM 侧使用「更新卡片」而非重复发消息
验证方法：mock 连续失败 3 次，断言总耗时 ≈ 3s 而非 7s
是否需要立即处理：否
```

```
问题编号：QUA-13
问题名称：JSON 日志与 uvicorn 文本日志混排，TraceIdFilter 未注册导致 trace_id 常为空
问题等级：P3
所属模块：日志
问题描述：98 处 get_logger 用 JSON，15 处 logging.getLogger 走默认格式；TraceIdFilter 定义了但未挂到 handler。
代码位置：api-server/app/core/logging.py:12-60
涉及函数或类：JsonFormatter、get_logger、TraceIdFilter
调用链：任意 logger.xxx → stdout
根本原因：渐进式改造未收尾
可能影响：日志采集侧 JSON 解析失败；跨服务 trace 串联断点
修改建议：启动时统一 root handler + filter；把剩余 15 处改用 get_logger
验证方法：启动后 grep 日志行是否 100% 可 json.loads
是否需要立即处理：否
```

```
问题编号：QUA-14
问题名称：前端单文件过大（api.ts 1455 行、SqlTerminal.tsx 1244 行）
问题等级：P3
所属模块：frontend
问题描述：147 个 ts/tsx 共 35,063 行，其中 api.ts 集中了全部接口封装（1455 行），两个终端页面近千行。
代码位置：frontend/src/services/api.ts、src/pages/SqlTerminal.tsx、src/pages/WebTerminal.tsx
涉及函数或类：api 对象、SqlTerminal 组件
调用链：页面 → api.xxx → axios
根本原因：无按域拆分
可能影响：合并冲突高发、tree-shaking 失效、单测无从下手（且前端 0 测试）
修改建议：api.ts 按域拆为 services/{cluster,resource,db,alert}.ts 并统一 re-export
验证方法：tsc --noEmit 通过 + 页面回归
是否需要立即处理：否
```

```
问题编号：QUA-15
问题名称：遗留产物与旧代码副本留在工作区（logs/archive 下 979 行旧版 alert_auto_service）
问题等级：P3
所属模块：仓库卫生
问题描述：logs/archive/dedup_backup_20260920/code/ 存放旧代码副本，虽被 .gitignore 排除，但会污染 grep/wc 统计与误读。
代码位置：logs/archive/dedup_backup_20260920/code/alert_auto_service.py:1
涉及函数或类：—
调用链：—
根本原因：备份脚本直接写 logs/archive
可能影响：审计/检索误命中旧版本
修改建议：备份改存 backups/（已在 .gitignore）或压缩归档
验证方法：grep 全仓不再命中 logs/archive/*.py
是否需要立即处理：否
```

---

## 15. 无法确认事项

1. `frontend/public/monitor-bigscreen-old.html`（1,346 行）是否仍被引用 → 需读 `App.tsx` 路由与 nginx 静态配置，本次未追（可能只是死文件）。
2. `agent_gateway.py` 双向流在「同一隧道多路复用 ExecPodStream + 普通 unary」时的写序化是否有锁保护 → 需读 `bridge.go` 与 `agent_gateway.py` 全文，本次只看了 310-335 段。
3. `crash_log_service` 三个去重 dict 是否在别处有定时清理（`grep _dedup` 只看了定义与前几处用法）→ 暂时无法确认。
4. 各模型 `created_at` 无索引是否已在 alembic 迁移里手工 `CREATE INDEX`（ORM 声明与 DB 实际可能不一致）→ 需解析 123 个迁移文件，本次未做。

---

## 16. 审计覆盖范围

**已读/已统计**：
- 全仓文件行数（`find`+`wc`）、`api-server/app` 全部 272 个 py 文件 AST 函数规模、`k8s-executor` 全部 go 文件函数规模。
- `app/api`、`app/services`、`app/models`、`app/core`、`app/clients` 的关键文件：`executor_client.py`、`approval_service.py`、`agent_gateway.py`、`http_client.py`、`database.py`、`logging.py`、`orm_hooks.py`、`command_guard.py`、`dialects/{base,mysql,oracle}.py`、`prometheus_service.py`、`notification_service.py`、`stats.py`(头部)、`db_approvals.py`、`db_collector.py`(片段)。
- 前端 `frontend/src` 文件规模。

**未读**：`ai-service/app` 全量（仅统计规模与 requirements）、`k8s-executor/internal/*` 逐包细读（仅规模与 retry/gray_release 片段）、123 个 alembic 迁移、`frontend/src/pages` 逐页。原因：本报告聚焦「可量化质量指标」，功能正确性见 04/05/06 等报告。

**未执行的检查及原因**：ruff/flake8（未安装，不允许装）、compileall（写 `__pycache__`）、go vet（写 build cache，改用 go test）、覆盖率统计（无 pytest-cov，不允许装）。
