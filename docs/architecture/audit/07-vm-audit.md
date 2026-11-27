# 07 主机 / 虚拟机（VM）管理审计报告（AutoHealOps）

- 审计对象：非 K8s 主机/VM 纳管链路（凭据托管、SSH 采集、Web 终端、会话录制、命令审计、SFTP、agent 代采）
- 审计方式：**只读**源码审计（未修改任何文件、未装依赖、未连任何环境、未做 git 写操作；仅新建本报告）
- 结论口径：所有结论带 `文件:行`；无法从源码确认的写「暂时无法确认」
- 覆盖文件见第 13 节

---

## 1. 结论摘要

1. **SSH 已完整实现，两条独立路径**：①平台直连（Python `asyncssh`，`host_ssh.py:12`），负责采集/终端/SFTP；②agent 集群内代采（Go `golang.org/x/crypto/ssh`，`hostagent.go:20`），只做 `ps` 采集并经隧道上报，**不支持终端与文件传输**（`host_terminal.py:154-156`、`host_terminal.py:523` 明确拒绝 `source=agent` 主机）。没有跳板机、没有 SSH 隧道/端口转发、没有 SOCKS 代理能力。
2. **凭据加密存储已实现但密钥体系薄弱**：AES-256-GCM，`key = settings.ENCRYPTION_KEY.encode()`（`security.py:20-37`），全部租户共用一把静态密钥，无 KMS、无轮换、无按记录派生；**API 只回 `has_secret/has_passphrase` 标志，不回明文**（`host_credential_service.py:19-32`、`hosts.py:44-52`）——这一点做得对。但同一把 key 同时保护 kubeconfig、数据库连接、AI API Key（`config.py:31`），一次泄露 = 全量失守。
3. **主机信息采集只有「进程」一个维度**：直连主机每 60s SSH 跑一次 `ps -eo pid,pcpu,pmem,rss,args`（`host_monitor_service.py:49,163-187`），按「命令行包含配置的路径」判断存活，**无 CPU/内存/磁盘/网络/负载等主机级指标**，无 iostat/df/free。直连路径**不写 `last_metrics`**（全仓仅 agent 路径写，`agent_alert_service.py:324`）→ 直连主机前端「最近指标」永远为空。
4. **功能完成度差异极大**：进程查询 ✅、交互式命令执行 ✅（Web 终端）、文件上传下载 ✅（SFTP 7 个操作）、**服务管理 ❌（无 systemctl）**、**日志查询 ❌（无 journalctl/tail）**、**非交互一次性命令执行 ❌**（只有交互终端）、agent 主机终端/SFTP ❌。
5. **Web 终端录制与回放已实现（双份存储）**：①`host_session_chunks` 表按块 gzip 存 stdin/stdout（`host_session.py:43-54`）；②同时写 asciinema cast v2 文件到 `data/host-recordings/{sid}.cast`（`host_session_record_service.py:43-96`）。回放仅 admin（`host_terminal.py:455-500`、`host_sessions.py:118-125`）。**但录制可由用户用 URL 参数关掉**：`record=0`（`host_terminal.py:165`）。
6. **命令审计不完整，且存在两种确定可用的绕过**：只记录「用户在终端里按回车提交的整行命令」（`host_terminal.py:246-267`），**不记录退出码、不关联输出、不覆盖非交互执行**。两个结构性绕过：①**用方向键回溯历史命令再回车**——`↑` 的 `ESC[A` 被 `LineAssembler` 整段吞掉（`command_guard.py:80-82,127-131`），缓冲区为空，既不审计也不拦截，但 shell 会执行；②**`!!`/`!n` 历史展开**——记录的是字面 `!!`，guard 无法判定，shell 展开后可能是有害命令。
7. **command_guard 是 12 条正则黑名单**（`command_guard.py:18-39`），能挡 `rm -rf /`、`mkfs`、`dd of=/dev/sd*`、fork 炸弹、`DROP DATABASE/TABLE`、`shutdown/reboot` 等；**但挡不住** `rm -rf --no-preserve-root /`、`find / -delete`、`systemctl poweroff`、`echo b > /proc/sysrq-trigger`、换行拆分的命令、以及任何「进入解释器后」执行的命令（vi/python/mysql REPL）。
8. **主机授权模型是「分组 + 三级角色」，但有一条确定的越权链路**：`update_host` 只要求对**当前主机**有 `operate`（组 operator 即可），却允许同时改写 `address`/`ssh_port`/`credential_id`（`hosts.py:212-283`），且 `credential_id` 不做归属校验 → **组 operator 可把任意一个托管凭据（可能是别的团队的 root 私钥）绑到一台自己控制地址的主机上，再用该凭据连任意目标机**。
9. **SFTP 默认白名单包含 `/home`，黑名单只挡 `/root/.ssh`**（`host_terminal.py:511-512`）→ 可在线读取 `/home/*/.ssh/id_rsa`，直接拿到其他账号的 SSH 私钥（进一步横向）。
10. **SSH 不做主机密钥校验**：直连 `known_hosts=None`（`host_ssh.py:64`），agent 侧 `ssh.InsecureIgnoreHostKey()`（`hostagent.go:127`）→ 同网段中间人可截获密码与私钥认证握手（并可用于钓鱼主机）。

---

## 2. SSH 实现方式（问题 1）

### 2.1 连接拓扑

```
┌────────────┐   ①平台直连（asyncssh, 22/TCP）        ┌──────────────┐
│ api-server │ ────────────────────────────────────► │ 主机 / VM     │
│            │ ◄──── 采集 ps / 交互 shell / SFTP ──── │ (被管目标)    │
└────────────┘                                        └──────────────┘
                                                     ▲
┌────────────┐   ②agent 集群内代采（x/crypto/ssh）    │
│ agent(Pod) │ ──────────────────────────────────────┘
│  (被管集群) │   ssh key 来自 Secret 挂载（-ssh-key） │
└─────┬──────┘
      │ ③只上报指标（HostMetrics event 帧），不经隧道下发命令
      ▼
   platform 9095
```

| 路径 | 库 | 用途 | 证据 |
|---|---|---|---|
| 平台直连 | `asyncssh`（纯 Python） | 采集 / Web 终端（pty）/ SFTP | `host_ssh.py:12,93-107`；`host_terminal.py:199-203`；`host_terminal.py:516-527` |
| agent 代采 | `golang.org/x/crypto/ssh` | 仅 `ps` 采集 | `hostagent.go:20,124-153` |
| 跳板机 / ProxyJump / 端口转发 | — | **未实现** | 全仓无 `direct-tcpip`/`ProxyJump`/`socks` 相关代码 |
| 反向隧道（agent 挂 SSH 隧道回平台） | — | **未实现** | agent 只做「出站连平台 9095」 |

### 2.2 认证方式解析顺序

`resolve_credentials()`（`host_ssh.py:59-90`）的口径：

1. 若主机绑定 `credential_id` → 解密凭据库里的私钥/密码，**优先于内联凭据**（`host_ssh.py:65-75`）；
2. 否则：内联加密私钥 `ssh_key_encrypted`（`host_ssh.py:78,81-84`）；
3. 否则：本地文件路径 `ssh_key_path`（`host_ssh.py:85-86`）——**这是服务端文件系统路径**，不是被管机上的路径；
4. 密码 `ssh_password_encrypted` 作为兜底（`host_ssh.py:87-89`）；
5. `known_hosts=None`（`host_ssh.py:64`）→ **完全不做 host key 校验**。

`ssh_key_path` 由用户在 API 里填写（`hosts.py:235`、前端 `HostManagement.tsx:376` 明确写「本地私钥路径」）→ 平台进程可读任意本地文件作为私钥（例如 `/root/.ssh/id_rsa`），这本身是「以 api-server 身份读文件」的能力，属配置面风险（P3）。

### 2.3 连接参数

- 连接超时 8s，`asyncio.wait_for(..., timeout=15)` 包一层（`host_ssh.py:101-107`；`host_terminal.py:199-203`）。
- 终端用 `create_process(term_type="xterm-256color", term_size=(rows,cols), encoding=None, stdin/stdout/stderr=PIPE)`（`host_terminal.py:200-202`）。
- 采集用 `conn.run("ps -eo pid,pcpu,pmem,rss,args")`（`host_monitor_service.py:49`）。

---

## 3. 凭据管理（问题 2）

### 3.1 存储与算法

```python
# api-server/app/core/security.py:20-27
def encrypt_aes256_gcm(plaintext: str) -> str:
    key = settings.ENCRYPTION_KEY.encode("utf-8")
    iv = secrets.token_bytes(12)          # 随机 12B IV
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)
    return base64.b64encode(iv + ciphertext).decode("utf-8")   # iv||ct||tag
```

| 项 | 事实 | 证据 |
|---|---|---|
| 算法 | AES-256-GCM，12B 随机 IV，无 AAD | `security.py:20-37` |
| 密钥来源 | `ENCRYPTION_KEY`（必填环境变量，32 字节字符串） | `config.py:31`（无默认值，缺则启动失败） |
| 密钥作用域 | **一把密钥同时加密** kubeconfig、主机密码/私钥、DB 连接、AI Key | `security.py` 被多处复用（`clusters.py:186`、`hosts.py:193-198`、`host_credential_service.py:61-62`） |
| 轮换 | 无（改 key 等于全部历史密文不可解） | 无相关代码 |
| 存储字段 | `hosts.ssh_password_encrypted / ssh_key_encrypted / ssh_key_passphrase_encrypted`；`host_credentials.secret_encrypted(2000) / passphrase_encrypted(500)` | `models/host.py:24-31`；`models/host_credential.py:23-24` |
| 长度限制 | `secret_encrypted` 是 `String(2000)` → **私钥超过约 1.4KB 明文就会被 MySQL 截断/报错**（RSA 4096 PEM 约 3.2KB） | `models/host_credential.py:23` |

> 最后一条是实际可用性缺陷：`String(2000)` 在 MySQL 下是 `VARCHAR(2000)`，装不下常见 4096 位 RSA 私钥（`Host.ssh_key_encrypted` 用的是 `Text`，但**凭据库走的是 String(2000)**）。是否需要立即处理取决于生产实际使用的密钥长度，属 P2。

### 3.2 是否回显 / 前端可见

| 接口 | 返回内容 | 证据 |
|---|---|---|
| `GET /api/v1/host-credentials` | 只回 `has_secret` / `has_passphrase` | `host_credential_service.py:19-38`；`host_credentials.py:25-28` |
| `GET /api/v1/hosts` | `has_password` / `has_key` / `has_key_passphrase` 布尔 + `ssh_key_path`（服务端路径明文） | `hosts.py:44-52` |
| Web 终端 | 不回显；`use_saved=0` 时由前端一次性发送 | `host_terminal.py:169-176` |
| 审计表 | 不存凭据 | `models/host_command_audit.py` 无凭据字段 |

结论：**没有明文回显**。唯一的信息泄露是 `ssh_key_path`（服务端文件路径）与 `credential_id` 枚举。

### 3.3 临时凭据（不落库）

`ws_host_terminal?use_saved=0` → accept 后在 5s 内等 `{"type":"auth","username","password"}`，直接用这对凭据 `asyncssh.connect(...)`（`host_terminal.py:167-176,193-199`）。**任意用户名/密码都能试**（不对照任何策略），且不落库、不进审计（审计只记命令，不记「用了什么身份登录」）→ 可用于口令喷洒且难追溯（VM-08）。

---

## 4. 主机信息采集（问题 3）

### 4.1 采集项

| 维度 | 是否采集 | 证据 |
|---|---|---|
| 进程存活（按命令行子串匹配配置路径） | ✅ | `host_monitor_service.py:63-89` |
| 每进程 PID 数 / 最大 CPU% / 累计 RSS(MB) | ✅ | `host_monitor_service.py:74-88` |
| 主机级 CPU / 内存 / 磁盘 / 网络 / 负载 | ❌ | 无 `free/vmstat/iostat/df/uptime` 调用 |
| 在线状态 + 失败原因（认证失败/不可达/超时） | ✅ | `host_monitor_service.py:41-57,154-160` |
| 主机名 / OS / 内核 / 启动时间 | ❌ | 无 `uname/hostnamectl` |

### 4.2 采集方式与频率

| 来源 | 方式 | 频率 | 落库 |
|---|---|---|---|
| `source=direct` | api-server 后台 `background_loop` 逐台 SSH 跑 `ps`，**串行**（一台 8s 超时，N 台主机一轮 ≥ N×RTT） | 60s（`CHECK_INTERVAL_SEC=60`，`host_monitor_service.py:22,163-187`） | `hosts.status/last_error/last_seen`（批量一次 commit）；**不写 `last_metrics`** |
| `source=agent` | agent 内 `startHostMetricsPush` 每 60s 用 `x/crypto/ssh` 跑同一 `ps`，经隧道 `event/HostMetrics` 上报 | 60s（`main.go:666-700`，`time.NewTicker(60*time.Second)`） | 平台 `handle_host_metrics` upsert `hosts`（含 `last_metrics`）`agent_alert_service.py:268-331` |

- 状态翻转通知：Redis `host:state:{id}` 记录上次状态，翻转时 `notify_platform`（`host_monitor_service.py:104-123`）。两条路径共用同一函数（`agent_alert_service.py:330`）。
- **agent 主机是「自动建」的**：首次上报即 `Host(source="agent", cluster_id, source_key)` 落库，`name` 冲突时加 `（key）` 后缀（`agent_alert_service.py:299-317`）。

---

## 5. 各项能力判定（问题 4）

| 能力 | 状态 | 证据 | 说明 |
|---|---|---|---|
| 进程查询 | 部分实现 | `host_monitor_service.py:63-89` | 只能查「配置好的若干路径是否在跑」+ PID 数/最大 CPU/累计 RSS；**不能按进程名/端口/用户任意查询**，无 `ps aux` 全量展示入口 |
| 服务管理（启停/重载/开机自启） | 完全未实现 | 全仓无 `systemctl` / `service` 调用（`grep systemctl\|journalctl` 无命中） | 只能靠 Web 终端手敲 |
| 日志查询（系统日志/应用日志） | 完全未实现 | 同上 | 只能靠 Web 终端 `tail`/`less`，或 SFTP 下载文件后本地看 |
| 命令执行 | 已实现（仅交互式） | `host_terminal.py:186-330` | 有 Web 终端（pty）；**没有**「提交一条命令→拿 stdout/exit_code」的 API；`POST /hosts/{id}/check` 只跑固定 `ps`（`hosts.py:316-328`） |
| 文件上传 / 下载 | 已实现（有代码依据） | `host_terminal.py:557-598`（下载，流式）、`601-648`（上传，200MB 上限）、`716-760`（在线编辑 ≤1MB） | 有路径白名单/黑名单；agent 主机不支持 |
| 文件管理（mkdir/delete/read/write） | 已实现 | `host_terminal.py:668-760` | 删除仅支持空目录 |
| 主机重启 / 关机 | 完全未实现 | — | 只能终端里执行（会被 guard 拦，见 §7） |
| 分组 / 标签 / 导入导出 | 已实现 | `host_groups.py`、`host_import_service.py`、`hosts.py:330-362` | CSV/粘贴导入，dry-run 支持 |
| 会话录制 / 回放 | 已实现 | 见 §6 | |

---

## 6. Web 终端：录制、回放与命令审计（问题 5）

### 6.1 会话建立链路

```
浏览器 ws://host/api/v1/ws/hosts/{id}/terminal?token=<JWT>&cols&rows&record=1
  └─ host_terminal.py:143 ws_host_terminal
      ├─ _resolve_user(token)                           # terminal.py:26-36，从 query 取 JWT
      ├─ hgs.host_access(user, host, "operate")          # 151，组角色 ≥ operator
      ├─ source=="agent" → 拒绝直连终端                    # 154-156
      ├─ 单用户并发 ≤5（进程内 ACTIVE 字典）                # 37,158-161
      ├─ recorded = query.record != "0"                  # 165  ← 用户可控
      ├─ use_saved=0 → 等 5s 内 auth 帧                    # 167-176
      ├─ hss.create_session(...)                         # 179
      ├─ SessionRecorder(DB chunks) + CastRecorder(文件)  # 180-181
      ├─ asyncssh.connect / create_process(pty)          # 193-203
      └─ 4 个任务：ws→ssh（含 guard/审计）、ssh→ws、idle、flusher   # 210-290
```

### 6.2 录制

| 存储 | 内容 | 格式 | 证据 |
|---|---|---|---|
| MySQL `host_session_chunks` | `direction(input/output)` + `ts_ms` + gzip(data) | 自定义块序列，前端按 ts_ms 回放 | `models/host_session.py:43-54`；`host_session_service.py:44-120` |
| 本地文件 `data/host-recordings/{sid}.cast` | 同样内容 | asciinema cast v2（首行 JSON 头 + `[t,"o"/"i","text"]`） | `host_session_record_service.py:43-96` |
| `host_sessions` | 元信息（谁/哪台/起止/字节数/close_reason/record_path/record_bytes） | 表 | `models/host_session.py:17-40` |

- **两份数据没有交叉校验**：任一份被删/被改，另一份不会报错；`record_bytes` 只用文件 size 回填（`host_session_record_service.py:163-180`）。
- **无完整性保护**：cast 文件是明文 JSON 行，chunk 是 gzip（可任意重压），**没有 HMAC/哈希链/append-only 约束** → 有服务器文件系统或 DB 写权限者可无痕篡改（VM-04）。
- **保留期**：文件默认 30 天（`RETENTION_DAYS_DEFAULT=30`，`host_session_record_service.py:38,214-248`）；chunk 的清理在 04 报告 §5.2 第 16 项的后台循环里（12h 周期），**具体保留天数本次未逐行核对 → 暂时无法确认**。
- **空会话会被删记录**：`duration_sec<=2 且 bytes_in==0` 时删除 `host_sessions` 行（`host_terminal.py:318-330`）→ 短连接不留痕。

### 6.3 回放 / 下载

- 回放 API 仅 admin：`GET /host-sessions/{id}/replay`（`host_terminal.py:455-500`）、`GET /host-sessions/{id}/cast`（`host_sessions.py:118-125`）。
- 列表仅 admin（`host_sessions.py:69-115`，注意第 79 行 docstring 位置错乱但 `if role!=admin` 仍先生效）。
- 强制下线：`POST /host-sessions/{id}/kick`（仅 admin，`host_terminal.py:474-484`）。

### 6.4 命令审计：记录了哪些

| 维度 | 现状 | 证据 |
|---|---|---|
| 触发时机 | 只在终端输入流里出现 `\r`/`\n` 时，把缓冲区当一条命令 | `command_guard.py:59-109`；`host_terminal.py:246-267` |
| 记录字段 | host_id/host_name/user_id/username/session_id/command/risk/action/reason/client_ip/created_at | `models/host_command_audit.py:20-31` |
| 退出码 | **无字段、无采集** | 同上（无 exit_code 列） |
| 命令输出 | **不关联**（只有录制流里能间接看到） | — |
| 非交互执行（SFTP/采集/临时凭据登录） | **不进命令审计** | 只有 `create_audit_record` 的文件操作摘要（`host_terminal.py:588-595`） |
| 阻断记录 | `action=blocked` + reason（命中规则时） | `host_terminal.py:253-258` |
| 可关闭性 | 审计不可关（代码无开关）；但**录制可关**（`record=0`） | `host_terminal.py:165` |

**两个确定的绕过**（不需要任何特殊权限，普通 operator 就能做）：

1. **历史命令回溯**：按 `↑` 会发送 `ESC [ A`，`LineAssembler._consume_escape` 把 CSI 序列整段吞掉（`command_guard.py:80-82,118-126`）→ 缓冲区仍为空 → 按回车时 `_emit()` 因 `if line:` 不成立而不产生命令（`command_guard.py:144-148`）→ **该命令既不入审计也不经过 guard**，但 shell 会执行历史里的真实命令。
2. **历史展开**：输入 `!!` 或 `!123` 回车 → 记录的命令是字面 `!!`，guard 的 12 条正则都不匹配 → shell 展开为真实（可能高危）命令并执行。

第 1 条同时否定了「命令审计完整」与「危险命令拦截」两个安全声明。

---

## 7. command_guard 真实规则与绕过（问题 6）

### 7.1 规则清单（`command_guard.py:18-39`，共 12 条，`re.search` + `IGNORECASE`）

| # | 风险 | 匹配目标 | 正则（要点） |
|---|---|---|---|
| 1 | high | 删根 | `\brm\s+(-[a-zA-Z]*r…f…\|-…f…r…)\s+(/\*\|~\|/(\s\|;\||\|&|$))` |
| 2 | high | 格式化 | `\bmkfs(\.[a-z0-9_-]+)?\b` |
| 3 | high | 写块设备 | `\bdd\b[^\n]*?\bof\s*=\s*/dev/(sd\|nvme\|vd\|hd)[a-z0-9]*` |
| 4 | high | fork 炸弹 | `:\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;?\s*:` |
| 5 | high | 递归放宽根权限 | `\bchmod\s+-…r…\s+777\s+(/\*\|~\|/(\s\|;…))` |
| 6 | high | 递归改根属主 | `\bchown\s+-…r…\s+\S+\s+(/\*\|~\|/(…))` |
| 7 | high | 重定向写块设备 | `>\s*/dev/(sd\|nvme\|vd\|hd)[a-z0-9]*` |
| 8 | high | 根目录搬空 | `\bmv\s+/\*\s+/dev/null\b` |
| 9 | high | 删库 | `\bdrop\s+database\b` |
| 10 | high | 删表 | `\bdrop\s+table\b` |
| 11 | medium | 关机重启 | `\b(shutdown\|reboot\|halt\|poweroff)\b` |
| 12 | medium | 运行级别 | `\binit\s+[06]\b` |

生效开关：`system_settings.terminal_security.block_dangerous`（默认 `true`，`host_terminal.py:52-58,213`）；admin 可在 `PUT /api/v1/terminal/security` 关掉拦截（关掉后仍审计，`host_terminal.py:259-267`）。

### 7.2 可绕过清单（按可利用度排序）

| 绕过 | 例子 | 为什么有效 |
|---|---|---|
| **历史回溯 / `!!`** | `↑` + Enter；`!!` | 见 §6.4，缓冲区不含真实命令文本 |
| 根路径变体 | `rm -rf --no-preserve-root /` | 规则 1 要求 flags 后紧跟 `/\*`、`~` 或 `/`；`--no-preserve-root` 打断匹配 |
| 其它破坏方式 | `find / -delete`、`truncate -s0 /etc/passwd`、`> /etc/shadow` | 不在规则里 |
| 关机替代 | `systemctl poweroff`、`echo b > /proc/sysrq-trigger`、`kexec -e` | 规则 11 只匹配命令名 `shutdown/reboot/halt/poweroff` |
| 换行拆分 | `rm -rf \` + Enter + `/` | `LineAssembler` 以换行切分，两段都不匹配 |
| 解释器内执行 | `python3 -c 'import os;os.system("rm -rf /")'`；进 `vi` 后 `:!rm -rf /`；`mysql> drop database x` | guard 只看「用户敲的行」，进入 REPL 后的命令不受检；`vi :!` 甚至不在输入流里以行形式出现 |
| 编码/混淆 | `echo cm0gLXJmIC8= \| base64 -d \| sh` | 黑名单不做语义分析 |
| 大小写/空格正常化 | 规则本身 `IGNORECASE`，无此问题 | — |

结论：这是**黑名单式 DLP**，只能挡「手滑」，不能作为安全边界（VM-03）。

---

## 8. 主机授权模型与越权路径（问题 7）

### 8.1 模型

```
User ──(host_group_members.role: admin|operator|viewer)──► HostGroup ──(hosts.group_id)──► Host
平台 admin（users.role='admin'）→ 全量
未分组主机（group_id IS NULL）→ 非 admin 不可见（host_group_service.py:52-58）
```

- 级别映射：`viewer=1 < operator=2 < admin=3`；`view/operate/manage` 分别要求 1/2/3（`host_group_service.py:13-14,40-58`）。
- 管理面（建主机/建凭据/建分组/导入）要求「平台 admin 或**任一组的 admin**」（`hosts.py:22-31` `require_host_admin`）。
- 终端要求对该主机 `operate`；SFTP 同（`host_terminal.py:151`、`host_terminal.py:516-527`）；删除/移组要求 `manage`（`hosts.py:228,308`）。
- 组成员管理要求对该组 `manage`（`host_groups.py:87-90,149`）。

### 8.2 越权路径（已验证的代码逻辑）

| # | 路径 | 前置条件 | 后果 | 等级 |
|---|---|---|---|---|
| 1 | **凭据挪用 + 改地址**：`PUT /hosts/{id}` 同时改 `address`/`ssh_port`/`credential_id` | 对**任意一台**主机有 `operate`（组 operator） | 把别人的托管凭据绑到自己指定的 IP/主机上，用别人的 root 私钥登录任意可达机器 | P1 |
| 2 | **读他人私钥**：SFTP 读 `/home/*/.ssh/id_rsa` | 对该主机 `operate` | 拿到宿主机上其他账号私钥，再横向 | P1 |
| 3 | **组 admin 越界建主机**：`require_host_admin` 允许「任一组 admin」建主机/凭据/导入 | 任一组 admin | 可创建指向任意地址的主机（自己凭据），等于获得「平台到任意内网 IP 的 SSH 通道」 | P2 |
| 4 | **未分组主机** | 平台 admin 才能见 | 不算越权，但 `POST /hosts/clear-ungrouped` 可一键删光（`hosts.py:285-300`），无二次确认 | P2 |
| 5 | **临时凭据喷洒**：`use_saved=0` 任意账号密码 | 对该主机 `operate` | 可爆破目标机本地账号（不在审计里体现身份） | P2 |
| 6 | **SFTP 白名单过宽**：默认 `/tmp,/var/tmp,/home` | `operate` | 读 `/home` 下任意文件（含密钥、配置、业务数据） | P1 |
| 7 | **agent 主机凭据在集群侧**：`source=agent` 主机由集群内私钥采集 | 集群内可读 Secret 者 | 拿到该私钥后可 SSH 所有被代采 VM（绕过平台全部审计） | P1 |

### 8.3 与 K8s 侧授权的一致性

主机侧用的是「分组 + 角色」，K8s 侧用的是「`user_cluster_permissions` + 项目」，两套模型互不相通，**同一用户在两个模块的权限完全独立**；平台没有统一的「资产级 ACL」（P3，架构问题）。

---

## 9. Agent → VM 的连接方式与凭据流转（问题 8）

```
① 部署：agent Pod 挂 Secret（SSH 私钥）→ args: -host-config=/etc/agent/hosts.json -ssh-key=/etc/agent/id_rsa
② 配置：hosts.json（明文 JSON）[{key,name,address,port,user,processes:[{name,path}]}]
③ 采集：每 60s 逐台 ssh.Dial(address:port, PublicKeys(signer), InsecureIgnoreHostKey)
        → session.CombinedOutput("ps -eo pid,pcpu,pmem,rss,args") → 按 path 子串匹配
④ 上报：AgentFrame{type:"event", method:"HostMetrics", payload:{hosts:[...]}}（隧道，mTLS）
⑤ 平台：handle_host_metrics → upsert hosts(source=agent, cluster_id, source_key)
        → 不保存任何 SSH 凭据 → last_metrics 落 60s 快照
⑥ 交互：平台明确拒绝 agent 主机的终端与 SFTP（凭据在对方集群）
```

| 环节 | 事实 | 证据 |
|---|---|---|
| 凭据来源 | 单个私钥文件路径，**所有主机共用一个 key**，无 per-host 凭据、无密码认证 | `hostagent.go:79-103`（`loadSigner` 一次）、`main.go` flag `-ssh-key` |
| host key 校验 | `ssh.InsecureIgnoreHostKey()` | `hostagent.go:127` |
| hosts.json | 明文，含全部主机地址/用户名/监控路径 | `hostagent.go:63-76` |
| 上报通道 | 隧道 event 帧（与告警/指标同一通道，mTLS） | `main.go:666-700` |
| 凭据是否回平台 | 否（只上报 name/address/user/进程指标） | `hostagent.go:49-60`（HostMetricRow 无凭据字段）；`agent_alert_service.py:268-331` |
| 平台能否反向操作这些 VM | 否（无终端/SFTP） | `host_terminal.py:154-156,523` |

评价：这条链路**设计上是合理的**（凭据不出集群），但实现上把「一个私钥打通所有 VM + 不校验 host key + 明文 hosts.json」三件事叠在一起，等于 agent Pod 一旦被读（Secret 可读）就拿到该网段全部 VM 的 root 通道。

---

## 10. 功能状态清单

| 功能 | 状态 | 证据 | 备注 |
|---|---|---|---|
| 主机 CRUD / 分组 / 标签 | 已实现（有代码依据） | `hosts.py:98-315`；`host_groups.py` | 删未分组主机无二次确认 |
| 凭据托管（私钥/密码） | 已实现（有代码依据） | `host_credentials.py`；`host_credential_service.py` | 单静态 key；私钥字段 2000 字符上限 |
| 凭据回显防护 | 已实现（有代码依据） | `host_credential_service.py:19-32` | 只回 has_* |
| 直连 SSH 采集（进程） | 已实现（有代码依据） | `host_monitor_service.py:33-89` | 仅 ps；不写 last_metrics |
| agent 代采（进程） | 已实现（有代码依据） | `hostagent.go`；`agent_alert_service.py:268-331` | 单私钥；不校验 host key |
| 主机级资源指标（CPU/内存/磁盘/网络） | 完全未实现 | 无相关命令 | — |
| 服务管理（systemctl） | 完全未实现 | 无相关代码 | — |
| 日志查询（journalctl/tail） | 完全未实现 | 无相关代码 | — |
| 非交互命令执行（拿 stdout/exit_code） | 完全未实现 | 无相关 API | — |
| Web 交互终端 | 已实现（有代码依据） | `host_terminal.py:143-330` | 单用户 5 并发、10min 空闲超时、踢下线 |
| 会话录制（DB chunks） | 已实现（有代码依据） | `host_session_service.py` | 可被 `record=0` 关闭 |
| 会话录制（cast 文件） | 已实现（有代码依据） | `host_session_record_service.py` | 无双写校验、无篡改检测 |
| 回放（前端） | 已实现（有代码依据） | `frontend/src/pages/HostSessionReplay.tsx`（197 行） | 仅 admin |
| 命令审计（行级） | 部分实现 | `host_terminal.py:246-267`；`host_audits.py` | 无 exit_code、可被历史回溯绕过 |
| 危险命令拦截 | 部分实现 | `command_guard.py:18-56` | 黑名单，多种绕过 |
| 命令审计导出 CSV | 已实现（有代码依据） | `host_audits.py:103-134` | 仅 admin |
| SFTP 列表/下载/上传 | 已实现（有代码依据） | `host_terminal.py:529-648` | 200MB 上限；路径白/黑名单 |
| SFTP 在线编辑/删除/建目录 | 已实现（有代码依据） | `host_terminal.py:668-760` | ≤1MB 编辑 |
| 文件操作审计 | 已实现（有代码依据） | `host_terminal.py:588-595,643-646,650-660` | 记 sha256，但只进 `audit_records` 摘要 |
| agent 主机终端 / SFTP | 完全未实现（明确拒绝） | `host_terminal.py:154-156,523` | 产品缺口 |
| 跳板机 / ProxyJump | 完全未实现 | 无相关代码 | — |
| K8s 容器终端录制/审计 | 完全未实现 | `api/terminal.py` 全文无 record/audit | 对照组，见 06/08 报告 |

---

## 11. 问题清单

```
问题编号：VM-01
问题名称：update_host 允许组 operator 改写地址并绑定任意托管凭据（凭据挪用 + 横向移动）
问题等级：P1
所属模块：主机纳管 / 授权
问题描述：
  PUT /api/v1/hosts/{id} 的权限只校验「对该主机有 operate」（组 operator 即可），但请求体
  允许同时修改 address、ssh_port、credential_id，且 credential_id 只校验「凭据存在」，
  不校验归属/可见性（全平台凭据是扁平命名空间）。于是：把一台自己管的 host 的 address
  改成任意内网 IP，把 credential_id 改成另一个团队托管的 root 私钥，再开 Web 终端/
  SFTP，即可以别人身份登录任意可达机器，且审计记录显示的是「自己操作自己那台主机」。
代码位置：
  api-server/app/api/hosts.py:212-283（update_host）
  api-server/app/api/hosts.py:218-219（仅校验 operate）
  api-server/app/api/hosts.py:235-239（address/port 直接覆盖）
  api-server/app/api/hosts.py:252-259（credential_id 只校验存在）
涉及函数或类：update_host、hgs.host_access、hgs.can_access_group
调用链：
  PUT /hosts/7 {address:"10.0.0.9", credential_id:3}
  → update_host（operate 通过）→ 覆盖 address/credential_id → commit
  → PUT/WS /hosts/7/terminal → host_ssh.load_credential → 解密凭据 3 → SSH 到 10.0.0.9
根本原因：资产、凭据、连接目标三者没有归属约束；凭据选择不做 ACL
可能影响：普通组 operator 可越权使用高权限凭据访问任意内网主机；审计归因错误
修改建议：
  1) 凭据按组/项目划分可见域，绑定时校验「对该凭据有 use 权限」；
  2) 修改 address/ssh_port 需要 manage 权限，并单独审计（记前后值）；
  3) 凭据与主机绑定后，改地址需二次确认或走审批；
  4) 建立 host↔credential 的使用审计（每次连接记录 credential_id）。
验证方法：
  用户 A（组 G operator）把主机地址改为 B 组机器 IP 并绑定 B 组凭据，连接成功即为漏洞
是否需要立即处理：是
```

```
问题编号：VM-02
问题名称：SFTP 默认白名单含 /home，可读取其他账号 SSH 私钥（横向）
问题等级：P1
所属模块：SFTP / 文件访问
问题描述：
  默认 sftp_allow_paths = "/tmp,/var/tmp,/home"，sftp_deny_paths = "/etc,/boot,/dev,
  /proc,/sys,/root/.ssh"。黑名单只挡 root 的 .ssh，/home/*/.ssh 完全可达：operator 可
  直接 sftp_read("/home/deploy/.ssh/id_rsa")（≤1MB 在线读）或 sftp_download 拿走私钥，
  再用该私钥横向到其他机器（且私钥内容进入浏览器/本地，平台无感知）。
代码位置：
  api-server/app/api/host_terminal.py:508-514（默认白/黑名单）
  api-server/app/api/host_terminal.py:716-734（sftp_read，1MB 明文返回）
  api-server/app/api/host_terminal.py:738-760（sftp_write，可覆盖 .ssh/authorized_keys）
涉及函数或类：_sftp_guard、_path_allowed、sftp_read、sftp_write
调用链：GET /hosts/{id}/sftp/read?path=/home/deploy/.ssh/id_rsa → 返回私钥 PEM
根本原因：按「目录」做黑白名单，没有按「敏感文件类型」限制（.ssh/id_*、.aws/credentials、
  .kube/config、*.pem、*.key、/etc/shadow 之外的密钥）
可能影响：单台主机 operator → 拿到主机上多个账号的私钥 → 横向整个环境；可写
  authorized_keys 直接建立持久化后门（sftp_write 只校验路径在白名单内）
修改建议：
  1) 默认黑名单加 `/*/.ssh`、`/*/.aws`、`/*/.kube`、`*.pem`、`*.key`、`*.p12`；
  2) 白名单改为「按主机配置的专用目录」，默认只允许日志/临时目录；
  3) 下载/上传加审批（高危路径）与内容扫描；
  4) 禁止写 `.ssh/authorized_keys`（即使目录在白名单）。
验证方法：sftp_read 读取 /home/<任意用户>/.ssh/id_rsa，能返回即漏洞
是否需要立即处理：是
```

```
问题编号：VM-03
问题名称：危险命令拦截可被历史回溯与 shell 语法绕过，不能作为安全边界
问题等级：P1
所属模块：Web 终端 / command_guard
问题描述：
  guard 只看「用户在 WS 输入流里按回车提交的整行文本」。两条最常用的绕过：
  ①方向键回溯历史命令：ESC[A 被 LineAssembler 整段吞掉，缓冲区为空 → 不审计也不拦截，
  但 shell 执行历史里的真实命令；②`!!` / `!n`：只记录字面量，shell 展开为真实命令。
  另有 rm -rf --no-preserve-root /、find / -delete、systemctl poweroff、
  echo b > /proc/sysrq-trigger、换行拆分（rm -rf \ + 回车 + /）、进 python/vi/mysql
  REPL 后执行等，全部不命中 12 条正则。
代码位置：
  api-server/app/services/command_guard.py:18-39（12 条黑名单）
  api-server/app/services/command_guard.py:80-82,118-131（CSI/SS3 整段吞掉）
  api-server/app/services/command_guard.py:144-148（空缓冲不产生命令）
  api-server/app/api/host_terminal.py:250-262（只有 feed 出的行才 check+审计）
涉及函数或类：LineAssembler.feed、check_command
调用链：↑(ESC[A) → feed 返回 [] → 无 check_command → 无审计 → Enter 直接写 stdin
根本原因：以「终端输入行」为拦截单元，而真实执行语义在远端 shell（还包含历史展开、
  多行、子 shell、解释器），信息必然不完备
可能影响：危险命令拦截形同虚设（可被最普通用户零成本绕过）；审计同样缺失该命令
修改建议：
  1) 关闭 shell 历史回溯能力（把 HOST 侧 shell 换成 `bash --norc` + HISTFILE=/dev/null 或
     禁用 ↑/`!` 展开：`set +H`）；
  2) 拦截层面下沉：对 SSH 会话做「命令通道 + 只允许白名单命令」模式（受限 shell / rbash），
     而不是黑名单；
  3) 把 `!!`、`!`、历史引用列为高危模式（至少审计告警）；
  4) 明确文档口径：黑名单只是「防手滑」，不作为安全控制。
验证方法：终端里先执行 `rm -rf /tmp/x`，再按 ↑ 回车；检查 host_command_audits 是否有记录
是否需要立即处理：是
```

```
问题编号：VM-04
问题名称：会话录像无完整性保护、可被用户关闭、短会话被自动删记录
问题等级：P1
所属模块：会话录制 / 审计
问题描述：
  ①录制开关由 URL 参数决定：`?record=0` → 不建任何 recorder，全程无录像（host_sessions.recorded=false）；
  ②两份存储（DB chunks + cast 文件）是明文/可重压，无 HMAC、无哈希链、无 append-only，
     有文件系统或 DB 权限者可任意改写；
  ③存活 ≤2s 且无输入的空会话会被物理删除（含元信息）；
  ④录像文件 30 天清理，chunk 有独立清理，两者时间轴不一致。
代码位置：
  api-server/app/api/host_terminal.py:165（record 参数）
  api-server/app/api/host_terminal.py:179-181（recorded=False 则不建 recorder）
  api-server/app/services/host_session_record_service.py:43-96（cast 明文写盘）
  api-server/app/services/host_session_record_service.py:163-180（record_bytes 仅取文件大小）
  api-server/app/api/host_terminal.py:318-330（空会话删除）
涉及函数或类：ws_host_terminal、CastRecorder、SessionRecorder
调用链：ws URL 带 record=0 → recorded False → 无录制 → 事后无任何证据
根本原因：录制作「功能」而非「合规控制」，缺少强制与完整性设计
可能影响：员工/攻击者可选择不留痕；证据可被篡改；合规审计（操作留痕）不成立
修改建议：
  1) 录制强制开启（服务端配置项，忽略客户端参数；仅允许 admin 临时关闭并审计该动作）；
  2) 每个 chunk/cast 事件带 HMAC（key 独立于 ENCRYPTION_KEY），收盘写文件哈希到 DB；
  3) 录像存储改对象存储 WORM 或 append-only；
  4) 短会话删除改为「软删 + 保留 7 天」。
验证方法：手动访问带 record=0 的 ws URL，会话列表/回放无数据即漏洞
是否需要立即处理：是
```

```
问题编号：VM-05
问题名称：命令审计缺少退出码与输出关联，无法证明「命令实际结果」
问题等级：P2
所属模块：命令审计
问题描述：
  host_command_audits 只有 command/risk/action/reason，没有 exit_code、没有输出摘要、
  没有「命令与录制时间轴的关联 id」。因此无法回答「这条命令是否执行成功」「实际输出了什么」，
  也无法把审计行与回放定位对齐（只有 session_id）。另外非交互动作（SFTP、采集、临时凭据
  登录）完全不在该表。
代码位置：
  api-server/app/models/host_command_audit.py:17-31（无 exit_code 字段）
  api-server/app/api/host_terminal.py:246-262（只写 command/risk/action/reason）
涉及函数或类：HostCommandAudit、_audit_command
调用链：终端回车 → _audit_command(...) → 无结果信息
根本原因：数据模型按「输入审计」设计，未考虑「执行结果审计」
可能影响：事件复盘只能看录像猜结果；合规检查项「命令执行结果」缺失
修改建议：
  1) 增加 exit_code、output_digest（sha256 前 8 字节）、cast_offset_ms；
  2) 在 shell 侧包一层（如 `PS1` + `trap` 或 `PROMPT_COMMAND` 记录 `$?`）以获取退出码；
  3) 或对交互终端只承诺「操作留痕」，在文档中明确不含结果。
验证方法：执行 `false` 与 `echo ok`，检查审计表是否可区分
是否需要立即处理：否
```

```
问题编号：VM-06
问题名称：SSH 不做主机密钥校验（known_hosts=None / InsecureIgnoreHostKey）
问题等级：P2
所属模块：SSH 连接
问题描述：
  直连链路 asyncssh.connect(..., known_hosts=None)；agent 代采链路
  ssh.InsecureIgnoreHostKey()。两条链路都不验证目标主机身份 → 同网段 ARP/DNS 欺骗即可
  冒充被管主机，接收密码/私钥认证握手，并返回伪造的 ps 结果（掩盖故障）。
代码位置：
  api-server/app/services/host_ssh.py:64（known_hosts=None）
  api-server/app/api/host_terminal.py:197（临时凭据连接同样 known_hosts=None）
  k8s-executor/internal/hostagent/hostagent.go:127
涉及函数或类：resolve_credentials、hostagent.collectOne
调用链：connect(address) → 无 host key 校验 → 中间人可接管
根本原因：为「开箱即用」放弃 host key 校验，且无 TOFU 记录表
可能影响：凭据被中间人截获（密码明文在线路上，私钥认证可被 relay）；监控数据可伪造
修改建议：
  1) 建 host_keys 表做 TOFU（首次记录指纹，之后校验，变更需审批）；
  2) 至少对密码认证强制校验；agent 侧把 host key 指纹一并上报由平台比对；
  3) UI 展示主机指纹与「未校验」状态。
验证方法：用另一台机器占用目标 IP，观察平台是否仍连接成功并采集
是否需要立即处理：否（内网风险，但影响凭据）
```

```
问题编号：VM-07
问题名称：临时凭据通道允许任意用户名/密码尝试，且不记身份、不落库
问题等级：P2
所属模块：Web 终端
问题描述：
  ?use_saved=0 时，WS 接受首帧 {type:"auth",username,password} 并直接用于 SSH 连接，
  不对照任何策略/白名单，不记录「使用了哪个账号」，不落库。可对目标机本地账号做口令喷洒，
  成功后所有命令审计的 username 仍是平台用户名，无法区分「谁以什么系统身份操作」。
代码位置：
  api-server/app/api/host_terminal.py:167-176（等 auth 帧）
  api-server/app/api/host_terminal.py:193-199（直接用临时用户名/密码连接）
  api-server/app/models/host_session.py:17-40（无 login_user 字段）
涉及函数或类：ws_host_terminal、_wait_auth_frame
调用链：ws → auth 帧 → asyncssh.connect(username=..., password=...)
根本原因：为「运维临时登录」放开，未纳入凭据治理
可能影响：绕过凭据托管（凭据不落库、不可审计）；口令爆破；特权账号被共享使用
修改建议：
  1) 临时凭据改为「一次性工单授权」（审批后生成短期凭据）；
  2) 记录 login_username 到 host_sessions 与 command audit；
  3) 对失败次数做限速/告警。
验证方法：用错误密码连 20 次，观察是否有告警/锁定
是否需要立即处理：否
```

```
问题编号：VM-08
问题名称：凭据加密使用单一静态密钥，无 KMS/轮换；私钥字段长度不足
问题等级：P2
所属模块：凭据存储
问题描述：
  所有敏感字段（kubeconfig、主机密码/私钥、DB 连接、AI Key）共用 settings.ENCRYPTION_KEY，
  无 KMS、无按记录派生、无轮换机制；密钥只在 .env 中。另 host_credentials.secret_encrypted
  为 String(2000)，AES-GCM base64 后约 1.33 倍膨胀 → 明文私钥上限约 1.4KB，
  常见 RSA-4096 PEM（约 3.2KB）无法存入凭据库（会截断/报错），只能走 Host 内联字段。
代码位置：
  api-server/app/core/security.py:20-37
  api-server/app/core/config.py:31
  api-server/app/models/host_credential.py:23-24
涉及函数或类：encrypt_aes256_gcm / decrypt_aes256_gcm
调用链：create_credential → encrypt(secret) → INSERT（超长则失败/截断）
根本原因：单密钥体系 + 字段长度按旧用例设定
可能影响：一次密钥泄露即可解全部资产凭据；长私钥无法托管（被迫用内联/文件路径，治理退化）
修改建议：
  1) 引入 KMS/Vault 或 envelope encryption（DEK/KEK），支持轮换；
  2) 字段改 TEXT/MEDIUMTEXT；
  3) 迁移时对历史密文做重加密。
验证方法：托管一把 RSA-4096 私钥，观察是否成功并在连接时可用
是否需要立即处理：否
```

```
问题编号：VM-09
问题名称：agent 代采使用单私钥 + 明文 hosts.json + 不校验 host key
问题等级：P2
所属模块：agent 主机代采
问题描述：
  agent 侧只加载一个 -ssh-key 私钥文件给所有 VM 使用；hosts.json 明文列出全部主机地址/
  用户/监控路径；连接不做 host key 校验。agent Pod 的 Secret/ConfigMap 一旦可读
  （同命名空间 RBAC 过宽、etcd 备份泄露），即获得该网段全部 VM 的 root 通道，
  且完全绕过平台审计（平台只收到指标）。
代码位置：
  k8s-executor/internal/hostagent/hostagent.go:79-103（单 signer）
  k8s-executor/internal/hostagent/hostagent.go:124-131（InsecureIgnoreHostKey）
  k8s-executor/internal/hostagent/hostagent.go:63-76（hosts.json 明文加载）
涉及函数或类：loadSigner、collectOne、LoadHosts
调用链：Pod Secret → id_rsa → ssh.Dial → ps
根本原因：以「简单」优先，未做 per-host 凭据与主机指纹
可能影响：被管集群内提权 → 该网段 VM 全失守；无审计
修改建议：
  1) per-host 凭据引用（凭据留在集群，按 host 选 key）；
  2) host key 指纹校验 + 上报平台比对；
  3) hosts.json 改 Secret 挂载并限制 RBAC；
  4) 代采动作也生成审计事件（每分钟心跳，异常变化告警）。
验证方法：读取 agent Pod 内的 id_rsa，尝试直连任一代采 VM
是否需要立即处理：否
```

```
问题编号：VM-10
问题名称：主机监控串行采集、维度单一，直连主机不落 last_metrics
问题等级：P2
所属模块：主机采集
问题描述：
  background_loop 对 direct 主机逐台串行 SSH（8s 连接超时 + 15s 命令超时），主机数 > 约
  100 台时一轮必然超过 60s，形成堆积；只采 ps 一个维度，无 CPU/内存/磁盘/网络/负载；
  直连路径不写 hosts.last_metrics（仅 agent 路径写），前端「最近指标」对直连主机恒为空。
代码位置：
  api-server/app/services/host_monitor_service.py:163-187（串行 + 60s 周期）
  api-server/app/services/host_monitor_service.py:33-60（只 ps）
  api-server/app/services/agent_alert_service.py:324（唯一写 last_metrics 的地方）
  api-server/app/api/hosts.py:56（读 last_metrics）
涉及函数或类：background_loop、check_host、handle_host_metrics
调用链：每 60s 串行 N 台 → 总耗时随 N 线性增长
根本原因：无并发池、无采集器抽象（direct/agent 两套实现）
可能影响：主机多时监控滞后/堆积；用户看不到资源趋势
修改建议：
  1) 用 asyncio.Semaphore 并发（如 16）+ 单台超时收紧；
  2) 增加 `uptime/free/df -P/iostat -x 1 2` 等指标并落 last_metrics；
  3) 统一 direct/agent 两个采集器的输出结构。
验证方法：造 200 台主机，测一轮耗时是否 >60s
是否需要立即处理：否
```

```
问题编号：VM-11
问题名称：服务管理 / 日志查询 / 非交互命令执行三项能力缺失
问题等级：P2
所属模块：产品能力
问题描述：
  平台没有 systemctl / journalctl / 一次性命令执行（返回 stdout+exit_code）的任何实现，
  所有运维动作只能靠交互式终端手敲（且受黑名单与审计绕过问题影响）。这与「主机运维平台」
  的产品定位有差距，也会把用户推向「用终端裸操作」的路径。
代码位置：
  api-server/app/api/hosts.py（仅 CRUD + check）
  api-server/app/api/host_terminal.py（仅 WS 终端 + SFTP）
  api-server/app/services/host_monitor_service.py（仅 ps）
涉及函数或类：—
调用链：—
根本原因：功能按迭代补齐，服务/日志域未开工
可能影响：用户满意度；重要操作缺乏结构化审计（都在裸终端里）
修改建议：
  1) 增加受控「服务操作」API（systemctl status/restart 白名单 + 审批）；
  2) 增加「日志查询」API（journalctl -u X --since，输出脱敏 + 大小限制）；
  3) 增加「一次性命令」API（白名单动词 + 结构化 stdout/exit_code + 强制审计）。
验证方法：检查路由表是否只有 WS/SFTP/CRUD
是否需要立即处理：否
```

```
问题编号：VM-12
问题名称：JWT 与 token 走 URL query，易被访问日志/历史泄露
问题等级：P2
所属模块：Web 终端认证
问题描述：
  终端 WS 与 SFTP 都用 `?token=<JWT>` 传认证（浏览器 WS 无法带 header 的现实妥协），
  nginx/网关 access log 会记录完整 URL；前端把 JWT 存在 localStorage
  （`hostConsoleApi.ts:51`）。令牌 24h 有效、无刷新、无绑定 IP/UA → 日志泄露即会话劫持。
代码位置：
  frontend/src/services/hostConsoleApi.ts:48-54（拼 URL）
  api-server/app/api/host_terminal.py:144-146（从 query 取 token）
  api-server/app/api/terminal.py:26-36（同样从 query 取）
涉及函数或类：buildHostTerminalWsUrl、_resolve_user
调用链：浏览器 → wss://.../terminal?token=JWT → nginx log 记录 JWT
根本原因：WS 无 header 认证，未采用「短期一次性 ticket」方案
可能影响：日志/截图/代理泄露 JWT → 全平台会话劫持（含 K8s 终端）
修改建议：
  1) 改为「先用 Bearer 换一次性 ws_ticket（30s、单次、绑定 user）」，WS 只带 ticket；
  2) nginx 对 query 中的 token 做脱敏日志；
  3) JWT 缩短到 2h + refresh。
验证方法：查看 nginx access log 是否含 token 明文
是否需要立即处理：否
```

```
问题编号：VM-13
问题名称：审计与录制的读取权限过窄，安全/合规角色无法自查
问题等级：P3
所属模块：审计可见性
问题描述：
  命令审计、会话列表、回放、cast 下载全部只允许平台 admin；组 admin/operator 看不到
  自己组的审计（连自己的操作记录都看不到）。这会导致「组管理员无法自查」，运维只能找
  平台管理员，实践中容易被绕过（例如私下导出 CSV 传播）。
代码位置：
  api-server/app/api/host_audits.py:85-87,113-114
  api-server/app/api/host_sessions.py:78-80,121-122
  api-server/app/api/host_terminal.py:413-414,455-457
涉及函数或类：list_host_command_audits、list_host_sessions、replay_host_session
调用链：—
根本原因：权限口径按「最小可见」设定，未区分「审计查看」与「录像下载」
可能影响：审计数据利用率低；组内合规无法闭环
修改建议：
  1) 组 admin 可看本组审计（只读、不可删除）；
  2) 录像下载保留 admin，回放放宽到组 admin（水印 + 审计查看动作）。
验证方法：以组 admin 调 /host-command-audits，现状 403
是否需要立即处理：否
```

```
问题编号：VM-14
问题名称：删除/清空类操作缺少二次确认与影响面提示
问题等级：P3
所属模块：主机管理
问题描述：
  `POST /hosts/clear-ungrouped` 一次性删除所有未分组主机（`hosts.py:285-300`）；
  `DELETE /hosts/{id}` 直接删除主机（不级联清理 host_sessions/audits，形成孤儿数据）。
  两者都只写一条 audit 摘要，无「先展示将删除的清单」步骤。
代码位置：
  api-server/app/api/hosts.py:285-300
  api-server/app/api/hosts.py:302-315
  api-server/app/models/host_session.py:17-21（host_id 无外键）
涉及函数或类：clear_ungrouped_hosts、delete_host
调用链：—
根本原因：缺少危险操作的确认协议
可能影响：误删主机配置（凭据、分组关系）不可恢复；审计/录像成孤儿
修改建议：
  1) 清空前返回 dry-run 清单并要求 confirm 字段；
  2) 删除改软删（deleted_at），保留 30 天；
  3) 审计查询时过滤已删主机的孤儿记录。
验证方法：调用 clear-ungrouped 检查是否有 dry-run 选项
是否需要立即处理：否
```

---

## 12. 无法确认事项

| 事项 | 卡点 |
|---|---|
| `host_session_chunks` 的实际保留天数 | 清理逻辑在 `api-server/app/main.py` 的后台循环族里（04 报告 §5.2 第 16 项），本次未逐行定位到具体保留参数 |
| 生产是否设置 `sftp_allow_paths` / `sftp_deny_paths` / `terminal_security` 覆盖默认值 | 存储在 `system_settings` 表，未连库 |
| 生产主机数量与采集轮次实际耗时 | 需运行数据，静态分析只能证明「串行 + 固定 60s」 |
| agent 侧 `hosts.json` 与 SSH 私钥的实际挂载方式（Secret 还是 ConfigMap） | install.yaml 模板未包含 host-config/ssh-key 参数（`clusters.py:470-505` 只给平台直连/代采开关），实际部署由运维手工挂载，仓库内无清单 |
| 前端 WebTerminal 是否暴露 `record=0` 给用户 | `hostConsoleApi.ts:48-54` 支持 record 参数，页面调用方式未逐行核对 |
| SqlTerminal（1244 行）是否具备录制/审计 | 属数据库终端链路（08 报告范围），本次仅确认主机终端无 DB 会话录制关联 |
| `ENCRYPTION_KEY` 的生产存放方式（.env / K8s Secret 值） | 只读审计未读取密钥值 |

---

## 13. 审计方法与覆盖范围

**精读（全文）**
- `api-server/app/api/host_terminal.py`（761 行，全文）
- `api-server/app/api/hosts.py`（362 行，全文）
- `api-server/app/api/host_credentials.py`（65 行）、`host_audits.py`（134 行）
- `api-server/app/api/host_groups.py`（187 行，重点段）、`host_sessions.py`（141 行）
- `api-server/app/api/terminal.py`（134 行，对照组）
- `api-server/app/services/host_ssh.py`（124 行，全文）
- `api-server/app/services/host_credential_service.py`（135 行，全文）
- `api-server/app/services/host_group_service.py`（71 行，全文）
- `api-server/app/services/host_monitor_service.py`（187 行，全文）
- `api-server/app/services/command_guard.py`（148 行，全文）
- `api-server/app/services/host_session_service.py`（1-120）、`host_session_record_service.py`（257 行，全文）
- `api-server/app/services/host_import_service.py`（1-120，字段/校验段）
- `api-server/app/models/host.py`、`host_credential.py`、`host_session.py`、`host_command_audit.py`（全文）
- `api-server/app/core/security.py:1-50`、`core/config.py:1-60`
- `k8s-executor/internal/hostagent/hostagent.go`（203 行，全文）
- `k8s-executor/internal/script/runner.go`（119 行，全文，作为「脚本执行」对照）

**扫描式阅读**
- `frontend/src/pages/HostManagement.tsx`（Tab 结构、列定义、表单字段）
- `frontend/src/pages/WebTerminal.tsx`、`HostSessionReplay.tsx`、`SqlTerminal.tsx`（存在性/结构）
- `frontend/src/services/hostConsoleApi.ts`（WS URL 构造、SFTP/会话 API 封装）
- `grep systemctl|journalctl|/var/log` → 无命中（证明服务/日志查询未实现）
- `grep last_metrics` → 仅 agent 路径写（证明直连不写）
- `agent_alert_service.py:268-331`（HostMetrics 落库链路）

**未读 / 不覆盖**
- `SqlTerminal.tsx`（1244 行）与数据库终端链路（属 08 数据库报告）
- K8s 容器终端录制（`api/terminal.py` 已确认无录制，细节归 06/08）
- 前端 SFTP 组件的交互细节（未逐行）

**未执行的操作（合规说明）**：未修改任何业务代码/配置/YAML/依赖，未安装依赖，未连接任何环境，未执行任何 git 写操作；仅新建本报告。
