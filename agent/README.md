# agent/ —— Agent 相关物料集中目录

被管集群里跑的 `autohealops-agent` 的构建物料、配置样例、说明。

```
agent/
├── Dockerfile              # 镜像构建（静态二进制 + debian slim 底包）
├── Makefile                # 交叉编译 / 打镜像 / 推送
├── README.md               # 本文件
└── examples/
    ├── install.yaml.example   # 一键安装清单样例（平台会按集群动态渲染，此文件仅参考）
    └── agent.env.example      # agent 启动参数 / 环境变量说明
```

## 源码在哪

Agent 的 Go 源码在 **`k8s-executor/cmd/agent`**（入口）以及它依赖的共享包：

| 路径 | 作用 |
|---|---|
| `k8s-executor/cmd/agent/main.go` | 入口：连平台网关、隧道收发、采集上报、告警检测 |
| `k8s-executor/internal/agentbridge/` | 隧道 ↔ 进程内 executor 的桥（全方法转发） |
| `k8s-executor/internal/agentdb/` | 经 agent 查数据库 / 数据库健康采集 |
| `k8s-executor/internal/eventstore/` | k8s 事件历史采集 |
| `k8s-executor/internal/metrics/`、`quota/` | Pod 指标 / 配额快照采集（与平台侧共用同一份逻辑） |
| `k8s-executor/internal/watcher/` | Pod/Node 告警检测（agent 内运行） |

**为什么源码不放在本目录**：agent 与 executor 同属一个 Go module（`k8s-executor/go.mod`），
且共用 `internal/` 下的包。Go 的 `internal` 规则要求调用方必须在同一 module 的父目录树内，
把 agent 源码物理移到 `agent/`（module 外）会直接编译失败。
因此这里只放「构建与配置物料」，源码仍在模块内 —— 这是 Go 工程的标准做法。

> 如果确实要求源码也物理搬过来，需要把整个仓库改成「单 module 根在仓库根」
> 或把共享包从 `internal/` 提升到 `pkg/` 并拆多 module，属于较大重构（约半天，改动面大）。

## 生产镜像怎么出

**当前 Mac 上没跑 docker**，所以生产镜像走「本机交叉编译 → 远端 docker 构建 → 推送」：

```bash
make -C agent remote-image VERSION=0.2.7
# 默认远端 root@192.168.6.149，可覆盖：
# make -C agent remote-image VERSION=0.2.7 REMOTE=root@1.2.3.4 REMOTE_DIR=/tmp/agent-build
```

这条命令依次做：
1. `CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build`（产物 `agent/build/agent`，静态二进制）
2. `scp` 二进制 + `Dockerfile` 到远端 `/tmp/agent-build/`
3. 远端 `docker build` → `tag` → `push`（`:VERSION` 和 `:latest` 两个 tag）

等价的手工流程（脚本没装/想一步步看时）：

```bash
make -C agent build VERSION=0.2.7
ssh root@192.168.6.149 'mkdir -p /tmp/agent-build'
scp agent/build/agent agent/Dockerfile root@192.168.6.149:/tmp/agent-build/
ssh root@192.168.6.149 'cd /tmp/agent-build && docker build -f Dockerfile \
  -t harbor.efreight.cn/ops/autohealops-agent:0.2.7 . \
  && docker tag harbor.efreight.cn/ops/autohealops-agent:0.2.7 harbor.efreight.cn/ops/autohealops-agent:latest \
  && docker push harbor.efreight.cn/ops/autohealops-agent:0.2.7 \
  && docker push harbor.efreight.cn/ops/autohealops-agent:latest'
```

**本机有 docker 时**（换到装了 docker 的机器）：

```bash
make -C agent image VERSION=0.2.7   # 打镜像
make -C agent push  VERSION=0.2.7   # 推送（自动带 latest）
```

> 平台自身的三个服务（api-server / ai-service / k8s-executor / frontend）用
> `./scripts/release_platform.sh --version <tag>` 构建+推送+滚动发布，同样需要 docker 环境；
> 在没有 docker 的机器上请到构建机执行。

## 配置

Agent 的配置全部通过**启动参数**传入（没有配置文件），由平台在生成 `install.yaml` 时写入：

| 参数 | 说明 |
|---|---|
| `-platform` | 平台 gateway 地址 `host:port`（mTLS） |
| `-cluster-id` | 平台里的集群 ID（注册用） |
| `-token` | 接入令牌（平台签发，可吊销） |
| `-watch` | 是否在 agent 内跑告警检测（默认 true） |
| `-ca-file` / `-cert-file` / `-key-file` | mTLS 证书（平台现场签发，挂 Secret） |
| `-whitelist` | 命令白名单 |
| `-server-name` | TLS SNI（可选） |
| `-version` | 打印版本后退出 |

详见 `examples/agent.env.example`。
