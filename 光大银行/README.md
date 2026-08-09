# 光大理财 爬虫（Playwright）说明

简要说明：本目录下的爬虫使用 Playwright 驱动浏览器抓取光大理财网站；为保证多浏览器轮换和可复现的运行，请按下列步骤准备环境。

1. 激活虚拟环境

Windows PowerShell:

```powershell
& ".\ .venv\Scripts\Activate.ps1"
```

或普通命令行（cmd）:

```cmd
.venv\Scripts\activate.bat
```

2. 安装 Python 依赖

```powershell
python -m pip install -r requirements.txt
```

3. 安装 Playwright 浏览器二进制

Playwright Python 包需要额外下载浏览器二进制：

```powershell
python -m playwright install
# 或只安装主要内核（chromium, firefox, webkit）
python -m playwright install chromium firefox webkit
```

注意：安装某些系统级渠道（例如 `chrome-beta`、`msedge-beta`、`msedge-dev`）可能需要管理员权限；如果出现安装失败，可略过这些渠道，默认的五项（msedge, chrome, chromium, firefox, webkit）通常已足够用于轮换。

4. 日常按天轮换浏览器（示例）

脚本会读取环境变量 `PLAYWRIGHT_BROWSER_CANDIDATES`，默认顺序是 `msedge,chrome,chromium,firefox,webkit`。你可以在运行前按天设置不同的候选以实现每日轮换：

PowerShell 示例（当天只用 Firefox）:

```powershell
$env:PLAYWRIGHT_BROWSER_CANDIDATES = 'firefox'
python main.py
```

或在 Linux/macOS 环境（或 Git Bash）：

```bash
export PLAYWRIGHT_BROWSER_CANDIDATES=firefox
python main.py
```

5. 关闭/不使用隧道代理

脚本默认通过环境变量 `ENABLE_TUNNEL_PROXY` 控制隧道代理，若不使用代理请设置：

```powershell
$env:ENABLE_TUNNEL_PROXY = '0'
```

6. 其它说明
- 请不要修改 `checkpoint.json`、`downloaded.txt` 等文件结构，脚本依赖这些文件维持断点与去重。
- 若需每天自动轮换浏览器，我可以为你添加一个小补丁，基于日期选择 `PLAYWRIGHT_BROWSER_CANDIDATES`，是否需要我实现？

---
文件：
- requirements.txt: 已包含 `playwright==1.59.0`（用于安装 Playwright Python 包）
- 额外的浏览器二进制仍需运行 `python -m playwright install` 获取

---

## 健康检查调度（推荐生产配置）

三层结构：

```
健康检查 health_check.ps1  (每 2h 被计划任务触发)
        ↓ 网站正常时启动
   批次守护 run_batch.ps1   (死循环, 每 5min 启动一次 main.py)
        ↓ 调用
     爬虫本体 main.py        (每批爬 20 个产品就主动退出)
```

### 紧急刹车机制

`main.py` 在以下情况会**主动 `sys.exit(10)`**：
- 连续 `MAX_CONSECUTIVE_DOWNLOAD_FAILS`（默认 5）个产品没有成功下载任何 PDF → 判定被风控
- 检测到 `state\emergency_stop.flag` 文件存在 → 立即停

`run_batch.ps1` 收到 exit=10/11 后会：
1. 创建 `state\emergency_stop.flag` 标记
2. 主动 `exit` 自己 → 整个守护链停掉

`health_check.ps1` 收到此信号后：
- 杀掉所有 python / powershell 爬虫进程
- 等待下一次计划任务触发（默认 2 小时后）

### 一键注册 Windows 计划任务（开机自启 + 每 2h 检测）

用**管理员 PowerShell** 跑一次即可：

```powershell
cd D:\crawler-project\光大银行
powershell -ExecutionPolicy Bypass -File .\register_health_task.ps1
```

注册成功后：
- ✅ 电脑开机后自动启动健康检查
- ✅ 每 2 小时检查一次 `https://www.cebwm.com` 连通性
- ✅ 网站正常 → 自动启动 `run_batch.ps1` 继续爬
- ✅ 网站异常 → 杀掉一切，等下个 2h 周期再试

### 管理命令

```powershell
# 手动触发一次健康检查
schtasks /Run /TN CebwmHealthCheck

# 查看任务状态
schtasks /Query /TN CebwmHealthCheck /V /FO LIST

# 取消注册（停用调度器）
schtasks /Delete /TN CebwmHealthCheck /F

# 实时查看健康检查日志
Get-Content D:\crawler-project\光大银行\state\health_check.log -Tail 50 -Wait

# 实时查看爬虫日志
Get-Content D:\crawler-project\光大银行\state\光大理财_日志记录.csv -Tail 20 -Wait

# 手动清理紧急停止标记 (网站恢复后强制重启)
Remove-Item D:\crawler-project\光大银行\state\emergency_stop.flag
```

### 调参 (环境变量)

| 变量 | 默认值 | 作用 |
|------|--------|------|
| `BATCH_MAX_SUCCESS_PRODUCTS` | 20 | 每批爬多少产品就退出 |
| `SLEEP_MINUTES` | 5 | run_batch 退出后等多少分钟再跑 |
| `MAX_CONSECUTIVE_DOWNLOAD_FAILS` | 5 | 连续多少产品无下载就紧急刹车 |
| `ENABLE_TUNNEL_PROXY` | true | 是否启用隧道代理 |

修改 `register_health_task.ps1` 里的 `PT2H` 可以改检查间隔（PT30M = 30 分钟，PT1H = 1 小时）。
