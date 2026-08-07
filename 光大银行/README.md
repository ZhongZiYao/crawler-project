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
