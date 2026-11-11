# 并行开发提示词指南（AutoHealOps 实战版）

> 老板 2026-11-11 问：开发时怎么写提示词，能并行给 4 个代理。
> 结论：**你只需要说清楚「做什么 + 边界 + 验收」，拆分和发单由我做。**

## 一、你（老板）怎么说 —— 三种粒度

### 最省事（推荐）
```
P1 集群内重构，并行拆这 4 块：工作负载列表 / 工作负载详情 / 容器组+服务 / 存储+配置+节点。
范围只要 frontend/src/pages/cluster/ 下新文件，别动 App.tsx 和 api.ts。不装依赖。
验收：tsc 通过 + 每块页面能从 /clusters/2/... 路由打开。
```
一句话就够：**目标 + 拆几块 + 文件边界 + 约束 + 验收**。

### 只要结果，让我拆
```
按 R32-P1 开工，能并行就并行，先出拆解表给我看。
```
我会先给「任务 / 文件白名单 / 模型 / 依赖」表，你点头再开跑。

### 高风险改动（要能一键丢弃）
```
用 worktree 隔离做 P1，验证过我再看。
```
每个子代理独立 checkout + 分支，不污染主干。

## 二、提示词的 6 个要素（缺一个就会跑偏）

| 要素 | 说明 | 例子 |
|---|---|---|
| ① 项目 | 仓库路径 | `/Users/wolf/Documents/project/autohealops` |
| ② 目标 | 要什么结果（不说怎么做） | 「集群内要有 KubeSphere 式左导航」 |
| ③ 范围 | 允许改的**文件/目录** | `frontend/src/pages/cluster/**`（新文件） |
| ④ 参照 | demo / 截图 / 现有页面 | `docs/mockups/platform-demo.html` 的集群视图 |
| ⑤ 约束 | 不装依赖 / 不改后端 / 保持兼容 | 「不装依赖；不改 App.tsx / api.ts」 |
| ⑥ 验收 | 怎么算完成 | 「`npx tsc -b` 通过；4 条路由能打开」 |

## 三、我这边怎么拆（你不需要关心，但知道有好处）

**铁律：按文件边界拆，不按功能边界拆。**
两个人改同一个文件 = 必然互相覆盖，而且不报错。

**公共接线文件一律我自己改，子代理不许碰**：
`App.tsx`、`MainLayout.tsx`、`services/api.ts`、`api-server/app/main.py`、`models/__init__.py`、迁移链、`package.json`。

**子代理 brief 必含 5 条**：目标（确切文件/组件）/ 落点（文件白名单）/ 禁止（公共文件+不装依赖）/ 约束（技术栈、现有约定）/ 验证（跑哪条命令）。
另加一句「**先落地最小实现，不要长时间探索**」——子代理最常见的失败是探索到超时。

## 四、P1 实拆示例（4 个代理，互不冲突）

| # | 任务 | 文件白名单（只写这几个） | 依赖 |
|---|---|---|---|
| A | 工作负载列表（部署/有状态/守护/定时，类型由下拉切换） | `pages/cluster/WorkloadsList.tsx` | 无 |
| B | 工作负载详情整页（概览/资源状态/元数据/环境变量/事件/**监控**） | `pages/cluster/WorkloadDetail.tsx`、`components/cluster/MonitorPanel.tsx` | 无 |
| C | 容器组列表 + 服务/路由列表（含日志/进容器/JVM/隔离/YAML/删除） | `pages/cluster/PodsList.tsx`、`pages/cluster/ServicesList.tsx` | 无 |
| D | 存储 + 配置 + 节点（PVC/StorageClass、ConfigMap/Secret、节点列表） | `pages/cluster/StorageConfig.tsx`、`pages/cluster/NodesPage.tsx` | 无 |
| 我 | ClusterLayout + 路由（`/clusters/:id/*`）+ api.ts 新方法 | `pages/cluster/ClusterLayout.tsx`、`App.tsx`、`services/api.ts` | 收口 |

四个子代理之间**没有共用文件**，可以同时跑；我最后串起来联调 + 分笔提交（**按路径 add，禁止 `git add -A`**，否则会吞掉别人的半成品）。

## 五、并发能力（当前配置）

- `agents.defaults.maxConcurrent = 6`，`subagents.maxConcurrent = 8`，`maxChildrenPerAgent = 8`，`maxSpawnDepth = 2`
- 实际可跑：**我 1 个 + 子代理 5~7 个**；再多受上游模型限流约束
- 开发类给 `deepseek-v4-pro`，跑腿/查证类给 `deepseek-v4-flash`

## 六、离线期特别提醒（无集群/无库）

子代理 brief 里的验证必须写**离线可跑的**：
`npx tsc -b --pretty false`、`npx eslint src`、`npm run build`、`python -m py_compile`、`go build ./...`、纯函数单测。
**不要写「调接口验证」「起服务看页面」**——没环境会卡住。

## 七、你验收时看什么

1. 我给的**拆解表**（任务/file 白名单/依赖）
2. 每个子代理的**改动文件清单** + 真实命令输出（不看自我汇报）
3. 我收口后的**整体构建结果**（tsc/build/py_compile）
4. `git log` 上**每块一个 commit**（按路径分笔提交，方便单独回滚）
