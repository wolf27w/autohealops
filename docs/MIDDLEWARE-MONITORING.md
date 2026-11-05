# 中间件监控扩展（P3 预留设计）

## 现状

平台容器资源监控已上线（Prometheus 数据源，executor Service Proxy 代理查询）。
集群 Prometheus 当前只有 **node_exporter + cAdvisor** 指标，**没有中间件 exporter**。

## 扩展机制（已就绪，无需改代码）

| 层 | 说明 |
|---|---|
| **查询通道** | executor `QueryPrometheus` 接受任意 PromQL，通用 |
| **规则表** | `resource_alert_rules.metric_type` 字符串自由扩展 |
| **模板库** | `api-server/app/services/resource_alert_service.py` 的 `PROMQL_TEMPLATES` 字典，加中间件指标只需加一项 |
| **前端** | 告警策略 → 容器资源阈值 → 指标下拉已含中间件分组 |

## 已预置的中间件指标模板

| metric_type | 含义 | 依赖 exporter |
|---|---|---|
| `redis_connections` | Redis 连接数 | redis_exporter |
| `redis_memory_pct` | Redis 内存使用率 % | redis_exporter |
| `mysql_connections` | MySQL 连接数 | mysqld_exporter |
| `mysql_slow_queries` | MySQL 慢查询速率 /s | mysqld_exporter |
| `rocketmq_backlog` | 消费者积压 | rocketmq-exporter |
| `rocketmq_get_count` | 消费速率 /s | rocketmq-exporter |
| `mongodb_connections` | MongoDB 连接数 | mongodb_exporter |
| `elasticsearch_status` | ES 健康（0绿/1黄/2红） | elasticsearch_exporter |
| `rabbitmq_connections` | RabbitMQ 连接数 | rabbitmq_exporter |

> 阈值语义：百分比类填 %，数值类（连接数/积压）填绝对数值，`value >= 阈值` 即告警。

## 集群侧待办（需部署 exporter）

在 K8s 集群部署 exporter（Prometheus Operator 的 ServiceMonitor 模式，业界同类产品 会自动发现）：

```bash
# 以 redis_exporter 为例（其余类似）
kubectl create deployment redis-exporter --image=oliver006/redis_exporter:latest \
  --namespace=ops -- /redis_exporter --redis.addr=redis://redis-0.redis:6379
kubectl expose deployment redis-exporter --namespace=ops --port=9121

# ServiceMonitor 让 Prometheus 抓取（业界同类产品 环境）：
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: redis-exporter
  namespace: ops
spec:
  selector: { matchLabels: { app: redis-exporter } }
  endpoints: [{ port: http, interval: 30s }]
```

对应中间件连接信息（Redis/MySQL/RocketMQ 等地址、密码）按各自 exporter 文档配置。

## 扩展新中间件的步骤（10 分钟）

1. 集群部署 exporter + ServiceMonitor（Prometheus 能查到指标）
2. `PROMQL_TEMPLATES` 加一项：`"kafka_lag": "kafka_consumergroup_lag"`（示例）
3. 前端 `METRIC_OPTIONS` 加对应下拉项
4. 告警策略页建规则（阈值/持续时长/namespace 圈定）

无需改扫描器、告警链路、通知路由——全部复用。
