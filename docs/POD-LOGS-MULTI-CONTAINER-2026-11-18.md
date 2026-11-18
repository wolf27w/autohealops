# Pod 实时日志页：多容器 Pod 取不到日志（502）

> 登记时间：2026-11-18 11:02（老板反馈）
> 状态：**已修复**（后端执行器自动选容器 + 前端默认容器），需求口径登记如下

## 1. 现象

打开「Pod → 实时日志」，弹出：

```
502 取日志失败: a container name must be specified for pod efreight-cloud-ui-5d7496b8cf-7gzqt,
choose one of: [efreight-cloud-ui filebeat]
```

其它 Pod 一样。原因：这些 Pod 都是 **应用容器 + filebeat sidecar** 的双容器，前端默认「容器（默认）」= 空，
执行器把空容器名透传给 apiserver → apiserver 直接拒绝 → 执行器返回 502。

## 2. 需求口径（修复后行为）

### 2.1 默认容器选择（后端统一兜底，所有入口一致）

容器名为空时按以下顺序自动选：

1. Pod 注解 `kubectl.kubernetes.io/default-container`（若有）
2. **第一个非 sidecar 容器**（按名字跳过：`filebeat` / `fluent-bit` / `fluentd` / `logstash` / `istio-proxy` / `envoy` / `linkerd-proxy` / `sidecar` / `logtail` / `vector` / `prometheus` / `node-exporter`）
3. 兜底第一个容器

适用范围（同一套规则）：
- 实时日志（WS → 执行器 `logstream` HTTP `/logs`）
- 日志查看接口（gRPC `GetPodLogs`，原有 `pickDefaultContainer`）
- 日志下载（`/logs/download`）

### 2.2 前端交互

- 打开弹窗加载容器列表后，**默认选中**上面规则算出的容器（不再留空），下拉仍可切 sidecar。
- 多容器时给提示：当前容器名 + 「共 N 个容器」，便于知道看的是哪个。

### 2.3 错误提示（保留，作为兜底）

- `a container name must be specified` → 「该 Pod 有多个容器，请在“容器”下拉里选择一个」
- `not found` / `could not find the requested resource` → 「Pod 不存在（可能已重建/删除），请刷新列表」
- `previous=true` 但无上次实例 → 「该容器没有上一次运行实例（未重启过）」

## 3. 代码落点

| 层 | 文件 | 改动 |
|---|---|---|
| 执行器 | `k8s-executor/internal/logstream/server.go` | 新增 `pickLogContainer`（注解 → 非 sidecar → 第一个），`handleLogs` 容器为空时调用；`default-container` 注解优先 |
| 前端 | `frontend/src/components/PodLogStreamModal.tsx` | 容器列表加载后默认选非 sidecar 容器（`pickDefaultContainer`） |
| api-server | `api-server/app/api/logs.py`、`api-server/app/api/resources.py` | 未改（错误映射已存在，容器兜底在执行器侧做） |

## 4. 验证

- 执行器直连：`GET :9099/logs?cluster_id=3&namespace=prod-eft&pod=efreight-cloud-ui-849d496d6f-7kgvl&tail=5&follow=false` → **200，1779 字节**（应用容器 nginx 日志），未传 container。
- 指定 sidecar：`&container=filebeat` → 200，2533 字节。
- api-server 日志接口：`/resources/clusters/3/namespaces/prod-eft/pods/<pod>/logs?tail=2` → 200 带日志。
- 前端：`tsc -b` 0 错、`npm run build` 通过。

## 5. 待办（本轮未做）

1. 实时日志页增加「容器名」列显示 + 「全部容器」合并视图（同时跟多个容器，按前缀区分）。
2. 日志高亮规则可配（现在只有 ERROR/WARNING/INFO 固定色）。
3. 日志下载支持「按时间段」而不是只 tail 行数。

## 6. 追加需求：聚合日志必须有关键字过滤（2026-11-18 11:09，已修复）

老板反馈「聚合日志中没有关键字过滤功能」。两处聚合日志 UI 都补上：

| 位置 | 组件 | 新增能力 |
|---|---|---|
| 工作负载详情 → 聚合日志 | `frontend/src/components/cluster/WorkloadLogsModal.tsx` | 关键字输入（空格分隔=同时包含，不区分大小写）、只看错误/警告开关、标题显示「匹配/总行数」 |
| 服务详情 → 聚合日志 | `frontend/src/pages/ServiceDetail.tsx` | 同上（按 Pod 分组内过滤，每组显示命中行数） |

口径：
1. 过滤只影响展示与「下载」，不改变已收到的日志缓冲（切换关键字立即生效，不需要重连）。
2. 关键字大小写不敏感；多个关键字用空格分隔表示 **AND**。
3. 「只看错误」匹配 `ERROR|ERR|FATAL|WARN|WARNING|Exception|panic`。
4. 下载（gzip）导出的是 **当前筛选结果**，文件名带 `_filtered` 便于区分。
