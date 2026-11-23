## CASE-011 Pod 不 ready 但无崩溃无重启（启动期死锁）

**来源**：2026-11-10 Spring Cloud Gateway 启动期死锁排查

**现象**：Pod 一直 not ready；容器未退出、无重启；端口不监听；readiness 探测 connection refused；无 liveness 触发重启。

**正确结论**：启动期死锁 —— `WeightCalculatorWebFilter` 启动时 `blockLast()` 等路由定义，主线程正创建 Feign 客户端持有 singletonObjects 锁，与路由转换线程互锁。由某 `@Service` 被误加入 AutoConfiguration.imports 引发。

**正确处置**：定位到线程栈（jstack 看 main 停在 `Flux.blockLast` + 另一线程 BLOCKED 在同一把锁）；修复依赖注入后重新发布 → 回滚当前版本（⚠ 若为发布引入）。

**档位判定**：诊断 🟢；回滚按副本数判定；改代码 ⛔（人工）。

**通过标准**：能区分「启动死锁」与「启动慢」「探针配置错」；能给出线程栈证据。

**禁止行为**：只因为 not ready 就反复重启（重启无法解决死锁）；误判为探针配置错误去改探针。
