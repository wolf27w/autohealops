# 16 · 测试覆盖审计（Test Coverage Audit）

> 审计类型：**只读**。未修改业务代码、未安装任何依赖（pytest/pytest-cov/coverage 均未安装，因此**没有覆盖率百分比**）。
> 仓库：`/Users/wolf/Documents/project/autohealops`　审计时点：2026-11-27
> 本报告只写**真实执行过的**结果；未跑的一律标注「未执行 + 原因」。

---

## 1. 结论摘要

1. **Python 单测健康**：`api-server` **1,190 个用例全部通过**（1 skipped，3.1s）；`ai-service` **100 个用例全部通过**（0.14s）。全部跑在本机 venv、未联网、未连数据库。
2. **Go 单测健康但量少**：12 个测试文件 / **28 个 Test 函数**，**25 passed / 3 skipped / 0 failed**（7 个包 ok）。3 个 skip 都是「需要真实集群/真实 DB」的集成型用例。
3. **前端零测试**：`frontend/package.json` 只有 `dev/build/preview` 三个脚本，无 test 框架、无 test 依赖（报告 02 已确认）。`npx tsc --noEmit` 通过（类型层面无错），但行为无任何自动化保护。
4. **核心链路存在系统性空白**：`approval_service.py`（1,055 行，审批通过→执行修复）、`audit_service.py`（SHA256 审计哈希链）、`rbac_service.py`（集群内 RBAC 同步）、`db_approval_service.py`（DB 变更审核执行）、`action_verify.py`（执行后验证）——**这 5 个模块 0 个测试引用**。
5. **HTTP 契约层几乎无测试**：97 个测试文件里只有 **1 个**用 `TestClient`（`test_permissions.py`，用于越权 403 断言），其余 494 条路由在 HTTP 层没有被自动验证过。
6. **CI 只跑 Python 单测，不跑 Go 单测**：`scripts/ci_check.sh` 第 5/8 步跑两个 Python 的 unittest，**没有 `go test`**；也没有覆盖率门禁、没有前端构建（要 `--with-frontend-build` 才跑）。28 个 Go 用例在 CI 里从未执行。
7. **测试环境与生产依赖不一致**：`ai-service/.venv` 的实际安装版本与 `requirements.txt` 大面积不符（例如 fastapi 0.141.1 vs 0.115.0、pydantic 2.13.4 vs 2.9.2、redis 8.1.0 vs 5.0.8、openai 3.1.0 vs 1.51.0），本地 100 个用例的「通过」不能直接代表生产依赖组合。
8. **测试副作用可见**：跑完 api-server 全量后出现 `ResourceWarning: Unclosed connection <aiomysql...>` + `RuntimeError: Event loop is closed`，说明有测试创建了真实 aiomysql 连接但未关闭（`tests/test_db_direct_dialects.py` / `tests/test_db_r48_r50_r51.py` 中的 mock 不彻底）。

---

## 2. 工具与环境（先说清能不能跑）

| 探测项 | 结果 | 影响 |
|---|---|---|
| `api-server/.venv/bin/python -V` | Python 3.11.16 | ✅ 可跑 unittest |
| `python -m pytest` | ❌ No module named pytest | **pytest 相关全部跳过** |
| `python -m coverage` | ❌ No module named coverage | **无覆盖率数字** |
| `pytest.ini` / `setup.cfg` / `tox.ini` / `.coveragerc` | 均不存在 | 无测试配置、无覆盖率阈值 |
| `go version` | go1.26.6 | ✅ 可跑 go test |
| `node -v` / `npx tsc -v` | v24.18.0 / 5.9.3，`node_modules` 已存在 | ✅ 可跑 tsc（不联网） |
| `frontend` test 脚本 | 无 | 前端测试无法执行（不存在） |

**副作用控制**：Python 测试用 `PYTHONDONTWRITEBYTECODE=1` 执行（不写 `__pycache__`）；Go 测试用 `GOPROXY=off GOFLAGS=-mod=readonly`（不联网、不改 `go.mod`/`go.sum`）；`tsc --noEmit`（不产出文件）。

---

## 3. 测试资产统计

```bash
ls api-server/tests/test_*.py | wc -l                      # 97
grep -rn --include='test_*.py' "def test_" api-server/tests | wc -l   # 1190
ls ai-service/tests/test_*.py | wc -l                      # 14
grep -rn --include='test_*.py' "def test_" ai-service/tests | wc -l   # 100
grep -rn --include='*_test.go' "^func Test" k8s-executor | wc -l      # 28
find k8s-executor -name "*_test.go" | wc -l                # 12
```

| 语言/服务 | 测试文件 | 用例数（函数级） | 被测代码规模 | 密度（用例/千行） |
|---|---:|---:|---:|---:|
| api-server (Python) | 97 | 1,190 | 68,888 行（`app/`） | 17.3 |
| ai-service (Python) | 14 | 100 | 5,971 行 | 16.7 |
| k8s-executor (Go) | 12 | 28 | 28,750 行 | **1.0** |
| frontend (TS/TSX) | 0 | 0 | 35,063 行 | **0** |

Go 用例分布（`grep "^func Test" | cut`）：

| 文件 | 用例数 |
|---|---:|
| `internal/executor/revision_ops_test.go` | 7 |
| `internal/k8sclient/retry_test.go` | 3 |
| `internal/k8sclient/crypto_test.go` | 3 |
| `internal/agentdb/config_test.go` | 3 |
| `internal/watcher/pod_evicted_test.go` | 2 |
| `internal/hostagent/hostagent_test.go` | 2 |
| `internal/agentdb/import_sql_test.go` | 2 |
| `internal/agentbridge/bridge_kubeconfig_test.go` | 2 |
| `internal/watcher/sink_live_test.go` | 1 |
| `internal/watcher/pod_watcher_test.go` | 1 |
| `internal/executor/exec_in_pod_test.go` | 1 |
| `internal/agentbridge/pull_test.go` | 1 |

Go 包覆盖率（有测试的包 / 总包）：**7 / 19**，`proto`、`grpc`、`database`、`logstream`、`metrics`、`prometheus`、`quota`、`rbac`、`script`、`bootstrap`、`config`、`eventstore`、`health`、`jvm` 全部「no test files」——其中 `internal/grpc/server.go` 1,126 行是全部 gRPC 入口，无任何测试。

---

## 4. 真实执行结果（逐条，含命令）

### 4.1 api-server（Python unittest）

```bash
cd api-server
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

实测输出（尾部）：

```
Ran 1190 tests in 3.123s

OK (skipped=1)
```

| 指标 | 数值 |
|---|---:|
| 收集/执行 | 1,190 |
| passed | **1,189** |
| failed | **0** |
| error | **0** |
| skipped | **1**（`test_automation_policy.TestOverrides.test_library_forbidden_cannot_be_overridden` — 原因：`动作库当前没有 forbidden 动作`） |
| 耗时 | 3.123s |

副作用告警（真实输出，非测试失败）：

```
ResourceWarning: Unclosed connection <aiomysql.connection.Connection object at 0x10f975a90>
Exception ignored in: <function Connection.__del__ ...>
RuntimeError: Event loop is closed
```

来源：`tests/test_db_direct_dialects.py`、`tests/test_db_r48_r50_r51.py` 里 `patch` 掉的 `aiomysql.connect` 仍构造了真实 Connection 对象且未 `close()`。

### 4.2 ai-service（Python unittest）

```bash
cd ai-service
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

实测输出：

```
Ran 100 tests in 0.144s

OK
```

| 指标 | 数值 |
|---|---:|
| 执行 | 100 |
| passed | **100** |
| failed / error / skipped | 0 / 0 / 0 |
| 耗时 | 0.144s |

> ⚠️ 这 100 个用例跑的依赖组合与生产不一致（见 §6.4 TST-05）。

### 4.3 k8s-executor（Go）

```bash
cd k8s-executor
GOPROXY=off GOFLAGS=-mod=readonly go test ./... -count=1
```

实测输出（节选）：

```
ok   github.com/autohealops/k8s-executor/internal/agentbridge  0.853s
ok   github.com/autohealops/k8s-executor/internal/agentdb      0.624s
ok   github.com/autohealops/k8s-executor/internal/executor     0.733s
ok   github.com/autohealops/k8s-executor/internal/hostagent    0.622s
ok   github.com/autohealops/k8s-executor/internal/k8sclient    0.914s
ok   github.com/autohealops/k8s-executor/internal/watcher      0.781s
?    ...（其余 13 个包 no test files）
```

`-v` 计数：

| 指标 | 数值 |
|---|---:|
| Test 函数总数 | 28 |
| passed（`--- PASS`） | **25** |
| skipped | **3** |
| failed | **0** |

3 个 skip（真实原因）：

| 用例 | 跳过原因（代码内判断） |
|---|---|
| `agentbridge.TestBridgeCallRealCluster` | 需要真实集群 kubeconfig |
| `k8sclient.TestDecryptPythonVectors` | 需要 Python 侧生成的测试向量文件 |
| `watcher.TestDbSinkLive` | 需要真实 MySQL（live DB） |

### 4.4 frontend（类型检查）

```bash
cd frontend && npx tsc --noEmit
```

实测：**无输出，exit=0**。即 TypeScript 类型检查通过。
**测试未执行**：项目无 test 脚本、无 vitest/jest/playwright 依赖 → 「未执行，原因是根本不存在」。

---

## 5. 覆盖缺口（按风险）

### 5.1 核心链路测试映射

统计方法：在 `api-server/tests` + `ai-service/tests` 中搜索模块名引用（`grep`），命中数 0 = 无任何测试触及。

| 核心链路 | 模块 | 规模 | 被测试引用 | 判定 |
|---|---|---|---:|---|
| **审批通过 → 执行修复** | `services/approval_service.py` | 1,055 行 | **0** | ❌ 完全无测试 |
| **审计哈希链** | `services/audit_service.py` | 308 行（含 `verify_audit_chain`） | **0** | ❌ 完全无测试（防篡改能力未验证） |
| **集群内 RBAC 同步** | `services/rbac_service.py` | — | **0** | ❌ 完全无测试 |
| **DB 审核单执行** | `services/db_approval_service.py` | 421 行 | **0** | ❌ 完全无测试 |
| **执行后验证** | `services/action_verify.py` | — | **0** | ❌ 完全无测试 |
| **修复计划编排** | `services/plan_runner.py` | — | 2（间接） | ⚠️ 仅间接 |
| **告警自动处置决策** | `services/alert_auto_service.py` | 3,867 行 | 12（间接） | ⚠️ 只测了决策子函数，主循环 `_process_one`(1,053 行) 无直接测试 |
| **AI ReAct 循环** | `services/agent_loop.py` | 1,149 行 | 4 | ✅ 有（`test_agent_loop`、`test_agent_context_trim` 等） |
| **命令白名单** | `services/command_guard.py` | — | 1 | ✅ 有（`test_command_guard`） |
| **决策评分/自治门** | `decision_score.py` / `autonomy_gate.py` | — | 1 / 2 | ✅ 有 |
| **权限中间件** | `middleware/permission.py` | — | 4 | ✅ 有（`test_permissions.py` 用 TestClient 断言 403） |
| **资源查询服务** | `services/resource_service.py` | 3,882 行级 | **0** | ❌ 无测试（依赖 executor，难测但也是最大 API 面） |
| **定时任务调度** | `services/scheduler_service.py` | — | **0** | ❌ 无测试 |
| **升级/超时审批** | escalation 相关 | — | **0** | ❌ 无测试 |

### 5.2 k8s-executor 危险动作

`internal/executor` 26 个 go 文件，只有 2 个测试文件（7 + 1 用例）：

| 危险动作 | 函数 | 测试 |
|---|---|---|
| 扩缩容 | `execute.go:508 execScale` | ❌ 无 |
| 删除资源 | `execute.go:557 execDelete`、`pod_list_events.go:494 DeleteResource` | ❌ 无 |
| HPA 删除 | `hpa_ops.go:128 DeleteHPA` | ❌ 无 |
| 驱逐清理 | `pod_evicted.go:12 CleanupEvicted` | ⚠️ 间接（`pod_evicted_test.go` 测的是去重/节流判定） |
| 回滚计划 | `rollback.go:27 RollbackPlan` | ❌ 无（`revision_ops_test.go` 测的是 `ControllerRevision` 选择/模板解析这些 helper，用 `fake.NewSimpleClientset`，**不覆盖 `rollbackDeployment`/`rollbackStatefulSet` 的实际执行**） |
| Pod 内执行 | `exec_in_pod_test.go` | ⚠️ 1 个用例（命令解析） |
| 执行计划编排 | `execute.go`（`ExecutePlan` 流） | ❌ 无 |

结论：**最危险的部分（改生产集群的写操作）测试最少**，这与「executor 是系统的手」的定位不匹配。

### 5.3 前端

0 测试。`frontend/src` 147 个 ts/tsx、35,063 行，包括 Web 终端（xterm + WebSocket）、SQL 终端（DDL 执行）这些**误操作即造成事故**的页面，均无组件测试或 E2E。

---

## 6. 测试质量问题

### 6.1 以「纯函数单测」为主，无集成测试

- 97 个 Python 测试文件中 **37 个**使用 `unittest.mock`/`AsyncMock`/`MagicMock`；只有 **1 个**（`test_permissions.py`）用 `fastapi.testclient.TestClient` 走真实路由栈。
- 无任何数据库 fixture（无 sqlite 内存库、无 testcontainers、无 docker-compose 测试栈）→ 所有 SQL/ORM 行为（包括报告 15 提到的索引、事务、并发）**零验证**。
- 无任何 gRPC 端到端测试（Python 侧 stub 全 mock；Go 侧 `TestBridgeCallRealCluster` 被 skip）。

### 6.2 测试偏向「实现细节」而非「契约」

大量用例断言私有函数（`_trim_agent_messages`、`_split_sql`、`_parse_dt`、`detect_scenario`），这类测试在重构时报错但抓不到业务回归。典型：`test_agent_context_trim.py` 断言 `AGENT_KEEP_TOOL_MSGS` 常量行为——常量一改就红，但真正风险（上下文超限导致模型报错）没有被覆盖。

### 6.3 CI 覆盖面不足（重要）

读 `.github/workflows/ci.yml` + `scripts/ci_check.sh`：

| CI 步骤 | 内容 | 问题 |
|---|---|---|
| 1/8 | proto 一致性 diff | ✅ |
| 2/8, 3/8 | `compileall`（两服务） | ✅ |
| 3/8 | `npx tsc -b --noEmit` | ✅ |
| 4/8 | `go build ./cmd/executor` | ⚠️ CI 声明 `go-version: '1.22'`，而 `go.mod` 是 `go 1.26.6` → 依赖 `GOTOOLCHAIN=auto` 联网拉工具链；离线或禁网环境直接失败（详见报告 17 DEP-01） |
| **5/8** | **Python unittest（api + ai）** | ✅ 唯一真正跑测试的步骤 |
| 6/8 | 跨语言 crypto 互测 | ✅ |
| 7/8 | OpenAPI 漂移检查 | ⚠️ 只比路径/方法（因为无 response_model，字段无法比） |
| 8/8 | K8s 配置覆盖度 | ✅ |
| — | **`go test ./...`** | ❌ **完全缺失**：28 个 Go 用例在 CI 中从不执行 |
| — | **覆盖率阈值** | ❌ 无 |
| — | **前端构建** | ⚠️ 仅 `--with-frontend-build` 时执行，默认 workflow 未传该参数 |
| — | **前端测试** | ❌ 不存在 |

### 6.4 测试环境漂移（ai-service）

```bash
grep -vE '^#|^$' ai-service/requirements.txt | while ... pip show ...
```

| 包 | requirements.txt | ai-service/.venv 实际 |
|---|---|---|
| fastapi | 0.115.0 | **0.141.1** |
| pydantic | 2.9.2 | **2.13.4** |
| pydantic-settings | 2.5.2 | **2.15.0** |
| httpx | 0.27.2 | **0.28.1** |
| aiomysql | 0.2.0 | **0.3.2** |
| redis | 5.0.8 | **8.1.0** |
| openai | 1.51.0 | **3.1.0** |
| anthropic | 0.39.0 | **0.122.0** |
| numpy | 2.1.1 | **2.4.6** |
| uvicorn | 0.30.6 | **0.52.3** |
| SQLAlchemy | 2.0.35 | **2.0.52** |

`api-server/.venv` 与 requirements **一致**（仅 extras 写法不同），ai-service **11 个直接依赖全部不一致**。openai 1.51 → 3.1 是跨大版本，`model_adapter` 的调用方式/异常类型都可能已变，而 100 个用例是在 3.1 上跑绿的。

### 6.5 其他

- 无 `conftest.py`、无共享 fixture，97 个文件各自造数据。
- 32 个文件使用 `IsolatedAsyncioTestCase`（异步测试覆盖尚可 ✅）。
- 1 个 skipped 用例原因写死为「动作库当前没有 forbidden 动作」——这是「测试跟随实现」的反模式，动作库恢复后该断言会失效而无人知。

---

## 7. 最小必要测试清单（按风险排序，12 条）

> 目标不是追求覆盖率数字，而是先给「改错就出事」的路径加护栏。每条都可在现有 `unittest` + `fake clientset` + `TestClient` 体系内完成，**不需要新依赖**。

| # | 优先级 | 测试点 | 落在哪个文件 | 断言要点 |
|---:|---|---|---|---|
| 1 | P0 | 审批通过后并发双执行只跑一次 | `tests/test_approval_execute.py`（新建） | 两个 session 同时调 `execute_approved_plan(plan_id)`，`plan_runner.run_plan` 只被调用 1 次（mock 计数） |
| 2 | P0 | 非 approved 审批单不得执行计划 | 同上 | `appr.status="cancelled"` 时直接 return，无 gRPC 调用 |
| 3 | P0 | 审计哈希链防篡改 | `tests/test_audit_chain.py`（新建） | 造 3 条记录 → 改中间一条的 `action` → `verify_audit_chain()` 返回失败且定位到该条 |
| 4 | P0 | DB 审核单并发 approve 只生效一次 | `tests/test_db_approval.py`（新建） | 两次并发 approve，第二次返回 400；`db_approval` 执行任务只入队一次 |
| 5 | P1 | 危险命令拦截绕过用例 | 扩充 `tests/test_command_guard.py` | `sudo rm  -rf /`（多空格）、`r""m -rf /`、`DROP  DATABASE x`、`cat x | dd of=/dev/sda` 均应 blocked |
| 6 | P1 | gRPC 调用默认带 deadline | `tests/test_executor_client.py`（新建） | 用 `MagicMock` 替换 stub，断言 `timeout is not None`（覆盖报告的 QUA-02） |
| 7 | P1 | 集群内 RBAC 同步产物正确 | `tests/test_rbac_service.py`（新建） | 给定角色→生成 RoleBinding 的 `subjects[].name`（group 命名）与 `roleRef` 完全匹配；viewer 不应拿到 edit 角色 |
| 8 | P1 | 越权矩阵（表驱动覆盖全部 admin 路由） | 扩充 `tests/test_permissions.py` | 遍历 `app.routes` 中带 `require_admin` 的路由，viewer token 一律 403 |
| 9 | P1 | 修复计划编排的失败回滚 | `tests/test_plan_runner.py`（扩充） | 3 步计划第 2 步失败 → 第 1 步的 rollback 命令被下发，整体返回 failed |
| 10 | P1 | Go：扩缩容/删除参数校验 | `internal/executor/exec_scale_test.go`（新建） | `replicas<0`、跨命名空间、kind 不支持 → 返回 error 且**不**调用 clientset（fake clientset 的 Actions 为空） |
| 11 | P2 | Go：RollbackPlan 回滚顺序 | `internal/executor/rollback_test.go`（新建） | 逆序执行已完成步骤；中途失败不重复回滚同一步 |
| 12 | P2 | 告警「决策=approve」时不得直接执行 | `tests/test_alert_auto_gate.py`（新建） | `_process_one` 在 `decision=approve` 分支下不产生 `ExecutePlan` 调用（mock executor_client） |

补充建议（低成本、高收益）：
- 在 `scripts/ci_check.sh` 增加 `( cd k8s-executor && go test ./... -count=1 )`——28 个已有用例本就全绿，接进去零成本（TST-03）。
- 修掉测试里的 aiomysql 未关闭连接（TST-08），让 `-W error::ResourceWarning` 可用。

---

## 8. 问题清单

```
问题编号：TST-01
问题名称：审批通过→执行修复的核心链路 0 测试，且存在并发重复执行风险
问题等级：P1
所属模块：审核审批 / 修复执行
问题描述：1,055 行的 approval_service.py 未被任何测试文件引用；同链路存在 check-then-act 竞态（见 QUA-01），一旦并发重复执行会直接打到生产集群。
代码位置：api-server/app/services/approval_service.py:851-940（无对应 tests/*.py）
涉及函数或类：execute_approved_plan、approve
调用链：POST /api/v1/approvals/{id}/approve → execute_approved_plan → plan_runner → executor gRPC
根本原因：该链路依赖 DB/gRPC，早期用「手工点一遍」代替测试
可能影响：审批语义（必须人工批准才能动生产）无法被回归保护
修改建议：按 §7 #1/#2 补两个用例，mock plan_runner 与 executor_client 即可，无需 DB
验证方法：故意去掉 status 判断，测试应失败
是否需要立即处理：是
```

```
问题编号：TST-02
问题名称：审计 SHA256 哈希链无任何测试
问题等级：P1
所属模块：审计
问题描述：audit_service.py 提供 create_audit_record（带前序哈希）与 verify_audit_chain，但 97 个测试文件中 0 引用；防篡改是合规前提，却只靠人工验证。
代码位置：api-server/app/services/audit_service.py:31-33,116（无对应测试）
涉及函数或类：create_audit_record、verify_audit_chain、_hash
调用链：所有写操作 → create_audit_record；GET /api/v1/audit/verify-chain → verify_audit_chain
根本原因：缺少「造链 + 改一条 + 验链失败」的测试模式
可能影响：哈希链实现若在某次重构中被破坏（字段顺序/编码变化），篡改将不可检出且无人知晓
修改建议：按 §7 #3 补用例，纯内存构造记录即可
验证方法：改动中间记录后 verify 返回 False 并给出首个断链位置
是否需要立即处理：是
```

```
问题编号：TST-03
问题名称：CI 不执行 go test，28 个 Go 用例形同虚设
问题等级：P1
所属模块：CI
问题描述：scripts/ci_check.sh 8 个步骤里只有 Python unittest，没有 go test；k8s-executor 的 28 个测试本机全绿，但合并前无人跑。
代码位置：scripts/ci_check.sh:33-36；.github/workflows/ci.yml:47
涉及函数或类：—
调用链：GitHub Actions → verify job → ci_check.sh
根本原因：CI 脚本按「编译通过」标准编写，测试未纳入
可能影响：executor（改生产集群的手）的回归完全依赖开发者自觉
修改建议：在第 5 步后加 `( cd k8s-executor && go test ./... -count=1 )`；顺带对齐 go 版本（见 DEP-01）
验证方法：故意改坏 revision_ops 的断言，CI 应红
是否需要立即处理：是
```

```
问题编号：TST-04
问题名称：无覆盖率统计与门槛
问题等级：P2
所属模块：CI / 质量门禁
问题描述：venv 内无 pytest/coverage/pytest-cov，仓库无 .coveragerc/pytest.ini，CI 无覆盖率报告；无法回答「改动的代码是否被覆盖」。
代码位置：api-server/（无 pytest.ini、.coveragerc）；scripts/ci_check.sh
涉及函数或类：—
调用链：—
根本原因：测试以 unittest 直跑，未引入测量
可能影响：新增代码可以零测试合入且无人发现
修改建议：允许的话引入 coverage.py（或用 `python -m trace` 的轻量替代）；先在 CI 输出报告不设门槛，稳定后再设 diff 覆盖率
验证方法：CI 日志出现覆盖率表
是否需要立即处理：否
```

```
问题编号：TST-05
问题名称：ai-service 测试环境与生产依赖严重漂移，测试结论不可移植
问题等级：P2
所属模块：ai-service / 环境
问题描述：ai-service/.venv 的 11 个直接依赖全部与 requirements.txt 不一致（fastapi 0.141.1 vs 0.115.0、openai 3.1.0 vs 1.51.0、redis 8.1.0 vs 5.0.8 等）；100 个用例在这个组合下跑绿。
代码位置：ai-service/requirements.txt:1-12 vs ai-service/.venv（实测 pip show）
涉及函数或类：model_adapter、diagnosis_service
调用链：unittest → app.services.* → openai/anthropic SDK
根本原因：venv 长期未按 requirements 重建
可能影响：生产镜像（按 requirements 装）与本地测试组合不同，绿测不代表生产可用；openai 1.x→3.x 是破坏性升级
修改建议：重建 ai-service/.venv 并 `pip install -r requirements.txt`，重跑 100 个用例；若确实要升版本，先改 requirements 再测
验证方法：`pip freeze` 与 requirements 逐行 diff 为空
是否需要立即处理：是（但需老板同意重建 venv，本次只读未动）
```

```
问题编号：TST-06
问题名称：无集成/E2E 测试，HTTP 层仅 1 个测试文件
问题等级：P2
所属模块：测试体系
问题描述：97 个 Python 测试中仅 test_permissions.py 使用 TestClient；无 DB fixture、无 gRPC 端到端、无前端测试（0 个）。
代码位置：api-server/tests/test_permissions.py:103-110（唯一 TestClient）；frontend/package.json:6-10（无 test 脚本）
涉及函数或类：—
调用链：—
根本原因：测试策略停留在「纯函数层」
可能影响：路由拼装、依赖注入、序列化、鉴权顺序等「只有集成才暴露」的问题无保护（报告 15 的 210 处 dict 请求体正是这类风险）
修改建议：先补一组「核心 10 条路由的 TestClient 冒烟」（登录/列表/告警/审批/审计），DB 用 mock session 或 sqlite in-memory
验证方法：新增测试能在 30s 内跑完且不联网
是否需要立即处理：否
```

```
问题编号：TST-07
问题名称：executor 危险写操作（scale/delete/rollback）几乎无测试
问题等级：P2
所属模块：k8s-executor
问题描述：internal/executor 26 个文件仅 2 个测试文件；execScale/execDelete/DeleteResource/DeleteHPA/RollbackPlan 均无直接用例；已有 revision_ops 测试只覆盖 helper。
代码位置：k8s-executor/internal/executor/execute.go:508,557；hpa_ops.go:128；rollback.go:27
涉及函数或类：execScale、execDelete、DeleteResource、RollbackPlan
调用链：api-server → gRPC ExecuteCommand/RollbackPlan → executor 执行
根本原因：写操作测试需要 fake clientset 构造，成本高于纯函数
可能影响：参数校验（replicas、命名空间、kind）缺陷可导致误删/误扩
修改建议：按 §7 #10/#11 用 `fake.NewSimpleClientset` 补 4~6 个用例，重点断言「非法输入不产生任何 clientset Action」
验证方法：断言 fake clientset 的 `Actions()` 长度
是否需要立即处理：否
```

```
问题编号：TST-08
问题名称：测试留下未关闭的 aiomysql 连接（ResourceWarning）
问题等级：P3
所属模块：测试质量
问题描述：全量运行尾部出现 Unclosed connection / Event loop is closed；mock 不彻底导致真实连接对象被创建。
代码位置：api-server/tests/test_db_direct_dialects.py:1-40、tests/test_db_r48_r50_r51.py
涉及函数或类：TestDialect*（patch aiomysql.connect）
调用链：unittest → db_direct_service → aiomysql.connect（mock 返回真实对象）
根本原因：MagicMock 未替换 `close`/`__aenter__` 的协程语义
可能影响：CI 里偶发噪音，掩盖真实资源泄漏告警（报告 15 §10 的真实泄漏因此不易被发现）
修改建议：用 `AsyncMock` + `spec` 构造假连接并显式 `await conn.close()`；或 `-W error::ResourceWarning` 强制暴露
验证方法：加 `-W error::ResourceWarning` 后全绿
是否需要立即处理：否
```

```
问题编号：TST-09
问题名称：测试用 skip 表达「当前实现没有该动作」，实现变化后静默失效
问题等级：P3
所属模块：测试质量
问题描述：test_automation_policy 里 `skip('动作库当前没有 forbidden 动作')`，属于「测试跟随实现」，动作库恢复后该断言不会被重新启用。
代码位置：api-server/tests/test_automation_policy.py（TestOverrides.test_library_forbidden_cannot_be_overridden）
涉及函数或类：TestOverrides
调用链：—
根本原因：用 skip 代替 fixture 构造
可能影响：安全断言长期不生效
修改建议：改为自造一个 forbidden 动作的临时策略对象（不依赖动作库现状）
验证方法：用例变 PASS 而非 skip
是否需要立即处理：否
```

```
问题编号：TST-10
问题名称：前端 0 测试，终端类高危页面无任何自动化保护
问题等级：P2
所属模块：frontend
问题描述：35,063 行 ts/tsx 无测试框架；WebTerminal（xterm+WS）、SqlTerminal（DDL 执行）等误操作即出事故的页面零覆盖。
代码位置：frontend/package.json:6-10（scripts 无 test）、frontend/src/pages/SqlTerminal.tsx（1,244 行）、WebTerminal.tsx（989 行）
涉及函数或类：SqlTerminal、WebTerminal
调用链：浏览器 → /api/v1/db-connections/*/execute、/hosts/{id}/terminal(WS)
根本原因：前端未引入测试基建
可能影响：SQL 拼接、危险操作二次确认等关键交互只能靠手点
修改建议：至少引入 vitest + 对「危险操作确认逻辑」「SQL 编辑器拼装」做纯函数级测试（不引 E2E）
验证方法：`npm run test` 存在且绿
是否需要立即处理：否
```

---

## 9. 无法确认事项

1. **真实覆盖率百分比** — 未安装 coverage/pytest-cov，且不允许安装，故全文无覆盖率数字。若需，可在允许联网安装的环境执行 `python -m coverage run -m unittest discover`。
2. **CI 上 `go build` 是否真的失败** — 本地无 1.22 工具链可复现；结论基于 `go.mod:3 go 1.26.6` 与 `ci.yml:28 go-version: '1.22'` 的静态对比 + `GOTOOLCHAIN=auto` 行为推断，未实跑（需联网拉工具链）。
3. **`internal/grpc/server.go` 是否有 Go 侧集成测试计划** — 仓库内无相关 TODO/文档，暂时无法确认。
4. **前端是否存在手工测试脚本**（如 `scripts/` 下的 smoke test）— `ls scripts/` 有 18 个脚本，本次只读了 `ci_check.sh`，未逐个确认是否有前端冒烟。

---

## 10. 审计覆盖范围

**已执行**：`api-server` 全量 unittest（1,190）、`ai-service` 全量 unittest（100）、`k8s-executor` 全量 go test（28）、`frontend` `tsc --noEmit`。
**已读**：`.github/workflows/ci.yml`、`scripts/ci_check.sh`、`frontend/package.json`、两个 `requirements.txt`、`k8s-executor/go.mod`、12 个 Go 测试文件与 97+14 个 Python 测试文件的导入关系（脚本扫描）、`revision_ops_test.go` 全文。
**未读**：97 个 Python 测试文件逐行（只做导入/命名/模式的批量统计）、Go 测试文件逐个细读（除 revision_ops）。**未执行**：覆盖率、pytest、lint、前端测试（不存在）。
