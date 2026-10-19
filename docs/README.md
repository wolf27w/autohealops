# docs 目录索引（2026-09-20 整理）

> 目的：止住需求/设计类文档互相重叠。**结论看主账，细节看分册，历史归档留在末尾。**

## 一、主账（先看这两份）

| 文档 | 作用 |
|---|---|
| `REQUIREMENTS-BACKLOG.md` | **需求主账**：已完成 / 待办 / 待确认 / 拍板口径 / 每轮落地记录（含迁移编号） |
| `PENDING-REQUIREMENTS-DETAIL.md` | **待开发需求明细**：现状 / 需求 / 实现要点 / 验收 / 工时 / 决策点 |

## 二、专题需求（按模块，已被主账引用）

| 文档 | 作用 |
|---|---|
| `DASHBOARD-V2-REQUIREMENTS.md` | 仪表盘 v2 规格（已实现） |
| `CLUSTER-MGMT-REQUIREMENTS.md` + `CLUSTER-MGMT-UI-UPGRADE.md` | 集群管理需求与 UI 升级（已实现） |
| `AGENT-DIAGNOSIS.md` / `AGENT-PHASES.md` / `AGENT-ROADMAP.md` / `AGENT-EVALUATION.md` | AI 诊断（会话式）的设计 / 阶段 / 路线 / 评测（4 份，按需查阅） |
| `DIAGNOSIS-CENTER.md` | 诊断中心设计 |
| `ALERT-SUPPRESSION.md` | 告警抑制/静默设计 |
| `ACCESS-CONTROL.md` / `SECURITY-TASKS.md` | 权限与安全任务 |
| `MESSAGING.md` / `MIDDLEWARE-MONITORING.md` | 消息通道 / 中间件监控 |
| `NEW-REQUIREMENTS-2026-10-19.md` | **新增需求 N12/N13**（监控无闪烁实时 + 容器级监控）—— ⏸ 已登记，暂不开发 |
| `ENCRYPTION.md` | 跨语言加密约定（Python ⇄ Go，CI 有互测） |
| `MIDDLEWARE-MONITORING.md` | 中间件监控 |

## 三、工程与运维

| 文档 | 作用 |
|---|---|
| `ARCHITECTURE.md` | 三服务架构 |
| `API.md` | 接口清单（**已同步去重后的端点**） |
| `DEPLOYMENT.md` | 部署 |
| `CODE-AUDIT.md` | 代码审计 |
| `DECISION-BOUNDARY.md` | 决策/审批边界 |
| `DUPLICATION-AUDIT.md` | **重复项审计 + 清理执行结果** |
| `ops/LOG-ROTATION.md` | 日志轮转/归档 |

## 四、历史归档

- `DESIGN-2026-09-10.md`：阶段性设计稿（内容已并入主账，保留备查）
- 根目录旧 `PROMPT.md`：开发总纲旧版，2026-09-20 删除，副本在 `logs/archive/dedup_backup_20260920/code/`（现以 `PROMPT-FINAL.md` 为准）

> 维护约定：新增需求先落主账（结论 + 状态），需要细节再开分册并在此登记；专题文档实现完成后在标题标注「已实现」。
