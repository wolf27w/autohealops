# 未完成需求盘点（2026-11-20，代码核实口径）

> 生成方式：逐条 grep / 读代码核实（非照抄文档结论）。HEAD = `84e8175`。
> 文档结论与代码冲突处，以代码为准并在「五」列出。

> **结论摘要：可立即开发 4 条 / 待老板拍板 4 条 / 需确认或待细化 2 条 / 部署动作 5 条 / 暂缓 3 条**
> （另有文档编号冲突 5 组 + 文档与代码不一致 10 处，见「五」）

---

## 一、可立即开发（无外部依赖、口径已明确）

| 编号 | 需求 | 代码现状（证据 文件:行） | 缺口 | 预估工时 | 主要落点文件 |
|---|---|---|---|---|---|
| **R59** | Agent 版本管理与升级通道（R55/R57 同类问题的结构性解法） | 全库 0 命中：平台无「agent 版本」概念、无能力协商、无升级通道；仅 docs 登记（commit `84e8175`）。现状反面证据：agent 集群调 `ListHPA` 报 `unknown method`（R57 联调） | ① 版本上报+能力协商 ② 集群列表版本列+「需升级」标 ③ 一键生成升级 YAML/触发滚动升级 ④ proto 向后兼容+Unimplemented 友好提示 | ~2.5d | `api/agent_gateway.py`、`api/clusters.py`（`agent_version` 上报已有点）、`services/*`、`frontend ClusterList/ClusterDetail`、agent 侧 `cmd/agent` |
| **R44-P2~P4** | 故障分类收敛剩余：Agent 输出三字段契约 + 低置信度转人工 + self_heal_rules 按 L2 + 提示词收敛 + 下线硬路由 | P1 已实现：`app/core/fault_taxonomy.py:29-42`（11 类 L2）、`:49-118`（旧 fault_type→L2 映射 + resource_type 兜底）、`models/alert.py:57`（fault_family/fault_domain 读时派生）、`api/alerts.py`（fault_family 筛选）。P2~P4 全库 0 命中：无 `category/confidence/evidence_refs` 输出解析、无 `confidence<阈值→转人工`、无 `self_heal_rules.fault_family_match`、无「未分类兜底移除」 | ① Agent 输出固定三字段 + 解析 ② confidence 阈值（默认 0.6）+ 低置信度转人工工单（带 evidence_refs）③ self_heal_rules 改按 L2 配动作集 ④ 诊断类提示词收敛为 diagnosis_app/db/node 三条 ⑤ 下线 fault_type 硬路由与「未分类→人工」兜底 | ~4-5d | `ai-service diagnosis_service/prompt_engine`、`api-server alert_auto_service`、`self_heal_rules` 相关、`fault_taxonomy.py` 扩展 |
| **R42-二期** | Agent trace → eval 评估数据集 + 批量回归 + 诊断准确率/平均步数/工具命中率 | 一期已实现：`services/trace_service.py`(834 行) + `pages/AgentTrace.tsx` + `api/trace.py`（commit `4620584`）。二期 0 命中：全库无 `eval/回归集/标注/诊断准确率` 相关实现 | 标注正确答案→批量回归→算准确率/步数/工具命中率→支撑 prompt 迭代；`confidence` 落库依赖 R44-P2 | ~2-3d | `trace_service.py` 扩展 + 新 eval 服务 + 统计视图 |
| **R51-尾巴** | PG 表结构对比的已知限制收尾 | PG 主体已实现（commit `5f75ebf`）：`dialects/pg.py:282` table_schema、`:467` build_table_alter、`:460` DROP DEFAULT 已补。剩余：PG **索引列序未连真实 PG 验证**（BACKLOG 已知限制原话） | 连真实 PG 验证索引列序（`pg_index`/`pg_attribute` 排序）生成与回读一致 | ~0.5d | `dialects/pg.py` + 单测 |

---

## 二、待老板拍板口径（不能直接开工）

| 编号 | 需求 | 卡在哪 | 需要老板定什么 |
|---|---|---|---|
| **R53** | 节省人工工时基准 | `api/app/api/stats.py:145` 写死 `_LABOR_MINUTES_PER_HEAL = 30`（注释自认「无历史工时基准，行业常用值」），大屏「节省人工」= 成功数 × 30 分钟 | 实际基准每起自愈省多少分钟（还是维持 30 分钟估算） |
| **R51-charset** | charset/collation 解耦生成口径 | `db_schema_diff.py:82-115` + `dialects/mysql.py` 已按 `DEFAULT CHARSET=`/`COLLATE=`（只改默认不动存量数据）落地；BACKLOG 标「口径待确认」 | 确认「只改默认、不改存量数据」是否符合预期（原实现 `CONVERT TO` 会连数据转换） |
| **B2** | R1 准入最终风险口径（fail-open vs fail-closed） | ROADMAP 称「admission webhook 已删」，但代码里 `k8s-executor/internal/admission/{mutate.go,rules.go,server.go}` 存在，且 commit `10b2c86` 重加为 **fail-closed**（JSONPatch 改写副本/requests/limits）。但 `cmd/`、`internal/grpc/` 内 0 命中 = 未接进 executor 主进程 | 准入到底是「作废销项」还是「保留 fail-closed 并接线」；准入拦截的最终风险口径 |
| **R44-confidence** | confidence 阈值先定多少 | `fault_taxonomy.py` 只有枚举/映射，无 confidence 逻辑（P2 才做） | 阈值先用 0.6 还是先跑数据再定（建议先 0.6，两周后按分布调，不阻塞开工） |

---

## 三、部署/运维动作（非代码开发）

| 编号 | 事项 | 现状（证据） | 需要做什么 |
|---|---|---|---|
| **R49** | R2 多副本实际部署验证 | 代码已就绪：`k8s/deployments.yaml:17/72/133/185/239` = api-server 2 / agent-gateway 1 / k8s-executor 1 / ai-service 2 / frontend 2；`k8s/hpa.yaml`（api-server 2-5、ai-service 2-4）。⚠️ 与 TOOLS.md「api-server 须单副本（agent 网关连接表进程内）」的旧结论冲突，需先核实网关是否已拆独立 Deployment（deployments.yaml 显示 agent-gateway 已是独立 Deployment） | 部署窗口 + 冒烟验证多副本下 agent 隧道/告警处理不串 |
| **R55-生效** | agent 升级让日志下载/时间段过滤真正生效 | 代码已修（commit `77ce753`/`a32914a`）：api-server 按 `clusters.source` 分流，agent 走 gRPC 带时间戳。但集群内 agent 是旧二进制，不认新字段 → 仍 502/空文件 | 升级测试环境（999025）与各 agent 集群镜像；旧 agent 需返回明确「版本过低」提示 |
| **M4/D2** | 存量表时间分区 DDL | `scripts/check_partitions.py`（只读核对）；ROADMAP 实测：`resource_snapshots` 已分区（12 个），**`pod_metrics`(261 万行)、`quota_snapshots`、`slow_sql_records` 仍未分区** | 运维窗口手动 `ALTER TABLE ... PARTITION BY RANGE`（勿自动跑） |
| **P4** | 9095 agent 网关对外 | 代码就绪：`agent_gateway.py:333`（`AGENT_GATEWAY_PUBLIC_ADDR`）、`docs/DEPLOYMENT.md:123`（NodePort 30905 方案）；现状仅本机可达 | 按部署环境配 NodePort/LB 或 nginx/SamWaf 四层转发 + 填 `AGENT_GATEWAY_PUBLIC_ADDR`（改后重启 api-server 重签服务端证书） |
| **A6** | 平台自身生产部署 | `docs/DEPLOYMENT.md` 材料齐（backup-cronjob、mTLS、证书备份脚本），未上生产 | 正式部署 + 备份/DR 演练 |

---

## 四、暂缓项（文档明确缓行，列出但不开工）

| 编号 | 需求 | 缓行原因 |
|---|---|---|
| **S1–S5** | 存储优化（索引瘦身/去重存储/分区保留/换 SSD/时序外迁） | 触发阈值未到（单表>5000 万行 / 库>50GB / 常态慢查询>100ms）；当前 3.4GB。代码 0 命中（VictoriaMetrics/ClickHouse 均无） |
| **M7** | 时区统一（DB 存 UTC + 展示转时区） | 2026-10-16 核实无当前 bug：全链路一致 Asia/Shanghai，改 UTC 属多时区重构、无收益 |
| **阶段8-全自主** | 告警闭环路径选择交给 Agent 自主 | 阶段 7（ReAct 决策循环，commit `236a6f1`，默认关闭）跑稳后再议。**注意：阶段8 里的「发布对接（Jenkins）」已实现**（`api/release.py` 31KB + `pages/ReleaseSettings.tsx`/`ReleaseHistory.tsx`/`components/PipelineView.tsx` + 路由 `/diagnose/release`），对账表把「阶段8 全自主+发布对接」整体列未实现不准确 |

---

## 五、文档冲突与编号问题

### 5.1 对账表与正文/代码矛盾（10 处，需以代码为准修正台账）

| # | 位置 | 文档说法 | 代码实际 |
|---|---|---|---|
| 1 | 对账表「未实现 19 条」 | R45/R46/R47 未实现 | 已实现（commit `2928808`；`monitor-bigscreen.html:9-16` 主题初始化、`:716` navCluster、`:23-26` D7 配色 #0e1412/#37c08a） |
| 2 | 对账表「未实现 19 条」 | R48/R50/R51/R52/R54 未实现 | 均已实现（`5f75ebf`/`802d2f4`/`9b05d8d`；pg.py table_schema、import_csv_chunk、db_schema_diff renamed、diagnosis_impact.py、死文件 GONE）。**与正文各节标 ✅ 直接矛盾** |
| 3 | R56 正文 | 「未开发」 | 已实现（commit `c4fd9c0`，`frontend/src/utils/logFilename.ts:35` buildLogFilename 时间语义） |
| 4 | 对账表「未实现 19 条」内 | R43-H8 列未实现却标「✅ 2026-11-19 完成」 | H8 已实现（commit `0f0636a`），表格内自相矛盾 |
| 5 | 对账表 | R44 完全未列（未实现/部分实现都没有） | P1 已实现（commit `fa0b455`，`core/fault_taxonomy.py`），P2~P4 未做 → 应列「部分实现」 |
| 6 | ROADMAP B2 | 「admission webhook 已删 / 模块不存在」 | `k8s-executor/internal/admission/{mutate.go,rules.go,server.go}` 存在（`10b2c86` 重加 fail-closed），但未接线 |
| 7 | ROADMAP R19 | 集群管理内发布（对接 Jenkins）标 ⚪ 未排期 | 已实现：`api/release.py` + `ReleaseSettings/ReleaseHistory/PipelineView` |
| 8 | BACKLOG「需求状态」 | 「剩 H7/H8/H9/H10」 | H7（`alert_auto_service.py:330` 建工单）、H8（`0f0636a`）、H9（`pages/ExecutionsPage.tsx:1` 执行明细）、H10（`clusters.py:177` `_new_agent_token` 266 字符）全部已实现 |
| 9 | ROADMAP B3 | SelfHealPolicy 死代码待砍 | 全库 0 命中，已清（可销项） |
| 10 | 对账表 | R49 列「未实现」 | 实为「代码就绪待部署」（`deployments.yaml` 多副本 + `hpa.yaml` 都在），属部署动作非开发缺口 |

### 5.2 文档编号冲突（5 组，待定正式口径）

| 冲突组 | 情况 |
|---|---|
| R1–R13 | 两套编号并存（KubeDoor 对标 vs 数据库/主机线） |
| S1–S5 vs S1–S2 | 两处 S 系列定义不同 |
| D1–D3 vs D1–D8 | 决策项两套编号 |
| M 系列 | 三套（监控/主机/杂项） |
| P 系列 | 四套 |

> 建议口径：后续新需求统一 R 系列续编（当前最大 R59），旧编号保留不再新增。

### 5.3 对账恒等式已漂移

台账写「119 已实现 + 14 部分 + 19 未实现 + 4 废弃 = 156」及「未实现 = 19+2 = 21、总数 160」。经本轮核实，至少 R45/R46/R47/R48/R50/R51/R52/R54/R56 这 9 条应从未实现移入已实现，R44/R49/R55 应改「部分实现/部署态」，实际「未开发」仅剩 **R59、R17、R41、R42-二期、R44-P2~P4、R51-尾巴、R53（拍板）、阶段8-全自主、S1–S5、M4/D2（运维）、M7、P4、A6**，且多属部署/拍板/暂缓，非纯代码缺口。

---

## 附：候选项逐条结论速查

| 编号 | 结论 | 一句话证据 |
|---|---|---|
| R17 | ❓未细化/未排期 | 全库 0 命中（binlog/cdc/logical replication 均无） |
| R40 | ✅ 已完成 | v9 上线（`25b46e8`）+ R45/R46/R47 补齐（`2928808`） |
| R41 | ❓未开发（大工程） | 全库 0 命中 vm-app 域；仅 `resources.py:2129` 有 `k8s-app` 标签常量误命中 |
| R42 一期 | ✅ 已完成 | `trace_service.py` + `AgentTrace.tsx`（`4620584`） |
| R42 二期 | ⬜ 未开发 | 全库 0 命中 eval/回归集/标注 |
| R43 H1–H10 | ✅ 全部完成 | H5 `db_connections.py:1903` export-result、H7 `alert_auto_service.py:330`、H9 `ExecutionsPage.tsx`、H10 `clusters.py:177` |
| R44 | 🟡 部分（P1 done / P2~P4 未做） | `fault_taxonomy.py` 有 11 类 L2；三字段/低置信度 0 命中 |
| R45 | ✅ 已完成 | `monitor-bigscreen.html:9-16` theme |
| R46 | ✅ 已完成 | `monitor-bigscreen.html:716` navCluster |
| R47 | ✅ 已完成 | `monitor-bigscreen.html:26` `--accent:#37c08a`、`#58a6ff` 已除 |
| R48 | ✅ 已完成 | `dialects/pg.py:282/467` |
| R49 | 🔧 部署动作 | `deployments.yaml:17/72/185/239` + `hpa.yaml` |
| R50 | ✅ 已完成 | `db_direct_service.py:44` import_csv_chunk |
| R51 | 🟡 部分（charset 口径待确认 + PG 索引列序未验证） | `db_schema_diff.py:82-115` renamed |
| R52 | ✅ 已完成 | `diagnosis_impact.py:107` summarize_impact |
| R53 | 🔴 待拍板 | `stats.py:145` 30min 常量 |
| R54 | ✅ 已完成 | 死文件 GONE（`9b05d8d`） |
| R55 | 🔧 代码已修待 agent 升级 | `77ce753`/`a32914a` |
| R56 | ✅ 已完成 | `logFilename.ts:35`（`c4fd9c0`） |
| R57/R58 | ✅ 已完成（今日） | `0947f8b~229947e` / `ebc6644` |
| R59 | ⬜ 未开发（今日登记） | `84e8175`（docs-only） |
| 阶段8 | 🟡 发布对接✅ / 全自主缓行 | `api/release.py` 有；全自主 0 命中 |
| S1–S5 | ⏸ 暂缓 | VictoriaMetrics/ClickHouse 0 命中 |
| M4/D2 | 🔧 运维 DDL | `check_partitions.py`；pod_metrics 未分区 |
| M7 | ⏸ 暂缓 | 核实无 bug |
| P4 | 🔧 部署 | `agent_gateway.py:333` 有、未对外 |
| A2 | ✅ 已完成 | `clusters.py:556/568` agent/upgrade |
| A4 | ✅ 已完成（并入 A1/P3） | agent-onboard + RBAC 下发 |
| A6 | 🔧 部署 | DEPLOYMENT.md 齐、未上生产 |
| D1 | ✅ 已完成 | `credential_notice_service.py` + `main.py:515` 调度 |
| B1 | ✅ 已完成（=C2） | `diagnosis_failures` 表 |
| B2 | 🔴 待拍板（admission 已重加） | `internal/admission/` 存在 |
| B3 | ✅ 已清（可销项） | SelfHealPolicy 0 命中 |

---

## 十四、收盘状态（2026-11-20 14:20 更新）

> 本节覆盖上文「一」中的旧结论，以本节为准。

### 已完成（今日，均已验证并提交）
| 编号 | 内容 | 提交 |
|---|---|---|
| R57 | HPA 管理（详情页 Tab，本 ns，operate 直放，诊断联动） | `0947f8b`~`229947e` |
| R58 | 工作负载详情页自动刷新 + YAML 保存后重载 | `ebc6644` |
| R42 二期 | 诊断质量评估（标注/评估集/回放/准确率·步数·命中率） | `c81ceeb` |
| R44 P2~P4 | 三字段契约 + 低置信度转人工(0.6) + 规则按 L2 | `84e0552` |
| R59 | agent 能力协商 + 版本列「需升级」+ 升级命令 | `664c8ba` |
| R51 尾巴 | PG 索引列序 + DROP DEFAULT + 12 条单测 | `36ca61f` |
| R55（部分） | 测试环境 999025 agent 升级到 0.2.17，HPA 与日志下载实测通过 | 镜像 0.2.17 / `8eb3b7e` |

### 新增（2026-11-20 17:00 老板提出）
| 编号 | 内容 | 分类 | 备注 |
|---|---|---|---|
| **R60** | 诊断能力缺口：版本/发布历史不可见（list_revisions / compare_revisions / 事件-版本关联 / 发布基线 / 现场留存 / 证据不足表达） | 纯代码可开发（P1 不需要外部依赖） | 详见 BACKLOG R60；验收基准 = qa-bgs/eft-gateway 真实案例 |

### 仍未完成
| 分类 | 编号 | 内容 | 卡点 |
|---|---|---|---|
| 纯代码缺口 | **R17** | 数据库同步/迁移 | 未细化，全库 0 命中，需先设计 |
| 纯代码缺口 | **R41** | 三域 Agent（vm-app 虚拟机应用域） | 大工程，需先定接入方式 |
| 待拍板 | **R53** | 节省人工工时基准（现 30 分钟/次） | 需真实数字 |
| 待拍板 | **R51-charset** | 只改默认不动存量数据 | 口径确认 |
| ~~待拍板~~ | ~~B2~~ | ~~准入 webhook~~ | ✅ 2026-11-20 老板决定**销项**，代码已清理，管控由资源治理承担 |
| ~~待拍板~~ | ~~A6 拓扑~~ | ~~生产是否需要 agent~~ | ✅ 已定：生产/uat 直连，不套 agent；平台进城走 in-cluster SA |
| 运维动作 | **R49** | 多副本实际部署验证 | 需窗口 |
| ~~运维动作~~ | ~~R55 剩余~~ | ~~生产/其它 agent 集群升级~~ | ✅ 2026-11-20 老板决定**销项**：生产/uat 保持 kubeconfig 直连，不下发 agent |
| 运维动作 | **M4/D2** | pod_metrics(261万) / quota_snapshots / slow_sql_records 分区 | 需 DB 窗口 |
| 运维动作 | **P4** | 9095 网关对外（NodePort/LB） | 需部署决策 |
| 运维动作 | **A6** | 平台自身生产部署 | 需部署窗口 |
| 暂缓 | **S1–S5** | 存储优化 | 触发阈值未到（当前 3.4GB） |
| 暂缓 | **M7** | 时区统一 UTC | 核实无 bug，重构无收益 |
| 暂缓 | **阶段8-全自主** | 告警闭环自主编排（发布对接已实现） | 阶段 7 跑稳再议 |
| 主动不做 | **R44 遗留** | 提示词收敛三条、下线 fault_type 硬路由 | 硬路由承载确定性动作，拆了会坏自愈闭环 |

### 结论
「纯代码可开发」的只剩 **R17、R41** 两条大工程；其余全部是**等你拍板**或**要动环境**。台账层面还有 5 组编号冲突待统一（R1–R13 / S / D / M / P 各自的重复定义）。
