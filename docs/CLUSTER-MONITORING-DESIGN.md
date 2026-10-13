# 集群监控（Prometheus 数据源）—— 设计定稿

> 决策：老板 2026-10-13；状态：**已定稿，暂未排期（老板「现在先不做」）**。
> 关联：放弃 Netdata 方案（资源开销大 + 事故，代码已回滚）。

## 一、结论
- **不再部署 Netdata**，改用**各集群自带的 Prometheus**（复用，零新增资源），对标 KubeDoor。
- 数据源策略 = **混合**：默认自动发现集群 Prometheus（cluster-monitoring 命名空间）+ 后台可配地址覆盖。
- 呈现位置 = **现有仪表盘**（`/dashboard`），顶部 **Tab 切换：「告警 | 监控」**。
  - **告警 Tab** = 现有仪表盘内容**原样保留**。
  - **监控 Tab** = 新增，聚合 → 集群 → namespace → Pod → 容器 下钻。

## 二、监控 Tab 布局
```
┌ 顶部：[集群选择器：全部集群 ▾]  [时间范围：最近 1h ▾]  [刷新 30s]
├─ 全部集群（聚合）视图：
│    集群卡片 ×N：名称 · 状态 · 节点(就绪/总) · 工作负载数   ← 节点维度，避开告警 Tab 的簇级 CPU/内存水位
│    （点卡片 → 选中该集群）
└─ 选中某集群后：
     ┌ 节点表：节点 · CPU% · 内存% · 磁盘% · 负载   （点节点 → 曲线）
     └ [Namespace 选择器 ▾] → Pod 表：Pod · CPU · 内存 · 重启
            └ 点 Pod → 抽屉：容器 CPU/内存/网络 曲线（range 趋势）
```

**避重**：告警 Tab 已有「集群资源水位(CPU/内存/Pod/负载均衡)」，监控 Tab 聚合**不重复**，改走节点维度。

## 三、数据源映射
| 层级 | 数据源 |
|---|---|
| 聚合（全部集群） | 平台统计（`/stats/dashboard`，已有） |
| 单集群 / ns / pod / 容器 | 该集群自带 Prometheus |

## 四、指标清单
- **节点**：CPU 使用率、内存使用率、磁盘使用率、网络吞吐、load（node-exporter）
- **工作负载**：副本数/可用数、CPU/内存（kube-state-metrics）
- **Pod**：CPU/内存 Top、重启 Top、状态分布（cadvisor + kube-state-metrics）
- **容器**：单 Pod 的 CPU/内存/网络曲线（cadvisor）

## 五、技术方案
- **即时查询**：已实现 —— `prometheus_service.py` + `/api/v1/monitoring` 接口（复用 executor `QueryPrometheus`，走 apiserver Service Proxy，零 proto 改动）。实测 cluster1 node_cpu 返回 45 节点真实值。
- **趋势（range）**：**待做** —— executor 的 `QueryPrometheus` 仅即时查询。方案：给 executor 补 HTTP 端点 `GET /prom/{cluster_id}/query_range?query=&start=&end=&step=`（绕 proto、不装工具，同 Netdata 反代套路）。
- **图表**：复用 `@ant-design/charts`（折线/饼/柱），不引新依赖。

## 六、已实现（本仓）
- `api-server/app/services/prometheus_service.py`（即时查询 + 预置指标）
- `api-server/app/api/monitoring.py`（`/api/v1/monitoring/metrics`、`/clusters/{id}/metrics/{metric}`、`/clusters/{id}/query`）

## 七、待做
1. executor range 端点（趋势曲线）
2. 前端：仪表盘改 Tab（告警 | 监控）+ 监控 Tab 下钻页
3. 后台「监控数据源」配置（自定义 Prometheus 地址覆盖自动发现）
