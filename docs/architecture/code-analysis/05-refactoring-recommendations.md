# 05 · 渐进式重构建议与优先级

> **代码基线**：`main @ b3b9f03`　**依据**：01~04 的源码级证据
> **路径约定**：相对项目根；`A/`=api-server/app、`I/`=ai-service/app、`F/`=frontend/src、`G/`=k8s-executor
> **标注**：【事实】源码可证 /【推断】分析判断（含全部工作量估算）/【待验证】需回归或运行期确认

---

## 0. 约束与原则（本次建议的边界）

以下为硬约束，所有建议均在其内：

1. **不推倒重建**：不引入新服务、不改部署拓扑、不改数据库表结构、不改任何对外 URL 与响应结构。
2. **不拆微服务**：`api-server` 仍是单体，拆分只发生在**进程内的文件/模块边界**。
3. **保持兼容**：现有 API、DB schema、部署方式、工具名字符串、prompt 契约均不变；所有拆分通过「原路径门面 + 再导出」实现。
4. **不为抽象而抽象**：只抽取已有 ≥3 处重复或已造成结构性风险的逻辑；一行转发、缓存四件套等合理代码保持不动（见 04 §4）。
5. **先补回归网，再动主链路**：核心链路当前 0 测试（04 §6），这是重构的前置条件而非可选项。
6. **从低波及面叶子模块入手**：`executor_client`（44 文件引用）与 `models/` 留到有回归网后再动（02 §8）。

---

## 1. 优先级矩阵

| 优先级 | 动作 | 结构收益 | 风险 | 工作量【推断】 | 前置 |
|---|---|---|---|---:|---|
| P0 | 补核心链路回归网（审批→执行、审计链、RBAC、修复验证、Agent 隧道 Python 侧） | 使后续重构可验证 | 低 | 3~5 人日 | 无 |
| P1 | **模块一**：`alert_auto_service` 门面化 + 抽 `alert_result_writer` 切断环 | 主链路从 3,867 行降到可维护粒度 | 中 | 5~8 人日 | P0 |
| P1 | **模块二**：api 层下沉 + 统一资源级鉴权依赖 | 消除 5 份复制与 12 个 api 直调 executor | 中 | 5~7 人日 | P0 |
| P1 | **模块三**：`frontend/services/api.ts` 域拆分 | 前端从 1,455 行单文件降到可定位模块 | 低 | 2~3 人日 | 无 |
| P2 | `stats.py` 三段拆分 | 消除 1,058 行函数 | 中 | 3~4 人日 | P0 |
| P2 | 枚举统一 `env_policy.py` + `/config` 下发 | 消除前后端双份规则 | 中 | 2~3 人日 | 前端域拆分完成 |
| P2 | `tool_registry` / `notification_service` 子域拆分 | 可读性、可测试性 | 中 | 4~6 人日 | P0 |
| P3 | `executor_client` transport 策略化 | 4 套传输路径收敛 | 高 | 6~9 人日 | 全量回归网 |
| P3 | `grpc/server.go` 按域注册 | Go 侧可读性 | 高 | 5~8 人日 | Go 测试补齐 |
| P3 | 共享契约包（fault_taxonomy / proto / ORM） | 消除两服务分叉 | 高 | 6~10 人日 | 契约一致性测试 |
| P3 | 网关连接表外置 Redis、`alerts` 单一写者 | 多副本与数据一致性 | 很高 | 10+ 人日 | 运行期验证环境 |

【推断】总工作量不是本建议的目标；建议按「每批 ≤2 周、每批可独立交付并回滚」推进。

---

## 2. 模块一：`A/services/alert_auto_service.py`（3,867 行）

### 2.1 现状【事实】

- 10 个子域混在一个无 class 的模块里；`_process_one:2464-3516` 单函数 1,053 行，内部直接做通知(`:2485`)、DB 写入(`:2488`)、executor 调用(`:2499`)、诊断、决策、执行、验证、知识沉淀。
- 两组循环依赖：`alert_auto_service → approval_service → alert_auto_service`、`alert_auto_service → action_verify → alert_auto_service`（02 §3），当前靠函数内延迟 import 规避。
- 12 个跨模块引用（02 §4），是全站最核心的收敛点（03 §0）。

### 2.2 目标结构【推断】

```
services/alert_auto_service.py          # 门面：只做再导出 + 极薄编排
services/alert/
    orchestrator.py                    # _process_one 的主流程（阶段函数顺序）
    gates.py                           # 置信度/环境/自主闸门/循环抑制
    recovery.py                        # 恢复扫描、关闭关联告警
    kb.py                              # 知识库去重与沉淀
    ticket.py                          # 转人工、工单冷却、超时升级
    result_writer.py                   # 【新增】告警状态/结果写入的唯一下层
```

`result_writer.py` 是切断循环依赖的关键：`approval_service` 与 `action_verify` 改为依赖它，不再反向 import `alert_auto_service`。

### 2.3 渐进步骤（每步可独立提交）

1. 补回归网：为 `process_alert` / `_execute_auto_plan` / `_verify_auto_repair_ex` / `_mark_manual_and_notify` 写行为级单测（mock 掉 `ai_client`、`executor_client`、session）。
2. 抽出 `result_writer.py`，把「改 `Alert.status/message`」的写入收敛到唯一入口；`approval_service`、`action_verify` 改依赖它。**此步不改任何对外行为**。
3. 按子域**逐个搬迁纯函数**（`kb.py` → `ticket.py` → `gates.py` → `recovery.py`），每搬一个，原文件加 `from .alert.kb import ...` 再导出，跑一次全量单测。
4. 最后把 `_process_one` 拆为阶段函数调用序列（编排骨架），保持函数名、参数、返回值、异常行为不变。
5. 门面文件仅保留导入与 `__all__`，`A/main.py:353/:420` 的定时链路引用不变。

### 2.4 收益 / 风险 / 工作量 / 验证

| 项 | 内容 |
|---|---|
| 收益【推断】 | 主链路单函数从 1,053 行降到约 150~250 行；消除 2 组循环依赖；子域可独立单测；新增告警类型不必再动 3,867 行文件 |
| 风险【推断】 | `_process_one` 隐含大量早退分支与状态写入顺序，搬迁中易丢分支；`except Exception` 宽捕获可能被无意收窄导致行为变化；定时链路回归不足 |
| 工作量【推断】 | 5~8 人日（含 2~3 人日回归网） |
| 验证 | ① 全量 `unittest`；② 对 `process_alert` 做搬迁前后的**输出快照对比**（同输入、同 mock）；③ `A/main.py:370/:420` 定时链路冒烟；④ `scripts/ci_check.sh` 全绿 |
| 回滚 | 单文件门面化，回滚只需恢复被搬函数；每步独立提交，可逐 commit 回退 |

---

## 3. 模块二：api 层下沉 + 统一资源级鉴权

### 3.1 现状【事实】

- `A/api/resources.py`(2,883) 与 `A/api/db_connections.py`(2,106) 合计 4,989 行，覆盖 14 个业务域。
- 49 个 api 文件直接写 SQL、12 个 api 文件直接调 executor（02 §2）。
- `_deny_cluster` 在 `resources.py:31`、`service_accounts.py:16`、`resource_governance.py:23`、`jvm.py:122`、`db_diagnosis.py:23` 共 5 份复制。
- 428 审核信封在 `db_connections.py:420` 与 `db_approvals.py` 各内联一份（03 G-3）。

### 3.2 目标结构【推断】

```
api/deps.py                      # 【新增】cluster_guard / db_guard（Depends 依赖）
services/resource_guard.py        # 唯一权限判定入口（包装 user_permission_service）
services/db_gateway.py           # 唯一 SQL 执行入口：_check_approval / _run_sql / 428 信封
api/resources.py                 # router 聚合 + 薄路由（保留 _deny_cluster 兼容包装）
api/db_connections.py            # 同上
services/db/…                    # CRUD / 导入导出 / 慢 SQL 等领域 service（04 §3.5）
```

### 3.3 渐进步骤

1. 新增 `deps.py`，实现 `cluster_guard`；先把 5 份 `_deny_cluster` 改为调用它（**保留原函数名为薄包装**），跑单测确认 403 行为一致。
2. 抽 `db_gateway.py`：把 `_check_approval`、`_run_sql`、428 信封结构收敛；`db_connections.py` 与 `db_approvals.py` 改为调用，响应体字段逐一比对。
3. 按域搬迁 `resources.py` 的只读路由 → 变更路由 → 节点 → HPA → 日志/文件，每批保留原路由函数为再导出。
4. `db_connections.py` 按 04 §3.5 拆 4 个领域文件，路由签名不动。
5. 最后再考虑把「api 直调 executor」下沉为 service（**这一步波及 12 个文件，放到 P2**）。

### 3.4 收益 / 风险 / 工作量 / 验证

| 项 | 内容 |
|---|---|
| 收益【推断】 | 新增路由不会漏鉴权；审核信封唯一实现；api 层从「业务容器」回到「协议适配」；两个巨型文件各降到约 300~500 行 |
| 风险【推断】 | 权限判定语义复杂（直授权 ∪ 项目授权取最高、namespace 空值退化），包装时易改变边界行为；428 信封字段变化会直接影响前端 `api.ts:47` |
| 工作量【推断】 | 5~7 人日（统一鉴权 1~2，db_gateway 1~2，域搬迁 3~4） |
| 验证 | ① `test_permissions.py` + 新增越权用例；② **OpenAPI 路由清单 diff**（路径/方法/参数逐条比对）；③ 428 响应体字段 diff；④ 资源查询冒烟（真实或 mock executor） |
| 回滚 | 兼容包装保证任何一步都可回退；建议按路由组灰度合并 |

---

## 4. 模块三：`F/services/api.ts`（1,455 行）

### 4.1 现状【事实】

473 个 `export const` 薄方法 + 大量 `interface/type`，按注释分 20+ 业务域；全站共享同一 axios 实例与两个拦截器（`:31/:40`）。另 `components/{eval,trace}/api.ts` 有 157/105 行旁路模块。

### 4.2 目标结构【推断】

```
services/api.ts                  # 兼容门面：export * from './api/index'
services/api/http.ts             # axios 实例 + 拦截器 + 泛型 http 门面
services/api/auth.ts / clusters.ts / alerts.ts / approvals.ts / db.ts / …   # 按域
services/api/index.ts            # 桶文件 export *
```

### 4.3 渐进步骤

1. 把 `api.ts:9-77` 的 axios 实例与拦截器原样搬到 `http.ts`，`api.ts` 改为 `export { http } from './api/http'` —— **第一步零业务代码改动**。
2. 按域成组剪切 `export const`，每批一个文件；用 `index.ts` 汇总。
3. 把 `interface/type` 逐步归并到 `types.ts`（可与域文件同行，不必一次性完成）。
4. 最后处理 `components/{eval,trace}/api.ts` 两个旁路模块，改为引用 `http.ts`。

### 4.4 收益 / 风险 / 工作量 / 验证

| 项 | 内容 |
|---|---|
| 收益【推断】 | 前端可按域定位接口；新增域不再放大单文件；为后续按域加测试（当前 0 测试）创造条件；`tsc` 增量编译更快 |
| 风险【推断】 | 具名导入路径若被改坏会全站编译失败；桶文件可能出现同名导出冲突（需先跑一次 `tsc --noEmit` 检查重复导出） |
| 工作量【推断】 | 2~3 人日 |
| 验证 | ① `npx tsc --noEmit`；② `npm run build`；③ 导出符号清单 diff（搬迁前后 `export` 名称集合相等）；④ 登录/告警/审批三条冒烟路径 |
| 回滚 | 纯文件搬迁，git 层面可整批回退；门面文件保证 import 路径不变 |

---

## 5. 推荐批次与依赖顺序

```
第 0 批  P0 回归网（审批→执行 / 审计链 / RBAC / 修复验证 / Agent 隧道）
   │
   ├── 第 1 批  模块三（前端 api.ts）        ← 无依赖、风险最低，可并行先做
   ├── 第 2 批  模块二 步骤 1~2（统一鉴权 + db_gateway）  ← 为模块一提供稳定边界
   └── 第 3 批  模块一（alert_auto_service 门面化 + result_writer）
   │
第 4 批  stats.py 三段拆分 / 枚举统一 / tool_registry 子域拆分
第 5 批  executor_client transport 策略化 / grpc/server.go 按域注册 / 共享契约包
第 6 批  网关连接表外置 / alerts 单一写者（需运行期验证环境）
```

【推断】把前端拆分放在第 1 批并行推进，是为了让「结构治理」尽早有可交付成果，同时不占用后端回归网的关键路径。

---

## 6. 明确不做的事

| 不做 | 理由 |
|---|---|
| 把 `api-server` 拆成多个服务 / 引入消息队列 | 超出「渐进式结构重构」范围，且当前 26 个后台协程与 inproc 网关状态本就未外置 |
| 给所有 CRUD 抽 ORM 基类 / Repository 层 | 收益低，会隐藏事务边界（04 §4） |
| 重写 `models/` 加 ForeignKey | 涉及 87 张表 + 123 迁移 + Go GORM 结构体，属于数据治理专项，不应与文件拆分同批 |
| 改 proto 契约或 RPC 命名 | 工具名/方法名进入 LLM 提示词与 agent 能力协商，属于跨语言外部契约 |
| 删除孤儿页面、合并网关三文件、统一文档口径 | 属于清理类工作，建议单独一批，先确认是否有回迁计划（01 §6.5） |
| 为减行数而合并 `_rt_store/_load/_refresh/_cached` 缓存四件套 | 现有抽象合理（04 §4） |

---

## 7. 每批通用的兼容性验收清单

1. **路由清单 diff**：搬迁前后 `GET/POST/...` 路径与方法集合完全一致。
2. **OpenAPI diff**：`docs/openapi.json` 除描述外无字段变化（可用 `scripts/openapi.sh` 生成后比对）。
3. **导出符号 diff**：Python 模块的 `__all__`/可导入名称、前端 `export` 名称集合不变。
4. **行为快照**：对主链路函数用同输入、同 mock 比对返回值与关键副作用（告警状态、审计记录、通知调用次数）。
5. **测试**：`make test` + `scripts/ci_check.sh`；新增测试覆盖本次搬迁的分支。
6. **回滚预案**：每批独立提交，门面文件保证「恢复被搬函数」即可回退，不需要改调用方。

---

## 8. 待验证事项

1. 工作量估算基于静态规模与依赖数量，未计入真实回归环境与联调时间【推断】。
2. `cluster_guard` 依赖化后，FastAPI 依赖注入顺序与现有 `_deny_cluster` 的调用时机是否完全等价，需运行期确认。
3. 前端桶文件是否存在同名导出冲突，需执行 `tsc --noEmit` 确认（本次未执行构建）。
4. `_process_one` 的宽 `except Exception` 在拆分后是否仍能保持「失败继续」语义，需逐条审。
