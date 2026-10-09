# 后台 UI 模板目录

> 用途：存放「现有风格存档」与「改版候选稿」，供后台换肤时对照。

## 目录结构

```
templates/
├── before/        现有风格截图存档（改版前基线）
├── proposals/     改版候选稿（纯 HTML/CSS，可交互预览）
└── README.md
```

## before/ — 现有风格截图（基线）

于 2026-10-09 对**本地运行中的现有后台**（vite :5173）逐页截取，作为换风格前的对照基线。

| 文件 | 页面 |
|------|------|
| 00-login.png | 登录页 |
| 01-dashboard.png | 仪表盘 |
| 02-clusters.png | 集群管理 |
| 03-alert-handling.png | 告警处理 |
| 04-tickets.png | 工单 |
| 05-strategy.png | 策略配置 |
| 06-fault-diagnosis.png | 故障诊断（应用） |
| 07-knowledge.png | 知识库 |
| 08-settings.png | 系统设置 |

- 分辨率 1440×900，设备像素比 2（实际 2880×1800）。
- 生成方式：无头 Chrome（CDP）注入本地 admin 令牌后截图。重新生成见文末命令。

## proposals/ — 改版候选稿

4 套纯 HTML/CSS 模板（**无图片、无外部依赖**，图标为内联 SVG），主色浅绿 `#12b981`。

| 文件 | 方案 | 特点 |
|------|------|------|
| `index.html` | 预览索引 | 4 套缩略图 + 配色 |
| `01-classic.html` | **经典侧栏（推荐）** | 深绿侧栏 + 顶栏，KPI×5、面积图、环形图、密集表 |
| `02-topnav.html` | 顶部横向导航 | 无侧栏，6 联统计条 + 三栏网格（信息最满） |
| `03-rail.html` | 图标窄栏 | 64px 图标导航 + 右「系统状态/动态流」 |
| `04-workbench.html` | 主从工作台 | 左列表 + 右详情（告警处理/诊断场景） |

### 配色

| 变量 | 值 |
|------|-----|
| 主色 | `#12b981` |
| 主色-深 | `#048c63` |
| 主色-浅底 | `#d5f5e5` / `#ecfdf5` |
| 页面底色 | 淡绿渐变（不用纯白） |

---

## 附：重新生成「现有风格」截图

前置：本地服务已启动（`./start.sh`），且 `frontend/public/__seed.html` 存在（用于注入本地令牌）。

```bash
# 1) 临时种子页（同源注入 token/user 后跳转）
cat > frontend/public/__seed.html <<'HTML'
<!doctype html><meta charset="utf-8"><script>
const p=new URLSearchParams(location.search);
localStorage.setItem('token',p.get('t')||'');
localStorage.setItem('user',JSON.stringify({id:1,username:'admin',role:'admin'}));
location.replace(p.get('to')||'/dashboard');
</script>
HTML

# 2) 用无头 Chrome 逐页截图（脚本见 templates/tools/shot.mjs、templates/tools/run-shot.sh）
bash templates/tools/shots-all.sh

# 3) 清理种子页
rm -f frontend/public/__seed.html
```

> 注：截图为本地开发环境真实渲染结果，非设计稿。
