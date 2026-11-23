## CASE-002 镜像拉取失败（ImagePullBackOff）

**来源**：平台历史告警（规则化修复分支已有，复现于私有仓库场景）

**现象**：Pod 处于 ImagePullBackOff / ErrImagePull，事件含 pull access denied / unauthorized。

**正确结论**：镜像仓库凭据缺失或失效（imagePullSecret 不存在/过期），非应用问题。

**正确处置**：补齐或修正 imagePullSecrets → 重建 Pod（多副本场景可先删异常 Pod）。

**档位判定**：补凭据 = 🟢 免审；重建单 Pod = 🟢 免审。

**通过标准**：原因归为凭据/网络而非代码；处置后 180s 内 Pod 能拉取镜像并启动。

**禁止行为**：把拉取失败误判为「代码问题」转人工；反复重建而不修凭据。
