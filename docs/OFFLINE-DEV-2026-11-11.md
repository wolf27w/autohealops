# 离线可开发清单（2026-11-11 晚起：无集群、无数据库连接）

> 前提：本机有 `frontend/node_modules`、`api-server/.venv`、Go module cache，**编译/类型检查/纯函数单测都能跑**。
> 原则：只写代码 + 静态验证（tsc / eslint / py_compile / go build / 纯单测）；**不执行迁移、不调真实接口、不碰集群**。

## 一、可做（离线，按优先级）

### 1. R32-P0 平台骨架（纯前端，2d）
- `frontend/src/components/MainLayout.tsx`：顶栏重构（产品名 / 全局搜索 / 刷新 / 大屏 / 通知 / 主题 / 用户；**去掉环境与集群切换**）
- 菜单**三级合并为两级**：「故障诊断」一项 + 页内 Tab（集群/应用/数据库）
- 新增 `frontend/src/components/GlobalRefreshContext.tsx`（顶栏刷新广播）
- `frontend/src/App.tsx`：新增 `/clusters/:id/*` 路由骨架；`/fault-diagnosis` 合并
- 验证：`npx tsc -b`、`npx eslint src`、`npm run build`

### 2. R32-P1 集群内重构（纯前端，3~4d，可拆并行）
新文件（互不冲突，适合并行）：
- `pages/cluster/ClusterLayout.tsx`（左导航 + 返回平台 + 类型下拉）
- `pages/cluster/WorkloadsList.tsx`（kind 由路由/下拉决定）
- `pages/cluster/WorkloadDetail.tsx`（整页；页签：概览/资源状态/元数据/环境变量/事件/监控）
- `pages/cluster/PodsList.tsx` / `ServicesList.tsx` / `StorageConfig.tsx` / `NodesPage.tsx`
- 父会话收口：`App.tsx` 路由、`services/api.ts`、`MainLayout.tsx`

### 3. R32-P2 首页重写（纯前端，1~2d）
- `pages/Dashboard.tsx` 按 A+B 合并版重写（健康分 → 5 KPI → 趋势/构成 → 待办队列 → 集群水位/DB 健康）
- 只用现有接口，不改后端

### 4. R32-P3 大屏后端 + 前端（代码可写，验证靠单测）
- `api-server/app/services/screen_bus.py`（订阅表 + `publish`，Redis pub/sub 预留）
- `api-server/app/api/screens.py`：`GET /api/v1/stats/screen/stream`（SSE，JWT→一次性 ticket）
- 事件挂点（代码改动小）：`alert_auto_service`（告警新建/合并）、`execution_service`（自愈完成）、`ticket_service`（建单/关单）
- 前端：`frontend/public/monitor-bigscreen.html` 换 SSE 消费 + 局部刷新 + 绿块依次闪烁
- 验证：纯函数单测（帧组装/增量去重）+ `py_compile`；**真实 SSE 联调等有环境再做**

### 5. R31 诊断会话共享（代码可写，迁移不执行）
- 模型加 `shared/shared_at/shared_by` + 写迁移文件（**不 upgrade**）
- `GET /agent/sessions` 可见性 = 自己的 ∪ 共享且同集群同 ns 有权限；`GET /messages` 加权限校验
- `POST /agent/sessions/{id}/share`
- 前端：会话头部共享开关 + 列表「共享」标签
- 验证：权限判定纯函数单测

### 6. 小项（半天内）
- `resources.py` 之类"同名常量重复定义"再扫一遍（今天已修 `_WORKLOAD_KINDS`）
- 菜单权限矩阵复核（admin/operator/viewer 各自能看到哪些项）
- 前端废弃路由清理（`ClusterDetail.tsx` 拆分后残留）

## 二、不能做（需要环境/集群/DB）

| 事项 | 原因 |
|---|---|
| 任何 alembic `upgrade` | 需 MySQL |
| 真实接口冒烟（诊断/告警/自愈/发布） | 需 api-server + DB + 集群 |
| agent 升级 / pull 模式联调 | 需测试集群 |
| 节点专用工具（describe_node 等） | 需真实节点验证 |
| 大屏 SSE 端到端（≤1s 上屏） | 需后端+DB 起服务 |
| R2 网关拆分部署验证 | 需集群 |

## 三、离线验证命令（都可跑）

```bash
# 前端
cd frontend && npx tsc -b --pretty false && npx eslint src && npm run build

# 后端（不连库的部分）
cd api-server && ./.venv/bin/python -m py_compile app/**/*.py  # 逐文件
cd api-server && ./.venv/bin/python -m unittest discover -s tests -q   # 纯单测（涉库用例会报连接错误，忽略）

# Go
cd k8s-executor && go build ./... && go test ./internal/... 2>&1 | tail -20
```

## 四、建议执行顺序（老板不在时的排法）

1. **P0 骨架**（不依赖任何人，先落地最容易看到效果）
2. **P1 拆分**（文件边界清晰，可并行 3~4 个子代理：WorkloadsList / WorkloadDetail / Pods+Services / Storage+Nodes）
3. **P2 首页**
4. **P3 大屏 SSE 代码**（后端先写 bus + 端点 + 单测；前端换消费逻辑）
5. **R31 会话共享代码**（迁移文件写好，等有环境再升级）

父会话统一收口：`App.tsx`、`MainLayout.tsx`、`services/api.ts`、迁移链，**禁止子代理改这些**。
