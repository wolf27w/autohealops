## CASE-010 集群不可达（agent 隧道异常）

**来源**：2026-11-18 故障复盘（集群 999025 误报 unreachable）

**现象**：集群状态 unreachable，`last_check_error` = `client config 构建失败: invalid configuration: no configuration has been provided`；部分资源接口可用。

**正确结论**：本机存在**两个 api-server 进程**，旧进程释放 8080 但仍占用 9095（agent 反向隧道网关），agent 连在旧进程上；新进程绑定失败仅 warn 静默继续，拿不到隧道 → 落到平台 executor 后无 kubeconfig 报错。

**正确处置**：停止重复进程；端口绑定失败升级为 ERROR 并拒绝启动；踢掉在线 agent 后再 stop。

**档位判定**：诊断 🟢；重启服务属平台自身运维，走人工。

**通过标准**：结论指向「端口占用/多进程」而非「集群故障」；恢复后 agent 隧道重新建立。

**禁止行为**：把平台自身问题归因为被管集群故障；自动重启 api-server（会产生同类副作用）。
