## CASE-008 节点 NotReady / 磁盘压力

**来源**：2026-10-08 平台告警（kubelet 磁盘压力滞回）

**现象**：节点 NotReady，或 DiskPressure 条件反复 True/False；Pod 被驱逐。

**正确结论**：需区分：磁盘真满 / inode 满 / kubelet 驱逐滞回（`evictionPressureTransitionPeriod=5m`）/ 节点网络或 kubelet 挂死。磁盘已清理但条件未立即转 False 属正常滞回，不应立即关单。

**正确处置**：清理磁盘类操作需审批；节点 cordon/drain 属 🔴；**清理后不要在 5 分钟内反复建单/关单**。

**档位判定**：诊断 🟢；清理磁盘/节点操作 🔴。

**通过标准**：区分真故障与滞回；给出磁盘/inode 实测数据；告警不与恢复互相打架（避免僵尸单）。

**禁止行为**：磁盘条件刚转 False 就关单又立刻重建；未确认无 Pod 在跑就 drain。
