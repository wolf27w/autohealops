# 节点故障处置手册（Node Runbook）

> 平台能力边界：**节点诊断是只读的（P1）**；**隔离恢复 cordon+drain 走审批（P2）**；
> **节点内的一切变更（清磁盘、删镜像、重启 kubelet、改内核参数）平台不自动执行**，
> 由值守人员按本手册操作（P3，人工执行）。
>
> 适用：`NodeNotReady` / `MemoryPressure` / `DiskPressure` / `PIDPressure` /
> `NetworkUnavailable` / `NodeCordoned` 六类节点告警。

---

## 0. 铁律（先看这段）

1. **先隔离、再动手**：节点上还有业务 Pod 时，任何清理/重启都可能把故障放大。先 `cordon`+`drain`。
2. **一次只动一个节点**，确认集群剩余容量足够（`kubectl describe node` 看可分配量，或平台「节点管理」页看水位）。
3. **禁止**在节点上直接 `rm -rf /var/lib/kubelet`、`rm -rf /var/lib/docker`、`iptables -F`、改 `/etc/fstab` 后不 `mount -a` 验证。
4. 所有变更**留痕**：在工单里记录命令、时间、结果（平台审计链路可查工单）。
5. 节点压力条件有滞回（kubelet `evictionPressureTransitionPeriod` 默认 5 分钟），**清理完不要立刻 uncordon**，等条件转 False。

---

## 1. 平台会自动做什么

| 动作 | 触发 | 说明 |
|---|---|---|
| 节点诊断（只读） | 收到节点类告警 | 采集 conditions / capacity / taints / Node 事件 / node-exporter 指标，产出结论写进告警与工单 |
| 生成隔离方案 | 压力或 NotReady，且集群还有别的可调度节点 | 方案 = `cordon` + `drain`，**必须审批**（drain 属高危命令） |
| 自动 uncordon | 平台挂起的节点恢复 Ready 且无压力条件 | 每 5 分钟扫描一次，只解平台自己 cordon 的节点 |
| 转工单 | 不能隔离 / 诊断无异常 | 工单正文带完整诊断结论 |

平台**不会**：登录节点执行命令、重启 kubelet、删镜像/日志、改内核参数、删除节点。

---

## 2. NodeNotReady

**先看诊断结论里的条件**：`Ready=False` 的 `reason/message` 基本就指向方向。

```bash
# 平台侧（无需登录节点）：看 Node 事件与状态
kubectl describe node <node> | sed -n '1,40p;/Events/,$p'
kubectl get node <node> -o jsonpath='{range .status.conditions[*]}{.type}={.status} {.reason} {.message}{"\n"}{end}'
```

| 现象 | 常见根因 | 处理 |
|---|---|---|
| `KubeletNotReady` + `PLEG is not healthy` | 容器运行时卡死 / 磁盘 IO 打满 | 见 §3.1/§3.4，必要时重启容器运行时 |
| `KubeletNotReady` + `failed to get node info` / `network is unreachable` | 网络/路由/安全组 | 见 §3.5，先查节点到 apiserver 6443 连通性 |
| `NodeStatusUnknown` | kubelet 不报心跳（进程挂了/节点断电） | 登录节点 `systemctl status kubelet` |
| 证书类（`x509: certificate has expired`） | kubelet 客户端证书过期 | 轮换证书后 `systemctl restart kubelet` |

登录节点后（需 root）：

```bash
systemctl status kubelet --no-pager
journalctl -u kubelet -n 200 --no-pager | tail -100
systemctl status containerd   # 或 docker
crictl ps -a | head -20
```

**重启 kubelet**（低风险，Pod 不会重启）：

```bash
systemctl restart kubelet && sleep 10 && systemctl is-active kubelet
```

重启后**观察 5 分钟**，Ready 转 True 且无压力条件，再 `uncordon`。

---

## 3. 压力类问题

### 3.1 DiskPressure（磁盘）

```bash
df -hT | sort -k5 -hr | head -10          # 哪些挂载点满
du -xh --max-depth=1 /var 2>/dev/null | sort -hr | head -10
```

按对象清理（从安全到激进）：

```bash
# 1) 容器日志（containerd）
du -xh --max-depth=2 /var/log/pods 2>/dev/null | sort -hr | head
find /var/log/pods -name '*.log' -size +200M -mtime +3 -delete

# 2) 已退出的容器（不删运行中的）
crictl rmi --prune

# 3) 未使用的镜像（docker 集群）
docker image prune -a -f --filter "until=168h"

# 4) 包缓存 / 旧内核（按发行版）
yum clean all 2>/dev/null || apt-get clean
```

清理完成后再看 `df -h`，kubelet 压力条件需等滞回窗口（约 5 分钟）转 False。

### 3.2 inode 耗尽（磁盘有空间但写不进去）

```bash
df -i | sort -k5 -hr | head
# 定位小文件重灾区
find /var/log /var/lib/containerd /tmp -xdev -type f 2>/dev/null | wc -l
find /var/log -xdev -type f -mtime +7 -delete
```

> inode 满常见于「应用把日志写到了容器可写层」，根治要改日志采集/挂载策略，见工单。

### 3.3 MemoryPressure

```bash
free -g
ps -eo pid,rss,comm --sort=-rss | head -20
for ns in $(kubectl get ns -o jsonpath='{.items[*].metadata.name}'); do
  kubectl top pod -n $ns --no-headers 2>/dev/null | sort -k3 -hr | head -3 | sed "s/^/$ns /"
done
```

优先把**大内存且无状态**的 Pod 迁走或缩容；不要动 DaemonSet/kube-system 组件。

### 3.4 PIDPressure / 高 load

```bash
ps -eLf | wc -l              # 线程总数
cat /proc/sys/kernel/pid_max
ps -eo pid,nlwp,comm --sort=-nlwp | head -10
top -b -n1 | head -15
```

### 3.5 NetworkUnavailable / 网络错误

```bash
ip -s link | grep -A3 -E '^[0-9]+:' | head -40
ethtool -S eth0 2>/dev/null | grep -Ei 'err|drop|fifo'
ping -c3 <apiserver-ip>
nc -zv <apiserver-ip> 6443
```

### 3.6 时间偏移

```bash
chronyc tracking 2>/dev/null || ntpq -p
timedatectl
```

时间漂移 >0.5s 会影响证书校验与日志排序，必须校时。

---

## 4. 节点维护标准流程（cordon → drain → 维护 → uncordon）

```bash
# 1) 隔离
kubectl cordon <node>

# 2) 预览会驱逐哪些 Pod（平台「节点管理 → 排空预览」也可）
kubectl get pods -A --field-selector spec.nodeName=<node> -o wide

# 3) 排空（DaemonSet 保留；注意 PDB 会阻塞，必要时加 --timeout）
kubectl drain <node> --ignore-daemonsets --delete-emptydir-data --timeout=300s

# 4) 维护（清磁盘 / 重启 kubelet / 换盘 ...）

# 5) 验证：Ready=True 且无压力条件
kubectl get node <node>
kubectl describe node <node> | grep -E 'MemoryPressure|DiskPressure|PIDPressure|Ready'

# 6) 恢复调度
kubectl uncordon <node>
```

> 平台侧若已生成隔离方案并审批通过，cordon+drain 会由平台执行；
> 之后的维护动作仍需人工，平台会在节点恢复后自动 uncordon。

---

## 5. 回滚

| 动作 | 回滚 |
|---|---|
| cordon | `kubectl uncordon <node>` |
| drain | 维护完成 → `uncordon`，Pod 会按调度器重新分布 |
| 清日志/镜像 | 不可回滚，但属可再生数据 |
| 重启 kubelet | 无状态影响；如异常，`journalctl` 定位后再次重启 |
| 节点失联需下线 | 先 `drain --force --grace-period=0`，再 `kubectl delete node <node>`（**需审批**） |

---

## 6. 与平台相关的问题排查

```bash
# 平台的节点诊断为什么是空的？
#  → 该集群 Prometheus 是否可达（大屏能出图就说明可达）；executor 是否在线
curl -s localhost:9090/healthz     # 本机 executor 健康
```

平台节点诊断依赖：executor 的 `get nodes -o json` / `describe node`（走 apiserver）+ 集群 Prometheus 的 node-exporter。
缺 node-exporter 时只能给「条件 + 事件」结论，指标类结论会缺失（诊断里会写「未采集到异常指标」）。

---

## 7. 常见命令速查

```bash
kubectl get nodes -o wide
kubectl top node
kubectl describe node <node>
kubectl get pods -A --field-selector spec.nodeName=<node>
kubectl cordon <node> && kubectl uncordon <node>
kubectl drain <node> --ignore-daemonsets --delete-emptydir-data
```
