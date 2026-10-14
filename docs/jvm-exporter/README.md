# R2.3 · JVM 堆 / G1 指标接入（JMX Exporter）

平台「JVM 指标（堆/GC）」依赖集群 Prometheus 里的 `jvm_*` 指标。当前集群**没有**这些指标，需为 Java 应用接入 **JMX Exporter**。

## 一次性准备
1. 取 `jmx_prometheus_javaagent.jar`（约 300KB，放进应用镜像或挂 ConfigMap/EmptyDir）。
2. 配置（见 `jmx-exporter-config.yaml`）挂到容器（例：`/opt/jmx/config.yaml`）。
3. 给应用容器加 **JAVA_TOOL_OPTIONS**：
   ```
   JAVA_TOOL_OPTIONS=-javaagent:/opt/jmx/jmx_prometheus_javaagent.jar=9404:/opt/jmx/config.yaml
   ```
   （Spring Boot 也可用 `-Dloader.path` 或启动脚本 `-javaagent`；端口 9404 暴露 `/metrics`。）
4. 让 Prometheus 采集：pod 加注解
   ```
   prometheus.io/scrape: "true"
   prometheus.io/port: "9404"
   prometheus.io/path: "/metrics"
   ```
   或用集群既有的 ServiceMonitor/PodMonitor 指向 9404。
5. 容器安全上下文需允许 `javaagent`（非受限 PSP）；端口无需对外，仅集群内 Prometheus 可达即可。

> 注意：这是**应用侧/集群侧前置**，需应用负责人或运维执行；平台不自动改你的工作负载。
> 可先在一个测试应用上灰度，确认 `jvm_*` 出现后再推广。

## 验证
- Prometheus 查 `jvm_memory_used_bytes{area="heap"}` 有数据。
- 平台 → JVM 诊断弹窗 →「读取 JVM 指标（堆/GC）」→ 显示堆已用/上限/使用率、GC 耗时/次数、线程、已加载类。
- （可选）基于 `jvm_*` 配 JVM 告警规则（堆使用率 > 85%、Full GC 频繁等）。

## 平台已就绪
`GET /api/v1/jvm/clusters/{id}/jvm-metrics?namespace=&pod=`（堆/GC/线程/类），前端 JVM 诊断弹窗已接。
