# 广州银行理财公告爬虫使用说明

本项目用于抓取广州银行理财公告页面（发行公告、存续期公告、到期公告），并将公告详情页按网页原排版导出为 PDF；如果详情页内存在附件，也会自动解析附件链接并一并下载，同时记录 CSV 日志。当前版本已经补齐了按公告类型分目录、断点续传、失败重试、检查点和更丰富的控制台输出。

## 1. 项目结构

- `main.py`：主脚本
- `requirements.txt`：依赖列表
- `download_pdf/`：下载根目录（运行时自动创建）
- `download_pdf/产品发行公告/`、`download_pdf/产品存续期公告/`、`download_pdf/产品到期公告/`：按公告类型分目录保存正文 PDF
- `download_pdf/产品发行公告/附件/` 等：对应公告类型下的附件子目录
- `download_pdf/downloaded.txt`：去重进度文件（运行时自动创建）
- `download_pdf/checkpoint.json`：断点续传检查点（运行时自动创建）
- `download_pdf/failed_records.csv`：失败记录（运行时自动创建）
- `广州银行_日志记录.csv`：运行日志（运行时自动创建）

说明：以上输出都固定保存在脚本目录 `guangzhou_bank` 下

## 2. 抓取目标

脚本支持以下三类公告：

- `fxgg`：产品发行公告
- `cxqgg`：产品存续期公告
- `dqgg`：产品到期公告

对应站点：

- `http://www.gzcb.com.cn/sy/jrcs/grlccs/lcgg/fxgg/`
- `http://www.gzcb.com.cn/sy/jrcs/grlccs/lcgg/cxqgg/`
- `http://www.gzcb.com.cn/sy/jrcs/grlccs/lcgg/dqgg/`

## 3. 环境准备

### 3.1 激活虚拟环境（PowerShell）

在 `guangzhou_bank` 目录执行：

```powershell
.\.venv-gz\Scripts\Activate.ps1
```

### 3.2 安装依赖

建议使用以下命令：

```powershell
uv pip install -r requirements.txt
```
文件会按公告类型分别保存到：

如果你用的是 pip：

广州银行_公告标题_公告类型_披露日期.pdf
pip install -r requirements.txt
```

## 4. 运行方式

在 `guangzhou_bank` 目录执行：
广州银行_公告标题_公告类型_披露日期.xls
```powershell
python main.py
```

运行流程：
- `unique_key`（当前使用 `机构名称+公告类型+公告名称+披露日期`）

### 抓取范围

- `CATEGORY_CODES = "fxgg,cxqgg,dqgg"`
  - 抓全部分类
  - 若只抓发行公告可改为：`"fxgg"`

- `MAX_PAGES_PER_CATEGORY = 3000`
  - 每类最大翻页数保护阈值

- `MAX_CONSECUTIVE_EMPTY = 3`
  - 连续空页达到阈值后停止该分类

### 下载与去重

- `DOWNLOAD_ROOT = os.path.join(SCRIPT_DIR, "download_pdf")`
- `LOG_CSV_PATH = os.path.join(SCRIPT_DIR, "广州银行_日志记录.csv")`
- `FAILED_CSV_PATH = os.path.join(DOWNLOAD_ROOT, "failed_records.csv")`
- `SKIP_DOWNLOADED = True`
  - `True`：跳过已下载来源链接（推荐）
  - `False`：允许重复导出（文件名会自动追加 `_1`、`_2`）

### 断点续传

- `ENABLE_CHECKPOINT_RESUME = True`
  - `True`：按分类记录最后成功处理的页码，下次运行从下一页继续
  - `False`：每次从第 1 页重新开始

### 反爬与稳定性

- `TIMEOUT = 30`
- `REQUEST_RETRY = 3`
- `DOWNLOAD_RETRY = 3`
- `RETRY_WAIT_SECONDS = 3`
- `REQUEST_DELAY_RANGE = (0.5, 1.5)`
- `LIST_PAGE_DELAY_RANGE = (0.8, 1.8)`
- `PDF_RENDER_WAIT = 2.0`

### 控制台输出

- `ENABLE_EMOJI_LOG = True`
  - `True`：显示更丰富的 emoji 日志
  - `False`：输出纯文本日志

如果网络不稳定或出现频繁失败，可增大这些等待参数。

## 6. 输出文件说明

### 6.1 PDF 命名规则

```text
广州银行_披露日期_公告类型_公告标题.pdf
```

重名时自动追加后缀：`_1`、`_2`...

文件会按公告类型分别保存到：

```text
download_pdf/产品发行公告/
download_pdf/产品存续期公告/
download_pdf/产品到期公告/
```

附件会进一步放到各类型目录下的 `附件/` 子目录。

### 6.2 附件命名规则

如果详情页存在附件，例如 `.xls`、`.xlsx`、`.doc`、`.docx`、`.zip` 等，会按以下规则保存：

```text
广州银行_披露日期_公告类型_公告标题.xls
```

扩展名以附件真实链接为准。

如果同一公告下出现多个同扩展名附件，为避免重名，程序会自动追加后缀：`_1`、`_2`...

### 6.3 CSV 字段

CSV 文件为 `广州银行_日志记录.csv`，字段如下：

- `机构名称`
- `公告名称`
- `公告类型`
- `披露日期`
- `下载时间`
- `状态`（`SUCCEED` / `FAILED`）
- `来源链接`
- `保存路径`
- `unique_key`（当前使用详情页链接）

## 7. 常见问题

### Q1：为什么会出现 403/412？

站点可能触发反爬。脚本已内置 Cookie 刷新：遇到 403/412 会重新访问首页获取 Cookie 并重试。

### Q2：为什么部分记录显示 FAILED？

常见原因：

- 页面临时不可访问
- 页面渲染慢，`PDF_RENDER_WAIT` 太小
- 网络波动导致请求中断

可增大等待参数后重跑。

### Q3：如何做增量抓取？

保持 `SKIP_DOWNLOADED = True`，脚本会根据 `download_pdf/downloaded.txt` 自动跳过已抓取链接。

补充说明：

- 已下载过 PDF 的公告，再次运行时不会重复生成 PDF
- 如果旧公告详情页里有附件但本地还没有，程序会自动补抓附件
- 已存在的附件文件不会重复下载

如果中途被中断，保留 `checkpoint.json` 后再次运行会从上次成功页继续。

### Q4：如何强制全量重跑？

两种方式：

1. 将 `SKIP_DOWNLOADED = False`
2. 删除 `download_pdf/downloaded.txt` 后再运行（会重新抓取）

## 8. 注意事项

- 首次运行会启动 Chromium 浏览器窗口用于取 Cookie 与页面导出。
- 请确保本机已安装可用的 Chrome/Chromium 内核环境。
- 程序不会修改站点内容，仅访问公开披露页面并保存本地文件。
