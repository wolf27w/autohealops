# 启动失败诊断 Runbook（CrashLoopBackOff / 发布后起不来）

> 固化自 2026-11-25 qa-asfp 事故复盘。当时 11 个 Java 服务滚动发布后全部 CrashLoop，
> 根因不在 k8s 层：镜像入口脚本硬编码 Nacos 出厂默认口令 + 新框架拒绝默认凭据。
> 平台原有工具只看到「CrashLoop / exitCode=1 / 首错行」，挖不到镜像内文件 → 8 轮预算烧完仍给不出根因。

## 一、判定标准（不要用现象当根因）

| 证据 | 含义 | 结论方向 |
|---|---|---|
| `lastState.terminated.exitCode ∈ {137,143}` | 被信号杀（OOM/驱逐/优雅退出） | 资源层：limit/OOMKilled/驱逐 |
| `lastState.terminated.exitCode` 其它非 0 | **应用主动退出** | 应用层：启动逻辑/配置/依赖/框架校验 |
| `lastState.terminated.reason=Error` + 首错行为框架自定义异常 | 应用显式拒绝启动 | 配置/凭据/兼容性 |
| 只有 `Readiness probe failed` | 现象，不是根因 | 继续挖容器内首错行 |

## 二、固定采集顺序（平台工具：`collect_startup_evidence` 一次拿到 1~5）

1. **退出码与重启**：Pod YAML `status.containerStatuses[].lastState.terminated`（最权威，优于 describe 文本）。
2. **首错行**：`Application run failed` / `Caused by` / 第一个 `xxxException: msg`（不是末尾 100 行）。
3. **入口脚本与启动参数**：`/usr/bin/enpoint`、`/entrypoint.sh`、`/proc/1/cmdline`。
   - 重点看 `-D` 参数里的凭据/profile/地址；`-D` 优先级高于环境变量，**脚本写死后 Deployment 配 env 无效**。
4. **镜像内配置**：`jar!/BOOT-INF/classes/application.yml`，看占位符有没有默认值：
   - `${KEY:default}` → 有兜底；`${KEY}` → 必须外部注入，缺失即启动失败。
5. **与上一版本对比**：同两个文件逐行 diff。历史 tag 可能已被仓库清理 → 退化为「集群里还在跑的旧 Pod」。
6. **配置中心账号面**（需要管理凭据）：账号是否存在、是否默认口令、权限是否覆盖目标 namespace。

读取通道优先级：`registry`（直读镜像层，CrashLoop 也能读，需 `REGISTRY_CREDS`）→ `exec`（同 Deployment 有 Running Pod 时可用，零凭据）。

## 三、常见根因模板

- **凭据/配置被收紧**：新框架强校验（非默认口令、必须加载配置中心）而镜像仍硬编码默认值 → 启动即挂。
  修法：镜像入口改读环境变量（无默认值）+ Secret 注入；临时可用回滚止血。
- **必填配置缺失**：`optional:nacos:` 改成 `nacos:` 后，配置中心没对应 profile 的文件 → 必挂。
- **jar/依赖损坏**：`invalid or corrupt jarfile` / `NoClassDefFoundError` → 镜像产物问题。
- **依赖不可达**：Redis/Nacos/DB 连接超时（注意 DNS 解析超时与 sentinel 端口）。

## 四、预算与降级

- 启动失败场景：`max_rounds=12`、`total_timeout=180s`、`collect_startup_evidence` 单工具超时 90s。
- 其它场景：8 步 / 90s，硬上限 16。
- 证据不足时必须结构化交代（① 已排除 ② 缺失证据 ③ 建议补充采集），禁止用探针失败/重启次数当根因。

## 五、已知限制

- `exec_in_pod` 是**单命令、不经 shell**（`sh -c`/管道/重定向被拦），且容器内常常没有 `unzip` → jar 内配置读不到。
  配 `REGISTRY_CREDS`（只读仓库账号）即可走 registry 通道直读，实测 14s 拿到入口脚本 + jar 内 yml。
- 镜像 tag 会被仓库生命周期清掉，历史版本对比可能只能依赖「还在跑的旧 Pod」。
- `exec_in_pod` 权限级别是 `operate`（允许装包），**不在 ReAct 只读工具集里**；证据包工具自行在服务端调用，对 LLM 表现为只读。
