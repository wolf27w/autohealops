# 日志轮转 / 归档（A2）

## 现状
本地以 `nohup` 方式运行时，各服务日志写在 `logs/`：

| 文件 | 来源 |
|---|---|
| `logs/api-server.log` | api-server（uvicorn） |
| `logs/ai-service.log` | ai-service（uvicorn） |
| `logs/executor.log` | k8s-executor（Go） |
| `logs/frontend.log` | 前端（如以 nohup 运行 vite） |

无轮转时日志会无限增长（本地实测 `executor.log` 已 11MB+，长期运行会持续变大）。

## 手动轮转
```bash
# 默认：轮转 ./logs，保留 7 份归档，单文件 >50MB 才轮转
./scripts/rotate_logs.sh

# 自定义：目录 保留份数 阈值(MB)
./scripts/rotate_logs.sh ./logs 14 100
```
- 采用 **copytruncate**（先拷贝再清空），不影响正在写日志的进程。
- 归档到 `logs/archive/<name>-<时间戳>.log.gz`，超出保留份数自动清理最旧的。

## 定时执行（二选一，**需老板确认后再配置**）
### 方案 A：系统 cron（每天 03:00）
```cron
0 3 * * * cd /Users/wolf/Documents/project/api-ops-platform && ./scripts/rotate_logs.sh >> logs/rotate.log 2>&1
```

### 方案 B：macOS launchd（推荐，无需常驻 shell）
新建 `~/Library/LaunchAgents/com.apiops.rotate-logs.plist`：
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.apiops.rotate-logs</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string>
    <string>/Users/wolf/Documents/project/api-ops-platform/scripts/rotate_logs.sh</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/wolf/Documents/project/api-ops-platform</string>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>0</integer></dict>
</dict></plist>
```
加载：`launchctl load ~/Library/LaunchAgents/com.apiops.rotate-logs.plist`

## 容器部署（docker-compose）
容器场景建议用 Docker 日志驱动限制大小，例如在 `docker-compose.yml` 各服务下加：
```yaml
    logging:
      driver: json-file
      options:
        max-size: "50m"
        max-file: "7"
```

## CI
`.github/workflows/ci.yml` + `scripts/ci_check.sh` 做静态校验（两服务 py_compile、前端 tsc、Go build、跨语言加密互测）。
本地等价命令：`make ci`。
