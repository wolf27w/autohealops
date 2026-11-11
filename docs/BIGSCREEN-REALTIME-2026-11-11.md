# 大屏实时化设计（2026-11-11 老板要求：功能与信息必须实时，集群内容器状态动态）

> 结论先说：**大屏走 SSE 推送 + 增量快照，不轮询**；集群内 Deployment/Pod 状态用现有 agent 事件 + 定时快照合成；
> 集群详情页（曲线）仍走 query_range，两套数据源不混。

## 一、大屏（/screen）

**通道**：`GET /api/v1/stats/screen/stream`（SSE，`text/event-stream`），一个连接推所有面板。

**帧格式**（每 3 秒一帧，只在有变化时推；前端按 `panel` 局部替换，不做整页重绘）：

```json
{"ts":"2026-11-11T17:10:03+08:00",
 "panels":{
   "kpi":{"unhandled":5,"priority":3,"bad_deploy":2,"heal_rate":93,"ready_rate":97},
   "clusters":[{"id":999025,"cpu":42,"mem":87,"pods":1284,"health":"warn"}],
   "deploy":{"999025":[{"ns":"qa-aero-chain","items":[{"n":"eft-module-hrs","r":2,"want":2}]}]},
   "container_top":{"cpu":[{"pod":"...","ns":"...","pct":92}],"mem":[...]},
   "selfheal":{"ok":18,"restart":7,"scale":4,"failed":2,"rate":93},
   "priority":[{"id":123,"level":"P0","title":"...","age_sec":720}],
   "alerts":[{"id":2917,"level":"P0","title":"...","ts":"..."}],
   "events":[{"ts":"17:09:58","action":"扩容","text":"..."}]
 }}
```

**数据来源与刷新节奏**

| 面板 | 来源 | 节奏 |
|---|---|---|
| KPI（告警/工单/异常数） | 直接查 alerts / tickets（带 Redis 5s 缓存） | 3s |
| 集群 CPU/内存/Pod | `resource_snapshots` 最新一条（已有窗口查询）+ Prometheus 可选 | 15s（采集节奏） |
| Deployment 就绪 | agent 定时上报的 `list_workloads` 快照 | 15s |
| 容器 CPU/内存 Top | Prometheus `container_cpu_usage_seconds_total` / `container_memory_working_set_bytes` | 5s |
| 自愈统计 | repair/executions 表 24h 聚合（Redis 60s 缓存） | 30s |
| 优先处理 | tickets 未关闭按 priority 排序 | 5s |
| 实时告警 | alerts 增量（按 id > last_id） | 事件驱动，立即推 |
| 自愈事件流 | execution/repair 记录增量 | 事件驱动，立即推 |

**事件驱动优先**：告警/自愈这类"发生了就必须马上看到"的，在 `alert` 创建、`execution` 完成处直接 `screen_bus.publish(frame)`，
不等到下一个 3s 心跳；心跳只负责把慢变面板（集群水位、Deployment 快照）刷新。

**断链处理**：SSE 断线前端自动重连（1s/3s/5s 退避），重连后带 `Last-Event-ID` 补发最近 50 条事件；页面隐藏时自动断开（省资源）。

## 二、集群内"容器状态动态"

- 状态来源三层：**agent 15s 快照**（Deployment 就绪/副本）+ **Pod watch 事件**（重启、驱逐、镜像拉取失败，秒级）+ **用户操作后立即刷新**（重启/伸缩/删除成功后前端主动拉一次）
- 前端用 SSE 帧里的 `deploy` 面板局部更新卡片（不是整表重渲染）；异常卡片加 1.2s 高亮闪一下
- 集群全屏视图复用同一份 `deploy` 数据，点击集群只是切换「过滤哪个 cluster_id」，不额外请求

## 三、浏览器资源

- 单页 1 条 SSE 连接，3s 帧 ≈ 每个大屏 ~2KB/帧（~0.7KB/s），比 5s 轮询多个接口省 90% 请求
- 大屏常驻（7×24）：`EventSource` 自带重连；心跳 30s 一次空帧防止中间代理断链
- 超过 3 个大屏同时打开时，服务端按连接数限流（每连接独立帧，但底层聚合查询走 Redis 缓存，不重复打库）

## 四、实现清单（估 2~3 天）

1. `api-server/app/services/screen_bus.py`：进程内订阅表 + Redis pub/sub（多副本可用）
2. `GET /api/v1/stats/screen/stream`：SSE 端点（鉴权走现有 JWT，浏览器 EventSource 用一次性 ticket）
3. 事件挂点：`alert_auto_service`（新建/合并告警）、`execution_service`（自愈动作完成）、`ticket_service`（建单/关单）→ `screen_bus.publish`
4. 慢变面板聚合：每 3s 采样一次（带 Redis 缓存），组装成一个帧
5. 前端大屏改造：`monitor-bigscreen.html` 换成 SSE 消费 + 局部 DOM 更新（保留现有 ECharts 图表）
6. 集群详情页：Deployment 卡片订阅同一个 `deploy` 面板

## 五、验收

- 触发一条测试告警 → 大屏 **≤1s** 出现在实时告警列表并高亮
- 手动重启一个 Pod → 大屏对应 Deployment 卡片 **≤2s** 变化（就绪数/状态色）
- 集群全屏视图打开时，服务端无重复聚合查询（Redis 缓存命中率 >90%）
- 断网 10s 恢复后，事件流补齐缺失事件，不重复、不丢
