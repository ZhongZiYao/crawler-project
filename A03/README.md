# A03 - 中银理财（BOC Wealth）抓取器

简体中文说明文档 — 本目录为针对中银理财网站（bocwm.cn）的数据抓取脚本集合，主要入口为 `main.py`。

**主要功能**
- 抓取产品公告、产品说明书、产品净值表现、定期运作报告和其他公告。
- 将直链文件（PDF/Doc/Excel 等）下载到分类目录，并对 HTML 详情页渲染或提取内嵌 PDF 后保存为 PDF。
- 抓取并导出产品净值（净值表）为 CSV 文件。
- 日志记录（CSV）与去重进度持久化（downloaded.txt）。

**目录结构**
- `main.py`：主脚本，包含抓取流程与解析逻辑。
- `requirements.txt`：Python 依赖清单。
- `download_files/`：抓取到的文件按分类保存（运行后生成）。
- `download_files/downloaded.txt`：已处理项的唯一键（进度文件）。
- `中银理财_日志记录.csv`：操作日志，记录每条下载/处理结果。

快速开始（Windows）
1. 建议在项目目录创建虚拟环境并激活：

```powershell
python -m venv .venv
& .\.venv\Scripts\Activate.ps1
```

2. 安装依赖：

```powershell
pip install -r requirements.txt
```

3. 运行抓取脚本：

```powershell
python main.py
```

运行前检查
- 请确保系统已安装 Chrome/Chromium，且 `DrissionPage` 能找到可用的浏览器驱动（参见 DrissionPage 文档）。
- 可根据需要调整 `main.py` 顶部的配置常量（如 `CATEGORY_CODES`, `MAX_PAGES_PER_CATEGORY` 等）。

配置说明（常用项）
- `CATEGORY_CODES`：逗号分隔的分类代码（默认抓取全部）。可设置为 `product_notice,specification,net_worth,periodic_report,other_notice` 的任意组合。
- `DOWNLOAD_ROOT`：下载文件根目录（默认 `download_files/`）。
- `PROGRESS_FILE`：去重进度文件路径（默认在 `download_files/downloaded.txt`）。
- 环境变量：
  - `PDF_FAST_MODE=1`：PDF 渲染快速模式（更小/固定纸张尺寸）；默认关闭。
  - `NETWORTH_PAGE_SIZE`：拉取净值时的每页大小，默认 10。

输出说明
- 抓取出的文件保存在 `download_files/<分类名>/`。
- 操作日志写入仓目录下的 `中银理财_日志记录.csv`，包含每条记录的状态与保存路径。

依赖
- 请参阅 `requirements.txt`（包含 `requests`, `beautifulsoup4`, `DrissionPage`, `tqdm` 等）。

注意与常见问题
- 若遇到 403/412 等反爬响应，脚本会尝试使用 `DrissionPage` 获取浏览器 Cookie 并重试。
- 若需要无头或自定义浏览器配置，请参考 `DrissionPage` 的官方配置方法。

联系方式
- 如需改进或遇到问题，请在仓库中打开 issue 或联系维护者。

---
本 README 为 A03 目录的使用说明；如需我将 README 翻译成英文或补充示例（例如运行日志示例、Windows 常见故障与解决办法），告诉我即可。
