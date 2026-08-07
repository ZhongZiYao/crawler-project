# B09 - 广银理财公告爬虫（Playwright）

本目录保留生产抓取核心文件，主程序为 main.py。

## 抓取范围

当前仅保留 4 个公告模块：

1. 成立公告
2. 到期公告
3. 运作公告
4. 理财公告

## 已落地规则

1. 列表抓取调用 getNewsList4Web.fun，按 fetchNum + beginNum 分页。
2. 详情抓取调用 getNewsById4Web.fun，按栏目自动携带 sourceType。
3. 附件优先下载：从 retData.fileList 和 retData.pdfPath/pdfName 构造候选链接。
4. 下载优先走 news/downloadFile.fun?path=<...>&name=<...>，并过滤高失败候选。
5. 详情有 HTML 且附件链路失败时，自动 HTML 转 PDF 兜底。
6. 按板块串行处理，失败不中断，支持重试和板块冷却。
7. 断点续跑 + 来源链接去重 + 同名文件自动后缀。
8. 程序启动后先回补历史失败，再继续断点抓取。
9. 回补成功后自动从 failed_records.csv 清理，避免下次重复回补。

## 环境与依赖

requirements.txt 已固定版本：

1. requests==2.32.3
2. DrissionPage==4.0.5.6
3. playwright==1.49.1

说明：

1. Windows 下该版本 Playwright 通常直接使用官方 wheel，不需要本地 C++ 编译器。
2. 仅安装 Python 包还不够，首次运行前还需要安装浏览器内核。

## 运行流程

1. 进入目录

```powershell
Set-Location "e:\Program Files\PythonProject\crawler project\c01-c08\B09"
```

2. 安装依赖（推荐继续用 uv）

```powershell
uv pip install -r requirements.txt --python ".\.venv-B09\Scripts\python.exe"
```

3. 安装 Playwright Chromium（首次必做）

```powershell
& ".\.venv-B09\Scripts\python.exe" -m playwright install chromium
```

4. 运行主程序

```powershell
& ".\.venv-B09\Scripts\python.exe" ".\main.py"
```

## 常用配置

main.py 顶部可配：

1. PLAYWRIGHT_HEADLESS：是否无头运行。
2. ENABLE_SECTIONS / RUN_ONLY_SECTIONS：板块开关。
3. TEST_ONE_NOTICE_MODE / TEST_NOTICE_KEYWORD：单条联调。
4. MAX_PAGES_PER_SECTION：每板块页数上限，0 表示不限制。
5. REQUEST_INTERVAL_SECONDS / PAGE_INTERVAL_SECONDS：全局节奏。
6. SECTION_ITEM_INTERVALS / SECTION_PAGE_INTERVALS：分板块节奏。
7. REQUEST_RETRY / DOWNLOAD_RETRY / RETRY_WAIT_SECONDS：重试策略。
8. SKIP_DOWNLOADED：是否按来源链接去重。

## 输出文件

1. 主日志：广银理财_日志记录.csv
2. 失败清单：download_files/failed_records.csv
3. 断点文件：download_files/checkpoint.json
4. 去重记录：download_files/downloaded.txt

## 风险提示

若出现 ERR_EMPTY_RESPONSE，建议按下面顺序排查：

1. 先用 RUN_ONLY_SECTIONS 只跑一个板块。
2. 再用 TEST_ONE_NOTICE_MODE + TEST_NOTICE_KEYWORD 做单条联调。
3. 调大板块间隔和页间隔，减小触发风控概率。
